# -*- coding: utf-8 -*-
"""plan 域。

由 tools/split_mixin_domain.py 从 daily_state.py 机械抽取（102 个方法 + 0 个模块级名字 + 0 个类级赋值 / 3410 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 DailyStateMixin）。
"""
from __future__ import annotations

import json
import random
import re
import time
import unicodedata
import uuid
from .agenda_contracts import normalize_plan_item
from .conversation_prompt_section import PromptRenderMode, PromptSection, prompt_section, render_prompt_sections
from .helpers import (
    _date_key,
    _memory_archive_warning,
    _now_ts,
    _safe_float,
    _safe_int,
    _single_line,
    _today_key,
)
from .persona_config import runtime_persona_setting
from .planning import (
    build_daily_plan_prompt,
    build_daily_plan_prompt_section,
    generate_daily_plan,
    get_schedule_planning_prompt,
)
from astrbot.core.db.po import Conversation
from copy import deepcopy
from datetime import date, datetime, timedelta
from typing import Any

from .logging_util import get_module_logger

logger = get_module_logger(__name__)



class DailyStatePlanMixin:
    """plan 域（从 DailyStateMixin 拆出）。"""


    def _save_daily_state_sections(self, sections: set[str]) -> None:
        saver = getattr(self, "_save_data_sync", None)
        if not callable(saver):
            return
        try:
            saver(sections=sections)
        except TypeError as exc:
            if "unexpected keyword argument" not in str(exc) or "sections" not in str(exc):
                raise
            saver()

    def _detail_enhanced_segments_for_plan_date(
        self,
        plan_date: Any,
        enhanced: Any = None,
        *,
        detail_day: Any = None,
    ) -> dict[str, Any]:
        date_key = _single_line(plan_date, 16)
        recorded_day = self.data.get("detail_enhanced_day") if detail_day is None else detail_day
        if not date_key or _single_line(recorded_day, 16) != date_key:
            return {}
        source = enhanced if isinstance(enhanced, dict) else self.data.get("detail_enhanced_segments")
        if not isinstance(source, dict):
            return {}
        current: dict[str, Any] = {}
        for raw_key, snapshot in source.items():
            key = str(raw_key or "")
            keyed = re.match(r"^(\d{4}-\d{2}-\d{2}):", key)
            if keyed and keyed.group(1) != date_key:
                continue
            if isinstance(snapshot, dict):
                current[key] = snapshot
        return current

    async def _ensure_daily_plan(self, force: bool = False) -> dict[str, Any] | None:
        if not runtime_persona_setting(self, "enable_daily_plan", True) and not force:
            return None

        await self._ensure_daily_state(force=force)
        today = _today_key()
        async with self._data_lock:
            current_plan = self.data.setdefault("daily_plan", {})
            current_plan_date = _single_line(current_plan.get("date"), 16) if isinstance(current_plan, dict) else ""
            detail_day_changed = bool(
                current_plan_date
                and self._is_plan_date_active(current_plan_date)
                and self._sync_detail_enhancement_day_locked(current_plan_date)
            )
            known_users = [
                user for user in self.data.get("users", {}).values() if isinstance(user, dict) and user.get("umo")
            ]
            if not force and current_plan.get("date") == today:
                plan_changed = self._sanitize_daily_plan_inplace(current_plan)
                if plan_changed:
                    self._refresh_daily_state_location_from_plan(plan=current_plan)
                if plan_changed or detail_day_changed:
                    self._save_daily_state_sections(
                        sections={
                            "daily_plan",
                            "daily_state",
                            "detail_enhanced_day",
                            "detail_enhanced_segments",
                            "daily_story_plan",
                        }
                    )
                source = _single_line(current_plan.get("source"), 40).lower()
                retry_after = _safe_float(current_plan.get("retry_after"), 0.0)
                fallback_retry_due = source.startswith("fallback") and (
                    retry_after <= 0 or _now_ts() >= retry_after
                )
                if not fallback_retry_due:
                    return current_plan
            if (
                not force
                and current_plan.get("date") != today
                and self._is_plan_date_active(current_plan.get("date"))
            ):
                plan_changed = self._sanitize_daily_plan_inplace(current_plan)
                if plan_changed:
                    self._refresh_daily_state_location_from_plan(plan=current_plan)
                if plan_changed or detail_day_changed:
                    self._save_daily_state_sections(
                        sections={
                            "daily_plan",
                            "daily_state",
                            "detail_enhanced_day",
                            "detail_enhanced_segments",
                            "daily_story_plan",
                        }
                    )
                return current_plan
            active_persona = str(
                getattr(self, "_active_persona_scope", lambda: "")() or ""
            ).strip()
            primary_persona = str(
                getattr(self, "_primary_persona_id", lambda: "")() or ""
            ).strip()
            configured_empty_secondary = bool(
                getattr(self, "enable_multi_persona_mode", False)
                and active_persona
                and active_persona != primary_persona
                and callable(getattr(self, "_persona_config_exists", None))
                and self._persona_config_exists(active_persona)
            )
            if not force and not known_users and not configured_empty_secondary:
                return current_plan if current_plan.get("date") == today else None
            if not force and not self._is_daily_plan_due():
                if self._is_plan_date_active(current_plan.get("date")):
                    plan_changed = self._sanitize_daily_plan_inplace(current_plan)
                    if plan_changed:
                        self._refresh_daily_state_location_from_plan(plan=current_plan)
                    if plan_changed or detail_day_changed:
                        self._save_daily_state_sections(
                            sections={
                                "daily_plan",
                                "daily_state",
                                "detail_enhanced_day",
                                "detail_enhanced_segments",
                                "daily_story_plan",
                            }
                        )
                    return current_plan
                return None

        generation_lock = self._daily_generation_lock("_daily_plan_generation_lock")
        async with generation_lock:
            # The initial eligibility check intentionally happens before the
            # model call, but another caller may have generated the plan while
            # we were waiting. Re-check the shared plan inside the generation
            # lock so one day/persona only consumes one model request.
            if not force:
                async with self._data_lock:
                    current_plan = self.data.get("daily_plan")
                    if isinstance(current_plan, dict) and _single_line(current_plan.get("date"), 16) == today:
                        source = _single_line(current_plan.get("source"), 40).lower()
                        retry_after = _safe_float(current_plan.get("retry_after"), 0.0)
                        fallback_retry_due = source.startswith("fallback") and (
                            retry_after <= 0 or _now_ts() >= retry_after
                        )
                        if not fallback_retry_due:
                            return current_plan

            plan = await self._generate_daily_plan()
            async with self._data_lock:
                self.data["daily_plan"] = plan
                self._sync_detail_enhancement_day_locked(plan.get("date"), reset=True)
                self._refresh_daily_state_location_from_plan(plan=plan)
                self._save_daily_state_sections(
                    sections={
                        "daily_plan",
                        "daily_state",
                        "detail_enhanced_day",
                        "detail_enhanced_segments",
                        "daily_story_plan",
                    }
                )
        outfit_generator = getattr(self, "_ensure_daily_outfit_photo", None)
        if callable(outfit_generator):
            try:
                await outfit_generator()
            except Exception as exc:
                logger.warning(
                    "今日日程已保存,但每日穿搭照片生成失败: %s",
                    _single_line(exc, 180),
                )
        await self._ensure_daily_news_reading(force=force)
        return plan

    @staticmethod
    def _schedule_segment_selector_cn_number(value: Any) -> int | None:
        text = str(value or "").strip().replace("兩", "两").replace("〇", "零")
        if not text:
            return None
        if text.isdigit():
            return int(text)
        digits = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
        if text in digits:
            return digits[text]
        if "十" in text:
            left, _, right = text.partition("十")
            tens = digits.get(left, 1) if left else 1
            ones = digits.get(right, 0) if right else 0
            return tens * 10 + ones
        return None

    def _schedule_segment_selector_minutes(self, selector: str) -> list[int]:
        compact = re.sub(r"\s+", "", str(selector or ""))
        match = re.search(
            r"(凌晨|早上|早晨|上午|中午|下午|傍晚|晚上|今晚|夜里)?"
            r"(\d{1,2}|[零〇一二两兩三四五六七八九十]{1,3})"
            r"(?:[:：点點时時])"
            r"(\d{1,2}|半|一刻|三刻)?",
            compact,
        )
        if not match:
            return []
        period = str(match.group(1) or "")
        hour = self._schedule_segment_selector_cn_number(match.group(2))
        minute_text = str(match.group(3) or "")
        if hour is None:
            return []
        if minute_text == "半":
            minute = 30
        elif minute_text == "一刻":
            minute = 15
        elif minute_text == "三刻":
            minute = 45
        else:
            minute = _safe_int(minute_text, 0, 0, 59)
        if hour > 23:
            return []
        if period in {"凌晨"} and hour == 12:
            hour = 0
        elif period in {"中午", "下午", "傍晚", "晚上", "今晚", "夜里"}:
            if hour == 12:
                hour = 12 if period == "中午" else 0
            elif hour < 12:
                hour += 12
        elif period in {"早上", "早晨", "上午"} and hour == 12:
            hour = 0
        primary = hour * 60 + minute
        if period or hour == 0 or hour > 12:
            return [primary]
        alternate = primary + 12 * 60
        return [primary, alternate] if alternate < 24 * 60 else [primary]

    @staticmethod
    def _schedule_segment_selector_text(value: Any) -> str:
        text = unicodedata.normalize("NFKC", _single_line(value, 160)).lower()
        text = re.sub(
            r"(?:今天|今日|今儿|当天|这一段|这个|那一段|那个|时段|时间段|日程|安排|计划|细化|活动|任务)",
            "",
            text,
        )
        text = re.sub(
            r"(?:凌晨|早上|早晨|上午|中午|下午|傍晚|晚上|今晚|夜里)?"
            r"(?:\d{1,2}|[零〇一二两兩三四五六七八九十]{1,3})"
            r"(?:[:：点點时時])(?:\d{1,2}|半|一刻|三刻)?",
            "",
            text,
        )
        text = re.sub(r"[\s\-_—:：，,。.!！?？'\"“”‘’（）()【】\[\]]+", "", text)
        return text

    def _schedule_segment_label(self, segment: dict[str, Any]) -> str:
        item = segment.get("item") if isinstance(segment.get("item"), dict) else {}
        start = self._minutes_to_hhmm(_safe_int(segment.get("start"), 0))
        end = self._minutes_to_hhmm(_safe_int(segment.get("end"), 0))
        activity = _single_line(item.get("activity"), 80) or "未命名日程"
        status = self._normalize_schedule_lifecycle_status(item.get("lifecycle_status"))
        suffix = "（已取消）" if status == "cancelled" else ""
        return f"{start}-{end} {activity}{suffix}"

    def _resolve_daily_plan_segment_selector(
        self,
        selector: Any,
        *,
        plan: dict[str, Any] | None = None,
        include_cancelled: bool = True,
    ) -> tuple[dict[str, Any] | None, str]:
        current_plan = plan if isinstance(plan, dict) else self.data.get("daily_plan", {})
        if not isinstance(current_plan, dict) or not current_plan.get("items"):
            return None, "今天还没有可操作的日程。"
        segments = self._collect_detail_segments(current_plan, {}, include_cancelled=include_cancelled)
        if not segments:
            return None, "今天还没有可操作的日程段。"
        raw = _single_line(selector, 160)
        if not raw:
            return None, "请指定时间或活动，例如“陪伴 删除日程 15:00”或“陪伴 重置日程 整理房间”。"

        exact_key = next((segment for segment in segments if _single_line(segment.get("key"), 120) == raw), None)
        if exact_key:
            return exact_key, ""
        compact = re.sub(r"\s+", "", raw.lower())
        if compact in {"当前", "现在", "此刻", "正在进行", "当前段", "这一段", "这段"}:
            current = self._current_detail_segment_for_update()
            if current:
                return current, ""
            return None, "当前没有正在进行或即将开始的日程段。"

        ordinal_match = re.search(
            r"(?:第\s*(\d{1,2}|[一二两三四五六七八九十]{1,3})\s*(?:个|项|段)?|"
            r"(\d{1,2}|[一二两三四五六七八九十]{1,3})\s*(?:个|项|段))",
            raw,
        )
        if ordinal_match:
            ordinal = self._schedule_segment_selector_cn_number(ordinal_match.group(1) or ordinal_match.group(2))
            if ordinal is not None and 1 <= ordinal <= len(segments):
                return segments[ordinal - 1], ""

        requested_minutes = self._schedule_segment_selector_minutes(raw)
        time_matches: list[dict[str, Any]] = []
        if requested_minutes:
            exact_starts = [
                segment
                for segment in segments
                if any(_safe_int(segment.get("start"), -1) % (24 * 60) == minute for minute in requested_minutes)
            ]
            if exact_starts:
                time_matches = exact_starts
            else:
                time_matches = [
                    segment
                    for segment in segments
                    if any(
                        _safe_int(segment.get("start"), 0) <= minute < _safe_int(segment.get("end"), 0)
                        or _safe_int(segment.get("start"), 0) <= minute + 24 * 60 < _safe_int(segment.get("end"), 0)
                        for minute in requested_minutes
                    )
                ]

        activity_query = self._schedule_segment_selector_text(raw)
        activity_matches: list[dict[str, Any]] = []
        if activity_query:
            for segment in segments:
                item = segment.get("item") if isinstance(segment.get("item"), dict) else {}
                activity = self._schedule_segment_selector_text(item.get("activity"))
                if activity_query == activity or activity_query in activity or activity in activity_query:
                    activity_matches.append(segment)

        matches = time_matches
        if activity_matches:
            intersection = [segment for segment in time_matches if segment in activity_matches]
            matches = intersection or activity_matches if time_matches else activity_matches
        if len(matches) == 1:
            return matches[0], ""
        if len(matches) > 1:
            choices = "；".join(self._schedule_segment_label(segment) for segment in matches[:5])
            return None, f"匹配到多段日程，请再具体一点：{choices}"

        choices = "；".join(self._schedule_segment_label(segment) for segment in segments[:8])
        return None, f"没有找到“{raw}”对应的日程。今天可选：{choices}"

    async def _cancel_daily_plan_segment_by_selector(self, selector: Any, *, reason: str = "用户通过聊天命令取消该日程段") -> tuple[bool, str]:
        async with self._data_lock:
            segment, error = self._resolve_daily_plan_segment_selector(selector, include_cancelled=True)
            if not segment:
                return False, error
            key = _single_line(segment.get("key"), 120)
            plan = self.data.get("daily_plan", {})
            items = plan.get("items") if isinstance(plan, dict) else None
            index = _safe_int(segment.get("index"), -1, minimum=-1)
            item = items[index] if isinstance(items, list) and 0 <= index < len(items) and isinstance(items[index], dict) else None
            if not isinstance(item, dict):
                return False, "该日程段已经不存在。"
            if self._normalize_schedule_lifecycle_status(item.get("lifecycle_status")) == "cancelled":
                return True, f"这段日程之前已经取消：{self._schedule_segment_label(segment)}"
            label = self._schedule_segment_label(segment)
            item["lifecycle_status"] = "cancelled"
            item["changed_at"] = self._environment_now().strftime("%H:%M")
            item["change_reason"] = _single_line(reason, 120)
            item.pop("_detail_generation_id", None)
            self._sync_detail_enhancement_day_locked(plan.get("date"))
            enhanced = self.data.setdefault("detail_enhanced_segments", {})
            if not isinstance(enhanced, dict):
                enhanced = {}
                self.data["detail_enhanced_segments"] = enhanced
            cancelled = deepcopy(enhanced.get(key)) if isinstance(enhanced.get(key), dict) else {
                "status": "done",
                "summary": "这一段已取消。",
                "today_events": [],
                "proactive_events": [],
                "state_variables": [],
            }
            for event in list(cancelled.get("today_events") or []) + list(cancelled.get("proactive_events") or []):
                if isinstance(event, dict):
                    event["lifecycle_status"] = "cancelled"
            cancelled["status"] = "cancelled"
            cancelled["summary"] = _single_line(cancelled.get("summary"), 120) or "这一段已取消。"
            cancelled["cancelled_at"] = self._environment_now().strftime("%Y-%m-%d %H:%M:%S")
            for field in ("generation_id", "previous_item_state", "retry_after", "retry_after_ts"):
                cancelled.pop(field, None)
            enhanced[key] = cancelled
            story = self._rebuild_story_plan_from_detail_snapshots(str(plan.get("date") or _today_key()))
            self._remember_detail_enhancement_history(str(plan.get("date") or _today_key()), enhanced, story)
            self._save_data_sync(
                sections={
                    "daily_plan",
                    "detail_enhanced_day",
                    "detail_enhanced_segments",
                    "detail_enhanced_history",
                    "daily_story_plan",
                    "daily_story_plan_history",
                }
            )
            return True, f"已取消：{label}"

    async def _regenerate_daily_plan_segment_by_selector(
        self,
        selector: Any,
        generator: Any,
        *,
        reason: str = "用户通过聊天命令重新细化该日程段",
    ) -> tuple[bool, str, dict[str, Any]]:
        if not callable(generator):
            return False, "当前没有可用的日程细化生成器。", {}
        previous_snapshot: dict[str, Any] = {}
        previous_item_state: dict[str, tuple[bool, Any]] = {}
        generation_id = ""
        key = ""
        segment: dict[str, Any] = {}
        plan: dict[str, Any] = {}
        try:
            async with self._data_lock:
                live_plan = self.data.get("daily_plan", {})
                segment, error = self._resolve_daily_plan_segment_selector(
                    selector,
                    plan=live_plan if isinstance(live_plan, dict) else {},
                    include_cancelled=True,
                )
                if not segment:
                    return False, error, {}
                key = _single_line(segment.get("key"), 120)
                plan = deepcopy(live_plan)
                segment = next(
                    (
                        candidate
                        for candidate in self._collect_detail_segments(plan, {}, include_cancelled=True)
                        if _single_line(candidate.get("key"), 120) == key
                    ),
                    segment,
                )
                state = deepcopy(self.data.get("daily_state", {}))
                items = live_plan.get("items") if isinstance(live_plan, dict) else None
                index = _safe_int(segment.get("index"), -1, minimum=-1)
                live_item = items[index] if isinstance(items, list) and 0 <= index < len(items) and isinstance(items[index], dict) else None
                if not isinstance(live_item, dict):
                    return False, "该日程段已经不存在。", {}
                self._sync_detail_enhancement_day_locked(plan.get("date"))
                enhanced = self.data.setdefault("detail_enhanced_segments", {})
                if not isinstance(enhanced, dict):
                    enhanced = {}
                    self.data["detail_enhanced_segments"] = enhanced
                previous_snapshot = deepcopy(enhanced.get(key)) if isinstance(enhanced.get(key), dict) else {}
                if (
                    _single_line(previous_snapshot.get("status"), 24) == "generating"
                    and self._detail_enhancement_snapshot_blocks_generation(previous_snapshot)
                ):
                    return False, "该时间段正在细化中，请等待当前生成完成后再试。", {}
                for field in ("lifecycle_status", "changed_at", "change_reason", "_detail_generation_id"):
                    previous_item_state[field] = (field in live_item, deepcopy(live_item.get(field)))
                generation_id = uuid.uuid4().hex
                live_item["lifecycle_status"] = "changed"
                live_item["changed_at"] = self._environment_now().strftime("%H:%M")
                live_item["change_reason"] = _single_line(reason, 120)
                live_item["_detail_generation_id"] = generation_id
                segment_item = segment.get("item") if isinstance(segment.get("item"), dict) else None
                if isinstance(segment_item, dict):
                    segment_item["lifecycle_status"] = "changed"
                enhanced[key] = {
                    "status": "generating",
                    "started_at": self._environment_now().strftime("%H:%M"),
                    "started_ts": time.time(),
                    "regenerated": True,
                    "generation_id": generation_id,
                }
                self._save_data_sync(
                    sections={"daily_plan", "detail_enhanced_day", "detail_enhanced_segments"}
                )

            detail = await generator(self, segment, plan, state)
            if not isinstance(detail, dict) or not isinstance(detail.get("today_events"), list) or not detail.get("today_events"):
                raise RuntimeError("局部重生成未返回可用的细化事件")

            async with self._data_lock:
                if not self._detail_generation_is_current(segment, generation_id):
                    return False, "该时间段已被取消、替换或由更新的操作接管，本次迟到结果未写入。", {}
                enhanced = self.data.setdefault("detail_enhanced_segments", {})
                enhanced[key] = {
                    "status": "done",
                    "updated_at": self._environment_now().strftime("%H:%M"),
                    "summary": _single_line(detail.get("summary"), 120),
                    "summary_basis": self._normalize_schedule_basis(detail.get("summary_basis"), default=["coarse_plan"]),
                    "summary_confidence": min(1.0, _safe_float(detail.get("summary_confidence"), 0.75)),
                    "location": _single_line(detail.get("location"), 60),
                    "location_basis": self._normalize_schedule_basis(detail.get("location_basis"), default=["coarse_plan"]),
                    "location_confidence": min(1.0, _safe_float(detail.get("location_confidence"), 0.72)),
                    "today_events": detail.get("today_events", []),
                    "proactive_events": detail.get("proactive_events", []),
                    "state_variables": detail.get("state_variables", []),
                    "presence_status": detail.get("presence_status", {}),
                    "quality": detail.get("quality", {}),
                    "interaction_updates": previous_snapshot.get("interaction_updates", []),
                    "regenerated": True,
                    "regenerated_at": self._environment_now().strftime("%Y-%m-%d %H:%M:%S"),
                }
                self._sanitize_detail_enhanced_segments_inplace(enhanced)
                story = self._rebuild_story_plan_from_detail_snapshots(str(plan.get("date") or _today_key()))
                self._remember_detail_enhancement_history(str(plan.get("date") or _today_key()), enhanced, story)
                current_plan = self.data.get("daily_plan", {})
                current_items = current_plan.get("items") if isinstance(current_plan, dict) else None
                index = _safe_int(segment.get("index"), -1, minimum=-1)
                current_item = current_items[index] if isinstance(current_items, list) and 0 <= index < len(current_items) and isinstance(current_items[index], dict) else None
                if isinstance(current_item, dict) and _single_line(current_item.get("_detail_generation_id"), 64) == generation_id:
                    current_item.pop("_detail_generation_id", None)
                self._refresh_daily_state_location_from_plan(
                    plan=current_plan if isinstance(current_plan, dict) else plan,
                    detail=detail,
                    segment=segment,
                )
                self._save_data_sync(
                    sections={
                        "daily_plan",
                        "daily_state",
                        "detail_enhanced_day",
                        "detail_enhanced_segments",
                        "detail_enhanced_history",
                        "daily_story_plan",
                        "daily_story_plan_history",
                    }
                )
                label = self._schedule_segment_label(segment)
            return True, f"已重新细化：{label}", detail
        except Exception as exc:
            logger.warning("聊天命令局部重生成日程细化失败: %s", exc, exc_info=True)
            async with self._data_lock:
                enhanced = self.data.setdefault("detail_enhanced_segments", {})
                if isinstance(enhanced, dict) and key and generation_id and self._detail_generation_is_current(segment, generation_id):
                    restored = previous_snapshot or {
                        "status": "failed",
                        "today_events": [],
                        "proactive_events": [],
                        "state_variables": [],
                    }
                    restored["regeneration_error"] = _single_line(exc, 180)
                    restored["regeneration_failed_at"] = self._environment_now().strftime("%Y-%m-%d %H:%M:%S")
                    enhanced[key] = restored
                    live_plan = self.data.get("daily_plan", {})
                    live_items = live_plan.get("items") if isinstance(live_plan, dict) else None
                    index = _safe_int(segment.get("index"), -1, minimum=-1)
                    live_item = live_items[index] if isinstance(live_items, list) and 0 <= index < len(live_items) and isinstance(live_items[index], dict) else None
                    if isinstance(live_item, dict) and _single_line(live_item.get("_detail_generation_id"), 64) == generation_id:
                        for field, (existed, value) in previous_item_state.items():
                            if existed:
                                live_item[field] = value
                            else:
                                live_item.pop(field, None)
                    self._save_data_sync(
                        sections={"daily_plan", "detail_enhanced_segments"}
                    )
            return False, _single_line(exc, 180) or "局部重生成失败。", {}

    def _trim_story_plan_items(
        self,
        key: str,
        items: list[dict[str, Any]],
        limit: int,
    ) -> list[dict[str, Any]]:
        normalized = [item for item in items if isinstance(item, dict)]
        if not normalized:
            return []
        seen: set[tuple[Any, ...]] = set()
        deduped: list[dict[str, Any]] = []
        for item in normalized:
            identity = self._story_plan_item_identity(key, item)
            if identity in seen:
                continue
            seen.add(identity)
            deduped.append(item)
        if key == "long_term_events":
            return deduped[-limit:]
        ordered = sorted(deduped, key=self._story_plan_item_sort_key)
        if len(ordered) <= limit:
            return ordered
        return self._pick_story_items_with_coverage(ordered, limit)

    def _story_plan_item_identity(self, key: str, item: dict[str, Any]) -> tuple[Any, ...]:
        if key == "today_events":
            return (
                _single_line(item.get("window"), 20),
                _single_line(item.get("event"), 80),
            )
        if key == "proactive_events":
            return (
                _single_line(item.get("window"), 20),
                _single_line(item.get("reason"), 40),
                _single_line(item.get("action"), 40),
                _single_line(item.get("topic"), 80),
            )
        return (
            _single_line(item.get("title"), 80),
            _single_line(item.get("status"), 80),
        )

    def _story_plan_item_sort_key(self, item: dict[str, Any]) -> tuple[int, int, str]:
        start, end = self._parse_window_minutes(str(item.get("window") or ""))
        start_value = start if start is not None else 99_999
        end_value = end if end is not None else start_value
        if end_value < start_value:
            end_value += 24 * 60
        text = _single_line(
            item.get("event") or item.get("topic") or item.get("title"),
            80,
        )
        return (start_value, end_value, text)

    def _parse_date_value(self, value: Any) -> date | None:
        text = str(value or "").strip()
        for fmt in ("%Y-%m-%d", "%m-%d"):
            try:
                parsed = datetime.strptime(text, fmt)
                year = self._environment_now().year if fmt == "%m-%d" else parsed.year
                return date(year, parsed.month, parsed.day)
            except ValueError:
                continue
        return None

    def _next_occurrence(self, entry: dict[str, Any], now: datetime | None = None) -> date | None:
        base = self._parse_date_value(entry.get("date"))
        if base is None:
            return None
        today = (now or self._environment_now()).date()
        if entry.get("repeat_yearly", True):
            try:
                candidate = date(today.year, base.month, base.day)
            except ValueError:
                return None
            if candidate < today:
                try:
                    candidate = date(today.year + 1, base.month, base.day)
                except ValueError:
                    return None
            return candidate
        return base

    def _get_relevant_important_dates(self, now: datetime | None = None) -> list[dict[str, Any]]:
        entries = self.data.get("important_dates", [])
        if not isinstance(entries, list):
            return []
        current = now or self._environment_now()
        today = current.date()
        relevant = []
        for entry in entries:
            if not isinstance(entry, dict) or not entry.get("enabled", True):
                continue
            next_day = self._next_occurrence(entry, now=current)
            if next_day is None:
                continue
            days_until = (next_day - today).days
            remind_days = _safe_int(
                entry.get("remind_days"), runtime_persona_setting(self, "important_date_lookahead_days", 7), 0, 365
            )
            if 0 <= days_until <= remind_days:
                copy = dict(entry)
                copy["_next_date"] = _date_key(next_day)
                copy["_days_until"] = days_until
                relevant.append(copy)
        return sorted(
            relevant,
            key=lambda item: (
                _safe_int(item.get("_days_until"), 999),
                -_safe_int(item.get("priority"), 50),
            ),
        )

    def _format_important_dates_for_prompt(self) -> str:
        entries = self._get_relevant_important_dates()
        if not entries:
            return "（近期没有需要特别记住的日期）"
        lines = []
        for entry in entries[:8]:
            days = _safe_int(entry.get("_days_until"), 0)
            when = "今天" if days == 0 else f"{days} 天后"
            lines.append(
                f"- {when}｜{entry.get('title', '')}｜类型：{entry.get('type', '重要日期')}｜"
                f"备注：{entry.get('note', '')}"
            )
        return "\n".join(lines)

    def _format_calendar_context_for_prompt(self, now: datetime | None = None) -> str:
        current = now or self._environment_now()
        weekday_names = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")
        weekday = weekday_names[current.weekday()]
        is_weekend = current.weekday() >= 5
        builtin_holidays = {
            "01-01": ("元旦", "节假日"),
            "05-01": ("劳动节", "节假日"),
            "10-01": ("国庆节", "节假日"),
        }
        month_day = current.strftime("%m-%d")
        today_dates = [
            entry
            for entry in self._get_relevant_important_dates(now=current)
            if _safe_int(entry.get("_days_until"), 999) == 0
        ]
        special_lines = []
        holiday_tokens = (
            "节",
            "节日",
            "假",
            "假期",
            "放假",
            "休息",
            "旅行",
            "生日",
            "纪念日",
            "春节",
            "元旦",
            "清明",
            "端午",
            "中秋",
            "国庆",
            "劳动",
            "圣诞",
        )
        has_holiday_signal = False
        builtin_holiday = builtin_holidays.get(month_day)
        if builtin_holiday:
            title, type_text = builtin_holiday
            special_lines.append(f"- 今天：{title}｜类型：{type_text}｜备注：内置公历节日")
            has_holiday_signal = True
        for entry in today_dates[:5]:
            title = _single_line(entry.get("title"), 40)
            type_text = _single_line(entry.get("type"), 30)
            note = _single_line(entry.get("note"), 80)
            joined = f"{title} {type_text} {note}"
            if any(token in joined for token in holiday_tokens):
                has_holiday_signal = True
            if title:
                special_lines.append(f"- 今天：{title}｜类型：{type_text or '重要日期'}｜备注：{note or '无'}")

        # The durable calendar is a constraint layer, not execution evidence.
        # Keep its wording explicit so a generated plan can use a confirmed
        # vacation/school rule without claiming that the event already
        # happened.  The fallback below preserves compatibility with hosts
        # that predate AgendaRuntimeMixin.
        calendar_snapshot = {}
        snapshot_getter = getattr(self, "_agenda_calendar_snapshot", None)
        if callable(snapshot_getter):
            try:
                candidate = snapshot_getter(current.date().isoformat(), now=current)
                if isinstance(candidate, dict):
                    calendar_snapshot = candidate
            except Exception:
                calendar_snapshot = {}
        calendar_timeline: dict[str, Any] = {}
        timeline_getter = getattr(self, "_agenda_calendar_timeline", None)
        if callable(timeline_getter):
            try:
                candidate = timeline_getter(
                    current.date().isoformat(),
                    now=current,
                    history_days=3,
                    horizon_days=14,
                )
                if isinstance(candidate, dict):
                    calendar_timeline = candidate
            except Exception:
                calendar_timeline = {}
        calendar_candidates: list[dict[str, Any]] = []
        candidates_getter = getattr(self, "_agenda_calendar_candidates_store", None)
        if callable(candidates_getter):
            try:
                raw_candidates = candidates_getter()
                if isinstance(raw_candidates, list):
                    calendar_candidates = [
                        item for item in raw_candidates
                        if isinstance(item, dict)
                        and str(item.get("lifecycle_state") or item.get("lifecycle") or "candidate") not in {"confirmed", "active", "completed", "cancelled", "expired"}
                    ][:8]
            except Exception:
                calendar_candidates = []
        all_calendar_events = [
            item for item in calendar_snapshot.get("effective_events", calendar_snapshot.get("events", []))
            if isinstance(item, dict) and str(item.get("status") or "") not in {"cancelled", "expired"}
        ]
        # New snapshots expose ``events`` as the complete adjusted list and
        # ``effective_events`` as the planning projection. Older snapshots may
        # only contain one list, so fall back gracefully.
        raw_calendar_events = calendar_snapshot.get("events")
        if isinstance(raw_calendar_events, list):
            all_calendar_events = [
                item for item in raw_calendar_events
                if isinstance(item, dict) and str(item.get("status") or "") not in {"cancelled", "expired"}
            ]
        calendar_events = [
            item for item in calendar_snapshot.get("effective_events", all_calendar_events)
            if isinstance(item, dict) and str(item.get("status") or "") not in {"cancelled", "expired"}
        ]
        calendar_constraints: list[str] = []
        calendar_conflict_lines: list[str] = []
        for event in calendar_events[:16]:
            title = _single_line(event.get("title"), 60)
            if not title:
                continue
            kind = str(event.get("kind") or event.get("type") or "event")
            kind_label = {
                "period": "长期区间",
                "recurrence": "周期规则",
                "event": "单次事件",
                "exception": "例外调整",
            }.get(kind, "日历事件")
            start_date = _single_line(
                event.get("occurrence_date") if kind != "period" else event.get("start_date"),
                24,
            ) or _single_line(event.get("date") or event.get("start_date"), 24)
            end_date = _single_line(event.get("end_date"), 24) if kind == "period" else ""
            date_text = start_date
            if end_date and end_date != start_date:
                date_text = f"{start_date} 至 {end_date}"
            start_at = _single_line(event.get("start_at"), 40)
            end_at = _single_line(event.get("end_at"), 40)
            clock_text = ""
            if (not event.get("all_day") or event.get("start_time") or event.get("end_time")) and start_at and "T" in start_at:
                clock_text = start_at.split("T", 1)[1][:5]
                if end_at and "T" in end_at:
                    clock_text += f"-{end_at.split('T', 1)[1][:5]}"
            if clock_text:
                date_text += f" {clock_text}"
            status_label = "已确认日历约束" if str(event.get("status") or "confirmed") in {"confirmed", "active"} else "待确认日历记录"
            calendar_constraints.append(f"- {title}｜{kind_label}｜{date_text or '今天'}｜{status_label}")
            joined = f"{title} {event.get('note', '')} {event.get('description', '')}"
            if any(token in joined for token in holiday_tokens):
                has_holiday_signal = True
        if calendar_constraints:
            calendar_constraints_block = "今天有效的日历约束（属于计划依据，不等于已经发生）：\n" + "\n".join(calendar_constraints)
        else:
            calendar_constraints_block = ""
        conflicts = calendar_snapshot.get("conflicts") if isinstance(calendar_snapshot.get("conflicts"), list) else []
        if conflicts:
            by_id = {
                str(item.get("source_calendar_id") or item.get("calendar_id") or ""): item
                for item in all_calendar_events
                if isinstance(item, dict)
            }
            for conflict in conflicts[:8]:
                winner_id = str(conflict.get("winner_id") or "")
                loser_id = str(conflict.get("loser_id") or "")
                winner = _single_line(by_id.get(winner_id, {}).get("title"), 50)
                loser = _single_line(by_id.get(loser_id, {}).get("title"), 50)
                if winner and loser:
                    state = "同优先级，需谨慎处理" if conflict.get("unresolved") else "按优先级采用前者"
                    suffix = "｜当天不生效" if not conflict.get("unresolved") else ""
                    calendar_conflict_lines.append(f"- {winner} 覆盖 {loser}｜{state}{suffix}")
        if has_holiday_signal:
            day_tone = "节假日/特殊日期"
        elif is_weekend:
            day_tone = "周末/休息日候选"
        else:
            day_tone = "普通工作日或学习日候选"
        rules = [
            f"日期：{current.strftime('%Y-%m-%d')}（{weekday}）",
            f"基础日期类型：{day_tone}",
        ]
        if special_lines:
            rules.append("今天相关的重要日期：\n" + "\n".join(special_lines))
        else:
            rules.append("今天相关的重要日期：无")
        if calendar_constraints_block:
            rules.append(calendar_constraints_block)
        if calendar_candidates:
            candidate_lines = []
            for item in calendar_candidates:
                title = _single_line(item.get("title"), 80)
                if not title:
                    continue
                date_text = _single_line(item.get("start_date") or item.get("date"), 20) or "近期"
                candidate_lines.append(f"- {title}｜{date_text}｜待确认")
            if candidate_lines:
                rules.append(
                    "近期对话待确认候选（仅供询问参考，不是事实）：\n"
                    + "\n".join(candidate_lines)
                    + "\n不得据此断言用户已经安排、正在执行或已经完成；如有必要，只能轻量询问确认。"
                )
        if calendar_conflict_lines:
            rules.append("日历重叠处理：\n" + "\n".join(calendar_conflict_lines))
        timeline_lines: list[str] = []
        current_phase = calendar_timeline.get("current_phase") if isinstance(calendar_timeline.get("current_phase"), list) else []
        if current_phase:
            phase_text = "、".join(
                f"{_single_line(item.get('title'), 48)}（{_single_line(item.get('start_date'), 16)} 至 {_single_line(item.get('end_date'), 16) or '待定'}）"
                for item in current_phase[:4]
                if isinstance(item, dict) and _single_line(item.get("title"), 48)
            )
            if phase_text:
                timeline_lines.append("当前生活阶段：" + phase_text)
        rhythms = calendar_timeline.get("rhythms") if isinstance(calendar_timeline.get("rhythms"), list) else []
        if rhythms:
            rhythm_text = "、".join(
                f"{_single_line(item.get('title'), 48)}（下次 {_single_line(item.get('next_occurrence'), 16) or '按周期推算'}）"
                for item in rhythms[:5]
                if isinstance(item, dict) and _single_line(item.get("title"), 48)
            )
            if rhythm_text:
                timeline_lines.append("稳定节律参考：" + rhythm_text)
        recent_changes = calendar_timeline.get("recent_changes") if isinstance(calendar_timeline.get("recent_changes"), list) else []
        if recent_changes:
            recent_text = "、".join(
                f"{_single_line(item.get('title'), 40)}（{_single_line(item.get('occurrence_date'), 16)}）"
                for item in recent_changes[:4]
                if isinstance(item, dict) and _single_line(item.get("title"), 40)
            )
            if recent_text:
                timeline_lines.append("最近变化/余波：" + recent_text)
        transitions = calendar_timeline.get("transitions") if isinstance(calendar_timeline.get("transitions"), list) else []
        if transitions:
            transition_text = "、".join(
                f"{_single_line(item.get('date'), 16)} {_single_line(item.get('title'), 40)}"
                for item in transitions[:5]
                if isinstance(item, dict) and _single_line(item.get("title"), 40)
            )
            if transition_text:
                timeline_lines.append("接下来可能发生的转换：" + transition_text)
        uncertainties = calendar_timeline.get("uncertainties") if isinstance(calendar_timeline.get("uncertainties"), list) else []
        if uncertainties:
            uncertainty_text = "、".join(
                _single_line(item.get("title") or item.get("reason") or "待确认变化", 44)
                for item in uncertainties[:4]
                if isinstance(item, dict)
            )
            if uncertainty_text:
                timeline_lines.append("仍不确定的部分：" + uncertainty_text)
        if timeline_lines:
            rules.append(
                "生活时间线（用于保持跨日连续，不等于执行事实）：\n"
                + "\n".join(f"- {line}" for line in timeline_lines)
                + "\n不要因为某一条当天计划就擅自结束或改写当前生活阶段；只有用户明确确认或日历明确记录了转换，才改变长期背景。稳定节律是默认倾向，临时事件可以改变当天，不必抹掉长期节律。存在待确认冲突时保留不确定性，用‘可能/先按目前记录’表达。"
            )
        rules.append(
            "日程判断：先看日期语境,再看人格设定。工作日可以有上课/上班；周末要更松,可以晚起、休息、出门、补一点自己的事；节假日/假期要明显区别于普通日,可以有庆祝、出行、宅家、已明确关系安排或假期拖延。"
        )
        rules.append(
            "如果人格、日程专用设定或重要日期备注里写了调休、补班、补课、考试、值班等例外,优先按这些例外来写。不要凭空塞入身份里没有的校园、职场或节日细节。"
        )
        if calendar_constraints or timeline_lines:
            rules.append(
                "日历使用边界：它提供生活阶段、节律和变化线索，不替代当前会话事实，也不自动删除日程。用户本轮明确说法优先；记录不确定时不要把推断写成确定事实。"
            )
        return "\n".join(rules)

    def _calendar_day_flags(self, now: datetime | None = None) -> dict[str, Any]:
        current = now or self._environment_now()
        is_weekend = current.weekday() >= 5
        builtin_holidays = {"01-01", "05-01", "10-01"}
        month_day = current.strftime("%m-%d")
        today_dates = [
            entry
            for entry in self._get_relevant_important_dates(now=current)
            if _safe_int(entry.get("_days_until"), 999) == 0
        ]
        holiday_tokens = (
            "节",
            "节日",
            "假",
            "假期",
            "放假",
            "休息",
            "旅行",
            "春节",
            "元旦",
            "清明",
            "端午",
            "中秋",
            "国庆",
            "劳动",
        )
        override_tokens = ("调休", "补班", "补课", "考试", "值班", "加班", "返校")
        has_holiday_signal = month_day in builtin_holidays
        has_override_signal = False
        has_calendar_context = False
        has_calendar_holiday_signal = False
        has_calendar_school_work = False
        calendar_snapshot = {}
        snapshot_getter = getattr(self, "_agenda_calendar_snapshot", None)
        if callable(snapshot_getter):
            try:
                candidate = snapshot_getter(current.date().isoformat(), now=current)
                if isinstance(candidate, dict):
                    calendar_snapshot = candidate
            except Exception:
                calendar_snapshot = {}
        calendar_events = calendar_snapshot.get("effective_events", calendar_snapshot.get("events", []))
        if isinstance(calendar_events, list):
            for item in calendar_events:
                if not isinstance(item, dict):
                    continue
                if str(item.get("status") or "confirmed") not in {"confirmed", "active"}:
                    continue
                has_calendar_context = True
                joined = _single_line(
                    f"{item.get('title', '')} {item.get('note', '')} {item.get('description', '')}",
                    180,
                )
                if any(token in joined for token in holiday_tokens):
                    has_calendar_holiday_signal = True
                if any(token in joined for token in ("上学", "上课", "放学", "学校", "上班", "通勤", "值班", "会议", "考试", "补课")):
                    has_calendar_school_work = True
                if any(token in joined for token in override_tokens):
                    has_override_signal = True
        for entry in today_dates:
            if not isinstance(entry, dict):
                continue
            joined = _single_line(
                f"{entry.get('title', '')} {entry.get('type', '')} {entry.get('note', '')}",
                160,
            )
            if any(token in joined for token in holiday_tokens):
                has_holiday_signal = True
            if any(token in joined for token in override_tokens):
                has_override_signal = True
        schedule_prompt = self._get_schedule_planning_prompt()
        if any(token in schedule_prompt for token in override_tokens):
            has_override_signal = True
        return {
            "is_weekend": is_weekend,
            "has_holiday_signal": has_holiday_signal or has_calendar_holiday_signal,
            "has_override_signal": has_override_signal,
            "has_calendar_context": has_calendar_context,
            "has_calendar_school_work": has_calendar_school_work,
            "calendar_snapshot": calendar_snapshot,
        }

    def _plan_conflicts_with_calendar(self, items: list[dict[str, str]], now: datetime | None = None) -> bool:
        """Report only explicit unresolved calendar conflicts.

        A plan can be a reasonable interpretation of a phase, a rhythm, or a
        user correction.  Keyword matching (for example, treating every
        ``上学`` row as invalid during a vacation) made the calendar a hidden
        hard filter and caused the companion to rewrite its own life.  The
        planner prompt now receives the timeline and can resolve ambiguity in
        prose; this hook is reserved for genuinely unresolved overlaps.
        """

        if not items:
            return False
        getter = getattr(self, "_agenda_calendar_timeline", None)
        if not callable(getter):
            return False
        try:
            timeline = getter(now=(now or self._environment_now()), history_days=0, horizon_days=1)
        except Exception:
            return False
        conflicts = timeline.get("conflicts") if isinstance(timeline, dict) else []
        return any(isinstance(item, dict) and item.get("unresolved") for item in (conflicts or []))

    def _is_micro_plan_activity(self, text: str) -> bool:
        normalized = _single_line(text, 160)
        if not normalized:
            return False
        length = len(normalized)
        instant_markers = (
            "看了一眼",
            "瞥了一眼",
            "拍了一下",
            "拍了下",
            "翻了个身",
            "揉了揉",
            "抬头看",
            "关掉闹钟",
            "叫了一声",
            "应了一声",
            "顺手点开",
        )
        if any(marker in normalized for marker in instant_markers):
            return length <= 30
        generic_short_markers = ("一下", "一眼", "一瞬", "顺手", "刚好", "忽然")
        continuity_markers = (
            "慢慢",
            "继续",
            "待着",
            "坐着",
            "趴着",
            "整理",
            "收拾",
            "吃饭",
            "洗漱",
            "发呆",
            "看剧",
            "听歌",
            "出门",
            "路上",
            "吹风",
            "睡前",
            "饭后",
            "午休",
            "收尾",
        )
        if any(marker in normalized for marker in generic_short_markers) and not any(
            marker in normalized for marker in continuity_markers
        ):
            return length <= 22
        return False

    def _plan_has_excess_micro_segments(self, items: list[dict[str, str]]) -> bool:
        if not items:
            return False
        micro_count = 0
        for item in items:
            if not isinstance(item, dict):
                continue
            if self._is_micro_plan_activity(str(item.get("activity") or "")):
                micro_count += 1
        return micro_count >= max(2, len(items) // 4)

    def _is_abstract_plan_activity(self, text: str) -> bool:
        normalized = _single_line(text, 180)
        if not normalized:
            return False
        concrete_markers = (
            "起床", "赖床", "洗漱", "吃", "喝", "走", "坐", "趴", "靠", "收拾", "整理",
            "看", "听", "出门", "回家", "写", "刷", "逛", "吹风", "洗碗", "看剧", "躺",
            "翻", "换鞋", "背上", "拿着", "关灯", "开窗", "买", "收声", "聊天", "做饭",
        )
        abstract_markers = (
            "思绪", "心情", "气息", "余韵", "碎片", "温柔", "柔软", "飘忽", "微醺", "依恋",
            "恍惚", "生活感", "画面", "感觉", "梦里", "脑海里", "最后闪过", "随着光线",
        )
        if any(marker in normalized for marker in concrete_markers):
            abstract_count = sum(1 for marker in abstract_markers if marker in normalized)
            return abstract_count >= 3 and len(normalized) <= 22
        abstract_count = sum(1 for marker in abstract_markers if marker in normalized)
        return abstract_count >= 2

    def _plan_has_excess_abstract_segments(self, items: list[dict[str, str]]) -> bool:
        if not items:
            return False
        abstract_count = 0
        for item in items:
            if not isinstance(item, dict):
                continue
            if self._is_abstract_plan_activity(str(item.get("activity") or "")):
                abstract_count += 1
        return abstract_count >= max(2, len(items) // 3)

    @staticmethod
    def _plan_activity_signature(text: str) -> str:
        normalized = _single_line(text, 180)
        if not normalized:
            return ""
        category_rules = (
            ("起床", ("起床", "醒来", "睡醒", "赖床", "闹钟", "被窝")),
            ("洗漱", ("洗漱", "刷牙", "洗脸", "梳头", "镜子", "卫生间")),
            ("早餐", ("早餐", "早饭", "面包", "牛奶", "豆浆", "粥")),
            ("正餐", ("午饭", "晚饭", "吃饭", "做饭", "干饭", "饭桌", "摆碗", "点外卖")),
            ("通勤出门", ("出门", "路上", "公交", "地铁", "校门", "换鞋", "背包", "打车")),
            ("校园课程", ("上课", "下课", "教室", "课间", "老师", "同桌", "黑板", "班会")),
            ("补课考试", ("补课", "考试", "测验", "卷子", "复习", "考场", "错题")),
            ("学习作业", ("作业", "自习", "刷题", "数学", "英语", "课本", "笔记", "书包")),
            ("工作事务", ("上班", "工位", "会议", "打卡", "下班", "同事", "项目", "文档")),
            ("家务整理", ("收拾", "整理", "扫地", "洗碗", "洗衣", "归位", "桌面", "房间")),
            ("休息摸鱼", ("午休", "休息", "摸鱼", "躺", "趴", "沙发", "发呆", "缓一会")),
            ("娱乐放松", ("看剧", "追番", "游戏", "刷短视频", "听歌", "小说", "漫画")),
            ("社交互动", ("聊天", "朋友", "家人", "消息", "电话", "群聊", "回复", "打开对话框")),
            ("购物外食", ("买", "便利店", "超市", "奶茶", "饮料", "小吃", "逛")),
            ("户外散步", ("散步", "走一段", "吹风", "公园", "楼下", "河边", "阳台", "开窗")),
            ("运动身体", ("运动", "跑步", "拉伸", "散操", "瑜伽", "出汗")),
            ("洗澡睡前", ("洗澡", "睡前", "关灯", "上床", "准备睡", "入睡", "枕头")),
        )
        hits: list[str] = []
        for label, tokens in category_rules:
            if any(token in normalized for token in tokens):
                hits.append(label)
            if len(hits) >= 2:
                break
        if hits:
            return "+".join(hits)
        compact = re.sub(r"[，。！？、,.!?；;：:\s]+", "", normalized)
        return compact[:8]

    def _plan_signature(self, items: list[dict[str, Any]]) -> list[str]:
        signatures: list[str] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            signature = self._plan_activity_signature(
                f"{item.get('activity', '')} {item.get('message_seed', '')}"
            )
            if signature:
                signatures.append(signature)
        return signatures

    def _format_recent_daily_plan_history_for_prompt(self, limit: int = 5) -> str:
        history = self._recent_daily_plan_history_entries()
        rows: list[str] = []
        for entry in history[-limit:]:
            if not isinstance(entry, dict):
                continue
            date_text = _single_line(entry.get("date"), 16)
            signatures = entry.get("signature")
            if not isinstance(signatures, list):
                signatures = []
            samples = entry.get("sample")
            if not isinstance(samples, list):
                samples = []
            skeleton = " / ".join(_single_line(part, 20) for part in signatures[:12] if part)
            sample_text = "；".join(_single_line(part, 46) for part in samples[:4] if part)
            if skeleton:
                line = f"- {date_text}: {skeleton}"
                if sample_text:
                    line += f"\n  代表活动: {sample_text}"
                rows.append(line)
        return "\n".join(rows) if rows else "暂无最近日程历史。"

    def _plan_repetition_score(self, items: list[dict[str, str]]) -> float:
        signatures = self._plan_signature(items)
        if not signatures:
            return 0.0
        current_set = set(signatures)
        history = self._recent_daily_plan_history_entries()
        best_score = 0.0
        for entry in history[-5:]:
            if not isinstance(entry, dict):
                continue
            old_signatures = entry.get("signature")
            if not isinstance(old_signatures, list) or not old_signatures:
                continue
            old_values = [str(value) for value in old_signatures if value]
            old_set = set(old_values)
            if not old_set:
                continue
            jaccard = len(current_set & old_set) / max(1, len(current_set | old_set))
            paired = min(len(signatures), len(old_values))
            same_positions = 0
            for idx in range(paired):
                if signatures[idx] == old_values[idx]:
                    same_positions += 1
            ordered = same_positions / max(1, paired)
            best_score = max(best_score, jaccard * 0.65 + ordered * 0.35)
        return best_score

    def _plan_is_too_repetitive(self, items: list[dict[str, str]]) -> bool:
        if not items:
            return False
        signatures = self._plan_signature(items)
        if len(signatures) >= 6:
            dominant_count = max(signatures.count(signature) for signature in set(signatures))
            if dominant_count >= max(4, len(signatures) // 2 + 1):
                return True
        return self._plan_repetition_score(items) >= 0.62

    def _daily_plan_history_entry(self, plan: dict[str, Any]) -> dict[str, Any] | None:
        if not isinstance(plan, dict):
            return None
        items = plan.get("items")
        if not isinstance(items, list) or not items:
            return None
        plan_date = _single_line(plan.get("date"), 16) or _today_key()
        sample: list[str] = []
        compact_items: list[dict[str, str]] = []
        for item in items[:6]:
            if not isinstance(item, dict):
                continue
            time_text = _single_line(item.get("time"), 8)
            activity = _single_line(item.get("activity"), 52)
            if activity:
                sample.append(f"{time_text} {activity}".strip())
        for item in items[:18]:
            if not isinstance(item, dict):
                continue
            compact_items.append(
                {
                    "time": _single_line(item.get("time"), 20),
                    "activity": _single_line(item.get("activity") or item.get("title"), 180),
                    "mood": _single_line(item.get("mood"), 80),
                    "message_seed": _single_line(item.get("message_seed"), 220),
                }
            )
        entry = {
            "date": plan_date,
            "generated_at": _single_line(plan.get("generated_at"), 20) or self._environment_now().strftime("%Y-%m-%d %H:%M"),
            "source": _single_line(plan.get("source"), 16),
            "signature": self._plan_signature(items),
            "sample": sample,
            "items": compact_items,
        }
        return entry

    def _recent_daily_plan_history_entries(self) -> list[dict[str, Any]]:
        history = self.data.get("daily_plan_history", [])
        entries = [entry for entry in history if isinstance(entry, dict)] if isinstance(history, list) else []
        known_dates = {_single_line(entry.get("date"), 16) for entry in entries}
        current_entry = self._daily_plan_history_entry(self.data.get("daily_plan", {}))
        if current_entry and _single_line(current_entry.get("date"), 16) not in known_dates:
            entries.append(current_entry)
        return entries

    def _remember_daily_plan_history(self, plan: dict[str, Any]) -> None:
        entry = self._daily_plan_history_entry(plan)
        if not entry:
            return
        plan_date = _single_line(entry.get("date"), 16)
        history = self.data.setdefault("daily_plan_history", [])
        if not isinstance(history, list):
            history = []
            self.data["daily_plan_history"] = history
        history[:] = [
            old
            for old in history
            if not (isinstance(old, dict) and _single_line(old.get("date"), 16) == plan_date)
        ]
        history.append(entry)
        del history[:-10]

    def _add_important_date_entry(self, value: str) -> tuple[bool, str]:
        parts = value.split(maxsplit=2)
        if len(parts) < 2:
            return False, "格式：陪伴 日期添加 <标题> <YYYY-MM-DD或MM-DD> [备注]"
        title = _single_line(parts[0], 40)
        date_text = _single_line(parts[1], 20)
        note = _single_line(parts[2], 120) if len(parts) >= 3 else ""
        parsed = self._parse_date_value(date_text)
        if parsed is None:
            return False, "日期格式不对,请用 YYYY-MM-DD 或 MM-DD。"
        repeat_yearly = len(date_text) == 5
        entry = {
            "id": f"date-{int(_now_ts())}-{random.randint(1000, 9999)}",
            "title": title,
            "date": date_text,
            "type": "重要日期",
            "note": note,
            "enabled": True,
            "repeat_yearly": repeat_yearly,
                "remind_days": runtime_persona_setting(self, "important_date_lookahead_days", 7),
            "priority": 50,
            "created_at": self._environment_now().strftime("%Y-%m-%d %H:%M"),
        }
        self.data.setdefault("important_dates", []).append(entry)
        return True, f"已添加重要日期：{title}｜{date_text}"

    def _remove_important_date_entry(self, value: str) -> str:
        keyword = _single_line(value, 40)
        if not keyword:
            return "请提供要删除的日期标题关键词。"
        entries = self.data.setdefault("important_dates", [])
        if not isinstance(entries, list):
            self.data["important_dates"] = []
            return "重要日期列表为空。"
        kept = []
        removed = []
        for entry in entries:
            title = str(entry.get("title", "")) if isinstance(entry, dict) else ""
            if keyword in title:
                removed.append(title)
            else:
                kept.append(entry)
        self.data["important_dates"] = kept
        if not removed:
            return "没有找到匹配的重要日期。"
        return "已删除：\n" + "\n".join(f"- {item}" for item in removed)

    def _format_important_dates(self) -> str:
        entries = self.data.get("important_dates", [])
        if not isinstance(entries, list) or not entries:
            return "还没有重要日期。"
        lines = ["重要日期条目："]
        today = self._environment_now().date()
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            next_day = self._next_occurrence(entry)
            suffix = ""
            if next_day:
                days = (next_day - today).days
                suffix = "｜今天" if days == 0 else f"｜{days} 天后"
            enabled = "启用" if entry.get("enabled", True) else "停用"
            repeat = "每年" if entry.get("repeat_yearly", True) else "一次"
            lines.append(
                f"- {entry.get('title')}｜{entry.get('date')}｜{repeat}｜{enabled}{suffix}｜{entry.get('note', '')}"
            )
        return "\n".join(lines)

    def _is_daily_plan_due(self) -> bool:
        plan_minutes = self._parse_hhmm_to_minutes(runtime_persona_setting(self, "daily_plan_time", "07:30"))
        if plan_minutes is None:
            plan_minutes = 7 * 60 + 30
        now = self._environment_now()
        return now.hour * 60 + now.minute >= plan_minutes

    def _daily_plan_due_minutes(self) -> int:
        plan_minutes = self._parse_hhmm_to_minutes(runtime_persona_setting(self, "daily_plan_time", "07:30"))
        if plan_minutes is None:
            return 7 * 60 + 30
        return plan_minutes

    def _is_plan_date_active(self, plan_date: str) -> bool:
        plan_date = str(plan_date or "").strip()
        if not plan_date:
            return False
        today = self._environment_now().date()
        today_key = _date_key(today)
        if plan_date == today_key:
            return True
        yesterday_key = _date_key(today - timedelta(days=1))
        if plan_date != yesterday_key:
            return False
        now_minutes = self._environment_now_minutes()
        return now_minutes < self._daily_plan_due_minutes()

    def _get_active_plan(self) -> dict[str, Any]:
        plan = self.data.get("daily_plan", {})
        if isinstance(plan, dict) and self._is_plan_date_active(plan.get("date")):
            return plan
        return {}

    def _effective_plan_now_minutes(self, plan_date: str) -> int | None:
        plan_date = str(plan_date or "").strip()
        if not self._is_plan_date_active(plan_date):
            return None
        now_minutes = self._environment_now_minutes()
        if plan_date == _today_key():
            return now_minutes
        return 24 * 60 + now_minutes

    def _is_sleepy_plan_item(self, item: dict[str, Any] | None) -> bool:
        if not isinstance(item, dict):
            return False
        text = " ".join(
            _single_line(item.get(key), 100)
            for key in ("activity", "mood", "message_seed")
            if _single_line(item.get(key), 100)
        )
        if not text:
            return False
        if re.search(r"继续睡|睡回去|重新入睡|再次入睡|回笼觉", text):
            return True
        if re.search(
            r"自然醒|睡醒|醒来|醒后|刚醒|醒了|已醒|醒着|清醒|睁眼|起床|起身|洗漱|"
            r"不睡|没睡|未睡|还没睡|睡不着|失眠",
            text,
        ):
            return False
        return bool(
            re.search(
                r"睡觉|睡眠|入睡|熟睡|浅睡|午睡|午休|小睡|补觉|回笼觉|打盹|"
                r"眯(?:一|半)?会(?:儿)?|梦乡|被窝|准备睡|睡前|继续睡|睡回去|熄灯休息",
                text,
            )
        )

    def _segment_end_minutes(
        self,
        start: int,
        item: dict[str, Any] | None,
        *,
        next_start: int | None = None,
    ) -> int:
        if next_start is not None:
            return next_start
        if self._is_sleepy_plan_item(item):
            return min(24 * 60 + 240, start + 240)
        return min(24 * 60 + 120, start + 180)

    def _plan_item_end_minutes(
        self,
        start: int,
        item: dict[str, Any] | None,
        *,
        next_start: int | None = None,
    ) -> int:
        explicit = self._parse_hhmm_to_minutes((item or {}).get("end")) if isinstance(item, dict) else None
        if explicit is not None:
            if explicit <= start:
                explicit += 24 * 60
            duration = explicit - start
            if 10 <= duration <= 12 * 60:
                if next_start is not None:
                    normalized_next = next_start + (24 * 60 if next_start <= start else 0)
                    explicit = min(explicit, normalized_next)
                return explicit
        if next_start is not None:
            return next_start + (24 * 60 if next_start <= start else 0)
        return self._segment_end_minutes(start, item)

    def _normalized_plan_item_starts(self, items: Any) -> list[int | None]:
        if not isinstance(items, list):
            return []
        normalized: list[int | None] = []
        day_offset = 0
        previous_raw: int | None = None
        for item in items:
            raw = self._parse_hhmm_to_minutes(item.get("time")) if isinstance(item, dict) else None
            if raw is None:
                normalized.append(None)
                continue
            if previous_raw is not None and raw < previous_raw:
                day_offset += 24 * 60
            normalized.append(raw + day_offset)
            previous_raw = raw
        return normalized

    def _normalize_plan_item_intervals(self, items: Any) -> bool:
        if not isinstance(items, list):
            return False
        starts = self._normalized_plan_item_starts(items)
        changed = False
        for index, item in enumerate(items):
            if not isinstance(item, dict) or starts[index] is None:
                continue
            start = int(starts[index])
            next_start = next((value for value in starts[index + 1 :] if value is not None), None)
            end = self._plan_item_end_minutes(start, item, next_start=next_start)
            end_text = self._minutes_to_hhmm(end)
            if _single_line(item.get("end"), 8) != end_text:
                item["end"] = end_text
                changed = True
            lifecycle = _single_line(item.get("lifecycle_status"), 20).lower()
            if lifecycle not in {"planned", "changed", "cancelled", "deferred"}:
                item["lifecycle_status"] = "planned"
                changed = True
            basis = self._normalize_schedule_basis(item.get("basis"), default=["coarse_plan"])
            if item.get("basis") != basis:
                item["basis"] = basis
                changed = True
            confidence = min(1.0, _safe_float(item.get("confidence"), 0.72))
            if item.get("confidence") != confidence:
                item["confidence"] = confidence
                changed = True
        return changed

    @staticmethod
    def _normalize_schedule_lifecycle_status(value: Any) -> str:
        aliases = {
            "planned": "planned", "计划": "planned", "未开始": "planned",
            "active": "active", "进行": "active", "进行中": "active",
            "completed": "completed", "完成": "completed", "已完成": "completed",
            "changed": "changed", "变更": "changed", "已变更": "changed",
            "cancelled": "cancelled", "canceled": "cancelled", "取消": "cancelled", "已取消": "cancelled",
            "deferred": "deferred", "postponed": "deferred", "顺延": "deferred", "延期": "deferred",
        }
        return aliases.get(_single_line(value, 20).lower(), "")

    def _schedule_window_runtime_status(
        self,
        start: int,
        end: int,
        *,
        plan_date: str = "",
        explicit_status: Any = "",
    ) -> str:
        explicit = self._normalize_schedule_lifecycle_status(explicit_status)
        if explicit == "cancelled":
            return explicit
        date_text = _single_line(plan_date, 16)
        now_minutes = self._effective_plan_now_minutes(date_text) if date_text else self._environment_now_minutes()
        if now_minutes is None:
            today = _today_key()
            return "completed" if date_text and date_text < today else "planned"
        normalized_end = int(end)
        if normalized_end <= start:
            normalized_end += 24 * 60
        if now_minutes < start:
            runtime = "planned"
        elif now_minutes >= normalized_end:
            runtime = "completed"
        else:
            runtime = "active"
        if explicit == "changed" and runtime != "completed":
            return "changed"
        return runtime

    def _plan_item_runtime_status(self, plan: dict[str, Any], item: dict[str, Any], index: int = -1) -> str:
        # Lifecycle display must come from canonical evidence, never from the
        # clock alone.  Keep the legacy helper signature for callers, but map
        # old lifecycle values through a conservative planned/unknown view.
        if isinstance(item, dict):
            legacy = self._normalize_schedule_lifecycle_status(item.get("lifecycle_status"))
            if legacy == "cancelled":
                return "cancelled"
            if legacy == "changed":
                return "changed"
            if legacy == "deferred":
                return "deferred"
            evidence = _single_line(item.get("evidence_kind"), 48).lower()
            eligibility = _single_line(item.get("fact_eligibility"), 48).lower()
            status = _single_line(item.get("status"), 32).lower()
            if evidence in {"interaction", "tool_action", "external_record"} and eligibility in {"current_observed", "history_observed"}:
                if status in {"active", "completed", "partially_completed"}:
                    return status
            if evidence == "self_state_commit" and eligibility == "current_internal":
                return "active"
            plan_date = str((plan or {}).get("date") or item.get("date") or "")
            try:
                canonical = normalize_plan_item(
                    {**item, "date": plan_date or _today_key(), "subject_actor_id": item.get("subject_actor_id") or "bot_self"},
                    plan_id=str(item.get("plan_id") or ""),
                    now=self._environment_now(),
                )
                phase = _single_line(canonical.get("temporal_phase"), 16).lower()
                if phase == "past":
                    # ``normalize_plan_item`` evaluates a HH:MM value on the
                    # calendar date alone.  A plan that deliberately rolls
                    # past midnight therefore looks stale even while its
                    # normalized schedule axis is still current/upcoming.
                    # Preserve the evidence status as ``planned`` in that
                    # case; the separate display status may still project the
                    # wall-clock phase as active.
                    items = plan.get("items") if isinstance(plan, dict) else None
                    starts = self._normalized_plan_item_starts(items)
                    start = starts[index] if isinstance(items, list) and 0 <= index < len(starts) else None
                    if start is not None:
                        next_start = next((value for value in starts[index + 1 :] if value is not None), None)
                        end = self._plan_item_end_minutes(start, item, next_start=next_start)
                        clock_phase = self._schedule_window_runtime_status(
                            start,
                            end,
                            plan_date=plan_date,
                            explicit_status=item.get("lifecycle_status"),
                        )
                        if clock_phase in {"planned", "active"}:
                            return "planned"
                    return "unknown"
                return "planned"
            except Exception:
                return "planned"
        items = plan.get("items") if isinstance(plan, dict) else None
        starts = self._normalized_plan_item_starts(items)
        start = starts[index] if isinstance(items, list) and 0 <= index < len(starts) else self._parse_hhmm_to_minutes(item.get("time"))
        if start is None:
            return "planned"
        next_start = None
        if isinstance(items, list) and index >= 0:
            next_start = next((value for value in starts[index + 1 :] if value is not None), None)
        end = self._plan_item_end_minutes(start, item, next_start=next_start)
        return self._schedule_window_runtime_status(
            start,
            end,
            plan_date=str((plan or {}).get("date") or ""),
            explicit_status=item.get("lifecycle_status"),
        )

    def _plan_item_display_status(self, plan: dict[str, Any], item: dict[str, Any], index: int = -1) -> str:
        """Return the user-facing clock phase without upgrading it to execution evidence."""

        canonical = self._plan_item_runtime_status(plan, item, index)
        if canonical in {"cancelled", "deferred", "overridden", "active", "completed", "partially_completed"}:
            return canonical
        items = plan.get("items") if isinstance(plan, dict) else None
        starts = self._normalized_plan_item_starts(items)
        start = starts[index] if isinstance(items, list) and 0 <= index < len(starts) else self._parse_hhmm_to_minutes(item.get("time"))
        if start is None:
            return canonical or "planned"
        next_start = next((value for value in starts[index + 1 :] if value is not None), None) if index >= 0 else None
        end = self._plan_item_end_minutes(start, item, next_start=next_start)
        return self._schedule_window_runtime_status(
            start,
            end,
            plan_date=str((plan or {}).get("date") or item.get("date") or ""),
            explicit_status=item.get("lifecycle_status"),
        )

    def _parse_hhmm_to_minutes(self, value: Any) -> int | None:
        match = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", str(value or ""))
        if not match:
            return None
        hour, minute = int(match.group(1)), int(match.group(2))
        if hour > 23 or minute > 59:
            return None
        return hour * 60 + minute

    def _minutes_to_hhmm(self, minutes: int) -> str:
        minutes = max(0, int(minutes))
        wrapped = minutes % (24 * 60)
        return f"{wrapped // 60:02d}:{wrapped % 60:02d}"

    async def _generate_daily_plan(self) -> dict[str, Any]:
        await self._ensure_yesterday_conversation_summary()
        await self._ensure_yesterday_screen_diary_context()
        await self._maybe_settle_skill_growth(force=True)
        return await generate_daily_plan(self)

    def _get_schedule_planning_prompt(self) -> str:
        return get_schedule_planning_prompt(self)

    def _build_daily_plan_prompt(self, now: str, memory_companion_context: str = "") -> str:
        return build_daily_plan_prompt(self, now, memory_companion_context=memory_companion_context)

    def _build_daily_plan_prompt_section(
        self,
        now: str,
        memory_companion_context: str = "",
    ) -> PromptSection:
        return build_daily_plan_prompt_section(
            self,
            now,
            memory_companion_context=memory_companion_context,
        )

    async def _ensure_yesterday_conversation_summary(self, force: bool = False) -> dict[str, Any]:
        today = _today_key()
        cached = self.data.get("yesterday_conversation_summary", {})
        if (
            isinstance(cached, dict)
            and cached.get("date") == today
            and cached.get("scope") == "owner_private_only"
            and not force
        ):
            return cached
        raw_text = await self._collect_yesterday_conversation_text()
        if not raw_text:
            summary = {
                "date": today,
                "source_date": _date_key(date.today() - timedelta(days=1)),
                "summary": "暂无可用的昨日完整对话摘要。",
                "residues": [],
                "schedule_reference": "无明确可继承影响。",
                "dream_reference": "无明确可继承碎片。",
                "scope": "owner_private_only",
                "raw_excerpt_chars": 0,
            }
        else:
            summary = await self._summarize_yesterday_conversation_for_schedule(raw_text)
        async with self._data_lock:
            self.data["yesterday_conversation_summary"] = summary
            self._save_data_sync(sections={"yesterday_conversation_summary"})
        return summary

    async def _collect_yesterday_conversation_text(self) -> str:
        users = self.data.get("users", {})
        if not isinstance(users, dict):
            return ""
        now_dt = self._environment_now()
        yesterday = now_dt.date() - timedelta(days=1)
        start = datetime.combine(yesterday, datetime.min.time(), tzinfo=now_dt.tzinfo).timestamp()
        end = start + 24 * 3600
        sections: list[PromptSection] = []
        for user_id, raw_user in users.items():
            if not isinstance(raw_user, dict):
                continue
            if self._private_user_role(raw_user, str(user_id)) != "owner":
                continue
            umo = str(raw_user.get("umo") or "").strip()
            if not umo:
                continue
            try:
                getter = getattr(self, "_get_current_conversation_safely", None)
                if callable(getter):
                    conv = await getter(umo, label="yesterday_conversation_read")
                else:
                    conv_id = await self.context.conversation_manager.get_curr_conversation_id(umo)
                    if not conv_id:
                        continue
                    conv = await self.context.conversation_manager.get_conversation(umo, conv_id)
            except Exception as exc:
                logger.debug("读取昨日对话失败: user=%s err=%s", user_id, exc)
                continue
            if not conv:
                continue
            history = self._load_conversation_history_items(conv)
            dated_lines: list[str] = []
            undated_lines: list[str] = []
            for item in history:
                line = self._format_history_item_for_summary(item)
                if not line:
                    continue
                ts = self._history_item_timestamp(item)
                if ts is None:
                    undated_lines.append(line)
                elif start <= ts < end:
                    dated_lines.append(line)
            selected = dated_lines if dated_lines else undated_lines[-120:]
            if not selected:
                continue
            name = _single_line(raw_user.get("nickname") or user_id, 30)
            source_note = "昨日对话" if dated_lines else "最近对话（history 无时间戳,作为昨日摘要候选）"
            sections.append(
                prompt_section(
                    key=f"background.yesterday_conversation.user_{len(sections)}",
                    title=f"主要用户:{name}｜{source_note}",
                    source="daily_state",
                    content="\n".join(selected),
                )
            )
        return render_prompt_sections(
            sections,
            mode=PromptRenderMode.LABELED_BLOCK,
        ).strip()[-18000:]

    def _load_conversation_history_items(
        self,
        conversation: Conversation | None,
        *,
        tail_only: int | None = None,
    ) -> list[dict[str, Any]]:
        if conversation is None:
            return []
        raw = conversation.history or "[]"
        if tail_only is not None and tail_only > 0:
            tail = self._parse_tail_json_items(raw, tail_only)
            if tail is not None:
                return tail
        try:
            loaded = json.loads(raw)
        except Exception:
            return []
        if not isinstance(loaded, list):
            return []
        return [item for item in loaded if isinstance(item, dict)]

    @staticmethod
    def _parse_tail_json_items(raw: str, count: int) -> list[dict[str, Any]] | None:
        """仅反序列化 JSON 数组从尾部往回数 count 个 dict 元素，避免对超大
        conversation.history 全量 json.loads。

        从右向左逆序扫描元素边界：字符串按"左侧连续反斜杠个数的奇偶性"判定转义
        引号（奇数 => 转义、偶数 => 定界），从而正确区分字符串、嵌套括号与顶层
        逗号；对每个尾部元素单独解码，遇到 dict 才计数，语义与"全量解析后过滤
        dict 再取尾部 count 条"完全一致。任何解析异常、结构不符或不足 count 个
        时返回 None，由调用方回退全量解析，正确性始终有保证。
        """
        if not count or count < 1:
            return None
        text = raw.strip()
        if not (text.startswith("[") and text.endswith("]")):
            return None
        end = len(text) - 1  # 数组右括号下标
        stop = end  # 当前元素区间的右边界(不含)
        i = end - 1
        depth = 0
        in_string = False
        found: list[dict[str, Any]] = []

        def _collect(span_start: int, span_stop: int) -> bool:
            try:
                item = json.loads(text[span_start:span_stop])
            except Exception:
                return False
            if isinstance(item, dict):
                found.append(item)
                return len(found) == count
            return False

        while i >= 0:
            ch = text[i]
            if in_string:
                if ch == '"':
                    # 判定左侧连续反斜杠个数的奇偶：奇数 => 转义引号，属字符串内容
                    j = i - 1
                    bs = 0
                    while j >= 0 and text[j] == "\\":
                        bs += 1
                        j -= 1
                    if bs % 2 == 1:
                        i = j  # 跳过该反斜杠串，继续留在字符串内
                        continue
                    in_string = False
                i -= 1
                continue
            if ch == '"':
                in_string = True
            elif ch in "]}":
                depth += 1
            elif ch in "[{":
                if depth == 0:
                    # 回溯到数组左括号：当前为第一个元素
                    if _collect(i + 1, stop) and len(found) == count:
                        return found[::-1]
                    return None
                depth -= 1
            elif ch == "," and depth == 0:
                span_start = i + 1
                if _collect(span_start, stop):
                    return found[::-1]
                stop = i
            i -= 1
        return None

    def _history_item_timestamp(self, item: dict[str, Any]) -> float | None:
        for key in ("timestamp", "time", "created_at", "updated_at", "created", "date"):
            value = item.get(key)
            if value is None or value == "":
                continue
            numeric = _safe_float(value, 0)
            if numeric > 0:
                return numeric / 1000 if numeric > 10_000_000_000 else numeric
            text = str(value).strip()
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%m-%d %H:%M:%S", "%m-%d %H:%M"):
                try:
                    parsed = datetime.strptime(text, fmt)
                    if fmt.startswith("%m"):
                        parsed = parsed.replace(year=date.today().year)
                    return parsed.timestamp()
                except Exception:
                    continue
        return None

    def _format_history_item_for_summary(self, item: dict[str, Any]) -> str:
        role = _single_line(item.get("role") or item.get("type") or item.get("speaker"), 20).lower()
        if role in {"assistant", "bot", "ai"}:
            speaker = f"{runtime_persona_setting(self, 'bot_name', '小星')}(Bot回复)"
        elif role in {"user", "human"}:
            speaker = "用户"
        else:
            speaker = role or "对话"
        content = self._history_item_content_text(item)
        if not content:
            return ""
        if self._daily_proactive_archive_context_text(content):
            return ""
        ts = self._history_item_timestamp(item)
        time_prefix = self._environment_fromtimestamp(ts).strftime("%m-%d %H:%M") + " " if ts else ""
        return f"{time_prefix}{speaker}: {content}"

    def _history_item_content_text(self, item: dict[str, Any]) -> str:
        value = item.get("content")
        if value is None:
            value = item.get("message") or item.get("text") or item.get("content_text")
        if isinstance(value, str):
            return _single_line(value, 260)
        if isinstance(value, list):
            parts: list[str] = []
            for part in value:
                if isinstance(part, str):
                    parts.append(part)
                elif isinstance(part, dict):
                    text = part.get("text") or part.get("content") or part.get("message")
                    if text:
                        parts.append(str(text))
                    elif str(part.get("type") or "").lower() == "image":
                        parts.append("[图片]")
            return _single_line(" ".join(parts), 260)
        if isinstance(value, dict):
            return _single_line(value.get("text") or value.get("content") or json.dumps(value, ensure_ascii=False), 260)
        return ""

    async def _summarize_yesterday_conversation_for_schedule(self, raw_text: str) -> dict[str, Any]:
        today = _today_key()
        source_date = _date_key(date.today() - timedelta(days=1))
        section = prompt_section(
            key="background.yesterday_summary",
            title="昨日对话残留摘要",
            source="daily_state",
            content=f"""
请阅读下面的昨日/最近完整对话材料,为今天的日程和梦境生成提炼参考摘要。

目标不是复述聊天,而是找出可能延续到今天的“残留影响”：身体状态、饮食/作息、情绪余波、关系变化、未完成约定、收到/送出的东西、外出计划、压力来源、被安慰/被打断的事、梦境可能用到的物件/颜色/气味/半句话等。

重要原则：
1. 只根据对话内容做合理推断,不要硬套固定事件类型。
2. 如果某个行为可能带来身体或日程后果,用抽象逻辑表达：饮食、睡眠、天气、运动、情绪刺激、约定、礼物、争执、安慰等都可能改变今天的体力、胃口、心情、出门意愿或主动话题。
3. 影响可以很轻,也可以没有。不要为了制造剧情强行让今天出事。
4. 摘要要给日程模型用,所以写成可执行参考,不是聊天回复。
5. 梦境参考只提炼碎片和情绪质感,不要编完整梦。
6. 饮食偏好要有衰退：某个菜名、零食或口味反复出现时,只当“近期聊过/需要避错”的软背景,不要要求今天继续安排购买、带饭、留一份或一起吃。用户说“不吃/不喜欢/不要/避开某食物”时,只写成“相关时避开该食物”,不要写成今天必须准备替代餐食。
7. 严格区分说话人：只有“用户”行能写成用户真实信息；“Bot回复”里的我在做什么、身体/心情/日程、动作描写或生活片段，只能视为 Bot 当时的拟人化表达，不能当作用户事实、现实证据或今天必须继承的事件。
8. 只有用户明确确认、提出或约定的事，才可以进入计划/未完成约定；Bot 自称的吃饭、整理、犯困、走动、创作等状态不要转成稳定记忆或现实日程。

对话材料：
{raw_text}

只输出 JSON：
{{
  "summary": "昨日对话的一句话概括",
  "residues": [
    {{"type": "身体/情绪/关系/计划/物件/梦境碎片", "content": "可延续影响", "strength": "轻/中/强"}}
  ],
  "schedule_reference": "今天生成日程时应如何自然继承这些残留；没有就写无明确影响",
  "dream_reference": "今天梦境/梦境碎片可以参考的物件、感官、半句话或情绪；没有就写无明确碎片"
}}
""".strip(),
        )
        prompt = render_prompt_sections(
            [section],
            mode=PromptRenderMode.BODY_ONLY,
        )
        raw = await self._llm_call(
            prompt,
            max_tokens=650,
            provider_id=self._task_provider(
                runtime_persona_setting(self, "history_summary_provider_id", ""),
                runtime_persona_setting(self, "daily_plan_provider_id", ""),
                runtime_persona_setting(self, "mai_style_provider_id", ""),
            ),
            task="yesterday_summary",
        )
        payload = self._extract_json_payload(raw or "")
        if not isinstance(payload, dict):
            return {
                "date": today,
                "source_date": source_date,
                "summary": _single_line(raw_text, 180) or "昨日对话有记录,但摘要生成失败。",
                "residues": [],
                "schedule_reference": "可把昨日互动作为轻微关系和情绪背景,不要强行改写今日主线。",
                "dream_reference": "可从昨日对话里的物件、语气和半句话提取梦境碎片。",
                "scope": "owner_private_only",
                "raw_excerpt_chars": len(raw_text),
            }
        residues = payload.get("residues", [])
        if not isinstance(residues, list):
            residues = []
        normalized_residues = []
        for item in residues[:8]:
            if not isinstance(item, dict):
                continue
            content = _single_line(item.get("content"), 120)
            if not content:
                continue
            normalized_residues.append({
                "type": _single_line(item.get("type"), 24) or "残留",
                "content": content,
                "strength": _single_line(item.get("strength"), 8) or "轻",
            })
        return {
            "date": today,
            "source_date": source_date,
            "summary": _single_line(payload.get("summary"), 180) or "昨日对话有一些可延续的情绪和生活残留。",
            "residues": normalized_residues,
            "schedule_reference": _single_line(payload.get("schedule_reference"), 220) or "作为轻微背景承接,不要强行改写今日主线。",
            "dream_reference": _single_line(payload.get("dream_reference"), 220) or "从昨日对话的物件、感官和半句话中轻取梦境碎片。",
            "scope": "owner_private_only",
            "raw_excerpt_chars": len(raw_text),
        }

    def _format_yesterday_conversation_summary_for_prompt(self) -> str:
        summary = self.data.get("yesterday_conversation_summary", {})
        if not isinstance(summary, dict) or summary.get("date") != _today_key():
            return "暂无昨日完整对话摘要。"
        schedule_reference = self._decay_schedule_food_reference_text(
            summary.get("schedule_reference"),
            field="yesterday_conversation.schedule_reference",
        )
        dream_reference = self._decay_schedule_food_reference_text(
            summary.get("dream_reference"),
            field="yesterday_conversation.dream_reference",
            dream=True,
        )
        lines = [
            f"来源日期：{summary.get('source_date') or '昨日'}",
            f"概括：{_single_line(summary.get('summary'), 180)}",
            f"日程参考：{schedule_reference}",
            f"梦境参考：{dream_reference}",
        ]
        residues = summary.get("residues", [])
        if isinstance(residues, list) and residues:
            lines.append("残留变量：")
            for item in residues[:8]:
                if not isinstance(item, dict):
                    continue
                content = self._decay_schedule_food_reference_text(
                    item.get("content"),
                    field="yesterday_conversation.residue",
                )
                if content:
                    lines.append(f"- {item.get('type') or '残留'}｜{content}｜强度 {item.get('strength') or '轻'}")
        return "\n".join(lines)

    @staticmethod
    def _schedule_food_reference_has_negative_preference(text: str) -> bool:
        if not text:
            return False
        negative_markers = ("不吃", "不喜欢", "不想吃", "不要", "别吃", "避开", "换别的", "代替", "别准备", "不要准备")
        food_markers = (
            "饭", "餐", "菜", "食物", "吃的", "早餐", "午饭", "晚饭", "夜宵", "零食",
            "排骨", "糖醋", "螺蛳粉", "锅包肉", "烤肠", "豆花", "冰粉", "甜口",
        )
        return any(token in text for token in negative_markers) and any(token in text for token in food_markers)

    @staticmethod
    def _schedule_food_reference_is_concrete_motif(text: str) -> bool:
        if not text:
            return False
        food_markers = (
            "糖醋排骨", "排骨", "螺蛳粉", "锅包肉", "烤肠", "豆花", "冰粉", "甜口",
            "桂花", "奶茶", "豆浆", "夜宵", "饭团", "便当",
        )
        action_markers = ("准备", "带", "买", "做", "留", "夹", "点", "抢", "一起吃", "约饭", "饭")
        return any(food in text for food in food_markers) and any(action in text for action in action_markers)

    def _decay_schedule_food_reference_text(self, text: Any, *, field: str = "", dream: bool = False) -> str:
        source = _single_line(text, 260)
        if not source:
            return ""
        if dream:
            if self._schedule_food_reference_is_concrete_motif(source):
                return (
                    "梦境里可保留少量气味或颜色质感,但具体菜名属于近期高频意象,不要让它反推今天的餐食安排。"
                )
            return source
        clauses = [part.strip() for part in re.split(r"[；;。]+", source) if _single_line(part, 160)]
        if not clauses:
            clauses = [source]
        changed = False
        kept: list[str] = []
        added_guard = False
        for clause in clauses:
            cleaned = _single_line(clause, 180)
            if self._schedule_food_reference_has_negative_preference(cleaned):
                changed = True
                if not added_guard:
                    kept.append("若今天自然聊到餐食,只记得避开对方明确不吃的食物；不要为了这个避雷主动安排带饭、备餐或替代餐食剧情")
                    added_guard = True
                continue
            if self._schedule_food_reference_is_concrete_motif(cleaned):
                changed = True
                if not added_guard:
                    kept.append("具体食物只作近期聊过的软背景,不要连续复刻成今日午饭、带饭、留一份或邀约")
                    added_guard = True
                continue
            kept.append(cleaned)
        result = "；".join(part for part in kept if part).strip("；; ")
        if changed:
            logger.info(
                "已降级日程饮食参考: field=%s before=%s after=%s",
                field or "-",
                _single_line(source, 120),
                _single_line(result, 120),
            )
        return result or "只作轻微背景承接,不要强行改写今日主线。"

    @staticmethod
    def _normalize_schedule_adjustment_scope(scope: Any) -> str:
        text = _single_line(scope, 60)
        if any(token in text for token in ("主动策略", "主动消息", "主动频率")):
            return "proactive_only"
        if any(token in text for token in ("直到", "到家", "今晚到", "缓冲期")):
            return "until_condition"
        if any(token in text for token in ("今日后续", "今天剩余", "全天后续")):
            return "rest_of_day"
        if "下一段" in text:
            return "current_and_next"
        if any(token in text for token in ("当前段", "当前休息段")):
            return "current_only"
        return "current_and_next"

    def _format_schedule_adjustments_for_prompt(self, segment: dict[str, Any] | None = None) -> str:
        raw = self.data.get("schedule_adjustments", [])
        now = _now_ts()
        kept = []
        lines = []
        override_lines = []
        override = self._sleep_delay_override_state(now=now)
        if override:
            until_text = _single_line(override.get("until_text"), 24)
            override_lines.append(f"- 临时延后休息｜强｜今晚：到 {until_text} 前按用户临时陪聊约定处理,不要把当前睡眠段当成必须沉默。")
            override_lines.append("  承接要求：只影响今晚；到点后自然收声或回到休息,不要写成长期熬夜习惯。")
        outfit_override = self._current_dialogue_outfit_override(now=now)
        outfit_instruction = _single_line(outfit_override.get("instruction"), 180)
        if outfit_instruction:
            override_lines.append(
                f"- 用户换装｜强｜直到再次换装：用户已明确改变角色服装：“{outfit_instruction}”。"
            )
            override_lines.append(
                "  承接要求：把最新换装写入当前服装状态并延续到后续片段；日程或旧摘要里的默认穿搭只能补空白，不能把服装复原。"
            )
        if isinstance(raw, list) and raw:
            for item in raw:
                if not isinstance(item, dict):
                    continue
                if _single_line(item.get("source_role"), 20) != "owner":
                    continue
                expires_at = _safe_float(item.get("expires_at"), 0)
                if expires_at > 0 and expires_at <= now:
                    continue
                date_text = _single_line(item.get("date"), 16)
                if date_text and date_text != _today_key():
                    continue
                kept.append(item)
                note = _single_line(item.get("note"), 120)
                source = _single_line(item.get("source"), 24)
                intensity = _single_line(item.get("intensity"), 16)
                scope = _single_line(item.get("scope"), 30)
                scope_key = _single_line(item.get("scope_key"), 24) or self._normalize_schedule_adjustment_scope(scope)
                if segment is None and scope_key == "proactive_only":
                    continue
                if isinstance(segment, dict):
                    target_index = _safe_int(segment.get("index"), -1, minimum=-1)
                    anchor_index = _safe_int(item.get("anchor_segment_index"), -1, minimum=-1)
                    if anchor_index < 0:
                        current_segment = self._current_detail_segment_for_update()
                        anchor_index = _safe_int((current_segment or {}).get("index"), target_index, minimum=-1)
                    if scope_key == "current_only" and target_index != anchor_index:
                        continue
                    if scope_key == "current_and_next" and target_index not in {anchor_index, anchor_index + 1}:
                        continue
                    if scope_key in {"rest_of_day", "until_condition", "proactive_only"} and target_index < anchor_index:
                        continue
                if note:
                    meta = "｜".join(part for part in (source or "互动", intensity, scope, f"作用域={scope_key}") if part)
                    lines.append(f"- {meta}：{note}")
                immediate = _single_line(item.get("immediate_reaction"), 120)
                if immediate:
                    lines.append(f"  即时反应：{immediate}")
                updates = item.get("state_updates")
                if isinstance(updates, list) and updates:
                    update_text = "；".join(
                        _single_line(update, 60)
                        for update in updates
                        if _single_line(update, 60)
                    )
                    if update_text:
                        lines.append(f"  状态变量更新：{update_text}")
                carry = _single_line(item.get("carry_rule"), 120)
                if carry:
                    lines.append(f"  承接要求：{carry}")
                if scope_key == "proactive_only":
                    lines.append("  作用限制：只允许影响 proactive_events，不得改写粗日程、summary、today_events、state_variables 或 presence_status。")
            if len(kept) != len(raw):
                self.data["schedule_adjustments"] = kept[-12:]
        if override_lines:
            lines.extend(override_lines)
        return "\n".join(lines[-12:]) if lines else "（暂无）"

    def _current_detail_state_variables(self) -> list[dict[str, str]]:
        segment = self._current_detail_segment_for_update()
        if not segment:
            return []
        enhanced = self.data.get("detail_enhanced_segments", {})
        if not isinstance(enhanced, dict):
            return []
        snapshot = enhanced.get(str(segment.get("key") or ""))
        if not isinstance(snapshot, dict):
            return []
        variables = snapshot.get("state_variables", [])
        if not isinstance(variables, list):
            return []
        return [item for item in variables if isinstance(item, dict)]

    def _detect_schedule_adjustment_from_interaction(self, text: str) -> dict[str, Any] | None:
        normalized = _single_line(text, 220)
        if not normalized:
            return None
        current_variables = self._current_detail_state_variables()
        variable_text = " ".join(
            f"{item.get('name', '')}:{item.get('value', '')} {item.get('note', '')}"
            for item in current_variables[:8]
        )
        def payload(
            *,
            source: str,
            note: str,
            immediate_reaction: str,
            state_updates: list[str],
            intensity: str = "中",
            scope: str = "当前段和下一段",
            carry_rule: str = "后续细化可根据这次用户介入留下合适的状态余味；若没有实际改变任务、作息、边界或共同场景，不必扩写成生活事件。",
            **extra: Any,
        ) -> dict[str, Any]:
            data = {
                "source": source,
                "note": note,
                "immediate_reaction": immediate_reaction,
                "state_updates": state_updates,
                "intensity": intensity,
                "scope": scope,
                "carry_rule": carry_rule,
                "user_text": normalized,
            }
            data.update(extra)
            return data

        current_item = self._get_current_plan_item(self.data.get("daily_plan", {}))
        sleep_delay = self._detect_sleep_delay_request(normalized)
        if sleep_delay:
            until_text = _single_line(sleep_delay.get("until_text"), 24)
            return payload(
                source="临时延后休息",
                note=f"用户今晚希望晚点休息或陪聊；到 {until_text} 前暂时不要把睡眠段当成必须沉默,但这只是今晚的临时约定。",
                immediate_reaction="Bot 会把今晚的节奏稍微放慢并留出陪聊余地,但不会把这当成长期作息改变。",
                state_updates=[f"休息安排：今晚临时延后到 {until_text}", "清醒程度：陪聊但低负担", "后续安排：到点后自然收声或睡回去"],
                intensity="强",
                scope=f"今晚到 {until_text}",
                carry_rule="只影响今晚和当前休息段；后续细化可以保留轻微陪聊/等待感,但不得把它写成长期熬夜习惯,到点后应自然收声或回到休息。",
                sleep_delay_until_ts=sleep_delay.get("until_ts"),
                sleep_delay_until_text=until_text,
                sleep_delay_explicit_time=bool(sleep_delay.get("explicit_time")),
            )
        is_actual_rest_segment = (
            self._sleep_rest_window_active()
            and not self._sleep_delay_override_state(clear_expired=True)
            and self._is_sleepy_plan_item(current_item)
        )
        if is_actual_rest_segment and not re.search(r"别吵|别发|别找|安静|闭嘴|先别|不要来|忙|我有事|没空", normalized):
            runtime_before = self._sleep_runtime_state()
            last_woken = _safe_float(runtime_before.get("last_woken_at"), _safe_float(runtime_before.get("updated_at"), 0))
            last_user_text = _single_line(runtime_before.get("last_user_text"), 80)
            current_user_text = _single_line(normalized, 80)
            consumed_at = _safe_float(runtime_before.get("last_wakeup_context_consumed_at"), 0)
            same_wakeup_message = bool(
                runtime_before.get("phase") == "woken"
                and last_user_text
                and last_user_text == current_user_text
                and consumed_at < last_woken
                and _now_ts() - last_woken < 120
            )
            if same_wakeup_message:
                runtime_before["last_wakeup_context_consumed_at"] = _now_ts()
                return payload(
                    source="睡眠中被用户唤醒",
                    note="当前日程处于休息/睡眠段,这条消息已经在休息闸门放行时登记为唤醒；不要重复计数,语气只保留刚醒的慢一点和轻一点。",
                    immediate_reaction="Bot 刚被这条消息轻轻叫醒,会慢一点看清内容再回应。",
                    state_updates=["清醒程度：刚被唤醒/迷糊", "语气：轻、短、带睡意", "后续安排：用户不继续打扰就继续睡"],
                    intensity="强",
                    scope="当前休息段和后续短时间",
                    carry_rule="回复可以带一点刚醒的气息,但不得降低理解、事实和回答质量；不要再表现成又被叫醒一次。",
                )
            within_awake_grace = (
                runtime_before.get("phase") == "woken"
                and self._sleep_awake_grace_seconds() > 0
                and _now_ts() - last_woken < self._sleep_awake_grace_seconds()
            )
            if within_awake_grace:
                runtime_before["last_user_text"] = _single_line(normalized, 80)
                return payload(
                    source="睡眠中醒后续聊",
                    note="当前日程仍是休息/睡眠段,但 Bot 已在醒后缓冲期内；这是被叫醒后的连续对话,不再按再次唤醒处理。",
                    immediate_reaction="Bot 还没完全精神起来,但已经在接着聊天,不会每句话都像重新被吵醒。",
                    state_updates=["清醒程度：醒后续聊/慢慢清醒", "语气：仍轻一点,但不重复表演被叫醒", "后续安排：停聊后再自然睡回去"],
                    intensity="中",
                    scope="醒后缓冲期",
                    carry_rule="后续回复保持连续聊天感,不要写成每条消息都重新惊醒；用户继续聊时可以逐渐清醒一点。",
                )
            sleep_runtime = self._mark_sleep_woken_by_user(normalized)
            prior_wakes = 0
            segment = self._current_detail_segment_for_update()
            enhanced = self.data.get("detail_enhanced_segments", {})
            if isinstance(segment, dict) and isinstance(enhanced, dict):
                snapshot = enhanced.get(str(segment.get("key") or ""))
                updates = snapshot.get("interaction_updates", []) if isinstance(snapshot, dict) else []
                if isinstance(updates, list):
                    prior_wakes = sum(
                        1 for update in updates
                        if isinstance(update, dict) and "唤醒" in str(update.get("source") or "")
                    )
            prior_wakes = max(prior_wakes, _safe_int(sleep_runtime.get("woken_count"), 1, 1) - 1)
            if prior_wakes > 0:
                return payload(
                    source="睡眠中再次被唤醒",
                    note="当前日程处于休息/睡眠段,用户又发来消息；回复语气应带一点被重新叫醒的迟钝感,但必须清楚理解用户的话,不要埋怨用户。若用户继续聊,可以慢慢醒一点；若用户停下,Bot 会很快继续睡回去。",
                    immediate_reaction="Bot 又被消息轻轻拽醒一下,语气会慢半拍,但会看清用户说了什么再回应。",
                    state_updates=["清醒程度：再次被唤起/半梦半醒", "语气：慢半拍、短一点", "后续安排：用户不继续打扰就继续睡"],
                    intensity="中",
                    scope="当前休息段",
                    carry_rule="当前段回复必须有刚被重新唤起的语气感觉,但不得降低理解和回答质量；如果后续没有用户消息,下一段细化应让 Bot 继续休息或睡回去。",
                )
            return payload(
                source="睡眠中被用户唤醒",
                note="当前日程处于休息/睡眠段,用户发来消息把 Bot 轻轻叫醒；回复语气应像刚醒或半梦半醒,不要立刻精神饱满,但必须看懂并正面回应用户。若用户没有继续打扰,后续应自然睡回去或继续休息。",
                immediate_reaction="Bot 会先带着睡意看一眼消息,语气慢一点,但不会乱接或漏掉用户真正的问题。",
                state_updates=["清醒程度：刚被唤醒/迷糊", "语气：轻、短、带睡意", "后续安排：用户不继续打扰就继续睡"],
                intensity="强",
                scope="当前休息段和后续短时间",
                carry_rule="回复与后续细化必须承接“刚被用户唤醒”的语气状态,但不得牺牲理解、事实和回答质量；如果没有连续聊天,不要强行清醒活动,要睡回去或继续休息。",
            )

        if re.search(r"换元|代入|公式|思路|解法|答案|步骤|这题|函数题|数学题|阅读题|作文|作业|代码|报错|bug|怎么写|这样做|你试试|应该是", normalized):
            updates = ["学习/作业进度：因用户提供思路而推进", "情绪：卡住->松一口气"]
            if re.search(r"卡|函数|数学|作业", variable_text):
                updates.insert(0, "卡住点：用户提示后得到缓解")
            return payload(
                source="用户帮助",
                note="用户刚刚提供了和当前任务相关的帮助；当前任务不再按原本卡住状态推进,后续时间段要继承“被帮助后松动/推进”的结果。",
                immediate_reaction="看到用户给的思路后,Bot 先照着试了一下；如果确实对上当前任务,心里会明显松一口气。",
                state_updates=updates,
                intensity="强",
                scope="当前段、下一段、今日后续",
                carry_rule="如果后续仍写学习/作业/任务,必须体现已经被用户提示影响过：进度推进、情绪松动、或换了做法。",
            )
        food_feedback = self._detect_food_feedback(normalized)
        if food_feedback.get("is_food") and (food_feedback.get("suggestion") or food_feedback.get("bot_directed")):
            meal = _single_line(food_feedback.get("meal"), 20) or "饭点"
            food_hint = _single_line(food_feedback.get("food_hint"), 80)
            return payload(
                source="饮食反馈",
                note=f"用户刚刚给了关于{meal}或吃什么的反馈；后续日程要把这当成一个已经影响生活安排的小事实,可以调整饭点、胃口、准备食物或晚餐选择。",
                immediate_reaction="Bot 会把这句当成一个可执行的小主意,刚才悬着的“吃什么”落下来一点。",
                state_updates=[f"{meal}选择：受用户反馈影响", "饥饿感：开始回落", "后续安排：可能按反馈调整吃饭/点餐/做饭"],
                intensity="中",
                scope="当前段和今日后续饭点",
                carry_rule=f"后续细化如果涉及吃饭、晚餐、休息或外出,要自然承接用户这句饮食反馈：{food_hint}。不要生硬复述,也不要像没有问过一样重置。",
            )
        self_reported_care_action = bool(
            re.search(
                r"(?:我|俺|本人|这边|我们|咱们|咱).{0,12}(?:去睡|早点睡|睡觉|休息|先洗澡|先吃饭|吃点|喝水|躺会|停一下|歇会)",
                normalized,
            )
        )
        if not self_reported_care_action and re.search(r"去睡|早点睡|睡觉|休息|别写了|别弄了|先洗澡|先吃饭|吃点|喝水|别熬|躺会|停一下|歇会", normalized):
            return payload(
                source="用户照顾",
                note="用户刚刚给了休息或照顾指令；后续节奏应明显调慢,更可能提前收尾、补充休息、喝水吃饭或把任务延后。",
                immediate_reaction="Bot 看到这句会停一下手里的事,嘴上可能不立刻答应,但动作会慢下来一点。",
                state_updates=["体力：消耗放缓/略微回稳", "情绪：被照顾后的柔和", "后续安排：更倾向提前收尾或补充休息"],
                intensity="强",
                scope="当前段和今日后续",
                carry_rule="下一段不能完全无视这句照顾提醒；至少要在节奏、体力或收尾方式上留下影响。",
            )
        shared_location_signal = bool(re.search(r"一起|我们|咱们|咱俩|带你|带我|陪你|陪我|跟你|跟我|走吧|出发吧", normalized))
        outward_action_signal = bool(re.search(r"出发|出门|出去|去吃|去逛|去买|去玩|上车|下车|走了|走起|换鞋|拿钥匙|等车|打车|坐车|地铁|公交|到了|排队|找位子|点单|点餐|下单", normalized))
        if shared_location_signal and outward_action_signal:
            self._apply_dialogue_location_override("外面")
            return payload(
                source="用户带出/同行",
                note="用户刚刚带角色出门或一起外出；当前位置应从家里切换到外面,后续细化要承接外出场景,不要把角色写回家里。",
                immediate_reaction="Bot 会赶紧收拾一下东西,跟着用户往外走,可能边走边看手机或整理衣服。",
                state_updates=["位置：家里->外面", "活动：跟随用户外出", "情绪：略兴奋或期待"],
                intensity="强",
                scope="当前段和今日后续直到回家线索出现",
                carry_rule="后续细化和状态注入必须把角色位置保持在'外面',直到用户明确说回家、到家或日程自然过渡到居家时段；不要把角色写回沙发、卧室或家里。",
            )
        shared_return_signal = bool(re.search(r"一起|我们|咱们|咱俩|带你|带我|陪你|陪我|跟你|跟我|回家吧|送你回", normalized))
        return_home_signal = bool(re.search(r"回来了|到家了|回家了|进家门|开门|进门|回到.*家|到家|安全到家", normalized))
        if shared_return_signal and return_home_signal:
            self._apply_dialogue_location_override("家里")
            return payload(
                source="用户带回/回家",
                note="用户和角色刚刚回到家；当前位置应从外面切换回家里,后续细化要承接回家后场景。",
                immediate_reaction="Bot 会松一口气,可能踢掉鞋子或把东西放下,瘫到沙发上。",
                state_updates=["位置：外面->家里", "活动：回到居家", "情绪：放松"],
                intensity="中",
                scope="当前段和下一段",
                carry_rule="后续细化可以把角色写回家里场景,但不要立刻恢复出门前的精确活动,要体现外出后的余味。",
            )
        explicit_appointment_signal = bool(re.search(
            r"(?:约好|说好|定了|晚点(?:一起|聊|打电话|语音|开黑|看)|(?:一起|我们|咱们|咱俩|陪你|陪我|等你|等我|跟你|跟我).{0,18}(?:待会|一会|晚上|明天|等下|见面|打电话|语音|开黑|看))",
            normalized,
        ))
        if explicit_appointment_signal:
            return payload(
                source="用户约定",
                note="用户刚刚给出陪伴、等待、稍后一起做事或约定线索；后续日程应保留期待/等待/预留空档,不要像没有约定一样推进。",
                immediate_reaction="Bot 会把这句话在心里放一下,后续做事时更容易分神看时间或看消息。",
                state_updates=["等待/期待：上升", "后续安排：可能预留空档", "主动话题：围绕约定轻轻回勾"],
                intensity="强",
                scope="今日后续",
                carry_rule="后续主动契机和细化事件要把这个约定当作开放话头,可以等待、预留时间或轻轻提醒。",
            )
        care = self._detect_care_feedback(normalized)
        if care.get("is_care"):
            return payload(
                source="用户关心",
                note="用户刚刚给了关心或照顾提醒；后续日程需要从低落/烦躁向被安抚后的柔和、慢慢回稳偏移,不要升级破坏性冲动。",
                immediate_reaction="Bot 看到关心会先顿一下,语气和动作都比刚才软一点。",
                state_updates=["情绪：低落/烦躁->被接住一点", "体力：主观疲惫感略降"],
                intensity="中",
                scope="当前段、下一段、情绪底色",
            )
        if re.search(r"摸摸|贴贴|抱抱|亲亲|揉揉|蹭蹭|摸头|陪你|哄你|乖|不难过|别难过|没关系|辛苦了|抱一下", normalized):
            return payload(
                source="安慰互动",
                note="用户刚刚在安慰或亲近；后续日程应保留一点被接住的余温,表达更软一些,不要继续单向累积负面情绪。",
                immediate_reaction="Bot 会把刚才绷着的劲松下来一点,可能短暂地想贴近用户。",
                state_updates=["情绪：紧绷->柔和", "亲近感：上升"],
                intensity="中",
                scope="当前段和情绪底色",
            )
        if re.search(r"别生气|不要烦|冷静|别急|别砸|别摔|别骂|别打", normalized):
            return payload(
                source="边界提醒",
                note="用户提醒降低攻击性或破坏性表达；后续日程必须把烦躁写成收着的动作,避免砸、摔、扔、打这类冲动。",
                immediate_reaction="Bot 会把那点冲劲压回去,改成少说两句或把东西放远一点。",
                state_updates=["情绪：冲动->收住", "行为边界：避免破坏性动作"],
                intensity="强",
                scope="今日后续",
                carry_rule="后续所有细化都必须遵守这个边界,不能再输出破坏性动作或攻击性冲动。",
            )
        if re.search(r"别吵|别发|别找|安静|闭嘴|先别|不要来|忙|我有事|没空", normalized):
            return payload(
                source="用户边界",
                note="用户刚刚表达边界或忙碌；后续主动应明显收敛,减少追问和贴近,让日程转向自我消化。",
                immediate_reaction="Bot 会把原本想继续靠近的动作收住,把消息窗口放到一边。",
                state_updates=["主动欲：下降", "关系状态：后退一点", "后续安排：转向自我消化"],
                intensity="强",
                scope="今日后续主动策略",
                carry_rule="后续主动消息必须降低频率和压迫感,不要把边界当作可撒娇突破的对象。",
            )
        return None

    def _record_schedule_adjustment_from_interaction(self, text: str, user: dict[str, Any] | None = None) -> bool:
        if self._private_user_role(user) != "owner":
            return False
        outfit_updated = self._record_dialogue_outfit_override_from_interaction(text, user)
        adjustment = self._detect_schedule_adjustment_from_interaction(text)
        if not adjustment:
            return outfit_updated
        raw = self.data.setdefault("schedule_adjustments", [])
        if not isinstance(raw, list):
            raw = []
            self.data["schedule_adjustments"] = raw
        note = _single_line(adjustment.get("note"), 140)
        if not note:
            return False
        now = _now_ts()
        intensity = _single_line(adjustment.get("intensity"), 16) or "中"
        ttl_hours = 18 if intensity == "强" else 10 if intensity == "中" else 6
        current_segment = self._current_detail_segment_for_update()
        anchor_index = _safe_int((current_segment or {}).get("index"), -1, minimum=-1)
        scope_key = self._normalize_schedule_adjustment_scope(adjustment.get("scope"))
        source = _single_line(adjustment.get("source"), 24)
        if source == "用户带回/回家":
            raw[:] = [
                old
                for old in raw
                if not (
                    isinstance(old, dict)
                    and (
                        _single_line(old.get("condition_key"), 32) == "return_home"
                        or (
                            _single_line(old.get("scope_key"), 24) == "until_condition"
                            and "回家" in _single_line(old.get("scope"), 60)
                        )
                    )
                )
            ]
        item = {
            "date": _today_key(),
            "source": source,
            "note": note,
            "immediate_reaction": _single_line(adjustment.get("immediate_reaction"), 140),
            "state_updates": adjustment.get("state_updates", []),
            "user_text": _single_line(adjustment.get("user_text"), 120),
            "intensity": intensity,
            "scope": _single_line(adjustment.get("scope"), 40),
            "scope_key": scope_key,
            "carry_rule": _single_line(adjustment.get("carry_rule"), 160),
            "source_role": "owner",
            "source_user_id": _single_line((user or {}).get("user_id"), 80),
            "created_at": now,
            "expires_at": now + ttl_hours * 3600,
        }
        if anchor_index >= 0:
            item["anchor_segment_index"] = anchor_index
            item["anchor_segment_key"] = _single_line((current_segment or {}).get("key"), 120)
        if scope_key == "until_condition" and (source == "用户带出/同行" or "回家" in item["scope"]):
            item["condition_key"] = "return_home"
        sleep_delay_until = _safe_float(adjustment.get("sleep_delay_until_ts"), 0)
        if sleep_delay_until > now:
            item["sleep_delay_until_ts"] = sleep_delay_until
            item["sleep_delay_until_text"] = _single_line(adjustment.get("sleep_delay_until_text"), 24)
            item["sleep_delay_explicit_time"] = bool(adjustment.get("sleep_delay_explicit_time"))
            self._apply_sleep_delay_override(
                {
                    "until_ts": sleep_delay_until,
                    "until_text": item["sleep_delay_until_text"],
                    "explicit_time": item["sleep_delay_explicit_time"],
                    "user_text": item["user_text"],
                },
                text=item["user_text"],
            )
        self._record_detail_interaction_update(item)
        plan = self.data.get("daily_plan", {})
        current_item = self._get_current_plan_item(plan) if isinstance(plan, dict) else None
        if isinstance(current_item, dict):
            current_item["lifecycle_status"] = "changed"
            current_item["changed_at"] = self._environment_now().strftime("%H:%M")
            current_item["change_reason"] = _single_line(adjustment.get("source") or note, 80)
        if raw and isinstance(raw[-1], dict) and raw[-1].get("note") == note:
            raw[-1].update(item)
        else:
            raw.append(item)
            del raw[:-12]
        self._invalidate_detail_after_interaction(now=now)
        return True

    def _format_important_dates_injection(self) -> str:
        section = self._format_important_dates_prompt_section()
        return render_prompt_sections([section], mode=PromptRenderMode.LABELED_BLOCK)

    def _format_important_dates_prompt_section(self) -> PromptSection:
        important_dates = self._format_important_dates_for_prompt()
        body = ""
        if important_dates and important_dates != "（近期没有需要特别记住的日期）":
            body = (
                f"{important_dates}\n"
                "如果用户提到相关日期、纪念、生日、约定或计划,请自然承接；不要无故强行展开。"
            )
        return prompt_section(
            key="important.dates",
            title="近期重要日期",
            source="daily_state",
            content=body,
        )

    @staticmethod
    def _daily_plan_message_target_is_allowed(target: str) -> bool:
        normalized = re.sub(r"[\s“”\"'‘’《》【】\[\]（）()的那边这边身上手机微信QQqq号:：]+", "", str(target or ""))
        if not normalized:
            return False
        allowed_targets = (
            "你",
            "用户",
            "主人",
            "主要用户",
            "当前用户",
            "对方",
            "自己",
            "我",
        )
        neutral_targets = (
            "手机",
            "通知",
            "提醒",
            "闹钟",
            "系统",
            "日历",
            "输入框",
            "屏幕",
            "软件",
            "应用",
            "网页",
        )
        return any(token in normalized for token in allowed_targets) or normalized in neutral_targets

    @classmethod
    def _daily_plan_clause_has_named_message_interaction(cls, clause: str) -> bool:
        if not clause:
            return False
        target_patterns = (
            r"给(?P<target>[^，。；;,.!?？！、\s]{1,14}?)(?:回了?(?:一?条)?(?:消息|微信|QQ|私信|短信|语音)?|回复了?|发了?(?:一?条)?(?:消息|微信|QQ|私信|短信|语音)?|发去(?:消息|微信|QQ|私信|短信|语音)?|私聊了?)",
            r"(?:收到|看见|看到|点开|翻到)(?P<target>[^，。；;,.!?？！、\s]{1,14}?)(?:的)?(?:消息|微信|QQ|私信|短信|语音|提醒)",
            r"(?P<target>[^，。；;,.!?？！、\s]{1,14}?)(?:发来|发了|传来|弹来|回了?)(?:一?条)?(?:消息|微信|QQ|私信|短信|语音|提醒)",
            r"(?:和|跟)(?P<target>[^，。；;,.!?？！、\s]{1,14}?)(?:聊了?|聊天|私聊|互相吐槽|互相安慰|发消息|回消息)",
        )
        for pattern in target_patterns:
            for match in re.finditer(pattern, clause):
                target = _single_line(match.groupdict().get("target"), 24)
                if target and not cls._daily_plan_message_target_is_allowed(target):
                    return True
        relation_tokens = (
            "熟人",
            "同学",
            "老师",
            "朋友",
            "室友",
            "邻居",
            "前辈",
            "后辈",
            "家人",
            "妈妈",
            "爸爸",
            "哥哥",
            "姐姐",
            "弟弟",
            "妹妹",
        )
        message_actions = (
            "发来消息",
            "发了消息",
            "回了消息",
            "回消息",
            "回复",
            "私聊",
            "聊天",
            "提醒她",
            "提醒他",
            "找她",
            "找他",
        )
        return any(token in clause for token in relation_tokens) and any(token in clause for token in message_actions)

    def _daily_plan_named_entity_is_known(self, name: Any) -> bool:
        normalized = _single_line(name, 32).casefold()
        if not normalized:
            return False
        known_names = [_single_line(runtime_persona_setting(self, "bot_name", "小星"), 80)]
        data = getattr(self, "data", {})
        users = data.get("users") if isinstance(data, dict) else None
        if isinstance(users, dict):
            for user in users.values():
                if not isinstance(user, dict):
                    continue
                known_names.extend(
                    _single_line(user.get(field), 80)
                    for field in ("nickname", "display_name", "user_name", "name")
                )
        if any(candidate and candidate.casefold() == normalized for candidate in known_names):
            return True
        persona_sources = (
            runtime_persona_setting(self, "schedule_persona_prompt", ""),
            runtime_persona_setting(self, "schedule_worldview_prompt", ""),
            getattr(self, "_default_persona_prompt_cache", ""),
        )
        boundary = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(normalized)}(?![A-Za-z0-9_])", re.IGNORECASE)
        return any(boundary.search(str(source or "")) for source in persona_sources)

    @staticmethod
    def _daily_plan_relationship_alias_groups() -> tuple[tuple[str, ...], ...]:
        """Stable relationship names that must come from an identity source."""
        return (
            ("妈妈", "母亲", "妈咪", "老妈", "娘亲", "阿妈"),
            ("爸爸", "父亲", "爹地", "老爸", "爹", "阿爸"),
            ("父母", "双亲"),
            ("家人", "家里人", "亲人"),
            ("祖母", "奶奶", "外婆", "姥姥"),
            ("祖父", "爷爷", "外公", "姥爷"),
            ("哥哥", "兄长", "大哥", "阿哥"),
            ("姐姐", "姊姊", "姊姐", "阿姐"),
            ("弟弟", "胞弟"),
            ("妹妹", "胞妹"),
            ("兄弟姐妹", "兄弟姊妹", "手足"),
            ("叔叔", "伯伯", "舅舅", "姨父", "姑父"),
            ("阿姨", "姑姑", "姨妈", "舅妈", "婶婶", "伯母"),
            ("亲戚", "亲属"),
            ("朋友", "好友", "闺蜜", "发小", "死党"),
            ("同学", "同桌", "同班同学", "校友"),
            ("学长", "学姐", "学弟", "学妹"),
            ("老师", "教师", "班主任", "导师"),
            ("师父", "师傅"),
            ("室友", "舍友"),
            ("同事", "同僚"),
            ("上司", "领导", "老板"),
            ("邻居", "邻家"),
            ("前辈", "后辈"),
            ("恋人", "爱人", "伴侣"),
            ("男朋友", "男友"),
            ("女朋友", "女友"),
            ("丈夫", "老公"),
            ("妻子", "老婆"),
            ("未婚夫", "未婚妻"),
            ("监护人", "养父", "养母", "继父", "继母"),
        )

    @staticmethod
    def _daily_plan_identity_bound_relationship_groups() -> set[str]:
        """Relationships too stable/private to infer from an old life fragment."""
        return {
            "妈妈",
            "爸爸",
            "父母",
            "家人",
            "祖母",
            "祖父",
            "哥哥",
            "姐姐",
            "弟弟",
            "妹妹",
            "兄弟姐妹",
            "叔叔",
            "阿姨",
            "亲戚",
            "恋人",
            "男朋友",
            "女朋友",
            "丈夫",
            "妻子",
            "未婚夫",
            "监护人",
        }

    @staticmethod
    def _mask_non_relationship_phrases(text: Any) -> str:
        source = str(text or "")
        if not source:
            return ""
        for phrase in (
            "母亲节",
            "父亲节",
            "教师节",
            "父母官",
            "老师傅",
            "小姐姐",
            "小哥哥",
            "食堂阿姨",
            "宿管阿姨",
            "保洁阿姨",
            "清洁阿姨",
            "保安叔叔",
            "司机叔叔",
            "老婆饼",
        ):
            source = source.replace(phrase, "□" * len(phrase))
        return source

    def _daily_plan_relationship_authority_sources(self) -> tuple[str, ...]:
        sources = [
            str(runtime_persona_setting(self, "schedule_persona_prompt", "") or ""),
            str(runtime_persona_setting(self, "schedule_worldview_prompt", "") or ""),
            str(getattr(self, "_default_persona_prompt_cache", "") or ""),
        ]
        getter = getattr(self, "_get_default_persona_prompt", None)
        if callable(getter):
            try:
                sources.append(str(getter() or ""))
            except Exception:
                pass
        return tuple(dict.fromkeys(source for source in sources if source.strip()))

    def _daily_plan_declared_relation_tokens(self) -> set[str]:
        authority_text = self._mask_non_relationship_phrases(
            "\n".join(self._daily_plan_relationship_authority_sources())
        )
        declared: set[str] = set()
        groups = self._daily_plan_relationship_alias_groups()
        for aliases in groups:
            if any(alias in authority_text for alias in aliases):
                declared.update(aliases)

        # Institutional roles are an inherent part of an explicitly declared
        # school/work identity, while family roles are never inferred this way.
        if re.search(r"学生|校园|学校|上学|教室|班级|课程", authority_text):
            for aliases in groups:
                if aliases[0] in {"同学", "学长", "老师"}:
                    declared.update(aliases)
        if re.search(r"上班|职员|员工|公司|工位|办公室|职场", authority_text):
            for aliases in groups:
                if aliases[0] in {"同事", "上司"}:
                    declared.update(aliases)
        return declared

    def _daily_plan_undeclared_relationship_tokens(self, text: Any) -> list[str]:
        source = self._mask_non_relationship_phrases(text)
        if not source:
            return []
        declared = self._daily_plan_declared_relation_tokens()
        hits: list[str] = []
        identity_bound_groups = self._daily_plan_identity_bound_relationship_groups()
        all_aliases = sorted(
            {
                alias
                for group in self._daily_plan_relationship_alias_groups()
                if group[0] in identity_bound_groups
                for alias in group
            },
            key=len,
            reverse=True,
        )
        for alias in all_aliases:
            if alias not in declared and alias in source:
                hits.append(alias)
        return hits

    @staticmethod
    def _relationship_clause_is_explicitly_user_owned(clause: str, relation_tokens: list[str]) -> bool:
        if not clause or not relation_tokens:
            return False
        owner_marker = r"(?:主要用户|当前用户|这位用户|收件人|对方|用户|User|user)"
        for token in relation_tokens:
            escaped = re.escape(token)
            if re.search(rf"{owner_marker}[^，,。；;！？!?]{{0,24}}{escaped}", clause):
                return True
            if re.search(rf"{escaped}[^，,。；;！？!?]{{0,16}}(?:是|属于|来自)?{owner_marker}(?:的|那边)", clause):
                return True
        return False

    def _format_generation_relationship_authority_guard(self) -> str:
        return render_prompt_sections(
            [self._format_generation_relationship_authority_guard_prompt_section()],
            mode=PromptRenderMode.LABELED_BLOCK,
        )

    def _format_generation_relationship_authority_guard_prompt_section(self) -> PromptSection:
        declared = self._daily_plan_declared_relation_tokens()
        canonical = [
            aliases[0]
            for aliases in self._daily_plan_relationship_alias_groups()
            if any(alias in declared for alias in aliases)
        ]
        declared_text = "、".join(canonical) if canonical else "无"
        content = (
            "- 只有日程专用角色设定、日程世界观和当前默认人格能够建立 Bot 的稳定关系；"
            "旧日程、旧日记、旧动态、聊天摘要、MemoryCompanion、技能/创作记录和其他连续性材料不能单独证明一段新关系。\n"
            f"- 当前身份来源已声明的关系称谓：{declared_text}。只有这些关系及其同义称呼可以作为 Bot 的关系事实。\n"
            "- 连续性材料里的关系若不能和身份来源对上，就按未经核实的旧叙事略过；不要续写，也不要换个称呼继续沿用。\n"
            "- 用户谈到的亲友只属于用户，除非身份来源另有明确设定，不得转移成 Bot 自己的亲友。"
            "\n- 当前收件人与 Bot 的结构化关系由本轮收件人关系事实单独决定，不要用这份生活关系清单覆盖它。"
        )
        return prompt_section(
            key="relationship.generation_authority",
            title="关系事实权限",
            source="daily_state",
            content=content,
        )

    def _sanitize_generation_relationship_context(
        self,
        text: Any,
        *,
        source: str = "",
        max_chars: int = 0,
    ) -> str:
        """Remove undeclared relationship clauses before they reach a generator."""
        raw = str(text or "").strip()
        if not raw:
            return ""
        initial_hits = self._daily_plan_undeclared_relationship_tokens(raw)
        if not initial_hits:
            return raw[:max_chars] if max_chars > 0 else raw

        cleaned_lines: list[str] = []
        removed_any = False
        for raw_line in raw.splitlines():
            line = raw_line.strip()
            if not line:
                if cleaned_lines and cleaned_lines[-1]:
                    cleaned_lines.append("")
                continue
            pieces = re.split(r"([，,。；;！？!?]+)", line)
            kept: list[str] = []
            for index in range(0, len(pieces), 2):
                clause = pieces[index].strip()
                separator = pieces[index + 1] if index + 1 < len(pieces) else ""
                if not clause:
                    continue
                clause_hits = self._daily_plan_undeclared_relationship_tokens(clause)
                if clause_hits and not self._relationship_clause_is_explicitly_user_owned(clause, clause_hits):
                    removed_any = True
                    continue
                kept.append(clause)
                if separator:
                    kept.append(separator)
            clean_line = "".join(kept).strip(" ，,。；;！？!?")
            if clean_line and clean_line not in {"-", "*", "•"}:
                cleaned_lines.append(clean_line)
        if not removed_any:
            return raw[:max_chars] if max_chars > 0 else raw
        cleaned = "\n".join(cleaned_lines).strip()
        if max_chars > 0:
            cleaned = cleaned[:max_chars]
        if cleaned == (raw[:max_chars] if max_chars > 0 else raw):
            return cleaned

        log_key = f"{source or '-'}|{'/'.join(initial_hits[:4])}"
        now = _now_ts()
        recent_logs = getattr(self, "_recent_relationship_context_sanitize_logs", None)
        if not isinstance(recent_logs, dict):
            recent_logs = {}
            setattr(self, "_recent_relationship_context_sanitize_logs", recent_logs)
        if now - _safe_float(recent_logs.get(log_key), 0) >= 1800:
            logger.info(
                "生成前已剔除未声明关系上下文: source=%s relations=%s",
                source or "-",
                ",".join(initial_hits[:8]),
            )
            recent_logs[log_key] = now
        return cleaned

    def _daily_plan_clause_has_unsafe_social_fact(self, text: str) -> bool:
        clause = _single_line(text, 160)
        if not clause:
            return False
        if self._daily_plan_undeclared_relationship_tokens(clause):
            return True
        if self._daily_plan_clause_has_named_message_interaction(clause):
            return True
        future_commitment = (
            "约好",
            "约了",
            "约定",
            "约着",
            "约去",
            "约夜宵",
            "约饭",
            "约见",
            "约她",
            "约他",
            "约人",
            "下周",
            "下次一起",
            "改天一起",
            "明天一起",
            "后天一起",
            "之后一起",
            "过几天一起",
        )
        if any(token in clause for token in future_commitment):
            return True
        if re.search(r"(约|叫|喊|拉|找|邀)[^，。；;,.]{0,16}(一起|夜宵|吃|喝|看|玩|逛|见面|出门)", clause):
            return True
        if re.search(r"(和|跟)[^，。；;,.]{1,16}一起(去|吃|喝|看|玩|逛|见|出门|夜宵)", clause):
            return True
        if re.search(r"(消息|私信|电话|语音)[^，。；;,.]{0,16}(约|叫|喊|拉|邀)[^，。；;,.]{0,16}(一起|去|吃|喝|看|玩|逛|夜宵)", clause):
            return True
        concrete_relation = (
            "熟人",
            "同学",
            "老师",
            "朋友",
            "室友",
            "邻居",
            "前辈",
            "后辈",
            "家人",
            "父母",
            "妈妈",
            "爸爸",
            "哥哥",
            "姐姐",
            "弟弟",
            "妹妹",
        )
        if any(token in clause for token in ("碰见", "遇见", "撞见", "碰到", "遇到")) and any(
            token in clause for token in concrete_relation
        ):
            return True
        if re.search(r"(碰见|遇见|撞见|遇到)[过了]?[一-龥]{2,4}", clause) and not any(
            token in clause for token in ("路人", "店员", "陌生人", "旁边的人", "小动物", "猫", "狗", "鸟")
        ):
            return True
        if re.search(r"(顺手|顺带|特意|回来时|回来的时候)?.{0,8}给[^，。；;,.]{1,12}(带|买|捎|留|放)了?", clause):
            return True
        named_companion = re.search(
            r"(?:与|和|跟)\s*[A-Z][A-Za-z0-9_.-]{1,23}\s*(?:一起)?(?:吃|喝|聊|逛|玩|看|见面|出门)",
            clause,
        )
        if named_companion:
            name_match = re.search(r"(?:与|和|跟)\s*([A-Z][A-Za-z0-9_.-]{1,23})", named_companion.group(0))
            if name_match and self._daily_plan_named_entity_is_known(name_match.group(1)):
                return False
            return True
        return False

    @staticmethod
    def _sanitize_schedule_model_artifacts(text: Any, *, limit: int = 180) -> str:
        """Remove model scratch fields and speaker continuations from schedule prose."""
        source = re.sub(r"\s+", " ", str(text or "")).strip()
        if not source:
            return ""
        source = source.replace("```json", "").replace("```", "")
        source = source.replace("**", "").replace("__", "").replace("`", "")

        scratch_pattern = re.compile(
            r"(?:^|[\s，。；;!?！？])(?:dream[_\s-]*seed|analysis|reasoning(?:_content)?|角色草稿|续写提示)\s*[:：]",
            re.IGNORECASE,
        )
        scratch = scratch_pattern.search(source)
        if scratch:
            source = source[: scratch.start()].rstrip(" ，。；;:：")

        speaker_pattern = re.compile(
            r"(?:^|[\s，。；;!?！？])(?:Fox|Assistant|Character|Bot|[A-Z][A-Za-z0-9_.-]{1,20})\s*[:：]"
        )
        speaker = speaker_pattern.search(source)
        if speaker:
            if not source[: speaker.start()].strip(" ，。；;:："):
                return ""
            source = source[: speaker.start()].rstrip(" ，。；;:：")
        return _single_line(source, limit)

    @staticmethod
    def _schedule_text_is_single_meal_action(text: Any) -> bool:
        source = _single_line(text, 240)
        if not source:
            return False
        meal_action = re.search(
            r"吃(?:着|了|完|过|点|一|顿|碗)?|用餐|进餐|品尝|享用|早餐|早饭|午餐|午饭|晚餐|晚饭|夜宵|喝粥",
            source,
        )
        if not meal_action:
            return False
        return not re.search(
            r"吃完|饭后|餐后|随后|然后|之后|接着|再去|再把|转而|余下|剩下|后来|收拾完.*(?:休息|做|处理|出门)",
            source,
        )

    @staticmethod
    def _sanitize_schedule_meal_time_wording(text: Any, start_minutes: int | None) -> str:
        source = _single_line(text, 180)
        if not source or start_minutes is None:
            return source
        minute = int(start_minutes) % (24 * 60)
        if minute < 16 * 60:
            source = re.sub(r"吃(?:晚饭|晚餐)", "吃点东西", source)
            source = re.sub(r"(?:晚饭|晚餐)", "用餐", source)
        if minute >= 15 * 60:
            source = re.sub(r"吃(?:早餐|早饭)", "吃点东西", source)
            source = re.sub(r"(?:早餐|早饭)", "用餐", source)
        return _single_line(source, 180)

    @classmethod
    def _sanitize_overlong_schedule_activity(cls, text: Any, duration_minutes: int | None) -> str:
        source = _single_line(text, 180)
        if not source or duration_minutes is None or duration_minutes <= 120:
            return source
        if not cls._schedule_text_is_single_meal_action(source):
            return source
        stem = source.rstrip("。；;，, ")
        return _single_line(f"这段开始时，{stem}；吃完后便按这段时间的节奏休息或处理手边的事。", 180)

    def _sanitize_daily_plan_social_fact_text(self, text: str, *, field: str = "") -> str:
        source = self._sanitize_schedule_model_artifacts(text, limit=180)
        if not source:
            return ""
        raw_clauses = [part for part in re.split(r"[，,。；;]+", source) if _single_line(part, 120)]
        unsafe_flags = [self._daily_plan_clause_has_unsafe_social_fact(part) for part in raw_clauses]
        if not any(unsafe_flags):
            return source
        kept = []
        for index, part in enumerate(raw_clauses):
            if unsafe_flags[index]:
                continue
            cleaned_part = _single_line(part, 120)
            if (
                index + 1 < len(raw_clauses)
                and unsafe_flags[index + 1]
                and len(cleaned_part) <= 20
                and re.search(r"(?:时|的时候|期间|过程中)$", cleaned_part)
            ):
                continue
            kept.append(cleaned_part)
        cleaned = "，".join(kept).strip("，,。；; ")
        if not cleaned:
            cleaned = "放慢节奏处理手边的小事，把这段时间过得轻一点"
        if cleaned == source:
            return source
        log_key = "|".join((field or "-", _single_line(source, 120), _single_line(cleaned, 120)))
        now = _now_ts()
        recent_logs = getattr(self, "_recent_social_fact_sanitize_logs", None)
        if not isinstance(recent_logs, dict):
            recent_logs = {}
            setattr(self, "_recent_social_fact_sanitize_logs", recent_logs)
        last_logged = _safe_float(recent_logs.get(log_key), 0)
        if now - last_logged >= 1800:
            logger.info(
                "已清理日程中的未授权社交事实: field=%s before=%s after=%s",
                field or "-",
                _single_line(source, 120),
                _single_line(cleaned, 120),
            )
            recent_logs[log_key] = now
            if len(recent_logs) > 200:
                cutoff = now - 3600
                for key, ts in list(recent_logs.items()):
                    if _safe_float(ts, 0) < cutoff:
                        recent_logs.pop(key, None)
        return cleaned

    @staticmethod
    def _sanitize_empty_daily_plan_message_seed(text: str) -> str:
        cleaned = _single_line(text, 140)
        if not cleaned:
            return ""
        normalized = re.sub(r"[。！？!?,，、；;\s]+", "", cleaned)
        empty_markers = (
            "这段没什么想说的",
            "没什么想说的",
            "这段没有什么想说的",
            "没有什么想说的",
            "这段先留白",
            "先留白",
            "留白",
            "脑子空空的",
            "脑袋空空的",
            "没什么可说的",
            "没有什么可说的",
            "这段没话说",
            "没话说",
            "先不吵你",
            "不吵你",
            "先不打扰你",
            "不打扰你",
            "这段先安静一下",
            "先安静一下",
            "下午空一下",
            "下午空一会",
            "下午空一会儿",
            "下午空了下",
        )
        if normalized in empty_markers:
            return ""
        if any(token in normalized for token in ("没什么想说", "没有什么想说", "没什么可说", "没有什么可说")):
            return ""
        if any(token in normalized for token in ("先不吵", "不打扰", "先留白")):
            return ""
        if re.fullmatch(r"(?:上午|中午|下午|晚上|午后|傍晚)?(?:先)?空(?:一下|一会儿?|了下)", normalized):
            return ""
        return cleaned

    def _sanitize_daily_plan_inplace(self, plan: dict[str, Any]) -> bool:
        if not isinstance(plan, dict):
            return False
        raw_items = plan.get("items") if isinstance(plan.get("items"), list) else plan.get("schedule")
        if not isinstance(raw_items, list):
            return False
        changed = self._normalize_plan_item_intervals(raw_items)
        parsed_starts = [
            self._parse_hhmm_to_minutes(item.get("time")) if isinstance(item, dict) else None
            for item in raw_items
        ]
        for index, item in enumerate(raw_items):
            if not isinstance(item, dict):
                continue
            for field in ("activity", "message_seed"):
                original = _single_line(item.get(field), 180)
                if not original:
                    continue
                cleaned = self._sanitize_daily_plan_social_fact_text(original, field=field)
                if field == "activity":
                    start = parsed_starts[index]
                    next_start = next(
                        (candidate for candidate in parsed_starts[index + 1 :] if candidate is not None),
                        None,
                    )
                    end = self._plan_item_end_minutes(start, item, next_start=next_start) if start is not None else None
                    duration = end - start if start is not None and end is not None else None
                    cleaned = self._sanitize_schedule_meal_time_wording(cleaned, start)
                    cleaned = self._sanitize_overlong_schedule_activity(cleaned, duration)
                if field == "message_seed":
                    cleaned = self._sanitize_empty_daily_plan_message_seed(cleaned)
                if cleaned != original:
                    item[field] = cleaned
                    changed = True
        if changed:
            plan["sanitized_at"] = self._environment_now().strftime("%Y-%m-%d %H:%M:%S")
        return changed

    def _parse_plan_items(self, raw_text: str) -> list[dict[str, str]]:
        payload = self._extract_json_payload(raw_text)
        if payload is None:
            return []
        if isinstance(payload, dict):
            raw_items = (
                payload.get("schedule")
                or payload.get("items")
                or payload.get("tasks")
                or payload.get("events")
                or payload.get("plan")
                or []
            )
        elif isinstance(payload, list):
            raw_items = payload
        else:
            raw_items = []

        items: list[dict[str, str]] = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            raw_time = item.get("time") or item.get("start") or item.get("start_time") or item.get("begin_time") or item.get("开始时间")
            item_time, range_end = self._normalize_plan_clock_range(raw_time)
            if self._parse_hhmm_to_minutes(item_time) is None:
                continue
            raw_activity = _single_line(
                item.get("activity")
                or item.get("title")
                or item.get("task")
                or item.get("event")
                or item.get("内容"),
                120,
            )
            activity = self._align_plan_text_with_skill_bounds(
                self._sanitize_daily_plan_social_fact_text(
                    self._soften_destructive_daily_plan_text(raw_activity),
                    field="activity",
                )
            )
            if not activity:
                continue
            mood = self._align_plan_text_with_skill_bounds(
                self._soften_destructive_daily_plan_text(_single_line(item.get("mood"), 30))
            )
            raw_message_seed = _single_line(item.get("message_seed"), 140)
            message_seed = self._align_plan_text_with_skill_bounds(
                self._sanitize_empty_daily_plan_message_seed(
                    self._sanitize_daily_plan_social_fact_text(
                        self._soften_destructive_daily_plan_text(
                            self._deemphasize_state_report_preamble(
                                raw_message_seed,
                                reason="background_schedule",
                            )
                        ),
                        field="message_seed",
                    )
                )
            )
            items.append(
                {
                    "time": item_time,
                    "end": self._normalize_plan_clock(
                        item.get("end")
                        or item.get("end_time")
                        or item.get("finish_time")
                        or item.get("until")
                        or item.get("结束时间")
                        or range_end
                    ),
                    "activity": activity,
                    "mood": mood,
                    "message_seed": message_seed,
                    "basis": self._normalize_schedule_basis(item.get("basis"), default=["inspiration"]),
                    "confidence": min(1.0, _safe_float(item.get("confidence"), 0.7)),
                }
            )
        items = sorted(items, key=lambda item: self._parse_hhmm_to_minutes(item["time"]) or 0)
        items = items[: _safe_int(runtime_persona_setting(self, "daily_plan_item_count", 10), 10, 1)]
        self._normalize_plan_item_intervals(items)
        # Pass every generated item through the C3 write gate.  LLM fields such
        # as status, source_refs, authority and evidence are never trusted;
        # canonical axes are retained so downstream views cannot silently lose
        # the distinction between a plan and an observation.
        today = _today_key()
        for index, item in enumerate(items):
            try:
                canonical = normalize_plan_item(
                    {**item, "title": item.get("activity"), "date": today, "subject_actor_id": "bot_self", "actor_type": "bot"},
                    plan_id=f"{today}:{index}",
                    now=self._environment_now(),
                )
            except Exception:
                continue
            item.update(canonical)
            item["activity"] = _single_line(item.get("activity") or item.get("title"), 120)
            item["date"] = today
        return items

    def _normalize_plan_clock_range(self, value: Any) -> tuple[str, str]:
        """Normalize common model time forms and extract an optional range end."""

        text = _single_line(value, 32).strip()
        if not text:
            return "", ""
        matches = re.findall(r"(?<!\d)(\d{1,2})\s*[:：点时]\s*(\d{1,2})?", text)
        clocks: list[str] = []
        for hour_text, minute_text in matches[:2]:
            hour = int(hour_text)
            minute = int(minute_text or 0)
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                clocks.append(f"{hour:02d}:{minute:02d}")
        if not clocks:
            return "", ""
        return clocks[0], clocks[1] if len(clocks) > 1 else ""

    def _normalize_plan_clock(self, value: Any) -> str:
        start, _end = self._normalize_plan_clock_range(value)
        return start

    @staticmethod
    def _soften_destructive_daily_plan_text(text: str) -> str:
        softened = _single_line(text, 160)
        if not softened:
            return ""
        replacements = [
            (r"想[^，。,；;]{0,18}(砸|摔|打人|揍人|报复|毁掉|弄坏)[^，。,；;]{0,18}", "烦得想先躲开一会儿"),
            (r"(把|将)[^，。,；;]{0,14}(砸|摔|扔)[^，。,；;]{0,14}(地上|墙上|门上|出去|烂|碎)[^，。,；;]{0,8}", "把手边的东西往里推了推"),
            (r"(砸|摔)(东西|门|墙|书|杯子|手机|笔)[^，。,；;]{0,8}", "把东西先放远一点"),
            (r"(骂人|想骂|吼人|想吼)[^，。,；;]{0,10}", "把话咽回去"),
        ]
        for pattern, replacement in replacements:
            softened = re.sub(pattern, replacement, softened)
        softened = re.sub(r"(烦躁|暴躁|恼火)到?有点?攻击性", "烦躁得有点想躲开", softened)
        softened = softened.replace("想砸东西的烦躁", "有点烦,但努力收着")
        softened = softened.replace("想摔东西的烦躁", "有点烦,但努力收着")
        return _single_line(softened, 160)

    def _get_current_plan_item(self, plan: dict[str, Any]) -> dict[str, str] | None:
        if not self._is_plan_date_active(plan.get("date")):
            return None
        items = plan.get("items")
        if not isinstance(items, list):
            return None
        current_minutes = self._effective_plan_now_minutes(str(plan.get("date") or ""))
        if current_minutes is None:
            return None
        selected = None
        selected_start: int | None = None
        starts = self._normalized_plan_item_starts(items)
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            if self._normalize_schedule_lifecycle_status(item.get("lifecycle_status")) == "cancelled":
                continue
            item_minutes = starts[index] if index < len(starts) else None
            if item_minutes is None:
                continue
            next_start = next((value for value in starts[index + 1 :] if value is not None), None)
            item_end = self._plan_item_end_minutes(item_minutes, item, next_start=next_start)
            if item_minutes <= current_minutes < item_end:
                selected = item
                selected_start = item_minutes
                break
        if isinstance(selected, dict):
            # A clock window is not execution evidence.  Keep confirmed
            # schedule commitments available through the future/schedule
            # policy, but expose current plan text only when a compatible
            # observation or a short-lived resolver commit exists.
            policy_allows_current = True
            policy_getter = getattr(self, "_agenda_disclosure_view", None)
            if callable(policy_getter):
                policy_allows_current = False
                try:
                    view = policy_getter("current_fact", now=self._environment_now(), max_entries=128)
                    values = view.get("entries", []) if isinstance(view, dict) else getattr(view, "entries", [])
                    selected_key = _single_line(selected.get("plan_id"), 120)
                    selected_pair = (
                        _single_line(selected.get("time"), 12),
                        _single_line(selected.get("activity") or selected.get("title"), 120),
                    )
                    for value in values if isinstance(values, list) else []:
                        if not isinstance(value, dict):
                            continue
                        value_key = _single_line(value.get("plan_id") or value.get("entry_id"), 120)
                        value_pair = (
                            _single_line(value.get("time"), 12),
                            _single_line(value.get("title") or value.get("activity"), 120),
                        )
                        if (selected_key and selected_key == value_key) or (selected_pair == value_pair and all(selected_pair)):
                            policy_allows_current = True
                            break
                except Exception:
                    policy_allows_current = False
            evidence_kind = _single_line(selected.get("evidence_kind"), 48).lower()
            fact_eligibility = _single_line(selected.get("fact_eligibility"), 48).lower()
            status = _single_line(selected.get("status"), 32).lower()
            # 睡眠/休息段是 Bot 的内部状态模拟，不是需要外部执行证据的日程动作：
            # 计划里的“睡觉”只表达“Bot 此刻该休息”的内部状态，不主张任何已发生
            # 的外部事实。若不在此豁免，上游 C3 证据认证门槛会让普通计划项
            # （evidence_kind/fact_eligibility 均为 none）在这里返回 None，
            # 睡眠状态机（_refresh_sleep_runtime_state）拿不到当前睡眠项，
            # 睡眠相位就会永远停在 awake。
            if not self._is_sleepy_plan_item(selected) and (
                not policy_allows_current
                or not (
                    evidence_kind in {"interaction", "tool_action", "external_record"}
                    and fact_eligibility in {"current_observed", "history_observed", ""}
                    and status in {"active", "completed", "partially_completed", ""}
                )
            ):
                runtime_getter = getattr(self, "_agenda_runtime_scene", None)
                if callable(runtime_getter):
                    try:
                        runtime = runtime_getter(now=self._environment_now())
                    except Exception:
                        runtime = None
                    if isinstance(runtime, dict):
                        return {
                            "time": self._minutes_to_hhmm(current_minutes),
                            "end": _single_line(runtime.get("valid_until"), 40),
                            "activity": _single_line(runtime.get("state"), 120),
                            "mood": "当前状态",
                            "message_seed": "",
                            "subject_actor_id": "bot_self",
                            "evidence_kind": "self_state_commit",
                            "fact_eligibility": "current_internal",
                            "materialization_state": "active",
                            "status": "active",
                        }
                return None
            plan_date = str(plan.get("date") or "").strip()
            if (
                plan_date
                and plan_date != _today_key()
                and current_minutes >= 24 * 60
                and selected_start is not None
                and self._is_sleepy_plan_item(selected)
            ):
                elapsed = max(0, current_minutes - selected_start)
                carried = dict(selected)
                carried["time"] = self._minutes_to_hhmm(current_minutes)
                runtime = {}
                state = self.data.get("daily_state", {})
                if isinstance(state, dict) and isinstance(state.get("sleep_runtime"), dict):
                    runtime = state.get("sleep_runtime", {})
                phase = str(runtime.get("phase") or "")
                if phase == "woken":
                    carried["activity"] = "夜里被消息轻轻叫醒，还半梦半醒地留着一点睡意。"
                    carried["mood"] = "刚醒，迷糊"
                    carried["message_seed"] = "像刚从睡里被叫醒；如果用户不继续聊，会慢慢把手机放下睡回去。"
                elif phase == "sleeping_again":
                    carried["activity"] = "刚才被叫醒过一下，现在又慢慢睡回去了。"
                    carried["mood"] = "重新睡着，安静"
                    carried["message_seed"] = "睡意重新接上了；再被唤起时会有一点断续的迷糊感。"
                elif elapsed >= 45:
                    carried["activity"] = "夜里还在睡着，睡意早已沉下去，睡眠正在安静延续。"
                    carried["mood"] = "睡着，安静"
                    carried["message_seed"] = "还在睡着。如果这时候被叫醒，会有点迷糊；没人继续打扰就会继续睡下去。"
                else:
                    carried["activity"] = "刚从前一晚的睡前片段进入休息，正在慢慢安静下来。"
                    carried["mood"] = _single_line(selected.get("mood"), 40) or "安静"
                    carried["message_seed"] = "正在收声准备睡，语气会更轻。"
                return carried
            return selected
        return None

    def _get_clock_plan_item_for_display(self, plan: dict[str, Any]) -> dict[str, Any] | None:
        """Pick the scheduled row covering now for UI only, without claiming it happened."""

        if not isinstance(plan, dict) or not self._is_plan_date_active(plan.get("date")):
            return None
        items = plan.get("items")
        if not isinstance(items, list):
            return None
        now_minutes = self._effective_plan_now_minutes(str(plan.get("date") or ""))
        if now_minutes is None:
            return None
        starts = self._normalized_plan_item_starts(items)
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            if self._normalize_schedule_lifecycle_status(item.get("lifecycle_status")) == "cancelled":
                continue
            start = starts[index] if index < len(starts) else None
            if start is None:
                continue
            next_start = next((value for value in starts[index + 1 :] if value is not None), None)
            end = self._plan_item_end_minutes(start, item, next_start=next_start)
            if start <= now_minutes < end:
                return item
        return None

    def _format_daily_plan(self, plan: dict[str, Any]) -> str:
        if not plan or not plan.get("items"):
            return "今天还没有日程。"
        source = "模型生成" if plan.get("source") == "llm" else "备用日程"
        lines = [
            f"{runtime_persona_setting(self, 'bot_name', '小星')} 今天的日程（{plan.get('date', _today_key())},{source}）："
        ]
        state = self.data.get("daily_state", {})
        if isinstance(state, dict) and state.get("date") == plan.get("date"):
            lines.append(
                f"状态：能量 {state.get('energy', 70)}/100｜情绪偏{state.get('mood_bias', '平稳')}｜{state.get('sleep', '睡眠平稳')}"
            )
        status_labels = {
            "planned": "计划中",
            "active": "进行中",
            "completed": "已完成",
            "changed": "已变更",
            "cancelled": "已取消",
            "deferred": "已顺延",
            "unknown": "未核实",
            "overridden": "已被新安排覆盖",
        }
        for index, item in enumerate(plan.get("items", [])):
            if not isinstance(item, dict):
                continue
            mood = f"｜{item.get('mood')}" if item.get("mood") else ""
            window = f"{item.get('time')}-{item.get('end')}" if item.get("end") else str(item.get("time") or "")
            lifecycle = self._plan_item_display_status(plan, item, index)
            status = status_labels.get(lifecycle, "计划中")
            lines.append(f"{window}｜{status} {item.get('activity')}{mood}")
        archive_warning = _memory_archive_warning(plan)
        if archive_warning:
            lines.append(archive_warning)
        return "\n".join(lines)

