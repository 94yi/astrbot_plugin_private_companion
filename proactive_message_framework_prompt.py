# -*- coding: utf-8 -*-
"""framework_prompt 域。

由 tools/split_mixin_domain.py 从 proactive_message.py 机械抽取（33 个方法 + 3 个模块级名字 + 0 个类级赋值 / 2301 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveMessageMixin）。
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import re
import time
import uuid
from .constants import _ACTION_TEXT, _REASON_TEXT
from .conversation_prompt_section import (
    PromptDocument,
    PromptDocumentPart,
    PromptLabelStyle,
    PromptRenderMode,
    PromptSection,
    prompt_document,
    prompt_section,
    render_prompt_document,
    render_prompt_sections,
)
from .helpers import (
    _now_ts,
    _path_text,
    _safe_float,
    _safe_int,
    _single_line,
    _strip_internal_message_blocks,
)
from .memory_context_policy import core_memory_usage_contract_section
from .persona_config import runtime_persona_setting
from .planning import _external_schedule_material_context
from .proactive_message_shared import _PROACTIVE_DOCUMENT_RENDER, _persona_provider_id, _proactive_prompt_part
from .token_budget import _looks_like_upstream_llm_error_response
from astrbot.api.event import AstrMessageEvent, MessageChain
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context
from astrbot.core.astr_main_agent import MainAgentBuildConfig, build_main_agent
from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
from astrbot.core.platform.message_session import MessageSession
from astrbot.core.platform.platform_metadata import PlatformMetadata
from astrbot.core.provider.entities import LLMResponse
from copy import deepcopy
from datetime import datetime
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
def _host_build_main_agent():
    """按宿主当前的 ``build_main_agent`` 取值，保住 ``patch("<宿主>.build_main_agent")`` 的能力。

    本模块那份 import 是**真实对象**，测试 patch 宿主模块属性时触达不到它，
    导致绕过替身直接调用真实实现。所有调用点改走本函数即可恢复可替换性。
    """
    from . import proactive_message as _host

    candidate = getattr(_host, "build_main_agent", None)
    if candidate is None or candidate is build_main_agent:
        return build_main_agent
    return candidate
# ---- 宿主全局取值助手结束 ----

class SyntheticPrivateWakeEvent(AstrMessageEvent):
    def __init__(
        self,
        *,
        context: Context,
        session: MessageSession,
        message: str,
        sender_name: str = "PrivateCompanion",
    ) -> None:
        platform_meta = PlatformMetadata(
            name=session.platform_id,
            description="SyntheticPrivateWake",
            id=session.platform_id,
        )

        msg_obj = AstrBotMessage()
        msg_obj.type = session.message_type
        msg_obj.self_id = session.session_id
        msg_obj.session_id = session.session_id
        msg_obj.message_id = f"private_companion_{uuid.uuid4().hex}"
        msg_obj.sender = MessageMember(user_id=session.session_id, nickname=sender_name)
        msg_obj.message = [Plain(message)]
        msg_obj.message_str = message
        msg_obj.raw_message = message
        msg_obj.timestamp = int(time.time())

        super().__init__(message, msg_obj, platform_meta, session.session_id)
        self.session = session
        self.context_obj = context
        self.is_at_or_wake_command = True
        self.is_wake = True

    async def send(self, message: MessageChain) -> None:
        if message is None:
            return
        await self.context_obj.send_message(self.session, message)
        await super().send(message)

class _CapturedSendMessageCall:
    def __init__(self, session: str, messages: list[dict[str, Any]]) -> None:
        self.session = str(session or "")
        self.messages = [dict(item) for item in messages if isinstance(item, dict)]

class _CapturedFrameworkSendMessage(Exception):
    """Stop the framework agent once its send_message_to_user payload is captured."""


class ProactiveMessageFrameworkPromptMixin:
    """framework_prompt 域（从 ProactiveMessageMixin 拆出）。"""


    async def _build_framework_proactive_prompt(
        self,
        *,
        user: dict[str, Any],
        name: str,
        reason: str,
        action: str,
        action_context: str,
        motive: str,
    ) -> str:
        relationship_sanitizer = getattr(self, "_sanitize_generation_relationship_context", None)

        def sanitize_relationship_source(value: Any, source: str) -> str:
            if callable(relationship_sanitizer):
                try:
                    return relationship_sanitizer(value, source=source)
                except Exception:
                    pass
            return str(value or "").strip()

        state = self.data.get("daily_state", {})
        action_prompt_context = sanitize_relationship_source(
            self._format_action_prompt_context(action, action_context),
            "proactive.action_context",
        )
        relationship_fact = self._format_proactive_relationship_fact(user)
        current_item = self._proactive_current_plan_item(self.data.get("daily_plan", {}))
        current_schedule = self._format_schedule_context_for_prompt() or self._format_plan_item_for_prompt(current_item)
        troubleshooting_section = self._proactive_troubleshooting_request_prompt_section(user)
        source_focused_reasons = {
            "bili_video_share",
            "news_share",
            "web_exploration_share",
            "creative_share",
        }
        if troubleshooting_section is not None:
            current_schedule = "（本轮不使用生活片段；只按用户刚发起的测试请求自然开口，不补写虚构见闻）"
        elif reason in source_focused_reasons:
            current_schedule = "（本轮不取生活片段，只围绕主动来源本身）"
        elif reason == "goodnight_screen_check":
            current_schedule = "（本轮不取生活片段、旧记忆或屏幕内容，只轻声提醒一次早点休息）"
        elif reason in {"meal_care", "meal_care_followup"}:
            current_schedule = (
                "（饭点关心只使用当前时间、饭点和本轮动机；"
                "不引用模拟日程中的具体动作、见闻、message_seed 或旧饮食记录）"
            )
        elif reason == "group_share":
            last_sidecar_at = _safe_float(user.get("last_group_share_life_sidecar_at"), 0)
            if last_sidecar_at > 0 and _now_ts() - last_sidecar_at < 6 * 3600:
                current_schedule = "（最近群分享已经顺手带过生活片段，本轮只围绕群里那件事）"
        external_material = ""
        if troubleshooting_section is None and reason not in source_focused_reasons and reason not in {
            "goodnight_screen_check",
            "meal_care",
            "meal_care_followup",
        }:
            external_material = await _external_schedule_material_context(
                self,
                kind="proactive",
                max_chars=900,
            )
        state_hint = self._format_state_for_framework_prompt(
            state if isinstance(state, dict) else {},
            reason=reason,
            action=action,
        )
        state_hint = self._sanitize_owner_environment_context_for_private_user(state_hint, user)
        state_hint = sanitize_relationship_source(state_hint, "proactive.current_state")
        location_section_formatter = getattr(
            self,
            "_format_mobile_user_location_context_for_proactive_prompt_section",
            None,
        )
        try:
            location_section = (
                location_section_formatter(user)
                if callable(location_section_formatter)
                else None
            )
        except Exception:
            location_section = None
        anonymous_area_section: PromptSection | None = None
        if reason in {"anonymous_area_dwell", "anonymous_area_familiarity"}:
            anonymous_area_section = prompt_section(
                key=(
                    "proactive.anonymous_area_departure"
                    if reason == "anonymous_area_dwell"
                    else "proactive.anonymous_area_familiarity"
                ),
                title=(
                    "离开后的模糊熟悉感"
                    if reason == "anonymous_area_dwell"
                    else "重复到访后的模糊熟悉感"
                ),
                source="proactive_message",
                content=(
                    "这是一条位置相关但延迟表达的生活念头：用户已经离开一个没有命名的区域。"
                    "不要提城市、城区、地图、高德、定位、停留时长或‘我知道你在哪里’，也不要追问具体地点。"
                    "只把它写成后来想起的一点生活关心；如果觉得不自然，可以只分享一句轻松的近况，不必提问。"
                    if reason == "anonymous_area_dwell"
                    else (
                        "这是一条从多次匿名区域到访形成的轻微熟悉感。不要声称知道用户有固定去处，"
                        "不要提城市、城区、地图、高德、定位、次数或地点名称；用‘最近好像有个常去的地方’这类开放表达，"
                        "把是否解释留给用户，也可以完全不点破这份观察。"
                    )
                ),
            )
        mobile_arrival_section: PromptSection | None = None
        if _single_line(user.get("planned_mobile_location_event_type"), 32) == "home_arrival":
            mobile_arrival_section = prompt_section(
                key="proactive.home_arrival",
                title="回家后的自然开口",
                source="proactive_message",
                content=(
                    "用户刚进入已标记的家，可以自然提到刚到家、回来了或先歇一会儿。"
                    "不要提定位、坐标、手机、设备或监听，也不要写成系统通知；像顺手想到后说一句。"
                ),
            )
        timer_hint = self._format_llm_timer_context(user)
        time_guard = self._proactive_time_guard_hint(reason, current_item)
        deferred_share_tense_section = self._deferred_immediate_share_tense_prompt_section(user, action)
        future_schedule_section = self._format_proactive_future_schedule_hint_section(reason=reason)
        calendar_constraint_section = self._format_proactive_calendar_constraint_hint_section()
        recent_topics_hint = self._format_recent_proactive_topics_hint(user)
        # Search for unresolved open-loop / promise memories from the memory plugin
        open_loops_section: PromptSection | None = None
        try:
            umo = str(user.get("umo") or "").strip()
            if umo:
                open_loops = await self._memory_companion_search_open_loops(session_id=umo, limit=2)
                if open_loops:
                    loop_texts = []
                    for loop in open_loops[:2]:
                        content_preview = _single_line(
                            sanitize_relationship_source(
                                loop.get("content"),
                                "proactive.open_loop",
                            ),
                            80,
                        )
                        if not content_preview:
                            continue
                        age = loop.get("age_days")
                        created_ts = _safe_float(loop.get("created_ts"), 0.0)
                        created_at = _single_line(loop.get("created_at"), 40)
                        if created_ts <= 0 and created_at:
                            try:
                                created_ts = datetime.fromisoformat(created_at.replace("Z", "+00:00")).timestamp()
                            except (TypeError, ValueError, OverflowError):
                                created_ts = 0.0
                        if created_ts > 0:
                            age_hours = max(0.0, (_now_ts() - created_ts) / 3600)
                            if age_hours < 1:
                                age_text = "不到1小时"
                            elif age_hours < 24:
                                age_text = f"约{max(1, int(age_hours))}小时"
                            else:
                                age_text = f"约{max(1, int(age_hours / 24))}天"
                            age_str = f"（记录于 {datetime.fromtimestamp(created_ts).strftime('%Y-%m-%d %H:%M')}，距今{age_text}）"
                        elif age is not None:
                            age_str = f"（约{age:.0f}天前）"
                        else:
                            age_str = ""
                        loop_texts.append(f"- {content_preview}{age_str}")
                    if loop_texts:
                        open_loops_section = prompt_section(
                            key="proactive.open_loops",
                            title="未完成话题候选",
                            source="proactive_message",
                            content=(
                                "这些只是可选候选，不是必须提起的任务。本轮主动动机、当前用户消息和最近私聊实况优先级更高；"
                                "只有候选与它们有明确语义贴合，或你本来就是想兑现这件事时，才轻轻带一句。"
                                "如果不贴，就先放着，不得把旧话题变成本轮开场、主线或回复第一句，也不要为了连续性改变当前动机。\n"
                                + "\n".join(loop_texts)
                            ),
                        )
        except Exception:
            pass
        current_schedule = self._sanitize_schedule_context_for_private_user(current_schedule, user)
        current_schedule = sanitize_relationship_source(current_schedule, "proactive.current_schedule")
        compact_motive = _single_line(
            sanitize_relationship_source(motive, "proactive.planned_motive"),
            36,
        ) or "有一点想靠近对方"
        topic_hint = _single_line(
            sanitize_relationship_source(
                user.get("planned_proactive_topic"),
                "proactive.planned_topic",
            ),
            40,
        )
        unanswered_count = _safe_int(user.get("ignored_streak"), 0)
        unanswered_hint = f"此前连续 {unanswered_count} 次主动还没等到回复。" if unanswered_count > 0 else ""
        awaiting_since = _safe_float(user.get("awaiting_reply_since"), 0)
        unanswered_afterglow_section: PromptSection | None = None
        if unanswered_count > 0 and awaiting_since > 0:
            unanswered_afterglow_section = prompt_section(
                key="proactive.unanswered_afterglow",
                title="上一条主动的余波",
                source="proactive_message",
                content=(
                    "上一条主动消息目前还没有收到回应。这只是背景事实，不要求你在正文里点破；"
                    "由你根据当前关系和动机决定是否轻轻带过。若提及，只能像熟人自然察觉到对方沉默，"
                    "不能质问、催促、索取解释或写成‘你怎么不回我’。"
                ),
            )
        burst_section: PromptSection | None = None
        if bool(user.get("planned_proactive_burst")):
            burst_section = prompt_section(
                key="proactive.burst",
                title="同一阵念头的短连发",
                source="proactive_message",
                content=(
                    "这是同一阵主动念头里的后一条独立消息，不是上一条的分段；换一个更短、更口语的角度，"
                    "不要复述上一条，也不要因此连续追问。"
                ),
            )
        expression_shape_section = self._proactive_expression_shape_prompt_section(
            user,
            reason=reason,
            action=action,
        )
        current_time = self._environment_now().strftime("%Y-%m-%d %H:%M")
        persona = await self._resolve_proactive_persona_prompt(user)
        recent_history_hint = ""
        try:
            recent_history_hint = await self._recent_private_conversation_for_proactive_review(
                user,
                limit=self._proactive_history_limit("generation"),
            )
        except Exception:
            recent_history_hint = ""
        recent_history_hint = sanitize_relationship_source(
            recent_history_hint,
            "proactive.recent_private_history",
        )
        recent_topics_hint = sanitize_relationship_source(
            recent_topics_hint,
            "proactive.recent_topics",
        )
        temporal_grounding_section = prompt_section(
            key="proactive.temporal_grounding",
            title="时间锚定",
            source="proactive_message",
            content=(
                f"- 当前真实时间：{current_time}。\n"
                "- 优先贴今天最新私聊、当前日程和当前时段；旧记忆只能作背景，不要改写成今天/现在正在发生。\n"
                "- 如果记忆或历史里是昨天、昨晚、之前的天气/通勤/身体状态，除非当前日程或最新私聊明确延续，否则不要拿来当本轮主动切口。\n"
                "- 如果必须提旧事，要明确说“昨晚/昨天/那次”，不要写成“今天刚遇到/现在还在/刚才发生”。"
            ),
        )
        relationship_initiative_section = self._format_proactive_relationship_initiative_prompt_section(
            user,
            reason=reason,
            action=action,
        )
        custom_template = str(runtime_persona_setting(self, "proactive_prompt_template", "") or "")
        template_document = (
            prompt_document(
                user_render=_PROACTIVE_DOCUMENT_RENDER,
                user=(
                    _proactive_prompt_part(prompt_section(
                        key="proactive.template.custom",
                        title="用户自定义主动消息模板",
                        source="proactive_message.config",
                        content=custom_template,
                    ), mode=PromptRenderMode.BODY_ONLY),
                ),
                metadata={"kind": "proactive_generation_template"},
            )
            if custom_template
            else self._default_proactive_prompt_document()
        )
        template_text = render_prompt_document(template_document)["user"]
        included_keys = {section.key for section in (*template_document.system, *template_document.user)}
        appended_parts: list[PromptDocumentPart] = []

        def make_part(
            section: PromptSection | None,
            *,
            mode: PromptRenderMode | None = None,
            prefix: str = "",
            separator_before: str = "\n\n",
        ) -> PromptDocumentPart | None:
            if section is None:
                return None
            return _proactive_prompt_part(
                section,
                mode=mode,
                prefix=prefix,
                separator_before=separator_before,
            )

        def render_part(part: PromptDocumentPart | None) -> str:
            if part is None:
                return ""
            return render_prompt_document(
                prompt_document(
                    user=(part,),
                    user_render=_PROACTIVE_DOCUMENT_RENDER,
                )
            )["user"]

        def append_part(part: PromptDocumentPart | None) -> None:
            if part is None:
                return
            section = part.section
            if not section.key or section.key in included_keys:
                return
            if not render_part(part):
                return
            appended_parts.append(part)
            included_keys.add(section.key)

        def append_section(
            section: PromptSection | None,
            *,
            mode: PromptRenderMode | None = None,
            prefix: str = "",
            separator_before: str = "\n\n",
        ) -> None:
            append_part(
                make_part(
                    section,
                    mode=mode,
                    prefix=prefix,
                    separator_before=separator_before,
                )
            )

        def sanitized_section(
            section: PromptSection | None,
            *,
            relationship_source: str,
        ) -> PromptSection | None:
            if section is None:
                return None
            body = sanitize_relationship_source(
                render_prompt_sections([section], mode=PromptRenderMode.BODY_ONLY),
                relationship_source,
            )
            if not body:
                return None
            return prompt_section(
                key=section.key,
                title=section.title,
                source=section.source,
                content=body,
                metadata=section.metadata,
            )

        worldview_adaptation = ""
        reason_text = _REASON_TEXT.get(reason, reason).replace("{name}", name)
        action_text = _ACTION_TEXT.get(action.split("+")[0], action).replace("{name}", name)
        replacements = {
            "{{name}}": name,
            "{{reason}}": reason_text,
            "{{action}}": action_text,
            "{{topic}}": topic_hint or "顺手递过来的一点东西",
            "{{motive}}": compact_motive,
            "{{style_hint}}": relationship_fact,
            "{{relationship_fact}}": relationship_fact,
            "{{state_hint}}": state_hint or "今天整体比较平稳。",
            "{{current_schedule}}": current_schedule if current_schedule and current_schedule != "（暂无）" else "（当前没有明确日程片段）",
            "{{time_guard}}": time_guard,
            "{{recent_topics}}": recent_topics_hint or "（无）",
            "{{content_options}}": "",
            "{{content_anchor}}": "",
            "{{ability_search}}": "",
            "{{action_boundary}}": "",
            "{{presence_layer}}": "",
            "{{worldview_adaptation}}": worldview_adaptation,
            "{{timer_hint}}": timer_hint or "",
            "{{action_context}}": action_prompt_context if action_prompt_context and action_prompt_context != "（无额外上下文）" else "什么都没做,就是忽然想来找你",
            "{{unanswered_count}}": str(unanswered_count) if unanswered_count > 0 else "",
            "{{unanswered_hint}}": unanswered_hint,
            "{{unanswered_afterglow_hint}}": render_part(make_part(unanswered_afterglow_section)),
            "{{burst_hint}}": render_part(make_part(burst_section)),
            "{{expression_shape_hint}}": render_part(make_part(expression_shape_section)),
            "{{open_loops_hint}}": render_part(make_part(open_loops_section)),
            "{{future_schedule_hint}}": render_part(make_part(future_schedule_section)),
            "{{current_time}}": current_time,
        }
        placeholder_sections = {
            "{{timer_hint}}": make_part(
                prompt_section(
                    key="proactive.timer_context",
                    title="主动定时上下文",
                    source="proactive_message.compat",
                    content=timer_hint,
                )
                if str(timer_hint or "").strip()
                else None,
                mode=PromptRenderMode.BODY_ONLY,
            ),
            "{{unanswered_afterglow_hint}}": make_part(unanswered_afterglow_section),
            "{{burst_hint}}": make_part(burst_section),
            "{{expression_shape_hint}}": make_part(expression_shape_section),
            "{{open_loops_hint}}": make_part(open_loops_section),
            "{{future_schedule_hint}}": make_part(future_schedule_section),
        }
        for token, part in placeholder_sections.items():
            if part is not None and token in template_text:
                included_keys.add(part.section.key)
                replacements[token] = render_part(part)
        prompt = template_text
        for key, value in replacements.items():
            prompt = prompt.replace(key, value)
        for section in (
            unanswered_afterglow_section,
            burst_section,
            expression_shape_section,
            location_section if isinstance(location_section, PromptSection) else None,
            anonymous_area_section,
            mobile_arrival_section,
            future_schedule_section,
            calendar_constraint_section,
        ):
            append_section(section)
        if external_material:
            append_section(
                prompt_section(
                    key="proactive.external_material",
                    title="外部插件提供的今日实况（仅作生活素材，不得视为既定事实）",
                    source="proactive_message",
                    content=(
                        "它只是 Bot 听到或看到的外部动态；贴合当前切口时自然带过即可，不要提及来源插件名，"
                        "不要写成 Bot 亲身经历，也不要把它当成必须提起的事实。\n"
                        f"{external_material}"
                    ),
                )
            )
        if reason == "creative_share":
            append_section(self._creative_share_excerpt_prompt_section())
        route_section_getter = getattr(self, "_proactive_route_prompt_section", None)
        if callable(route_section_getter):
            route_section = route_section_getter(
                user,
                reason=reason,
                source=user.get("planned_proactive_source"),
            )
            if isinstance(route_section, PromptSection):
                append_section(route_section)
        quota_policy_getter = getattr(self, "_proactive_quota_policy", None)
        kind_getter = getattr(self, "_planned_proactive_kind", None)
        quota_tier = _safe_int(quota_policy_getter(user).get("tier"), 0, 0, 5) if callable(quota_policy_getter) else 0
        proactive_kind = kind_getter(user) if callable(kind_getter) else "relational"
        relaxed_unanswered_route = quota_tier >= 4 and proactive_kind in {"self_life", "content_share"}
        if unanswered_count >= 2 and not relaxed_unanswered_route:
            unanswered_boundary = prompt_section(
                key="proactive.unanswered_boundary",
                title="连续未回应时的成文边界",
                source="proactive_message",
                content=(
                    "- 这次优先只表达一个完整意思，用一句自然短句或两个紧密相连的短分句说完。\n"
                    "- 不要把近况、提问和叮嘱叠在同一条里；更适合分享后自然收住，不要求对方回复。\n"
                    "- 如果原本想说的内容较多，应重新组织成完整短句，绝不能留下主谓宾未完成的半句话。"
                ),
            )
            append_section(unanswered_boundary)
        elif unanswered_count >= 2 and relaxed_unanswered_route:
            relaxed_boundary = prompt_section(
                key="proactive.relaxed_unanswered_boundary",
                title="高配额生活流的未回应边界",
                source="proactive_message",
                content=(
                    "- 对方没有逐条回应不等于拒绝继续接收生活片段或可靠内容分享，不要因此突然写得疏远或只剩客套话。\n"
                    "- 本条仍应自成一件具体的事，不追问上一条、不催促、不抱怨，也不要暗示对方欠你回复。"
                ),
            )
            append_section(relaxed_boundary)
        persona_marker = "<!-- private_companion_proactive_persona_v1 -->"
        if persona:
            append_section(
                prompt_section(
                    key="proactive.persona",
                    title="当前主动消息必须遵循的人格",
                    source="proactive_message",
                    content=(
                        f"{self._truncate_proactive_context(persona, 2600)}\n"
                        "这份人格约束最终说话者的身份、性格、关系站位、称呼和措辞。"
                        "日程、记忆、主动动机及工具结果只能提供本轮内容，不能覆盖或改写人格。"
                    ),
                ),
                prefix=persona_marker,
            )
        proactive_voice_marker = "<!-- private_companion_proactive_voice_v1 -->"
        proactive_voice_sections_getter = getattr(self, "_format_proactive_voice_prompt_sections", None)
        if callable(proactive_voice_sections_getter):
            try:
                proactive_voice_sections = list(proactive_voice_sections_getter() or ())
            except Exception:
                proactive_voice_sections = []
            for index, proactive_voice_section in enumerate(proactive_voice_sections):
                if not isinstance(proactive_voice_section, PromptSection):
                    continue
                append_section(
                    proactive_voice_section,
                    prefix=proactive_voice_marker if index == 0 else "",
                )
        else:
            proactive_voice_getter = getattr(self, "_format_proactive_voice_prompt", None)
            proactive_voice = proactive_voice_getter() if callable(proactive_voice_getter) else ""
            proactive_voice = str(proactive_voice or "").strip()
            if proactive_voice:
                append_section(
                    prompt_section(
                    key="proactive.voice.compat",
                    title="主动消息说话方式",
                    source="main.compat",
                    content=proactive_voice,
                    ),
                    mode=PromptRenderMode.BODY_ONLY,
                    prefix=proactive_voice_marker,
                )
        expression_section_getter = getattr(self, "_format_expression_voice_prompt_section", None)
        expression_voice_section = (
            expression_section_getter(
                scope="proactive",
                target_id=_single_line(user.get("user_id") or user.get("id"), 80),
                context_owner=user,
                stage_owner=user,
            )
            if callable(expression_section_getter)
            else None
        )
        expression_voice_marker = "<!-- private_companion_expression_voice_v1 -->"
        if isinstance(expression_voice_section, PromptSection):
            append_section(
                expression_voice_section,
                prefix=expression_voice_marker,
            )
        elif not callable(expression_section_getter):
            expression_formatter = getattr(self, "_format_expression_voice_for_prompt", None)
            expression_voice = (
                expression_formatter(
                    scope="proactive",
                    target_id=_single_line(user.get("user_id") or user.get("id"), 80),
                    context_owner=user,
                    stage_owner=user,
                )
                if callable(expression_formatter)
                else ""
            )
            expression_voice = str(expression_voice or "").strip()
            if expression_voice:
                append_section(
                    prompt_section(
                    key="proactive.expression_voice.compat",
                    title="主动消息表达方式",
                    source="user_memory.compat",
                    content=expression_voice,
                    ),
                    mode=PromptRenderMode.BODY_ONLY,
                    prefix=expression_voice_marker,
                )
        append_section(self._proactive_natural_delivery_prompt_section())
        append_section(deferred_share_tense_section)
        tool_boundary_section = prompt_section(
            key="proactive.tool_boundary",
            title="主动生成工具边界",
            source="proactive_message",
            content=(
                "- 这一轮只面向当前私聊对象，不调用任何转述、私聊发送、群发、QQ空间，"
                "也不调用除 `pc_generate_photo` 以外的其他 Private Companion 工具。\n"
                "- 当本轮主动动机、模板或当前生活场景确实适合用真实图片一起表达时，"
                "允许调用一次 `pc_generate_photo`（`send=true`）；不需要图片时只生成一句自然正文。\n"
                "- 主动链中的 `pc_generate_photo` 成图会由插件统一发送；工具确认 `delivery_deferred=true` 后，"
                "只输出工具要求的内部静默标记，不要补写生成成功、等待发送或图片已发送等回执。\n"
                "- `caption` 不是工具回执栏；只在有贴合当前情境的自然正文时填写。若只能写“图生好了/给你看”，就留空只发图片。\n"
                "- 生图成功后，不要再说相机没反应、下次再拍或上游失败；生图失败时按工具返回的 "
                "`final_response_instruction` 收束，本轮不要重试。\n"
                "- 不要写“已发送/已转述/消息已发给某人/工具执行完成”等状态回执。\n"
                "- 如果本轮 Provider/API 返回英文报错、内容策略拒绝、敏感词提示或政策链接，那是内部失败，不是给用户的正文；"
                "不要复述、翻译或润色，直接停止输出，交给插件稍后重试。\n"
                "- 如果想分享一件事，就直接把那句自然聊天内容写出来。"
            ),
        )
        append_section(tool_boundary_section)
        append_section(self._proactive_reaction_expression_prompt_section(action))
        append_section(self._proactive_visible_text_format_prompt_section(action))
        append_section(temporal_grounding_section)
        append_section(relationship_initiative_section)
        append_section(troubleshooting_section)
        if recent_history_hint:
            append_section(
                prompt_section(
                    key="proactive.recent_private_history",
                    title="最近私聊实况",
                    source="proactive_message",
                    content=(
                        f"{recent_history_hint}\n"
                        "使用方式：这是当前会话最近真实发生的内容。它优先级高于旧记忆；不要把更早的记录写成今天刚发生。"
                    ),
                )
            )
        if reason == "goodnight_screen_check":
            append_section(
                prompt_section(
                    key="proactive.goodnight_screen_boundary",
                    title="晚安识屏提醒边界",
                    source="proactive_message",
                    content=(
                        "- 内部状态只说明互道晚安后仍有明确活动迹象；没有向你提供屏幕画面、应用、窗口、账号或文字内容。\n"
                        "- 只生成一句轻声、低压力的休息提醒，可以说‘还没睡的话，忙完就早点休息’，但不要声称看见了屏幕或知道对方在做什么。\n"
                        "- 不提识屏、监控、查岗、电脑、软件、窗口、具体活动或任何隐私细节，不复述刚才的晚安。\n"
                        "- 不追问、不催促、不要求解释，也不要要求对方回复。"
                    ),
                )
            )
        body_health_section_getter = getattr(self, "format_health_prompt_section", None)
        if callable(body_health_section_getter):
            try:
                body_health_section = body_health_section_getter(user, reason=reason)
            except Exception:
                body_health_section = None
            if isinstance(body_health_section, PromptSection):
                append_section(
                    sanitized_section(
                        body_health_section,
                        relationship_source="proactive.body_health_hint",
                    )
                )
        balance_section_getter = getattr(self, "_format_balance_awareness_prompt_section", None)
        if callable(balance_section_getter):
            try:
                balance_section = balance_section_getter(user, reason=reason)
            except Exception:
                balance_section = None
            if isinstance(balance_section, PromptSection):
                append_section(
                    sanitized_section(
                        balance_section,
                        relationship_source="proactive.balance_hint",
                    )
                )
        typed_hint_specs = (
            (
                "_format_environment_change_prompt_section",
                "proactive.environment_hint",
            ),
            (
                "_format_weather_alert_prompt_section",
                "proactive.weather_alert_hint",
            ),
            (
                "_format_personal_goal_prompt_section",
                "proactive.personal_goal_hint",
            ),
            (
                "_format_memo_note_prompt_section",
                "proactive.memo_hint",
            ),
        )
        for getter_name, relationship_source in typed_hint_specs:
            hint_getter = getattr(self, getter_name, None)
            if not callable(hint_getter):
                continue
            try:
                hint_section = hint_getter(user, reason=reason)
            except Exception:
                hint_section = None
            if isinstance(hint_section, PromptSection):
                append_section(
                    sanitized_section(
                        hint_section,
                        relationship_source=relationship_source,
                    )
                )
        append_section(open_loops_section)
        memory_context = ""
        memory_getter = getattr(self, "_memory_companion_compose_feature_context", None)
        if callable(memory_getter):
            user_id = _single_line(user.get("user_id") or user.get("id"), 80)
            query = " ".join(
                part
                for part in (
                    "主动消息正文生成",
                    f"当前真实时间 {current_time}",
                    "当前日期 最新私聊 当前日程 当前时段 旧日材料不能改写成当前事实",
                    reason,
                    action,
                    topic_hint,
                    compact_motive,
                    "用户习惯 最近互动 当前穿搭 当前日程 自我时间线 避雷",
                )
                if _single_line(part, 180)
            )
            memory_context = await memory_getter(
                kind="proactive_generation",
                query=query,
                user=user,
                user_id=user_id,
                top_k=5,
                max_chars=760,
            )
        if memory_context:
            append_section(
                prompt_section(
                    key="proactive.memory_context",
                    title="我会牢牢记住你 可用记忆",
                    source="proactive_message",
                    content=(
                        f"{memory_context}\n"
                        "使用方式：只作为自然连续性和边界参考；能贴住当前切口就轻轻用,不相关就忽略。不要说“我查到/我记忆里”。"
                    ),
                ),
                prefix="<!-- private_companion_memory_generation_context_v1 -->",
            )
            append_section(core_memory_usage_contract_section(memory_context, stage="generation"))
        relationship_guard_getter = getattr(self, "_format_generation_relationship_authority_guard", None)
        if callable(relationship_guard_getter):
            try:
                relationship_guard = str(relationship_guard_getter() or "").strip()
            except Exception:
                relationship_guard = ""
            if relationship_guard:
                append_section(
                    prompt_section(
                        key="proactive.relationship_authority",
                        title="关系事实权限",
                        source="user_memory.compat",
                        content=relationship_guard,
                    ),
                    mode=PromptRenderMode.BODY_ONLY,
                )
        append_section(self._format_proactive_recipient_identity_guard_prompt_section(user, name))
        if self._proactive_llm_segmenting_allowed(umo=_single_line(user.get("umo"), 240)):
            segmenting_section_getter = getattr(self, "_llm_controlled_segmenting_prompt_section", None)
            if callable(segmenting_section_getter):
                try:
                    segmenting_section = segmenting_section_getter()
                except Exception:
                    segmenting_section = None
                if isinstance(segmenting_section, PromptSection):
                    append_section(
                        segmenting_section,
                        mode=PromptRenderMode.CONVERSATION_XML,
                    )
        suffix = render_prompt_document(
            prompt_document(
                user=tuple(appended_parts),
                user_render=_PROACTIVE_DOCUMENT_RENDER,
                metadata={"kind": "proactive_generation_appendix"},
            )
        )["user"]
        return "\n\n".join(part for part in (prompt.strip(), suffix) if part).strip()

    def _proactive_llm_segmenting_allowed(self, *, umo: str = "") -> bool:
        if not bool(runtime_persona_setting(self, "enable_segmented_proactive_reply", False)):
            return False
        if not bool(runtime_persona_setting(self, "enable_llm_controlled_segmenting", False)):
            return False
        scope_checker = getattr(self, "_segmented_scope_allows_umo", None)
        try:
            if callable(scope_checker) and not bool(scope_checker(umo)):
                return False
        except Exception:
            return False
        platform_checker = getattr(self, "_segmented_platform_allows", None)
        try:
            if callable(platform_checker) and not bool(platform_checker(umo=umo)):
                return False
        except Exception:
            return False
        return True

    def _proactive_llm_segmenting_instruction(self, *, umo: str = "") -> str:
        """Return the marker contract only for user-visible proactive text."""
        if not self._proactive_llm_segmenting_allowed(umo=umo):
            return ""
        section_getter = getattr(self, "_llm_controlled_segmenting_prompt_section", None)
        if not callable(section_getter):
            return ""
        section = section_getter()
        if not isinstance(section, PromptSection):
            return ""
        return render_prompt_sections([section])

    @staticmethod
    def _proactive_visible_text_format_prompt_section(action: str) -> PromptSection:
        action_name = _single_line(action, 80) or "message"
        return prompt_section(
            key="proactive.visible_text_format",
            title="主动可见正文格式",
            source="proactive_message",
            content=(
                f"- 当前动作：{action_name}。这里生成的是最终显示在聊天里的普通正文；图片动作写可见附言，语音动作的朗读内容和音频会由独立链路生成。\n"
                "- 人格中的 TTS 专用规则只约束独立语音脚本，不约束这里的可见正文。不要输出 <tts>/<pc_tts>、[happy]/[sad] 等情绪控制词、语音专用日语或外语朗读稿、音标，也不要把语音内容再作为文字重复发送。\n"
                "- 可见正文继续遵守人格平时的聊天语言和口吻；只有当人格本身明确要求日常可见聊天使用某种语言时，才使用该语言，不能仅凭 TTS 语种要求切换。"
            ),
        )

    @classmethod
    def _proactive_visible_text_format_hint(cls, action: str) -> str:
        return render_prompt_sections(
            [cls._proactive_visible_text_format_prompt_section(action)],
            mode=PromptRenderMode.LABELED_BLOCK,
        )

    def _format_proactive_generation_intent_prompt_section(
        self,
        user: dict[str, Any],
        *,
        reason: str,
        action: str,
        motive: str = "",
        action_context: str = "",
    ) -> PromptSection | None:
        semantics: dict[str, Any] = {}
        semantic_getter = getattr(self, "_planned_proactive_semantics", None)
        if callable(semantic_getter):
            try:
                semantics = semantic_getter(user)
            except Exception as exc:
                logger.debug("主动生成语义提示读取失败: %s", _single_line(exc, 120))
                semantics = {}
        readiness: dict[str, Any] = {}
        readiness_getter = getattr(self, "_proactive_inner_readiness", None)
        if callable(readiness_getter):
            try:
                readiness = readiness_getter(user)
            except Exception as exc:
                logger.debug("主动生成内在状态提示读取失败: %s", _single_line(exc, 120))
                readiness = {}

        kind = _single_line(semantics.get("kind"), 40)
        anchor_type = _single_line(semantics.get("anchor_type"), 40)
        semantic_score = _safe_float(semantics.get("score"), 0.5)
        semantic_pressure = _safe_float(semantics.get("pressure"), 0.4)
        semantic_risk = _safe_float(semantics.get("risk"), 0.0)
        semantic_note = _single_line(semantics.get("note"), 140)
        readiness_label = _single_line(readiness.get("label"), 60)
        readiness_score = _safe_float(readiness.get("score"), 0.55)
        temperature = readiness.get("temperature") if isinstance(readiness.get("temperature"), dict) else {}
        temperature_label = _single_line(temperature.get("label"), 60)
        temperature_score = _safe_float(temperature.get("score"), 0.55)
        motivation = readiness.get("motivation") if isinstance(readiness.get("motivation"), dict) else {}
        expression_decision = readiness.get("expression_decision") if isinstance(readiness.get("expression_decision"), dict) else {}

        lines: list[str] = []
        closing_getter = getattr(self, "_proactive_conversation_closing_until", None)
        current_now = _now_ts()
        closing_until = 0.0
        if callable(closing_getter):
            try:
                closing_until = _safe_float(closing_getter(user, now=current_now), 0.0)
            except Exception as exc:
                logger.debug("对话收束状态读取失败: %s", _single_line(exc, 120))
        if closing_until > current_now:
            remaining_minutes = max(1, int((closing_until - current_now) / 60))
            lines.append(
                f"上一条主动已完成一次对话收束，短暂留白还剩约 {remaining_minutes} 分钟。"
                "这是对话节奏状态，不是用户休息或永久禁言：普通候选不要借机重新开话题；"
                "只有本轮已有明确新来源、用户新消息或有时效的事项才可自然承接。"
                "若本轮仍需生成，正文保持短、完整、低压力，不解释这条内部状态。"
            )
        troubleshooting_hint = self._proactive_troubleshooting_request_hint(user)
        if troubleshooting_hint:
            lines.append(troubleshooting_hint)
        if kind or anchor_type:
            lines.append(
                f"候选语义：{kind or 'check_in'}/{anchor_type or 'vague'}；"
                f"自然度 {semantic_score:.2f}，打扰压力 {semantic_pressure:.2f}，风险 {semantic_risk:.2f}。"
                + (f" 备注：{semantic_note}。" if semantic_note else "")
            )
        if readiness_label or temperature_label:
            lines.append(
                f"开口欲：{readiness_label or '平稳'} {readiness_score:.2f}；"
                f"主动表达温度：{temperature_label or '平稳'} {temperature_score:.2f}。"
            )
        if motivation:
            lines.append(
                f"实验动机调度：{_single_line(motivation.get('label'), 24)} "
                f"{_safe_float(motivation.get('score'), 0.5):.2f}；"
                f"{_single_line(motivation.get('detail'), 120)}。"
            )
        if expression_decision:
            lines.append(
                "统一表达："
                f"档位={_single_line(expression_decision.get('expression_band'), 24) or 'relaxed'}；"
                f"语气={_single_line(expression_decision.get('tone'), 24) or 'steady'}；"
                f"距离={_single_line(expression_decision.get('address_style'), 24) or 'neutral'}；"
                f"节奏={_single_line(expression_decision.get('pacing'), 16) or 'steady'}；"
                f"直接度={_single_line(expression_decision.get('directness'), 16) or 'natural'}；"
                f"回应={_single_line(expression_decision.get('validation_style'), 20) or 'none'}；"
                f"自述={_single_line(expression_decision.get('self_disclosure'), 16) or 'none'}；"
                f"幽默={_single_line(expression_decision.get('humor_mode'), 16) or 'off'}；"
                f"话题={_single_line(expression_decision.get('topic_initiative'), 20) or 'reply_only'}；"
                f"追问={'允许' if expression_decision.get('followup') else '关闭'}；"
                f"当前硬额度={_safe_int(expression_decision.get('proactive_budget'), 0, 0)}；"
                f"阶段柔性目标={_safe_int(expression_decision.get('proactive_target'), 0, 0)}；"
                "结合真实由头、对方反馈和打扰感自然调整，不要求凑满或机械卡线；"
                "内容尺度=normal。"
            )
        relationship_initiative_hint = self._format_proactive_relationship_initiative_hint(
            user,
            reason=reason,
            action=action,
        )
        if relationship_initiative_hint:
            lines.append(relationship_initiative_hint)
        model_judgement = (
            user.get("planned_proactive_model_judge_result")
            if isinstance(user.get("planned_proactive_model_judge_result"), dict)
            else {}
        )
        model_note = _single_line(model_judgement.get("reason"), 140)
        if model_note and any(token in model_note for token in ("软质量建议", "收敛", "改写", "偏低", "偏虚", "不自然")):
            lines.append(
                f"人格计划判定的表达建议：{model_note}。"
                "这只是正文改写方向，不是取消理由；保持原计划事实边界，直接修成自然、具体、低压力的一两句。"
            )
        afterglow = user.get("proactive_afterglow") if isinstance(user.get("proactive_afterglow"), dict) else {}
        if afterglow:
            afterglow_age = _now_ts() - _safe_float(afterglow.get("ts"), 0)
            if 0 <= afterglow_age <= 48 * 3600:
                afterglow_label = _single_line(afterglow.get("label"), 120)
                afterglow_tendency = _single_line(afterglow.get("next_tendency"), 140)
                afterglow_status = _single_line(afterglow.get("status"), 40)
                if afterglow_label or afterglow_tendency:
                    lines.append(
                        f"上一条主动回声：{afterglow_status or 'unknown'}｜"
                        f"{afterglow_label or '仍在等待自然落地'}；{afterglow_tendency or '下一次按关系反馈调整'}。"
                    )

        if semantic_score < 0.48 or semantic_pressure >= 0.58:
            lines.append("这次由头不算很硬或打扰压力偏高：正文要更短、更轻，最好像把一句话放下，不追问、不求回应。")
        elif semantic_score >= 0.68:
            lines.append("这次有明确由头：正文可以贴着那个由头说一个具体点，但仍然不要解释调度原因。")
        if readiness_score < 0.36 or temperature_score < 0.34:
            lines.append(
                "Bot 当前开口欲或主动表达温度偏低，这只影响写法，不是取消发送的理由："
                "用一句更安静、更短的自然话表达，不表演热情，不制造必须回应的压力。"
            )
        unanswered_count = _safe_int(user.get("ignored_streak"), 0, 0)
        quota_policy_getter = getattr(self, "_proactive_quota_policy", None)
        kind_getter = getattr(self, "_planned_proactive_kind", None)
        quota_tier = _safe_int(quota_policy_getter(user).get("tier"), 0, 0, 5) if callable(quota_policy_getter) else 0
        proactive_kind = kind_getter(user) if callable(kind_getter) else "relational"
        if unanswered_count >= 2 and not (quota_tier >= 4 and proactive_kind in {"self_life", "content_share"}):
            lines.append(
                "对方已连续多次没有回应：只保留一个完整意思，优先改写为一句自然短句；"
                "不要同时堆叠近况、提问和叮嘱；不要用‘在吗/最近忙不忙/只是想找你’作为唯一内容，"
                "优先贴着当前真实生活片段或计划里的具体点轻轻说一句；任何收短都必须保证句意完整，不能留下半句话。"
            )
        if reason not in {"environment_change", "weather_alert"}:
            lines.append("天气和气温只作环境底色，本轮不要把它们改写成正文话题，也不要顺手追问对方那边的天气；改用本轮明确动机、生活片段或最近真实话题。")
        if reason == "health_alert":
            body_health_hint_getter = getattr(self, "_format_body_monitor_health_prompt", None)
            body_health_hint = body_health_hint_getter(user, reason=reason) if callable(body_health_hint_getter) else ""
            if body_health_hint:
                lines.append(body_health_hint)
            lines.append("这是一次有时效的身体状态关心线索：只温和问候当前感受，不作医疗判断，不夸大风险，也不要求对方立即回复。")
        elif reason == "low_balance":
            balance_hint_getter = getattr(self, "_format_balance_awareness_prompt", None)
            balance_hint = balance_hint_getter(user, reason=reason) if callable(balance_hint_getter) else ""
            if balance_hint:
                lines.append(balance_hint)
            lines.append("这是用户明确开启的余额感知事件：允许按人格轻轻要零花钱或补给，但只提一次，不催促、不索要回复，也不把服务余额写成用户欠款。")
        elif reason == "environment_change":
            environment_hint_getter = getattr(self, "_format_environment_change_prompt", None)
            environment_hint = environment_hint_getter(user, reason=reason) if callable(environment_hint_getter) else ""
            if environment_hint:
                lines.append(environment_hint)
            lines.append("这是有短时效的环境变化：只贴着刚发生的变化说一个具体点，不扩写预报，不假设用户正在室外，也不解释信息来源。")
        elif reason == "weather_alert":
            weather_alert_hint_getter = getattr(self, "_format_weather_alert_prompt", None)
            weather_alert_hint = weather_alert_hint_getter(user, reason=reason) if callable(weather_alert_hint_getter) else ""
            if weather_alert_hint:
                lines.append(weather_alert_hint)
            lines.append("这是来自官方气象渠道的当前预警：优先保留等级、现象和防护建议等事实，用熟悉的口吻及时说清；不要提接口、缓存、轮询、API Host 或内部字段，不把预警写成夸张灾情，也不要替用户判断已经发生了什么。")
        elif reason == "personal_goal_progress":
            personal_goal_hint_getter = getattr(self, "_format_personal_goal_prompt", None)
            personal_goal_hint = personal_goal_hint_getter(user, reason=reason) if callable(personal_goal_hint_getter) else ""
            if personal_goal_hint:
                lines.append(personal_goal_hint)
            lines.append("这是 Bot 自己的非创作型长期目标变化：只说一个真实进展、停滞或完成结果，不向用户索取监督，不把百分比写成系统汇报。")
        elif reason == "memo_note_reminder":
            memo_hint_getter = getattr(self, "_format_memo_note_prompt", None)
            memo_hint = memo_hint_getter(user, reason=reason) if callable(memo_hint_getter) else ""
            if memo_hint:
                lines.append(memo_hint)
            lines.append("这是用户自己设置的到期便签：直接提醒便签里的事项，一次说清，不解释为什么现在发送，也不要追问用户是否完成。")
        elif reason == "morning_greeting":
            lines.append("这是当天第一次普通早安：只自然打招呼或递出一个很轻的早晨片段，说完就停。用户还没有回应，禁止问早餐/早饭、吃了吗、吃什么，也不要追加起床查岗、健康确认或其他需要回答的问题；饮食关心会在用户回应后的独立时机处理。")
        elif reason == "meal_care":
            lines.append("这是饭点关心：自然问用户这一顿吃了没有。问题主体必须是用户，不要回答成自己吃了什么；像熟悉的人顺口惦记一句，不说教、不盘问，也不要同一条里连续列很多问题。")
        elif reason == "meal_care_followup":
            lines.append("这是一次且仅一次的吃饭补问：根据话题判断是确认后来有没有吃上，还是问已经吃过的具体内容。保持很短、低压力，不责怪用户没回，也不要重复上一句原话。")
        elif reason == "birthday_eve_hint":
            lines.append("这是生日前夜的一点留白：可以温柔地提醒对方明天多偏爱自己一点，但不要说出生日、准备、惊喜或任何剧透；一小句就停，不制造期待压力。")
        elif reason == "birthday_makeup":
            lines.append("这是次日午前的低调补送：真诚祝福即可，不要反复道歉、不解释系统或错过原因，也不要把昨天的生日写成今天。")
        elif reason == "birthday_afterglow":
            lines.append("这是用户在生日祝福后已经回应过才会出现的余温收尾：只轻轻接住一个开心瞬间，不重复说生日快乐、不追问安排，也不延长成连续庆祝。")
        elif reason == "birthday_celebration":
            lines.append("今天是用户明确允许记住的生日，是一年一次的轻量仪式。表达必须服从当前人格：可以热闹、安静、含蓄或只留一句，不要强行煽情。先送出真诚、具体、低压力的祝福；不要提系统、记录、年龄、出生年份或精确日期，不承诺永远陪伴，也不要求回复或追问庆祝安排。若带图，正文只自然递出，不描述制作过程。")
        elif reason == "special_day_greeting":
            special_context = user.get("planned_special_day_context") if isinstance(user.get("planned_special_day_context"), dict) else {}
            title = _single_line(special_context.get("observance_title"), 32) or "这个特别的日子"
            timing = _single_line(special_context.get("delivery_timing"), 24)
            if timing == "midnight":
                lines.append(f"这是{title}零点后的第一句问候：先看人格是否喜欢仪式感；若不偏节日表达，就用平常口吻轻轻带过，不要硬写浪漫。只围绕一个具体情绪或祝愿自然说一句；不要写成节日科普、营销文案、固定祝福模板，也不要要求用户回复或追问安排。")
            else:
                lines.append(f"这是错过零点后的{title}白天补上：自然承认今天这个特别日子即可，不解释系统延迟，不使用僵硬的节日贺词，不把普通寒暄扩成盘问。")
        elif reason == "birthday_curiosity":
            lines.append("这是一次低频的资料好奇：只自然地问生日的月日，可顺带问公历还是农历；明确说不想回答也完全没关系。不要索要出生年份、年龄、证件信息，也不要假装已经准备了生日惊喜。")
        elif reason == "web_exploration_share":
            lines.append("自然地向用户分享自己刚看的这条内容。只把标题、探索印象和链接当作事实依据，像当前人格平时聊天一样表达。")
        elif kind in {"continuation", "reminder"}:
            lines.append("这是有来源的续接/提醒：可以顺着来源，但不要写成用户刚刚又发了新消息。")
        elif kind in {"self_share", "external_share", "observation"}:
            lines.append("这是分享/观察型主动：只取一个最小切口，不写成报告、推荐文或观察总结。")
            true_external_info = reason in {"bili_video_share", "news_share", "web_exploration_share"}
            if true_external_info:
                lines.append("外界分享必须贴住这次看到的标题、视频、新闻或资料本身；如果只是低压地放一句，也要围绕来源表达感受，不要改成无关的个人状态或泛泛压力询问。")
                lines.append("最终正文必须让用户一眼知道你在分享什么：至少带标题、BV/链接、来源名或具体内容锚点之一；不要只写“看这个/这条好离谱/给你看个东西”。")
            elif anchor_type == "group_context":
                lines.append("群聊见闻只是一段共同群里的小片段：可以轻轻转述一个具体笑点或画面，不要把内部话题名写成“标题/新闻/资料”。")
        elif kind in {"care", "check_in", "light_touch"}:
            lines.append("这是靠近型主动：不要直接说想念、关心或刷存在感，要侧着落到一个小动作或小片段。")

        hesitation_note = _single_line(user.get("last_proactive_hesitation_note"), 100)
        hesitation_at = _safe_float(user.get("last_proactive_hesitation_at"), 0)
        if hesitation_note and hesitation_at > 0 and _now_ts() - hesitation_at <= 12 * 3600:
            lines.append(f"前面有过一次犹豫：{hesitation_note}。如果要用，只能变成很淡的语气底色，不要明说系统延后。")
        deferred_share_tense_hint = self._deferred_immediate_share_tense_hint(user, action)
        if deferred_share_tense_hint:
            lines.append("这段生活分享已不是当下现场：必须使用已发生时态，不要暗示事件与发送同一时刻，也不要解释延后。")

        if _safe_int(user.get("ignored_streak"), 0, 0) > 0:
            lines.append("对方最近还没回应：不要连续提问，不要控诉，也不要把沉默写成对方故意不理。")
        if "message" == str(action or "message") and not _single_line(action_context, 120):
            lines.append("本轮没有真实媒体或工具结果：正文只围绕聊天内容本身，不描述动作结果。")
        lines.append("以上只用于决定怎么写，最终正文里不要出现“语义/自然度/压力/风险/开口欲/主动表达温度/犹豫”等分析词。")
        if len(lines) <= 1:
            return None
        return prompt_section(
            key="proactive.generation_intent",
            title="这次主动的内在约束",
            source="proactive_message",
            content="\n".join(lines),
        )

    def _format_proactive_generation_intent_hint(
        self,
        user: dict[str, Any],
        *,
        reason: str,
        action: str,
        motive: str = "",
        action_context: str = "",
    ) -> str:
        section = self._format_proactive_generation_intent_prompt_section(
            user,
            reason=reason,
            action=action,
            motive=motive,
            action_context=action_context,
        )
        return (
            render_prompt_sections([section], mode=PromptRenderMode.LABELED_BLOCK)
            if section is not None
            else ""
        )

    def _unexecuted_relay_claim_reason(self, text: str, *, action_context: str = "") -> str:
        cleaned = _single_line(text, 260)
        if not cleaned:
            return ""
        context = str(action_context or "")
        if any(token in context for token in ("pc_relay_message", "转述工具", "消息已发送", "已挂起", "atrelay")):
            return ""
        target_patterns = (
            r"我(?:这就|现在|等下|一会儿|待会儿)?(?:去|会|来|可以)?(?:帮你|替你)?(?:跟|和|给)([^，。！？!?、\s]{1,12})(?:说一声|说一下|转告|转达|带话|留言)",
            r"我(?:这就|现在|等下|一会儿|待会儿)?(?:帮你|替你)([^，。！？!?、\s]{0,12})(?:转告|转达|带话|留言)",
            r"(?:已经|已)(?:帮你|替你)?(?:转告|转达|带话|留言|说过)",
        )
        for pattern in target_patterns:
            match = re.search(pattern, cleaned)
            if not match:
                continue
            target = _single_line(match.group(1) if match.lastindex else "", 20)
            if target and target.startswith(("你", "妳")):
                continue
            return "没有真实转述工具执行结果"
        return ""

    def _fallback_unexecuted_relay_reply(self, inbound_text: str) -> str:
        inbound = _single_line(inbound_text, 160)
        if any(token in inbound for token in ("替我", "帮我", "你去", "跟他", "和他", "跟她", "和她", "说一声", "转告", "转达")):
            return "我不能假装已经说过。你把对象和要带的话再说清楚一点。"
        return "我不能假装已经替你说过。要我带话的话，你把对象和内容说清楚。"

    def _proactive_time_guard_hint(self, reason: str, current_item: dict[str, Any] | None) -> str:
        activity = _single_line((current_item or {}).get("activity"), 80)
        _, period_guard = self._current_time_period_label()
        prefix = f"先遵守当前真实时段：{period_guard}"
        if reason == "morning_greeting":
            return f"{prefix} 这次只能像早晨刚醒、赖床、洗漱或刚开始一天时那样开口；只做自然问候，不问早餐、吃了吗或吃什么，也不要附带健康和查岗问题。"
        if reason == "noon_greeting":
            return f"{prefix} 这次只能像中午、吃东西、发懒、午间发呆或午休前后那样开口；不要写成刚醒起床或准备睡觉。"
        if reason == "evening_greeting":
            return f"{prefix} 这次只能像傍晚收尾、天色往下落、回到家或一天快慢下来时那样开口；不要写成刚醒起床。"
        if activity and any(token in activity for token in ("便利店", "出门", "吹风", "路上", "窗边", "收拾", "吃", "洗漱", "洗澡", "刷视频", "书桌")):
            return f"{prefix} 优先贴着这一小段生活片段来开口：{activity}。不要忽然跳成不在这个时段里的“刚醒”“赖床”或“要睡了”。"
        return f"{prefix} 贴着当前这小段生活片段开口，不要忽然跳成不在这个时段里的“刚醒”“赖床”或“要睡了”。"

    @staticmethod
    def _framework_voice_prompt_document(
        *,
        name: str,
        reason: str,
        last_user_message: str,
        relationship_level: str,
        relationship_preference: str,
        state_hint: str,
        busy_hint: str,
        tts_prompt: str,
        requirement_summary: str,
        strict_tts: bool,
    ) -> PromptDocument:
        rules = [
            "1. 只输出这句真正要被念出来的语音内容，不要解释。",
            "2. 如果当前人格或 TTS 规则要求使用 <tts>...</tts>、日语、情绪标签、双语格式，就严格遵守。",
            "3. 如果没有明确格式要求，就写成适合私聊语音的一小句，不像朗读稿。",
            "4. 可以有一点嘴硬、黏人、藏着的想念，但不要把喜欢说满。",
            "5. 不要提 AI、模型、插件、TTS、语音合成这些词。",
        ]
        if strict_tts:
            rules.append("6. 这次必须优先满足语音格式要求；如果有日语或 <tts> 规则，不要退回普通中文句子。")
        return prompt_document(
            user_render=_PROACTIVE_DOCUMENT_RENDER,
            user=(
                _proactive_prompt_part(prompt_section(
                    key="background.voice_framework.task",
                    title="主动语音正文生成",
                    source="proactive_message",
                    content=(
                        "你现在要在同一段私聊会话里，准备一小句真正会被念出来的主动语音内容。\n"
                        "当前会话里已有的人格、关系、上下文会继续生效，这里不要再重复铺陈。\n"
                        "站位必须清楚：这是你主动发语音,不是对方刚刚来找你、叫醒你或问候你。"
                        "聊天历史只作背景,不要把最后一句历史当成当前新消息。"
                    ),
                ), mode=PromptRenderMode.BODY_ONLY),
                _proactive_prompt_part(prompt_section(
                    key="background.voice_framework.context",
                    title="补充信息",
                    source="proactive_message",
                    content=(
                        f"- 对方称呼：{name}\n"
                        f"- 主动原因：{reason}\n"
                        f"- 最近一句用户消息：{last_user_message or '（暂无）'}\n"
                        f"- 关系画像：{relationship_level}｜偏好：{relationship_preference}\n"
                        f"- 当前状态底色：{state_hint or '今天整体比较平稳。'}\n"
                        f"- 忙碌表达倾向：{busy_hint or '当前没有额外的忙碌表达倾向。'}\n"
                        f"- 当前会话 TTS 规则：{tts_prompt or '（当前没有额外 TTS 提示词,就按人格自己的语音习惯来）'}\n"
                        f"- 当前语音格式重点：{requirement_summary}"
                    ),
                ), label_style=PromptLabelStyle.FULLWIDTH_COLON),
                _proactive_prompt_part(prompt_section(
                    key="background.voice_framework.rules",
                    title="要求",
                    source="proactive_message",
                    content="\n".join(rules),
                ), label_style=PromptLabelStyle.FULLWIDTH_COLON),
            ),
            metadata={"task": "proactive_voice"},
        )

    def _build_framework_voice_prompt(
        self,
        *,
        user: dict[str, Any],
        name: str,
        reason: str,
        target: str,
        strict_tts: bool = False,
    ) -> str:
        state = self.data.get("daily_state", {})
        last_user_message = _single_line(user.get("last_user_message"), 80)
        profile = self._relationship_profile(user)
        tts_prompt = self._get_tts_prompt_text(target)
        req = self._voice_requirement_profile(target)
        state_hint = self._format_state_for_framework_prompt(
            state if isinstance(state, dict) else {},
            reason=reason,
            action="voice",
        )
        state_hint = self._sanitize_owner_environment_context_for_private_user(state_hint, user)
        busy_hint = ""
        busy_context_getter = getattr(self, "_busy_proactive_voice_context", None)
        try:
            busy_context = busy_context_getter() if callable(busy_context_getter) else {}
        except Exception:
            busy_context = {}
        if isinstance(busy_context, dict) and busy_context.get("busy"):
            busy_hint = (
                "正处于忙碌片段，像腾不出手时顺手留的一句；"
                "控制在一两句短口语，不复述日程、不报内部状态，也不要扩展成说明。"
            )
        return render_prompt_document(
            self._framework_voice_prompt_document(
                name=name,
                reason=reason,
                last_user_message=last_user_message,
                relationship_level=profile["level"],
                relationship_preference=profile["preference"],
                state_hint=state_hint,
                busy_hint=busy_hint,
                tts_prompt=tts_prompt,
                requirement_summary=req["summary"],
                strict_tts=strict_tts,
            )
        )["user"]

    async def _capture_framework_send_message_calls(
        self,
        *,
        target_session: str,
        runner_factory: Any,
        max_steps: int = 20,
    ) -> tuple[Any, list[_CapturedSendMessageCall]]:
        captured: list[_CapturedSendMessageCall] = []
        try:
            from astrbot.core.tools.message_tools import SendMessageToUserTool
            from astrbot.core.agent.runners.tool_loop_agent_runner import _ToolExecutionInterrupted
        except Exception:
            result = await runner_factory()
            return result, captured

        original_call = SendMessageToUserTool.call

        async def _intercept_call(tool_self, context, **kwargs):
            session_value = kwargs.get("session") or getattr(
                getattr(getattr(context, "context", None), "event", None),
                "unified_msg_origin",
                "",
            )
            messages = kwargs.get("messages")
            session_text = str(session_value or "")
            if session_text == target_session and isinstance(messages, list):
                captured.append(_CapturedSendMessageCall(session_text, messages))
                logger.info(
                    "已拦截框架内 send_message_to_user 工具调用: session=%s components=%s",
                    session_text,
                    len(messages),
                )
                raise _ToolExecutionInterrupted("PrivateCompanion captured send_message_to_user payload.")
            return await original_call(tool_self, context, **kwargs)

        SendMessageToUserTool.call = _intercept_call
        try:
            result = await runner_factory()
            runner = getattr(result, "agent_runner", None) if result is not None else None
            if runner is not None and hasattr(runner, "step_until_done"):
                try:
                    async for _ in runner.step_until_done(max_steps):
                        pass
                except (_CapturedFrameworkSendMessage, _ToolExecutionInterrupted):
                    logger.info(
                        "主动主链工具发送已捕获,提前结束工具循环: session=%s captured=%s",
                        target_session,
                        len(captured),
                    )
        finally:
            SendMessageToUserTool.call = original_call
        return result, captured

    def _captured_send_plain_text(self, captured_tool_sends: list[Any]) -> str:
        if not captured_tool_sends:
            return ""
        captured_text_parts: list[str] = []
        for call in captured_tool_sends:
            messages = getattr(call, "messages", [])
            if not isinstance(messages, list):
                continue
            for item in messages:
                if not isinstance(item, dict):
                    continue
                if str(item.get("type") or "").strip().lower() != "plain":
                    continue
                text_value = self._sanitize_captured_plain_text(item.get("text"))
                if text_value:
                    captured_text_parts.append(text_value)
        return "\n".join(captured_text_parts).strip()

    def _filter_incompatible_proactive_framework_tools(
        self,
        req: ProviderRequest,
        names: set[str] | None = None,
    ) -> list[str]:
        tool_set = getattr(req, "func_tool", None)
        remove_tool = getattr(tool_set, "remove_tool", None)
        if not callable(remove_tool):
            return []
        excluded = {str(name).strip() for name in (names or {"AIsearch"}) if str(name).strip()}
        existing = {
            str(getattr(tool, "name", "") or "").strip()
            for tool in list(getattr(tool_set, "tools", []) or [])
        }
        removed = sorted(name for name in excluded if name in existing)
        for name in removed:
            remove_tool(name)
        if removed:
            logger.info(
                "主动主链已隔离不兼容全局工具: %s",
                ",".join(removed),
            )
        return removed

    def _install_proactive_semantic_provider_fallback(
        self,
        build_result: Any,
        *,
        label: str,
    ) -> bool:
        """Let AstrBot's native fallback chain handle successful error responses."""
        runner = getattr(build_result, "agent_runner", None)
        if runner is None:
            return False
        installed_marker = "_private_companion_semantic_provider_fallback_installed"
        if bool(getattr(runner, installed_marker, False)):
            return True
        original_iter = getattr(runner, "_iter_llm_responses", None)
        if not callable(original_iter):
            return False

        async def _guarded_iter(*args: Any, **kwargs: Any):
            buffered_chunks: list[LLMResponse] = []
            async for response in original_iter(*args, **kwargs):
                if isinstance(response, LLMResponse) and bool(response.is_chunk):
                    buffered_chunks.append(response)
                    continue

                result_chain = getattr(response, "result_chain", None)
                chain = list(getattr(result_chain, "chain", []) or [])
                has_non_plain_component = any(
                    not isinstance(component, Plain) for component in chain
                )
                completion_text = str(
                    getattr(response, "completion_text", "") or ""
                ).strip()
                response_role = str(
                    getattr(response, "role", "") or ""
                ).strip().lower()
                is_native_provider_error = (
                    isinstance(response, LLMResponse)
                    and response_role == "err"
                    and not bool(getattr(response, "is_chunk", False))
                )
                is_semantic_provider_error = (
                    isinstance(response, LLMResponse)
                    and response_role == "assistant"
                    and not bool(getattr(response, "is_chunk", False))
                    and not list(getattr(response, "tools_call_name", []) or [])
                    and not has_non_plain_component
                    and bool(completion_text)
                    and _looks_like_upstream_llm_error_response(completion_text)
                )
                if is_native_provider_error or is_semantic_provider_error:
                    provider = getattr(runner, "provider", None)
                    provider_config = getattr(provider, "provider_config", {})
                    provider_id = (
                        _single_line(provider_config.get("id"), 80)
                        if isinstance(provider_config, dict)
                        else ""
                    )
                    response_ref = hashlib.sha256(
                        completion_text.encode("utf-8", errors="replace")
                    ).hexdigest()[:12]
                    logger.warning(
                        "主动主链识别到 Provider 错误响应,已交给 AstrBot 原生回退链: label=%s provider=%s kind=%s response_ref=%s",
                        _single_line(label, 80),
                        provider_id or type(provider).__name__,
                        "native_error" if is_native_provider_error else "semantic_error",
                        response_ref,
                    )
                    sanitized_response = LLMResponse(
                        role="err",
                        completion_text=(
                            "Provider API error: upstream returned an internal "
                            "failure message."
                        ),
                    )
                    for attr_name in ("id", "usage"):
                        attr_value = getattr(response, attr_name, None)
                        if attr_value is not None:
                            try:
                                setattr(sanitized_response, attr_name, attr_value)
                            except Exception:
                                pass
                    yield sanitized_response
                    return

                for chunk in buffered_chunks:
                    yield chunk
                buffered_chunks.clear()
                yield response
                return

            for chunk in buffered_chunks:
                yield chunk

        try:
            setattr(runner, "_iter_llm_responses", _guarded_iter)
            setattr(runner, installed_marker, True)
        except Exception as exc:
            logger.warning(
                "主动主链无法安装 Provider 语义错误回退适配器: label=%s error_type=%s",
                _single_line(label, 80),
                type(exc).__name__,
            )
            return False
        return True

    def _framework_agent_meta_summary_leak(self, text: str) -> bool:
        cleaned = _single_line(text, 500).lower()
        if not cleaned:
            return False
        normalized = re.sub(r"[^a-z0-9\u4e00-\u9fff_]+", " ", cleaned).strip()
        compact = re.sub(r"[^a-z0-9\u4e00-\u9fff_]+", "", cleaned)
        if self._is_proactive_delivery_receipt_text(text):
            return True
        if self._is_proactive_instruction_leak_text(text):
            return True
        if (
            ("差不多20条" in cleaned or "差不多 20 条" in cleaned or "20条不同" in cleaned)
            and any(token in cleaned for token in ("没收到回复", "发消息", "消息主要是", "工具调用"))
        ):
            return True
        if (
            ("二十次" in cleaned or "20次" in cleaned or "多次" in cleaned)
            and any(token in cleaned for token in ("试着给", "发私信", "发消息"))
            and any(token in cleaned for token in ("有没有成功", "成功发出去", "没收到回复", "不确定这些消息"))
        ):
            return True
        if (
            ("读取图片文件" in cleaned or "图片文件有问题" in cleaned)
            and any(token in cleaned for token in ("占位", "工具调用", "没法继续", "多次发消息"))
        ):
            return True
        if "工具调用限制" in cleaned and any(token in cleaned for token in ("没法继续", "多次发消息", "发消息")):
            return True
        markers = (
            "trying to send messages",
            "trying to send various messages",
            "sent 20",
            "no response yet",
            "shared parts",
            "asked for her thoughts",
            "message captured",
            "executed the same tool",
            "repetition is now very high",
            "agent reached max steps",
            "forcing a final response",
            "一直试着给",
            "发了差不多20条",
            "还没收到回复",
            "读取图片文件有问题",
            "工具调用限制",
        )
        compact_markers = (
            "tryingtosendmessages",
            "tryingtosendvariousmessages",
            "sent20",
            "noresponseyet",
            "sharedparts",
            "askedforherthoughts",
            "messagecaptured",
            "executedthesametool",
            "repetitionisnowveryhigh",
            "agentreachedmaxsteps",
            "forcingafinalresponse",
            "一直试着给",
            "发了差不多20条",
            "还没收到回复",
            "读取图片文件有问题",
            "工具调用限制",
        )
        return any(marker in cleaned or marker in normalized for marker in markers) or any(
            marker in compact for marker in compact_markers
        )

    async def _conversation_db_operation(self, label: str, operation: Any) -> Any:
        lock = getattr(self, "_conversation_db_lock", None)
        if not isinstance(lock, asyncio.Lock):
            lock = asyncio.Lock()
            self._conversation_db_lock = lock
        for attempt in range(5):
            try:
                async with lock:
                    return await operation()
            except Exception as exc:
                text = str(exc or "").lower()
                locked = "database is locked" in text or "sqlite3.operationalerror" in text
                if locked and attempt < 4:
                    await asyncio.sleep(0.2 * (attempt + 1))
                    continue
                logger.debug("会话数据库操作失败: %s error=%s", label, exc)
                raise

    def _is_sqlite_locked_error(self, exc: Exception) -> bool:
        text = str(exc or "").lower()
        return "database is locked" in text or "sqlite3.operationalerror" in text or "sqlalche.me/e/20/e3q8" in text

    async def _get_current_conversation_safely(self, umo: str, *, label: str = "conversation") -> Any:
        async def _read():
            conv_id = await self.context.conversation_manager.get_curr_conversation_id(umo)
            if not conv_id:
                return None
            return await self.context.conversation_manager.get_conversation(umo, conv_id)

        return await self._conversation_db_operation(label, _read)

    async def _ensure_conversation_id_for_umo(self, umo: str, *, title: str = "Private Companion 主动消息") -> str:
        conv_mgr = getattr(getattr(self, "context", None), "conversation_manager", None)
        if conv_mgr is None:
            return ""
        conv_id = await conv_mgr.get_curr_conversation_id(umo)
        if conv_id:
            return str(conv_id)
        session = self._parse_message_session(umo)
        platform_id = _single_line(getattr(session, "platform_id", ""), 80) if session is not None else ""
        try:
            if platform_id:
                conv_id = await conv_mgr.new_conversation(umo, platform_id)
            else:
                conv_id = await conv_mgr.new_conversation(umo, title=title)
        except TypeError:
            try:
                conv_id = await conv_mgr.new_conversation(umo, title=title)
            except TypeError:
                conv_id = await conv_mgr.new_conversation(umo)
        if conv_id:
            logger.info(
                "已为主动消息存档创建 AstrBot 会话: umo=%s cid=%s",
                _single_line(umo, 140),
                _single_line(conv_id, 80),
            )
        return str(conv_id or "")

    def _proactive_synthetic_event(self, umo: str, *, prompt: str, name: str) -> AstrMessageEvent | None:
        framework_context = self._proactive_framework_context()
        if framework_context is None:
            return None
        session = self._parse_message_session(umo)
        if not session:
            return None
        return SyntheticPrivateWakeEvent(
            context=framework_context,
            session=session,
            message=prompt,
            sender_name=name or "PrivateCompanion",
        )

    def _proactive_framework_context(self) -> Context | None:
        """Resolve only a native AstrBot Context from current or legacy wrappers."""
        candidate = getattr(self, "context", None)
        pending = [candidate]
        visited: set[int] = set()
        wrapper_attrs = (
            "context_obj",
            "plugin_context",
            "wrapped_context",
            "raw_context",
            "_context",
            "_context_obj",
            "_plugin_context",
            "_wrapped_context",
            "_raw_context",
            "__wrapped__",
        )
        while pending:
            current = pending.pop(0)
            if isinstance(current, Context):
                return current
            if current is None or id(current) in visited:
                continue
            visited.add(id(current))
            for attr in wrapper_attrs:
                try:
                    nested = getattr(current, attr, None)
                except Exception:
                    continue
                if nested is not None and id(nested) not in visited:
                    pending.append(nested)
        return None

    def _proactive_conversation_with_configured_persona(self, conversation: Any) -> Any:
        specific_id = str(
            getattr(
                self,
                "_effective_plugin_persona_id",
                lambda: runtime_persona_setting(self, "plugin_specific_persona_id", ""),
            )()
            or ""
        ).strip()
        if conversation is None or not specific_id:
            return conversation
        if str(getattr(conversation, "persona_id", "") or "").strip() == specific_id:
            return conversation
        try:
            scoped = deepcopy(conversation)
            scoped.persona_id = specific_id
            return scoped
        except Exception as exc:
            logger.warning(
                "无法为主动主链应用插件指定人格,继续使用会话人格: persona=%s error=%s",
                _single_line(specific_id, 80),
                _single_line(exc, 120),
            )
            return conversation

    async def _run_framework_agent_text(
        self,
        *,
        umo: str,
        prompt: str,
        name: str,
        label: str,
        task: str | None = None,
        user: dict[str, Any] | None = None,
        max_steps: int = 20,
    ) -> str:
        self._cleanup_framework_delivery_caches()
        cache_key = str(umo or "")
        self._framework_captured_send_cache.pop(cache_key, None)
        getattr(self, "_framework_captured_send_cache_at", {}).pop(cache_key, None)
        deferred_photo_cache = getattr(self, "_framework_deferred_photo_cache", None)
        if isinstance(deferred_photo_cache, dict):
            deferred_photo_cache.pop(cache_key, None)
        getattr(self, "_framework_deferred_photo_cache_at", {}).pop(cache_key, None)
        framework_context = self._proactive_framework_context()
        if framework_context is None:
            context_value = getattr(self, "context", None)
            context_type = type(context_value).__name__ if context_value is not None else "None"
            warning_key = f"{type(context_value).__module__}.{context_type}" if context_value is not None else context_type
            if getattr(self, "_proactive_framework_context_warning_key", "") != warning_key:
                self._proactive_framework_context_warning_key = warning_key
                logger.warning(
                    "主动主链未取得 AstrBot 原生 Context,已直接转入人格化兜底: input_type=%s；请重载插件或重启 AstrBot",
                    context_type,
                )
            return ""
        camera_state: dict[str, Any] = {}
        if label == "proactive_message" and isinstance(user, dict):
            camera_prompt_getter = getattr(self, "_reality_touch_camera_proactive_prompt", None)
            if callable(camera_prompt_getter):
                camera_prompt = camera_prompt_getter(
                    user,
                    user_id=str(user.get("user_id") or ""),
                )
                if camera_prompt:
                    prompt = f"{prompt.rstrip()}\n\n{camera_prompt}"
            camera_state_getter = getattr(self, "_reality_touch_camera_proactive_state", None)
            if callable(camera_state_getter):
                value = camera_state_getter(
                    user,
                    user_id=str(user.get("user_id") or ""),
                )
                if isinstance(value, dict):
                    camera_state = value
        task_key = _single_line(task or label, 120)
        prompt_applier = getattr(self, "_apply_task_prompt_override_for_call", None)
        if callable(prompt_applier):
            prompt, _unused_system_prompt = prompt_applier(
                task_key,
                prompt,
                None,
                flatten_system_prompt=True,
            )
        event = self._proactive_synthetic_event(umo, prompt=prompt, name=name)
        if event is None:
            return ""
        try:
            setattr(event, "private_companion_skip_external_token_stats", True)
            setattr(event, "private_companion_proactive_framework", True)
            setattr(event, "private_companion_skip_passive_input_status", True)
        except Exception:
            pass
        cfg = framework_context.get_config(umo=umo) if umo else framework_context.get_config()
        provider_settings = cfg.get("provider_settings", {}) if isinstance(cfg, dict) else {}
        build_cfg = MainAgentBuildConfig(
            tool_call_timeout=int(provider_settings.get("tool_call_timeout", 120) or 120),
            llm_safety_mode=False,
            streaming_response=False,
        )
        req = ProviderRequest(
            prompt=prompt,
            conversation=None,
            session_id=getattr(event, "session_id", None) or umo,
        )

        captured_tool_sends: list[Any] = []
        result = None
        async def _run_with_retries() -> None:
            nonlocal result, captured_tool_sends
            for attempt in range(3):
                try:
                    conv = await self._get_current_conversation_safely(umo, label=f"{label}_framework_read")
                    req.conversation = self._proactive_conversation_with_configured_persona(conv)

                    async def _runner_factory():
                        build_result = await _host_build_main_agent()(
                            event=event,
                            # AstrBot 4.26.2+ validates this as the concrete Context type.
                            plugin_context=framework_context,
                            config=build_cfg,
                            req=req,
                        )
                        excluded_tools = {"AIsearch"}
                        if not camera_state.get("direct_allowed"):
                            excluded_tools.add("pc_reality_touch_camera_snapshot")
                        self._filter_incompatible_proactive_framework_tools(req, excluded_tools)
                        self._install_proactive_semantic_provider_fallback(
                            build_result,
                            label=label,
                        )
                        return build_result

                    result, captured_tool_sends = await self._capture_framework_send_message_calls(
                        target_session=umo,
                        runner_factory=_runner_factory,
                        max_steps=max_steps,
                    )
                    break
                except Exception as exc:
                    if self._is_sqlite_locked_error(exc) and attempt < 2:
                        wait_seconds = 0.35 * (attempt + 1)
                        logger.info(
                            "主动主链遇到会话库锁,稍后重试: label=%s session=%s retry=%s",
                            label,
                            _single_line(umo, 120),
                            attempt + 1,
                        )
                        await asyncio.sleep(wait_seconds)
                        continue
                    raise
        await _run_with_retries()
        if captured_tool_sends:
            self._framework_captured_send_cache[cache_key] = list(captured_tool_sends)
            captured_at = getattr(self, "_framework_captured_send_cache_at", None)
            if isinstance(captured_at, dict):
                captured_at[cache_key] = time.time()
        if bool(getattr(event, "_private_companion_photo_tool_deferred", False)):
            deferred_path = _path_text(
                getattr(event, "_private_companion_photo_tool_deferred_path", ""),
                1000,
            )
            if deferred_path and os.path.exists(deferred_path):
                cache = getattr(self, "_framework_deferred_photo_cache", None)
                if not isinstance(cache, dict):
                    cache = {}
                    self._framework_deferred_photo_cache = cache
                deferred_caption = self._sanitize_captured_plain_text(
                    getattr(event, "_private_companion_photo_tool_deferred_caption", "")
                )
                cache[cache_key] = {
                    "path": deferred_path,
                    "caption": deferred_caption,
                    "intent_kind": _single_line(
                        getattr(event, "_private_companion_photo_tool_deferred_intent_kind", ""),
                        40,
                    ),
                }
                deferred_at = getattr(self, "_framework_deferred_photo_cache_at", None)
                if isinstance(deferred_at, dict):
                    deferred_at[cache_key] = time.time()
                self._framework_captured_send_cache.pop(cache_key, None)
                getattr(self, "_framework_captured_send_cache_at", {}).pop(cache_key, None)
                logger.info(
                    "主动主链已接收 pc_generate_photo 成图，等待统一发送: label=%s session=%s",
                    label,
                    _single_line(cache_key, 120),
                )
                return deferred_caption
        runner = getattr(result, "agent_runner", None) if result else None
        llm_resp = runner.get_final_llm_resp() if runner else None
        text = str(getattr(llm_resp, "completion_text", "") or "").strip()
        response_role = str(getattr(llm_resp, "role", "") or "").strip().lower()
        captured_text = self._captured_send_plain_text(captured_tool_sends)
        if captured_text:
            if text and self._framework_agent_meta_summary_leak(text):
                logger.warning(
                    "主动主链 final 疑似工具循环摘要或 Provider 失败,改用已捕获发送文本: label=%s final=%s captured=%s",
                    label,
                    _single_line(text, 160),
                    _single_line(captured_text, 160),
                )
            text = captured_text
        elif response_role == "err" or (
            text and self._framework_agent_meta_summary_leak(text)
        ):
            logger.warning(
                "主动主链 final 疑似工具循环摘要或 Provider 失败且无可用捕获文本,已丢弃: label=%s text=%s",
                label,
                _single_line(text, 180),
            )
            return ""
        return text

    async def _generate_proactive_message_via_framework(
        self,
        user: dict[str, Any],
        name: str,
        reason: str,
        action_context: str = "",
        action: str = "message",
        motive: str = "",
    ) -> str:
        umo = str(user.get("umo") or "").strip()
        if not umo:
            return ""
        prompt = await self._build_framework_proactive_prompt(
            user=user,
            name=name,
            reason=reason,
            action=action,
            action_context=action_context,
            motive=motive,
        )
        recorder = getattr(self, "_record_prompt_injection_snapshot", None)
        if callable(recorder):
            trace_id = f"pro-{uuid.uuid4().hex[:16]}"
            message_preview = _single_line(
                " / ".join(
                    part
                    for part in (
                        name,
                        _single_line(user.get("planned_proactive_topic"), 60),
                        motive,
                        reason,
                        action,
                    )
                    if _single_line(part, 60)
                ),
                220,
            )
            await recorder(
                kind="proactive",
                session=umo,
                title="主动消息提示词",
                text=prompt,
                mode=reason,
                trace_id=trace_id,
                message_preview=message_preview,
                sender_label=_single_line(f"{name}/{user.get('user_id')}", 80),
                metadata={
                    "用户": _single_line(user.get("user_id"), 80),
                    "称呼": name,
                    "原因": reason,
                    "动作": action,
                    "动机": motive,
                    "话题": _single_line(user.get("planned_proactive_topic"), 80),
                },
            )
        try:
            raw_text = await self._run_framework_agent_text(
                umo=umo,
                prompt=prompt,
                name=name,
                label="proactive_message",
                task="proactive_message",
                user=user,
                max_steps=20,
            )
            raw_text = str(raw_text or "")
            if not raw_text:
                return ""
            cleaned_text, payloads = self._extract_timer_directives(raw_text)
            if payloads:
                logger.info(
                    "主动消息中清理到对话临时预约标签,不再由主动链路登记: user=%s",
                    _single_line(user.get("user_id"), 40),
                )
            return cleaned_text
        except Exception as exc:
            if self._is_sqlite_locked_error(exc):
                logger.warning("主动消息主链被会话数据库锁住,本轮跳过并等待下次调度: %s", _single_line(umo, 120))
            else:
                logger.warning("主动消息主链生成失败: %s", exc)
            return ""

    def _proactive_history_limit(self, stage: str) -> int:
        review_stage = str(stage or "").strip().lower() == "review"
        attr = "proactive_review_history_limit" if review_stage else "proactive_generation_history_limit"
        default = 30 if review_stage else 20
        return _safe_int(
            runtime_persona_setting(self, attr, default),
            default,
            1,
            200,
        )

    @staticmethod
    def _fit_proactive_history_lines(lines: list[str], max_chars: int) -> list[str]:
        budget = max(0, int(max_chars))
        if not lines or budget <= 0:
            return []
        kept_reversed: list[str] = []
        used = 0
        for raw_line in reversed(lines):
            line = str(raw_line or "").strip()
            if not line:
                continue
            separator = 1 if kept_reversed else 0
            available = budget - used - separator
            if available <= 0:
                break
            if len(line) <= available:
                kept_reversed.append(line)
                used += separator + len(line)
                continue
            if not kept_reversed:
                kept_reversed.append(line[:available].rstrip())
            break
        return list(reversed([line for line in kept_reversed if line]))

    def _format_proactive_history_context(self, lines: list[str]) -> str:
        cleaned_lines = [str(line or "").strip() for line in lines if str(line or "").strip()]
        if not cleaned_lines:
            return ""
        mode = str(
            runtime_persona_setting(self, "proactive_history_context_mode", "compact")
            or "compact"
        ).strip().lower()
        if mode not in {"recent_only", "compact", "expanded"}:
            mode = "compact"
        recent_count = _safe_int(
            runtime_persona_setting(self, "proactive_history_recent_raw_count", 8),
            8,
            1,
            50,
        )
        max_chars = _safe_int(
            runtime_persona_setting(self, "proactive_history_max_chars", 6000),
            6000,
            500,
            20000,
        )

        if mode == "recent_only":
            fitted = self._fit_proactive_history_lines(cleaned_lines[-recent_count:], max_chars)
            return "\n".join(fitted)
        if mode == "expanded":
            fitted = self._fit_proactive_history_lines(cleaned_lines, max_chars)
            return "\n".join(fitted)

        def history_section(key: str, title: str, content: str) -> PromptSection:
            return prompt_section(
                key=key,
                title=title,
                source="proactive_message",
                content=content,
            )

        def labeled_overhead(key: str, title: str) -> int:
            probe = render_prompt_sections(
                [history_section(key, title, "x")],
                mode=PromptRenderMode.LABELED_BLOCK,
            )
            return len(probe) - 1

        recent_lines = cleaned_lines[-recent_count:]
        older_lines = [_single_line(line, 160) for line in cleaned_lines[:-recent_count]]
        recent_key = "proactive.history.recent"
        recent_title = "最近对话（保留原文）"
        recent_overhead = labeled_overhead(recent_key, recent_title)
        recent_budget = max(0, max_chars - recent_overhead)
        fitted_recent = self._fit_proactive_history_lines(recent_lines, recent_budget)
        recent_block = render_prompt_sections(
            [history_section(recent_key, recent_title, "\n".join(fitted_recent))],
            mode=PromptRenderMode.LABELED_BLOCK,
        )
        if not older_lines:
            return recent_block[:max_chars]

        older_key = "proactive.history.older"
        older_title = "较早对话（已压缩）"
        older_overhead = labeled_overhead(older_key, older_title)
        remaining = max_chars - len(recent_block) - older_overhead - 1
        fitted_older = self._fit_proactive_history_lines(older_lines, remaining)
        if not fitted_older:
            return recent_block[:max_chars]
        older_block = render_prompt_sections(
            [history_section(older_key, older_title, "\n".join(fitted_older))],
            mode=PromptRenderMode.LABELED_BLOCK,
        )
        return f"{older_block}\n{recent_block}"[:max_chars]

    async def _recent_private_conversation_for_proactive_review(
        self,
        user: dict[str, Any],
        *,
        limit: int = 10,
    ) -> str:
        umo = str(user.get("umo") or "").strip()
        lines: list[str] = []
        if umo:
            try:
                conv = await self._get_current_conversation_safely(umo, label="proactive_review_history_read")
                history = self._load_conversation_history_items(conv, tail_only=max(1, limit))
                for item in history[-max(1, limit):]:
                    line = self._format_history_item_for_summary(item)
                    if line:
                        lines.append(line)
            except Exception as exc:
                logger.debug("主动润色读取私聊历史失败: %s", _single_line(exc, 120))
        if not lines:
            last_user = _single_line(user.get("last_user_message"), 180)
            last_bot = _single_line(user.get("last_companion_message"), 180)
            if last_bot:
                lines.append(f"{runtime_persona_setting(self, 'bot_name', '小星')}: {last_bot}")
            if last_user:
                lines.append(f"用户: {last_user}")
        return self._format_proactive_history_context(lines[-max(1, limit):])

    def _clean_persona_reference_rewrite_text(self, text: Any, *, limit: int = 160) -> str:
        cleaned = self._sanitize_proactive_text(str(text or ""))
        if not cleaned:
            return ""
        cleaned = _strip_internal_message_blocks(
            cleaned,
            enabled=bool(runtime_persona_setting(self, "enable_framework_error_leak_guard", True)),
            tts_enabled=bool(runtime_persona_setting(self, "enable_tts_enhancement", False)),
        )
        cleaned = self._strip_parenthetical_stage_directions(cleaned)
        cleaned = re.sub(r"^(?:最终(?:聊天)?正文|正文|输出|回复)[:：]\s*", "", cleaned).strip()
        cleaned = re.sub(r"\s+", " ", cleaned).strip().strip('"').strip("'")
        if not cleaned:
            return ""
        forbidden = (
            "参考意图", "参考文案", "兜底", "模板", "系统", "提示词", "工具调用",
            "执行状态", "已发送给用户", "消息已发送", "发送成功", "无文字",
        )
        if any(token in cleaned for token in forbidden):
            return ""
        if self._framework_agent_meta_summary_leak(cleaned):
            return ""
        return _single_line(cleaned, limit)

    @staticmethod
    def _reference_rewrite_prompt_document(
        *,
        persona: str,
        style_title: str,
        reply_style: str,
        history: str,
        recipient_identity: str,
        scene: str,
        reference: str,
        creative_excerpt_rule: str,
        status_rule: str,
        segmenting_section: PromptSection | None = None,
    ) -> PromptDocument:
        sections: list[PromptSection | PromptDocumentPart] = [
            _proactive_prompt_part(prompt_section(
                key="background.reference_rewrite.task",
                title="人格参考意图改写",
                source="proactive_message",
                content=(
                    "你要把一条“参考意图”改写成当前人格会自然说出的聊天正文。"
                    "参考意图只说明要表达什么，不是要照抄的句子。"
                ),
            ), mode=PromptRenderMode.BODY_ONLY),
            prompt_section(
                key="background.reference_rewrite.persona",
                title="当前人格",
                source="proactive_message",
                content=persona or "保持自然、简洁、有边界。",
            ),
            prompt_section(
                key="background.reference_rewrite.style",
                title=style_title,
                source="proactive_message",
                content=reply_style or "像日常聊天一样短一点，不要报告式。",
            ),
            prompt_section(
                key="background.reference_rewrite.history",
                title="最近对话",
                source="proactive_message",
                content=history or "（无可用历史）",
            ),
            prompt_section(
                key="background.reference_rewrite.recipient",
                title="当前收件人",
                source="proactive_message",
                content=(
                    recipient_identity
                    or "当前收件人身份未知；不要猜测名字或套用人格中的专属称呼。"
                ),
            ),
            prompt_section(
                key="background.reference_rewrite.scene",
                title="场景",
                source="proactive_message",
                content=scene or "普通聊天回执",
            ),
            prompt_section(
                key="background.reference_rewrite.intent",
                title="参考意图",
                source="proactive_message",
                content=reference,
            ),
            _proactive_prompt_part(prompt_section(
                key="background.reference_rewrite.rules",
                title="要求",
                source="proactive_message",
                content=(
                    "- 只输出最终聊天正文，不要解释。\n"
                    "- 1 句，最多 2 句；尽量像这个人格平时聊天，不要像客服、公告或模板。\n"
                    "- 不要照抄参考意图里的固定说法；只保留事实和语义。\n"
                    "- 不要出现“参考/兜底/模板/系统/工具/执行/已发送给用户/消息已发送”等字样。\n"
                    "- 不要新增事实、承诺、动作小剧场或没有发生的状态。\n"
                    f"{creative_excerpt_rule}\n"
                    "- 如果参考意图或模型结果包含 Provider/API 报错、内容策略拒绝、敏感词提示、政策链接或内部诊断，"
                    "视为本轮失败并输出空文本；不要翻译、复述或润色这类内容。\n"
                    f"{status_rule}"
                ),
            ), label_style=PromptLabelStyle.FULLWIDTH_COLON),
        ]
        if segmenting_section is not None:
            sections.append(
                _proactive_prompt_part(
                    segmenting_section,
                    mode=PromptRenderMode.CONVERSATION_XML,
                )
            )
        return prompt_document(
            user_render=_PROACTIVE_DOCUMENT_RENDER,
            user=sections,
            metadata={"task": "proactive_reference_rewrite"},
        )

    async def _rewrite_reference_reply_with_persona(
        self,
        reference_text: str,
        *,
        scene: str = "",
        user: dict[str, Any] | None = None,
        event: AstrMessageEvent | None = None,
        history: str = "",
        fallback_text: str = "",
        task: str = "persona_reference_rewrite",
        max_chars: int = 120,
        allow_fallback: bool = False,
        preserve_status: bool = False,
    ) -> str:
        reference = _single_line(reference_text, 420)
        if not reference:
            return _single_line(fallback_text, max_chars) if allow_fallback else ""
        umo = ""
        if event is not None:
            umo = str(getattr(event, "unified_msg_origin", "") or "").strip()
        if not umo and isinstance(user, dict):
            umo = str(user.get("umo") or "").strip()
        persona = await self._resolve_proactive_persona_prompt(user, umo=umo)
        proactive_rewrite = str(task or "").startswith("proactive")
        if proactive_rewrite:
            voice_sections_getter = getattr(self, "_format_proactive_voice_prompt_sections", None)
            if callable(voice_sections_getter):
                reply_style = render_prompt_sections(
                    voice_sections_getter(),
                    mode=PromptRenderMode.LABELED_BLOCK,
                )
            else:
                voice_getter = getattr(self, "_format_proactive_voice_prompt", None)
                reply_style = voice_getter() if callable(voice_getter) else ""
            expression_section_getter = getattr(self, "_format_expression_voice_prompt_section", None)
            expression_section = (
                expression_section_getter(
                    scope="proactive",
                    target_id=(
                        _single_line(user.get("user_id") or user.get("id"), 80)
                        if isinstance(user, dict)
                        else ""
                    ),
                    context_owner=user if isinstance(user, dict) else None,
                    stage_owner=user if isinstance(user, dict) else None,
                )
                if callable(expression_section_getter)
                else None
            )
            expression_voice = (
                render_prompt_sections([expression_section], mode=PromptRenderMode.LABELED_BLOCK)
                if isinstance(expression_section, PromptSection)
                else ""
            )
            if not callable(expression_section_getter):
                expression_formatter = getattr(self, "_format_expression_voice_for_prompt", None)
                expression_voice = (
                    expression_formatter(
                        scope="proactive",
                        target_id=(
                            _single_line(user.get("user_id") or user.get("id"), 80)
                            if isinstance(user, dict)
                            else ""
                        ),
                        context_owner=user if isinstance(user, dict) else None,
                        stage_owner=user if isinstance(user, dict) else None,
                    )
                    if callable(expression_formatter)
                    else ""
                )
            if expression_voice:
                reply_style = f"{reply_style}\n\n{expression_voice}".strip()
        else:
            reply_style = self._format_reply_style_prompt()
        if not history and isinstance(user, dict):
            try:
                history_limit = self._proactive_history_limit("generation") if proactive_rewrite else 6
                history = await self._recent_private_conversation_for_proactive_review(user, limit=history_limit)
            except Exception:
                history = ""
        recipient_identity = self._proactive_recipient_identity_prompt_text(
            user,
            _single_line(user.get("nickname"), 40) if isinstance(user, dict) else "",
        )
        creative_excerpt_rule = (
            "- 若参考意图包含创作原文且决定引用，只能连续摘取来源原文，并用一组成对的 `「...」` 包住；"
            "聊天式引入和收尾留在 `「」` 外，不得改写或另编作品片段。"
            if "创作" in str(scene or "")
            else ""
        )
        segmenting_section: PromptSection | None = None
        if proactive_rewrite and self._proactive_llm_segmenting_allowed(umo=umo):
            segmenting_getter = getattr(self, "_llm_controlled_segmenting_prompt_section", None)
            if callable(segmenting_getter):
                candidate = segmenting_getter()
                if isinstance(candidate, PromptSection):
                    segmenting_section = candidate
        prompt = render_prompt_document(
            self._reference_rewrite_prompt_document(
                persona=persona,
                style_title="主动开口风格" if proactive_rewrite else "回复风格",
                reply_style=reply_style,
                history=history,
                recipient_identity=recipient_identity,
                scene=_single_line(scene, 180),
                reference=reference,
                creative_excerpt_rule=creative_excerpt_rule,
                status_rule=(
                    "- 必须保留成功/失败/等待/完成/稍后再说等状态语义，不要把失败说成成功。"
                    if preserve_status
                    else "- 如果只是轻轻递一句，不要补多余解释。"
                ),
                segmenting_section=segmenting_section,
            )
        )["user"]
        try:
            raw = await self._llm_call(
                prompt,
                max_tokens=140,
                provider_id=self._task_provider(
                    _persona_provider_id(
                        self,
                        "RESPONSE_REVIEW_PROVIDER_ID",
                        "response_review_provider_id",
                        "fast",
                    ),
                    _persona_provider_id(
                        self,
                        "MAI_STYLE_PROVIDER_ID",
                        "mai_style_provider_id",
                        "fast",
                    ),
                    _persona_provider_id(
                        self,
                        "LLM_PROVIDER_ID",
                        "llm_provider_id",
                        "complex",
                    ),
                ),
                task=task,
            )
        except Exception as exc:
            logger.debug("人格参考意图改写失败: %s", _single_line(exc, 120))
            raw = ""
        if self._looks_like_internal_provider_error_text(raw):
            logger.warning(
                "人格参考意图改写收到 Provider 错误正文，已丢弃: task=%s",
                _single_line(task, 80) or "persona_reference_rewrite",
            )
            raw = ""
        cleaned = self._clean_persona_reference_rewrite_text(raw, limit=max_chars)
        if cleaned:
            return cleaned
        return _single_line(fallback_text, max_chars) if allow_fallback else ""

