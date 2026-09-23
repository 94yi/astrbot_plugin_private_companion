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
    _PROACTIVE_ONLY_TEMP_UNLOCK_RELATED,
    _PROACTIVE_ONLY_TEMP_UNLOCK_LABELS,
    _PROACTIVE_ONLY_TEMP_UNLOCK_GROUPS,
    _WINDOWS_RESERVED_FILENAME_STEMS,
    _multi_persona_event_context,
    _plugin_instance_can_dispatch,
    _plugin_instance_root,
    _private_companion_runtime,
    _strip_chain_plain_thinking,
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
from .main_req036_unified_person import PrivateCompanionPluginReq036UnifiedPersonMixin
from .main_persona_profile import PrivateCompanionPluginPersonaProfileMixin
from .main_segmented_reply import PrivateCompanionPluginSegmentedReplyMixin
from .main_private_passive_prompt import PrivateCompanionPluginPrivatePassivePromptMixin
from .main_misc_unassigned import PrivateCompanionPluginMiscUnassignedMixin
from .main_util_small import PrivateCompanionPluginUtilSmallMixin
from .main_proactive_only_unlock import PrivateCompanionPluginProactiveOnlyUnlockMixin
from .main_lifecycle import PrivateCompanionPluginLifecycleMixin
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
    PrivateCompanionPluginReq036UnifiedPersonMixin,
    PrivateCompanionPluginPersonaProfileMixin,
    PrivateCompanionPluginSegmentedReplyMixin,
    PrivateCompanionPluginPrivatePassivePromptMixin,
    PrivateCompanionPluginMiscUnassignedMixin,
    PrivateCompanionPluginUtilSmallMixin,
    PrivateCompanionPluginProactiveOnlyUnlockMixin,
    PrivateCompanionPluginLifecycleMixin,
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

    # ------------------------------------------------------------------
    # 手机端陪伴形象：Bot 称呼的唯一数据源在这里，终端只是远程入口
    # ------------------------------------------------------------------

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

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @_multi_persona_event_context
    @event_data_save_boundary(flush=True)
    async def on_group_message(self, event: AstrMessageEvent, *args, **kwargs):
        await _mark_hdsi_inbound(self, event)
        return await handle_group_message(self, event, *args, **kwargs)
