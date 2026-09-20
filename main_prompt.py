# -*- coding: utf-8 -*-
"""LLM 请求提示词编排域。

由 tools/split_main_domain.py 从 main.py 机械抽取（41 个方法 / 1794 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPlugin）。
"""
from __future__ import annotations

import asyncio
import re
import time
from .conversation_injection_plan import (
    DELIVERY_GROUP_MARKER_METADATA_KEY,
    PLACEMENT_DYNAMIC_SYSTEM,
    PLACEMENT_STABLE_SYSTEM,
    PLACEMENT_TURN_TAIL,
    get_conversation_injection_plan,
)
from .conversation_prompt_section import PromptRenderMode, PromptSection, prompt_cdata, prompt_section, render_prompt_sections
from .helpers import _now_ts, _safe_float, _safe_int, _single_line
from .main_shared import _PROACTIVE_ONLY_TEMP_UNLOCK_ALIASES, _multi_persona_event_context
from .persona_config import runtime_persona_setting
from .private_scope_isolation import sanitize_private_request_group_artifacts
from .prompt_surface import CollectedPromptContext
from .segmented_message import LLM_SEGMENT_MARKER, sanitize_llm_segment_control_tokens
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest
from typing import Any, Iterable

from .logging_util import get_module_logger

logger = get_module_logger(__name__)

