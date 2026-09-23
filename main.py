from __future__ import annotations

import asyncio
import base64
from collections.abc import Collection
from contextlib import asynccontextmanager
import contextvars
import functools
import gc
import hashlib
import html
import importlib
import inspect
import json
import math
import os
import random
import re
import shutil
import sqlite3
import stat
import sys
import threading
import time
import unicodedata
import uuid
import zoneinfo
from copy import copy, deepcopy
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
from http.cookies import SimpleCookie
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, Iterable
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlparse, urlunparse
from xml.etree import ElementTree as ET

from astrbot.api import AstrBotConfig
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
try:
    from astrbot.api.message_components import (
        At,
        BaseMessageComponent,
        ComponentType,
        Image,
        Plain,
        Record,
        Reply,
    )
except ImportError:
    from astrbot.api.message_components import At, Image, Plain
    from astrbot.core.message.components import BaseMessageComponent, ComponentType, Record
    try:
        from astrbot.api.message_components import Reply
    except ImportError:
        try:
            from astrbot.core.message.components import Reply
        except ImportError:
            Reply = None
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star, StarTools
from astrbot.core import file_token_service
from astrbot.core.astr_main_agent import MainAgentBuildConfig, build_main_agent
from astrbot.core.agent.message import AssistantMessageSegment, TextPart, UserMessageSegment
from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
from astrbot.core.platform.message_session import MessageSession
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.platform import PlatformStatus
from astrbot.core.platform.platform_metadata import PlatformMetadata
from astrbot.core.star.star_handler import EventType, star_handlers_registry
from astrbot.core.provider.entities import LLMResponse

from .private_scope_isolation import (
    GROUP_SCOPE_MARKERS,
    sanitize_private_request_group_artifacts,
)

try:
    import chinese_calendar as calendar_cn
except Exception:
    calendar_cn = None

try:
    from lunarcalendar import Converter, Solar
except Exception:
    Converter = None
    Solar = None

from .constants import (
    DEFAULT_DAILY_PLAN_ITEMS,
    DEFAULT_HUMANIZED_STATE,
    DEFAULT_NATURAL_LANGUAGE_PHOTO_EXTRA_PROMPT,
    DEFAULT_REPLY_STYLE_PROMPT,
    PAGE_FONT_NAMES,
    PAGE_THEME_NAMES,
    PLUGIN_NAME,
    DATA_VERSION,
    PROACTIVE_ABILITY_REGISTRY,
    VOICE_FALLBACK_TEMPLATES,
    TIMER_TAG_PATTERN,
    SUPPORTED_TIMER_FORMATS,
    _ACTION_TEXT,
    _DATA_STORE_KEYS,
    _DEFAULT_GROUP_TEMPLATE,
    _DEFAULT_USER_TEMPLATE,
    _REASON_TEXT,
    _SIMULATION_FALLBACK_EVENTS,
)
from .dreaming import (
    build_dream_memory_fragments,
    dream_fragment_effective_weight,
    dream_theme_specs,
    extract_weighted_dream_fragments,
    fallback_diary_payload,
    fallback_dream_fragments_for_diary,
    generate_daily_diary,
    generate_enhanced_dream_pick,
    merge_dream_fragment_pool,
    normalize_dream_fragment_item,
    normalize_dream_fragment_pool,
    recent_diary_context,
    recent_diary_tags,
    weighted_unique_fragment_sample,
)
from .helpers import (
    _date_key,
    _flat_get,
    _group_link_message_context,
    _missing_optional_model_dependency,
    _normalize_outbound_punctuation_flow,
    _now_ts,
    _normalize_timezone_name,
    _normalize_timezone_setting,
    _path_text,
    _redact_outbound_secrets,
    _safe_float,
    _safe_int,
    _set_today_key_timezone,
    _set_into_config,
    _single_line,
    _strip_internal_message_blocks,
    _strip_outbound_control_blocks,
    _today_key,
    _resolve_timezone_setting,
)
from .main_shared import (
    _ACTIVE_PERSONA_ID,
    _PERSONA_PROFILE_FORBIDDEN_FILENAME_CHARS,
    _PERSONA_SETTING_MANIFEST,
    _PROACTIVE_ONLY_TEMP_UNLOCK_ALIASES,
    _WINDOWS_RESERVED_FILENAME_STEMS,
    _multi_persona_event_context,
    _plugin_instance_can_dispatch,
    _plugin_instance_root,
    _private_companion_runtime,
)
from .config_migration import migrate_flat_config_into_schema_groups
from .group_context_interception import (
    intercept_astrbot_group_context,
    restore_astrbot_group_history,
)
from .persona_config import (
    PERSONA_SETTINGS_KEY,
    PERSONA_SETTINGS_REVISION_KEY,
    PERSONA_SETTINGS_SCHEMA_VERSION,
    PERSONA_SETTINGS_VERSION_KEY,
    PersonaConfigError,
    PersonaSettingsTypeError,
    create_persona_settings,
    copy_from_primary_config,
    detach_persona_settings,
    load_scope_manifest,
    migrate_persona_profile,
    normalize_persona_settings,
    normalize_setting_value,
    resolve_effective_settings,
    resolve_persona_setting,
    runtime_persona_setting,
)
from .persona_sqlite_store import (
    PersonaSqliteStoreError,
    PersonaSqliteStoreRegistry,
    load_persona_sqlite_store,
    read_persona_store_snapshot_read_only,
)
from .model_routing import contains_sensitive_refusal, scope_allows
from .person_context_contract import (
    CONTRACT_NAME as PERSON_CONTRACT_NAME,
    CONTRACT_VERSION as PERSON_CONTRACT_VERSION,
    P3_CONTRACT_NAME,
    P3_CONTRACT_VERSION,
    build_identity_key,
    contract_self_check as person_contract_self_check,
)
from .unified_person_registry import UnifiedPersonRegistry
from .migration_backfill import MigrationBackfill, legacy_pending_reference
from .migration_dual_write import MigrationDualWriteProducer
from .migration_replay import MigrationReplayWorker
from .migration_read_router import MigrationRelationshipReadRouter
from .migration_stability import advance_migration_stability
from .migration_source_inspector import inspect_migration_sources
from .relationship_account_store import RelationshipAccountStore
from .req041_observability import Req041Observability
from .relationship_affinity_runtime import (
    admit_confirmed_group_affinity,
    normalize_group_allowlist,
    prepare_group_affinity_candidate,
)
from .identity_namespace import AssurancePolicy, NamespaceContext
from .migration_scoped_projection import (
    ScopedProjectionSynchronizer,
    scoped_group_ref,
    scoped_persona_ref,
)
from .scoped_runtime_view import overlay_group_runtime_view, overlay_private_runtime_view
from .unified_profile_contract import (
    build_person_ref as req036_build_person_ref,
    build_profile_dto as req036_build_profile_dto,
    build_portrait_request as req036_build_portrait_request,
    validate_profile_dto as req036_validate_profile_dto,
)
from .unified_profile_service import (
    DEFAULT_UNAUTHORIZED_PRIVATE_REPLY,
    capability_summary as req036_capability_summary,
    ensure_new_profile_capabilities as req036_ensure_new_profile_capabilities,
    private_companion_gate as req036_private_companion_gate,
    proactive_private_gate as req036_proactive_private_gate,
    update_capabilities as req036_update_capabilities,
)
from .context_orchestration import build_context, project_context
from .p4_shadow import build_p4_shadow
from .p4_affinity_confinement import apply_legacy_relationship_delta
from .p4_live_runtime import decide_live_request
from .p4_runtime_gate import SAFE_CONFINEMENT_REPLY
from .extension_api_content import _ContentCapabilityFamily
from .extension_api_diagnostics import _DiagnosticsCapabilityFamily
from .extension_api_identity import _IdentityCapabilityFamily
from .extension_api_image import _ImageCapabilityFamily
from .extension_api_memory import _MemoryCapabilityFamily
from .extension_api_qzone import _QzoneCapabilityFamily
from .extension_api_relationship import _RelationshipCapabilityFamily
from .extension_api_scheduler import _SchedulerCapabilityFamily
from .memory_page_snapshot import MemoryPageSnapshotService
from .story_authority import (
    StoryAuthorityError,
    story_authority_controller,
    story_legacy_operation,
    story_legacy_sync_operation,
)
from .story_handoff import resume_story_handoff
from .domains.affect.reply_temperature import (
    compose_reply_temperature,
    reply_temperature_prompt_section,
)
from .plugin_identity import (
    PLUGIN_ID,
    PLUGIN_VERSION,
    is_module_path_for_package,
)
from .lab_fixture_adapter import register_companion_lab_fixture_adapter
from .companion_interaction_expression import (
    build_expression_decision,
    content_intent_from_text,
    expression_decision_prompt_section,
)
from .photo_reference_catalog import CATALOG_VERSION, load_catalog, validate_and_serialize
from .photo_nai_params import extract_user_photo_nai_params
from .relationship_ledger import normalize_relationship_positive_stage_cap_key
from .relationship_policy import normalize_relationship_stage_policy
from .runtime_config_dispatcher import dispatch_runtime_config_effects
from .companion.injection import (
    PROTOCOL_VERSION,
    ContextContribution,
    ExtensionManifest,
    ExtensionRegistry,
    ExtensionStatus,
    RuntimeScope,
    Scope,
)


_PHOTO_TOOL_PROMPT_FORMAT_MARKER = "<!-- private_companion_prompt_format_req_v1 -->"

from .busy_reply_gate import BusyReplyGateMixin
from .chronotype import ChronotypeMixin
from .memory_companion_adapter import MemoryCompanionAdapterMixin
from .p5_attestation import P5AttestationError, REASON_CODES as P5_ATTESTATION_REASON_CODES
from .p5_source_observer import evaluate_source
from .message_pipeline import (
    event_data_save_boundary,
    handle_group_message,
    handle_private_message,
)
from .wake_message_context import capture_wake_message_context, restore_wake_message_request
from .tool_history_sanitizer import sanitize_history_image_blocks, sanitize_openai_tool_history
from .forward_message import ForwardMessageMixin
from .private_image import PrivateImageMixin
from .conversation_injection_plan import (
    DELIVERY_GROUP_MARKER_METADATA_KEY,
    PLACEMENT_DYNAMIC_SYSTEM,
    PLACEMENT_STABLE_SYSTEM,
    PLACEMENT_TOOL_CONTRACT,
    PLACEMENT_TURN_TAIL,
    get_conversation_injection_plan,
)
from .conversation_prompt_section import (
    ExactText,
    PromptRenderMode,
    PromptSection,
    exact_text,
    prompt_cdata,
    prompt_heading_ref,
    prompt_section,
    render_prompt_content,
    render_prompt_sections,
)
from .hdsi_experiment import (
    apply_hdsi_prompt,
    finalize_trial_response,
    hdsi_window_command,
    mark_hdsi_route,
    record_hdsi_inbound_event,
    record_hdsi_outbound_event,
    record_hdsi_proactive_event,
    record_trial_failure,
)
from .prompt_surface import CollectedPromptContext, PromptSurface
from .passive_state_pipeline import inject_humanized_state as run_humanized_state_injection
from .qzone_integration import QzoneMixin
from .segmented_message import (
    bind_reply_components_to_first_text,
    component_kind,
    component_order_from_owner,
    component_strategies_from_owner,
    flatten_component_chunks,
    has_fenced_llm_segment_marker,
    LLM_SEGMENT_MARKER,
    normalize_component_strategy,
    parse_llm_segment_control,
    plan_component_chunks,
    sanitize_llm_segment_control_tokens,
    split_llm_controlled_text,
    strip_llm_segment_marker_lines,
)
from .token_budget import TokenBudgetMixin
from .balance_awareness import BalanceAwarenessMixin
from .body_monitor_integration import BodyMonitorIntegration
from .worldbook import WorldbookMixin
from .user_memory import UserMemoryMixin
from .creative import CreativeMixin
from .content_companion_bridge import ContentCompanionBridgeMixin
from .external_bridge_resolver import invalidate_external_bridge_cache
from .proactive import ProactiveMixin
from .group_wakeup import GroupWakeupMixin
from .group_observation import GroupObservationMixin
from .group_cycle_boundary import (
    build_group_cycle_boundary,
    group_cycle_boundary_prompt_section,
)
from .logging_util import get_module_logger

# ``logger`` must be bound before the optional-module fallbacks below: each
# degradation path reports the missing file through the logger, so leaving the
# binding until later in the module turned a fail-open fallback into a
# NameError that aborted the whole plugin import.
logger = get_module_logger(__name__)
try:
    from .group_member_safety import GroupMemberSafetyMixin
except ModuleNotFoundError as exc:
    if str(getattr(exc, "name", "") or "").split(".")[-1] != "group_member_safety":
        raise

    class GroupMemberSafetyMixin:
        """Fail-open fallback for an incomplete release package."""

        @staticmethod
        def _extract_group_member_safety_hidden_markers(text: Any) -> tuple[str, list[dict[str, Any]]]:
            return str(text or ""), []

        @staticmethod
        def _group_member_safety_hidden_marker_mode() -> str:
            return "disabled"

        @staticmethod
        def _group_member_safety_member(*args: Any, **kwargs: Any) -> None:
            return None

        @staticmethod
        def _group_member_safety_is_exempt_event(*args: Any, **kwargs: Any) -> bool:
            return True

        @staticmethod
        def _group_member_safety_active(*args: Any, **kwargs: Any) -> bool:
            return False

        async def _append_group_member_safety_hidden_marker_to_request(
            self,
            *args: Any,
            **kwargs: Any,
        ) -> None:
            return None

        async def _record_group_member_safety_decision(
            self,
            *args: Any,
            **kwargs: Any,
        ) -> dict[str, Any]:
            return {"reviewed": False, "counted": False, "blocked": False, "reason": "module_missing"}

        async def _review_group_member_safety_message(
            self,
            *args: Any,
            **kwargs: Any,
        ) -> dict[str, Any]:
            return {"reviewed": False, "counted": False, "blocked": False, "reason": "module_missing"}

    logger.error(
        "发布包缺少 group_member_safety.py，群成员风控已停用；插件其余功能继续加载。"
        "请重新安装包含该文件的完整版本。"
    )
from .event_dispatch import EventDispatchMixin, _ON_WAITING_LLM_REQUEST
from .reading_archive import ReadingArchiveMixin
from .news_exploration import NewsExplorationMixin
try:
    from .self_timeline import SelfTimelineMixin
except ModuleNotFoundError as exc:
    if str(getattr(exc, "name", "") or "").split(".")[-1] != "self_timeline":
        raise

    class SelfTimelineMixin:
        """Fallback used when an old release package missed self_timeline.py."""

        def _format_self_timeline_context_for_reply(self, *args: Any, **kwargs: Any) -> str:
            return ""

    logger.warning("self_timeline.py 缺失，已跳过 Bot 自身时间线注入能力。请重新安装完整版本。")
from .core_store import CoreStoreMixin
from .storage.path_generation import activate_persistence_owner
from .platform_compat import PlatformCompatibilityMixin
from .integration_status import IntegrationStatusMixin
from .astrbot_knowledge import AstrBotKnowledgeMixin
from .atrelay import AtRelayMixin
from .main_reaction_expression import PrivateCompanionPluginReactionExpressionMixin
from .main_outbound_persistence import PrivateCompanionPluginOutboundPersistenceMixin
from .main_p5_attestation import PrivateCompanionPluginP5AttestationMixin
from .main_pc_llm_tools import PrivateCompanionPluginPcLlmToolsMixin
from .main_sqlite_group_reset import PrivateCompanionPluginSqliteGroupResetMixin
from .main_rest_reply import PrivateCompanionPluginRestReplyMixin
from .main_prompt_formatting import PrivateCompanionPluginPromptFormattingMixin
from .main_atrelay_relay import PrivateCompanionPluginAtrelayRelayMixin
from .main_group_inbound_capture import PrivateCompanionPluginGroupInboundCaptureMixin
from .main_persona_routing import PrivateCompanionPluginPersonaRoutingMixin
from .main_outbound_guard import PrivateCompanionPluginOutboundGuardMixin
from .main_prompt import PrivateCompanionPluginPromptMixin
from .main_req041 import PrivateCompanionPluginReq041Mixin
from .proactive_engine import ProactiveEngineMixin
from .proactive_message import ProactiveMessageMixin
from .image_companion_bridge import ImageCompanionBridgeMixin
from .nai_image_bridge import NAIImageBridgeMixin
from .proactive_chat_runtime_bridge import ProactiveChatRuntimeBridge
from .plugin_lifecycle import (
    assemble_plugin_dependencies,
    cancel_registered_host_tasks,
    close_early_resources,
    task_manager,
)
from .plugin_bootstrap import (
    DEFAULT_AI_DAILY_JUYA_UID,
    DEFAULT_AI_DAILY_MORNING_UID,
    DEFAULT_AI_DAILY_SOURCES,
    DEFAULT_NEWS_SOURCES,
    LEGACY_DEFAULT_NEWS_SOURCES,
    PREVIOUS_TECH_DEFAULT_NEWS_SOURCES,
    initialize_plugin_entrypoint_state,
    initialize_plugin_config,
    initialize_plugin_post_runtime_state,
    initialize_plugin_runtime,
)
from .daily_state import DailyStateMixin
from .agenda_runtime import AgendaRuntimeMixin
from .daily_review import DailyReviewMixin
from .scene_context import SceneContextMixin
from .place_cognitive_map import PlaceCognitiveMapMixin
from .game_integration import GameIntegrationMixin
from .state_views import StateViewsMixin
from .interaction_utils import InteractionUtilsMixin
from .llm_tool_actions import LlmToolActionsMixin, PHOTO_TOOL_SILENT_SENTINEL
from .command_handlers import CommandHandlersMixin
from .wardrobe_runtime import WardrobeMixin
from .tts_enhancement import TtsEnhancementMixin
from .tts_tool_sanitizer import TtsToolSanitizerMixin
from .reality_companion_bridge import RealityCompanionBridgeMixin
from .planning import (
    build_daily_plan_prompt,
    build_detail_enhancement_prompt,
    format_plan_for_diary,
    generate_daily_plan,
    generate_detail_enhancement,
    get_schedule_planning_prompt,
    normalize_long_term_events,
    normalize_story_items,
    normalize_story_plan,
    pick_detail_segment,
)

_PRIVATE_COMPANION_RUNTIME_KEY = "_astrbot_private_companion_runtime_v1"



_private_companion_plugin: Any | None = _private_companion_runtime.active_plugin



def _is_primary_plugin_instance(instance: Any) -> bool:
    return _plugin_instance_root(instance) == PLUGIN_NAME






def get_private_companion_api() -> Any | None:
    with _private_companion_runtime.lock:
        plugin = _private_companion_runtime.active_plugin
        api = getattr(plugin, "extension_api", None) if plugin is not None else None
        lifecycle = getattr(api, "bridge_lifecycle_status", None)
        if not callable(lifecycle):
            return None
        try:
            status = lifecycle()
        except Exception:
            return None
        if not isinstance(status, dict) or status.get("active") is not True:
            return None
        return api


class PrivateCompanionExtensionAPI:
    """Lightweight integration API for external AstrBot plugins."""

    def __init__(self, plugin: "PrivateCompanionPlugin") -> None:
        self._plugin = plugin
        self._story_migration_generation = uuid.uuid4().hex
        self._story_migration_state = "created"
        self._extension_registry = ExtensionRegistry()
        self._extension_registry.register(
            ExtensionManifest(
                id=PLUGIN_ID,
                version=self._protocol_version(PLUGIN_VERSION),
                sdk_version=PROTOCOL_VERSION,
                display_name="Private Companion",
            )
        )
        self._qzone_reference_lock = threading.RLock()
        self._qzone_references: dict[str, tuple[float, Any]] = {}
        story_authority_controller().stage_generation(
            self._story_migration_generation
        )
        self._memory_page_service = MemoryPageSnapshotService(self)
        self._identity_family = _IdentityCapabilityFamily(self)
        self._relationship_family = _RelationshipCapabilityFamily(self)
        self._scheduler_family = _SchedulerCapabilityFamily(self)
        self._memory_family = _MemoryCapabilityFamily(self)
        self._content_family = _ContentCapabilityFamily(self)
        self._diagnostics_family = _DiagnosticsCapabilityFamily(self)
        self._image_family = _ImageCapabilityFamily(self)
        self._qzone_family = _QzoneCapabilityFamily(self)

    @staticmethod
    def _protocol_version(version: Any) -> str:
        """Reduce a host plugin version to the protocol's major.minor form."""
        match = re.search(r"(\d+)\.(\d+)", str(version or ""))
        return f"{match.group(1)}.{match.group(2)}" if match else "0.1"

    def _set_core_extension_status(self, state: str, *, reason: str = "") -> None:
        """Keep the control-plane status aligned with the published API generation."""
        try:
            current = self._extension_registry.status(PLUGIN_ID)
            task_counter = getattr(self._plugin, "_extension_task_count", None)
            task_count = task_counter() if callable(task_counter) else 0
            now = datetime.now().astimezone().isoformat()
            self._extension_registry.set_status(
                ExtensionStatus(
                    id=PLUGIN_ID,
                    state=state,
                    reason=reason,
                    task_count=task_count,
                    started_at=(current.started_at if current and current.started_at else now),
                    updated_at=now,
                )
            )
        except Exception as exc:
            logger.warning("更新扩展控制面状态失败: %s", _single_line(exc, 160))

    def _story_migration_instance_generation(self) -> str:
        return self._story_migration_generation

    def _story_migration_lifecycle_state(self) -> str:
        return self._story_migration_state

    def _extension_instance_generation(self) -> str:
        return self._story_migration_generation

    def _extension_lifecycle_state(self) -> str:
        return self._story_migration_state

    def bridge_lifecycle_status(self) -> dict[str, Any]:
        """Expose whether this published cross-plugin generation is callable."""
        state = self._story_migration_state
        published = _private_companion_runtime.active_plugin is self._plugin
        return {
            "active": state == "ready" and published,
            "state": state,
            "instance_generation": self._story_migration_generation,
        }

    def register_extension(
        self,
        manifest: ExtensionManifest | dict[str, Any],
    ) -> dict[str, Any]:
        """Register public extension metadata without importing provider objects."""
        item = manifest if isinstance(manifest, ExtensionManifest) else ExtensionManifest.from_dict(manifest)
        self._extension_registry.register(item)
        return {
            "ok": True,
            "extension_id": item.id,
            "version": item.version,
            "protocol_version": PROTOCOL_VERSION,
        }

    def set_extension_status(
        self,
        status: ExtensionStatus | dict[str, Any],
    ) -> dict[str, Any]:
        item = status if isinstance(status, ExtensionStatus) else ExtensionStatus.from_dict(status)
        self._extension_registry.set_status(item)
        return {"ok": True, "extension_id": item.id, "state": item.state}

    def unregister_extension(self, extension_id: str) -> dict[str, Any]:
        normalized_id = str(extension_id or "").strip().lower()
        if normalized_id == PLUGIN_ID:
            return {"ok": False, "extension_id": normalized_id, "reason": "core_extension_protected"}
        removed = self._extension_registry.unregister(normalized_id)
        return {"ok": removed, "extension_id": normalized_id}

    def extension_control_plane_status(self) -> dict[str, Any]:
        """Return bounded metadata used by the panel and self-check command."""
        core_status = self._extension_registry.status(PLUGIN_ID)
        task_counter = getattr(self._plugin, "_extension_task_count", None)
        task_count = task_counter() if callable(task_counter) else 0
        if core_status is not None and core_status.task_count != task_count:
            self._extension_registry.set_status(
                ExtensionStatus.from_dict(
                    {
                        **core_status.to_dict(),
                        "task_count": task_count,
                    }
                )
            )
        return {
            "protocol_version": PROTOCOL_VERSION,
            "instance_generation": self._story_migration_generation,
            "lifecycle_state": self._story_migration_state,
            "extensions": list(self._extension_registry.snapshot()),
            "issues": list(self._extension_registry.self_check()),
        }

    def runtime_scope_for_event(self, event: Any) -> RuntimeScope | None:
        """Resolve one complete, stable scope at the host boundary.

        Extension code should use this result instead of extracting a user ID
        or platform-specific origin on its own. Missing adapter fields get
        explicit ``unknown`` values so a DTO is never silently re-scoped to a
        different Bot or persona.
        """
        if event is None:
            return None
        plugin = self._plugin

        def _call(name: str, default: Any = "") -> Any:
            getter = getattr(plugin, name, None)
            if not callable(getter):
                return default
            try:
                return getter(event)
            except Exception:
                return default

        raw: dict[str, Any] = {}
        raw_reader = getattr(plugin, "_event_raw_payload", None)
        if callable(raw_reader):
            try:
                candidate = raw_reader(event)
                if isinstance(candidate, dict):
                    raw = candidate
            except Exception:
                raw = {}

        origin = _single_line(getattr(event, "unified_msg_origin", ""), 240)
        platform = _single_line(_call("_platform_kind_for_event"), 80)
        if not platform:
            platform_getter = getattr(event, "get_platform_name", None)
            if callable(platform_getter):
                try:
                    platform = _single_line(platform_getter(), 80)
                except Exception:
                    platform = ""
        if not platform and origin:
            platform = _single_line(origin.split(":", 1)[0], 80)
        platform = platform or _single_line(getattr(plugin, "target_platform", ""), 80) or "unknown"

        sender_id = _single_line(_call("_safe_event_sender_id"), 160)
        if not sender_id:
            sender_id = _single_line(raw.get("user_id") or raw.get("sender_id"), 160)
        self_id = _single_line(_call("_event_self_id"), 160)
        if not self_id:
            self_id = _single_line(raw.get("self_id") or raw.get("bot_id"), 160)
        bot_id = _single_line(
            raw.get("bot_id")
            or getattr(event, "bot_id", "")
            or (f"{platform}:{self_id}" if self_id else ""),
            160,
        ) or f"{platform}:unknown"
        account_id = _single_line(
            raw.get("account_id")
            or getattr(event, "account_id", "")
            or (f"{platform}:{self_id}" if self_id else bot_id),
            160,
        ) or f"{platform}:{bot_id}"

        group_id = _single_line(_call("_extract_group_id_from_event"), 160)
        if not group_id:
            group_id = _single_line(raw.get("group_id"), 160)
        conversation_id = origin or _single_line(
            raw.get("conversation_id") or getattr(event, "conversation_id", ""),
            240,
        )
        if not conversation_id:
            conversation_id = f"{platform}:{group_id or sender_id or 'global'}"
        session_id = _single_line(
            getattr(event, "session_id", "") or raw.get("session_id") or conversation_id,
            240,
        ) or conversation_id

        persona_getter = getattr(plugin, "_effective_plugin_persona_id", None)
        try:
            persona_id = _single_line(persona_getter() if callable(persona_getter) else "", 96)
        except Exception:
            persona_id = ""
        if not persona_id:
            primary_getter = getattr(plugin, "_primary_persona_id", None)
            try:
                persona_id = _single_line(primary_getter() if callable(primary_getter) else "", 96)
            except Exception:
                persona_id = ""
        persona_id = persona_id or "default"

        installation_source = _single_line(
            getattr(plugin, "data_dir", "")
            or getattr(plugin, "data_file", "")
            or PLUGIN_ID,
            512,
        )
        installation_id = "installation:" + hashlib.sha256(
            installation_source.encode("utf-8", "ignore")
        ).hexdigest()[:24]
        try:
            binding_revision = int(
                getattr(event, "persona_binding_revision", 0)
                or raw.get("persona_binding_revision", 0)
                or 0
            )
        except (TypeError, ValueError, OverflowError):
            binding_revision = 0

        try:
            return RuntimeScope(
                installation_id=installation_id,
                bot_id=bot_id,
                platform=platform,
                account_id=account_id,
                persona_id=persona_id,
                conversation_id=conversation_id,
                session_id=session_id,
                user_id=sender_id,
                group_id=group_id,
                persona_binding_revision=max(0, binding_revision),
            )
        except Exception as exc:
            logger.debug("解析扩展运行作用域失败: %s", _single_line(exc, 160))
            return None

    @staticmethod
    def _scopes_compatible(expected: Scope | RuntimeScope, supplied: Scope | RuntimeScope) -> bool:
        """Compare only fields present on both sides of a legacy scope."""
        for name in (
            "installation_id",
            "bot_id",
            "platform",
            "account_id",
            "conversation_id",
            "session_id",
            "user_id",
            "group_id",
            "persona_id",
        ):
            left = _single_line(getattr(expected, name, ""), 256)
            right = _single_line(getattr(supplied, name, ""), 256)
            if left and right and left != right:
                return False
        expected_revision = int(getattr(expected, "persona_binding_revision", 0) or 0)
        supplied_revision = int(getattr(supplied, "persona_binding_revision", 0) or 0)
        if expected_revision and supplied_revision and expected_revision != supplied_revision:
            return False
        return True

    @staticmethod
    def _complete_scope(scope: Scope | RuntimeScope | None) -> RuntimeScope | None:
        """Convert a legacy Scope only when all host identity fields exist."""
        if isinstance(scope, RuntimeScope):
            return scope
        if not isinstance(scope, Scope):
            return None
        required = ("installation_id", "bot_id", "platform", "account_id", "persona_id")
        if any(not _single_line(getattr(scope, name, ""), 256) for name in required):
            return None
        try:
            return RuntimeScope(**{
                name: getattr(scope, name)
                for name in (
                    "installation_id", "bot_id", "platform", "account_id", "persona_id",
                    "conversation_id", "session_id", "user_id", "group_id", "persona_binding_revision",
                )
            })
        except Exception:
            return None

    def add_context_contribution(
        self,
        req: Any,
        contribution: ContextContribution | dict[str, Any],
        *,
        event: Any | None = None,
        source_id: str = "",
        priority: int | None = None,
    ) -> bool:
        """Publish one bounded typed context section for the current request.

        The method is intentionally synchronous so an extension can call it
        from ``on_llm_request`` without creating another task. It only stages a
        request-local section; the host still controls final prompt placement,
        delivery and audit metadata.
        """
        if req is None:
            return False
        try:
            item = (
                contribution
                if isinstance(contribution, ContextContribution)
                else ContextContribution.from_dict(contribution)
            )
            source = _single_line(source_id or item.source or "external", 128).lower()
            if not source or not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{1,127}", source):
                return False
            if source != item.source:
                payload = item.to_dict()
                payload["source"] = source
                item = ContextContribution.from_dict(payload)
            resolved_scope = self.runtime_scope_for_event(event) if event is not None else self._complete_scope(item.scope)
            if resolved_scope is None:
                return False
            if item.scope is not None and not self._scopes_compatible(resolved_scope, item.scope):
                logger.warning(
                    "拒绝跨作用域扩展上下文: source=%s key=%s",
                    source,
                    item.key,
                )
                return False
            section = prompt_section(
                key=f"extension.{source}.{item.key}",
                title=f"扩展上下文：{source}",
                source="extension_api",
                content=item.content,
                metadata={
                    "source_id": source,
                    "lane": item.lane,
                    "evidence": item.evidence,
                    "source_refs": list(item.source_refs),
                    "revision": item.revision,
                    "trace_id": item.trace_id,
                    "visibility": item.visibility,
                    "max_age_seconds": item.max_age_seconds,
                    "scope_key": resolved_scope.scope_key,
                },
            )
            normalized_priority = item.priority if priority is None else priority
            normalized_priority = max(-100000, min(100000, int(normalized_priority)))
            marker = f"extension_context:{source}:{item.key}"
            append = getattr(self._plugin, "_append_turn_prompt_fragment_by_position", None)
            if not callable(append):
                return False
            return bool(
                append(
                    req,
                    marker,
                    section,
                    priority=normalized_priority,
                    force_dynamic=True,
                )
            )
        except Exception as exc:
            logger.debug("扩展上下文注入失败: %s", _single_line(exc, 160))
            return False

    publish_context_contribution = add_context_contribution
    submit_context_contribution = add_context_contribution
    get_runtime_scope = runtime_scope_for_event

    def _activate_story_migration_api(self) -> bool:
        if self._story_migration_state != "created":
            return False
        story_authority_controller().activate_generation(
            self._story_migration_generation
        )
        self._story_migration_state = "ready"
        self._set_core_extension_status("ready")
        return True

    def _supersede_story_migration_api(self) -> None:
        if self._story_migration_state in {"created", "ready"}:
            self._story_migration_state = "superseded"
            self._set_core_extension_status("stopped", reason="superseded")
            self._memory_page_service.clear_references()
            with self._qzone_reference_lock:
                self._qzone_references.clear()
            story_authority_controller().supersede_generation(
                self._story_migration_generation
            )

    def _close_story_migration_api(self) -> None:
        self._memory_page_service.clear_references()
        with self._qzone_reference_lock:
            self._qzone_references.clear()
        if self._story_migration_state != "superseded":
            self._story_migration_state = "closed"
            self._set_core_extension_status("stopped", reason="closed")
            story_authority_controller().close_generation(
                self._story_migration_generation
            )

    def register_proactive_ability(self, spec: dict[str, Any]) -> bool:
        return self._scheduler_family.register_proactive_ability(
            spec,
        )

    def unregister_proactive_ability(self, name: str) -> bool:
        return self._scheduler_family.unregister_proactive_ability(
            name,
        )

    def list_proactive_abilities(self) -> list[dict[str, Any]]:
        return self._scheduler_family.list_proactive_abilities()

    async def record_game_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Apply one idempotent, per-user game event to companion afterglow."""
        return await self._memory_family.record_game_event(
            payload,
        )

    def memory_page_capabilities(self) -> dict[str, Any]:
        """Describe the versioned, read-only Memory Page producer API."""
        return self._memory_family.memory_page_capabilities()

    async def export_memory_page_snapshot(
        self,
        *,
        target_plugin_id: str,
        selected_date: str = "",
    ) -> dict[str, Any]:
        """Export a bounded, path-free Memory Page snapshot."""
        return await self._memory_family.export_memory_page_snapshot(
            target_plugin_id=target_plugin_id,
            selected_date=selected_date,
        )

    async def read_memory_page_photo(
        self,
        *,
        target_plugin_id: str,
        photo_ref: str,
    ) -> dict[str, Any]:
        """Read one generation-bound photo reference after strict revalidation."""
        return await self._memory_family.read_memory_page_photo(
            target_plugin_id=target_plugin_id,
            photo_ref=photo_ref,
        )

    def get_realtime_voice_config(self) -> dict[str, Any]:
        """Expose the active companion voice language to realtime plugins."""
        return self._content_family.get_realtime_voice_config()

    def story_migration_capabilities(self) -> dict[str, Any]:
        """Describe the versioned, read-only Story migration snapshot API."""
        return self._content_family.story_migration_capabilities()

    async def export_story_migration_snapshot(
        self,
        *,
        lease_token: str = "",
    ) -> dict[str, Any]:
        """Export a detached Story snapshot without exposing local paths."""
        return await self._content_family.export_story_migration_snapshot(
            lease_token=lease_token,
        )

    async def prepare_story_handoff(
        self,
        *,
        target_plugin_id: str,
        owner_id: str,
    ) -> dict[str, Any]:
        """Drain legacy Story writers and prepare an ephemeral handoff lease."""
        return await self._content_family.prepare_story_handoff(
            target_plugin_id=target_plugin_id,
            owner_id=owner_id,
        )

    async def abort_story_handoff(
        self,
        *,
        lease_token: str,
    ) -> dict[str, Any]:
        """Abort the exact active Story handoff lease."""
        return await self._content_family.abort_story_handoff(
            lease_token=lease_token,
        )

    async def commit_story_handoff(
        self,
        *,
        lease_token: str = "",
    ) -> dict[str, Any]:
        """Commit a live lease or replay an already durable handoff marker."""

        return await self._content_family.commit_story_handoff(
            lease_token=lease_token,
        )

    def qzone_capabilities(self) -> dict[str, Any]:
        """Describe the generation-bound Companion-owned QZone contract."""

        return self._qzone_family.qzone_capabilities()

    def qzone_status_snapshot(self) -> dict[str, Any]:
        """Return a path-free, credential-free QZone status snapshot."""

        return self._qzone_family.qzone_status_snapshot()

    def export_qzone_config_snapshot(
        self,
        *,
        target_plugin_id: str,
    ) -> dict[str, Any]:
        """Export only non-secret owner settings plus credential state."""

        return self._qzone_family.export_qzone_config_snapshot(
            target_plugin_id=target_plugin_id,
        )

    async def execute_qzone_operation(
        self,
        operation: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute one exact QZone operation without exposing the host/Page API."""

        return await self._qzone_family.execute_qzone_operation(operation, payload)

    async def synthesize_realtime_voice(
        self,
        text: str,
        *,
        tts_provider: Any = None,
        provider_settings: dict[str, Any] | None = None,
        source: str = "external_realtime",
        play_local: bool = True,
    ) -> dict[str, Any]:
        """Synthesize external realtime speech through companion TTS rules."""
        return await self._plugin._synthesize_realtime_voice(
            text,
            tts_provider=tts_provider,
            provider_settings=provider_settings,
            source=source,
            play_local=play_local,
        )

    def get_reality_touch_authorized_user_ids(self) -> list[str]:
        """Return host administrators and primary users eligible for device consent."""
        return self._identity_family.get_reality_touch_authorized_user_ids()

    async def notify_mobile_location_update(self, user_id: str) -> dict[str, Any]:
        """Let the mobile gateway wake location-aware proactive planning promptly."""
        return await self._scheduler_family.notify_mobile_location_update(
            user_id,
        )

    def get_reality_touch_host_context(self, user_id: str) -> dict[str, Any]:
        """Expose bounded identity and relationship context to the device plugin."""
        return self._relationship_family.get_reality_touch_host_context(
            user_id,
        )

    def export_reality_touch_legacy_state(self) -> dict[str, Any]:
        """Return a detached one-time migration payload for Reality Companion."""
        plugin = self._plugin
        source_config = getattr(plugin, "config", {})

        def legacy_bool(key: str, default: bool = False) -> bool:
            value = _flat_get(source_config, key, default)
            if isinstance(value, str):
                normalized = value.strip().lower()
                if normalized in {"true", "1", "yes", "y", "on", "enable", "enabled", "启用", "开启", "开", "是"}:
                    return True
                if normalized in {"false", "0", "no", "n", "off", "disable", "disabled", "停用", "关闭", "关", "否", ""}:
                    return False
            return bool(value)

        def legacy_int(key: str, default: int, minimum: int, maximum: int) -> int:
            return _safe_int(_flat_get(source_config, key, default), default, minimum, maximum)

        source_users = plugin.data.get("users") if isinstance(plugin.data, dict) else None
        allowed_keys = {
            "user_id",
            "umo",
            "nickname",
            "last_display_name",
            "display_name",
            "reality_touch_consent",
            "reality_touch_pending_consent",
            "reality_touch_policy",
            "reality_touch_camera_consent",
            "reality_touch_camera_policy",
            "wakeup_alarm",
            "reality_touch_reminders",
        }
        users: dict[str, dict[str, Any]] = {}
        if isinstance(source_users, dict):
            for user_id, user in source_users.items():
                if not isinstance(user, dict):
                    continue
                selected = {
                    key: deepcopy(value)
                    for key, value in user.items()
                    if key in allowed_keys
                }
                if any(key.startswith("reality_touch") or key == "wakeup_alarm" for key in selected):
                    selected.setdefault("user_id", _single_line(user_id, 120))
                    users[_single_line(user_id, 120)] = selected
        store = plugin.data.get("reality_touch") if isinstance(plugin.data, dict) else None
        config = {
            "enabled": legacy_bool("enable_experimental_bluetooth_wakeup"),
            "camera_enabled": legacy_bool("enable_reality_touch_camera"),
            "camera_index": legacy_int("reality_touch_camera_index", 0, 0, 100000),
            "camera_min_interval_seconds": legacy_int("reality_touch_camera_min_interval_seconds", 60, 10, 3600),
            "camera_capture_timeout_seconds": legacy_int("reality_touch_camera_capture_timeout_seconds", 5, 2, 20),
            "camera_analysis_timeout_seconds": legacy_int("reality_touch_camera_analysis_timeout_seconds", 25, 5, 90),
            "camera_proactive_curiosity_enabled": legacy_bool("enable_reality_touch_camera_proactive_curiosity"),
            "camera_proactive_min_tier": legacy_int("reality_touch_camera_proactive_min_tier", 4, 1, 5),
            "camera_proactive_max_daily": legacy_int("reality_touch_camera_proactive_max_daily", 1, 0, 10),
            "camera_proactive_cooldown_minutes": legacy_int("reality_touch_camera_proactive_cooldown_minutes", 240, 10, 1440),
            "audio_default_playback_volume": legacy_int("tts_local_playback_volume", 35, 0, 100),
        }
        return {
            "version": 1,
            "users": users,
            "reality_touch": deepcopy(store) if isinstance(store, dict) else {},
            "config": config,
        }

    async def generate_reality_touch_text(self, prompt: str, **kwargs: Any) -> str:
        """Generate bounded device-facing wording through the host model stack."""
        caller = getattr(self._plugin, "_llm_call", None)
        if not callable(caller):
            return ""
        return str(await caller(prompt, **kwargs) or "")

    async def send_reality_touch_chat(self, umo: str, text: str) -> bool:
        return await self._content_family.send_reality_touch_chat(
            umo,
            text,
        )

    async def record_reality_touch_output(
        self,
        user_id: str,
        text: str,
        *,
        source: str = "reality_touch_audio",
        delivered_at: float | None = None,
    ) -> dict[str, Any]:
        """Record speech delivered outside chat so the next reply can continue it."""
        return await self._content_family.record_reality_touch_output(
            user_id,
            text,
            source=source,
            delivered_at=delivered_at,
        )

    def get_reality_touch_cron_manager(self) -> Any | None:
        return self._scheduler_family.get_reality_touch_cron_manager()

    async def delete_reality_touch_cron_job(self, job_id: str) -> tuple[bool, str]:
        return await self._scheduler_family.delete_reality_touch_cron_job(
            job_id,
        )

    def get_bot_identity(self) -> dict[str, Any]:
        """Return a stable Bot identity without guessing between multiple accounts."""
        return self._identity_family.get_bot_identity()

    def get_unified_person_contract(self) -> dict[str, Any]:
        return self._identity_family.get_unified_person_contract()

    def resolve_unified_person(self, identity: dict[str, Any]) -> dict[str, Any]:
        return self._identity_family.resolve_unified_person(
            identity,
        )

    def create_unified_person(
        self,
        identity: dict[str, Any],
        *,
        profile: dict[str, Any] | None = None,
        operation_id: str = "",
    ) -> dict[str, Any]:
        return self._identity_family.create_unified_person(
            identity,
            profile=profile,
            operation_id=operation_id,
        )

    def get_unified_person_projection(self, person_id: str) -> dict[str, Any] | None:
        return self._identity_family.get_unified_person_projection(
            person_id,
        )

    def get_p6_readonly_status(self) -> dict[str, Any]:
        """Expose bounded Unified Person counts without an authority surface."""
        return self._diagnostics_family.get_p6_readonly_status()

    def get_unified_person_context(self, event: Any | None = None) -> dict[str, Any]:
        return self._identity_family.get_unified_person_context(
            event,
        )

    def get_scene_context(self, user_id: str = "") -> dict[str, Any]:
        """Return the current structured Bot-life context for plugin integrations."""
        return self._diagnostics_family.get_scene_context(
            user_id,
        )

    def get_realtime_context(self, user_id: str = "", purpose: str = "together") -> dict[str, Any]:
        """Return the full structured scene and its canonical prompt representation."""
        return self._diagnostics_family.get_realtime_context(
            user_id,
            purpose,
        )

    def record_external_realtime_continuity(
        self,
        user_id: str,
        *,
        summary: str,
        public_summary: str = "",
        facts: list[str] | None = None,
        ttl_seconds: int = 21600,
        activity_id: str = "",
    ) -> dict[str, Any]:
        """Store bounded post-call continuity without writing long-term memory."""
        return self._memory_family.record_external_realtime_continuity(
            user_id,
            summary=summary,
            public_summary=public_summary,
            facts=facts,
            ttl_seconds=ttl_seconds,
            activity_id=activity_id,
        )

    def get_external_realtime_continuity(self, *, user_id: str = "", public: bool = False) -> dict[str, Any]:
        return self._memory_family.get_external_realtime_continuity(
            user_id=user_id,
            public=public,
        )

    def notify_external_activity_started(
        self,
        activity_id: str,
        *,
        user_id: str = "",
        kind: str = "external",
        label: str = "",
        source_plugin: str = "external",
        ttl_seconds: int = 240,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._scheduler_family.notify_external_activity_started(
            activity_id,
            user_id=user_id,
            kind=kind,
            label=label,
            source_plugin=source_plugin,
            ttl_seconds=ttl_seconds,
            metadata=metadata,
        )

    def notify_external_activity_updated(
        self,
        activity_id: str,
        *,
        user_id: str = "",
        kind: str = "",
        label: str = "",
        source_plugin: str = "",
        ttl_seconds: int = 240,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._scheduler_family.notify_external_activity_updated(
            activity_id,
            user_id=user_id,
            kind=kind,
            label=label,
            source_plugin=source_plugin,
            ttl_seconds=ttl_seconds,
            metadata=metadata,
        )

    def notify_external_activity_ended(self, activity_id: str) -> bool:
        return self._scheduler_family.notify_external_activity_ended(
            activity_id,
        )

    def get_external_activity(self, *, user_id: str = "", activity_id: str = "") -> dict[str, Any]:
        return self._scheduler_family.get_external_activity(
            user_id=user_id,
            activity_id=activity_id,
        )

    async def prepare_proactive_chat(
        self,
        session_id: str,
        *,
        unanswered_count: int = 0,
    ) -> dict[str, Any]:
        return await self._plugin._prepare_proactive_chat_bridge(
            session_id,
            unanswered_count=unanswered_count,
        )

    async def review_proactive_chat_message(
        self,
        session_id: str,
        text: str,
        *,
        token: str = "",
    ) -> dict[str, Any]:
        return await self._plugin._review_proactive_chat_bridge_message(
            session_id,
            text,
            token=token,
        )

    async def notify_proactive_chat_sent(
        self,
        session_id: str,
        text: str,
        *,
        token: str = "",
    ) -> dict[str, Any]:
        return await self._plugin._record_proactive_chat_bridge_sent(
            session_id,
            text,
            token=token,
        )

    async def cancel_proactive_chat(
        self,
        session_id: str,
        *,
        token: str = "",
    ) -> bool:
        return await self._plugin._cancel_proactive_chat_bridge(
            session_id,
            token=token,
        )

    def resolve_historical_chat_identities(self, speakers: list[str]) -> dict[str, Any]:
        return self._identity_family.resolve_historical_chat_identities(
            speakers,
        )

    async def stage_historical_relationship_observations(
        self,
        *,
        user_id: str,
        user_name: str,
        batch_id: str,
        observations: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return await self._relationship_family.stage_historical_relationship_observations(
            user_id=user_id,
            user_name=user_name,
            batch_id=batch_id,
            observations=observations,
        )

    async def rebind_historical_relationship_observations(
        self,
        *,
        batch_id: str,
        old_user_id: str,
        user_id: str,
        user_name: str = "",
    ) -> dict[str, Any]:
        """Move one imported batch of traceable pending and confirmed relationship observations."""
        return await self._relationship_family.rebind_historical_relationship_observations(
            batch_id=batch_id,
            old_user_id=old_user_id,
            user_id=user_id,
            user_name=user_name,
        )

    async def rollback_historical_relationship_observations(self, batch_id: str) -> dict[str, Any]:
        return await self._relationship_family.rollback_historical_relationship_observations(
            batch_id,
        )

_LUNAR_MONTH_NAMES = [
    "正月",
    "二月",
    "三月",
    "四月",
    "五月",
    "六月",
    "七月",
    "八月",
    "九月",
    "十月",
    "冬月",
    "腊月",
]
_LUNAR_DAY_NAMES = [
    "初一",
    "初二",
    "初三",
    "初四",
    "初五",
    "初六",
    "初七",
    "初八",
    "初九",
    "初十",
    "十一",
    "十二",
    "十三",
    "十四",
    "十五",
    "十六",
    "十七",
    "十八",
    "十九",
    "二十",
    "廿一",
    "廿二",
    "廿三",
    "廿四",
    "廿五",
    "廿六",
    "廿七",
    "廿八",
    "廿九",
    "三十",
]
_SOLAR_TERM_DATES = {
    (1, 5): "小寒",
    (1, 20): "大寒",
    (2, 4): "立春",
    (2, 19): "雨水",
    (3, 5): "惊蛰",
    (3, 20): "春分",
    (4, 4): "清明",
    (4, 20): "谷雨",
    (5, 5): "立夏",
    (5, 21): "小满",
    (6, 5): "芒种",
    (6, 21): "夏至",
    (7, 7): "小暑",
    (7, 22): "大暑",
    (8, 7): "立秋",
    (8, 23): "处暑",
    (9, 7): "白露",
    (9, 23): "秋分",
    (10, 8): "寒露",
    (10, 23): "霜降",
    (11, 7): "立冬",
    (11, 22): "小雪",
    (12, 7): "大雪",
    (12, 22): "冬至",
}
_ALMANAC_YI = ["整理房间", "写字", "散步", "读书", "听歌", "轻度创作", "复盘", "安静休息"]
_ALMANAC_JI = ["熬夜", "冲动发言", "硬撑", "反复纠结", "过度解释", "临时加压", "情绪化决定"]
_PLATFORM_DISPLAY_NAMES = {
    "aiocqhttp": "QQ",
    "qq": "QQ",
    "onebot": "QQ",
    "telegram": "Telegram",
    "wechat": "微信",
    "discord": "Discord",
}

_PROACTIVE_ONLY_TEMP_UNLOCK_LABELS = {
    "all": "全部被动链路",
    "inject_passive_states": "被动状态注入",
    "enable_intent_emotion_analysis": "意图/情绪分析",
    "enable_llm_timer_scheduling": "预约类主动捕获",
    "enable_passive_topic_suppression": "重复话题抑制",
    "enable_environment_perception": "环境感知",
    "enable_message_debounce": "防抖",
    "enable_recall_enhancement": "撤回增强",
    "enable_private_image_self_recognition": "私聊图片识别",
    "enable_forward_message_adaptation": "合并/转发消息阅读",
    "enable_group_companion": "群聊观察",
    "enable_skill_growth_passive_injection": "技能被动注入",
    "enable_food_menu_recommendation": "吃什么候选",
    "enable_meal_care_proactive": "饭点主动关心",
    "enable_worldbook_member_recognition": "关系网成员识别",
    "enable_cross_user_memory_bridge": "跨用户记忆互通",
    "enable_atrelay_tools": "跨群转述工具",
    "enable_livingmemory_integration": "记忆插件被动引导",
    "enable_tts_enhancement": "TTS 后处理",
    "enable_segmented_proactive_reply": "普通 LLM 分段",
}
_PROACTIVE_ONLY_TEMP_UNLOCK_GROUPS = {
    "private_event_pipeline": {
        "enable_message_debounce",
        "enable_private_image_self_recognition",
        "enable_forward_message_adaptation",
    },
    "group_event_pipeline": {
        "enable_group_companion",
        "enable_message_debounce",
        "enable_forward_message_adaptation",
    },
    "llm_request": {
        "inject_passive_states",
        "enable_intent_emotion_analysis",
        "enable_llm_timer_scheduling",
        "enable_passive_topic_suppression",
        "enable_environment_perception",
        "enable_tts_enhancement",
        "enable_private_image_self_recognition",
        "enable_forward_message_adaptation",
        "enable_group_companion",
        "enable_skill_growth_passive_injection",
        "enable_food_menu_recommendation",
        "enable_worldbook_member_recognition",
        "enable_cross_user_memory_bridge",
        "enable_livingmemory_integration",
    },
    "pc_tools": {
        "enable_atrelay_tools",
        "enable_worldbook_member_recognition",
        "enable_cross_user_memory_bridge",
        "enable_qzone_integration",
    },
}
_PROACTIVE_ONLY_TEMP_UNLOCK_RELATED = {
    "enable_atrelay_tools": ["enable_worldbook_member_recognition"],
    "enable_cross_user_memory_bridge": ["enable_worldbook_member_recognition"],
    "enable_group_companion": ["enable_worldbook_member_recognition"],
    "enable_forward_message_adaptation": ["enable_private_image_self_recognition"],
}


def _strip_chain_plain_thinking(owner: Any, chain: list[Any]) -> None:
    """Clean registered internal tags from all Plain components as one span."""
    if not bool(runtime_persona_setting(owner, "enable_framework_error_leak_guard", True)):
        return
    plain_components = [(i, comp) for i, comp in enumerate(chain) if isinstance(comp, Plain)]
    if not plain_components:
        return
    all_text = "".join(str(getattr(comp, "text", "") or "") for _, comp in plain_components)
    split_marker_token = "\x00PRIVATE_COMPANION_SPLIT\x00"
    all_text = all_text.replace(LLM_SEGMENT_MARKER, split_marker_token)
    cleaned = _strip_internal_message_blocks(
        all_text,
        tts_enabled=bool(runtime_persona_setting(owner, "enable_tts_enhancement", False)),
    )
    cleaned = cleaned.replace(split_marker_token, LLM_SEGMENT_MARKER)
    if not all_text.startswith("\n"):
        cleaned = cleaned.lstrip("\n")
    if cleaned == all_text:
        return
    for idx, (_, comp) in enumerate(plain_components):
        try:
            comp.text = cleaned if idx == 0 else ""
        except Exception:
            pass
async def _mark_hdsi_inbound(plugin: Any, event: Any) -> None:
    """Record an inbound HDSI route for both private and group entry points.

    The two message handlers need identical bookkeeping, so keep it in one
    place and fail open: a broken HDSI sidecar must never block the normal
    companion reply path.
    """
    mark_hdsi_route(plugin, event)
    try:
        await record_hdsi_inbound_event(plugin, event)
    except Exception:
        return


# 本插件的部分 @filter.* hook 定义在子模块（atrelay / main_outbound_guard / main_prompt 等）里。
# AstrBot 的 get_handlers_by_event_type(only_activated=True) 按 handler_module_path 去
# star_map 反查插件元数据，而 star_map 只登记插件主模块路径，故这些 handler 会被静默跳过。
# 这里统一把它们重绑到主模块路径，使其能通过 only_activated 反查。
from .handler_binding import bind_submodule_handlers as _bind_submodule_handlers  # noqa: E402

_PACKAGE_NAME = __package__ or "astrbot_plugin_private_companion"
for _pkg in {_PACKAGE_NAME, _PACKAGE_NAME.rsplit(".", 1)[0]}:
    try:
        _bind_submodule_handlers(_pkg, f"{_pkg}.main")
    except Exception:
        # 绑定失败不应阻断插件加载。
        pass





class PrivateCompanionPlugin(
    CoreStoreMixin,
    PlatformCompatibilityMixin,
    AstrBotKnowledgeMixin,
    IntegrationStatusMixin,
    BusyReplyGateMixin,
    ChronotypeMixin,
    MemoryCompanionAdapterMixin,
    PrivateImageMixin,
    ForwardMessageMixin,
    QzoneMixin,
    TokenBudgetMixin,
    BalanceAwarenessMixin,
    WorldbookMixin,
    UserMemoryMixin,
    ContentCompanionBridgeMixin,
    CreativeMixin,
    ProactiveMixin,
    ProactiveEngineMixin,
    GameIntegrationMixin,
    PlaceCognitiveMapMixin,
    SceneContextMixin,
    ProactiveMessageMixin,
    ImageCompanionBridgeMixin,
    NAIImageBridgeMixin,
    DailyStateMixin,
    AgendaRuntimeMixin,
    DailyReviewMixin,
    StateViewsMixin,
    InteractionUtilsMixin,
    LlmToolActionsMixin,
    CommandHandlersMixin,
    WardrobeMixin,
    TtsEnhancementMixin,
    TtsToolSanitizerMixin,
    RealityCompanionBridgeMixin,
    GroupWakeupMixin,
    GroupObservationMixin,
    GroupMemberSafetyMixin,
    EventDispatchMixin,
    ReadingArchiveMixin,
    NewsExplorationMixin,
    SelfTimelineMixin,
    AtRelayMixin,
    PrivateCompanionPluginReactionExpressionMixin,
    PrivateCompanionPluginOutboundPersistenceMixin,
    PrivateCompanionPluginP5AttestationMixin,
    PrivateCompanionPluginPcLlmToolsMixin,
    PrivateCompanionPluginSqliteGroupResetMixin,
    PrivateCompanionPluginRestReplyMixin,
    PrivateCompanionPluginPromptFormattingMixin,
    PrivateCompanionPluginAtrelayRelayMixin,
    PrivateCompanionPluginGroupInboundCaptureMixin,
    PrivateCompanionPluginPersonaRoutingMixin,
    PrivateCompanionPluginOutboundGuardMixin,
    PrivateCompanionPluginPromptMixin,
    PrivateCompanionPluginReq041Mixin,
    Star,
):
    @filter.on_plugin_loaded()
    async def _on_external_plugin_loaded(self, metadata: Any, *args: Any, **kwargs: Any) -> None:
        # 任意插件装载后主动失效桥接缓存：运行中安装/重载可选扩展能立即被
        # 发现，未安装扩展的负向结果因此可以缓存到失效为止，不做周期重查。
        # metadata 是 AstrBot 传入的外部对象，这里不读取其内容。
        invalidate_external_bridge_cache(self)

    @filter.on_plugin_unloaded()
    async def _on_external_plugin_unloaded(self, metadata: Any, *args: Any, **kwargs: Any) -> None:
        # 卸载后立即清掉旧实例引用，避免正向缓存继续指向已卸载插件的
        # extension_api。同样只失效，不读取 metadata 内容。
        invalidate_external_bridge_cache(self)

    # AstrBot registers handlers from their exact defining module.  Keep the
    # implementations in EventDispatchMixin, but expose the decorated entry
    # points here so waiting/request/response form one complete pipeline.
    @_ON_WAITING_LLM_REQUEST(priority=110000)
    @_multi_persona_event_context
    async def route_model_replacement_before_agent_hook(
        self,
        event: AstrMessageEvent,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        await EventDispatchMixin.route_model_replacement_before_agent(
            self,
            event,
            *args,
            **kwargs,
        )

    @filter.on_llm_request(priority=110000)
    @_multi_persona_event_context
    async def enforce_model_replacement_request_hook(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        await EventDispatchMixin.enforce_model_replacement_request(
            self,
            event,
            req,
            *args,
            **kwargs,
        )

    @filter.command("HDSI", alias={"hdsi"})
    @_multi_persona_event_context
    async def hdsi_experiment_command(self, event: AstrMessageEvent, action: str = "状态"):
        event.stop_event()
        await self._reply(event, await hdsi_window_command(self, event, action))

    @filter.on_llm_request(priority=109000)
    @_multi_persona_event_context
    async def inject_hdsi_experiment_prompt(
        self, event: AstrMessageEvent, req: ProviderRequest, *args: Any, **kwargs: Any,
    ) -> None:
        try:
            await apply_hdsi_prompt(self, event, req)
        except Exception as exc:
            await record_trial_failure(self, event, type(exc).__name__)
            logger.warning("HDSI 表达试验处理失败: error_type=%s", type(exc).__name__)

    @filter.on_llm_response(priority=-99950)
    @_multi_persona_event_context
    async def finalize_hdsi_trial_response(self, event: AstrMessageEvent, resp: LLMResponse, *args: Any, **kwargs: Any) -> None:
        await finalize_trial_response(self, event, resp)
        try:
            await record_hdsi_outbound_event(self, event, resp)
        except Exception:
            # The event bridge is observational and must never alter delivery.
            pass

    @filter.on_llm_response(priority=-100000)
    @_multi_persona_event_context
    async def clear_model_replacement_context_hook(
        self,
        event: AstrMessageEvent,
        resp: LLMResponse,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        await EventDispatchMixin.clear_model_replacement_context(
            self,
            event,
            resp,
            *args,
            **kwargs,
        )

    @_ON_WAITING_LLM_REQUEST(priority=100000)
    @_multi_persona_event_context
    async def guard_pending_message_debounce_hook(
        self,
        event: AstrMessageEvent,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        await EventDispatchMixin.guard_pending_message_debounce(
            self,
            event,
            *args,
            **kwargs,
        )

    @filter.on_llm_response(priority=100000)
    @_multi_persona_event_context
    async def settle_pending_message_debounce_hook(
        self,
        event: AstrMessageEvent,
        resp: LLMResponse,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        await EventDispatchMixin.settle_pending_message_debounce(
            self,
            event,
            resp,
            *args,
            **kwargs,
        )

    # 与 astrbot_plugin_reality_companion 的 capability 契约一致；仅用于识别
    # 拆分前版本遗留在本插件用户数据中的摄像头待授权记录。
    _REALITY_TOUCH_CAMERA_CAPABILITY = "camera_single_frame"

    @staticmethod
    def _schema_runtime_default(
        key: str,
        fallback: Any,
        *,
        schema_types: frozenset[str] | None = None,
    ) -> Any:
        entry = _PERSONA_SETTING_MANIFEST.get(key)
        if (
            isinstance(entry, dict)
            and "default" in entry
            and (
                schema_types is None
                or str(entry.get("type") or "") in schema_types
            )
        ):
            return deepcopy(entry["default"])
        return deepcopy(fallback)

    @staticmethod
    def _cfg_raw(config: AstrBotConfig, key: str, default: Any = None) -> Any:
        # ``None`` is retained as the explicit missing-value probe used by
        # compatibility migrations. All ordinary public defaults come from
        # the schema manifest, not from call-site literals.
        effective = (
            default
            if default is None
            else PrivateCompanionPlugin._schema_runtime_default(key, default)
        )
        return _flat_get(config, key, effective)

    @staticmethod
    def _cfg_bool(config: AstrBotConfig, key: str, default: bool = True) -> bool:
        effective = PrivateCompanionPlugin._schema_runtime_default(
            key,
            default,
            schema_types=frozenset({"bool"}),
        )
        value = _flat_get(config, key, effective)
        if isinstance(value, str):
            text = value.strip().lower()
            parsed: bool | None = None
            if text in {"true", "1", "yes", "y", "on", "enable", "enabled", "启用", "开启", "开", "是"}:
                parsed = True
            elif text in {"false", "0", "no", "n", "off", "disable", "disabled", "停用", "关闭", "关", "否", ""}:
                parsed = False
            if parsed is not None:
                _set_into_config(config, key, parsed)
                return parsed
        return bool(value)

    @staticmethod
    def _cfg_str(config: AstrBotConfig, key: str, default: str = "", fallback: str = "") -> str:
        effective = PrivateCompanionPlugin._schema_runtime_default(
            key,
            default,
            schema_types=frozenset({"string", "text"}),
        )
        return str(_flat_get(config, key, effective)).strip() or fallback

    @staticmethod
    def _cfg_int(config: AstrBotConfig, key: str, default: int, minimum: int = 0, maximum: int | None = None) -> int:
        effective = PrivateCompanionPlugin._schema_runtime_default(
            key,
            default,
            schema_types=frozenset({"int"}),
        )
        return _safe_int(
            _flat_get(config, key, effective),
            effective,
            minimum,
            maximum,
        )

    @staticmethod
    def _cfg_float(
        config: AstrBotConfig,
        key: str,
        default: float,
        minimum: float = 0.0,
        maximum: float | None = None,
    ) -> float:
        effective = PrivateCompanionPlugin._schema_runtime_default(
            key,
            default,
            schema_types=frozenset({"float", "int"}),
        )
        return _safe_float(
            _flat_get(config, key, effective),
            effective,
            minimum,
            maximum,
        )

    @staticmethod
    def _cfg_unit_interval(config: AstrBotConfig, key: str, default: float, minimum: float = 0.0) -> float:
        effective = PrivateCompanionPlugin._schema_runtime_default(
            key,
            default,
            schema_types=frozenset({"float", "int"}),
        )
        original = _safe_float(
            _flat_get(config, key, effective),
            effective,
            minimum,
        )
        value = original / 100.0 if original > 1.0 else original
        value = max(minimum, min(1.0, value))
        if value != original:
            _set_into_config(config, key, value)
        return value

    @property
    def data(self) -> dict[str, Any]:
        """Return the profile store bound to the current event task."""
        active = _ACTIVE_PERSONA_ID.get()
        if active and bool(getattr(self, "enable_multi_persona_mode", False)):
            if self._sanitize_persona_id(active) == self._primary_persona_id():
                return getattr(self, "_data_default", {})
            profiles = getattr(self, "_persona_data_profiles", {})
            profile = profiles.get(active) if isinstance(profiles, dict) else None
            if isinstance(profile, dict):
                return profile
            ensure_profile = getattr(self, "_ensure_persona_profile", None)
            if callable(ensure_profile):
                profile = ensure_profile(active)
                if isinstance(profile, dict):
                    return profile
            factory = getattr(self, "_new_store", None)
            profile = factory() if callable(factory) else {}
            if not isinstance(profiles, dict):
                profiles = {}
                self._persona_data_profiles = profiles
            profiles[active] = profile
            return profile
        return getattr(self, "_data_default", {})

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

    # ------------------------------------------------------------------
    # 手机端陪伴形象：Bot 称呼的唯一数据源在这里，终端只是远程入口
    # ------------------------------------------------------------------

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

    @data.setter
    def data(self, value: dict[str, Any]) -> None:
        active = _ACTIVE_PERSONA_ID.get()
        if active and bool(getattr(self, "enable_multi_persona_mode", False)):
            if self._sanitize_persona_id(active) == self._primary_persona_id():
                self._data_default = value if isinstance(value, dict) else {}
                return
            profiles = getattr(self, "_persona_data_profiles", None)
            if profiles is None:
                profiles = {}
                self._persona_data_profiles = profiles
            profiles[active] = value if isinstance(value, dict) else {}
            return
        self._data_default = value if isinstance(value, dict) else {}

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


    @staticmethod
    async def _await_if_needed(value: Any) -> Any:
        return await value if inspect.isawaitable(value) else value

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

    async def _astrbot_effective_persona_for_event(self, event: Any) -> dict[str, Any]:
        """Resolve AstrBot's request persona using AstrBot's own precedence."""
        umo = str(getattr(event, "unified_msg_origin", "") or "").strip()
        result = {
            "persona_id": "",
            "source": "unresolved",
            "exists": False,
            "explicit_none": False,
            "umo": umo,
            "error": "",
        }
        if not umo:
            result["error"] = "umo_missing"
            return result
        context = getattr(self, "context", None)
        conversation_persona = None
        try:
            conversation_manager = getattr(context, "conversation_manager", None)
            if conversation_manager is not None:
                cid = await self._await_if_needed(
                    conversation_manager.get_curr_conversation_id(umo)
                )
                if cid:
                    conversation = await self._await_if_needed(
                        conversation_manager.get_conversation(umo, cid)
                    )
                    conversation_persona = getattr(conversation, "persona_id", None)

            config_getter = getattr(context, "get_config", None)
            try:
                astrbot_config = config_getter(umo=umo) if callable(config_getter) else {}
            except TypeError:
                astrbot_config = config_getter() if callable(config_getter) else {}
            provider_settings = (
                astrbot_config.get("provider_settings", {})
                if isinstance(astrbot_config, dict)
                else {}
            )
            platform_getter = getattr(event, "get_platform_name", None)
            platform_name = (
                str(platform_getter() or "") if callable(platform_getter) else ""
            )
            persona_manager = getattr(context, "persona_manager", None)
            resolver = getattr(persona_manager, "resolve_selected_persona", None)
            if callable(resolver):
                resolved = await self._await_if_needed(
                    resolver(
                        umo=umo,
                        conversation_persona_id=conversation_persona,
                        platform_name=platform_name,
                        provider_settings=provider_settings,
                    )
                )
                selected = resolved[0] if isinstance(resolved, (list, tuple)) and resolved else ""
                persona = resolved[1] if isinstance(resolved, (list, tuple)) and len(resolved) > 1 else None
                forced = resolved[2] if isinstance(resolved, (list, tuple)) and len(resolved) > 2 else ""
                pid = self._sanitize_persona_id(selected)
                result.update(
                    {
                        "persona_id": pid,
                        "source": (
                            "session_rule"
                            if forced
                            else "explicit_none"
                            if conversation_persona == "[%None]"
                            else "conversation"
                            if conversation_persona
                            else "provider_default"
                        ),
                        "exists": persona is not None,
                        "explicit_none": selected == "[%None]" or conversation_persona == "[%None]",
                    }
                )
                return result

            selected = conversation_persona
            source = "conversation"
            if selected is None:
                selected = provider_settings.get("default_personality")
                source = "provider_default"
            result.update(
                {
                    "persona_id": self._sanitize_persona_id(selected),
                    "source": "explicit_none" if selected == "[%None]" else source,
                    "exists": self._astrbot_persona_exists(selected),
                    "explicit_none": selected == "[%None]",
                    "error": "astrbot_effective_persona_resolver_unavailable",
                }
            )
        except Exception as exc:
            result["error"] = _single_line(exc, 160) or "persona_resolution_failed"
        return result

    async def _conversation_persona_id_for_event(self, event: Any) -> str:
        """Compatibility wrapper returning AstrBot's final effective persona."""
        resolved = await self._astrbot_effective_persona_for_event(event)
        return self._sanitize_persona_id(resolved.get("persona_id"))

    async def _validate_proactive_persona_delivery(
        self,
        target_umo: Any,
        scheduled_persona_id: Any,
    ) -> dict[str, Any]:
        """Validate the last-mile target without changing AstrBot's routing."""
        umo = _single_line(target_umo, 240)
        multi = bool(getattr(self, "enable_multi_persona_mode", False))
        primary = self._primary_persona_id()
        scheduled = self._sanitize_persona_id(
            scheduled_persona_id
            or _ACTIVE_PERSONA_ID.get()
            or ("" if multi else primary)
        )
        # In single-persona mode an empty plugin_specific_persona_id means
        # “use AstrBot's current/default persona”, not a routing problem.
        # AstrBot remains the authority for the effective conversation persona.
        if not multi and not primary and umo:
            return {
                "ok": True,
                "action": "matched",
                "astrbot_persona_id": "",
                "scheduled_persona_id": "",
                "reason_code": "",
            }
        event = SimpleNamespace(
            unified_msg_origin=umo,
            get_platform_name=lambda: umo.partition(":")[0],
        )
        resolved = await self._astrbot_effective_persona_for_event(event)
        astrbot_persona = self._sanitize_persona_id(resolved.get("persona_id"))
        reason = ""
        if not umo:
            reason = "target_umo_missing"
        elif not astrbot_persona:
            reason = "astrbot_persona_unresolved"
        elif not resolved.get("exists"):
            reason = "astrbot_persona_missing"
        elif not scheduled:
            reason = "scheduled_persona_missing"
        elif astrbot_persona != scheduled:
            reason = "target_persona_mismatch"
        elif multi:
            ready, readiness_reason = self._persona_profile_route_status(scheduled)
            if not ready:
                reason = readiness_reason

        if not reason:
            await self._resolve_persona_routing_warnings(
                channel="proactive",
                window_key=umo,
                warning_families={"proactive_delivery"},
            )
            return {
                "ok": True,
                "action": "matched",
                "astrbot_persona_id": astrbot_persona,
                "scheduled_persona_id": scheduled,
                "reason_code": "",
            }
        if not multi:
            await self._record_persona_routing_warning(
                code="persona.route.proactive_single_mismatch_allowed",
                channel="proactive",
                disposition="sent_with_warning",
                reason_code=reason,
                window_key=umo,
                requested_persona_id=astrbot_persona,
                resolved_persona_id=scheduled,
                active_persona_id=scheduled,
            )
            return {
                "ok": True,
                "action": "sent_with_warning",
                "astrbot_persona_id": astrbot_persona,
                "scheduled_persona_id": scheduled,
                "reason_code": reason,
            }
        await self._record_persona_routing_warning(
            code="persona.route.proactive_multi_mismatch_blocked",
            channel="proactive",
            disposition="blocked",
            reason_code=reason,
            window_key=umo,
            requested_persona_id=astrbot_persona,
            resolved_persona_id=scheduled,
            active_persona_id=scheduled,
        )
        return {
            "ok": False,
            "action": "blocked",
            "astrbot_persona_id": astrbot_persona,
            "scheduled_persona_id": scheduled,
            "reason_code": reason,
        }

    async def _record_persona_routing_warning(
        self,
        *,
        code: str,
        channel: str,
        disposition: str,
        reason_code: str,
        window_key: Any = "",
        requested_persona_id: Any = "",
        resolved_persona_id: Any = "",
        active_persona_id: Any = "",
    ) -> None:
        """Persist one global, content-free persona routing diagnostic."""
        now = time.time()
        window = _single_line(window_key, 180)
        requested = self._sanitize_persona_id(requested_persona_id)
        resolved = self._sanitize_persona_id(resolved_persona_id)
        active = self._sanitize_persona_id(active_persona_id)
        reason = _single_line(reason_code, 80) or "unknown"
        normalized_code = _single_line(code, 100)
        normalized_channel = _single_line(channel, 24)
        warning_family = self._persona_routing_warning_family(
            normalized_code,
            normalized_channel,
        )

        signature = "|".join((normalized_code, reason, normalized_channel, window))
        record_id = hashlib.sha256(signature.encode("utf-8")).hexdigest()[:20]
        should_schedule = False
        lock = getattr(self, "_data_lock", None)

        async def update() -> None:
            nonlocal should_schedule
            store = getattr(self, "_data_default", None)
            if not isinstance(store, dict):
                return
            root = store.get("persona_routing_warnings")
            if not isinstance(root, dict):
                root = {"schema_version": 2, "items": []}
                store["persona_routing_warnings"] = root
            root["schema_version"] = 2
            items = root.get("items")
            if not isinstance(items, list):
                items = []
                root["items"] = items
            item = next(
                (
                    candidate
                    for candidate in items
                    if isinstance(candidate, dict)
                    and (
                        candidate.get("id") == record_id
                        or (
                            _single_line(candidate.get("code"), 100) == normalized_code
                            and _single_line(candidate.get("reason_code"), 80) == reason
                            and _single_line(candidate.get("channel"), 24) == normalized_channel
                            and _single_line(candidate.get("window_key"), 180) == window
                        )
                    )
                ),
                None,
            )
            if item is None:
                item = next(
                    (
                        candidate
                        for candidate in items
                        if isinstance(candidate, dict)
                        and not self._persona_routing_warning_is_active(candidate)
                        and _single_line(candidate.get("channel"), 24) == normalized_channel
                        and _single_line(candidate.get("window_key"), 180) == window
                        and (
                            _single_line(candidate.get("warning_family"), 80)
                            or self._persona_routing_warning_family(
                                candidate.get("code"),
                                candidate.get("channel"),
                            )
                        )
                        == warning_family
                    ),
                    None,
                )
            if item is None:
                item = {
                    "id": record_id,
                    "first_ts": now,
                    "count": 0,
                    "lifetime_count": 0,
                }
                items.append(item)
                should_schedule = True
            previous_last_ts = _safe_float(item.get("last_ts"), 0.0)
            was_active = self._persona_routing_warning_is_active(item) and (
                previous_last_ts > 0 and now - previous_last_ts <= 2 * 60 * 60
            )
            previous_episode_count = _safe_int(item.get("count"), 0, 0)
            previous_lifetime_count = max(
                previous_episode_count,
                _safe_int(item.get("lifetime_count"), 0, 0),
            )
            if not was_active:
                item["first_ts"] = now
                item["count"] = 0
                should_schedule = True
            current_episode_count = previous_episode_count + 1 if was_active else 1
            item.update(
                {
                    "schema_version": 2,
                    "code": normalized_code,
                    "level": "error" if disposition == "blocked" else "warn",
                    "channel": normalized_channel,
                    "warning_family": warning_family,
                    "disposition": _single_line(disposition, 24),
                    "reason_code": reason,
                    "source": "persona_router",
                    "window_key": window,
                    "requested_persona_id": requested,
                    "resolved_persona_id": resolved,
                    "active_persona_id": active,
                    "primary_persona_id": self._primary_persona_id(),
                    "multi_persona": bool(getattr(self, "enable_multi_persona_mode", False)),
                    "status": "active",
                    "resolved_ts": 0,
                    "last_ts": now,
                    "count": current_episode_count,
                    "lifetime_count": previous_lifetime_count + 1,
                }
            )
            items.sort(
                key=lambda candidate: _safe_float(
                    candidate.get("last_ts") if isinstance(candidate, dict) else 0,
                    0.0,
                ),
                reverse=True,
            )
            del items[120:]
            save_marks = getattr(self, "_persona_routing_warning_save_marks", None)
            if not isinstance(save_marks, dict):
                save_marks = {}
                self._persona_routing_warning_save_marks = save_marks
            previous_save = _safe_float(save_marks.get(record_id), 0.0)
            if now - previous_save >= 60:
                save_marks[record_id] = now
                should_schedule = True

        if isinstance(lock, asyncio.Lock):
            async with lock:
                await update()
        else:
            await update()
        if should_schedule:
            scheduler = getattr(self, "_schedule_default_data_save", None)
            if callable(scheduler):
                clear_token = _ACTIVE_PERSONA_ID.set("")
                try:
                    scheduler(sections={"persona_routing_warnings"}, delay=0.2)
                finally:
                    _ACTIVE_PERSONA_ID.reset(clear_token)
        log_marks = getattr(self, "_persona_routing_warning_log_marks", None)
        if not isinstance(log_marks, dict):
            log_marks = {}
            self._persona_routing_warning_log_marks = log_marks
        if now - _safe_float(log_marks.get(record_id), 0.0) >= 300:
            log_marks[record_id] = now
            logger.warning(
                "人格路由告警 code=%s reason=%s umo=%s astrbot=%s plugin=%s action=%s",
                code,
                reason,
                window or "-",
                requested or "-",
                active or "-",
                disposition,
            )

    async def _resolve_persona_routing_warnings(
        self,
        *,
        channel: str,
        window_key: Any,
        warning_families: set[str],
    ) -> int:
        """Resolve active routing warnings after the same route becomes healthy."""
        now = time.time()
        normalized_channel = _single_line(channel, 24)
        window = _single_line(window_key, 180)
        families = {
            _single_line(value, 80)
            for value in warning_families
            if _single_line(value, 80)
        }
        if not normalized_channel or not families:
            return 0
        resolved_count = 0
        lock = getattr(self, "_data_lock", None)

        async def update() -> None:
            nonlocal resolved_count
            store = getattr(self, "_data_default", None)
            if not isinstance(store, dict):
                return
            root = store.get("persona_routing_warnings")
            if not isinstance(root, dict):
                return
            items = root.get("items")
            if not isinstance(items, list):
                return
            for item in items:
                if not self._persona_routing_warning_is_active(item):
                    continue
                item_channel = _single_line(item.get("channel"), 24)
                item_window = _single_line(item.get("window_key"), 180)
                family = _single_line(item.get("warning_family"), 80) or self._persona_routing_warning_family(
                    item.get("code"), item_channel
                )
                if item_channel != normalized_channel or item_window != window or family not in families:
                    continue
                item.update(
                    {
                        "schema_version": 2,
                        "warning_family": family,
                        "status": "resolved",
                        "resolved_ts": now,
                    }
                )
                resolved_count += 1
            if resolved_count:
                root["schema_version"] = 2

        if isinstance(lock, asyncio.Lock):
            async with lock:
                await update()
        else:
            await update()
        if resolved_count:
            scheduler = getattr(self, "_schedule_default_data_save", None)
            if callable(scheduler):
                clear_token = _ACTIVE_PERSONA_ID.set("")
                try:
                    scheduler(sections={"persona_routing_warnings"}, delay=0.2)
                finally:
                    _ACTIVE_PERSONA_ID.reset(clear_token)
        return resolved_count

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

    def __init__(self, context: Context, config: AstrBotConfig):
        self._private_companion_instance_guard_enabled = True
        self._private_companion_duplicate_instance = False
        super().__init__(context)
        initialize_plugin_entrypoint_state(
            self,
            context,
            config,
            extension_api_factory=PrivateCompanionExtensionAPI,
        )
        initialize_plugin_config(self, config)
        initialize_plugin_runtime(self)
        initialize_plugin_post_runtime_state(self, config)
        assemble_plugin_dependencies(
            self,
            observability_factory=Req041Observability,
        )

    def _initialize_lab_fixture_adapter(self) -> None:
        try:
            self._lab_fixture_adapter = register_companion_lab_fixture_adapter()
        except Exception as exc:
            self._lab_fixture_adapter = None
            logger.warning(
                "LAB fixture 门控注册失败，已保持生产路径关闭: %s",
                type(exc).__name__,
            )

    def _lab_fixture_relationship_view(self, event: Any, user: Any) -> Any:
        adapter = getattr(self, "_lab_fixture_adapter", None)
        overlay = getattr(adapter, "overlay_relationship_view", None)
        if not callable(overlay):
            return user
        try:
            return overlay(event, user)
        except Exception as exc:
            logger.warning(
                "LAB fixture 关系投影失败，已放行原始生产视图: %s",
                type(exc).__name__,
            )
            return user

    async def _pull_body_monitor_candidates(self) -> dict[str, Any]:
        integration = getattr(self, "_body_monitor_integration", None)
        if integration is None:
            return {}
        return await integration.poll()

    def _body_monitor_integration_status_view(self) -> dict[str, Any]:
        integration = getattr(self, "_body_monitor_integration", None)
        if integration is None:
            return {
                "enabled": bool(getattr(self, "enable_body_monitor_integration", False)),
                "state": "initializing",
                "status": "initializing",
            }
        return integration.status_view()

    def _format_body_monitor_health_prompt(self, user: dict[str, Any], *, reason: str = "") -> str:
        integration = getattr(self, "_body_monitor_integration", None)
        if integration is None:
            return ""
        return integration.format_health_prompt(user, reason=reason)

    def plugin_identity_status(self) -> dict[str, Any]:
        return dict(self.plugin_identity)

    def _extension_task_count(self) -> int:
        """Count live companion-owned tasks for the control-plane snapshot."""
        tasks: set[asyncio.Task] = set()
        for candidate in (
            getattr(self, "_task", None),
            getattr(self, "_startup_maintenance_task", None),
            getattr(self, "_req041_replay_task", None),
            getattr(self, "_req041_scoped_sync_task", None),
            getattr(self, "_termination_save_task", None),
        ):
            if isinstance(candidate, asyncio.Task) and not candidate.done():
                tasks.add(candidate)
        for registry_name in (
            "_startup_background_tasks",
            "_lifecycle_background_tasks",
            "_passive_input_status_tasks",
            "_group_image_understanding_tasks",
            "_troubleshooting_proactive_wakeup_tasks",
        ):
            registry = getattr(self, registry_name, {})
            if isinstance(registry, dict):
                values = registry.keys() if registry_name == "_lifecycle_background_tasks" else registry.values()
                for value in values:
                    task = value.get("task") if isinstance(value, dict) else value
                    if isinstance(task, asyncio.Task) and not task.done():
                        tasks.add(task)
        return len(tasks)

    def runtime_compatibility_status(self) -> dict[str, Any]:
        return self.runtime_capabilities.to_dict()

    def bot_personal_capability_status(self) -> dict[str, Any]:
        return dict(self.bot_personal_capabilities)

    def _active_unified_person_registry(self) -> UnifiedPersonRegistry:
        """Bind identity operations to the store selected by the current persona context."""
        store = self.data
        registry = getattr(self, "unified_person_registry", None)
        if isinstance(registry, UnifiedPersonRegistry) and registry.is_bound_to(store):
            return registry
        return UnifiedPersonRegistry(store)

    def _req036_source_event_anchor(self, event: Any) -> str:
        """Build an event-local, content-free anchor when an adapter omits message IDs."""
        cached = _single_line(
            getattr(event, "_private_companion_req036_source_event_anchor", ""),
            80,
        )
        if cached:
            return cached

        raw_reader = getattr(self, "_event_raw_payload", None)
        try:
            raw = raw_reader(event) if callable(raw_reader) else {}
        except Exception:
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
        message_obj = getattr(event, "message_obj", None)
        metadata: dict[str, str] = {
            "origin": _single_line(getattr(event, "unified_msg_origin", ""), 200),
            "event_type": _single_line(type(event).__qualname__, 120),
            "runtime_event_ref": f"{id(event):x}",
        }
        for key in (
            "post_type",
            "message_type",
            "notice_type",
            "sub_type",
            "time",
            "timestamp",
            "user_id",
            "group_id",
            "self_id",
        ):
            value = _single_line(raw.get(key), 120)
            if value:
                metadata[f"raw_{key}"] = value
        for attr in ("time", "timestamp"):
            value = _single_line(getattr(message_obj, attr, ""), 120)
            if value:
                metadata[f"message_{attr}"] = value
            event_value = _single_line(getattr(event, attr, ""), 120)
            if event_value:
                metadata[f"event_{attr}"] = event_value

        inbound_ts = _single_line(
            getattr(event, "_private_companion_inbound_ts", ""),
            80,
        )
        if not inbound_ts:
            inbound_reader = getattr(self, "_event_inbound_activity_ts", None)
            try:
                inbound_ts = _single_line(
                    inbound_reader(event) if callable(inbound_reader) else _now_ts(),
                    80,
                )
            except Exception:
                inbound_ts = _single_line(_now_ts(), 80)
        metadata["observed_at"] = inbound_ts

        anchor = hashlib.sha256(
            json.dumps(
                metadata,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        try:
            setattr(event, "_private_companion_req036_source_event_anchor", anchor)
        except Exception:
            pass
        return anchor

    def create_unified_person(
        self,
        identity: dict[str, Any],
        *,
        profile: dict[str, Any] | None = None,
        operation_id: str = "",
    ) -> dict[str, Any]:
        registry = self._active_unified_person_registry()
        result = registry.create_or_link(
            identity,
            profile=profile,
            operation_id=operation_id,
            actor_id="companion",
        )
        self._req041_emit_identity_dual_write(
            result,
            action="create",
            operation_id=operation_id,
            registry=registry,
        )
        return result

    def get_unified_person_projection(self, person_id: str) -> dict[str, Any] | None:
        return self._active_unified_person_registry().read_projection(person_id)

    def _req036_private_gate_for_user(self, user: Any) -> dict[str, Any]:
        return req036_private_companion_gate(user)

    def _req036_migrate_configured_target_capability(self, user_id: Any, user: Any) -> bool:
        """Repair migration-only false gates for configured targets and legacy owners."""
        if not isinstance(user, dict):
            return False
        if bool(user.get("manual_disabled")):
            return False
        canonicalizer = getattr(self, "_canonical_private_user_id", None)
        identity_normalizer = getattr(self, "_normalize_private_identity_id", None)

        def normalized_identity(value: Any) -> str:
            candidate = _single_line(value, 160)
            if callable(identity_normalizer):
                try:
                    candidate = _single_line(identity_normalizer(candidate), 160) or candidate
                except Exception:
                    return ""
            if callable(canonicalizer):
                try:
                    candidate = _single_line(canonicalizer(candidate), 160)
                except Exception:
                    return ""
            return candidate

        configured_targets = getattr(self, "_configured_target_ids", None)
        target_ids: set[str] = set()
        try:
            if callable(configured_targets):
                target_ids = {
                    target_id
                    for target_id in (normalized_identity(target) for target in configured_targets())
                    if target_id
                }
        except Exception:
            return False

        stamped_subject = normalized_identity(user.get("identity_subject_id"))
        if stamped_subject:
            # Once a record has a stamped platform subject, its storage key
            # and historical transport aliases are no longer authorization
            # candidates. This prevents a target-named shadow row from
            # inheriting the target's active capability for another person.
            identity_candidates = {stamped_subject}
        else:
            identity_candidates = {
                candidate
                for candidate in (
                    normalized_identity(user_id),
                    normalized_identity(user.get("user_id")),
                )
                if candidate
            }
        # ``alias_user_ids`` are transport/history hints, never authorization
        # credentials.  Using them here allowed a renamed or migrated identity
        # to reopen active permission for another stable user.
        configured_match = bool(target_ids.intersection(identity_candidates))

        # A bare numeric/openid target must not grant the same identifier on a
        # different platform. Adapter instance changes inside the configured
        # platform remain compatible and are handled by the scoped profile.
        if configured_match:
            platform_normalizer = getattr(self, "_normalize_platform_kind", None)
            configured_platform_raw = _single_line(getattr(self, "target_platform", ""), 80).lower()
            observed_platform = _single_line(user.get("identity_platform_kind"), 40).lower()
            configured_platform = ""
            if callable(platform_normalizer) and configured_platform_raw:
                try:
                    configured_platform = _single_line(platform_normalizer(configured_platform_raw), 40).lower()
                except Exception:
                    configured_platform = ""
            if (
                configured_platform
                and configured_platform != "generic"
                and observed_platform
                and observed_platform != "generic"
                and configured_platform != observed_platform
            ):
                configured_match = False
            elif configured_platform == "generic" and configured_platform_raw:
                observed_adapter = _single_line(user.get("identity_adapter_instance_id"), 120).lower()
                if observed_adapter and configured_platform_raw not in {observed_adapter, observed_adapter.split(":", 1)[0]}:
                    configured_match = False

        owner_match = False
        if not configured_match:
            return False

        capabilities = user.get("unified_profile_capabilities")
        if isinstance(capabilities, dict) and capabilities.get("private_companion_enabled") is True:
            return False
        grant_source = (
            _single_line(capabilities.get("grant_source"), 80).lower()
            if isinstance(capabilities, dict)
            else ""
        )
        explicit_sources = {
            "admin",
            "administrator",
            "manual",
            "page_administrator",
            "page_administrator_update",
        }
        if grant_source in explicit_sources or "administrator" in grant_source:
            return False

        # Audit provenance is authoritative even when an older writer failed
        # to keep grant_source synchronized.
        audit = user.get("unified_profile_capability_audit")
        if isinstance(audit, list):
            for entry in reversed(audit[-64:]):
                if not isinstance(entry, dict):
                    continue
                changed = entry.get("changed")
                private_change = changed.get("private_companion_enabled") if isinstance(changed, dict) else None
                if not isinstance(private_change, dict) or "to" not in private_change:
                    continue
                if private_change.get("to") is True:
                    break
                actor = _single_line(entry.get("actor_id"), 80).lower()
                reason = _single_line(entry.get("reason_code"), 80).lower()
                compatibility_change = any(
                    token in f"{actor} {reason}"
                    for token in ("migration", "compatibility", "reconciliation", "startup")
                )
                if private_change.get("to") is False and not compatibility_change:
                    return False
                break

        repairable_sources = {
            "",
            "default_closed",
            "group_observation",
            "legacy_effective_migration",
            "legacy_configured_target_migration",
            "owner_default_enabled",
        }
        if (
            isinstance(capabilities, dict)
            and grant_source not in repairable_sources
            and not bool(user.get("manual_enabled"))
        ):
            return False

        proactive_enabled = bool(
            owner_match
            or (isinstance(capabilities, dict) and capabilities.get("proactive_private_enabled") is True)
            or user.get("proactive_private_enabled") is True
            or _safe_int(user.get("proactive_daily_limit"), 0, 0) > 0
        )
        source = (
            "owner_capability_reconciliation"
            if owner_match and not configured_match
            else "configured_target_capability_reconciliation"
            if isinstance(capabilities, dict)
            else "legacy_configured_target_migration"
        )
        result = req036_update_capabilities(
            user,
            {
                "private_companion_enabled": True,
                "proactive_private_enabled": proactive_enabled,
            },
            actor_authorized=True,
            grant_source=source,
            actor_id="compatibility_migration",
            target_identity=normalized_identity(user.get("identity_subject_id")) or normalized_identity(user_id),
            reason_code=source,
        )
        return bool(result.get("ok"))

    def _req036_capability_summary_for_user(self, user: Any) -> dict[str, Any]:
        bridge = self._memory_companion_bridge()
        portrait_backend_available = callable(getattr(bridge, "read_unified_profile_portrait", None))
        return req036_capability_summary(
            user,
            global_portrait_mode=runtime_persona_setting(self, 'portrait_global_mode', "disabled"),
            portrait_backend_available=portrait_backend_available,
        )

    def _req036_proactive_private_allowed(self, user: Any) -> bool:
        return bool(req036_proactive_private_gate(user).get("allowed"))

    def _req036_update_capabilities(
        self,
        user: dict[str, Any],
        changes: dict[str, Any],
        *,
        actor_id: str = "page_administrator",
        target_identity: str = "",
        reason_code: str = "administrator_update",
    ) -> dict[str, Any]:
        requested_mode = _single_line(changes.get("portrait_mode"), 40).lower() if isinstance(changes, dict) else ""
        if requested_mode and requested_mode not in {"disabled", "off", "follow_global"}:
            bridge = self._memory_companion_bridge()
            if not callable(getattr(bridge, "read_unified_profile_portrait", None)):
                return {
                    "ok": False,
                    "code": "memory_companion_required",
                    "message": "需要安装并启用 MemoryCompanion",
                    "capabilities": self._req036_capability_summary_for_user(user),
                }
        return req036_update_capabilities(
            user,
            changes,
            actor_authorized=True,
            grant_source="administrator",
            actor_id=actor_id,
            target_identity=target_identity,
            reason_code=reason_code,
        )

    def _req036_attach_unified_profile_context(
        self,
        event: Any,
        *,
        user: dict[str, Any] | None = None,
        group_id: str = "",
        source: str = "observation",
    ) -> dict[str, Any]:
        """Attach the smallest exact-person context for Memory's read-only use."""
        identity = self._unified_person_event_identity(event)
        if not identity:
            return {"state": "identity_pending", "code": "identity_pending"}
        resolution = self.resolve_unified_person_identity(identity)
        if resolution.get("state") != "resolved":
            profile = user if isinstance(user, dict) else {}
            created = self.create_unified_person_for_event(
                event,
                operation_id=f"req036.{source}:{str(resolution.get('identity_key') or '')[-24:]}",
                profile={
                    "display_name": (
                        _single_line(profile.get("nickname"), 80) if not group_id else ""
                    ),
                    "preferred_address": (
                        _single_line(profile.get("nickname"), 40) if not group_id else ""
                    ),
                    "style": _single_line(profile.get("style"), 40) if not group_id else "",
                    "profile_origin": _single_line(profile.get("profile_origin"), 60),
                    "auto_profile_created": bool(profile.get("auto_profile_created", False)),
                    "affinity_score": _safe_int(profile.get("relationship_score"), 0, -1200, 1200),
                    "owner_mode": "owner" if _single_line(profile.get("relationship_role"), 40) == "owner" else "not_owner",
                    "relation_policy_id": _single_line(profile.get("relationship_mode"), 40) or "default_friend",
                },
            )
            if created.get("state") != "resolved":
                return {"state": "identity_pending", "code": str(created.get("code") or "identity_pending")}
            resolution = self.resolve_unified_person_identity(identity)
        projection = resolution.get("projection") if isinstance(resolution.get("projection"), dict) else None
        if not isinstance(projection, dict):
            return {"state": "identity_pending", "code": "projection_missing"}
        person_id = _single_line(projection.get("person_id"), 80)
        if isinstance(user, dict) and person_id:
            user["unified_person_id"] = person_id
            user["unified_profile_projection_revision"] = int(projection.get("projection_revision") or 1)
            if not group_id:
                private_facts = {
                    "style": _single_line(user.get("style"), 40),
                    "profile_origin": _single_line(user.get("profile_origin"), 60),
                    "auto_profile_created": bool(user.get("auto_profile_created", False)),
                }
                private_name = _single_line(user.get("nickname"), 80)
                if private_name:
                    private_facts["display_name"] = private_name
                    private_facts["preferred_address"] = private_name[:40]
                fact_signature = hashlib.sha256(
                    json.dumps(private_facts, ensure_ascii=False, sort_keys=True).encode("utf-8")
                ).hexdigest()[:32]
                self._req041_update_unified_profile_facts(
                    user,
                    private_facts,
                    operation_id=f"req041-private-profile-observation-{person_id[-16:]}-{fact_signature}",
                    actor_id="private_observation",
                    schedule_save=True,
                )
        group_scope = ""
        if group_id and person_id:
            platform = _single_line(identity.get("subject_namespace"), 80).split(":", 1)[0]
            group_scope = self._unified_wire_group_scope(platform, group_id)
            if group_scope:
                self._active_unified_person_registry().upsert_group_overlay(
                    person_id,
                    group_scope,
                    {
                        "alias": _single_line((user or {}).get("nickname"), 80),
                        "source": "group_observation",
                        "public": True,
                    },
                    operation_id=f"req036.overlay:{person_id[-12:]}:{_single_line(group_id, 40)}",
                    actor_id="companion",
                )
        if person_id:
            event_id = _single_line(self._event_message_id(event), 120)
            event_anchor = event_id or self._req036_source_event_anchor(event)
            source_fingerprint = hashlib.sha256(
                f"req036:{source}:{group_scope or 'private'}:{event_anchor}".encode("utf-8", errors="ignore")
            ).hexdigest()
            self._active_unified_person_registry().record_identity_source_event(
                person_id,
                _single_line(projection.get("resolved_identity_key"), 160),
                group_scope or "private",
                source_fingerprint,
                operation_id=f"req036.source:{source}:{source_fingerprint[:24]}",
            )
        portrait_namespace_getter = getattr(self, "_req041_scoped_context_for_user", None)
        if callable(portrait_namespace_getter) and isinstance(user, dict):
            try:
                portrait_namespace = portrait_namespace_getter(
                    user,
                    kind="group_member" if group_id else "private",
                    group_id=group_id,
                    purpose="profile_read",
                )
            except Exception:
                portrait_namespace = None
            if isinstance(portrait_namespace, NamespaceContext) and not portrait_namespace.errors():
                try:
                    setattr(
                        event,
                        "private_companion_namespace_context",
                        portrait_namespace.to_dict(),
                    )
                except Exception:
                    pass
        dto = req036_build_profile_dto(
            person_ref=req036_build_person_ref(projection),
            identity_summary={"display_name": _single_line((user or {}).get("nickname"), 80)},
            expression_summary={
                "relationship_score": _safe_int((user or {}).get("relationship_score"), 0, -1200, 1200),
                "relationship_role": _single_line((user or {}).get("relationship_role"), 40) or "friend",
            },
            capability_summary=self._req036_capability_summary_for_user(user),
            context_overlays={"group_scope": group_scope} if group_scope else {},
            bridge_status={"state": "ready", "source": "companion"},
        )
        errors = req036_validate_profile_dto(dto)
        if errors:
            return {"state": "degraded", "code": "bridge_contract_mismatch", "errors": errors}
        try:
            setattr(event, "private_companion_unified_profile_context", dto)
        except Exception:
            return {"state": "degraded", "code": "bridge_unavailable"}
        return {"state": "profile_exact", "code": "profile_exact", "dto": dto, "person_id": person_id}

    async def _req036_reject_unauthorized_private_event(self, event: Any, gate: dict[str, Any]) -> None:
        """Reply before any LLM, bridge, tool, portrait, or relationship path."""
        inbound_checker = getattr(self, "_event_is_inbound_chat_message", None)
        if callable(inbound_checker) and not inbound_checker(event):
            return
        if bool(getattr(event, "private_companion_req036_denied", False)):
            try:
                event.stop_event()
            except Exception:
                pass
            return
        try:
            setattr(event, "private_companion_req036_denied", True)
            setattr(event, "private_companion_req036_denial_code", str(gate.get("code") or "private_companion_disabled"))
        except Exception:
            pass
        try:
            event.stop_event()
        except Exception:
            pass

        # Adapter redelivery can reconstruct the same genuine message as a
        # different event object.  Deduplicate only by its stable platform
        # message identity; this is not a time-based user rate limit.
        message_id = ""
        message_id_getter = getattr(self, "_event_message_id", None)
        if callable(message_id_getter):
            try:
                message_id = _single_line(message_id_getter(event), 120)
            except Exception:
                message_id = ""
        denial_cache_key = ""
        denial_cache: dict[str, float] | None = None
        denial_cache_stamp = time.monotonic()
        if message_id:
            try:
                sender_id = _single_line(event.get_sender_id(), 120)
            except Exception:
                sender_id = ""
            platform_getter = getattr(self, "_platform_kind_for_event", None)
            try:
                platform = _single_line(platform_getter(event), 80) if callable(platform_getter) else ""
            except Exception:
                platform = ""
            scope_getter = getattr(self, "_event_req036_scope", None)
            try:
                denial_scope = _single_line(scope_getter(event), 480) if callable(scope_getter) else ""
            except Exception:
                denial_scope = ""
            denial_scope = denial_scope or f"{platform or 'unknown'}:{sender_id or 'unknown'}"
            denial_cache_key = f"{denial_scope}:{message_id}"
            denial_cache = getattr(self, "_req036_recent_denial_message_ids", None)
            if not isinstance(denial_cache, dict):
                denial_cache = {}
                self._req036_recent_denial_message_ids = denial_cache
            for cache_key, cached_at in list(denial_cache.items()):
                age = denial_cache_stamp - _safe_float(cached_at, 0.0)
                if age < 0 or age > 180.0:
                    denial_cache.pop(cache_key, None)
            if denial_cache_key in denial_cache:
                logger.debug(
                    "已忽略重复的未授权私聊拒绝: sender=%s message_id=%s",
                    sender_id or "-",
                    message_id,
                )
                return
            denial_cache[denial_cache_key] = denial_cache_stamp
        reply_text = str(gate.get("reply") or DEFAULT_UNAUTHORIZED_PRIVATE_REPLY)
        echo_entry = None
        echo_remember = getattr(self, "_remember_req036_denial_echo", None)
        if callable(echo_remember):
            try:
                echo_entry = echo_remember(event, reply_text)
            except Exception:
                echo_entry = None

        def release_reply_reservations() -> None:
            if denial_cache is not None and denial_cache_key and denial_cache.get(denial_cache_key) == denial_cache_stamp:
                denial_cache.pop(denial_cache_key, None)
            echo_forget = getattr(self, "_forget_req036_denial_echo", None)
            if callable(echo_forget) and echo_entry is not None:
                try:
                    echo_forget(echo_entry)
                except Exception:
                    pass

        try:
            reply_result = await self._reply(event, reply_text)
        except Exception:
            release_reply_reservations()
            raise
        if reply_result is False:
            release_reply_reservations()
            logger.debug("未授权私聊拒绝未发送，已释放回流与消息去重占位")
            return
        echo_confirm = getattr(self, "_confirm_req036_denial_echo", None)
        if callable(echo_confirm) and echo_entry is not None:
            try:
                echo_confirm(echo_entry)
            except Exception:
                pass

    @staticmethod
    def _req036_group_portrait_query_kind(text: Any) -> str:
        value = _single_line(text, 240)
        # A preference phrase followed by advice or a conclusion is ordinary
        # group chatter, not a request to summarize anyone's profile.
        ordinary_statement_patterns = (
            r"(?:^|[\s，,：:@])(?:自己|按自己|个人|各自)\s*(?:喜欢|爱)(?:吃|喝|玩|看|听)?什么\s*(?:就|便|吧|呀|喵|都|随便)",
            r"(?:喜欢|爱)(?:吃|喝|玩|看|听)?什么\s*(?:就|便|吧|呀|喵|都|随便)",
            # Questions about choosing/feeding an item are ordinary chatter,
            # not requests to summarize a person's preference profile.
            r"(?:要|该|应该|可以|能|想|准备)?\s*(?:喂|选|挑|买|点|吃|喝|做|换).{0,8}(?:什么|啥|哪种|哪个)口味",
            # Negative interest statements ("现在干啥都提不起兴趣" etc.) are
            # ordinary venting chatter. Without this the stray 啥 in 干啥
            # before 兴趣 false-positives as a third-party portrait probe.
            r"(?:提不起|不感|没(?:有|啥|什么)?|毫无|失去|缺(?:乏)?)(?:任何的?\s*)?兴趣",
        )
        if any(re.search(pattern, value) for pattern in ordinary_statement_patterns):
            return ""
        probe_patterns = (
            r"喜欢(?:吃|喝|玩|看|听)?什么",
            r"爱(?:吃|喝|玩|看|听)什么",
            r"(?:爱好|兴趣|偏好|习惯|口味|画像)(?:是|有|包括)?(?:什么|啥|哪些|怎么样)",
            r"(?:什么|啥|哪些|有啥|有哪些).{0,8}(?:爱好|兴趣|偏好|习惯|口味)",
            r"(?:说说|看看|查查|总结|整理).{0,12}(?:爱好|兴趣|偏好|习惯|口味|画像)",
        )
        if not any(re.search(pattern, value) for pattern in probe_patterns):
            return ""
        self_subject = r"(?:我自己|我的|我|本人自己|本人的|本人|俺自己|俺的|俺|咱自己|咱的|咱)"
        self_predicate = (
            r"(?:平时|一般|通常|到底|最)?(?:"
            r"喜欢(?:吃|喝|玩|看|听)?什么|爱(?:吃|喝|玩|看|听)什么|"
            r"(?:有|有什么|有啥|有哪些).{0,8}(?:爱好|兴趣|偏好|习惯)|"
            r"(?:的)?(?:爱好|兴趣|偏好|习惯|口味|画像)(?:是|有|包括)?(?:什么|啥|哪些|怎么样)"
            r")"
        )
        direct_self_query = rf"{self_subject}\s*{self_predicate}"
        reflective_self_query = (
            rf"(?:^|[\s，,：:@])我\s*(?:想知道|想问|想看看|想了解)\s*"
            rf"(?:一下)?\s*(?:自己|我自己|我的)\s*{self_predicate}"
        )
        if re.search(direct_self_query, value) or re.search(reflective_self_query, value):
            return "self"
        bot_subject = r"(?:你自己|你的|你)"
        direct_bot_query = rf"{bot_subject}\s*{self_predicate}"
        reflective_bot_query = (
            rf"(?:^|[\s，,：:@])我\s*(?:想知道|想问|想看看|想了解)\s*"
            rf"(?:一下)?\s*(?:你自己|你的|你)\s*{self_predicate}"
        )
        if re.search(direct_bot_query, value) or re.search(reflective_bot_query, value):
            return "bot_self"
        # An omitted subject is ambiguous in natural group speech. Let the
        # normal reply chain decide whether the user means the Bot, instead of
        # treating a prompt such as "喜欢什么发型" as a third-party probe.
        subjectless_query = (
            r"^(?:@[^\s]+\s*)?(?:(?:你觉得|你认为|请问|我想(?:知道|问|看看|了解))(?:一下)?\s*)?"
            r"(?:喜欢|爱)(?:吃|喝|玩|看|听)?什么"
            r"|^(?:@[^\s]+\s*)?(?:(?:你觉得|你认为|请问|我想(?:知道|问|看看|了解))(?:一下)?\s*)?"
            r"(?:什么|啥|哪些).{0,8}(?:爱好|兴趣|偏好|习惯|口味|画像)"
        )
        if re.search(subjectless_query, value):
            return ""
        return "third_party"

    def _req036_group_portrait_query_is_directed(self, event: Any) -> bool:
        """Use adapter addressing metadata so ordinary group chatter never triggers this guard."""
        # ``is_wake`` only means that some handler accepted the event; it does
        # not prove that the user addressed this Bot. Keep the more specific
        # command flag and structured At/Reply evidence below.
        if bool(getattr(event, "is_at_or_wake_command", False)):
            return True
        try:
            signals = self._event_scene_signals(event)
        except Exception:
            signals = {}
        if not isinstance(signals, dict):
            return False
        if any(
            isinstance(item, dict) and bool(item.get("is_bot"))
            for item in (signals.get("at_targets") or [])
        ):
            return True
        self_id = _single_line(signals.get("self_id"), 80)
        return bool(self_id and _single_line(signals.get("reply_to_id"), 80) == self_id)

    async def _req036_read_group_self_portrait(self, event: Any) -> str:
        dto = getattr(event, "private_companion_unified_profile_context", None)
        if not isinstance(dto, dict):
            return "这部分画像暂时不可用。"
        capabilities = dto.get("capability_summary")
        if not isinstance(capabilities, dict) or capabilities.get("portrait_usage_enabled") is not True:
            return "智能画像当前未开启。"
        person_ref = dto.get("person_ref") if isinstance(dto.get("person_ref"), dict) else {}
        person_id = _single_line(person_ref.get("person_id"), 80)
        overlays = dto.get("context_overlays") if isinstance(dto.get("context_overlays"), dict) else {}
        scope = _single_line(overlays.get("group_scope"), 80)
        if not scope.startswith("group:"):
            return "这部分画像暂时不可用。"
        request = req036_build_portrait_request(
            person_ref=person_ref,
            requester_person_id=person_id,
            target_person_id=person_id,
            scope=scope,
            purpose="summarize_to_subject",
        )
        namespace_context = getattr(event, "private_companion_namespace_context", None)
        if isinstance(namespace_context, dict):
            request["namespace_context"] = dict(namespace_context)
        bridge = self._memory_companion_bridge()
        reader = getattr(bridge, "read_unified_profile_portrait", None) if bridge is not None else None
        if not callable(reader):
            return "这部分画像暂时不可用。"
        try:
            result = reader(request, limit=5)
            if asyncio.iscoroutine(result) or hasattr(result, "__await__"):
                result = await result
        except Exception:
            return "这部分画像暂时不可用。"
        if not isinstance(result, dict) or not result.get("ok"):
            return "这部分画像暂时不可用。"
        summaries = [
            _single_line(item.get("summary"), 80)
            for item in result.get("items", [])
            if isinstance(item, dict) and _single_line(item.get("summary"), 80)
        ]
        return "我目前只记得这些公开的低敏偏好：" + "；".join(summaries[:5]) if summaries else "我还没有整理出可公开的低敏画像。"

    async def _req036_portrait_bridge_status_for_user(self, user: Any) -> dict[str, Any]:
        """Read synchronization state only; facts stay in Memory's admin UI."""
        source = user if isinstance(user, dict) else {}
        person_id = _single_line(source.get("unified_person_id"), 80)
        if not person_id:
            return {"available": False, "code": "identity_pending", "last_synced_at": "", "portrait_revision": 0}
        bridge = self._memory_companion_bridge()
        reader = getattr(bridge, "unified_profile_portrait_status", None) if bridge is not None else None
        if not callable(reader):
            return {"available": False, "code": "bridge_unavailable", "last_synced_at": "", "portrait_revision": 0}
        try:
            result = reader(person_id)
            if asyncio.iscoroutine(result) or hasattr(result, "__await__"):
                result = await result
        except Exception:
            return {"available": False, "code": "bridge_degraded", "last_synced_at": "", "portrait_revision": 0}
        if not isinstance(result, dict):
            return {"available": False, "code": "bridge_degraded", "last_synced_at": "", "portrait_revision": 0}
        response = {
            "available": bool(result.get("ok")),
            "code": _single_line(result.get("code"), 80) or "bridge_degraded",
            "last_synced_at": _single_line(result.get("last_synced_at"), 80),
            "portrait_revision": _safe_int(result.get("portrait_revision"), 0, 0),
        }
        projection = self.get_unified_person_projection(person_id)
        portrait_reader = getattr(bridge, "read_unified_profile_portrait", None) if bridge is not None else None
        if not response["available"] or not isinstance(projection, dict) or not callable(portrait_reader):
            return response
        try:
            request = req036_build_portrait_request(
                person_ref=req036_build_person_ref(projection),
                requester_person_id=person_id,
                target_person_id=person_id,
                scope="private",
                purpose="summarize_to_subject",
            )
            namespace_getter = getattr(self, "_req041_scoped_context_for_user", None)
            if callable(namespace_getter):
                namespace_context = namespace_getter(
                    source, kind="private", purpose="profile_read"
                )
                if isinstance(namespace_context, NamespaceContext) and not namespace_context.errors():
                    request["namespace_context"] = namespace_context.to_dict()
            portrait = portrait_reader(request, limit=3)
            if asyncio.iscoroutine(portrait) or hasattr(portrait, "__await__"):
                portrait = await portrait
            response["summaries"] = [
                _single_line(item.get("summary"), 80)
                for item in (portrait.get("items", []) if isinstance(portrait, dict) else [])
                if isinstance(item, dict) and _single_line(item.get("summary"), 80)
            ][:3]
        except Exception:
            response["summaries"] = []
        return response

    async def _req036_preferred_address_from_portrait(self, user: Any) -> str:
        """Resolve this exact private subject's latest explicit address hint."""
        source = user if isinstance(user, dict) else {}
        capabilities = self._req036_capability_summary_for_user(source)
        if capabilities.get("portrait_usage_enabled") is not True:
            return ""
        person_id = _single_line(source.get("unified_person_id"), 80)
        if not person_id:
            return ""
        projection = self.get_unified_person_projection(person_id)
        if not isinstance(projection, dict):
            return ""
        bridge = self._memory_companion_bridge()
        reader = (
            getattr(bridge, "read_unified_profile_portrait", None)
            if bridge is not None
            else None
        )
        if not callable(reader):
            return ""
        request = req036_build_portrait_request(
            person_ref=req036_build_person_ref(projection),
            requester_person_id=person_id,
            target_person_id=person_id,
            scope="private",
            purpose="summarize_to_subject",
        )
        namespace_getter = getattr(self, "_req041_scoped_context_for_user", None)
        if callable(namespace_getter):
            namespace_context = namespace_getter(
                source, kind="private", purpose="profile_read"
            )
            if (
                isinstance(namespace_context, NamespaceContext)
                and not namespace_context.errors()
            ):
                request["namespace_context"] = namespace_context.to_dict()
        result = reader(request, limit=8)
        if asyncio.iscoroutine(result) or hasattr(result, "__await__"):
            result = await result
        if not isinstance(result, dict) or not result.get("ok"):
            return ""
        for item in result.get("items", []):
            if not isinstance(item, dict):
                continue
            if _single_line(item.get("dimension"), 80) != "preferred_address":
                continue
            summary = _single_line(item.get("summary"), 180)
            match = re.fullmatch(r"希望被称为\s+(.+)", summary)
            preferred = _single_line(match.group(1) if match else "", 24)
            if preferred:
                return preferred
        return ""

    def read_p4_effect_state(self, person_id: str) -> dict[str, Any]:
        return self._active_unified_person_registry().read_p4_effect_state(person_id)

    def read_p4_live_state(self, person_id: str) -> dict[str, Any]:
        return self._active_unified_person_registry().read_p4_live_state(person_id)

    def _p4_b_apply_legacy_relationship_delta(
        self,
        user: dict[str, Any],
        delta: int,
        *,
        reason_code: str = "",
    ) -> bool:
        del reason_code
        return apply_legacy_relationship_delta(
            user,
            delta,
            isolate=bool(getattr(self, "enable_p4_b_legacy_score_isolation", False)),
        )

    def _p4_live_state_for_event(self, event: Any) -> dict[str, Any] | None:
        try:
            if not bool(event.is_private_chat()):
                return None
        except Exception:
            return None
        resolution = self.resolve_unified_person_for_event(event)
        if resolution.get("state") != "resolved":
            return None
        person_id = _single_line(resolution.get("person_id"), 160)
        if not person_id:
            return None
        result = self.read_p4_live_state(person_id)
        if result.get("ok") is not True:
            return {"_p4_live_invalid": True}
        return result.get("state")

    def _bounded_p4_reply_temperature_signals(self, event: Any) -> dict[str, Any]:
        """Return transient, bounded advisory inputs for the P4 reply projection."""
        data = getattr(self, "data", None)
        daily_state = data.get("daily_state") if isinstance(data, dict) else None
        energy = daily_state.get("energy") if isinstance(daily_state, dict) else None
        if (
            isinstance(energy, bool)
            or not isinstance(energy, (int, float))
            or not math.isfinite(float(energy))
        ):
            energy = None
        else:
            energy = max(0, min(100, energy))
        mood = ""
        if isinstance(daily_state, dict):
            mood = _single_line(daily_state.get("mood_bias") or daily_state.get("mood"), 64)

        schedule_parts: list[str] = []
        segment_getter = getattr(self, "_current_detail_segment_for_update", None)
        if callable(segment_getter):
            try:
                segment = segment_getter()
            except Exception:
                segment = None
            if isinstance(segment, dict):
                schedule_parts.extend(
                    _single_line(segment.get(key), 80)
                    for key in ("title", "name", "summary", "activity", "location")
                    if _single_line(segment.get(key), 80)
                )
        if not schedule_parts and isinstance(data, dict):
            try:
                current_item = self._get_current_plan_item(data.get("daily_plan", {}))
            except Exception:
                current_item = None
            if isinstance(current_item, dict):
                schedule_parts.extend(
                    _single_line(current_item.get(key), 80)
                    for key in ("title", "name", "summary", "activity", "location")
                    if _single_line(current_item.get(key), 80)
                )

        return {
            "energy": energy,
            "mood": mood or None,
            "schedule": " ".join(schedule_parts)[:240] or None,
            "context": _single_line(getattr(event, "message_str", ""), 280) or None,
        }

    def record_p4_effect_event(
        self,
        person_id: str,
        event: dict[str, Any],
        *,
        operation_id: str,
        actor_id: str = "system",
    ) -> dict[str, Any]:
        result = self._active_unified_person_registry().record_p4_effect_event(
            person_id,
            event,
            operation_id=operation_id,
            actor_id=actor_id,
        )
        if result.get("ok") and result.get("changed"):
            saver = getattr(self, "_schedule_data_save", None)
            if callable(saver):
                saver(sections={"unified_person"})
        return result

    def create_unified_person_for_event(
        self,
        event: Any | None = None,
        *,
        operation_id: str = "",
        profile: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        identity = self._unified_person_event_identity(event)
        if not identity:
            return {"ok": False, "state": "pending", "code": "event_identity_missing", "person_id": ""}
        try:
            identity_key = build_identity_key(identity)
        except (TypeError, ValueError):
            return {"ok": False, "state": "invalid", "code": "identity_invalid", "person_id": ""}
        user_profile = dict(profile) if isinstance(profile, dict) else {}
        if event is not None and not user_profile.get("display_name"):
            name_getter = getattr(self, "_sender_display_name", None)
            if callable(name_getter):
                try:
                    user_profile["display_name"] = _single_line(name_getter(event), 80)
                except Exception:
                    pass
        return self.create_unified_person(
            identity,
            profile=user_profile,
            operation_id=operation_id or f"companion.person.create:{identity_key[-24:]}",
        )

    def build_unified_person_context(self, event: Any | None = None) -> dict[str, Any]:
        identity = self._unified_person_event_identity(event)
        resolution = self.resolve_unified_person_identity(identity) if identity else {
            "state": "pending", "identity_key": "", "person_id": "", "errors": ["event_identity_missing"],
        }
        state = str(resolution.get("state") or "pending")
        projection = resolution.get("projection") if isinstance(resolution.get("projection"), dict) else None
        scope = "unknown"
        if event is not None:
            try:
                scope = "private" if bool(event.is_private_chat()) else "group"
            except Exception:
                scope = "unknown"
        platform = str(identity.get("subject_namespace") or "").split(":", 1)[0] if identity else ""
        group_id = ""
        if scope == "group":
            group_getter = getattr(self, "_extract_group_id_from_event", None)
            if callable(group_getter):
                try:
                    group_id = _single_line(group_getter(event), 160)
                except Exception:
                    group_id = ""
        group_scope = self._unified_wire_group_scope(platform, group_id)
        group_overlay = None
        if state == "resolved" and group_scope and resolution.get("person_id"):
            group_overlay = self._active_unified_person_registry().read_group_overlay(
                str(resolution.get("person_id") or ""), group_scope
            )
        person_payload = {
            key: projection.get(key)
            for key in (
                "person_id", "identity_assurance", "profile_status", "relation_policy_id",
                "relation_label", "owner_mode", "affinity_band", "projection_revision",
                "group_overlay_ref",
            )
            if projection is not None and projection.get(key) not in (None, "", [], {})
        }
        p3 = build_context(
            persona={"companion_instance_id": self._unified_persona_scoped_value(PLUGIN_ID)},
            runtime={"platform": platform, "scope": scope, "adapter_instance_id": identity.get("adapter_instance_id", "")},
            person=person_payload,
            scene={
                "scope": scope,
                "group_scope": group_scope,
                "group_id_present": bool(group_id),
                "group_overlay_revision": group_overlay.get("revision") if isinstance(group_overlay, dict) else 0,
            },
            bridge_available=True,
        )
        if state != "resolved":
            p3["state"] = state if state in {"pending", "invalid", "degraded", "legacy_local"} else "degraded"
            p3["warnings"] = list(p3.get("warnings") or []) + list(resolution.get("errors") or [f"person_{state}"])[:8]
            person_slot = p3.get("slots", {}).get("person")
            if isinstance(person_slot, dict):
                person_slot["state"] = p3["state"]
        p3 = project_context(p3)
        p4 = build_p4_shadow(
            source_kind="companion",
            target_kind="memory_bridge",
            authority="companion",
            reason_code="projection_ready" if state == "resolved" else f"person_{state}",
            safe_reference=str(resolution.get("person_id") or ""),
            operation_id=f"person.context:{str(resolution.get('identity_key') or 'pending')[-24:]}",
            status="shadow" if state == "resolved" else "degraded",
        )
        return {
            "contract_name": PERSON_CONTRACT_NAME,
            "contract_version": PERSON_CONTRACT_VERSION,
            "p3_contract_name": P3_CONTRACT_NAME,
            "p3_contract_version": P3_CONTRACT_VERSION,
            "state": state,
            "identity": {"identity_key": str(resolution.get("identity_key") or ""), "person_id": str(resolution.get("person_id") or "")},
            "projection": projection,
            "p3": p3,
            "p4_shadow": p4,
            "scope": scope,
            "group_scope": group_scope,
        }

    def _repair_private_companion_handler_bindings(self) -> None:
        """热更新后强制把残留 handler 重新绑定到当前插件实例。"""
        try:
            module_path = str(getattr(type(self), "__module__", "") or "")
            package_prefix = module_path.rsplit(".", 1)[0] if "." in module_path else module_path
            if not package_prefix:
                return
            repaired = 0
            for handler in list(star_handlers_registry):
                handler_module_path = str(getattr(handler, "handler_module_path", "") or "")
                if not is_module_path_for_package(handler_module_path, package_prefix):
                    continue
                handler_name = str(getattr(handler, "handler_name", "") or "")
                if not handler_name:
                    continue
                current_func = getattr(type(self), handler_name, None)
                if not callable(current_func):
                    continue
                handler.handler = functools.partial(current_func, self)
                repaired += 1
            if repaired:
                logger.info("已修复热更新残留回调绑定: handlers=%s", repaired)
        except Exception as exc:
            logger.warning("修复热更新残留回调绑定失败: %s", _single_line(exc, 160))

    async def archive_unified_person(
        self,
        person_id: str,
        *,
        operation_id: str,
        confirmation_token: str = "",
        dry_run: bool = True,
        actor_id: str = "page_administrator",
        reason_code: str = "person_archive",
    ) -> dict[str, Any]:
        """Run the request-bound, resumable person archive saga."""
        clean_person = _single_line(person_id, 80)
        clean_operation = _single_line(operation_id, 120)
        if not clean_person or not clean_operation or type(dry_run) is not bool:
            return {"ok": False, "state": "invalid", "code": "invalid_request"}
        async with self._data_lock:
            registry = self._active_unified_person_registry()
            prepared = registry.prepare_person_archive(
                clean_person, operation_id=clean_operation,
                actor_id=actor_id, reason_code=reason_code,
            )
            if not prepared.get("ok"):
                return prepared
            self._req041_persist_archive_saga_locked(
                sections={"unified_person"},
            )
            if prepared.get("code") == "person_archived":
                subjects = registry.archived_identity_subjects(clean_person)
                removed = self._req041_erase_person_private_auxiliary_locked(clean_person, subjects)
                if sum(removed.values()) > 0:
                    self._req041_persist_archive_saga_locked(
                        sections={
                            name
                            for name, count in removed.items()
                            if int(count or 0) > 0
                        },
                    )
                return prepared
            if dry_run:
                return prepared
            if not confirmation_token or confirmation_token != prepared.get("confirmation_token"):
                return {
                    "ok": False, "state": "prepared", "code": "archive_confirmation_mismatch",
                    "person_id": clean_person, "operation_id": clean_operation, "changed": False,
                }
            archive_available = getattr(self, "_req041_scoped_archive_available", None)
            if callable(archive_available) and not archive_available():
                return {
                    "ok": False, "state": "prepared", "code": "scoped_identity_archive_unavailable",
                    "person_id": clean_person, "operation_id": clean_operation, "changed": False,
                }
            confirmed = registry.confirm_person_archive(
                clean_person, clean_operation, confirmation_token,
                actor_id=actor_id, reason_code=reason_code,
            )
            if not confirmed.get("ok"):
                return confirmed
            self._req041_persist_archive_saga_locked(
                sections={"unified_person"},
            )
            context = self._req041_scoped_private_context_for_person(clean_person)
            synchronizer = getattr(self, "req041_scoped_projection_sync", None)
            relationship_store = getattr(self, "req041_relationship_store", None)
            outbox = getattr(self, "req041_migration_outbox", None)
            if context is None or synchronizer is None:
                return {
                    "ok": False, "state": "prepared", "code": "scoped_identity_archive_unavailable",
                    "person_id": clean_person, "operation_id": clean_operation, "changed": False,
                }
            if relationship_store is None or not callable(getattr(relationship_store, "tombstone_account", None)):
                synchronizer.mark_dirty()
                return {
                    "ok": False, "state": "prepared", "code": "relationship_archive_unavailable",
                    "person_id": clean_person, "operation_id": clean_operation, "changed": False,
                }
            if outbox is None or not callable(getattr(outbox, "retire_streams", None)):
                synchronizer.mark_dirty()
                return {
                    "ok": False, "state": "prepared", "code": "archive_outbox_unavailable",
                    "person_id": clean_person, "operation_id": clean_operation, "changed": False,
                }
            try:
                stream_receipt = outbox.retire_streams(
                    [f"identity:{clean_person}", f"relationship:{clean_person}"],
                    synchronizer.migration_epoch,
                    operation_id=f"req041-streams-{clean_operation}", reason_code=reason_code,
                )
            except Exception as exc:
                synchronizer.mark_dirty()
                return {
                    "ok": False, "state": "prepared",
                    "code": _single_line(exc, 120) or "archive_stream_retirement_failed",
                    "person_id": clean_person, "operation_id": clean_operation, "changed": False,
                }
            scoped_receipt = synchronizer.archive_identity_scopes(
                context, operation_id=f"req041-scoped-{clean_operation}", reason_code=reason_code,
            )
            if not scoped_receipt.get("ok"):
                return {
                    "ok": False, "state": "prepared",
                    "code": str(scoped_receipt.get("code") or "scoped_identity_archive_failed")[:120],
                    "person_id": clean_person, "operation_id": clean_operation, "changed": False,
                }
            try:
                relationship_receipt = relationship_store.tombstone_account(
                    context, operation_id=f"req041-relationship-{clean_operation}",
                    reason_code=reason_code, actor="administrator",
                )
            except Exception as exc:
                return {
                    "ok": False, "state": "prepared",
                    "code": _single_line(exc, 120) or "relationship_archive_failed",
                    "person_id": clean_person, "operation_id": clean_operation, "changed": False,
                }
            legacy_subjects = [
                _single_line(item.get("identity_subject_id") or item.get("user_id"), 160)
                for item in (self.data.get("users") or {}).values()
                if isinstance(item, dict)
                and _single_line(item.get("unified_person_id"), 80) == clean_person
            ] if isinstance(self.data.get("users"), dict) else []
            auxiliary_counts = self._req041_erase_person_private_auxiliary_locked(
                clean_person, legacy_subjects,
            )
            result = registry.finalize_person_archive(
                clean_person, clean_operation, confirmation_token,
                scoped_receipt, relationship_receipt, stream_receipt,
                actor_id=actor_id, reason_code=reason_code,
            )
            if result.get("changed"):
                result["auxiliary_removed_record_count"] = sum(auxiliary_counts.values())
                coordinator = getattr(self, "req041_migration_coordinator", None)
                rollback = getattr(coordinator, "rollback_identity", None)
                if callable(rollback):
                    rollback(clean_person, reason_code="person_archived")
                self._req041_persist_archive_saga_locked(
                    sections={
                        "unified_person",
                        *(
                            name
                            for name, count in auxiliary_counts.items()
                            if int(count or 0) > 0
                        ),
                    },
                )
            return result

    async def purge_unified_person(
        self,
        person_id: str,
        *,
        operation_id: str,
        confirmation_token: str = "",
        dry_run: bool = True,
        actor_id: str = "page_administrator",
        reason_code: str = "person_delete",
    ) -> dict[str, Any]:
        clean_person = _single_line(person_id, 80)
        clean_operation = _single_line(operation_id, 120)
        if not clean_person or not clean_operation or type(dry_run) is not bool:
            return {"ok": False, "state": "invalid", "code": "invalid_request"}
        async with self._data_lock:
            registry = self._active_unified_person_registry()
            prepared = registry.prepare_person_purge(
                clean_person, operation_id=clean_operation,
                actor_id=actor_id, reason_code=reason_code,
            )
            if not prepared.get("ok"):
                return prepared
            self._req041_persist_archive_saga_locked(
                sections={"unified_person"},
            )
            if prepared.get("code") == "person_purged":
                return prepared
            if dry_run:
                return prepared
            if not confirmation_token or confirmation_token != prepared.get("confirmation_token"):
                return {
                    "ok": False, "state": "prepared", "code": "purge_confirmation_mismatch",
                    "person_id": clean_person, "operation_id": clean_operation, "changed": False,
                }
            confirmed = registry.confirm_person_purge(
                clean_person, clean_operation, confirmation_token,
                actor_id=actor_id, reason_code=reason_code,
            )
            if not confirmed.get("ok"):
                return confirmed
            self._req041_persist_archive_saga_locked(
                sections={"unified_person"},
            )
            subjects = registry.archived_identity_subjects(clean_person)
            if int(prepared.get("detached_identity_count") or 0) > 0 and not subjects:
                return {
                    "ok": False, "state": "confirmed", "code": "purge_identity_subjects_invalid",
                    "person_id": clean_person, "operation_id": clean_operation, "changed": False,
                }
            outbox = getattr(self, "req041_migration_outbox", None)
            synchronizer = getattr(self, "req041_scoped_projection_sync", None)
            if outbox is None or synchronizer is None or not callable(getattr(outbox, "purge_retired_streams", None)):
                return {
                    "ok": False, "state": "confirmed", "code": "purge_outbox_unavailable",
                    "person_id": clean_person, "operation_id": clean_operation, "changed": False,
                }
            try:
                outbox_receipt = outbox.purge_retired_streams(
                    [f"identity:{clean_person}", f"relationship:{clean_person}"],
                    synchronizer.migration_epoch,
                    operation_id=f"req041-purge-streams-{clean_operation}", reason_code=reason_code,
                )
            except Exception as exc:
                return {
                    "ok": False, "state": "confirmed",
                    "code": _single_line(exc, 120) or "purge_outbox_failed",
                    "person_id": clean_person, "operation_id": clean_operation, "changed": False,
                }
            auxiliary_counts = self._req041_erase_person_private_auxiliary_locked(clean_person, subjects)
            legacy_changed_sections: set[str] = set()
            legacy_counts = self._req041_purge_legacy_person_locked(
                clean_person,
                subjects,
                changed_sections=legacy_changed_sections,
            )
            result = registry.finalize_person_purge(
                clean_person, clean_operation, confirmation_token, outbox_receipt,
                actor_id=actor_id, reason_code=reason_code,
            )
            if result.get("changed"):
                result["legacy_removed_record_count"] = int(legacy_counts.get("records") or 0)
                result["auxiliary_removed_record_count"] = sum(auxiliary_counts.values())
                if legacy_changed_sections:
                    # Legacy identity records may live under unregistered roots.
                    # This administrator-confirmed purge is therefore an explicit
                    # full-store repair boundary rather than an implicit fallback.
                    self._req041_persist_archive_saga_locked(
                        full_scope="admin_import_export",
                    )
                else:
                    self._req041_persist_archive_saga_locked(
                        sections={"unified_person"}
                        | {
                            name
                            for name, count in auxiliary_counts.items()
                            if int(count or 0) > 0
                        },
                    )
            return result

    async def initialize(self):
        global _private_companion_plugin
        # Lifecycle tests execute this method in an isolated namespace that
        # only contains the class methods. Keep duplicate-instance fencing
        # active in the full module while allowing that reduced namespace to
        # exercise publication behavior.
        is_primary_instance = globals().get("_is_primary_plugin_instance")
        if not callable(is_primary_instance):
            is_primary_instance = lambda _instance: True
        with _private_companion_runtime.lock:
            active = _private_companion_runtime.active_plugin
            if (
                active is not None
                and active is not self
                and is_primary_instance(active)
                and not is_primary_instance(self)
            ):
                self._private_companion_duplicate_instance = True
                logger.warning(
                    "检测到同名 worktree 插件实例，已跳过其事件处理: module=%s",
                    type(self).__module__,
                )
                return
        await self._initialize_before_publication()
        try:
            await resume_story_handoff(self)
        except asyncio.CancelledError:
            raise
        except StoryAuthorityError as exc:
            # A durable marker is irreversible, but Content may legitimately
            # load later. Keep the Companion core available while Story stays
            # fenced and a later startup/API call replays the same marker.
            logger.warning(
                "Story handoff replay pending: code=%s",
                exc.code,
            )
        except Exception:
            logger.warning(
                "Story handoff replay pending: "
                "code=story_handoff_replay_failed"
            )
        # Keep the prior ready instance visible until every startup step succeeds.
        activate = getattr(self.extension_api, "_activate_story_migration_api", None)
        if not callable(activate) or not activate():
            return
        # No await may split activation, supersession, and global publication.
        with _private_companion_runtime.lock:
            store_manager = getattr(self, "store_manager", None)
            activate_persistence = getattr(
                store_manager, "activate_persistence_generation", None
            )
            if store_manager is not None and not callable(activate_persistence):
                raise RuntimeError("persistence generation activation is unavailable")
            if callable(activate_persistence):
                activate_persistence()
            else:
                owner_token = str(
                    getattr(self, "_persistence_owner_token", "") or ""
                ).strip()
                data_file = getattr(self, "data_file", None)
                if not owner_token or data_file is None or not str(data_file).strip():
                    raise RuntimeError(
                        "direct persistence generation activation is unavailable"
                    )
                activate_persistence_owner(owner_token, [data_file])
            previous = _private_companion_runtime.active_plugin
            if previous is not None and previous is not self:
                previous_api = getattr(previous, "extension_api", None)
                supersede = getattr(previous_api, "_supersede_story_migration_api", None)
                if callable(supersede):
                    supersede()
            _private_companion_runtime.active_plugin = self
        _private_companion_plugin = self

    async def _initialize_before_publication(self):
        self._repair_private_companion_handler_bindings()
        if getattr(self, "_legacy_enabled_config_disabled", False):
            logger.warning(
                "检测到旧版配置 enabled=false；该字段已废弃并被忽略。"
                "如需停用插件，请在 AstrBot 官方插件管理页关闭本插件。"
            )
        self._log_registered_command_handlers()
        self._install_send_message_to_user_tool_sanitizer()
        boundary_ability_registrar = getattr(self, "_register_relationship_boundary_proactive_ability", None)
        if callable(boundary_ability_registrar) and bool(runtime_persona_setting(self, 'enable_relationship_boundary_feedback', True)):
            boundary_ability_registrar()
        self._schedule_default_persona_prompt_refresh()
        await self._body_monitor_integration.set_enabled(self.enable_body_monitor_integration)
        needs_startup_save = False
        agenda_before = bool(getattr(self, "_agenda_migration_dirty", False))
        self._agenda_prepare_store()
        agenda_migration_changed = bool(
            getattr(self, "_agenda_migration_dirty", False) and not agenda_before
        )
        if agenda_migration_changed:
            needs_startup_save = True
        async with self._data_lock:
            changed = False
            raw_users = self.data.get("users") if isinstance(self.data, dict) else None
            if isinstance(raw_users, dict):
                cleaned_habit_users = 0
                for habit_user in raw_users.values():
                    if isinstance(habit_user, dict) and self._sanitize_user_behavior_habit_patterns(habit_user):
                        cleaned_habit_users += 1
                if cleaned_habit_users:
                    changed = True
                    logger.info(
                        "已清理旧版低质量用户习惯记录: users=%s",
                        cleaned_habit_users,
                    )
            if runtime_persona_setting(self, 'default_enable_configured_targets', True):
                self._sync_configured_targets()
                changed = True
            recovered_troubleshooting = self._recover_stale_troubleshooting_proactive_plans()
            if recovered_troubleshooting:
                logger.info("已恢复未完成的排障临时主动任务: %s", recovered_troubleshooting)
            if self._prime_enabled_user_schedules():
                changed = True
            if recovered_troubleshooting:
                changed = True
            if changed:
                needs_startup_save = True
        if needs_startup_save:
            # Initialization may combine an agenda schema migration with legacy
            # user repair and recovery across several durable roots. Keep this
            # one-time boundary explicit and distinguish migration from upkeep.
            if agenda_migration_changed:
                self._schedule_data_save(
                    full_scope="startup_migration",
                    delay=0.5,
                )
            else:
                self._schedule_data_save(
                    full_scope="startup_maintenance",
                    delay=0.5,
                )
        self._create_startup_background_task(
            "req041_automatic_migration",
            self._req041_initialize_automatic_migration,
        )
        self._create_startup_background_task(
            "req041_memory_scope_rebind",
            self._req041_run_memory_scope_rebind,
        )
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._scheduler_loop())
            logger.info("主动消息循环已启动")
        if self._startup_maintenance_task is None or self._startup_maintenance_task.done():
            self._startup_maintenance_task = asyncio.create_task(self._run_startup_background_maintenance())
        self._create_startup_background_task(
            "reset_stale_qq_presence",
            self._reset_stale_qq_presence_if_needed,
        )
        self._create_startup_background_task("prepare_today", self._startup_prepare_today)
        # Keep one orchestrator alive in multi-persona mode so each persona's
        # own enable/time/provider settings are evaluated inside its ContextVar.
        if runtime_persona_setting(self, 'enable_daily_review', True) or bool(getattr(self, "enable_multi_persona_mode", False)):
            self._create_startup_background_task("daily_review", self._daily_review_loop)
        if self.enable_balance_awareness:
            self._create_startup_background_task(
                "refresh_balance_awareness",
                self._maybe_refresh_balance_awareness,
            )
        self._create_startup_background_task(
            "refresh_passive_injection_cache",
            self._refresh_passive_injection_cache,
        )
        await self._proactive_chat_runtime_bridge.start()
        standalone_webui = getattr(self, "standalone_webui", None)
        if standalone_webui is not None:
            try:
                await standalone_webui.start()
            except Exception as exc:
                logger.warning(
                    "独立陪伴 WebUI 启动失败: %s",
                    _single_line(exc, 160),
                    exc_info=True,
                )

    def _create_startup_background_task(self, label: str, operation: Any) -> asyncio.Task:
        return task_manager(self).create_startup(label, operation)

    @asynccontextmanager
    async def _temporarily_release_data_lock(self):
        """Release the data lock for an external await, then reacquire it safely."""
        lock = getattr(self, "_data_lock", None)
        if lock is None or not lock.locked():
            yield
            return
        lock.release()
        reacquire_cancelled = False
        try:
            yield
        finally:
            while True:
                try:
                    await lock.acquire()
                    break
                except asyncio.CancelledError:
                    reacquire_cancelled = True
            if reacquire_cancelled:
                raise asyncio.CancelledError

    def _create_lifecycle_background_task(
        self,
        operation: Any,
        *,
        label: str,
    ) -> asyncio.Task | None:
        return task_manager(self).create_lifecycle(operation, label=label)

    async def _cancel_lifecycle_background_tasks(self, timeout: float = 3.0) -> None:
        await task_manager(self).cancel_lifecycle(timeout)

    def _log_registered_command_handlers(self) -> None:
        expected = {
            "companion_command": "/陪伴(alias: /私聊陪伴, /主动陪伴)",
            "group_companion_command": "/陪伴群(alias: /群陪伴, /群聊陪伴)",
        }
        found: set[str] = set()
        try:
            for handler in star_handlers_registry:
                callback = getattr(handler, "handler", None) or getattr(handler, "func", None)
                handler_name = (
                    getattr(handler, "handler_name", "")
                    or getattr(handler, "name", "")
                    or getattr(callback, "__name__", "")
                )
                if handler_name in expected:
                    found.add(handler_name)
        except Exception as exc:
            logger.debug("指令注册诊断失败: %s", _single_line(exc, 120))
            return
        registered = [expected[name] for name in expected if name in found]
        missing = [expected[name] for name in expected if name not in found]
        if registered:
            logger.info("AstrBot 指令已注册: %s", "；".join(registered))
        if missing:
            logger.warning("AstrBot 指令注册诊断未找到: %s", "；".join(missing))

    @story_legacy_sync_operation("startup.story-maintenance")
    def _run_startup_data_maintenance_locked(self) -> bool:
        changed = False

        def run_step(label: str, func: Any) -> None:
            nonlocal changed
            started = time.perf_counter()
            try:
                if callable(func) and func():
                    changed = True
            except Exception as exc:
                logger.warning("启动后台维护步骤失败: %s error=%s", label, _single_line(exc, 160))
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            if elapsed_ms > 1200:
                logger.warning("启动后台维护步骤耗时较高: step=%s elapsed=%sms", label, elapsed_ms)

        run_step("legacy_prompt_trace_cleanup", self._cleanup_legacy_proactive_prompt_traces)
        run_step("framework_meta_leak_cleanup", self._cleanup_framework_meta_leak_records)
        run_step("creative_fallback_cleanup", self._cleanup_legacy_creative_fallback_chunks)
        run_step("runtime_social_fact_sanitize", self._sanitize_runtime_social_facts_inplace)
        run_step("false_sleep_interaction_cleanup", self._cleanup_false_sleep_interaction_updates)
        run_step("private_user_alias_merge", self._merge_private_user_alias_records)
        run_step("reaction_expression_orphan_user_cleanup", self._cleanup_orphan_reaction_expression_users)
        run_step("group_slang_cleanup", self._cleanup_all_group_slang_terms)
        run_step("recall_image_cache_cleanup", lambda: self._cleanup_recall_message_image_cache(force=True))

        def cleanup_groups() -> bool:
            groups = self.data.get("groups") if isinstance(self.data.get("groups"), dict) else {}
            if not isinstance(groups, dict):
                return False
            group_changed = False
            cleaner = getattr(self, "_cleanup_group_members", None)
            edge_cleaner = getattr(self, "_cleanup_group_relationship_edges", None)
            for raw_group in groups.values():
                if not isinstance(raw_group, dict):
                    continue
                if callable(cleaner) and cleaner(raw_group):
                    group_changed = True
                if callable(edge_cleaner) and edge_cleaner(raw_group):
                    group_changed = True
            return group_changed

        run_step("group_record_cleanup", cleanup_groups)

        if runtime_persona_setting(self, 'worldbook_auto_import', True):
            run_step("worldbook_auto_import", self._import_worldbook_entries_from_sources)
        return changed

    async def _run_startup_background_maintenance(self) -> None:
        await asyncio.sleep(0)
        started = time.perf_counter()
        try:
            reconcile = getattr(self, "_reconcile_deleted_personas_async", None)
            if callable(reconcile):
                try:
                    self._persona_deleted_reconciliation_status = await reconcile()
                except Exception as exc:
                    self._persona_deleted_reconciliation_status = {"ok": False, "state": "degraded", "reason": "reconciliation_failed", "error": _single_line(exc, 180)}
                    logger.warning("启动已删除人格对账失败，保留现有插件数据: %s", _single_line(exc, 180))
            if bool(getattr(self, "_startup_photo_reference_catalog_migration_pending", False)):
                config_started = time.perf_counter()
                catalog_saved = await self._save_config_if_possible()
                if catalog_saved and _set_into_config(self.config, "photo_reference_catalog_version", CATALOG_VERSION):
                    self.photo_reference_catalog_version = CATALOG_VERSION
                    marker_saved = await self._save_config_if_possible()
                    if marker_saved:
                        self._startup_photo_reference_catalog_migration_pending = False
                        self.photo_reference_catalog_read_only = False
                        logger.info(
                            "参考图目录迁移完成: version=%s references=%s",
                            CATALOG_VERSION,
                            len(runtime_persona_setting(self, 'photo_reference_catalog', ()) or ()),
                        )
                    else:
                        logger.error("参考图目录已保存，但迁移版本号保存失败；下次启动会安全重试")
                elif not catalog_saved:
                    logger.error("参考图目录迁移保存失败，当前进程继续使用只读内存投影")
                else:
                    logger.error("参考图目录已保存，但迁移版本号无法写入；当前进程继续使用只读内存投影")
                elapsed_ms = int((time.perf_counter() - config_started) * 1000)
                if elapsed_ms > 1200:
                    logger.warning("启动后台配置保存耗时较高: elapsed=%sms", elapsed_ms)
            elif _safe_int(getattr(self, "_startup_config_migration_changes", 0), 0, 0) > 0:
                config_started = time.perf_counter()
                await self._save_config_if_possible()
                elapsed_ms = int((time.perf_counter() - config_started) * 1000)
                if elapsed_ms > 1200:
                    logger.warning("启动后台配置保存耗时较高: elapsed=%sms", elapsed_ms)
            try:
                await asyncio.wait_for(self._apply_sqlite_wal_optimizations(), timeout=20)
            except asyncio.TimeoutError:
                logger.warning("SQLite WAL 后台优化超时,已跳过本轮启动优化")
            await self._image_companion_maintenance()
            if self._nai_image_selected():
                await self._nai_image_maintenance()
            async with self._data_lock:
                if self._run_startup_data_maintenance_locked():
                    # This one-time pass can scrub legacy records across several
                    # roots, so it intentionally uses the startup maintenance scope.
                    self._save_data_sync(full_scope="startup_maintenance")
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            if elapsed_ms > 1200:
                logger.info("启动后台维护完成: elapsed=%sms", elapsed_ms)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("启动后台维护失败: %s", _single_line(exc, 160), exc_info=True)

    async def terminate(self):
        global _private_companion_plugin
        await close_early_resources(self)
        await self._cancel_lifecycle_background_tasks()
        invalidate_bridge = getattr(self, "_memory_companion_invalidate_bridge_cache", None)
        if callable(invalidate_bridge):
            invalidate_bridge()
        scoped_sync = getattr(self, "req041_scoped_projection_sync", None)
        if scoped_sync is not None:
            mark_dirty = getattr(scoped_sync, "mark_dirty", None)
            if callable(mark_dirty):
                mark_dirty()
        self.req041_scoped_projection_sync = None
        self._req041_scoped_bridge = None

        runtime_bridge = getattr(self, "_proactive_chat_runtime_bridge", None)
        if runtime_bridge is not None:
            try:
                await runtime_bridge.stop()
            except Exception as exc:
                logger.warning(
                    "终止 Proactive Chat 深度联动失败: %s",
                    _single_line(exc, 160),
                )

        await cancel_registered_host_tasks(self)
        try:
            await asyncio.wait_for(self._flush_scheduled_data_save(), timeout=3.0)
        except asyncio.TimeoutError:
            logger.warning(
                "Scheduled persistence did not drain before "
                "shutdown; final persistence will continue in the background"
            )
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.debug(
                "Waiting for scheduled persistence during "
                "shutdown failed: %s",
                _single_line(exc, 160),
            )
        self._termination_flush_already_attempted = True
        close_image_download_session = getattr(self, "_close_external_image_download_session", None)
        if callable(close_image_download_session):
            try:
                await asyncio.wait_for(close_image_download_session(), timeout=3.0)
            except asyncio.TimeoutError:
                logger.warning("终止时关闭在线图片下载会话超时")
            except Exception as exc:
                logger.debug("终止时关闭在线图片下载会话失败: %s", _single_line(exc, 160))
        final_save_task = asyncio.create_task(self._save_data_on_terminate())
        self._termination_save_task = final_save_task
        self._termination_save_status = {
            "state": "running",
            "started_at": asyncio.get_running_loop().time(),
            "task_id": id(final_save_task),
        }

        def observe_final_save(task: asyncio.Task) -> None:
            if task.cancelled():
                self._termination_save_status = {
                    **getattr(self, "_termination_save_status", {}),
                    "state": "cancelled",
                    "completed_at": asyncio.get_running_loop().time(),
                }
                if getattr(self, "_termination_save_task", None) is task:
                    self._termination_save_task = None
                return
            try:
                error = task.exception()
            except (asyncio.CancelledError, asyncio.InvalidStateError):
                return
            if error is not None:
                self._termination_save_status = {
                    **getattr(self, "_termination_save_status", {}),
                    "state": "failed",
                    "completed_at": asyncio.get_running_loop().time(),
                    "error": _single_line(error, 160),
                }
                logger.warning(
                    "Final shutdown persistence failed: %s",
                    _single_line(error, 160),
                )
            else:
                persistence = dict(
                    getattr(self, "_last_persistence_write_status", {}) or {}
                )
                self._termination_save_status = {
                    **getattr(self, "_termination_save_status", {}),
                    "state": (
                        "superseded"
                        if persistence.get("accepted") is False
                        else "completed"
                    ),
                    "completed_at": asyncio.get_running_loop().time(),
                    "persistence": persistence,
                }
            if getattr(self, "_termination_save_task", None) is task:
                self._termination_save_task = None

        final_save_task.add_done_callback(observe_final_save)
        try:
            await asyncio.wait_for(asyncio.shield(final_save_task), timeout=3.0)
        except asyncio.TimeoutError:
            self._termination_save_status = {
                **getattr(self, "_termination_save_status", {}),
                "state": "timed_out_background",
                "timed_out_at": asyncio.get_running_loop().time(),
            }
            logger.warning(
                "Final shutdown persistence timed out; the "
                "shielded task will continue in the background"
            )
        with _private_companion_runtime.lock:
            if _private_companion_runtime.active_plugin is self:
                _private_companion_runtime.active_plugin = None
        if _private_companion_plugin is self:
            _private_companion_plugin = None

    async def _save_data_on_terminate(self) -> None:
        if not bool(getattr(self, "_termination_flush_already_attempted", False)):
            await self._flush_scheduled_data_save()

        manager = getattr(self, "store_manager", None)
        manager_backend = str(getattr(manager, "backend_name", "") or "").lower()
        sqlite_incremental = bool(
            manager_backend == "sqlite"
            and callable(getattr(manager, "save_sections", None))
        )
        if sqlite_incremental:
            await self._flush_default_data_save_on_terminate()
        else:
            task = getattr(self, "_data_save_task", None)
            if (
                isinstance(task, asyncio.Task)
                and not task.done()
                and task is not asyncio.current_task()
            ):
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    pass
                except Exception as exc:
                    logger.debug(
                        "Waiting for the default JSON writer "
                        "during shutdown failed: %s",
                        _single_line(exc, 160),
                    )
            async with self._data_lock:
                snapshot = deepcopy(getattr(self, "_data_default", self.data))
            await asyncio.to_thread(self._write_data_snapshot_sync, snapshot)

        primary = self._primary_persona_id()
        if bool(getattr(self, "enable_multi_persona_mode", False)):
            profiles = getattr(self, "_persona_data_profiles", {}) or {}
            persona_ids = (
                [
                    str(persona_id)
                    for persona_id, profile in profiles.items()
                    if isinstance(profile, dict) and str(persona_id) != primary
                ]
                if isinstance(profiles, dict)
                else []
            )
            for persona_id in persona_ids:
                task = getattr(self, "_persona_data_save_tasks", {}).get(persona_id)
                if isinstance(task, asyncio.Task) and not task.done():
                    try:
                        await asyncio.shield(task)
                    except asyncio.CancelledError:
                        pass
                    except Exception as exc:
                        logger.debug(
                            "Waiting for a persona writer during "
                            "shutdown failed: persona=%s error=%s",
                            persona_id,
                            _single_line(exc, 160),
                        )
                async with self._data_lock:
                    profile = getattr(self, "_persona_data_profiles", {}).get(persona_id)
                    if not isinstance(profile, dict):
                        continue
                    snapshot = deepcopy(profile)
                await asyncio.to_thread(
                    self._write_persona_data_snapshot_sync,
                    persona_id,
                    snapshot,
                )

    @filter.on_decorating_result(priority=100)
    @_multi_persona_event_context
    async def apply_segmented_llm_reply_scope(self, event: AstrMessageEvent, *args, **kwargs):
        """按回复范围与分段策略整理 LLM 输出，减少长回复和误引用。"""
        if self is None or not self.enabled:
            return
        external_proactive = (
            str(getattr(event, "_private_companion_external_proactive_source", "") or "")
            == "proactive_chat"
        )
        # Join Plain components so enabled tag cleanup can match split blocks.
        early_result = event.get_result()
        if early_result is not None:
            early_chain = list(getattr(early_result, "chain", []) or [])
            if early_chain:
                _strip_chain_plain_thinking(self, early_chain)
        if self._proactive_only_blocks_passive_event(event, "enable_segmented_proactive_reply"):
            return
        if not self._feature_enabled_or_temp_unlocked("enable_segmented_proactive_reply"):
            return
        segmented_scope = str(
            self._segmented_setting("scope", event=event, default="proactive_only")
            or "proactive_only"
        )
        if segmented_scope != "all_llm" and not external_proactive:
            return
        if external_proactive and bool(getattr(event, "_private_companion_external_presegmented", False)):
            return
        if not self._segmented_scope_allows_event(event):
            return
        result = event.get_result()
        if result is None or not result.chain:
            return
        source_result = result
        is_llm_result = False
        try:
            is_llm_result = bool(result.is_llm_result())
        except Exception:
            is_llm_result = False
        chain = list(result.chain or [])
        if self._restore_response_review_meta_leak_before_send(event, chain):
            result = event.get_result()
            chain = list(getattr(result, "chain", []) or []) if result is not None else []
            if not chain:
                return
            source_result = result
            try:
                is_llm_result = bool(result.is_llm_result())
            except Exception:
                is_llm_result = False
        reaction_intent = getattr(
            event,
            "_private_companion_reaction_expression_intent",
            None,
        )
        has_reaction_intent = isinstance(reaction_intent, dict) and bool(
            reaction_intent
        )
        deferred_reaction_tts = getattr(
            event,
            "_private_companion_deferred_reaction_tts",
            None,
        )
        plugin_owned_reaction_text = (
            has_reaction_intent
            and isinstance(deferred_reaction_tts, dict)
            and bool(deferred_reaction_tts)
        )
        plugin_tts_plain_fallback = (
            bool(getattr(event, "_private_companion_tts_request_applied", False))
            and bool(self._plain_result_body_text(chain))
        )
        owned_non_llm_result = bool(
            getattr(result, "_private_companion_owned_result", False)
        )
        legacy_plain_result_allowed = False
        if not is_llm_result and not owned_non_llm_result:
            try:
                legacy_plain_result_allowed = bool(
                    self._private_plain_result_allows_segmenting(event, chain)
                )
            except Exception:
                legacy_plain_result_allowed = False
        if is_llm_result and await self._should_defer_segmenting_to_astrbot_tts(event, result, chain):
            logger.debug(
                "当前 LLM 结果交由 AstrBot 官方 TTS 与原生分段处理: session=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            )
            return
        if (
            not is_llm_result
            and not external_proactive
            and not owned_non_llm_result
            and not plugin_owned_reaction_text
            and not plugin_tts_plain_fallback
            and not legacy_plain_result_allowed
        ):
            # A general/plain result has no reliable producer information in
            # AstrBot. Only results explicitly built by this plugin (or its
            # TTS/reaction paths) may enter the optional splitter; otherwise a
            # response from an unrelated plugin would be rewritten here.
            return
        if getattr(result, "use_t2i_", None):
            return
        if not self._segmented_platform_allows(event=event):
            return
        if not chain:
            return
        markdown_mode = getattr(result, "use_markdown_", None)
        try:
            setattr(event, "_private_companion_segmented_markdown_mode", markdown_mode)
        except Exception:
            pass
        chunks, changed, text = self._segment_llm_reply_chain(event, chain)
        if not chunks or not text:
            return
        chunks = self._limit_private_routine_check_segments(
            str(getattr(event, "message_str", "") or ""),
            chunks,
        )
        if len(chunks) <= 1:
            if changed:
                event.set_result(
                    self._build_segmented_result_from_chain(chunks[0], source_result)
                )
            return
        llm_segment_count = max(0, _safe_int(getattr(event, "_private_companion_llm_segment_count", 0), 0, 0))
        logger.debug(
            "按分段计划整理 LLM 回复: chars=%s segments=%s llm_segments=%s",
            len(text),
            len(chunks),
            llm_segment_count,
        )
        logger.info(
            "已按分段计划发送 LLM 回复: segments=%s llm_segments=%s first=%s full=%s",
            len(chunks),
            llm_segment_count,
            _single_line(self._segmented_chunk_log_text(chunks[0]), 120),
            _single_line(text, 420),
        )
        plain_segments = self._plain_text_segments_from_chunks(chunks)
        if (
            not has_reaction_intent
            and not bool(markdown_mode)
            and not bool(
                getattr(event, "_private_companion_segmented_markdown_detected", False)
            )
            and plain_segments
            and len(plain_segments) == len(chunks)
            and await self._send_segmented_event_forward_message(
                event,
                plain_segments,
                source="decorating_result",
            )
        ):
            self._suppress_outbound_reply(
                event,
                source="分段合并转发",
                reason="分段消息已由插件转发",
                history_note="[本轮未发送：分段消息已由插件转发]",
                level="info",
            )
            return
        event.set_result(
            self._build_segmented_result_from_chain(chunks[0], source_result)
        )
        if runtime_persona_setting(self, 'enable_daily_case_review_experiment', False):
            self._record_daily_review_outbound_case(event, chunks[0])
        activity_baseline = time.time()
        if len(chunks) > 1:
            previous_segment = self._segmented_chunk_log_text(chunks[0])
            if has_reaction_intent:
                setattr(
                    event,
                    "_private_companion_reaction_expression_expected_primary_chunks",
                    chunks,
                )
                setattr(
                    event,
                    "_private_companion_reaction_expression_segmented_remainder",
                    {
                        "chunks": chunks[1:],
                        "primary_chunk": chunks[0],
                        "previous_segment": previous_segment,
                        "started_at": activity_baseline,
                        "started": False,
                        "completed": False,
                    },
                )
                logger.info(
                    "表情正文启用有序分段: session=%s segments=%s",
                    _single_line(getattr(event, "unified_msg_origin", ""), 120)
                    or "unknown",
                    len(chunks),
                )
            else:
                self._create_lifecycle_background_task(
                    self._send_segmented_llm_chain_remainder(
                        event,
                        chunks[1:],
                        previous_segment=previous_segment,
                        source="decorating_result",
                        started_at=activity_baseline,
                    ),
                    label="segmented_llm_remainder",
                )

    def _plain_result_body_text(self, chain: list[Any]) -> str:
        """Return text when a result contains only an optional quote and plain body."""
        body = [comp for comp in list(chain or []) if not self._is_reply_component(comp)]
        if not body or any(not isinstance(comp, Plain) for comp in body):
            return ""
        return "".join(str(getattr(comp, "text", "") or "") for comp in body).strip()

    def _segmented_result_from_chain(
        self,
        event: AstrMessageEvent,
        chain: list[Any],
    ) -> Any:
        result = self._build_result_from_chain(chain)
        markdown_mode = getattr(
            event,
            "_private_companion_segmented_markdown_mode",
            None,
        )
        if markdown_mode is None:
            return result
        try:
            setter = getattr(result, "use_markdown", None)
            if callable(setter):
                updated = setter(bool(markdown_mode))
                if updated is not None:
                    result = updated
            elif hasattr(result, "use_markdown_"):
                result.use_markdown_ = bool(markdown_mode)
        except Exception:
            pass
        return result

    def _private_plain_result_allows_segmenting(
        self,
        event: AstrMessageEvent,
        chain: list[Any],
    ) -> bool:
        """Allow plugin text replies while leaving functional command output intact."""
        if not self._plain_result_body_text(chain):
            return False
        # Results produced before the ownership marker was introduced have no
        # reliable producer metadata. Restrict the compatibility path to the
        # two contexts where this plugin historically emits plain fallbacks:
        # private chats and quoted replies. A bare group text may belong to an
        # unrelated plugin and must not be rewritten by this global hook.
        is_private_chat = False
        checker = getattr(event, "is_private_chat", None)
        if callable(checker):
            try:
                is_private_chat = bool(checker())
            except Exception:
                is_private_chat = False
        has_reply_quote = any(self._is_reply_component(comp) for comp in list(chain or []))
        if not is_private_chat and not has_reply_quote:
            return False
        command_reason = getattr(self, "_tts_functional_command_reason", None)
        if callable(command_reason):
            try:
                if command_reason(event):
                    return False
            except Exception:
                pass
        return True

    def _is_reply_component(self, component: Any) -> bool:
        try:
            if Reply is not None and isinstance(component, Reply):
                return True
        except Exception:
            pass
        return component.__class__.__name__.lower() == "reply"

    def _segmented_chunk_log_text(self, chunk: list[Any]) -> str:
        parts: list[str] = []
        for comp in chunk or []:
            if isinstance(comp, Plain):
                text = str(getattr(comp, "text", "") or "").strip()
                if text:
                    parts.append(text)
                continue
            if self._is_reply_component(comp):
                parts.append("[引用]")
            else:
                parts.append(f"[{comp.__class__.__name__}]")
        return " ".join(parts).strip()

    def _plain_text_segments_from_chunks(self, chunks: list[list[Any]]) -> list[str]:
        segments: list[str] = []
        for chunk in chunks or []:
            if not chunk or any(not isinstance(comp, Plain) for comp in chunk):
                return []
            text = "".join(str(getattr(comp, "text", "") or "") for comp in chunk).strip()
            text = self._strip_leading_sentence_boundary_artifacts(text)
            if not text:
                return []
            segments.append(text)
        return segments

    def _segmented_context_chars(self, text: str) -> set[str]:
        text = re.sub(r"</?(?:pc[_-]?tts|t{2,}s)\b[^>]*>", "", str(text or ""), flags=re.IGNORECASE)
        stop_chars = set(
            "的一是不了在有和人就都而及与着或个上也很到说要去会这那我你他她它们"
            "吧呢呀啊吗么啦喔哦噢嘛哈嘿诶哎被把给让才还再又没别刚边里外"
        )
        chars = {ch for ch in text if "\u4e00" <= ch <= "\u9fff" and ch not in stop_chars}
        chars.update(re.findall(r"[a-zA-Z][a-zA-Z0-9_]{1,}", text.lower()))
        return chars

    def _segmented_context_overlap_ratio(self, left: str, right: str) -> float:
        left_chars = self._segmented_context_chars(left)
        right_chars = self._segmented_context_chars(right)
        if not left_chars or not right_chars:
            return 1.0
        return len(left_chars & right_chars) / max(1, min(len(left_chars), len(right_chars)))

    def _segmented_remainder_context_drift_reason(
        self,
        event: AstrMessageEvent,
        *,
        previous_text: str,
        next_text: str,
        source: str = "",
    ) -> str:
        """Stop delayed passive chunks when they look like a different reply turn."""
        segmented_scope = self._segmented_setting(
            "scope",
            event=event,
            default="proactive_only",
        )
        if source != "decorating_result" or segmented_scope != "all_llm":
            return ""
        prev = _single_line(previous_text, 260)
        nxt = _single_line(next_text, 260)
        if not prev or not nxt:
            return ""
        inbound = ""
        getter = getattr(event, "get_message_str", None)
        if callable(getter):
            try:
                inbound = str(getter() or "")
            except Exception:
                inbound = ""
        if not inbound:
            inbound = str(getattr(event, "message_str", "") or "")
        if any(marker in inbound for marker in ("在干嘛", "干什么", "忙什么", "忙啥", "进度", "代码", "项目", "修到", "跑通", "测试", "校验")):
            return ""

        context = f"{inbound}\n{prev}"
        context_chars = self._segmented_context_chars(context)
        next_chars = self._segmented_context_chars(nxt)
        if len(context_chars) < 8 or len(next_chars) < 6:
            return ""
        overlap = self._segmented_context_overlap_ratio(context, nxt)
        if overlap >= 0.08:
            return ""

        food_markers = ("西瓜", "水果", "吃", "甜", "买", "拎", "饭", "餐", "晚饭", "午饭", "口", "手勒", "奖励")
        work_markers = ("逻辑", "校验", "进度", "跑通", "顺手", "焦躁", "代码", "编译", "测试", "调试", "需求", "项目")
        checkin_markers = ("忙完没", "忙完了吗", "忙完了没", "你那边忙", "歇会", "休息一下", "停下来")
        fresh_turn_pattern = r"^\s*(在呢|我在|我这边|这边|刚把|刚刚把|刚刚|我刚|你那边|你这边)"

        context_has_food = any(marker in context for marker in food_markers)
        next_has_work = any(marker in nxt for marker in work_markers)
        next_is_checkin = any(marker in nxt for marker in checkin_markers)
        if context_has_food and (next_has_work or next_is_checkin):
            return "food_topic_to_work_or_checkin"
        if re.search(fresh_turn_pattern, nxt) and (next_has_work or next_is_checkin):
            return "fresh_turn_without_topic_overlap"
        if re.search(r"^\s*(你那边|你这边)", nxt) and next_is_checkin:
            return "new_checkin_without_topic_overlap"
        if "昨晚" in nxt and "昨晚" not in context and re.search(fresh_turn_pattern, nxt):
            return "unexpected_time_anchor"
        return ""

    @staticmethod
    def _strip_llm_segment_marker_lines(text: Any) -> str:
        return strip_llm_segment_marker_lines(text)

    def _clean_segmented_reply_chunks(
        self,
        event: AstrMessageEvent,
        chunks: list[list[Any]],
    ) -> list[list[Any]]:
        cleaned_chunks: list[list[Any]] = []
        removed_internal_control = False
        for chunk in chunks or []:
            cleaned_chunk: list[Any] = []
            for comp in chunk or []:
                if isinstance(comp, Plain):
                    original = str(getattr(comp, "text", "") or "")
                    text = self._sanitize_segmented_plain_text(event, original)
                    removed_internal_control = removed_internal_control or text != original.strip()
                    text = self._strip_leading_sentence_boundary_artifacts(text)
                    if text:
                        cleaned_chunk.append(Plain(text))
                    continue
                cleaned_chunk.append(comp)
            if cleaned_chunk:
                cleaned_chunks.append(cleaned_chunk)
        if removed_internal_control:
            logger.warning(
                "分段前已移除内部控制标记: session=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            )
        return cleaned_chunks

    @staticmethod
    def _default_llm_controlled_segmenting_prompt() -> str:
        return (
            "你可以自行决定是否把你的本轮回复作为多条消息发送；"
            "日常对话、聊天 等需要短句输出的情景建议使用拆分；"
            "长文、教程、代码 等连续性较高的表述尽量少用分段。"
            "\n使用方法：每个消息边界都必须使用下面的完整标记： {{split_marker}}，并让它单独占一行，不能添加引号，也不能把它放进代码块。"
            "\n示例：第一段内容\n{{split_marker}}\n第二段内容。"
            "\n只有上述方法可以实现发送多条消息，换行和连续换行都不会作为多条消息发送。（仅在确有必要时使用）。"
        )

    def _split_llm_controlled_text_for_event(
        self,
        event: AstrMessageEvent | None,
        text: str,
        *,
        umo: str = "",
    ) -> list[str]:
        """Apply explicit LLM boundaries and then spend the remaining rule budget."""
        planned = PrivateCompanionPlugin._split_llm_controlled_text_buffers_for_event(
            self,
            event,
            [text],
            umo=umo,
        )
        return planned[0] if planned else []

    def _split_llm_controlled_text_buffers_for_event(
        self,
        event: AstrMessageEvent | None,
        texts: list[str],
        *,
        umo: str = "",
    ) -> list[list[str]]:
        """Plan LLM and plugin boundaries across every text buffer in a chain."""
        raw_buffers = [str(text or "").strip() for text in texts]
        if not raw_buffers:
            return []
        if not bool(runtime_persona_setting(self, "enable_segmented_proactive_reply", False)):
            return [
                [cleaned] if (cleaned := sanitize_llm_segment_control_tokens(text)) else []
                for text in raw_buffers
            ]
        if not bool(runtime_persona_setting(self, "enable_llm_controlled_segmenting", False)):
            return [
                self._split_proactive_text(cleaned, event=event, umo=umo) if cleaned else []
                for text in raw_buffers
                for cleaned in [sanitize_llm_segment_control_tokens(text)]
            ]
        plugin_rules_enabled = bool(
            runtime_persona_setting(self, "enable_segmented_plugin_rules", True)
        )

        def apply_common_transforms(segment: str) -> str:
            candidate = self._split_proactive_text(
                segment,
                event=event,
                umo=umo,
                force_common_transforms=True,
                common_transforms_only=True,
            )
            return str(candidate[0] if candidate else "").strip()

        parsed_buffers: list[list[str]] = []
        controlled_buffers: list[bool] = []
        parse_results = []
        for raw_text in raw_buffers:
            parsed = parse_llm_segment_control(raw_text)
            parse_results.append(parsed)
            parsed_buffers.append(
                list(parsed.segments)
                if parsed.controlled
                else ([parsed.sanitized_text] if parsed.sanitized_text else [])
            )
            controlled_buffers.append(parsed.controlled)

        if event is not None:
            setattr(
                event,
                "_private_companion_llm_segment_diagnostics",
                {
                    "exact": sum(item.exact_boundary_count for item in parse_results),
                    "recovered": sum(item.recovered_boundary_count for item in parse_results),
                    "cleaned_only": sum(item.cleaned_only_count for item in parse_results),
                },
            )
            for attr_name in (
                "_private_companion_llm_history_segments",
                "_private_companion_llm_planned_chunk_texts",
                "_private_companion_llm_planned_segment_ids",
            ):
                try:
                    delattr(event, attr_name)
                except AttributeError:
                    pass
            if len(parse_results) == 1 and parse_results[0].controlled:
                history_segments = [
                    cleaned
                    for segment in parse_results[0].segments
                    if (cleaned := apply_common_transforms(segment))
                ]
                if len(history_segments) >= 2:
                    setattr(
                        event,
                        "_private_companion_llm_history_segments",
                        tuple(history_segments),
                    )

        def split_uncontrolled_buffer(text: str, *, suppress_plugin_rules: bool = False) -> list[str]:
            if not text:
                return []
            if suppress_plugin_rules:
                transformed = apply_common_transforms(text)
                return [transformed] if transformed else []
            return self._split_proactive_text(text, event=event, umo=umo)

        if not any(controlled_buffers):
            if plugin_rules_enabled:
                return [
                    split_uncontrolled_buffer(
                        parsed.sanitized_text,
                        suppress_plugin_rules=parsed.suppress_plugin_rule_split,
                    )
                    for parsed in parse_results
                ]
            return [
                [transformed]
                if (transformed := apply_common_transforms(parsed.sanitized_text))
                else []
                for parsed in parse_results
            ]

        llm_count = sum(len(segments) for segments in parsed_buffers)
        if event is not None:
            setattr(event, "_private_companion_llm_segment_count", llm_count)
        if not plugin_rules_enabled or any(
            item.suppress_plugin_rule_split for item in parse_results
        ):
            planned = [
                [
                    cleaned
                    for segment in segments
                    if (cleaned := apply_common_transforms(segment))
                ]
                for segments in parsed_buffers
            ]
            if event is not None and len(planned) == 1 and len(planned[0]) >= 2:
                setattr(
                    event,
                    "_private_companion_llm_planned_segment_ids",
                    tuple(range(len(planned[0]))),
                )
            return planned

        max_segments = max(
            1,
            _safe_int(
                self._segmented_setting("max_segments", event=event, umo=umo, default=3),
                3,
                1,
                8,
            ),
        )
        if llm_count >= max_segments:
            planned = [
                [
                    cleaned
                    for segment in segments
                    if (cleaned := apply_common_transforms(str(segment or "")))
                ]
                for segments in parsed_buffers
            ]
            if event is not None and len(planned) == 1 and len(planned[0]) >= 2:
                setattr(
                    event,
                    "_private_companion_llm_planned_segment_ids",
                    tuple(range(len(planned[0]))),
                )
            return planned

        result: list[list[list[str] | str]] = [list(segments) for segments in parsed_buffers]
        rule_processed: set[tuple[int, int]] = set()
        remaining = max_segments - llm_count
        candidate_indices = sorted(
            (
                (buffer_index, segment_index)
                for buffer_index, segments in enumerate(parsed_buffers)
                for segment_index in range(len(segments))
                if not has_fenced_llm_segment_marker(segments[segment_index])
            ),
            key=lambda position: (
                -len(str(parsed_buffers[position[0]][position[1]])),
                position[0],
                position[1],
            ),
        )
        for buffer_index, segment_index in candidate_indices:
            if remaining <= 0:
                break
            candidate = self._split_proactive_text(
                str(parsed_buffers[buffer_index][segment_index]),
                event=event,
                umo=umo,
                max_segments_override=remaining + 1,
            )
            additions = max(0, len(candidate) - 1)
            if additions <= 0 or additions > remaining:
                continue
            result[buffer_index][segment_index] = candidate
            rule_processed.add((buffer_index, segment_index))
            remaining -= additions

        flattened_buffers: list[list[str]] = []
        flattened_ids_by_buffer: list[list[int]] = []
        for buffer_index, segments in enumerate(result):
            flattened: list[str] = []
            flattened_ids: list[int] = []
            for segment_index, item in enumerate(segments):
                if isinstance(item, list):
                    for part in item:
                        clean_part = str(part or "").strip()
                        if clean_part:
                            flattened.append(clean_part)
                            flattened_ids.append(segment_index)
                    continue
                transformed = (
                    str(item or "").strip()
                    if (buffer_index, segment_index) in rule_processed
                    else apply_common_transforms(str(item or ""))
                )
                if transformed:
                    flattened.append(transformed)
                    flattened_ids.append(segment_index)
            flattened_buffers.append(flattened)
            flattened_ids_by_buffer.append(flattened_ids)
        if (
            event is not None
            and len(flattened_buffers) == 1
            and len(set(flattened_ids_by_buffer[0])) >= 2
        ):
            setattr(
                event,
                "_private_companion_llm_planned_segment_ids",
                tuple(flattened_ids_by_buffer[0]),
            )
        return flattened_buffers

    def _segment_llm_reply_chain(self, event: AstrMessageEvent, chain: list[Any]) -> tuple[list[list[Any]], bool, str]:
        working_chain = list(chain or [])
        # Apply the same configured cleanup before segmenting joined text.
        _strip_chain_plain_thinking(self, working_chain)
        reply_prefix = [comp for comp in working_chain if self._is_reply_component(comp)]
        content_chain = [comp for comp in working_chain if not self._is_reply_component(comp)]
        if (
            bool(runtime_persona_setting(self, 'enable_proactive_quote_trigger_message', False))
            and bool(runtime_persona_setting(self, 'enable_quote_group_reply', True))
            and not reply_prefix
            and not self._chain_has_reply_component(working_chain)
        ):
            quote_message_id = self._group_current_reply_quote_message_id(
                event,
                text_or_chain=content_chain,
            )
            reply = self._make_reply_component(quote_message_id, event=event)
            if reply is not None:
                working_chain = [reply, *working_chain]

        llm_controlled = bool(
            runtime_persona_setting(self, "enable_llm_controlled_segmenting", False)
        )
        prepared_buffers: list[list[str]] = []
        if llm_controlled:
            raw_buffers: list[str] = []
            plain_buffer: list[str] = []

            def flush_plain_buffer() -> None:
                if not plain_buffer:
                    return
                raw_text = "".join(plain_buffer).strip()
                plain_buffer.clear()
                if raw_text:
                    raw_buffers.append(raw_text)

            for component in working_chain:
                if self._is_reply_component(component):
                    continue
                if isinstance(component, Plain):
                    plain_buffer.append(str(getattr(component, "text", "") or ""))
                    continue
                flush_plain_buffer()
            flush_plain_buffer()
            prepared_buffers = self._split_llm_controlled_text_buffers_for_event(
                event,
                raw_buffers,
            )
        prepared_iter = iter(prepared_buffers)

        def split_text_buffer(text: str) -> list[str]:
            if llm_controlled:
                try:
                    return next(prepared_iter)
                except StopIteration:
                    return [str(text or "").strip()]
            return self._split_proactive_text(text, event=event)

        chunks, changed, _split_changed, full_text = plan_component_chunks(
            working_chain,
            plain_type=Plain,
            split_text=split_text_buffer,
            strategies=component_strategies_from_owner(self),
            component_order=component_order_from_owner(self),
            classify=component_kind,
        )
        if not full_text:
            return [], False, ""
        full_text = sanitize_llm_segment_control_tokens(full_text)
        final_chunks = self._clean_segmented_reply_chunks(event, chunks) if changed else [chain]
        history_segments = getattr(
            event,
            "_private_companion_llm_history_segments",
            (),
        )
        if isinstance(history_segments, tuple) and len(history_segments) >= 2:
            planned_texts = [
                self._plain_result_body_text(chunk)
                for chunk in final_chunks
            ]
            planned_ids = getattr(
                event,
                "_private_companion_llm_planned_segment_ids",
                (),
            )
            if (
                planned_texts
                and all(planned_texts)
                and isinstance(planned_ids, tuple)
                and len(planned_ids) == len(planned_texts)
            ):
                setattr(
                    event,
                    "_private_companion_llm_planned_chunk_texts",
                    tuple(planned_texts),
                )
            else:
                for attr_name in (
                    "_private_companion_llm_history_segments",
                    "_private_companion_llm_planned_segment_ids",
                ):
                    try:
                        delattr(event, attr_name)
                    except AttributeError:
                        pass
        return final_chunks, changed, full_text

    @staticmethod
    def _event_can_deliver_directly(event: AstrMessageEvent) -> bool:
        """判断 ``event.send()`` 是否真的会把消息投递到平台。

        AstrBot 基类 ``AstrMessageEvent.send()`` 是空实现：只上传一次埋点、
        设置 ``_has_send_oper`` 标志位，既不发送也不抛异常；只有平台适配器子类
        才重写它。外部插件（例如屏幕伴侣）会自行构造基类合成事件来触发
        ``OnDecoratingResultEvent``，这类事件调用 ``send()`` 会静默丢弃消息，
        必须改走平台直发。

        返回 True 表示可以安全使用 ``event.send()``。
        """
        try:
            # 本项目导入的 AstrMessageEvent 就是基类本体
            # （astrbot.api.event → astrbot.core.platform → astr_message_event）。
            return type(event).send is not AstrMessageEvent.send
        except Exception:
            # 判定失败时保持原行为，避免误伤正常链路
            return True

    async def _send_segmented_remainder_chain(
        self,
        event: AstrMessageEvent,
        chain: list[Any],
    ) -> str:
        """Send delayed chunks through a live platform route when the source event is proactive."""
        external_proactive = (
            str(getattr(event, "_private_companion_external_proactive_source", "") or "")
            == "proactive_chat"
        )
        proactive_delivery_umo = _single_line(
            getattr(event, "_private_companion_proactive_delivery_umo", ""),
            240,
        )
        if (
            external_proactive
            or proactive_delivery_umo
            or not self._event_can_deliver_directly(event)
        ):
            umo = proactive_delivery_umo or _single_line(
                getattr(event, "unified_msg_origin", ""),
                240,
            )
            sender = getattr(self, "_send_chain_components", None)
            if not umo or not callable(sender):
                raise RuntimeError("主动分段补发缺少可用的平台发送入口")
            accepted = await sender(
                umo,
                list(chain),
                apply_decorating_hooks=False,
            )
            if not accepted:
                raise RuntimeError("主动分段补发未被平台接受")
            return "platform"
        markdown_mode = getattr(
            event,
            "_private_companion_segmented_markdown_mode",
            None,
        )
        if markdown_mode is not None:
            await event.send(self._segmented_result_from_chain(event, chain))
        else:
            try:
                await event.send(event.chain_result(chain))
            except Exception:
                await event.send(self._build_result_from_chain(chain))
        return "event"

    async def _send_segmented_llm_chain_remainder(
        self,
        event: AstrMessageEvent,
        chunks: list[list[Any]],
        *,
        previous_segment: str = "",
        source: str = "",
        started_at: float | None = None,
    ) -> None:
        """后台补发被动分段的剩余组件片段；只拆文本，媒体组件保持原子发送。"""
        prev = previous_segment
        total = len([item for item in chunks if item])
        sent_index = 0
        case_id = _single_line(getattr(event, "_private_companion_daily_review_case_id", ""), 20)
        proactive_delivery_umo = _single_line(
            getattr(event, "_private_companion_proactive_delivery_umo", ""),
            240,
        )
        scope = self._event_scope_key(event)
        async with self._segmented_remainder_lock(scope):
            for chunk in chunks:
                if not chunk:
                    continue
                sent_index += 1
                try:
                    preview = self._segmented_chunk_log_text(chunk)
                    outbound_chunk = chunk
                    drift_reason = self._segmented_remainder_context_drift_reason(
                        event,
                        previous_text=prev,
                        next_text=preview,
                        source=source,
                    )
                    if drift_reason:
                        if case_id:
                            self._update_daily_review_case(
                                case_id,
                                outcome="incomplete",
                                signals={"stop_reason": drift_reason, "segments_expected": total + 1, "segments_sent": sent_index},
                            )
                        logger.info(
                            "分段剩余组件疑似上下文割裂，停止发送: source=%s reason=%s sent=%s/%s prev=%s next=%s",
                            source or "unknown",
                            drift_reason,
                            max(0, sent_index - 1),
                            total,
                            _single_line(prev, 120),
                            _single_line(preview, 120),
                        )
                        return
                    wait_for = prev or preview
                    delay = await self._calc_segmented_proactive_interval(wait_for, event=event)
                    if delay > 0:
                        await asyncio.sleep(delay)
                    recalled_message_id = await self._should_cancel_reply_for_missing_or_recalled_trigger(event)
                    if recalled_message_id:
                        if case_id:
                            self._update_daily_review_case(
                                case_id,
                                outcome="incomplete",
                                signals={"stop_reason": "trigger_recalled", "segments_expected": total + 1, "segments_sent": sent_index},
                            )
                        logger.info(
                            "触发消息已撤回或发送前不可见，停止发送分段剩余组件: source=%s message_id=%s sent=%s/%s",
                            source or "unknown",
                            recalled_message_id,
                            max(0, sent_index - 1),
                            total,
                        )
                        return
                    if chunk and all(isinstance(comp, Plain) for comp in chunk):
                        normalized_segment = "".join(str(getattr(comp, "text", "") or "") for comp in chunk).strip()
                        normalizer = getattr(self, "_normalize_tts_tags", None)
                        if callable(normalizer) and re.search(r"</?(?:pc[_-]?tts|t{2,}s)\b", normalized_segment, flags=re.IGNORECASE):
                            try:
                                normalized_segment = str(normalizer(normalized_segment) or normalized_segment).strip()
                            except Exception:
                                pass
                        if (
                            bool(runtime_persona_setting(self, 'enable_tts_enhancement', False))
                            and re.search(r"<tts\b[^>]*>.*?</tts>", normalized_segment, flags=re.IGNORECASE | re.DOTALL)
                        ):
                            processor = getattr(self, "_process_tts_tags", None)
                            if callable(processor):
                                fallback_plain = re.sub(r"</?(?:pc[_-]?tts|t{2,}s)\b[^>]*>", "", normalized_segment, flags=re.IGNORECASE).strip()
                                processed_chunk = await processor(normalized_segment, event, fallback_plain=fallback_plain)
                                if processed_chunk:
                                    outbound_chunk = processed_chunk
                        elif re.search(r"</?(?:pc[_-]?tts|t{2,}s)\b", normalized_segment, flags=re.IGNORECASE):
                            cleaned = re.sub(r"</?(?:pc[_-]?tts|t{2,}s)\b[^>]*>", "", normalized_segment, flags=re.IGNORECASE).strip()
                            outbound_chunk = [Plain(cleaned)] if cleaned else []
                    if not outbound_chunk:
                        continue
                    sanitized_chunk: list[Any] = []
                    leaked_tools: list[str] = []
                    for component in outbound_chunk:
                        if not isinstance(component, Plain):
                            sanitized_chunk.append(component)
                            continue
                        original_text = str(getattr(component, "text", "") or "")
                        visible_text = self._sanitize_segmented_plain_text(event, original_text)
                        cleaned_text, calls = self._strip_plaintext_tool_call_envelopes(
                            visible_text
                        )
                        leaked_tools.extend(str(item.get("name") or "") for item in calls)
                        if cleaned_text:
                            sanitized_chunk.append(
                                Plain(cleaned_text)
                                if calls or cleaned_text != original_text else component
                            )
                    if leaked_tools:
                        logger.warning(
                            "分段组件发送前已移除明文工具调用: tools=%s",
                            ",".join(leaked_tools),
                        )
                    outbound_chunk = sanitized_chunk
                    if not outbound_chunk:
                        continue
                    hit = self._forbidden_recall_hit(self._chain_text_for_forbidden_recall(outbound_chunk))
                    if hit:
                        if case_id:
                            self._update_daily_review_case(
                                case_id,
                                outcome="incomplete",
                                signals={"stop_reason": "forbidden_recall", "segments_expected": total + 1, "segments_sent": sent_index},
                            )
                        logger.warning("分段剩余组件命中违禁词，停止发送: word=%s", _single_line(hit, 40))
                        return
                    delivery_path = await self._send_segmented_remainder_chain(
                        event,
                        outbound_chunk,
                    )
                    if case_id:
                        self._update_daily_review_case(
                            case_id,
                            append_output=self._segmented_chunk_log_text(outbound_chunk),
                            outcome="delivered" if sent_index >= total else "delivery_pending",
                            signals={"segments_expected": total + 1, "segments_sent": sent_index + 1},
                        )
                    logger.info(
                        "分段 LLM 剩余组件已发送: source=%s delivery=%s index=%s/%s preview=%s",
                        source or "unknown",
                        delivery_path,
                        sent_index,
                        total,
                        _single_line(preview, 120),
                    )
                    prev = preview
                except asyncio.CancelledError:
                    if case_id:
                        self._update_daily_review_case(
                            case_id,
                            outcome="incomplete",
                            signals={"stop_reason": "task_cancelled", "segments_expected": total + 1, "segments_sent": sent_index},
                        )
                    raise
                except Exception as exc:
                    if (
                        str(getattr(event, "_private_companion_external_proactive_source", "") or "")
                        == "proactive_chat"
                        or proactive_delivery_umo
                    ):
                        if case_id:
                            self._update_daily_review_case(
                                case_id,
                                outcome="delivery_failed",
                                signals={"segments_expected": total + 1, "segments_sent": sent_index},
                            )
                        logger.warning(
                            "主动分段 LLM 剩余组件发送失败: source=%s error=%s",
                            source or "unknown",
                            _single_line(exc, 160),
                            exc_info=True,
                        )
                        return
                    try:
                        if not self._event_can_deliver_directly(event):
                            sender = getattr(self, "_send_chain_components", None)
                            fallback_umo = _single_line(
                                getattr(event, "unified_msg_origin", ""),
                                240,
                            )
                            if not fallback_umo or not callable(sender):
                                raise RuntimeError("被动分段补发缺少可用的平台发送入口")
                            accepted = await sender(
                                fallback_umo,
                                list(outbound_chunk),
                                apply_decorating_hooks=False,
                            )
                            if not accepted:
                                raise RuntimeError("被动分段补发未被平台接受")
                        else:
                            await event.send(
                                self._segmented_result_from_chain(event, outbound_chunk)
                            )
                        if case_id:
                            self._update_daily_review_case(
                                case_id,
                                append_output=self._segmented_chunk_log_text(outbound_chunk),
                                outcome="delivered" if sent_index >= total else "delivery_pending",
                                signals={"segments_expected": total + 1, "segments_sent": sent_index + 1},
                            )
                        logger.info(
                            "分段 LLM 剩余组件已发送: source=%s index=%s/%s preview=%s",
                            source or "unknown",
                            sent_index,
                            total,
                            _single_line(self._segmented_chunk_log_text(chunk), 120),
                        )
                        prev = self._segmented_chunk_log_text(chunk)
                    except Exception:
                        if case_id:
                            self._update_daily_review_case(
                                case_id,
                                outcome="delivery_failed",
                                signals={"segments_expected": total + 1, "segments_sent": sent_index},
                            )
                        logger.warning(
                            "分段 LLM 剩余组件发送失败: source=%s error=%s",
                            source or "unknown",
                            _single_line(exc, 160),
                            exc_info=True,
                        )
                        return

    async def _send_segmented_llm_reply_remainder(
        self,
        event: AstrMessageEvent,
        segments: list[str],
        *,
        previous_segment: str = "",
        source: str = "",
        started_at: float | None = None,
    ) -> None:
        """后台补发被动分段的剩余片段，避免阻塞主链首包。"""
        prev = previous_segment
        total = len([item for item in segments if str(item or "").strip()])
        sent_index = 0
        for segment in segments:
            segment = str(segment or "").strip()
            if not segment:
                continue
            segment, leaked_calls = self._strip_plaintext_tool_call_envelopes(segment)
            if leaked_calls:
                logger.warning(
                    "分段文本发送前已移除明文工具调用: tools=%s",
                    ",".join(str(item.get("name") or "") for item in leaked_calls),
                )
            if not segment:
                continue
            sent_index += 1
            try:
                drift_reason = self._segmented_remainder_context_drift_reason(
                    event,
                    previous_text=prev,
                    next_text=segment,
                    source=source,
                )
                if drift_reason:
                    logger.info(
                        "分段剩余片段疑似上下文割裂，停止发送: source=%s reason=%s sent=%s/%s prev=%s next=%s",
                        source or "unknown",
                        drift_reason,
                        max(0, sent_index - 1),
                        total,
                        _single_line(prev, 120),
                        _single_line(segment, 120),
                    )
                    return
                wait_for = prev or segment
                delay = await self._calc_segmented_proactive_interval(wait_for, event=event)
                if delay > 0:
                    await asyncio.sleep(delay)
                recalled_message_id = await self._should_cancel_reply_for_missing_or_recalled_trigger(event)
                if recalled_message_id:
                    logger.info(
                        "触发消息已撤回或发送前不可见，停止发送分段剩余片段: source=%s message_id=%s sent=%s/%s",
                        source or "unknown",
                        recalled_message_id,
                        max(0, sent_index - 1),
                        total,
                    )
                    return
                sent_tts_chain = False
                normalized_segment = segment
                normalizer = getattr(self, "_normalize_tts_tags", None)
                if callable(normalizer) and re.search(r"</?(?:pc[_-]?tts|t{2,}s)\b", normalized_segment, flags=re.IGNORECASE):
                    try:
                        normalized_segment = str(normalizer(normalized_segment) or normalized_segment).strip()
                    except Exception:
                        pass
                if (
                    bool(runtime_persona_setting(self, 'enable_tts_enhancement', False))
                    and re.search(r"<tts\b[^>]*>.*?</tts>", normalized_segment, flags=re.IGNORECASE | re.DOTALL)
                ):
                        processor = getattr(self, "_process_tts_tags", None)
                        if callable(processor):
                            fallback_plain = re.sub(r"</?(?:pc[_-]?tts|t{2,}s)\b[^>]*>", "", normalized_segment, flags=re.IGNORECASE).strip()
                            chain = await processor(normalized_segment, event, fallback_plain=fallback_plain)
                            if chain:
                                hit = self._forbidden_recall_hit(self._chain_text_for_forbidden_recall(chain))
                                if hit:
                                    logger.warning("分段 TTS 剩余片段命中违禁词，停止发送: word=%s", _single_line(hit, 40))
                                    return
                                try:
                                    await event.send(event.chain_result(chain))
                                except Exception:
                                    await event.send(self._build_result_from_chain(chain))
                                sent_tts_chain = True
                if not sent_tts_chain:
                    outbound = re.sub(r"</?(?:pc[_-]?tts|t{2,}s)\b[^>]*>", "", normalized_segment, flags=re.IGNORECASE).strip() or segment
                    hit = self._forbidden_recall_hit(outbound)
                    if hit:
                        logger.warning("分段剩余片段命中违禁词，停止发送: word=%s", _single_line(hit, 40))
                        return
                    await event.send(event.plain_result(outbound))
                logger.info(
                    "分段 LLM 剩余片段已发送: source=%s index=%s/%s preview=%s",
                    source or "unknown",
                    sent_index,
                    total,
                    _single_line(segment, 120),
                )
                prev = segment
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "分段 LLM 剩余片段发送失败: source=%s error=%s",
                    source or "unknown",
                    _single_line(exc, 160),
                    exc_info=True,
                )
                return


    @staticmethod
    def _normalize_provider_config_mode(value: Any, config: Any = None) -> str:
        text = str(value or "").strip().lower()
        aliases = {
            "quick": "quick",
            "fast": "quick",
            "simple": "quick",
            "快速": "quick",
            "快速配置": "quick",
            "precision": "precision",
            "precise": "precision",
            "advanced": "precision",
            "detail": "precision",
            "detailed": "precision",
            "精准": "precision",
            "精准配置": "precision",
            "分流": "precision",
            "分流模型": "precision",
        }
        if text in aliases:
            return aliases[text]
        if text in {"quick", "precision"}:
            return text

        precision_keys = (
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
            "NEWS_PROVIDER_ID",
            "WEB_EXPLORATION_PROVIDER_ID",
        )
        if any(str(_flat_get(config, key, "") or "").strip() for key in precision_keys):
            return "precision"
        return "quick"

    @staticmethod
    def _normalize_external_image_api_platform(value: Any) -> str:
        text = str(value or "").strip().lower()
        aliases = {
            "auto": "auto",
            "自动": "auto",
            "openai": "openai",
            "openai-compatible": "openai",
            "openai_compatible": "openai",
            "openai兼容": "openai",
            "兼容": "openai",
            "兼容模式": "openai",
            "external": "openai",
            "openrouter": "openrouter",
            "open-router": "openrouter",
            "open_router": "openrouter",
            "openrouter.ai": "openrouter",
            "agnes": "agnes",
            "agnes-ai": "agnes",
            "agnes_ai": "agnes",
            "agnes image": "agnes",
            "agnes-image": "agnes",
            "sapiens": "agnes",
            "sapiens ai": "agnes",
            "bailian": "bailian",
            "dashscope": "bailian",
            "aliyun": "bailian",
            "alibaba": "bailian",
            "modelstudio": "bailian",
            "model_studio": "bailian",
            "百炼": "bailian",
            "阿里云百炼": "bailian",
            "通义万相": "bailian",
            "modelscope": "modelscope",
            "model_scope": "modelscope",
            "魔搭": "modelscope",
            "魔搭社区": "modelscope",
            "api-inference": "modelscope",
            "doubao": "doubao",
            "豆包": "doubao",
            "火山": "doubao",
            "火山引擎": "doubao",
            "volcengine": "doubao",
            "volces": "doubao",
            "ark": "doubao",
            "seedream": "doubao",
            "seed": "doubao",
            "gemini": "gemini",
            "google": "gemini",
            "google-ai": "gemini",
            "google_ai": "gemini",
            "generativelanguage": "gemini",
            "nano-banana": "gemini",
            "sensenova": "sensenova",
            "sense-nova": "sensenova",
            "日日新": "sensenova",
            "商汤日日新": "sensenova",
            "minimax": "minimax",
            "minimaxi": "minimax",
            "minimax-ai": "minimax",
            "minimax_ai": "minimax",
            "海螺": "minimax",
            "海螺ai": "minimax",
        }
        return aliases.get(text, text if text in {"auto", "openai", "openrouter", "agnes", "bailian", "modelscope", "doubao", "gemini", "sensenova", "minimax"} else "auto")

    @staticmethod
    def _normalize_external_image_endpoint_enabled(value: Any, default: bool = True) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        text = str(value).strip().lower()
        if text in {"true", "1", "yes", "y", "on", "enable", "enabled", "启用", "开启", "开", "是"}:
            return True
        if text in {"false", "0", "no", "n", "off", "disable", "disabled", "停用", "关闭", "关", "否", ""}:
            return False
        return default

    def _normalize_external_image_api_endpoint(self, item: Any, *, index: int = 0) -> dict[str, Any]:
        if not isinstance(item, dict):
            return {}

        def pick(*keys: str, default: Any = "") -> Any:
            for key in keys:
                if key in item and item.get(key) not in (None, ""):
                    return item.get(key)
            return default

        endpoint = {
            "name": _single_line(pick("name", "label", "title", default=f"在线 API {index + 1}"), 80) or f"在线 API {index + 1}",
            "enabled": self._normalize_external_image_endpoint_enabled(pick("enabled", "enable", "active", default=True), True),
            "platform": self._normalize_external_image_api_platform(
                pick("platform", "external_image_api_platform", "image_api_platform", default="auto")
            ),
            "base_url": str(
                pick(
                    "base_url",
                    "api_base",
                    "api_base_url",
                    "url",
                    "endpoint",
                    "EXTERNAL_IMAGE_API_BASE_URL",
                    "BACKUP_EXTERNAL_IMAGE_API_BASE_URL",
                    default="",
                )
                or ""
            ).strip(),
            "api_key": str(
                pick(
                    "api_key",
                    "key",
                    "token",
                    "EXTERNAL_IMAGE_API_KEY",
                    "BACKUP_EXTERNAL_IMAGE_API_KEY",
                    default="",
                )
                or ""
            ).strip(),
            "model": str(
                pick(
                    "model",
                    "model_name",
                    "EXTERNAL_IMAGE_API_MODEL",
                    "BACKUP_EXTERNAL_IMAGE_API_MODEL",
                    default="",
                )
                or ""
            ).strip(),
            "size": str(
                pick("size", "image_size", "external_image_api_size", "backup_external_image_api_size", default="1024x1024")
                or "1024x1024"
            ).strip()
            or "1024x1024",
            "ratio": _single_line(pick("ratio", "aspect_ratio", "image_ratio", default=""), 20),
            "timeout_seconds": _safe_int(
                pick(
                    "timeout_seconds",
                    "timeout",
                    "external_image_api_timeout_seconds",
                    "backup_external_image_api_timeout_seconds",
                    default=180,
                ),
                180,
                20,
                600,
            ),
            "custom_headers": str(
                pick(
                    "custom_headers",
                    "headers",
                    "external_image_api_custom_headers",
                    "backup_external_image_api_custom_headers",
                    default="",
                )
                or ""
            ).strip(),
        }
        base_lower = str(endpoint.get("base_url") or "").lower()
        model_lower = str(endpoint.get("model") or "").lower()
        parsed_base = urlparse(base_lower if "://" in base_lower else f"https://{base_lower}")
        base_host = str(parsed_base.hostname or "").strip().lower()
        if endpoint["platform"] in {"auto", "openai", "openrouter"} and (
            base_host == "openrouter.ai" or base_host.endswith(".openrouter.ai")
        ):
            endpoint["platform"] = "openrouter"
        if endpoint["platform"] in {"auto", "openai"} and (
            "apihub.agnes-ai.com" in base_lower or model_lower.startswith("agnes-image-")
        ):
            endpoint["platform"] = "agnes"
        minimax_official_host = any(
            host in base_lower
            for host in ("api.minimaxi.com", "api.minimax.io", "minimaxi.com", "minimax.io")
        )
        if (
            endpoint["platform"] in {"auto", "openai"} and minimax_official_host
        ) or (
            endpoint["platform"] == "auto" and model_lower in {"image-01", "image-01-live"}
        ):
            endpoint["platform"] = "minimax"
        if endpoint["platform"] == "minimax" and re.search(
            r"/v1/(?:image_generation|image/generation|images/generations|images/edits)/?(?:[?#].*)?$",
            str(endpoint.get("base_url") or ""),
            flags=re.I,
        ):
            base_normalizer = getattr(self, "_normalized_external_image_api_base_url", None)
            if callable(base_normalizer):
                normalized_root = base_normalizer(endpoint["base_url"], platform="minimax")
                if normalized_root:
                    endpoint["base_url"] = f"{normalized_root.rstrip('/')}/image_generation"
        if endpoint["platform"] == "auto" and ("token.sensenova.cn" in base_lower or model_lower in {"senova-u1-fast", "sensenova-u1-fast"}):
            endpoint["platform"] = "sensenova"
        if endpoint["platform"] == "sensenova" and model_lower == "senova-u1-fast":
            endpoint["model"] = "sensenova-u1-fast"
        return endpoint

    def _normalize_external_image_api_endpoints(self, value: Any) -> list[dict[str, Any]]:
        raw = value
        if isinstance(raw, str):
            text = raw.strip()
            if not text:
                raw = []
            else:
                try:
                    parsed = json.loads(text)
                    raw = parsed
                except Exception:
                    lines = [line.strip() for line in text.splitlines() if line.strip()]
                    raw = [{"base_url": line} for line in lines]
        if isinstance(raw, dict):
            raw = raw.get("items") or raw.get("endpoints") or raw.get("apis") or []
        if not isinstance(raw, list):
            return []
        normalized: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str, str]] = set()
        for index, item in enumerate(raw[:12]):
            endpoint = self._normalize_external_image_api_endpoint(item, index=index)
            if not endpoint:
                continue
            if not any(str(endpoint.get(key) or "").strip() for key in ("base_url", "api_key", "model", "custom_headers")):
                continue
            signature = (
                str(endpoint.get("platform") or "auto").lower(),
                str(endpoint.get("base_url") or "").rstrip("/"),
                str(endpoint.get("model") or ""),
                str(endpoint.get("api_key") or "")[:12],
            )
            if signature in seen:
                continue
            seen.add(signature)
            normalized.append(endpoint)
        return normalized

    def _apply_quick_provider_defaults(self) -> None:
        fast = str(getattr(self, "fast_response_provider_id", "") or "").strip()
        complex_model = str(getattr(self, "complex_reasoning_provider_id", "") or "").strip()
        creative = str(getattr(self, "creative_model_provider_id", "") or "").strip()
        plugin_vision = str(getattr(self, "plugin_vision_provider_id", "") or "").strip()
        config = getattr(self, "config", None)

        def configured_provider(config_key: str, fallback: str = "") -> str:
            # Preserve an explicit empty value while tolerating older configs
            # that do not yet contain the independent vision key.
            raw = self._cfg_raw(config, config_key, None)
            return fallback if raw is None else str(raw or "").strip()

        # These routes are independent of quick/precision text assignment and
        # must be refreshed in either mode when the page saves a new value.
        for attr, config_key in (
            ("embedding_provider_id", "EMBEDDING_PROVIDER_ID"),
            ("group_member_safety_provider_id", "GROUP_MEMBER_SAFETY_PROVIDER_ID"),
            ("reaction_expression_embedding_provider_id", "REACTION_EXPRESSION_EMBEDDING_PROVIDER_ID"),
            ("deepseek_peak_replacement_provider_id", "DEEPSEEK_PEAK_REPLACEMENT_PROVIDER_ID"),
            ("sensitive_replacement_provider_id", "SENSITIVE_REPLACEMENT_PROVIDER_ID"),
        ):
            setattr(self, attr, self._cfg_str(config, config_key, ""))

        attr_config_keys = {
            "llm_provider_id": "LLM_PROVIDER_ID",
            "mai_style_provider_id": "MAI_STYLE_PROVIDER_ID",
            "daily_plan_provider_id": "DAILY_PLAN_PROVIDER_ID",
            "detail_enhancement_provider_id": "DETAIL_ENHANCEMENT_PROVIDER_ID",
            "history_summary_provider_id": "HISTORY_SUMMARY_PROVIDER_ID",
            "relationship_analysis_provider_id": "RELATIONSHIP_ANALYSIS_PROVIDER_ID",
            "companion_memory_provider_id": "COMPANION_MEMORY_PROVIDER_ID",
            "dialogue_episode_provider_id": "DIALOGUE_EPISODE_PROVIDER_ID",
            "group_episode_provider_id": "GROUP_EPISODE_PROVIDER_ID",
            "forward_message_provider_id": "FORWARD_MESSAGE_PROVIDER_ID",
            "proactive_persona_judge_provider_id": "PROACTIVE_PERSONA_JUDGE_PROVIDER_ID",
            "response_review_provider_id": "RESPONSE_REVIEW_PROVIDER_ID",
            "smart_silence_provider_id": "SMART_SILENCE_PROVIDER_ID",
            "troubleshooting_provider_id": "TROUBLESHOOTING_PROVIDER_ID",
            "daily_review_provider_id": "DAILY_REVIEW_PROVIDER_ID",
            "emotion_judgement_provider_id": "EMOTION_JUDGEMENT_PROVIDER_ID",
            "smart_message_debounce_provider_id": "SMART_MESSAGE_DEBOUNCE_PROVIDER_ID",
            "rest_wakeup_provider_id": "REST_WAKEUP_PROVIDER_ID",
            "group_followup_judge_provider_id": "GROUP_FOLLOWUP_JUDGE_PROVIDER_ID",
            "group_interject_provider_id": "GROUP_INTERJECT_PROVIDER_ID",
            "group_slang_provider_id": "GROUP_SLANG_PROVIDER_ID",
            "voice_prompt_provider_id": "VOICE_PROMPT_PROVIDER_ID",
            "tts_conversion_provider_id": "tts_conversion_provider_id",
            "narration_provider_id": "NARRATION_PROVIDER_ID",
            "news_provider_id": "NEWS_PROVIDER_ID",
            "web_exploration_provider_id": "WEB_EXPLORATION_PROVIDER_ID",
            "creative_provider_id": "CREATIVE_PROVIDER_ID",
            "creative_outline_provider_id": "CREATIVE_OUTLINE_PROVIDER_ID",
            "creative_review_provider_id": "CREATIVE_REVIEW_PROVIDER_ID",
            "dream_diary_provider_id": "DREAM_DIARY_PROVIDER_ID",
            "dream_provider_id": "DREAM_DIARY_PROVIDER_ID",
            "diary_provider_id": "DREAM_DIARY_PROVIDER_ID",
            "photo_prompt_provider_id": "PHOTO_PROMPT_PROVIDER_ID",
            "embedding_provider_id": "EMBEDDING_PROVIDER_ID",
            "group_member_safety_provider_id": "GROUP_MEMBER_SAFETY_PROVIDER_ID",
            "reaction_expression_embedding_provider_id": "REACTION_EXPRESSION_EMBEDDING_PROVIDER_ID",
            "deepseek_peak_replacement_provider_id": "DEEPSEEK_PEAK_REPLACEMENT_PROVIDER_ID",
            "sensitive_replacement_provider_id": "SENSITIVE_REPLACEMENT_PROVIDER_ID",
        }

        if str(getattr(self, "provider_config_mode", "quick") or "quick").strip().lower() != "quick":
            for attr, config_key in attr_config_keys.items():
                setattr(self, attr, self._cfg_str(config, config_key, ""))
            self.plugin_vision_provider_id = configured_provider("PLUGIN_VISION_PROVIDER_ID", plugin_vision)
            return

        def fill(attr: str, provider_id: str) -> None:
            setattr(self, attr, provider_id)

        fill("llm_provider_id", complex_model)
        fill("mai_style_provider_id", fast or complex_model)

        for attr in (
            "daily_plan_provider_id",
            "detail_enhancement_provider_id",
            "history_summary_provider_id",
            "relationship_analysis_provider_id",
            "companion_memory_provider_id",
            "dialogue_episode_provider_id",
            "group_episode_provider_id",
            "forward_message_provider_id",
            "proactive_persona_judge_provider_id",
            "troubleshooting_provider_id",
            "daily_review_provider_id",
        ):
            fill(attr, complex_model)

        for attr in (
            "response_review_provider_id",
            "smart_silence_provider_id",
            "emotion_judgement_provider_id",
            "smart_message_debounce_provider_id",
            "rest_wakeup_provider_id",
            "group_interject_provider_id",
            "group_slang_provider_id",
            "voice_prompt_provider_id",
            "tts_conversion_provider_id",
            "narration_provider_id",
            "news_provider_id",
            "web_exploration_provider_id",
        ):
            fill(attr, fast or complex_model)
        fill("group_followup_judge_provider_id", fast)

        for attr in (
            "creative_provider_id",
            "creative_outline_provider_id",
            "creative_review_provider_id",
            "dream_diary_provider_id",
            "dream_provider_id",
            "diary_provider_id",
            "photo_prompt_provider_id",
        ):
            fill(attr, creative or complex_model)
        self.plugin_vision_provider_id = configured_provider("PLUGIN_VISION_PROVIDER_ID", plugin_vision)

    def _detect_astrbot_version(self) -> str:
        candidates: list[Any] = []
        for obj in (
            getattr(self, "context", None),
            getattr(getattr(self, "context", None), "core_lifecycle", None),
            getattr(getattr(self, "context", None), "metadata", None),
        ):
            if obj is None:
                continue
            for attr in ("version", "astrbot_version", "__version__", "VERSION"):
                try:
                    candidates.append(getattr(obj, attr, ""))
                except Exception:
                    pass
        for module_name in ("astrbot", "astrbot.core", "astrbot.api"):
            try:
                module = importlib.import_module(module_name)
            except Exception:
                continue
            for attr in ("__version__", "VERSION", "version"):
                try:
                    candidates.append(getattr(module, attr, ""))
                except Exception:
                    pass
        for candidate in candidates:
            text = _single_line(candidate, 40)
            if re.search(r"\d+\.\d+(?:\.\d+)?", text):
                return text
        return ""

    @staticmethod
    def _parse_version_tuple(value: Any) -> tuple[int, int, int] | None:
        match = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", str(value or ""))
        if not match:
            return None
        return (
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3) or 0),
        )

    def _materialize_conversation_system_block(
        self,
        req: ProviderRequest,
        *,
        section: PromptSection,
        marker: str = "",
        priority: int = 50,
        placement: str = PLACEMENT_DYNAMIC_SYSTEM,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Materialize one authored system section without changing its wire shape."""
        if not isinstance(section, PromptSection):
            raise TypeError("conversation system block requires PromptSection")
        plan = get_conversation_injection_plan(req)
        if plan is None:
            raise RuntimeError("conversation injection plan is unavailable")
        return plan.materialize_system_block(
            req,
            section=section,
            marker=marker,
            priority=priority,
            placement=placement,
            metadata=metadata,
        )

    @staticmethod
    def _request_context_role(item: Any) -> str:
        if isinstance(item, dict):
            return str(item.get("role") or "").strip().lower()
        return str(getattr(item, "role", "") or "").strip().lower()

    @staticmethod
    def _request_context_tool_calls(item: Any) -> list[Any]:
        raw = item.get("tool_calls") if isinstance(item, dict) else getattr(item, "tool_calls", None)
        return list(raw) if isinstance(raw, (list, tuple)) else []

    @staticmethod
    def _request_context_tool_call_id(item: Any) -> str:
        value = item.get("tool_call_id") if isinstance(item, dict) else getattr(item, "tool_call_id", None)
        return str(value or "").strip()

    @staticmethod
    def _request_context_declared_tool_call_id(item: Any) -> str:
        value = item.get("id") if isinstance(item, dict) else getattr(item, "id", None)
        return str(value or "").strip()

    def _repair_incomplete_tool_context_groups(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
        """Drop broken tool-call groups atomically before strict providers see them."""
        contexts = getattr(req, "contexts", None)
        if not isinstance(contexts, list) or not contexts:
            return

        repaired: list[Any] = []
        removed_groups = 0
        removed_messages = 0
        index = 0
        while index < len(contexts):
            item = contexts[index]
            role = self._request_context_role(item)
            declared_calls = self._request_context_tool_calls(item) if role == "assistant" else []
            if declared_calls:
                declared_ids = [
                    self._request_context_declared_tool_call_id(call)
                    for call in declared_calls
                ]
                next_index = index + 1
                tool_messages: list[Any] = []
                while (
                    next_index < len(contexts)
                    and self._request_context_role(contexts[next_index]) == "tool"
                ):
                    tool_messages.append(contexts[next_index])
                    next_index += 1

                expected_ids = set(declared_ids)
                result_ids = {
                    self._request_context_tool_call_id(tool_message)
                    for tool_message in tool_messages
                    if self._request_context_tool_call_id(tool_message)
                }
                complete = (
                    bool(expected_ids)
                    and len(expected_ids) == len(declared_ids)
                    and expected_ids.issubset(result_ids)
                )
                if complete:
                    repaired.append(item)
                    kept_ids: set[str] = set()
                    for tool_message in tool_messages:
                        tool_call_id = self._request_context_tool_call_id(tool_message)
                        if tool_call_id in expected_ids and tool_call_id not in kept_ids:
                            repaired.append(tool_message)
                            kept_ids.add(tool_call_id)
                        else:
                            removed_messages += 1
                else:
                    removed_groups += 1
                    removed_messages += 1 + len(tool_messages)
                index = next_index
                continue

            if role == "tool":
                removed_messages += 1
            else:
                repaired.append(item)
            index += 1

        if removed_messages <= 0:
            return
        try:
            req.contexts = repaired
        except Exception:
            return
        logger.warning(
            "已修复不完整工具调用历史: session=%s groups=%s messages=%s contexts=%s->%s",
            _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            removed_groups,
            removed_messages,
            len(contexts),
            len(repaired),
        )

    async def _collect_prompt_contexts_parallel(
        self,
        specs: list[dict[str, Any]],
    ) -> list[CollectedPromptContext]:
        tasks = [self._resolve_prompt_context_collector(spec) for spec in specs if isinstance(spec, dict)]
        if not tasks:
            return []
        results = await asyncio.gather(*tasks, return_exceptions=True)
        collected: list[CollectedPromptContext] = []
        for result in results:
            if isinstance(result, CollectedPromptContext):
                collected.append(result)
            elif isinstance(result, Exception):
                logger.debug("请求上下文并行收集出现未捕获异常: %s", _single_line(result, 120))
        return collected

    def _add_collected_prompt_contexts(
        self,
        prompt_surface: PromptSurface,
        collected: list[CollectedPromptContext],
    ) -> None:
        for item in collected:
            for index, section in enumerate(item.sections):
                if not render_prompt_sections(
                    [section],
                    mode=PromptRenderMode.BODY_ONLY,
                ).strip():
                    continue
                prompt_surface.add(section, priority=item.priority + index)

    def _expression_profile_prompt_metadata(
        self,
        user: dict[str, Any],
        rule_details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        profile = user.get("expression_profile") if isinstance(user.get("expression_profile"), dict) else {}
        samples = profile.get("samples") if isinstance(profile.get("samples"), list) else []
        pending = profile.get("pending_samples") if isinstance(profile.get("pending_samples"), list) else []
        scene_profiles = profile.get("scene_profiles") if isinstance(profile.get("scene_profiles"), dict) else {}
        stable_scene_count = sum(
            1
            for item in scene_profiles.values()
            if isinstance(item, dict) and _safe_int(item.get("count"), 0, 0) >= 2
        )
        return {
            "来源": "表达学习样本",
            "置信度": min(1.0, round(len(samples) / 8, 2)) if samples else 0,
            "样本数": len(samples),
            "待审核": len(pending),
            "已学场景": stable_scene_count,
            "本轮命中": _single_line((rule_details or {}).get("label"), 32) or "无稳定规则",
            "规则证据": _safe_int((rule_details or {}).get("evidence_count"), 0, 0),
            "启用": bool(runtime_persona_setting(self, "enable_expression_learning", False)),
            "模式": _single_line(runtime_persona_setting(self, "expression_learning_mode", "balanced"), 20),
        }

    async def _collect_private_passive_prompt_contexts(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        *,
        inbound_text: str,
        current_user: dict[str, Any],
        is_private_chat: bool,
    ) -> list[CollectedPromptContext]:
        specs: list[dict[str, Any]] = []

        def add_spec(
            key: str,
            source: str,
            priority: int,
            func: Any,
            *,
            timeout: float = 0.8,
            metadata: dict[str, Any] | None = None,
        ) -> None:
            specs.append(
                {
                    "key": key,
                    "source": source,
                    "priority": priority,
                    "func": func,
                    "timeout": timeout,
                    "metadata": metadata or {},
                }
            )

        current_user_id = ""
        if is_private_chat:
            try:
                current_user_id = _single_line(current_user.get("user_id") or event.get_sender_id(), 80)
            except Exception:
                current_user_id = _single_line(current_user.get("user_id"), 80)
        prompt_user = current_user
        current_umo = _single_line(getattr(event, "unified_msg_origin", ""), 220)
        if current_umo:
            prompt_user = dict(current_user)
            prompt_user["_game_current_umo"] = current_umo

        third_party_activity_question = self._user_activity_question_targets_someone_else(inbound_text)
        current_state_memory_needed = not third_party_activity_question and bool(
            self._user_asks_bot_current_state_or_activity(inbound_text)
            or re.search(
                r"(你|星缘|bot|机器人).{0,8}(在干嘛|在做什么|做什么|穿什么|穿的?什么|衣服|衣服颜色|什么颜色|吃了什么|吃的?什么|几点吃|什么时候吃|吃饭|进食|在哪里|在哪儿|当前位置|今天状态|现在状态)",
                inbound_text,
            )
            or re.search(
                r"(穿搭|自拍|衣服.{0,8}(颜色|什么色)|穿.{0,6}什么|今天.*衣服|今天.*颜色|刚才.*做|几点.*做了什么)",
                inbound_text,
            )
        )

        async def current_state_memory_context() -> PromptSection | None:
            composer = getattr(self, "_memory_companion_compose_feature_context", None)
            if not callable(composer):
                return None
            current_state_memory = await composer(
                kind="current_state_reply",
                query=(
                    f"当前状态问答：{inbound_text}；"
                    "今日穿搭、衣服颜色、当前日程、当前位置、刚才做了什么、进食时间、吃了什么、最近自拍、用户常问状态习惯"
                ),
                user=current_user,
                user_id=current_user_id,
                event=event,
                top_k=6,
                max_chars=950,
                timeout_seconds=1.6,
            )
            current_state_memory = str(current_state_memory or "").strip()
            if not current_state_memory:
                return None
            return prompt_section(
                key="memory.current_state",
                title="我会牢牢记住你 当前状态参考",
                source="memory_companion",
                content=(
                    f"{current_state_memory}\n"
                    "使用方式：只把它当作回答当前状态、穿搭、吃饭、日程连续性的辅助证据；"
                    "优先服从本轮状态注入和当前会话中明确发生的时间线。尤其是近期明确换装、换地点或动作变化，"
                    "高于每日穿搭、旧日程和旧记忆，不得被它们覆盖。不要说“我查到/记忆里”。"
                ),
                metadata={"范围": "当前私聊会话", "触发": "当前状态问答"},
            )

        if is_private_chat and current_state_memory_needed:
            add_spec(
                "memory.current_state",
                "memory_companion",
                54,
                current_state_memory_context,
                timeout=1.65,
                metadata={"范围": "当前私聊会话", "触发": "当前状态问答"},
            )

        add_spec(
            "creative.hidden",
            "creative",
            60,
            lambda: self._format_hidden_creative_context_for_reply_prompt_section(
                inbound_text,
                current_user,
            ),
        )
        add_spec(
            "photo.recent_share",
            "photo",
            61,
            lambda: self._format_recent_photo_share_snapshot_for_reply_prompt_section(
                current_user,
                inbound_text,
            ),
        )
        add_spec(
            "bookshelf.secret",
            "bookshelf",
            61,
            lambda: self._format_bookshelf_secret_prompt_section(inbound_text, current_user),
            timeout=1.2,
        )
        add_spec(
            "news.recent",
            "news",
            64,
            lambda: self._format_recent_news_context_prompt_section(inbound_text),
        )
        add_spec(
            "web_exploration.recent",
            "web_exploration",
            65,
            lambda: self._format_recent_web_exploration_context_prompt_section(inbound_text),
        )
        if is_private_chat:
            add_spec(
                "reality_touch.continuity",
                "reality_touch",
                56,
                lambda: self._format_reality_touch_continuity_context_prompt_section(
                    current_user
                ),
            )
            add_spec(
                "reality_touch.mobile_location",
                "reality_touch",
                55,
                lambda: self._format_mobile_user_location_context_prompt_section(
                    current_user
                ),
                metadata={"范围": "当前私聊会话", "来源": "用户主动授权的手机前台定位"},
            )
        if self._feature_enabled_or_temp_unlocked("enable_skill_growth_passive_injection"):
            add_spec("skill.growth", "skill", 66, self._format_skill_growth_prompt_section)
        else:
            add_spec(
                "skill.growth.match",
                "skill",
                66,
                lambda: self._format_skill_growth_for_user_text_prompt_section(inbound_text),
            )
        if not self._memory_companion_should_defer_prompt_section("self_timeline", event, req):
            add_spec(
                "self.timeline",
                "self_timeline",
                67,
                lambda: self._format_self_timeline_context_for_reply_section(
                    inbound_text,
                    current_user,
                    limit=8,
                ),
            )
        if is_private_chat:
            add_spec(
                "relationship.owner_exclusive",
                "relationship",
                18,
                lambda: self._format_owner_exclusive_relationship_prompt_section(
                    current_user,
                    stable_user_id=current_user_id,
                    channel_scope="private",
                ),
                metadata={"范围": "当前人格与精确私聊用户", "模式": "owner_exclusive"},
            )
        private_context_deferred = self._memory_companion_should_defer_prompt_section("private_context", event, req)
        if not private_context_deferred:
            add_spec(
                "private.context",
                "companion",
                70,
                lambda: self._format_private_chat_context_prompt_section(current_user),
            )
        if is_private_chat and not private_context_deferred:
            add_spec(
                "memory.private_recall",
                "memory_companion",
                73,
                lambda: self._memory_companion_compose_private_recall(
                    event=event,
                    user=current_user,
                    user_id=current_user_id,
                    text=inbound_text,
                ),
                timeout=min(1.4, max(0.3, _safe_float(getattr(self, "memory_companion_context_timeout_seconds", 1.2), 1.2, 0.2))),
                metadata={"范围": "当前私聊会话", "触发": "记忆线索"},
            )
        add_spec(
            "companion.planner",
            "companion",
            80,
            lambda: self._format_companion_planner_prompt_section(prompt_user),
        )
        if not self._memory_companion_should_defer_prompt_section("livingmemory_guidance", event, req):
            add_spec("livingmemory.guidance", "livingmemory", 90, lambda: self._format_livingmemory_guidance_sections(scope="private" if is_private_chat else "group"))
        add_spec("detail.injection", "daily_detail", 40, self._format_detail_injection_prompt_section)

        if is_private_chat:
            expression_user_id = self._expression_private_scope_id(current_user_id)
            expression_voice_selection = self._expression_voice_selection(
                scope="private",
                target_id=expression_user_id,
                inbound_text=inbound_text,
                context_owner=current_user,
            )
            expression_voice_section = expression_voice_selection.get("section")
            semantic_expression_rules = expression_voice_selection.get("rules")
            if isinstance(semantic_expression_rules, list) and semantic_expression_rules:
                try:
                    setattr(event, "private_companion_semantic_expression_rules", semantic_expression_rules)
                    setattr(
                        event,
                        "private_companion_semantic_expression_context",
                        dict(expression_voice_selection.get("context") or {}),
                    )
                except Exception:
                    pass
            if isinstance(expression_voice_section, PromptSection):
                add_spec(
                    "expression.voice",
                    "expression",
                    68,
                    lambda: expression_voice_section,
                    metadata={"范围": "全局抽象表达底色", "目标": expression_user_id},
                )

        async def timer_context() -> PromptSection | None:
            if not (self.enable_llm_timer_scheduling and is_private_chat):
                return None
            try:
                target_user_id = str(event.get_sender_id())
            except Exception:
                target_user_id = ""
            resolver = getattr(self, "_private_user_id_for_event", None)
            if callable(resolver) and target_user_id:
                target_user_id = resolver(event, target_user_id)
            if not target_user_id:
                return None
            async with self._data_lock:
                timer_user = dict(self._get_user(target_user_id))
                enabled = bool(timer_user.get("enabled"))
            return self._format_timer_scheduling_prompt_section(timer_user) if enabled else None

        add_spec("timer.scheduling", "timer", 95, timer_context, timeout=0.5)
        return await self._collect_prompt_contexts_parallel(specs)

    @filter.on_agent_begin()
    @_multi_persona_event_context
    async def enforce_memo_reminder_tool_boundary(self, event: AstrMessageEvent, run_context: Any, *args, **kwargs):
        """AstrBot 会在请求钩子之后补内置工具，因此在 Agent 启动时做最终互斥。"""
        if self is None or event is None:
            return
        await self._acknowledge_official_llm_timer_trigger(event)
        self._finalize_passive_reply_tool_boundary(event)
        if self._finalize_memo_request_tool_boundary(event):
            logger.info(
                "明确便签请求已从最终工具集移除 future_task,避免重复提醒: session=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            )

    @filter.on_llm_tool_respond()
    @_multi_persona_event_context
    async def capture_future_task_result(
        self,
        event: AstrMessageEvent,
        tool: Any,
        tool_args: dict[str, Any] | None,
        tool_result: Any,
        *args,
        **kwargs,
    ):
        """记录官方定时与创作读取工具的真实结果，供响应阶段可靠校验。"""
        if self is None or event is None:
            return
        await self._record_official_llm_timer_tool_result(event, tool, tool_result)
        if self._record_future_task_result(event, tool, tool_args, tool_result):
            logger.info(
                "已记录本轮 future_task 成功: action=%s session=%s",
                _single_line((tool_args or {}).get("action"), 20) or "unknown",
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            )
        if self._record_creative_work_tool_result(event, tool, tool_args, tool_result):
            logger.info(
                "已记录本轮创作读取工具结果: action=%s status=%s inventory_complete=%s session=%s",
                _single_line((tool_args or {}).get("action") if isinstance(tool_args, dict) else "", 20) or "get",
                _single_line(getattr(event, "private_companion_creative_work_tool_status", ""), 24) or "unknown",
                bool(getattr(event, "private_companion_bookshelf_inventory_complete", False)),
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            )

    @filter.on_agent_done()
    @_multi_persona_event_context
    async def complete_official_llm_timer_lifecycle(
        self,
        event: AstrMessageEvent,
        run_context: Any,
        response: Any,
        *args,
        **kwargs,
    ):
        """Only finalize timer state when the cron event matches this plugin's timer id/job id."""
        if self is None or event is None:
            return
        await self._complete_official_llm_timer_event(event)

    def _is_lightweight_private_passive_inbound(self, text: str) -> bool:
        cleaned = _single_line(text, 80)
        if not cleaned:
            return False
        if len(cleaned) > 18:
            return False
        weather_query_detector = getattr(self, "_user_asks_current_weather", None)
        if callable(weather_query_detector) and weather_query_detector(cleaned):
            return False
        current_activity_detector = getattr(
            self,
            "_user_asks_bot_current_state_or_activity",
            None,
        )
        if callable(current_activity_detector) and current_activity_detector(cleaned):
            return False
        outfit_change_detector = getattr(self, "_detect_dialogue_outfit_change", None)
        if callable(outfit_change_detector):
            try:
                if outfit_change_detector(cleaned):
                    return False
            except Exception:
                pass
        heavy_tokens = (
            "图片", "看图", "照片", "语音", "引用", "转发", "聊天记录",
            "帮我", "怎么", "为什么", "是什么", "怎么办", "分析", "解释", "总结",
            "日程", "状态", "近况", "在干嘛", "干什么", "做什么", "忙什么",
            "资料柜", "夹层", "抽屉", "阅读", "读过", "看过", "素材", "资料", "漫画", "藏本",
            "创作", "作品", "写作", "写书", "写过书", "小说", "随笔", "散文", "剧本", "手稿", "草稿", "出版",
            "新闻", "说说", "空间", "发给", "转告", "@",
        )
        if any(token in cleaned for token in heavy_tokens):
            return False
        bookshelf_checker = getattr(self, "_user_asks_bookshelf_reading_memory", None)
        if callable(bookshelf_checker) and bookshelf_checker(cleaned):
            return False
        creative_checker = getattr(self, "_user_asks_recent_creative_activity", None)
        if callable(creative_checker) and creative_checker(cleaned):
            return False
        return True

    @staticmethod
    def _is_private_routine_check_invocation(text: str) -> bool:
        cleaned = _single_line(text, 80)
        if not cleaned or len(cleaned) > 28:
            return False
        compact = re.sub(r"[\s，。！？!?,.、~～…]+", "", cleaned)
        markers = ("例行检查", "日常检查", "每日检查", "晚间检查", "夜间检查")
        prefixes = (
            "开始", "来", "继续", "进行", "该",
            "那", "那么", "那就", "嗯", "嗯那", "嗯那就", "好", "好吧", "好那就",
        )
        suffixes = ("啦", "咯", "了", "开始", "时间", "时间到", "一下")
        variants = set(markers)
        for marker in markers:
            variants.update(f"{prefix}{marker}" for prefix in prefixes)
            variants.update(f"{marker}{suffix}" for suffix in suffixes)
            variants.update(f"{prefix}{marker}{suffix}" for prefix in prefixes for suffix in suffixes)
        return compact in variants

    def _format_private_routine_check_boundary(
        self,
        text: str,
    ) -> str:
        section = self._format_private_routine_check_boundary_section(text)
        return render_prompt_sections(
            [section],
            mode=PromptRenderMode.LABELED_BLOCK,
        )

    def _format_private_routine_check_boundary_section(
        self,
        text: str,
    ) -> PromptSection:
        body = ""
        if self._is_private_routine_check_invocation(text):
            body = (
                "用户正在发起一次例行检查，但这不等于要求你自动展开固定健康清单。\n"
                "优先承接当前原始对话或可靠记忆中已经明确的双方约定；整次回复最多两个短句、最多提出一个问题。\n"
                "开头若有语气词和称呼，要和后面的承接正文自然写在同一句里，不要把“嗯，某某”“唔，某某大人”单独拆成一条消息。\n"
                "只询问当前消息、最近原始对话、明确提醒/便签或可靠记忆实际支持的项目。没有依据时，不要假定用户正在服药、生病、没吃饭或遗漏了某项现实任务。\n"
                "如果没有明确检查项目，就自然问今天想先检查哪一项；不要一口气连续追问晚饭、吃药和睡觉。"
            )
        return prompt_section(
            key="turn.routine_check_boundary",
            title="轻量例行检查边界",
            source="conversation",
            content=body,
        )

    def _limit_private_routine_check_segments(self, text: str, chunks: list[list[Any]]) -> list[list[Any]]:
        if not self._is_private_routine_check_invocation(text):
            return chunks
        limited = list(chunks or [])
        if len(limited) >= 2 and all(
            part and all(isinstance(component, Plain) for component in part)
            for part in limited[:2]
        ):
            lead = "".join(str(getattr(component, "text", "") or "") for component in limited[0]).strip()
            following = "".join(str(getattr(component, "text", "") or "") for component in limited[1]).strip()
            match = re.fullmatch(
                r"(唔|嗯|哦|啊|诶|欸|哎|唉)([\s，,、…~～]+)([\u4e00-\u9fffA-Za-z0-9·]{1,10})[\s，。！？!?,.、…~～]*",
                lead,
            )
            address = match.group(3) if match else ""
            address_titles = ("大人", "主人", "老师", "先生", "小姐", "同学", "哥哥", "姐姐", "前辈", "殿下")
            non_address_phrases = ("知道", "明白", "收到", "可以", "没事", "不用", "不要", "好了", "好吧")
            looks_like_address = bool(
                address
                and (
                    address.endswith(address_titles)
                    or (len(address) <= 4 and not any(token in address for token in non_address_phrases))
                )
            )
            if looks_like_address and following:
                separator = "" if re.search(r"[，,。！？!?、…~～]$", lead) else "，"
                limited = [[Plain(f"{lead}{separator}{following}")], *limited[2:]]
        if len(limited) <= 2:
            return limited
        return [limited[0], flatten_component_chunks(limited[1:])]

    def _private_passive_schedule_material(
        self,
        current_user: dict[str, Any] | None = None,
    ) -> tuple[str, str]:
        """Return evidence-backed and clock-only schedule material separately."""

        plan = self.data.get("daily_plan", {})
        if not isinstance(plan, dict):
            return "", ""

        def format_item(item: Any, *, clock_projection: bool = False) -> str:
            if not isinstance(item, dict):
                return ""
            if clock_projection:
                start = _single_line(item.get("time"), 12)
                end = _single_line(item.get("end"), 12)
                window = f"{start}-{end}" if start and end else start
                activity = _single_line(item.get("activity") or item.get("title"), 120)
                mood = _single_line(item.get("mood"), 32)
                text = "｜".join(
                    part
                    for part in (
                        window,
                        activity,
                        f"情绪：{mood}" if mood else "",
                    )
                    if part
                )
            else:
                text = self._format_plan_item_for_prompt(item)
            return self._sanitize_schedule_context_for_private_user(
                text,
                current_user or {},
            )

        current_item = self._get_current_plan_item(plan)
        verified_schedule = format_item(current_item)
        clock_item = None
        clock_getter = getattr(self, "_get_clock_plan_item_for_display", None)
        if callable(clock_getter):
            try:
                clock_item = clock_getter(plan)
            except Exception:
                clock_item = None
        if isinstance(clock_item, dict):
            lifecycle = self._normalize_schedule_lifecycle_status(
                clock_item.get("lifecycle_status") or clock_item.get("status")
            )
            if lifecycle not in {"", "planned", "active"}:
                clock_item = None
        return verified_schedule, format_item(clock_item, clock_projection=True)

    def _private_passive_state_fingerprint(self, state: dict[str, Any], current_user: dict[str, Any] | None = None) -> dict[str, Any]:
        now = self._environment_now()
        time_label, _ = self._current_time_period_label(now)
        energy = _safe_int(state.get("energy"), 70, 0, 100)
        verified_schedule, planned_schedule = self._private_passive_schedule_material(current_user)
        detail = self._current_detail_segment_for_update()
        detail_key = _single_line(detail.get("key"), 80) if isinstance(detail, dict) else ""
        detail_snapshot_getter = getattr(self, "_current_detail_snapshot_for_update", None)
        detail_snapshot = detail_snapshot_getter() if callable(detail_snapshot_getter) else None
        detail_summary = _single_line(detail_snapshot.get("summary"), 80) if isinstance(detail_snapshot, dict) else ""
        if detail_summary:
            detail_summary = self._sanitize_schedule_context_for_private_user(
                detail_summary,
                current_user or {},
            )
        friend_user = self._private_user_role(current_user or {}) == "friend"
        weather = "" if friend_user else _single_line(state.get("weather"), 60)
        conditions: list[str] = []
        raw_conditions = state.get("conditions")
        if isinstance(raw_conditions, list):
            for cond in raw_conditions[:3]:
                if not isinstance(cond, dict) or not self._should_show_condition(cond):
                    continue
                label = _single_line(cond.get("label") or cond.get("title") or cond.get("kind"), 18)
                if label and label not in conditions:
                    conditions.append(label)
        cycle_profile = self._active_body_cycle_profile(state)
        return {
            "date": _today_key(),
            "time_label": time_label,
            "energy_bracket": (energy // 10) * 10,
            "mood": _single_line(state.get("mood_bias"), 18) or "平稳",
            "activity": _single_line(verified_schedule, 100),
            "planned_activity": _single_line(planned_schedule, 100),
            "detail": f"{detail_key}|{detail_summary}" if detail_summary else detail_key,
            "weather": weather if weather and weather != "暂无天气信息" else "",
            "conditions": conditions[:2],
            "body_cycle": _single_line(state.get("body_cycle"), 120) if cycle_profile else "",
            "body_cycle_phase": _single_line(cycle_profile.get("phase"), 24),
        }

    def _format_private_passive_state_snapshot(
        self,
        state: dict[str, Any],
        current_user: dict[str, Any] | None,
        *,
        direct: bool = False,
    ) -> str:
        section = self._format_private_passive_state_snapshot_section(
            state,
            current_user,
            direct=direct,
        )
        return render_prompt_sections(
            [section],
            mode=PromptRenderMode.LABELED_BLOCK,
        )

    def _format_private_passive_state_snapshot_section(
        self,
        state: dict[str, Any],
        current_user: dict[str, Any] | None,
        *,
        direct: bool = False,
    ) -> PromptSection:
        energy = _safe_int(state.get("energy"), 70, 0, 100)
        mood = _single_line(state.get("mood_bias"), 18) or "平稳"
        now = self._environment_now()
        time_label, _ = self._current_time_period_label(now)
        pieces = [f"时间节奏：{time_label}", f"精神约 {energy}/100", f"情绪底色偏{mood}"]
        realtime_formatter = getattr(self, "_format_external_realtime_prompt_section", None)
        realtime_section = realtime_formatter(current_user, public=False) if callable(realtime_formatter) else None
        realtime_context = (
            render_prompt_sections(
                [realtime_section],
                mode=PromptRenderMode.BODY_ONLY,
            )
            if isinstance(realtime_section, PromptSection)
            else ""
        )
        verified_schedule, planned_schedule = self._private_passive_schedule_material(current_user)
        if verified_schedule and not realtime_context:
            pieces.append(f"拟人化日程素材：{verified_schedule}")
        elif verified_schedule:
            pieces.append(f"原定日程素材（已被实时共同活动覆盖）：{verified_schedule}")
        elif planned_schedule and not realtime_context:
            pieces.append(f"当前计划时段（未确认执行）：{planned_schedule}")
        elif planned_schedule:
            pieces.append(f"原定计划时段（未确认执行，已被实时共同活动覆盖）：{planned_schedule}")
        detail_snapshot_getter = getattr(self, "_current_detail_snapshot_for_update", None)
        detail_snapshot = detail_snapshot_getter() if callable(detail_snapshot_getter) else None
        if isinstance(detail_snapshot, dict):
            summary = _single_line(detail_snapshot.get("summary"), 90)
            if summary:
                summary = self._sanitize_schedule_context_for_private_user(
                    summary,
                    current_user or {},
                )
            if summary and not realtime_context:
                pieces.append(f"模拟氛围（计划细化，未确认执行）：{summary}")
            elif summary:
                pieces.append(
                    f"原定模拟氛围（计划细化，未确认执行，已被实时共同活动覆盖）：{summary}"
                )
        weather = _single_line(state.get("weather"), 60)
        if self._private_user_role(current_user or {}) == "friend":
            weather = ""
        if weather and weather != "暂无天气信息":
            pieces.append(f"天气素材：{weather}")
        conditions: list[str] = []
        raw_conditions = state.get("conditions")
        if isinstance(raw_conditions, list):
            for cond in raw_conditions[:3]:
                if not isinstance(cond, dict) or not self._should_show_condition(cond):
                    continue
                label = _single_line(cond.get("label") or cond.get("title") or cond.get("kind"), 18)
                if label and label not in conditions:
                    conditions.append(label)
        if conditions:
            pieces.append("身体感素材：" + "、".join(conditions[:2]))
        cycle_profile = self._active_body_cycle_profile(state)
        if cycle_profile:
            pieces.append(f"周期状态：Bot 当前{cycle_profile['stage']}")
        usage = (
            "本轮状态素材触发原因：用户直接询问 Bot 近况或状态。"
            if direct
            else "本轮状态素材触发原因：Bot 自身模拟状态发生变化。"
        )
        guidance = (
            "用户正在直接问 Bot 此刻在做什么或当前状态：先回答实时共同活动（若有），它高于固定日程、旧对话、旧记忆和临场发挥。"
            "固定日程只是原计划，若与实时共同活动冲突，必须说原计划被打断/覆盖，禁止继续声称仍在旧地点或旧动作中。"
            "若没有实时共同活动且有拟人化日程素材，先正面回答拟人化日程素材中的当前活动。"
            "若只有‘当前计划时段（未确认执行）’，必须用‘按计划/原本安排’口径回答，不得声称已经在执行。"
            "不得另编素材未提供的动作、地点、饮食或娱乐活动。"
            "如果素材本身较笼统，就按原有粒度自然转述，例如只说正在专心处理手头的事；不要为了显得具体而补造细节。"
            if direct
            else "只用于语气、长短、节奏和轻微接话；不要把它改写成用户做过的事或现实已经发生的事件。"
        )
        blocks = [
            "以下只描述 Bot 的拟人化内部状态/场景素材，不是用户事实、不是现实证据，也不要写入长期记忆。",
        ]
        if realtime_context:
            blocks.append(realtime_context)
        blocks.extend([
            guidance,
            usage + " " + "；".join(pieces) + "。",
        ])
        return prompt_section(
            key="state.session_update",
            title="Bot 自身模拟状态更新",
            source="daily_state",
            content="\n".join(blocks),
        )

    def _format_external_realtime_context_for_prompt(
        self,
        current_user: dict[str, Any] | None = None,
        *,
        public: bool = False,
    ) -> str:
        section = self._format_external_realtime_prompt_section(
            current_user,
            public=public,
        )
        return render_prompt_sections(
            [section],
            mode=PromptRenderMode.LABELED_BLOCK,
        )

    def _format_external_realtime_context_body(
        self,
        current_user: dict[str, Any] | None = None,
        *,
        public: bool = False,
    ) -> str:
        """Format extension state for ordinary private/group prompts.

        Active shared activity is authoritative over the schedule. Continuity is
        deliberately bounded and public views never include call transcript.
        """
        now = _now_ts()
        user = current_user if isinstance(current_user, dict) else {}
        user_id = _single_line(user.get("user_id"), 80)
        role = _single_line(user.get("relationship_role"), 24)
        role_getter = getattr(self, "_private_user_role", None)
        if callable(role_getter):
            try:
                role = _single_line(role_getter(user, user_id), 24)
            except TypeError:
                role = _single_line(role_getter(user), 24)
            except Exception:
                pass
        activity: dict[str, Any] = {}
        registry = getattr(self, "_external_realtime_activities", None)
        if isinstance(registry, dict):
            for key, item in list(registry.items()):
                if not isinstance(item, dict) or _safe_float(item.get("expires_at"), 0.0) <= now:
                    registry.pop(key, None)
                    continue
                item_user = _single_line(item.get("user_id"), 80)
                if item_user and item_user == user_id:
                    activity = dict(item)
                    break
                if not activity and (public or role != "owner"):
                    activity = dict(item)
        continuity: dict[str, Any] = {}
        continuity_registry = getattr(self, "_external_realtime_continuity", None)
        if isinstance(continuity_registry, dict) and user_id:
            item = continuity_registry.get(user_id)
            if isinstance(item, dict) and _safe_float(item.get("expires_at"), 0.0) > now:
                continuity = dict(item)
        if not activity and not continuity:
            return ""
        label = _single_line(activity.get("label"), 140) or {
            "shared_call": "正在和主要用户通话",
            "shared_watch": "正在和主要用户一起看视频",
            "shared_work": "正在和主要用户进行工作协同",
        }.get(_single_line(activity.get("kind"), 40), "正在进行共同活动")
        if public:
            return (
                f"{label}。这是当前优先级最高的实时事实，固定日程只是原计划。"
                "群聊只可概括说明正在与主要用户共同活动，不得透露电话内容、具体约定、逐字转写或私密地点。"
            )
        lines = [
            "实时共同活动是正在发生的高优先级事实；固定日程只是原计划，冲突时必须以实时活动为准。",
        ]
        if activity:
            lines.append(f"当前活动：{label}")
        summary = _single_line(continuity.get("summary"), 1800)
        if summary:
            lines.append(
                "最近通话/共同活动的短期连续性（带说话人归属，仅作自然接续，不是长期记忆）："
                + summary
            )
        return "\n".join(lines)

    def _private_passive_state_reply_policy_prompt(self) -> str:
        section = self._private_passive_state_reply_policy_section()
        return render_prompt_sections(
            [section],
            mode=PromptRenderMode.LABELED_BLOCK,
        )

    def _private_passive_state_reply_policy_section(
        self,
        *,
        compact: bool = False,
    ) -> PromptSection:
        lines = (
            [
                "先自然回应用户当前表达；主动提供一处与 Bot 自身有关的具体细节；不要逐项汇报状态；不要把回复写成连续盘问；整次回复最多提出一个问题；没有必要时可以不提问。"
            ]
            if compact
            else [
                "先自然回应用户当前表达；主动提供一处与 Bot 自身有关的具体细节；不要逐项汇报状态，也不要把内部素材描述成已经证实的现实事件。",
                "不要把回复写成连续盘问；整次回复最多提出一个问题；没有必要时可以不提问。",
                "当前用户最后一条消息是本轮唯一的主线：先接住其中的具体词、问题或情绪，再决定是否补充背景。旧话题、未完成话头和状态素材只有在与当前内容有明确语义连接时才轻轻带过；不贴合就留在背景里，不要为了连续性硬拽回来。",
                "话题确实转向时，用当前消息里的连接点自然过渡，不要凭空写“刚刚/刚才/前面”作为转场。相对时间词只在用户明确提到时间、或有可靠事实表明确实发生在那个时间段时使用；内部提示中的时间标签不得原样出现在回复里。",
            ]
        )
        return prompt_section(
            key="state.reply_policy",
            title="私聊被动回复策略",
            source="daily_state",
            content="\n".join(lines),
        )

    def _format_private_passive_state_continuity_anchor(
        self,
        state: dict[str, Any],
        current_user: dict[str, Any] | None,
    ) -> str:
        section = self._format_private_passive_state_continuity_anchor_section(
            state,
            current_user,
        )
        return render_prompt_sections(
            [section],
            mode=PromptRenderMode.LABELED_BLOCK,
        )

    def _format_private_passive_state_continuity_anchor_section(
        self,
        state: dict[str, Any],
        current_user: dict[str, Any] | None,
    ) -> PromptSection:
        now = self._environment_now()
        time_label, _ = self._current_time_period_label(now)
        pieces = [f"时段={time_label}"]

        raw_energy = state.get("energy") if isinstance(state, dict) else None
        if isinstance(raw_energy, (int, float)) and not isinstance(raw_energy, bool):
            energy = _safe_int(raw_energy, 70, 0, 100)
            energy_floor = min(90, (energy // 10) * 10)
            energy_ceiling = 100 if energy_floor == 90 else energy_floor + 9
            pieces.append(f"精力={energy_floor}-{energy_ceiling}/100")

        mood = (
            _single_line(state.get("mood_bias"), 18) if isinstance(state, dict) else ""
        )
        if mood:
            pieces.append(f"情绪底色={mood}")

        current_item = self._get_current_plan_item(self.data.get("daily_plan", {}))
        activity = ""
        scene_text = ""
        if isinstance(current_item, dict):
            scene_text = self._sanitize_schedule_model_artifacts(
                current_item.get("activity"), limit=72
            )
            future_marker = re.search(
                r"准备\s*(?:(?:先|再|马上|即将|随后|然后|接着|待会儿?|等会儿?|晚点|稍后)\s*)?"
                r"(?:去|到|回|前往|出发|开始|继续|做|处理|整理|收拾|上课|自习|洗漱|洗澡|睡觉|"
                r"出门|吃饭|用餐|跑步|散步|运动|锻炼|看书|读书|写作|买东西|买菜)|"
                r"正要|马上|即将|稍后|之后|随后|然后|接着|待会儿?|等会儿?|过(?:一)?会儿|一会儿后|"
                r"晚点|晚些时候|接下来|下一段|再(?:去|到|回|前往|开始|继续|做|处理|整理|收拾)|"
                r"(?:做|整理|收拾|写|看|读|处理)?完(?:后)?(?:再)?(?:去|到|回|前往)",
                scene_text,
            )
            if future_marker:
                scene_text = scene_text[: future_marker.start()].rstrip(" ，,；;。")
            if scene_text and self._daily_plan_clause_has_unsafe_social_fact(
                scene_text
            ):
                scene_text = ""
            if scene_text and re.search(
                r"用户|主要用户|当前用户|主人|对方|给你|和你|跟你|你在|你的|明天|后天|下周|未来|日程|计划|打算|将要",
                scene_text,
            ):
                scene_text = ""
            scene_text = self._sanitize_schedule_context_for_private_user(
                scene_text, current_user or {}
            )
            if scene_text and re.search(
                r"(?:^|[，,；;。])(?:准备|正要|要去|想去|去往|前往|出发|赶往|回到?)",
                scene_text,
            ):
                scene_text = ""
            action_match = re.search(
                r"(?:整理|收拾|看书|阅读|读书|写作|写字|写笔记|听歌|听音乐|休息|发呆|学习|"
                r"上课|自习|工作|处理|做饭|吃饭|用餐|洗漱|洗澡|睡觉|散步|运动|锻炼|画画|"
                r"练习|聊天|看电影|看视频|玩游戏|刷手机|喝咖啡|喝茶|做手工|晒太阳|通勤|买东西|买菜)"
                r"[^，,；;。]{0,52}",
                scene_text,
            )
            if action_match:
                activity = action_match.group(0).strip()
                if re.search(
                    r"(?:在|到|去|回|靠近|路过|位于|身处)[^，,；;。]{1,24}|"
                    r"[^，,；;。]{2,24}(?:省|市|区|县|镇|村|路|街|巷|号|小区|校区|商场|广场|"
                    r"大厦|园区|车站|机场|酒店|咖啡店|餐厅|公园|图书馆)",
                    activity,
                ):
                    activity = ""
        if activity:
            pieces.append(f"当前活动={_single_line(activity, 56)}")
        if scene_text:
            inferred_location = self._coarse_roleplay_location_text(
                self._infer_location_from_text(scene_text)
            )
            safe_location = self._sanitize_schedule_context_for_private_user(
                f"当前位置：{inferred_location}" if inferred_location else "",
                current_user or {},
            )
            if safe_location:
                pieces.append(f"粗略位置={inferred_location}")

        lines = [
            "这是 Bot 的拟人化模拟状态，不是用户事实、现实证据或长期记忆。",
            "当下素材（仅供隐性承接）：" + "；".join(pieces) + "。",
        ]
        return prompt_section(
            key="state.session_update",
            title="Bot 当下连续性",
            source="daily_state",
            content="\n".join(lines)[:300],
        )

    def _private_passive_state_update_for_prompt(
        self,
        *,
        session: str,
        state: dict[str, Any],
        current_user: dict[str, Any] | None,
        inbound_text: str,
        lightweight: bool,
    ) -> tuple[str, bool, str]:
        sections, state_changed, reason = self._private_passive_state_update_prompt_sections(
            session=session,
            state=state,
            current_user=current_user,
            inbound_text=inbound_text,
            lightweight=lightweight,
        )
        return (
            "\n".join(
                render_prompt_sections(
                    [section],
                    mode=PromptRenderMode.LABELED_BLOCK,
                )
                for section in sections
            ),
            state_changed,
            reason,
        )

    def _add_private_active_period_boundary_to_surface(
        self,
        prompt_surface: PromptSurface,
        state: dict[str, Any],
    ) -> str:
        boundary_section = self._format_active_period_boundary_prompt_section(
            state,
            public=False,
        )
        boundary = render_prompt_sections(
            [boundary_section],
            mode=PromptRenderMode.BODY_ONLY,
        )
        if boundary:
            prompt_surface.add(
                boundary_section,
                priority=89,
            )
        return boundary

    def _text_looks_like_relation_lookup_question(self, text: str) -> bool:
        cleaned = _single_line(text, 180)
        if not cleaned:
            return False
        compact = re.sub(r"\s+", "", cleaned)
        has_query_word = any(
            token in compact
            for token in (
                "认识吗",
                "认得吗",
                "知道吗",
                "是谁",
                "哪位",
                "什么人",
                "这个人",
                "这人",
                "那个人",
                "那人",
                "qq号",
                "QQ号",
                "QQ",
                "qq",
            )
        )
        if re.search(r"\d{5,12}", compact):
            return has_query_word
        if has_query_word:
            try:
                return bool(self._select_worldbook_member_profiles_for_private_text(compact, limit=1))
            except Exception:
                return True
        return False

    async def _private_reply_only_relation_lookup_text(self, event: AstrMessageEvent) -> str:
        try:
            message_id, raw_message = await self._reply_raw_message_for_event(event)
        except Exception as exc:
            logger.info("私聊引用关系网问题预读取失败: %s", _single_line(exc, 120))
            return ""
        if raw_message is None:
            return ""
        try:
            info = self._extract_reply_rich_card_info(raw_message)
        except Exception as exc:
            logger.info("私聊引用关系网问题解析失败: message_id=%s error=%s", message_id or "-", _single_line(exc, 120))
            return ""
        texts = [_single_line(item, 120) for item in info.get("texts", []) if _single_line(item, 120)]
        if not texts:
            return ""
        quoted_text = _single_line("；".join(texts[:3]), 180)
        if not self._text_looks_like_relation_lookup_question(quoted_text):
            return ""
        logger.info(
            "私聊纯引用关系网问题已补触发文本: message_id=%s text=%s",
            message_id or "-",
            _single_line(quoted_text, 120),
        )
        return quoted_text

    def _request_context_text_size(self, value: Any, *, depth: int = 0) -> int:
        if depth > 8 or value is None:
            return 0
        if isinstance(value, str):
            return len(value)
        if isinstance(value, (int, float, bool)):
            return len(str(value))
        if isinstance(value, dict):
            total = 0
            for key, item in value.items():
                if str(key) in {"tool_calls", "extra_content", "metadata"}:
                    continue
                total += self._request_context_text_size(item, depth=depth + 1)
            return total
        if isinstance(value, (list, tuple)):
            return sum(self._request_context_text_size(item, depth=depth + 1) for item in value)
        return len(str(value))

    def _plain_context_content_for_fast_reply(self, content: Any) -> str:
        if content is None:
            return ""
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    if item.strip():
                        parts.append(item.strip())
                    continue
                if not isinstance(item, dict):
                    text = str(item or "").strip()
                    if text:
                        parts.append(text)
                    continue
                item_type = str(item.get("type") or "").lower()
                if item_type in {"text", "input_text"}:
                    text = str(item.get("text") or "").strip()
                    if text:
                        parts.append(text)
                elif "image" in item_type:
                    parts.append("[图片]")
                elif "audio" in item_type or "voice" in item_type:
                    parts.append("[语音]")
            return "\n".join(parts).strip()
        if isinstance(content, dict):
            for key in ("text", "content", "value"):
                if key in content:
                    return self._plain_context_content_for_fast_reply(content.get(key))
        return str(content or "").strip()

    def _trim_passive_request_context_if_needed(self, event: AstrMessageEvent, req: ProviderRequest, *, is_private_chat: bool) -> None:
        if not is_private_chat:
            return
        contexts = getattr(req, "contexts", None)
        if not isinstance(contexts, list) or len(contexts) <= 24:
            return
        approx_tokens = max(0, self._request_context_text_size(contexts) // 4)
        if approx_tokens < 50000 and len(contexts) < 120:
            return
        trimmed: list[Any] = []
        for item in contexts[-36:]:
            if not isinstance(item, dict):
                text = self._plain_context_content_for_fast_reply(item)
                if text:
                    trimmed.append({"role": "user", "content": _single_line(text, 1200)})
                continue
            role = str(item.get("role") or "").strip().lower()
            if role not in {"system", "user", "assistant"}:
                continue
            text = self._plain_context_content_for_fast_reply(item.get("content"))
            if not text:
                continue
            trimmed.append({"role": role, "content": _single_line(text, 1200)})
        trimmed = trimmed[-24:]
        if not trimmed:
            return
        try:
            req.contexts = trimmed
        except Exception:
            return
        logger.info(
            "私聊超长上下文已启用轻量护栏: session=%s contexts=%s->%s approx_tokens=%s",
            _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            len(contexts),
            len(trimmed),
            approx_tokens,
        )

    def _context_text_is_new_conversation_boundary(self, text: Any) -> bool:
        raw = str(text or "").strip()
        if not raw:
            return False
        compact = re.sub(r"\s+", "", raw).lower()
        if compact in {"/new", "／new"}:
            return True
        if "switchedtonewconversation" in compact:
            return True
        if re.search(r"(已|成功)?(切换|开启|创建|新建).{0,8}(新)?会话", raw, flags=re.IGNORECASE):
            return True
        return False

    def _is_private_companion_command_event(self, event: AstrMessageEvent) -> bool:
        text = _single_line(getattr(event, "message_str", ""), 160)
        if not text:
            return False
        stripped = text.lstrip()
        if stripped.startswith(("/", "／", "!", "！", "#", "＃", ".", "。")):
            return True
        prefixes = (
            "陪伴群", "/陪伴群", "群陪伴", "群聊陪伴",
            "陪伴", "/陪伴", "私聊陪伴", "主动陪伴",
        )
        return any(text == prefix or re.match(rf"^{re.escape(prefix)}\s+", text) for prefix in prefixes)

    def _group_llm_reply_block_for_event(self, event: AstrMessageEvent) -> dict[str, Any]:
        if bool(getattr(event, "is_private_chat", lambda: False)()):
            return {}
        group_id = self._extract_group_id_from_event(event)
        if not group_id:
            return {}
        item = self._group_llm_reply_block_item(group_id)
        if not bool(item.get("enabled")):
            return {}
        return item

    def _passive_no_reply_event_text(self, event: AstrMessageEvent | None, *, limit: int = 180) -> str:
        if event is None:
            return ""
        candidates = [
            getattr(event, "private_companion_group_text", ""),
            getattr(event, "message_str", ""),
        ]
        message_obj = getattr(event, "message_obj", None)
        if message_obj is not None:
            candidates.append(getattr(message_obj, "message_str", ""))
        for value in candidates:
            text = _single_line(value, limit)
            if text:
                return text
        component_types: list[str] = []
        try:
            for item in self._event_components(event):
                name = _single_line(self._component_type_name(item), 32)
                if name and name not in component_types:
                    component_types.append(name)
        except Exception:
            component_types = []
        return ",".join(component_types[:6])

    def _record_passive_no_reply(
        self,
        event: AstrMessageEvent | None,
        *,
        source: str,
        reason: str,
        detail: str = "",
        level: str = "info",
        action: str = "",
        reply_preview: str = "",
    ) -> None:
        if bool(getattr(event, "_private_companion_passive_no_reply_recorded", False)):
            return
        if bool(getattr(event, "private_companion_proactive_framework", False)):
            return
        source_text = _single_line(source, 40) or "被动未回复"
        reason_text = _single_line(reason, 120) or "未说明原因"
        level_text = _single_line(level, 12)
        if level_text not in {"error", "warn", "info"}:
            level_text = "info"
        now = _now_ts()
        session = _single_line(getattr(event, "unified_msg_origin", ""), 160) if event is not None else ""
        try:
            sender_id = _single_line(event.get_sender_id(), 80) if event is not None else ""
        except Exception:
            sender_id = ""
        inbound = self._passive_no_reply_event_text(event)
        detail_text = _single_line(detail, 220)
        reply_text = _single_line(reply_preview, 180)
        key = hashlib.sha1(f"{source_text}|{reason_text}".encode("utf-8", errors="ignore")).hexdigest()[:16]
        root = self.data.setdefault("passive_no_reply_records", {})
        if not isinstance(root, dict):
            root = {}
            self.data["passive_no_reply_records"] = root
        items = root.setdefault("items", [])
        if not isinstance(items, list):
            items = []
            root["items"] = items
        target: dict[str, Any] | None = None
        for item in items:
            if isinstance(item, dict) and item.get("key") == key:
                target = item
                break
        if target is None:
            target = {
                "key": key,
                "source": source_text,
                "reason": reason_text,
                "level": level_text,
                "count": 0,
                "first_ts": now,
                "last_ts": 0,
                "samples": [],
            }
            items.append(target)
        target["source"] = source_text
        target["reason"] = reason_text
        target["level"] = level_text
        target["count"] = _safe_int(target.get("count"), 0, 0) + 1
        target["last_ts"] = now
        target["last_session"] = session
        target["last_sender_id"] = sender_id
        target["last_inbound"] = inbound
        target["last_detail"] = detail_text
        target["last_action"] = _single_line(action, 120)
        target["last_reply_preview"] = reply_text
        sample = {
            "ts": now,
            "time": self._format_timestamp_elapsed(now),
            "session": session,
            "sender_id": sender_id,
            "inbound": inbound,
            "detail": detail_text,
            "reply_preview": reply_text,
        }
        samples = target.setdefault("samples", [])
        if not isinstance(samples, list):
            samples = []
            target["samples"] = samples
        samples.insert(0, sample)
        del samples[5:]
        root["total"] = _safe_int(root.get("total"), 0, 0) + 1
        root["last_ts"] = now
        items.sort(key=lambda item: _safe_float(item.get("last_ts"), 0) if isinstance(item, dict) else 0, reverse=True)
        del items[80:]
        if event is not None:
            try:
                setattr(event, "_private_companion_passive_no_reply_recorded", True)
            except Exception:
                pass
        logger.info(
            "已记录被动未回复: source=%s reason=%s count=%s session=%s inbound=%s",
            source_text,
            reason_text,
            target.get("count"),
            session or "-",
            _single_line(inbound, 120),
        )
        try:
            self._schedule_data_save(sections={"passive_no_reply_records"})
        except Exception:
            pass
        self._schedule_reply_interception_forward(
            "plugin_block",
            source=source_text,
            reason=reason_text,
            source_session=session,
            inbound=inbound,
            after=reply_text,
            detail=detail_text,
        )

    def _proactive_only_unlock_store(self) -> set[str]:
        data = getattr(self, "data", None)
        if not isinstance(data, dict):
            return set()
        raw = data.get("proactive_only_temp_unlocks", [])
        if isinstance(raw, dict):
            items = raw.keys()
        elif isinstance(raw, (list, tuple, set)):
            items = raw
        else:
            items = []
        return {str(item).strip() for item in items if str(item or "").strip()}

    def _set_proactive_only_unlock_store(self, keys: set[str]) -> None:
        self.data["proactive_only_temp_unlocks"] = sorted(keys)

    def _proactive_only_unlock_label(self, key: str) -> str:
        return _PROACTIVE_ONLY_TEMP_UNLOCK_LABELS.get(key, key)

    def _proactive_only_temp_unlock_allows(self, feature: str = "") -> bool:
        unlocks = self._proactive_only_unlock_store()
        if not unlocks:
            return False
        if "all" in unlocks:
            return True
        feature = str(feature or "").strip()
        if not feature:
            return False
        if feature in unlocks:
            return True
        group = _PROACTIVE_ONLY_TEMP_UNLOCK_GROUPS.get(feature, set())
        return bool(group and (group & unlocks))

    def _feature_enabled_or_temp_unlocked(self, feature: str, default: bool = False) -> bool:
        if bool(runtime_persona_setting(self, feature, default)):
            return True
        return bool(
            runtime_persona_setting(self, 'enable_proactive_only_mode', False)
            and self._proactive_only_temp_unlock_allows(feature)
        )

    def _proactive_only_limited_passive_event(self, event: AstrMessageEvent | None) -> bool:
        return bool(
            runtime_persona_setting(self, 'enable_proactive_only_mode', False)
            and not bool(getattr(event, "private_companion_proactive_framework", False))
        )

    def _proactive_only_llm_request_needs_full_path(self) -> bool:
        unlocks = self._proactive_only_unlock_store()
        if "all" in unlocks or "llm_request" in unlocks:
            return True
        full_path_keys = {
            "inject_passive_states",
            "enable_intent_emotion_analysis",
            "enable_llm_timer_scheduling",
            "enable_passive_topic_suppression",
            "enable_private_image_self_recognition",
            "enable_group_companion",
            "enable_skill_growth_passive_injection",
            "enable_worldbook_member_recognition",
            "enable_livingmemory_integration",
        }
        return bool(full_path_keys & unlocks)

    def _clear_proactive_only_temp_unlocks_if_mode_off(self) -> None:
        if runtime_persona_setting(self, 'enable_proactive_only_mode', False):
            return
        if not self._proactive_only_unlock_store():
            return
        self.data["proactive_only_temp_unlocks"] = []
        self._schedule_data_save(sections={"proactive_only_temp_unlocks"})

    def _related_proactive_only_unlock_keys(self, key: str) -> list[str]:
        related = list(_PROACTIVE_ONLY_TEMP_UNLOCK_RELATED.get(key, []) or [])
        return [item for item in related if item and item != key]

    def _proactive_only_blocks_passive_event(self, event: AstrMessageEvent | None, feature: str = "") -> bool:
        proactive_framework = bool(getattr(event, "private_companion_proactive_framework", False))
        allow_proactive_photo = feature == "pc_generate_photo"
        effective_feature = "pc_tools" if allow_proactive_photo else feature
        if effective_feature == "pc_tools" and proactive_framework and not allow_proactive_photo:
            return True
        if not bool(runtime_persona_setting(self, 'enable_proactive_only_mode', False)):
            self._clear_proactive_only_temp_unlocks_if_mode_off()
            return False
        if proactive_framework:
            return False
        return not self._proactive_only_temp_unlock_allows(effective_feature)

    def _extract_user_photo_nai_params(self, text: str) -> str:
        return extract_user_photo_nai_params(text)

    async def _record_proactive_only_private_feedback(
        self,
        event: AstrMessageEvent,
        *,
        user_id: str,
        sender_display_name: str,
        text: str,
        received_ts: float,
    ) -> None:
        """主动专用模式下只记录用户回应,不接管被动回复链路。"""
        async with self._data_lock:
            users = self.data.get("users", {})
            canonical_user_id = self._canonical_private_user_id(user_id)
            user = users.get(canonical_user_id) if isinstance(users, dict) else None
            if not isinstance(user, dict):
                return
            user_id = canonical_user_id
            if not self._private_passive_profile_available(user_id, user):
                return
            if self._is_recent_poke_echo(user, text):
                logger.info("主动专用模式忽略 poke 回流事件: user=%s", user_id)
                return
            if self._is_duplicate_inbound_message(event, scope=f"private:{user_id}", sender_id=user_id, text=text):
                self._schedule_data_save(sections={"inbound_debounce_stats"})
                return
            self._note_private_user_umo(user_id, user, event.unified_msg_origin)
            self._note_private_display_name_observation(user, user_id, sender_display_name, now=received_ts)
            user["last_seen"] = received_ts
            user["last_activity_at"] = received_ts
            self._note_private_inbound_activity(user, received_ts, text=text)
            self._mark_greetings_satisfied_by_recent_activity(user, activity_ts=received_ts)
            self._note_morning_greeting_reply(user, now=received_ts)
            if self._cancel_inbound_conflicting_greeting(
                user,
                now=received_ts,
                user_id=user_id,
                trigger_umo=str(getattr(event, "unified_msg_origin", "") or ""),
            ):
                logger.info("用户已在当前问候时段自然来聊,已请求取消冲突问候候选: %s", user_id)
                if not self._simulation_active(user) and _safe_float(user.get("next_proactive_at"), 0) <= 0:
                    self._schedule_next_proactive(user, now=received_ts)
            if text:
                safe_text = self._sanitize_orphan_tts_placeholders(text)
                user["last_user_message"] = safe_text or text
                user["last_user_message_at"] = received_ts
                if self._clear_state_share_proactive_after_user_status_question(user, user_id=user_id, text=safe_text or text, now=received_ts):
                    if not self._simulation_active(user) and _safe_float(user.get("next_proactive_at"), 0) <= 0:
                        self._schedule_next_proactive(user, now=received_ts)
                user["inbound_count"] = _safe_int(user.get("inbound_count"), 0) + 1
                user["episode_message_count"] = _safe_int(user.get("episode_message_count"), 0, 0) + 1
                self._apply_user_rest_silence_from_message(user, safe_text or text, now=received_ts)
            if _safe_float(user.get("awaiting_reply_since"), 0) > 0:
                audit_outcome_recorder = getattr(self, "_mark_proactive_audit_reply_outcome", None)
                if callable(audit_outcome_recorder):
                    audit_outcome_recorder(
                        user,
                        received_at=received_ts,
                        message_id=self._event_message_id(event),
                    )
                user["reply_count"] = _safe_int(user.get("reply_count"), 0) + 1
                self._note_action_reply_feedback(
                    user,
                    str(user.get("last_proactive_action") or "message"),
                    text,
                )
                self._apply_relationship_event(
                    user,
                    2,
                    reason_code="proactive_reply",
                    event_id=self._event_message_id(event),
                    now=received_ts,
                )
                user["awaiting_reply_since"] = 0
                user["last_reply_at"] = received_ts
                user["last_private_reply_at"] = received_ts
                user["pending_followup_event"] = {}
                user["planned_proactive_quota_exempt"] = False
            user["ignored_streak"] = 0
            user["friend_unanswered_silenced_since"] = 0
            user["friend_unanswered_silence_note"] = ""
            meal_care_result: dict[str, Any] = {}
            if self._private_user_role(user, user_id) == "owner" and text:
                meal_care_result = self._handle_meal_care_inbound(user, text, now=received_ts)
            save_sections = {"users"}
            if meal_care_result.get("foods"):
                save_sections.add("food_menu")
            self._schedule_data_save(sections=save_sections)
        logger.info(
            "主动消息专用模式已跳过私聊被动增强: user=%s text=%s",
            user_id,
            _single_line(text, 80) or "非文本消息",
        )

    @filter.on_llm_request()
    @_multi_persona_event_context
    async def inject_tts_enhancement_request_fallback(self, event: AstrMessageEvent, req: ProviderRequest, *args, **kwargs):
        """TTS 请求规则独立兜底，避免被状态注入链路早退顺手跳过。"""
        if self is None or not self.enabled:
            return
        if self._stop_group_llm_reply_if_blocked(event, source="llm_request_tts_fallback"):
            return
        if self._proactive_only_blocks_passive_event(event, "enable_tts_enhancement"):
            return
        await self.apply_tts_enhancement_request(event, req)

    def _llm_request_provider_settings_for_event(self, event: AstrMessageEvent | None) -> dict[str, Any]:
        umo = str(getattr(event, "unified_msg_origin", "") or "")
        resolver = getattr(self, "_astrbot_provider_settings_for_umo", None)
        if callable(resolver):
            try:
                return dict(resolver(umo) or {})
            except Exception:
                pass
        try:
            cfg = self.context.get_config(umo=umo) if umo else self.context.get_config()
        except TypeError:
            try:
                cfg = self.context.get_config(umo) if umo else self.context.get_config()
            except Exception:
                cfg = {}
        except Exception:
            cfg = {}
        settings = cfg.get("provider_settings", {}) if isinstance(cfg, dict) else {}
        return dict(settings or {}) if isinstance(settings, dict) else {}

    def _llm_request_provider_identity_parts(self, event: AstrMessageEvent | None, req: ProviderRequest | None) -> list[str]:
        parts: list[str] = []

        def add(value: Any) -> None:
            text = _single_line(value, 200)
            if text and text not in parts:
                parts.append(text)

        if req is not None:
            for key in ("provider_id", "llm_provider_id", "chat_provider_id", "model"):
                add(getattr(req, key, ""))
        settings = self._llm_request_provider_settings_for_event(event)
        for key in (
            "default_provider_id",
            "default_llm_provider_id",
            "provider_id",
            "model",
            "api_base",
            "base_url",
        ):
            add(settings.get(key))
        context = getattr(self, "context", None)
        get_using = getattr(context, "get_using_provider", None)
        if callable(get_using):
            umo = str(getattr(event, "unified_msg_origin", "") or "")
            provider = None
            try:
                provider = get_using(umo=umo) if umo else get_using()
            except TypeError:
                try:
                    provider = get_using(umo) if umo else get_using(None)
                except Exception:
                    provider = None
            except Exception:
                provider = None
            if provider is not None:
                try:
                    meta = provider.meta()
                    if isinstance(meta, dict):
                        for key in ("id", "model", "type"):
                            add(meta.get(key))
                    else:
                        for key in ("id", "model", "type"):
                            add(getattr(meta, key, ""))
                except Exception:
                    pass
                config = getattr(provider, "provider_config", None) or getattr(provider, "config", None) or {}
                if isinstance(config, dict):
                    for key in ("id", "provider_id", "provider", "model", "api_base", "base_url"):
                        add(config.get(key))
        return parts

    def _llm_request_uses_gemini_family_provider(self, event: AstrMessageEvent | None, req: ProviderRequest | None) -> bool:
        identity = " ".join(self._llm_request_provider_identity_parts(event, req)).lower()
        return any(
            marker in identity
            for marker in (
                "gemini",
                "generativelanguage.googleapis.com",
                "googleapis.com/v1beta/openai",
            )
        )

    def _llm_request_uses_deepseek_family_provider(self, event: AstrMessageEvent | None, req: ProviderRequest | None) -> bool:
        identity = " ".join(self._llm_request_provider_identity_parts(event, req)).lower()
        return "deepseek" in identity

    def _llm_request_uses_deepseek_openai_compatible_provider(
        self,
        event: AstrMessageEvent | None,
        req: ProviderRequest | None,
    ) -> bool:
        """Limit history cleanup to DeepSeek's OpenAI-compatible endpoint."""
        identity = " ".join(self._llm_request_provider_identity_parts(event, req)).lower()
        return "deepseek" in identity

    def _append_deepseek_tool_protocol_guard(self, event: AstrMessageEvent, req: ProviderRequest) -> bool:
        if getattr(req, "func_tool", None) is None:
            return False
        if not self._llm_request_uses_deepseek_family_provider(event, req):
            return False
        marker = "<!-- private_companion_tool_protocol_v1 -->"
        current_prompt = str(getattr(req, "system_prompt", "") or "")
        if marker in current_prompt:
            return False
        instruction = (
            "当前模型兼容接口会严格核对每个 tool_call_id 与工具结果。"
            "需要使用多个工具时，请按顺序逐个调用：每条 assistant 消息只发起一个工具调用，"
            "拿到该工具结果后再决定是否调用下一个；不要并行或批量发起 tool_calls。"
            "回复当前会话的普通文字时，直接输出最终回复，不要调用 send_message_to_user；"
            "确需使用该工具发送媒体或主动消息时，plain 文本不得为空，调用同一轮不要额外输出可见正文。"
        )
        section = prompt_section(
            key="tools.deepseek_protocol",
            title="工具调用协议",
            source="tools",
            content=instruction,
        )
        self._materialize_conversation_system_block(
            req,
            section=section,
            marker=marker,
            priority=10,
            placement=PLACEMENT_DYNAMIC_SYSTEM,
        )
        return True

    def _append_passive_reply_tool_boundary(self, event: AstrMessageEvent, req: ProviderRequest) -> list[str]:
        """Guide ordinary replies without removing AstrBot's media sender.

        Plain text stays on the final assistant-response path so it cannot be
        delivered twice. The official sender remains available for real files,
        images, records and videos that the current turn needs to deliver.
        """
        if req is None:
            return []
        if callable(getattr(self, "_event_requires_direct_same_session_tool_delivery", None)):
            if self._event_requires_direct_same_session_tool_delivery(event):
                return []
        if str(getattr(event, "_private_companion_external_proactive_source", "") or ""):
            return []
        umo = _single_line(getattr(event, "unified_msg_origin", ""), 240)
        if not umo or not any(marker in umo for marker in (":GroupMessage:", ":FriendMessage:")):
            return []

        try:
            setattr(event, "_private_companion_passive_reply_tool_boundary", True)
            setattr(event, "_private_companion_passive_reply_request", req)
        except Exception:
            pass
        marker = "<!-- private_companion_passive_reply_tool_boundary_v1 -->"
        plan = get_conversation_injection_plan(req, create=False)
        # Agent startup repeats this hook after the request plan is frozen.
        if plan is not None and (plan.frozen or plan.contains_marker(marker)):
            return []
        prompt = str(getattr(req, "system_prompt", "") or "")
        instruction = (
            "这是普通私聊或群聊的被动回复。请直接输出一次最终正文；"
            "普通文字不要调用 `send_message_to_user`，同一正文也不要在工具调用后再次输出。"
            "只有本轮确实需要投递已经存在、且带有真实 path 或 url 的图片、音频、视频或文件时，"
            "才可使用该官方工具；messages 至少包含一个非 plain 媒体组件。"
            "媒体消息中的 plain 只写必要附言，工具调用后不要重复输出附言或发送结果。"
            "不要猜测文件路径，也不要把该工具当成生成、搜索或读取文件的能力。"
            "需要跨会话主动发送时，使用 PrivateCompanion 专用发送工具；官方 Cron 任务不受此边界影响。"
        )
        section = prompt_section(
            key="tools.passive_reply_boundary",
            title="当前会话回复边界",
            source="tools",
            content=instruction,
        )
        if marker not in prompt and hasattr(req, "system_prompt"):
            materializer = getattr(self, "_materialize_conversation_system_block", None)
            if callable(materializer):
                materializer(
                    req,
                    section=section,
                    marker=marker,
                    priority=10,
                    placement=PLACEMENT_DYNAMIC_SYSTEM,
                )
            else:
                plan = get_conversation_injection_plan(req)
                if plan is not None:
                    plan.materialize_system_block(
                        req,
                        section=section,
                        marker=marker,
                        priority=10,
                        placement=PLACEMENT_DYNAMIC_SYSTEM,
                    )
            if self._tool_set_has_named_tool(getattr(req, "func_tool", None), "send_message_to_user"):
                logger.info(
                    "已约束被动回复的 send_message_to_user 仅用于媒体投递: session=%s",
                    umo,
                )
        return []

    def _finalize_passive_reply_tool_boundary(self, event: AstrMessageEvent) -> list[str]:
        if not bool(getattr(event, "_private_companion_passive_reply_tool_boundary", False)):
            return []
        req = getattr(event, "_private_companion_passive_reply_request", None)
        getter = getattr(event, "get_extra", None)
        if callable(getter):
            try:
                req = getter("provider_request") or req
            except Exception:
                pass
        return self._append_passive_reply_tool_boundary(event, req) if req is not None else []

    @staticmethod
    def _tool_set_has_named_tool(tool_set: Any, tool_name: str) -> bool:
        get_tool = getattr(tool_set, "get_tool", None)
        if callable(get_tool):
            try:
                return get_tool(tool_name) is not None
            except Exception:
                pass
        tools = getattr(tool_set, "tools", None)
        if isinstance(tools, list):
            return any(_single_line(getattr(tool, "name", ""), 120) == tool_name for tool in tools)
        return False

    @staticmethod
    def _tool_set_tool_names(tool_set: Any) -> list[str]:
        names: list[str] = []
        tools = getattr(tool_set, "tools", None)
        if isinstance(tools, list):
            for tool in tools:
                name = _single_line(getattr(tool, "name", ""), 120)
                if name and name not in names:
                    names.append(name)
        return names

    @staticmethod
    def _safe_event_sender_id(event: AstrMessageEvent | None) -> str:
        if event is None:
            return ""
        getter = getattr(event, "get_sender_id", None)
        if callable(getter):
            try:
                return _single_line(getter(), 80)
            except Exception:
                pass
        return _single_line(getattr(event, "sender_id", "") or getattr(event, "user_id", ""), 80)

    @staticmethod
    def _safe_event_is_private(event: AstrMessageEvent | None) -> bool:
        if event is None:
            return False
        unified_msg_origin = str(getattr(event, "unified_msg_origin", "") or "")
        try:
            if bool(getattr(event, "is_private_chat", lambda: False)()):
                return True
        except Exception:
            pass
        return ":FriendMessage:" in unified_msg_origin

    def _is_owner_private_event(self, event: AstrMessageEvent | None) -> bool:
        if event is None:
            return False
        if not self._safe_event_is_private(event):
            return False
        try:
            resolver = getattr(self, "_private_user_id_for_event", None)
            requester_id = (
                resolver(event)
                if callable(resolver)
                else self._canonical_private_user_id(self._safe_event_sender_id(event))
            )
        except Exception:
            requester_id = ""
        if not requester_id:
            return False
        requester_profile = None
        try:
            requester_profile = self._get_user(requester_id)
        except Exception:
            users = self.data.get("users") if isinstance(getattr(self, "data", {}), dict) and isinstance(self.data.get("users"), dict) else {}
            requester_profile = users.get(requester_id) if isinstance(users, dict) else None
        try:
            return (
                bool(requester_id and self._is_target_private_user(requester_id, requester_profile if isinstance(requester_profile, dict) else None))
                and isinstance(requester_profile, dict)
                and bool(requester_profile.get("enabled", True))
                and self._private_user_role(requester_profile, requester_id) == "owner"
            )
        except Exception:
            return False

    @filter.on_llm_request(priority=220000)
    @_multi_persona_event_context
    async def guard_req036_private_capability_before_llm(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        *args,
        **kwargs,
    ):
        """Compatibility hook retained after removing passive private-chat gating."""
        return

    @filter.on_llm_request(priority=-30000)
    @_multi_persona_event_context
    async def enforce_p4_live_confinement_before_enrichment(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        *args,
        **kwargs,
    ):
        """Block only an already-resolved private person with an invalid or active P4 state."""
        if self is None or req is None or not bool(getattr(self, "enabled", False)):
            return
        state = self._p4_live_state_for_event(event)
        decision = decide_live_request(state)
        if decision.get("decision") == "skip":
            return
        if decision.get("decision") == "block":
            # This runs before P5/Memory and the enrichment collectors. Leave
            # no request route to original prompt, tool, bridge, or context data.
            for attribute, value in (
                ("system_prompt", SAFE_CONFINEMENT_REPLY),
                ("prompt", ""),
                ("contexts", []),
                ("extra_user_content_parts", []),
                ("func_tool", None),
                ("tools", []),
                ("images", []),
                ("image_urls", []),
            ):
                try:
                    setattr(req, attribute, value)
                except Exception:
                    pass
            try:
                setattr(event, "private_companion_p4_blocked", True)
                setattr(event, "private_companion_p4_block_code", decision.get("code", "p4_state_invalid"))
            except Exception:
                pass
            await self._reply(event, SAFE_CONFINEMENT_REPLY)
            event.stop_event()
            return
        temperature = compose_reply_temperature(
            decision.get("warmth_projection", {}).get("tier"),
            **self._bounded_p4_reply_temperature_signals(event),
        )
        try:
            setattr(req, "_private_companion_reply_temperature", temperature)
        except Exception:
            pass
        if hasattr(req, "system_prompt"):
            section = reply_temperature_prompt_section(temperature)
            self._materialize_conversation_system_block(
                req,
                section=section,
                marker="[Reply boundary]",
                priority=5,
                placement=PLACEMENT_DYNAMIC_SYSTEM,
            )

    @filter.on_llm_request(priority=-30000)
    @_multi_persona_event_context
    async def inject_unified_relationship_expression(self, event: AstrMessageEvent, req: ProviderRequest, *args, **kwargs):
        """Inject one fail-closed relationship expression decision before Memory enrichment."""
        if self is None or req is None or not bool(getattr(self, "enabled", False)):
            return
        if not bool(runtime_persona_setting(self, 'enable_custom_relationship_stage_policy', False)):
            return
        is_private = self._safe_event_is_private(event)
        group_id = "" if is_private else self._extract_group_id_from_event(event)
        if not is_private and not group_id:
            return
        raw_sender_id = self._safe_event_sender_id(event)
        current_user = None
        if is_private:
            try:
                resolver = getattr(self, "_private_user_id_for_event", None)
                sender_id = (
                    resolver(event)
                    if callable(resolver)
                    else self._canonical_private_user_id(raw_sender_id)
                )
            except Exception:
                sender_id = ""
            users = self.data.get("users", {}) if isinstance(getattr(self, "data", None), dict) else {}
            current_user = users.get(sender_id) if sender_id and isinstance(users, dict) else None
        else:
            projection_getter = getattr(self, "_req039_group_observation_projection", None)
            if callable(projection_getter):
                current_user = projection_getter(
                    event,
                    sender_id=raw_sender_id,
                    sender_name=self._sender_display_name(event),
                )
        if not isinstance(current_user, dict):
            return
        fixture_user = self._lab_fixture_relationship_view(event, current_user)
        fixture_relationship_applied = fixture_user is not current_user
        current_user = fixture_user
        expression_builder = getattr(self, "_build_expression_decision_for_user", None)
        if not callable(expression_builder):
            return
        try:
            expression_args = {
                "passive_reengagement": True,
                "bot_state": {
                    "energy": current_user.get("bot_energy", 70),
                    "mood": current_user.get("bot_mood", ""),
                },
                "message_intent": content_intent_from_text(getattr(event, "message_str", "")),
                "content_policy": {
                    "enabled": bool(runtime_persona_setting(self, 'enable_relationship_content_tiers', False)),
                    "flirt_enabled": bool(runtime_persona_setting(self, 'enable_flirt_content_tier', True)),
                    "private_chat": is_private,
                },
                "channel_scope": "private" if is_private else "group",
            }
            if fixture_relationship_applied:
                expression_args["_authoritative_relationship_view"] = True
            expression = expression_builder(current_user, **expression_args)
            projection = expression.to_dict() if hasattr(expression, "to_dict") else dict(expression or {})
            if is_private:
                violation_hint_getter = getattr(self, "_relationship_violation_prompt_hint", None)
                if callable(violation_hint_getter):
                    hint = violation_hint_getter(current_user, now=_now_ts())
                    if hint:
                        projection["relationship_violation_hint"] = hint
        except Exception as exc:
            logger.debug("统一表达决策生成失败，使用日常保守默认值: %s", _single_line(exc, 120))
            projection = build_expression_decision({}).to_dict()
        try:
            setattr(req, "_private_companion_expression_decision", projection)
            setattr(event, "_private_companion_expression_decision", projection)
        except Exception:
            pass
        section = expression_decision_prompt_section(projection)
        self._append_turn_prompt_fragment_by_position(
            req,
            "<!-- private_companion_expression_decision_v2 -->",
            section,
            priority=5,
            force_dynamic=True,
        )

    def _remove_sensitive_screen_tools_from_request(self, event: AstrMessageEvent, req: ProviderRequest) -> list[str]:
        tool_set = getattr(req, "func_tool", None)
        if tool_set is None:
            return []
        allow_owner_private = self._is_owner_private_event(event)
        if allow_owner_private:
            return []
        sensitive_tools = {"screen_peek", "screen_usage_context"}
        names = self._tool_set_tool_names(tool_set)
        if not names:
            names = [name for name in sensitive_tools if self._tool_set_has_named_tool(tool_set, name)]
        removed: list[str] = []
        remove_tool = getattr(tool_set, "remove_tool", None)
        for name in names:
            if name not in sensitive_tools:
                continue
            try:
                if callable(remove_tool):
                    remove_tool(name)
                else:
                    tools = getattr(tool_set, "tools", None)
                    if isinstance(tools, list):
                        tool_set.tools = [tool for tool in tools if _single_line(getattr(tool, "name", ""), 120) != name]
                    else:
                        continue
                removed.append(name)
            except Exception as exc:
                logger.warning(
                    "移除敏感屏幕工具失败: tool=%s session=%s error=%s",
                    name,
                    _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                    _single_line(exc, 160),
                )
        if removed:
            try:
                setattr(event, "_private_companion_removed_sensitive_tools", removed)
            except Exception:
                pass
            logger.info(
                "已移除非主人私聊场景的敏感屏幕工具: session=%s sender=%s tools=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                self._safe_event_sender_id(event) or "-",
                ",".join(removed),
            )
        return removed

    @filter.on_llm_request(priority=-240000)
    @_multi_persona_event_context
    async def flush_conversation_injection_plan(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        *args,
        **kwargs,
    ):
        """Render all registered plugin-owned conversation blocks once before provider cleanup."""
        if self is None or req is None or not bool(getattr(self, "enabled", False)):
            return
        plan = get_conversation_injection_plan(req, create=False)
        if plan is None:
            return
        try:
            plan.render_into(req)
        except Exception as exc:
            logger.error(
                "主对话注入计划最终渲染失败: session=%s error=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                _single_line(exc, 180),
            )

    def _scope_photo_generation_tool_for_request(
        self,
        req: ProviderRequest,
        event: AstrMessageEvent | None = None,
    ) -> bool:
        """Remove the Image tool when this request lacks runtime permission."""
        if req is None or self._user_photo_generation_prompt_enabled(event):
            return False
        tool_set = getattr(req, "func_tool", None)
        if tool_set is None:
            return False
        tools = getattr(tool_set, "tools", None)
        if isinstance(tools, list):
            filtered = [
                tool
                for tool in tools
                if _single_line(getattr(tool, "name", ""), 120) != "pc_generate_photo"
            ]
            if len(filtered) == len(tools):
                return False
            try:
                request_tool_set = copy(tool_set)
                request_tool_set.tools = filtered
                req.func_tool = request_tool_set
                return True
            except Exception as exc:
                logger.debug(
                    "请求级移除未就绪生图工具失败: %s",
                    _single_line(exc, 120),
                )
                return False

        try:
            request_tool_set = copy(tool_set)
            remove_tool = getattr(request_tool_set, "remove_tool", None)
            if not callable(remove_tool):
                return False
            remove_tool("pc_generate_photo")
            req.func_tool = request_tool_set
            return True
        except Exception as exc:
            logger.debug(
                "兼容请求级移除未就绪生图工具失败: %s",
                _single_line(exc, 120),
            )
            return False

    def _annotate_photo_tool_prompt_format_for_request(self, req: ProviderRequest) -> bool:
        """Attach the selected prompt syntax to this request's photo tool schema."""
        if not self._photo_generation_runtime_available():
            return False
        tool_set = getattr(req, "func_tool", None) if req is not None else None
        get_tool = getattr(tool_set, "get_tool", None) if tool_set is not None else None
        if not callable(get_tool):
            return False
        try:
            tool = get_tool("pc_generate_photo")
        except Exception:
            return False
        if tool is None or not bool(getattr(tool, "active", True)):
            return False

        instruction_getter = getattr(self, "_photo_tool_prompt_format_instruction", None)
        if not callable(instruction_getter):
            return False
        instruction = re.sub(
            r"\s+",
            " ",
            str(instruction_getter() or ""),
        ).strip()
        if not instruction:
            return False
        marker = _PHOTO_TOOL_PROMPT_FORMAT_MARKER
        description = re.sub(
            rf"\n*\s*{re.escape(marker)}.*?{re.escape(marker)}\s*",
            "",
            str(getattr(tool, "description", "") or ""),
            flags=re.DOTALL,
        ).strip()
        annotated = (
            f"{description}\n\n{marker}\n"
            f"{render_prompt_content(prompt_heading_ref('提示词表达方式'))}"
            f"prompt 参数必须按下述格式书写：{instruction}\n"
            f"{marker}"
        ).strip()
        contract_section = prompt_section(
            key="tool.photo.prompt_format",
            title="提示词表达方式",
            source="photo_tool",
            content=exact_text(annotated),
        )

        def record_contract() -> None:
            plan = get_conversation_injection_plan(req)
            if plan is None:
                return
            plan.add(
                section=contract_section,
                marker=marker,
                priority=10,
                placement=PLACEMENT_TOOL_CONTRACT,
                materialized=True,
                merge_policy="replace",
            )

        tools = getattr(tool_set, "tools", None)
        if isinstance(tools, list):
            try:
                for index, existing in enumerate(tools):
                    if existing is tool:
                        request_tool = copy(tool)
                        request_tool.description = annotated
                        request_tools = list(tools)
                        request_tools[index] = request_tool
                        request_tool_set = copy(tool_set)
                        request_tool_set.tools = request_tools
                        req.func_tool = request_tool_set
                        record_contract()
                        return True
            except Exception as exc:
                logger.debug(
                    "pc_generate_photo 请求工具描述标注失败: %s",
                    _single_line(exc, 120),
                )
                return False

        # Older request-local wrappers may expose get_tool() without a tools list.
        if getattr(tool, "handler", None) is None:
            try:
                tool.description = annotated
                record_contract()
                return True
            except Exception as exc:
                logger.debug(
                    "pc_generate_photo 兼容工具描述标注失败: %s",
                    _single_line(exc, 120),
                )
        return False

    @filter.on_llm_request(priority=-251000)
    @_multi_persona_event_context
    async def annotate_photo_tool_prompt_format(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        *args,
        **kwargs,
    ):
        """Expose prompt-format guidance whenever the photo tool is available."""
        if self is None or req is None or not bool(getattr(self, "enabled", False)):
            return
        try:
            if self._scope_photo_generation_tool_for_request(req, event):
                return
            self._annotate_photo_tool_prompt_format_for_request(req)
        except Exception as exc:
            logger.debug(
                "pc_generate_photo 工具提示词格式标注失败: %s",
                _single_line(exc, 120),
            )

    @filter.on_llm_request(priority=-252000)
    @_multi_persona_event_context
    async def intercept_native_astrbot_group_context(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        *args,
        **kwargs,
    ):
        """Prefer the plugin group context while preserving AstrBot records."""
        if self is None or req is None or not bool(getattr(self, "enabled", False)):
            return
        if bool(getattr(event, "is_private_chat", lambda: False)()):
            return
        if not bool(runtime_persona_setting(self, "intercept_astrbot_group_context", True)):
            return
        if not bool(runtime_persona_setting(self, "enable_group_history_injection", True)):
            return
        marker = "<!-- private_companion_group_context_v1 -->"
        if not self._request_has_managed_prompt_marker(req, marker):
            return
        result = intercept_astrbot_group_context(event, req)
        logger.info(
            "已拦截 AstrBot 群聊对话注入: session=%s history=%s icl=%s",
            _single_line(getattr(event, "unified_msg_origin", ""), 140) or "unknown",
            result.get("history_messages", 0),
            result.get("group_icl_removed", 0),
        )

    @filter.on_llm_request(priority=-259000)
    @_multi_persona_event_context
    async def enforce_private_request_scope_isolation(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        *args,
        **kwargs,
    ):
        """Prune group-only plan blocks and residues before private dispatch."""
        if self is None or req is None or not bool(getattr(self, "enabled", False)):
            return
        try:
            if not bool(getattr(event, "is_private_chat", lambda: False)()):
                return
        except Exception:
            if ":FriendMessage:" not in str(
                getattr(event, "unified_msg_origin", "") or ""
            ):
                return

        plan = get_conversation_injection_plan(req, create=False)
        if plan is not None:
            try:
                if plan.remove_markers(
                    f"<!-- {marker} -->" for marker in GROUP_SCOPE_MARKERS
                ):
                    plan.render_into(req)
            except Exception as exc:
                logger.warning(
                "私聊请求的群作用域注入计划裁剪失败: session=%s error=%s",
                    _single_line(getattr(event, "unified_msg_origin", ""), 120)
                    or "unknown",
                    _single_line(exc, 160),
                )
        self._sanitize_private_companion_prompt_artifacts_in_request(event, req)

    @filter.on_llm_request(priority=-260000)
    @_multi_persona_event_context
    async def finalize_conversation_injection_plan(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        *args,
        **kwargs,
    ):
        """Freeze a privacy-safe manifest after all plugin-owned request changes."""
        if self is None or req is None or not bool(getattr(self, "enabled", False)):
            return
        plan = get_conversation_injection_plan(req, create=False)
        if plan is None:
            return
        try:
            plan.render_into(req)
            setattr(
                req,
                "_private_companion_conversation_injection_manifest",
                plan.manifest(),
            )
            plan.freeze()
        except Exception as exc:
            logger.error(
                "主对话注入计划冻结失败: session=%s error=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120)
                or "unknown",
                _single_line(exc, 180),
            )

    @filter.on_llm_request()
    @_multi_persona_event_context
    async def inject_humanized_state(self, event: AstrMessageEvent, req: ProviderRequest, *args, **kwargs):
        return await run_humanized_state_injection(self, event, req, *args, **kwargs)

    @filter.on_llm_response(priority=120000)
    @_multi_persona_event_context
    async def replace_sensitive_conversation_response(
        self,
        event: AstrMessageEvent,
        resp: LLMResponse,
        *args,
        **kwargs,
    ):
        """Retry common model refusals on the configured conversation Provider."""
        if self is None or not bool(getattr(self, "enabled", False)):
            return
        if not bool(getattr(self, "enable_sensitive_model_replacement", False)):
            return
        if not scope_allows(getattr(self, "model_replacement_scope", "plugin"), "conversation"):
            return
        if bool(getattr(event, "private_companion_sensitive_model_retry", False)):
            return
        text = str(getattr(resp, "completion_text", "") or "").strip()
        keyword = contains_sensitive_refusal(text, getattr(self, "sensitive_replacement_keywords", ""))
        if not keyword:
            return
        target_provider = _single_line(getattr(self, "sensitive_replacement_provider_id", ""), 160)
        if not target_provider:
            return
        current_provider = self._provider_id_from_llm_response(resp)
        if target_provider == current_provider:
            return
        getter = getattr(getattr(self, "context", None), "get_provider_by_id", None)
        if not callable(getter):
            return
        try:
            if getter(target_provider) is None:
                logger.warning("敏感拒答替换模型不存在：%s", target_provider)
                return
        except Exception:
            return
        request = self._model_replacement_event_extra(event, "provider_request", None)
        if request is None:
            request = getattr(event, "private_companion_model_replacement_request", None)
        try:
            setattr(event, "private_companion_sensitive_model_retry", True)
        except Exception:
            pass
        try:
            retry_kwargs: dict[str, Any] = {
                "chat_provider_id": target_provider,
                "prompt": getattr(request, "prompt", None) if request is not None else getattr(event, "message_str", ""),
                "contexts": getattr(request, "contexts", None) if request is not None else None,
                "system_prompt": getattr(request, "system_prompt", None) if request is not None else None,
                "image_urls": list(getattr(request, "image_urls", None) or []) if request is not None else None,
                "audio_urls": list(getattr(request, "audio_urls", None) or []) if request is not None else None,
            }
            fallback = await self.context.llm_generate(**retry_kwargs)
            fallback_text = str(getattr(fallback, "completion_text", "") or "").strip()
            fallback_keyword = contains_sensitive_refusal(
                fallback_text,
                getattr(self, "sensitive_replacement_keywords", ""),
            )
            if not fallback_text or fallback_keyword:
                try:
                    resp.result_chain = None
                    resp.completion_text = ""
                except Exception:
                    pass
                logger.warning(
                    "敏感拒答替换模型仍拒答，已阻断原回复: original=%s target=%s keyword=%s",
                    _single_line(current_provider, 120) or "unknown",
                    target_provider,
                    _single_line(fallback_keyword or keyword, 80),
                )
                return
            resp.completion_text = fallback_text
            resp.result_chain = getattr(fallback, "result_chain", None)
            try:
                resp.role = getattr(fallback, "role", None) or resp.role
            except Exception:
                pass
            logger.info(
                "检测到模型敏感拒答，已改用指定对话模型: original=%s target=%s keyword=%s",
                _single_line(current_provider, 120) or "unknown",
                target_provider,
                _single_line(keyword, 80),
            )
        except Exception as exc:
            logger.warning(
                "敏感拒答替换模型调用失败，已阻断原回复: target=%s error=%s",
                target_provider,
                _single_line(exc, 180),
            )
            try:
                resp.result_chain = None
                resp.completion_text = ""
            except Exception:
                pass

    @filter.on_llm_response()
    @_multi_persona_event_context
    async def normalize_tts_enhancement_response(self, event: AstrMessageEvent, resp: LLMResponse, *args, **kwargs):
        """恢复降级为正文的生图调用，并规范化 TTS 标签。"""
        if self is None or not self.enabled:
            return
        original_text = str(getattr(resp, "completion_text", "") or "")
        same_session_tool = getattr(self, "_prepare_same_session_send_tool_response", None)
        same_session_tool_call = False
        if callable(same_session_tool):
            try:
                same_session_tool_call, _ = same_session_tool(event, resp)
            except Exception as exc:
                logger.debug(
                    "同会话工具回复去重准备失败: %s",
                    _single_line(exc, 120),
                )
        if same_session_tool_call:
            # AstrBot yields completion_text even when the same response also has
            # a tool call. The tool/final-response path is authoritative here.
            try:
                resp.result_chain = None
            except Exception:
                pass
            resp.completion_text = ""
            original_text = ""
        tool_names = getattr(resp, "tools_call_name", None)
        if isinstance(tool_names, str):
            normalized_tool_names = {tool_names.strip()}
        elif isinstance(tool_names, (list, tuple, set)):
            normalized_tool_names = {
                str(item or "").strip() for item in tool_names if str(item or "").strip()
            }
        else:
            normalized_tool_names = set()
        media_delivery_tool_call = bool(
            normalized_tool_names
            & {"pc_find_reaction_image", "pc_generate_photo", "pc_send_current_media"}
        )
        if media_delivery_tool_call:
            # These tools own their visible caption/media delivery. AstrBot also
            # yields assistant content attached to a tool call as an llm_result;
            # exposing that intermediate text produces a duplicate before the
            # tool result is known and can falsely claim that an image was sent.
            try:
                resp.result_chain = None
            except Exception:
                pass
            resp.completion_text = ""
            original_text = ""
            logger.info(
                "已隐藏媒体工具调用前的中间正文: tools=%s session=%s",
                ",".join(sorted(normalized_tool_names)),
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            )
        recovered_text, _ = await self._recover_plaintext_photo_tool_call(event, resp, original_text)
        if recovered_text != original_text:
            resp.completion_text = recovered_text
        if bool(getattr(event, "_private_companion_photo_tool_sent", False)):
            # pc_generate_photo 已经把 caption 与图片作为唯一可见回复发出。
            # 不论模型是否输出静默标记，都丢弃同一轮尾随正文，避免再次分段、TTS 或触发表情附件。
            try:
                resp.result_chain = None
            except Exception:
                pass
            resp.completion_text = ""
            for attr in (
                "_private_companion_reaction_expression_intent",
                "_private_companion_deferred_reaction_tts",
                "_private_companion_reaction_expression_expected_primary_chunks",
                "_private_companion_reaction_expression_segmented_remainder",
            ):
                try:
                    delattr(event, attr)
                except (AttributeError, TypeError):
                    pass
            logger.info(
                "图片已发送，已丢弃同轮尾随模型正文: session=%s chars=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                len(recovered_text or ""),
            )
            return
        reaction_extractor = getattr(
            self, "_extract_reaction_expression_hidden_intent", None
        )
        if callable(reaction_extractor):
            cleaned_reaction_text, reaction_intent = reaction_extractor(
                recovered_text
            )
        else:
            cleaned_reaction_text, reaction_intent = recovered_text, {}
        response_has_tool_call = bool(getattr(resp, "tools_call_name", None))
        if response_has_tool_call:
            try:
                delattr(event, "_private_companion_reaction_expression_intent")
            except (AttributeError, TypeError):
                pass
        reaction_authorization_getter = getattr(
            self, "_reaction_expression_authorization", None
        )
        authorization = (
            reaction_authorization_getter(event)
            if callable(reaction_authorization_getter)
            else {}
        )
        reaction_visible_checker = getattr(
            self, "_reaction_expression_has_visible_text", None
        )
        reaction_visible_text = (
            reaction_visible_checker(cleaned_reaction_text)
            if callable(reaction_visible_checker)
            else bool(str(cleaned_reaction_text or "").strip())
        )
        reaction_runtime_logger = getattr(
            self, "_log_reaction_expression_event", None
        )
        reaction_scope_getter = getattr(self, "_reaction_expression_scope", None)
        reaction_scope = (
            reaction_scope_getter(event)
            if callable(reaction_scope_getter)
            else "unknown"
        )
        if cleaned_reaction_text != recovered_text:
            resp.completion_text = cleaned_reaction_text
            recovered_text = cleaned_reaction_text
            if (
                reaction_intent
                and authorization.get("authorized")
                and not authorization.get("consumed")
                and reaction_visible_text
                and not response_has_tool_call
            ):
                try:
                    setattr(
                        event,
                        "_private_companion_reaction_expression_intent",
                        reaction_intent,
                    )
                except Exception:
                    pass
                if callable(reaction_runtime_logger):
                    reaction_runtime_logger(
                        event,
                        stage="intent",
                        decision="accepted",
                        reason="intent_extracted",
                        scope=reaction_scope,
                    )
            elif reaction_intent and callable(reaction_runtime_logger):
                reaction_runtime_logger(
                    event,
                    stage="intent",
                    decision="discarded",
                        reason=(
                            "tool_call_intermediate"
                            if response_has_tool_call
                            else "intent_discarded"
                        ),
                        scope=reaction_scope,
                    )
        existing_reaction_intent = getattr(
            event, "_private_companion_reaction_expression_intent", None
        )
        if (
            authorization.get("authorized")
            and not authorization.get("consumed")
            and reaction_visible_text
            and not reaction_intent
            and not (
                isinstance(existing_reaction_intent, dict)
                and bool(existing_reaction_intent)
            )
            and not response_has_tool_call
            and not authorization.get("model_omission_recorded")
        ):
            authorization["model_omission_recorded"] = True
            authorization_setter = getattr(
                self, "_set_reaction_expression_authorization", None
            )
            if callable(authorization_setter):
                authorization_setter(event, authorization)
            runtime_notifier = getattr(self, "_note_reaction_expression_runtime", None)
            if callable(runtime_notifier):
                runtime_notifier(
                    model_omissions=1,
                    last_reason="model_omitted_intent",
                )
            if callable(reaction_runtime_logger):
                reaction_runtime_logger(
                    event,
                    stage="intent",
                    decision="omit",
                    reason="model_omitted_intent",
                    scope=authorization.get("scope") or reaction_scope,
                )
            fallback_builder = getattr(self, "_reaction_expression_local_fallback_intent", None)
            fallback_intent = (
                fallback_builder(event, cleaned_reaction_text, authorization)
                if callable(fallback_builder)
                else {}
            )
            if fallback_intent:
                try:
                    setattr(
                        event,
                        "_private_companion_reaction_expression_intent",
                        fallback_intent,
                    )
                except Exception:
                    pass
                if callable(runtime_notifier):
                    runtime_notifier(
                        local_fallbacks=1,
                        last_reason="local_fallback_intent",
                    )
                if callable(reaction_runtime_logger):
                    reaction_runtime_logger(
                        event,
                        stage="intent",
                        decision="accepted",
                        reason="local_fallback_intent",
                        scope=authorization.get("scope") or reaction_scope,
                    )
        sent_photo_caption = str(
            getattr(event, "_private_companion_photo_tool_sent_caption", "") or ""
        ).strip()
        if (
            bool(getattr(event, "_private_companion_photo_tool_sent", False))
            and PHOTO_TOOL_SILENT_SENTINEL in recovered_text
        ):
            try:
                resp.result_chain = None
            except Exception:
                pass
            resp.completion_text = ""
            original_text = ""
            recovered_text = ""
            logger.info(
                "已清除图片工具成功发送后的内部静默标记: session=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            )
        if (
            bool(getattr(event, "_private_companion_photo_tool_sent", False))
            and self._photo_tool_followup_is_redundant(sent_photo_caption, recovered_text)
        ):
            try:
                resp.result_chain = None
            except Exception:
                pass
            resp.completion_text = ""
            original_text = ""
            recovered_text = ""
            logger.info(
                "已移除生图工具成功发送后的重复承接正文: session=%s caption=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                _single_line(sent_photo_caption, 120),
            )
        pending_tool_text = str(
            getattr(event, "_private_companion_same_session_tool_text", "") or ""
        ).strip()
        tool_names = getattr(resp, "tools_call_name", None)
        has_tool_call = bool(tool_names) if isinstance(tool_names, (list, tuple, set, str)) else False
        if (
            not same_session_tool_call
            and pending_tool_text
            and not has_tool_call
            and not bool(getattr(event, "_private_companion_same_session_tool_finalized", False))
        ):
            # A same-session tool call already contains the intended visible
            # message. Use it once as the final assistant response instead of
            # sending the tool payload and then repeating it here.
            try:
                resp.result_chain = None
            except Exception:
                pass
            resp.completion_text = pending_tool_text
            recovered_text = pending_tool_text
            try:
                setattr(event, "_private_companion_same_session_tool_finalized", True)
            except Exception:
                pass
            logger.info(
                "已将同会话工具文本恢复为唯一最终回复: session=%s text=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                _single_line(pending_tool_text, 160),
            )
        called_names = getattr(resp, "tools_call_name", None)
        creative_tool_called = bool(
            (isinstance(called_names, str) and called_names.strip() == "pc_view_creative_work")
            or (
                isinstance(called_names, (list, tuple, set))
                and "pc_view_creative_work" in {str(item) for item in called_names}
            )
        )
        if creative_tool_called:
            try:
                setattr(event, "private_companion_creative_work_tool_attempted", True)
            except Exception:
                pass
        guarded_text = self._guard_unread_creative_work_response(event, recovered_text)
        if guarded_text != recovered_text:
            resp.completion_text = guarded_text
        original_text = guarded_text
        if self._proactive_only_blocks_passive_event(event, "enable_tts_enhancement"):
            return
        normalized_text = _normalize_outbound_punctuation_flow(original_text)
        if normalized_text and normalized_text != original_text:
            resp.completion_text = normalized_text
        await self.protect_tts_enhancement_response_blocks(event, resp)

    @filter.on_llm_response()
    @_multi_persona_event_context
    async def record_external_llm_token_usage(self, event: AstrMessageEvent, resp: LLMResponse, *args, **kwargs):
        """统计非插件内部调用的 AstrBot 主回复 Token，单独展示且不计入插件限额。"""
        if self is None or not self.enabled:
            return
        if self._proactive_only_blocks_passive_event(event, "llm_request"):
            return
        if bool(getattr(event, "private_companion_skip_external_token_stats", False)):
            return
        prompt = str(getattr(event, "private_companion_external_token_prompt", "") or "")
        started = _safe_float(getattr(event, "private_companion_external_token_start", 0), 0)
        completion = self._completion_text_for_token_stats(resp)
        response_tool_names = getattr(resp, "tools_call_name", None) if resp is not None else None
        if isinstance(response_tool_names, str):
            has_tool_call = bool(response_tool_names.strip())
        elif isinstance(response_tool_names, (list, tuple, set)):
            has_tool_call = any(str(item or "").strip() for item in response_tool_names)
        else:
            has_tool_call = False
        if not prompt and not completion and resp is None:
            return
        umo = str(getattr(event, "unified_msg_origin", "") or "")
        try:
            sender_id = str(event.get_sender_id())
        except Exception:
            sender_id = ""
        resp_id = _single_line(getattr(resp, "id", ""), 120) if resp is not None else ""
        usage = getattr(resp, "usage", None) if resp is not None else None
        usage_total = _safe_int(getattr(usage, "total", 0), 0)
        trigger_message_id = self._event_message_id(event)
        completion_sig = hashlib.sha1(
            completion[:4000].encode("utf-8", errors="ignore")
        ).hexdigest()[:16] if completion else ""
        record_key = "|".join(
            (
                resp_id,
                umo,
                sender_id,
                trigger_message_id,
                str(usage_total),
                str(len(prompt)),
                str(len(completion)),
                completion_sig,
            )
        )
        try:
            recorded_keys = getattr(event, "private_companion_external_token_recorded_keys", None)
            if not isinstance(recorded_keys, set):
                recorded_keys = set()
                setattr(event, "private_companion_external_token_recorded_keys", recorded_keys)
            if record_key in recorded_keys:
                return
            recorded_keys.add(record_key)
        except Exception:
            pass
        try:
            is_private_chat = bool(getattr(event, "is_private_chat", lambda: False)())
            task = "astrbot_private_reply" if is_private_chat else "astrbot_group_reply"
        except Exception:
            is_private_chat = False
            task = "astrbot_reply"
        provider_id = self._provider_id_from_llm_response(resp) or self._default_chat_provider_id(umo)
        self._record_external_llm_usage(
            provider_id=provider_id,
            task=task,
            prompt=prompt,
            completion=completion,
            elapsed_ms=int(max(0.0, time.time() - started) * 1000) if started > 0 else 0,
            success=bool(completion or has_tool_call),
            error="" if completion or has_tool_call else "empty_response",
            resp=resp,
            session_id=umo,
            sender_id=sender_id,
            message_type="private" if is_private_chat else "group",
        )

    @filter.on_llm_response()
    @_multi_persona_event_context
    async def record_group_expression_rule_usage(self, event: AstrMessageEvent, resp: LLMResponse, *args, **kwargs):
        """记录群聊中实际进入主回复链的已审核语义表达规则。"""
        if self is None or not self.enabled or bool(getattr(event, "is_private_chat", lambda: False)()):
            return
        semantic_rules = getattr(event, "private_companion_semantic_expression_rules", None)
        if not isinstance(semantic_rules, list) or not semantic_rules:
            return
        if bool(getattr(event, "private_companion_group_semantic_usage_recorded", False)):
            return
        completion = _single_line(getattr(resp, "completion_text", ""), 500)
        if not completion:
            return
        # Rule usage is committed by the confirmed-delivery finalizer. Keep this
        # hook read-only so an LLM response that is later dropped cannot mutate
        # durable expression-learning state.
        try:
            setattr(event, "private_companion_group_semantic_usage_pending", True)
        except Exception:
            pass

    @filter.on_llm_response()
    @_multi_persona_event_context
    async def capture_llm_timer_directive(self, event: AstrMessageEvent, resp: LLMResponse, *args, **kwargs):
        """LLM 回复后捕获定时/状态指令，并做私聊回复审校。"""
        release_now = False
        try:
            if self is None or not self.enabled:
                release_now = True
                return
            if bool(getattr(event, "private_companion_proactive_framework", False)):
                return
            if self._proactive_only_blocks_passive_event(event, "enable_llm_timer_scheduling"):
                release_now = True
                return
            if not bool(getattr(event, "is_private_chat", lambda: False)()):
                return
            original_text = str(resp.completion_text or "").strip()
            if not original_text:
                if bool(getattr(event, "_private_companion_plaintext_photo_sent", False)):
                    self._stop_passive_input_status_loop(event)
                    release_now = True
                    return
                self._stop_passive_input_status_loop(event)
                self._record_passive_no_reply(
                    event,
                    source="主链回复",
                    reason="LLM 返回空回复",
                    level="warn",
                )
                release_now = True
                return
            try:
                user_id = str(event.get_sender_id())
            except Exception:
                self._stop_passive_input_status_loop(event)
                release_now = True
                return
            resolver = getattr(self, "_private_user_id_for_event", None)
            if callable(resolver):
                user_id = resolver(event, user_id)
            raw_users = self.data.get("users", {})
            current_user = raw_users.get(user_id) if isinstance(raw_users, dict) else None
            if not isinstance(current_user, dict):
                self._stop_passive_input_status_loop(event)
                release_now = True
                return
            working_text = original_text
            reply_image_count = _safe_int(getattr(event, "private_companion_reply_image_count", 1), 1, 1, 5)
            reply_image_vision = _single_line(
                getattr(event, "private_companion_reply_image_vision_text", ""),
                self._private_image_vision_text_limit(reply_image_count),
            )
            reply_image_user_text = _single_line(
                getattr(event, "private_companion_reply_image_user_text", "") or current_user.get("last_user_message"),
                260,
            )
            if (
                reply_image_vision
                and bool(getattr(event, "private_companion_reply_image_content_question", False))
                and self._private_image_reply_misses_content_question(working_text)
            ):
                corrected = self._private_image_content_answer_from_vision(
                    reply_image_vision,
                    user_text=reply_image_user_text,
                )
                if corrected:
                    logger.info(
                        "私聊引用图片回复疑似被历史话题污染,已按视觉摘要纠偏: user=%s before=%s after=%s",
                        user_id,
                        _single_line(working_text, 120),
                        _single_line(corrected, 160),
                    )
                    working_text = corrected
                    resp.completion_text = corrected
            if self.enable_llm_timer_scheduling and "<timer" in original_text.lower():
                cleaned_text, payloads = self._extract_timer_directives(original_text)
                if cleaned_text != original_text:
                    working_text = cleaned_text
                    resp.completion_text = working_text
                if payloads:
                    timer_source_text = _single_line(current_user.get("last_user_message"), 260) or working_text
                    await self._schedule_llm_timer_after_response_dedup(
                        event,
                        resp,
                        user_id,
                        payloads[-1],
                        source_text=timer_source_text,
                        visible_text=working_text,
                        trigger_message_id=self._event_message_id(event),
                        trigger_umo=str(getattr(event, "unified_msg_origin", "") or ""),
                    )

            inbound_text = _single_line(current_user.get("last_user_message"), 260)
            sanitized_elapsed_text = self._sanitize_unverified_repeat_elapsed_claim(
                inbound_text,
                working_text,
                current_user,
            )
            sanitized_elapsed_text = self._sanitize_robotic_topic_choice_after_repeat_correction(
                inbound_text,
                sanitized_elapsed_text,
            )
            if sanitized_elapsed_text != working_text:
                logger.info(
                    "已清理重复纠正后的生硬回复: user=%s before=%s after=%s",
                    user_id,
                    _single_line(working_text, 120),
                    _single_line(sanitized_elapsed_text, 120),
                )
                working_text = sanitized_elapsed_text
                resp.completion_text = working_text
            music_album_context = getattr(event, "private_companion_reply_music_album_context", None)
            silence_decision = await self._decide_smart_silence(
                inbound_text=inbound_text,
                response_text=working_text,
                user=current_user,
                session_kind="private",
            )
            if str(silence_decision.get("decision") or "") == "silent":
                setattr(event, "_private_companion_smart_silence_drop", True)
                setattr(event, "_private_companion_smart_silence_reason", _single_line(silence_decision.get("reason"), 120))
                resp.completion_text = ""
                async with self._data_lock:
                    current = self._get_user(user_id)
                    stats = current.setdefault("postprocess_stats", {})
                    if not isinstance(stats, dict):
                        stats = {}
                        current["postprocess_stats"] = stats
                    stats["smart_silence"] = _safe_int(stats.get("smart_silence"), 0, 0) + 1
                    stats["last_smart_silence_at"] = self._environment_now().strftime("%Y-%m-%d %H:%M")
                    self._save_data_sync(sections={"users"})
                logger.info(
                    "智能沉默已取消本轮私聊回复: user=%s reason=%s inbound=%s reply=%s",
                    user_id,
                    _single_line(silence_decision.get("reason"), 120),
                    _single_line(inbound_text, 120),
                    _single_line(working_text, 140),
                )
                self._record_passive_no_reply(
                    event,
                    source="智能沉默",
                    reason=_single_line(silence_decision.get("reason"), 120) or "用户边界语义触发静默",
                    detail=inbound_text,
                    reply_preview=working_text,
                    level="info",
                )
                release_now = True
                return
            reviewed_text = await self._review_and_rewrite_response(
                current_user,
                inbound_text,
                working_text,
                music_album_context=music_album_context if isinstance(music_album_context, dict) else None,
                creative_context=str(getattr(event, "private_companion_creative_reply_context", "") or ""),
                review_event=event,
            )
            if self._passive_response_review_enabled() and self._is_response_review_drop_marker(reviewed_text):
                setattr(event, "_private_companion_response_review_drop", True)
                resp.completion_text = ""
                async with self._data_lock:
                    current = self._get_user(user_id)
                    stats = current.setdefault("postprocess_stats", {})
                    if not isinstance(stats, dict):
                        stats = {}
                        current["postprocess_stats"] = stats
                    stats["duplicate_dropped"] = _safe_int(stats.get("duplicate_dropped"), 0, 0) + 1
                    stats["last_duplicate_dropped_at"] = self._environment_now().strftime("%Y-%m-%d %H:%M")
                    self._save_data_sync(sections={"users"})
                logger.info(
                    "回复复核已取消重复私聊回复: user=%s inbound=%s reply=%s",
                    user_id,
                    _single_line(inbound_text, 120),
                    _single_line(working_text, 160),
                )
                self._record_passive_no_reply(
                    event,
                    source="回复复核去重",
                    reason="最终回复与上一条 Bot 消息重复",
                    detail=inbound_text,
                    reply_preview=working_text,
                    level="info",
                )
                release_now = True
                return
            if reviewed_text != working_text:
                resp.completion_text = reviewed_text
                working_text = reviewed_text
                async with self._data_lock:
                    current = self._get_user(user_id)
                    stats = current.setdefault("postprocess_stats", {})
                    if not isinstance(stats, dict):
                        stats = {}
                        current["postprocess_stats"] = stats
                    stats["rewritten"] = _safe_int(stats.get("rewritten"), 0, 0) + 1
                    stats["last_rewritten_at"] = self._environment_now().strftime("%Y-%m-%d %H:%M")
                    self._save_data_sync(sections={"users"})

            async with self._data_lock:
                live_user_for_duplicate = self._get_user(user_id)
            if self._passive_response_review_enabled() and self._effective_passive_review_strength() != "lenient":
                should_drop_duplicate, duplicate_reason = self._should_drop_duplicate_reply_text(live_user_for_duplicate, inbound_text, working_text)
            else:
                should_drop_duplicate, duplicate_reason = False, ""
            if should_drop_duplicate:
                setattr(event, "_private_companion_response_review_drop", True)
                resp.completion_text = ""
                async with self._data_lock:
                    current = self._get_user(user_id)
                    stats = current.setdefault("postprocess_stats", {})
                    if not isinstance(stats, dict):
                        stats = {}
                        current["postprocess_stats"] = stats
                    stats["duplicate_dropped"] = _safe_int(stats.get("duplicate_dropped"), 0, 0) + 1
                    stats["last_duplicate_dropped_at"] = self._environment_now().strftime("%Y-%m-%d %H:%M")
                    self._save_data_sync(sections={"users"})
                logger.info(
                    "发送前去重已取消重复私聊回复: user=%s reason=%s inbound=%s reply=%s",
                    user_id,
                    _single_line(duplicate_reason, 120),
                    _single_line(inbound_text, 120),
                    _single_line(working_text, 160),
                )
                self._record_passive_no_reply(
                    event,
                    source="回复复核去重",
                    reason=duplicate_reason or "最终回复与上一条 Bot 消息重复",
                    detail=inbound_text,
                    reply_preview=working_text,
                    level="info",
                )
                release_now = True
                return

            if working_text != original_text:
                self._schedule_reply_interception_forward(
                    "rewrite",
                    source="私聊回复处理",
                    reason="回复在发送前经过纠偏、清理或复核改写",
                    source_session=_single_line(getattr(event, "unified_msg_origin", ""), 180),
                    inbound=inbound_text,
                    before=original_text,
                    after=working_text,
                )
        except Exception:
            release_now = True
            raise
        finally:
            pass

    async def _debug_prompt_text(self, kind: str, user: dict[str, Any], event: AstrMessageEvent | None = None) -> str:
        normalized = str(kind or "").strip().lower()
        await self._ensure_weather_context()
        if normalized in {"日程", "plan", "daily_plan"}:
            memory_companion_context = ""
            memory_companion_context_getter = getattr(self, "_memory_companion_compose_schedule_context", None)
            if callable(memory_companion_context_getter):
                memory_companion_context = await memory_companion_context_getter(kind="daily_plan", max_chars=1300)
            return self._build_daily_plan_prompt(
                self._environment_now().strftime("%Y-%m-%d %H:%M"),
                memory_companion_context=memory_companion_context,
            )
        if normalized in {"细化", "detail", "enhancement"}:
            plan = dict(self.data.get("daily_plan", {}))
            state = dict(self.data.get("daily_state", {}))
            enhanced = self.data.get("detail_enhanced_segments", {})
            if not isinstance(enhanced, dict):
                enhanced = {}
            segment = self._current_detail_segment_for_update() or self._pick_detail_segment(plan, enhanced)
            if not segment:
                current_item = self._get_current_plan_item(plan)
                if not isinstance(current_item, dict):
                    return "当前没有可用于细化的日程段。先生成日程,并等到某个时间段临近,或让当天有当前日程项。"
                start = self._parse_hhmm_to_minutes(current_item.get("time")) or self._environment_now_minutes()
                segment = {
                    "start": start,
                    "end": min(24 * 60, start + 120),
                    "item": current_item,
                }
            memory_companion_context = ""
            memory_companion_context_getter = getattr(self, "_memory_companion_compose_schedule_context", None)
            if callable(memory_companion_context_getter):
                memory_companion_context = await memory_companion_context_getter(
                    kind="detail",
                    segment=segment,
                    plan=plan,
                    state=state,
                    max_chars=1100,
                )
            return self._build_detail_enhancement_prompt(
                segment,
                plan,
                state,
                memory_companion_context=memory_companion_context,
            )
        if normalized in {"主动", "proactive"}:
            name = str(user.get("nickname") or runtime_persona_setting(self, 'default_nickname', '你'))
            planned_reason = str(user.get("planned_proactive_reason") or "")
            planned_action = str(user.get("planned_proactive_action") or "message")
            planned_motive = _single_line(user.get("planned_proactive_motive"), 140)
            reason = planned_reason if planned_reason and self._is_reason_allowed_now(planned_reason, user) else ""
            if not reason:
                reason, _ = self._choose_proactive_message(user, name, planned_reason)
                planned_motive = self._choose_proactive_motive(reason, user, action=planned_action)
            planned_topic = _single_line(user.get("planned_proactive_topic"), 48)
            framework_prompt = await self._build_framework_proactive_prompt(
                user=user,
                name=name,
                reason=reason,
                action=planned_action,
                action_context="（调试预览：这里会放工具结果或观察结果）",
                motive=planned_motive,
            )
            sections = [
                prompt_section(
                    key="debug.proactive.description",
                    title="说明",
                    source="main",
                    content=(
                        "当前主动消息已改为走 AstrBot 框架唤醒链。\n"
                        "人格、历史对话和会话上下文不再在这里手工重复拼接,而是由框架根据当前 conversation 自动注入。"
                    ),
                )
            ]
            if planned_topic:
                sections.append(
                    prompt_section(
                        key="debug.proactive.topic",
                        title="内部话题钩子",
                        source="main",
                        content=planned_topic,
                    )
                )
            sections.append(
                prompt_section(
                    key="debug.proactive.framework_prompt",
                    title="送入框架的任务提示",
                    source="main",
                    content=framework_prompt,
                )
            )
            return render_prompt_sections(
                sections,
                mode=PromptRenderMode.LABELED_BLOCK,
            )
        if normalized in {"回复注入", "reply", "injection"}:
            await self._refresh_default_persona_prompt(getattr(event, "unified_msg_origin", "") if event is not None else "")
            state = await self._ensure_daily_state()
            parts = [self._format_state_injection(state)]
            life_context = self._format_life_context_injection()
            if life_context:
                parts.append(life_context)
            important_dates = self._format_important_dates_injection()
            if important_dates:
                parts.append(important_dates)
            memo_notes = self._format_memo_notes_injection()
            if memo_notes:
                parts.append(memo_notes)
            detail_injection = self._format_detail_injection()
            if detail_injection:
                parts.append(detail_injection)
            return "\n\n".join(parts)
        return "可查看的提示词类型：日程 / 细化 / 主动 / 回复注入"

    def _should_skip_recent_outfit_command_send(
        self,
        event: AstrMessageEvent,
        *,
        text: str,
        image_path: str,
        ttl_seconds: float = 30.0,
    ) -> bool:
        cache = getattr(self, "_recent_outfit_command_sends", None)
        if not isinstance(cache, dict):
            cache = {}
            self._recent_outfit_command_sends = cache
        now = _now_ts()
        ttl = max(1.0, float(ttl_seconds or 30.0))
        for key, ts in list(cache.items()):
            if now - _safe_float(ts, 0.0) > ttl:
                cache.pop(key, None)
        try:
            scope = self._event_scope_key(event)
        except Exception:
            scope = _single_line(getattr(event, "unified_msg_origin", ""), 160) or "unknown"
        signature = hashlib.sha1(
            f"{scope}|daily_outfit_photo|{text}|{image_path}".encode("utf-8", errors="ignore")
        ).hexdigest()[:20]
        last_at = _safe_float(cache.get(signature), 0.0)
        if last_at and now - last_at <= ttl:
            logger.info(
                "已跳过重复的每日穿搭命令发图: scope=%s image=%s age=%.1fs",
                _single_line(scope, 120),
                _single_line(image_path, 160),
                now - last_at,
            )
            return True
        cache[signature] = now
        return False

    @filter.command("陪伴", alias={"私聊陪伴", "主动陪伴"})
    @_multi_persona_event_context
    async def companion_command(self, event: AstrMessageEvent):
        """管理私聊陪伴状态、日程、记忆、风格、重要日期和可选外部动作。"""
        if self is None:
            return
        try:
            is_private = bool(event.is_private_chat())
        except Exception:
            is_private = False
        raw_command_text = str(getattr(event, "message_str", "") or "")
        # Some adapters (notably QQ official) preserve the slash while others
        # strip the registered command token before invoking the handler. Keep
        # both forms equivalent so bootstrap commands do not fall back to help.
        command_text = raw_command_text.replace("\u3000", " ").replace("／", "/").strip()
        if command_text.startswith("/"):
            command_text = command_text[1:].lstrip()
        bootstrap_args = command_text.split(maxsplit=2)
        bootstrap_action = bootstrap_args[1].strip() if len(bootstrap_args) >= 2 else ""
        bootstrap_value = bootstrap_args[2].strip() if len(bootstrap_args) >= 3 else ""
        if len(bootstrap_args) == 1 and bootstrap_args[0] in {
            "绑定主动消息", "绑定主动会话", "绑定会话",
            "查看主动路由", "查看主动绑定", "主动路由", "主动绑定",
            "解绑主动消息", "解绑主动会话", "解绑会话",
        }:
            bootstrap_action = bootstrap_args[0]
        bootstrap_normalizer = getattr(self, "_normalize_companion_command_action", None)
        if callable(bootstrap_normalizer):
            bootstrap_action, _ = bootstrap_normalizer(
                bootstrap_action,
                bootstrap_value,
            )
        private_delivery_bind_actions = {"绑定主动消息", "绑定主动会话", "绑定会话"}
        is_private_delivery_bootstrap = bootstrap_action in private_delivery_bind_actions
        if is_private:
            raw_user_id = str(event.get_sender_id() or "").strip()
            identity_normalizer = getattr(self, "_normalize_private_identity_id", None)
            user_id = identity_normalizer(raw_user_id) if callable(identity_normalizer) else raw_user_id
            user_id = user_id or raw_user_id
            sender_name_reader = getattr(self, "_sender_display_name", None)
            if callable(sender_name_reader):
                sender_display_name = _single_line(sender_name_reader(event), 40)
            else:
                sender_display_name = _single_line(user_id, 40)
            async with self._data_lock:
                private_user, _ = self._ensure_auto_private_user_profile(
                    event,
                    user_id=user_id,
                    sender_display_name=sender_display_name,
                    now=_now_ts(),
                )
                if isinstance(private_user, dict):
                    user_id = _single_line(private_user.get("user_id"), 160) or user_id
                migrator = getattr(self, "_req036_migrate_configured_target_capability", None)
                if callable(migrator):
                    migrator(user_id, private_user)
                self._req036_attach_unified_profile_context(
                    event,
                    user=private_user if isinstance(private_user, dict) else None,
                    source="private_command",
                )
                self._schedule_data_save(sections={"users", "unified_person"})
        self._qzone_note_event_bot(event)
        raw_text = str(event.message_str or "")
        normalized_text = raw_text.replace("\u3000", " ").replace("／", "/").strip()
        if normalized_text.startswith("/"):
            normalized_text = normalized_text[1:].lstrip()
        args = normalized_text.split(maxsplit=2)
        action = args[1].strip() if len(args) >= 2 else "帮助"
        value = args[2].strip() if len(args) >= 3 else ""
        if len(args) == 1 and args[0] in {
            "绑定主动消息", "绑定主动会话", "绑定会话",
            "查看主动路由", "查看主动绑定", "主动路由", "主动绑定",
            "解绑主动消息", "解绑主动会话", "解绑会话",
        }:
            action, value = args[0], ""
        action, value = self._normalize_companion_command_action(action, value)
        companion_manual_query_actions = {"答疑", "排障", "诊断", "说明"}
        companion_manual_confirm_actions = {"答疑确认", "排障确认", "诊断确认", "应用答疑建议", "应用建议"}
        companion_manual_cancel_actions = {"答疑取消", "排障取消", "诊断取消", "取消答疑建议", "取消建议"}
        companion_manual_setting_actions = {"答疑设置", "排障设置", "诊断设置", "答疑修改", "排障修改", "诊断修改"}
        daily_outfit_view_actions = {"今日穿搭图", "今日穿搭", "查看穿搭图", "查看穿搭", "穿搭图", "每日穿搭图", "每日穿搭", "当前穿搭图", "当前穿搭", "展示穿搭图"}
        wardrobe_command_actions = {"衣柜", "衣橱", "wardrobe", "角色衣柜", "服装库"}
        daily_outfit_generate_actions = {
            "生成穿搭", "刷新穿搭", "重置穿搭",
            "生成穿搭图", "刷新穿搭图", "重置穿搭图",
            "重新生成穿搭", "重新生成穿搭图", "重生穿搭", "重生穿搭图",
            "生成今日穿搭", "生成今日穿搭图", "生成每日穿搭", "生成每日穿搭图",
        }
        photo_command_actions = {"生图", "画图", "绘图", "生成图片", "出图", "自拍", "拍照", "拍一张", "改图", "修图", "重绘", "P图", "p图"}
        daily_schedule_regenerate_actions = {"重置日程", "生成日程", "刷新日程", "重新生成日程"}
        daily_schedule_cancel_actions = {"删除日程", "取消日程", "移除日程"}
        image_api_status_actions = {"查看生图API", "查看生图api", "生图API状态", "生图api状态", "在线生图API", "在线生图api", "生图接口"}
        image_api_swap_actions = {
            "切换生图API", "切换生图api", "交换生图API", "交换生图api",
            "切换在线生图API", "切换在线生图api", "交换在线生图API", "交换在线生图api",
            "切换图片API", "切换图片api", "交换图片API", "交换图片api",
            "切换备用生图", "启用备用生图", "使用备用生图", "切到备用生图",
            "切换备选生图", "启用备选生图", "使用备选生图", "切到备选生图",
        }
        qweather_location_bind_actions = {"绑定城市", "设置城市"}
        qweather_location_view_actions = {"查看城市", "当前城市", "天气城市"}
        qweather_location_unbind_actions = {"解绑城市", "清除城市"}
        qweather_location_actions = {
            *qweather_location_bind_actions,
            *qweather_location_view_actions,
            *qweather_location_unbind_actions,
        }
        wakeup_alarm_actions = {"现实触及", "现实触及闹钟", "现实触及起床", "起床闹钟", "起床提醒", "蓝牙起床", "蓝牙闹钟"}
        private_delivery_view_actions = {"查看主动路由", "查看主动绑定", "主动路由", "主动绑定"}
        private_delivery_unbind_actions = {"解绑主动消息", "解绑主动会话", "解绑会话"}
        private_delivery_actions = {
            *private_delivery_bind_actions,
            *private_delivery_view_actions,
            *private_delivery_unbind_actions,
        }
        tts_language_actions = {"TTS语种", "tts语种", "语音语种", "TTS", "tts"}
        if action in companion_manual_query_actions:
            inline_value = value.strip()
            if inline_value in {"确认", "应用", "执行", "确认执行", "应用建议"}:
                action = "答疑确认"
                value = ""
            elif inline_value in {"取消", "取消建议", "放弃"}:
                action = "答疑取消"
                value = ""
            else:
                inline_parts = inline_value.split(maxsplit=1)
                if len(inline_parts) >= 2 and inline_parts[0] in {"设置", "修改", "set", "Set", "SET"}:
                    action = "答疑设置"
                    value = inline_parts[1].strip()
                elif re.search(r"^[A-Za-z_][A-Za-z0-9_]*\s*(?:=|:|：|设为|设置为|改成|调到)\s*\S+", inline_value):
                    action = "答疑设置"
                    value = inline_value
                else:
                    maybe_key, maybe_value = self._companion_manual_parse_setting_text(inline_value)
                    if maybe_key and maybe_value:
                        ok, _, _ = self._companion_manual_normalize_config_value(maybe_key, maybe_value)
                        if ok:
                            action = "答疑设置"
                            value = inline_value
        bookshelf_password_reset_actions = {
            "重置夹层密码", "重设夹层密码", "重新生成夹层密码", "刷新夹层密码", "生成夹层密码",
            "重置资料柜密码", "重设资料柜密码", "重新生成资料柜密码", "刷新资料柜密码", "生成资料柜密码",
        }
        bookshelf_password_output_actions = {
            "输出夹层密码", "强制输出夹层密码", "查看夹层密码", "显示夹层密码",
            "输出资料柜密码", "强制输出资料柜密码", "查看资料柜密码", "显示资料柜密码",
            "输出抽屉密码", "查看抽屉密码", "显示抽屉密码",
        }
        bookshelf_password_value_actions = {"强制输出", "输出", "查看密码", "查看", "显示"}
        bookshelf_password_value_targets = {"夹层密码", "资料柜密码", "抽屉密码", "资料柜暗格", "夹层", "资料柜"}
        bookshelf_password_output_requested = (
            action in bookshelf_password_output_actions
            or (
                action in bookshelf_password_value_actions
                and _single_line(value, 24) in bookshelf_password_value_targets
            )
        )
        response_image_path = ""
        response_extra_components: list[Any] = []
        deferred_actions = {
            "重置当前人格", "当前人格重置", "重置人格",
            "重置插件", "全部重置",
            "查看提示词", "提示词", "prompt",
            "重置细化",
            *daily_schedule_regenerate_actions,
            *daily_schedule_cancel_actions,
            *daily_outfit_generate_actions,
            "生成状态", "刷新状态", "重生状态",
            "增添状态", "添加状态",
            "生成日记", "刷新日记",
            "梦境", "做了什么梦", "今日梦境",
            *bookshelf_password_reset_actions,
            "发说说", "发QQ空间", "发布说说", "空间发布", "发布空间",
            "测试说说链路", "测试空间发布", "测试QQ空间发布", "测试qzone发布",
            "测试说说配图", "测试空间配图", "测试QQ空间配图", "测试qzone配图",
            "新闻", "今日新闻", "AI新闻", "ai新闻", "AI日报", "ai日报", "日报", "AI早报", "ai早报", "早报",
            *companion_manual_query_actions,
            *photo_command_actions,
            *image_api_swap_actions,
            *qweather_location_actions,
        }

        is_private = bool(getattr(event, "is_private_chat", lambda: False)())
        public_safe_actions = {
            *companion_manual_query_actions,
            *companion_manual_confirm_actions,
            *companion_manual_cancel_actions,
            *companion_manual_setting_actions,
            *daily_outfit_view_actions,
            *tts_language_actions,
            *wakeup_alarm_actions,
        }
        if action in private_delivery_actions and not is_private:
            await self._reply(event, "请在需要接收主动消息的私聊窗口执行这个指令。")
            event.stop_event()
            return
        if action in wakeup_alarm_actions and not is_private:
            await self._reply(event, "现实触及只在私聊窗口设置，避免群聊误触发本机播放。")
            event.stop_event()
            return
        if action in qweather_location_actions and not self._can_manage_sensitive_location(event):
            await self._reply(event, self._sensitive_location_denied_text())
            event.stop_event()
            return
        if runtime_persona_setting(self, 'require_private_opt_in', True) and not is_private and action not in public_safe_actions:
            await self._reply(event, self._private_only_text())
            event.stop_event()
            return

        management_actions = {
            "重置当前人格", "当前人格重置", "重置人格",
            "重置插件", "全部重置",
            "查看提示词", "提示词", "prompt",
            "重置细化", *daily_schedule_regenerate_actions, *daily_schedule_cancel_actions,
            *daily_outfit_generate_actions,
            "生成状态", "刷新状态", "重生状态",
            "增添状态", "添加状态",
            "生成日记", "刷新日记",
            *bookshelf_password_reset_actions,
            *bookshelf_password_output_actions,
            "发说说", "发QQ空间", "发布说说", "空间发布", "发布空间",
            "测试说说链路", "测试空间发布", "测试QQ空间发布", "测试qzone发布",
            "测试说说配图", "测试空间配图", "测试QQ空间配图", "测试qzone配图",
            "新闻", "今日新闻", "AI新闻", "ai新闻", "AI日报", "ai日报", "日报", "AI早报", "ai早报", "早报",
            *tts_language_actions,
            "撤回消息", "防撤回", "转述撤回", "撤回转述",
            "日期添加", "添加日期", "重要日期添加",
            "日期删除", "删除日期", "重要日期删除",
            "话头删除", "删除话头", "未完话头删除", "删除未完话头",
            "清空记忆", "忘记我",
            "参考图", "人设参考图", "自拍参考图", "参考图库",
            *image_api_status_actions,
            *image_api_swap_actions,
            *qweather_location_actions,
        }
        if (action in management_actions or bookshelf_password_output_requested) and not self._can_manage_private_companion(event):
            await self._reply(event, self._management_denied_text())
            event.stop_event()
            return

        raw_user_id = str(event.get_sender_id() or "").strip()
        resolver = getattr(self, "_private_user_id_for_event", None)
        canonicalizer = getattr(self, "_canonical_private_user_id", None)
        identity_normalizer = getattr(self, "_normalize_private_identity_id", None)
        fallback_user_id = (
            identity_normalizer(raw_user_id)
            if callable(identity_normalizer)
            else raw_user_id
        ) or raw_user_id
        user_id = (
            resolver(event, raw_user_id)
            if callable(resolver)
            else canonicalizer(fallback_user_id) if callable(canonicalizer) else fallback_user_id
        )
        user_id = _single_line(user_id, 160) or raw_user_id
        wakeup_test_requested: Any = False
        async with self._data_lock:
            user = self._get_user(user_id)
            stamper = getattr(self, "_stamp_private_event_identity", None)
            if is_private and callable(stamper):
                stamper(user, event, raw_user_id)
            self._note_private_user_umo(user_id, user, event.unified_msg_origin)

            if action in private_delivery_bind_actions:
                changed, response = self._bind_private_delivery_umo(user_id, user, event.unified_msg_origin)
                if changed:
                    self._save_data_sync(sections={"users"})
            elif action in private_delivery_view_actions:
                response = self._format_private_delivery_binding_status(user_id, user)
            elif action in private_delivery_unbind_actions:
                changed, response = self._unbind_private_delivery_umo(user)
                if changed:
                    self._save_data_sync(sections={"users"})
            elif action in wakeup_alarm_actions:
                camera_command_requested = bool(
                    re.sub(r"\s+", "", str(value or "")).lower().startswith(
                        ("摄像头", "确认摄像头", "读取摄像头", "测试摄像头", "撤销摄像头", "取消摄像头")
                    )
                )
                if camera_command_requested and not self._reality_touch_camera_user_eligible(user_id):
                    response = "主机摄像头只允许 AstrBot 管理员或主要用户本人授权和使用。"
                    wakeup_test_requested = False
                else:
                    response, wakeup_test_requested = self._wakeup_alarm_command(user, value)
                enabled_getter = getattr(self, "_reality_companion_enabled", None)
                feature_enabled = bool(callable(enabled_getter) and enabled_getter())
                if not feature_enabled:
                    wakeup_test_requested = False
                    response += "\n现实触及联动插件未启用，请在“我会来到你身边”配置中开启总开关。"
            elif action in {"状态", "status"}:
                self._reset_daily_counter_if_needed(user)
                last_seen = self._format_timestamp_elapsed(self._latest_user_activity_ts(user))
                last_sent = self._format_timestamp_elapsed(user.get("last_sent"))
                plan = self.data.get("daily_plan", {})
                plan_text = self._format_plan_status_summary(plan if isinstance(plan, dict) else {})
                state = self.data.get("daily_state", {})
                state_text = (
                    f"{state.get('date')}｜能量 {state.get('energy', 70)}/100｜情绪偏{state.get('mood_bias', '平稳')}"
                    if state else "未生成"
                )
                simulation_text = self._format_simulation_summary(user)
                response = "".join(
                    [
                        "运行模式：默认开启\n",
                        f"称呼：{user.get('nickname') or runtime_persona_setting(self, 'default_nickname', '你')}\n",
                        f"语气：{user.get('style') or runtime_persona_setting(self, 'default_style', '温柔')}\n",
                        f"日程：{plan_text}\n",
                        f"拟人状态：{state_text}\n",
                        f"关系角色：{self._private_user_role_label(self._private_user_role(user, user_id))}\n",
                        f"今日主动消息：{user.get('sent_today', 0)}/{self._effective_user_daily_limit(user)}\n",
                        f"今日软目标：约 {self._soft_daily_target(user):.1f} 条\n",
                        f"免打扰：{runtime_persona_setting(self, 'quiet_hours', '23:00-08:30')}\n",
                        f"上次活跃：{last_seen}\n",
                        f"上次主动：{last_sent}\n",
                        f"下次候选：{self._format_next_proactive(user)}\n",
                        f"{simulation_text}\n" if simulation_text else "",
                        f"{self._format_suspended_summary(user)}\n",
                        f"主动方式承接：{self._format_action_affinity_summary(user)}\n",
                        f"关系：{self._format_relationship_summary(user)}",
                    ]
                )
            elif action in {"撤回消息", "防撤回", "转述撤回", "撤回转述"}:
                if not runtime_persona_setting(self, 'enable_recall_enhancement', True) or not runtime_persona_setting(self, 'enable_recall_transcribe_command', True):
                    response = "撤回消息转述没有开启。"
                else:
                    response = self._format_recalled_messages_for_event(event, limit=5)
                    response_extra_components = self._recalled_message_media_components_for_event(event, limit=5)
            elif action in tts_language_actions:
                tts_value = value
                if action in {"TTS", "tts"}:
                    tts_parts = value.split(maxsplit=1)
                    if tts_parts and tts_parts[0].strip().lower() in {"语种", "语言", "language", "lang"}:
                        tts_value = tts_parts[1].strip() if len(tts_parts) >= 2 else ""
                response = self._set_tts_voice_language_from_command(tts_value)
            elif action in companion_manual_confirm_actions:
                response = await self._companion_manual_apply_pending_config(event)
            elif action in companion_manual_cancel_actions:
                response = self._companion_manual_cancel_pending_config(event)
            elif action in companion_manual_setting_actions:
                response = await self._companion_manual_apply_setting_command(event, value)
            elif action in companion_manual_query_actions:
                response = "正在结合说明书和当前运行状态做诊断。"
            elif action in {"参考图", "人设参考图", "自拍参考图"}:
                response, response_image_path = await self._photo_reference_command_payload(event, user_id, value)
            elif action == "参考图库":
                response, response_image_path = await self._photo_reference_library_command_payload(event, user_id, value)
            elif action in wardrobe_command_actions:
                response, response_image_path = await self._wardrobe_command_payload(event, user_id, value)
            elif action in daily_outfit_view_actions:
                response, response_image_path = self._daily_outfit_command_payload()
            elif action in image_api_status_actions:
                response = self._image_api_command_status_text()
            elif action in image_api_swap_actions:
                response = "正在交换在线生图 API 优先级。"
            elif action in qweather_location_actions:
                response = "正在处理天气城市设置。"
            elif action in photo_command_actions:
                response = "正在准备图片。"
            elif action in {"查看主动判定", "主动判定", "判定"}:
                response = self._explain_proactive_decision(user)
            elif action in {"能力列表", "主动能力", "工具列表"}:
                response = self._format_proactive_ability_list_for_user(user)
            elif action in {"重置当前人格", "当前人格重置", "重置人格"}:
                response = "正在备份并重置当前人格资料，插件基础配置和窗口绑定会保留。"
            elif action in {"重置插件", "全部重置"}:
                response = "正在清空插件状态,并重新生成今天的状态和日程。"
            elif action == "重置":
                response = "请明确要重置的对象，例如“陪伴 重置 日程”“陪伴 重置 细化”或“陪伴 重置 插件”。"
            elif action in {"查看提示词", "提示词", "prompt"}:
                response = "正在整理当前这层提示词。"
            elif action in {"重置细化"}:
                response = "正在生成当前时间段细化。"
            elif action in {"增添状态", "添加状态"}:
                response = "正在把这个状态加进去。"
            elif action in {"当前细化", "查看当前细化"}:
                response = self._format_current_detail_view()
            elif action in {"查看今日日程", "查看日程", "今日日程", "日程"}:
                plan = self.data.get("daily_plan", {})
                response = self._format_daily_plan(plan)
            elif action in daily_schedule_regenerate_actions:
                response = (
                    "正在重新细化指定的日程段。"
                    if value
                    else "正在生成今天的日程,我先把今天怎么过想清楚。"
                )
            elif action in daily_schedule_cancel_actions:
                response = "正在取消指定的日程段。"
            elif action in daily_outfit_generate_actions:
                response = "正在按今日日程生成每日穿搭照片。"
            elif action in {"生成状态", "刷新状态", "重生状态"}:
                response = "正在刷新今天的拟人状态。"
            elif action in {"梦境", "做了什么梦", "今日梦境"}:
                state = self.data.get("daily_state", {})
                response = self._format_dream_view(state if isinstance(state, dict) else {})
            elif action in {"梦境碎片", "梦碎片", "碎片梦境"}:
                response = self._format_dream_fragment_pool_view()
            elif action in {"画像", "关系", "回复率"}:
                response = self._format_user_profile(user)
            elif action in {"记忆", "陪伴记忆"}:
                response = "当前本地陪伴画像：\n" + self._format_companion_memory_for_prompt(user)
            elif action in {"表达学习", "说话风格", "口癖"}:
                response = "当前表达节奏学习：\n" + self._format_expression_profile_for_prompt(user)
            elif action in {"气氛", "意图", "关系状态"}:
                response = "当前气氛判断：\n" + (self._format_intent_relationship_injection(user) or "暂无样本。")
            elif action in {"片段", "对话片段", "共同经历", "未完成"}:
                episode_text = self._format_dialogue_episodes_for_prompt(user) or "暂无对话片段记忆。"
                loop_text = self._format_open_loops_for_prompt(user) or "暂无未完成约定。"
                response = f"当前对话片段：\n{episode_text}\n\n未完话头：\n{loop_text}"
            elif action in {"话头删除", "删除话头", "未完话头删除", "删除未完话头"}:
                memory_managed = self._req041_private_memory_managed()
                memory_revision = (
                    self._req041_prepare_authoritative_private_memory(user)
                    if memory_managed else None
                )
                if memory_managed and memory_revision is None:
                    response = "权威私聊记忆暂不可写，请稍后重试。"
                else:
                    response = self._remove_open_loop_entry(user, value)
                    committed = not memory_managed or self._req041_commit_authoritative_private_memory(
                        user,
                        expected_revision=memory_revision,
                        operation_id="req041-command-open-loop:" + uuid.uuid4().hex,
                        fields=("open_loops",),
                    )
                    if committed:
                        save_sections = {"users"}
                        if memory_managed:
                            save_sections.add("_req041_private_memory")
                        self._save_data_sync(sections=save_sections)
                    else:
                        response = "记忆已发生并发变更，请重试。"
            elif action in {"长期记忆", "livingmemory", "lmem", "向量记忆"}:
                response = self._format_livingmemory_status()
            elif action in {"日记", "bot日记", "小记"}:
                response = self._format_diaries()
            elif action in {"资料柜密码", "夹层密码", "抽屉密码", "资料柜暗格"}:
                response = "这个要直接问我本人。她会不会说、怎么说,要看当时的人格和心情。"
            elif bookshelf_password_output_requested:
                password = await self._ensure_bookshelf_password_async()
                password_reason = await self._ensure_bookshelf_password_reason_async(password)
                secret = self.data.get("bookshelf_secret", {}) if isinstance(self.data.get("bookshelf_secret"), dict) else {}
                response = (
                    "当前资料柜夹层密码：\n"
                    f"{password}\n"
                    f"生成方式：{_single_line(secret.get('basis'), 40) or '未知'}\n"
                    f"理由：{password_reason or '这是一枚资料柜夹层里的私密暗号。'}"
                )
            elif action in bookshelf_password_reset_actions:
                secret = self.data.setdefault("bookshelf_secret", {})
                if not isinstance(secret, dict):
                    secret = {}
                    self.data["bookshelf_secret"] = secret
                secret.pop("password", None)
                # Changing the secret also revokes any browser session issued for
                # the previous password; a fresh unlock should be required.
                secret.pop("web_access", None)
                runtime_access = getattr(self, "_bookshelf_access_tokens", None)
                if isinstance(runtime_access, dict):
                    runtime_access.clear()
                secret["reset_at"] = _now_ts()
                await self._ensure_bookshelf_password_async()
                self._save_data_sync(sections={"bookshelf_secret"})
                response = "已重新设置资料柜夹层密码。需要查看真实密码可用：陪伴 输出夹层密码"
            elif action in {"发说说", "发QQ空间", "发布说说", "空间发布", "发布空间"}:
                response = "正在发布 QQ 空间说说。"
            elif action in {"测试说说链路", "测试空间发布", "测试QQ空间发布", "测试qzone发布"}:
                response = "正在模拟 QQ 空间发布链路。"
            elif action in {"测试说说配图", "测试空间配图", "测试QQ空间配图", "测试qzone配图"}:
                response = "正在测试 QQ 空间配图生成链路。"
            elif action in {"AI日报", "ai日报", "日报", "AI早报", "ai早报", "早报"}:
                response = "我先看看最近的 AI 日报记录。"
            elif action in {"新闻", "今日新闻", "AI新闻", "ai新闻"}:
                response = "正在读今天的新闻源。"
            elif action in {"生成日记", "刷新日记"}:
                response = "正在写今天的日记。"
            elif action in {"日期列表", "重要日期", "日期"}:
                response = self._format_important_dates()
            elif action in {"日期添加", "添加日期", "重要日期添加"}:
                ok, response = self._add_important_date_entry(value)
                if ok:
                    self._save_data_sync(sections={"important_dates"})
            elif action in {"日期删除", "删除日期", "重要日期删除"}:
                response = self._remove_important_date_entry(value)
                self._save_data_sync(sections={"important_dates"})
            elif action in {"可做事项", "能做什么"}:
                items = self.data.get("can_do", [])
                if items:
                    response = "我现在可以安排进日程的事：\n" + "\n".join(f"- {_single_line(item, 80)}" for item in items)
                else:
                    response = "还没有可做事项。"
            elif action in {"昵称", "称呼"}:
                if not value:
                    response = "请这样设置：陪伴 昵称 <你喜欢的称呼>"
                else:
                    user["nickname"] = _single_line(value, 24)
                    self._save_data_sync(sections={"users"})
                    response = f"记住了,以后我会叫你：{user['nickname']}"
            elif action in {"语气", "风格"}:
                style_value = _single_line(value, 24)
                if not style_value:
                    response = "请这样设置：陪伴 语气 <简短语气描述>"
                else:
                    user["style"] = style_value
                    self._save_data_sync(sections={"users"})
                    response = f"语气偏好已记录：{style_value}"
            elif action in {"清空记忆", "忘记我"}:
                self.data.setdefault("users", {}).pop(user_id, None)
                self._save_data_sync(sections={"users"})
                response = "已清空你的陪伴设置和轻量记忆。"
            else:
                response = self._help_text()

        if action not in deferred_actions:
            await self._reply_with_optional_media(
                event,
                response,
                response_image_path,
                extra_components=response_extra_components,
            )
        if (
            action in wakeup_alarm_actions
            and isinstance(wakeup_test_requested, dict)
            and wakeup_test_requested.get("camera_snapshot")
        ):
            camera_snapshotter = getattr(self, "_reality_touch_camera_snapshot_for_user", None)
            try:
                result = (
                    await camera_snapshotter(user_id, wakeup_test_requested.get("purpose"))
                    if callable(camera_snapshotter)
                    else {"status": "unavailable", "message": "当前插件实例没有摄像头单帧能力"}
                )
            except Exception as exc:
                logger.warning(
                    "摄像头单帧读取异常: %s",
                    _single_line(exc, 160),
                )
                result = {"status": "error", "message": "摄像头单帧读取失败，请稍后再试或检查设备连接。"}
            observation = result.get("observation") if isinstance(result.get("observation"), dict) else {}
            detail = _single_line(observation.get("summary"), 180)
            await self._reply(
                event,
                ("单帧读取完成：" + detail) if result.get("status") == "success" and detail else _single_line(result.get("message"), 200),
            )
            event.stop_event()
            return
        if action in wakeup_alarm_actions and wakeup_test_requested:
            self._create_lifecycle_background_task(
                self._test_wakeup_alarm(user),
                label="wakeup_alarm_test",
            )
            event.stop_event()
            return
        if action in companion_manual_query_actions:
            await self._reply(event, await self._companion_manual_answer(event, value))
            event.stop_event()
            return
        if action in qweather_location_actions:
            await self._reply(event, await self._qweather_location_command_text(action, value))
            event.stop_event()
            return
        if action in photo_command_actions:
            await self._handle_companion_photo_command(event, user_id, action, value)
            return
        if action in image_api_swap_actions:
            force_swap = bool(re.search(r"(?:强制|force|确认|直接)", value, flags=re.I))
            await self._reply(event, await self._swap_external_image_api_command_text(force=force_swap))
            event.stop_event()
            return
        if action in {"发说说", "发QQ空间", "发布说说", "空间发布", "发布空间"}:
            image_sources = await self._qzone_image_sources_from_event(event)
            image_sources, image_select_message = self._qzone_select_image_sources(value, image_sources)
            if image_select_message:
                await self._reply(event, image_select_message)
                event.stop_event()
                return
            publish_text = self._qzone_clean_publish_text(value)
            if image_sources and publish_text in {"[图片]", "【图片】", "图片"}:
                publish_text = ""
            if not publish_text and not image_sources:
                await self._reply(event, "请这样使用：陪伴 发说说 <正文>，也可以随消息附带图片。\n这是公开发布动作，正文或图片不能为空。")
                event.stop_event()
                return
            await self._reply(event, response)
            result = await self._publish_qzone_text(publish_text, event, images=image_sources, auto_generate_image=True)
            if result.get("success"):
                await self._reply(
                    event,
                    "QQ 空间说说已发布。\n"
                    f"QQ：{result.get('uin') or '未知'}\n"
                    f"tid：{result.get('tid') or '未知'}\n"
                    f"正文：{_single_line(result.get('text'), 160) or '无'}\n"
                    f"图片：{len(result.get('images') or [])} 张\n"
                    f"校验：{_single_line(result.get('verify_message'), 120) or ('通过' if result.get('verified') else '未校验')}",
                )
            else:
                await self._reply(event, f"发布失败：{_single_line(result.get('message'), 180)}")
            event.stop_event()
            return
        if action in {"测试说说链路", "测试空间发布", "测试QQ空间发布", "测试qzone发布"}:
            await self._reply(event, response)
            await self._reply(event, await self._test_qzone_publish_tool_chain(event))
            event.stop_event()
            return
        if action in {"测试说说配图", "测试空间配图", "测试QQ空间配图", "测试qzone配图"}:
            await self._reply(event, response)
            await self._reply(event, await self._test_qzone_publish_image_chain(event))
            event.stop_event()
            return
        if action in {"AI日报", "ai日报", "日报", "AI早报", "ai早报", "早报"}:
            await self._reply(event, response)
            await self._maybe_track_ai_daily(force=True)
            await self._reply(event, self._format_ai_daily_digest_for_command())
            event.stop_event()
            return
        if action in {"新闻", "今日新闻", "AI新闻", "ai新闻"}:
            await self._reply(event, response)
            await self._perform_news_reading(reason="user_query", allow_share=False, force=True)
            await self._reply(event, self._format_news_digest_for_command())
            event.stop_event()
            return
        if action in bookshelf_password_reset_actions:
            await self._reply(event, response)
        if action in {"重置当前人格", "当前人格重置", "重置人格"}:
            result = await self._reset_current_persona_store(rebuild_today=True)
            if not result.get("ok"):
                await self._reply(event, result.get("message") or "当前人格重置失败。")
            else:
                persona_label = result.get("persona_id") or "当前单人格资料"
                generation = result.get("generation") or 1
                rebuild_error = _single_line(result.get("rebuild_error"), 180)
                message = (
                    f"当前人格已重置：{persona_label}\n"
                    f"人格资料代次：第 {generation} 代\n"
                    "插件基础配置、多人格列表和窗口绑定均已保留。\n"
                    "重置前资料已保存到 persona_backups。同步到 MemoryCompanion 的当前人格分域投影会一并清理；"
                    "AstrBot 会话历史和 MemoryCompanion 自主管理的其他长期记忆不受影响。"
                )
                if rebuild_error:
                    message += f"\n今日状态与日程自动重建失败：{rebuild_error}"
                else:
                    state = result.get("state") if isinstance(result.get("state"), dict) else {}
                    plan = result.get("plan") if isinstance(result.get("plan"), dict) else {}
                    if state:
                        message += "\n\n" + self._format_state_detail(state)
                    if plan:
                        message += "\n\n" + self._format_daily_plan(plan)
                await self._reply(event, message)
        if action in {"重置插件", "全部重置"}:
            await self._reset_plugin_store()
            state, plan, _ = await self._rebuild_today_after_reset()
            await self._reply(
                event,
                "插件状态已清空并重建。\n"
                + self._format_state_detail(state)
                + "\n\n"
                + self._format_daily_plan(plan or {}),
            )
        if action in daily_schedule_regenerate_actions:
            if value:
                ok, message, detail = await self._regenerate_daily_plan_segment_by_selector(
                    value,
                    generate_detail_enhancement,
                )
                if ok and isinstance(detail, dict):
                    summary = _single_line(detail.get("summary"), 140)
                    if summary:
                        message = f"{message}\n{summary}"
                await self._reply(event, message)
            else:
                plan = await self._ensure_daily_plan(force=True)
                async with self._data_lock:
                    self.data["detail_enhanced_day"] = str((plan or {}).get("date") or _today_key())
                    self.data["detail_enhanced_segments"] = {}
                    self.data["daily_story_plan"] = {}
                    self._save_data_sync(
                        sections={
                            "daily_plan",
                            "daily_story_plan",
                            "detail_enhanced_day",
                            "detail_enhanced_segments",
                        }
                    )
                await self._reply(event, self._format_daily_plan(plan or {}))
        if action in daily_schedule_cancel_actions:
            _, message = await self._cancel_daily_plan_segment_by_selector(value)
            await self._reply(event, message)
        if action in daily_outfit_generate_actions:
            outfit_generator = getattr(self, "_ensure_daily_outfit_photo", None)
            outfit_lock = getattr(self, "_daily_outfit_photo_generation_lock", None)
            wait_existing = bool(outfit_lock is not None and outfit_lock.locked())
            if wait_existing:
                await self._reply(event, "穿搭图已经在生成中了，我等这轮结果出来直接发给你。")
                outfit = await outfit_generator(force=False) if callable(outfit_generator) else None
            else:
                await self._reply(event, "等我换身衣服哦")
                plan = await self._ensure_daily_plan(force=False)
                if not plan:
                    plan = await self._ensure_daily_plan(force=True)
                outfit = await outfit_generator(force=True) if callable(outfit_generator) else None
            if isinstance(outfit, dict) and outfit.get("path"):
                image_path = _path_text(outfit.get("path"), 1000)
                if not os.path.exists(image_path):
                    await self._reply(event, f"每日穿搭照片未生成：图片文件不存在 {image_path}")
                    event.stop_event()
                    return
                caption = "换好啦，你看"
                if not self._should_skip_recent_outfit_command_send(event, text=caption, image_path=image_path):
                    try:
                        await self._reply_with_optional_media(event, caption, image_path)
                    except Exception as exc:
                        logger.warning(
                            "每日穿搭命令发图异常,为避免重复发送已不再兜底补发: image=%s err=%s",
                            _single_line(image_path, 160),
                            _single_line(exc, 180),
                        )
            else:
                error = _single_line((outfit or {}).get("error") if isinstance(outfit, dict) else "", 180)
                note = _single_line((outfit or {}).get("note") if isinstance(outfit, dict) else "", 180)
                await self._reply(event, f"每日穿搭照片未生成：{error or note or '没有可用结果'}")
        if action in {"生成状态", "刷新状态", "重生状态"}:
            state = await self._ensure_daily_state(force=True)
            async with self._data_lock:
                self.data["daily_plan"] = {}
                self._save_data_sync(sections={"daily_plan"})
            await self._reply(
                event,
                self._format_state_detail(state)
                + "\n今天的日程已清空,下次生成日程会按这个状态重新安排。",
            )
        if action in {"增添状态", "添加状态"}:
            ok, message = await self._add_manual_state(value)
            if ok:
                async with self._data_lock:
                    self.data["daily_plan"] = {}
                    state = dict(self.data.get("daily_state", {}))
                    self._save_data_sync(sections={"daily_plan"})
                await self._reply(
                    event,
                    message
                    + "\n"
                    + self._format_state_detail(state)
                    + "\n今天的日程已清空,下次生成日程会按这个状态重新安排。",
                )
            else:
                await self._reply(event, message)
        if action in {"查看提示词", "提示词", "prompt"}:
            prompt_text = await self._debug_prompt_text(value or "主动", user, event)
            await self._reply(event, prompt_text)
        if action in {"重置细化"}:
            plan = await self._ensure_daily_plan(force=False)
            if not plan:
                plan = await self._ensure_daily_plan(force=True)
            ok, message, detail = await self._regenerate_daily_plan_segment_by_selector(
                "当前",
                generate_detail_enhancement,
                reason="用户通过聊天命令重置当前日程细化",
            )
            if ok:
                detail_text = self._format_current_detail_view()
                if detail_text:
                    message = f"{message}\n{detail_text}"
            await self._reply(event, message)
        if action in {"生成日记", "刷新日记"}:
            diary = await self._ensure_daily_diary(force=True)
            await self._reply(event, self._format_single_diary(diary or {}))
        if action in {"梦境", "做了什么梦", "今日梦境"}:
            state = await self._ensure_daily_state(force=False)
            if not state:
                state = await self._ensure_daily_state(force=True)
            await self._reply(event, self._format_dream_view(state or {}))
        event.stop_event()

    @filter.command("陪伴群", alias={"群陪伴", "群聊陪伴"})
    @_multi_persona_event_context
    async def group_companion_command(self, event: AstrMessageEvent):
        """管理群聊陪伴状态、群友画像、群内常见词、话题线程和关系网。"""
        if self is None:
            return
        self._qzone_note_event_bot(event)
        async for result in self._group_companion_command_impl(event):
            yield result

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE, priority=220000)
    @_multi_persona_event_context
    @event_data_save_boundary
    async def guard_req036_private_capability_early(self, event: AstrMessageEvent, *args, **kwargs):
        """Reject an unauthorized private event before any normal message plugin runs."""
        if self is None or bool(getattr(event, "private_companion_req036_denied", False)):
            return
        inbound_checker = getattr(self, "_event_is_inbound_chat_message", None)
        if callable(inbound_checker) and not inbound_checker(event):
            logger.debug("非入站聊天事件跳过私聊档案预建")
            return
        try:
            user_id = str(event.get_sender_id())
        except Exception:
            user_id = ""
        self_id = self._event_self_id(event)
        if user_id and self_id and user_id == self_id:
            return
        sender_display_name = _single_line(self._sender_display_name(event), 40)
        async with self._data_lock:
            private_user, auto_profile_created = self._ensure_auto_private_user_profile(
                event,
                user_id=user_id,
                sender_display_name=sender_display_name,
                now=_now_ts(),
            )
            if isinstance(private_user, dict):
                user_id = _single_line(private_user.get("user_id"), 160) or user_id
            migrator = getattr(self, "_req036_migrate_configured_target_capability", None)
            migrated = bool(migrator(user_id, private_user)) if callable(migrator) else False
            if migrated:
                self._schedule_data_save(sections={"users"})
        if auto_profile_created:
            logger.info(
                "已建立最小用户档案: user=%s platform=%s",
                _single_line(self._canonical_private_user_id(user_id), 80),
                _single_line(self._platform_kind_for_event(event), 40),
            )

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @_multi_persona_event_context
    @event_data_save_boundary(flush=True)
    async def on_private_message(self, event: AstrMessageEvent, *args, **kwargs):
        await _mark_hdsi_inbound(self, event)
        if await self._handle_private_message_preflight(event):
            return
        return await handle_private_message(self, event, *args, **kwargs)

    async def _handle_private_message_preflight(self, event: AstrMessageEvent) -> bool:
        feedback_text = str(getattr(event, "message_str", "") or "")
        if self._message_debounce_command_text(event, feedback_text):
            return False
        feedback_handler = getattr(self, "_maybe_handle_wakeup_feedback", None)
        pending_confirmation_handler = getattr(self, "_reality_touch_apply_pending_confirmation", None)
        if callable(pending_confirmation_handler):
            resolver = getattr(self, "_private_user_id_for_event", None)
            user_id = resolver(event) if callable(resolver) else str(event.get_sender_id() or "").strip()
            confirmation_reply = None
            camera_pending = False
            # 锁内只读数据并判定是否为遗留的摄像头待授权；外部插件调用一律
            # 放到锁外，避免拉长全局数据锁的持有时间。
            async with self._data_lock:
                users = self.data.get("users", {}) if isinstance(getattr(self, "data", None), dict) else {}
                user = users.get(user_id) if isinstance(users, dict) else None
                if isinstance(user, dict):
                    pending = user.get("reality_touch_pending_consent")
                    camera_pending = isinstance(pending, dict) and pending.get("capability") == self._REALITY_TOUCH_CAMERA_CAPABILITY
            if camera_pending:
                if self._reality_touch_camera_user_eligible(user_id):
                    try:
                        confirmation_reply = pending_confirmation_handler(user, feedback_text)
                    except Exception as exc:
                        logger.warning(
                            "现实触及待授权确认处理失败: %s",
                            _single_line(exc, 160),
                        )
                else:
                    async with self._data_lock:
                        users = self.data.get("users", {}) if isinstance(getattr(self, "data", None), dict) else {}
                        user = users.get(user_id) if isinstance(users, dict) else None
                        if isinstance(user, dict):
                            user.pop("reality_touch_pending_consent", None)
                            self._save_data_sync(sections={"users"})
                    confirmation_reply = "主机摄像头只允许 AstrBot 管理员或主要用户本人授权和使用。"
            elif isinstance(user, dict) and isinstance(
                user.get("reality_touch_pending_consent"), dict
            ):
                try:
                    confirmation_reply = pending_confirmation_handler(user, feedback_text)
                except Exception as exc:
                    logger.warning(
                        "现实触及待授权确认处理失败: %s",
                        _single_line(exc, 160),
                    )
            if confirmation_reply:
                await self._reply(event, confirmation_reply)
                event.stop_event()
                return True
        if callable(feedback_handler):
            raw_user_id = str(event.get_sender_id() or "").strip()
            normalizer = getattr(self, "_canonical_private_user_id", None)
            user_id = normalizer(raw_user_id) if callable(normalizer) else raw_user_id
            users = self.data.get("users", {}) if isinstance(getattr(self, "data", None), dict) else {}
            user = users.get(user_id) if isinstance(users, dict) else None
            if isinstance(user, dict) and await feedback_handler(
                event,
                user_id,
                user,
                feedback_text,
            ):
                return True
        return False

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE, priority=180000)
    @_multi_persona_event_context
    @event_data_save_boundary
    async def guard_req036_group_portrait_queries(self, event: AstrMessageEvent, *args, **kwargs):
        """Reject third-party portrait probing before any retrieval or LLM hook."""
        if self is None or bool(getattr(event, "_private_companion_member_safety_blocked", False)):
            return
        if not bool(runtime_persona_setting(self, 'enable_group_third_party_portrait_guard', True)):
            return
        group_id = self._extract_group_id_from_event(event)
        if not group_id:
            return
        if not self._req036_group_portrait_query_is_directed(event):
            return
        text = self._group_observation_event_text(event)
        kind = self._req036_group_portrait_query_kind(text)
        if not kind:
            return
        if kind == "bot_self":
            return
        if kind == "third_party":
            logger.info(
                "群聊第三方画像查询已拦截: reason=explicit_third_party_query group_hash=%s text_hash=%s text_len=%s",
                hashlib.sha256(str(group_id).encode("utf-8", errors="ignore")).hexdigest()[:12],
                hashlib.sha256(str(text).encode("utf-8", errors="ignore")).hexdigest()[:12],
                len(str(text)),
            )
            event.stop_event()
            await self._reply(event, "这个我不方便替别人整理啦。")
            return
        # An observation-disabled group must not become a wording bypass.  It
        # still does not receive normal group capture; this narrow explicit
        # self-query only prepares a minimal identity/scene reference for the
        # low-sensitivity, same-person Memory request below.
        if not isinstance(getattr(event, "private_companion_unified_profile_context", None), dict):
            try:
                raw_sender_id = str(event.get_sender_id())
                resolver = getattr(self, "_event_private_user_storage_id", None)
                sender_id = (
                    resolver(event, raw_sender_id)
                    if callable(resolver)
                    else self._canonical_private_user_id(raw_sender_id)
                )
            except Exception:
                sender_id = ""
            async with self._data_lock:
                users = self.data.get("users", {}) if isinstance(self.data, dict) else {}
                user = users.get(sender_id) if sender_id and isinstance(users, dict) else None
                self._req036_attach_unified_profile_context(
                    event,
                    user=user if isinstance(user, dict) else None,
                    group_id=group_id,
                    source="group_portrait_query",
                )
                self._schedule_data_save(sections={"users", "unified_person"})
        event.stop_event()
        await self._reply(event, await self._req036_read_group_self_portrait(event))

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @_multi_persona_event_context
    @event_data_save_boundary(flush=True)
    async def on_group_message(self, event: AstrMessageEvent, *args, **kwargs):
        await _mark_hdsi_inbound(self, event)
        return await handle_group_message(self, event, *args, **kwargs)

    def _format_timestamp_elapsed(self, timestamp: Any) -> str:
        ts = _safe_float(timestamp, 0)
        if ts <= 0:
            return "从未"
        delta = _now_ts() - ts
        if delta < -5:
            seconds = abs(delta)
            if seconds < 60:
                return f"{max(1, int(seconds))} 秒后"
            if seconds < 3600:
                return f"{max(1, int(seconds // 60))} 分钟后"
            if seconds < 86400:
                return f"{max(1, int(seconds // 3600))} 小时后"
            return f"{max(1, int(seconds // 86400))} 天后"
        seconds = max(0, delta)
        return self._format_elapsed(seconds)

    def _format_elapsed(self, seconds: float) -> str:
        if seconds < 5:
            return "刚刚"
        if seconds < 60:
            return f"{int(seconds)} 秒前"
        if seconds < 3600:
            return f"{int(seconds // 60)} 分钟前"
        if seconds < 86400:
            return f"{int(seconds // 3600)} 小时前"
        return f"{int(seconds // 86400)} 天前"
