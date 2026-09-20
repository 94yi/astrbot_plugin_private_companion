# -*- coding: utf-8 -*-
"""REQ041 迁移 / 作用域重绑域。

由 tools/split_main_domain.py 从 main.py 机械抽取（48 个方法 / 1911 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPlugin）。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import uuid
from .helpers import _now_ts, _set_into_config, _single_line
from .identity_namespace import AssurancePolicy, NamespaceContext
from .migration_backfill import MigrationBackfill, legacy_pending_reference
from .migration_dual_write import MigrationDualWriteProducer
from .migration_read_router import MigrationRelationshipReadRouter
from .migration_replay import MigrationReplayWorker
from .migration_scoped_projection import ScopedProjectionSynchronizer, scoped_group_ref, scoped_persona_ref
from .migration_source_inspector import inspect_migration_sources
from .migration_stability import advance_migration_stability
from .persona_config import runtime_persona_setting
from .relationship_account_store import RelationshipAccountStore
from .relationship_affinity_runtime import (
    admit_confirmed_group_affinity,
    normalize_group_allowlist,
    prepare_group_affinity_candidate,
)
from .relationship_ledger import normalize_relationship_positive_stage_cap_key
from .req041_observability import Req041Observability
from .scoped_runtime_view import overlay_group_runtime_view, overlay_private_runtime_view
from .unified_person_registry import UnifiedPersonRegistry
from astrbot.api.event import AstrMessageEvent
from collections.abc import Collection
from copy import deepcopy
from pathlib import Path
from typing import Any

from .logging_util import get_module_logger

logger = get_module_logger(__name__)

class PrivateCompanionPluginReq041Mixin:
    """REQ041 迁移 / 作用域重绑域（从 PrivateCompanionPlugin 拆出）。"""

    def _req041_update_unified_profile_facts(
        self,
        user: dict[str, Any],
        changes: dict[str, Any],
        *,
        operation_id: str = "",
        actor_id: str = "companion",
        schedule_save: bool = False,
    ) -> dict[str, Any]:
        if not isinstance(user, dict) or not isinstance(changes, dict) or not changes:
            return {"ok": False, "state": "skipped", "code": "profile_fact_update_skipped"}
        person_id = _single_line(user.get("unified_person_id"), 80)
        if not person_id:
            return {"ok": False, "state": "skipped", "code": "profile_identity_pending"}
        result = self._active_unified_person_registry().update_identity_profile_facts(
            person_id,
            changes,
            operation_id=(
                _single_line(operation_id, 120)
                or f"req041-profile-{uuid.uuid4().hex}"
            ),
            actor_id=actor_id,
        )
        if result.get("ok") and result.get("changed") and schedule_save:
            self._schedule_data_save(sections={"unified_person"})
        return result

    def _req041_emit_identity_dual_write(
        self,
        result: dict[str, Any],
        *,
        action: str,
        operation_id: str,
        registry: UnifiedPersonRegistry | None = None,
    ) -> dict[str, Any]:
        producer = getattr(self, "req041_dual_write_producer", None)
        if producer is None:
            return {"status": "skipped", "code": "dual_write_not_active"}
        active_registry = registry if isinstance(registry, UnifiedPersonRegistry) else self._active_unified_person_registry()
        try:
            return producer.emit_identity_change(
                registry=active_registry,
                result=result,
                action=action,
                operation_id=operation_id,
            )
        except Exception as exc:
            producer.fail_closed("identity_dual_write_failed")
            migration_status = getattr(self, "req041_migration_status", None)
            if isinstance(migration_status, dict):
                migration_status.update({
                    "state": "paused",
                    "code": "identity_dual_write_failed",
                    "dual_write": "failed",
                })
            logger.warning(
                "REQ-041 身份双写失败，已暂停新读切换并保留 legacy 写入: %s",
                _single_line(exc, 160),
            )
            return {"status": "failed", "code": "identity_dual_write_failed"}

    def _req041_emit_relationship_snapshot(
        self,
        user: dict[str, Any],
        *,
        reason_code: str,
    ) -> dict[str, Any]:
        producer = getattr(self, "req041_dual_write_producer", None)
        if producer is None:
            return {"status": "skipped", "code": "dual_write_not_active"}
        try:
            try:
                source_revision = max(0, int(user.get("req041_relationship_source_revision") or 0)) + 1
            except (TypeError, ValueError, OverflowError):
                source_revision = 1
            scope = self._unified_persona_domain()
            emitted = producer.emit_relationship_snapshot(
                registry=self._active_unified_person_registry(),
                user=user,
                reason_code=reason_code,
                source_scope=scope or "default",
                source_revision=source_revision,
            )
            if int(emitted.get("source_revision") or 0) > 0:
                user["req041_relationship_source_revision"] = int(emitted["source_revision"])
            return emitted
        except Exception as exc:
            producer.fail_closed("relationship_snapshot_dual_write_failed")
            migration_status = getattr(self, "req041_migration_status", None)
            if isinstance(migration_status, dict):
                migration_status.update({
                    "state": "paused",
                    "code": "relationship_snapshot_dual_write_failed",
                    "dual_write": "failed",
                })
            logger.warning(
                "REQ-041 关系快照双写失败，已暂停新读切换并保留 legacy 写入: %s",
                _single_line(exc, 160),
            )
            return {"status": "failed", "code": "relationship_snapshot_dual_write_failed"}

    def _req041_migration_source_files(self) -> list[Path]:
        # Once a migration has a verified backup, its manifest is the authority
        # for the legacy source set. Persona profiles created later are new
        # runtime stores and must not change the resume contract on restart.
        coordinator = getattr(self, "req041_migration_coordinator", None)
        status_getter = getattr(coordinator, "status", None)
        if callable(status_getter):
            try:
                status = status_getter()
            except Exception:
                status = {}
            manifest_name = _single_line(
                status.get("backup_manifest") if isinstance(status, dict) else "",
                300,
            )
            if manifest_name:
                data_root = Path(str(getattr(self, "data_dir", "") or "")).resolve()
                try:
                    manifest_path = (data_root / manifest_name).resolve(strict=True)
                    manifest_path.relative_to(data_root)
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                    entries = manifest.get("files") if isinstance(manifest, dict) else None
                    frozen: list[Path] = []
                    if isinstance(entries, list):
                        for entry in entries:
                            name = entry.get("name") if isinstance(entry, dict) else ""
                            if not isinstance(name, str) or not name.strip():
                                frozen = []
                                break
                            candidate = (data_root / name).resolve(strict=False)
                            candidate.relative_to(data_root)
                            if candidate.is_symlink():
                                frozen = []
                                break
                            frozen.append(candidate)
                    if frozen:
                        return frozen
                except (OSError, ValueError, json.JSONDecodeError):
                    # Fall through to live discovery. The coordinator will then
                    # retain its existing fail-closed validation and error code.
                    pass
        candidates: list[Path] = []
        if str(getattr(self, "storage_backend", "json") or "json").lower() == "sqlite":
            candidates.append(Path(str(getattr(self, "storage_sqlite_effective_path", "") or "")))
        else:
            candidates.append(Path(str(getattr(self, "data_file", "") or "")))
        profiles = Path(str(getattr(self, "_persona_profiles_dir", "") or ""))
        if profiles.is_dir():
            candidates.extend(sorted(profiles.glob("*.db")))
            candidates.extend(sorted(profiles.glob("*.json")))
        result: list[Path] = []
        for candidate in candidates:
            try:
                if candidate and candidate.is_file() and not candidate.is_symlink():
                    result.append(candidate)
            except OSError:
                continue
        return result

    def _req041_compatibility_snapshot(self) -> dict[str, Any]:
        return {
            "auto_profile_creation": bool(runtime_persona_setting(self, 'enable_auto_user_profile_creation', False)),
            "private_access_policy": {
                "passive_private_default": "legacy_effective",
                "configured_targets_default": bool(runtime_persona_setting(self, 'default_enable_configured_targets', False)),
            },
            "proactive_policy": {
                "proactive_only": bool(runtime_persona_setting(self, 'enable_proactive_only_mode', False)),
                "intensity": _single_line(runtime_persona_setting(self, 'proactive_intensity_preset', "off"), 40) or "off",
            },
            "tool_policy": {
                "photo": bool(runtime_persona_setting(self, 'enable_photo_text_action', False)),
                "screen": bool(runtime_persona_setting(self, 'enable_screen_glance_action', False)),
                "poke": bool(runtime_persona_setting(self, 'enable_poke_action', False)),
                "voice": bool(runtime_persona_setting(self, 'enable_voice_action', False)),
            },
            "content_policy": {
                "relationship_tiers": bool(runtime_persona_setting(self, 'enable_relationship_content_tiers', False)),
            },
            "owner_policy": {
                "configured_target": _single_line(getattr(self, "target_user_id", ""), 80) != "",
                "normal_cap_exempt": True,
                "exclusive_mode_frozen": True,
            },
            "relationship_policy": {
                "enabled": bool(runtime_persona_setting(self, 'enable_custom_relationship_stage_policy', False)),
                "positive_cap": _single_line(
                    runtime_persona_setting(self, 'relationship_positive_stage_cap_key', "deeply_bonded"), 40
                ) or "deeply_bonded",
                "group_ordinary_delta": 0,
            },
        }

    def _req041_registry_for_person(self, person_id: str) -> UnifiedPersonRegistry | None:
        """Locate exactly one persona-scoped registry for a stable person id."""
        stores: list[dict[str, Any]] = []
        default_data = getattr(self, "_data_default", None)
        if not isinstance(default_data, dict):
            default_data = self.data if isinstance(getattr(self, "data", None), dict) else None
        if isinstance(default_data, dict):
            stores.append(default_data)
        profiles = getattr(self, "_persona_data_profiles", {})
        if isinstance(profiles, dict):
            for profile_data in profiles.values():
                if isinstance(profile_data, dict) and all(profile_data is not item for item in stores):
                    stores.append(profile_data)
        matches = [
            UnifiedPersonRegistry(store)
            for store in stores
            if UnifiedPersonRegistry(store).read_projection(person_id) is not None
        ]
        return matches[0] if len(matches) == 1 else None

    def _req041_legacy_relationship_state(self, person_id: str) -> dict[str, Any] | None:
        """Read exactly one live legacy authority row for S5 reconciliation."""
        stores: list[dict[str, Any]] = []
        default_data = getattr(self, "_data_default", None)
        if not isinstance(default_data, dict):
            default_data = self.data if isinstance(getattr(self, "data", None), dict) else None
        if isinstance(default_data, dict):
            stores.append(default_data)
        profiles = getattr(self, "_persona_data_profiles", {})
        if isinstance(profiles, dict):
            for profile_data in profiles.values():
                if isinstance(profile_data, dict) and all(profile_data is not item for item in stores):
                    stores.append(profile_data)
        matches: list[dict[str, Any]] = []
        for store in stores:
            registry = UnifiedPersonRegistry(store)
            users = store.get("users") if isinstance(store.get("users"), dict) else {}
            for legacy_key, user in users.items():
                if not isinstance(user, dict) or user.get("unified_person_id") != person_id:
                    continue
                subject = _single_line(
                    user.get("identity_subject_id") or user.get("user_id") or legacy_key, 160
                )
                if not subject or not registry.matches_person_subject(person_id, subject):
                    continue
                try:
                    score = int(user.get("relationship_score", 0))
                    totals = user.get("relationship_daily_totals")
                    totals = totals if isinstance(totals, dict) else {}
                    positive = int(totals.get("positive", 0))
                    negative = int(totals.get("negative", 0))
                    effective = float(user.get("relationship_last_effective_at") or 0.0)
                except (TypeError, ValueError, OverflowError):
                    return None
                if (
                    any(isinstance(value, bool) for value in (user.get("relationship_score"), totals.get("positive"), totals.get("negative")))
                    or not -1200 <= score <= 1200 or not 0 <= positive <= 120
                    or not -180 <= negative <= 0 or not math.isfinite(effective) or effective < 0
                ):
                    return None
                role = "owner" if str(user.get("relationship_role") or "").strip().lower() == "owner" else "friend"
                mode = (
                    "owner_exclusive"
                    if role == "owner" and str(user.get("relationship_mode") or "").strip().lower() == "owner_exclusive"
                    else "normal"
                )
                matches.append({
                    "relationship_role": role,
                    "relationship_mode": mode,
                    "relationship_score": score,
                    "positive_stage_cap_key": normalize_relationship_positive_stage_cap_key(
                        user.get("relationship_positive_stage_cap_key")
                    ),
                    "daily_totals": {
                        "day": _single_line(totals.get("day"), 16),
                        "positive": positive,
                        "negative": negative,
                    },
                    "last_effective_at": effective,
                })
        return matches[0] if len(matches) == 1 else None

    def _req041_resolve_legacy_pending_for_person(self, person_id: str) -> int:
        """Resolve one S4 opaque pending row only after S5 proves exact parity."""
        coordinator = getattr(self, "req041_migration_coordinator", None)
        status = coordinator.status() if coordinator is not None else {}
        epoch = _single_line(status.get("migration_epoch"), 128) if isinstance(status, dict) else ""
        if not epoch:
            return 0
        scoped_stores: list[tuple[str, dict[str, Any]]] = []
        default_data = getattr(self, "_data_default", None)
        if not isinstance(default_data, dict):
            default_data = self.data if isinstance(getattr(self, "data", None), dict) else None
        if isinstance(default_data, dict):
            scoped_stores.append(("default", default_data))
        profiles = getattr(self, "_persona_data_profiles", {})
        if isinstance(profiles, dict):
            for persona_id, profile_data in profiles.items():
                if not isinstance(profile_data, dict):
                    continue
                scope_hash = hashlib.sha256(str(persona_id).encode("utf-8")).hexdigest()[:24]
                scoped_stores.append((f"persona:{scope_hash}", profile_data))
        matches: list[tuple[str, str]] = []
        for source_scope, store in scoped_stores:
            registry = UnifiedPersonRegistry(store)
            users = store.get("users") if isinstance(store.get("users"), dict) else {}
            for legacy_key, user in users.items():
                if not isinstance(user, dict) or user.get("unified_person_id") != person_id:
                    continue
                subject = _single_line(
                    user.get("identity_subject_id") or user.get("user_id") or legacy_key, 160
                )
                if subject and registry.matches_person_subject(person_id, subject):
                    matches.append((source_scope, str(legacy_key)))
        if len(matches) != 1:
            return 0
        source_scope, legacy_key = matches[0]
        reference = legacy_pending_reference(epoch, source_scope, legacy_key)
        return int(bool(coordinator.resolve_pending(reference)))

    def _req041_schedule_replay(self) -> None:
        worker = getattr(self, "req041_migration_replay", None)
        if worker is None:
            return
        self._req041_replay_requested = True
        task = getattr(self, "_req041_replay_task", None)
        if task is not None and not task.done():
            return
        try:
            self._req041_replay_task = asyncio.get_running_loop().create_task(
                self._req041_run_replay_batch(), name="req041-shadow-replay"
            )
            self._req041_replay_task.add_done_callback(self._req041_replay_finished)
        except RuntimeError:
            raise RuntimeError("migration_replay_loop_unavailable")

    def _req041_legacy_snapshots_locked(self) -> list[tuple[str, dict[str, Any]]]:
        snapshots: list[tuple[str, dict[str, Any]]] = []
        default_data = getattr(self, "_data_default", None)
        if not isinstance(default_data, dict):
            default_data = self.data if isinstance(getattr(self, "data", None), dict) else {}
        snapshots.append(("default", deepcopy(default_data)))
        profiles = getattr(self, "_persona_data_profiles", {})
        if isinstance(profiles, dict):
            for persona_id, profile_data in profiles.items():
                if not isinstance(profile_data, dict):
                    continue
                scope_hash = hashlib.sha256(str(persona_id).encode("utf-8")).hexdigest()[:24]
                snapshots.append((f"persona:{scope_hash}", deepcopy(profile_data)))
        return snapshots

    async def _req041_sync_scoped_now(self) -> dict[str, Any]:
        synchronizer = getattr(self, "req041_scoped_projection_sync", None)
        if synchronizer is None:
            return {"ok": False, "code": "scoped_projection_not_initialized", "scopes": []}
        synchronizer.mark_dirty()
        async with self._data_lock:
            snapshots = self._req041_legacy_snapshots_locked()
        results: list[dict[str, Any]] = []
        for source_scope, snapshot in snapshots:
            results.append(await asyncio.to_thread(
                synchronizer.sync_snapshot, snapshot, source_scope=source_scope
            ))
        ok = all(item.get("ok") is True for item in results)
        summary = {
            "ok": ok,
            "code": "scoped_projection_synced" if ok else "scoped_projection_degraded",
            "scopes": results,
            "records": sum(int(item.get("records") or 0) for item in results),
            "errors": sum(int(item.get("errors") or 0) for item in results),
        }
        self.req041_scoped_projection_status = summary
        return summary

    async def _req041_rebind_memory_scope_if_available(self) -> dict[str, Any]:
        """Bind the scoped memory API after a late MemoryCompanion startup."""
        enabled = getattr(self, "_memory_companion_bridge_enabled", None)
        if callable(enabled) and not enabled():
            return {"ok": False, "code": "memory_bridge_disabled"}
        coordinator = getattr(self, "req041_migration_coordinator", None)
        binder = getattr(self, "_memory_companion_bind_namespace_epoch", None)
        bridge_getter = getattr(self, "_memory_companion_bridge", None)
        if coordinator is None or not callable(binder) or not callable(bridge_getter):
            return {"ok": False, "code": "memory_scope_runtime_unavailable"}
        lock = getattr(self, "_req041_memory_bind_lock", None)
        if lock is None:
            lock = asyncio.Lock()
            self._req041_memory_bind_lock = lock
        async with lock:
            status_reader = getattr(coordinator, "status", None)
            try:
                control = status_reader() if callable(status_reader) else {}
            except Exception:
                control = {}
            if not isinstance(control, dict):
                control = {}
            epoch = _single_line(control.get("migration_epoch"), 128)
            policy = _single_line(control.get("policy_version"), 64)
            if not epoch or not policy:
                return {"ok": False, "code": "migration_epoch_unavailable"}
            try:
                bridge = bridge_getter()
            except Exception as exc:
                return {"ok": False, "code": _single_line(exc, 120) or "memory_bridge_lookup_failed"}
            if bridge is None:
                return {"ok": False, "code": "memory_bridge_unavailable"}
            synchronizer = getattr(self, "req041_scoped_projection_sync", None)
            bound_bridge = getattr(self, "_req041_scoped_bridge", None)
            # A synchronizer without an explicit bridge marker may have been
            # created by an older hot-loaded plugin instance. Do not reuse
            # its closures against a newly discovered bridge.
            if synchronizer is not None and bound_bridge is bridge:
                scoped_result = await self._req041_sync_scoped_now()
                if scoped_result.get("ok"):
                    self._req041_mark_memory_scope_bound()
                    runtime = getattr(self, "req041_migration_status", None)
                    if isinstance(runtime, dict):
                        runtime.update({"memory_bound": True, "scoped": scoped_result})
                return scoped_result
            if synchronizer is not None:
                mark_dirty = getattr(synchronizer, "mark_dirty", None)
                if callable(mark_dirty):
                    mark_dirty()
            try:
                remote = binder(
                    bridge,
                    operation_id=f"req041-bind-{epoch}",
                    migration_epoch=epoch,
                    policy_version=policy,
                )
            except Exception as exc:
                return {"ok": False, "code": _single_line(exc, 120) or "namespace_epoch_bind_exception"}
            if not isinstance(remote, dict) or not remote.get("ok"):
                return dict(remote) if isinstance(remote, dict) else {
                    "ok": False, "code": "namespace_epoch_bind_invalid"
                }
            self._req041_mark_memory_scope_bound()
            self.req041_scoped_projection_sync = ScopedProjectionSynchronizer(
                read=lambda namespace, **kwargs: self._memory_companion_read_scoped_record(
                    bridge, namespace, **kwargs
                ),
                list_records=lambda namespace, **kwargs: self._memory_companion_list_scoped_records(
                    bridge, namespace, **kwargs
                ),
                upsert=lambda namespace, **kwargs: self._memory_companion_upsert_scoped_record(
                    bridge, namespace, **kwargs
                ),
                tombstone=lambda namespace, **kwargs: self._memory_companion_tombstone_scoped_record(
                    bridge, namespace, **kwargs
                ),
                tombstone_identity_scopes=lambda namespace, **kwargs: self._memory_companion_tombstone_scoped_identity_scopes(
                    bridge, namespace, **kwargs
                ),
                erase_group_scopes=lambda namespace, **kwargs: self._memory_companion_erase_scoped_group_scopes(
                    bridge, namespace, **kwargs
                ),
                erase_persona_scopes=lambda namespace, **kwargs: self._memory_companion_erase_scoped_persona_scopes(
                    bridge, namespace, **kwargs
                ),
                migration_epoch=epoch,
                policy_version=policy,
                observability=getattr(self, "req041_observability", None),
            )
            self._req041_scoped_bridge = bridge
            scoped_result = await self._req041_sync_scoped_now()
            runtime = getattr(self, "req041_migration_status", None)
            if isinstance(runtime, dict):
                runtime.update({"memory_bound": True, "scoped": scoped_result})
            return scoped_result

    async def _req041_run_memory_scope_rebind(self) -> None:
        """Retry late memory binding until the scoped runtime becomes usable."""
        stop_event = getattr(self, "_stop_event", None)
        startup_tasks = getattr(self, "_startup_background_tasks", {})
        migration_task = startup_tasks.get("req041_automatic_migration") if isinstance(startup_tasks, dict) else None
        if isinstance(migration_task, asyncio.Task) and migration_task is not asyncio.current_task():
            try:
                await asyncio.shield(migration_task)
            except Exception:
                pass
        while True:
            if isinstance(stop_event, asyncio.Event) and stop_event.is_set():
                return
            enabled = getattr(self, "_memory_companion_bridge_enabled", None)
            if callable(enabled) and not enabled():
                return
            status = getattr(self, "req041_migration_status", None)
            if not isinstance(status, dict):
                await asyncio.sleep(2.0)
                continue
            try:
                result = await self._req041_rebind_memory_scope_if_available()
            except Exception as exc:
                logger.warning(
                    "[PrivateCompanion] 记忆作用域补绑定暂未完成，将重试: %s",
                    _single_line(exc, 160),
                    exc_info=True,
                )
                await asyncio.sleep(2.0)
                continue
            if result.get("ok"):
                status = getattr(self, "req041_migration_status", None)
                if isinstance(status, dict):
                    status.update({"memory_bound": True, "scoped": result})
                    paused = status.get("state") == "paused"
                    replay_ready = (
                        not status.get("required")
                        or (status.get("s5") or {}).get("status") == "ok"
                    )
                    if paused or not replay_ready:
                        return
                    if status.get("required"):
                        status.update({"state": "active", "code": "migration_shadow_active"})
                    else:
                        status.update({"state": "active", "code": "fresh_scoped_runtime_active"})
                    return
                elif status is None:
                    return
            await asyncio.sleep(2.0)

    async def _req041_run_scoped_sync(self) -> None:
        while bool(getattr(self, "_req041_scoped_sync_requested", False)):
            self._req041_scoped_sync_requested = False
            result = await self._req041_sync_scoped_now()
            if result.get("ok") is not True:
                status = getattr(self, "req041_migration_status", None)
                if isinstance(status, dict):
                    status.update({"state": "degraded", "code": "scoped_projection_degraded", "scoped": result})
                return

    def _req041_scoped_sync_finished(self, task: Any) -> None:
        self._req041_scoped_sync_task = None
        if not task.cancelled():
            try:
                error = task.exception()
            except Exception:
                error = None
            if error is not None:
                self.req041_scoped_projection_status = {
                    "ok": False, "code": _single_line(error, 120) or "scoped_projection_exception"
                }
        if bool(getattr(self, "_req041_scoped_sync_requested", False)):
            self._req041_schedule_scoped_sync()

    def _req041_schedule_scoped_sync(self) -> None:
        if getattr(self, "req041_scoped_projection_sync", None) is None:
            return
        self.req041_scoped_projection_sync.mark_dirty()
        stop_event = getattr(self, "_stop_event", None)
        if stop_event is not None and callable(getattr(stop_event, "is_set", None)) and stop_event.is_set():
            return
        self._req041_scoped_sync_requested = True
        task = getattr(self, "_req041_scoped_sync_task", None)
        if isinstance(task, asyncio.Task) and not task.done():
            return
        try:
            task = asyncio.get_running_loop().create_task(
                self._req041_run_scoped_sync(), name="req041-scoped-projection-sync"
            )
        except RuntimeError:
            return
        self._req041_scoped_sync_task = task
        task.add_done_callback(self._req041_scoped_sync_finished)

    def _req041_scoped_context_for_user(
        self,
        user: dict[str, Any],
        *,
        kind: str = "private",
        group_id: str = "",
        purpose: str = "memory_read",
    ) -> NamespaceContext | None:
        if not isinstance(user, dict):
            return None
        person_id = str(user.get("unified_person_id") or "").strip()
        subject = str(user.get("identity_subject_id") or user.get("user_id") or "").strip()
        if not person_id or not subject:
            return None
        registry = self._active_unified_person_registry()
        if not registry.matches_person_subject(person_id, subject):
            return None
        synchronizer = getattr(self, "req041_scoped_projection_sync", None)
        if synchronizer is None:
            return None
        active_persona = self._active_persona_scope()
        persona_id = scoped_persona_ref(active_persona)
        safe_group = scoped_group_ref(persona_id, group_id) if kind == "group_member" else ""
        resolution = registry.formal_namespace_for_person(
            person_id, kind=kind, group_id=safe_group,
            policy_version=synchronizer.policy_version,
            migration_epoch=synchronizer.migration_epoch,
            purpose=purpose,
        )
        raw = resolution.get("context") if isinstance(resolution, dict) else None
        if not resolution.get("ok") or not isinstance(raw, dict):
            return None
        context = NamespaceContext(
            kind=kind, persona_id=persona_id, identity_id=person_id, group_id=safe_group,
            assurance=str(raw.get("assurance") or "verified"),
            profile_status=str(raw.get("profile_status") or "active"),
            policy_version=synchronizer.policy_version,
            migration_epoch=synchronizer.migration_epoch,
        )
        return context if not context.errors() else None

    def _req041_scoped_private_context_for_person(
        self,
        person_id: str,
        *,
        purpose: str = "memory_write",
    ) -> NamespaceContext | None:
        synchronizer = getattr(self, "req041_scoped_projection_sync", None)
        if synchronizer is None:
            return None
        clean_person = _single_line(person_id, 80)
        if not clean_person:
            return None
        registry = self._active_unified_person_registry()
        resolution = registry.formal_namespace_for_person(
            clean_person, kind="private",
            policy_version=synchronizer.policy_version,
            migration_epoch=synchronizer.migration_epoch,
            purpose=purpose,
        )
        raw = resolution.get("context") if isinstance(resolution, dict) else None
        if not resolution.get("ok") or not isinstance(raw, dict):
            return None
        context = NamespaceContext(
            kind="private", persona_id=scoped_persona_ref(self._active_persona_scope()),
            identity_id=clean_person, group_id="",
            assurance=str(raw.get("assurance") or "verified"), profile_status="active",
            policy_version=synchronizer.policy_version,
            migration_epoch=synchronizer.migration_epoch,
        )
        return context if not context.errors() else None

    @staticmethod
    def _req041_person_private_aux_key(person_id: str) -> str:
        """Return a stable opaque key for persona-local person-private helpers."""
        clean_person = str(person_id or "").strip()
        if not clean_person:
            return ""
        digest = hashlib.sha256(f"req041-person-private-aux:{clean_person}".encode("utf-8")).hexdigest()
        return f"person:{digest}"

    def _req041_reality_private_binding(
        self,
        user_id: Any,
        *,
        purpose: str = "memory_read",
    ) -> dict[str, Any]:
        """Resolve one mobile/reality operation to a reconciled private person scope."""
        normalized = _single_line(user_id, 120)
        users = self.data.get("users") if isinstance(getattr(self, "data", None), dict) else None
        user = users.get(normalized) if normalized and isinstance(users, dict) else None
        if not isinstance(user, dict):
            return {"ok": False, "code": "private_user_not_managed"}
        context = self._req041_scoped_context_for_user(user, kind="private", purpose=purpose)
        synchronizer = getattr(self, "req041_scoped_projection_sync", None)
        if context is None or synchronizer is None:
            return {"ok": False, "code": "formal_private_identity_required"}
        projection = synchronizer.read_projection(context)
        if not isinstance(projection, dict) or projection.get("ok") is not True:
            return {
                "ok": False,
                "code": str(projection.get("code") or "scoped_projection_not_reconciled")[:120]
                if isinstance(projection, dict) else "scoped_projection_not_reconciled",
            }
        person_id = _single_line(getattr(context, "identity_id", ""), 80)
        store_key = self._req041_person_private_aux_key(person_id)
        if not person_id or not store_key:
            return {"ok": False, "code": "formal_private_identity_required"}
        snapshot = self._req041_relationship_snapshot_view(
            user, source=f"reality_{_single_line(purpose, 40) or 'memory'}",
        )
        return {
            "ok": True,
            "code": "formal_private_identity_bound",
            "context": context,
            "person_id": person_id,
            "store_key": store_key,
            "subject_ref": store_key,
            "user": snapshot if isinstance(snapshot, dict) else user,
        }

    def _req041_erase_person_private_auxiliary_locked(
        self,
        person_id: str,
        subjects: list[str] | tuple[str, ...] = (),
    ) -> dict[str, int]:
        """Erase canonical and exact legacy auxiliary nodes for one person."""
        canonical = self._req041_person_private_aux_key(person_id)
        keys = {canonical} if canonical else set()
        keys.update(_single_line(item, 160) for item in subjects if _single_line(item, 160))
        counts = {"place_cognitive_maps": 0, "reality_touch_outputs": 0}
        for root_name in tuple(counts):
            root = self.data.get(root_name) if isinstance(self.data, dict) else None
            if not isinstance(root, dict):
                continue
            for key in keys:
                if key in root:
                    root.pop(key, None)
                    counts[root_name] += 1
        return counts

    def _req041_scoped_group_context(
        self,
        group_id: str,
        *,
        purpose: str = "rule_write",
    ) -> NamespaceContext | None:
        synchronizer = getattr(self, "req041_scoped_projection_sync", None)
        raw_group = _single_line(group_id, 160)
        if synchronizer is None or not raw_group:
            return None
        persona_id = scoped_persona_ref(self._active_persona_scope())
        context = NamespaceContext(
            kind="group_shared", persona_id=persona_id, identity_id="",
            group_id=scoped_group_ref(persona_id, raw_group), assurance="verified",
            profile_status="active", policy_version=synchronizer.policy_version,
            migration_epoch=synchronizer.migration_epoch,
        )
        policy = AssurancePolicy()
        decision = policy.authorize(context, purpose)
        return context if decision.allowed and not context.errors() else None

    def _req041_persona_global_context(
        self, *, purpose: str = "rule_read"
    ) -> NamespaceContext | None:
        synchronizer = getattr(self, "req041_scoped_projection_sync", None)
        if synchronizer is None:
            return None
        context = NamespaceContext(
            kind="persona_global", persona_id=scoped_persona_ref(self._active_persona_scope()),
            identity_id="", group_id="", assurance="verified", profile_status="active",
            policy_version=synchronizer.policy_version,
            migration_epoch=synchronizer.migration_epoch,
        )
        decision = AssurancePolicy().authorize(context, purpose)
        return context if decision.allowed and not context.errors() else None

    def _req041_erase_scoped_group_data(
        self,
        group_id: str,
        *,
        operation_id: str = "",
        persona_id: str = "",
    ) -> dict[str, Any]:
        synchronizer = getattr(self, "req041_scoped_projection_sync", None)
        if synchronizer is None:
            if self._req041_group_remote_cleanup_required():
                return {"ok": False, "state": "degraded", "code": "scoped_group_erase_unavailable"}
            return {"ok": True, "state": "not_required", "code": "scoped_group_erase_not_required", "count": 0}
        raw_group = _single_line(group_id, 160)
        safe_persona = _single_line(persona_id, 80) or scoped_persona_ref(self._active_persona_scope())
        group_ref = scoped_group_ref(safe_persona, raw_group)
        context = NamespaceContext(
            kind="group_shared", persona_id=safe_persona, identity_id="", group_id=group_ref,
            assurance="verified", profile_status="active",
            policy_version=synchronizer.policy_version,
            migration_epoch=synchronizer.migration_epoch,
        )
        if not raw_group or context.errors():
            return {"ok": False, "state": "rejected", "code": "scoped_group_erase_context_invalid"}
        clean_operation = _single_line(operation_id, 120) or "req041-group-reset-" + uuid.uuid4().hex
        return synchronizer.erase_group_scopes(
            context, operation_id=clean_operation, reason_code="group_reset",
        )

    def _req041_memory_scope_was_bound(self) -> bool:
        """Return whether this persona has ever had a remote scoped bridge bound."""
        data = getattr(self, "data", None)
        state = data.get("_req041_memory_scope_state") if isinstance(data, dict) else None
        return isinstance(state, dict) and bool(state.get("ever_bound"))

    def _req041_mark_memory_scope_bound(self) -> None:
        """Persist remote-binding history so a later outage remains fail-closed."""
        data = getattr(self, "data", None)
        if not isinstance(data, dict):
            return
        state = data.get("_req041_memory_scope_state")
        if not isinstance(state, dict):
            state = {}
            data["_req041_memory_scope_state"] = state
        if state.get("ever_bound"):
            return
        state["ever_bound"] = True
        state["bound_at"] = _now_ts()
        scheduler = getattr(self, "_schedule_data_save", None)
        if callable(scheduler):
            try:
                scheduler(delay=0.35)
            except Exception:
                pass

    def _req041_group_remote_cleanup_required(self) -> bool:
        """Decide whether deleting a group must wait for remote scope erasure.

        A fresh runtime with no remote bridge has never published scoped records,
        so local group cleanup is safe. Once a bridge was bound, an outage must
        continue to fail closed because the remote side may retain group data.
        Legacy migrations also remain fail-closed until their remote cleanup is
        available.
        """
        status = getattr(self, "req041_migration_status", None)
        if not isinstance(status, dict):
            return False
        if status.get("required"):
            if (
                not status.get("memory_bound")
                and not self._req041_memory_scope_was_bound()
            ):
                coordinator = getattr(self, "req041_migration_coordinator", None)
                status_getter = getattr(coordinator, "status", None)
                if callable(status_getter):
                    try:
                        control = status_getter()
                    except Exception:
                        control = {}
                    if isinstance(control, dict) and control.get("memory_version") == "not-detected":
                        return False
            return True
        if not status.get("scoped_required"):
            return False
        if status.get("memory_bound") or self._req041_memory_scope_was_bound():
            return True
        coordinator = getattr(self, "req041_migration_coordinator", None)
        status_getter = getattr(coordinator, "status", None)
        if callable(status_getter):
            try:
                control = status_getter()
            except Exception:
                control = {}
            if isinstance(control, dict) and control.get("source_schema_version") == "req041-fresh-v1":
                return False
        return True

    def _req041_erase_scoped_persona_data(
        self,
        persona_id: str,
        *,
        operation_id: str,
    ) -> dict[str, Any]:
        synchronizer = getattr(self, "req041_scoped_projection_sync", None)
        if synchronizer is None:
            status = getattr(self, "req041_migration_status", None)
            if isinstance(status, dict) and (status.get("required") or status.get("scoped_required")):
                return {"ok": False, "state": "degraded", "code": "scoped_persona_erase_unavailable"}
            return {"ok": True, "state": "not_required", "code": "scoped_persona_erase_not_required"}
        persona_ref = scoped_persona_ref(persona_id)
        context = NamespaceContext(
            kind="persona_global", persona_id=persona_ref, identity_id="", group_id="",
            assurance="verified", profile_status="active",
            policy_version=synchronizer.policy_version,
            migration_epoch=synchronizer.migration_epoch,
        )
        clean_operation = _single_line(operation_id, 120)
        if not clean_operation or context.errors():
            return {"ok": False, "state": "rejected", "code": "scoped_persona_erase_context_invalid"}
        return synchronizer.erase_persona_scopes(
            context, operation_id=clean_operation, reason_code="persona_reset",
        )

    def _req041_group_reset_sagas_locked(self) -> dict[str, dict[str, Any]]:
        sagas = self.data.get("_req041_group_reset_sagas")
        if not isinstance(sagas, dict):
            sagas = {}
            self.data["_req041_group_reset_sagas"] = sagas
        return sagas

    def _req041_finalize_group_reset_locked(self, group_id: str) -> dict[str, Any]:
        normalize = getattr(self, "_normalize_group_identity_id", None)

        def normalized(value: Any) -> str:
            if callable(normalize):
                return _single_line(normalize(value), 160)
            return _single_line(value, 160)

        clean_group = normalized(group_id)
        groups = self.data.get("groups")
        if not isinstance(groups, dict):
            groups = {}
            self.data["groups"] = groups
        matching_keys = [key for key in groups if normalized(key) == clean_group]
        for key in matching_keys:
            groups.pop(key, None)

        changed: dict[str, list[str]] = {}
        for key in (
            "group_whitelist_ids", "group_blacklist_ids",
            "expression_group_learning_source_ids", "expression_group_application_ids",
        ):
            old_values = list(getattr(self, key, []) or [])
            new_values = [
                str(item).strip() for item in old_values
                if str(item).strip() and normalized(item) != clean_group
            ]
            setattr(self, key, new_values)
            _set_into_config(self.config, key, new_values)
            if new_values != old_values:
                changed[key] = new_values
        refresher = getattr(self, "_refresh_expression_voice_profile", None)
        if callable(refresher):
            refresher()
        return {
            "removed_group": bool(matching_keys),
            "removed_whitelist": "group_whitelist_ids" in changed,
            "removed_blacklist": "group_blacklist_ids" in changed,
            "removed_expression_scope": bool(
                {"expression_group_learning_source_ids", "expression_group_application_ids"} & changed.keys()
            ),
        }

    async def _req041_resume_confirmed_group_resets(self) -> dict[str, Any]:
        async with self._data_lock:
            pending = [
                deepcopy(saga) for saga in self._req041_group_reset_sagas_locked().values()
                if isinstance(saga, dict) and saga.get("state") in {"confirmed", "config_pending"}
            ][:32]
        completed = 0
        errors: list[str] = []
        for saga in pending:
            result = await self.reset_group_scoped_data(
                str(saga.get("group_id") or ""),
                operation_id=str(saga.get("operation_id") or ""),
            )
            if result.get("ok") and result.get("state") == "completed":
                completed += 1
            else:
                errors.append(str(result.get("code") or "group_reset_resume_failed")[:120])
        return {
            "ok": not errors,
            "code": "group_reset_resume_complete" if not errors else "group_reset_resume_degraded",
            "pending": len(pending), "completed": completed,
            "error_codes": sorted(set(errors))[:16],
        }

    async def _req041_resume_confirmed_persona_resets(self) -> dict[str, Any]:
        pending: list[dict[str, str]] = []
        async with self._data_lock:
            default_data = getattr(self, "_data_default", None)
            default_marker = (
                default_data.get("_req041_persona_reset_saga")
                if isinstance(default_data, dict) else None
            )
            if isinstance(default_marker, dict) and default_marker.get("state") == "confirmed":
                pending.append({
                    "persona_id": "",
                    "operation_id": str(default_marker.get("operation_id") or ""),
                    "force_default": "1",
                })
            profiles = getattr(self, "_persona_data_profiles", None)
            if isinstance(profiles, dict):
                for raw_persona, profile in profiles.items():
                    marker = profile.get("_req041_persona_reset_saga") if isinstance(profile, dict) else None
                    if isinstance(marker, dict) and marker.get("state") == "confirmed":
                        pending.append({
                            "persona_id": str(raw_persona or ""),
                            "operation_id": str(marker.get("operation_id") or ""),
                            "force_default": "0",
                        })
        completed = 0
        errors: list[str] = []
        for saga in pending[:32]:
            result = await self._reset_current_persona_store(
                saga["persona_id"], rebuild_today=False,
                operation_id=saga["operation_id"],
                _force_default_store=saga["force_default"] == "1",
            )
            if result.get("ok"):
                completed += 1
            else:
                errors.append(str(result.get("code") or "persona_reset_resume_failed")[:120])
        return {
            "ok": not errors,
            "code": "persona_reset_resume_complete" if not errors else "persona_reset_resume_degraded",
            "pending": min(len(pending), 32), "completed": completed,
            "error_codes": sorted(set(errors))[:16],
        }

    def _req041_persist_archive_saga_locked(
        self,
        *,
        sections: Collection[str] | None = None,
        deleted_sections: Collection[str] = (),
        full_scope: str | None = None,
    ) -> None:
        """Durably persist a destructive saga before any cross-store write."""
        normalized_scope = str(full_scope or "").strip() or None
        normalized_deleted = {
            str(section).strip()
            for section in deleted_sections
            if str(section).strip()
        }
        normalized_sections = {
            str(section).strip()
            for section in (sections or ())
            if str(section).strip()
        }
        if normalized_scope is None and sections is None:
            raise ValueError(
                "archive saga sections must be explicit unless full_scope is provided"
            )
        if normalized_scope is not None:
            if normalized_scope != "admin_import_export":
                raise ValueError(
                    "archive saga full_scope must be admin_import_export"
                )
            if sections is not None or normalized_deleted:
                raise ValueError(
                    "archive saga full_scope cannot be combined with sections"
                )
        else:
            # The live mapping is authoritative when an internal caller names a
            # section in both sets. Present values are upserts; absent values are
            # tombstones. Never pass an overlapping request to the writer.
            for section in normalized_sections & normalized_deleted:
                if section in self.data:
                    normalized_deleted.discard(section)
                else:
                    normalized_sections.discard(section)
        if not normalized_sections and not normalized_deleted:
            if normalized_scope is None:
                return
        validator = getattr(self, "_validate_save_request", None)
        if callable(validator):
            validator(
                None if normalized_scope is not None else normalized_sections,
                normalized_deleted,
                normalized_scope,
            )
        active_persona = str(self._active_persona_scope() or "")
        if bool(getattr(self, "enable_multi_persona_mode", False)) and active_persona:
            # Destructive sagas must be durable before touching another store;
            # the persona profile backend exposes only an immediate snapshot API.
            self._write_persona_data_snapshot_sync(active_persona, deepcopy(self.data))
            return
        if normalized_scope is not None:
            self._save_data_now_sync(full_scope="admin_import_export")
        else:
            self._save_data_now_sync(
                sections=normalized_sections,
                deleted_sections=normalized_deleted,
            )

    def _req041_scoped_archive_available(self) -> bool:
        """Return whether a confirmed identity archive can reach Memory safely."""
        synchronizer = getattr(self, "req041_scoped_projection_sync", None)
        if synchronizer is None or not callable(
            getattr(synchronizer, "archive_identity_scopes", None)
        ):
            return False
        status = getattr(self, "req041_migration_status", None)
        if not isinstance(status, dict):
            return False
        if str(status.get("state") or "").strip().lower() in {"degraded", "paused", "stopped"}:
            return False
        return True

    async def _req041_resume_confirmed_person_archives(self) -> dict[str, Any]:
        registry = self._active_unified_person_registry()
        pending = registry.confirmed_person_archives(limit=32)
        completed = 0
        errors: list[str] = []
        for saga in pending:
            result = await self.archive_unified_person(
                saga["person_id"], operation_id=saga["operation_id"],
                confirmation_token=saga["confirmation_token"], dry_run=False,
                actor_id=saga["actor_id"], reason_code=saga["reason_code"],
            )
            if result.get("ok") and result.get("code") == "person_archived":
                completed += 1
            else:
                errors.append(str(result.get("code") or "person_archive_resume_failed")[:120])
        return {
            "ok": not errors,
            "code": "person_archive_resume_complete" if not errors else "person_archive_resume_degraded",
            "pending": len(pending), "completed": completed,
            "error_codes": sorted(set(errors))[:16],
        }

    def _req041_purge_legacy_person_locked(
        self,
        person_id: str,
        subjects: list[str],
        *,
        changed_sections: set[str] | None = None,
    ) -> dict[str, int]:
        """Remove only exact identity-owned legacy nodes; never fuzzy-search text."""
        clean_person = _single_line(person_id, 80)
        subject_set = {_single_line(item, 160) for item in subjects if _single_line(item, 160)}
        counts = {"mapping_entries": 0, "list_entries": 0, "records": 0}
        identity_fields = {
            "user_id", "identity_subject_id", "platform_subject_id", "sender_id", "member_id",
            "linked_qq_user_id", "target_user_id", "qq_user_id",
        }

        def owned(value: Any) -> bool:
            if not isinstance(value, dict):
                return False
            if str(value.get("unified_person_id") or "").strip() == clean_person:
                return True
            return any(
                str(value.get(field) or "").strip() in subject_set
                for field in identity_fields
                if value.get(field) not in (None, "")
            )

        def scrub(value: Any, *, depth: int = 0) -> Any:
            if depth > 10:
                return value
            if isinstance(value, dict):
                for key in list(value):
                    item = value[key]
                    if str(key) == clean_person or str(key) in subject_set or owned(item):
                        value.pop(key, None)
                        counts["mapping_entries"] += 1
                        counts["records"] += 1
                        continue
                    value[key] = scrub(item, depth=depth + 1)
                return value
            if isinstance(value, list):
                kept: list[Any] = []
                for item in value:
                    if owned(item):
                        counts["list_entries"] += 1
                        counts["records"] += 1
                    else:
                        kept.append(scrub(item, depth=depth + 1))
                value[:] = kept
            return value

        for key in list(self.data):
            if key == "unified_person":
                continue
            before = deepcopy(self.data[key])
            scrubbed = scrub(self.data[key])
            self.data[key] = scrubbed
            if changed_sections is not None and before != scrubbed:
                changed_sections.add(str(key))
        return counts

    async def _req041_resume_confirmed_person_purges(self) -> dict[str, Any]:
        registry = self._active_unified_person_registry()
        pending = registry.confirmed_person_purges(limit=32)
        completed = 0
        errors: list[str] = []
        for saga in pending:
            result = await self.purge_unified_person(
                saga["person_id"], operation_id=saga["operation_id"],
                confirmation_token=saga["confirmation_token"], dry_run=False,
                actor_id=saga["actor_id"], reason_code=saga["reason_code"],
            )
            if result.get("ok") and result.get("code") == "person_purged":
                completed += 1
            else:
                errors.append(str(result.get("code") or "person_purge_resume_failed")[:120])
        return {
            "ok": not errors,
            "code": "person_purge_resume_complete" if not errors else "person_purge_resume_degraded",
            "pending": len(pending), "completed": completed,
            "error_codes": sorted(set(errors))[:16],
        }

    def _req041_scoped_private_read_view(self, event: Any, user: dict[str, Any]) -> dict[str, Any]:
        existing = getattr(event, "req041_scoped_private_read_view", None)
        if isinstance(existing, dict):
            return existing
        view = dict(user) if isinstance(user, dict) else user
        synchronizer = getattr(self, "req041_scoped_projection_sync", None)
        context = self._req041_scoped_context_for_user(user, kind="private")
        if synchronizer is None or context is None:
            return view
        projection = synchronizer.read_projection(context)
        if not isinstance(projection, dict) or projection.get("ok") is not True:
            if isinstance(view, dict):
                view["req041_scoped_read_generation"] = "new_unavailable"
                try:
                    setattr(event, "req041_scoped_private_read_view", view)
                except Exception:
                    pass
            return view
        persona_context = self._req041_persona_global_context(purpose="rule_read")
        persona_projection = (
            synchronizer.read_projection(persona_context) if persona_context is not None else None
        )
        view = overlay_private_runtime_view(view, projection, persona_projection)
        if not isinstance(view, dict) or view.get("req041_scoped_read_generation") != "new":
            return view
        try:
            setattr(event, "req041_scoped_private_read_view", view)
        except Exception:
            pass
        return view

    def _req041_scoped_group_read_view(
        self,
        event: Any,
        *,
        group_id: str,
        group: dict[str, Any],
        sender_id: str,
        relationship_user: dict[str, Any] | None,
    ) -> dict[str, Any]:
        existing = getattr(event, "req041_scoped_group_read_view", None)
        if isinstance(existing, dict):
            return existing
        view = deepcopy(group) if isinstance(group, dict) else group
        synchronizer = getattr(self, "req041_scoped_projection_sync", None)
        if synchronizer is None or not isinstance(view, dict):
            return view
        persona_id = scoped_persona_ref(self._active_persona_scope())
        safe_group = scoped_group_ref(persona_id, group_id)
        shared = NamespaceContext(
            kind="group_shared", persona_id=persona_id, identity_id="", group_id=safe_group,
            assurance="verified", profile_status="active", policy_version=synchronizer.policy_version,
            migration_epoch=synchronizer.migration_epoch,
        )
        shared_projection = synchronizer.read_projection(shared)
        persona_context = self._req041_persona_global_context(purpose="rule_read")
        persona_projection = (
            synchronizer.read_projection(persona_context) if persona_context is not None else None
        )
        member_projection = None
        member_context = self._req041_scoped_context_for_user(
            relationship_user or {}, kind="group_member", group_id=group_id, purpose="profile_read"
        )
        if member_context is not None:
            member_projection = synchronizer.read_projection(member_context)
        if not isinstance(shared_projection, dict) or shared_projection.get("ok") is not True:
            view["req041_scoped_read_generation"] = "new_unavailable"
            try:
                setattr(event, "req041_scoped_group_read_view", view)
            except Exception:
                pass
            return view
        view = overlay_group_runtime_view(
            view, shared_projection, sender_id=sender_id, member_projection=member_projection,
            persona_projection=persona_projection,
        )
        if not isinstance(view, dict) or view.get("req041_scoped_read_generation") != "new":
            return view
        view["req041_scoped_read_generation"] = "new"
        try:
            setattr(event, "req041_scoped_group_read_view", view)
        except Exception:
            pass
        return view

    def _req041_replay_finished(self, _task: Any) -> None:
        self._req041_replay_task = None
        if bool(getattr(self, "_req041_replay_requested", False)):
            try:
                self._req041_schedule_replay()
            except RuntimeError:
                status = getattr(self, "req041_migration_status", None)
                if isinstance(status, dict):
                    status.update({"state": "paused", "code": "migration_replay_loop_unavailable"})

    def _req041_relationship_read_view(
        self,
        event: Any,
        user: dict[str, Any],
        *,
        kind: str = "private",
        group_id: str = "",
    ) -> dict[str, Any]:
        existing = getattr(event, "req041_relationship_read_view", None)
        if isinstance(existing, dict):
            return existing
        router = getattr(self, "req041_relationship_read_router", None)
        if not isinstance(user, dict):
            return user
        result: dict[str, Any] = {}
        view = user
        if router is not None:
            event_ref = self._event_message_id(event)
            if not event_ref:
                event_ref = f"{getattr(event, 'unified_msg_origin', '')}:{uuid.uuid4().hex}"
            result = router.begin(user, event_ref=event_ref, kind=kind, group_id=group_id)
            view = result.get("user") if isinstance(result.get("user"), dict) else user
        production_view = view
        view = self._lab_fixture_relationship_view(event, view)
        if router is None and view is production_view:
            return user
        try:
            setattr(event, "req041_relationship_read_view", view)
            if router is not None:
                setattr(event, "req041_read_chain_id", str(result.get("chain_id") or ""))
                setattr(event, "req041_read_generation", str(result.get("generation") or "legacy"))
                setattr(event, "req041_read_identity_id", str(result.get("identity_id") or ""))
        except Exception:
            pass
        return view

    def _req041_relationship_snapshot_view(
        self,
        user: dict[str, Any],
        *,
        source: str,
    ) -> dict[str, Any]:
        """Take one short-lived private relationship view for background decisions."""
        if not isinstance(user, dict):
            return user
        if user.get("_req041_relationship_snapshot_resolved") is True:
            return user
        relationship_view = user
        router = getattr(self, "req041_relationship_read_router", None)
        if router is not None and user.get("req041_read_generation") != "new":
            result = router.begin(
                user,
                event_ref=f"snapshot:{_single_line(source, 60) or 'relationship'}:{uuid.uuid4().hex}",
                kind="private",
            )
            chain_id = str(result.get("chain_id") or "")
            try:
                relationship_view = (
                    result.get("user") if isinstance(result.get("user"), dict) else user
                )
            finally:
                if chain_id:
                    try:
                        router.finish(chain_id)
                    except Exception:
                        pass
        scoped_getter = getattr(self, "_req041_scoped_private_read_view", None)
        return (
            scoped_getter(None, relationship_view)
            if callable(scoped_getter) else relationship_view
        )

    def _req041_group_sender_is_human(self, event: AstrMessageEvent) -> bool:
        if not self._event_is_inbound_chat_message(event):
            return False
        sender_id = self._event_sender_id(event)
        self_id = self._event_self_id(event)
        if not sender_id or (self_id and sender_id == self_id):
            return False
        raw = self._event_raw_payload(event)
        sender = raw.get("sender") if isinstance(raw.get("sender"), dict) else {}
        message_obj = getattr(event, "message_obj", None)
        message_sender = getattr(message_obj, "sender", None) if message_obj is not None else None

        def field(owner: Any, name: str) -> Any:
            if isinstance(owner, dict):
                return owner.get(name)
            try:
                return getattr(owner, name, None)
            except Exception:
                return None

        for owner in (raw, sender, message_sender):
            if owner is None:
                continue
            for key in ("is_bot", "bot", "is_system", "system"):
                value = field(owner, key)
                if value is True or str(value or "").strip().lower() in {"1", "true", "yes", "bot", "system"}:
                    return False
            role = str(field(owner, "role") or field(owner, "sender_type") or "").strip().lower()
            if role in {"assistant", "bot", "system", "service"}:
                return False
        return True

    def _req041_prepare_group_affinity_candidate(
        self,
        event: AstrMessageEvent,
        *,
        group_id: str,
        relationship_user: dict[str, Any] | None,
        scene_trigger: str,
        forwarded: bool,
    ) -> dict[str, Any] | None:
        if (
            not isinstance(relationship_user, dict)
            or str(getattr(event, "req041_read_generation", "") or "") != "new"
            or getattr(self, "req041_dual_write_producer", None) is None
            or getattr(self, "req041_migration_replay", None) is None
            or getattr(self, "req041_relationship_store", None) is None
            or not bool(runtime_persona_setting(self, 'enable_custom_relationship_stage_policy', False))
        ):
            return None
        direction = "at_bot" if scene_trigger == "at_bot" else "reply_bot" if scene_trigger == "reply_bot" else ""
        context = self._req041_scoped_context_for_user(
            relationship_user,
            kind="group_member",
            group_id=group_id,
            purpose="relationship_write",
        )
        if context is None:
            return None
        candidate = prepare_group_affinity_candidate(
            context,
            raw_group_id=group_id,
            allowlist=getattr(self, "group_relationship_affinity_allowlist", ()),
            enabled=bool(getattr(self, "enable_group_relationship_affinity", False)),
            inbound_event_id=self._event_message_id(event),
            directed_by=direction,
            legacy_user_key=str(relationship_user.get("user_id") or ""),
            inbound=self._event_is_inbound_chat_message(event),
            human_sender=self._req041_group_sender_is_human(event),
            forwarded=bool(forwarded),
            echo=False,
            historical=False,
        )
        if isinstance(candidate, dict):
            setattr(event, "req041_group_affinity_candidate", candidate)
        return candidate

    async def _req041_settle_confirmed_group_affinity(self, event: AstrMessageEvent) -> None:
        candidate = getattr(event, "req041_group_affinity_candidate", None)
        if not isinstance(candidate, dict) or bool(candidate.get("settled")):
            return
        if not self._reaction_expression_primary_reply_confirmed(
            event, require_segmented_complete=True,
        ):
            return
        live_allowlist = normalize_group_allowlist(
            getattr(self, "group_relationship_affinity_allowlist", ())
        )
        if (
            not bool(runtime_persona_setting(self, 'enable_custom_relationship_stage_policy', False))
            or not bool(getattr(self, "enable_group_relationship_affinity", False))
            or str(candidate.get("raw_group_id") or "") not in live_allowlist
        ):
            candidate["settled"] = True
            candidate["result_code"] = "group_affinity_config_revoked"
            return
        store = getattr(self, "req041_relationship_store", None)
        if not isinstance(store, RelationshipAccountStore):
            return
        admission = await asyncio.to_thread(
            admit_confirmed_group_affinity,
            candidate,
            store,
            reply_succeeded=True,
            requested_delta=4,
            group_daily_net_cap=int(getattr(self, "group_relationship_daily_net_cap", 2)),
            group_window_seconds=int(getattr(self, "group_relationship_window_minutes", 30)) * 60,
            group_window_absolute_cap=int(getattr(self, "group_relationship_window_absolute_cap", 1)),
            group_person_daily_absolute_cap=int(
                getattr(self, "group_relationship_person_daily_absolute_cap", 4)
            ),
            group_scope_daily_absolute_cap=int(
                getattr(self, "group_relationship_scope_daily_absolute_cap", 20)
            ),
            group_event_cap=4,
        )
        if admission is None:
            return
        candidate["settled"] = True
        candidate["result_code"] = admission.code
        if admission.admitted_delta == 0:
            return
        context = NamespaceContext(**candidate.get("context", {}))
        user_key = str(candidate.get("legacy_user_key") or "")
        async with self._data_lock:
            users = self.data.get("users") if isinstance(self.data.get("users"), dict) else {}
            user = users.get(user_key) if isinstance(users, dict) else None
            if (
                not isinstance(user, dict)
                or str(user.get("unified_person_id") or "") != context.identity_id
            ):
                candidate["settled"] = False
                raise RuntimeError("group_affinity_legacy_subject_mismatch")
            result = self._apply_relationship_event(
                user,
                admission.admitted_delta,
                reason_code="direct_group_interaction",
                event_id=admission.event_id,
                now=_now_ts(),
                req041_group_admission_event_id=admission.event_id,
            )
            candidate["legacy_result_code"] = str(result.get("code") or "")
            if result.get("changed"):
                self._schedule_data_save(sections={"users"})

    async def _req041_run_replay_batch(self) -> None:
        worker = getattr(self, "req041_migration_replay", None)
        if worker is None:
            return
        while bool(getattr(self, "_req041_replay_requested", False)):
            self._req041_replay_requested = False
            result = await asyncio.to_thread(worker.run_batch)
            if result.get("status") == "paused":
                status = getattr(self, "req041_migration_status", None)
                if isinstance(status, dict):
                    status.update({
                        "state": "paused",
                        "code": str(result.get("error_code") or "migration_replay_failed")[:120],
                        "s5": result,
                    })
                return
            runtime = getattr(self, "req041_migration_status", None)
            coordinator = getattr(self, "req041_migration_coordinator", None)
            outbox = getattr(self, "req041_migration_outbox", None)
            if isinstance(runtime, dict) and coordinator is not None and outbox is not None:
                try:
                    stability_fn = advance_migration_stability
                except NameError:
                    from migration_stability import advance_migration_stability as stability_fn
                control = coordinator.status()
                scoped = runtime.get("scoped") if isinstance(runtime.get("scoped"), dict) else {}
                stability = await asyncio.to_thread(
                    stability_fn,
                    coordinator=coordinator, outbox=outbox,
                    migration_epoch=str(control.get("migration_epoch") or ""),
                    replay_ok=True, scoped_ok=bool(scoped.get("ok")),
                    memory_bound=bool(runtime.get("memory_bound")),
                    observability=self.req041_observability,
                    boot_ref=str(getattr(self, "_req041_runtime_boot_ref", f"boot-{id(self)}")),
                )
                control = coordinator.status()
                runtime.update({"phase": control.get("phase", runtime.get("phase")),
                                "checkpoint": control.get("checkpoint", runtime.get("checkpoint")),
                                "stability": stability})
            if int(result.get("count") or 0) > 0 or int(result.get("recovered") or 0) > 0:
                self._req041_replay_requested = True

    async def _req041_initialize_automatic_migration(self) -> None:
        try:
            metrics_type = Req041Observability
        except NameError:  # Standalone migration harnesses load selected methods only.
            from req041_observability import Req041Observability as metrics_type
        if not isinstance(getattr(self, "req041_observability", None), metrics_type):
            self.req041_observability = metrics_type()
        if not str(getattr(self, "_req041_runtime_boot_ref", "") or ""):
            self._req041_runtime_boot_ref = f"boot-{id(self)}"
        coordinator = getattr(self, "req041_migration_coordinator", None)
        outbox = getattr(self, "req041_migration_outbox", None)
        if coordinator is None or outbox is None:
            self.req041_migration_status = {
                "required": False, "state": "degraded", "code": "migration_runtime_unavailable"
            }
            return
        sources = self._req041_migration_source_files()
        presence_getter = getattr(self, "_memory_companion_presence", None)
        try:
            presence = presence_getter() if callable(presence_getter) else {}
        except Exception:
            presence = {}
        memory_version = _single_line((presence or {}).get("version"), 32) or "not-detected"
        companion_version = _single_line((getattr(self, "plugin_identity", {}) or {}).get("version"), 32) or "unknown"
        try:
            current_status = coordinator.status()
            is_fresh_runtime = current_status.get("source_schema_version") == "req041-fresh-v1"
            if not sources and not current_status:
                current_status = await asyncio.to_thread(
                    coordinator.initialize_fresh_runtime,
                    policy_version="req041-v1",
                    target_schema_version="req041-v1",
                    companion_version=companion_version,
                    memory_version=memory_version,
                )
                is_fresh_runtime = True
            if is_fresh_runtime:
                await self._req041_initialize_fresh_scoped_runtime(current_status)
                return
            async with self._data_lock:
                source_inventory = await asyncio.to_thread(
                    inspect_migration_sources,
                    self.data_dir,
                    sources,
                )
                status = await asyncio.to_thread(
                    coordinator.start_or_resume,
                    source_files=sources,
                    policy_version="req041-v1",
                    source_schema_version=source_inventory["source_schema_version"],
                    target_schema_version="req041-v1",
                    companion_version=companion_version,
                    memory_version=memory_version,
                    source_inventory=source_inventory,
                )
            if status.get("phase") == "S1" and status.get("state") != "paused":
                await asyncio.to_thread(coordinator.capture_compatibility, self._req041_compatibility_snapshot())
                status = coordinator.status()
            epoch = str(status.get("migration_epoch") or "")
            policy = str(status.get("policy_version") or "")
            await asyncio.to_thread(outbox.begin_epoch, epoch, policy_version=policy)
            self.req041_dual_write_producer = MigrationDualWriteProducer(
                outbox=outbox,
                coordinator=coordinator,
                migration_epoch=epoch,
                policy_version=policy,
                on_enqueued=self._req041_schedule_replay,
            )
            if status.get("state") == "paused":
                self.req041_migration_status = {
                    "required": True, "state": "paused", "code": status.get("error_code") or "migration_paused",
                    "phase": status.get("phase", "S0"), "dual_write": "capturing_while_paused",
                }
                return
            if status.get("phase") == "S2":
                status = await asyncio.to_thread(coordinator.transition, "S3", checkpoint="durable_outbox_active")

            backfill_result: dict[str, Any] = {"ok": True, "code": "s4_not_required"}
            if status.get("phase") in {"S3", "S4"}:
                try:
                    async with self._data_lock:
                        legacy_snapshots = self._req041_legacy_snapshots_locked()
                    backfiller = await asyncio.to_thread(
                        MigrationBackfill,
                        coordinator=coordinator,
                        relationship_path=Path(self.data_dir) / "req041_relationship.db",
                        migration_epoch=epoch,
                        policy_version=policy,
                        outbox=outbox,
                    )
                    backfill_counts: dict[str, Any] = {
                        "phase": status.get("phase", "S3"), "migrated": 0, "idempotent": 0,
                        "pending": 0, "conflicts": 0, "formal_identities": 0, "legacy_users": 0,
                        "identity_baselines": 0,
                        "source_scopes": len(legacy_snapshots),
                    }
                    for source_scope, legacy_snapshot in legacy_snapshots:
                        scoped_counts = await asyncio.to_thread(
                            backfiller.run,
                            legacy_snapshot,
                            source_scope=source_scope,
                        )
                        backfill_counts["phase"] = scoped_counts["phase"]
                        for count_key in (
                            "migrated", "idempotent", "pending", "conflicts",
                            "formal_identities", "legacy_users",
                            "identity_baselines",
                        ):
                            backfill_counts[count_key] += int(scoped_counts[count_key])
                    self.req041_migration_backfill = backfiller
                    self.req041_relationship_store = backfiller.relationships
                    backfill_result = {"ok": True, "code": "s4_shadow_backfilled", **backfill_counts}
                    status = coordinator.status()
                except Exception as backfill_exc:
                    backfill_result = {
                        "ok": False,
                        "code": _single_line(backfill_exc, 120) or "s4_backfill_failed",
                    }
                    logger.warning(
                        "REQ-041 S4 Shadow 回填失败，继续使用 legacy 路径: %s",
                        _single_line(backfill_exc, 160),
                    )

            relationship_store = getattr(self, "req041_relationship_store", None)
            if relationship_store is None and status.get("phase") in {"S4", "S5", "S6", "S7", "S8", "S9"}:
                backfiller = await asyncio.to_thread(
                    MigrationBackfill,
                    coordinator=coordinator,
                    relationship_path=Path(self.data_dir) / "req041_relationship.db",
                    migration_epoch=epoch,
                    policy_version=policy,
                    outbox=outbox,
                )
                self.req041_migration_backfill = backfiller
                self.req041_relationship_store = backfiller.relationships
                relationship_store = backfiller.relationships
                relationship_store.set_observability(self.req041_observability)

            replay_result: dict[str, Any] = {"status": "skipped", "code": "s5_not_ready"}
            if relationship_store is not None and backfill_result.get("ok"):
                if status.get("phase") == "S4":
                    status = await asyncio.to_thread(
                        coordinator.transition, "S5", checkpoint="ordered_shadow_replay_active"
                    )
                if status.get("phase") in {"S5", "S6", "S7", "S8", "S9"}:
                    active_registry = self._active_unified_person_registry()
                    replay_worker = MigrationReplayWorker(
                        outbox=outbox,
                        coordinator=coordinator,
                        relationship_store=relationship_store,
                        registry=active_registry,
                        registry_resolver=self._req041_registry_for_person,
                        legacy_relationship_resolver=self._req041_legacy_relationship_state,
                        legacy_pending_resolver=self._req041_resolve_legacy_pending_for_person,
                        enable_gap_recovery=True,
                        migration_epoch=epoch,
                        policy_version=policy,
                        observability=self.req041_observability,
                    )
                    self.req041_migration_replay = replay_worker
                    await asyncio.to_thread(
                        outbox.set_epoch_state, epoch, "replaying", checkpoint="s5_replay_batch"
                    )
                    replay_result = await asyncio.to_thread(replay_worker.run_batch)
                    if replay_result.get("status") == "ok" and status.get("phase") == "S5":
                        status = await asyncio.to_thread(
                            coordinator.transition, "S6", checkpoint="per_identity_relationship_cutover_enabled"
                        )
                        replay_result = await asyncio.to_thread(replay_worker.run_batch)
                    if replay_result.get("status") == "ok":
                        await asyncio.to_thread(
                            outbox.set_epoch_state, epoch, "active", checkpoint="s5_reconciled"
                        )
                    else:
                        status = coordinator.status()
                    if replay_result.get("status") == "ok":
                        self.req041_relationship_read_router = MigrationRelationshipReadRouter(
                            coordinator=coordinator,
                            relationship_store=relationship_store,
                            registry_resolver=self._req041_registry_for_person,
                            migration_epoch=epoch,
                            policy_version=policy,
                            observability=self.req041_observability,
                        )
                        await asyncio.to_thread(
                            coordinator.prune_read_chains, older_than=_now_ts() - 3600
                        )

            remote = {"ok": False, "state": "degraded", "code": "memory_bridge_unavailable"}
            bridge_getter = getattr(self, "_memory_companion_bridge", None)
            bridge = bridge_getter() if callable(bridge_getter) else None
            binder = getattr(self, "_memory_companion_bind_namespace_epoch", None)
            if bridge is not None and callable(binder):
                remote = binder(
                    bridge,
                    operation_id=f"req041-bind-{epoch}",
                    migration_epoch=epoch,
                    policy_version=policy,
                )
            scoped_result: dict[str, Any] = {
                "ok": False, "code": "namespace_scoped_api_not_bound", "scopes": []
            }
            archive_resume: dict[str, Any] = {
                "ok": True, "code": "person_archive_resume_not_required",
                "pending": 0, "completed": 0, "error_codes": [],
            }
            purge_resume: dict[str, Any] = {
                "ok": True, "code": "person_purge_resume_not_required",
                "pending": 0, "completed": 0, "error_codes": [],
            }
            group_reset_resume: dict[str, Any] = {
                "ok": True, "code": "group_reset_resume_not_required",
                "pending": 0, "completed": 0, "error_codes": [],
            }
            persona_reset_resume: dict[str, Any] = {
                "ok": True, "code": "persona_reset_resume_not_required",
                "pending": 0, "completed": 0, "error_codes": [],
            }
            if remote.get("ok") and bridge is not None:
                self._req041_mark_memory_scope_bound()
                self._req041_scoped_bridge = bridge
                self.req041_scoped_projection_sync = ScopedProjectionSynchronizer(
                    read=lambda namespace, **kwargs: self._memory_companion_read_scoped_record(
                        bridge, namespace, **kwargs
                    ),
                    list_records=lambda namespace, **kwargs: self._memory_companion_list_scoped_records(
                        bridge, namespace, **kwargs
                    ),
                    upsert=lambda namespace, **kwargs: self._memory_companion_upsert_scoped_record(
                        bridge, namespace, **kwargs
                    ),
                    tombstone=lambda namespace, **kwargs: self._memory_companion_tombstone_scoped_record(
                        bridge, namespace, **kwargs
                    ),
                    tombstone_identity_scopes=lambda namespace, **kwargs: self._memory_companion_tombstone_scoped_identity_scopes(
                        bridge, namespace, **kwargs
                    ),
                    erase_group_scopes=lambda namespace, **kwargs: self._memory_companion_erase_scoped_group_scopes(
                        bridge, namespace, **kwargs
                    ),
                    erase_persona_scopes=lambda namespace, **kwargs: self._memory_companion_erase_scoped_persona_scopes(
                        bridge, namespace, **kwargs
                    ),
                    migration_epoch=epoch,
                    policy_version=policy,
                    observability=self.req041_observability,
                )
                resumer = getattr(self, "_req041_resume_confirmed_person_archives", None)
                if callable(resumer):
                    archive_resume = await resumer()
                purge_resumer = getattr(self, "_req041_resume_confirmed_person_purges", None)
                if archive_resume.get("ok") and callable(purge_resumer):
                    purge_resume = await purge_resumer()
                group_resumer = getattr(self, "_req041_resume_confirmed_group_resets", None)
                if archive_resume.get("ok") and purge_resume.get("ok") and callable(group_resumer):
                    group_reset_resume = await group_resumer()
                persona_resumer = getattr(self, "_req041_resume_confirmed_persona_resets", None)
                if (
                    archive_resume.get("ok") and purge_resume.get("ok")
                    and group_reset_resume.get("ok") and callable(persona_resumer)
                ):
                    persona_reset_resume = await persona_resumer()
                if archive_resume.get("ok") and purge_resume.get("ok") and group_reset_resume.get("ok") and persona_reset_resume.get("ok"):
                    scoped_result = await self._req041_sync_scoped_now()
                else:
                    scoped_result = {
                        "ok": False, "code": "lifecycle_resume_degraded", "scopes": [],
                    }
            self.req041_migration_status = {
                "required": True,
                "state": "active" if remote.get("ok") and archive_resume.get("ok") and purge_resume.get("ok") and group_reset_resume.get("ok") and persona_reset_resume.get("ok") and scoped_result.get("ok") and backfill_result.get("ok") and replay_result.get("status") == "ok" else (
                    "paused" if status.get("state") == "paused" else "degraded"
                ),
                "code": (
                    "migration_shadow_active"
                    if remote.get("ok") and archive_resume.get("ok") and purge_resume.get("ok") and group_reset_resume.get("ok") and persona_reset_resume.get("ok") and scoped_result.get("ok") and backfill_result.get("ok") and replay_result.get("status") == "ok"
                    else str(
                        backfill_result.get("code") if not backfill_result.get("ok")
                        else replay_result.get("error_code") if replay_result.get("status") == "paused"
                        else archive_resume.get("code") if not archive_resume.get("ok")
                        else purge_resume.get("code") if not purge_resume.get("ok")
                        else group_reset_resume.get("code") if not group_reset_resume.get("ok")
                        else persona_reset_resume.get("code") if not persona_reset_resume.get("ok")
                        else scoped_result.get("code") if remote.get("ok") and not scoped_result.get("ok")
                        else remote.get("code") or "migration_degraded"
                    )[:120]
                ),
                "phase": status.get("phase", "S5"),
                "memory_bound": bool(remote.get("ok")),
                "checkpoint": status.get("checkpoint", ""),
                "s4": backfill_result,
                "s5": replay_result,
                "dual_write": "capturing",
                "scoped": scoped_result,
                "archive_resume": archive_resume,
                "purge_resume": purge_resume,
                "group_reset_resume": group_reset_resume,
                "persona_reset_resume": persona_reset_resume,
            }
            try:
                stability_fn = advance_migration_stability
            except NameError:
                from migration_stability import advance_migration_stability as stability_fn
            stability = await asyncio.to_thread(
                stability_fn,
                coordinator=coordinator, outbox=outbox, migration_epoch=epoch,
                replay_ok=replay_result.get("status") == "ok",
                scoped_ok=bool(scoped_result.get("ok")), memory_bound=bool(remote.get("ok")),
                observability=self.req041_observability,
                boot_ref=self._req041_runtime_boot_ref,
            )
            status = coordinator.status()
            self.req041_migration_status.update({
                "phase": status.get("phase", self.req041_migration_status.get("phase")),
                "checkpoint": status.get("checkpoint", self.req041_migration_status.get("checkpoint")),
                "stability": stability,
            })
        except Exception as exc:
            status = coordinator.status()
            self.req041_migration_status = {
                "required": bool(status),
                "state": "paused" if status.get("state") == "paused" else "degraded",
                "code": _single_line(exc, 120) or "migration_startup_failed",
                "phase": status.get("phase", "S0") if status else "S0",
            }
            logger.warning(
                "REQ-041 自动迁移启动失败，继续使用官方 legacy 路径: %s",
                _single_line(exc, 160),
            )

    async def _req041_initialize_fresh_scoped_runtime(
        self,
        status: dict[str, Any],
    ) -> None:
        """Bring a source-free install directly into the normal scoped runtime."""
        coordinator = self.req041_migration_coordinator
        outbox = self.req041_migration_outbox
        epoch = _single_line(status.get("migration_epoch"), 128)
        policy = _single_line(status.get("policy_version"), 64)
        if not epoch or not policy:
            raise RuntimeError("fresh_runtime_contract_invalid")
        await asyncio.to_thread(outbox.begin_epoch, epoch, policy_version=policy)
        relationship_store = RelationshipAccountStore(
            Path(self.data_dir) / "req041_relationship.db",
            active_migration_epoch=epoch,
            observability=self.req041_observability,
        )
        self.req041_relationship_store = relationship_store
        self.req041_dual_write_producer = MigrationDualWriteProducer(
            outbox=outbox,
            coordinator=coordinator,
            migration_epoch=epoch,
            policy_version=policy,
            on_enqueued=self._req041_schedule_replay,
        )
        replay_worker = MigrationReplayWorker(
            outbox=outbox,
            coordinator=coordinator,
            relationship_store=relationship_store,
            registry=self._active_unified_person_registry(),
            registry_resolver=self._req041_registry_for_person,
            legacy_relationship_resolver=self._req041_legacy_relationship_state,
            legacy_pending_resolver=self._req041_resolve_legacy_pending_for_person,
            enable_gap_recovery=True,
            migration_epoch=epoch,
            policy_version=policy,
            observability=self.req041_observability,
        )
        self.req041_migration_replay = replay_worker
        await asyncio.to_thread(outbox.set_epoch_state, epoch, "active", checkpoint="fresh_runtime_active")
        replay_result = await asyncio.to_thread(replay_worker.run_batch)
        self.req041_relationship_read_router = MigrationRelationshipReadRouter(
            coordinator=coordinator,
            relationship_store=relationship_store,
            registry_resolver=self._req041_registry_for_person,
            migration_epoch=epoch,
            policy_version=policy,
            observability=self.req041_observability,
        )

        remote = {"ok": False, "state": "degraded", "code": "memory_bridge_unavailable"}
        bridge_getter = getattr(self, "_memory_companion_bridge", None)
        bridge = bridge_getter() if callable(bridge_getter) else None
        binder = getattr(self, "_memory_companion_bind_namespace_epoch", None)
        if bridge is not None and callable(binder):
            remote = binder(
                bridge,
                operation_id=f"req041-bind-{epoch}",
                migration_epoch=epoch,
                policy_version=policy,
            )
        scoped_result: dict[str, Any] = {
            "ok": False, "code": "namespace_scoped_api_not_bound", "scopes": []
        }
        if remote.get("ok") and bridge is not None:
            self._req041_mark_memory_scope_bound()
            self._req041_scoped_bridge = bridge
            self.req041_scoped_projection_sync = ScopedProjectionSynchronizer(
                read=lambda namespace, **kwargs: self._memory_companion_read_scoped_record(
                    bridge, namespace, **kwargs
                ),
                list_records=lambda namespace, **kwargs: self._memory_companion_list_scoped_records(
                    bridge, namespace, **kwargs
                ),
                upsert=lambda namespace, **kwargs: self._memory_companion_upsert_scoped_record(
                    bridge, namespace, **kwargs
                ),
                tombstone=lambda namespace, **kwargs: self._memory_companion_tombstone_scoped_record(
                    bridge, namespace, **kwargs
                ),
                tombstone_identity_scopes=lambda namespace, **kwargs: self._memory_companion_tombstone_scoped_identity_scopes(
                    bridge, namespace, **kwargs
                ),
                erase_group_scopes=lambda namespace, **kwargs: self._memory_companion_erase_scoped_group_scopes(
                    bridge, namespace, **kwargs
                ),
                erase_persona_scopes=lambda namespace, **kwargs: self._memory_companion_erase_scoped_persona_scopes(
                    bridge, namespace, **kwargs
                ),
                migration_epoch=epoch,
                policy_version=policy,
                observability=self.req041_observability,
            )
            scoped_result = await self._req041_sync_scoped_now()
        ready = bool(
            remote.get("ok")
            and scoped_result.get("ok")
            and replay_result.get("status") == "ok"
        )
        self.req041_migration_status = {
            "required": False,
            "scoped_required": True,
            "state": "active" if ready else "degraded",
            "code": "fresh_scoped_runtime_active" if ready else str(
                replay_result.get("error_code")
                if replay_result.get("status") != "ok"
                else scoped_result.get("code") if remote.get("ok")
                else remote.get("code") or "fresh_scoped_runtime_degraded"
            )[:120],
            "phase": "S9",
            "memory_bound": bool(remote.get("ok")),
            "checkpoint": status.get("checkpoint", "fresh_runtime_initialized"),
            "dual_write": "capturing",
            "s5": replay_result,
            "scoped": scoped_result,
        }

