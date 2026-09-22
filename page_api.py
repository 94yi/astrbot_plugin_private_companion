# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import functools
import io
import json
import math
import os
import time
import re
import shutil
import base64
import binascii
import hmac
import hashlib
import mimetypes
import secrets
import sqlite3
import sys
import uuid
from contextlib import asynccontextmanager
from copy import copy, deepcopy
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping
from urllib.parse import quote, urlparse

from astrbot.api.event import MessageChain
from astrbot.core.utils.astrbot_path import get_astrbot_data_path
from quart import request, send_file


def _multi_persona_page_context(function):
    @functools.wraps(function)
    async def wrapper(self, *args, **kwargs):
        plugin = getattr(self, "plugin", None)
        activator = getattr(plugin, "_activate_persona_id", None)
        primary_getter = getattr(plugin, "_primary_persona_id", None)
        pid = primary_getter() if callable(primary_getter) else ""
        active_getter = getattr(plugin, "_active_persona_scope", None)
        active = str(active_getter() if callable(active_getter) else "").strip()
        token = (
            activator(pid, allow_inactive=True)
            if not active and callable(activator) and pid
            else None
        )
        try:
            return await function(self, *args, **kwargs)
        finally:
            deactivator = getattr(plugin, "_deactivate_persona_for_event", None)
            if token is not None and callable(deactivator):
                deactivator(token)
    return wrapper

from .constants import (
    DEFAULT_DAILY_PLAN_ITEMS,
    PAGE_FONT_NAMES,
    PAGE_THEME_NAMES,
    WORLDBOOK_IMPORTANT_MEMORY_CAPACITY,
    WORLDBOOK_PENDING_OBSERVATION_CAPACITY,
    _REASON_TEXT,
)
from .config_migration import _config_root_mapping, _ensure_config_parent_dir
from .conversation_prompt_section import (
    PromptRenderMode,
    prompt_document,
    prompt_heading_ref,
    prompt_section,
    render_prompt_content,
    render_prompt_document,
    render_prompt_sections,
)
from .diagnostic_envelope import DIAGNOSTIC_ENVELOPE_VERSION, diagnostic_test_id, normalize_diagnostic_result
from .helpers import _MISSING, _flat_get, _normalize_timezone_name, _normalize_timezone_setting, _path_text, _redact_outbound_secrets, _safe_int, _set_into_config, _strip_internal_message_blocks, _text_looks_garbled, _text_similarity, _today_key, normalize_bot_relationship_cards
from .persona_config import runtime_persona_setting
from .wardrobe import WARDROBE_MAX_DESCRIPTION, WARDROBE_MAX_NAME, WARDROBE_MAX_TAG
from .wardrobe_assets import asset_abs_path, asset_root, load_asset_index
from .story_authority import (
    StoryAuthorityError,
    story_authority_controller,
    story_legacy_operation,
    story_legacy_operation_if,
)
from .reference_asset_gate import ReferenceAssetGate
from .owned_reaction_asset_catalog import MAX_ASSET_BYTES, OwnedReactionAssetCatalog
from .companion_interaction_expression import current_interaction_projection, normalize_normal_interaction_band_cap
from .expression_scope_ownership import (
    ExpressionScopeError,
    bind_expression_item,
    bind_expression_profile,
    validate_expression_scope_binding,
)
from .relationship_ledger import (
    migrate_legacy_relationship_score,
    migrate_relationship_positive_stage_cap,
    normalize_relationship_mode,
    normalize_relationship_positive_stage_cap_key,
    relationship_ledger_summary,
)
from .relationship_policy import (
    normalize_relationship_stage_provider_routes,
    normalize_relationship_stage_policy,
    relationship_stage_for_score,
    relationship_stage_policy_json,
)
from .runtime_config_dispatcher import (
    TTS_RUNTIME_KEYS,
    dispatch_runtime_config_effects,
)
from .page_api_qzone import PrivateCompanionPageApiQzoneMixin
from .page_api_users_groups import PrivateCompanionPageApiUsersGroupsMixin
from .page_api_persona import PrivateCompanionPageApiPersonaMixin
from .page_api_expression import PrivateCompanionPageApiExpressionMixin
from .page_api_worldbook import PrivateCompanionPageApiWorldbookMixin
from .page_api_diagnostics import PrivateCompanionPageApiDiagnosticsMixin
from .page_api_migration import PrivateCompanionPageApiMigrationMixin
from .page_api_proactive import PrivateCompanionPageApiProactiveMixin
from .page_api_media import PrivateCompanionPageApiMediaMixin
from .page_api_settings import PageSettingNormalizerMixin
from .model_routing import build_rules, normalize_scope
from .planning import evaluate_daily_plan_quality, generate_daily_plan, generate_detail_enhancement
from .agenda_contracts import timezone_or_default
from .calendar_contracts import (
    AgendaContractError,
    calendar_lifecycle_summary,
    normalize_calendar_record,
    normalize_calendar_records,
    resolve_calendar_timeline,
    resolve_calendar_snapshot,
)
from .memo_notes import (
    apply_memo_note_action,
    memo_note_due_state,
    memo_note_sort_key,
    normalize_memo_note,
)
from .photo_reference_catalog import (
    CATALOG_VERSION,
    MAX_LIBRARY_REFERENCES,
    CatalogValidationError,
    PhotoReference,
    load_catalog,
    project_reference_candidate,
    validate_and_serialize,
)
from .photo_reference_metadata import (
    build_reference_metadata_review_prompt,
    compile_reference_metadata,
    merge_reference_questionnaire_evidence,
    normalize_reviewed_reference_intent,
)
from .photo_reference_selection import SelectionResult, run_photo_selection_trial
from .reference_assets import (
    REFERENCE_ASSET_MAX_BYTES,
    REFERENCE_ASSET_MAX_PER_OWNER,
    REFERENCE_ASSET_MAX_TOTAL,
    REFERENCE_ASSET_ROLES,
    normalize_reference_asset,
    normalize_reference_asset_scope,
    normalize_reference_owner_id,
)
from .reaction_asset_library import get_reaction_asset_library
from .logging_util import get_module_logger
from .page_api_social_group import PrivateCompanionPageApiSocialGroupMixin
from .page_api_food_body import PrivateCompanionPageApiFoodBodyMixin
from .page_api_memory_recall import PrivateCompanionPageApiMemoryRecallMixin
from .page_api_calendar_daily import PrivateCompanionPageApiCalendarDailyMixin
from .page_api_creative import PrivateCompanionPageApiCreativeMixin
from .page_api_creative import _render_page_background_prompt, _render_page_background_prompt_pair  # noqa: F401 (兼容 page_api._render_page_background_prompt* 旧命名空间)
from .page_api_summary_panel import PrivateCompanionPageApiSummaryPanelMixin
from .page_api_bookshelf import PrivateCompanionPageApiBookshelfMixin
from .page_api_bookshelf import BOOKSHELF_ACCESS_TOKEN_MAX_PERSISTED, BOOKSHELF_ACCESS_TOKEN_TTL_SECONDS  # noqa: F401 (兼容 from page_api import BOOKSHELF_*)
from .page_api_tts import PrivateCompanionPageApiTtsMixin
from .page_api_config import PrivateCompanionPageApiConfigMixin
from .page_backend import MigrationBackupService, build_route_bindings, generation_log_candidates
from .task_prompt_registry import (
    TASK_PROMPT_CONFIG_KEY,
    TASK_PROMPT_GROUPS,
    catalog_task_prompts,
    normalize_task_prompt_overrides,
    validate_task_prompt_override,
)

logger = get_module_logger(__name__)



PLUGIN_NAME = "astrbot_plugin_private_companion"
PAGE_API_PREFIX = f"/{PLUGIN_NAME}/page"
_MIGRATION_UNKNOWN_CONFIG_KEY = "_migration_unknown_config_fields_v1"
_MIGRATION_UNKNOWN_NAMESPACES = ("settings", "features", "providers")
_MIGRATION_UNKNOWN_MAX_BYTES = 256 * 1024
_MIGRATION_UNKNOWN_MAX_FIELDS = 128
_MIGRATION_UNKNOWN_SENSITIVE_NAME = re.compile(
    r"(?:access[_-]?token|password|secret|cookie|api[_-]?key|storage[_-])",
    flags=re.I,
)
EXTENSION_MIGRATION_NOTICE_VERSION = "6.2.2"
IMAGE_CACHE_THUMBNAIL_MAX_EDGE = 160
IMAGE_CACHE_THUMBNAIL_QUALITY = 78
PHOTO_REFERENCE_PREVIEW_MAX_BYTES = 20 * 1024 * 1024
# The guided editor must never leave a WebUI request waiting forever when the
# configured main model or its upstream connection stops responding.
PHOTO_REFERENCE_METADATA_REVIEW_TIMEOUT_SECONDS = 60.0
# Reference assets are an independent store for member/role/knowledge images. Keep
# the limits generous enough for a small visual knowledge base while preventing
# an accidental page upload from exhausting the plugin data directory.
PHOTO_REFERENCE_ASSET_MAX_BYTES = 12 * 1024 * 1024
PHOTO_REFERENCE_ASSET_MAX_COUNT = 256
PHOTO_REFERENCE_ASSET_MAX_PER_OWNER = 32
# WebUI catalog uploads are content-addressed so repeated submissions do not
# create another copy of the same image. These limits cover abandoned uploads
# that are no longer referenced by the saved catalog.
PHOTO_REFERENCE_UPLOAD_MAX_COUNT = 256
PHOTO_REFERENCE_UPLOAD_MAX_TOTAL_BYTES = 1024 * 1024 * 1024
# A 12 MiB image expands to roughly 16 MiB when Base64 encoded. Leave room for
# the data URL and JSON envelope, while rejecting oversized bodies before
# Quart parses them into memory.
PHOTO_REFERENCE_UPLOAD_MAX_REQUEST_BYTES = 20 * 1024 * 1024
PHOTO_REFERENCE_ASSET_SCOPES = {"relation_user", "group", "knowledge"}
PHOTO_REFERENCE_ASSET_MIMES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}

EXTERNAL_API_TEST_SETTING_ATTRS: dict[str, dict[str, str]] = {
    "weather_api": {
        "weather_source": "weather_source",
        "weather_api_host": "weather_api_host",
        "weather_token": "weather_token",
        "weather_location": "weather_location",
        "weather_api_key": "weather_api_key",
        "weather_city": "weather_city",
        "weather_amap_api_key": "weather_amap_api_key",
        "weather_amap_city": "weather_amap_city",
        "weather_lat": "weather_lat",
        "weather_lon": "weather_lon",
    },
    "balance_api": {
        "balance_api_url": "balance_api_url",
        "balance_api_key": "balance_api_key",
        "balance_api_auth_header": "balance_api_auth_header",
        "balance_api_auth_scheme": "balance_api_auth_scheme",
        "balance_api_custom_headers": "balance_api_custom_headers",
        "balance_json_path": "balance_json_path",
        "balance_total_json_path": "balance_total_json_path",
        "balance_used_json_path": "balance_used_json_path",
        "balance_value_divisor": "balance_value_divisor",
        "balance_currency_label": "balance_currency_label",
        "balance_request_timeout_seconds": "balance_request_timeout_seconds",
    },
    "web_search": {
        "WEB_EXPLORATION_API_BASE_URL": "web_exploration_api_base_url",
        "WEB_EXPLORATION_API_KEY": "web_exploration_api_key",
        "WEB_EXPLORATION_API_MODEL": "web_exploration_api_model",
        "web_exploration_max_results": "web_exploration_max_results",
    },
}

EXTERNAL_API_TEST_SECRET_FIELDS = frozenset(
    {
        "weather_token",
        "weather_api_key",
        "weather_amap_api_key",
        "balance_api_key",
        "balance_api_custom_headers",
        "WEB_EXPLORATION_API_KEY",
    }
)

# These values configure the page/API trust boundary itself. They must remain
# available to AstrBot's native config editor, but must never be projected back
# through the companion panel or accepted by its migration importer.

_DEBUG_TAIL_MAX_WINDOW_BYTES = 16 * 1024 * 1024

class _PageApiError(dict[str, Any]):
    """Dictionary-compatible API error with HTTP-only status metadata."""

    __slots__ = ("http_status",)

    def __init__(self, payload: dict[str, Any], http_status: int) -> None:
        super().__init__(payload)
        self.http_status = int(http_status)


# Keep the cycle editor contract explicit.  These settings live inside the
# humanized-state schema group, but the page API must remain usable when an
# older AstrBot process has not rebuilt its schema index yet.
try:
    from PIL import Image as PILImage
    from PIL import ImageOps as PILImageOps
except Exception:  # pragma: no cover - Pillow 缺失时回退到原图预览
    PILImage = None
    PILImageOps = None


