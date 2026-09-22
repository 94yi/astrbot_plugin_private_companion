# -*- coding: utf-8 -*-
"""outbound_delivery 域。

由 tools/split_mixin_domain.py 从 proactive_message.py 机械抽取（58 个方法 + 1 个模块级名字 + 0 个类级赋值 / 2228 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveMessageMixin）。
"""
from __future__ import annotations

import asyncio
import inspect
import json
import os
import re
import time
import uuid
from .final_response_persistence import collect_proactive_delivery
from .helpers import (
    _format_history_media_marker,
    _normalize_photo_subject_owner,
    _now_ts,
    _path_text,
    _photo_subject_owner_prompt_label,
    _redact_outbound_secrets,
    _safe_int,
    _single_line,
    _strip_outbound_control_blocks,
    _today_key,
)
from .persona_config import runtime_persona_setting
from .segmented_message import (
    component_kind,
    component_order_from_owner,
    component_strategies_from_owner,
    plan_component_chunks,
    sanitize_llm_segment_control_tokens,
)
from astrbot.api.event import AstrMessageEvent, MessageChain
from astrbot.core.agent.message import AssistantMessageSegment, UserMessageSegment
from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
from astrbot.core.platform.message_session import MessageSession
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.platform import PlatformStatus
from astrbot.core.star.star_handler import EventType, star_handlers_registry
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from .logging_util import get_module_logger

logger = get_module_logger(__name__)



# ---- 宿主全局转发层（由 tmp/refactor/autofix_domain_globals.py 生成）----
# ==== 需真实对象（isinstance/下标/继承）：复制宿主的 import 语句 ====
try:
    from astrbot.api.message_components import At, Image, Plain, Record, Reply
except ImportError:
    from astrbot.api.message_components import At, Image, Plain
    from astrbot.core.message.components import Record
    try:
        from astrbot.api.message_components import Reply
    except ImportError:
        try:
            from astrbot.core.message.components import Reply
        except ImportError:
            Reply = None
# ==== 需实时转发（可被 patch）：同名函数转发宿主 ====
def _now_ts(*args, **kwargs):
    from . import proactive_message as _host
    return getattr(_host, "_now_ts")(*args, **kwargs)
# ---- 宿主全局转发层结束 ----

# ---- 宿主全局取值助手（由 tmp/refactor/autofix_domain_globals.py 生成）----
def _host_Image():
    """按宿主当前的 ``Image`` 取值，保住 ``patch("<宿主>.Image")`` 的能力。

    ``Image`` 在本模块有**两种互斥**用法：``isinstance(x, Image)`` 需要真实
    class 对象（转发函数会 ``TypeError: isinstance() arg 2 must be a type``），
    而 ``Image.xxx(...)`` 构造调用需要可被替换的入口（测试会 patch 宿主）。
    折中：模块级 ``Image`` 保持真实 class 供 isinstance，构造调用改走本函数 ——
    优先取宿主属性（被 patch 时即替身），否则退回模块级真实 class。
    """
    from . import proactive_message as _host

    candidate = getattr(_host, "Image", None)
    if candidate is None or candidate is Image:
        return Image
    return candidate
# ---- 宿主全局取值助手结束 ----

@dataclass(frozen=True, slots=True)
class _ProactiveSendOutcome:
    delivered: bool
    complete: bool
    delivered_text: str = ""
    image_delivered: bool = False
    extra_components_delivered: int = 0
    note: str = ""
    primary_complete: bool = False
    delivery_umo: str = ""
    delivered_chain: tuple[Any, ...] = ()

    def __bool__(self) -> bool:
        return self.delivered


