# -*- coding: utf-8 -*-
"""人格档案域。

由 tools/split_main_domain.py 从 main.py 机械抽取（33 个方法 / 923 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPlugin）。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import time
import uuid
from .helpers import _set_into_config, _single_line
from .main_shared import _ACTIVE_PERSONA_ID
from .persona_config import (
    PERSONA_SETTINGS_KEY,
    PERSONA_SETTINGS_REVISION_KEY,
    PERSONA_SETTINGS_SCHEMA_VERSION,
    PERSONA_SETTINGS_VERSION_KEY,
    PersonaConfigError,
    PersonaSettingsTypeError,
    copy_from_primary_config,
    create_persona_settings,
    detach_persona_settings,
    migrate_persona_profile,
    normalize_persona_settings,
    normalize_setting_value,
    resolve_effective_settings,
    resolve_persona_setting,
    runtime_persona_setting,
)
from .persona_sqlite_store import load_persona_sqlite_store
from .runtime_config_dispatcher import dispatch_runtime_config_effects
from .story_authority import story_legacy_operation, story_legacy_sync_operation
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from .logging_util import get_module_logger

logger = get_module_logger(__name__)

class PrivateCompanionPluginPersonaProfileMixin:
    """人格档案域（从 PrivateCompanionPlugin 拆出）。"""

    def _primary_persona_config(self) -> Any:
        return getattr(self, "config", {})

    def _primary_persona_id(self) -> str:
        """Return the plugin's only authoritative primary persona ID."""
        return self._sanitize_persona_id(
            object.__getattribute__(self, "plugin_specific_persona_id")
            if hasattr(self, "plugin_specific_persona_id")
            else ""
        )

    def get_persona_setting(self, key: str, persona_id: Any = "", default: Any = None) -> Any:
        """Return one effective setting for the active or requested persona."""
        key = str(key or "").strip()
        if not key:
            return default
        manifest = self._persona_scope_manifest()
        # Runtime attributes use snake_case while AstrBot's provider schema
        # keeps legacy provider IDs in upper case. Treat those spellings as
        # aliases at the resolver boundary so sparse persona profiles written
        # by either version continue to work.
        manifest_key = key
        if manifest_key not in manifest:
            folded = key.casefold()
            manifest_key = next(
                (candidate for candidate in manifest if str(candidate).casefold() == folded),
                key,
            )
        runtime_key = (
            manifest_key.lower()
            if manifest_key.isupper() and manifest_key.endswith("_PROVIDER_ID")
            else key
        )
        active = self._sanitize_persona_id(persona_id or _ACTIVE_PERSONA_ID.get())
        enabled = bool(object.__getattribute__(self, "enable_multi_persona_mode")) if hasattr(self, "enable_multi_persona_mode") else False
        if not enabled or not active:
            try:
                return object.__getattribute__(self, runtime_key)
            except AttributeError:
                return default
        if manifest_key == "plugin_specific_persona_id":
            return active
        primary = self._primary_persona_id()
        if active == primary:
            try:
                return object.__getattribute__(self, runtime_key)
            except AttributeError:
                pass
        settings = self._persona_settings_for_id(active)
        if manifest_key not in settings and key in settings and manifest_key != key:
            # Accept the lowercase runtime spelling in hand-edited/early
            # profile files while keeping the schema's canonical key in the
            # resolver path.
            settings[manifest_key] = deepcopy(settings[key])
        try:
            primary_value = object.__getattribute__(self, runtime_key)
            primary_source: Any = {manifest_key: deepcopy(primary_value)}
        except AttributeError:
            primary_source = self._primary_persona_config()
        value = resolve_persona_setting(
            manifest_key,
            settings,
            primary_source,
            manifest=manifest,
            default=default,
        )
        # The resolver intentionally returns a copy. This keeps mutable list/
        # object settings from being modified through a runtime read.
        return value

    def mobile_persona_identity(self) -> dict[str, Any]:
        """读取陪伴形象。主人格/单人格场景下 bot_name 即通用配置。"""
        name = _single_line(self.persona_setting("bot_name", getattr(self, "bot_name", "")), 12)
        return {"bot_name": name or "小星"}

    async def mobile_set_persona_identity(self, bot_name: Any = "") -> dict[str, Any]:
        """写入 Bot 称呼，复用与网页端一致的配置持久化路径。"""
        name = _single_line(bot_name, 12).strip()
        if not name:
            return {"ok": False, "code": "identity_name_empty", "message": "称呼不能为空"}
        before = _single_line(getattr(self, "bot_name", ""), 80)
        try:
            await self._flush_scheduled_data_save()
            _set_into_config(self.config, "bot_name", name)
            if not bool(await self._save_config_if_possible()):
                _set_into_config(self.config, "bot_name", before)
                return {"ok": False, "code": "identity_config_not_saved", "message": "配置未能持久化"}
            # 运行时读取走实例属性，同步更新让新称呼立刻生效
            self.bot_name = name
        except Exception as exc:
            _set_into_config(self.config, "bot_name", before)
            return {"ok": False, "code": "identity_update_failed", "message": str(exc)[:200]}
        return {"ok": True, "bot_name": name}

    def effective_persona_settings(self, persona_id: Any = "", *, include_common: bool = False) -> dict[str, Any]:
        active = self._sanitize_persona_id(persona_id or _ACTIVE_PERSONA_ID.get())
        if not active:
            active = self._primary_persona_id()
        settings = self._persona_settings_for_id(active)
        primary = self._primary_persona_id()
        if active == primary:
            result: dict[str, Any] = {}
            for key, entry in self._persona_scope_manifest().items():
                if not include_common and entry.get("scope") == "common":
                    continue
                try:
                    result[key] = deepcopy(object.__getattribute__(self, key))
                except AttributeError:
                    result[key] = resolve_persona_setting(
                        key,
                        {},
                        self._primary_persona_config(),
                        manifest=self._persona_scope_manifest(),
                    )
            return result
        return resolve_effective_settings(
            settings,
            self._primary_persona_config(),
            manifest=self._persona_scope_manifest(),
            include_common=include_common,
            include_identity=True,
        )

    @story_legacy_sync_operation("persona.profiles.startup-migrate")
    def _migrate_persona_profiles_sync(self) -> dict[str, Any]:
        """Migrate legacy persona JSON into SQLite and upgrade sparse settings."""
        result = {"ok": True, "migrated": [], "degraded": [], "skipped": []}
        profiles_dir = Path(str(getattr(self, "_persona_profiles_dir", "") or ""))
        if not profiles_dir.exists():
            return result
        primary = self._primary_persona_id()
        errors = getattr(self, "_persona_profile_errors", None)
        if not isinstance(errors, dict):
            errors = {}
            self._persona_profile_errors = errors
        candidates: dict[str, list[Path]] = {}
        for pattern in ("*.json", "*.db"):
            for path in sorted(profiles_dir.glob(pattern)):
                pid = self._persona_id_from_profile_path(path)
                if pid:
                    candidates.setdefault(pid, []).append(path)
        profiles = getattr(self, "_persona_data_profiles", None)
        if not isinstance(profiles, dict):
            profiles = {}
            self._persona_data_profiles = profiles
        for pid, paths in sorted(candidates.items()):
            if not pid or pid == primary:
                result["skipped"].append(pid or paths[0].name)
                continue
            legacy_path = self._persona_profile_path(pid)
            legacy_present = legacy_path.is_file()
            try:
                handle = self._load_secondary_persona_store_sync(pid)
                raw = handle.data
                settings = raw.get(PERSONA_SETTINGS_KEY)
                if settings is not None and not isinstance(settings, dict):
                    raise PersonaSettingsTypeError("persona_settings must be an object")
                # Legacy JSON is transformed before the SQLite write. For an
                # existing DB, keep the upgrade path below so a failed schema
                # migration leaves the already-authoritative DB untouched.
                migrated = raw if legacy_present else migrate_persona_profile(
                    raw,
                    manifest=self._persona_scope_manifest(),
                    target_version=PERSONA_SETTINGS_SCHEMA_VERSION,
                    persona_id=pid,
                    legacy_bot_name=(
                        self._persona_display_name_for_id(pid)
                        if not isinstance(settings, dict)
                        or not str(settings.get("bot_name") or "").strip()
                        else ""
                    ),
                )
                changed = migrated != raw
                if changed:
                    handle.manager.save_snapshot(deepcopy(migrated))
                profiles[pid] = migrated
                errors.pop(pid, None)
                if changed or legacy_present:
                    result["migrated"].append(pid)
            except Exception as exc:
                backup = (
                    self._backup_corrupt_persona_profile_sync(legacy_path, reason=str(exc))
                    if legacy_path.is_file()
                    else None
                )
                errors[pid] = str(exc)
                result["degraded"].append(pid)
                result["ok"] = False
                if backup is not None:
                    result.setdefault("backups", {})[pid] = str(backup)
                logger.warning(
                    "人格配置迁移降级: persona=%s error=%s",
                    pid,
                    _single_line(exc, 180),
                )
        return result

    def _effective_plugin_persona_id(self) -> str:
        active = _ACTIVE_PERSONA_ID.get()
        if bool(getattr(self, "enable_multi_persona_mode", False)) and active:
            return active
        return str(runtime_persona_setting(self, 'plugin_specific_persona_id', "") or "").strip()

    def _active_persona_scope(self) -> str:
        return _ACTIVE_PERSONA_ID.get() if bool(getattr(self, "enable_multi_persona_mode", False)) else ""

    def _configured_multi_persona_ids(self, *, strict: bool = False) -> list[str]:
        raw = self._cfg_raw(getattr(self, "config", {}), "multi_persona_ids", [])
        if isinstance(raw, str):
            raw = re.split(r"[\s,，、]+", raw)
        elif strict and not isinstance(raw, list):
            raise PersonaConfigError(
                "multi_persona_ids has an unverifiable container"
            )
        if not isinstance(raw, (list, tuple, set)):
            raw = []
        result: list[str] = []
        for value in raw:
            if strict and not isinstance(value, str):
                raise PersonaConfigError(
                    "multi_persona_ids contains a non-text persona id"
                )
            pid = self._sanitize_persona_id(value)
            if strict and str(value or "").strip() not in {"", pid}:
                raise PersonaConfigError(
                    "multi_persona_ids contains a non-canonical persona id"
                )
            if pid and pid not in result:
                result.append(pid)
        primary = self._primary_persona_id()
        if strict and not primary:
            raise PersonaConfigError(
                "multi-persona primary id cannot be verified"
            )
        if primary:
            result = [primary, *(pid for pid in result if pid != primary)]
        return result

    @story_legacy_sync_operation("persona.store.load")
    def _load_secondary_persona_store_sync(
        self,
        persona_id: Any,
        *,
        prepare_payload: Any = None,
    ):
        pid = self._sanitize_persona_id(persona_id)
        if not pid or pid == self._primary_persona_id():
            raise PersonaConfigError("secondary persona SQLite requires a non-primary persona")
        legacy_path, database_path = self._persona_profile_store_paths(pid)
        database_path.parent.mkdir(parents=True, exist_ok=True)
        if not callable(prepare_payload):
            prepare_payload = lambda payload: self._prepare_legacy_persona_payload(
                pid, payload
            )
        return load_persona_sqlite_store(
            persona_id=pid,
            legacy_json_path=legacy_path,
            sqlite_path=database_path,
            ensure_defaults=self._ensure_store_defaults,
            new_store=self._new_store,
            registry=self._persona_sqlite_registry(),
            prepare_payload=prepare_payload,
        )

    def _secondary_persona_store_exists(self, persona_id: Any) -> bool:
        pid = self._sanitize_persona_id(persona_id)
        if not pid or pid == self._primary_persona_id():
            return False
        legacy_path, database_path = self._persona_profile_store_paths(pid)
        return legacy_path.is_file() or database_path.is_file()

    def _backup_corrupt_persona_profile_sync(self, path: Path, *, reason: str) -> Path | None:
        if not path.exists():
            return None
        backup = path.with_name(
            f"{path.name}.corrupt-{int(time.time())}-{uuid.uuid4().hex[:8]}.bak"
        )
        try:
            shutil.copy2(path, backup)
            logger.error(
                "已隔离损坏人格 profile: source=%s backup=%s reason=%s",
                path,
                backup,
                _single_line(reason, 160),
            )
            return backup
        except Exception as exc:
            logger.error(
                "人格 profile 备份失败: source=%s error=%s",
                path,
                _single_line(exc, 160),
            )
            return None

    def _ensure_persona_profile(self, persona_id: str) -> dict[str, Any]:
        pid = self._sanitize_persona_id(persona_id) or self._primary_persona_id()
        if not pid:
            return self._data_default
        if pid == self._primary_persona_id():
            return self._data_default
        profile_errors = getattr(self, "_persona_profile_errors", None)
        if not isinstance(profile_errors, dict):
            profile_errors = {}
            self._persona_profile_errors = profile_errors
        profiles = getattr(self, "_persona_data_profiles", None)
        if profiles is None:
            profiles = {}
            self._persona_data_profiles = profiles
        existing = profiles.get(pid)
        if isinstance(existing, dict):
            return existing
        legacy_path, database_path = self._persona_profile_store_paths(pid)
        store_existed = legacy_path.is_file() or database_path.is_file()
        loaded: dict[str, Any] | None = None
        try:
            handle = self._load_secondary_persona_store_sync(pid)
            loaded = handle.data
            profile_errors.pop(pid, None)
        except Exception as exc:
            if legacy_path.is_file():
                self._backup_corrupt_persona_profile_sync(legacy_path, reason=str(exc))
            profile_errors[pid] = str(exc)
            logger.warning("人格资料读取失败 persona=%s error=%s", pid, _single_line(exc, 160))
        if loaded is not None:
            profile = loaded
        else:
            factory = getattr(self, "_new_store", None)
            profile = factory() if callable(factory) else {}
        ensure_defaults = getattr(self, "_ensure_store_defaults", None)
        if callable(ensure_defaults):
            profile = ensure_defaults(profile)
        if PERSONA_SETTINGS_KEY not in profile:
            profile["persona_settings"] = {}
        elif not isinstance(profile.get(PERSONA_SETTINGS_KEY), dict):
            reason = "persona_settings must be an object"
            self._backup_corrupt_persona_profile_sync(legacy_path, reason=reason)
            profile_errors[pid] = reason
            profile[PERSONA_SETTINGS_KEY] = {}
        if store_existed and loaded is not None and not str(profile[PERSONA_SETTINGS_KEY].get("bot_name") or "").strip():
            # Pre-settings secondary profiles need an identity to be editable;
            # ordinary missing keys remain sparse and continue following the
            # primary configuration.
            profile[PERSONA_SETTINGS_KEY]["bot_name"] = self._persona_display_name_for_id(pid)
            profile[PERSONA_SETTINGS_VERSION_KEY] = PERSONA_SETTINGS_SCHEMA_VERSION
            profile.setdefault(PERSONA_SETTINGS_REVISION_KEY, 0)
            try:
                self._save_persona_profile_sync(pid, profile)
            except Exception as exc:
                logger.warning(
                    "旧人格身份配置初始化落盘失败: persona=%s error=%s",
                    pid,
                    _single_line(exc, 160),
                )
        profiles[pid] = profile
        return profile

    def _save_persona_profile_sync(self, persona_id: str, data: dict[str, Any] | None = None) -> None:
        pid = self._sanitize_persona_id(persona_id)
        if not pid:
            return
        if pid == self._primary_persona_id():
            payload = data if isinstance(data, dict) else self._data_default
            self._write_data_snapshot_sync(deepcopy(payload))
            return
        payload = data if isinstance(data, dict) else self._ensure_persona_profile(pid)
        handle = self._load_secondary_persona_store_sync(pid)
        handle.manager.save_snapshot(deepcopy(payload))

    async def _save_persona_profile_async(
        self,
        persona_id: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        # 全量快照写盘可能耗时（JSON 序列化 + SQLite 事务），从事件循环移到线程池。
        await asyncio.to_thread(self._save_persona_profile_sync, persona_id, data)

    def _write_persona_reset_backup_sync(
        self,
        persona_id: str,
        snapshot: dict[str, Any],
    ) -> Path:
        pid = self._sanitize_persona_id(persona_id)
        profile_stem = (
            Path(self._persona_profile_filename(pid)).stem
            if pid
            else "single-profile"
        )
        data_root = Path(
            str(getattr(self, "data_dir", "") or "").strip()
            or Path(self._persona_profiles_dir).parent
        )
        backup_dir = data_root / "persona_backups" / profile_stem
        backup_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        path = backup_dir / f"{timestamp}-{uuid.uuid4().hex[:8]}.json"
        temp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        payload = {
            "backup_version": 1,
            "persona_id": pid,
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "data": snapshot,
        }
        try:
            temp.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(temp, path)
        finally:
            try:
                temp.unlink(missing_ok=True)
            except Exception:
                pass
        return path

    def _astrbot_persona_exists(self, persona_id: Any) -> bool:
        pid = self._sanitize_persona_id(persona_id)
        if not pid:
            return False
        manager = getattr(getattr(self, "context", None), "persona_manager", None)
        getter = getattr(manager, "get_persona_v3_by_id", None)
        if callable(getter):
            try:
                return getter(pid) is not None
            except Exception:
                pass
        for item in list(getattr(manager, "personas_v3", None) or []) + list(
            getattr(manager, "personas", None) or []
        ):
            if isinstance(item, dict):
                item_id = item.get("name") or item.get("persona_id") or item.get("id")
            else:
                item_id = (
                    getattr(item, "persona_id", None)
                    or getattr(item, "name", None)
                    or getattr(item, "id", None)
                )
            if self._sanitize_persona_id(item_id) == pid:
                return True
        return False

    def _astrbot_persona_ids_snapshot(self) -> tuple[set[str] | None, str]:
        """Return AstrBot's complete in-memory persona set when verifiable."""
        manager = getattr(getattr(self, "context", None), "persona_manager", None)
        if manager is None:
            return None, "persona_manager_unavailable"
        source = getattr(manager, "personas", None)
        source_name = "personas"
        # PersonaManager creates both attributes eagerly, but only populates
        # them during initialize().  Treat the pre-initialize empty state as
        # unknown instead of interpreting it as an authoritative empty list.
        if (
            isinstance(source, (list, tuple))
            and not source
            and getattr(manager, "selected_default_persona", None) is None
            and getattr(manager, "selected_default_persona_v3", None) is None
        ):
            return None, "persona_manager_not_initialized"
        if source is None and hasattr(manager, "personas_v3"):
            source = getattr(manager, "personas_v3", None)
            source_name = "personas_v3"
        if not isinstance(source, (list, tuple)):
            return None, f"{source_name}_unavailable"
        ids: set[str] = set()
        for item in source:
            if isinstance(item, dict):
                item_id = item.get("persona_id") or item.get("name") or item.get("id")
            else:
                item_id = (
                    getattr(item, "persona_id", None)
                    or getattr(item, "name", None)
                    or getattr(item, "id", None)
                )
            pid = self._sanitize_persona_id(item_id)
            if not pid:
                return None, f"{source_name}_item_invalid"
            ids.add(pid)
        return ids, "ok"

    def _backup_deleted_persona_store_sync(self, persona_id: str, path: Path) -> Path | None:
        """Make a recoverable copy before retiring a deleted persona store."""
        if not path.is_file():
            return None
        root = Path(str(getattr(self, "data_dir", "") or "").strip() or Path(self._persona_profiles_dir).parent)
        backup_dir = root / "persona_backups" / self._persona_profile_stem(persona_id)
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup = backup_dir / f"deleted-{int(time.time())}-{uuid.uuid4().hex[:8]}-{path.name}"
        try:
            shutil.copy2(path, backup)
            return backup
        except Exception as exc:
            logger.warning("已删除人格档案备份失败，保留原文件: persona=%s path=%s error=%s", persona_id, path, _single_line(exc, 160))
            return None

    async def _reconcile_deleted_personas_async(self) -> dict[str, Any]:
        """Reconcile plugin persona state with AstrBot's official persona list."""
        official_ids, reason = self._astrbot_persona_ids_snapshot()
        result: dict[str, Any] = {"ok": official_ids is not None, "state": "verified" if official_ids is not None else "unverifiable", "reason": reason, "removed": [], "backups": {}}
        if official_ids is None:
            logger.info("跳过已删除人格对账: AstrBot 人格列表不可验证 reason=%s", reason)
            return result
        primary = self._primary_persona_id()
        configured = self._configured_multi_persona_ids()
        try:
            candidates = set(self._persona_profile_ids())
        except Exception:
            candidates = set(configured)
        stale = sorted(pid for pid in candidates if pid and pid != primary and pid not in official_ids)
        if not stale:
            return result
        next_ids = [pid for pid in configured if pid == primary or pid in official_ids]
        config_changed = next_ids != configured
        before_config = deepcopy(self._cfg_raw(self.config, "multi_persona_ids", []))
        if config_changed:
            _set_into_config(self.config, "multi_persona_ids", next_ids)
            try:
                if not bool(await self._save_config_if_possible()):
                    raise RuntimeError("AstrBot 配置未能持久化")
            except Exception as exc:
                _set_into_config(self.config, "multi_persona_ids", before_config)
                result.update({"ok": False, "state": "degraded", "reason": "config_save_failed", "error": _single_line(exc, 180)})
                logger.warning("已删除人格对账未完成，配置保存失败: %s", _single_line(exc, 180))
                return result
        self.multi_persona_ids = next_ids
        for pid in stale:
            retired = self._retire_deleted_persona_store_sync(pid)
            if retired["removed"] and not retired["failed"]:
                result["removed"].append(pid)
            if retired["failed"]:
                result.setdefault("failed", {})[pid] = retired["failed"]
            if retired.get("backups"):
                result["backups"][pid] = retired["backups"]
        result["config_changed"] = config_changed
        if result["removed"] or config_changed:
            logger.info("已同步 AstrBot 删除的人格: removed=%s config_changed=%s", ",".join(result["removed"]) or "-", config_changed)
        return result

    async def _conversation_persona_id_for_event(self, event: Any) -> str:
        """Compatibility wrapper returning AstrBot's final effective persona."""
        resolved = await self._astrbot_effective_persona_for_event(event)
        return self._sanitize_persona_id(resolved.get("persona_id"))

    def _clear_persona_runtime_cache(self, profile: dict[str, Any]) -> None:
        if not isinstance(profile, dict):
            return
        for key in tuple(profile.keys()):
            lowered = str(key).lower()
            if "cache" in lowered or lowered in {"conversation_history", "recent_context", "pending_context"}:
                profile.pop(key, None)

    @story_legacy_sync_operation("persona.profile.migrate")
    def _migrate_persona_profile(self, source_persona_id: Any, target_persona_id: Any, keys: list[Any]) -> dict[str, Any]:
        source = self._sanitize_persona_id(source_persona_id)
        target = self._sanitize_persona_id(target_persona_id)
        if not source or not target or source == target:
            return {"ok": False, "message": "源人格和目标人格必须不同"}
        if not bool(getattr(self, "enable_multi_persona_mode", False)):
            return {
                "ok": False,
                "code": "persona_migration_multi_persona_disabled",
                "message": "请先开启多人格模式后再迁移人格资料",
            }
        enabled_ids = set(self._configured_multi_persona_ids())
        if source not in enabled_ids:
            return {
                "ok": False,
                "code": "persona_migration_source_not_enabled",
                "message": "来源人格未在已保存的人格拓扑中启用",
            }
        if target not in enabled_ids:
            return {
                "ok": False,
                "code": "persona_migration_target_not_enabled",
                "message": "目标人格未在已保存的人格拓扑中启用",
            }
        primary = self._primary_persona_id()

        def eligible(persona_id: str) -> bool:
            # The primary intentionally has no separate persona JSON; its
            # authoritative data is the single-persona store.
            return persona_id == primary or self._persona_config_exists(persona_id)

        if not eligible(source):
            return {
                "ok": False,
                "code": "persona_migration_source_config_missing",
                "message": "来源人格尚未建立有效的人格配置文件",
            }
        if not eligible(target):
            return {
                "ok": False,
                "code": "persona_migration_target_config_missing",
                "message": "目标人格尚未建立有效的人格配置文件",
            }
        source_data = self._ensure_persona_profile(source)
        target_data = self._ensure_persona_profile(target)
        source_before = deepcopy(source_data)
        target_before = deepcopy(target_data)
        source_next = deepcopy(source_data)
        target_next = deepcopy(target_data)
        selected = [str(key).strip() for key in keys if str(key).strip()]
        if not selected:
            selected = ["daily_plan", "daily_state", "bot_diaries", "users", "groups", "memo_notes", "token_usage"]
        migration_keys = list(selected)
        if "bot_diaries" in migration_keys:
            for companion_key in (
                "diary_generated_day",
                "daily_diary_deleted_days",
                "daily_diary_delete_revision",
            ):
                if companion_key not in migration_keys:
                    migration_keys.append(companion_key)
        for key in migration_keys:
            source_settings = source_next.get("persona_settings") if isinstance(source_next.get("persona_settings"), dict) else {}
            target_settings = target_next.setdefault("persona_settings", {})
            if key in source_settings:
                target_settings[key] = deepcopy(source_settings[key])
            elif key in source_next:
                target_next[key] = deepcopy(source_next[key])
        if "bot_diaries" in migration_keys:
            diaries = source_next.get("bot_diaries")
            diary_days: list[str] = []
            if isinstance(diaries, list):
                diary_days = [
                    _single_line(item.get("date"), 16)
                    for item in diaries
                    if isinstance(item, dict)
                ]
            elif isinstance(diaries, dict):
                diary_days = [
                    _single_line(
                        (item.get("date") if isinstance(item, dict) else "") or stored_date,
                        16,
                    )
                    for stored_date, item in diaries.items()
                ]
            valid_days = [day for day in diary_days if re.fullmatch(r"\d{4}-\d{2}-\d{2}", day)]
            source_marker = _single_line(source_next.get("diary_generated_day"), 16)
            target_next["diary_generated_day"] = (
                source_marker
                if re.fullmatch(r"\d{4}-\d{2}-\d{2}", source_marker)
                else max(valid_days, default="")
            )
            source_deleted_days = source_next.get("daily_diary_deleted_days")
            target_next["daily_diary_deleted_days"] = deepcopy(
                source_deleted_days if isinstance(source_deleted_days, list) else []
            )
            try:
                target_next["daily_diary_delete_revision"] = max(
                    0,
                    int(source_next.get("daily_diary_delete_revision") or 0),
                )
            except (TypeError, ValueError, OverflowError):
                target_next["daily_diary_delete_revision"] = 0
        self._clear_persona_runtime_cache(source_next)
        self._clear_persona_runtime_cache(target_next)
        try:
            self._save_persona_profile_sync(source, source_next)
            self._save_persona_profile_sync(target, target_next)
        except Exception as exc:
            for persona_id, previous in ((source, source_before), (target, target_before)):
                try:
                    self._save_persona_profile_sync(persona_id, previous)
                except Exception:
                    pass
            return {
                "ok": False,
                "message": f"人格资料迁移落盘失败: {_single_line(exc, 120)}",
            }
        source_data.clear()
        source_data.update(source_next)
        target_data.clear()
        target_data.update(target_next)
        self._reset_persona_prompt_caches(source, target)
        return {"ok": True, "source_persona_id": source, "target_persona_id": target, "keys": migration_keys, "cache_cleared": True}

    @story_legacy_operation("persona.profile.migrate-transaction")
    async def _migrate_persona_profile_async(
        self,
        source_persona_id: Any,
        target_persona_id: Any,
        keys: list[Any],
    ) -> dict[str, Any]:
        await self._flush_scheduled_data_save()
        async with self._data_lock:
            return await asyncio.to_thread(
                self._migrate_persona_profile,
                source_persona_id,
                target_persona_id,
                keys,
            )

    def _switch_persona_for_window(
        self,
        persona_id: Any,
        *,
        window_key: str = "",
        source_persona_id: str = "",
        migrate_keys: list[Any] | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        return {
            "ok": False,
            "status_code": 410,
            "code": "plugin_persona_routing_removed",
            "message": "窗口人格由 AstrBot 管理，插件不再提供窗口绑定",
            "routing_authority": "astrbot",
        }

    async def _switch_persona_for_window_async(
        self,
        *args,
        persist: bool = False,
        **kwargs,
    ) -> dict[str, Any]:
        return self._switch_persona_for_window(
            args[0] if args else kwargs.get("persona_id", "")
        )

    @story_legacy_sync_operation("persona.mode.transition")
    def _prepare_multi_persona_transition(self, enabled: bool) -> None:
        """Keep the canonical single store authoritative across mode changes."""
        current = bool(getattr(self, "enable_multi_persona_mode", False))
        if current == bool(enabled):
            return
        if enabled:
            primary = self._primary_persona_id()
            if not primary:
                raise PersonaConfigError("开启多人格前必须先补充插件指定人格 ID")
            if not self._astrbot_persona_exists(primary):
                raise PersonaConfigError("插件指定人格在 AstrBot 中不存在，请先重新选择")
            ids = self._configured_multi_persona_ids()
            if primary not in ids:
                ids.insert(0, primary)
            self.multi_persona_ids = ids
        else:
            self._write_data_snapshot_sync(deepcopy(self._data_default))

    async def _create_persona_config_async(
        self,
        persona_id: Any,
        *,
        bot_name: Any,
        mode: Any,
        source_persona_id: Any = "",
        recovery: bool = False,
    ) -> dict[str, Any]:
        pid = self._sanitize_persona_id(persona_id)
        name = _single_line(bot_name, 80)
        primary = self._primary_persona_id()
        if not pid or pid == primary:
            return {"ok": False, "code": "persona_config_target_invalid", "message": "请选择非主人格作为新配置目标"}
        if pid not in set(self._configured_multi_persona_ids()):
            return {"ok": False, "code": "persona_config_target_not_enabled", "message": "请先保存人格拓扑，再为该人格创建配置"}
        if not name:
            return {"ok": False, "code": "persona_bot_name_required", "message": "Bot 名字不能为空"}
        create_mode = str(mode or "follow_primary").strip().lower()
        source_id = self._sanitize_persona_id(source_persona_id)
        if create_mode == "copy":
            if not source_id or source_id == pid:
                return {"ok": False, "code": "persona_config_invalid", "message": "请选择不同的来源人格"}
            if source_id != primary and not self._persona_config_exists(source_id):
                return {"ok": False, "code": "persona_config_invalid", "message": "复制来源尚未创建独立人格配置"}
        await self._flush_scheduled_data_save()
        async with self._data_lock:
            existed = self._secondary_persona_store_exists(pid) or pid in getattr(
                self, "_persona_data_profiles", {}
            )
            profile = deepcopy(self._ensure_persona_profile(pid))
            current_settings = profile.get(PERSONA_SETTINGS_KEY)
            profile_degraded = pid in set(getattr(self, "_persona_profile_errors", {}))
            if existed and isinstance(current_settings, dict) and current_settings and not (recovery and profile_degraded):
                return {"ok": False, "code": "persona_config_exists", "message": "该人格配置已存在，请直接编辑或恢复使用"}
            try:
                if create_mode == "copy":
                    if source_id == primary:
                        settings = copy_from_primary_config(
                            self._primary_persona_config(),
                            bot_name=name,
                            manifest=self._persona_scope_manifest(),
                        )
                    else:
                        source_profile = self._ensure_persona_profile(source_id)
                        settings = create_persona_settings(
                            "copy",
                            bot_name=name,
                            source_settings=source_profile.get(PERSONA_SETTINGS_KEY) or {},
                            manifest=self._persona_scope_manifest(),
                        )
                else:
                    settings = create_persona_settings(
                        create_mode,
                        bot_name=name,
                        primary_config=self._primary_persona_config(),
                        manifest=self._persona_scope_manifest(),
                        normalizer=normalize_setting_value,
                    )
            except PersonaConfigError as exc:
                return {"ok": False, "code": "persona_config_invalid", "message": str(exc)}
            before_config = deepcopy(self._cfg_raw(self.config, "multi_persona_ids", []))
            before_profile = deepcopy(profile)
            next_profile = deepcopy(profile)
            database_path = self._persona_profile_db_path(pid)
            next_profile[PERSONA_SETTINGS_KEY] = settings
            next_profile[PERSONA_SETTINGS_VERSION_KEY] = PERSONA_SETTINGS_SCHEMA_VERSION
            next_profile[PERSONA_SETTINGS_REVISION_KEY] = max(1, int(profile.get(PERSONA_SETTINGS_REVISION_KEY) or 0) + 1)
            ids = self._configured_multi_persona_ids()
            if pid not in ids:
                ids.append(pid)
            try:
                await self._save_persona_profile_async(pid, next_profile)
                _set_into_config(self.config, "multi_persona_ids", ids)
                config_saved = bool(await self._save_config_if_possible())
                if not config_saved:
                    raise RuntimeError("AstrBot 配置未能持久化")
            except Exception as exc:
                _set_into_config(self.config, "multi_persona_ids", before_config)
                try:
                    if existed:
                        await self._save_persona_profile_async(pid, before_profile)
                    else:
                        registry = getattr(self, "_persona_sqlite_store_registry", None)
                        discard = getattr(registry, "discard", None)
                        if callable(discard):
                            discard(database_path)
                        database_path.unlink(missing_ok=True)
                        database_path.with_name(database_path.name + "-wal").unlink(missing_ok=True)
                        database_path.with_name(database_path.name + "-shm").unlink(missing_ok=True)
                except Exception:
                    pass
                return {"ok": False, "code": "persona_config_persistence_failed", "message": f"人格配置保存失败，已回滚: {_single_line(exc, 120)}"}
            live = self._ensure_persona_profile(pid)
            live.clear()
            live.update(next_profile)
            self.multi_persona_ids = ids
            self._reset_persona_prompt_caches(pid)
            return {"ok": True, "created": True, **self._persona_config_state(pid)}

    async def _update_persona_settings_async(
        self,
        persona_id: Any,
        *,
        changes: Any,
        follow_primary_keys: Any,
        expected_revision: Any,
    ) -> dict[str, Any]:
        pid = self._sanitize_persona_id(persona_id)
        primary = self._primary_persona_id()
        if not pid or pid == primary:
            return {"ok": False, "code": "persona_settings_target_invalid", "message": "主人格请使用现有通用配置保存接口"}
        if not self._persona_config_exists(pid):
            return {"ok": False, "code": "persona_config_missing", "message": "该人格尚未创建独立配置"}
        if not isinstance(changes, dict) or not isinstance(follow_primary_keys, list):
            return {"ok": False, "code": "persona_settings_payload_invalid", "message": "人格配置更新格式无效"}
        manifest = self._persona_scope_manifest()
        overlap = set(changes) & {str(key) for key in follow_primary_keys}
        if overlap:
            return {"ok": False, "code": "persona_settings_overlap", "message": "同一配置项不能同时覆盖和恢复跟随"}
        await self._flush_scheduled_data_save()
        changed_keys = sorted(set(changes) | set(map(str, follow_primary_keys)))
        async with self._data_lock:
            profile = self._ensure_persona_profile(pid)
            revision = int(profile.get(PERSONA_SETTINGS_REVISION_KEY) or 0)
            try:
                expected = int(expected_revision)
            except (TypeError, ValueError):
                expected = revision
            if expected != revision:
                return {"ok": False, "status_code": 409, "code": "persona_settings_revision_conflict", "message": "人格配置已被其他页面修改", "revision": revision}
            next_profile = deepcopy(profile)
            raw = deepcopy(next_profile.get(PERSONA_SETTINGS_KEY) or {})
            for key, value in changes.items():
                entry = manifest.get(str(key))
                if not entry or entry.get("scope") != "persona":
                    return {"ok": False, "code": "persona_setting_not_allowed", "message": f"配置项不允许按人格覆盖: {key}"}
                raw[str(key)] = normalize_setting_value(str(key), value, entry)
            for raw_key in follow_primary_keys:
                key = str(raw_key)
                entry = manifest.get(key)
                if not entry or entry.get("scope") != "persona":
                    return {"ok": False, "code": "persona_setting_not_allowed", "message": f"配置项不允许按人格跟随: {key}"}
                if entry.get("identity"):
                    return {"ok": False, "code": "persona_identity_cannot_follow", "message": f"身份配置不能跟随主人格: {key}"}
                raw.pop(key, None)
            if not str(raw.get("bot_name") or "").strip():
                return {"ok": False, "code": "persona_bot_name_required", "message": "Bot 名字不能为空"}
            next_profile[PERSONA_SETTINGS_KEY] = normalize_persona_settings(raw, manifest=manifest, preserve_unknown=True)
            next_profile[PERSONA_SETTINGS_VERSION_KEY] = PERSONA_SETTINGS_SCHEMA_VERSION
            next_profile[PERSONA_SETTINGS_REVISION_KEY] = revision + 1
            if any(
                self._persona_setting_invalidates_runtime_cache(key, manifest)
                for key in changed_keys
            ):
                self._clear_persona_runtime_cache(next_profile)
            try:
                await self._save_persona_profile_async(pid, next_profile)
            except Exception as exc:
                return {"ok": False, "code": "persona_settings_persistence_failed", "message": f"人格配置保存失败: {_single_line(exc, 120)}"}
            profile.clear()
            profile.update(next_profile)
            result = {
                "ok": True,
                "changed": changed_keys,
                **self._persona_config_state(pid),
            }
        self._apply_persona_setting_hot_effects(pid, changed_keys)
        return result

    def _apply_persona_setting_hot_effects(
        self,
        persona_id: str,
        changed_keys: list[str],
    ) -> None:
        """Compatibility adapter to the sole runtime side-effect dispatcher."""
        dispatch_runtime_config_effects(
            self,
            {str(key): None for key in changed_keys},
            scope="persona",
            persona_id=persona_id,
            source="persona",
        )

    async def _detach_persona_settings_async(self, persona_id: Any, *, expected_revision: Any, preview_hash: Any) -> dict[str, Any]:
        preview = self._persona_detach_preview(persona_id)
        if not preview.get("ok"):
            return preview
        if str(preview_hash or "") != preview["preview_hash"]:
            return {"ok": False, "status_code": 409, "code": "persona_detach_preview_stale", "message": "脱离预览已过期，请重新预览"}
        return await self._update_persona_settings_async(
            preview["persona_id"],
            changes=detach_persona_settings(
                self._ensure_persona_profile(preview["persona_id"]).get(PERSONA_SETTINGS_KEY) or {},
                self._primary_persona_config(),
                manifest=self._persona_scope_manifest(),
            ),
            follow_primary_keys=[],
            expected_revision=expected_revision,
        )

    async def _mutate_persona_window_binding_async(
        self,
        *,
        action: str,
        window_key: Any,
        persona_id: Any = "",
        previous_window_key: Any = "",
        expected_revision: Any = None,
    ) -> dict[str, Any]:
        return {
            "ok": False,
            "status_code": 410,
            "code": "plugin_persona_routing_removed",
            "message": "窗口人格由 AstrBot 管理，旧插件绑定只读保留",
            "routing_authority": "astrbot",
        }

