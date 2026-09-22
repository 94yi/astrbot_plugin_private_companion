# -*- coding: utf-8 -*-
"""事件挑选/构造域。

由 tools/split_mixin_domain.py 从 proactive_engine.py 机械抽取（32 个方法 + 0 个模块级名字 + 0 个类级赋值 / 1243 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveEngineMixin）。
"""
from __future__ import annotations

import hashlib
import random
import re
from .helpers import _now_ts, _safe_float, _safe_int, _single_line, _today_key
from .persona_config import runtime_persona_setting
from datetime import datetime, timedelta
from typing import Any

from .logging_util import get_module_logger
from .proactive_engine_shared import _engine_host

logger = get_module_logger(__name__)



class ProactiveEngineEventMixin:
    """事件挑选/构造域（从 ProactiveEngineMixin 拆出）。"""


    def _user_activity_question_targets_someone_else(self, text: str) -> bool:
        raw = _single_line(text, 180)
        if not raw:
            return False
        compact = re.sub(r"[\s,，。.!！?？~～…·、；;：:（）()【】\[\]\"'“”‘’]+", "", raw)
        if not compact:
            return False
        # “你觉得春希现在在干什么”虽然以“你”开头，询问对象仍是春希。
        # 这类认知/转述问句不能触发 Bot 自身状态、近期活动或状态记忆注入。
        return bool(
            re.search(
                r"(?:你|bot|机器人)(?:觉得|猜|知道|认为|看看|看|问).{0,24}"
                r"(?:干嘛|干啥|干什么|做什么|做啥|忙什么|忙啥)(?:呢|呀|啊|吗|嘛|没)?$",
                compact,
                flags=re.I,
            )
        )

    def _user_asks_bot_current_state_or_activity(self, text: str) -> bool:
        raw = _single_line(text, 120)
        if not raw:
            return False
        if raw.lstrip().startswith(("/", "／", "!", "！", "#", "＃")):
            return False
        compact = re.sub(r"[\s,，。.!！?？~～…·、；;：:（）()【】\[\]\"'“”‘’]+", "", raw)
        if not compact or len(compact) > 80:
            return False
        if re.search(r"(?:我|俺|咱|我们)(?:现在|这会儿|刚刚|刚才)?在?(?:干嘛|干啥|干什么|做什么|做啥|忙什么|忙啥)", compact):
            return False
        tech_status_words = ("插件", "系统", "接口", "API", "api", "配置", "页面", "排障", "日志", "服务", "连接", "模型", "任务", "进程")
        if "状态" in compact and any(word in raw for word in tech_status_words):
            return False
        direct_patterns = (
            r"(?:你|bot|机器人)?(?:现在|这会儿|这时候|刚才|今天)?在?(?:干嘛|干啥|干什么|做什么|做啥|忙什么|忙啥)(?:呢|呀|啊|吗|嘛|没)?$",
            r"(?:你|bot|机器人)?(?:现在|这会儿|今天)?在(?:上课|上班|睡觉|休息|吃饭|忙|摸鱼|干活|写作业|看书)(?:吗|嘛|没|呢)?$",
            r"(?:你|bot|机器人)(?:现在|这会儿|今天)?(?:状态|情况)?(?:怎么样|咋样|如何|还好吗|还好不|累不累|困不困|忙不忙|饿不饿)$",
            r"(?:你|bot|机器人)(?:现在|这会儿)?(?:什么状态|啥状态)$",
        )
        if any(re.fullmatch(pattern, compact, flags=re.I) for pattern in direct_patterns):
            return True
        # Do not treat a question addressed to the Bot *about somebody else*
        # as a request for the Bot's own state.  The permissive colloquial
        # fallback below intentionally accepts leading observations, so
        # cognition/reporting verbs need an explicit boundary first.
        if self._user_activity_question_targets_someone_else(compact):
            return False
        # 私聊里常见的口语问法会带承接词或观察性前缀，例如
        # “那你现在在干啥呢”“好像你在忙的样子，忙啥呢”。
        return bool(
            re.search(
                r"(?:你|bot|机器人).{0,16}(?:在)?(?:干嘛|干啥|干什么|做什么|做啥|忙什么|忙啥)(?:呢|呀|啊|吗|嘛|没)?$",
                compact,
                flags=re.I,
            )
        )

    def _proactive_item_is_state_share_for_current_status_question(self, item: dict[str, Any] | None) -> bool:
        if not isinstance(item, dict):
            return False
        reason = self._normalize_legacy_proactive_text(item.get("reason") or item.get("planned_proactive_reason"), limit=40)
        source = self._normalize_legacy_proactive_text(item.get("source") or item.get("planned_proactive_source"), limit=40)
        if source in {"timer", "troubleshooting", "simulation"}:
            return False
        if reason in {"group_share", "news_share", "bili_video_share", "web_exploration_share", "creative_share", "important_date_share"}:
            return False
        if reason in {"state_share", "activity_share", "background_schedule", "diary_share"}:
            return True
        text = " ".join(
            _single_line(item.get(key), 120)
            for key in (
                "topic",
                "planned_proactive_topic",
                "motive",
                "planned_proactive_motive",
                "why",
                "scene",
                "impulse",
            )
            if _single_line(item.get(key), 120)
        )
        if not text:
            return False
        state_tokens = (
            "当前日程", "现在日程", "当前细化", "正在", "刚好在",
            "上课", "上班", "摸鱼", "休息", "吃饭", "路上", "通勤", "回家", "小日常",
            "今天的小事", "刚看到", "刚听到", "刚经历",
        )
        return reason in {"check_in", "quiet_care"} and any(token in text for token in state_tokens)

    def _clear_state_share_proactive_after_user_status_question(
        self,
        user: dict[str, Any],
        *,
        user_id: str = "",
        text: str = "",
        now: float | None = None,
    ) -> bool:
        if not isinstance(user, dict) or not self._user_asks_bot_current_state_or_activity(text):
            return False
        check_now = _engine_host._now_ts() if now is None else now
        note = "用户已询问当前状态，状态分享念头已由被动回复承接"
        changed = False
        planned_item = {
            "reason": user.get("planned_proactive_reason"),
            "action": user.get("planned_proactive_action"),
            "source": user.get("planned_proactive_source"),
            "topic": user.get("planned_proactive_topic"),
            "motive": user.get("planned_proactive_motive"),
        }
        if _safe_float(user.get("next_proactive_at"), 0) > 0 and self._proactive_item_is_state_share_for_current_status_question(planned_item):
            self._mark_planned_candidate_status(user, "blocked", note)
            self._clear_pending_proactive_plan(user)
            changed = True
        for impulse in self._cleanup_proactive_impulses(user, now=check_now):
            if not isinstance(impulse, dict):
                continue
            state = _single_line(impulse.get("state") or "queued", 24).lower()
            if state not in {"queued", "deferred", "pending", ""}:
                continue
            if not self._proactive_item_is_state_share_for_current_status_question(impulse):
                continue
            impulse["state"] = "blocked"
            impulse["last_status"] = "blocked"
            impulse["last_note"] = note
            impulse["updated_ts"] = check_now
            changed = True
        target_user_id = _single_line(user_id or user.get("user_id") or user.get("id"), 40)
        if target_user_id:
            for candidate in self._cleanup_proactive_candidate_pool(now=check_now):
                if not isinstance(candidate, dict):
                    continue
                if self._candidate_user_id(candidate) != target_user_id:
                    continue
                status = _single_line(candidate.get("status"), 24).lower()
                if not self._pending_candidate_status(status):
                    continue
                if not self._proactive_item_is_state_share_for_current_status_question(candidate):
                    continue
                candidate["status"] = "blocked"
                candidate["note"] = note
                candidate["updated_ts"] = check_now
                changed = True
        if changed:
            logger.info(
                "用户已询问当前状态,已清理状态分享主动念头: user=%s text=%s",
                target_user_id or "unknown",
                _single_line(text, 80),
            )
        return changed

    def _friend_proactive_candidate_leaks_owner_environment(self, user: dict[str, Any], candidate: dict[str, Any]) -> bool:
        if not isinstance(user, dict) or self._private_user_role(user) != "friend" or not isinstance(candidate, dict):
            return False
        reason = self._normalize_legacy_proactive_text(candidate.get("reason"), limit=40)
        if reason not in {"activity_share", "diary_share", "background_schedule", "state_share", "check_in", "quiet_care"}:
            return False
        text = " ".join(
            _single_line(candidate.get(key), 180)
            for key in ("topic", "motive", "why", "scene", "impulse", "status")
            if _single_line(candidate.get(key), 180)
        )
        if not text:
            return False
        weather_tokens = (
            "天气", "气温", "温度", "降雨", "下雨", "阵雨", "小雨", "中雨", "大雨",
            "暴雨", "雷雨", "雷暴", "晴", "阳光", "多云", "阴天", "晚霞", "风",
            "外面在下雨", "天色",
        )
        location_tokens = (
            "当前位置", "当前地点", "所在城市", "住处", "住址", "地址", "小区", "街道",
            "校区", "宿舍", "家里", "学校", "工作地点", "路上", "通勤",
        )
        return any(token in text for token in weather_tokens) or any(token in text for token in location_tokens)

    def _pick_mobile_location_arrival_event(
        self,
        user: dict[str, Any],
        *,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        """Offer one gentle anchor when consented location confirms a place transition."""
        scene_getter = getattr(self, "_mobile_user_proactive_scene", None)
        if not callable(scene_getter):
            return None
        check_now = _engine_host._now_ts() if now is None else now
        budget_available = getattr(self, "_mobile_location_humanization_budget_available", None)
        if callable(budget_available) and not budget_available(user, now=check_now):
            return None
        try:
            scene = scene_getter(user, now=check_now)
        except TypeError:
            try:
                scene = scene_getter(user)
            except Exception:
                return None
        except Exception:
            return None
        if not isinstance(scene, dict) or not scene.get("recent_transition"):
            return None
        transition_key = _single_line(scene.get("transition_key"), 80)
        place_name = _single_line(scene.get("place_name"), 40)
        place_kind = _single_line(scene.get("place_kind"), 24)
        transition_kind = _single_line(scene.get("transition_kind"), 24)
        if not transition_key or not place_name:
            return None
        if _single_line(user.get("last_mobile_location_arrival_key"), 80) == transition_key:
            return None
        weather = _single_line(self._weather_summary_text(self.data.get("daily_weather", {})), 120)
        risk_getter = getattr(self, "_mobile_location_weather_is_safety_relevant", None)
        weather_risk = bool(risk_getter(weather) if callable(risk_getter) else any(
            token in weather for token in ("暴雨", "雷雨", "雷暴", "台风", "大风", "强风")
        ))
        if transition_kind == "departure" and place_kind == "home":
            topic = "风雨天刚离开家后的路上" if weather_risk else "刚离开家后的路上"
            motive = "外面风雨明显，刚出门，想提醒你路上留意一点" if weather_risk else "刚出门，想顺手跟你说一声"
            scene_hint = "用户刚离开已标记的家"
        elif transition_kind == "departure" and place_kind == "work":
            topic = "风雨天刚离开公司后的这一段" if weather_risk else "刚离开公司后的这一段"
            motive = "外面风雨明显，刚离开公司，想提醒你路上留意一点" if weather_risk else "刚离开公司，想顺手问问接下来怎么走"
            scene_hint = "用户刚离开已标记的工作地点"
        elif transition_kind == "departure":
            topic = f"刚离开{place_name}后的这一段"
            motive = f"刚离开{place_name}，想顺手跟你说一声"
            scene_hint = f"用户刚离开已标记地点{place_name}"
        elif place_kind == "home":
            topic = "刚到家后的这一小段"
            motive = "刚到家，想顺手跟你说一声"
            scene_hint = "用户刚进入已标记的家"
        elif place_kind == "work":
            topic = "到公司后的这会儿"
            motive = "到公司后缓下来一点，想顺手跟你说一声"
            scene_hint = "用户刚进入已标记的工作地点"
        else:
            topic = f"到{place_name}后的这会儿"
            motive = f"刚到{place_name}，想顺手跟你说一声"
            scene_hint = f"用户刚进入已标记地点{place_name}"
        priority_key = _single_line(user.get("mobile_location_priority_key"), 80)
        priority_until = _safe_float(user.get("mobile_location_priority_until"), 0)
        is_priority_arrival = priority_key and priority_key == transition_key and priority_until > check_now
        delay_seconds = _engine_host.random.uniform(5, 20) if is_priority_arrival else _engine_host.random.uniform(45, 240)
        battery = scene.get("battery_percent")
        low_battery = isinstance(battery, int) and battery <= 15 and not bool(scene.get("charging"))
        tone = "轻一点，短一点，不邀请长通话" if low_battery else "轻一点"
        return {
            "event_id": f"mobile-place-{transition_kind or 'arrival'}:{place_kind}:{transition_key}",
            "reason": "check_in",
            "action": "message",
            "topic": topic,
            "motive": motive,
            "scene": scene_hint,
            "tone": tone,
            "impulse": motive,
            "_scheduled_ts": check_now + delay_seconds,
            "_mobile_location_transition_key": transition_key,
            "_mobile_location_priority": bool(is_priority_arrival),
            "mobile_location_event_type": (
                "home_arrival" if transition_kind != "departure" and place_kind == "home" else "place_transition"
            ),
            "weather_linked": bool(weather_risk and transition_kind == "departure"),
        }

    def _pick_game_invite_event(
        self,
        user: dict[str, Any],
        *,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        """Turn a strong, recent game afterglow into one optional rematch invite."""
        check_now = _engine_host._now_ts() if now is None else now
        if _safe_int(user.get("ignored_streak"), 0, 0) > 0:
            return None
        state_getter = getattr(self, "_game_afterglow_for_user", None)
        view_getter = getattr(self, "_game_afterglow_public_view", None)
        if not callable(state_getter) or not callable(view_getter):
            return None
        try:
            view = view_getter(state_getter(user), now=check_now)
        except Exception:
            return None
        interest = _safe_int(view.get("invite_interest"), 0, 0, 100)
        last_event_at = _safe_float(view.get("last_event_at"), 0)
        if not view.get("active") or interest < 70 or last_event_at <= 0:
            return None
        event_age = check_now - last_event_at
        if event_age < 30 * 60 or event_age > 5 * 24 * 3600:
            return None
        last_sent = _safe_float(user.get("last_sent"), 0)
        if last_sent > 0 and check_now - last_sent < 10 * 3600:
            return None
        game = _single_line(view.get("game"), 40)
        game_label = _single_line(view.get("game_label"), 40) or game or "上次那局游戏"
        invite_key = hashlib.sha1(f"{game}|{int(last_event_at)}".encode("utf-8")).hexdigest()[:20]
        if invite_key in {
            _single_line(user.get("last_game_invite_key"), 40),
            _single_line(user.get("game_invite_checked_key"), 40),
        }:
            return None
        user["game_invite_checked_key"] = invite_key
        invite_probability = min(0.78, 0.28 + max(0, interest - 70) / 100)
        if _engine_host.random.random() > invite_probability:
            return None
        scheduled = self._move_timestamp_into_reason_window(
            check_now + _engine_host.random.randint(30, 120) * 60,
            "game_invite",
            user,
        )
        context = {
            "invite_key": invite_key,
            "game": game,
            "game_label": game_label,
            "last_event_at": last_event_at,
            "invite_interest": interest,
            "tone": _single_line(view.get("tone"), 120),
            "reflection": _single_line(view.get("reflection"), 160),
        }
        return {
            "window": self._window_from_delay_minutes(max(5, int((scheduled - check_now) / 60)), width_minutes=60),
            "reason": "game_invite",
            "action": "message",
            "why": "最近一局游戏留下了明确的再玩意愿，想发一次可拒绝、无催促的邀约",
            "topic": f"再玩一局{game_label}",
            "motive": f"想起上次的{game_label}，有点想再约一局",
            "_scheduled_ts": scheduled,
            "context_key": "game_invite_context",
            "context": context,
            "origin_event_id": f"game_invite:{invite_key}",
        }

    def _pick_state_need_event(
        self,
        user: dict[str, Any],
        *,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        now = now or _engine_host._now_ts()
        state = self.data.get("daily_state", {})
        if not isinstance(state, dict) or state.get("date") != _today_key():
            return None
        hunger_text = _single_line(state.get("hunger"), 80)
        if hunger_text in {"", "饥饿感平稳", "该人格不适用饥饿状态"}:
            return None
        if self._food_prompt_cooldown_remaining(user, now=now) > 0:
            return None
        if _safe_float(user.get("last_food_feedback_at"), 0) + 2 * 3600 > now:
            return None
        active_hunger = None
        for cond in self._get_active_conditions():
            if isinstance(cond, dict) and str(cond.get("kind") or "") == "hunger":
                active_hunger = cond
                break
        if not isinstance(active_hunger, dict):
            return None
        started = _safe_float(active_hunger.get("start_ts"), now)
        if now - started < 25 * 60:
            return None
        when = self._environment_fromtimestamp(now)
        minute = when.hour * 60 + when.minute
        if not (10 * 60 + 30 <= minute <= 21 * 60 + 40):
            return None
        intensity = max(
            0.0,
            min(1.0, runtime_persona_setting(self, "humanized_state_intensity", 50) / 100),
        )
        chance = 0.18 + 0.32 * intensity
        if _engine_host.random.random() > chance:
            return None
        delay_minutes = _engine_host.random.randint(4, 12) if now - started >= 55 * 60 else _engine_host.random.randint(12, 32)
        scheduled = now + delay_minutes * 60
        phase = _single_line(active_hunger.get("phase"), 24)
        topic = "吃点什么"
        if phase == "afternoon":
            topic = _engine_host.random.choice(["下午想吃点甜的", "下午想吃点咸的", "下午想吃点热的", "下午想吃点凉的"])
        elif phase == "late_snack":
            topic = "夜里要不要吃点东西"
        elif phase in {"lunch", "dinner"}:
            topic = "这一顿吃什么"
        return {
            "date": _today_key(),
            "window": self._window_from_delay_minutes(delay_minutes, width_minutes=18),
            "reason": "state_share",
            "action": "message",
            "why": "有些饿了",
            "topic": topic,
            "motive": self._normalize_internal_motive_text(
                "有些饿了，想问问用户吃什么"
            ),
            "scene": "饭点或嘴馋的小空档",
            "tone": "自然",
            "impulse": "想问问用户吃什么比较好",
            "_scheduled_ts": scheduled,
            "_state_need": "hunger",
        }

    @staticmethod
    def _is_sticky_greeting_event(event: dict[str, Any]) -> bool:
        reason = str(event.get("reason") or "")
        return (
            bool(event.get("_daily_greeting"))
            and reason in {"morning_greeting", "noon_greeting", "evening_greeting"}
        ) or bool(event.get("_daily_meal_care"))

    def _pick_open_loop_followup_event(
        self,
        user: dict[str, Any],
        now: float | None = None,
    ) -> dict[str, Any] | None:
        """Turn an unresolved conversation thread into a normal, expiring impulse."""
        if not bool(runtime_persona_setting(self, "enable_open_loop_tracking", True)):
            return None
        if self._private_user_role(user) == "friend":
            return None
        check_now = _engine_host._now_ts() if now is None else now
        if _safe_float(user.get("awaiting_reply_since"), 0) > 0:
            return None
        loops = user.get("open_loops")
        if not isinstance(loops, list):
            return None
        candidates: list[tuple[float, float, dict[str, Any]]] = []
        for item in loops:
            if not isinstance(item, dict) or str(item.get("status") or "") in {"已完成", "已取消"}:
                continue
            text = _single_line(item.get("text"), 120)
            created_at = _safe_float(item.get("created_ts"), 0)
            if not text or created_at <= 0:
                continue
            age = check_now - created_at
            if age < 4 * 3600 or age > 14 * 86400:
                continue
            last_candidate_at = _safe_float(item.get("proactive_candidate_at"), 0)
            if last_candidate_at > 0 and check_now - last_candidate_at < 36 * 3600:
                continue
            score_getter = getattr(self, "_open_loop_relevance_score", None)
            score = _safe_float(score_getter(item) if callable(score_getter) else 0.5, 0.5)
            candidates.append((score, created_at, item))
        if not candidates:
            return None
        _, created_at, selected = max(candidates, key=lambda value: (value[0], value[1]))
        text = _single_line(selected.get("text"), 120)
        sampler = getattr(self, "_sample_proactive_timestamp", None)
        scheduled = (
            sampler(user, now=check_now, delay_hours=(0.25, 2.0), reason="open_loop_followup")
            if callable(sampler)
            else check_now + _engine_host.random.uniform(15 * 60, 2 * 3600)
        )
        selected["proactive_candidate_at"] = check_now
        anonymous_pending = user.get("mobile_anonymous_area_pending")
        anonymous_linked = (
            isinstance(anonymous_pending, dict)
            and _safe_float(anonymous_pending.get("expires_at"), 0.0) > check_now
            and _safe_float(anonymous_pending.get("candidate_at"), 0.0) <= 0
        )
        if anonymous_linked:
            anonymous_pending["candidate_at"] = check_now
        open_loop_motive_prefix = "刚离开外面后，" if anonymous_linked else ""
        return {
            "date": _today_key(),
            "window": self._window_from_delay_minutes(max(15, int((scheduled - check_now) / 60)), width_minutes=75),
            "reason": "open_loop_followup",
            "action": "message",
            "why": "用户之前提过一件还没有下文的事，隔了一段时间后自然想起",
            "topic": text,
            "motive": self._normalize_internal_motive_text(f"{open_loop_motive_prefix}想自然问问之前提到的这件事后来怎么样了：{text}"),
            "scene": "离开外出区域后的聊天间隙，忽然想起对方之前说过的事" if anonymous_linked else "日常聊天间隙忽然想起对方之前说过的事",
            "tone": "像朋友随口问起，不像提醒或查岗",
            "impulse": "想知道那件事后来有没有新进展",
            "_scheduled_ts": scheduled,
            "origin_event_id": "open-loop:" + hashlib.sha1(f"{created_at}:{text}".encode("utf-8")).hexdigest()[:16],
            "context_key": "open_loop_followup_context",
            "context": {
                "text": text,
                "created_ts": created_at,
                "created_at": datetime.fromtimestamp(created_at).strftime("%Y-%m-%d %H:%M:%S"),
                "source": selected.get("source"),
                "after_anonymous_area_departure": anonymous_linked,
            },
            "followup_kind": "open_loop",
        }

    def _pick_pending_followup_event(
        self, user: dict[str, Any], now: float | None = None
    ) -> dict[str, Any] | None:
        now = now or _engine_host._now_ts()
        if self._private_user_role(user) == "friend":
            return None
        if self._in_llm_timer_silence_window(user, now=now):
            return None
        opener_event = self._build_suspended_opener_followup_event(user, now=now)
        if isinstance(opener_event, dict):
            return opener_event
        raw = user.get("pending_followup_event")
        if not isinstance(raw, dict):
            return None
        if raw.get("_meal_care_followup"):
            context = self._meal_care_active_context(user, now=now)
            blocked_by_newer_food_prompt = bool(
                context
                and self._meal_care_followup_blocked_by_newer_food_prompt(user, context, now=now)
            )
            if (
                not context
                or _safe_int(context.get("followup_count"), 0, 0, 1) >= 1
                or blocked_by_newer_food_prompt
            ):
                if blocked_by_newer_food_prompt:
                    context.update(
                        {
                            "active": False,
                            "stage": "closed_newer_food_prompt",
                            "closed_at": now,
                            "followup_due_at": 0,
                        }
                    )
                    user["meal_check_context"] = context
                user["pending_followup_event"] = {}
                return None
        raw = dict(raw)
        raw["reason"] = self._normalize_legacy_proactive_text(raw.get("reason"), limit=40) or _single_line(raw.get("reason"), 40) or "check_in"
        followup_date = str(raw.get("date") or "")
        if followup_date and followup_date != _today_key():
            return None
        scheduled = _safe_float(raw.get("_scheduled_ts"), 0)
        if scheduled <= 0:
            return None
        if scheduled <= now:
            return raw
        return raw

    def _build_suspended_opener_followup_event(
        self,
        user: dict[str, Any],
        *,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        raw = user.get("suspended_proactive")
        if not isinstance(raw, dict) or not raw.get("active"):
            return None
        if not raw.get("complaint_enabled") or raw.get("complaint_sent"):
            return None
        if max(_safe_float(user.get("awaiting_reply_since"), 0), _safe_float(user.get("last_sent"), 0)) <= 0:
            return None
        due_at = _safe_float(raw.get("complaint_after_ts"), 0)
        if due_at <= 0:
            return None
        now = now or _engine_host._now_ts()
        if now < due_at:
            return None
        name = _single_line(
            user.get("nickname") or runtime_persona_setting(self, "default_nickname", "你"),
            24,
        )
        return {
            "date": _today_key(),
            "window": self._window_from_delay_minutes(4, width_minutes=18),
            "reason": self._normalize_legacy_proactive_text(raw.get("complaint_reason"), limit=40) or "check_in",
            "action": "message",
            "why": "之前只叫了用户一声，因此把话说完",
            "topic": _single_line(raw.get("complaint_topic"), 80) or "刚才那句后面",
            "motive": _single_line(raw.get("complaint_motive"), 100) or f"刚才只喊了{name}一声，想补完话",
            "scene": "先前那句之后又过了一阵",
            "tone": _single_line(raw.get("complaint_tone"), 30) or "耐心等待",
            "impulse": "想把刚才没说完的话补上",
            "_scheduled_ts": due_at,
            "_opener_followup": True,
            "_cancel_on_inbound": True,
        }

    def _build_followup_event_from_chain(
        self,
        chain: list[dict[str, Any]] | None,
        *,
        origin_reason: str,
        origin_action: str,
        now_ts: float | None = None,
    ) -> dict[str, Any] | None:
        steps = [dict(step) for step in (chain or []) if isinstance(step, dict)]
        if not steps:
            return None
        current = None
        remaining: list[dict[str, Any]] = []
        consumed_name_only = False
        for step in steps:
            kind = str(step.get("kind") or "")
            if kind == "name_only_opener" and not consumed_name_only:
                consumed_name_only = True
                continue
            if current is None and kind in {"if_no_reply", "if_still_no_reply"}:
                current = step
                continue
            remaining.append(step)
        if not isinstance(current, dict):
            return None
        now_ts = now_ts or _engine_host._now_ts()
        after_minutes = _safe_int(current.get("after_minutes"), 18, 0, 240)
        origin_reason = self._normalize_legacy_proactive_text(origin_reason, limit=40)
        follow_reason = self._normalize_legacy_proactive_text(current.get("reason"), limit=40) or origin_reason or "check_in"
        if origin_reason == "morning_greeting" or follow_reason == "morning_greeting":
            after_minutes = max(after_minutes, 75)
        topic = _single_line(current.get("topic"), 80) or "刚才那条主动后面"
        motive = self._normalize_internal_motive_text(
            _single_line(current.get("motive"), 100) or "刚才那句话信息不够完整,所以想补充一句"
        )
        tone = _single_line(current.get("tone"), 30)
        return {
            "date": _today_key(),
            "window": self._window_from_delay_minutes(after_minutes, width_minutes=18),
            "reason": follow_reason,
            "action": "message",
            "why": "刚才那句话还有个具体点没说完,如果用户还没接住,就把那一点补上。",
            "topic": topic,
            "motive": motive,
            "scene": "前一条主动消息发出去后又过了一阵",
            "tone": "克制一点,把重点补上" if (origin_reason == "morning_greeting" or follow_reason == "morning_greeting") else (tone or "有点认真,顺手补上"),
            "impulse": "早上那句还差个重点,想补完整" if (origin_reason == "morning_greeting" or follow_reason == "morning_greeting") else "刚才那句话还有个点没落到实处,想补完整",
            "_scheduled_ts": now_ts + after_minutes * 60,
            "_origin_action": origin_action,
            "_origin_reason": origin_reason,
            "_cancel_on_inbound": True,
            "_chain_followup": True,
            "chain": remaining,
        }

    def _pick_daily_greeting_event(
        self, user: dict[str, Any], now: float | None = None
    ) -> dict[str, Any] | None:
        if not runtime_persona_setting(self, "enable_daily_greetings", True):
            return None
        self._reset_daily_counter_if_needed(user)
        sent = user.get("greetings_sent", [])
        if not isinstance(sent, list):
            sent = []
            user["greetings_sent"] = sent
        suppressed = user.get("greetings_suppressed_by_inbound", [])
        if not isinstance(suppressed, list):
            suppressed = []
            user["greetings_suppressed_by_inbound"] = suppressed
        now_dt = self._environment_fromtimestamp(now or _engine_host._now_ts())
        minute = now_dt.hour * 60 + now_dt.minute
        morning_start, morning_end = self._morning_greeting_window()
        anchors = [
            (
                "morning_greeting",
                f"{self._minutes_to_hhmm(morning_start)}-{self._minutes_to_hhmm(morning_end)}",
                "刚睡醒，想打个招呼",
                "刚醒",
            ),
            ("noon_greeting", "12:05-13:35", "中午有些犯困，想打个招呼", "午饭后那会儿"),
            ("evening_greeting", "20:10-21:20", "晚上闲下来时，想打个招呼", "天暗下来那会儿"),
        ]
        today = now_dt.date()
        candidates = []
        for reason, window, why, topic in anchors:
            if self._greeting_was_sent_today(user, reason) or reason in suppressed:
                continue
            start, end = self._parse_window_minutes(window)
            if start is None or end is None:
                continue
            if self._private_user_role(user) == "friend":
                bucket = self._proactive_daypart_bucket_for_minute(start)
                if _safe_int(self._today_proactive_daypart_counts(user).get(bucket), 0, 0) >= 1:
                    continue
            if self._recent_activity_satisfies_greeting(user, reason, now=now_dt.timestamp()):
                if reason not in suppressed:
                    suppressed.append(reason)
                continue
            if minute >= end:
                continue
            start_dt = datetime.combine(today, datetime.min.time(), tzinfo=now_dt.tzinfo) + timedelta(minutes=start)
            end_dt = datetime.combine(today, datetime.min.time(), tzinfo=now_dt.tzinfo) + timedelta(minutes=end)
            earliest = max(now_dt + timedelta(minutes=1), start_dt)
            if earliest >= end_dt:
                continue
            if reason == "morning_greeting":
                early_window_end = min(
                    end_dt.timestamp(),
                    (earliest + timedelta(minutes=18)).timestamp(),
                )
                scheduled = _engine_host.random.uniform(
                    earliest.timestamp(),
                    max(earliest.timestamp() + 60, early_window_end),
                )
            elif reason == "evening_greeting":
                tighten_end = min(end_dt.timestamp(), (earliest + timedelta(minutes=48)).timestamp())
                scheduled = _engine_host.random.uniform(earliest.timestamp(), max(earliest.timestamp() + 60, tighten_end))
            else:
                scheduled = _engine_host.random.uniform(earliest.timestamp(), end_dt.timestamp())
            if self._friend_proactive_scheduled_too_early(user, scheduled):
                continue
            candidates.append(
                (
                    scheduled,
                    {
                        "window": window,
                        "reason": reason,
                        "action": "message",
                        "_daily_greeting": True,
                        "conversation_posture": "closing" if reason == "evening_greeting" else "",
                        "why": why,
                        "topic": topic,
                        "_scheduled_ts": scheduled,
                    },
                )
            )
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0])
        return candidates[0][1]

    def _pick_insomnia_night_event(
        self,
        user: dict[str, Any],
        *,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        check_now = _engine_host._now_ts() if now is None else now
        if not self._can_send_insomnia_night_message(user, now=check_now):
            return None
        night_key = self._insomnia_night_key(check_now)
        planned_context = user.get("insomnia_night_context") if isinstance(user.get("insomnia_night_context"), dict) else {}
        if (
            self._normalize_legacy_proactive_text(user.get("planned_proactive_reason"), limit=40) == "insomnia_night"
            and _single_line(planned_context.get("night_key"), 20) == night_key
            and _safe_float(user.get("next_proactive_at"), 0) > 0
        ):
            return None
        current = self._environment_fromtimestamp(check_now)
        end = (
            current.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
            if current.hour >= 23
            else current.replace(hour=6, minute=0, second=0, microsecond=0)
        )
        remaining_seconds = int(end.timestamp() - check_now)
        if remaining_seconds <= 30:
            return None
        max_delay = max(20, min(22 * 60, remaining_seconds - 10))
        min_delay = min(4 * 60, max_delay)
        scheduled = check_now + _engine_host.random.randint(min_delay, max_delay)
        return {
            "window": "23:00-24:00" if current.hour >= 23 else "00:00-06:00",
            "reason": "insomnia_night",
            "action": "message",
            "why": "Bot 还醒着，夜里只想给用户留一句不要求回应的话",
            "topic": "夜里还醒着",
            "motive": "夜里一直没睡着，想短短和对方说一句",
            "conversation_posture": "closing",
            "_scheduled_ts": scheduled,
            "_proactive_source": "night_care",
            "context_key": "insomnia_night_context",
            "context": {"night_key": night_key},
        }

    def _pick_special_day_greeting_event(
        self,
        user: dict[str, Any],
        *,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        if self._private_user_role(user) != "owner" or bool(user.get("special_day_greeting_opt_out")):
            return None
        check_now = _engine_host._now_ts() if now is None else now
        current = self._environment_fromtimestamp(check_now)
        # A user's birthday owns the midnight ritual when it overlaps a
        # calendar holiday; avoid sending two competing greetings in one slot.
        if self._birthday_profile_matches_on_date(user, current) or self._birthday_profile_matches_on_date(
            user, current + timedelta(days=1)
        ):
            return None
        observance = self._special_day_observance(current)
        tomorrow = current + timedelta(days=1)
        tomorrow_observance = self._special_day_observance(tomorrow)
        current_minute = current.hour * 60 + current.minute
        if observance is None and not (tomorrow_observance and current_minute >= 21 * 60 + 30):
            return None
        target = observance or tomorrow_observance
        assert target is not None
        receipt_key = f"{target['key']}:{target['year']}"
        receipts = user.get("special_day_greeting_receipts")
        if isinstance(receipts, dict) and receipt_key in receipts:
            return None
        if observance:
            if current.hour == 0 and current.minute < 15:
                midnight_end = current.replace(hour=0, minute=15, second=0, microsecond=0).timestamp()
                remaining = int(midnight_end - check_now)
                if remaining > 10:
                    scheduled = check_now + _engine_host.random.randint(5, min(120, remaining - 5))
                    midnight = True
                else:
                    scheduled = current.replace(hour=8, minute=30, second=0, microsecond=0).timestamp() + _engine_host.random.randint(0, 35) * 60
                    midnight = False
            elif current.hour < 21:
                scheduled = check_now + _engine_host.random.randint(8, 28) * 60
                midnight = False
            else:
                return None
            date_text = current.date().isoformat()
        else:
            scheduled = datetime.combine(tomorrow.date(), datetime.min.time(), tzinfo=current.tzinfo).timestamp() + _engine_host.random.randint(1, 7) * 60
            midnight = True
            date_text = tomorrow.date().isoformat()
        return {
            "window": "00:00-00:15" if midnight else "08:30-21:30",
            "date": date_text,
            "reason": "special_day_greeting",
            "action": "message",
            "why": f"{target['title']}刚开始，想第一时间送一句有关系感的问候",
            "topic": f"{target['title']}的问候",
            "motive": f"今天是{target['title']}，想在特别的时间点先和对方说一句",
            "_scheduled_ts": scheduled,
            "_midnight_ritual": midnight,
            "_proactive_source": "special_day_ritual",
            "context_key": "planned_special_day_context",
            "context": {
                "observance_key": target["key"],
                "observance_title": target["title"],
                "observance_year": target["year"],
                "receipt_key": receipt_key,
                "delivery_timing": "midnight" if midnight else "daytime_fallback",
            },
        }

    def _pick_story_plan_event(
        self,
        now: float | None = None,
        *,
        user: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        plan = self.data.get("daily_story_plan", {})
        if not isinstance(plan, dict) or not self._is_plan_date_active(plan.get("date")):
            return None
        events = plan.get("proactive_events", [])
        if not isinstance(events, list):
            return None
        now = now or _engine_host._now_ts()
        future_events = []
        for event in events:
            if not isinstance(event, dict):
                continue
            if _single_line(event.get("lifecycle_status"), 20).lower() in {
                "cancelled", "canceled", "取消", "已取消", "expired", "skipped", "completed",
            }:
                continue
            if self._unverified_social_relay_plan_reason(
                event,
                source="event",
                has_trigger=bool(_single_line(event.get("trigger_message_id"), 120)),
            ):
                continue
            reason = str(event.get("reason") or "check_in")
            prepared, _invalid_reason = self._prepare_proactive_candidate_window(
                event,
                reason=reason,
                source="story",
                now=now,
            )
            if not isinstance(prepared, dict):
                continue
            event_ts = _safe_float(
                prepared.get("scheduled_ts"),
                self._timestamp_from_story_event(event, reason),
            )
            if event_ts > now or (
                event_ts > 0
                and now - event_ts
                <= runtime_persona_setting(self, "max_proactive_plan_lag_minutes", 180) * 60
            ):
                future_events.append((event_ts, event))
        if not future_events:
            return None
        future_events.sort(key=lambda item: item[0])
        shortlist = future_events[:6]
        weighted: list[tuple[dict[str, Any], float]] = []
        daypart_counts = self._today_proactive_daypart_counts(user or {})
        friend_user = isinstance(user, dict) and self._private_user_role(user) == "friend"
        for index, (_, event) in enumerate(shortlist):
            event_ts = self._timestamp_from_story_event(event, str(event.get("reason") or "check_in"))
            if friend_user and user is not None and self._friend_proactive_scheduled_too_early(user, event_ts):
                continue
            priority_tuple = self._event_priority(event)
            priority_score = float(-priority_tuple[0])
            weight = 1.0 + priority_score * 0.08 + max(0.0, 0.45 - index * 0.06)
            bucket = self._proactive_daypart_bucket_for_event(event)
            sent_in_bucket = _safe_int(daypart_counts.get(bucket), 0, 0) if bucket else 0
            if friend_user and bucket and sent_in_bucket >= 1:
                continue
            if bucket == "late_night" and sent_in_bucket >= 1 and not self._is_sticky_greeting_event(event):
                continue
            if bucket and sent_in_bucket >= 2 and not self._is_sticky_greeting_event(event):
                continue
            if sent_in_bucket > 0:
                weight *= max(0.22, 0.56 ** sent_in_bucket)
            if bucket == "late_night":
                weight *= 0.72
            weighted.append((event, weight))
        if not weighted and shortlist:
            for _, event in shortlist:
                if self._is_sticky_greeting_event(event):
                    weighted.append((event, 1.0))
                    break
        if not weighted:
            return None
        return self._weighted_choice(weighted)

    def _today_proactive_daypart_counts(self, user: dict[str, Any]) -> dict[str, int]:
        if not isinstance(user, dict):
            return {}
        self._reset_daily_counter_if_needed(user)
        raw = user.get("proactive_daypart_counts")
        if not isinstance(raw, dict):
            raw = {}
            user["proactive_daypart_counts"] = raw
        counts: dict[str, int] = {}
        for key, value in raw.items():
            text_key = str(key or "")
            if text_key:
                counts[text_key] = _safe_int(value, 0, 0)
        return counts

    def _proactive_daypart_bucket_for_event(self, event: dict[str, Any]) -> str:
        reason = str(event.get("reason") or "check_in")
        event_ts = self._timestamp_from_story_event(event, reason)
        if event_ts <= 0:
            start, _ = self._parse_window_minutes(str(event.get("window") or ""))
            if start is None:
                return ""
            minute = start
        else:
            when = self._environment_fromtimestamp(event_ts)
            minute = when.hour * 60 + when.minute
        return self._proactive_daypart_bucket_for_minute(minute)

    def _proactive_daypart_bucket_for_timestamp(self, timestamp: float) -> str:
        if timestamp <= 0:
            return ""
        when = self._environment_fromtimestamp(timestamp)
        return self._proactive_daypart_bucket_for_minute(when.hour * 60 + when.minute)

    def _planned_event_exceeds_daypart_cap(self, user: dict[str, Any], reason: str, scheduled_at: float) -> bool:
        if reason in {"insomnia_night", "important_date_share"}:
            return False
        if bool(self._proactive_intensity_effect("ignore_soft_daily_target", False)):
            return False
        if self._friend_proactive_scheduled_too_early(user, scheduled_at):
            return True
        bucket = self._proactive_daypart_bucket_for_timestamp(scheduled_at)
        if not bucket:
            return False
        counts = self._today_proactive_daypart_counts(user)
        sent_in_bucket = _safe_int(counts.get(bucket), 0, 0)
        if bucket == "late_night":
            return sent_in_bucket >= 1
        return sent_in_bucket >= 2

    @staticmethod
    def _proactive_daypart_bucket_for_minute(minute: int) -> str:
        if minute < 11 * 60:
            return "morning"
        if minute < 14 * 60 + 30:
            return "noon"
        if minute < 18 * 60:
            return "afternoon"
        if minute < 21 * 60:
            return "evening"
        return "late_night"

    def _note_proactive_daypart_sent(self, user: dict[str, Any], sent_at: float | None = None) -> None:
        self._reset_daily_counter_if_needed(user)
        when = self._environment_fromtimestamp(sent_at or _engine_host._now_ts())
        bucket = self._proactive_daypart_bucket_for_minute(when.hour * 60 + when.minute)
        raw = user.setdefault("proactive_daypart_counts", {})
        if not isinstance(raw, dict):
            raw = {}
            user["proactive_daypart_counts"] = raw
        raw[bucket] = _safe_int(raw.get(bucket), 0, 0) + 1

    def _maybe_make_followup_event(self, user: dict[str, Any], reason: str, action: str) -> dict[str, Any] | None:
        daily_limit = self._effective_user_daily_limit(user)
        if (
            not self._proactive_daily_limit_is_unlimited(daily_limit)
            and _safe_int(user.get("sent_today"), 0) >= max(0, daily_limit - 1)
        ):
            return None
        if action not in {"photo_text", "poke", "voice", "screen_peek"} and "+" not in action:
            return None
        chance = 0.12
        if "voice" in action:
            chance += 0.06
        if "photo_text" in action:
            chance += 0.05
        if "poke" in action:
            chance += 0.03
        if _engine_host.random.random() > chance:
            return None
        delay_minutes = _engine_host.random.randint(22, 95)
        follow_reason = "check_in" if action in {"poke", "screen_peek"} else "diary_share"
        topic = {
            "photo_text": "对发送的图片进行补充说明",
            "poke": "刚才戳完之后进行补充说明",
            "voice": "发完语音后的互动",
            "screen_peek": "偷看用户屏幕后的互动",
        }.get(action.split("+")[0], "刚刚那条主动后面")
        motive = {
            "photo_text": "刚才发完图以后，想和{name}聊聊",
            "poke": "刚才戳完以后，想和{name}聊聊",
            "voice": "刚才发完语音消息以后，想和{name}聊聊",
            "screen_peek": "刚才看过屏幕后，想问问{name}现在还忙不忙",
        }.get(action.split("+")[0], "刚才那条主动后面，还有一句话想补上")
        display_name = _single_line(
            user.get("nickname") or runtime_persona_setting(self, "default_nickname", "你"),
            24,
        )
        if display_name:
            motive = motive.replace("{name}", display_name)
        return {
            "date": _today_key(),
            "window": self._window_from_delay_minutes(delay_minutes, width_minutes=26),
            "reason": follow_reason,
            "action": "message",
            "why": "上一条主动消息之后进行自然的接话",
            "topic": topic,
            "motive": motive,
            "scene": "上一条主动消息发出去之后的互动",
            "tone": "自然",
            "impulse": "想接着刚才的话继续聊聊",
            "_scheduled_ts": _engine_host._now_ts() + delay_minutes * 60,
            "_origin_action": action,
            "_origin_reason": reason,
            "_cancel_on_inbound": True,
        }

    def _bot_currently_bored_for_unanswered_peek(self, user: dict[str, Any]) -> bool:
        text_parts = [
            user.get("last_proactive_reason"),
            user.get("last_proactive_action"),
            user.get("last_proactive_motive"),
            user.get("planned_proactive_reason"),
            user.get("planned_proactive_motive"),
        ]
        current_item = self._proactive_current_agenda_item()
        if isinstance(current_item, dict):
            text_parts.extend(
                [
                    current_item.get("activity"),
                    current_item.get("mood"),
                    current_item.get("message_seed"),
                ]
            )
        snapshot = self._current_story_plan_snapshot()
        if isinstance(snapshot, dict):
            text_parts.extend(snapshot.values())
        text = " ".join(_single_line(part, 80) for part in text_parts if part)
        bored_tokens = (
            "无聊", "发呆", "摸鱼", "闲", "空", "没事", "百无聊赖", "松下来",
            "喘口气", "空档", "空隙", "刷视频", "短视频", "休息",
        )
        if any(token in text for token in bored_tokens):
            return True
        reason = self._normalize_legacy_proactive_text(user.get("last_proactive_reason") or user.get("planned_proactive_reason"), limit=40)
        return reason in {"check_in", "quiet_care", "background_schedule"} and _safe_int(user.get("ignored_streak"), 0) >= 1

    def _maybe_make_unanswered_screen_peek_event(
        self,
        user: dict[str, Any],
        reason: str,
        action: str,
    ) -> dict[str, Any] | None:
        if not runtime_persona_setting(self, "enable_unanswered_screen_peek_followup", True):
            return None
        if "screen_peek" in str(action or ""):
            return None
        if not self._screen_glance_available(user, ignore_daily_limit=True):
            return None
        now = _engine_host._now_ts()
        cooldown = max(
            30,
            runtime_persona_setting(self, "unanswered_screen_peek_cooldown_minutes", 180),
        ) * 60
        last_at = _safe_float(user.get("last_unanswered_screen_peek_at"), 0)
        if last_at > 0 and now - last_at < cooldown:
            return None
        if not self._bot_currently_bored_for_unanswered_peek(user):
            return None
        delay_minutes = max(
            10,
            runtime_persona_setting(self, "unanswered_screen_peek_after_minutes", 45),
        )
        return {
            "date": _today_key(),
            "window": self._window_from_delay_minutes(delay_minutes, width_minutes=18),
            "reason": "check_in",
            "action": "screen_peek",
            "why": "上一条之后那边一直安静，想看一眼是不是还在忙。",
            "topic": "看看那边是不是还在忙",
            "motive": "那边一直安静着",
            "scene": "上一条主动消息之后的安静空档",
            "tone": "好奇",
            "impulse": "想看一眼那边是不是还在忙",
            "_scheduled_ts": now + delay_minutes * 60,
            "_cancel_on_inbound": True,
            "_unanswered_screen_peek": True,
            "_free_screen_peek": True,
            "_origin_action": action,
            "_origin_reason": reason,
        }

    def _timestamp_from_story_event(self, event: dict[str, Any], reason: str) -> float:
        scheduled_ts = _safe_float(event.get("_scheduled_ts"), 0)
        if scheduled_ts > 0:
            return scheduled_ts
        window = str(event.get("window") or "").strip()
        match = re.fullmatch(r"(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})", window)
        now_dt = self._environment_now()
        today = now_dt.date()
        if match:
            sh, sm, eh, em = [int(part) for part in match.groups()]
            start = datetime.combine(today, datetime.min.time(), tzinfo=now_dt.tzinfo).replace(hour=sh % 24, minute=sm)
            end = datetime.combine(today, datetime.min.time(), tzinfo=now_dt.tzinfo).replace(hour=eh % 24, minute=em)
            if end <= start:
                end = end + timedelta(days=1)
            if now_dt >= end:
                return 0
            earliest = max(start.timestamp(), (now_dt + timedelta(seconds=45)).timestamp())
            latest = end.timestamp()
            if earliest >= latest:
                return 0
            scheduled = _engine_host.random.uniform(earliest, latest)
            event["_scheduled_ts"] = scheduled
            return scheduled
        scheduled = self._move_timestamp_into_reason_window(_engine_host._now_ts() + _engine_host.random.uniform(2 * 3600, 10 * 3600), reason)
        event["_scheduled_ts"] = scheduled
        return scheduled

    def _reschedule_greeting_within_window(
        self,
        user: dict[str, Any],
        reason: str,
        *,
        now: float | None = None,
    ) -> bool:
        if not self._is_sticky_greeting_reason(reason):
            return False
        now_dt = self._environment_fromtimestamp(now or _engine_host._now_ts())
        windows = self._reason_windows(reason)
        if not windows:
            return False
        today = now_dt.date()
        for start, end in windows:
            start_dt = datetime.combine(today, datetime.min.time(), tzinfo=now_dt.tzinfo) + timedelta(minutes=start)
            end_dt = datetime.combine(today, datetime.min.time(), tzinfo=now_dt.tzinfo) + timedelta(minutes=end)
            if now_dt >= end_dt:
                continue
            earliest = max(now_dt + timedelta(minutes=_engine_host.random.randint(6, 14)), start_dt)
            latest = end_dt - timedelta(minutes=3)
            if earliest >= latest:
                continue
            user["next_proactive_at"] = _engine_host.random.uniform(earliest.timestamp(), latest.timestamp())
            return True
        return False

    def _pick_life_thought_topic(self, reason: str = "") -> str:
        terms = self._worldview_terms()
        if reason == "group_share":
            return f"{terms['group_chat']}里那段片段"
        if reason == "bili_video_share":
            return f"刚看到的{terms['video']}"
        if reason == "news_share":
            return "刚看到的一条新闻"
        if reason == "creative_share":
            return "刚写到的小说片段"
        current_item = self._proactive_current_agenda_item()
        activity = _single_line((current_item or {}).get("activity"), 36)
        if activity:
            return f"{activity}里自然冒出来的小内容"
        if reason == "diary_share":
            return "今天记录里想给你看看的一小段"
        return "当前时段里自然冒出来的小内容"

    def _should_use_name_only_opener(
        self,
        user: dict[str, Any],
        *,
        reason: str,
        action: str,
        motive: str,
    ) -> bool:
        if self._private_user_role(user) == "friend":
            return False
        if action != "message":
            return False
        if str(user.get("planned_followup_kind") or "") == "suspended_opener":
            return False
        chain = user.get("planned_event_chain")
        if isinstance(chain, list) and chain:
            first = chain[0] if isinstance(chain[0], dict) else {}
            if str(first.get("kind") or "") == "name_only_opener":
                return True
        if reason not in {"check_in", "quiet_care", "state_share", "evening_greeting", "insomnia_night"}:
            return False
        if _safe_float(user.get("awaiting_reply_since"), 0) > 0:
            return False
        profile = self._persona_action_profile()
        chance = 0.09
        if reason in {"quiet_care", "evening_greeting", "insomnia_night"}:
            chance += 0.05
        if profile.get("clingy"):
            chance += 0.06
        if profile.get("observant"):
            chance += 0.03
        if profile.get("playful"):
            chance += 0.02
        if any(token in motive for token in ("来找你", "确认一下用户状态", "想和用户说一句", "放心不下", "想看你在不在")):
            chance += 0.05
        if self._is_vague_seek_user_motive(reason, action, motive):
            chance *= 0.45
        return _engine_host.random.random() < min(0.32, chance)

    def _build_name_only_opener(self, name: str) -> str:
        clean_name = _single_line(name, 24) or runtime_persona_setting(
            self, "default_nickname", "你"
        )
        return f"{clean_name}……"

    def _build_suspended_proactive_payload(
        self,
        *,
        opener_text: str,
        reason: str,
        action: str,
        motive: str,
        action_summary: str,
        chain: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        profile = self._persona_action_profile()
        delay_minutes = _engine_host.random.randint(26, 95)
        complaint_chance = 0.18
        if profile.get("clingy"):
            complaint_chance += 0.16
        if profile.get("playful"):
            complaint_chance += 0.08
        if reason in {"quiet_care", "insomnia_night", "evening_greeting"}:
            complaint_chance += 0.08
        if reason == "morning_greeting":
            delay_minutes = _engine_host.random.randint(80, 150)
            complaint_chance = min(complaint_chance, 0.08)
        chain = list(chain or [])
        no_reply_step = None
        still_no_reply_step = None
        for step in chain:
            if not isinstance(step, dict):
                continue
            kind = str(step.get("kind") or "")
            if kind == "if_no_reply" and no_reply_step is None:
                no_reply_step = step
            elif kind == "if_still_no_reply" and still_no_reply_step is None:
                still_no_reply_step = step
        complaint_after_minutes = _safe_int((no_reply_step or {}).get("after_minutes"), delay_minutes, 0, 240)
        if reason == "morning_greeting":
            complaint_after_minutes = max(complaint_after_minutes, 75)
        return {
            "active": True,
            "resume_ready": False,
            "created_at": _engine_host._now_ts(),
            "opener_text": _single_line(opener_text, 60),
            "reason": reason,
            "action": action,
            "motive": self._normalize_internal_motive_text(motive),
            "summary": _single_line(action_summary, 60),
            "complaint_enabled": bool(no_reply_step) or _engine_host.random.random() < min(0.55, complaint_chance),
            "complaint_sent": False,
            "complaint_after_ts": _engine_host._now_ts() + complaint_after_minutes * 60,
            "complaint_reason": _single_line((no_reply_step or {}).get("reason"), 40),
            "complaint_topic": _single_line((no_reply_step or {}).get("topic"), 80),
            "complaint_motive": self._normalize_internal_motive_text(_single_line((no_reply_step or {}).get("motive"), 100)),
            "complaint_tone": "克制一点,把重点补上" if reason == "morning_greeting" else _single_line((no_reply_step or {}).get("tone"), 30),
            "second_followup": still_no_reply_step if isinstance(still_no_reply_step, dict) else {},
        }