class PrivateCompanionPageApi(
    PageSettingNormalizerMixin,
    PrivateCompanionPageApiQzoneMixin,
    PrivateCompanionPageApiUsersGroupsMixin,
    PrivateCompanionPageApiMediaMixin,
    PrivateCompanionPageApiPersonaMixin,
    PrivateCompanionPageApiExpressionMixin,
    PrivateCompanionPageApiWorldbookMixin,
    PrivateCompanionPageApiDiagnosticsMixin,
    PrivateCompanionPageApiMigrationMixin,
    PrivateCompanionPageApiProactiveMixin,
    PrivateCompanionPageApiConfigMixin,
    PrivateCompanionPageApiTtsMixin,
    PrivateCompanionPageApiBookshelfMixin,
    PrivateCompanionPageApiSummaryPanelMixin,
    PrivateCompanionPageApiCreativeMixin,
    PrivateCompanionPageApiCalendarDailyMixin,
    PrivateCompanionPageApiMemoryRecallMixin,
    PrivateCompanionPageApiFoodBodyMixin,
    PrivateCompanionPageApiSocialGroupMixin,
):
    """AstrBot 官方插件拓展页面 API。"""

    @staticmethod
    def _save_plugin_sections(plugin: Any, sections: set[str]) -> None:
        saver = getattr(plugin, "_save_data_sync", None)
        if not callable(saver):
            return
        saver(sections=sections)

    IMAGE_API_RUNTIME_SETTING_KEYS = {
        "external_image_api_platform",
        "EXTERNAL_IMAGE_API_BASE_URL",
        "EXTERNAL_IMAGE_API_KEY",
        "EXTERNAL_IMAGE_API_MODEL",
        "external_image_api_size",
        "external_image_api_timeout_seconds",
        "external_image_api_custom_headers",
        "external_image_download_proxy",
        "external_image_download_use_environment_proxy",
        "external_image_api_endpoints",
        "enable_backup_external_image_api",
        "backup_external_image_api_platform",
        "BACKUP_EXTERNAL_IMAGE_API_BASE_URL",
        "BACKUP_EXTERNAL_IMAGE_API_KEY",
        "BACKUP_EXTERNAL_IMAGE_API_MODEL",
        "backup_external_image_api_size",
        "backup_external_image_api_timeout_seconds",
        "backup_external_image_api_custom_headers",
    }

    PERCENT_PROBABILITY_KEYS = {
        "group_repeat_follow_probability",
        "group_repeat_interrupt_probability",
        "group_repeat_interrupt_probability_step",
        "group_wakeup_interest_probability",
        "group_wakeup_topic_interest_max_boost",
        "group_wakeup_debounce_pending_penalty",
        "tts_trigger_probability",
        "auto_voice_probability",
        "main_user_mention_voice_probability",
        "rest_reply_probability",
        "proactive_photo_text_probability",
        "proactive_share_probability",
    }
    INHERIT_PERCENT_PROBABILITY_KEYS = {
        "tts_private_trigger_probability",
        "tts_group_trigger_probability",
        "main_user_voice_probability",
    }
    FRACTIONAL_PERCENT_SETTING_KEYS = {
        "reaction_expression_trigger_probability",
        "reaction_expression_embedding_score_threshold",
        "bilibili_share_probability",
        "news_share_probability",
        "external_event_self_link_probability",
        "web_exploration_share_probability",
        "qzone_life_publish_probability",
        "qzone_generated_image_probability",
        "qzone_emotional_vent_probability",
        "proactive_review_hard_risk_threshold",
        "proactive_review_low_score_threshold",
        "proactive_review_pressure_threshold",
        "smart_silence_min_confidence",
        "reading_archive_share_probability",
        "reading_archive_ask_probability",
        "creative_inspiration_probability",
        "creative_share_probability",
        "skill_growth_schedule_influence_strength",
    }
    PERSONALITY_AUTO_TUNE_KEYS = {
        "proactive_intensity_preset",
        "max_daily_messages",
        "idle_minutes",
        "min_interval_minutes",
        "proactive_persona_judge_send_threshold",
        "proactive_review_strength",
    }
    PERSONALITY_AUTO_TUNE_RECOVERY_STREAK = 3
    PERSONALITY_AUTO_TUNE_RECOVERY_MIN_SECONDS = 5 * 60
    TROUBLESHOOTING_PROACTIVE_SUMMARY_CACHE_SECONDS = 20.0

    def __init__(self, plugin: Any) -> None:
        self.plugin = plugin
        self._schema_key_index_cache: dict[str, Any] | None = None
        self._proactive_task_summary_task: asyncio.Task[dict[str, Any]] | None = None
        self._proactive_task_summary_cache: dict[str, Any] = {}
        self._proactive_task_summary_cache_at = 0.0
        self._proactive_task_summary_cache_ready = False
        self._proactive_task_summary_generation = 0


    @staticmethod
    def _p4_page_status_projection() -> dict[str, Any]:
        """Expose only fixed P4 boundaries; never resolve a user or ledger."""
        return {
            "schema_version": "chat.p4.page_status.v1",
            "scope": "chat_event_only",
            "reply_gate": "host_verified_event_only",
            "warmth": "host_verified_event_only",
            "confinement": "not_exposed_to_page",
            "manual_review": "not_migrated",
            "action_available": False,
        }




    def _create_page_background_task(self, operation: Any, *, label: str) -> asyncio.Task | None:
        creator = getattr(self.plugin, "_create_lifecycle_background_task", None)
        if callable(creator):
            task = creator(operation, label=label)
            if task is None:
                close = getattr(operation, "close", None)
                if callable(close):
                    close()
            return task
        try:
            task = asyncio.create_task(operation, name=f"private-companion-page-{label}")
        except RuntimeError:
            close = getattr(operation, "close", None)
            if callable(close):
                close()
            return None

        def consume(done_task: asyncio.Task) -> None:
            try:
                done_task.result()
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.warning(
                    "background task failed: label=%s error=%s",
                    label,
                    self._single_line(exc, 160),
                )

        task.add_done_callback(consume)
        return task






    def _http_status_route_handler(self, handler):
        """Apply transport status codes without changing direct-call results."""

        @functools.wraps(handler)
        async def wrapper(*args, **kwargs):
            return self._as_http_response(await handler(*args, **kwargs))

        return wrapper


    # Calendar records are durable constraints and are intentionally exposed
    # separately from the generated ``daily_plan``.  These small helpers keep
    # range parsing and the response contract consistent across the page and
    # the standalone API transport.
    def register_routes(self) -> None:
        register = self.plugin.context.register_web_api
        for path, handler, methods, desc in self.route_bindings():
            register(f"{PAGE_API_PREFIX}{path}", handler, methods, desc)



    def _task_prompt_overrides(self) -> dict[str, str]:
        """Return only normalized overrides owned by this plugin.

        The runtime attribute is authoritative after a hot update.  Before
        plugin initialization creates that attribute, fall back to the
        persisted configuration value.  The registry filters unknown and
        ``astrbot_*`` conversation keys in either case.
        """
        runtime_value = getattr(self.plugin, TASK_PROMPT_CONFIG_KEY, _MISSING)
        raw = runtime_value
        if raw is _MISSING:
            raw = self._config_get_raw(TASK_PROMPT_CONFIG_KEY, {})
        try:
            return normalize_task_prompt_overrides(raw)
        except Exception:
            return {}

    def _task_prompt_payload(
        self,
        overrides: Any = None,
        *,
        config_saved: bool | None = None,
        changed: list[str] | None = None,
    ) -> dict[str, Any]:
        normalized = normalize_task_prompt_overrides(
            self._task_prompt_overrides() if overrides is None else overrides
        )
        rows = catalog_task_prompts(normalized)
        customized_count = sum(1 for row in rows if bool(row.get("customized")))
        payload: dict[str, Any] = {
            "config_key": TASK_PROMPT_CONFIG_KEY,
            "groups": list(TASK_PROMPT_GROUPS),
            "tasks": rows,
            "overrides": normalized,
            "configured_count": len(normalized),
            "customized_count": customized_count,
        }
        if config_saved is not None:
            payload["config_saved"] = bool(config_saved)
        if changed is not None:
            payload["changed"] = list(changed)
        return payload

    async def get_task_prompts(self) -> dict[str, Any]:
        """List every editable prompt used by plugin-internal task models."""
        try:
            return self._ok(self._task_prompt_payload())
        except Exception as exc:
            logger.warning("读取任务模型提示词目录失败: %s", self._single_line(exc, 180))
            return self._exception_error("读取任务模型提示词失败")

    @staticmethod
    def _task_prompt_payload_value(raw_value: Any, *, reset: bool = False) -> Any:
        """Extract the accepted editor value from a single update entry."""
        if reset or raw_value is None:
            return ""
        if isinstance(raw_value, Mapping):
            if raw_value.get("reset") is True:
                return ""
            for key in ("custom_prompt", "prompt", "value"):
                if key in raw_value:
                    raw_value = raw_value.get(key)
                    break
        return raw_value

    def _task_prompt_update_entries(self, payload: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
        """Parse single/batch/reset forms into ``{key: value}`` updates."""
        entries: dict[str, Any] = {}
        reset_all = payload.get("reset_all") is True

        def add(key: Any, value: Any, *, reset: bool = False) -> None:
            clean_key = self._single_line(key, 120).strip().lower()
            if not clean_key:
                raise ValueError("缺少任务模型提示词标识")
            entries[clean_key] = self._task_prompt_payload_value(value, reset=reset)

        for source_name in ("overrides", "updates", "items"):
            source = payload.get(source_name)
            if isinstance(source, Mapping):
                for key, value in source.items():
                    if isinstance(value, Mapping):
                        add(key, value, reset=value.get("reset") is True)
                    else:
                        add(key, value)
            elif isinstance(source, list):
                for item in source:
                    if not isinstance(item, Mapping):
                        raise ValueError("批量任务提示词条目格式无效")
                    key = item.get("task_key") or item.get("override_key") or item.get("key")
                    add(key, item, reset=item.get("reset") is True)

        if payload.get("task_key") is not None or payload.get("key") is not None:
            key = payload.get("task_key") or payload.get("key")
            add(key, payload.get("prompt", payload.get("custom_prompt", payload.get("value"))), reset=payload.get("reset") is True)

        reset_keys = payload.get("reset_keys") or payload.get("remove_keys") or []
        if isinstance(reset_keys, (str, bytes)):
            reset_keys = [reset_keys]
        if not isinstance(reset_keys, list):
            raise ValueError("reset_keys 必须是任务标识列表")
        for key in reset_keys:
            add(key, "", reset=True)
        if payload.get("reset") is True and not entries:
            key = payload.get("override_key")
            if key is not None:
                add(key, "", reset=True)
        return entries, reset_all

    def _task_prompt_update_lock(self) -> asyncio.Lock:
        """Serialize prompt persistence and rollback for one plugin instance."""
        lock = getattr(self.plugin, "_task_prompt_update_lock", None)
        if not isinstance(lock, asyncio.Lock):
            lock = asyncio.Lock()
            setattr(self.plugin, "_task_prompt_update_lock", lock)
        return lock

    async def update_task_prompts(self) -> dict[str, Any]:
        """Save plugin-internal task prompt additions without touching chat prompts."""
        payload = await request.get_json(silent=True) or {}
        if not isinstance(payload, Mapping):
            return self._error("任务提示词请求格式无效")
        async with self._task_prompt_update_lock():
            return await self._update_task_prompts_unlocked(payload)

    async def _update_task_prompts_unlocked(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Apply one validated update while the instance update lock is held."""
        try:
            entries, reset_all = self._task_prompt_update_entries(payload)
            current = self._task_prompt_overrides()
            replace_all = payload.get("replace") is True or str(payload.get("mode") or "").strip().lower() == "replace"
            updated = {} if replace_all or reset_all else dict(current)
            changed: set[str] = set()
            if reset_all:
                changed.update(current)
            for key, raw_value in entries.items():
                value = self._task_prompt_payload_value(raw_value)
                try:
                    normalized_value = validate_task_prompt_override(key, value)
                except ValueError as exc:
                    raise ValueError(f"{key}：{exc}") from exc
                if normalized_value:
                    if updated.get(key) != normalized_value:
                        changed.add(key)
                    updated[key] = normalized_value
                elif key in updated:
                    updated.pop(key, None)
                    changed.add(key)
            if replace_all:
                # In replacement mode, entries omitted by the client are
                # intentionally removed; report those keys as changed too.
                changed.update(set(current) - set(updated))
            if not changed and updated == current:
                return self._ok(self._task_prompt_payload(current, config_saved=True, changed=[]))

            before_runtime = getattr(self.plugin, TASK_PROMPT_CONFIG_KEY, _MISSING)
            before_config = self._config_get_raw(TASK_PROMPT_CONFIG_KEY, _MISSING)

            def rollback_task_prompt_state() -> None:
                """Restore both persisted and runtime values after a failed save."""
                if before_config is _MISSING:
                    config = getattr(self.plugin, "config", None)
                    try:
                        if isinstance(config, dict):
                            config.pop(TASK_PROMPT_CONFIG_KEY, None)
                        elif config is not None:
                            del config[TASK_PROMPT_CONFIG_KEY]
                    except Exception:
                        pass
                else:
                    try:
                        self._set_config_value(TASK_PROMPT_CONFIG_KEY, deepcopy(before_config))
                    except Exception:
                        pass
                try:
                    if before_runtime is _MISSING:
                        delattr(self.plugin, TASK_PROMPT_CONFIG_KEY)
                    else:
                        setattr(self.plugin, TASK_PROMPT_CONFIG_KEY, before_runtime)
                except Exception:
                    pass

            # The schema intentionally declares this hidden compatibility
            # field as ``text``. Persist JSON text so AstrBot's config
            # validator accepts the value across reloads; the runtime
            # attribute remains a normalized mapping for fast lookups.
            persisted_overrides = json.dumps(updated, ensure_ascii=False, separators=(",", ":"))
            self._set_config_value(TASK_PROMPT_CONFIG_KEY, persisted_overrides)
            setattr(self.plugin, TASK_PROMPT_CONFIG_KEY, deepcopy(updated))
            try:
                config_saved = await self._save_config_if_possible()
            except Exception:
                rollback_task_prompt_state()
                raise
            if not config_saved:
                rollback_task_prompt_state()
                return self._error("保存任务模型提示词失败", status_code=500)
            return self._ok(
                self._task_prompt_payload(
                    updated,
                    config_saved=config_saved,
                    changed=sorted(changed),
                )
            )
        except ValueError as exc:
            return self._error(str(exc))
        except Exception as exc:
            logger.warning("保存任务模型提示词失败: %s", self._single_line(exc, 180), exc_info=True)
            return self._exception_error("保存任务模型提示词失败")














    def _reaction_library(self):
        library = get_reaction_asset_library(self.plugin)
        if library is None:
            raise RuntimeError("插件数据目录尚未初始化")
        return library

    def _reaction_library_analysis_lock(self) -> asyncio.Lock:
        lock = getattr(self.plugin, "_reaction_library_analysis_lock", None)
        if not isinstance(lock, asyncio.Lock):
            lock = asyncio.Lock()
            setattr(self.plugin, "_reaction_library_analysis_lock", lock)
        return lock





    @staticmethod
    def _parse_reaction_library_analysis(text: Any, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        raw = str(text or "").strip()
        if not raw:
            return []
        fenced = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", raw, flags=re.IGNORECASE)
        if fenced:
            raw = fenced.group(1).strip()
        candidates = [raw]
        array_start, array_end = raw.find("["), raw.rfind("]")
        if array_start >= 0 and array_end > array_start:
            candidates.append(raw[array_start : array_end + 1])
        parsed: Any = None
        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
                break
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        if isinstance(parsed, dict):
            parsed = parsed.get("items") or parsed.get("results") or parsed.get("images")
        if not isinstance(parsed, list):
            return []
        results: list[dict[str, Any]] = []
        used_indexes: set[int] = set()
        for row in parsed:
            if not isinstance(row, dict):
                continue
            index = _safe_int(row.get("image_index") or row.get("index"), 0, 0)
            if index < 1 or index > len(items) or index in used_indexes:
                continue
            used_indexes.add(index)
            item = items[index - 1]
            results.append(
                {
                    "id": item.get("id"),
                    "name": row.get("name"),
                    "description": row.get("description"),
                    "visible_text": row.get("visible_text") or row.get("text"),
                    "tags": row.get("tags"),
                    "emotions": row.get("emotions"),
                    "intents": row.get("intents") or row.get("purposes"),
                }
            )
        return results

    def _reaction_library_analysis_provider_candidates(self) -> list[tuple[str, str, str]]:
        """Prefer the plugin-owned vision card for reaction asset metadata."""
        candidates_getter = getattr(self.plugin, "_private_image_visual_provider_candidates", None)
        inherited = candidates_getter("") if callable(candidates_getter) else []
        inherited_rows = inherited if isinstance(inherited, list) else []
        configured_id = self._single_line(getattr(self.plugin, "plugin_vision_provider_id", ""), 160)
        fallback_getter = getattr(self.plugin, "_model_fallback_provider_id", None)
        configured_fallback_id = (
            self._single_line(
                fallback_getter("PLUGIN_VISION_PROVIDER_ID", configured_id),
                160,
            )
            if configured_id and callable(fallback_getter)
            else ""
        )

        ordered: list[tuple[str, str, str]] = []
        seen: set[str] = set()

        def append(provider_id: Any, source: Any, prompt: Any = "") -> None:
            clean_id = self._single_line(provider_id, 160)
            if not clean_id or clean_id in seen:
                return
            seen.add(clean_id)
            ordered.append(
                (
                    clean_id,
                    self._single_line(source, 80),
                    str(prompt or "").strip(),
                )
            )

        append(configured_id, "plugin_vision")
        append(configured_fallback_id, "plugin_vision_fallback")
        for row in inherited_rows:
            if not isinstance(row, (list, tuple)) or not row:
                continue
            append(
                row[0],
                row[1] if len(row) > 1 else "",
                row[2] if len(row) > 2 else "",
            )
        return ordered

    async def _call_reaction_library_analysis_provider(
        self,
        items: list[dict[str, Any]],
        image_urls: list[str],
    ) -> tuple[list[dict[str, Any]], str, str]:
        provider_getter = getattr(self.plugin, "_private_image_provider_by_id", None)
        supports_image = getattr(self.plugin, "_provider_supports_image", None)
        cooldown_check = getattr(self.plugin, "_private_image_provider_in_failure_cooldown", None)
        candidate_rows = self._reaction_library_analysis_provider_candidates()
        prompt = self._reaction_library_analysis_prompt(items)
        last_error = "未配置可用的视觉模型"
        seen: set[str] = set()
        primary_visual_id = next(
            (
                self._single_line(row[0], 160)
                for row in candidate_rows
                if isinstance(row, (list, tuple)) and len(row) >= 2 and row[1] == "plugin_vision"
            ),
            "",
        )
        fallback_visual_id = next(
            (
                self._single_line(row[0], 160)
                for row in candidate_rows
                if isinstance(row, (list, tuple)) and len(row) >= 2 and row[1] == "plugin_vision_fallback"
            ),
            "",
        )
        configured_visual_id = self._single_line(
            getattr(self.plugin, "plugin_vision_provider_id", ""),
            160,
        )
        prompt_applier = getattr(self.plugin, "_apply_task_prompt_override_for_call", None)
        if callable(prompt_applier):
            prompt, _unused_system_prompt = prompt_applier(
                "reaction_library_analysis",
                prompt,
                None,
                flatten_system_prompt=True,
            )
        visual_key_getter = getattr(self.plugin, "_private_image_visual_provider_card_key", None)
        visual_provider_key = (
            "PLUGIN_VISION_PROVIDER_ID"
            if configured_visual_id
            else visual_key_getter()
            if callable(visual_key_getter)
            else "PLUGIN_VISION_PROVIDER_ID"
        )
        for row in candidate_rows:
            if not isinstance(row, (list, tuple)) or not row:
                continue
            provider_id = self._single_line(row[0], 160)
            provider_source = self._single_line(row[1] if len(row) > 1 else "", 80)
            if not provider_id or provider_id in seen:
                continue
            seen.add(provider_id)
            if callable(cooldown_check) and cooldown_check(provider_id, provider_source):
                continue
            provider = provider_getter(provider_id) if callable(provider_getter) else None
            if provider is None or (callable(supports_image) and not supports_image(provider)):
                continue
            token_skip_getter = getattr(self.plugin, "_model_token_limit_should_skip_primary", None)
            if callable(token_skip_getter) and token_skip_getter(
                task="reaction_library_analysis",
                provider_id=provider_id,
                primary_provider_id=primary_visual_id,
                fallback_provider_id=fallback_visual_id,
                provider_key=visual_provider_key,
                prompt=prompt,
                max_tokens=1200,
                image_count=len(image_urls),
            ):
                recorder = getattr(self.plugin, "_record_llm_usage", None)
                if callable(recorder):
                    recorder(
                        provider_id=provider_id,
                        task="reaction_library_analysis",
                        prompt=prompt,
                        completion="",
                        elapsed_ms=0,
                        success=False,
                        error="model_token_limit_exceeded",
                        budget_exempt=False,
                    )
                last_error = "主视觉模型预估超出 Token 上限，已切换备用模型"
                continue
            budget_check = getattr(self.plugin, "_can_run_llm_task", None)
            if callable(budget_check) and not budget_check(provider_id, task="reaction_library_analysis"):
                last_error = "视觉模型调用预算已达到限制"
                continue
            started = time.time()
            result: Any = None
            completion = ""
            try:
                timeout_getter = getattr(self.plugin, "_private_image_provider_timeout_seconds", None)
                timeout = float(timeout_getter(provider_id, provider_source)) if callable(timeout_getter) else 30.0
                request_call = provider.text_chat(prompt=prompt, image_urls=image_urls)
                result = await asyncio.wait_for(request_call, timeout=timeout) if timeout > 0 else await request_call
                completion = str(getattr(result, "completion_text", result) or "").strip()
                parsed = self._parse_reaction_library_analysis(completion, items)
                if not parsed:
                    raise ValueError("视觉模型未返回可解析的素材 JSON")
                recorder = getattr(self.plugin, "_record_llm_usage", None)
                if callable(recorder):
                    recorder(
                        provider_id=provider_id,
                        task="reaction_library_analysis",
                        prompt=prompt,
                        completion=completion,
                        elapsed_ms=int((time.time() - started) * 1000),
                        success=True,
                        resp=result,
                        budget_exempt=False,
                    )
                success_notifier = getattr(self.plugin, "_note_private_image_visual_provider_success", None)
                if callable(success_notifier):
                    success_notifier(provider_id, provider_source, scope="reaction_library", chars=len(completion))
                return parsed, provider_id, ""
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                last_error = self._visual_call_error_text(exc, timeout=timeout)
                recorder = getattr(self.plugin, "_record_llm_usage", None)
                if callable(recorder):
                    recorder(
                        provider_id=provider_id,
                        task="reaction_library_analysis",
                        prompt=prompt,
                        completion=completion,
                        elapsed_ms=int((time.time() - started) * 1000),
                        success=False,
                        error=last_error,
                        resp=result,
                        budget_exempt=False,
                    )
                logger.info(
                    "表情包自动识别尝试下一个视觉模型: provider=%s images=%s exception=%s error=%s",
                    provider_id,
                    len(image_urls),
                    exc.__class__.__name__,
                    last_error,
                )
        return [], "", last_error

    async def _run_reaction_library_analysis_queue(self) -> None:
        library = self._reaction_library()
        async with self._reaction_library_analysis_lock():
            while True:
                items = await asyncio.to_thread(
                    library.analysis_candidates,
                    statuses=("pending", "running"),
                    limit=4,
                )
                if not items:
                    return
                ids = [str(item.get("id") or "") for item in items if item.get("id")]
                await asyncio.to_thread(library.mark_analysis_running, ids)
                images = await asyncio.gather(
                    *(asyncio.to_thread(library.get_analysis_image_data, item_id) for item_id in ids)
                )
                usable_items: list[dict[str, Any]] = []
                image_urls: list[str] = []
                unavailable: list[str] = []
                for item, image in zip(items, images):
                    if isinstance(image, dict) and image.get("data_url"):
                        usable_items.append(item)
                        image_urls.append(str(image["data_url"]))
                    else:
                        unavailable.append(str(item.get("id") or ""))
                if unavailable:
                    await asyncio.to_thread(library.mark_analysis_failed, unavailable, "图片文件不存在或无法读取")
                if not usable_items:
                    continue
                results, provider_id, error = await self._call_reaction_library_analysis_provider(
                    usable_items,
                    image_urls,
                )
                if not results:
                    await asyncio.to_thread(
                        library.mark_analysis_failed,
                        [str(item.get("id") or "") for item in usable_items],
                        error,
                    )
                    continue
                applied = await asyncio.to_thread(
                    library.apply_analysis_results,
                    results,
                    provider_id=provider_id,
                )
                completed_ids = set(applied.get("ids") or [])
                missing_ids = [
                    str(item.get("id") or "")
                    for item in usable_items
                    if str(item.get("id") or "") not in completed_ids
                ]
                if missing_ids:
                    await asyncio.to_thread(library.mark_analysis_failed, missing_ids, "视觉模型遗漏了这张图片")

    def _schedule_reaction_library_analysis(self) -> asyncio.Task[Any] | None:
        current = getattr(self.plugin, "_reaction_library_analysis_task", None)
        if isinstance(current, asyncio.Task) and not current.done():
            return current
        operation = self._run_reaction_library_analysis_queue()
        tracker = getattr(self.plugin, "_create_lifecycle_background_task", None)
        if callable(tracker):
            task = tracker(operation, label="reaction_library_analysis")
        else:
            task = asyncio.create_task(operation, name="private_companion_reaction_library_analysis")
        if isinstance(task, asyncio.Task):
            setattr(self.plugin, "_reaction_library_analysis_task", task)

            def restart_if_queue_refilled(finished: asyncio.Task[Any]) -> None:
                if getattr(self.plugin, "_reaction_library_analysis_task", None) is finished:
                    setattr(self.plugin, "_reaction_library_analysis_task", None)
                if finished.cancelled():
                    return
                try:
                    if finished.exception() is not None:
                        return
                    pending = _safe_int(self._reaction_library().summary().get("analysis_pending"), 0, 0)
                except Exception:
                    return
                if pending:
                    self._schedule_reaction_library_analysis()

            task.add_done_callback(restart_if_queue_refilled)
        return task

    async def list_reaction_library(self) -> dict[str, Any]:
        try:
            data = await asyncio.to_thread(
                self._reaction_library().list_items,
                query=self._single_line(request.args.get("q"), 160),
                status=self._single_line(request.args.get("status"), 20) or "all",
                scope=self._single_line(request.args.get("scope"), 20) or "all",
                analysis=self._single_line(request.args.get("analysis"), 20) or "all",
                page=_safe_int(request.args.get("page"), 1, 1),
                page_size=_safe_int(request.args.get("page_size"), 48, 1, 120),
            )
            if _safe_int(data.get("summary", {}).get("analysis_pending"), 0, 0) > 0:
                self._schedule_reaction_library_analysis()
            return self._ok(data)
        except Exception as exc:
            logger.error("读取表情包素材库失败: %s", exc, exc_info=True)
            return self._error(str(exc))


    async def import_reaction_library(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        files = payload.get("files") if isinstance(payload, dict) else None
        if not isinstance(files, list) or not files:
            return self._error("请选择图片或 ZIP 文件")
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        try:
            result = await asyncio.to_thread(
                self._reaction_library().import_base64_payloads,
                files,
                metadata=metadata,
            )
            result["message"] = (
                f"已导入 {result.get('imported', 0)} 张，跳过 {len(result.get('duplicates', []))} 张重复素材"
            )
            if _safe_int(result.get("analysis_queued"), 0, 0) > 0:
                self._schedule_reaction_library_analysis()
                result["message"] += f"；{result.get('analysis_queued', 0)} 张已进入自动识别"
            return self._ok(result)
        except Exception as exc:
            logger.error("导入表情包失败: %s", exc, exc_info=True)
            return self._error(str(exc))

    async def analyze_reaction_library(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        ids = payload.get("ids") if isinstance(payload.get("ids"), list) else []
        if not ids:
            return self._error("没有选择要识别的表情包")
        try:
            result = await asyncio.to_thread(
                self._reaction_library().queue_analysis,
                ids,
                include_complete=bool(payload.get("force", True)),
            )
            if result.get("queued"):
                self._schedule_reaction_library_analysis()
            result["message"] = f"已将 {result.get('queued', 0)} 张素材加入识别队列"
            return self._ok(result)
        except Exception as exc:
            logger.error("表情包自动识别排队失败: %s", exc, exc_info=True)
            return self._error(str(exc))

    async def update_reaction_library(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        ids = payload.get("ids") if isinstance(payload.get("ids"), list) else []
        changes = payload.get("changes") if isinstance(payload.get("changes"), dict) else {}
        if not ids:
            return self._error("没有选择表情包")
        if not changes:
            return self._error("没有可保存的修改")
        try:
            result = await asyncio.to_thread(self._reaction_library().update_items, ids, changes)
            return self._ok(result)
        except Exception as exc:
            logger.error("更新表情包失败: %s", exc, exc_info=True)
            return self._error(str(exc))

    async def delete_reaction_library(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        ids = payload.get("ids") if isinstance(payload.get("ids"), list) else []
        if not ids:
            return self._error("没有选择表情包")
        if payload.get("confirm") is not True:
            return self._error("删除需要 confirm=true")
        try:
            return self._ok(await asyncio.to_thread(self._reaction_library().delete_items, ids))
        except Exception as exc:
            logger.error("删除表情包失败: %s", exc, exc_info=True)
            return self._error(str(exc))

    async def rescan_reaction_library(self) -> dict[str, Any]:
        try:
            result = await asyncio.to_thread(self._reaction_library().rescan)
            if _safe_int(result.get("analysis_queued"), 0, 0) > 0:
                self._schedule_reaction_library_analysis()
            duplicate_count = len(result.get("duplicates", [])) if isinstance(result.get("duplicates"), list) else 0
            if duplicate_count:
                result["message"] = f"已重建索引；发现 {duplicate_count} 个重复文件，未重复导入"
            return self._ok(result)
        except Exception as exc:
            logger.error("重建表情包索引失败: %s", exc, exc_info=True)
            return self._error(str(exc))





















    async def preview_wardrobe_outfit(self) -> dict[str, Any]:
        """Preview what the wardrobe would inject for one occasion.

        Read-only: never writes config and never calls the model, so the panel
        can refresh it freely while the administrator tunes the settings.
        """

        payload = await request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            return self._error("请求体必须是 JSON 对象")
        preview = getattr(self.plugin, "_wardrobe_outfit_preview", None)
        if not callable(preview):
            return self._error("当前插件实例不支持着装预览")
        # 缺省的 scene/weather 表示「用插件自动判定的值」；传空串表示这一轮没有场合上下文。
        # 场合只写进请求与种子，从不过滤候选，所以这里怎么填都不会藏起某件衣物。
        raw_scene = payload.get("scene")
        raw_weather = payload.get("weather")
        try:
            data = preview(
                scene=None if raw_scene is None else self._single_line(raw_scene, 20),
                weather=None if raw_weather is None else self._single_line(raw_weather, 120),
                seed=self._single_line(payload.get("seed"), 60),
            )
        except Exception as exc:
            logger.warning("着装预览失败: %s", self._single_line(exc, 160), exc_info=True)
            return self._error("着装预览失败，请稍后再试")
        return self._ok(data)

    def _wardrobe_page_local_path(self, value: Any) -> Path | None:
        """Allow only images already stored in the plugin's own asset directories."""

        path = Path(str(value or "")).expanduser()
        try:
            if not path.is_file() or path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
                return None
            resolved = path.resolve()
            data_root = Path(str(getattr(self.plugin, "data_dir", "") or ".")).expanduser().resolve()
            allowed_roots = (
                data_root / "photo_reference_images",
                data_root / "photo_reference_assets",
            )
            if not any(resolved == root or root in resolved.parents for root in allowed_roots):
                return None
            return resolved
        except (OSError, ValueError):
            return None

    # 缩略图只服务衣柜素材目录里的位图；单张上限是为了不把 32MB 的原图
    # base64 成 43MB 再塞回浏览器 —— 队列里那一小格图不值得这个带宽。
    WARDROBE_ASSET_IMAGE_MIMES = frozenset({"image/png", "image/jpeg", "image/webp", "image/gif"})
    WARDROBE_ASSET_IMAGE_MAX_BYTES = 8 * 1024 * 1024



    async def get_wardrobe_intent(self) -> dict[str, Any]:
        """Read the session outfit intent for the wardrobe panel.

        Read-only: it only asks the plugin for the author's dialogue_outfit_override
        snapshot, so the panel can show what this session asked the character to wear.
        """

        reader = getattr(self.plugin, "_wardrobe_intent_snapshot", None)
        if not callable(reader):
            return self._error("当前插件实例不支持穿衣意图")
        try:
            snapshot = reader()
        except Exception as exc:
            logger.warning("穿衣意图读取失败: %s", self._single_line(exc, 160), exc_info=True)
            return self._error("读取穿衣意图失败，请稍后再试")
        return self._ok({"intent": snapshot if isinstance(snapshot, dict) else {}})

    async def clear_wardrobe_intent(self) -> dict[str, Any]:
        """Clear the session outfit intent so the daily rotation takes over again."""

        clearer = getattr(self.plugin, "_wardrobe_clear_intent", None)
        if not callable(clearer):
            return self._error("当前插件实例不支持穿衣意图")
        try:
            cleared = bool(clearer())
        except Exception as exc:
            logger.warning("穿衣意图清除失败: %s", self._single_line(exc, 160), exc_info=True)
            return self._error("清除穿衣意图失败，请稍后再试")
        return self._ok({"cleared": cleared})




























    # ------------------------------------------------------------------
    # Independent visual reference assets
    # ------------------------------------------------------------------
























    @staticmethod
    def _extract_labeled_text(text: str, label: str, limit: int) -> str:
        source = str(text or "")
        pattern = rf"{re.escape(label)}\s*[：:]\s*(.+?)(?=(?:\s+[^\s：:]{{2,20}}\s*[：:])|$)"
        match = re.search(pattern, source)
        if not match:
            return ""
        return PrivateCompanionPageApi._single_line(match.group(1), limit)



    async def get_reality_touch(self) -> dict[str, Any]:
        bridge_getter = getattr(self.plugin, "_reality_companion_api", None)
        bridge = bridge_getter() if callable(bridge_getter) else None
        linked_snapshotter = getattr(bridge, "page_snapshot", None) if bridge is not None else None
        if callable(linked_snapshotter):
            try:
                return self._ok(self._normalize_reality_touch_snapshot(linked_snapshotter()))
            except Exception as exc:
                logger.error("获取现实触及联动状态失败: %s", exc, exc_info=True)
                return self._exception_error("获取现实触及联动状态失败")
        return self._error(
            "现实触及已由“我会来到你身边”管理，请先安装并启用 astrbot_plugin_reality_companion。",
            status_code=503,
        )

    async def update_reality_touch(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        bridge_getter = getattr(self.plugin, "_reality_companion_api", None)
        bridge = bridge_getter() if callable(bridge_getter) else None
        linked_action = getattr(bridge, "page_action", None) if bridge is not None else None
        if callable(linked_action):
            try:
                result = await linked_action(payload)
                if not isinstance(result, dict) or not result.get("ok"):
                    return self._error(
                        self._single_line(result.get("message"), 240)
                        if isinstance(result, dict)
                        else "现实触及联动操作失败"
                    )
                snapshot = result.get("data") if isinstance(result.get("data"), dict) else {}
                if isinstance(result.get("result"), dict):
                    snapshot["action_result"] = result["result"]
                snapshot = self._normalize_reality_touch_snapshot(snapshot)
                snapshot["message"] = self._single_line(result.get("message"), 240) or "现实触及联动操作已完成"
                return self._ok(snapshot)
            except Exception as exc:
                logger.error("更新现实触及联动失败: %s", exc, exc_info=True)
                return self._exception_error("更新现实触及联动失败")
        return self._error(
            "现实触及已由“我会来到你身边”管理，请先安装并启用 astrbot_plugin_reality_companion。",
            status_code=503,
        )

    @staticmethod
    def _normalize_reality_touch_snapshot(snapshot: Any) -> dict[str, Any]:
        """Normalize snapshots from old and new Reality Companion bridges.

        Older embedded MiHome snapshots expose auth/login/device fields but do
        not include the newer ``available`` marker.  Keep an explicit false
        authoritative while deriving availability from the capability payload
        when the marker is absent.
        """
        normalized = dict(snapshot) if isinstance(snapshot, dict) else {}
        mihome = normalized.get("mihome")
        if not isinstance(mihome, dict):
            return normalized
        mihome = dict(mihome)
        if "available" not in mihome:
            mihome["available"] = any(
                key in mihome
                for key in ("auth", "login", "devices", "mappings", "tool_settings")
            )
        normalized["mihome"] = mihome
        return normalized



    def _req041_config_runtime_snapshot(self, changed: dict[str, Any]) -> dict[str, Any]:
        """Snapshot only identity/relationship isolation controls before hot apply."""
        critical = {
            "enable_auto_user_profile_creation",
            "portrait_global_mode",
            "auto_profile_platforms",
            "owner_group_relationship_projection",
            "owner_group_interaction_projection",
            "enable_group_relationship_affinity",
            "group_relationship_affinity_allowlist",
            "group_relationship_daily_net_cap",
            "group_relationship_window_minutes",
            "group_relationship_window_absolute_cap",
            "group_relationship_person_daily_absolute_cap",
            "group_relationship_scope_daily_absolute_cap",
            "relationship_event_window_minutes",
            "relationship_positive_event_cap",
            "relationship_negative_event_cap",
            "relationship_positive_daily_cap",
        }
        snapshot: dict[str, Any] = {}
        getter = getattr(self, "_config_get_raw", None)
        for key in sorted(critical & set(changed)):
            if hasattr(self.plugin, key):
                snapshot[key] = deepcopy(getattr(self.plugin, key))
            elif callable(getter):
                snapshot[key] = deepcopy(getter(key, None))
        return snapshot

    async def _rollback_req041_config_runtime(self, snapshot: dict[str, Any]) -> bool:
        """Restore runtime and config object, then durably save the old values."""
        for key, value in snapshot.items():
            self._apply_config_value(key, deepcopy(value))
        try:
            return bool(await self._save_config_if_possible())
        except Exception as exc:
            logger.error(
                "REQ-041 配置回滚持久化失败: %s",
                self._single_line(exc, 160),
            )
            return False





















    async def get_extension_control_plane_status(self) -> dict[str, Any]:
        """Expose extension metadata and invariant checks without provider data."""
        api = getattr(self.plugin, "extension_api", None)
        getter = getattr(api, "extension_control_plane_status", None)
        if not callable(getter):
            return self._ok(
                {
                    "protocol_version": "0.1",
                    "extensions": [],
                    "issues": ["control_plane_unavailable"],
                }
            )
        try:
            payload = getter()
            return self._ok(payload if isinstance(payload, dict) else {})
        except Exception as exc:
            logger.error("读取扩展控制面状态失败: %s", self._single_line(exc, 160), exc_info=True)
            return self._exception_error("读取扩展状态失败")



    async def get_daily_review(self) -> dict[str, Any]:
        try:
            payload_getter = getattr(self.plugin, "_daily_review_status_payload", None)
            if not callable(payload_getter):
                return self._error("当前插件版本未加载每日终盘巡视模块")
            async with self.plugin._data_lock:
                payload = payload_getter()
            return self._ok(payload)
        except Exception as exc:
            logger.error("获取每日巡视报告失败: %s", self._single_line(exc, 180), exc_info=True)
            return self._error(str(exc))

    async def run_daily_review(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        target_date = self._single_line(payload.get("date"), 16)
        if target_date and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", target_date):
            return self._error("巡视日期格式必须为 YYYY-MM-DD")
        runner = getattr(self.plugin, "_ensure_daily_review", None)
        if not callable(runner):
            return self._error("当前插件版本未加载每日终盘巡视模块")
        try:
            report = await runner(force=True, target_date=target_date)
            if not isinstance(report, dict):
                return self._error("巡视未生成有效报告")
            async with self.plugin._data_lock:
                status = self.plugin._daily_review_status_payload()
            return self._ok({"report": report, **status})
        except Exception as exc:
            logger.warning("手动执行每日巡视失败: %s", self._single_line(exc, 180))
            return self._error(str(exc))

    async def update_daily_review_guidance(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        active = bool(payload.get("active", False))
        try:
            async with self.plugin._data_lock:
                guidance = self.plugin.data.get("daily_review_active_guidance")
                if not isinstance(guidance, dict) or not isinstance(guidance.get("items"), list) or not guidance.get("items"):
                    return self._error("当前没有可启用的低风险巡视指导")
                if active and self._float(guidance.get("active_until")) <= time.time():
                    return self._error("这份巡视指导已经过期，请重新执行巡视")
                guidance["active"] = active
                guidance["manual_paused"] = not active
                self.plugin._save_data_sync(sections={"daily_review_active_guidance"})
                status = self.plugin._daily_review_status_payload()
            return self._ok(status)
        except Exception as exc:
            logger.error("更新每日巡视指导失败: %s", self._single_line(exc, 180), exc_info=True)
            return self._error(str(exc))



    def _classify_test_failure(self, test_type: str, result: dict[str, Any]) -> dict[str, Any]:
        if result.get("unsupported") or result.get("test_status") in {"unsupported", "skipped"}:
            return {
                "error_code": self._single_line(result.get("code"), 80) or "test_not_supported",
                "error_category": self._single_line(result.get("error_category"), 40) or "契约限制",
                "retryable": False,
                "suggestion": self._single_line(result.get("next_step"), 600)
                or "请使用对应插件提供的完整链路测试。",
            }
        if bool(result.get("ok")) or bool(result.get("pending")):
            return {
                "error_code": "",
                "error_category": "",
                "retryable": False,
                "suggestion": "",
            }
        text = " ".join(
            str(result.get(key) or "")
            for key in ("error", "delivery_error", "detail")
        ).lower()
        rules = [
            (
                "queue_timeout",
                "队列等待超时",
                True,
                ("排队超时", "释放队列", "queue timeout", "queue wait"),
                "当前任务队列繁忙；等待其他任务结束后重试，必要时检查是否有卡住的生成任务。",
            ),
            (
                "timeout",
                "请求超时",
                True,
                ("timeout", "timed out", "超时", "等待上限", "排队超时"),
                "检查服务和任务队列；若服务本身较慢，可适当提高该测试的超时后重试。",
            ),
            (
                "authentication",
                "鉴权失败",
                False,
                ("unauthorized", "forbidden", "invalid api key", "api key", "401", "403", "鉴权", "认证", "密钥"),
                "核对 API Key、请求地址和账号权限，保存配置后重新测试。",
            ),
            (
                "rate_limit",
                "额度或限流",
                True,
                ("rate limit", "too many requests", "429", "quota", "insufficient", "限流", "额度", "余额不足"),
                "检查额度和并发限制，等待限流窗口恢复后重试。",
            ),
            (
                "endpoint_mismatch",
                "端点不匹配",
                False,
                ("http 404", "http状态 404", "status=404", "status 404", "未找到生图接口", "端点不匹配"),
                "当前平台返回 HTTP 404，通常是生图端点协议不匹配；核对实际请求 URL、平台选择和参考图/文生图端点后再试。",
            ),
            (
                "network",
                "网络连接失败",
                True,
                ("connection", "connect", "network", "dns", "proxy", "ssl", "certificate", "网络", "连接", "代理", "证书"),
                "检查服务地址、DNS、代理和证书配置，再重试连接。",
            ),
            (
                "configuration",
                "配置不可用",
                False,
                ("未配置", "未启用", "不存在", "不可用", "不支持", "缺少", "尚未启用", "not found", "missing", "disabled"),
                "补齐或启用测试所需配置，确认目标能力已加载后重试。",
            ),
            (
                "delivery",
                "消息投递失败",
                True,
                ("投递", "发送", "delivery", "send_message", "平台未确认", "未能发送"),
                "检查主要用户私聊会话、平台适配器和消息发送链路后重试。",
            ),
            (
                "empty_result",
                "返回内容无效",
                True,
                ("未返回", "返回为空", "没有得到", "无有效", "empty", "invalid result", "不可投递"),
                "确认模型、工作流或 Provider 能返回该测试要求的有效内容。",
            ),
        ]
        for code, category, retryable, needles, suggestion in rules:
            if any(needle in text for needle in needles):
                return {
                    "error_code": code,
                    "error_category": category,
                    "retryable": retryable,
                    "suggestion": suggestion,
                }
        return {
            "error_code": "internal_error",
            "error_category": "执行异常",
            "retryable": True,
            "suggestion": "复制测试编号和诊断详情，并查看同一时间的 AstrBot 后端日志。",
        }


    @staticmethod
    def _normalize_external_api_test_setting(key: str, value: Any) -> Any:
        if key == "weather_source":
            source = str(value or "qweather").strip().lower()
            return source if source in {"qweather", "openweathermap", "openmeteo", "amap"} else "qweather"
        if key in {"weather_lat", "weather_lon"}:
            try:
                return float(value)
            except (TypeError, ValueError):
                return 0.0
        if key == "balance_value_divisor":
            try:
                return max(1e-12, min(1e12, float(value)))
            except (TypeError, ValueError):
                return 1.0
        if key == "balance_request_timeout_seconds":
            try:
                return max(2.0, min(60.0, float(value)))
            except (TypeError, ValueError):
                return 10.0
        if key == "web_exploration_max_results":
            try:
                return max(3, min(20, int(value)))
            except (TypeError, ValueError):
                return 6
        limits = {
            "weather_api_host": 800,
            "weather_token": 2000,
            "weather_location": 180,
            "weather_api_key": 1000,
            "weather_city": 180,
            "weather_amap_api_key": 1000,
            "weather_amap_city": 180,
            "balance_api_url": 1000,
            "balance_api_key": 1000,
            "balance_api_auth_header": 80,
            "balance_api_auth_scheme": 40,
            "balance_api_custom_headers": 4000,
            "balance_json_path": 180,
            "balance_total_json_path": 180,
            "balance_used_json_path": 180,
            "balance_currency_label": 20,
            "WEB_EXPLORATION_API_BASE_URL": 800,
            "WEB_EXPLORATION_API_KEY": 800,
            "WEB_EXPLORATION_API_MODEL": 160,
        }
        return str(value or "").strip()[: limits.get(key, 1000)]

    def _external_api_test_plugin_copy(
        self,
        test_type: str,
        raw_settings: Any,
    ) -> tuple[Any, dict[str, Any]]:
        settings = raw_settings if isinstance(raw_settings, dict) else {}
        tester = copy(self.plugin)
        live_data = getattr(self.plugin, "data", None)
        tester.data = dict(live_data) if isinstance(live_data, dict) else {}
        for state_key in ("daily_weather", "qweather_location", "web_search_runtime", "balance_awareness"):
            if state_key in tester.data:
                tester.data[state_key] = deepcopy(tester.data[state_key])
        tester._data_lock = asyncio.Lock()
        tester._qweather_location_resolve_lock = asyncio.Lock()
        tester._save_data_sync = lambda **_kwargs: None
        tester._external_api_test_mode = True

        normalized: dict[str, Any] = {}
        for key, attr in EXTERNAL_API_TEST_SETTING_ATTRS.get(test_type, {}).items():
            if key not in settings:
                continue
            value = self._normalize_external_api_test_setting(key, settings.get(key))
            setattr(tester, attr, value)
            normalized[key] = value

        # The current form value is authoritative during a test, including
        # when the user has just cleared a field that has a legacy alias.
        if test_type == "weather_api" and "weather_api_host" in normalized:
            tester.qweather_api_host = ""
            tester.weather_alert_api_host = ""
        if test_type == "weather_api" and "weather_token" in normalized:
            tester.qweather_token = ""
            tester.weather_alert_token = ""
            tester.weather_alert_jwt = ""
            tester.weather_alert_api_key = ""
        return tester, normalized

    @staticmethod
    def _safe_external_api_test_url(value: Any) -> str:
        raw = str(value or "").strip()
        if not raw:
            return ""
        try:
            parsed = urlparse(raw)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                return "<外部接口>"
            host = parsed.hostname
            if ":" in host and not host.startswith("["):
                host = f"[{host}]"
            port = f":{parsed.port}" if parsed.port else ""
            return f"{parsed.scheme}://{host}{port}{parsed.path or ''}"
        except ValueError:
            return "<外部接口>"

    def _redact_external_api_test_text(
        self,
        value: Any,
        *,
        tester: Any,
        settings: dict[str, Any],
        limit: int = 1200,
    ) -> str:
        cleaned = _redact_outbound_secrets(str(value or ""), tester)
        secrets_to_hide: list[str] = []
        for key in EXTERNAL_API_TEST_SECRET_FIELDS:
            raw = str(settings.get(key) or "").strip()
            if not raw:
                continue
            if key == "balance_api_custom_headers":
                for line in raw.splitlines():
                    _, separator, header_value = line.partition(":")
                    if separator and len(header_value.strip()) >= 4:
                        secrets_to_hide.append(header_value.strip())
                continue
            if len(raw) >= 4:
                secrets_to_hide.append(raw)
        for secret in sorted(set(secrets_to_hide), key=len, reverse=True):
            cleaned = cleaned.replace(secret, "[密钥已隐藏]")
        cleaned = re.sub(
            r"https?://[^\s'\"<>]+",
            lambda match: self._safe_external_api_test_url(match.group(0)),
            cleaned,
            flags=re.IGNORECASE,
        )
        return self._multi_line(cleaned, max(80, limit))

    @staticmethod
    def _external_weather_location_label(tester: Any, result: Any = None) -> str:
        if isinstance(result, dict) and str(result.get("location_label") or "").strip():
            return str(result.get("location_label") or "").strip()[:120]
        source = str(getattr(tester, "weather_source", "qweather") or "qweather").strip().lower()
        attr = {
            "qweather": "weather_location",
            "amap": "weather_amap_city",
            "openweathermap": "weather_city",
        }.get(source, "")
        label = str(getattr(tester, attr, "") or "").strip() if attr else ""
        if label:
            return label[:120]
        try:
            latitude = float(getattr(tester, "weather_lat", 0))
            longitude = float(getattr(tester, "weather_lon", 0))
        except (TypeError, ValueError):
            return ""
        if math.isfinite(latitude) and math.isfinite(longitude) and (latitude != 0 or longitude != 0):
            return f"{longitude:g},{latitude:g}"
        return ""

    def _external_weather_configuration_error(self, tester: Any) -> str:
        source = str(getattr(tester, "weather_source", "qweather") or "qweather").strip().lower()
        location = self._external_weather_location_label(tester)
        if source == "qweather":
            missing = []
            if not str(getattr(tester, "weather_api_host", "") or "").strip():
                missing.append("专属 API Host")
            if not str(getattr(tester, "weather_token", "") or "").strip():
                missing.append("天气凭据")
            if not location:
                missing.append("天气地点")
            return f"和风天气缺少{'、'.join(missing)}" if missing else ""
        if source == "amap":
            missing = []
            if not str(getattr(tester, "weather_amap_api_key", "") or "").strip():
                missing.append("高德 API Key")
            if not str(getattr(tester, "weather_amap_city", "") or "").strip():
                missing.append("高德城市")
            return f"高德天气缺少{'、'.join(missing)}" if missing else ""
        if source == "openmeteo":
            return "" if location else "Open-Meteo 缺少有效经纬度"
        missing = []
        if not str(getattr(tester, "weather_api_key", "") or "").strip():
            missing.append("OpenWeatherMap API Key")
        if not location:
            missing.append("城市或经纬度")
        return f"OpenWeatherMap 缺少{'、'.join(missing)}" if missing else ""

    async def _run_weather_api_test(self, tester: Any, settings: dict[str, Any]) -> dict[str, Any]:
        title = "天气 API 请求测试"
        provider = self._single_line(getattr(tester, "weather_source", "qweather"), 40).lower() or "qweather"
        configuration_error = self._external_weather_configuration_error(tester)
        if configuration_error:
            return {
                "ok": False,
                "title": title,
                "provider": provider,
                "location_label": self._external_weather_location_label(tester),
                "error": configuration_error,
                "steps": [{"name": "检查天气配置", "status": "error", "detail": configuration_error}],
            }
        fetcher = getattr(tester, "_fetch_own_weather_prompt", None)
        if not callable(fetcher):
            return {"ok": False, "title": title, "provider": provider, "error": "当前插件未加载天气请求入口"}
        try:
            raw = await asyncio.wait_for(fetcher(), timeout=25.0)
        except asyncio.TimeoutError:
            error = "天气接口请求超时（25 秒）"
            return {
                "ok": False,
                "title": title,
                "provider": provider,
                "location_label": self._external_weather_location_label(tester),
                "error": error,
                "steps": [{"name": "请求天气接口", "status": "error", "detail": error}],
            }
        except Exception as exc:
            error = self._redact_external_api_test_text(exc, tester=tester, settings=settings, limit=800)
            return {
                "ok": False,
                "title": title,
                "provider": provider,
                "location_label": self._external_weather_location_label(tester),
                "error": error or "天气接口请求失败",
                "exception_type": exc.__class__.__name__,
                "steps": [{"name": "请求天气接口", "status": "error", "detail": error or "请求失败"}],
            }
        prompt = self._single_line(raw.get("prompt"), 320) if isinstance(raw, dict) else ""
        source = self._single_line(raw.get("source"), 80) if isinstance(raw, dict) else ""
        location_label = self._external_weather_location_label(tester, raw)
        if not isinstance(raw, dict) or not prompt or not source:
            error = "天气接口未返回有效天气结果；请核对地点、凭据、接口权限和网络状态"
            return {
                "ok": False,
                "title": title,
                "provider": provider,
                "source": source,
                "location_label": location_label,
                "error": error,
                "steps": [{"name": "校验天气结果", "status": "error", "detail": error}],
            }
        return {
            "ok": True,
            "title": title,
            "provider": provider,
            "source": source,
            "location_label": location_label,
            "detail": prompt,
            "steps": [{"name": "请求天气接口", "status": "ok", "detail": prompt}],
        }

    async def _run_balance_api_test(self, tester: Any, settings: dict[str, Any]) -> dict[str, Any]:
        title = "余额接口请求测试"
        fetcher = getattr(tester, "_fetch_balance_snapshot", None)
        if not callable(fetcher):
            return {"ok": False, "title": title, "error": "当前插件未加载余额查询入口"}
        try:
            snapshot = await asyncio.wait_for(fetcher(), timeout=45.0)
        except asyncio.TimeoutError:
            error = "余额接口请求超时（45 秒）"
            return {
                "ok": False,
                "title": title,
                "error": error,
                "steps": [{"name": "请求余额接口", "status": "error", "detail": error}],
            }
        except Exception as exc:
            safe_error = ""
            sanitizer = getattr(tester, "_balance_safe_error", None)
            if callable(sanitizer):
                try:
                    safe_error = sanitizer(exc)
                except Exception:
                    safe_error = ""
            error = self._redact_external_api_test_text(
                safe_error or exc,
                tester=tester,
                settings=settings,
                limit=800,
            )
            return {
                "ok": False,
                "title": title,
                "error": error or "余额接口请求失败",
                "exception_type": exc.__class__.__name__,
                "steps": [{"name": "请求余额接口", "status": "error", "detail": error or "请求失败"}],
            }
        if not isinstance(snapshot, dict) or snapshot.get("amount") is None:
            error = "余额接口未返回可用余额字段"
            return {
                "ok": False,
                "title": title,
                "error": error,
                "steps": [{"name": "校验余额结果", "status": "error", "detail": error}],
            }
        currency = self._single_line(getattr(tester, "balance_currency_label", "元"), 20) or "元"
        amount = snapshot.get("amount")
        detail = f"余额接口返回 {amount:g} {currency}" if isinstance(amount, (int, float)) else "余额接口返回有效结果"
        return {
            "ok": True,
            "title": title,
            "query_mode": self._single_line(snapshot.get("query_mode"), 20).lower(),
            "source_id": self._single_line(snapshot.get("source_id"), 100),
            "endpoint_path": self._single_line(snapshot.get("endpoint_path"), 180),
            "amount": snapshot.get("amount"),
            "total": snapshot.get("total"),
            "used": snapshot.get("used"),
            "remaining_percent": snapshot.get("remaining_percent"),
            "currency_label": currency,
            "detail": detail,
            "steps": [{"name": "请求余额接口", "status": "ok", "detail": detail}],
        }

    async def _run_web_search_test(
        self,
        tester: Any,
        settings: dict[str, Any],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        title = "搜索接口请求测试"
        query = self._single_line(payload.get("query"), 120)
        if not query:
            error = "请输入用于测试的搜索词"
            return {"ok": False, "title": title, "error": error, "steps": [{"name": "检查搜索词", "status": "error", "detail": error}]}
        topic = self._single_line(payload.get("topic"), 20).lower()
        topic = topic if topic in {"general", "news"} else "general"
        umo = self._single_line(payload.get("umo"), 180)
        if not umo:
            picker = getattr(tester, "_pick_available_web_search_umo", None)
            if callable(picker):
                try:
                    umo = self._single_line(picker(""), 180)
                except Exception:
                    umo = ""
        searcher = getattr(tester, "_run_astrbot_web_search", None)
        if not callable(searcher):
            return {"ok": False, "title": title, "error": "当前插件未加载统一网页搜索入口"}
        try:
            results = await asyncio.wait_for(
                searcher(query, umo=umo, topic=topic, usage="web_exploration"),
                timeout=25.0,
            )
        except asyncio.TimeoutError:
            error = "搜索接口请求超时（25 秒）"
            return {"ok": False, "title": title, "error": error, "steps": [{"name": "请求搜索接口", "status": "error", "detail": error}]}
        except Exception as exc:
            error = self._redact_external_api_test_text(exc, tester=tester, settings=settings, limit=800)
            return {
                "ok": False,
                "title": title,
                "error": error or "搜索接口请求失败",
                "exception_type": exc.__class__.__name__,
                "steps": [{"name": "请求搜索接口", "status": "error", "detail": error or "请求失败"}],
            }

        custom_configured = False
        custom_checker = getattr(tester, "_custom_web_exploration_search_configured", None)
        if callable(custom_checker):
            try:
                custom_configured = bool(custom_checker())
            except Exception:
                custom_configured = False
        preview: list[dict[str, str]] = []
        providers: list[str] = []
        for item in results[:3] if isinstance(results, list) else []:
            if not isinstance(item, dict):
                continue
            provider = self._single_line(item.get("provider"), 100)
            if provider and provider not in providers:
                providers.append(provider)
            title_text = self._redact_external_api_test_text(
                self._single_line(item.get("title"), 140), tester=tester, settings=settings, limit=140
            )
            snippet = self._redact_external_api_test_text(
                self._single_line(item.get("snippet"), 240), tester=tester, settings=settings, limit=240
            )
            if title_text or snippet:
                preview.append({"title": title_text, "snippet": snippet})
        provider = providers[0] if providers else ("custom_web_exploration" if custom_configured else "astrbot")
        if not isinstance(results, list) or not results:
            raw_error = self._single_line(getattr(tester, "_last_web_search_error", ""), 500)
            if not raw_error:
                available = getattr(tester, "_astrbot_any_web_search_available", None)
                astrbot_available = False
                if callable(available):
                    try:
                        astrbot_available = bool(available())
                    except Exception:
                        astrbot_available = False
                raw_error = (
                    "搜索接口未返回可用结果"
                    if custom_configured or astrbot_available
                    else "未配置可用的自定义搜索接口或 AstrBot 网页搜索 Provider"
                )
            error = self._redact_external_api_test_text(raw_error, tester=tester, settings=settings, limit=800)
            return {
                "ok": False,
                "title": title,
                "provider": provider,
                "result_count": 0,
                "result_preview": [],
                "error": error,
                "steps": [{"name": "校验搜索结果", "status": "error", "detail": error}],
            }
        count = len(results)
        detail = f"{provider} 返回 {count} 条可用搜索结果"
        return {
            "ok": True,
            "title": title,
            "provider": provider,
            "result_count": count,
            "result_preview": preview,
            "detail": detail,
            "steps": [{"name": "请求统一搜索入口", "status": "ok", "detail": detail}],
        }

    async def _run_external_api_test(self, test_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        tester, settings = self._external_api_test_plugin_copy(test_type, payload.get("settings"))
        if test_type == "weather_api":
            result = await self._run_weather_api_test(tester, settings)
        elif test_type == "balance_api":
            result = await self._run_balance_api_test(tester, settings)
        else:
            result = await self._run_web_search_test(tester, settings, payload)
        steps = result.get("steps") if isinstance(result.get("steps"), list) else []
        result["steps"] = [
            {
                "name": "应用临时配置",
                "status": "ok",
                "detail": f"已在隔离副本中应用 {len(settings)} 个当前表单字段",
            },
            *steps,
        ]
        return result













    @staticmethod
    def _read_debug_lines(path: Path, *, tail_lines: int | None = None) -> list[str]:
        """Read a bounded tail for collapsed status requests."""
        if tail_lines is None:
            return path.read_text(encoding="utf-8", errors="replace").splitlines()
        limit = max(1, min(4096, int(tail_lines)))
        # 从文件尾往前读：先读一个小窗口，只有窗口里没有换行（说明落进了超长行）
        # 才按指数放大，避免在正常日志上把整个文件读进内存。
        chunk_size = max(64 * 1024, limit * 2048)
        max_window = min(_DEBUG_TAIL_MAX_WINDOW_BYTES, 16 * 1024 * 1024)
        content = b""
        with path.open("rb") as handle:
            handle.seek(0, 2)
            end = handle.tell()
            window_start = end
            found_newline = False
            while window_start > 0:
                window_start = max(0, window_start - chunk_size)
                handle.seek(window_start)
                window = handle.read(end - window_start)
                if b"\n" in window and (window_start == 0 or window.count(b"\n") > limit):
                    content = window
                    found_newline = True
                    break
                if window_start == 0:
                    # 整个文件都在同一段缓冲里（含只有一条超长记录的极端情况）。
                    content = window
                    found_newline = b"\n" in window
                    break
                if end - window_start >= max_window:
                    # 命中硬上限：保留窗口内最新的一段，绝不把 tail 读成空白。
                    content = window
                    found_newline = b"\n" in window
                    break
                chunk_size = min(max_window, chunk_size * 2)
        if window_start > 0 and found_newline:
            # 窗口起点落在某条记录内部：丢掉开头那条不完整记录。
            content = content[content.find(b"\n") + 1:]
        return content.decode("utf-8", "replace").splitlines()[-limit:]

    @staticmethod
    def _attach_debug_payload_contents(
        events: list[dict[str, Any]],
        roots: list[Path],
        *,
        max_total_bytes: int = 2 * 1024 * 1024,
        max_payload_bytes: int = 512 * 1024,
    ) -> None:
        """Attach captured sidecar bodies for the explicitly expanded view.

        Payload paths are recorder-generated relative paths. Resolve them only
        below each known ``photo_debug`` directory and reject symlinks so a
        forged log line cannot turn the page API into an arbitrary file reader.
        """
        remaining = max(0, int(max_total_bytes))
        if remaining <= 0:
            return
        for event in events:
            if remaining <= 0 or not isinstance(event, dict):
                break
            data = event.get("data")
            payloads = data.get("payloads") if isinstance(data, dict) else None
            if not isinstance(payloads, dict):
                continue
            for metadata in payloads.values():
                if remaining <= 0 or not isinstance(metadata, dict):
                    continue
                relative = str(metadata.get("path") or "").strip()
                if not relative or not bool(metadata.get("captured")):
                    continue
                for root in roots:
                    debug_root = root / "photo_debug"
                    candidate = debug_root / relative
                    try:
                        if candidate.is_symlink():
                            continue
                        resolved_root = debug_root.resolve(strict=False)
                        resolved = candidate.resolve(strict=True)
                        if resolved == resolved_root or resolved_root not in resolved.parents:
                            continue
                        if not resolved.is_file():
                            continue
                        size = resolved.stat().st_size
                        if size > min(max_payload_bytes, remaining):
                            metadata["content_truncated"] = True
                            metadata["content_limit"] = min(max_payload_bytes, remaining)
                            break
                        raw = resolved.read_bytes()
                    except (OSError, RuntimeError, ValueError):
                        continue
                    remaining -= len(raw)
                    encoding = str(metadata.get("encoding") or "utf-8").lower()
                    if encoding in {"base64", "binary"} or str(metadata.get("mime_type") or "").startswith("image/"):
                        metadata["content_base64"] = base64.b64encode(raw).decode("ascii")
                    else:
                        metadata["content"] = raw.decode("utf-8", "replace")
                    break















    async def _run_screen_peek_chain_test(self, payload: dict[str, Any]) -> dict[str, Any]:
        getter = getattr(self.plugin, "_get_screen_companion_plugin", None)
        screen_plugin = getter() if callable(getter) else None
        if screen_plugin is None or not callable(getattr(screen_plugin, "_invoke_screen_skill", None)):
            return {
                "ok": False,
                "title": "窥屏链路测试",
                "error": "未检测到可用的 screen_companion 插件或识屏入口",
            }
        async with self.plugin._data_lock:
            users = deepcopy(self.plugin.data.get("users") if isinstance(self.plugin.data.get("users"), dict) else {})
        umo = self._single_line(payload.get("umo"), 180) or self._preferred_tts_test_umo(users)
        event = None
        if umo and callable(getattr(screen_plugin, "_create_virtual_event", None)):
            try:
                event = screen_plugin._create_virtual_event(umo)
            except Exception as exc:
                logger.info(
                    "窥屏排障虚拟事件创建失败,将无事件调用: umo=%s error=%s",
                    self._single_line(umo, 120),
                    self._single_line(exc, 120),
                )
        prompt = self._single_line(payload.get("prompt"), 500) or (
            "这是一次插件排障中心发起的授权识屏链路测试。请只判断当前屏幕观察能力是否可用，"
            "用一句很短的内部摘要描述大概画面类型；不要输出账号、完整聊天内容、隐私细节或长文本。"
        )
        started = time.time()
        try:
            raw_result = await asyncio.wait_for(
                screen_plugin._invoke_screen_skill(
                    event,
                    request_prompt=prompt,
                    history_user_text="Private Companion 排障中心正在测试 screen_companion 识屏链路。",
                    task_id="private_companion_troubleshooting_screen_peek",
                ),
                timeout=max(15, self._int(payload.get("timeout_seconds"), 60, 5, 180)),
            )
        except Exception as exc:
            elapsed_ms = int((time.time() - started) * 1000)
            error = self._single_line(exc, 220)
            logger.warning("窥屏排障测试失败: %s", error, exc_info=True)
            return {
                "ok": False,
                "title": "窥屏链路测试",
                "umo": umo,
                "elapsed_ms": elapsed_ms,
                "error": error or repr(exc),
            }
        elapsed_ms = int((time.time() - started) * 1000)
        context = "screen_peek：\n" + (self._single_line(raw_result, 500) if raw_result else "没有得到屏幕观察结果")
        unusable_checker = getattr(self.plugin, "_is_unusable_screen_peek_context", None)
        unusable = bool(unusable_checker(context)) if callable(unusable_checker) else not bool(raw_result)
        preview = self._single_line(raw_result, 220)
        logger.info(
            "窥屏排障测试结束: ok=%s elapsed=%sms umo=%s preview=%s",
            not unusable,
            elapsed_ms,
            self._single_line(umo, 120),
            preview,
        )
        return {
            "ok": not unusable,
            "title": "窥屏链路测试",
            "umo": umo,
            "provider": "screen_companion",
            "detail": "已成功获得屏幕观察摘要" if not unusable else "识屏返回为空或不可用结果",
            "text_preview": preview,
            "context_chars": len(str(raw_result or "")),
            "elapsed_ms": elapsed_ms,
            "error": "" if not unusable else (preview or "没有得到屏幕观察结果"),
        }

    async def _run_qzone_chain_test(self, payload: dict[str, Any]) -> dict[str, Any]:
        steps: list[dict[str, str]] = []

        def add_step(name: str, status: str, detail: str) -> None:
            steps.append(
                {
                    "name": self._single_line(name, 40),
                    "status": self._single_line(status, 16),
                    "detail": self._single_line(detail, 180),
                }
            )

        started = time.time()
        service_available = bool(
            callable(getattr(self.plugin, "_test_qzone_integration", None))
            and callable(getattr(self.plugin, "_qzone_get_cookies", None))
        )
        platform_checker = getattr(self.plugin, "_qzone_platform_supported", None)
        platform_supported = bool(platform_checker(None)) if callable(platform_checker) else True
        enabled = bool(getattr(self.plugin, "enable_qzone_integration", False) and platform_supported)
        comment_enabled = bool(getattr(self.plugin, "enable_qzone_comment_inbox", False))
        add_step("内置服务", "ok" if service_available else "error", "可用" if service_available else "QQ 空间模块入口不可用")
        add_step(
            "平台能力",
            "ok" if platform_supported else "error",
            "OneBot/aiocqhttp 可用" if platform_supported else "QQ 官方机器人不支持 QQ 空间",
        )
        add_step("整合开关", "ok" if enabled else "warn", "已开启" if enabled else "已关闭")
        if not service_available or not enabled:
            unavailable_detail = (
                "QQ 官方机器人不支持 QQ 空间；不会执行读取、发布、点赞、评论或后台轮询。"
                if not platform_supported
                else "QQ 空间整合未启用或模块入口不可用"
            )
            return {
                "ok": False,
                "title": "QQ 空间链路测试",
                "provider": "qzone",
                "detail": unavailable_detail,
                "text_preview": unavailable_detail if not platform_supported else "开启 QQ 空间整合后再测试 Cookie、读取和发布工具链路。",
                "steps": steps,
                "elapsed_ms": int((time.time() - started) * 1000),
                "error": unavailable_detail,
            }

        target_id = self._single_line(payload.get("target_id"), 40)
        read_text = ""
        read_ok = False
        read_detail = ""
        reader = getattr(self.plugin, "_test_qzone_integration", None)
        if callable(reader):
            try:
                read_text = await asyncio.wait_for(
                    reader(None, target_id=target_id),
                    timeout=max(15, self._int(payload.get("timeout_seconds"), 45, 10, 180)),
                )
                read_ok = ("读取链路正常" in read_text) or ("读取链路可调用" in read_text)
                read_detail = (
                    (self._qzone_test_line_with_prefix(read_text, "查询结果：失败") if not read_ok else "")
                    or self._qzone_test_last_result_line(read_text)
                    or self._single_line(read_text, 180)
                )
                add_step("Cookie/读取", "ok" if read_ok else "error", read_detail or "读取测试未返回明确结果")
            except Exception as exc:
                read_detail = self._single_line(exc, 180)
                add_step("Cookie/读取", "error", read_detail or "读取测试异常")
        else:
            add_step("Cookie/读取", "error", "缺少 _test_qzone_integration 测试入口")

        publish_ok = False
        publish_detail = ""
        publisher = getattr(self.plugin, "_pc_qzone_publish_feed_impl", None)
        if callable(publisher):
            try:
                raw = await asyncio.wait_for(publisher(None, ""), timeout=15)
                parsed = json.loads(raw) if isinstance(raw, str) else {}
                status = self._single_line(parsed.get("status"), 40) if isinstance(parsed, dict) else ""
                message = self._single_line(parsed.get("message"), 160) if isinstance(parsed, dict) else self._single_line(raw, 160)
                publish_ok = status == "need_text"
                publish_detail = "空参数返回 need_text，发布工具入口正常" if publish_ok else (message or f"返回 {status or '未知状态'}")
                add_step("发布模拟", "ok" if publish_ok else "warn", publish_detail)
            except Exception as exc:
                publish_detail = self._single_line(exc, 180)
                add_step("发布模拟", "error", publish_detail or "发布工具空参数测试异常")
        else:
            add_step("发布模拟", "warn", "缺少发布工具入口，无法测试空参数模拟")

        async with self.plugin._data_lock:
            qzone_state = deepcopy(
                self.plugin.data.get("qzone_integration")
                if isinstance(self.plugin.data.get("qzone_integration"), dict)
                else {}
            )
        list_len = lambda key: len(qzone_state.get(key)) if isinstance(qzone_state.get(key), list) else 0
        seen_count = list_len("comment_inbox_seen_ids") + list_len("comment_inbox_seen_keys")
        replied_count = list_len("comment_inbox_replied_ids") + list_len("comment_inbox_replied_keys")
        inbox_status = self._single_line(qzone_state.get("last_comment_inbox_status"), 120)
        inbox_detail = (
            f"{'已开启' if comment_enabled else '未开启'}；已见 {seen_count}，已回复 {replied_count}"
            + (f"；最近 {inbox_status}" if inbox_status else "")
        )
        add_step("评论收件箱", "ok" if comment_enabled else "info", inbox_detail)

        ok = bool(read_ok and publish_ok)
        preview_parts = [
            read_detail,
            publish_detail,
            inbox_detail,
        ]
        if read_text:
            preview_parts.append(self._single_line(read_text, 500))
        return {
            "ok": ok,
            "title": "QQ 空间链路测试",
            "provider": "qzone",
            "detail": "QQ 空间读取和发布模拟正常" if ok else "QQ 空间链路存在需要处理的项",
            "text_preview": self._single_line("；".join(part for part in preview_parts if part), 500),
            "steps": steps,
            "elapsed_ms": int((time.time() - started) * 1000),
            "error": "" if ok else (read_detail or publish_detail or "QQ 空间链路测试未通过"),
        }

    @staticmethod
    def _qzone_test_last_result_line(text: str) -> str:
        for line in reversed(str(text or "").replace("\r", "\n").split("\n")):
            clean = line.strip().lstrip("-").strip()
            if clean.startswith("结果："):
                return clean
        return ""

    @staticmethod
    def _qzone_test_line_with_prefix(text: str, prefix: str) -> str:
        for line in str(text or "").replace("\r", "\n").split("\n"):
            clean = line.strip().lstrip("-").strip()
            if clean.startswith(prefix):
                return clean
        return ""


    @staticmethod
    def _is_private_test_umo(umo: Any) -> bool:
        text = str(umo or "").strip()
        return bool(
            text
            and not re.search(r"(?:^|:)GroupMessage(?=:|$)", text, flags=re.IGNORECASE)
            and re.search(r"(?:^|:)FriendMessage(?=:|$)", text, flags=re.IGNORECASE)
        )

















    @staticmethod
    def _token_task_label(task: Any) -> str:
        normalized = str(task or "").strip()
        if not normalized:
            return "未分类模型调用"
        labels = {
            "daily_plan": "日程生成",
            "detail": "日程细化",
            "dream": "梦境内容",
            "diary": "日记整理",
            "diary_rewrite": "日记修订",
            "diary_derivatives": "日记线索提取",
            "memory_profile": "本地陪伴画像",
            "dialogue_episode": "私聊片段",
            "response_review": "回复/主动复核",
            "emotion_judgement": "情绪判断",
            "relationship": "关系分析",
            "group_interject": "群聊插话",
            "group_episode": "群聊片段",
            "group_slang": "黑话释义",
            "group_question_wakeup_reply_review": "群聊答疑复核",
            "group_followup_judge": "群聊续接判断",
            "worldbook_registration": "关系网自登记",
            "web_exploration_query": "探索选题",
            "web_exploration_digest": "探索笔记",
            "external_event_self_link": "外界信息关联",
            "news_digest": "新闻整理",
            "creative_project": "创作立项",
            "creative_outline": "创作大纲",
            "creative_writing": "文本创作",
            "creative_review": "创作审校",
            "creative_extract": "创作抽取",
            "photo_prompt": "生图提示",
            "screen_narration": "识屏转述",
            "forward_message": "合并转发转述",
            "forward_message_image_vision": "转发图片识别",
            "private_image_vision": "私聊图片识别",
            "group_image_vision": "群聊图片识别",
            "private_image_only_framework": "单图回复主链",
            "private_image_only_fallback": "单图兜底回复",
            "voice": "语音文本",
            "proactive_framework": "主动主回复",
            "proactive_persona_judge": "主动人格判定",
            "voice_framework": "框架语音",
            "voice_repair": "语音格式修复",
            "tts_conversion": "TTS 快速转换",
            "tts_spoken_conversion": "TTS 口语转换",
            "tts_postprocess": "TTS 后处理",
            "tts_visible_translation": "TTS 可见译文",
            "smart_message_debounce": "智能收口防抖",
            "smart_silence": "智能沉默判断",
            "group_air_reply_guard": "群聊插话把关",
            "group_nsfw_image_review": "群图安全审核",
            "rest_wakeup_judge": "休息醒来判断",
            "yesterday_summary": "昨日摘要",
            "full_test_detail": "完整测试细化",
            "provider_test": "模型测试",
            "qzone_comment": "空间评论",
            "qzone_comment_inbox_decision": "空间评论判断",
            "qzone_publish": "空间说说",
            "qzone_publish_test": "空间发布测试",
            "qzone_publish_sanitize": "空间文案清理",
            "qzone_publish_image_test_draft": "空间配图测试草稿",
            "qzone_emotional_vent": "空间情绪表达",
            "companion_manual_diagnosis": "陪伴答疑",
            "proactive_send_review": "主动发送复核",
            "atrelay_rewrite": "代答转写",
            "bookshelf_password": "资料柜密码生成",
            "bookshelf_password_reason": "资料柜密码缘由",
            "astrbot_private_reply": "非插件私聊主回复",
            "astrbot_group_reply": "非插件群聊主回复",
            "astrbot_reply": "非插件主回复",
            "other": "未分类模型调用",
        }
        if normalized in labels:
            return labels[normalized]
        if normalized.startswith("qzone_") and normalized.endswith("_photo_prompt"):
            return "空间配图提示"
        if normalized.startswith("qzone_"):
            return "QQ 空间任务"
        if normalized.startswith("astrbot_"):
            return "AstrBot 主回复"
        if normalized.startswith("private_image_"):
            return "私聊图片处理"
        if normalized.startswith("web_exploration_"):
            return "主动搜索"
        return normalized



    def _passive_no_reply_item_is_obsolete_fixed_error(self, item: dict[str, Any]) -> bool:
        checker = getattr(self.plugin, "_proactive_audit_note_is_obsolete_fixed_error", None)
        texts = [
            item.get("reason"),
            item.get("last_detail"),
            item.get("last_action"),
            item.get("last_reply_preview"),
        ]
        samples = item.get("samples") if isinstance(item.get("samples"), list) else []
        for sample in samples[:5]:
            if not isinstance(sample, dict):
                continue
            texts.extend([sample.get("detail"), sample.get("reply_preview")])
        joined = "\n".join(str(value or "") for value in texts)
        if callable(checker) and checker(joined):
            return True
        return "NameError" in joined and any(
            token in joined
            for token in (
                "name 'topic' is not defined",
                "name 'name' is not defined",
            )
        )

    def _active_token_failures(
        self,
        recent: Any,
        *,
        max_age_seconds: float = 30 * 60,
        limit: int = 80,
    ) -> list[dict[str, Any]]:
        if not isinstance(recent, list):
            return []
        now = time.time()
        recovered: set[tuple[str, str]] = set()
        failures: list[dict[str, Any]] = []
        for item in recent[:limit]:
            if not isinstance(item, dict):
                continue
            task = self._single_line(item.get("task"), 40) or "LLM 调用"
            provider = self._single_line(item.get("provider"), 80) or "-"
            key = (task, provider)
            if bool(item.get("success", True)):
                recovered.add(key)
                continue
            ts = self._float(item.get("ts"))
            if ts > 0 and now - ts > max_age_seconds:
                continue
            if key in recovered:
                continue
            failures.append(item)
        return failures




    async def _to_thread_sqlite_inspect(self, func: Any, path: Path) -> dict[str, Any]:
        try:
            import asyncio

            return await asyncio.to_thread(func, path)
        except Exception:
            return func(path)



    def _remember_deleted_diary_day(self, date_key: str) -> None:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_key):
            return
        stored = self.plugin.data.get("daily_diary_deleted_days")
        values = stored if isinstance(stored, list) else []
        normalized: list[str] = []
        for value in [*values, date_key]:
            day = self._bookshelf_diary_date_key(value)
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) and day not in normalized:
                normalized.append(day)
        self.plugin.data["daily_diary_deleted_days"] = normalized[-90:]
        try:
            revision = max(0, int(self.plugin.data.get("daily_diary_delete_revision") or 0))
        except (TypeError, ValueError, OverflowError):
            revision = 0
        self.plugin.data["daily_diary_delete_revision"] = revision + 1




    async def _read_file_base64(self, path: Path) -> str:
        import asyncio

        raw = await asyncio.to_thread(path.read_bytes)
        return base64.b64encode(raw).decode("ascii")


    def _archive_item_comment_sample(self, item: dict[str, Any]) -> tuple[Path | None, list[Path], list[int]]:
        cover_path = self._resolve_bookshelf_data_file(item.get("cover_path"))
        pages = item.get("pages") if isinstance(item.get("pages"), list) else []
        page_by_index: dict[int, Path] = {}
        for page in pages:
            if not isinstance(page, dict):
                continue
            page_index = self._int(page.get("index"))
            page_path = self._resolve_bookshelf_data_file(page.get("path"))
            if page_index > 0 and page_path:
                page_by_index[page_index] = page_path
        sampled_pages = [
            self._int(page)
            for page in (item.get("sampled_pages") if isinstance(item.get("sampled_pages"), list) else [])
            if self._int(page) > 0 and self._int(page) in page_by_index
        ][:5]
        if not sampled_pages:
            sampled_pages = sorted(page_by_index)[:5]
        return cover_path, [page_by_index[page] for page in sampled_pages if page in page_by_index], sampled_pages






    async def apply_preset(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        name = str(payload.get("name", "")).strip()
        presets = self._presets()
        if name not in presets:
            return self._error("未知预设")
        preset = presets[name]
        try:
            for key, value in preset.get("settings", {}).items():
                self._apply_config_value(key, self._normalize_setting_value(key, value))
            for key, value in preset.get("features", {}).items():
                if key in self._allowed_feature_keys():
                    self._apply_config_value(key, self._normalize_bool_value(value))
            if any(key in self._allowed_provider_keys() for key in preset.get("settings", {})) or "provider_config_mode" in preset.get("settings", {}):
                apply_quick = getattr(self.plugin, "_apply_quick_provider_defaults", None)
                if callable(apply_quick):
                    apply_quick()
            config_saved = await self._save_config_if_possible()
            overview = await self.get_overview()
            if overview.get("success"):
                overview["data"]["preset"] = name
                overview["data"]["preset_label"] = preset.get("label", name)
                overview["data"]["config_saved"] = config_saved
            return overview
        except Exception as exc:
            logger.error(f"应用预设失败: {exc}", exc_info=True)
            return self._exception_error("应用预设失败")


    async def update_external_ability(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        normalizer = getattr(self.plugin, "_normalize_external_ability_name", None)
        name = self._single_line(payload.get("name"), 80)
        name = normalizer(name) if callable(normalizer) else name
        if not name:
            return self._error("缺少外部能力名称")
        try:
            async with self.plugin._data_lock:
                store_getter = getattr(self.plugin, "_external_ability_store", None)
                store = store_getter() if callable(store_getter) else self.plugin.data.setdefault("external_proactive_abilities", {})
                if not isinstance(store, dict):
                    store = {}
                    self.plugin.data["external_proactive_abilities"] = store
                item = store.get(name) if isinstance(store.get(name), dict) else {"name": name}
                if "enabled" in payload:
                    item["enabled"] = bool(payload.get("enabled"))
                if "share_probability" in payload:
                    item["share_probability"] = max(0.0, min(1.0, self._float(payload.get("share_probability"))))
                if "min_interval_hours" in payload:
                    item["min_interval_hours"] = max(0.0, self._float(payload.get("min_interval_hours")))
                if "config" in payload:
                    config = payload.get("config")
                    if isinstance(config, str):
                        import json
                        config = json.loads(config or "{}")
                    if not isinstance(config, dict):
                        return self._error("自定义配置必须是 JSON 对象")
                    item["config"] = config
                item["updated_ts"] = time.time()
                store[name] = item
                self.plugin._save_data_sync(sections={"external_proactive_abilities"})
                data = deepcopy(self.plugin.data)
            return self._ok({"message": "已保存外部主动能力", "external_abilities": self._external_ability_summary(data)})
        except Exception as exc:
            logger.error(f"更新外部主动能力失败: {exc}", exc_info=True)
            return self._exception_error("更新外部主动能力失败")












    def _astrbot_config_candidate_paths(self) -> list[Path]:
        root = Path(get_astrbot_data_path())
        home_root = Path.home() / ".astrbot"
        candidate_roots = [
            root,
            home_root / "data",
            home_root / "backend" / "data",
            home_root / "backend" / "app" / "data",
        ]
        paths: list[Path] = []
        seen: set[str] = set()

        def add_path(path: Path) -> None:
            try:
                key = str(path.resolve()).lower()
            except Exception:
                key = str(path).lower()
            if key in seen:
                return
            seen.add(key)
            paths.append(path)

        for candidate_root in candidate_roots:
            add_path(candidate_root / "cmd_config.json")
            config_dir = candidate_root / "config"
            if config_dir.exists():
                for path in sorted(config_dir.glob("abconf_*.json")):
                    add_path(path)
        return paths




    async def run_setup_daily_generation(self) -> dict[str, Any]:
        """Run setup-guide daily plan generation; current detail is optional because it is slow."""
        try:
            payload = await request.get_json(silent=True) or {}
        except Exception:
            payload = {}
        generate_schedule = self._normalize_bool_value(payload.get("generate_schedule", True))
        refine_schedule = self._normalize_bool_value(payload.get("refine_schedule", True))
        force_detail = self._normalize_bool_value(payload.get("force_detail", False))
        timeout_seconds = self._int(payload.get("timeout_seconds"), 18, 3, 60)
        plan: dict[str, Any] | None = None
        detail: dict[str, Any] | None = None
        current_detail_text = ""
        generation_status = ""
        pending = False
        detail_pending = False
        detail_error = ""
        try:
            if generate_schedule:
                plan, generation_status, pending = await self._setup_guide_generate_daily_plan_fast(timeout=timeout_seconds)
            else:
                async with self.plugin._data_lock:
                    current_plan = self.plugin.data.get("daily_plan", {})
                    plan = dict(current_plan) if isinstance(current_plan, dict) else {}
                generation_status = "current"

            if refine_schedule:
                if not plan:
                    plan = await self.plugin._ensure_daily_plan(force=False)
                    if not plan:
                        plan = await self.plugin._ensure_daily_plan(force=True)
                detail_refiner = getattr(self.plugin, "_ensure_detail_enhancement", None)
                if callable(detail_refiner):
                    detail_timeout = max(3, min(60, timeout_seconds))
                    detail_task = self._create_page_background_task(
                        detail_refiner(force=bool(force_detail)),
                        label="setup_daily_detail",
                    )
                    if detail_task is None:
                        detail_error = "后台细化任务无法启动"
                        detail_pending = False
                        detail_task = None

                    def _consume_setup_detail_task(done_task: asyncio.Task) -> None:
                        try:
                            done_task.result()
                        except asyncio.CancelledError:
                            pass
                        except Exception as exc:
                            logger.warning(
                                "首次配置后台日程细化失败: %s",
                                self._single_line(exc, 180),
                                exc_info=True,
                            )

                    if detail_task is not None:
                        detail_task.add_done_callback(_consume_setup_detail_task)
                    try:
                        if detail_task is None:
                            raise RuntimeError("setup detail task unavailable")
                        detail = await asyncio.wait_for(asyncio.shield(detail_task), timeout=detail_timeout)
                    except asyncio.TimeoutError:
                        detail_pending = True
                        detail_error = f"当前细化超过 {detail_timeout}s，已转入后台继续生成，可先继续配置。"
                    except Exception as exc:
                        detail_error = f"当前细化失败：{self._single_line(exc, 160)}"
                        logger.warning(
                            "首次配置日程细化失败: %s",
                            self._single_line(exc, 180),
                            exc_info=True,
                        )

            formatter = getattr(self.plugin, "_format_current_detail_view", None)
            if callable(formatter):
                try:
                    current_detail_text = formatter()
                except Exception as exc:
                    current_detail_text = f"当前细化展示失败：{self._single_line(exc, 160)}"

            async with self.plugin._data_lock:
                data = self._overview_data_snapshot_locked(self.plugin.data)

            plan_payload = dict(plan) if isinstance(plan, dict) else {}
            if not isinstance(plan_payload.get("items"), list) and isinstance(plan_payload.get("schedule"), list):
                plan_payload["items"] = plan_payload.get("schedule")

            return self._ok(
                {
                    "ok": True,
                    "plan": plan_payload,
                    "detail": detail if isinstance(detail, dict) else {},
                    "current_detail_text": current_detail_text,
                    "daily_state": self._daily_state_summary(data.get("daily_state")),
                    "daily_timeline": self._daily_timeline_summary(data),
                    "generation_status": generation_status,
                    "pending": pending,
                    "detail_pending": detail_pending,
                    "detail_error": detail_error,
                    "detail_skipped": bool(generate_schedule and not refine_schedule),
                }
            )
        except Exception as exc:
            logger.warning(f"首次配置日程生成失败: {exc}", exc_info=True)
            return self._ok({"ok": False, "error": self._single_line(exc, 220)})

    async def regenerate_daily_detail_segment(self) -> dict[str, Any]:
        try:
            payload = await request.get_json(silent=True) or {}
        except Exception:
            payload = {}
        key = self._single_line(payload.get("key"), 120)
        if not key:
            return self._error("缺少要重生成的时间段")
        action = self._single_line(payload.get("action"), 24).lower() or "regenerate"
        if action not in {"regenerate", "cancel"}:
            return self._error("不支持的日程段操作")
        previous_snapshot: dict[str, Any] = {}
        previous_item_state: dict[str, tuple[bool, Any]] = {}
        generation_id = ""
        segment: dict[str, Any] = {}
        try:
            async with self.plugin._data_lock:
                plan = deepcopy(self.plugin.data.get("daily_plan", {}))
                state = deepcopy(self.plugin.data.get("daily_state", {}))
                segments = self.plugin._collect_detail_segments(plan, {}, include_cancelled=True)
                segment = next((item for item in segments if self._single_line(item.get("key"), 120) == key), None)
                if not isinstance(segment, dict):
                    return self._error("该时间段已不存在或不属于今天的日程")
                live_plan = self.plugin.data.get("daily_plan", {})
                live_items = live_plan.get("items") if isinstance(live_plan, dict) else None
                live_index = self._int(segment.get("index"), -1, -1)
                live_item = live_items[live_index] if isinstance(live_items, list) and 0 <= live_index < len(live_items) and isinstance(live_items[live_index], dict) else None
                self.plugin._sync_detail_enhancement_day_locked(plan.get("date"))
                enhanced = self.plugin.data.setdefault("detail_enhanced_segments", {})
                if not isinstance(enhanced, dict):
                    enhanced = {}
                    self.plugin.data["detail_enhanced_segments"] = enhanced
                previous_snapshot = deepcopy(enhanced.get(key)) if isinstance(enhanced.get(key), dict) else {}
                if (
                    action == "regenerate"
                    and self._single_line(previous_snapshot.get("status"), 24) == "generating"
                    and self.plugin._detail_enhancement_snapshot_blocks_generation(previous_snapshot)
                ):
                    return self._error("该时间段正在细化中，请等待当前生成完成后再试")
                if action == "cancel":
                    if isinstance(live_item, dict):
                        live_item["lifecycle_status"] = "cancelled"
                        live_item["changed_at"] = self.plugin._environment_now().strftime("%H:%M")
                        live_item["change_reason"] = "用户在陪伴面板取消该日程段"
                        live_item.pop("_detail_generation_id", None)
                    cancelled = previous_snapshot or {"status": "done", "summary": "这一段已取消。", "today_events": [], "proactive_events": [], "state_variables": []}
                    for event in list(cancelled.get("today_events") or []) + list(cancelled.get("proactive_events") or []):
                        if isinstance(event, dict):
                            event["lifecycle_status"] = "cancelled"
                    cancelled["status"] = "cancelled"
                    cancelled["summary"] = self._single_line(cancelled.get("summary"), 120) or "这一段已取消。"
                    cancelled["cancelled_at"] = self.plugin._environment_now().strftime("%Y-%m-%d %H:%M:%S")
                    cancelled.pop("generation_id", None)
                    cancelled.pop("previous_item_state", None)
                    cancelled.pop("retry_after", None)
                    cancelled.pop("retry_after_ts", None)
                    enhanced[key] = cancelled
                    story = self.plugin._rebuild_story_plan_from_detail_snapshots(str(plan.get("date") or _today_key()))
                    self.plugin._remember_detail_enhancement_history(str(plan.get("date") or _today_key()), enhanced, story)
                    self.plugin._save_data_sync(
                        sections={
                            "daily_plan",
                            "detail_enhanced_day",
                            "detail_enhanced_segments",
                            "detail_enhanced_history",
                            "daily_story_plan",
                            "daily_story_plan_history",
                        }
                    )
                    data = self._overview_data_snapshot_locked(self.plugin.data)
                    return self._ok({"key": key, "cancelled": True, "daily_timeline": self._daily_timeline_summary(data)})
                if isinstance(live_item, dict):
                    for field in ("lifecycle_status", "changed_at", "change_reason", "_detail_generation_id"):
                        previous_item_state[field] = (field in live_item, deepcopy(live_item.get(field)))
                    generation_id = uuid.uuid4().hex
                    live_item["lifecycle_status"] = "changed"
                    live_item["changed_at"] = self.plugin._environment_now().strftime("%H:%M")
                    live_item["change_reason"] = "用户在陪伴面板重新细化该日程段"
                    live_item["_detail_generation_id"] = generation_id
                    segment_item = segment.get("item") if isinstance(segment.get("item"), dict) else None
                    if isinstance(segment_item, dict):
                        segment_item["lifecycle_status"] = "changed"
                else:
                    generation_id = uuid.uuid4().hex
                enhanced[key] = {
                    "status": "generating",
                    "started_at": self.plugin._environment_now().strftime("%H:%M"),
                    "started_ts": time.time(),
                    "regenerated": True,
                    "generation_id": generation_id,
                    "previous_item_state": {
                        field: {"existed": existed, "value": value}
                        for field, (existed, value) in previous_item_state.items()
                    },
                }
                self.plugin._save_data_sync(
                    sections={
                        "daily_plan",
                        "detail_enhanced_day",
                        "detail_enhanced_segments",
                    }
                )

            detail = await generate_detail_enhancement(self.plugin, segment, plan, state)
            if not isinstance(detail.get("today_events"), list) or not detail.get("today_events"):
                raise RuntimeError("局部重生成未返回可用的细化事件")

            async with self.plugin._data_lock:
                if not self.plugin._detail_generation_is_current(segment, generation_id):
                    stale_plan = self.plugin.data.get("daily_plan", {})
                    stale_items = stale_plan.get("items") if isinstance(stale_plan, dict) else None
                    stale_index = self._int(segment.get("index"), -1, -1)
                    stale_item = stale_items[stale_index] if isinstance(stale_items, list) and 0 <= stale_index < len(stale_items) and isinstance(stale_items[stale_index], dict) else None
                    if isinstance(stale_item, dict) and self._single_line(stale_item.get("_detail_generation_id"), 64) == generation_id:
                        stale_item.pop("_detail_generation_id", None)
                        self.plugin._save_data_sync(
                            sections={
                                "daily_plan",
                                "detail_enhanced_day",
                                "detail_enhanced_segments",
                            }
                        )
                    return self._error("该时间段已被取消、替换或由更新的操作接管，本次迟到结果未写入")
                current = self.plugin.data.setdefault("detail_enhanced_segments", {})
                current[key] = {
                    "status": "done",
                    "updated_at": self.plugin._environment_now().strftime("%H:%M"),
                    "summary": self._single_line(detail.get("summary"), 120),
                    "summary_basis": self.plugin._normalize_schedule_basis(detail.get("summary_basis"), default=["coarse_plan"]),
                    "summary_confidence": min(1.0, self._float(detail.get("summary_confidence"), 0.75)),
                    "location": self._single_line(detail.get("location"), 60),
                    "location_basis": self.plugin._normalize_schedule_basis(detail.get("location_basis"), default=["coarse_plan"]),
                    "location_confidence": min(1.0, self._float(detail.get("location_confidence"), 0.72)),
                    "today_events": detail.get("today_events", []),
                    "proactive_events": detail.get("proactive_events", []),
                    "state_variables": detail.get("state_variables", []),
                    "presence_status": detail.get("presence_status", {}),
                    "quality": detail.get("quality", {}),
                    "interaction_updates": previous_snapshot.get("interaction_updates", []),
                    "regenerated": True,
                    "regenerated_at": self.plugin._environment_now().strftime("%Y-%m-%d %H:%M:%S"),
                }
                self.plugin._sanitize_detail_enhanced_segments_inplace(current)
                story = self.plugin._rebuild_story_plan_from_detail_snapshots(str(plan.get("date") or _today_key()))
                self.plugin._remember_detail_enhancement_history(str(plan.get("date") or _today_key()), current, story)
                current_plan = self.plugin.data.get("daily_plan", {})
                current_items = current_plan.get("items") if isinstance(current_plan, dict) else None
                current_index = self._int(segment.get("index"), -1, -1)
                current_item = current_items[current_index] if isinstance(current_items, list) and 0 <= current_index < len(current_items) and isinstance(current_items[current_index], dict) else None
                if isinstance(current_item, dict) and self._single_line(current_item.get("_detail_generation_id"), 64) == generation_id:
                    current_item.pop("_detail_generation_id", None)
                self.plugin._refresh_daily_state_location_from_plan(
                    plan=current_plan if isinstance(current_plan, dict) else plan,
                    detail=detail,
                    segment=segment,
                )
                self.plugin._save_data_sync(
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
                data = self._overview_data_snapshot_locked(self.plugin.data)
            return self._ok({"key": key, "detail": detail, "daily_timeline": self._daily_timeline_summary(data)})
        except Exception as exc:
            logger.warning("局部重生成日程细化失败: %s", exc, exc_info=True)
            async with self.plugin._data_lock:
                enhanced = self.plugin.data.setdefault("detail_enhanced_segments", {})
                if isinstance(enhanced, dict) and key and generation_id and self.plugin._detail_generation_is_current(segment, generation_id):
                    restored = previous_snapshot or {"status": "failed", "today_events": [], "proactive_events": [], "state_variables": []}
                    restored["regeneration_error"] = self._single_line(exc, 180)
                    restored["regeneration_failed_at"] = self.plugin._environment_now().strftime("%Y-%m-%d %H:%M:%S")
                    enhanced[key] = restored
                    live_plan = self.plugin.data.get("daily_plan", {})
                    live_items = live_plan.get("items") if isinstance(live_plan, dict) else None
                    live_index = self._int(segment.get("index"), -1, -1)
                    live_item = live_items[live_index] if isinstance(live_items, list) and 0 <= live_index < len(live_items) and isinstance(live_items[live_index], dict) else None
                    if isinstance(live_item, dict) and self._single_line(live_item.get("_detail_generation_id"), 64) == generation_id:
                        for field, (existed, value) in previous_item_state.items():
                            if existed:
                                live_item[field] = value
                            else:
                                live_item.pop(field, None)
                    self.plugin._save_data_sync(
                        sections={
                            "daily_plan",
                            "detail_enhanced_day",
                            "detail_enhanced_segments",
                        }
                    )
            return self._exception_error("局部重生成失败")


    def _roleplay_provider_role(self, provider_id: Any) -> str:
        pid = str(provider_id or "").strip()
        if not pid:
            return ""
        roles = [
            ("FAST_RESPONSE_PROVIDER_ID", getattr(self.plugin, "fast_response_provider_id", "")),
            ("COMPLEX_REASONING_PROVIDER_ID", getattr(self.plugin, "complex_reasoning_provider_id", "")),
            ("LLM_PROVIDER_ID", getattr(self.plugin, "llm_provider_id", "")),
        ]
        for role, value in roles:
            if pid == str(value or "").strip():
                return role
        return "CUSTOM_PROVIDER_ID"


























    def _loads_json_object(self, raw: Any) -> dict[str, Any]:
        if isinstance(raw, dict):
            return raw
        text = str(raw or "").strip()
        if not text:
            raise ValueError("模型返回为空")
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE).strip()
            text = re.sub(r"\s*```$", "", text).strip()
        try:
            parsed = json.loads(text)
        except Exception:
            start = text.find("{")
            end = text.rfind("}")
            if start < 0 or end <= start:
                raise ValueError("模型没有返回 JSON 对象")
            parsed = json.loads(text[start : end + 1])
        if not isinstance(parsed, dict):
            raise ValueError("模型返回的 JSON 不是对象")
        return parsed

    async def list_available_providers(self) -> dict[str, Any]:
        try:
            items = self._available_provider_items()
            embedding_items = await self._available_embedding_provider_items()
            tts_items = self._available_tts_provider_items()
            return self._ok(
                {
                    "items": items,
                    "total": len(items),
                    "embedding_items": embedding_items,
                    "embedding_total": len(embedding_items),
                    "tts_items": tts_items,
                    "tts_total": len(tts_items),
                }
            )
        except Exception as exc:
            logger.error(f"获取 Provider 列表失败: {exc}", exc_info=True)
            return self._exception_error("获取 Provider 列表失败")



























    def _display_message_text(self, value: Any, limit: int = 500) -> str:
        source = str(value or "").strip()
        source = source.strip("\"'“”‘’` ")
        if self._looks_like_internal_delivery_receipt(source):
            return ""
        if re.fullmatch(r"[.。…~～\s\"'“”‘’`-]{0,12}", source):
            return ""
        if re.search(r"<t{2,}s\b[^>]*>.*?</t{2,}s>", source, flags=re.IGNORECASE | re.DOTALL):
            outside = re.sub(r"<t{2,}s\b[^>]*>.*?</t{2,}s>", "", source, flags=re.IGNORECASE | re.DOTALL)
            outside = re.sub(r"</?t{2,}s\b[^>]*>", "", outside, flags=re.IGNORECASE).strip()
            if re.search(r"[\u4e00-\u9fff]", outside):
                source = outside
            else:
                source = re.sub(r"</?t{2,}s\b[^>]*>", "", source, flags=re.IGNORECASE)
        if re.search(r"[\u3040-\u30ff]", source) and re.search(r"[\u4e00-\u9fff]", source):
            units = re.findall(r".*?[。！？!?…~～]+|.+$", source, flags=re.DOTALL)
            kept = [unit.strip() for unit in units if unit.strip() and not re.search(r"[\u3040-\u30ff]", unit)]
            if kept and any(re.search(r"[\u4e00-\u9fff]", item) for item in kept):
                source = "".join(kept)
        return self._single_line(_strip_internal_message_blocks(source, enabled=bool(runtime_persona_setting(self.plugin, "enable_framework_error_leak_guard", True))), limit)

    @staticmethod
    def _looks_like_internal_delivery_receipt(text: Any) -> bool:
        raw = str(text or "").strip()
        if not raw:
            return False
        compact = re.sub(r"[\s。.!！?？,，；;:：、~～\"'“”‘’（）()【】\[\]]+", "", raw).lower()
        if compact in {"已发送", "发送成功", "发送完成", "发送完毕", "已成功发送", "消息已发送", "消息发送成功"}:
            return True
        markers = (
            ("已经把", "转给"),
            ("已把", "转给"),
            ("已经将", "转给"),
            ("已将", "转给"),
            ("已经发给", "就假装"),
            ("已经发送给", "就假装"),
            ("就假装", "语气很自然"),
            ("随手分享", "语气很自然"),
        )
        if any(all(token in raw for token in pair) for pair in markers):
            return True
        return (
            any(token in compact for token in ("视频链接转给", "链接转给", "消息转给", "内容转给"))
            and any(token in compact for token in ("已经", "已", "完成", "成功"))
        )

    def _sanitize_last_bot_interjection(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict) or not value:
            return {}
        item = dict(value)
        item["text"] = self._display_message_text(item.get("text"), 120)
        if not item["text"] and not item.get("has_image"):
            return {}
        return item

    @staticmethod
    def _format_duration(seconds: float) -> str:
        seconds = max(0, int(seconds or 0))
        if seconds < 60:
            return "不到 1 分钟"
        minutes = seconds // 60
        if minutes < 60:
            return f"{minutes} 分钟"
        hours = minutes // 60
        rest = minutes % 60
        return f"{hours} 小时 {rest} 分钟" if rest else f"{hours} 小时"




    def _provider_settings(self) -> dict[str, str]:
        keys = [
            "FAST_RESPONSE_PROVIDER_ID",
            "COMPLEX_REASONING_PROVIDER_ID",
            "CREATIVE_MODEL_PROVIDER_ID",
            "EMBEDDING_PROVIDER_ID",
            "LLM_PROVIDER_ID",
            "MAI_STYLE_PROVIDER_ID",
            "DAILY_PLAN_PROVIDER_ID",
            "DETAIL_ENHANCEMENT_PROVIDER_ID",
            "DREAM_DIARY_PROVIDER_ID",
            "CREATIVE_PROVIDER_ID",
            "CREATIVE_OUTLINE_PROVIDER_ID",
            "CREATIVE_REVIEW_PROVIDER_ID",
            "VOICE_PROMPT_PROVIDER_ID",
            "tts_conversion_provider_id",
            "PHOTO_PROMPT_PROVIDER_ID",
            "NARRATION_PROVIDER_ID",
            "HISTORY_SUMMARY_PROVIDER_ID",
            "RESPONSE_REVIEW_PROVIDER_ID",
            "SMART_SILENCE_PROVIDER_ID",
            "PROACTIVE_PERSONA_JUDGE_PROVIDER_ID",
            "TROUBLESHOOTING_PROVIDER_ID",
            "DAILY_REVIEW_PROVIDER_ID",
            "SMART_MESSAGE_DEBOUNCE_PROVIDER_ID",
            "REST_WAKEUP_PROVIDER_ID",
            "RELATIONSHIP_ANALYSIS_PROVIDER_ID",
            "EMOTION_JUDGEMENT_PROVIDER_ID",
            "COMPANION_MEMORY_PROVIDER_ID",
            "DIALOGUE_EPISODE_PROVIDER_ID",
            "GROUP_INTERJECT_PROVIDER_ID",
            "GROUP_EPISODE_PROVIDER_ID",
            "GROUP_SLANG_PROVIDER_ID",
            "GROUP_FOLLOWUP_JUDGE_PROVIDER_ID",
            "FORWARD_MESSAGE_PROVIDER_ID",
            "PLUGIN_VISION_PROVIDER_ID",
            "READING_ARCHIVE_VISION_PROVIDER_ID",
            "REACTION_EXPRESSION_EMBEDDING_PROVIDER_ID",
            "NEWS_PROVIDER_ID",
            "WEB_EXPLORATION_PROVIDER_ID",
        ]
        for key in sorted(self._schema_provider_keys(public_only=True)):
            if key not in keys:
                keys.append(key)
        values = {key: self._config_get(key) for key in keys}

        def runtime_fallback(config_key: str, attr_name: str) -> None:
            # Runtime values are only a compatibility fallback for configs
            # that predate the grouped Provider key.  They must not override
            # an explicit empty value saved by the user.
            if self._config_get_raw(config_key, _MISSING) is _MISSING:
                values[config_key] = str(getattr(self.plugin, attr_name, "") or "")

        for config_key, attr_name in (
            ("FAST_RESPONSE_PROVIDER_ID", "fast_response_provider_id"),
            ("COMPLEX_REASONING_PROVIDER_ID", "complex_reasoning_provider_id"),
            ("CREATIVE_MODEL_PROVIDER_ID", "creative_model_provider_id"),
            ("PLUGIN_VISION_PROVIDER_ID", "plugin_vision_provider_id"),
            ("EMBEDDING_PROVIDER_ID", "embedding_provider_id"),
            ("READING_ARCHIVE_VISION_PROVIDER_ID", "reading_archive_vision_provider_id"),
            ("DREAM_DIARY_PROVIDER_ID", "dream_diary_provider_id"),
            ("SMART_MESSAGE_DEBOUNCE_PROVIDER_ID", "smart_message_debounce_provider_id"),
            ("SMART_SILENCE_PROVIDER_ID", "smart_silence_provider_id"),
            ("REST_WAKEUP_PROVIDER_ID", "rest_wakeup_provider_id"),
            ("PROACTIVE_PERSONA_JUDGE_PROVIDER_ID", "proactive_persona_judge_provider_id"),
            ("tts_conversion_provider_id", "tts_conversion_provider_id"),
        ):
            runtime_fallback(config_key, attr_name)
        return values

    @staticmethod
    def _normalize_provider_mode_value(value: Any) -> str:
        text = str(value or "").strip().lower()
        return "precision" if text in {"precision", "precise", "advanced", "精准", "精准配置", "分流"} else "quick"

    def _precision_bundle_from_quick(self, values: dict[str, str]) -> dict[str, str]:
        fast = self._single_line(values.get("FAST_RESPONSE_PROVIDER_ID"), 160)
        complex_model = self._single_line(values.get("COMPLEX_REASONING_PROVIDER_ID") or values.get("LLM_PROVIDER_ID"), 160)
        creative = self._single_line(values.get("CREATIVE_MODEL_PROVIDER_ID"), 160)
        return {
            "LLM_PROVIDER_ID": complex_model,
            "MAI_STYLE_PROVIDER_ID": fast or complex_model,
            "DAILY_PLAN_PROVIDER_ID": complex_model,
            "DETAIL_ENHANCEMENT_PROVIDER_ID": complex_model,
            "HISTORY_SUMMARY_PROVIDER_ID": complex_model,
            "RELATIONSHIP_ANALYSIS_PROVIDER_ID": complex_model,
            "COMPANION_MEMORY_PROVIDER_ID": complex_model,
            "DIALOGUE_EPISODE_PROVIDER_ID": complex_model,
            "GROUP_EPISODE_PROVIDER_ID": complex_model,
            "FORWARD_MESSAGE_PROVIDER_ID": complex_model,
            "PROACTIVE_PERSONA_JUDGE_PROVIDER_ID": complex_model,
            "RESPONSE_REVIEW_PROVIDER_ID": fast or complex_model,
            "SMART_SILENCE_PROVIDER_ID": fast or complex_model,
            "TROUBLESHOOTING_PROVIDER_ID": complex_model,
            "DAILY_REVIEW_PROVIDER_ID": complex_model,
            "EMOTION_JUDGEMENT_PROVIDER_ID": fast or complex_model,
            "SMART_MESSAGE_DEBOUNCE_PROVIDER_ID": fast or complex_model,
            "REST_WAKEUP_PROVIDER_ID": fast or complex_model,
            "GROUP_FOLLOWUP_JUDGE_PROVIDER_ID": fast,
            "GROUP_INTERJECT_PROVIDER_ID": fast or complex_model,
            "GROUP_SLANG_PROVIDER_ID": fast or complex_model,
            "VOICE_PROMPT_PROVIDER_ID": fast or complex_model,
            "tts_conversion_provider_id": fast or complex_model,
            "NARRATION_PROVIDER_ID": fast or complex_model,
            "NEWS_PROVIDER_ID": fast or complex_model,
            "WEB_EXPLORATION_PROVIDER_ID": fast or complex_model,
            "CREATIVE_PROVIDER_ID": creative or complex_model,
            "CREATIVE_OUTLINE_PROVIDER_ID": creative or complex_model,
            "CREATIVE_REVIEW_PROVIDER_ID": creative or complex_model,
            "DREAM_DIARY_PROVIDER_ID": creative or complex_model,
            "PHOTO_PROMPT_PROVIDER_ID": creative or complex_model,
        }

    def _quick_bundle_from_precision(self, values: dict[str, str]) -> dict[str, str]:
        fast = self._single_line(
            values.get("RESPONSE_REVIEW_PROVIDER_ID")
            or values.get("SMART_MESSAGE_DEBOUNCE_PROVIDER_ID")
            or values.get("SMART_SILENCE_PROVIDER_ID")
            or values.get("MAI_STYLE_PROVIDER_ID")
            or values.get("LLM_PROVIDER_ID"),
            160,
        )
        complex_model = self._single_line(
            values.get("LLM_PROVIDER_ID")
            or values.get("DAILY_PLAN_PROVIDER_ID")
            or values.get("COMPANION_MEMORY_PROVIDER_ID")
            or values.get("MAI_STYLE_PROVIDER_ID"),
            160,
        )
        creative = self._single_line(
            values.get("CREATIVE_PROVIDER_ID")
            or values.get("DREAM_DIARY_PROVIDER_ID")
            or values.get("PHOTO_PROMPT_PROVIDER_ID")
            or complex_model,
            160,
        )
        return {
            "FAST_RESPONSE_PROVIDER_ID": fast,
            "COMPLEX_REASONING_PROVIDER_ID": complex_model,
            "CREATIVE_MODEL_PROVIDER_ID": creative,
        }

    def _expand_provider_overwrite_bundle(self, mode: str, values: dict[str, str]) -> dict[str, str]:
        merged = {key: self._single_line(value, 160) for key, value in self._provider_settings().items()}
        for key, value in values.items():
            if key in self._allowed_provider_keys():
                merged[key] = self._single_line(value, 160)
        if self._normalize_provider_mode_value(mode) == "quick":
            merged.update(self._precision_bundle_from_quick(merged))
        else:
            merged.update(self._quick_bundle_from_precision(merged))
        return {key: self._single_line(value, 160) for key, value in merged.items() if key in self._allowed_provider_keys()}






























    def _plugin_version(self) -> str:
        for source in (self.plugin, getattr(self.plugin, "metadata", None)):
            for attr in ("version", "__version__", "plugin_version"):
                value = getattr(source, attr, None)
                if value:
                    return str(value).strip()
        try:
            metadata_path = Path(__file__).with_name("metadata.yaml")
            text = metadata_path.read_text(encoding="utf-8")
            match = re.search(r"(?m)^version:\s*['\"]?([^'\"\s#]+)", text)
            if match:
                return match.group(1).strip()
        except Exception:
            pass
        return "unknown"




    def _strip_runtime_data(self, value: Any) -> Any:
        runtime_keys = {
            "recent_messages",
            "recent_message_ids",
            "recent_replies",
            "recent_group_messages",
            "proactive_sending",
            "proactive_audit_log",
            "proactive_candidates",
            "pending_followup_event",
            "pending_timer_events",
            "pending_atrelay_requests",
            "suspended_proactive",
            "input_status",
            "current_input_status",
            "recall_message_cache",
            "image_cache",
            "visual_summary_cache",
            "token_usage",
            "token_stats",
            "troubleshooting_records",
            "maintenance_records",
        }
        runtime_prefixes = (
            "recent_",
            "pending_",
            "last_message",
            "last_reply",
            "last_sent",
            "last_proactive",
            "cooldown_",
            "session_",
        )
        if isinstance(value, dict):
            cleaned: dict[str, Any] = {}
            for key, item in value.items():
                key_text = str(key)
                if key_text in runtime_keys or any(key_text.startswith(prefix) for prefix in runtime_prefixes):
                    continue
                if key_text.endswith("_cache") or key_text.endswith("_audit_log"):
                    continue
                cleaned[key_text] = self._strip_runtime_data(item)
            return cleaned
        if isinstance(value, list):
            return [self._strip_runtime_data(item) for item in value]
        return value







    def _deep_merge_dict(self, target: dict[str, Any], incoming: dict[str, Any], *, conflict: str = "use_backup") -> None:
        for key, value in incoming.items():
            if isinstance(value, dict) and isinstance(target.get(key), dict):
                self._deep_merge_dict(target[key], value, conflict=conflict)
            elif key in target and not self._should_apply_migration_value(target.get(key), value, conflict):
                continue
            else:
                target[key] = deepcopy(value)


    def _available_provider_items(self) -> list[dict[str, Any]]:
        providers: list[Any] = []
        context = getattr(self.plugin, "context", None)
        get_all = getattr(context, "get_all_providers", None)
        if callable(get_all):
            try:
                providers = list(get_all() or [])
            except Exception:
                providers = []
        if not providers:
            manager = getattr(context, "provider_manager", None)
            inst_map = getattr(manager, "inst_map", None)
            if isinstance(inst_map, dict):
                providers = list(inst_map.values())

        using_id = ""
        get_using = getattr(context, "get_using_provider", None)
        if callable(get_using):
            try:
                using_id = self._provider_id(get_using())
            except Exception:
                using_id = ""

        items: list[dict[str, str]] = []
        seen: set[str] = set()
        for provider in providers:
            provider_id = self._provider_id(provider)
            if not provider_id or provider_id in seen:
                continue
            seen.add(provider_id)
            items.append(
                {
                    "id": provider_id,
                    "name": self._provider_name(provider, provider_id),
                    "type": self._provider_type(provider),
                    "model": self._provider_model(provider),
                    "is_default": provider_id == using_id,
                }
            )
        items.sort(key=lambda item: (not item["is_default"], item["name"].lower(), item["id"].lower()))
        return items

    @staticmethod
    def _is_embedding_provider(provider: Any) -> bool:
        return any(
            callable(getattr(provider, name, None))
            for name in ("get_embedding", "get_embeddings", "get_embeddings_batch")
        )

    async def _available_embedding_provider_items(self) -> list[dict[str, Any]]:
        context = getattr(self.plugin, "context", None)
        providers: list[Any] = []
        get_all = getattr(context, "get_all_embedding_providers", None)
        if callable(get_all):
            try:
                resolved = get_all()
                if asyncio.iscoroutine(resolved) or hasattr(resolved, "__await__"):
                    resolved = await resolved
                providers = list(resolved.values()) if isinstance(resolved, dict) else list(resolved or [])
            except Exception:
                providers = []
        manager = getattr(context, "provider_manager", None)
        if not providers:
            providers = list(getattr(manager, "embedding_provider_insts", None) or [])
        if not providers and isinstance(getattr(manager, "inst_map", None), dict):
            providers = [
                provider
                for provider in manager.inst_map.values()
                if self._is_embedding_provider(provider)
            ]

        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        for provider in providers:
            if not self._is_embedding_provider(provider):
                continue
            provider_id = self._provider_id(provider)
            if not provider_id or provider_id in seen:
                continue
            seen.add(provider_id)
            items.append(
                {
                    "id": provider_id,
                    "name": self._provider_name(provider, provider_id),
                    "type": self._provider_type(provider) or "embedding",
                    "model": self._provider_model(provider),
                    "is_default": False,
                }
            )
        items.sort(key=lambda item: (item["name"].lower(), item["id"].lower()))
        return items

    async def _embedding_provider_for_test(self, provider_id: str) -> Any:
        context = getattr(self.plugin, "context", None)
        if provider_id and context is not None:
            for getter_name in ("get_embedding_provider_by_id", "get_provider_by_id"):
                getter = getattr(context, getter_name, None)
                if not callable(getter):
                    continue
                try:
                    provider = getter(provider_id)
                    if asyncio.iscoroutine(provider) or hasattr(provider, "__await__"):
                        provider = await provider
                except Exception:
                    provider = None
                if self._is_embedding_provider(provider):
                    return provider
            manager = getattr(context, "provider_manager", None)
            candidates = list(getattr(manager, "embedding_provider_insts", None) or [])
            if isinstance(getattr(manager, "inst_map", None), dict):
                candidates.extend(manager.inst_map.values())
            for provider in candidates:
                if self._provider_id(provider) == provider_id and self._is_embedding_provider(provider):
                    return provider
            return None
        resolver = getattr(self.plugin, "_reaction_embedding_provider", None)
        if callable(resolver):
            resolved = resolver()
            if asyncio.iscoroutine(resolved) or hasattr(resolved, "__await__"):
                resolved = await resolved
            if isinstance(resolved, tuple) and resolved and self._is_embedding_provider(resolved[0]):
                return resolved[0]
        manager = getattr(context, "provider_manager", None)
        for provider in list(getattr(manager, "embedding_provider_insts", None) or []):
            if self._is_embedding_provider(provider):
                return provider
        return None

    @staticmethod
    def _provider_config(provider: Any) -> Any:
        return getattr(provider, "provider_config", None) or getattr(provider, "config", None) or {}

    @classmethod
    def _provider_config_value(cls, provider: Any, *keys: str) -> str:
        config = cls._provider_config(provider)
        for key in keys:
            value = ""
            if isinstance(config, dict):
                value = str(config.get(key, "") or "")
            else:
                value = str(getattr(config, key, "") or "")
            if value:
                return value.strip()
        return ""

    @classmethod
    def _provider_id(cls, provider: Any) -> str:
        if provider is None:
            return ""
        return (
            cls._provider_config_value(provider, "id", "provider_id")
            or str(getattr(provider, "provider_id", "") or "").strip()
            or str(getattr(provider, "id", "") or "").strip()
        )

    @classmethod
    def _provider_name(cls, provider: Any, provider_id: str) -> str:
        explicit_name = (
            cls._provider_config_value(provider, "name", "display_name", "label", "title")
            or str(getattr(provider, "name", "") or "").strip()
            or str(getattr(provider, "display_name", "") or "").strip()
        )
        if explicit_name and not cls._provider_name_is_protocol(explicit_name):
            return explicit_name
        if provider_id and not cls._provider_name_is_protocol(provider_id):
            return provider_id
        inferred = cls._provider_vendor_from_config(provider)
        if inferred:
            return inferred
        protocol_name = cls._provider_config_value(provider, "provider", "type", "provider_type")
        return explicit_name or protocol_name or provider_id

    @staticmethod
    def _provider_name_is_protocol(value: Any) -> bool:
        text = str(value or "").strip().lower()
        normalized = re.sub(r"[\s_\-]+", "", text)
        return normalized in {
            "openai",
            "openai兼容",
            "openai-compatible",
            "openaicompatible",
            "compatible",
            "兼容",
            "兼容模式",
        }

    @classmethod
    def _provider_vendor_from_config(cls, provider: Any) -> str:
        source = " ".join(
            cls._provider_config_value(
                provider,
                "api_base",
                "base_url",
                "api_base_url",
                "api_url",
                "endpoint",
                "url",
                "model",
                "model_name",
                "api_model",
                "model_id",
            ).split()
        ).lower()
        if not source:
            return ""
        try:
            parsed = urlparse(source if "://" in source else f"https://{source}")
            host = parsed.netloc.lower()
        except Exception:
            host = ""
        haystack = f"{source} {host}"
        vendors = [
            ("火山引擎", ("volces.com", "volcengine", "huoshan", "火山", "doubao", "ark.cn-beijing")),
            ("DeepSeek", ("deepseek.com", "deepseek")),
            ("阿里云百炼", ("dashscope", "aliyuncs.com", "bailian", "百炼", "qwen", "tongyi")),
            ("魔搭社区", ("modelscope", "api-inference", "魔搭")),
            ("OpenRouter", ("openrouter.ai", "openrouter")),
            ("硅基流动", ("siliconflow.cn", "siliconflow")),
            ("智谱", ("bigmodel.cn", "zhipu", "glm")),
            ("月之暗面", ("moonshot.cn", "moonshot", "kimi")),
            ("Google", ("generativelanguage.googleapis.com", "googleapis.com", "gemini")),
            ("Anthropic", ("anthropic.com", "claude")),
            ("OpenAI", ("api.openai.com", "openai.com", "gpt-")),
        ]
        for label, needles in vendors:
            if any(needle in haystack for needle in needles):
                return label
        if host:
            core = host.split(":")[0]
            for prefix in ("api.", "ark.", "www."):
                if core.startswith(prefix):
                    core = core[len(prefix):]
            return core.split(".")[0] or ""
        return ""

    @classmethod
    def _provider_model(cls, provider: Any) -> str:
        configured = cls._provider_config_value(provider, "model", "model_name", "api_model", "model_id")
        if configured:
            return configured
        get_model = getattr(provider, "get_model", None)
        if callable(get_model):
            try:
                return str(get_model() or "").strip()
            except Exception:
                pass
        return str(getattr(provider, "model_name", "") or getattr(provider, "model", "") or "").strip()

    @classmethod
    def _provider_type(cls, provider: Any) -> str:
        return (
            cls._provider_config_value(provider, "type", "provider_type")
            or provider.__class__.__name__
        )














    @staticmethod
    def _presets() -> dict[str, dict[str, Any]]:
        return {
            "safe": {
                "label": "保守低打扰",
                "settings": {
                    "max_daily_messages": 3,
                    "idle_minutes": 180,
                    "min_interval_minutes": 360,
                    "group_interject_max_daily": 0,
                    "group_interject_min_interval_minutes": 360,
                    "memory_refresh_interval_minutes": 720,
                    "episode_memory_refresh_messages": 12,
                    "episode_memory_refresh_minutes": 180,
                },
                "features": {
                    "enable_group_companion": True,
                    "enable_group_interjection": False,
                    "enable_companion_memory": True,
                    "enable_expression_learning": True,
                    "enable_passive_response_review": True,
                    "enable_proactive_message_review": True,
                    "enable_livingmemory_integration": True,
                },
            },
            "standard": {
                "label": "标准陪伴",
                "settings": {
                    "max_daily_messages": 6,
                    "idle_minutes": 60,
                    "min_interval_minutes": 120,
                    "group_interject_max_daily": 1,
                    "group_interject_min_interval_minutes": 240,
                    "memory_refresh_interval_minutes": 360,
                    "episode_memory_refresh_messages": 8,
                    "episode_memory_refresh_minutes": 90,
                },
                "features": {
                    "enable_group_companion": True,
                    "enable_group_interjection": False,
                    "enable_group_context_injection": True,
                    "enable_group_injection_guard": True,
                    "enable_companion_memory": True,
                    "enable_expression_learning": True,
                    "enable_dialogue_episode_memory": True,
                    "enable_open_loop_tracking": True,
                    "enable_passive_response_review": True,
                    "enable_proactive_message_review": True,
                },
            },
            "active": {
                "label": "高互动学习",
                "settings": {
                    "max_daily_messages": 10,
                    "idle_minutes": 30,
                    "min_interval_minutes": 60,
                    "group_interject_max_daily": 2,
                    "group_interject_min_interval_minutes": 180,
                    "memory_refresh_interval_minutes": 240,
                    "episode_memory_refresh_messages": 5,
                    "episode_memory_refresh_minutes": 60,
                },
                "features": {
                    "enable_group_companion": True,
                    "enable_group_injection_guard": True,
                    "enable_companion_memory": True,
                    "enable_expression_learning": True,
                    "enable_intent_emotion_analysis": True,
                    "enable_passive_response_review": True,
                    "enable_proactive_message_review": True,
                    "enable_dialogue_episode_memory": True,
                    "enable_open_loop_tracking": True,
                    "enable_group_interjection": True,
                    "enable_group_interjection_feedback": True,
                },
            },
            "group_observer": {
                "label": "群聊观察优先",
                "settings": {
                    "max_daily_messages": 4,
                    "idle_minutes": 90,
                    "min_interval_minutes": 180,
                    "group_interject_max_daily": 0,
                    "group_interject_min_interval_minutes": 240,
                    "max_group_recent_messages": 120,
                    "max_group_slang_terms": 80,
                },
                "features": {
                    "enable_group_companion": True,
                    "enable_group_context_injection": True,
                    "enable_group_injection_guard": True,
                    "enable_group_slang_learning": True,
                    "enable_group_member_profiles": True,
                    "enable_group_topic_threads": True,
                    "enable_group_episode_memory": True,
                    "enable_group_slang_meanings": True,
                    "enable_group_relationship_graph": True,
                    "enable_group_privacy_guard": True,
                    "enable_group_interjection": False,
                },
            },
        }

    def _forward_runtime_config_effects(
        self,
        key: str,
        value: Any,
        overrides: dict[str, Any] | None = None,
    ) -> None:
        dispatch_runtime_config_effects(
            self.plugin,
            {key: value},
            source="page",
            adapter=self,
            overrides=overrides,
        )

    def _schedule_body_monitor_integration_toggle(self, enabled: bool) -> asyncio.Task[Any] | None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return None
        task = loop.create_task(
            self._sync_body_monitor_integration_toggle(enabled),
            name="private_companion_body_monitor_toggle",
        )
        self.plugin._body_monitor_integration_toggle_task = task
        return task



    @staticmethod
    def _normalize_bool_value(value: Any) -> bool:
        if isinstance(value, str):
            text = value.strip().lower()
            if text in {"true", "1", "yes", "y", "on", "enable", "enabled", "启用", "开启", "开", "是"}:
                return True
            if text in {"false", "0", "no", "n", "off", "disable", "disabled", "停用", "关闭", "关", "否", ""}:
                return False
        return bool(value)

    def _set_config_value(self, key: str, value: Any) -> None:
        config = getattr(self.plugin, "config", None)
        if config is None:
            return
        if key == "provider_config_mode":
            self._set_provider_config_mode_value(config, value)
            return
        if key == "proactive_intensity_preset":
            self._set_schema_compat_value(config, key, value)
            return
        if key in {"page_font_family", "page_theme"}:
            self._set_schema_compat_value(config, key, value)
            return
        if key == "allow_generate_photo_on_reaction_turns":
            # Keep the grouped schema entry and the hidden legacy flat key in
            # sync: the runtime reads the flat attribute injected by AstrBot,
            # while the visible config page renders the grouped entry.  A
            # one-sided write leaves a stale flat False that silently disables
            # the opt-in switch after a reload.
            self._set_schema_compat_value(config, key, value)
            return
        updated_existing = _set_into_config(config, key, value, allow_flat_fallback=False)
        updated_group = self._set_schema_group_config_value(config, key, value, create_group=True)
        if updated_existing or updated_group:
            return
        _set_into_config(config, key, value)
        return

    def _set_provider_config_mode_value(self, config: Any, value: Any) -> None:
        # Keep the visible schema group and the hidden legacy flat key in sync.
        # AstrBot validates unknown flat keys during plugin load, while older
        # page/API paths still read or write the flat name directly.
        self._set_schema_group_config_value(config, "provider_config_mode", value, create_group=True)
        _set_into_config(config, "provider_config_mode", value)

    def _set_schema_compat_value(self, config: Any, key: str, value: Any) -> None:
        self._set_schema_group_config_value(config, key, value, create_group=True)
        # Write the hidden legacy key explicitly at the top level.  A recursive
        # setter would find the grouped key created above and leave an existing
        # top-level compatibility value stale.
        if isinstance(config, dict):
            config[key] = value
            return
        try:
            config[key] = value
            return
        except (AttributeError, KeyError, TypeError):
            pass
        for attr in ("data", "config"):
            target = getattr(config, attr, None)
            if isinstance(target, dict):
                target[key] = value
                return

    def _set_schema_group_config_value(self, config: Any, key: str, value: Any, *, create_group: bool = False) -> bool:
        group_key = self._schema_group_for_key(key)
        if not group_key:
            return False

        def set_in_group(target: dict[str, Any]) -> bool:
            group = target.get(group_key)
            if isinstance(group, dict):
                group[key] = value
                return True
            if create_group:
                target[group_key] = {key: value}
                return True
            return False

        if isinstance(config, dict) and set_in_group(config):
            return True
        for attr in ("data", "config"):
            target = getattr(config, attr, None)
            if isinstance(target, dict) and set_in_group(target):
                return True
        return False

    def _config_overlay(self, overrides: dict[str, Any]) -> Any:
        base = getattr(self.plugin, "config", {}) or {}

        class _Overlay:
            def get(self, item: str, default: Any = None) -> Any:
                if item in overrides:
                    return overrides[item]
                return _flat_get(base, item, getattr(base, item, default))

        return _Overlay()

    def _config_get_raw(self, key: str, default: Any = None) -> Any:
        # An explicitly cleared value is still a value.  Treating ``""`` as
        # missing makes an old flat compatibility key (or a stale runtime
        # attribute) reappear after the user clears a grouped Provider field.
        config = getattr(self.plugin, "config", None)
        value = _flat_get(config, key, _MISSING)
        if value is not _MISSING and value is not None:
            return value
        data = getattr(config, "data", None)
        if isinstance(data, dict):
            value = _flat_get(data, key, _MISSING)
            if value is not _MISSING and value is not None:
                return value
        raw = getattr(config, "config", None)
        if isinstance(raw, dict):
            value = _flat_get(raw, key, _MISSING)
            if value is not _MISSING and value is not None:
                return value
        try:
            return getattr(config, key, default)
        except Exception:
            return default

    def _config_get(self, key: str) -> str:
        value = self._config_get_raw(key, "")
        if isinstance(value, (list, dict)):
            try:
                return json.dumps(value, ensure_ascii=False)
            except Exception:
                return str(value)
        return str(value or "")

    def _private_alias_config_text(self, key: str) -> str:
        raw = self._config_get(key)
        if raw:
            return raw
        mapping = getattr(self.plugin, key, None)
        if not isinstance(mapping, dict):
            return ""
        lines: list[str] = []
        for alias, target in mapping.items():
            left = str(alias or "").strip()
            right = str(target or "").strip()
            if left and right:
                lines.append(f"{left}={right}")
        return "\n".join(lines)

    async def _save_config_if_possible(self) -> bool:
        config = getattr(self.plugin, "config", None)
        for method_name in ("save_config", "save", "save_conf"):
            save = getattr(config, method_name, None)
            if callable(save):
                try:
                    _ensure_config_parent_dir(config, logger=logger)
                    result = save()
                    if asyncio.iscoroutine(result) or hasattr(result, "__await__"):
                        result = await result
                    # Config adapters conventionally return None on success,
                    # but some expose an explicit False failure result.
                    if result is False:
                        logger.warning("配置保存失败(%s): 保存方法返回 False", method_name)
                        return False
                    return True
                except TypeError:
                    continue
                except FileNotFoundError as exc:
                    if _ensure_config_parent_dir(config, error=exc, logger=logger):
                        try:
                            result = save()
                            if asyncio.iscoroutine(result) or hasattr(result, "__await__"):
                                result = await result
                            if result is False:
                                logger.warning("配置保存重试失败(%s): 保存方法返回 False", method_name)
                                return False
                            return True
                        except Exception as retry_exc:
                            logger.warning("配置保存重试失败(%s): %s", method_name, self._single_line(retry_exc, 160))
                            return False
                    logger.warning("配置保存失败(%s): %s", method_name, self._single_line(exc, 160))
                    return False
                except Exception as exc:
                    logger.warning("配置保存失败(%s): %s", method_name, self._single_line(exc, 160))
                    return False
        logger.warning("当前配置对象没有可用保存方法,本次改动可能只在运行态生效")
        return False

    def _can_save_config(self) -> bool:
        config = getattr(self.plugin, "config", None)
        return any(callable(getattr(config, method_name, None)) for method_name in ("save_config", "save", "save_conf"))

    def _allowed_provider_keys(self) -> set[str]:
        keys = {
            "FAST_RESPONSE_PROVIDER_ID",
            "COMPLEX_REASONING_PROVIDER_ID",
            "CREATIVE_MODEL_PROVIDER_ID",
            "LLM_PROVIDER_ID",
            "MAI_STYLE_PROVIDER_ID",
            "DAILY_PLAN_PROVIDER_ID",
            "DETAIL_ENHANCEMENT_PROVIDER_ID",
            "DREAM_DIARY_PROVIDER_ID",
            "CREATIVE_PROVIDER_ID",
            "CREATIVE_OUTLINE_PROVIDER_ID",
            "CREATIVE_REVIEW_PROVIDER_ID",
            "VOICE_PROMPT_PROVIDER_ID",
            "tts_conversion_provider_id",
            "PHOTO_PROMPT_PROVIDER_ID",
            "NARRATION_PROVIDER_ID",
            "HISTORY_SUMMARY_PROVIDER_ID",
            "RESPONSE_REVIEW_PROVIDER_ID",
            "SMART_SILENCE_PROVIDER_ID",
            "PROACTIVE_PERSONA_JUDGE_PROVIDER_ID",
            "TROUBLESHOOTING_PROVIDER_ID",
            "DAILY_REVIEW_PROVIDER_ID",
            "SMART_MESSAGE_DEBOUNCE_PROVIDER_ID",
            "REST_WAKEUP_PROVIDER_ID",
            "RELATIONSHIP_ANALYSIS_PROVIDER_ID",
            "COMPANION_MEMORY_PROVIDER_ID",
            "DIALOGUE_EPISODE_PROVIDER_ID",
            "GROUP_INTERJECT_PROVIDER_ID",
            "GROUP_EPISODE_PROVIDER_ID",
            "GROUP_SLANG_PROVIDER_ID",
            "GROUP_FOLLOWUP_JUDGE_PROVIDER_ID",
            "FORWARD_MESSAGE_PROVIDER_ID",
            "PLUGIN_VISION_PROVIDER_ID",
            "READING_ARCHIVE_VISION_PROVIDER_ID",
            "NEWS_PROVIDER_ID",
            "WEB_EXPLORATION_PROVIDER_ID",
            "EMOTION_JUDGEMENT_PROVIDER_ID",
        }
        keys.update(self._schema_provider_keys(public_only=True))
        return keys


    def _normalize_schema_setting_value(self, value: Any, schema_item: dict[str, Any]) -> Any:
        item_type = str(schema_item.get("type") or "").lower()
        default = schema_item.get("default")
        slider = schema_item.get("slider") if isinstance(schema_item.get("slider"), dict) else {}

        def clamp_number(raw: float) -> float:
            if "min" in slider:
                try:
                    raw = max(float(slider.get("min")), raw)
                except (TypeError, ValueError):
                    pass
            if "max" in slider:
                try:
                    raw = min(float(slider.get("max")), raw)
                except (TypeError, ValueError):
                    pass
            return raw

        if item_type == "bool":
            return self._normalize_bool_value(value)
        if item_type == "int":
            try:
                return int(round(clamp_number(float(value))))
            except (TypeError, ValueError):
                try:
                    return int(default)
                except (TypeError, ValueError):
                    return 0
        if item_type == "float":
            try:
                return clamp_number(float(value))
            except (TypeError, ValueError):
                try:
                    return float(default)
                except (TypeError, ValueError):
                    return 0.0
        if item_type == "list":
            if isinstance(value, list):
                return [self._single_line(item, 160) for item in value if self._single_line(item, 160)]
            text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
            parts = re.split(r"[\n,，、\s]+", text)
            return [self._single_line(item, 160) for item in parts if self._single_line(item, 160)]
        limit = 4000 if item_type == "text" else 1000
        return str(value if value is not None else default or "").strip()[:limit]

    @staticmethod
    def _normalize_fractional_percent_value(value: Any, default: float = 0.0) -> float:
        try:
            raw = float(value)
        except (TypeError, ValueError):
            raw = default
        if raw > 1.0:
            raw /= 100.0
        return max(0.0, min(1.0, raw))

    @staticmethod
    def _normalize_multiline_source_config(value: Any, *, limit: int = 4000) -> str:
        text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        if text and "\n" not in text:
            markers = list(re.finditer(r"(?:^|\s+)(#?\s*[^|\n]+?)\|(?=(?:https?://|bilibili:|bvid:))", text, flags=re.I))
            if len(markers) > 1:
                recovered: list[str] = []
                for index, match in enumerate(markers):
                    start = match.end()
                    end = markers[index + 1].start() if index + 1 < len(markers) else len(text)
                    name = str(match.group(1) or "").strip()
                    target = text[start:end].strip()
                    if name and target:
                        recovered.append(f"{name}|{target}")
                if recovered:
                    text = "\n".join(recovered)
        lines: list[str] = []
        for raw_line in text.split("\n"):
            line = raw_line.strip()
            if line:
                lines.append(line)
        return "\n".join(lines)[:limit].strip()

    def _schema_key_index(self) -> dict[str, Any]:
        cached = self._schema_key_index_cache
        if cached is not None:
            return cached
        index: dict[str, Any] = {
            "all": set(),
            "public": set(),
            "bool": set(),
            "public_bool": set(),
            "provider": set(),
            "public_provider": set(),
            "group": {},
            "item": {},
        }

        def visit(items: dict[str, Any], group_key: str = "") -> None:
            for raw_key, item in items.items():
                if not isinstance(item, dict):
                    continue
                key = str(raw_key)
                item_type = str(item.get("type") or "")
                if item_type == "object" and isinstance(item.get("items"), dict):
                    visit(item["items"], key)
                    continue
                hidden = bool(item.get("invisible"))
                existing_item = index["item"].get(key)
                existing_group = str(index["group"].get(key) or "")
                existing_hidden = bool(existing_item.get("invisible")) if isinstance(existing_item, dict) else True
                prefer_candidate = (
                    existing_item is None
                    or (existing_hidden and not hidden)
                    or (existing_hidden == hidden and bool(group_key) and not existing_group)
                )
                if prefer_candidate:
                    index["group"][key] = group_key
                    index["item"][key] = item
                index["all"].add(key)
                if not hidden:
                    index["public"].add(key)
                if item_type == "bool":
                    index["bool"].add(key)
                    if not hidden:
                        index["public_bool"].add(key)
                if self._schema_item_is_provider(key, item):
                    index["provider"].add(key)
                    if not hidden:
                        index["public_provider"].add(key)

        try:
            raw = json.loads(Path(__file__).with_name("_conf_schema.json").read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                visit(raw)
        except Exception as exc:
            logger.debug("读取配置 schema 索引失败: %s", exc)
        self._schema_key_index_cache = index
        return index

    @staticmethod
    def _schema_item_is_provider(key: str, item: dict[str, Any]) -> bool:
        if item.get("_special") == "select_provider":
            return True
        return key.endswith("PROVIDER_ID") or key.endswith("_provider_id") or key.endswith("provider_id")

    def _schema_setting_keys(self, *, public_only: bool = False) -> set[str]:
        index = self._schema_key_index()
        key_name = "public" if public_only else "all"
        provider_key_name = "public_provider" if public_only else "provider"
        return set(index[key_name]) - set(index[provider_key_name])

    def _schema_provider_keys(self, *, public_only: bool = False) -> set[str]:
        index = self._schema_key_index()
        return set(index["public_provider" if public_only else "provider"])

    def _schema_bool_keys(self) -> set[str]:
        return set(self._schema_key_index()["bool"])

    def _schema_group_for_key(self, key: str) -> str:
        group_map = self._schema_key_index().get("group")
        return str(group_map.get(key, "") if isinstance(group_map, dict) else "")

    def _schema_item_for_key(self, key: str) -> dict[str, Any]:
        item_map = self._schema_key_index().get("item")
        item = item_map.get(key) if isinstance(item_map, dict) else None
        return item if isinstance(item, dict) else {}








    @staticmethod
    def _merge_activity_item(target: dict[str, Any], source: dict[str, Any]) -> None:
        def as_float(value: Any, default: float = 0.0) -> float:
            try:
                return float(value)
            except (TypeError, ValueError):
                return default

        def as_int(value: Any, default: int = 0) -> int:
            try:
                return int(value)
            except (TypeError, ValueError):
                return default

        target["total_events"] = as_int(target.get("total_events")) + as_int(source.get("total_events"))
        if source.get("display_name") and not target.get("display_name"):
            target["display_name"] = source.get("display_name")
        if source.get("live_username") and not target.get("live_username"):
            target["live_username"] = source.get("live_username")
        target["first_seen"] = min(as_float(target.get("first_seen"), time.time()), as_float(source.get("first_seen"), time.time()))
        target["last_seen"] = max(as_float(target.get("last_seen")), as_float(source.get("last_seen")))
        counts = target.setdefault("event_counts", {})
        if isinstance(counts, dict) and isinstance(source.get("event_counts"), dict):
            for key, value in source["event_counts"].items():
                counts[key] = as_int(counts.get(key)) + as_int(value)
        for field, limit in (("recent_events", 12), ("recent_danmaku", 8)):
            rows = []
            for row in [*(target.get(field) if isinstance(target.get(field), list) else []), *(source.get(field) if isinstance(source.get(field), list) else [])]:
                if isinstance(row, dict):
                    rows.append(row)
            rows.sort(key=lambda row: as_float(row.get("ts")), reverse=True)
            target[field] = rows[:limit]


    def _screen_companion_available(self) -> bool:
        getter = getattr(self.plugin, "_get_screen_companion_plugin", None)
        if callable(getter):
            try:
                return getter() is not None
            except Exception:
                return False
        return False












    # ============================================================
    # Creative Project Management Endpoints
    # ============================================================

    def _segment_from_key(self, key: str, plan: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
        keyed = re.fullmatch(r"(\d{4}-\d{2}-\d{2}):(\d+):(\d{1,2}:\d{2})", key)
        if keyed:
            index = int(keyed.group(2))
            key_start_text = keyed.group(3)
            start = self.plugin._parse_hhmm_to_minutes(key_start_text)
            items = []
            if isinstance(plan, dict):
                if isinstance(plan.get("items"), list):
                    items = plan.get("items") or []
                elif isinstance(plan.get("schedule"), list):
                    items = plan.get("schedule") or []
            end = None
            current_item = items[index] if isinstance(items, list) and index < len(items) and isinstance(items[index], dict) else None
            if isinstance(current_item, dict) and self._single_line(current_item.get("time"), 8) == key_start_text:
                starts = self.plugin._normalized_plan_item_starts(items)
                if index < len(starts) and starts[index] is not None:
                    start = starts[index]
            else:
                current_item = None
            if isinstance(current_item, dict):
                end = self.plugin._parse_hhmm_to_minutes(current_item.get("end"))
                if start is not None and end is not None and end <= start:
                    end += 24 * 60
            if isinstance(items, list):
                for next_item in items[index + 1:]:
                    if end is not None:
                        break
                    if isinstance(next_item, dict):
                        end = self.plugin._parse_hhmm_to_minutes(next_item.get("time"))
                        if end is not None:
                            break
            if start is not None:
                if end is None:
                    end = self.plugin._plan_item_end_minutes(start, current_item)
                return {
                    "window": f"{self.plugin._minutes_to_hhmm(start)}-{self.plugin._minutes_to_hhmm(end)}",
                    "start": start,
                    "end": end,
                    "index": index,
                    "item": current_item or {},
                }

        inferred = self._segment_from_story_windows(snapshot)
        if inferred:
            return inferred

        match = re.search(r"(?:^|[:|_])(\d{1,4})[-_](\d{1,4})(?:$|[:|_])", key)
        if not match:
            return {"window": key, "start": 99999}
        start = int(match.group(1))
        end = int(match.group(2))
        if not (0 <= start < 24 * 60 and 0 < end <= 28 * 60):
            return {"window": key, "start": 99999}
        return {"window": f"{self.plugin._minutes_to_hhmm(start)}-{self.plugin._minutes_to_hhmm(end)}", "start": start}

    def _memory_plugin_token_usage_raw(self) -> dict[str, Any]:
        getter = getattr(self.plugin, "_memory_companion_token_usage_summary", None)
        if not callable(getter):
            return {"available": False, "display_name": "我会牢牢记住你", "reason": "陪伴插件当前未接入记忆插件桥"}
        try:
            usage = getter()
        except Exception as exc:
            return {"available": False, "display_name": "我会牢牢记住你", "reason": self._single_line(exc, 160)}
        if not isinstance(usage, dict):
            return {"available": False, "display_name": "我会牢牢记住你", "reason": "记忆插件返回的 Token 统计格式无效"}
        usage.setdefault("available", True)
        usage.setdefault("display_name", "我会牢牢记住你")
        usage.setdefault("counted_in_private_companion_budget", False)
        return usage

    def _together_plugin_token_usage_raw(self) -> dict[str, Any]:
        target_plugin_name = "astrbot_plugin_together_companion"

        def static_namespace(module: Any) -> dict[str, Any]:
            if module is None:
                return {}
            try:
                namespace = object.__getattribute__(module, "__dict__")
            except (AttributeError, TypeError):
                return {}
            return namespace if isinstance(namespace, dict) else {}

        def is_target_module_name(value: Any) -> bool:
            name = str(value or "").strip()
            target_main = f"{target_plugin_name}.main"
            return name == target_main or name.endswith(f".{target_main}")

        modules: list[Any] = []
        seen_module_ids: set[int] = set()

        def append_module(module: Any) -> None:
            if module is None or id(module) in seen_module_ids:
                return
            seen_module_ids.add(id(module))
            modules.append(module)

        append_module(sys.modules.get("data.plugins.astrbot_plugin_together_companion.main"))
        append_module(sys.modules.get("astrbot_plugin_together_companion.main"))
        for loaded_name, module in tuple(sys.modules.items()):
            namespace = static_namespace(module)
            module_name = namespace.get("__name__", loaded_name)
            if (
                namespace.get("PLUGIN_NAME") == target_plugin_name
                or is_target_module_name(loaded_name)
                or is_target_module_name(module_name)
            ):
                append_module(module)
        for module in modules:
            # transformers 等懒加载模块会在 getattr() 时导入 torch/torchvision。
            # 集成发现只读取模块已注册的静态符号，不能触发任意第三方模块加载。
            getter = static_namespace(module).get("get_together_companion_bridge")
            if not callable(getter):
                continue
            try:
                bridge = getter()
                summary_getter = getattr(bridge, "get_token_usage_summary", None) if bridge is not None else None
                usage = summary_getter() if callable(summary_getter) else None
            except Exception as exc:
                return {
                    "available": False,
                    "installed": True,
                    "display_name": "我会和你在一起",
                    "reason": self._single_line(exc, 160),
                }
            if isinstance(usage, dict):
                usage.setdefault("available", True)
                usage.setdefault("installed", True)
                usage.setdefault("display_name", "我会和你在一起")
                usage.setdefault("counted_in_private_companion_budget", False)
                return usage
        return {
            "available": False,
            "installed": False,
            "display_name": "我会和你在一起",
            "reason": "未检测到运行中的一起插件",
        }

    def _safe_together_plugin_token_usage_raw(self) -> dict[str, Any]:
        try:
            return self._together_plugin_token_usage_raw()
        except Exception as exc:
            reason = self._single_line(exc, 160) or "联动状态读取失败"
            warning_key = f"{type(exc).__name__}:{reason}"
            if getattr(self, "_together_token_usage_warning_key", "") != warning_key:
                self._together_token_usage_warning_key = warning_key
                logger.warning(
                    "一起插件 Token 统计暂不可用，已跳过该可选来源: %s",
                    reason,
                )
            return {
                "available": False,
                "installed": False,
                "display_name": "我会和你在一起",
                "reason": "一起插件统计暂不可用，不影响陪伴面板其他功能",
            }

    def _balance_status_payload(self, state: Any = None) -> dict[str, Any]:
        raw = state if isinstance(state, dict) else {}

        def optional_number(value: Any) -> float | None:
            try:
                number = float(value)
            except (TypeError, ValueError):
                return None
            return number if math.isfinite(number) else None

        amount = optional_number(raw.get("amount"))
        total = optional_number(raw.get("total"))
        remaining_percent = optional_number(raw.get("remaining_percent"))
        tier = self._single_line(raw.get("tier"), 20) or "unknown"
        if tier not in {"normal", "low", "critical", "unknown"}:
            tier = "unknown"
        enabled = bool(getattr(self.plugin, "enable_balance_awareness", False))
        manual_configured = bool(str(getattr(self.plugin, "balance_api_url", "") or "").strip())
        auto_available_getter = getattr(self.plugin, "_balance_auto_discovery_available", None)
        try:
            auto_discovery_available = bool(auto_available_getter()) if callable(auto_available_getter) else False
        except Exception:
            auto_discovery_available = False
        configured = manual_configured or auto_discovery_available
        query_mode = self._single_line(raw.get("query_mode"), 20)
        if query_mode not in {"manual", "auto"}:
            query_mode = "manual" if manual_configured else "auto"
        return {
            "enabled": enabled,
            "configured": configured,
            "manual_configured": manual_configured,
            "auto_discovery_available": auto_discovery_available,
            "query_mode": query_mode,
            "source_label": self._single_line(raw.get("auto_source_id"), 80),
            "available": bool(enabled and configured and amount is not None and self._float(raw.get("last_success_at")) > 0),
            "amount": amount,
            "total": total,
            "remaining_percent": remaining_percent,
            "currency_label": self._single_line(
                raw.get("currency_label") or getattr(self.plugin, "balance_currency_label", "元"),
                20,
            ) or "元",
            "tier": tier,
            "last_check_at": self._float(raw.get("last_check_at")),
            "last_success_at": self._float(raw.get("last_success_at")),
            "next_check_at": self._float(raw.get("next_check_at")),
            "last_prompted_at": self._float(raw.get("last_prompted_at")),
            "consecutive_failures": self._int(raw.get("consecutive_failures")),
            "last_error": self._single_line(raw.get("last_error"), 180),
        }


    def _token_external_payload(self, usage: Any) -> dict[str, Any]:
        if not isinstance(usage, dict):
            usage = {}
        by_day = self._token_series_map(usage.get("by_day"), limit=30)
        by_day_provider_raw = usage.get("by_day_provider") if isinstance(usage.get("by_day_provider"), dict) else {}
        by_day_task_raw = usage.get("by_day_task") if isinstance(usage.get("by_day_task"), dict) else {}
        by_day_session_raw = usage.get("by_day_session") if isinstance(usage.get("by_day_session"), dict) else {}
        by_day_detail = []
        for item in by_day:
            day_key = item.get("key", "")
            by_day_detail.append(
                {
                    **item,
                    "providers": self._token_ranked_map(by_day_provider_raw.get(day_key))[:5],
                    "tasks": self._token_ranked_map(by_day_task_raw.get(day_key))[:6],
                    "sessions": self._token_ranked_map(by_day_session_raw.get(day_key))[:8],
                }
            )
        recent = []
        recent_raw = usage.get("recent")
        if isinstance(recent_raw, list):
            for item in recent_raw[-80:][::-1]:
                if not isinstance(item, dict):
                    continue
                recent.append(
                    {
                        "time": self._single_line(item.get("time"), 24),
                        "ts": self._float(item.get("ts")),
                        "provider": self._single_line(item.get("provider"), 80),
                        "task": self._single_line(item.get("task"), 40),
                        "session": self._single_line(item.get("session"), 160),
                        "sender": self._single_line(item.get("sender"), 80),
                        "message_type": self._single_line(item.get("message_type"), 20),
                        "success": bool(item.get("success", True)),
                        "prompt_tokens": self._int(item.get("prompt_tokens")),
                        "completion_tokens": self._int(item.get("completion_tokens")),
                        "reasoning_tokens": self._int(item.get("reasoning_tokens")),
                        "total_tokens": self._int(item.get("total_tokens")),
                        "reported_tokens": self._int(item.get("reported_tokens")),
                        "estimated_tokens": self._int(item.get("estimated_tokens")),
                        "usage_source": self._single_line(item.get("usage_source"), 20),
                        "cached_tokens": self._int(item.get("cached_tokens")),
                        "cache_read_tokens": self._int(item.get("cache_read_tokens")),
                        "cache_write_tokens": self._int(item.get("cache_write_tokens")),
                        "estimated": bool(item.get("estimated", False)),
                        "elapsed_ms": self._int(item.get("elapsed_ms")),
                        "prompt_chars": self._int(item.get("prompt_chars")),
                        "completion_chars": self._int(item.get("completion_chars")),
                        "error": self._single_line(item.get("error"), 160),
                        "external": True,
                    }
                )
        return {
            "updated_at": self._single_line(usage.get("updated_at"), 24),
            "totals": self._token_bucket(usage.get("totals")),
            "by_provider": self._token_ranked_map(usage.get("by_provider")),
            "by_task": self._token_ranked_map(usage.get("by_task")),
            "by_session": self._token_ranked_map(usage.get("by_session")),
            "by_day": by_day,
            "by_day_detail": by_day_detail,
            "by_hour": self._token_series_map(usage.get("by_hour"), limit=48),
            "recent": recent,
        }

    def _token_memory_plugin_payload(self, usage: Any) -> dict[str, Any]:
        if not isinstance(usage, dict):
            usage = {"available": False}
        available = bool(usage.get("available", True))
        by_day = self._token_series_map(usage.get("by_day"), limit=30)
        by_day_provider_raw = usage.get("by_day_provider") if isinstance(usage.get("by_day_provider"), dict) else {}
        by_day_task_raw = usage.get("by_day_task") if isinstance(usage.get("by_day_task"), dict) else {}
        by_day_detail = []
        for item in by_day:
            day_key = item.get("key", "")
            by_day_detail.append(
                {
                    **item,
                    "providers": self._token_ranked_map(by_day_provider_raw.get(day_key))[:5],
                    "tasks": self._token_ranked_map(by_day_task_raw.get(day_key))[:8],
                }
            )
        recent = []
        recent_raw = usage.get("recent")
        if isinstance(recent_raw, list):
            for item in recent_raw[-80:][::-1]:
                if not isinstance(item, dict):
                    continue
                recent_total_tokens = self._int(item.get("total_tokens"))
                recent_estimated_tokens = self._int(item.get("estimated_tokens"))
                recent_reported_tokens = self._int(item.get("reported_tokens"), -1)
                if recent_reported_tokens < 0:
                    if recent_estimated_tokens <= 0 and bool(item.get("estimated", False)):
                        recent_estimated_tokens = recent_total_tokens
                    recent_reported_tokens = max(0, recent_total_tokens - recent_estimated_tokens)
                recent.append(
                    {
                        "time": self._single_line(item.get("time"), 24),
                        "ts": self._float(item.get("ts")),
                        "provider": self._single_line(item.get("provider"), 80),
                        "task": self._single_line(item.get("task"), 60),
                        "success": bool(item.get("success", True)),
                        "prompt_tokens": self._int(item.get("prompt_tokens")),
                        "completion_tokens": self._int(item.get("completion_tokens")),
                        "reasoning_tokens": self._int(item.get("reasoning_tokens")),
                        "total_tokens": recent_total_tokens,
                        "reported_tokens": recent_reported_tokens,
                        "estimated_tokens": recent_estimated_tokens,
                        "usage_source": self._single_line(item.get("usage_source"), 20),
                        "cached_tokens": self._int(item.get("cached_tokens")),
                        "cache_read_tokens": self._int(item.get("cache_read_tokens")),
                        "cache_write_tokens": self._int(item.get("cache_write_tokens")),
                        "estimated": bool(item.get("estimated", False)),
                        "elapsed_ms": self._int(item.get("elapsed_ms")),
                        "prompt_chars": self._int(item.get("prompt_chars")),
                        "completion_chars": self._int(item.get("completion_chars")),
                        "error": self._single_line(item.get("error"), 160),
                    }
                )
        return {
            "available": available,
            "installed": bool(usage.get("installed", available)),
            "display_name": self._single_line(usage.get("display_name") or "我会牢牢记住你", 80),
            "plugin_name": self._single_line(usage.get("plugin_name") or "astrbot_plugin_memory_companion", 80),
            "reason": self._single_line(usage.get("reason"), 160),
            "note": self._single_line(
                usage.get("note") or "仅展示记忆插件自身模型调用；已确认 Token 来自 Provider 用量，估算 Token 只用于标记无用量返回或失败请求，不计入陪伴插件每日 Token 限额。",
                300,
            ),
            "counted_in_private_companion_budget": bool(usage.get("counted_in_private_companion_budget", False)),
            "updated_at": self._single_line(usage.get("updated_at"), 24),
            "totals": self._token_bucket(usage.get("totals")),
            "by_provider": self._token_ranked_map(usage.get("by_provider")),
            "by_task": self._token_ranked_map(usage.get("by_task")),
            "by_day": by_day,
            "by_day_detail": by_day_detail,
            "by_hour": self._token_series_map(usage.get("by_hour"), limit=48),
            "recent": recent,
        }

    @classmethod
    def _token_bucket(cls, value: Any) -> dict[str, Any]:
        bucket = value if isinstance(value, dict) else {}
        calls = cls._int(bucket.get("calls"))
        elapsed = cls._int(bucket.get("elapsed_ms"))
        total_tokens = cls._int(bucket.get("total_tokens"))
        estimated_tokens = cls._int(bucket.get("estimated_tokens"))
        reported_tokens = cls._int(bucket.get("reported_tokens"), -1)
        if reported_tokens < 0:
            reported_tokens = max(0, total_tokens - estimated_tokens)
        cached_tokens = cls._int(bucket.get("cached_tokens"))
        cache_read_tokens = cls._int(bucket.get("cache_read_tokens"))
        cache_write_tokens = cls._int(bucket.get("cache_write_tokens"))
        reasoning_tokens = cls._int(bucket.get("reasoning_tokens"))
        return {
            "calls": calls,
            "success": cls._int(bucket.get("success")),
            "errors": cls._int(bucket.get("errors")),
            "prompt_tokens": cls._int(bucket.get("prompt_tokens")),
            "completion_tokens": cls._int(bucket.get("completion_tokens")),
            "reasoning_tokens": reasoning_tokens,
            "total_tokens": total_tokens,
            "reported_tokens": reported_tokens,
            "cached_tokens": cached_tokens,
            "cache_read_tokens": cache_read_tokens,
            "cache_write_tokens": cache_write_tokens,
            "cached_ratio": round(cached_tokens / total_tokens, 4) if total_tokens > 0 else 0,
            "estimated_tokens": estimated_tokens,
            "estimated_ratio": round(estimated_tokens / total_tokens, 4) if total_tokens > 0 else 0,
            "reported_ratio": round(reported_tokens / total_tokens, 4) if total_tokens > 0 else 0,
            "estimated_calls": cls._int(bucket.get("estimated_calls")),
            "avg_tokens": round(total_tokens / calls, 1) if calls > 0 else 0,
            "avg_reported_tokens": round(reported_tokens / calls, 1) if calls > 0 else 0,
            "avg_latency_ms": round(elapsed / calls, 1) if calls > 0 else 0,
            "last_ts": cls._float(bucket.get("last_ts")),
        }

    @classmethod
    def _token_ranked_map(cls, value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, dict):
            return []
        rows = []
        for key, bucket in value.items():
            item = cls._token_bucket(bucket)
            item["key"] = cls._single_line(key, 120)
            rows.append(item)
        rows.sort(key=lambda item: item.get("total_tokens", 0), reverse=True)
        return rows

    @classmethod
    def _token_series_map(cls, value: Any, *, limit: int) -> list[dict[str, Any]]:
        if not isinstance(value, dict):
            return []
        rows = []
        for key, bucket in value.items():
            item = cls._token_bucket(bucket)
            item["key"] = cls._single_line(key, 32)
            rows.append(item)
        rows.sort(key=lambda item: item.get("key", ""))
        return rows[-limit:]

    @staticmethod
    def _int(value: Any, default: int = 0, minimum: int | None = None, maximum: int | None = None) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = default
        if minimum is not None:
            parsed = max(minimum, parsed)
        if maximum is not None:
            parsed = min(maximum, parsed)
        return parsed

    @staticmethod
    def _float(value: Any, default: float = 0.0, minimum: float | None = None, maximum: float | None = None) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            parsed = default
        if minimum is not None:
            parsed = max(minimum, parsed)
        if maximum is not None:
            parsed = min(maximum, parsed)
        return parsed

    @staticmethod
    def _limited_state_variables(value: Any) -> list[dict[str, str]]:
        if not isinstance(value, list):
            return []
        items: list[dict[str, str]] = []
        for item in value[:8]:
            if not isinstance(item, dict):
                continue
            items.append(
                {
                    "name": PrivateCompanionPageApi._single_line(item.get("name") or item.get("key"), 40),
                    "value": PrivateCompanionPageApi._single_line(item.get("value"), 80),
                    "note": PrivateCompanionPageApi._single_line(item.get("note"), 100),
                }
            )
        return items

    @staticmethod
    def _limited_interaction_updates(value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        items: list[dict[str, Any]] = []
        for item in value[-8:]:
            if not isinstance(item, dict):
                continue
            updates = item.get("state_updates")
            items.append(
                {
                    "at": PrivateCompanionPageApi._single_line(item.get("at"), 12),
                    "source": PrivateCompanionPageApi._single_line(item.get("source"), 24),
                    "reaction": PrivateCompanionPageApi._single_line(item.get("reaction"), 140),
                    "state_updates": [
                        PrivateCompanionPageApi._single_line(update, 80)
                        for update in updates[:6]
                        if PrivateCompanionPageApi._single_line(update, 80)
                    ]
                    if isinstance(updates, list)
                    else [],
                }
            )
        return items

    def _timeline_story_items(
        self,
        value: Any,
        limit: int,
        plan_date: str,
        *,
        parent_start: int | None = None,
        parent_end: int | None = None,
    ) -> list[dict[str, Any]]:
        items = self._limited_story_items(
            value,
            limit,
            parent_start=parent_start,
            parent_end=parent_end,
        )
        for item in items:
            start, end = self.plugin._parse_window_minutes(str(item.get("window") or ""))
            if start is not None and end is not None:
                duration = end - start
                if duration <= 0:
                    duration += 24 * 60
                axis_start = self._story_item_axis_start(
                    start,
                    parent_start=parent_start,
                    parent_end=parent_end,
                )
                item["clock_status"] = self.plugin._schedule_window_runtime_status(
                    axis_start,
                    axis_start + duration,
                    plan_date=plan_date,
                )
            # Story/detail entries are projections.  Their clock window only
            # describes when a generated scene could occur; it is not an
            # execution observation.  Never turn them into ``active`` or
            # ``completed`` solely because the wall clock crossed the window.
            # Preserve explicit editorial transitions (cancelled/changed/
            # deferred), while evidence-backed canonical states may still be
            # surfaced when a producer supplied the full evidence contract.
            explicit = self._single_line(item.get("lifecycle_status"), 24).lower()
            if explicit in {"cancelled", "canceled", "changed", "deferred", "postponed"}:
                item["lifecycle"] = "cancelled" if explicit == "canceled" else (
                    "deferred" if explicit == "postponed" else explicit
                )
                continue
            evidence_kind = self._single_line(item.get("evidence_kind"), 48).lower()
            eligibility = self._single_line(item.get("fact_eligibility"), 48).lower()
            status = self._single_line(item.get("status"), 32).lower()
            if (
                evidence_kind in {"interaction", "tool_action", "external_record"}
                and eligibility in {"current_observed", "history_observed"}
                and status in {"active", "completed", "partially_completed"}
            ):
                item["lifecycle"] = status
            else:
                item["lifecycle"] = "planned"
        return items

    @staticmethod
    def _limited_story_items(
        value: Any,
        limit: int,
        *,
        parent_start: int | None = None,
        parent_end: int | None = None,
    ) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        items: list[dict[str, Any]] = []
        indexed_items = [
            (
                PrivateCompanionPageApi._story_item_axis_start(
                    PrivateCompanionPageApi._story_item_start_minutes(item),
                    parent_start=parent_start,
                    parent_end=parent_end,
                ),
                index,
                item,
            )
            for index, item in enumerate(value)
            if isinstance(item, dict)
        ]
        indexed_items.sort(key=lambda row: (row[0], row[1]))
        for _, _, item in indexed_items[:limit]:
            if not isinstance(item, dict):
                continue
            evidence_kind = PrivateCompanionPageApi._single_line(item.get("evidence_kind"), 48).lower()
            if evidence_kind not in {"interaction", "tool_action", "external_record"}:
                evidence_kind = ""
            fact_eligibility = PrivateCompanionPageApi._single_line(item.get("fact_eligibility"), 48).lower()
            if fact_eligibility not in {"current_observed", "history_observed"}:
                fact_eligibility = ""
            status = PrivateCompanionPageApi._single_line(item.get("status"), 32).lower()
            if status not in {"planned", "unknown", "active", "completed", "partially_completed"}:
                status = ""
            items.append(
                {
                    "window": PrivateCompanionPageApi._single_line(item.get("window") or item.get("time"), 24),
                    "text": PrivateCompanionPageApi._single_line(
                        item.get("event")
                        or item.get("topic")
                        or item.get("summary")
                        or item.get("text")
                        or item.get("content"),
                        160,
                    ),
                    "mood": PrivateCompanionPageApi._single_line(item.get("mood"), 24),
                    "action": PrivateCompanionPageApi._single_line(item.get("action"), 24),
                    "reason": PrivateCompanionPageApi._single_line(item.get("reason"), 32),
                    "lifecycle_status": PrivateCompanionPageApi._single_line(item.get("lifecycle_status"), 20),
                    "evidence_kind": evidence_kind,
                    "fact_eligibility": fact_eligibility,
                    "status": status,
                    "basis": [
                        PrivateCompanionPageApi._single_line(value, 24)
                        for value in (item.get("basis") or [])[:3]
                        if PrivateCompanionPageApi._single_line(value, 24)
                    ] if isinstance(item.get("basis"), list) else [],
                    "confidence": min(1.0, PrivateCompanionPageApi._float(item.get("confidence"), 0.72)),
                }
            )
        return items

    @staticmethod
    def _story_item_start_minutes(item: Any) -> int:
        if not isinstance(item, dict):
            return 99999
        text = PrivateCompanionPageApi._single_line(item.get("window") or item.get("time"), 32)
        match = re.search(r"(\d{1,2}):(\d{2})", text)
        if not match:
            return 99999
        hour = int(match.group(1))
        minute = int(match.group(2))
        if hour > 23 or minute > 59:
            return 99999
        return hour * 60 + minute

    @staticmethod
    def _limited_adjustments(value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        items: list[dict[str, Any]] = []
        for item in value[-10:]:
            if not isinstance(item, dict):
                continue
            updates = item.get("state_updates")
            items.append(
                {
                    "date": PrivateCompanionPageApi._single_line(item.get("date"), 12),
                    "source": PrivateCompanionPageApi._single_line(item.get("source"), 24),
                    "scope": PrivateCompanionPageApi._single_line(item.get("scope"), 40),
                    "scope_key": PrivateCompanionPageApi._single_line(item.get("scope_key"), 24),
                    "note": PrivateCompanionPageApi._single_line(item.get("note"), 140),
                    "reaction": PrivateCompanionPageApi._single_line(item.get("immediate_reaction"), 140),
                    "state_updates": [
                        PrivateCompanionPageApi._single_line(update, 80)
                        for update in updates[:6]
                        if PrivateCompanionPageApi._single_line(update, 80)
                    ]
                    if isinstance(updates, list)
                    else [],
                }
            )
        return items

    @staticmethod
    def _limited_dream_fragments(value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        items: list[dict[str, Any]] = []
        for raw in value[-18:]:
            if isinstance(raw, dict):
                text = PrivateCompanionPageApi._single_line(
                    raw.get("text") or raw.get("keyword") or raw.get("label"),
                    42,
                )
                if not text:
                    continue
                items.append(
                    {
                        "text": text,
                        "weight": PrivateCompanionPageApi._float(raw.get("effective_weight") or raw.get("weight")),
                        "source": PrivateCompanionPageApi._single_line(raw.get("source"), 24),
                        "created_at": PrivateCompanionPageApi._single_line(raw.get("created_at") or raw.get("created_ts"), 24),
                    }
                )
            else:
                text = PrivateCompanionPageApi._single_line(raw, 42)
                if text:
                    items.append({"text": text, "weight": 1.0, "source": "", "created_at": ""})
        items.sort(key=lambda item: float(item.get("weight") or 0), reverse=True)
        return items[:14]

    @staticmethod
    def _limited_list(value: Any, limit: int) -> list[Any]:
        return list(value[:limit]) if isinstance(value, list) else []


    @staticmethod
    def _sqlite_like_pattern(token: str) -> str:
        escaped = str(token).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return f"%{escaped}%"

    @staticmethod
    def _json_dict(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        if not isinstance(value, str) or not value.strip():
            return {}
        try:
            parsed = json.loads(value)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _json_list(value: Any) -> list[Any]:
        if isinstance(value, list):
            return value
        if not isinstance(value, str) or not value.strip():
            return []
        try:
            parsed = json.loads(value)
        except Exception:
            return []
        return parsed if isinstance(parsed, list) else []

    @staticmethod
    def _coerce_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _query_livingmemory_for_tokens(self, db_path: Path, token_bundle: dict[str, list[str]], limit: int) -> list[dict[str, Any]]:
        tokens = token_bundle.get("primary_tokens") or token_bundle.get("tokens", [])
        if not tokens:
            return []
        db_uri = f"file:{db_path.as_posix()}?mode=ro"
        conn = sqlite3.connect(db_uri, uri=True, timeout=2.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA query_only=ON")
            conn.execute("PRAGMA busy_timeout=1500")
            like_tokens = [self._sqlite_like_pattern(token) for token in tokens]
            table_limit = max(80, limit * 8)
            items: list[dict[str, Any]] = []

            def where_for(columns: list[str]) -> tuple[str, list[str]]:
                parts: list[str] = []
                params: list[str] = []
                for pattern in like_tokens:
                    sub = []
                    for column in columns:
                        sub.append(f"{column} LIKE ? ESCAPE '\\'")
                        params.append(pattern)
                    parts.append("(" + " OR ".join(sub) + ")")
                return " OR ".join(parts), params

            if self._sqlite_table_exists(conn, "documents"):
                where, params = where_for(["text", "metadata"])
                for row in conn.execute(
                    f"SELECT id, doc_id, text, metadata, created_at, updated_at FROM documents WHERE {where} ORDER BY id DESC LIMIT ?",
                    [*params, table_limit],
                ).fetchall():
                    item = self._livingmemory_item_from_document(row, token_bundle)
                    if item:
                        items.append(item)

            if self._sqlite_table_exists(conn, "memory_atoms"):
                where, params = where_for(["content", "entities", "metadata"])
                for row in conn.execute(
                    f"""SELECT id, parent_memory_id, atom_type, content, entities, importance, confidence,
                              created_at, last_accessed_at, session_id, persona_id, metadata
                         FROM memory_atoms
                        WHERE (status IS NULL OR status != 'expired') AND ({where})
                        ORDER BY id DESC LIMIT ?""",
                    [*params, table_limit],
                ).fetchall():
                    item = self._livingmemory_item_from_atom(row, token_bundle)
                    if item:
                        items.append(item)

            if self._sqlite_table_exists(conn, "graph_entries"):
                where, params = where_for(["content", "metadata", "session_id"])
                for row in conn.execute(
                    f"""SELECT id, entry_key, source_memory_id, session_id, persona_id, entry_type,
                              relation_type, content, metadata, created_at, updated_at
                         FROM graph_entries
                        WHERE {where}
                        ORDER BY id DESC LIMIT ?""",
                    [*params, table_limit],
                ).fetchall():
                    item = self._livingmemory_item_from_graph_entry(row, token_bundle)
                    if item:
                        items.append(item)

            seen: set[tuple[str, str]] = set()
            unique: list[dict[str, Any]] = []
            for item in sorted(items, key=lambda entry: (entry.get("score") or 0, entry.get("last_access_time") or entry.get("create_time") or 0), reverse=True):
                key = (str(item.get("source") or ""), str(item.get("id") or ""))
                if key in seen:
                    continue
                seen.add(key)
                unique.append(item)
                if len(unique) >= limit:
                    break
            return unique
        finally:
            conn.close()

    @staticmethod
    def _sqlite_table_exists(conn: sqlite3.Connection, table: str) -> bool:
        row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (table,)).fetchone()
        return row is not None

    @staticmethod
    def _query_int(name: str, default: int, minimum: int, maximum: int) -> int:
        raw = request.args.get(name, default)
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = default
        return max(minimum, min(maximum, value))

    @staticmethod
    def _clamp_int(raw: Any, default: int, minimum: int, maximum: int) -> int:
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = default
        return max(minimum, min(maximum, value))

    @staticmethod
    def _normalize_id_list(value: Any) -> list[str]:
        if isinstance(value, str):
            raw_items = value.replace("，", ",").replace("\n", ",").split(",")
        elif isinstance(value, list):
            raw_items = value
        else:
            raw_items = []
        result: list[str] = []
        seen: set[str] = set()
        for item in raw_items:
            text = PrivateCompanionPageApi._single_line(item, 128)
            if not text or text in seen:
                continue
            seen.add(text)
            result.append(text)
        return result

    @classmethod
    def _dedupe_text_list(cls, value: Any, limit: int = 12) -> list[str]:
        items = value if isinstance(value, list) else []
        result: list[str] = []
        seen: set[str] = set()
        for item in items:
            text = cls._single_line(item, 180)
            if not text or text in seen:
                continue
            seen.add(text)
            result.append(text)
            if len(result) >= limit:
                break
        return result

    def _normalize_private_target_id_list(self, value: Any) -> list[str]:
        normalizer = getattr(self.plugin, "_normalize_private_identity_id", None)
        result: list[str] = []
        seen: set[str] = set()
        for item in self._normalize_id_list(value):
            text = normalizer(item) if callable(normalizer) else self._single_line(item, 128)
            if not text or text in seen:
                continue
            seen.add(text)
            result.append(text)
        return result

    @staticmethod
    def _single_line(value: Any, limit: int) -> str:
        text = " ".join(str(value or "").strip().split())
        return text[:limit]

    @classmethod
    def _sanitize_news_text(cls, value: Any, limit: int, *, fallback: str = "") -> str:
        text = cls._single_line(value, limit)
        if text and _text_looks_garbled(text):
            return fallback
        return text

    @staticmethod
    def _multi_line(value: Any, limit: int) -> str:
        text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text[:limit].strip()

    @staticmethod
    def _multi_line_head_tail(value: Any, limit: int) -> str:
        text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        if len(text) <= limit:
            return text.strip()
        marker = "\n\n（中间内容过长，已保留开头和结尾；请优先依据稳定重复信息归纳。）\n\n"
        if limit <= len(marker) + 200:
            return text[:limit].strip()
        head_len = max(200, int((limit - len(marker)) * 0.65))
        tail_len = max(200, limit - len(marker) - head_len)
        return (text[:head_len].strip() + marker + text[-tail_len:].strip())[:limit].strip()

    @staticmethod
    def _ok(data: Any = None) -> dict[str, Any]:
        return {"success": True, "data": data, "ts": int(time.time())}

    @staticmethod
    def _is_http_error_response(value: Any) -> bool:
        return (
            isinstance(value, _PageApiError)
            or (isinstance(value, dict) and value.get("success") is False)
            or (
                isinstance(value, tuple)
                and len(value) == 2
                and isinstance(value[0], dict)
                and isinstance(value[1], int)
            )
        )

    @staticmethod
    def _as_http_response(value: Any) -> Any:
        if isinstance(value, _PageApiError):
            return dict(value), value.http_status
        if isinstance(value, dict) and value.get("success") is False:
            return value, 400
        return value

    @staticmethod
    def _safe_error_message(message: Any) -> str:
        text = PrivateCompanionPageApi._single_line(message, 220)
        if not text:
            return "请求失败"
        lowered = text.lower()
        sensitive_markers = (
            "api_key",
            "apikey",
            "password",
            "authorization",
            "cookie",
            "credential",
            "secret",
            "token",
        )
        if any(marker in lowered for marker in sensitive_markers):
            return "内部操作失败"
        if "\\\\" in text or re.search(r"(?:[A-Za-z]:[\\/]|(?:^|[\\s'\"=])/[^\s]+)", text):
            return "内部操作失败"
        return text

    @staticmethod
    def _error(message: str, *, status_code: int = 400) -> dict[str, Any]:
        return _PageApiError(
            {
                "success": False,
                "error": PrivateCompanionPageApi._safe_error_message(message),
                "ts": int(time.time()),
            },
            status_code,
        )

    @staticmethod
    def _exception_error(message: str = "内部操作失败") -> dict[str, Any]:
        return PrivateCompanionPageApi._error(message, status_code=500)
