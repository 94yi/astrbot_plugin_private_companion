# -*- coding: utf-8 -*-
"""
ProactiveMessageMixin — 主动消息生成、动作执行和发送链路
"""
from __future__ import annotations

import asyncio
import base64
import binascii
from contextvars import ContextVar
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
import sys
import threading
import time
import unicodedata
import uuid
import zoneinfo
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
from http.cookies import SimpleCookie
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlparse, urlunparse
from xml.etree import ElementTree as ET

_PHOTO_GENERATION_TRACE_FILE_LOCK = threading.Lock()

from astrbot.api import AstrBotConfig
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
try:
    from astrbot.api.message_components import At, Image, Plain, Record, Reply
except ImportError:
    from astrbot.api.message_components import At, Image, Plain
    from astrbot.core.message.components import Record
    try:
        from astrbot.api.message_components import Reply
    except ImportError:
        try:
            from astrbot.core.message.components import Reply
        except ImportError:
            Reply = None
try:
    from astrbot.core.message import components as CoreMessageComponents
except ImportError:
    CoreMessageComponents = None
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.core import file_token_service
from astrbot.core.astr_main_agent import MainAgentBuildConfig, build_main_agent
from astrbot.core.agent.message import AssistantMessageSegment, UserMessageSegment
from astrbot.core.db.po import Conversation
from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
from astrbot.core.platform.message_session import MessageSession
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.platform import PlatformStatus
from astrbot.core.platform.platform_metadata import PlatformMetadata
from astrbot.core.star.star_handler import EventType, star_handlers_registry
from astrbot.core.provider.entities import LLMResponse
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .conversation_prompt_section import (
    PhotoPromptContent,
    PromptDocument,
    PromptDocumentPart,
    PromptLabel,
    PromptLabelStyle,
    PromptRenderMode,
    PromptRenderSpec,
    PromptSection,
    exact_text,
    prompt_cdata,
    prompt_document,
    prompt_document_part,
    prompt_section,
    render_prompt_document,
    render_prompt_sections,
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
from .reference_asset_gate import (
    MAX_INPUT_ASSETS,
    ReferenceAssetGate,
    ReferenceAssetPlan,
    ReferenceAssetTicket,
)
from .helpers import (
    _date_key,
    _format_history_media_marker,
    _normalize_outbound_punctuation_flow,
    _normalize_photo_subject_owner,
    _now_ts,
    _path_text,
    _photo_group_request_matches,
    _photo_subject_owner_prompt_label,
    _redact_outbound_secrets,
    _safe_float,
    _safe_int,
    _single_line,
    _split_address_terms,
    _strip_internal_message_blocks,
    _strip_outbound_control_blocks,
    _today_key,
    normalize_bot_relationship_cards,
)
from .memory_context_policy import (
    core_memory_usage_contract_section,
)
from .final_response_persistence import (
    FinalResponsePersistenceMixin,
    collect_proactive_delivery,
)
from .planning import (
    build_daily_plan_prompt,
    build_detail_enhancement_prompt,
    _external_schedule_material_context,
    format_plan_for_diary,
    generate_daily_plan,
    generate_detail_enhancement,
    get_schedule_planning_prompt,
    normalize_long_term_events,
    normalize_story_items,
    normalize_story_plan,
    pick_detail_segment,
)
from .scene_context import infer_companion_scene_category
from .segmented_message import (
    LLM_SEGMENT_MARKER,
    component_kind,
    component_order_from_owner,
    component_strategies_from_owner,
    plan_component_chunks,
    sanitize_llm_segment_control_tokens,
    split_llm_controlled_text,
)
from .token_budget import _looks_like_upstream_llm_error_response
from .reaction_expression import (
    normalize_reaction_expression_intent,
    reaction_expression_high_frequency,
)
from .photo_reference_catalog import (
    PhotoReference,
    build_daily_outfit_reference,
    load_catalog,
    project_reference_candidate,
)
from .photo_prompt_context import (
    _clip as _clip_photo_prompt_text,
    compile_local_photo_prompt,
    resolve_photo_prompt_context,
)
from .photo_reference_feedback import analyze_photo_reference_feedback
from .photo_reference_intent import (
    CONTINUITY_MODES,
    REFERENCE_ROLES,
    ReferenceIntent,
    analyze_indexed_reference_roles,
    analyze_reference_intent,
    explicitly_excludes_reference_outfit,
)
from .photo_reference_selection import (
    CandidateMatch,
    SelectionResult,
    parse_photo_reference_context_categories,
    select_photo_reference,
)
from .photo_reference_plan import (
    PhotoReferencePlan,
    ReferenceFallback,
    build_photo_reference_plan,
    evaluate_reference_fallback,
    project_reference_plan_for_backend,
)
from .reference_assets import (
    normalize_reference_asset,
    normalize_reference_owner_id,
    reference_asset_tokens,
)
from .wardrobe_photo import resolve_daily_outfit_profile as resolve_wardrobe_daily_outfit_profile
from .photo_wardrobe_decision import (
    PhotoWardrobeDecision,
    PhotoWardrobeIntent,
    analyze_photo_wardrobe,
    merge_photo_wardrobe_continuity,
    resolve_photo_wardrobe_decision,
)

_EXTERNAL_IMAGE_MAX_BYTES = 32 * 1024 * 1024
_EXTERNAL_IMAGE_DOWNLOAD_MAX_ATTEMPTS = 2
_EXTERNAL_IMAGE_DOWNLOAD_RETRY_DELAY_SECONDS = 0.8
_EXTERNAL_IMAGE_DOWNLOAD_TOTAL_TIMEOUT_SECONDS = 75.0
_EXTERNAL_IMAGE_DOWNLOAD_ATTEMPT_TIMEOUT_SECONDS = 35.0
_MINIMAX_REFERENCE_IMAGE_MAX_BYTES = 10 * 1024 * 1024

_EXTERNAL_IMAGE_DOWNLOAD_TIMEOUT_OVERRIDE: ContextVar[float | None] = ContextVar(
    "private_companion_external_image_download_timeout_override",
    default=None,
)
from .proactive_routes import PROACTIVE_ROUTE_REGISTRY
from .persona_config import runtime_persona_setting
from .proactive_message_framework_prompt import ProactiveMessageFrameworkPromptMixin
from .proactive_message_action_execution import ProactiveMessageActionExecutionMixin
from .proactive_message_prompt_context import ProactiveMessagePromptContextMixin
from .proactive_message_external_share import ProactiveMessageExternalShareMixin
from .proactive_message_send_review import ProactiveMessageSendReviewMixin
from .proactive_message_generation import ProactiveMessageGenerationMixin
from .proactive_message_text_finalize import ProactiveMessageTextFinalizeMixin
from .proactive_message_voice_tts import ProactiveMessageVoiceTtsMixin
from .proactive_message_chat_bridge import ProactiveMessageChatBridgeMixin
from .logging_util import get_module_logger
from .proactive_message_shared import (  # noqa: F401  (宿主继续导出，既有调用点零改动)
    _PROACTIVE_DOCUMENT_RENDER,  # noqa: F401
    _persona_provider_id,  # noqa: F401
    _proactive_prompt_part,  # noqa: F401
)

logger = get_module_logger(__name__)


DEFAULT_NEWS_SOURCES = "\n".join(
    [
        "BBC中文|https://feeds.bbci.co.uk/zhongwen/simp/rss.xml",
        "Google新闻中文|https://news.google.com/rss?hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
        "Solidot|https://www.solidot.org/index.rss",
        "Hacker News|https://hnrss.org/frontpage",
        "MIT Technology Review|https://www.technologyreview.com/feed/",
        "Ars Technica|https://feeds.arstechnica.com/arstechnica/index",
    ]
)

LEGACY_DEFAULT_NEWS_SOURCES = "\n".join(
    [
        "BBC中文|https://feeds.bbci.co.uk/zhongwen/simp/rss.xml",
        "Google新闻中文|https://news.google.com/rss?hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
        "Solidot|https://www.solidot.org/index.rss",
    ]
)

PREVIOUS_TECH_DEFAULT_NEWS_SOURCES = "\n".join(
    [
        "BBC中文|https://feeds.bbci.co.uk/zhongwen/simp/rss.xml",
        "Google新闻中文|https://news.google.com/rss?hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
        "Solidot|https://www.solidot.org/index.rss",
        "Hacker News|https://hnrss.org/frontpage",
        "MIT Technology Review|https://www.technologyreview.com/feed/",
        "Ars Technica|https://feeds.arstechnica.com/arstechnica/index",
    ]
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


@dataclass(frozen=True, slots=True)
class _ProactiveSendOutcome:
    delivered: bool
    complete: bool
    delivered_text: str = ""
    image_delivered: bool = False
    extra_components_delivered: int = 0
    note: str = ""
    primary_complete: bool = False
    delivery_umo: str = ""
    delivered_chain: tuple[Any, ...] = ()

    def __bool__(self) -> bool:
        return self.delivered


@dataclass(frozen=True, slots=True)
class PhotoGenerationResult:
    backend: str = ""
    image_path: str = ""
    note: str = ""
    trace_id: str = ""
    reference_selected_path: str = ""
    reference_used: bool = False
    reference_id: str = ""
    reference_kind: str = ""
    reference_roles: tuple[str, ...] = ()
    wardrobe_mode: str = ""
    wardrobe_category: str = ""
    outfit_locked: bool = False
    daily_outfit_removed: bool = False
    preset_names: tuple[str, ...] = ()
    preset_hint: str = ""
    preset_source: str = ""
    suggestion_status: str = ""
    prompt_hash: str = ""
    prompt_path: str = ""
    reference_requested_roles: tuple[str, ...] = ()
    reference_excluded_roles: tuple[str, ...] = ()
    continuity_mode: str = "ambiguous"
    reference_confidence: float = 0.0
    reference_plan: tuple[dict[str, Any], ...] = ()
    reference_fulfilled_roles: tuple[str, ...] = ()
    reference_missing_roles: tuple[str, ...] = ()
    reference_fallback_message: str = ""
    generation_completed: bool = False
    failure_stage: str = ""

    @property
    def success(self) -> bool:
        path = _path_text(self.image_path, 1000)
        if not path:
            return False
        try:
            return Path(path).is_file()
        except (OSError, ValueError):
            return False

    def as_legacy_tuple(self) -> tuple[str, str, str]:
        return self.backend, self.image_path, self.note


@dataclass(frozen=True, slots=True, eq=False)
class _ExternalPhotoGenerationOutcome:
    """Internal result state that preserves the legacy ``(path, note)`` API."""

    image_path: str = ""
    note: str = ""
    generation_completed: bool = False
    failure_stage: str = ""

    def as_legacy_tuple(self) -> tuple[str, str]:
        return self.image_path, self.note

    def __iter__(self):
        yield self.image_path
        yield self.note

    def __len__(self) -> int:
        return 2

    def __getitem__(self, index: int) -> str:
        return self.as_legacy_tuple()[index]

    def __eq__(self, other: object) -> bool:
        if isinstance(other, _ExternalPhotoGenerationOutcome):
            return (
                self.image_path,
                self.note,
                self.generation_completed,
                self.failure_stage,
            ) == (
                other.image_path,
                other.note,
                other.generation_completed,
                other.failure_stage,
            )
        if isinstance(other, (tuple, list)) and len(other) == 2:
            return self.as_legacy_tuple() == (other[0], other[1])
        return NotImplemented





class ProactiveMessageMixin(FinalResponsePersistenceMixin, ProactiveMessageChatBridgeMixin, ProactiveMessageVoiceTtsMixin, ProactiveMessageTextFinalizeMixin, ProactiveMessageGenerationMixin, ProactiveMessageSendReviewMixin, ProactiveMessageExternalShareMixin, ProactiveMessagePromptContextMixin, ProactiveMessageActionExecutionMixin, ProactiveMessageFrameworkPromptMixin):
    """主动消息生成、动作执行和发送链路"""


    async def _run_photo_text_action(self, user: dict[str, Any], name: str, reason: str) -> str:
        if not runtime_persona_setting(self, "enable_photo_text_action", True):
            return "photo_text：未启用"
        user_id = str(user.get("user_id") or "")
        scope_checker = getattr(self, "_photo_generation_scope_allowed", None)
        if callable(scope_checker) and not scope_checker(proactive=True, user=user, user_id=user_id):
            return "photo_text：主动生图不在当前配置的使用范围内,不能假装已经拍照"
        load_defer_note = self._photo_text_load_defer_note("photo_text", force_refresh=True)
        if load_defer_note:
            return f"photo_text：{load_defer_note},不能假装已经拍照"
        if not self._photo_text_available(user):
            return "photo_text：今日发图额度已用完或生图后端不可用,不能假装已经拍照"
        if not self._photo_text_available():
            return "photo_text：当前没有可用的生图后端,不能假装已经拍照"

        scene = await self._build_photo_scene_prompt(user, name, reason)
        workflow_kind = scene.get("kind", "text2img")
        normalized_workflow_kind = _single_line(workflow_kind, 40).strip().lower()
        raw_subject_owner = _normalize_photo_subject_owner(scene.get("subject_owner"))
        subject_owner = raw_subject_owner
        if not subject_owner:
            subject_owner = (
                "bot"
                if bool(scene.get("use_persona_reference"))
                or normalized_workflow_kind in {"selfie", "portrait", "自拍", "人像"}
                else "scene"
            )
        if normalized_workflow_kind in {"selfie", "portrait", "自拍", "人像"} and subject_owner == "scene":
            subject_owner = "bot"
        non_bot_identity_owner = raw_subject_owner in {"third_party", "unknown"} or subject_owner in {
            "third_party",
            "unknown",
        }
        bot_identity_required = (
            not non_bot_identity_owner
            and (
                bool(scene.get("use_persona_reference"))
                or normalized_workflow_kind in {"selfie", "portrait", "自拍", "人像"}
                or subject_owner == "bot"
            )
        )
        reference_image_path = ""
        reference_selection_source = ""

        def valid_reference_path(value: Any) -> str:
            """Return a local image path only when it is an existing file."""
            if isinstance(value, SelectionResult):
                value = value.selected
            if isinstance(value, dict):
                value = value.get("path") or value.get("source") or value.get("file_path")
            raw = _path_text(value, 1000)
            if not raw or re.match(r"^(?:https?|data):", raw, flags=re.I):
                return ""
            try:
                path = Path(raw).expanduser()
                if not path.is_absolute():
                    data_dir = _path_text(getattr(self, "data_dir", ""), 1000)
                    if data_dir:
                        path = Path(data_dir) / path
                path = path.resolve()
                if not path.is_file() or path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
                    return ""
            except (OSError, ValueError, TypeError, RuntimeError):
                return ""
            return str(path)

        # A third-party or ambiguous owner must never inherit Bot's persona image.
        # Such scenes may proceed only with an explicitly supplied, valid reference.
        if non_bot_identity_owner:
            reference_image_path = valid_reference_path(scene.get("reference_image_path"))
            reference_selection_source = "explicit_reference" if reference_image_path else ""
            if not reference_image_path:
                return (
                    "photo_text：缺少有效身份参考图，已停止提交人物画面\n"
                    f"画面草稿：{_single_line(scene.get('caption'), 180)}\n"
                    "失败原因：第三方或归属不明的人物只能使用对应的可验证参考图，不能套用 Bot 身份图。"
                )

        if bot_identity_required:
            # Character-bearing photo_text scenes need identity continuity even when
            # their rendering workflow is text2img rather than selfie.
            reference_image_path = valid_reference_path(scene.get("reference_image_path"))
            if reference_image_path:
                reference_selection_source = "explicit_reference"
            async_reference_getter = getattr(
                self,
                "_photo_persona_reference_image_for_kind_async",
                None,
            )
            if not reference_image_path and callable(async_reference_getter):
                try:
                    selected_path = await async_reference_getter(
                        "selfie",
                        allow_daily_outfit=True,
                        requester_user_id=str(user.get("user_id") or ""),
                        request_text=_single_line(scene.get("prompt"), 900),
                        ambient_context=_single_line(scene.get("scene_context"), 900),
                    )
                    reference_image_path = valid_reference_path(selected_path)
                    if reference_image_path:
                        reference_selection_source = "selected_reference"
                except Exception as exc:
                    logger.debug(
                        "proactive photo reference selection failed: %s",
                        _single_line(exc, 160),
                    )
            if not reference_image_path:
                fallback_getter = getattr(
                    self,
                    "_photo_persona_reference_image_for_kind",
                    None,
                )
                if callable(fallback_getter):
                    try:
                        reference_image_path = valid_reference_path(
                            fallback_getter("selfie", allow_daily_outfit=False)
                        )
                    except Exception as exc:
                        logger.debug(
                            "proactive photo identity fallback failed: %s",
                            _single_line(exc, 160),
                        )
                if not reference_image_path:
                    fallback_path_getter = getattr(
                        self,
                        "_photo_persona_reference_image_path_async",
                        None,
                    )
                    if callable(fallback_path_getter):
                        try:
                            reference_image_path = valid_reference_path(
                                await fallback_path_getter()
                            )
                        except Exception as exc:
                            logger.debug(
                                "proactive photo identity path fallback failed: %s",
                                _single_line(exc, 160),
                            )
                if reference_image_path:
                    reference_selection_source = "identity_fallback"
            if not reference_image_path:
                return (
                    "photo_text：缺少有效身份参考图，已停止提交人物画面\n"
                    f"画面草稿：{_single_line(scene.get('caption'), 180)}\n"
                    "失败原因：需要 Bot 或其他人物的可验证参考图，不能生成无来源的人脸。"
                )
        elif subject_owner == "scene":
            scene["prompt"] = self._append_photo_negative_terms(
                scene.get("prompt", ""),
                ["people", "human figures", "faces", "silhouettes"],
                limit=900,
            )
        session_key = str(user.get("umo") or user.get("user_id") or name)
        continuity_key = self._compose_photo_continuity_key(session_key, user.get("user_id"))
        backend_name, image_path, workflow_note = await self._generate_photo_image(
            workflow_kind=workflow_kind,
            prompt_text=scene["prompt"],
            request_text=scene["prompt"],
            session_key=session_key,
            continuity_key=continuity_key,
            requester_user_id=str(user.get("user_id") or ""),
            reference_image_path=reference_image_path,
            prompt_format=_single_line(scene.get("prompt_format"), 40),
        )
        if not image_path:
            counted_attempt = self._photo_generation_failure_counts_as_attempt(workflow_note)
            if counted_attempt:
                async with self._data_lock:
                    self._note_photo_generation_attempt(user_id, image_path="")
                    scope_notifier = getattr(self, "_note_photo_generation_scope_attempt", None)
                    if callable(scope_notifier):
                        scope_notifier(
                            proactive=True,
                            user=user,
                            user_id=user_id,
                            scope="proactive",
                        )
                    self._save_photo_generation_attempts_compat()
            return (
                "photo_text：生图失败,不能假装已经拍照\n"
                f"画面草稿：{scene['caption']}\n"
                f"失败原因：{_single_line(workflow_note, 160)}"
                + ("\n本次已计入今日生图尝试额度,避免接口失败时反复请求。" if counted_attempt else "")
            )
        async with self._data_lock:
            self._note_photo_generation_attempt(user_id, image_path=image_path)
            scope_notifier = getattr(self, "_note_photo_generation_scope_attempt", None)
            if callable(scope_notifier):
                scope_notifier(
                    proactive=True,
                    user=user,
                    user_id=user_id,
                    scope="proactive",
                )
            self._save_photo_generation_attempts_compat()
        scene_context_line = _single_line(scene.get("scene_context"), 500)
        return (
            f"photo_text：已通过 {backend_name} 生成真实图片\n"
            f"图片类型：{workflow_kind}\n"
            f"后端：{backend_name}\n"
            f"图片路径：{image_path}\n"
            f"画面：{scene['caption']}\n"
            f"图片主体归属：{subject_owner}\n"
            f"人物参考图：{('已使用（' + (reference_selection_source or 'selected_reference') + '）') if reference_image_path else '未使用'}\n"
            + (f"统一情境：{scene_context_line}\n" if scene_context_line else "")
            + f"生图提示：{_single_line(scene['prompt'], 240)}"
        )

    def _save_photo_generation_attempts_compat(self) -> None:
        """Persist photo quota changes while tolerating legacy test/host overrides."""
        saver = getattr(self, "_save_data_sync", None)
        if not callable(saver):
            return
        try:
            parameters = inspect.signature(saver).parameters.values()
            accepts_sections = any(
                parameter.name == "sections"
                or parameter.kind is inspect.Parameter.VAR_KEYWORD
                for parameter in parameters
            )
        except (TypeError, ValueError):
            accepts_sections = True
        if accepts_sections:
            saver(sections={"users", "photo_generation_scope_attempts"})
        else:
            saver()

    def _photo_generation_failure_counts_as_attempt(self, note: str) -> bool:
        text = _single_line(note, 500)
        if not text:
            return False
        count_tokens = (
            "HTTP",
            "超时",
            "请求",
            "接口",
            "上游",
            "upstream",
            "返回格式",
            "未返回",
            "返回空",
            "下载",
            "保存",
            "响应不是图片",
            "输出不是图片",
            "工作流完成但图片",
            "Error code",
            "Exception",
        )
        if any(token in text for token in count_tokens):
            return True
        skip_tokens = (
            "未启用",
            "未配置",
            "不可用或未配置",
            "后端不可用",
            "插件不可用",
            "工作流名",
            "未找到匹配工作流",
            "电脑高负荷",
            "负载偏高",
            "今日发图额度",
            "文本/聊天模型",
            "请改成图片模型",
        )
        return not any(token in text for token in skip_tokens)

    async def _ensure_daily_outfit_photo(
        self,
        diary: dict[str, Any] | None = None,
        *,
        force: bool = False,
    ) -> dict[str, Any] | None:
        if not force and not runtime_persona_setting(self, "enable_daily_outfit_photo", False):
            return None
        today = _today_key()
        async with self._data_lock:
            existing = self.data.get("daily_outfit_photo") if isinstance(self.data.get("daily_outfit_photo"), dict) else {}
            if not force and existing.get("date") == today:
                return dict(existing)
        lock = getattr(self, "_daily_outfit_photo_generation_lock", None)
        if lock is None:
            lock = asyncio.Lock()
            self._daily_outfit_photo_generation_lock = lock
        async with lock:
            async with self._data_lock:
                existing = self.data.get("daily_outfit_photo") if isinstance(self.data.get("daily_outfit_photo"), dict) else {}
                if not force and existing.get("date") == today:
                    return dict(existing)
            return await self._ensure_daily_outfit_photo_unlocked(diary, force=force, today=today)

    async def _ensure_daily_outfit_photo_unlocked(
        self,
        diary: dict[str, Any] | None = None,
        *,
        force: bool = False,
        today: str = "",
    ) -> dict[str, Any] | None:
        today = today or _today_key()
        if not runtime_persona_setting(self, "enable_photo_text_action", True):
            return await self._record_daily_outfit_photo_result(today, "", "主动拍照/生图未开启")
        scope_checker = getattr(self, "_photo_generation_scope_allowed", None)
        if callable(scope_checker) and not scope_checker(proactive=True):
            return await self._record_daily_outfit_photo_result(today, "", "主动生图不在当前配置的使用范围内")
        if not self._photo_text_available():
            return await self._record_daily_outfit_photo_result(today, "", "当前没有可用的生图后端")
        memory_context = ""
        composer = getattr(self, "_memory_companion_compose_feature_context", None)
        if callable(composer):
            try:
                memory_context = await composer(
                    kind="daily_outfit_photo",
                    query=(
                        "今日穿搭生成：历史穿搭、今天日程、天气、地点、用户常问衣服颜色、"
                        "最近自拍、服装连续性、需要避免的造型重复"
                    ),
                    top_k=5,
                    max_chars=900,
                )
            except Exception as exc:
                logger.debug("每日穿搭 我会牢牢记住你 上下文读取失败: %s", _single_line(exc, 120))
        schedule_hint = self._daily_outfit_schedule_text()
        weather = self._format_weather_for_prompt() if callable(getattr(self, "_format_weather_for_prompt", None)) else ""
        outfit_profile = self._select_daily_outfit_profile(
            schedule_hint=schedule_hint,
            weather=weather,
            date_key=today,
        )
        prompt_text = self._build_daily_outfit_photo_prompt(
            diary if isinstance(diary, dict) else {},
            memory_context=memory_context,
            outfit_profile=outfit_profile,
        )
        prompt_sections = self._build_daily_outfit_photo_prompt_sections(
            diary if isinstance(diary, dict) else {},
            memory_context=memory_context,
            outfit_profile=outfit_profile,
        )
        backend_name, image_path, note = await self._generate_photo_image(
            workflow_kind="selfie",
            prompt_text=prompt_text,
            request_text=prompt_text,
            session_key="daily_outfit",
            image_size="1024x1024",
            allow_daily_outfit_reference=False,
            prompt_sections=prompt_sections,
        )
        if image_path:
            return await self._record_daily_outfit_photo_result(
                today,
                image_path,
                "",
                backend=backend_name,
                prompt=prompt_text,
                note=note,
                outfit_profile=outfit_profile,
            )
        return await self._record_daily_outfit_photo_result(
            today,
            "",
            _single_line(note, 220) or "生图失败",
            backend=backend_name,
            prompt=prompt_text,
            note=note,
            outfit_profile=outfit_profile,
        )

    async def _record_daily_outfit_photo_result(
        self,
        date_key: str,
        image_path: str,
        error: str = "",
        *,
        backend: str = "",
        prompt: str = "",
        note: str = "",
        outfit_profile: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        item = {
            "date": _single_line(date_key, 20),
            "path": _path_text(image_path, 1000),
            "error": _single_line(error, 240),
            "backend": _single_line(backend, 80),
            "prompt": _single_line(prompt, 500),
            "note": _single_line(note, 220),
            "generated_at": _now_ts(),
            "outfit_profile": self._normalize_daily_outfit_profile(outfit_profile),
        }
        async with self._data_lock:
            history = self._daily_outfit_history_items(include_current=True)
            if image_path:
                history.insert(0, dict(item))
            self.data["daily_outfit_history"] = history[:30]
            self.data["daily_outfit_photo"] = item
            self._save_data_sync(
                sections={"daily_outfit_history", "daily_outfit_photo"}
            )
        if image_path:
            await self._memory_companion_record_daily_outfit(item)
            logger.info(
                "每日穿搭照片已生成: backend=%s path=%s",
                _single_line(backend, 80) or "-",
                _single_line(image_path, 160),
            )
        else:
            logger.info("每日穿搭照片未生成: %s", _single_line(error or note, 180))
        return item

    def _daily_outfit_schedule_text(self) -> str:
        plan = self.data.get("daily_plan", {}) if isinstance(getattr(self, "data", {}), dict) else {}
        if not isinstance(plan, dict):
            return ""
        items = plan.get("items")
        if not isinstance(items, list) or not items:
            return ""
        lines: list[str] = []
        for item in items[:12]:
            if not isinstance(item, dict):
                continue
            time_text = _single_line(item.get("time"), 12)
            activity = _single_line(item.get("activity"), 120)
            mood = _single_line(item.get("mood"), 24)
            if not activity:
                continue
            line = f"{time_text} {activity}".strip()
            if mood:
                line = f"{line}（{mood}）"
            lines.append(line)
        return _single_line("；".join(lines), 620)

    @staticmethod
    def _normalize_daily_outfit_profile(profile: Any) -> dict[str, str]:
        if not isinstance(profile, dict):
            return {}
        limits = {
            "look_id": 80,
            "scene": 32,
            "weather": 32,
            "palette": 120,
            "silhouette": 120,
            "top": 160,
            "outer": 160,
            "bottom": 140,
            # 衣柜生图投影里有 footwear：不加进来会被静默丢弃
            "footwear": 140,
            "accessory": 140,
        }
        return {
            key: value
            for key, maximum in limits.items()
            if (value := _single_line(profile.get(key), maximum))
        }

    def _daily_outfit_history_items(self, *, include_current: bool = True) -> list[dict[str, Any]]:
        data = self.data if isinstance(getattr(self, "data", {}), dict) else {}
        candidates: list[Any] = []
        if include_current:
            candidates.append(data.get("daily_outfit_photo"))
        raw_history = data.get("daily_outfit_history")
        if isinstance(raw_history, list):
            candidates.extend(raw_history)

        history: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw_item in candidates:
            if not isinstance(raw_item, dict):
                continue
            path = _path_text(raw_item.get("path"), 1000)
            if not path:
                continue
            profile = self._normalize_daily_outfit_profile(raw_item.get("outfit_profile"))
            item = {
                "date": _single_line(raw_item.get("date"), 20),
                "path": path,
                "generated_at": _single_line(raw_item.get("generated_at"), 40),
                "outfit_profile": profile,
            }
            identity = "|".join(
                (
                    item["path"],
                    item["generated_at"],
                    item["date"],
                    profile.get("look_id", ""),
                )
            )
            if identity in seen:
                continue
            seen.add(identity)
            history.append(item)
        return history[:30]

    def _daily_outfit_rotation_history(self) -> list[dict[str, Any]]:
        rotation_days = _safe_int(
            runtime_persona_setting(self, "daily_outfit_rotation_days", 10),
            10,
            1,
            30,
        )
        # 与写入侧 _today_key() 保持同一插件时区，避免截止日跨时区偏差一天。
        cutoff = datetime.strptime(_today_key(), "%Y-%m-%d").date() - timedelta(
            days=rotation_days - 1
        )
        history: list[dict[str, Any]] = []
        for item in self._daily_outfit_history_items():
            profile = self._normalize_daily_outfit_profile(item.get("outfit_profile"))
            if not profile:
                continue
            date_key = _single_line(item.get("date"), 20)
            try:
                item_date = datetime.strptime(date_key, "%Y-%m-%d").date()
            except (TypeError, ValueError):
                item_date = None
            if item_date and item_date < cutoff:
                continue
            history.append({**item, "outfit_profile": profile})
        return history[:30]

    @staticmethod
    def _daily_outfit_scene_kind(schedule_hint: str, weather: str) -> str:
        text = f"{schedule_hint} {weather}".lower()
        if any(token in text for token in ("运动", "跑步", "健身", "体育", "workout", "gym", "running")):
            return "sport"
        if any(token in text for token in ("校服", "上课", "教室", "学校", "自习", "放学", "school", "class")):
            return "school"
        if any(token in text for token in ("上班", "工作", "会议", "通勤", "办公室", "office", "commute", "meeting")):
            return "commute"
        if any(token in text for token in ("家", "房间", "卧室", "午休", "起床", "睡前", "入睡", "home", "bedroom")):
            return "home"
        return "daily"

    @staticmethod
    def _daily_outfit_weather_kind(weather: str) -> str:
        text = _single_line(weather, 240).lower()
        if any(token in text for token in ("冷", "降温", "低温", "寒", "雪", "snow", "cold")):
            return "cold"
        if any(token in text for token in ("热", "高温", "闷", "暑", "hot", "heat")):
            return "hot"
        if any(token in text for token in ("雨", "阵雨", "雷", "storm", "rain", "wet")):
            return "rainy"
        return "mild"

    @staticmethod
    def _daily_outfit_outer_options(scene: str, weather_kind: str) -> list[str]:
        if scene == "sport":
            options = {
                "cold": [
                    "lightweight insulated track jacket",
                    "technical hooded running jacket",
                    "short quilted sports jacket",
                    "fleece zip-up athletic layer",
                ],
                "hot": [
                    "no heavy outer layer, breathable short-sleeve overshirt",
                    "no outer layer, airy sun-protection layer tied at the waist",
                    "no outer layer, light mesh sports layer",
                    "no outer layer, sleeveless technical vest",
                ],
                "rainy": [
                    "water-resistant hooded running jacket",
                    "compact rain shell with reflective trim",
                    "light technical windbreaker",
                    "hooded quick-dry sports jacket",
                ],
                "mild": [
                    "clean zip-up track jacket",
                    "lightweight athletic windbreaker",
                    "soft cropped sports jacket",
                    "open technical overshirt",
                ],
            }
            return options[weather_kind]
        if scene == "home":
            options = {
                "cold": [
                    "soft oversized knit cardigan",
                    "warm zip-up hoodie",
                    "light quilted home jacket",
                    "plush lounge cardigan",
                ],
                "hot": [
                    "no outer layer, loose breathable overshirt",
                    "no outer layer, thin open cotton shirt",
                    "no outer layer, airy short-sleeve layer",
                    "no outer layer, light linen cardigan",
                ],
                "rainy": [
                    "soft hooded cardigan for a rainy day indoors",
                    "light zip-up hoodie",
                    "cozy knit cardigan",
                    "thin water-resistant overshirt near the doorway",
                ],
                "mild": [
                    "soft open cardigan",
                    "light zip-up hoodie",
                    "relaxed cotton overshirt",
                    "thin knit vest",
                ],
            }
            return options[weather_kind]
        options = {
            "cold": [
                "camel wool coat with a soft scarf",
                "short dark quilted jacket",
                "navy duffle coat",
                "light gray padded jacket",
            ],
            "hot": [
                "no heavy outer layer, breathable overshirt left open",
                "no outer layer, thin sun-protection cardigan",
                "no outer layer, rolled-sleeve linen overshirt",
                "no outer layer, light short-sleeve shirt layer",
            ],
            "rainy": [
                "water-resistant hooded jacket",
                "light trench coat suitable for rain",
                "compact windbreaker with a hood",
                "short rain shell with clean lines",
            ],
            "mild": [
                "soft knit cardigan",
                "light denim jacket",
                "short bomber jacket",
                "unstructured lightweight blazer",
            ],
        }
        return options[weather_kind]

    def _daily_outfit_candidate_profiles(self, scene: str, weather_kind: str) -> list[dict[str, str]]:
        base_options: dict[str, list[dict[str, str]]] = {
            "school": [
                {"palette": "navy, ivory, and muted burgundy", "silhouette": "neat layered campus silhouette", "top": "crisp white shirt with a navy knit vest", "bottom": "straight-cut charcoal trousers", "accessory": "small burgundy ribbon and a simple watch"},
                {"palette": "pale blue, gray, and silver", "silhouette": "clean relaxed academic silhouette", "top": "pale blue oxford shirt under a fine gray cardigan", "bottom": "neat navy trousers", "accessory": "slim silver hair clip or lapel pin"},
                {"palette": "cream, forest green, and warm brown", "silhouette": "soft collegiate silhouette", "top": "cream sweatshirt with a collared shirt edge showing", "bottom": "tailored brown trousers", "accessory": "small canvas shoulder bag"},
                {"palette": "black, white, and dusty rose", "silhouette": "compact modern campus silhouette", "top": "fine striped tee beneath a clean black overshirt", "bottom": "straight dark jeans", "accessory": "subtle rose-toned hair tie or keychain"},
                {"palette": "sage green, ivory, and charcoal", "silhouette": "quiet knitwear silhouette", "top": "sage knit polo layered over a light tee", "bottom": "relaxed charcoal slacks", "accessory": "small geometric earrings or a simple ring"},
                {"palette": "lavender, cream, and deep gray", "silhouette": "light preppy silhouette", "top": "lavender crewneck knit over a white collar", "bottom": "clean deep-gray trousers", "accessory": "thin patterned scarf or a neat ribbon"},
            ],
            "commute": [
                {"palette": "charcoal, ivory, and cobalt blue", "silhouette": "clean tailored commute silhouette", "top": "ivory ribbed knit top with a cobalt accent", "bottom": "straight charcoal trousers", "accessory": "minimal metal watch and structured tote"},
                {"palette": "sand, white, and muted olive", "silhouette": "relaxed smart-casual silhouette", "top": "white shirt under a muted olive knit vest", "bottom": "sand-colored tapered trousers", "accessory": "small leather crossbody bag"},
                {"palette": "black, soft gray, and wine red", "silhouette": "sleek layered city silhouette", "top": "soft gray mock-neck top with a wine-red scarf accent", "bottom": "black straight-leg pants", "accessory": "simple silver earrings or cufflinks"},
                {"palette": "dusty blue, cream, and camel", "silhouette": "light professional silhouette", "top": "dusty-blue blouse or shirt with a cream knit layer", "bottom": "camel tailored trousers", "accessory": "thin belt and understated wristwatch"},
                {"palette": "deep green, black, and ivory", "silhouette": "structured modern silhouette", "top": "deep-green fine knit with a crisp ivory collar", "bottom": "black pleated trousers", "accessory": "small enamel pin and clean shoulder bag"},
                {"palette": "warm taupe, navy, and white", "silhouette": "comfortable polished silhouette", "top": "warm taupe long-sleeve tee beneath a navy overshirt", "bottom": "white or light-stone straight trousers", "accessory": "subtle patterned scarf"},
            ],
            "sport": [
                {"palette": "cobalt blue, white, and graphite", "silhouette": "clean athletic silhouette", "top": "cobalt quick-dry training tee", "bottom": "graphite track pants", "accessory": "simple sports watch and compact water bottle"},
                {"palette": "black, lime green, and gray", "silhouette": "light running silhouette", "top": "black technical long-sleeve top with lime trim", "bottom": "gray joggers", "accessory": "small sweatband and running watch"},
                {"palette": "coral, navy, and white", "silhouette": "bright casual sports silhouette", "top": "coral breathable tee under a navy sleeveless layer", "bottom": "navy athletic pants", "accessory": "minimal cap or hair band"},
                {"palette": "sage green, cream, and black", "silhouette": "relaxed outdoor exercise silhouette", "top": "sage performance polo with a cream inner layer", "bottom": "black tapered joggers", "accessory": "small crossbody sports pouch"},
                {"palette": "lavender, charcoal, and silver", "silhouette": "soft technical silhouette", "top": "lavender moisture-wicking zip collar top", "bottom": "charcoal training pants", "accessory": "reflective wrist band"},
                {"palette": "rust orange, white, and deep blue", "silhouette": "energetic training silhouette", "top": "rust-orange athletic tee with a white panel", "bottom": "deep-blue track pants", "accessory": "compact earbud case or sports watch"},
            ],
            "home": [
                {"palette": "cream, pale blue, and soft gray", "silhouette": "soft relaxed home silhouette", "top": "cream cotton lounge top", "bottom": "pale-blue relaxed pants", "accessory": "simple fabric hair band or soft slippers"},
                {"palette": "sage green, ivory, and warm brown", "silhouette": "cozy knitwear silhouette", "top": "sage knit tee over an ivory inner layer", "bottom": "warm-brown lounge trousers", "accessory": "small mug held naturally"},
                {"palette": "lavender, charcoal, and white", "silhouette": "quiet oversized silhouette", "top": "lavender oversized sweatshirt", "bottom": "charcoal soft joggers", "accessory": "thin reading glasses or a simple hair clip"},
                {"palette": "dusty rose, cream, and gray", "silhouette": "light comfortable silhouette", "top": "dusty-rose long-sleeve tee", "bottom": "cream cotton pants", "accessory": "small pendant necklace"},
                {"palette": "navy, light gray, and muted yellow", "silhouette": "casual layered home silhouette", "top": "navy striped lounge shirt", "bottom": "light-gray relaxed pants", "accessory": "muted-yellow blanket edge or soft socks"},
                {"palette": "white, olive, and soft black", "silhouette": "minimal restful silhouette", "top": "white breathable henley shirt", "bottom": "olive lounge pants", "accessory": "small wireless earbud case"},
            ],
            "daily": [
                {"palette": "denim blue, white, and red", "silhouette": "casual layered street silhouette", "top": "white tee under a denim-blue overshirt", "bottom": "dark straight-leg jeans", "accessory": "small red hair tie or keychain"},
                {"palette": "cream, black, and forest green", "silhouette": "clean relaxed silhouette", "top": "cream ribbed knit top with a forest-green collar layer", "bottom": "black tapered trousers", "accessory": "minimal canvas crossbody bag"},
                {"palette": "dusty rose, charcoal, and ivory", "silhouette": "soft modern silhouette", "top": "dusty-rose sweatshirt over an ivory tee", "bottom": "charcoal straight trousers", "accessory": "small silver pendant"},
                {"palette": "sage green, navy, and light gray", "silhouette": "easy outdoor silhouette", "top": "sage polo layered with a light-gray tee", "bottom": "navy relaxed trousers", "accessory": "simple cap or structured backpack"},
                {"palette": "lavender, white, and deep blue", "silhouette": "light casual silhouette", "top": "lavender knit tee with a white collar detail", "bottom": "deep-blue jeans", "accessory": "thin patterned scarf"},
                {"palette": "warm brown, ivory, and muted orange", "silhouette": "textured everyday silhouette", "top": "ivory henley shirt beneath a warm-brown knit vest", "bottom": "muted-orange straight trousers", "accessory": "small leather bracelet or watch"},
            ],
        }
        outer_options = self._daily_outfit_outer_options(scene, weather_kind)
        candidates: list[dict[str, str]] = []
        for base_index, base in enumerate(base_options.get(scene, base_options["daily"])):
            for outer_index, outer in enumerate(outer_options):
                candidates.append(
                    {
                        **base,
                        "scene": scene,
                        "weather": weather_kind,
                        "outer": outer,
                        "look_id": f"{scene}-{base_index + 1}-{outer_index + 1}",
                    }
                )
        return candidates

    def _select_daily_outfit_profile(
        self,
        *,
        schedule_hint: str,
        weather: str,
        date_key: str = "",
    ) -> dict[str, str]:
        # 衣柜接管：有可用裁决时优先用它（取不到就落回作者的候选表）
        wardrobe_profile = resolve_wardrobe_daily_outfit_profile(self, date_key=date_key)
        if wardrobe_profile:
            return wardrobe_profile
        scene = self._daily_outfit_scene_kind(schedule_hint, weather)
        weather_kind = self._daily_outfit_weather_kind(weather)
        candidates = self._daily_outfit_candidate_profiles(scene, weather_kind)
        if not candidates:
            return {}
        history = self._daily_outfit_rotation_history()
        fields = ("palette", "silhouette", "top", "outer", "bottom", "footwear", "accessory")
        weights = {
            "palette": 16,
            "silhouette": 12,
            "top": 20,
            "outer": 18,
            "bottom": 10,
            "footwear": 9,
            "accessory": 8,
        }

        def cooldown_score(candidate: dict[str, str]) -> int:
            score = 0
            for index, item in enumerate(history):
                previous = self._normalize_daily_outfit_profile(item.get("outfit_profile"))
                if not previous:
                    continue
                recency_weight = max(1, 8 - index)
                matching_fields = sum(
                    1
                    for field in fields
                    if candidate.get(field) and candidate.get(field) == previous.get(field)
                )
                score += sum(
                    weights[field] * recency_weight
                    for field in fields
                    if candidate.get(field) and candidate.get(field) == previous.get(field)
                )
                if candidate.get("look_id") == previous.get("look_id"):
                    score += 600 * recency_weight
                if index == 0:
                    changed_fields = len(fields) - matching_fields
                    if changed_fields < 2:
                        score += 10000
                    elif changed_fields < 3:
                        score += 800
            return score

        rotation_seed = f"{date_key or _today_key()}|{len(history)}"

        def tie_breaker(candidate: dict[str, str]) -> int:
            digest = hashlib.sha1(
                f"{rotation_seed}|{candidate.get('look_id', '')}".encode("utf-8")
            ).hexdigest()
            return int(digest[:12], 16)

        return min(candidates, key=lambda candidate: (cooldown_score(candidate), tie_breaker(candidate)))

    def _daily_outfit_rotation_reference(self) -> str:
        history = self._daily_outfit_rotation_history()
        if not history:
            return ""

        def collect(fields: dict[str, str]) -> list[str]:
            fragments: list[str] = []
            for field, label in fields.items():
                values: list[str] = []
                for item in history:
                    profile = self._normalize_daily_outfit_profile(item.get("outfit_profile"))
                    value = _single_line(profile.get(field), 56)
                    if value and value not in values:
                        values.append(value)
                    if len(values) >= 2:
                        break
                if values:
                    fragments.append(f"{label}: {' / '.join(values)}")
            return fragments

        fragments = collect(
            {
                "palette": "color palettes",
                "outer": "outer layers",
                "silhouette": "silhouettes",
            }
        )
        if not fragments:
            # 衣柜接管的投影只有 top/outer/bottom/footwear（没有 palette/silhouette），
            # 不退一步的话这句 "avoid repeating" 约束会整段从照片提示词里消失。
            fragments = collect({"top": "tops", "bottom": "bottoms", "footwear": "footwear"})
        return _single_line("; ".join(fragments), 280)

    def _format_weather_for_prompt(self) -> str:
        data = getattr(self, "data", None)
        weather = data.get("daily_weather") if isinstance(data, dict) else None
        if not isinstance(weather, dict):
            return ""
        formatter = getattr(self, "_weather_summary_text", None)
        if callable(formatter):
            try:
                text = _single_line(formatter(weather), 120)
                if text and text != "暂无天气信息":
                    return text
            except Exception:
                pass
        text = _single_line(weather.get("prompt"), 120)
        return "" if text == "暂无天气信息" else text

    def _build_daily_outfit_photo_prompt(
        self,
        diary: dict[str, Any],
        *,
        memory_context: str = "",
        outfit_profile: dict[str, Any] | None = None,
    ) -> str:
        sections = self._build_daily_outfit_photo_prompt_sections(
            diary,
            memory_context=memory_context,
            outfit_profile=outfit_profile,
        )
        prompt = (
            "Positive prompt: "
            + ", ".join(
                section.content.positive
                for section in sections
                if isinstance(section.content, PhotoPromptContent)
                and section.content.positive
            )
            + ". Negative prompt: "
            + ", ".join(
                section.content.negative
                for section in sections
                if isinstance(section.content, PhotoPromptContent)
                and section.content.negative
            )
            + "."
        )
        return _single_line(prompt, 1400)

    def _build_daily_outfit_photo_prompt_sections(
        self,
        diary: dict[str, Any],
        *,
        memory_context: str = "",
        outfit_profile: dict[str, Any] | None = None,
    ) -> tuple[PromptSection, ...]:
        persona = self._daily_outfit_role_appearance_text()
        style_name, style_instruction = self._get_photo_style_instruction()
        style_prompt = self._photo_style_prompt_en(style_name, style_instruction)
        state = self.data.get("daily_state", {}) if isinstance(getattr(self, "data", {}), dict) else {}
        weather = self._format_weather_for_prompt() if callable(getattr(self, "_format_weather_for_prompt", None)) else ""
        schedule_hint = self._daily_outfit_schedule_text()
        state_visual = self._daily_outfit_visual_state_text(state if isinstance(state, dict) else {})
        outfit_profile = self._normalize_daily_outfit_profile(outfit_profile)
        if not outfit_profile:
            outfit_profile = self._select_daily_outfit_profile(schedule_hint=schedule_hint, weather=weather)
        outfit_hint = self._daily_outfit_outfit_hint(
            schedule_hint=schedule_hint,
            weather=weather,
            outfit_profile=outfit_profile,
        )
        rotation_reference = self._daily_outfit_rotation_reference()
        scene_hint = self._daily_outfit_scene_hint(state if isinstance(state, dict) else {}, schedule_hint=schedule_hint, weather=weather)
        visual_memory = ""
        visual_memory_getter = getattr(self, "_visual_photo_memory_context", None)
        if callable(visual_memory_getter):
            try:
                visual_memory = visual_memory_getter(memory_context, limit=260)
            except Exception:
                visual_memory = ""
        diary_hint = _single_line(
            (diary or {}).get("summary")
            or (diary or {}).get("share_seed")
            or (diary or {}).get("body"),
            80,
        )
        custom = _single_line(
            runtime_persona_setting(self, "daily_outfit_photo_prompt", ""), 220
        )
        anime_style = style_name == "二次元"
        composition_style = (
            [
                "daily outfit character illustration",
                "selfie-inspired outfit portrait composition",
                "non-mirror casual illustrated portrait",
                "soft illustrated lighting",
                "clean illustrated background",
                "anime slice-of-life atmosphere",
            ]
            if anime_style
            else [
                "daily outfit selfie",
                "selfie outfit photo",
                "non-mirror handheld selfie or natural environmental outfit portrait",
                "natural phone snapshot",
                "soft natural light",
                "clean background",
                "lifelike daily atmosphere",
            ]
        )
        positive = [
            "single character",
            *composition_style[:3],
            "solo",
            "visible face",
            "complete head and hair",
            "clear eyes",
            "natural expression",
            "upper body to three-quarter body portrait, not a full-length mirror shot",
            "centered composition",
            "1:1 square cover composition",
            "safe margins around head and body",
            *composition_style[3:],
            persona or "keep the face, hairstyle, hair color, eye color, and key traits consistent with the reference image",
            outfit_hint,
            scene_hint,
            state_visual or "relaxed natural mood",
            style_prompt,
        ]
        if rotation_reference:
            positive.append(
                "wardrobe rotation: show exactly one character wearing one coherent new outfit in this single image; "
                "make that one outfit differ from recent daily outfit photos in at least two design dimensions, "
                f"but never display the old outfit or multiple alternatives; avoid repeating {rotation_reference}"
            )
        if diary_hint:
            positive.append(f"daily mood cue: {diary_hint}")
        negative = [
            "cropped head",
            "headless",
            "faceless",
            "face hidden",
            "extreme close-up",
            "arm in foreground",
            "body only",
            "outfit only",
            "back view",
            "mirror selfie",
            "full-length mirror selfie",
            "full body mirror shot",
            "standing in front of a mirror",
            "dressing room mirror",
            "phone covering face",
            "cut off face",
            "bad hands",
            "extra fingers",
            "text",
            "caption",
            "label",
            "watermark",
            "logo",
            "other people",
            "duplicate character",
            "twins",
            "multiple people",
            "multiple outfits",
            "outfit comparison",
            "before and after",
            "split screen",
            "side-by-side panels",
            "diptych",
            "collage",
            "character sheet",
            "user in frame",
            "private screen",
            "nsfw",
            "revealing outfit",
        ]
        if anime_style:
            negative.extend(
                [
                    "photorealistic",
                    "real person",
                    "live-action",
                    "realistic photography",
                    "photo-real skin texture",
                ]
            )
        if rotation_reference:
            negative.extend(
                [
                    "same outfit as a recent daily outfit photo",
                    f"repeat any recently used outfit element: {rotation_reference}",
                ]
            )
        sections = [
            prompt_section(
                key="photo.daily_outfit.user_request",
                title="user_request",
                source="photo_prompt_context",
                content=PhotoPromptContent(
                    positive=_single_line(
                        ", ".join(
                            _single_line(part, 400)
                            for part in positive
                            if _single_line(part, 400)
                        ),
                        1400,
                    ),
                    domain_source="user_request",
                    protected=True,
                ),
            ),
            prompt_section(
                key="photo.daily_outfit.contract",
                title="daily_outfit_contract",
                source="photo_prompt_context",
                content=PhotoPromptContent(
                    # These are resolved workflow exclusions rather than ambient
                    # visual context. Freeze them for this task so the N-1 resolver
                    # preserves safety and wardrobe-rotation rules.
                    negative=_single_line(", ".join(negative), 760),
                    domain_source="fixed_prompt",
                    protected=True,
                ),
            ),
        ]
        if visual_memory:
            sections.append(
                prompt_section(
                    key="photo.daily_outfit.visual_memory",
                    title="visual_memory",
                    source="photo_prompt_context",
                    content=PhotoPromptContent(
                        positive=f"visual continuity reference: {visual_memory}",
                        domain_source="visual_memory",
                    ),
                )
            )
        if custom:
            sections.append(
                prompt_section(
                    key="photo.daily_outfit.preference",
                    title="daily_outfit_preference",
                    source="photo_prompt_context",
                    content=PhotoPromptContent(
                        positive=f"additional outfit preference: {custom}",
                        domain_source="fixed_prompt",
                    ),
                )
            )
        return tuple(sections)

    def _photo_style_prompt_en(self, style_name: str, style_instruction: str = "") -> str:
        name = _single_line(style_name, 40)
        instruction = _single_line(style_instruction, 220)
        if name == "二次元":
            return "2D anime illustration style, clean detailed character art, cel-shaded rendering, soft colors, slice-of-life feeling"
        if name == "真实":
            return "realistic photography style, believable phone photo, natural lighting, realistic fabric details"
        if instruction:
            return instruction
        return "consistent visual style, natural daily-life feeling"

    def _daily_outfit_visual_state_text(self, state: dict[str, Any]) -> str:
        fragments: list[str] = []
        energy = _safe_int((state or {}).get("energy"), 70, 0, 100)
        if energy < 40:
            fragments.append("slightly sleepy, soft expression")
        elif energy > 82:
            fragments.append("fresh and energetic, bright eyes")
        mood = _single_line((state or {}).get("mood_bias"), 20).replace("黏人", "粘人")
        mood_map = {
            "开心": "gentle happy mood",
            "轻快": "light cheerful mood",
            "柔和": "soft gentle mood",
            "安静": "quiet calm mood",
            "疲惫": "tired but gentle mood",
            "困": "sleepy mood",
            "困倦": "sleepy mood",
            "低落": "subdued mood",
            "敏感": "delicate sensitive mood",
            "粘人": "soft attached mood",
        }
        if mood and mood not in {"平稳", "中性"}:
            fragments.append(mood_map.get(mood, f"{mood} mood"))
        conditions = (state or {}).get("conditions")
        visual_tokens = ("雨", "风", "冷", "热", "困", "疲", "生理期", "感冒", "发烧", "头痛", "胃", "睡", "醒")
        if isinstance(conditions, list):
            for cond in conditions[:6]:
                if not isinstance(cond, dict):
                    continue
                label = _single_line(cond.get("label") or cond.get("title") or cond.get("kind"), 30)
                if label and any(token in label for token in visual_tokens):
                    fragments.append(self._daily_outfit_condition_hint_en(label))
                if len(fragments) >= 3:
                    break
        return _single_line(", ".join(dict.fromkeys(item for item in fragments if item)), 140)

    def _daily_outfit_condition_hint_en(self, text: str) -> str:
        value = _single_line(text, 60).lower()
        if any(token in value for token in ("雨", "rain", "淋")):
            return "rainy-day softness"
        if any(token in value for token in ("风", "wind")):
            return "slight wind-blown hair"
        if any(token in value for token in ("冷", "寒", "snow")):
            return "cold-weather outfit"
        if any(token in value for token in ("热", "暑", "hot")):
            return "light breathable outfit"
        if any(token in value for token in ("困", "疲", "睡", "醒")):
            return "sleepy gentle expression"
        if any(token in value for token in ("生理期", "胃", "感冒", "发烧", "头痛")):
            return "soft low-energy expression"
        return _single_line(text, 60)

    def _daily_outfit_scene_hint(self, state: dict[str, Any], *, schedule_hint: str = "", weather: str = "") -> str:
        location = ""
        try:
            location = _single_line(self._current_location_state_text(state), 60)
            coarse = _single_line(self._coarse_roleplay_location_text(location), 40)
            location = coarse or location
        except Exception:
            location = ""
        text = f"{schedule_hint} {weather}".lower()
        if not location:
            if any(token in text for token in ("上课", "教室", "学校", "校门", "放学", "自习")):
                location = "school or commute-to-school setting"
            elif any(token in text for token in ("出门", "路上", "街", "公交", "地铁", "下班", "回家")):
                location = "outdoor street or commute setting"
            elif any(token in text for token in ("家", "房间", "卧室", "起床", "午休", "睡")):
                location = "home or bedroom setting"
            else:
                location = "daily-life setting"
        else:
            location = self._daily_outfit_location_hint_en(location)
        weather_hint = self._daily_outfit_weather_visual_hint(weather)
        return _single_line(", ".join(part for part in [location, weather_hint, "simple background, lived-in daily atmosphere"] if part), 180)

    def _daily_outfit_location_hint_en(self, location: str) -> str:
        text = _single_line(location, 80).lower()
        if any(token in text for token in ("学校", "教室", "上课", "school", "classroom")):
            return "school or classroom setting"
        if any(token in text for token in ("家", "房间", "卧室", "home", "room", "bedroom")):
            return "home or bedroom setting"
        if any(token in text for token in ("工作", "office", "公司")):
            return "workplace or office setting"
        if any(token in text for token in ("外面", "路", "街", "通勤", "outside", "street")):
            return "outdoor street or commute setting"
        return _single_line(location, 80)

    def _daily_outfit_weather_visual_hint(self, weather: str) -> str:
        text = _single_line(weather, 200).lower()
        if not text:
            return ""
        hints: list[str] = []
        if any(token in text for token in ("雨", "阵雨", "雷", "storm", "rain")):
            hints.append("rainy-day atmosphere, umbrella or damp ground, light jacket")
        if any(token in text for token in ("风", "大风", "强对流", "wind")):
            hints.append("windy feeling, slightly wind-blown hair and hem")
        if any(token in text for token in ("冷", "降温", "低温", "寒", "snow")):
            hints.append("cold weather, warm outerwear")
        if any(token in text for token in ("热", "高温", "闷", "暑", "hot")):
            hints.append("hot weather, light breathable clothes")
        return _single_line(", ".join(dict.fromkeys(hints)), 140)

    def _daily_outfit_outfit_hint(
        self,
        *,
        schedule_hint: str = "",
        weather: str = "",
        outfit_profile: dict[str, Any] | None = None,
    ) -> str:
        profile = self._normalize_daily_outfit_profile(outfit_profile)
        if profile:
            fields = (
                ("palette", "color palette"),
                ("silhouette", "silhouette"),
                ("top", "top"),
                ("outer", "outer layer"),
                ("bottom", "bottoms"),
                ("footwear", "footwear"),
                ("accessory", "accessories"),
            )
            hints = ["intentionally distinct coordinated daily outfit"]
            hints.extend(
                f"{label}: {profile[key]}"
                for key, label in fields
                if profile.get(key)
            )
            return _single_line(", ".join(hints), 620)
        text = f"{schedule_hint} {weather}".lower()
        hints: list[str] = []
        if any(token in text for token in ("校服", "上课", "教室", "学校", "高一", "自习", "放学")):
            hints.append("neat school outfit or school-uniform inspired outfit")
        if any(token in text for token in ("上班", "工作", "会议", "通勤")):
            hints.append("clean daily commute outfit")
        if any(token in text for token in ("运动", "跑步", "健身", "体育")):
            hints.append("light sporty outfit")
        if any(token in text for token in ("家", "房间", "午休", "整理", "起床")) and not hints:
            hints.append("soft casual home outfit")
        if any(token in text for token in ("睡衣", "睡前", "入睡", "刚醒")) and not any(token in text for token in ("上课", "上班", "出门", "通勤")):
            hints.append("comfortable pajamas or loungewear")
        weather_hint = self._daily_outfit_weather_visual_hint(weather)
        if weather_hint:
            hints.append(weather_hint)
        if not hints:
            hints.append("natural daily outfit, coordinated colors, clear clothing layers")
        return _single_line(", ".join(dict.fromkeys(hints)), 180)

    def _daily_outfit_role_appearance_text(self) -> str:
        persona = str(runtime_persona_setting(self, "schedule_persona_prompt", "") or "")
        recognition = str(
            runtime_persona_setting(self, "private_image_self_recognition_hint", "") or ""
        )
        labels = {
            "性别": "gender",
            "识别点": "key visual traits",
            "外貌": "appearance",
            "主要识别点": "key visual traits",
            "发型发色": "hairstyle and hair color",
            "发色": "hair color",
            "发型": "hairstyle",
            "瞳色": "eye color",
            "眼睛": "eyes",
            "服饰风格": "clothing style",
            "服装": "clothing",
            "衣着": "outfit",
        }
        parts: list[str] = []
        for line in persona.replace("\r", "\n").split("\n"):
            text = line.strip()
            if not text or ("：" not in text and ":" not in text):
                continue
            label, value = text.split("：", 1) if "：" in text else text.split(":", 1)
            label = label.strip()
            value = _single_line(value, 160)
            english_label = labels.get(label)
            if english_label and value:
                parts.append(f"{english_label}: {value}")
        if recognition:
            parts.append(f"additional visual recognition notes: {_single_line(recognition, 180)}")
        seen: set[str] = set()
        unique = []
        for item in parts:
            if item in seen:
                continue
            seen.add(item)
            unique.append(item)
        return _single_line(", ".join(unique), 620)

    def _choose_photo_workflow_name(self, kind: str) -> str:
        normalized = str(kind or "").strip().lower()
        if normalized in {"selfie", "portrait", "自拍", "人像", "edit", "改图", "修图", "重绘", "p图"}:
            return self.comfyui_selfie_workflow_name or self.comfyui_text2img_workflow_name
        return self.comfyui_text2img_workflow_name or self.comfyui_selfie_workflow_name

    def _photo_generation_trace_id(self, session_key: str, workflow_kind: str) -> str:
        seed = f"{session_key}|{workflow_kind}|{_now_ts()}|{uuid.uuid4().hex[:8]}"
        return hashlib.sha1(seed.encode("utf-8", "ignore")).hexdigest()[:10]

    def _photo_generation_file_detail(self, image_path: str) -> str:
        path_text = _path_text(image_path, 1000)
        if not path_text:
            return "path=- exists=false size=0"
        if re.match(r"^(?:https?://|data:|base64://)", path_text, flags=re.I):
            return f"path={_single_line(path_text, 120)} exists=remote size=-"
        local_text = path_text[len("file://"):] if path_text.startswith("file://") else path_text
        try:
            path = Path(local_text)
            exists = path.exists() and path.is_file()
            size = path.stat().st_size if exists else 0
            return f"path={_single_line(str(path), 160)} exists={str(exists).lower()} size={size}"
        except Exception:
            return f"path={_single_line(path_text, 120)} exists=unknown size=-"

    def _photo_generation_backend_config_summary(self) -> str:
        nai_api_getter = getattr(self, "_nai_image_api", None)
        nai_installed = nai_api_getter() is not None if callable(nai_api_getter) else False
        configured_endpoints = getattr(self, "external_image_api_endpoints", [])
        if isinstance(configured_endpoints, list) and configured_endpoints:
            queue_getter = getattr(self, "_external_image_api_endpoint_queue", None)
            endpoints: list[dict[str, Any]] = []
            if callable(queue_getter):
                try:
                    endpoints = [
                        endpoint
                        for endpoint in queue_getter(include_incomplete=True, include_disabled=True)
                        if isinstance(endpoint, dict)
                    ]
                except Exception:
                    endpoints = []
            endpoint_bits = []
            for index, endpoint in enumerate(endpoints[:6]):
                ready = not bool(self._external_image_api_endpoint_unavailable_note(endpoint))
                endpoint_bits.append(
                    f"{index + 1}:{_single_line(endpoint.get('name') or endpoint.get('model'), 40) or '-'}"
                    f"/{_single_line(endpoint.get('platform'), 20) or 'auto'}"
                    f"/{'ready' if ready else 'unready'}"
                )
            return (
                f"preferred={_single_line(runtime_persona_setting(self, 'photo_generation_backend', ''), 30) or 'auto'} "
                f"comfyui={self._comfyui_photo_available()} "
                f"sdgen={self._sdgen_photo_available()} "
                f"external={self._external_photo_available()} "
                f"external_queue={len(endpoints)} "
                f"external_queue_items={';'.join(endpoint_bits) or '-'} "
                f"backup_note={_single_line(self._backup_external_photo_unavailable_note(), 80) or '-'} "
                f"nai={nai_installed} "
                f"tool_call={self._custom_tool_photo_available()} "
                f"tool_name={_single_line(getattr(self, 'custom_photo_tool_name', ''), 80) or '-'}"
            )
        external_base = _single_line(self._normalized_external_image_api_base_url(), 120)
        if external_base:
            external_base = re.sub(r"([?&](?:key|token|access_token|api_key)=)[^&]+", r"\1***", external_base, flags=re.I)
        backup_base = _single_line(getattr(self, "backup_external_image_api_base_url", ""), 120)
        if backup_base:
            backup_base = re.sub(r"([?&](?:key|token|access_token|api_key)=)[^&]+", r"\1***", backup_base, flags=re.I)
        return (
            f"preferred={_single_line(runtime_persona_setting(self, 'photo_generation_backend', ''), 30) or 'auto'} "
            f"comfyui={self._comfyui_photo_available()} "
            f"sdgen={self._sdgen_photo_available()} "
            f"external={self._external_photo_available()} "
            f"external_platform={self._resolved_external_image_api_platform()} "
            f"external_model={_single_line(getattr(self, 'external_image_api_model', ''), 80) or '-'} "
            f"external_size={_single_line(runtime_persona_setting(self, 'external_image_api_size', ''), 40) or '-'} "
            f"external_base={external_base or '-'} "
            f"backup_external={self._backup_external_photo_available()} "
            f"backup_platform={_single_line(runtime_persona_setting(self, 'backup_external_image_api_platform', ''), 30) or '-'} "
            f"backup_model={_single_line(getattr(self, 'backup_external_image_api_model', ''), 80) or '-'} "
            f"backup_base={backup_base or '-'} "
            f"backup_note={_single_line(self._backup_external_photo_unavailable_note(), 80) or '-'} "
            f"nai={nai_installed} "
            f"tool_call={self._custom_tool_photo_available()} "
            f"tool_name={_single_line(getattr(self, 'custom_photo_tool_name', ''), 80) or '-'}"
        )

    def _photo_generation_trace_max_bytes(self) -> int:
        max_kb = _safe_int(
            runtime_persona_setting(self, "photo_generation_trace_max_size_kb", 0),
            0,
        )
        return max(0, min(102400, max_kb)) * 1024

    def _photo_generation_trace_backup_count(self) -> int:
        return max(
            0,
            min(
                20,
                _safe_int(
                    runtime_persona_setting(self, "photo_generation_trace_backup_count", 5), 5
                ),
            ),
        )

    def _photo_generation_trace_file_path(self) -> Path:
        return Path(self.data_dir) / "photo_generation_trace.txt"

    def _rotate_photo_generation_trace_files(self, path: Path) -> None:
        backup_count = self._photo_generation_trace_backup_count()
        if backup_count <= 0:
            path.unlink(missing_ok=True)
            return
        for index in range(backup_count, 0, -1):
            source = path if index == 1 else path.with_name(
                f"{path.stem}.{index - 1}{path.suffix}"
            )
            target = path.with_name(f"{path.stem}.{index}{path.suffix}")
            if source.exists():
                os.replace(source, target)

    def _sanitize_photo_generation_trace_value(
        self,
        value: Any,
        *,
        key: str = "",
        depth: int = 0,
    ) -> Any:
        if depth > 5:
            return "[truncated]"
        normalized_key = str(key or "").strip().lower()
        if any(
            token in normalized_key
            for token in ("api_key", "apikey", "authorization", "access_token", "secret", "password")
        ):
            return "***"
        if isinstance(value, dict):
            return {
                _single_line(item_key, 80): self._sanitize_photo_generation_trace_value(
                    item_value,
                    key=str(item_key),
                    depth=depth + 1,
                )
                for item_key, item_value in list(value.items())[:48]
                if _single_line(item_key, 80)
            }
        if isinstance(value, (list, tuple, set)):
            return [
                self._sanitize_photo_generation_trace_value(item, depth=depth + 1)
                for item in list(value)[:48]
            ]
        if isinstance(value, str):
            redacted = _redact_outbound_secrets(value, self)
            if normalized_key.endswith("path") or normalized_key.endswith("_path"):
                return _path_text(redacted, 1000)
            if normalized_key in {"prompt", "submitted_prompt"}:
                return redacted
            return _single_line(redacted, 1200)
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return _single_line(value, 500)

    def _append_photo_generation_trace_event(
        self,
        trace_id: str,
        stage: str,
        *,
        status: str = "ok",
        data: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
        payloads: dict[str, Any] | None = None,
    ) -> None:
        try:
            max_bytes = self._photo_generation_trace_max_bytes()
            if max_bytes <= 0:
                return
            normalized_trace = _single_line(trace_id, 80)
            normalized_stage = _single_line(stage, 80)
            if not normalized_trace or not normalized_stage:
                return
            now = _now_ts()
            states = getattr(self, "_photo_generation_trace_states", None)
            if not isinstance(states, dict):
                states = {}
                self._photo_generation_trace_states = states
            if normalized_trace not in states and len(states) >= 128:
                states.pop(next(iter(states)), None)
            state = states.setdefault(
                normalized_trace,
                {"started_at": now, "seq": 0, "context": {}},
            )
            state["seq"] = _safe_int(state.get("seq"), 0, 0) + 1
            if context:
                state["context"].update(self._sanitize_photo_generation_trace_value(context))
            payload = {
                "schema_version": 1,
                "ts": now,
                "time": datetime.fromtimestamp(now).astimezone().isoformat(timespec="milliseconds"),
                "trace": normalized_trace,
                "seq": state["seq"],
                "stage": normalized_stage,
                "status": _single_line(status, 30) or "ok",
                "elapsed_ms": max(0, int((now - _safe_float(state.get("started_at"), now, 0.0)) * 1000)),
                "context": dict(state["context"]),
                "data": self._sanitize_photo_generation_trace_value(data or {}),
            }
            line = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
            encoded_size = len(line.encode("utf-8"))
            if encoded_size > max_bytes:
                payload["context"] = {
                    "truncated": True,
                    "reason": "event_exceeds_max_size",
                }
                payload["data"] = {
                    "truncated": True,
                    "reason": "event_exceeds_max_size",
                    "original_bytes": encoded_size,
                }
                line = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
                encoded_size = len(line.encode("utf-8"))
            path = self._photo_generation_trace_file_path()
            with _PHOTO_GENERATION_TRACE_FILE_LOCK:
                path.parent.mkdir(parents=True, exist_ok=True)
                current_size = path.stat().st_size if path.exists() else 0
                if current_size and current_size + encoded_size > max_bytes:
                    self._rotate_photo_generation_trace_files(path)
                with path.open("a", encoding="utf-8", newline="\n") as handle:
                    handle.write(line)
            if normalized_stage in {"delivery_completed", "delivery_failed", "failed"}:
                states.pop(normalized_trace, None)
        except Exception as exc:
            logger.debug(
                "记录生图可观测 trace 失败: %s",
                _single_line(exc, 120),
            )

    async def _append_photo_generation_trace_event_async(
        self,
        trace_id: str,
        stage: str,
        *,
        status: str = "ok",
        data: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        # trace 文件追加涉及 path.stat() 与写盘，从事件循环移到线程池。
        await asyncio.to_thread(
            self._append_photo_generation_trace_event,
            trace_id,
            stage,
            status=status,
            data=data,
            context=context,
        )

    def _record_recent_photo_generation(
        self,
        *,
        trace_id: str,
        session_key: str,
        continuity_key: str = "",
        workflow_kind: str,
        backend: str,
        ok: bool,
        prompt_text: str,
        image_path: str = "",
        note: str = "",
        reference_image_path: str = "",
        image_size: str = "",
        elapsed_ms: int = 0,
        presets: list[str] | None = None,
        reference_used: bool = False,
        reference_candidate: dict[str, Any] | None = None,
        reference_intent: ReferenceIntent | None = None,
        reference_plan: PhotoReferencePlan | None = None,
        reference_fallback: ReferenceFallback | None = None,
        submitted_reference_ids: tuple[str, ...] = (),
        wardrobe: PhotoWardrobeDecision | None = None,
        prompt_hash: str = "",
        submitted_prompt_hash: str = "",
        prompt_path: str = "",
        complete_prompt_length: int = 0,
        submitted_prompt_length: int = 0,
        prompt_sections: dict[str, str] | None = None,
        conflicts: list[str] | None = None,
        removed_conflicts: list[str] | None = None,
        residual_conflicts: list[str] | None = None,
        reference_removed: dict[str, Any] | None = None,
        sanitizer_version: int = 0,
        detected_conflict_details: list[dict[str, Any]] | None = None,
        removed_conflict_details: list[dict[str, Any]] | None = None,
        residual_conflict_details: list[dict[str, Any]] | None = None,
        suggested_scene_preset: str = "",
        prompt_format: str = "",
        workflow_fixed_prompt_audit: dict[str, Any] | None = None,
        generation_completed: bool = False,
        failure_stage: str = "",
    ) -> None:
        try:
            reference_candidate = reference_candidate or {}
            wardrobe_payload = wardrobe.as_dict() if wardrobe is not None else {}
            intent_payload = reference_intent or ReferenceIntent((), (), "ambiguous", 0.0, "none")
            plan_payload = reference_plan or PhotoReferencePlan((), "", "", "")
            fallback_payload = reference_fallback or ReferenceFallback((), (), (), "")
            final_presets = [
                _single_line(name, 40)
                for name in (presets or [])
                if _single_line(name, 40)
            ][:1]

            def compact_audit(values: list[dict[str, Any]] | None) -> list[dict[str, str]]:
                result: list[dict[str, str]] = []
                for value in values or []:
                    if not isinstance(value, dict):
                        continue
                    item = {
                        key: _single_line(value.get(key), 120 if key == "preview" else 80)
                        for key in ("source", "section", "rule", "category", "action", "preview", "sha256")
                        if _single_line(value.get(key), 120 if key == "preview" else 80)
                    }
                    if item:
                        result.append(item)
                return result[:24]

            fixed_prompt_audit = dict(workflow_fixed_prompt_audit or {})
            item = {
                "schema_version": 3,
                "ts": _now_ts(),
                "trace": _single_line(trace_id, 40),
                "session": _single_line(session_key, 340),
                "continuity_key": self._normalize_photo_continuity_key(continuity_key),
                "kind": _single_line(workflow_kind, 30),
                "backend": _single_line(backend, 80),
                "ok": bool(ok),
                "generation_completed": bool(generation_completed),
                "failure_stage": _single_line(failure_stage, 60),
                "prompt_format": (
                    self._normalize_photo_generation_prompt_format(prompt_format)
                    if prompt_format
                    else self._photo_generation_prompt_format_mode()
                ),
                "prompt": _single_line(prompt_text, 900),
                "path": _path_text(image_path, 1000),
                "note": _single_line(note, 240),
                "reference": bool(reference_image_path),
                "reference_used": bool(reference_used),
                "reference_path": _path_text(reference_image_path, 1000),
                "reference_id": _single_line(reference_candidate.get("id"), 60),
                "reference_kind": _single_line(reference_candidate.get("kind"), 40),
                "reference_roles": list(reference_candidate.get("reference_roles") or [])[:8],
                "reference_outfit_category": _single_line(reference_candidate.get("outfit_category"), 40),
                "reference_intent": {
                    "requested_roles": list(intent_payload.requested_roles),
                    "excluded_roles": list(intent_payload.excluded_roles),
                    "continuity_mode": _single_line(intent_payload.continuity_mode, 30),
                    "confidence": round(float(intent_payload.confidence), 3),
                    "source": _single_line(intent_payload.source, 40),
                },
                "reference_plan": {
                    "bindings": [
                        {
                            "reference_id": _single_line(binding.reference_id, 80),
                            "path": _path_text(binding.path, 1000),
                            "roles": list(binding.roles),
                            "priority": int(binding.priority),
                            "preserve": list(binding.preserve),
                            "ignore": list(binding.ignore),
                            "submitted": binding.reference_id in submitted_reference_ids,
                        }
                        for binding in plan_payload.bindings
                    ],
                    "primary_reference_id": _single_line(plan_payload.primary_reference_id, 80),
                    "selection_reason": _single_line(plan_payload.selection_reason, 80),
                    "fallback_reason": _single_line(plan_payload.fallback_reason, 80),
                    "submitted_reference_ids": [
                        _single_line(reference_id, 80)
                        for reference_id in submitted_reference_ids
                        if _single_line(reference_id, 80)
                    ],
                },
                "reference_fallback": {
                    "requested_roles": list(fallback_payload.requested_roles),
                    "fulfilled_roles": list(fallback_payload.fulfilled_roles),
                    "missing_roles": list(fallback_payload.missing_roles),
                    "message": _single_line(fallback_payload.message, 260),
                },
                "image_size": _single_line(image_size, 40),
                "elapsed_ms": int(max(0, elapsed_ms or 0)),
                "presets": final_presets,
                "preset_hint": _single_line(suggested_scene_preset, 80),
                "requested_scene_preset": _single_line(suggested_scene_preset, 80),
                "scene_preset": final_presets[0] if final_presets else "",
                "wardrobe_decision_version": _safe_int(wardrobe_payload.get("decision_version"), 0),
                "wardrobe_rule_id": _single_line(wardrobe_payload.get("rule_id"), 80),
                "wardrobe_mode": _single_line(wardrobe_payload.get("mode"), 40),
                "wardrobe_source": _single_line(wardrobe_payload.get("source"), 40),
                "wardrobe_category": _single_line(wardrobe_payload.get("category"), 40),
                "outfit_locked": bool(wardrobe_payload.get("lock_outfit")),
                "daily_outfit_removed": bool(wardrobe_payload.get("remove_daily_outfit_context")),
                "wardrobe_reason": _single_line(wardrobe_payload.get("reason"), 240),
                "preset_source": _single_line(wardrobe_payload.get("preset_source"), 40),
                "suggestion_status": _single_line(wardrobe_payload.get("suggestion_status"), 60),
                "wardrobe_selected_presets": [
                    _single_line(value, 80)
                    for value in (wardrobe_payload.get("selected_presets") or [])
                    if _single_line(value, 80)
                ][:6],
                "wardrobe_adjustments": [
                    _single_line(value, 120)
                    for value in (wardrobe_payload.get("adjustments") or [])
                    if _single_line(value, 120)
                ][:12],
                "prompt_hash": _single_line(prompt_hash, 80),
                "submitted_prompt_hash": _single_line(submitted_prompt_hash, 80),
                "prompt_path": _path_text(prompt_path, 1000),
                "complete_prompt_length": _safe_int(complete_prompt_length, 0, 0),
                "submitted_prompt_length": _safe_int(submitted_prompt_length, 0, 0),
                "prompt_sections": {
                    _single_line(key, 50): _single_line(value, 240)
                    for key, value in (prompt_sections or {}).items()
                    if _single_line(key, 50) and _single_line(value, 240)
                },
                "conflicts": [_single_line(value, 120) for value in (conflicts or []) if _single_line(value, 120)][:12],
                "removed_conflicts": [
                    _single_line(value, 120)
                    for value in (removed_conflicts or [])
                    if _single_line(value, 120)
                ][:12],
                "residual_conflicts": [
                    _single_line(value, 120)
                    for value in (residual_conflicts or [])
                    if _single_line(value, 120)
                ][:12],
                "reference_removed": bool(reference_removed),
                "reference_removal": dict(reference_removed or {}),
                "sanitizer_version": _safe_int(sanitizer_version, 0, 0),
                "workflow_fixed_prompt": {
                    "scope": _single_line(fixed_prompt_audit.get("scope"), 30),
                    "config_key": _single_line(fixed_prompt_audit.get("config_key"), 80),
                    "configured": bool(fixed_prompt_audit.get("configured")),
                    "normalized": bool(fixed_prompt_audit.get("normalized")),
                    "normalization_changed": bool(
                        fixed_prompt_audit.get("normalization_changed")
                    ),
                    "conflict_cleaned": bool(fixed_prompt_audit.get("conflict_cleaned")),
                    "cleaned": bool(fixed_prompt_audit.get("cleaned")),
                    "applied": bool(fixed_prompt_audit.get("applied")),
                    "raw_length": _safe_int(fixed_prompt_audit.get("raw_length"), 0, 0),
                    "normalized_length": _safe_int(
                        fixed_prompt_audit.get("normalized_length"), 0, 0
                    ),
                    "applied_length": _safe_int(
                        fixed_prompt_audit.get("applied_length"), 0, 0
                    ),
                    "raw_sha256": _single_line(
                        fixed_prompt_audit.get("raw_sha256"), 80
                    ),
                    "normalized_sha256": _single_line(
                        fixed_prompt_audit.get("normalized_sha256"), 80
                    ),
                    "applied_sha256": _single_line(
                        fixed_prompt_audit.get("applied_sha256"), 80
                    ),
                    "removed_rules": [
                        _single_line(value, 80)
                        for value in (fixed_prompt_audit.get("removed_rules") or [])
                        if _single_line(value, 80)
                    ][:12],
                },
                "detected_conflicts": compact_audit(detected_conflict_details),
                "removed_conflict_details": compact_audit(removed_conflict_details),
                "residual_conflict_details": compact_audit(residual_conflict_details),
            }
            raw = self.data.setdefault("recent_photo_generations", [])
            if not isinstance(raw, list):
                raw = []
                self.data["recent_photo_generations"] = raw
            raw.insert(0, item)
            del raw[48:]
            self._save_data_sync(sections={"recent_photo_generations"})
        except Exception as exc:
            logger.debug("记录最近生图提示词失败: %s", _single_line(exc, 120))

    def _record_photo_reference_feedback(
        self,
        feedback_text: Any,
        *,
        continuity_key: str = "",
        session_key: str = "",
    ) -> dict[str, Any]:
        feedback = analyze_photo_reference_feedback(feedback_text)
        if not feedback.issues and not feedback.regenerate_requested:
            return {}
        data = getattr(self, "data", None)
        if not isinstance(data, dict):
            return {}
        generations = data.get("recent_photo_generations")
        if not isinstance(generations, list):
            return {}
        normalized_continuity = self._normalize_photo_continuity_key(continuity_key)
        normalized_session = _single_line(session_key, 340)
        if not normalized_continuity and not normalized_session:
            return {}
        linked: dict[str, Any] | None = None
        now = _now_ts()
        for candidate in generations:
            if not isinstance(candidate, dict):
                continue
            if normalized_continuity and self._normalize_photo_continuity_key(
                candidate.get("continuity_key")
            ) != normalized_continuity:
                continue
            if normalized_session and _single_line(candidate.get("session"), 340) != normalized_session:
                continue
            generated_at = _safe_float(candidate.get("ts"), 0.0, 0.0)
            if generated_at and now - generated_at > 6 * 3600:
                continue
            linked = candidate
            break
        if linked is None:
            return {}

        issues = list(dict.fromkeys((*linked.get("reference_feedback_issues", []), *feedback.issues)))
        linked["regeneration_requested"] = bool(
            linked.get("regeneration_requested") or feedback.regenerate_requested
        )
        linked["reference_feedback_issues"] = issues
        linked["reference_feedback_count"] = _safe_int(
            linked.get("reference_feedback_count"), 0, 0
        ) + 1
        record = {
            "schema_version": 1,
            "ts": now,
            "feedback": _single_line(feedback_text, 500),
            "regenerate_requested": feedback.regenerate_requested,
            "issues": list(feedback.issues),
            "confidence": feedback.confidence,
            "source": feedback.source,
            "generation_trace": _single_line(linked.get("trace"), 40),
            "generation_ts": linked.get("ts"),
            "continuity_key": self._normalize_photo_continuity_key(linked.get("continuity_key")),
            "session": _single_line(linked.get("session"), 340),
            "backend": _single_line(linked.get("backend"), 80),
            "final_prompt": _single_line(linked.get("prompt"), 900),
            "prompt_hash": _single_line(linked.get("prompt_hash"), 80),
            "prompt_path": _path_text(linked.get("prompt_path"), 1000),
            "reference_intent": deepcopy(linked.get("reference_intent") or {}),
            "reference_plan": deepcopy(linked.get("reference_plan") or {}),
            "reference_fallback": deepcopy(linked.get("reference_fallback") or {}),
        }
        records = data.setdefault("photo_reference_feedback", [])
        if not isinstance(records, list):
            records = []
            data["photo_reference_feedback"] = records
        records.insert(0, record)
        del records[96:]
        self._save_data_sync(
            sections={"recent_photo_generations", "photo_reference_feedback"}
        )
        return record

    def _record_photo_reference_feedback_from_event(self, event: Any) -> dict[str, Any]:
        if event is None or bool(getattr(event, "_private_companion_photo_feedback_recorded", False)):
            return {}
        try:
            setattr(event, "_private_companion_photo_feedback_recorded", True)
        except Exception:
            pass
        text = str(getattr(event, "message_str", "") or "")
        try:
            user_id = str(event.get_sender_id())
        except Exception:
            user_id = ""
        session = _single_line(getattr(event, "unified_msg_origin", ""), 340)
        continuity_key = self._compose_photo_continuity_key(session, user_id)
        if not continuity_key:
            return {}
        return self._record_photo_reference_feedback(
            text,
            continuity_key=continuity_key,
        )

    def _write_photo_prompt_debug_file(
        self,
        *,
        trace_id: str,
        session_key: str,
        workflow_kind: str,
        base_prompt: str,
        scene_context_before: str,
        scene_context_after: str,
        reference: dict[str, Any] | None,
        wardrobe: PhotoWardrobeDecision,
        presets: list[str],
        prompt_sections_before: dict[str, Any],
        prompt_sections: dict[str, str],
        prompt_sections_after: dict[str, Any],
        final_prompt: str,
        submitted_prompt: str = "",
        conflicts: list[str],
        removed_conflicts: list[str],
        residual_conflicts: list[str],
        detected_conflict_details: list[dict[str, Any]],
        removed_conflict_details: list[dict[str, Any]],
        residual_conflict_details: list[dict[str, Any]],
        reference_removed: dict[str, Any] | None,
        sanitizer_version: int,
        reference_intent: ReferenceIntent | None = None,
        reference_plan: PhotoReferencePlan | None = None,
        reference_fallback: ReferenceFallback | None = None,
        suggested_scene_preset: str = "",
        prompt_format: str = "",
        workflow_fixed_prompt_audit: dict[str, Any] | None = None,
    ) -> tuple[str, str]:
        prompt_hash = hashlib.sha256(str(final_prompt or "").encode("utf-8", "ignore")).hexdigest()
        submitted_prompt_hash = hashlib.sha256(
            str(submitted_prompt or final_prompt or "").encode("utf-8", "ignore")
        ).hexdigest()
        if self._photo_generation_trace_max_bytes() <= 0:
            return "", prompt_hash
        try:
            root = Path(self.data_dir) / "photo_prompt_debug"
            root.mkdir(parents=True, exist_ok=True)
            now = datetime.now()
            filename = f"{now.strftime('%Y%m%d_%H%M%S_%f')}_{_single_line(trace_id, 40) or 'photo'}.json"
            path = root / filename

            def redact(value: Any) -> Any:
                if isinstance(value, str):
                    return _redact_outbound_secrets(value, self)
                if isinstance(value, dict):
                    return {str(key): redact(item) for key, item in value.items()}
                if isinstance(value, (list, tuple)):
                    return [redact(item) for item in value]
                return value

            reference_payload = {
                "id": _single_line((reference or {}).get("id"), 60),
                "kind": _single_line((reference or {}).get("kind"), 40),
                "path": _path_text((reference or {}).get("path"), 1000),
                "roles": list((reference or {}).get("reference_roles") or []),
                "outfit_category": _single_line((reference or {}).get("outfit_category"), 40),
                "outfit_lock_default": bool((reference or {}).get("outfit_lock_default")),
                "preferred_preset": _single_line((reference or {}).get("preferred_preset"), 60),
                "metadata_source": _single_line((reference or {}).get("metadata_source"), 30),
            }
            intent_payload = reference_intent or ReferenceIntent((), (), "ambiguous", 0.0, "none")
            plan_payload = reference_plan or PhotoReferencePlan((), "", "", "")
            fallback_payload = reference_fallback or ReferenceFallback((), (), (), "")
            payload = redact(
                {
                    "schema_version": 4,
                    "created_at": now.isoformat(timespec="seconds"),
                    "trace": _single_line(trace_id, 40),
                    "session": _single_line(session_key, 340),
                    "workflow_kind": _single_line(workflow_kind, 40),
                    "preset_hint": _single_line(suggested_scene_preset, 80),
                    "requested_scene_preset": _single_line(suggested_scene_preset, 80),
                    "prompt_format": (
                        self._normalize_photo_generation_prompt_format(prompt_format)
                        if prompt_format
                        else self._photo_generation_prompt_format_mode()
                    ),
                    "base_prompt": base_prompt,
                    "scene_context_before": scene_context_before,
                    "scene_context_after": scene_context_after,
                    "reference": reference_payload,
                    "reference_intent": {
                        "requested_roles": list(intent_payload.requested_roles),
                        "excluded_roles": list(intent_payload.excluded_roles),
                        "continuity_mode": intent_payload.continuity_mode,
                        "confidence": intent_payload.confidence,
                        "source": intent_payload.source,
                    },
                    "reference_plan": {
                        "bindings": [
                            {
                                "reference_id": binding.reference_id,
                                "path": binding.path,
                                "roles": list(binding.roles),
                                "priority": binding.priority,
                                "preserve": list(binding.preserve),
                                "ignore": list(binding.ignore),
                            }
                            for binding in plan_payload.bindings
                        ],
                        "primary_reference_id": plan_payload.primary_reference_id,
                        "selection_reason": plan_payload.selection_reason,
                        "fallback_reason": plan_payload.fallback_reason,
                    },
                    "reference_fallback": {
                        "requested_roles": list(fallback_payload.requested_roles),
                        "fulfilled_roles": list(fallback_payload.fulfilled_roles),
                        "missing_roles": list(fallback_payload.missing_roles),
                        "message": fallback_payload.message,
                    },
                    "wardrobe_decision": wardrobe.as_dict(),
                    "presets": list(presets)[:1],
                    "prompt_sections_before": prompt_sections_before,
                    "prompt_sections": prompt_sections,
                    "prompt_sections_after": prompt_sections_after,
                    "conflicts": list(conflicts),
                    "removed_conflicts": list(removed_conflicts),
                    "residual_conflicts": list(residual_conflicts),
                    "detected_conflicts": list(detected_conflict_details),
                    "removed_conflict_details": list(removed_conflict_details),
                    "residual_conflict_details": list(residual_conflict_details),
                    "reference_removed": dict(reference_removed or {}),
                    "sanitizer_version": _safe_int(sanitizer_version, 0, 0),
                    "workflow_fixed_prompt": dict(workflow_fixed_prompt_audit or {}),
                    "final_prompt": final_prompt,
                    "final_prompt_length": len(str(final_prompt or "")),
                    "final_prompt_sha256": prompt_hash,
                    "submitted_prompt_length": len(str(submitted_prompt or final_prompt or "")),
                    "submitted_prompt_sha256": submitted_prompt_hash,
                }
            )
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            debug_files = sorted(
                root.glob("*.json"),
                key=lambda item: item.name,
                reverse=True,
            )
            for stale in debug_files[40:]:
                try:
                    stale.unlink()
                except OSError:
                    pass
            return str(path), prompt_hash
        except Exception as exc:
            logger.debug(
                "写入完整生图提示词调试文件失败: trace=%s error=%s",
                _single_line(trace_id, 40),
                _single_line(exc, 160),
            )
            return "", prompt_hash

    def _photo_generation_result_metadata(
        self,
        *,
        image_path: str = "",
        session_key: str = "",
    ) -> dict[str, Any]:
        raw = self.data.get("recent_photo_generations") if isinstance(getattr(self, "data", None), dict) else []
        if not isinstance(raw, list):
            return {}
        target_path = _path_text(image_path, 1000)
        target_session = _single_line(session_key, 340)
        for item in raw:
            if not isinstance(item, dict):
                continue
            if target_path and _path_text(item.get("path"), 1000) != target_path:
                continue
            if not target_path and target_session and _single_line(item.get("session"), 340) != target_session:
                continue
            return dict(item)
        return {}

    @staticmethod
    def _normalize_photo_continuity_key(value: Any) -> str:
        key = _single_line(value, 340).strip()
        if key.startswith("tool_photo_"):
            key = key[len("tool_photo_") :]
        return key

    @classmethod
    def _compose_photo_continuity_key(cls, session_key: Any, user_id: Any) -> str:
        session = cls._normalize_photo_continuity_key(session_key)
        sender = _single_line(user_id, 80).strip()
        if not session or not sender:
            return ""
        return _single_line(f"{session}|sender={sender}", 340)

    @classmethod
    def _photo_continuity_store_key(cls, continuity_key: Any) -> str:
        normalized = cls._normalize_photo_continuity_key(continuity_key)
        if not normalized:
            return ""
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]

    def _remember_sent_photo_continuity_reference(self, item: dict[str, Any]) -> None:
        if not isinstance(item, dict) or not bool(item.get("ok")) or not bool(item.get("sent")):
            return
        continuity_key = self._normalize_photo_continuity_key(item.get("continuity_key"))
        store_key = self._photo_continuity_store_key(continuity_key)
        image_path = _path_text(item.get("path"), 1000)
        if not store_key or not image_path:
            return
        try:
            path = Path(image_path).expanduser().resolve()
            if (
                not path.exists()
                or not path.is_file()
                or path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}
            ):
                return
        except (OSError, ValueError):
            return

        final_presets = [
            _single_line(value, 80)
            for value in (item.get("presets") if isinstance(item.get("presets"), list) else [])
            if _single_line(value, 80)
        ]
        final_scene_preset = (
            final_presets[0]
            if final_presets
            else (
                _single_line(item.get("scene_preset"), 80)
                if _safe_int(item.get("schema_version"), 1) >= 2
                else ""
            )
        )
        now = _now_ts()
        raw_store = self.data.setdefault("recent_photo_continuity", {})
        if not isinstance(raw_store, dict):
            raw_store = {}
            self.data["recent_photo_continuity"] = raw_store
        raw_store[store_key] = {
            "schema_version": 2,
            "continuity_key": continuity_key,
            "sent_at": now,
            "generated_at": _safe_float(item.get("ts"), now),
            "path": str(path),
            "kind": _single_line(item.get("kind"), 30),
            "intent_kind": _single_line(item.get("intent_kind"), 30),
            "prompt": _single_line(item.get("prompt"), 900),
            "caption": _single_line(item.get("caption"), 160),
            "scene_preset": final_scene_preset,
            "preset_source": _single_line(item.get("preset_source"), 40),
            "reference_path": _path_text(item.get("reference_path"), 1000),
            "wardrobe_mode": _single_line(item.get("wardrobe_mode"), 40),
            "wardrobe_category": _single_line(item.get("wardrobe_category"), 40),
            "reference_roles": list(item.get("reference_roles") or []),
        }

        keep_after = now - 24 * 3600
        for key, record in list(raw_store.items()):
            if not isinstance(record, dict) or _safe_float(record.get("sent_at"), 0) < keep_after:
                raw_store.pop(key, None)
        if len(raw_store) > 96:
            ordered = sorted(
                raw_store.items(),
                key=lambda pair: _safe_float(pair[1].get("sent_at"), 0) if isinstance(pair[1], dict) else 0,
                reverse=True,
            )
            self.data["recent_photo_continuity"] = dict(ordered[:96])

    def _recent_sent_photo_continuity_candidate(
        self,
        continuity_key: Any,
        *,
        now: float | None = None,
        max_age_seconds: float = 45 * 60,
    ) -> dict[str, str]:
        normalized = self._normalize_photo_continuity_key(continuity_key)
        store_key = self._photo_continuity_store_key(normalized)
        data = getattr(self, "data", {})
        raw_store = data.get("recent_photo_continuity") if isinstance(data, dict) else {}
        record = raw_store.get(store_key) if store_key and isinstance(raw_store, dict) else None
        if not isinstance(record, dict):
            return {}
        if self._normalize_photo_continuity_key(record.get("continuity_key")) != normalized:
            return {}
        check_now = _now_ts() if now is None else float(now)
        sent_at = _safe_float(record.get("sent_at"), 0)
        age = check_now - sent_at
        if sent_at <= 0 or age < -300 or age > max(60.0, float(max_age_seconds)):
            return {}
        image_path = _path_text(record.get("path"), 1000)
        try:
            path = Path(image_path).expanduser().resolve()
            if (
                not path.exists()
                or not path.is_file()
                or path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}
            ):
                return {}
        except (OSError, ValueError):
            return {}
        previous_prompt = _single_line(record.get("prompt"), 360)
        previous_caption = _single_line(record.get("caption"), 120)
        record_schema_version = _safe_int(record.get("schema_version"), 1)
        previous_scene = (
            _single_line(record.get("scene_preset"), 80)
            if record_schema_version >= 2
            else ""
        )
        details = "；".join(
            part
            for part in (
                f"上一张画面要求：{previous_prompt}" if previous_prompt else "",
                f"上一张附言：{previous_caption}" if previous_caption else "",
                f"上一张场景预设：{previous_scene}" if previous_scene else "",
            )
            if part
        )
        note = (
            "同一会话刚刚已经实际发送的上一张成图；只有当前要求是在原画面上自然续拍，主要改变动作、表情、视线、机位或近似构图时使用；"
            "若明确更换人物、服装、地点、时间、整体场景或另起主题则不要使用"
        )
        if details:
            note = f"{note}；{details}"
        return {
            "id": "recent_sent_photo",
            "path": str(path),
            "source": str(path),
            "kind": "recent_sent_photo",
            "note": _single_line(note, 760),
            "reference_roles": ["identity", "outfit", "scene", "continuity"],
            "outfit_category": _single_line(record.get("wardrobe_category"), 40),
            "outfit_lock_default": True,
            "preferred_preset": previous_scene,
            "metadata_source": "runtime",
        }

    def _annotate_recent_photo_generation(
        self,
        *,
        image_path: str = "",
        session_key: str = "",
        trigger: str = "",
        intent_kind: str = "",
        sent: bool | None = None,
        caption: str = "",
        preset_hint: str = "",
        tool_name: str = "",
    ) -> None:
        try:
            raw = self.data.get("recent_photo_generations")
            if not isinstance(raw, list):
                return
            target_path = _path_text(image_path, 1000)
            target_session = _single_line(session_key, 340)
            for item in raw:
                if not isinstance(item, dict):
                    continue
                same_path = bool(target_path and _path_text(item.get("path"), 1000) == target_path)
                same_session = bool(target_session and _single_line(item.get("session"), 340) == target_session)
                if not (same_path if target_path else same_session):
                    continue
                if trigger:
                    item["trigger"] = _single_line(trigger, 40)
                if intent_kind:
                    item["intent_kind"] = _single_line(intent_kind, 30)
                if sent is not None:
                    item["sent"] = bool(sent)
                if caption:
                    item["caption"] = _single_line(caption, 120)
                if preset_hint:
                    item["preset_hint"] = _single_line(preset_hint, 80)
                if tool_name:
                    item["tool_name"] = _single_line(tool_name, 60)
                item["annotated_at"] = _now_ts()
                if sent is True:
                    self._remember_sent_photo_continuity_reference(item)
                save_sections = {"recent_photo_generations"}
                if sent is True:
                    save_sections.add("recent_photo_continuity")
                self._save_data_sync(sections=save_sections)
                return
        except Exception as exc:
            logger.debug("标注最近生图记录失败: %s", _single_line(exc, 120))

    def _apply_photo_generation_fixed_prompt(self, prompt_text: str) -> str:
        prompt = str(prompt_text or "").strip()
        fixed = _single_line(
            runtime_persona_setting(self, "photo_generation_fixed_prompt", ""), 500
        )
        if not fixed:
            return prompt
        if fixed in prompt:
            return _single_line(prompt, 1800)
        return _single_line(f"{prompt}\n\nAdditional fixed prompt: {fixed}".strip(), 1800)

    @staticmethod
    def _sanitize_photo_generation_fixed_prompt_config(value: Any, *, limit: int = 5000) -> str:
        text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
        text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", " ", text)
        text = re.sub(r"[\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]", "", text)
        text = re.sub(
            r"</?(?:instruction|system|assistant|user|tool|memorycompanion-context)\b[^>]*>",
            " ",
            text,
            flags=re.I,
        )
        text = re.sub(
            r"(?im)^\s*\[(?:user image request|reference and wardrobe ruling|"
            r"scene, style and final preset|composition and continuity)\]\s*$",
            " ",
            text,
        )
        return _single_line(text, max(0, int(limit or 0)))

    def _photo_generation_workflow_fixed_prompt_section(
        self,
        workflow_kind: str,
    ) -> tuple[PromptSection, dict[str, Any]]:
        normalized = str(workflow_kind or "").strip().lower()
        if normalized in {"edit", "改图", "修图", "重绘", "p图"}:
            scope = "edit"
            config_key = "photo_generation_edit_fixed_prompt"
            label = "Additional image-edit fixed prompt"
        elif normalized in {"selfie", "portrait", "自拍", "人像"}:
            scope = "selfie"
            config_key = "photo_generation_selfie_fixed_prompt"
            label = "Additional selfie fixed prompt"
        else:
            scope = "text2img"
            config_key = "photo_generation_text2img_fixed_prompt"
            label = "Additional text-to-image fixed prompt"

        raw = str(runtime_persona_setting(self, config_key, "") or "")
        normalized_prompt = self._sanitize_photo_generation_fixed_prompt_config(raw)
        positive, negative = self._photo_generation_semantic_prompt_parts(normalized_prompt)
        section = prompt_section(
            key=f"photo.workflow_fixed.{scope}",
            title="workflow_fixed_prompt",
            source="photo_prompt_context",
            content=PhotoPromptContent(
                positive=f"{label}: {positive}" if positive else "",
                negative=negative,
                domain_source="fixed_prompt",
                protected=True,
                sanitize_conflicts=True,
            ),
        )
        raw_trimmed = raw.strip()
        audit = {
            "scope": scope,
            "config_key": config_key,
            "configured": bool(raw_trimmed),
            "normalized": bool(normalized_prompt),
            "normalization_changed": raw_trimmed != normalized_prompt,
            "raw_length": len(raw),
            "normalized_length": len(normalized_prompt),
            "raw_sha256": hashlib.sha256(raw.encode("utf-8", "ignore")).hexdigest()
            if raw
            else "",
            "normalized_sha256": hashlib.sha256(
                normalized_prompt.encode("utf-8", "ignore")
            ).hexdigest()
            if normalized_prompt
            else "",
        }
        return section, audit

    @staticmethod
    def _normalize_photo_generation_prompt_format(value: Any) -> str:
        text = str(value or "traditional").strip().lower().replace("-", "_")
        if text in {"nai", "novelai", "nai4", "nai_4", "nai45", "nai_diffusion", "naidiffusion", "nai联动", "nai插件联动"}:
            return "nai"
        if text in {"natural", "natural_language", "description", "prose", "自然语言", "自然语言描述"}:
            return "natural_language"
        return "traditional"

    @staticmethod
    def _normalize_bot_relationship_cards(value: Any) -> list[str]:
        return normalize_bot_relationship_cards(value)

    def _photo_generation_prompt_format_mode(self) -> str:
        return self._normalize_photo_generation_prompt_format(
            runtime_persona_setting(self, "photo_generation_prompt_format", "traditional")
        )

    @staticmethod
    def _normalize_photo_generation_negative_prompt_mode(value: Any) -> str:
        normalized = str(value or "safe_default").strip().lower().replace("-", "_")
        if normalized in {"merge", "append", "custom_merge", "合并", "合并自定义"}:
            return "merge"
        if normalized in {"replace", "override", "custom_replace", "替换", "完全替换"}:
            return "replace"
        return "safe_default"

    def _photo_generation_negative_prompt_mode(self) -> str:
        return self._normalize_photo_generation_negative_prompt_mode(
            runtime_persona_setting(
                self, "photo_generation_negative_prompt_mode", "safe_default"
            )
        )

    @classmethod
    def _sanitize_photo_generation_negative_prompt_config(
        cls,
        value: Any,
        *,
        limit: int = 3000,
    ) -> str:
        raw = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
        text = cls._sanitize_photo_generation_fixed_prompt_config(
            raw.replace("\n", ", "),
            limit=limit,
        )
        if not text:
            return ""
        negative_match = re.search(r"negative\s+prompt\s*:\s*(.*)$", text, flags=re.I | re.S)
        if negative_match:
            text = negative_match.group(1)
        else:
            text = re.sub(r"^(?:avoid|negative|负面提示词)\s*[：:]?\s*", "", text, flags=re.I)
        values: list[str] = []
        seen: set[str] = set()
        for raw_part in re.split(r"(?:\r?\n+|[,，;；]+)", text):
            part = re.sub(r"\s+", " ", raw_part).strip(" .。")
            if not part:
                continue
            _is_negative, content = cls._photo_generation_negative_clause_content(part)
            content = content or part
            key = content.casefold()
            if key in seen:
                continue
            seen.add(key)
            values.append(content)
        return _single_line(", ".join(values), limit)

    def _photo_generation_custom_negative_prompt(self, workflow_kind: str) -> str:
        raw_kind = str(workflow_kind or "").strip().lower()
        if raw_kind in {"edit", "改图", "修图", "重绘", "p图"}:
            normalized = "edit"
        elif raw_kind in {"selfie", "portrait", "自拍", "人像", "sticker", "emoji", "meme", "表情包", "贴纸"}:
            normalized = "selfie"
        else:
            normalized = "text2img"
        scoped_key = {
            "text2img": "photo_generation_text2img_negative_prompt",
            "selfie": "photo_generation_selfie_negative_prompt",
            "edit": "photo_generation_edit_negative_prompt",
        }[normalized]
        values = (
            runtime_persona_setting(self, "photo_generation_negative_prompt", ""),
            runtime_persona_setting(self, scoped_key, ""),
        )
        combined: list[str] = []
        seen: set[str] = set()
        for value in values:
            sanitized = self._sanitize_photo_generation_negative_prompt_config(value)
            for part in (item.strip() for item in sanitized.split(",")):
                key = part.casefold()
                if not part or key in seen:
                    continue
                seen.add(key)
                combined.append(part)
        return _single_line(", ".join(combined), 5000)

    def _apply_photo_generation_negative_prompt_policy(
        self,
        sections: tuple[PromptSection, ...],
        workflow_kind: str,
    ) -> tuple[PromptSection, ...]:
        mode = self._photo_generation_negative_prompt_mode()
        adjusted = list(sections)
        if mode == "replace":
            replaceable_names = {
                "natural_language_contract",
                "daily_outfit_contract",
                "edit_contract",
                "composition",
                "subject_count",
            }
            adjusted = [
                replace(
                    section,
                    content=replace(section.content, negative=""),
                )
                if section.title in replaceable_names
                and isinstance(section.content, PhotoPromptContent)
                and section.content.negative
                else section
                for section in adjusted
            ]
        if mode in {"merge", "replace"}:
            custom_negative = self._photo_generation_custom_negative_prompt(workflow_kind)
            if custom_negative:
                adjusted.append(
                    prompt_section(
                        key="photo.custom_negative_prompt",
                        title="custom_negative_prompt",
                        source="photo_prompt_context",
                        content=PhotoPromptContent(
                            negative=custom_negative,
                            domain_source="fixed_prompt",
                            protected=True,
                            sanitize_conflicts=True,
                        ),
                    )
                )
        return tuple(adjusted)

    def _photo_generation_prompt_format_instruction(self) -> str:
        mode = self._photo_generation_prompt_format_mode()
        if mode == "nai":
            return (
                "使用 NAI（NovelAI 4/4.5）联动写法：以英文 danbooru 风格标签为主、逗号分隔，"
                "精简到能讲清构图即可，不堆砌重复或无意义 tag。"
                "加权用花括号 {tag} 提升、方括号 [tag] 降低，可叠层；也可用 权重::标签:: "
                "对一个或多个标签整体加权（示例 1.5::red dress, long dress::），"
                "可以使用较高权重值（2、5 甚至 10 以上）强调关键元素。"
                "移除物体或翻转概念用负向权重（示例 -1::unwanted object::）；"
                "混合 3 个以上画师风格时可加 -2::artist collaboration:: 降低鬼图概率。"
                "已知二次元角色用 角色名 (作品名) 形式（示例 texas the omertosa (arknights)），"
                "特征不全时多补几个描述词；情绪词有效，可加入增强表情。"
                "多角色（最多 6 名）时每个角色分别用 {人物 [该角色的画风/动作/神态/外貌 tags] 人物} 包裹，"
                "块内两个占位符‘人物’不能删除，可用 {位置中} {位置左} {位置右上} 等位置标签"
                "（5x5 共 25 种）指定站位，角色专属负面词条写 ntags = [tags]；"
                "角色互动动作用 source#/target#/mutual# 前缀（示例 source#hug 发起拥抱，"
                "target#hug 被拥抱，mutual#hug 互相拥抱）。"
                "需要画面英文文字用 Text: 内容, ；不需要文字加 no text, 。"
                "直接输出可投喂生图后端的提示词字符串，不要输出 Positive prompt/Negative prompt 标题，"
                "也不要写解释性段落。"
            )
        if mode == "natural_language":
            return (
                "自然语言描述：连贯具体的英文句子，覆盖主体、外观、动作、场景、光线、镜头、构图与风格；"
                "不要标签堆或权重语法；要避免的内容在末句用 Avoid ... 表达。"
            )
        return (
            "传统文生图写法：英文短词组按主体、外观、服装、场景、光线、镜头、构图、风格排列，逗号分隔；"
            "用 Positive prompt: ... Negative prompt: ... 结构，不写解释段落。"
        )

    def _photo_tool_prompt_format_instruction(self) -> str:
        """Compact format hint for the request-local tool description."""
        mode = self._photo_generation_prompt_format_mode()
        if mode == "nai":
            return (
                "NAI 4/4.5：使用精简的英文 danbooru 标签并以逗号分隔；"
                "用 {tag} 加权、[tag] 降权、1.5::tags:: 数值加权，负权重移除不需要的概念；"
                "已知角色写作 人物名(作品名)，可使用情绪标签；"
                "多角色分别写作 {人物[tags]人物}，互动使用 source#/target#/mutual#。"
                "直接输出可投喂后端的提示词，不加 Positive/Negative 标题或解释。"
                "用户明确给出的画面标签应原样保留，不得无依据删减、替换或软化；同时遵守生图后端的年龄、权限与安全边界。"
            )
        if mode == "natural_language":
            return (
                "使用自然语言描述：用连贯具体的英文句子描述主体、外观、动作、场景、光线、镜头、构图与风格；"
                "不要标签堆、权重语法或 Positive/Negative 标题；需要避免的内容在末句用 Avoid ... 表达。"
                "用户明确给出的画面要素应尽量原样保留，不得无依据删减、替换或软化；同时遵守生图后端的年龄、权限与安全边界。"
            )
        return (
            "使用英文短词组，按主体、外观、服装、场景、光线、镜头、构图、风格排列并以逗号分隔；"
            "使用 Positive prompt: ... Negative prompt: ... 结构，不写解释。"
            "用户明确给出的画面标签应尽量原样保留，不得无依据删减、替换或软化；同时遵守生图后端的年龄、权限与安全边界。"
        )

    @staticmethod
    def _photo_generation_negative_clause_content(clause: str) -> tuple[bool, str]:
        text = re.sub(r"\s+", " ", str(clause or "")).strip(" ,.;；。，")
        if not text:
            return False, ""
        text = re.sub(
            r"^(?:user\s+request|requested\s+final\s+image|用户要求|画面要求)\s*[：:]\s*",
            "",
            text,
            flags=re.I,
        ).strip()
        prefix = re.compile(
            r"^(?:请)?(?:不要|别(?:再)?(?:穿|用|选)?|不想穿|不穿|不用|不是|无需|无须|避免|禁止|不许|不得|排除|拒绝|去掉|脱下|取消)\s*"
            r"|^(?:do\s+not|don't|not|avoid|without|no|exclude|skip|remove)\s+",
            flags=re.I,
        )
        match = prefix.match(text)
        if match:
            return True, text[match.end():].strip(" ,.;；。，")
        postfix = re.compile(
            r"\s*(?:不要(?:了)?|别穿|不穿|不用|算了|就算了|除外|排除|取消|not|no)\s*$",
            flags=re.I,
        )
        match = postfix.search(text)
        if match:
            return True, text[:match.start()].strip(" ,.;；。，")
        return False, text

    @classmethod
    def _photo_generation_semantic_prompt_parts(cls, prompt_text: str) -> tuple[str, str]:
        """Separate positive request clauses from explicit exclusions without losing mixed requests."""
        prompt = str(prompt_text or "").strip()
        positive_match = re.search(
            r"positive\s+prompt\s*:\s*(.*?)(?=negative\s+prompt\s*:|$)",
            prompt,
            flags=re.I | re.S,
        )
        if positive_match:
            positive_raw = positive_match.group(1).strip()
            negative_match = re.search(r"negative\s+prompt\s*:\s*(.*)$", prompt, flags=re.I | re.S)
            negative_raw = negative_match.group(1).strip() if negative_match else ""
        else:
            positive_raw = prompt
            negative_raw = ""

        positive_parts: list[str] = []
        negative_parts: list[str] = []

        def add_clause(raw_clause: str) -> None:
            clause = re.sub(r"\s+", " ", str(raw_clause or "")).strip(" ,.;；。，")
            if not clause:
                return
            is_negative, content = cls._photo_generation_negative_clause_content(clause)
            if is_negative:
                transition = re.search(
                    r"(?:但|而|不过|可是)?(?:改穿|换成|换上|换为|改为|要穿|穿上|而要)"
                    r"|\b(?:but|instead|and)\s+(?:wear|change\s+into|switch\s+to|put\s+on)\b",
                    content,
                    flags=re.I,
                )
                if transition and transition.start() > 0:
                    excluded = content[:transition.start()].strip(" ,.;；。，")
                    requested = content[transition.start():].strip(" ,.;；。，")
                    if excluded:
                        negative_parts.append(excluded)
                    if requested:
                        positive_parts.append(requested)
                    return
                if content:
                    negative_parts.append(content)
                return
            if content:
                positive_parts.append(content)

        for clause in re.split(r"(?:\r?\n+|[。；;，,]+|(?<=[.!?])\s+)", positive_raw):
            add_clause(clause)
        for clause in re.split(r"(?:\r?\n+|[。；;，,]+|(?<=[.!?])\s+)", negative_raw):
            cleaned = re.sub(r"\s+", " ", str(clause or "")).strip(" ,.;；。，")
            if not cleaned:
                continue
            _, content = cls._photo_generation_negative_clause_content(cleaned)
            if content:
                negative_parts.append(content)

        return ", ".join(dict.fromkeys(positive_parts)), ", ".join(dict.fromkeys(negative_parts))

    def _apply_photo_generation_prompt_format(
        self,
        prompt_text: str,
        *,
        prompt_format: str = "",
    ) -> str:
        prompt = str(prompt_text or "").strip()
        if not prompt:
            return ""
        mode = (
            self._normalize_photo_generation_prompt_format(prompt_format)
            if prompt_format
            else self._photo_generation_prompt_format_mode()
        )
        if mode == "nai":
            # Preserve NovelAI inline syntax ({}/[], weight::tags::, multi-character blocks) as authored.
            positive_match = re.search(
                r"positive\s+prompt\s*:\s*(.*?)(?=negative\s+prompt\s*:|$)",
                prompt,
                flags=re.I | re.S,
            )
            negative_match = re.search(r"negative\s+prompt\s*:\s*(.*)$", prompt, flags=re.I | re.S)
            if positive_match or negative_match:
                if positive_match:
                    positive_raw = positive_match.group(1).strip()
                elif negative_match:
                    positive_raw = prompt[:negative_match.start()].strip(" \t\r\n,;。；")
                else:
                    positive_raw = prompt
                negative_raw = negative_match.group(1).strip(" \t\r\n.,;!?。；！") if negative_match else ""
                separator = ", " if positive_raw and negative_raw else ""
                prompt = positive_raw + (f"{separator}-1.5::{negative_raw}::" if negative_raw else "")
            return self._photo_prompt_clip(prompt, 2400, preserve_tail=True)
        positive, negative = self._photo_generation_semantic_prompt_parts(prompt)
        positive = positive or "the requested image"
        if mode == "natural_language":
            natural = f"Create a single coherent image showing {positive}."
            if negative:
                natural += f" Avoid {negative}."
            return self._photo_prompt_clip(natural, 6000, preserve_tail=True)
        formatted = f"Positive prompt: {positive}."
        if negative:
            formatted += f" Negative prompt: {negative}."
        return self._photo_prompt_clip(formatted, 6000, preserve_tail=True)

    def _photo_generation_selfie_schedule_scene_hint(
        self,
        user_id: str = "",
        *,
        include_dialogue_outfit: bool = True,
    ) -> str:
        snapshot_builder = getattr(self, "_build_companion_scene_snapshot", None)
        snapshot_formatter = getattr(self, "_format_companion_scene_snapshot", None)
        if callable(snapshot_builder) and callable(snapshot_formatter):
            try:
                scene_user: dict[str, Any] | None = None
                normalized_user_id = _single_line(user_id, 80)
                if normalized_user_id:
                    user_getter = getattr(self, "_get_user", None)
                    if callable(user_getter):
                        try:
                            candidate = user_getter(normalized_user_id)
                            if isinstance(candidate, dict):
                                scene_user = dict(candidate)
                                scene_user.setdefault("user_id", normalized_user_id)
                        except Exception:
                            scene_user = {"user_id": normalized_user_id, "relationship_role": "owner"}
                try:
                    snapshot = snapshot_builder(
                        scene_user,
                        include_dialogue_outfit=include_dialogue_outfit,
                    )
                except TypeError:
                    snapshot = snapshot_builder(scene_user)
                snapshot_text = _single_line(
                    snapshot_formatter(snapshot, purpose="selfie_scene"),
                    700,
                )
                if snapshot_text:
                    return snapshot_text
            except Exception as exc:
                logger.debug(
                    "自拍场景读取统一情境快照失败，已回退旧路径: %s",
                    _single_line(exc, 160),
                )
        plan = self.data.get("daily_plan", {}) if isinstance(getattr(self, "data", {}), dict) else {}
        plan = plan if isinstance(plan, dict) else {}
        state = self.data.get("daily_state", {}) if isinstance(getattr(self, "data", {}), dict) else {}
        state = state if isinstance(state, dict) else {}

        current_schedule = ""
        try:
            current_item = self._proactive_current_plan_item(plan)
            if isinstance(current_item, dict):
                current_schedule = _single_line(self._format_plan_item_for_prompt(current_item), 260)
        except Exception:
            current_schedule = ""
        if not current_schedule and callable(getattr(self, "_format_schedule_context_for_prompt", None)):
            try:
                current_schedule = _single_line(self._format_schedule_context_for_prompt(plan), 260)
            except Exception:
                current_schedule = ""

        location = ""
        if callable(getattr(self, "_current_location_state_text", None)):
            try:
                location = _single_line(self._current_location_state_text(state), 60)
            except Exception:
                location = ""
        coarse_location = ""
        if location and callable(getattr(self, "_coarse_roleplay_location_text", None)):
            try:
                coarse_location = _single_line(self._coarse_roleplay_location_text(location), 40)
            except Exception:
                coarse_location = ""
        location_text = coarse_location or location
        if location and coarse_location and location != coarse_location:
            location_text = f"{coarse_location}（{location}）"

        parts: list[str] = []
        if current_schedule:
            parts.append(f"当前日程：{current_schedule}")
        if location_text:
            parts.append(f"当前位置：{location_text}")
        _, scene_category_label = infer_companion_scene_category(current_schedule, location_text)
        if scene_category_label:
            parts.append(f"当前场景：{scene_category_label}")
        return _single_line("；".join(parts), 460)

    def _photo_reference_schedule_history_context(self) -> str:
        """Format today's started schedule items for reference selection only."""

        snapshot_builder = getattr(self, "_build_companion_scene_snapshot", None)
        if not callable(snapshot_builder):
            return ""
        try:
            snapshot = snapshot_builder()
        except Exception:
            return ""
        schedule = snapshot.get("schedule") if isinstance(snapshot, dict) else {}
        history = schedule.get("history") if isinstance(schedule, dict) else []
        if not isinstance(history, list):
            return ""
        labels = {
            "active": "进行中",
            "completed": "已完成",
            "changed": "已变更",
        }
        lines: list[str] = []
        for item in history[:24]:
            if not isinstance(item, dict):
                continue
            status = _single_line(item.get("status"), 20).lower()
            if status not in labels:
                continue
            start = _single_line(item.get("time"), 12)
            end = _single_line(item.get("end"), 12)
            activity = _single_line(item.get("activity"), 160)
            mood = _single_line(item.get("mood"), 32)
            if not activity:
                continue
            window = "-".join(part for part in (start, end) if part)
            lines.append(
                "｜".join(
                    part
                    for part in (
                        window,
                        labels[status],
                        activity,
                        f"情绪：{mood}" if mood else "",
                    )
                    if part
                )
            )
        return self._photo_prompt_clip("\n".join(lines), 2400)

    @staticmethod
    def _photo_reference_paths_equal(left: str, right: str) -> bool:
        left_text = str(left or "").strip()
        right_text = str(right or "").strip()
        if not left_text or not right_text:
            return False
        try:
            left_text = str(Path(left_text).expanduser().resolve())
            right_text = str(Path(right_text).expanduser().resolve())
        except (OSError, ValueError):
            pass
        return os.path.normcase(left_text) == os.path.normcase(right_text)

    @staticmethod
    def _photo_persona_fallback_allowed(
        workflow_kind: str,
        reference_intent: ReferenceIntent,
    ) -> bool:
        requested_roles = set(reference_intent.requested_roles or ())
        excluded_roles = set(reference_intent.excluded_roles or ())
        return (
            str(workflow_kind or "").strip().lower()
            in {"selfie", "portrait", "自拍", "人像"}
            and reference_intent.continuity_mode != "new_topic"
            and "identity" in requested_roles
            and "identity" not in excluded_roles
        )

    def _photo_generation_recent_continuity_constraint(
        self,
        workflow_kind: str,
        *,
        reference_image_path: str,
        continuity_key: str,
        wardrobe: PhotoWardrobeDecision | None = None,
    ) -> tuple[str, bool]:
        normalized_kind = str(workflow_kind or "").strip().lower()
        if normalized_kind not in {"selfie", "portrait", "自拍", "人像"}:
            return "", False
        recent = self._recent_sent_photo_continuity_candidate(continuity_key)
        if not recent or not self._photo_reference_paths_equal(
            reference_image_path,
            recent.get("path", ""),
        ):
            return "", False
        effective_roles = set(getattr(wardrobe, "effective_reference_roles", ()) or ())
        preserved = ["identity", "face", "hairstyle"]
        if "outfit" in effective_roles:
            preserved.append("exact outfit and accessories")
        if effective_roles & {"scene", "continuity"}:
            preserved.extend(("room or location", "lighting", "time of day"))
        continuity_instruction = (
            "Recent-photo continuity: this reference is the last image actually sent in the same conversation. "
            f"Unless the current request explicitly changes them, preserve {', '.join(preserved)}. "
            "Change only the requested action, pose, expression, gaze, camera angle, or framing. "
            "Any explicit new clothing, person, place, time, or scene request still has priority."
        )
        return continuity_instruction, True

    @staticmethod
    def _photo_prompt_clip(value: Any, limit: int, *, preserve_tail: bool = False) -> str:
        return _clip_photo_prompt_text(value, limit, preserve_tail=preserve_tail)

    @staticmethod
    def _photo_prompt_split_formatted(prompt_text: str) -> tuple[str, str]:
        prompt = str(prompt_text or "").strip()
        positive_match = re.search(
            r"positive\s+prompt\s*:\s*(.*?)(?=negative\s+prompt\s*:|$)",
            prompt,
            flags=re.I | re.S,
        )
        if not positive_match:
            avoid_match = re.search(
                r"(?:^|(?<=[.!?。！？]))\s*avoid\s+(.+?)\s*[.!?。！？]?\s*$",
                prompt,
                flags=re.I | re.S,
            )
            if avoid_match:
                positive = prompt[:avoid_match.start()].rstrip(" \t\r\n.!?。！？")
                negative = avoid_match.group(1).strip(" \t\r\n.!?。！？")
                return positive, negative
            return prompt, ""
        negative_match = re.search(r"negative\s+prompt\s*:\s*(.*)$", prompt, flags=re.I | re.S)
        return positive_match.group(1).strip(), (negative_match.group(1).strip() if negative_match else "")

    @staticmethod
    def _photo_generation_reference_wardrobe_section(
        reference: dict[str, Any] | None,
        wardrobe: PhotoWardrobeDecision,
    ) -> tuple[str, str]:
        reference = reference or {}
        effective_roles = tuple(wardrobe.effective_reference_roles)
        roles = ", ".join(effective_roles)
        parts: list[str] = []
        if reference:
            active_outfit_category = (
                _single_line(reference.get("outfit_category"), 40) or "unspecified"
                if "outfit" in effective_roles
                else "not active"
            )
            parts.append(
                "Reference responsibility: "
                f"effective roles={roles or 'none'}; "
                f"outfit category={active_outfit_category}."
            )
            if reference.get("kind") == "relation_role":
                role_name = _single_line(reference.get("role_name"), 80) or "the named relationship role"
                relationship = _single_line(reference.get("relationship"), 100)
                role_context = f" ({relationship})" if relationship else ""
                parts.append(
                    "Named relationship-role reference: "
                    f"the image identifies {role_name}{role_context}, not Bot. "
                    "Use it to depict that role only when the current request explicitly asks that role to appear or share the frame; "
                    "otherwise keep the role off-camera and use natural contextual cues. Do not transfer this identity, face, or body to Bot."
                )
        if wardrobe.positive_instruction:
            parts.append(f"Wardrobe decision: {wardrobe.positive_instruction}")
        return " ".join(parts), wardrobe.negative_instruction

    @classmethod
    def _photo_generation_compact_scene_hint(cls, scene_hint: str, *, limit: int = 420) -> str:
        text = _single_line(scene_hint, 1600)
        if not text or len(text) <= limit:
            return text
        parts = [part.strip() for part in re.split(r"[；;]+", text) if part.strip()]
        if len(parts) <= 1:
            return cls._photo_prompt_clip(text, min(limit, 260), preserve_tail=True)
        priorities = (
            (r"^(?:当前位置|地点|位置)[：:]", 90),
            (r"^(?:当前场景|场景)[：:]", 60),
            (r"^(?:时间|当前时间)[：:]", 60),
            (r"^(?:当前日程|日程)[：:]", 130),
            (r"^(?:今日穿搭|当天基础穿搭|当天穿搭|日常穿搭|today'?s outfit|daily outfit)[：:]", 120),
            (r"^(?:天气背景|天气|当前天气)[：:]", 90),
            (r"^(?:状态|状态余波|情绪)[：:]", 80),
            (r"^(?:视觉话题|背景)[：:]", 80),
        )
        ordered: list[tuple[str, int]] = []
        used: set[int] = set()
        for pattern, field_limit in priorities:
            for index, part in enumerate(parts):
                if index not in used and re.search(pattern, part, flags=re.I):
                    ordered.append((part, field_limit))
                    used.add(index)
        ordered.extend((part, 80) for index, part in enumerate(parts) if index not in used)
        kept: list[str] = []
        for part, field_limit in ordered:
            compact = cls._photo_prompt_clip(part, field_limit, preserve_tail=True)
            candidate = "；".join((*kept, compact))
            if len(candidate) <= limit:
                kept.append(compact)
                continue
            remaining = limit - len("；".join(kept)) - (1 if kept else 0)
            if remaining >= 36:
                kept.append(cls._photo_prompt_clip(compact, remaining, preserve_tail=True))
            break
        return "；".join(kept)

    @staticmethod
    def _photo_generation_selfie_scene_constraint(
        workflow_kind: str,
        scene_hint: str,
        *,
        has_reference: bool,
    ) -> str:
        normalized = str(workflow_kind or "").strip().lower()
        if normalized not in {"selfie", "portrait", "自拍", "人像"} or not scene_hint:
            return ""
        reference_boundary = (
            "The reference controls only the roles declared by the wardrobe ruling. "
            if has_reference
            else "Do not assume an unavailable reference was supplied. "
        )
        return (
            "Resolved selfie scene facts: "
            f"{scene_hint}. An explicit scene or location in the current request overrides conflicting facts; otherwise use these facts for time, location, activity, mood, weather, and light. "
            f"{reference_boundary}"
            "Do not restore a conflicting schedule location or wardrobe, and avoid unrelated rooms."
        )

    def _photo_generation_composition_sections(
        self,
        workflow_kind: str,
        prompt_text: str,
        *,
        allow_group_photo: bool = False,
    ) -> tuple[str, str]:
        normalized = str(workflow_kind or "").strip().lower()
        if normalized not in {"selfie", "portrait", "自拍", "人像"}:
            return "", ""
        explicit_mirror = self._photo_generation_explicit_mirror_request(prompt_text)
        explicit_back_view = self._photo_generation_explicit_back_view_request(prompt_text)
        if allow_group_photo and _photo_group_request_matches(prompt_text):
            positive = (
                "Referenced multi-person composition: preserve every person represented by the submitted visual references, "
                "their count, identity, and relative placement in one continuous scene; do not invent anyone else."
            )
            negative = "unreferenced extra people, invented faces, duplicated people, comparison panels, split screen, collage"
        elif explicit_back_view:
            positive = (
                "Back-view character composition: exactly one recognizable character wearing one coherent outfit in one continuous scene; "
                "the requested back view or facing-away pose is intentional, preserve the reference hairstyle silhouette and stable appearance, "
                "and compose a natural environmental portrait without requiring the face to be visible."
            )
            negative = "duplicated subject, twins, multiple people, outfit alternatives, comparison panels, split screen, side-by-side panels, collage, character sheet"
        elif explicit_mirror:
            positive = (
                "Selfie composition: exactly one character wearing one coherent outfit in one continuous scene; "
                "one mirror reflection of that same outfit is allowed; keep the complete face visible and do not let the phone cover it."
            )
            negative = "duplicated subject, outfit alternatives, comparison panels, split screen, side-by-side panels, collage, character sheet, phone covering face"
        else:
            positive = (
                "Selfie composition: exactly one character wearing one coherent outfit in one continuous scene; keep the face visible, "
                "prefer a handheld selfie or natural environmental portrait with upper-body to three-quarter framing, and place the character naturally in the resolved scene."
            )
            negative = (
                "duplicate character, twins, multiple people, multiple outfits, outfit comparison, before and after, split screen, "
                "side-by-side panels, diptych, collage, character sheet, mirror selfie, full-length mirror selfie, dressing-room mirror, phone covering face"
            )
        return positive, negative

    @staticmethod
    def _photo_generation_subject_count_contract(
        workflow_kind: str,
        request_text: str,
        *,
        explicit_reference_supplied: bool,
    ) -> tuple[str, str]:
        normalized = str(workflow_kind or "").strip().lower()
        if normalized in {"edit", "改图", "修图", "重绘", "p图"}:
            return "", ""
        group_photo_requested = _photo_group_request_matches(request_text)
        if explicit_reference_supplied and group_photo_requested:
            return (
                "Multi-person composition is permitted only because the current request supplied an explicit source reference; "
                "preserve the referenced people's identities and do not invent additional people.",
                "unreferenced extra people, invented faces, duplicated people",
            )
        if normalized not in {"selfie", "portrait", "自拍", "人像"} and not group_photo_requested:
            return "", ""
        return (
            "Subject-count boundary: show at most one recognizable human character in one continuous scene. "
            "Other people may be implied only by non-human traces such as a second cup, gift, note, or off-camera context; "
            "do not show another face, body, silhouette, reflection, or portrait.",
            "group photo, group portrait, couple photo, two people, multiple people, extra person, second person, "
            "companion in frame, crowd, invented face",
        )

    @staticmethod
    def _photo_generation_explicit_back_view_request(text: str) -> bool:
        raw = _single_line(text, 1200)
        if not raw:
            return False
        positive = re.split(r"negative prompt\s*:", raw, maxsplit=1, flags=re.I)[0]
        positive = re.sub(
            r"(?:不要|避免|别|不许|禁止).{0,18}(?:背影|背对镜头|背对相机)|"
            r"\b(?:no|not|avoid|without)\s+(?:a\s+)?(?:back[-\s]?view|facing\s+away)[^,.;；。]*",
            " ",
            positive,
            flags=re.I,
        )
        return bool(
            any(marker in positive for marker in ("背影", "背对镜头", "背对相机", "从背后", "身后视角"))
            or re.search(r"\b(?:back[-\s]?view|from\s+behind|facing\s+away)\b", positive, flags=re.I)
        )

    @staticmethod
    def _photo_generation_edit_contract(workflow_kind: str) -> tuple[str, str]:
        normalized = str(workflow_kind or "").strip().lower()
        if normalized not in {"edit", "改图", "修图", "重绘", "p图"}:
            return "", ""
        return (
            "Image edit contract: use the user-provided image as the sole source canvas and visual identity reference. "
            "Treat the request strictly as a constrained edit of that supplied canvas. Preserve every subject, face, body, outfit, pose, composition, camera angle, and background detail unless the user explicitly asks to change it. "
            "Apply only the requested edit and keep unrelated pixels and details as close to the source as possible.",
            "a selfie or a new character portrait, replacing the source person with the assistant persona, restoring today's outfit, unrelated redesigns",
        )

    @staticmethod
    def _photo_generation_explicit_mirror_request(text: str) -> bool:
        raw = _single_line(text, 1200)
        if not raw:
            return False
        lowered = raw.lower()
        detection_text = re.split(r"negative prompt\s*:", lowered, maxsplit=1, flags=re.I)[0]
        positive_scan = re.sub(
            r"(?:不要|避免|别|不许|禁止).{0,18}(?:镜前|对镜|镜中|镜子|全身镜|穿衣镜|试衣镜)",
            " ",
            detection_text,
            flags=re.I,
        )
        positive_scan = re.sub(
            r"(?:no|not|avoid|without)\s+(?:a\s+)?(?:mirror|mirror\s+selfie|full[-\s]?length\s+mirror|"
            r"full[-\s]?body\s+mirror|mirror\s+shot|mirror\s+photo|mirror\s+portrait)[^,.;；。]*",
            " ",
            positive_scan,
            flags=re.I,
        )
        positive_scan = re.sub(r"\bnon[-\s]?mirror\b", " ", positive_scan, flags=re.I)
        positive_scan = re.sub(r"unless[^,.;；。]*mirror[^,.;；。]*", " ", positive_scan, flags=re.I)
        if re.search(
            r"镜前|对镜|镜中|镜子|全身镜|穿衣镜|试衣镜|\bmirror\b|looking\s+in\s+the\s+mirror|in\s+front\s+of\s+(?:a\s+)?mirror",
            positive_scan,
            flags=re.I,
        ):
            return True
        return False

    @staticmethod
    def _append_photo_negative_terms(prompt_text: str, terms: list[str], *, limit: int = 1800) -> str:
        prompt = str(prompt_text or "").strip()
        if not prompt:
            return ""
        existing = prompt.lower()
        missing = [term for term in terms if term and term.lower() not in existing]
        if not missing:
            return _single_line(prompt, limit)
        suffix = ", ".join(missing)
        if re.search(r"negative prompt\s*:", prompt, flags=re.I):
            prompt = prompt.rstrip().rstrip(".")
            return _single_line(f"{prompt}, {suffix}.", limit)
        return _single_line(f"{prompt}. Negative prompt: {suffix}.", limit)

    def _sanitize_unrequested_mirror_selfie_prompt(
        self,
        prompt_text: str,
        *,
        context_text: str = "",
        limit: int = 1800,
    ) -> str:
        prompt = str(prompt_text or "").strip()
        if not prompt:
            return ""
        if self._photo_generation_explicit_mirror_request(context_text):
            return _single_line(prompt, limit)
        replacements = (
            (r"\bfull[-\s]?length\s+mirror\s+(?:selfie|shot|photo|portrait)\b", "natural upper-body to three-quarter portrait"),
            (r"\bfull[-\s]?body\s+mirror\s+(?:selfie|shot|photo|portrait)\b", "natural upper-body to three-quarter portrait"),
            (r"\bmirror\s+(?:selfie|shot|photo|portrait)\b", "handheld selfie or natural environmental portrait"),
            (r"\bstanding\s+in\s+front\s+of\s+(?:a\s+)?mirror\b", "standing naturally in the current location"),
            (r"\bdressing[-\s]?room\s+mirror\b", "current-location background"),
            (r"\bphone\s+covering\s+(?:the\s+)?face\b", "visible face"),
            (r"全身镜自拍|全身对镜|对镜自拍|镜前自拍|镜中自拍|穿衣镜|试衣镜", "自然半身或四分之三身随手拍"),
        )
        cleaned = prompt
        for pattern, replacement in replacements:
            cleaned = re.sub(pattern, replacement, cleaned, flags=re.I)
        cleaned = re.sub(r"\s*,\s*,+", ", ", cleaned)
        cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" ,;；")
        negative_terms = [
            "mirror selfie",
            "full-length mirror selfie",
            "full body mirror shot",
            "dressing room mirror",
            "phone covering face",
        ]
        return self._append_photo_negative_terms(cleaned or prompt, negative_terms, limit=limit)

    def _apply_photo_generation_selfie_composition_guard(self, prompt_text: str, workflow_kind: str) -> str:
        prompt = str(prompt_text or "").strip()
        normalized = str(workflow_kind or "").strip().lower()
        if normalized not in {"selfie", "portrait", "自拍", "人像"}:
            return _single_line(prompt, 1800)
        explicit_mirror = self._photo_generation_explicit_mirror_request(prompt)
        explicit_back_view = self._photo_generation_explicit_back_view_request(prompt)
        if explicit_back_view:
            guard = (
                "Back-view character composition guard: exactly one recognizable character wearing exactly one coherent outfit in one continuous scene; "
                "the explicitly requested back-view pose is allowed, preserve the reference hairstyle silhouette and stable appearance, "
                "and do not require the face to be visible."
            )
        elif explicit_mirror:
            guard = (
                "Selfie composition guard: exactly one character wearing exactly one coherent outfit in one continuous scene; "
                "a single mirror reflection of that same outfit is allowed, but do not create outfit alternatives, comparison panels, duplicated subjects, or a collage; "
                "keep the face visible and avoid the phone covering the face."
            )
        else:
            guard = (
                "Default selfie composition guard: exactly one character wearing exactly one coherent outfit in one continuous scene; "
                "no duplicated subject, outfit alternatives, comparison layout, split screen, side-by-side panels, diptych, collage, or character sheet; "
                "no mirror selfie or full-length mirror shot unless explicitly requested; keep the face visible, avoid phone covering face, "
                "use upper-body to three-quarter framing, and place the character naturally in the current scene."
            )
        merged = f"{prompt}\n\n{guard}".strip()
        negative_terms = [
            "duplicate character",
            "twins",
            "multiple people",
            "multiple outfits",
            "outfit comparison",
            "before and after",
            "split screen",
            "side-by-side panels",
            "diptych",
            "collage",
            "character sheet",
        ]
        if not explicit_mirror and not explicit_back_view:
            negative_terms.extend(
                ["mirror selfie", "full-length mirror selfie", "full body mirror shot", "dressing room mirror"]
            )
        if not explicit_back_view:
            negative_terms.append("phone covering face")
        return self._append_photo_negative_terms(
            merged,
            negative_terms,
            limit=1800,
        )

    def _apply_photo_generation_edit_guard(self, prompt_text: str, workflow_kind: str) -> str:
        prompt = str(prompt_text or "").strip()
        normalized = str(workflow_kind or "").strip().lower()
        if normalized not in {"edit", "改图", "修图", "重绘", "p图"}:
            return _single_line(prompt, 1800)
        guard = (
            "Image edit contract: use the user-provided image as the sole source canvas and visual identity reference. "
            "This is not a selfie or a new character portrait. Preserve every subject, face, body, outfit, pose, "
            "composition, camera angle, and background detail unless the user explicitly asks to change it. "
            "Never replace a person with the assistant persona, a configured persona reference, or today's outfit. "
            "Apply only the requested edit and keep all unrelated pixels and details as close to the source as possible."
        )
        return _single_line(f"{guard}\n\nEdit request and existing prompt: {prompt}".strip(), 1800)

    def _builtin_photo_generation_scene_presets(self) -> dict[str, str]:
        return {
            "角色自拍": (
                "natural casual character photo, single character, face visible by default, clear face, hair, expression, neck and shoulders, "
                "phone snapshot feeling, lifelike composition, no cropped head, no hidden face or back view unless explicitly requested, no body-only framing"
            ),
            "COS自拍": (
                "cosplay themed selfie, keep the character's own face, hair color, eye color, and key visual traits, "
                "clear costume theme, tasteful outfit, convention snapshot or room fitting photo feeling"
            ),
            "日常穿搭": (
                "daily outfit portrait without mirror, exactly one character wearing one coherent outfit in one continuous frame, "
                "no outfit comparison, no split screen, no side-by-side panels, handheld selfie or natural environmental portrait, "
                "upper-body to three-quarter framing, visible face, clear clothing layers and color palette, "
                "location-appropriate background, no phone covering face, not body-only"
            ),
            "居家睡衣": (
                "sleepwear or bedtime loungewear portrait matching the explicit clothing request and selected reference, "
                "exactly one coherent sleepwear outfit, preserve the character identity, natural home or bedtime context, "
                "do not restore a daytime outfit, coat, school uniform, or commuter layers unless explicitly requested"
            ),
            "居家服": (
                "comfortable homewear portrait, one coherent relaxed indoor outfit, natural home activity and lived-in setting, "
                "preserve the character identity and selected homewear reference, no commuter coat or formal layers unless requested"
            ),
            "校服人像": (
                "school-uniform portrait matching the explicit request, one coherent uniform with consistent layers and colors, "
                "natural school or campus context, preserve the character identity, not cosplay unless explicitly requested"
            ),
            "礼服人像": (
                "formalwear portrait matching the explicit request, one coherent formal outfit with consistent silhouette and materials, "
                "location-appropriate formal context, preserve the character identity, no casual or sportswear substitution"
            ),
            "泳装人像": (
                "swimwear portrait matching the explicit request, one coherent swim outfit, appropriate pool or beach context, "
                "preserve the character identity, tasteful natural composition, no unrelated daytime clothing layers"
            ),
            "运动服人像": (
                "sportswear portrait matching the explicit request, one coherent practical athletic outfit, natural activity setting, "
                "preserve the character identity, no formalwear or commuter outfit substitution"
            ),
            "镜前穿搭": (
                "explicitly requested mirror outfit photo, half-body to three-quarter mirror composition, "
                "clear clothes, jacket, accessories and color palette, complete visible face, no phone covering face, "
                "subject inside square safe area, not full-length body-only, not outfit-only, not clothing close-up"
            ),
            "头像特写": (
                "avatar-ready face close-up, clear hair, eyes and expression, clean background, centered face, enough margin, "
                "no text, no watermark, no cluttered props"
            ),
            "房间日常": (
                "indoor slice-of-life photo, natural desk objects, books, cup, window side or bedside details, "
                "one clear subject, calm lived-in atmosphere, avoid overcrowded composition"
            ),
            "可拍画面": (
                "casual photo shared with a close friend, concrete visual subject, natural lighting, not a vague landscape, "
                "not a weather report, when the request is scenery or an object frame it from the photographer's point of view, "
                "do not insert an unrequested person, character, visible photographer, or back-view figure, "
                "no private screen, no real personal information, no unrelated text, no watermark"
            ),
            "表情包场景": (
                "single sticker-like image for chat, clear emotion, simple composition, cute exaggerated expression, "
                "character remains recognizable, only include short text if the user explicitly requested it"
            ),
        }

    def _parse_photo_generation_scene_presets(self, raw: Any) -> dict[str, str]:
        presets: dict[str, str] = {}
        if isinstance(raw, dict):
            iterable = raw.items()
            for key, value in iterable:
                name = _single_line(key, 40)
                prompt = _single_line(value, 900)
                if name and prompt:
                    presets[name] = prompt
            return presets
        items: list[Any] = []
        if isinstance(raw, list):
            items = raw
        elif isinstance(raw, str):
            text = raw.replace("\r\n", "\n").replace("\r", "\n")
            items = [line for line in text.split("\n") if str(line or "").strip()]
        for item in items:
            if isinstance(item, dict):
                name = _single_line(item.get("name") or item.get("key") or item.get("title"), 40)
                prompt = _single_line(item.get("prompt") or item.get("value") or item.get("content"), 900)
            else:
                text = str(item or "").strip()
                if ":" in text:
                    name, prompt = text.split(":", 1)
                elif "：" in text:
                    name, prompt = text.split("：", 1)
                else:
                    continue
                name = _single_line(name, 40)
                prompt = _single_line(prompt, 900)
            if name and prompt:
                presets[name] = prompt
        return presets

    def _photo_generation_scene_presets(self) -> dict[str, str]:
        presets = self._builtin_photo_generation_scene_presets()
        presets.update(
            self._parse_photo_generation_scene_presets(
                runtime_persona_setting(self, "photo_generation_scene_presets", "")
            )
        )
        return presets

    def _apply_photo_generation_scene_presets(
        self,
        prompt_text: str,
        workflow_kind: str,
        *,
        preset_names: list[str] | None = None,
    ) -> tuple[str, list[str]]:
        prompt = str(prompt_text or "").strip()
        presets = self._photo_generation_scene_presets()
        requested_names = preset_names or []
        names = [name for name in requested_names if name in presets][:1]
        if not names:
            return _single_line(prompt, 1800), []
        blocks = []
        for name in names:
            content = _single_line(presets.get(name), 900)
            if content and content not in prompt:
                blocks.append(f"{self._photo_generation_scene_preset_label_en(name)}: {content}")
        if not blocks:
            return _single_line(prompt, 1800), names
        merged = f"{prompt}\n\nScene preset: " + "; ".join(blocks)
        return _single_line(merged, 1800), names

    def _photo_generation_scene_preset_label_en(self, name: str) -> str:
        return {
            "角色自拍": "casual character selfie",
            "COS自拍": "cosplay selfie",
            "日常穿搭": "daily outfit portrait",
            "居家睡衣": "home sleepwear portrait",
            "居家服": "comfortable homewear portrait",
            "校服人像": "school uniform portrait",
            "礼服人像": "formalwear portrait",
            "泳装人像": "swimwear portrait",
            "运动服人像": "sportswear portrait",
            "镜前穿搭": "mirror outfit photo",
            "头像特写": "avatar close-up",
            "房间日常": "indoor slice-of-life",
            "可拍画面": "casual shareable photo",
            "表情包场景": "sticker scene",
        }.get(_single_line(name, 40), _single_line(name, 40) or "scene preset")

    async def _generate_photo_image(
        self,
        **kwargs: Any,
    ) -> tuple[str, str, str]:
        """Run image generation through the selected backend service."""
        nai_selected = getattr(self, "_nai_image_selected", None)
        if callable(nai_selected) and nai_selected(kwargs.get("workflow_kind", "")):
            nai_bridge = getattr(self, "_nai_image_generate", None)
            if callable(nai_bridge):
                return await nai_bridge(**kwargs)
            return (
                "NAI 生图",
                "",
                "生图后端已选择 NAI 直连，但未检测到 NAI 生图插件，请安装并启用 astrbot_plugin_nai_image。",
            )
        bridge = getattr(self, "_image_companion_generate", None)
        if callable(bridge):
            return await bridge(**kwargs)
        return (
            "独立生图服务",
            "",
            "生图能力已拆分，请安装并启用“我会画给你看”插件 astrbot_plugin_image_companion。",
        )

    async def _generate_photo_image_result(self, **kwargs: Any) -> PhotoGenerationResult:
        self._image_companion_generation_metadata = {}
        self._nai_image_generation_metadata = {}
        backend, image_path, note = await self._generate_photo_image(**kwargs)
        metadata: dict[str, Any] = {}
        bridge_metadata_supported = False
        for getter_name in ("_image_companion_last_metadata", "_nai_image_last_metadata"):
            getter = getattr(self, getter_name, None)
            if callable(getter):
                bridge_metadata_supported = True
                metadata = getter() or {}
                if metadata:
                    break
        if not metadata and not bridge_metadata_supported:
            metadata = self._photo_generation_result_metadata(
                image_path=image_path,
                session_key=_single_line(kwargs.get("session_key"), 340),
            )
        reference_path = _path_text(
            metadata.get("reference_path") or kwargs.get("reference_image_path"),
            1000,
        )
        intent_metadata = metadata.get("reference_intent") if isinstance(metadata.get("reference_intent"), dict) else {}
        plan_metadata = metadata.get("reference_plan") if isinstance(metadata.get("reference_plan"), dict) else {}
        fallback_metadata = metadata.get("reference_fallback") if isinstance(metadata.get("reference_fallback"), dict) else {}
        return PhotoGenerationResult(
            backend=_single_line(backend, 80),
            image_path=_path_text(image_path, 1000),
            note=_single_line(note, 500),
            trace_id=_single_line(metadata.get("trace"), 40),
            reference_selected_path=reference_path,
            reference_used=bool(metadata.get("reference_used")),
            reference_id=_single_line(metadata.get("reference_id"), 60),
            reference_kind=_single_line(metadata.get("reference_kind"), 40),
            reference_roles=tuple(
                _single_line(role, 40)
                for role in (metadata.get("reference_roles") or [])
                if _single_line(role, 40)
            ),
            wardrobe_mode=_single_line(metadata.get("wardrobe_mode"), 40),
            wardrobe_category=_single_line(metadata.get("wardrobe_category"), 40),
            outfit_locked=bool(metadata.get("outfit_locked")),
            daily_outfit_removed=bool(metadata.get("daily_outfit_removed")),
            preset_names=tuple(
                _single_line(name, 60)
                for name in (metadata.get("presets") or [])
                if _single_line(name, 60)
            )[:1],
            preset_hint=_single_line(metadata.get("preset_hint"), 80),
            preset_source=_single_line(metadata.get("preset_source"), 40),
            suggestion_status=_single_line(metadata.get("suggestion_status"), 60),
            prompt_hash=_single_line(metadata.get("prompt_hash"), 80),
            prompt_path=_path_text(metadata.get("prompt_path"), 1000),
            reference_requested_roles=tuple(
                _single_line(role, 40)
                for role in (intent_metadata.get("requested_roles") or [])
                if _single_line(role, 40)
            ),
            reference_excluded_roles=tuple(
                _single_line(role, 40)
                for role in (intent_metadata.get("excluded_roles") or [])
                if _single_line(role, 40)
            ),
            continuity_mode=_single_line(intent_metadata.get("continuity_mode"), 30) or "ambiguous",
            reference_confidence=_safe_float(intent_metadata.get("confidence"), 0.0, 0.0, 1.0),
            reference_plan=tuple(
                dict(binding)
                for binding in (plan_metadata.get("bindings") or [])
                if isinstance(binding, dict)
            ),
            reference_fulfilled_roles=tuple(
                _single_line(role, 40)
                for role in (fallback_metadata.get("fulfilled_roles") or [])
                if _single_line(role, 40)
            ),
            reference_missing_roles=tuple(
                _single_line(role, 40)
                for role in (fallback_metadata.get("missing_roles") or [])
                if _single_line(role, 40)
            ),
            reference_fallback_message=_single_line(fallback_metadata.get("message"), 260),
            generation_completed=bool(metadata.get("generation_completed")),
            failure_stage=_single_line(metadata.get("failure_stage"), 60),
        )

    @staticmethod
    def _photo_scene_generation_prompt_document(
        *,
        persona: str,
        recipient_name: str,
        scene_context: str,
        topic_hint: str,
        motive_hint: str,
        relationship_section: PromptSection | None,
        birthday_rule: str,
        content_options: str,
        style_name: str,
        style_instruction: str,
        prompt_format_instruction: str,
        reason: str,
    ) -> PromptDocument:
        sections: list[PromptSection | PromptDocumentPart] = [
            _proactive_prompt_part(prompt_section(
                key="background.photo_scene.task",
                title="主动生活图片提示词生成",
                source="proactive_message",
                content="请根据 AstrBot 默认人格和主动原因,生成一张要通过生图后端制作的“社交媒体随手拍/自拍/生活碎片图”提示词。",
            ), mode=PromptRenderMode.BODY_ONLY),
            prompt_section(
                key="background.photo_scene.persona",
                title="人格",
                source="proactive_message",
                content=persona,
            ),
            prompt_section(
                key="background.photo_scene.recipient",
                title="收信人",
                source="proactive_message",
                content=recipient_name,
            ),
            prompt_section(
                key="background.photo_scene.snapshot",
                title="当前统一情境快照",
                source="proactive_message",
                content=(
                    f"{scene_context}\n"
                    "使用方式：这是当前事实和连续性参考。优先保持时间、地点、日程和情绪互相一致；"
                    "今日穿搭只在本次没有新的服装请求时用于连续性。若话题、动机或画面需求明确要求睡衣、居家服、礼服、COS 等服装变化，"
                    "以本次明确请求为准，不要被今日穿搭覆盖。它只帮助选择自然画面，不要求把所有字段都画出来或写进配文。"
                ),
            ),
            prompt_section(
                key="background.photo_scene.hook",
                title="这次想分享的画面钩子",
                source="proactive_message",
                content=(
                    f"话题：{topic_hint or '（未指定）'}\n"
                    f"那一刻的小动机：{motive_hint or '（未指定）'}"
                ),
            ),
        ]
        if relationship_section is not None:
            sections.append(relationship_section)
        sections.extend(
            (
                prompt_section(
                    key="background.photo_scene.birthday",
                    title="生日卡特殊规则",
                    source="proactive_message",
                    content=birthday_rule,
                ),
                prompt_section(
                    key="background.photo_scene.options",
                    title="内容选择菜单",
                    source="proactive_message",
                    content=content_options,
                ),
                prompt_section(
                    key="background.photo_scene.style",
                    title="生图风格",
                    source="proactive_message",
                    content=f"{style_name}\n风格要求：{style_instruction}",
                ),
                prompt_section(
                    key="background.photo_scene.format",
                    title="提示词表达方式",
                    source="proactive_message",
                    content=prompt_format_instruction,
                ),
                _proactive_prompt_part(prompt_section(
                    key="background.photo_scene.reason",
                    title="主动原因",
                    source="proactive_message",
                    content=f"主动原因：{reason}",
                ), mode=PromptRenderMode.BODY_ONLY),
                _proactive_prompt_part(prompt_section(
                    key="background.photo_scene.output",
                    title="输出 JSON",
                    source="proactive_message",
                    content=(
                        "{\n"
                        '  "kind": "selfie 或 text2img；自拍/人像用 selfie,其他随手拍用 text2img",\n'
                        '  "use_persona_reference": true,\n'
                        '  "prompt": "按上方提示词表达方式输出的英文生图提示词",\n'
                        '  "caption": "图片完成后可转述给最终私聊模型的一句话画面描述"\n'
                        "}"
                    ),
                ), label_style=PromptLabelStyle.FULLWIDTH_COLON),
                _proactive_prompt_part(prompt_section(
                    key="background.photo_scene.rules",
                    title="要求",
                    source="proactive_message",
                    content=(
                        "1. 画面必须符合当前时间、日程和人格,不要把身份设定里没有的场景、职业、服装或外观细节写进去。日程是背景参考，不可单独当作动作已经发生的证明。\n"
                        "2. 图片不要总是天气或窗外。先从“内容选择菜单”里单选一个视觉锚点；当前日程、话题和人格只用于筛选主体和调整画面气质,不要把多个主体拼在一张图里。若本次来自延后候选，画面应与原话题连续，不应伪装成发送当下的新现场。\n"
                        "3. 可以是路上风景、桌面小物、随手自拍、偶遇小动物等,但不要每次都是自拍；没有明确自拍动机时优先 text2img。\n"
                        "4. `prompt` 必须使用英文，并严格遵守“提示词表达方式”；可以把必要中文专名作为 visual note 保留，但不要写任务说明或聊天口吻。\n"
                        "5. `prompt` 里要明确体现上面的风格要求。\n"
                        "6. 不要包含 NSFW、隐私信息、用户真实电脑画面。\n"
                        "7. 如果“话题”已经很具体,就优先把那个具体视觉主体画出来；如果话题很抽象,从菜单里另选一个适合拍照的具体画面。不要退回成泛泛的天气图、手部动作或普通记录照。\n"
                        "8. 不要默认生成全身镜/对镜自拍/手机挡脸自拍；只有话题、动机或当前日程明确出现“镜前/对镜/镜子/全身镜/mirror”时才允许。普通穿搭图用当前地点里的手持自拍、半身或四分之三身环境人像。\n"
                        "9. `use_persona_reference` 仅表示画面中是否出现 Bot 本人：自拍、人物生活照、人物穿搭图填 true；纯风景、食物、桌面物品、动物、手机屏幕或生日卡填 false。\n"
                        "10. 服装语义优先级为：本次明确服装需求优先；具体场景服装参考用于落实该需求；今日穿搭仅在没有新服装意图时作为连续性补充。不要同时写入彼此冲突的两套服装。\n"
                        "11. 只有当前请求明确要求关系角色出现/合影，且选中了对应的角色参考图时，才可让该角色按参考图自然入镜；否则禁止凭文字补画另一人的脸、身体、背影、剪影、倒影或肖像。未明确要求时，关系卡只影响情境，并用非人物生活线索间接表达关系。"
                    ),
                ), label_style=PromptLabelStyle.FULLWIDTH_COLON),
            )
        )
        return prompt_document(
            user_render=_PROACTIVE_DOCUMENT_RENDER,
            user=sections,
            metadata={"task": "photo_prompt"},
        )

    async def _build_photo_scene_prompt(
        self, user: dict[str, Any], name: str, reason: str
    ) -> dict[str, Any]:
        prompt_format = self._photo_generation_prompt_format_mode()
        prompt_format_instruction = self._photo_generation_prompt_format_instruction()
        persona = self._get_default_persona_prompt()
        state = self.data.get("daily_state", {})
        current_item = self._proactive_current_plan_item(self.data.get("daily_plan", {}))
        style_name, style_instruction = self._get_photo_style_instruction()
        style_prompt_en = self._photo_style_prompt_en(style_name, style_instruction)
        topic_hint = _single_line(user.get("planned_proactive_topic"), 60)
        motive_hint = _single_line(user.get("planned_proactive_motive"), 120)
        schedule_context = self._format_plan_item_for_prompt(current_item)
        pure_scene_context = "；".join(
            part for part in (topic_hint, motive_hint, schedule_context) if part
        )
        explicit_person_scene = bool(
            re.search(
                r"自拍|合影|合照|人像|人物|角色|穿搭|女孩|男孩|女生|男生|女人|男人|"
                r"少女|少年|路人|猫女|拟人|"
                r"\b(?:selfie|portrait|character|person|people|woman|man|girl|boy|outfit)\b",
                pure_scene_context,
                flags=re.I,
            )
        )
        explicit_pure_scene = reason == "birthday_celebration" or (
            bool(
                re.search(
                    r"风景|景色|风光|日落|晚霞|天空|海边风景|海边景色|"
                    r"食物|美食|早餐|午餐|晚餐|甜点|蛋糕|咖啡|饮料|"
                    r"动物|猫|狗|小猫|小狗|桌面(?:物品)?|物品|礼物|花束|花瓶|"
                    r"卡片|生日卡|手机屏幕|屏幕|"
                    r"\b(?:scenery|landscape|sunset|sky|seascape|food|breakfast|lunch|"
                    r"dinner|dessert|cake|coffee|drink|animal|cat|dog|tabletop|object|"
                    r"gift|bouquet|vase|card|birthday card|screen)\b",
                    pure_scene_context,
                    flags=re.I,
                )
            )
            and not explicit_person_scene
        )

        def scene_subject_flags(text: Any) -> tuple[bool, bool]:
            """Classify explicit person and pure-scene cues in scene text."""
            value = _single_line(text, 900)
            has_person = bool(
                re.search(
                    r"自拍|合影|合照|人像|人物|角色|穿搭|女孩|男孩|女生|男生|女人|男人|"
                    r"少女|少年|路人|猫女|拟人|"
                    r"\b(?:selfie|portrait|character|person|people|woman|man|girl|boy|outfit|human)\b",
                    value,
                    flags=re.I,
                )
            )
            has_scene = bool(
                re.search(
                    r"风景|景色|风光|日落|晚霞|天空|海边风景|海边景色|"
                    r"食物|美食|早餐|午餐|晚餐|甜点|蛋糕|咖啡|饮料|"
                    r"动物|猫|狗|小猫|小狗|桌面(?:物品)?|物品|礼物|花束|花瓶|"
                    r"卡片|生日卡|手机屏幕|屏幕|"
                    r"\b(?:scenery|landscape|sunset|sky|seascape|food|breakfast|lunch|"
                    r"dinner|dessert|cake|coffee|drink|soup|ramen|noodles|meal|bread|"
                    r"toast|sandwich|rice|fruit|animal|cat|dog|tabletop|object|"
                    r"gift|bouquet|vase|card|birthday card|screen)\b",
                    value,
                    flags=re.I,
                )
            )
            return has_person, has_scene
        delayed_scene = bool(self._deferred_immediate_share_tense_hint(user, "photo_text"))
        if delayed_scene:
            schedule_context = "本次画面对应较早的生活片段；日程只用于保持人物与场景连续，不可作为发送当下的事实依据。"
        scene_snapshot: dict[str, Any] = {}
        scene_context = ""
        snapshot_builder = getattr(self, "_build_companion_scene_snapshot", None)
        snapshot_formatter = getattr(self, "_format_companion_scene_snapshot", None)
        if callable(snapshot_builder) and callable(snapshot_formatter):
            try:
                scene_snapshot = snapshot_builder(user)
                scene_context = _single_line(
                    snapshot_formatter(
                        scene_snapshot,
                        purpose="proactive_photo",
                    ),
                    1200,
                )
                snapshot_schedule = scene_snapshot.get("schedule")
                if not delayed_scene and isinstance(snapshot_schedule, dict):
                    schedule_context = (
                        _single_line(snapshot_schedule.get("text"), 320)
                        or schedule_context
                    )
            except Exception as exc:
                scene_snapshot = {}
                scene_context = ""
                logger.debug(
                    "主动照片读取统一情境快照失败，已回退旧路径: %s",
                    _single_line(exc, 160),
                )
        if not scene_context:
            scene_context = _single_line(
                "；".join(
                    part
                    for part in (
                        self._format_state_for_prompt(state if isinstance(state, dict) else {}),
                        schedule_context,
                    )
                    if part
                ),
                1200,
            )
        relationship_section: PromptSection | None = None
        if runtime_persona_setting(self, "enable_bot_relationship_network", False):
            card_lines: list[str] = []
            for raw_card in self._normalize_bot_relationship_cards(
                runtime_persona_setting(self, "bot_relationship_cards", [])
            ):
                parts = [_single_line(part, 200) for part in raw_card.split(" || ", 2)]
                relation = parts[1] if len(parts) > 1 else ""
                appearance = parts[2] if len(parts) > 2 else ""
                card_lines.append(f"- 角色：{parts[0]}；与Bot的关系：{relation or '（未填写）'}；外貌描述：{appearance or '（未填写）'}")
            if card_lines:
                relationship_section = prompt_section(
                    key="background.photo_scene.relationships",
                    title="Bot 关系网",
                    source="proactive_message",
                    content=(
                        "\n".join(card_lines)
                        + "\n使用方式：这些角色卡首先用于理解关系情境；角色卡文字不能替代人物参考图。只有当前请求明确点名角色/关系，或明确要求合影、合照、一起入镜时，"
                        "并且候选中确实选中了对应的角色参考图，才可让该角色按参考图自然入镜；没有匹配参考图时不要凭文字补画脸、身体、背影、剪影或倒影。"
                        "未明确要求角色出现时，仍不得让关系卡人物本人入镜，保持 Bot 单人或纯场景；在没有其他可验证人物参考时，禁止合影、合照、双人/多人同框。"
                        "可用第二只杯子、礼物、便签、空座位等非人物线索间接表达；不合适时忽略本节。"
                    ),
                )
        prompt = render_prompt_document(
            self._photo_scene_generation_prompt_document(
                persona=persona,
                recipient_name=name,
                scene_context=scene_context,
                topic_hint=topic_hint,
                motive_hint=motive_hint,
                relationship_section=relationship_section,
                birthday_rule=(
                    "如果主动原因是 birthday_celebration：制作一张没有文字、没有姓名、没有日期的温柔生日小卡。"
                    "只选一个与人格和用户偏好相称的具体意象，不画蛋糕上文字、不出现年龄、不要节庆海报或营销风。"
                    if reason == "birthday_celebration"
                    else "（非生日卡）"
                ),
                content_options=self._format_content_choice_options_for_prompt("photo_text"),
                style_name=style_name,
                style_instruction=style_instruction,
                prompt_format_instruction=prompt_format_instruction,
                reason=reason,
            )
        )["user"]
        text = ""
        try:
            text = await self._llm_call(
                prompt,
                max_tokens=260,
                provider_id=self._task_provider(
                    _persona_provider_id(self, "PHOTO_PROMPT_PROVIDER_ID", "photo_prompt_provider_id", "creative"),
                    _persona_provider_id(self, "MAI_STYLE_PROVIDER_ID", "mai_style_provider_id", "fast"),
                ),
                task="photo_prompt",
            )
        except Exception as exc:
            logger.debug(
                "proactive photo prompt model failed; using deterministic fallback: %s",
                _single_line(exc, 160),
            )
        payload = self._extract_json_payload(text or "")
        model_scene_valid = isinstance(payload, dict) and bool(
            _single_line(payload.get("prompt"), 600)
        )
        if model_scene_valid:
            kind = _single_line(payload.get("kind"), 20).lower()
            image_prompt = _single_line(payload.get("prompt"), 600)
            caption = _single_line(payload.get("caption"), 180)
            raw_use_reference = payload.get("use_persona_reference")
            if isinstance(raw_use_reference, bool):
                use_persona_reference = raw_use_reference
            elif str(raw_use_reference or "").strip().lower() in {"true", "1", "yes", "是", "使用"}:
                use_persona_reference = True
            elif str(raw_use_reference or "").strip().lower() in {"false", "0", "no", "否", "不使用"}:
                use_persona_reference = False
            else:
                use_persona_reference = not explicit_pure_scene
        else:
            kind = "text2img"
            image_prompt = ""
            caption = ""
            # When the scene model is unavailable, prefer a stable character photo
            # for ordinary proactive sharing instead of allowing an arbitrary face.
            use_persona_reference = not explicit_pure_scene
        model_person_scene, model_pure_scene = scene_subject_flags(
            f"{image_prompt} {caption}"
        )
        if model_pure_scene and not model_person_scene:
            explicit_pure_scene = True
            raw_model_reference = payload.get("use_persona_reference") if isinstance(payload, dict) else None
            explicit_model_reference = (
                isinstance(raw_model_reference, bool)
                or str(raw_model_reference or "").strip().lower()
                in {"true", "1", "yes", "是", "使用", "false", "0", "no", "否", "不使用"}
            )
            if not explicit_model_reference:
                use_persona_reference = False
        if kind not in {"selfie", "portrait", "自拍", "人像", "text2img", "scene", "photo", "风景"}:
            kind = "text2img"
        if kind in {"portrait", "自拍", "人像"}:
            kind = "selfie"
        if kind in {"scene", "photo", "风景"}:
            kind = "text2img"
        if kind == "selfie":
            use_persona_reference = True
        elif isinstance(payload, dict) and payload.get("use_persona_reference") is None:
            use_persona_reference = not explicit_pure_scene
        if not image_prompt:
            if topic_hint:
                image_prompt = (
                    f"Visual note: {topic_hint}; concrete visual subject kept faithful to this topic, "
                    f"natural everyday snapshot shared with a close friend, "
                    f"the moment is motivated by {motive_hint or 'a small moment worth sharing'}, "
                    f"{style_prompt_en}, clear composition, soft natural light"
                )
            else:
                image_prompt = (
                    f"Casual everyday snapshot with a concrete subject from the current context: "
                    f"{schedule_context or 'an ordinary daily moment'}, "
                    f"{motive_hint or 'a small moment worth sharing'}, "
                    f"{style_prompt_en}, clear composition, soft natural light"
                )
        if kind == "selfie":
            mirror_context = "；".join(
                part
                for part in (
                    f"reason={reason}",
                    f"topic={topic_hint}",
                    f"motive={motive_hint}",
                    f"schedule={schedule_context}",
                )
                if _single_line(part, 260)
            )
            image_prompt = self._sanitize_unrequested_mirror_selfie_prompt(
                image_prompt,
                context_text=mirror_context,
                limit=900,
            )
        if not caption:
            if topic_hint:
                caption = f"我把{topic_hint}这个小画面拍下来分享给你。"
            elif motive_hint:
                caption = f"刚好想把这个片刻拍下来给你看看：{motive_hint}。"
            else:
                caption = "今天看到一个很适合拍下来分享的小画面。"
        if use_persona_reference:
            subject_owner = "bot"
        else:
            character_text = f"{image_prompt} {caption}"
            subject_owner = (
                "third_party"
                if re.search(r"\b(?:person|people|man|woman|boy|girl|character|human)\b", character_text, flags=re.I)
                or any(token in character_text for token in ("人物", "男人", "女人", "男生", "女生", "男孩", "女孩", "路人"))
                else "scene"
            )
        if not use_persona_reference and subject_owner == "scene":
            image_prompt = _single_line(
                f"{image_prompt}; do not insert an unrequested person, character, visible photographer, "
                "back-view figure, face, body, silhouette, or reflection",
                900,
            )
        return {
            "kind": kind,
            "prompt": image_prompt,
            "caption": caption,
            "use_persona_reference": use_persona_reference,
            "subject_owner": subject_owner,
            "scene_context": scene_context,
            "prompt_format": prompt_format,
        }

    def _get_photo_style_instruction(self) -> tuple[str, str]:
        style = str(runtime_persona_setting(self, "photo_generation_style", "真实") or "真实").strip()
        if style == "二次元":
            return "二次元", "日系二次元插画风,人物与场景干净细腻,保留生活感,不要写实摄影质感"
        if style == "其他":
            custom = _single_line(
                runtime_persona_setting(self, "photo_generation_style_custom_prompt", ""),
                200,
            )
            if custom:
                return "其他", custom
            return "其他", "保持统一审美风格,自然生活感,避免默认写实照片风格"
        return "真实", "真实摄影风格,像手机随手拍到的生活照片,光线自然,细节可信"

    def _q5_structured_reference_assets_enabled(self) -> bool:
        return bool(runtime_persona_setting(self, "enable_p5_structured_reference_assets", False))

    def _q5_structured_reference_generation_mode(
        self,
        workflow_kind: str,
        prompt_text: str,
        reference_candidate: dict[str, Any],
    ) -> str:
        kind = _single_line(workflow_kind, 40).lower()
        if kind in {"edit", "改图", "修图", "重绘", "p图"}:
            return "edit"
        if _single_line(reference_candidate.get("kind"), 40).lower() == "recent_sent_photo" or re.search(
            r"续拍|继续拍|接着拍|再来一张|换个姿势|换个表情|same scene|continue the photo",
            str(prompt_text or ""),
            flags=re.I,
        ):
            return "continuation"
        return "new_topic"

    def _q5_prepare_structured_reference_plan(
        self,
        *,
        generation_id: str,
        workflow_kind: str,
        prompt_text: str,
        reference_candidate: dict[str, Any],
        explicit_reference_supplied: bool,
    ) -> tuple[ReferenceAssetGate | None, ReferenceAssetPlan | None, str]:
        if not self._q5_structured_reference_assets_enabled():
            return None, None, "disabled"
        # User-provided and quoted paths remain legacy single-image flows. They
        # can never be promoted into the managed multi-image sink.
        if explicit_reference_supplied:
            return None, None, "legacy_explicit_reference"
        gate = ReferenceAssetGate(getattr(self, "data_dir", ""))
        mode = self._q5_structured_reference_generation_mode(
            workflow_kind,
            prompt_text,
            reference_candidate,
        )
        plan, status = gate.plan(
            runtime_persona_setting(self, "photo_structured_reference_assets", []),
            generation_id=generation_id,
            mode=mode,
        )
        if not plan:
            logger.info(
                "Q5 受管参考素材未进入图片输入汇: trace=%s status=%s",
                _single_line(generation_id, 80),
                status,
            )
            return gate, None, status
        return gate, plan, "ok"

    @staticmethod
    def _q5_managed_reference_candidate(plan: ReferenceAssetPlan) -> dict[str, Any]:
        primary = plan.primary_asset
        if primary is None:
            return {}
        return {
            "id": primary.asset_id,
            "kind": "managed_asset",
            "source": "q5_reference_asset_gate",
            "note": "管理员登记并校验的受管身份参考素材",
            "reference_roles": [item.role for item in plan.assets],
            "outfit_lock_default": any(
                item.role == "outfit" and item.outfit_lock_default
                for item in plan.assets
            ),
            "metadata_source": "q5_reference_asset_gate",
        }

    def _photo_persona_reference_image_path(self) -> str:
        catalog = runtime_persona_setting(self, "photo_reference_catalog", None)
        if catalog is None:
            raw = _path_text(
                runtime_persona_setting(self, "photo_persona_reference_image_path", ""),
                1000,
            )
        else:
            persona = next(
                (
                    item
                    for item in (catalog or ())
                    if isinstance(item, PhotoReference) and item.kind == "persona"
                ),
                None,
            )
            raw = _path_text(persona.source if persona is not None else "", 1000)
        if not raw:
            return ""
        raw = raw.strip().strip('"').strip("'")
        if re.match(r"^https?://", raw, flags=re.I):
            return ""
        candidates = [Path(raw).expanduser()]
        if not candidates[0].is_absolute():
            candidates.append(Path(self.data_dir) / raw)
        for candidate in candidates:
            try:
                path = candidate.resolve()
            except Exception:
                path = candidate
            if not path.exists() or not path.is_file():
                continue
            if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
                continue
            return str(path)
        return ""

    @staticmethod
    def _photo_reference_normalize_roles(value: Any) -> list[str]:
        if isinstance(value, (list, tuple, set)):
            raw_items = list(value)
        else:
            raw_items = re.split(r"[,，、/|\s]+", str(value or ""))
        aliases = {
            "identity": "identity",
            "persona": "identity",
            "face": "identity",
            "人设": "identity",
            "身份": "identity",
            "人物": "identity",
            "脸": "identity",
            "outfit": "outfit",
            "wardrobe": "outfit",
            "clothing": "outfit",
            "服装": "outfit",
            "穿搭": "outfit",
            "pose": "pose",
            "姿势": "pose",
            "scene": "scene",
            "background": "scene",
            "场景": "scene",
            "背景": "scene",
            "style": "style",
            "画风": "style",
            "风格": "style",
            "continuity": "continuity",
            "连续性": "continuity",
            "source": "source",
            "原图": "source",
        }
        roles: list[str] = []
        for item in raw_items:
            key = str(item or "").strip().lower()
            normalized = aliases.get(key, "")
            if normalized and normalized not in roles:
                roles.append(normalized)
        return roles

    @staticmethod
    def _photo_outfit_category_matches(value: Any) -> list[tuple[str, int, int, str]]:
        text = re.sub(r"\s+", " ", str(value or "")).strip().lower()
        if not text:
            return []
        patterns = (
            ("cosplay", r"(?<![a-z0-9])cos(?:play)?(?![a-z0-9])|角色扮演|扮成|女仆装|巫女服|魔法少女|表演服"),
            ("school_uniform", r"校服|学院制服|学生制服|school[\s_-]*uniform"),
            ("sleepwear", r"睡衣|睡裙|睡袍|睡眠服|nightgown|nightdress|pajama|pyjama|sleepwear|bedtime outfit"),
            ("swimwear", r"泳装|泳衣|比基尼|swimsuit|swimwear|bikini"),
            ("sportswear", r"运动服|健身服|瑜伽服|球衣|sportswear|activewear|gym wear|jersey"),
            ("formalwear", r"礼服|晚礼服|正装|燕尾服|西装|tuxedo|formalwear|formal attire|evening gown|\bsuit\b"),
            ("homewear", r"居家服|家居服|家常服|宅家服|homewear|loungewear"),
            ("daily_outfit", r"今日穿搭|当天基础穿搭|当天穿搭|日常穿搭|today'?s outfit|daily outfit"),
        )
        matches: list[tuple[str, int, int, str]] = []
        for category, pattern in patterns:
            for match in re.finditer(pattern, text, flags=re.I):
                resolved_category = category
                if category == "homewear" and match.group(0).lower() == "loungewear":
                    context = text[max(0, match.start() - 40) : match.end() + 40]
                    if "bedtime" in context:
                        resolved_category = "sleepwear"
                matches.append((resolved_category, match.start(), match.end(), match.group(0)))
        matches.sort(key=lambda item: (item[1], item[2]))
        return matches

    @classmethod
    def _photo_outfit_category_from_text(cls, value: Any) -> str:
        matches = cls._photo_outfit_category_matches(value)
        return matches[0][0] if matches else ""

    @staticmethod
    def _photo_reference_scene_categories_from_text(value: Any) -> list[str]:
        text = re.sub(r"\s+", "", str(value or "")).lower()
        categories: list[str] = []
        mappings = (
            ("home", ("在家", "家里", "居家", "宅家", "home")),
            ("bedroom", ("卧室", "床边", "睡前", "刚起床", "bedroom", "bedtime")),
            ("school", ("上学", "校园", "教室", "校门", "school", "campus")),
            ("office", ("上班", "公司", "办公室", "office", "workplace")),
            ("outdoor", ("外出", "通勤", "逛街", "街头", "旅行", "outdoor", "commute")),
            ("formal_event", ("宴会", "舞会", "典礼", "正式场合", "banquet", "ceremony")),
            ("sport", ("运动", "健身", "跑步", "瑜伽", "球场", "gym", "sport")),
            ("beach", ("海边", "沙滩", "泳池", "beach", "pool")),
        )
        for category, tokens in mappings:
            if any(token in text for token in tokens):
                categories.append(category)
        return categories

    @staticmethod
    def _photo_reference_preset_for_category(category: str) -> str:
        return {
            "sleepwear": "居家睡衣",
            "homewear": "居家服",
            "cosplay": "COS自拍",
            "school_uniform": "校服人像",
            "formalwear": "礼服人像",
            "swimwear": "泳装人像",
            "sportswear": "运动服人像",
            "daily_outfit": "日常穿搭",
            "custom_outfit": "日常穿搭",
        }.get(str(category or "").strip().lower(), "")

    @staticmethod
    def _photo_reference_bool(value: Any, default: bool = False) -> bool:
        if isinstance(value, bool):
            return value
        if value is None or str(value).strip() == "":
            return default
        return str(value).strip().lower() in {"1", "true", "yes", "on", "是", "开启", "锁定"}

    def _normalize_photo_reference_candidate_metadata(self, item: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(item or {})
        note = _single_line(normalized.get("note") or normalized.get("description"), 700)
        kind = _single_line(normalized.get("kind"), 40).lower() or "library"
        explicit_roles = normalized.get("reference_roles", normalized.get("reference_role"))
        roles = self._photo_reference_normalize_roles(explicit_roles)
        raw_category = normalized.get("outfit_category") or normalized.get("wardrobe_category")
        if not raw_category and isinstance(normalized.get("wardrobe_categories"), (list, tuple)):
            raw_category = next(iter(normalized.get("wardrobe_categories") or []), "")
        category = _single_line(raw_category, 40).lower()
        if not category:
            category = self._photo_outfit_category_from_text(note)
        if not roles:
            if kind == "persona":
                roles = ["identity"]
            elif kind in {"daily_outfit", "recent_sent_photo"}:
                roles = ["identity", "outfit"]
                if kind == "recent_sent_photo":
                    roles.extend(["scene", "continuity"])
            elif re.search(r"仅(?:用于)?(?:人设|身份|脸|发型)|只(?:参考|用于)(?:人设|身份|脸|发型)|identity only", note, flags=re.I):
                roles = ["identity"]
            elif category:
                roles = ["identity", "outfit"]
            else:
                roles = ["identity"]
        if kind == "daily_outfit" and not category:
            category = "daily_outfit"
        scene_values = normalized.get("scene_categories", normalized.get("scene_tags"))
        if isinstance(scene_values, (list, tuple, set)):
            scene_categories = [
                _single_line(value, 40).lower()
                for value in scene_values
                if _single_line(value, 40)
            ]
        else:
            scene_categories = self._photo_reference_scene_categories_from_text(scene_values or note)
        time_values = normalized.get("time_categories", normalized.get("time_tags"))
        if isinstance(time_values, (list, tuple, set)):
            time_categories = [
                _single_line(value, 40).lower()
                for value in time_values
                if _single_line(value, 40)
            ]
        else:
            time_categories = [
                _single_line(value, 40).lower()
                for value in re.split(r"[,，、/|\s]+", str(time_values or ""))
                if _single_line(value, 40)
            ]
        lock_default = self._photo_reference_bool(
            normalized.get("outfit_lock_default"),
            default=bool("outfit" in roles and (category or kind in {"daily_outfit", "recent_sent_photo"})),
        )
        preferred_preset = _single_line(
            normalized.get("preferred_preset") or normalized.get("preset"),
            60,
        ) or self._photo_reference_preset_for_category(category)
        normalized.update(
            {
                "kind": kind,
                "note": note,
                "reference_roles": list(dict.fromkeys(roles)),
                "outfit_category": category,
                "outfit_lock_default": lock_default,
                "scene_categories": list(dict.fromkeys(scene_categories)),
                "time_categories": list(dict.fromkeys(time_categories)),
                "preferred_preset": preferred_preset,
                "metadata_source": _single_line(normalized.get("metadata_source"), 30)
                or ("configured" if explicit_roles is not None or normalized.get("outfit_category") else "inferred_note"),
            }
        )
        return normalized

    def _photo_reference_library_entries(self) -> list[dict[str, Any]]:
        if runtime_persona_setting(self, "photo_reference_catalog", None) is None:
            loaded = load_catalog(
                [],
                catalog_version=0,
                legacy_library=runtime_persona_setting(self, "photo_reference_library", []),
                preset_names=self._photo_generation_scene_presets().keys(),
            )
            entries = [project_reference_candidate(item) for item in loaded.references if item.kind == "library"]
            for index, entry in enumerate(entries):
                raw_items = runtime_persona_setting(self, "photo_reference_library", []) or []
                raw_item = raw_items[index] if isinstance(raw_items, list) and index < len(raw_items) else None
                entry["_config_format"] = "dict" if isinstance(raw_item, dict) else "text"
            return entries
        return [
            project_reference_candidate(item)
            for item in (runtime_persona_setting(self, "photo_reference_catalog", ()) or ())
            if isinstance(item, PhotoReference) and item.kind == "library"
        ]

    def _photo_reference_local_path(self, source: str) -> str:
        raw = _path_text(source, 1000)
        if not raw or re.match(r"^https?://", raw, flags=re.I):
            return ""
        candidates = [Path(raw).expanduser()]
        if not candidates[0].is_absolute():
            candidates.append(Path(self.data_dir) / raw)
        for candidate in candidates:
            try:
                path = candidate.resolve()
                if path.exists() and path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
                    return str(path)
            except (OSError, ValueError):
                continue
        return ""

    def _daily_outfit_reference_image_path(self) -> str:
        item = self.data.get("daily_outfit_photo") if isinstance(getattr(self, "data", None), dict) else {}
        if not isinstance(item, dict):
            return ""
        if _single_line(item.get("date"), 20) != _today_key():
            return ""
        raw = _path_text(item.get("path"), 1000)
        if not raw:
            return ""
        try:
            path = Path(raw).expanduser()
            if not path.is_absolute():
                path = Path(self.data_dir) / raw
            path = path.resolve()
        except Exception:
            path = Path(raw)
        try:
            if not path.exists() or not path.is_file():
                return ""
        except (OSError, ValueError):
            return ""
        if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            return ""
        return str(path)

    def _photo_persona_reference_image_for_kind(self, workflow_kind: str, *, allow_daily_outfit: bool = True) -> str:
        if not bool(runtime_persona_setting(self, "enable_photo_reference_image", False)):
            return ""
        if str(workflow_kind or "").strip().lower() not in {"selfie", "portrait", "自拍", "人像"}:
            return ""
        if allow_daily_outfit:
            outfit_path = self._daily_outfit_reference_image_path()
            if outfit_path:
                return outfit_path
        return self._photo_persona_reference_image_path()

    async def _photo_persona_reference_image_for_kind_async(
        self,
        workflow_kind: str,
        *,
        allow_daily_outfit: bool = True,
        requester_user_id: str = "",
        request_text: str = "",
        ambient_context: str = "",
        selection_context: str = "",
        suggested_scene_preset: str = "",
        continuity_key: str = "",
    ) -> str:
        if not bool(
            runtime_persona_setting(self, "enable_photo_reference_image", False)
        ):
            return ""
        if str(workflow_kind or "").strip().lower() not in {
            "selfie",
            "portrait",
            "自拍",
            "人像",
        }:
            return ""
        selector = getattr(self, "_select_photo_reference_candidate_async", None)
        if not callable(selector):
            return ""
        try:
            selected = await selector(
                workflow_kind,
                allow_daily_outfit=allow_daily_outfit,
                requester_user_id=requester_user_id,
                request_text=request_text,
                ambient_context=ambient_context,
                selection_context=selection_context,
                suggested_scene_preset=suggested_scene_preset,
                continuity_key=continuity_key,
            )
        except TypeError:
            selected = await selector(
                workflow_kind,
                allow_daily_outfit=allow_daily_outfit,
                request_text=request_text,
                ambient_context=ambient_context,
                selection_context=selection_context,
            )
        if isinstance(selected, SelectionResult):
            selected = selected.selected
        return _path_text(selected.get("path") if isinstance(selected, dict) else "", 1000)

    async def _photo_persona_reference_image_path_async(self) -> str:
        if not bool(runtime_persona_setting(self, "enable_photo_reference_image", False)):
            return ""
        local_path = self._photo_persona_reference_image_path()
        if local_path:
            return local_path
        catalog = runtime_persona_setting(self, "photo_reference_catalog", None)
        if catalog is None:
            raw = _path_text(
                runtime_persona_setting(self, "photo_persona_reference_image_path", ""),
                1000,
            )
        else:
            persona = next(
                (
                    item
                    for item in (catalog or ())
                    if isinstance(item, PhotoReference) and item.kind == "persona"
                ),
                None,
            )
            raw = _path_text(persona.source if persona is not None else "", 1000)
        if not raw or not re.match(r"^https?://", raw, flags=re.I):
            return ""
        resolver = getattr(self, "_photo_reference_source_to_stable_path", None)
        if not callable(resolver):
            return ""
        try:
            stable_path = await resolver(raw, stem="config_url_reference")
        except Exception as exc:
            logger.info("配置页人设参考图 URL 下载失败: %s url=%s", _single_line(exc, 120), _single_line(raw, 120))
            return ""
        if not stable_path:
            logger.info("配置页人设参考图 URL 未能转为本地参考图: url=%s", _single_line(raw, 120))
            return ""
        setter = getattr(self, "_set_photo_reference_config_path", None)
        if callable(setter):
            try:
                result = setter(stable_path)
                if hasattr(result, "__await__"):
                    result = await result
                if result is False:
                    logger.info(
                        "配置页人设参考图 URL 已下载但配置保存返回失败: path=%s",
                        _single_line(stable_path, 160),
                    )
            except Exception as exc:
                logger.info("配置页人设参考图 URL 已下载但回写失败: %s path=%s", _single_line(exc, 120), _single_line(stable_path, 160))
        logger.info("配置页人设参考图 URL 已缓存为本地文件: path=%s", _single_line(stable_path, 160))
        return stable_path

    def _photo_reference_config_value(self, item: dict[str, Any], source: str = "") -> Any:
        persisted_source = _path_text(source or item.get("source"), 1000)
        note = _single_line(item.get("note"), 500)
        if item.get("_config_format") != "dict":
            return f"{persisted_source} || {note}" if note else persisted_source
        return {
            "path": persisted_source,
            "note": note,
            "reference_roles": list(item.get("reference_roles") or []),
            "outfit_category": _single_line(item.get("outfit_category"), 40),
            "outfit_lock_default": bool(item.get("outfit_lock_default")),
            "scene_categories": list(item.get("scene_categories") or []),
            "preferred_preset": _single_line(item.get("preferred_preset"), 60),
        }

    def _photo_reference_asset_records(self) -> list[dict[str, Any]]:
        raw = self.data.get("photo_reference_assets") if isinstance(getattr(self, "data", None), dict) else []
        records: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in raw if isinstance(raw, list) else []:
            normalized = normalize_reference_asset(item)
            if not normalized or normalized["id"] in seen:
                continue
            seen.add(normalized["id"])
            records.append(normalized)
        return records

    def _photo_reference_asset_path(self, asset: dict[str, Any]) -> str:
        source = _path_text(asset.get("path") or asset.get("source"), 1200)
        if not source:
            return ""
        resolver = getattr(self, "_photo_reference_local_path", None)
        if callable(resolver):
            try:
                resolved = _path_text(resolver(source), 1200)
                if resolved:
                    return resolved
            except Exception:
                pass
        try:
            path = Path(source).expanduser().resolve()
            if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
                return str(path)
        except (OSError, ValueError):
            pass
        return ""

    def _photo_reference_relation_owner_ids(self, requester_user_id: str, request_text: str) -> set[str]:
        owners: set[str] = set()
        raw_requester = _single_line(requester_user_id, 80)
        canonicalizer = getattr(self, "_canonical_private_user_id", None)
        if raw_requester:
            owners.add(raw_requester)
            if callable(canonicalizer):
                try:
                    canonical = _single_line(canonicalizer(raw_requester), 80)
                    if canonical:
                        owners.add(canonical)
                except Exception:
                    pass
        profiles = self.data.get("worldbook_member_profiles") if isinstance(getattr(self, "data", None), dict) else {}
        text = re.sub(r"\s+", "", str(request_text or "")).lower()
        if not isinstance(profiles, dict) or not text:
            return owners
        for profile_id, profile in profiles.items():
            if not isinstance(profile, dict) or profile.get("enabled", True) is False:
                continue
            tokens = [profile_id, profile.get("name"), *(profile.get("aliases") or []), *(profile.get("observed_names") or [])]
            if any(len(re.sub(r"\s+", "", str(token or ""))) >= 2 and re.sub(r"\s+", "", str(token or "")).lower() in text for token in tokens):
                owners.add(str(profile_id))
        return owners

    def _photo_reference_relation_asset_candidates(self, *, requester_user_id: str, request_text: str) -> list[dict[str, Any]]:
        owners = self._photo_reference_relation_owner_ids(requester_user_id, request_text)
        if not owners:
            return []
        candidates: list[dict[str, Any]] = []
        for asset in self._photo_reference_asset_records():
            if asset.get("scope") != "relation_user" or asset.get("owner_id") not in owners or asset.get("enabled") is False:
                continue
            path = self._photo_reference_asset_path(asset)
            if not path:
                continue
            roles = list(asset.get("reference_roles") or ("identity",))
            candidates.append({
                "id": asset.get("id"),
                "kind": "relation_user",
                "scope": "relation_user",
                "owner_id": asset.get("owner_id"),
                "path": path,
                "source": asset.get("path"),
                "title": asset.get("title"),
                "note": asset.get("note"),
                "tags": list(asset.get("tags") or []),
                "reference_roles": roles,
                "available_reference_roles": roles,
                "priority": max(650, _safe_int(asset.get("priority"), 0, -1000)),
                "metadata_source": "relation_user",
            })
        return candidates

    def _photo_reference_role_asset_candidates(self, *, request_text: str) -> list[dict[str, Any]]:
        """Resolve setting/relationship-card references only for an explicit role context.

        Role cards describe people other than Bot.  Loading their images for every
        selfie would make an otherwise single-person request ambiguous, so the
        asset is eligible only when the current request names the role/name or
        clearly asks for a group frame.
        """
        if not bool(runtime_persona_setting(self, "enable_bot_relationship_network", False)):
            return []
        cards = self._normalize_bot_relationship_cards(
            runtime_persona_setting(self, "bot_relationship_cards", [])
        )
        if not cards:
            return []
        request_compact = re.sub(r"\s+", "", str(request_text or "")).casefold()
        group_requested = _photo_group_request_matches(request_text)
        role_context: dict[str, dict[str, str]] = {}
        for raw_card in cards:
            parts = [_single_line(part, 200) for part in raw_card.split(" || ", 2)]
            role_name = parts[0] if parts else ""
            if not role_name:
                continue
            owner_id = normalize_reference_owner_id("relation_role", role_name)
            if not owner_id:
                continue
            relation = parts[1] if len(parts) > 1 else ""
            tokens = [role_name, relation]
            explicit_hit = any(
                len(re.sub(r"\s+", "", str(token or ""))) >= 2
                and re.sub(r"\s+", "", str(token or "")).casefold() in request_compact
                for token in tokens
            )
            if explicit_hit or group_requested:
                role_context[owner_id] = {
                    "role_name": role_name,
                    "relationship": relation,
                    "appearance": parts[2] if len(parts) > 2 else "",
                    "explicit_mention": "1" if explicit_hit else "0",
                }
        if not role_context:
            return []
        candidates: list[dict[str, Any]] = []
        for asset in self._photo_reference_asset_records():
            if asset.get("scope") != "relation_role" or asset.get("enabled") is False:
                continue
            owner_id = str(asset.get("owner_id") or "")
            context = role_context.get(owner_id)
            if not context:
                continue
            path = self._photo_reference_asset_path(asset)
            if not path:
                continue
            roles = list(asset.get("reference_roles") or ("identity",))
            candidates.append(
                {
                    "id": asset.get("id"),
                    "kind": "relation_role",
                    "scope": "relation_role",
                    "owner_id": owner_id,
                    "path": path,
                    "source": asset.get("path"),
                    "title": asset.get("title"),
                    "note": asset.get("note"),
                    "tags": list(asset.get("tags") or []),
                    "reference_roles": roles,
                    "available_reference_roles": roles,
                    "priority": max(700, _safe_int(asset.get("priority"), 0, -1000)),
                    "metadata_source": "relation_role",
                    "role_name": context["role_name"],
                    "relationship": context["relationship"],
                    "role_appearance": context["appearance"],
                    "role_explicit_mention": context["explicit_mention"] == "1",
                    "group_photo_requested": group_requested,
                }
            )
        return candidates

    def _photo_reference_knowledge_asset_candidates(self, *, request_text: str, ambient_context: str) -> list[dict[str, Any]]:
        selected = {
            str(item or "").strip()
            for item in (runtime_persona_setting(self, "roleplay_knowledge_source_ids", None) or [])
            if str(item or "").strip().startswith(("kb:", "doc:"))
        }
        if not selected:
            return []
        combined = re.sub(r"\s+", "", f"{request_text}\n{ambient_context}").lower()
        candidates: list[dict[str, Any]] = []
        for asset in self._photo_reference_asset_records():
            if asset.get("scope") != "knowledge" or asset.get("enabled") is False:
                continue
            owner = str(asset.get("owner_id") or "")
            if owner.startswith("doc:"):
                parts = owner.split(":", 2)
                if owner not in selected and (len(parts) < 3 or f"kb:{parts[1]}" not in selected):
                    continue
            elif owner not in selected:
                continue
            tokens = reference_asset_tokens(asset)
            if not tokens or not any(token in combined for token in tokens):
                continue
            path = self._photo_reference_asset_path(asset)
            if not path:
                continue
            roles = list(asset.get("reference_roles") or ("scene", "style"))
            candidates.append({
                "id": asset.get("id"),
                "kind": "knowledge_reference",
                "scope": "knowledge",
                "owner_id": owner,
                "path": path,
                "source": asset.get("path"),
                "title": asset.get("title"),
                "note": asset.get("note"),
                "tags": list(asset.get("tags") or []),
                "reference_roles": roles,
                "available_reference_roles": roles,
                "priority": max(520, _safe_int(asset.get("priority"), 0, -1000)),
                "metadata_source": "knowledge_reference",
                "knowledge_context_match": True,
            })
        return candidates

    async def _photo_reference_candidates_async(
        self,
        *,
        allow_daily_outfit: bool = True,
        requester_user_id: str = "",
        request_text: str = "",
        ambient_context: str = "",
        scoped_only: bool = False,
    ) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        canonical_mode = runtime_persona_setting(self, "photo_reference_catalog", None) is not None
        if canonical_mode:
            catalog = tuple(runtime_persona_setting(self, "photo_reference_catalog", ()) or ())
        else:
            catalog = load_catalog(
                [],
                catalog_version=0,
                legacy_persona=runtime_persona_setting(
                    self, "photo_persona_reference_image_path", ""
                ),
                legacy_library=runtime_persona_setting(self, "photo_reference_library", []),
                preset_names=self._photo_generation_scene_presets().keys(),
            ).references
        updated_catalog = list(catalog)
        catalog_changed = False
        resolver = getattr(self, "_photo_reference_source_to_stable_path", None)
        for index, item in enumerate(catalog):
            if not isinstance(item, PhotoReference) or item.kind != "library":
                continue
            source = item.source
            path = self._photo_reference_local_path(source)
            if not path and re.match(r"^https?://", source, flags=re.I) and callable(resolver):
                try:
                    path = await resolver(source, stem=item.id)
                except Exception as exc:
                    logger.info(
                        "参考图库远程图片下载失败: item=%s error=%s",
                        item.id,
                        _single_line(exc, 120),
                    )
                if path:
                    updated_catalog[index] = replace(item, source=path)
                    catalog_changed = True
            if path:
                candidates.append(project_reference_candidate(item, resolved_source=path))
        if catalog_changed:
            setter = getattr(
                self,
                "_set_photo_reference_catalog_config" if canonical_mode else "_set_photo_reference_library_config",
                None,
            )
            if callable(setter):
                try:
                    payload: Any = updated_catalog
                    if not canonical_mode:
                        payload = [
                            {
                                "path": item.source,
                                "note": item.note,
                                "reference_roles": list(item.reference_roles),
                                "outfit_category": item.outfit_category,
                                "outfit_lock_default": item.outfit_lock_default,
                                "scene_categories": list(item.scene_categories),
                                "preferred_preset": item.preferred_preset,
                            }
                            for item in updated_catalog
                            if isinstance(item, PhotoReference) and item.kind == "library"
                        ]
                    result = setter(payload)
                    if hasattr(result, "__await__"):
                        result = await result
                    if result is False:
                        logger.info("参考图库远程图片已下载但配置保存返回失败")
                except Exception as exc:
                    logger.info(
                        "参考图库远程图片已下载但回写失败: %s",
                        _single_line(exc, 120),
                    )

        if allow_daily_outfit:
            outfit_path = self._daily_outfit_reference_image_path()
            if outfit_path:
                daily_reference = build_daily_outfit_reference(
                    outfit_path,
                    note="今天生成的外出穿搭；仅在画面明确承接今天外出、通勤、上学、逛街或展示当日穿搭时使用，在家、卧室、睡前、刚起床等场景不要使用",
                    preset_names=self._photo_generation_scene_presets().keys(),
                )
                candidates.append(project_reference_candidate(daily_reference, resolved_source=outfit_path))
        persona_path = await self._photo_persona_reference_image_path_async()
        if persona_path and not any(item.get("kind") == "persona" for item in candidates):
            persona = next(
                (
                    item
                    for item in catalog
                    if isinstance(item, PhotoReference) and item.kind == "persona"
                ),
                None,
            )
            if persona is not None:
                candidates.append(project_reference_candidate(persona, resolved_source=persona_path))
            else:
                candidates.append(
                    {
                        "id": "persona",
                        "kind": "persona",
                        "path": persona_path,
                        "source": persona_path,
                        "note": "Bot persona identity reference",
                        "reference_roles": ["identity"],
                        "available_reference_roles": ["identity"],
                        "priority": 400,
                        "metadata_source": "legacy_persona",
                        "outfit_lock_default": False,
                    }
                )
        candidates.extend(
            self._photo_reference_role_asset_candidates(
                request_text=request_text,
            )
        )
        candidates.extend(
            self._photo_reference_relation_asset_candidates(
                requester_user_id=requester_user_id,
                request_text=request_text,
            )
        )
        candidates.extend(
            self._photo_reference_knowledge_asset_candidates(
                request_text=request_text,
                ambient_context=ambient_context,
            )
        )
        if scoped_only:
            candidates = [
                item
                for item in candidates
                if item.get("kind") in {"relation_user", "relation_role", "knowledge_reference"}
            ]
        return candidates

    @staticmethod
    def _photo_reference_candidate_score(
        candidate: dict[str, Any],
        request_text: str,
        ambient_context: str,
        *,
        schedule_history_context: str = "",
        wardrobe_intent: PhotoWardrobeIntent,
        requested_outfit_category: str = "",
    ) -> float:
        request_context = re.sub(r"\s+", "", str(request_text or "")).lower()
        ambient = re.sub(r"\s+", "", str(ambient_context or "")).lower()
        schedule_history = re.sub(r"\s+", "", str(schedule_history_context or "")).lower()
        note = re.sub(r"\s+", "", str(candidate.get("note") or "")).lower()
        kind = candidate.get("kind")
        score = 2.0 if kind == "persona" else 1.0
        if kind == "relation_role":
            # A named role is a stronger signal than an unrelated persona or
            # library image; group intent is still a softer, contextual signal.
            score += 6.0 if candidate.get("role_explicit_mention") else 3.0
        categories = (
            ("home", ("在家", "家里", "居家", "宿舍", "公寓", "卧室", "房间", "客厅", "宅家", "居家室内", "室内日常")),
            ("sleep", ("睡衣", "睡前", "起床", "刚醒", "床上", "夜晚休息")),
            ("outdoor", ("外出", "通勤", "上学", "上班", "逛街", "商场", "街头", "旅行")),
            ("sport", ("运动", "健身", "跑步", "瑜伽", "泳装", "游泳")),
            ("formal", ("正式", "礼服", "宴会", "约会", "聚会", "舞会")),
            ("cos", ("cos", "cosplay", "角色扮演", "制服", "表演服")),
        )
        for name, words in categories:
            request_hit = any(word in request_context for word in words)
            ambient_hit = any(word in ambient for word in words)
            history_hit = any(word in schedule_history for word in words)
            note_hit = any(word in note for word in words)
            if (request_hit or ambient_hit) and candidate.get("kind") == "daily_outfit" and name in {"home", "sleep"}:
                score -= 20.0
            elif note_hit:
                if request_hit:
                    score += 12.0
                if ambient_hit:
                    score += 6.0
                if history_hit:
                    score += 2.0
        candidate_category = str(candidate.get("outfit_category") or "").strip().lower()
        outfit_bearing = "outfit" in set(candidate.get("reference_roles") or ())
        if not outfit_bearing:
            candidate_category = ""
        requested_category = (
            _single_line(requested_outfit_category, 40).lower()
            or wardrobe_intent.target_category
        )
        excluded_categories = set(wardrobe_intent.excluded_categories)
        if candidate_category and candidate_category in excluded_categories:
            score -= 40.0
        elif candidate_category and candidate_category == requested_category:
            score += 18.0
        elif requested_category and candidate_category and candidate_category != "daily_outfit":
            score -= 6.0
        if requested_category == "custom_outfit" and outfit_bearing and bool(candidate.get("outfit_lock_default")):
            score -= 8.0
        structured_scenes = {
            str(value or "").strip().lower()
            for value in (candidate.get("scene_categories") or [])
            if str(value or "").strip()
        }
        def scene_categories(text: str) -> set[str]:
            scenes: set[str] = set()
            if any(token in text for token in ("在家", "家里", "居家", "宿舍", "卧室", "居家室内")):
                scenes.add("home")
            if any(token in text for token in ("卧室", "床边", "睡前", "刚起床")):
                scenes.add("bedroom")
            if any(token in text for token in ("上学", "校园", "教室", "校门")):
                scenes.add("school")
            if any(token in text for token in ("外出", "通勤", "逛街", "街头", "旅行")):
                scenes.add("outdoor")
            if any(token in text for token in ("办公室", "办公", "公司", "工作场所", "office", "workplace")):
                scenes.add("office")
            if any(token in text for token in ("正式场合", "宴会", "婚礼", "舞会", "典礼", "formal event", "banquet")):
                scenes.add("formal_event")
            if any(token in text for token in ("运动", "健身", "跑步", "瑜伽", "球场", "体育馆", "gym", "sport")):
                scenes.add("sport")
            if any(token in text for token in ("海边", "海滩", "沙滩", "泳池", "beach", "seaside", "pool")):
                scenes.add("beach")
            return scenes

        if structured_scenes & scene_categories(request_context):
            score += 10.0
        if structured_scenes & scene_categories(ambient):
            score += 4.0
        if structured_scenes & scene_categories(schedule_history):
            score += 2.0
        structured_times = {
            str(value or "").strip().lower()
            for value in (candidate.get("time_categories") or [])
            if str(value or "").strip()
        }

        def time_categories(text: str) -> set[str]:
            times: set[str] = set()
            mappings = (
                ("morning", ("清晨", "早晨", "早上", "晨间", "morning", "sunrise")),
                ("daytime", ("白天", "日间", "daytime", "daylight")),
                ("afternoon", ("下午", "午后", "afternoon")),
                ("evening", ("傍晚", "黄昏", "日落", "evening", "sunset")),
                ("night", ("夜晚", "晚上", "深夜", "夜景", "night")),
                ("bedtime", ("睡前", "临睡", "bedtime")),
            )
            for category, tokens in mappings:
                if any(token in text for token in tokens):
                    times.add(category)
            return times

        if structured_times & time_categories(request_context):
            score += 8.0
        if structured_times & time_categories(ambient):
            score += 3.0
        if structured_times & time_categories(schedule_history):
            score += 1.0
        for token in re.split(r"[，,。；;、/|：:\s]+", note):
            if len(token) >= 2 and token in request_context:
                score += min(6.0, float(len(token)))
            elif len(token) >= 2 and token in ambient:
                score += min(3.0, float(len(token)) / 2.0)
            elif len(token) >= 2 and token in schedule_history:
                score += min(1.0, float(len(token)) / 4.0)
        return score

    @staticmethod
    def _photo_reference_selection_prompt_document(
        *,
        request_text: str,
        ambient_context: str,
        suggested_scene_preset: str,
        schedule_history_context: str,
        candidate_options: str,
    ) -> PromptDocument:
        return prompt_document(
            user_render=_PROACTIVE_DOCUMENT_RENDER,
            user=(
                _proactive_prompt_part(prompt_section(
                    key="background.photo_reference_selection.task",
                    title="人物参考图选择",
                    source="proactive_message",
                    content=(
                        "你在为角色生图选择一张人物参考图。结合最终画面需求中的日程、位置、当前场景和服装需求，按管理员给每张图的用途注释判断。\n"
                        "优先选择用途更具体且与当前场景兼容的参考图；只有没有更具体的场景或服装参考时，才选择基础人物身份图。\n"
                        "严格遵守候选的选用策略、排除场景与排除时间；条件不匹配时输出 0，不要为了使用参考图而曲解用户原话。\n"
                        "明确处于家里、卧室、睡前或刚起床时，优先在适用的居家服/睡衣参考中选择；只有明确外出、通勤、上学、逛街或展示今日穿搭时才选今日穿搭。\n"
                        "当前要求明确否定某类服装时，不得选择以该服装为职责的参考图；即使它是唯一候选，也应输出 0。普通换装或自定义衣服没有匹配参考时，可选身份图或输出 0，不要让旧衣服反向覆盖新要求。\n"
                        "用户原始要求高于环境上下文；两者冲突时必须按用户原始要求选图，不能让日程或位置覆盖用户明确要求。\n"
                        "若用户没有明确服装要求，但结构化场景预设给出了服装类别，且候选中存在同类别服装参考，优先选择该服装参考，不要改选基础身份图。结构化预设只用于补足空白，不得覆盖用户明确要求。\n"
                        "当天已发生日程只可作为较弱的经历、服装和连续性线索，不代表当前位置或当前活动。不得用历史中的旧地点覆盖当前环境；用户原始要求和当前环境始终优先于历史日程。\n"
                        "不要仅凭疲惫、揉眼睛、电脑桌等间接描述猜测地点或服装；场景不明确时保持保守，不要虚构居家或外出状态。\n"
                        "若候选带有“角色”和“关系”，且用户在本轮明确点名该角色或关系，优先选择对应的关系角色参考图；它只代表该角色本人，不要把该身份转移给 Bot。没有明确点名角色时，不要因为关系卡文字而选择关系角色参考图。\n"
                        "只输出候选编号，不要解释。"
                    ),
                ), mode=PromptRenderMode.BODY_ONLY),
                prompt_section(
                    key="background.photo_reference_selection.request",
                    title="最终画面需求",
                    source="proactive_message",
                    content=request_text,
                ),
                prompt_section(
                    key="background.photo_reference_selection.environment",
                    title="环境上下文",
                    source="proactive_message",
                    content=ambient_context or "无",
                ),
                prompt_section(
                    key="background.photo_reference_selection.preset",
                    title="结构化场景预设",
                    source="proactive_message",
                    content=suggested_scene_preset or "无",
                ),
                prompt_section(
                    key="background.photo_reference_selection.schedule",
                    title="当天已发生日程",
                    source="proactive_message",
                    content=schedule_history_context or "无",
                ),
                prompt_section(
                    key="background.photo_reference_selection.candidates",
                    title="候选参考图",
                    source="proactive_message",
                    content=candidate_options,
                ),
            ),
            metadata={"task": "photo_reference_selection"},
        )

    async def _select_photo_reference_candidate_async(
        self,
        workflow_kind: str,
        *,
        allow_daily_outfit: bool = True,
        requester_user_id: str = "",
        request_text: str = "",
        ambient_context: str = "",
        schedule_history_context: str = "",
        selection_context: str = "",
        suggested_scene_preset: str = "",
        continuity_key: str = "",
        wardrobe_intent: PhotoWardrobeIntent | None = None,
        trace_id: str = "",
        candidate_overrides: Any = None,
        selection_provider_id: str = "",
        selection_strict_provider: bool = False,
        return_selection_result: bool = False,
    ) -> dict[str, Any] | SelectionResult:
        def empty_selection(reason: str) -> dict[str, Any] | SelectionResult:
            if return_selection_result:
                return SelectionResult(None, (), "none", reason)
            return {}

        using_candidate_overrides = candidate_overrides is not None
        if not using_candidate_overrides and not bool(
            runtime_persona_setting(self, "enable_photo_reference_image", False)
        ):
            return empty_selection("reference_feature_disabled")
        normalized_workflow = str(workflow_kind or "").strip().lower()
        portrait_workflow = normalized_workflow in {"selfie", "portrait", "自拍", "人像"}
        scoped_context = bool(requester_user_id) or bool(
            self._photo_reference_knowledge_asset_candidates(
                request_text=request_text,
                ambient_context=ambient_context,
            )
        ) or bool(self._photo_reference_role_asset_candidates(request_text=request_text))
        if not portrait_workflow and not scoped_context and not using_candidate_overrides:
            return empty_selection("workflow_does_not_use_reference")
        if using_candidate_overrides:
            candidates = []
            for raw_candidate in candidate_overrides or ():
                if not isinstance(raw_candidate, dict):
                    continue
                candidate = self._normalize_photo_reference_candidate_metadata(dict(raw_candidate))
                if not candidate.get("path") and candidate.get("source"):
                    candidate["path"] = candidate["source"]
                candidates.append(candidate)
        else:
            try:
                candidates = await self._photo_reference_candidates_async(
                    allow_daily_outfit=allow_daily_outfit,
                    requester_user_id=requester_user_id,
                    request_text=request_text,
                    ambient_context=ambient_context,
                    scoped_only=not portrait_workflow,
                )
            except TypeError:
                # Keep compatibility with lightweight test/integration adapters that
                # still expose the original one-argument candidate loader.
                candidates = await self._photo_reference_candidates_async(
                    allow_daily_outfit=allow_daily_outfit,
                )
        if not candidates:
            return empty_selection("no_candidates")
        legacy_context = str(selection_context or "").strip()
        if legacy_context:
            looks_like_ambient_context = bool(
                re.search(
                    r"(?:^|[；;，,])\s*(?:时间|状态|当前日程|日程|情绪|可分享碎片|"
                    r"当前位置|当前场景|天气背景|今日穿搭|当天基础穿搭|当天穿搭|日常穿搭)\s*[：:]",
                    legacy_context,
                    flags=re.I,
                )
            )
            if not request_text and not ambient_context:
                if looks_like_ambient_context:
                    ambient_context = legacy_context
                else:
                    request_text = legacy_context
            elif not ambient_context:
                ambient_context = legacy_context
        wardrobe_intent = wardrobe_intent or analyze_photo_wardrobe(request_text)
        suggested_scene_preset = _single_line(suggested_scene_preset, 80)
        suggested_category = ""
        available_presets = self._photo_generation_scene_presets()
        if (
            not wardrobe_intent.target_category
            and suggested_scene_preset
            and suggested_scene_preset in available_presets
        ):
            suggested_category = self._photo_outfit_category_from_text(
                suggested_scene_preset
            )
        requested_category = wardrobe_intent.target_category or suggested_category
        excluded_categories = set(wardrobe_intent.excluded_categories)
        request_scenes, request_times, request_excluded_scenes, request_excluded_times = (
            parse_photo_reference_context_categories(request_text)
        )
        suggested_scenes, suggested_times, suggested_excluded_scenes, suggested_excluded_times = (
            parse_photo_reference_context_categories(suggested_scene_preset)
        )
        ambient_scenes, ambient_times, ambient_excluded_scenes, ambient_excluded_times = (
            parse_photo_reference_context_categories(ambient_context)
        )
        # Hard eligibility follows the strongest current signal. Historical schedule
        # text remains a weak score/prompt hint and must never override this turn.
        if request_scenes or request_excluded_scenes:
            requested_scene_categories = request_scenes
            excluded_scene_categories = request_excluded_scenes
        elif suggested_scenes or suggested_excluded_scenes:
            requested_scene_categories = suggested_scenes
            excluded_scene_categories = suggested_excluded_scenes
        else:
            requested_scene_categories = ambient_scenes
            excluded_scene_categories = ambient_excluded_scenes
        if request_times or request_excluded_times:
            requested_time_categories = request_times
            excluded_time_categories = request_excluded_times
        elif suggested_times or suggested_excluded_times:
            requested_time_categories = suggested_times
            excluded_time_categories = suggested_excluded_times
        else:
            requested_time_categories = ambient_times
            excluded_time_categories = ambient_excluded_times

        seen_candidate_ids: set[str] = set()
        for index, item in enumerate(candidates, start=1):
            base_id = _single_line(item.get("id"), 120) or f"candidate-{index}"
            candidate_id = base_id
            suffix = 2
            while candidate_id in seen_candidate_ids:
                candidate_id = f"{base_id}#{suffix}"
                suffix += 1
            item["id"] = candidate_id
            seen_candidate_ids.add(candidate_id)

        policy_result = select_photo_reference(
            {
                "request_text": request_text,
                "outfit_category": requested_category,
                "scene_categories": requested_scene_categories,
                "time_categories": requested_time_categories,
                "excluded_scene_categories": excluded_scene_categories,
                "excluded_time_categories": excluded_time_categories,
            },
            candidates,
        )
        policy_matches = {item.candidate_id: item for item in policy_result.candidates}
        candidate_policy_exclusions: dict[str, set[str]] = {}
        eligible_candidates: list[dict[str, Any]] = []
        for item in candidates:
            item_id = str(item.get("id") or "")
            reasons = set(policy_matches.get(item_id).excluded if item_id in policy_matches else ())
            candidate_policy_exclusions[item_id] = reasons
            if not reasons:
                eligible_candidates.append(item)

        scored_candidates = [
            (
                item,
                self._photo_reference_candidate_score(
                    item,
                    request_text,
                    ambient_context,
                    schedule_history_context=schedule_history_context,
                    wardrobe_intent=wardrobe_intent,
                    requested_outfit_category=requested_category,
                ),
            )
            for item in candidates
        ]
        def responsible_outfit_category(item: dict[str, Any]) -> str:
            if "outfit" not in set(item.get("reference_roles") or ()):
                return ""
            return str(item.get("outfit_category") or "").strip().lower()

        normal_scored = [
            pair
            for pair in scored_candidates
            if not candidate_policy_exclusions.get(str(pair[0].get("id") or ""))
            and responsible_outfit_category(pair[0]) not in excluded_categories
            and (
                not requested_category
                or not responsible_outfit_category(pair[0])
                or (
                    requested_category != "custom_outfit"
                    and responsible_outfit_category(pair[0]) == requested_category
                )
            )
        ]
        fallback = max(normal_scored, key=lambda pair: pair[1])[0] if normal_scored else None
        selected = fallback
        selection_source = "rule_fallback"
        selection_reason = "model_not_attempted" if eligible_candidates else "no_eligible_reference"
        model_reply = ""
        provider_id = _single_line(selection_provider_id, 160)
        provider_selector = getattr(self, "_task_provider", None)
        if not provider_id and callable(provider_selector):
            provider_id = provider_selector(
                _persona_provider_id(
                    self, "PHOTO_PROMPT_PROVIDER_ID", "photo_prompt_provider_id", "creative"
                ),
                _persona_provider_id(
                    self, "FAST_RESPONSE_PROVIDER_ID", "fast_response_provider_id", "fast"
                ),
                _persona_provider_id(
                    self, "LLM_PROVIDER_ID", "llm_provider_id", "complex"
                ),
                _persona_provider_id(
                    self, "MAI_STYLE_PROVIDER_ID", "mai_style_provider_id", "fast"
                ),
            )
        llm_call = getattr(self, "_llm_call", None)
        specialized_candidate = any(
            bool(item.get("outfit_lock_default"))
            or any(role in {"outfit", "scene", "continuity"} for role in (item.get("reference_roles") or []))
            for item in eligible_candidates
        )
        needs_model_choice = len(eligible_candidates) > 1 or specialized_candidate
        model_attempted = False
        model_selected_id = ""
        if (request_text or ambient_context or schedule_history_context) and needs_model_choice and callable(llm_call):
            model_attempted = True
            selection_reason = "model_invalid_response"
            options = "\n".join(
                f"{index}. id={item['id']}；角色={_single_line(item.get('role_name'), 80) or 'Bot/未指定'}；"
                f"关系={_single_line(item.get('relationship'), 80) or 'none'}；职责={','.join(item.get('reference_roles') or []) or 'identity'}；"
                f"服装类别={_single_line(item.get('outfit_category'), 40) or 'none'}；"
                f"场景类别={','.join(sorted(str(value) for value in (item.get('scene_categories') or []) if str(value).strip())) or 'none'}；"
                f"时间类别={','.join(sorted(str(value) for value in (item.get('time_categories') or []) if str(value).strip())) or 'none'}；"
                f"选用策略={_single_line(item.get('selection_eligibility'), 40) or 'matching_only'}；"
                f"排除场景={','.join(sorted(str(value) for value in (item.get('excluded_scene_categories') or []) if str(value).strip())) or 'none'}；"
                f"排除时间={','.join(sorted(str(value) for value in (item.get('excluded_time_categories') or []) if str(value).strip())) or 'none'}；"
                f"默认锁服装={bool(item.get('outfit_lock_default'))}；注释={_single_line(item.get('note'), 360)}"
                for index, item in enumerate(eligible_candidates, start=1)
            )
            none_option = "\n0. 不使用这些候选参考图，按当前要求生成全新画面"
            prompt = render_prompt_document(
                self._photo_reference_selection_prompt_document(
                    request_text=_single_line(request_text, 1200),
                    ambient_context=_single_line(ambient_context, 800),
                    suggested_scene_preset=suggested_scene_preset,
                    schedule_history_context=_single_line(schedule_history_context, 1200),
                    candidate_options=f"{options}{none_option}",
                )
            )["user"]
            try:
                llm_kwargs = {
                    "max_tokens": 12,
                    "provider_id": provider_id or None,
                    "task": "photo_reference_selection",
                }
                if selection_strict_provider:
                    llm_kwargs["strict_provider"] = True
                raw = await llm_call(prompt, **llm_kwargs)
                model_reply = _single_line(raw, 80)
                match = re.search(r"(?<!\d)(\d{1,2})(?!\d)", model_reply)
                choice = int(match.group(1)) if match else -1
                if match and choice == 0:
                    selected = None
                    selection_source = "model"
                    selection_reason = "fresh_image_requested"
                elif match and 1 <= choice <= len(eligible_candidates):
                    proposed = eligible_candidates[choice - 1]
                    model_selected_id = str(proposed.get("id") or "")
                    proposed_category = responsible_outfit_category(proposed)
                    if proposed_category and proposed_category in excluded_categories:
                        selected = None
                        selection_source = "semantic_exclusion"
                        selection_reason = "model_selected_explicitly_excluded_outfit"
                    elif (
                        requested_category
                        and proposed_category
                        and (
                            requested_category == "custom_outfit"
                            or proposed_category != requested_category
                        )
                    ):
                        selected = fallback
                        selection_source = "semantic_user_request"
                        selection_reason = "model_selected_incompatible_user_outfit"
                    elif (
                        requested_category
                        and not proposed_category
                        and isinstance(fallback, dict)
                        and responsible_outfit_category(fallback) == requested_category
                    ):
                        selected = fallback
                        selection_source = "semantic_scene_preset"
                        selection_reason = "model_ignored_matching_outfit_reference"
                    else:
                        selected = proposed
                        selection_source = "model"
                        selection_reason = "valid_candidate_number"
                elif not model_reply:
                    selection_reason = "model_empty_response"
                elif match:
                    selection_reason = "model_candidate_out_of_range"
            except Exception as exc:
                selection_reason = f"model_error:{type(exc).__name__}"
                logger.info(
                    "参考图库模型选图失败，使用规则兜底: error=%s",
                    _single_line(exc, 120),
                )
        elif len(candidates) == 1 and not specialized_candidate:
            selection_source = "single_candidate"
            selection_reason = "only_one_candidate"
        elif not (request_text or ambient_context or schedule_history_context):
            selection_reason = "empty_selection_context"
        elif not callable(llm_call):
            selection_reason = "model_unavailable"

        score_summary = ",".join(
            f"{_single_line(item.get('id'), 40)}={score:g}"
            for item, score in scored_candidates
        )
        logger.info(
            "参考图库候选评分: fallback=%s scores=%s request=%s ambient=%s",
            fallback.get("id") if isinstance(fallback, dict) else "none",
            score_summary,
            _single_line(request_text, 180),
            _single_line(ambient_context, 120),
        )
        logger.info(
            "参考图库已选图: source=%s reason=%s id=%s kind=%s fallback=%s "
            "model_reply=%s path=%s note=%s candidates=%s",
            selection_source,
            selection_reason,
            selected.get("id") if isinstance(selected, dict) else "none",
            selected.get("kind") if isinstance(selected, dict) else "none",
            fallback.get("id") if isinstance(fallback, dict) else "none",
            model_reply or "-",
            _single_line(selected.get("path"), 260) if isinstance(selected, dict) else "-",
            _single_line(selected.get("note"), 160) if isinstance(selected, dict) else "-",
            len(candidates),
        )
        def structured_exclusions(item: dict[str, Any]) -> tuple[str, ...]:
            reasons = set(candidate_policy_exclusions.get(str(item.get("id") or ""), set()))
            if responsible_outfit_category(item) in excluded_categories:
                reasons.add("outfit")
            return tuple(sorted(reasons))

        structured_matches = tuple(
            CandidateMatch(
                candidate_id=str(item.get("id") or ""),
                score=float(score),
                rank=index,
                matched=tuple(policy_matches.get(str(item.get("id") or "")).matched) if str(item.get("id") or "") in policy_matches else tuple(),
                excluded=structured_exclusions(item),
                reason="formal_model_selection" if selection_source == "model" else selection_reason,
            )
            for index, (item, score) in enumerate(
                sorted(scored_candidates, key=lambda pair: (-pair[1], str(pair[0].get("id") or ""))),
                start=1,
            )
        )
        structured_selection = SelectionResult(
            selected=selected if isinstance(selected, dict) else None,
            candidates=structured_matches,
            selection_source=selection_source,
            selection_reason=selection_reason,
            fallback_id=str(fallback.get("id") or "") if isinstance(fallback, dict) else "",
            model_attempted=model_attempted,
            model_selected_id=model_selected_id,
        )
        await self._append_photo_generation_trace_event_async(
            trace_id,
            "reference_candidates",
            data={
                "candidates": [
                    {
                        "id": item.get("id"),
                        "kind": item.get("kind"),
                        "path": item.get("path"),
                        "roles": list(item.get("reference_roles") or ()),
                        "outfit_category": item.get("outfit_category"),
                        "outfit_lock_default": bool(item.get("outfit_lock_default")),
                        "scene_categories": list(item.get("scene_categories") or ()),
                        "time_categories": list(item.get("time_categories") or ()),
                        "excluded_scene_categories": list(item.get("excluded_scene_categories") or ()),
                        "excluded_time_categories": list(item.get("excluded_time_categories") or ()),
                        "selection_eligibility": item.get("selection_eligibility") or "matching_only",
                        "policy_exclusions": sorted(candidate_policy_exclusions.get(str(item.get("id") or ""), set())),
                        "metadata_source": item.get("metadata_source"),
                        "score": score,
                    }
                    for item, score in scored_candidates
                ],
                "rule_fallback_id": fallback.get("id") if isinstance(fallback, dict) else "",
                "selected_id": selected.get("id") if isinstance(selected, dict) else "",
                "model_reply": model_reply,
                "selection_source": selection_source,
                "selection_reason": selection_reason,
                "selection_result": structured_selection.to_dict(),
                "schedule_history_context": _single_line(schedule_history_context, 800),
                "schedule_history_used": bool(str(schedule_history_context or "").strip()),
            },
        )
        if return_selection_result:
            return structured_selection
        return self._normalize_photo_reference_candidate_metadata(selected) if isinstance(selected, dict) else {}

    @staticmethod
    def _photo_reference_intent_prompt_document(request_text: str) -> PromptDocument:
        return prompt_document(
            user_render=_PROACTIVE_DOCUMENT_RENDER,
            user=(
                _proactive_prompt_part(prompt_section(
                    key="background.photo_reference_intent",
                    title="参考图职责识别",
                    source="proactive_message",
                    template=(
                        "分析用户对显式参考图的职责要求，只输出一个 JSON 对象：\n"
                        '{{"requested_roles":[],"excluded_roles":[],"continuity_mode":"ambiguous","confidence":0.0}}\n'
                        "roles 只能是 identity、outfit、pose、scene、style、continuity、source。\n"
                        "continuity_mode 只能是 continuation、edit、new_topic、ambiguous。\n"
                        "否定表达放进 excluded_roles，不能同时作为 requested_roles。\n"
                        "无法确定时 confidence 必须低于 0.7；不要猜测服装、场景或连续性。\n\n"
                        "用户要求：{request_text}"
                    ),
                    variables={"request_text": request_text},
                ), mode=PromptRenderMode.BODY_ONLY),
            ),
            metadata={"task": "photo_reference_intent"},
        )

    async def _analyze_photo_reference_intent_async(
        self,
        request_text: str,
        *,
        workflow_kind: str,
        has_explicit_reference: bool,
    ) -> ReferenceIntent:
        rule_intent = analyze_reference_intent(
            request_text,
            has_explicit_reference=has_explicit_reference,
            workflow_kind=workflow_kind,
        )
        llm_call = getattr(self, "_llm_call", None)
        if (
            not has_explicit_reference
            or rule_intent.source != "conservative"
            or not callable(llm_call)
        ):
            return rule_intent
        compact_request = _single_line(request_text, 1200).lower().strip(" ，,。.!！?？；;")
        if re.fullmatch(
            r"(?:参考(?:一下|下)?|参考(?:这个|这张|这张图)(?:一下)?|"
            r"照着(?:这个|这张|这张图)(?:来|画)?|按(?:照)?(?:这个|这张|这张图)(?:来|画)?)",
            compact_request,
        ):
            return rule_intent

        provider_selector = getattr(self, "_task_provider", None)
        provider_id = ""
        if callable(provider_selector):
            provider_id = provider_selector(
                _persona_provider_id(
                    self, "PHOTO_PROMPT_PROVIDER_ID", "photo_prompt_provider_id", "creative"
                ),
                _persona_provider_id(
                    self, "FAST_RESPONSE_PROVIDER_ID", "fast_response_provider_id", "fast"
                ),
                _persona_provider_id(
                    self, "LLM_PROVIDER_ID", "llm_provider_id", "complex"
                ),
                _persona_provider_id(
                    self, "MAI_STYLE_PROVIDER_ID", "mai_style_provider_id", "fast"
                ),
            )

        prompt = render_prompt_document(
            self._photo_reference_intent_prompt_document(
                _single_line(request_text, 1200)
            )
        )["user"]
        try:
            raw = await llm_call(
                prompt,
                max_tokens=180,
                provider_id=provider_id or None,
                task="photo_reference_intent",
            )
            match = re.search(r"\{[\s\S]*\}", str(raw or ""))
            payload = json.loads(match.group(0)) if match else {}
            if not isinstance(payload, dict):
                return rule_intent
            requested_set = {
                str(role or "").strip().lower()
                for role in (payload.get("requested_roles") or [])
            }
            excluded_set = {
                str(role or "").strip().lower()
                for role in (payload.get("excluded_roles") or [])
            }
            requested = tuple(
                role
                for role in REFERENCE_ROLES
                if role in requested_set and role not in excluded_set
            )
            excluded = tuple(role for role in REFERENCE_ROLES if role in excluded_set)
            mode = _single_line(payload.get("continuity_mode"), 30).lower()
            if mode not in CONTINUITY_MODES:
                mode = "ambiguous"
            confidence = _safe_float(payload.get("confidence"), 0.0, 0.0, 1.0)
        except Exception as exc:
            logger.debug(
                "参考职责模型解析失败，使用保守规则: %s",
                _single_line(exc, 120),
            )
            return rule_intent
        if confidence < 0.7:
            return ReferenceIntent(("identity",), (), "ambiguous", confidence, "model_conservative")
        return ReferenceIntent(requested or ("identity",), excluded, mode, confidence, "model")

    async def _select_photo_reference_image_async(
        self,
        workflow_kind: str,
        *,
        allow_daily_outfit: bool = True,
        request_text: str = "",
        ambient_context: str = "",
        selection_context: str = "",
        suggested_scene_preset: str = "",
    ) -> str:
        """Return the selected reference path for legacy image-only callers."""
        selected = await self._select_photo_reference_candidate_async(
            workflow_kind,
            allow_daily_outfit=allow_daily_outfit,
            request_text=request_text,
            ambient_context=ambient_context,
            selection_context=selection_context,
            suggested_scene_preset=suggested_scene_preset,
        )
        return str(selected.get("path") or "") if isinstance(selected, dict) else ""

    async def _photo_reference_candidate_for_path_async(
        self,
        reference_image_path: str,
        *,
        workflow_kind: str,
        allow_daily_outfit: bool = True,
        continuity_key: str = "",
    ) -> dict[str, Any]:
        """Resolve transient reference metadata without an Image implementation mixin."""
        path = _path_text(reference_image_path, 1000)
        if not path:
            return {}
        normalized_kind = str(workflow_kind or "").strip().lower()
        if normalized_kind in {"edit", "改图", "修图", "重绘", "p图"}:
            return self._normalize_photo_reference_candidate_metadata(
                {
                    "id": "explicit_reference",
                    "path": path,
                    "source": path,
                    "kind": "source",
                    "note": "用户本轮明确提供或引用的改图原图",
                    "reference_roles": ["source"],
                    "outfit_lock_default": False,
                    "metadata_source": "runtime",
                }
            )
        candidates = await self._photo_reference_candidates_async(
            allow_daily_outfit=allow_daily_outfit,
        )
        recent = self._recent_sent_photo_continuity_candidate(continuity_key)
        if recent:
            candidates.insert(0, recent)
        for candidate in candidates:
            if self._photo_reference_paths_equal(path, candidate.get("path", "")):
                return self._normalize_photo_reference_candidate_metadata(candidate)
        return self._normalize_photo_reference_candidate_metadata(
            {
                "id": "explicit_reference",
                "path": path,
                "source": path,
                "kind": "explicit",
                "note": "用户本轮明确提供或引用的参考图",
                "reference_roles": ["identity"],
                "outfit_lock_default": False,
                "metadata_source": "runtime",
            }
        )

    async def _select_photo_reference_plan_async(
        self,
        workflow_kind: str,
        *,
        reference_intent: ReferenceIntent,
        wardrobe_intent: PhotoWardrobeIntent | None = None,
        requested_outfit_category: str | None = None,
        allow_daily_outfit: bool = True,
        requester_user_id: str = "",
        session_key: str = "",
        request_text: str = "",
        ambient_context: str = "",
        schedule_history_context: str = "",
        suggested_scene_preset: str = "",
        continuity_key: str = "",
        explicit_reference_paths: Any = (),
        require_existing_paths: bool = False,
        trace_id: str = "",
    ) -> PhotoReferencePlan:
        if reference_intent.continuity_mode == "new_topic":
            return build_photo_reference_plan(reference_intent, ())

        paths: list[str] = []
        raw_paths = explicit_reference_paths
        if isinstance(raw_paths, str):
            raw_paths = (raw_paths,)
        for raw_path in raw_paths or ():
            path = _path_text(raw_path, 1000)
            if path and path not in paths:
                paths.append(path)

        candidates: list[dict[str, Any]] = []
        indexed_roles = analyze_indexed_reference_roles(
            request_text,
            image_count=len(paths),
        )
        has_indexed_roles = any(indexed_roles)
        indexed_edit_has_source = any(
            "source" in roles for roles in indexed_roles
        )
        for index, path in enumerate(paths):
            if require_existing_paths and not os.path.isfile(path):
                continue
            candidate = await self._photo_reference_candidate_for_path_async(
                path,
                workflow_kind=workflow_kind,
                allow_daily_outfit=allow_daily_outfit,
                continuity_key=continuity_key,
            )
            if candidate:
                candidate = dict(candidate)
                candidate["available_reference_roles"] = list(
                    candidate.get("reference_roles") or ()
                )
                candidate["id"] = f"explicit_reference_{index + 1}" if len(paths) > 1 else "explicit_reference"
                if reference_intent.continuity_mode == "edit":
                    assigned_roles = list(indexed_roles[index]) if has_indexed_roles else ["source"]
                    if has_indexed_roles and index == 0 and not indexed_edit_has_source:
                        assigned_roles.insert(0, "source")
                    candidate["kind"] = "source" if "source" in assigned_roles else "explicit"
                    candidate["reference_roles"] = list(dict.fromkeys(assigned_roles))
                else:
                    candidate["kind"] = "explicit"
                    if has_indexed_roles:
                        candidate["reference_roles"] = list(indexed_roles[index])
                    else:
                        candidate["reference_roles"] = [
                            role
                            for role in reference_intent.requested_roles
                            if role not in {"continuity", "source"}
                        ]
                candidates.append(candidate)

        if (
            not candidates
            and not paths
            and "source" not in reference_intent.requested_roles
        ):
            selected = await self._select_photo_reference_candidate_async(
                workflow_kind,
                allow_daily_outfit=allow_daily_outfit,
                requester_user_id=requester_user_id,
                request_text=request_text,
                ambient_context=ambient_context,
                schedule_history_context=schedule_history_context,
                suggested_scene_preset=suggested_scene_preset,
                wardrobe_intent=wardrobe_intent,
                trace_id=trace_id,
            )
            if selected:
                selected_candidates: list[dict[str, Any]] = []
                resolved_role_candidates = self._photo_reference_role_asset_candidates(
                    request_text=request_text,
                )
                role_candidates = [
                    item
                    for item in resolved_role_candidates
                    if item.get("role_explicit_mention")
                    and item.get("group_photo_requested")
                ]
                if (
                    not role_candidates
                    and selected.get("kind") == "relation_role"
                    and selected.get("group_photo_requested")
                ):
                    role_candidates = [selected]
                if role_candidates:
                    unique_role_candidates: list[dict[str, Any]] = []
                    seen_role_owners: set[str] = set()
                    for role_candidate in sorted(
                        role_candidates,
                        key=lambda item: -_safe_int(item.get("priority"), 0, -1000),
                    ):
                        owner_id = str(role_candidate.get("owner_id") or "")
                        if not owner_id or owner_id in seen_role_owners:
                            continue
                        seen_role_owners.add(owner_id)
                        unique_role_candidates.append(role_candidate)
                    role_candidates = unique_role_candidates
                group_role_requested = bool(role_candidates)
                if group_role_requested:
                    # A named relationship-role group shot should retain both
                    # Bot's identity and the named role when the backend can
                    # accept multiple references.  The projection layer still
                    # handles one-image backends and emits a textual fallback.
                    persona_candidate = next(
                        (
                            item
                            for item in await self._photo_reference_candidates_async(
                                request_text=request_text,
                                requester_user_id=requester_user_id,
                                ambient_context=ambient_context,
                                allow_daily_outfit=allow_daily_outfit,
                            )
                            if item.get("kind") == "persona"
                        ),
                        None,
                    )
                    if persona_candidate:
                        persona_candidate = dict(persona_candidate)
                        persona_candidate["priority"] = max(
                            760,
                            _safe_int(persona_candidate.get("priority"), 0, -1000),
                        )
                        selected_candidates.append(persona_candidate)
                    elif selected.get("kind") in {
                        "persona",
                        "library",
                        "daily_outfit",
                        "recent_sent_photo",
                    }:
                        bot_candidate = dict(selected)
                        bot_candidate["priority"] = max(
                            760,
                            _safe_int(bot_candidate.get("priority"), 0, -1000),
                        )
                        selected_candidates.append(bot_candidate)
                    selected_candidates.extend(role_candidates[:4])
                if not selected_candidates:
                    selected_candidates = [selected]
                for candidate in selected_candidates:
                    if (
                        candidate.get("kind") == "knowledge_reference"
                        and reference_intent.source == "workflow_default"
                        and "identity" not in set(candidate.get("reference_roles") or ())
                    ):
                        candidate = dict(candidate)
                        candidate["reference_roles"] = [
                            "identity",
                            *(role for role in (candidate.get("reference_roles") or ()) if role != "identity"),
                        ]
                        candidate["available_reference_roles"] = list(candidate["reference_roles"])
                    if all(
                        str(candidate.get("id") or "") != str(existing.get("id") or "")
                        for existing in candidates
                    ):
                        candidates.append(candidate)
        plan_intent = reference_intent
        if not plan_intent.requested_roles and candidates:
            scoped_roles: set[str] = set()
            for candidate in candidates:
                if candidate.get("kind") in {"relation_user", "relation_role"}:
                    scoped_roles.update(candidate.get("reference_roles") or ("identity",))
                elif candidate.get("kind") == "knowledge_reference":
                    scoped_roles.update(candidate.get("reference_roles") or ("scene", "style"))
            scoped_roles.intersection_update(REFERENCE_ROLES)
            if scoped_roles:
                plan_intent = ReferenceIntent(
                    tuple(role for role in REFERENCE_ROLES if role in scoped_roles),
                    reference_intent.excluded_roles,
                    reference_intent.continuity_mode,
                    reference_intent.confidence,
                    "scoped_context",
                )
        if reference_intent.continuity_mode == "edit" and has_indexed_roles:
            requested_roles = set(reference_intent.requested_roles)
            for candidate in candidates:
                requested_roles.update(candidate.get("reference_roles") or ())
            plan_intent = ReferenceIntent(
                tuple(role for role in REFERENCE_ROLES if role in requested_roles),
                reference_intent.excluded_roles,
                reference_intent.continuity_mode,
                reference_intent.confidence,
                reference_intent.source,
            )
        if requested_outfit_category is None:
            requested_outfit_category = (
                str(getattr(wardrobe_intent, "target_category", "") or "")
                .strip()
                .lower()
            )
        else:
            requested_outfit_category = str(requested_outfit_category).strip().lower()
        reference_outfit_excluded = explicitly_excludes_reference_outfit(request_text)
        if reference_intent.source == "workflow_default" and candidates and not paths:
            requested_roles = set(reference_intent.requested_roles)
            for candidate in candidates:
                candidate_roles = {
                    str(role or "").strip().lower()
                    for role in (candidate.get("reference_roles") or ())
                }
                candidate_category = (
                    str(candidate.get("outfit_category") or "").strip().lower()
                )
                if (
                    bool(candidate.get("outfit_lock_default"))
                    and "outfit" in candidate_roles
                    and "outfit" not in reference_intent.excluded_roles
                    and not reference_outfit_excluded
                    and (
                        not requested_outfit_category
                        or (
                            requested_outfit_category != "custom_outfit"
                            and candidate_category == requested_outfit_category
                        )
                    )
                ):
                    requested_roles.add("outfit")
            if requested_roles != set(reference_intent.requested_roles):
                plan_intent = ReferenceIntent(
                    tuple(role for role in REFERENCE_ROLES if role in requested_roles),
                    reference_intent.excluded_roles,
                    reference_intent.continuity_mode,
                    reference_intent.confidence,
                    reference_intent.source,
                )
        matching_outfit_candidates: list[tuple[dict[str, Any], bool]] = []
        for candidate in candidates:
            candidate_roles = {
                str(role or "").strip().lower()
                for role in (candidate.get("reference_roles") or ())
            }
            uses_available_outfit_role = (
                "outfit" not in candidate_roles
                and candidate.get("kind") == "explicit"
                and not has_indexed_roles
                and reference_intent.continuity_mode != "edit"
                and "outfit"
                in {
                    str(role or "").strip().lower()
                    for role in (candidate.get("available_reference_roles") or ())
                }
            )
            declared_roles = candidate_roles | (
                {"outfit"} if uses_available_outfit_role else set()
            )
            if (
                "outfit" in declared_roles
                and str(candidate.get("outfit_category") or "").strip().lower()
                == requested_outfit_category
            ):
                matching_outfit_candidates.append(
                    (candidate, uses_available_outfit_role)
                )
        matching_outfit_reference = bool(matching_outfit_candidates)
        if (
            requested_outfit_category
            and requested_outfit_category != "custom_outfit"
            and matching_outfit_reference
            and not reference_outfit_excluded
        ):
            for candidate, uses_available_outfit_role in matching_outfit_candidates:
                if uses_available_outfit_role:
                    candidate_roles = {
                        str(role or "").strip().lower()
                        for role in (candidate.get("reference_roles") or ())
                    }
                    candidate_roles.add("outfit")
                    candidate["reference_roles"] = [
                        role for role in REFERENCE_ROLES if role in candidate_roles
                    ]
            requested_roles = set(plan_intent.requested_roles)
            excluded_roles = set(plan_intent.excluded_roles)
            requested_roles.add("outfit")
            excluded_roles.discard("outfit")
            plan_intent = replace(
                plan_intent,
                requested_roles=tuple(
                    role for role in REFERENCE_ROLES if role in requested_roles
                ),
                excluded_roles=tuple(
                    role for role in REFERENCE_ROLES if role in excluded_roles
                ),
            )
        plan = build_photo_reference_plan(plan_intent, candidates)
        if (
            not plan.bindings
            and self._photo_persona_fallback_allowed(
                workflow_kind,
                reference_intent,
            )
        ):
            persona_path = await self._photo_persona_reference_image_path_async()
            if persona_path:
                fallback_plan = build_photo_reference_plan(
                    reference_intent,
                    (
                        {
                            "id": "persona",
                            "kind": "persona",
                            "path": persona_path,
                            "reference_roles": ["identity"],
                        },
                    ),
                )
                if fallback_plan.bindings:
                    plan = fallback_plan
        return plan

    async def _photo_reference_candidate_from_plan_binding_async(
        self,
        binding: Any,
        *,
        workflow_kind: str,
        allow_daily_outfit: bool,
        continuity_key: str,
    ) -> dict[str, Any]:
        path = _path_text(getattr(binding, "path", ""), 1000)
        if not path:
            return {}
        snapshot = getattr(binding, "candidate", None)
        candidate = dict(snapshot) if isinstance(snapshot, dict) else {}
        if not candidate:
            candidate = await self._photo_reference_candidate_for_path_async(
                path,
                workflow_kind=workflow_kind,
                allow_daily_outfit=allow_daily_outfit,
                continuity_key=continuity_key,
            )
        if not candidate:
            return {}
        normalized = dict(candidate)
        normalized["reference_roles"] = list(getattr(binding, "roles", ()) or ())
        normalized["ignored_reference_roles"] = list(getattr(binding, "ignore", ()) or ())
        normalized["outfit_lock_default"] = bool(
            normalized.get("outfit_lock_default") and "outfit" in normalized["reference_roles"]
        )
        return self._normalize_photo_reference_candidate_metadata(normalized)

    def _extract_action_image_path(self, action_context: str) -> str:
        text = str(action_context or "")
        match = re.search(r"(?:图片路径|真实图片文件)[:：]\s*(.+)", text)
        if not match:
            return ""
        path = match.group(1).strip().splitlines()[0].strip()
        return path if path and os.path.exists(path) else ""

    def _extract_action_photo_caption(self, action_context: str) -> str:
        text = str(action_context or "")
        match = re.search(r"(?:画面|图片画面|画面草稿)[:：]\s*(.+)", text)
        if not match:
            return ""
        return _single_line(match.group(1).splitlines()[0], 220)

    def _extract_action_photo_subject_owner(self, action_context: str) -> str:
        text = str(action_context or "")
        match = re.search(r"(?:图片主体归属|画面主体归属)[:：]\s*([^\r\n]+)", text)
        if not match:
            return ""
        return _normalize_photo_subject_owner(match.group(1))

    def _build_outbound_chain(
        self,
        text: str,
        image_path: str = "",
        extra_components: list[Any] | None = None,
    ) -> list[Any]:
        chain: list[Any] = []
        if text:
            chain.append(Plain(text))
        for component in extra_components or []:
            if component is not None:
                chain.append(component)
        if image_path and os.path.exists(image_path):
            try:
                chain.append(Image.fromFileSystem(image_path))
            except AttributeError:
                chain.append(Image.from_file_system(image_path))
        if not chain:
            chain.append(Plain(""))
        return chain

    def _parse_message_session(self, umo: str) -> MessageSession | None:
        try:
            return MessageSession.from_str(str(umo or ""))
        except Exception:
            return None

    def _platform_instance_id(self, platform: Any | None) -> str:
        if platform is None:
            return ""
        try:
            meta = platform.meta()
        except Exception:
            return ""
        return str(getattr(meta, "id", "") or getattr(meta, "name", "") or "").strip()

    def _session_for_platform(self, session: MessageSession, platform: Any | None = None) -> MessageSession:
        platform_id = self._platform_instance_id(platform) or str(getattr(session, "platform_id", "") or "")
        return MessageSession(
            platform_name=platform_id,
            message_type=self._message_type_for_session(session),
            session_id=str(getattr(session, "session_id", "") or ""),
        )

    def _get_platform_for_session(self, session: MessageSession) -> Any | None:
        platform_id = str(getattr(session, "platform_id", "") or "")
        manager = getattr(self.context, "platform_manager", None)
        if not platform_id or not manager:
            return None
        platforms = []
        try:
            platforms = list(manager.get_insts())
        except Exception:
            platforms = list(getattr(manager, "platform_insts", []) or [])
        for platform in platforms:
            try:
                meta = platform.meta()
            except Exception:
                continue
            if getattr(meta, "id", "") == platform_id or getattr(meta, "name", "") == platform_id:
                return platform
        return None

    def _message_type_for_session(self, session: MessageSession) -> MessageType:
        msg_type = getattr(session, "message_type", MessageType.FRIEND_MESSAGE)
        if isinstance(msg_type, MessageType):
            return msg_type
        msg_type_text = str(msg_type or "")
        if "Group" in msg_type_text or "GROUP" in msg_type_text:
            return MessageType.GROUP_MESSAGE
        return MessageType.FRIEND_MESSAGE

    def _format_send_exception(self, exc: Exception | BaseException | None) -> str:
        if exc is None:
            return ""
        text = _single_line(str(exc), 180)
        if text:
            return f"{exc.__class__.__name__}: {text}"
        return repr(exc)

    @staticmethod
    def _is_onebot_event_checker_send_rejection(error: Any) -> bool:
        """Identify the NTQQ sendMsg rejection shared by every aiocqhttp send route."""
        text = str(error or "").strip().lower()
        compact = re.sub(r"\s+", "", text)
        has_retcode = any(
            token in compact
            for token in ("retcode=1200", "retcode:1200", "'retcode':1200", '\"retcode\":1200')
        )
        return bool(
            has_retcode
            and "eventcheckerfailed" in compact
            and ("sendmsg" in compact or "nodeikernelmsgservice" in compact)
        )

    @staticmethod
    def _onebot_event_checker_rejection_summary() -> str:
        return "QQ/NTQQ 拒绝发送（retcode=1200，EventChecker sendMsg）；目标可能暂时不可私聊、好友状态已变化，或 QQ 客户端正处于异常状态"

    def _describe_send_target(self, umo: str, session: MessageSession | None, platform: Any | None) -> str:
        if session is None:
            return f"umo={_single_line(umo, 140) or '-'} session=unparsed platform=-"
        platform_id = _single_line(getattr(session, "platform_id", ""), 60)
        session_id = _single_line(getattr(session, "session_id", ""), 80)
        message_type = _single_line(getattr(session, "message_type", ""), 60)
        platform_desc = "found" if platform else "missing"
        if platform:
            platform_desc = _single_line(self._platform_instance_id(platform), 80) or platform.__class__.__name__
        return (
            f"umo={_single_line(umo, 140) or '-'} "
            f"platform_id={platform_id or '-'} type={message_type or '-'} session_id={session_id or '-'} platform={platform_desc}"
        )

    def _apply_proactive_tts_message_scope(self, event: Any, chain: list[Any]) -> bool:
        feature_enabled = getattr(self, "_feature_enabled_or_temp_unlocked", None)
        tts_enabled = (
            feature_enabled("enable_tts_enhancement")
            if callable(feature_enabled)
            else bool(runtime_persona_setting(self, "enable_tts_enhancement", False))
        )
        if (
            not tts_enabled
            or str(
                runtime_persona_setting(self, "tts_message_scope", "replies_only")
                or "replies_only"
            ).lower()
            != "replies_and_proactive"
        ):
            return False
        if any(isinstance(component, Record) for component in chain) or any(
            bool(getattr(component, "_private_companion_skip_tts_enhancement", False))
            for component in chain
        ):
            return False
        try:
            setattr(event, "_private_companion_tts_request_applied", True)
            setattr(event, "_private_companion_tts_forced_by_message_scope", True)
        except Exception:
            return False
        return True

    async def _trigger_proactive_decorating_hooks(self, umo: str, chain: list[Any]) -> list[Any]:
        if not runtime_persona_setting(self, "enable_proactive_decorating_hooks", True) or not chain:
            return chain
        session = self._parse_message_session(umo)
        if not session:
            return chain
        platform = self._get_platform_for_session(session)
        if not platform:
            return chain
        try:
            message_obj = AstrBotMessage()
            message_obj.type = self._message_type_for_session(session)
            message_obj.self_id = str(getattr(session, "session_id", "") or "")
            message_obj.session_id = str(getattr(session, "session_id", "") or "")
            message_obj.message_id = f"private_companion_proactive_{uuid.uuid4().hex}"
            message_obj.sender = MessageMember(user_id=message_obj.session_id)
            message_obj.message = chain
            message_obj.message_str = ""
            message_obj.raw_message = None
            message_obj.timestamp = int(time.time())
            event = AstrMessageEvent("", message_obj, platform.meta(), message_obj.session_id)
            event.set_result(self._build_result_from_chain(chain))
            setattr(event, "_private_companion_proactive_delivery_umo", umo)
            if self._apply_proactive_tts_message_scope(event, chain):
                logger.info(
                    "主动消息按 TTS 生效范围进入强化链: session=%s",
                    _single_line(umo, 120) or "unknown",
                )
            for component in chain:
                raw_full_text = getattr(component, "_private_companion_proactive_full_text", "")
                if not raw_full_text:
                    continue
                setattr(event, "_private_companion_proactive_full_text", raw_full_text)
                setattr(
                    event,
                    "_private_companion_proactive_segment_index",
                    max(0, int(getattr(component, "_private_companion_proactive_segment_index", 0) or 0)),
                )
                setattr(
                    event,
                    "_private_companion_proactive_segment_count",
                    max(1, int(getattr(component, "_private_companion_proactive_segment_count", 1) or 1)),
                )
                break
            if any(
                bool(getattr(component, "_private_companion_skip_tts_enhancement", False))
                for component in chain
            ):
                setattr(event, "_private_companion_skip_tts_enhancement", "proactive_prebuilt_voice")
        except Exception as e:
            logger.debug("构造主动消息装饰事件失败,跳过 hooks: %s", e)
            return chain
        try:
            handlers = star_handlers_registry.get_handlers_by_event_type(
                EventType.OnDecoratingResultEvent
            )
        except Exception as e:
            logger.debug("获取装饰 hooks 失败: %s", e)
            return chain
        for handler in handlers:
            try:
                await handler.handler(event)
            except Exception as e:
                logger.warning(
                    "主动消息装饰 hook 失败: %s: %s",
                    getattr(handler, "handler_full_name", "unknown"),
                    e,
                )
        is_stopped = getattr(event, "is_stopped", None)
        if callable(is_stopped):
            try:
                if is_stopped():
                    return []
            except Exception:
                pass
        result = event.get_result()
        processed = getattr(result, "chain", None) if result is not None else None
        if processed is None:
            return []
        processed_chain = list(processed or [])
        return self._filter_decorated_proactive_chain(chain, processed_chain)

    def _proactive_plain_segment_component(
        self,
        text: str,
        *,
        full_text: str = "",
        index: int = 0,
        count: int = 1,
        suppress_tts: bool = False,
    ) -> Plain:
        comp = Plain(text)
        full_source = str(full_text or "")
        marker_cleaner = getattr(self, "_strip_llm_segment_marker_lines", None)
        if callable(marker_cleaner):
            full_source = marker_cleaner(full_source)
        clean_full = _single_line(full_source, max(1200, len(full_source) + 32))
        if clean_full:
            try:
                object.__setattr__(comp, "_private_companion_proactive_full_text", clean_full)
                object.__setattr__(comp, "_private_companion_proactive_segment_index", max(0, int(index)))
                object.__setattr__(comp, "_private_companion_proactive_segment_count", max(1, int(count)))
            except Exception:
                pass
        if suppress_tts:
            try:
                object.__setattr__(comp, "_private_companion_skip_tts_enhancement", True)
            except Exception:
                pass
        return comp

    def _filter_decorated_proactive_chain(self, original_chain: list[Any], processed_chain: list[Any]) -> list[Any]:
        if not processed_chain:
            return []

        filtered: list[Any] = []
        removed_any = False
        for component in processed_chain:
            if isinstance(component, Plain):
                text = self._plain_component_text(component)
                if self._is_proactive_delivery_receipt_text(text):
                    removed_any = True
                    continue
                cleaned = self._strip_proactive_delivery_receipt_lines(text)
                if not cleaned:
                    removed_any = True
                    continue
                if cleaned != text:
                    removed_any = True
                    filtered.append(Plain(cleaned))
                else:
                    filtered.append(component)
                continue
            filtered.append(component)

        if filtered:
            return filtered
        return [] if removed_any else processed_chain

    @staticmethod
    def _plain_component_text(component: Any) -> str:
        for attr in ("text", "content", "message"):
            value = getattr(component, attr, None)
            if isinstance(value, str):
                return value
        return str(component or "")

    @staticmethod
    def _contains_inline_image_tag(text: str) -> bool:
        return bool(re.search(r"<img\b[^>]*\bsrc\s*=", str(text or ""), flags=re.IGNORECASE))

    @staticmethod
    def _is_proactive_delivery_receipt_text(text: str) -> bool:
        raw = _single_line(text, 240)
        if not raw:
            return False
        compact = re.sub(r"[\s。.!！?？,，；;:：、~～\"'“”‘’（）()【】\[\]]+", "", raw).lower()
        if not compact:
            return False
        if compact in {
            "已发送",
            "发送成功",
            "发送完成",
            "发送完毕",
            "已成功发送",
            "消息已发送",
            "消息发送成功",
            "messagesent",
            "sent",
            "我主动开口了",
            "我主动发了一段语音",
            "我主动分享了一点东西",
            "我主动做了一次小互动",
        }:
            return True
        if re.fullmatch(r"(?:图|图片|照片)(?:好|好了|生成好了|出来了|完成了)[啦了]*", compact):
            return True
        if re.fullmatch(r"(?:生图|出图|图片生成)(?:完成|好了|成功)[啦了]*", compact):
            return True
        if re.search(r"(?:还在|正在|继续)?(?:排队|队列|等待生成|等图|等图片|等它出图)", compact):
            return True
        if re.match(r"^(?:已经|已)(?:发|发送)过去[啦了]?(?:等(?:着|他|你|对方)|等回复|等回我)?$", compact):
            return True
        if re.match(r"^等(?:着)?(?:他|你|对方)?回(?:我|复)?[啦了]*$", compact):
            return True
        if compact.startswith("消息已送达"):
            return True
        if re.match(r"^这是.{0,80}(?:发的|发送的|收到的).{0,80}(?:消息|打招呼|问候|回复)", compact):
            return True
        if re.match(r"^这(?:条|是).{0,80}(?:语气|内容|消息).{0,80}$", compact):
            return True
        receipt_prefixes = (
            "消息已发送给",
            "消息发送给",
            "已发送给",
            "已经发送给",
            "已向",
            "已经向",
        )
        receipt_descriptors = (
            "讲的是",
            "说的是",
            "内容是",
            "内容就是",
            "发的是",
            "转述的是",
            "分享的是",
            "告诉的是",
        )
        if compact.startswith(receipt_prefixes) and any(token in compact for token in receipt_descriptors):
            return True
        long_receipt_markers = (
            ("已经把", "转给"),
            ("已把", "转给"),
            ("已经将", "转给"),
            ("已将", "转给"),
            ("已经发给", "就假装"),
            ("已经发送给", "就假装"),
            ("就假装", "语气很自然"),
            ("随手分享", "语气很自然"),
        )
        if any(all(token in raw for token in pair) for pair in long_receipt_markers):
            return True
        if (
            any(token in compact for token in ("视频链接转给", "链接转给", "消息转给", "内容转给"))
            and any(token in compact for token in ("已经", "已", "完成", "成功"))
        ):
            return True
        return (
            len(compact) <= 32
            and any(token in compact for token in ("发送给用户", "发给用户", "发送给对方", "发给对方", "发出去了"))
            and any(token in compact for token in ("已", "已经", "完成", "成功"))
        )

    @staticmethod
    def _is_proactive_instruction_leak_text(text: str) -> bool:
        raw = _single_line(text, 360)
        if not raw:
            return False
        compact = re.sub(r"[\s。.!！?？,，；;:：、~～\"'“”‘’（）()【】\[\]<>《》]+", "", raw).lower()
        if not compact:
            return False
        exact_leaks = {
            "直接在当前对话中输出这条主动消息",
            "请直接在当前对话中输出这条主动消息",
            "在当前对话中输出这条主动消息",
            "直接输出这条主动消息",
            "输出这条主动消息",
            "发送这条主动消息",
            "sendthisproactivemessage",
            "outputthisproactivemessage",
        }
        if compact in exact_leaks:
            return True
        has_proactive_target = "主动消息" in raw or "proactive message" in raw.lower()
        has_delivery_command = any(
            token in compact
            for token in (
                "直接输出",
                "请输出",
                "输出这条",
                "输出本条",
                "直接发送",
                "请发送",
                "发送这条",
                "发出这条",
                "sendthis",
                "outputthis",
            )
        )
        has_instruction_context = any(
            token in compact
            for token in (
                "当前对话",
                "当前聊天",
                "本轮对话",
                "用户对话",
                "聊天窗口",
                "给用户",
                "touser",
                "currentchat",
                "currentconversation",
            )
        )
        if has_proactive_target and has_delivery_command and (has_instruction_context or len(compact) <= 36):
            return True
        if len(compact) <= 44 and has_delivery_command and has_instruction_context and any(
            token in compact for token in ("消息", "正文", "文本", "content", "message")
        ):
            return True
        return False

    def _strip_proactive_delivery_receipt_lines(self, text: str) -> str:
        kept: list[str] = []
        for raw_line in str(text or "").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if self._is_proactive_delivery_receipt_text(line):
                continue
            kept.append(line)
        return "\n".join(kept).strip()

    def _validate_proactive_outbound_candidate(
        self,
        text: str,
        *,
        umo: str = "",
        image_path: str = "",
        extra_components: list[Any] | None = None,
        reason: str = "",
        action: str = "",
        source: str = "send",
    ) -> dict[str, Any]:
        raw = str(text or "").strip()
        has_media = bool(_path_text(image_path, 1000)) or bool(extra_components)
        if not raw:
            sticker_pending_getter = getattr(self, "_proactive_sticker_only_pending", None)
            try:
                sticker_only_pending = bool(sticker_pending_getter(umo)) if callable(sticker_pending_getter) else False
            except Exception:
                sticker_only_pending = False
            if has_media or sticker_only_pending:
                return {"decision": "send", "text": "", "reason": ""}
            return {"decision": "drop", "text": "", "reason": "主动行为没有产出可发送内容", "hard": True}
        if self._looks_like_internal_provider_error_text(raw):
            return {"decision": "drop", "text": "", "reason": "主动正文是模型/工具调用失败信息", "hard": True}

        kept_lines: list[str] = []
        removed_leak = False
        for raw_line in raw.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if (
                self._is_proactive_delivery_receipt_text(line)
                or self._is_proactive_instruction_leak_text(line)
                or self._framework_agent_meta_summary_leak(line)
            ):
                removed_leak = True
                continue
            kept_lines.append(line)
        if removed_leak:
            cleaned = "\n".join(kept_lines).strip()
            if cleaned:
                return {"decision": "rewrite", "text": cleaned, "reason": "已清理主动正文中的内部提示词/执行回执残留"}
            if has_media:
                return {"decision": "rewrite", "text": "", "reason": "已清理主动正文中的内部提示词/执行回执残留"}
            return {"decision": "drop", "text": "", "reason": "主动正文只剩内部提示词/执行回执残留", "hard": True}

        if self._is_proactive_delivery_receipt_text(raw):
            return {"decision": "drop", "text": "", "reason": "主动正文是工具/执行状态回执", "hard": True}
        if self._is_proactive_instruction_leak_text(raw):
            return {"decision": "drop", "text": "", "reason": "主动正文疑似内部提示词/发送指令泄漏", "hard": True}
        if self._framework_agent_meta_summary_leak(raw):
            return {"decision": "drop", "text": "", "reason": "主动正文疑似工具循环/内部发送摘要泄漏", "hard": True}

        return {"decision": "send", "text": raw, "reason": ""}

    def _proactive_archive_context_text(self, text: str) -> bool:
        cleaned = _single_line(text, 500)
        if not cleaned:
            return False
        if "【主动承接占位】" in cleaned or "下一条是 Bot 主动发出的内容" in cleaned:
            return True
        if self._is_proactive_delivery_receipt_text(cleaned):
            return True
        return False

    @staticmethod
    def _strip_leading_sentence_boundary_artifacts(text: str) -> str:
        cleaned = str(text or "").strip()
        cleaned = re.sub(r"^(?:[。！？!?；;，,、：:]+[\s\u3000]*)+", "", cleaned).strip()
        return cleaned

    def _forward_sender_id_for_segments(self, event: Any | None = None) -> str:
        if event is not None:
            try:
                sender_id = _single_line(self._event_self_id(event), 40)
                if sender_id:
                    return sender_id
            except Exception:
                pass
        for sender_id in self._known_bot_self_ids():
            if sender_id:
                return sender_id
        return "0"

    def _forward_nodes_for_segments(self, segments: list[str], *, event: Any | None = None) -> list[dict[str, Any]]:
        sender_name = _single_line(
            runtime_persona_setting(self, "bot_name", ""), 40
        ) or "PrivateCompanion"
        sender_id = self._forward_sender_id_for_segments(event)
        nodes: list[dict[str, Any]] = []
        for segment in segments:
            text = str(segment or "").strip()
            if not text:
                continue
            nodes.append(
                {
                    "type": "node",
                    "data": {
                        "name": sender_name,
                        "uin": sender_id,
                        "content": [{"type": "text", "data": {"text": text}}],
                    },
                }
            )
        return nodes

    def _clean_forward_segment_texts(self, segments: list[str]) -> list[str]:
        cleaned: list[str] = []
        for segment in segments:
            text = re.sub(r"</?t{2,}s\b[^>]*>", "", str(segment or ""), flags=re.IGNORECASE).strip()
            text = self._strip_leading_sentence_boundary_artifacts(text)
            if text:
                cleaned.append(text)
        return cleaned

    def _onebot_forward_action_result_ok(self, result: Any) -> bool:
        if result is None:
            return True
        if isinstance(result, dict):
            status = str(result.get("status") or result.get("result") or "").strip().lower()
            if status in {"failed", "fail", "error", "nok"}:
                return False
            retcode = result.get("retcode", result.get("code", None))
            if retcode is not None:
                try:
                    return int(retcode) == 0
                except Exception:
                    return False
            data = result.get("data")
            if isinstance(data, dict) and any(data.get(key) for key in ("message_id", "forward_id", "res_id", "resid")):
                return True
            return any(result.get(key) for key in ("message_id", "forward_id", "res_id", "resid"))
        return bool(result)

    async def _call_onebot_forward_action(self, client: Any, action: str, **params: Any) -> bool:
        for attr in ("call_action", "call_api", "api"):
            func = getattr(client, attr, None)
            if not callable(func):
                continue
            try:
                result = func(action, **params)
            except TypeError:
                try:
                    result = func(action, params)
                except Exception as exc:
                    if self._delivery_outcome_is_uncertain(exc):
                        self._log_uncertain_onebot_submission(action, exc)
                        return True
                    continue
            except Exception as exc:
                if self._delivery_outcome_is_uncertain(exc):
                    self._log_uncertain_onebot_submission(action, exc)
                    return True
                continue
            try:
                if hasattr(result, "__await__"):
                    result = await result
            except Exception as exc:
                if self._delivery_outcome_is_uncertain(exc):
                    self._log_uncertain_onebot_submission(action, exc)
                    return True
                continue
            if self._onebot_forward_action_result_ok(result):
                return True
        func = getattr(client, action, None)
        if callable(func):
            try:
                result = func(**params)
            except Exception as exc:
                if self._delivery_outcome_is_uncertain(exc):
                    self._log_uncertain_onebot_submission(action, exc)
                    return True
                return False
            try:
                if hasattr(result, "__await__"):
                    result = await result
            except Exception as exc:
                if self._delivery_outcome_is_uncertain(exc):
                    self._log_uncertain_onebot_submission(action, exc)
                    return True
                return False
            return self._onebot_forward_action_result_ok(result)
        return False

    async def _send_segmented_forward_message(
        self,
        *,
        target_type: str,
        target_id: str,
        segments: list[str],
        event: Any | None = None,
        source: str = "",
    ) -> bool:
        send_as_forward = self._segmented_setting(
            "send_as_forward",
            chat_type=target_type,
            default=False,
        )
        if not bool(send_as_forward):
            return False
        target_type = str(target_type or "").strip().lower()
        target_id = _single_line(target_id, 80)
        if target_type not in {"private", "group"} or not target_id:
            return False
        raw_segments = [_redact_outbound_secrets(item, self).strip() for item in segments if str(item or "").strip()]
        if len(raw_segments) <= 1:
            return False
        if runtime_persona_setting(self, "enable_tts_enhancement", False) and any(
            re.search(r"</?t{2,}s\b", item, flags=re.IGNORECASE) for item in raw_segments
        ):
            logger.info("分段合并消息跳过 TTS 内容: source=%s target=%s:%s", source or "unknown", target_type, target_id)
            return False
        cleaned_segments = self._clean_forward_segment_texts(raw_segments)
        if len(cleaned_segments) <= 1:
            return False
        hit = self._forbidden_recall_hit("\n".join(cleaned_segments))
        if hit:
            logger.warning(
                "分段合并消息命中违禁词，已拦截发送: source=%s target=%s:%s word=%s",
                source or "unknown",
                target_type,
                target_id,
                _single_line(hit, 40),
            )
            return False
        client = self._resolve_aiocqhttp_client()
        if client is None:
            return False
        nodes = self._forward_nodes_for_segments(cleaned_segments, event=event)
        if len(nodes) <= 1:
            return False
        target_value: Any = target_id
        try:
            target_value = int(target_id)
        except Exception:
            pass
        if target_type == "group":
            attempts = [
                ("send_group_forward_msg", {"group_id": target_value, "messages": nodes}),
                ("send_group_forward_msg", {"group_id": target_value, "nodes": nodes}),
                ("send_forward_msg", {"group_id": target_value, "messages": nodes}),
                ("send_forward_msg", {"group_id": target_value, "nodes": nodes}),
            ]
        else:
            attempts = [
                ("send_private_forward_msg", {"user_id": target_value, "messages": nodes}),
                ("send_private_forward_msg", {"user_id": target_value, "nodes": nodes}),
                ("send_forward_msg", {"user_id": target_value, "messages": nodes}),
                ("send_forward_msg", {"user_id": target_value, "nodes": nodes}),
            ]
        for action, params in attempts:
            if await self._call_onebot_forward_action(client, action, **params):
                self._confirm_outbound_delivery(
                    "",
                    [Plain(segment) for segment in cleaned_segments],
                )
                logger.info(
                    "分段消息已合并转发发送: source=%s target=%s:%s segments=%s",
                    source or "unknown",
                    target_type,
                    target_id,
                    len(cleaned_segments),
                )
                return True
        logger.info(
            "分段合并转发发送不可用，回退普通分段: source=%s target=%s:%s segments=%s",
            source or "unknown",
            target_type,
            target_id,
            len(cleaned_segments),
        )
        return False

    async def _send_segmented_proactive_forward_message(self, umo: str, segments: list[str], *, source: str = "proactive") -> bool:
        platform_supports = getattr(self, "_platform_supports", None)
        if callable(platform_supports) and not platform_supports("merged_forward", umo=umo):
            return False
        session = self._parse_message_session(umo)
        if not session:
            return False
        target_id = _single_line(getattr(session, "session_id", ""), 80)
        if not target_id:
            return False
        target_type = "group" if self._message_type_for_session(session) == MessageType.GROUP_MESSAGE else "private"
        return await self._send_segmented_forward_message(
            target_type=target_type,
            target_id=target_id,
            segments=segments,
            source=source,
        )

    async def _send_segmented_event_forward_message(self, event: AstrMessageEvent, segments: list[str], *, source: str = "decorating_result") -> bool:
        platform_supports = getattr(self, "_platform_supports", None)
        if callable(platform_supports) and not platform_supports("merged_forward", event=event):
            return False
        try:
            if bool(getattr(event, "is_private_chat", lambda: False)()):
                user_id = _single_line(event.get_sender_id(), 80)
                if user_id:
                    return await self._send_segmented_forward_message(
                        target_type="private",
                        target_id=user_id,
                        segments=segments,
                        event=event,
                        source=source,
                    )
        except Exception:
            pass
        group_id = self._extract_group_id_from_event(event)
        if group_id:
            return await self._send_segmented_forward_message(
                target_type="group",
                target_id=group_id,
                segments=segments,
                event=event,
                source=source,
            )
        return False

    def _segmented_chat_scope_allows(self, chat_type: str) -> bool:
        chat_type = str(chat_type or "").strip().lower()
        if chat_type not in {"private", "group"}:
            chat_type = "private"
        if bool(
            runtime_persona_setting(self, "enable_segmented_proactive_chat_profiles", False)
        ):
            return bool(
                runtime_persona_setting(
                    self,
                    f"segmented_proactive_{chat_type}_enabled",
                    True,
                )
            )
        scope = str(
            runtime_persona_setting(self, "segmented_proactive_chat_scope", "all") or "all"
        ).strip().lower()
        if scope not in {"all", "private", "group"}:
            scope = "all"
        return scope == "all" or scope == chat_type

    def _segmented_chat_type_for_umo(self, umo: str) -> str:
        session = self._parse_message_session(umo)
        if session and self._message_type_for_session(session) == MessageType.GROUP_MESSAGE:
            return "group"
        return "private"

    def _segmented_chat_type_for_event(self, event: AstrMessageEvent) -> str:
        try:
            if bool(getattr(event, "is_private_chat", lambda: False)()):
                return "private"
        except Exception:
            pass
        return "group" if self._extract_group_id_from_event(event) else "private"

    def _segmented_setting(
        self,
        name: str,
        *,
        event: AstrMessageEvent | None = None,
        umo: str = "",
        chat_type: str = "",
        default: Any = None,
    ) -> Any:
        normalized_name = str(name or "").strip()
        fallback = runtime_persona_setting(
            self,
            f"segmented_proactive_{normalized_name}",
            default,
        )
        if not bool(
            runtime_persona_setting(self, "enable_segmented_proactive_chat_profiles", False)
        ):
            return fallback
        resolved_chat_type = str(chat_type or "").strip().lower()
        if resolved_chat_type not in {"private", "group"}:
            resolved_chat_type = (
                self._segmented_chat_type_for_event(event)
                if event is not None
                else self._segmented_chat_type_for_umo(umo)
            )
        return runtime_persona_setting(
            self,
            f"segmented_proactive_{resolved_chat_type}_{normalized_name}",
            fallback,
        )

    def _segmented_scope_allows_umo(self, umo: str) -> bool:
        return self._segmented_chat_scope_allows(self._segmented_chat_type_for_umo(umo))

    def _segmented_scope_allows_event(self, event: AstrMessageEvent) -> bool:
        return self._segmented_chat_scope_allows(self._segmented_chat_type_for_event(event))

    def _segmented_platform_allows(
        self,
        *,
        event: AstrMessageEvent | None = None,
        umo: str = "",
    ) -> bool:
        platform_supports = getattr(self, "_platform_supports", None)
        return not callable(platform_supports) or bool(
            platform_supports("segmented_reply", event=event, umo=umo)
        )

    async def _onebot_messages_from_chain(self, chain: list[Any]) -> tuple[list[dict[str, Any]], str]:
        try:
            from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent

            messages = await AiocqhttpMessageEvent._parse_onebot_json(MessageChain(chain))
            return list(messages or []), ""
        except Exception as exc:
            return [], self._format_send_exception(exc)

    async def _send_chain_components_via_onebot_direct(
        self,
        umo: str,
        session: MessageSession | None,
        chain: list[Any],
    ) -> tuple[bool, str]:
        if session is None:
            return False, "UMO 无法解析，不能使用 OneBot 原生兜底"
        target_id = _single_line(getattr(session, "session_id", ""), 80)
        if not target_id or not target_id.isdigit():
            return False, f"session_id 不是纯数字，不能使用 OneBot 原生兜底: {target_id or '-'}"
        client = self._resolve_aiocqhttp_client()
        if client is None:
            return False, "没有找到可用的 aiocqhttp/OneBot 客户端"
        messages, parse_error = await self._onebot_messages_from_chain(chain)
        if not messages:
            return False, parse_error or "消息链无法转换为 OneBot 消息段"
        target_value: Any = target_id
        try:
            target_value = int(target_id)
        except Exception:
            pass
        is_group = self._message_type_for_session(session) == MessageType.GROUP_MESSAGE
        action = "send_group_msg" if is_group else "send_private_msg"
        params = {"group_id": target_value, "message": messages} if is_group else {"user_id": target_value, "message": messages}
        ok, error = await self._call_onebot_action_with_error(
            client,
            action,
            at_most_once=True,
            **params,
        )
        if ok:
            logger.info(
                "主动消息已通过 OneBot 原生兜底发送: action=%s target=%s segments=%s umo=%s",
                action,
                target_id,
                len(messages),
                _single_line(umo, 140),
            )
            return True, ""
        return False, error or f"OneBot 原生动作 {action} 返回失败"

    async def _send_chain_components(
        self,
        umo: str,
        chain: list[Any],
        *,
        apply_decorating_hooks: bool = True,
    ) -> bool:
        bot_scope_checker = getattr(self, "_bot_scope_allows_umo", None)
        if callable(bot_scope_checker) and not bot_scope_checker(umo):
            logger.info(
                "Bot 作用域已跳过后台投递: umo=%s",
                _single_line(umo, 140),
            )
            return False
        marker_cleaner = getattr(self, "_strip_llm_segment_marker_lines", None)
        if callable(marker_cleaner):
            cleaned_chain: list[Any] = []
            for component in chain or []:
                if not isinstance(component, Plain):
                    cleaned_chain.append(component)
                    continue
                original = str(getattr(component, "text", "") or "")
                cleaned = marker_cleaner(original)
                if cleaned:
                    cleaned_chain.append(Plain(cleaned) if cleaned != original else component)
            chain = cleaned_chain
        chain_redactor = getattr(self, "_redact_outbound_chain_secrets", None)
        if callable(chain_redactor):
            chain, redacted = chain_redactor(chain)
            if redacted:
                logger.error("主动发送前检测到敏感凭据并已脱敏: umo=%s stage=before_hooks", _single_line(umo, 120))
        hit = self._forbidden_recall_hit(self._chain_text_for_forbidden_recall(chain))
        if hit:
            logger.warning(
                "主动待发送消息命中违禁词，已拦截发送: umo=%s word=%s",
                umo,
                _single_line(hit, 40),
            )
            notifier = getattr(self, "_schedule_reply_interception_forward", None)
            if callable(notifier):
                notifier(
                    "proactive_block",
                    source="主动发送组件校验",
                    reason=f"命中违禁词：{_single_line(hit, 40)}",
                    source_session=umo,
                    before=self._chain_text_for_forbidden_recall(chain),
                )
            return False
        processed_chain = (
            await self._trigger_proactive_decorating_hooks(umo, chain)
            if apply_decorating_hooks
            else list(chain)
        )
        if not processed_chain:
            notifier = getattr(self, "_schedule_reply_interception_forward", None)
            if callable(notifier):
                notifier(
                    "proactive_block",
                    source="主动发送装饰钩子",
                    reason="装饰钩子清空了待发送消息",
                    source_session=umo,
                    before=self._chain_text_for_forbidden_recall(chain),
                )
            return False
        if callable(chain_redactor):
            processed_chain, redacted = chain_redactor(processed_chain)
            if redacted:
                logger.error("主动装饰后检测到敏感凭据并已脱敏: umo=%s stage=after_hooks", _single_line(umo, 120))
        tts_chain_guard = getattr(self, "_sanitize_outbound_tts_chain_without_event", None)
        if callable(tts_chain_guard):
            processed_chain = await tts_chain_guard(processed_chain, umo=umo)
            if not processed_chain:
                notifier = getattr(self, "_schedule_reply_interception_forward", None)
                if callable(notifier):
                    notifier("proactive_block", source="主动发送 TTS 校验", reason="TTS 校验清空了待发送消息", source_session=umo)
                return False
        hit = self._forbidden_recall_hit(self._chain_text_for_forbidden_recall(processed_chain))
        if hit:
            logger.warning(
                "主动装饰后消息命中违禁词，已拦截发送: umo=%s word=%s",
                umo,
                _single_line(hit, 40),
            )
            notifier = getattr(self, "_schedule_reply_interception_forward", None)
            if callable(notifier):
                notifier(
                    "proactive_block",
                    source="主动装饰后校验",
                    reason=f"装饰后命中违禁词：{_single_line(hit, 40)}",
                    source_session=umo,
                    before=self._chain_text_for_forbidden_recall(processed_chain),
                )
            return False
        session = self._parse_message_session(umo)
        platform = self._get_platform_for_session(session) if session else None
        precise_error: Exception | None = None
        if runtime_persona_setting(self, "enable_precise_platform_send", True) and session and platform:
            status = getattr(platform, "status", None)
            if status is not None and status != PlatformStatus.RUNNING:
                logger.warning("目标平台未运行,跳过主动发送: %s", umo)
                notifier = getattr(self, "_schedule_reply_interception_forward", None)
                if callable(notifier):
                    notifier(
                        "proactive_block",
                        source="主动发送平台校验",
                        reason="目标平台未运行",
                        source_session=umo,
                        before=self._chain_text_for_forbidden_recall(processed_chain),
                    )
                raise RuntimeError(f"目标平台未运行，无法发送主动消息: {_single_line(umo, 140)}")
            try:
                session_obj = self._session_for_platform(session, platform)
                precise_result = await platform.send_by_session(session_obj, MessageChain(processed_chain))
                if precise_result is not False:
                    self._confirm_outbound_delivery(umo, processed_chain)
                    return True
                precise_error = RuntimeError("精确平台发送返回 False（平台未接受消息）")
                logger.warning(
                    "精确平台发送未被目标平台接受,回退核心发送: target=%s",
                    self._describe_send_target(umo, session, platform),
                )
            except Exception as e:
                precise_error = e
                if self._is_onebot_event_checker_send_rejection(e):
                    summary = self._onebot_event_checker_rejection_summary()
                    logger.info(
                        "主动发送被 QQ/NTQQ 底层拒绝，停止对同一 sendMsg 链路的立即重复尝试: target=%s",
                        self._describe_send_target(umo, session, platform),
                    )
                    raise RuntimeError(summary) from e
                if self._delivery_outcome_is_uncertain(e):
                    logger.warning(
                        "精确平台发送回执不确定，为避免同一主动消息立即重复发送，本次按已提交处理: target=%s error=%s",
                        self._describe_send_target(umo, session, platform),
                        self._format_send_exception(e),
                    )
                    self._confirm_outbound_delivery(umo, processed_chain)
                    return True
                logger.warning(
                    "精确平台发送失败,回退核心发送: target=%s error=%s",
                    self._describe_send_target(umo, session, platform),
                    self._format_send_exception(e),
                )
        core_error: Exception | None = None
        core_result: Any = None
        core_session: str | MessageSession = umo
        if session and platform:
            core_session = self._session_for_platform(session, platform)
        try:
            core_result = await self.context.send_message(core_session, self._build_result_from_chain(processed_chain))
            if core_result is not False:
                self._confirm_outbound_delivery(umo, processed_chain)
                return True
            platform_supports = getattr(self, "_platform_supports", None)
            if not callable(platform_supports) or platform_supports("onebot_actions", umo=umo):
                logger.warning(
                    "主动核心发送未找到匹配平台,尝试 OneBot 原生兜底: target=%s",
                    self._describe_send_target(umo, session, platform),
                )
            else:
                logger.warning(
                    "主动核心发送未被官方平台接受,不使用 OneBot 原生兜底: target=%s",
                    self._describe_send_target(umo, session, platform),
                )
        except Exception as e:
            core_error = e
            if self._is_onebot_event_checker_send_rejection(e):
                logger.info(
                    "主动核心发送被 QQ/NTQQ 底层拒绝，停止同链立即重试: target=%s",
                    self._describe_send_target(umo, session, platform),
                )
                raise RuntimeError(self._onebot_event_checker_rejection_summary()) from e
            if self._delivery_outcome_is_uncertain(e):
                logger.warning(
                    "主动核心发送回执不确定，为避免 OneBot 兜底重复发送，本次按已提交处理: target=%s error=%s",
                    self._describe_send_target(umo, session, platform),
                    self._format_send_exception(e),
                )
                self._confirm_outbound_delivery(umo, processed_chain)
                return True
            target = self._describe_send_target(umo, session, platform)
            precise_text = self._format_send_exception(precise_error) or "未尝试或未失败"
            fallback_text = self._format_send_exception(e)
            logger.warning(
                "主动核心发送失败: target=%s precise_error=%s fallback_error=%s",
                target,
                precise_text,
                fallback_text,
            )
        platform_supports = getattr(self, "_platform_supports", None)
        if callable(platform_supports) and not platform_supports("onebot_actions", umo=umo):
            target = self._describe_send_target(umo, session, platform)
            precise_text = self._format_send_exception(precise_error) or "未尝试或未失败"
            fallback_text = self._format_send_exception(core_error) if core_error is not None else (
                "AstrBot 核心发送返回 False（平台未找到或官方通道拒绝）"
            )
            raise RuntimeError(
                f"主动消息发送失败: {target}; precise={precise_text}; fallback={fallback_text}; 当前平台不使用 OneBot 原生兜底"
            ) from core_error
        direct_ok, direct_error = await self._send_chain_components_via_onebot_direct(umo, session, processed_chain)
        if direct_ok:
            self._confirm_outbound_delivery(umo, processed_chain)
            return True
        if self._is_onebot_event_checker_send_rejection(direct_error):
            raise RuntimeError(self._onebot_event_checker_rejection_summary())
        target = self._describe_send_target(umo, session, platform)
        precise_text = self._format_send_exception(precise_error) or "未尝试或未失败"
        if core_error is not None:
            fallback_text = self._format_send_exception(core_error)
        elif core_result is False:
            fallback_text = "AstrBot 核心发送返回 False（未找到匹配平台或平台拒绝发送）"
        else:
            fallback_text = "未尝试或未失败"
        logger.warning(
            "主动发送兜底也失败: target=%s precise_error=%s fallback_error=%s direct_error=%s",
            target,
            precise_text,
            fallback_text,
            direct_error,
        )
        raise RuntimeError(
            f"主动消息发送失败: {target}; precise={precise_text}; fallback={fallback_text}; direct={direct_error}"
        ) from core_error

    async def _send_media_proactive_chain(
        self,
        umo: str,
        text: str,
        image_path: str = "",
        *,
        extra_components: list[Any] | None = None,
        quote_message_id: str = "",
        disable_segmenting: bool = False,
        media_delivery_mode: str = "separate_after",
        require_complete_text_before_media: bool = False,
    ) -> _ProactiveSendOutcome:
        trigger_message_id = _single_line(quote_message_id, 120)
        delivered_segments: list[str] = []
        complete = True
        image_delivered = False
        extra_components_delivered = 0
        primary_complete = False
        failure_note = ""

        def outcome(*, note: str = "") -> _ProactiveSendOutcome:
            delivered_text = "\n".join(item for item in delivered_segments if item).strip()
            delivered = bool(delivered_text or image_delivered or extra_components_delivered)
            resolved_note = _single_line(note or failure_note, 240)
            return _ProactiveSendOutcome(
                delivered=delivered,
                complete=bool(delivered and complete and not resolved_note),
                delivered_text=delivered_text,
                image_delivered=image_delivered,
                extra_components_delivered=extra_components_delivered,
                note=resolved_note,
                primary_complete=primary_complete,
            )

        outbound_components = [
            component for component in (extra_components or []) if component is not None
        ]
        has_prebuilt_voice = any(
            isinstance(component, Record) for component in outbound_components
        )
        if self._contains_inline_image_tag(text):
            image_path = ""
            outbound_components = []
        if text:
            await self._maybe_send_input_status(umo, text)
        if media_delivery_mode == "same_message":
            platform_supports = getattr(self, "_platform_supports", None)
            platform_quote = not callable(platform_supports) or platform_supports(
                "reply_quote",
                umo=umo,
            )
            if quote_message_id and not platform_quote:
                logger.info(
                    "当前平台不支持主动引用，正文与表情同链发送已降级为普通发送: umo=%s",
                    _single_line(umo, 140),
                )
                quote_message_id = ""
            recalled_message_id = self._should_cancel_reply_for_recalled_message_ids(
                trigger_message_id
            )
            if recalled_message_id:
                logger.info(
                    "触发消息已撤回，取消主动正文与表情同链发送: umo=%s message_id=%s",
                    umo,
                    recalled_message_id,
                )
                complete = False
                return outcome(note="触发消息已撤回")
            combined_chain = self._build_outbound_chain(
                text,
                image_path,
                extra_components=outbound_components,
            )
            combined_chain = self._with_optional_reply(
                combined_chain,
                quote_message_id,
            )
            sent = await self._send_chain_components(umo, combined_chain)
            if sent:
                delivered_segments.append(text)
                image_delivered = bool(image_path and os.path.exists(image_path))
                extra_components_delivered = len(outbound_components)
                primary_complete = True
            else:
                complete = False
            return outcome(
                note="" if sent else "主动正文与表情同链发送未被平台接受"
            )
        platform_supports = getattr(self, "_platform_supports", None)
        platform_segmented = self._segmented_platform_allows(umo=umo)
        platform_quote = not callable(platform_supports) or platform_supports("reply_quote", umo=umo)
        if quote_message_id and not platform_quote:
            logger.info(
                "当前平台不支持主动引用，已降级为普通发送: umo=%s",
                _single_line(umo, 140),
            )
            quote_message_id = ""
        splitter = getattr(self, "_split_llm_controlled_text_for_event", None)
        if (
            callable(splitter)
            and bool(runtime_persona_setting(self, "enable_llm_controlled_segmenting", False))
            and not disable_segmenting
            and platform_segmented
            and self._segmented_scope_allows_umo(umo)
        ):
            segments = splitter(None, text, umo=umo)
        else:
            segments = self._split_proactive_text(
                text,
                umo=umo,
                image_path="",
                extra_components=None,
                disable_segmenting=disable_segmenting or not platform_segmented or not self._segmented_scope_allows_umo(umo),
            )
        if len(segments) > 1:
            logger.info(
                "主动媒体文本已分段: umo=%s segments=%s lengths=%s",
                _single_line(umo, 140),
                len(segments),
                [len(segment) for segment in segments],
            )
        if quote_message_id and segments and self._quote_skip_reason_for_short_reply(segments[0]):
            quote_message_id = ""

        image_exists = bool(image_path and os.path.exists(image_path))
        path_image_component: Any | None = None
        if image_exists:
            image_chain = self._build_outbound_chain("", image_path)
            path_image_component = next(
                (component for component in image_chain if isinstance(component, Image)),
                None,
            )
            image_exists = path_image_component is not None

        leading_components: list[Any] = []
        trailing_components: list[Any] = []
        for component in outbound_components:
            if component_kind(component) in {"voice", "at", "reply"}:
                leading_components.append(component)
            else:
                trailing_components.append(component)

        source_chain: list[Any] = list(leading_components)
        if segments:
            source_chain.append(Plain(text))
        source_chain.extend(trailing_components)
        if path_image_component is not None:
            source_chain.append(path_image_component)
        if quote_message_id:
            source_chain = self._with_optional_reply(source_chain, quote_message_id)

        strategies = component_strategies_from_owner(self)
        strategies["reaction"] = (
            "inline" if media_delivery_mode == "same_message" else "separate"
        )
        chunks, _changed, _split_changed, _full_text = plan_component_chunks(
            source_chain,
            plain_type=Plain,
            split_text=lambda _value: list(segments),
            strategies=strategies,
            component_order=component_order_from_owner(self),
            classify=component_kind,
        )

        primary_components: list[Plain] = []
        for chunk in chunks:
            for component_index, component in enumerate(chunk):
                if not isinstance(component, Plain) or len(primary_components) >= len(segments):
                    continue
                segment_index = len(primary_components)
                segment_component = self._proactive_plain_segment_component(
                    segments[segment_index],
                    full_text=text,
                    index=segment_index,
                    count=len(segments),
                    suppress_tts=has_prebuilt_voice,
                )
                try:
                    object.__setattr__(
                        segment_component,
                        "_private_companion_proactive_primary_text",
                        True,
                    )
                except Exception:
                    pass
                chunk[component_index] = segment_component
                primary_components.append(segment_component)

        has_media = bool(outbound_components or image_exists)
        if has_media:
            logger.info(
                "主动媒体已按组件策略规划: text_segments=%s chunks=%s image=%s extra_components=%s strategies=%s",
                len(segments),
                len(chunks),
                image_exists,
                len(outbound_components),
                strategies,
            )
        if not chunks:
            complete = False
            return outcome(note="主动正文与媒体均为空")

        remaining_extra_components = list(outbound_components)
        delivered_primary_count = 0

        def chunk_primary_texts(chunk: list[Any]) -> list[str]:
            return [
                str(getattr(component, "text", "") or "").strip()
                for component in chunk
                if isinstance(component, Plain)
                and bool(
                    getattr(
                        component,
                        "_private_companion_proactive_primary_text",
                        False,
                    )
                )
                and str(getattr(component, "text", "") or "").strip()
            ]

        for chunk_index, chunk in enumerate(chunks):
            primary_texts = chunk_primary_texts(chunk)
            chunk_has_reaction = any(
                component_kind(component) == "reaction" for component in chunk
            )
            primary_complete = bool(
                segments and delivered_primary_count >= len(segments)
            )
            if (
                require_complete_text_before_media
                and chunk_has_reaction
                and not primary_complete
            ):
                complete = False
                return outcome(note="主动正文未完整送达，已跳过表情图片")

            recalled_message_id = self._should_cancel_reply_for_recalled_message_ids(
                trigger_message_id
            )
            if recalled_message_id:
                logger.info(
                    "触发消息已撤回，停止主动组件发送: umo=%s message_id=%s chunk=%s/%s",
                    umo,
                    recalled_message_id,
                    chunk_index + 1,
                    len(chunks),
                )
                complete = False
                return outcome(note=f"第 {chunk_index + 1} 条发送前触发消息已撤回")

            try:
                sent = await self._send_chain_components(umo, chunk)
            except Exception as exc:
                has_delivered_content = bool(
                    delivered_segments or image_delivered or extra_components_delivered
                )
                has_future_primary = any(
                    chunk_primary_texts(candidate)
                    for candidate in chunks[chunk_index + 1 :]
                )
                if not primary_texts and has_future_primary:
                    complete = False
                    failure_note = failure_note or (
                        f"第 {chunk_index + 1} 条组件发送失败：{_single_line(exc, 160)}"
                    )
                    logger.warning(
                        "主动前置组件发送失败，继续发送正文: umo=%s chunk=%s error=%s",
                        _single_line(umo, 140),
                        chunk_index + 1,
                        _single_line(exc, 180),
                    )
                    continue
                if not has_delivered_content:
                    raise
                complete = False
                logger.warning(
                    "主动组件部分送达后后续发送失败，不再整条重试: umo=%s chunk=%s error=%s",
                    _single_line(umo, 140),
                    chunk_index + 1,
                    _single_line(exc, 180),
                )
                return outcome(
                    note=f"第 {chunk_index + 1} 条发送失败：{_single_line(exc, 160)}"
                )

            if not sent:
                complete = False
                failure_note = failure_note or f"第 {chunk_index + 1} 条未被平台接受"
                continue

            if primary_texts:
                delivered_segments.extend(primary_texts)
                delivered_primary_count += len(primary_texts)
            if path_image_component is not None and any(
                component is path_image_component for component in chunk
            ):
                image_delivered = True
            for sent_component in chunk:
                matched_index = next(
                    (
                        index
                        for index, candidate in enumerate(remaining_extra_components)
                        if sent_component is candidate
                    ),
                    -1,
                )
                if matched_index >= 0:
                    remaining_extra_components.pop(matched_index)
                    extra_components_delivered += 1

            primary_complete = bool(
                segments and delivered_primary_count >= len(segments)
            )
            if primary_texts and any(
                chunk_primary_texts(candidate)
                for candidate in chunks[chunk_index + 1 :]
            ):
                await asyncio.sleep(
                    await self._calc_segmented_proactive_interval(primary_texts[-1], umo=umo)
                )

        primary_complete = bool(
            segments and delivered_primary_count >= len(segments)
        )
        return outcome()

    @staticmethod
    def _normalize_reaction_expression_delivery_mode(value: Any) -> str:
        mode = str(value or "separate_after").strip().lower().replace("-", "_")
        aliases = {
            "after": "separate_after",
            "separate": "separate_after",
            "separate_after_text": "separate_after",
            "inline": "same_message",
            "current_chain": "same_message",
            "same_chain": "same_message",
            "before": "separate_before",
            "separate_before_text": "separate_before",
        }
        normalized = aliases.get(mode, mode)
        if normalized in {"separate_after", "same_message", "separate_before"}:
            return normalized
        return "separate_after"

    def _build_proactive_reaction_event(
        self,
        *,
        umo: str,
        user_id: str,
        visible_text: str,
    ) -> Any:
        extras: dict[str, Any] = {}
        event = SimpleNamespace(
            unified_msg_origin=umo,
            message_str=visible_text,
            extras=extras,
        )
        event.get_sender_id = lambda: user_id
        event.get_message_str = lambda: visible_text
        event.is_private_chat = lambda: True
        event.get_extra = lambda key: extras.get(key)
        event.set_extra = lambda key, value: extras.__setitem__(key, value)
        return event

    async def _prepare_proactive_reaction_attachment(
        self,
        umo: str,
        visible_text: str,
    ) -> tuple[Any | None, dict[str, Any] | None]:
        entry = self._pop_proactive_reaction_intent(umo)
        intent = entry.get("intent") if isinstance(entry.get("intent"), dict) else {}
        user_id = _single_line(entry.get("user_id"), 160)
        if (
            not intent
            or not user_id
            or not self._proactive_reaction_expression_enabled("message")
        ):
            return None, None
        sticker_only = self._proactive_reaction_intent_allows_sticker_only(intent)
        visible_checker = getattr(self, "_reaction_expression_has_visible_text", None)
        if callable(visible_checker) and not visible_checker(visible_text) and not sticker_only:
            return None, None

        event = self._build_proactive_reaction_event(
            umo=_single_line(umo, 240),
            user_id=user_id,
            visible_text=str(visible_text or ""),
        )
        preauthorize = getattr(self, "_preauthorize_reaction_expression_prompt", None)
        prepare = getattr(self, "_pc_reaction_expression_impl", None)
        settle = getattr(self, "_settle_reaction_expression_attachment_data", None)
        if not callable(preauthorize) or not callable(prepare) or not callable(settle):
            return None, None
        try:
            if not await preauthorize(event):
                return None, None
            raw_prepared = await prepare(
                event,
                query=_single_line(intent.get("provider_query"), 500),
                context=_single_line(intent.get("context"), 1000)
                or _single_line(visible_text, 700),
                meme_only=True,
                send=True,
                purpose=_single_line(intent.get("purpose"), 120),
                emotion=_single_line(intent.get("emotion"), 80),
                intensity=_safe_int(intent.get("intensity"), 0, 0, 5),
                candidate_queries=intent.get("candidate_queries", []),
                attach_only=True,
            )
            prepared = json.loads(raw_prepared)
        except Exception as exc:
            pending = getattr(
                event,
                "_private_companion_reaction_expression_pending_attachment",
                None,
            )
            if isinstance(pending, dict):
                await settle(pending, sent=False, reason="attachment_prepare_failed")
            logger.warning(
                "主动表情附件准备失败,继续发送纯文字: error_type=%s",
                type(exc).__name__,
            )
            return None, None
        if not isinstance(prepared, dict) or prepared.get("decision") != "attach":
            return None, None

        pending = getattr(
            event,
            "_private_companion_reaction_expression_pending_attachment",
            None,
        )
        image_path = _path_text(prepared.get("path"), 1000)
        if not isinstance(pending, dict) or not image_path or not os.path.isfile(image_path):
            if isinstance(pending, dict):
                await settle(pending, sent=False, reason="attachment_file_missing")
            return None, None
        pending["sticker_only"] = sticker_only
        try:
            builder = getattr(self, "_build_reaction_image_component", None)
            if callable(builder):
                image_component = builder(event, image_path)
            else:
                try:
                    image_component = Image.fromFileSystem(image_path)
                except AttributeError:
                    image_component = Image.from_file_system(image_path)
        except Exception as exc:
            await settle(pending, sent=False, reason="attachment_component_failed")
            logger.warning(
                "主动表情图片组件构建失败,继续发送纯文字: error_type=%s",
                type(exc).__name__,
            )
            return None, None

        pending["attached"] = True
        pending["component"] = image_component
        runtime_logger = getattr(self, "_log_reaction_expression_event", None)
        if callable(runtime_logger):
            runtime_logger(
                event,
                stage="attachment",
                decision="accepted",
                reason="attachment_appended",
                scope="private",
                found=True,
                sent=False,
                image_id=prepared.get("image_id"),
                confidence=prepared.get("confidence"),
                cache_hit=prepared.get("cache_hit"),
                latency_ms=prepared.get("lookup_latency_ms"),
                match_basis=pending.get("match_basis"),
            )
        return image_component, pending

    async def _settle_proactive_reaction_attachment(
        self,
        pending: dict[str, Any] | None,
        *,
        sent: bool,
        reason: str,
    ) -> None:
        if not isinstance(pending, dict):
            return
        settle = getattr(self, "_settle_reaction_expression_attachment_data", None)
        if not callable(settle):
            return
        try:
            await settle(pending, sent=sent, reason=reason)
        except Exception as exc:
            # Delivery state is authoritative. A bookkeeping failure must not
            # make the caller retry content that the platform already received.
            logger.warning(
                "主动表情发送结算失败,不改变消息投递结果: "
                "sent=%s reason=%s error_type=%s",
                bool(sent),
                _single_line(reason, 80),
                type(exc).__name__,
            )

    async def _proactive_persona_delivery_allowed(self, target_umo: str) -> bool:
        """Revalidate the scheduled persona immediately before platform I/O."""
        validator = getattr(self, "_validate_proactive_persona_delivery", None)
        if not callable(validator):
            return True

        multi_persona = bool(getattr(self, "enable_multi_persona_mode", False))
        active_getter = getattr(self, "_active_persona_scope", None)
        scheduled_persona_id = ""
        if callable(active_getter):
            try:
                scheduled_persona_id = str(active_getter() or "").strip()
            except Exception:
                scheduled_persona_id = ""
        if not multi_persona and not scheduled_persona_id:
            effective_getter = getattr(self, "_effective_plugin_persona_id", None)
            if callable(effective_getter):
                try:
                    scheduled_persona_id = str(effective_getter() or "").strip()
                except Exception:
                    scheduled_persona_id = ""
        if not multi_persona and not scheduled_persona_id:
            primary_getter = getattr(self, "_primary_persona_id", None)
            try:
                scheduled_persona_id = str(
                    primary_getter()
                    if callable(primary_getter)
                    else getattr(self, "plugin_specific_persona_id", "")
                ).strip()
            except Exception:
                scheduled_persona_id = ""

        try:
            result = validator(target_umo, scheduled_persona_id)
            if inspect.isawaitable(result):
                result = await result
            allowed = bool(result.get("ok")) if isinstance(result, dict) else bool(result)
        except Exception as exc:
            logger.warning(
                "主动消息最终人格一致性校验失败: multi=%s persona=%s umo=%s error=%s",
                multi_persona,
                _single_line(scheduled_persona_id, 96) or "-",
                _single_line(target_umo, 140) or "-",
                _single_line(exc, 160),
            )
            return not multi_persona

        if allowed:
            return True
        reason_code = _single_line(result.get("reason_code"), 80) if isinstance(result, dict) else ""
        action = _single_line(result.get("action"), 40) if isinstance(result, dict) else ""
        logger.warning(
            "主动投递因人格不一致被取消: multi=%s persona=%s umo=%s action=%s reason=%s",
            multi_persona,
            _single_line(scheduled_persona_id, 96) or "-",
            _single_line(target_umo, 140) or "-",
            action or "blocked",
            reason_code or "validator_rejected",
        )
        return False

    @collect_proactive_delivery
    async def _send_proactive_message_chain(
        self,
        umo: str,
        text: str,
        image_path: str = "",
        *,
        extra_components: list[Any] | None = None,
        quote_message_id: str = "",
        disable_segmenting: bool = False,
    ) -> _ProactiveSendOutcome:
        if not await self._proactive_persona_delivery_allowed(umo):
            return _ProactiveSendOutcome(False, False, note="主动投递人格已变化，已取消发送")
        # Recheck at the final delivery boundary. A realtime call may start
        # after a proactive candidate was planned but before it is sent.
        busy_context_getter = getattr(self, "_busy_reply_proactive_block_context", None)
        if callable(busy_context_getter):
            try:
                busy_context = busy_context_getter({}, now=_now_ts())
            except TypeError:
                busy_context = busy_context_getter({}, now=_now_ts(), umo=umo)
            except Exception:
                busy_context = {}
            if isinstance(busy_context, dict) and busy_context.get("kind") == "external_realtime":
                logger.info(
                    "实时共同活动期间在最终发送边界取消主动消息: umo=%s",
                    _single_line(umo, 140),
                )
                return _ProactiveSendOutcome(False, False, note="实时共同活动期间已取消主动消息")
        trigger_message_id = _single_line(quote_message_id, 120)
        placeholder_cleaner = getattr(self, "_sanitize_orphan_tts_placeholders", None)
        if callable(placeholder_cleaner):
            cleaned_text = placeholder_cleaner(text)
            if cleaned_text != text:
                logger.warning(
                    "主动发送前清理孤儿 TTS 占位符: umo=%s before=%s after=%s",
                    _single_line(umo, 120),
                    _single_line(text, 120),
                    _single_line(cleaned_text, 120),
                )
                text = cleaned_text
        reaction_pending: dict[str, Any] | None = None
        reaction_delivery_mode = self._normalize_reaction_expression_delivery_mode(
            runtime_persona_setting(self, "reaction_expression_delivery_mode", "separate_after")
        )
        has_existing_media = bool(
            image_path
            or extra_components
            or (text and self._contains_inline_image_tag(text))
        )
        if has_existing_media:
            self._clear_proactive_reaction_intent(umo)
        else:
            reaction_component, reaction_pending = await self._prepare_proactive_reaction_attachment(
                umo,
                text,
            )
            if reaction_component is not None:
                try:
                    object.__setattr__(
                        reaction_component,
                        "_private_companion_reaction_expression",
                        True,
                    )
                except Exception:
                    pass
                if isinstance(reaction_pending, dict) and reaction_pending.get("sticker_only"):
                    try:
                        reaction_sent = bool(
                            await self._send_chain_components(
                                umo,
                                [reaction_component],
                            )
                        )
                    except Exception as exc:
                        reaction_sent = False
                        logger.warning(
                            "主动纯表情投递失败: umo=%s error_type=%s",
                            _single_line(umo, 140),
                            type(exc).__name__,
                        )
                    await self._settle_proactive_reaction_attachment(
                        reaction_pending,
                        sent=reaction_sent,
                        reason="delivered" if reaction_sent else "delivery_failed",
                    )
                    return _ProactiveSendOutcome(
                        delivered=reaction_sent,
                        complete=reaction_sent,
                        extra_components_delivered=1 if reaction_sent else 0,
                        note="" if reaction_sent else "主动纯表情未送达",
                    )
                if isinstance(reaction_pending, dict):
                    reaction_pending["delivery_mode"] = reaction_delivery_mode
                if reaction_delivery_mode == "separate_before":
                    try:
                        reaction_sent = bool(
                            await self._send_chain_components(
                                umo,
                                [reaction_component],
                            )
                        )
                    except Exception as exc:
                        reaction_sent = False
                        logger.warning(
                            "主动表情先行发送失败，继续发送正文: "
                            "umo=%s error_type=%s",
                            _single_line(umo, 140),
                            type(exc).__name__,
                        )
                    await self._settle_proactive_reaction_attachment(
                        reaction_pending,
                        sent=reaction_sent,
                        reason="delivered" if reaction_sent else "delivery_failed",
                    )
                    reaction_pending = None
                else:
                    extra_components = [reaction_component]
        if has_existing_media or image_path or extra_components:
            try:
                outcome = await self._send_media_proactive_chain(
                    umo,
                    text,
                    image_path,
                    extra_components=extra_components,
                    quote_message_id=quote_message_id,
                    disable_segmenting=disable_segmenting,
                    media_delivery_mode=(
                        reaction_delivery_mode
                        if reaction_pending is not None
                        else "separate_after"
                    ),
                    require_complete_text_before_media=bool(
                        reaction_pending is not None
                        and reaction_delivery_mode == "separate_after"
                    ),
                )
            except Exception:
                await self._settle_proactive_reaction_attachment(
                    reaction_pending,
                    sent=False,
                    reason=(
                        "primary_not_delivered"
                        if reaction_pending is not None
                        and reaction_delivery_mode == "separate_after"
                        else "delivery_failed"
                    ),
                )
                raise
            if reaction_pending is not None:
                reaction_sent = bool(outcome.extra_components_delivered)
                settlement_reason = (
                    "delivered"
                    if reaction_sent
                    else "primary_not_delivered"
                    if reaction_delivery_mode == "separate_after"
                    and not outcome.primary_complete
                    else "delivery_failed"
                )
                await self._settle_proactive_reaction_attachment(
                    reaction_pending,
                    sent=reaction_sent,
                    reason=settlement_reason,
                )
            return outcome
        if text:
            await self._maybe_send_input_status(umo, text)
        splitter = getattr(self, "_split_llm_controlled_text_for_event", None)
        if (
            callable(splitter)
            and bool(runtime_persona_setting(self, "enable_llm_controlled_segmenting", False))
            and not disable_segmenting
            and self._segmented_platform_allows(umo=umo)
            and self._segmented_scope_allows_umo(umo)
        ):
            segments = splitter(None, text, umo=umo)
        else:
            segments = self._split_proactive_text(
                text,
                umo=umo,
                image_path="",
                extra_components=None,
                disable_segmenting=(
                    disable_segmenting
                    or not self._segmented_platform_allows(umo=umo)
                    or not self._segmented_scope_allows_umo(umo)
                ),
            )
        if len(segments) > 1:
            logger.info(
                "主动文本已分段: umo=%s segments=%s lengths=%s",
                _single_line(umo, 140),
                len(segments),
                [len(segment) for segment in segments],
            )
        if len(segments) <= 1:
            outbound_text = segments[0] if segments else text
            if not str(outbound_text or "").strip():
                return _ProactiveSendOutcome(False, False, note="主动正文为空")
            if quote_message_id and self._quote_skip_reason_for_short_reply(outbound_text):
                quote_message_id = ""
            recalled_message_id = self._should_cancel_reply_for_recalled_message_ids(trigger_message_id)
            if recalled_message_id:
                logger.info("触发消息已撤回，取消主动消息发送: umo=%s message_id=%s", umo, recalled_message_id)
                return _ProactiveSendOutcome(False, False, note="触发消息已撤回")
            sent = await self._send_chain_components(
                umo,
                self._with_optional_reply(
                    [
                        self._proactive_plain_segment_component(outbound_text, full_text=text, index=0, count=1)
                    ],
                    quote_message_id,
                ),
            )
            return _ProactiveSendOutcome(
                delivered=bool(sent),
                complete=bool(sent),
                delivered_text=outbound_text if sent else "",
                note="" if sent else "主动发送组件被取消或清空",
            )
        recalled_message_id = self._should_cancel_reply_for_recalled_message_ids(trigger_message_id)
        if recalled_message_id:
            logger.info("触发消息已撤回，取消主动合并分段发送: umo=%s message_id=%s", umo, recalled_message_id)
            return _ProactiveSendOutcome(False, False, note="触发消息已撤回")
        if await self._send_segmented_proactive_forward_message(umo, segments, source="proactive_text"):
            return _ProactiveSendOutcome(True, True, delivered_text="\n".join(segments).strip())
        delivered_segments: list[str] = []
        complete = True
        for index, segment in enumerate(segments):
            if index == 0 and quote_message_id and self._quote_skip_reason_for_short_reply(segment):
                quote_message_id = ""
            recalled_message_id = self._should_cancel_reply_for_recalled_message_ids(trigger_message_id)
            if recalled_message_id:
                logger.info("触发消息已撤回，停止主动消息分段发送: umo=%s message_id=%s index=%s", umo, recalled_message_id, index + 1)
                return _ProactiveSendOutcome(
                    bool(delivered_segments),
                    False,
                    delivered_text="\n".join(delivered_segments).strip(),
                    note=f"第 {index + 1} 段发送前触发消息已撤回",
                )
            segment_comp = self._proactive_plain_segment_component(segment, full_text=text, index=index, count=len(segments))
            chain = self._with_optional_reply([segment_comp], quote_message_id) if index == 0 else [segment_comp]
            try:
                sent = await self._send_chain_components(umo, chain)
            except Exception as exc:
                if not delivered_segments:
                    raise
                logger.warning(
                    "主动文本部分送达后后续分段失败，不再整条重试: umo=%s index=%s error=%s",
                    _single_line(umo, 140),
                    index + 1,
                    _single_line(exc, 180),
                )
                return _ProactiveSendOutcome(
                    True,
                    False,
                    delivered_text="\n".join(delivered_segments).strip(),
                    note=f"第 {index + 1} 段发送失败：{_single_line(exc, 160)}",
                )
            if sent:
                delivered_segments.append(segment)
            else:
                complete = False
            quote_message_id = ""
            if index < len(segments) - 1:
                try:
                    interval = await self._calc_segmented_proactive_interval(segment, umo=umo)
                except TypeError:
                    interval = await self._calc_segmented_proactive_interval(segment)
                await asyncio.sleep(interval)
        delivered_text = "\n".join(delivered_segments).strip()
        return _ProactiveSendOutcome(
            delivered=bool(delivered_text),
            complete=bool(delivered_text and complete),
            delivered_text=delivered_text,
            note="" if complete else "部分分段被发送钩子取消或清空",
        )

    def _build_outbound_result(
        self,
        text: str,
        image_path: str = "",
        extra_components: list[Any] | None = None,
    ) -> Any:
        chain = self._build_outbound_chain(text, image_path, extra_components=extra_components)
        return self._build_result_from_chain(chain)

    def _build_proactive_archive_user_prompt(
        self,
        *,
        reason: str,
        action: str,
        motive: str = "",
        action_summary: str = "",
    ) -> str:
        return ""

    @staticmethod
    def _proactive_component_is_image(component: Any) -> bool:
        return isinstance(component, Image) or bool(
            getattr(component, "_private_companion_reaction_expression", False)
        )

    @staticmethod
    def _proactive_components_contain_image(components: list[Any] | None) -> bool:
        return any(
            ProactiveMessageMixin._proactive_component_is_image(component)
            for component in (components or [])
        )

    def _build_actual_proactive_delivery_summary(
        self,
        *,
        text: str,
        image_path: str = "",
        extra_components: list[Any] | None = None,
        original_summary: str = "",
    ) -> str:
        parts: list[str] = []
        visible_text = self._visible_text_without_tts_reading(text, limit=320)
        if visible_text:
            parts.append(f"文字消息：{visible_text}")

        image_count = int(bool(image_path)) + sum(
            1
            for component in (extra_components or [])
            if self._proactive_component_is_image(component)
        )
        if image_count:
            photo_caption = ""
            if "：" in str(original_summary or "") or ":" in str(original_summary or ""):
                photo_caption = _single_line(
                    re.split(r"[:：]", str(original_summary), maxsplit=1)[-1],
                    220,
                )
            image_label = "图片" if image_count == 1 else f"{image_count} 张图片"
            if photo_caption and photo_caption not in {"发图", "图片", "photo_text"}:
                parts.append(f"{image_label}：{photo_caption}")
            else:
                parts.append(f"{image_label}已发送")

        voice_count = sum(
            1 for component in (extra_components or []) if isinstance(component, Record)
        )
        if voice_count:
            parts.append("语音消息已发送" if voice_count == 1 else f"{voice_count} 条语音消息已发送")

        other_count = sum(
            1
            for component in (extra_components or [])
            if not self._proactive_component_is_image(component)
            and not isinstance(component, Record)
        )
        if other_count:
            parts.append(f"{other_count} 个附加消息组件已发送")
        return _single_line("；".join(parts), 500)

    def _reconcile_proactive_delivery_metadata(
        self,
        *,
        text: str,
        image_path: str = "",
        extra_components: list[Any] | None = None,
        action: str = "message",
        action_summary: str = "",
        delivery_complete: bool = True,
    ) -> tuple[str, str, bool]:
        delivered_photo = bool(image_path) or self._proactive_components_contain_image(extra_components)
        if delivery_complete:
            return action or "message", action_summary, delivered_photo

        action_parts = [part.strip() for part in str(action or "").split("+") if part.strip()]
        removed_media = False
        if not delivered_photo and "photo_text" in action_parts:
            action_parts = [part for part in action_parts if part != "photo_text"]
            removed_media = True
        delivered_voice = any(isinstance(component, Record) for component in (extra_components or []))
        if not delivered_voice and "voice" in action_parts:
            action_parts = [part for part in action_parts if part != "voice"]
            removed_media = True
        if removed_media and text and "message" not in action_parts:
            action_parts.insert(0, "message")
        actual_action = "+".join(action_parts) or ("message" if text else action or "message")
        actual_summary = self._build_actual_proactive_delivery_summary(
            text=text,
            image_path=image_path,
            extra_components=extra_components,
            original_summary=action_summary,
        )
        return actual_action, actual_summary or "主动消息仅部分送达。", delivered_photo

    def _build_proactive_archive_assistant_text(
        self,
        *,
        text: str,
        image_path: str = "",
        extra_components: list[Any] | None = None,
        action_summary: str = "",
        photo_subject_owner: str = "",
    ) -> str:
        original_is_receipt = self._is_proactive_delivery_receipt_text(text)
        message_text = sanitize_llm_segment_control_tokens(
            self._visible_text_without_tts_reading(text, limit=1000)
        )
        attachment_notes: list[str] = []
        history_image_count = 0
        history_record_count = 0
        if image_path:
            history_image_count += 1
            photo_caption = ""
            if "：" in str(action_summary or "") or ":" in str(action_summary or ""):
                photo_caption = _single_line(re.split(r"[:：]", str(action_summary), maxsplit=1)[-1], 220)
            if photo_caption and photo_caption not in {"发图", "图片", "photo_text"}:
                attachment_notes.append(f"图片画面：{photo_caption}")
            normalized_owner = _normalize_photo_subject_owner(photo_subject_owner)
            if normalized_owner:
                attachment_notes.append(f"图片主体：{_photo_subject_owner_prompt_label(normalized_owner)}")
        if extra_components:
            tts_notes: list[str] = []
            note_builder = getattr(self, "_tts_component_log_note", None)
            image_components = [
                comp
                for comp in extra_components
                if self._proactive_component_is_image(comp)
            ]
            for comp in extra_components:
                if isinstance(comp, Record) and callable(note_builder):
                    note = _single_line(note_builder(comp), 220)
                    if note:
                        tts_notes.append(note)
            if image_components:
                history_image_count += len(image_components)
                photo_caption = ""
                if "：" in str(action_summary or "") or ":" in str(action_summary or ""):
                    photo_caption = _single_line(re.split(r"[:：]", str(action_summary), maxsplit=1)[-1], 220)
                if photo_caption and photo_caption not in {"发图", "图片", "photo_text"}:
                    attachment_notes.append(f"图片画面：{photo_caption}")
                normalized_owner = _normalize_photo_subject_owner(photo_subject_owner)
                if normalized_owner:
                    attachment_notes.append(f"图片主体：{_photo_subject_owner_prompt_label(normalized_owner)}")
            if tts_notes:
                attachment_notes.extend(tts_notes[:3])
            record_count = sum(1 for comp in extra_components if isinstance(comp, Record))
            history_record_count += record_count
            other_count = len(extra_components) - len(image_components) - record_count
            if other_count > 0:
                attachment_notes.append(f"随消息发送了 {other_count} 个附加消息组件")
        if attachment_notes:
            suffix = "（" + ",".join(attachment_notes) + "）"
            message_text = f"{message_text}{suffix}" if message_text else suffix
        media_marker = _format_history_media_marker(
            images=history_image_count,
            records=history_record_count,
        )
        if media_marker:
            message_text = f"{message_text}\n{media_marker}" if message_text else media_marker
        if message_text:
            return message_text
        if original_is_receipt:
            return ""
        return _single_line(action_summary, 160) or "主动向用户发送了一条消息。"

    async def _archive_proactive_message_to_conversation(
        self,
        *,
        user: dict[str, Any],
        user_prompt: str,
        assistant_response: str,
        umo: str = "",
    ) -> bool:
        umo = str(umo or user.get("umo") or "").strip()
        if not umo or not assistant_response:
            return False
        visible_assistant_response = sanitize_llm_segment_control_tokens(
            _strip_outbound_control_blocks(
                assistant_response,
                enabled=bool(runtime_persona_setting(self, "enable_framework_error_leak_guard", True)),
                tts_enabled=bool(runtime_persona_setting(self, "enable_tts_enhancement", False)),
            )
        )
        if not visible_assistant_response:
            return False
        for attempt in range(4):
            try:
                safe_user_prompt = str(user_prompt or "").strip()
                archive_context_only = not safe_user_prompt or self._proactive_archive_context_text(
                    safe_user_prompt
                )
                assistant_msg_obj = AssistantMessageSegment(content=visible_assistant_response)

                async def _write():
                    conv_id = await self._ensure_conversation_id_for_umo(umo, title="Private Companion 主动消息")
                    if not conv_id:
                        return False
                    conversation_manager = self.context.conversation_manager
                    if archive_context_only:
                        conversation = await conversation_manager.get_conversation(umo, conv_id)
                        if conversation is None:
                            return False
                        raw_history = getattr(conversation, "history", "[]")
                        if isinstance(raw_history, str):
                            history = json.loads(raw_history or "[]")
                        elif isinstance(raw_history, list):
                            history = list(raw_history)
                        else:
                            history = []
                        history.append(assistant_msg_obj.model_dump())
                        await conversation_manager.update_conversation(umo, conv_id, history=history)
                    else:
                        await conversation_manager.add_message_pair(
                            cid=conv_id,
                            user_message=UserMessageSegment(content=safe_user_prompt),
                            assistant_message=assistant_msg_obj,
                        )
                    return True

                written = await self._conversation_db_operation("archive_proactive_message", _write)
                if not written:
                    logger.warning("主动消息存档失败: 无法获取或创建 AstrBot 会话 history umo=%s", _single_line(umo, 140))
                    return False
                if attempt > 0:
                    logger.info("主动消息写入 AstrBot 会话历史成功: %s retry=%s", umo, attempt)
                else:
                    logger.info("已将主动消息写入 AstrBot 会话历史: %s", umo)
                return True
            except Exception as e:
                text = str(e or "").lower()
                if ("database is locked" in text or "sqlite3.operationalerror" in text) and attempt < 3:
                    await asyncio.sleep(0.25 * (attempt + 1))
                    continue
                logger.warning("主动消息写入会话历史失败: %s", e)
                return False
        return False

    def _format_story_plan_for_prompt(self) -> str:
        plan = self.data.get("daily_story_plan", {})
        if not isinstance(plan, dict) or plan.get("date") != _today_key():
            return "（暂无）"
        lines = []
        now_minutes = self._environment_now_minutes()
        events = plan.get("today_events", [])
        if isinstance(events, list) and events:
            nearby_events = [
                item for item in events
                if isinstance(item, dict) and self._story_item_relevant_to_now(item, now_minutes)
            ][:6]
            if nearby_events:
                lines.append("附近可能发生：")
                for item in nearby_events:
                    lines.append(f"- {item.get('window', '')}｜{item.get('event', '')}｜{item.get('mood', '')}")
        proactive = plan.get("proactive_events", [])
        if isinstance(proactive, list) and proactive:
            nearby_proactive = [
                item for item in proactive
                if isinstance(item, dict) and self._story_item_relevant_to_now(item, now_minutes, future_minutes=240)
            ][:6]
            if nearby_proactive:
                lines.append("附近主动计划：")
                for item in nearby_proactive:
                    lines.append(
                        f"- {item.get('window', '')}｜{item.get('reason', '')}｜{item.get('action', 'message')}｜"
                        f"{item.get('why', '')}｜{item.get('topic', '')}｜{item.get('motive', '')}｜"
                        f"{item.get('scene', '')}｜{item.get('tone', '')}｜{item.get('impulse', '')}"
                    )
        long_term = plan.get("long_term_events", [])
        if isinstance(long_term, list) and long_term:
            lines.append("长线事件：")
            for item in long_term[:4]:
                if isinstance(item, dict):
                    lines.append(
                        f"- {item.get('title', '')}｜{item.get('status', '')}｜"
                        f"{item.get('tendency', '')}｜{item.get('next_hint', '')}"
                    )
        return "\n".join(lines) if lines else "（暂无）"