class PrivateCompanionPluginPromptMixin:
    """LLM 请求提示词编排域（从 PrivateCompanionPlugin 拆出）。"""

    def _format_body_monitor_health_prompt_section(
        self,
        user: dict[str, Any],
        *,
        reason: str = "",
    ) -> PromptSection | None:
        integration = getattr(self, "_body_monitor_integration", None)
        if integration is None:
            return None
        builder = getattr(integration, "format_health_prompt_section", None)
        return builder(user, reason=reason) if callable(builder) else None

    def _llm_controlled_segmenting_allowed(self, event: AstrMessageEvent | None = None) -> bool:
        """Return whether the current conversation may use LLM boundaries."""
        if not bool(runtime_persona_setting(self, "enable_segmented_proactive_reply", False)):
            return False
        if not bool(runtime_persona_setting(self, "enable_llm_controlled_segmenting", False)):
            return False
        scope = self._segmented_setting(
            "scope",
            event=event,
            default="proactive_only",
        )
        external_proactive = bool(
            event is not None
            and (
                bool(getattr(event, "private_companion_proactive_framework", False))
                or bool(getattr(event, "_private_companion_external_proactive_source", ""))
            )
        )
        if str(scope or "proactive_only").strip().lower() != "all_llm" and not external_proactive:
            return False
        try:
            if event is not None and not bool(self._segmented_scope_allows_event(event)):
                return False
            if event is not None and not bool(self._segmented_platform_allows(event=event)):
                return False
            return True
        except Exception:
            return False

    def _llm_controlled_segmenting_prompt(self) -> str:
        """Resolve the active persona's user-facing segmentation instruction."""
        custom = str(
            runtime_persona_setting(self, "llm_controlled_segmenting_prompt", "")
            or ""
        ).strip()[:4000]
        template = custom or PrivateCompanionPlugin._default_llm_controlled_segmenting_prompt()
        return re.sub(
            r"\{\{\s*split_marker\s*\}\}",
            lambda _match: LLM_SEGMENT_MARKER,
            template,
            flags=re.IGNORECASE,
        )

    def _llm_controlled_segmenting_prompt_section(self) -> PromptSection:
        return prompt_section(
            key="reply.segmentation",
            title="回复分段控制",
            source="segmented_reply",
            content=prompt_cdata(self._llm_controlled_segmenting_prompt()),
        )

    @filter.on_llm_request(priority=-253000)
    @_multi_persona_event_context
    async def inject_llm_controlled_segmenting_instruction(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        *args,
        **kwargs,
    ) -> None:
        """Tell only the main conversation model about the outbound marker."""
        if self is None or req is None or not bool(getattr(self, "enabled", False)):
            return
        if not self._llm_controlled_segmenting_allowed(event):
            return
        marker = "<!-- private_companion_reply_segmentation_v1 -->"
        if self._request_has_managed_prompt_marker(req, marker):
            return
        section = self._llm_controlled_segmenting_prompt_section()
        placement = "prompt" if self._append_turn_prompt_fragment_by_position(
            req,
            marker,
            section,
            priority=90,
        ) else "system_prompt"
        if placement == "system_prompt":
            self._materialize_conversation_system_block(
                req,
                section=section,
                marker=marker,
                priority=90,
                placement=PLACEMENT_DYNAMIC_SYSTEM,
            )

    async def _append_environment_perception_to_request(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
        marker = "<!-- private_companion_environment_v1 -->"
        current_prompt = req.system_prompt or ""
        current_turn_prompt = str(getattr(req, "prompt", "") or "")
        if marker in current_prompt or marker in current_turn_prompt:
            return
        environment_section = await self._format_environment_perception_prompt_section(event)
        environment_injection = str(environment_section.content or "")
        if environment_injection:
            placement = self._place_conversation_prompt_section(
                req,
                marker,
                environment_section,
                priority=30,
            )
            await self._record_request_prompt_fragment(
                event,
                title="请求级环境感知注入",
                key="environment.request",
                text=environment_injection,
                source="environment",
                metadata={"注入位置": placement},
            )

    def _format_persona_voice_channel_prompt_section(
        self,
        channel: str,
    ) -> PromptSection | None:
        if not bool(runtime_persona_setting(self, 'enable_persona_voice_channels', True)):
            return None
        channel = str(channel or "").strip().lower()
        specs = {
            "conversation": (
                "对话风格",
                "persona_conversation_voice_prompt",
                "只用于私聊/群聊里真正说出口的聊天回复。不要把创作腔、日程计划或内心分析写进外发消息；用户要求详细说明时可优先保证信息完整。",
            ),
            "creative": (
                "创作风格",
                "persona_creative_voice_prompt",
                "只用于日记、QQ 空间、私下创作、文案和公开动态。允许比聊天更完整,但仍应像角色本人写的,避免模型作文、升华总结和营销文案腔。",
            ),
            "planning": (
                "计划风格",
                "persona_planning_voice_prompt",
                "只影响日程、计划、候选排序和行动倾向。这里描述角色会怎样安排自己、被什么驱动、什么时候收住,不是最终聊天台词。",
            ),
            "inner": (
                "内心活动风格",
                "persona_inner_voice_prompt",
                "只用于内部动机、念头、犹豫和状态余波。它默认不可直接外发,不能泄露系统、插件、模型或自我分析过程。",
            ),
            "proactive": (
                "主动开口风格",
                "persona_proactive_voice_prompt",
                "只用于把主动动机改写成最终私聊/群聊开口。优先具体由头、低压力、短句和可接话落点；不要写成回复空气、任务汇报或询问是否继续。",
            ),
        }
        label, attr, note = specs.get(channel, ("表达风格", f"persona_{channel}_voice_prompt", "只在对应链路使用。"))
        text = self._normalize_persona_voice_text(
            runtime_persona_setting(self, attr, ""),
            max_chars=1400,
        )
        if not text:
            return None
        body = f"{text}\n使用边界：{note}"
        return prompt_section(
            key=f"persona.voice.{channel or 'default'}",
            title=f"人格标准化：{label}",
            source="persona_voice",
            content=body,
        )

    def _format_proactive_voice_prompt_sections(self) -> list[PromptSection]:
        sections: list[PromptSection] = []
        base = self._normalize_persona_voice_text(runtime_persona_setting(self, 'reply_style_prompt', ""), max_chars=900)
        if base:
            sections.append(
                prompt_section(
                    key="proactive.base_voice",
                    title="主动消息基础表达约束",
                    source="persona_voice",
                    content=(
                        f"{base}\n"
                        "这里只保留句数、口语化和简洁度等通用约束；不要把普通被动接话方式直接当成主动开口。"
                    ),
                )
            )
        proactive = self._format_persona_voice_channel_prompt_section("proactive")
        if proactive is not None:
            sections.append(proactive)
        conversation = self._format_persona_voice_channel_prompt_section("conversation")
        if conversation is not None and proactive is None:
            sections.append(
                prompt_section(
                    key=conversation.key,
                    title=conversation.title,
                    source=conversation.source,
                    content=(
                        f"{conversation.content}\n"
                        "补充边界：当前没有单独配置主动开口风格,因此只把对话风格作为轻量回退；仍必须围绕主动由头自然开口。"
                    ),
                    metadata=conversation.metadata,
                )
            )
        return sections

    def _format_reply_style_prompt_section(self) -> PromptSection:
        text = str(runtime_persona_setting(self, 'reply_style_prompt', "") or "").strip()
        persona_voice_section = self._format_persona_voice_channel_prompt_section("conversation")
        persona_voice = ""
        if persona_voice_section is not None:
            persona_voice = render_prompt_sections(
                [persona_voice_section],
                mode=PromptRenderMode.BODY_ONLY,
            )
        content = ""
        if text or persona_voice:
            text = self._normalize_persona_voice_text(text)
            parts: list[str] = []
            if text:
                parts.append(text)
            if persona_voice:
                parts.append(persona_voice)
            content = (
                "\n\n".join(parts)
                + "\n这些规则用于普通聊天的表达节奏；如果当前问题确实需要排障、教程、代码说明、复杂解释或用户明确要求详细说明，可以优先保证信息完整。"
                + "\n无论工具或模型返回什么内容，外发正文都不要照抄英文报错、内容策略提示、政策链接或内部诊断；遇到这类结果时，用当前人格的一句简短中文说明，再自然收住或邀请用户换一种说法。"
            )
        return prompt_section(
            key="reply.style",
            title="回复风格约束",
            source="reply_style",
            content=content,
        )

    @staticmethod
    def _format_technical_reasoning_prompt_section(
        event: AstrMessageEvent | None,
        req: ProviderRequest | None = None,
    ) -> PromptSection | None:
        text = "\n".join(
            part
            for part in (
                str(getattr(event, "message_str", "") or "").strip(),
                str(getattr(req, "prompt", "") or "").strip(),
            )
            if part
        )
        compact = re.sub(r"\s+", "", text).lower()
        if not compact:
            return None
        technical_markers = (
            "代码", "源码", "脚本", "python", "sleep(", "报错", "日志", "执行结果",
            "计算", "公式", "换算", "单位", "耗时", "延迟", "超时", "秒", "分钟", "小时",
        )
        if not any(marker in compact for marker in technical_markers):
            return None
        return prompt_section(
            key="reply.technical_accuracy",
            title="技术解释准确性",
            source="reply_style",
            content=(
                "解释代码、公式、日志耗时或单位换算时，先逐项读取用户给出的原表达式和原始数值，写清每个量的单位；"
                "先统一换算到同一种基本单位，再换算成用户需要的展示单位，并用一次反向换算复核。"
                "严格区分配置/代码要求的时长、程序实际运行耗时、日志记录值和界面格式化后的显示值，不要把它们当成同一个量。\n"
                "不得引入源码、日志或用户材料中没有出现的运算、常数、倍率、对数或所谓解释器规则来凑结果；"
                "尤其不能凭空加入 ln、log、指数或除法。如果结果与原表达式不一致，明确指出缺少哪段真实代码或日志，不要虚构原因。\n"
                "例如 `time.sleep(10 * 60)` 的参数是 600 秒，也就是 10 分钟；除非真实代码另有运算，不能解释成 4.35 分钟。"
            ),
        )

    async def _append_reply_style_to_request(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        *,
        mode: str = "passive",
        priority: int = 12,
    ) -> None:
        sections = [self._format_reply_style_prompt_section()]
        technical_section = self._format_technical_reasoning_prompt_section(
            event,
            req,
        )
        if technical_section is not None:
            sections.append(technical_section)
        combined_prompt = render_prompt_sections(sections)
        if not combined_prompt:
            return
        marker = "<!-- private_companion_reply_style_v1 -->"
        current_prompt = req.system_prompt or ""
        current_turn_prompt = str(getattr(req, "prompt", "") or "")
        if marker in current_prompt or marker in current_turn_prompt:
            return
        placement, _, _ = self._place_conversation_prompt_sections(
            req,
            marker,
            sections,
            priority=priority,
        )
        await self._record_request_prompt_fragment(
            event,
            title="回复风格约束",
            key="reply.style",
            text=combined_prompt,
            source="reply_style",
            mode=mode,
            metadata={"注入位置": placement},
        )

    async def _append_group_high_intensity_reply_guard_to_request(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
    ) -> None:
        guard_section = self._format_group_high_intensity_reply_guard_section(event)
        if guard_section is None:
            return
        guard_text = render_prompt_sections(
            [guard_section],
            mode=PromptRenderMode.BODY_ONLY,
        )
        marker = "<!-- private_companion_group_high_intensity_reply_guard_v1 -->"
        current_prompt = req.system_prompt or ""
        current_turn_prompt = str(getattr(req, "prompt", "") or "")
        if marker in current_prompt or marker in current_turn_prompt:
            return
        placement = self._place_conversation_prompt_section(
            req,
            marker,
            guard_section,
            priority=11,
        )
        await self._record_request_prompt_fragment(
            event,
            title="群聊高强度短回复护栏",
            key="group.high_intensity.reply_guard",
            text=guard_text,
            source="group_high_intensity",
            mode="group",
            priority=11,
            metadata={"注入位置": placement},
        )

    def _place_conversation_prompt_section(
        self,
        req: ProviderRequest,
        marker: str,
        section: PromptSection,
        *,
        priority: int = 50,
        force_dynamic: bool = False,
    ) -> str:
        """Place one authored section and render it exactly once through the plan."""

        if not isinstance(section, PromptSection):
            raise TypeError("conversation prompt placement requires PromptSection")
        position = self._normalize_passive_injection_position(
            runtime_persona_setting(self, "passive_injection_position", "prompt")
        )
        marker = _single_line(marker, 120) or "<!-- private_companion_turn_fragment -->"
        plan = get_conversation_injection_plan(req)
        if plan is None:
            raise RuntimeError("conversation injection plan is unavailable")
        if not plan.contains_marker(marker):
            use_system_prompt = position == "system_prompt" and not force_dynamic
            plan.add(
                section=section,
                marker=marker,
                priority=int(priority),
                placement=(
                    PLACEMENT_DYNAMIC_SYSTEM
                    if use_system_prompt
                    else PLACEMENT_TURN_TAIL
                ),
                materialized=False,
            )
        setattr(req, "_private_companion_turn_prompt_fragments", plan.turn_fragments())
        plan.render_into(req, prefer_extra_user_content=True)
        if position == "system_prompt" and not force_dynamic:
            return "system_prompt"
        return _single_line(
            getattr(req, "_private_companion_turn_prompt_placement", "prompt"),
            40,
        ) or "prompt"

    def _append_turn_prompt_fragment_by_position(
        self,
        req: ProviderRequest,
        marker: str,
        section: PromptSection,
        *,
        priority: int = 50,
        force_dynamic: bool = False,
    ) -> bool:
        if not isinstance(section, PromptSection):
            raise TypeError("turn prompt fragment requires PromptSection")
        if (
            section.content is None
            or (isinstance(section.content, str) and not section.content.strip())
        ) and not section.children:
            return False
        try:
            placement = self._place_conversation_prompt_section(
                req,
                marker,
                section,
                priority=priority,
                force_dynamic=force_dynamic,
            )
            return placement not in {"none", "system_prompt"}
        except Exception as exc:
            logger.debug("指定位置 prompt 注入失败,回退 system_prompt: %s", _single_line(exc, 120))
            return False

    @staticmethod
    def _request_has_managed_prompt_marker(req: ProviderRequest, marker: str) -> bool:
        """Only trust markers placed by the plugin, never raw user prompt text."""
        marker_text = _single_line(marker, 120)
        if not marker_text:
            return False
        plan = get_conversation_injection_plan(req, create=False)
        if plan is not None and plan.contains_marker(marker_text):
            return True
        if marker_text in str(getattr(req, "system_prompt", "") or ""):
            return True
        fragments = getattr(req, "_private_companion_turn_prompt_fragments", None)
        if isinstance(fragments, list) and any(
            isinstance(item, dict) and item.get("marker") == marker_text
            for item in fragments
        ):
            return True
        extra_parts = getattr(req, "extra_user_content_parts", None)
        if not isinstance(extra_parts, list):
            return False
        for part in extra_parts:
            if not bool(getattr(part, "_private_companion_turn_fragments", False)):
                continue
            text = str(getattr(part, "text", "") or getattr(part, "content", "") or "")
            if marker_text in text:
                return True
        return False

    def _request_prompt_context_surface(self, req: ProviderRequest) -> str:
        parts = [str(getattr(req, "prompt", "") or ""), str(getattr(req, "system_prompt", "") or "")]
        extra_parts = getattr(req, "extra_user_content_parts", None)
        if isinstance(extra_parts, list):
            for part in extra_parts:
                if isinstance(part, dict):
                    parts.append(str(part.get("text") or part.get("content") or ""))
                else:
                    parts.append(str(getattr(part, "text", "") or getattr(part, "content", "") or ""))
        return "\n".join(item for item in parts if item)

    @staticmethod
    def _strip_private_companion_prompt_artifacts(text: Any) -> str:
        cleaned = sanitize_llm_segment_control_tokens(text)
        if not cleaned or "private_companion_" not in cleaned:
            return cleaned
        cleaned = re.sub(
            r"\n*\s*<!--\s*private_companion_turn_fragments_start\s*-->.*?<!--\s*private_companion_turn_fragments_end\s*-->\s*",
            "\n",
            cleaned,
            flags=re.DOTALL,
        )
        block_markers = (
            "state",
            "static",
            "reply_style",
            "environment",
            "reply_image_anchor",
            "atrelay_tools",
            "relation_lookup",
            "qzone_tools",
            "photo_generation_tool",
            "cross_user_memory",
            "group_persona_denoise",
            "group_high_intensity_reply_guard",
            "group_context",
            "recall_query",
            "self_timeline",
            "rest_backlog",
            "atrelay_target_summary",
            "worldbook_mentions",
            "non_target_private_guard",
            "capability_boundary",
            "forward_message",
            "group_injection_guard",
            "reply_chain",
            "reply_segmentation",
            "media_delivery_truth",
            "tool_protocol",
            "period_boundary",
        )
        marker_pattern = "|".join(re.escape(f"private_companion_{name}_v1") for name in block_markers)
        cleaned = re.sub(
            rf"\n*\s*<!--\s*(?:{marker_pattern})\s*-->.*?(?=\n\s*<!--\s*private_companion_[a-z0-9_]+_v1\s*-->|\Z)",
            "\n",
            cleaned,
            flags=re.DOTALL,
        )
        return re.sub(r"\n{3,}", "\n\n", cleaned).strip()

    def _sanitize_private_companion_prompt_artifacts_in_request(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
        contexts = getattr(req, "contexts", None)
        changed = sanitize_private_request_group_artifacts(event, req)

        def clean_content(value: Any) -> tuple[Any, bool]:
            if isinstance(value, str):
                cleaned = self._strip_private_companion_prompt_artifacts(value)
                return cleaned, cleaned != value
            if isinstance(value, dict):
                updated = dict(value)
                dirty = False
                for key in ("text", "content", "value"):
                    if key in updated and isinstance(updated.get(key), str):
                        cleaned = self._strip_private_companion_prompt_artifacts(updated.get(key))
                        if cleaned != updated.get(key):
                            updated[key] = cleaned
                            dirty = True
                return updated, dirty
            if isinstance(value, list):
                new_items = []
                dirty = False
                for item in value:
                    cleaned_item, item_dirty = clean_content(item)
                    new_items.append(cleaned_item)
                    dirty = dirty or item_dirty
                return new_items, dirty
            return value, False

        if isinstance(contexts, list) and contexts:
            sanitized: list[Any] = []
            for item in contexts:
                if isinstance(item, dict):
                    updated = dict(item)
                    cleaned_content, dirty = clean_content(updated.get("content"))
                    if dirty:
                        updated["content"] = cleaned_content
                        changed += 1
                    sanitized.append(updated)
                else:
                    cleaned_item, dirty = clean_content(item)
                    if dirty:
                        changed += 1
                    sanitized.append(cleaned_item)
            try:
                req.contexts = sanitized
            except Exception:
                return
        if changed <= 0:
            return
        logger.info(
            "已清理请求中的跨轮/跨作用域动态注入残留: session=%s surfaces_changed=%s",
            _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            changed,
        )

    async def _record_request_prompt_fragment(
        self,
        event: AstrMessageEvent,
        *,
        title: str,
        key: str,
        text: str,
        source: str = "",
        mode: str = "",
        priority: int = 50,
        metadata: dict[str, Any] | None = None,
        section_manifest: list[PromptSection] | tuple[PromptSection, ...] | None = None,
    ) -> None:
        recorder = getattr(self, "_record_prompt_injection_snapshot", None)
        content = str(text or "").strip()
        if not callable(recorder) or not content:
            return
        await recorder(
            kind="request",
            session=_single_line(getattr(event, "unified_msg_origin", ""), 160) or self._event_scope_key(event),
            title=title,
            text=content,
            mode=mode,
            trace_id=self._prompt_injection_trace_id_for_event(event),
            message_preview=self._prompt_injection_message_preview_for_event(event),
            sender_label=self._prompt_injection_sender_label_for_event(event),
            section_manifest=(
                section_manifest
                if section_manifest is not None
                else [
                    {
                        "key": key,
                        "title": title,
                        "source": source,
                        "priority": priority,
                        "content": content,
                        "chars": len(content),
                    }
                ]
            ),
            metadata={
                **(metadata or {}),
                "会话": _single_line(getattr(event, "unified_msg_origin", ""), 160) or "unknown",
                "发送者": _single_line(self._event_sender_id(event), 80),
            },
        )

    async def _resolve_prompt_context_collector(
        self,
        spec: dict[str, Any],
    ) -> CollectedPromptContext:
        key = _single_line(spec.get("key"), 80)
        source = _single_line(spec.get("source"), 80)
        priority = _safe_int(spec.get("priority"), 100, 0)
        timeout = max(0.05, _safe_float(spec.get("timeout"), 0.8, 0.05))
        started = time.time()
        metadata = dict(spec.get("metadata") if isinstance(spec.get("metadata"), dict) else {})
        metadata.setdefault("来源", source or key)
        metadata.setdefault("超时秒数", round(timeout, 2))
        try:
            func = spec.get("func")
            if not callable(func):
                raise TypeError("collector is not callable")
            result = func()
            if asyncio.iscoroutine(result):
                result = await asyncio.wait_for(result, timeout=timeout)
            if result is None:
                sections: tuple[PromptSection, ...] = ()
            elif isinstance(result, PromptSection):
                sections = (result,)
            elif isinstance(result, (list, tuple)) and all(
                isinstance(item, PromptSection) for item in result
            ):
                sections = tuple(result)
            else:
                raise TypeError(
                    f"prompt collector {key or source or 'unknown'} must return "
                    "PromptSection, a PromptSection sequence, or None"
                )
            content = "\n\n".join(
                render_prompt_sections(
                    [item],
                    mode=PromptRenderMode.BODY_ONLY,
                )
                for item in sections
            ).strip()
            elapsed_ms = int((time.time() - started) * 1000)
            metadata.update(
                {
                    "耗时ms": elapsed_ms,
                    "状态": "命中" if content else "空",
                    "字符数": len(content),
                }
            )
            return CollectedPromptContext(
                key=key,
                priority=priority,
                sections=sections,
                metadata=metadata,
                status="hit" if content else "empty",
            )
        except asyncio.TimeoutError:
            elapsed_ms = int((time.time() - started) * 1000)
            metadata.update({"耗时ms": elapsed_ms, "状态": "超时"})
            logger.warning(
                "请求上下文收集超时: key=%s source=%s timeout=%.2fs",
                key or "-",
                source or "-",
                timeout,
            )
            return CollectedPromptContext(
                key=key,
                priority=priority,
                sections=(),
                metadata=metadata,
                status="timeout",
            )
        except Exception as exc:
            elapsed_ms = int((time.time() - started) * 1000)
            metadata.update({"耗时ms": elapsed_ms, "状态": "失败", "错误": _single_line(exc, 120)})
            logger.debug(
                "请求上下文收集失败: key=%s source=%s error=%s",
                key or "-",
                source or "-",
                _single_line(exc, 120),
            )
            return CollectedPromptContext(
                key=key,
                priority=priority,
                sections=(),
                metadata=metadata,
                status="error",
            )

    async def _format_passive_environment_prompt_section(
        self,
        event: AstrMessageEvent,
        *,
        lightweight: bool = False,
    ) -> PromptSection:
        if not lightweight:
            return await self._format_environment_perception_prompt_section(event)
        lines: list[str] = []
        if self._feature_enabled_or_temp_unlocked("enable_environment_perception"):
            current = self._environment_now()
            lines = [
                "这是当前消息的轻量背景边界，主要影响时间感、平台语境和回复节奏；如果用户刚好在问时间、平台或环境感受，可以按需要自然带出，没问到时就只当背景参考。",
                f"时间：{current.strftime('%Y-%m-%d %H:%M')}",
                "时间锚点必须以这一行真实时间为准；不要把未来日程、睡眠段、旧记忆或上次对话里的时间说成当前时间。",
            ]
            current_minutes = current.hour * 60 + current.minute
            if not (22 * 60 <= current_minutes or current_minutes <= 90):
                lines.append(
                    "当前没有进入深夜时段；即使人格、作息或旧上下文提到“可能很晚”“晚上睡觉”，也不能主动说快十一点、困不困、该睡了或晚安。"
                )
            platform = await self._format_platform_perception(event)
            if platform:
                lines.append(f"会话：{platform}")
        return prompt_section(
            key="environment.lightweight",
            title="轻量环境感知",
            source="environment",
            content="\n".join(lines),
        )

    async def _append_capability_boundary_to_request(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
        marker = "<!-- private_companion_capability_boundary_v1 -->"
        current_prompt = req.system_prompt or ""
        if marker in current_prompt:
            return
        boundary = (
            "你不能假装自己能影响现实、网络、游戏房间、他人设备或用户身体动作。"
            "没有可用工具且没有实际执行结果时,不要承诺“我这就拉你/我帮你操作/我已经处理/我去修/我给你弄好”。"
            "遇到拉人、开房间、修网、重启、登录、下载、现实代办等请求,只能自然说明自己做不到实际操作,可以提醒、陪用户确认、建议对方找能操作的人,或在确有工具时调用工具后再描述结果。"
        )
        boundary_sections = [
            prompt_section(
                key="guard.capability_boundary",
                title="能力边界",
                source="guard",
                content=boundary,
            )
        ]
        platform_boundary_getter = getattr(
            self,
            "_platform_capability_prompt_section",
            None,
        )
        if callable(platform_boundary_getter):
            platform_boundary = platform_boundary_getter(event)
            if platform_boundary is not None and not isinstance(
                platform_boundary,
                PromptSection,
            ):
                raise TypeError("platform capability prompt must return PromptSection or None")
            if isinstance(platform_boundary, PromptSection) and render_prompt_sections(
                [platform_boundary],
                mode=PromptRenderMode.BODY_ONLY,
            ).strip():
                boundary_sections.append(platform_boundary)
        boundary = render_prompt_sections(boundary_sections)
        for index, section in enumerate(boundary_sections):
            self._materialize_conversation_system_block(
                req,
                section=section,
                marker=marker if index == 0 else "",
                priority=30,
                placement=PLACEMENT_DYNAMIC_SYSTEM,
                metadata={DELIVERY_GROUP_MARKER_METADATA_KEY: marker},
            )
        await self._record_request_prompt_fragment(
            event,
            title="能力边界注入",
            key="capability.boundary",
            text=boundary,
            source="guard",
            mode="group",
        )

    async def _append_media_delivery_truth_to_request(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
        sections = self._media_delivery_truth_prompt_sections()
        media_truth_marker = "<!-- private_companion_media_delivery_truth_v1 -->"
        if not sections or self._request_has_managed_prompt_marker(req, media_truth_marker):
            return
        plan = get_conversation_injection_plan(req)
        for index, section in enumerate(sections):
            plan.materialize_system_block(
                req,
                section=section,
                marker=media_truth_marker if index == 0 else "",
                priority=30 + index,
                placement=PLACEMENT_STABLE_SYSTEM,
            )
        media_truth_instruction = render_prompt_sections(sections)
        await self._record_request_prompt_fragment(
            event,
            title="媒体发送真实性约束",
            key="tools.media_delivery_truth",
            text=media_truth_instruction,
            source="tools",
            mode="always",
            metadata={"注入位置": "system_prompt"},
            section_manifest=sections,
        )

    def _place_conversation_prompt_sections(
        self,
        req: ProviderRequest,
        marker: str,
        sections: Iterable[PromptSection],
        *,
        priority: int,
    ) -> tuple[str, str, tuple[PromptSection, ...]]:
        authored = tuple(
            section
            for section in sections
            if isinstance(section, PromptSection)
            and render_prompt_sections(
                [section],
                mode=PromptRenderMode.BODY_ONLY,
            ).strip()
        )
        if not authored:
            return "none", "", ()
        visible = render_prompt_sections(authored)
        position = self._normalize_passive_injection_position(
            runtime_persona_setting(self, "passive_injection_position", "prompt")
        )
        marker = _single_line(marker, 120) or "<!-- private_companion_turn_fragment -->"
        plan = get_conversation_injection_plan(req)
        if plan is None:
            raise RuntimeError("conversation injection plan is unavailable")
        use_system_prompt = position == "system_prompt"
        if not plan.contains_marker(marker):
            for index, section in enumerate(authored):
                plan.add(
                    section=section,
                    marker=marker if index == 0 else "",
                    priority=int(priority),
                    placement=(
                        PLACEMENT_DYNAMIC_SYSTEM
                        if use_system_prompt
                        else PLACEMENT_TURN_TAIL
                    ),
                    materialized=False,
                    metadata={DELIVERY_GROUP_MARKER_METADATA_KEY: marker},
                )
        setattr(req, "_private_companion_turn_prompt_fragments", plan.turn_fragments())
        rendered_placement = plan.render_into(req, prefer_extra_user_content=True)
        placement = "system_prompt" if use_system_prompt else rendered_placement
        return placement, visible, authored

    async def _append_conditional_tool_instructions_to_request(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
        message_text = str(getattr(event, "message_str", "") or "")
        current_prompt = req.system_prompt or ""
        atrelay_section = self._atrelay_tool_prompt_section()
        atrelay_instruction = (
            render_prompt_sections(
                [atrelay_section],
                mode=PromptRenderMode.BODY_ONLY,
            )
            if isinstance(atrelay_section, PromptSection)
            else ""
        )
        current_turn_prompt = str(getattr(req, "prompt", "") or "")
        atrelay_marker = "<!-- private_companion_atrelay_tools_v1 -->"
        if atrelay_instruction and atrelay_marker not in current_prompt and atrelay_marker not in current_turn_prompt:
            if self._message_looks_like_atrelay_request(message_text):
                await self._append_atrelay_target_summary_to_request(event, req)
                current_prompt = req.system_prompt or ""
                current_turn_prompt = str(getattr(req, "prompt", "") or "")
                placement, rendered_instruction, manifest = self._place_conversation_prompt_sections(
                    req,
                    atrelay_marker,
                    [atrelay_section],
                    priority=88,
                )
                await self._record_request_prompt_fragment(
                    event,
                    title="跨群转述工具注入",
                    key="tools.atrelay",
                    text=rendered_instruction,
                    source="tools",
                    mode="conditional",
                    metadata={"注入位置": placement},
                    section_manifest=manifest,
                )
        relation_section = self._relation_lookup_prompt_section()
        relation_instruction = (
            render_prompt_sections(
                [relation_section],
                mode=PromptRenderMode.BODY_ONLY,
            )
            if isinstance(relation_section, PromptSection)
            else ""
        )
        current_prompt = req.system_prompt or ""
        current_turn_prompt = str(getattr(req, "prompt", "") or "")
        relation_marker = "<!-- private_companion_relation_lookup_v1 -->"
        try:
            relation_private = bool(getattr(event, "is_private_chat", lambda: False)())
        except Exception:
            relation_private = ":FriendMessage:" in str(getattr(event, "unified_msg_origin", "") or "")
        relation_query = any(token in message_text for token in ("查关系网", "关系网查", "查一下关系", "查查关系"))
        relation_query = relation_query or (
            any(token in message_text for token in ("查一下", "查查", "帮我查", "查一查"))
            and (
                bool(re.search(r"\d{5,12}", message_text))
                or any(token in message_text for token in ("这个人", "这人", "那个人", "那人", "是谁", "认识"))
            )
        )
        livingmemory_relation_context = (
            relation_private
            and bool(getattr(self, "enable_livingmemory_integration", False))
            and bool(getattr(self, "_livingmemory_available", lambda: False)())
        )
        if relation_private and relation_instruction and relation_marker not in current_prompt and relation_marker not in current_turn_prompt and (relation_query or livingmemory_relation_context):
            placement, rendered_instruction, manifest = self._place_conversation_prompt_sections(
                req,
                relation_marker,
                [relation_section],
                priority=87,
            )
            await self._record_request_prompt_fragment(
                event,
                title="关系网查询工具注入",
                key="tools.relation_lookup",
                text=rendered_instruction,
                source="tools",
                mode="conditional",
                metadata={"注入位置": placement, "触发原因": "livingmemory" if livingmemory_relation_context and not relation_query else "query"},
                section_manifest=manifest,
            )
        qzone_sections = self._qzone_tool_prompt_sections(event)
        qzone_instruction = render_prompt_sections(
            qzone_sections,
            mode=PromptRenderMode.BODY_ONLY,
        )
        current_prompt = req.system_prompt or ""
        current_turn_prompt = str(getattr(req, "prompt", "") or "")
        qzone_marker = "<!-- private_companion_qzone_tools_v1 -->"
        if qzone_instruction and qzone_marker not in current_prompt and qzone_marker not in current_turn_prompt:
            if any(token in message_text for token in ("说说", "空间", "QQ空间", "动态", "点赞", "评论")):
                placement, rendered_instruction, manifest = self._place_conversation_prompt_sections(
                    req,
                    qzone_marker,
                    qzone_sections,
                    priority=88,
                )
                await self._record_request_prompt_fragment(
                    event,
                    title="QQ 空间工具注入",
                    key="tools.qzone",
                    text=rendered_instruction,
                    source="tools",
                    mode="conditional",
                    metadata={"注入位置": placement},
                    section_manifest=manifest,
                )
        schedule_management_section = self._schedule_management_tool_prompt_section()
        schedule_management_instruction = render_prompt_sections(
            [schedule_management_section],
            mode=PromptRenderMode.BODY_ONLY,
        )
        current_prompt = req.system_prompt or ""
        current_turn_prompt = str(getattr(req, "prompt", "") or "")
        schedule_management_marker = "<!-- private_companion_schedule_management_v1 -->"
        try:
            schedule_management_private = bool(getattr(event, "is_private_chat", lambda: False)())
        except Exception:
            schedule_management_private = ":FriendMessage:" in str(getattr(event, "unified_msg_origin", "") or "")
        if (
            schedule_management_private
            and self._can_manage_private_companion(event)
            and self._schedule_management_instruction_matches(message_text)
            and schedule_management_instruction
            and schedule_management_marker not in current_prompt
            and schedule_management_marker not in current_turn_prompt
        ):
            placement, rendered_instruction, manifest = self._place_conversation_prompt_sections(
                req,
                schedule_management_marker,
                [schedule_management_section],
                priority=88,
            )
            await self._record_request_prompt_fragment(
                event,
                title="指定日程管理工具注入",
                key="tools.schedule_management",
                text=rendered_instruction,
                source="tools",
                mode="conditional",
                metadata={"注入位置": placement},
                section_manifest=manifest,
            )
        memo_section = self._memo_management_tool_prompt_section()
        memo_instruction = render_prompt_sections(
            [memo_section],
            mode=PromptRenderMode.BODY_ONLY,
        )
        current_prompt = req.system_prompt or ""
        current_turn_prompt = str(getattr(req, "prompt", "") or "")
        memo_marker = "<!-- private_companion_memo_management_v1 -->"
        try:
            memo_private = bool(getattr(event, "is_private_chat", lambda: False)())
            identity_for_event = getattr(self, "_event_permission_identity_id", None)
            memo_requester = (
                identity_for_event(event)
                if callable(identity_for_event)
                else self._permission_identity_id(event.get_sender_id())
            )
        except Exception:
            memo_private = ":FriendMessage:" in str(getattr(event, "unified_msg_origin", "") or "")
            memo_requester = ""
        memo_owner = bool(memo_requester and self._is_private_companion_owner_user_id(memo_requester))
        memo_request = bool(
            memo_private
            and memo_owner
            and self._memo_management_instruction_matches(message_text)
        )
        if memo_request:
            self._mark_memo_request_tool_boundary(event, req)
            if self._remove_future_task_for_memo_request(req, message_text):
                logger.debug(
                    "明确便签请求已从初始工具集移除 future_task: session=%s",
                    _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                )
        if (
            memo_request
            and memo_instruction
            and memo_marker not in current_prompt
            and memo_marker not in current_turn_prompt
        ):
            placement, rendered_instruction, manifest = self._place_conversation_prompt_sections(
                req,
                memo_marker,
                [memo_section],
                priority=88,
            )
            await self._record_request_prompt_fragment(
                event,
                title="备忘便签工具注入",
                key="tools.memo_management",
                text=rendered_instruction,
                source="tools",
                mode="conditional",
                metadata={"注入位置": placement},
                section_manifest=manifest,
            )

        creative_work_section = self._creative_work_tool_prompt_section()
        creative_work_instruction = (
            render_prompt_sections(
                [creative_work_section],
                mode=PromptRenderMode.BODY_ONLY,
            )
            if isinstance(creative_work_section, PromptSection)
            else ""
        )
        current_prompt = req.system_prompt or ""
        current_turn_prompt = str(getattr(req, "prompt", "") or "")
        creative_work_marker = "<!-- private_companion_creative_work_tool_v1 -->"
        try:
            creative_work_private = bool(getattr(event, "is_private_chat", lambda: False)())
        except Exception:
            creative_work_private = ":FriendMessage:" in str(getattr(event, "unified_msg_origin", "") or "")
        if (
            creative_work_private
            and creative_work_instruction
            and self._creative_work_query_instruction_matches(message_text)
            and creative_work_marker not in current_prompt
            and creative_work_marker not in current_turn_prompt
        ):
            try:
                setattr(event, "private_companion_creative_work_tool_required", True)
            except Exception:
                pass
            placement, rendered_instruction, manifest = self._place_conversation_prompt_sections(
                req,
                creative_work_marker,
                [creative_work_section],
                priority=89,
            )
            await self._record_request_prompt_fragment(
                event,
                title="创作正文读取工具注入",
                key="tools.creative_work",
                text=rendered_instruction,
                source="tools",
                mode="conditional",
                metadata={"注入位置": placement},
                section_manifest=manifest,
            )

        await self._append_media_delivery_truth_to_request(event, req)
        explicit_photo_request = self._photo_generation_instruction_matches(message_text)
        explicit_media_delivery_request = self._current_media_delivery_instruction_matches(message_text)
        referenced_media_edit_request = False
        if (
            not explicit_photo_request
            and self._referenced_media_edit_instruction_matches(message_text)
        ):
            finder = getattr(self, "_find_reply_image_sources_for_event", None)
            if callable(finder):
                try:
                    referenced_media_edit_request = bool(await finder(event))
                except Exception as exc:
                    logger.debug(
                        "引用图片编辑意图确认失败: session=%s error=%s",
                        _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                        _single_line(exc, 160),
                    )
        explicit_media_request = bool(
            explicit_photo_request
            or explicit_media_delivery_request
            or referenced_media_edit_request
        )
        reaction_expression_authorized = False
        if (
            not explicit_media_request
            and bool(runtime_persona_setting(self, 'enable_reaction_expression_experiment', False))
        ):
            reaction_expression_authorized = await self._preauthorize_reaction_expression_prompt(event)
        reaction_expression_evaluated = bool(
            self._reaction_expression_authorization(event)
        )
        allow_photo_on_reaction_turns = bool(
            runtime_persona_setting(
                self,
                'allow_generate_photo_on_reaction_turns',
                False,
            )
        )
        removed_reaction_tools = self._scope_reaction_media_tools_for_request(
            req,
            explicit_media_request=explicit_media_request,
            reaction_authorized=reaction_expression_authorized,
            reaction_evaluated=reaction_expression_evaluated,
            allow_photo_on_reaction_turns=allow_photo_on_reaction_turns,
        )
        if removed_reaction_tools:
            self._log_reaction_expression_event(
                event,
                stage="authorization",
                decision="scoped",
                reason="media_tools_scoped",
                scope=self._reaction_expression_scope(event),
            )
        photo_section = self._photo_generation_tool_prompt_section(
            event,
            include_spontaneous=reaction_expression_authorized,
            spontaneous_only=reaction_expression_authorized and not explicit_media_request,
            allow_photo_on_reaction_turns=allow_photo_on_reaction_turns,
        )
        photo_instruction = (
            render_prompt_sections(
                [photo_section],
                mode=PromptRenderMode.BODY_ONLY,
            )
            if isinstance(photo_section, PromptSection)
            else ""
        )
        current_prompt = req.system_prompt or ""
        current_turn_prompt = str(getattr(req, "prompt", "") or "")
        photo_marker = "<!-- private_companion_photo_generation_tool_v1 -->"
        if photo_instruction and photo_marker not in current_prompt and photo_marker not in current_turn_prompt:
            if explicit_media_request or reaction_expression_authorized:
                placement, rendered_instruction, manifest = self._place_conversation_prompt_sections(
                    req,
                    photo_marker,
                    [photo_section],
                    priority=88,
                )
                await self._record_request_prompt_fragment(
                    event,
                    title=(
                        "实验性表情表达工具注入"
                        if reaction_expression_authorized and not explicit_media_request
                        else "生图工具注入"
                    ),
                    key=(
                        "tools.reaction_expression"
                        if reaction_expression_authorized and not explicit_media_request
                        else "tools.photo_generation"
                    ),
                    text=rendered_instruction,
                    source="tools",
                    mode="conditional",
                    metadata={
                        "注入位置": placement,
                        "预授权": bool(reaction_expression_authorized),
                    },
                    section_manifest=manifest,
                )
        cross_user_section = self._cross_user_memory_query_prompt_section()
        cross_user_instruction = (
            render_prompt_sections(
                [cross_user_section],
                mode=PromptRenderMode.BODY_ONLY,
            )
            if isinstance(cross_user_section, PromptSection)
            else ""
        )
        current_prompt = req.system_prompt or ""
        current_turn_prompt = str(getattr(req, "prompt", "") or "")
        cross_user_marker = "<!-- private_companion_cross_user_memory_v1 -->"
        if cross_user_instruction and cross_user_marker not in current_prompt and cross_user_marker not in current_turn_prompt:
            if any(token in message_text for token in (
                "聊了什么", "说了什么", "发了什么", "讲了什么", "互动", "和谁聊", "跟谁聊", "最近跟", "最近和",
                "你和", "你跟", "在群里", "那个群", "这个群", "私聊过", "聊过",
            )):
                placement, rendered_instruction, manifest = self._place_conversation_prompt_sections(
                    req,
                    cross_user_marker,
                    [cross_user_section],
                    priority=88,
                )
                await self._record_request_prompt_fragment(
                    event,
                    title="跨用户记忆互通工具注入",
                    key="tools.cross_user_memory",
                    text=rendered_instruction,
                    source="tools",
                    mode="conditional",
                    metadata={"注入位置": placement},
                    section_manifest=manifest,
                )

    def _format_external_realtime_prompt_section(
        self,
        current_user: dict[str, Any] | None = None,
        *,
        public: bool = False,
    ) -> PromptSection:
        return prompt_section(
            key="realtime.activity_public" if public else "realtime.activity_continuity",
            title="实时共同活动公开状态" if public else "实时共同活动与短期连续性",
            source="external_realtime",
            content=self._format_external_realtime_context_body(
                current_user,
                public=public,
            ),
        )

    def _private_passive_state_update_prompt_sections(
        self,
        *,
        session: str,
        state: dict[str, Any],
        current_user: dict[str, Any] | None,
        inbound_text: str,
        lightweight: bool,
    ) -> tuple[list[PromptSection], bool, str]:
        session_key = _single_line(session, 160) or "unknown"
        cache = getattr(self, "_passive_state_session_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            self._passive_state_session_cache = cache
        fingerprint = self._private_passive_state_fingerprint(state, current_user)
        previous = cache.get(session_key) if isinstance(cache.get(session_key), dict) else {}
        changed = previous.get("fingerprint") != fingerprint
        third_party_activity_question = self._user_activity_question_targets_someone_else(inbound_text)
        direct_state_request = not third_party_activity_question and (
            self._user_asks_bot_current_state_or_activity(inbound_text)
            or self._user_asks_recent_bot_activity(inbound_text)
            or bool(
                re.search(r"(状态|日程|精力|心情|情绪|在干嘛|做什么|忙什么|近况)", str(inbound_text or ""))
            )
        )
        now_ts = _now_ts()
        cache[session_key] = {
            "fingerprint": fingerprint,
            "ts": now_ts,
            "last_changed_ts": now_ts if changed else _safe_float(previous.get("last_changed_ts"), now_ts),
        }
        if len(cache) > 240:
            stale = sorted(
                ((key, _safe_float(value.get("ts"), 0)) for key, value in cache.items() if isinstance(value, dict)),
                key=lambda item: item[1],
            )
            for key, _ in stale[: max(0, len(cache) - 200)]:
                cache.pop(key, None)
        if direct_state_request:
            state_section = self._format_private_passive_state_snapshot_section(
                state,
                current_user,
                direct=True,
            )
            state_changed = changed
            reason = "direct"
        elif changed:
            state_section = self._format_private_passive_state_snapshot_section(
                state,
                current_user,
                direct=False,
            )
            state_changed = True
            reason = "changed"
        elif bool(runtime_persona_setting(self, 'enable_passive_state_continuity_anchor', False)):
            state_section = self._format_private_passive_state_continuity_anchor_section(
                state,
                current_user,
            )
            state_changed = False
            reason = "continuity_anchor"
        else:
            return [], False, "unchanged_light" if lightweight else "unchanged"

        if reason == "continuity_anchor":
            reply_policy_section = self._private_passive_state_reply_policy_section(
                compact=True,
            )
            state_body = render_prompt_sections(
                [state_section],
                mode=PromptRenderMode.BODY_ONLY,
            )
            policy_body = render_prompt_sections(
                [reply_policy_section],
                mode=PromptRenderMode.BODY_ONLY,
            )
            state_section = prompt_section(
                key=state_section.key,
                title=state_section.title,
                source=state_section.source,
                content=state_body[: max(0, 300 - len(policy_body) - 1)].rstrip(),
            )
        else:
            reply_policy_section = self._private_passive_state_reply_policy_section()
        sections = [state_section, reply_policy_section]
        return sections, state_changed, reason

    async def _append_group_active_period_boundary_to_request(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        group_id: str,
    ) -> str:
        if not group_id:
            return ""
        try:
            state = await self._ensure_daily_state(
                skip_conversation_summary=True,
                passive_fast=True,
            )
            boundary_section = self._format_active_period_boundary_prompt_section(
                state,
                public=True,
            )
            boundary = render_prompt_sections(
                [boundary_section],
                mode=PromptRenderMode.BODY_ONLY,
            )
        except Exception as exc:
            logger.debug(
                "群聊读取经期互动边界失败，已跳过: group=%s error=%s",
                _single_line(group_id, 40) or "-",
                _single_line(exc, 120),
            )
            return ""
        if not boundary:
            return ""

        marker = "<!-- private_companion_period_boundary_v1 -->"
        if self._request_has_managed_prompt_marker(req, marker):
            return boundary
        placement = self._place_conversation_prompt_section(
            req,
            marker,
            boundary_section,
            priority=89,
        )
        await self._record_request_prompt_fragment(
            event,
            title="群聊经期互动边界",
            key="state.period_boundary",
            text=boundary,
            source="daily_state",
            mode="group",
            priority=89,
            metadata={"注入位置": placement, "群号": _single_line(group_id, 40)},
        )
        return boundary

    async def _append_private_active_period_boundary_to_request(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        state: dict[str, Any],
    ) -> str:
        boundary_section = self._format_active_period_boundary_prompt_section(
            state,
            public=False,
        )
        boundary = render_prompt_sections(
            [boundary_section],
            mode=PromptRenderMode.BODY_ONLY,
        )
        if not boundary:
            return ""
        marker = "<!-- private_companion_period_boundary_v1 -->"
        if self._request_has_managed_prompt_marker(req, marker):
            return boundary
        placement = self._place_conversation_prompt_section(
            req,
            marker,
            boundary_section,
            priority=89,
        )
        await self._record_request_prompt_fragment(
            event,
            title="私聊经期互动边界",
            key="state.period_boundary",
            text=boundary,
            source="daily_state",
            mode="private",
            priority=89,
            metadata={"注入位置": placement},
        )
        return boundary

    def _format_group_persona_denoise_prompt_sections(
        self,
        event: AstrMessageEvent | None = None,
    ) -> list[PromptSection]:
        body = self._format_group_persona_denoise_body(event)
        if not body:
            return []
        return [
            prompt_section(
                key="group.persona_denoise",
                title="群聊人格降噪",
                source="group",
                content=body,
            ),
            prompt_section(
                key="group.persona_denoise.joke_boundary",
                title="群聊玩笑边界",
                source="group",
                content=self._group_persona_denoise_joke_boundary(),
            ),
        ]

    async def _append_group_persona_denoise_to_request(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
        if not bool(runtime_persona_setting(self, 'enable_group_companion', True)):
            return
        group_id = self._extract_group_id_from_event(event)
        if not group_id or not self._group_enabled_for_event(group_id):
            return
        denoise_sections = self._format_group_persona_denoise_prompt_sections(event)
        if not denoise_sections:
            return
        denoise_text = render_prompt_sections(denoise_sections)
        marker = "<!-- private_companion_group_persona_denoise_v1 -->"
        current_prompt = req.system_prompt or ""
        current_turn_prompt = str(getattr(req, "prompt", "") or "")
        if marker in current_prompt or marker in current_turn_prompt:
            return
        placement, _, _ = self._place_conversation_prompt_sections(
            req,
            marker,
            denoise_sections,
            priority=32,
        )
        await self._record_request_prompt_fragment(
            event,
            title="群聊人格降噪注入",
            key="group.persona_denoise",
            text=denoise_text,
            source="group",
            mode="group",
            metadata={"注入位置": placement},
        )

    async def _append_non_target_private_identity_guard_to_request(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
        marker = "<!-- private_companion_non_target_private_guard_v1 -->"
        current_prompt = req.system_prompt or ""
        if marker in current_prompt:
            return
        try:
            user_id = str(event.get_sender_id())
        except Exception:
            user_id = ""
        user_id = _single_line(user_id, 40)
        resolver = getattr(self, "_private_user_id_for_event", None)
        canonical_user_id = (
            resolver(event, user_id)
            if callable(resolver) and user_id
            else self._canonical_private_user_id(user_id)
            if user_id
            else ""
        )
        if canonical_user_id:
            user_id = canonical_user_id
        if not user_id or self._is_bot_self_user_id(user_id):
            return
        raw_users = self.data.get("users", {})
        current_user = raw_users.get(user_id) if isinstance(raw_users, dict) else None
        if (
            isinstance(current_user, dict)
            and self._private_passive_profile_available(user_id, current_user)
        ):
            return
        display_name = ""
        try:
            display_name = _single_line(self._sender_display_name(event), 40)
        except Exception:
            display_name = ""
        lines = [
            f"当前私聊对象稳定 ID：{user_id}",
            "这个用户不是插件当前启用的目标陪伴用户/主用户。",
            "如果基础人格里包含“主要用户/主人”“恋人”“专属称呼”或只属于主要用户的关系设定,不要套用到当前私聊对象身上。",
            "可以保留人格的通用说话风格,但关系身份、亲密度、记忆和承诺必须按当前用户重新判断。",
            "除非当前用户明确提出角色扮演或临时设定,否则不要把对方当成主要用户、恋人或目标陪伴对象。",
        ]
        if display_name and display_name != user_id:
            lines.append(f"平台当前显示名：{display_name}。显示名只作称呼线索,不能覆盖稳定 ID。")
        profile = None
        try:
            profile = self._worldbook_profile_by_user_id(user_id)
        except Exception:
            profile = None
        profile_lines: list[str] = []
        if isinstance(profile, dict) and profile.get("enabled", True):
            name = _single_line(profile.get("name"), 40)
            gender = _single_line(profile.get("gender"), 40)
            identity = _single_line(profile.get("identity_note") or profile.get("note") or profile.get("content"), 220)
            boundary = _single_line(profile.get("boundary_note"), 140)
            aliases = []
            for item in profile.get("aliases") if isinstance(profile.get("aliases"), list) else []:
                alias = _single_line(item, 24)
                if alias and alias != user_id and alias not in aliases:
                    aliases.append(alias)
            profile_lines.append("以下资料来自当前私聊 QQ 号的精确匹配,只用于识别当前用户,不能外推到主用户。")
            if name and name != user_id:
                profile_lines.append(f"登记名：{name}")
            if gender:
                profile_lines.append(f"性别：{gender}")
            if aliases:
                profile_lines.append(f"可用称呼线索：{'、'.join(aliases[:6])}")
            if identity:
                profile_lines.append(f"身份备注：{identity}")
            if boundary:
                profile_lines.append(f"互动边界：{boundary}")
            profile_lines.append("即使此用户资料中有亲昵称呼,也必须服从上面的防串规则：不要把目标陪伴用户的专属关系套给 TA。")
        guard_sections = [
            prompt_section(
                key="identity.non_target_private",
                title="私聊身份防串",
                source="identity",
                content=chr(10).join(lines),
            )
        ]
        if profile_lines:
            guard_sections.append(
                prompt_section(
                    key="identity.non_target_profile",
                    title="当前用户关系网资料",
                    source="identity",
                    content=chr(10).join(profile_lines),
                )
            )
        guard_text = render_prompt_sections(guard_sections)
        for index, section in enumerate(guard_sections):
            self._materialize_conversation_system_block(
                req,
                section=section,
                marker=marker if index == 0 else "",
                priority=10,
                placement=PLACEMENT_DYNAMIC_SYSTEM,
                metadata={DELIVERY_GROUP_MARKER_METADATA_KEY: marker},
            )
        await self._record_request_prompt_fragment(
            event,
            title="非目标私聊防串注入",
            key="identity.non_target",
            text=guard_text,
            source="identity",
            mode="private",
        )

    def _format_atrelay_target_summary_prompt_section(
        self,
        text: str,
    ) -> PromptSection | None:
        if not (self.enabled and bool(getattr(self, "enable_atrelay_tools", False))):
            return None
        text = str(text or "")
        if not self._message_looks_like_atrelay_request(text):
            return None
        lines: list[str] = []
        has_signal = False
        group_expected = any(token in text for token in ("群里", "群聊", "发到", "发群", "群"))
        member_expected = any(token in text for token in ("找", "告诉", "转告", "转达", "跟", "和", "给", "@", "艾特", "私聊", "说一句", "说一声"))

        group_matches = self._atrelay_cached_group_matches(text)
        if group_matches:
            has_signal = True
            if len(group_matches) == 1:
                group = group_matches[0]
                lines.append(
                    "目标群候选：确定｜"
                    f"{_single_line(group.get('group_name'), 60) or group.get('group_id')}（群号:{_single_line(group.get('group_id'), 40)}）"
                    f"｜来源:{_single_line(group.get('source'), 30) or 'local'}"
                )
            else:
                parts = [
                    f"{_single_line(item.get('group_name'), 40) or item.get('group_id')}（{_single_line(item.get('group_id'), 40)}）"
                    for item in group_matches[:5]
                ]
                lines.append("目标群候选：多个｜" + "；".join(parts))
        elif group_expected:
            has_signal = True
            lines.append("目标群候选：未命中｜用户可能还需要补充群名或群号。")

        member_profiles = self._select_worldbook_member_profiles_for_private_text(text, limit=5)
        if member_profiles:
            has_signal = True
            if len(member_profiles) == 1:
                profile = member_profiles[0]
                uid = _single_line(profile.get("user_id"), 40)
                name = _single_line(profile.get("name"), 40) or uid
                identity = _single_line(profile.get("identity_note") or profile.get("note") or profile.get("content"), 100)
                parts = [f"{name}（QQ:{uid or '-'}）"]
                if identity:
                    parts.append(f"身份:{identity}")
                lines.append("目标成员候选：确定｜" + "｜".join(parts))
            else:
                parts = [
                    f"{_single_line(profile.get('name'), 32) or _single_line(profile.get('user_id'), 40)}"
                    f"（{_single_line(profile.get('user_id'), 40) or '-'}）"
                    for profile in member_profiles[:5]
                ]
                lines.append("目标成员候选：多个｜" + "；".join(parts))
        elif member_expected:
            has_signal = True
            lines.append("目标成员候选：未命中｜没有从关系网里确定收话人。")

        if not has_signal:
            return None
        lines.append("这些只是本轮目标解析线索；真正发送仍以用户明确要求和工具执行结果为准。")
        return prompt_section(
            key="atrelay.target_summary",
            title="本轮转述目标摘要",
            source="atrelay",
            content="\n".join(lines),
        )

    async def _append_atrelay_target_summary_to_request(self, event: AstrMessageEvent, req: ProviderRequest) -> bool:
        text = str(
            getattr(event, "private_companion_group_text", "")
            or getattr(event, "message_str", "")
            or ""
        )
        summary_section = self._format_atrelay_target_summary_prompt_section(text)
        if summary_section is None:
            return False
        summary = render_prompt_sections(
            [summary_section],
            mode=PromptRenderMode.BODY_ONLY,
        )
        marker = "<!-- private_companion_atrelay_target_summary_v1 -->"
        current_prompt = req.system_prompt or ""
        current_turn_prompt = str(getattr(req, "prompt", "") or "")
        if marker in current_prompt or marker in current_turn_prompt:
            return True
        placement = self._place_conversation_prompt_section(
            req,
            marker,
            summary_section,
            priority=86,
        )
        await self._record_request_prompt_fragment(
            event,
            title="本轮转述目标摘要",
            key="tools.atrelay.targets",
            text=summary,
            source="tools",
            mode="conditional",
            metadata={"注入位置": placement},
            section_manifest=[summary_section],
        )
        return True

    async def _append_worldbook_mentions_to_request(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        *,
        mode: str = "conditional",
    ) -> None:
        if not bool(runtime_persona_setting(self, 'enable_worldbook_member_recognition', False)):
            return
        text = str(
            getattr(event, "private_companion_group_text", "")
            or getattr(event, "message_str", "")
            or ""
        )
        if self._format_atrelay_target_summary_prompt_section(text) is not None:
            return
        mention_section = self._format_worldbook_private_mentions_prompt_section(
            text,
            limit=4,
        )
        mention_text = render_prompt_sections(
            [mention_section],
            mode=PromptRenderMode.BODY_ONLY,
        )
        if not mention_text:
            return
        marker = "<!-- private_companion_worldbook_mentions_v1 -->"
        current_prompt = req.system_prompt or ""
        current_turn_prompt = str(getattr(req, "prompt", "") or "")
        if marker in current_prompt or marker in current_turn_prompt:
            return
        placement = self._place_conversation_prompt_section(
            req,
            marker,
            mention_section,
            priority=58,
        )
        await self._record_request_prompt_fragment(
            event,
            title="本轮关系网提及注入",
            key="worldbook.mentions",
            text=mention_text,
            source="worldbook",
            mode=mode,
            metadata={"注入位置": placement},
            section_manifest=[mention_section],
        )

    async def _append_rest_reply_backlog_to_request(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        user: dict[str, Any],
    ) -> str:
        backlog_prompt = self._take_rest_reply_backlog_prompt(user)
        if not backlog_prompt:
            return ""
        marker = "<!-- private_companion_rest_backlog_v1 -->"
        backlog_section = prompt_section(
            key="rest.backlog",
            title="醒后补看私聊",
            source="daily_state",
            content=backlog_prompt,
        )
        placement = self._place_conversation_prompt_section(
            req,
            marker,
            backlog_section,
            priority=25,
        )
        await self._record_request_prompt_fragment(
            event,
            title="醒后补看私聊",
            key="rest.backlog",
            text=backlog_prompt,
            source="daily_state",
            mode="private",
            metadata={"注入位置": placement},
        )
        return backlog_prompt

    def _normalize_proactive_only_unlock_key(self, value: Any) -> str:
        text = _single_line(value, 80).strip()
        if not text:
            return ""
        return _PROACTIVE_ONLY_TEMP_UNLOCK_ALIASES.get(text, text)

    async def _append_proactive_only_unlocked_llm_request_fragments(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
        if self._proactive_only_temp_unlock_allows("enable_tts_enhancement"):
            await self.apply_tts_enhancement_request(event, req)
        if self._proactive_only_temp_unlock_allows("enable_forward_message_adaptation"):
            await self._append_forward_message_context_to_request(event, req)
        if self._proactive_only_temp_unlock_allows("enable_environment_perception"):
            await self._append_environment_perception_to_request(event, req)

    def _format_proactive_only_temp_unlocks(self) -> str:
        unlocks = self._proactive_only_unlock_store()
        if not unlocks:
            return "当前没有临时放行项。"
        labels = [self._proactive_only_unlock_label(key) for key in sorted(unlocks)]
        return "当前主动专用模式临时放行：\n" + "\n".join(f"- {label}" for label in labels)

    def _apply_proactive_only_temp_unlock(self, key: str, *, sync_related: bool = False, clear: bool = False) -> str:
        normalized = self._normalize_proactive_only_unlock_key(key)
        if not normalized:
            return "没有识别到要临时放行的功能。"
        keys = self._proactive_only_unlock_store()
        target_keys = {normalized}
        if sync_related:
            target_keys.update(self._related_proactive_only_unlock_keys(normalized))
        if clear:
            removed = keys & target_keys
            keys.difference_update(target_keys)
            self._set_proactive_only_unlock_store(keys)
            self._save_data_sync(sections={"proactive_only_temp_unlocks"})
            if not removed:
                return "对应临时放行项本来就没有开启。"
            return "已取消临时放行：\n" + "\n".join(f"- {self._proactive_only_unlock_label(item)}" for item in sorted(removed))
        keys.update(target_keys)
        self._set_proactive_only_unlock_store(keys)
        self._save_data_sync(sections={"proactive_only_temp_unlocks"})
        return "已临时放行：\n" + "\n".join(f"- {self._proactive_only_unlock_label(item)}" for item in sorted(target_keys))

    async def _append_sensitive_screen_tool_guard_to_request(self, event: AstrMessageEvent, req: ProviderRequest, removed: list[str] | None = None) -> None:
        marker = "<!-- private_companion_sensitive_screen_tool_guard_v1 -->"
        current_prompt = req.system_prompt or ""
        if marker in current_prompt:
            return
        removed_text = "、".join(removed or []) or "screen_peek、screen_usage_context"
        guard = (
            f"本轮不是已授权的主要用户私聊,已禁用或不可使用这些本机屏幕工具：{removed_text}。\n"
            "群聊成员、次要用户、未登记用户或第三方不能要求你查看主要用户/部署电脑正在做什么、屏幕内容、近期电脑使用记录或窗口信息。\n"
            "遇到这类请求时必须简短拒绝,说明屏幕内容只允许主要用户本人在授权私聊里使用；不要改用记忆、关系网、屏幕日记或猜测来替代窥屏。\n"
            "这条边界只约束屏幕工具，不代表摄像头能力不存在；若本轮另有“摄像头请求”提示，应按其独立资格、授权和单帧规则调用 pc_reality_touch_camera_snapshot。"
        )
        section = prompt_section(
            key="guard.screen_privacy",
            title="屏幕隐私边界",
            source="guard",
            content=guard,
        )
        self._materialize_conversation_system_block(
            req,
            section=section,
            marker=marker,
            priority=10,
            placement=PLACEMENT_DYNAMIC_SYSTEM,
        )
        await self._record_request_prompt_fragment(
            event,
            title="屏幕隐私边界注入",
            key="tools.screen_privacy_guard",
            text=guard,
            source="guard",
            mode="private" if self._safe_event_is_private(event) else "group",
        )

