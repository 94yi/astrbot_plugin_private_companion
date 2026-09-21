# -*- coding: utf-8 -*-
"""proactive 域。

由 tools/split_mixin_domain.py 从 daily_state.py 机械抽取（43 个方法 + 0 个模块级名字 + 0 个类级赋值 / 1625 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 DailyStateMixin）。
"""
from __future__ import annotations

import hashlib
import math
import random
import re
from .helpers import (
    _normalize_photo_subject_owner,
    _now_ts,
    _path_text,
    _safe_float,
    _safe_int,
    _single_line,
    _strip_internal_message_blocks,
    _today_key,
    normalize_legacy_tag_text,
)
from .persona_config import runtime_persona_setting
from copy import deepcopy
from pathlib import Path
from typing import Any

from .logging_util import get_module_logger

logger = get_module_logger(__name__)



class DailyStateProactiveMixin:
    """proactive 域（从 DailyStateMixin 拆出）。"""


    def _reschedule_users_for_new_detail_events(self, segment: dict[str, Any]) -> None:
        users = self.data.get("users", {})
        if not isinstance(users, dict):
            return
        now = _now_ts()
        start = _safe_int(segment.get("start"), 0) * 60
        end = _safe_int(segment.get("end"), 0) * 60
        for user in users.values():
            if not isinstance(user, dict) or not user.get("umo"):
                continue
            next_at = _safe_float(user.get("next_proactive_at"), 0)
            if next_at <= 0:
                self._schedule_next_proactive(user, now=now)
                continue
            dt = self._environment_fromtimestamp(next_at)
            seconds_today = dt.hour * 3600 + dt.minute * 60 + dt.second
            if not (start <= seconds_today <= end):
                self._schedule_next_proactive(user, now=now)

    async def _apply_detail_presence_status(
        self,
        segment: dict[str, Any],
        detail: dict[str, Any] | None = None,
    ) -> None:
        if not self.enable_qq_presence_sync:
            return
        status = (detail or {}).get("presence_status") if isinstance(detail, dict) else None
        if not isinstance(status, dict):
            key = str((segment or {}).get("key") or "")
            enhanced = self.data.get("detail_enhanced_segments", {})
            snapshot = enhanced.get(key) if isinstance(enhanced, dict) else None
            status = snapshot.get("presence_status") if isinstance(snapshot, dict) else None
        # An omitted/unchanged status still matters when moving away from a
        # status that this plugin applied for the previous detail segment.
        # Treat it as a transition request, while leaving unrelated manual
        # QQ status changes untouched.
        if not isinstance(status, dict):
            status = {"mode": "unchanged"}
        key = str((segment or {}).get("key") or "")
        state = self.data.setdefault("qq_presence_state", {})
        if not isinstance(state, dict):
            state = {}
            self.data["qq_presence_state"] = state
        mode = str(status.get("mode") or status.get("status") or "unchanged").strip().lower()
        if mode in {"away", "invisible", "dnd", "do_not_disturb", "离开", "隐身", "请勿打扰", "勿扰"}:
            mode = "online"
        custom_text = _single_line(
            status.get("custom_text")
            or status.get("wording")
            or status.get("text")
            or status.get("label")
            or status.get("自定义状态")
            or status.get("文案"),
            28,
        )
        custom_sync_enabled = bool(getattr(self, "enable_qq_custom_presence_sync", False))
        custom_note = ""
        if mode in {"busy", "忙碌"}:
            if custom_sync_enabled:
                mode = "custom"
                custom_text = custom_text or "专注中"
            else:
                mode = "busy"
                custom_text = ""
                custom_note = "自定义短状态未开启，已改用标准忙碌"
        if mode in {"sleep", "睡觉", "睡眠"}:
            if custom_sync_enabled:
                mode = "custom"
                custom_text = custom_text or "休息中"
            else:
                return
        if mode in {"custom", "自定义", "自定义状态"} and not custom_sync_enabled:
            # A disabled custom-status feature must not clear a status managed
            # manually or by another QQ client.  Treat this plan as unchanged.
            return
        if mode in {"custom", "自定义", "自定义状态"} and not custom_text:
            return
        same_presence = (
            str(state.get("mode") or "") == mode
            and str(state.get("custom_text") or "") == custom_text
        )
        elapsed = _now_ts() - _safe_float(state.get("updated_at"), 0)
        previous_detail_key = str(state.get("detail_key") or "")
        detail_changed = bool(key and previous_detail_key and previous_detail_key != key)
        same_plan = (
            str(state.get("date") or "") == _today_key()
            and str(state.get("plan_date") or "") == str(self.data.get("detail_enhanced_day") or "")
            and previous_detail_key == key
        )
        if same_presence and not detail_changed and (
            (same_plan and bool(state.get("ok", False)) and elapsed < 10 * 60)
            or (not bool(state.get("ok", False)) and elapsed < 60 * 60)
        ):
            return
        if mode in {"", "unchanged", "keep", "保持", "不变"}:
            # Only reset a status with an explicit plugin ownership marker.
            # This avoids turning a user's manually selected QQ status into
            # online merely because the next schedule segment is quiet.
            if not detail_changed or not bool(state.get("managed_by_plugin", bool(previous_detail_key))):
                return
            if str(state.get("mode") or "") == "online" and not str(state.get("custom_text") or ""):
                state["detail_key"] = key
                state["date"] = _today_key()
                state["plan_date"] = str(self.data.get("detail_enhanced_day") or "")
                self._save_daily_state_sections({"qq_presence_state"})
                return
            ok, note = await self._set_qq_online_presence("online")
            state["detail_key"] = key
            state["date"] = _today_key()
            state["plan_date"] = str(self.data.get("detail_enhanced_day") or "")
            state["mode"] = "online"
            state["custom_text"] = ""
            state["reason"] = "当前日程段未要求自定义状态"
            state["updated_at"] = _now_ts()
            state["ok"] = bool(ok)
            state["note"] = _single_line(note, 120)
            state["managed_by_plugin"] = True
            self._save_daily_state_sections({"qq_presence_state"})
            return
        if mode in {"custom", "自定义", "自定义状态"}:
            ok, note = await self._set_qq_custom_presence(custom_text)
            mode = "custom"
            if not ok:
                note = f"{note}；未追加在线状态，保持账号原状态"
        else:
            ok, note = await self._set_qq_online_presence(mode)
        if custom_note:
            note = f"{note}；{custom_note}" if note else custom_note
        state["detail_key"] = key
        state["date"] = _today_key()
        state["plan_date"] = str(self.data.get("detail_enhanced_day") or "")
        state["mode"] = mode
        state["custom_text"] = custom_text
        state["reason"] = _single_line(status.get("reason"), 80)
        state["updated_at"] = _now_ts()
        state["ok"] = bool(ok)
        state["note"] = _single_line(note, 120)
        state["managed_by_plugin"] = True
        self._save_daily_state_sections({"qq_presence_state"})

    async def _ensure_current_detail_presence_status(self) -> None:
        plan = self.data.get("daily_plan", {})
        if not isinstance(plan, dict) or str(plan.get("date") or "") != _today_key():
            return
        enhanced = self.data.get("detail_enhanced_segments", {})
        if not isinstance(enhanced, dict):
            return
        segment = self._current_detail_segment_for_update()
        if not segment:
            return
        snapshot = enhanced.get(str(segment.get("key") or ""))
        if not isinstance(snapshot, dict) or snapshot.get("status") != "done":
            if self._refresh_daily_state_location_from_plan(plan=plan, segment=segment):
                self._save_daily_state_sections({"daily_state"})
            if self.enable_qq_presence_sync:
                # Clear a previous plugin-managed segment status while the
                # new segment is still being generated. The completed detail
                # will apply its own status when it becomes available.
                await self._apply_detail_presence_status(segment, {})
            return
        if self._refresh_daily_state_location_from_plan(plan=plan, detail=snapshot, segment=segment):
            self._save_daily_state_sections({"daily_state"})
        if not self.enable_qq_presence_sync:
            return
        await self._apply_detail_presence_status(segment, snapshot)

    def _balance_proactive_events_for_day(
        self,
        events: list[dict[str, Any]],
        *,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        prepared: list[dict[str, Any]] = []
        for raw in events:
            if not isinstance(raw, dict):
                continue
            item = dict(raw)
            if str(item.get("reason") or "") == "state_share":
                item["reason"] = "quiet_care"
            for key, fallback in (
                ("topic", "短短说一句"),
                ("why", "生活里刚好空出一点缝隙"),
                ("motive", "刚好停了一下，想短短说一句"),
                ("impulse", "想短短说一句"),
            ):
                item[key] = _single_line(item.get(key), 100) or fallback
            if str(item.get("action") or "message") == "message":
                item["action"] = self._preferred_action_for_story_event(item)
            prepared.append(item)
        if not prepared:
            return []
        ordered = sorted(prepared, key=self._story_plan_item_sort_key)
        buckets = ["morning", "noon", "afternoon", "evening", "late_night"]
        by_bucket: dict[str, list[dict[str, Any]]] = {bucket: [] for bucket in buckets}
        for item in ordered:
            bucket = self._proactive_daypart_bucket_for_event(item)
            if bucket in by_bucket:
                by_bucket[bucket].append(item)
        selected: list[dict[str, Any]] = []
        seen: set[tuple[Any, ...]] = set()

        def add(item: dict[str, Any]) -> None:
            if len(selected) >= limit:
                return
            identity = self._story_plan_item_identity("proactive_events", item)
            if identity in seen:
                return
            seen.add(identity)
            selected.append(item)

        for bucket in buckets:
            if by_bucket[bucket]:
                add(by_bucket[bucket][0])
        for bucket in buckets:
            cap = 1 if bucket == "late_night" else 2
            count = sum(1 for item in selected if self._proactive_daypart_bucket_for_event(item) == bucket)
            for item in by_bucket[bucket][1:]:
                if count >= cap:
                    break
                add(item)
                count += 1
        remaining = sorted(
            ordered,
            key=lambda item: (
                0 if str(item.get("action") or "message") != "message" else 1,
                self._event_priority(item),
                self._story_plan_item_sort_key(item),
            ),
        )
        for item in remaining:
            if len(selected) >= limit:
                break
            add(item)
        return sorted(selected, key=self._story_plan_item_sort_key)

    def _preferred_action_for_story_event(self, event: dict[str, Any]) -> str:
        reason = str(event.get("reason") or "check_in")
        text = " ".join(
            _single_line(event.get(key), 80)
            for key in ("topic", "why", "scene", "motive", "impulse")
        )
        if self._photo_text_available() and (
            reason in {"activity_share", "diary_share", "background_schedule", "noon_greeting", "evening_greeting"}
            or any(token in text for token in self._visual_share_tokens())
        ):
            return "photo_text"
        if self._screen_glance_available() and reason in {"check_in", "quiet_care", "background_schedule"}:
            return "screen_peek"
        if self._voice_available() and reason in {"quiet_care", "diary_share", "insomnia_night", "evening_greeting"}:
            return "voice"
        if self._poke_available() and reason in {"check_in", "quiet_care", "morning_greeting", "evening_greeting"}:
            return "poke"
        return "message"

    def _generate_morning_linked_proactive_events(self) -> list[dict[str, Any]]:
        state = self.data.get("daily_state", {})
        if not isinstance(state, dict):
            return []
        sleep_text = str(state.get("sleep") or "")
        conditions = state.get("conditions", [])
        if not isinstance(conditions, list):
            conditions = []

        morning_start, morning_end = 8 * 60 + 20, 9 * 60 + 50
        window_getter = getattr(self, "_morning_greeting_window", None)
        if callable(window_getter):
            try:
                candidate_start, candidate_end = window_getter()
                if 0 <= candidate_start < candidate_end <= 24 * 60:
                    morning_start, morning_end = candidate_start, candidate_end
            except Exception:
                pass

        def morning_window(*, delay_minutes: int, span_minutes: int) -> str:
            latest_start = max(morning_start, morning_end - 8)
            start = min(morning_start + max(0, delay_minutes), latest_start)
            end = min(morning_end, max(start + 8, start + max(8, span_minutes)))
            return f"{start // 60:02d}:{start % 60:02d}-{end // 60:02d}:{end % 60:02d}"

        events: list[dict[str, Any]] = []
        if any(token in sleep_text for token in ("赖床", "闹钟", "起得有点迟", "还没完全开机", "懵懵", "有点懵")):
            events.append(
                {
                    "window": morning_window(delay_minutes=8, span_minutes=30),
                    "reason": "morning_greeting",
                    "action": "message",
                    "why": "迷迷糊糊醒来，虽然还想再睡，但先轻轻说声早安",
                    "topic": "赖床间隙的早安",
                    "motive": "迷迷糊糊醒来，虽然还想再睡，但先轻轻说声早安",
                    "scene": "睡意依旧，不想起床",
                    "tone": "迷糊",
                    "impulse": "虽然打算继续睡，但想轻轻说声早安",
                    "chain": [
                        {"kind": "name_only_opener"},
                        {"kind": "if_no_reply", "after_minutes": 80, "reason": "check_in", "topic": "赖床醒来", "motive": "回笼觉结束，看看用户是先醒了还是依旧在睡", "tone": "耐心等待"},
                        {"kind": "if_still_no_reply", "after_minutes": 140, "reason": "morning_greeting", "topic": "催用户起床", "motive": "用户依旧没有回应你的消息，该催用户起床了", "tone": "调侃"},
                    ],
                    "mood": "迷糊",
                }
            )
        elif any(token in sleep_text for token in ("睡得很浅", "半夜醒", "一晚上都在做梦", "失眠")):
            events.append(
                {
                    "window": morning_window(delay_minutes=6, span_minutes=28),
                    "reason": "morning_greeting",
                    "action": "message",
                    "why": "醒来还带着一点睡意时,迷迷糊糊先发一声早安。",
                    "topic": "没完全醒的早安",
                    "motive": "人还没完全清醒,但还是先想打个招呼",
                    "scene": "人还带着睡意的时候",
                    "tone": "迟钝",
                    "impulse": "想轻轻说声早安",
                    "chain": [
                        {"kind": "name_only_opener"},
                        {"kind": "if_no_reply", "after_minutes": 90, "reason": "check_in", "topic": "早安余韵", "motive": "已经清醒过来，但刚刚和用户说的早安还没得到回应,猜测用户还在休息", "tone": "耐心等待"},
                        {"kind": "if_still_no_reply", "after_minutes": 150, "reason": "morning_greeting", "topic": "催用户起床", "motive": "用户依旧没有回应你的消息，该催用户起床了", "tone": "调侃"},
                    ],
                    "mood": "迟钝",
                }
            )
        energy = _safe_int(state.get("energy"), 70, 0, 100)
        if energy >= 62 and random.random() < 0.45:
            events.append(
                {
                    "window": morning_window(delay_minutes=3, span_minutes=24),
                    "reason": "morning_greeting",
                    "action": "message",
                    "why": "睡得很好,习惯性地想去打个招呼。",
                    "topic": "早安",
                    "motive": "昨晚睡得很好，刚醒来就去和用户打个招呼",
                    "scene": "刚从床上爬起来的时候",
                    "tone": "清爽",
                    "impulse": "想轻轻说早安",
                    "chain": [
                        {"kind": "name_only_opener"},
                        {"kind": "if_no_reply", "after_minutes": 85, "reason": "check_in", "topic": "早安余韵", "motive": "刚刚和用户说了早安但没得到回应,猜测用户还在休息", "tone": "耐心等待"},
                        {"kind": "if_still_no_reply", "after_minutes": 145, "reason": "morning_greeting", "topic": "催用户起床", "motive": "用户依旧没有回应你的消息，该催用户起床了", "tone": "调侃"},
                    ],
                    "mood": "清爽",
                }
            )
        for cond in conditions:
            if not isinstance(cond, dict):
                continue
            title = str(cond.get("title") or "")
            label = str(cond.get("label") or "")
            if "睡眠延续" in title and random.random() < 0.55:
                events.append(
                    {
                        "window": morning_window(delay_minutes=10, span_minutes=30),
                        "reason": "morning_greeting",
                        "action": "message",
                        "why": "睡意延续到白天,有种半梦半醒的感觉",
                        "topic": "刚醒来后脑子晕乎乎的",
                        "motive": "依旧带着睡意的早安问候",
                        "scene": "依旧带着睡意",
                        "tone": "半梦半醒",
                        "impulse": "醒来迷迷糊糊的，想轻轻说早安",
                        "mood": _single_line(cond.get("mood"), 20) or "迟钝",
                    }
                )
                break
            if any(token in label for token in ("赖床", "闹钟", "起得有点迟")):
                events.append(
                    {
                        "window": morning_window(delay_minutes=6, span_minutes=32),
                        "reason": "morning_greeting",
                        "action": "message",
                        "why": "早晨发生了一点生活小插曲，和用户抱怨一句或打个招呼。",
                        "topic": "早晨的生活小插曲",
                        "motive": "早上折腾了一下,想来找你吐个小槽",
                        "scene": "被早晨的小事故折腾了一下之后",
                        "tone": "迷糊又有点乱",
                        "impulse": "想顺手分享早上的生活小插曲",
                        "mood": "迷糊",
                    }
                )
                break
        return events[:2]

    def _generate_daypart_linked_proactive_events(self) -> list[dict[str, Any]]:
        state = self.data.get("daily_state", {})
        if not isinstance(state, dict):
            return []
        weather = self._weather_summary_text(self.data.get("daily_weather", {}))
        energy = _safe_int(state.get("energy"), 70, 0, 100)
        sleep_text = str(state.get("sleep") or "")
        events: list[dict[str, Any]] = []
        if 36 <= energy <= 68 and random.random() < 0.58:
            events.append(
                {
                    "window": "12:10-13:30",
                    "reason": "noon_greeting",
                    "action": "message",
                    "why": "中午有些犯困，想短短打声招呼。",
                    "topic": "午后犯困",
                    "motive": "中午这会儿有点犯困，想短短说句话",
                    "scene": "午后犯困的时候",
                    "tone": "懒洋洋",
                    "impulse": "想趁午后休息时短短说一句",
                    "mood": "懒洋洋",
                }
            )
        if any(token in weather for token in ("晚霞", "晴", "阳光", "多云")) and random.random() < 0.52:
            events.append(
                {
                    "window": "17:20-19:10",
                    "reason": "activity_share",
                    "action": "photo_text" if self._photo_text_available() else "message",
                    "why": "傍晚天色好看时，想拍一张路上的画面给你看。",
                    "topic": "傍晚路上",
                    "motive": "傍晚路上的天色很好看，想拍给你看看",
                    "scene": "傍晚走在路上时",
                    "tone": "松弛",
                    "impulse": "想顺手分享傍晚路上的画面",
                    "mood": "松弛",
                }
            )
        if 45 <= energy <= 82 and random.random() < 0.46:
            events.append(
                {
                    "window": "15:20-17:10",
                    "reason": "check_in",
                    "action": "message",
                    "why": "下午短暂休息时，想轻轻问一句用户那边怎么样。",
                    "topic": "下午短暂休息",
                    "motive": "下午节奏缓下来一点，想看看用户是不是也能休息一下",
                    "scene": "下午短暂休息的时候",
                    "tone": "平静",
                    "impulse": "好奇用户在做什么",
                    "mood": "微松",
                }
            )
        if random.random() < 0.48:
            topic = self._pick_life_thought_topic("activity_share")
            action = "photo_text" if self._photo_text_available() and random.random() < 0.16 else "message"
            events.append(
                {
                    "window": "14:40-18:40" if 12 <= self._environment_now().hour < 18 else "19:20-21:40",
                    "reason": "activity_share",
                    "action": action,
                    "why": "日常里突然冒出一个小想法，想短短说一句。",
                    "topic": topic,
                    "motive": f"刚刚想到“{topic}”，想顺手分享一下",
                    "scene": "闲下来的时候",
                    "tone": "自然",
                    "impulse": "想把刚冒出来的小想法顺口提一下",
                    "mood": "微妙",
                }
            )
        if any(token in sleep_text for token in ("失眠", "睡得很浅", "半夜醒", "一晚上都在做梦")) and random.random() < 0.5:
            events.append(
                {
                    "window": "22:10-23:25",
                    "reason": "quiet_care",
                    "action": "message",
                    "why": "睡前还没完全困下来，想随便聊两句",
                    "topic": "睡前还没困下来",
                    "motive": "明明快该睡了，但还是想找用户说说话",
                    "scene": "准备睡觉但还没困下来的时候",
                    "tone": "平静",
                    "impulse": "想在睡前和用户聊天",
                    "mood": "安静",
                    "conversation_posture": "closing",
                }
            )
        if energy < 42 and random.random() < 0.42:
            events.append(
                {
                    "window": "19:40-21:10",
                    "reason": "quiet_care",
                    "action": "message",
                    "why": "累了一天之后，想在睡前和用户聊聊天",
                    "topic": "一天快结束时",
                    "motive": "今天快结束了，睡前想聊两句",
                    "scene": "一天快结束的时候",
                    "tone": "疲惫",
                    "impulse": "想在睡前和用户聊天",
                    "mood": "疲惫",
                    "conversation_posture": "closing",
                }
            )
        return events[:3]

    def _normalize_event_motive(self, item: dict[str, Any]) -> str:
        direct = _single_line(item.get("motive"), 80)
        if direct:
            return self._normalize_internal_motive_text(direct)
        reason = _single_line(item.get("reason"), 40)
        action = _single_line(item.get("action"), 20)
        topic = _single_line(item.get("topic"), 50)
        why = _single_line(item.get("why"), 80)
        scene = _single_line(item.get("scene"), 60)
        tone = _single_line(item.get("tone"), 24)
        impulse = _single_line(item.get("impulse"), 80)
        if impulse:
            return self._normalize_internal_motive_text(impulse)
        base = {
            "insomnia_night": "夜里还没睡着，想短短留一句",
            "state_share": "当前状态有变化,想让你知道",
            "quiet_care": "想到用户，想确认一下用户那边怎么样",
            "activity_share": "遇到一段可以分享的日常内容",
            "diary_share": "整理今日记录时想到可以分享",
            "important_date_share": "有个重要时间点值得提前提醒",
            "background_schedule": "当前日程有一点可以自然提到",
            "check_in": "刚好停下来,想看看那边有没有空",
            "morning_greeting": "早上这会儿想先把一句招呼放过去",
            "noon_greeting": "中午松下来时想短短说一句",
            "evening_greeting": "晚上慢下来时想先来你这边说一句",
        }.get(reason, "刚好停下来,想到可以短短说一句")
        if action == "screen_peek":
            base = "刚好有点空，想看看那边是不是还在忙"
        elif action == "photo_text":
            base = "刚刚看到的画面想分享一下"
        elif action == "poke":
            base = "想做一次轻量提醒"
        elif action == "voice":
            base = "这会儿更适合用语音表达"
        if topic and any(token in topic for token in ("日记", "笔记", "碎片", "念头", "半句", "想法")):
            base = "整理记录时发现一段适合分享的内容"
        elif topic and any(token in topic for token in self._visual_share_tokens()):
            base = "眼前有个具体小画面适合顺手分享"
        elif topic and any(token in topic for token in ("雨", "天气", "晚霞", "阳光")):
            base = "当前天气内容适合分享"
        elif why and len(why) <= 30:
            base = why
        if scene and tone:
            base = f"{scene}里有个可以自然提到的小切口"
        elif scene:
            base = f"{scene}里有个可以自然提到的小切口"
        elif tone and not topic:
            base = "这会儿适合短短说一句,状态只留在语气里"
        return self._normalize_internal_motive_text(_single_line(base, 80))

    def _dedupe_proactive_events(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        deduped: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in events:
            if not isinstance(item, dict):
                continue
            key = "|".join(
                [
                    _single_line(item.get("window"), 20),
                    _single_line(item.get("reason"), 40),
                    _single_line(item.get("action"), 20),
                    _single_line(item.get("topic"), 80),
                ]
            )
            if not key or key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped

    def _proactive_topic_signature(self, *parts: Any) -> str:
        normalized_parts: list[str] = []
        address_prefix = re.compile(
            r"^(?:[嗯唔哦噢诶欸啊呀哎嘿嗨]+[。！？!?…~～，,\s]*)?"
            r"(?:[\w\u4e00-\u9fffぁ-んァ-ヶー]{1,10}(?:大人|老师|主人|哥哥|姐姐|同学|宝宝|宝贝)"
            r"[，,、：:\s~～…]*|(?!(?:今天|现在|刚才|刚刚|这会儿|早上|中午|晚上|外面|天气|最近|等下|待会)[，,、：:])"
            r"[\w\u4e00-\u9fffぁ-んァ-ヶー]{1,3}[，,、：:]\s*)"
        )
        for part in parts:
            value = _single_line(part, 160)
            if not value:
                continue
            # 收件人称呼不是主题。先去掉句首称呼，避免不同内容仅因反复称呼
            # 同一用户而被误判为重复。
            normalized_parts.append(address_prefix.sub("", value, count=1).strip() or value)
        text = " ".join(normalized_parts)
        if not text:
            return ""
        school_stress_markers = (
            "上课", "课", "物理", "老师", "点名", "叫上去", "做题", "抓到",
            "发呆", "心跳", "紧张", "差点", "讲台",
        )
        if sum(1 for token in school_stress_markers if token in text) >= 2:
            return "school_class_anxiety"
        food_markers = ("食堂", "午饭", "中午", "菜", "咸", "吃")
        if sum(1 for token in food_markers if token in text) >= 2:
            return "noon_food_share"
        weather_markers = (
            "外面下雨", "外面下雪", "天气", "天晴", "晴吗", "晴天", "下雨", "没下雨",
            "雨声", "雨停", "雨雪停", "小雨", "中雨", "大雨", "阵雨", "雷雨", "雷暴", "降雨",
            "阴天", "天阴", "阴阴", "多云", "放晴", "太阳", "阳光", "晚霞", "天色", "气温",
            "降温", "升温", "起风", "风声", "下雪", "雪天", "雾霾",
        )
        if any(token in text for token in weather_markers):
            # 普通天气换一种说法仍是同一个主动话题。结构化预警和实时
            # 环境变化在候选层按事件指纹去重，不依赖这里放行。
            return "ordinary_weather_topic"
        image_markers = ("图", "图片", "照片", "拍", "自拍", "画面")
        if sum(1 for token in image_markers if token in text) >= 2:
            return "photo_share"
        tokens = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9_]{3,}", text)
        stopwords = {
            "刚才", "现在", "今天", "这个", "那个", "一下", "一点", "有点", "还是",
            "没有", "已经", "时候", "用户", "对方", "主动", "消息", "这会儿",
            "内容", "第一", "时间", "看到", "喜欢", "希望", "继续", "话题", "换个",
            "说过", "讲过", "提过", "聊过", "发过", "前面", "之前", "刚刚",
        }
        kept: list[str] = []
        def add_anchor(value: str) -> None:
            anchor = str(value or "").strip()
            if len(anchor) < 2 or anchor in stopwords:
                return
            if re.fullmatch(r"[了啦呀呢嘛吗吧啊哦噢诶嗯]+", anchor):
                return
            if re.fullmatch(r"[年月日点分秒上下左右前后早晚中午今晚昨今明]+", anchor):
                return
            if anchor not in kept:
                kept.append(anchor)

        for token in tokens:
            if re.fullmatch(r"[A-Za-z0-9_]{3,}", token):
                add_anchor(token.lower())
                continue
            cleaned = re.sub(r"(的时候|时候|一下|一点|了|啦|呀|呢|嘛|吗|吧|啊|哦|噢|诶|嗯)$", "", token)
            add_anchor(cleaned)
            if len(cleaned) >= 3:
                for size in (2, 3):
                    for index in range(0, max(0, len(cleaned) - size + 1)):
                        add_anchor(cleaned[index : index + size])
        return "|".join(kept)

    def _cleanup_recent_proactive_topics(self, user: dict[str, Any], *, now: float | None = None) -> list[dict[str, Any]]:
        now = now or _now_ts()
        raw = user.get("recent_proactive_topics", [])
        if not isinstance(raw, list):
            raw = []
        meta_leak_checker = getattr(self, "_framework_agent_meta_summary_leak", None)
        kept: list[dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            signature = str(item.get("signature") or "")
            visible_text = _single_line(item.get("text"), 240)
            derived_signature = self._proactive_topic_signature(visible_text) if visible_text else ""
            if signature == "morning_weather_check" or derived_signature == "ordinary_weather_topic":
                signature = "ordinary_weather_topic"
                item["signature"] = signature
            if signature == "ordinary_weather_topic":
                configured_minutes = self._proactive_dedup_window_minutes("weather", 1080)
                retention = 30 * 24 * 3600 if configured_minutes <= 0 else max(18 * 3600, configured_minutes * 60)
            else:
                configured_minutes = self._proactive_dedup_window_minutes("sent", 240)
                retention = 30 * 24 * 3600 if configured_minutes <= 0 else max(6 * 3600, configured_minutes * 60)
            if now - _safe_float(item.get("ts"), 0) > retention:
                continue
            if callable(meta_leak_checker) and (
                meta_leak_checker(str(item.get("text") or ""))
                or meta_leak_checker(str(item.get("signature") or ""))
            ):
                continue
            kept.append(item)
        user["recent_proactive_topics"] = kept[-12:]
        return user["recent_proactive_topics"]

    def _proactive_dedup_window_minutes(self, kind: str, default: int) -> int:
        key = (
            "proactive_dedup_weather_window_minutes"
            if kind == "weather"
            else "proactive_dedup_last_message_window_minutes"
            if kind == "last_message"
            else "proactive_dedup_sent_window_minutes"
        )
        raw = runtime_persona_setting(self, key, default)
        try:
            return max(0, int(raw))
        except (TypeError, ValueError):
            return max(0, int(default))

    @staticmethod
    def _proactive_dedup_age_allowed(age: float, window_minutes: int) -> bool:
        return window_minutes <= 0 or age <= window_minutes * 60

    def _topic_signature_similar(self, left: str, right: str, *, use_proactive_dedup_config: bool = False) -> bool:
        if not left or not right:
            return False
        if left == right:
            return True
        left_set = {part for part in left.split("|") if part}
        right_set = {part for part in right.split("|") if part}
        if not left_set or not right_set:
            return False
        common = left_set & right_set
        smaller_size = min(len(left_set), len(right_set))
        min_shared_tokens = 1
        overlap_floor = 0.0
        if use_proactive_dedup_config:
            try:
                min_shared_tokens = max(1, min(4, int(runtime_persona_setting(self, "proactive_dedup_min_shared_tokens", 1))))
            except (TypeError, ValueError):
                min_shared_tokens = 1
            try:
                overlap_floor = max(0.0, min(1.0, float(runtime_persona_setting(self, "proactive_dedup_min_overlap_ratio", 0.0))))
            except (TypeError, ValueError):
                overlap_floor = 0.0
        if smaller_size <= 2:
            return len(common) >= min_shared_tokens
        overlap = len(common) / smaller_size
        return bool(
            (len(common) >= 2 and overlap >= max(0.5, overlap_floor))
            or (len(common) >= 3 and overlap >= max(0.3, overlap_floor))
            or (len(common) >= 4 and overlap >= max(0.18, overlap_floor))
            or (len(common) >= 6 and overlap >= overlap_floor)
        )

    def _recent_proactive_topic_repeated(self, user: dict[str, Any], signature: str, *, now: float | None = None) -> bool:
        if not signature:
            return False
        check_now = now or _now_ts()
        for item in self._cleanup_recent_proactive_topics(user, now=check_now):
            item_signature = str(item.get("signature") or "")
            is_weather = signature == "ordinary_weather_topic" or item_signature == "ordinary_weather_topic"
            window_minutes = self._proactive_dedup_window_minutes(
                "weather" if is_weather else "sent",
                1080 if is_weather else 240,
            )
            if not self._proactive_dedup_age_allowed(check_now - _safe_float(item.get("ts"), 0), window_minutes):
                continue
            if self._topic_signature_similar(signature, item_signature, use_proactive_dedup_config=True):
                return True
        return False

    def _remember_proactive_topic(self, user: dict[str, Any], *, text: str = "", topic: str = "", motive: str = "") -> None:
        meta_leak_checker = getattr(self, "_framework_agent_meta_summary_leak", None)
        if callable(meta_leak_checker) and (
            meta_leak_checker(text) or meta_leak_checker(topic) or meta_leak_checker(motive)
        ):
            logger.warning("跳过记录疑似工具循环摘要的主动话题记忆")
            return
        signature = self._proactive_topic_signature(text, topic, motive)
        if not signature:
            return
        recent = self._cleanup_recent_proactive_topics(user)
        recent.append(
            {
                "ts": _now_ts(),
                "signature": signature,
                "text": _single_line(text or topic or motive, 120),
            }
        )
        del recent[:-12]

    def _proactive_dedup_enabled_policies(self) -> frozenset[str]:
        raw_value = runtime_persona_setting(self, "proactive_dedup_policies", None)
        if raw_value is None:
            return frozenset({"semantic", "content_fingerprint", "life_event"})
        raw = str(raw_value).strip().lower()
        return frozenset(part for part in re.split(r"[,，;；\s]+", raw) if part)

    def _recent_proactive_text_duplicate_reason(
        self,
        user: dict[str, Any],
        *,
        text: str = "",
        topic: str = "",
        motive: str = "",
        now: float | None = None,
    ) -> str:
        if not bool(runtime_persona_setting(self, "proactive_dedup_enabled", True)):
            return ""
        signature = self._proactive_topic_signature(text, topic, motive)
        if not signature:
            return ""
        check_now = now or _now_ts()
        for item in self._cleanup_recent_proactive_topics(user, now=check_now):
            old_signature = str(item.get("signature") or "")
            if not self._topic_signature_similar(signature, old_signature, use_proactive_dedup_config=True):
                continue
            age = check_now - _safe_float(item.get("ts"), 0)
            duplicate_window = self._proactive_dedup_window_minutes(
                "weather" if signature == "ordinary_weather_topic" else "sent",
                1080 if signature == "ordinary_weather_topic" else 240,
            )
            if not self._proactive_dedup_age_allowed(age, duplicate_window):
                continue
            old_text = _single_line(item.get("text"), 80)
            if signature == "ordinary_weather_topic":
                return f"近期已经主动聊过天气" + (f"：{old_text}" if old_text else "")
            return f"近 {max(1, int(age // 60))} 分钟已发送相似主动" + (f"：{old_text}" if old_text else "")
        last_message = _single_line(_strip_internal_message_blocks(user.get("last_companion_message"), enabled=bool(runtime_persona_setting(self, "enable_framework_error_leak_guard", True))), 500)
        # last_reply_at is inbound user activity, so it must never make an old
        # companion message look newly delivered.
        last_at = _safe_float(user.get("last_companion_message_at"), 0)
        sending_started_at = _safe_float(user.get("proactive_sending_started_at"), 0)
        unconfirmed_current_candidate = bool(
            sending_started_at > 0
            and last_at >= sending_started_at
            and user.get("proactive_sending")
        )
        if (
            bool(runtime_persona_setting(self, "proactive_dedup_last_message_enabled", True))
            and not unconfirmed_current_candidate
            and last_message
            and last_at > 0
            and self._proactive_dedup_age_allowed(
                check_now - last_at,
                self._proactive_dedup_window_minutes("last_message", 240),
            )
        ):
            last_signature = self._proactive_topic_signature(last_message)
            if self._topic_signature_similar(signature, last_signature, use_proactive_dedup_config=True):
                age = check_now - last_at
                return f"近 {max(1, int(age // 60))} 分钟聊天里已经说过相似内容：{_single_line(last_message, 80)}"
        return ""

    def _pending_proactive_send_retry(self, user: dict[str, Any], *, now: float | None = None) -> dict[str, Any] | None:
        payload = user.get("pending_proactive_send_retry") if isinstance(user, dict) else None
        if not isinstance(payload, dict) or not payload.get("active"):
            return None
        current = _now_ts() if now is None else float(now)
        if _safe_float(payload.get("expires_at"), 0) <= current:
            self._clear_pending_proactive_send_retry(user)
            return None
        delivery_key_getter = getattr(self, "_planned_proactive_delivery_key", None)
        current_delivery_key = delivery_key_getter(user) if callable(delivery_key_getter) else ""
        retry_delivery_key = _single_line(payload.get("delivery_key"), 80)
        retry_freshness = _single_line(payload.get("freshness"), 24)
        retry_profile = _single_line(payload.get("route_retry_profile"), 32) or "normal"
        cancel_if_new_inbound = bool(payload.get("route_cancel_if_new_inbound", True))
        retry_fresh_until = _safe_float(payload.get("fresh_until_at"), 0)
        retry_activity_at = _safe_float(payload.get("private_activity_at"), 0)
        retry_inbound_count = _safe_int(payload.get("private_inbound_count"), 0)
        current_activity_at = self._latest_private_user_activity_ts(user)
        current_inbound_count = _safe_int(user.get("private_inbound_count"), 0)
        if (
            not retry_delivery_key
            or retry_delivery_key != current_delivery_key
            or (retry_profile == "normal" and retry_freshness != "durable")
            or retry_fresh_until <= current
            or (
                cancel_if_new_inbound
                and (current_activity_at > retry_activity_at or current_inbound_count > retry_inbound_count)
            )
        ):
            self._clear_pending_proactive_send_retry(user)
            return None
        image_path = str(payload.get("image_path") or "").strip()
        text = _single_line(payload.get("text"), 1200)
        validator = getattr(self, "_validate_proactive_outbound_candidate", None)
        if callable(validator):
            try:
                validation = validator(
                    text,
                    image_path=image_path,
                    reason=_single_line(payload.get("reason"), 40),
                    action=_single_line(payload.get("action"), 40),
                    source="retry_load",
                )
            except Exception:
                validation = {"decision": "send", "text": text}
            decision = str(validation.get("decision") or "send")
            if decision == "drop":
                self._clear_pending_proactive_send_retry(user)
                return None
            if decision == "rewrite":
                text = _single_line(validation.get("text"), 1200)
                payload["text"] = text
        if image_path and not re.match(r"^(?:https?://|file://|data:)", image_path, flags=re.I):
            try:
                if not Path(image_path).exists():
                    self._clear_pending_proactive_send_retry(user)
                    return None
            except Exception:
                self._clear_pending_proactive_send_retry(user)
                return None
        if not text and not image_path:
            self._clear_pending_proactive_send_retry(user)
            return None
        return payload

    def _clear_pending_proactive_send_retry(self, user: dict[str, Any]) -> None:
        if isinstance(user, dict):
            user["pending_proactive_send_retry"] = {}

    def _abandon_failed_proactive_retry_candidate(
        self,
        user: dict[str, Any],
        *,
        note: str,
        now: float,
        delay_hours: tuple[float, float],
    ) -> None:
        self._clear_pending_proactive_send_retry(user)
        self._mark_planned_candidate_status(user, "dropped", note)
        self._clear_pending_proactive_plan(user)
        self._schedule_next_proactive(user, now=now, delay_hours=delay_hours)

    def _store_or_advance_proactive_send_retry(
        self,
        user: dict[str, Any],
        *,
        text: str,
        image_path: str,
        extra_components: list[Any],
        reason: str,
        action: str,
        action_summary: str,
        error_text: str,
        photo_subject_owner: str = "",
        now: float | None = None,
    ) -> str:
        if not isinstance(user, dict):
            return "无法保存待重发内容"
        current = _now_ts() if now is None else float(now)
        delivery_snapshot_getter = getattr(self, "_ensure_planned_proactive_delivery_state", None)
        delivery_snapshot = delivery_snapshot_getter(user, now=current) if callable(delivery_snapshot_getter) else {}
        freshness = _single_line(delivery_snapshot.get("freshness"), 24) if isinstance(delivery_snapshot, dict) else ""
        delivery_key = _single_line(delivery_snapshot.get("key"), 80) if isinstance(delivery_snapshot, dict) else ""
        existing = user.get("pending_proactive_send_retry")
        previous_count = _safe_int(existing.get("retry_count"), 0, 0, 10) if isinstance(existing, dict) else 0
        retry_count = previous_count + 1
        retry_profile = _single_line(user.get("planned_proactive_route_retry_profile"), 32) or "normal"
        retry_limit = 4 if retry_profile == "until_expiry" else 2
        clean_error = _single_line(error_text, 180)
        error_hint = ""
        if clean_error:
            compact_error = clean_error.lower()
            if "retcode=1200" in compact_error and "eventchecker" in compact_error:
                error_hint = "QQ/NTQQ 拒绝发送（目标当前不可私聊或客户端临时异常）"
            elif "timeout" in compact_error:
                error_hint = "平台发送超时"
            elif "actionfailed" in compact_error or "failed" in compact_error:
                error_hint = "平台发送失败"
            else:
                error_hint = clean_error
        if retry_count > retry_limit:
            self._abandon_failed_proactive_retry_candidate(
                user,
                note="发送失败，待重发内容连续失败，已放弃复用并重新排程",
                now=current,
                delay_hours=(12, 24),
            )
            return "发送失败，待重发内容连续失败，已放弃复用并重新排程" + (f"；原因：{error_hint}" if error_hint else "")
        if (freshness != "durable" and retry_profile == "normal") or not delivery_key:
            self._abandon_failed_proactive_retry_candidate(
                user,
                note="发送失败，当前候选依赖即时语境，已放弃复用并重新编排",
                now=current,
                delay_hours=(1.5, 4.0),
            )
            return "发送失败，当前候选依赖即时语境，已放弃复用并重新编排" + (f"；原因：{error_hint}" if error_hint else "")
        if extra_components:
            self._abandon_failed_proactive_retry_candidate(
                user,
                note="发送失败，包含复杂组件，已放弃复用并重新排程",
                now=current,
                delay_hours=(6, 12),
            )
            return "发送失败，包含复杂组件，未缓存待重发内容，已延后重新排程" + (f"；原因：{error_hint}" if error_hint else "")
        clean_text = _single_line(text, 1200)
        clean_image = _path_text(image_path, 1000)
        if not clean_text and not clean_image:
            self._abandon_failed_proactive_retry_candidate(
                user,
                note="发送失败，无可复用内容，已放弃复用并重新排程",
                now=current,
                delay_hours=(6, 12),
            )
            return "发送失败，无可复用内容，已延后重新排程" + (f"；原因：{error_hint}" if error_hint else "")
        validator = getattr(self, "_validate_proactive_outbound_candidate", None)
        unsafe_retry_text = False
        if callable(validator):
            try:
                validation = validator(
                    clean_text,
                    image_path=clean_image,
                    extra_components=extra_components,
                    reason=reason,
                    action=action,
                    source="retry_store",
                )
            except Exception:
                validation = {"decision": "send", "text": clean_text}
            decision = str(validation.get("decision") or "send")
            if decision == "drop":
                unsafe_retry_text = True
            elif decision == "rewrite":
                clean_text = _single_line(validation.get("text"), 1200)
        else:
            meta_leak_checker = getattr(self, "_framework_agent_meta_summary_leak", None)
            instruction_leak_checker = getattr(self, "_is_proactive_instruction_leak_text", None)
            try:
                unsafe_retry_text = bool(clean_text) and (
                    (callable(meta_leak_checker) and meta_leak_checker(clean_text))
                    or (callable(instruction_leak_checker) and instruction_leak_checker(clean_text))
                    or self._is_proactive_delivery_receipt_text(clean_text)
                )
            except Exception:
                unsafe_retry_text = False
        if unsafe_retry_text:
            self._abandon_failed_proactive_retry_candidate(
                user,
                note="发送失败，候选正文疑似内部提示词/执行指令泄漏，已放弃复用并重新排程",
                now=current,
                delay_hours=(2, 6),
            )
            return "发送失败，候选正文疑似内部提示词/执行指令泄漏，已放弃复用并重新排程" + (f"；原因：{error_hint}" if error_hint else "")
        if not clean_text and not clean_image:
            self._abandon_failed_proactive_retry_candidate(
                user,
                note="发送失败，清理后无可复用内容，已放弃复用并重新排程",
                now=current,
                delay_hours=(6, 12),
            )
            return "发送失败，清理后无可复用内容，已延后重新排程" + (f"；原因：{error_hint}" if error_hint else "")
        if retry_profile == "until_expiry":
            retry_delay_seconds = 3 * 60 if retry_count <= 1 else 8 * 60
        elif retry_profile == "short_lived":
            retry_delay_seconds = 2 * 60 if retry_count <= 1 else 5 * 60
        elif retry_profile == "while_anchor_live":
            retry_delay_seconds = 5 * 60 if retry_count <= 1 else 12 * 60
        else:
            retry_delay_seconds = 8 * 60 if retry_count <= 1 else 20 * 60
        planned_expire_at = _safe_float(delivery_snapshot.get("expire_at"), 0) if isinstance(delivery_snapshot, dict) else 0
        fresh_until_at = min(current + 72 * 3600, planned_expire_at) if planned_expire_at > current else current
        if fresh_until_at <= current + retry_delay_seconds:
            self._abandon_failed_proactive_retry_candidate(
                user,
                note="发送失败，候选在下一次重试前会失效，已放弃复用并重新编排",
                now=current,
                delay_hours=(1.5, 4.0),
            )
            return "发送失败，候选在下一次重试前会失效，已重新编排" + (f"；原因：{error_hint}" if error_hint else "")
        user["pending_proactive_send_retry"] = {
            "active": True,
            "created_at": _safe_float(existing.get("created_at"), current) if isinstance(existing, dict) else current,
            "updated_at": current,
            "expires_at": current + 72 * 3600,
            "fresh_until_at": fresh_until_at,
            "retry_count": retry_count,
            "text": clean_text,
            "image_path": clean_image,
            "reason": _single_line(reason, 40) or "check_in",
            "action": _single_line(action, 40) or "message",
            "action_summary": _single_line(action_summary, 500),
            "photo_subject_owner": _normalize_photo_subject_owner(photo_subject_owner),
            "last_error": clean_error,
            "delivery_key": delivery_key,
            "freshness": freshness,
            "route_retry_profile": retry_profile,
            "route_cancel_if_new_inbound": bool(
                user.get("planned_proactive_route_cancel_if_new_inbound", True)
            ),
            "private_activity_at": self._latest_private_user_activity_ts(user),
            "private_inbound_count": _safe_int(user.get("private_inbound_count"), 0),
        }
        user["next_proactive_at"] = current + retry_delay_seconds
        user["planned_proactive_window_start_at"] = user["next_proactive_at"]
        user["planned_proactive_delivery_state"] = "retrying"
        return f"发送失败，已保留待重发内容，约 {max(1, int(retry_delay_seconds // 60))} 分钟后第 {retry_count} 次重试" + (f"；原因：{error_hint}" if error_hint else "")

    def _activity_share_global_signature(self, user: dict[str, Any], *, text: str = "", action_summary: str = "") -> str:
        state = self.data.get("daily_state", {})
        current_item = self._get_current_plan_item(self.data.get("daily_plan", {}))
        parts: list[Any] = [
            user.get("planned_proactive_topic"),
            user.get("planned_proactive_motive"),
            action_summary,
        ]
        if isinstance(current_item, dict):
            parts.extend(
                [
                    current_item.get("time"),
                    current_item.get("activity"),
                    current_item.get("message_seed"),
                ]
            )
        if isinstance(state, dict):
            parts.extend(
                [
                    state.get("activity"),
                    state.get("current_activity"),
                    state.get("message_seed"),
                    state.get("mood_bias"),
                ]
            )
        parts.append(text)
        signature = self._proactive_topic_signature(*parts)
        if signature:
            return signature
        raw = " ".join(_single_line(part, 120) for part in parts if _single_line(part, 120))
        return hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:16] if raw else ""

    def _cleanup_global_activity_share_topics(self, *, now: float | None = None) -> list[dict[str, Any]]:
        check_now = now or _now_ts()
        runtime = self.data.setdefault("proactive_runtime", {})
        if not isinstance(runtime, dict):
            runtime = {}
            self.data["proactive_runtime"] = runtime
        raw = runtime.get("recent_activity_shares")
        if not isinstance(raw, list):
            raw = []
        kept = [
            item for item in raw
            if isinstance(item, dict) and check_now - _safe_float(item.get("ts"), 0) <= 90 * 60
        ]
        runtime["recent_activity_shares"] = kept[-12:]
        return runtime["recent_activity_shares"]

    def _activity_share_recently_sent_elsewhere(
        self,
        user_id: str,
        user: dict[str, Any],
        *,
        text: str = "",
        action_summary: str = "",
        now: float | None = None,
    ) -> str:
        signature = self._activity_share_global_signature(user, text=text, action_summary=action_summary)
        if not signature:
            return ""
        for item in self._cleanup_global_activity_share_topics(now=now):
            if str(item.get("user_id") or "") == str(user_id):
                continue
            if self._topic_signature_similar(signature, str(item.get("signature") or "")):
                return _single_line(item.get("text"), 80) or "同一日常碎片刚刚已分享给其他私聊对象"
        return ""

    def _remember_global_activity_share(
        self,
        user_id: str,
        user: dict[str, Any],
        *,
        text: str = "",
        action_summary: str = "",
    ) -> None:
        signature = self._activity_share_global_signature(user, text=text, action_summary=action_summary)
        if not signature:
            return
        recent = self._cleanup_global_activity_share_topics()
        recent.append(
            {
                "ts": _now_ts(),
                "user_id": str(user_id),
                "signature": signature,
                "text": _single_line(text or user.get("planned_proactive_topic") or user.get("planned_proactive_motive"), 120),
            }
        )
        del recent[:-12]

    def _activity_share_duplicate_block_remaining(self, user: dict[str, Any], *, now: float | None = None) -> float:
        check_now = now or _now_ts()
        until = _safe_float(user.get("activity_share_duplicate_block_until"), 0)
        return max(0.0, until - check_now)

    def _block_duplicate_activity_share_for_user(
        self,
        user: dict[str, Any],
        *,
        duplicate_note: str = "",
        now: float | None = None,
        seconds: float = 90 * 60,
    ) -> None:
        check_now = now or _now_ts()
        user["activity_share_duplicate_block_until"] = check_now + max(60.0, float(seconds or 0))
        user["activity_share_duplicate_block_note"] = _single_line(duplicate_note, 120)
        user["last_activity_share_duplicate_block_at"] = check_now

    def _format_recent_proactive_topics_hint(self, user: dict[str, Any]) -> str:
        recent = self._cleanup_recent_proactive_topics(user)
        if not recent:
            return ""
        lines: list[str] = []
        meta_leak_checker = getattr(self, "_framework_agent_meta_summary_leak", None)
        for item in recent[-4:]:
            text = _single_line(item.get("text"), 80)
            if not text:
                continue
            if callable(meta_leak_checker) and meta_leak_checker(text):
                continue
            when = self._format_timestamp_elapsed(item.get("ts"))
            lines.append(f"- {when}说过：{text}")
        if any(str(item.get("signature") or "") == "ordinary_weather_topic" for item in recent):
            lines.append("- 最近已经用天气开过话题；除非本轮原因是刚发生的环境突变或官方预警，否则这次不要再写天气、气温、下雨、天色，也不要追问对方那边的天气。")
        return "\n".join(lines)

    def _generate_weather_linked_proactive_events(self) -> list[dict[str, Any]]:
        weather = self._weather_summary_text(self.data.get("daily_weather", {}))
        if weather == "暂无天气信息":
            return []
        events: list[dict[str, Any]] = []
        if any(token in weather for token in ("雨", "阵雨", "雷", "小雨", "中雨", "大雨")) and random.random() < 0.24:
            events.append(
                {
                    "source": "weather_context",
                    "weather_linked": True,
                    "window": self._pick_weather_window("rain"),
                    "reason": "activity_share",
                    "action": "message",
                    "why": f"外面在下雨，想短短提一句。{weather}",
                    "topic": "外面下雨了",
                    "motive": "听见外面下雨，想短短提一声",
                    "mood": "安静",
                }
            )
        if any(token in weather for token in ("晴", "阳光", "多云", "晚霞")) and random.random() < 0.12:
            events.append(
                {
                    "source": "weather_context",
                    "weather_linked": True,
                    "window": self._pick_weather_window("clear"),
                    "reason": "activity_share",
                    "action": "message",
                    "why": f"外面的天色有点好看，想短短提一句。{weather}",
                    "topic": "天色有点好看",
                    "motive": "外面天色不错",
                    "mood": "松弛",
                }
            )
        return events[:1]

    @staticmethod
    def _daily_proactive_archive_context_text(text: str) -> bool:
        if not text:
            return False
        raw = str(text)
        compact = re.sub(r"\s+", "", raw).lower()
        lowered = raw.lower()
        if "主动承接占位" in raw and ("用户还没发来新消息" in raw or "bot主动" in compact):
            return True
        if "这不是用户消息" in raw and "private companion" in lowered and "主动消息" in raw:
            return True
        if "[主动消息]" in raw or "【主动消息】" in raw:
            legacy_markers = ("触发原因", "行为结果", "内部动机", "动作摘要")
            if sum(1 for marker in legacy_markers if marker in raw) >= 2:
                return True
        return False

    def _sanitize_proactive_social_fact_fields_inplace(self, item: dict[str, Any], *, field: str) -> bool:
        if not isinstance(item, dict):
            return False
        changed = False
        for key in ("topic", "motive", "why", "scene", "impulse"):
            original = _single_line(item.get(key), 180)
            if not original:
                continue
            cleaned = self._sanitize_daily_plan_social_fact_text(original, field=f"{field}.{key}")
            if cleaned != original:
                item[key] = cleaned
                changed = True
        if changed and "signature" in item:
            item["signature"] = self._proactive_topic_signature(
                item.get("reason"),
                item.get("source"),
                item.get("topic"),
                item.get("motive"),
            )
        return changed

    def _sanitize_user_proactive_social_facts_inplace(self, user: dict[str, Any], *, field: str) -> bool:
        if not isinstance(user, dict):
            return False
        changed = False
        for source_key in ("planned_proactive_topic", "planned_proactive_motive"):
            original = _single_line(user.get(source_key), 180)
            if not original:
                continue
            cleaned = self._sanitize_daily_plan_social_fact_text(original, field=f"{field}.{source_key}")
            if cleaned != original:
                user[source_key] = cleaned
                changed = True
        if changed:
            user["planned_proactive_model_judge_signature"] = ""
            user["planned_proactive_model_judge_result"] = {}
        impulses = user.get("proactive_impulses")
        if isinstance(impulses, list):
            for index, item in enumerate(impulses):
                if self._sanitize_proactive_social_fact_fields_inplace(
                    item,
                    field=f"{field}.proactive_impulses.{index}",
                ):
                    changed = True
        recent_topics = user.get("recent_proactive_topics")
        if isinstance(recent_topics, list):
            kept_topics: list[Any] = []
            topics_changed = False
            meta_leak_checker = getattr(self, "_framework_agent_meta_summary_leak", None)
            for index, topic in enumerate(recent_topics):
                if isinstance(topic, dict):
                    if callable(meta_leak_checker) and (
                        meta_leak_checker(str(topic.get("text") or ""))
                        or meta_leak_checker(str(topic.get("signature") or ""))
                    ):
                        topics_changed = True
                        continue
                    item_changed = False
                    cleaned_topic = dict(topic)
                    for key in ("text", "topic", "motive"):
                        original_value = _single_line(cleaned_topic.get(key), 180)
                        if not original_value:
                            continue
                        cleaned_value = self._sanitize_daily_plan_social_fact_text(
                            original_value,
                            field=f"{field}.recent_proactive_topics.{index}.{key}",
                        )
                        if cleaned_value != original_value:
                            cleaned_topic[key] = cleaned_value
                            item_changed = True
                    if item_changed:
                        topics_changed = True
                    kept_topics.append(cleaned_topic)
                    continue
                original = _single_line(topic, 180)
                if not original:
                    continue
                cleaned = self._sanitize_daily_plan_social_fact_text(
                    original,
                    field=f"{field}.recent_proactive_topics.{index}",
                )
                if cleaned != original:
                    topics_changed = True
                if cleaned and cleaned != "放慢节奏处理手边的小事，把这段时间过得轻一点":
                    kept_topics.append(cleaned)
            if topics_changed:
                user["recent_proactive_topics"] = kept_topics[-20:]
                changed = True
        return changed

    def _sync_live_user_proactive_schedule(self, user_id: str, source: dict[str, Any]) -> bool:
        """Mirror proactive-plan mutations from a tick snapshot back to the live user record."""
        if not isinstance(source, dict):
            return False
        raw_user_id = str(user_id or source.get("user_id") or source.get("id") or "").strip()
        if not raw_user_id:
            return False
        try:
            current = self._get_user(raw_user_id)
        except Exception:
            return False
        if not isinstance(current, dict):
            return False
        keys = (
            "next_proactive_at",
            "planned_proactive_reason",
            "planned_proactive_action",
            "planned_proactive_source",
            "planned_proactive_conversation_posture",
            "planned_proactive_conversation_closing_deferred",
            "planned_proactive_kind",
            "planned_proactive_route_version",
            "planned_proactive_route_dedupe_key",
            "planned_proactive_route_review_profile",
            "planned_proactive_route_retry_profile",
            "planned_proactive_route_cancel_if_new_inbound",
            "planned_proactive_route_recent_chat_policy",
            "planned_proactive_route_allow_automatic_followup",
            "planned_proactive_route_disable_segmenting",
            "planned_proactive_response_expectation",
            "planned_proactive_origin_event_id",
            "planned_proactive_route_preflight_action",
            "planned_proactive_route_preflight_note",
            "planned_proactive_motive",
            "planned_proactive_topic",
            "planned_proactive_impulse_id",
            "planned_proactive_window_start_at",
            "planned_proactive_best_until_at",
            "planned_proactive_expire_at",
            "planned_proactive_origin_at",
            "planned_proactive_origin_key",
            "planned_proactive_freshness",
            "planned_proactive_delivery_state",
            "planned_proactive_semantic_kind",
            "planned_proactive_anchor_type",
            "planned_proactive_semantic_score",
            "planned_proactive_semantic_note",
            "planned_proactive_model_judge_signature",
            "planned_proactive_model_judge_result",
            "planned_proactive_model_judge_at",
            "planned_event_chain",
            "planned_opener_mode",
            "planned_followup_kind",
            "planned_proactive_quota_exempt",
            "planned_proactive_window_timezone",
            "planned_birthday_event_context",
            "planned_special_day_context",
            "insomnia_night_context",
            "planned_candidate_id",
            "planned_proactive_trigger_message_id",
            "planned_proactive_trigger_umo",
            "planned_proactive_trigger_ts",
            "planned_proactive_trigger_inbound_count",
            "planned_proactive_trigger_created_at",
            "proactive_impulses",
            "recent_proactive_hesitations",
            "last_proactive_hesitation_at",
            "last_proactive_hesitation_note",
        )
        changed = False
        for key_name in keys:
            if key_name not in source:
                continue
            value = deepcopy(source.get(key_name))
            if current.get(key_name) != value:
                current[key_name] = value
                changed = True
        return changed

    def _recent_chat_proactive_guard_reason(
        self,
        user: dict[str, Any],
        *,
        now: float | None = None,
        planned_reason: str = "",
        planned_source: str = "",
        due_timer_active: bool = False,
        is_troubleshooting: bool = False,
    ) -> str:
        """Block ordinary proactive messages when the private chat has just moved."""
        if not isinstance(user, dict):
            return ""
        source = normalize_legacy_tag_text(planned_source or user.get("planned_proactive_source"))
        if is_troubleshooting or due_timer_active or source == "timer":
            return ""
        check_now = _now_ts() if now is None else now
        reason = normalize_legacy_tag_text(planned_reason or user.get("planned_proactive_reason"))
        idle_minutes = (
            self._effective_user_greeting_idle_minutes(user)
            if self._is_greeting_reason(reason)
            else self._effective_user_idle_minutes(user)
        )
        idle_seconds = max(0, idle_minutes) * 60
        if idle_seconds <= 0:
            return ""
        recent_at = self._latest_private_user_activity_ts(user)
        if recent_at <= 0:
            return ""
        remaining = recent_at + idle_seconds - check_now
        if remaining <= 0:
            return ""
        minutes = max(1, int(math.ceil(remaining / 60)))
        return f"刚聊完，普通主动延后（还需安静约 {minutes} 分钟）"

    def _defer_proactive_for_recent_chat(
        self,
        user: dict[str, Any],
        *,
        now: float | None = None,
        note: str = "",
    ) -> None:
        if not isinstance(user, dict):
            return
        check_now = _now_ts() if now is None else now
        reason = normalize_legacy_tag_text(user.get("planned_proactive_reason"))
        idle_minutes = (
            self._effective_user_greeting_idle_minutes(user)
            if self._is_greeting_reason(reason)
            else self._effective_user_idle_minutes(user)
        )
        recent_at = self._latest_private_user_activity_ts(user)
        quiet_until = recent_at + max(0, idle_minutes) * 60 if recent_at > 0 else check_now + 10 * 60
        if self._is_sticky_greeting_reason(reason) and self._reschedule_greeting_within_window(user, reason, now=check_now):
            pass
        else:
            delay_minutes = (
                max(5.0, (quiet_until - check_now) / 60 + 2.0),
                max(8.0, (quiet_until - check_now) / 60 + 8.0),
            )
            replacer = getattr(self, "_defer_or_replace_planned_impulse", None)
            replaced = False
            handled_by_replacer = False
            if callable(replacer):
                try:
                    handled_by_replacer = True
                    replaced = bool(
                        replacer(
                            user,
                            now=check_now,
                            note=note or "刚聊完，普通主动延后",
                            delay_minutes=delay_minutes,
                            block_current=False,
                        )
                    )
                except Exception as exc:
                    logger.debug("刚聊完主动换念头失败,回退延后: %s", _single_line(exc, 120))
                    replaced = False
                    handled_by_replacer = False
            if not replaced:
                if handled_by_replacer and _safe_float(user.get("next_proactive_at"), 0) > check_now:
                    pass
                elif handled_by_replacer and not _single_line(normalize_legacy_tag_text(user.get("planned_proactive_reason")), 40):
                    self._schedule_next_proactive(user, now=check_now, delay_hours=(max(0.2, delay_minutes[0] / 60), max(0.35, delay_minutes[1] / 60)))
                else:
                    user["next_proactive_at"] = max(check_now + 5 * 60, quiet_until + random.uniform(2 * 60, 8 * 60))
            if normalize_legacy_tag_text(user.get("planned_proactive_source")) == "simulation":
                sim = user.get("simulation_mode")
                events = sim.get("events") if isinstance(sim, dict) else None
                if isinstance(events, list) and events and isinstance(events[0], dict):
                    events[0]["_scheduled_ts"] = user["next_proactive_at"]
            if handled_by_replacer:
                return
        self._mark_planned_candidate_status(user, "deferred", note or "刚聊完，普通主动延后")

    def _is_troubleshooting_proactive_plan(self, user: dict[str, Any]) -> bool:
        return isinstance(user, dict) and normalize_legacy_tag_text(user.get("planned_proactive_source")) == "troubleshooting"

    def _append_troubleshooting_proactive_step(
        self,
        user: dict[str, Any],
        name: str,
        status: str,
        detail: str = "",
    ) -> list[dict[str, str]]:
        steps = user.setdefault("troubleshooting_proactive_steps", [])
        if not isinstance(steps, list):
            steps = []
            user["troubleshooting_proactive_steps"] = steps
        steps.append(
            {
                "name": _single_line(name, 40),
                "status": _single_line(status, 16) or "info",
                "detail": _single_line(detail, 180),
            }
        )
        del steps[:-12]
        return steps

    def _record_troubleshooting_proactive_result(
        self,
        user_id: str,
        user: dict[str, Any],
        *,
        ok: bool,
        detail: str,
        error: str = "",
        text: str = "",
        original_text: str = "",
        final_text: str = "",
        action: str = "message",
        reason: str = "check_in",
        extra_count: int = 0,
        diagnostic_detail: str = "",
        pending: bool = False,
        outcome_type: str = "",
    ) -> None:
        raw = self.data.setdefault("troubleshooting_test_results", {})
        if not isinstance(raw, dict):
            raw = {}
            self.data["troubleshooting_test_results"] = raw
        started = _safe_float(user.get("troubleshooting_proactive_started_at"), 0)
        now = _now_ts()
        diagnostic_sanitizer = getattr(self, "_proactive_audit_safe_note", None)
        safe_diagnostic_detail = (
            diagnostic_sanitizer(diagnostic_detail, limit=2400)
            if diagnostic_detail and callable(diagnostic_sanitizer)
            else _single_line(diagnostic_detail, 2400)
        )
        outcome = _single_line(outcome_type, 40).lower()
        if not outcome:
            combined = f"{detail} {error}".lower()
            if pending:
                outcome = "running"
            elif ok:
                outcome = "completed"
            elif "发送失败" in combined or "投递失败" in combined:
                outcome = "delivery_failed"
            elif "final content gate" in combined or "复核" in combined or "校验" in combined:
                outcome = "content_rejected"
            elif "生成" in combined or "llm" in combined:
                outcome = "generation_failed"
            elif "超时" in combined or "到点" in combined or "未启用" in combined:
                outcome = "scheduler_blocked"
            else:
                outcome = "interrupted"
        raw["proactive_message"] = {
            "type": "proactive_message",
            "ok": bool(ok),
            "pending": bool(pending),
            "trace_id": _single_line(user.get("troubleshooting_proactive_test_id"), 32),
            "outcome_type": outcome,
            "title": "主动消息链路测试",
            "umo": _single_line(user.get("umo"), 180),
            "detail": _single_line(detail, 220),
            "error": _single_line(error, 220),
            "diagnostic_detail": safe_diagnostic_detail,
            "text_preview": self._proactive_visible_text_preview(text) if text else "",
            "original_text_preview": self._proactive_visible_text_preview(original_text) if original_text else "",
            "final_text_preview": self._proactive_visible_text_preview(final_text) if final_text else "",
            "action": _single_line(action, 60) or "message",
            "reason": _single_line(reason, 40) or "check_in",
            "extra_count": max(0, int(extra_count or 0)),
            "steps": list(user.get("troubleshooting_proactive_steps") or [])[:12],
            "elapsed_ms": int(max(0.0, now - started) * 1000) if started > 0 else 0,
            "ran_at": now,
            "ran_at_text": self._format_timestamp_elapsed(now),
            "user_id": _single_line(user_id, 80),
        }

    def _restore_troubleshooting_proactive_plan(self, user: dict[str, Any]) -> None:
        restore = user.get("troubleshooting_proactive_restore")
        if isinstance(restore, dict):
            values = restore.get("values")
            if isinstance(values, dict):
                missing = restore.get("missing")
                if isinstance(missing, list):
                    for key in missing:
                        if isinstance(key, str):
                            user.pop(key, None)
                for key, value in values.items():
                    if isinstance(key, str):
                        user[key] = deepcopy(value)
            else:
                for key, value in restore.items():
                    user[key] = deepcopy(value)
        else:
            self._clear_pending_proactive_plan(user)
        user.pop("troubleshooting_proactive_restore", None)
        user.pop("troubleshooting_proactive_test_id", None)
        user.pop("troubleshooting_proactive_started_at", None)
        user.pop("troubleshooting_proactive_steps", None)

    def _recover_stale_troubleshooting_proactive_plans(self) -> int:
        users = self.data.get("users")
        if not isinstance(users, dict):
            return 0
        recovered = 0
        for user_id, user in users.items():
            if not isinstance(user, dict) or not isinstance(user.get("troubleshooting_proactive_restore"), dict):
                continue
            self._append_troubleshooting_proactive_step(user, "启动恢复", "error", "上次排障临时主动未完成，已恢复原计划")
            self._record_troubleshooting_proactive_result(
                str(user_id),
                user,
                ok=False,
                detail="上次排障临时主动任务未完成，插件启动时已恢复原主动计划",
                error="插件重启或任务中断",
                action=str(user.get("planned_proactive_action") or "message"),
                reason=normalize_legacy_tag_text(user.get("planned_proactive_reason")) or "check_in",
            )
            user["proactive_sending"] = False
            user["proactive_sending_started_at"] = 0
            self._restore_troubleshooting_proactive_plan(user)
            recovered += 1
        return recovered

    async def _run_proactive_maintenance_tasks(self) -> None:
        if self._proactive_generation_disabled():
            return
        # 分批轮换：每个 tick 周期只执行约一半维护任务，交错进行，
        # 避免单个周期内串行跑完全部任务拉高瞬时负载；各任务内部自带到期门控。
        tasks = (
            ("技能成长结算", self._maybe_settle_skill_growth),
            ("B站无聊观看", self._maybe_trigger_bilibili_boredom_watch),
            ("网页探索", self._maybe_trigger_web_exploration),
            ("AI日报追踪", self._maybe_track_ai_daily),
            ("新闻无聊阅读", self._maybe_trigger_news_boredom_read),
            ("QQ空间生活说说", self._maybe_publish_qzone_life_post),
            ("QQ空间评论收件箱", self._maybe_process_qzone_comment_inbox),
        )
        batch = getattr(self, "_proactive_maintenance_batch", 0)
        self._proactive_maintenance_batch = 1 - batch
        for index, (label, task_factory) in enumerate(tasks):
            if (index % 2) != batch:
                continue
            try:
                await task_factory()
            except Exception as exc:
                logger.warning("主动维护任务失败,不阻塞私聊主动: %s error=%s", label, _single_line(exc, 160))

    @staticmethod
    def _proactive_send_disables_segmenting(reason: str, *, friend_proactive: bool = False) -> bool:
        # Friend-proactive output has already been planned by its upstream sender.
        # All locally rendered reasons, including creative shares, should respect
        # the user's segmentation settings; media remains atomic in the planner.
        return bool(friend_proactive)

