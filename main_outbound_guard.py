# -*- coding: utf-8 -*-
"""出站守卫 / 内容净化域。

由 tools/split_main_domain.py 从 main.py 机械抽取（40 个方法 / 1382 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPlugin）。
"""
from __future__ import annotations

import json
import os
import re
import time
import unicodedata
from .conversation_prompt_section import PromptRenderMode, prompt_section, render_prompt_sections
from .helpers import (
    _now_ts,
    _path_text,
    _safe_float,
    _safe_int,
    _single_line,
    _strip_outbound_control_blocks,
)
from .main_shared import _multi_persona_event_context, _plugin_instance_can_dispatch
from .persona_config import runtime_persona_setting
from .segmented_message import sanitize_llm_segment_control_tokens
from .tool_history_sanitizer import sanitize_history_image_blocks, sanitize_openai_tool_history
from .wake_message_context import restore_wake_message_request
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest
from typing import Any

try:
    from astrbot.api.message_components import Image, Plain
except ImportError:  # pragma: no cover - 兼容旧版 AstrBot
    from astrbot.core.message.components import Image, Plain

from .logging_util import get_module_logger

logger = get_module_logger(__name__)

class PrivateCompanionPluginOutboundGuardMixin:
    """出站守卫 / 内容净化域（从 PrivateCompanionPlugin 拆出）。"""

    @staticmethod
    def _sanitize_persona_id(value: Any) -> str:
        text = unicodedata.normalize("NFC", str(value or ""))
        text = "".join(
            character
            for character in text
            if unicodedata.category(character) not in {"Cc", "Cs"}
        ).strip()
        return text[:96]

    @filter.on_llm_request(priority=230000)
    async def restore_addressed_user_request(
        self, event: AstrMessageEvent, req: ProviderRequest, *args, **kwargs,
    ):
        """Restore addressed chat before state enrichment and MemoryCompanion run."""
        if (
            self is None or not _plugin_instance_can_dispatch(self)
            or not self.enabled or not self._bot_scope_allows_event(event)
        ):
            return
        restore_wake_message_request(self, event, req)

    @filter.on_decorating_result(priority=-18000)
    @_multi_persona_event_context
    async def attach_reaction_expression_image_before_send(
        self, event: AstrMessageEvent, *args, **kwargs
    ):
        """Prepare a local reaction image without weakening the text reply."""
        if self is None or not self.enabled:
            return
        if bool(getattr(event, "_private_companion_skip_reaction_expression", False)):
            for attr in (
                "_private_companion_reaction_expression_intent",
                "_private_companion_deferred_reaction_tts",
            ):
                try:
                    delattr(event, attr)
                except (AttributeError, TypeError):
                    pass
            logger.debug(
                "本轮已有真实生图，跳过追加表情附件: session=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            )
            return
        intent = getattr(
            event, "_private_companion_reaction_expression_intent", None
        )
        if not isinstance(intent, dict) or not intent:
            return
        tracker_installer = getattr(
            self,
            "_install_reaction_expression_delivery_tracker",
            None,
        )
        if callable(tracker_installer):
            # TTS may already be deferred even when lookup later misses. Track the
            # primary reply before any attachment-only early return.
            tracker_installer(event, {})
        if bool(
            getattr(
                event,
                "_private_companion_reaction_expression_attachment_attempted",
                False,
            )
        ):
            return
        setattr(
            event,
            "_private_companion_reaction_expression_attachment_attempted",
            True,
        )

        result = event.get_result()
        chain = list(getattr(result, "chain", []) or []) if result is not None else []
        visible_text = "".join(
            str(getattr(component, "text", "") or "")
            for component in chain
            if isinstance(component, Plain)
        ).strip()
        if not self._reaction_expression_has_visible_text(visible_text):
            self._note_reaction_expression_runtime(
                skipped=1, last_reason="missing_visible_text"
            )
            self._log_reaction_expression_event(
                event,
                stage="attachment",
                decision="skip",
                reason="missing_visible_text",
                scope=self._reaction_expression_scope(event),
                found=False,
                sent=False,
            )
            return
        if any(isinstance(component, Image) for component in chain):
            self._note_reaction_expression_runtime(
                skipped=1, last_reason="existing_image"
            )
            self._log_reaction_expression_event(
                event,
                stage="attachment",
                decision="skip",
                reason="existing_image",
                scope=self._reaction_expression_scope(event),
                found=False,
                sent=False,
            )
            return

        context_text = _single_line(intent.get("context"), 1000) or _single_line(
            visible_text, 700
        )
        raw_prepared = await self._pc_reaction_expression_impl(
            event,
            query=_single_line(intent.get("provider_query"), 500),
            context=context_text,
            meme_only=True,
            send=True,
            purpose=_single_line(intent.get("purpose"), 120),
            emotion=_single_line(intent.get("emotion"), 80),
            intensity=_safe_int(intent.get("intensity"), 0, 0, 5),
            candidate_queries=intent.get("candidate_queries", []),
            attach_only=True,
        )
        try:
            prepared = json.loads(raw_prepared)
        except (TypeError, ValueError, json.JSONDecodeError):
            prepared = {}
        if not isinstance(prepared, dict) or prepared.get("decision") != "attach":
            return

        pending = getattr(
            event,
            "_private_companion_reaction_expression_pending_attachment",
            None,
        )
        image_path = _path_text(prepared.get("path"), 1000)
        if not isinstance(pending, dict) or not image_path or not os.path.isfile(
            image_path
        ):
            if isinstance(pending, dict):
                await self._settle_reaction_expression_attachment_data(
                    pending,
                    sent=False,
                    reason="attachment_file_missing",
                )
            return
        try:
            builder = getattr(self, "_build_reaction_image_component", None)
            if callable(builder):
                image_component = builder(event, image_path)
            else:
                try:
                    image_component = Image.fromFileSystem(image_path)
                except AttributeError:
                    image_component = Image.from_file_system(image_path)
                try:
                    object.__setattr__(
                        image_component,
                        "_private_companion_reaction_expression",
                        True,
                    )
                except Exception:
                    pass
        except Exception as exc:
            await self._settle_reaction_expression_attachment_data(
                pending,
                sent=False,
                reason="attachment_component_failed",
            )
            logger.warning(
                "表情图片附件构建失败: error_type=%s",
                type(exc).__name__,
            )
            return
        delivery_mode = self._reaction_expression_delivery_mode()
        pending["delivery_mode"] = delivery_mode
        pending["component"] = image_component
        pending["delivery_started"] = False
        self._install_reaction_expression_delivery_tracker(event, pending)
        if delivery_mode == "same_message":
            chain.append(image_component)
            try:
                result.chain = chain
            except Exception:
                event.set_result(self._build_result_from_chain(chain))
            pending["attached"] = True
        elif delivery_mode == "separate_before":
            pending["delivery_started"] = True
            sent = await self._send_reaction_expression_component_separately(
                event,
                image_component,
            )
            await self._settle_reaction_expression_attachment_data(
                pending,
                sent=sent,
                reason="delivered" if sent else "delivery_failed",
            )
        self._log_reaction_expression_event(
            event,
            stage="attachment",
            decision="accepted",
            reason=(
                "attachment_appended"
                if delivery_mode == "same_message"
                else "delivered_before_primary"
                if delivery_mode == "separate_before" and pending.get("sent")
                else "delivery_failed"
                if delivery_mode == "separate_before"
                else "attachment_prepared"
            ),
            scope=self._reaction_expression_scope(event),
            found=True,
            sent=bool(pending.get("sent")),
            image_id=prepared.get("image_id"),
            confidence=prepared.get("confidence"),
            cache_hit=prepared.get("cache_hit"),
            latency_ms=prepared.get("lookup_latency_ms"),
            match_basis=pending.get("match_basis"),
        )

    @staticmethod
    def _restore_reaction_expression_delivery_tracker(event: AstrMessageEvent) -> None:
        tracker = getattr(
            event,
            "_private_companion_reaction_expression_delivery_tracker",
            None,
        )
        if not isinstance(tracker, dict) or tracker.get("restored"):
            return
        tracker["restored"] = True
        original_send = tracker.get("original_send")
        if callable(original_send):
            try:
                setattr(event, "send", original_send)
            except Exception:
                pass
        try:
            delattr(
                event,
                "_private_companion_reaction_expression_delivery_tracker",
            )
        except Exception:
            try:
                setattr(
                    event,
                    "_private_companion_reaction_expression_delivery_tracker",
                    None,
                )
            except Exception:
                pass

    @filter.on_decorating_result(priority=-10000)
    @_multi_persona_event_context
    async def suppress_recent_duplicate_outbound_text(self, event: AstrMessageEvent, *args, **kwargs):
        """Last-mile idempotency guard for adapter echoes and concurrent reply chains."""
        if self is None or not self.enabled:
            return
        candidate = self._outbound_text_duplicate_candidate(event)
        if not candidate:
            return
        duplicate_state = self._reserve_outbound_text_candidate(candidate)
        if not duplicate_state:
            setattr(event, "_private_companion_outbound_text_candidate", candidate)
            return
        logger.warning(
            "发送前拦截短时间重复正文: scope=%s sender=%s previous=%s text=%s",
            candidate.get("scope") or "unknown",
            candidate.get("sender_id") or "-",
            duplicate_state,
            _single_line(candidate.get("text"), 120),
        )
        self._suppress_outbound_reply(
            event,
            source="重复正文",
            reason="短时间内重复发送相同正文",
            history_note="[本轮未发送：短时间内重复正文]",
            level="info",
        )

    @filter.on_decorating_result()
    @_multi_persona_event_context
    async def strip_outbound_control_blocks_before_send(self, event: AstrMessageEvent, *args, **kwargs):
        """发送前兜底清理内部控制块，避免 timer/TTSBLOCK 泄漏到聊天。"""
        if self is None or not self.enabled:
            return
        if self._proactive_only_blocks_passive_event(event, "llm_request"):
            return
        if not bool(runtime_persona_setting(self, "enable_framework_error_leak_guard", True)):
            return
        result = event.get_result()
        chain = list(getattr(result, "chain", []) or []) if result is not None else []
        if not chain:
            return
        changed = False
        protected_tts_tokens = getattr(event, "_private_companion_tts_block_tokens", None)
        preserve_private_tts_tokens = (
            bool(runtime_persona_setting(self, 'enable_tts_enhancement', False))
            and isinstance(protected_tts_tokens, dict)
            and bool(protected_tts_tokens)
        )
        for comp in chain:
            if not isinstance(comp, Plain):
                continue
            original = str(getattr(comp, "text", "") or "")
            cleaned = _strip_outbound_control_blocks(
                original,
                tts_enabled=bool(runtime_persona_setting(self, "enable_tts_enhancement", False)),
                preserve_private_tts_tokens=preserve_private_tts_tokens,
                allowed_private_tts_tokens=set(protected_tts_tokens.keys()) if isinstance(protected_tts_tokens, dict) else None,
            )
            if not bool(runtime_persona_setting(self, 'enable_tts_enhancement', False)):
                cleaned = re.sub(r"</?t{2,}s\b[^>]*>", "", cleaned, flags=re.IGNORECASE).strip()
            cleaned = sanitize_llm_segment_control_tokens(cleaned)
            if cleaned != original:
                changed = True
                try:
                    comp.text = cleaned
                except Exception:
                    pass
        if changed:
            logger.warning(
                "发送前已清理内部控制标签: session=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            )

    @filter.on_decorating_result(priority=-29999)
    @_multi_persona_event_context
    async def final_strip_outbound_control_blocks_before_send(
        self, event: AstrMessageEvent, *args, **kwargs
    ):
        """最后一环清理，防止后续 TTS/分段钩子重新带出内部标签。"""
        if self is None or not bool(getattr(self, "enabled", False)):
            return
        if not bool(runtime_persona_setting(self, "enable_framework_error_leak_guard", True)):
            return
        result = event.get_result()
        chain = list(getattr(result, "chain", []) or []) if result is not None else []
        if not chain:
            return
        protected_tts_tokens = getattr(event, "_private_companion_tts_block_tokens", None)
        preserve_private_tts_tokens = (
            bool(runtime_persona_setting(self, 'enable_tts_enhancement', False))
            and isinstance(protected_tts_tokens, dict)
            and bool(protected_tts_tokens)
        )
        cleaned_chain: list[Any] = []
        changed = False
        for component in chain:
            if not isinstance(component, Plain):
                cleaned_chain.append(component)
                continue
            original = str(getattr(component, "text", "") or "")
            cleaned = _strip_outbound_control_blocks(
                original,
                tts_enabled=bool(runtime_persona_setting(self, "enable_tts_enhancement", False)),
                preserve_private_tts_tokens=preserve_private_tts_tokens,
                allowed_private_tts_tokens=(
                    set(protected_tts_tokens.keys())
                    if isinstance(protected_tts_tokens, dict)
                    else None
                ),
            )
            if not bool(runtime_persona_setting(self, 'enable_tts_enhancement', False)):
                cleaned = re.sub(r"</?t{2,}s\b[^>]*>", "", cleaned, flags=re.IGNORECASE).strip()
            cleaned = sanitize_llm_segment_control_tokens(cleaned)
            if cleaned:
                cleaned_chain.append(Plain(cleaned) if cleaned != original else component)
            if cleaned != original:
                changed = True
        if not changed:
            return
        try:
            result.chain = cleaned_chain
        except Exception:
            event.set_result(self._build_result_from_chain(cleaned_chain))
        logger.warning(
            "最终发送前已清理内部控制标签: session=%s",
            _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
        )

    @filter.on_decorating_result()
    @_multi_persona_event_context
    async def strip_plaintext_tool_calls_before_send(self, event: AstrMessageEvent, *args, **kwargs):
        """阻止兼容模型把工具调用 JSON 当普通聊天正文发送。"""
        if self is None or not self.enabled:
            return
        result = event.get_result()
        chain = list(getattr(result, "chain", []) or []) if result is not None else []
        if not chain:
            return
        changed = False
        leaked_names: list[str] = []
        cleaned_chain: list[Any] = []
        for comp in chain:
            if not isinstance(comp, Plain):
                cleaned_chain.append(comp)
                continue
            original = str(getattr(comp, "text", "") or "")
            cleaned, calls = self._strip_plaintext_tool_call_envelopes(original)
            if not calls:
                cleaned_chain.append(comp)
                continue
            changed = True
            leaked_names.extend(str(item.get("name") or "") for item in calls)
            if cleaned:
                try:
                    comp.text = cleaned
                    cleaned_chain.append(comp)
                except Exception:
                    cleaned_chain.append(Plain(cleaned))
        if not changed:
            return
        try:
            result.chain = cleaned_chain
        except Exception:
            event.set_result(self._build_result_from_chain(cleaned_chain))
        logger.warning(
            "发送前终检已移除明文工具调用: session=%s tools=%s",
            _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            ",".join(leaked_names),
        )

    @filter.on_decorating_result()
    @_multi_persona_event_context
    async def cancel_reply_if_trigger_recalled_before_send(self, event: AstrMessageEvent, *args, **kwargs):
        """若触发/唤醒消息在回复发出前被撤回，则静默取消本次回复。"""
        if self is None or not self.enabled:
            return
        if self._proactive_only_blocks_passive_event(event, "enable_recall_enhancement"):
            return
        recalled_message_id = await self._should_cancel_reply_for_missing_or_recalled_trigger(event)
        if not recalled_message_id:
            return
        logger.info(
            "触发消息已撤回或发送前不可见，取消本次发送: session=%s message_id=%s",
            _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            recalled_message_id,
        )
        self._suppress_outbound_reply(
            event,
            source="撤回取消",
            reason="触发消息已撤回或发送前不可见",
            history_note="[本轮未发送：触发消息已撤回]",
            detail=str(recalled_message_id),
            level="info",
        )

    @filter.on_decorating_result()
    @_multi_persona_event_context
    async def suppress_forbidden_outbound_before_send(self, event: AstrMessageEvent, *args, **kwargs):
        """自己的待发送消息命中违禁词时，优先在发送前拦截。"""
        if self is None or not self.enabled:
            return
        if self._proactive_only_blocks_passive_event(event, "enable_recall_enhancement"):
            return
        if not runtime_persona_setting(self, 'enable_recall_enhancement', True) or not runtime_persona_setting(self, 'enable_forbidden_word_recall', False):
            return
        if not self._forbidden_recall_words():
            return
        result = event.get_result()
        chain = list(getattr(result, "chain", []) or []) if result is not None else []
        if not chain:
            return
        text = self._chain_text_for_forbidden_recall(chain)
        hit = self._forbidden_recall_hit(text)
        if not hit:
            return
        logger.warning(
            "待发送消息命中违禁词，已拦截发送: word=%s session=%s",
            _single_line(hit, 40),
            _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
        )
        self._suppress_outbound_reply(
            event,
            source="发送前拦截",
            reason="待发送消息命中屏蔽词",
            history_note="[本轮未发送：待发送消息命中屏蔽词]",
            detail=hit,
            level="warn",
        )

    @filter.on_decorating_result()
    @_multi_persona_event_context
    async def suppress_framework_error_leak_before_send(self, event: AstrMessageEvent, *args, **kwargs):
        """还原本插件复核评语泄漏的正文。"""
        if self is None or not self.enabled:
            return
        result = event.get_result()
        chain = list(getattr(result, "chain", []) or []) if result is not None else []
        if not chain or any(not isinstance(comp, Plain) for comp in chain):
            return
        self._restore_response_review_meta_leak_before_send(event, chain)

    @filter.on_decorating_result()
    @_multi_persona_event_context
    async def rewrite_atrelay_delivery_receipt_before_send(self, event: AstrMessageEvent, *args, **kwargs):
        if self is None or not self.enabled:
            return
        await self._rewrite_atrelay_delivery_receipt_before_send(event)

    def _restore_response_review_meta_leak_before_send(self, event: AstrMessageEvent, chain: list[Any]) -> bool:
        if not chain or any(not isinstance(comp, Plain) for comp in chain):
            return False
        outbound = "\n".join(str(getattr(comp, "text", "") or "") for comp in chain).strip()
        cleaned, reason = self._strip_response_review_meta_leak(outbound)
        if not reason:
            return False
        fallback = str(getattr(event, "_private_companion_response_review_fallback_text", "") or "").strip()
        replacement = cleaned or fallback
        if replacement and self._response_review_meta_leak_reason(replacement):
            replacement = ""
        setattr(event, "_private_companion_response_review_guard_active", False)
        logger.error(
            "发送前拦截到回复复核内部判断: session=%s reason=%s before=%s after=%s",
            _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            reason,
            _single_line(outbound, 180),
            _single_line(replacement, 180),
        )
        if replacement:
            try:
                current_result = event.get_result()
                current_result.chain = [Plain(replacement)]
            except Exception:
                event.set_result(self._build_result_from_chain([Plain(replacement)]))
            self._schedule_reply_interception_forward(
                "rewrite",
                source="回复复核发送前保护",
                reason=f"复核模型返回内部判断，已回退可发送正文：{reason}",
                source_session=_single_line(getattr(event, "unified_msg_origin", ""), 180),
                before=outbound,
                after=replacement,
            )
            return True
        self._suppress_outbound_reply(
            event,
            source="发送前拦截",
            reason=f"回复复核内部判断泄漏：{reason}",
            history_note="[本轮未发送：回复复核输出无法安全改写]",
            level="warn",
        )
        return True

    @filter.on_decorating_result()
    @_multi_persona_event_context
    async def suppress_group_question_wakeup_collision_reply(self, event: AstrMessageEvent, *args, **kwargs):
        """答疑唤醒的群聊回复发送前复核，避免 Bot 碰瓷式插话。"""
        if self is None or not self.enabled:
            return
        if self._proactive_only_blocks_passive_event(event, "enable_group_companion"):
            return
        if not self._feature_enabled_or_temp_unlocked("enable_group_companion"):
            return
        if not self._passive_response_review_enabled():
            return
        if self._effective_passive_review_mode() == "local_only":
            return
        if bool(getattr(event, "_private_companion_group_question_review_done", False)):
            return
        setattr(event, "_private_companion_group_question_review_done", True)
        group_id = self._extract_group_id_from_event(event)
        if not group_id:
            return
        scene = getattr(event, "private_companion_group_scene", None)
        if not isinstance(scene, dict) or str(scene.get("trigger") or "") != "group_wakeup_question":
            return
        result = event.get_result()
        if result is None:
            return
        try:
            if hasattr(result, "is_llm_result") and not result.is_llm_result():
                return
        except Exception:
            pass
        chain = list(getattr(result, "chain", []) or [])
        if not chain:
            return
        reply_text = self._chain_text_for_forbidden_recall(chain, limit=600)
        if not reply_text:
            return
        try:
            review = await self._review_group_question_wakeup_reply_before_send(event, reply_text=reply_text)
        except Exception as exc:
            logger.warning(
                "群聊答疑回复发送前复核失败,默认放行: %s",
                _single_line(exc, 160),
            )
            return
        if str(review.get("decision") or "") != "drop":
            return
        logger.info(
            "已拦截群聊答疑碰瓷回复: group=%s reason=%s text=%s",
            group_id,
            _single_line(review.get("reason"), 120),
            _single_line(reply_text, 160),
        )
        self._suppress_outbound_reply(
            event,
            source="群聊答疑复核",
            reason=_single_line(review.get("reason"), 120) or "群聊答疑碰瓷回复被拦截",
            history_note="[本轮未发送：群聊答疑复核未通过]",
            level="info",
        )

    @filter.on_decorating_result()
    @_multi_persona_event_context
    async def suppress_smart_silence_reply_before_send(self, event: AstrMessageEvent, *args, **kwargs):
        """用户明确想停下当前话题时，用小模型决定是否静默取消待发送回复。"""
        if self is None or not self.enabled:
            return
        if (
            self._passive_response_review_enabled()
            and bool(getattr(event, "_private_companion_response_review_drop", False))
        ):
            logger.info("回复复核去重发送前兜底拦截")
            self._suppress_outbound_reply(
                event,
                source="回复复核去重",
                reason="最终回复与上一条 Bot 消息重复",
                history_note="[本轮未发送：最终回复与上一条消息重复]",
                level="info",
            )
            return
        if bool(getattr(event, "_private_companion_smart_silence_drop", False)):
            logger.info(
                "智能沉默发送前兜底拦截: reason=%s",
                _single_line(getattr(event, "_private_companion_smart_silence_reason", ""), 120),
            )
            self._suppress_outbound_reply(
                event,
                source="智能沉默",
                reason=_single_line(getattr(event, "_private_companion_smart_silence_reason", ""), 120) or "用户边界语义触发静默",
                history_note="[本轮未发送：智能沉默判定]",
                level="info",
            )
            return
        if not bool(runtime_persona_setting(self, 'enable_smart_silence', True)):
            return
        try:
            if bool(getattr(event, "is_private_chat", lambda: False)()):
                return
        except Exception:
            pass
        result = event.get_result()
        if result is None:
            return
        try:
            if hasattr(result, "is_llm_result") and not result.is_llm_result():
                return
        except Exception:
            pass
        chain = list(getattr(result, "chain", []) or [])
        if not chain or any(not isinstance(comp, Plain) for comp in chain):
            return
        reply_text = self._chain_text_for_forbidden_recall(chain, limit=600)
        if not reply_text:
            return
        inbound_text = _single_line(
            getattr(event, "private_companion_group_text", "") or getattr(event, "message_str", ""),
            260,
        )
        trigger_checker = getattr(self, "_smart_silence_contextual_trigger_reason", None)
        trigger_reason = (
            trigger_checker(inbound_text, reply_text, session_kind="group")
            if callable(trigger_checker)
            else self._smart_silence_trigger_reason(inbound_text)
        )
        if not trigger_reason:
            return
        recent_context: list[str] = []
        group_id = self._extract_group_id_from_event(event)
        if group_id:
            group = self._get_group(group_id)
            sender_id = ""
            try:
                sender_id = str(event.get_sender_id())
            except Exception:
                sender_id = ""
            flow_formatter = getattr(self, "_format_group_recent_flow_for_review", None)
            recent_flow = (
                flow_formatter(group, sender_id=sender_id, text=inbound_text, max_lines=8, max_chars=1000)
                if callable(flow_formatter)
                else ""
            )
            for line in recent_flow.splitlines():
                line = _single_line(line, 140)
                if line.startswith("- "):
                    line = line[2:].strip()
                if line:
                    recent_context.append(line)
        try:
            decision = await self._decide_smart_silence(
                inbound_text=inbound_text,
                response_text=reply_text,
                user=None,
                session_kind="group" if group_id else "chat",
                recent_context=recent_context,
            )
        except Exception as exc:
            logger.warning("智能沉默发送前判定失败,默认放行: %s", _single_line(exc, 120))
            return
        if str(decision.get("decision") or "") != "silent":
            return
        logger.info(
            "智能沉默已取消本轮群聊回复: group=%s reason=%s inbound=%s reply=%s",
            group_id or "-",
            _single_line(decision.get("reason"), 120),
            _single_line(inbound_text, 120),
            _single_line(reply_text, 140),
        )
        self._suppress_outbound_reply(
            event,
            source="智能沉默",
            reason=_single_line(decision.get("reason"), 120) or "群聊边界语义触发静默",
            history_note="[本轮未发送：群聊智能沉默判定]",
            level="info",
        )

    @filter.on_decorating_result()
    @_multi_persona_event_context
    async def record_empty_passive_result_before_send(self, event: AstrMessageEvent, *args, **kwargs):
        """发送前兜底记录空结果，避免被动不回复却没有排障原因。"""
        if self is None or not self.enabled:
            return
        if bool(getattr(event, "_private_companion_passive_no_reply_recorded", False)):
            return
        if bool(getattr(event, "private_companion_proactive_framework", False)):
            return
        result = event.get_result()
        if result is None:
            return
        chain = list(getattr(result, "chain", []) or [])
        if chain:
            return
        try:
            is_private = bool(getattr(event, "is_private_chat", lambda: False)())
        except Exception:
            is_private = False
        is_group = bool(self._extract_group_id_from_event(event))
        if not is_private and not is_group:
            return
        self._record_passive_no_reply(
            event,
            source="发送前检查",
            reason="发送前结果为空",
            level="info",
        )

    @filter.on_decorating_result(priority=-19000)
    @_multi_persona_event_context
    async def suppress_empty_photo_tool_followup_before_send(self, event: AstrMessageEvent, *args, **kwargs):
        """Stop any adapter-visible followup after a tool already sent the photo."""
        if self is None or not self.enabled:
            return
        if not bool(getattr(event, "_private_companion_photo_tool_sent", False)):
            return
        result = event.get_result()
        if result is None:
            return
        chain = list(getattr(result, "chain", []) or [])
        had_visible_content = self._photo_tool_followup_chain_has_visible_content(chain)
        self._suppress_outbound_reply(
            event,
            source="图片工具尾随",
            reason="图片工具已发送成功，取消尾随文本",
            history_note="[本轮未发送：图片工具已发送成功]",
            level="info",
        )
        logger.info(
            "已阻止图片工具成功发送后的尾随消息: session=%s components=%s visible=%s",
            _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            len(chain),
            had_visible_content,
        )

    @filter.on_decorating_result(priority=300)
    @_multi_persona_event_context
    async def apply_tts_enhancement_before_send_hook(self, event: AstrMessageEvent, *args, **kwargs):
        """发送前处理 TTS强化标签和自动语音转换。"""
        if self is None or not self.enabled:
            return
        if self._proactive_only_blocks_passive_event(event, "enable_tts_enhancement"):
            return
        await self.apply_tts_enhancement_before_send(event)

    @filter.on_decorating_result()
    @_multi_persona_event_context
    async def strip_group_internal_identity_anchors(self, event: AstrMessageEvent, *args, **kwargs):
        """发送前清理群聊内部身份锚点，避免调试标记泄露到回复。"""
        if self is None or not self.enabled:
            return
        if self._proactive_only_blocks_passive_event(event, "enable_group_companion"):
            return
        if not self._feature_enabled_or_temp_unlocked("enable_group_companion"):
            return
        if not self._extract_group_id_from_event(event):
            return
        result = event.get_result()
        chain = list(getattr(result, "chain", []) or []) if result is not None else []
        if not chain:
            return
        for comp in chain:
            if not isinstance(comp, Plain):
                continue
            original = str(getattr(comp, "text", "") or "")
            cleaned = self._strip_internal_identity_anchors(original)
            if cleaned != original:
                try:
                    comp.text = cleaned
                except Exception:
                    pass

    @filter.on_decorating_result()
    @_multi_persona_event_context
    async def suppress_group_silent_control_reply(self, event: AstrMessageEvent, *args, **kwargs):
        """模型输出“不回复”控制语时静默吞掉，避免把内部判断发到群里。"""
        if self is None or not self.enabled:
            return
        if self._proactive_only_blocks_passive_event(event, "enable_group_companion"):
            return
        if not self._feature_enabled_or_temp_unlocked("enable_group_companion"):
            return
        if not self._extract_group_id_from_event(event):
            return
        result = event.get_result()
        chain = list(getattr(result, "chain", []) or []) if result is not None else []
        if not chain or any(not isinstance(comp, Plain) for comp in chain):
            return
        text = "".join(str(getattr(comp, "text", "") or "") for comp in chain).strip()
        if not self._is_silent_control_reply_text(text):
            return
        logger.info("已静默吞掉群聊不回复控制语: %s", _single_line(text, 120))
        self._suppress_outbound_reply(
            event,
            source="群聊静默",
            reason="模型输出不回复控制语",
            history_note="[本轮未发送：模型输出不回复控制语]",
            level="info",
        )

    async def _review_group_question_wakeup_reply_before_send(
        self,
        event: AstrMessageEvent,
        *,
        reply_text: str,
    ) -> dict[str, str]:
        provider_id = self._task_provider(
            runtime_persona_setting(self, "response_review_provider_id", ""),
            runtime_persona_setting(self, "group_followup_judge_provider_id", ""),
            runtime_persona_setting(self, "mai_style_provider_id", ""),
        )
        if not provider_id:
            return {"decision": "send", "reason": "未配置复核模型"}
        scene = getattr(event, "private_companion_group_scene", None)
        if not isinstance(scene, dict):
            scene = {}
        group_id = self._extract_group_id_from_event(event)
        group = self._get_group(group_id) if group_id else {}
        inbound_text = _single_line(
            getattr(event, "private_companion_group_text", "") or getattr(event, "message_str", "") or "",
            220,
        )
        sender_id = ""
        try:
            sender_id = str(event.get_sender_id())
        except Exception:
            sender_id = ""
        flow_formatter = getattr(self, "_format_group_recent_flow_for_review", None)
        recent_flow = (
            flow_formatter(group, sender_id=sender_id, text=inbound_text, max_lines=12, max_chars=1400)
            if callable(flow_formatter)
            else ""
        )
        wakeup = group.get("last_group_wakeup") if isinstance(group.get("last_group_wakeup"), dict) else {}
        intro_section = prompt_section(
            key="background.group_question_wakeup_review.intro",
            title="群唤醒回复发送前复核",
            source="main",
            content=(
                "判断这条群聊回复是否应该在发送前拦截。\n\n"
                "只输出 JSON 对象，不要解释。\n\n"
                "可选 decision：\n"
                "- send：确实是在自然回答群里的公共求助/开放问题，可以发送。\n"
                "- drop：像 Bot 碰瓷插话，或问题明显是在接群友的话、问别人、吐槽/反问，不该发送。\n\n"
                "判断标准：\n"
                "- 没有明确 @ Bot 或引用 Bot 时，要更保守。\n"
                "- 如果触发句只是“为什么/啥情况/怎么回事/不会吧？”这类接话、吐槽、反问，通常 drop。\n"
                "- 如果是“有没有人懂/谁会/求问/报错/怎么解决/帮忙”这类公共求助，通常 send。\n"
                "- 如果待发送内容虽然正确，但当前群聊并不需要 Bot 插入，也应 drop。"
            ),
        )
        context_sections = [
            prompt_section(
                key="background.group_question_wakeup_review.wakeup",
                title="本轮群唤醒",
                source="main",
                content=(
                    f"trigger={_single_line(scene.get('trigger'), 40)} reason={_single_line(scene.get('reason'), 60)}\n"
                    f"wakeup_type={_single_line(wakeup.get('type'), 40)} score={_single_line(wakeup.get('score'), 20)}/{_single_line(wakeup.get('threshold'), 20)} detail={_single_line(wakeup.get('reason_detail'), 160)}"
                ),
            ),
            prompt_section(
                key="background.group_question_wakeup_review.history",
                title="真实最近群聊",
                source="main",
                content=recent_flow or "（无）",
            ),
            prompt_section(
                key="background.group_question_wakeup_review.trigger",
                title="触发消息",
                source="main",
                content=inbound_text,
            ),
            prompt_section(
                key="background.group_question_wakeup_review.reply",
                title="待发送回复",
                source="main",
                content=_single_line(reply_text, 360),
            ),
        ]
        output_section = prompt_section(
            key="background.group_question_wakeup_review.output",
            title="输出契约",
            source="main",
            content='请输出：\n{"decision":"send|drop","reason":"一句很短的原因"}',
        )
        prompt = "\n\n".join(
            (
                render_prompt_sections([intro_section], mode=PromptRenderMode.BODY_ONLY),
                render_prompt_sections(context_sections, mode=PromptRenderMode.LABELED_BLOCK),
                render_prompt_sections([output_section], mode=PromptRenderMode.BODY_ONLY),
            )
        )
        started = time.perf_counter()
        raw = await self._llm_call(
            prompt,
            max_tokens=120,
            provider_id=provider_id,
            task="group_question_wakeup_reply_review",
        )
        payload = self._parse_json_object(raw)
        decision = str((payload or {}).get("decision") or "").strip().lower()
        reason = _single_line((payload or {}).get("reason"), 120)
        if decision not in {"send", "drop"}:
            decision = "send"
            reason = reason or "复核输出不可解析，默认放行"
        logger.info(
            "群聊答疑回复发送前复核: decision=%s elapsed=%dms reason=%s trigger=%s text=%s",
            decision,
            int((time.perf_counter() - started) * 1000),
            reason,
            _single_line(scene.get("trigger"), 40),
            _single_line(reply_text, 140),
        )
        if recent_flow:
            logger.info(
                "群聊答疑复核已附带真实群聊上下文: group=%s lines=%s chars=%s",
                group_id or "-",
                len([line for line in recent_flow.splitlines() if line.strip()]),
                len(recent_flow),
            )
        return {"decision": decision, "reason": reason}

    @filter.on_decorating_result(priority=-1000)
    @_multi_persona_event_context
    async def strip_unexpected_private_passive_reply(self, event: AstrMessageEvent, *args, **kwargs):
        """私聊被动主链不沿用框架误带的引用，避免 QQ 显示跨会话引用。"""
        if self is None or not self.enabled:
            return
        if bool(getattr(event, "private_companion_proactive_framework", False)):
            return
        try:
            if not bool(event.is_private_chat()):
                return
        except Exception:
            return
        try:
            result = event.get_result()
        except Exception:
            return
        if result is None:
            return
        try:
            is_llm_result = bool(result.is_llm_result())
        except Exception:
            return
        if not is_llm_result:
            return
        chain = list(getattr(result, "chain", []) or [])
        if not chain:
            return
        current_message_ids = set(self._event_message_id_candidates(event))
        cleaned_chain: list[Any] = []
        removed_reply_ids: list[str] = []
        for component in chain:
            if not self._is_reply_component(component):
                cleaned_chain.append(component)
                continue
            reply_id = _single_line(self._extract_reply_message_id(component), 120)
            if reply_id and reply_id in current_message_ids:
                cleaned_chain.append(component)
                continue
            removed_reply_ids.append(reply_id or "unknown")
        if len(cleaned_chain) == len(chain):
            return
        try:
            result.chain = cleaned_chain
        except Exception:
            event.set_result(self._build_result_from_chain(cleaned_chain))
        logger.info(
            "已移除私聊被动主链中的跨目标引用组件: session=%s current=%s removed=%s targets=%s",
            _single_line(getattr(event, "unified_msg_origin", ""), 120) or "-",
            ",".join(sorted(current_message_ids)) or "-",
            len(chain) - len(cleaned_chain),
            ",".join(removed_reply_ids) or "-",
        )

    @filter.on_decorating_result()
    @_multi_persona_event_context
    async def remember_group_bot_reply_context_before_send(self, event: AstrMessageEvent, *args, **kwargs):
        """记录群聊 Bot 实际候选回复，供下一轮连续对话判断使用。"""
        # Group continuity is committed only by the confirmed-delivery
        # finalizer. This decorating hook must remain a pure read/transform
        # stage because the adapter can reject the outgoing result afterwards.
        return

    @filter.on_decorating_result(priority=-9000)
    @_multi_persona_event_context
    async def final_tts_markup_guard_before_send(self, event: AstrMessageEvent, *args, **kwargs):
        """发送前终检 TTS 标签，避免 <tts> 原样泄漏到聊天。"""
        if self is None or not self.enabled:
            return
        if self._proactive_only_blocks_passive_event(event, "enable_tts_enhancement"):
            return
        guard = getattr(self, "finalize_outbound_tts_markup_guard", None)
        if callable(guard):
            await guard(event)

    def _sanitize_segmented_plain_text(self, event: AstrMessageEvent, text: Any) -> str:
        if not bool(runtime_persona_setting(self, "enable_framework_error_leak_guard", True)):
            return str(text or "")
        protected_tts_tokens = getattr(event, "_private_companion_tts_block_tokens", None)
        preserve_private_tts_tokens = (
            bool(runtime_persona_setting(self, 'enable_tts_enhancement', False))
            and isinstance(protected_tts_tokens, dict)
            and bool(protected_tts_tokens)
        )
        cleaned = _strip_outbound_control_blocks(
            text,
            tts_enabled=bool(runtime_persona_setting(self, "enable_tts_enhancement", False)),
            preserve_private_tts_tokens=preserve_private_tts_tokens,
            allowed_private_tts_tokens=set(protected_tts_tokens.keys())
            if isinstance(protected_tts_tokens, dict) else None,
        )
        if not bool(runtime_persona_setting(self, 'enable_tts_enhancement', False)):
            cleaned = re.sub(r"</?t{2,}s\b[^>]*>", "", cleaned, flags=re.IGNORECASE).strip()
        cleaned = sanitize_llm_segment_control_tokens(cleaned)
        return cleaned

    def _sanitize_request_context_new_conversation_boundary(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
        contexts = getattr(req, "contexts", None)
        if not isinstance(contexts, list) or not contexts:
            return
        boundary_index = -1
        for index, item in enumerate(contexts):
            text = self._plain_context_content_for_fast_reply(item.get("content") if isinstance(item, dict) else item)
            if self._context_text_is_new_conversation_boundary(text):
                boundary_index = index
        if boundary_index < 0:
            return
        trimmed: list[Any] = []
        for item in contexts[boundary_index + 1:]:
            text = self._plain_context_content_for_fast_reply(item.get("content") if isinstance(item, dict) else item)
            if self._context_text_is_new_conversation_boundary(text):
                continue
            trimmed.append(item)
        if len(trimmed) == len(contexts):
            return
        try:
            req.contexts = trimmed
        except Exception:
            return
        logger.info(
            "已按新会话边界裁剪 AstrBot 上下文: session=%s contexts=%s->%s boundary_index=%s",
            _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            len(contexts),
            len(trimmed),
            boundary_index,
        )

    def _stop_reply_for_rest_gate(self, event: AstrMessageEvent, reason: str) -> None:
        self._record_rest_reply_backlog(event, reason)
        logger.info(
            "睡眠/休息回复闸门拦截本轮被动回复: session=%s reason=%s",
            _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            _single_line(reason, 120),
        )
        self._record_passive_no_reply(
            event,
            source="休息闸门",
            reason=reason or "睡眠/休息回复闸门拦截",
            level="info",
        )
        empty_result = self._build_result_from_chain([])
        try:
            empty_result.stop_event()
        except Exception:
            pass
        event.set_result(empty_result)
        event.stop_event()

    def _stop_private_reply_after_user_rest_signal(self, event: AstrMessageEvent, user_id: str, text: str) -> None:
        logger.info(
            "用户明确勿扰/不用回复,已前置拦截本轮私聊回复: user=%s text=%s",
            _single_line(user_id, 80),
            _single_line(text, 120),
        )
        self._record_passive_no_reply(
            event,
            source="休息静默",
            reason="用户明确要求勿扰或不用回复",
            detail=text,
            level="info",
        )
        empty_result = self._build_result_from_chain([])
        try:
            empty_result.stop_event()
        except Exception:
            pass
        event.set_result(empty_result)
        event.stop_event()

    def _stop_group_llm_reply_if_blocked(self, event: AstrMessageEvent, *, source: str) -> bool:
        if self._is_private_companion_command_event(event):
            return False
        item = self._group_llm_reply_block_for_event(event)
        if not item:
            return False
        if bool(getattr(event, "_private_companion_group_llm_reply_blocked", False)):
            return True
        group_id = _single_line(item.get("group_id"), 80) or self._extract_group_id_from_event(event)
        logger.info(
            "本群 LLM 回复已被单独关闭,拦截本轮回复: group=%s source=%s",
            group_id or "-",
            _single_line(source, 40),
        )
        setattr(event, "_private_companion_group_llm_reply_blocked", True)
        self._record_passive_no_reply(
            event,
            source="群聊 LLM 熔断",
            reason="本群所有 LLM 回复已关闭",
            detail=f"group={group_id or '-'} source={_single_line(source, 40)}",
            level="warn",
        )
        event.set_result(self._build_result_from_chain([]))
        event.stop_event()
        return True

    @filter.on_decorating_result()
    @_multi_persona_event_context
    async def redact_outbound_secrets_before_send(self, event: AstrMessageEvent, *args, **kwargs):
        """Final passive-reply guard against API keys, tokens and passwords."""
        if self is None or not self.enabled or not bool(getattr(self, "enable_outbound_secret_redaction", True)):
            return
        result = event.get_result()
        chain = list(getattr(result, "chain", []) or []) if result is not None else []
        if not chain:
            return
        _, changed = self._redact_outbound_chain_secrets(chain)
        if changed:
            logger.error(
                "发送前检测到敏感凭据并已脱敏: session=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            )

    @filter.on_decorating_result(priority=-21000)
    @_multi_persona_event_context
    async def record_daily_review_outbound_case_before_send(self, event: AstrMessageEvent, *args, **kwargs):
        """Experimental final-stage sampling for the next daily case review."""
        if self is None or not self.enabled or not runtime_persona_setting(self, 'enable_daily_case_review_experiment', False):
            return
        result = event.get_result()
        chain = list(getattr(result, "chain", []) or []) if result is not None else []
        if chain:
            self._record_daily_review_outbound_case(event, chain)

    @filter.on_llm_request(priority=-22000)
    @_multi_persona_event_context
    async def prepare_p5_memory_attestation(self, event: AstrMessageEvent, req: ProviderRequest, *args, **kwargs):
        """Expose a per-request attestation issuer before MemoryCompanion runs."""
        if self is None or event is None or not bool(getattr(self, "enable_p5_source_observer", False)):
            return
        request_carrier = req if req is not None else event
        p3_state = getattr(event, "private_companion_p5_p3_state", None)
        if p3_state is None:
            p3_state = object()
            try:
                setattr(event, "private_companion_p5_p3_state", p3_state)
            except Exception:
                pass
        try:
            setattr(event, "private_companion_p5_request_carrier", request_carrier)
            setattr(
                event,
                "private_companion_p5_issue_attestation",
                lambda sink, _event=event, _request=request_carrier: self._p5_issue_attestation_for_event(
                    event=_event,
                    request=_request,
                    sink=str(sink or "memory_recall"),
                ),
            )
            setattr(event, "private_companion_p5_status", self.p5_source_observer_status())
        except Exception:
            logger.debug("P5 request carrier attach failed")

    @filter.on_llm_request(priority=-21000)
    @_multi_persona_event_context
    async def sanitize_sensitive_screen_tools(self, event: AstrMessageEvent, req: ProviderRequest, *args, **kwargs):
        """屏幕工具只能保留给已启用的主要用户私聊，群聊和第三方场景一律裁掉。"""
        if self is None or req is None:
            return
        removed = self._remove_sensitive_screen_tools_from_request(event, req)
        if removed:
            await self._append_sensitive_screen_tool_guard_to_request(event, req, removed)

    @filter.on_llm_request(priority=-20500)
    @_multi_persona_event_context
    async def sanitize_deepseek_tool_call_history(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        *args,
        **kwargs,
    ):
        """Drop only malformed tool-call groups before a DeepSeek provider request."""
        if self is None or req is None:
            return
        if not self._llm_request_uses_deepseek_openai_compatible_provider(event, req):
            return
        contexts = getattr(req, "contexts", None)
        cleaned, stats = sanitize_openai_tool_history(contexts)
        if not stats.get("changed"):
            return
        try:
            req.contexts = cleaned
        except Exception:
            return
        logger.info(
            "Cleaned malformed DeepSeek tool history: groups=%s assistants=%s tool_results=%s orphans=%s",
            stats.get("removed_groups", 0),
            stats.get("removed_assistants", 0),
            stats.get("removed_tool_results", 0),
            stats.get("removed_orphans", 0),
        )

    @filter.on_llm_request(priority=-20000)
    @_multi_persona_event_context
    async def sanitize_incompatible_web_search_tools(self, event: AstrMessageEvent, req: ProviderRequest, *args, **kwargs):
        """移除 Gemini/OpenAI 兼容层会拒绝的 Baidu AI Search MCP 工具声明。"""
        if self is None or req is None:
            return
        tool_set = getattr(req, "func_tool", None)
        if tool_set is None:
            return
        if not self._tool_set_has_named_tool(tool_set, "AIsearch"):
            return
        if not self._llm_request_uses_gemini_family_provider(event, req):
            return
        remove_tool = getattr(tool_set, "remove_tool", None)
        if not callable(remove_tool):
            return
        try:
            remove_tool("AIsearch")
        except Exception as exc:
            logger.debug("移除不兼容 AIsearch 工具失败: %s", _single_line(exc, 160))
            return
        settings = self._llm_request_provider_settings_for_event(event)
        provider_label = " / ".join(self._llm_request_provider_identity_parts(event, req)[:3]) or "unknown"
        umo = _single_line(getattr(event, "unified_msg_origin", ""), 120)
        log_key = f"{umo}:{provider_label}:AIsearch"
        logged = getattr(self, "_incompatible_web_search_tool_logged_keys", None)
        if not isinstance(logged, set):
            logged = set()
            setattr(self, "_incompatible_web_search_tool_logged_keys", logged)
        if log_key not in logged:
            logged.add(log_key)
            logger.warning(
                "已移除本轮 Gemini 不兼容的 AIsearch 搜索工具，避免请求 400: provider=%s websearch_provider=%s session=%s",
                _single_line(provider_label, 200),
                _single_line(settings.get("websearch_provider"), 80) or "unknown",
                umo or "unknown",
            )

    @filter.on_llm_request(priority=-249000)
    @_multi_persona_event_context
    async def sanitize_historical_image_blocks_before_provider(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        *args,
        **kwargs,
    ):
        """Keep legacy multimodal history compatible with text-only chat endpoints."""
        if self is None or req is None or not bool(getattr(self, "enabled", False)):
            return
        cleaned, stats = sanitize_history_image_blocks(getattr(req, "contexts", None))
        if not stats.get("changed"):
            return
        try:
            req.contexts = cleaned
        except Exception:
            return
        logger.info(
            "已兼容化历史图片消息: session=%s messages=%s image_blocks=%s",
            _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            stats.get("messages_changed", 0),
            stats.get("image_blocks_replaced", 0),
        )

    @filter.on_llm_request(priority=-250000)
    @_multi_persona_event_context
    async def sanitize_gif_inputs_before_provider(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        *args,
        **kwargs,
    ):
        """Keep provider adapters from receiving unsupported raw GIF inputs."""
        if self is None or req is None or not bool(getattr(self, "enabled", False)):
            return
        replaced, dropped = self._sanitize_provider_request_gif_inputs(req)
        if replaced or dropped:
            logger.info(
                "Provider 请求中的 GIF 已兼容化: converted=%s dropped=%s session=%s",
                replaced,
                dropped,
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            )

    def _sanitize_unverified_repeat_elapsed_claim(
        self,
        inbound_text: str,
        response_text: str,
        user: dict[str, Any],
    ) -> str:
        text = str(response_text or "").strip()
        if not text:
            return ""
        inbound = str(inbound_text or "").strip()
        if not re.search(r"(说过|讲过|提过|聊过|发过|说了|讲了|提了).{0,4}(啦|了|呀|啊)?$", inbound):
            return text
        if not re.search(r"\d+\s*(?:个)?\s*(?:小时|分钟|天)前.{0,8}(?:说过|讲过|提过|聊过|发过)", text):
            return text

        last_at = 0.0
        if isinstance(user, dict):
            last_at = _safe_float(user.get("last_companion_message_at"), 0) or _safe_float(user.get("last_reply_at"), 0)
        elapsed = _now_ts() - last_at if last_at > 0 else 0.0
        if 0 < elapsed <= 90 * 60:
            replacement = "刚才说过了"
        elif 0 < elapsed <= 6 * 3600:
            replacement = "前面说过了"
        else:
            replacement = "之前说过了"
        cleaned = re.sub(
            r"\d+\s*(?:个)?\s*(?:小时|分钟|天)前.{0,4}(?:已经|就)?(?:说过|讲过|提过|聊过|发过)(?:了)?",
            replacement,
            text,
        )
        cleaned = re.sub(r"(刚才说过了|前面说过了|之前说过了)(?:了)+", r"\1", cleaned)
        return cleaned.strip()

    def _sanitize_robotic_topic_choice_after_repeat_correction(
        self,
        inbound_text: str,
        response_text: str,
    ) -> str:
        text = str(response_text or "").strip()
        if not text:
            return ""
        inbound = str(inbound_text or "").strip()
        if not re.search(r"(说过|讲过|提过|聊过|发过|说了|讲了|提了).{0,4}(啦|了|呀|啊)?$", inbound):
            return text
        original = text
        text = re.sub(r"刚醒(?=脑子|反应|没转|有点懵)", "刚才", text)
        text = re.sub(
            r"(?:（|\()\s*(?:看来|可能|大概)?\s*刚(?:才)?脑子([^）)]{0,24}?没(?:转|反应)[^）)]*?)\s*(?:）|\))",
            r"刚才脑子\1。",
            text,
        )
        text = re.sub(
            r"(?:那)?\s*(?:你)?(?:希望|想让|要不要|要我|我是不是该)?[^。！？!?]{0,36}(?:换个话题|换话题)[^。！？!?]{0,36}(?:继续聊|接着聊|聊下去)[^。！？!?]*[？?。！!]*",
            "",
            text,
        )
        text = re.sub(
            r"(?:那)?\s*(?:你)?(?:希望|想让|要不要|要我|我是不是该)?[^。！？!?]{0,36}(?:继续聊|接着聊|聊下去)[^。！？!?]{0,36}(?:换个话题|换话题)[^。！？!?]*[？?。！!]*",
            "",
            text,
        )
        text = re.sub(r"\s+", " ", text).strip()
        text = re.sub(r"([。！？!?])\s+", r"\1", text)
        text = re.sub(r"[，,、；;]\s*$", "。", text).strip()
        if text != original and not re.search(r"(不绕|先收|换个轻点|我记住|脑子|对哦|说过)", text):
            text = f"{text.rstrip('。！？!?')}，我先不绕这个了。"
        if text != original and re.fullmatch(r"(啊[，,。…]*)?(对哦[，,。…]*)?", text):
            text = "啊，对哦，刚才脑子没转过来，我先不绕这个了。"
        return text or original

    def _stop_group_member_safety_event(self, event: AstrMessageEvent) -> None:
        """清空可能已生成的结果，并停止已静默成员的当前群消息。"""
        try:
            event.set_result(self._build_result_from_chain([]))
        except Exception:
            pass
        try:
            event.stop_event()
        except Exception:
            pass
        setattr(event, "_private_companion_member_safety_blocked", True)

