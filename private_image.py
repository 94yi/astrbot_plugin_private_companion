# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import base64
import hashlib
import html
import io
import json
import os
import re
import shutil
import tempfile
import time
import urllib.request
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlparse, urlsplit, urlunparse, urlunsplit

from astrbot.api.event import AstrMessageEvent
try:
    from astrbot.api.message_components import Image, Plain
except ImportError:
    from astrbot.api.message_components import Image, Plain
from astrbot.api.provider import ProviderRequest
from astrbot.core.agent.message import AssistantMessageSegment, TextPart, UserMessageSegment
from astrbot.core import file_token_service
from astrbot.core.astr_main_agent import MainAgentBuildConfig, build_main_agent
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .conversation_injection_plan import (
    PLACEMENT_DYNAMIC_SYSTEM,
    get_conversation_injection_plan,
)
from .conversation_prompt_section import (
    PromptRenderMode,
    PromptSection,
    prompt_section,
    exact_text,
    render_prompt_sections,
)
from .helpers import _missing_optional_model_dependency, _safe_float, _safe_int, _single_line, _strip_internal_message_blocks, _strip_outbound_control_blocks, _today_key, _url_host_is_public
from .persona_config import runtime_persona_setting
from .segmented_message import (
    component_kind,
    component_order_from_owner,
    component_strategies_from_owner,
    plan_component_chunks,
    sanitize_llm_segment_control_tokens,
)
from .logging_util import get_module_logger
from .private_image_ingest_cache import PrivateImageIngestCacheMixin
from .private_image_transcribe_group import PrivateImageTranscribeGroupMixin
from .private_image_reply_send import PrivateImageReplySendMixin
from .private_image_persona_visual import PrivateImagePersonaVisualMixin
from .private_image_placeholder_buffer import PrivateImagePlaceholderBufferMixin
from .private_image_review_delivery import PrivateImageReviewDeliveryMixin
from .private_image_provider_governance import PrivateImageProviderGovernanceMixin
from .private_image_model_capability import PrivateImageModelCapabilityMixin

from .private_image_shared import logger, _private_image_host, PREPARED_IMAGE_MAX_AGE_SECONDS, CONTEXT_IMAGE_FAILURE_COOLDOWN_SECONDS


