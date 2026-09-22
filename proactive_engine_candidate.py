# -*- coding: utf-8 -*-
"""候选池/冲动池域。

由 tools/split_mixin_domain.py 从 proactive_engine.py 机械抽取（63 个方法 + 0 个模块级名字 + 0 个类级赋值 / 3005 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveEngineMixin）。
"""
from __future__ import annotations

import hashlib
import random
import re
import uuid
from .helpers import _now_ts, _safe_float, _safe_int, _single_line, _today_key
from .persona_config import runtime_persona_setting
from .proactive_engine_shared import _engine_proactive_window_timezone
from datetime import datetime, timedelta
from typing import Any

from .logging_util import get_module_logger
from .proactive_engine_shared import _engine_host

logger = get_module_logger(__name__)



class ProactiveEngineCandidateMixin:
    """候选池/冲动池域（从 ProactiveEngineMixin 拆出）。"""


    def _proactive_candidate_pool(self) -> list[dict[str, Any]]:
        raw = self.data.setdefault("proactive_candidate_pool", [])
        if not isinstance(raw, list):
            raw = []
            self.data["proactive_candidate_pool"] = raw
        return raw

    def _pending_proactive_candidate_limit(self, user: dict[str, Any] | None = None) -> int:
        if not isinstance(user, dict):
            return 200
        override = _safe_int(user.get("pending_proactive_candidate_limit"), -1, -1)
        return override if override > 0 else 200

    def _candidate_user_id(self, item: dict[str, Any]) -> str:
        if not isinstance(item, dict):
            return ""
        return _single_line(item.get("user_id") or item.get("target_user_id") or item.get("id"), 40)

    @staticmethod
    def _pending_candidate_status(status: str) -> bool:
        normalized = _single_line(status, 24).lower()
        return normalized in {"accepted", "deferred", "queued", "pending", "unknown", ""}

    @staticmethod
    def _candidate_repeat_count_limit(status: str = "") -> int:
        normalized = _single_line(status, 24).lower()
        if normalized in {"accepted", "deferred", "queued", "pending", "unknown", ""}:
            return 12
        if normalized == "sent":
            return 8
        return 6

    def _normalize_candidate_repeat_count(self, item: dict[str, Any]) -> int:
        if not isinstance(item, dict):
            return 1
        limit = self._candidate_repeat_count_limit(str(item.get("status") or ""))
        count = _safe_int(item.get("repeat_count"), 1, 1)
        normalized = max(1, min(limit, count))
        if count != normalized:
            item["repeat_count"] = normalized
            item["repeat_count_capped"] = True
        return normalized

    def _planned_candidate_ids_by_user(self) -> dict[str, str]:
        users = self.data.get("users") if isinstance(self.data.get("users"), dict) else {}
        planned: dict[str, str] = {}
        for user_id, user in users.items():
            if not isinstance(user, dict):
                continue
            candidate_id = _single_line(user.get("planned_candidate_id"), 40)
            if candidate_id:
                planned[str(user_id)] = candidate_id
        return planned

    def _trim_proactive_candidate_total(self, items: list[dict[str, Any]], *, limit: int = 600) -> list[dict[str, Any]]:
        if len(items) <= limit:
            return items
        planned_ids = set(self._planned_candidate_ids_by_user().values())
        protected = [
            item for item in items
            if _single_line(item.get("id"), 40) in planned_ids
        ]
        protected_ids = {_single_line(item.get("id"), 40) for item in protected}
        remaining = [
            item for item in items
            if _single_line(item.get("id"), 40) not in protected_ids
        ]
        keep_count = max(0, limit - len(protected))
        trimmed = remaining[-keep_count:] if keep_count else []
        result = protected + trimmed
        result.sort(
            key=lambda item: max(
                _safe_float(item.get("updated_ts"), 0),
                _safe_float(item.get("created_ts"), 0),
                _safe_float(item.get("scheduled_ts"), 0),
                _safe_float(item.get("last_seen_ts"), 0),
            )
        )
        return result[-limit:]

    def _candidate_trim_priority(self, item: dict[str, Any], *, planned_candidate_id: str = "") -> tuple[int, int, int, float]:
        status = _single_line(item.get("status"), 24).lower()
        note = _single_line(item.get("note"), 160)
        item_id = _single_line(item.get("id"), 40)
        updated = _safe_float(item.get("updated_ts"), 0)
        created = _safe_float(item.get("created_ts"), 0)
        scheduled = _safe_float(item.get("scheduled_ts"), 0)
        last_seen = _safe_float(item.get("last_seen_ts"), 0)
        repeat_count = _safe_int(item.get("repeat_count"), 1, 1)
        protected = item_id and planned_candidate_id and item_id == planned_candidate_id
        status_rank = {
            "failed": 0,
            "cancelled": 1,
            "dropped": 2,
            "blocked": 3,
            "deferred": 4,
            "accepted": 6,
        }.get(status, 5)
        note_penalty = 0 if note else 1
        freshness = max(updated, scheduled, last_seen, created)
        return (1 if protected else 0, status_rank, repeat_count + note_penalty, freshness)

    def _apply_per_user_pending_candidate_cap(
        self,
        items: list[dict[str, Any]],
        *,
        pending_cap: int | None = None,
        target_user_id: str = "",
    ) -> tuple[list[dict[str, Any]], int]:
        users = self.data.get("users") if isinstance(self.data.get("users"), dict) else {}
        planned_ids = self._planned_candidate_ids_by_user()
        grouped: dict[str, list[dict[str, Any]]] = {}
        passthrough: list[dict[str, Any]] = []
        removed = 0
        target = str(target_user_id or "").strip()
        for item in items:
            if not isinstance(item, dict):
                continue
            user_id = self._candidate_user_id(item)
            if not user_id:
                passthrough.append(item)
                continue
            if target and user_id != target:
                passthrough.append(item)
                continue
            grouped.setdefault(user_id, []).append(item)
        kept: list[dict[str, Any]] = list(passthrough)
        for user_id, user_items in grouped.items():
            user = users.get(user_id) if isinstance(users, dict) else None
            limit = pending_cap if pending_cap is not None else self._pending_proactive_candidate_limit(user if isinstance(user, dict) else None)
            if limit <= 0:
                kept.extend(user_items)
                continue
            pending_items = [item for item in user_items if self._pending_candidate_status(str(item.get("status") or ""))]
            sent_items = [item for item in user_items if not self._pending_candidate_status(str(item.get("status") or ""))]
            if len(pending_items) > limit:
                planned_candidate_id = planned_ids.get(user_id, "")
                pending_items.sort(
                    key=lambda item: self._candidate_trim_priority(item, planned_candidate_id=planned_candidate_id),
                    reverse=True,
                )
                trimmed_pending = pending_items[:limit]
                removed += max(0, len(pending_items) - len(trimmed_pending))
                pending_items = sorted(
                    trimmed_pending,
                    key=lambda item: max(
                        _safe_float(item.get("updated_ts"), 0),
                        _safe_float(item.get("created_ts"), 0),
                        _safe_float(item.get("scheduled_ts"), 0),
                    ),
                )
            kept.extend(sent_items)
            kept.extend(pending_items)
        kept.sort(
            key=lambda item: max(
                _safe_float(item.get("updated_ts"), 0),
                _safe_float(item.get("created_ts"), 0),
                _safe_float(item.get("scheduled_ts"), 0),
                _safe_float(item.get("last_seen_ts"), 0),
            )
        )
        return kept, removed

    def _shrink_user_proactive_candidates(
        self,
        user_id: str,
        *,
        pending_cap: int | None = None,
        note: str = "",
    ) -> int:
        target_user_id = str(user_id or "").strip()
        if not target_user_id:
            return 0
        current = [item for item in self._proactive_candidate_pool() if isinstance(item, dict)]
        kept, removed = self._apply_per_user_pending_candidate_cap(
            current,
            pending_cap=pending_cap,
            target_user_id=target_user_id,
        )
        if removed > 0:
            self.data["proactive_candidate_pool"] = kept
            logger.info(
                "主动候选自动收缩: user=%s removed=%s cap=%s note=%s",
                target_user_id,
                removed,
                pending_cap or "default",
                _single_line(note, 120),
            )
        return removed

    def _cleanup_proactive_candidate_pool(self, *, now: float | None = None) -> list[dict[str, Any]]:
        now = now or _engine_host._now_ts()
        kept: list[dict[str, Any]] = []
        for item in self._proactive_candidate_pool():
            if not isinstance(item, dict):
                continue
            self._normalize_candidate_repeat_count(item)
            created = _safe_float(item.get("created_ts"), 0)
            scheduled = _safe_float(item.get("scheduled_ts"), 0)
            status = str(item.get("status") or "")
            short_lived = self._proactive_candidate_is_short_lived(item)
            ttl = (
                6 * 3600
                if short_lived and status in {"accepted", "sent"}
                else 3 * 3600
                if short_lived
                else 36 * 3600
                if status in {"accepted", "sent"}
                else 18 * 3600
            )
            expire_at = _safe_float(item.get("expire_at"), 0)
            if short_lived and expire_at > 0 and now > expire_at + 2 * 3600:
                continue
            anchor = max(created, scheduled)
            if anchor > 0 and now - anchor <= ttl:
                kept.append(item)
        kept, _ = self._apply_per_user_pending_candidate_cap(kept)
        self.data["proactive_candidate_pool"] = self._trim_proactive_candidate_total(kept, limit=600)
        return self.data["proactive_candidate_pool"]

    @staticmethod
    def _proactive_candidate_is_short_lived(item: dict[str, Any]) -> bool:
        """Weather and environment transitions must not survive into another day."""
        if not isinstance(item, dict):
            return False
        values = {
            _single_line(item.get("source"), 40).strip().lower(),
            _single_line(item.get("reason"), 40).strip().lower(),
            _single_line(item.get("planned_proactive_source"), 40).strip().lower(),
            _single_line(item.get("planned_proactive_reason"), 40).strip().lower(),
        }
        return bool(values & {"weather_alert", "environment_change"})

    def _proactive_impulse_pool(self, user: dict[str, Any]) -> list[dict[str, Any]]:
        raw = user.get("proactive_impulses")
        if not isinstance(raw, list):
            raw = []
            user["proactive_impulses"] = raw
        return raw

    @staticmethod
    def _scrub_body_monitor_impulse_context(item: dict[str, Any]) -> None:
        if _single_line(item.get("source"), 40) != "body_monitor":
            return
        item.pop("context", None)
        item["context_key"] = ""

    def _cleanup_proactive_impulses(
        self,
        user: dict[str, Any],
        *,
        now: float | None = None,
    ) -> list[dict[str, Any]]:
        check_now = _engine_host._now_ts() if now is None else now
        kept: list[dict[str, Any]] = []
        for item in self._proactive_impulse_pool(user):
            if not isinstance(item, dict):
                continue
            created = _safe_float(item.get("created_ts"), 0)
            updated = _safe_float(item.get("updated_ts"), created)
            state = str(item.get("state") or "queued").strip().lower()
            window_start_at = _safe_float(item.get("window_start_at"), 0)
            preferred_ts = _safe_float(item.get("preferred_ts"), window_start_at)
            best_until_at = _safe_float(item.get("best_until_at"), preferred_ts)
            expire_at = _safe_float(item.get("expire_at"), 0)
            if state in {"sent", "blocked", "cancelled", "dropped"}:
                self._scrub_body_monitor_impulse_context(item)
                if max(created, updated, expire_at) > 0 and check_now - max(created, updated, expire_at) <= 12 * 3600:
                    kept.append(item)
                continue
            if expire_at > 0 and check_now > expire_at:
                item["state"] = "blocked"
                item["last_status"] = "blocked"
                item["last_note"] = "潜在念头窗口已过期"
                item["updated_ts"] = check_now
                self._scrub_body_monitor_impulse_context(item)
                kept.append(item)
                continue
            if not (
                window_start_at > 0
                and window_start_at <= preferred_ts <= best_until_at <= expire_at
            ):
                item["state"] = "blocked"
                item["last_status"] = "blocked"
                item["last_note"] = "潜在念头时间窗口无效"
                item["updated_ts"] = check_now
                self._scrub_body_monitor_impulse_context(item)
                kept.append(item)
                continue
            if expire_at > 0 and check_now - expire_at > 2 * 3600:
                continue
            if created > 0 and check_now - created > 48 * 3600:
                continue
            kept.append(item)
        user["proactive_impulses"] = kept[-16:]
        return user["proactive_impulses"]

    def _proactive_impulse_signature(self, item: dict[str, Any]) -> str:
        route_key = _single_line(item.get("route_dedupe_key"), 160)
        if route_key:
            return route_key
        # motive 是模板化动机文本，不参与主题相似判定，避免不同内容被误判重复。
        return self._proactive_topic_signature(
            item.get("reason"),
            item.get("source"),
            item.get("topic"),
        )

    def _proactive_impulse_default_window_seconds(self, reason: str, *, source: str = "") -> tuple[float, float]:
        route = self._proactive_route_for(reason=reason, source=source)
        return float(route.active_window_seconds), float(route.grace_window_seconds)

    def _event_time_window_bounds(
        self,
        event: dict[str, Any],
        *,
        reason: str,
        source: str = "",
        now: float | None = None,
    ) -> tuple[float, float, float, float]:
        check_now = _engine_host._now_ts() if now is None else now
        preferred_ts = _safe_float(event.get("_scheduled_ts"), 0)
        start_ts = preferred_ts
        end_ts = 0.0
        window = str(event.get("window") or "").strip()
        if window:
            start_minute, end_minute = self._parse_window_minutes(window)
            if start_minute is not None and end_minute is not None:
                when = self._environment_fromtimestamp(check_now)
                date_text = str(event.get("date") or "").strip()
                try:
                    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_text):
                        base_date = datetime.strptime(date_text, "%Y-%m-%d").date()
                    else:
                        base_date = when.date()
                except Exception:
                    base_date = when.date()
                tzinfo = when.tzinfo
                start_dt = datetime.combine(base_date, datetime.min.time(), tzinfo=tzinfo) + timedelta(minutes=start_minute)
                end_dt = datetime.combine(base_date, datetime.min.time(), tzinfo=tzinfo) + timedelta(minutes=end_minute)
                start_ts = start_dt.timestamp()
                end_ts = end_dt.timestamp()
        if preferred_ts <= 0:
            preferred_ts = start_ts if start_ts > 0 else check_now + 60
        if start_ts <= 0:
            start_ts = preferred_ts
        route = self._proactive_route_for(
            reason=reason,
            source=source or event.get("source"),
            semantic_kind=event.get("semantic_kind"),
            kind=event.get("kind"),
        )
        active_span = float(route.active_window_seconds)
        grace_span = float(route.grace_window_seconds)
        if end_ts <= 0:
            end_ts = max(start_ts + 60.0, preferred_ts + active_span)
        expire_at = max(end_ts + grace_span, preferred_ts + 5 * 60.0)
        return start_ts, preferred_ts, end_ts, expire_at

    def _proactive_origin_event_id(self, candidate: dict[str, Any], *, source: str = "") -> str:
        explicit = _single_line(
            candidate.get("origin_event_id")
            or candidate.get("event_id")
            or candidate.get("source_event_id")
            or candidate.get("key")
            or candidate.get("id"),
            80,
        )
        if explicit:
            return explicit
        context = candidate.get("context") if isinstance(candidate.get("context"), dict) else {}
        context_id = _single_line(
            context.get("id")
            or context.get("memo_id")
            or context.get("goal_id")
            or context.get("event_id"),
            80,
        )
        scheduled_ts = _safe_float(
            candidate.get("_scheduled_ts")
            or candidate.get("scheduled_ts")
            or candidate.get("window_start_at")
            or candidate.get("preferred_ts"),
            0,
        )
        window = _single_line(candidate.get("window"), 40)
        date_text = _single_line(candidate.get("date"), 20)
        if not date_text and scheduled_ts > 0:
            try:
                date_text = self._environment_fromtimestamp(scheduled_ts).strftime("%Y-%m-%d")
            except Exception:
                date_text = datetime.fromtimestamp(scheduled_ts).strftime("%Y-%m-%d")
        # 有明确日期/时段的来源事件，其随机落点分钟不是事件身份的一部分。
        # 否则同一饭点、问候或日程事件每次重选随机分钟都会得到新 ID。
        scheduled_anchor = "" if window else str(int(scheduled_ts // 60))
        raw = "|".join(
            (
                _single_line(source or candidate.get("source"), 40),
                _single_line(candidate.get("reason"), 40),
                date_text,
                window,
                scheduled_anchor,
                context_id,
                _single_line(candidate.get("topic"), 80),
            )
        )
        return hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:16]

    def _prepare_proactive_candidate_window(
        self,
        candidate: dict[str, Any],
        *,
        reason: str,
        source: str,
        now: float,
    ) -> tuple[dict[str, Any] | None, str]:
        if not isinstance(candidate, dict):
            return None, "主动来源无效"
        prepared = dict(candidate)
        current_timezone = _engine_proactive_window_timezone(self)
        candidate_timezone = _single_line(candidate.get("window_timezone"), 64)
        time_exempt = source in {"timer", "troubleshooting", "simulation"}
        if (
            candidate_timezone
            and candidate_timezone != current_timezone
            and not time_exempt
        ):
            candidate["lifecycle_status"] = "skipped"
            candidate["lifecycle_updated_at"] = now
            candidate["lifecycle_note"] = "来源事件生成时区已变化"
            return None, "来源事件生成时区已变化"
        prepared["window_timezone"] = candidate_timezone or current_timezone
        candidate.setdefault("window_timezone", prepared["window_timezone"])
        origin_event_id = self._proactive_origin_event_id(candidate, source=source)
        prepared["origin_event_id"] = origin_event_id
        if origin_event_id and not _single_line(candidate.get("origin_event_id"), 80):
            candidate["origin_event_id"] = origin_event_id
        effective_timezone = _single_line(
            getattr(self, "environment_perception_timezone", ""), 64
        ) or _single_line(getattr(self, "environment_perception_timezone_setting", ""), 64)
        stored_timezone = _single_line(
            prepared.get("window_timezone") or candidate.get("window_timezone"), 64
        )
        weather_window = reason == "weather_alert" or source in {
            "weather_alert",
            "environment_change",
        }
        # Window timestamps are derived from local calendar boundaries. When the
        # effective timezone changes, discard only the derived weather window and
        # rebuild it from the stable source event instead of mixing two calendars.
        if weather_window and stored_timezone and effective_timezone and stored_timezone != effective_timezone:
            for key in (
                "window_start_at",
                "preferred_ts",
                "best_until_at",
                "expire_at",
                "scheduled_ts",
                "_scheduled_ts",
            ):
                prepared.pop(key, None)
            prepared["timezone_rebased_from"] = stored_timezone
        if effective_timezone and not time_exempt:
            prepared["window_timezone"] = effective_timezone
        window_start_at = _safe_float(prepared.get("window_start_at"), 0)
        preferred_ts = _safe_float(prepared.get("preferred_ts"), 0)
        best_until_at = _safe_float(prepared.get("best_until_at"), 0)
        expire_at = _safe_float(prepared.get("expire_at"), 0)
        if any(value <= 0 for value in (window_start_at, preferred_ts, best_until_at, expire_at)):
            window_start_at, preferred_ts, best_until_at, expire_at = self._event_time_window_bounds(
                prepared,
                reason=reason,
                source=source,
                now=now,
            )
        midnight_ritual = bool(prepared.get("_midnight_ritual")) and reason in {
            "birthday_celebration",
            "special_day_greeting",
        }
        quiet_hours_exempt = reason == "insomnia_night" or midnight_ritual
        if (
            reason == "morning_greeting"
            and source in {"daily_greeting", "story", "daily_story", "state"}
            and _single_line(prepared.get("window"), 40)
        ):
            current = self._environment_fromtimestamp(now)
            morning_start, morning_end = self._morning_greeting_window()
            day_start = datetime.combine(current.date(), datetime.min.time(), tzinfo=current.tzinfo)
            canonical_start = (day_start + timedelta(minutes=morning_start)).timestamp()
            canonical_end = (day_start + timedelta(minutes=morning_end)).timestamp()
            if best_until_at < canonical_start or window_start_at > canonical_end:
                window_start_at = canonical_start
                preferred_ts = min(max(preferred_ts, canonical_start), canonical_end)
                best_until_at = canonical_end
            else:
                window_start_at = max(window_start_at, canonical_start)
                preferred_ts = min(max(preferred_ts, window_start_at), canonical_end)
                best_until_at = min(max(best_until_at, preferred_ts), canonical_end)
            expire_at = min(
                max(expire_at, best_until_at + 5 * 60),
                canonical_end + 35 * 60,
            )
        if not time_exempt and expire_at <= now:
            candidate["lifecycle_status"] = "expired"
            candidate["expired_at"] = now
            candidate["lifecycle_updated_at"] = now
            candidate["lifecycle_note"] = "来源事件有效窗口已过期"
            return None, "来源事件有效窗口已过期"
        if not (
            window_start_at > 0
            and window_start_at <= preferred_ts <= best_until_at <= expire_at
        ):
            candidate["lifecycle_status"] = "skipped"
            candidate["lifecycle_updated_at"] = now
            candidate["lifecycle_note"] = "来源事件时间窗口无效"
            return None, "来源事件时间窗口无效"

        quiet_end_getter = getattr(self, "_quiet_hours_end_timestamp", None)
        quiet_end = 0.0
        if not time_exempt and not quiet_hours_exempt and callable(quiet_end_getter):
            try:
                quiet_end = _safe_float(quiet_end_getter(max(window_start_at, preferred_ts)), 0.0)
            except Exception:
                quiet_end = 0.0
        if quiet_end > max(window_start_at, preferred_ts):
            target = quiet_end + 2 * 60
            freshness = self._proactive_item_freshness_class(
                action=str(prepared.get("action") or "message"),
                reason=reason,
                source=source,
                semantic_kind=str(prepared.get("semantic_kind") or ""),
            )
            if expire_at <= target and freshness != "durable":
                candidate["lifecycle_status"] = "skipped"
                candidate["expired_at"] = now
                candidate["lifecycle_updated_at"] = now
                candidate["lifecycle_note"] = "免打扰覆盖整个有效窗口"
                return None, "免打扰覆盖整个有效窗口"
            if expire_at <= target:
                shift = target - window_start_at
                window_start_at += shift
                preferred_ts = max(preferred_ts + shift, window_start_at)
                best_until_at = max(best_until_at + shift, preferred_ts + 20 * 60)
                expire_at = max(expire_at + shift, best_until_at + 20 * 60)
            else:
                window_start_at = max(window_start_at, target)
                preferred_ts = max(preferred_ts, target)
                best_until_at = max(best_until_at, min(expire_at, target + 20 * 60))
            prepared["quiet_hours_adjusted"] = True
            prepared["quiet_hours_until"] = quiet_end

        prepared["window_start_at"] = window_start_at
        prepared["preferred_ts"] = preferred_ts
        prepared["best_until_at"] = best_until_at
        prepared["expire_at"] = expire_at
        prepared["scheduled_ts"] = max(
            _safe_float(prepared.get("scheduled_ts") or prepared.get("_scheduled_ts"), window_start_at),
            window_start_at,
        )
        return prepared, ""

    def _build_proactive_impulse(
        self,
        user: dict[str, Any],
        *,
        reason: str,
        action: str,
        motive: str,
        topic: str,
        source: str,
        window_start_at: float,
        preferred_ts: float,
        best_until_at: float,
        expire_at: float,
        window_timezone: str = "",
        chain: list[dict[str, Any]] | None = None,
        trigger_message_id: str = "",
        trigger_umo: str = "",
        trigger_ts: float = 0,
        quota_exempt: bool = False,
        context_key: str = "",
        context: Any = None,
        opener_mode: str = "",
        followup_kind: str = "",
        origin_event_id: str = "",
        conversation_posture: str = "",
    ) -> dict[str, Any]:
        role = self._private_user_role(user)
        impulse_reason = _single_line(reason, 40) or "check_in"
        impulse_action = _single_line(action, 40) or "message"
        impulse_topic = _single_line(topic, 80)
        impulse_motive = self._normalize_internal_motive_text(_single_line(motive, 180))
        posture = _single_line(conversation_posture, 24).lower()
        if posture not in {"closing", "open", "neutral"}:
            posture = ""
        salience = 0.54
        warmth = 0.46
        urgency = 0.38
        if source in {"followup", "timer", "pending_followup"}:
            salience += 0.2
            urgency += 0.12
        elif source in {"story", "event"}:
            salience += 0.12
        elif source == "random":
            warmth += 0.06
        if impulse_reason in {"morning_greeting", "noon_greeting", "evening_greeting", "special_day_greeting"}:
            warmth += 0.1
            urgency += 0.08
        if impulse_reason in {"quiet_care", "important_date_share", "insomnia_night"}:
            warmth += 0.16
        if role == "friend":
            warmth = max(0.18, warmth - 0.08)
            urgency = max(0.16, urgency - 0.04)
        decay_per_hour = 0.06 if source in {"followup", "timer"} else 0.1
        persona_alignment = self._proactive_persona_alignment(
            user,
            reason=impulse_reason,
            action=impulse_action,
            motive=impulse_motive,
            topic=impulse_topic,
            source=source,
        )
        semantics = self._proactive_candidate_semantics(
            user,
            reason=impulse_reason,
            action=impulse_action,
            motive=impulse_motive,
            topic=impulse_topic,
            source=source,
            context=context,
            chain=chain,
            trigger_message_id=trigger_message_id,
            trigger_ts=trigger_ts,
        )
        proactive_kind = self._proactive_message_kind(
            reason=impulse_reason,
            source=source,
            semantic_kind=semantics.get("kind"),
        )
        kind_policy = self._proactive_kind_policy(proactive_kind)
        quota_policy = self._proactive_quota_policy(user)
        return {
            "id": uuid.uuid4().hex[:12],
            "created_ts": _engine_host._now_ts(),
            "updated_ts": _engine_host._now_ts(),
            "state": "queued",
            "source": _single_line(source, 40) or "random",
            "kind": proactive_kind,
            "kind_label": _single_line(kind_policy.get("label"), 40),
            "response_expectation": _single_line(kind_policy.get("response_expectation"), 24),
            "quota_tier": _safe_int(quota_policy.get("tier"), 0, 0, 5),
            "quota_tier_label": _single_line(quota_policy.get("label"), 40),
            "reason": impulse_reason,
            "action": impulse_action,
            "topic": impulse_topic,
            "motive": impulse_motive,
            "conversation_posture": posture,
            "window_start_at": max(0.0, float(window_start_at or preferred_ts or _engine_host._now_ts())),
            "preferred_ts": max(0.0, float(preferred_ts or window_start_at or _engine_host._now_ts())),
            "best_until_at": max(float(best_until_at or preferred_ts or _engine_host._now_ts()), float(window_start_at or 0.0)),
            "expire_at": max(float(expire_at or best_until_at or preferred_ts or _engine_host._now_ts()), float(best_until_at or 0.0)),
            "window_timezone": _single_line(window_timezone, 64)
            or _engine_proactive_window_timezone(self),
            "salience": max(0.0, min(1.0, salience)),
            "warmth": max(0.0, min(1.0, warmth)),
            "urgency": max(0.0, min(1.0, urgency)),
            "decay_per_hour": max(0.01, min(0.5, decay_per_hour)),
            "persona_fit": max(0.0, min(1.0, _safe_float(persona_alignment.get("score"), 0.5))),
            "persona_fit_note": _single_line(persona_alignment.get("note"), 160),
            "persona_fit_blocker": bool(persona_alignment.get("blocker")),
            "semantic_kind": _single_line(semantics.get("kind"), 40),
            "semantic_anchor_type": _single_line(semantics.get("anchor_type"), 40),
            "semantic_score": max(0.0, min(1.0, _safe_float(semantics.get("score"), 0.5))),
            "semantic_anchor_score": max(0.0, min(1.0, _safe_float(semantics.get("anchor_score"), 0.5))),
            "semantic_pressure": max(0.0, min(1.0, _safe_float(semantics.get("pressure"), 0.4))),
            "semantic_risk": max(0.0, min(1.0, _safe_float(semantics.get("risk"), 0.0))),
            "semantic_note": _single_line(semantics.get("note"), 180),
            "semantic_need_layer": _single_line(semantics.get("need_layer"), 40),
            "semantic_need_drive": _single_line(semantics.get("need_drive"), 80),
            "semantic_need_note": _single_line(semantics.get("need_note"), 120),
            "semantic_need_score_bias": _safe_float(semantics.get("need_score_bias"), 0.0),
            "semantic_need_pressure_bias": _safe_float(semantics.get("need_pressure_bias"), 0.0),
            "semantic_blocker": bool(semantics.get("blocker")),
            "signature": self._proactive_topic_signature(impulse_reason, source, impulse_topic, impulse_motive),
            "chain": [] if role == "friend" else [dict(item) for item in (chain or []) if isinstance(item, dict)],
            "trigger_message_id": _single_line(trigger_message_id, 120),
            "trigger_umo": _single_line(trigger_umo, 160),
            "trigger_ts": _safe_float(trigger_ts, 0),
            "quota_exempt": bool(quota_exempt),
            "context_key": _single_line(context_key, 60),
            "context": dict(context) if isinstance(context, dict) else context,
            "opener_mode": _single_line(opener_mode, 24),
            "followup_kind": _single_line(followup_kind, 32),
            "origin_event_id": _single_line(origin_event_id, 80),
        }

    def _proactive_impulse_orchestration_priority(self, impulse: dict[str, Any]) -> int:
        source = _single_line(impulse.get("source"), 40).lower()
        reason = self._normalize_legacy_proactive_text(impulse.get("reason"), limit=40)
        priorities = {
            "timer": 100,
            "weather_alert": 98,
            "body_monitor": 96,
            "memo_note": 94,
            "environment_change": 90,
            "pending_followup": 92,
            "followup": 88,
            "mobile_location": 84,
            "birthday_celebration": 86,
            "special_day_ritual": 89,
            "night_care": 87,
            "daily_greeting": 72,
            "meal_care": 78,
            "balance": 82,
            "birthday_curiosity": 68,
            "habit": 64,
            "state": 60,
            "story": 58,
            "event": 56,
            "creative": 54,
            "random": 20,
        }
        priority = priorities.get(source, 48)
        if reason in {
            "birthday_celebration",
            "birthday_eve_hint",
            "birthday_makeup",
            "important_date_share",
            "special_day_greeting",
            "insomnia_night",
        }:
            priority = max(priority, 86)
        elif reason == "morning_greeting":
            priority = max(priority, 82)
        elif reason in {"noon_greeting", "evening_greeting"}:
            priority = max(priority, 72)
        elif reason == "quiet_care":
            priority = max(priority, 74)
        # 外部分享类（content_share 路线）优先级可配：默认 48 与硬编码一致，
        # 调高后（如 72~78 与饭点关心同档）这类内容在活跃时段更容易被选中发出，
        # 但仍受免打扰/冷却/日上限等闸门约束。
        if reason in {
            "news_share",
            "bili_video_share",
            "web_exploration_share",
            "creative_share",
            "group_share",
            "reading_archive_recommendation_request",
            "game_invite",
        }:
            priority = _safe_int(
                runtime_persona_setting(self, "proactive_share_priority", 48),
                48,
                0,
                100,
            )
        return priority

    def _proactive_impulse_content_signature(self, impulse: dict[str, Any]) -> str:
        # 只按内容（topic）比对；motive 是模板化动机文本，计入会把不同内容误判为相似而合并。
        return self._proactive_topic_signature(
            impulse.get("topic"),
        )

    def _merge_proactive_impulse_timing(self, target: dict[str, Any], incoming: dict[str, Any]) -> None:
        target_start = _safe_float(target.get("window_start_at"), 0)
        incoming_start = _safe_float(incoming.get("window_start_at"), 0)
        target_preferred = _safe_float(target.get("preferred_ts"), 0)
        incoming_preferred = _safe_float(incoming.get("preferred_ts"), 0)
        if target_start <= 0 or (incoming_start > 0 and incoming_start < target_start):
            target["window_start_at"] = incoming_start
        if target_preferred <= 0 or (incoming_preferred > 0 and incoming_preferred < target_preferred):
            target["preferred_ts"] = incoming_preferred
        target["best_until_at"] = max(
            _safe_float(target.get("best_until_at"), 0),
            _safe_float(incoming.get("best_until_at"), 0),
        )
        target["expire_at"] = max(
            _safe_float(target.get("expire_at"), 0),
            _safe_float(incoming.get("expire_at"), 0),
        )

    def _replace_proactive_impulse_with_higher_priority(
        self,
        existing: dict[str, Any],
        incoming: dict[str, Any],
    ) -> dict[str, Any]:
        existing_id = _single_line(existing.get("id"), 20) or uuid.uuid4().hex[:12]
        existing_created = _safe_float(existing.get("created_ts"), _engine_host._now_ts())
        existing_state = _single_line(existing.get("state"), 24) or "queued"
        replacement = dict(incoming)
        replacement["id"] = existing_id
        replacement["created_ts"] = existing_created
        replacement["updated_ts"] = _engine_host._now_ts()
        replacement["state"] = existing_state
        self._merge_proactive_impulse_timing(replacement, existing)
        replacement["signature"] = self._proactive_impulse_signature(replacement)
        replacement["salience"] = max(
            _safe_float(existing.get("salience"), 0.0),
            _safe_float(incoming.get("salience"), 0.0),
        )
        replacement["urgency"] = max(
            _safe_float(existing.get("urgency"), 0.0),
            _safe_float(incoming.get("urgency"), 0.0),
        )
        existing.clear()
        existing.update(replacement)
        return existing

    def _queue_proactive_impulse(
        self,
        user: dict[str, Any],
        impulse: dict[str, Any],
    ) -> dict[str, Any]:
        disabled = getattr(self, "_proactive_generation_disabled", None)
        if callable(disabled) and disabled(user):
            return {}
        check_now = _engine_host._now_ts()
        prepared, invalid_reason = self._prepare_proactive_candidate_window(
            impulse,
            reason=_single_line(impulse.get("reason"), 40) or "check_in",
            source=_single_line(impulse.get("source"), 40) or "random",
            now=check_now,
        )
        if not isinstance(prepared, dict):
            impulse["state"] = "blocked"
            impulse["last_status"] = "blocked"
            impulse["last_note"] = invalid_reason
            impulse["updated_ts"] = check_now
            return {}
        impulse = prepared
        pool = self._cleanup_proactive_impulses(user)
        origin_event_id = _single_line(impulse.get("origin_event_id"), 80)
        if origin_event_id:
            for existing in reversed(pool):
                if _single_line(existing.get("origin_event_id"), 80) != origin_event_id:
                    continue
                existing_state = str(existing.get("state") or "queued")
                # 位置转场可能在发送前被用户活跃、休息或复核闸门拦下；
                # 只有真实发送成功才消耗这次转场，其他来源沿用原有去重契约。
                terminal_states = {"sent"} if _single_line(impulse.get("source"), 40) == "mobile_location" else {
                    "sent", "blocked", "cancelled", "dropped"
                }
                if existing_state in terminal_states:
                    return {}
                if _single_line(impulse.get("source"), 40) == "mobile_location":
                    for key in ("_mobile_location_transition_key", "_mobile_location_priority", "mobile_location_event_type"):
                        if key in impulse:
                            existing[key] = impulse.get(key)
        signature = self._proactive_impulse_signature(impulse)
        reason = _single_line(impulse.get("reason"), 40)
        source = _single_line(impulse.get("source"), 40)
        for existing in reversed(pool):
            if not isinstance(existing, dict):
                continue
            if str(existing.get("state") or "queued") not in {"queued", "deferred"}:
                continue
            if str(existing.get("reason") or "") != reason:
                continue
            if str(existing.get("source") or "") != source:
                continue
            if not self._topic_signature_similar(signature, str(existing.get("signature") or "")):
                continue
            existing_start = _safe_float(existing.get("window_start_at"), 0)
            incoming_start = _safe_float(impulse.get("window_start_at"), 0)
            existing_preferred = _safe_float(existing.get("preferred_ts"), 0)
            incoming_preferred = _safe_float(impulse.get("preferred_ts"), 0)
            closing_deferred = bool(
                existing.get("conversation_closing_deferred") or impulse.get("conversation_closing_deferred")
            )
            existing["updated_ts"] = _engine_host._now_ts()
            if existing_start <= 0:
                existing["window_start_at"] = incoming_start
            elif incoming_start > 0:
                existing["window_start_at"] = (
                    max(existing_start, incoming_start)
                    if closing_deferred
                    else min(existing_start, incoming_start)
                )
            if existing_preferred <= 0:
                existing["preferred_ts"] = incoming_preferred
            elif incoming_preferred > 0:
                existing["preferred_ts"] = (
                    max(existing_preferred, incoming_preferred)
                    if closing_deferred
                    else min(existing_preferred, incoming_preferred)
                )
            if closing_deferred:
                existing["conversation_closing_deferred"] = True
                existing["conversation_closing_deferred_until"] = max(
                    _safe_float(existing.get("conversation_closing_deferred_until"), 0),
                    _safe_float(impulse.get("conversation_closing_deferred_until"), 0),
                )
            existing["best_until_at"] = max(
                _safe_float(existing.get("best_until_at"), 0),
                _safe_float(impulse.get("best_until_at"), 0),
            )
            existing["expire_at"] = max(
                _safe_float(existing.get("expire_at"), 0),
                _safe_float(impulse.get("expire_at"), 0),
            )
            existing["salience"] = max(_safe_float(existing.get("salience"), 0.0), _safe_float(impulse.get("salience"), 0.0))
            existing["warmth"] = max(_safe_float(existing.get("warmth"), 0.0), _safe_float(impulse.get("warmth"), 0.0))
            existing["urgency"] = max(_safe_float(existing.get("urgency"), 0.0), _safe_float(impulse.get("urgency"), 0.0))
            existing_fit = _safe_float(existing.get("persona_fit"), 0.0)
            incoming_fit = _safe_float(impulse.get("persona_fit"), 0.0)
            if incoming_fit > 0:
                existing["persona_fit"] = max(existing_fit, incoming_fit)
            if incoming_fit >= existing_fit:
                existing["persona_fit_blocker"] = bool(impulse.get("persona_fit_blocker"))
            elif impulse.get("persona_fit_blocker"):
                existing["persona_fit_blocker"] = True
            if _single_line(impulse.get("persona_fit_note"), 160):
                existing["persona_fit_note"] = _single_line(impulse.get("persona_fit_note"), 160)
            existing_semantic = _safe_float(existing.get("semantic_score"), 0.0)
            incoming_semantic = _safe_float(impulse.get("semantic_score"), 0.0)
            if incoming_semantic >= existing_semantic:
                for key in (
                    "semantic_kind",
                    "semantic_anchor_type",
                    "semantic_score",
                    "semantic_anchor_score",
                    "semantic_pressure",
                    "semantic_risk",
                    "semantic_note",
                    "semantic_need_layer",
                    "semantic_need_drive",
                    "semantic_need_note",
                    "semantic_need_score_bias",
                    "semantic_need_pressure_bias",
                    "semantic_blocker",
                ):
                    if key in impulse:
                        existing[key] = impulse.get(key)
            elif impulse.get("semantic_blocker"):
                existing["semantic_blocker"] = True
                existing["semantic_risk"] = max(_safe_float(existing.get("semantic_risk"), 0.0), _safe_float(impulse.get("semantic_risk"), 0.0))
            if _single_line(impulse.get("topic"), 80):
                existing["topic"] = _single_line(impulse.get("topic"), 80)
            if _single_line(impulse.get("motive"), 180):
                existing["motive"] = self._normalize_internal_motive_text(_single_line(impulse.get("motive"), 180))
            if impulse.get("chain"):
                existing["chain"] = [dict(item) for item in impulse.get("chain", []) if isinstance(item, dict)]
            if _single_line(impulse.get("trigger_message_id"), 120):
                existing["trigger_message_id"] = _single_line(impulse.get("trigger_message_id"), 120)
            if _single_line(impulse.get("trigger_umo"), 160):
                existing["trigger_umo"] = _single_line(impulse.get("trigger_umo"), 160)
            if _safe_float(impulse.get("trigger_ts"), 0) > 0:
                existing["trigger_ts"] = _safe_float(impulse.get("trigger_ts"), 0)
            if impulse.get("quota_exempt"):
                existing["quota_exempt"] = True
            if _single_line(impulse.get("context_key"), 60) and isinstance(impulse.get("context"), dict):
                existing["context_key"] = _single_line(impulse.get("context_key"), 60)
                existing["context"] = dict(impulse.get("context"))
            return existing
        content_signature = self._proactive_impulse_content_signature(impulse)
        if content_signature:
            incoming_priority = self._proactive_impulse_orchestration_priority(impulse)
            for existing in reversed(pool):
                if not isinstance(existing, dict):
                    continue
                if str(existing.get("state") or "queued") not in {"queued", "deferred"}:
                    continue
                existing_signature = self._proactive_impulse_content_signature(existing)
                if not self._topic_signature_similar(content_signature, existing_signature):
                    continue
                existing_priority = self._proactive_impulse_orchestration_priority(existing)
                if incoming_priority > existing_priority:
                    return self._replace_proactive_impulse_with_higher_priority(existing, impulse)
                if incoming_priority == existing_priority:
                    self._merge_proactive_impulse_timing(existing, impulse)
                existing["updated_ts"] = _engine_host._now_ts()
                existing["salience"] = max(
                    _safe_float(existing.get("salience"), 0.0),
                    _safe_float(impulse.get("salience"), 0.0),
                )
                existing["urgency"] = max(
                    _safe_float(existing.get("urgency"), 0.0),
                    _safe_float(impulse.get("urgency"), 0.0),
                )
                return existing
        item = dict(impulse)
        item["id"] = _single_line(item.get("id"), 20) or uuid.uuid4().hex[:12]
        item["signature"] = signature
        item["state"] = str(item.get("state") or "queued")
        pool.append(item)
        del pool[:-16]
        return item

    def _proactive_conversation_closing_until(
        self,
        user: dict[str, Any],
        *,
        now: float | None = None,
    ) -> float:
        """Return the short-lived conversation closing boundary, if still valid.

        This is deliberately separate from the user's rest gate.  A bot-initiated
        closing is a conversational posture with a TTL; a later user activity
        supersedes it without requiring text matching.
        """
        if not isinstance(user, dict):
            return 0.0
        continuity = user.get("state_continuity")
        marker = continuity.get("conversation_closing") if isinstance(continuity, dict) else None
        if not isinstance(marker, dict):
            departure = (
                continuity.get("conversation_departure")
                if isinstance(continuity, dict) and isinstance(continuity.get("conversation_departure"), dict)
                else user.get("conversation_departure")
            )
            if isinstance(departure, dict):
                marker = {
                    "at": departure.get("at"),
                    "posture": "closing",
                    "source": "confirmed_visible_reply",
                }
        if not isinstance(marker, dict):
            return 0.0
        posture = _single_line(marker.get("posture"), 24).lower()
        if posture and posture != "closing":
            return 0.0
        check_now = _engine_host._now_ts() if now is None else now
        at = _safe_float(marker.get("at"), 0.0)
        if at <= 0:
            return 0.0
        latest_activity = 0.0
        private_activity_getter = getattr(self, "_latest_private_user_activity_ts", None)
        if callable(private_activity_getter):
            try:
                latest_activity = _safe_float(private_activity_getter(user), 0.0)
            except Exception:
                latest_activity = 0.0
        if latest_activity <= 0:
            latest_activity = max(
                _safe_float(user.get("last_private_activity_at"), 0.0),
                _safe_float(user.get("last_user_message_at"), 0.0),
            )
        if latest_activity > at + 0.001:
            self._release_proactive_closing_deferred_plan(user, now=check_now)
            return 0.0
        until = _safe_float(marker.get("until"), 0.0)
        if until <= at:
            grace_minutes = _safe_float(
                runtime_persona_setting(self, "proactive_closing_grace_minutes", 45),
                45.0,
            )
            until = at + max(0.0, min(240.0, grace_minutes)) * 60.0
        return until if until > check_now else 0.0

    def _release_proactive_closing_deferred_plan(
        self,
        user: dict[str, Any],
        *,
        now: float,
    ) -> None:
        """Make a deferred candidate re-evaluable after fresh private activity."""
        if not isinstance(user, dict):
            return
        if bool(user.get("planned_proactive_conversation_closing_deferred")):
            next_at = _safe_float(user.get("next_proactive_at"), 0.0)
            if next_at > now:
                user["next_proactive_at"] = now
            window_start = _safe_float(user.get("planned_proactive_window_start_at"), 0.0)
            if window_start > now:
                user["planned_proactive_window_start_at"] = now
            user["planned_proactive_conversation_closing_deferred"] = False
        impulses = user.get("proactive_impulses")
        if not isinstance(impulses, list):
            return
        for impulse in impulses:
            if not isinstance(impulse, dict) or not impulse.get("conversation_closing_deferred"):
                continue
            for key in ("window_start_at", "preferred_ts"):
                value = _safe_float(impulse.get(key), 0.0)
                if value > now:
                    impulse[key] = now
            impulse["conversation_closing_deferred"] = False
            impulse["conversation_closing_released_at"] = now
            impulse["updated_ts"] = now

    def _defer_candidate_after_conversation_closing(
        self,
        user: dict[str, Any],
        candidate: dict[str, Any],
        *,
        now: float,
        timeliness: str = "routine",
    ) -> dict[str, Any]:
        """Softly move ordinary candidates past a recent bot closing.

        The candidate remains inspectable and keeps its source/trigger.  Explicit
        triggers and time-sensitive routes are allowed through; only a routine
        untriggered candidate is shifted.
        """
        if not isinstance(candidate, dict):
            return candidate
        closing_until = self._proactive_conversation_closing_until(user, now=now)
        if closing_until <= now:
            return candidate
        posture = _single_line(candidate.get("conversation_posture"), 24).lower()
        source = _single_line(candidate.get("source"), 40).lower()
        has_trigger = bool(self._candidate_trigger_message_id(candidate))
        if posture == "closing" or has_trigger or timeliness in {"urgent", "timely"}:
            return candidate
        if source in {"timer", "pending_followup", "followup", "troubleshooting", "simulation"}:
            return candidate
        scheduled = _safe_float(candidate.get("scheduled_ts"), now)
        if scheduled >= closing_until:
            return candidate
        shift = closing_until - scheduled
        shifted = dict(candidate)
        for key in ("scheduled_ts", "window_start_at", "preferred_ts", "best_until_at", "expire_at"):
            value = _safe_float(shifted.get(key), 0.0)
            if value > 0:
                shifted[key] = value + shift
        shifted["conversation_closing_deferred"] = True
        shifted["conversation_closing_deferred_until"] = closing_until
        return shifted

    def _candidate_to_impulse(
        self,
        user: dict[str, Any],
        candidate: dict[str, Any],
        *,
        source: str,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        if not isinstance(candidate, dict):
            return None
        disabled = getattr(self, "_proactive_generation_disabled", None)
        if callable(disabled) and disabled(user):
            return None
        check_now = _engine_host._now_ts() if now is None else now
        candidate = self._prepare_proactive_route_candidate(
            user,
            candidate,
            source=source,
            now=check_now,
        )
        reason = _single_line(candidate.get("reason"), 40) or "check_in"
        action = _single_line(candidate.get("action"), 40) or "message"
        motive = _single_line(candidate.get("motive"), 180)
        topic = _single_line(candidate.get("topic"), 80)
        prepared, _invalid_reason = self._prepare_proactive_candidate_window(
            candidate,
            reason=reason,
            source=source,
            now=check_now,
        )
        if not isinstance(prepared, dict):
            return None
        window_start_at = _safe_float(prepared.get("window_start_at"), 0)
        preferred_ts = _safe_float(prepared.get("preferred_ts"), 0)
        best_until_at = _safe_float(prepared.get("best_until_at"), 0)
        expire_at = _safe_float(prepared.get("expire_at"), 0)
        impulse = self._build_proactive_impulse(
            user,
            reason=reason,
            action=action,
            motive=motive,
            topic=topic,
            source=source,
            window_start_at=window_start_at,
            preferred_ts=preferred_ts,
            best_until_at=best_until_at,
            expire_at=expire_at,
            window_timezone=_single_line(prepared.get("window_timezone"), 64),
            chain=prepared.get("chain") if isinstance(prepared.get("chain"), list) else [],
            trigger_message_id=self._candidate_trigger_message_id(prepared),
            trigger_umo=_single_line(prepared.get("trigger_umo") or prepared.get("umo"), 160),
            trigger_ts=_safe_float(prepared.get("trigger_ts") or prepared.get("created_ts"), 0),
            quota_exempt=bool(prepared.get("_free_screen_peek")),
            context_key=_single_line(prepared.get("context_key"), 60),
            context=prepared.get("context"),
            opener_mode="name_only" if candidate.get("_name_only_opener") else "",
            followup_kind=(
                "suspended_opener"
                if candidate.get("_opener_followup")
                else "chain_followup"
                if candidate.get("_chain_followup")
                else ""
            ),
            origin_event_id=_single_line(prepared.get("origin_event_id"), 80),
            conversation_posture=_single_line(prepared.get("conversation_posture"), 24),
        )
        for key in ("_mobile_location_transition_key", "_mobile_location_priority", "mobile_location_event_type"):
            if key in prepared:
                impulse[key] = prepared.get(key)
        for key in (
            "kind",
            "kind_label",
            "route_version",
            "route_dedupe_key",
            "route_review_profile",
            "route_retry_profile",
            "route_cancel_if_new_inbound",
            "route_recent_chat_policy",
            "route_allow_automatic_followup",
            "route_disable_segmenting",
            "response_expectation",
            "quota_tier",
        ):
            if key in prepared:
                impulse[key] = prepared[key]
        impulse["conversation_closing_deferred"] = bool(prepared.get("conversation_closing_deferred"))
        if prepared.get("conversation_closing_deferred_until"):
            impulse["conversation_closing_deferred_until"] = _safe_float(
                prepared.get("conversation_closing_deferred_until"),
            )
        return impulse

    def _impulse_ready_now(self, impulse: dict[str, Any], *, now: float | None = None) -> bool:
        check_now = _engine_host._now_ts() if now is None else now
        return (
            str(impulse.get("state") or "queued") in {"queued", "deferred"}
            and check_now >= _safe_float(impulse.get("window_start_at"), 0)
            and check_now <= _safe_float(impulse.get("expire_at"), 0)
        )

    def _score_proactive_impulse(
        self,
        user: dict[str, Any],
        impulse: dict[str, Any],
        *,
        now: float | None = None,
    ) -> float:
        check_now = _engine_host._now_ts() if now is None else now
        proactive_kind = _single_line(impulse.get("kind"), 40) or self._proactive_message_kind(
            reason=impulse.get("reason"),
            source=impulse.get("source"),
            semantic_kind=impulse.get("semantic_kind"),
        )
        impulse["kind"] = proactive_kind
        kind_policy = self._proactive_kind_policy(proactive_kind)
        quota_policy = self._proactive_quota_policy(user)
        created = _safe_float(impulse.get("created_ts"), check_now)
        preferred_ts = _safe_float(impulse.get("preferred_ts"), _safe_float(impulse.get("window_start_at"), check_now))
        best_until_at = _safe_float(impulse.get("best_until_at"), preferred_ts)
        age_hours = max(0.0, check_now - created) / 3600.0
        score = (
            _safe_float(impulse.get("salience"), 0.5)
            + _safe_float(impulse.get("urgency"), 0.3) * 0.9
            + _safe_float(impulse.get("warmth"), 0.3) * 0.7
        )
        score += _safe_float(kind_policy.get("score_bias"), 0.0)
        score += _safe_float(quota_policy.get("candidate_score_bias"), 0.0)
        source_feedback = self._proactive_source_feedback_modifier(
            user,
            _single_line(impulse.get("source"), 40),
        )
        score += source_feedback
        quota_tier = _safe_int(quota_policy.get("tier"), 0, 0, 5)
        if quota_tier >= 4 and proactive_kind in {"self_life", "content_share"}:
            score += 0.05
        score -= age_hours * _safe_float(impulse.get("decay_per_hour"), 0.08)
        if preferred_ts > 0:
            score -= min(0.55, abs(check_now - preferred_ts) / 3600.0 * 0.14)
        if best_until_at > 0 and check_now > best_until_at:
            score -= min(0.7, (check_now - best_until_at) / 3600.0 * 0.25)
        if str(impulse.get("source") or "") in {"pending_followup", "followup"} or str(impulse.get("reason") or "") == "quiet_care":
            score += 0.05
        if (
            str(impulse.get("reason") or "") == "morning_greeting"
            and preferred_ts > 0
            and check_now <= max(preferred_ts, best_until_at)
        ):
            score += 0.10
        persona_fit = _safe_float(impulse.get("persona_fit"), -1.0)
        if persona_fit < 0:
            persona_alignment = self._proactive_persona_alignment(
                user,
                reason=_single_line(impulse.get("reason"), 40),
                action=_single_line(impulse.get("action"), 40) or "message",
                motive=_single_line(impulse.get("motive"), 180),
                topic=_single_line(impulse.get("topic"), 80),
                source=_single_line(impulse.get("source"), 40),
                now=check_now,
            )
            persona_fit = _safe_float(persona_alignment.get("score"), 0.55)
            impulse["persona_fit"] = persona_fit
            impulse["persona_fit_note"] = _single_line(persona_alignment.get("note"), 160)
            impulse["persona_fit_blocker"] = bool(persona_alignment.get("blocker"))
        score += (persona_fit - 0.6) * 0.36
        if impulse.get("persona_fit_blocker"):
            score -= 0.45
        semantic_score = _safe_float(impulse.get("semantic_score"), -1.0)
        if semantic_score < 0:
            semantics = self._proactive_candidate_semantics(
                user,
                reason=_single_line(impulse.get("reason"), 40),
                action=_single_line(impulse.get("action"), 60) or "message",
                motive=_single_line(impulse.get("motive"), 180),
                topic=_single_line(impulse.get("topic"), 100),
                source=_single_line(impulse.get("source"), 40),
                context=impulse.get("context"),
                chain=impulse.get("chain") if isinstance(impulse.get("chain"), list) else [],
                trigger_message_id=_single_line(impulse.get("trigger_message_id"), 120),
                trigger_ts=_safe_float(impulse.get("trigger_ts"), 0),
            )
            semantic_score = _safe_float(semantics.get("score"), 0.5)
            impulse["semantic_kind"] = _single_line(semantics.get("kind"), 40)
            impulse["semantic_anchor_type"] = _single_line(semantics.get("anchor_type"), 40)
            impulse["semantic_score"] = semantic_score
            impulse["semantic_anchor_score"] = _safe_float(semantics.get("anchor_score"), 0.5)
            impulse["semantic_pressure"] = _safe_float(semantics.get("pressure"), 0.4)
            impulse["semantic_risk"] = _safe_float(semantics.get("risk"), 0.0)
            impulse["semantic_note"] = _single_line(semantics.get("note"), 180)
            impulse["semantic_need_layer"] = _single_line(semantics.get("need_layer"), 40)
            impulse["semantic_need_drive"] = _single_line(semantics.get("need_drive"), 80)
            impulse["semantic_need_note"] = _single_line(semantics.get("need_note"), 120)
            impulse["semantic_need_score_bias"] = _safe_float(semantics.get("need_score_bias"), 0.0)
            impulse["semantic_need_pressure_bias"] = _safe_float(semantics.get("need_pressure_bias"), 0.0)
            impulse["semantic_blocker"] = bool(semantics.get("blocker"))
        score += (semantic_score - 0.5) * 0.42
        score -= max(0.0, _safe_float(impulse.get("semantic_pressure"), 0.4) - 0.55) * 0.22
        score -= _safe_float(impulse.get("semantic_risk"), 0.0) * 0.42
        if impulse.get("semantic_blocker"):
            score -= 0.5
        readiness = self._proactive_inner_readiness(user, now=check_now)
        temperature = readiness.get("temperature") if isinstance(readiness.get("temperature"), dict) else {}
        score += (_safe_float(readiness.get("score"), 0.55) - 0.55) * 0.38
        score += (_safe_float(temperature.get("score"), 0.55) - 0.55) * 0.22
        hesitation_count = _safe_int(impulse.get("hesitation_count"), 0, 0, 8)
        if hesitation_count > 0:
            score += min(0.12, hesitation_count * 0.035)
        if _safe_int(user.get("ignored_streak"), 0, 0) >= 2:
            unanswered_penalty = _safe_float(kind_policy.get("unanswered_score_penalty"), 0.08, 0.0)
            if quota_tier >= 4 and proactive_kind in {"self_life", "content_share"}:
                unanswered_penalty = 0.0
            score -= unanswered_penalty
        return score

    def _proactive_candidate_semantics(
        self,
        user: dict[str, Any],
        *,
        reason: str,
        action: str,
        motive: str,
        topic: str = "",
        source: str = "",
        context: Any = None,
        chain: list[dict[str, Any]] | None = None,
        trigger_message_id: str = "",
        trigger_ts: float = 0,
    ) -> dict[str, Any]:
        normalized_reason = _single_line(reason, 40) or "check_in"
        normalized_action = _single_line(action, 60) or "message"
        normalized_motive = self._normalize_internal_motive_text(_single_line(motive, 180))
        normalized_topic = _single_line(topic, 100)
        normalized_source = _single_line(source, 40)
        context_text = self._proactive_semantic_evidence_text(context)
        chain_text = self._proactive_semantic_chain_text(chain)
        has_context = bool(context_text)
        has_chain = bool(chain_text)
        has_trigger = bool(_single_line(trigger_message_id, 120))
        text = f"{normalized_reason} {normalized_action} {normalized_topic} {normalized_motive}"
        evidence_text = f"{text} {context_text} {chain_text}"
        action_parts = {part.strip() for part in normalized_action.split("+") if part.strip()}
        kind = "check_in"
        if normalized_reason in {"morning_greeting", "noon_greeting", "evening_greeting", "insomnia_night"}:
            kind = "greeting"
        elif normalized_reason in {"meal_care", "meal_care_followup"}:
            kind = "care"
        elif normalized_reason in {
            "quiet_care", "state_share", "post_goodnight_group_activity",
            "memory_echo", "mood_checkin", "absence_miss",
            "anonymous_area_dwell", "anonymous_area_familiarity",
        }:
            kind = "care"
        elif normalized_reason in {"activity_share", "diary_share", "background_schedule", "creative_share", "personal_goal_progress"}:
            kind = "self_share"
        elif normalized_reason in {"important_date_share", "memo_note_reminder", "birthday_eve_hint", "birthday_celebration", "birthday_makeup", "birthday_afterglow"}:
            kind = "reminder"
        elif normalized_reason in {"environment_change", "weather_alert"}:
            kind = "observation"
        elif normalized_reason in {"group_share", "bili_video_share", "news_share", "web_exploration_share", "game_invite"}:
            kind = "external_share"
        elif normalized_source in {"pending_followup", "followup"}:
            kind = "continuation"
        elif action_parts & {"screen_peek"}:
            kind = "observation"
        elif action_parts & {"poke"}:
            kind = "light_touch"

        anchor_type = "vague"
        anchor_score = 0.28
        if normalized_reason == "memory_echo":
            anchor_type, anchor_score = "cross_day_memory", 0.76
        elif normalized_reason in {"mood_checkin", "absence_miss"}:
            anchor_type, anchor_score = "recent_context", 0.76
        elif normalized_source in {"pending_followup", "followup"} or has_trigger or has_chain or "前面提过" in evidence_text:
            anchor_type, anchor_score = "recent_context", 0.78
        elif normalized_reason in {"group_share", "post_goodnight_group_activity"} or "群" in evidence_text:
            anchor_type, anchor_score = "group_context", 0.72
        elif normalized_reason in {"diary_share", "creative_share"} or any(token in evidence_text for token in ("日记", "写到", "作品", "片段")):
            anchor_type, anchor_score = "inner_life", 0.68
        elif normalized_reason in {"meal_care", "meal_care_followup"} or any(token in evidence_text for token in ("早餐", "早饭", "午饭", "午餐", "晚饭", "晚餐", "吃了吗", "吃了没")):
            anchor_type, anchor_score = "meal_time", 0.74
        elif normalized_reason in {"background_schedule"} or any(token in evidence_text for token in ("手上", "忙到", "日程", "计划", "刚好停")):
            anchor_type, anchor_score = "current_activity", 0.62
        elif normalized_reason in {"important_date_share", "birthday_eve_hint", "birthday_celebration", "birthday_makeup", "birthday_afterglow"} or any(token in evidence_text for token in ("生日", "纪念", "日期", "考试", "提醒")):
            anchor_type, anchor_score = "important_date", 0.78
        elif normalized_reason in {"environment_change", "weather_alert"}:
            anchor_type, anchor_score = "environment", 0.82
        elif normalized_reason in {"anonymous_area_dwell", "anonymous_area_familiarity"}:
            anchor_type, anchor_score = "environment", 0.68
        elif normalized_reason in {"news_share", "web_exploration_share", "bili_video_share"}:
            anchor_type, anchor_score = "external_info", 0.66
        elif normalized_reason in {"morning_greeting", "noon_greeting", "evening_greeting", "insomnia_night"}:
            anchor_type, anchor_score = "time_ritual", 0.55
        elif any(token in evidence_text for token in ("天气", "雨", "阳光", "晚霞", "天色", "风", "窗")):
            anchor_type, anchor_score = "environment", 0.58
        elif has_context:
            anchor_type, anchor_score = "topic_hint", 0.56
        elif normalized_topic:
            anchor_type, anchor_score = "topic_hint", 0.48

        pressure = 0.34
        if kind in {"greeting", "self_share", "reminder"}:
            pressure -= 0.04
        if kind in {"check_in", "care", "observation", "light_touch"}:
            pressure += 0.08
        if action_parts & {"screen_peek", "poke", "voice"}:
            pressure += 0.16
        if has_context or has_trigger:
            pressure -= 0.05
        if has_chain and normalized_source in {"pending_followup", "followup", "daily_greeting"}:
            pressure -= 0.03
        if self._is_vague_seek_user_motive(normalized_reason, normalized_action, normalized_motive, normalized_topic):
            pressure += 0.18
        if self._private_user_role(user) == "friend":
            pressure += 0.08
        if _safe_int(user.get("ignored_streak"), 0, 0) > 0:
            pressure += min(0.22, _safe_int(user.get("ignored_streak"), 0, 0) * 0.06)

        risk = 0.0
        blocker = False
        notes: list[str] = []

        def note(value: str) -> None:
            clean = _single_line(value, 60)
            if clean and clean not in notes:
                notes.append(clean)

        if anchor_score >= 0.65:
            note(f"由头明确:{anchor_type}")
        elif anchor_score <= 0.35:
            note("由头偏虚")
        if pressure >= 0.62:
            note("打扰压力偏高")
        if self._unverified_social_relay_plan_reason(
            {
                "reason": normalized_reason,
                "action": normalized_action,
                "topic": normalized_topic,
                "motive": normalized_motive,
                "scene": context_text,
            },
            source=normalized_source,
            has_trigger=has_trigger,
        ):
            risk += 0.35
            blocker = True
            note("疑似无来源转述")
        if any(token in evidence_text for token in ("模型", "插件", "接口", "后台", "提示词", "系统调度", "action")):
            risk += 0.42
            blocker = True
            note("内部机制泄露")
        if self._friend_can_receive_proactive_reason(user, normalized_reason, normalized_action) is False:
            risk += 0.35
            blocker = True
            note("次要用户关系语义越界")
        if self._proactive_text_is_intimate(normalized_reason, normalized_action, normalized_motive, normalized_topic):
            risk += 0.18
            if self._private_user_role(user) == "friend":
                risk += 0.18
                note("次要用户关系亲密过量")
        score = 0.52 + (anchor_score - 0.5) * 0.42 - max(0.0, pressure - 0.45) * 0.36 - risk * 0.5
        if kind in {"continuation", "reminder"}:
            score += 0.08
        if kind == "self_share" and anchor_score >= 0.48:
            score += 0.05
        if has_context and anchor_score >= 0.5:
            score += 0.04
        if kind == "check_in" and anchor_score < 0.45:
            score -= 0.08
        need_profile: dict[str, Any] = {}
        if bool(runtime_persona_setting(self, "enable_maslow_motivation_experiment", False)):
            need_profile = self._maslow_motivation_profile(
                user,
                reason=normalized_reason,
                action=normalized_action,
                motive=normalized_motive,
                topic=normalized_topic,
                source=normalized_source,
                semantic_kind=kind,
                anchor_type=anchor_type,
                anchor_score=anchor_score,
                evidence_text=evidence_text,
            )
            strength = max(
                0.0,
                min(
                    1.0,
                    _safe_float(
                        runtime_persona_setting(self, "maslow_motivation_strength", 35),
                        35,
                        0.0,
                    )
                    / 100.0,
                ),
            )
            score += _safe_float(need_profile.get("score_bias"), 0.0) * strength
            pressure += _safe_float(need_profile.get("pressure_bias"), 0.0) * strength
            need_note = _single_line(need_profile.get("note"), 60)
            if need_note:
                note(f"实验动机:{need_note}")
        score = max(0.0, min(1.0, score))
        if not notes:
            note(f"{kind}/{anchor_type}")
        result = {
            "kind": kind,
            "anchor_type": anchor_type,
            "anchor_score": anchor_score,
            "pressure": max(0.0, min(1.0, pressure)),
            "risk": max(0.0, min(1.0, risk)),
            "score": score,
            "note": "；".join(notes[:4]),
            "blocker": blocker,
        }
        if need_profile:
            result.update(
                {
                    "need_layer": _single_line(need_profile.get("layer"), 40),
                    "need_drive": _single_line(need_profile.get("drive"), 80),
                    "need_note": _single_line(need_profile.get("note"), 120),
                    "need_score_bias": _safe_float(need_profile.get("score_bias"), 0.0),
                    "need_pressure_bias": _safe_float(need_profile.get("pressure_bias"), 0.0),
                }
            )
        return result

    def _planned_proactive_semantics(self, user: dict[str, Any]) -> dict[str, Any]:
        impulse = self._planned_proactive_impulse(user)
        if isinstance(impulse, dict):
            return {
                "kind": _single_line(impulse.get("semantic_kind"), 40),
                "anchor_type": _single_line(impulse.get("semantic_anchor_type"), 40),
                "score": _safe_float(impulse.get("semantic_score"), 0.5),
                "anchor_score": _safe_float(impulse.get("semantic_anchor_score"), 0.5),
                "pressure": _safe_float(impulse.get("semantic_pressure"), 0.4),
                "risk": _safe_float(impulse.get("semantic_risk"), 0.0),
                "note": _single_line(impulse.get("semantic_note"), 180),
                "need_layer": _single_line(impulse.get("semantic_need_layer"), 40),
                "need_drive": _single_line(impulse.get("semantic_need_drive"), 80),
                "need_note": _single_line(impulse.get("semantic_need_note"), 120),
                "need_score_bias": _safe_float(impulse.get("semantic_need_score_bias"), 0.0),
                "need_pressure_bias": _safe_float(impulse.get("semantic_need_pressure_bias"), 0.0),
                "blocker": bool(impulse.get("semantic_blocker")),
            }
        return self._proactive_candidate_semantics(
            user,
            reason=self._normalize_legacy_proactive_text(user.get("planned_proactive_reason"), limit=40) or "check_in",
            action=self._normalize_legacy_proactive_text(user.get("planned_proactive_action"), limit=60) or "message",
            motive=_single_line(user.get("planned_proactive_motive"), 180),
            topic=_single_line(user.get("planned_proactive_topic"), 100),
            source=self._normalize_legacy_proactive_text(user.get("planned_proactive_source"), limit=40),
            chain=user.get("planned_event_chain") if isinstance(user.get("planned_event_chain"), list) else [],
            trigger_message_id=_single_line(user.get("planned_proactive_trigger_message_id"), 120),
            trigger_ts=_safe_float(user.get("planned_proactive_trigger_ts"), 0),
        )

    def _planned_proactive_persona_alignment(
        self,
        user: dict[str, Any],
        *,
        now: float | None = None,
    ) -> dict[str, Any]:
        return self._proactive_persona_alignment(
            user,
            reason=self._normalize_legacy_proactive_text(user.get("planned_proactive_reason"), limit=40) or "check_in",
            action=self._normalize_legacy_proactive_text(user.get("planned_proactive_action"), limit=40) or "message",
            motive=_single_line(user.get("planned_proactive_motive"), 180),
            topic=_single_line(user.get("planned_proactive_topic"), 80),
            source=self._normalize_legacy_proactive_text(user.get("planned_proactive_source"), limit=40),
            now=now,
        )

    def _planned_proactive_impulse(self, user: dict[str, Any]) -> dict[str, Any] | None:
        impulse_id = _single_line(user.get("planned_proactive_impulse_id"), 20)
        if not impulse_id:
            return None
        for item in self._cleanup_proactive_impulses(user):
            if isinstance(item, dict) and _single_line(item.get("id"), 20) == impulse_id:
                return item
        return None

    def _planned_impulse_value(self, user: dict[str, Any], *, now: float | None = None) -> float:
        impulse = self._planned_proactive_impulse(user)
        if isinstance(impulse, dict):
            return self._score_proactive_impulse(user, impulse, now=now)
        reason = self._normalize_legacy_proactive_text(user.get("planned_proactive_reason"), limit=40)
        source = self._normalize_legacy_proactive_text(user.get("planned_proactive_source"), limit=40)
        value = 0.62
        if source in {"timer", "troubleshooting", "simulation"}:
            value += 0.35
        if source in {"pending_followup", "daily_greeting", "story", "state"}:
            value += 0.08
        if reason in {"important_date_share", "birthday_eve_hint", "birthday_celebration", "birthday_makeup", "birthday_afterglow", "quiet_care", "group_share", "news_share", "creative_share"}:
            value += 0.08
        if self._private_user_role(user) == "friend":
            value -= 0.06
        return value

    def _planned_impulse_window_phase(
        self,
        user: dict[str, Any],
        *,
        now: float | None = None,
    ) -> tuple[str, str]:
        check_now = _engine_host._now_ts() if now is None else now
        start_at = _safe_float(user.get("planned_proactive_window_start_at"), 0)
        best_until = _safe_float(user.get("planned_proactive_best_until_at"), 0)
        expire_at = _safe_float(user.get("planned_proactive_expire_at"), 0)
        if expire_at > 0 and check_now > expire_at:
            return "expired", "念头窗口已过期"
        if start_at > 0 and check_now < start_at:
            return "before", f"距窗口开始还有 {self._format_elapsed(start_at - check_now)}"
        if best_until > 0 and check_now <= best_until:
            return "best", f"正处于最佳表达窗口,剩余 {self._format_elapsed(best_until - check_now)}"
        if expire_at > 0:
            return "tail", f"已过最佳窗口,距过期 {self._format_elapsed(max(0, expire_at - check_now))}"
        return "unknown", "未记录念头窗口"

    def _proactive_item_freshness_class(
        self,
        *,
        action: str,
        reason: str,
        source: str,
        semantic_kind: str = "",
    ) -> str:
        normalized_reason = self._normalize_legacy_proactive_text(reason, limit=40)
        normalized_source = self._normalize_legacy_proactive_text(source, limit=40)
        normalized_kind = self._normalize_legacy_proactive_text(semantic_kind, limit=40)
        if normalized_reason in {"environment_change", "weather_alert", "health_alert", "memo_note_reminder"} or normalized_source in {
            "environment_change",
            "weather_alert",
            "body_monitor",
            "memo_note",
        }:
            return "immediate"
        if normalized_source == "timer" or normalized_reason in {
            "birthday_eve_hint",
            "birthday_celebration",
            "birthday_makeup",
            "birthday_afterglow",
            "important_date_share",
            "special_day_greeting",
            "bili_video_share",
            "news_share",
            "web_exploration_share",
            "creative_share",
        }:
            return "durable"
        action_parts = {part.strip() for part in str(action or "").split("+") if part.strip()}
        if {"photo_text", "screen_peek"} & action_parts:
            return "immediate"
        if normalized_kind in {"self_share", "observation"} and normalized_source in {
            "story",
            "daily_story",
            "state",
            "event",
            "simulation",
        }:
            return "immediate"
        return "contextual"

    def _proactive_timeliness_level(
        self,
        *,
        reason: Any = "",
        source: Any = "",
    ) -> str:
        """Classify only events whose value materially decays within minutes."""

        normalized_reason = self._normalize_legacy_proactive_text(reason, limit=40)
        normalized_source = self._normalize_legacy_proactive_text(source, limit=40)
        if normalized_reason in {"weather_alert", "health_alert"} or normalized_source in {
            "weather_alert",
            "body_monitor",
        }:
            return "urgent"
        if normalized_reason in {
            "environment_change",
            "memo_note_reminder",
            "birthday_celebration",
            "special_day_greeting",
            "insomnia_night",
        } or normalized_source in {
            "environment_change",
            "memo_note",
            "special_day_ritual",
            "night_care",
        }:
            return "timely"
        return "routine"

    @staticmethod
    def _proactive_timeliness_rank(level: Any) -> int:
        return {"routine": 0, "timely": 1, "urgent": 2}.get(str(level or "routine"), 0)

    def _planned_proactive_timeliness_level(self, user: dict[str, Any]) -> str:
        if not isinstance(user, dict):
            return "routine"
        return self._proactive_timeliness_level(
            reason=user.get("planned_proactive_reason"),
            source=user.get("planned_proactive_source"),
        )

    def _planned_proactive_delivery_key(self, user: dict[str, Any]) -> str:
        if not isinstance(user, dict):
            return ""
        parts = (
            _single_line(user.get("planned_candidate_id"), 40),
            _single_line(user.get("planned_proactive_impulse_id"), 40),
            self._normalize_legacy_proactive_text(user.get("planned_proactive_reason"), limit=40),
            self._normalize_legacy_proactive_text(user.get("planned_proactive_source"), limit=40),
            _single_line(user.get("planned_proactive_topic"), 120),
            _single_line(user.get("planned_proactive_motive"), 220),
        )
        if not any(parts):
            return ""
        return hashlib.sha1("\n".join(parts).encode("utf-8", errors="ignore")).hexdigest()

    def _planned_proactive_freshness_class(self, user: dict[str, Any]) -> str:
        if not isinstance(user, dict):
            return "contextual"
        delivery_key = self._planned_proactive_delivery_key(user)
        if delivery_key and _single_line(user.get("planned_proactive_origin_key"), 80) == delivery_key:
            stored = _single_line(user.get("planned_proactive_freshness"), 24)
            if stored in {"immediate", "contextual", "durable"}:
                return stored
        return self._proactive_item_freshness_class(
            action=str(user.get("planned_proactive_action") or "message"),
            reason=str(user.get("planned_proactive_reason") or ""),
            source=str(user.get("planned_proactive_source") or ""),
            semantic_kind=str(user.get("planned_proactive_semantic_kind") or ""),
        )

    def _ensure_planned_proactive_delivery_state(
        self,
        user: dict[str, Any],
        *,
        now: float | None = None,
    ) -> dict[str, Any]:
        if not isinstance(user, dict):
            return {}
        check_now = _engine_host._now_ts() if now is None else now
        delivery_key = self._planned_proactive_delivery_key(user)
        if not delivery_key:
            return {}
        origin_key = _single_line(user.get("planned_proactive_origin_key"), 80)
        origin_at = _safe_float(user.get("planned_proactive_origin_at"), 0)
        freshness = self._planned_proactive_freshness_class(user)
        if origin_key != delivery_key or origin_at <= 0:
            origin_at = _safe_float(user.get("planned_proactive_window_start_at"), 0) or _safe_float(user.get("next_proactive_at"), 0) or check_now
            user["planned_proactive_origin_at"] = origin_at
            user["planned_proactive_origin_key"] = delivery_key
            user["planned_proactive_freshness"] = freshness
            user["planned_proactive_delivery_state"] = "fresh"
        elif _single_line(user.get("planned_proactive_freshness"), 24) not in {"immediate", "contextual", "durable"}:
            user["planned_proactive_freshness"] = freshness
        return {
            "key": delivery_key,
            "origin_at": origin_at,
            "best_until_at": _safe_float(user.get("planned_proactive_best_until_at"), 0),
            "expire_at": _safe_float(user.get("planned_proactive_expire_at"), 0),
            "freshness": _single_line(user.get("planned_proactive_freshness"), 24) or freshness,
            "delivery_state": _single_line(user.get("planned_proactive_delivery_state"), 24) or "fresh",
        }

    def _planned_proactive_send_freshness_reason(
        self,
        user: dict[str, Any],
        snapshot: dict[str, Any] | None,
        *,
        now: float | None = None,
    ) -> str:
        if not isinstance(snapshot, dict) or not snapshot:
            return ""
        current = self._ensure_planned_proactive_delivery_state(user, now=now)
        if not current:
            return "主动候选已被清理或替换"
        if _single_line(current.get("key"), 80) != _single_line(snapshot.get("key"), 80):
            return "主动候选在生成期间已变化"
        check_now = _engine_host._now_ts() if now is None else now
        expire_at = _safe_float(current.get("expire_at"), 0)
        if expire_at > 0 and check_now > expire_at and self._normalize_legacy_proactive_text(user.get("planned_proactive_source"), limit=40) != "timer":
            return "主动候选在生成期间已过期"
        if _single_line(current.get("freshness"), 24) == "immediate":
            best_until = _safe_float(current.get("best_until_at"), 0)
            if best_until > 0 and check_now > best_until:
                return "即时主动已越过自然表达窗口"
        return ""

    def _is_immediate_life_share_impulse(self, impulse: dict[str, Any]) -> bool:
        if not isinstance(impulse, dict) or not self._action_has_photo_text(str(impulse.get("action") or "")):
            return False
        return self._proactive_item_freshness_class(
            action=str(impulse.get("action") or ""),
            reason=str(impulse.get("reason") or ""),
            source=str(impulse.get("source") or ""),
            semantic_kind=str(impulse.get("semantic_kind") or ""),
        ) == "immediate"

    def _defer_or_replace_planned_impulse(
        self,
        user: dict[str, Any],
        *,
        now: float | None = None,
        note: str = "",
        delay_minutes: tuple[float, float] = (30.0, 90.0),
        block_current: bool = False,
    ) -> bool:
        check_now = _engine_host._now_ts() if now is None else now
        impulse = self._planned_proactive_impulse(user)
        current_id = _single_line(user.get("planned_proactive_impulse_id"), 20)
        delivery = self._ensure_planned_proactive_delivery_state(user, now=check_now)
        freshness = _single_line(delivery.get("freshness"), 24) or "contextual"
        best_until = _safe_float(delivery.get("best_until_at"), 0)
        source = _single_line(user.get("planned_proactive_source"), 40)
        hard_expire_at = _safe_float(user.get("planned_proactive_expire_at"), 0) if source == "body_monitor" else 0
        if hard_expire_at > 0 and not block_current:
            delay = _engine_host.random.uniform(max(1.0, delay_minutes[0]), max(delay_minutes[0] + 1.0, delay_minutes[1])) * 60
            next_window = check_now + delay
            if next_window >= hard_expire_at:
                self._mark_planned_candidate_status(user, "blocked", "身体状态事件有效期已结束")
                if isinstance(impulse, dict):
                    impulse["state"] = "blocked"
                    impulse["last_note"] = "身体状态事件有效期已结束"
                    impulse["updated_ts"] = check_now
                self._clear_pending_proactive_plan(user)
                return False
            self._mark_planned_candidate_status(user, "deferred", note)
            if isinstance(impulse, dict):
                impulse["state"] = "deferred"
                impulse["window_start_at"] = next_window
                impulse["preferred_ts"] = next_window
                impulse["best_until_at"] = min(_safe_float(impulse.get("best_until_at"), hard_expire_at), hard_expire_at)
                impulse["expire_at"] = hard_expire_at
                impulse["updated_ts"] = check_now
            user["next_proactive_at"] = next_window
            user["planned_proactive_window_start_at"] = next_window
            user["planned_proactive_best_until_at"] = min(_safe_float(user.get("planned_proactive_best_until_at"), hard_expire_at), hard_expire_at)
            user["planned_proactive_expire_at"] = hard_expire_at
            user["planned_proactive_delivery_state"] = "deferred"
            return False
        is_immediate = freshness == "immediate"
        if is_immediate and not block_current and best_until > 0 and check_now >= best_until:
            expired_note = _single_line(note, 120) or "即时主动已过自然窗口"
            expired_note = f"{expired_note}；原候选已作废并重新安排"
            self._mark_planned_candidate_status(user, "blocked", expired_note)
            if isinstance(impulse, dict):
                impulse["updated_ts"] = check_now
                impulse["last_note"] = expired_note
                impulse["state"] = "blocked"
            self._clear_pending_proactive_plan(user)
            if not self._materialize_best_proactive_impulse(user, now=check_now):
                return False
            return bool(_single_line(user.get("planned_proactive_impulse_id"), 20) != current_id)
        if is_immediate and not block_current:
            self._mark_planned_candidate_status(user, "deferred", note)
            delay = _engine_host.random.uniform(max(1.0, delay_minutes[0]), max(delay_minutes[0] + 1.0, delay_minutes[1])) * 60
            next_window = min(check_now + delay, best_until) if best_until > 0 else check_now + delay
            capped_expire_at = best_until + 8 * 60 if best_until > 0 else 0
            if isinstance(impulse, dict):
                impulse["updated_ts"] = check_now
                impulse["last_note"] = _single_line(note, 160)
                impulse["state"] = "deferred"
                impulse["hesitation_count"] = _safe_int(impulse.get("hesitation_count"), 0, 0, 8) + 1
                impulse["hesitation_at"] = check_now
                impulse["hesitation_note"] = _single_line(note, 160)
                impulse["window_start_at"] = next_window
                impulse["preferred_ts"] = max(_safe_float(impulse.get("preferred_ts"), 0), next_window)
                if capped_expire_at > 0:
                    old_expire_at = _safe_float(impulse.get("expire_at"), 0)
                    impulse["expire_at"] = min(old_expire_at if old_expire_at > 0 else capped_expire_at, capped_expire_at)
                self._remember_proactive_hesitation(user, impulse, note=note, now=check_now)
            user["next_proactive_at"] = next_window
            user["planned_proactive_window_start_at"] = next_window
            if capped_expire_at > 0:
                old_expire_at = _safe_float(user.get("planned_proactive_expire_at"), 0)
                user["planned_proactive_expire_at"] = min(old_expire_at if old_expire_at > 0 else capped_expire_at, capped_expire_at)
            user["planned_proactive_delivery_state"] = "deferred"
            return False
        self._mark_planned_candidate_status(user, "blocked" if block_current else "deferred", note)
        if isinstance(impulse, dict):
            impulse["updated_ts"] = check_now
            impulse["last_note"] = _single_line(note, 160)
            if block_current:
                impulse["state"] = "blocked"
            else:
                hesitation_count = _safe_int(impulse.get("hesitation_count"), 0, 0, 8) + 1
                impulse["hesitation_count"] = hesitation_count
                impulse["hesitation_at"] = check_now
                impulse["hesitation_note"] = _single_line(note, 160)
                self._remember_proactive_hesitation(user, impulse, note=note, now=check_now)
                delay = _engine_host.random.uniform(max(1.0, delay_minutes[0]), max(delay_minutes[0] + 1.0, delay_minutes[1])) * 60
                next_window = check_now + delay
                impulse["state"] = "deferred"
                impulse["window_start_at"] = next_window
                impulse["preferred_ts"] = max(_safe_float(impulse.get("preferred_ts"), 0), next_window)
                impulse["best_until_at"] = max(_safe_float(impulse.get("best_until_at"), 0), next_window + 25 * 60)
                impulse["expire_at"] = max(_safe_float(impulse.get("expire_at"), 0), next_window + 90 * 60)
        elif not block_current:
            delay = _engine_host.random.uniform(max(1.0, delay_minutes[0]), max(delay_minutes[0] + 1.0, delay_minutes[1])) * 60
            next_window = check_now + delay
            user["next_proactive_at"] = next_window
            user["planned_proactive_window_start_at"] = next_window
            user["planned_proactive_best_until_at"] = next_window + 25 * 60
            user["planned_proactive_expire_at"] = next_window + 90 * 60
            user["planned_proactive_delivery_state"] = "deferred"
            return False
        self._clear_pending_proactive_plan(user)
        if not self._materialize_best_proactive_impulse(user, now=check_now):
            return False
        return bool(_single_line(user.get("planned_proactive_impulse_id"), 20) != current_id)

    def _defer_planned_proactive_to_quiet_end(
        self,
        user: dict[str, Any],
        *,
        now: float | None = None,
    ) -> tuple[bool, str]:
        check_now = _engine_host._now_ts() if now is None else now
        quiet_end_getter = getattr(self, "_quiet_hours_end_timestamp", None)
        quiet_end = _safe_float(quiet_end_getter(check_now), 0.0) if callable(quiet_end_getter) else 0.0
        if quiet_end <= check_now:
            return False, "当前不在免打扰时段"
        source = self._normalize_legacy_proactive_text(user.get("planned_proactive_source"), limit=40)
        if source in {"timer", "troubleshooting", "simulation"}:
            return False, "来源不参与免打扰改期"
        target = quiet_end + _engine_host.random.uniform(2 * 60, 8 * 60)
        delivery = self._ensure_planned_proactive_delivery_state(user, now=check_now)
        freshness = _single_line(delivery.get("freshness"), 24) or self._planned_proactive_freshness_class(user)
        expire_at = _safe_float(user.get("planned_proactive_expire_at"), 0)
        impulse = self._planned_proactive_impulse(user)
        if expire_at > 0 and target >= expire_at and freshness != "durable":
            self._mark_planned_candidate_status(user, "blocked", "免打扰覆盖整个有效窗口")
            if isinstance(impulse, dict):
                impulse["state"] = "blocked"
                impulse["last_status"] = "blocked"
                impulse["last_note"] = "免打扰覆盖整个有效窗口"
                impulse["updated_ts"] = check_now
            self._clear_pending_proactive_plan(user)
            # Do not let the generic scheduler put a weather event immediately
            # back into the same window.  Respect the quiet-hours boundary,
            # the user's normal proactive interval, and the weather cooldown.
            floor = quiet_end + 2 * 60
            last_sent = max(
                _safe_float(user.get("last_proactive_sent_at"), 0),
                _safe_float(user.get("last_proactive_message_at"), 0),
            )
            if last_sent > check_now:
                last_sent = 0.0
            min_interval_getter = getattr(self, "_effective_min_interval_seconds", None)
            if callable(min_interval_getter):
                try:
                    floor = max(floor, last_sent + max(0, int(min_interval_getter(user))))
                except Exception:
                    pass
            if source in {"weather_alert", "weather_context", "environment_change"}:
                floor = max(floor, _safe_float(user.get("weather_proactive_last_at"), 0) + 6 * 3600)
            delay_hours = max(0.08, (floor - check_now) / 3600.0)
            self._schedule_next_proactive(user, now=check_now, delay_hours=(delay_hours, delay_hours))
            if _safe_float(user.get("next_proactive_at"), 0) < floor:
                user["next_proactive_at"] = floor
            return True, "有效窗口被免打扰覆盖，已跳过并在免打扰结束后重排"

        old_start = _safe_float(user.get("planned_proactive_window_start_at"), check_now)
        old_best = _safe_float(user.get("planned_proactive_best_until_at"), old_start)
        if expire_at > 0 and target >= expire_at:
            shift = target - old_start
            new_best = max(old_best + shift, target + 20 * 60)
            new_expire = max(expire_at + shift, new_best + 20 * 60)
        else:
            new_best = max(old_best, min(expire_at, target + 20 * 60) if expire_at > 0 else target + 20 * 60)
            new_expire = expire_at if expire_at > 0 else new_best + 40 * 60
        user["next_proactive_at"] = target
        user["planned_proactive_window_start_at"] = target
        user["planned_proactive_best_until_at"] = new_best
        user["planned_proactive_expire_at"] = new_expire
        user["planned_proactive_delivery_state"] = "deferred"
        if isinstance(impulse, dict):
            impulse["state"] = "deferred"
            impulse["window_start_at"] = target
            impulse["preferred_ts"] = max(_safe_float(impulse.get("preferred_ts"), 0), target)
            impulse["best_until_at"] = new_best
            impulse["expire_at"] = new_expire
            impulse["updated_ts"] = check_now
            impulse["last_status"] = "deferred"
            impulse["last_note"] = "免打扰时段，已直接移到结束后"
        candidate_id = _single_line(user.get("planned_candidate_id"), 40)
        if candidate_id:
            for item in self._cleanup_proactive_candidate_pool(now=check_now):
                if _single_line(item.get("id"), 40) != candidate_id:
                    continue
                item["status"] = "deferred"
                item["note"] = "免打扰时段，已直接移到结束后"
                item["scheduled_ts"] = target
                item["window_start_at"] = target
                item["best_until_at"] = new_best
                item["expire_at"] = new_expire
                item["updated_ts"] = check_now
                break
        return True, "已直接调度到免打扰结束后"

    def _remember_proactive_hesitation(
        self,
        user: dict[str, Any],
        impulse: dict[str, Any],
        *,
        note: str = "",
        now: float | None = None,
    ) -> None:
        check_now = _engine_host._now_ts() if now is None else now
        raw = user.setdefault("recent_proactive_hesitations", [])
        if not isinstance(raw, list):
            raw = []
            user["recent_proactive_hesitations"] = raw
        item = {
            "ts": check_now,
            "reason": _single_line(impulse.get("reason"), 40),
            "source": _single_line(impulse.get("source"), 40),
            "topic": _single_line(impulse.get("topic"), 80),
            "motive": _single_line(impulse.get("motive"), 140),
            "note": _single_line(note, 140),
            "count": _safe_int(impulse.get("hesitation_count"), 1, 1, 20),
        }
        raw.append(item)
        del raw[:-8]
        user["last_proactive_hesitation_at"] = check_now
        user["last_proactive_hesitation_note"] = item["note"]

    def _motive_with_hesitation_memory(self, impulse: dict[str, Any], motive: str) -> str:
        count = _safe_int(impulse.get("hesitation_count"), 0, 0, 8)
        cleaned = self._normalize_internal_motive_text(motive)
        if count <= 0:
            return cleaned
        source = str(impulse.get("source") or "")
        if source in {"timer", "troubleshooting", "simulation"}:
            return cleaned
        topic = _single_line(impulse.get("topic"), 40)
        if cleaned:
            return cleaned
        if topic:
            return self._normalize_internal_motive_text(f"想到“{topic}”，想短短提一句")
        return ""

    def _materialize_best_proactive_impulse(
        self,
        user: dict[str, Any],
        *,
        now: float | None = None,
    ) -> bool:
        check_now = _engine_host._now_ts() if now is None else now
        user_id = str(user.get("user_id") or user.get("id") or "")
        active = [
            item
            for item in self._cleanup_proactive_impulses(user, now=check_now)
            if isinstance(item, dict) and str(item.get("state") or "queued") in {"queued", "deferred"}
        ]
        if not active:
            return False
        ready = [item for item in active if self._impulse_ready_now(item, now=check_now)]
        selected: dict[str, Any] | None = None
        review_at = 0.0
        if ready:
            ready.sort(
                key=lambda item: (
                    self._score_proactive_impulse(user, item, now=check_now)
                    + self._proactive_impulse_orchestration_priority(item) / 300.0,
                    self._proactive_impulse_orchestration_priority(item),
                ),
                reverse=True,
            )
            selected = ready[0]
            review_at = check_now
        else:
            future = sorted(
                [
                    item for item in active
                    if _safe_float(item.get("expire_at"), 0) > check_now
                    and _safe_float(item.get("window_start_at"), 0) > check_now
                ],
                key=lambda item: (
                    _safe_float(item.get("window_start_at"), check_now + 365 * 24 * 3600),
                    -self._score_proactive_impulse(user, item, now=check_now),
                ),
            )
            if not future:
                return False
            earliest_start = _safe_float(future[0].get("window_start_at"), check_now)
            near_term = [
                item
                for item in future
                if _safe_float(item.get("window_start_at"), earliest_start) <= earliest_start + 30 * 60
            ]
            selected = max(
                near_term,
                key=lambda item: (
                    self._proactive_impulse_orchestration_priority(item),
                    self._score_proactive_impulse(user, item, now=check_now),
                    -_safe_float(item.get("window_start_at"), earliest_start),
                ),
            )
            review_at = _safe_float(selected.get("window_start_at"), check_now)
        last_materialized_at = _safe_float(selected.get("last_materialized_at"), 0)
        materialized_count = _safe_int(selected.get("materialized_count"), 0, 0)
        if materialized_count >= 3 and check_now - last_materialized_at <= 15 * 60:
            selected["state"] = "blocked"
            selected["last_status"] = "blocked"
            selected["last_note"] = "同一来源短时间重复物化已熔断"
            selected["updated_ts"] = check_now
            logger.warning(
                "主动念头重复物化熔断: user=%s origin=%s count=%s",
                _single_line(user_id, 40),
                _single_line(selected.get("origin_event_id"), 80) or _single_line(selected.get("id"), 20),
                materialized_count,
            )
            return self._materialize_best_proactive_impulse(user, now=check_now)
        candidate = {
            "source": self._normalize_legacy_proactive_text(selected.get("source"), limit=40) or "impulse",
            "kind": _single_line(selected.get("kind"), 40) or self._proactive_message_kind(
                reason=selected.get("reason"),
                source=selected.get("source"),
                semantic_kind=selected.get("semantic_kind"),
            ),
            "quota_tier": _safe_int(self._proactive_quota_policy(user).get("tier"), 0, 0, 5),
            "reason": self._normalize_legacy_proactive_text(selected.get("reason"), limit=40) or "check_in",
            "action": self._normalize_legacy_proactive_text(selected.get("action"), limit=40) or "message",
            "scheduled_ts": max(review_at, _safe_float(selected.get("window_start_at"), review_at)),
            "topic": _single_line(selected.get("topic"), 80),
            "motive": self._motive_with_hesitation_memory(selected, _single_line(selected.get("motive"), 180)),
            "conversation_posture": _single_line(selected.get("conversation_posture"), 24).lower(),
            "conversation_closing_deferred": bool(selected.get("conversation_closing_deferred")),
            "score": int(max(0.0, min(1.0, self._score_proactive_impulse(user, selected, now=check_now))) * 100),
            "context_key": _single_line(selected.get("context_key"), 60),
            "context": selected.get("context"),
            "chain": selected.get("chain") if isinstance(selected.get("chain"), list) else [],
            "origin_event_id": _single_line(selected.get("origin_event_id"), 80),
            "window_start_at": _safe_float(selected.get("window_start_at"), 0),
            "preferred_ts": _safe_float(selected.get("preferred_ts"), 0),
            "best_until_at": _safe_float(selected.get("best_until_at"), 0),
            "expire_at": _safe_float(selected.get("expire_at"), 0),
            "window_timezone": _single_line(selected.get("window_timezone"), 64)
            or _engine_proactive_window_timezone(self),
        }
        for key in ("_mobile_location_transition_key", "_mobile_location_priority", "mobile_location_event_type"):
            if key in selected:
                candidate[key] = selected.get(key)
        item = self._record_proactive_candidate(
            user_id,
            candidate,
            status="accepted",
            note="由潜在念头池物化为当前主动计划",
            user=user,
        )
        self._reset_planned_proactive_delivery_state(user)
        user["next_proactive_at"] = candidate["scheduled_ts"]
        user["planned_proactive_reason"] = self._normalize_legacy_proactive_text(candidate["reason"], limit=40) or "check_in"
        user["planned_proactive_action"] = self._normalize_legacy_proactive_text(candidate["action"], limit=40) or "message"
        user["planned_proactive_source"] = self._normalize_legacy_proactive_text(candidate["source"], limit=40) or "impulse"
        user["planned_proactive_conversation_posture"] = _single_line(
            candidate.get("conversation_posture"),
            24,
        ).lower()
        user["planned_proactive_conversation_closing_deferred"] = bool(
            candidate.get("conversation_closing_deferred")
        )
        user["planned_proactive_kind"] = _single_line(candidate.get("kind"), 40)
        self._store_planned_proactive_route_fields(user, selected)
        user["planned_proactive_motive"] = self._normalize_internal_motive_text(candidate["motive"])
        user["planned_proactive_topic"] = candidate["topic"]
        if user["planned_proactive_reason"] == "birthday_curiosity":
            user["birthday_curiosity_asked_at"] = check_now
        user["planned_proactive_impulse_id"] = _single_line(selected.get("id"), 20)
        user["planned_mobile_location_transition_key"] = _single_line(
            selected.get("_mobile_location_transition_key"), 80
        )
        user["planned_mobile_location_event_type"] = _single_line(
            selected.get("mobile_location_event_type"), 32
        )
        user["planned_proactive_window_start_at"] = _safe_float(selected.get("window_start_at"), 0)
        user["planned_proactive_window_timezone"] = _single_line(
            selected.get("window_timezone"),
            64,
        ) or _engine_proactive_window_timezone(self)
        user["planned_proactive_best_until_at"] = _safe_float(selected.get("best_until_at"), 0)
        user["planned_proactive_expire_at"] = _safe_float(selected.get("expire_at"), 0)
        user["planned_proactive_semantic_kind"] = _single_line(selected.get("semantic_kind"), 40)
        user["planned_proactive_anchor_type"] = _single_line(selected.get("semantic_anchor_type"), 40)
        user["planned_proactive_semantic_score"] = int(max(0.0, min(1.0, _safe_float(selected.get("semantic_score"), 0.5))) * 100)
        user["planned_proactive_semantic_note"] = _single_line(selected.get("semantic_note"), 180)
        user["planned_proactive_need_layer"] = _single_line(selected.get("semantic_need_layer"), 40)
        user["planned_proactive_need_drive"] = _single_line(selected.get("semantic_need_drive"), 80)
        user["planned_proactive_need_note"] = _single_line(selected.get("semantic_need_note"), 120)
        user["planned_candidate_id"] = item.get("id", "")
        user["planned_event_chain"] = (
            []
            if self._private_user_role(user) == "friend"
            else [dict(step) for step in selected.get("chain", []) if isinstance(step, dict)]
        )
        user["planned_opener_mode"] = _single_line(selected.get("opener_mode"), 24)
        user["planned_followup_kind"] = _single_line(selected.get("followup_kind"), 32)
        user["planned_proactive_quota_exempt"] = bool(selected.get("quota_exempt"))
        self._set_planned_proactive_trigger(
            user,
            message_id=_single_line(selected.get("trigger_message_id"), 120),
            umo=_single_line(selected.get("trigger_umo"), 160),
            created_at=_safe_float(selected.get("trigger_ts"), 0),
        )
        context_key = _single_line(selected.get("context_key"), 60)
        context = selected.get("context")
        if context_key and isinstance(context, dict):
            user[context_key] = dict(context)
        selected["updated_ts"] = check_now
        selected["state"] = "queued"
        materialized_at = _safe_float(selected.get("last_materialized_at"), 0)
        materialized_count = _safe_int(selected.get("materialized_count"), 0, 0)
        selected["materialized_count"] = materialized_count + 1 if check_now - materialized_at <= 15 * 60 else 1
        selected["last_materialized_at"] = check_now
        return True

    def _record_proactive_candidate(
        self,
        user_id: str,
        candidate: dict[str, Any],
        *,
        status: str,
        note: str = "",
        user: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        disabled = getattr(self, "_proactive_generation_disabled", None)
        target_user = user
        if not isinstance(target_user, dict):
            users = self.data.get("users") if isinstance(self.data.get("users"), dict) else {}
            target_user = users.get(str(user_id)) if isinstance(users.get(str(user_id)), dict) else None
        if callable(disabled) and disabled(target_user):
            return {}
        now = _engine_host._now_ts()
        source_hint = _single_line(candidate.get("source"), 40) or "unknown"
        if isinstance(target_user, dict):
            candidate = self._prepare_proactive_route_candidate(
                target_user,
                candidate,
                source=source_hint,
                now=now,
            )
        topic = _single_line(candidate.get("topic"), 80)
        motive = _single_line(candidate.get("motive"), 160)
        action = _single_line(candidate.get("action"), 40) or "message"
        source = _single_line(candidate.get("source"), 40) or "unknown"
        reason = _single_line(candidate.get("reason"), 40) or "check_in"
        scheduled = _safe_float(candidate.get("scheduled_ts"), now)
        origin_event_id = _single_line(candidate.get("origin_event_id"), 80)
        signature = self._proactive_topic_signature(topic, motive)
        semantics: dict[str, Any] = {}
        if isinstance(user, dict):
            semantics = self._proactive_candidate_semantics(
                user,
                reason=reason,
                action=action,
                motive=motive,
                topic=topic,
                source=source,
                context=candidate.get("context"),
                chain=candidate.get("chain") if isinstance(candidate.get("chain"), list) else [],
                trigger_message_id=self._candidate_trigger_message_id(candidate),
                trigger_ts=_safe_float(candidate.get("trigger_ts") or candidate.get("created_ts"), 0),
            )
        semantic_fields = {
            "semantic_kind": _single_line(semantics.get("kind"), 40),
            "semantic_anchor_type": _single_line(semantics.get("anchor_type"), 40),
            "semantic_score": int(max(0.0, min(1.0, _safe_float(semantics.get("score"), 0.0))) * 100) if semantics else 0,
            "semantic_pressure": int(max(0.0, min(1.0, _safe_float(semantics.get("pressure"), 0.0))) * 100) if semantics else 0,
            "semantic_risk": int(max(0.0, min(1.0, _safe_float(semantics.get("risk"), 0.0))) * 100) if semantics else 0,
            "semantic_note": _single_line(semantics.get("note"), 180),
            "semantic_need_layer": _single_line(semantics.get("need_layer"), 40),
            "semantic_need_drive": _single_line(semantics.get("need_drive"), 80),
            "semantic_need_note": _single_line(semantics.get("need_note"), 120),
            "semantic_need_score_bias": _safe_float(semantics.get("need_score_bias"), 0.0),
            "semantic_need_pressure_bias": _safe_float(semantics.get("need_pressure_bias"), 0.0),
        }
        proactive_kind = _single_line(candidate.get("kind"), 40) or self._proactive_message_kind(
            reason=reason,
            source=source,
            semantic_kind=semantic_fields.get("semantic_kind"),
        )
        quota_policy = self._proactive_quota_policy(target_user if isinstance(target_user, dict) else {})
        pool = self._cleanup_proactive_candidate_pool(now=now)
        if status in {"blocked", "accepted"}:
            for existing in reversed(pool):
                if not isinstance(existing, dict):
                    continue
                if str(existing.get("status") or "") != status:
                    continue
                if str(existing.get("user_id") or "") != str(user_id):
                    continue
                if status == "accepted" and str(existing.get("id") or "") == str(candidate.get("id") or ""):
                    continue
                same_origin = bool(
                    origin_event_id
                    and origin_event_id == _single_line(existing.get("origin_event_id"), 80)
                )
                if not same_origin and not self._topic_signature_similar(signature, str(existing.get("signature") or "")):
                    continue
                existing_short_lived = self._proactive_candidate_is_short_lived(existing)
                incoming_short_lived = reason in {"weather_alert", "environment_change"} or source in {
                    "weather_alert",
                    "environment_change",
                }
                merge_horizon = 2 * 3600 if existing_short_lived or incoming_short_lived else 18 * 3600
                if now - _safe_float(existing.get("last_seen_ts") or existing.get("created_ts"), 0) > merge_horizon:
                    continue
                existing_expire_at = _safe_float(existing.get("expire_at"), 0)
                if (existing_short_lived or incoming_short_lived) and existing_expire_at > 0 and now > existing_expire_at + 2 * 3600:
                    continue
                repeat_limit = self._candidate_repeat_count_limit(status)
                previous_repeat = _safe_int(existing.get("repeat_count"), 1, 1)
                existing["repeat_count"] = min(repeat_limit, previous_repeat + 1)
                existing["merged_trigger_count"] = _safe_int(
                    existing.get("merged_trigger_count"),
                    max(0, previous_repeat - 1),
                    0,
                ) + 1
                merged_by_day = existing.get("merged_by_day")
                if not isinstance(merged_by_day, dict):
                    merged_by_day = {}
                    existing["merged_by_day"] = merged_by_day
                today_key = _today_key()
                merged_by_day[today_key] = _safe_int(merged_by_day.get(today_key), 0, 0) + 1
                if len(merged_by_day) > 8:
                    existing["merged_by_day"] = {
                        key: merged_by_day[key]
                        for key in sorted(merged_by_day)[-8:]
                    }
                if previous_repeat + 1 > repeat_limit:
                    existing["repeat_count_capped"] = True
                existing["last_seen_ts"] = now
                existing["updated_ts"] = now
                existing["scheduled_ts"] = max(_safe_float(existing.get("scheduled_ts"), scheduled), scheduled)
                if origin_event_id:
                    existing["origin_event_id"] = origin_event_id
                for key in ("window_start_at", "preferred_ts", "best_until_at", "expire_at"):
                    incoming_value = _safe_float(candidate.get(key), 0)
                    if incoming_value > 0:
                        existing[key] = incoming_value
                existing["source"] = source or _single_line(existing.get("source"), 40)
                existing["kind"] = proactive_kind
                existing["quota_tier"] = _safe_int(quota_policy.get("tier"), 0, 0, 5)
                for route_key in (
                    "route_version",
                    "route_dedupe_key",
                    "route_review_profile",
                    "route_retry_profile",
                    "route_cancel_if_new_inbound",
                    "route_recent_chat_policy",
                    "route_allow_automatic_followup",
                    "route_disable_segmenting",
                    "response_expectation",
                ):
                    if route_key in candidate:
                        existing[route_key] = candidate[route_key]
                existing["reason"] = reason or _single_line(existing.get("reason"), 40)
                existing["action"] = action or _single_line(existing.get("action"), 40)
                existing["topic"] = topic or _single_line(existing.get("topic"), 80)
                existing["motive"] = motive or _single_line(existing.get("motive"), 160)
                existing_posture = _single_line(candidate.get("conversation_posture"), 24).lower()
                if existing_posture in {"closing", "open", "neutral"}:
                    existing["conversation_posture"] = existing_posture
                if note:
                    existing["note"] = _single_line(note, 160)
                existing["score"] = max(_safe_int(existing.get("score"), 0, 0, 100), _safe_int(candidate.get("score"), 0, 0, 100))
                if semantics:
                    existing.update(semantic_fields)
                return existing
        item = {
            "id": uuid.uuid4().hex[:12],
            "created_ts": now,
            "last_seen_ts": now,
            "scheduled_ts": scheduled,
            "window_start_at": _safe_float(candidate.get("window_start_at"), 0),
            "preferred_ts": _safe_float(candidate.get("preferred_ts"), 0),
            "best_until_at": _safe_float(candidate.get("best_until_at"), 0),
            "expire_at": _safe_float(candidate.get("expire_at"), 0),
            "origin_event_id": origin_event_id,
            "user_id": str(user_id),
            "source": source,
            "kind": proactive_kind,
            "kind_label": _single_line(self._proactive_kind_policy(proactive_kind).get("label"), 40),
            "quota_tier": _safe_int(quota_policy.get("tier"), 0, 0, 5),
            "route_version": _safe_int(candidate.get("route_version"), 0, 0),
            "route_dedupe_key": _single_line(candidate.get("route_dedupe_key"), 180),
            "route_review_profile": _single_line(candidate.get("route_review_profile"), 40),
            "route_retry_profile": _single_line(candidate.get("route_retry_profile"), 40),
            "route_cancel_if_new_inbound": bool(candidate.get("route_cancel_if_new_inbound", True)),
            "route_recent_chat_policy": _single_line(candidate.get("route_recent_chat_policy"), 40),
            "route_allow_automatic_followup": bool(candidate.get("route_allow_automatic_followup", True)),
            "route_disable_segmenting": bool(candidate.get("route_disable_segmenting", False)),
            "response_expectation": _single_line(candidate.get("response_expectation"), 24),
            "reason": reason,
            "action": action,
            "topic": topic,
            "motive": motive,
            "conversation_posture": (
                _single_line(candidate.get("conversation_posture"), 24).lower()
                if _single_line(candidate.get("conversation_posture"), 24).lower() in {"closing", "open", "neutral"}
                else ""
            ),
            "score": _safe_int(candidate.get("score"), 0, 0, 100),
            "signature": signature,
            "status": status,
            "note": _single_line(note, 160),
            "repeat_count": 1,
            "merged_trigger_count": 0,
            "merged_by_day": {},
            **(semantic_fields if semantics else {}),
        }
        pool.append(item)
        self._cleanup_proactive_candidate_pool(now=now)
        return item

    def _proactive_candidate_repeated(self, user: dict[str, Any], candidate: dict[str, Any]) -> bool:
        candidate_kind = _single_line(candidate.get("kind"), 40) or self._proactive_message_kind(
            reason=candidate.get("reason"),
            source=candidate.get("source"),
            semantic_kind=candidate.get("semantic_kind"),
        )
        # Deterministic event routes own their lifecycle and evidence identity;
        # generic topic similarity must not suppress a new reminder or alert.
        if candidate_kind in {"transactional", "safety_event"}:
            return False
        # 只按内容（topic）判定重复；外部分享类的 motive 是统一模板，计入签名
        # 会让不同内容被判"主题过于相似"而误杀。
        signature = self._proactive_topic_signature(
            candidate.get("topic"),
        )
        if not signature:
            return False
        if self._recent_proactive_topic_repeated(user, signature):
            return True
        now = _engine_host._now_ts()
        user_id = str(user.get("user_id") or user.get("id") or "")
        for item in self._cleanup_proactive_candidate_pool(now=now):
            if str(item.get("user_id") or "") != user_id:
                continue
            if str(item.get("status") or "") not in {"accepted", "sent"}:
                continue
            item_kind = _single_line(item.get("kind"), 40) or self._proactive_message_kind(
                reason=item.get("reason"),
                source=item.get("source"),
                semantic_kind=item.get("semantic_kind"),
            )
            if item_kind != candidate_kind:
                continue
            if now - _safe_float(item.get("created_ts"), 0) > 8 * 3600:
                continue
            if self._topic_signature_similar(signature, str(item.get("signature") or "")):
                return True
        return False

    def _offer_proactive_candidate(self, user_id: str, user: dict[str, Any], candidate: dict[str, Any]) -> bool:
        user["user_id"] = str(user.get("user_id") or user_id)
        now = _engine_host._now_ts()
        source = _single_line(candidate.get("source"), 40) or "unknown"
        scheduled = _safe_float(candidate.get("scheduled_ts"), now)
        prepared, invalid_window_reason = self._prepare_proactive_candidate_window(
            candidate,
            reason=_single_line(candidate.get("reason"), 40) or "check_in",
            source=source,
            now=now,
        )
        if not isinstance(prepared, dict):
            if invalid_window_reason == "免打扰覆盖整个有效窗口":
                self._remember_weather_proactive_block(
                    user,
                    candidate,
                    now=now,
                    reason=invalid_window_reason,
                )
            logger.info(
                "主动来源在入队前终止: user=%s source=%s reason=%s note=%s",
                _single_line(user_id, 40),
                source,
                _single_line(candidate.get("reason"), 40),
                _single_line(invalid_window_reason, 120),
            )
            return False
        candidate = prepared
        scheduled = _safe_float(candidate.get("scheduled_ts"), now)
        incoming_timeliness = self._proactive_timeliness_level(
            reason=candidate.get("reason"),
            source=source,
        )
        social_relay_note = self._unverified_social_relay_plan_reason(
            candidate,
            source=source,
            has_trigger=bool(self._candidate_trigger_message_id(candidate)),
        )
        if social_relay_note:
            self._record_proactive_candidate(user_id, candidate, status="blocked", note=social_relay_note, user=user)
            return False
        rest_until = self._proactive_rest_block_until(
            user,
            now=now,
            reason=candidate.get("reason"),
            source=source,
        )
        if rest_until > now and scheduled < rest_until:
            self._record_proactive_candidate(user_id, candidate, status="blocked", note="用户明确休息中", user=user)
            return False
        busy_until = 0.0
        busy_block_kind = ""
        busy_context_getter = getattr(self, "_busy_reply_proactive_block_context", None)
        busy_gate = getattr(self, "_busy_reply_proactive_block_until", None)
        if callable(busy_context_getter):
            try:
                busy_context = busy_context_getter(
                    user,
                    now=now,
                    reason=candidate.get("reason"),
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
                        reason=candidate.get("reason"),
                        source=source,
                    ),
                    0.0,
                )
            except Exception:
                busy_until = 0.0
        if busy_until > now and scheduled < busy_until and (
            incoming_timeliness == "routine" or busy_block_kind == "external_realtime"
        ):
            expire_at = _safe_float(candidate.get("expire_at"), 0)
            preserve_event_expiry = incoming_timeliness != "routine"
            if preserve_event_expiry and expire_at > 0 and busy_until >= expire_at:
                self._record_proactive_candidate(user_id, candidate, status="blocked", note="实时共处覆盖事件有效期", user=user)
                return False
            shift = busy_until - scheduled
            candidate = dict(candidate)
            shift_keys = (
                ("scheduled_ts", "window_start_at", "preferred_ts", "best_until_at")
                if preserve_event_expiry
                else ("scheduled_ts", "window_start_at", "preferred_ts", "best_until_at", "expire_at")
            )
            for key in shift_keys:
                value = _safe_float(candidate.get(key), 0.0)
                if value > 0:
                    candidate[key] = value + shift
            if preserve_event_expiry:
                candidate["best_until_at"] = min(_safe_float(candidate.get("best_until_at"), 0), expire_at)
            scheduled = _safe_float(candidate.get("scheduled_ts"), busy_until)
        candidate = self._defer_candidate_after_conversation_closing(
            user,
            candidate,
            now=now,
            timeliness=incoming_timeliness,
        )
        scheduled = _safe_float(candidate.get("scheduled_ts"), scheduled)
        if not self._user_enabled_for_proactive(str(user_id), user):
            self._clear_pending_proactive_plan(user)
            return False
        weather_block = self._weather_proactive_block_reason(user, candidate, now=now)
        if weather_block:
            self._record_proactive_candidate(user_id, candidate, status="blocked", note=weather_block, user=user)
            return False
        planned_source = self._normalize_legacy_proactive_text(
            user.get("planned_proactive_source"), limit=40
        ).lower()
        planned_timezone = _single_line(user.get("planned_proactive_window_timezone"), 64)
        candidate_timezone = _single_line(candidate.get("window_timezone"), 64)
        if (
            candidate_timezone
            and planned_timezone
            and planned_timezone != candidate_timezone
            and source in {"weather_alert", "environment_change", "weather_context"}
            and planned_source in {"weather_alert", "environment_change", "weather_context"}
        ):
            # The candidate was rebased to a new effective timezone; discard
            # the old planned timestamp before comparing scheduling priority.
            self._clear_pending_proactive_plan(user)
        silence_reason_getter = getattr(self, "_friend_unanswered_silence_reason", None)
        silence_reason = silence_reason_getter(user, now=now) if callable(silence_reason_getter) else ""
        if silence_reason and source not in {"timer", "troubleshooting", "simulation"}:
            self._record_proactive_candidate(user_id, candidate, status="blocked", note=silence_reason, user=user)
            return False
        if not self._friend_can_receive_proactive_reason(user, candidate.get("reason"), candidate.get("action")):
            return False
        timer_event = self._get_active_llm_timer(user)
        timer_scheduled = _safe_float(timer_event.get("scheduled_ts"), 0) if isinstance(timer_event, dict) else 0.0
        if timer_scheduled > now and scheduled < timer_scheduled and self._in_llm_timer_silence_window(user, now=now):
            self._remember_silenced_candidate_for_timer(user, candidate, now=now)
            self._record_proactive_candidate(user_id, candidate, status="blocked", note="已有聊天临时预约临近", user=user)
            return False
        if _safe_float(user.get("next_proactive_at"), 0) > 0 and self._normalize_legacy_proactive_text(user.get("planned_proactive_source"), limit=40) == "timer":
            current_timer = self._get_active_llm_timer(user)
            if self._llm_timer_can_use_internal_scheduler(current_timer if isinstance(current_timer, dict) else None):
                self._record_proactive_candidate(user_id, candidate, status="blocked", note="已有用户预约/定时主动", user=user)
                return False
            self._clear_llm_timer_internal_plan_fields(user)
        current_next = _safe_float(user.get("next_proactive_at"), 0)
        preempted_for_timeliness = False
        if current_next > 0 and current_next <= scheduled:
            current_timeliness = self._planned_proactive_timeliness_level(user)
            if self._proactive_timeliness_rank(incoming_timeliness) <= self._proactive_timeliness_rank(current_timeliness):
                # 挤占策略：默认直接丢弃（blocked，保持原行为）；
                # 开启 proactive_preempt_queue_enabled 后改为入池排队（deferred），
                # 当前计划发送完成后由 _schedule_next_proactive 自动晋升为下一条。
                if bool(runtime_persona_setting(self, "proactive_preempt_queue_enabled", False)):
                    queued_impulse = self._candidate_to_impulse(user, candidate, source=source, now=now)
                    if isinstance(queued_impulse, dict):
                        queued_impulse["state"] = "deferred"
                        queued_impulse["last_note"] = "已有更早主动候选，挤占入池排队"
                        queued_impulse["updated_ts"] = now
                        # 入池时把窗口整体后移：保证 expire_at 至少到入池时刻 +
                        # 配置小时数（proactive_preempt_queue_expire_hours，默认 2h），
                        # 避免排队等待期间窗口过期被 _cleanup_proactive_impulses 清掉。
                        preempt_expire_hours = _safe_int(
                            runtime_persona_setting(self, "proactive_preempt_queue_expire_hours", 2),
                            2,
                            1,
                            24,
                        )
                        min_expire = now + preempt_expire_hours * 3600
                        shift = max(0.0, min_expire - _safe_float(queued_impulse.get("expire_at"), 0))
                        if shift > 0:
                            for key in ("window_start_at", "preferred_ts", "best_until_at", "expire_at"):
                                value = _safe_float(queued_impulse.get(key), 0)
                                if value > 0:
                                    queued_impulse[key] = value + shift
                        self._queue_proactive_impulse(user, queued_impulse)
                        self._record_proactive_candidate(
                            user_id, candidate, status="deferred", note="已有更早主动候选，已入池排队等待", user=user
                        )
                    else:
                        self._record_proactive_candidate(user_id, candidate, status="blocked", note="已有更早主动候选", user=user)
                else:
                    self._record_proactive_candidate(user_id, candidate, status="blocked", note="已有更早主动候选", user=user)
                return False
            preempted_for_timeliness = True
        action = _single_line(candidate.get("action"), 40) or "message"
        if self._private_user_role(user, str(user_id)) == "friend" and self._action_has_photo_text(action):
            action = self._fallback_action_for_unavailable(action, user)
        if self._private_user_role(user, str(user_id)) == "friend":
            sanitized = self._sanitize_friend_proactive_plan_fields(
                user,
                reason=_single_line(candidate.get("reason"), 40) or "check_in",
                action=action,
                topic=_single_line(candidate.get("topic"), 80),
                motive=_single_line(candidate.get("motive"), 180),
            )
            action = sanitized["action"]
            candidate = dict(candidate)
            candidate["reason"] = sanitized["reason"]
            candidate["topic"] = sanitized["topic"]
            candidate["motive"] = sanitized["motive"]
            if self._friend_proactive_candidate_leaks_owner_environment(user, candidate):
                self._record_proactive_candidate(user_id, candidate, status="blocked", note="次要用户不接收主要用户环境/天气分享", user=user)
                return False
        if not self._action_is_available(action, user):
            self._record_proactive_candidate(user_id, candidate, status="blocked", note="动作不可用或媒体额度不足", user=user)
            return False
        if incoming_timeliness == "routine" and self._proactive_candidate_repeated(user, candidate):
            self._record_proactive_candidate(user_id, candidate, status="blocked", note="近期主题过于相似", user=user)
            return False
        impulse = self._candidate_to_impulse(user, candidate, source=source, now=now)
        if not isinstance(impulse, dict):
            return False
        queued_impulse = self._queue_proactive_impulse(user, impulse)
        if not isinstance(queued_impulse, dict) or not queued_impulse:
            return False
        impulse = queued_impulse
        if preempted_for_timeliness:
            self._mark_planned_candidate_status(user, "deferred", "更高时效主动已优先进入当前发送窗口")
        item = self._record_proactive_candidate(user_id, candidate, status="accepted", note="进入主动计划", user=user)
        self._remember_weather_proactive_accept(user, candidate, now=now)
        self._reset_planned_proactive_delivery_state(user)
        user["next_proactive_at"] = scheduled
        user["planned_proactive_reason"] = self._normalize_legacy_proactive_text(candidate.get("reason"), limit=40) or "check_in"
        user["planned_proactive_action"] = self._normalize_legacy_proactive_text(action, limit=40) or "message"
        user["planned_proactive_source"] = self._normalize_legacy_proactive_text(source, limit=40) or "proactive"
        user["planned_proactive_conversation_posture"] = _single_line(
            impulse.get("conversation_posture") if isinstance(impulse, dict) else candidate.get("conversation_posture"),
            24,
        ).lower()
        user["planned_proactive_conversation_closing_deferred"] = bool(
            candidate.get("conversation_closing_deferred")
        )
        user["planned_proactive_window_timezone"] = _single_line(candidate.get("window_timezone"), 64)
        user["planned_proactive_kind"] = _single_line(impulse.get("kind"), 40) or self._proactive_message_kind(
            reason=candidate.get("reason"),
            source=source,
            semantic_kind=impulse.get("semantic_kind"),
        )
        self._store_planned_proactive_route_fields(user, impulse)
        user["planned_proactive_motive"] = self._normalize_internal_motive_text(
            _single_line(candidate.get("motive"), 180)
        )
        user["planned_proactive_topic"] = _single_line(candidate.get("topic"), 80)
        user["planned_mobile_location_transition_key"] = _single_line(
            candidate.get("_mobile_location_transition_key"), 80
        )
        user["planned_mobile_location_event_type"] = _single_line(
            candidate.get("mobile_location_event_type"), 32
        )
        user["planned_proactive_impulse_id"] = _single_line(impulse.get("id"), 20) if isinstance(impulse, dict) else ""
        user["planned_proactive_window_start_at"] = _safe_float(
            impulse.get("window_start_at"),
            scheduled,
        ) if isinstance(impulse, dict) else scheduled
        user["planned_proactive_window_timezone"] = (
            _single_line(impulse.get("window_timezone"), 64)
            if isinstance(impulse, dict)
            else ""
        ) or _engine_proactive_window_timezone(self)
        user["planned_proactive_best_until_at"] = _safe_float(
            impulse.get("best_until_at"),
            scheduled,
        ) if isinstance(impulse, dict) else scheduled
        user["planned_proactive_expire_at"] = _safe_float(
            impulse.get("expire_at"),
            scheduled,
        ) if isinstance(impulse, dict) else scheduled
        if isinstance(impulse, dict):
            user["planned_proactive_semantic_kind"] = _single_line(impulse.get("semantic_kind"), 40)
            user["planned_proactive_anchor_type"] = _single_line(impulse.get("semantic_anchor_type"), 40)
            user["planned_proactive_semantic_score"] = int(max(0.0, min(1.0, _safe_float(impulse.get("semantic_score"), 0.5))) * 100)
            user["planned_proactive_semantic_note"] = _single_line(impulse.get("semantic_note"), 180)
            user["planned_proactive_need_layer"] = _single_line(impulse.get("semantic_need_layer"), 40)
            user["planned_proactive_need_drive"] = _single_line(impulse.get("semantic_need_drive"), 80)
            user["planned_proactive_need_note"] = _single_line(impulse.get("semantic_need_note"), 120)
        else:
            semantics = self._planned_proactive_semantics(user)
            user["planned_proactive_semantic_kind"] = _single_line(semantics.get("kind"), 40)
            user["planned_proactive_anchor_type"] = _single_line(semantics.get("anchor_type"), 40)
            user["planned_proactive_semantic_score"] = int(max(0.0, min(1.0, _safe_float(semantics.get("score"), 0.5))) * 100)
            user["planned_proactive_semantic_note"] = _single_line(semantics.get("note"), 180)
            user["planned_proactive_need_layer"] = _single_line(semantics.get("need_layer"), 40)
            user["planned_proactive_need_drive"] = _single_line(semantics.get("need_drive"), 80)
            user["planned_proactive_need_note"] = _single_line(semantics.get("need_note"), 120)
        user["planned_event_chain"] = [] if self._private_user_role(user) == "friend" else (
            [dict(step) for step in impulse.get("chain", []) if isinstance(step, dict)]
            if isinstance(impulse, dict)
            else []
        )
        user["planned_opener_mode"] = _single_line(impulse.get("opener_mode"), 24) if isinstance(impulse, dict) else ""
        user["planned_followup_kind"] = _single_line(impulse.get("followup_kind"), 32) if isinstance(impulse, dict) else ""
        self._clear_planned_proactive_trigger(user)
        user["planned_proactive_quota_exempt"] = False
        user["planned_candidate_id"] = item.get("id", "")
        self._set_planned_proactive_trigger(
            user,
            message_id=self._candidate_trigger_message_id(candidate),
            umo=_single_line(candidate.get("trigger_umo") or candidate.get("umo"), 160),
            created_at=_safe_float(candidate.get("trigger_ts") or candidate.get("created_ts"), 0),
        )
        context_key = _single_line(candidate.get("context_key"), 60)
        context = candidate.get("context")
        if context_key and isinstance(context, dict):
            user[context_key] = context
        return True

    def _planned_proactive_signature(self, user: dict[str, Any]) -> str:
        # motive 不参与计划重复判定（模板化动机文本）；source/reason 保留以区分主动类型。
        return self._proactive_topic_signature(
            user.get("planned_proactive_topic"),
            self._normalize_legacy_proactive_text(user.get("planned_proactive_source"), limit=40),
            self._normalize_legacy_proactive_text(user.get("planned_proactive_reason"), limit=40),
        )

    def _planned_proactive_recently_repeated(self, user: dict[str, Any]) -> bool:
        signature = self._planned_proactive_signature(user)
        if not signature:
            return False
        return self._recent_proactive_topic_repeated(user, signature)

    def _unverified_social_relay_plan_reason(
        self,
        item: dict[str, Any],
        *,
        source: str = "",
        has_trigger: bool = False,
    ) -> str:
        if not isinstance(item, dict):
            return ""
        normalized_source = self._normalize_legacy_proactive_text(source or item.get("source") or item.get("planned_proactive_source"), limit=40)
        if normalized_source in {"timer", "troubleshooting", "simulation", "group_share"}:
            return ""
        if has_trigger:
            return ""
        reason = self._normalize_legacy_proactive_text(item.get("reason") or item.get("planned_proactive_reason"), limit=40)
        if reason in {"group_share", "news_share", "bili_video_share", "web_exploration_share"}:
            return ""
        if normalized_source not in {"event", "random", "unknown", ""}:
            return ""
        text = " ".join(
            _single_line(item.get(key), 180)
            for key in (
                "topic",
                "planned_proactive_topic",
                "motive",
                "planned_proactive_motive",
                "why",
                "scene",
                "impulse",
            )
            if _single_line(item.get(key), 180)
        )
        if not text:
            return ""
        relay_markers = ("转达", "转述", "转告", "带话", "捎话")
        if any(token in text for token in relay_markers):
            return "疑似第三方转述/带话内容,缺少真实触发来源"
        invite_markers = ("约", "邀请", "要不要去", "去不去", "一起", "夜宵", "吃饭", "见面", "碰头")
        soft_message_markers = ("留言", "说一声", "说一下", "告诉你一声", "通知你一声")
        third_party_patterns = (
            r"[\u4e00-\u9fffA-Za-z0-9_]{1,12}(?:说|问|发(?:来|了|的)?(?:消息)?|留言|约|邀请)",
            r"(?:他|她|TA|ta)(?:说|问|发(?:来|了|的)?|留言|约|邀请)",
            r"(?:他的|她的|TA的|ta的).{0,8}(?:消息|留言|邀约|邀请)",
        )
        has_third_party_signal = any(re.search(pattern, text) for pattern in third_party_patterns)
        if has_third_party_signal and any(token in text for token in soft_message_markers):
            return "疑似第三方留言/带话内容,缺少真实触发来源"
        if any(token in text for token in invite_markers) and has_third_party_signal:
            return "疑似第三方邀约内容,缺少真实触发来源"
        return ""

    def _planned_proactive_status_snapshot(self, user: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(user, dict):
            return {}
        keys = (
            "planned_candidate_id",
            "planned_proactive_impulse_id",
            "planned_proactive_reason",
            "planned_proactive_action",
            "planned_proactive_source",
            "planned_proactive_conversation_posture",
            "planned_proactive_conversation_closing_deferred",
            "planned_proactive_kind",
            "planned_proactive_motive",
            "planned_proactive_topic",
            "planned_proactive_semantic_kind",
            "planned_proactive_anchor_type",
            "planned_proactive_semantic_score",
            "planned_proactive_semantic_note",
            "planned_proactive_need_layer",
            "planned_proactive_need_drive",
            "planned_proactive_need_note",
            "planned_mobile_location_transition_key",
            "planned_mobile_location_event_type",
        )
        return {key: user.get(key) for key in keys}

    def _mark_planned_candidate_status(
        self,
        user: dict[str, Any],
        status: str,
        note: str = "",
        *,
        planned_snapshot: dict[str, Any] | None = None,
    ) -> None:
        restored_values: dict[str, Any] = {}
        if isinstance(planned_snapshot, dict) and planned_snapshot:
            for key, value in planned_snapshot.items():
                if value in (None, "", {}, []):
                    continue
                restored_values[key] = user.get(key)
                user[key] = value
        try:
            outcome_recorder = getattr(self, "_note_proactive_afterglow_outcome", None)
            if callable(outcome_recorder):
                try:
                    outcome_recorder(user, status=status, note=note)
                except Exception as exc:
                    logger.debug("主动结果余韵记录失败: %s", _single_line(exc, 120))
            candidate_id = str(user.get("planned_candidate_id") or "")
            user_id = str(user.get("user_id") or user.get("id") or "")
            if candidate_id:
                for item in self._cleanup_proactive_candidate_pool():
                    if str(item.get("id") or "") == candidate_id:
                        item["status"] = status
                        item["note"] = _single_line(note, 160)
                        item["updated_ts"] = _engine_host._now_ts()
                        break
            impulse_id = _single_line(user.get("planned_proactive_impulse_id"), 20)
            if not impulse_id:
                return
            for impulse in self._cleanup_proactive_impulses(user):
                if _single_line(impulse.get("id"), 20) != impulse_id:
                    continue
                impulse["updated_ts"] = _engine_host._now_ts()
                impulse["last_status"] = _single_line(status, 24)
                impulse["last_note"] = _single_line(note, 160)
                if status in {"sent"}:
                    impulse["state"] = "sent"
                elif status in {"blocked", "cancelled", "dropped"}:
                    impulse["state"] = "blocked"
                elif status == "deferred":
                    impulse["state"] = "deferred"
                    next_at = _safe_float(user.get("next_proactive_at"), 0)
                    if next_at > 0:
                        impulse["window_start_at"] = next_at
                        impulse["preferred_ts"] = max(_safe_float(impulse.get("preferred_ts"), 0), next_at)
                        if _single_line(impulse.get("source"), 40) == "body_monitor":
                            hard_expire_at = _safe_float(user.get("planned_proactive_expire_at"), 0)
                            impulse["best_until_at"] = min(
                                max(_safe_float(impulse.get("best_until_at"), 0), next_at),
                                hard_expire_at,
                            )
                            impulse["expire_at"] = hard_expire_at
                        else:
                            impulse["best_until_at"] = max(_safe_float(impulse.get("best_until_at"), 0), next_at + 20 * 60)
                            impulse["expire_at"] = max(_safe_float(impulse.get("expire_at"), 0), impulse["best_until_at"] + 40 * 60)
                else:
                    impulse["state"] = "queued"
                break
            is_send_retry_deferred = status == "deferred" and (
                "已保留待重发内容" in str(note or "") or "平台发送" in str(note or "")
            )
            if user_id and status in {"blocked", "cancelled", "dropped", "failed", "deferred"} and not is_send_retry_deferred:
                self._shrink_user_proactive_candidates(user_id, note=note)
        finally:
            for key, value in restored_values.items():
                user[key] = value

    def _maybe_upgrade_planned_message_action(
        self,
        action: str,
        *,
        reason: str,
        user: dict[str, Any],
        motive: str = "",
        planned_event: dict[str, Any] | None = None,
    ) -> str:
        normalized = str(action or "message").strip() or "message"
        if normalized != "message":
            return self._fallback_action_for_unavailable(normalized, user)
        if isinstance(planned_event, dict) and (planned_event.get("_daily_greeting") or planned_event.get("_daily_meal_care")):
            return "message"
        candidates: list[tuple[str, float]] = []
        event_text = ""
        if isinstance(planned_event, dict):
            event_text = " ".join(
                _single_line(planned_event.get(key), 80)
                for key in ("topic", "why", "scene", "motive", "impulse")
            )
        combined_hint = f"{event_text} {motive}"
        if self._screen_glance_available(user) and reason in {"check_in", "quiet_care", "background_schedule"}:
            candidates.append(("screen_peek", 1.15))
        if (
            self._photo_text_available(user)
            and reason in {"activity_share", "diary_share", "background_schedule", "noon_greeting", "evening_greeting"}
            and self._strong_photo_share_intent(event_text, motive, user.get("planned_proactive_topic"))
        ):
            return "photo_text"
        photo_probability = self._proactive_photo_text_trigger_probability(
            reason,
            event_text,
            motive,
            user.get("planned_proactive_topic"),
            user=user,
        )
        if self._photo_text_available(user) and photo_probability > 0 and _engine_host.random.random() < photo_probability:
            return "photo_text"
        if self._photo_text_available(user) and (
            reason in {"activity_share", "diary_share", "background_schedule", "noon_greeting", "evening_greeting"}
            or any(token in combined_hint for token in self._visual_share_tokens())
        ):
            candidates.append(("photo_text", 1.05))
        if self._voice_available(user) and reason in {"quiet_care", "diary_share", "insomnia_night", "evening_greeting"}:
            candidates.append(("voice", 0.82))
        if self._poke_available() and self._effective_user_poke_daily_limit(user) > 0 and self._poke_action_cooldown_remaining(user) <= 0 and reason in {"check_in", "quiet_care", "morning_greeting", "evening_greeting"}:
            candidates.append(("poke", 0.62))
        if not candidates:
            return "message"
        candidates.append(("message", 0.38))
        return self._fallback_action_for_unavailable(self._weighted_choice(candidates), user)

    def _pick_best_planned_event(
        self, user: dict[str, Any], now: float | None = None
    ) -> dict[str, Any] | None:
        now = now or _engine_host._now_ts()
        candidates = []
        for event in (
            self._pick_pending_followup_event(user, now),
            self._pick_meal_care_event(user, now=now),
            self._pick_daily_greeting_event(user, now),
            self._pick_mobile_location_arrival_event(user, now=now),
            self._habit_proactive_event_for_user(user, now=now),
            self._pick_mood_checkin_event(user, now=now),
            self._pick_memory_echo_event(user, now=now),
            self._pick_absence_miss_event(user, now=now),
            self._pick_game_invite_event(user, now=now),
            self._pick_state_need_event(user, now=now),
            self._pick_story_plan_event(now, user=user),
        ):
            if not isinstance(event, dict):
                continue
            if self._unverified_social_relay_plan_reason(
                event,
                source="event",
                has_trigger=bool(_single_line(event.get("trigger_message_id"), 120)),
            ):
                continue
            reason = str(event.get("reason") or "check_in")
            event_ts = self._timestamp_from_story_event(event, reason)
            if self._friend_proactive_scheduled_too_early(user, event_ts):
                continue
            if event_ts > now or (
                event_ts > 0
                and now - event_ts
                <= runtime_persona_setting(self, "max_proactive_plan_lag_minutes", 180) * 60
            ):
                candidates.append((event_ts, event))
        if not candidates:
            return None
        near_sticky = [
            (event_ts, event)
            for event_ts, event in candidates
            if self._is_sticky_greeting_event(event) and 0 < event_ts - now <= 90 * 60
        ]
        if near_sticky:
            near_sticky.sort(key=lambda item: (self._event_priority(item[1]), item[0]))
            return near_sticky[0][1]
        non_sticky = [
            (event_ts, event)
            for event_ts, event in candidates
            if not self._is_sticky_greeting_event(event)
        ]
        if non_sticky:
            non_sticky.sort(key=lambda item: item[0])
            weighted = []
            for index, (_, event) in enumerate(non_sticky[:3]):
                priority_tuple = self._event_priority(event)
                priority_score = float(-priority_tuple[0])
                weighted.append((event, 1.0 + priority_score * 0.05 + max(0.0, 0.35 - index * 0.1)))
            return self._weighted_choice(weighted)
        ranked = sorted(
            candidates,
            key=lambda item: (self._event_priority(item[1]), item[0]),
        )
        top = ranked[:3]
        return _engine_host.random.choice(top)[1]

