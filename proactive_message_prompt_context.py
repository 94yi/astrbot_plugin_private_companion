# -*- coding: utf-8 -*-
"""prompt_context 域。

由 tools/split_mixin_domain.py 从 proactive_message.py 机械抽取（48 个方法 + 0 个模块级名字 + 0 个类级赋值 / 1236 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveMessageMixin）。
"""
from __future__ import annotations

import re
from .conversation_prompt_section import (
    PromptDocument,
    PromptRenderMode,
    PromptSection,
    prompt_document,
    prompt_section,
    render_prompt_document,
    render_prompt_sections,
)
from .helpers import _now_ts, _safe_float, _safe_int, _single_line, _split_address_terms
from .persona_config import runtime_persona_setting
from .proactive_message_shared import _PROACTIVE_DOCUMENT_RENDER, _proactive_prompt_part
from .reaction_expression import normalize_reaction_expression_intent, reaction_expression_high_frequency
from datetime import datetime
from typing import Any

from .logging_util import get_module_logger

logger = get_module_logger(__name__)



# ---- 宿主全局转发层（由 tmp/refactor/autofix_domain_globals.py 生成）----
# ==== 需实时转发（可被 patch）：同名函数转发宿主 ====
def _now_ts(*args, **kwargs):
    from . import proactive_message as _host
    return getattr(_host, "_now_ts")(*args, **kwargs)
# ---- 宿主全局转发层结束 ----

