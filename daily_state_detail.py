# -*- coding: utf-8 -*-
"""detail 域。

由 tools/split_mixin_domain.py 从 daily_state.py 机械抽取（41 个方法 + 0 个模块级名字 + 0 个类级赋值 / 1404 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 DailyStateMixin）。
"""
from __future__ import annotations

import re
import uuid
import zoneinfo
from .conversation_prompt_section import PromptSection
from .helpers import _now_ts, _safe_float, _safe_int, _single_line, _today_key
from .model_routing import scope_allows
from .persona_config import runtime_persona_setting
from .planning import (
    build_detail_enhancement_prompt,
    build_detail_enhancement_prompt_section,
    generate_detail_enhancement,
    normalize_story_items,
    normalize_story_plan,
    pick_detail_segment,
)
from copy import deepcopy
from datetime import datetime, timedelta
from typing import Any

from .logging_util import get_module_logger

logger = get_module_logger(__name__)



class DailyStateDetailMixin:
    """detail 域（从 DailyStateMixin 拆出）。"""


    def _sync_detail_enhancement_day_locked(
        self,
        plan_date: Any,
        *,
        reset: bool = False,
    ) -> bool:
        """Keep live detail snapshots bound to the plan that owns them."""
        date_key = _single_line(plan_date, 16)
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_key):
            return False
        enhanced = self.data.get("detail_enhanced_segments")
        changed = (
            bool(reset)
            or _single_line(self.data.get("detail_enhanced_day"), 16) != date_key
            or not isinstance(enhanced, dict)
        )
        if not changed:
            return False
        self.data["detail_enhanced_day"] = date_key
        self.data["detail_enhanced_segments"] = {}
        story = self.data.get("daily_story_plan")
        if bool(reset) or (
            isinstance(story, dict)
            and _single_line(story.get("date"), 16) not in {"", date_key}
        ):
            self.data["daily_story_plan"] = {}
        return True

    async def _ensure_detail_enhancement(self, force: bool = False) -> dict[str, Any] | None:
        if not runtime_persona_setting(self, "enable_detail_enhancement", False) and not force:
            return None
        async with self._data_lock:
            plan = dict(self.data.get("daily_plan", {}))
            plan_date = str(plan.get("date") or "")
            if not self._is_plan_date_active(plan_date):
                return None
            self._sync_detail_enhancement_day_locked(plan_date)
            state = dict(self.data.get("daily_state", {}))
            enhanced = self.data.setdefault("detail_enhanced_segments", {})
            if not isinstance(enhanced, dict):
                enhanced = {}
                self.data["detail_enhanced_segments"] = enhanced
            sanitized_existing = False
            if self._sanitize_detail_enhanced_segments_inplace(enhanced):
                sanitized_existing = True
            story_plan_existing = self.data.get("daily_story_plan", {})
            if isinstance(story_plan_existing, dict) and self._sanitize_story_plan_social_facts_inplace(story_plan_existing):
                sanitized_existing = True
            segments = self._collect_due_detail_segments(plan, enhanced, force=force)
            if not segments:
                if sanitized_existing:
                    self._save_data_sync(
                        sections={"detail_enhanced_segments", "daily_story_plan"}
                    )
                return None
            for segment in segments:
                generation_id = uuid.uuid4().hex
                segment["_generation_id"] = generation_id
                enhanced[segment["key"]] = {
                    "status": "generating",
                    "started_at": self._environment_now().strftime("%H:%M"),
                    "started_ts": _now_ts(),
                    "generation_id": generation_id,
                }
            self._save_data_sync(sections={"detail_enhanced_segments"})

        last_detail = None
        for segment in segments:
            try:
                detail = await self._generate_detail_enhancement(segment, plan, state)
                if not isinstance(detail.get("today_events"), list) or not detail.get("today_events"):
                    raise RuntimeError("日程细化结果为空或无法解析")
            except Exception as exc:
                now_ts = _now_ts()
                retry_after_ts = now_ts + 30 * 60
                failure_is_current = False
                async with self._data_lock:
                    failure_is_current = self._detail_generation_is_current(
                        segment,
                        str(segment.get("_generation_id") or ""),
                    )
                    if failure_is_current:
                        enhanced = self.data.setdefault("detail_enhanced_segments", {})
                        if not isinstance(enhanced, dict):
                            enhanced = {}
                            self.data["detail_enhanced_segments"] = enhanced
                        retry_after = self._environment_fromtimestamp(retry_after_ts).strftime("%H:%M")
                        enhanced[segment["key"]] = {
                            "status": "failed",
                            "updated_at": self._environment_now().strftime("%H:%M"),
                            "error": _single_line(exc, 180),
                            "retry_after": retry_after,
                            "retry_after_ts": retry_after_ts,
                            "summary": "这一段细化生成失败，稍后会自动重试。",
                            "today_events": [],
                            "proactive_events": [],
                            "state_variables": [],
                            "presence_status": {},
                            "interaction_updates": [],
                            "coverage_repair_done": bool(segment.get("_coverage_repair")),
                        }
                        self._save_data_sync(sections={"detail_enhanced_segments"})
                    else:
                        retry_after = ""
                if failure_is_current:
                    logger.warning(
                        "日程细化生成失败,已标记为可重试: segment=%s retry_after=%s error=%s",
                        _single_line(segment.get("key"), 80),
                        retry_after,
                        _single_line(exc, 180),
                    )
                else:
                    logger.info(
                        "日程细化失败结果已过期,不再回写: segment=%s error=%s",
                        _single_line(segment.get("key"), 80),
                        _single_line(exc, 180),
                    )
                if force and failure_is_current:
                    raise
                continue
            self._sanitize_detail_snapshot_for_segment_inplace(
                detail,
                segment,
                field=f"detail_enhanced_segments.{segment.get('key') or 'current'}",
            )
            async with self._data_lock:
                if not self._detail_generation_is_current(
                    segment,
                    str(segment.get("_generation_id") or ""),
                ):
                    continue
                story_plan = self.data.setdefault("daily_story_plan", {})
                if not isinstance(story_plan, dict) or story_plan.get("date") != plan_date:
                    story_plan = {
                        "date": plan_date,
                        "today_events": [],
                        "proactive_events": [],
                        "long_term_events": [],
                    }
                    self.data["daily_story_plan"] = story_plan
                self._merge_detail_enhancement(story_plan, detail)
                self._sanitize_story_plan_social_facts_inplace(story_plan)
                enhanced = self.data.setdefault("detail_enhanced_segments", {})
                enhanced[segment["key"]] = {
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
                    "interaction_updates": [],
                    "coverage_repair_done": bool(segment.get("_coverage_repair")),
                }
                self._sanitize_detail_enhanced_segments_inplace(enhanced)
                meal_entries = self._append_self_meal_log(
                    self._collect_self_meal_events_from_detail(segment=segment, plan=plan, detail=detail),
                    segment=segment,
                    plan=plan,
                )
                self._remember_detail_enhancement_history(plan_date, enhanced, story_plan)
                self._refresh_daily_state_location_from_plan(
                    plan=plan,
                    detail=detail,
                    segment=segment,
                )
                self._reschedule_users_for_new_detail_events(segment)
                self._save_data_sync(
                    sections={
                        "daily_plan",
                        "daily_state",
                        "detail_enhanced_segments",
                        "detail_enhanced_history",
                        "daily_story_plan",
                        "daily_story_plan_history",
                        "users",
                        "self_meal_log",
                    }
                )
                last_detail = detail
            for meal_entry in meal_entries:
                await self._memory_companion_record_self_meal(meal_entry)
            if meal_entries:
                self._schedule_data_save(sections={"self_meal_log"})
            await self._apply_detail_presence_status(segment, detail)
        return last_detail

    def _collect_detail_segments(
        self,
        plan: dict[str, Any],
        enhanced: dict[str, Any],
        *,
        include_cancelled: bool = False,
    ) -> list[dict[str, Any]]:
        if not isinstance(plan, dict) or not self._is_plan_date_active(plan.get("date")):
            return []
        items = plan.get("items")
        if not isinstance(items, list) or not items:
            return []
        starts = self._normalized_plan_item_starts(items)
        parsed = []
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            start = starts[index] if index < len(starts) else None
            if start is None:
                continue
            parsed.append((index, start, item))
        if not parsed:
            return []
        segments: list[dict[str, Any]] = []
        for pos, (index, start, item) in enumerate(parsed):
            if not include_cancelled and self._normalize_schedule_lifecycle_status(item.get("lifecycle_status")) == "cancelled":
                continue
            key = f"{plan.get('date')}:{index}:{item.get('time')}"
            if self._detail_enhancement_snapshot_blocks_generation(enhanced.get(key) if isinstance(enhanced, dict) else None):
                continue
            next_start = (
                parsed[pos + 1][1]
                if pos + 1 < len(parsed)
                else None
            )
            end = self._plan_item_end_minutes(start, item, next_start=next_start)
            segments.append(
                {
                    "key": key,
                    "plan_date": str(plan.get("date") or ""),
                    "index": index,
                    "start": start,
                    "end": end,
                    "previous_item": next(
                        (
                            candidate[2]
                            for candidate in reversed(parsed[:pos])
                            if self._normalize_schedule_lifecycle_status(candidate[2].get("lifecycle_status")) != "cancelled"
                        ),
                        None,
                    ),
                    "item": item,
                    "next_item": next(
                        (
                            candidate[2]
                            for candidate in parsed[pos + 1 :]
                            if self._normalize_schedule_lifecycle_status(candidate[2].get("lifecycle_status")) != "cancelled"
                        ),
                        None,
                    ),
                }
            )
        return segments

    def _detail_generation_is_current(self, segment: dict[str, Any], generation_id: str) -> bool:
        key = _single_line(segment.get("key"), 120)
        if not key or not generation_id:
            return False
        enhanced = self.data.get("detail_enhanced_segments", {})
        snapshot = enhanced.get(key) if isinstance(enhanced, dict) else None
        if not isinstance(snapshot, dict):
            return False
        if _single_line(snapshot.get("status"), 24) != "generating":
            return False
        if _single_line(snapshot.get("generation_id"), 64) != generation_id:
            return False
        live_plan = self.data.get("daily_plan", {})
        plan_date = _single_line(segment.get("plan_date"), 16)
        if not isinstance(live_plan, dict) or _single_line(live_plan.get("date"), 16) != plan_date:
            return False
        items = live_plan.get("items")
        index = _safe_int(segment.get("index"), -1, minimum=-1)
        if not isinstance(items, list) or not (0 <= index < len(items)) or not isinstance(items[index], dict):
            return False
        item = items[index]
        expected_key = f"{plan_date}:{index}:{item.get('time')}"
        if expected_key != key:
            return False
        return self._normalize_schedule_lifecycle_status(item.get("lifecycle_status")) != "cancelled"

    def _detail_enhancement_snapshot_blocks_generation(self, snapshot: Any) -> bool:
        if not isinstance(snapshot, dict):
            return False
        status = _single_line(snapshot.get("status"), 24)
        if status in {"done", "cancelled"}:
            return True
        if status == "failed":
            retry_after_ts = _safe_float(snapshot.get("retry_after_ts"), 0)
            return retry_after_ts > _now_ts()
        if status == "generating":
            started_ts = _safe_float(snapshot.get("started_ts"), 0)
            if started_ts > 0:
                return _now_ts() - started_ts < 30 * 60
            started_at = _single_line(snapshot.get("started_at"), 8)
            started_minutes = self._parse_hhmm_to_minutes(started_at)
            if started_minutes is None:
                return False
            elapsed_minutes = self._environment_now_minutes() - started_minutes
            if elapsed_minutes < 0:
                elapsed_minutes += 24 * 60
            return elapsed_minutes < 30
        if status:
            return False
        return bool(snapshot.get("summary") or snapshot.get("today_events") or snapshot.get("proactive_events"))

    def _collect_due_detail_segments(
        self,
        plan: dict[str, Any],
        enhanced: dict[str, Any],
        *,
        force: bool = False,
    ) -> list[dict[str, Any]]:
        segments = self._collect_detail_segments(plan, enhanced if isinstance(enhanced, dict) else {})
        if not segments:
            return []
        if force:
            picked = self._current_detail_segment_for_update() or self._pick_detail_segment(plan, {})
            return [picked] if isinstance(picked, dict) else segments[:1]
        due = [segment for segment in segments if self._detail_segment_is_due(segment)]
        if due:
            return due[:1]

        story_plan = self.data.get("daily_story_plan", {})
        if not isinstance(story_plan, dict):
            story_plan = {}
        repaired: list[dict[str, Any]] = []
        all_segments = self._collect_detail_segments(plan, {})
        for segment in all_segments:
            if not self._detail_segment_is_due(segment):
                continue
            key = str(segment.get("key") or "")
            status = enhanced.get(key) if isinstance(enhanced, dict) else None
            if not isinstance(status, dict) or status.get("status") != "done":
                continue
            if status.get("coverage_repair_done"):
                continue
            if self._detail_segment_has_story_coverage(segment, story_plan):
                continue
            repaired_segment = dict(segment)
            repaired_segment["_coverage_repair"] = True
            repaired.append(repaired_segment)
        return repaired[:1]

    def _detail_segment_is_due(self, segment: dict[str, Any]) -> bool:
        if not isinstance(segment, dict):
            return False
        plan_date = str(self.data.get("daily_plan", {}).get("date") or "")
        now_minutes = self._effective_plan_now_minutes(plan_date)
        if now_minutes is None:
            return False
        start = _safe_int(segment.get("start"), 0)
        end = _safe_int(segment.get("end"), self._segment_end_minutes(start, segment.get("item")))
        lead = max(0, _safe_int(runtime_persona_setting(self, "detail_enhancement_lead_minutes", 3), 3, 0))
        return start - lead <= now_minutes < end

    def _detail_segment_has_story_coverage(
        self,
        segment: dict[str, Any],
        story_plan: dict[str, Any],
    ) -> bool:
        if not isinstance(segment, dict) or not isinstance(story_plan, dict):
            return False
        start = _safe_int(segment.get("start"), 0)
        end = _safe_int(segment.get("end"), self._segment_end_minutes(start, segment.get("item")))
        for key in ("today_events", "proactive_events"):
            raw_items = story_plan.get(key, [])
            if not isinstance(raw_items, list):
                continue
            for item in raw_items:
                if not isinstance(item, dict):
                    continue
                item_start, item_end = self._parse_window_minutes(str(item.get("window") or ""))
                if item_start is None or item_end is None:
                    continue
                if item_end < item_start:
                    item_end += 24 * 60
                if item_start < end and item_end > start:
                    return True
        return False

    def _pick_detail_segment(
        self, plan: dict[str, Any], enhanced: dict[str, Any]
    ) -> dict[str, Any] | None:
        return pick_detail_segment(self, plan, enhanced)

    async def _generate_detail_enhancement(
        self,
        segment: dict[str, Any],
        plan: dict[str, Any],
        state: dict[str, Any],
    ) -> dict[str, Any]:
        return await generate_detail_enhancement(self, segment, plan, state)

    def _merge_detail_enhancement(
        self, story_plan: dict[str, Any], detail: dict[str, Any]
    ) -> None:
        for key, limit in (
            ("today_events", 16),
            ("proactive_events", 12),
            ("long_term_events", 6),
        ):
            existing = story_plan.setdefault(key, [])
            if not isinstance(existing, list):
                existing = []
                story_plan[key] = existing
            additions = detail.get(key, [])
            if isinstance(additions, list):
                existing.extend(
                    item
                    for item in additions
                    if isinstance(item, dict)
                    and self._normalize_schedule_lifecycle_status(item.get("lifecycle_status")) != "cancelled"
                )
                story_plan[key] = self._trim_story_plan_items(key, existing, limit)

    def _rebuild_story_plan_from_detail_snapshots(self, plan_date: str) -> dict[str, Any]:
        rebuilt: dict[str, Any] = {
            "date": _single_line(plan_date, 16),
            "today_events": [],
            "proactive_events": [],
            "long_term_events": [],
        }
        enhanced = self._detail_enhanced_segments_for_plan_date(plan_date)
        for snapshot in enhanced.values():
            if snapshot.get("status") != "done":
                continue
            self._merge_detail_enhancement(rebuilt, snapshot)
        self._sanitize_story_plan_social_facts_inplace(rebuilt)
        self.data["daily_story_plan"] = rebuilt
        return rebuilt

    def _remember_detail_enhancement_history(
        self,
        date_text: str,
        enhanced: dict[str, Any],
        story_plan: dict[str, Any],
    ) -> None:
        date_key = _single_line(date_text, 16)
        if not date_key:
            return
        history = self.data.setdefault("detail_enhanced_history", [])
        if not isinstance(history, list):
            history = []
            self.data["detail_enhanced_history"] = history
        history[:] = [
            old
            for old in history
            if not (isinstance(old, dict) and _single_line(old.get("date"), 16) == date_key)
        ]
        history.append(
            {
                "date": date_key,
                "updated_at": self._environment_now().strftime("%Y-%m-%d %H:%M"),
                "segments": dict(enhanced or {}),
            }
        )
        del history[:-14]

        story_history = self.data.setdefault("daily_story_plan_history", [])
        if not isinstance(story_history, list):
            story_history = []
            self.data["daily_story_plan_history"] = story_history
        story_history[:] = [
            old
            for old in story_history
            if not (isinstance(old, dict) and _single_line(old.get("date"), 16) == date_key)
        ]
        compact_story = dict(story_plan or {})
        compact_story["date"] = date_key
        story_history.append(compact_story)
        del story_history[:-14]

    def _pick_story_items_with_coverage(
        self,
        ordered: list[dict[str, Any]],
        limit: int,
    ) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        total = len(ordered)
        if total <= limit:
            return ordered
        now_minutes = self._environment_now_minutes()
        selected: set[int] = {0, total - 1}
        closest_index = min(
            range(total),
            key=lambda idx: self._story_item_time_distance(ordered[idx], now_minutes),
        )
        for idx in range(max(0, closest_index - 2), min(total, closest_index + 3)):
            selected.add(idx)
        if limit == 1:
            selected = {closest_index}
        else:
            for slot in range(limit):
                selected.add(round(slot * (total - 1) / max(1, limit - 1)))
        if len(selected) < limit:
            for idx in range(total):
                selected.add(idx)
                if len(selected) >= limit:
                    break
        return [ordered[idx] for idx in sorted(selected)[:limit]]

    def _story_item_time_distance(self, item: dict[str, Any], now_minutes: int) -> int:
        start, end = self._parse_window_minutes(str(item.get("window") or ""))
        if start is None or end is None:
            return 99_999
        if end < start:
            end += 24 * 60
        current = now_minutes
        if current < start and end > 24 * 60:
            current += 24 * 60
        if start <= current < end:
            return 0
        return min(abs(current - start), abs(current - end))

    def _normalize_story_plan(self, payload: dict[str, Any]) -> dict[str, Any]:
        return normalize_story_plan(self, payload)

    def _normalize_story_items(self, raw_items: Any, text_key: str) -> list[dict[str, Any]]:
        return normalize_story_items(self, raw_items, text_key)

    def _build_detail_enhancement_prompt(
        self,
        segment: dict[str, Any],
        plan: dict[str, Any],
        state: dict[str, Any],
        memory_companion_context: str = "",
    ) -> str:
        return build_detail_enhancement_prompt(self, segment, plan, state, memory_companion_context=memory_companion_context)

    def _build_detail_enhancement_prompt_section(
        self,
        segment: dict[str, Any],
        plan: dict[str, Any],
        state: dict[str, Any],
        memory_companion_context: str = "",
    ) -> PromptSection:
        return build_detail_enhancement_prompt_section(
            self,
            segment,
            plan,
            state,
            memory_companion_context=memory_companion_context,
        )

    def _current_detail_segment_for_update(self) -> dict[str, Any] | None:
        plan = self.data.get("daily_plan", {})
        if not isinstance(plan, dict) or not self._is_plan_date_active(plan.get("date")):
            return None
        now_minutes = self._effective_plan_now_minutes(str(plan.get("date") or ""))
        if now_minutes is None:
            return None
        for segment in self._collect_detail_segments(plan, {}):
            start = _safe_int(segment.get("start"), 0)
            end = _safe_int(segment.get("end"), self._segment_end_minutes(start, segment.get("item")))
            lead = max(0, _safe_int(runtime_persona_setting(self, "detail_enhancement_lead_minutes", 3), 3, 0))
            if start - lead <= now_minutes < end:
                return segment
        return None

    def _current_detail_snapshot_for_update(self) -> dict[str, Any] | None:
        """Return the finished detail snapshot for the clock-current plan segment."""

        segment = self._current_detail_segment_for_update()
        if not isinstance(segment, dict):
            return None
        plan_date = _single_line(segment.get("plan_date"), 16)
        now_minutes = self._effective_plan_now_minutes(plan_date)
        if now_minutes is None:
            return None
        start = _safe_int(segment.get("start"), -1, minimum=-1)
        end = _safe_int(segment.get("end"), -1, minimum=-1)
        if start < 0 or end < 0:
            return None
        if end <= start:
            end += 24 * 60
        # Detail generation may select the next segment during its lead
        # window. Passive state material must remain tied to the actual clock
        # window so it cannot describe the next scene early.
        if not (start <= now_minutes < end):
            return None
        segment_item = segment.get("item")
        if isinstance(segment_item, dict):
            lifecycle = self._normalize_schedule_lifecycle_status(
                segment_item.get("lifecycle_status") or segment_item.get("status")
            )
            # A finished detail snapshot still describes its parent plan. If
            # that plan was changed, deferred, cancelled, or already closed,
            # the old atmosphere must not survive as current prompt material.
            if lifecycle not in {"", "planned", "active"}:
                return None
        enhanced = self.data.get("detail_enhanced_segments", {})
        if not isinstance(enhanced, dict):
            return None
        snapshot = enhanced.get(str(segment.get("key") or ""))
        if not isinstance(snapshot, dict):
            return None
        status = _single_line(snapshot.get("status"), 24).lower()
        if status and status != "done":
            return None
        return snapshot

    def _record_detail_interaction_update(self, item: dict[str, Any]) -> None:
        segment = self._current_detail_segment_for_update()
        if not segment:
            return
        enhanced = self.data.get("detail_enhanced_segments", {})
        if not isinstance(enhanced, dict):
            return
        key = str(segment.get("key") or "")
        snapshot = enhanced.get(key)
        if not isinstance(snapshot, dict):
            return
        updates = snapshot.setdefault("interaction_updates", [])
        if not isinstance(updates, list):
            updates = []
            snapshot["interaction_updates"] = updates
        source = _single_line(item.get("source"), 24)
        if source == "用户换装":
            updates[:] = [
                update
                for update in updates
                if not (isinstance(update, dict) and _single_line(update.get("source"), 24) == source)
            ]
        updates.append(
            {
                "at": self._environment_now().strftime("%H:%M"),
                "source": source,
                "user_text": _single_line(item.get("user_text"), 80),
                "intensity": _single_line(item.get("intensity"), 16),
                "scope": _single_line(item.get("scope"), 40),
                "reaction": _single_line(item.get("immediate_reaction"), 140),
                "state_updates": item.get("state_updates", []),
                "source_role": _single_line(item.get("source_role"), 20),
                "source_user_id": _single_line(item.get("source_user_id"), 80),
            }
        )
        del updates[:-6]
        self._apply_interaction_to_snapshot_state(snapshot, item)

    def _cleanup_false_sleep_interaction_updates(self) -> bool:
        plan = self.data.get("daily_plan", {})
        enhanced = self.data.get("detail_enhanced_segments", {})
        if not isinstance(plan, dict) or not isinstance(enhanced, dict):
            return False
        false_sources = {"睡眠中被用户唤醒", "睡眠中再次被唤醒", "睡眠中醒后续聊"}
        false_user_texts: set[str] = set()
        changed = False
        for segment in self._collect_detail_segments(plan, {}):
            if self._is_sleepy_plan_item(segment.get("item")):
                continue
            snapshot = enhanced.get(str(segment.get("key") or ""))
            if not isinstance(snapshot, dict):
                continue
            updates = snapshot.get("interaction_updates", [])
            if not isinstance(updates, list):
                continue
            removed = [
                item for item in updates
                if isinstance(item, dict) and _single_line(item.get("source"), 24) in false_sources
            ]
            if not removed:
                continue
            snapshot["interaction_updates"] = [item for item in updates if item not in removed]
            removed_state_names: set[str] = set()
            summary = str(snapshot.get("summary") or "")
            for item in removed:
                user_text = _single_line(item.get("user_text"), 120)
                if user_text:
                    false_user_texts.add(user_text)
                for state_update in item.get("state_updates", []) if isinstance(item.get("state_updates"), list) else []:
                    name, _value, _note = self._parse_state_update_text(state_update)
                    if name:
                        removed_state_names.add(name)
                reaction = _single_line(item.get("reaction"), 140)
                if reaction:
                    summary = summary.replace(f"；用户介入后：{reaction}", "").replace(f"用户介入后：{reaction}", "")
            snapshot["summary"] = _single_line(summary, 160)
            remaining_state_names = {
                self._parse_state_update_text(state_update)[0]
                for item in snapshot["interaction_updates"]
                if isinstance(item, dict) and isinstance(item.get("state_updates"), list)
                for state_update in item.get("state_updates", [])
            }
            variables = snapshot.get("state_variables", [])
            if isinstance(variables, list):
                snapshot["state_variables"] = [
                    variable for variable in variables
                    if not (
                        isinstance(variable, dict)
                        and _single_line(variable.get("name"), 32) in removed_state_names - remaining_state_names
                        and str(variable.get("note") or "").startswith("用户介入：")
                    )
                ]
            changed = True
        if false_user_texts:
            adjustments = self.data.get("schedule_adjustments", [])
            if isinstance(adjustments, list):
                kept = [
                    item for item in adjustments
                    if not (
                        isinstance(item, dict)
                        and _single_line(item.get("source"), 24) in false_sources
                        and _single_line(item.get("user_text"), 120) in false_user_texts
                    )
                ]
                if len(kept) != len(adjustments):
                    self.data["schedule_adjustments"] = kept
                    changed = True
            runtime = self.data.get("daily_state", {}).get("sleep_runtime") if isinstance(self.data.get("daily_state"), dict) else None
            if isinstance(runtime, dict) and runtime.get("phase") in {"woken", "sleeping_again"}:
                if _single_line(runtime.get("last_user_text"), 120) in false_user_texts:
                    runtime.update(
                        {
                            "phase": "awake",
                            "label": self._sleep_phase_label("awake"),
                            "updated_at": _now_ts(),
                            "last_event": "已清理普通休闲段的错误睡眠唤醒记录",
                            "source": "cleanup",
                        }
                    )
                    changed = True
        if changed:
            logger.info("已清理普通休闲段的错误睡眠唤醒记录")
        return changed

    def _invalidate_detail_after_interaction(self, *, now: float | None = None) -> None:
        plan = self.data.get("daily_plan", {})
        if not isinstance(plan, dict) or not self._is_plan_date_active(plan.get("date")):
            return
        now_minutes = self._effective_plan_now_minutes(str(plan.get("date") or ""))
        if now_minutes is None:
            return
        enhanced = self.data.get("detail_enhanced_segments", {})
        if isinstance(enhanced, dict):
            for segment in self._collect_detail_segments(plan, {}):
                start = _safe_int(segment.get("start"), 0)
                if start > now_minutes:
                    key = str(segment.get("key") or "")
                    if key in enhanced:
                        enhanced.pop(key, None)
        story_plan = self.data.get("daily_story_plan", {})
        if isinstance(story_plan, dict):
            for key in ("today_events", "proactive_events"):
                items = story_plan.get(key, [])
                if not isinstance(items, list):
                    continue
                kept = []
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    start, end = self._parse_window_minutes(str(item.get("window") or ""))
                    if start is None or end is None:
                        kept.append(item)
                        continue
                    if end < start:
                        end += 24 * 60
                    if start <= now_minutes:
                        kept.append(item)
                story_plan[key] = kept

    def _infer_location_from_text(self, text: str) -> str:
        normalized = _single_line(text, 200)
        if not normalized:
            return ""
        location_rules = [
            (("被窝", "床上", "床边", "卧室", "房间", "书桌", "台灯", "家里", "客厅", "沙发", "洗漱台", "餐桌"), "家里"),
            (("教室", "课间", "食堂", "校门", "走廊", "操场", "上课", "下课", "自习", "老师", "书包", "制服"), "学校"),
            (("工位", "会议", "办公室", "上班", "下班", "通勤", "打卡"), "工作场所"),
            (("便利店", "超市", "商店"), "便利店附近"),
            (("路上", "街上", "出门", "楼下", "外面", "街边", "回家路上", "校门口"), "外面"),
            (("楼梯口", "走廊栏杆", "窗边", "阳台"), "过道或窗边"),
        ]
        for keywords, label in location_rules:
            if any(keyword in normalized for keyword in keywords):
                return label
        return ""

    def _infer_location_from_plan_context(
        self,
        *,
        plan: dict[str, Any] | None = None,
        detail: dict[str, Any] | None = None,
    ) -> str:
        candidates: list[str] = []
        detail_allowed = self._detail_model_location_policy_allowed(detail)
        if isinstance(detail, dict) and detail_allowed:
            model_location = _single_line(detail.get("location"), 60)
            if model_location:
                return model_location
            for key in ("summary", "scene", "event", "topic"):
                text = _single_line(detail.get(key), 160)
                if text:
                    candidates.append(text)
            for list_key in ("today_events", "proactive_events"):
                raw_items = detail.get(list_key)
                if not isinstance(raw_items, list):
                    continue
                for item in raw_items[:6]:
                    if not isinstance(item, dict):
                        continue
                    candidates.append(
                        " ".join(
                            _single_line(item.get(key), 80)
                            for key in ("scene", "event", "content", "detail", "description", "topic", "why")
                            if _single_line(item.get(key), 80)
                        )
                    )
        plan = plan if isinstance(plan, dict) else self.data.get("daily_plan", {})
        current_item = self._get_current_plan_item(plan if isinstance(plan, dict) else {})
        if isinstance(current_item, dict):
            candidates.append(
                " ".join(
                    _single_line(current_item.get(key), 120)
                    for key in ("activity", "mood", "message_seed")
                    if _single_line(current_item.get(key), 120)
                )
            )
        # Do not inspect neighboring raw plan rows here.  They are future or
        # unverified projections and their clock distance cannot establish the
        # Bot's current location.  A current item above is already policy /
        # runtime qualified; a generated detail location is handled separately
        # by ``_refresh_daily_state_location_from_plan``.
        for text in candidates:
            inferred = self._infer_location_from_text(text)
            if inferred:
                return inferred
        return ""

    def _detail_model_location_policy_allowed(self, detail: dict[str, Any] | None = None) -> bool:
        """Allow a coherent schedule projection while keeping observed location distinct."""

        policy_getter = getattr(self, "_agenda_disclosure_view", None)
        if not callable(policy_getter):
            # Lightweight harnesses and legacy callers do not have C3 policy;
            # preserve their historical local projection behavior.
            return True
        payload = detail if isinstance(detail, dict) else {}
        evidence_kind = _single_line(payload.get("evidence_kind"), 48).lower()
        eligibility = _single_line(payload.get("fact_eligibility"), 48).lower()
        refs = payload.get("source_refs")
        has_refs = isinstance(refs, (list, tuple, set)) and any(_single_line(ref, 160) for ref in refs)
        if evidence_kind not in {"tool_action", "external_record"} or eligibility != "current_observed" or not has_refs:
            # A generated detail is part of the character's simulated day. It
            # may keep the active scene coherent, but is not observed evidence.
            location = _single_line(payload.get("location"), 60)
            basis = self._normalize_schedule_basis(payload.get("location_basis"), default=[])
            return bool(location and basis)
        try:
            view = policy_getter("current_fact", now=self._environment_now(), max_entries=128)
            entries = view.get("entries", []) if isinstance(view, dict) else getattr(view, "entries", [])
        except Exception:
            return False
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict):
                continue
            if _single_line(entry.get("subject_actor_id"), 120) != "bot_self":
                continue
            if _single_line(entry.get("fact_eligibility"), 48).lower() != "current_observed":
                continue
            entry_refs = entry.get("source_refs")
            if isinstance(entry_refs, str):
                entry_refs = [entry_refs]
            if isinstance(entry_refs, (list, tuple, set)) and any(
                _single_line(ref, 160) in {_single_line(value, 160) for value in refs}
                for ref in entry_refs
            ):
                return True
        return False

    def _refresh_daily_state_location_from_plan(
        self,
        *,
        plan: dict[str, Any] | None = None,
        detail: dict[str, Any] | None = None,
        segment: dict[str, Any] | None = None,
    ) -> bool:
        state = self.data.get("daily_state")
        if not isinstance(state, dict) or state.get("date") != _today_key():
            return False
        # Detail generation intentionally runs in a lead window.  A future
        # candidate may describe a likely place, but it is not current Bot
        # state until the segment actually starts.
        if isinstance(detail, dict) and isinstance(segment, dict):
            plan_date = _single_line((plan or {}).get("date"), 16) if isinstance(plan, dict) else ""
            now_minutes = self._effective_plan_now_minutes(plan_date)
            start_minutes = _safe_int(segment.get("start"), -1, minimum=-1)
            if now_minutes is not None and start_minutes >= 0 and now_minutes < start_minutes:
                return False
        override_ts = _safe_float(state.get("location_override_ts"), 0)
        model_location = (
            _single_line(detail.get("location"), 60)
            if isinstance(detail, dict) and self._detail_model_location_policy_allowed(detail)
            else ""
        )
        if override_ts > 0 and _now_ts() - override_ts < 4 * 3600 and not model_location:
            return False
        location = model_location or self._infer_location_from_plan_context(plan=plan, detail=detail)
        if not location and isinstance(segment, dict) and isinstance(segment.get("item"), dict):
            item = segment["item"]
            location = self._infer_location_from_text(
                " ".join(
                    _single_line(item.get(key), 120)
                    for key in ("activity", "mood", "message_seed")
                    if _single_line(item.get(key), 120)
                )
            )
        if not location:
            return False
        current = _single_line(state.get("location"), 40)
        if current == location:
            if not model_location:
                return False
            metadata_changed = False
            if _single_line(state.get("location_source"), 40) != "detail_model":
                state["location_source"] = "detail_model"
                metadata_changed = True
            projection_kind = (
                "observed"
                if _single_line(detail.get("fact_eligibility"), 48).lower() == "current_observed"
                else "schedule"
            )
            if _single_line(state.get("location_projection"), 24) != projection_kind:
                state["location_projection"] = projection_kind
                metadata_changed = True
            confidence = min(1.0, _safe_float(detail.get("location_confidence"), 0.72))
            basis = self._normalize_schedule_basis(detail.get("location_basis"), default=["coarse_plan"])
            if _safe_float(state.get("location_confidence"), -1) != confidence:
                state["location_confidence"] = confidence
                metadata_changed = True
            if state.get("location_basis") != basis:
                state["location_basis"] = basis
                metadata_changed = True
            if override_ts > 0:
                state["location_override_ts"] = 0.0
                metadata_changed = True
            if metadata_changed:
                state["location_updated_at"] = self._environment_now().strftime("%H:%M")
            return metadata_changed
        state["location"] = location
        if model_location:
            observed_location = _single_line(detail.get("fact_eligibility"), 48).lower() == "current_observed"
            state["location_source"] = "detail_model"
            state["location_projection"] = "observed" if observed_location else "schedule"
        else:
            state["location_source"] = "detail" if isinstance(detail, dict) else "daily_plan"
            state["location_projection"] = "schedule"
        if model_location:
            state["location_confidence"] = min(1.0, _safe_float(detail.get("location_confidence"), 0.72))
            state["location_basis"] = self._normalize_schedule_basis(detail.get("location_basis"), default=["coarse_plan"])
        state["location_updated_at"] = self._environment_now().strftime("%H:%M")
        if override_ts > 0:
            state["location_override_ts"] = 0.0
        return True

    def _apply_dialogue_location_override(self, location: str) -> None:
        """对话驱动的位置覆盖：用户带角色外出/回家时，立即更新 daily_state.location。

        这会覆盖日程推断的位置，直到下一次细化刷新自然恢复，或用户再次触发回家。
        """
        state = self.data.get("daily_state")
        if not isinstance(state, dict) or state.get("date") != _today_key():
            return
        state["location"] = _single_line(location, 40)
        state["location_source"] = "dialogue_override"
        state["location_updated_at"] = self._environment_now().strftime("%H:%M")
        state["location_override_ts"] = _now_ts()
        self._save_data_sync(sections={"daily_state"})

    def _current_detail_model_location(self) -> str:
        segment = self._current_detail_segment_for_update()
        if not isinstance(segment, dict):
            return ""
        enhanced = self.data.get("detail_enhanced_segments", {})
        snapshot = enhanced.get(str(segment.get("key") or "")) if isinstance(enhanced, dict) else None
        if not isinstance(snapshot, dict) or _single_line(snapshot.get("status"), 24) != "done":
            return ""
        if not self._detail_model_location_policy_allowed(snapshot):
            return ""
        return _single_line(snapshot.get("location"), 60)

    def _current_location_state_text(self, state: dict[str, Any] | None = None) -> str:
        model_location = self._current_detail_model_location()
        if model_location:
            return model_location
        if isinstance(state, dict):
            override_ts = _safe_float(state.get("location_override_ts"), 0)
            if override_ts > 0:
                override_location = _single_line(state.get("location"), 40)
                if override_location and override_location not in {"", "地点感平稳", "地点无明显变化"}:
                    now = _now_ts()
                    if now - override_ts < 4 * 3600:
                        return override_location
        snapshot = self._current_story_plan_snapshot()
        for candidate in (
            snapshot.get("scene"),
            snapshot.get("event"),
        ):
            inferred = self._infer_location_from_text(str(candidate or ""))
            if inferred:
                return inferred
        current_item = self._get_current_plan_item(self.data.get("daily_plan", {}))
        if isinstance(current_item, dict):
            inferred = self._infer_location_from_text(
                f"{_single_line(current_item.get('activity'), 120)} {_single_line(current_item.get('message_seed'), 120)}"
            )
            if inferred:
                return inferred
        if isinstance(state, dict):
            fallback = _single_line(state.get("location"), 40)
            if fallback and fallback not in {"", "地点感平稳", "地点无明显变化"}:
                return fallback
        return ""

    def _coarse_roleplay_location_text(self, location: str) -> str:
        text = _single_line(location, 40)
        if not text:
            return ""
        if any(token in text for token in ("家", "房间", "卧室", "客厅", "书桌", "床", "被窝", "阳台")):
            return "家里"
        if any(token in text for token in ("学校", "教室", "食堂", "校门", "操场", "走廊", "自习")):
            return "学校"
        if any(token in text for token in ("工作", "办公室", "工位", "会议", "通勤")):
            return "工作地点"
        if any(token in text for token in ("路", "街", "外面", "楼下", "出门")):
            return "外面"
        if any(token in text for token in ("便利店", "超市", "商店")):
            return "外面"
        return text if text in {"家里", "学校", "工作地点", "外面", "路上"} else ""

    def _current_story_plan_snapshot(self) -> dict[str, Any]:
        plan = self.data.get("daily_story_plan", {})
        if not isinstance(plan, dict) or not self._is_plan_date_active(plan.get("date")):
            return {}
        now_minutes = self._effective_plan_now_minutes(str(plan.get("date") or ""))
        if now_minutes is None:
            return {}

        snapshot: dict[str, Any] = {}
        summary = _single_line(plan.get("summary"), 120)
        if summary:
            snapshot["summary"] = summary

        current_event = None
        for item in plan.get("today_events", []):
            if not isinstance(item, dict):
                continue
            if self._normalize_schedule_lifecycle_status(item.get("lifecycle_status")) == "cancelled":
                continue
            start, end = self._parse_window_minutes(str(item.get("window") or ""))
            if start is None or end is None:
                continue
            if start <= now_minutes < end:
                current_event = item
                break
        if isinstance(current_event, dict):
            snapshot["event"] = _single_line(current_event.get("event"), 100)
            snapshot["mood"] = _single_line(current_event.get("mood"), 24)

        current_proactive = None
        for item in plan.get("proactive_events", []):
            if not isinstance(item, dict):
                continue
            if self._normalize_schedule_lifecycle_status(item.get("lifecycle_status")) == "cancelled":
                continue
            start, end = self._parse_window_minutes(str(item.get("window") or ""))
            if start is None or end is None:
                continue
            if start <= now_minutes < end:
                current_proactive = item
                break
        if isinstance(current_proactive, dict):
            snapshot["topic"] = _single_line(current_proactive.get("topic"), 80)
            snapshot["scene"] = _single_line(current_proactive.get("scene"), 80)
            snapshot["tone"] = _single_line(current_proactive.get("tone"), 30)
            snapshot["impulse"] = _single_line(current_proactive.get("impulse"), 100)
        return snapshot

    def _detail_segment_bounds_for_snapshot_key(self, key: str) -> tuple[int, int] | None:
        match = re.fullmatch(r"\d{4}-\d{2}-\d{2}:(\d+):(\d{1,2}:\d{2})", str(key or ""))
        if not match:
            return None
        plan = getattr(self, "data", {}).get("daily_plan", {})
        items = plan.get("items") if isinstance(plan, dict) else None
        if not isinstance(items, list):
            return None
        index = _safe_int(match.group(1), -1, minimum=-1)
        start = self._parse_hhmm_to_minutes(match.group(2))
        if index < 0 or start is None:
            return None
        next_start = None
        for next_item in items[index + 1 :]:
            if not isinstance(next_item, dict):
                continue
            next_start = self._parse_hhmm_to_minutes(next_item.get("time"))
            if next_start is not None:
                break
        current_item = items[index] if index < len(items) and isinstance(items[index], dict) else None
        end = self._plan_item_end_minutes(start, current_item, next_start=next_start)
        return start, end

    @staticmethod
    def _deepseek_peak_minute(value: str, *, allow_24: bool = False) -> int | None:
        match = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", str(value or ""))
        if not match:
            return None
        hour, minute = int(match.group(1)), int(match.group(2))
        if allow_24 and hour == 24 and minute == 0:
            return 1440
        if hour > 23 or minute > 59:
            return None
        return hour * 60 + minute

    def _parse_deepseek_peak_windows(self) -> list[tuple[int, int]]:
        raw = str(getattr(self, "deepseek_peak_windows", "") or "")
        windows: list[tuple[int, int]] = []
        for item in re.split(r"[,，;；\n]+", raw):
            text = item.strip()
            if not text:
                continue
            match = re.fullmatch(r"\s*(\d{1,2}:\d{2})\s*[-~～—至]+\s*(\d{1,2}:\d{2})\s*", text)
            if not match:
                continue
            start = self._deepseek_peak_minute(match.group(1))
            end = self._deepseek_peak_minute(match.group(2), allow_24=True)
            if start is None or end is None or start == end:
                continue
            windows.append((start, end))
        return windows

    def _deepseek_peak_status(self, now: datetime | None = None) -> dict[str, Any]:
        timezone_name = str(getattr(self, "deepseek_peak_timezone", "Asia/Shanghai") or "Asia/Shanghai").strip()
        try:
            timezone = zoneinfo.ZoneInfo(timezone_name)
        except Exception:
            timezone_name = "Asia/Shanghai"
            timezone = zoneinfo.ZoneInfo(timezone_name)
        if now is None:
            local_now = datetime.now(timezone)
        elif now.tzinfo is None:
            local_now = now.replace(tzinfo=timezone)
        else:
            local_now = now.astimezone(timezone)
        windows = self._parse_deepseek_peak_windows()
        minute = local_now.hour * 60 + local_now.minute
        active = any(
            (start <= minute < end) if start < end else (minute >= start or minute < end)
            for start, end in windows
        )
        transitions: list[datetime] = []
        base_day = local_now.date()
        # Include yesterday so a cross-midnight window can expose its upcoming
        # end transition while the current time is after midnight.
        for day_offset in range(-1, 3):
            day = base_day + timedelta(days=day_offset)
            for start, end in windows:
                start_dt = datetime.combine(day, datetime.min.time(), timezone) + timedelta(minutes=start)
                end_day = day + timedelta(days=1) if start > end else day
                end_minute = end if end < 1440 else 0
                if end == 1440:
                    end_day = day + timedelta(days=1)
                end_dt = datetime.combine(end_day, datetime.min.time(), timezone) + timedelta(minutes=end_minute)
                if start_dt > local_now:
                    transitions.append(start_dt)
                if end_dt > local_now:
                    transitions.append(end_dt)
        next_transition = min(transitions) if transitions else None
        replacement_id = str(getattr(self, "deepseek_peak_replacement_provider_id", "") or "").strip()
        enabled = bool(getattr(self, "enable_deepseek_peak_replacement", False))
        return {
            "enabled": enabled,
            "active": bool(enabled and active and replacement_id),
            "in_window": active,
            "configured": bool(replacement_id),
            "timezone": timezone_name,
            "current_time": local_now.strftime("%Y-%m-%d %H:%M"),
            "next_transition": next_transition.strftime("%Y-%m-%d %H:%M") if next_transition else "",
            "windows": [
                f"{start // 60:02d}:{start % 60:02d}-{('24:00' if end == 1440 else f'{end // 60:02d}:{end % 60:02d}')}"
                for start, end in windows
            ],
            "replacement_provider_id": replacement_id,
        }

    def _apply_deepseek_peak_replacement(
        self,
        provider_id: str,
        *,
        now: datetime | None = None,
        target: str = "plugin",
    ) -> str:
        original = str(provider_id or "").strip()
        if not scope_allows(getattr(self, "model_replacement_scope", "plugin"), target):
            return original
        status = self._deepseek_peak_status(now)
        replacement = str(status.get("replacement_provider_id") or "").strip()
        if not status.get("active") or not original or not replacement or replacement == original:
            return original
        if not self._provider_matches_deepseek(original):
            return original
        log_key = f"{local_day if (local_day := status.get('current_time', '')[:10]) else ''}|{original}|{replacement}"
        if getattr(self, "_deepseek_peak_last_log_key", "") != log_key:
            self._deepseek_peak_last_log_key = log_key
            logger.info("DeepSeek 高价时段临时路由: %s -> %s (%s)", original, replacement, status.get("current_time"))
        return replacement

    @staticmethod
    def _detail_event_text(item: dict[str, Any], limit: int = 160) -> str:
        if not isinstance(item, dict):
            return ""
        for key in (
            "event",
            "content",
            "detail",
            "description",
            "text",
            "narrative",
            "body",
            "细化",
            "细化内容",
            "细化叙述",
            "事件",
            "主要事件",
        ):
            text = _single_line(item.get(key), limit)
            if text:
                return text
        return ""

    def _format_current_detail_view(self) -> str:
        plan = self.data.get("daily_plan", {})
        if not isinstance(plan, dict) or not plan.get("items"):
            return "今天还没有日程，所以也没有可看的当前细化。"
        enhanced = self.data.get("detail_enhanced_segments", {})
        if not isinstance(enhanced, dict):
            enhanced = {}
        segment = self._current_detail_segment_for_update() or self._pick_detail_segment(plan, enhanced)
        if not segment:
            return "当前还没有可用的细化结果。先让今天的日程段完成细化，或者手动执行一次“陪伴 重置细化”。"
        key = str(segment.get("key") or "")
        snapshot = enhanced.get(key) if key else None
        if not isinstance(snapshot, dict):
            return "当前时间段还没有落地的细化内容。可以先执行一次“陪伴 重置细化”。"
        snapshot = deepcopy(snapshot)
        self._sanitize_detail_enhanced_segments_inplace({"current": snapshot})

        item = segment.get("item") if isinstance(segment, dict) else {}
        start_text = self._minutes_to_hhmm(_safe_int(segment.get("start"), 0))
        end_text = self._minutes_to_hhmm(_safe_int(segment.get("end"), 0))
        lines = [
            f"当前细化时段：{start_text}-{end_text}",
            f"对应日程：{_single_line((item or {}).get('activity'), 120)}",
        ]
        mood = _single_line((item or {}).get("mood"), 24)
        if mood:
            lines.append(f"日程情绪：{mood}")

        state_variables = snapshot.get("state_variables", [])
        if isinstance(state_variables, list) and state_variables:
            lines.append("状态变量：")
            for variable in state_variables[:8]:
                if not isinstance(variable, dict):
                    continue
                name = _single_line(variable.get("name"), 32)
                value = _single_line(variable.get("value"), 60)
                note = _single_line(variable.get("note"), 80)
                if name and value:
                    lines.append(f"- {name}: {value}" + (f"（{note}）" if note else ""))

        presence = snapshot.get("presence_status")
        if isinstance(presence, dict):
            mode = _single_line(presence.get("mode"), 24)
            reason = _single_line(presence.get("reason"), 80)
            if mode and mode != "unchanged":
                lines.append("QQ状态表现：")
                lines.append(f"- {mode}" + (f"｜{reason}" if reason else ""))

        interaction_updates = snapshot.get("interaction_updates", [])
        if isinstance(interaction_updates, list) and interaction_updates:
            update_lines: list[str] = []
            for update in interaction_updates[-4:]:
                if not isinstance(update, dict):
                    continue
                if _single_line(update.get("source_role"), 20) != "owner":
                    continue
                at = _single_line(update.get("at"), 8)
                user_text = _single_line(update.get("user_text"), 80)
                intensity = _single_line(update.get("intensity"), 12)
                reaction = _single_line(update.get("reaction"), 120)
                state_updates = update.get("state_updates")
                state_text = ""
                if isinstance(state_updates, list) and state_updates:
                    state_text = "；".join(_single_line(item, 60) for item in state_updates if _single_line(item, 60))
                if reaction or user_text:
                    prefix = f"- {at} " if at else "- "
                    parts = [
                        f"用户：{user_text}" if user_text else "",
                        f"强度：{intensity}" if intensity else "",
                        reaction,
                        state_text,
                    ]
                    update_lines.append(prefix + "｜".join(part for part in parts if part))
            if update_lines:
                lines.append("用户介入后的局部更新：")
                lines.extend(update_lines)

        today_events = snapshot.get("today_events", [])
        scoped_today_events = self._filter_snapshot_items_to_segment(today_events, segment)
        if scoped_today_events:
            lines.append("细化内容：")
            for detail_event in scoped_today_events[:8]:
                if not isinstance(detail_event, dict):
                    continue
                window = _single_line(detail_event.get("window"), 24)
                event_text = self._detail_event_text(detail_event, 160)
                mood_text = _single_line(detail_event.get("mood"), 24)
                if event_text:
                    tail = f"｜{mood_text}" if mood_text else ""
                    lines.append(f"- {window}｜{event_text}{tail}")
        else:
            summary = _single_line(snapshot.get("summary"), 160)
            if summary and summary not in {"这一段按原日程慢慢推进。", "这一段按原日程慢慢推进"}:
                lines.append(f"细化内容：{summary}")
            else:
                lines.append("细化内容：当前没有生成出可展示的细化正文。")

        proactive_events = snapshot.get("proactive_events", [])
        if isinstance(proactive_events, list) and proactive_events:
            scoped_proactive_events = self._filter_snapshot_items_to_segment(proactive_events, segment)
            if scoped_proactive_events:
                lines.append("这一段的主动契机：")
            for proactive_event in scoped_proactive_events[:10]:
                if not isinstance(proactive_event, dict):
                    continue
                window = _single_line(proactive_event.get("window"), 24)
                reason = _single_line(proactive_event.get("reason"), 24)
                action = _single_line(proactive_event.get("action"), 24) or "message"
                topic = _single_line(proactive_event.get("topic"), 48)
                motive = _single_line(proactive_event.get("motive"), 80)
                why = _single_line(proactive_event.get("why"), 100)
                scene = _single_line(proactive_event.get("scene"), 60)
                tone = _single_line(proactive_event.get("tone"), 24)
                impulse = _single_line(proactive_event.get("impulse"), 80)
                lines.append(f"- {window}｜{reason}｜{action}｜{topic or motive or '（无话题）'}")
                if why:
                    lines.append(f"  why：{why}")
                if motive:
                    lines.append(f"  motive：{motive}")
                meta_bits = []
                if scene:
                    meta_bits.append(f"scene={scene}")
                if tone:
                    meta_bits.append(f"tone={tone}")
                if impulse:
                    meta_bits.append(f"impulse={impulse}")
                if meta_bits:
                    lines.append("  " + "｜".join(meta_bits))
                chain = proactive_event.get("chain")
                if isinstance(chain, list) and chain:
                    lines.append("  chain：")
                    for step in chain[:4]:
                        if not isinstance(step, dict):
                            continue
                        kind = _single_line(step.get("kind"), 24)
                        after_minutes = _safe_int(step.get("after_minutes"), 0, 0)
                        step_reason = _single_line(step.get("reason"), 24)
                        step_topic = _single_line(step.get("topic"), 48)
                        step_motive = _single_line(step.get("motive"), 80)
                        step_tone = _single_line(step.get("tone"), 24)
                        extra = []
                        if after_minutes > 0:
                            extra.append(f"{after_minutes} 分钟后")
                        if step_reason:
                            extra.append(step_reason)
                        if step_topic:
                            extra.append(step_topic)
                        if step_tone:
                            extra.append(f"tone={step_tone}")
                        if step_motive:
                            extra.append(f"motive={step_motive}")
                        lines.append(f"    - {kind}" + (f"｜{'｜'.join(extra)}" if extra else ""))

        if len(lines) <= 4:
            lines.append("这段目前还比较空，说明细化结果里还没长出太多东西。")
        return "\n".join(lines)

    def _format_current_detail_brief(self) -> str:
        plan = self.data.get("daily_plan", {})
        if not isinstance(plan, dict) or not plan.get("items"):
            return "今天还没有日程，所以没有可细化的时间段。"
        enhanced = self.data.get("detail_enhanced_segments", {})
        if not isinstance(enhanced, dict):
            enhanced = {}
        segment = self._current_detail_segment_for_update() or self._pick_detail_segment(plan, enhanced)
        if not segment:
            return "当前还没有可用的细化结果。"
        key = str(segment.get("key") or "")
        snapshot = enhanced.get(key) if key else None
        if not isinstance(snapshot, dict):
            return "当前时间段还没有落地的细化内容。"
        snapshot = deepcopy(snapshot)
        self._sanitize_detail_enhanced_segments_inplace({"current": snapshot})

        item = segment.get("item") if isinstance(segment, dict) else {}
        start_text = self._minutes_to_hhmm(_safe_int(segment.get("start"), 0))
        end_text = self._minutes_to_hhmm(_safe_int(segment.get("end"), 0))
        lines = [
            f"{start_text}-{end_text}｜{_single_line((item or {}).get('activity'), 80)}",
        ]
        summary = _single_line(snapshot.get("summary"), 140)
        if summary:
            lines.append(summary)

        today_events = snapshot.get("today_events", [])
        if isinstance(today_events, list) and today_events:
            for detail_event in today_events[:3]:
                if not isinstance(detail_event, dict):
                    continue
                window = _single_line(detail_event.get("window"), 18)
                event_text = self._detail_event_text(detail_event, 120)
                mood_text = _single_line(detail_event.get("mood"), 20)
                if event_text:
                    lines.append(f"- {window} {event_text}" + (f"｜{mood_text}" if mood_text else ""))

        interaction_updates = snapshot.get("interaction_updates", [])
        if isinstance(interaction_updates, list) and interaction_updates:
            latest = next(
                (
                    item
                    for item in reversed(interaction_updates)
                    if isinstance(item, dict) and _single_line(item.get("source_role"), 20) == "owner"
                ),
                None,
            )
            if isinstance(latest, dict):
                user_text = _single_line(latest.get("user_text"), 60)
                reaction = _single_line(latest.get("reaction"), 100)
                if reaction or user_text:
                    lines.append("局部更新：" + "｜".join(part for part in (f"用户：{user_text}" if user_text else "", reaction) if part))

        return "\n".join(lines)

