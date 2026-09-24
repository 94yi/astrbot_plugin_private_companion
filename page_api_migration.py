# -*- coding: utf-8 -*-
"""配置迁移 / 导入导出 / 备份 域页面 API。

由 tools/split_mixin_domain.py 从 page_api.py 机械抽取（46 个方法 / 956 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApi）。

"""
from __future__ import annotations

import asyncio
import json
import time
import re
import hashlib
import secrets
from copy import copy, deepcopy
from pathlib import Path
from typing import Any, Mapping
from quart import send_file
from .page_api_shared import _page_api_host, _page_api_host_request as request
from .config_migration import _config_root_mapping, _ensure_config_parent_dir
from .helpers import _MISSING, _flat_get, _normalize_timezone_name, _normalize_timezone_setting, _path_text, _redact_outbound_secrets, _safe_int, _set_into_config, _strip_internal_message_blocks, _text_looks_garbled, _text_similarity, _today_key, normalize_bot_relationship_cards
from .story_authority import (
    StoryAuthorityError,
    story_authority_controller,
    story_legacy_operation,
    story_legacy_operation_if,
)
from .logging_util import get_module_logger
from .page_backend import MigrationBackupService, build_route_bindings, generation_log_candidates

logger = get_module_logger(__name__)

# 本域方法在宿主模块中用到的模块级常量。
# 拆分后方法体留在本模块，名字必须在本模块可解析，否则运行期 NameError。
PLUGIN_NAME = "astrbot_plugin_private_companion"
_MIGRATION_UNKNOWN_CONFIG_KEY = "_migration_unknown_config_fields_v1"
_MIGRATION_UNKNOWN_NAMESPACES = ("settings", "features", "providers")
_MIGRATION_UNKNOWN_MAX_BYTES = 256 * 1024
_MIGRATION_UNKNOWN_MAX_FIELDS = 128
_MIGRATION_UNKNOWN_SENSITIVE_NAME = re.compile(
    r"(?:access[_-]?token|password|secret|cookie|api[_-]?key|storage[_-])",
    flags=re.I,
)
EXTENSION_MIGRATION_NOTICE_VERSION = "6.2.2"