class _PublicOnlyRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Re-check redirects so a public image URL cannot pivot into local networks."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        if not _url_host_is_public(newurl):
            logger.warning(
                "remote image redirect rejected: url=%s",
                _single_line(newurl, 160),
            )
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class PrivateImageMixin(PrivateImageModelCapabilityMixin, PrivateImageProviderGovernanceMixin, PrivateImageReviewDeliveryMixin, PrivateImagePlaceholderBufferMixin, PrivateImagePersonaVisualMixin, PrivateImageReplySendMixin, PrivateImageTranscribeGroupMixin, PrivateImageIngestCacheMixin):
    """Methods split from main.PrivateCompanionPlugin."""

    def _route_private_image_caption_with_keyword_router(
        self, event: AstrMessageEvent, vision_text: str
    ) -> bool:
        """让绕过标准流水线的纯图片 Agent 也能应用关键词模型路由。"""
        caption = _single_line(vision_text, 8000)
        if not caption:
            return False
        context = self._private_image_framework_context()
        getter = getattr(context, "get_registered_star", None)
        if not callable(getter):
            return False
        try:
            metadata = getter("astrbot_plugin_keyword_model_router")
            router = getattr(metadata, "star_cls", None) if metadata is not None else None
            route = getattr(router, "route_companion_image_caption", None)
            if not callable(route):
                return False
            setattr(event, "private_companion_image_caption_route_text", caption)
            return bool(route(event, caption))
        except Exception as exc:
            logger.debug(
                "调用关键词模型路由失败，保留原 Provider: %s",
                _single_line(exc, 120),
            )
            return False

    def _take_buffered_private_image_context_for_event(self, event: AstrMessageEvent) -> dict[str, Any]:
        try:
            sender_id = str(event.get_sender_id())
        except Exception:
            sender_id = ""
        if not sender_id:
            return {}
        resolver = getattr(self, "_private_user_id_for_event", None)
        if callable(resolver):
            try:
                sender_id = _single_line(resolver(event, sender_id), 160) or sender_id
            except Exception:
                pass
        key = self._semantic_buffer_key(f"private:{sender_id}", sender_id)
        now = _private_image_host._now_ts()
        handoffs = self._cleanup_private_image_vision_handoffs(now=now)
        buffers = getattr(self, "_semantic_message_buffers", None)
        buffer = buffers.get(key) if isinstance(buffers, dict) else None
        max_live_age = max(30.0, self._message_debounce_seconds("image") + 30.0)
        live_updated_ts = (
            _safe_float(buffer.get("updated_ts"), buffer.get("first_ts"), 0)
            if isinstance(buffer, dict)
            else 0.0
        )
        if isinstance(buffer, dict) and now - live_updated_ts <= max_live_age:
            handoffs.pop(key, None)
            # 标记图片上下文已被本轮文字请求认领，防抖 finalizer 会跳过二次派发。
            buffer["vision_context_claimed_ts"] = now
            images = buffer.pop("images", [])
            image_limit = self._private_image_vision_text_limit(len(images))
            return {
                "images": [str(item) for item in images[:5] if str(item or "").strip()],
                "image_mode": _single_line(buffer.pop("image_mode", ""), 20),
                "vision_task": buffer.pop("vision_task", None),
                "vision_text": _single_line(buffer.pop("vision_text", ""), image_limit),
                "from_handoff": False,
            }

        handoff = handoffs.get(key)
        if not isinstance(handoff, dict):
            return {}
        stored_session = _single_line(handoff.get("session"), 500)
        current_session = self._private_image_vision_handoff_session(event)
        if stored_session != current_session:
            logger.info(
                "私聊图片视觉交接会话不匹配,保留给原会话: sender=%s stored=%s current=%s",
                sender_id,
                stored_session,
                current_session or "-",
            )
            return {}
        handoffs.pop(key, None)
        images = handoff.get("images") if isinstance(handoff.get("images"), list) else []
        image_limit = self._private_image_vision_text_limit(len(images))
        vision_task = handoff.get("vision_task")
        vision_text = _single_line(handoff.get("vision_text"), image_limit)
        if not vision_text:
            vision_text = _single_line(
                self._completed_private_image_vision_task_text(vision_task),
                image_limit,
            )
        logger.info(
            "私聊补充文字已领取延迟图片视觉交接: sender=%s images=%s has_vision=%s pending=%s",
            sender_id,
            len(images),
            bool(vision_text),
            isinstance(vision_task, asyncio.Task) and not vision_task.done(),
        )
        return {
            "images": [str(item) for item in images[:5] if str(item or "").strip()],
            "image_mode": _single_line(handoff.get("image_mode"), 20),
            "vision_task": vision_task,
            "vision_text": vision_text,
            "from_handoff": True,
        }

    def _private_image_context_user_message(self, *, vision_text: str, image_count: int = 1) -> str:
        count = max(1, int(image_count or 1))
        image_label = "一张图片" if count == 1 else f"{count} 张图片"
        summary = _single_line(vision_text, self._private_image_vision_text_limit(count))
        if summary:
            return f"用户发送了{image_label}。[图片内容：{summary}]"
        return f"用户发送了{image_label}，但当前没有获得可靠视觉摘要。"

    @staticmethod
    def _private_image_history_content_text(value: Any) -> str:
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, list):
            parts: list[str] = []
            for item in value:
                if isinstance(item, dict):
                    item_type = str(item.get("type") or "").lower()
                    if item_type in {"text", "plain"}:
                        parts.append(str(item.get("text") or item.get("content") or ""))
                    elif item_type in {"image", "image_url"}:
                        parts.append("[图片]")
                elif isinstance(item, str):
                    parts.append(item)
                else:
                    item_type = str(getattr(item, "type", "") or "").lower()
                    item_text = getattr(item, "text", None)
                    if item_type in {"text", "plain"} and item_text:
                        parts.append(str(item_text))
                    elif "image" in item_type or "image" in item.__class__.__name__.lower():
                        parts.append("[图片]")
            return " ".join(part for part in parts if part).strip()
        if isinstance(value, dict):
            return PrivateImageMixin._private_image_history_content_text(
                value.get("content") or value.get("text") or value.get("message") or ""
            )
        item_type = str(getattr(value, "type", "") or "").lower()
        item_text = getattr(value, "text", None)
        if item_text:
            return str(item_text).strip()
        if "image" in item_type or "image" in value.__class__.__name__.lower():
            return "[图片]"
        return ""

    @staticmethod
    def _private_image_history_item_role(item: Any) -> str:
        if isinstance(item, dict):
            return str(item.get("role") or item.get("type") or "").strip().lower()
        return str(getattr(item, "role", "") or "").strip().lower()

    @staticmethod
    def _private_image_history_item_content(item: Any) -> Any:
        if isinstance(item, dict):
            return item.get("content")
        return getattr(item, "content", None)

    def _private_image_append_history_marker(self, item: Any, marker: str) -> bool:
        content = self._private_image_history_item_content(item)
        current_text = self._private_image_history_content_text(content)
        if marker in current_text:
            return False
        if isinstance(content, str):
            new_content = f"{content}\n{marker}".strip()
            if isinstance(item, dict):
                item["content"] = new_content
            else:
                item.content = new_content
            return True
        if isinstance(content, list):
            for part in reversed(content):
                if isinstance(part, dict) and str(part.get("type") or "").lower() in {"text", "plain"}:
                    part["text"] = f"{part.get('text') or part.get('content') or ''}\n{marker}".strip()
                    part.pop("content", None)
                    return True
                if str(getattr(part, "type", "") or "").lower() in {"text", "plain"}:
                    part.text = f"{getattr(part, 'text', '') or ''}\n{marker}".strip()
                    return True
            content.append(TextPart(text=marker))
            return True
        new_content = marker
        if isinstance(item, dict):
            item["content"] = new_content
        else:
            item.content = new_content
        return True

    @staticmethod
    def _private_image_history_summary_line(summary: str) -> str:
        cleaned = _single_line(summary, 2400)
        return f"[图片内容：{cleaned}]" if cleaned else ""

    def _private_image_history_user_matches_event(
        self,
        item: Any,
        event: AstrMessageEvent,
    ) -> bool:
        content = self._private_image_history_content_text(
            self._private_image_history_item_content(item)
        )
        if not content:
            return False
        event_text = _single_line(getattr(event, "message_str", ""), 600)
        normalized_content = re.sub(r"\s+", "", content)
        normalized_event = re.sub(r"\s+", "", event_text)
        if normalized_event and normalized_event not in {"[图片]", "图片", "【图片】"}:
            return normalized_event in normalized_content or normalized_content in normalized_event
        return "图片" in normalized_content or "[CQ:image" in normalized_content.lower()

    async def _persist_private_image_vision_summary_to_history(
        self,
        event: AstrMessageEvent,
    ) -> bool:
        """Attach the vision result to the current user history turn.

        The caption provider is an auxiliary call and its result is not part of
        AstrBot's normal request history.  Persisting a bounded, visible user
        text marker makes the next turn able to recover what the image showed,
        while keeping the original image segment and assistant reply intact.
        """
        summary = ""
        for field_name in (
            "private_companion_delayed_image_vision_text",
            "private_companion_reply_image_vision_text",
            "private_companion_image_caption_route_text",
        ):
            summary = _single_line(getattr(event, field_name, ""), 2400)
            if summary:
                break
        marker = self._private_image_history_summary_line(summary)
        if not marker:
            return False
        umo = _single_line(getattr(event, "unified_msg_origin", ""), 200)
        manager = getattr(getattr(self, "context", None), "conversation_manager", None)
        if not umo:
            return False
        requested_cid = _single_line(
            getattr(event, "_private_companion_response_conversation_id", ""),
            160,
        )

        async def write() -> bool:
            # The core serializes this live context after the send hooks.  Try
            # it first so the marker cannot be lost to a stale database copy.
            run_context = getattr(event, "_private_companion_run_context", None)
            run_messages = getattr(run_context, "messages", None)
            if isinstance(run_messages, list):
                for item in reversed(run_messages):
                    if self._private_image_history_item_role(item) != "user":
                        continue
                    if not self._private_image_history_user_matches_event(item, event):
                        continue
                    current_text = self._private_image_history_content_text(
                        self._private_image_history_item_content(item)
                    )
                    if marker in current_text:
                        return False
                    if self._private_image_append_history_marker(item, marker):
                        logger.info("已将图片视觉摘要附加到当前用户消息，交由 AstrBot 核心保存: session=%s", umo)
                        return True

            if manager is None:
                return False
            conversation_id = requested_cid or _single_line(
                await manager.get_curr_conversation_id(umo),
                160,
            )
            if not conversation_id:
                return False
            conversation = await manager.get_conversation(umo, conversation_id)
            if conversation is None:
                return False
            raw_history = getattr(conversation, "history", "[]")
            if isinstance(raw_history, str):
                history = json.loads(raw_history or "[]")
            elif isinstance(raw_history, list):
                history = list(raw_history)
            else:
                history = []

            for item in reversed(history):
                if not isinstance(item, dict) or str(item.get("role") or "") != "user":
                    continue
                if not self._private_image_history_user_matches_event(item, event):
                    continue
                content = item.get("content")
                current_text = self._private_image_history_content_text(content)
                if marker in current_text:
                    return False
                self._private_image_append_history_marker(item, marker)
                await manager.update_conversation(umo, conversation_id, history=history)
                logger.info("已将图片视觉摘要写入当前用户 history: session=%s", umo)
                return True
            logger.debug("未找到可附加图片视觉摘要的当前用户 history: session=%s", umo)
            return False

        db_operation = getattr(self, "_conversation_db_operation", None)
        try:
            result = db_operation("persist_private_image_vision", write) if callable(db_operation) else write()
            if hasattr(result, "__await__"):
                result = await result
            return bool(result)
        except Exception as exc:
            logger.warning("图片视觉摘要写入会话 history 失败: %s", _single_line(exc, 160))
            return False

    def _private_image_context_assistant_message(self, reply: str) -> str:
        if not bool(runtime_persona_setting(self, "enable_framework_error_leak_guard", True)):
            return _single_line(reply, 1200)
        cleaner = getattr(self, "_visible_text_without_tts_reading", None)
        if callable(cleaner):
            try:
                cleaned = str(cleaner(reply, limit=1200) or "").strip()
            except Exception:
                cleaned = ""
        else:
            cleaned = ""
        if not cleaned:
            cleaned = re.sub(r"</?(?:pc[_-]?tts|t{2,}s)\b[^>]*>", "", str(reply or ""), flags=re.IGNORECASE).strip()
        # This text is persisted into AstrBot's user-visible conversation
        # history; remove plugin-only markers before it reaches that store.
        return _single_line(
            sanitize_llm_segment_control_tokens(
                _strip_outbound_control_blocks(
                    cleaned or reply,
                    tts_enabled=bool(runtime_persona_setting(self, "enable_tts_enhancement", False)),
                )
            ),
            1200,
        )

    async def _archive_private_image_turn_to_conversation(
        self,
        event: AstrMessageEvent,
        *,
        user_message: str,
        assistant_message: str,
    ) -> None:
        umo = _single_line(getattr(event, "unified_msg_origin", ""), 200)
        if not umo or not user_message or not assistant_message:
            return
        conv_mgr = getattr(getattr(self, "context", None), "conversation_manager", None)
        if conv_mgr is None:
            return
        ensure_conv = getattr(self, "_ensure_conversation_id_for_umo", None)
        db_operation = getattr(self, "_conversation_db_operation", None)
        for attempt in range(4):
            try:
                user_msg_obj = UserMessageSegment(content=str(user_message or ""))
                assistant_msg_obj = AssistantMessageSegment(content=str(assistant_message or ""))

                async def _write() -> bool:
                    if callable(ensure_conv):
                        conv_id = await ensure_conv(umo, title="Private Companion 图片对话")
                    else:
                        conv_id = await conv_mgr.get_curr_conversation_id(umo)
                        if not conv_id:
                            try:
                                conv_id = await conv_mgr.new_conversation(umo, title="Private Companion 图片对话")
                            except TypeError:
                                conv_id = await conv_mgr.new_conversation(umo)
                    if not conv_id:
                        return False
                    await conv_mgr.add_message_pair(
                        cid=conv_id,
                        user_message=user_msg_obj,
                        assistant_message=assistant_msg_obj,
                    )
                    return True

                written = await db_operation("archive_private_image_turn", _write) if callable(db_operation) else await _write()
                if written:
                    logger.info("已将私聊图片回复写入 AstrBot 会话历史: %s", umo)
                else:
                    logger.warning("私聊图片回复写入会话历史失败: 无法获取或创建 AstrBot 会话 history umo=%s", umo)
                return
            except Exception as exc:
                text = str(exc or "").lower()
                if ("database is locked" in text or "sqlite3.operationalerror" in text) and attempt < 3:
                    await asyncio.sleep(0.25 * (attempt + 1))
                    continue
                logger.warning("私聊图片回复写入会话历史失败: %s", _single_line(exc, 160))
                return

    async def _memory_companion_record_private_image_visible_turn(
        self,
        event: AstrMessageEvent,
        *,
        user_id: str,
        user_message: str,
        assistant_message: str,
        vision_text: str = "",
        image_count: int = 1,
    ) -> None:
        bridge_getter = getattr(self, "_memory_companion_bridge", None)
        try:
            bridge = bridge_getter() if callable(bridge_getter) else None
        except Exception as exc:
            optional_failed = getattr(self, "_memory_companion_optional_dependency_failed", None)
            if callable(optional_failed) and optional_failed(exc, where="private_image_visible_turn_bridge"):
                return
            logger.debug("MemoryCompanion 桥接读取失败，跳过私聊图片可见上下文写入: %s", _single_line(exc, 120))
            return
        recorder = getattr(bridge, "record_visible_turn", None) if bridge is not None else None
        if not callable(recorder) or not user_message or not assistant_message:
            return
        session_id = _single_line(getattr(event, "unified_msg_origin", ""), 200)
        if not session_id:
            return
        platform = session_id.split(":", 1)[0] if ":" in session_id else ""
        user_name = ""
        try:
            user_name = _single_line(self._sender_display_name(event), 80)
        except Exception:
            user_name = _single_line(user_id, 80)
        turn_id = uuid.uuid4().hex
        summary = _single_line(vision_text, self._private_image_vision_text_limit(image_count))
        base_metadata = {
            "source": "private_companion_private_image_turn",
            "image_count": max(1, int(image_count or 1)),
            "summary": summary,
            "conversation_turn": "private_image",
        }
        try:
            await recorder(
                role="user",
                content=user_message,
                scope="private",
                session_id=session_id,
                platform=platform,
                user_id=str(user_id or ""),
                user_name=user_name,
                message_id=f"private_companion_image_turn_{turn_id}_user",
                source="private_companion_private_image_turn",
                metadata={**base_metadata, "turn_role": "user"},
            )
            await recorder(
                role="assistant",
                content=assistant_message,
                scope="private",
                session_id=session_id,
                platform=platform,
                user_id=str(user_id or ""),
                user_name=user_name,
                message_id=f"private_companion_image_turn_{turn_id}_assistant",
                source="private_companion_private_image_turn",
                metadata={**base_metadata, "turn_role": "assistant"},
            )
            logger.info("已将私聊图片回复同步为 MemoryCompanion 可见上下文: session=%s", session_id)
        except Exception as exc:
            optional_failed = getattr(self, "_memory_companion_optional_dependency_failed", None)
            if callable(optional_failed) and optional_failed(exc, where="record_private_image_visible_turn"):
                return
            logger.debug("MemoryCompanion 私聊图片可见上下文写入失败: %s", _single_line(exc, 120))

    async def _archive_private_image_turn_context(
        self,
        event: AstrMessageEvent,
        *,
        user_id: str,
        vision_text: str,
        reply: str,
        image_count: int = 1,
    ) -> None:
        assistant_message = self._private_image_context_assistant_message(reply)
        if not assistant_message:
            return
        user_message = self._private_image_context_user_message(vision_text=vision_text, image_count=image_count)
        await self._archive_private_image_turn_to_conversation(
            event,
            user_message=user_message,
            assistant_message=assistant_message,
        )
        await self._memory_companion_record_private_image_visible_turn(
            event,
            user_id=user_id,
            user_message=user_message,
            assistant_message=assistant_message,
            vision_text=vision_text,
            image_count=image_count,
        )
        livingmemory_recorder = getattr(
            self,
            "_record_final_assistant_in_livingmemory",
            None,
        )
        if callable(livingmemory_recorder):
            message_id_getter = getattr(self, "_event_message_id", None)
            message_id = (
                _single_line(message_id_getter(event), 120)
                if callable(message_id_getter)
                else ""
            )
            await livingmemory_recorder(
                umo=str(getattr(event, "unified_msg_origin", "") or ""),
                assistant_response=assistant_message,
                delivery_id=(
                    f"private_image:{message_id or user_id}:"
                    f"{_private_image_host._now_ts():.6f}"
                ),
            )

    async def _record_private_image_vision_feedback_target(
        self,
        *,
        user_id: str,
        image_sources: list[str],
        vision_text: str,
        reply: str,
        ownership: str = "",
        intent: str = "",
    ) -> None:
        raw_sources = [str(item) for item in image_sources[:5] if str(item or "").strip()]
        image_keys = self._private_image_cache_image_keys(raw_sources)
        if not image_keys:
            return
        image_aliases = self._private_image_cache_aliases_for_sources(raw_sources)
        image_limit = self._private_image_vision_text_limit(len(raw_sources))
        try:
            async with self._data_lock:
                user = self._get_user(user_id)
                user["last_private_image_vision_feedback_target"] = {
                    "ts": _private_image_host._now_ts(),
                    "image_keys": image_keys,
                    "image_aliases": image_aliases,
                    "vision_text": _single_line(vision_text, image_limit),
                    "reply": _single_line(reply, 300),
                    "ownership": _single_line(ownership, 120),
                    "intent": _single_line(intent, 160),
                }
                self._save_data_sync(sections={"users"})
        except Exception as exc:
            logger.debug("私聊图片视觉反馈目标记录失败: %s", exc)

    async def _send_delayed_private_image_only_event(
        self,
        event: AstrMessageEvent,
        user_id: str,
        buffer: dict[str, Any],
    ) -> None:
        feature_checker = getattr(self, "_feature_enabled_or_temp_unlocked", None)
        feature_enabled = (
            feature_checker("enable_private_image_self_recognition")
            if callable(feature_checker)
            else bool(self._private_image_setting("enable_private_image_self_recognition", True))
        )
        if not feature_enabled:
            logger.info(
                "私聊单图处理期间图片转述增强已关闭,但原事件已接管,继续完成本轮回复: user=%s",
                user_id,
            )
        images = buffer.get("images") if isinstance(buffer.get("images"), list) else []
        vision_task = buffer.get("vision_task")
        image_limit = self._private_image_vision_text_limit(len(images))
        vision_text = _single_line(buffer.get("vision_text"), image_limit)
        vision_wait_timed_out = False
        if not vision_text and isinstance(vision_task, asyncio.Task):
            timeout = self._private_image_vision_wait_budget_seconds()
            try:
                if timeout > 0:
                    logger.info("私聊单图等待视觉转述完成: user=%s timeout=%.1fs", user_id, timeout)
                    vision_text = _single_line(await asyncio.wait_for(asyncio.shield(vision_task), timeout=timeout), image_limit)
            except asyncio.TimeoutError:
                vision_wait_timed_out = True
                logger.warning("私聊单图延迟处理时视觉转述仍未完成: user=%s timeout=%.1fs", user_id, timeout)
            except Exception as exc:
                logger.warning("私聊单图延迟视觉转述失败: user=%s error=%s", user_id, _single_line(exc, 120))
        ownership_line = self._private_image_ownership_line(vision_text)
        intent_line = self._private_image_intent_line(vision_text)
        reply_objective = self._private_image_reply_objective(ownership_line, vision_text=vision_text)
        prompt = _single_line(getattr(event, "message_str", ""), 120)
        if not prompt or prompt == "[图片]":
            prompt = (
                "用户刚刚只发了一张图片,没有补充文字。"
                "图片内容已在系统提示的本轮图片视觉摘要中给出；请直接回应那张图,不要说没看到图片。"
                "本轮只回应当前图片和用户发图可能表达的态度/梗/疑问；"
                "但如果最近对话里用户明确规定了这张/下一张图片的回复方式（例如只回复某句话、不要回复其他内容）,必须优先照做。"
                "除此之外,聊天历史只作语气背景,不要续写、答应或安排旧话题。"
                if vision_text
                else (
                    "用户刚刚只发了一张图片,没有补充文字；但当前没有可靠视觉摘要。"
                    "不要描述图片内容、场景、天气、人物、表情或文字，也不要根据聊天历史猜图。"
                    "如果最近对话里用户明确规定了这张/下一张图片的回复方式,必须优先照做；否则只用一句自然短回复说明这边没看清/没识别出来，并请用户补一句想让你看哪里。"
                )
            )
        logger.info(
            "私聊单图准备进入主链: user=%s images=%s has_vision=%s intent=%s ownership=%s objective=%s vision_preview=%s",
            user_id,
            len(images),
            bool(vision_text),
            intent_line or "无",
            ownership_line or "无",
            _single_line(reply_objective, 120),
            _single_line(vision_text, 220),
        )
        raw_image_sources = [str(item) for item in images[:5] if str(item or "").strip()]
        image_items = self._private_image_model_image_items(raw_image_sources)
        model_image_urls = [url for _, url in image_items]
        request_image_refs = self._private_image_sources_for_astrbot_request(raw_image_sources)
        try:
            umo = str(getattr(event, "unified_msg_origin", "") or "")
            framework_context = self._private_image_framework_context()
            framework_event = event
            if umo and framework_context is not None:
                try:
                    from astrbot.core.platform.message_session import MessageSession
                    from .proactive_message import SyntheticPrivateWakeEvent

                    session = MessageSession.from_str(umo)
                    sender_name = ""
                    try:
                        sender_name = _single_line(event.get_sender_name(), 60)
                    except Exception:
                        sender_name = ""
                    framework_event = SyntheticPrivateWakeEvent(
                        context=framework_context,
                        session=session,
                        message="[图片]",
                        sender_name=sender_name or "PrivateCompanion",
                    )
                    try:
                        selected_provider = event.get_extra("selected_provider")
                        if selected_provider:
                            framework_event.set_extra("selected_provider", selected_provider)
                    except Exception:
                        pass
                    logger.info("私聊单图主链使用合成私聊事件执行: user=%s session=%s", user_id, umo)
                except Exception as exc:
                    framework_event = event
                    logger.info("私聊单图合成私聊事件创建失败,回退原事件: user=%s error=%s", user_id, _single_line(exc, 160))
            elif umo:
                logger.warning(
                    "私聊单图主链未取得 AstrBot 原生 Context,已直接转入视觉摘要兜底: user=%s",
                    user_id,
                )
            setattr(framework_event, "private_companion_deferred_private_image_only_ready", True)
            setattr(framework_event, "private_companion_deferred_private_image_only", False)
            setattr(framework_event, "private_companion_skip_external_token_stats", True)
            setattr(framework_event, "private_companion_delayed_image_vision_text", vision_text)
            setattr(framework_event, "private_companion_delayed_image_sources", list(request_image_refs))
            if vision_text:
                self._route_private_image_caption_with_keyword_router(
                    framework_event, vision_text
                )
            buffered_image_mode = _single_line(buffer.get("image_mode"), 20)
            main_provider_supports_image = self._event_main_provider_supports_image(framework_event)
            has_visual_provider = self._has_private_image_visual_provider(umo)
            has_dynamic_gif_sources = (
                bool(self._private_image_setting("enable_private_image_gif_enhancement", True))
                and self._private_image_sources_include_gif(raw_image_sources)
            )
            resolved_image_mode = self._private_image_delivery_mode(
                has_visual_provider=has_visual_provider,
                main_provider_supports_image=main_provider_supports_image,
                has_dynamic_gif=has_dynamic_gif_sources,
            )
            direct_image_mode = bool(
                request_image_refs
                and buffered_image_mode == "direct"
                and resolved_image_mode == "direct"
            )
            direct_provider_id = ""
            direct_provider_source = "current_main_provider"
            if direct_image_mode:
                try:
                    direct_provider_id = _single_line(framework_event.get_extra("selected_provider"), 160)
                except Exception:
                    direct_provider_id = ""
                if not direct_provider_id:
                    direct_provider_id = "current_main_provider"
                setattr(framework_event, "private_companion_delayed_image_mode", "direct")
            elif request_image_refs:
                setattr(framework_event, "private_companion_delayed_image_mode", "caption" if has_visual_provider else "no_vision")
            if not direct_image_mode and has_visual_provider and not vision_text and images:
                completed_vision = self._completed_private_image_vision_task_text(vision_task)
                if completed_vision:
                    vision_text = _single_line(completed_vision, self._private_image_vision_text_limit(len(images)))
                    logger.info(
                        "私聊单图主链前取到后台视觉摘要: user=%s preview=%s",
                        user_id,
                        _single_line(vision_text, 220),
                    )
                elif not vision_wait_timed_out:
                    vision_text = _single_line(await self._transcribe_private_inbound_images(images, umo=umo), self._private_image_vision_text_limit(len(images)))
                else:
                    logger.warning("私聊单图识图等待已超时,主链不再重复发起视觉转述: user=%s", user_id)
                    setattr(framework_event, "private_companion_delayed_image_mode", "no_vision")
                if vision_text:
                    setattr(framework_event, "private_companion_delayed_image_vision_text", vision_text)
                    self._route_private_image_caption_with_keyword_router(
                        framework_event, vision_text
                    )
                    ownership_line = self._private_image_ownership_line(vision_text)
                    intent_line = self._private_image_intent_line(vision_text)
                    reply_objective = self._private_image_reply_objective(ownership_line, vision_text=vision_text)
            if has_dynamic_gif_sources and request_image_refs:
                logger.info(
                    "私聊单图检测到动态 GIF,已改用抽帧视觉摘要链路: user=%s has_vision=%s",
                    user_id,
                    bool(vision_text),
            )
            conv = None
            if umo:
                getter = getattr(self, "_get_current_conversation_safely", None)
                if callable(getter):
                    conv = await getter(umo, label="private_image_framework_read")
                else:
                    conv_id = await self.context.conversation_manager.get_curr_conversation_id(umo)
                    if conv_id:
                        conv = await self.context.conversation_manager.get_conversation(umo, conv_id)
            config_context = framework_context or self.context
            cfg = config_context.get_config(umo=umo) if umo else config_context.get_config()
            provider_settings = cfg.get("provider_settings", {}) if isinstance(cfg, dict) else {}
            build_cfg = MainAgentBuildConfig(
                tool_call_timeout=int(provider_settings.get("tool_call_timeout", 120) or 120),
                llm_safety_mode=False,
                streaming_response=False,
            )
            # The single-image response is a plugin-owned task even though it
            # runs through AstrBot's framework agent. Keep the custom rule in
            # this task request body; never mutate the main conversation system
            # prompt or the stored conversation configuration.
            prompt_applier = getattr(self, "_apply_task_prompt_override_for_call", None)
            if callable(prompt_applier):
                prompt, _unused_system_prompt = prompt_applier(
                    "private_image_only_framework",
                    prompt,
                    None,
                    flatten_system_prompt=True,
                )
            req = ProviderRequest(
                prompt=prompt,
                conversation=conv,
                session_id=getattr(framework_event, "session_id", None) or umo,
            )
            try:
                selected_model = framework_event.get_extra("selected_model")
            except Exception:
                selected_model = None
            if isinstance(selected_model, str) and selected_model.strip():
                # This path passes an explicit request to build_main_agent, so the
                # framework cannot copy selected_model from the event for us.
                req.model = selected_model.strip()
            previous_selected_provider = ""
            selected_provider_changed = False
            if direct_image_mode:
                req.image_urls = list(request_image_refs)
            await self.inject_humanized_state(framework_event, req)
            boundary_intro = (
                "用户当前只发了一张图片,没有文字补充；但当前没有可靠视觉摘要,本轮也没有把图片直接交给主模型。"
                "你不能看见图片内容,不要猜测画面、天气、地点、人物、表情、截图文字或图片类型。"
                "只允许短句请用户补一句想让你看哪里。\n"
                if not vision_text and not direct_image_mode
                else "用户当前只发了一张图片,没有文字补充。你的当前任务是回应这张图片本身和用户借图表达的态度/梗/疑问。\n"
            )
            boundary_prompt = (
                f"{boundary_intro}"
                "用户没有明确问‘图里是什么/写了什么/有几个人’时，不要逐项描述主体、衣服、背景和文字；"
                "把图当作对方递来的一句话，按人格自然评价、接梗、回应情绪或追问一个重点，最多顺带点出一个最显眼细节。\n"
                "如果最近对话上下文里有用户对本轮图片或下一张图片的明确回复限制,例如“只回复某句话”“不要回复其他内容”,必须优先遵守；这不是旧话题。\n"
                "不要把聊天历史、长期记忆、主动消息、旧 TTS 文本或压缩摘要里的邀约当成当前输入；"
                "不要顺便提下午、五点、放学、出去走走、陪你、到时候叫我等旧约定。"
            )
            boundary_section = prompt_section(
                key="private.image_reply_boundary",
                title="本轮图片回复边界",
                source="private_image",
                content=boundary_prompt,
            )
            recent_group_context = self._format_recent_group_messages_for_private_image_prompt_section(
                user_id
            )
            boundary_children: list[PromptSection] = []
            if str(recent_group_context.content or "").strip():
                boundary_children.append(recent_group_context)
            if boundary_children:
                boundary_section = prompt_section(
                    key=boundary_section.key,
                    title=boundary_section.title,
                    source=boundary_section.source,
                    content=boundary_section.content,
                    children=boundary_children,
                )
            self._register_materialized_private_image_context(
                req,
                section=boundary_section,
                marker="",
                priority=31,
            )
            segmenting_injector = getattr(
                self,
                "inject_llm_controlled_segmenting_instruction",
                None,
            )
            if callable(segmenting_injector):
                try:
                    await segmenting_injector(framework_event, req)
                except Exception as exc:
                    logger.debug(
                        "私聊单图分段说明注入失败，继续生成正文: %s",
                        _single_line(exc, 120),
                    )
            request_plan = get_conversation_injection_plan(req, create=False)
            if request_plan is not None:
                request_plan.render_into(req)
            if direct_image_mode:
                existing = getattr(req, "image_urls", None)
                if not isinstance(existing, list):
                    existing = []
                for image_ref in request_image_refs:
                    if image_ref not in existing:
                        existing.append(image_ref)
                req.image_urls = existing
                logger.info(
                    "私聊单图主链已挂载图片: user=%s provider=%s source=%s images=%s has_vision=%s",
                    user_id,
                    direct_provider_id,
                    direct_provider_source,
                    len(existing),
                    bool(vision_text),
                )
            start = time.time()
            captured_tool_sends = []
            llm_resp = None
            try:
                async def _runner_factory():
                    if framework_context is None:
                        return None
                    built = await build_main_agent(
                        event=framework_event,
                        plugin_context=framework_context,
                        config=build_cfg,
                        req=req,
                    )
                    return built

                capture_runner = getattr(self, "_capture_framework_send_message_calls", None)
                framework_lock = getattr(self, "_framework_agent_lock", None)
                if not isinstance(framework_lock, asyncio.Lock):
                    framework_lock = asyncio.Lock()
                    self._framework_agent_lock = framework_lock
                async with framework_lock:
                    if callable(capture_runner) and umo:
                        result, captured_tool_sends = await capture_runner(
                            target_session=umo,
                            runner_factory=_runner_factory,
                        )
                        if captured_tool_sends:
                            logger.info(
                                "私聊单图主链拦截到框架工具直发: user=%s count=%s",
                                user_id,
                                len(captured_tool_sends),
                            )
                    else:
                        result = await _runner_factory()
                        runner_for_step = getattr(result, "agent_runner", None) if result else None
                        if runner_for_step is not None and hasattr(runner_for_step, "step_until_done"):
                            async for _ in runner_for_step.step_until_done(20):
                                pass
            except Exception as exc:
                if direct_image_mode and self._exception_indicates_image_input_unsupported(exc):
                    logger.warning(
                        "私聊单图主链模型不支持图片输入,已降级为视觉摘要兜底: user=%s provider=%s error=%s",
                        user_id,
                        direct_provider_id,
                        _single_line(exc, 180),
                    )
                    direct_image_mode = False
                    reply = ""
                    reply_source = "image_input_unsupported_fallback"
                    result = None
                elif self._exception_indicates_tool_schema_invalid(exc):
                    logger.warning(
                        "私聊单图主链工具 schema 不兼容,已转入兜底回复: user=%s error=%s",
                        user_id,
                        _single_line(exc, 180),
                    )
                    direct_image_mode = False
                    reply = ""
                    reply_source = "tool_schema_invalid_fallback"
                    result = None
                else:
                    logger.warning(
                        "私聊单图主链异常,已转入人格兜底: user=%s error=%s",
                        user_id,
                        _single_line(exc, 180),
                        exc_info=True,
                    )
                    direct_image_mode = False
                    reply = ""
                    reply_source = "main_chain_exception_fallback"
                    result = None
            finally:
                if selected_provider_changed:
                    try:
                        framework_event.set_extra("selected_provider", previous_selected_provider)
                    except Exception:
                        pass
            runner = getattr(result, "agent_runner", None) if result else None
            if llm_resp is None:
                llm_resp = runner.get_final_llm_resp() if runner else None
            if "reply" not in locals():
                reply = self._private_image_framework_response_text(llm_resp)
                if reply and not str(getattr(llm_resp, "completion_text", "") or "").strip():
                    logger.info(
                        "私聊单图主链 completion_text 为空,已从 result_chain 恢复可见文本: user=%s preview=%s",
                        user_id,
                        _single_line(reply, 180),
                    )
            if "reply_source" not in locals():
                reply_source = "main_chain"
            reply = self._restore_private_image_framework_tts_reply(
                reply,
                framework_event,
            )
            if reply and self._private_image_reply_is_internal_error(reply):
                logger.warning(
                    "私聊单图主链返回内部错误文本,已拦截转入兜底: user=%s preview=%s",
                    user_id,
                    _single_line(reply, 180),
                )
                reply = ""
                reply_source = "internal_error_fallback"
            if reply and direct_image_mode and self._private_image_reply_denies_image_capability(reply):
                logger.warning(
                    "私聊单图主链返回无法看图声明,已转视觉摘要兜底: user=%s provider=%s preview=%s",
                    user_id,
                    direct_provider_id,
                    _single_line(reply, 180),
                )
                reply = ""
                direct_image_mode = False
                reply_source = "image_capability_denial_fallback"
            if not reply and captured_tool_sends:
                captured_text_parts: list[str] = []
                sanitizer = getattr(self, "_sanitize_captured_plain_text", None)
                for call in reversed(captured_tool_sends):
                    messages = getattr(call, "messages", [])
                    if not isinstance(messages, list):
                        continue
                    for item in messages:
                        if not isinstance(item, dict):
                            continue
                        if str(item.get("type") or "").strip().lower() != "plain":
                            continue
                        raw_text = item.get("text")
                        text_value = sanitizer(raw_text) if callable(sanitizer) else _single_line(raw_text, 260)
                        if text_value:
                            captured_text_parts.append(text_value)
                    if captured_text_parts:
                        break
                reply = _single_line("\n".join(captured_text_parts), 500)
                if reply:
                    reply_source = "main_chain_tool_capture"
                    logger.info(
                        "私聊单图主链工具直发文本已转为普通回复: user=%s chars=%s reply_preview=%s",
                        user_id,
                        len(reply),
                        _single_line(reply, 180),
                    )
            if reply and vision_text and self._private_image_reply_ignores_vision_summary(reply):
                logger.info(
                    "私聊单图主链疑似忽略视觉摘要,转入兜底回复: user=%s reply_preview=%s",
                    user_id,
                    _single_line(reply, 180),
                )
                reply = ""
            if reply and self._private_image_reply_drifts_to_stale_context(reply):
                trimmed_reply = self._trim_private_image_stale_context_tail(reply)
                if trimmed_reply and trimmed_reply != reply and not self._private_image_reply_drifts_to_stale_context(trimmed_reply):
                    logger.info(
                        "私聊单图主链回复夹带旧上下文,已裁剪: user=%s before=%s after=%s",
                        user_id,
                        _single_line(reply, 180),
                        _single_line(trimmed_reply, 180),
                    )
                    reply = trimmed_reply
                else:
                    logger.info(
                        "私聊单图主链回复夹带旧上下文,转入兜底回复: user=%s reply_preview=%s",
                        user_id,
                        _single_line(reply, 180),
                    )
                    reply = ""
            if reply:
                reply_preview = reply
                preview_cleaner = getattr(self, "_sanitize_orphan_tts_placeholders", None)
                if callable(preview_cleaner):
                    try:
                        reply_preview = preview_cleaner(reply_preview)
                    except Exception:
                        reply_preview = reply
                logger.info(
                    "私聊单图主链回复生成: user=%s chars=%s intent=%s ownership=%s reply_preview=%s",
                    user_id,
                    len(reply),
                    intent_line or "无",
                    ownership_line or "无",
                    _single_line(reply_preview, 180),
                )
            if not reply:
                if not vision_text and images and has_visual_provider:
                    vision_text = self._completed_private_image_vision_task_text(vision_task)
                    if vision_text:
                        logger.info(
                            "私聊单图兜底前取到后台视觉摘要: user=%s preview=%s",
                            user_id,
                            _single_line(vision_text, 220),
                        )
                    elif not vision_wait_timed_out:
                        vision_text = _single_line(await self._transcribe_private_inbound_images(images, umo=umo), self._private_image_vision_text_limit(len(images)))
                    else:
                        logger.info("私聊单图兜底阶段跳过重复视觉转述: user=%s", user_id)
                    setattr(event, "private_companion_delayed_image_vision_text", vision_text)
                    ownership_line = self._private_image_ownership_line(vision_text)
                    intent_line = self._private_image_intent_line(vision_text)
                    reply_objective = self._private_image_reply_objective(ownership_line, vision_text=vision_text)
                fallback_system_prompt = str(getattr(req, "system_prompt", "") or "").strip()
                reply, reply_source = await self._generate_private_image_fallback_reply(
                    vision_text=vision_text,
                    reply_objective=reply_objective,
                    system_prompt=fallback_system_prompt,
                    user_id=user_id,
                )
                if not vision_text:
                    logger.info(
                        "私聊单图无可靠视觉摘要,已尝试人格兜底回复: user=%s chars=%s reply_preview=%s",
                        user_id,
                        len(reply),
                        _single_line(reply, 180),
                    )
                else:
                    logger.info(
                        "私聊单图兜底回复生成: user=%s chars=%s intent=%s ownership=%s objective=%s reply_preview=%s",
                        user_id,
                        len(reply),
                        intent_line or "无",
                        ownership_line or "无",
                        _single_line(reply_objective, 120),
                        _single_line(reply, 180),
                    )
                if not reply:
                    logger.warning(
                        "私聊单图原生链路与兜底 LLM 均未生成有效回复,不启用本地静态兜底: user=%s images=%s has_vision=%s",
                        user_id,
                        len(images),
                        bool(vision_text),
                    )
                    self._record_llm_usage(
                        provider_id="framework",
                        task="private_image_only_framework",
                        prompt=prompt,
                        completion="",
                        elapsed_ms=int((time.time() - start) * 1000),
                        success=False,
                        resp=llm_resp,
                        budget_exempt=True,
                    )
                    return
                logger.info("私聊单图原生链路回复为空,已使用兜底 LLM 回复: user=%s images=%s", user_id, len(images))
            self._record_llm_usage(
                provider_id="framework",
                task="private_image_only_framework",
                prompt=prompt,
                completion=reply,
                elapsed_ms=int((time.time() - start) * 1000),
                success=True,
                resp=llm_resp,
                budget_exempt=True,
            )
            await self._record_private_image_vision_feedback_target(
                user_id=user_id,
                image_sources=raw_image_sources,
                vision_text=vision_text,
                reply=reply,
                ownership=ownership_line,
                intent=intent_line,
            )
            sent_reply = await self._send_private_image_reply_text(event, reply)
            buffer["delayed_reply_sent"] = bool(sent_reply)
            buffer["delayed_reply_sent_ts"] = _private_image_host._now_ts() if sent_reply else 0.0
            if sent_reply:
                await self._archive_private_image_turn_context(
                    event,
                    user_id=user_id,
                    vision_text=vision_text,
                    reply=sent_reply,
                    image_count=len(images),
                )
            if reply_source == "main_chain":
                logger.info("私聊单图无补充说明,已由原生 LLM 链路回复: user=%s images=%s", user_id, len(images))
            else:
                logger.info(
                    "私聊单图无补充说明,原生链路为空,已由兜底回复发送: user=%s images=%s source=%s",
                    user_id,
                    len(images),
                    reply_source,
                )
        except Exception as exc:
            logger.warning("私聊单图延迟回复失败: user=%s error=%s", user_id, _single_line(exc, 180), exc_info=True)

    async def _finalize_private_image_buffer_after_wait(self, key: str, user_id: str, first_ts: float) -> None:
        wait = self._message_debounce_seconds("image")
        remaining = max(0.0, first_ts + wait - _private_image_host._now_ts())
        if remaining > 0:
            await asyncio.sleep(remaining)
        buffers = getattr(self, "_semantic_message_buffers", None)
        buffer = buffers.get(key) if isinstance(buffers, dict) else None
        if not isinstance(buffer, dict):
            return
        messages = buffer.get("messages") if isinstance(buffer.get("messages"), list) else []
        placeholder = "用户刚刚先单独发送了一张图片,可能马上会补充说明。"
        has_followup = any(
            isinstance(item, dict)
            and (cleaned := _single_line(item.get("text"), 260))
            and cleaned != placeholder
            for item in messages
        )
        if has_followup:
            logger.info("私聊单图已由补充消息接管: user=%s", user_id)
            return
        claimed_ts = _safe_float(buffer.get("vision_context_claimed_ts"), 0.0)
        if claimed_ts > 0:
            logger.info(
                "私聊单图上下文已由补充文字请求认领,跳过延迟派发: user=%s claimed_ago=%.1fs",
                user_id,
                max(0.0, _private_image_host._now_ts() - claimed_ts),
            )
            buffers.pop(key, None)
            return
        original_event = buffer.get("original_event")
        delayed_buffer = dict(buffer)
        delayed_buffer["images"] = list(buffer.get("images") or [])
        delayed_buffer["messages"] = list(messages)
        handoff = (
            self._remember_private_image_vision_handoff(key, original_event, delayed_buffer)
            if isinstance(original_event, _private_image_host.AstrMessageEvent)
            else None
        )
        buffers.pop(key, None)
        if isinstance(original_event, _private_image_host.AstrMessageEvent):
            try:
                await self._send_delayed_private_image_only_event(original_event, user_id, delayed_buffer)
            finally:
                if isinstance(handoff, dict):
                    handoff["delayed_dispatch_finished_ts"] = _private_image_host._now_ts()
                    handoff["delayed_reply_sent"] = bool(delayed_buffer.get("delayed_reply_sent"))
                    handoff["delayed_reply_sent_ts"] = _safe_float(
                        delayed_buffer.get("delayed_reply_sent_ts"),
                        0.0,
                    )
                    completed_vision = self._completed_private_image_vision_task_text(handoff.get("vision_task"))
                    if completed_vision:
                        handoff["vision_text"] = _single_line(
                            completed_vision,
                            self._private_image_vision_text_limit(len(handoff.get("images") or [])),
                        )
            return
        vision_task = delayed_buffer.get("vision_task")
        if isinstance(vision_task, asyncio.Task) and not vision_task.done():
            vision_task.cancel()
        logger.info("私聊单图等待补充后无文字指示,但原事件不可用: user=%s", user_id)

