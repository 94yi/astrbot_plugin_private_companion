# -*- coding: utf-8 -*-
"""人格路由 / 统一身份域。

由 tools/split_main_domain.py 从 main.py 机械抽取（44 个方法 / 1055 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPlugin）。
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import time
import unicodedata
import uuid
from .helpers import _now_ts, _single_line
from .main_shared import (
    _ACTIVE_PERSONA_ID,
    _PERSONA_PROFILE_FORBIDDEN_FILENAME_CHARS,
    _PERSONA_SETTING_MANIFEST,
    _WINDOWS_RESERVED_FILENAME_STEMS,
)
from .migration_scoped_projection import scoped_persona_ref
from .person_context_contract import (
    CONTRACT_NAME as PERSON_CONTRACT_NAME,
    CONTRACT_VERSION as PERSON_CONTRACT_VERSION,
    P3_CONTRACT_NAME,
    P3_CONTRACT_VERSION,
    contract_self_check as person_contract_self_check,
)
from .persona_config import (
    PERSONA_SETTINGS_KEY,
    PERSONA_SETTINGS_REVISION_KEY,
    PERSONA_SETTINGS_SCHEMA_VERSION,
    PERSONA_SETTINGS_VERSION_KEY,
    PersonaConfigError,
    PersonaSettingsTypeError,
    detach_persona_settings,
    migrate_persona_profile,
    runtime_persona_setting,
)
from .persona_sqlite_store import PersonaSqliteStoreRegistry, read_persona_store_snapshot_read_only
from .plugin_identity import PLUGIN_ID
from .story_authority import story_legacy_operation
from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from .logging_util import get_module_logger

logger = get_module_logger(__name__)

class PrivateCompanionPluginPersonaRoutingMixin:
    """人格路由 / 统一身份域（从 PrivateCompanionPlugin 拆出）。"""

    def _persona_scope_manifest(self) -> dict[str, dict[str, Any]]:
        return _PERSONA_SETTING_MANIFEST

    def _persona_settings_for_id(self, persona_id: Any = "") -> dict[str, Any]:
        pid = self._sanitize_persona_id(persona_id or _ACTIVE_PERSONA_ID.get())
        if not pid:
            return {}
        primary = self._primary_persona_id()
        if pid == primary:
            return {}
        profile = self._ensure_persona_profile(pid)
        settings = profile.get(PERSONA_SETTINGS_KEY)
        return dict(settings) if isinstance(settings, dict) else {}

    def persona_setting(self, key: str, default: Any = None, persona_id: Any = "") -> Any:
        """Short explicit accessor for persona-aware runtime code."""
        return self.get_persona_setting(key, persona_id=persona_id, default=default)

    def _persona_profile_stem(self, persona_id: Any) -> str:
        """Return a reversible, cross-platform-safe filename stem."""
        pid = self._sanitize_persona_id(persona_id)
        encoded_parts: list[str] = []
        for character in pid:
            if (
                character in _PERSONA_PROFILE_FORBIDDEN_FILENAME_CHARS
                or unicodedata.category(character).startswith("C")
            ):
                encoded_parts.extend(
                    f"%{byte:02X}" for byte in character.encode("utf-8")
                )
            else:
                encoded_parts.append(character)
        stem = "".join(encoded_parts)
        if stem.partition(".")[0].upper() in _WINDOWS_RESERVED_FILENAME_STEMS and stem:
            stem = f"%{ord(stem[0]):02X}{stem[1:]}"
        return stem

    def _persona_profile_filename(self, persona_id: Any) -> str:
        """Return the legacy JSON filename for one logical persona ID."""
        return f"{self._persona_profile_stem(persona_id)}.json"

    def _persona_profile_db_filename(self, persona_id: Any) -> str:
        """Return the authoritative secondary-persona SQLite filename."""
        return f"{self._persona_profile_stem(persona_id)}.db"

    def _persona_id_from_profile_path(self, path: Path) -> str:
        filename = path.name
        suffix = path.suffix.lower()
        if suffix not in {".json", ".db"}:
            return ""
        try:
            decoded = unquote(filename[: -len(suffix)], encoding="utf-8", errors="strict")
        except (UnicodeDecodeError, ValueError):
            return ""
        return self._sanitize_persona_id(decoded)

    def _persona_profile_path(self, persona_id: str) -> Path:
        return Path(self._persona_profiles_dir) / self._persona_profile_filename(persona_id)

    def _persona_profile_db_path(self, persona_id: str) -> Path:
        return Path(self._persona_profiles_dir) / self._persona_profile_db_filename(persona_id)

    def _persona_profile_store_paths(self, persona_id: Any) -> tuple[Path, Path]:
        pid = self._sanitize_persona_id(persona_id)
        return self._persona_profile_path(pid), self._persona_profile_db_path(pid)

    def _persona_sqlite_registry(self) -> PersonaSqliteStoreRegistry:
        registry = getattr(self, "_persona_sqlite_store_registry", None)
        if not isinstance(registry, PersonaSqliteStoreRegistry):
            registry = PersonaSqliteStoreRegistry()
            self._persona_sqlite_store_registry = registry
        return registry

    def _prepare_legacy_persona_payload(
        self,
        persona_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Validate and migrate a legacy JSON profile before SQLite commit."""
        settings = payload.get(PERSONA_SETTINGS_KEY)
        if settings is not None and not isinstance(settings, dict):
            raise PersonaSettingsTypeError("persona_settings must be an object")
        return migrate_persona_profile(
            payload,
            manifest=self._persona_scope_manifest(),
            target_version=PERSONA_SETTINGS_SCHEMA_VERSION,
            persona_id=persona_id,
            legacy_bot_name=(
                self._persona_display_name_for_id(persona_id)
                if not isinstance(settings, dict)
                or not str(settings.get("bot_name") or "").strip()
                else ""
            ),
        )

    def _persona_display_name_for_id(self, persona_id: Any) -> str:
        """Return an AstrBot persona label without requiring async I/O."""
        pid = self._sanitize_persona_id(persona_id)
        manager = getattr(getattr(self, "context", None), "persona_manager", None)
        for item in list(getattr(manager, "personas", None) or []):
            if isinstance(item, dict):
                item_id = item.get("persona_id") or item.get("id") or item.get("name")
                label = item.get("name") or item.get("label") or item_id
            else:
                item_id = getattr(item, "persona_id", None) or getattr(item, "id", None) or getattr(item, "name", None)
                label = getattr(item, "name", None) or getattr(item, "label", None) or item_id
            if self._sanitize_persona_id(item_id) == pid:
                return _single_line(label, 80) or pid
        return pid

    @story_legacy_operation("persona.store.reset")
    async def _reset_current_persona_store(
        self,
        persona_id: Any = "",
        *,
        rebuild_today: bool = True,
        operation_id: str = "",
        _force_default_store: bool = False,
    ) -> dict[str, Any]:
        multi_enabled = bool(getattr(self, "enable_multi_persona_mode", False)) and not _force_default_store
        requested = self._sanitize_persona_id(persona_id)
        active = self._sanitize_persona_id(self._active_persona_scope())
        pid = ""
        if multi_enabled:
            configured = self._configured_multi_persona_ids()
            pid = (
                requested
                or active
                or self._sanitize_persona_id(getattr(self, "_page_current_persona_id", ""))
                or self._primary_persona_id()
                or (configured[0] if configured else "")
            )
            if not pid or pid not in set(configured):
                return {"ok": False, "message": "当前人格不在已启用的多人格列表中"}

        token = None
        if multi_enabled and active != pid:
            token = self._activate_persona_id(pid)
            if token is None:
                return {"ok": False, "message": "无法激活要重置的人格"}

        backup_path: Path | None = None
        generation = 1
        try:
            await self._flush_scheduled_data_save()
            scoped_reset: dict[str, Any] = {
                "ok": True, "state": "not_required", "code": "scoped_persona_erase_not_required",
            }
            synchronizer = getattr(self, "req041_scoped_projection_sync", None)
            migration_status = getattr(self, "req041_migration_status", None)
            if synchronizer is None and isinstance(migration_status, dict) and (
                migration_status.get("required") or migration_status.get("scoped_required")
            ):
                return {"ok": False, "message": "人格分域清理暂不可用", "code": "scoped_persona_erase_unavailable"}
            if synchronizer is not None:
                persona_ref = scoped_persona_ref(pid)
                async with self._data_lock:
                    group_sagas = self.data.get("_req041_group_reset_sagas")
                    if isinstance(group_sagas, dict) and group_sagas:
                        return {
                            "ok": False, "message": "存在未完成的群删除事务，请等待恢复完成后再重置人格",
                            "code": "group_reset_in_progress",
                        }
                    marker = self.data.get("_req041_persona_reset_saga")
                    if marker is not None and not isinstance(marker, dict):
                        return {"ok": False, "message": "人格重置恢复记录损坏", "code": "persona_reset_saga_invalid"}
                    clean_operation = _single_line(operation_id, 120)
                    if isinstance(marker, dict):
                        marker_operation = _single_line(marker.get("operation_id"), 120)
                        if (
                            marker.get("state") != "confirmed"
                            or _single_line(marker.get("persona_id"), 80) != persona_ref
                            or (clean_operation and clean_operation != marker_operation)
                            or not marker_operation
                        ):
                            return {"ok": False, "message": "人格重置恢复记录冲突", "code": "persona_reset_saga_conflict"}
                        clean_operation = marker_operation
                    else:
                        clean_operation = clean_operation or "req041-persona-reset-" + uuid.uuid4().hex
                        self.data["_req041_persona_reset_saga"] = {
                            "operation_id": clean_operation,
                            "persona_id": persona_ref,
                            "source_persona_id": pid,
                            "state": "confirmed",
                            "created_at": _now_ts(),
                        }
                        self._req041_persist_archive_saga_locked(
                            sections={"_req041_persona_reset_saga"},
                        )
                scoped_reset = self._req041_erase_scoped_persona_data(
                    pid, operation_id=clean_operation,
                )
                if not scoped_reset.get("ok"):
                    return {
                        "ok": False,
                        "message": "人格分域清理失败，已保留本地资料并将在启动时重试",
                        "code": str(scoped_reset.get("code") or "scoped_persona_erase_failed")[:120],
                        "operation_id": clean_operation,
                    }
            async with self._data_lock:
                previous = deepcopy(self.data)
                backup_snapshot = deepcopy(previous)
                backup_snapshot.pop("_req041_persona_reset_saga", None)
                lifecycle = previous.get("persona_lifecycle")
                if not isinstance(lifecycle, dict):
                    lifecycle = {}
                try:
                    previous_generation = max(
                        1,
                        int(lifecycle.get("generation", 1) or 1),
                    )
                except (TypeError, ValueError):
                    previous_generation = 1
                generation = previous_generation + 1
                backup_path = self._write_persona_reset_backup_sync(pid, backup_snapshot)

                replacement = self._new_store()
                ensure_defaults = getattr(self, "_ensure_store_defaults", None)
                if callable(ensure_defaults):
                    replacement = ensure_defaults(replacement)
                # Reset life data without resetting the persona's independent
                # configuration or its optimistic-concurrency metadata.
                for settings_key in (
                    "persona_settings",
                    "persona_settings_schema_version",
                    "persona_settings_revision",
                ):
                    if settings_key in previous:
                        replacement[settings_key] = deepcopy(previous[settings_key])
                replacement["persona_lifecycle"] = {
                    "generation": generation,
                    "reset_at": _now_ts(),
                    "previous_backup": str(backup_path),
                }
                self.data = replacement
                if bool(runtime_persona_setting(self, "default_enable_configured_targets", False)):
                    sync_targets = getattr(self, "_sync_configured_targets", None)
                    if callable(sync_targets):
                        sync_targets()
                try:
                    if multi_enabled:
                        self._write_persona_data_snapshot_sync(
                            pid,
                            deepcopy(self.data),
                        )
                        clear_dirty = getattr(self, "_clear_scheduled_data_save_dirty", None)
                        if callable(clear_dirty):
                            clear_dirty(persona_id=pid)
                        else:
                            dirty = getattr(self, "_persona_data_save_dirty", None)
                            if isinstance(dirty, set):
                                dirty.discard(pid)
                    else:
                        self._write_data_snapshot_sync(deepcopy(self.data))
                        clear_dirty = getattr(self, "_clear_scheduled_data_save_dirty", None)
                        if callable(clear_dirty):
                            clear_dirty()
                        else:
                            self._data_save_dirty = False
                except Exception:
                    self.data = previous
                    if multi_enabled:
                        self._write_persona_data_snapshot_sync(pid, previous)
                    else:
                        self._write_data_snapshot_sync(previous)
                    raise

            self._reset_persona_prompt_caches(pid)
            bookshelf_tokens = getattr(self, "_bookshelf_access_tokens", None)
            if isinstance(bookshelf_tokens, dict):
                bookshelf_tokens.clear()

            state: dict[str, Any] = {}
            plan: dict[str, Any] = {}
            rebuild_error = ""
            if rebuild_today:
                try:
                    state, plan, _ = await self._rebuild_today_after_reset()
                except Exception as exc:
                    rebuild_error = _single_line(exc, 180)
                    logger.warning(
                        "当前人格资料已重置，但今日数据重建失败: persona=%s error=%s",
                        pid or "single",
                        rebuild_error,
                        exc_info=True,
                    )
            return {
                "ok": True,
                "persona_id": pid,
                "generation": generation,
                "backup_path": str(backup_path or ""),
                "state": state,
                "plan": plan,
                "rebuild_error": rebuild_error,
                "external_memory_preserved": synchronizer is None,
                "non_req041_external_memory_preserved": True,
                "scoped_memory_reset": bool(synchronizer is not None and scoped_reset.get("ok")),
                "scoped_cleanup": scoped_reset,
            }
        finally:
            if token is not None:
                self._deactivate_persona_for_event(token)

    def _persona_profile_ids(self, *, strict: bool = False) -> list[str]:
        ids = self._configured_multi_persona_ids(strict=strict)
        profiles = getattr(self, "_persona_data_profiles", {})
        if strict and not isinstance(profiles, dict):
            raise PersonaConfigError(
                "persona profile cache cannot be enumerated"
            )
        if isinstance(profiles, dict):
            for pid in profiles:
                if strict and not isinstance(pid, str):
                    raise PersonaConfigError(
                        "persona profile cache contains a non-text id"
                    )
                clean = self._sanitize_persona_id(pid)
                if strict and pid.strip() not in {"", clean}:
                    raise PersonaConfigError(
                        "persona profile cache contains a non-canonical id"
                    )
                if clean and clean not in ids:
                    ids.append(clean)
                elif strict and not clean:
                    raise PersonaConfigError(
                        "persona profile enumeration contains an invalid id"
                    )
        try:
            profiles_dir = Path(self._persona_profiles_dir)
            if strict:
                try:
                    root_stat = profiles_dir.lstat()
                except FileNotFoundError:
                    paths = []
                else:
                    if not stat.S_ISDIR(root_stat.st_mode):
                        raise PersonaConfigError(
                            "persona profile root is not a regular directory"
                        )
                    paths = sorted(
                        profiles_dir.iterdir(),
                        key=lambda item: item.name,
                    )
                candidates = [
                    path
                    for path in paths
                    if path.suffix.lower() in {".db", ".json"}
                ]
            else:
                candidates = [
                    path
                    for pattern in ("*.db", "*.json")
                    for path in profiles_dir.glob(pattern)
                ]
            for path in candidates:
                if strict:
                    entry_stat = path.lstat()
                    if not stat.S_ISREG(entry_stat.st_mode):
                        raise PersonaConfigError(
                            "persona profile entry is not a regular file"
                        )
                clean = self._persona_id_from_profile_path(path)
                if strict and not clean:
                    raise PersonaConfigError(
                        "persona profile entry cannot be identified"
                    )
                if strict:
                    expected_name = (
                        self._persona_profile_db_filename(clean)
                        if path.suffix == ".db"
                        else self._persona_profile_filename(clean)
                    )
                    if path.name != expected_name:
                        raise PersonaConfigError(
                            "persona profile entry is not canonical"
                        )
                if clean and clean not in ids:
                    ids.append(clean)
        except Exception:
            if strict:
                raise PersonaConfigError(
                    "persona profile enumeration is unavailable"
                ) from None
        return ids

    def _persona_profile_snapshot_if_exists(self, persona_id: Any) -> dict[str, Any] | None:
        """Read an existing profile without creating one as a side effect."""
        pid = self._sanitize_persona_id(persona_id)
        if not pid:
            return None
        profiles = getattr(self, "_persona_data_profiles", {})
        if isinstance(profiles, dict) and isinstance(profiles.get(pid), dict):
            return profiles[pid]
        if not self._secondary_persona_store_exists(pid):
            return None
        try:
            payload = self._load_secondary_persona_store_sync(pid).data
        except Exception:
            return None
        return payload if isinstance(payload, dict) else None

    def _persona_profile_snapshot_read_only(
        self,
        persona_id: Any,
    ) -> dict[str, Any] | None:
        """Inspect persisted profile state without migration or initialization."""
        pid = self._sanitize_persona_id(persona_id)
        if not pid:
            raise PersonaConfigError(
                "read-only persisted profile requires a persona id"
            )
        legacy_path, database_path = self._persona_profile_store_paths(pid)
        return read_persona_store_snapshot_read_only(
            persona_id=pid,
            legacy_json_path=legacy_path,
            sqlite_path=database_path,
        )

    def _persona_config_exists(self, persona_id: Any) -> bool:
        pid = self._sanitize_persona_id(persona_id)
        if not pid or pid == self._primary_persona_id():
            return bool(pid)
        errors = getattr(self, "_persona_profile_errors", {})
        if isinstance(errors, dict) and pid in errors:
            return False
        profile = self._persona_profile_snapshot_if_exists(pid)
        if not isinstance(profile, dict):
            return False
        settings = profile.get(PERSONA_SETTINGS_KEY)
        if not isinstance(settings, dict) or not _single_line(settings.get("bot_name"), 80):
            return False
        try:
            revision = int(profile.get(PERSONA_SETTINGS_REVISION_KEY) or 0)
        except (TypeError, ValueError):
            revision = 0
        if revision > 0 or any(key != "bot_name" for key in settings):
            return True
        factory = getattr(self, "_new_store", None)
        if not callable(factory):
            return False
        try:
            baseline = factory()
            ensure_defaults = getattr(self, "_ensure_store_defaults", None)
            if callable(ensure_defaults):
                baseline = ensure_defaults(baseline)
            candidate = deepcopy(profile)
            for key in (
                PERSONA_SETTINGS_KEY,
                PERSONA_SETTINGS_VERSION_KEY,
                PERSONA_SETTINGS_REVISION_KEY,
            ):
                candidate.pop(key, None)
                baseline.pop(key, None)
            for payload in (candidate, baseline):
                for key in tuple(payload):
                    if payload.get(key) in (None, "", [], {}) and key not in (
                        baseline if payload is candidate else candidate
                    ):
                        payload.pop(key, None)
            return candidate != baseline
        except Exception:
            return False

    def _persona_config_profile_ids(self) -> list[str]:
        primary = self._primary_persona_id()
        candidates: list[str] = []
        profiles = getattr(self, "_persona_data_profiles", {})
        if isinstance(profiles, dict):
            candidates.extend(map(str, profiles))
        try:
            profiles_dir = Path(self._persona_profiles_dir)
            for pattern in ("*.db", "*.json"):
                candidates.extend(
                    self._persona_id_from_profile_path(path)
                    for path in profiles_dir.glob(pattern)
                )
        except Exception:
            pass
        result: list[str] = []
        for candidate in candidates:
            pid = self._sanitize_persona_id(candidate)
            if (
                pid
                and pid != primary
                and pid not in result
                and self._persona_config_exists(pid)
            ):
                result.append(pid)
        return result

    def _persona_window_bindings(self) -> dict[str, str]:
        # Compatibility surface only. AstrBot is the sole routing authority.
        return {}

    def _persona_window_bindings_store_path(self) -> Path:
        configured = str(getattr(self, "_persona_window_bindings_file", "") or "").strip()
        if configured:
            return Path(configured)
        data_dir = str(getattr(self, "data_dir", "") or "").strip()
        if data_dir:
            return Path(data_dir) / "persona_window_bindings.json"
        profiles_dir = Path(str(getattr(self, "_persona_profiles_dir", "persona_profiles")))
        return profiles_dir.parent / "persona_window_bindings.json"

    def _retire_legacy_persona_routing_sync(self) -> dict[str, Any]:
        """Back up legacy plugin-owned routes without using them at runtime."""
        config_bindings = self._cfg_raw(
            getattr(self, "config", {}), "multi_persona_window_bindings", {}
        )
        config_bindings = dict(config_bindings) if isinstance(config_bindings, dict) else {}
        path = self._persona_window_bindings_store_path()
        file_bindings: dict[str, Any] = {}
        file_error = ""
        if path.is_file():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(payload, dict) and isinstance(payload.get("bindings"), dict):
                    file_bindings = dict(payload["bindings"])
                elif isinstance(payload, dict):
                    file_bindings = dict(payload)
                else:
                    file_error = "legacy_binding_root_invalid"
            except Exception as exc:
                file_error = _single_line(exc, 160) or "legacy_binding_read_failed"
        merged = {
            _single_line(window, 240): self._sanitize_persona_id(persona_id)
            for window, persona_id in {**config_bindings, **file_bindings}.items()
            if _single_line(window, 240) and self._sanitize_persona_id(persona_id)
        }
        backup_path = path.with_name("persona_window_bindings.retired.json")
        if (merged or file_error) and not backup_path.exists():
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = backup_path.with_name(
                f".{backup_path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
            )
            try:
                temporary.write_text(
                    json.dumps(
                        {
                            "version": 1,
                            "retired_at": time.time(),
                            "reason": "astrbot_is_persona_routing_authority",
                            "config_bindings": config_bindings,
                            "file_bindings": file_bindings,
                            "source_file": str(path),
                            "source_file_error": file_error,
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                os.replace(temporary, backup_path)
            finally:
                temporary.unlink(missing_ok=True)
        status = {
            "ignored": bool(merged or file_error),
            "count": len(merged),
            "backup_path": str(backup_path) if backup_path.exists() else "",
            "warning_code": "legacy_persona_binding_ignored" if merged or file_error else "",
            "source_file_preserved": path.is_file(),
            "source_file_error": file_error,
        }
        self._legacy_persona_window_bindings = merged
        self._legacy_persona_routing_status = status
        if status["ignored"]:
            logger.warning(
                "已停用插件窗口人格路由，AstrBot 为唯一权威: count=%s backup=%s error=%s",
                len(merged),
                status["backup_path"] or "-",
                file_error or "-",
            )
        return status

    def _persona_id_for_event(self, event: Any) -> tuple[str, str]:
        """Return a cached plugin scope without consulting legacy bindings."""
        umo = str(getattr(event, "unified_msg_origin", "") or "").strip()
        cached = getattr(event, "_private_companion_persona_route_decision", None)
        pid = self._sanitize_persona_id(
            cached.get("plugin_persona_id") if isinstance(cached, dict) else ""
        )
        enabled = set(self._configured_multi_persona_ids())
        if pid not in enabled:
            pid = self._primary_persona_id()
        return pid, umo

    def _retire_deleted_persona_store_sync(self, persona_id: Any) -> dict[str, Any]:
        pid = self._sanitize_persona_id(persona_id)
        if not pid or pid == self._primary_persona_id():
            return {"persona_id": pid, "removed": [], "failed": []}
        removed: list[str] = []
        failed: list[str] = []
        legacy_path, database_path = self._persona_profile_store_paths(pid)
        backups: list[str] = []
        for path in (
            legacy_path,
            database_path,
            database_path.with_name(database_path.name + "-wal"),
            database_path.with_name(database_path.name + "-shm"),
        ):
            if not path.is_file():
                continue
            backup = self._backup_deleted_persona_store_sync(pid, path)
            if backup is None:
                failed.append(str(path))
                continue
            backups.append(str(backup))
            try:
                path.unlink()
                removed.append(str(path))
            except Exception as exc:
                failed.append(str(path))
                logger.warning("已删除人格档案清理失败，保留原文件: persona=%s path=%s error=%s", pid, path, _single_line(exc, 160))
        if not failed:
            registry = getattr(self, "_persona_sqlite_store_registry", None)
            discard = getattr(registry, "discard", None)
            if callable(discard):
                try:
                    discard(database_path)
                except Exception:
                    pass
            profiles = getattr(self, "_persona_data_profiles", None)
            if isinstance(profiles, dict):
                profiles.pop(pid, None)
            errors = getattr(self, "_persona_profile_errors", None)
            if isinstance(errors, dict):
                errors.pop(pid, None)
            self._reset_persona_prompt_caches(pid)
            if self._sanitize_persona_id(getattr(self, "_page_current_persona_id", "")) == pid:
                self._page_current_persona_id = self._primary_persona_id()
        return {"persona_id": pid, "removed": removed, "failed": failed, "backups": backups}

    def _persona_profile_route_status(self, persona_id: Any) -> tuple[bool, str]:
        pid = self._sanitize_persona_id(persona_id)
        primary = self._primary_persona_id()
        if not pid:
            return False, "persona_id_missing"
        if pid not in set(self._configured_multi_persona_ids()):
            return False, "persona_not_enabled"
        if pid == primary:
            return True, "primary"
        errors = getattr(self, "_persona_profile_errors", {})
        if isinstance(errors, dict) and pid in errors:
            return False, "persona_profile_degraded"
        if not self._persona_config_exists(pid):
            return False, "persona_config_missing"
        return True, "configured"

    @staticmethod
    def _persona_routing_warning_is_active(item: Any) -> bool:
        if not isinstance(item, dict):
            return False
        status = _single_line(item.get("status"), 16).lower()
        return not status or status == "active"

    @staticmethod
    def _persona_routing_warning_family(code: Any, channel: Any = "") -> str:
        normalized_code = _single_line(code, 100).lower()
        normalized_channel = _single_line(channel, 24).lower()
        if normalized_code == "persona.route.legacy_binding_ignored":
            return "legacy_binding"
        if normalized_channel == "passive" or normalized_code in {
            "persona.route.passive_primary_fallback",
        }:
            return "passive_delivery"
        if normalized_channel == "proactive" or normalized_code.startswith("persona.route.proactive_"):
            return "proactive_delivery"
        if normalized_code == "persona.route.plugin_persona_unspecified":
            return f"{normalized_channel or 'unknown'}_delivery"
        return normalized_code or "unknown"

    async def _activate_persona_for_event_context(self, event: Any) -> tuple[Any, str]:
        if not bool(getattr(self, "enable_multi_persona_mode", False)):
            # Single-persona mode follows AstrBot's effective session persona.
            # An empty plugin-specific ID is valid and should not create a
            # persistent troubleshooting warning.
            if self._primary_persona_id():
                await self._resolve_persona_routing_warnings(
                    channel="passive",
                    window_key=getattr(event, "unified_msg_origin", ""),
                    warning_families={"passive_delivery"},
                )
            return None, ""
        active = _ACTIVE_PERSONA_ID.get()
        if active:
            return None, active
        cached = getattr(event, "_private_companion_persona_route_decision", None)
        if isinstance(cached, dict):
            pid = self._sanitize_persona_id(cached.get("plugin_persona_id"))
            if pid:
                return _ACTIVE_PERSONA_ID.set(pid), pid

        primary = self._primary_persona_id()
        resolved = await self._astrbot_effective_persona_for_event(event)
        astrbot_persona = self._sanitize_persona_id(resolved.get("persona_id"))
        legacy_bindings = getattr(self, "_legacy_persona_window_bindings", {})
        legacy_persona = self._sanitize_persona_id(
            legacy_bindings.get(str(resolved.get("umo") or ""), "")
            if isinstance(legacy_bindings, dict)
            else ""
        )
        if legacy_persona and legacy_persona != astrbot_persona:
            await self._record_persona_routing_warning(
                code="persona.route.legacy_binding_ignored",
                channel="passive",
                disposition="ignored",
                reason_code="astrbot_is_routing_authority",
                window_key=resolved.get("umo"),
                requested_persona_id=legacy_persona,
                resolved_persona_id=astrbot_persona,
                active_persona_id=astrbot_persona,
            )
        ready, route_reason = self._persona_profile_route_status(astrbot_persona)
        if resolved.get("explicit_none"):
            ready, route_reason = False, "astrbot_persona_explicit_none"
        elif not astrbot_persona:
            ready, route_reason = False, "astrbot_persona_unresolved"
        elif not resolved.get("exists"):
            ready, route_reason = False, "astrbot_persona_missing"
        pid = astrbot_persona if ready else primary
        if not pid:
            await self._record_persona_routing_warning(
                code="persona.route.passive_primary_fallback",
                channel="passive",
                disposition="fallback_unavailable",
                reason_code="primary_persona_invalid",
                window_key=resolved.get("umo"),
                requested_persona_id=astrbot_persona,
            )
            return None, ""
        primary_ready, primary_reason = self._persona_profile_route_status(primary)
        if not ready and not primary_ready:
            await self._record_persona_routing_warning(
                code="persona.route.passive_primary_fallback",
                channel="passive",
                disposition="fallback_unavailable",
                reason_code=f"{route_reason}:{primary_reason}",
                window_key=resolved.get("umo"),
                requested_persona_id=astrbot_persona,
                resolved_persona_id=primary,
            )
            return None, ""

        decision = {
            "astrbot_persona_id": astrbot_persona,
            "plugin_persona_id": pid,
            "source": resolved.get("source"),
            "reason_code": route_reason,
            "fallback": not ready,
            "umo": resolved.get("umo"),
        }
        try:
            setattr(event, "_private_companion_persona_route_decision", decision)
            setattr(event, "private_companion_astrbot_persona_id", astrbot_persona)
            setattr(event, "private_companion_persona_id", pid)
            setattr(event, "private_companion_persona_window", "")
            setattr(event, "private_companion_persona_conflict", {})
        except Exception:
            pass
        if not ready:
            await self._record_persona_routing_warning(
                code="persona.route.passive_primary_fallback",
                channel="passive",
                disposition="fallback",
                reason_code=route_reason,
                window_key=resolved.get("umo"),
                requested_persona_id=astrbot_persona,
                resolved_persona_id=primary,
                active_persona_id=pid,
            )
        else:
            await self._resolve_persona_routing_warnings(
                channel="passive",
                window_key=resolved.get("umo"),
                warning_families={"passive_delivery"},
            )
        self._ensure_persona_profile(pid)
        return _ACTIVE_PERSONA_ID.set(pid), pid

    def _activate_persona_for_event(self, event: Any) -> tuple[Any, str]:
        """Legacy synchronous activation uses only a prior async decision."""
        if not bool(getattr(self, "enable_multi_persona_mode", False)):
            return None, ""
        pid, _ = self._persona_id_for_event(event)
        if not pid:
            return None, ""
        self._ensure_persona_profile(pid)
        return _ACTIVE_PERSONA_ID.set(pid), pid

    def _activate_persona_id(self, persona_id: Any, *, allow_inactive: bool = False) -> Any:
        pid = self._sanitize_persona_id(persona_id)
        if not pid or not bool(getattr(self, "enable_multi_persona_mode", False)):
            return None
        profile_errors = getattr(self, "_persona_profile_errors", {})
        if isinstance(profile_errors, dict) and pid in profile_errors and not allow_inactive:
            return None
        if not allow_inactive and pid not in set(self._configured_multi_persona_ids()):
            return None
        if pid != self._primary_persona_id() and not self._persona_config_exists(pid):
            return None
        self._ensure_persona_profile(pid)
        return _ACTIVE_PERSONA_ID.set(pid)

    def _deactivate_persona_for_event(self, token: Any) -> None:
        if token is not None:
            _ACTIVE_PERSONA_ID.reset(token)

    def _reset_persona_prompt_caches(self, *persona_ids: Any) -> None:
        for attr, value in (
            ("_default_persona_prompt_cache", ""),
            ("_default_persona_prompt_cache_at", 0.0),
            ("_default_persona_prompt_cache_umo", ""),
            ("_default_persona_prompt_cache_persona_id", ""),
            ("_default_persona_prompt_cache_by_scope", {}),
        ):
            try:
                setattr(self, attr, deepcopy(value))
            except Exception:
                pass

        ids = {
            pid
            for pid in (self._sanitize_persona_id(value) for value in persona_ids)
            if pid
        }
        cache = getattr(self, "_passive_light_injection_cache", None)
        if not isinstance(cache, dict) or "text" in cache or not ids:
            self._passive_light_injection_cache = {}
            return
        next_cache = dict(cache)
        for pid in ids:
            next_cache.pop(pid, None)
        self._passive_light_injection_cache = next_cache

    def _multi_persona_status(self) -> dict[str, Any]:
        enabled = bool(getattr(self, "enable_multi_persona_mode", False))
        primary = self._primary_persona_id()
        configured_profiles = self._persona_config_profile_ids()
        profile_labels: dict[str, str] = {}
        if primary:
            profile_labels[primary] = _single_line(
                getattr(self, "bot_name", "") or primary,
                80,
            )
        for pid in configured_profiles:
            profile = self._persona_profile_snapshot_if_exists(pid)
            settings = profile.get(PERSONA_SETTINGS_KEY) if isinstance(profile, dict) else {}
            profile_labels[pid] = _single_line(
                settings.get("bot_name") if isinstance(settings, dict) else "",
                80,
            ) or pid
        return {
            "enabled": enabled,
            "primary": primary,
            "enabled_ids": self._configured_multi_persona_ids(),
            "configured_profiles": configured_profiles,
            "profiles": [primary, *configured_profiles] if primary else configured_profiles,
            "profile_labels": profile_labels,
            "window_bindings": {},
            "window_conflicts": {},
            "window_bindings_revision": 0,
            "routing_authority": "astrbot",
            "legacy_routing": deepcopy(getattr(self, "_legacy_persona_routing_status", {})),
            "primary_setup": {
                "required": bool(
                    getattr(self, "_multi_persona_primary_requires_configuration", False)
                    or getattr(self, "_multi_persona_primary_invalid", False)
                    or (getattr(self, "_multi_persona_enable_requested", False) and not primary)
                ),
                "invalid": bool(getattr(self, "_multi_persona_primary_invalid", False)),
                "legacy_candidate": self._sanitize_persona_id(
                    getattr(self, "_legacy_multi_persona_primary_id_candidate", "")
                ),
                "legacy_mismatch": deepcopy(
                    getattr(self, "_multi_persona_primary_id_mismatch", {})
                ),
            },
            "profile_errors": deepcopy(getattr(self, "_persona_profile_errors", {})),
            "settings_migration": deepcopy(getattr(self, "_persona_settings_migration_status", {})),
            "deleted_persona_reconciliation": deepcopy(
                getattr(self, "_persona_deleted_reconciliation_status", {})
            ),
        }

    def _persona_config_state(self, persona_id: Any) -> dict[str, Any]:
        pid = self._sanitize_persona_id(persona_id)
        primary = self._primary_persona_id()
        if not pid:
            pid = primary
        if not pid:
            raise PersonaConfigError("人格 ID 不能为空")
        is_primary = pid == primary
        if not is_primary and not self._persona_config_exists(pid):
            raise PersonaConfigError("该人格尚未创建独立配置")
        profile = self._ensure_persona_profile(pid)
        raw = {} if is_primary else deepcopy(profile.get(PERSONA_SETTINGS_KEY) or {})
        effective = self.effective_persona_settings(pid, include_common=False)
        manifest = self._persona_scope_manifest()
        sources = {
            key: (
                "primary"
                if is_primary
                else "persona"
                if key in raw
                else "primary"
            )
            for key, entry in manifest.items()
            if entry.get("scope") == "persona"
        }
        sensitive = {key for key, entry in manifest.items() if entry.get("sensitive")}
        redacted_effective = {
            key: ("***" if key in sensitive and value not in (None, "", [], {}) else value)
            for key, value in effective.items()
        }
        redacted_raw = {
            key: ("***" if key in sensitive and value not in (None, "", [], {}) else value)
            for key, value in raw.items()
            if key in manifest and manifest[key].get("scope") == "persona"
        }
        return {
            "persona_id": pid,
            "is_primary": is_primary,
            "enabled": pid in set(self._configured_multi_persona_ids()),
            "degraded": pid in set(getattr(self, "_persona_profile_errors", {})),
            "degraded_reason": str(getattr(self, "_persona_profile_errors", {}).get(pid, "")),
            "schema_version": int(profile.get(PERSONA_SETTINGS_VERSION_KEY) or PERSONA_SETTINGS_SCHEMA_VERSION),
            "revision": int(profile.get(PERSONA_SETTINGS_REVISION_KEY) or 0),
            "settings": redacted_effective,
            "raw_settings": redacted_raw,
            "sources": sources,
        }

    @staticmethod
    def _persona_setting_invalidates_runtime_cache(
        key: str,
        manifest: dict[str, dict[str, Any]],
    ) -> bool:
        entry = manifest.get(str(key)) or {}
        lowered = str(key).lower()
        return bool(entry.get("identity")) or any(
            marker in lowered
            for marker in (
                "provider",
                "prompt",
                "reference",
                "worldbook",
                "knowledge_source",
                "voice",
                "tts_",
            )
        )

    def _persona_detach_preview(self, persona_id: Any) -> dict[str, Any]:
        pid = self._sanitize_persona_id(persona_id)
        primary = self._primary_persona_id()
        if not pid or pid == primary:
            return {"ok": False, "code": "persona_detach_target_invalid", "message": "主人格不需要脱离跟随"}
        if not self._persona_config_exists(pid):
            return {"ok": False, "code": "persona_config_missing", "message": "该人格尚未创建独立配置，不能执行脱离"}
        profile = self._ensure_persona_profile(pid)
        revision = int(profile.get(PERSONA_SETTINGS_REVISION_KEY) or 0)
        settings = profile.get(PERSONA_SETTINGS_KEY) or {}
        detached = detach_persona_settings(settings, self._primary_persona_config(), manifest=self._persona_scope_manifest())
        existing = sorted(set(detached) & set(settings))
        missing = sorted(set(detached) - set(settings))
        digest = hashlib.sha256(
            json.dumps({"persona_id": pid, "revision": revision, "settings": detached}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return {
            "ok": True,
            "persona_id": pid,
            "revision": revision,
            "existing_override_keys": existing,
            "existing_override_count": len(existing),
            "follow_primary_keys": missing,
            "follow_primary_count": len(missing),
            "final_settings_count": len(detached),
            # Kept for compatibility with clients built before split statistics.
            "missing_keys": missing,
            "materialized_count": len(detached),
            "preview_hash": digest,
        }

    def _unified_persona_domain(self) -> str:
        """Return a stable, opaque identity domain for the active persona."""
        if not bool(getattr(self, "enable_multi_persona_mode", False)):
            return ""
        persona_id = self._sanitize_persona_id(self._effective_plugin_persona_id())
        if not persona_id:
            return ""
        digest = hashlib.sha256(persona_id.encode("utf-8")).hexdigest()[:16]
        return f"persona:{digest}"

    def _unified_persona_scoped_value(self, value: Any, *, limit: int = 120) -> str:
        maximum = max(40, min(160, int(limit or 120)))
        base = _single_line(value, maximum)
        persona_domain = self._unified_persona_domain()
        if not base or not persona_domain:
            return base
        available = maximum - len(persona_domain) - 1
        if len(base) > available:
            base_hash = hashlib.sha256(base.encode("utf-8")).hexdigest()[:12]
            prefix_length = max(1, available - len(base_hash) - 1)
            base = f"{base[:prefix_length]}:{base_hash}"
        return f"{base}:{persona_domain}"

    @staticmethod
    def _unified_wire_group_scope(platform: Any, group_id: Any) -> str:
        """Match Memory's persona-neutral group scope wire contract."""
        platform_name = _single_line(platform, 40).lower()
        group_key = _single_line(group_id, 120)
        if not platform_name or not group_key:
            return ""
        return _single_line(f"group:{platform_name}:{group_key}", 80)

    def unified_person_contract_status(self) -> dict[str, Any]:
        issues = list(person_contract_self_check())
        return {
            "available": not issues,
            "state": "ready" if not issues else "degraded",
            "degraded": bool(issues),
            "contract_name": PERSON_CONTRACT_NAME,
            "contract_version": PERSON_CONTRACT_VERSION,
            "p3_contract_name": P3_CONTRACT_NAME,
            "p3_contract_version": P3_CONTRACT_VERSION,
            "warnings": issues,
            "registry": self._active_unified_person_registry().status(),
        }

    def _unified_person_registry_status(self) -> dict[str, Any]:
        return self._active_unified_person_registry().status()

    def _unified_person_event_identity(
        self,
        event: Any | None = None,
        *,
        subject_id: str = "",
        subject_namespace: str = "",
    ) -> dict[str, str]:
        sender_id = _single_line(subject_id, 160)
        if not sender_id and event is not None:
            sender_getter = getattr(self, "_event_sender_id", None)
            if callable(sender_getter):
                try:
                    sender_id = _single_line(sender_getter(event), 160)
                except Exception:
                    sender_id = ""
            if not sender_id:
                try:
                    sender_id = _single_line(event.get_sender_id(), 160)
                except Exception:
                    sender_id = ""
        platform = ""
        if event is not None:
            try:
                platform = _single_line(event.get_platform_name(), 80)
            except Exception:
                platform = ""
            if not platform:
                platform = _single_line(str(getattr(event, "unified_msg_origin", "") or "").split(":", 1)[0], 80)
        platform = platform or _single_line(getattr(self, "target_platform", ""), 80) or "unknown"
        self_id = ""
        if event is not None:
            self_getter = getattr(self, "_event_self_id", None)
            if callable(self_getter):
                try:
                    self_id = _single_line(self_getter(event), 160)
                except Exception:
                    self_id = ""
        if not self_id:
            ids = sorted(_single_line(item, 160) for item in self._known_bot_self_ids() if _single_line(item, 160))
            if len(ids) == 1:
                self_id = ids[0]
        if not sender_id or not self_id:
            return {}
        namespace = _single_line(subject_namespace, 160).lower()
        if not namespace:
            namespace = f"{platform}:bot" if sender_id == self_id else f"{platform}:user"
        adapter_instance = _single_line(
            getattr(event, "adapter_instance_id", "") if event is not None else "",
            160,
        ) or f"{platform}:{_single_line(getattr(self, 'target_platform', ''), 80) or platform}"
        return {
            "companion_instance_id": self._unified_persona_scoped_value(PLUGIN_ID),
            "bot_account_id": f"{platform}:{self_id}",
            "adapter_instance_id": adapter_instance,
            "subject_namespace": namespace,
            "platform_subject_id": sender_id,
        }

    def resolve_unified_person_identity(self, identity: dict[str, Any]) -> dict[str, Any]:
        return self._active_unified_person_registry().resolve(identity)

    def resolve_unified_person_for_event(self, event: Any | None = None) -> dict[str, Any]:
        identity = self._unified_person_event_identity(event)
        if not identity:
            return {"state": "pending", "identity_key": "", "person_id": "", "errors": ["event_identity_missing"]}
        return self.resolve_unified_person_identity(identity)