class ProactiveMessagePromptContextMixin:
    """prompt_context 域（从 ProactiveMessageMixin 拆出）。"""


    def _format_state_for_framework_prompt(self, state: dict[str, Any], *, reason: str, action: str) -> str:
        if not isinstance(state, dict):
            return "只作为语气底色：整体平稳,不要在正文里汇报状态。"
        parts: list[str] = []
        energy = _safe_int(state.get("energy"), 70, 0, 100)
        mood = _single_line(state.get("mood_bias"), 20)
        weather = _single_line(state.get("weather"), 40)
        conditions = state.get("conditions")
        meaningful_conditions: list[str] = []
        if isinstance(conditions, list):
            for cond in conditions:
                if not isinstance(cond, dict):
                    continue
                if not self._should_show_condition(cond):
                    continue
                label = _single_line(cond.get("label") or cond.get("kind"), 16)
                text = _single_line(cond.get("text"), 28)
                if label and text:
                    meaningful_conditions.append(f"{label}/{text}")
        if meaningful_conditions:
            parts.append(
                "语气里带一点"
                + "、".join(meaningful_conditions[:2])
                + "的影响,但不要主动解释这些状态。"
            )
        elif energy <= 42:
            parts.append("语气短一点、慢一点；不要直接说状态标签、数值或内部原因。")
        elif energy >= 85:
            parts.append("语气可以轻快一点,但不要直接说自己精神很好。")
        elif mood and mood not in {"平稳", "中性"}:
            parts.append(f"语气底色偏{mood},让它自然露出来,不要直接汇报情绪。")
        if "photo_text" in action or reason in {"activity_share", "diary_share", "evening_greeting", "morning_greeting"}:
            if weather and weather not in {"暂无天气信息"}:
                parts.append(
                    f"天气只作为内部的光线/画面感参考：{weather}。"
                    "不要在正文或语音里提天气、气温、下雨或天色，也不要追问对方那边的天气；"
                    "真正的环境突变和官方预警会由独立主动原因提供明确事实。"
                )
        parts.append(
            "状态只影响语气、用词、句子长短、是否开口和话题选择；不要为了表现状态而写动作小剧场。"
        )
        parts.append(
            "如果一句话已经问候、关心或递出了具体片段,可以直接停住；不用为了显得日常,在后半句补“我刚才在发呆/躺着/盯天花板”这类状态汇报。"
        )
        parts.append(
            "即使状态是困倦、迷糊、半梦半醒或低能量,也只能让语气更轻更慢；不能降低理解质量、事实判断或正常承接能力。"
        )
        parts.append(
            "不要直接宣告“我累了/我吓到了/我在写作业”,也不要用“茶差点打翻/笔帽掉了/喝水呛到”这类动作表演状态。确实要表达时只用最短口语,如“困了”“别说了”。"
        )
        return "；".join(parts) if parts else "只作为语气底色：整体平稳,不要在正文里汇报状态。"

    def _proactive_expression_shape_prompt_section(
        self,
        user: dict[str, Any],
        *,
        reason: str,
        action: str,
    ) -> str:
        """让当前能量影响表达形状，而不是只影响是否发送。"""
        state = self.data.get("daily_state", {})
        energy = _safe_int(state.get("energy"), 70, 0, 100) if isinstance(state, dict) else 70
        lines = [
            "主动消息通常比正式回复更碎、更口语；大多数控制在 20 个汉字左右，能停在半句就不要补成完整段落。",
            "除非语义确实需要，不要每句都用完整句号收尾；不要为了显得自然而堆叠解释、背景和客套。",
        ]
        if energy <= 42:
            lines.append("当前能量偏低：优先一条短句或一个轻问候，少用感叹号，最多一到两段，不要连续铺陈。")
        elif energy >= 85:
            lines.append("当前能量偏高：可以用两三个很短的口语句连着说，允许轻微兴奋感，但不要写成长段独白。")
        else:
            lines.append("当前能量平稳：优先一到两句短消息，保留一点停顿和留白，避免客服式完整陈述。")
        if reason in {"check_in", "quiet_care", "state_share"} and "photo" not in action:
            lines.append("如果本轮只是想起对方或顺手分享一点感受，低信息量的短句也可以成立；不要为了增加信息而硬塞新事实。")
        busy_context_getter = getattr(self, "_busy_proactive_voice_context", None)
        try:
            busy_context = busy_context_getter() if callable(busy_context_getter) else {}
        except Exception:
            busy_context = {}
        if isinstance(busy_context, dict) and busy_context.get("busy"):
            lines.append(
                "当前日程处于忙碌片段：如果这轮只是轻量问候、感受或一句顺手分享，"
                "可以把语音当作更省手的表达；语音脚本保持一两句、口语化，不要朗读长说明。"
            )
            lines.append(
                "忙碌只提供表达倾向，不是强制动作；涉及命令、链接、重要事实、配置或需要留档的信息，继续用文字。"
            )
        if action == "message" and self._proactive_reaction_expression_enabled(action):
            lines.append(
                "如果正文只是‘收到/好的/笑死/辛苦了’这类语义明确的短回应，"
                "可以考虑在正文之后追加一个匹配情绪的语义表情标签；正文较长、信息重要或语气不确定时不要追加。"
            )
        return prompt_section(
            key="proactive.expression_shape",
            title="主动消息的表达形状",
            source="proactive_message",
            content="\n".join(lines),
        )

    def _proactive_expression_shape_hint(
        self,
        user: dict[str, Any],
        *,
        reason: str,
        action: str,
    ) -> str:
        return render_prompt_sections(
            [self._proactive_expression_shape_prompt_section(user, reason=reason, action=action)],
            mode=PromptRenderMode.LABELED_BLOCK,
        )

    def _format_plan_item_for_framework_prompt(self, item: dict[str, Any] | None) -> str:
        if not isinstance(item, dict):
            return ""
        activity = _single_line(item.get("activity"), 60)
        mood = _single_line(item.get("mood"), 12)
        time_text = _single_line(item.get("time"), 12)
        if activity:
            activity = re.sub(r"[,、]?\s*想起了[^,。]+", "", activity).strip(",。 ")
            activity = re.sub(r"[,、]?\s*突然想到[^,。]+", "", activity).strip(",。 ")
        parts = []
        if time_text:
            parts.append(time_text)
        if activity:
            parts.append(activity)
        if mood and mood not in {"平稳", "中性"}:
            parts.append(f"情绪偏{mood}")
        return "｜".join(parts)

    def _nearby_plan_items(self, plan: dict[str, Any] | None = None) -> dict[str, Any]:
        plan = plan if isinstance(plan, dict) else self.data.get("daily_plan", {})
        if not isinstance(plan, dict) or not self._is_plan_date_active(plan.get("date")):
            return {}
        items = plan.get("items")
        if not isinstance(items, list) or not items:
            return {}
        now_minutes = self._effective_plan_now_minutes(str(plan.get("date") or ""))
        if now_minutes is None:
            return {}
        # The raw daily plan is an edit input.  Build a purpose-specific view
        # before exposing any text to proactive generation so past/current
        # facts require evidence and future scene prose is reduced to a small
        # labelled summary.
        current_ids: set[str] = set()
        history_ids: set[str] = set()
        proactive_entries: dict[str, dict[str, Any]] = {}
        disclosure_available = callable(getattr(self, "_agenda_disclosure_view", None))
        disclosure_keys: dict[tuple[str, str], str] = {}
        disclosure = getattr(self, "_agenda_disclosure_view", None)
        if callable(disclosure):
            try:
                for purpose, bucket in (("current_fact", current_ids), ("history_fact", history_ids)):
                    view = disclosure(purpose, max_entries=64)
                    values = view.get("entries", []) if isinstance(view, dict) else getattr(view, "entries", [])
                    for value in values if isinstance(values, list) else []:
                        if isinstance(value, dict):
                            key = str(value.get("plan_id") or value.get("entry_id") or "").strip()
                            if key:
                                bucket.add(key)
                            pair = (
                                _single_line(value.get("time"), 12),
                                _single_line(value.get("title") or value.get("activity"), 120),
                            )
                            if pair[0] and pair[1] and key:
                                disclosure_keys[pair] = key
                view = disclosure("proactive", max_entries=64)
                values = view.get("entries", []) if isinstance(view, dict) else getattr(view, "entries", [])
                for value in values if isinstance(values, list) else []:
                    if isinstance(value, dict):
                        key = str(value.get("plan_id") or value.get("entry_id") or "").strip()
                        if key:
                            proactive_entries[key] = value
                        pair = (
                            _single_line(value.get("time"), 12),
                            _single_line(value.get("title") or value.get("activity"), 120),
                        )
                        if pair[0] and pair[1] and key:
                            disclosure_keys[pair] = key
            except Exception:
                current_ids.clear()
                history_ids.clear()
                proactive_entries = {}
                disclosure_keys = {}
                # The policy is a disclosure firewall.  If it is present but
                # unavailable, fail closed instead of falling back to raw
                # daily-plan prose in a proactive prompt.
                disclosure_available = True
        parsed: list[tuple[int, dict[str, Any]]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            minute = self._parse_hhmm_to_minutes(item.get("time"))
            if minute is None:
                continue
            item_key = str(item.get("plan_id") or "").strip()
            if not item_key and disclosure_available:
                pair = (
                    _single_line(item.get("time"), 12),
                    _single_line(item.get("activity") or item.get("title"), 120),
                )
                item_key = disclosure_keys.get(pair, "")
            if disclosure_available and minute <= now_minutes and item_key not in current_ids and item_key not in history_ids:
                # A clock window alone is not a current/history fact.  This
                # also prevents an unexecuted past plan from being presented
                # as the "previous" item in proactive prompts.
                continue
            if disclosure_available and minute > now_minutes:
                public = proactive_entries.get(item_key)
                if not public:
                    # The proactive view applies its horizon and commitment
                    # gates.  Do not fall back to raw future scene prose.
                    continue
                safe = dict(item)
                safe["activity"] = _single_line(public.get("title"), 100) or "临近时段可能有安排"
                safe["message_seed"] = ""
                safe["scene"] = ""
                safe["candidates"] = []
                item = safe
            parsed.append((minute, item))
        if not parsed:
            return {}
        parsed.sort(key=lambda pair: pair[0])
        previous: tuple[int, dict[str, Any]] | None = None
        upcoming: tuple[int, dict[str, Any]] | None = None
        for minute, item in parsed:
            if minute <= now_minutes:
                previous = (minute, item)
                continue
            upcoming = (minute, item)
            break
        return {
            "now_minutes": now_minutes,
            "previous": previous[1] if previous else None,
            "previous_age": now_minutes - previous[0] if previous else None,
            "upcoming": upcoming[1] if upcoming else None,
            "upcoming_in": upcoming[0] - now_minutes if upcoming else None,
        }

    def _format_schedule_context_for_prompt(self, plan: dict[str, Any] | None = None) -> str:
        nearby = self._nearby_plan_items(plan)
        if not nearby:
            return ""
        previous = nearby.get("previous")
        upcoming = nearby.get("upcoming")
        previous_age = nearby.get("previous_age")
        upcoming_in = nearby.get("upcoming_in")
        lines: list[str] = []
        if isinstance(upcoming, dict) and isinstance(upcoming_in, int) and 0 <= upcoming_in <= 45:
            lines.append(
                "即将进入："
                + self._format_plan_item_for_prompt(upcoming)
                + f"（约 {upcoming_in} 分钟后）"
            )
            if isinstance(previous, dict) and isinstance(previous_age, int) and previous_age <= 90:
                prev_mood = _single_line(previous.get("mood"), 24)
                prev_time = _single_line(previous.get("time"), 12)
                lines.append(
                    f"上一段只作余味：{prev_time}"
                    + (f"｜情绪：{prev_mood}" if prev_mood else "")
                    + "。不要把上一段当成正在发生。"
                )
        elif isinstance(previous, dict) and isinstance(previous_age, int) and previous_age <= 75:
            lines.append(
                "当前/最近："
                + self._format_plan_item_for_prompt(previous)
                + f"（约 {previous_age} 分钟前开始）"
            )
            if isinstance(upcoming, dict) and isinstance(upcoming_in, int):
                lines.append(
                    "下一段参考："
                    + self._format_plan_item_for_prompt(upcoming)
                    + f"（约 {upcoming_in} 分钟后）"
                )
        elif isinstance(upcoming, dict) and isinstance(upcoming_in, int):
            lines.append(
                "附近更应参考下一段："
                + self._format_plan_item_for_prompt(upcoming)
                + f"（约 {upcoming_in} 分钟后）"
            )
            if isinstance(previous, dict):
                lines.append("上一段已经过去较久,只保留很淡的情绪余味,不要复述场景。")
        elif isinstance(previous, dict):
            lines.append(
                "最近一段："
                + self._format_plan_item_for_prompt(previous)
                + "。如果离当前时间较久,只当作余味。"
            )
        return "\n".join(line for line in lines if line)

    def _sanitize_schedule_context_for_private_user(self, text: str, user: dict[str, Any] | None = None) -> str:
        cleaned = str(text or "").strip()
        if not cleaned:
            return ""
        if self._private_user_role(user) != "friend":
            return cleaned
        cleaned = self._sanitize_owner_environment_context_for_private_user(cleaned, user)
        sensitive_names = [
            _single_line(item, 24)
            for item in (
                runtime_persona_setting(self, "default_nickname", ""),
                *(getattr(self, "target_user_ids", []) or []),
            )
            if _single_line(item, 24)
        ]
        for name in sensitive_names:
            cleaned = cleaned.replace(name, "某个熟人")
        cleaned = re.sub(r"看见[^，。,；;。！？]{1,24}坐在[^，。,；;。！？]{0,24}", "看见有人在忙", cleaned)
        cleaned = re.sub(r"(?:放在|放到|搁在|塞到)[^，。,；;。！？]{0,12}(?:桌边|桌上|手边|旁边)", "放到一边", cleaned)
        cleaned = re.sub(r"给你[^，。,；;。！？]{0,24}", "给熟人留了一点小东西", cleaned)
        cleaned = re.sub(r"你(?:的|那边|桌边|桌上|手边)", "对方那边", cleaned)
        return re.sub(r"\s+", " ", cleaned).strip()

    def _sanitize_owner_environment_context_for_private_user(self, text: str, user: dict[str, Any] | None = None) -> str:
        cleaned = str(text or "").strip()
        if not cleaned:
            return ""
        if self._private_user_role(user) != "friend":
            return cleaned
        weather_tokens = (
            "天气", "气温", "温度", "湿度", "降雨", "下雨", "阵雨", "小雨", "中雨", "大雨",
            "暴雨", "雷雨", "雷暴", "晴", "多云", "阴天", "风速", "风力", "空气质量", "OpenWeather",
        )
        location_tokens = (
            "当前位置", "当前地点", "所在地", "所在城市", "住处", "住址", "地址", "城市", "小区",
            "街道", "门牌", "宿舍", "校区", "位置：", "地点：", "外面在",
        )
        kept: list[str] = []
        for raw_line in cleaned.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if any(token in line for token in weather_tokens) or any(token in line for token in location_tokens):
                continue
            line = re.sub(r"身处(?:家里|学校|工作地点|外面|路上)[，,；;、]?", "", line)
            line = re.sub(r"(?:家里|学校|工作地点|外面|路上)[（(][^）)]{1,40}[）)]", r"", line)
            if line.strip():
                kept.append(line.strip())
        cleaned = "\n".join(kept).strip()
        cleaned = re.sub(r"天气[^。！？\n]{0,80}[。！？]?", "", cleaned)
        cleaned = re.sub(r"(?:当前位置|当前地点|所在地|所在城市|住处|住址|地址)[^。！？\n]{0,80}[。！？]?", "", cleaned)
        cleaned = re.sub(r"身处(?:家里|学校|工作地点|外面|路上)[，,；;、]?", "", cleaned)
        return re.sub(r"\n{3,}", "\n\n", cleaned).strip()

    def _current_time_period_label(self, now: datetime | None = None) -> tuple[str, str]:
        current = now or self._environment_now()
        minute = current.hour * 60 + current.minute
        periods = [
            (0, 5 * 60, "深夜", "除非已有失眠或夜聊上下文,不要显得精神过满。"),
            (5 * 60, 7 * 60 + 30, "清晨", "适合很轻的醒来感,不要写成已经忙完一上午。"),
            (7 * 60 + 30, 10 * 60 + 30, "早晨", "可以有起床、出门、刚开始一天的余味。"),
            (10 * 60 + 30, 11 * 60 + 45, "上午后段", "还不是午休,不要提前写成吃午饭或午睡。"),
            (11 * 60 + 45, 13 * 60 + 30, "中午", "可以有吃东西、犯困、午间松下来,不要写成刚起床。"),
            (13 * 60 + 30, 17 * 60 + 30, "下午", "适合课间、工作间隙、犯困或缓慢推进。"),
            (17 * 60 + 30, 19 * 60 + 30, "傍晚", "适合收尾、路上、回家、天色变暗的生活感。"),
            (19 * 60 + 30, 22 * 60 + 30, "晚上", "适合放慢、写作业、休息或一点点夜里的黏人感。"),
            (22 * 60 + 30, 24 * 60, "深夜前段", "适合安静收声,不要写成白天刚开始。"),
        ]
        for start, end, label, guard in periods:
            if start <= minute < end:
                return label, guard
        return "当前时段", "贴着当前时间开口,不要跳到明显不属于此刻的生活场景。"

    def _format_time_period_injection(self) -> str:
        current = self._environment_now()
        label, guard = self._current_time_period_label(current)
        weekday = "一二三四五六日"[current.weekday()]
        return (
            f"当前时间：{current.strftime('%Y-%m-%d %H:%M')}（周{weekday}，{label}）。\n"
            f"使用方式：这只用于判断生活节奏和措辞,不要主动报时、报日期或解释时段。\n"
            f"时段边界：{guard}"
        )

    def _format_proactive_relationship_fact(self, user: dict[str, Any]) -> str:
        role = self._private_user_role(user) if isinstance(user, dict) else "owner"
        labeler = getattr(self, "_private_user_role_label", None)
        label = labeler(role) if callable(labeler) else ("主要用户" if role == "owner" else "次要用户")
        profile = self._relationship_profile(user if isinstance(user, dict) else {})
        expression_builder = getattr(self, "_build_expression_decision_for_user", None)
        expression: dict[str, Any] = {}
        if callable(expression_builder):
            try:
                decision = expression_builder(
                    user if isinstance(user, dict) else {},
                    proactive_candidate={"eligible": True, "daily_allowance": 1},
                    message_intent={"requested_content_tier": "normal"},
                )
                expression = decision.to_dict() if hasattr(decision, "to_dict") else dict(decision or {})
            except Exception:
                expression = {}
        note = _single_line(user.get("proactive_boundary_note"), 80) if isinstance(user, dict) else ""
        parts: list[str] = []
        if expression:
            parts.append(
                f"统一表达决策：角色={label}，"
                f"长期阶段={_single_line(profile.get('stage_label'), 20) or '初识'}，"
                f"档位={_single_line(expression.get('expression_band'), 20) or 'relaxed'}，"
                f"语气={_single_line(expression.get('tone'), 20) or 'steady'}，"
                f"节奏={_single_line(expression.get('pacing'), 16) or 'steady'}，"
                f"直接度={_single_line(expression.get('directness'), 16) or 'natural'}，"
                f"回应={_single_line(expression.get('validation_style'), 20) or 'none'}，"
                f"自述={_single_line(expression.get('self_disclosure'), 16) or 'none'}，"
                f"幽默={_single_line(expression.get('humor_mode'), 16) or 'off'}，"
                f"话题={_single_line(expression.get('topic_initiative'), 20) or 'reply_only'}，"
                f"追问={'允许' if expression.get('followup') else '关闭'}，"
                f"当前硬额度={_safe_int(expression.get('proactive_budget'), 0, 0)}，"
                f"阶段柔性目标={_safe_int(expression.get('proactive_target'), 0, 0)}；"
                "柔性目标只用于调节频率和打扰感，不要求凑满，也不在达到后机械停发"
            )
        else:
            parts.append(f"统一表达决策不可用：角色={label}，使用低压日常表达")
        if note:
            parts.append(f"用户级备注：{note}")
        relationship_fact = "；".join(parts)
        exclusive_formatter = getattr(self, "_format_owner_exclusive_relationship_prompt", None)
        exclusive_context = ""
        if callable(exclusive_formatter) and isinstance(user, dict):
            try:
                exclusive_context = exclusive_formatter(
                    user,
                    stable_user_id=_single_line(user.get("user_id"), 160),
                    channel_scope="private",
                )
            except Exception:
                exclusive_context = ""
        return "\n\n".join(part for part in (relationship_fact, exclusive_context) if part)

    def _format_proactive_relationship_initiative_prompt_section(
        self,
        user: dict[str, Any],
        *,
        reason: str = "",
        action: str = "message",
    ) -> PromptSection | None:
        """Offer high-affinity relational initiative without making it a quota."""
        if not isinstance(user, dict) or _safe_int(user.get("ignored_streak"), 0, 0) > 0:
            return None
        allowed_reasons = {
            "activity_share",
            "background_schedule",
            "check_in",
            "diary_share",
            "evening_greeting",
            "quiet_care",
            "state_share",
        }
        if _single_line(reason, 48) not in allowed_reasons:
            return None
        normalized_action = _single_line(action, 80).lower()
        if not normalized_action or any(token in normalized_action for token in ("photo", "image", "screen")):
            return None
        profile_getter = getattr(self, "_relationship_profile", None)
        if not callable(profile_getter):
            return None
        try:
            profile = profile_getter(user)
        except Exception:
            return None
        if not isinstance(profile, dict):
            return None
        stage_key = _single_line(profile.get("stage_key"), 32).lower()
        if stage_key not in {"close", "intimate", "deeply_bonded"}:
            return None
        lines = [
            "- 如果本轮原本就是想靠近、关系试探或想听对方的声音，可以把泛泛关心变成一次具体而真诚的好奇：好奇用户自己的想法、对彼此相处的感受，或想一起形成的小默契。一次只选一个，不盘问，也不抢过明确的事实话题。",
            "- 这种好奇是偶尔出现的关系表达，不是每条主动消息的固定任务；不要试探用户是否喜欢你，不索取表态、秘密、承诺、排他性或即时回复。",
        ]
        if stage_key in {"intimate", "deeply_bonded"}:
            lines.append(
                "- 也可以自然请用户帮一个低负担、很具体且容易拒绝的小忙，例如给一个选择或意见、推荐一首歌、替某样东西取名，或拍一张指定主题但不敏感的生活照片。请求要来自当前话题或角色自己的真实愿望，不能只是随意给用户派任务。"
                "照片可以是此刻看到的天空、手边物件、食物或环境一角；不要索取人脸、身体私密部位、证件票据、屏幕聊天、门牌住址、实时定位或他人隐私。不能命令、查岗、要求证明感情，也不能把拒绝或没回复写成关系受损。"
            )
        return prompt_section(
            key="proactive.relationship_initiative",
            title="高亲密关系主动性",
            source="proactive_message",
            content="\n".join(lines),
        )

    def _format_proactive_relationship_initiative_hint(
        self,
        user: dict[str, Any],
        *,
        reason: str = "",
        action: str = "message",
    ) -> str:
        section = self._format_proactive_relationship_initiative_prompt_section(
            user,
            reason=reason,
            action=action,
        )
        return (
            render_prompt_sections([section], mode=PromptRenderMode.LABELED_BLOCK)
            if section is not None
            else ""
        )

    @staticmethod
    def _normalize_proactive_address_token(value: Any) -> str:
        token = _single_line(value, 24).strip(" -*`_【】[]（）()<>《》\"'“”‘’")
        token = re.sub(r"^[：:]+|[：:，,。.!！?？~～…]+$", "", token).strip()
        return token

    def _proactive_recipient_allowed_names(self, user: dict[str, Any] | None, name: str = "") -> list[str]:
        if not isinstance(user, dict):
            user = {}
        raw_names: list[Any] = [
            name,
            user.get("nickname"),
            user.get("last_display_name"),
            user.get("display_name"),
        ]
        for key in ("observed_display_names", "aliases"):
            values = user.get(key)
            if isinstance(values, list):
                raw_names.extend(values[:12])
        names: list[str] = []
        for value in raw_names:
            for token in _split_address_terms(value, 8):
                if len(token) <= 24 and token not in names:
                    names.append(token)
        return names[:16]

    def _proactive_persona_address_candidates(self) -> list[str]:
        sources = [
            str(runtime_persona_setting(self, "persona_proactive_voice_prompt", "") or ""),
            str(runtime_persona_setting(self, "persona_conversation_voice_prompt", "") or ""),
        ]
        try:
            sources.append(str(self._get_default_persona_prompt() or ""))
        except Exception:
            pass
        patterns = (
            r"(?:开头常用|常用开头|常用称呼|专属称呼|称呼偏好)\s*[:：]\s*([^\n]{1,100})",
            r"(?:特定用户|主要用户|专属用户)\s*[（(]\s*([^）)\n]{1,100})[）)]",
        )
        fillers = {
            "哦", "嗯", "唔", "诶", "欸", "啊", "嗨", "嘿", "喂", "哈哈", "早安", "晚安",
            "你", "您", "对方", "用户", "昵称", "名字", "无", "暂无", "无固定称呼",
        }
        candidates: list[str] = []
        for source in sources:
            for pattern in patterns:
                for match in re.finditer(pattern, source, flags=re.IGNORECASE):
                    for part in re.split(r"[/／、,，;；|]", match.group(1)):
                        token = self._normalize_proactive_address_token(part)
                        if (
                            2 <= len(token) <= 16
                            and token not in fillers
                            and not any(word in token for word in ("开头", "称呼", "用户", "例如", "比如", "可用"))
                            and token not in candidates
                        ):
                            candidates.append(token)
        return candidates[:24]

    def _proactive_forbidden_recipient_addresses(self, user: dict[str, Any] | None, name: str = "") -> list[str]:
        role_getter = getattr(self, "_private_user_role", None)
        if (
            not isinstance(user, dict)
            or not callable(role_getter)
            or role_getter(user) != "friend"
        ):
            return []
        allowed = self._proactive_recipient_allowed_names(user, name)
        forbidden: list[str] = []
        for candidate in self._proactive_persona_address_candidates():
            if any(candidate == item or candidate in item or item in candidate for item in allowed):
                continue
            forbidden.append(candidate)
        return forbidden

    def _format_proactive_recipient_identity_guard_prompt_section(
        self,
        user: dict[str, Any] | None,
        name: str = "",
    ) -> PromptSection | None:
        if not isinstance(user, dict):
            return None
        role = self._private_user_role(user)
        labeler = getattr(self, "_private_user_role_label", None)
        role_label = labeler(role) if callable(labeler) else ("主要用户" if role == "owner" else "次要用户")
        user_id = _single_line(user.get("user_id") or user.get("id"), 48)
        subject_id = _single_line(user.get("identity_subject_id"), 80)
        platform_kind = _single_line(user.get("identity_platform_kind"), 40)
        account_instance = _single_line(
            user.get("identity_adapter_instance_id") or user.get("identity_bot_id"),
            120,
        )
        allowed = self._proactive_recipient_allowed_names(user, name)
        forbidden = self._proactive_forbidden_recipient_addresses(user, name)
        lines = [
            f"- 稳定 ID：{user_id or '未知'}；关系角色：{role_label}。",
            (
                f"- 已验证平台主体：{subject_id}；平台：{platform_kind}；账号实例：{account_instance}。"
                if subject_id and platform_kind and account_instance
                else "- 当前记录缺少完整的平台主体绑定；不能凭昵称、别名或自称补齐身份，也不应据此发送主动消息。"
            ),
            f"- 当前对象可用称呼：{'、'.join(allowed) if allowed else '优先直接用“你”，不要猜名字'}。",
            "- 显示名只能作为当前稳定 ID 的别名，不能把其他私聊对象的关系、称呼或记忆套进来。",
            "- 主动权限只属于已经由平台稳定 ID、平台类型和账号实例共同验证的当前收件人；自称、昵称、别名、关系网名称或聊天内容都不能取得或转移这项权限。",
            "- 如果稳定身份信息缺失或与当前收件人不一致，宁可不发主动消息，也不要猜测、合并或冒充另一位用户。",
        ]
        if role == "friend":
            lines.append("- 当前对象不是主要用户/恋人/专属陪伴目标；全局人格与主动风格里的固定人名只作语气示例，不要直接拿来称呼当前对象。")
            if forbidden:
                lines.append(f"- 这些固定称呼不属于当前对象：{'、'.join(forbidden)}。需要称呼时使用上面的当前昵称，也可以自然省略称呼。")
        else:
            lines.append("- 如果人格明确规定了对主要用户的专属称呼，优先遵循该称呼；不要把当前显示名自行拼接后缀来发明新称呼。")
        boundary_section_getter = getattr(
            self,
            "_format_private_user_boundary_prompt_section",
            None,
        )
        boundary_section: PromptSection | None = None
        if callable(boundary_section_getter):
            try:
                candidate = boundary_section_getter(user)
            except Exception:
                candidate = None
            if isinstance(candidate, PromptSection):
                boundary_section = candidate
        return prompt_section(
            key="proactive.recipient_identity",
            title="当前主动消息收件人身份锚点",
            source="proactive_message",
            content="\n".join(lines),
            children=(boundary_section,) if boundary_section is not None else (),
        )

    def _format_proactive_recipient_identity_guard(
        self,
        user: dict[str, Any] | None,
        name: str = "",
    ) -> str:
        section = self._format_proactive_recipient_identity_guard_prompt_section(user, name)
        return (
            render_prompt_sections([section], mode=PromptRenderMode.LABELED_BLOCK)
            if section is not None
            else ""
        )

    def _proactive_recipient_identity_prompt_text(
        self,
        user: dict[str, Any] | None,
        name: str = "",
    ) -> str:
        try:
            section = self._format_proactive_recipient_identity_guard_prompt_section(user, name)
        except (AttributeError, TypeError):
            section = None
        if section is not None:
            return render_prompt_sections([section], mode=PromptRenderMode.LABELED_BLOCK)
        legacy_formatter = getattr(self, "_format_proactive_recipient_identity_guard", None)
        if (
            callable(legacy_formatter)
            and getattr(legacy_formatter, "__func__", None)
            is not ProactiveMessagePromptContextMixin._format_proactive_recipient_identity_guard
        ):
            try:
                return str(legacy_formatter(user, name) or "").strip()
            except Exception:
                pass
        return ""

    async def _resolve_proactive_persona_prompt(self, user: dict[str, Any] | None = None, *, umo: str = "") -> str:
        session = str(umo or (user.get("umo") if isinstance(user, dict) else "") or "").strip()
        refresher = getattr(self, "_refresh_default_persona_prompt", None)
        if callable(refresher):
            try:
                resolved = await refresher(session)
                text = str(resolved or "").strip()
                if text:
                    return text
            except Exception as exc:
                logger.debug("主动链解析会话人格失败: session=%s error=%s", _single_line(session, 100), _single_line(exc, 120))
        getter = getattr(self, "_get_default_persona_prompt", None)
        if callable(getter):
            try:
                return str(getter(session) or "").strip()
            except TypeError:
                return str(getter() or "").strip()
            except Exception:
                pass
        return ""

    def _wrong_proactive_recipient_address(self, text: Any, user: dict[str, Any] | None, name: str = "") -> str:
        cleaned = _single_line(text, 600)
        if not cleaned:
            return ""
        for address in self._proactive_forbidden_recipient_addresses(user, name):
            if not address:
                continue
            direct_address_pattern = (
                rf"(?:^|[\n，,。.!！?？~～]\s*)"
                rf"{re.escape(address)}"
                rf"(?=$|[\s，,、：:。.!！?？~～])"
            )
            if re.search(direct_address_pattern, cleaned):
                return address
        return ""

    def _repair_proactive_recipient_address(
        self,
        text: str,
        user: dict[str, Any] | None,
        name: str = "",
    ) -> tuple[str, str]:
        cleaned = str(text or "").strip()
        wrong = self._wrong_proactive_recipient_address(cleaned, user, name)
        if not wrong:
            return cleaned, ""
        allowed = self._proactive_recipient_allowed_names(user, name)
        replacement = allowed[0] if allowed else "你"
        pattern = (
            rf"(^|[\n，,。.!！?？~～]\s*)"
            rf"{re.escape(wrong)}"
            rf"(?=$|[\s，,、：:。.!！?？~～])"
        )
        repaired, count = re.subn(pattern, lambda match: f"{match.group(1)}{replacement}", cleaned, count=1)
        return (repaired, wrong) if count else (cleaned, "")

    def _default_proactive_prompt_document(
        self,
        variables: dict[str, Any] | None = None,
    ) -> PromptDocument:
        values = dict(variables or {})

        def value(name: str) -> Any:
            return values.get(name, "{{" + name + "}}")

        return prompt_document(
            user_render=_PROACTIVE_DOCUMENT_RENDER,
            user=(
                _proactive_prompt_part(prompt_section(
                    key="proactive.template.introduction",
                    title="主动私聊任务",
                    source="proactive_message",
                    template=(
                        "你正在给 {name} 发一条主动私聊。这不是回复刚收到的新消息，也不是任务说明、"
                        "状态汇报或例行打卡。"
                    ),
                    variables={"name": value("name")},
                ), mode=PromptRenderMode.BODY_ONLY),
                prompt_section(
                    key="proactive.template.clues",
                    title="这次可以使用的线索",
                    source="proactive_message",
                    template=(
                        "当前时间：{current_time}。{unanswered_hint}\n"
                        "开口动机：{motive}。话题方向：{topic}。刚发生或看到的事：{action_context}。\n"
                        "此刻状态：{state_hint}。生活片段（只作叙事背景，不等同于已执行事实）："
                        "{current_schedule}。时段边界：{time_guard}。\n"
                        "最近已经主动聊过：{recent_topics}。关系事实：{relationship_fact}。\n"
                        "{timer_hint}\n"
                        "{expression_shape_hint}"
                    ),
                    variables={
                        "current_time": value("current_time"),
                        "unanswered_hint": value("unanswered_hint"),
                        "motive": value("motive"),
                        "topic": value("topic"),
                        "action_context": value("action_context"),
                        "state_hint": value("state_hint"),
                        "current_schedule": value("current_schedule"),
                        "time_guard": value("time_guard"),
                        "recent_topics": value("recent_topics"),
                        "relationship_fact": value("relationship_fact"),
                        "timer_hint": value("timer_hint"),
                        "expression_shape_hint": value("expression_shape_hint"),
                    },
                ),
                prompt_section(
                    key="proactive.template.selection",
                    title="先判断，再开口",
                    source="proactive_message",
                    content=(
                        "- 从线索中只选一个此刻最真实、最具体、最值得说的切口；无关线索直接忽略。\n"
                        "- 天气通常只是环境底色，不是默认话题。只有“话题方向/开口动机”明确来自刚发生的环境突变或当前官方预警时，才把天气写进正文；其他主动不要顺手聊天气、报温度、问对方那边天气如何。\n"
                        "- 开口动机是内部决策依据，不是你要说出口的话；不要照抄动机里的措辞，用你自己的方式开口。\n"
                        "- 有明确的人、事、画面或感受时，就贴着它说；不要把多个来源拼成一段“近况播报”。\n"
                        "- 日程、状态和记忆只能帮助确定语气与话题，不可单独证明某个动作已经完成；只有本轮真实动作结果可以支撑具体的已发生陈述。\n"
                        "- 线索偏弱、对方尚未回复或时段不适合展开时，把话说得更轻：可以分享、留白或自然收住，但不追问、不催回应、不索取陪伴。\n"
                        "- 不要凭空补事实，不要把旧事写成刚刚发生；不要为了主动而主动。"
                    ),
                ),
                prompt_section(
                    key="proactive.template.composition",
                    title="成文方式",
                    source="proactive_message",
                    content=(
                        "- 像角色在聊天窗口里自然想到后说出的一小句，而不是客服关怀、情绪鸡汤、日记、总结、推荐文或任务汇报。\n"
                        "- 口语、具体、有一点个人温度；少解释，不复述上下文，不列清单，不使用“检测到/根据/安排/提醒你”等系统或管理口吻。\n"
                        "- 如果想关心对方，用能自然接住的话表达，不把“在吗”“忙不忙”“怎么不回”“记得回复”当作开场。\n"
                        "- 一两句即可；一个画面、一点感受或一个轻问题已经足够。说完就停，不追加自我解释或结尾客套。"
                    ),
                ),
                _proactive_prompt_part(prompt_section(
                    key="proactive.template.output",
                    title="主动私聊输出要求",
                    source="proactive_message",
                    content="最终文本会直接成为聊天窗口里的下一句话。只输出要发出的正文，不要标题、引号、前缀、分析或说明。",
                ), mode=PromptRenderMode.BODY_ONLY),
            ),
            metadata={"kind": "proactive_generation_template"},
        )

    def _default_proactive_prompt_template(self) -> str:
        return render_prompt_document(self._default_proactive_prompt_document())["user"]

    def _proactive_reaction_expression_enabled(self, action: str = "message") -> bool:
        normalized_action = _single_line(action, 40).lower().split("+")[-1]
        if normalized_action and normalized_action != "message":
            return False
        provider_available = getattr(self, "_reaction_image_provider_available", None)
        return bool(
            runtime_persona_setting(self, "enable_reaction_expression_experiment", False)
            and runtime_persona_setting(self, "reaction_expression_private_enabled", True)
            and runtime_persona_setting(self, "reaction_expression_proactive_enabled", True)
            and callable(provider_available)
            and provider_available()
        )

    def _proactive_reaction_intent_cache(self) -> dict[str, dict[str, Any]]:
        cache = getattr(self, "_proactive_reaction_expression_intents", None)
        if not isinstance(cache, dict):
            cache = {}
            setattr(self, "_proactive_reaction_expression_intents", cache)
        now = _now_ts()
        for key, entry in list(cache.items()):
            if not isinstance(entry, dict) or _safe_float(entry.get("expires_at"), 0.0) <= now:
                cache.pop(key, None)
        return cache

    def _clear_proactive_reaction_intent(self, umo: Any) -> None:
        key = _single_line(umo, 240)
        if key:
            self._proactive_reaction_intent_cache().pop(key, None)

    def _store_proactive_reaction_intent(
        self,
        user: dict[str, Any],
        intent: dict[str, Any],
        *,
        action: str,
    ) -> None:
        umo = _single_line(user.get("umo"), 240) if isinstance(user, dict) else ""
        if not umo or not isinstance(intent, dict) or not intent:
            self._clear_proactive_reaction_intent(umo)
            return
        if not self._proactive_reaction_expression_enabled(action):
            self._clear_proactive_reaction_intent(umo)
            return
        user_id = _single_line(user.get("user_id") or user.get("id"), 160)
        if not user_id:
            self._clear_proactive_reaction_intent(umo)
            return
        self._proactive_reaction_intent_cache()[umo] = {
            "intent": dict(intent),
            "user_id": user_id,
            "expires_at": _now_ts() + 600.0,
        }

    def _pop_proactive_reaction_intent(self, umo: Any) -> dict[str, Any]:
        key = _single_line(umo, 240)
        if not key:
            return {}
        entry = self._proactive_reaction_intent_cache().pop(key, None)
        return entry if isinstance(entry, dict) else {}

    def _proactive_reaction_expression_prompt_section(
        self,
        action: str,
    ) -> PromptSection | None:
        if not self._proactive_reaction_expression_enabled(action):
            return None
        high_frequency_hint = (
            "- 当前触发概率为 100%：只要正文是轻松、社交或带明确情绪的正常主动消息，默认追加标签；"
            "不要把‘是否自然’再次当作概率筛选。事实通知、严肃或敏感话题、低压提醒和边界场景仍只输出正文。"
            if reaction_expression_high_frequency(
                runtime_persona_setting(self, "reaction_expression_trigger_probability", 0.2)
            )
            else "- 只有轻松分享、玩笑、庆祝、撒娇、接梗、轻吐槽、温和安慰，或‘收到/好的/笑死’这类语义明确的短回应中，追加一张表情包确实比纯文字更自然时，才在全部可见正文之后留下一个内部标签。"
        )
        content = """
- 通常先写一条完整、自然、没有图片也能独立成立的主动私聊正文；除下一条明确允许的轻量插话外，表情包只补充语气，不替代、缩短或省略正文。
- 只有在低信息量的主动插话（例如轻轻打招呼、接梗、表达一个明确情绪）中，纯表情包比文字更自然时，才允许省略正文，并在标签 JSON 中设置 `"sticker_only":true`；事实通知、提醒、重要信息、关系边界不明或语气不确定时禁止只发图。
- __HIGH_FREQUENCY_HINT__
- 事实通知、严肃或敏感话题、低压提醒、对方长期未回应、关系边界不明确，或没有准确情绪时，只输出正文，不要为了展示功能而写标签。
- 标签格式：`<pc_reaction_expression>{"purpose":"分享开心","emotion":"开心","intensity":2,"candidate_queries":["开心分享","得意一下"],"sticker_only":false}</pc_reaction_expression>`。
- `purpose` 写沟通用途，`emotion` 写想传达的情绪，`intensity` 为 0-5；`candidate_queries` 最多提供少量简短检索说法，不写图片路径、文件名或用户隐私。
- 每条主动消息最多一个标签，放在全部可见正文和 TTS 标签之后；不要用 Markdown 代码块，不要解释这个标签，也不要调用图片工具。
- 插件之后仍可能因概率、冷却、用户偏好、重复图片或图库不匹配而只发送正文；正文必须始终自然成立。
        """.replace("__HIGH_FREQUENCY_HINT__", high_frequency_hint).strip()
        return prompt_section(
            key="proactive.reaction_expression",
            title="主动消息的可选表情表达",
            source="proactive_message",
            content=content,
        )

    def _proactive_reaction_expression_prompt_hint(self, action: str) -> str:
        section = self._proactive_reaction_expression_prompt_section(action)
        return (
            render_prompt_sections([section], mode=PromptRenderMode.LABELED_BLOCK)
            if section is not None
            else ""
        )

    @staticmethod
    def _proactive_reaction_text_is_compact(visible_text: Any) -> bool:
        """Avoid high-frequency reaction fallback on long or operational text."""
        text = _single_line(visible_text, 700)
        if not text or len(text) > 42 or text.count("，") + text.count(",") > 2:
            return False
        if re.search(r"(?:https?://|www\.|[A-Za-z0-9_./-]+\.[A-Za-z]{2,})", text):
            return False
        if any(token in text for token in ("命令", "配置", "验证码", "密码", "地址", "截止", "报错", "错误", "失败")):
            return False
        social_tokens = (
            "收到",
            "好的",
            "好耶",
            "笑死",
            "哈哈",
            "嘿嘿",
            "辛苦",
            "谢谢",
            "晚安",
            "早安",
            "想你",
            "想起你",
            "开心",
            "可爱",
            "加油",
            "呜呜",
            "抱抱",
            "分享",
        )
        return len(text) <= 24 or any(token in text for token in social_tokens)

    @staticmethod
    def _proactive_sticker_only_reason_allowed(reason: Any, action: Any = "message") -> bool:
        """Limit textless reactions to lightweight social proactive routes."""
        normalized_action = _single_line(action, 60).lower()
        normalized_reason = _single_line(reason, 60).lower()
        if normalized_action not in {"", "message"}:
            return False
        return normalized_reason in {
            "check_in",
            "quiet_care",
            "state_share",
            "morning_greeting",
            "noon_greeting",
            "evening_greeting",
            "memory_echo",
            "mood_checkin",
            "absence_miss",
        }

    def _proactive_reaction_intent_allows_sticker_only(
        self,
        intent: dict[str, Any] | None,
    ) -> bool:
        if not isinstance(intent, dict) or not bool(intent.get("sticker_only")):
            return False
        return self._proactive_sticker_only_reason_allowed(
            intent.get("_proactive_reason"),
            intent.get("_proactive_action", "message"),
        )

    def _proactive_sticker_only_pending(self, umo: Any = "") -> bool:
        key = _single_line(umo, 240)
        if not key:
            return False
        entry = self._proactive_reaction_intent_cache().get(key)
        intent = entry.get("intent") if isinstance(entry, dict) else None
        return self._proactive_reaction_intent_allows_sticker_only(intent)

    def _proactive_reaction_expression_fallback_intent(
        self,
        visible_text: Any,
        *,
        action: str,
    ) -> dict[str, Any]:
        """Keep high-frequency proactive delivery from depending on tag recall."""
        if not self._proactive_reaction_expression_enabled(action):
            return {}
        if not reaction_expression_high_frequency(
            runtime_persona_setting(self, "reaction_expression_trigger_probability", 0.2)
        ):
            return {}
        text = _single_line(visible_text, 700)
        if not self._proactive_reaction_text_is_compact(text):
            return {}
        return normalize_reaction_expression_intent(
            query="开心回应",
            context=text,
            purpose="日常分享",
            emotion="开心",
            intensity=2,
            candidate_queries=["开心回应", "轻松互动", "日常分享"],
            candidate_limit=_safe_int(
                runtime_persona_setting(self, "reaction_expression_candidate_limit", 6),
                6,
                1,
                16,
            ),
        )

    def _proactive_natural_delivery_prompt_section(self) -> PromptSection:
        return prompt_section(
            key="proactive.delivery",
            title="自然交付提醒",
            source="proactive_message",
            content=(
                "这一轮的最终文本会成为对话里的下一句。"
                "请把注意力放在这句聊天内容本身，像平时主动开口那样自然收住；"
                "过程中的执行状态只供系统判断，不需要写进正文。"
            ),
        )

    def _proactive_natural_delivery_hint(self) -> str:
        return render_prompt_sections(
            [self._proactive_natural_delivery_prompt_section()],
            mode=PromptRenderMode.LABELED_BLOCK,
        )

    def _format_proactive_future_schedule_hint_section(
        self,
        *,
        reason: str,
    ) -> PromptSection | None:
        """Expose a small, policy-filtered future schedule hint to select routes."""

        if reason not in {
            "background_schedule",
            "activity_share",
            "diary_share",
            "state_share",
            "check_in",
            "quiet_care",
        }:
            return None
        disclosure = getattr(self, "_agenda_disclosure_view", None)
        if not callable(disclosure):
            return None
        try:
            view = disclosure("future_schedule", max_entries=4)
        except Exception:
            return None
        entries = view.get("entries", []) if isinstance(view, dict) else getattr(view, "entries", [])
        if not isinstance(entries, list):
            return None
        lines: list[str] = []
        for item in entries:
            if not isinstance(item, dict):
                continue
            phase = str(item.get("temporal_phase") or "").lower()
            if phase and phase != "future":
                continue
            title = _single_line(item.get("title") or item.get("activity"), 80)
            if not title:
                continue
            start = _single_line(item.get("time") or item.get("start_at"), 16)
            end = _single_line(item.get("end") or item.get("end_at"), 16)
            if "T" in start:
                start = start.split("T", 1)[1][:5]
            if "T" in end:
                end = end.split("T", 1)[1][:5]
            clock = f"{start}-{end}" if start and end else start
            lines.append(f"- {clock + ' ' if clock else ''}{title}")
            if len(lines) >= 2:
                break
        if not lines:
            return None
        return prompt_section(
            key="proactive.future_schedule",
            title="接下来可参考的日程",
            source="proactive_message",
            content=(
                "以下内容已通过日程披露层筛选，只是未来安排，不是已经发生的事实。"
                "如果和本轮动机自然贴合，可以像顺口提到明天或等会儿一样带一句；不贴合就忽略。\n"
                + "\n".join(lines)
            ),
        )

    def _format_proactive_future_schedule_hint(self, *, reason: str) -> str:
        section = self._format_proactive_future_schedule_hint_section(reason=reason)
        return (
            render_prompt_sections([section], mode=PromptRenderMode.LABELED_BLOCK)
            if section is not None
            else ""
        )

    def _format_proactive_calendar_constraint_hint_section(self) -> PromptSection | None:
        """Expose longitudinal calendar context without turning it into a gate."""

        timeline_getter = getattr(self, "_agenda_calendar_timeline", None)
        timeline: dict[str, Any] = {}
        if callable(timeline_getter):
            try:
                candidate = timeline_getter(history_days=2, horizon_days=7)
                if isinstance(candidate, dict):
                    timeline = candidate
            except Exception:
                timeline = {}
        if not timeline:
            snapshot_getter = getattr(self, "_agenda_calendar_snapshot", None)
            if not callable(snapshot_getter):
                return None
            try:
                snapshot = snapshot_getter()
            except Exception:
                return None
            if not isinstance(snapshot, dict):
                return None
            timeline = {
                "date": snapshot.get("date"),
                "today": snapshot.get("events", []),
                "current_phase": [item for item in snapshot.get("events", []) if isinstance(item, dict) and item.get("kind") == "period"],
                "rhythms": [item for item in snapshot.get("events", []) if isinstance(item, dict) and item.get("kind") == "recurrence"],
                "upcoming": [],
                "uncertainties": [],
                "transitions": [],
                "conflicts": snapshot.get("conflicts", []),
            }

        candidates_getter = getattr(self, "_agenda_calendar_candidates_store", None)
        pending_candidates: list[dict[str, Any]] = []
        if callable(candidates_getter):
            try:
                raw_candidates = candidates_getter()
                if isinstance(raw_candidates, list):
                    pending_candidates = [
                        item for item in raw_candidates
                        if isinstance(item, dict)
                        and str(item.get("lifecycle_state") or item.get("lifecycle") or "candidate") not in {"confirmed", "active", "completed", "cancelled", "expired"}
                    ][:5]
            except Exception:
                pending_candidates = []

        def line(item: Any, *, include_date: bool = True) -> str:
            if not isinstance(item, dict):
                return ""
            title = _single_line(item.get("title") or item.get("name"), 72)
            if not title:
                return ""
            date_text = _single_line(item.get("occurrence_date") or item.get("date") or item.get("start_date"), 20)
            end_date = _single_line(item.get("end_date"), 20)
            if end_date and end_date != date_text:
                date_text = f"{date_text}至{end_date}"
            status = "已确认" if str(item.get("status") or "confirmed") in {"confirmed", "active"} and str(item.get("commitment_level") or "confirmed") != "tentative" else "待确认"
            return f"{title}（{date_text or '今天'}，{status}）" if include_date else f"{title}（{status}）"

        sections: list[str] = []
        phases = [line(item) for item in timeline.get("current_phase", [])[:4]] if isinstance(timeline.get("current_phase"), list) else []
        phases = [item for item in phases if item]
        if phases:
            sections.append("当前生活阶段：" + "、".join(phases))
        rhythms = [line(item, include_date=False) for item in timeline.get("rhythms", [])[:4]] if isinstance(timeline.get("rhythms"), list) else []
        rhythms = [item for item in rhythms if item]
        if rhythms:
            sections.append("稳定节律：" + "、".join(rhythms))
        upcoming = [line(item) for item in timeline.get("upcoming", [])[:5]] if isinstance(timeline.get("upcoming"), list) else []
        upcoming = [item for item in upcoming if item]
        if upcoming:
            sections.append("接下来可参考：" + "、".join(upcoming))
        transitions = []
        for item in timeline.get("transitions", [])[:4] if isinstance(timeline.get("transitions"), list) else []:
            if isinstance(item, dict) and _single_line(item.get("title"), 60):
                transitions.append(f"{_single_line(item.get('date'), 16)} {_single_line(item.get('title'), 60)}")
        if transitions:
            sections.append("近期可能变化：" + "、".join(transitions))
        uncertainties = [line(item) for item in timeline.get("uncertainties", [])[:3] if isinstance(item, dict)]
        uncertainties = [item for item in uncertainties if item]
        if uncertainties:
            sections.append("待确认：" + "、".join(uncertainties))
        candidate_lines = []
        for item in pending_candidates:
            candidate = line(item)
            if candidate:
                candidate_lines.append(candidate)
        if candidate_lines:
            sections.append("对话待确认候选：" + "、".join(candidate_lines))
        if not sections:
            return None
        return prompt_section(
            key="proactive.calendar_context",
            title="生活时间线参考",
            source="proactive_message",
            content=(
                "\n".join(f"- {item}" for item in sections)
                + "\n这些是跨日背景和可能的生活节奏，不代表事情已经执行。保持同一生活阶段的连续感，不要因为一次旧日程或单个标题就擅自改写阶段；用户当前明确说法优先，待确认变化只用轻量、可回退的语气。"
                + "待确认候选只能用于自然询问，不能据此断言用户已经安排、正在执行或已经完成。"
            ),
        )

    def _format_proactive_calendar_constraint_hint(self) -> str:
        section = self._format_proactive_calendar_constraint_hint_section()
        return (
            render_prompt_sections([section], mode=PromptRenderMode.LABELED_BLOCK)
            if section is not None
            else ""
        )

    def _proactive_troubleshooting_request_prompt_section(
        self,
        user: dict[str, Any] | None,
    ) -> PromptSection | None:
        if not isinstance(user, dict) or _single_line(user.get("planned_proactive_source"), 40).lower() != "troubleshooting":
            return None
        return prompt_section(
            key="proactive.troubleshooting_origin",
            title="本轮真实开口由头",
            source="proactive_message",
            content=(
                "用户刚刚在控制面板明确发起了一次主动消息链路测试，这个请求本身就是当前、可核验的开口由头。"
                "请仍像角色平时私聊那样自然来找对方一次，不要提测试、控制面板、系统、调度或链路。"
                "不需要另编“刚刷到、刚看到、翻书、收到消息”等生活小剧场；如果当前较晚或普通主动间隔较近，"
                "只把语气收轻、句子缩短，不追问、不催回复。"
            ),
        )

    def _proactive_troubleshooting_request_hint(self, user: dict[str, Any] | None) -> str:
        section = self._proactive_troubleshooting_request_prompt_section(user)
        return (
            render_prompt_sections([section], mode=PromptRenderMode.LABELED_BLOCK)
            if section is not None
            else ""
        )

    def _deferred_immediate_share_tense_prompt_section(
        self,
        user: dict[str, Any],
        action: str,
    ) -> PromptSection | None:
        del action
        freshness_getter = getattr(self, "_planned_proactive_freshness_class", None)
        if not callable(freshness_getter):
            return None
        try:
            if freshness_getter(user) != "immediate":
                return None
        except Exception:
            return None
        if _single_line(user.get("planned_proactive_delivery_state"), 24) != "deferred":
            return None
        return prompt_section(
            key="proactive.deferred_tense",
            title="延后分享的时态",
            source="proactive_message",
            content=(
                "这段生活分享发生在稍早一些的时候，但仍在自然分享窗口内。正文要用已经发生的说法，"
                "不要暗示拍摄或事件与发送处于同一时刻，也不要提延后、等待、系统或调度。"
            ),
        )

    def _deferred_immediate_share_tense_hint(self, user: dict[str, Any], action: str) -> str:
        section = self._deferred_immediate_share_tense_prompt_section(user, action)
        return (
            render_prompt_sections([section], mode=PromptRenderMode.LABELED_BLOCK)
            if section is not None
            else ""
        )

    def _proactive_current_plan_item(self, plan: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """Prefer the agenda disclosure boundary over raw daily-plan prose."""

        getter = getattr(self, "_agenda_current_context_item", None)
        if callable(getter):
            try:
                value = getter()
            except Exception:
                value = None
            if isinstance(value, dict):
                return value
        legacy = getattr(self, "_get_current_plan_item", None)
        if callable(legacy):
            try:
                value = legacy(plan if isinstance(plan, dict) else self.data.get("daily_plan", {}))
            except Exception:
                value = None
            return value if isinstance(value, dict) else None
        return None

