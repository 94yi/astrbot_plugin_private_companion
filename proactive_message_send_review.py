# -*- coding: utf-8 -*-
"""send_review 域。

由 tools/split_mixin_domain.py 从 proactive_message.py 机械抽取（21 个方法 + 0 个模块级名字 + 0 个类级赋值 / 1417 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveMessageMixin）。
"""
from __future__ import annotations

import asyncio
import os
import re
import time
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
from .helpers import _now_ts, _safe_float, _safe_int, _single_line, _today_key
from .persona_config import runtime_persona_setting
from .proactive_message_shared import _PROACTIVE_DOCUMENT_RENDER, _persona_provider_id, _proactive_prompt_part
from .proactive_routes import PROACTIVE_ROUTE_REGISTRY
from datetime import datetime
from typing import Any

from .logging_util import get_module_logger

logger = get_module_logger(__name__)



# ---- 宿主全局转发层（由 tmp/refactor/autofix_domain_globals.py 生成）----
# ==== 需实时转发（可被 patch）：同名函数转发宿主 ====
def At(*args, **kwargs):
    from . import proactive_message as _host
    return getattr(_host, "At")(*args, **kwargs)
def CoreMessageComponents(*args, **kwargs):
    from . import proactive_message as _host
    return getattr(_host, "CoreMessageComponents")(*args, **kwargs)
def Image(*args, **kwargs):
    from . import proactive_message as _host
    return getattr(_host, "Image")(*args, **kwargs)
def _now_ts(*args, **kwargs):
    from . import proactive_message as _host
    return getattr(_host, "_now_ts")(*args, **kwargs)
# ---- 宿主全局转发层结束 ----

