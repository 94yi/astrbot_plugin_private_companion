# -*- coding: utf-8 -*-
"""面板摘要域。

由 tools/split_mixin_domain.py 从 page_api.py 机械抽取（30 个方法 + 0 个模块级名字 + 0 个类级赋值 / 2195 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApi）。
"""
from __future__ import annotations

import re
import sqlite3
import time
from .companion_interaction_expression import current_interaction_projection
from .helpers import _safe_int
from .relationship_ledger import normalize_relationship_mode, relationship_ledger_summary
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .logging_util import get_module_logger

logger = get_module_logger(__name__)



class PrivateCompanionPageApiSummaryPanelMixin:
    """面板摘要域（从 PrivateCompanionPageApi 拆出）。"""


    def _relationship_panel(
        self,
        user_id: str,
        user: dict[str, Any],
        *,
        relationship_stage: str,
    ) -> dict[str, Any]:
        """Return a user-scoped display DTO without authority or private content."""
        del user_id
        role = self.plugin._private_user_role(user, str(user.get("user_id") or "")) if hasattr(self.plugin, "_private_user_role") else str(user.get("relationship_role") or "friend")
        mode = normalize_relationship_mode(user.get("relationship_mode"), role)
        intimacy = self._relationship_intimacy_projection(self._int(user.get("relationship_score")))
        changes = relationship_ledger_summary(user)
        intimacy["trend"] = changes.get("trend", "steady")
        intimacy["recent_delta"] = changes.get("recent_delta", 0)
        interaction = current_interaction_projection(
            user.get("current_interaction"),
            relationship_role=role,
            relationship_mode=mode,
            relationship_score=user.get("relationship_score"),
            normal_interaction_band_cap=getattr(self.plugin, "normal_interaction_band_cap", "warm"),
            now=time.time(),
        )
        expression: dict[str, Any] = {
            "contract": "companion_interaction_expression.v2",
            "status": "configured_projection",
            "expression_band": interaction.get("expression_band") or "relaxed",
            "tone": "steady",
            "response_length": "balanced",
            "initiative": "passive_only",
            "pacing": "steady",
            "directness": "natural",
            "validation_style": "none",
            "self_disclosure": "none",
            "humor_mode": "off",
            "topic_initiative": "reply_only",
            "safety_mode": "live_event_not_evaluated",
            "blocker": "",
            "reason_codes": [],
        }
        builder = getattr(self.plugin, "_build_expression_decision_for_user", None)
        if callable(builder):
            try:
                raw = builder(user, passive_reengagement=True)
                raw_projection = raw.to_dict() if hasattr(raw, "to_dict") else dict(raw or {})
                if isinstance(raw_projection, dict):
                    expression.update(raw_projection)
            except Exception:
                pass
        dimension_defaults = {
            "pacing": "steady",
            "directness": "natural",
            "validation_style": "none",
            "self_disclosure": "none",
            "humor_mode": "off",
            "topic_initiative": "reply_only",
        }
        dimension_values = {
            "pacing": {"slow", "steady", "bright"},
            "directness": {"indirect", "natural", "direct"},
            "validation_style": {"none", "acknowledge", "support_first"},
            "self_disclosure": {"none", "light", "allowed"},
            "humor_mode": {"off", "light", "playful"},
            "topic_initiative": {"reply_only", "followup", "shared_topic"},
        }
        expression["contract"] = "companion_interaction_expression.v2"
        for key, allowed in dimension_values.items():
            value = self._single_line(expression.get(key), 20)
            expression[key] = value if value in allowed else dimension_defaults[key]
        inbound = max(0, self._int(user.get("inbound_count")))
        proactive = max(0, self._int(user.get("proactive_sent_count")))
        replies = max(0, self._int(user.get("reply_count")))
        if not proactive:
            reply_band = "no_proactive_sample"
        elif replies / proactive >= 0.65:
            reply_band = "steady"
        elif replies / proactive >= 0.30:
            reply_band = "some"
        else:
            reply_band = "low"

        score = self._int(user.get("relationship_score"))
        basis = "close" if score >= 55 else "familiar" if score >= 3 else "initial"
        memory_phase = {"status": "unavailable", "phase": "unknown", "momentum_band": "unknown"}
        getter = getattr(self.plugin, "_memory_companion_peek_relationship_phase", None)
        session_id = self._single_line(user.get("umo"), 200)
        if session_id and callable(getter):
            try:
                raw = getter(session_id=session_id)
            except Exception:
                raw = {}
            if isinstance(raw, dict):
                observed = raw.get("observed") is True and raw.get("status") == "observed"
                phase = self._single_line(raw.get("phase"), 32)
                memory_phase = {
                    "status": "observed" if observed and phase else "not_observed",
                    "phase": phase if observed and phase else "unknown",
                    "momentum_band": self._single_line(raw.get("momentum_band"), 20) if observed else "unknown",
                }

        return {
            "relationship_mode": mode,
            "relationship_intimacy": intimacy,
            "relationship_changes": changes,
            "current_interaction": interaction,
            "expression_decision": expression,
            "relationship_positive_stage_cap_key": getattr(self.plugin, "relationship_positive_stage_cap_key", "close"),
            "normal_interaction_band_cap": getattr(self.plugin, "normal_interaction_band_cap", "warm"),
            "relationship_basis": {"band": basis},
            "relationship_stage": self._single_line(relationship_stage, 24) or "unclassified",
            "interaction": {"inbound_count": inbound, "reply_count": replies, "reply_band": reply_band},
            "memory_phase": memory_phase,
            "network": {"status": "group_local_only", "pending_observation_count": 0},
            "reply_temperature": {"status": "live_chat_only"},
        }

    def _companion_plugins_summary(self) -> dict[str, dict[str, bool]]:
        image_api_getter = getattr(self.plugin, "_image_companion_api", None)
        try:
            image_api = image_api_getter() if callable(image_api_getter) else None
        except Exception:
            image_api = None
        image_status_getter = getattr(image_api, "status", None) if image_api is not None else None
        try:
            image_status = image_status_getter() if callable(image_status_getter) else {}
        except Exception:
            image_status = {}
        if not isinstance(image_status, dict):
            image_status = {}
        image_contract = self._image_extension_contract_status(image_api)
        if image_contract:
            image_status = {**image_status, "companion_contract": image_contract}
            if not image_contract["available"]:
                image_status["available"] = False
                image_status["reason"] = image_contract["reason"] or "image_contract_incompatible"

        nai_api_getter = getattr(self.plugin, "_nai_image_api", None)
        try:
            nai_api = nai_api_getter() if callable(nai_api_getter) else None
        except Exception:
            nai_api = None
        nai_status_getter = getattr(self.plugin, "_nai_image_status", None)
        if not callable(nai_status_getter):
            nai_status_getter = getattr(nai_api, "status", None) if nai_api is not None else None
        try:
            nai_status = nai_status_getter() if callable(nai_status_getter) else {}
        except Exception:
            nai_status = {}
        if not isinstance(nai_status, dict):
            nai_status = {}

        reality_api_getter = getattr(self.plugin, "_reality_companion_api", None)
        try:
            reality_api = reality_api_getter() if callable(reality_api_getter) else None
        except Exception:
            reality_api = None
        reality_status_getter = getattr(reality_api, "status", None) if reality_api is not None else None
        try:
            reality_status = reality_status_getter() if callable(reality_status_getter) else {}
        except Exception:
            reality_status = {}
        if not isinstance(reality_status, dict):
            reality_status = {}

        content_status_getter = getattr(self.plugin, "_content_companion_status", None)
        try:
            content_status = content_status_getter() if callable(content_status_getter) else {}
        except Exception as exc:
            logger.warning(
                "创作扩展状态读取失败: %s",
                self._single_line(exc, 160),
            )
            content_status = {}
        if not isinstance(content_status, dict):
            content_status = {}

        image_summary = {
            "installed": image_api is not None,
            "enabled": bool(image_status.get("enabled")),
            "available": bool(image_api is not None and image_status.get("available", True)),
            "reason": self._single_line(image_status.get("reason"), 120),
        }
        if image_status.get("companion_contract"):
            image_summary["companion_contract"] = image_status["companion_contract"]

        return {
            # These two capabilities are part of the companion core. Keep them
            # visible in the same status payload so the panel and diagnostics
            # do not mistake them for optional external plugins.
            "boundary_feedback": {
                "installed": True,
                "enabled": bool(getattr(self.plugin, "enable_relationship_boundary_feedback", True)),
                "available": callable(getattr(self.plugin, "_enrich_boundary_feedback_intent", None)),
            },
            "temp_emotion": {
                "installed": True,
                "enabled": bool(getattr(self.plugin, "enable_emotion_simulation", True)),
                "available": callable(getattr(self.plugin, "_record_interaction_emotion_event", None)),
            },
            "content": {
                "installed": bool(content_status.get("installed")),
                "enabled": bool(content_status.get("enabled")),
                "available": bool(content_status.get("available")),
                "reason": self._single_line(
                    content_status.get("reason") or "content_companion_unavailable",
                    120,
                ),
            },
            "image": image_summary,
            "nai": {
                "installed": nai_api is not None,
                "enabled": bool(nai_status.get("enabled")),
                "available": bool(nai_api is not None and nai_status.get("available", False)),
            },
            "reality": {
                "installed": reality_api is not None,
                "enabled": bool(reality_status.get("enabled")),
                "available": bool(reality_api is not None and reality_status.get("available", True)),
            },
        }

    def _req041_runtime_summary(self) -> dict[str, Any]:
        """Build an aggregate-only migration and isolation status for administrators."""
        runtime = getattr(self.plugin, "req041_migration_status", None)
        runtime = runtime if isinstance(runtime, dict) else {}
        coordinator = getattr(self.plugin, "req041_migration_coordinator", None)
        outbox = getattr(self.plugin, "req041_migration_outbox", None)
        control: dict[str, Any] = {}
        aggregates: dict[str, Any] = {
            "identities": [], "active_read_leases": 0,
            "pending": {"total": 0, "reasons": []},
        }
        queue: dict[str, Any] = {"backlog": 0, "states": {}}
        try:
            if coordinator is not None:
                control = coordinator.status()
                summary_getter = getattr(coordinator, "safe_admin_summary", None)
                if callable(summary_getter):
                    aggregates = summary_getter()
            epoch = str(control.get("migration_epoch") or "")
            queue_getter = getattr(outbox, "safe_admin_summary", None)
            if epoch and callable(queue_getter):
                queue = queue_getter(epoch)
        except Exception:
            return {
                "state": "degraded", "phase": "", "code": "admin_summary_unavailable",
                "checkpoint": "", "required": bool(runtime.get("required")),
                "migration": aggregates, "outbox": queue,
                "observability": {}, "config_consistency": {},
            }
        state = str(runtime.get("state") or control.get("state") or "unknown")
        phase = str(runtime.get("phase") or control.get("phase") or "")
        pending_total = int((aggregates.get("pending") or {}).get("total") or 0)
        observability = getattr(self.plugin, "req041_observability", None)
        if observability is not None:
            observability.migration(
                state=state, phase=phase, backlog=int(queue.get("backlog") or 0),
                pending=pending_total,
                mismatches=int((observability.snapshot().get("counters") or {}).get("migration_mismatch") or 0),
            )
            metrics = observability.snapshot()
        else:
            metrics = {}
        allowlist = getattr(self.plugin, "group_relationship_affinity_allowlist", [])
        allowlist_count = len(allowlist) if isinstance(allowlist, (list, tuple, set, frozenset)) else 0
        affinity_enabled = bool(getattr(self.plugin, "enable_group_relationship_affinity", False))
        return {
            "state": state if state in {"active", "replaying", "degraded", "paused", "complete"} else "unknown",
            "phase": phase if phase in {"S0", "S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8", "S9"} else "",
            "code": self._single_line(runtime.get("code") or control.get("error_code"), 120),
            "checkpoint": self._single_line(runtime.get("checkpoint") or control.get("checkpoint"), 120),
            "required": bool(runtime.get("required")),
            "migration": aggregates,
            "outbox": queue,
            "observability": metrics,
            "config_consistency": {
                "group_affinity_enabled": affinity_enabled,
                "group_affinity_allowlist_count": allowlist_count,
                "group_affinity_effective": bool(affinity_enabled and allowlist_count > 0),
                "memory_bridge_bound": bool(runtime.get("memory_bound")),
                "scoped_projection_ready": bool((runtime.get("scoped") or {}).get("ok")),
            },
        }

    def _daily_outfit_summary(self, data: dict[str, Any]) -> dict[str, Any]:
        item = data.get("daily_outfit_photo") if isinstance(data.get("daily_outfit_photo"), dict) else {}
        path = self._single_line(item.get("path"), 300)
        exists = False
        if path:
            try:
                exists = Path(path).exists() and Path(path).is_file()
            except Exception:
                exists = False
        date_key = self._single_line(item.get("date"), 20)
        image_query = f"?date={quote(date_key)}&ts={self._single_line(item.get('generated_at'), 40)}" if exists else ""
        return {
            "enabled": bool(getattr(self.plugin, "enable_daily_outfit_photo", False)),
            "date": date_key,
            "available": bool(exists),
            "path": path if exists else "",
            "image_url": f"/daily_outfit/image{image_query}" if exists else "",
            "image_data_url": f"/daily_outfit/image_data{image_query}" if exists else "",
            "backend": self._single_line(item.get("backend"), 80),
            "error": self._single_line(item.get("error"), 220),
            "generated_at": self.plugin._format_timestamp_elapsed(item.get("generated_at", 0)) if item else "",
            "retry_count": int(item.get("retry_count", 0) or 0),
            "retry_max": 5,
        }

    def _prompt_injection_summary(self, data: dict[str, Any]) -> dict[str, Any]:
        raw = data.get("recent_prompt_injections")
        if not isinstance(raw, dict):
            raw = {}
        raw_events = data.get("recent_prompt_injection_events")
        if not isinstance(raw_events, list):
            raw_events = []

        def preview_is_internal_prompt(value: Any) -> bool:
            cleaned = self._single_line(value, 260)
            if not cleaned:
                return False
            internal_markers = (
                "【语音消息规则】",
                "<pc_tts>",
                "</pc_tts>",
                "语音消息规则",
                "提示词片段",
                "请求级环境感知注入",
                "被动回复注入",
                "当前语音正文目标语种",
                "自然聊天时用中文文字推进对话",
                "不要写“中文含义”",
            )
            if any(marker in cleaned for marker in internal_markers):
                return True
            return cleaned.startswith("【") and "规则" in cleaned[:40]

        def safe_message_preview(value: Any, limit: int = 120) -> str:
            preview = self._single_line(value, limit)
            if preview_is_internal_prompt(preview):
                return ""
            return preview

        def normalize_item(item: Any) -> dict[str, Any] | None:
            if not isinstance(item, dict):
                return None
            ts = self._float(item.get("ts"))
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            raw_modules = item.get("modules") if isinstance(item.get("modules"), list) else []
            if not raw_modules:
                normalizer = getattr(self.plugin, "_normalize_prompt_injection_modules", None)
                if callable(normalizer):
                    try:
                        raw_modules = normalizer(str(item.get("content") or ""), None)
                    except Exception:
                        raw_modules = []
            modules: list[dict[str, Any]] = []
            for index, module in enumerate(raw_modules[:28]):
                if not isinstance(module, dict):
                    continue
                content = str(module.get("content") or "")[:6500]
                if not content.strip():
                    continue
                module_metadata = module.get("metadata") if isinstance(module.get("metadata"), dict) else {}
                modules.append(
                    {
                        "key": self._single_line(module.get("key"), 100) or f"module.{index + 1}",
                        "source": self._single_line(module.get("source"), 80),
                        "priority": self._int(module.get("priority")),
                        "title": self._single_line(module.get("title"), 80) or "提示词片段",
                        "description": self._single_line(module.get("description"), 260),
                        "chars": self._int(module.get("chars")),
                        "truncated": bool(module.get("truncated")),
                        "preview": self._single_line(module.get("preview"), 220),
                        "content": content,
                        "metadata": {
                            self._single_line(key, 40): self._single_line(value, 120)
                            for key, value in module_metadata.items()
                            if self._single_line(key, 40) and self._single_line(value, 120)
                        },
                    }
                )
            return {
                "ts": ts,
                "time": self.plugin._format_timestamp_elapsed(ts),
                "kind": self._single_line(item.get("kind"), 20),
                "session": self._single_line(item.get("session"), 160),
                "title": self._single_line(item.get("title"), 80),
                "mode": self._single_line(item.get("mode"), 40),
                "chars": self._int(item.get("chars")),
                "truncated": bool(item.get("truncated")),
                "preview": self._single_line(item.get("preview"), 260),
                "trace_seq": self._int(item.get("trace_seq")),
                "content": str(item.get("content") or "")[:13000],
                "modules": modules,
                "metadata": {
                    self._single_line(key, 40): self._single_line(value, 240)
                    for key, value in metadata.items()
                    if self._single_line(key, 40) and self._single_line(value, 240)
                },
            }

        def normalize_message(item: Any) -> dict[str, Any] | None:
            if not isinstance(item, dict):
                return None
            raw_items = item.get("items") if isinstance(item.get("items"), list) else []
            normalized_items = [entry for entry in (normalize_item(raw_item) for raw_item in raw_items[:32]) if entry]
            normalized_items.sort(
                key=lambda entry: (
                    self._float(entry.get("ts")),
                    self._int(entry.get("trace_seq")),
                )
            )
            if not normalized_items:
                return None
            first_ts = self._float(item.get("first_ts")) or min(self._float(entry.get("ts")) for entry in normalized_items)
            last_ts = self._float(item.get("last_ts")) or max(self._float(entry.get("ts")) for entry in normalized_items)
            kinds: list[str] = []
            for entry in normalized_items:
                kind = self._single_line(entry.get("kind"), 20)
                if kind and kind not in kinds:
                    kinds.append(kind)
            message_preview = safe_message_preview(item.get("message_preview"), 120)
            if not message_preview:
                message_preview = next(
                    (
                        safe_message_preview(entry.get("metadata", {}).get("触发消息"), 120)
                        for entry in normalized_items
                        if isinstance(entry, dict)
                        and safe_message_preview(entry.get("metadata", {}).get("触发消息"), 120)
                    ),
                    "",
                )
            return {
                "trace_id": self._single_line(item.get("trace_id"), 80),
                "session": self._single_line(item.get("session"), 160) or self._single_line(normalized_items[-1].get("session"), 160),
                "sender_label": self._single_line(item.get("sender_label"), 80),
                "message_preview": message_preview,
                "first_ts": first_ts,
                "first_time": self.plugin._format_timestamp_elapsed(first_ts),
                "last_ts": last_ts,
                "time": self.plugin._format_timestamp_elapsed(last_ts),
                "item_count": len(normalized_items),
                "module_count": sum(len(entry.get("modules") or []) for entry in normalized_items),
                "kinds": kinds,
                "items": normalized_items,
            }

        result: dict[str, Any] = {}
        for kind in ("tts", "proactive", "passive", "request"):
            items = raw.get(kind) if isinstance(raw.get(kind), list) else []
            limit = 8 if kind == "tts" else 5
            normalized = [entry for entry in (normalize_item(item) for item in items[:limit]) if entry]
            result[kind] = normalized
        messages = [entry for entry in (normalize_message(item) for item in raw_events[:10]) if entry]
        if not messages:
            legacy_messages: list[dict[str, Any]] = []
            for kind in ("request", "passive", "proactive", "tts"):
                for index, item in enumerate(result.get(kind, [])):
                    if not isinstance(item, dict):
                        continue
                    ts = self._float(item.get("ts"))
                    legacy_messages.append(
                        {
                            "trace_id": self._single_line(item.get("trace_id"), 80) or f"legacy-{kind}-{index}",
                            "session": self._single_line(item.get("session"), 160),
                            "sender_label": self._single_line(item.get("metadata", {}).get("发送者"), 80),
                            "message_preview": safe_message_preview(item.get("metadata", {}).get("触发消息"), 120),
                            "first_ts": ts,
                            "first_time": self.plugin._format_timestamp_elapsed(ts),
                            "last_ts": ts,
                            "time": self.plugin._format_timestamp_elapsed(ts),
                            "item_count": 1,
                            "module_count": len(item.get("modules") or []),
                            "kinds": [kind],
                            "items": [item],
                        }
                    )
            legacy_messages.sort(key=lambda entry: self._float(entry.get("last_ts")), reverse=True)
            messages = legacy_messages[:10]
        else:
            messages.sort(key=lambda entry: self._float(entry.get("last_ts")), reverse=True)
        result["messages"] = messages[:10]
        result["message_total"] = len(result["messages"])
        result["total"] = (
            len(result.get("tts", []))
            + len(result.get("proactive", []))
            + len(result.get("passive", []))
            + len(result.get("request", []))
        )
        return result

    def _passive_no_reply_summary(self, data: dict[str, Any]) -> dict[str, Any]:
        raw = data.get("passive_no_reply_records")
        if not isinstance(raw, dict):
            return {"total": 0, "items": []}
        items: list[dict[str, Any]] = []
        now = time.time()
        max_age_seconds = 2 * 60 * 60
        hidden_stale = 0
        hidden_obsolete = 0
        for item in raw.get("items", []):
            if not isinstance(item, dict):
                continue
            last_ts = self._float(item.get("last_ts"))
            if self._passive_no_reply_item_is_obsolete_fixed_error(item):
                hidden_obsolete += 1
                continue
            if last_ts > 0 and now - last_ts > max_age_seconds:
                hidden_stale += 1
                continue
            samples = []
            for sample in item.get("samples", []) if isinstance(item.get("samples"), list) else []:
                if not isinstance(sample, dict):
                    continue
                ts = self._float(sample.get("ts"))
                samples.append(
                    {
                        "time": self.plugin._format_timestamp_elapsed(ts) if ts else self._single_line(sample.get("time"), 40),
                        "session": self._single_line(sample.get("session"), 120),
                        "sender_id": self._single_line(sample.get("sender_id"), 80),
                        "inbound": self._single_line(sample.get("inbound"), 120),
                        "detail": self._single_line(sample.get("detail"), 160),
                        "reply_preview": self._single_line(sample.get("reply_preview"), 140),
                        "ts": ts,
                    }
                )
            items.append(
                {
                    "key": self._single_line(item.get("key"), 32),
                    "level": self._single_line(item.get("level"), 12) or "info",
                    "source": self._single_line(item.get("source"), 40) or "被动未回复",
                    "reason": self._single_line(item.get("reason"), 120) or "未说明原因",
                    "count": self._int(item.get("count")),
                    "first_ts": self._float(item.get("first_ts")),
                    "last_ts": last_ts,
                    "last_time": self.plugin._format_timestamp_elapsed(last_ts) if last_ts else "",
                    "last_session": self._single_line(item.get("last_session"), 120),
                    "last_sender_id": self._single_line(item.get("last_sender_id"), 80),
                    "last_inbound": self._single_line(item.get("last_inbound"), 120),
                    "last_detail": self._single_line(item.get("last_detail"), 160),
                    "last_action": self._single_line(item.get("last_action"), 120),
                    "last_reply_preview": self._single_line(item.get("last_reply_preview"), 140),
                    "samples": samples[:5],
                }
            )
        items.sort(key=lambda item: self._float(item.get("last_ts")), reverse=True)
        total = self._int(raw.get("total")) or sum(self._int(item.get("count")) for item in items)
        return {
            "total": total,
            "last_ts": self._float(raw.get("last_ts")),
            "items": items[:80],
            "hidden_stale": hidden_stale,
            "hidden_obsolete": hidden_obsolete,
            "max_age_seconds": max_age_seconds,
        }

    async def _sqlite_wal_status_summary(self) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        paths_getter = getattr(self.plugin, "_sqlite_wal_candidate_paths", None)
        paths = paths_getter() if callable(paths_getter) else []

        def inspect(path: Path) -> dict[str, Any]:
            try:
                conn = sqlite3.connect(str(path), timeout=2.0)
                try:
                    mode_row = conn.execute("PRAGMA journal_mode").fetchone()
                    timeout_row = conn.execute("PRAGMA busy_timeout").fetchone()
                    mode = str(mode_row[0] if mode_row else "").lower()
                    timeout_ms = self._int(timeout_row[0] if timeout_row else 0)
                finally:
                    conn.close()
                level = "ok" if mode == "wal" and timeout_ms >= 1000 else "warn"
                text = f"journal_mode={mode or '-'}，busy_timeout={timeout_ms}ms"
                return {"path": str(path), "name": path.name, "level": level, "text": text, "journal_mode": mode, "busy_timeout_ms": timeout_ms}
            except Exception as exc:
                return {"path": str(path), "name": path.name, "level": "error", "text": self._single_line(exc, 180)}

        for path in paths[:12]:
            items.append(await self._to_thread_sqlite_inspect(inspect, path))
        return {
            "items": items,
            "ok": sum(1 for item in items if item.get("level") == "ok"),
            "warn": sum(1 for item in items if item.get("level") == "warn"),
            "error": sum(1 for item in items if item.get("level") == "error"),
        }

    def _user_summary(self, user_id: str, user: dict[str, Any]) -> dict[str, Any]:
        last_seen = user.get("last_seen", 0)
        last_sent = user.get("last_sent", 0)
        user_id_text = str(user_id)
        is_qq_user = user_id_text.isdigit()
        umo = str(
            user.get("umo")
            or user.get("bound_delivery_umo")
            or user.get("preferred_delivery_umo")
            or user.get("last_inbound_umo", "")
            or ""
        )
        source = self._single_line(umo.split(":", 1)[0], 40) if ":" in umo else ""
        platform_profile_getter = getattr(self.plugin, "_platform_profile", None)
        platform_profile = platform_profile_getter(umo=umo) if callable(platform_profile_getter) else {}
        platform_kind = self._single_line((platform_profile or {}).get("kind"), 40) or ("onebot" if is_qq_user else "generic")
        nickname = self._single_line(user.get("nickname"), 40)
        generic_names = {"用户", "主人", "主要用户", "默认用户", "临时会话"}
        profile_origin = self._single_line(user.get("profile_origin"), 40)
        capabilities = user.get("unified_profile_capabilities") if isinstance(user.get("unified_profile_capabilities"), dict) else {}
        grant_source = self._single_line(capabilities.get("grant_source"), 60)
        group_observation_identity = bool(
            profile_origin == "group_observation"
            or user.get("observation_only")
            or (
                grant_source == "legacy_effective_migration"
                and not umo
                and not self._float(last_seen)
            )
        )
        directory_scope = "group" if group_observation_identity else "private"
        if is_qq_user:
            display_name = nickname if nickname and nickname not in generic_names else user_id_text
            if group_observation_identity:
                resolver = getattr(self.plugin, "_group_member_identity_name", None)
                if callable(resolver):
                    try:
                        resolved = self._single_line(resolver(user_id_text, nickname, limit=40), 40)
                        if resolved and resolved != user_id_text:
                            display_name = resolved
                    except Exception:
                        pass
        elif platform_kind == "qq_official":
            display_name = nickname if nickname and nickname not in generic_names else f"QQ 官方 · {user_id_text[:8]}"
        else:
            display_name = f"临时会话 · {user_id_text[:8]}"
        relationship_stage = ""
        profile_getter = getattr(self.plugin, "_relationship_profile", None)
        if not relationship_stage and callable(profile_getter):
            try:
                profile = profile_getter(user)
                if isinstance(profile, dict):
                    relationship_stage = self._single_line(profile.get("level"), 12)
            except Exception:
                relationship_stage = ""
        if relationship_stage not in {"亲近", "熟悉", "陌生"}:
            score = self._int(user.get("relationship_score"))
            inbound_count = self._int(user.get("inbound_count"))
            proactive_count = self._int(user.get("proactive_sent_count"))
            reply_count = self._int(user.get("reply_count"))
            reply_rate = reply_count / proactive_count if proactive_count > 0 else 0.0
            if score >= 16 and reply_rate >= 0.35:
                relationship_stage = "亲近"
            elif score >= 3 or inbound_count >= 1 or reply_rate >= 0.2:
                relationship_stage = "熟悉"
            else:
                relationship_stage = "陌生"
        role = self.plugin._private_user_role(user, user_id_text) if hasattr(self.plugin, "_private_user_role") else ""
        role_labeler = getattr(self.plugin, "_private_user_role_label", None)
        role_label = role_labeler(role) if callable(role_labeler) else ("主要用户" if role == "owner" else "次要用户")
        relationship_panel = self._relationship_panel(
            user_id_text,
            user,
            relationship_stage=relationship_stage,
        )
        relationship_mode = relationship_panel["relationship_mode"]
        relationship_intimacy = relationship_panel["relationship_intimacy"]
        current_interaction = relationship_panel["current_interaction"]
        if relationship_mode == "owner_exclusive":
            relationship_stage = self._single_line(getattr(self.plugin, "owner_exclusive_label", "专属联结"), 20) or "专属联结"
            relationship_intimacy["owner_exclusive"] = {
                "label": relationship_stage,
                "tone": self._single_line(getattr(self.plugin, "owner_exclusive_tone", "温暖、亲近、稳定"), 120),
                "fixed": True,
            }
        else:
            relationship_stage = self._single_line((relationship_intimacy.get("phase") or {}).get("label"), 20) or relationship_stage
        exclusive_prompt_status_getter = getattr(
            self.plugin,
            "_owner_exclusive_relationship_prompt_status",
            None,
        )
        owner_exclusive_relationship_prompt = (
            exclusive_prompt_status_getter(user, stable_user_id=user_id_text)
            if callable(exclusive_prompt_status_getter)
            else {
                "persona_id": "",
                "persona_label": "当前人格",
                "stable_user_id": user_id_text,
                "text": "",
                "configured": False,
                "eligible": role == "owner",
                "active": False,
                "relationship_mode": relationship_mode,
                "max_chars": 2400,
            }
        )
        slowdown_count_getter = getattr(self.plugin, "_unanswered_slowdown_count", None)
        multiplier_getter = getattr(self.plugin, "_unanswered_interval_multiplier", None)
        unanswered_slowdown_count = 0
        unanswered_interval_multiplier = 1.0
        if callable(slowdown_count_getter):
            try:
                unanswered_slowdown_count = max(0, self._int(slowdown_count_getter(user)))
            except Exception:
                unanswered_slowdown_count = 0
        if callable(multiplier_getter):
            try:
                unanswered_interval_multiplier = max(1.0, float(multiplier_getter(user)))
            except Exception:
                unanswered_interval_multiplier = 1.0
        unanswered_slowdown_text = (
            f"连续未回应 {self._int(user.get('ignored_streak'))} 次，最小主动间隔 ×{unanswered_interval_multiplier:.2f}"
            if unanswered_slowdown_count > 0
            else "未触发"
        )
        soft_daily_target = 0.0
        soft_target_getter = getattr(self.plugin, "_soft_daily_target", None)
        if callable(soft_target_getter):
            try:
                soft_daily_target = max(0.0, float(soft_target_getter(user)))
            except Exception:
                soft_daily_target = 0.0
        quota_policy_getter = getattr(self.plugin, "_proactive_quota_policy", None)
        quota_policy = quota_policy_getter(user) if callable(quota_policy_getter) else {}
        pending_emotion_judgement = self._emotion_pending_judgement_summary(user.get("pending_emotion_judgement"))
        last_emotion_judgement = self._emotion_last_judgement_summary(user.get("last_emotion_judgement"))
        last_emotion_judgement_error = self._emotion_judgement_error_summary(user.get("last_emotion_judgement_error"))
        capability_getter = getattr(self.plugin, "_req036_capability_summary_for_user", None)
        capability_summary = capability_getter(user) if callable(capability_getter) else {
            "private_companion_enabled": True,
            "proactive_private_enabled": False,
            "effective_proactive_private_enabled": False,
            "portrait_mode": "disabled",
            "portrait_learning_enabled": False,
            "portrait_usage_enabled": False,
            "grant_source": "legacy",
            "blocked_reasons": [],
        }
        return {
            "user_id": user_id_text,
            "display_name": display_name,
            "directory_scope": directory_scope,
            "observation_only": group_observation_identity,
            "is_qq_user": is_qq_user,
            "platform_kind": platform_kind,
            "platform_label": self._single_line((platform_profile or {}).get("label"), 60),
            "identity_label": self._single_line((platform_profile or {}).get("identity_label"), 60),
            "stable_platform_identity": bool(platform_kind == "qq_official" or is_qq_user),
            "source": source,
            "enabled": True,
            "private_companion_enabled": True,
            "proactive_private_enabled": bool(capability_summary.get("proactive_private_enabled")),
            "portrait_mode": self._single_line(capability_summary.get("portrait_mode"), 40) or "disabled",
            "portrait_learning_enabled": bool(capability_summary.get("portrait_learning_enabled")),
            "portrait_usage_enabled": bool(capability_summary.get("portrait_usage_enabled")),
            "portrait_mode_override": self._single_line(
                (user.get("unified_profile_capabilities") if isinstance(user.get("unified_profile_capabilities"), dict) else {}).get("portrait_mode_override"),
                40,
            ) or "follow_global",
            "capability_summary": capability_summary,
            "unified_person_id": self._single_line(user.get("unified_person_id"), 80),
            "proactive_contact_enabled": bool(capability_summary.get("effective_proactive_private_enabled")),
            "relationship_role": role,
            "relationship_role_label": role_label,
            "nickname": user.get("nickname", ""),
            "style": user.get("style", ""),
            "umo": user.get("umo", ""),
            "delivery_bound": bool(self._single_line(user.get("bound_delivery_umo"), 240)),
            "bound_delivery_umo": self._single_line(user.get("bound_delivery_umo"), 240),
            "last_seen_ts": last_seen,
            "last_seen": self.plugin._format_timestamp_elapsed(last_seen),
            "last_sent_ts": last_sent,
            "last_sent": self.plugin._format_timestamp_elapsed(last_sent),
            "sent_today": user.get("sent_today", 0),
            "last_proactive_skip_ts": self._float(user.get("last_proactive_skip_at")),
            "last_proactive_skip": self.plugin._format_timestamp_elapsed(user.get("last_proactive_skip_at", 0)),
            "last_proactive_skip_reason": self._single_line(user.get("last_proactive_skip_reason"), 120),
            "last_proactive_skip_prefix": self._single_line(user.get("last_proactive_skip_prefix"), 20),
            "effective_daily_limit": (
                self.plugin._effective_user_daily_limit(user)
                if hasattr(self.plugin, "_effective_user_daily_limit")
                else getattr(self.plugin, "max_daily_messages", 0)
            ),
            "effective_daily_limit_text": (
                self.plugin._format_proactive_daily_limit(self.plugin._effective_user_daily_limit(user))
                if hasattr(self.plugin, "_format_proactive_daily_limit") and hasattr(self.plugin, "_effective_user_daily_limit")
                else str(getattr(self.plugin, "max_daily_messages", 0))
            ),
            "effective_daily_limit_unlimited": (
                self.plugin._proactive_daily_limit_is_unlimited(self.plugin._effective_user_daily_limit(user))
                if hasattr(self.plugin, "_proactive_daily_limit_is_unlimited") and hasattr(self.plugin, "_effective_user_daily_limit")
                else False
            ),
            "soft_daily_target": round(soft_daily_target, 2),
            "proactive_quota_tier": self._int(quota_policy.get("tier")),
            "proactive_quota_tier_label": self._single_line(quota_policy.get("label"), 40),
            "unanswered_slowdown_count": unanswered_slowdown_count,
            "unanswered_interval_multiplier": unanswered_interval_multiplier,
            "unanswered_slowdown_text": unanswered_slowdown_text,
            "effective_idle_minutes": (
                self.plugin._effective_user_idle_minutes(user)
                if hasattr(self.plugin, "_effective_user_idle_minutes")
                else getattr(self.plugin, "idle_minutes", 0)
            ),
            "effective_min_interval_minutes": (
                self.plugin._effective_user_min_interval_minutes(user)
                if hasattr(self.plugin, "_effective_user_min_interval_minutes")
                else getattr(self.plugin, "min_interval_minutes", 0)
            ),
            "effective_screen_peek_daily_limit": (
                self.plugin._effective_user_screen_peek_daily_limit(user)
                if hasattr(self.plugin, "_effective_user_screen_peek_daily_limit")
                else getattr(self.plugin, "screen_peek_max_daily", 0)
            ),
            "effective_photo_daily_limit": (
                self.plugin._effective_user_photo_daily_limit(user)
                if hasattr(self.plugin, "_effective_user_photo_daily_limit")
                else getattr(self.plugin, "photo_action_max_daily", 0)
            ),
            "proactive_daily_limit": user.get("proactive_daily_limit", -1),
            "proactive_idle_minutes": user.get("proactive_idle_minutes", -1),
            "proactive_min_interval_minutes": user.get("proactive_min_interval_minutes", -1),
            "photo_daily_limit": user.get("photo_daily_limit", -1),
            "screen_peek_daily_limit": user.get("screen_peek_daily_limit", -1),
            "poke_daily_limit": user.get("poke_daily_limit", -1),
            "proactive_boundary_note": user.get("proactive_boundary_note", ""),
            "inbound_count": user.get("inbound_count", 0),
            "reply_count": user.get("reply_count", 0),
            "proactive_sent_count": user.get("proactive_sent_count", 0),
            "relationship_score": user.get("relationship_score", 0),
            "relationship_stage": relationship_stage,
            "relationship_mode": relationship_mode,
            "relationship_mode_label": "专属关系" if relationship_mode == "owner_exclusive" else "普通阶段",
            "owner_exclusive_relationship_prompt": owner_exclusive_relationship_prompt,
            "relationship_intimacy": relationship_intimacy,
            "relationship_ledger": relationship_panel["relationship_changes"],
            "current_interaction": current_interaction,
            "expression_decision": relationship_panel["expression_decision"],
            "pending_emotion_judgement": pending_emotion_judgement,
            "last_emotion_judgement": last_emotion_judgement,
            "last_emotion_judgement_error": last_emotion_judgement_error,
            "planned_reason": user.get("planned_proactive_reason", ""),
            "planned_action": user.get("planned_proactive_action", ""),
            "next_proactive_ts": user.get("next_proactive_at", 0),
            "next_proactive": self.plugin._format_next_proactive(user),
            "memory_items": self._memory_item_count(user.get("companion_memory")),
            "dialogue_episode_count": len(user.get("dialogue_episodes") or []),
            "open_loop_count": len(user.get("open_loops") or []),
            "habit_count": len(self._behavior_habit_summary(user).get("items", [])),
            "alias_user_ids": [
                self._single_line(item, 80)
                for item in (user.get("alias_user_ids") if isinstance(user.get("alias_user_ids"), list) else [])
                if self._single_line(item, 80)
            ],
        }

    def _emotion_relationship_state_summary(self, state: Any) -> dict[str, Any]:
        if not isinstance(state, dict):
            return {}
        scalar_limits = {
            "mode": 24,
            "stage": 24,
            "last_intent": 40,
            "last_emotion": 40,
            "last_emotion_event": 40,
            "last_emotion_reason": 120,
            "last_emotion_target": 40,
            "last_emotion_rule": 60,
            "last_hurt_reason": 120,
            "last_hurt_text": 180,
            "updated_at": 40,
        }
        numeric_keys = {
            "mood_score",
            "mood_updated_ts",
            "hurt_until",
            "emotion_min_until",
            "silence_turns",
            "last_pressure",
            "last_emotion_intensity",
            "last_intent_confidence",
            "last_emotion_confidence",
        }
        summary: dict[str, Any] = {}
        for key, limit in scalar_limits.items():
            if key in state:
                summary[key] = self._single_line(state.get(key), limit)
        for key in numeric_keys:
            if key in state:
                summary[key] = state.get(key)
        dims = state.get("emotion_dimensions")
        if isinstance(dims, dict):
            summary["emotion_dimensions"] = {
                key: dims.get(key)
                for key in ("pleasantness", "tension", "arousal", "certainty")
                if key in dims
            }
        plutchik_emotions = state.get("plutchik_emotions")
        if isinstance(plutchik_emotions, dict):
            summary["plutchik_emotions"] = {
                key: plutchik_emotions.get(key)
                for key in ("joy", "trust", "fear", "surprise", "sadness", "disgust", "anger", "anticipation")
                if key in plutchik_emotions
            }
        plutchik = state.get("plutchik_profile")
        if isinstance(plutchik, dict):
            profile = {
                key: plutchik.get(key)
                for key in (
                    "dominant",
                    "dominant_label",
                    "dominant_value",
                    "blend_key",
                    "blend_label",
                    "blend_value",
                    "updated_at",
                )
                if key in plutchik
            }
            active = plutchik.get("active")
            if isinstance(active, list):
                profile["active"] = [dict(item) for item in active[:8] if isinstance(item, dict)]
            summary["plutchik_profile"] = profile
        regulation = state.get("emotion_regulation")
        if isinstance(regulation, dict):
            reg = {
                key: regulation.get(key)
                for key in ("strategy", "strategy_label", "intensity", "reason", "updated_at")
                if key in regulation
            }
            stack = regulation.get("strategy_stack")
            if isinstance(stack, list):
                reg["strategy_stack"] = [dict(item) for item in stack[:5] if isinstance(item, dict)]
            summary["emotion_regulation"] = reg
        return summary

    def _emotion_pending_judgement_summary(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        text = self._single_line(value.get("text"), 180)
        created_at = self._float(value.get("created_at"))
        local = value.get("local") if isinstance(value.get("local"), dict) else {}
        result: dict[str, Any] = {
            "text": text,
            "created_at": created_at,
            "created_at_text": self.plugin._format_timestamp_elapsed(created_at) if created_at else "",
        }
        if local:
            result["local"] = {
                "emotion_event": self._single_line(local.get("emotion_event"), 40),
                "emotion_target": self._single_line(local.get("emotion_target"), 40),
                "emotion_intensity": local.get("emotion_intensity"),
                "emotion_confidence": local.get("emotion_confidence"),
                "emotion_reason": self._single_line(local.get("emotion_reason"), 100),
            }
        return result if text or created_at or local else {}

    def _emotion_last_judgement_summary(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        status = self._single_line(value.get("status"), 24)
        if status not in {"applied", "kept_local", "failed"}:
            return {}
        return {
            "status": status,
            "outcome": self._single_line(value.get("outcome"), 32),
            "event": self._single_line(value.get("event"), 32),
            "target": self._single_line(value.get("target"), 24),
            "intensity": self._int(value.get("intensity")),
            "confidence": round(max(0.0, min(1.0, self._float(value.get("confidence")))), 2),
            "reason": self._single_line(value.get("reason"), 100),
            "reviewed_at": self._single_line(value.get("reviewed_at"), 32),
        }

    def _emotion_judgement_error_summary(self, value: Any) -> str:
        text = self._single_line(value, 160)
        if not text:
            return ""
        if text.startswith("{") or '"event"' in text or '"confidence"' in text:
            return ""
        labels = {
            "request_failed": "模型请求失败",
            "empty_response": "模型返回为空",
            "invalid_response": "模型返回格式无效",
            "empty_or_invalid": "模型返回为空或格式无效",
        }
        return labels.get(text, "模型请求或返回格式异常")

    def _behavior_habit_summary(self, user: dict[str, Any]) -> dict[str, Any]:
        formatter = getattr(self.plugin, "_qualified_user_behavior_habits", None)
        if callable(formatter):
            try:
                items = formatter(user)
            except Exception:
                items = []
        else:
            raw = user.get("behavior_habits") if isinstance(user.get("behavior_habits"), dict) else {}
            patterns = raw.get("patterns") if isinstance(raw.get("patterns"), list) else []
            items = [item for item in patterns if isinstance(item, dict)]
        normalized = []
        for item in items[:12]:
            if not isinstance(item, dict):
                continue
            normalized.append(
                {
                    "bucket": self._single_line(item.get("bucket"), 12),
                    "category": self._single_line(item.get("category"), 20),
                    "topic": self._single_line(item.get("topic"), 80),
                    "count": self._int(item.get("count")),
                    "avg_time": self.plugin._format_user_habit_time(item.get("avg_minute")) if hasattr(self.plugin, "_format_user_habit_time") else "",
                    "last_seen": self.plugin._format_timestamp_elapsed(item.get("last_seen_ts", 0)),
                    "last_seen_text": self._single_line(item.get("last_seen_text"), 100),
                }
            )
        raw_habits = user.get("behavior_habits") if isinstance(user.get("behavior_habits"), dict) else {}
        return {
            "enabled": bool(getattr(self.plugin, "enable_user_habit_learning", False)),
            "updated_at": self._single_line(raw_habits.get("updated_at"), 30) if isinstance(raw_habits, dict) else "",
            "items": normalized,
        }

    def _group_summary(self, group_id: str, group: dict[str, Any]) -> dict[str, Any]:
        atmosphere = group.get("atmosphere") if isinstance(group.get("atmosphere"), dict) else {}
        slang_terms = group.get("slang_terms") if isinstance(group.get("slang_terms"), list) else []
        slang_meanings = group.get("slang_meanings") if isinstance(group.get("slang_meanings"), dict) else {}
        members = group.get("members") if isinstance(group.get("members"), dict) else {}
        group_for_filter = group
        group_id_text = str(group_id)
        manual_group_name = self._single_line(group.get("manual_group_name"), 80)
        group_name = self._single_line(
            manual_group_name or group.get("name") or group.get("group_name") or group.get("display_name"),
            80,
        )
        if group_name == group_id_text:
            group_name = ""
        cleaner = getattr(self.plugin, "_cleanup_group_slang_terms", None)
        if callable(cleaner):
            try:
                group_for_filter = {
                    "slang_terms": [dict(item) if isinstance(item, dict) else item for item in slang_terms],
                    "slang_meanings": {str(key): dict(value) if isinstance(value, dict) else value for key, value in slang_meanings.items()},
                    "members": {
                        str(user_id): {
                            key: member.get(key)
                            for key in ("name", "identity_name", "display_name", "nickname", "card")
                            if isinstance(member, dict) and key in member
                        }
                        for user_id, member in members.items()
                        if isinstance(member, dict)
                    },
                }
                if cleaner(group_for_filter):
                    slang_terms = group_for_filter.get("slang_terms") if isinstance(group_for_filter.get("slang_terms"), list) else []
            except Exception:
                pass
        promoter = getattr(self.plugin, "_group_slang_term_is_promoted", None)
        if callable(promoter):
            visible_terms: list[Any] = []
            for item in slang_terms:
                try:
                    if not promoter(group_for_filter, item):
                        continue
                except Exception:
                    continue
                if isinstance(item, dict):
                    visible_terms.append({**item, "promoted": True})
                else:
                    visible_terms.append(item)
            slang_terms = visible_terms
        identity_count = sum(1 for item in members.values() if isinstance(item, dict) and item.get("identity_known"))
        safety_getter = getattr(self.plugin, "_group_member_safety_compact_summary", None)
        member_safety = safety_getter(group) if callable(safety_getter) else {}
        wakeup_logs = group.get("group_wakeup_logs") if isinstance(group.get("group_wakeup_logs"), list) else []
        last_wakeup = group.get("last_group_wakeup") if isinstance(group.get("last_group_wakeup"), dict) else {}
        last_interjection = self._sanitize_last_bot_interjection(group.get("last_bot_interjection"))
        return {
            "group_id": group_id_text,
            "name": group_name,
            "group_name": group_name,
            "display_name": group_name or "未命名群聊",
            "manual_group_name": manual_group_name,
            "group_name_source": "manual" if manual_group_name else str(group.get("group_name_source") or "auto"),
            "global_enabled": bool(getattr(self.plugin, "enable_group_companion", False)),
            "enabled": bool(group.get("enabled", True)),
            "allowed_by_mode": self.plugin._group_allowed_by_access_mode(group_id_text),
            "message_count": group.get("message_count", 0),
            "last_seen_ts": group.get("last_seen", 0),
            "last_seen": self.plugin._format_timestamp_elapsed(group.get("last_seen", 0)),
            "member_count": len(members),
            "recognized_member_count": identity_count,
            "member_safety_blocked_count": _safe_int(member_safety.get("blocked_count"), 0),
            "member_safety_watching_count": _safe_int(member_safety.get("watching_count"), 0),
            "recent_message_count": len(group.get("recent_messages") or []),
            "recent_bot_reply_count": len(group.get("recent_bot_replies") or []),
            "slang_count": len(slang_terms),
            "slang_meaning_count": len(slang_meanings),
            "slang_terms": slang_terms[:16],
            "topic_count": len(group.get("topic_threads") or []),
            "episode_count": len(group.get("group_episodes") or []),
            "relationship_edge_count": len(group.get("relationship_edges") or {}),
            "interject_today": group.get("interject_today", 0),
            "effective_interject_max_daily": (
                self.plugin._effective_group_interject_max_daily()
                if hasattr(self.plugin, "_effective_group_interject_max_daily")
                else getattr(self.plugin, "group_interject_max_daily", 0)
            ),
            "effective_interject_min_interval_minutes": (
                self.plugin._effective_group_interject_min_interval_minutes()
                if hasattr(self.plugin, "_effective_group_interject_min_interval_minutes")
                else getattr(self.plugin, "group_interject_min_interval_minutes", 0)
            ),
            "last_interject": self.plugin._format_timestamp_elapsed(group.get("last_interject_at", 0)),
            "last_bot_interjection": last_interjection,
            "wakeup_log_count": len(wakeup_logs),
            "wakeup_fatigue": self._group_wakeup_runtime(group),
            "last_group_wakeup": {
                "time": self.plugin._format_timestamp_elapsed(last_wakeup.get("ts", 0)),
                "type": self._single_line(last_wakeup.get("type"), 40),
                "word": self._single_line(last_wakeup.get("word"), 60),
                "strength_label": self._single_line(last_wakeup.get("strength_label"), 24),
                "score": self._int(last_wakeup.get("score")),
                "threshold": self._int(last_wakeup.get("threshold")),
                "intensity": self._single_line(last_wakeup.get("intensity"), 20),
                "help_type": self._single_line(last_wakeup.get("help_type"), 30),
                "reason": self._single_line(last_wakeup.get("reason"), 80),
                "reason_label": self._single_line(last_wakeup.get("reason_label"), 80),
                "reason_detail": self._single_line(last_wakeup.get("reason_detail"), 180),
                "sender_name": self._single_line(last_wakeup.get("sender_name"), 40),
                "text": self._display_message_text(last_wakeup.get("text"), 120),
            } if last_wakeup else {},
            "atmosphere": {
                "mood": atmosphere.get("mood", ""),
                "pace": atmosphere.get("pace", ""),
                "heat": atmosphere.get("heat") or atmosphere.get("pace", ""),
                "last_summary": atmosphere.get("summary") or atmosphere.get("last_summary", ""),
                "recent_count": atmosphere.get("recent_count", 0),
                "active_speakers": atmosphere.get("active_speakers", 0),
                "updated_at": atmosphere.get("updated_at", ""),
            },
        }

    def _skill_growth_summary(self, data: dict[str, Any]) -> dict[str, Any]:
        state = data.get("skill_growth") if isinstance(data.get("skill_growth"), dict) else {}
        skills = state.get("skills") if isinstance(state.get("skills"), dict) else {}
        items: list[dict[str, Any]] = []
        for raw in skills.values():
            if not isinstance(raw, dict):
                continue
            level = self._int(raw.get("level")) or 1
            exp = self._float(raw.get("exp"))
            next_exp = self.plugin._skill_next_exp(level) if hasattr(self.plugin, "_skill_next_exp") else None
            prev_exp = {1: 0, 2: 100, 3: 260, 4: 520, 5: 900, 6: 1400}.get(level, 0)
            if next_exp:
                progress = max(0, min(100, int(((exp - prev_exp) / max(1, next_exp - prev_exp)) * 100)))
            else:
                progress = 100
            logs = raw.get("recent_logs") if isinstance(raw.get("recent_logs"), list) else []
            keywords = raw.get("keywords") if isinstance(raw.get("keywords"), list) else []
            aliases = raw.get("aliases") if isinstance(raw.get("aliases"), list) else []
            items.append(
                {
                    "id": self._single_line(raw.get("id"), 32),
                    "name": self._single_line(raw.get("name"), 32),
                    "category": self._single_line(raw.get("category"), 24),
                    "keywords": [self._single_line(item, 24) for item in keywords if self._single_line(item, 24)][:16],
                    "aliases": [self._single_line(item, 24) for item in aliases if self._single_line(item, 24)][:12],
                    "hidden": bool(raw.get("hidden")),
                    "frozen": bool(raw.get("frozen")),
                    "level": level,
                    "level_title": self.plugin._skill_level_title(level) if hasattr(self.plugin, "_skill_level_title") else self._single_line(raw.get("level_title"), 24),
                    "description": self.plugin._skill_level_description(level) if hasattr(self.plugin, "_skill_level_description") else "",
                    "exp": round(exp, 2),
                    "next_exp": next_exp,
                    "progress": progress,
                    "training_count": self._int(raw.get("training_count")),
                    "last_trained": self.plugin._format_timestamp_elapsed(raw.get("last_trained_ts", 0)),
                    "recent_logs": [
                        {
                            "activity": self._single_line(log.get("activity"), 80),
                            "exp": self._float(log.get("exp")),
                            "time": self.plugin._format_timestamp_elapsed(log.get("ts", 0)),
                            "level_up": bool(log.get("level_up")),
                        }
                        for log in logs[-4:]
                        if isinstance(log, dict)
                    ],
                }
            )
        items.sort(key=lambda item: (bool(item.get("hidden")), -item["level"], -item["exp"], -item["training_count"], item.get("category") or "", item.get("name") or ""))
        return {
            "enabled": bool(getattr(self.plugin, "enable_skill_growth_simulation", False)),
            "rate": float(getattr(self.plugin, "skill_growth_rate", 1.0) or 1.0),
            "passive_injection": bool(getattr(self.plugin, "enable_skill_growth_passive_injection", False)),
            "schedule_influence": bool(getattr(self.plugin, "enable_skill_growth_schedule_influence", False)),
            "schedule_influence_strength": float(getattr(self.plugin, "skill_growth_schedule_influence_strength", 0.35) or 0.0),
            "updated": self.plugin._format_timestamp_elapsed(state.get("updated_ts", 0)),
            "skill_count": len(items),
            "hidden_count": sum(1 for item in items if item.get("hidden")),
            "frozen_count": sum(1 for item in items if item.get("frozen")),
            "items": items[:120],
        }

    def _food_menu_summary(self, data: dict[str, Any]) -> dict[str, Any]:
        state = data.get("food_menu") if isinstance(data.get("food_menu"), dict) else {}
        raw_items = state.get("items") if isinstance(state.get("items"), list) else []
        type_label = getattr(self.plugin, "_food_menu_type_label", None)
        time_label = getattr(self.plugin, "_food_menu_time_label", None)

        def _list(value: Any, *, limit: int = 12, item_limit: int = 24) -> list[str]:
            raw = value if isinstance(value, list) else re.split(r"[,，、\n/|]+", str(value or ""))
            items: list[str] = []
            for part in raw:
                item = self._single_line(part, item_limit)
                if item and item not in items:
                    items.append(item)
            return items[:limit]

        items: list[dict[str, Any]] = []
        counts: dict[str, int] = {}
        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            name = self._single_line(raw.get("name"), 40)
            if not name:
                continue
            item_type = self._single_line(raw.get("type"), 20) or "dish"
            counts[item_type] = counts.get(item_type, 0) + 1
            times = _list(raw.get("times"), limit=5, item_limit=16)
            items.append(
                {
                    "id": self._single_line(raw.get("id"), 48),
                    "name": name,
                    "type": item_type,
                    "type_label": type_label(item_type) if callable(type_label) else item_type,
                    "category": self._single_line(raw.get("category"), 24),
                    "tags": _list(raw.get("tags"), limit=10, item_limit=16),
                    "times": times,
                    "time_labels": [time_label(value) if callable(time_label) else value for value in times],
                    "avoid": _list(raw.get("avoid"), limit=8, item_limit=24),
                    "aliases": _list(raw.get("aliases"), limit=10, item_limit=24),
                    "note": self._single_line(raw.get("note"), 100),
                    "favorite": bool(raw.get("favorite")),
                    "hidden": bool(raw.get("hidden")),
                    "use_count": self._int(raw.get("use_count")),
                    "last_used": self.plugin._format_timestamp_elapsed(raw.get("last_used_at", 0)),
                    "last_recommended": self.plugin._format_timestamp_elapsed(raw.get("last_recommended_at", 0)),
                    "updated": self.plugin._format_timestamp_elapsed(raw.get("updated_ts", 0)),
                }
            )
        items.sort(key=lambda item: (bool(item.get("hidden")), not bool(item.get("favorite")), item.get("type_label", ""), item.get("name", "")))
        return {
            "items": items[:160],
            "total": len(items),
            "visible_count": sum(1 for item in items if not item.get("hidden")),
            "favorite_count": sum(1 for item in items if item.get("favorite")),
            "hidden_count": sum(1 for item in items if item.get("hidden")),
            "counts": counts,
            "updated": self.plugin._format_timestamp_elapsed(state.get("updated_ts", 0)),
        }

    def _external_ability_summary(self, data: dict[str, Any]) -> dict[str, Any]:
        runtime_getter = getattr(self.plugin, "external_proactive_abilities", None)
        if callable(runtime_getter):
            raw_items = runtime_getter()
        else:
            store = data.get("external_proactive_abilities") if isinstance(data.get("external_proactive_abilities"), dict) else {}
            raw_items = list(store.values()) if isinstance(store, dict) else []
        items: list[dict[str, Any]] = []
        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            config = raw.get("config") if isinstance(raw.get("config"), dict) else {}
            schema = raw.get("config_schema") if isinstance(raw.get("config_schema"), dict) else {}
            items.append(
                {
                    "name": self._single_line(raw.get("name"), 64),
                    "module": self._single_line(raw.get("module"), 24) or "外部主动能力",
                    "label": self._single_line(raw.get("label"), 32) or self._single_line(raw.get("name"), 64),
                    "description": self._single_line(raw.get("description"), 180),
                    "when": self._single_line(raw.get("when"), 140),
                    "use_for": self._single_line(raw.get("use_for"), 140),
                    "avoid": self._single_line(raw.get("avoid"), 140),
                    "enabled": bool(raw.get("enabled")),
                    "available": bool(raw.get("available")),
                    "registered": bool(raw.get("registered")),
                    "share_probability": max(0.0, min(1.0, self._float(raw.get("share_probability")))),
                    "min_interval_hours": max(0.0, self._float(raw.get("min_interval_hours"))),
                    "config": config,
                    "config_schema": schema,
                    "last_executed": self.plugin._format_timestamp_elapsed(raw.get("last_executed_ts", 0)),
                    "last_status": self._single_line(raw.get("last_status"), 160),
                    "last_summary": self._single_line(raw.get("last_summary"), 160),
                    "success_count": self._int(raw.get("success_count")),
                    "failure_count": self._int(raw.get("failure_count")),
                    "updated": self.plugin._format_timestamp_elapsed(raw.get("updated_ts", 0)),
                }
            )
        items.sort(key=lambda item: (not item["enabled"], not item["available"], item["module"], item["label"]))
        return {
            "total": len(items),
            "enabled_count": sum(1 for item in items if item["enabled"]),
            "available_count": sum(1 for item in items if item["available"]),
            "items": items,
        }

    def _body_monitor_integration_summary(self) -> dict[str, Any]:
        enabled = bool(getattr(self.plugin, "enable_body_monitor_integration", False))
        installed = False
        detector = getattr(self.plugin, "_integrated_plugin_installed", None)
        if callable(detector):
            try:
                installed = bool(detector("astrbot_plugin_body_monitor"))
            except Exception:
                installed = False

        raw: dict[str, Any] = {}
        status_getter = getattr(self.plugin, "_body_monitor_integration_status_view", None)
        if callable(status_getter):
            try:
                value = status_getter()
                raw = value if isinstance(value, dict) else {}
            except Exception as exc:
                raw = {"state": "error", "last_error": exc}

        if "installed" in raw:
            installed = bool(raw.get("installed"))
        elif raw.get("available") is True:
            installed = True
        state = self._body_monitor_status_state(raw, enabled=enabled, installed=installed)
        state_text = {
            "disabled": "联动已关闭",
            "not_installed": "未安装 Body Monitor",
            "incompatible": "接口版本不兼容",
            "initializing": "正在初始化",
            "connected": "已连接",
            "error": "连接异常",
        }[state]

        last_pull_at = self._float(
            raw.get("last_pull_at")
            or raw.get("last_pull_ts")
            or raw.get("last_polled_at")
        )
        last_pull_text = self._single_line(raw.get("last_pull_text"), 80)
        formatter = getattr(self.plugin, "_format_timestamp_elapsed", None)
        if not last_pull_text and last_pull_at > 0 and callable(formatter):
            try:
                last_pull_text = self._single_line(formatter(last_pull_at), 80)
            except Exception:
                last_pull_text = ""

        raw_batch = raw.get("last_batch")
        if not isinstance(raw_batch, dict):
            raw_batch = raw.get("batch") if isinstance(raw.get("batch"), dict) else {}
        batch = {
            "received": max(0, self._int(raw_batch.get("received"))),
            "accepted": max(0, self._int(raw_batch.get("accepted") or raw_batch.get("queued"))),
            "skipped": max(0, self._int(raw_batch.get("skipped") or raw_batch.get("rejected"))),
            "duplicate": max(0, self._int(raw_batch.get("duplicate") or raw_batch.get("duplicates"))),
            "expired": max(0, self._int(raw_batch.get("expired"))),
        }
        api_version = self._int(
            raw.get("api_version")
            or raw.get("proactive_event_api_version")
            or raw.get("version")
        )
        supported_api_version = self._int(
            raw.get("supported_api_version")
            or raw.get("expected_api_version")
            or 1
        )
        return {
            "enabled": enabled,
            "installed": installed,
            "state": state,
            "state_text": state_text,
            "api_version": api_version,
            "supported_api_version": supported_api_version,
            "last_pull_at": last_pull_at,
            "last_pull_text": last_pull_text,
            "last_batch": batch,
            "error": self._body_monitor_status_error(raw.get("last_error") or raw.get("error")),
        }

    def _deepseek_peak_routing_summary(self) -> dict[str, Any]:
        getter = getattr(self.plugin, "_deepseek_peak_status", None)
        if not callable(getter):
            return {"enabled": False, "active": False, "configured": False}
        try:
            return dict(getter())
        except Exception as exc:
            return {
                "enabled": bool(getattr(self.plugin, "enable_deepseek_peak_replacement", False)),
                "active": False,
                "configured": False,
                "error": self._single_line(exc, 160),
            }

    def _message_debounce_summary(self, data: dict[str, Any]) -> dict[str, Any]:
        raw = data.get("smart_message_debounce")
        if not isinstance(raw, dict):
            raw = {}
        logs_raw = raw.get("recent_logs") if isinstance(raw.get("recent_logs"), list) else []
        examples_raw = raw.get("examples") if isinstance(raw.get("examples"), list) else []
        logs: list[dict[str, Any]] = []
        for item in logs_raw[-30:][::-1]:
            if not isinstance(item, dict):
                continue
            logs.append(
                {
                    "ts": self._float(item.get("ts")),
                    "time": self.plugin._format_timestamp_elapsed(self._float(item.get("ts"))) if self._float(item.get("ts")) else "",
                    "scope": self._single_line(item.get("scope"), 80),
                    "sender_id": self._single_line(item.get("sender_id"), 40),
                    "chat": self._single_line(item.get("chat"), 20),
                    "text": self._single_line(item.get("text"), 180),
                    "decision": self._single_line(item.get("decision"), 40),
                    "confidence": self._float(item.get("confidence")),
                    "reason": self._single_line(item.get("reason"), 120),
                    "wait_seconds": self._float(item.get("wait_seconds")),
                    "outcome": self._single_line(item.get("outcome"), 40),
                    "note": self._single_line(item.get("note"), 160),
                    "source": self._single_line(item.get("source"), 40),
                    "raw": self._single_line(item.get("raw"), 180),
                    "message_count": self._int(item.get("message_count")),
                }
            )
        examples: list[dict[str, Any]] = []
        for item in examples_raw[-10:][::-1]:
            if not isinstance(item, dict):
                continue
            messages = item.get("messages") if isinstance(item.get("messages"), list) else []
            examples.append(
                {
                    "time": self.plugin._format_timestamp_elapsed(self._float(item.get("ts"))) if self._float(item.get("ts")) else "",
                    "kind": self._single_line(item.get("kind"), 40),
                    "scope": self._single_line(item.get("scope"), 80),
                    "sender_id": self._single_line(item.get("sender_id"), 40),
                    "messages": [self._single_line(message, 120) for message in messages[:4]],
                    "previous_decision": self._single_line(item.get("previous_decision"), 40),
                    "note": self._single_line(item.get("note"), 120),
                }
            )
        return {
            "enabled": bool(getattr(self.plugin, "enable_message_debounce", False)),
            "smart_enabled": bool(getattr(self.plugin, "enable_smart_message_debounce", False)),
            "text_wait": self._float(getattr(self.plugin, "text_message_debounce_seconds", 0.0)),
            "max_wait": self._float(getattr(self.plugin, "text_message_debounce_max_wait_seconds", 0.0)),
            "max_merge": self._int(getattr(self.plugin, "message_debounce_max_merge_messages", 0)),
            "smart_wait": self._float(getattr(self.plugin, "smart_message_debounce_wait_seconds", 0.0)),
            "learning_window": self._float(getattr(self.plugin, "smart_message_debounce_learning_window_seconds", 0.0)),
            "provider_id": self._single_line(getattr(self.plugin, "smart_message_debounce_provider_id", ""), 160),
            "recent_logs": logs,
            "examples": examples,
        }

    def _livingmemory_summary(self) -> dict[str, Any]:
        try:
            available = bool(self.plugin._livingmemory_available())
        except Exception:
            available = False
        try:
            plugin_dir = str(self.plugin._livingmemory_plugin_dir())
        except Exception:
            plugin_dir = ""
        try:
            status = self.plugin._format_livingmemory_status()
        except Exception:
            status = "记忆插件：状态探测失败，已跳过协同。"
        # Detect "我会牢牢记住你" (RememberYou) bridge availability
        memory_companion_active = False
        memory_companion_display_name = ""
        memory_companion_presence: dict[str, Any] = {}
        try:
            bridge = self.plugin._memory_companion_bridge()  # type: ignore[attr-defined]
            if bridge is not None:
                memory_companion_active = True
                memory_companion_display_name = getattr(bridge, "display_name", "") or "我会牢牢记住你"
                if str(memory_companion_display_name).strip().lower() in {"rememberyou", "remember you", "memorycompanion", "memory companion", "astrbot_plugin_memory_companion", "astrbot_plugin_remember_you"}:
                    memory_companion_display_name = "我会牢牢记住你"
        except Exception:
            pass
        presence_getter = getattr(self.plugin, "_memory_companion_presence", None)
        if callable(presence_getter):
            try:
                presence = presence_getter()
                if isinstance(presence, dict):
                    memory_companion_presence = dict(presence)
            except Exception:
                memory_companion_presence = {}
        if not memory_companion_display_name and memory_companion_presence.get("detected"):
            memory_companion_display_name = self._single_line(
                memory_companion_presence.get("display_name"),
                80,
            ) or "我会牢牢记住你"
        configured_enabled = bool(getattr(self.plugin, "enable_livingmemory_integration", False))
        active_plugins: list[dict[str, Any]] = []
        if memory_companion_active:
            active_plugins.append(
                {
                    "type": "memory_companion",
                    "name": memory_companion_display_name or "我会牢牢记住你",
                    "display_name": memory_companion_display_name or "我会牢牢记住你",
                    "status": "桥接可用",
                }
            )
        if available:
            active_plugins.append(
                {
                    "type": "livingmemory",
                    "name": "LivingMemory",
                    "display_name": "LivingMemory",
                    "status": "工具式召回可用",
                    "tool_name": getattr(self.plugin, "livingmemory_tool_name", "") or "recall_long_term_memory",
                    "plugin_dir": plugin_dir,
                }
            )
        selected_plugin = active_plugins[0] if active_plugins else None
        conflict = bool(memory_companion_active and available)
        conflict_warning = (
            f"同时检测到{memory_companion_display_name or '我会牢牢记住你'}和 LivingMemory。建议只保留一个可协同记忆插件，避免重复召回、重复写入或提示词膨胀。"
            if conflict
            else ""
        )
        return {
            "enabled": bool(configured_enabled and active_plugins),
            "configured_enabled": configured_enabled,
            "compatible_available": bool(active_plugins),
            "available": available,
            "tool_name": getattr(self.plugin, "livingmemory_tool_name", ""),
            "plugin_dir": plugin_dir,
            "status": status,
            "memory_companion_active": memory_companion_active,
            "memory_companion_detected": bool(memory_companion_presence.get("detected")),
            "memory_companion_loaded": bool(memory_companion_presence.get("loaded")),
            "memory_companion_activated": bool(memory_companion_presence.get("activated")),
            "memory_companion_reason": self._single_line(memory_companion_presence.get("reason"), 80),
            "memory_companion_version": self._single_line(memory_companion_presence.get("version"), 40),
            "memory_companion_plugin_dir": self._single_line(memory_companion_presence.get("plugin_dir"), 260),
            "memory_companion_display_name": memory_companion_display_name,
            "active_plugins": active_plugins,
            "selected_plugin": selected_plugin,
            "selected_plugin_name": str((selected_plugin or {}).get("display_name") or ""),
            "conflict": conflict,
            "conflict_warning": conflict_warning,
        }

    def _screen_companion_summary(self, data: dict[str, Any]) -> dict[str, Any]:
        available = self._screen_companion_available()
        context = data.get("screen_diary_context") if isinstance(data.get("screen_diary_context"), dict) else {}
        return {
            "enabled": bool(available and getattr(self.plugin, "enable_yesterday_screen_diary_context", False)),
            "available": available,
            "source": context.get("source", ""),
            "source_date": context.get("source_date", ""),
            "context_available": bool(context.get("available")),
            "summary_chars": len(str(context.get("summary") or "")),
        }

    def _bilibili_summary(self, data: dict[str, Any]) -> dict[str, Any]:
        state = data.get("bilibili_integration") if isinstance(data.get("bilibili_integration"), dict) else {}
        try:
            available = bool(getattr(self.plugin, "_bilibili_available", lambda: False)())
        except Exception:
            available = False
        latest = None
        try:
            latest_getter = getattr(self.plugin, "_latest_bilibili_video_candidate", None)
            latest = latest_getter(include_memory_api=False) if callable(latest_getter) else None
        except Exception:
            latest = None
        try:
            watch_log = str(getattr(self.plugin, "_bilibili_watch_log_file", lambda: "")())
        except Exception:
            watch_log = ""
        try:
            memory_checker = getattr(self.plugin, "_bilibili_memory_api_available", None)
            if callable(memory_checker):
                try:
                    memory_api_available = bool(memory_checker(allow_probe=False))
                except TypeError:
                    memory_api_available = bool(memory_checker())
            else:
                memory_api_available = False
        except Exception:
            memory_api_available = False
        return {
            "enabled": bool(available and getattr(self.plugin, "enable_bilibili_integration", False)),
            "boredom_watch_enabled": bool(available and getattr(self.plugin, "enable_bilibili_boredom_watch", False)),
            "available": available,
            "memory_api_available": memory_api_available,
            "watch_log": watch_log,
            "last_boredom_watch_at": self.plugin._format_timestamp_elapsed(state.get("last_boredom_watch_at", 0)),
            "last_status": state.get("last_boredom_watch_status", ""),
            "latest_video": latest if isinstance(latest, dict) else {},
        }

    def _news_summary(self, data: dict[str, Any]) -> dict[str, Any]:
        state = data.get("news_integration") if isinstance(data.get("news_integration"), dict) else {}
        digest = state.get("last_digest") if isinstance(state.get("last_digest"), dict) else {}
        latest_items = state.get("latest_items") if isinstance(state.get("latest_items"), list) else []
        history = [
            item
            for item in self._browsing_history_entries(data)
            if item.get("source") == "news"
        ]
        ai_daily = state.get("ai_daily") if isinstance(state.get("ai_daily"), dict) else {}
        ai_digest = ai_daily.get("last_digest") if isinstance(ai_daily.get("last_digest"), dict) else {}
        ai_digest_items = ai_digest.get("items") if isinstance(ai_digest.get("items"), list) else []
        ai_digest_first_item = ai_digest_items[0] if ai_digest_items and isinstance(ai_digest_items[0], dict) else {}
        try:
            ai_text_chars = max(0, int(ai_daily.get("last_text_chars") or 0))
        except (TypeError, ValueError):
            ai_text_chars = 0
        if not ai_text_chars and ai_digest_first_item:
            ai_text_chars = len(str(ai_digest_first_item.get("article_text") or ""))
        try:
            ai_subtitle_chars = max(0, int(ai_daily.get("last_video_subtitle_chars") or 0))
        except (TypeError, ValueError):
            ai_subtitle_chars = 0
        if not ai_subtitle_chars and ai_digest_first_item:
            ai_subtitle_chars = len(str(ai_digest_first_item.get("video_subtitle_text") or ""))
        try:
            ai_video_context_chars = max(0, int(ai_daily.get("last_video_context_chars") or 0))
        except (TypeError, ValueError):
            ai_video_context_chars = 0
        if not ai_video_context_chars and ai_digest_first_item:
            ai_video_context_chars = len(str(ai_digest_first_item.get("video_context_text") or ""))
        try:
            ai_video_duration = max(0, int(ai_daily.get("last_video_duration") or ai_digest_first_item.get("video_duration") or 0))
        except (TypeError, ValueError):
            ai_video_duration = 0
        ai_video_tags_raw = ai_daily.get("last_video_tags") if isinstance(ai_daily.get("last_video_tags"), list) else ai_digest_first_item.get("video_tags")
        ai_video_tags = [
            self._single_line(tag, 40)
            for tag in ai_video_tags_raw
            if self._single_line(tag, 40)
        ] if isinstance(ai_video_tags_raw, list) else []
        ai_video_comments_raw = ai_daily.get("last_video_hot_comments") if isinstance(ai_daily.get("last_video_hot_comments"), list) else ai_digest_first_item.get("video_hot_comments")
        ai_video_comments = [
            self._single_line(comment, 120)
            for comment in ai_video_comments_raw
            if self._single_line(comment, 120)
        ] if isinstance(ai_video_comments_raw, list) else []
        try:
            source_count = len(getattr(self.plugin, "_news_source_items", lambda: [])())
        except Exception:
            source_count = 0
        return {
            "enabled": bool(getattr(self.plugin, "enable_news_integration", False)),
            "boredom_read_enabled": bool(getattr(self.plugin, "enable_news_boredom_read", False)),
            "daily_hot_enabled": bool(getattr(self.plugin, "enable_news_daily_hot_read", False)),
            "ai_daily_enabled": bool(getattr(self.plugin, "enable_ai_daily_watch", False)),
            "source_count": source_count,
            "history_count": len(history),
            "history": history,
            "last_read_at": self.plugin._format_timestamp_elapsed(state.get("last_read_at", 0)),
            "last_status": self._single_line(state.get("last_status"), 80),
            "ai_daily": {
                "status": self._single_line(ai_daily.get("status"), 80),
                "date": self._single_line(ai_daily.get("date"), 20),
                "last_checked_at": self.plugin._format_timestamp_elapsed(ai_daily.get("last_checked_at", 0)),
                "last_success_date": self._single_line(ai_daily.get("last_success_date"), 20),
                "last_source_name": self._single_line(ai_daily.get("last_source_name"), 40),
                "last_source_author": self._single_line(ai_daily.get("last_source_author"), 60),
                "last_source_mid": self._single_line(ai_daily.get("last_source_mid"), 40),
                "last_source_schedule": self._single_line(ai_daily.get("last_source_schedule"), 10),
                "last_video_title": self._single_line(ai_daily.get("last_video_title"), 120),
                "last_video_link": self._single_line(ai_daily.get("last_video_link"), 400),
                "last_video_owner_name": self._single_line(ai_daily.get("last_video_owner_name") or ai_digest_first_item.get("video_owner_name"), 80),
                "last_video_tname": self._single_line(ai_daily.get("last_video_tname") or ai_digest_first_item.get("video_tname"), 60),
                "last_video_duration": ai_video_duration,
                "last_video_context_chars": ai_video_context_chars,
                "last_video_tags": ai_video_tags[:10],
                "last_video_hot_comments": ai_video_comments[:5],
                "last_text_link": self._single_line(ai_daily.get("last_text_link"), 400),
                "last_text_readable": bool(ai_daily.get("last_text_readable")) if "last_text_readable" in ai_daily else bool(ai_digest_first_item.get("article_readable") and ai_digest_first_item.get("article_text")),
                "last_text_chars": ai_text_chars,
                "last_video_subtitle_readable": bool(ai_daily.get("last_video_subtitle_readable")) if "last_video_subtitle_readable" in ai_daily else bool(ai_digest_first_item.get("video_subtitle_readable") and ai_digest_first_item.get("video_subtitle_text")),
                "last_video_subtitle_chars": ai_subtitle_chars,
                "last_video_subtitle_status": self._single_line(ai_daily.get("last_video_subtitle_status") or ai_digest_first_item.get("video_subtitle_status"), 40),
                "last_read_basis": self._single_line(ai_daily.get("last_read_basis"), 40),
                "sources": [
                    {
                        "key": self._single_line(item.get("key"), 80),
                        "name": self._single_line(item.get("name"), 40),
                        "author_name": self._single_line(item.get("author_name"), 60),
                        "mid": self._single_line(item.get("mid"), 40),
                        "schedule": self._single_line(item.get("schedule"), 10),
                    }
                    for item in (ai_daily.get("sources") if isinstance(ai_daily.get("sources"), list) else [])
                    if isinstance(item, dict)
                ],
                "source_states": {
                    self._single_line(key, 80): {
                        "name": self._single_line(value.get("name"), 40),
                        "author_name": self._single_line(value.get("author_name"), 60),
                        "mid": self._single_line(value.get("mid"), 40),
                        "schedule": self._single_line(value.get("schedule"), 10),
                        "status": self._single_line(value.get("status"), 80),
                        "last_checked_at": self.plugin._format_timestamp_elapsed(value.get("last_checked_at", 0)),
                        "last_success_date": self._single_line(value.get("last_success_date"), 20),
                        "last_video_title": self._single_line(value.get("last_video_title"), 120),
                    }
                    for key, value in (ai_daily.get("source_states") if isinstance(ai_daily.get("source_states"), dict) else {}).items()
                    if isinstance(value, dict)
                },
                "topic": self._single_line(ai_digest.get("topic"), 60),
                "headline": self._single_line(ai_digest.get("headline"), 120),
            },
            "last_digest": {
                "topic": self._single_line(digest.get("topic"), 60),
                "headline": self._single_line(digest.get("headline"), 120),
                "source": self._single_line(digest.get("selected_source"), 40),
                "impression": self._sanitize_news_text(
                    digest.get("impression"),
                    180,
                    fallback="这条新闻的正文解析异常，建议稍后重新阅读。",
                ),
                "link": self._single_line(digest.get("selected_link"), 400),
            },
            "latest_items": [
                {
                    "source": self._single_line(item.get("source"), 40),
                    "title": self._single_line(item.get("title"), 120),
                    "summary": self._sanitize_news_text(
                        item.get("summary"),
                        160,
                        fallback="这条新闻摘要暂时解析异常，建议打开原文查看。",
                    ),
                    "link": self._single_line(item.get("link"), 400),
                }
                for item in latest_items[:8]
                if isinstance(item, dict)
            ],
        }

    def _web_exploration_summary(self, data: dict[str, Any]) -> dict[str, Any]:
        state = data.get("web_exploration") if isinstance(data.get("web_exploration"), dict) else {}
        digest = state.get("last_digest") if isinstance(state.get("last_digest"), dict) else {}
        notes = state.get("notes") if isinstance(state.get("notes"), list) else []
        history = [
            item
            for item in self._browsing_history_entries(data)
            if item.get("source") != "news"
        ]
        custom_available = bool(getattr(self.plugin, "_custom_web_exploration_search_configured", lambda: False)())
        try:
            astrbot_available = bool(getattr(self.plugin, "_astrbot_any_web_search_available", lambda: False)())
        except Exception:
            astrbot_available = False
        available = bool(custom_available or astrbot_available)
        search_backend = "custom" if custom_available else ("astrbot" if astrbot_available else "none")
        return {
            "enabled": bool(getattr(self.plugin, "enable_web_exploration", False)),
            "boredom_search_enabled": bool(getattr(self.plugin, "enable_web_exploration_boredom_search", False)),
            "available": available,
            "search_backend": search_backend,
            "custom_search_enabled": custom_available,
            "astrbot_search_available": astrbot_available,
            "last_explore_at": self.plugin._format_timestamp_elapsed(state.get("last_explore_at", 0)),
            "last_status": self._single_line(state.get("last_status"), 80),
            "last_query": {
                "query": self._single_line((state.get("last_query") or {}).get("query") if isinstance(state.get("last_query"), dict) else "", 80),
                "reason": self._single_line((state.get("last_query") or {}).get("reason") if isinstance(state.get("last_query"), dict) else "", 120),
                "topic": self._single_line((state.get("last_query") or {}).get("topic") if isinstance(state.get("last_query"), dict) else "", 20),
            },
            "last_digest": {
                "topic": self._single_line(digest.get("topic"), 80),
                "note": self._single_line(digest.get("note"), 220),
                "source_title": self._single_line(digest.get("source_title"), 120),
                "source_url": self._single_line(digest.get("source_url"), 400),
            },
            "note_count": len(notes),
            "history_count": len(history),
            "history": history,
            "recent_notes": [
                {
                    "topic": self._single_line(item.get("topic"), 80),
                    "note": self._single_line(item.get("note"), 180),
                    "query": self._single_line(item.get("query"), 80),
                    "created_at": self.plugin._format_timestamp_elapsed(item.get("created_ts", 0)),
                }
                for item in notes[-8:]
                if isinstance(item, dict)
            ],
        }

    def _qzone_summary(self, data: dict[str, Any]) -> dict[str, Any]:
        state = data.get("qzone_integration") if isinstance(data.get("qzone_integration"), dict) else {}
        daily_plan = state.get("life_publish_daily_plan") if isinstance(state.get("life_publish_daily_plan"), dict) else {}
        plan_items = daily_plan.get("items") if isinstance(daily_plan.get("items"), list) else []
        pending_times = [
            self._float(item.get("planned_at"))
            for item in plan_items
            if isinstance(item, dict) and item.get("status") == "planned" and self._float(item.get("planned_at")) > 0
        ]
        plan_status_counts: dict[str, int] = {}
        for item in plan_items:
            if not isinstance(item, dict):
                continue
            status = self._single_line(item.get("status"), 24) or "planned"
            plan_status_counts[status] = plan_status_counts.get(status, 0) + 1
        service_available = bool(
            callable(getattr(self.plugin, "_qzone_get_cookies", None))
            and callable(getattr(self.plugin, "_qzone_query_feeds", None))
        )
        platform_checker = getattr(self.plugin, "_qzone_platform_supported", None)
        platform_supported = bool(platform_checker(None)) if callable(platform_checker) else True
        available = bool(service_available and platform_supported)
        enabled = bool(available and getattr(self.plugin, "enable_qzone_integration", False))
        return {
            "enabled": enabled,
            "life_publish_enabled": bool(enabled and getattr(self.plugin, "enable_qzone_life_publish", False)),
            "comment_inbox_enabled": bool(enabled and getattr(self.plugin, "enable_qzone_comment_inbox", False)),
            "emotional_vent_enabled": bool(
                enabled
                and getattr(self.plugin, "enable_emotion_simulation", False)
                and getattr(self.plugin, "enable_qzone_emotional_vent_publish", False)
            ),
            "available": available,
            "service_available": service_available,
            "platform_supported": platform_supported,
            "unavailable_reason": "" if platform_supported else "QQ 官方机器人不支持 QQ 空间；仅 OneBot/aiocqhttp 可用。",
            "last_life_publish_at": self.plugin._format_timestamp_elapsed(state.get("last_life_publish_at", 0)),
            "last_status": state.get("last_life_publish_status", ""),
            "last_text": state.get("last_life_publish_text", ""),
            "life_publish_plan_date": self._single_line(daily_plan.get("date"), 24),
            "life_publish_plan_target_count": self._int(daily_plan.get("target_count")),
            "life_publish_plan_published_count": self._int(daily_plan.get("published_count")),
            "life_publish_plan_skip_reason": self._single_line(daily_plan.get("skip_reason"), 80),
            "life_publish_plan_status_counts": plan_status_counts,
            "life_publish_plan_next_at": self.plugin._format_timestamp_elapsed(min(pending_times)) if pending_times else "",
            "generated_image_enabled": bool(enabled and getattr(self.plugin, "enable_qzone_generated_image_publish", False)),
            "generated_image_probability": self._float(getattr(self.plugin, "qzone_generated_image_probability", 0)),
            "last_life_publish_images": self._int(state.get("last_life_publish_images")),
            "last_life_publish_generated_image_status": state.get("last_life_publish_generated_image_status", ""),
            "last_life_publish_generated_image_note": state.get("last_life_publish_generated_image_note", ""),
            "last_life_publish_generated_image_backend": state.get("last_life_publish_generated_image_backend", ""),
            "last_life_publish_generated_image_caption": state.get("last_life_publish_generated_image_caption", ""),
            "last_life_publish_generated_image_reference": state.get("last_life_publish_generated_image_reference", ""),
            "last_life_publish_generated_image_reference_exists": bool(state.get("last_life_publish_generated_image_reference_exists", False)),
            "last_life_publish_generated_image_anchor": state.get("last_life_publish_generated_image_anchor", ""),
            "last_life_publish_generated_image_composition": state.get("last_life_publish_generated_image_composition", ""),
            "last_manual_publish_generated_image_status": state.get("last_manual_publish_generated_image_status", ""),
            "last_manual_publish_generated_image_note": state.get("last_manual_publish_generated_image_note", ""),
            "last_manual_publish_generated_image_backend": state.get("last_manual_publish_generated_image_backend", ""),
            "last_manual_publish_generated_image_caption": state.get("last_manual_publish_generated_image_caption", ""),
            "last_manual_publish_generated_image_reference": state.get("last_manual_publish_generated_image_reference", ""),
            "last_manual_publish_generated_image_reference_exists": bool(state.get("last_manual_publish_generated_image_reference_exists", False)),
            "last_manual_publish_generated_image_anchor": state.get("last_manual_publish_generated_image_anchor", ""),
            "last_manual_publish_generated_image_composition": state.get("last_manual_publish_generated_image_composition", ""),
            "last_emotional_vent_at": self.plugin._format_timestamp_elapsed(state.get("last_emotional_vent_at", 0)),
            "last_emotional_vent_status": state.get("last_emotional_vent_status", ""),
            "last_emotional_vent_text": state.get("last_emotional_vent_text", ""),
            "last_emotional_vent_images": self._int(state.get("last_emotional_vent_images")),
            "last_emotional_vent_generated_image_status": state.get("last_emotional_vent_generated_image_status", ""),
            "last_emotional_vent_generated_image_note": state.get("last_emotional_vent_generated_image_note", ""),
            "last_emotional_vent_generated_image_backend": state.get("last_emotional_vent_generated_image_backend", ""),
            "last_emotional_vent_generated_image_caption": state.get("last_emotional_vent_generated_image_caption", ""),
            "last_emotional_vent_generated_image_reference": state.get("last_emotional_vent_generated_image_reference", ""),
            "last_emotional_vent_generated_image_reference_exists": bool(state.get("last_emotional_vent_generated_image_reference_exists", False)),
            "last_emotional_vent_generated_image_anchor": state.get("last_emotional_vent_generated_image_anchor", ""),
            "last_emotional_vent_generated_image_composition": state.get("last_emotional_vent_generated_image_composition", ""),
            "last_comment_inbox_checked_at": self.plugin._format_timestamp_elapsed(state.get("last_comment_inbox_checked_at", 0)),
            "last_comment_inbox_status": state.get("last_comment_inbox_status", ""),
            "last_comment_inbox_reply_text": state.get("last_comment_inbox_reply_text", ""),
            "auth_block_until": self.plugin._format_timestamp_elapsed(state.get("auth_block_until", 0)),
            "auth_failure_reason": state.get("last_auth_failure_reason", ""),
            "auth_failure_count": self._int(state.get("auth_failure_count")),
            "auth_status": state.get("last_auth_status", ""),
            "cookie_fetch_status": state.get("last_cookie_fetch_status", ""),
            "cookie_fetch_reason": state.get("last_cookie_fetch_reason", ""),
            "cookie_fetch_has_uin": bool(state.get("last_cookie_fetch_has_uin", False)),
            "cookie_fetch_has_skey": bool(state.get("last_cookie_fetch_has_skey", False)),
            "cookie_fetch_has_p_skey": bool(state.get("last_cookie_fetch_has_p_skey", False)),
            "cookie_fetch_uin": state.get("last_cookie_fetch_uin", ""),
            "cookie_fetch_at": self.plugin._format_timestamp_elapsed(state.get("last_cookie_fetch_at", 0)),
        }

    def _creative_summary(self, data: dict[str, Any]) -> dict[str, Any]:
        projects = data.get("creative_projects") if isinstance(data.get("creative_projects"), list) else []
        items = [item for item in projects if isinstance(item, dict)]
        active = [item for item in items if item.get("status") == "drafting"]
        latest = items[-1] if items else {}
        return {
            "enabled": bool(getattr(self.plugin, "enable_creative_writing", False)),
            "hidden_mode": bool(getattr(self.plugin, "creative_hidden_mode", False)),
            "cover_generation_enabled": bool(getattr(self.plugin, "enable_creative_cover_generation", False)),
            "project_count": len(items),
            "active_projects": len(active),
            "latest_title": self._single_line(latest.get("title"), 60) if isinstance(latest, dict) else "",
            "latest_status": self._single_line(latest.get("status"), 24) if isinstance(latest, dict) else "",
            "latest_progress": {
                "current_chars": self._int(latest.get("current_chars")) if isinstance(latest, dict) else 0,
                "target_chars": self._int(latest.get("target_chars")) if isinstance(latest, dict) else 0,
            },
            "items": [
                {
                    "id": self._single_line(item.get("id"), 20),
                    "title": self._single_line(item.get("title"), 60),
                    "work_type": self._single_line(item.get("work_type"), 30) or "短篇小说",
                    "premise": self._single_line(item.get("premise"), 160),
                    "tone": self._single_line(item.get("tone"), 40),
                    "point_of_view": self._single_line(item.get("point_of_view"), 40) or "第三人称有限视角",
                    "source": self._single_line(item.get("source_text"), 160),
                    "status": self._single_line(item.get("status"), 24),
                    "current_chars": self._int(item.get("current_chars")),
                    "target_chars": self._int(item.get("target_chars")),
                    "chunk_count": len(item.get("draft_chunks") or []) if isinstance(item.get("draft_chunks"), list) else 0,
                    "outline_count": len(item.get("outline") or []) if isinstance(item.get("outline"), list) else 0,
                    "character_count": len(item.get("characters") or []) if isinstance(item.get("characters"), list) else 0,
                    "has_story_bible": bool(item.get("story_bible")) if isinstance(item.get("story_bible"), dict) else False,
                    "review_count": len(item.get("quality_reviews") or []) if isinstance(item.get("quality_reviews"), list) else 0,
                    "manual_edit_count": len(item.get("manual_edits") or []) if isinstance(item.get("manual_edits"), list) else 0,
                    "latest_snippet": self._single_line(
                        (item.get("draft_chunks") or [])[-1].get("text") if isinstance(item.get("draft_chunks"), list) and item.get("draft_chunks") else "",
                        260,
                    ),
                    "milestones": item.get("disclosed_milestones") if isinstance(item.get("disclosed_milestones"), list) else [],
                    "created_at": self.plugin._format_timestamp_elapsed(item.get("created_at", 0)),
                    "last_advanced": self.plugin._format_timestamp_elapsed(item.get("last_advanced_at", 0)),
                    "next_advance": self.plugin._format_timestamp_elapsed(item.get("next_advance_at", 0)),
                    "cover_src": self._creative_project_cover_url(item),
                    "cover_status": self._single_line(item.get("cover_generation_status"), 24),
                    "cover_error": self._single_line(item.get("cover_generation_error"), 180),
                }
                for item in items[-6:]
            ],
        }

    def _daily_state_summary(self, state: Any) -> dict[str, Any]:
        if not isinstance(state, dict):
            return {}
        keys = ["date", "sleep", "dream", "health", "hunger", "body_cycle", "location", "weather", "mood_bias", "energy", "note"]
        summary = {key: state.get(key, "") for key in keys}
        cycle_runtime = state.get("cycle_runtime") if isinstance(state.get("cycle_runtime"), dict) else {}
        if cycle_runtime:
            discomfort = cycle_runtime.get("discomfort")
            summary["cycle_runtime"] = {
                "phase": self._single_line(cycle_runtime.get("phase"), 24),
                "phase_name": self._single_line(cycle_runtime.get("phase_name"), 24),
                "day_in_phase": self._int(cycle_runtime.get("day_in_phase")),
                "phase_days": self._int(cycle_runtime.get("phase_days")),
                "cycle_day": self._int(cycle_runtime.get("cycle_day")),
                "cycle_days": self._int(cycle_runtime.get("cycle_days")),
                "mood": self._single_line(cycle_runtime.get("mood"), 20),
                "energy_delta": self._int(cycle_runtime.get("energy_delta")),
                "next_phase_name": self._single_line(cycle_runtime.get("next_phase_name"), 24),
                "discomfort": [
                    {
                        "type": self._single_line(item.get("type"), 12),
                        "label": self._single_line(item.get("label"), 80),
                        "mood": self._single_line(item.get("mood"), 12),
                    }
                    for item in discomfort[:4]
                    if isinstance(item, dict)
                ]
                if isinstance(discomfort, list)
                else [],
            }
        location_getter = getattr(self.plugin, "_current_location_state_text", None)
        if callable(location_getter):
            try:
                effective_location = self._single_line(location_getter(state), 60)
            except Exception:
                effective_location = ""
            if effective_location:
                summary["location"] = effective_location
        summary["location_source"] = self._single_line(state.get("location_source"), 40)
        summary["location_confidence"] = self._float(state.get("location_confidence"), 0.0)
        runtime = state.get("sleep_runtime") if isinstance(state.get("sleep_runtime"), dict) else {}
        if runtime:
            summary["sleep_phase"] = self._single_line(runtime.get("label") or runtime.get("phase"), 40)
            summary["sleep_runtime"] = {
                "phase": self._single_line(runtime.get("phase"), 40),
                "label": self._single_line(runtime.get("label") or runtime.get("phase"), 40),
                "last_event": self._single_line(runtime.get("last_event"), 120),
                "source": self._single_line(runtime.get("source"), 40),
                "woken_count": self._int(runtime.get("woken_count")),
                "updated_at": self.plugin._format_timestamp_elapsed(runtime.get("updated_at", 0)),
            }
            delay_until = self._float(runtime.get("sleep_delay_until_ts"), 0)
            if delay_until > time.time():
                summary["sleep_delay_override"] = {
                    "active": True,
                    "until": self._single_line(runtime.get("sleep_delay_until_text"), 40)
                    or self.plugin._environment_fromtimestamp(delay_until).strftime("%m-%d %H:%M"),
                    "reason": self._single_line(runtime.get("sleep_delay_reason"), 120),
                    "user_text": self._single_line(runtime.get("sleep_delay_user_text"), 120),
                    "explicit_time": bool(runtime.get("sleep_delay_explicit_time")),
                }
        return summary

    def _life_observation_summary(self, data: dict[str, Any]) -> dict[str, Any]:
        dream = data.get("daily_dream") if isinstance(data.get("daily_dream"), dict) else {}
        diaries = data.get("bot_diaries") if isinstance(data.get("bot_diaries"), list) else []
        fragments = data.get("dream_fragments") if isinstance(data.get("dream_fragments"), list) else []
        plan = data.get("daily_plan") if isinstance(data.get("daily_plan"), dict) else {}
        story = data.get("daily_story_plan") if isinstance(data.get("daily_story_plan"), dict) else {}
        current_item = {}
        current_lifecycle = ""
        current_evidence_lifecycle = ""
        current_clock_status = ""
        try:
            current_getter = getattr(self.plugin, "_agenda_current_context_item", None)
            picked = (
                current_getter()
                if callable(current_getter)
                else self.plugin._get_current_plan_item(plan)
            )
            if not isinstance(picked, dict):
                display_getter = getattr(self.plugin, "_get_clock_plan_item_for_display", None)
                picked = display_getter(plan) if callable(display_getter) else None
            current_item = picked if isinstance(picked, dict) else {}
            items = plan.get("items") if isinstance(plan.get("items"), list) else []
            current_index = next((index for index, item in enumerate(items) if item is picked), -1)
            if current_item:
                current_evidence_lifecycle = self.plugin._plan_item_runtime_status(
                    plan,
                    current_item,
                    current_index,
                )
                display_status = getattr(self.plugin, "_plan_item_display_status", None)
                current_clock_status = (
                    display_status(plan, current_item, current_index)
                    if callable(display_status)
                    else current_evidence_lifecycle
                )
                # Preserve the legacy display-oriented field for older page
                # consumers while exposing the evidence/clock split to new UI.
                current_lifecycle = current_clock_status
        except Exception:
            current_item = {}
            current_lifecycle = ""
            current_evidence_lifecycle = ""
            current_clock_status = ""

        return {
            "dream": {
                "date": self._single_line(dream.get("date"), 24),
                "label": self._single_line(dream.get("label"), 120),
                "dream_type": self._single_line(dream.get("dream_type"), 40),
                "content": self._single_line(dream.get("content"), 1000),
                "afterglow": self._single_line(dream.get("afterglow"), 220),
                "mood": self._single_line(dream.get("mood"), 30),
                "energy_delta": self._int(dream.get("energy_delta")),
                "duration_hours": self._int(dream.get("duration_hours")),
                "generated_at": self._single_line(dream.get("generated_at"), 24),
                "factors": [self._single_line(item, 40) for item in dream.get("factors", [])[:8] if self._single_line(item, 40)]
                if isinstance(dream.get("factors"), list)
                else [],
            },
            "diaries": [
                {
                    "date": self._single_line(item.get("date"), 24),
                    "summary": self.plugin._polish_diary_text(item.get("summary"), field="summary"),
                    "body": self.plugin._polish_diary_text(item.get("body"), field="body"),
                    "share_seed": self.plugin._polish_diary_text(item.get("share_seed"), field="share"),
                    "tags": [self._single_line(tag, 20) for tag in item.get("tags", [])[:6] if self._single_line(tag, 20)]
                    if isinstance(item.get("tags"), list)
                    else [],
                    "generated_at": self._single_line(item.get("generated_at"), 24),
                }
                for item in diaries[-4:]
                if isinstance(item, dict)
            ],
            "dream_fragments": self._limited_dream_fragments(fragments),
            "current_plan": {
                "time": self._single_line(current_item.get("time"), 12),
                "end": self._single_line(current_item.get("end"), 12),
                "lifecycle": current_lifecycle,
                "evidence_lifecycle": current_evidence_lifecycle,
                "clock_status": current_clock_status,
                "activity": self._single_line(current_item.get("activity"), 600),
                "mood": self._single_line(current_item.get("mood"), 40),
                "message_seed": self._single_line(current_item.get("message_seed"), 500),
            },
            "story": {
                "date": self._single_line(story.get("date"), 24),
                "today_events": self._limited_story_items(story.get("today_events"), 4),
                "proactive_events": self._limited_story_items(story.get("proactive_events"), 4),
            },
        }

    def _daily_timeline_summary(self, data: dict[str, Any]) -> dict[str, Any]:
        plan = data.get("daily_plan") if isinstance(data.get("daily_plan"), dict) else {}
        raw_enhanced = data.get("detail_enhanced_segments") if isinstance(data.get("detail_enhanced_segments"), dict) else {}
        enhanced = self.plugin._detail_enhanced_segments_for_plan_date(
            plan.get("date"),
            raw_enhanced,
            detail_day=data.get("detail_enhanced_day"),
        )
        story = data.get("daily_story_plan") if isinstance(data.get("daily_story_plan"), dict) else {}
        adjustments = data.get("schedule_adjustments") if isinstance(data.get("schedule_adjustments"), list) else []
        presence = data.get("qq_presence_state") if isinstance(data.get("qq_presence_state"), dict) else {}

        segments: list[dict[str, Any]] = []
        seen_segment_keys: set[str] = set()
        plan_items = plan.get("items") if isinstance(plan.get("items"), list) else []
        normalized_starts = self.plugin._normalized_plan_item_starts(plan_items)
        for key, snapshot in enhanced.items():
            if not isinstance(snapshot, dict):
                continue
            segment = self._segment_from_key(str(key), plan, snapshot)
            seen_segment_keys.add(str(key))
            segment_item = segment.get("item") if isinstance(segment.get("item"), dict) else {}
            evidence_lifecycle = self.plugin._plan_item_runtime_status(
                plan,
                segment_item,
                self._int(segment.get("index"), -1, -1),
            ) if segment_item else "planned"
            display_status = getattr(self.plugin, "_plan_item_display_status", None)
            clock_status = (
                display_status(plan, segment_item, self._int(segment.get("index"), -1, -1))
                if segment_item and callable(display_status)
                else self.plugin._plan_item_runtime_status(
                    plan,
                    segment_item,
                    self._int(segment.get("index"), -1, -1),
                ) if segment_item else "planned"
            )
            segments.append(
                {
                    "key": str(key),
                    "window": segment.get("window", str(key)),
                    "start": segment.get("start", 99999),
                    "end": segment.get("end", 99999),
                    # Keep lifecycle as the legacy display field for API
                    # compatibility; new clients should use the explicit
                    # evidence/clock split below.
                    "lifecycle": clock_status,
                    "evidence_lifecycle": evidence_lifecycle,
                    "clock_status": clock_status,
                    "activity": self._single_line(segment_item.get("activity"), 180),
                    "basis": self.plugin._normalize_schedule_basis(segment_item.get("basis"), default=["coarse_plan"]),
                    "confidence": self._float(segment_item.get("confidence"), 0.72, 0.0, 1.0),
                    "status": snapshot.get("status", ""),
                    "started_at": snapshot.get("started_at", ""),
                    "summary": snapshot.get("summary", ""),
                    "summary_basis": self.plugin._normalize_schedule_basis(snapshot.get("summary_basis"), default=["coarse_plan"]),
                    "summary_confidence": self._float(snapshot.get("summary_confidence"), 0.75, 0.0, 1.0),
                    "quality": snapshot.get("quality") if isinstance(snapshot.get("quality"), dict) else {},
                    "regeneration_error": self._single_line(snapshot.get("regeneration_error"), 180),
                    "error": snapshot.get("error", ""),
                    "retry_after": snapshot.get("retry_after", ""),
                    "state_variables": self._limited_state_variables(snapshot.get("state_variables")),
                    "presence_status": snapshot.get("presence_status") if isinstance(snapshot.get("presence_status"), dict) else {},
                    "interaction_updates": self._limited_interaction_updates(snapshot.get("interaction_updates")),
                    "today_events": self._timeline_story_items(
                        snapshot.get("today_events"),
                        5,
                        str(plan.get("date") or ""),
                        parent_start=self._int(segment.get("start"), -1, -1),
                        parent_end=self._int(segment.get("end"), -1, -1),
                    ),
                    "proactive_events": self._timeline_story_items(
                        snapshot.get("proactive_events"),
                        4,
                        str(plan.get("date") or ""),
                        parent_start=self._int(segment.get("start"), -1, -1),
                        parent_end=self._int(segment.get("end"), -1, -1),
                    ),
                }
            )
        for index, item in enumerate(plan_items):
            if not isinstance(item, dict):
                continue
            start_text = self._single_line(item.get("time"), 8)
            start = normalized_starts[index] if index < len(normalized_starts) else None
            if start is None:
                continue
            key = f"{plan.get('date')}:{index}:{start_text}"
            if key in seen_segment_keys:
                continue
            next_start = None
            for next_item in plan_items[index + 1 :]:
                if isinstance(next_item, dict):
                    next_start = self.plugin._parse_hhmm_to_minutes(next_item.get("time"))
                    if next_start is not None:
                        break
            end = self.plugin._plan_item_end_minutes(start, item, next_start=next_start)
            evidence_lifecycle = self.plugin._plan_item_runtime_status(plan, item, index)
            clock_status = (
                self.plugin._plan_item_display_status(plan, item, index)
                if callable(getattr(self.plugin, "_plan_item_display_status", None))
                else evidence_lifecycle
            )
            segments.append(
                {
                    "key": key,
                    "window": f"{self.plugin._minutes_to_hhmm(start)}-{self.plugin._minutes_to_hhmm(end)}",
                    "start": start,
                    "end": end,
                    "lifecycle": clock_status,
                    "evidence_lifecycle": evidence_lifecycle,
                    "clock_status": clock_status,
                    "activity": self._single_line(item.get("activity"), 180),
                    "basis": self.plugin._normalize_schedule_basis(item.get("basis"), default=["coarse_plan"]),
                    "confidence": self._float(item.get("confidence"), 0.72, 0.0, 1.0),
                    "status": "",
                    "summary": self._single_line(item.get("activity"), 180),
                    "summary_basis": self.plugin._normalize_schedule_basis(item.get("basis"), default=["coarse_plan"]),
                    "summary_confidence": self._float(item.get("confidence"), 0.72, 0.0, 1.0),
                    "quality": {},
                    "state_variables": [],
                    "presence_status": {},
                    "interaction_updates": [],
                    "today_events": [],
                    "proactive_events": [],
                }
            )
        segments.sort(key=lambda item: item.get("start", 99999))

        return {
            "plan_date": plan.get("date", ""),
            "story_date": story.get("date", ""),
            "detail_day": data.get("detail_enhanced_day", ""),
            "segment_count": len(segments),
            "plan_quality": plan.get("quality") if isinstance(plan.get("quality"), dict) else {},
            "segments": segments,
            "story_today_events": self._limited_story_items(story.get("today_events"), 48),
            "story_proactive_events": self._limited_story_items(story.get("proactive_events"), 32),
            "adjustments": self._limited_adjustments(adjustments),
            "qq_presence_state": presence,
        }