class ProactiveMessageOutboundDeliveryMixin:
    """outbound_delivery 域（从 ProactiveMessageMixin 拆出）。"""


    def _extract_action_photo_caption(self, action_context: str) -> str:
        text = str(action_context or "")
        match = re.search(r"(?:画面|图片画面|画面草稿)[:：]\s*(.+)", text)
        if not match:
            return ""
        return _single_line(match.group(1).splitlines()[0], 220)

    def _extract_action_photo_subject_owner(self, action_context: str) -> str:
        text = str(action_context or "")
        match = re.search(r"(?:图片主体归属|画面主体归属)[:：]\s*([^\r\n]+)", text)
        if not match:
            return ""
        return _normalize_photo_subject_owner(match.group(1))

    def _build_outbound_chain(
        self,
        text: str,
        image_path: str = "",
        extra_components: list[Any] | None = None,
    ) -> list[Any]:
        chain: list[Any] = []
        if text:
            chain.append(Plain(text))
        for component in extra_components or []:
            if component is not None:
                chain.append(component)
        if image_path and os.path.exists(image_path):
            try:
                chain.append(_host_Image().fromFileSystem(image_path))
            except AttributeError:
                chain.append(_host_Image().from_file_system(image_path))
        if not chain:
            chain.append(Plain(""))
        return chain

    def _parse_message_session(self, umo: str) -> MessageSession | None:
        try:
            return MessageSession.from_str(str(umo or ""))
        except Exception:
            return None

    def _platform_instance_id(self, platform: Any | None) -> str:
        if platform is None:
            return ""
        try:
            meta = platform.meta()
        except Exception:
            return ""
        return str(getattr(meta, "id", "") or getattr(meta, "name", "") or "").strip()

    def _session_for_platform(self, session: MessageSession, platform: Any | None = None) -> MessageSession:
        platform_id = self._platform_instance_id(platform) or str(getattr(session, "platform_id", "") or "")
        return MessageSession(
            platform_name=platform_id,
            message_type=self._message_type_for_session(session),
            session_id=str(getattr(session, "session_id", "") or ""),
        )

    def _get_platform_for_session(self, session: MessageSession) -> Any | None:
        platform_id = str(getattr(session, "platform_id", "") or "")
        manager = getattr(self.context, "platform_manager", None)
        if not platform_id or not manager:
            return None
        platforms = []
        try:
            platforms = list(manager.get_insts())
        except Exception:
            platforms = list(getattr(manager, "platform_insts", []) or [])
        for platform in platforms:
            try:
                meta = platform.meta()
            except Exception:
                continue
            if getattr(meta, "id", "") == platform_id or getattr(meta, "name", "") == platform_id:
                return platform
        return None

    def _message_type_for_session(self, session: MessageSession) -> MessageType:
        msg_type = getattr(session, "message_type", MessageType.FRIEND_MESSAGE)
        if isinstance(msg_type, MessageType):
            return msg_type
        msg_type_text = str(msg_type or "")
        if "Group" in msg_type_text or "GROUP" in msg_type_text:
            return MessageType.GROUP_MESSAGE
        return MessageType.FRIEND_MESSAGE

    def _format_send_exception(self, exc: Exception | BaseException | None) -> str:
        if exc is None:
            return ""
        text = _single_line(str(exc), 180)
        if text:
            return f"{exc.__class__.__name__}: {text}"
        return repr(exc)

    @staticmethod
    def _is_onebot_event_checker_send_rejection(error: Any) -> bool:
        """Identify the NTQQ sendMsg rejection shared by every aiocqhttp send route."""
        text = str(error or "").strip().lower()
        compact = re.sub(r"\s+", "", text)
        has_retcode = any(
            token in compact
            for token in ("retcode=1200", "retcode:1200", "'retcode':1200", '\"retcode\":1200')
        )
        return bool(
            has_retcode
            and "eventcheckerfailed" in compact
            and ("sendmsg" in compact or "nodeikernelmsgservice" in compact)
        )

    @staticmethod
    def _onebot_event_checker_rejection_summary() -> str:
        return "QQ/NTQQ 拒绝发送（retcode=1200，EventChecker sendMsg）；目标可能暂时不可私聊、好友状态已变化，或 QQ 客户端正处于异常状态"

    def _describe_send_target(self, umo: str, session: MessageSession | None, platform: Any | None) -> str:
        if session is None:
            return f"umo={_single_line(umo, 140) or '-'} session=unparsed platform=-"
        platform_id = _single_line(getattr(session, "platform_id", ""), 60)
        session_id = _single_line(getattr(session, "session_id", ""), 80)
        message_type = _single_line(getattr(session, "message_type", ""), 60)
        platform_desc = "found" if platform else "missing"
        if platform:
            platform_desc = _single_line(self._platform_instance_id(platform), 80) or platform.__class__.__name__
        return (
            f"umo={_single_line(umo, 140) or '-'} "
            f"platform_id={platform_id or '-'} type={message_type or '-'} session_id={session_id or '-'} platform={platform_desc}"
        )

    def _apply_proactive_tts_message_scope(self, event: Any, chain: list[Any]) -> bool:
        feature_enabled = getattr(self, "_feature_enabled_or_temp_unlocked", None)
        tts_enabled = (
            feature_enabled("enable_tts_enhancement")
            if callable(feature_enabled)
            else bool(runtime_persona_setting(self, "enable_tts_enhancement", False))
        )
        if (
            not tts_enabled
            or str(
                runtime_persona_setting(self, "tts_message_scope", "replies_only")
                or "replies_only"
            ).lower()
            != "replies_and_proactive"
        ):
            return False
        if any(isinstance(component, Record) for component in chain) or any(
            bool(getattr(component, "_private_companion_skip_tts_enhancement", False))
            for component in chain
        ):
            return False
        try:
            setattr(event, "_private_companion_tts_request_applied", True)
            setattr(event, "_private_companion_tts_forced_by_message_scope", True)
        except Exception:
            return False
        return True

    async def _trigger_proactive_decorating_hooks(self, umo: str, chain: list[Any]) -> list[Any]:
        if not runtime_persona_setting(self, "enable_proactive_decorating_hooks", True) or not chain:
            return chain
        session = self._parse_message_session(umo)
        if not session:
            return chain
        platform = self._get_platform_for_session(session)
        if not platform:
            return chain
        try:
            message_obj = AstrBotMessage()
            message_obj.type = self._message_type_for_session(session)
            message_obj.self_id = str(getattr(session, "session_id", "") or "")
            message_obj.session_id = str(getattr(session, "session_id", "") or "")
            message_obj.message_id = f"private_companion_proactive_{uuid.uuid4().hex}"
            message_obj.sender = MessageMember(user_id=message_obj.session_id)
            message_obj.message = chain
            message_obj.message_str = ""
            message_obj.raw_message = None
            message_obj.timestamp = int(time.time())
            event = AstrMessageEvent("", message_obj, platform.meta(), message_obj.session_id)
            event.set_result(self._build_result_from_chain(chain))
            setattr(event, "_private_companion_proactive_delivery_umo", umo)
            if self._apply_proactive_tts_message_scope(event, chain):
                logger.info(
                    "主动消息按 TTS 生效范围进入强化链: session=%s",
                    _single_line(umo, 120) or "unknown",
                )
            for component in chain:
                raw_full_text = getattr(component, "_private_companion_proactive_full_text", "")
                if not raw_full_text:
                    continue
                setattr(event, "_private_companion_proactive_full_text", raw_full_text)
                setattr(
                    event,
                    "_private_companion_proactive_segment_index",
                    max(0, int(getattr(component, "_private_companion_proactive_segment_index", 0) or 0)),
                )
                setattr(
                    event,
                    "_private_companion_proactive_segment_count",
                    max(1, int(getattr(component, "_private_companion_proactive_segment_count", 1) or 1)),
                )
                break
            if any(
                bool(getattr(component, "_private_companion_skip_tts_enhancement", False))
                for component in chain
            ):
                setattr(event, "_private_companion_skip_tts_enhancement", "proactive_prebuilt_voice")
        except Exception as e:
            logger.debug("构造主动消息装饰事件失败,跳过 hooks: %s", e)
            return chain
        try:
            handlers = star_handlers_registry.get_handlers_by_event_type(
                EventType.OnDecoratingResultEvent
            )
        except Exception as e:
            logger.debug("获取装饰 hooks 失败: %s", e)
            return chain
        for handler in handlers:
            try:
                await handler.handler(event)
            except Exception as e:
                logger.warning(
                    "主动消息装饰 hook 失败: %s: %s",
                    getattr(handler, "handler_full_name", "unknown"),
                    e,
                )
        is_stopped = getattr(event, "is_stopped", None)
        if callable(is_stopped):
            try:
                if is_stopped():
                    return []
            except Exception:
                pass
        result = event.get_result()
        processed = getattr(result, "chain", None) if result is not None else None
        if processed is None:
            return []
        processed_chain = list(processed or [])
        return self._filter_decorated_proactive_chain(chain, processed_chain)

    def _proactive_plain_segment_component(
        self,
        text: str,
        *,
        full_text: str = "",
        index: int = 0,
        count: int = 1,
        suppress_tts: bool = False,
    ) -> Plain:
        comp = Plain(text)
        full_source = str(full_text or "")
        marker_cleaner = getattr(self, "_strip_llm_segment_marker_lines", None)
        if callable(marker_cleaner):
            full_source = marker_cleaner(full_source)
        clean_full = _single_line(full_source, max(1200, len(full_source) + 32))
        if clean_full:
            try:
                object.__setattr__(comp, "_private_companion_proactive_full_text", clean_full)
                object.__setattr__(comp, "_private_companion_proactive_segment_index", max(0, int(index)))
                object.__setattr__(comp, "_private_companion_proactive_segment_count", max(1, int(count)))
            except Exception:
                pass
        if suppress_tts:
            try:
                object.__setattr__(comp, "_private_companion_skip_tts_enhancement", True)
            except Exception:
                pass
        return comp

    def _filter_decorated_proactive_chain(self, original_chain: list[Any], processed_chain: list[Any]) -> list[Any]:
        if not processed_chain:
            return []

        filtered: list[Any] = []
        removed_any = False
        for component in processed_chain:
            if isinstance(component, Plain):
                text = self._plain_component_text(component)
                if self._is_proactive_delivery_receipt_text(text):
                    removed_any = True
                    continue
                cleaned = self._strip_proactive_delivery_receipt_lines(text)
                if not cleaned:
                    removed_any = True
                    continue
                if cleaned != text:
                    removed_any = True
                    filtered.append(Plain(cleaned))
                else:
                    filtered.append(component)
                continue
            filtered.append(component)

        if filtered:
            return filtered
        return [] if removed_any else processed_chain

    @staticmethod
    def _plain_component_text(component: Any) -> str:
        for attr in ("text", "content", "message"):
            value = getattr(component, attr, None)
            if isinstance(value, str):
                return value
        return str(component or "")

    @staticmethod
    def _contains_inline_image_tag(text: str) -> bool:
        return bool(re.search(r"<img\b[^>]*\bsrc\s*=", str(text or ""), flags=re.IGNORECASE))

    @staticmethod
    def _is_proactive_delivery_receipt_text(text: str) -> bool:
        raw = _single_line(text, 240)
        if not raw:
            return False
        compact = re.sub(r"[\s。.!！?？,，；;:：、~～\"'“”‘’（）()【】\[\]]+", "", raw).lower()
        if not compact:
            return False
        if compact in {
            "已发送",
            "发送成功",
            "发送完成",
            "发送完毕",
            "已成功发送",
            "消息已发送",
            "消息发送成功",
            "messagesent",
            "sent",
            "我主动开口了",
            "我主动发了一段语音",
            "我主动分享了一点东西",
            "我主动做了一次小互动",
        }:
            return True
        if re.fullmatch(r"(?:图|图片|照片)(?:好|好了|生成好了|出来了|完成了)[啦了]*", compact):
            return True
        if re.fullmatch(r"(?:生图|出图|图片生成)(?:完成|好了|成功)[啦了]*", compact):
            return True
        if re.search(r"(?:还在|正在|继续)?(?:排队|队列|等待生成|等图|等图片|等它出图)", compact):
            return True
        if re.match(r"^(?:已经|已)(?:发|发送)过去[啦了]?(?:等(?:着|他|你|对方)|等回复|等回我)?$", compact):
            return True
        if re.match(r"^等(?:着)?(?:他|你|对方)?回(?:我|复)?[啦了]*$", compact):
            return True
        if compact.startswith("消息已送达"):
            return True
        if re.match(r"^这是.{0,80}(?:发的|发送的|收到的).{0,80}(?:消息|打招呼|问候|回复)", compact):
            return True
        if re.match(r"^这(?:条|是).{0,80}(?:语气|内容|消息).{0,80}$", compact):
            return True
        receipt_prefixes = (
            "消息已发送给",
            "消息发送给",
            "已发送给",
            "已经发送给",
            "已向",
            "已经向",
        )
        receipt_descriptors = (
            "讲的是",
            "说的是",
            "内容是",
            "内容就是",
            "发的是",
            "转述的是",
            "分享的是",
            "告诉的是",
        )
        if compact.startswith(receipt_prefixes) and any(token in compact for token in receipt_descriptors):
            return True
        long_receipt_markers = (
            ("已经把", "转给"),
            ("已把", "转给"),
            ("已经将", "转给"),
            ("已将", "转给"),
            ("已经发给", "就假装"),
            ("已经发送给", "就假装"),
            ("就假装", "语气很自然"),
            ("随手分享", "语气很自然"),
        )
        if any(all(token in raw for token in pair) for pair in long_receipt_markers):
            return True
        if (
            any(token in compact for token in ("视频链接转给", "链接转给", "消息转给", "内容转给"))
            and any(token in compact for token in ("已经", "已", "完成", "成功"))
        ):
            return True
        return (
            len(compact) <= 32
            and any(token in compact for token in ("发送给用户", "发给用户", "发送给对方", "发给对方", "发出去了"))
            and any(token in compact for token in ("已", "已经", "完成", "成功"))
        )

    @staticmethod
    def _is_proactive_instruction_leak_text(text: str) -> bool:
        raw = _single_line(text, 360)
        if not raw:
            return False
        compact = re.sub(r"[\s。.!！?？,，；;:：、~～\"'“”‘’（）()【】\[\]<>《》]+", "", raw).lower()
        if not compact:
            return False
        exact_leaks = {
            "直接在当前对话中输出这条主动消息",
            "请直接在当前对话中输出这条主动消息",
            "在当前对话中输出这条主动消息",
            "直接输出这条主动消息",
            "输出这条主动消息",
            "发送这条主动消息",
            "sendthisproactivemessage",
            "outputthisproactivemessage",
        }
        if compact in exact_leaks:
            return True
        has_proactive_target = "主动消息" in raw or "proactive message" in raw.lower()
        has_delivery_command = any(
            token in compact
            for token in (
                "直接输出",
                "请输出",
                "输出这条",
                "输出本条",
                "直接发送",
                "请发送",
                "发送这条",
                "发出这条",
                "sendthis",
                "outputthis",
            )
        )
        has_instruction_context = any(
            token in compact
            for token in (
                "当前对话",
                "当前聊天",
                "本轮对话",
                "用户对话",
                "聊天窗口",
                "给用户",
                "touser",
                "currentchat",
                "currentconversation",
            )
        )
        if has_proactive_target and has_delivery_command and (has_instruction_context or len(compact) <= 36):
            return True
        if len(compact) <= 44 and has_delivery_command and has_instruction_context and any(
            token in compact for token in ("消息", "正文", "文本", "content", "message")
        ):
            return True
        return False

    def _strip_proactive_delivery_receipt_lines(self, text: str) -> str:
        kept: list[str] = []
        for raw_line in str(text or "").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if self._is_proactive_delivery_receipt_text(line):
                continue
            kept.append(line)
        return "\n".join(kept).strip()

    def _validate_proactive_outbound_candidate(
        self,
        text: str,
        *,
        umo: str = "",
        image_path: str = "",
        extra_components: list[Any] | None = None,
        reason: str = "",
        action: str = "",
        source: str = "send",
    ) -> dict[str, Any]:
        raw = str(text or "").strip()
        has_media = bool(_path_text(image_path, 1000)) or bool(extra_components)
        if not raw:
            sticker_pending_getter = getattr(self, "_proactive_sticker_only_pending", None)
            try:
                sticker_only_pending = bool(sticker_pending_getter(umo)) if callable(sticker_pending_getter) else False
            except Exception:
                sticker_only_pending = False
            if has_media or sticker_only_pending:
                return {"decision": "send", "text": "", "reason": ""}
            return {"decision": "drop", "text": "", "reason": "主动行为没有产出可发送内容", "hard": True}
        if self._looks_like_internal_provider_error_text(raw):
            return {"decision": "drop", "text": "", "reason": "主动正文是模型/工具调用失败信息", "hard": True}

        kept_lines: list[str] = []
        removed_leak = False
        for raw_line in raw.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if (
                self._is_proactive_delivery_receipt_text(line)
                or self._is_proactive_instruction_leak_text(line)
                or self._framework_agent_meta_summary_leak(line)
            ):
                removed_leak = True
                continue
            kept_lines.append(line)
        if removed_leak:
            cleaned = "\n".join(kept_lines).strip()
            if cleaned:
                return {"decision": "rewrite", "text": cleaned, "reason": "已清理主动正文中的内部提示词/执行回执残留"}
            if has_media:
                return {"decision": "rewrite", "text": "", "reason": "已清理主动正文中的内部提示词/执行回执残留"}
            return {"decision": "drop", "text": "", "reason": "主动正文只剩内部提示词/执行回执残留", "hard": True}

        if self._is_proactive_delivery_receipt_text(raw):
            return {"decision": "drop", "text": "", "reason": "主动正文是工具/执行状态回执", "hard": True}
        if self._is_proactive_instruction_leak_text(raw):
            return {"decision": "drop", "text": "", "reason": "主动正文疑似内部提示词/发送指令泄漏", "hard": True}
        if self._framework_agent_meta_summary_leak(raw):
            return {"decision": "drop", "text": "", "reason": "主动正文疑似工具循环/内部发送摘要泄漏", "hard": True}

        return {"decision": "send", "text": raw, "reason": ""}

    def _proactive_archive_context_text(self, text: str) -> bool:
        cleaned = _single_line(text, 500)
        if not cleaned:
            return False
        if "【主动承接占位】" in cleaned or "下一条是 Bot 主动发出的内容" in cleaned:
            return True
        if self._is_proactive_delivery_receipt_text(cleaned):
            return True
        return False

    @staticmethod
    def _strip_leading_sentence_boundary_artifacts(text: str) -> str:
        cleaned = str(text or "").strip()
        cleaned = re.sub(r"^(?:[。！？!?；;，,、：:]+[\s\u3000]*)+", "", cleaned).strip()
        return cleaned

    def _forward_sender_id_for_segments(self, event: Any | None = None) -> str:
        if event is not None:
            try:
                sender_id = _single_line(self._event_self_id(event), 40)
                if sender_id:
                    return sender_id
            except Exception:
                pass
        for sender_id in self._known_bot_self_ids():
            if sender_id:
                return sender_id
        return "0"

    def _forward_nodes_for_segments(self, segments: list[str], *, event: Any | None = None) -> list[dict[str, Any]]:
        sender_name = _single_line(
            runtime_persona_setting(self, "bot_name", ""), 40
        ) or "PrivateCompanion"
        sender_id = self._forward_sender_id_for_segments(event)
        nodes: list[dict[str, Any]] = []
        for segment in segments:
            text = str(segment or "").strip()
            if not text:
                continue
            nodes.append(
                {
                    "type": "node",
                    "data": {
                        "name": sender_name,
                        "uin": sender_id,
                        "content": [{"type": "text", "data": {"text": text}}],
                    },
                }
            )
        return nodes

    def _clean_forward_segment_texts(self, segments: list[str]) -> list[str]:
        cleaned: list[str] = []
        for segment in segments:
            text = re.sub(r"</?t{2,}s\b[^>]*>", "", str(segment or ""), flags=re.IGNORECASE).strip()
            text = self._strip_leading_sentence_boundary_artifacts(text)
            if text:
                cleaned.append(text)
        return cleaned

    def _onebot_forward_action_result_ok(self, result: Any) -> bool:
        if result is None:
            return True
        if isinstance(result, dict):
            status = str(result.get("status") or result.get("result") or "").strip().lower()
            if status in {"failed", "fail", "error", "nok"}:
                return False
            retcode = result.get("retcode", result.get("code", None))
            if retcode is not None:
                try:
                    return int(retcode) == 0
                except Exception:
                    return False
            data = result.get("data")
            if isinstance(data, dict) and any(data.get(key) for key in ("message_id", "forward_id", "res_id", "resid")):
                return True
            return any(result.get(key) for key in ("message_id", "forward_id", "res_id", "resid"))
        return bool(result)

    async def _call_onebot_forward_action(self, client: Any, action: str, **params: Any) -> bool:
        for attr in ("call_action", "call_api", "api"):
            func = getattr(client, attr, None)
            if not callable(func):
                continue
            try:
                result = func(action, **params)
            except TypeError:
                try:
                    result = func(action, params)
                except Exception as exc:
                    if self._delivery_outcome_is_uncertain(exc):
                        self._log_uncertain_onebot_submission(action, exc)
                        return True
                    continue
            except Exception as exc:
                if self._delivery_outcome_is_uncertain(exc):
                    self._log_uncertain_onebot_submission(action, exc)
                    return True
                continue
            try:
                if hasattr(result, "__await__"):
                    result = await result
            except Exception as exc:
                if self._delivery_outcome_is_uncertain(exc):
                    self._log_uncertain_onebot_submission(action, exc)
                    return True
                continue
            if self._onebot_forward_action_result_ok(result):
                return True
        func = getattr(client, action, None)
        if callable(func):
            try:
                result = func(**params)
            except Exception as exc:
                if self._delivery_outcome_is_uncertain(exc):
                    self._log_uncertain_onebot_submission(action, exc)
                    return True
                return False
            try:
                if hasattr(result, "__await__"):
                    result = await result
            except Exception as exc:
                if self._delivery_outcome_is_uncertain(exc):
                    self._log_uncertain_onebot_submission(action, exc)
                    return True
                return False
            return self._onebot_forward_action_result_ok(result)
        return False

    async def _send_segmented_forward_message(
        self,
        *,
        target_type: str,
        target_id: str,
        segments: list[str],
        event: Any | None = None,
        source: str = "",
    ) -> bool:
        send_as_forward = self._segmented_setting(
            "send_as_forward",
            chat_type=target_type,
            default=False,
        )
        if not bool(send_as_forward):
            return False
        target_type = str(target_type or "").strip().lower()
        target_id = _single_line(target_id, 80)
        if target_type not in {"private", "group"} or not target_id:
            return False
        raw_segments = [_redact_outbound_secrets(item, self).strip() for item in segments if str(item or "").strip()]
        if len(raw_segments) <= 1:
            return False
        if runtime_persona_setting(self, "enable_tts_enhancement", False) and any(
            re.search(r"</?t{2,}s\b", item, flags=re.IGNORECASE) for item in raw_segments
        ):
            logger.info("分段合并消息跳过 TTS 内容: source=%s target=%s:%s", source or "unknown", target_type, target_id)
            return False
        cleaned_segments = self._clean_forward_segment_texts(raw_segments)
        if len(cleaned_segments) <= 1:
            return False
        hit = self._forbidden_recall_hit("\n".join(cleaned_segments))
        if hit:
            logger.warning(
                "分段合并消息命中违禁词，已拦截发送: source=%s target=%s:%s word=%s",
                source or "unknown",
                target_type,
                target_id,
                _single_line(hit, 40),
            )
            return False
        client = self._resolve_aiocqhttp_client()
        if client is None:
            return False
        nodes = self._forward_nodes_for_segments(cleaned_segments, event=event)
        if len(nodes) <= 1:
            return False
        target_value: Any = target_id
        try:
            target_value = int(target_id)
        except Exception:
            pass
        if target_type == "group":
            attempts = [
                ("send_group_forward_msg", {"group_id": target_value, "messages": nodes}),
                ("send_group_forward_msg", {"group_id": target_value, "nodes": nodes}),
                ("send_forward_msg", {"group_id": target_value, "messages": nodes}),
                ("send_forward_msg", {"group_id": target_value, "nodes": nodes}),
            ]
        else:
            attempts = [
                ("send_private_forward_msg", {"user_id": target_value, "messages": nodes}),
                ("send_private_forward_msg", {"user_id": target_value, "nodes": nodes}),
                ("send_forward_msg", {"user_id": target_value, "messages": nodes}),
                ("send_forward_msg", {"user_id": target_value, "nodes": nodes}),
            ]
        for action, params in attempts:
            if await self._call_onebot_forward_action(client, action, **params):
                self._confirm_outbound_delivery(
                    "",
                    [Plain(segment) for segment in cleaned_segments],
                )
                logger.info(
                    "分段消息已合并转发发送: source=%s target=%s:%s segments=%s",
                    source or "unknown",
                    target_type,
                    target_id,
                    len(cleaned_segments),
                )
                return True
        logger.info(
            "分段合并转发发送不可用，回退普通分段: source=%s target=%s:%s segments=%s",
            source or "unknown",
            target_type,
            target_id,
            len(cleaned_segments),
        )
        return False

    async def _send_segmented_proactive_forward_message(self, umo: str, segments: list[str], *, source: str = "proactive") -> bool:
        platform_supports = getattr(self, "_platform_supports", None)
        if callable(platform_supports) and not platform_supports("merged_forward", umo=umo):
            return False
        session = self._parse_message_session(umo)
        if not session:
            return False
        target_id = _single_line(getattr(session, "session_id", ""), 80)
        if not target_id:
            return False
        target_type = "group" if self._message_type_for_session(session) == MessageType.GROUP_MESSAGE else "private"
        return await self._send_segmented_forward_message(
            target_type=target_type,
            target_id=target_id,
            segments=segments,
            source=source,
        )

    async def _send_segmented_event_forward_message(self, event: AstrMessageEvent, segments: list[str], *, source: str = "decorating_result") -> bool:
        platform_supports = getattr(self, "_platform_supports", None)
        if callable(platform_supports) and not platform_supports("merged_forward", event=event):
            return False
        try:
            if bool(getattr(event, "is_private_chat", lambda: False)()):
                user_id = _single_line(event.get_sender_id(), 80)
                if user_id:
                    return await self._send_segmented_forward_message(
                        target_type="private",
                        target_id=user_id,
                        segments=segments,
                        event=event,
                        source=source,
                    )
        except Exception:
            pass
        group_id = self._extract_group_id_from_event(event)
        if group_id:
            return await self._send_segmented_forward_message(
                target_type="group",
                target_id=group_id,
                segments=segments,
                event=event,
                source=source,
            )
        return False

    def _segmented_chat_scope_allows(self, chat_type: str) -> bool:
        chat_type = str(chat_type or "").strip().lower()
        if chat_type not in {"private", "group"}:
            chat_type = "private"
        if bool(
            runtime_persona_setting(self, "enable_segmented_proactive_chat_profiles", False)
        ):
            return bool(
                runtime_persona_setting(
                    self,
                    f"segmented_proactive_{chat_type}_enabled",
                    True,
                )
            )
        scope = str(
            runtime_persona_setting(self, "segmented_proactive_chat_scope", "all") or "all"
        ).strip().lower()
        if scope not in {"all", "private", "group"}:
            scope = "all"
        return scope == "all" or scope == chat_type

    def _segmented_chat_type_for_umo(self, umo: str) -> str:
        session = self._parse_message_session(umo)
        if session and self._message_type_for_session(session) == MessageType.GROUP_MESSAGE:
            return "group"
        return "private"

    def _segmented_chat_type_for_event(self, event: AstrMessageEvent) -> str:
        try:
            if bool(getattr(event, "is_private_chat", lambda: False)()):
                return "private"
        except Exception:
            pass
        return "group" if self._extract_group_id_from_event(event) else "private"

    def _segmented_setting(
        self,
        name: str,
        *,
        event: AstrMessageEvent | None = None,
        umo: str = "",
        chat_type: str = "",
        default: Any = None,
    ) -> Any:
        normalized_name = str(name or "").strip()
        fallback = runtime_persona_setting(
            self,
            f"segmented_proactive_{normalized_name}",
            default,
        )
        if not bool(
            runtime_persona_setting(self, "enable_segmented_proactive_chat_profiles", False)
        ):
            return fallback
        resolved_chat_type = str(chat_type or "").strip().lower()
        if resolved_chat_type not in {"private", "group"}:
            resolved_chat_type = (
                self._segmented_chat_type_for_event(event)
                if event is not None
                else self._segmented_chat_type_for_umo(umo)
            )
        return runtime_persona_setting(
            self,
            f"segmented_proactive_{resolved_chat_type}_{normalized_name}",
            fallback,
        )

    def _segmented_scope_allows_umo(self, umo: str) -> bool:
        return self._segmented_chat_scope_allows(self._segmented_chat_type_for_umo(umo))

    def _segmented_scope_allows_event(self, event: AstrMessageEvent) -> bool:
        return self._segmented_chat_scope_allows(self._segmented_chat_type_for_event(event))

    def _segmented_platform_allows(
        self,
        *,
        event: AstrMessageEvent | None = None,
        umo: str = "",
    ) -> bool:
        platform_supports = getattr(self, "_platform_supports", None)
        return not callable(platform_supports) or bool(
            platform_supports("segmented_reply", event=event, umo=umo)
        )

    async def _onebot_messages_from_chain(self, chain: list[Any]) -> tuple[list[dict[str, Any]], str]:
        try:
            from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent

            messages = await AiocqhttpMessageEvent._parse_onebot_json(MessageChain(chain))
            return list(messages or []), ""
        except Exception as exc:
            return [], self._format_send_exception(exc)

    async def _send_chain_components_via_onebot_direct(
        self,
        umo: str,
        session: MessageSession | None,
        chain: list[Any],
    ) -> tuple[bool, str]:
        if session is None:
            return False, "UMO 无法解析，不能使用 OneBot 原生兜底"
        target_id = _single_line(getattr(session, "session_id", ""), 80)
        if not target_id or not target_id.isdigit():
            return False, f"session_id 不是纯数字，不能使用 OneBot 原生兜底: {target_id or '-'}"
        client = self._resolve_aiocqhttp_client()
        if client is None:
            return False, "没有找到可用的 aiocqhttp/OneBot 客户端"
        messages, parse_error = await self._onebot_messages_from_chain(chain)
        if not messages:
            return False, parse_error or "消息链无法转换为 OneBot 消息段"
        target_value: Any = target_id
        try:
            target_value = int(target_id)
        except Exception:
            pass
        is_group = self._message_type_for_session(session) == MessageType.GROUP_MESSAGE
        action = "send_group_msg" if is_group else "send_private_msg"
        params = {"group_id": target_value, "message": messages} if is_group else {"user_id": target_value, "message": messages}
        ok, error = await self._call_onebot_action_with_error(
            client,
            action,
            at_most_once=True,
            **params,
        )
        if ok:
            logger.info(
                "主动消息已通过 OneBot 原生兜底发送: action=%s target=%s segments=%s umo=%s",
                action,
                target_id,
                len(messages),
                _single_line(umo, 140),
            )
            return True, ""
        return False, error or f"OneBot 原生动作 {action} 返回失败"

    async def _send_chain_components(
        self,
        umo: str,
        chain: list[Any],
        *,
        apply_decorating_hooks: bool = True,
    ) -> bool:
        bot_scope_checker = getattr(self, "_bot_scope_allows_umo", None)
        if callable(bot_scope_checker) and not bot_scope_checker(umo):
            logger.info(
                "Bot 作用域已跳过后台投递: umo=%s",
                _single_line(umo, 140),
            )
            return False
        marker_cleaner = getattr(self, "_strip_llm_segment_marker_lines", None)
        if callable(marker_cleaner):
            cleaned_chain: list[Any] = []
            for component in chain or []:
                if not isinstance(component, Plain):
                    cleaned_chain.append(component)
                    continue
                original = str(getattr(component, "text", "") or "")
                cleaned = marker_cleaner(original)
                if cleaned:
                    cleaned_chain.append(Plain(cleaned) if cleaned != original else component)
            chain = cleaned_chain
        chain_redactor = getattr(self, "_redact_outbound_chain_secrets", None)
        if callable(chain_redactor):
            chain, redacted = chain_redactor(chain)
            if redacted:
                logger.error("主动发送前检测到敏感凭据并已脱敏: umo=%s stage=before_hooks", _single_line(umo, 120))
        hit = self._forbidden_recall_hit(self._chain_text_for_forbidden_recall(chain))
        if hit:
            logger.warning(
                "主动待发送消息命中违禁词，已拦截发送: umo=%s word=%s",
                umo,
                _single_line(hit, 40),
            )
            notifier = getattr(self, "_schedule_reply_interception_forward", None)
            if callable(notifier):
                notifier(
                    "proactive_block",
                    source="主动发送组件校验",
                    reason=f"命中违禁词：{_single_line(hit, 40)}",
                    source_session=umo,
                    before=self._chain_text_for_forbidden_recall(chain),
                )
            return False
        processed_chain = (
            await self._trigger_proactive_decorating_hooks(umo, chain)
            if apply_decorating_hooks
            else list(chain)
        )
        if not processed_chain:
            notifier = getattr(self, "_schedule_reply_interception_forward", None)
            if callable(notifier):
                notifier(
                    "proactive_block",
                    source="主动发送装饰钩子",
                    reason="装饰钩子清空了待发送消息",
                    source_session=umo,
                    before=self._chain_text_for_forbidden_recall(chain),
                )
            return False
        if callable(chain_redactor):
            processed_chain, redacted = chain_redactor(processed_chain)
            if redacted:
                logger.error("主动装饰后检测到敏感凭据并已脱敏: umo=%s stage=after_hooks", _single_line(umo, 120))
        tts_chain_guard = getattr(self, "_sanitize_outbound_tts_chain_without_event", None)
        if callable(tts_chain_guard):
            processed_chain = await tts_chain_guard(processed_chain, umo=umo)
            if not processed_chain:
                notifier = getattr(self, "_schedule_reply_interception_forward", None)
                if callable(notifier):
                    notifier("proactive_block", source="主动发送 TTS 校验", reason="TTS 校验清空了待发送消息", source_session=umo)
                return False
        hit = self._forbidden_recall_hit(self._chain_text_for_forbidden_recall(processed_chain))
        if hit:
            logger.warning(
                "主动装饰后消息命中违禁词，已拦截发送: umo=%s word=%s",
                umo,
                _single_line(hit, 40),
            )
            notifier = getattr(self, "_schedule_reply_interception_forward", None)
            if callable(notifier):
                notifier(
                    "proactive_block",
                    source="主动装饰后校验",
                    reason=f"装饰后命中违禁词：{_single_line(hit, 40)}",
                    source_session=umo,
                    before=self._chain_text_for_forbidden_recall(processed_chain),
                )
            return False
        session = self._parse_message_session(umo)
        platform = self._get_platform_for_session(session) if session else None
        precise_error: Exception | None = None
        if runtime_persona_setting(self, "enable_precise_platform_send", True) and session and platform:
            status = getattr(platform, "status", None)
            if status is not None and status != PlatformStatus.RUNNING:
                logger.warning("目标平台未运行,跳过主动发送: %s", umo)
                notifier = getattr(self, "_schedule_reply_interception_forward", None)
                if callable(notifier):
                    notifier(
                        "proactive_block",
                        source="主动发送平台校验",
                        reason="目标平台未运行",
                        source_session=umo,
                        before=self._chain_text_for_forbidden_recall(processed_chain),
                    )
                raise RuntimeError(f"目标平台未运行，无法发送主动消息: {_single_line(umo, 140)}")
            try:
                session_obj = self._session_for_platform(session, platform)
                precise_result = await platform.send_by_session(session_obj, MessageChain(processed_chain))
                if precise_result is not False:
                    self._confirm_outbound_delivery(umo, processed_chain)
                    return True
                precise_error = RuntimeError("精确平台发送返回 False（平台未接受消息）")
                logger.warning(
                    "精确平台发送未被目标平台接受,回退核心发送: target=%s",
                    self._describe_send_target(umo, session, platform),
                )
            except Exception as e:
                precise_error = e
                if self._is_onebot_event_checker_send_rejection(e):
                    summary = self._onebot_event_checker_rejection_summary()
                    logger.info(
                        "主动发送被 QQ/NTQQ 底层拒绝，停止对同一 sendMsg 链路的立即重复尝试: target=%s",
                        self._describe_send_target(umo, session, platform),
                    )
                    raise RuntimeError(summary) from e
                if self._delivery_outcome_is_uncertain(e):
                    logger.warning(
                        "精确平台发送回执不确定，为避免同一主动消息立即重复发送，本次按已提交处理: target=%s error=%s",
                        self._describe_send_target(umo, session, platform),
                        self._format_send_exception(e),
                    )
                    self._confirm_outbound_delivery(umo, processed_chain)
                    return True
                logger.warning(
                    "精确平台发送失败,回退核心发送: target=%s error=%s",
                    self._describe_send_target(umo, session, platform),
                    self._format_send_exception(e),
                )
        core_error: Exception | None = None
        core_result: Any = None
        core_session: str | MessageSession = umo
        if session and platform:
            core_session = self._session_for_platform(session, platform)
        try:
            core_result = await self.context.send_message(core_session, self._build_result_from_chain(processed_chain))
            if core_result is not False:
                self._confirm_outbound_delivery(umo, processed_chain)
                return True
            platform_supports = getattr(self, "_platform_supports", None)
            if not callable(platform_supports) or platform_supports("onebot_actions", umo=umo):
                logger.warning(
                    "主动核心发送未找到匹配平台,尝试 OneBot 原生兜底: target=%s",
                    self._describe_send_target(umo, session, platform),
                )
            else:
                logger.warning(
                    "主动核心发送未被官方平台接受,不使用 OneBot 原生兜底: target=%s",
                    self._describe_send_target(umo, session, platform),
                )
        except Exception as e:
            core_error = e
            if self._is_onebot_event_checker_send_rejection(e):
                logger.info(
                    "主动核心发送被 QQ/NTQQ 底层拒绝，停止同链立即重试: target=%s",
                    self._describe_send_target(umo, session, platform),
                )
                raise RuntimeError(self._onebot_event_checker_rejection_summary()) from e
            if self._delivery_outcome_is_uncertain(e):
                logger.warning(
                    "主动核心发送回执不确定，为避免 OneBot 兜底重复发送，本次按已提交处理: target=%s error=%s",
                    self._describe_send_target(umo, session, platform),
                    self._format_send_exception(e),
                )
                self._confirm_outbound_delivery(umo, processed_chain)
                return True
            target = self._describe_send_target(umo, session, platform)
            precise_text = self._format_send_exception(precise_error) or "未尝试或未失败"
            fallback_text = self._format_send_exception(e)
            logger.warning(
                "主动核心发送失败: target=%s precise_error=%s fallback_error=%s",
                target,
                precise_text,
                fallback_text,
            )
        platform_supports = getattr(self, "_platform_supports", None)
        if callable(platform_supports) and not platform_supports("onebot_actions", umo=umo):
            target = self._describe_send_target(umo, session, platform)
            precise_text = self._format_send_exception(precise_error) or "未尝试或未失败"
            fallback_text = self._format_send_exception(core_error) if core_error is not None else (
                "AstrBot 核心发送返回 False（平台未找到或官方通道拒绝）"
            )
            raise RuntimeError(
                f"主动消息发送失败: {target}; precise={precise_text}; fallback={fallback_text}; 当前平台不使用 OneBot 原生兜底"
            ) from core_error
        direct_ok, direct_error = await self._send_chain_components_via_onebot_direct(umo, session, processed_chain)
        if direct_ok:
            self._confirm_outbound_delivery(umo, processed_chain)
            return True
        if self._is_onebot_event_checker_send_rejection(direct_error):
            raise RuntimeError(self._onebot_event_checker_rejection_summary())
        target = self._describe_send_target(umo, session, platform)
        precise_text = self._format_send_exception(precise_error) or "未尝试或未失败"
        if core_error is not None:
            fallback_text = self._format_send_exception(core_error)
        elif core_result is False:
            fallback_text = "AstrBot 核心发送返回 False（未找到匹配平台或平台拒绝发送）"
        else:
            fallback_text = "未尝试或未失败"
        logger.warning(
            "主动发送兜底也失败: target=%s precise_error=%s fallback_error=%s direct_error=%s",
            target,
            precise_text,
            fallback_text,
            direct_error,
        )
        raise RuntimeError(
            f"主动消息发送失败: {target}; precise={precise_text}; fallback={fallback_text}; direct={direct_error}"
        ) from core_error

    async def _send_media_proactive_chain(
        self,
        umo: str,
        text: str,
        image_path: str = "",
        *,
        extra_components: list[Any] | None = None,
        quote_message_id: str = "",
        disable_segmenting: bool = False,
        media_delivery_mode: str = "separate_after",
        require_complete_text_before_media: bool = False,
    ) -> _ProactiveSendOutcome:
        trigger_message_id = _single_line(quote_message_id, 120)
        delivered_segments: list[str] = []
        complete = True
        image_delivered = False
        extra_components_delivered = 0
        primary_complete = False
        failure_note = ""

        def outcome(*, note: str = "") -> _ProactiveSendOutcome:
            delivered_text = "\n".join(item for item in delivered_segments if item).strip()
            delivered = bool(delivered_text or image_delivered or extra_components_delivered)
            resolved_note = _single_line(note or failure_note, 240)
            return _ProactiveSendOutcome(
                delivered=delivered,
                complete=bool(delivered and complete and not resolved_note),
                delivered_text=delivered_text,
                image_delivered=image_delivered,
                extra_components_delivered=extra_components_delivered,
                note=resolved_note,
                primary_complete=primary_complete,
            )

        outbound_components = [
            component for component in (extra_components or []) if component is not None
        ]
        has_prebuilt_voice = any(
            isinstance(component, Record) for component in outbound_components
        )
        if self._contains_inline_image_tag(text):
            image_path = ""
            outbound_components = []
        if text:
            await self._maybe_send_input_status(umo, text)
        if media_delivery_mode == "same_message":
            platform_supports = getattr(self, "_platform_supports", None)
            platform_quote = not callable(platform_supports) or platform_supports(
                "reply_quote",
                umo=umo,
            )
            if quote_message_id and not platform_quote:
                logger.info(
                    "当前平台不支持主动引用，正文与表情同链发送已降级为普通发送: umo=%s",
                    _single_line(umo, 140),
                )
                quote_message_id = ""
            recalled_message_id = self._should_cancel_reply_for_recalled_message_ids(
                trigger_message_id
            )
            if recalled_message_id:
                logger.info(
                    "触发消息已撤回，取消主动正文与表情同链发送: umo=%s message_id=%s",
                    umo,
                    recalled_message_id,
                )
                complete = False
                return outcome(note="触发消息已撤回")
            combined_chain = self._build_outbound_chain(
                text,
                image_path,
                extra_components=outbound_components,
            )
            combined_chain = self._with_optional_reply(
                combined_chain,
                quote_message_id,
            )
            sent = await self._send_chain_components(umo, combined_chain)
            if sent:
                delivered_segments.append(text)
                image_delivered = bool(image_path and os.path.exists(image_path))
                extra_components_delivered = len(outbound_components)
                primary_complete = True
            else:
                complete = False
            return outcome(
                note="" if sent else "主动正文与表情同链发送未被平台接受"
            )
        platform_supports = getattr(self, "_platform_supports", None)
        platform_segmented = self._segmented_platform_allows(umo=umo)
        platform_quote = not callable(platform_supports) or platform_supports("reply_quote", umo=umo)
        if quote_message_id and not platform_quote:
            logger.info(
                "当前平台不支持主动引用，已降级为普通发送: umo=%s",
                _single_line(umo, 140),
            )
            quote_message_id = ""
        splitter = getattr(self, "_split_llm_controlled_text_for_event", None)
        if (
            callable(splitter)
            and bool(runtime_persona_setting(self, "enable_llm_controlled_segmenting", False))
            and not disable_segmenting
            and platform_segmented
            and self._segmented_scope_allows_umo(umo)
        ):
            segments = splitter(None, text, umo=umo)
        else:
            segments = self._split_proactive_text(
                text,
                umo=umo,
                image_path="",
                extra_components=None,
                disable_segmenting=disable_segmenting or not platform_segmented or not self._segmented_scope_allows_umo(umo),
            )
        if len(segments) > 1:
            logger.info(
                "主动媒体文本已分段: umo=%s segments=%s lengths=%s",
                _single_line(umo, 140),
                len(segments),
                [len(segment) for segment in segments],
            )
        if quote_message_id and segments and self._quote_skip_reason_for_short_reply(segments[0]):
            quote_message_id = ""

        image_exists = bool(image_path and os.path.exists(image_path))
        path_image_component: Any | None = None
        if image_exists:
            image_chain = self._build_outbound_chain("", image_path)
            path_image_component = next(
                (component for component in image_chain if isinstance(component, Image)),
                None,
            )
            image_exists = path_image_component is not None

        leading_components: list[Any] = []
        trailing_components: list[Any] = []
        for component in outbound_components:
            if component_kind(component) in {"voice", "at", "reply"}:
                leading_components.append(component)
            else:
                trailing_components.append(component)

        source_chain: list[Any] = list(leading_components)
        if segments:
            source_chain.append(Plain(text))
        source_chain.extend(trailing_components)
        if path_image_component is not None:
            source_chain.append(path_image_component)
        if quote_message_id:
            source_chain = self._with_optional_reply(source_chain, quote_message_id)

        strategies = component_strategies_from_owner(self)
        strategies["reaction"] = (
            "inline" if media_delivery_mode == "same_message" else "separate"
        )
        chunks, _changed, _split_changed, _full_text = plan_component_chunks(
            source_chain,
            plain_type=Plain,
            split_text=lambda _value: list(segments),
            strategies=strategies,
            component_order=component_order_from_owner(self),
            classify=component_kind,
        )

        primary_components: list[Plain] = []
        for chunk in chunks:
            for component_index, component in enumerate(chunk):
                if not isinstance(component, Plain) or len(primary_components) >= len(segments):
                    continue
                segment_index = len(primary_components)
                segment_component = self._proactive_plain_segment_component(
                    segments[segment_index],
                    full_text=text,
                    index=segment_index,
                    count=len(segments),
                    suppress_tts=has_prebuilt_voice,
                )
                try:
                    object.__setattr__(
                        segment_component,
                        "_private_companion_proactive_primary_text",
                        True,
                    )
                except Exception:
                    pass
                chunk[component_index] = segment_component
                primary_components.append(segment_component)

        has_media = bool(outbound_components or image_exists)
        if has_media:
            logger.info(
                "主动媒体已按组件策略规划: text_segments=%s chunks=%s image=%s extra_components=%s strategies=%s",
                len(segments),
                len(chunks),
                image_exists,
                len(outbound_components),
                strategies,
            )
        if not chunks:
            complete = False
            return outcome(note="主动正文与媒体均为空")

        remaining_extra_components = list(outbound_components)
        delivered_primary_count = 0

        def chunk_primary_texts(chunk: list[Any]) -> list[str]:
            return [
                str(getattr(component, "text", "") or "").strip()
                for component in chunk
                if isinstance(component, Plain)
                and bool(
                    getattr(
                        component,
                        "_private_companion_proactive_primary_text",
                        False,
                    )
                )
                and str(getattr(component, "text", "") or "").strip()
            ]

        for chunk_index, chunk in enumerate(chunks):
            primary_texts = chunk_primary_texts(chunk)
            chunk_has_reaction = any(
                component_kind(component) == "reaction" for component in chunk
            )
            primary_complete = bool(
                segments and delivered_primary_count >= len(segments)
            )
            if (
                require_complete_text_before_media
                and chunk_has_reaction
                and not primary_complete
            ):
                complete = False
                return outcome(note="主动正文未完整送达，已跳过表情图片")

            recalled_message_id = self._should_cancel_reply_for_recalled_message_ids(
                trigger_message_id
            )
            if recalled_message_id:
                logger.info(
                    "触发消息已撤回，停止主动组件发送: umo=%s message_id=%s chunk=%s/%s",
                    umo,
                    recalled_message_id,
                    chunk_index + 1,
                    len(chunks),
                )
                complete = False
                return outcome(note=f"第 {chunk_index + 1} 条发送前触发消息已撤回")

            try:
                sent = await self._send_chain_components(umo, chunk)
            except Exception as exc:
                has_delivered_content = bool(
                    delivered_segments or image_delivered or extra_components_delivered
                )
                has_future_primary = any(
                    chunk_primary_texts(candidate)
                    for candidate in chunks[chunk_index + 1 :]
                )
                if not primary_texts and has_future_primary:
                    complete = False
                    failure_note = failure_note or (
                        f"第 {chunk_index + 1} 条组件发送失败：{_single_line(exc, 160)}"
                    )
                    logger.warning(
                        "主动前置组件发送失败，继续发送正文: umo=%s chunk=%s error=%s",
                        _single_line(umo, 140),
                        chunk_index + 1,
                        _single_line(exc, 180),
                    )
                    continue
                if not has_delivered_content:
                    raise
                complete = False
                logger.warning(
                    "主动组件部分送达后后续发送失败，不再整条重试: umo=%s chunk=%s error=%s",
                    _single_line(umo, 140),
                    chunk_index + 1,
                    _single_line(exc, 180),
                )
                return outcome(
                    note=f"第 {chunk_index + 1} 条发送失败：{_single_line(exc, 160)}"
                )

            if not sent:
                complete = False
                failure_note = failure_note or f"第 {chunk_index + 1} 条未被平台接受"
                continue

            if primary_texts:
                delivered_segments.extend(primary_texts)
                delivered_primary_count += len(primary_texts)
            if path_image_component is not None and any(
                component is path_image_component for component in chunk
            ):
                image_delivered = True
            for sent_component in chunk:
                matched_index = next(
                    (
                        index
                        for index, candidate in enumerate(remaining_extra_components)
                        if sent_component is candidate
                    ),
                    -1,
                )
                if matched_index >= 0:
                    remaining_extra_components.pop(matched_index)
                    extra_components_delivered += 1

            primary_complete = bool(
                segments and delivered_primary_count >= len(segments)
            )
            if primary_texts and any(
                chunk_primary_texts(candidate)
                for candidate in chunks[chunk_index + 1 :]
            ):
                await asyncio.sleep(
                    await self._calc_segmented_proactive_interval(primary_texts[-1], umo=umo)
                )

        primary_complete = bool(
            segments and delivered_primary_count >= len(segments)
        )
        return outcome()

    @staticmethod
    def _normalize_reaction_expression_delivery_mode(value: Any) -> str:
        mode = str(value or "separate_after").strip().lower().replace("-", "_")
        aliases = {
            "after": "separate_after",
            "separate": "separate_after",
            "separate_after_text": "separate_after",
            "inline": "same_message",
            "current_chain": "same_message",
            "same_chain": "same_message",
            "before": "separate_before",
            "separate_before_text": "separate_before",
        }
        normalized = aliases.get(mode, mode)
        if normalized in {"separate_after", "same_message", "separate_before"}:
            return normalized
        return "separate_after"

    def _build_proactive_reaction_event(
        self,
        *,
        umo: str,
        user_id: str,
        visible_text: str,
    ) -> Any:
        extras: dict[str, Any] = {}
        event = SimpleNamespace(
            unified_msg_origin=umo,
            message_str=visible_text,
            extras=extras,
        )
        event.get_sender_id = lambda: user_id
        event.get_message_str = lambda: visible_text
        event.is_private_chat = lambda: True
        event.get_extra = lambda key: extras.get(key)
        event.set_extra = lambda key, value: extras.__setitem__(key, value)
        return event

    async def _prepare_proactive_reaction_attachment(
        self,
        umo: str,
        visible_text: str,
    ) -> tuple[Any | None, dict[str, Any] | None]:
        entry = self._pop_proactive_reaction_intent(umo)
        intent = entry.get("intent") if isinstance(entry.get("intent"), dict) else {}
        user_id = _single_line(entry.get("user_id"), 160)
        if (
            not intent
            or not user_id
            or not self._proactive_reaction_expression_enabled("message")
        ):
            return None, None
        sticker_only = self._proactive_reaction_intent_allows_sticker_only(intent)
        visible_checker = getattr(self, "_reaction_expression_has_visible_text", None)
        if callable(visible_checker) and not visible_checker(visible_text) and not sticker_only:
            return None, None

        event = self._build_proactive_reaction_event(
            umo=_single_line(umo, 240),
            user_id=user_id,
            visible_text=str(visible_text or ""),
        )
        preauthorize = getattr(self, "_preauthorize_reaction_expression_prompt", None)
        prepare = getattr(self, "_pc_reaction_expression_impl", None)
        settle = getattr(self, "_settle_reaction_expression_attachment_data", None)
        if not callable(preauthorize) or not callable(prepare) or not callable(settle):
            return None, None
        try:
            if not await preauthorize(event):
                return None, None
            raw_prepared = await prepare(
                event,
                query=_single_line(intent.get("provider_query"), 500),
                context=_single_line(intent.get("context"), 1000)
                or _single_line(visible_text, 700),
                meme_only=True,
                send=True,
                purpose=_single_line(intent.get("purpose"), 120),
                emotion=_single_line(intent.get("emotion"), 80),
                intensity=_safe_int(intent.get("intensity"), 0, 0, 5),
                candidate_queries=intent.get("candidate_queries", []),
                attach_only=True,
            )
            prepared = json.loads(raw_prepared)
        except Exception as exc:
            pending = getattr(
                event,
                "_private_companion_reaction_expression_pending_attachment",
                None,
            )
            if isinstance(pending, dict):
                await settle(pending, sent=False, reason="attachment_prepare_failed")
            logger.warning(
                "主动表情附件准备失败,继续发送纯文字: error_type=%s",
                type(exc).__name__,
            )
            return None, None
        if not isinstance(prepared, dict) or prepared.get("decision") != "attach":
            return None, None

        pending = getattr(
            event,
            "_private_companion_reaction_expression_pending_attachment",
            None,
        )
        image_path = _path_text(prepared.get("path"), 1000)
        if not isinstance(pending, dict) or not image_path or not os.path.isfile(image_path):
            if isinstance(pending, dict):
                await settle(pending, sent=False, reason="attachment_file_missing")
            return None, None
        pending["sticker_only"] = sticker_only
        try:
            builder = getattr(self, "_build_reaction_image_component", None)
            if callable(builder):
                image_component = builder(event, image_path)
            else:
                try:
                    image_component = _host_Image().fromFileSystem(image_path)
                except AttributeError:
                    image_component = _host_Image().from_file_system(image_path)
        except Exception as exc:
            await settle(pending, sent=False, reason="attachment_component_failed")
            logger.warning(
                "主动表情图片组件构建失败,继续发送纯文字: error_type=%s",
                type(exc).__name__,
            )
            return None, None

        pending["attached"] = True
        pending["component"] = image_component
        runtime_logger = getattr(self, "_log_reaction_expression_event", None)
        if callable(runtime_logger):
            runtime_logger(
                event,
                stage="attachment",
                decision="accepted",
                reason="attachment_appended",
                scope="private",
                found=True,
                sent=False,
                image_id=prepared.get("image_id"),
                confidence=prepared.get("confidence"),
                cache_hit=prepared.get("cache_hit"),
                latency_ms=prepared.get("lookup_latency_ms"),
                match_basis=pending.get("match_basis"),
            )
        return image_component, pending

    async def _settle_proactive_reaction_attachment(
        self,
        pending: dict[str, Any] | None,
        *,
        sent: bool,
        reason: str,
    ) -> None:
        if not isinstance(pending, dict):
            return
        settle = getattr(self, "_settle_reaction_expression_attachment_data", None)
        if not callable(settle):
            return
        try:
            await settle(pending, sent=sent, reason=reason)
        except Exception as exc:
            # Delivery state is authoritative. A bookkeeping failure must not
            # make the caller retry content that the platform already received.
            logger.warning(
                "主动表情发送结算失败,不改变消息投递结果: "
                "sent=%s reason=%s error_type=%s",
                bool(sent),
                _single_line(reason, 80),
                type(exc).__name__,
            )

    async def _proactive_persona_delivery_allowed(self, target_umo: str) -> bool:
        """Revalidate the scheduled persona immediately before platform I/O."""
        validator = getattr(self, "_validate_proactive_persona_delivery", None)
        if not callable(validator):
            return True

        multi_persona = bool(getattr(self, "enable_multi_persona_mode", False))
        active_getter = getattr(self, "_active_persona_scope", None)
        scheduled_persona_id = ""
        if callable(active_getter):
            try:
                scheduled_persona_id = str(active_getter() or "").strip()
            except Exception:
                scheduled_persona_id = ""
        if not multi_persona and not scheduled_persona_id:
            effective_getter = getattr(self, "_effective_plugin_persona_id", None)
            if callable(effective_getter):
                try:
                    scheduled_persona_id = str(effective_getter() or "").strip()
                except Exception:
                    scheduled_persona_id = ""
        if not multi_persona and not scheduled_persona_id:
            primary_getter = getattr(self, "_primary_persona_id", None)
            try:
                scheduled_persona_id = str(
                    primary_getter()
                    if callable(primary_getter)
                    else getattr(self, "plugin_specific_persona_id", "")
                ).strip()
            except Exception:
                scheduled_persona_id = ""

        try:
            result = validator(target_umo, scheduled_persona_id)
            if inspect.isawaitable(result):
                result = await result
            allowed = bool(result.get("ok")) if isinstance(result, dict) else bool(result)
        except Exception as exc:
            logger.warning(
                "主动消息最终人格一致性校验失败: multi=%s persona=%s umo=%s error=%s",
                multi_persona,
                _single_line(scheduled_persona_id, 96) or "-",
                _single_line(target_umo, 140) or "-",
                _single_line(exc, 160),
            )
            return not multi_persona

        if allowed:
            return True
        reason_code = _single_line(result.get("reason_code"), 80) if isinstance(result, dict) else ""
        action = _single_line(result.get("action"), 40) if isinstance(result, dict) else ""
        logger.warning(
            "主动投递因人格不一致被取消: multi=%s persona=%s umo=%s action=%s reason=%s",
            multi_persona,
            _single_line(scheduled_persona_id, 96) or "-",
            _single_line(target_umo, 140) or "-",
            action or "blocked",
            reason_code or "validator_rejected",
        )
        return False

    @collect_proactive_delivery
    async def _send_proactive_message_chain(
        self,
        umo: str,
        text: str,
        image_path: str = "",
        *,
        extra_components: list[Any] | None = None,
        quote_message_id: str = "",
        disable_segmenting: bool = False,
    ) -> _ProactiveSendOutcome:
        if not await self._proactive_persona_delivery_allowed(umo):
            return _ProactiveSendOutcome(False, False, note="主动投递人格已变化，已取消发送")
        # Recheck at the final delivery boundary. A realtime call may start
        # after a proactive candidate was planned but before it is sent.
        busy_context_getter = getattr(self, "_busy_reply_proactive_block_context", None)
        if callable(busy_context_getter):
            try:
                busy_context = busy_context_getter({}, now=_now_ts())
            except TypeError:
                busy_context = busy_context_getter({}, now=_now_ts(), umo=umo)
            except Exception:
                busy_context = {}
            if isinstance(busy_context, dict) and busy_context.get("kind") == "external_realtime":
                logger.info(
                    "实时共同活动期间在最终发送边界取消主动消息: umo=%s",
                    _single_line(umo, 140),
                )
                return _ProactiveSendOutcome(False, False, note="实时共同活动期间已取消主动消息")
        trigger_message_id = _single_line(quote_message_id, 120)
        placeholder_cleaner = getattr(self, "_sanitize_orphan_tts_placeholders", None)
        if callable(placeholder_cleaner):
            cleaned_text = placeholder_cleaner(text)
            if cleaned_text != text:
                logger.warning(
                    "主动发送前清理孤儿 TTS 占位符: umo=%s before=%s after=%s",
                    _single_line(umo, 120),
                    _single_line(text, 120),
                    _single_line(cleaned_text, 120),
                )
                text = cleaned_text
        reaction_pending: dict[str, Any] | None = None
        reaction_delivery_mode = self._normalize_reaction_expression_delivery_mode(
            runtime_persona_setting(self, "reaction_expression_delivery_mode", "separate_after")
        )
        has_existing_media = bool(
            image_path
            or extra_components
            or (text and self._contains_inline_image_tag(text))
        )
        if has_existing_media:
            self._clear_proactive_reaction_intent(umo)
        else:
            reaction_component, reaction_pending = await self._prepare_proactive_reaction_attachment(
                umo,
                text,
            )
            if reaction_component is not None:
                try:
                    object.__setattr__(
                        reaction_component,
                        "_private_companion_reaction_expression",
                        True,
                    )
                except Exception:
                    pass
                if isinstance(reaction_pending, dict) and reaction_pending.get("sticker_only"):
                    try:
                        reaction_sent = bool(
                            await self._send_chain_components(
                                umo,
                                [reaction_component],
                            )
                        )
                    except Exception as exc:
                        reaction_sent = False
                        logger.warning(
                            "主动纯表情投递失败: umo=%s error_type=%s",
                            _single_line(umo, 140),
                            type(exc).__name__,
                        )
                    await self._settle_proactive_reaction_attachment(
                        reaction_pending,
                        sent=reaction_sent,
                        reason="delivered" if reaction_sent else "delivery_failed",
                    )
                    return _ProactiveSendOutcome(
                        delivered=reaction_sent,
                        complete=reaction_sent,
                        extra_components_delivered=1 if reaction_sent else 0,
                        note="" if reaction_sent else "主动纯表情未送达",
                    )
                if isinstance(reaction_pending, dict):
                    reaction_pending["delivery_mode"] = reaction_delivery_mode
                if reaction_delivery_mode == "separate_before":
                    try:
                        reaction_sent = bool(
                            await self._send_chain_components(
                                umo,
                                [reaction_component],
                            )
                        )
                    except Exception as exc:
                        reaction_sent = False
                        logger.warning(
                            "主动表情先行发送失败，继续发送正文: "
                            "umo=%s error_type=%s",
                            _single_line(umo, 140),
                            type(exc).__name__,
                        )
                    await self._settle_proactive_reaction_attachment(
                        reaction_pending,
                        sent=reaction_sent,
                        reason="delivered" if reaction_sent else "delivery_failed",
                    )
                    reaction_pending = None
                else:
                    extra_components = [reaction_component]
        if has_existing_media or image_path or extra_components:
            try:
                outcome = await self._send_media_proactive_chain(
                    umo,
                    text,
                    image_path,
                    extra_components=extra_components,
                    quote_message_id=quote_message_id,
                    disable_segmenting=disable_segmenting,
                    media_delivery_mode=(
                        reaction_delivery_mode
                        if reaction_pending is not None
                        else "separate_after"
                    ),
                    require_complete_text_before_media=bool(
                        reaction_pending is not None
                        and reaction_delivery_mode == "separate_after"
                    ),
                )
            except Exception:
                await self._settle_proactive_reaction_attachment(
                    reaction_pending,
                    sent=False,
                    reason=(
                        "primary_not_delivered"
                        if reaction_pending is not None
                        and reaction_delivery_mode == "separate_after"
                        else "delivery_failed"
                    ),
                )
                raise
            if reaction_pending is not None:
                reaction_sent = bool(outcome.extra_components_delivered)
                settlement_reason = (
                    "delivered"
                    if reaction_sent
                    else "primary_not_delivered"
                    if reaction_delivery_mode == "separate_after"
                    and not outcome.primary_complete
                    else "delivery_failed"
                )
                await self._settle_proactive_reaction_attachment(
                    reaction_pending,
                    sent=reaction_sent,
                    reason=settlement_reason,
                )
            return outcome
        if text:
            await self._maybe_send_input_status(umo, text)
        splitter = getattr(self, "_split_llm_controlled_text_for_event", None)
        if (
            callable(splitter)
            and bool(runtime_persona_setting(self, "enable_llm_controlled_segmenting", False))
            and not disable_segmenting
            and self._segmented_platform_allows(umo=umo)
            and self._segmented_scope_allows_umo(umo)
        ):
            segments = splitter(None, text, umo=umo)
        else:
            segments = self._split_proactive_text(
                text,
                umo=umo,
                image_path="",
                extra_components=None,
                disable_segmenting=(
                    disable_segmenting
                    or not self._segmented_platform_allows(umo=umo)
                    or not self._segmented_scope_allows_umo(umo)
                ),
            )
        if len(segments) > 1:
            logger.info(
                "主动文本已分段: umo=%s segments=%s lengths=%s",
                _single_line(umo, 140),
                len(segments),
                [len(segment) for segment in segments],
            )
        if len(segments) <= 1:
            outbound_text = segments[0] if segments else text
            if not str(outbound_text or "").strip():
                return _ProactiveSendOutcome(False, False, note="主动正文为空")
            if quote_message_id and self._quote_skip_reason_for_short_reply(outbound_text):
                quote_message_id = ""
            recalled_message_id = self._should_cancel_reply_for_recalled_message_ids(trigger_message_id)
            if recalled_message_id:
                logger.info("触发消息已撤回，取消主动消息发送: umo=%s message_id=%s", umo, recalled_message_id)
                return _ProactiveSendOutcome(False, False, note="触发消息已撤回")
            sent = await self._send_chain_components(
                umo,
                self._with_optional_reply(
                    [
                        self._proactive_plain_segment_component(outbound_text, full_text=text, index=0, count=1)
                    ],
                    quote_message_id,
                ),
            )
            return _ProactiveSendOutcome(
                delivered=bool(sent),
                complete=bool(sent),
                delivered_text=outbound_text if sent else "",
                note="" if sent else "主动发送组件被取消或清空",
            )
        recalled_message_id = self._should_cancel_reply_for_recalled_message_ids(trigger_message_id)
        if recalled_message_id:
            logger.info("触发消息已撤回，取消主动合并分段发送: umo=%s message_id=%s", umo, recalled_message_id)
            return _ProactiveSendOutcome(False, False, note="触发消息已撤回")
        if await self._send_segmented_proactive_forward_message(umo, segments, source="proactive_text"):
            return _ProactiveSendOutcome(True, True, delivered_text="\n".join(segments).strip())
        delivered_segments: list[str] = []
        complete = True
        for index, segment in enumerate(segments):
            if index == 0 and quote_message_id and self._quote_skip_reason_for_short_reply(segment):
                quote_message_id = ""
            recalled_message_id = self._should_cancel_reply_for_recalled_message_ids(trigger_message_id)
            if recalled_message_id:
                logger.info("触发消息已撤回，停止主动消息分段发送: umo=%s message_id=%s index=%s", umo, recalled_message_id, index + 1)
                return _ProactiveSendOutcome(
                    bool(delivered_segments),
                    False,
                    delivered_text="\n".join(delivered_segments).strip(),
                    note=f"第 {index + 1} 段发送前触发消息已撤回",
                )
            segment_comp = self._proactive_plain_segment_component(segment, full_text=text, index=index, count=len(segments))
            chain = self._with_optional_reply([segment_comp], quote_message_id) if index == 0 else [segment_comp]
            try:
                sent = await self._send_chain_components(umo, chain)
            except Exception as exc:
                if not delivered_segments:
                    raise
                logger.warning(
                    "主动文本部分送达后后续分段失败，不再整条重试: umo=%s index=%s error=%s",
                    _single_line(umo, 140),
                    index + 1,
                    _single_line(exc, 180),
                )
                return _ProactiveSendOutcome(
                    True,
                    False,
                    delivered_text="\n".join(delivered_segments).strip(),
                    note=f"第 {index + 1} 段发送失败：{_single_line(exc, 160)}",
                )
            if sent:
                delivered_segments.append(segment)
            else:
                complete = False
            quote_message_id = ""
            if index < len(segments) - 1:
                try:
                    interval = await self._calc_segmented_proactive_interval(segment, umo=umo)
                except TypeError:
                    interval = await self._calc_segmented_proactive_interval(segment)
                await asyncio.sleep(interval)
        delivered_text = "\n".join(delivered_segments).strip()
        return _ProactiveSendOutcome(
            delivered=bool(delivered_text),
            complete=bool(delivered_text and complete),
            delivered_text=delivered_text,
            note="" if complete else "部分分段被发送钩子取消或清空",
        )

    def _build_outbound_result(
        self,
        text: str,
        image_path: str = "",
        extra_components: list[Any] | None = None,
    ) -> Any:
        chain = self._build_outbound_chain(text, image_path, extra_components=extra_components)
        return self._build_result_from_chain(chain)

    def _build_proactive_archive_user_prompt(
        self,
        *,
        reason: str,
        action: str,
        motive: str = "",
        action_summary: str = "",
    ) -> str:
        return ""

    @staticmethod
    def _proactive_component_is_image(component: Any) -> bool:
        return isinstance(component, Image) or bool(
            getattr(component, "_private_companion_reaction_expression", False)
        )

    @staticmethod
    def _proactive_components_contain_image(components: list[Any] | None) -> bool:
        return any(
            ProactiveMessageOutboundDeliveryMixin._proactive_component_is_image(component)
            for component in (components or [])
        )

    def _build_actual_proactive_delivery_summary(
        self,
        *,
        text: str,
        image_path: str = "",
        extra_components: list[Any] | None = None,
        original_summary: str = "",
    ) -> str:
        parts: list[str] = []
        visible_text = self._visible_text_without_tts_reading(text, limit=320)
        if visible_text:
            parts.append(f"文字消息：{visible_text}")

        image_count = int(bool(image_path)) + sum(
            1
            for component in (extra_components or [])
            if self._proactive_component_is_image(component)
        )
        if image_count:
            photo_caption = ""
            if "：" in str(original_summary or "") or ":" in str(original_summary or ""):
                photo_caption = _single_line(
                    re.split(r"[:：]", str(original_summary), maxsplit=1)[-1],
                    220,
                )
            image_label = "图片" if image_count == 1 else f"{image_count} 张图片"
            if photo_caption and photo_caption not in {"发图", "图片", "photo_text"}:
                parts.append(f"{image_label}：{photo_caption}")
            else:
                parts.append(f"{image_label}已发送")

        voice_count = sum(
            1 for component in (extra_components or []) if isinstance(component, Record)
        )
        if voice_count:
            parts.append("语音消息已发送" if voice_count == 1 else f"{voice_count} 条语音消息已发送")

        other_count = sum(
            1
            for component in (extra_components or [])
            if not self._proactive_component_is_image(component)
            and not isinstance(component, Record)
        )
        if other_count:
            parts.append(f"{other_count} 个附加消息组件已发送")
        return _single_line("；".join(parts), 500)

    def _reconcile_proactive_delivery_metadata(
        self,
        *,
        text: str,
        image_path: str = "",
        extra_components: list[Any] | None = None,
        action: str = "message",
        action_summary: str = "",
        delivery_complete: bool = True,
    ) -> tuple[str, str, bool]:
        delivered_photo = bool(image_path) or self._proactive_components_contain_image(extra_components)
        if delivery_complete:
            return action or "message", action_summary, delivered_photo

        action_parts = [part.strip() for part in str(action or "").split("+") if part.strip()]
        removed_media = False
        if not delivered_photo and "photo_text" in action_parts:
            action_parts = [part for part in action_parts if part != "photo_text"]
            removed_media = True
        delivered_voice = any(isinstance(component, Record) for component in (extra_components or []))
        if not delivered_voice and "voice" in action_parts:
            action_parts = [part for part in action_parts if part != "voice"]
            removed_media = True
        if removed_media and text and "message" not in action_parts:
            action_parts.insert(0, "message")
        actual_action = "+".join(action_parts) or ("message" if text else action or "message")
        actual_summary = self._build_actual_proactive_delivery_summary(
            text=text,
            image_path=image_path,
            extra_components=extra_components,
            original_summary=action_summary,
        )
        return actual_action, actual_summary or "主动消息仅部分送达。", delivered_photo

    def _build_proactive_archive_assistant_text(
        self,
        *,
        text: str,
        image_path: str = "",
        extra_components: list[Any] | None = None,
        action_summary: str = "",
        photo_subject_owner: str = "",
    ) -> str:
        original_is_receipt = self._is_proactive_delivery_receipt_text(text)
        message_text = sanitize_llm_segment_control_tokens(
            self._visible_text_without_tts_reading(text, limit=1000)
        )
        attachment_notes: list[str] = []
        history_image_count = 0
        history_record_count = 0
        if image_path:
            history_image_count += 1
            photo_caption = ""
            if "：" in str(action_summary or "") or ":" in str(action_summary or ""):
                photo_caption = _single_line(re.split(r"[:：]", str(action_summary), maxsplit=1)[-1], 220)
            if photo_caption and photo_caption not in {"发图", "图片", "photo_text"}:
                attachment_notes.append(f"图片画面：{photo_caption}")
            normalized_owner = _normalize_photo_subject_owner(photo_subject_owner)
            if normalized_owner:
                attachment_notes.append(f"图片主体：{_photo_subject_owner_prompt_label(normalized_owner)}")
        if extra_components:
            tts_notes: list[str] = []
            note_builder = getattr(self, "_tts_component_log_note", None)
            image_components = [
                comp
                for comp in extra_components
                if self._proactive_component_is_image(comp)
            ]
            for comp in extra_components:
                if isinstance(comp, Record) and callable(note_builder):
                    note = _single_line(note_builder(comp), 220)
                    if note:
                        tts_notes.append(note)
            if image_components:
                history_image_count += len(image_components)
                photo_caption = ""
                if "：" in str(action_summary or "") or ":" in str(action_summary or ""):
                    photo_caption = _single_line(re.split(r"[:：]", str(action_summary), maxsplit=1)[-1], 220)
                if photo_caption and photo_caption not in {"发图", "图片", "photo_text"}:
                    attachment_notes.append(f"图片画面：{photo_caption}")
                normalized_owner = _normalize_photo_subject_owner(photo_subject_owner)
                if normalized_owner:
                    attachment_notes.append(f"图片主体：{_photo_subject_owner_prompt_label(normalized_owner)}")
            if tts_notes:
                attachment_notes.extend(tts_notes[:3])
            record_count = sum(1 for comp in extra_components if isinstance(comp, Record))
            history_record_count += record_count
            other_count = len(extra_components) - len(image_components) - record_count
            if other_count > 0:
                attachment_notes.append(f"随消息发送了 {other_count} 个附加消息组件")
        if attachment_notes:
            suffix = "（" + ",".join(attachment_notes) + "）"
            message_text = f"{message_text}{suffix}" if message_text else suffix
        media_marker = _format_history_media_marker(
            images=history_image_count,
            records=history_record_count,
        )
        if media_marker:
            message_text = f"{message_text}\n{media_marker}" if message_text else media_marker
        if message_text:
            return message_text
        if original_is_receipt:
            return ""
        return _single_line(action_summary, 160) or "主动向用户发送了一条消息。"

    async def _archive_proactive_message_to_conversation(
        self,
        *,
        user: dict[str, Any],
        user_prompt: str,
        assistant_response: str,
        umo: str = "",
    ) -> bool:
        umo = str(umo or user.get("umo") or "").strip()
        if not umo or not assistant_response:
            return False
        visible_assistant_response = sanitize_llm_segment_control_tokens(
            _strip_outbound_control_blocks(
                assistant_response,
                enabled=bool(runtime_persona_setting(self, "enable_framework_error_leak_guard", True)),
                tts_enabled=bool(runtime_persona_setting(self, "enable_tts_enhancement", False)),
            )
        )
        if not visible_assistant_response:
            return False
        for attempt in range(4):
            try:
                safe_user_prompt = str(user_prompt or "").strip()
                archive_context_only = not safe_user_prompt or self._proactive_archive_context_text(
                    safe_user_prompt
                )
                assistant_msg_obj = AssistantMessageSegment(content=visible_assistant_response)

                async def _write():
                    conv_id = await self._ensure_conversation_id_for_umo(umo, title="Private Companion 主动消息")
                    if not conv_id:
                        return False
                    conversation_manager = self.context.conversation_manager
                    if archive_context_only:
                        conversation = await conversation_manager.get_conversation(umo, conv_id)
                        if conversation is None:
                            return False
                        raw_history = getattr(conversation, "history", "[]")
                        if isinstance(raw_history, str):
                            history = json.loads(raw_history or "[]")
                        elif isinstance(raw_history, list):
                            history = list(raw_history)
                        else:
                            history = []
                        history.append(assistant_msg_obj.model_dump())
                        await conversation_manager.update_conversation(umo, conv_id, history=history)
                    else:
                        await conversation_manager.add_message_pair(
                            cid=conv_id,
                            user_message=UserMessageSegment(content=safe_user_prompt),
                            assistant_message=assistant_msg_obj,
                        )
                    return True

                written = await self._conversation_db_operation("archive_proactive_message", _write)
                if not written:
                    logger.warning("主动消息存档失败: 无法获取或创建 AstrBot 会话 history umo=%s", _single_line(umo, 140))
                    return False
                if attempt > 0:
                    logger.info("主动消息写入 AstrBot 会话历史成功: %s retry=%s", umo, attempt)
                else:
                    logger.info("已将主动消息写入 AstrBot 会话历史: %s", umo)
                return True
            except Exception as e:
                text = str(e or "").lower()
                if ("database is locked" in text or "sqlite3.operationalerror" in text) and attempt < 3:
                    await asyncio.sleep(0.25 * (attempt + 1))
                    continue
                logger.warning("主动消息写入会话历史失败: %s", e)
                return False
        return False

    def _format_story_plan_for_prompt(self) -> str:
        plan = self.data.get("daily_story_plan", {})
        if not isinstance(plan, dict) or plan.get("date") != _today_key():
            return "（暂无）"
        lines = []
        now_minutes = self._environment_now_minutes()
        events = plan.get("today_events", [])
        if isinstance(events, list) and events:
            nearby_events = [
                item for item in events
                if isinstance(item, dict) and self._story_item_relevant_to_now(item, now_minutes)
            ][:6]
            if nearby_events:
                lines.append("附近可能发生：")
                for item in nearby_events:
                    lines.append(f"- {item.get('window', '')}｜{item.get('event', '')}｜{item.get('mood', '')}")
        proactive = plan.get("proactive_events", [])
        if isinstance(proactive, list) and proactive:
            nearby_proactive = [
                item for item in proactive
                if isinstance(item, dict) and self._story_item_relevant_to_now(item, now_minutes, future_minutes=240)
            ][:6]
            if nearby_proactive:
                lines.append("附近主动计划：")
                for item in nearby_proactive:
                    lines.append(
                        f"- {item.get('window', '')}｜{item.get('reason', '')}｜{item.get('action', 'message')}｜"
                        f"{item.get('why', '')}｜{item.get('topic', '')}｜{item.get('motive', '')}｜"
                        f"{item.get('scene', '')}｜{item.get('tone', '')}｜{item.get('impulse', '')}"
                    )
        long_term = plan.get("long_term_events", [])
        if isinstance(long_term, list) and long_term:
            lines.append("长线事件：")
            for item in long_term[:4]:
                if isinstance(item, dict):
                    lines.append(
                        f"- {item.get('title', '')}｜{item.get('status', '')}｜"
                        f"{item.get('tendency', '')}｜{item.get('next_hint', '')}"
                    )
        return "\n".join(lines) if lines else "（暂无）"