class ProactiveMessageSendReviewMixin:
    """send_review 域（从 ProactiveMessageMixin 拆出）。"""


    def _local_proactive_send_decision(
        self,
        user: dict[str, Any],
        text: str,
        *,
        reason: str,
        action: str,
        motive: str = "",
        topic: str = "",
        action_context: str = "",
    ) -> dict[str, Any]:
        strength = self._proactive_review_strength()
        cleaned = _single_line(text, 500)
        if not cleaned:
            return {"decision": "drop", "reason": "主动消息为空", "hard": True}
        external_info_reasons = {"bili_video_share", "news_share", "web_exploration_share"}
        external_share_active = reason in external_info_reasons
        link_platform_mismatch = self._proactive_link_platform_mismatch_reason(cleaned)
        if link_platform_mismatch:
            if external_share_active:
                external_fix = self._external_share_source_consistency_decision(
                    user,
                    cleaned,
                    reason=reason,
                    topic=topic,
                    motive=motive,
                    action_context=action_context,
                )
                if external_fix:
                    return external_fix
            return {
                "decision": "drop",
                "reason": link_platform_mismatch,
                "hard": True,
            }
        if reason == "environment_change" and re.search(
            r"https?://|(?:^|[^A-Za-z0-9])BV[0-9A-Za-z]{8,16}(?:$|[^A-Za-z0-9])|《[^》\n]{1,120}》",
            cleaned,
            flags=re.I,
        ):
            return {
                "decision": "drop",
                "reason": "环境变化主动消息混入了文章、视频或旧链接来源",
                "hard": True,
            }
        wrong_address = self._wrong_proactive_recipient_address(
            cleaned,
            user,
            _single_line(user.get("nickname"), 40),
        )
        if wrong_address:
            repaired_text, repaired_address = self._repair_proactive_recipient_address(
                cleaned,
                user,
                _single_line(user.get("nickname"), 40),
            )
            if repaired_address:
                return {
                    "decision": "rewrite",
                    "reason": f"已把收件人称呼纠正为当前昵称：{repaired_address}",
                    "text": repaired_text,
                    "hard": True,
                }
            return {
                "decision": "drop",
                "reason": f"主动正文无法确认收件人称呼：{wrong_address}",
                "hard": True,
            }
        outbound_guard = self._validate_proactive_outbound_candidate(
            cleaned,
            reason=reason,
            action=action,
            source="review",
        )
        guard_decision = str(outbound_guard.get("decision") or "send")
        if guard_decision == "drop":
            return {
                "decision": "drop",
                "reason": _single_line(outbound_guard.get("reason"), 120) or "主动候选疑似内部泄漏",
                "hard": bool(outbound_guard.get("hard", True)),
            }
        if guard_decision == "rewrite":
            rewritten_guard_text = _single_line(outbound_guard.get("text"), 500)
            if rewritten_guard_text:
                return {
                    "decision": "rewrite",
                    "reason": _single_line(outbound_guard.get("reason"), 120) or "清理主动候选内部残留",
                    "text": rewritten_guard_text,
                }
            return {"decision": "drop", "reason": _single_line(outbound_guard.get("reason"), 120) or "主动候选只剩内部残留", "hard": True}
        fact_decision = self._unverified_proactive_fact_decision(
            cleaned,
            reason=reason,
            action=action,
            action_context=action_context,
        )
        if fact_decision:
            return fact_decision
        semantics: dict[str, Any] = {}
        semantic_getter = getattr(self, "_planned_proactive_semantics", None)
        if callable(semantic_getter):
            try:
                semantics = semantic_getter(user)
            except Exception:
                semantics = {}
        semantic_kind = _single_line(semantics.get("kind"), 40)
        semantic_anchor_type = _single_line(semantics.get("anchor_type"), 40)
        semantic_score = _safe_float(semantics.get("score"), 0.5)
        semantic_pressure = _safe_float(semantics.get("pressure"), 0.4)
        semantic_risk = _safe_float(semantics.get("risk"), 0.0)
        default_hard_risk = 0.70 if strength == "lenient" else 0.45
        hard_risk_threshold = max(
            0.0,
            min(
                1.0,
                _safe_float(
                    runtime_persona_setting(
                        self, "proactive_review_hard_risk_threshold", default_hard_risk
                    ),
                    default_hard_risk,
                ),
            ),
        )
        low_score_threshold = max(
            0.0,
            min(
                1.0,
                _safe_float(
                    runtime_persona_setting(self, "proactive_review_low_score_threshold", 0.34),
                    0.34,
                ),
            ),
        )
        pressure_threshold = max(
            0.0,
            min(
                1.0,
                _safe_float(
                    runtime_persona_setting(self, "proactive_review_pressure_threshold", 0.55),
                    0.55,
                ),
            ),
        )
        if semantic_risk >= hard_risk_threshold:
            return {
                "decision": "drop",
                "reason": f"候选语义风险偏高 risk={semantic_risk:.2f}/{hard_risk_threshold:.2f}",
                "hard": True,
            }
        if strength != "lenient" and semantic_score < low_score_threshold and semantic_pressure >= pressure_threshold:
            return {"decision": "defer", "reason": "候选由头偏虚且打扰压力高", "delay_minutes": 75}
        reply_like_openers = (
            "好呀", "好啊", "可以呀", "行啊", "那就", "你说呢", "要不", "刚看到", "才看到",
            "你来了", "你叫我", "你问", "我帮你查", "我去问", "我去说",
        )
        matched_reply_opener = next((token for token in reply_like_openers if cleaned.startswith(token)), "")
        if matched_reply_opener and not (external_share_active and matched_reply_opener in {"刚看到", "才看到"}):
            if strength != "strict":
                rewritten = re.sub(
                    r"^(?:好呀|好啊|可以呀|行啊|那就|你说呢|要不|刚看到|才看到|你来了|你叫我|你问|我帮你查|我去问|我去说)[，,。！!？?\s]*",
                    "",
                    cleaned,
                    count=1,
                ).strip()
                if rewritten and len(rewritten) >= 4:
                    return {"decision": "rewrite", "reason": "去掉回复式开头", "text": rewritten}
            return {"decision": "drop", "reason": "像是在回复刚发来的消息"}
        motive_leak_repaired = self._strip_proactive_motive_leak_text(cleaned)
        if motive_leak_repaired != cleaned:
            if motive_leak_repaired and len(motive_leak_repaired) >= 2:
                return {"decision": "rewrite", "reason": "去掉主动动机自述", "text": motive_leak_repaired}
            return {"decision": "defer", "reason": "主动消息只剩动机自述", "delay_minutes": 75}
        vague = ("想你了", "来看看你", "你在忙什么", "最近怎么样", "吃了吗", "辛苦了", "在吗", "忙不忙")
        if strength != "lenient" and reason in {"check_in", "quiet_care", "state_share"} and any(token in cleaned for token in vague):
            return {"decision": "defer", "reason": "普通主动过于泛泛", "delay_minutes": 60}
        if strength != "lenient" and semantic_kind in {"self_share", "external_share", "observation"} and any(token in cleaned for token in vague):
            return {"decision": "defer", "reason": "生成结果偏离分享型由头", "delay_minutes": 60}
        if external_share_active:
            external_fix = self._external_share_source_consistency_decision(
                user,
                cleaned,
                reason=reason,
                topic=topic,
                motive=motive,
                action_context=action_context,
            )
            if external_fix:
                return external_fix
        role = self._private_user_role(user) if isinstance(user, dict) else "friend"
        # 外部分享（新闻/B站/搜索）跳过「疑似混入其他私聊互动」检查（与 PR #168 同源）：
        # 该检查的 _daily_plan_clause_has_named_message_interaction 正则会把新闻正文
        # 「看到个消息」的「个」误判为私聊 target（08-25 16:19 红色沙漠新闻被此误杀实锤）。
        # 外部分享 gen 不含对其他用户的私聊互动描述，跳过不会漏真问题。
        if role == "owner" and not external_share_active:
            social_checker = getattr(self, "_daily_plan_clause_has_named_message_interaction", None)
            has_cross_private_interaction = False
            if callable(social_checker):
                try:
                    has_cross_private_interaction = bool(social_checker(cleaned))
                except Exception:
                    has_cross_private_interaction = False
            if has_cross_private_interaction or any(token in cleaned for token in ("朋友那边", "朋友用户", "朋友私聊", "次要用户那边", "次要用户私聊")):
                return {"decision": "drop", "reason": "疑似混入其他私聊互动", "hard": True}
        if _safe_int(user.get("ignored_streak"), 0, 0) >= 1 and cleaned.count("？") + cleaned.count("?") >= 2:
            return {"decision": "rewrite", "reason": "未回应状态下问题太多", "text": re.split(r"[？?]", cleaned, maxsplit=1)[0].rstrip("，,。") + "。"}
        return {"decision": "send", "reason": "本地检查通过"}

    def _strip_proactive_motive_leak_text(self, text: str) -> str:
        cleaned = str(text or "").strip()
        if not cleaned:
            return ""
        units: list[str] = []
        for line in cleaned.splitlines() or [cleaned]:
            units.extend(self._split_proactive_sentence_units(line))
        if not units:
            units = [cleaned]

        leak_unit_patterns = (
            r"(?:怕|担心)[^。！？\n]{0,16}(?:太早|太晚|打扰|吵到|烦到)",
            r"(?:先|又|就)?(?:收住|忍住|憋住|忍了一下|放了一会)[^。！？\n]{0,20}",
            r"(?:结果|后来)?[^。！？\n]{0,12}(?:绕了一圈|转了一圈|想了半天)[^。！？\n]{0,24}(?:来找你|找你|说出口)",
            r"(?:还是|又|最后|结果)[^。！？\n]{0,12}(?:来找你|找你|跑来找你|过来找你)[啦了啊呀]*",
            r"(?:没什么事|没有别的事|也没什么)[^。！？\n]{0,18}(?:就是|只是)?想(?:来)?(?:找你|跟你说话|和你说话|说一句)",
        )
        leak_clause_patterns = (
            r"[，,、\s]*(?:刚[^，。！？\n]{0,24})?(?:就|还是)?想(?:先)?(?:跟|和)?你(?:说早安|说早|说一句|说点什么|打个招呼|聊两句|说话)[^，。！？\n]*",
            r"[，,、\s]*(?:中午|晚上|早上|这会儿|刚才|刚刚)?[^，。！？\n]{0,18}(?:就|又|还是)?想(?:顺手)?(?:来)?(?:找你|跟你打个照面|和你打个照面|往你这边冒个头)[^，。！？\n]*",
            r"[，,、\s]*(?:刚刚|刚才|这会儿|今天|明明|还是)?[^，。！？\n]{0,24}想(?:和|给|问|提醒|确认|看看)?用户[^，。！？\n]*",
            r"[，,、\s]*(?:怕|担心)[^，。！？\n]{0,16}(?:太早|太晚|打扰|吵到|烦到)[^，。！？\n]*",
            r"[，,、\s]*(?:就)?先(?:收住|忍住|憋住)[^，。！？\n]*",
            r"[，,、\s]*(?:结果|后来)?[^，。！？\n]{0,12}(?:绕了一圈|转了一圈|想了半天)[^，。！？\n]*",
            r"[，,、\s]*莫名觉得[^，。！？\n]*",
            r"[，,、\s]*(?:顺手)(?:丢给你|放这儿|递给你|想起|分享一下|分享一下)[^，。！？\n]*",
            r"[，,、\s]*(?:多看一眼|也会留意这个|也会看一眼)[^，。！？\n]*",
            r"[，,、\s]*(?:只)?轻轻(?:提一句|提醒[^，。！？\n]*|说声|补上一句)[^，。！？\n]*",
            r"[，,、\s]*想(?:短短|轻轻)(?:说一句|提一句|说句话|提一声|说一下|打声招呼)[^，。！？\n]*",
            r"[，,、\s]*感觉和[^，。！？\n]{0,20}有点贴[^，。！？\n]*",
            r"[，,、\s]*想跟你说一句[^，。！？\n]*",
        )
        kept: list[str] = []
        changed = False
        for raw_unit in units:
            unit = str(raw_unit or "").strip()
            if not unit:
                continue
            if any(re.search(pattern, unit) for pattern in leak_unit_patterns):
                changed = True
                continue
            repaired = unit
            for pattern in leak_clause_patterns:
                repaired, count = re.subn(pattern, "", repaired)
                changed = changed or count > 0
            repaired = repaired.strip(" ，,、。！？!?；;")
            if repaired:
                kept.append(self._ensure_chat_sentence_punctuation(repaired))
            elif repaired != unit:
                changed = True
        if not changed:
            return cleaned
        return "\n".join(kept)[:260].strip()

    def _proactive_review_strength(self) -> str:
        strength = str(
            runtime_persona_setting(self, "proactive_review_strength", "lenient") or "lenient"
        ).strip().lower()
        return strength if strength in {"lenient", "balanced", "strict"} else "lenient"

    def _effective_proactive_review_mode(self) -> str:
        mode = str(
            runtime_persona_setting(self, "proactive_review_mode", "full") or "full"
        ).strip().lower()
        return mode if mode in {"local_only", "severe_only", "full"} else "full"

    @staticmethod
    def _proactive_review_hard_block_reason(reason: str) -> bool:
        text = str(reason or "")
        if not text:
            return False
        markers = (
            "隐私", "泄露", "越界", "风险", "危险", "敏感", "违规", "骚扰", "威胁",
            "其他私聊", "朋友私聊", "混入", "承诺工具", "承诺发图", "承诺语音", "承诺查询",
            "系统动作", "发送状态", "状态汇报", "工具执行", "工具结果", "工具回执",
            "执行回执", "发送回执", "系统回执", "不是角色真正",
        )
        return any(marker in text for marker in markers)

    def _balanced_proactive_defer_release_reason(
        self,
        user: dict[str, Any],
        *,
        note: str = "",
        now: float | None = None,
    ) -> str:
        if not isinstance(user, dict):
            return ""
        note_text = _single_line(note, 120)
        generic_defer = any(
            token in note_text
            for token in ("刚结束", "稍后", "稍候", "时机", "自然", "突兀", "间隔", "不合适")
        )
        if not generic_defer:
            return ""
        today = _today_key()
        sent_today = _safe_int(user.get("sent_today"), 0) if str(user.get("sent_day") or "") == today else 0
        if sent_today > 0:
            return ""
        last_sent_at = max(
            _safe_float(user.get("last_proactive_sent_at"), 0),
            _safe_float(user.get("last_sent"), 0),
        )
        if last_sent_at > 0 and datetime.fromtimestamp(last_sent_at).strftime("%Y-%m-%d") == today:
            return ""
        check_now = _now_ts() if now is None else now
        now_dt = datetime.fromtimestamp(check_now)
        if now_dt.hour * 60 + now_dt.minute < 10 * 60 + 30:
            return ""
        idle_getter = getattr(self, "_effective_user_idle_minutes", None)
        try:
            idle_minutes = (
                idle_getter(user)
                if callable(idle_getter)
                else _safe_int(runtime_persona_setting(self, "idle_minutes", 20), 20)
            )
        except Exception:
            idle_minutes = _safe_int(runtime_persona_setting(self, "idle_minutes", 20), 20)
        recent_private_at = max(
            _safe_float(user.get("last_user_message_at"), 0),
            _safe_float(user.get("last_private_seen"), 0),
        )
        if recent_private_at > 0 and check_now - recent_private_at < max(10, min(60, idle_minutes)) * 60:
            return ""
        return "今日尚无主动且候选非硬风险，标准强度低频放行"

    @staticmethod
    def _proactive_review_elapsed_text(seconds: float) -> str:
        if seconds < 0:
            return "未知"
        if seconds < 90:
            return "刚刚"
        if seconds < 3600:
            return f"约{max(1, int(seconds // 60))}分钟"
        if seconds < 86400:
            return f"约{max(1, int(seconds // 3600))}小时"
        return f"约{max(1, int(seconds // 86400))}天"

    @staticmethod
    def _proactive_has_verified_recent_fact_source(
        *,
        reason: str,
        action: str,
        action_context: str = "",
    ) -> bool:
        source_reasons = {
            "bili_video_share",
            "news_share",
            "web_exploration_share",
            "creative_share",
            "weather_alert",
            "goodnight_screen_check",
        }
        if str(reason or "").strip() in source_reasons:
            return True
        context = str(action_context or "")
        if str(reason or "").strip() == "group_share" and "群聊分享线索" in context:
            return True
        if re.search(r"(?:真实图片文件|图片路径|真实动作结果|工具结果|来源链接|https?://)", context, re.I):
            return True
        return str(action or "message").strip() not in {"", "message", "photo_text"} and bool(_single_line(context, 240))

    def _unverified_proactive_fact_decision(
        self,
        text: str,
        *,
        reason: str,
        action: str,
        action_context: str = "",
    ) -> dict[str, Any] | None:
        if self._proactive_has_verified_recent_fact_source(
            reason=reason,
            action=action,
            action_context=action_context,
        ):
            return None
        recent_self_action = re.compile(
            r"(?:我\s*)?(?:刚刚|刚才|方才|刚|才)\s*"
            r"(?:刷到|刷了|看到|看见|听到|听见|读到|发现|碰到|遇到|收到|"
            r"买了|拍了|做了|画了|写了|吃了|喝了|回到|到家|出门|回来)"
        )
        stale_meal_attribution = re.compile(
            r"你[^。！？!?；;…~～]{0,12}(?:昨天|昨晚)[^。！？!?；;…~～]{0,16}"
            r"(?:吃的|点的|喝的|吃了|点了|喝了)"
        )
        unsafe_units: list[str] = []
        safe_units: list[str] = []
        for unit in self._split_proactive_sentence_units(text):
            recent_claim = bool(recent_self_action.search(unit))
            stale_claim = reason in {"meal_care", "meal_care_followup"} and bool(stale_meal_attribution.search(unit))
            if recent_claim or stale_claim:
                unsafe_units.append(unit)
            else:
                safe_units.append(unit)
        if not unsafe_units:
            return None
        repaired = " ".join(safe_units).strip()
        if repaired and len(re.sub(r"\s+", "", repaired)) >= 4:
            return {
                "decision": "rewrite",
                "reason": "已移除无真实来源的近期动作或旧饮食归因",
                "text": repaired,
                "hard": True,
            }
        return {
            "decision": "drop",
            "reason": "主动正文依赖无真实来源的近期动作或旧饮食归因",
            "hard": True,
        }

    def _format_proactive_review_runtime_context(self, user: dict[str, Any], *, now: float | None = None) -> str:
        check_now = _now_ts() if now is None else now
        now_dt = datetime.fromtimestamp(check_now)
        today = _today_key()
        sent_today = _safe_int(user.get("sent_today"), 0) if str(user.get("sent_day") or "") == today else 0
        last_sent_at = max(
            _safe_float(user.get("last_proactive_sent_at"), 0),
            _safe_float(user.get("last_sent"), 0),
        )
        activity_getter = getattr(self, "_latest_private_user_activity_ts", None)
        try:
            last_private_at = activity_getter(user) if callable(activity_getter) else 0
        except Exception:
            last_private_at = 0
        if last_private_at <= 0:
            last_private_at = max(
                _safe_float(user.get("last_user_message_at"), 0),
                _safe_float(user.get("last_private_seen"), 0),
            )
        idle_getter = getattr(self, "_effective_user_idle_minutes", None)
        interval_getter = getattr(self, "_effective_user_min_interval_minutes", None)
        try:
            idle_minutes = (
                idle_getter(user)
                if callable(idle_getter)
                else _safe_int(runtime_persona_setting(self, "idle_minutes", 20), 20)
            )
        except Exception:
            idle_minutes = _safe_int(runtime_persona_setting(self, "idle_minutes", 20), 20)
        try:
            min_interval = (
                interval_getter(user)
                if callable(interval_getter)
                else _safe_int(runtime_persona_setting(self, "min_interval_minutes", 80), 80)
            )
        except Exception:
            min_interval = _safe_int(
                runtime_persona_setting(self, "min_interval_minutes", 80), 80
            )
        private_elapsed = check_now - last_private_at if last_private_at > 0 else -1
        sent_elapsed = check_now - last_sent_at if last_sent_at > 0 else -1
        runtime_context = "\n".join(
            part
            for part in (
                f"当前判定时间：{now_dt.strftime('%Y-%m-%d %H:%M')}",
                f"今天已成功主动：{sent_today} 条",
                f"距用户上次私聊活动：{self._proactive_review_elapsed_text(private_elapsed)}；普通主动要求空闲约 {max(0, int(idle_minutes))} 分钟",
                f"距上次主动发送：{self._proactive_review_elapsed_text(sent_elapsed)}；普通主动最小间隔约 {max(0, int(min_interval))} 分钟",
                f"上次主动内容：{_single_line(user.get('last_proactive_message'), 120)}" if user.get("last_proactive_message") else "",
            )
            if part
        )
        calendar_hint = self._format_proactive_calendar_constraint_hint()
        return f"{runtime_context}\n{calendar_hint}".strip() if calendar_hint else runtime_context

    def _stale_proactive_review_defer_release_reason(
        self,
        user: dict[str, Any],
        *,
        note: str = "",
        reason: str = "",
        now: float | None = None,
    ) -> str:
        note_text = _single_line(note, 120)
        reason_key = _single_line(reason, 40).lower()
        time_sensitive_reasons = {
            "morning_greeting": 12 * 60,
            "noon_greeting": 15 * 60,
            "evening_greeting": 23 * 60 + 30,
            "environment_change": 120,
            "creative_share": 180,
            "reminder": 180,
            "meal_care": 120,
            "meal_care_followup": 120,
            "weather_alert": 180,
        }
        if not note_text and not reason_key:
            return ""
        check_now = _now_ts() if now is None else now
        if reason_key in time_sensitive_reasons:
            window_start = _safe_float(user.get("planned_proactive_window_start_at"), 0)
            expire_at = _safe_float(user.get("planned_proactive_expire_at"), 0)
            if expire_at > 0 and check_now >= expire_at:
                return "主动候选的有效窗口已结束，放弃过期复核结果并重新编排"
            if window_start > 0 and check_now - window_start >= time_sensitive_reasons[reason_key] * 60:
                return "主动候选已超过当前场景有效期，放弃过期复核结果并重新编排"
        if not re.search(r"(早安|今早|早上|睡前|晚安)", note_text):
            return ""
        now_minutes = datetime.fromtimestamp(check_now).hour * 60 + datetime.fromtimestamp(check_now).minute
        stale_after = 9 * 60 if ("睡前" in note_text or "晚安" in note_text) else 12 * 60
        if now_minutes < stale_after:
            return ""
        recent_private_at = max(
            _safe_float(user.get("last_user_message_at"), 0),
            _safe_float(user.get("last_private_seen"), 0),
        )
        if recent_private_at > 0 and check_now - recent_private_at < 45 * 60:
            return ""
        return "复核理由沿用了过期早间/睡前语境，已改按当前运行态放行"

    @staticmethod
    def _proactive_rewrite_blacklist_reason(text: str) -> str:
        """Reject structured model leakage without blocking ordinary chat words."""
        candidate = str(text or "")
        if not candidate:
            return ""
        patterns = (
            (r"```(?:json|python|javascript|text)?\s*", "改写残留代码块"),
            (r"\{[^{}\n]{0,600}(?:[\"'](?:decision|text|reason|status|tool|delay_minutes)[\"']\s*:)", "改写残留结构化 JSON"),
            (r"(?:作为(?:一个)?(?:AI|人工智能|语言模型)|我是(?:一个)?(?:AI|语言模型))", "改写暴露模型身份"),
            (r"(?:系统提示|系统消息|提示词泄漏|模型输出|模型回复|工具调用|调用工具|主动消息复核|附加组件(?:发送|列表))", "改写残留内部流程"),
            (r"[\"'](?:decision|text|reason|delay_minutes|planned_reason)[\"']\s*:", "改写残留 JSON 字段"),
        )
        for pattern, reason in patterns:
            if re.search(pattern, candidate, re.IGNORECASE | re.DOTALL):
                return reason
        return ""

    def _accept_proactive_rewrite(
        self,
        text: str,
        *,
        original_text: str = "",
        user: dict[str, Any] | None = None,
        reason: str = "",
        action: str = "",
        topic: str = "",
        motive: str = "",
        action_context: str = "",
        image_path: str = "",
    ) -> str | None:
        """Run the single acceptance chain shared by every proactive rewrite."""
        candidate = self._sanitize_action_boundaries(
            self._sanitize_proactive_text(str(text or "")),
            reason=reason,
            action=action,
            action_context=action_context,
            has_real_image=bool(image_path)
            or "真实图片文件：" in str(action_context or "")
            or "图片路径：" in str(action_context or ""),
        )
        candidate = self._normalize_proactive_sentence_flow(candidate)
        if not candidate:
            return None
        if re.fullmatch(
            r"[嗯哦唔呃诶欸啊呀哎噢喔哈]+[。！？!?…~～]*",
            re.sub(r"\s+", "", candidate),
        ):
            return None
        current_user = user if isinstance(user, dict) else {}
        recipient_name = _single_line(current_user.get("nickname"), 40)
        candidate, _ = self._repair_proactive_recipient_address(
            candidate,
            current_user,
            recipient_name,
        )
        if not candidate or self._wrong_proactive_recipient_address(candidate, current_user, recipient_name):
            return None
        meta_leak_checker = getattr(self, "_response_review_meta_leak_reason", None)
        if callable(meta_leak_checker) and meta_leak_checker(candidate):
            return None
        if self._framework_agent_meta_summary_leak(candidate):
            return None
        original_length = len(str(original_text or "").strip())
        if original_length and len(candidate) > max(original_length + 60, 240):
            return None
        if self._proactive_rewrite_blacklist_reason(candidate):
            return None
        if reason in {"bili_video_share", "news_share", "web_exploration_share"}:
            if self._external_share_source_consistency_decision(
                current_user,
                candidate,
                reason=reason,
                topic=topic,
                motive=motive,
                action_context=action_context,
            ):
                return None
        return candidate

    def _normalize_proactive_review_decision_policy(
        self,
        user: dict[str, Any],
        payload: dict[str, Any],
        *,
        strength: str,
        source: str = "model",
        reason: str = "",
        action: str = "",
        topic: str = "",
        motive: str = "",
        action_context: str = "",
        image_path: str = "",
        original_text: str = "",
    ) -> dict[str, Any]:
        """Normalize the final proactive content gate result."""
        if not isinstance(payload, dict):
            return {"decision": "send", "reason": "empty review result; local safety gate allowed the message"}
        decision = str(payload.get("decision") or "send").strip().lower()
        note = _single_line(payload.get("reason"), 120)
        reviewed_text = str(payload.get("text") or "").strip()
        delay_minutes = max(5, min(240, _safe_int(payload.get("delay_minutes"), 60, 5, 240)))
        if decision not in {"send", "rewrite", "defer", "drop"}:
            decision = "send"
        if decision == "rewrite" and not reviewed_text:
            decision = "drop"
            note = _single_line(f"{note or 'rewrite result is empty'}; candidate dropped", 120)
        if decision == "rewrite" and reviewed_text:
            accepted_text = self._accept_proactive_rewrite(
                reviewed_text,
                original_text=original_text,
                user=user,
                reason=reason,
                action=action,
                topic=topic,
                motive=motive,
                action_context=action_context,
                image_path=image_path,
            )
            if accepted_text is None:
                decision = "drop"
                reviewed_text = ""
                note = _single_line(f"{note or '改写未通过统一验收'}; 最终改写已拒绝", 120)
            else:
                reviewed_text = accepted_text
        return {
            "decision": decision,
            "text": reviewed_text if decision == "rewrite" else "",
            "reason": note or "proactive final content gate",
            "hard": bool(payload.get("hard")),
            "delay_minutes": delay_minutes if decision == "defer" else 0,
        }

    @staticmethod
    def _proactive_send_review_prompt_document(
        *,
        creative_excerpt_section: PromptSection | None,
        history: str,
        runtime_context: str,
        troubleshooting_context: str,
        fact_source_context: str,
        local_context: str,
        source_context: str,
        route_review_directive: str,
        persona_context: str,
        intent_hint: str,
        proactive_voice: str,
        expression_voice: str,
        recipient_identity: str,
        candidate: str,
    ) -> PromptDocument:
        def square(
            key: str,
            title: str,
            content: str,
            *,
            separator_before: str = "\n\n",
        ) -> PromptDocumentPart:
            return _proactive_prompt_part(
                prompt_section(
                    key=key,
                    title=title,
                    source="proactive_message",
                    content=content,
                ),
                label_style=PromptLabelStyle.SQUARE,
                separator_before=separator_before,
            )

        sections: list[PromptSection | PromptDocumentPart] = [
            _proactive_prompt_part(prompt_section(
                key="background.proactive_send_review.contract",
                title="主动消息发送终审",
                source="proactive_message",
                content=(
                    "You are the final content gate immediately before one proactive private message is sent.\n"
                    "Return JSON only. You must decide exactly one of send, rewrite, or drop.\n\n"
                    "Decision contract:\n"
                    "- send: the candidate is natural, persona-consistent, useful now, and ready to send unchanged. Leave text empty.\n"
                    "- rewrite: the message still has a concrete reason to exist, but needs a small rewrite to sound natural in this exact conversation. text must be the complete sendable final message.\n"
                    "- drop: do not send this candidate. Use it for weak, generic, intrusive, fabricated, context-conflicting, reply-to-nothing, internal-status, tool-result, or unsafe content.\n\n"
                    "Rules:\n"
                    "- This is a content gate, not a scheduler. Never output defer, waiting, or a delay.\n"
                    "- Read the recent conversation and runtime context first. The candidate must read like a natural message from the current persona, not a system-triggered interruption.\n"
                    "- Do not invent facts or promise tools, searches, media, relays, or actions that were not actually performed.\n"
                    "- Planned schedules, persona continuity, and message seeds are narrative inspiration, not evidence that an action happened.\n"
                    "- Relative dates such as yesterday must be supported by the recent conversation or an explicitly dated reliable source.\n"
                    "- Preserve real media context. Do not claim an image exists when none is attached.\n"
                    "- A rewrite must be shorter or similarly sized and must not add new factual claims.\n"
                    "- A rewrite must preserve the candidate's concrete communicative purpose. Never collapse a meaningful reminder, question, warning, or check-in into a standalone filler such as “嗯。”, “哦。”, “唔。”, or “诶。”. If no complete rewrite is better, choose send and keep the candidate unchanged.\n"
                    "- If a user has just been discussing something and the candidate cannot naturally fit, drop it; do not defer it.\n"
                    "- If the candidate or any model output contains a Provider/API error, policy refusal, sensitive-word notice, policy URL, or internal diagnostic, choose drop with an empty text; never translate, quote, or polish it.\n"
                    "- When the current request context says the user explicitly requested this troubleshooting message, treat that request as a concrete reason to speak. Do not drop solely because it is late, the normal proactive interval is short, or there is no spontaneous life story. If the wording is too strong or generic, prefer a shorter, softer rewrite. Fact, safety, privacy, identity, and conversation-conflict checks still apply.\n"
                    "- For a creative share, preserve any `「...」` excerpt exactly as one continuous source quote. Keep conversational introduction and closing outside it; never paraphrase or fabricate text inside the excerpt."
                ),
            ), mode=PromptRenderMode.BODY_ONLY),
        ]
        if creative_excerpt_section is not None:
            sections.append(creative_excerpt_section)
        sections.extend(
            (
                square(
                    "background.proactive_send_review.history",
                    "Recent conversation",
                    history or "(none)",
                    separator_before="\n\n\n\n" if creative_excerpt_section is None else "",
                ),
                square("background.proactive_send_review.runtime", "Runtime state", runtime_context),
                square(
                    "background.proactive_send_review.request",
                    "Current request context",
                    troubleshooting_context
                    or "(ordinary proactive message; no explicit user-requested test)",
                ),
                square("background.proactive_send_review.fact_boundary", "Verified fact boundary", fact_source_context),
                square("background.proactive_send_review.local", "Local safety result", local_context or "local gate passed"),
                square("background.proactive_send_review.source", "Proactive source", source_context),
                square("background.proactive_send_review.route", "Route-specific final gate", route_review_directive),
                square("background.proactive_send_review.persona", "Full persona", persona_context),
                square("background.proactive_send_review.intent", "Persona and intent constraints", intent_hint or "(none)"),
                square(
                    "background.proactive_send_review.voice",
                    "Proactive voice",
                    proactive_voice or "(natural, low-pressure private chat)",
                ),
                square(
                    "background.proactive_send_review.expression",
                    "Learned expression voice",
                    expression_voice or "(none)",
                ),
                square(
                    "background.proactive_send_review.recipient",
                    "Recipient identity boundary",
                    recipient_identity
                    or "Use only the current recipient identity. Do not guess or copy an exclusive name from persona examples.",
                ),
                square("background.proactive_send_review.candidate", "Candidate", candidate),
                _proactive_prompt_part(
                    prompt_section(
                        key="background.proactive_send_review.output",
                        title="Output",
                        source="proactive_message",
                        content='{"decision":"send|rewrite|drop","text":"","reason":"brief reason"}',
                    ),
                    label_style=PromptLabelStyle.COLON,
                ),
            )
        )
        return prompt_document(
            user_render=_PROACTIVE_DOCUMENT_RENDER,
            user=sections,
            metadata={"task": "proactive_send_review"},
        )

    async def _review_proactive_message_send_decision(
        self,
        user: dict[str, Any],
        text: str,
        *,
        reason: str,
        action: str,
        motive: str = "",
        topic: str = "",
        action_summary: str = "",
        image_path: str = "",
    ) -> dict[str, Any]:
        strength = self._proactive_review_strength()
        route_getter = getattr(self, "_proactive_route_for", None)
        route = (
            route_getter(
                reason=reason,
                source=user.get("planned_proactive_source"),
                semantic_kind=user.get("planned_proactive_semantic_kind"),
                kind=user.get("planned_proactive_kind"),
            )
            if callable(route_getter)
            else PROACTIVE_ROUTE_REGISTRY.route_for(
                reason=reason,
                source=user.get("planned_proactive_source"),
                semantic_kind=user.get("planned_proactive_semantic_kind"),
                kind=user.get("planned_proactive_kind"),
            )
        )
        review_context = _single_line(action_summary, 240)
        if image_path:
            review_context = _single_line(f"{review_context}\n真实图片文件：{image_path}", 360)

        def normalize_review_result(payload: dict[str, Any], *, source: str) -> dict[str, Any]:
            return self._normalize_proactive_review_decision_policy(
                user,
                payload,
                strength=strength,
                source=source,
                reason=reason,
                action=action,
                topic=topic,
                motive=motive,
                action_context=review_context,
                image_path=image_path,
                original_text=text,
            )

        def local_model_fallback(fallback_reason: str) -> dict[str, Any]:
            result = normalize_review_result(local, source="local")
            result["review_fallback"] = True
            result["review_fallback_reason"] = _single_line(fallback_reason, 180)
            return result

        local = self._local_proactive_send_decision(
            user,
            text,
            reason=reason,
            action=action,
            motive=motive,
            topic=topic,
            action_context=review_context,
        )
        local_decision = str(local.get("decision") or "send").strip().lower()
        local_hard_block = bool(local.get("hard")) or self._proactive_review_hard_block_reason(_single_line(local.get("reason"), 120))
        if route.key == "transactional" and local_decision in {"drop", "defer"} and not local_hard_block:
            local = {
                "decision": "send",
                "text": "",
                "reason": "事务路线保留原始提醒事实，忽略通用低价值软拦截",
            }
            local_decision = "send"
        review_enabled = bool(
            runtime_persona_setting(self, "enable_proactive_message_review", True)
        )
        review_mode = self._effective_proactive_review_mode()
        if not review_enabled:
            local_mode_label = "主动发送前审核未启用"
            if local_decision in {"drop", "defer"}:
                if local_hard_block:
                    return normalize_review_result(local, source="local")
                return {
                    "decision": "send",
                    "text": "",
                    "reason": f"{local_mode_label}，已跳过非安全性的本地软拦截",
                }
            if local_decision == "rewrite":
                local_rewrite_text = str(local.get("text") or "").strip()
                if local_rewrite_text:
                    local_result = normalize_review_result(local, source="local")
                    local_result["reason"] = _single_line(
                        f"{local_mode_label}，已采用本地确定性改写："
                        + (_single_line(local.get("reason"), 80) or "轻量清理"),
                        120,
                    )
                    return local_result
                if not local_hard_block:
                    return {
                        "decision": "send",
                        "text": "",
                        "reason": f"{local_mode_label}，本地软建议未形成确定改写，保留原文",
                    }
                return {
                    "decision": "drop",
                    "text": "",
                    "reason": f"{local_mode_label}，本地检查仅能提供参考意图，无法形成确定正文，已取消本轮发送",
                    "hard": True,
                }
            return {
                "decision": "send",
                "text": "",
                "reason": f"{local_mode_label}，本地检查允许原文发送",
            }
        if review_mode == "local_only":
            local_mode_label = "仅本地检查模式"
            if local_decision in {"drop", "defer"}:
                return normalize_review_result(local, source="local")
            if local_decision == "rewrite":
                local_rewrite_text = str(local.get("text") or "").strip()
                if local_rewrite_text:
                    local_result = normalize_review_result(local, source="local")
                    local_result["reason"] = _single_line(
                        f"{local_mode_label}，已采用本地确定性改写："
                        + (_single_line(local.get("reason"), 80) or "轻量清理"),
                        120,
                    )
                    return local_result
                return {
                    "decision": "drop",
                    "text": "",
                    "reason": f"{local_mode_label}，本地检查仅能提供参考意图，无法形成确定正文，已取消本轮发送",
                    "hard": True,
                }
            return {
                "decision": "send",
                "text": "",
                "reason": f"{local_mode_label}，本地检查允许原文发送",
            }
        if local_decision in {"drop", "defer"} and local_hard_block:
            return normalize_review_result(local, source="local")
        if local.get("decision") == "rewrite" and str(local.get("reference_text") or "").strip():
            rewrite_scene = _single_line(
                "自然地向用户分享自己刚看的这条内容；保留真实标题、来源和链接"
                if reason in {"bili_video_share", "news_share", "web_exploration_share"}
                else f"主动消息改写；reason={reason or 'check_in'}；action={action or 'message'}",
                180,
            )
            rewritten_reference = await self._rewrite_reference_reply_with_persona(
                str(local.get("reference_text") or ""),
                scene=rewrite_scene,
                user=user,
                fallback_text="",
                task="proactive_reference_rewrite",
                max_chars=140,
                allow_fallback=False,
            )
            if rewritten_reference:
                accepted_reference = self._accept_proactive_rewrite(
                    rewritten_reference,
                    original_text=str(local.get("reference_text") or ""),
                    user=user,
                    reason=reason,
                    action=action,
                    topic=topic,
                    motive=motive,
                    action_context=review_context,
                    image_path=image_path,
                )
                if accepted_reference is None:
                    safe_reference = _single_line(local.get("reference_text"), 300)
                    accepted_reference = self._accept_proactive_rewrite(
                        safe_reference,
                        original_text=safe_reference,
                        user=user,
                        reason=reason,
                        action=action,
                        topic=topic,
                        motive=motive,
                        action_context=review_context,
                        image_path=image_path,
                    )
                    if accepted_reference:
                        logger.info(
                            "主动外界分享人格润色未通过统一验收，已使用确定性来源文本: reason=%s",
                            reason,
                        )
                rewritten_reference = accepted_reference or ""
            if rewritten_reference:
                local = dict(local)
                local["text"] = rewritten_reference
                local.pop("reference_text", None)
            else:
                return {
                    "decision": "drop",
                    "reason": _single_line(local.get("reason"), 80) or "兜底参考意图未能按人格改写",
                    "hard": True,
                }
        if local.get("decision") == "rewrite" and bool(local.get("hard")):
            return normalize_review_result(local, source="local")
        if local_decision == "drop":
            return normalize_review_result(local, source="local")
        if review_mode == "severe_only" and local_decision == "send" and not local_hard_block:
            return {
                "decision": "send",
                "text": "",
                "reason": "主动终审严重问题模式：本地检查通过",
            }
        persona = await self._resolve_proactive_persona_prompt(user)
        history = await self._recent_private_conversation_for_proactive_review(
            user,
            limit=self._proactive_history_limit("review"),
        )
        intent_hint = self._format_proactive_generation_intent_hint(
            user,
            reason=reason,
            action=action,
            motive=motive,
            action_context=review_context,
        )
        proactive_voice_sections_getter = getattr(self, "_format_proactive_voice_prompt_sections", None)
        proactive_voice = (
            render_prompt_sections(
                proactive_voice_sections_getter(),
                mode=PromptRenderMode.LABELED_BLOCK,
            )
            if callable(proactive_voice_sections_getter)
            else ""
        )
        if not callable(proactive_voice_sections_getter):
            proactive_voice_getter = getattr(self, "_format_proactive_voice_prompt", None)
            proactive_voice = proactive_voice_getter() if callable(proactive_voice_getter) else ""
        expression_section_getter = getattr(self, "_format_expression_voice_prompt_section", None)
        expression_section = (
            expression_section_getter(
                scope="proactive",
                target_id=_single_line(user.get("user_id") or user.get("id"), 80),
                context_owner=user,
                stage_owner=user,
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
                    target_id=_single_line(user.get("user_id") or user.get("id"), 80),
                    context_owner=user,
                    stage_owner=user,
                )
                if callable(expression_formatter)
                else ""
            )
        recipient_identity = self._proactive_recipient_identity_prompt_text(
            user,
            _single_line(user.get("nickname"), 40),
        )
        runtime_context = self._format_proactive_review_runtime_context(user)
        troubleshooting_hint = self._proactive_troubleshooting_request_hint(user)
        has_verified_fact_source = self._proactive_has_verified_recent_fact_source(
            reason=reason,
            action=action,
            action_context=review_context,
        )
        fact_source_context = (
            f"本轮存在可核验动作/来源：{review_context}"
            if has_verified_fact_source
            else "本轮没有可核验的近期动作或外部来源；不得声称自己刚刚看见、刷到、听到、收到或完成了某件事。"
        )
        local_context = "；".join(
            part
            for part in (
                f"本地结论={local_decision or 'send'}",
                f"说明={_single_line(local.get('reason'), 100)}" if local.get("reason") else "",
                "硬风险=yes" if local_hard_block else "硬风险=no",
                f"本地建议文本={_single_line(local.get('text'), 120)}" if local.get("text") else "",
            )
            if part
        )
        route_review_directive = route.review_directive()
        persona_context = (
            "(Creative-share compact review: use the proactive voice and excerpt rule below; do not restate the full persona.)"
            if reason == "creative_share"
            else self._truncate_proactive_context(persona, 2600)
        ) if persona else "(No explicit persona was resolved. Preserve the candidate instead of inventing a new voice.)"
        creative_excerpt_section = (
            self._creative_share_excerpt_prompt_section()
            if reason == "creative_share"
            else None
        )
        source_context = (
            f"route={route.key}({route.label}); review_profile={route.review_profile}; "
            f"reason={reason or 'check_in'}; action={action or 'message'}; "
            f"topic={_single_line(topic, 80) or 'none'}; "
            f"motive={_single_line(motive, 120) or 'none'}; "
            f"summary={_single_line(action_summary, 80) or 'none'}"
        )
        prompt = render_prompt_document(
            self._proactive_send_review_prompt_document(
                creative_excerpt_section=creative_excerpt_section,
                history=history,
                runtime_context=runtime_context,
                troubleshooting_context=troubleshooting_hint,
                fact_source_context=fact_source_context,
                local_context=local_context,
                source_context=source_context,
                route_review_directive=route_review_directive,
                persona_context=persona_context,
                intent_hint=intent_hint,
                proactive_voice=proactive_voice,
                expression_voice=expression_voice,
                recipient_identity=recipient_identity,
                candidate=text,
            )
        )["user"]
        started = time.perf_counter()
        review_provider_id = self._task_provider(
            _persona_provider_id(self, "RESPONSE_REVIEW_PROVIDER_ID", "response_review_provider_id", "fast"),
            _persona_provider_id(self, "MAI_STYLE_PROVIDER_ID", "mai_style_provider_id", "fast"),
        )
        timeout_seconds = 8.0
        timeout_getter = getattr(self, "_model_timeout_seconds_for_call", None)
        timeout_override = (
            timeout_getter(
                task="proactive_send_review",
                provider_id=review_provider_id,
                timeout_key="RESPONSE_REVIEW_PROVIDER_ID",
            )
            if callable(timeout_getter)
            else None
        )
        if timeout_override is not None:
            timeout_seconds = float(timeout_override)
        try:
            raw = await asyncio.wait_for(
                self._llm_call(
                    prompt,
                    max_tokens=220,
                    provider_id=review_provider_id,
                    task="proactive_send_review",
                ),
                timeout=timeout_seconds,
            )
        except Exception as exc:
            now = time.time()
            last_log_at = float(getattr(self, "_proactive_review_fallback_log_at", 0.0) or 0.0)
            if now - last_log_at >= 600:
                self._proactive_review_fallback_log_at = now
                logger.info(
                    "主动最终内容复核模型暂不可用，已安全回退本地复核（同类日志 10 分钟内不重复）: %s",
                    self._format_send_exception(exc),
                )
            return local_model_fallback(self._format_send_exception(exc))
        payload = self._parse_json_object(raw)
        if not isinstance(payload, dict):
            return local_model_fallback("复核模型未返回有效 JSON")
        decision = str(payload.get("decision") or "").strip().lower()
        if decision not in {"send", "rewrite", "drop"}:
            return local_model_fallback("复核模型返回了无效 decision")
        reviewed_text = str(payload.get("text") or "").strip()
        note = _single_line(payload.get("reason"), 120)
        original_decision = decision
        if decision == "rewrite":
            if not reviewed_text:
                return local_model_fallback("复核模型要求改写但未返回正文")
            accepted_text = self._accept_proactive_rewrite(
                reviewed_text,
                original_text=text,
                user=user,
                reason=reason,
                action=action,
                topic=topic,
                motive=motive,
                action_context=review_context,
                image_path=image_path,
            )
            if accepted_text is None and reason in {"bili_video_share", "news_share", "web_exploration_share"}:
                original_safe = self._accept_proactive_rewrite(
                    text,
                    original_text=text,
                    user=user,
                    reason=reason,
                    action=action,
                    topic=topic,
                    motive=motive,
                    action_context=review_context,
                    image_path=image_path,
                )
                safe_reference = original_safe or self._external_share_fallback_reference(
                    _single_line(topic or action_summary or text, 300),
                )
                accepted_text = self._accept_proactive_rewrite(
                    safe_reference,
                    original_text=safe_reference,
                    user=user,
                    reason=reason,
                    action=action,
                    topic=topic,
                    motive=motive,
                    action_context=review_context,
                    image_path=image_path,
                ) if safe_reference else None
                if accepted_text:
                    note = "终审改写未通过统一验收，已恢复复核前或确定性来源文本"
            if accepted_text is None:
                original_safe = self._accept_proactive_rewrite(
                    text,
                    original_text=text,
                    user=user,
                    reason=reason,
                    action=action,
                    topic=topic,
                    motive=motive,
                    action_context=review_context,
                    image_path=image_path,
                )
                if original_safe:
                    decision = "send"
                    reviewed_text = ""
                    note = "终审改写未通过统一验收，已保留完整原候选"
                else:
                    local_result = normalize_review_result(local, source="local")
                    local_result["review_model_ok"] = True
                    return local_result
            else:
                reviewed_text = accepted_text
        normalized_payload = self._normalize_proactive_review_decision_policy(
            user,
            {
                "decision": decision,
                "text": reviewed_text,
                "reason": note,
                "delay_minutes": payload.get("delay_minutes", 0),
            },
            strength=strength,
            source="model",
            reason=reason,
            action=action,
            topic=topic,
            motive=motive,
            action_context=review_context,
            image_path=image_path,
            original_text=text,
        )
        decision = str(normalized_payload.get("decision") or decision).strip().lower()
        reviewed_text = str(normalized_payload.get("text") or reviewed_text or "").strip()
        note = _single_line(normalized_payload.get("reason") or note, 120)
        if decision == "send" and str(local.get("decision") or "") == "rewrite" and str(local.get("text") or "").strip():
            local_text = self._accept_proactive_rewrite(
                str(local.get("text") or "").strip(),
                original_text=text,
                user=user,
                reason=reason,
                action=action,
                topic=topic,
                motive=motive,
                action_context=review_context,
                image_path=image_path,
            )
            if local_text:
                reviewed_text = local_text
                decision = "rewrite"
                note = _single_line(note or local.get("reason") or "本地轻改写后放行", 120)
            else:
                reviewed_text = ""
                decision = "send"
                note = _single_line(note or "本地改写未通过统一验收，保留原候选", 120)
        if str(local.get("decision") or "").strip().lower() == "defer" and not local_hard_block:
            delay_minutes = max(5, min(240, _safe_int(local.get("delay_minutes"), 60, 5, 240)))
            if decision in {"send", "rewrite"}:
                return {
                    "decision": "defer",
                    "text": "",
                    "delay_minutes": delay_minutes,
                    "reason": _single_line(
                        f"本地时机判断保留延后 {delay_minutes} 分钟：{local.get('reason') or '当前不宜立即发送'}",
                        180,
                    ),
                    "review_model_ok": True,
                }
        final_text = reviewed_text if decision == "rewrite" and reviewed_text else text
        link_platform_mismatch = self._proactive_link_platform_mismatch_reason(final_text)
        if decision in {"send", "rewrite"} and link_platform_mismatch:
            decision = "drop"
            reviewed_text = ""
            note = link_platform_mismatch
        if decision in {"send", "rewrite"} and self._framework_agent_meta_summary_leak(final_text):
            decision = "drop"
            reviewed_text = ""
            note = "主动候选疑似工具循环/内部发送摘要泄漏"
        logger.info(
            "Proactive final content gate: decision=%s raw=%s strength=%s elapsed=%dms reason=%s",
            decision,
            original_decision,
            strength,
            int((time.perf_counter() - started) * 1000),
            note or "-",
        )
        return {
            "decision": decision,
            "text": reviewed_text,
            "reason": note or "主动发送前价值复核",
            "delay_minutes": max(0, _safe_int(normalized_payload.get("delay_minutes"), 0, 0, 240)),
            "review_model_ok": True,
        }

    def _pop_framework_captured_send_payload(
        self,
        umo: str,
    ) -> tuple[str, str, list[Any]]:
        cache_key = str(umo or "")
        self._cleanup_framework_delivery_caches()
        captured = self._framework_captured_send_cache.pop(cache_key, [])
        getattr(self, "_framework_captured_send_cache_at", {}).pop(cache_key, None)
        if not captured:
            return "", "", []
        text_parts: list[str] = []
        image_path = ""
        extra_components: list[Any] = []
        for call in captured:
            messages = getattr(call, "messages", [])
            if not isinstance(messages, list):
                continue
            for item in messages:
                if not isinstance(item, dict):
                    continue
                item_type = str(item.get("type") or "").strip().lower()
                if item_type == "plain":
                    text_value = self._sanitize_captured_plain_text(item.get("text"))
                    if text_value:
                        text_parts.append(text_value)
                    continue
                if item_type == "image":
                    path_value = str(item.get("path") or "").strip()
                    if path_value and os.path.exists(path_value) and not image_path:
                        image_path = path_value
                        continue
                component = self._captured_framework_message_component(item)
                if component is not None:
                    extra_components.append(component)
        return "\n".join(part for part in text_parts if part).strip(), image_path, extra_components

    def _pop_framework_deferred_photo_payload(self, umo: str) -> dict[str, Any]:
        self._cleanup_framework_delivery_caches()
        cache_key = str(umo or "")
        cache = getattr(self, "_framework_deferred_photo_cache", None)
        if not isinstance(cache, dict):
            return {}
        payload = cache.pop(cache_key, None)
        getattr(self, "_framework_deferred_photo_cache_at", {}).pop(cache_key, None)
        return dict(payload) if isinstance(payload, dict) else {}

    def _cleanup_framework_delivery_caches(self, *, force: bool = False) -> None:
        """Drop abandoned framework delivery payloads and their component references."""
        now = time.time()
        try:
            ttl = max(30.0, float(getattr(self, "framework_delivery_cache_ttl_seconds", 300) or 300))
        except (TypeError, ValueError):
            ttl = 300.0

        def stamp(key: str, stamps: dict[str, Any]) -> float:
            try:
                return float(stamps.get(key, now) or now)
            except (TypeError, ValueError):
                return now

        for cache_name, stamp_name in (
            ("_framework_captured_send_cache", "_framework_captured_send_cache_at"),
            ("_framework_deferred_photo_cache", "_framework_deferred_photo_cache_at"),
        ):
            cache = getattr(self, cache_name, None)
            if not isinstance(cache, dict):
                continue
            stamps = getattr(self, stamp_name, None)
            if not isinstance(stamps, dict):
                stamps = {}
                setattr(self, stamp_name, stamps)
            if force:
                cache.clear()
                stamps.clear()
                continue
            for key in list(stamps):
                if key not in cache:
                    stamps.pop(key, None)
            stale = [key for key in cache if now - stamp(key, stamps) > ttl]
            for key in stale:
                cache.pop(key, None)
                stamps.pop(key, None)
            # A small defensive cap protects against callers that bypass the normal writer.
            max_items = 128
            if len(cache) > max_items:
                ordered = sorted(cache, key=lambda key: stamp(key, stamps))
                for key in ordered[: len(cache) - max_items]:
                    cache.pop(key, None)
                    stamps.pop(key, None)

    def _captured_framework_message_component(self, item: dict[str, Any]) -> Any | None:
        item_type = str(item.get("type") or "").strip().lower()
        path_value = str(item.get("path") or "").strip()
        url_value = str(item.get("url") or "").strip()
        if item_type == "mention_user":
            mention_user_id = item.get("mention_user_id")
            return At(qq=mention_user_id) if mention_user_id else None
        if item_type == "image":
            if url_value:
                try:
                    return Image.fromURL(url_value)
                except Exception:
                    return None
            return None
        if CoreMessageComponents is None:
            return None
        if item_type in {"record", "video"}:
            component_cls = getattr(CoreMessageComponents, item_type.capitalize(), None)
            if component_cls is None:
                return None
            try:
                if path_value and os.path.exists(path_value):
                    return component_cls.fromFileSystem(path_value)
                if url_value:
                    return component_cls.fromURL(url_value)
            except Exception:
                return None
            return None
        if item_type == "file":
            component_cls = getattr(CoreMessageComponents, "File", None)
            if component_cls is None:
                return None
            name = _single_line(item.get("text"), 120) or os.path.basename(path_value or url_value) or "file"
            if path_value and os.path.exists(path_value):
                return component_cls(name=name, file=path_value)
            if url_value:
                return component_cls(name=name, url=url_value)
        return None

    def _sanitize_captured_plain_text(self, raw_text: Any) -> str:
        text = str(raw_text or "").strip()
        if not text:
            return ""
        kept: list[str] = []
        for raw_line in text.replace("\r", "\n").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if self._is_proactive_delivery_receipt_text(line):
                continue
            if self._looks_like_internal_provider_error_text(line):
                continue
            kept.append(line)
        cleaned = "\n".join(kept).strip().strip('"').strip("'")
        cleaned = cleaned.replace("（图片已送达）", "").replace("(图片已送达)", "")
        tts_cleaner = getattr(self, "_clean_tool_plain_text_tts_markup", None)
        if callable(tts_cleaner):
            cleaned = tts_cleaner(cleaned)
        else:
            cleaned = re.sub(r"</?(?:pc[_-]?tts|t{2,}s)\b[^>]*>", "", cleaned, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
        if self._looks_like_internal_provider_error_text(cleaned):
            return ""
        return cleaned[:260]