class PrivateCompanionPageApiMigrationMixin:
    """配置迁移 / 导入导出 / 备份 域（从 PrivateCompanionPageApi 拆出）。"""

    async def get_extension_migration_notice(self) -> dict[str, Any]:
        """Read the persisted dismissal state for the extension migration notice."""
        version = EXTENSION_MIGRATION_NOTICE_VERSION
        try:
            async with self.plugin._data_lock:
                raw = self.plugin.data.get("extension_migration_notice_preferences")
                record = raw.get(version) if isinstance(raw, dict) else None
                dismissed = bool(record.get("dismissed")) if isinstance(record, dict) else False
            return self._ok({"version": version, "dismissed": dismissed})
        except Exception as exc:
            logger.debug("读取拓展迁移提示偏好失败: %s", self._single_line(exc, 160))
            return self._ok({"version": version, "dismissed": False, "persistent": False})
    async def update_extension_migration_notice(self) -> dict[str, Any]:
        """Persist the user's choice so embedded Page containers do not re-show it."""
        payload = await request.get_json(silent=True) or {}
        version = self._single_line(payload.get("version"), 40) or EXTENSION_MIGRATION_NOTICE_VERSION
        if version != EXTENSION_MIGRATION_NOTICE_VERSION:
            return self._error("无效的迁移提示版本")
        dismissed = payload.get("dismissed") is True
        try:
            async with self.plugin._data_lock:
                preferences = self.plugin.data.get("extension_migration_notice_preferences")
                if not isinstance(preferences, dict):
                    preferences = {}
                preferences = {
                    str(key): value
                    for key, value in preferences.items()
                    if isinstance(value, dict)
                }
                preferences[version] = {
                    "dismissed": dismissed,
                    "updated_at": time.time(),
                }
                self.plugin.data["extension_migration_notice_preferences"] = dict(list(preferences.items())[-12:])
                self.plugin._save_data_sync(sections={"extension_migration_notice_preferences"})
            return self._ok({"version": version, "dismissed": dismissed, "persistent": True})
        except Exception as exc:
            logger.warning("保存拓展迁移提示偏好失败: %s", self._single_line(exc, 160))
            return self._exception_error("保存迁移提示偏好失败")
    async def export_migration_config(self) -> dict[str, Any]:
        try:
            package = await self._build_migration_package(self._migration_export_options_from_request())
            return self._ok(package)
        except Exception as exc:
            logger.error(f"导出配置备份失败: {exc}", exc_info=True)
            return self._exception_error(str(exc))
    async def list_migration_backups(self) -> dict[str, Any]:
        try:
            return self._ok({"items": self._list_migration_backup_items(limit=8)})
        except Exception as exc:
            logger.error(f"读取配置备份列表失败: {exc}", exc_info=True)
            return self._exception_error(str(exc))
    async def restore_migration_backup(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        try:
            backup_id = self._single_line(payload.get("id") or payload.get("name"), 160)
            path = self._resolve_migration_backup_path(backup_id)
            package = json.loads(path.read_text(encoding="utf-8-sig"))
            package = self._extract_migration_package(package)
            normalized = self._normalize_migration_package(package)
            overview = await self._apply_migration_normalized(normalized, mode="replace", conflict="use_backup")
            if self._is_http_error_response(overview):
                return overview
            data = overview.get("data") if isinstance(overview.get("data"), dict) else {}
            data["message"] = "已从自动备份恢复。"
            data["restored_from"] = path.name
            overview["data"] = data
            return overview
        except Exception as exc:
            logger.error(f"恢复配置备份失败: {exc}", exc_info=True)
            return self._exception_error(str(exc))
    async def preview_migration_config_import(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        try:
            package = self._extract_migration_package(
                payload,
                allow_checksum_mismatch=self._normalize_bool_value(payload.get("allow_checksum_mismatch")),
            )
            normalized = self._normalize_migration_package(package)
            summary = await self._migration_import_summary(normalized)
            summary["message"] = "已读取备份，确认后才会写入。"
            return self._ok(summary)
        except Exception as exc:
            logger.error(f"预览配置导入失败: {exc}", exc_info=True)
            return self._exception_error(str(exc))
    async def apply_migration_config_import(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        try:
            package = self._extract_migration_package(
                payload,
                allow_checksum_mismatch=self._normalize_bool_value(payload.get("allow_checksum_mismatch")),
            )
            mode = str(payload.get("mode") or "merge").strip().lower()
            if mode not in {"merge", "replace"}:
                mode = "merge"
            conflict = str(payload.get("conflict") or "use_backup").strip().lower()
            if conflict not in {"use_backup", "keep_current", "fill_empty"}:
                conflict = "use_backup"
            normalized = self._normalize_migration_package(package)
            if normalized.get("legacy_snapshot") and mode == "replace":
                mode = "merge"
            return await self._apply_migration_normalized(normalized, mode=mode, conflict=conflict)
        except Exception as exc:
            logger.error(f"应用配置导入失败: {exc}", exc_info=True)
            return self._exception_error(str(exc))
    def _migration_export_options_from_request(self) -> set[str]:
        raw = request.args.get("sections") or ""
        if not raw:
            return {"basic", "relations", "food_skills"}
        options = {part.strip().lower() for part in re.split(r"[,，\s]+", raw) if part.strip()}
        allowed = {"basic", "relations", "food_skills", "providers", "sensitive"}
        selected = {item for item in options if item in allowed}
        return selected or {"basic", "relations", "food_skills"}
    @staticmethod
    def _migration_unknown_key_allowed(value: Any) -> bool:
        return bool(
            type(value) is str
            and 0 < len(value) <= 120
            and not value.startswith("__")
            and _MIGRATION_UNKNOWN_SENSITIVE_NAME.search(value) is None
        )
    @classmethod
    def _bounded_migration_unknown_fields(cls, value: Any) -> dict[str, dict[str, Any]]:
        source = value if type(value) is dict else {}
        result: dict[str, dict[str, Any]] = {}
        total_bytes = 2
        total_fields = 0
        for namespace in _MIGRATION_UNKNOWN_NAMESPACES:
            raw_fields = source.get(namespace)
            if type(raw_fields) is not dict:
                continue
            kept: dict[str, Any] = {}
            for key in sorted(key for key in raw_fields if type(key) is str):
                if (
                    total_fields >= _MIGRATION_UNKNOWN_MAX_FIELDS
                    or not cls._migration_unknown_key_allowed(key)
                ):
                    continue
                try:
                    encoded = json.dumps(
                        [namespace, key, raw_fields[key]],
                        ensure_ascii=False,
                        allow_nan=False,
                        separators=(",", ":"),
                    ).encode("utf-8")
                except (TypeError, ValueError, UnicodeError):
                    continue
                if total_bytes + len(encoded) > _MIGRATION_UNKNOWN_MAX_BYTES:
                    continue
                kept[key] = deepcopy(raw_fields[key])
                total_bytes += len(encoded)
                total_fields += 1
            if kept:
                result[namespace] = kept
        return result
    def _migration_unknown_config_fields(self) -> dict[str, dict[str, Any]]:
        raw = self._config_get_raw(_MIGRATION_UNKNOWN_CONFIG_KEY, {})
        if type(raw) is str:
            try:
                raw = json.loads(raw)
            except (json.JSONDecodeError, TypeError, ValueError):
                raw = {}
        return self._bounded_migration_unknown_fields(raw)
    @story_legacy_operation_if(
        "page.migration.story-apply",
        lambda self, normalized, **kwargs: (
            isinstance(normalized, dict)
            and isinstance(normalized.get("data"), dict)
            and "creative_projects" in normalized["data"]
        ),
    )
    async def _apply_migration_normalized(self, normalized: dict[str, Any], *, mode: str, conflict: str) -> dict[str, Any]:
        data_payload = normalized.get("data") if isinstance(normalized.get("data"), dict) else {}
        if data_payload:
            # Validate the normalized section set before changing config or live data.
            validator = getattr(self.plugin, "_validate_save_request", None)
            if not callable(validator):
                raise RuntimeError("migration section validator is unavailable")
            validator(set(data_payload), (), None)
        before = await self._build_migration_package(include_all=True)
        backup_path = self._write_migration_backup(before)

        config_root = _config_root_mapping(getattr(self.plugin, "config", None))
        if not isinstance(config_root, dict):
            raise RuntimeError("migration configuration store is unavailable")
        config_snapshot = deepcopy(config_root)
        async with self.plugin._data_lock:
            data_snapshot = deepcopy(self.plugin.data)
        try:
            return await self._commit_migration_normalized(
                normalized,
                mode=mode,
                conflict=conflict,
                backup_path=backup_path,
            )
        except BaseException as exc:
            rollback_error = await self._rollback_migration_normalized(
                config_snapshot,
                data_snapshot,
                normalized,
            )
            if rollback_error:
                raise RuntimeError(
                    f"配置导入失败，且回滚未完整: {rollback_error}"
                ) from exc
            raise
    async def _commit_migration_normalized(
        self,
        normalized: dict[str, Any],
        *,
        mode: str,
        conflict: str,
        backup_path: str,
    ) -> dict[str, Any]:
        data_payload = normalized.get("data") if isinstance(normalized.get("data"), dict) else {}

        changed_config: dict[str, Any] = {}
        incoming_unknown = self._bounded_migration_unknown_fields(
            normalized.get("unknown_config_fields")
        )
        if incoming_unknown:
            current_unknown = self._migration_unknown_config_fields()
            merged_unknown = deepcopy(current_unknown)
            self._deep_merge_dict(
                merged_unknown,
                incoming_unknown,
                conflict=conflict,
            )
            merged_unknown = self._bounded_migration_unknown_fields(merged_unknown)
            if merged_unknown != current_unknown:
                changed_config[_MIGRATION_UNKNOWN_CONFIG_KEY] = merged_unknown
        for key, value in normalized.get("features", {}).items():
            normalized_value = self._normalize_bool_value(value)
            current_value = self._normalize_bool_value(getattr(self.plugin, key, self._config_get(key)))
            if self._should_apply_migration_value(current_value, normalized_value, conflict):
                changed_config[key] = normalized_value
        for key, value in normalized.get("providers", {}).items():
            normalized_value = self._single_line(value, 160)
            current_value = self._single_line(self._provider_settings().get(key, ""), 160)
            if self._should_apply_migration_value(current_value, normalized_value, conflict):
                changed_config[key] = normalized_value
        for key, value in normalized.get("settings", {}).items():
            if key in {"storage_backend", "storage_sqlite_path"}:
                continue
            if key in {"group_whitelist_ids", "group_blacklist_ids"}:
                normalized_value = self._normalize_id_list(value)
            elif key == "group_access_mode":
                normalized_mode = str(value or "").strip().lower()
                normalized_value = normalized_mode if normalized_mode in {"whitelist", "blacklist"} else "whitelist"
            else:
                normalized_value = self._normalize_setting_value(key, value)
            current_value = self._migration_current_setting_value(key)
            if self._should_apply_migration_value(current_value, normalized_value, conflict):
                changed_config[key] = normalized_value
        for key, value in changed_config.items():
            self._apply_config_value(key, value, changed_config)
        if "enable_body_monitor_integration" in changed_config:
            runtime_task = getattr(
                self.plugin,
                "_body_monitor_integration_toggle_task",
                None,
            )
            if isinstance(runtime_task, asyncio.Task):
                await runtime_task
        if any(key in self._allowed_provider_keys() for key in changed_config) or "provider_config_mode" in changed_config:
            apply_quick = getattr(self.plugin, "_apply_quick_provider_defaults", None)
            if callable(apply_quick):
                apply_quick()
        config_saved = True
        if changed_config:
            config_saved = await self._save_config_if_possible()
            if not config_saved:
                raise RuntimeError("配置导入持久化失败")

        applied_sections: list[dict[str, Any]] = []
        if data_payload:
            async with self.plugin._data_lock:
                for section, imported_value in data_payload.items():
                    current_value = self.plugin.data.get(section)
                    if mode == "replace" or not isinstance(current_value, dict) or not isinstance(imported_value, dict):
                        self.plugin.data[section] = deepcopy(imported_value)
                    else:
                        merged = deepcopy(current_value)
                        self._deep_merge_dict(merged, imported_value, conflict=conflict)
                        self.plugin.data[section] = merged
                    applied_sections.append(
                        {
                            "key": section,
                            "label": self._migration_section_label(section),
                            "count": self._migration_count_items(imported_value),
                        }
                    )
                self.plugin._save_data_sync(sections=set(data_payload))
            self._refresh_migration_runtime_caches(data_payload)

        overview = await self.get_overview()
        if self._is_http_error_response(overview):
            return overview
        data = overview.get("data") if isinstance(overview.get("data"), dict) else {}
        checks = await self._migration_post_import_checks(config_saved=config_saved)
        data["message"] = "配置已导入。"
        data["mode"] = mode
        data["conflict"] = conflict
        data["backup_path"] = backup_path
        data["config_saved"] = config_saved
        data["changed_config_count"] = len(changed_config)
        data["applied_sections"] = applied_sections
        data["post_import_checks"] = checks
        data["migration_backups"] = self._list_migration_backup_items(limit=8)
        overview["data"] = data
        return overview
    async def _rollback_migration_normalized(
        self,
        config_snapshot: dict[str, Any],
        data_snapshot: dict[str, Any],
        normalized: dict[str, Any],
    ) -> str:
        errors: list[str] = []
        changed_keys = {
            str(key)
            for namespace in ("features", "providers", "settings")
            for key in (
                normalized.get(namespace).keys()
                if isinstance(normalized.get(namespace), dict)
                else ()
            )
        }
        # Reapply old values to reverse runtime-only side effects, then replace
        # the config root again so aliases/default projections cannot alter the
        # exact pre-import image.
        for key in sorted(changed_keys, reverse=True):
            old_value = _flat_get(config_snapshot, key, _MISSING)
            if old_value is _MISSING:
                continue
            try:
                self._apply_config_value(key, deepcopy(old_value), {})
            except Exception as rollback_exc:
                errors.append(f"runtime:{key}:{type(rollback_exc).__name__}")
        config_root = _config_root_mapping(getattr(self.plugin, "config", None))
        if isinstance(config_root, dict):
            try:
                config_root.clear()
                config_root.update(deepcopy(config_snapshot))
            except Exception as rollback_exc:
                errors.append(f"config-memory:{type(rollback_exc).__name__}")
        else:
            errors.append("config-memory:unavailable")

        runtime_task = getattr(
            self.plugin,
            "_body_monitor_integration_toggle_task",
            None,
        )
        if isinstance(runtime_task, asyncio.Task):
            try:
                await asyncio.shield(runtime_task)
            except (asyncio.CancelledError, Exception) as rollback_exc:
                errors.append(f"runtime-task:{type(rollback_exc).__name__}")

        try:
            async with self.plugin._data_lock:
                self.plugin.data.clear()
                self.plugin.data.update(deepcopy(data_snapshot))
                writer = getattr(self.plugin, "_write_data_snapshot_sync", None)
                if not callable(writer):
                    raise RuntimeError("data snapshot writer unavailable")
                writer(deepcopy(data_snapshot))
        except Exception as rollback_exc:
            errors.append(f"data:{type(rollback_exc).__name__}")
        try:
            if not await self._save_config_if_possible():
                errors.append("config-persist:false")
        except Exception as rollback_exc:
            errors.append(f"config-persist:{type(rollback_exc).__name__}")
        return ";".join(errors)
    async def _build_migration_package(self, options: set[str] | None = None, *, include_all: bool = False) -> dict[str, Any]:
        selected = {"basic", "relations", "food_skills", "providers", "sensitive"} if include_all else (options or {"basic", "relations", "food_skills"})
        async with self.plugin._data_lock:
            raw_data = deepcopy(self.plugin.data)
        package = {
            "kind": "private_companion_config_backup",
            "plugin": PLUGIN_NAME,
            "schema": 1,
            "version": self._plugin_version(),
            "exported_at": int(time.time()),
            "included_sections": sorted(selected),
            "settings": self._migration_settings_snapshot(selected),
            "features": self._migration_feature_snapshot(selected),
            "data": self._migration_data_snapshot(raw_data, selected),
            "excluded": [
                "Token 消耗统计",
                "图片/视觉摘要缓存",
                "最近消息和输入状态",
                "主动消息审计与冷却队列",
                "临时任务、排障记录和运行时缓存",
                "本机存储后端与 SQLite 路径",
            ],
        }
        if "providers" in selected:
            package["providers"] = self._migration_provider_snapshot()
        if "sensitive" in selected:
            for namespace, fields in self._migration_unknown_config_fields().items():
                target = package.setdefault(namespace, {})
                if not isinstance(target, dict):
                    continue
                for key, value in fields.items():
                    target.setdefault(key, deepcopy(value))
        package["checksum_algorithm"] = "sha256"
        package["checksum"] = self._migration_checksum(package)
        return package
    def _migration_settings_snapshot(self, selected: set[str]) -> dict[str, Any]:
        if "basic" not in selected and "sensitive" not in selected:
            return {}
        runtime = self._runtime_settings()
        allowed = self._allowed_setting_keys()
        settings = {}
        for key, value in runtime.items():
            if key in {"storage_backend", "storage_sqlite_path"}:
                continue
            if key not in allowed:
                continue
            group = self._migration_setting_group(key)
            if group == "sensitive":
                if "sensitive" in selected:
                    settings[key] = deepcopy(value)
            elif group == "providers":
                if "providers" in selected:
                    settings[key] = deepcopy(value)
            elif "basic" in selected:
                settings[key] = deepcopy(value)
        if "basic" in selected:
            settings["group_access_mode"] = str(getattr(self.plugin, "group_access_mode", "whitelist") or "whitelist")
            settings["group_whitelist_ids"] = list(getattr(self.plugin, "group_whitelist_ids", []) or [])
            settings["group_blacklist_ids"] = list(getattr(self.plugin, "group_blacklist_ids", []) or [])
        if "sensitive" in selected:
            qzone_cookie = self._config_get("QZONE_COOKIE") or str(getattr(self.plugin, "qzone_cookie", "") or "")
            if qzone_cookie:
                settings["QZONE_COOKIE"] = qzone_cookie
            search_api_key = (
                self._config_get("WEB_EXPLORATION_API_KEY")
                or str(getattr(self.plugin, "web_exploration_api_key", "") or "")
            )
            if search_api_key:
                settings["WEB_EXPLORATION_API_KEY"] = search_api_key
        return settings
    def _migration_feature_snapshot(self, selected: set[str]) -> dict[str, bool]:
        if "basic" not in selected and "sensitive" not in selected:
            return {}
        features: dict[str, bool] = {}
        for key in sorted(self._allowed_feature_keys()):
            group = self._migration_setting_group(key)
            if group == "sensitive" and "sensitive" not in selected:
                continue
            if group == "providers" and "providers" not in selected:
                continue
            if group not in {"sensitive", "providers"} and "basic" not in selected:
                continue
            if hasattr(self.plugin, key):
                features[key] = self._normalize_bool_value(getattr(self.plugin, key))
                continue
            raw = self._config_get(key)
            if raw != "":
                features[key] = self._normalize_bool_value(raw)
        return features
    def _migration_provider_snapshot(self) -> dict[str, str]:
        allowed = self._allowed_provider_keys()
        return {key: value for key, value in self._provider_settings().items() if key in allowed}
    def _migration_data_snapshot(self, raw_data: dict[str, Any], selected: set[str]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for section in self._migration_data_sections(selected):
            if section not in raw_data:
                continue
            value = deepcopy(raw_data.get(section))
            if section in {"users", "groups"}:
                value = self._strip_runtime_data(value)
            if self._migration_count_items(value) > 0:
                result[section] = value
        return result
    @classmethod
    def _migration_data_sections(cls, selected: set[str] | None = None) -> tuple[str, ...]:
        selected = selected or {"basic", "relations", "food_skills"}
        sections: list[str] = []
        if "relations" in selected:
            sections.extend(
                [
                    "users",
                    "groups",
                    "worldbook_entries",
                    "worldbook_member_profiles",
                    "worldbook_group_profiles",
                ]
            )
        if "food_skills" in selected:
            sections.extend(["skill_growth", "food_menu", "external_proactive_abilities", "important_dates", "can_do"])
        if "sensitive" in selected:
            sections.extend(["reading_archive_integration", "bookshelf_items"])
        return tuple(sections)
    @staticmethod
    def _migration_setting_group(key: str) -> str:
        text = str(key)
        if text in {"storage_backend", "storage_sqlite_path"}:
            return "environment"
        if text == "provider_config_mode":
            return "providers"
        if text == "QZONE_COOKIE":
            return "sensitive"
        if text == "WEB_EXPLORATION_API_KEY":
            return "sensitive"
        if text.startswith("reading_archive_") or text.startswith("enable_reading_archive_"):
            return "sensitive"
        if text in {"READING_ARCHIVE_VISION_PROVIDER_ID"}:
            return "sensitive"
        if text.endswith("_PROVIDER_ID") or text in {"LLM_PROVIDER_ID", "tts_conversion_provider_id"}:
            return "providers"
        return "basic"
    @staticmethod
    def _migration_section_label(key: str) -> str:
        return {
            "users": "私聊对象资料",
            "groups": "群聊观测资料",
            "worldbook_entries": "关系网原始条目",
            "worldbook_member_profiles": "关系网成员资料",
            "worldbook_group_profiles": "关系网群资料",
            "skill_growth": "技能熟练度",
            "food_menu": "吃什么候选菜单",
            "external_proactive_abilities": "外部主动能力",
            "important_dates": "重要日期",
            "can_do": "自定义能力",
            "reading_archive_integration": "资料归档状态",
            "bookshelf_items": "资料柜阅读记录",
        }.get(key, key)
    def _extract_migration_package(self, payload: Any, *, allow_checksum_mismatch: bool = False) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("导入内容必须是 JSON 对象")
        package = self._unwrap_migration_package_payload(payload)
        if not isinstance(package, dict):
            raise ValueError("没有读取到可导入的配置备份")
        if package.get("kind") != "private_companion_config_backup" or package.get("plugin") != PLUGIN_NAME:
            legacy = self._legacy_snapshot_to_migration_package(package)
            if legacy is None:
                raise ValueError("这不是 Private Companion 的配置备份")
            return legacy
        checksum = str(package.get("checksum") or "").strip()
        if checksum and not self._migration_checksum_matches(package, checksum):
            if not allow_checksum_mismatch:
                raise ValueError("备份校验失败：文件可能被截断或手动修改过。如确认只是手动脱敏/删改敏感字段，请勾选“校验失败仍继续预览/导入”。")
            package = deepcopy(package)
            package["_checksum_mismatch_allowed"] = True
        return package
    def _unwrap_migration_package_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        current = payload
        for _ in range(4):
            if not isinstance(current, dict):
                return {}
            if current.get("kind") == "private_companion_config_backup" or (
                isinstance(current.get("overview"), dict)
                and ("users" in current or "groups" in current)
            ):
                return current
            for key in ("package", "data", "payload", "result"):
                nested = current.get(key)
                if isinstance(nested, dict):
                    current = nested
                    break
            else:
                return current
        return current if isinstance(current, dict) else {}
    def _legacy_snapshot_to_migration_package(self, package: dict[str, Any]) -> dict[str, Any] | None:
        overview = package.get("overview") if isinstance(package.get("overview"), dict) else {}
        has_snapshot_shape = bool(overview) and ("users" in package or "groups" in package)
        if not has_snapshot_shape:
            return None

        settings: dict[str, Any] = {}
        raw_settings = overview.get("settings") if isinstance(overview.get("settings"), dict) else {}
        for key, value in raw_settings.items():
            if key in {"storage_backend", "storage_sqlite_path"}:
                continue
            if key in self._allowed_setting_keys():
                settings[key] = deepcopy(value)
        group_overview = overview.get("group") if isinstance(overview.get("group"), dict) else {}
        if group_overview:
            settings["group_access_mode"] = str(group_overview.get("access_mode") or "whitelist")
            settings["group_whitelist_ids"] = self._normalize_id_list(group_overview.get("whitelist"))
            settings["group_blacklist_ids"] = self._normalize_id_list(group_overview.get("blacklist"))

        features: dict[str, bool] = {}
        raw_features = overview.get("features") if isinstance(overview.get("features"), dict) else {}
        for key, value in raw_features.items():
            if key in self._allowed_feature_keys():
                features[key] = self._normalize_bool_value(value)

        providers: dict[str, str] = {}
        raw_providers = overview.get("providers") if isinstance(overview.get("providers"), dict) else {}
        for key, value in raw_providers.items():
            if key in self._allowed_provider_keys():
                providers[key] = self._single_line(value, 160)

        converted = {
            "kind": "private_companion_config_backup",
            "plugin": PLUGIN_NAME,
            "schema": 1,
            "version": str(package.get("version") or overview.get("plugin", {}).get("version") or self._plugin_version()),
            "exported_at": int(time.time()),
            "included_sections": ["basic", "relations"],
            "settings": settings,
            "features": features,
            "providers": providers,
            "data": {},
            "legacy_snapshot": True,
            "excluded": [
                "由旧版页面快照转换；仅导入快照中可识别的配置、名单、开关和模型指向",
                "旧版页面快照中的私聊/群聊列表是展示摘要，不会写回数据文件",
                "最近消息、缓存、Token、运行日志和临时队列不会导入",
            ],
        }
        converted["checksum_algorithm"] = "sha256"
        converted["checksum"] = self._migration_checksum(converted)
        return converted
    def _normalize_migration_package(self, package: dict[str, Any]) -> dict[str, Any]:
        settings: dict[str, Any] = {}
        features: dict[str, bool] = {}
        providers: dict[str, str] = {}
        ignored: list[str] = []
        unknown_config_fields: dict[str, dict[str, Any]] = {}

        def preserve_unknown(namespace: str, key: Any, value: Any) -> None:
            if self._migration_unknown_key_allowed(key):
                unknown_config_fields.setdefault(namespace, {})[str(key)] = deepcopy(value)
            else:
                ignored.append(str(key))

        raw_settings = package.get("settings") if isinstance(package.get("settings"), dict) else {}
        for key, value in raw_settings.items():
            if key in {"storage_backend", "storage_sqlite_path"}:
                ignored.append(str(key))
                continue
            if key == "group_access_mode":
                mode = str(value or "").strip().lower()
                if mode in {"whitelist", "blacklist"}:
                    settings[key] = mode
                continue
            if key in {"group_whitelist_ids", "group_blacklist_ids"}:
                settings[key] = self._normalize_id_list(value)
                continue
            if key in self._allowed_setting_keys():
                settings[key] = self._normalize_setting_value(key, value)
            else:
                preserve_unknown("settings", key, value)

        raw_features = package.get("features") if isinstance(package.get("features"), dict) else {}
        for key, value in raw_features.items():
            if key in self._allowed_feature_keys():
                features[key] = self._normalize_bool_value(value)
            else:
                preserve_unknown("features", key, value)

        raw_providers = package.get("providers") if isinstance(package.get("providers"), dict) else {}
        for key, value in raw_providers.items():
            if key in self._allowed_provider_keys():
                providers[key] = self._single_line(value, 160)
            else:
                preserve_unknown("providers", key, value)

        raw_data = package.get("data") if isinstance(package.get("data"), dict) else {}
        data: dict[str, Any] = {}
        for section in self._migration_data_sections({"relations", "food_skills", "sensitive"}):
            if section not in raw_data:
                continue
            value = deepcopy(raw_data.get(section))
            if section in {"users", "groups"}:
                value = self._strip_runtime_data(value)
            data[section] = value
        for section in raw_data:
            if section not in data:
                ignored.append(str(section))

        bounded_unknown = self._bounded_migration_unknown_fields(
            unknown_config_fields
        )
        preserved_unknown = sorted(
            f"{namespace}.{key}"
            for namespace, fields in bounded_unknown.items()
            for key in fields
        )
        return {
            "version": package.get("version"),
            "exported_at": package.get("exported_at"),
            "included_sections": [str(item) for item in package.get("included_sections", []) if str(item).strip()],
            "checksum": str(package.get("checksum") or ""),
            "checksum_ok": bool(package.get("checksum")) and self._migration_checksum_matches(package, str(package.get("checksum") or "")),
            "checksum_bypassed": bool(package.get("_checksum_mismatch_allowed")),
            "legacy_snapshot": bool(package.get("legacy_snapshot")),
            "settings": settings,
            "features": features,
            "providers": providers,
            "data": data,
            "unknown_config_fields": bounded_unknown,
            "preserved_unknown": preserved_unknown,
            "ignored": sorted(set(ignored))[:80],
        }
    async def _migration_import_summary(self, normalized: dict[str, Any]) -> dict[str, Any]:
        async with self.plugin._data_lock:
            current_data = deepcopy(self.plugin.data)
        config_diff = self._migration_config_diff(normalized)
        config_count = len(normalized.get("settings", {})) + len(normalized.get("features", {})) + len(normalized.get("providers", {}))
        compatibility = self._migration_compatibility(normalized.get("version"))
        sections: list[dict[str, Any]] = []
        for key, value in (normalized.get("data") or {}).items():
            current_value = current_data.get(key)
            diff = self._migration_diff_counts(current_value, value)
            sections.append(
                {
                    "key": key,
                    "label": self._migration_section_label(key),
                    "count": self._migration_count_items(value),
                    "current_count": self._migration_count_items(current_value),
                    **diff,
                }
            )
        return {
            "version": normalized.get("version") or "",
            "current_version": self._plugin_version(),
            "compatibility": compatibility,
            "exported_at": normalized.get("exported_at") or 0,
            "included_sections": normalized.get("included_sections", []),
            "checksum": normalized.get("checksum") or "",
            "checksum_ok": bool(normalized.get("checksum_ok")),
            "checksum_bypassed": bool(normalized.get("checksum_bypassed")),
            "legacy_snapshot": bool(normalized.get("legacy_snapshot")),
            "config_count": config_count,
            "config_diff": config_diff,
            "settings_count": len(normalized.get("settings", {})),
            "features_count": len(normalized.get("features", {})),
            "providers_count": len(normalized.get("providers", {})),
            "preserved_unknown": normalized.get("preserved_unknown", []),
            "preserved_unknown_count": len(
                normalized.get("preserved_unknown", [])
            ),
            "sections": sections,
            "ignored": normalized.get("ignored", []),
            "excluded": [
                "不会导入 Token 统计、缓存、最近消息、审计日志和临时队列。",
                "导入前会自动保存一份当前可迁移配置备份。",
            ],
        }
    def _write_migration_backup(self, package: dict[str, Any]) -> str:
        data_dir = Path(getattr(self.plugin, "data_dir", "") or Path(getattr(self.plugin, "data_file", ".")).parent)
        backup_dir = data_dir / "config_backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        path = backup_dir / f"private_companion_before_import_{stamp}_{secrets.token_hex(3)}.json"
        if not package.get("checksum"):
            package["checksum_algorithm"] = "sha256"
            package["checksum"] = self._migration_checksum(package)
        path.write_text(json.dumps(package, ensure_ascii=False, indent=2), encoding="utf-8")
        return str(path)
    def _migration_backup_dir(self) -> Path:
        data_dir = Path(getattr(self.plugin, "data_dir", "") or Path(getattr(self.plugin, "data_file", ".")).parent)
        return data_dir / "config_backups"
    def _migration_backup_service(self) -> MigrationBackupService:
        return MigrationBackupService(
            self._migration_backup_dir(),
            checksum_matches=self._migration_checksum_matches,
            error_text=self._single_line,
        )
    def _resolve_migration_backup_path(self, backup_id: str) -> Path:
        return self._migration_backup_service().resolve(backup_id)
    def _list_migration_backup_items(self, *, limit: int = 8) -> list[dict[str, Any]]:
        return self._migration_backup_service().list_items(limit=limit)
    def _migration_checksum(self, package: dict[str, Any]) -> str:
        payload = deepcopy(package)
        payload.pop("checksum", None)
        payload.pop("checksum_algorithm", None)
        text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(text.encode("utf-8")).hexdigest()
    @staticmethod
    def _migration_checksum_digest(payload: dict[str, Any], *, sort_keys: bool, compact: bool) -> str:
        separators = (",", ":") if compact else None
        text = json.dumps(payload, ensure_ascii=False, sort_keys=sort_keys, separators=separators)
        return hashlib.sha256(text.encode("utf-8")).hexdigest()
    def _migration_checksum_candidates(self, package: dict[str, Any]) -> set[str]:
        candidates: set[str] = set()
        for remove_algorithm in (True, False):
            payload = deepcopy(package)
            payload.pop("checksum", None)
            if remove_algorithm:
                payload.pop("checksum_algorithm", None)
            for sort_keys in (True, False):
                for compact in (True, False):
                    try:
                        candidates.add(
                            self._migration_checksum_digest(
                                payload,
                                sort_keys=sort_keys,
                                compact=compact,
                            )
                        )
                    except Exception:
                        continue
        return candidates
    def _migration_checksum_matches(self, package: dict[str, Any], expected: str) -> bool:
        checksum = str(expected or "").strip().lower()
        if not checksum:
            return False
        return checksum in self._migration_checksum_candidates(package)
    def _parse_migration_version(self, value: Any) -> tuple[int, int, int] | None:
        match = re.search(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?", str(value or ""))
        if not match:
            return None
        return tuple(int(part or 0) for part in match.groups())
    def _migration_compatibility(self, backup_version: Any) -> dict[str, Any]:
        current_text = self._plugin_version()
        backup_text = str(backup_version or "")
        current = self._parse_migration_version(current_text)
        backup = self._parse_migration_version(backup_text)
        level = "ok"
        message = "版本接近，可以按预览结果导入。"
        if not backup or not current:
            level = "unknown"
            message = "无法判断版本跨度，建议合并导入，不建议覆盖。"
        elif backup[0] != current[0] or abs((current[1] if len(current) > 1 else 0) - (backup[1] if len(backup) > 1 else 0)) >= 2:
            level = "warn"
            message = "版本跨度较大，建议合并导入，不建议覆盖。"
        elif backup > current:
            level = "warn"
            message = "备份来自更新版本，建议合并导入，不建议覆盖。"
        return {
            "level": level,
            "backup_version": backup_text or "未知",
            "current_version": current_text,
            "message": message,
        }
    async def _migration_post_import_checks(self, *, config_saved: bool) -> list[dict[str, str]]:
        async with self.plugin._data_lock:
            data = deepcopy(self.plugin.data)
        checks: list[dict[str, str]] = []

        def add(level: str, title: str, detail: str) -> None:
            checks.append({"level": level, "title": title, "detail": detail})

        add("ok" if config_saved else "warn", "配置保存", "配置已写入并保存。" if config_saved else "运行态已写入，但配置持久化可能失败。")
        mode = str(getattr(self.plugin, "group_access_mode", "whitelist") or "whitelist")
        whitelist = list(getattr(self.plugin, "group_whitelist_ids", []) or [])
        blacklist = list(getattr(self.plugin, "group_blacklist_ids", []) or [])
        if mode == "whitelist" and not whitelist:
            add("warn", "群聊名单", "当前是白名单模式，但白名单为空；群聊能力可能不会生效。")
        else:
            add("ok", "群聊名单", f"当前为{'白名单' if mode == 'whitelist' else '黑名单'}模式，白名单 {len(whitelist)} 个，黑名单 {len(blacklist)} 个。")

        available_ids = {str(item.get("id") or "") for item in self._available_provider_items()}
        try:
            available_ids.update(
                str(item.get("id") or "")
                for item in await self._available_embedding_provider_items()
                if str(item.get("id") or "")
            )
        except Exception:
            pass
        configured = {key: value for key, value in self._provider_settings().items() if str(value or "").strip()}
        missing = [value for value in configured.values() if available_ids and value not in available_ids]
        if configured and not available_ids:
            add("warn", "模型配置", f"已配置 {len(configured)} 个模型指向，但当前无法读取 AstrBot Provider 列表，暂不能校验是否存在。")
        elif missing:
            add("warn", "模型配置", f"有 {len(missing)} 个 Provider ID 当前未在 AstrBot 中找到。")
        else:
            add("ok", "模型配置", f"已配置 {len(configured)} 个模型指向，当前未发现缺失 Provider。")

        members = data.get("worldbook_member_profiles") if isinstance(data.get("worldbook_member_profiles"), dict) else {}
        groups = data.get("worldbook_group_profiles") if isinstance(data.get("worldbook_group_profiles"), dict) else {}
        if not members and not groups:
            add("warn", "关系网", "当前没有关系网成员或群资料；如果刚导入关系网备份，可能需要检查导入内容。")
        else:
            add("ok", "关系网", f"成员资料 {len(members)} 个，群资料 {len(groups)} 个。")

        food = data.get("food_menu") if isinstance(data.get("food_menu"), dict) else {}
        food_items = food.get("items") if isinstance(food.get("items"), dict) else food
        if isinstance(food_items, dict):
            add("ok" if food_items else "warn", "吃什么候选", f"候选菜单可读取，当前 {len(food_items)} 项。")
        else:
            add("warn", "吃什么候选", "候选菜单结构不是预期格式，请到功能页检查。")
        return checks
    @staticmethod
    def _migration_count_items(value: Any) -> int:
        if isinstance(value, dict):
            return len(value)
        if isinstance(value, list):
            return len(value)
        return 1 if value not in (None, "", [], {}) else 0
    def _migration_current_setting_value(self, key: str) -> Any:
        if key == "group_access_mode":
            return str(getattr(self.plugin, "group_access_mode", "whitelist") or "whitelist")
        if key == "group_whitelist_ids":
            return list(getattr(self.plugin, "group_whitelist_ids", []) or [])
        if key == "group_blacklist_ids":
            return list(getattr(self.plugin, "group_blacklist_ids", []) or [])
        if hasattr(self.plugin, key):
            return deepcopy(getattr(self.plugin, key))
        return self._config_get(key)
    @staticmethod
    def _migration_value_empty(value: Any) -> bool:
        return value is None or value == "" or value == [] or value == {}
    def _should_apply_migration_value(self, current_value: Any, incoming_value: Any, conflict: str) -> bool:
        if current_value == incoming_value:
            return False
        if conflict == "keep_current":
            return False
        if conflict == "fill_empty":
            return self._migration_value_empty(current_value)
        return True
    def _migration_diff_counts(self, current: Any, incoming: Any) -> dict[str, int]:
        if isinstance(incoming, dict):
            current_dict = current if isinstance(current, dict) else {}
            added = 0
            overwritten = 0
            unchanged = 0
            for key, value in incoming.items():
                if key not in current_dict:
                    added += 1
                elif current_dict.get(key) == value:
                    unchanged += 1
                else:
                    overwritten += 1
            return {"added": added, "overwritten": overwritten, "unchanged": unchanged}
        if isinstance(incoming, list):
            if not isinstance(current, list) or not current:
                return {"added": len(incoming), "overwritten": 0, "unchanged": 0}
            if current == incoming:
                return {"added": 0, "overwritten": 0, "unchanged": len(incoming)}
            return {"added": 0, "overwritten": len(incoming), "unchanged": 0}
        return {
            "added": 1 if self._migration_value_empty(current) and not self._migration_value_empty(incoming) else 0,
            "overwritten": 1 if not self._migration_value_empty(current) and current != incoming else 0,
            "unchanged": 1 if current == incoming else 0,
        }
    def _migration_config_diff(self, normalized: dict[str, Any]) -> dict[str, int]:
        counts = {"added": 0, "overwritten": 0, "unchanged": 0}
        for key, value in (normalized.get("settings") or {}).items():
            diff = self._migration_diff_counts(self._migration_current_setting_value(key), value)
            for item in counts:
                counts[item] += diff[item]
        provider_snapshot = self._provider_settings()
        for key, value in (normalized.get("providers") or {}).items():
            diff = self._migration_diff_counts(provider_snapshot.get(key, ""), value)
            for item in counts:
                counts[item] += diff[item]
        for key, value in (normalized.get("features") or {}).items():
            diff = self._migration_diff_counts(self._normalize_bool_value(getattr(self.plugin, key, self._config_get(key))), self._normalize_bool_value(value))
            for item in counts:
                counts[item] += diff[item]
        return counts
    def _refresh_migration_runtime_caches(self, data_payload: dict[str, Any]) -> None:
        if "external_proactive_abilities" in data_payload and hasattr(self.plugin, "_external_proactive_abilities"):
            store = self.plugin.data.get("external_proactive_abilities")
            if isinstance(store, dict):
                self.plugin._external_proactive_abilities = deepcopy(store)
