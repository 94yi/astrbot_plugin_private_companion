# -*- coding: utf-8 -*-
"""表情检索与向量域。

由 tools/split_mixin_domain.py 从 llm_tool_actions.py 机械抽取（19 个方法 + 0 个模块级名字 + 0 个类级赋值 / 1096 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 LlmToolActionsMixin）。
"""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
import re
import time
from .helpers import _now_ts, _path_text, _safe_float, _safe_int, _single_line
from .llm_tool_actions_shared import PHOTO_TOOL_SILENT_SENTINEL, logger
from .persona_config import runtime_persona_setting
from .reaction_asset_library import ReactionAssetLibrary
from .reaction_expression import (
    classify_reaction_expression_feedback,
    ensure_reaction_expression_state,
    record_reaction_expression_feedback,
    sync_reaction_expression_auto_preference,
)
from astrbot.api.event import AstrMessageEvent
from typing import Any



class LlmToolActionsReactionSearchMixin:
    """表情检索与向量域（从 LlmToolActionsMixin 拆出）。"""


    @staticmethod
    def _is_reaction_embedding_provider(provider: Any) -> bool:
        return any(
            callable(getattr(provider, name, None))
            for name in ("get_embedding", "get_embeddings", "get_embeddings_batch")
        )

    @staticmethod
    def _reaction_embedding_provider_runtime_id(provider: Any) -> str:
        try:
            meta = provider.meta() if callable(getattr(provider, "meta", None)) else None
        except Exception:
            meta = None
        if isinstance(meta, dict):
            value = meta.get("id")
        else:
            value = getattr(meta, "id", "") if meta is not None else ""
        if value:
            return _single_line(value, 160)
        config = getattr(provider, "provider_config", None)
        if isinstance(config, dict) and config.get("id"):
            return _single_line(config.get("id"), 160)
        direct = _single_line(getattr(provider, "id", "") or getattr(provider, "provider_id", ""), 160)
        if direct:
            return direct
        provider_class = provider.__class__
        return _single_line(
            f"auto:{getattr(provider_class, '__module__', '')}.{getattr(provider_class, '__qualname__', provider_class.__name__)}",
            160,
        )

    async def _embedding_provider_for_configured_id(self, configured_id: Any = "") -> tuple[Any, str]:
        configured = _single_line(configured_id, 160)
        context = getattr(self, "context", None)
        if context is None:
            return None, configured

        async def resolve(getter_name: str, provider_id: str = "") -> Any:
            getter = getattr(context, getter_name, None)
            if not callable(getter):
                return None
            try:
                value = getter(provider_id) if provider_id else getter()
                return await value if inspect.isawaitable(value) else value
            except Exception:
                return None

        if configured:
            for getter_name in ("get_embedding_provider_by_id", "get_provider_by_id"):
                provider = await resolve(getter_name, configured)
                if self._is_reaction_embedding_provider(provider):
                    return provider, configured
            manager = getattr(context, "provider_manager", None)
            candidates = list(getattr(manager, "embedding_provider_insts", []) or [])
            if isinstance(getattr(manager, "inst_map", None), dict):
                candidates.extend(manager.inst_map.values())
            for provider in candidates:
                if (
                    self._reaction_embedding_provider_runtime_id(provider) == configured
                    and self._is_reaction_embedding_provider(provider)
                ):
                    return provider, configured
            logger.warning(
                "Embedding Provider 不可用，回退本地语义与关键词: provider_id=%s",
                configured,
            )
            return None, configured

        for getter_name in ("get_all_embedding_providers", "get_all_providers"):
            providers = await resolve(getter_name)
            provider_rows = providers.values() if isinstance(providers, dict) else providers or []
            for provider in provider_rows:
                if self._is_reaction_embedding_provider(provider):
                    return provider, self._reaction_embedding_provider_runtime_id(provider) or "<auto>"
        manager = getattr(context, "provider_manager", None)
        for provider in (
            list(getattr(manager, "embedding_provider_insts", []) or [])
            + list(getattr(manager, "inst_map", {}).values() if manager is not None else [])
        ):
            if self._is_reaction_embedding_provider(provider):
                return provider, self._reaction_embedding_provider_runtime_id(provider) or "<auto>"
        return None, ""

    async def _shared_embedding_provider(self) -> tuple[Any, str]:
        configured = _single_line(
            runtime_persona_setting(self, "embedding_provider_id", "")
            or runtime_persona_setting(
                self,
                "reaction_expression_embedding_provider_id",
                "",
            ),
            160,
        )
        if not configured:
            return None, ""
        return await self._embedding_provider_for_configured_id(configured)

    async def _reaction_embedding_provider(self) -> tuple[Any, str]:
        configured = _single_line(
            runtime_persona_setting(
                self,
                "reaction_expression_embedding_provider_id",
                "",
            )
            or runtime_persona_setting(self, "embedding_provider_id", ""),
            160,
        )
        return await self._embedding_provider_for_configured_id(configured)

    @staticmethod
    def _reaction_embedding_input_text(value: Any) -> str:
        """Keep BGE-style embedding requests below common 512-token limits.

        Providers expose different tokenizers and many local BGE servers reject
        an oversized request before they can truncate it.  A conservative
        character budget keeps the semantic labels at both ends of a catalog
        entry while avoiding a provider-specific dependency in the plugin.
        """
        text = _single_line(value, 1800)
        limit = 480
        if len(text) <= limit:
            return text
        head = 360
        tail = limit - head - 3
        return f"{text[:head]}...{text[-tail:]}"

    async def _reaction_embedding_vector(self, provider: Any, text: str) -> list[float]:
        if not self._is_reaction_embedding_provider(provider):
            return []
        limit = max(0, _safe_int(runtime_persona_setting(self, 'reaction_expression_embedding_timeout_ms', 5000), 5000, 0))
        async def wait_result(value: Any) -> Any:
            if not inspect.isawaitable(value):
                return value
            if limit <= 0:
                return await value
            return await asyncio.wait_for(value, timeout=limit / 1000.0)
        get_embedding = getattr(provider, "get_embedding", None)
        input_text = self._reaction_embedding_input_text(text)
        if callable(get_embedding):
            payload = await wait_result(get_embedding(input_text))
        else:
            get_embeddings = getattr(provider, "get_embeddings", None)
            if callable(get_embeddings):
                payload = await wait_result(get_embeddings([input_text]))
            else:
                get_batch = getattr(provider, "get_embeddings_batch", None)
                if not callable(get_batch):
                    return []
                try:
                    payload = await wait_result(get_batch([input_text], batch_size=1, tasks_limit=1, max_retries=1))
                except TypeError:
                    payload = await wait_result(get_batch([input_text]))
        return ReactionAssetLibrary.normalize_embedding_vector(payload)

    async def _reaction_embedding_vectors(self, provider: Any, texts: list[str]) -> list[list[float]]:
        cleaned = [
            self._reaction_embedding_input_text(item)
            for item in texts
            if self._reaction_embedding_input_text(item)
        ]
        if not cleaned or not self._is_reaction_embedding_provider(provider):
            return []
        if len(cleaned) == 1:
            vector = await self._reaction_embedding_vector(provider, cleaned[0])
            return [vector] if vector else []

        limit = max(0, _safe_int(runtime_persona_setting(self, 'reaction_expression_embedding_timeout_ms', 5000), 5000, 0))

        async def wait_result(value: Any) -> Any:
            if not inspect.isawaitable(value):
                return value
            if limit <= 0:
                return await value
            return await asyncio.wait_for(value, timeout=limit / 1000.0)

        payload: Any = None
        get_embeddings = getattr(provider, "get_embeddings", None)
        get_batch = getattr(provider, "get_embeddings_batch", None)
        if callable(get_embeddings):
            try:
                payload = await wait_result(get_embeddings(cleaned))
            except Exception as exc:
                logger.debug(
                    "批量表情向量请求失败，回退逐条生成: error_type=%s",
                    type(exc).__name__,
                )
                return await asyncio.gather(
                    *(self._reaction_embedding_vector(provider, item) for item in cleaned)
                )
        elif callable(get_batch):
            try:
                payload = await wait_result(
                    get_batch(cleaned, batch_size=min(32, len(cleaned)), tasks_limit=2, max_retries=1)
                )
            except TypeError:
                try:
                    payload = await wait_result(get_batch(cleaned))
                except Exception as exc:
                    logger.debug(
                        "批量表情向量请求失败，回退逐条生成: error_type=%s",
                        type(exc).__name__,
                    )
                    return await asyncio.gather(
                        *(self._reaction_embedding_vector(provider, item) for item in cleaned)
                    )
            except Exception as exc:
                logger.debug(
                    "批量表情向量请求失败，回退逐条生成: error_type=%s",
                    type(exc).__name__,
                )
                return await asyncio.gather(
                    *(self._reaction_embedding_vector(provider, item) for item in cleaned)
                )
        else:
            return await asyncio.gather(
                *(self._reaction_embedding_vector(provider, item) for item in cleaned)
            )

        rows = payload
        if isinstance(payload, dict):
            rows = next(
                (payload.get(key) for key in ("data", "embeddings", "vectors") if isinstance(payload.get(key), list)),
                payload,
            )
        elif not isinstance(payload, (list, tuple)):
            for attribute in ("data", "embeddings", "vectors"):
                value = getattr(payload, attribute, None)
                if isinstance(value, (list, tuple)):
                    rows = value
                    break
        if not isinstance(rows, (list, tuple)):
            return []
        vectors = [ReactionAssetLibrary.normalize_embedding_vector(item) for item in rows]
        return vectors if len(vectors) == len(cleaned) and all(vectors) else []

    async def _reaction_embedding_backfill(self, library: Any, provider: Any, provider_id: str) -> None:
        try:
            batch_size = max(1, min(100, _safe_int(runtime_persona_setting(self, 'reaction_expression_embedding_backfill_batch_size', 24), 24, 1)))
            rows = await asyncio.to_thread(library.list_embedding_missing, provider_id, limit=batch_size)
            updates: list[dict[str, Any]] = []
            for item, text_hash in rows:
                try:
                    vector = await self._reaction_embedding_vector(provider, library.embedding_text(item))
                except Exception as exc:
                    logger.debug("表情向量补齐失败: provider=%s error_type=%s", provider_id, type(exc).__name__)
                    continue
                if vector:
                    updates.append({"id": item.get("id"), "text_hash": text_hash, "vector": vector})
            if updates:
                await asyncio.to_thread(library.upsert_embeddings, provider_id, updates)
                logger.info("已补齐表情语义向量: provider=%s count=%s", provider_id, len(updates))
        finally:
            inflight = getattr(self, "_reaction_embedding_backfill_inflight", set())
            inflight.discard(provider_id)

    def _schedule_reaction_embedding_backfill(self, library: Any, provider: Any, provider_id: str) -> None:
        if not bool(runtime_persona_setting(self, 'reaction_expression_embedding_backfill_enabled', True)):
            return
        inflight = getattr(self, "_reaction_embedding_backfill_inflight", None)
        if not isinstance(inflight, set):
            inflight = set()
            setattr(self, "_reaction_embedding_backfill_inflight", inflight)
        if provider_id in inflight:
            return
        now = time.monotonic()
        last_runs = getattr(self, "_reaction_embedding_backfill_last_run", None)
        if not isinstance(last_runs, dict):
            last_runs = {}
            setattr(self, "_reaction_embedding_backfill_last_run", last_runs)
        interval = max(0, _safe_int(runtime_persona_setting(self, 'reaction_expression_embedding_backfill_interval_seconds', 300), 300, 0))
        if interval and now - _safe_float(last_runs.get(provider_id), 0.0, 0.0) < interval:
            return
        inflight.add(provider_id)
        last_runs[provider_id] = now
        coroutine = self._reaction_embedding_backfill(library, provider, provider_id)
        creator = getattr(self, "_create_lifecycle_background_task", None)
        try:
            if callable(creator):
                creator(coroutine, label=f"reaction_embedding:{provider_id[:24]}")
            else:
                asyncio.create_task(coroutine)
        except Exception:
            inflight.discard(provider_id)
            coroutine.close()

    @staticmethod
    def _reaction_expression_lookup_cache_key(
        provider: Any,
        query: str,
        context: str,
        meme_only: bool,
        scope: str = "",
        revision: str = "",
    ) -> tuple[int, str, str, bool, str, str]:
        def normalize(value: Any) -> str:
            return re.sub(r"\s+", " ", str(value or "")).strip().casefold()

        return (
            id(provider),
            normalize(query),
            normalize(context),
            bool(meme_only),
            normalize(scope),
            normalize(revision),
        )

    @staticmethod
    def _reaction_expression_lookup_cache_revision(provider: Any) -> str:
        """Return a cheap catalog revision so UI edits do not leave stale hits alive."""
        revision_getter = getattr(provider, "selection_revision", None)
        if not callable(revision_getter):
            revision_getter = getattr(provider, "lookup_revision", None)
        if callable(revision_getter):
            try:
                return str(revision_getter() or "")
            except Exception:
                pass
        catalog_path = getattr(provider, "catalog_path", None)
        if catalog_path is None:
            return ""
        try:
            stat = os.stat(catalog_path)
            return f"{int(stat.st_mtime_ns)}:{int(stat.st_size)}"
        except (OSError, TypeError, ValueError):
            return ""

    @staticmethod
    def _reaction_expression_selection_revision(
        selection_preferences: Any,
        selection_signature: Any = "",
    ) -> str:
        """Hash the bounded preference snapshot used by reaction selection."""
        if not isinstance(selection_preferences, dict):
            return ""
        signature = _single_line(
            selection_signature or selection_preferences.get("intent_signature"),
            40,
        )
        raw_assets = selection_preferences.get("assets")
        rows: list[dict[str, Any]] = []
        if isinstance(raw_assets, dict):
            raw_assets = [
                {"key": key, **value}
                for key, value in raw_assets.items()
                if isinstance(value, dict)
            ]
        if isinstance(raw_assets, list):
            for raw_item in raw_assets:
                if not isinstance(raw_item, dict):
                    continue
                key = _single_line(raw_item.get("key"), 180)
                if not key:
                    continue
                rows.append(
                    {
                        "key": key,
                        "score": _safe_int(raw_item.get("score"), 0, -20, 20),
                        "positive_count": _safe_int(
                            raw_item.get("positive_count"), 0, 0, 1000
                        ),
                        "negative_count": _safe_int(
                            raw_item.get("negative_count"), 0, 0, 1000
                        ),
                        "intent_score": _safe_int(
                            raw_item.get("intent_score"), 0, -8, 8
                        ),
                    }
                )
        if not rows:
            return ""
        rows.sort(key=lambda item: item["key"])
        payload = json.dumps(
            {"intent_signature": signature, "assets": rows},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _reaction_expression_lookup_cache_get(
        self,
        key: tuple[int, str, str, bool, str, str],
    ) -> dict[str, Any] | None:
        cache = getattr(self, "_reaction_expression_lookup_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            setattr(self, "_reaction_expression_lookup_cache", cache)
        now = time.monotonic()
        for cached_key, entry in list(cache.items()):
            if not isinstance(entry, dict) or now > _safe_float(entry.get("expires_at"), 0.0):
                cache.pop(cached_key, None)
        entry = cache.get(key)
        if not isinstance(entry, dict):
            return None
        lookup = entry.get("lookup")
        if not isinstance(lookup, dict):
            cache.pop(key, None)
            return None
        if lookup.get("success"):
            cached_path = _path_text(lookup.get("path"), 1000)
            if not cached_path or not os.path.isfile(cached_path):
                cache.pop(key, None)
                return None
        return dict(lookup)

    def _reaction_expression_lookup_cache_put(
        self,
        key: tuple[int, str, str, bool, str, str],
        lookup: dict[str, Any],
    ) -> None:
        if not isinstance(lookup, dict):
            return
        status = _single_line(lookup.get("status"), 40).lower()
        success = bool(lookup.get("success"))
        if success:
            image_path = _path_text(lookup.get("path"), 1000)
            if not image_path or not os.path.isfile(image_path):
                return
            ttl_seconds = 120.0
        elif status in {"not_found", "empty_library"}:
            ttl_seconds = 30.0
        else:
            return
        cache = getattr(self, "_reaction_expression_lookup_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            setattr(self, "_reaction_expression_lookup_cache", cache)
        now = time.monotonic()
        cache[key] = {
            "lookup": dict(lookup),
            "created_at": now,
            "expires_at": now + ttl_seconds,
        }
        if len(cache) > 48:
            oldest = sorted(
                cache.items(),
                key=lambda item: _safe_float(item[1].get("created_at"), 0.0)
                if isinstance(item[1], dict)
                else 0.0,
            )
            for cached_key, _entry in oldest[: len(cache) - 48]:
                cache.pop(cached_key, None)

    def _reaction_expression_lookup_context(
        self,
        user: dict[str, Any],
        intent: dict[str, Any],
        *,
        profile_snapshot: dict[str, Any] | None = None,
    ) -> str:
        parts = [
            "实验性表情表达：仅在候选自然贴合时选择，不合适时允许不返回图片。",
            f"沟通用途：{_single_line(intent.get('purpose'), 120)}"
            if intent.get("purpose")
            else "",
            f"表达情绪：{_single_line(intent.get('emotion'), 80)}"
            if intent.get("emotion")
            else "",
            f"表达强度：{_safe_int(intent.get('intensity'), 0, 0, 5)}/5",
            f"当前语境：{_single_line(intent.get('context'), 500)}"
            if intent.get("context")
            else "",
        ]
        candidates = intent.get("candidate_queries")
        if isinstance(candidates, list) and candidates:
            parts.append(f"候选检索表达：{'；'.join(_single_line(item, 100) for item in candidates)}")
        intent_profile = (
            profile_snapshot
            if isinstance(profile_snapshot, dict) and profile_snapshot
            else user.get("intent_profile")
        )
        if isinstance(intent_profile, dict) and intent_profile:
            parts.append(
                "近期用户意图："
                + _single_line(json.dumps(intent_profile, ensure_ascii=False), 260)
            )
        expression_builder = getattr(self, "_build_expression_decision_for_user", None)
        if callable(expression_builder):
            try:
                decision = expression_builder(
                    user,
                    message_intent={"requested_content_tier": "normal"},
                    passive_reengagement=True,
                )
                expression = decision.to_dict() if hasattr(decision, "to_dict") else dict(decision or {})
                parts.append(
                    "统一表达边界："
                    f"档位={_single_line(expression.get('expression_band'), 24) or 'relaxed'}，"
                    f"语气={_single_line(expression.get('tone'), 24) or 'steady'}，"
                    f"追问={'允许' if expression.get('followup') else '关闭'}，"
                    f"内容尺度={_single_line(expression.get('content_tier'), 16) or 'normal'}"
                )
            except Exception:
                pass
        preference = ensure_reaction_expression_state(user).get("preference")
        if isinstance(preference, dict):
            score = _safe_int(preference.get("score"), 0, -20, 20)
            if score:
                parts.append(f"用户对近期表情表达的轻量偏好分：{score}")
        return _single_line("；".join(part for part in parts if part), 1000)

    def _record_reaction_expression_feedback(
        self,
        user: dict[str, Any],
        signal: str,
        text: str,
        *,
        scope_key: str = "",
    ) -> dict[str, Any]:
        state = ensure_reaction_expression_state(user)
        return record_reaction_expression_feedback(
            state,
            signal,
            text,
            now=_now_ts(),
            event_limit=max(8, _safe_int(runtime_persona_setting(self, 'reaction_expression_candidate_limit', 6), 6, 1, 16) * 2),
            scope_key=scope_key,
        )

    def _apply_reaction_expression_feedback(
        self,
        user: dict[str, Any],
        text: str,
        *,
        scope_key: str = "",
    ) -> dict[str, Any]:
        state = ensure_reaction_expression_state(user)
        now = _now_ts()
        preference_change = sync_reaction_expression_auto_preference(
            state, text, now=now, scope_key=scope_key
        )
        signal = classify_reaction_expression_feedback(
            state,
            text,
            now=now,
            scope_key=scope_key,
        )
        if not signal:
            if preference_change:
                return {
                    "auto_preference": preference_change,
                    "score": _safe_int(
                        (state.get("preference") or {}).get("score"),
                        0,
                        -20,
                        20,
                    ),
                }
            return {}
        result = record_reaction_expression_feedback(
            state,
            signal,
            text,
            now=now,
            event_limit=max(8, _safe_int(runtime_persona_setting(self, 'reaction_expression_candidate_limit', 6), 6, 1, 16) * 2),
            scope_key=scope_key,
        )
        if preference_change:
            result["auto_preference"] = preference_change
        return result

    async def _pc_find_reaction_image_impl(
        self,
        event: AstrMessageEvent,
        query: str = "",
        search_context: str = "",
        meme_only: bool = True,
        send: bool = True,
        caption: str = "",
        low_latency: bool = False,
        internal_attachment: bool = False,
        context: str = "",
        selection_preferences: Any = None,
        selection_signature: str = "",
    ) -> str:
        scope = self._reaction_expression_scope(event)
        preference_snapshot = (
            selection_preferences if isinstance(selection_preferences, dict) else {}
        )
        preference_signature = _single_line(
            selection_signature or preference_snapshot.get("intent_signature"),
            40,
        )
        preference_revision = self._reaction_expression_selection_revision(
            preference_snapshot,
            preference_signature,
        )
        query_text = _single_line(query, 500)
        if not query_text:
            getter = getattr(event, "get_message_str", None)
            query_text = _single_line(
                getter() if callable(getter) else getattr(event, "message_str", ""),
                500,
            )
        if not query_text:
            self._log_reaction_expression_event(
                event,
                stage="lookup",
                decision="miss",
                reason="missing_query",
                scope=scope,
                status="need_query",
                found=False,
                sent=False,
            )
            return json.dumps(
                {
                    "status": "need_query",
                    "success": False,
                    "found": False,
                    "sent": False,
                    "message": "缺少表情包检索需求",
                    "must_not_claim_sent": True,
                },
                ensure_ascii=False,
            )

        def bool_arg(value: Any, default: bool) -> bool:
            if isinstance(value, bool):
                return value
            if value is None:
                return default
            normalized = str(value).strip().lower()
            if normalized in {"1", "true", "yes", "on", "是", "发送"}:
                return True
            if normalized in {"0", "false", "no", "off", "否", "不发送"}:
                return False
            return default

        send_image = bool_arg(send, True)
        meme_filter = bool_arg(meme_only, True)
        visible_caption = self._sanitize_photo_tool_caption(caption, limit=500)
        if send_image and not visible_caption:
            return json.dumps(
                {
                    "status": "missing_visible_caption",
                    "success": False,
                    "found": False,
                    "sent": False,
                    "message": "发送表情包前需要同时提供一条完整的可见正文",
                    "must_not_claim_sent": True,
                    "final_response_instruction": "请保留完整自然文字回复；不要用图片替代正文。",
                },
                ensure_ascii=False,
            )
        if send_image:
            caption = visible_caption
        if not search_context and isinstance(context, str):
            search_context = context
        lookup_context = _single_line(search_context, 1000)
        snapshot_builder = getattr(self, "_build_companion_scene_snapshot", None)
        snapshot_formatter = getattr(self, "_format_companion_scene_snapshot", None)
        if callable(snapshot_builder) and callable(snapshot_formatter):
            try:
                sender_getter = getattr(event, "get_sender_id", None)
                sender_id = self._reaction_expression_event_storage_id(
                    event,
                    sender_getter() if callable(sender_getter) else "",
                )
                users = self.data.get("users") if isinstance(getattr(self, "data", None), dict) and isinstance(self.data.get("users"), dict) else {}
                current_user = users.get(sender_id) if sender_id else None
                if isinstance(current_user, dict):
                    current_user = dict(current_user)
                    current_user.setdefault("user_id", sender_id)
                scene_text = _single_line(
                    snapshot_formatter(
                        snapshot_builder(current_user if isinstance(current_user, dict) else None),
                        purpose="image_search",
                    ),
                    620,
                )
                if scene_text:
                    scene_note = f"Bot当前情境（仅辅助判断回应情绪，不覆盖用户的明确需求）：{scene_text}"
                    lookup_context = _single_line(
                        "；".join(part for part in (lookup_context, scene_note) if part),
                        1000,
                    )
            except Exception as exc:
                self._log_reaction_expression_event(
                    event,
                    stage="degrade",
                    decision="failed",
                    reason="scene_snapshot_failed",
                    scope=scope,
                    error_type=type(exc).__name__,
                )

        # Q6 is an optional, hash-locked local source. A hit wins before the
        # editable reaction library, while every miss preserves its existing
        # lookup, authorization, reservation and delivery behavior.
        owned_lookup_finder = getattr(self, "_find_owned_reaction_asset", None)
        owned_lookup = (
            owned_lookup_finder(
                query_text,
                search_context=lookup_context,
                meme_only=meme_filter,
            )
            if callable(owned_lookup_finder)
            else None
        )
        library = self._reaction_asset_library()
        if owned_lookup is None and (library is None or not library.has_enabled_assets()):
            self._log_reaction_expression_event(
                event,
                stage="lookup",
                decision="miss",
                reason="library_unavailable",
                scope=scope,
                status="unavailable",
                found=False,
                sent=False,
            )
            return json.dumps(
                {
                    "status": "unavailable",
                    "success": False,
                    "found": False,
                    "sent": False,
                    "message": "Private Companion 表情包素材库为空，请先在实验功能页导入并启用素材",
                    "must_not_claim_sent": True,
                },
                ensure_ascii=False,
            )

        embedding_provider = None
        embedding_provider_id = ""
        embedding_query: list[float] = []
        if bool(runtime_persona_setting(self, 'reaction_expression_embedding_enabled', False)):
            try:
                embedding_provider, embedding_provider_id = await self._reaction_embedding_provider()
                if embedding_provider is not None and embedding_provider_id:
                    setattr(self, "_reaction_embedding_active_provider_id", embedding_provider_id)
                    self._schedule_reaction_embedding_backfill(
                        library, embedding_provider, embedding_provider_id
                    )
                    embedding_query = await self._reaction_embedding_vector(
                        embedding_provider,
                        "；".join(part for part in (query_text, lookup_context) if part),
                    )
            except Exception as exc:
                logger.debug(
                    "表情查询向量生成失败，回退关键词: provider=%s error_type=%s",
                    embedding_provider_id or "<auto>",
                    type(exc).__name__,
                )
                embedding_query = []

        lookup_started = time.perf_counter()
        cache_hit = False
        lookup_error_type = ""
        lookup = dict(owned_lookup) if isinstance(owned_lookup, dict) else None
        if lookup is None:
            lookup_revision = self._reaction_expression_lookup_cache_revision(library)
            if embedding_provider_id:
                lookup_revision = f"{lookup_revision}|embedding:{embedding_provider_id}"
            if preference_revision:
                lookup_revision = f"{lookup_revision}|preference:{preference_revision}"
            cache_key = self._reaction_expression_lookup_cache_key(
                library,
                query_text,
                lookup_context,
                meme_filter,
                scope,
                lookup_revision,
            )
            lookup = (
                self._reaction_expression_lookup_cache_get(cache_key)
                if low_latency
                else None
            )
            if isinstance(lookup, dict):
                cache_hit = True
        if lookup is None:
            try:
                find_kwargs = {
                    "context": lookup_context,
                    "scope": scope,
                    "selection_preferences": preference_snapshot,
                    "selection_signature": preference_signature,
                }
                if embedding_query and embedding_provider_id:
                    find_kwargs.update(
                        {
                            "embedding_query": embedding_query,
                            "embedding_provider_id": embedding_provider_id,
                            "embedding_score_threshold": runtime_persona_setting(self, 'reaction_expression_embedding_score_threshold', 0.42),
                            "embedding_weight": runtime_persona_setting(self, 'reaction_expression_embedding_weight', 0.7),
                            "embedding_candidate_limit": runtime_persona_setting(self, 'reaction_expression_embedding_candidate_limit', 1200),
                        }
                    )
                lookup = await asyncio.to_thread(
                    library.find,
                    query_text,
                    **find_kwargs,
                )
                if lookup is None:
                    lookup = {
                        "success": False,
                        "status": "not_found",
                        "message": "素材库中没有足够贴合当前语境的表情包",
                    }
            except Exception as exc:
                lookup_error_type = type(exc).__name__
                logger.warning(
                    "自有表情包素材库检索失败: error_type=%s",
                    lookup_error_type,
                )
                lookup = {
                    "success": False,
                    "status": "error",
                    "message": f"图库检索失败：{_single_line(exc, 160)}",
                }
            if low_latency and isinstance(lookup, dict) and owned_lookup is None:
                self._reaction_expression_lookup_cache_put(cache_key, lookup)
        lookup_latency_ms = round(
            max(0.0, (time.perf_counter() - lookup_started) * 1000.0), 2
        )
        if low_latency:
            self._note_reaction_expression_runtime(
                lookups=0 if cache_hit else 1,
                cache_hits=1 if cache_hit else 0,
                last_reason="cache_hit" if cache_hit else "lookup",
                latency_ms=lookup_latency_ms,
                lookup_elapsed_ms=0.0 if cache_hit else lookup_latency_ms,
            )
        if not isinstance(lookup, dict) or not lookup.get("success"):
            lookup = lookup if isinstance(lookup, dict) else {}
            lookup_status = _single_line(lookup.get("status"), 40) or "not_found"
            self._log_reaction_expression_event(
                event,
                stage="lookup",
                decision="miss",
                reason="lookup_error" if lookup_status == "error" else lookup_status,
                scope=scope,
                status=lookup_status,
                found=False,
                sent=False,
                cache_hit=cache_hit,
                latency_ms=lookup_latency_ms,
                error_type=lookup_error_type,
            )
            return json.dumps(
                {
                    "status": lookup_status,
                    "success": False,
                    "found": False,
                    "sent": False,
                    "message": _single_line(lookup.get("message"), 220) or "图库中没有找到合适的表情包",
                    "need": _single_line(lookup.get("need"), 220),
                    "reason": _single_line(lookup.get("reason"), 220),
                    "cache_hit": cache_hit,
                    "lookup_latency_ms": lookup_latency_ms,
                    "must_not_claim_sent": True,
                },
                ensure_ascii=False,
            )

        image_path = _path_text(lookup.get("path"), 1000)
        if not image_path or not os.path.isfile(image_path):
            self._log_reaction_expression_event(
                event,
                stage="lookup",
                decision="miss",
                reason="missing_file",
                scope=scope,
                status="missing_file",
                found=False,
                sent=False,
                image_id=lookup.get("image_id"),
                confidence=lookup.get("confidence"),
                cache_hit=cache_hit,
                latency_ms=lookup_latency_ms,
                match_basis=self._reaction_expression_match_basis(lookup),
            )
            return json.dumps(
                {
                    "status": "missing_file",
                    "success": False,
                    "found": False,
                    "sent": False,
                    "message": "匹配到的图库图片文件不可用",
                    "cache_hit": cache_hit,
                    "lookup_latency_ms": lookup_latency_ms,
                    "must_not_claim_sent": True,
                },
                ensure_ascii=False,
            )

        self._log_reaction_expression_event(
            event,
            stage="lookup",
            decision="hit",
            reason="matched",
            scope=scope,
            status=_single_line(lookup.get("status"), 40) or "success",
            found=True,
            sent=False,
            image_id=lookup.get("image_id"),
            confidence=lookup.get("confidence"),
            cache_hit=cache_hit,
            latency_ms=lookup_latency_ms,
            match_basis=self._reaction_expression_match_basis(lookup),
        )

        vision_review: dict[str, Any] | None = None
        verify_mode = _single_line(
            runtime_persona_setting(self, "reaction_expression_vision_verify_mode", "embedding"), 20
        ).lower()
        # The automatic tag path prepares the image here (send=False,
        # internal_attachment=True) and delivers it after the text, so it needs
        # the same pre-send check as a direct tool send.
        if (send_image or internal_attachment) and not low_latency and (
            verify_mode == "always"
            or (verify_mode == "embedding" and lookup.get("match_basis") == "embedding")
        ):
            vision_review = await self._reaction_expression_vision_verify(
                event, library, lookup, query_text, lookup_context
            )
            if vision_review is not None and not vision_review.get("fit"):
                # Reservation ownership belongs to ``_pc_reaction_expression_impl``.
                # This helper is also called directly by the public lookup tool,
                # where those variables do not exist.  The caller consumes this
                # miss result and releases its own reservation when applicable.
                self._log_reaction_expression_event(
                    event,
                    stage="lookup",
                    decision="miss",
                    reason="vision_rejected",
                    scope=scope,
                    status="not_found",
                    found=False,
                    sent=False,
                    image_id=lookup.get("image_id"),
                    confidence=lookup.get("confidence"),
                    cache_hit=cache_hit,
                    latency_ms=lookup_latency_ms,
                    match_basis=self._reaction_expression_match_basis(lookup),
                )
                return json.dumps(
                    {
                        "status": "not_found",
                        "success": False,
                        "found": False,
                        "sent": False,
                        "message": "候选表情包经视觉复核后不贴合当前语境，未发送",
                        "image_description": _single_line(vision_review.get("description"), 300),
                        "reason": _single_line(vision_review.get("reason"), 120),
                        "cache_hit": cache_hit,
                        "lookup_latency_ms": lookup_latency_ms,
                        "must_not_claim_sent": True,
                    },
                    ensure_ascii=False,
                )

        sent = False
        delivery: dict[str, Any] = {}
        visible_caption = self._sanitize_photo_tool_caption(caption, limit=120)
        if send_image:
            try:
                delivery = await self._deliver_generated_image_to_event(
                    event,
                    image_path=image_path,
                    caption=visible_caption,
                    reaction_image=True,
                )
            except Exception as exc:
                delivery = {
                    "sent": False,
                    "destination": "error",
                    "message": f"图片发送失败：{_single_line(exc, 180) or '未知错误'}",
                }
            sent = bool(delivery.get("sent"))
            if sent:
                try:
                    setattr(event, "_private_companion_photo_tool_sent", True)
                    setattr(event, "_private_companion_photo_tool_sent_caption", visible_caption)
                except Exception:
                    pass

        tags = [
            _single_line(item, 60)
            for item in lookup.get("tags", [])
            if _single_line(item, 60)
        ]
        need = _single_line(lookup.get("need"), 220) or query_text
        match_reason = _single_line(lookup.get("reason"), 220)
        snapshot_caption = "；".join(
            part
            for part in (
                f"图片画面：{_single_line(lookup.get('description') or lookup.get('image_description'), 200)}"
                if lookup.get("description") or lookup.get("image_description")
                else "",
                f"图库标签：{'、'.join(tags[:8])}" if tags else "",
                f"表达需求：{need}" if need else "",
                f"选图依据：{match_reason}" if match_reason else "",
            )
            if part
        )
        if sent and snapshot_caption:
            try:
                user_id = self._reaction_expression_event_storage_id(event, event.get_sender_id())
            except Exception:
                user_id = ""
            if user_id:
                async with self._data_lock:
                    user = self._reaction_expression_state_owner(event, user_id)
                    if not isinstance(user, dict):
                        return json.dumps(
                            self._reaction_expression_skip_result(
                                "state_unavailable",
                                event=event,
                                scope=scope,
                            ),
                            ensure_ascii=False,
                        )
                    self._remember_recent_photo_share_snapshot(
                        user,
                        caption=snapshot_caption,
                        topic=need,
                        motive=match_reason,
                        reason="reaction_library_image",
                        subject_owner="unknown",
                    )
                    try:
                        self._save_data_sync(sections={"users"})
                    except TypeError:
                        # Keep lightweight hosts/test doubles compatible with
                        # the historical no-argument persistence hook.
                        self._save_data_sync()

        if sent:
            self._mark_reaction_asset_used(lookup.get("image_id"), event=event)
        delivery_uncertain = bool(delivery.get("uncertain"))
        success = bool(image_path and (not send_image or sent))
        if send_image:
            self._log_reaction_expression_event(
                event,
                stage="delivery",
                decision=(
                    "sent"
                    if sent
                    else "uncertain"
                    if delivery_uncertain
                    else "failed"
                ),
                reason=(
                    "delivered"
                    if sent
                    else "delivery_uncertain"
                    if delivery_uncertain
                    else "delivery_failed"
                ),
                scope=scope,
                status=(
                    "success"
                    if sent
                    else "delivery_uncertain"
                    if delivery_uncertain
                    else "delivery_failed"
                ),
                found=True,
                sent=sent,
                image_id=lookup.get("image_id"),
                confidence=lookup.get("confidence"),
                cache_hit=cache_hit,
                latency_ms=lookup_latency_ms,
                delivery=delivery.get("destination"),
                match_basis=self._reaction_expression_match_basis(lookup),
            )
        result_payload = {
            "status": (
                "success"
                if success
                else "delivery_uncertain"
                if delivery_uncertain
                else "delivery_failed"
            ),
            "success": success,
            "found": True,
            "send_requested": send_image,
            "sent": sent,
            "delivery_uncertain": delivery_uncertain,
            "message": (
                _single_line(delivery.get("message"), 220)
                if send_image
                else "已找到图库图片，但按请求未发送"
            ),
            "image_id": _single_line(lookup.get("image_id"), 120),
            "tags": tags,
            "need": need,
            "reason": match_reason,
            "confidence": _safe_float(lookup.get("confidence"), 0.0, 0.0, 1.0),
            "image_description": _single_line(
                (vision_review or {}).get("description")
                or getattr(self, "_reaction_vision_description_cache", {}).get(_single_line(lookup.get("asset_id"), 64))
                or lookup.get("description"),
                300,
            ),
            "delivery": _single_line(delivery.get("destination"), 40),
            "cache_hit": cache_hit,
            "lookup_latency_ms": lookup_latency_ms,
            "must_not_claim_sent": not sent,
            "final_response_instruction": (
                f"完整正文 caption 与图片已一并发送。最终回复不要留空，只输出 {PHOTO_TOOL_SILENT_SENTINEL}。"
                if sent
                else ""
            ),
        }
        if (
            _single_line(lookup.get("source"), 60) != "owned_reaction_assets"
            or internal_attachment
        ):
            result_payload["path"] = image_path
        return json.dumps(result_payload, ensure_ascii=False)

