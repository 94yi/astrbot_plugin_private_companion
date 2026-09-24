# -*- coding: utf-8 -*-
"""生图执行域。

由 tools/split_mixin_domain.py 从 llm_tool_actions.py 机械抽取（2 个方法 + 0 个模块级名字 + 0 个类级赋值 / 1254 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 LlmToolActionsMixin）。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from .helpers import (
    _missing_optional_model_dependency,
    _now_ts,
    _path_text,
    _photo_group_request_matches,
    _safe_float,
    _single_line,
)
from .llm_tool_actions_shared import PHOTO_TOOL_SILENT_SENTINEL, logger
from .persona_config import runtime_persona_setting
from .photo_nai_params import merge_user_photo_nai_params, recent_cached_photo_nai_params
from astrbot.api.event import AstrMessageEvent
from typing import Any



class LlmToolActionsPhotoGenerateMixin:
    """生图执行域（从 LlmToolActionsMixin 拆出）。"""


    def _photo_tool_call_timeout_seconds(self) -> float:
        context = getattr(self, "context", None)
        getter = getattr(context, "get_config", None)
        if not callable(getter):
            return 120.0
        try:
            cfg = getter()
        except Exception:
            return 120.0
        provider_settings = cfg.get("provider_settings", {}) if isinstance(cfg, dict) else {}
        if not isinstance(provider_settings, dict):
            return 120.0
        return _safe_float(provider_settings.get("tool_call_timeout"), 120.0, 1.0, 3600.0)

    async def _pc_generate_photo_impl(
        self,
        event: AstrMessageEvent,
        prompt: str = "",
        kind: str = "text2img",
        reference_image_path: str = "",
        reference_image_paths: Any = None,
        image_size: str = "",
        send: bool = True,
        caption: str = "",
        scene_preset: str = "",
        **kwargs,
    ) -> str:
        def public_receipt(
            payload: dict[str, Any],
            *,
            ensure_ascii: bool = False,
            known_paths: tuple[Any, ...] = (),
        ) -> str:
            return json.dumps(
                self._sanitize_photo_tool_result_payload(
                    payload,
                    known_paths=known_paths,
                ),
                ensure_ascii=ensure_ascii,
            )

        if not self._photo_generation_runtime_available():
            return public_receipt(
                {
                    "status": "unavailable",
                    "success": False,
                    "generated": False,
                    "sent": False,
                    "error_code": "image_extension_unavailable",
                    "message": "生图扩展未安装、未启用或尚未就绪。",
                    "must_not_claim_sent": True,
                    "final_response_instruction": "自然说明当前不能生成图片，不要声称图片已经生成或发送，也不要在本轮重试。",
                },
                ensure_ascii=False,
            )

        tool_started_at = time.monotonic()
        scope_getter = getattr(self, "_photo_generation_scope", None)
        initial_scope = ""
        if callable(scope_getter):
            try:
                initial_scope = _single_line(scope_getter(event), 40).lower()
            except Exception:
                initial_scope = ""
        proactive_request = bool(
            initial_scope == "proactive"
            or getattr(event, "private_companion_proactive_framework", False)
        )
        permission_getter = getattr(
            self,
            "_user_requested_photo_generation_allowed",
            None,
        )
        if callable(permission_getter):
            try:
                user_request_allowed = bool(permission_getter(event))
            except Exception:
                user_request_allowed = False
        else:
            user_request_allowed = bool(
                runtime_persona_setting(
                    self,
                    "enable_user_requested_photo_generation",
                    True,
                )
            )
        if not proactive_request and not user_request_allowed:
            return public_receipt(
                {
                    "status": "disabled",
                    "success": False,
                    "generated": False,
                    "sent": False,
                    "message": "管理员已关闭用户请求生图/改图。",
                    "must_not_claim_sent": True,
                    "retryable": False,
                },
                ensure_ascii=False,
            )
        mode = _single_line(runtime_persona_setting(self, 'natural_language_photo_generation_mode', "tool_first"), 40).lower()
        if mode == "off" and not proactive_request:
            return public_receipt({"status": "disabled", "message": "非指令生图/改图已关闭；显式指令仍可使用“陪伴 生图/自拍/改图”。"}, ensure_ascii=False)
        if not runtime_persona_setting(self, 'enable_photo_text_action', False):
            return public_receipt({"status": "disabled", "message": "主动拍照/生图能力未启用"}, ensure_ascii=False)
        scope_checker = getattr(self, "_photo_generation_scope_allowed", None)
        structured_generator = getattr(self, "_generate_photo_image_result", None)
        legacy_generator = getattr(self, "_generate_photo_image", None)
        if not callable(structured_generator) and not callable(legacy_generator):
            return public_receipt({"status": "disabled", "message": "缺少生图入口 _generate_photo_image"}, ensure_ascii=False)
        if not self._photo_text_available():
            return public_receipt({"status": "unavailable", "message": "当前没有可用生图后端，或已被负载/token 保护临时延后"}, ensure_ascii=False)

        content = _single_line(prompt or kwargs.get("text") or kwargs.get("description") or kwargs.get("prompt_text"), 900)
        visible_caption = self._sanitize_photo_tool_caption(caption, limit=120)
        raw_kind = _single_line(kind or kwargs.get("workflow_kind") or kwargs.get("type"), 40).lower()
        if raw_kind in {"sticker", "emoji", "meme", "表情包", "贴纸"}:
            workflow_kind = "selfie"
            intent_kind = "sticker"
        elif raw_kind in {"selfie", "portrait", "自拍", "人像", "拍照", "头像", "avatar", "cos", "cosplay", "穿搭"}:
            workflow_kind = "selfie"
            intent_kind = "selfie"
        elif raw_kind in {"edit", "改图", "修图", "重绘", "p图", "P图"}:
            workflow_kind = "edit"
            intent_kind = "edit"
        else:
            workflow_kind = "text2img"
            intent_kind = "text2img"
        inherited_nai_params = ""
        inbound_photo_text = str(getattr(event, "message_str", "") or "")[:4000]
        if workflow_kind != "edit":
            extractor = getattr(self, "_extract_user_photo_nai_params", None)
            if callable(extractor):
                try:
                    inherited_nai_params = extractor(inbound_photo_text)
                except Exception:
                    inherited_nai_params = ""
        if not content:
            return public_receipt(
                {
                    "status": "need_prompt",
                    "message": "缺少 prompt。请把要生成的画面或修改要求传入 prompt。",
                },
                ensure_ascii=False,
            )
        compact_prompt = re.sub(r"\s+", "", content)
        group_photo_requested = _photo_group_request_matches(content)
        bot_name = re.sub(r"\s+", "", _single_line(runtime_persona_setting(self, 'bot_name', ""), 80))
        assistant_in_frame = bool(
            (bot_name and bot_name in compact_prompt)
            or any(
                token in compact_prompt
                for token in (
                    "我本人",
                    "我在画面",
                    "我站在",
                    "我坐在",
                    "我躺在",
                    "我走在",
                    "我的背影",
                    "我的侧脸",
                    "我的全身",
                    "角色本人",
                    "本人出镜",
                )
            )
            or re.search(r"\b(?:the\s+assistant|assistant\s+persona|bot\s+character)\b", content, flags=re.I)
        )
        if intent_kind == "text2img" and any(token in compact_prompt for token in ("表情包", "贴纸", "sticker", "meme")):
            workflow_kind = "selfie"
            intent_kind = "sticker"
        elif intent_kind == "text2img" and (
            self._character_photo_request_matches(content)
            or group_photo_requested
            or assistant_in_frame
            or any(
                token in compact_prompt
                for token in ("自拍", "拍照", "头像", "人像", "角色本人", "本人出镜", "露脸", "穿搭", "镜前", "cos", "COS", "cosplay")
            )
        ):
            workflow_kind = "selfie"
            intent_kind = "selfie"

        try:
            requester_id = str(event.get_sender_id())
        except Exception:
            requester_id = ""
        resolver = getattr(self, "_private_user_id_for_event", None)
        if callable(resolver) and requester_id:
            requester_id = resolver(event, requester_id)
        requester = None
        request_scope = "private"
        group_gate_message = "当前群聊未启用陪伴功能，或请求者身份不可用。"
        user_getter = getattr(self, "_get_user", None)
        if callable(user_getter):
            if not requester_id:
                return public_receipt(
                    {
                        "status": "unauthorized",
                        "success": False,
                        "generated": False,
                        "sent": False,
                        "message": "这个生图工具只对已启用的陪伴对象开放。",
                        "must_not_claim_sent": True,
                        "retryable": False,
                    },
                    ensure_ascii=False,
                )
            # Group senders are not private users by default.  Looking them up
            # through ``_get_user`` would create a new private record (and the
            # configured fallback nickname) before authorization can reject it.
            scope_getter = getattr(self, "_reaction_expression_scope", None)
            try:
                private_marker = getattr(event, "is_private_chat", None)
                event_is_private = (
                    bool(private_marker())
                    if callable(private_marker)
                    else (True if private_marker is None else bool(private_marker))
                )
                request_scope = (
                    _single_line(scope_getter(event), 16).casefold()
                    if callable(scope_getter)
                    else ("private" if event_is_private else "group")
                )
            except Exception:
                request_scope = "private"
            def existing_private_user(raw_id: str) -> dict[str, Any] | None:
                data = getattr(self, "data", None)
                users = data.get("users") if isinstance(data, dict) else None
                if not isinstance(users, dict):
                    return None
                normalized = _single_line(raw_id, 160)
                if not normalized:
                    return None
                canonical = normalized
                canonicalizer = getattr(self, "_canonical_private_user_id", None)
                if callable(canonicalizer):
                    try:
                        canonical = _single_line(canonicalizer(normalized), 160) or normalized
                    except Exception:
                        canonical = normalized
                for candidate_id in dict.fromkeys((normalized, canonical)):
                    candidate = users.get(candidate_id)
                    if isinstance(candidate, dict):
                        return candidate
                for candidate in users.values():
                    if not isinstance(candidate, dict):
                        continue
                    aliases = candidate.get("alias_user_ids")
                    if (
                        _single_line(candidate.get("user_id"), 160) in {normalized, canonical}
                        or isinstance(aliases, list)
                        and any(_single_line(alias, 160) in {normalized, canonical} for alias in aliases)
                    ):
                        return candidate
                return None

            data_lock = getattr(self, "_data_lock", None)
            if data_lock is not None:
                async with data_lock:
                    requester = (
                        existing_private_user(requester_id)
                        if request_scope == "group"
                        else user_getter(requester_id)
                    )
                    group_enabled = False
                    if request_scope == "group":
                        group_id_getter = getattr(self, "_extract_group_id_from_event", None)
                        group_id = group_id_getter(event) if callable(group_id_getter) else ""
                        checker = getattr(self, "_group_enabled_for_event", None)
                        group_enabled = bool(group_id and callable(checker) and checker(group_id))
                        if not runtime_persona_setting(self, "enable_group_companion", True):
                            group_gate_message = "群聊陪伴总开关未开启。"
                        elif callable(getattr(self, "_group_allowed_by_access_mode", None)) and not self._group_allowed_by_access_mode(group_id):
                            group_gate_message = "本群不在当前群聊访问名单内。"
                        elif not group_enabled:
                            group_gate_message = "本群单独停用；请在群聊面板启用本群。"
                    requester_authorized = (group_enabled if request_scope == "group" else isinstance(requester, dict))
            else:
                requester = (
                    existing_private_user(requester_id)
                    if request_scope == "group"
                    else user_getter(requester_id)
                )
                group_enabled = False
                if request_scope == "group":
                    group_id_getter = getattr(self, "_extract_group_id_from_event", None)
                    group_id = group_id_getter(event) if callable(group_id_getter) else ""
                    checker = getattr(self, "_group_enabled_for_event", None)
                    group_enabled = bool(group_id and callable(checker) and checker(group_id))
                    if not runtime_persona_setting(self, "enable_group_companion", True):
                        group_gate_message = "群聊陪伴总开关未开启。"
                    elif callable(getattr(self, "_group_allowed_by_access_mode", None)) and not self._group_allowed_by_access_mode(group_id):
                        group_gate_message = "本群不在当前群聊访问名单内。"
                    elif not group_enabled:
                        group_gate_message = "本群单独停用；请在群聊面板启用本群。"
                requester_authorized = (group_enabled if request_scope == "group" else isinstance(requester, dict))
            if not requester_authorized:
                return public_receipt(
                    {
                        "status": "unauthorized",
                        "success": False,
                        "generated": False,
                        "sent": False,
                        "message": group_gate_message,
                        "must_not_claim_sent": True,
                        "retryable": False,
                    },
                    ensure_ascii=False,
                )
        if requester_id and requester is None and callable(user_getter):
            data_lock = getattr(self, "_data_lock", None)
            if data_lock is not None:
                async with data_lock:
                    requester = user_getter(requester_id)
            else:
                requester = user_getter(requester_id)

        photo_scope_getter = getattr(self, "_photo_generation_scope", None)
        if callable(photo_scope_getter):
            photo_scope = photo_scope_getter(
                event,
                user=requester if isinstance(requester, dict) else None,
                user_id=requester_id,
            )
        elif bool(getattr(event, "private_companion_proactive_framework", False)):
            photo_scope = "proactive"
        elif request_scope == "group":
            photo_scope = "group"
        else:
            photo_scope = ""

        scope_quota_getter = getattr(self, "_photo_generation_scope_quota_left", None)
        scope_left = (
            scope_quota_getter(
                event,
                user=requester if isinstance(requester, dict) else None,
                user_id=requester_id,
                scope=photo_scope,
            )
            if callable(scope_quota_getter)
            else None
        )
        scope_blocked = scope_left is not None and scope_left <= 0
        if not callable(scope_quota_getter) and callable(scope_checker):
            scope_blocked = not scope_checker(
                event,
                user=requester if isinstance(requester, dict) else None,
                user_id=requester_id,
            )
        if scope_blocked:
            scope_message_getter = getattr(self, "_photo_generation_scope_quota_block_message", None)
            scope_message = (
                scope_message_getter(
                    event,
                    user=requester if isinstance(requester, dict) else None,
                    user_id=requester_id,
                    scope=photo_scope,
                )
                if callable(scope_message_getter)
                else "当前不允许在这个会话范围生图/改图，或今天该范围的额度已经用完。"
            )
            return public_receipt(
                {
                    "status": "quota_exhausted",
                    "success": False,
                    "generated": False,
                    "sent": False,
                    "message": scope_message,
                    "must_not_claim_sent": True,
                    "retryable": False,
                },
                ensure_ascii=False,
            )

        if photo_scope == "proactive" and isinstance(requester, dict):
            proactive_available = True
            photo_available = getattr(self, "_photo_text_available", None)
            if callable(photo_available):
                try:
                    proactive_available = bool(photo_available(requester))
                except TypeError:
                    proactive_available = bool(photo_available())
            if not proactive_available:
                return public_receipt(
                    {
                        "status": "quota_exhausted",
                        "success": False,
                        "generated": False,
                        "sent": False,
                        "message": "今天主动生图额度已经用完，或该陪伴用户不允许主动生图。",
                        "must_not_claim_sent": True,
                        "retryable": False,
                    },
                    ensure_ascii=False,
                )
        else:
            quota_getter = getattr(self, "_command_photo_quota_left", None)
            quota_left = (
                quota_getter(requester)
                if callable(quota_getter) and isinstance(requester, dict)
                else None
            )
            if quota_left is not None and quota_left <= 0:
                quota_message_getter = getattr(self, "_command_photo_quota_block_message", None)
                quota_message = (
                    quota_message_getter()
                    if callable(quota_message_getter)
                    else "当前不允许用户请求生图/改图，或今天的用户请求生图额度已经用完。"
                )
                return public_receipt(
                    {
                        "status": "quota_exhausted",
                        "success": False,
                        "generated": False,
                        "sent": False,
                        "message": quota_message,
                        "must_not_claim_sent": True,
                        "retryable": False,
                    },
                    ensure_ascii=False,
                )

        def bool_arg(value: Any, default: bool = True) -> bool:
            if isinstance(value, bool):
                return value
            if value is None:
                return default
            text = str(value).strip().lower()
            if text in {"1", "true", "yes", "y", "on", "发送", "发出", "是"}:
                return True
            if text in {"0", "false", "no", "n", "off", "不发送", "否"}:
                return False
            return default

        send_image = bool_arg(send, True)
        if send_image:
            marker = getattr(self, "_mark_smart_imagechat_skip_proactive_emoji", None)
            if callable(marker):
                marker(event)
        reference_sources: list[str] = []

        def add_reference_source(value: Any) -> None:
            if isinstance(value, dict):
                value = value.get("path") or value.get("source") or value.get("url")
            path = _path_text(value, 1000)
            if path and path not in reference_sources:
                reference_sources.append(path)

        add_reference_source(
            reference_image_path
            or kwargs.get("reference")
            or kwargs.get("image")
            or kwargs.get("image_path")
            or kwargs.get("image_url")
        )
        raw_multi_references = (
            reference_image_paths
            if reference_image_paths is not None
            else kwargs.get("reference_images", kwargs.get("images"))
        )
        if isinstance(raw_multi_references, (list, tuple, set)):
            for raw_reference in raw_multi_references:
                add_reference_source(raw_reference)
        elif raw_multi_references:
            add_reference_source(raw_multi_references)

        if group_photo_requested:
            # 合影只能由本轮图片，或用户明确点名且已有托管参考图的关系角色授权。
            # 模型传入的路径始终不参与授权，关系角色图片仍交给下游选图器处理。
            reference_sources.clear()
            reference_path = ""
            role_reference_candidates: list[dict[str, Any]] = []
            role_reference_resolver = getattr(
                self,
                "_photo_reference_role_asset_candidates",
                None,
            )
            if bool(runtime_persona_setting(self, 'enable_photo_reference_image', False)) and callable(
                role_reference_resolver
            ):
                try:
                    resolved_candidates = role_reference_resolver(
                        request_text=content,
                    )
                    if isinstance(resolved_candidates, list):
                        role_reference_candidates = [
                            candidate
                            for candidate in resolved_candidates
                            if isinstance(candidate, dict)
                            and candidate.get("kind") == "relation_role"
                            and bool(candidate.get("role_explicit_mention"))
                            and _path_text(candidate.get("path"), 1000)
                        ]
                except Exception as exc:
                    logger.info(
                        "合影关系网角色参考图解析失败，继续检查本轮图片: %s",
                        _single_line(exc, 160),
                    )
            has_named_role_reference = bool(role_reference_candidates)
            context_resolver = getattr(self, "_photo_reference_image_from_command_context", None)
            saw_image = False
            if callable(context_resolver):
                try:
                    resolved_path, _resolved_label, saw_image = await context_resolver(event, requester_id)
                    reference_path = _path_text(resolved_path, 1000)
                except Exception as exc:
                    if not has_named_role_reference:
                        missing = _missing_optional_model_dependency(exc)
                        message = (
                            f"合影参考图解析缺少可选依赖 {missing}，请让用户重新发送或引用人物图片。"
                            if missing
                            else f"合影参考图解析失败：{_single_line(exc, 160)}"
                        )
                        return public_receipt(
                            {
                                "status": "need_reference",
                                "success": False,
                                "generated": False,
                                "sent": False,
                                "message": message,
                                "must_not_claim_sent": True,
                                "retryable": False,
                            },
                            ensure_ascii=False,
                        )
            if not reference_path and not has_named_role_reference:
                return public_receipt(
                    {
                        "status": "need_reference",
                        "success": False,
                        "generated": False,
                        "sent": False,
                        "message": (
                            "看到了本轮图片，但没能保存成可用的其他人物参考图；请让用户重新发送或引用人物图片。"
                            if saw_image
                            else "合影需要本轮随消息发送或引用的其他人物参考图，或明确点名已绑定可用参考图的关系网角色。Bot 单人人设图、今日穿搭图、纯文字描述或单独传入的路径都不算，已停止生成。"
                        ),
                        "must_not_claim_sent": True,
                        "retryable": False,
                    },
                    ensure_ascii=False,
                )
            if reference_path:
                add_reference_source(reference_path)

        resolver = getattr(self, "_photo_reference_source_to_stable_path", None)
        event_bound_resolver = getattr(self, "_photo_reference_event_bound_stable_path", None)
        resolved_reference_paths: list[str] = []
        for index, source in enumerate(reference_sources):
            # Keep the mixin compatible with lightweight/legacy hosts that do
            # not expose the optional reference normalizer. Full plugin hosts
            # still pass model-controlled sources through the untrusted path
            # guard below; Q5 managed assets use their separate ticketed sink.
            stable = source if not callable(resolver) else ""
            if callable(resolver):
                try:
                    stable = await resolver(source, stem=f"tool_{index + 1}", event=event, trusted=False)
                except Exception as exc:
                    logger.info(
                        "tool reference %s rejected: %s",
                        index + 1,
                        _single_line(exc, 160),
                    )
            if not stable and callable(event_bound_resolver):
                try:
                    stable = await event_bound_resolver(
                        event,
                        requester_id,
                        source,
                        stem=f"tool_event_{index + 1}",
                    )
                except Exception as exc:
                    logger.info(
                        "current-event reference %s could not be persisted: %s",
                        index + 1,
                        _single_line(exc, 160),
                    )
                if stable:
                    logger.info(
                        "accepted model reference after exact current-event source verification: index=%s",
                        index + 1,
                    )
            if not stable:
                logger.warning(
                    "model-controlled image reference rejected: source=%s",
                    _single_line(source, 200),
                )
                return public_receipt(
                    {
                        "status": "invalid_reference",
                        "success": False,
                        "generated": False,
                        "sent": False,
                        "message": "这张参考图不能使用。参考图只支持当前消息里的图片、插件数据目录内的图片，或公网图片链接。",
                        "must_not_claim_sent": True,
                        "retryable": False,
                    },
                    ensure_ascii=False,
                )
            resolved = stable
            if resolved and resolved not in resolved_reference_paths:
                resolved_reference_paths.append(resolved)
        reference_path = resolved_reference_paths[0] if resolved_reference_paths else ""
        if intent_kind == "edit" and not reference_path:
            context_resolver = getattr(self, "_photo_reference_image_from_command_context", None)
            if callable(context_resolver):
                try:
                    try:
                        user_id = str(event.get_sender_id())
                    except Exception:
                        user_id = ""
                    resolved_path, resolved_label, saw_image = await context_resolver(event, user_id)
                    if resolved_path:
                        reference_path = resolved_path
                        resolved_reference_paths = [resolved_path]
                    elif saw_image:
                        return public_receipt(
                            {
                                "status": "need_reference",
                                "message": "看到了图片，但没能保存成可用参考图；请让用户重新发送图片，或用“陪伴 参考图 查看”检查平台是否能取到原图。",
                            },
                            ensure_ascii=False,
                        )
                except Exception as exc:
                    missing = _missing_optional_model_dependency(exc)
                    if missing:
                        return public_receipt(
                            {
                                "status": "need_reference",
                                "message": f"改图参考图解析缺少可选依赖 {missing}，请让用户直接提供本地图片路径或图片 URL。",
                            },
                            ensure_ascii=False,
                        )
                    return public_receipt(
                        {"status": "error", "message": f"改图参考图解析失败：{_single_line(exc, 160)}"},
                        ensure_ascii=False,
                    )
            if not reference_path:
                return public_receipt(
                    {
                        "status": "need_reference",
                        "message": "改图/重绘需要参考图。可以让用户把图片和要求一起发，或引用近期图片再说“改成……”。",
                    },
                    ensure_ascii=False,
                )
        if not reference_path and intent_kind in {"selfie", "sticker"}:
            wants_indexed_references = bool(
                re.search(
                    r"(?:第(?:[一二三四五六七八九十\d]+)张|"
                    r"(?:first|second|third|fourth|fifth|sixth|seventh|eighth)\s+"
                    r"(?:image|photo|picture))",
                    compact_prompt,
                    flags=re.I,
                )
            )
            try:
                try:
                    user_id = str(event.get_sender_id())
                except Exception:
                    user_id = ""
                saw_image = False
                if wants_indexed_references:
                    multi_resolver = getattr(
                        self,
                        "_photo_reference_images_from_command_context",
                        None,
                    )
                else:
                    multi_resolver = None
                if callable(multi_resolver):
                    images, saw_image = await multi_resolver(event, user_id, limit=8)
                    resolved_reference_paths = [
                        _path_text(item[0], 1000)
                        for item in images
                        if isinstance(item, (list, tuple))
                        and item
                        and _path_text(item[0], 1000)
                    ]
                    if resolved_reference_paths:
                        reference_path = resolved_reference_paths[0]
                else:
                    context_resolver = getattr(
                        self,
                        "_photo_reference_image_from_command_context",
                        None,
                    )
                    if callable(context_resolver):
                        resolved_path, resolved_label, saw_image = await context_resolver(event, user_id)
                        if resolved_path:
                            reference_path = resolved_path
                            resolved_reference_paths = [resolved_path]
                if saw_image and not resolved_reference_paths:
                    return public_receipt(
                        {
                            "status": "need_reference",
                            "message": "看到了图片，但没能保存成可用参考图；请让用户重新发送图片，或用“陪伴 参考图 查看”检查平台是否能取到原图。",
                        },
                        ensure_ascii=False,
                    )
            except Exception as exc:
                missing = _missing_optional_model_dependency(exc)
                if missing:
                    return public_receipt(
                        {
                            "status": "need_reference",
                            "message": f"参考图解析缺少可选依赖 {missing}；如已开启参考图一致性，会改用已配置的人设参考图或今日穿搭图。",
                        },
                        ensure_ascii=False,
                    )
                return public_receipt(
                    {"status": "error", "message": f"参考图解析失败：{_single_line(exc, 160)}"},
                    ensure_ascii=False,
                )
        prompt_format_mode = ""
        prompt_format_getter = getattr(self, "_photo_generation_prompt_format_mode", None)
        if callable(prompt_format_getter):
            try:
                prompt_format_mode = (
                    _single_line(prompt_format_getter(), 40).lower() or "traditional"
                )
            except Exception as exc:
                logger.debug(
                    "tool 生图读取提示词格式失败，保留原始提示词: %s",
                    _single_line(exc, 160),
                )
                prompt_format_mode = "traditional"
        if (
            workflow_kind != "edit"
            and not proactive_request
            and request_scope == "private"
            and not inbound_photo_text.strip()
            and not inherited_nai_params
        ):
            inherited_nai_params = recent_cached_photo_nai_params(
                requester,
                now=_now_ts(),
            )
        content = merge_user_photo_nai_params(
            content,
            inherited_nai_params,
            prompt_format=prompt_format_mode,
        )
        prompt_builder = getattr(self, "_build_natural_language_photo_prompt_sections", None)
        use_natural_prompt_builder = not callable(prompt_format_getter) or prompt_format_mode in {
            "natural_language",
            "natural",
            "prose",
            "description",
            "自然语言",
            "自然语言描述",
        }
        if callable(prompt_builder) and use_natural_prompt_builder:
            prompt_sections = prompt_builder(
                prompt=content,
                kind="selfie" if intent_kind == "sticker" else intent_kind,
                has_reference=bool(resolved_reference_paths),
                memory_context="",
            )
            prompt_text = content
        else:
            prompt_sections = None
            prompt_text = content
        preset_text = _single_line(scene_preset or kwargs.get("preset") or kwargs.get("scene"), 80)
        workflow_default_preset = "表情包场景" if intent_kind == "sticker" else ""

        event_umo = _single_line(getattr(event, "unified_msg_origin", ""), 240)
        session_key = event_umo or "tool_photo"
        continuity_composer = getattr(self, "_compose_photo_continuity_key", None)
        continuity_key = (
            continuity_composer(event_umo, requester_id)
            if callable(continuity_composer)
            else ""
        )
        generation_session_key = f"tool_photo_{session_key}"
        outer_timeout = self._photo_tool_call_timeout_seconds()
        timeout_margin = max(2.0, min(8.0, outer_timeout * 0.1))
        generation_timeout = outer_timeout - (time.monotonic() - tool_started_at) - timeout_margin
        if generation_timeout <= 0:
            generation_timeout = 0.01
        generation_kwargs = {
            "event": event,
            "workflow_kind": workflow_kind,
            "prompt_text": prompt_text,
            "request_text": content,
            "session_key": generation_session_key,
            "continuity_key": continuity_key,
            "requester_user_id": requester_id,
            "requester_is_private": bool(
                (getattr(event, "is_private_chat", lambda: False)() if callable(getattr(event, "is_private_chat", None)) else getattr(event, "is_private_chat", False))
            ),
            "reference_image_path": reference_path,
            "reference_image_paths": list(resolved_reference_paths),
            "image_size": _single_line(image_size or kwargs.get("size"), 40),
            "requested_scene_preset": preset_text,
            "suggested_scene_preset": preset_text,
            "workflow_default_scene_preset": workflow_default_preset,
            "prompt_sections": prompt_sections,
        }
        if callable(prompt_format_getter):
            generation_kwargs["prompt_format"] = prompt_format_mode
        try:
            generation_output = await asyncio.wait_for(
                structured_generator(**generation_kwargs)
                if callable(structured_generator)
                else legacy_generator(**generation_kwargs),
                timeout=generation_timeout,
            )
        except asyncio.TimeoutError:
            actual_error = (
                f"生图未能在 AstrBot 工具调用时限 {outer_timeout:g} 秒内完成；"
                "本次工具调用没有生成或发送图片。"
            )
            logger.warning(
                "pc_generate_photo 在外层工具超时前主动结束: session=%s timeout=%.1fs budget=%.1fs",
                session_key,
                outer_timeout,
                generation_timeout,
            )
            await self._note_photo_tool_quota_attempt(
                event,
                requester_id=requester_id,
                requester=requester if isinstance(requester, dict) else None,
                photo_scope=photo_scope,
                image_path="",
            )
            return public_receipt(
                {
                    "status": "timeout",
                    "success": False,
                    "generated": False,
                    "send_requested": send_image,
                    "sent": False,
                    "message": actual_error,
                    "actual_error": actual_error,
                    "actionable_hint": "请如实告诉用户本次没有出图、没有发送；不要声称已经发出。可稍后重试，或让管理员提高 AstrBot tool_call_timeout/缩短生图后端超时。",
                    "must_not_claim_sent": True,
                    "retryable": True,
                },
                ensure_ascii=False,
            )
        generation_metadata: dict[str, Any] = {}
        if hasattr(generation_output, "as_legacy_tuple"):
            backend_name, image_path, note = generation_output.as_legacy_tuple()
            generation_metadata = {
                "trace_id": _single_line(getattr(generation_output, "trace_id", ""), 80),
                "reference_used": bool(getattr(generation_output, "reference_used", False)),
                "reference_path": _path_text(getattr(generation_output, "reference_selected_path", ""), 1000),
                "reference_id": _single_line(getattr(generation_output, "reference_id", ""), 60),
                "reference_kind": _single_line(getattr(generation_output, "reference_kind", ""), 40),
                "reference_roles": list(getattr(generation_output, "reference_roles", ()) or ()),
                "wardrobe_mode": _single_line(getattr(generation_output, "wardrobe_mode", ""), 40),
                "wardrobe_category": _single_line(getattr(generation_output, "wardrobe_category", ""), 40),
                "outfit_locked": bool(getattr(generation_output, "outfit_locked", False)),
                "daily_outfit_removed": bool(getattr(generation_output, "daily_outfit_removed", False)),
                "preset_names": list(getattr(generation_output, "preset_names", ()) or ()),
                "preset_hint": _single_line(getattr(generation_output, "preset_hint", ""), 80),
                "preset_source": _single_line(getattr(generation_output, "preset_source", ""), 40),
                "suggestion_status": _single_line(getattr(generation_output, "suggestion_status", ""), 60),
                "prompt_hash": _single_line(getattr(generation_output, "prompt_hash", ""), 80),
                "prompt_path": _single_line(getattr(generation_output, "prompt_path", ""), 1000),
                "reference_requested_roles": list(getattr(generation_output, "reference_requested_roles", ()) or ()),
                "reference_excluded_roles": list(getattr(generation_output, "reference_excluded_roles", ()) or ()),
                "continuity_mode": _single_line(getattr(generation_output, "continuity_mode", ""), 30),
                "reference_confidence": getattr(generation_output, "reference_confidence", 0.0),
                "reference_plan": list(getattr(generation_output, "reference_plan", ()) or ()),
                "reference_fulfilled_roles": list(getattr(generation_output, "reference_fulfilled_roles", ()) or ()),
                "reference_missing_roles": list(getattr(generation_output, "reference_missing_roles", ()) or ()),
                "reference_fallback_message": _single_line(getattr(generation_output, "reference_fallback_message", ""), 260),
                # A provider may have accepted and generated the image while
                # its result URL could not be materialized locally. Keep that
                # state separate from ``generated`` (which means a usable
                # local file) so the reply model receives an accurate receipt.
                "generation_completed": bool(getattr(generation_output, "generation_completed", False)),
                "failure_stage": _single_line(getattr(generation_output, "failure_stage", ""), 40),
            }
        else:
            backend_name, image_path, note = generation_output
            metadata_getter = getattr(self, "_photo_generation_result_metadata", None)
            if callable(metadata_getter):
                generation_metadata = metadata_getter(
                    image_path=image_path,
                    session_key=generation_session_key,
                ) or {}
        generation_completed = bool(generation_metadata.get("generation_completed"))
        failure_stage = _single_line(generation_metadata.get("failure_stage"), 40)
        reference_usage_known = "reference_used" in generation_metadata
        actual_reference_path = _path_text(
            generation_metadata.get("reference_path") or reference_path,
            1000,
        )
        used_reference = bool(generation_metadata.get("reference_used"))
        final_presets = [
            _single_line(value, 60)
            for value in (
                generation_metadata.get("preset_names")
                or generation_metadata.get("presets")
                or []
            )
            if _single_line(value, 60)
        ][:1]
        final_scene_preset = final_presets[0] if final_presets else ""
        ok = bool(image_path and os.path.exists(image_path))
        tool_delivery_confirmed = ";tool_delivery_confirmed" in str(note or "")
        annotator = getattr(self, "_annotate_recent_photo_generation", None)
        if callable(annotator):
            annotator(
                image_path=image_path,
                session_key=generation_session_key,
                trigger="llm_tool",
                intent_kind=intent_kind,
                sent=False,
                caption=visible_caption,
                preset_hint=preset_text,
                tool_name="pc_generate_photo",
            )
        billable_attempt = bool(ok or generation_completed)
        failure_counter = getattr(self, "_photo_generation_failure_counts_as_attempt", None)
        if not billable_attempt and callable(failure_counter):
            billable_attempt = bool(failure_counter(note))
        if billable_attempt:
            await self._note_photo_tool_quota_attempt(
                event,
                requester_id=requester_id,
                requester=requester if isinstance(requester, dict) else None,
                photo_scope=photo_scope,
                image_path=image_path if ok else "",
            )
        sent = False
        delivery_deferred = False
        delivery: dict[str, Any] = {}
        generation_trace_id = _single_line(generation_metadata.get("trace_id"), 80)
        if ok and send_image and tool_delivery_confirmed:
            sent = True
            delivery = {
                "sent": True,
                "destination": "custom_tool",
                "message": "自定义生图工具已完成图片投递",
                "external": True,
            }
        elif ok and send_image:
            # 图片本身就是成功结果。纯状态 caption 不应成为可见回执；
            # 只有包含实际语境信息的自然正文才随图发送。
            usable_caption = "" if self._photo_caption_is_generic(visible_caption) else visible_caption
            message = usable_caption
            fallback_message = _single_line(
                generation_metadata.get("reference_fallback_message"),
                260,
            )
            if fallback_message:
                message = f"{message}\n{fallback_message}".strip()
            trace_writer = getattr(self, "_append_photo_generation_trace_event_async", None)
            if callable(trace_writer):
                await trace_writer(
                    generation_trace_id,
                    "delivery_started",
                    data={"caption": message, "image_path": image_path},
                )
            delivery_deferred = bool(
                getattr(event, "private_companion_proactive_framework", False)
            )
            if delivery_deferred:
                delivery = {
                    "sent": False,
                    "destination": "proactive_framework",
                    "message": "图片已生成，等待主动消息发送链统一投递",
                    "deferred": True,
                }
                try:
                    setattr(event, "_private_companion_photo_tool_deferred", True)
                    setattr(event, "_private_companion_photo_tool_deferred_path", image_path)
                    setattr(event, "_private_companion_photo_tool_deferred_caption", message)
                    setattr(event, "_private_companion_photo_tool_deferred_intent_kind", intent_kind)
                except Exception:
                    pass
                logger.info(
                    "pc_generate_photo 成图已交由主动发送链统一投递: session=%s kind=%s",
                    session_key,
                    intent_kind,
                )
            else:
                try:
                    delivery = await self._deliver_generated_image_to_event(
                        event,
                        image_path=image_path,
                        caption=message,
                    )
                except Exception as exc:
                    delivery = {
                        "sent": False,
                        "destination": "error",
                        "message": f"图片发送失败：{_single_line(exc, 180) or '未知错误'}",
                    }
                    logger.warning(
                        "pc_generate_photo 图片投递异常: session=%s err=%s",
                        session_key,
                        _single_line(exc, 180),
                    )
            sent = bool(delivery.get("sent"))
            if callable(trace_writer):
                trace_writer(
                    generation_trace_id,
                    "delivery_deferred"
                    if delivery_deferred
                    else "delivery_completed"
                    if sent
                    else "delivery_failed",
                    status="ok" if sent or delivery_deferred else "error",
                    data={
                        "sent": sent,
                        "deferred": delivery_deferred,
                        "destination": delivery.get("destination"),
                        "message": delivery.get("message"),
                        "review_label": delivery.get("review_label"),
                    },
                )
            if sent:
                try:
                    setattr(event, "_private_companion_photo_tool_sent", True)
                    setattr(event, "_private_companion_photo_tool_sent_caption", message)
                except Exception:
                    pass
        if callable(annotator):
            annotator(
                image_path=image_path,
                session_key=generation_session_key,
                trigger="llm_tool",
                intent_kind=intent_kind,
                sent=sent,
                caption=visible_caption,
                preset_hint=preset_text,
                tool_name="pc_generate_photo",
            )
        if ok:
            memory_recorder = getattr(self, "_memory_companion_record_photo_generation", None)
            if callable(memory_recorder):
                await memory_recorder(
                    event,
                    prompt=content,
                    kind=workflow_kind,
                    intent_kind=intent_kind,
                    backend=backend_name,
                    image_path=image_path,
                    note=note,
                    sent=sent,
                    trigger="llm_tool",
                    scene_preset=final_scene_preset,
                    reference_image_path=actual_reference_path,
                    reference_used=used_reference if reference_usage_known else None,
                )
        delivery_uncertain = bool(delivery.get("uncertain"))
        overall_success = bool(ok and (not send_image or sent or delivery_deferred))
        public_reference_plan = [
            {
                key: value
                for key, value in binding.items()
                if key in {"reference_id", "roles", "priority", "preserve", "ignore", "submitted"}
            }
            for binding in (generation_metadata.get("reference_plan") or [])[:8]
            if isinstance(binding, dict)
        ]
        result_payload = {
            "status": (
                "success"
                if overall_success
                else "delivery_uncertain"
                if ok and send_image and delivery_uncertain
                else "delivery_failed"
                if ok
                else "result_retrieval_failed"
                if generation_completed and failure_stage == "result_materialization"
                else "error"
            ),
            "success": overall_success,
            "generated": ok,
            "generation_completed": generation_completed,
            "failure_stage": failure_stage,
            "send_requested": send_image,
            "message": (
                _single_line(delivery.get("message"), 220)
                if ok and send_image and delivery
                else ("图片已生成但按请求未发送" if ok and not send_image else (
                    "上游已完成生图，但图片结果没有成功取回，未发送。"
                    if generation_completed and failure_stage == "result_materialization"
                    else (_single_line(note, 220) or "生图失败")
                ))
            ),
            "backend": _single_line(backend_name, 80),
            "kind": workflow_kind,
            "intent_kind": intent_kind,
            "used_reference": used_reference,
            "reference_id": _single_line(generation_metadata.get("reference_id"), 60),
            "reference_kind": _single_line(generation_metadata.get("reference_kind"), 40),
            "reference_roles": list(generation_metadata.get("reference_roles") or [])[:8],
            "reference_intent": {
                "requested_roles": list(generation_metadata.get("reference_requested_roles") or [])[:8],
                "excluded_roles": list(generation_metadata.get("reference_excluded_roles") or [])[:8],
                "continuity_mode": _single_line(generation_metadata.get("continuity_mode"), 30),
                "confidence": generation_metadata.get("reference_confidence", 0.0),
            },
            "reference_plan": public_reference_plan,
            "reference_fulfilled_roles": list(generation_metadata.get("reference_fulfilled_roles") or [])[:8],
            "reference_missing_roles": list(generation_metadata.get("reference_missing_roles") or [])[:8],
            "reference_fallback_message": _single_line(generation_metadata.get("reference_fallback_message"), 260),
            "wardrobe_mode": _single_line(generation_metadata.get("wardrobe_mode"), 40),
            "wardrobe_category": _single_line(generation_metadata.get("wardrobe_category"), 40),
            "outfit_locked": bool(generation_metadata.get("outfit_locked")),
            "daily_outfit_removed": bool(generation_metadata.get("daily_outfit_removed")),
            "preset_hint": preset_text,
            "preset_source": _single_line(generation_metadata.get("preset_source"), 40),
            "suggestion_status": _single_line(generation_metadata.get("suggestion_status"), 60),
            "final_presets": final_presets,
            "prompt_hash": _single_line(generation_metadata.get("prompt_hash"), 80),
            "sent": sent,
            "delivery_deferred": delivery_deferred,
            "delivery_uncertain": delivery_uncertain,
            "delivery": _single_line(delivery.get("destination"), 30),
            "safety_review": _single_line(delivery.get("review_label"), 30),
            "note": _single_line(note, 220),
            "must_not_claim_sent": not sent,
            "same_turn_retry_allowed": False,
            "final_response_instruction": (
                f"图片及可选的自然 caption 已作为本轮唯一可见回复发送。最终回复不要留空，只输出 {PHOTO_TOOL_SILENT_SENTINEL}。"
                if sent
                else f"图片已生成并交给主动发送链；只有非回执的自然 caption 才会随图发送。不要输出状态回执，只输出 {PHOTO_TOOL_SILENT_SENTINEL}。"
                if delivery_deferred
                else ""
            ),
        }
        if ok and send_image and not sent and not delivery_deferred:
            delivery_error = _single_line(delivery.get("message"), 360) or "图片发送失败"
            result_payload.update(
                {
                    "failure_stage": "delivery",
                    "delivery_error": delivery_error,
                    "actual_error": delivery_error,
                    "actionable_hint": (
                        "图片已经提交给平台，但发送回执未确认。不要断言用户已收到，也不要断言发送失败；"
                        "如需回复，只能简短说明回执未确认并请用户查看，绝对不要立即再次发送。"
                        if delivery_uncertain
                        else "图片文件已经生成，但用户没有收到图片。请明确说发送失败，绝对不能说已经发出。"
                    ),
                    "retryable": not delivery_uncertain,
                }
            )
        elif not ok:
            note_text = _single_line(note, 360) or "生图失败"
            lowered_note = note_text.lower()
            upstream_submission_unconfirmed = bool(
                re.search(r"HTTP\s*(?:500|502|503|504)\b", note_text, flags=re.I)
                or "上游生图服务临时失败" in note_text
                or "网关中断" in note_text
                or ("在线图片 API" in note_text and "超时" in note_text)
            )
            hint = "请按 actual_error 里的真实原因回复用户，不要改写成未出现的超时、排队或权限问题。"
            policy_refusal = self._photo_generation_policy_refusal(note_text)
            if policy_refusal:
                public_error = "图片服务拒绝了这次画面描述，本次没有生成或发送图片。"
                logger.warning(
                    "pc_generate_photo 被图片服务策略拒绝: backend=%s error=%s",
                    _single_line(backend_name, 80),
                    note_text,
                )
                result_payload.update(
                    {
                        "message": public_error,
                        "note": public_error,
                        "error_code": "provider_policy_refusal",
                        "failure_reason": public_error,
                        "actual_error": public_error,
                        "actionable_hint": "请用当前人格简短说明这次没有生成出来，并自然询问用户是否换一种画面描述重试；不要复述 Provider 原文、政策名称、敏感词判断或链接。",
                        "do_not_claim_timeout": True,
                        "must_not_claim_sent": True,
                        "retryable": True,
                        "final_response_instruction": "不要复述或翻译 Provider 的英文原文、政策名称、敏感词判断和链接。只用符合当前人格的一句简短中文说明这次没有生成出来，再自然询问是否换一种画面描述重试。",
                    }
                )
            elif "404" in note_text or "not found" in lowered_note or "未找到" in note_text:
                hint = "在线生图接口返回 404，通常是 API 地址端点不对或缺少 /v1；请让用户检查在线图片 API 地址是否支持 /images/generations。"
            elif "图片模型" in note_text or "image model" in lowered_note:
                hint = "当前模型可能不是生图模型；请让用户把在线图片模型改成对应平台的图片模型。"
            elif "api key" in lowered_note or "unauthorized" in lowered_note or "401" in note_text or "403" in note_text:
                hint = "请让用户检查在线图片 API Key、权限和额度。"
            if not policy_refusal:
                result_payload.update(
                    {
                        "failure_reason": note_text,
                        "actual_error": note_text,
                        "actionable_hint": hint,
                        "do_not_claim_timeout": "超时" not in note_text and "timeout" not in lowered_note,
                        "must_not_claim_sent": True,
                    }
                )
            if upstream_submission_unconfirmed:
                result_payload.update(
                    {
                        "status": "submission_unconfirmed",
                        "failure_stage": "upstream_response",
                        "retryable": False,
                        "same_turn_retry_allowed": False,
                        "possible_upstream_execution": True,
                        "actionable_hint": (
                            "网关失败不代表上游任务没有执行，且可能已经计费。"
                            "本轮绝对不要重新调用任何生图工具；请用户先检查服务端任务或账单，稍后再明确决定是否重试。"
                        ),
                        "final_response_instruction": (
                            "简短说明本次没有取回图片，但上游可能仍在执行；不要声称确定失败，"
                            "不要自动重试，也不要建议用户立刻重复提交。"
                        ),
                    }
                )
            if generation_completed and failure_stage == "result_materialization":
                retrieval_message = (
                    "上游已经完成生图，但返回的图片结果未能取回或保存到本地；"
                    "本轮没有发送图片，也不要再次提交同一生图请求。"
                )
                result_payload.update(
                    {
                        "status": "result_retrieval_failed",
                        "message": retrieval_message,
                        "note": retrieval_message,
                        "failure_reason": retrieval_message,
                        "actual_error": note_text,
                        "failure_stage": "result_materialization",
                        "upstream_generated": True,
                        "retryable": False,
                        "same_turn_retry_allowed": False,
                        "actionable_hint": (
                            "如实说明上游已生成但图片结果取回失败，未发送；"
                            "不要说成上游生图请求失败，也不要在本轮再次调用 pc_generate_photo。"
                        ),
                        "final_response_instruction": (
                            "本轮不要再次调用 pc_generate_photo。简短说明图片结果取回失败、没有发送；"
                            "不要声称用户已经收到图片。用户下一轮明确要求时再重试。"
                        ),
                    }
                )
        known_private_paths: list[Any] = [
            image_path,
            actual_reference_path,
            generation_metadata.get("prompt_path"),
            *resolved_reference_paths,
        ]
        for binding in generation_metadata.get("reference_plan") or []:
            if not isinstance(binding, dict):
                continue
            known_private_paths.extend(
                value
                for key, value in binding.items()
                if "path" in str(key or "").lower()
            )
        return public_receipt(
            result_payload,
            ensure_ascii=False,
            known_paths=tuple(known_private_paths),
        )

