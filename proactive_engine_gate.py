# -*- coding: utf-8 -*-
"""发送闸门/决策域。

由 tools/split_mixin_domain.py 从 proactive_engine.py 机械抽取（4 个方法 + 0 个模块级名字 + 0 个类级赋值 / 1318 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveEngineMixin）。
"""
from __future__ import annotations

import os
import random
from .helpers import _now_ts, _path_text, _safe_float, _safe_int, _single_line
from .persona_config import runtime_persona_setting
from .proactive_engine_shared import _engine_proactive_window_timezone
from .proactive_routes import PROACTIVE_ROUTE_REGISTRY
from typing import Any

from .logging_util import get_module_logger

logger = get_module_logger(__name__)



class ProactiveEngineGateMixin:
    """发送闸门/决策域（从 ProactiveEngineMixin 拆出）。"""


    def _should_send(self, user: dict[str, Any]) -> tuple[bool, str]:
        self._recover_stale_proactive_sending(user)
        user_id = str(user.get("user_id") or user.get("id") or "")
        planned_source = self._normalize_legacy_proactive_text(user.get("planned_proactive_source"), limit=40)
        planned_reason = self._normalize_legacy_proactive_text(user.get("planned_proactive_reason"), limit=40)
        is_troubleshooting = planned_source == "troubleshooting"
        if not self._user_enabled_for_proactive(user_id, user):
            self._clear_pending_proactive_plan(user)
            return False, "私聊对象未启用"
        if self._proactive_generation_disabled(user):
            self._suspend_user_proactive_generation(user)
            reason_formatter = getattr(self, "_format_daily_limit_disabled_reason", None)
            if callable(reason_formatter):
                return False, reason_formatter(user)
            return False, "每日上限为 0，主动生成已停止"
        if user.get("proactive_sending"):
            return False, "上一条主动消息仍在发送中"
        umo_filled = False
        filler = getattr(self, "_ensure_private_user_umo", None)
        if callable(filler):
            try:
                umo_filled = bool(filler(user_id, user))
            except Exception:
                umo_filled = False
        if not user.get("umo"):
            return False, "缺少私聊会话"
        if umo_filled:
            logger.info(
                "已为主动私聊对象补全 UMO: user=%s umo=%s",
                _single_line(user_id, 40),
                _single_line(user.get("umo"), 120),
            )
        daily_limit = self._effective_user_daily_limit(user)
        if daily_limit <= 0:
            reason_formatter = getattr(self, "_format_daily_limit_disabled_reason", None)
            if callable(reason_formatter):
                return False, reason_formatter(user)
            return False, "每日上限为 0"
        if self._simulation_active(user):
            return self._should_send_simulation(user)
        now = _now_ts()
        due_timer_active = self._has_due_llm_timer(user, now=now)
        planned_timezone = _single_line(
            user.get("planned_proactive_window_timezone"),
            64,
        )
        current_timezone = _engine_proactive_window_timezone(self)
        if (
            planned_timezone
            and planned_timezone != current_timezone
            and planned_source not in {"timer", "troubleshooting", "simulation"}
            and not due_timer_active
        ):
            self._mark_planned_candidate_status(
                user,
                "blocked",
                "运行时区已变化，旧主动窗口已作废",
            )
            self._clear_pending_proactive_plan(user)
            self._schedule_next_proactive(user, now=now)
            return False, "运行时区已变化，已重新安排主动窗口"
        timeliness = self._planned_proactive_timeliness_level(user)
        if not is_troubleshooting:
            route_preflight_getter = getattr(self, "_planned_proactive_route_preflight", None)
            if callable(route_preflight_getter):
                route_preflight = route_preflight_getter(user, now=now)
            else:
                route = PROACTIVE_ROUTE_REGISTRY.route_for(
                    reason=planned_reason,
                    source=planned_source,
                    semantic_kind=user.get("planned_proactive_semantic_kind"),
                    kind=user.get("planned_proactive_kind"),
                )
                route_preflight = route.preflight(
                    user,
                    {
                        "reason": planned_reason,
                        "source": planned_source,
                        "trigger_message_id": user.get("planned_proactive_trigger_message_id"),
                        "trigger_inbound_count": user.get("planned_proactive_trigger_inbound_count"),
                        "private_inbound_count": user.get("private_inbound_count"),
                        "expire_at": user.get("planned_proactive_expire_at"),
                    },
                    now=now,
                )
            user["planned_proactive_route_preflight_action"] = _single_line(route_preflight.action, 32)
            user["planned_proactive_route_preflight_note"] = _single_line(route_preflight.reason, 180)
            if not route_preflight.allowed:
                note = _single_line(route_preflight.reason, 160) or "主动路线准入未通过"
                if route_preflight.action == "defer":
                    delay = route_preflight.defer_minutes
                    self._defer_or_replace_planned_impulse(
                        user,
                        now=now,
                        note=note,
                        delay_minutes=delay if delay != (0.0, 0.0) else (30.0, 90.0),
                        block_current=False,
                    )
                else:
                    self._mark_planned_candidate_status(user, "blocked", note)
                    self._clear_pending_proactive_plan(user)
                return False, note
        if (
            not is_troubleshooting
            and not due_timer_active
            and (planned_source == "creative_writing" or planned_reason == "creative_share")
            and not bool(runtime_persona_setting(self, "enable_creative_writing", False))
        ):
            self._mark_planned_candidate_status(user, "blocked", "创作功能未开启，已清理旧的创作分享候选")
            user["creative_share_context"] = {}
            self._clear_pending_proactive_plan(user)
            schedule_save = getattr(self, "_schedule_data_save", None)
            if callable(schedule_save):
                schedule_save(sections={"users"})
            return False, "创作功能未开启"
        if not is_troubleshooting and planned_source == "timer" and not due_timer_active:
            self._clear_llm_timer_internal_plan_fields(user)
            if _safe_float(user.get("next_proactive_at"), 0) <= 0:
                self._schedule_next_proactive(user, now=now)
            return False, "对话临时预约已交给官方定时计划"
        planned_impulse_id = _single_line(user.get("planned_proactive_impulse_id"), 20)
        planned_expire_at = _safe_float(user.get("planned_proactive_expire_at"), 0)
        if (
            not is_troubleshooting
            and planned_expire_at > 0
            and now > planned_expire_at
            and not due_timer_active
            and planned_source != "timer"
        ):
            expired_note = "潜在念头窗口已过期" if planned_impulse_id else "主动计划窗口已过期"
            self._mark_planned_candidate_status(user, "blocked", expired_note)
            self._clear_pending_proactive_plan(user)
            if not self._materialize_best_proactive_impulse(user, now=now):
                self._schedule_next_proactive(user, now=now, delay_hours=(1.0, 3.0))
            return False, "原主动计划已过期,已重新挑选"
        silence_reason_getter = getattr(self, "_friend_unanswered_silence_reason", None)
        silence_reason = silence_reason_getter(user, now=now) if callable(silence_reason_getter) else ""
        if (
            silence_reason
            and not is_troubleshooting
            and not due_timer_active
            and planned_source not in {"timer", "simulation"}
        ):
            blocker = getattr(self, "_block_friend_unanswered_pending_proactive", None)
            if callable(blocker):
                blocker(user, note=silence_reason, now=now)
            self._mark_planned_candidate_status(user, "blocked", silence_reason)
            self._clear_pending_proactive_plan(user)
            return False, silence_reason
        if (
            not is_troubleshooting
            and
            self._proactive_rest_block_until(
                user,
                now=now,
                reason=user.get("planned_proactive_reason"),
                source=planned_source,
            ) > now
            and not due_timer_active
        ):
            return False, "用户明确休息中"
        busy_until = 0.0
        busy_block_kind = ""
        busy_block_note = ""
        busy_context_getter = getattr(self, "_busy_reply_proactive_block_context", None)
        busy_gate = getattr(self, "_busy_reply_proactive_block_until", None)
        if not is_troubleshooting and not due_timer_active and callable(busy_context_getter):
            try:
                busy_context = busy_context_getter(
                    user,
                    now=now,
                    reason=user.get("planned_proactive_reason"),
                    source=planned_source,
                )
                if isinstance(busy_context, dict):
                    busy_until = _safe_float(busy_context.get("until"), 0.0)
                    busy_block_kind = _single_line(busy_context.get("kind"), 40)
                    busy_block_note = _single_line(busy_context.get("note"), 160)
            except Exception:
                busy_until = 0.0
        elif not is_troubleshooting and not due_timer_active and callable(busy_gate):
            try:
                busy_until = _safe_float(
                    busy_gate(
                        user,
                        now=now,
                        reason=user.get("planned_proactive_reason"),
                        source=planned_source,
                    ),
                    0.0,
                )
            except Exception:
                busy_until = 0.0
        if busy_until > now and (timeliness == "routine" or busy_block_kind == "external_realtime"):
            defer_busy = getattr(self, "_defer_proactive_for_busy", None)
            changed = bool(defer_busy(user, now=now, until=busy_until)) if callable(defer_busy) else False
            if changed:
                external_realtime = busy_block_kind == "external_realtime"
                defer_note = (
                    "Bot 正在与用户实时共处，已顺延到共同活动结束后"
                    if external_realtime
                    else "Bot 当前日程忙碌，已顺延到忙完后"
                )
                self._mark_planned_candidate_status(user, "deferred", defer_note)
                schedule_save = getattr(self, "_schedule_data_save", None)
                if callable(schedule_save):
                    schedule_save(sections={"users"})
                logger.info(
                    "%s已顺延主动消息: user=%s until=%s reason=%s source=%s detail=%s",
                    "实时共处期间" if external_realtime else "繁忙回复闸门",
                    _single_line(user.get("user_id") or user.get("umo") or user.get("nickname"), 80),
                    int(busy_until),
                    _single_line(user.get("planned_proactive_reason"), 48) or "check_in",
                    planned_source or "unknown",
                    busy_block_note or "-",
                )
            if busy_block_kind == "external_realtime":
                return False, "正在实时共处，普通主动消息已顺延"
            return False, "Bot 当前日程忙碌，主动消息已顺延"
        # Refresh time-sensitive rituals before the quiet-hours and quota gates
        # so their narrow midnight window is not hidden behind an older plan.
        if not is_troubleshooting and not due_timer_active and self._promote_earlier_daily_greeting_event(user, now=now):
            planned_reason = self._normalize_legacy_proactive_text(user.get("planned_proactive_reason"), limit=40)
            planned_source = self._normalize_legacy_proactive_text(user.get("planned_proactive_source"), limit=40) or planned_source
            next_at = _safe_float(user.get("next_proactive_at"), 0)
            impulse_value = self._planned_impulse_value(user, now=now)
            window_phase, window_detail = self._planned_impulse_window_phase(user, now=now)
        post_goodnight_active = self._post_goodnight_group_activity_is_fresh(user, now=now)
        planned_reason = self._normalize_legacy_proactive_text(user.get("planned_proactive_reason"), limit=40)
        ritual_context = (
            user.get("planned_birthday_event_context")
            if planned_reason == "birthday_celebration"
            else user.get("planned_special_day_context")
        )
        if not isinstance(ritual_context, dict):
            ritual_context = {}
        midnight_ritual_active = planned_reason in {"birthday_celebration", "special_day_greeting"} and (
            _single_line(ritual_context.get("delivery_timing"), 24) == "midnight"
        )
        insomnia_slot_available = planned_reason == "insomnia_night" and self._can_send_insomnia_night_message(user, now=now)
        if (
            not is_troubleshooting
            and self._is_quiet_time()
            and not insomnia_slot_available
            and not midnight_ritual_active
            and not post_goodnight_active
        ):
            return False, "免打扰时段"
        pre_gate_next_at = _safe_float(user.get("next_proactive_at"), 0)
        if not is_troubleshooting and not due_timer_active:
            if pre_gate_next_at <= 0:
                self._schedule_next_proactive(user, now=now)
                return False, "已安排下一次候选主动时间"
            if now < pre_gate_next_at:
                return False, "未到候选主动时间"
        relationship_mode = self._current_relationship_gate_mode(user, now=now) if not is_troubleshooting else ""
        emotion_mode = self._current_emotion_gate_mode(user, now=now) if not is_troubleshooting else ""
        relationship_blocked = relationship_mode == "backoff"
        emotion_blocked = emotion_mode == "hurt"
        if relationship_blocked or emotion_blocked:
            interaction = user.get("current_interaction") if isinstance(user.get("current_interaction"), dict) else {}
            gate_until = _safe_float(interaction.get("expires_at"), 0)
            if relationship_blocked:
                gate_until = max(gate_until, now + 6 * 3600)
            before_next_at = _safe_float(user.get("next_proactive_at"), 0)
            adjuster = getattr(self, "_defer_or_clean_emotion_blocked_plan", None)
            if callable(adjuster):
                adjusted_reason = adjuster(user, now=now)
            else:
                adjusted_reason = "情绪/关系状态处于收敛期"
            after_next_at = _safe_float(user.get("next_proactive_at"), 0)
            if after_next_at <= now and gate_until > now:
                after_next_at = gate_until + random.uniform(15 * 60, 75 * 60)
                user["next_proactive_at"] = after_next_at
                user["planned_proactive_window_start_at"] = after_next_at
                user["planned_proactive_best_until_at"] = after_next_at + 45 * 60
                user["planned_proactive_expire_at"] = after_next_at + 90 * 60
            logger.info(
                "统一互动/联系边界闸门拦截主动: mode=%s gate_until=%s reason=%s",
                relationship_mode or emotion_mode,
                int(gate_until),
                _single_line(interaction.get("reason"), 80),
            )
            return False, adjusted_reason

        planned_reason = self._normalize_legacy_proactive_text(user.get("planned_proactive_reason"), limit=40)
        if due_timer_active and planned_source != "timer":
            self._promote_due_llm_timer_plan(user, now=now)
            planned_reason = self._normalize_legacy_proactive_text(user.get("planned_proactive_reason"), limit=40)
            planned_source = self._normalize_legacy_proactive_text(user.get("planned_proactive_source"), limit=40) or planned_source
        next_at = _safe_float(user.get("next_proactive_at"), 0)
        if next_at <= 0:
            self._schedule_next_proactive(user, now=now)
            return False, "已安排下一次候选主动时间"
        impulse_value = self._planned_impulse_value(user, now=now)
        window_phase, window_detail = self._planned_impulse_window_phase(user, now=now)
        if (
            not is_troubleshooting
            and planned_impulse_id
            and window_phase == "tail"
            and impulse_value < 0.28
            and not due_timer_active
            and timeliness == "routine"
        ):
            replaced = self._defer_or_replace_planned_impulse(
                user,
                now=now,
                note="低价值念头已过最佳表达窗口",
                delay_minutes=(45, 120),
                block_current=True,
            )
            if not replaced and _safe_float(user.get("next_proactive_at"), 0) <= 0:
                self._schedule_next_proactive(user, now=now, delay_hours=(1.0, 3.0))
            return False, "低价值念头已过最佳窗口,已重新挑选"
        if not is_troubleshooting and self._promote_earlier_daily_greeting_event(user, now=now):
            planned_reason = self._normalize_legacy_proactive_text(user.get("planned_proactive_reason"), limit=40)
            planned_source = self._normalize_legacy_proactive_text(user.get("planned_proactive_source"), limit=40) or planned_source
            next_at = _safe_float(user.get("next_proactive_at"), 0)
            impulse_value = self._planned_impulse_value(user, now=now)
            window_phase, window_detail = self._planned_impulse_window_phase(user, now=now)
        if (
            not is_troubleshooting
            and
            not due_timer_active
            and planned_source != "timer"
            and self._in_llm_timer_silence_window(user, now=now)
        ):
            self._remember_silenced_plan_for_timer(user, now=now)
            self._promote_upcoming_llm_timer_plan(user, now=now)
            return False, "用户预约静默窗口"
        if now < next_at:
            return False, "未到候选主动时间"
        delivery = self._ensure_planned_proactive_delivery_state(user, now=now)
        if (
            not is_troubleshooting
            and not due_timer_active
            and _single_line(delivery.get("freshness"), 24) == "immediate"
            and _safe_float(delivery.get("best_until_at"), 0) > 0
            and now > _safe_float(delivery.get("best_until_at"), 0)
        ):
            replaced = self._defer_or_replace_planned_impulse(
                user,
                now=now,
                note="即时主动已越过自然表达窗口",
                block_current=True,
            )
            if not replaced and _safe_float(user.get("next_proactive_at"), 0) <= 0:
                self._schedule_next_proactive(user, now=now, delay_hours=(1.0, 3.0))
            return False, "即时主动已越过自然表达窗口,已重新挑选"
        if not is_troubleshooting and self._is_proactive_plan_stale(user, now=now) and not due_timer_active:
            self._clear_pending_proactive_plan(user)
            self._schedule_next_proactive(user, now=now, delay_hours=(1, 4))
            return False, "候选主动计划已过期,已重新安排"
        inner_readiness = self._proactive_inner_readiness(user, now=now)
        inner_score = _safe_float(inner_readiness.get("score"), 0.55)
        if (
            not is_troubleshooting
            and not due_timer_active
            and planned_source != "timer"
            and inner_score < 0.36
            and impulse_value < 0.72
            and timeliness == "routine"
        ):
            logger.debug(
                "Bot 表达温度偏低，交由正文提示收敛为短句而不延后: user=%s detail=%s",
                _single_line(user.get("user_id") or user.get("umo"), 80),
                _single_line(inner_readiness.get("detail"), 120),
            )
        social_relay_note = self._unverified_social_relay_plan_reason(
            user,
            source=planned_source,
            has_trigger=bool(_single_line(user.get("planned_proactive_trigger_message_id"), 120)),
        )
        if not is_troubleshooting and social_relay_note:
            self._mark_planned_candidate_status(user, "blocked", social_relay_note)
            self._clear_pending_proactive_plan(user)
            self._schedule_next_proactive(user, now=now, delay_hours=(1.5, 4.5))
            return False, social_relay_note
        if (
            not is_troubleshooting
            and not due_timer_active
            and planned_source != "timer"
            and self._is_greeting_reason(planned_reason)
            and self._recent_activity_satisfies_greeting(user, planned_reason, now=now)
        ):
            self._mark_greeting_satisfied_by_inbound(user, planned_reason)
            self._mark_planned_candidate_status(user, "blocked", "用户在该问候窗口附近已经自然聊过")
            self._clear_pending_proactive_plan(user)
            self._schedule_next_proactive(user, now=now, delay_hours=(2, 5))
            return False, "用户在该问候窗口附近已经自然聊过"
        suppressed_raw = user.get("greetings_suppressed_by_inbound", [])
        suppressed_greetings: set[str] = set()
        if isinstance(suppressed_raw, list):
            suppressed_greetings = {str(item).strip() for item in suppressed_raw if str(item).strip()}
        if (
            not is_troubleshooting
            and planned_reason in suppressed_greetings
            and self._is_greeting_reason(planned_reason)
            and planned_source != "timer"
            and not due_timer_active
        ):
            self._mark_planned_candidate_status(user, "blocked", "用户在该问候窗口内已经活跃过")
            self._clear_pending_proactive_plan(user)
            self._schedule_next_proactive(user, now=now, delay_hours=(2, 5))
            return False, "用户在该问候窗口内已经活跃过"
        self._reset_daily_counter_if_needed(user)
        if (
            not is_troubleshooting
            and planned_reason == "morning_greeting"
            and planned_source != "timer"
            and not due_timer_active
            and self._greeting_was_sent_today(user, planned_reason)
        ):
            self._mark_planned_candidate_status(user, "blocked", "今天已经自然说过早安")
            self._clear_pending_proactive_plan(user)
            self._schedule_next_proactive(user, now=now, delay_hours=(2, 5))
            return False, "今天已经自然说过早安"
        if (
            not is_troubleshooting
            and not self._proactive_daily_limit_is_unlimited(daily_limit)
            and _safe_int(user.get("sent_today"), 0) >= daily_limit
            and not insomnia_slot_available
        ):
            if not due_timer_active:
                self._schedule_next_proactive(user, now=now, delay_hours=(8, 16))
            return False, "已达每日上限"
        idle_minutes = self._effective_user_idle_minutes(user)
        recent_activity_at = self._latest_private_user_activity_ts(user)
        if (
            not is_troubleshooting
            and not due_timer_active
            and not self._post_goodnight_group_activity_is_fresh(user, now=now)
            and now - recent_activity_at < idle_minutes * 60
        ):
            idle_limit = (
                self._effective_user_greeting_idle_minutes(user) * 60
                if self._is_greeting_reason(planned_reason)
                else idle_minutes * 60
            )
            timely_idle_floor = 0.0
            if timeliness == "urgent":
                timely_idle_floor = 2 * 60.0
            elif timeliness == "timely":
                timely_idle_floor = 5 * 60.0
            if now - recent_activity_at < (min(idle_limit, timely_idle_floor) if timely_idle_floor > 0 else idle_limit):
                if self._is_sticky_greeting_reason(planned_reason):
                    self._reschedule_greeting_within_window(user, planned_reason, now=now)
                else:
                    replaced = self._defer_or_replace_planned_impulse(
                        user,
                        now=now,
                        note="用户刚活跃过,当前念头先收住",
                        delay_minutes=(max(8.0, idle_limit / 60 * 0.5), max(15.0, idle_limit / 60 + 8.0)),
                        block_current=impulse_value < 0.52,
                    )
                    if replaced:
                        return False, "用户刚活跃过,已换用更贴近当前节奏的念头"
                return False, "用户刚活跃过"
        min_interval = self._effective_min_interval_seconds(user)
        if self._is_greeting_reason(planned_reason) and self._private_user_role(user) != "friend":
            min_interval = min(min_interval, self._greeting_min_interval_seconds(planned_reason))
        if timeliness == "urgent":
            min_interval = min(min_interval, 2 * 60.0)
        elif timeliness == "timely":
            min_interval = min(min_interval, 10 * 60.0)
        if (
            not is_troubleshooting
            and not due_timer_active
            and not bool(user.get("planned_proactive_burst"))
            and now - _safe_float(user.get("last_sent"), 0) < min_interval
        ):
            if self._is_sticky_greeting_reason(planned_reason):
                self._reschedule_greeting_within_window(user, planned_reason, now=now)
            else:
                remaining_minutes = max(5.0, (min_interval - (now - _safe_float(user.get("last_sent"), 0))) / 60)
                self._defer_or_replace_planned_impulse(
                    user,
                    now=now,
                    note="距离上次主动太近,当前念头先压低",
                    delay_minutes=(remaining_minutes, remaining_minutes + 30.0),
                    block_current=False,
                )
            return False, "发送间隔不足"
        planned_action = str(user.get("planned_proactive_action") or "message")
        normalizer = getattr(self, "_normalize_existing_plan_for_emotion", None)
        if not is_troubleshooting and callable(normalizer):
            emotion_note = normalizer(user, now=now)
            if emotion_note:
                planned_reason = self._normalize_legacy_proactive_text(user.get("planned_proactive_reason"), limit=40) or planned_reason
                planned_action = self._normalize_legacy_proactive_text(user.get("planned_proactive_action"), limit=40) or planned_action or "message"
                if _safe_float(user.get("next_proactive_at"), 0) > now + 1:
                    return False, emotion_note
        if not is_troubleshooting and self._private_user_role(user) == "friend":
            before_friend_sanitize = (
                planned_reason,
                planned_action,
                _single_line(user.get("planned_proactive_topic"), 80),
                _single_line(user.get("planned_proactive_motive"), 180),
            )
            sanitized = self._sanitize_friend_proactive_plan_fields(
                user,
                reason=planned_reason,
                action=planned_action,
                topic=_single_line(user.get("planned_proactive_topic"), 80),
                motive=_single_line(user.get("planned_proactive_motive"), 180),
            )
            user["planned_proactive_reason"] = sanitized["reason"]
            user["planned_proactive_action"] = sanitized["action"]
            user["planned_proactive_topic"] = sanitized["topic"]
            user["planned_proactive_motive"] = sanitized["motive"]
            planned_reason = sanitized["reason"]
            planned_action = sanitized["action"]
            after_friend_sanitize = (
                planned_reason,
                planned_action,
                sanitized["topic"],
                sanitized["motive"],
            )
            if after_friend_sanitize != before_friend_sanitize:
                user["planned_proactive_impulse_id"] = ""
                user["planned_proactive_semantic_kind"] = ""
                user["planned_proactive_anchor_type"] = ""
                user["planned_proactive_semantic_score"] = 0
                user["planned_proactive_semantic_note"] = ""
                user["planned_proactive_need_layer"] = ""
                user["planned_proactive_need_drive"] = ""
                user["planned_proactive_need_note"] = ""
                user["planned_proactive_model_judge_signature"] = ""
                user["planned_proactive_model_judge_result"] = {}
                user["planned_proactive_model_judge_at"] = 0
                self._mark_planned_candidate_status(user, "accepted", "次要用户未回应状态下已降级为低压主动")
        if not is_troubleshooting and not self._friend_can_receive_proactive_reason(user, planned_reason, planned_action):
            self._clear_pending_proactive_plan(user)
            self._schedule_next_proactive(user, now=now, delay_hours=(2, 6))
            return False, "次要用户关系不接收敏感主动"
        planned_semantics = self._planned_proactive_semantics(user)
        semantic_score = _safe_float(planned_semantics.get("score"), 0.5)
        semantic_pressure = _safe_float(planned_semantics.get("pressure"), 0.4)
        semantic_risk = _safe_float(planned_semantics.get("risk"), 0.0)
        semantic_blocked = bool(planned_semantics.get("blocker"))
        if (
            not is_troubleshooting
            and not due_timer_active
            and planned_source != "timer"
            and (semantic_blocked or semantic_risk >= 0.70)
        ):
            replaced = self._defer_or_replace_planned_impulse(
                user,
                now=now,
                note="候选语义不够自然: " + _single_line(planned_semantics.get("note"), 120),
                delay_minutes=(90, 240),
                block_current=semantic_blocked or semantic_risk >= 0.70,
            )
            if not replaced and _safe_float(user.get("next_proactive_at"), 0) <= 0:
                self._schedule_next_proactive(user, now=now, delay_hours=(2, 6))
            return False, "候选语义不够自然,已重新挑选"
        if semantic_score < 0.32 and semantic_pressure >= 0.58:
            logger.debug(
                "候选由头偏弱且压力偏高，交由正文提示改成低压短句: user=%s note=%s",
                _single_line(user.get("user_id") or user.get("umo"), 80),
                _single_line(planned_semantics.get("note"), 120),
            )
        persona_alignment = self._planned_proactive_persona_alignment(user, now=now)
        persona_fit = _safe_float(persona_alignment.get("score"), 0.55)
        persona_blocked = bool(persona_alignment.get("blocker"))
        persona_threshold = 0.48 if self._private_user_role(user) == "friend" else 0.42
        if (
            not is_troubleshooting
            and not due_timer_active
            and planned_source != "timer"
            and persona_blocked
        ):
            replaced = self._defer_or_replace_planned_impulse(
                user,
                now=now,
                note="人格/世界观贴合度不足: " + _single_line(persona_alignment.get("note"), 120),
                delay_minutes=(90, 240),
                block_current=True,
            )
            if not replaced and _safe_float(user.get("next_proactive_at"), 0) <= 0:
                self._schedule_next_proactive(user, now=now, delay_hours=(2, 6))
            return False, "人格/世界观贴合度不足,已重新挑选"
        if persona_fit < persona_threshold:
            logger.debug(
                "人格贴合度偏低，交由人格判定/正文生成修正而不延后: user=%s fit=%.2f note=%s",
                _single_line(user.get("user_id") or user.get("umo"), 80),
                persona_fit,
                _single_line(persona_alignment.get("note"), 120),
            )
        if due_timer_active:
            return True, "ok(timer)"
        ignored_streak = _safe_int(user.get("ignored_streak"), 0, 0)
        if (
            not is_troubleshooting
            and ignored_streak >= 2
            and impulse_value < (0.72 if self._private_user_role(user) == "friend" else 0.66)
            and timeliness == "routine"
        ):
            logger.debug(
                "连续未回应时保留低压候选，由提示词缩短且禁止追问: user=%s ignored=%s value=%.2f",
                _single_line(user.get("user_id") or user.get("umo"), 80),
                ignored_streak,
                impulse_value,
            )
        if not is_troubleshooting and not self._is_reason_allowed_now(planned_reason, user):
            if self._is_sticky_greeting_reason(planned_reason):
                self._reschedule_greeting_within_window(user, planned_reason, now=now)
                return False, "问候仍在窗口内,稍后再试"
            replaced = self._defer_or_replace_planned_impulse(
                user,
                now=now,
                note="计划动机不适合当前时间",
                delay_minutes=(45, 150),
                block_current=window_phase == "tail" or impulse_value < 0.6,
            )
            if not replaced and _safe_float(user.get("next_proactive_at"), 0) <= 0:
                self._schedule_next_proactive(user, now=now)
            return False, "计划动机不适合当前时间"
        if self._private_user_role(user) == "friend" and self._action_has_photo_text(planned_action):
            fallback_action = self._fallback_action_for_unavailable(planned_action, user)
            if fallback_action != planned_action:
                planned_action = fallback_action
                user["planned_proactive_action"] = planned_action
        if not self._action_is_available(planned_action, user):
            load_defer_note = self._photo_text_load_defer_note(planned_action)
            if load_defer_note:
                self._defer_planned_photo_text_for_load(user, now=now, note=load_defer_note)
                return False, load_defer_note
            replaced = self._defer_or_replace_planned_impulse(
                user,
                now=now,
                note="动作不可用或媒体额度不足",
                delay_minutes=(90, 240),
                block_current=True,
            )
            if not replaced and _safe_float(user.get("next_proactive_at"), 0) <= 0:
                self._schedule_next_proactive(user, now=now, delay_hours=(2, 6))
            return False, "动作不可用或媒体额度不足"
        if not is_troubleshooting and timeliness == "routine" and self._planned_proactive_recently_repeated(user):
            replaced = self._defer_or_replace_planned_impulse(
                user,
                now=now,
                note="近期主题过于相似",
                delay_minutes=(120, 360),
                block_current=True,
            )
            if not replaced and _safe_float(user.get("next_proactive_at"), 0) <= 0:
                self._schedule_next_proactive(user, now=now, delay_hours=(2, 6))
            return False, "近期主动主题过于相似"
        if not is_troubleshooting and timeliness == "routine" and self._planned_event_exceeds_daypart_cap(user, planned_reason, next_at):
            delay = self._friend_proactive_spread_delay_hours(user, now=now)
            if delay is None:
                delay = (7.5, 10.5) if self._proactive_daypart_bucket_for_timestamp(next_at) == "late_night" else (2.5, 5.0)
            self._defer_or_replace_planned_impulse(
                user,
                now=now,
                note="当前时段主动已足够",
                delay_minutes=(delay[0] * 60, delay[1] * 60),
                block_current=False,
            )
            if _safe_float(user.get("next_proactive_at"), 0) <= 0:
                self._schedule_next_proactive(user, now=now, delay_hours=delay)
            if self._private_user_role(user) == "friend":
                return False, "朋友主动已按日内节奏延后"
            return False, "当前时段主动已足够,已避开扎堆"
        return True, "ok"

    def _proactive_decision_factors(self, user: dict[str, Any], *, now: float | None = None) -> list[dict[str, Any]]:
        now = _now_ts() if now is None else now
        factors: list[dict[str, Any]] = []

        def add(
            key: str,
            label: str,
            passed: bool,
            score: int,
            detail: str = "",
            *,
            blocker: bool = False,
        ) -> None:
            factors.append(
                {
                    "key": key,
                    "label": label,
                    "passed": bool(passed),
                    "score": int(score),
                    "detail": _single_line(detail, 160),
                    "blocker": bool(blocker),
                }
            )

        user_id = str(user.get("user_id") or user.get("id") or "")
        enabled = self._user_enabled_for_proactive(user_id, user)
        add("enabled", "用户启用", enabled, 18 if enabled else -80, "已启用" if enabled else "私聊对象未启用", blocker=not enabled)

        has_session = bool(user.get("umo"))
        add("session", "私聊会话", has_session, 12 if has_session else -70, "会话可用" if has_session else "缺少私聊会话", blocker=not has_session)

        if user.get("proactive_sending"):
            add("sending", "发送占用", False, -60, "上一条主动消息仍在发送中", blocker=True)
        else:
            add("sending", "发送占用", True, 6, "当前没有发送占用")

        daily_limit = self._effective_user_daily_limit(user)
        sent_today = _safe_int(user.get("sent_today"), 0)
        unlimited_daily_limit = self._proactive_daily_limit_is_unlimited(daily_limit)
        under_limit = daily_limit > 0 and (unlimited_daily_limit or sent_today < daily_limit)
        daily_limit_text = self._format_proactive_daily_limit(daily_limit)
        if daily_limit <= 0:
            add("daily_limit", "每日上限", False, -55, "每日上限为 0", blocker=True)
        else:
            add(
                "daily_limit",
                "每日上限",
                under_limit,
                8 if under_limit else -40,
                f"{sent_today}/{daily_limit_text}",
                blocker=not under_limit,
            )

        due_timer_active = self._has_due_llm_timer(user, now=now)
        source = self._normalize_legacy_proactive_text(user.get("planned_proactive_source"), limit=40)
        timeliness = self._planned_proactive_timeliness_level(user)
        if timeliness != "routine":
            add(
                "timeliness",
                "消息时效",
                True,
                8 if timeliness == "urgent" else 5,
                "紧急事件：放宽普通频率闸门" if timeliness == "urgent" else "短时效事件：适度放宽普通频率闸门",
                blocker=False,
            )
        rest_until = self._proactive_rest_block_until(
            user,
            now=now,
            reason=user.get("planned_proactive_reason"),
            source=source,
        )
        rest_blocked = rest_until > now and not due_timer_active
        add(
            "rest",
            "休息静默",
            not rest_blocked,
            5 if not rest_blocked else -45,
            "未命中静默" if not rest_blocked else "用户明确休息中",
            blocker=rest_blocked,
        )

        busy_until = 0.0
        busy_block_kind = ""
        busy_context_getter = getattr(self, "_busy_reply_proactive_block_context", None)
        busy_gate = getattr(self, "_busy_reply_proactive_block_until", None)
        if callable(busy_context_getter):
            try:
                busy_context = busy_context_getter(
                    user,
                    now=now,
                    reason=user.get("planned_proactive_reason"),
                    source=source,
                )
                if isinstance(busy_context, dict):
                    busy_until = _safe_float(busy_context.get("until"), 0.0)
                    busy_block_kind = _single_line(busy_context.get("kind"), 40)
            except Exception:
                busy_until = 0.0
        elif callable(busy_gate):
            try:
                busy_until = _safe_float(
                    busy_gate(
                        user,
                        now=now,
                        reason=user.get("planned_proactive_reason"),
                        source=source,
                    ),
                    0.0,
                )
            except Exception:
                busy_until = 0.0
        busy_blocked = (
            busy_until > now
            and not due_timer_active
            and (timeliness == "routine" or busy_block_kind == "external_realtime")
        )
        add(
            "bot_busy",
            "Bot 忙碌日程",
            not busy_blocked,
            4 if not busy_blocked else -35,
            (
                "短时效事件不受普通日程忙碌顺延"
                if busy_until > now and not busy_blocked
                else "当前不忙"
                if not busy_blocked
                else f"顺延到 {self._environment_fromtimestamp(busy_until).strftime('%H:%M')} 后"
            ),
            blocker=busy_blocked,
        )

        quiet_blocked = (
            self._is_quiet_time()
            and not self._can_send_insomnia_night_message(user)
            and not self._post_goodnight_group_activity_is_fresh(user, now=now)
        )
        add(
            "quiet_hours",
            "免打扰",
            not quiet_blocked,
            4 if not quiet_blocked else -42,
            "当前可发" if not quiet_blocked else "处于免打扰时段",
            blocker=quiet_blocked,
        )

        relationship_mode = self._current_relationship_gate_mode(user, now=now)
        emotion_mode = self._current_emotion_gate_mode(user, now=now)
        relationship_blocked = relationship_mode == "backoff"
        emotion_blocked = emotion_mode == "hurt"
        relation_ok = not (relationship_blocked or emotion_blocked)
        relation_detail = f"mode={relationship_mode or emotion_mode}" if not relation_ok else "状态平稳"
        add(
            "relationship_gate",
            "关系/情绪闸门",
            relation_ok,
            7 if relation_ok else -48,
            relation_detail,
            blocker=not relation_ok,
        )

        next_at = _safe_float(user.get("next_proactive_at"), 0)
        planned_reason = self._normalize_legacy_proactive_text(user.get("planned_proactive_reason"), limit=40)
        if next_at <= 0:
            add("planned", "候选计划", False, -12, "尚未安排下一次候选")
        else:
            due = now >= next_at
            add(
                "planned",
                "候选计划",
                due,
                10 if due else -10,
                (
                    self._environment_fromtimestamp(next_at).strftime("%m-%d %H:%M:%S")
                    if next_at > 0
                    else "未安排"
                ),
                blocker=False,
            )
        impulse_value = self._planned_impulse_value(user, now=now)
        window_phase, window_detail = self._planned_impulse_window_phase(user, now=now)
        phase_labels = {
            "before": "窗口未开始",
            "best": "最佳窗口",
            "tail": "窗口尾段",
            "expired": "已经过期",
            "unknown": "未记录",
        }
        window_ok = window_phase not in {"expired"}
        add(
            "impulse_window",
            "念头窗口",
            window_ok,
            7 if window_phase == "best" else 2 if window_phase == "tail" else -6 if window_phase == "before" else -35 if window_phase == "expired" else 0,
            f"{phase_labels.get(window_phase, window_phase)}｜{window_detail}",
            blocker=window_phase == "expired",
        )
        add(
            "impulse_value",
            "念头价值",
            impulse_value >= 0.55,
            8 if impulse_value >= 0.85 else 4 if impulse_value >= 0.65 else -8,
            f"{impulse_value:.2f}｜越高越像当前角色真的想说",
            blocker=False,
        )
        inner_readiness = self._proactive_inner_readiness(user, now=now)
        drive = inner_readiness.get("drive") if isinstance(inner_readiness.get("drive"), dict) else {}
        temperature = inner_readiness.get("temperature") if isinstance(inner_readiness.get("temperature"), dict) else {}
        inner_score = _safe_float(inner_readiness.get("score"), 0.55)
        add(
            "bot_drive",
            "Bot 开口欲",
            inner_score >= 0.36 or timeliness != "routine",
            7 if inner_score >= 0.72 else 3 if inner_score >= 0.5 else -14,
            f"{inner_score:.2f}｜{_single_line(inner_readiness.get('label'), 40)}｜{_single_line(drive.get('detail'), 70)}",
            blocker=inner_score < 0.28 and timeliness == "routine",
        )
        motivation = inner_readiness.get("motivation") if isinstance(inner_readiness.get("motivation"), dict) else {}
        if motivation:
            motivation_score = _safe_float(motivation.get("score"), 0.5)
            add(
                "experimental_motivation",
                "实验动机调度",
                motivation_score >= 0.40,
                6 if motivation_score >= 0.66 else 2 if motivation_score >= 0.50 else -10,
                f"{motivation_score:.2f}｜{_single_line(motivation.get('label'), 24)}｜{_single_line(motivation.get('detail'), 100)}",
                blocker=motivation_score < 0.28,
            )
        temp_score = _safe_float(temperature.get("score"), 0.55)
        add(
            "relationship_temperature",
            "主动表达温度",
            temp_score >= 0.34,
            7 if temp_score >= 0.7 else 3 if temp_score >= 0.48 else -16,
            f"{temp_score:.2f}｜{_single_line(temperature.get('label'), 24)}｜{_single_line(temperature.get('detail'), 80)}",
            blocker=temp_score < 0.24,
        )
        planned_impulse = self._planned_proactive_impulse(user)
        hesitation_count = _safe_int(planned_impulse.get("hesitation_count"), 0, 0, 20) if isinstance(planned_impulse, dict) else 0
        if hesitation_count > 0:
            add(
                "hesitation_memory",
                "犹豫记忆",
                True,
                min(6, 2 + hesitation_count),
                f"同一候选曾延后 {hesitation_count} 次｜{_single_line(planned_impulse.get('hesitation_note'), 80)}",
                blocker=False,
            )
        semantics = self._planned_proactive_semantics(user)
        semantic_score = _safe_float(semantics.get("score"), 0.5)
        semantic_pressure = _safe_float(semantics.get("pressure"), 0.4)
        semantic_risk = _safe_float(semantics.get("risk"), 0.0)
        semantic_ok = not bool(semantics.get("blocker")) and semantic_risk < 0.45 and not (semantic_score < 0.32 and semantic_pressure >= 0.58)
        add(
            "candidate_semantics",
            "候选语义",
            semantic_ok,
            8 if semantic_score >= 0.68 else 4 if semantic_score >= 0.48 else -18,
            (
                f"{_single_line(semantics.get('kind'), 30)}/{_single_line(semantics.get('anchor_type'), 30)}"
                f"｜语义{semantic_score:.2f} 压力{semantic_pressure:.2f} 风险{semantic_risk:.2f}"
                f"｜{_single_line(semantics.get('note'), 70)}"
            ),
            blocker=not semantic_ok,
        )
        persona_alignment = self._planned_proactive_persona_alignment(user, now=now)
        persona_fit = _safe_float(persona_alignment.get("score"), 0.55)
        persona_threshold = 0.48 if self._private_user_role(user) == "friend" else 0.42
        persona_blocked = bool(persona_alignment.get("blocker")) and source != "timer"
        persona_ok = due_timer_active or source == "timer" or (not persona_blocked and persona_fit >= persona_threshold)
        add(
            "persona_fit",
            "人格/世界观贴合",
            persona_ok,
            8 if persona_fit >= 0.78 else 4 if persona_fit >= 0.58 else -18,
            f"{persona_fit:.2f}｜{_single_line(persona_alignment.get('note'), 110)}",
            blocker=not persona_ok,
        )
        model_signature = self._planned_proactive_model_judge_signature(user)
        model_judgement = self._cached_proactive_model_judgement(user, signature=model_signature, now=now)
        if isinstance(model_judgement, dict):
            model_decision = str(model_judgement.get("decision") or "")
            model_score = _safe_int(model_judgement.get("score"), 0, 0, 100)
            model_ok = model_decision in {"send", "rewrite"}
            add(
                "model_persona_judge",
                "模型人格判定",
                model_ok,
                8 if model_decision == "send" else 4 if model_decision == "rewrite" else -30,
                f"{model_decision or 'unknown'}｜{model_score}/100｜{_single_line(model_judgement.get('reason'), 90)}",
                blocker=not model_ok,
            )
        else:
            add(
                "model_persona_judge",
                "模型人格判定",
                True,
                0,
                "未执行；硬规则通过且到点发送前执行",
                blocker=False,
            )

        last_seen = self._latest_private_user_activity_ts(user)
        idle_minutes = self._effective_user_idle_minutes(user)
        if self._is_greeting_reason(planned_reason):
            idle_minutes = self._effective_user_greeting_idle_minutes(user)
        idle_seconds = max(0, idle_minutes) * 60
        if timeliness == "urgent":
            idle_seconds = min(idle_seconds, 2 * 60.0)
        elif timeliness == "timely":
            idle_seconds = min(idle_seconds, 5 * 60.0)
        idle_elapsed = now - last_seen if last_seen > 0 else 999999999.0
        idle_passed = due_timer_active or idle_elapsed >= idle_seconds
        add(
            "idle",
            "用户空闲",
            idle_passed,
            9 if idle_passed else -28,
            (
                f"已空闲 {self._format_elapsed(max(0, idle_elapsed))} / 至少 {self._format_elapsed(idle_seconds)}"
                if last_seen > 0
                else "暂无活跃记录"
            ),
            blocker=not idle_passed and not due_timer_active,
        )

        last_sent = _safe_float(user.get("last_sent"), 0)
        min_interval = self._effective_min_interval_seconds(user)
        if self._is_greeting_reason(planned_reason) and self._private_user_role(user) != "friend":
            min_interval = min(min_interval, self._greeting_min_interval_seconds(planned_reason))
        if timeliness == "urgent":
            min_interval = min(min_interval, 2 * 60.0)
        elif timeliness == "timely":
            min_interval = min(min_interval, 10 * 60.0)
        send_elapsed = now - last_sent if last_sent > 0 else 999999999.0
        interval_passed = due_timer_active or send_elapsed >= min_interval
        add(
            "interval",
            "发送间隔",
            interval_passed,
            8 if interval_passed else -25,
            (
                f"已过 {self._format_elapsed(max(0, send_elapsed))} / 至少 {self._format_elapsed(min_interval)}"
                if last_sent > 0
                else "还没有主动发送记录"
            ),
            blocker=not interval_passed and not due_timer_active,
        )

        if planned_reason:
            reason_allowed = due_timer_active or self._is_reason_allowed_now(planned_reason, user)
            add(
                "reason_window",
                "时段适配",
                reason_allowed,
                6 if reason_allowed else -18,
                planned_reason,
                blocker=not reason_allowed and not due_timer_active,
            )

        planned_action = str(user.get("planned_proactive_action") or "message")
        action_ok = self._action_is_available(planned_action, user)
        add(
            "action",
            "动作可用",
            action_ok,
            6 if action_ok else -24,
            planned_action or "message",
            blocker=not action_ok,
        )

        repeated = self._planned_proactive_recently_repeated(user)
        dedupe_passed = not repeated or timeliness != "routine"
        add(
            "dedupe",
            "主题去重",
            dedupe_passed,
            6 if dedupe_passed else -20,
            (
                "同一事件仍由事件指纹去重，普通话题重复不阻断"
                if repeated and timeliness != "routine"
                else "近期无重复"
                if not repeated
                else "近期主动主题过于相似"
            ),
            blocker=not dedupe_passed,
        )

        total_score = 50 + sum(int(item.get("score") or 0) for item in factors)
        factors.append(
            {
                "key": "total",
                "label": "综合评分",
                "passed": total_score >= 50,
                "score": max(0, min(100, total_score)),
                "detail": "分数越高越适合现在发",
                "blocker": False,
            }
        )
        return factors

    def _passes_proactive_moment(self, user: dict[str, Any]) -> bool:
        hour = self._environment_now().hour
        state = self.data.get("daily_state", {})
        energy = _safe_int(state.get("energy") if isinstance(state, dict) else 70, 70, 0, 100)
        active_conditions = state.get("conditions", []) if isinstance(state, dict) else []
        current_item = self._proactive_current_agenda_item()
        can_do = self.data.get("can_do", [])
        important_dates = self._get_relevant_important_dates()
        ignored_streak = _safe_int(user.get("ignored_streak"), 0)

        probability = 0.32
        if 8 <= hour <= 11:
            probability += 0.16
        elif 14 <= hour <= 17:
            probability += 0.16
        elif 19 <= hour <= 22:
            probability += 0.18
        else:
            probability -= 0.05

        if energy < 40:
            probability += 0.12
        elif energy > 80:
            probability += 0.06
        if active_conditions:
            probability += min(0.18, len(active_conditions) * 0.06)
        if current_item:
            probability += 0.08
        if isinstance(can_do, list) and can_do:
            probability += 0.12
        if current_item and _single_line(current_item.get("message_seed"), 80):
            probability += 0.12
        if important_dates:
            probability += 0.1 if _safe_int(important_dates[0].get("_days_until"), 0) == 0 else 0.05
        unanswered_weight = _safe_float(
            self._proactive_quota_policy(user).get("unanswered_interval_weight"),
            1.0,
            0.0,
        )
        probability -= min(0.18, ignored_streak * 0.07) * min(1.0, unanswered_weight)
        probability *= self._daily_intensity_factor(user)
        probability = max(0.12, min(0.9, probability))
        return random.random() < probability

    async def _render_message(self, user: dict[str, Any]) -> tuple[str, str, str, list[Any], str, str]:
        name = str(user.get("nickname") or runtime_persona_setting(self, "default_nickname", "你"))
        user["planned_opener_mode"] = ""
        user.pop("_proactive_photo_subject_owner", None)
        planned_reason = self._normalize_legacy_proactive_text(user.get("planned_proactive_reason"), limit=40)
        planned_action = str(user.get("planned_proactive_action") or "message")
        planned_motive = _single_line(user.get("planned_proactive_motive"), 140)
        due_timer_active = self._has_due_llm_timer(user)
        troubleshooting_active = self._normalize_legacy_proactive_text(user.get("planned_proactive_source"), limit=40) == "troubleshooting"
        reason = planned_reason if planned_reason and (troubleshooting_active or due_timer_active or self._is_reason_allowed_now(planned_reason, user)) else ""
        if not reason:
            reason, _ = self._choose_proactive_message(user, name, planned_reason)
            planned_motive = self._choose_proactive_motive(reason, user, action=planned_action)
            planned_action = self._choose_action_for_reason(reason, user, motive=planned_motive)
        if self._should_use_name_only_opener(
            user,
            reason=reason,
            action=planned_action,
            motive=planned_motive,
        ):
            user["planned_opener_mode"] = "name_only"
            return reason, self._build_name_only_opener(name), "", [], "先轻轻叫了你一声", "message"
        budget_remaining = getattr(self, "_llm_daily_budget_remaining", None)
        if callable(budget_remaining) and budget_remaining() == 0:
            user["_proactive_render_failure_stage"] = "今日 Token 硬限额已耗尽，未执行主动动作"
            return reason, "", "", [], "Token 硬限额已耗尽", planned_action
        deferred_poke = planned_action == "poke"
        action_payload = (
            {
                "success": True,
                "context": "poke：待主动正文确认可发送后再执行；本阶段尚未产生实际戳一戳",
                "extra_components": [],
                "summary": "准备戳一下",
                "effective_action": "poke",
            }
            if deferred_poke
            else await self._execute_proactive_action(planned_action, user, name, reason)
        )
        effective_action = _single_line(action_payload.get("effective_action") or planned_action, 60) or "message"
        raw_action_context = str(action_payload.get("context") or "")
        if reason == "group_share":
            share_context = self._format_group_share_action_context(user)
            raw_action_context = "\n".join(part for part in (raw_action_context, share_context) if part).strip()
        if reason == "bili_video_share":
            video_context = self._format_bilibili_video_action_context(user)
            raw_action_context = "\n".join(part for part in (raw_action_context, video_context) if part).strip()
        if reason == "news_share":
            news_context = self._format_news_action_context(user)
            raw_action_context = "\n".join(part for part in (raw_action_context, news_context) if part).strip()
        if reason == "web_exploration_share":
            exploration_context = self._format_web_exploration_action_context(user)
            raw_action_context = "\n".join(part for part in (raw_action_context, exploration_context) if part).strip()
        if reason == "creative_share":
            creative_context = self._format_creative_share_action_context(user)
            raw_action_context = "\n".join(part for part in (raw_action_context, creative_context) if part).strip()
        if reason == "memory_echo":
            echo = user.get("memory_echo_context") if isinstance(user.get("memory_echo_context"), dict) else {}
            echo_summary = _single_line(echo.get("summary"), 180)
            echo_residue = _single_line(echo.get("residue"), 140)
            echo_correction = _single_line(echo.get("correction"), 180)
            echo_source_date = _single_line(echo.get("source_date"), 20) or "昨日"
            echo_context = (
                f"记忆回响证据（{echo_source_date}，摘要而非逐字原话）：概括={echo_summary}；残留={echo_residue}。"
                "只可把它当作轻微承接背景，不得添加摘要中没有的事实，不得使用引号伪装成用户原话，"
                "不要说‘系统记录/记忆库显示’，也不要要求用户必须回应。"
                + (
                    f"这是一次纠正后的记忆：{echo_correction}。必须沿用修正版，不要再次复述或维护原来的错误；"
                    "可以自然承认自己之前记岔过，但不要把故意出错写成表演。"
                    if echo_correction
                    else "若细节置信不足，使用‘我是不是记得……’这类留有余地的表达，允许用户自然纠正。"
                )
            )
            raw_action_context = "\n".join(part for part in (raw_action_context, echo_context) if part).strip()
        if reason == "mood_checkin":
            mood = user.get("mood_checkin_context") if isinstance(user.get("mood_checkin_context"), dict) else {}
            mood_context = (
                f"隔日情绪回访证据（摘要而非逐字原话）：{_single_line(mood.get('residue'), 160)}。"
                "只围绕这项已知状态轻声问一句今天是否好一点；不得诊断，不得扩大严重程度，"
                "不得声称用户现在仍处于昨天的状态，也不要连续追问。"
            )
            raw_action_context = "\n".join(part for part in (raw_action_context, mood_context) if part).strip()
        if reason == "absence_miss":
            absence = user.get("absence_miss_context") if isinstance(user.get("absence_miss_context"), dict) else {}
            absence_context = (
                f"自然停聊时长约 {_safe_float(absence.get('absent_days'), 0):.1f} 天。"
                "可以直接、简短地表达一点想念，但不得写成控诉、查岗、索取安抚或催促回复；"
                "不要虚构这几天用户的经历。"
            )
            raw_action_context = "\n".join(part for part in (raw_action_context, absence_context) if part).strip()
        if reason == "game_invite":
            game = user.get("game_invite_context") if isinstance(user.get("game_invite_context"), dict) else {}
            game_context = (
                f"游戏邀约证据：游戏={_single_line(game.get('game_label'), 40) or '上次那款游戏'}；"
                f"余韵={_single_line(game.get('reflection'), 160)}；语气={_single_line(game.get('tone'), 120)}。"
                "只发一次轻量、可拒绝的邀约；不要声称已经开房、已开始对局或现在轮到用户操作，"
                "也不要暴露 invite_interest 等内部评分。"
            )
            raw_action_context = "\n".join(part for part in (raw_action_context, game_context) if part).strip()
        extra_components = list(action_payload.get("extra_components") or [])
        action_summary = _single_line(action_payload.get("summary") or planned_action, 80)
        if not bool(action_payload.get("success", True)):
            if "photo_text" in {planned_action, effective_action}:
                logger.info(
                    "主动图片动作未产出,降级为纯文字分享: user=%s reason=%s topic=%s",
                    _single_line(user.get("user_id"), 40),
                    reason,
                    _single_line(user.get("planned_proactive_topic"), 80),
                )
                planned_action = "message"
                effective_action = "message"
                extra_components = []
                raw_action_context = "message：图片动作本轮未产出；只按原话题自然分享，不得声称已拍照、已生成或已发送图片"
                action_summary = "图片未产出，已降级为文字"
            else:
                user["_proactive_render_failure_stage"] = f"主动动作执行失败：{effective_action or planned_action or 'unknown'}"
                return reason, "", "", [], action_summary, effective_action
        image_path = self._extract_action_image_path(raw_action_context)
        photo_caption = self._extract_action_photo_caption(raw_action_context)
        photo_subject_owner = self._extract_action_photo_subject_owner(raw_action_context)
        if image_path:
            user["_proactive_photo_subject_owner"] = photo_subject_owner or "unknown"
        if image_path and photo_caption:
            action_summary = f"发图：{photo_caption}"
        action_context = await self._narrate_action_context(effective_action, raw_action_context)
        if image_path:
            action_context = f"{action_context}\n真实图片文件：{image_path}".strip()
        text = await self._generate_proactive_message_with_llm(
            user, name, reason, action_context, action=effective_action, motive=planned_motive
        )
        captured_text, captured_image_path, captured_extra_components = self._pop_framework_captured_send_payload(
            str(user.get("umo") or "")
        )
        deferred_photo = self._pop_framework_deferred_photo_payload(
            str(user.get("umo") or "")
        )
        deferred_photo_path = _path_text(deferred_photo.get("path"), 1000)
        if deferred_photo_path and os.path.exists(deferred_photo_path):
            deferred_caption = _single_line(deferred_photo.get("caption"), 500)
            text = deferred_caption
            image_path = deferred_photo_path
            extra_components = []
            effective_action = "photo_text"
            action_summary = f"发图：{deferred_caption}" if deferred_caption else "发送了一张图片"
            deferred_intent_kind = _single_line(deferred_photo.get("intent_kind"), 40)
            user["_proactive_photo_subject_owner"] = (
                "bot"
                if deferred_intent_kind in {"selfie", "sticker"}
                else "scene"
                if deferred_intent_kind == "text2img"
                else "unknown"
            )
            logger.info(
                "主动消息采用 pc_generate_photo 成图并进入统一发送链: user=%s kind=%s",
                _single_line(user.get("user_id"), 40),
                deferred_intent_kind or "unknown",
            )
        if not deferred_photo_path and (
            "photo_text" in effective_action or planned_action == "photo_text"
        ):
            if captured_text:
                text = captured_text
            if captured_image_path:
                image_path = captured_image_path
            if self._contains_inline_image_tag(text):
                image_path = ""
                extra_components = []
        if captured_extra_components and not deferred_photo_path:
            extra_components = list(captured_extra_components)
        if "photo_text" in planned_action and self._contains_inline_image_tag(text):
            image_path = ""
            extra_components = []
        if not image_path and not extra_components:
            text = self._remove_unbacked_media_claims(text)
        text = self._visible_text_without_tts_reading(text, limit=1000)
        text = self._normalize_proactive_sentence_flow(text)
        if reason == "group_share":
            recency_repair = getattr(self, "_repair_group_share_recency_text", None)
            if callable(recency_repair):
                text = recency_repair(user, text)
        sticker_pending_getter = getattr(self, "_proactive_sticker_only_pending", None)
        try:
            sticker_only_pending = bool(sticker_pending_getter(user.get("umo"))) if callable(sticker_pending_getter) else False
        except Exception:
            sticker_only_pending = False
        if not text and not image_path and not extra_components and not sticker_only_pending:
            return reason, "", "", [], action_summary, effective_action
        if deferred_poke:
            poke_payload = await self._execute_proactive_action("poke", user, name, reason)
            if not bool(poke_payload.get("success", False)):
                user["_proactive_render_failure_stage"] = _single_line(
                    poke_payload.get("context"), 160
                ) or "主动戳一戳执行失败"
                return reason, "", "", [], "戳一戳未执行", "poke"
            action_summary = _single_line(poke_payload.get("summary"), 80) or "戳了你一下"
        if sticker_only_pending:
            pre_poke_count, pre_poke_context = 0, ""
        else:
            pre_poke_count, pre_poke_context = await self._maybe_run_pre_message_poke(
                user,
                name,
                reason,
                action=effective_action,
                motive=planned_motive,
            )
        if pre_poke_context and not pre_poke_context.startswith("poke：已"):
            logger.info("消息前置戳一戳失败,跳过本次前置戳: %s", _single_line(pre_poke_context, 120))
        if pre_poke_count > 0:
            action_summary = f"先戳了 {pre_poke_count} 下 + {action_summary}"
            effective_action = f"poke+{effective_action}" if effective_action != "poke" else "poke"
        return reason, text, image_path, extra_components, action_summary, effective_action

