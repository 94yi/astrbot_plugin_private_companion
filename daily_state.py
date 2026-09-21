# -*- coding: utf-8 -*-
"""
DailyStateMixin — 日程、状态、天气、日记、技能成长和计时器
"""
from __future__ import annotations

import asyncio
import ast
import base64
import gc
import hashlib
import html
import inspect
import importlib
import json
import math
import os
import random
import re
import sqlite3
import shutil
import sys
import time
import unicodedata
import uuid
import zoneinfo
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from http.cookies import SimpleCookie
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable
from urllib.parse import parse_qsl, quote, urlencode, urlparse, urlunparse
from xml.etree import ElementTree as ET

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
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.core import file_token_service
from astrbot.core.astr_main_agent import MainAgentBuildConfig, build_main_agent
from astrbot.core.agent.message import AssistantMessageSegment, TextPart, UserMessageSegment
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
    PromptRenderMode,
    PromptSection,
    prompt_section,
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
from .helpers import _date_key, _memory_archive_warning, _normalize_outbound_punctuation_flow, _normalize_photo_subject_owner, _now_ts, _path_text, _photo_subject_owner_prompt_label, _safe_float, _safe_int, _single_line, _strip_internal_message_blocks, _today_key, normalize_legacy_tag_text
from .model_routing import CURRENT_MODEL_REPLACEMENT_SOURCES, find_route, scope_allows
from .persona_config import runtime_persona_setting
from .story_authority import story_legacy_sync_operation
from .conversation_injection_plan import (
    PLACEMENT_DYNAMIC_SYSTEM,
    PLACEMENT_TURN_TAIL,
    get_conversation_injection_plan,
)
from .domains.affect.affect_modulation import compose_affect_modulation
from .daily_state_tick import DailyStateTickMixin
from .daily_state_proactive import DailyStateProactiveMixin
from .daily_state_diary import DailyStateDiaryMixin
from .daily_state_meal import DailyStateMealMixin
from .daily_state_sleep import DailyStateSleepMixin
from .daily_state_skill_growth import DailyStateSkillGrowthMixin
from .daily_state_context_snapshot import DailyStateContextSnapshotMixin
from .daily_state_timer import DailyStateTimerMixin
from .daily_state_weather import DailyStateWeatherMixin
from .memo_notes import memo_note_due_state, memo_note_sort_key, normalize_memo_note
from .agenda_contracts import normalize_plan_item
from .planning import (
    build_daily_plan_prompt,
    build_daily_plan_prompt_section,
    build_detail_enhancement_prompt,
    build_detail_enhancement_prompt_section,
    evaluate_detail_quality,
    format_plan_for_diary,
    generate_daily_plan,
    generate_detail_enhancement,
    get_schedule_planning_prompt,
    normalize_long_term_events,
    normalize_story_items,
    normalize_story_plan,
    pick_detail_segment,
)
from .logging_util import get_module_logger

logger = get_module_logger(__name__)


DEFAULT_AI_DAILY_NEWS_SOURCE = "B站 AI早报|bilibili:285286947"

DEFAULT_NEWS_SOURCES = "\n".join(
    [
        "BBC中文|https://feeds.bbci.co.uk/zhongwen/simp/rss.xml",
        "Google新闻中文|https://news.google.com/rss?hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
        "Solidot|https://www.solidot.org/index.rss",
        "Hacker News|https://hnrss.org/frontpage",
        "MIT Technology Review|https://www.technologyreview.com/feed/",
        "Ars Technica|https://feeds.arstechnica.com/arstechnica/index",
        DEFAULT_AI_DAILY_NEWS_SOURCE,
    ]
)

DEFAULT_PERSONA_PROMPT_FALLBACK = "未读取到 AstrBot 默认人格。请保持简洁、温和、有边界,不额外创造新身份。"









LEGACY_DEFAULT_NEWS_SOURCES = "\\n".join(
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


class DailyStateMixin(DailyStateTickMixin, DailyStateWeatherMixin, DailyStateTimerMixin, DailyStateContextSnapshotMixin, DailyStateSkillGrowthMixin, DailyStateSleepMixin, DailyStateMealMixin, DailyStateDiaryMixin, DailyStateProactiveMixin):
    """日程、状态、天气、日记、技能成长和计时器"""

    def _save_daily_state_sections(self, sections: set[str]) -> None:
        saver = getattr(self, "_save_data_sync", None)
        if not callable(saver):
            return
        try:
            saver(sections=sections)
        except TypeError as exc:
            if "unexpected keyword argument" not in str(exc) or "sections" not in str(exc):
                raise
            saver()

    def _daily_generation_lock(self, attribute: str) -> asyncio.Lock:
        scope = self._daily_generation_scope()
        if scope:
            locks_attribute = f"{attribute}_by_scope"
            locks = getattr(self, locks_attribute, None)
            if not isinstance(locks, dict):
                locks = {}
                setattr(self, locks_attribute, locks)
            lock = locks.get(scope)
            if not isinstance(lock, asyncio.Lock):
                lock = asyncio.Lock()
                locks[scope] = lock
            return lock
        lock = getattr(self, attribute, None)
        if not isinstance(lock, asyncio.Lock):
            lock = asyncio.Lock()
            setattr(self, attribute, lock)
        return lock

    def _daily_generation_scope(self) -> str:
        getter = getattr(self, "_active_persona_scope", None)
        return str(getter() if callable(getter) else "").strip()

    def _daily_force_result_cache(self, attribute: str) -> dict[str, dict[str, Any]]:
        cache = getattr(self, attribute, None)
        if not isinstance(cache, dict):
            cache = {}
            setattr(self, attribute, cache)
        return cache

    def _sync_detail_enhancement_day_locked(
        self,
        plan_date: Any,
        *,
        reset: bool = False,
    ) -> bool:
        """Keep live detail snapshots bound to the plan that owns them."""
        date_key = _single_line(plan_date, 16)
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_key):
            return False
        enhanced = self.data.get("detail_enhanced_segments")
        changed = (
            bool(reset)
            or _single_line(self.data.get("detail_enhanced_day"), 16) != date_key
            or not isinstance(enhanced, dict)
        )
        if not changed:
            return False
        self.data["detail_enhanced_day"] = date_key
        self.data["detail_enhanced_segments"] = {}
        story = self.data.get("daily_story_plan")
        if bool(reset) or (
            isinstance(story, dict)
            and _single_line(story.get("date"), 16) not in {"", date_key}
        ):
            self.data["daily_story_plan"] = {}
        return True

    def _detail_enhanced_segments_for_plan_date(
        self,
        plan_date: Any,
        enhanced: Any = None,
        *,
        detail_day: Any = None,
    ) -> dict[str, Any]:
        date_key = _single_line(plan_date, 16)
        recorded_day = self.data.get("detail_enhanced_day") if detail_day is None else detail_day
        if not date_key or _single_line(recorded_day, 16) != date_key:
            return {}
        source = enhanced if isinstance(enhanced, dict) else self.data.get("detail_enhanced_segments")
        if not isinstance(source, dict):
            return {}
        current: dict[str, Any] = {}
        for raw_key, snapshot in source.items():
            key = str(raw_key or "")
            keyed = re.match(r"^(\d{4}-\d{2}-\d{2}):", key)
            if keyed and keyed.group(1) != date_key:
                continue
            if isinstance(snapshot, dict):
                current[key] = snapshot
        return current

    async def _ensure_daily_plan(self, force: bool = False) -> dict[str, Any] | None:
        if not runtime_persona_setting(self, "enable_daily_plan", True) and not force:
            return None

        await self._ensure_daily_state(force=force)
        today = _today_key()
        async with self._data_lock:
            current_plan = self.data.setdefault("daily_plan", {})
            current_plan_date = _single_line(current_plan.get("date"), 16) if isinstance(current_plan, dict) else ""
            detail_day_changed = bool(
                current_plan_date
                and self._is_plan_date_active(current_plan_date)
                and self._sync_detail_enhancement_day_locked(current_plan_date)
            )
            known_users = [
                user for user in self.data.get("users", {}).values() if isinstance(user, dict) and user.get("umo")
            ]
            if not force and current_plan.get("date") == today:
                plan_changed = self._sanitize_daily_plan_inplace(current_plan)
                if plan_changed:
                    self._refresh_daily_state_location_from_plan(plan=current_plan)
                if plan_changed or detail_day_changed:
                    self._save_daily_state_sections(
                        sections={
                            "daily_plan",
                            "daily_state",
                            "detail_enhanced_day",
                            "detail_enhanced_segments",
                            "daily_story_plan",
                        }
                    )
                source = _single_line(current_plan.get("source"), 40).lower()
                retry_after = _safe_float(current_plan.get("retry_after"), 0.0)
                fallback_retry_due = source.startswith("fallback") and (
                    retry_after <= 0 or _now_ts() >= retry_after
                )
                if not fallback_retry_due:
                    return current_plan
            if (
                not force
                and current_plan.get("date") != today
                and self._is_plan_date_active(current_plan.get("date"))
            ):
                plan_changed = self._sanitize_daily_plan_inplace(current_plan)
                if plan_changed:
                    self._refresh_daily_state_location_from_plan(plan=current_plan)
                if plan_changed or detail_day_changed:
                    self._save_daily_state_sections(
                        sections={
                            "daily_plan",
                            "daily_state",
                            "detail_enhanced_day",
                            "detail_enhanced_segments",
                            "daily_story_plan",
                        }
                    )
                return current_plan
            active_persona = str(
                getattr(self, "_active_persona_scope", lambda: "")() or ""
            ).strip()
            primary_persona = str(
                getattr(self, "_primary_persona_id", lambda: "")() or ""
            ).strip()
            configured_empty_secondary = bool(
                getattr(self, "enable_multi_persona_mode", False)
                and active_persona
                and active_persona != primary_persona
                and callable(getattr(self, "_persona_config_exists", None))
                and self._persona_config_exists(active_persona)
            )
            if not force and not known_users and not configured_empty_secondary:
                return current_plan if current_plan.get("date") == today else None
            if not force and not self._is_daily_plan_due():
                if self._is_plan_date_active(current_plan.get("date")):
                    plan_changed = self._sanitize_daily_plan_inplace(current_plan)
                    if plan_changed:
                        self._refresh_daily_state_location_from_plan(plan=current_plan)
                    if plan_changed or detail_day_changed:
                        self._save_daily_state_sections(
                            sections={
                                "daily_plan",
                                "daily_state",
                                "detail_enhanced_day",
                                "detail_enhanced_segments",
                                "daily_story_plan",
                            }
                        )
                    return current_plan
                return None

        generation_lock = self._daily_generation_lock("_daily_plan_generation_lock")
        async with generation_lock:
            # The initial eligibility check intentionally happens before the
            # model call, but another caller may have generated the plan while
            # we were waiting. Re-check the shared plan inside the generation
            # lock so one day/persona only consumes one model request.
            if not force:
                async with self._data_lock:
                    current_plan = self.data.get("daily_plan")
                    if isinstance(current_plan, dict) and _single_line(current_plan.get("date"), 16) == today:
                        source = _single_line(current_plan.get("source"), 40).lower()
                        retry_after = _safe_float(current_plan.get("retry_after"), 0.0)
                        fallback_retry_due = source.startswith("fallback") and (
                            retry_after <= 0 or _now_ts() >= retry_after
                        )
                        if not fallback_retry_due:
                            return current_plan

            plan = await self._generate_daily_plan()
            async with self._data_lock:
                self.data["daily_plan"] = plan
                self._sync_detail_enhancement_day_locked(plan.get("date"), reset=True)
                self._refresh_daily_state_location_from_plan(plan=plan)
                self._save_daily_state_sections(
                    sections={
                        "daily_plan",
                        "daily_state",
                        "detail_enhanced_day",
                        "detail_enhanced_segments",
                        "daily_story_plan",
                    }
                )
        outfit_generator = getattr(self, "_ensure_daily_outfit_photo", None)
        if callable(outfit_generator):
            try:
                await outfit_generator()
            except Exception as exc:
                logger.warning(
                    "今日日程已保存,但每日穿搭照片生成失败: %s",
                    _single_line(exc, 180),
                )
        await self._ensure_daily_news_reading(force=force)
        return plan

    async def _ensure_detail_enhancement(self, force: bool = False) -> dict[str, Any] | None:
        if not runtime_persona_setting(self, "enable_detail_enhancement", False) and not force:
            return None
        async with self._data_lock:
            plan = dict(self.data.get("daily_plan", {}))
            plan_date = str(plan.get("date") or "")
            if not self._is_plan_date_active(plan_date):
                return None
            self._sync_detail_enhancement_day_locked(plan_date)
            state = dict(self.data.get("daily_state", {}))
            enhanced = self.data.setdefault("detail_enhanced_segments", {})
            if not isinstance(enhanced, dict):
                enhanced = {}
                self.data["detail_enhanced_segments"] = enhanced
            sanitized_existing = False
            if self._sanitize_detail_enhanced_segments_inplace(enhanced):
                sanitized_existing = True
            story_plan_existing = self.data.get("daily_story_plan", {})
            if isinstance(story_plan_existing, dict) and self._sanitize_story_plan_social_facts_inplace(story_plan_existing):
                sanitized_existing = True
            segments = self._collect_due_detail_segments(plan, enhanced, force=force)
            if not segments:
                if sanitized_existing:
                    self._save_data_sync(
                        sections={"detail_enhanced_segments", "daily_story_plan"}
                    )
                return None
            for segment in segments:
                generation_id = uuid.uuid4().hex
                segment["_generation_id"] = generation_id
                enhanced[segment["key"]] = {
                    "status": "generating",
                    "started_at": self._environment_now().strftime("%H:%M"),
                    "started_ts": _now_ts(),
                    "generation_id": generation_id,
                }
            self._save_data_sync(sections={"detail_enhanced_segments"})

        last_detail = None
        for segment in segments:
            try:
                detail = await self._generate_detail_enhancement(segment, plan, state)
                if not isinstance(detail.get("today_events"), list) or not detail.get("today_events"):
                    raise RuntimeError("日程细化结果为空或无法解析")
            except Exception as exc:
                now_ts = _now_ts()
                retry_after_ts = now_ts + 30 * 60
                failure_is_current = False
                async with self._data_lock:
                    failure_is_current = self._detail_generation_is_current(
                        segment,
                        str(segment.get("_generation_id") or ""),
                    )
                    if failure_is_current:
                        enhanced = self.data.setdefault("detail_enhanced_segments", {})
                        if not isinstance(enhanced, dict):
                            enhanced = {}
                            self.data["detail_enhanced_segments"] = enhanced
                        retry_after = self._environment_fromtimestamp(retry_after_ts).strftime("%H:%M")
                        enhanced[segment["key"]] = {
                            "status": "failed",
                            "updated_at": self._environment_now().strftime("%H:%M"),
                            "error": _single_line(exc, 180),
                            "retry_after": retry_after,
                            "retry_after_ts": retry_after_ts,
                            "summary": "这一段细化生成失败，稍后会自动重试。",
                            "today_events": [],
                            "proactive_events": [],
                            "state_variables": [],
                            "presence_status": {},
                            "interaction_updates": [],
                            "coverage_repair_done": bool(segment.get("_coverage_repair")),
                        }
                        self._save_data_sync(sections={"detail_enhanced_segments"})
                    else:
                        retry_after = ""
                if failure_is_current:
                    logger.warning(
                        "日程细化生成失败,已标记为可重试: segment=%s retry_after=%s error=%s",
                        _single_line(segment.get("key"), 80),
                        retry_after,
                        _single_line(exc, 180),
                    )
                else:
                    logger.info(
                        "日程细化失败结果已过期,不再回写: segment=%s error=%s",
                        _single_line(segment.get("key"), 80),
                        _single_line(exc, 180),
                    )
                if force and failure_is_current:
                    raise
                continue
            self._sanitize_detail_snapshot_for_segment_inplace(
                detail,
                segment,
                field=f"detail_enhanced_segments.{segment.get('key') or 'current'}",
            )
            async with self._data_lock:
                if not self._detail_generation_is_current(
                    segment,
                    str(segment.get("_generation_id") or ""),
                ):
                    continue
                story_plan = self.data.setdefault("daily_story_plan", {})
                if not isinstance(story_plan, dict) or story_plan.get("date") != plan_date:
                    story_plan = {
                        "date": plan_date,
                        "today_events": [],
                        "proactive_events": [],
                        "long_term_events": [],
                    }
                    self.data["daily_story_plan"] = story_plan
                self._merge_detail_enhancement(story_plan, detail)
                self._sanitize_story_plan_social_facts_inplace(story_plan)
                enhanced = self.data.setdefault("detail_enhanced_segments", {})
                enhanced[segment["key"]] = {
                    "status": "done",
                    "updated_at": self._environment_now().strftime("%H:%M"),
                    "summary": _single_line(detail.get("summary"), 120),
                    "summary_basis": self._normalize_schedule_basis(detail.get("summary_basis"), default=["coarse_plan"]),
                    "summary_confidence": min(1.0, _safe_float(detail.get("summary_confidence"), 0.75)),
                    "location": _single_line(detail.get("location"), 60),
                    "location_basis": self._normalize_schedule_basis(detail.get("location_basis"), default=["coarse_plan"]),
                    "location_confidence": min(1.0, _safe_float(detail.get("location_confidence"), 0.72)),
                    "today_events": detail.get("today_events", []),
                    "proactive_events": detail.get("proactive_events", []),
                    "state_variables": detail.get("state_variables", []),
                    "presence_status": detail.get("presence_status", {}),
                    "quality": detail.get("quality", {}),
                    "interaction_updates": [],
                    "coverage_repair_done": bool(segment.get("_coverage_repair")),
                }
                self._sanitize_detail_enhanced_segments_inplace(enhanced)
                meal_entries = self._append_self_meal_log(
                    self._collect_self_meal_events_from_detail(segment=segment, plan=plan, detail=detail),
                    segment=segment,
                    plan=plan,
                )
                self._remember_detail_enhancement_history(plan_date, enhanced, story_plan)
                self._refresh_daily_state_location_from_plan(
                    plan=plan,
                    detail=detail,
                    segment=segment,
                )
                self._reschedule_users_for_new_detail_events(segment)
                self._save_data_sync(
                    sections={
                        "daily_plan",
                        "daily_state",
                        "detail_enhanced_segments",
                        "detail_enhanced_history",
                        "daily_story_plan",
                        "daily_story_plan_history",
                        "users",
                        "self_meal_log",
                    }
                )
                last_detail = detail
            for meal_entry in meal_entries:
                await self._memory_companion_record_self_meal(meal_entry)
            if meal_entries:
                self._schedule_data_save(sections={"self_meal_log"})
            await self._apply_detail_presence_status(segment, detail)
        return last_detail

    def _collect_detail_segments(
        self,
        plan: dict[str, Any],
        enhanced: dict[str, Any],
        *,
        include_cancelled: bool = False,
    ) -> list[dict[str, Any]]:
        if not isinstance(plan, dict) or not self._is_plan_date_active(plan.get("date")):
            return []
        items = plan.get("items")
        if not isinstance(items, list) or not items:
            return []
        starts = self._normalized_plan_item_starts(items)
        parsed = []
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            start = starts[index] if index < len(starts) else None
            if start is None:
                continue
            parsed.append((index, start, item))
        if not parsed:
            return []
        segments: list[dict[str, Any]] = []
        for pos, (index, start, item) in enumerate(parsed):
            if not include_cancelled and self._normalize_schedule_lifecycle_status(item.get("lifecycle_status")) == "cancelled":
                continue
            key = f"{plan.get('date')}:{index}:{item.get('time')}"
            if self._detail_enhancement_snapshot_blocks_generation(enhanced.get(key) if isinstance(enhanced, dict) else None):
                continue
            next_start = (
                parsed[pos + 1][1]
                if pos + 1 < len(parsed)
                else None
            )
            end = self._plan_item_end_minutes(start, item, next_start=next_start)
            segments.append(
                {
                    "key": key,
                    "plan_date": str(plan.get("date") or ""),
                    "index": index,
                    "start": start,
                    "end": end,
                    "previous_item": next(
                        (
                            candidate[2]
                            for candidate in reversed(parsed[:pos])
                            if self._normalize_schedule_lifecycle_status(candidate[2].get("lifecycle_status")) != "cancelled"
                        ),
                        None,
                    ),
                    "item": item,
                    "next_item": next(
                        (
                            candidate[2]
                            for candidate in parsed[pos + 1 :]
                            if self._normalize_schedule_lifecycle_status(candidate[2].get("lifecycle_status")) != "cancelled"
                        ),
                        None,
                    ),
                }
            )
        return segments

    @staticmethod
    def _schedule_segment_selector_cn_number(value: Any) -> int | None:
        text = str(value or "").strip().replace("兩", "两").replace("〇", "零")
        if not text:
            return None
        if text.isdigit():
            return int(text)
        digits = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
        if text in digits:
            return digits[text]
        if "十" in text:
            left, _, right = text.partition("十")
            tens = digits.get(left, 1) if left else 1
            ones = digits.get(right, 0) if right else 0
            return tens * 10 + ones
        return None

    def _schedule_segment_selector_minutes(self, selector: str) -> list[int]:
        compact = re.sub(r"\s+", "", str(selector or ""))
        match = re.search(
            r"(凌晨|早上|早晨|上午|中午|下午|傍晚|晚上|今晚|夜里)?"
            r"(\d{1,2}|[零〇一二两兩三四五六七八九十]{1,3})"
            r"(?:[:：点點时時])"
            r"(\d{1,2}|半|一刻|三刻)?",
            compact,
        )
        if not match:
            return []
        period = str(match.group(1) or "")
        hour = self._schedule_segment_selector_cn_number(match.group(2))
        minute_text = str(match.group(3) or "")
        if hour is None:
            return []
        if minute_text == "半":
            minute = 30
        elif minute_text == "一刻":
            minute = 15
        elif minute_text == "三刻":
            minute = 45
        else:
            minute = _safe_int(minute_text, 0, 0, 59)
        if hour > 23:
            return []
        if period in {"凌晨"} and hour == 12:
            hour = 0
        elif period in {"中午", "下午", "傍晚", "晚上", "今晚", "夜里"}:
            if hour == 12:
                hour = 12 if period == "中午" else 0
            elif hour < 12:
                hour += 12
        elif period in {"早上", "早晨", "上午"} and hour == 12:
            hour = 0
        primary = hour * 60 + minute
        if period or hour == 0 or hour > 12:
            return [primary]
        alternate = primary + 12 * 60
        return [primary, alternate] if alternate < 24 * 60 else [primary]

    @staticmethod
    def _schedule_segment_selector_text(value: Any) -> str:
        text = unicodedata.normalize("NFKC", _single_line(value, 160)).lower()
        text = re.sub(
            r"(?:今天|今日|今儿|当天|这一段|这个|那一段|那个|时段|时间段|日程|安排|计划|细化|活动|任务)",
            "",
            text,
        )
        text = re.sub(
            r"(?:凌晨|早上|早晨|上午|中午|下午|傍晚|晚上|今晚|夜里)?"
            r"(?:\d{1,2}|[零〇一二两兩三四五六七八九十]{1,3})"
            r"(?:[:：点點时時])(?:\d{1,2}|半|一刻|三刻)?",
            "",
            text,
        )
        text = re.sub(r"[\s\-_—:：，,。.!！?？'\"“”‘’（）()【】\[\]]+", "", text)
        return text

    def _schedule_segment_label(self, segment: dict[str, Any]) -> str:
        item = segment.get("item") if isinstance(segment.get("item"), dict) else {}
        start = self._minutes_to_hhmm(_safe_int(segment.get("start"), 0))
        end = self._minutes_to_hhmm(_safe_int(segment.get("end"), 0))
        activity = _single_line(item.get("activity"), 80) or "未命名日程"
        status = self._normalize_schedule_lifecycle_status(item.get("lifecycle_status"))
        suffix = "（已取消）" if status == "cancelled" else ""
        return f"{start}-{end} {activity}{suffix}"

    def _resolve_daily_plan_segment_selector(
        self,
        selector: Any,
        *,
        plan: dict[str, Any] | None = None,
        include_cancelled: bool = True,
    ) -> tuple[dict[str, Any] | None, str]:
        current_plan = plan if isinstance(plan, dict) else self.data.get("daily_plan", {})
        if not isinstance(current_plan, dict) or not current_plan.get("items"):
            return None, "今天还没有可操作的日程。"
        segments = self._collect_detail_segments(current_plan, {}, include_cancelled=include_cancelled)
        if not segments:
            return None, "今天还没有可操作的日程段。"
        raw = _single_line(selector, 160)
        if not raw:
            return None, "请指定时间或活动，例如“陪伴 删除日程 15:00”或“陪伴 重置日程 整理房间”。"

        exact_key = next((segment for segment in segments if _single_line(segment.get("key"), 120) == raw), None)
        if exact_key:
            return exact_key, ""
        compact = re.sub(r"\s+", "", raw.lower())
        if compact in {"当前", "现在", "此刻", "正在进行", "当前段", "这一段", "这段"}:
            current = self._current_detail_segment_for_update()
            if current:
                return current, ""
            return None, "当前没有正在进行或即将开始的日程段。"

        ordinal_match = re.search(
            r"(?:第\s*(\d{1,2}|[一二两三四五六七八九十]{1,3})\s*(?:个|项|段)?|"
            r"(\d{1,2}|[一二两三四五六七八九十]{1,3})\s*(?:个|项|段))",
            raw,
        )
        if ordinal_match:
            ordinal = self._schedule_segment_selector_cn_number(ordinal_match.group(1) or ordinal_match.group(2))
            if ordinal is not None and 1 <= ordinal <= len(segments):
                return segments[ordinal - 1], ""

        requested_minutes = self._schedule_segment_selector_minutes(raw)
        time_matches: list[dict[str, Any]] = []
        if requested_minutes:
            exact_starts = [
                segment
                for segment in segments
                if any(_safe_int(segment.get("start"), -1) % (24 * 60) == minute for minute in requested_minutes)
            ]
            if exact_starts:
                time_matches = exact_starts
            else:
                time_matches = [
                    segment
                    for segment in segments
                    if any(
                        _safe_int(segment.get("start"), 0) <= minute < _safe_int(segment.get("end"), 0)
                        or _safe_int(segment.get("start"), 0) <= minute + 24 * 60 < _safe_int(segment.get("end"), 0)
                        for minute in requested_minutes
                    )
                ]

        activity_query = self._schedule_segment_selector_text(raw)
        activity_matches: list[dict[str, Any]] = []
        if activity_query:
            for segment in segments:
                item = segment.get("item") if isinstance(segment.get("item"), dict) else {}
                activity = self._schedule_segment_selector_text(item.get("activity"))
                if activity_query == activity or activity_query in activity or activity in activity_query:
                    activity_matches.append(segment)

        matches = time_matches
        if activity_matches:
            intersection = [segment for segment in time_matches if segment in activity_matches]
            matches = intersection or activity_matches if time_matches else activity_matches
        if len(matches) == 1:
            return matches[0], ""
        if len(matches) > 1:
            choices = "；".join(self._schedule_segment_label(segment) for segment in matches[:5])
            return None, f"匹配到多段日程，请再具体一点：{choices}"

        choices = "；".join(self._schedule_segment_label(segment) for segment in segments[:8])
        return None, f"没有找到“{raw}”对应的日程。今天可选：{choices}"

    async def _cancel_daily_plan_segment_by_selector(self, selector: Any, *, reason: str = "用户通过聊天命令取消该日程段") -> tuple[bool, str]:
        async with self._data_lock:
            segment, error = self._resolve_daily_plan_segment_selector(selector, include_cancelled=True)
            if not segment:
                return False, error
            key = _single_line(segment.get("key"), 120)
            plan = self.data.get("daily_plan", {})
            items = plan.get("items") if isinstance(plan, dict) else None
            index = _safe_int(segment.get("index"), -1, minimum=-1)
            item = items[index] if isinstance(items, list) and 0 <= index < len(items) and isinstance(items[index], dict) else None
            if not isinstance(item, dict):
                return False, "该日程段已经不存在。"
            if self._normalize_schedule_lifecycle_status(item.get("lifecycle_status")) == "cancelled":
                return True, f"这段日程之前已经取消：{self._schedule_segment_label(segment)}"
            label = self._schedule_segment_label(segment)
            item["lifecycle_status"] = "cancelled"
            item["changed_at"] = self._environment_now().strftime("%H:%M")
            item["change_reason"] = _single_line(reason, 120)
            item.pop("_detail_generation_id", None)
            self._sync_detail_enhancement_day_locked(plan.get("date"))
            enhanced = self.data.setdefault("detail_enhanced_segments", {})
            if not isinstance(enhanced, dict):
                enhanced = {}
                self.data["detail_enhanced_segments"] = enhanced
            cancelled = deepcopy(enhanced.get(key)) if isinstance(enhanced.get(key), dict) else {
                "status": "done",
                "summary": "这一段已取消。",
                "today_events": [],
                "proactive_events": [],
                "state_variables": [],
            }
            for event in list(cancelled.get("today_events") or []) + list(cancelled.get("proactive_events") or []):
                if isinstance(event, dict):
                    event["lifecycle_status"] = "cancelled"
            cancelled["status"] = "cancelled"
            cancelled["summary"] = _single_line(cancelled.get("summary"), 120) or "这一段已取消。"
            cancelled["cancelled_at"] = self._environment_now().strftime("%Y-%m-%d %H:%M:%S")
            for field in ("generation_id", "previous_item_state", "retry_after", "retry_after_ts"):
                cancelled.pop(field, None)
            enhanced[key] = cancelled
            story = self._rebuild_story_plan_from_detail_snapshots(str(plan.get("date") or _today_key()))
            self._remember_detail_enhancement_history(str(plan.get("date") or _today_key()), enhanced, story)
            self._save_data_sync(
                sections={
                    "daily_plan",
                    "detail_enhanced_day",
                    "detail_enhanced_segments",
                    "detail_enhanced_history",
                    "daily_story_plan",
                    "daily_story_plan_history",
                }
            )
            return True, f"已取消：{label}"

    async def _regenerate_daily_plan_segment_by_selector(
        self,
        selector: Any,
        generator: Any,
        *,
        reason: str = "用户通过聊天命令重新细化该日程段",
    ) -> tuple[bool, str, dict[str, Any]]:
        if not callable(generator):
            return False, "当前没有可用的日程细化生成器。", {}
        previous_snapshot: dict[str, Any] = {}
        previous_item_state: dict[str, tuple[bool, Any]] = {}
        generation_id = ""
        key = ""
        segment: dict[str, Any] = {}
        plan: dict[str, Any] = {}
        try:
            async with self._data_lock:
                live_plan = self.data.get("daily_plan", {})
                segment, error = self._resolve_daily_plan_segment_selector(
                    selector,
                    plan=live_plan if isinstance(live_plan, dict) else {},
                    include_cancelled=True,
                )
                if not segment:
                    return False, error, {}
                key = _single_line(segment.get("key"), 120)
                plan = deepcopy(live_plan)
                segment = next(
                    (
                        candidate
                        for candidate in self._collect_detail_segments(plan, {}, include_cancelled=True)
                        if _single_line(candidate.get("key"), 120) == key
                    ),
                    segment,
                )
                state = deepcopy(self.data.get("daily_state", {}))
                items = live_plan.get("items") if isinstance(live_plan, dict) else None
                index = _safe_int(segment.get("index"), -1, minimum=-1)
                live_item = items[index] if isinstance(items, list) and 0 <= index < len(items) and isinstance(items[index], dict) else None
                if not isinstance(live_item, dict):
                    return False, "该日程段已经不存在。", {}
                self._sync_detail_enhancement_day_locked(plan.get("date"))
                enhanced = self.data.setdefault("detail_enhanced_segments", {})
                if not isinstance(enhanced, dict):
                    enhanced = {}
                    self.data["detail_enhanced_segments"] = enhanced
                previous_snapshot = deepcopy(enhanced.get(key)) if isinstance(enhanced.get(key), dict) else {}
                if (
                    _single_line(previous_snapshot.get("status"), 24) == "generating"
                    and self._detail_enhancement_snapshot_blocks_generation(previous_snapshot)
                ):
                    return False, "该时间段正在细化中，请等待当前生成完成后再试。", {}
                for field in ("lifecycle_status", "changed_at", "change_reason", "_detail_generation_id"):
                    previous_item_state[field] = (field in live_item, deepcopy(live_item.get(field)))
                generation_id = uuid.uuid4().hex
                live_item["lifecycle_status"] = "changed"
                live_item["changed_at"] = self._environment_now().strftime("%H:%M")
                live_item["change_reason"] = _single_line(reason, 120)
                live_item["_detail_generation_id"] = generation_id
                segment_item = segment.get("item") if isinstance(segment.get("item"), dict) else None
                if isinstance(segment_item, dict):
                    segment_item["lifecycle_status"] = "changed"
                enhanced[key] = {
                    "status": "generating",
                    "started_at": self._environment_now().strftime("%H:%M"),
                    "started_ts": time.time(),
                    "regenerated": True,
                    "generation_id": generation_id,
                }
                self._save_data_sync(
                    sections={"daily_plan", "detail_enhanced_day", "detail_enhanced_segments"}
                )

            detail = await generator(self, segment, plan, state)
            if not isinstance(detail, dict) or not isinstance(detail.get("today_events"), list) or not detail.get("today_events"):
                raise RuntimeError("局部重生成未返回可用的细化事件")

            async with self._data_lock:
                if not self._detail_generation_is_current(segment, generation_id):
                    return False, "该时间段已被取消、替换或由更新的操作接管，本次迟到结果未写入。", {}
                enhanced = self.data.setdefault("detail_enhanced_segments", {})
                enhanced[key] = {
                    "status": "done",
                    "updated_at": self._environment_now().strftime("%H:%M"),
                    "summary": _single_line(detail.get("summary"), 120),
                    "summary_basis": self._normalize_schedule_basis(detail.get("summary_basis"), default=["coarse_plan"]),
                    "summary_confidence": min(1.0, _safe_float(detail.get("summary_confidence"), 0.75)),
                    "location": _single_line(detail.get("location"), 60),
                    "location_basis": self._normalize_schedule_basis(detail.get("location_basis"), default=["coarse_plan"]),
                    "location_confidence": min(1.0, _safe_float(detail.get("location_confidence"), 0.72)),
                    "today_events": detail.get("today_events", []),
                    "proactive_events": detail.get("proactive_events", []),
                    "state_variables": detail.get("state_variables", []),
                    "presence_status": detail.get("presence_status", {}),
                    "quality": detail.get("quality", {}),
                    "interaction_updates": previous_snapshot.get("interaction_updates", []),
                    "regenerated": True,
                    "regenerated_at": self._environment_now().strftime("%Y-%m-%d %H:%M:%S"),
                }
                self._sanitize_detail_enhanced_segments_inplace(enhanced)
                story = self._rebuild_story_plan_from_detail_snapshots(str(plan.get("date") or _today_key()))
                self._remember_detail_enhancement_history(str(plan.get("date") or _today_key()), enhanced, story)
                current_plan = self.data.get("daily_plan", {})
                current_items = current_plan.get("items") if isinstance(current_plan, dict) else None
                index = _safe_int(segment.get("index"), -1, minimum=-1)
                current_item = current_items[index] if isinstance(current_items, list) and 0 <= index < len(current_items) and isinstance(current_items[index], dict) else None
                if isinstance(current_item, dict) and _single_line(current_item.get("_detail_generation_id"), 64) == generation_id:
                    current_item.pop("_detail_generation_id", None)
                self._refresh_daily_state_location_from_plan(
                    plan=current_plan if isinstance(current_plan, dict) else plan,
                    detail=detail,
                    segment=segment,
                )
                self._save_data_sync(
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
                label = self._schedule_segment_label(segment)
            return True, f"已重新细化：{label}", detail
        except Exception as exc:
            logger.warning("聊天命令局部重生成日程细化失败: %s", exc, exc_info=True)
            async with self._data_lock:
                enhanced = self.data.setdefault("detail_enhanced_segments", {})
                if isinstance(enhanced, dict) and key and generation_id and self._detail_generation_is_current(segment, generation_id):
                    restored = previous_snapshot or {
                        "status": "failed",
                        "today_events": [],
                        "proactive_events": [],
                        "state_variables": [],
                    }
                    restored["regeneration_error"] = _single_line(exc, 180)
                    restored["regeneration_failed_at"] = self._environment_now().strftime("%Y-%m-%d %H:%M:%S")
                    enhanced[key] = restored
                    live_plan = self.data.get("daily_plan", {})
                    live_items = live_plan.get("items") if isinstance(live_plan, dict) else None
                    index = _safe_int(segment.get("index"), -1, minimum=-1)
                    live_item = live_items[index] if isinstance(live_items, list) and 0 <= index < len(live_items) and isinstance(live_items[index], dict) else None
                    if isinstance(live_item, dict) and _single_line(live_item.get("_detail_generation_id"), 64) == generation_id:
                        for field, (existed, value) in previous_item_state.items():
                            if existed:
                                live_item[field] = value
                            else:
                                live_item.pop(field, None)
                    self._save_data_sync(
                        sections={"daily_plan", "detail_enhanced_segments"}
                    )
            return False, _single_line(exc, 180) or "局部重生成失败。", {}

    def _detail_generation_is_current(self, segment: dict[str, Any], generation_id: str) -> bool:
        key = _single_line(segment.get("key"), 120)
        if not key or not generation_id:
            return False
        enhanced = self.data.get("detail_enhanced_segments", {})
        snapshot = enhanced.get(key) if isinstance(enhanced, dict) else None
        if not isinstance(snapshot, dict):
            return False
        if _single_line(snapshot.get("status"), 24) != "generating":
            return False
        if _single_line(snapshot.get("generation_id"), 64) != generation_id:
            return False
        live_plan = self.data.get("daily_plan", {})
        plan_date = _single_line(segment.get("plan_date"), 16)
        if not isinstance(live_plan, dict) or _single_line(live_plan.get("date"), 16) != plan_date:
            return False
        items = live_plan.get("items")
        index = _safe_int(segment.get("index"), -1, minimum=-1)
        if not isinstance(items, list) or not (0 <= index < len(items)) or not isinstance(items[index], dict):
            return False
        item = items[index]
        expected_key = f"{plan_date}:{index}:{item.get('time')}"
        if expected_key != key:
            return False
        return self._normalize_schedule_lifecycle_status(item.get("lifecycle_status")) != "cancelled"

    def _detail_enhancement_snapshot_blocks_generation(self, snapshot: Any) -> bool:
        if not isinstance(snapshot, dict):
            return False
        status = _single_line(snapshot.get("status"), 24)
        if status in {"done", "cancelled"}:
            return True
        if status == "failed":
            retry_after_ts = _safe_float(snapshot.get("retry_after_ts"), 0)
            return retry_after_ts > _now_ts()
        if status == "generating":
            started_ts = _safe_float(snapshot.get("started_ts"), 0)
            if started_ts > 0:
                return _now_ts() - started_ts < 30 * 60
            started_at = _single_line(snapshot.get("started_at"), 8)
            started_minutes = self._parse_hhmm_to_minutes(started_at)
            if started_minutes is None:
                return False
            elapsed_minutes = self._environment_now_minutes() - started_minutes
            if elapsed_minutes < 0:
                elapsed_minutes += 24 * 60
            return elapsed_minutes < 30
        if status:
            return False
        return bool(snapshot.get("summary") or snapshot.get("today_events") or snapshot.get("proactive_events"))

    def _collect_due_detail_segments(
        self,
        plan: dict[str, Any],
        enhanced: dict[str, Any],
        *,
        force: bool = False,
    ) -> list[dict[str, Any]]:
        segments = self._collect_detail_segments(plan, enhanced if isinstance(enhanced, dict) else {})
        if not segments:
            return []
        if force:
            picked = self._current_detail_segment_for_update() or self._pick_detail_segment(plan, {})
            return [picked] if isinstance(picked, dict) else segments[:1]
        due = [segment for segment in segments if self._detail_segment_is_due(segment)]
        if due:
            return due[:1]

        story_plan = self.data.get("daily_story_plan", {})
        if not isinstance(story_plan, dict):
            story_plan = {}
        repaired: list[dict[str, Any]] = []
        all_segments = self._collect_detail_segments(plan, {})
        for segment in all_segments:
            if not self._detail_segment_is_due(segment):
                continue
            key = str(segment.get("key") or "")
            status = enhanced.get(key) if isinstance(enhanced, dict) else None
            if not isinstance(status, dict) or status.get("status") != "done":
                continue
            if status.get("coverage_repair_done"):
                continue
            if self._detail_segment_has_story_coverage(segment, story_plan):
                continue
            repaired_segment = dict(segment)
            repaired_segment["_coverage_repair"] = True
            repaired.append(repaired_segment)
        return repaired[:1]

    def _detail_segment_is_due(self, segment: dict[str, Any]) -> bool:
        if not isinstance(segment, dict):
            return False
        plan_date = str(self.data.get("daily_plan", {}).get("date") or "")
        now_minutes = self._effective_plan_now_minutes(plan_date)
        if now_minutes is None:
            return False
        start = _safe_int(segment.get("start"), 0)
        end = _safe_int(segment.get("end"), self._segment_end_minutes(start, segment.get("item")))
        lead = max(0, _safe_int(runtime_persona_setting(self, "detail_enhancement_lead_minutes", 3), 3, 0))
        return start - lead <= now_minutes < end

    def _detail_segment_has_story_coverage(
        self,
        segment: dict[str, Any],
        story_plan: dict[str, Any],
    ) -> bool:
        if not isinstance(segment, dict) or not isinstance(story_plan, dict):
            return False
        start = _safe_int(segment.get("start"), 0)
        end = _safe_int(segment.get("end"), self._segment_end_minutes(start, segment.get("item")))
        for key in ("today_events", "proactive_events"):
            raw_items = story_plan.get(key, [])
            if not isinstance(raw_items, list):
                continue
            for item in raw_items:
                if not isinstance(item, dict):
                    continue
                item_start, item_end = self._parse_window_minutes(str(item.get("window") or ""))
                if item_start is None or item_end is None:
                    continue
                if item_end < item_start:
                    item_end += 24 * 60
                if item_start < end and item_end > start:
                    return True
        return False

    def _pick_detail_segment(
        self, plan: dict[str, Any], enhanced: dict[str, Any]
    ) -> dict[str, Any] | None:
        return pick_detail_segment(self, plan, enhanced)

    async def _generate_detail_enhancement(
        self,
        segment: dict[str, Any],
        plan: dict[str, Any],
        state: dict[str, Any],
    ) -> dict[str, Any]:
        return await generate_detail_enhancement(self, segment, plan, state)

    def _merge_detail_enhancement(
        self, story_plan: dict[str, Any], detail: dict[str, Any]
    ) -> None:
        for key, limit in (
            ("today_events", 16),
            ("proactive_events", 12),
            ("long_term_events", 6),
        ):
            existing = story_plan.setdefault(key, [])
            if not isinstance(existing, list):
                existing = []
                story_plan[key] = existing
            additions = detail.get(key, [])
            if isinstance(additions, list):
                existing.extend(
                    item
                    for item in additions
                    if isinstance(item, dict)
                    and self._normalize_schedule_lifecycle_status(item.get("lifecycle_status")) != "cancelled"
                )
                story_plan[key] = self._trim_story_plan_items(key, existing, limit)

    def _rebuild_story_plan_from_detail_snapshots(self, plan_date: str) -> dict[str, Any]:
        rebuilt: dict[str, Any] = {
            "date": _single_line(plan_date, 16),
            "today_events": [],
            "proactive_events": [],
            "long_term_events": [],
        }
        enhanced = self._detail_enhanced_segments_for_plan_date(plan_date)
        for snapshot in enhanced.values():
            if snapshot.get("status") != "done":
                continue
            self._merge_detail_enhancement(rebuilt, snapshot)
        self._sanitize_story_plan_social_facts_inplace(rebuilt)
        self.data["daily_story_plan"] = rebuilt
        return rebuilt

    def _remember_detail_enhancement_history(
        self,
        date_text: str,
        enhanced: dict[str, Any],
        story_plan: dict[str, Any],
    ) -> None:
        date_key = _single_line(date_text, 16)
        if not date_key:
            return
        history = self.data.setdefault("detail_enhanced_history", [])
        if not isinstance(history, list):
            history = []
            self.data["detail_enhanced_history"] = history
        history[:] = [
            old
            for old in history
            if not (isinstance(old, dict) and _single_line(old.get("date"), 16) == date_key)
        ]
        history.append(
            {
                "date": date_key,
                "updated_at": self._environment_now().strftime("%Y-%m-%d %H:%M"),
                "segments": dict(enhanced or {}),
            }
        )
        del history[:-14]

        story_history = self.data.setdefault("daily_story_plan_history", [])
        if not isinstance(story_history, list):
            story_history = []
            self.data["daily_story_plan_history"] = story_history
        story_history[:] = [
            old
            for old in story_history
            if not (isinstance(old, dict) and _single_line(old.get("date"), 16) == date_key)
        ]
        compact_story = dict(story_plan or {})
        compact_story["date"] = date_key
        story_history.append(compact_story)
        del story_history[:-14]

    def _trim_story_plan_items(
        self,
        key: str,
        items: list[dict[str, Any]],
        limit: int,
    ) -> list[dict[str, Any]]:
        normalized = [item for item in items if isinstance(item, dict)]
        if not normalized:
            return []
        seen: set[tuple[Any, ...]] = set()
        deduped: list[dict[str, Any]] = []
        for item in normalized:
            identity = self._story_plan_item_identity(key, item)
            if identity in seen:
                continue
            seen.add(identity)
            deduped.append(item)
        if key == "long_term_events":
            return deduped[-limit:]
        ordered = sorted(deduped, key=self._story_plan_item_sort_key)
        if len(ordered) <= limit:
            return ordered
        return self._pick_story_items_with_coverage(ordered, limit)

    def _story_plan_item_identity(self, key: str, item: dict[str, Any]) -> tuple[Any, ...]:
        if key == "today_events":
            return (
                _single_line(item.get("window"), 20),
                _single_line(item.get("event"), 80),
            )
        if key == "proactive_events":
            return (
                _single_line(item.get("window"), 20),
                _single_line(item.get("reason"), 40),
                _single_line(item.get("action"), 40),
                _single_line(item.get("topic"), 80),
            )
        return (
            _single_line(item.get("title"), 80),
            _single_line(item.get("status"), 80),
        )

    def _story_plan_item_sort_key(self, item: dict[str, Any]) -> tuple[int, int, str]:
        start, end = self._parse_window_minutes(str(item.get("window") or ""))
        start_value = start if start is not None else 99_999
        end_value = end if end is not None else start_value
        if end_value < start_value:
            end_value += 24 * 60
        text = _single_line(
            item.get("event") or item.get("topic") or item.get("title"),
            80,
        )
        return (start_value, end_value, text)

    def _pick_story_items_with_coverage(
        self,
        ordered: list[dict[str, Any]],
        limit: int,
    ) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        total = len(ordered)
        if total <= limit:
            return ordered
        now_minutes = self._environment_now_minutes()
        selected: set[int] = {0, total - 1}
        closest_index = min(
            range(total),
            key=lambda idx: self._story_item_time_distance(ordered[idx], now_minutes),
        )
        for idx in range(max(0, closest_index - 2), min(total, closest_index + 3)):
            selected.add(idx)
        if limit == 1:
            selected = {closest_index}
        else:
            for slot in range(limit):
                selected.add(round(slot * (total - 1) / max(1, limit - 1)))
        if len(selected) < limit:
            for idx in range(total):
                selected.add(idx)
                if len(selected) >= limit:
                    break
        return [ordered[idx] for idx in sorted(selected)[:limit]]

    def _story_item_time_distance(self, item: dict[str, Any], now_minutes: int) -> int:
        start, end = self._parse_window_minutes(str(item.get("window") or ""))
        if start is None or end is None:
            return 99_999
        if end < start:
            end += 24 * 60
        current = now_minutes
        if current < start and end > 24 * 60:
            current += 24 * 60
        if start <= current < end:
            return 0
        return min(abs(current - start), abs(current - end))

    def _normalize_story_plan(self, payload: dict[str, Any]) -> dict[str, Any]:
        return normalize_story_plan(self, payload)

    def _normalize_story_items(self, raw_items: Any, text_key: str) -> list[dict[str, Any]]:
        return normalize_story_items(self, raw_items, text_key)

    async def _ensure_daily_state(
        self,
        force: bool = False,
        *,
        skip_conversation_summary: bool = False,
        passive_fast: bool = False,
    ) -> dict[str, Any]:
        request_started = time.monotonic()
        scope = self._daily_generation_scope()
        force_cache = self._daily_force_result_cache("_daily_state_force_results_by_scope")
        lock = self._daily_generation_lock("_daily_state_generation_lock")
        async with lock:
            completed_entry = force_cache.get(scope, {})
            if force and _safe_float(completed_entry.get("completed_at"), 0) >= request_started:
                completed = completed_entry.get("result")
                if isinstance(completed, dict):
                    return completed
            state = await self._ensure_daily_state_once(
                force=force,
                skip_conversation_summary=skip_conversation_summary,
                passive_fast=passive_fast,
            )
            if force and isinstance(state, dict):
                force_cache[scope] = {
                    "result": state,
                    "completed_at": time.monotonic(),
                }
            return state

    async def _ensure_daily_state_once(
        self,
        force: bool = False,
        *,
        skip_conversation_summary: bool = False,
        passive_fast: bool = False,
    ) -> dict[str, Any]:
        today = _today_key()
        if passive_fast and not force:
            cached_state = self.data.get("daily_state", {})
            if isinstance(cached_state, dict) and cached_state.get("date") == today:
                cached_weather = self.data.get("daily_weather", {})
                weather = cached_weather if isinstance(cached_weather, dict) and cached_weather.get("date") == today else {
                    "date": today,
                    "prompt": "暂无天气信息",
                    "source": "passive_fast",
                }
                if not runtime_persona_setting(self, "enable_humanized_states", True):
                    state = dict(DEFAULT_HUMANIZED_STATE)
                    state.update(self._base_state_values())
                    state["date"] = today
                    state["weather"] = self._weather_summary_text(weather)
                    return state
                async with self._data_lock:
                    deleted_sections = set(self._cleanup_expired_conditions() or ())
                    self._ensure_time_based_hunger_condition()
                    state = self._compose_state_from_conditions(weather)
                    existing_state = self.data.get("daily_state")
                    if (not isinstance(existing_state, dict) or existing_state != state) or deleted_sections:
                        self.data["daily_state"] = state
                        save_sections = {
                            "daily_state",
                            "state_conditions",
                            "body_cycle_state",
                        } - set(deleted_sections)
                        if "body_cycle_state" in self.data:
                            deleted_sections.discard("body_cycle_state")
                            save_sections.add("body_cycle_state")
                        else:
                            save_sections.discard("body_cycle_state")
                        self._save_data_sync(
                            sections=save_sections,
                            deleted_sections=deleted_sections,
                        )
                    return state
            cached_weather = self.data.get("daily_weather", {})
            weather = cached_weather if isinstance(cached_weather, dict) and cached_weather.get("date") == today else {
                "date": today,
                "prompt": "暂无天气信息",
                "source": "passive_fast",
            }
            if not runtime_persona_setting(self, "enable_humanized_states", True):
                state = dict(DEFAULT_HUMANIZED_STATE)
                state.update(self._base_state_values())
                state["date"] = today
                state["weather"] = self._weather_summary_text(weather)
                return state
            return self._compose_state_from_conditions(weather)
        weather = await self._ensure_weather_context(force=force)
        await self._ensure_yesterday_screen_diary_context(force=force)
        if not skip_conversation_summary:
            await self._ensure_yesterday_conversation_summary(force=force)
        async with self._data_lock:
            if not runtime_persona_setting(self, "enable_humanized_states", True) and not force:
                state = dict(DEFAULT_HUMANIZED_STATE)
                state.update(self._base_state_values())
                state["date"] = today
                state["weather"] = self._weather_summary_text(weather)
                self.data["daily_state"] = state
                self._save_data_sync(sections={"daily_state"})
                return state

            needs_generation = force or self.data.get("state_generated_day") != today
            if not needs_generation:
                deleted_sections = set(self._cleanup_expired_conditions() or ())
                self._ensure_time_based_hunger_condition()
                state = self._compose_state_from_conditions(weather)
                self.data["daily_state"] = state
                save_sections = {
                    "daily_state",
                    "state_conditions",
                    "body_cycle_state",
                    "hunger_window_attempts",
                } - set(deleted_sections)
                if "body_cycle_state" in self.data:
                    deleted_sections.discard("body_cycle_state")
                    save_sections.add("body_cycle_state")
                else:
                    save_sections.discard("body_cycle_state")
                self._save_data_sync(
                    sections=save_sections,
                    deleted_sections=deleted_sections,
                )
                return state

        generation_day = _today_key()
        deferred_updates: dict[str, Any] = {}
        generated_conditions = await self._generate_state_conditions(
            weather,
            deferred_state_updates=deferred_updates,
        )

        async with self._data_lock:
            deleted_sections: set[str] = set()
            if not force and self.data.get("state_generated_day") == generation_day:
                deleted_sections = set(self._cleanup_expired_conditions() or ())
            else:
                deleted_sections = set(self._cleanup_expired_conditions() or ())
                if force:
                    self.data["state_conditions"] = []
                dream_pick = deferred_updates.get("dream_pick")
                if isinstance(dream_pick, tuple):
                    self._remember_daily_dream_pick(dream_pick)
                discomfort_roll_date = deferred_updates.get("cycle_discomfort_roll_date")
                if discomfort_roll_date:
                    cycle_meta = self.data.get("body_cycle_state")
                    cycle_meta = dict(cycle_meta) if isinstance(cycle_meta, dict) else {}
                    cycle_meta["last_discomfort_roll_date"] = discomfort_roll_date
                    self.data["body_cycle_state"] = cycle_meta
                body_cycle_conditions = deferred_updates.get("body_cycle_conditions", [])
                if isinstance(body_cycle_conditions, list):
                    for condition in body_cycle_conditions:
                        if isinstance(condition, dict):
                            self._record_body_cycle_episode(condition)
                conditions = self.data.setdefault("state_conditions", [])
                if not isinstance(conditions, list):
                    conditions = []
                    self.data["state_conditions"] = conditions
                conditions.extend(generated_conditions)
                self.data["state_generated_day"] = generation_day
            self._ensure_time_based_hunger_condition()
            state = self._compose_state_from_conditions(weather)
            self.data["daily_state"] = state
            save_sections = {
                "daily_state",
                "state_conditions",
                "state_generated_day",
                "daily_dream",
                "body_cycle_state",
                "hunger_window_attempts",
            } - set(deleted_sections)
            if "body_cycle_state" in self.data:
                deleted_sections.discard("body_cycle_state")
                save_sections.add("body_cycle_state")
            else:
                save_sections.discard("body_cycle_state")
            self._save_data_sync(
                sections=save_sections,
                deleted_sections=deleted_sections,
            )
            return state

    async def _generate_state_conditions(
        self,
        weather: dict[str, Any] | None = None,
        *,
        deferred_state_updates: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        intensity = _safe_float(runtime_persona_setting(self, "humanized_state_intensity", 50), 50, 0, 100) / 100
        persona_profile = self._persona_state_profile()
        now_dt = self._environment_now()
        current_minute = now_dt.hour * 60 + now_dt.minute

        sleep_pool = [
            ("睡得很踏实", "平稳", 0, 8),
            ("昨晚睡得很浅,半夜醒了好几次", "迟钝", -16, 10),
            ("失眠了,翻来覆去很久才睡着", "敏感", -24, 14),
            ("一晚上都在做梦,醒过来却记不清", "恍惚", -18, 12),
            ("赖床赖得有点久,懵懵的", "迷糊", -14, 8),
            ("闹钟没叫醒我,起来还有点懵", "慌乱", -17, 7),
        ]
        dream_pool = [
            ("没有记住梦", "平稳", 0, 2),
            ("梦里一直在找一件放错地方的小东西,醒来还残着一点没找完的感觉", "恍惚", -6, 5),
            ("梦见走过一段很安静的路,路灯和风声都很近", "柔和", 4, 4),
            ("梦里反复听见一句没听清的话,醒来后胸口还有点闷", "低落", -10, 7),
        ]
        hunger_pool = [
            ("无饥饿感", "平稳", 0, 3),
            ("饿,想吃东西", "粘人", -4, 2),
            ("胃口不好", "低落", -8, 3),
            ("想吃甜的", "柔软", 1, 2),
        ]
        cycle_pool = [
            ("不处于生理期", "平稳", 0, 24),
            ("生理期前,身体感受更敏锐,耐受度稍低", "敏感", -18, 24),
            ("处于生理期,身体舒适度与能量偏低", "疲惫", -24, 72),
        ]

        def pick(pool: list[tuple[str, str, int, int]], special_chance: float = 0.35) -> tuple[str, str, int, int]:
            if random.random() > special_chance * max(0.2, intensity):
                return pool[0]
            return random.choice(pool[1:])

        sleep_pick = pick(sleep_pool, 0.42)
        enhanced_dream = None
        if bool(runtime_persona_setting(self, "enable_enhanced_dreams", False)):
            enhanced_dream = await self._generate_enhanced_dream_pick(weather)
        dream_pick = enhanced_dream or pick(dream_pool, 0.55)
        if deferred_state_updates is None:
            self._remember_daily_dream_pick(dream_pick)
        else:
            deferred_state_updates["dream_pick"] = dream_pick
        hunger_pick = pick(hunger_pool, 0.22)
        specs = [
            ("sleep", "睡眠", *sleep_pick),
            ("dream", "梦境", *dream_pick),
        ]
        if persona_profile.get("allow_hunger", True):
            specs.append(("hunger", "饥饿", *hunger_pick))
        if persona_profile.get("allow_cycle", False):
            skip_cycle_spec = False
            if self._advanced_cycle_enabled():
                meta = self.data.get("body_cycle_state", {})
                anchor_ts = _safe_float(meta.get("cycle_anchor_ts"), 0) if isinstance(meta, dict) else 0
                active_advanced_cycle = any(
                    isinstance(cond, dict)
                    and str(cond.get("kind") or "") == "body_cycle"
                    and str(cond.get("phase") or "") in self._ADVANCED_CYCLE_PHASES
                    and _safe_float(cond.get("start_ts"), 0) <= _now_ts() < _safe_float(cond.get("end_ts"), 0)
                    for cond in (self.data.get("state_conditions", []) or [])
                )
                # The anchored continuous timeline owns phase progression once
                # started, so no daily random cycle pick is needed anymore.
                skip_cycle_spec = anchor_ts > 0 or active_advanced_cycle
            if not skip_cycle_spec:
                cycle_spec = (
                    self._pick_advanced_cycle_spec(intensity)
                    if self._advanced_cycle_enabled()
                    else self._pick_body_cycle_spec(cycle_pool, intensity)
                )
                specs.append(("body_cycle", "周期", *cycle_spec))
        else:
            specs.append(("body_cycle", "周期", *cycle_pool[0]))

        diary_tags = self._recent_diary_tags()
        weather_text = self._weather_summary_text(weather)
        if persona_profile.get("allow_health", True):
            health_causes = self._build_health_causes(
                sleep_label=sleep_pick[0],
                weather_text=weather_text,
                diary_tags=diary_tags,
            )
            health_spec = self._pick_health_spec(health_causes, intensity, weather_text)
            if health_spec is not None:
                specs.append(("health", "健康", *health_spec))
        if "失眠" in diary_tags and random.random() < 0.35:
            specs.append(("sleep", "睡眠延续", "昨晚的失眠感还没完全散掉", "迟钝", -12, 8))
        if persona_profile.get("allow_health", True) and "生病" in diary_tags and random.random() < 0.4:
            specs.append(("health", "健康延续", "身体像还在恢复,反应慢半拍", "疲惫", -14, 18, "前两天的不舒服还没完全退掉"))
        if "低能量" in diary_tags and random.random() < 0.35:
            specs.append(("sleep", "能量延续", "昨天的低电量拖到今天早上", "安静", -10, 6))
        if "好梦" in diary_tags and random.random() < 0.3:
            specs.append(("dream", "梦境余温", "梦里留下了一点柔和的亮色", "柔和", 4, 5))
        screen_diary_spec = self._screen_diary_state_condition_spec()
        if screen_diary_spec is not None:
            specs.append(screen_diary_spec)

        conditions = []
        for spec in specs:
            extras: dict[str, Any] = {}
            if len(spec) >= 7:
                kind, title, label, mood, energy_delta, duration_hours, cause = spec[:7]
                extras["cause"] = cause
            else:
                kind, title, label, mood, energy_delta, duration_hours = spec[:6]
            cycle_phase = self._infer_body_cycle_phase(label) if kind == "body_cycle" else ""
            advanced_cycle_phase = self._advanced_cycle_enabled() and cycle_phase in self._ADVANCED_CYCLE_PHASES
            if energy_delta == 0 and kind not in {"sleep", "dream"} and not advanced_cycle_phase:
                continue
            if kind == "health" and energy_delta < 0:
                extras["on_end_transition"] = "health_relief"
                extras["phase"] = "mild_discomfort"
            if kind == "sleep" and energy_delta <= -16:
                extras["on_end_transition"] = "sleep_rebound"
                extras["phase"] = "sleep_debt"
            if kind == "body_cycle" and cycle_phase != "cycle":
                extras["phase"] = cycle_phase
                extras["episode_key"] = f"body-cycle-{_today_key()}"
                if cycle_phase in self._ADVANCED_CYCLE_PHASES:
                    extras["transition_options"] = self._advanced_cycle_transition_options(cycle_phase)
                elif extras["phase"] == "pre":
                    extras["transition_options"] = [{"to": "body_period", "base_weight": 0.72}, {"to": "stable", "base_weight": 0.28}]
                elif extras["phase"] == "period":
                    extras["transition_options"] = [{"to": "body_recovery", "base_weight": 0.65}, {"to": "stable", "base_weight": 0.35}]
            effective_energy_delta = (
                int(energy_delta)
                if advanced_cycle_phase
                else int(energy_delta * max(0.4, intensity))
            )
            extras["transition_options"] = self._build_transition_options(
                kind=kind,
                energy_delta=effective_energy_delta,
                cause=str(extras.get("cause") or ""),
                on_end_transition=str(extras.get("on_end_transition") or ""),
            ) or extras.get("transition_options", [])
            condition = self._make_condition(
                kind=kind,
                title=title,
                label=label,
                mood=mood,
                energy_delta=effective_energy_delta,
                duration_hours=duration_hours,
                intensity=random.randint(35, 90),
                **extras,
            )
            if kind == "body_cycle" and cycle_phase != "cycle":
                if deferred_state_updates is None:
                    self._record_body_cycle_episode(condition)
                else:
                    deferred_state_updates.setdefault("body_cycle_conditions", []).append(condition)
            conditions.append(condition)
        dream_aftertaste = self._build_dream_aftertaste_condition(dream_pick)
        if dream_aftertaste is not None:
            conditions.append(dream_aftertaste)
        discomfort_condition = self._maybe_pick_cycle_discomfort(deferred_state_updates)
        if discomfort_condition is not None:
            conditions.append(discomfort_condition)
        if 0 <= current_minute < 5 * 60:
            late_night_pool = [
                ("夜里还没完全安静下来,眼睛和脑子都慢半拍", "困倦", -14, 4),
                ("这个点还醒着,困意和清醒混在一起", "恍惚", -12, 3),
                ("已经很晚了,精神有点发飘,只想把声音放轻", "疲惫", -10, 5),
            ]
            label, mood, energy_delta, duration_hours = random.choice(late_night_pool)
            conditions.append(
                self._make_condition(
                    kind="sleep",
                    title="夜深未眠",
                    label=label,
                    mood=mood,
                    energy_delta=int(energy_delta * max(0.55, intensity)),
                    duration_hours=duration_hours,
                    intensity=random.randint(45, 88),
                    phase="late_night_awake",
                    transition_options=[
                        {"to": "sleep_afterglow", "base_weight": 0.35},
                        {"to": "sleep_tail", "base_weight": 0.2},
                        {"to": "stable", "base_weight": 0.45},
                    ],
                )
            )
        return conditions

    def _ensure_time_based_hunger_condition(self) -> None:
        profile = self._persona_state_profile()
        if not profile.get("allow_hunger", True):
            return
        if any(str(cond.get("kind") or "") == "hunger" for cond in self._get_active_conditions()):
            return
        if _safe_float(self.data.get("last_food_state_feedback_at"), 0) + 90 * 60 > _now_ts():
            return
        now_dt = self._environment_now()
        minute = now_dt.hour * 60 + now_dt.minute
        windows = [
            ("breakfast", 7 * 60, 9 * 60 + 30, "饿,想吃热的", "柔软", -4, 2),
            ("lunch", 11 * 60, 13 * 60 + 40, "饿,想吃东西", "走神", -6, 2),
            ("afternoon", 15 * 60, 17 * 60, "想吃甜的", "柔软", 2, 2),
            ("dinner", 17 * 60 + 30, 20 * 60, "饿,想吃热的", "粘人", -5, 3),
            ("late_snack", 21 * 60 + 30, 23 * 60 + 30, "有点想吃东西", "松散", -3, 2),
        ]
        matched = next((item for item in windows if item[1] <= minute <= item[2]), None)
        if not matched:
            return
        window_id, _start, _end, label, mood, energy_delta, duration_hours = matched
        attempts = self.data.get("hunger_window_attempts")
        if not isinstance(attempts, dict):
            attempts = {}
        today = _today_key()
        generated = attempts.get("generated")
        if not isinstance(generated, list):
            generated = []
        generated = [
            item for item in generated
            if isinstance(item, dict) and str(item.get("date") or "") == today
        ][-5:]
        if len(generated) >= 2:
            attempts["generated"] = generated
            self.data["hunger_window_attempts"] = attempts
            return
        last_generated_ts = max((_safe_float(item.get("ts"), 0) for item in generated), default=0.0)
        if last_generated_ts and _now_ts() - last_generated_ts < 4 * 3600:
            attempts["generated"] = generated
            self.data["hunger_window_attempts"] = attempts
            return
        attempt_key = f"{today}:{window_id}"
        if attempts.get("last_key") == attempt_key:
            return
        attempts["last_key"] = attempt_key
        attempts["last_attempt_ts"] = _now_ts()
        self.data["hunger_window_attempts"] = attempts
        intensity = max(0.0, min(1.0, _safe_float(runtime_persona_setting(self, "humanized_state_intensity", 50), 50, 0, 100) / 100))
        chance = 0.25 + 0.30 * intensity
        if window_id in {"afternoon", "late_snack"}:
            chance *= 0.65
        if random.random() > chance:
            return
        self.data.setdefault("state_conditions", []).append(
            self._make_condition(
                kind="hunger",
                title="饭点",
                label=label,
                mood=mood,
                energy_delta=int(energy_delta * max(0.55, intensity)),
                duration_hours=duration_hours,
                intensity=random.randint(45, 82),
                phase=window_id,
                cause="饭点自然波动",
            )
        )
        generated.append({"date": today, "window": window_id, "ts": _now_ts()})
        attempts["generated"] = generated[-5:]
        attempts["last_generated_ts"] = _now_ts()
        self.data["hunger_window_attempts"] = attempts

    _ADVANCED_CYCLE_PHASES = (
        "menstrual",
        "follicular",
        "pre_ovulation",
        "ovulation",
        "luteal",
        "pms",
    )
    _ADVANCED_CYCLE_TRANSITIONS = {
        "menstrual": "body_follicular",
        "follicular": "body_pre_ovulation",
        "pre_ovulation": "body_ovulation",
        "ovulation": "body_luteal",
        "luteal": "body_pms",
        "pms": "body_menstrual",
    }
    _ADVANCED_CYCLE_INTENSITY_MEDIANS = {
        "menstrual": -12.0,
        "follicular": 0.0,
        "pre_ovulation": 7.5,
        "ovulation": 9.0,
        "luteal": 4.5,
        "pms": -7.5,
    }
    _ADVANCED_CYCLE_PHASE_NAMES = {
        "menstrual": "月经期",
        "follicular": "卵泡期",
        "pre_ovulation": "排卵前期",
        "ovulation": "排卵期",
        "luteal": "黄体期",
        "pms": "PMS 期",
    }
    _ADVANCED_CYCLE_DISCOMFORT_SPECS = {
        "痛经": {
            "phases": {"menstrual"},
            "label": "今天有点痛经，小腹闷闷地不舒服",
            "mood": "疲惫",
            "energy_delta": -14,
            "duration_hours": 6,
            "weight": 4,
        },
        "头痛": {
            "phases": {"menstrual", "pms"},
            "label": "头有点闷痛，注意力不太集中",
            "mood": "迟钝",
            "energy_delta": -10,
            "duration_hours": 5,
            "weight": 3,
        },
        "腰酸": {
            "phases": {"menstrual", "luteal"},
            "label": "腰有点酸，不太想久坐",
            "mood": "疲惫",
            "energy_delta": -8,
            "duration_hours": 6,
            "weight": 3,
        },
        "乏力": {
            "phases": {"menstrual", "luteal", "pms"},
            "label": "身上没什么力气，动作慢半拍",
            "mood": "困倦",
            "energy_delta": -12,
            "duration_hours": 8,
            "weight": 4,
        },
        "情绪低落": {
            "phases": {"pms"},
            "label": "情绪有点低，不太想说话",
            "mood": "低落",
            "energy_delta": -6,
            "duration_hours": 5,
            "weight": 2,
        },
        "恶心": {
            "phases": {"menstrual", "pms"},
            "label": "胃里有点泛恶心，不太想吃东西",
            "mood": "虚弱",
            "energy_delta": -9,
            "duration_hours": 4,
            "weight": 1,
        },
    }

    def _advanced_cycle_enabled(self) -> bool:
        return bool(runtime_persona_setting(self, "enable_advanced_cycle_strategy", False))

    def _infer_body_cycle_phase(self, label: str) -> str:
        text = str(label or "")
        upper_text = text.upper()
        if "PMS" in upper_text or "经前综合征" in text:
            return "pms"
        if "排卵前期" in text:
            return "pre_ovulation"
        if "月经期" in text:
            return "menstrual"
        if "卵泡期" in text:
            return "follicular"
        if "排卵期" in text:
            return "ovulation"
        if "黄体期" in text:
            return "luteal"
        if "生理期后" in text or "恢复" in text:
            return "recovery"
        if "前" in text:
            return "pre"
        if "生理期" in text:
            return "period"
        return "cycle"

    def _body_cycle_max_hours(self, phase: str, label: str = "") -> int:
        phase = str(phase or self._infer_body_cycle_phase(label))
        advanced_hours = self._advanced_cycle_phase_hours(phase)
        if advanced_hours is not None:
            return advanced_hours
        if phase == "period":
            return 72
        if phase in {"pre", "recovery"}:
            return 24
        return 48

    def _body_cycle_interval_seconds(self) -> int:
        if self._advanced_cycle_enabled():
            return self._advanced_cycle_total_days() * 86400
        return random.randint(25, 34) * 86400

    def _advanced_cycle_phase_days(self, phase: str) -> int:
        defaults = {
            "menstrual": 5,
            "follicular": 5,
            "pre_ovulation": 3,
            "ovulation": 1,
            "luteal": 8,
            "pms": 6,
        }
        attributes = {
            "menstrual": "advanced_cycle_menstrual_days",
            "follicular": "advanced_cycle_follicular_days",
            "pre_ovulation": "advanced_cycle_pre_ovulation_days",
            "ovulation": "advanced_cycle_ovulation_days",
            "luteal": "advanced_cycle_luteal_days",
            "pms": "advanced_cycle_pms_days",
        }
        default = defaults.get(phase, 1)
        attribute = attributes.get(phase, "")
        return _safe_int(runtime_persona_setting(self, attribute, default), default, 1, 30) if attribute else default

    def _advanced_cycle_phase_hours(self, phase: str) -> int | None:
        if phase not in self._ADVANCED_CYCLE_PHASES:
            return None
        return self._advanced_cycle_phase_days(phase) * 24

    def _advanced_cycle_total_days(self) -> int:
        return sum(self._advanced_cycle_phase_days(phase) for phase in self._ADVANCED_CYCLE_PHASES)

    def _advanced_cycle_offset_signature(self, offset: int) -> str:
        durations = ",".join(str(self._advanced_cycle_phase_days(phase)) for phase in self._ADVANCED_CYCLE_PHASES)
        return f"{max(0, int(offset))}:{durations}"

    def _advanced_cycle_position_from_offset(self, offset: int) -> tuple[str, int]:
        total_days = max(1, self._advanced_cycle_total_days())
        cycle_day = ((max(1, int(offset)) - 1) % total_days) + 1
        cursor = 0
        for phase in self._ADVANCED_CYCLE_PHASES:
            phase_days = self._advanced_cycle_phase_days(phase)
            if cycle_day <= cursor + phase_days:
                return phase, cycle_day - cursor
            cursor += phase_days
        return "pms", self._advanced_cycle_phase_days("pms")

    def _advanced_cycle_day_of_phase(self, phase: str, day_in_phase: int) -> int:
        """Map a phase plus its day index to the absolute cycle day."""
        cursor = 0
        for candidate in self._ADVANCED_CYCLE_PHASES:
            if candidate == phase:
                return cursor + max(1, int(day_in_phase))
            cursor += self._advanced_cycle_phase_days(candidate)
        return 1

    def _advanced_cycle_runtime(self) -> dict[str, Any]:
        """Derive the current six-phase position for display and continuity.

        The stored cycle anchor timestamp is the authoritative continuous
        timeline: it always yields the current phase and day, even when the
        bot was offline or no body_cycle condition is currently active. Active
        conditions are only used as a fallback for old data without an anchor.

        Returns:
            Phase position details, or an empty dict when the strategy is off
            or the cycle has not started yet.
        """
        if not self._advanced_cycle_enabled():
            return {}
        now = _now_ts()
        meta = self.data.get("body_cycle_state")
        anchor_ts = _safe_float(meta.get("cycle_anchor_ts"), 0) if isinstance(meta, dict) else 0
        phase = ""
        day_in_phase = 0
        if anchor_ts > 0:
            cycle_day = int((now - anchor_ts) // 86400) + 1
            phase, day_in_phase = self._advanced_cycle_position_from_offset(cycle_day)
        else:
            # Legacy fallback for historical data created before the anchor
            # existed. The anchor is always seeded on first enable now, so this
            # branch only matters while migrating old conditions.
            conditions = self.data.get("state_conditions", [])
            if isinstance(conditions, list):
                for cond in conditions:
                    if not isinstance(cond, dict) or str(cond.get("kind") or "") != "body_cycle":
                        continue
                    cond_phase = str(cond.get("phase") or "")
                    if cond_phase not in self._ADVANCED_CYCLE_PHASES:
                        continue
                    start_ts = _safe_float(cond.get("start_ts"), 0)
                    end_ts = _safe_float(cond.get("end_ts"), 0)
                    if start_ts <= now < end_ts:
                        phase = cond_phase
                        day_in_phase = int((now - start_ts) // 86400) + 1
                        break
            if not phase:
                return {}
        phase_days = self._advanced_cycle_phase_days(phase)
        day_in_phase = max(1, min(phase_days, int(day_in_phase)))
        label, mood, energy_delta, _ = self._advanced_cycle_phase_spec(phase)
        next_phase = self._ADVANCED_CYCLE_TRANSITIONS.get(phase, "")
        next_phase = self._ADVANCED_CYCLE_TRANSITIONS.get(phase, "").removeprefix("body_")
        return {
            "phase": phase,
            "phase_name": self._ADVANCED_CYCLE_PHASE_NAMES.get(phase, phase),
            "day_in_phase": day_in_phase,
            "phase_days": phase_days,
            "cycle_day": self._advanced_cycle_day_of_phase(phase, day_in_phase),
            "cycle_days": self._advanced_cycle_total_days(),
            "mood": _single_line(mood, 20),
            "energy_delta": int(energy_delta),
            "label": _single_line(label, 160),
            "next_phase": next_phase,
            "next_phase_name": self._ADVANCED_CYCLE_PHASE_NAMES.get(next_phase, ""),
        }

    def _active_cycle_discomfort_conditions(self) -> list[dict[str, Any]]:
        now = _now_ts()
        items: list[dict[str, Any]] = []
        conditions = self.data.get("state_conditions", [])
        if not isinstance(conditions, list):
            return items
        for cond in conditions:
            if not isinstance(cond, dict) or str(cond.get("kind") or "") != "cycle_discomfort":
                continue
            if _safe_float(cond.get("start_ts"), 0) <= now < _safe_float(cond.get("end_ts"), 0):
                items.append(
                    {
                        "type": _single_line(cond.get("phase"), 12) or "经期不适",
                        "label": _single_line(cond.get("label"), 80),
                        "mood": _single_line(cond.get("mood"), 12),
                    }
                )
        return items

    def _maybe_pick_cycle_discomfort(self, deferred_state_updates: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """Roll once per day for a menstrual discomfort episode on the current phase.

        Only runs when the discomfort simulation, the advanced six-phase
        strategy and the persona cycle allowance are all enabled, and only
        during phases allowed per discomfort type. Rolls at most once per
        calendar day and skips the roll while another discomfort condition is
        still active.

        Returns:
            A cycle_discomfort condition dict, or None when skipped.
        """
        if not bool(runtime_persona_setting(self, "advanced_cycle_discomfort_simulation", False)):
            return None
        if not self._persona_state_profile().get("allow_cycle", False):
            return None
        intensity = _safe_int(runtime_persona_setting(self, "humanized_state_intensity", 50), 50, 0, 100)
        if intensity <= 0:
            return None
        meta = self.data.get("body_cycle_state")
        meta = dict(meta) if isinstance(meta, dict) else {}
        if meta.get("last_discomfort_roll_date") == _today_key():
            return None
        runtime = self._advanced_cycle_runtime()
        phase = runtime.get("phase") if runtime else ""
        if phase not in self._ADVANCED_CYCLE_PHASES:
            return None
        now = _now_ts()
        conditions = self.data.get("state_conditions", [])
        if isinstance(conditions, list):
            for cond in conditions:
                if (
                    isinstance(cond, dict)
                    and str(cond.get("kind") or "") == "cycle_discomfort"
                    and _safe_float(cond.get("end_ts"), 0) > now
                ):
                    return None
        # One roll attempt per day regardless of the outcome, so a failed roll
        # does not give the phase extra chances later the same day.
        if deferred_state_updates is None:
            meta["last_discomfort_roll_date"] = _today_key()
            self.data["body_cycle_state"] = meta
        else:
            deferred_state_updates["cycle_discomfort_roll_date"] = _today_key()
        chance = _safe_int(runtime_persona_setting(self, "advanced_cycle_discomfort_chance", 55), 55, 0, 100)
        if chance <= 0 or random.random() > chance / 100.0:
            return None
        raw_types = str(runtime_persona_setting(self, "advanced_cycle_discomfort_types", "痛经,头痛,腰酸,乏力") or "痛经,头痛,腰酸,乏力")
        requested = {token.strip() for token in raw_types.replace("，", ",").split(",") if token.strip()}
        candidates = [
            (name, spec)
            for name, spec in self._ADVANCED_CYCLE_DISCOMFORT_SPECS.items()
            if name in requested and phase in spec.get("phases", set())
        ]
        if not candidates:
            return None
        name, spec = random.choices(
            candidates,
            weights=[int(spec.get("weight") or 1) for _, spec in candidates],
            k=1,
        )[0]
        energy_delta = int((spec.get("energy_delta") or 0) * max(0.5, intensity / 50.0))
        return self._make_condition(
            kind="cycle_discomfort",
            title="经期不适",
            label=_single_line(spec.get("label"), 80),
            mood=_single_line(spec.get("mood"), 12) or "疲惫",
            energy_delta=energy_delta,
            duration_hours=_safe_int(spec.get("duration_hours"), 6, 1, 24),
            intensity=random.randint(45, max(46, min(92, 40 + intensity))),
            cause="生理周期阶段伴随不适",
            phase=name,
            episode_key=f"cycle-discomfort-{_today_key()}",
        )

    def _advanced_cycle_linked_energy(self, phase: str) -> int:
        median = self._ADVANCED_CYCLE_INTENSITY_MEDIANS.get(phase, 0.0)
        intensity = _safe_int(runtime_persona_setting(self, "humanized_state_intensity", 50), 50, 0, 100)
        return int(round(median * (intensity / 50.0)))

    def _advanced_cycle_phase_spec(self, phase: str) -> tuple[str, str, int, int]:
        defaults = {
            "menstrual": ("处于月经期，身体更容易疲倦，情绪感受稍敏锐", "疲惫", -12),
            "follicular": ("处于卵泡期，精力平稳回升，心情逐渐轻快", "轻快", 0),
            "pre_ovulation": ("处于排卵前期，身体逐渐轻盈，精力有所上升", "期待", 8),
            "ovulation": ("处于排卵期，精力较充足，社交意愿稍有增强", "明朗", 9),
            "luteal": ("处于黄体期，精力尚可，情绪整体平稳", "平稳", 5),
            "pms": ("处于 PMS 期，精力有所下降，情绪波动稍明显", "敏感", -8),
        }
        attributes = {
            "menstrual": ("advanced_cycle_menstrual_prompt", "advanced_cycle_menstrual_mood", "advanced_cycle_menstrual_energy"),
            "follicular": ("advanced_cycle_follicular_prompt", "advanced_cycle_follicular_mood", "advanced_cycle_follicular_energy"),
            "pre_ovulation": ("advanced_cycle_pre_ovulation_prompt", "advanced_cycle_pre_ovulation_mood", "advanced_cycle_pre_ovulation_energy"),
            "ovulation": ("advanced_cycle_ovulation_prompt", "advanced_cycle_ovulation_mood", "advanced_cycle_ovulation_energy"),
            "luteal": ("advanced_cycle_luteal_prompt", "advanced_cycle_luteal_mood", "advanced_cycle_luteal_energy"),
            "pms": ("advanced_cycle_pms_prompt", "advanced_cycle_pms_mood", "advanced_cycle_pms_energy"),
        }
        selected_phase = phase if phase in defaults else "menstrual"
        default_prompt, default_mood, default_energy = defaults[selected_phase]
        prompt_attr, mood_attr, energy_attr = attributes[selected_phase]
        label = _single_line(runtime_persona_setting(self, prompt_attr, default_prompt), 160) or default_prompt
        mood = _single_line(runtime_persona_setting(self, mood_attr, default_mood), 20) or default_mood
        energy_delta = (
            self._advanced_cycle_linked_energy(selected_phase)
        if bool(runtime_persona_setting(self, "advanced_cycle_link_intensity", False))
            else _safe_int(runtime_persona_setting(self, energy_attr, default_energy), default_energy, -50, 30)
        )
        return label, mood, energy_delta, self._advanced_cycle_phase_days(selected_phase) * 24

    def _advanced_cycle_transition_options(self, phase: str) -> list[dict[str, Any]]:
        target = self._ADVANCED_CYCLE_TRANSITIONS.get(phase, "")
        return [{"to": target, "base_weight": 1.0}] if target else []

    def _advanced_cycle_condition(
        self,
        phase: str,
        *,
        episode_key: str = "",
        cause: str = "周期阶段自然推进",
        duration_hours: int | None = None,
    ) -> dict[str, Any]:
        label, mood, energy_delta, configured_hours = self._advanced_cycle_phase_spec(phase)
        return self._make_condition(
            kind="body_cycle",
            title="周期",
            label=label,
            mood=mood,
            energy_delta=energy_delta,
            duration_hours=max(1, int(duration_hours or configured_hours)),
            intensity=max(35, _safe_int(runtime_persona_setting(self, "humanized_state_intensity", 50), 50, 0, 100)),
            cause=cause,
            phase=phase,
            episode_key=episode_key or f"body-cycle-{_today_key()}",
            transition_options=self._advanced_cycle_transition_options(phase),
        )

    def _pick_advanced_cycle_spec(self, intensity: float) -> tuple[str, str, int, int]:
        neutral = ("不处于生理期", "平稳", 0, 24)
        if self._body_cycle_generation_blocked():
            return neutral
        meta = self.data.get("body_cycle_state", {})
        anchor_ts = _safe_float(meta.get("cycle_anchor_ts"), 0) if isinstance(meta, dict) else 0
        if anchor_ts > 0:
            # Once the continuous timeline is anchored, phase progression is
            # deterministic; a random new-cycle pick would shift it backwards.
            return neutral
        now = _now_ts()
        expected_ts = _safe_float(meta.get("next_expected_start_ts"), 0) if isinstance(meta, dict) else 0
        if expected_ts > 0:
            days_late = max(0.0, (now - expected_ts) / 86400)
            chance = min(0.75, 0.22 + days_late * 0.14) * max(0.35, min(1.15, intensity))
        else:
            chance = 0.10 * max(0.35, min(1.2, intensity))
        if random.random() > chance:
            return neutral
        return self._advanced_cycle_phase_spec("menstrual")

    def _body_cycle_generation_blocked(self, now: float | None = None) -> bool:
        now = _now_ts() if now is None else now
        meta = self.data.get("body_cycle_state", {})
        if isinstance(meta, dict):
            expected_ts = _safe_float(meta.get("next_expected_start_ts"), 0)
            if expected_ts > 0 and now < expected_ts - 2 * 86400:
                return True
            if expected_ts <= 0 and _safe_float(meta.get("last_end_ts"), 0) + 18 * 86400 > now:
                return True
        conditions = self.data.get("state_conditions", [])
        if not isinstance(conditions, list):
            return False
        recent_floor = now - 14 * 86400
        for cond in conditions:
            if not isinstance(cond, dict) or str(cond.get("kind") or "") != "body_cycle":
                continue
            start_ts = _safe_float(cond.get("start_ts"), 0)
            end_ts = _safe_float(cond.get("end_ts"), 0)
            if end_ts > now or max(start_ts, end_ts) >= recent_floor:
                return True
        return False

    def _pick_body_cycle_spec(
        self,
        cycle_pool: list[tuple[str, str, int, int]],
        intensity: float,
    ) -> tuple[str, str, int, int]:
        neutral = cycle_pool[0]
        if self._body_cycle_generation_blocked():
            return neutral
        now = _now_ts()
        meta = self.data.get("body_cycle_state", {})
        expected_ts = _safe_float(meta.get("next_expected_start_ts"), 0) if isinstance(meta, dict) else 0
        if expected_ts > 0:
            days_late = max(0.0, (now - expected_ts) / 86400)
            chance = min(0.65, 0.18 + days_late * 0.12) * max(0.35, min(1.15, intensity))
        else:
            chance = 0.085 * max(0.35, min(1.2, intensity))
        if random.random() > chance:
            return neutral
        return random.choices(cycle_pool[1:], weights=[0.45, 0.55], k=1)[0]

    def _record_body_cycle_episode(self, cond: dict[str, Any]) -> None:
        start_ts = _safe_float(cond.get("start_ts"), _now_ts())
        end_ts = _safe_float(cond.get("end_ts"), start_ts)
        phase = str(cond.get("phase") or self._infer_body_cycle_phase(str(cond.get("label") or "")))
        previous = self.data.get("body_cycle_state")
        meta = dict(previous) if isinstance(previous, dict) else {}
        payload = {
            "last_start_ts": start_ts,
            "last_end_ts": end_ts,
            "next_expected_start_ts": start_ts + self._body_cycle_interval_seconds(),
            "last_phase": phase,
            "last_label": _single_line(cond.get("label"), 80),
        }
        # Episode reconciliation rewrites this record whenever the bot
        # catches up after downtime. Keep the daily discomfort dedup marker
        # across those rewrites so one calendar day still gets one roll.
        if meta.get("last_discomfort_roll_date"):
            payload["last_discomfort_roll_date"] = _single_line(
                meta.get("last_discomfort_roll_date"), 16
            )
        if phase in self._ADVANCED_CYCLE_PHASES:
            payload["strategy"] = "advanced"
            previous_anchor = _safe_float(meta.get("cycle_anchor_ts"), 0)
            if phase == "menstrual" and previous_anchor <= 0:
                payload["cycle_anchor_ts"] = start_ts
            elif previous_anchor > 0:
                payload["cycle_anchor_ts"] = previous_anchor
            if phase != "menstrual" and _safe_float(meta.get("last_start_ts"), 0) > 0:
                payload["last_start_ts"] = _safe_float(meta.get("last_start_ts"), start_ts)
                payload["next_expected_start_ts"] = _safe_float(
                    meta.get("next_expected_start_ts"),
                    payload["last_start_ts"] + self._advanced_cycle_total_days() * 86400,
                )
            for key in ("manual_offset", "manual_offset_signature", "manual_offset_phase", "manual_offset_day_in_phase"):
                if key in meta:
                    payload[key] = meta[key]
        else:
            payload["strategy"] = "legacy"
        self.data["body_cycle_state"] = payload

    def _build_health_causes(
        self,
        *,
        sleep_label: str,
        weather_text: str,
        diary_tags: set[str],
    ) -> list[str]:
        causes: list[str] = []
        if sleep_label not in {"睡眠平稳", "睡得很踏实"} and random.random() < 0.7:
            causes.append("昨晚没睡踏实")
        if any(tag in diary_tags for tag in {"失眠", "低能量"}) and random.random() < 0.45:
            causes.append("前一天状态就有点透支")
        weather_lower = str(weather_text or "").lower()
        if any(token in weather_text for token in ("降雨", "小雨", "中雨", "大雨", "阴", "多云")) and random.random() < 0.4:
            causes.append("空气有点潮,身上那股乏劲更明显")
        if any(token in weather_text for token in ("风", "降温", "冷")) and random.random() < 0.55:
            causes.append("吹了点风,身上容易发空")
        temp_match = re.search(r"(-?\d+(?:\.\d+)?)\s*°C", weather_lower)
        if temp_match:
            try:
                temp = float(temp_match.group(1))
            except ValueError:
                temp = 20.0
            if temp <= 10 and random.random() < 0.55:
                causes.append("天气偏冷,早上容易着凉")
            elif temp >= 30 and random.random() < 0.35:
                causes.append("天气闷热,整个人有点蔫")
        return causes

    def _pick_health_spec(
        self, causes: list[str], intensity: float, weather_text: str
    ) -> tuple[str, str, int, int, str] | None:
        if not causes:
            return None
        chance = min(0.42, 0.12 + len(causes) * 0.1 * max(0.5, intensity))
        if random.random() > chance:
            return None
        cause_text = ",".join(dict.fromkeys(causes[:2]))
        pool = [
            ("喉咙有点发紧,今天想少说重话", "安静", -10, 24),
            ("头有点沉,做事想放慢一点", "疲惫", -14, 18),
            ("像有点发虚,反应会慢半拍", "疲惫", -18, 30),
        ]
        label, mood, energy_delta, duration_hours = random.choice(pool)
        if "闷热" in cause_text and "喉咙" in label:
            label = "有点发闷,只想把动作放轻一点"
        if "潮" in cause_text and "头有点沉" in label:
            label = "身上有点沉,今天想把事情做轻一点"
        return label, mood, energy_delta, duration_hours, cause_text

    def _build_transition_options(
        self,
        *,
        kind: str,
        energy_delta: int,
        cause: str,
        on_end_transition: str,
    ) -> list[dict[str, Any]]:
        if on_end_transition == "health_relief":
            return [
                {"to": "recovery_afterglow", "base_weight": 0.45},
                {"to": "stable", "base_weight": 0.4},
                {"to": "health_tail", "base_weight": 0.15},
            ]
        if on_end_transition == "sleep_rebound":
            return [
                {"to": "sleep_afterglow", "base_weight": 0.35},
                {"to": "stable", "base_weight": 0.5},
                {"to": "sleep_tail", "base_weight": 0.15},
            ]
        if kind == "care_warmth":
            return [
                {"to": "stable", "base_weight": 0.8},
                {"to": "soft_afterglow", "base_weight": 0.2},
            ]
        return []

    def _make_condition(
        self,
        *,
        kind: str,
        title: str,
        label: str,
        mood: str,
        energy_delta: int,
        duration_hours: int,
        intensity: int,
        cause: str = "",
        on_end_transition: str = "",
        phase: str = "",
        episode_key: str = "",
        transition_options: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        start_ts = _now_ts()
        return {
            "id": f"{kind}-{int(start_ts)}-{random.randint(1000, 9999)}",
            "kind": kind,
            "title": title,
            "label": label,
            "mood": mood,
            "energy_delta": energy_delta,
            "intensity": intensity,
            "start_ts": start_ts,
            "end_ts": start_ts + duration_hours * 3600,
            "duration_hours": duration_hours,
            "cause": cause,
            "on_end_transition": on_end_transition,
            "phase": phase,
            "episode_key": episode_key,
            "transition_options": list(transition_options or []),
        }

    def _infer_manual_state_mood(self, text: str) -> str:
        raw = str(text or "")
        mapping = [
            (("累", "疲惫", "困", "没电"), "疲惫"),
            (("烦", "乱", "躁", "闷"), "烦闷"),
            (("病", "难受", "不舒服", "头疼", "发烧"), "虚弱"),
            (("饿", "胃口", "嘴馋"), "黏人"),
            (("开心", "轻快", "高兴", "兴奋"), "轻快"),
            (("紧张", "慌", "忐忑"), "紧张"),
            (("安静", "困倦", "恍惚"), "安静"),
        ]
        for markers, mood in mapping:
            if any(marker in raw for marker in markers):
                return mood
        return "平稳"

    def _infer_manual_state_energy_delta(self, text: str) -> int:
        raw = str(text or "")
        if any(token in raw for token in ("开心", "轻快", "高兴", "兴奋")):
            return 6
        if any(token in raw for token in ("病", "难受", "不舒服", "发烧", "头疼")):
            return -16
        if any(token in raw for token in ("累", "疲惫", "困", "没电")):
            return -10
        if any(token in raw for token in ("烦", "乱", "躁", "闷")):
            return -8
        return -4 if any(token in raw for token in ("紧张", "慌")) else 0

    async def _add_manual_state(self, value: str) -> tuple[bool, str]:
        raw = str(value or "").strip()
        if not raw:
            return False, "请这样填写：陪伴 增添状态 有点累了|8"
        label_part, sep, hours_part = raw.partition("|")
        label = _single_line(label_part, 80)
        if not label:
            return False, "状态描述不能为空。"
        profile = self._persona_state_profile()
        hunger_like = any(token in label for token in ("饿", "胃口", "嘴馋", "馋", "想吃", "吃点", "吃些"))
        health_like = any(token in label for token in ("病", "难受", "不舒服", "发烧", "头疼", "头痛", "咳", "感冒"))
        if hunger_like and not profile.get("allow_hunger", True):
            return False, "当前配置未开启饥饿/胃口状态。"
        if health_like and not profile.get("allow_health", True):
            return False, "当前配置未开启健康/不适状态。"
        duration_hours = _safe_int(hours_part.strip() if sep else 12, 12, 1, 72)
        mood = self._infer_manual_state_mood(label)
        energy_delta = self._infer_manual_state_energy_delta(label)
        await self._ensure_daily_state()
        async with self._data_lock:
            conditions = self.data.setdefault("state_conditions", [])
            if not isinstance(conditions, list):
                self.data["state_conditions"] = []
                conditions = self.data["state_conditions"]
            conditions.append(
                self._make_condition(
                    kind="manual_state",
                    title="手动增添状态",
                    label=label,
                    mood=mood,
                    energy_delta=energy_delta,
                    duration_hours=duration_hours,
                    intensity=60,
                    cause="由用户手动增添",
                    phase="manual",
                )
            )
            state = self._compose_state_from_conditions(self.data.get("daily_weather", {}))
            self.data["daily_state"] = state
            self._save_data_sync(sections={"daily_state", "state_conditions"})
        return True, f"已增添状态：{label}（约持续 {duration_hours} 小时）"



    def _detect_interaction_warmth_feedback(self, text: str, user: dict[str, Any] | None = None) -> dict[str, Any]:
        normalized = _single_line(text, 220)
        if not normalized:
            return {"is_warmth": False}
        intimate = bool(re.search(r"摸摸|贴贴|抱抱|亲亲|揉揉|蹭蹭|摸头|抱一下|贴一下|rua", normalized, re.IGNORECASE))
        comfort = bool(re.search(r"陪你|哄你|乖|不难过|别难过|没关系|辛苦了|抱一下|摸摸头", normalized))
        positive = bool(re.search(r"开心|好耶|哈哈|笑死|可爱|喜欢|太好了|真好|想你|爱你|在呢|来了|陪我", normalized, re.IGNORECASE))
        if not (intimate or comfort or positive):
            return {"is_warmth": False}

        relationship_score = _safe_int(user.get("relationship_score") if isinstance(user, dict) else 0, 0, 0)
        episode_count = _safe_int(user.get("episode_message_count") if isinstance(user, dict) else 0, 0, 0)
        state = self._compose_state_from_conditions(self.data.get("daily_weather", {}))
        energy = _safe_int(state.get("energy"), 75, 0, 100)

        is_sustained_positive = positive and episode_count >= 6 and relationship_score >= 18
        if positive and not (intimate or comfort or is_sustained_positive):
            return {"is_warmth": False}

        base_delta = 2 if intimate or comfort else 1
        if energy <= 45:
            base_delta += 2
        elif energy <= 62:
            base_delta += 1
        elif energy >= 86:
            base_delta = max(1, base_delta - 1)
        if relationship_score >= 120:
            base_delta += 2
        elif relationship_score >= 55:
            base_delta += 1
        if is_sustained_positive and episode_count >= 10:
            base_delta += 1

        max_delta = 8 if intimate or comfort else 4
        delta = max(1, min(max_delta, base_delta))
        if intimate:
            source = "亲密互动回暖"
            label = "被亲近安抚后,精神轻轻回暖"
            mood = "柔和"
            duration_hours = 4
            intensity = 58
            phase = "intimacy"
        elif comfort:
            source = "安慰互动回暖"
            label = "被安慰后,紧绷感松开一点"
            mood = "柔和"
            duration_hours = 4
            intensity = 54
            phase = "comfort"
        else:
            source = "连续对话回暖"
            label = "和熟悉的人连续聊了一会儿,精神被带起来一点"
            mood = "轻快"
            duration_hours = 3
            intensity = 42
            phase = "sustained_positive_chat"
        return {
            "is_warmth": True,
            "source": source,
            "label": label,
            "mood": mood,
            "energy_delta": delta,
            "duration_hours": duration_hours,
            "intensity": intensity,
            "phase": phase,
            "cause": _single_line(normalized, 80),
            "max_delta": max_delta,
        }

    def _apply_interaction_warmth_to_state(self, text: str, user: dict[str, Any] | None = None) -> bool:
        feedback = self._detect_interaction_warmth_feedback(text, user)
        if not feedback.get("is_warmth"):
            return False
        now = _now_ts()
        conditions = self.data.setdefault("state_conditions", [])
        if not isinstance(conditions, list):
            self.data["state_conditions"] = []
            conditions = self.data["state_conditions"]
        max_delta = _safe_int(feedback.get("max_delta"), 6, 1, 10)
        active = next(
            (
                cond for cond in reversed(conditions)
                if isinstance(cond, dict)
                and str(cond.get("kind") or "") == "interaction_warmth"
                and _safe_float(cond.get("end_ts"), 0) > now
            ),
            None,
        )
        if isinstance(active, dict):
            current_delta = _safe_int(active.get("energy_delta"), 0, 0, 20)
            incoming_delta = _safe_int(feedback.get("energy_delta"), 1, 1, 10)
            active["energy_delta"] = min(max_delta, max(current_delta, incoming_delta) + 1)
            active["end_ts"] = max(
                _safe_float(active.get("end_ts"), now),
                now + _safe_int(feedback.get("duration_hours"), 3, 1, 8) * 3600,
            )
            active["duration_hours"] = max(1, int((_safe_float(active.get("end_ts"), now) - now) / 3600))
            active["label"] = _single_line(feedback.get("label"), 80)
            active["mood"] = _single_line(feedback.get("mood"), 20) or active.get("mood") or "柔和"
            active["cause"] = _single_line(feedback.get("cause"), 80)
            active["phase"] = _single_line(feedback.get("phase"), 40)
            active["intensity"] = max(_safe_int(active.get("intensity"), 40), _safe_int(feedback.get("intensity"), 40))
        else:
            conditions.append(
                self._make_condition(
                    kind="interaction_warmth",
                    title=_single_line(feedback.get("source"), 40) or "互动回暖",
                    label=_single_line(feedback.get("label"), 80),
                    mood=_single_line(feedback.get("mood"), 20) or "柔和",
                    energy_delta=_safe_int(feedback.get("energy_delta"), 2, 1, 10),
                    duration_hours=_safe_int(feedback.get("duration_hours"), 3, 1, 8),
                    intensity=_safe_int(feedback.get("intensity"), 45, 0, 100),
                    cause=_single_line(feedback.get("cause"), 80),
                    phase=_single_line(feedback.get("phase"), 40),
                )
            )
        self.data["daily_state"] = self._compose_state_from_conditions(self.data.get("daily_weather", {}))
        return True

    def _parse_date_value(self, value: Any) -> date | None:
        text = str(value or "").strip()
        for fmt in ("%Y-%m-%d", "%m-%d"):
            try:
                parsed = datetime.strptime(text, fmt)
                year = self._environment_now().year if fmt == "%m-%d" else parsed.year
                return date(year, parsed.month, parsed.day)
            except ValueError:
                continue
        return None

    def _next_occurrence(self, entry: dict[str, Any], now: datetime | None = None) -> date | None:
        base = self._parse_date_value(entry.get("date"))
        if base is None:
            return None
        today = (now or self._environment_now()).date()
        if entry.get("repeat_yearly", True):
            try:
                candidate = date(today.year, base.month, base.day)
            except ValueError:
                return None
            if candidate < today:
                try:
                    candidate = date(today.year + 1, base.month, base.day)
                except ValueError:
                    return None
            return candidate
        return base

    def _get_relevant_important_dates(self, now: datetime | None = None) -> list[dict[str, Any]]:
        entries = self.data.get("important_dates", [])
        if not isinstance(entries, list):
            return []
        current = now or self._environment_now()
        today = current.date()
        relevant = []
        for entry in entries:
            if not isinstance(entry, dict) or not entry.get("enabled", True):
                continue
            next_day = self._next_occurrence(entry, now=current)
            if next_day is None:
                continue
            days_until = (next_day - today).days
            remind_days = _safe_int(
                entry.get("remind_days"), runtime_persona_setting(self, "important_date_lookahead_days", 7), 0, 365
            )
            if 0 <= days_until <= remind_days:
                copy = dict(entry)
                copy["_next_date"] = _date_key(next_day)
                copy["_days_until"] = days_until
                relevant.append(copy)
        return sorted(
            relevant,
            key=lambda item: (
                _safe_int(item.get("_days_until"), 999),
                -_safe_int(item.get("priority"), 50),
            ),
        )

    def _format_important_dates_for_prompt(self) -> str:
        entries = self._get_relevant_important_dates()
        if not entries:
            return "（近期没有需要特别记住的日期）"
        lines = []
        for entry in entries[:8]:
            days = _safe_int(entry.get("_days_until"), 0)
            when = "今天" if days == 0 else f"{days} 天后"
            lines.append(
                f"- {when}｜{entry.get('title', '')}｜类型：{entry.get('type', '重要日期')}｜"
                f"备注：{entry.get('note', '')}"
            )
        return "\n".join(lines)

    def _format_calendar_context_for_prompt(self, now: datetime | None = None) -> str:
        current = now or self._environment_now()
        weekday_names = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")
        weekday = weekday_names[current.weekday()]
        is_weekend = current.weekday() >= 5
        builtin_holidays = {
            "01-01": ("元旦", "节假日"),
            "05-01": ("劳动节", "节假日"),
            "10-01": ("国庆节", "节假日"),
        }
        month_day = current.strftime("%m-%d")
        today_dates = [
            entry
            for entry in self._get_relevant_important_dates(now=current)
            if _safe_int(entry.get("_days_until"), 999) == 0
        ]
        special_lines = []
        holiday_tokens = (
            "节",
            "节日",
            "假",
            "假期",
            "放假",
            "休息",
            "旅行",
            "生日",
            "纪念日",
            "春节",
            "元旦",
            "清明",
            "端午",
            "中秋",
            "国庆",
            "劳动",
            "圣诞",
        )
        has_holiday_signal = False
        builtin_holiday = builtin_holidays.get(month_day)
        if builtin_holiday:
            title, type_text = builtin_holiday
            special_lines.append(f"- 今天：{title}｜类型：{type_text}｜备注：内置公历节日")
            has_holiday_signal = True
        for entry in today_dates[:5]:
            title = _single_line(entry.get("title"), 40)
            type_text = _single_line(entry.get("type"), 30)
            note = _single_line(entry.get("note"), 80)
            joined = f"{title} {type_text} {note}"
            if any(token in joined for token in holiday_tokens):
                has_holiday_signal = True
            if title:
                special_lines.append(f"- 今天：{title}｜类型：{type_text or '重要日期'}｜备注：{note or '无'}")

        # The durable calendar is a constraint layer, not execution evidence.
        # Keep its wording explicit so a generated plan can use a confirmed
        # vacation/school rule without claiming that the event already
        # happened.  The fallback below preserves compatibility with hosts
        # that predate AgendaRuntimeMixin.
        calendar_snapshot = {}
        snapshot_getter = getattr(self, "_agenda_calendar_snapshot", None)
        if callable(snapshot_getter):
            try:
                candidate = snapshot_getter(current.date().isoformat(), now=current)
                if isinstance(candidate, dict):
                    calendar_snapshot = candidate
            except Exception:
                calendar_snapshot = {}
        calendar_timeline: dict[str, Any] = {}
        timeline_getter = getattr(self, "_agenda_calendar_timeline", None)
        if callable(timeline_getter):
            try:
                candidate = timeline_getter(
                    current.date().isoformat(),
                    now=current,
                    history_days=3,
                    horizon_days=14,
                )
                if isinstance(candidate, dict):
                    calendar_timeline = candidate
            except Exception:
                calendar_timeline = {}
        calendar_candidates: list[dict[str, Any]] = []
        candidates_getter = getattr(self, "_agenda_calendar_candidates_store", None)
        if callable(candidates_getter):
            try:
                raw_candidates = candidates_getter()
                if isinstance(raw_candidates, list):
                    calendar_candidates = [
                        item for item in raw_candidates
                        if isinstance(item, dict)
                        and str(item.get("lifecycle_state") or item.get("lifecycle") or "candidate") not in {"confirmed", "active", "completed", "cancelled", "expired"}
                    ][:8]
            except Exception:
                calendar_candidates = []
        all_calendar_events = [
            item for item in calendar_snapshot.get("effective_events", calendar_snapshot.get("events", []))
            if isinstance(item, dict) and str(item.get("status") or "") not in {"cancelled", "expired"}
        ]
        # New snapshots expose ``events`` as the complete adjusted list and
        # ``effective_events`` as the planning projection. Older snapshots may
        # only contain one list, so fall back gracefully.
        raw_calendar_events = calendar_snapshot.get("events")
        if isinstance(raw_calendar_events, list):
            all_calendar_events = [
                item for item in raw_calendar_events
                if isinstance(item, dict) and str(item.get("status") or "") not in {"cancelled", "expired"}
            ]
        calendar_events = [
            item for item in calendar_snapshot.get("effective_events", all_calendar_events)
            if isinstance(item, dict) and str(item.get("status") or "") not in {"cancelled", "expired"}
        ]
        calendar_constraints: list[str] = []
        calendar_conflict_lines: list[str] = []
        for event in calendar_events[:16]:
            title = _single_line(event.get("title"), 60)
            if not title:
                continue
            kind = str(event.get("kind") or event.get("type") or "event")
            kind_label = {
                "period": "长期区间",
                "recurrence": "周期规则",
                "event": "单次事件",
                "exception": "例外调整",
            }.get(kind, "日历事件")
            start_date = _single_line(
                event.get("occurrence_date") if kind != "period" else event.get("start_date"),
                24,
            ) or _single_line(event.get("date") or event.get("start_date"), 24)
            end_date = _single_line(event.get("end_date"), 24) if kind == "period" else ""
            date_text = start_date
            if end_date and end_date != start_date:
                date_text = f"{start_date} 至 {end_date}"
            start_at = _single_line(event.get("start_at"), 40)
            end_at = _single_line(event.get("end_at"), 40)
            clock_text = ""
            if (not event.get("all_day") or event.get("start_time") or event.get("end_time")) and start_at and "T" in start_at:
                clock_text = start_at.split("T", 1)[1][:5]
                if end_at and "T" in end_at:
                    clock_text += f"-{end_at.split('T', 1)[1][:5]}"
            if clock_text:
                date_text += f" {clock_text}"
            status_label = "已确认日历约束" if str(event.get("status") or "confirmed") in {"confirmed", "active"} else "待确认日历记录"
            calendar_constraints.append(f"- {title}｜{kind_label}｜{date_text or '今天'}｜{status_label}")
            joined = f"{title} {event.get('note', '')} {event.get('description', '')}"
            if any(token in joined for token in holiday_tokens):
                has_holiday_signal = True
        if calendar_constraints:
            calendar_constraints_block = "今天有效的日历约束（属于计划依据，不等于已经发生）：\n" + "\n".join(calendar_constraints)
        else:
            calendar_constraints_block = ""
        conflicts = calendar_snapshot.get("conflicts") if isinstance(calendar_snapshot.get("conflicts"), list) else []
        if conflicts:
            by_id = {
                str(item.get("source_calendar_id") or item.get("calendar_id") or ""): item
                for item in all_calendar_events
                if isinstance(item, dict)
            }
            for conflict in conflicts[:8]:
                winner_id = str(conflict.get("winner_id") or "")
                loser_id = str(conflict.get("loser_id") or "")
                winner = _single_line(by_id.get(winner_id, {}).get("title"), 50)
                loser = _single_line(by_id.get(loser_id, {}).get("title"), 50)
                if winner and loser:
                    state = "同优先级，需谨慎处理" if conflict.get("unresolved") else "按优先级采用前者"
                    suffix = "｜当天不生效" if not conflict.get("unresolved") else ""
                    calendar_conflict_lines.append(f"- {winner} 覆盖 {loser}｜{state}{suffix}")
        if has_holiday_signal:
            day_tone = "节假日/特殊日期"
        elif is_weekend:
            day_tone = "周末/休息日候选"
        else:
            day_tone = "普通工作日或学习日候选"
        rules = [
            f"日期：{current.strftime('%Y-%m-%d')}（{weekday}）",
            f"基础日期类型：{day_tone}",
        ]
        if special_lines:
            rules.append("今天相关的重要日期：\n" + "\n".join(special_lines))
        else:
            rules.append("今天相关的重要日期：无")
        if calendar_constraints_block:
            rules.append(calendar_constraints_block)
        if calendar_candidates:
            candidate_lines = []
            for item in calendar_candidates:
                title = _single_line(item.get("title"), 80)
                if not title:
                    continue
                date_text = _single_line(item.get("start_date") or item.get("date"), 20) or "近期"
                candidate_lines.append(f"- {title}｜{date_text}｜待确认")
            if candidate_lines:
                rules.append(
                    "近期对话待确认候选（仅供询问参考，不是事实）：\n"
                    + "\n".join(candidate_lines)
                    + "\n不得据此断言用户已经安排、正在执行或已经完成；如有必要，只能轻量询问确认。"
                )
        if calendar_conflict_lines:
            rules.append("日历重叠处理：\n" + "\n".join(calendar_conflict_lines))
        timeline_lines: list[str] = []
        current_phase = calendar_timeline.get("current_phase") if isinstance(calendar_timeline.get("current_phase"), list) else []
        if current_phase:
            phase_text = "、".join(
                f"{_single_line(item.get('title'), 48)}（{_single_line(item.get('start_date'), 16)} 至 {_single_line(item.get('end_date'), 16) or '待定'}）"
                for item in current_phase[:4]
                if isinstance(item, dict) and _single_line(item.get("title"), 48)
            )
            if phase_text:
                timeline_lines.append("当前生活阶段：" + phase_text)
        rhythms = calendar_timeline.get("rhythms") if isinstance(calendar_timeline.get("rhythms"), list) else []
        if rhythms:
            rhythm_text = "、".join(
                f"{_single_line(item.get('title'), 48)}（下次 {_single_line(item.get('next_occurrence'), 16) or '按周期推算'}）"
                for item in rhythms[:5]
                if isinstance(item, dict) and _single_line(item.get("title"), 48)
            )
            if rhythm_text:
                timeline_lines.append("稳定节律参考：" + rhythm_text)
        recent_changes = calendar_timeline.get("recent_changes") if isinstance(calendar_timeline.get("recent_changes"), list) else []
        if recent_changes:
            recent_text = "、".join(
                f"{_single_line(item.get('title'), 40)}（{_single_line(item.get('occurrence_date'), 16)}）"
                for item in recent_changes[:4]
                if isinstance(item, dict) and _single_line(item.get("title"), 40)
            )
            if recent_text:
                timeline_lines.append("最近变化/余波：" + recent_text)
        transitions = calendar_timeline.get("transitions") if isinstance(calendar_timeline.get("transitions"), list) else []
        if transitions:
            transition_text = "、".join(
                f"{_single_line(item.get('date'), 16)} {_single_line(item.get('title'), 40)}"
                for item in transitions[:5]
                if isinstance(item, dict) and _single_line(item.get("title"), 40)
            )
            if transition_text:
                timeline_lines.append("接下来可能发生的转换：" + transition_text)
        uncertainties = calendar_timeline.get("uncertainties") if isinstance(calendar_timeline.get("uncertainties"), list) else []
        if uncertainties:
            uncertainty_text = "、".join(
                _single_line(item.get("title") or item.get("reason") or "待确认变化", 44)
                for item in uncertainties[:4]
                if isinstance(item, dict)
            )
            if uncertainty_text:
                timeline_lines.append("仍不确定的部分：" + uncertainty_text)
        if timeline_lines:
            rules.append(
                "生活时间线（用于保持跨日连续，不等于执行事实）：\n"
                + "\n".join(f"- {line}" for line in timeline_lines)
                + "\n不要因为某一条当天计划就擅自结束或改写当前生活阶段；只有用户明确确认或日历明确记录了转换，才改变长期背景。稳定节律是默认倾向，临时事件可以改变当天，不必抹掉长期节律。存在待确认冲突时保留不确定性，用‘可能/先按目前记录’表达。"
            )
        rules.append(
            "日程判断：先看日期语境,再看人格设定。工作日可以有上课/上班；周末要更松,可以晚起、休息、出门、补一点自己的事；节假日/假期要明显区别于普通日,可以有庆祝、出行、宅家、已明确关系安排或假期拖延。"
        )
        rules.append(
            "如果人格、日程专用设定或重要日期备注里写了调休、补班、补课、考试、值班等例外,优先按这些例外来写。不要凭空塞入身份里没有的校园、职场或节日细节。"
        )
        if calendar_constraints or timeline_lines:
            rules.append(
                "日历使用边界：它提供生活阶段、节律和变化线索，不替代当前会话事实，也不自动删除日程。用户本轮明确说法优先；记录不确定时不要把推断写成确定事实。"
            )
        return "\n".join(rules)

    def _calendar_day_flags(self, now: datetime | None = None) -> dict[str, Any]:
        current = now or self._environment_now()
        is_weekend = current.weekday() >= 5
        builtin_holidays = {"01-01", "05-01", "10-01"}
        month_day = current.strftime("%m-%d")
        today_dates = [
            entry
            for entry in self._get_relevant_important_dates(now=current)
            if _safe_int(entry.get("_days_until"), 999) == 0
        ]
        holiday_tokens = (
            "节",
            "节日",
            "假",
            "假期",
            "放假",
            "休息",
            "旅行",
            "春节",
            "元旦",
            "清明",
            "端午",
            "中秋",
            "国庆",
            "劳动",
        )
        override_tokens = ("调休", "补班", "补课", "考试", "值班", "加班", "返校")
        has_holiday_signal = month_day in builtin_holidays
        has_override_signal = False
        has_calendar_context = False
        has_calendar_holiday_signal = False
        has_calendar_school_work = False
        calendar_snapshot = {}
        snapshot_getter = getattr(self, "_agenda_calendar_snapshot", None)
        if callable(snapshot_getter):
            try:
                candidate = snapshot_getter(current.date().isoformat(), now=current)
                if isinstance(candidate, dict):
                    calendar_snapshot = candidate
            except Exception:
                calendar_snapshot = {}
        calendar_events = calendar_snapshot.get("effective_events", calendar_snapshot.get("events", []))
        if isinstance(calendar_events, list):
            for item in calendar_events:
                if not isinstance(item, dict):
                    continue
                if str(item.get("status") or "confirmed") not in {"confirmed", "active"}:
                    continue
                has_calendar_context = True
                joined = _single_line(
                    f"{item.get('title', '')} {item.get('note', '')} {item.get('description', '')}",
                    180,
                )
                if any(token in joined for token in holiday_tokens):
                    has_calendar_holiday_signal = True
                if any(token in joined for token in ("上学", "上课", "放学", "学校", "上班", "通勤", "值班", "会议", "考试", "补课")):
                    has_calendar_school_work = True
                if any(token in joined for token in override_tokens):
                    has_override_signal = True
        for entry in today_dates:
            if not isinstance(entry, dict):
                continue
            joined = _single_line(
                f"{entry.get('title', '')} {entry.get('type', '')} {entry.get('note', '')}",
                160,
            )
            if any(token in joined for token in holiday_tokens):
                has_holiday_signal = True
            if any(token in joined for token in override_tokens):
                has_override_signal = True
        schedule_prompt = self._get_schedule_planning_prompt()
        if any(token in schedule_prompt for token in override_tokens):
            has_override_signal = True
        return {
            "is_weekend": is_weekend,
            "has_holiday_signal": has_holiday_signal or has_calendar_holiday_signal,
            "has_override_signal": has_override_signal,
            "has_calendar_context": has_calendar_context,
            "has_calendar_school_work": has_calendar_school_work,
            "calendar_snapshot": calendar_snapshot,
        }

    def _plan_conflicts_with_calendar(self, items: list[dict[str, str]], now: datetime | None = None) -> bool:
        """Report only explicit unresolved calendar conflicts.

        A plan can be a reasonable interpretation of a phase, a rhythm, or a
        user correction.  Keyword matching (for example, treating every
        ``上学`` row as invalid during a vacation) made the calendar a hidden
        hard filter and caused the companion to rewrite its own life.  The
        planner prompt now receives the timeline and can resolve ambiguity in
        prose; this hook is reserved for genuinely unresolved overlaps.
        """

        if not items:
            return False
        getter = getattr(self, "_agenda_calendar_timeline", None)
        if not callable(getter):
            return False
        try:
            timeline = getter(now=(now or self._environment_now()), history_days=0, horizon_days=1)
        except Exception:
            return False
        conflicts = timeline.get("conflicts") if isinstance(timeline, dict) else []
        return any(isinstance(item, dict) and item.get("unresolved") for item in (conflicts or []))

    def _is_micro_plan_activity(self, text: str) -> bool:
        normalized = _single_line(text, 160)
        if not normalized:
            return False
        length = len(normalized)
        instant_markers = (
            "看了一眼",
            "瞥了一眼",
            "拍了一下",
            "拍了下",
            "翻了个身",
            "揉了揉",
            "抬头看",
            "关掉闹钟",
            "叫了一声",
            "应了一声",
            "顺手点开",
        )
        if any(marker in normalized for marker in instant_markers):
            return length <= 30
        generic_short_markers = ("一下", "一眼", "一瞬", "顺手", "刚好", "忽然")
        continuity_markers = (
            "慢慢",
            "继续",
            "待着",
            "坐着",
            "趴着",
            "整理",
            "收拾",
            "吃饭",
            "洗漱",
            "发呆",
            "看剧",
            "听歌",
            "出门",
            "路上",
            "吹风",
            "睡前",
            "饭后",
            "午休",
            "收尾",
        )
        if any(marker in normalized for marker in generic_short_markers) and not any(
            marker in normalized for marker in continuity_markers
        ):
            return length <= 22
        return False

    def _plan_has_excess_micro_segments(self, items: list[dict[str, str]]) -> bool:
        if not items:
            return False
        micro_count = 0
        for item in items:
            if not isinstance(item, dict):
                continue
            if self._is_micro_plan_activity(str(item.get("activity") or "")):
                micro_count += 1
        return micro_count >= max(2, len(items) // 4)

    def _is_abstract_plan_activity(self, text: str) -> bool:
        normalized = _single_line(text, 180)
        if not normalized:
            return False
        concrete_markers = (
            "起床", "赖床", "洗漱", "吃", "喝", "走", "坐", "趴", "靠", "收拾", "整理",
            "看", "听", "出门", "回家", "写", "刷", "逛", "吹风", "洗碗", "看剧", "躺",
            "翻", "换鞋", "背上", "拿着", "关灯", "开窗", "买", "收声", "聊天", "做饭",
        )
        abstract_markers = (
            "思绪", "心情", "气息", "余韵", "碎片", "温柔", "柔软", "飘忽", "微醺", "依恋",
            "恍惚", "生活感", "画面", "感觉", "梦里", "脑海里", "最后闪过", "随着光线",
        )
        if any(marker in normalized for marker in concrete_markers):
            abstract_count = sum(1 for marker in abstract_markers if marker in normalized)
            return abstract_count >= 3 and len(normalized) <= 22
        abstract_count = sum(1 for marker in abstract_markers if marker in normalized)
        return abstract_count >= 2

    def _plan_has_excess_abstract_segments(self, items: list[dict[str, str]]) -> bool:
        if not items:
            return False
        abstract_count = 0
        for item in items:
            if not isinstance(item, dict):
                continue
            if self._is_abstract_plan_activity(str(item.get("activity") or "")):
                abstract_count += 1
        return abstract_count >= max(2, len(items) // 3)

    @staticmethod
    def _plan_activity_signature(text: str) -> str:
        normalized = _single_line(text, 180)
        if not normalized:
            return ""
        category_rules = (
            ("起床", ("起床", "醒来", "睡醒", "赖床", "闹钟", "被窝")),
            ("洗漱", ("洗漱", "刷牙", "洗脸", "梳头", "镜子", "卫生间")),
            ("早餐", ("早餐", "早饭", "面包", "牛奶", "豆浆", "粥")),
            ("正餐", ("午饭", "晚饭", "吃饭", "做饭", "干饭", "饭桌", "摆碗", "点外卖")),
            ("通勤出门", ("出门", "路上", "公交", "地铁", "校门", "换鞋", "背包", "打车")),
            ("校园课程", ("上课", "下课", "教室", "课间", "老师", "同桌", "黑板", "班会")),
            ("补课考试", ("补课", "考试", "测验", "卷子", "复习", "考场", "错题")),
            ("学习作业", ("作业", "自习", "刷题", "数学", "英语", "课本", "笔记", "书包")),
            ("工作事务", ("上班", "工位", "会议", "打卡", "下班", "同事", "项目", "文档")),
            ("家务整理", ("收拾", "整理", "扫地", "洗碗", "洗衣", "归位", "桌面", "房间")),
            ("休息摸鱼", ("午休", "休息", "摸鱼", "躺", "趴", "沙发", "发呆", "缓一会")),
            ("娱乐放松", ("看剧", "追番", "游戏", "刷短视频", "听歌", "小说", "漫画")),
            ("社交互动", ("聊天", "朋友", "家人", "消息", "电话", "群聊", "回复", "打开对话框")),
            ("购物外食", ("买", "便利店", "超市", "奶茶", "饮料", "小吃", "逛")),
            ("户外散步", ("散步", "走一段", "吹风", "公园", "楼下", "河边", "阳台", "开窗")),
            ("运动身体", ("运动", "跑步", "拉伸", "散操", "瑜伽", "出汗")),
            ("洗澡睡前", ("洗澡", "睡前", "关灯", "上床", "准备睡", "入睡", "枕头")),
        )
        hits: list[str] = []
        for label, tokens in category_rules:
            if any(token in normalized for token in tokens):
                hits.append(label)
            if len(hits) >= 2:
                break
        if hits:
            return "+".join(hits)
        compact = re.sub(r"[，。！？、,.!?；;：:\s]+", "", normalized)
        return compact[:8]

    def _plan_signature(self, items: list[dict[str, Any]]) -> list[str]:
        signatures: list[str] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            signature = self._plan_activity_signature(
                f"{item.get('activity', '')} {item.get('message_seed', '')}"
            )
            if signature:
                signatures.append(signature)
        return signatures

    def _format_recent_daily_plan_history_for_prompt(self, limit: int = 5) -> str:
        history = self._recent_daily_plan_history_entries()
        rows: list[str] = []
        for entry in history[-limit:]:
            if not isinstance(entry, dict):
                continue
            date_text = _single_line(entry.get("date"), 16)
            signatures = entry.get("signature")
            if not isinstance(signatures, list):
                signatures = []
            samples = entry.get("sample")
            if not isinstance(samples, list):
                samples = []
            skeleton = " / ".join(_single_line(part, 20) for part in signatures[:12] if part)
            sample_text = "；".join(_single_line(part, 46) for part in samples[:4] if part)
            if skeleton:
                line = f"- {date_text}: {skeleton}"
                if sample_text:
                    line += f"\n  代表活动: {sample_text}"
                rows.append(line)
        return "\n".join(rows) if rows else "暂无最近日程历史。"

    def _plan_repetition_score(self, items: list[dict[str, str]]) -> float:
        signatures = self._plan_signature(items)
        if not signatures:
            return 0.0
        current_set = set(signatures)
        history = self._recent_daily_plan_history_entries()
        best_score = 0.0
        for entry in history[-5:]:
            if not isinstance(entry, dict):
                continue
            old_signatures = entry.get("signature")
            if not isinstance(old_signatures, list) or not old_signatures:
                continue
            old_values = [str(value) for value in old_signatures if value]
            old_set = set(old_values)
            if not old_set:
                continue
            jaccard = len(current_set & old_set) / max(1, len(current_set | old_set))
            paired = min(len(signatures), len(old_values))
            same_positions = 0
            for idx in range(paired):
                if signatures[idx] == old_values[idx]:
                    same_positions += 1
            ordered = same_positions / max(1, paired)
            best_score = max(best_score, jaccard * 0.65 + ordered * 0.35)
        return best_score

    def _plan_is_too_repetitive(self, items: list[dict[str, str]]) -> bool:
        if not items:
            return False
        signatures = self._plan_signature(items)
        if len(signatures) >= 6:
            dominant_count = max(signatures.count(signature) for signature in set(signatures))
            if dominant_count >= max(4, len(signatures) // 2 + 1):
                return True
        return self._plan_repetition_score(items) >= 0.62

    def _daily_plan_history_entry(self, plan: dict[str, Any]) -> dict[str, Any] | None:
        if not isinstance(plan, dict):
            return None
        items = plan.get("items")
        if not isinstance(items, list) or not items:
            return None
        plan_date = _single_line(plan.get("date"), 16) or _today_key()
        sample: list[str] = []
        compact_items: list[dict[str, str]] = []
        for item in items[:6]:
            if not isinstance(item, dict):
                continue
            time_text = _single_line(item.get("time"), 8)
            activity = _single_line(item.get("activity"), 52)
            if activity:
                sample.append(f"{time_text} {activity}".strip())
        for item in items[:18]:
            if not isinstance(item, dict):
                continue
            compact_items.append(
                {
                    "time": _single_line(item.get("time"), 20),
                    "activity": _single_line(item.get("activity") or item.get("title"), 180),
                    "mood": _single_line(item.get("mood"), 80),
                    "message_seed": _single_line(item.get("message_seed"), 220),
                }
            )
        entry = {
            "date": plan_date,
            "generated_at": _single_line(plan.get("generated_at"), 20) or self._environment_now().strftime("%Y-%m-%d %H:%M"),
            "source": _single_line(plan.get("source"), 16),
            "signature": self._plan_signature(items),
            "sample": sample,
            "items": compact_items,
        }
        return entry

    def _recent_daily_plan_history_entries(self) -> list[dict[str, Any]]:
        history = self.data.get("daily_plan_history", [])
        entries = [entry for entry in history if isinstance(entry, dict)] if isinstance(history, list) else []
        known_dates = {_single_line(entry.get("date"), 16) for entry in entries}
        current_entry = self._daily_plan_history_entry(self.data.get("daily_plan", {}))
        if current_entry and _single_line(current_entry.get("date"), 16) not in known_dates:
            entries.append(current_entry)
        return entries

    def _remember_daily_plan_history(self, plan: dict[str, Any]) -> None:
        entry = self._daily_plan_history_entry(plan)
        if not entry:
            return
        plan_date = _single_line(entry.get("date"), 16)
        history = self.data.setdefault("daily_plan_history", [])
        if not isinstance(history, list):
            history = []
            self.data["daily_plan_history"] = history
        history[:] = [
            old
            for old in history
            if not (isinstance(old, dict) and _single_line(old.get("date"), 16) == plan_date)
        ]
        history.append(entry)
        del history[:-10]

    def _add_important_date_entry(self, value: str) -> tuple[bool, str]:
        parts = value.split(maxsplit=2)
        if len(parts) < 2:
            return False, "格式：陪伴 日期添加 <标题> <YYYY-MM-DD或MM-DD> [备注]"
        title = _single_line(parts[0], 40)
        date_text = _single_line(parts[1], 20)
        note = _single_line(parts[2], 120) if len(parts) >= 3 else ""
        parsed = self._parse_date_value(date_text)
        if parsed is None:
            return False, "日期格式不对,请用 YYYY-MM-DD 或 MM-DD。"
        repeat_yearly = len(date_text) == 5
        entry = {
            "id": f"date-{int(_now_ts())}-{random.randint(1000, 9999)}",
            "title": title,
            "date": date_text,
            "type": "重要日期",
            "note": note,
            "enabled": True,
            "repeat_yearly": repeat_yearly,
                "remind_days": runtime_persona_setting(self, "important_date_lookahead_days", 7),
            "priority": 50,
            "created_at": self._environment_now().strftime("%Y-%m-%d %H:%M"),
        }
        self.data.setdefault("important_dates", []).append(entry)
        return True, f"已添加重要日期：{title}｜{date_text}"

    def _remove_important_date_entry(self, value: str) -> str:
        keyword = _single_line(value, 40)
        if not keyword:
            return "请提供要删除的日期标题关键词。"
        entries = self.data.setdefault("important_dates", [])
        if not isinstance(entries, list):
            self.data["important_dates"] = []
            return "重要日期列表为空。"
        kept = []
        removed = []
        for entry in entries:
            title = str(entry.get("title", "")) if isinstance(entry, dict) else ""
            if keyword in title:
                removed.append(title)
            else:
                kept.append(entry)
        self.data["important_dates"] = kept
        if not removed:
            return "没有找到匹配的重要日期。"
        return "已删除：\n" + "\n".join(f"- {item}" for item in removed)

    def _format_important_dates(self) -> str:
        entries = self.data.get("important_dates", [])
        if not isinstance(entries, list) or not entries:
            return "还没有重要日期。"
        lines = ["重要日期条目："]
        today = self._environment_now().date()
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            next_day = self._next_occurrence(entry)
            suffix = ""
            if next_day:
                days = (next_day - today).days
                suffix = "｜今天" if days == 0 else f"｜{days} 天后"
            enabled = "启用" if entry.get("enabled", True) else "停用"
            repeat = "每年" if entry.get("repeat_yearly", True) else "一次"
            lines.append(
                f"- {entry.get('title')}｜{entry.get('date')}｜{repeat}｜{enabled}{suffix}｜{entry.get('note', '')}"
            )
        return "\n".join(lines)

    def _synchronize_body_cycle_strategy(self, conditions: list[Any], now: float) -> list[Any]:
        advanced_enabled = self._advanced_cycle_enabled()
        desired_mode = "advanced" if advanced_enabled else "legacy"
        previous_mode = str(self.data.get("body_cycle_strategy_mode") or "")
        kept: list[Any] = []
        removed = 0
        for cond in conditions:
            if not isinstance(cond, dict) or str(cond.get("kind") or "") != "body_cycle":
                kept.append(cond)
                continue
            phase = str(cond.get("phase") or self._infer_body_cycle_phase(str(cond.get("label") or "")))
            is_advanced = phase in self._ADVANCED_CYCLE_PHASES
            if is_advanced != advanced_enabled:
                removed += 1
                continue
            cond["phase"] = phase
            kept.append(cond)
        if removed:
            existing_meta = self.data.get("body_cycle_state")
            # A legacy condition may still be present while an advanced
            # timeline has already been anchored. Remove only the incompatible
            # condition in that case; resetting the anchor would move the
            # user back to day one after a restart or migration.
            keep_continuous_state = (
                desired_mode == "advanced"
                and isinstance(existing_meta, dict)
                and _safe_float(existing_meta.get("cycle_anchor_ts"), 0) > 0
            )
            if not keep_continuous_state:
                self.data.pop("body_cycle_state", None)
            logger.info(
                "周期策略切换，已清理不兼容旧状态: mode=%s removed=%s",
                desired_mode,
                removed,
            )
        self.data["body_cycle_strategy_mode"] = desired_mode

        if not advanced_enabled:
            return kept

        offset = _safe_int(runtime_persona_setting(self, "advanced_cycle_start_offset", 0), 0, 0, 180)
        meta = self.data.get("body_cycle_state")
        meta = dict(meta) if isinstance(meta, dict) else {}
        if offset <= 0:
            if meta.get("manual_offset_signature"):
                for key in ("manual_offset", "manual_offset_signature", "manual_offset_phase", "manual_offset_day_in_phase"):
                    meta.pop(key, None)
                self.data["body_cycle_state"] = meta
            has_cycle_condition = any(
                isinstance(cond, dict) and str(cond.get("kind") or "") == "body_cycle"
                for cond in kept
            )
            anchor_ts = _safe_float(meta.get("cycle_anchor_ts"), 0)
            if not has_cycle_condition and anchor_ts <= 0:
                condition = self._advanced_cycle_condition(
                    "menstrual",
                    cause="六阶段周期策略首次启用，自然进入第一周期",
                )
                kept.append(condition)
                self._record_body_cycle_episode(condition)
                logger.info("六阶段周期策略首次启用，已从月经期第 1 天开始推进")
            return kept

        signature = self._advanced_cycle_offset_signature(offset)
        if meta.get("manual_offset_signature") == signature:
            return kept

        kept = [
            cond
            for cond in kept
            if not (isinstance(cond, dict) and str(cond.get("kind") or "") == "body_cycle")
        ]
        phase, day_in_phase = self._advanced_cycle_position_from_offset(offset)
        remaining_days = self._advanced_cycle_phase_days(phase) - day_in_phase + 1
        condition = self._advanced_cycle_condition(
            phase,
            cause="管理员设置了周期起始日",
            duration_hours=remaining_days * 24,
        )
        kept.append(condition)
        self._record_body_cycle_episode(condition)
        meta = self.data.get("body_cycle_state")
        meta = dict(meta) if isinstance(meta, dict) else {}
        meta.update(
            {
                "manual_offset": offset,
                "manual_offset_signature": signature,
                "manual_offset_phase": phase,
                "manual_offset_day_in_phase": day_in_phase,
                "cycle_anchor_ts": max(0.0, now - (offset - 1) * 86400),
                "strategy": "advanced",
            }
        )
        self.data["body_cycle_state"] = meta
        logger.info(
            "已应用六阶段周期起始日: offset=%s phase=%s phase_day=%s previous_mode=%s",
            offset,
            phase,
            day_in_phase,
            previous_mode or "unknown",
        )
        return kept

    def _cleanup_expired_conditions(self) -> set[str]:
        now = _now_ts()
        had_body_cycle_state = "body_cycle_state" in self.data
        conditions = self.data.setdefault("state_conditions", [])
        if not isinstance(conditions, list):
            self.data["state_conditions"] = []
            return set()
        profile = self._persona_state_profile()
        if not profile.get("allow_cycle", False):
            before_count = len(conditions)
            conditions = [
                cond for cond in conditions
                if not isinstance(cond, dict) or str(cond.get("kind") or "") not in {"body_cycle", "cycle_discomfort"}
            ]
            removed_count = before_count - len(conditions)
            if removed_count:
                self.data.pop("body_cycle_state", None)
                logger.info("生理期模拟已关闭，清理旧周期状态: removed=%s", removed_count)
        else:
            conditions = self._synchronize_body_cycle_strategy(conditions, now)
            conditions = self._repair_body_cycle_conditions(conditions, now)
            if not self._advanced_cycle_enabled() or not bool(
                runtime_persona_setting(self, "advanced_cycle_discomfort_simulation", False)
            ):
                before_count = len(conditions)
                conditions = [
                    cond
                    for cond in conditions
                    if not isinstance(cond, dict) or str(cond.get("kind") or "") != "cycle_discomfort"
                ]
                if len(conditions) < before_count:
                    logger.info(
                        "不适模拟已关闭，清理残留经期不适状态: removed=%s",
                        before_count - len(conditions),
                    )
        active = []
        expired = []
        for cond in conditions:
            if not isinstance(cond, dict):
                continue
            if _safe_float(cond.get("end_ts"), 0) > now:
                active.append(cond)
            else:
                expired.append(cond)
        for cond in expired:
            active.extend(self._spawn_followup_conditions(cond))
        active = self._reconcile_advanced_cycle_condition(active, now)
        active = self._prune_active_hunger_conditions(active, now)
        self.data["state_conditions"] = active
        return {
            "body_cycle_state"
        } if had_body_cycle_state and "body_cycle_state" not in self.data else set()

    def _prune_active_hunger_conditions(self, conditions: list[dict[str, Any]], now: float) -> list[dict[str, Any]]:
        hunger_items = [
            cond for cond in conditions
            if isinstance(cond, dict)
            and str(cond.get("kind") or "") == "hunger"
            and _safe_float(cond.get("start_ts"), 0) <= now < _safe_float(cond.get("end_ts"), 0)
        ]
        if len(hunger_items) <= 1:
            return conditions
        hunger_items.sort(key=lambda item: (_safe_float(item.get("start_ts"), 0), _safe_float(item.get("end_ts"), 0)), reverse=True)
        keep_id = hunger_items[0].get("id")
        pruned: list[dict[str, Any]] = []
        for cond in conditions:
            if isinstance(cond, dict) and str(cond.get("kind") or "") == "hunger" and cond.get("id") != keep_id:
                continue
            pruned.append(cond)
        logger.info("已清理重复饥饿状态: kept=%s removed=%s", keep_id or "-", len(hunger_items) - 1)
        return pruned

    def _repair_body_cycle_conditions(self, conditions: list[Any], now: float) -> list[dict[str, Any]]:
        repaired: list[dict[str, Any]] = []
        active_cycles: list[dict[str, Any]] = []
        last_cycle_end = 0.0
        for cond in conditions:
            if not isinstance(cond, dict):
                continue
            if str(cond.get("kind") or "") != "body_cycle":
                repaired.append(cond)
                continue
            label = _single_line(cond.get("label"), 80)
            phase = str(cond.get("phase") or self._infer_body_cycle_phase(label))
            cond["phase"] = phase
            start_ts = _safe_float(cond.get("start_ts"), now)
            if start_ts <= 0:
                start_ts = now
                cond["start_ts"] = start_ts
            max_hours = self._body_cycle_max_hours(phase, label)
            max_end_ts = start_ts + max_hours * 3600
            end_ts = _safe_float(cond.get("end_ts"), max_end_ts)
            if end_ts <= 0:
                end_ts = max_end_ts
            if end_ts > max_end_ts:
                end_ts = max_end_ts
                cond["end_ts"] = end_ts
                cond["duration_hours"] = max_hours
            if not cond.get("episode_key"):
                cond["episode_key"] = f"body-cycle-{self._environment_fromtimestamp(start_ts).strftime('%Y-%m-%d')}"
            last_cycle_end = max(last_cycle_end, end_ts)
            if start_ts <= now < end_ts:
                active_cycles.append(cond)
            repaired.append(cond)

        if len(active_cycles) > 1:
            active_cycles.sort(key=lambda item: _safe_float(item.get("start_ts"), 0), reverse=True)
            keep_id = active_cycles[0].get("id")
            filtered: list[dict[str, Any]] = []
            for cond in repaired:
                if str(cond.get("kind") or "") == "body_cycle" and cond.get("id") != keep_id:
                    cond["end_ts"] = min(_safe_float(cond.get("end_ts"), now), now - 1)
                filtered.append(cond)
            repaired = filtered

        if last_cycle_end > 0:
            meta = self.data.get("body_cycle_state")
            if not isinstance(meta, dict):
                meta = {}
            expected_ts = _safe_float(meta.get("next_expected_start_ts"), 0)
            base_start = _safe_float(meta.get("last_start_ts"), 0)
            if base_start <= 0:
                base_start = max(0.0, last_cycle_end - 4 * 86400)
            if self._advanced_cycle_enabled():
                if expected_ts <= 0:
                    expected_ts = base_start + self._advanced_cycle_total_days() * 86400
            else:
                if expected_ts <= 0 or expected_ts <= last_cycle_end:
                    expected_ts = base_start + 28 * 86400
                expected_ts = max(expected_ts, last_cycle_end + 18 * 86400)
            meta.update(
                {
                    "last_end_ts": max(_safe_float(meta.get("last_end_ts"), 0), last_cycle_end),
                    "next_expected_start_ts": expected_ts,
                }
            )
            self.data["body_cycle_state"] = meta
        return repaired

    def _reconcile_advanced_cycle_condition(self, conditions: list[dict[str, Any]], now: float) -> list[dict[str, Any]]:
        """Align the active cycle condition with the anchored continuous timeline.

        The anchor always knows the true current phase and day. When the bot
        was offline or a transition condition was spawned late, this replaces
        the stale condition with one positioned exactly on the timeline so its
        energy and mood effects never lag behind the displayed phase.

        Args:
            conditions: Currently active condition list after follow-up spawns.
            now: Current unix timestamp.

        Returns:
            The adjusted condition list.
        """
        if not self._advanced_cycle_enabled():
            return conditions
        meta = self.data.get("body_cycle_state")
        anchor_ts = _safe_float(meta.get("cycle_anchor_ts"), 0) if isinstance(meta, dict) else 0
        if anchor_ts <= 0:
            return conditions
        expected_phase, day_in_phase = self._advanced_cycle_position_from_offset(
            int((now - anchor_ts) // 86400) + 1
        )
        phase_days = self._advanced_cycle_phase_days(expected_phase)
        phase_start = anchor_ts + (self._advanced_cycle_day_of_phase(expected_phase, 1) - 1) * 86400
        active_cycles = [
            cond
            for cond in conditions
            if isinstance(cond, dict)
            and str(cond.get("kind") or "") == "body_cycle"
            and _safe_float(cond.get("start_ts"), 0) <= now < _safe_float(cond.get("end_ts"), 0)
        ]
        if len(active_cycles) == 1:
            cond = active_cycles[0]
            cond_start = _safe_float(cond.get("start_ts"), 0)
            if str(cond.get("phase") or "") == expected_phase and abs(cond_start - phase_start) < 6 * 3600:
                return conditions
        kept = [
            cond
            for cond in conditions
            if not (isinstance(cond, dict) and str(cond.get("kind") or "") == "body_cycle")
        ]
        condition = self._advanced_cycle_condition(
            expected_phase,
            cause="周期阶段自然推进",
        )
        condition["start_ts"] = phase_start
        condition["duration_hours"] = phase_days * 24
        condition["end_ts"] = phase_start + phase_days * 24 * 3600
        kept.append(condition)
        self._record_body_cycle_episode(condition)
        logger.info(
            "已对齐六阶段周期状态: phase=%s phase_start=%s day_in_phase=%s",
            expected_phase,
            self._environment_fromtimestamp(phase_start).strftime("%Y-%m-%d %H:%M"),
            day_in_phase,
        )
        return kept

    def _spawn_followup_conditions(self, cond: dict[str, Any]) -> list[dict[str, Any]]:
        choice = self._pick_condition_transition(cond)
        if not choice or choice == "stable":
            return []
        followup = self._build_transition_condition(choice, cond)
        if isinstance(followup, dict) and str(followup.get("kind") or "") == "body_cycle":
            self._record_body_cycle_episode(followup)
        return [followup] if followup else []

    def _pick_condition_transition(self, cond: dict[str, Any]) -> str:
        options = cond.get("transition_options", [])
        if not isinstance(options, list) or not options:
            return ""
        weighted: list[tuple[str, float]] = []
        cause = _single_line(cond.get("cause"), 120)
        intensity = _safe_int(cond.get("intensity"), 50, 0, 100)
        weather_text = self._weather_summary_text(self.data.get("daily_weather", {}))
        care_notes = cond.get("care_notes", [])
        care_count = len(care_notes) if isinstance(care_notes, list) else 0
        for option in options:
            if not isinstance(option, dict):
                continue
            target = str(option.get("to") or "").strip()
            weight = float(option.get("base_weight") or 0)
            if not target or weight <= 0:
                continue
            if target == "recovery_afterglow":
                weight += min(0.22, care_count * 0.08)
                if "提醒" in cause or "用户" in cause:
                    weight += 0.06
            elif target == "health_tail":
                if intensity >= 75:
                    weight += 0.1
                if any(token in cause for token in ("透支", "失眠")):
                    weight += 0.08
                if any(token in weather_text for token in ("降雨", "小雨", "中雨", "大雨", "冷", "风")):
                    weight += 0.05
                weight -= min(0.12, care_count * 0.05)
            elif target == "sleep_afterglow":
                weight += min(0.16, care_count * 0.05)
            elif target == "sleep_tail":
                if intensity >= 80:
                    weight += 0.08
                if any(token in cause for token in ("失眠", "睡")):
                    weight += 0.04
            weighted.append((target, max(0.0, weight)))
        total = sum(weight for _, weight in weighted)
        if total <= 0:
            return ""
        pick = random.random() * total
        cursor = 0.0
        for target, weight in weighted:
            cursor += weight
            if pick <= cursor:
                return target
        return weighted[-1][0]

    def _build_transition_condition(self, target: str, cond: dict[str, Any]) -> dict[str, Any] | None:
        cause = _single_line(cond.get("cause"), 120)
        if target == "recovery_afterglow":
            label = "不适缓解后的轻度回升"
            if cause:
                label = "不适正在缓解,状态明显回升"
            return self._make_condition(
                kind="recovery_afterglow",
                title="恢复后的回弹",
                label=label,
                mood="轻快",
                energy_delta=10,
                duration_hours=12,
                intensity=68,
                cause="前序不适开始缓解",
                phase="afterglow",
            )
        if target == "health_tail":
            return self._make_condition(
                kind="health_tail",
                title="恢复尾声",
                label="整体好转,但仍有轻微虚弱残留",
                mood="平缓",
                energy_delta=-4,
                duration_hours=10,
                intensity=48,
                cause="恢复中,体力尚未完全回满",
                phase="tail",
            )
        if target == "sleep_afterglow":
            return self._make_condition(
                kind="sleep_afterglow",
                title="补回来一点精神",
                label="睡意缓解后的轻度回升",
                mood="轻松",
                energy_delta=8,
                duration_hours=8,
                intensity=60,
                cause="前序失眠或浅睡影响减弱",
                phase="afterglow",
            )
        if target == "sleep_tail":
            return self._make_condition(
                kind="sleep_tail",
                title="迟钝尾声",
                label="睡眠影响减弱,但反应仍略慢",
                mood="安静",
                energy_delta=-3,
                duration_hours=6,
                intensity=42,
                cause="睡眠债仍有轻微残留",
                phase="tail",
            )
        if target == "soft_afterglow":
            return self._make_condition(
                kind="soft_afterglow",
                title="被关心后的余温",
                label="收到关心反馈后的柔和余波",
                mood="柔和",
                energy_delta=4,
                duration_hours=4,
                intensity=48,
                cause="用户关心反馈仍有轻度影响",
                phase="afterglow",
            )
        if target == "body_period":
            return self._make_condition(
                kind="body_cycle",
                title="周期",
                label="处于生理期,身体舒适度与能量偏低",
                mood="疲惫",
                energy_delta=-18,
                duration_hours=72,
                intensity=64,
                cause="周期阶段自然推进",
                phase="period",
                episode_key=_single_line(cond.get("episode_key"), 40),
                transition_options=[
                    {"to": "body_recovery", "base_weight": 0.65},
                    {"to": "stable", "base_weight": 0.35},
                ],
            )
        if target == "body_recovery":
            return self._make_condition(
                kind="body_cycle",
                title="周期",
                label="生理期后,慢慢回到稳定状态",
                mood="松弛",
                energy_delta=-5,
                duration_hours=24,
                intensity=48,
                cause="周期阶段自然推进",
                phase="recovery",
                episode_key=_single_line(cond.get("episode_key"), 40),
                transition_options=[{"to": "stable", "base_weight": 1.0}],
            )
        advanced_targets = {
            "body_menstrual": "menstrual",
            "body_follicular": "follicular",
            "body_pre_ovulation": "pre_ovulation",
            "body_ovulation": "ovulation",
            "body_luteal": "luteal",
            "body_pms": "pms",
        }
        if target in advanced_targets and self._advanced_cycle_enabled():
            return self._advanced_cycle_condition(
                advanced_targets[target],
                episode_key=_single_line(cond.get("episode_key"), 40),
            )
        return None

    def _get_active_conditions(self) -> list[dict[str, Any]]:
        now = _now_ts()
        conditions = self.data.get("state_conditions", [])
        if not isinstance(conditions, list):
            return []
        active = []
        for cond in conditions:
            if not isinstance(cond, dict):
                continue
            start_ts = _safe_float(cond.get("start_ts"), 0)
            end_ts = _safe_float(cond.get("end_ts"), 0)
            if start_ts <= now < end_ts:
                active.append(cond)
        return active

    def _compose_state_from_conditions(self, weather: dict[str, Any] | None = None) -> dict[str, Any]:
        profile = self._persona_state_profile()
        active = [
            cond for cond in self._get_active_conditions()
            if self._state_condition_allowed(str(cond.get("kind") or ""), profile)
        ]
        values = self._base_state_values(profile)
        weather_text = self._weather_summary_text(weather)
        energy = 75
        composed_at = _now_ts()
        mood_candidates = []
        health_cause = ""
        for cond in active:
            kind = str(cond.get("kind") or "")
            if kind in values:
                values[kind] = _single_line(cond.get("label"), 80)
            energy += self._condition_effective_energy_delta(cond, now=composed_at)
            mood = _single_line(cond.get("mood"), 20)
            if mood and mood != "平稳":
                intensity = _safe_int(cond.get("intensity"), 50, 0, 100)
                if kind == "memory_afterglow":
                    intensity = max(0, round(intensity * self._memory_afterglow_decay(cond, now=composed_at)))
                mood_candidates.append((mood, intensity))
            if kind == "health" and not health_cause:
                health_cause = _single_line(cond.get("cause"), 120)
        remembered_dream = self._remembered_daily_dream_label()
        if values.get("dream") == "没有记住梦" and remembered_dream:
            values["dream"] = remembered_dream
        existing_state = self.data.get("daily_state")
        existing_override_ts = 0.0
        if isinstance(existing_state, dict) and existing_state.get("date") == _today_key():
            existing_override_ts = _safe_float(existing_state.get("location_override_ts"), 0)
        override_active = existing_override_ts > 0 and _now_ts() - existing_override_ts < 4 * 3600
        if override_active:
            inferred_location = self._current_location_state_text(existing_state)
        else:
            inferred_location = self._current_location_state_text({"location": values.get("location", "")})
        if inferred_location:
            values["location"] = inferred_location
        energy = max(10, min(100, energy))
        mood_bias = (
            sorted(mood_candidates, key=lambda item: item[1], reverse=True)[0][0]
            if mood_candidates else "平稳"
        )
        cycle_runtime: dict[str, Any] = {}
        if self._advanced_cycle_enabled() and profile.get("allow_cycle", False):
            cycle_runtime = self._advanced_cycle_runtime()
            if cycle_runtime:
                values["body_cycle"] = (
                    f"{cycle_runtime.get('phase_name', '周期')} 第{cycle_runtime.get('day_in_phase', 1)}天"
                )
                discomfort = self._active_cycle_discomfort_conditions()
                if discomfort:
                    cycle_runtime["discomfort"] = discomfort
        note = self._build_state_note(
            values["sleep"],
            values["dream"],
            values["health"],
            values["hunger"],
            values["body_cycle"],
            weather_text,
            mood_bias,
            energy,
            health_cause,
        )
        result = {
            "date": _today_key(),
            **values,
            "weather": weather_text,
            "mood_bias": mood_bias,
            "energy": energy,
            "note": note,
            "cycle_runtime": cycle_runtime,
            "conditions": active,
            "affect_modulation": compose_affect_modulation(active, now=composed_at),
        }
        if override_active:
            result["location_override_ts"] = existing_override_ts
            result["location_source"] = "dialogue_override"
        return result

    @staticmethod
    def _memory_afterglow_decay(cond: dict[str, Any], *, now: float) -> float:
        if str(cond.get("kind") or "") != "memory_afterglow":
            return 1.0
        start_ts = _safe_float(cond.get("start_ts"), now)
        half_life = max(60.0, min(86400.0, _safe_float(cond.get("half_life_seconds"), 1800.0)))
        age = max(0.0, now - start_ts)
        return max(0.0, min(1.0, 0.5 ** (age / half_life)))

    def _condition_effective_energy_delta(self, cond: dict[str, Any], *, now: float) -> int:
        base = _safe_int(cond.get("energy_delta"), 0, -100, 100)
        if str(cond.get("kind") or "") != "memory_afterglow":
            return base
        return int(round(base * self._memory_afterglow_decay(cond, now=now)))

    def _build_state_note(
        self,
        sleep: str,
        dream: str,
        health: str,
        hunger: str,
        body_cycle: str,
        weather: str,
        mood_bias: str,
        energy: int,
        health_cause: str = "",
    ) -> str:
        if energy < 35:
            pace = "今天能量很低,日程应更轻、更慢,主动消息也要更短。"
        elif energy < 55:
            pace = "今天能量偏低,适合少量任务和更多停顿。"
        elif energy > 80:
            pace = "今天能量不错,可以安排一些需要专注的事情。"
        else:
            pace = "今天能量中等,适合保持温和节奏。"
        weather_text = str(weather or "").strip()
        weather_text = weather_text.rstrip("。！？!?,,；; ")
        weather_part = f"天气：{weather_text}。" if weather_text and weather_text != "暂无天气信息" else ""
        cause_part = f" 身体不太舒服更像是因为{health_cause}。" if health_cause else ""
        detail_parts = []
        if sleep and sleep not in {"睡眠平稳", "睡得很踏实"}:
            detail_parts.append(f"睡眠：{sleep}")
        if dream and dream != "没有记住梦":
            detail_parts.append(f"梦境：{dream}")
        if health and health != "状态正常" and not self._is_inapplicable_state_text(health):
            detail_parts.append(f"健康：{health}")
        if hunger and hunger not in {"饥饿感平稳", "无饥饿感"} and not self._is_inapplicable_state_text(hunger):
            detail_parts.append(f"饥饿：{hunger}")
        if body_cycle and body_cycle not in {"无明显周期影响", "不处于生理期"} and not self._is_inapplicable_state_text(body_cycle):
            detail_parts.append(f"周期：{body_cycle}")
        detail_text = (" " + "；".join(detail_parts) + "。") if detail_parts else ""
        return (
            f"{pace} 情绪底色偏{mood_bias}。"
            f"{weather_part}{cause_part}"
            f"{detail_text}"
        )

    def _is_daily_plan_due(self) -> bool:
        plan_minutes = self._parse_hhmm_to_minutes(runtime_persona_setting(self, "daily_plan_time", "07:30"))
        if plan_minutes is None:
            plan_minutes = 7 * 60 + 30
        now = self._environment_now()
        return now.hour * 60 + now.minute >= plan_minutes

    def _daily_plan_due_minutes(self) -> int:
        plan_minutes = self._parse_hhmm_to_minutes(runtime_persona_setting(self, "daily_plan_time", "07:30"))
        if plan_minutes is None:
            return 7 * 60 + 30
        return plan_minutes

    def _is_plan_date_active(self, plan_date: str) -> bool:
        plan_date = str(plan_date or "").strip()
        if not plan_date:
            return False
        today = self._environment_now().date()
        today_key = _date_key(today)
        if plan_date == today_key:
            return True
        yesterday_key = _date_key(today - timedelta(days=1))
        if plan_date != yesterday_key:
            return False
        now_minutes = self._environment_now_minutes()
        return now_minutes < self._daily_plan_due_minutes()

    def _get_active_plan(self) -> dict[str, Any]:
        plan = self.data.get("daily_plan", {})
        if isinstance(plan, dict) and self._is_plan_date_active(plan.get("date")):
            return plan
        return {}

    def _effective_plan_now_minutes(self, plan_date: str) -> int | None:
        plan_date = str(plan_date or "").strip()
        if not self._is_plan_date_active(plan_date):
            return None
        now_minutes = self._environment_now_minutes()
        if plan_date == _today_key():
            return now_minutes
        return 24 * 60 + now_minutes

    def _is_sleepy_plan_item(self, item: dict[str, Any] | None) -> bool:
        if not isinstance(item, dict):
            return False
        text = " ".join(
            _single_line(item.get(key), 100)
            for key in ("activity", "mood", "message_seed")
            if _single_line(item.get(key), 100)
        )
        if not text:
            return False
        if re.search(r"继续睡|睡回去|重新入睡|再次入睡|回笼觉", text):
            return True
        if re.search(
            r"自然醒|睡醒|醒来|醒后|刚醒|醒了|已醒|醒着|清醒|睁眼|起床|起身|洗漱|"
            r"不睡|没睡|未睡|还没睡|睡不着|失眠",
            text,
        ):
            return False
        return bool(
            re.search(
                r"睡觉|睡眠|入睡|熟睡|浅睡|午睡|午休|小睡|补觉|回笼觉|打盹|"
                r"眯(?:一|半)?会(?:儿)?|梦乡|被窝|准备睡|睡前|继续睡|睡回去|熄灯休息",
                text,
            )
        )

    def _segment_end_minutes(
        self,
        start: int,
        item: dict[str, Any] | None,
        *,
        next_start: int | None = None,
    ) -> int:
        if next_start is not None:
            return next_start
        if self._is_sleepy_plan_item(item):
            return min(24 * 60 + 240, start + 240)
        return min(24 * 60 + 120, start + 180)

    def _plan_item_end_minutes(
        self,
        start: int,
        item: dict[str, Any] | None,
        *,
        next_start: int | None = None,
    ) -> int:
        explicit = self._parse_hhmm_to_minutes((item or {}).get("end")) if isinstance(item, dict) else None
        if explicit is not None:
            if explicit <= start:
                explicit += 24 * 60
            duration = explicit - start
            if 10 <= duration <= 12 * 60:
                if next_start is not None:
                    normalized_next = next_start + (24 * 60 if next_start <= start else 0)
                    explicit = min(explicit, normalized_next)
                return explicit
        if next_start is not None:
            return next_start + (24 * 60 if next_start <= start else 0)
        return self._segment_end_minutes(start, item)

    def _normalized_plan_item_starts(self, items: Any) -> list[int | None]:
        if not isinstance(items, list):
            return []
        normalized: list[int | None] = []
        day_offset = 0
        previous_raw: int | None = None
        for item in items:
            raw = self._parse_hhmm_to_minutes(item.get("time")) if isinstance(item, dict) else None
            if raw is None:
                normalized.append(None)
                continue
            if previous_raw is not None and raw < previous_raw:
                day_offset += 24 * 60
            normalized.append(raw + day_offset)
            previous_raw = raw
        return normalized

    def _normalize_plan_item_intervals(self, items: Any) -> bool:
        if not isinstance(items, list):
            return False
        starts = self._normalized_plan_item_starts(items)
        changed = False
        for index, item in enumerate(items):
            if not isinstance(item, dict) or starts[index] is None:
                continue
            start = int(starts[index])
            next_start = next((value for value in starts[index + 1 :] if value is not None), None)
            end = self._plan_item_end_minutes(start, item, next_start=next_start)
            end_text = self._minutes_to_hhmm(end)
            if _single_line(item.get("end"), 8) != end_text:
                item["end"] = end_text
                changed = True
            lifecycle = _single_line(item.get("lifecycle_status"), 20).lower()
            if lifecycle not in {"planned", "changed", "cancelled", "deferred"}:
                item["lifecycle_status"] = "planned"
                changed = True
            basis = self._normalize_schedule_basis(item.get("basis"), default=["coarse_plan"])
            if item.get("basis") != basis:
                item["basis"] = basis
                changed = True
            confidence = min(1.0, _safe_float(item.get("confidence"), 0.72))
            if item.get("confidence") != confidence:
                item["confidence"] = confidence
                changed = True
        return changed

    @staticmethod
    def _normalize_schedule_lifecycle_status(value: Any) -> str:
        aliases = {
            "planned": "planned", "计划": "planned", "未开始": "planned",
            "active": "active", "进行": "active", "进行中": "active",
            "completed": "completed", "完成": "completed", "已完成": "completed",
            "changed": "changed", "变更": "changed", "已变更": "changed",
            "cancelled": "cancelled", "canceled": "cancelled", "取消": "cancelled", "已取消": "cancelled",
            "deferred": "deferred", "postponed": "deferred", "顺延": "deferred", "延期": "deferred",
        }
        return aliases.get(_single_line(value, 20).lower(), "")

    @staticmethod
    def _normalize_schedule_basis(value: Any, *, default: list[str] | None = None) -> list[str]:
        allowed = {"calendar", "persona", "adjustment", "state", "weather", "continuity", "inspiration", "coarse_plan"}
        raw = value if isinstance(value, list) else re.split(r"[,，;；\s]+", str(value or ""))
        result: list[str] = []
        for item in raw:
            key = _single_line(item, 24).lower()
            if key in allowed and key not in result:
                result.append(key)
        return result[:3] or list(default or [])[:3]

    def _schedule_window_runtime_status(
        self,
        start: int,
        end: int,
        *,
        plan_date: str = "",
        explicit_status: Any = "",
    ) -> str:
        explicit = self._normalize_schedule_lifecycle_status(explicit_status)
        if explicit == "cancelled":
            return explicit
        date_text = _single_line(plan_date, 16)
        now_minutes = self._effective_plan_now_minutes(date_text) if date_text else self._environment_now_minutes()
        if now_minutes is None:
            today = _today_key()
            return "completed" if date_text and date_text < today else "planned"
        normalized_end = int(end)
        if normalized_end <= start:
            normalized_end += 24 * 60
        if now_minutes < start:
            runtime = "planned"
        elif now_minutes >= normalized_end:
            runtime = "completed"
        else:
            runtime = "active"
        if explicit == "changed" and runtime != "completed":
            return "changed"
        return runtime

    def _plan_item_runtime_status(self, plan: dict[str, Any], item: dict[str, Any], index: int = -1) -> str:
        # Lifecycle display must come from canonical evidence, never from the
        # clock alone.  Keep the legacy helper signature for callers, but map
        # old lifecycle values through a conservative planned/unknown view.
        if isinstance(item, dict):
            legacy = self._normalize_schedule_lifecycle_status(item.get("lifecycle_status"))
            if legacy == "cancelled":
                return "cancelled"
            if legacy == "changed":
                return "changed"
            if legacy == "deferred":
                return "deferred"
            evidence = _single_line(item.get("evidence_kind"), 48).lower()
            eligibility = _single_line(item.get("fact_eligibility"), 48).lower()
            status = _single_line(item.get("status"), 32).lower()
            if evidence in {"interaction", "tool_action", "external_record"} and eligibility in {"current_observed", "history_observed"}:
                if status in {"active", "completed", "partially_completed"}:
                    return status
            if evidence == "self_state_commit" and eligibility == "current_internal":
                return "active"
            plan_date = str((plan or {}).get("date") or item.get("date") or "")
            try:
                canonical = normalize_plan_item(
                    {**item, "date": plan_date or _today_key(), "subject_actor_id": item.get("subject_actor_id") or "bot_self"},
                    plan_id=str(item.get("plan_id") or ""),
                    now=self._environment_now(),
                )
                phase = _single_line(canonical.get("temporal_phase"), 16).lower()
                if phase == "past":
                    # ``normalize_plan_item`` evaluates a HH:MM value on the
                    # calendar date alone.  A plan that deliberately rolls
                    # past midnight therefore looks stale even while its
                    # normalized schedule axis is still current/upcoming.
                    # Preserve the evidence status as ``planned`` in that
                    # case; the separate display status may still project the
                    # wall-clock phase as active.
                    items = plan.get("items") if isinstance(plan, dict) else None
                    starts = self._normalized_plan_item_starts(items)
                    start = starts[index] if isinstance(items, list) and 0 <= index < len(starts) else None
                    if start is not None:
                        next_start = next((value for value in starts[index + 1 :] if value is not None), None)
                        end = self._plan_item_end_minutes(start, item, next_start=next_start)
                        clock_phase = self._schedule_window_runtime_status(
                            start,
                            end,
                            plan_date=plan_date,
                            explicit_status=item.get("lifecycle_status"),
                        )
                        if clock_phase in {"planned", "active"}:
                            return "planned"
                    return "unknown"
                return "planned"
            except Exception:
                return "planned"
        items = plan.get("items") if isinstance(plan, dict) else None
        starts = self._normalized_plan_item_starts(items)
        start = starts[index] if isinstance(items, list) and 0 <= index < len(starts) else self._parse_hhmm_to_minutes(item.get("time"))
        if start is None:
            return "planned"
        next_start = None
        if isinstance(items, list) and index >= 0:
            next_start = next((value for value in starts[index + 1 :] if value is not None), None)
        end = self._plan_item_end_minutes(start, item, next_start=next_start)
        return self._schedule_window_runtime_status(
            start,
            end,
            plan_date=str((plan or {}).get("date") or ""),
            explicit_status=item.get("lifecycle_status"),
        )

    def _plan_item_display_status(self, plan: dict[str, Any], item: dict[str, Any], index: int = -1) -> str:
        """Return the user-facing clock phase without upgrading it to execution evidence."""

        canonical = self._plan_item_runtime_status(plan, item, index)
        if canonical in {"cancelled", "deferred", "overridden", "active", "completed", "partially_completed"}:
            return canonical
        items = plan.get("items") if isinstance(plan, dict) else None
        starts = self._normalized_plan_item_starts(items)
        start = starts[index] if isinstance(items, list) and 0 <= index < len(starts) else self._parse_hhmm_to_minutes(item.get("time"))
        if start is None:
            return canonical or "planned"
        next_start = next((value for value in starts[index + 1 :] if value is not None), None) if index >= 0 else None
        end = self._plan_item_end_minutes(start, item, next_start=next_start)
        return self._schedule_window_runtime_status(
            start,
            end,
            plan_date=str((plan or {}).get("date") or item.get("date") or ""),
            explicit_status=item.get("lifecycle_status"),
        )

    def _parse_hhmm_to_minutes(self, value: Any) -> int | None:
        match = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", str(value or ""))
        if not match:
            return None
        hour, minute = int(match.group(1)), int(match.group(2))
        if hour > 23 or minute > 59:
            return None
        return hour * 60 + minute

    def _minutes_to_hhmm(self, minutes: int) -> str:
        minutes = max(0, int(minutes))
        wrapped = minutes % (24 * 60)
        return f"{wrapped // 60:02d}:{wrapped % 60:02d}"

    async def _generate_daily_plan(self) -> dict[str, Any]:
        await self._ensure_yesterday_conversation_summary()
        await self._ensure_yesterday_screen_diary_context()
        await self._maybe_settle_skill_growth(force=True)
        return await generate_daily_plan(self)

    def _get_schedule_planning_prompt(self) -> str:
        return get_schedule_planning_prompt(self)

    def _build_daily_plan_prompt(self, now: str, memory_companion_context: str = "") -> str:
        return build_daily_plan_prompt(self, now, memory_companion_context=memory_companion_context)

    def _build_daily_plan_prompt_section(
        self,
        now: str,
        memory_companion_context: str = "",
    ) -> PromptSection:
        return build_daily_plan_prompt_section(
            self,
            now,
            memory_companion_context=memory_companion_context,
        )

    async def _ensure_yesterday_conversation_summary(self, force: bool = False) -> dict[str, Any]:
        today = _today_key()
        cached = self.data.get("yesterday_conversation_summary", {})
        if (
            isinstance(cached, dict)
            and cached.get("date") == today
            and cached.get("scope") == "owner_private_only"
            and not force
        ):
            return cached
        raw_text = await self._collect_yesterday_conversation_text()
        if not raw_text:
            summary = {
                "date": today,
                "source_date": _date_key(date.today() - timedelta(days=1)),
                "summary": "暂无可用的昨日完整对话摘要。",
                "residues": [],
                "schedule_reference": "无明确可继承影响。",
                "dream_reference": "无明确可继承碎片。",
                "scope": "owner_private_only",
                "raw_excerpt_chars": 0,
            }
        else:
            summary = await self._summarize_yesterday_conversation_for_schedule(raw_text)
        async with self._data_lock:
            self.data["yesterday_conversation_summary"] = summary
            self._save_data_sync(sections={"yesterday_conversation_summary"})
        return summary

    async def _collect_yesterday_conversation_text(self) -> str:
        users = self.data.get("users", {})
        if not isinstance(users, dict):
            return ""
        now_dt = self._environment_now()
        yesterday = now_dt.date() - timedelta(days=1)
        start = datetime.combine(yesterday, datetime.min.time(), tzinfo=now_dt.tzinfo).timestamp()
        end = start + 24 * 3600
        sections: list[PromptSection] = []
        for user_id, raw_user in users.items():
            if not isinstance(raw_user, dict):
                continue
            if self._private_user_role(raw_user, str(user_id)) != "owner":
                continue
            umo = str(raw_user.get("umo") or "").strip()
            if not umo:
                continue
            try:
                getter = getattr(self, "_get_current_conversation_safely", None)
                if callable(getter):
                    conv = await getter(umo, label="yesterday_conversation_read")
                else:
                    conv_id = await self.context.conversation_manager.get_curr_conversation_id(umo)
                    if not conv_id:
                        continue
                    conv = await self.context.conversation_manager.get_conversation(umo, conv_id)
            except Exception as exc:
                logger.debug("读取昨日对话失败: user=%s err=%s", user_id, exc)
                continue
            if not conv:
                continue
            history = self._load_conversation_history_items(conv)
            dated_lines: list[str] = []
            undated_lines: list[str] = []
            for item in history:
                line = self._format_history_item_for_summary(item)
                if not line:
                    continue
                ts = self._history_item_timestamp(item)
                if ts is None:
                    undated_lines.append(line)
                elif start <= ts < end:
                    dated_lines.append(line)
            selected = dated_lines if dated_lines else undated_lines[-120:]
            if not selected:
                continue
            name = _single_line(raw_user.get("nickname") or user_id, 30)
            source_note = "昨日对话" if dated_lines else "最近对话（history 无时间戳,作为昨日摘要候选）"
            sections.append(
                prompt_section(
                    key=f"background.yesterday_conversation.user_{len(sections)}",
                    title=f"主要用户:{name}｜{source_note}",
                    source="daily_state",
                    content="\n".join(selected),
                )
            )
        return render_prompt_sections(
            sections,
            mode=PromptRenderMode.LABELED_BLOCK,
        ).strip()[-18000:]

    def _load_conversation_history_items(
        self,
        conversation: Conversation | None,
        *,
        tail_only: int | None = None,
    ) -> list[dict[str, Any]]:
        if conversation is None:
            return []
        raw = conversation.history or "[]"
        if tail_only is not None and tail_only > 0:
            tail = self._parse_tail_json_items(raw, tail_only)
            if tail is not None:
                return tail
        try:
            loaded = json.loads(raw)
        except Exception:
            return []
        if not isinstance(loaded, list):
            return []
        return [item for item in loaded if isinstance(item, dict)]

    @staticmethod
    def _parse_tail_json_items(raw: str, count: int) -> list[dict[str, Any]] | None:
        """仅反序列化 JSON 数组从尾部往回数 count 个 dict 元素，避免对超大
        conversation.history 全量 json.loads。

        从右向左逆序扫描元素边界：字符串按"左侧连续反斜杠个数的奇偶性"判定转义
        引号（奇数 => 转义、偶数 => 定界），从而正确区分字符串、嵌套括号与顶层
        逗号；对每个尾部元素单独解码，遇到 dict 才计数，语义与"全量解析后过滤
        dict 再取尾部 count 条"完全一致。任何解析异常、结构不符或不足 count 个
        时返回 None，由调用方回退全量解析，正确性始终有保证。
        """
        if not count or count < 1:
            return None
        text = raw.strip()
        if not (text.startswith("[") and text.endswith("]")):
            return None
        end = len(text) - 1  # 数组右括号下标
        stop = end  # 当前元素区间的右边界(不含)
        i = end - 1
        depth = 0
        in_string = False
        found: list[dict[str, Any]] = []

        def _collect(span_start: int, span_stop: int) -> bool:
            try:
                item = json.loads(text[span_start:span_stop])
            except Exception:
                return False
            if isinstance(item, dict):
                found.append(item)
                return len(found) == count
            return False

        while i >= 0:
            ch = text[i]
            if in_string:
                if ch == '"':
                    # 判定左侧连续反斜杠个数的奇偶：奇数 => 转义引号，属字符串内容
                    j = i - 1
                    bs = 0
                    while j >= 0 and text[j] == "\\":
                        bs += 1
                        j -= 1
                    if bs % 2 == 1:
                        i = j  # 跳过该反斜杠串，继续留在字符串内
                        continue
                    in_string = False
                i -= 1
                continue
            if ch == '"':
                in_string = True
            elif ch in "]}":
                depth += 1
            elif ch in "[{":
                if depth == 0:
                    # 回溯到数组左括号：当前为第一个元素
                    if _collect(i + 1, stop) and len(found) == count:
                        return found[::-1]
                    return None
                depth -= 1
            elif ch == "," and depth == 0:
                span_start = i + 1
                if _collect(span_start, stop):
                    return found[::-1]
                stop = i
            i -= 1
        return None

    def _history_item_timestamp(self, item: dict[str, Any]) -> float | None:
        for key in ("timestamp", "time", "created_at", "updated_at", "created", "date"):
            value = item.get(key)
            if value is None or value == "":
                continue
            numeric = _safe_float(value, 0)
            if numeric > 0:
                return numeric / 1000 if numeric > 10_000_000_000 else numeric
            text = str(value).strip()
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%m-%d %H:%M:%S", "%m-%d %H:%M"):
                try:
                    parsed = datetime.strptime(text, fmt)
                    if fmt.startswith("%m"):
                        parsed = parsed.replace(year=date.today().year)
                    return parsed.timestamp()
                except Exception:
                    continue
        return None

    def _format_history_item_for_summary(self, item: dict[str, Any]) -> str:
        role = _single_line(item.get("role") or item.get("type") or item.get("speaker"), 20).lower()
        if role in {"assistant", "bot", "ai"}:
            speaker = f"{runtime_persona_setting(self, 'bot_name', '小星')}(Bot回复)"
        elif role in {"user", "human"}:
            speaker = "用户"
        else:
            speaker = role or "对话"
        content = self._history_item_content_text(item)
        if not content:
            return ""
        if self._daily_proactive_archive_context_text(content):
            return ""
        ts = self._history_item_timestamp(item)
        time_prefix = self._environment_fromtimestamp(ts).strftime("%m-%d %H:%M") + " " if ts else ""
        return f"{time_prefix}{speaker}: {content}"

    def _history_item_content_text(self, item: dict[str, Any]) -> str:
        value = item.get("content")
        if value is None:
            value = item.get("message") or item.get("text") or item.get("content_text")
        if isinstance(value, str):
            return _single_line(value, 260)
        if isinstance(value, list):
            parts: list[str] = []
            for part in value:
                if isinstance(part, str):
                    parts.append(part)
                elif isinstance(part, dict):
                    text = part.get("text") or part.get("content") or part.get("message")
                    if text:
                        parts.append(str(text))
                    elif str(part.get("type") or "").lower() == "image":
                        parts.append("[图片]")
            return _single_line(" ".join(parts), 260)
        if isinstance(value, dict):
            return _single_line(value.get("text") or value.get("content") or json.dumps(value, ensure_ascii=False), 260)
        return ""

    async def _summarize_yesterday_conversation_for_schedule(self, raw_text: str) -> dict[str, Any]:
        today = _today_key()
        source_date = _date_key(date.today() - timedelta(days=1))
        section = prompt_section(
            key="background.yesterday_summary",
            title="昨日对话残留摘要",
            source="daily_state",
            content=f"""
请阅读下面的昨日/最近完整对话材料,为今天的日程和梦境生成提炼参考摘要。

目标不是复述聊天,而是找出可能延续到今天的“残留影响”：身体状态、饮食/作息、情绪余波、关系变化、未完成约定、收到/送出的东西、外出计划、压力来源、被安慰/被打断的事、梦境可能用到的物件/颜色/气味/半句话等。

重要原则：
1. 只根据对话内容做合理推断,不要硬套固定事件类型。
2. 如果某个行为可能带来身体或日程后果,用抽象逻辑表达：饮食、睡眠、天气、运动、情绪刺激、约定、礼物、争执、安慰等都可能改变今天的体力、胃口、心情、出门意愿或主动话题。
3. 影响可以很轻,也可以没有。不要为了制造剧情强行让今天出事。
4. 摘要要给日程模型用,所以写成可执行参考,不是聊天回复。
5. 梦境参考只提炼碎片和情绪质感,不要编完整梦。
6. 饮食偏好要有衰退：某个菜名、零食或口味反复出现时,只当“近期聊过/需要避错”的软背景,不要要求今天继续安排购买、带饭、留一份或一起吃。用户说“不吃/不喜欢/不要/避开某食物”时,只写成“相关时避开该食物”,不要写成今天必须准备替代餐食。
7. 严格区分说话人：只有“用户”行能写成用户真实信息；“Bot回复”里的我在做什么、身体/心情/日程、动作描写或生活片段，只能视为 Bot 当时的拟人化表达，不能当作用户事实、现实证据或今天必须继承的事件。
8. 只有用户明确确认、提出或约定的事，才可以进入计划/未完成约定；Bot 自称的吃饭、整理、犯困、走动、创作等状态不要转成稳定记忆或现实日程。

对话材料：
{raw_text}

只输出 JSON：
{{
  "summary": "昨日对话的一句话概括",
  "residues": [
    {{"type": "身体/情绪/关系/计划/物件/梦境碎片", "content": "可延续影响", "strength": "轻/中/强"}}
  ],
  "schedule_reference": "今天生成日程时应如何自然继承这些残留；没有就写无明确影响",
  "dream_reference": "今天梦境/梦境碎片可以参考的物件、感官、半句话或情绪；没有就写无明确碎片"
}}
""".strip(),
        )
        prompt = render_prompt_sections(
            [section],
            mode=PromptRenderMode.BODY_ONLY,
        )
        raw = await self._llm_call(
            prompt,
            max_tokens=650,
            provider_id=self._task_provider(
                runtime_persona_setting(self, "history_summary_provider_id", ""),
                runtime_persona_setting(self, "daily_plan_provider_id", ""),
                runtime_persona_setting(self, "mai_style_provider_id", ""),
            ),
            task="yesterday_summary",
        )
        payload = self._extract_json_payload(raw or "")
        if not isinstance(payload, dict):
            return {
                "date": today,
                "source_date": source_date,
                "summary": _single_line(raw_text, 180) or "昨日对话有记录,但摘要生成失败。",
                "residues": [],
                "schedule_reference": "可把昨日互动作为轻微关系和情绪背景,不要强行改写今日主线。",
                "dream_reference": "可从昨日对话里的物件、语气和半句话提取梦境碎片。",
                "scope": "owner_private_only",
                "raw_excerpt_chars": len(raw_text),
            }
        residues = payload.get("residues", [])
        if not isinstance(residues, list):
            residues = []
        normalized_residues = []
        for item in residues[:8]:
            if not isinstance(item, dict):
                continue
            content = _single_line(item.get("content"), 120)
            if not content:
                continue
            normalized_residues.append({
                "type": _single_line(item.get("type"), 24) or "残留",
                "content": content,
                "strength": _single_line(item.get("strength"), 8) or "轻",
            })
        return {
            "date": today,
            "source_date": source_date,
            "summary": _single_line(payload.get("summary"), 180) or "昨日对话有一些可延续的情绪和生活残留。",
            "residues": normalized_residues,
            "schedule_reference": _single_line(payload.get("schedule_reference"), 220) or "作为轻微背景承接,不要强行改写今日主线。",
            "dream_reference": _single_line(payload.get("dream_reference"), 220) or "从昨日对话的物件、感官和半句话中轻取梦境碎片。",
            "scope": "owner_private_only",
            "raw_excerpt_chars": len(raw_text),
        }

    def _format_yesterday_conversation_summary_for_prompt(self) -> str:
        summary = self.data.get("yesterday_conversation_summary", {})
        if not isinstance(summary, dict) or summary.get("date") != _today_key():
            return "暂无昨日完整对话摘要。"
        schedule_reference = self._decay_schedule_food_reference_text(
            summary.get("schedule_reference"),
            field="yesterday_conversation.schedule_reference",
        )
        dream_reference = self._decay_schedule_food_reference_text(
            summary.get("dream_reference"),
            field="yesterday_conversation.dream_reference",
            dream=True,
        )
        lines = [
            f"来源日期：{summary.get('source_date') or '昨日'}",
            f"概括：{_single_line(summary.get('summary'), 180)}",
            f"日程参考：{schedule_reference}",
            f"梦境参考：{dream_reference}",
        ]
        residues = summary.get("residues", [])
        if isinstance(residues, list) and residues:
            lines.append("残留变量：")
            for item in residues[:8]:
                if not isinstance(item, dict):
                    continue
                content = self._decay_schedule_food_reference_text(
                    item.get("content"),
                    field="yesterday_conversation.residue",
                )
                if content:
                    lines.append(f"- {item.get('type') or '残留'}｜{content}｜强度 {item.get('strength') or '轻'}")
        return "\n".join(lines)

    @staticmethod
    def _schedule_food_reference_has_negative_preference(text: str) -> bool:
        if not text:
            return False
        negative_markers = ("不吃", "不喜欢", "不想吃", "不要", "别吃", "避开", "换别的", "代替", "别准备", "不要准备")
        food_markers = (
            "饭", "餐", "菜", "食物", "吃的", "早餐", "午饭", "晚饭", "夜宵", "零食",
            "排骨", "糖醋", "螺蛳粉", "锅包肉", "烤肠", "豆花", "冰粉", "甜口",
        )
        return any(token in text for token in negative_markers) and any(token in text for token in food_markers)

    @staticmethod
    def _schedule_food_reference_is_concrete_motif(text: str) -> bool:
        if not text:
            return False
        food_markers = (
            "糖醋排骨", "排骨", "螺蛳粉", "锅包肉", "烤肠", "豆花", "冰粉", "甜口",
            "桂花", "奶茶", "豆浆", "夜宵", "饭团", "便当",
        )
        action_markers = ("准备", "带", "买", "做", "留", "夹", "点", "抢", "一起吃", "约饭", "饭")
        return any(food in text for food in food_markers) and any(action in text for action in action_markers)

    def _decay_schedule_food_reference_text(self, text: Any, *, field: str = "", dream: bool = False) -> str:
        source = _single_line(text, 260)
        if not source:
            return ""
        if dream:
            if self._schedule_food_reference_is_concrete_motif(source):
                return (
                    "梦境里可保留少量气味或颜色质感,但具体菜名属于近期高频意象,不要让它反推今天的餐食安排。"
                )
            return source
        clauses = [part.strip() for part in re.split(r"[；;。]+", source) if _single_line(part, 160)]
        if not clauses:
            clauses = [source]
        changed = False
        kept: list[str] = []
        added_guard = False
        for clause in clauses:
            cleaned = _single_line(clause, 180)
            if self._schedule_food_reference_has_negative_preference(cleaned):
                changed = True
                if not added_guard:
                    kept.append("若今天自然聊到餐食,只记得避开对方明确不吃的食物；不要为了这个避雷主动安排带饭、备餐或替代餐食剧情")
                    added_guard = True
                continue
            if self._schedule_food_reference_is_concrete_motif(cleaned):
                changed = True
                if not added_guard:
                    kept.append("具体食物只作近期聊过的软背景,不要连续复刻成今日午饭、带饭、留一份或邀约")
                    added_guard = True
                continue
            kept.append(cleaned)
        result = "；".join(part for part in kept if part).strip("；; ")
        if changed:
            logger.info(
                "已降级日程饮食参考: field=%s before=%s after=%s",
                field or "-",
                _single_line(source, 120),
                _single_line(result, 120),
            )
        return result or "只作轻微背景承接,不要强行改写今日主线。"

    def _build_detail_enhancement_prompt(
        self,
        segment: dict[str, Any],
        plan: dict[str, Any],
        state: dict[str, Any],
        memory_companion_context: str = "",
    ) -> str:
        return build_detail_enhancement_prompt(self, segment, plan, state, memory_companion_context=memory_companion_context)

    def _build_detail_enhancement_prompt_section(
        self,
        segment: dict[str, Any],
        plan: dict[str, Any],
        state: dict[str, Any],
        memory_companion_context: str = "",
    ) -> PromptSection:
        return build_detail_enhancement_prompt_section(
            self,
            segment,
            plan,
            state,
            memory_companion_context=memory_companion_context,
        )

    @staticmethod
    def _persona_prompt_cache_scope(umo: str = "", specific_id: str = "") -> str:
        if specific_id:
            return f"persona:{specific_id}"
        if umo:
            return f"session:{umo}"
        return "default"

    def _cached_persona_prompt_for_scope(self, umo: str = "", specific_id: str = "") -> tuple[str, float]:
        scope = self._persona_prompt_cache_scope(umo, specific_id)
        entries = getattr(self, "_default_persona_prompt_cache_by_scope", None)
        if isinstance(entries, dict):
            entry = entries.get(scope)
            if isinstance(entry, dict):
                return (
                    str(entry.get("prompt") or "").strip(),
                    _safe_float(entry.get("cached_at"), 0.0),
                )
        return "", 0.0

    def _store_persona_prompt_for_scope(self, prompt: str, *, umo: str = "", specific_id: str = "") -> str:
        cleaned = str(prompt or "").strip()
        if not cleaned:
            return ""
        entries = getattr(self, "_default_persona_prompt_cache_by_scope", None)
        if not isinstance(entries, dict):
            entries = {}
            self._default_persona_prompt_cache_by_scope = entries
        now = _now_ts()
        entries[self._persona_prompt_cache_scope(umo, specific_id)] = {
            "prompt": cleaned,
            "cached_at": now,
            "umo": umo,
            "persona_id": specific_id,
        }
        if len(entries) > 64:
            newest = sorted(
                entries.items(),
                key=lambda item: _safe_float(item[1].get("cached_at"), 0.0) if isinstance(item[1], dict) else 0.0,
                reverse=True,
            )[:64]
            self._default_persona_prompt_cache_by_scope = dict(newest)
        # Keep legacy fields synchronized for code paths that do not have a session key.
        self._default_persona_prompt_cache = cleaned
        self._default_persona_prompt_cache_at = now
        self._default_persona_prompt_cache_umo = umo
        self._default_persona_prompt_cache_persona_id = specific_id
        return cleaned

    def _get_default_persona_prompt(self, umo: str = "") -> str:
        specific_id = str(getattr(self, "_effective_plugin_persona_id", lambda: getattr(self, "plugin_specific_persona_id", ""))() or "").strip()
        scoped, _ = self._cached_persona_prompt_for_scope(umo, specific_id)
        if scoped:
            return scoped
        cached = str(getattr(self, "_default_persona_prompt_cache", "") or "").strip()
        cached_persona_id = str(getattr(self, "_default_persona_prompt_cache_persona_id", "") or "")
        cached_umo = str(getattr(self, "_default_persona_prompt_cache_umo", "") or "")
        if cached and (
            (specific_id and cached_persona_id == specific_id)
            or (not specific_id and not cached_persona_id and (not umo or cached_umo == umo))
        ):
            return cached
        return DEFAULT_PERSONA_PROMPT_FALLBACK

    def _extract_default_persona_prompt(self, persona: Any) -> str:
        if isinstance(persona, dict):
            return str(persona.get("prompt") or "").strip()
        if isinstance(persona, str):
            return persona.strip()
        for attr in ("prompt", "system_prompt", "content"):
            try:
                value = getattr(persona, attr, None)
            except Exception:
                value = None
            text = str(value or "").strip()
            if text:
                return text
        return ""

    async def _refresh_default_persona_prompt(self, umo: str = "") -> str:
        def _cancel_requested() -> bool:
            # A database/manager implementation may raise CancelledError for
            # its own failed lookup. Preserve cancellation requested for the
            # plugin task itself so shutdown remains responsive.
            try:
                task = asyncio.current_task()
                return bool(task is not None and task.cancelling())
            except RuntimeError:
                return False

        try:
            specific_id = str(getattr(self, "_effective_plugin_persona_id", lambda: getattr(self, "plugin_specific_persona_id", ""))() or "").strip()
            cached, cached_at = self._cached_persona_prompt_for_scope(umo, specific_id)
            if not cached:
                legacy_cached = str(getattr(self, "_default_persona_prompt_cache", "") or "").strip()
                legacy_umo = str(getattr(self, "_default_persona_prompt_cache_umo", "") or "")
                legacy_persona_id = str(getattr(self, "_default_persona_prompt_cache_persona_id", "") or "")
                if (
                    (specific_id and legacy_persona_id == specific_id)
                    or (not specific_id and not legacy_persona_id and (not umo or legacy_umo == umo))
                ):
                    cached = legacy_cached
                    cached_at = _safe_float(getattr(self, "_default_persona_prompt_cache_at", 0.0), 0.0)
            cache_fresh = cached and (_now_ts() - cached_at < 300.0)
            if cache_fresh:
                return cached

            manager = getattr(getattr(self, "context", None), "persona_manager", None)
            if manager and specific_id:
                try:
                    specific_getter = getattr(manager, "get_persona", None)
                    if callable(specific_getter):
                        result = await self._await_framework_db_query(
                            f"persona:{specific_id}",
                            lambda: specific_getter(specific_id),
                            timeout=2.0,
                        )
                        prompt = self._extract_default_persona_prompt(result)
                        if prompt:
                            return self._store_persona_prompt_for_scope(prompt, umo=umo, specific_id=specific_id)
                except asyncio.CancelledError:
                    if _cancel_requested():
                        raise
                    logger.debug(
                        "指定人格查询被管理器取消(ID: %s),本轮使用缓存人格",
                        specific_id,
                    )
                    return cached or self._get_default_persona_prompt(umo)
                except (sqlite3.OperationalError, sqlite3.ProgrammingError) as exc:
                    logger.debug(
                        "指定人格数据库暂不可用(ID: %s),本轮使用缓存人格: %s",
                        specific_id,
                        _single_line(exc, 160),
                    )
                    return cached or self._get_default_persona_prompt(umo)
                except asyncio.TimeoutError:
                    logger.warning("读取插件指定人格超时(ID: %s),本轮使用缓存人格", specific_id)
                    return cached or self._get_default_persona_prompt(umo)
                except Exception as e:
                    logger.warning(f"读取插件指定人格失败(ID: {specific_id}): {e}")
            getter = getattr(manager, "get_default_persona_v3", None) if manager else None
            if not callable(getter):
                return cached or self._get_default_persona_prompt(umo)
            def _read_default_persona() -> Any:
                try:
                    return getter(umo=umo)
                except TypeError:
                    try:
                        return getter(umo)
                    except TypeError:
                        return getter()

            result = await self._await_framework_db_query(
                f"default_persona:{umo}",
                _read_default_persona,
                timeout=2.0,
            )
            prompt = self._extract_default_persona_prompt(result)
            if prompt:
                return self._store_persona_prompt_for_scope(prompt, umo=umo, specific_id="")
        except asyncio.CancelledError:
            if _cancel_requested():
                raise
            logger.debug("默认人格查询被管理器取消,本轮使用缓存人格")
        except (sqlite3.OperationalError, sqlite3.ProgrammingError) as exc:
            logger.debug(
                "默认人格数据库暂不可用,本轮使用缓存人格: %s",
                _single_line(exc, 160),
            )
        except asyncio.TimeoutError:
            logger.warning("读取 AstrBot 默认人格超时,本轮使用缓存人格")
        except Exception as e:
            logger.warning(f"读取 AstrBot 默认人格失败: {e}")
        return self._get_default_persona_prompt(umo)

    async def _await_framework_db_query(
        self,
        key: str,
        factory: Any,
        *,
        timeout: float,
    ) -> Any:
        """Bound a core DB read without cancelling its aiosqlite connection."""
        tasks = getattr(self, "_framework_db_query_tasks", None)
        if not isinstance(tasks, dict):
            tasks = {}
            self._framework_db_query_tasks = tasks
        task = tasks.get(key)
        if not isinstance(task, asyncio.Task) or task.done():
            result = factory()
            if not inspect.isawaitable(result):
                return result
            task = asyncio.create_task(result, name=f"private-companion-db:{key[:80]}")
            tasks[key] = task

            def _cleanup(done: asyncio.Task, *, query_key: str = key) -> None:
                if tasks.get(query_key) is done:
                    tasks.pop(query_key, None)
                if done.cancelled():
                    return
                try:
                    done.exception()
                except Exception:
                    pass

            task.add_done_callback(_cleanup)
        return await asyncio.wait_for(asyncio.shield(task), timeout=timeout)

    def _schedule_default_persona_prompt_refresh(self, umo: str = "") -> None:
        specific_id = str(getattr(self, "_effective_plugin_persona_id", lambda: getattr(self, "plugin_specific_persona_id", ""))() or "").strip()
        cached, cached_at = self._cached_persona_prompt_for_scope(umo, specific_id)
        cache_fresh = cached and (_now_ts() - cached_at < 300.0)
        if cache_fresh:
            return
        scope = self._persona_prompt_cache_scope(umo, specific_id)
        tasks = getattr(self, "_default_persona_prompt_refresh_tasks", None)
        if not isinstance(tasks, dict):
            tasks = {}
            self._default_persona_prompt_refresh_tasks = tasks
        task = tasks.get(scope)
        if isinstance(task, asyncio.Task) and not task.done():
            return

        async def _runner() -> None:
            try:
                await self._refresh_default_persona_prompt(umo)
            finally:
                current_tasks = getattr(self, "_default_persona_prompt_refresh_tasks", None)
                if isinstance(current_tasks, dict):
                    current_tasks.pop(scope, None)

        operation = _runner()
        creator = getattr(self, "_create_lifecycle_background_task", None)
        try:
            task = (
                creator(operation, label="default_persona_prompt_refresh")
                if callable(creator)
                else asyncio.create_task(operation, name="private-companion-persona-prompt-refresh")
            )
            if task is not None:
                tasks[scope] = task
                self._default_persona_prompt_refresh_task = task
                if not callable(creator):
                    def consume(done_task: asyncio.Task) -> None:
                        try:
                            done_task.result()
                        except asyncio.CancelledError:
                            pass
                        except Exception as exc:
                            logger.warning(
                                "默认人格后台刷新失败: %s",
                                _single_line(exc, 160),
                            )

                    task.add_done_callback(consume)
            else:
                close = getattr(operation, "close", None)
                if callable(close):
                    close()
        except RuntimeError:
            close = getattr(operation, "close", None)
            if callable(close):
                close()

    def _format_plugin_persona_request_injection(self) -> str:
        section = self._format_plugin_persona_request_prompt_section()
        return (
            render_prompt_sections(
                [section],
                mode=PromptRenderMode.LABELED_BLOCK,
            )
            if section is not None
            else ""
        )

    def _format_plugin_persona_request_prompt_section(self) -> PromptSection | None:
        specific_id = str(getattr(self, "_effective_plugin_persona_id", lambda: getattr(self, "plugin_specific_persona_id", ""))() or "").strip()
        if not specific_id:
            return None
        persona = self._get_default_persona_prompt()
        if not persona or persona == DEFAULT_PERSONA_PROMPT_FALLBACK:
            return None
        return prompt_section(
            key="persona.plugin_specific",
            title="本插件指定人格",
            source="daily_state",
            content=(
                "本轮私聊陪伴相关回复请优先遵循下面的人格设定。"
                "如果它与更高优先级系统安全规则冲突,以安全规则为准；如果与插件的状态/记忆材料冲突,以人格设定为准。\n"
                f"{persona}"
            ),
        )

    def _persona_state_profile(self) -> dict[str, bool]:
        prompt = self._get_default_persona_prompt()
        role_prompt = str(runtime_persona_setting(self, "schedule_persona_prompt", "") or "")
        text = unicodedata.normalize("NFKC", f"{prompt}\n{role_prompt}").lower()
        compact = re.sub(r"\s+", "", text)

        def has_any(markers: tuple[str, ...]) -> bool:
            return any(marker in text or marker in compact for marker in markers)

        strong_non_human_markers = (
            "机器人", "机械体", "机体", "仿生", "android", "robot", "电子生命", "终端人格"
        )
        soft_non_human_markers = (
            "bot", "系统", "程序", "ai"
        )
        explicitly_human_markers = (
            "人类", "学生", "上班", "工作", "生活", "年龄", "岁",
            "吃饭", "睡觉", "起床", "洗漱", "身体", "生理期"
        )
        bodyless_markers = (
            "无实体", "没有实体", "没有身体", "无身体", "纯意识", "虚拟人格", "虚拟形象",
            "全息投影", "投影形态", "灵体", "幽灵", "意识体"
        )
        has_human_markers = has_any(explicitly_human_markers)
        has_bodyless_markers = has_any(bodyless_markers)
        has_strong_non_human = has_any(strong_non_human_markers)
        soft_non_human_hits = sum(1 for marker in soft_non_human_markers if marker in text)
        is_non_human = (has_strong_non_human or soft_non_human_hits >= 2) and not has_human_markers
        allow_health = bool(runtime_persona_setting(self, "enable_health_state", True))
        allow_hunger = bool(runtime_persona_setting(self, "enable_hunger_state", True))
        allow_cycle = bool(runtime_persona_setting(self, "enable_cycle_state", True))
        return {
            "non_human": is_non_human or has_bodyless_markers,
            "allow_health": allow_health,
            "allow_hunger": allow_hunger,
            "allow_cycle": allow_cycle,
        }

    def _base_state_values(self, profile: dict[str, bool] | None = None) -> dict[str, str]:
        profile = profile or self._persona_state_profile()
        values = {
            "sleep": "睡眠平稳",
            "dream": "没有记住梦",
            "health": "状态正常",
            "hunger": "无饥饿感",
            "body_cycle": "不处于生理期",
            "location": "",
        }
        if not profile.get("allow_health", True):
            values["health"] = "健康/不适状态未开启"
        if not profile.get("allow_hunger", True):
            values["hunger"] = "饥饿/胃口状态未开启"
        if not profile.get("allow_cycle", False):
            values["body_cycle"] = "生理期模拟未开启"
        return values

    def _is_inapplicable_state_text(self, text: str) -> bool:
        return "不适用" in str(text or "")

    @staticmethod
    def _state_condition_allowed(kind: str, profile: dict[str, bool]) -> bool:
        if kind == "health":
            return bool(profile.get("allow_health", True))
        if kind == "hunger":
            return bool(profile.get("allow_hunger", True))
        if kind in {"body_cycle", "cycle_discomfort"}:
            return bool(profile.get("allow_cycle", False))
        return True

    def _should_show_condition(self, cond: dict[str, Any]) -> bool:
        if not isinstance(cond, dict):
            return False
        if _safe_int(cond.get("energy_delta"), 0) != 0:
            return True
        if _single_line(cond.get("mood"), 20) not in {"", "平稳"}:
            return True
        if cond.get("cause") or cond.get("phase"):
            return True
        return str(cond.get("kind") or "") not in {"sleep", "dream"}

    def _format_can_do_for_prompt(self) -> str:
        items = self.data.get("can_do", [])
        if not isinstance(items, list) or not items:
            return "（暂未设置）"
        lines = []
        for item in items[:30]:
            text = _single_line(item, 80)
            if text:
                lines.append(f"- {text}")
        return "\n".join(lines) if lines else "（暂未设置）"

    @staticmethod
    def _detect_dialogue_outfit_change(text: Any) -> str:
        normalized = _single_line(text, 180)
        if not normalized:
            return ""
        outfit = (
            r"(?:JK(?:制服|服)?|jk(?:制服|服)?|校服|制服|衣服|衣裳|服装|穿搭|套装|"
            r"睡衣|睡裙|睡袍|居家服|礼服|正装|西装|汉服|和服|旗袍|洛丽塔|lo裙|"
            r"女仆装|巫女服|泳装|泳衣|运动服|球衣|外套|风衣|大衣|夹克|衬衫|"
            r"T恤|毛衣|卫衣|上衣|背心|连衣裙|短裙|长裙|裙子|裤子|短裤|袜子|鞋子|帽子|围巾)"
        )
        action = r"(?:换(?:装|衣|上|成|为|掉|下|回|一套|一身|一件|一条|身)?|改穿|穿(?:上|着|了)?|套上|脱下|脱掉)"
        has_outfit_change = bool(
            re.search(rf"{action}.{{0,24}}{outfit}|{outfit}.{{0,12}}{action}", normalized, re.IGNORECASE)
        )
        if not has_outfit_change:
            return ""

        question_or_hypothesis = bool(
            re.search(r"要不要|能不能|可不可以|是否|是不是|想不想|会不会|如果|假如|[？?]", normalized)
        )
        positive_after_boundary = bool(
            re.search(rf"(?:^|[，,。；;！!]\s*)(?:那|现在|然后|再|先|快|去|把|给|来)?\s*(?:你|她)?\s*{action}.{{0,24}}{outfit}", normalized, re.IGNORECASE)
            or re.search(rf"(?:^|[，,。；;！!]\s*)把.{{0,10}}{outfit}.{{0,8}}{action}", normalized, re.IGNORECASE)
        )
        if question_or_hypothesis and not positive_after_boundary:
            return ""

        negated_change = bool(re.search(rf"(?:不要|别|不用|不必|不许|禁止).{{0,8}}{action}", normalized))
        if negated_change and not re.search(rf"[，,。；;！!].{{0,12}}{action}.{{0,24}}{outfit}", normalized, re.IGNORECASE):
            return ""

        direct_target = bool(
            re.search(rf"(?:让|叫|给|帮)?(?:你|她|角色|星缘|bot|机器人).{{0,16}}{action}", normalized, re.IGNORECASE)
        )
        shared_target = bool(re.search(rf"(?:我们|咱们|咱俩).{{0,8}}{action}", normalized))
        imperative = positive_after_boundary
        if not (direct_target or shared_target or imperative):
            return ""

        meta_feedback = bool(
            re.search(r"掉状态|对不上|文本里|文本里面|旧衣服|原本|之前|怎么又|为什么|bug|BUG|问题", normalized)
        )
        if meta_feedback and not imperative:
            return ""
        return normalized

    def _current_dialogue_outfit_override(
        self,
        *,
        user_id: str = "",
        now: float | None = None,
    ) -> dict[str, Any]:
        data = getattr(self, "data", None)
        if not isinstance(data, dict):
            return {}
        snapshot = data.get("dialogue_outfit_override")
        if not isinstance(snapshot, dict):
            return {}
        check_now = _now_ts() if now is None else now
        if _single_line(snapshot.get("date"), 16) != _today_key():
            return {}
        if _safe_float(snapshot.get("expires_at"), 0) <= check_now:
            return {}
        source_user_id = _single_line(snapshot.get("source_user_id"), 80)
        requested_user_id = _single_line(user_id, 80)
        if requested_user_id and source_user_id != requested_user_id:
            return {}
        instruction = _single_line(snapshot.get("instruction"), 180)
        return dict(snapshot) if instruction else {}

    def _format_dialogue_outfit_continuity_prompt_section(
        self,
        user: dict[str, Any] | None = None,
    ) -> PromptSection:
        user_id = _single_line((user or {}).get("user_id"), 80) if isinstance(user, dict) else ""
        snapshot = self._current_dialogue_outfit_override(user_id=user_id)
        instruction = _single_line(snapshot.get("instruction"), 180)
        body = ""
        if instruction:
            body = (
                f"最近一次明确换装：用户说“{instruction}”。\n"
                "把它理解为当前剧情中已经发生、需要继续承接的服装变化，不要逐字复述。"
                "它高于人格默认服装、今日穿搭参考、旧日程、旧摘要和旧图片中的衣服。"
                "在用户再次明确换装、明确换回，或剧情自然写出新的换衣过程前，不得自行恢复旧服装。"
            )
        return prompt_section(
            key="dialogue.outfit_continuity",
            title="当前会话服装连续性",
            source="daily_state",
            content=body,
        )

    def _record_dialogue_outfit_override_from_interaction(
        self,
        text: str,
        user: dict[str, Any] | None = None,
    ) -> bool:
        instruction = self._detect_dialogue_outfit_change(text)
        if not instruction:
            return False
        now = _now_ts()
        source_user_id = _single_line((user or {}).get("user_id"), 80) if isinstance(user, dict) else ""
        self.data["dialogue_outfit_override"] = {
            "date": _today_key(),
            "instruction": instruction,
            "source": "user_dialogue",
            "source_user_id": source_user_id,
            "created_at": now,
            "expires_at": now + 12 * 3600,
        }
        self._record_detail_interaction_update(
            {
                "source": "用户换装",
                "user_text": instruction,
                "intensity": "强",
                "scope": "直到再次换装或当日结束",
                "immediate_reaction": "Bot 已经按用户这次要求换好衣服，后续动作和场景继续沿用这套服装。",
                "state_updates": [f"当前服装：按用户换装要求“{instruction}”继续"],
                "source_role": "owner",
                "source_user_id": source_user_id,
            }
        )
        return True

    @staticmethod
    def _normalize_schedule_adjustment_scope(scope: Any) -> str:
        text = _single_line(scope, 60)
        if any(token in text for token in ("主动策略", "主动消息", "主动频率")):
            return "proactive_only"
        if any(token in text for token in ("直到", "到家", "今晚到", "缓冲期")):
            return "until_condition"
        if any(token in text for token in ("今日后续", "今天剩余", "全天后续")):
            return "rest_of_day"
        if "下一段" in text:
            return "current_and_next"
        if any(token in text for token in ("当前段", "当前休息段")):
            return "current_only"
        return "current_and_next"

    def _format_schedule_adjustments_for_prompt(self, segment: dict[str, Any] | None = None) -> str:
        raw = self.data.get("schedule_adjustments", [])
        now = _now_ts()
        kept = []
        lines = []
        override_lines = []
        override = self._sleep_delay_override_state(now=now)
        if override:
            until_text = _single_line(override.get("until_text"), 24)
            override_lines.append(f"- 临时延后休息｜强｜今晚：到 {until_text} 前按用户临时陪聊约定处理,不要把当前睡眠段当成必须沉默。")
            override_lines.append("  承接要求：只影响今晚；到点后自然收声或回到休息,不要写成长期熬夜习惯。")
        outfit_override = self._current_dialogue_outfit_override(now=now)
        outfit_instruction = _single_line(outfit_override.get("instruction"), 180)
        if outfit_instruction:
            override_lines.append(
                f"- 用户换装｜强｜直到再次换装：用户已明确改变角色服装：“{outfit_instruction}”。"
            )
            override_lines.append(
                "  承接要求：把最新换装写入当前服装状态并延续到后续片段；日程或旧摘要里的默认穿搭只能补空白，不能把服装复原。"
            )
        if isinstance(raw, list) and raw:
            for item in raw:
                if not isinstance(item, dict):
                    continue
                if _single_line(item.get("source_role"), 20) != "owner":
                    continue
                expires_at = _safe_float(item.get("expires_at"), 0)
                if expires_at > 0 and expires_at <= now:
                    continue
                date_text = _single_line(item.get("date"), 16)
                if date_text and date_text != _today_key():
                    continue
                kept.append(item)
                note = _single_line(item.get("note"), 120)
                source = _single_line(item.get("source"), 24)
                intensity = _single_line(item.get("intensity"), 16)
                scope = _single_line(item.get("scope"), 30)
                scope_key = _single_line(item.get("scope_key"), 24) or self._normalize_schedule_adjustment_scope(scope)
                if segment is None and scope_key == "proactive_only":
                    continue
                if isinstance(segment, dict):
                    target_index = _safe_int(segment.get("index"), -1, minimum=-1)
                    anchor_index = _safe_int(item.get("anchor_segment_index"), -1, minimum=-1)
                    if anchor_index < 0:
                        current_segment = self._current_detail_segment_for_update()
                        anchor_index = _safe_int((current_segment or {}).get("index"), target_index, minimum=-1)
                    if scope_key == "current_only" and target_index != anchor_index:
                        continue
                    if scope_key == "current_and_next" and target_index not in {anchor_index, anchor_index + 1}:
                        continue
                    if scope_key in {"rest_of_day", "until_condition", "proactive_only"} and target_index < anchor_index:
                        continue
                if note:
                    meta = "｜".join(part for part in (source or "互动", intensity, scope, f"作用域={scope_key}") if part)
                    lines.append(f"- {meta}：{note}")
                immediate = _single_line(item.get("immediate_reaction"), 120)
                if immediate:
                    lines.append(f"  即时反应：{immediate}")
                updates = item.get("state_updates")
                if isinstance(updates, list) and updates:
                    update_text = "；".join(
                        _single_line(update, 60)
                        for update in updates
                        if _single_line(update, 60)
                    )
                    if update_text:
                        lines.append(f"  状态变量更新：{update_text}")
                carry = _single_line(item.get("carry_rule"), 120)
                if carry:
                    lines.append(f"  承接要求：{carry}")
                if scope_key == "proactive_only":
                    lines.append("  作用限制：只允许影响 proactive_events，不得改写粗日程、summary、today_events、state_variables 或 presence_status。")
            if len(kept) != len(raw):
                self.data["schedule_adjustments"] = kept[-12:]
        if override_lines:
            lines.extend(override_lines)
        return "\n".join(lines[-12:]) if lines else "（暂无）"

    def _current_detail_segment_for_update(self) -> dict[str, Any] | None:
        plan = self.data.get("daily_plan", {})
        if not isinstance(plan, dict) or not self._is_plan_date_active(plan.get("date")):
            return None
        now_minutes = self._effective_plan_now_minutes(str(plan.get("date") or ""))
        if now_minutes is None:
            return None
        for segment in self._collect_detail_segments(plan, {}):
            start = _safe_int(segment.get("start"), 0)
            end = _safe_int(segment.get("end"), self._segment_end_minutes(start, segment.get("item")))
            lead = max(0, _safe_int(runtime_persona_setting(self, "detail_enhancement_lead_minutes", 3), 3, 0))
            if start - lead <= now_minutes < end:
                return segment
        return None

    def _current_detail_snapshot_for_update(self) -> dict[str, Any] | None:
        """Return the finished detail snapshot for the clock-current plan segment."""

        segment = self._current_detail_segment_for_update()
        if not isinstance(segment, dict):
            return None
        plan_date = _single_line(segment.get("plan_date"), 16)
        now_minutes = self._effective_plan_now_minutes(plan_date)
        if now_minutes is None:
            return None
        start = _safe_int(segment.get("start"), -1, minimum=-1)
        end = _safe_int(segment.get("end"), -1, minimum=-1)
        if start < 0 or end < 0:
            return None
        if end <= start:
            end += 24 * 60
        # Detail generation may select the next segment during its lead
        # window. Passive state material must remain tied to the actual clock
        # window so it cannot describe the next scene early.
        if not (start <= now_minutes < end):
            return None
        segment_item = segment.get("item")
        if isinstance(segment_item, dict):
            lifecycle = self._normalize_schedule_lifecycle_status(
                segment_item.get("lifecycle_status") or segment_item.get("status")
            )
            # A finished detail snapshot still describes its parent plan. If
            # that plan was changed, deferred, cancelled, or already closed,
            # the old atmosphere must not survive as current prompt material.
            if lifecycle not in {"", "planned", "active"}:
                return None
        enhanced = self.data.get("detail_enhanced_segments", {})
        if not isinstance(enhanced, dict):
            return None
        snapshot = enhanced.get(str(segment.get("key") or ""))
        if not isinstance(snapshot, dict):
            return None
        status = _single_line(snapshot.get("status"), 24).lower()
        if status and status != "done":
            return None
        return snapshot

    def _current_detail_state_variables(self) -> list[dict[str, str]]:
        segment = self._current_detail_segment_for_update()
        if not segment:
            return []
        enhanced = self.data.get("detail_enhanced_segments", {})
        if not isinstance(enhanced, dict):
            return []
        snapshot = enhanced.get(str(segment.get("key") or ""))
        if not isinstance(snapshot, dict):
            return []
        variables = snapshot.get("state_variables", [])
        if not isinstance(variables, list):
            return []
        return [item for item in variables if isinstance(item, dict)]

    def _detect_schedule_adjustment_from_interaction(self, text: str) -> dict[str, Any] | None:
        normalized = _single_line(text, 220)
        if not normalized:
            return None
        current_variables = self._current_detail_state_variables()
        variable_text = " ".join(
            f"{item.get('name', '')}:{item.get('value', '')} {item.get('note', '')}"
            for item in current_variables[:8]
        )
        def payload(
            *,
            source: str,
            note: str,
            immediate_reaction: str,
            state_updates: list[str],
            intensity: str = "中",
            scope: str = "当前段和下一段",
            carry_rule: str = "后续细化可根据这次用户介入留下合适的状态余味；若没有实际改变任务、作息、边界或共同场景，不必扩写成生活事件。",
            **extra: Any,
        ) -> dict[str, Any]:
            data = {
                "source": source,
                "note": note,
                "immediate_reaction": immediate_reaction,
                "state_updates": state_updates,
                "intensity": intensity,
                "scope": scope,
                "carry_rule": carry_rule,
                "user_text": normalized,
            }
            data.update(extra)
            return data

        current_item = self._get_current_plan_item(self.data.get("daily_plan", {}))
        sleep_delay = self._detect_sleep_delay_request(normalized)
        if sleep_delay:
            until_text = _single_line(sleep_delay.get("until_text"), 24)
            return payload(
                source="临时延后休息",
                note=f"用户今晚希望晚点休息或陪聊；到 {until_text} 前暂时不要把睡眠段当成必须沉默,但这只是今晚的临时约定。",
                immediate_reaction="Bot 会把今晚的节奏稍微放慢并留出陪聊余地,但不会把这当成长期作息改变。",
                state_updates=[f"休息安排：今晚临时延后到 {until_text}", "清醒程度：陪聊但低负担", "后续安排：到点后自然收声或睡回去"],
                intensity="强",
                scope=f"今晚到 {until_text}",
                carry_rule="只影响今晚和当前休息段；后续细化可以保留轻微陪聊/等待感,但不得把它写成长期熬夜习惯,到点后应自然收声或回到休息。",
                sleep_delay_until_ts=sleep_delay.get("until_ts"),
                sleep_delay_until_text=until_text,
                sleep_delay_explicit_time=bool(sleep_delay.get("explicit_time")),
            )
        is_actual_rest_segment = (
            self._sleep_rest_window_active()
            and not self._sleep_delay_override_state(clear_expired=True)
            and self._is_sleepy_plan_item(current_item)
        )
        if is_actual_rest_segment and not re.search(r"别吵|别发|别找|安静|闭嘴|先别|不要来|忙|我有事|没空", normalized):
            runtime_before = self._sleep_runtime_state()
            last_woken = _safe_float(runtime_before.get("last_woken_at"), _safe_float(runtime_before.get("updated_at"), 0))
            last_user_text = _single_line(runtime_before.get("last_user_text"), 80)
            current_user_text = _single_line(normalized, 80)
            consumed_at = _safe_float(runtime_before.get("last_wakeup_context_consumed_at"), 0)
            same_wakeup_message = bool(
                runtime_before.get("phase") == "woken"
                and last_user_text
                and last_user_text == current_user_text
                and consumed_at < last_woken
                and _now_ts() - last_woken < 120
            )
            if same_wakeup_message:
                runtime_before["last_wakeup_context_consumed_at"] = _now_ts()
                return payload(
                    source="睡眠中被用户唤醒",
                    note="当前日程处于休息/睡眠段,这条消息已经在休息闸门放行时登记为唤醒；不要重复计数,语气只保留刚醒的慢一点和轻一点。",
                    immediate_reaction="Bot 刚被这条消息轻轻叫醒,会慢一点看清内容再回应。",
                    state_updates=["清醒程度：刚被唤醒/迷糊", "语气：轻、短、带睡意", "后续安排：用户不继续打扰就继续睡"],
                    intensity="强",
                    scope="当前休息段和后续短时间",
                    carry_rule="回复可以带一点刚醒的气息,但不得降低理解、事实和回答质量；不要再表现成又被叫醒一次。",
                )
            within_awake_grace = (
                runtime_before.get("phase") == "woken"
                and self._sleep_awake_grace_seconds() > 0
                and _now_ts() - last_woken < self._sleep_awake_grace_seconds()
            )
            if within_awake_grace:
                runtime_before["last_user_text"] = _single_line(normalized, 80)
                return payload(
                    source="睡眠中醒后续聊",
                    note="当前日程仍是休息/睡眠段,但 Bot 已在醒后缓冲期内；这是被叫醒后的连续对话,不再按再次唤醒处理。",
                    immediate_reaction="Bot 还没完全精神起来,但已经在接着聊天,不会每句话都像重新被吵醒。",
                    state_updates=["清醒程度：醒后续聊/慢慢清醒", "语气：仍轻一点,但不重复表演被叫醒", "后续安排：停聊后再自然睡回去"],
                    intensity="中",
                    scope="醒后缓冲期",
                    carry_rule="后续回复保持连续聊天感,不要写成每条消息都重新惊醒；用户继续聊时可以逐渐清醒一点。",
                )
            sleep_runtime = self._mark_sleep_woken_by_user(normalized)
            prior_wakes = 0
            segment = self._current_detail_segment_for_update()
            enhanced = self.data.get("detail_enhanced_segments", {})
            if isinstance(segment, dict) and isinstance(enhanced, dict):
                snapshot = enhanced.get(str(segment.get("key") or ""))
                updates = snapshot.get("interaction_updates", []) if isinstance(snapshot, dict) else []
                if isinstance(updates, list):
                    prior_wakes = sum(
                        1 for update in updates
                        if isinstance(update, dict) and "唤醒" in str(update.get("source") or "")
                    )
            prior_wakes = max(prior_wakes, _safe_int(sleep_runtime.get("woken_count"), 1, 1) - 1)
            if prior_wakes > 0:
                return payload(
                    source="睡眠中再次被唤醒",
                    note="当前日程处于休息/睡眠段,用户又发来消息；回复语气应带一点被重新叫醒的迟钝感,但必须清楚理解用户的话,不要埋怨用户。若用户继续聊,可以慢慢醒一点；若用户停下,Bot 会很快继续睡回去。",
                    immediate_reaction="Bot 又被消息轻轻拽醒一下,语气会慢半拍,但会看清用户说了什么再回应。",
                    state_updates=["清醒程度：再次被唤起/半梦半醒", "语气：慢半拍、短一点", "后续安排：用户不继续打扰就继续睡"],
                    intensity="中",
                    scope="当前休息段",
                    carry_rule="当前段回复必须有刚被重新唤起的语气感觉,但不得降低理解和回答质量；如果后续没有用户消息,下一段细化应让 Bot 继续休息或睡回去。",
                )
            return payload(
                source="睡眠中被用户唤醒",
                note="当前日程处于休息/睡眠段,用户发来消息把 Bot 轻轻叫醒；回复语气应像刚醒或半梦半醒,不要立刻精神饱满,但必须看懂并正面回应用户。若用户没有继续打扰,后续应自然睡回去或继续休息。",
                immediate_reaction="Bot 会先带着睡意看一眼消息,语气慢一点,但不会乱接或漏掉用户真正的问题。",
                state_updates=["清醒程度：刚被唤醒/迷糊", "语气：轻、短、带睡意", "后续安排：用户不继续打扰就继续睡"],
                intensity="强",
                scope="当前休息段和后续短时间",
                carry_rule="回复与后续细化必须承接“刚被用户唤醒”的语气状态,但不得牺牲理解、事实和回答质量；如果没有连续聊天,不要强行清醒活动,要睡回去或继续休息。",
            )

        if re.search(r"换元|代入|公式|思路|解法|答案|步骤|这题|函数题|数学题|阅读题|作文|作业|代码|报错|bug|怎么写|这样做|你试试|应该是", normalized):
            updates = ["学习/作业进度：因用户提供思路而推进", "情绪：卡住->松一口气"]
            if re.search(r"卡|函数|数学|作业", variable_text):
                updates.insert(0, "卡住点：用户提示后得到缓解")
            return payload(
                source="用户帮助",
                note="用户刚刚提供了和当前任务相关的帮助；当前任务不再按原本卡住状态推进,后续时间段要继承“被帮助后松动/推进”的结果。",
                immediate_reaction="看到用户给的思路后,Bot 先照着试了一下；如果确实对上当前任务,心里会明显松一口气。",
                state_updates=updates,
                intensity="强",
                scope="当前段、下一段、今日后续",
                carry_rule="如果后续仍写学习/作业/任务,必须体现已经被用户提示影响过：进度推进、情绪松动、或换了做法。",
            )
        food_feedback = self._detect_food_feedback(normalized)
        if food_feedback.get("is_food") and (food_feedback.get("suggestion") or food_feedback.get("bot_directed")):
            meal = _single_line(food_feedback.get("meal"), 20) or "饭点"
            food_hint = _single_line(food_feedback.get("food_hint"), 80)
            return payload(
                source="饮食反馈",
                note=f"用户刚刚给了关于{meal}或吃什么的反馈；后续日程要把这当成一个已经影响生活安排的小事实,可以调整饭点、胃口、准备食物或晚餐选择。",
                immediate_reaction="Bot 会把这句当成一个可执行的小主意,刚才悬着的“吃什么”落下来一点。",
                state_updates=[f"{meal}选择：受用户反馈影响", "饥饿感：开始回落", "后续安排：可能按反馈调整吃饭/点餐/做饭"],
                intensity="中",
                scope="当前段和今日后续饭点",
                carry_rule=f"后续细化如果涉及吃饭、晚餐、休息或外出,要自然承接用户这句饮食反馈：{food_hint}。不要生硬复述,也不要像没有问过一样重置。",
            )
        self_reported_care_action = bool(
            re.search(
                r"(?:我|俺|本人|这边|我们|咱们|咱).{0,12}(?:去睡|早点睡|睡觉|休息|先洗澡|先吃饭|吃点|喝水|躺会|停一下|歇会)",
                normalized,
            )
        )
        if not self_reported_care_action and re.search(r"去睡|早点睡|睡觉|休息|别写了|别弄了|先洗澡|先吃饭|吃点|喝水|别熬|躺会|停一下|歇会", normalized):
            return payload(
                source="用户照顾",
                note="用户刚刚给了休息或照顾指令；后续节奏应明显调慢,更可能提前收尾、补充休息、喝水吃饭或把任务延后。",
                immediate_reaction="Bot 看到这句会停一下手里的事,嘴上可能不立刻答应,但动作会慢下来一点。",
                state_updates=["体力：消耗放缓/略微回稳", "情绪：被照顾后的柔和", "后续安排：更倾向提前收尾或补充休息"],
                intensity="强",
                scope="当前段和今日后续",
                carry_rule="下一段不能完全无视这句照顾提醒；至少要在节奏、体力或收尾方式上留下影响。",
            )
        shared_location_signal = bool(re.search(r"一起|我们|咱们|咱俩|带你|带我|陪你|陪我|跟你|跟我|走吧|出发吧", normalized))
        outward_action_signal = bool(re.search(r"出发|出门|出去|去吃|去逛|去买|去玩|上车|下车|走了|走起|换鞋|拿钥匙|等车|打车|坐车|地铁|公交|到了|排队|找位子|点单|点餐|下单", normalized))
        if shared_location_signal and outward_action_signal:
            self._apply_dialogue_location_override("外面")
            return payload(
                source="用户带出/同行",
                note="用户刚刚带角色出门或一起外出；当前位置应从家里切换到外面,后续细化要承接外出场景,不要把角色写回家里。",
                immediate_reaction="Bot 会赶紧收拾一下东西,跟着用户往外走,可能边走边看手机或整理衣服。",
                state_updates=["位置：家里->外面", "活动：跟随用户外出", "情绪：略兴奋或期待"],
                intensity="强",
                scope="当前段和今日后续直到回家线索出现",
                carry_rule="后续细化和状态注入必须把角色位置保持在'外面',直到用户明确说回家、到家或日程自然过渡到居家时段；不要把角色写回沙发、卧室或家里。",
            )
        shared_return_signal = bool(re.search(r"一起|我们|咱们|咱俩|带你|带我|陪你|陪我|跟你|跟我|回家吧|送你回", normalized))
        return_home_signal = bool(re.search(r"回来了|到家了|回家了|进家门|开门|进门|回到.*家|到家|安全到家", normalized))
        if shared_return_signal and return_home_signal:
            self._apply_dialogue_location_override("家里")
            return payload(
                source="用户带回/回家",
                note="用户和角色刚刚回到家；当前位置应从外面切换回家里,后续细化要承接回家后场景。",
                immediate_reaction="Bot 会松一口气,可能踢掉鞋子或把东西放下,瘫到沙发上。",
                state_updates=["位置：外面->家里", "活动：回到居家", "情绪：放松"],
                intensity="中",
                scope="当前段和下一段",
                carry_rule="后续细化可以把角色写回家里场景,但不要立刻恢复出门前的精确活动,要体现外出后的余味。",
            )
        explicit_appointment_signal = bool(re.search(
            r"(?:约好|说好|定了|晚点(?:一起|聊|打电话|语音|开黑|看)|(?:一起|我们|咱们|咱俩|陪你|陪我|等你|等我|跟你|跟我).{0,18}(?:待会|一会|晚上|明天|等下|见面|打电话|语音|开黑|看))",
            normalized,
        ))
        if explicit_appointment_signal:
            return payload(
                source="用户约定",
                note="用户刚刚给出陪伴、等待、稍后一起做事或约定线索；后续日程应保留期待/等待/预留空档,不要像没有约定一样推进。",
                immediate_reaction="Bot 会把这句话在心里放一下,后续做事时更容易分神看时间或看消息。",
                state_updates=["等待/期待：上升", "后续安排：可能预留空档", "主动话题：围绕约定轻轻回勾"],
                intensity="强",
                scope="今日后续",
                carry_rule="后续主动契机和细化事件要把这个约定当作开放话头,可以等待、预留时间或轻轻提醒。",
            )
        care = self._detect_care_feedback(normalized)
        if care.get("is_care"):
            return payload(
                source="用户关心",
                note="用户刚刚给了关心或照顾提醒；后续日程需要从低落/烦躁向被安抚后的柔和、慢慢回稳偏移,不要升级破坏性冲动。",
                immediate_reaction="Bot 看到关心会先顿一下,语气和动作都比刚才软一点。",
                state_updates=["情绪：低落/烦躁->被接住一点", "体力：主观疲惫感略降"],
                intensity="中",
                scope="当前段、下一段、情绪底色",
            )
        if re.search(r"摸摸|贴贴|抱抱|亲亲|揉揉|蹭蹭|摸头|陪你|哄你|乖|不难过|别难过|没关系|辛苦了|抱一下", normalized):
            return payload(
                source="安慰互动",
                note="用户刚刚在安慰或亲近；后续日程应保留一点被接住的余温,表达更软一些,不要继续单向累积负面情绪。",
                immediate_reaction="Bot 会把刚才绷着的劲松下来一点,可能短暂地想贴近用户。",
                state_updates=["情绪：紧绷->柔和", "亲近感：上升"],
                intensity="中",
                scope="当前段和情绪底色",
            )
        if re.search(r"别生气|不要烦|冷静|别急|别砸|别摔|别骂|别打", normalized):
            return payload(
                source="边界提醒",
                note="用户提醒降低攻击性或破坏性表达；后续日程必须把烦躁写成收着的动作,避免砸、摔、扔、打这类冲动。",
                immediate_reaction="Bot 会把那点冲劲压回去,改成少说两句或把东西放远一点。",
                state_updates=["情绪：冲动->收住", "行为边界：避免破坏性动作"],
                intensity="强",
                scope="今日后续",
                carry_rule="后续所有细化都必须遵守这个边界,不能再输出破坏性动作或攻击性冲动。",
            )
        if re.search(r"别吵|别发|别找|安静|闭嘴|先别|不要来|忙|我有事|没空", normalized):
            return payload(
                source="用户边界",
                note="用户刚刚表达边界或忙碌；后续主动应明显收敛,减少追问和贴近,让日程转向自我消化。",
                immediate_reaction="Bot 会把原本想继续靠近的动作收住,把消息窗口放到一边。",
                state_updates=["主动欲：下降", "关系状态：后退一点", "后续安排：转向自我消化"],
                intensity="强",
                scope="今日后续主动策略",
                carry_rule="后续主动消息必须降低频率和压迫感,不要把边界当作可撒娇突破的对象。",
            )
        return None

    def _record_schedule_adjustment_from_interaction(self, text: str, user: dict[str, Any] | None = None) -> bool:
        if self._private_user_role(user) != "owner":
            return False
        outfit_updated = self._record_dialogue_outfit_override_from_interaction(text, user)
        adjustment = self._detect_schedule_adjustment_from_interaction(text)
        if not adjustment:
            return outfit_updated
        raw = self.data.setdefault("schedule_adjustments", [])
        if not isinstance(raw, list):
            raw = []
            self.data["schedule_adjustments"] = raw
        note = _single_line(adjustment.get("note"), 140)
        if not note:
            return False
        now = _now_ts()
        intensity = _single_line(adjustment.get("intensity"), 16) or "中"
        ttl_hours = 18 if intensity == "强" else 10 if intensity == "中" else 6
        current_segment = self._current_detail_segment_for_update()
        anchor_index = _safe_int((current_segment or {}).get("index"), -1, minimum=-1)
        scope_key = self._normalize_schedule_adjustment_scope(adjustment.get("scope"))
        source = _single_line(adjustment.get("source"), 24)
        if source == "用户带回/回家":
            raw[:] = [
                old
                for old in raw
                if not (
                    isinstance(old, dict)
                    and (
                        _single_line(old.get("condition_key"), 32) == "return_home"
                        or (
                            _single_line(old.get("scope_key"), 24) == "until_condition"
                            and "回家" in _single_line(old.get("scope"), 60)
                        )
                    )
                )
            ]
        item = {
            "date": _today_key(),
            "source": source,
            "note": note,
            "immediate_reaction": _single_line(adjustment.get("immediate_reaction"), 140),
            "state_updates": adjustment.get("state_updates", []),
            "user_text": _single_line(adjustment.get("user_text"), 120),
            "intensity": intensity,
            "scope": _single_line(adjustment.get("scope"), 40),
            "scope_key": scope_key,
            "carry_rule": _single_line(adjustment.get("carry_rule"), 160),
            "source_role": "owner",
            "source_user_id": _single_line((user or {}).get("user_id"), 80),
            "created_at": now,
            "expires_at": now + ttl_hours * 3600,
        }
        if anchor_index >= 0:
            item["anchor_segment_index"] = anchor_index
            item["anchor_segment_key"] = _single_line((current_segment or {}).get("key"), 120)
        if scope_key == "until_condition" and (source == "用户带出/同行" or "回家" in item["scope"]):
            item["condition_key"] = "return_home"
        sleep_delay_until = _safe_float(adjustment.get("sleep_delay_until_ts"), 0)
        if sleep_delay_until > now:
            item["sleep_delay_until_ts"] = sleep_delay_until
            item["sleep_delay_until_text"] = _single_line(adjustment.get("sleep_delay_until_text"), 24)
            item["sleep_delay_explicit_time"] = bool(adjustment.get("sleep_delay_explicit_time"))
            self._apply_sleep_delay_override(
                {
                    "until_ts": sleep_delay_until,
                    "until_text": item["sleep_delay_until_text"],
                    "explicit_time": item["sleep_delay_explicit_time"],
                    "user_text": item["user_text"],
                },
                text=item["user_text"],
            )
        self._record_detail_interaction_update(item)
        plan = self.data.get("daily_plan", {})
        current_item = self._get_current_plan_item(plan) if isinstance(plan, dict) else None
        if isinstance(current_item, dict):
            current_item["lifecycle_status"] = "changed"
            current_item["changed_at"] = self._environment_now().strftime("%H:%M")
            current_item["change_reason"] = _single_line(adjustment.get("source") or note, 80)
        if raw and isinstance(raw[-1], dict) and raw[-1].get("note") == note:
            raw[-1].update(item)
        else:
            raw.append(item)
            del raw[:-12]
        self._invalidate_detail_after_interaction(now=now)
        return True

    @staticmethod
    def _parse_state_update_text(update: Any) -> tuple[str, str, str]:
        text = _single_line(update, 120)
        if not text:
            return "", "", ""
        if "：" in text:
            name, value = text.split("：", 1)
        elif ":" in text:
            name, value = text.split(":", 1)
        else:
            return text[:24], "已受用户介入影响", text
        return _single_line(name, 32), _single_line(value, 60), text

    def _apply_interaction_to_snapshot_state(self, snapshot: dict[str, Any], item: dict[str, Any]) -> None:
        raw_updates = item.get("state_updates", [])
        if not isinstance(raw_updates, list):
            raw_updates = []
        variables = snapshot.setdefault("state_variables", [])
        if not isinstance(variables, list):
            variables = []
            snapshot["state_variables"] = variables
        index_by_name = {
            _single_line(variable.get("name"), 32): variable
            for variable in variables
            if isinstance(variable, dict) and _single_line(variable.get("name"), 32)
        }
        for update in raw_updates:
            name, value, note = self._parse_state_update_text(update)
            if not name:
                continue
            variable = index_by_name.get(name)
            if isinstance(variable, dict):
                variable["value"] = value or variable.get("value") or "已更新"
                variable["note"] = f"用户介入：{note}" if note else "用户介入后更新"
            else:
                variable = {
                    "name": name,
                    "value": value or "已更新",
                    "note": f"用户介入：{note}" if note else "用户介入后更新",
                }
                variables.append(variable)
                index_by_name[name] = variable
        summary = _single_line(snapshot.get("summary"), 140)
        reaction = _single_line(item.get("immediate_reaction"), 90)
        if reaction and reaction not in summary:
            snapshot["summary"] = _single_line(
                f"{summary}；用户介入后：{reaction}" if summary else f"用户介入后：{reaction}",
                160,
            )

    def _record_detail_interaction_update(self, item: dict[str, Any]) -> None:
        segment = self._current_detail_segment_for_update()
        if not segment:
            return
        enhanced = self.data.get("detail_enhanced_segments", {})
        if not isinstance(enhanced, dict):
            return
        key = str(segment.get("key") or "")
        snapshot = enhanced.get(key)
        if not isinstance(snapshot, dict):
            return
        updates = snapshot.setdefault("interaction_updates", [])
        if not isinstance(updates, list):
            updates = []
            snapshot["interaction_updates"] = updates
        source = _single_line(item.get("source"), 24)
        if source == "用户换装":
            updates[:] = [
                update
                for update in updates
                if not (isinstance(update, dict) and _single_line(update.get("source"), 24) == source)
            ]
        updates.append(
            {
                "at": self._environment_now().strftime("%H:%M"),
                "source": source,
                "user_text": _single_line(item.get("user_text"), 80),
                "intensity": _single_line(item.get("intensity"), 16),
                "scope": _single_line(item.get("scope"), 40),
                "reaction": _single_line(item.get("immediate_reaction"), 140),
                "state_updates": item.get("state_updates", []),
                "source_role": _single_line(item.get("source_role"), 20),
                "source_user_id": _single_line(item.get("source_user_id"), 80),
            }
        )
        del updates[:-6]
        self._apply_interaction_to_snapshot_state(snapshot, item)

    def _cleanup_false_sleep_interaction_updates(self) -> bool:
        plan = self.data.get("daily_plan", {})
        enhanced = self.data.get("detail_enhanced_segments", {})
        if not isinstance(plan, dict) or not isinstance(enhanced, dict):
            return False
        false_sources = {"睡眠中被用户唤醒", "睡眠中再次被唤醒", "睡眠中醒后续聊"}
        false_user_texts: set[str] = set()
        changed = False
        for segment in self._collect_detail_segments(plan, {}):
            if self._is_sleepy_plan_item(segment.get("item")):
                continue
            snapshot = enhanced.get(str(segment.get("key") or ""))
            if not isinstance(snapshot, dict):
                continue
            updates = snapshot.get("interaction_updates", [])
            if not isinstance(updates, list):
                continue
            removed = [
                item for item in updates
                if isinstance(item, dict) and _single_line(item.get("source"), 24) in false_sources
            ]
            if not removed:
                continue
            snapshot["interaction_updates"] = [item for item in updates if item not in removed]
            removed_state_names: set[str] = set()
            summary = str(snapshot.get("summary") or "")
            for item in removed:
                user_text = _single_line(item.get("user_text"), 120)
                if user_text:
                    false_user_texts.add(user_text)
                for state_update in item.get("state_updates", []) if isinstance(item.get("state_updates"), list) else []:
                    name, _value, _note = self._parse_state_update_text(state_update)
                    if name:
                        removed_state_names.add(name)
                reaction = _single_line(item.get("reaction"), 140)
                if reaction:
                    summary = summary.replace(f"；用户介入后：{reaction}", "").replace(f"用户介入后：{reaction}", "")
            snapshot["summary"] = _single_line(summary, 160)
            remaining_state_names = {
                self._parse_state_update_text(state_update)[0]
                for item in snapshot["interaction_updates"]
                if isinstance(item, dict) and isinstance(item.get("state_updates"), list)
                for state_update in item.get("state_updates", [])
            }
            variables = snapshot.get("state_variables", [])
            if isinstance(variables, list):
                snapshot["state_variables"] = [
                    variable for variable in variables
                    if not (
                        isinstance(variable, dict)
                        and _single_line(variable.get("name"), 32) in removed_state_names - remaining_state_names
                        and str(variable.get("note") or "").startswith("用户介入：")
                    )
                ]
            changed = True
        if false_user_texts:
            adjustments = self.data.get("schedule_adjustments", [])
            if isinstance(adjustments, list):
                kept = [
                    item for item in adjustments
                    if not (
                        isinstance(item, dict)
                        and _single_line(item.get("source"), 24) in false_sources
                        and _single_line(item.get("user_text"), 120) in false_user_texts
                    )
                ]
                if len(kept) != len(adjustments):
                    self.data["schedule_adjustments"] = kept
                    changed = True
            runtime = self.data.get("daily_state", {}).get("sleep_runtime") if isinstance(self.data.get("daily_state"), dict) else None
            if isinstance(runtime, dict) and runtime.get("phase") in {"woken", "sleeping_again"}:
                if _single_line(runtime.get("last_user_text"), 120) in false_user_texts:
                    runtime.update(
                        {
                            "phase": "awake",
                            "label": self._sleep_phase_label("awake"),
                            "updated_at": _now_ts(),
                            "last_event": "已清理普通休闲段的错误睡眠唤醒记录",
                            "source": "cleanup",
                        }
                    )
                    changed = True
        if changed:
            logger.info("已清理普通休闲段的错误睡眠唤醒记录")
        return changed

    def _invalidate_detail_after_interaction(self, *, now: float | None = None) -> None:
        plan = self.data.get("daily_plan", {})
        if not isinstance(plan, dict) or not self._is_plan_date_active(plan.get("date")):
            return
        now_minutes = self._effective_plan_now_minutes(str(plan.get("date") or ""))
        if now_minutes is None:
            return
        enhanced = self.data.get("detail_enhanced_segments", {})
        if isinstance(enhanced, dict):
            for segment in self._collect_detail_segments(plan, {}):
                start = _safe_int(segment.get("start"), 0)
                if start > now_minutes:
                    key = str(segment.get("key") or "")
                    if key in enhanced:
                        enhanced.pop(key, None)
        story_plan = self.data.get("daily_story_plan", {})
        if isinstance(story_plan, dict):
            for key in ("today_events", "proactive_events"):
                items = story_plan.get(key, [])
                if not isinstance(items, list):
                    continue
                kept = []
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    start, end = self._parse_window_minutes(str(item.get("window") or ""))
                    if start is None or end is None:
                        kept.append(item)
                        continue
                    if end < start:
                        end += 24 * 60
                    if start <= now_minutes:
                        kept.append(item)
                story_plan[key] = kept

    def _infer_location_from_text(self, text: str) -> str:
        normalized = _single_line(text, 200)
        if not normalized:
            return ""
        location_rules = [
            (("被窝", "床上", "床边", "卧室", "房间", "书桌", "台灯", "家里", "客厅", "沙发", "洗漱台", "餐桌"), "家里"),
            (("教室", "课间", "食堂", "校门", "走廊", "操场", "上课", "下课", "自习", "老师", "书包", "制服"), "学校"),
            (("工位", "会议", "办公室", "上班", "下班", "通勤", "打卡"), "工作场所"),
            (("便利店", "超市", "商店"), "便利店附近"),
            (("路上", "街上", "出门", "楼下", "外面", "街边", "回家路上", "校门口"), "外面"),
            (("楼梯口", "走廊栏杆", "窗边", "阳台"), "过道或窗边"),
        ]
        for keywords, label in location_rules:
            if any(keyword in normalized for keyword in keywords):
                return label
        return ""

    def _infer_location_from_plan_context(
        self,
        *,
        plan: dict[str, Any] | None = None,
        detail: dict[str, Any] | None = None,
    ) -> str:
        candidates: list[str] = []
        detail_allowed = self._detail_model_location_policy_allowed(detail)
        if isinstance(detail, dict) and detail_allowed:
            model_location = _single_line(detail.get("location"), 60)
            if model_location:
                return model_location
            for key in ("summary", "scene", "event", "topic"):
                text = _single_line(detail.get(key), 160)
                if text:
                    candidates.append(text)
            for list_key in ("today_events", "proactive_events"):
                raw_items = detail.get(list_key)
                if not isinstance(raw_items, list):
                    continue
                for item in raw_items[:6]:
                    if not isinstance(item, dict):
                        continue
                    candidates.append(
                        " ".join(
                            _single_line(item.get(key), 80)
                            for key in ("scene", "event", "content", "detail", "description", "topic", "why")
                            if _single_line(item.get(key), 80)
                        )
                    )
        plan = plan if isinstance(plan, dict) else self.data.get("daily_plan", {})
        current_item = self._get_current_plan_item(plan if isinstance(plan, dict) else {})
        if isinstance(current_item, dict):
            candidates.append(
                " ".join(
                    _single_line(current_item.get(key), 120)
                    for key in ("activity", "mood", "message_seed")
                    if _single_line(current_item.get(key), 120)
                )
            )
        # Do not inspect neighboring raw plan rows here.  They are future or
        # unverified projections and their clock distance cannot establish the
        # Bot's current location.  A current item above is already policy /
        # runtime qualified; a generated detail location is handled separately
        # by ``_refresh_daily_state_location_from_plan``.
        for text in candidates:
            inferred = self._infer_location_from_text(text)
            if inferred:
                return inferred
        return ""

    def _detail_model_location_policy_allowed(self, detail: dict[str, Any] | None = None) -> bool:
        """Allow a coherent schedule projection while keeping observed location distinct."""

        policy_getter = getattr(self, "_agenda_disclosure_view", None)
        if not callable(policy_getter):
            # Lightweight harnesses and legacy callers do not have C3 policy;
            # preserve their historical local projection behavior.
            return True
        payload = detail if isinstance(detail, dict) else {}
        evidence_kind = _single_line(payload.get("evidence_kind"), 48).lower()
        eligibility = _single_line(payload.get("fact_eligibility"), 48).lower()
        refs = payload.get("source_refs")
        has_refs = isinstance(refs, (list, tuple, set)) and any(_single_line(ref, 160) for ref in refs)
        if evidence_kind not in {"tool_action", "external_record"} or eligibility != "current_observed" or not has_refs:
            # A generated detail is part of the character's simulated day. It
            # may keep the active scene coherent, but is not observed evidence.
            location = _single_line(payload.get("location"), 60)
            basis = self._normalize_schedule_basis(payload.get("location_basis"), default=[])
            return bool(location and basis)
        try:
            view = policy_getter("current_fact", now=self._environment_now(), max_entries=128)
            entries = view.get("entries", []) if isinstance(view, dict) else getattr(view, "entries", [])
        except Exception:
            return False
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict):
                continue
            if _single_line(entry.get("subject_actor_id"), 120) != "bot_self":
                continue
            if _single_line(entry.get("fact_eligibility"), 48).lower() != "current_observed":
                continue
            entry_refs = entry.get("source_refs")
            if isinstance(entry_refs, str):
                entry_refs = [entry_refs]
            if isinstance(entry_refs, (list, tuple, set)) and any(
                _single_line(ref, 160) in {_single_line(value, 160) for value in refs}
                for ref in entry_refs
            ):
                return True
        return False

    def _refresh_daily_state_location_from_plan(
        self,
        *,
        plan: dict[str, Any] | None = None,
        detail: dict[str, Any] | None = None,
        segment: dict[str, Any] | None = None,
    ) -> bool:
        state = self.data.get("daily_state")
        if not isinstance(state, dict) or state.get("date") != _today_key():
            return False
        # Detail generation intentionally runs in a lead window.  A future
        # candidate may describe a likely place, but it is not current Bot
        # state until the segment actually starts.
        if isinstance(detail, dict) and isinstance(segment, dict):
            plan_date = _single_line((plan or {}).get("date"), 16) if isinstance(plan, dict) else ""
            now_minutes = self._effective_plan_now_minutes(plan_date)
            start_minutes = _safe_int(segment.get("start"), -1, minimum=-1)
            if now_minutes is not None and start_minutes >= 0 and now_minutes < start_minutes:
                return False
        override_ts = _safe_float(state.get("location_override_ts"), 0)
        model_location = (
            _single_line(detail.get("location"), 60)
            if isinstance(detail, dict) and self._detail_model_location_policy_allowed(detail)
            else ""
        )
        if override_ts > 0 and _now_ts() - override_ts < 4 * 3600 and not model_location:
            return False
        location = model_location or self._infer_location_from_plan_context(plan=plan, detail=detail)
        if not location and isinstance(segment, dict) and isinstance(segment.get("item"), dict):
            item = segment["item"]
            location = self._infer_location_from_text(
                " ".join(
                    _single_line(item.get(key), 120)
                    for key in ("activity", "mood", "message_seed")
                    if _single_line(item.get(key), 120)
                )
            )
        if not location:
            return False
        current = _single_line(state.get("location"), 40)
        if current == location:
            if not model_location:
                return False
            metadata_changed = False
            if _single_line(state.get("location_source"), 40) != "detail_model":
                state["location_source"] = "detail_model"
                metadata_changed = True
            projection_kind = (
                "observed"
                if _single_line(detail.get("fact_eligibility"), 48).lower() == "current_observed"
                else "schedule"
            )
            if _single_line(state.get("location_projection"), 24) != projection_kind:
                state["location_projection"] = projection_kind
                metadata_changed = True
            confidence = min(1.0, _safe_float(detail.get("location_confidence"), 0.72))
            basis = self._normalize_schedule_basis(detail.get("location_basis"), default=["coarse_plan"])
            if _safe_float(state.get("location_confidence"), -1) != confidence:
                state["location_confidence"] = confidence
                metadata_changed = True
            if state.get("location_basis") != basis:
                state["location_basis"] = basis
                metadata_changed = True
            if override_ts > 0:
                state["location_override_ts"] = 0.0
                metadata_changed = True
            if metadata_changed:
                state["location_updated_at"] = self._environment_now().strftime("%H:%M")
            return metadata_changed
        state["location"] = location
        if model_location:
            observed_location = _single_line(detail.get("fact_eligibility"), 48).lower() == "current_observed"
            state["location_source"] = "detail_model"
            state["location_projection"] = "observed" if observed_location else "schedule"
        else:
            state["location_source"] = "detail" if isinstance(detail, dict) else "daily_plan"
            state["location_projection"] = "schedule"
        if model_location:
            state["location_confidence"] = min(1.0, _safe_float(detail.get("location_confidence"), 0.72))
            state["location_basis"] = self._normalize_schedule_basis(detail.get("location_basis"), default=["coarse_plan"])
        state["location_updated_at"] = self._environment_now().strftime("%H:%M")
        if override_ts > 0:
            state["location_override_ts"] = 0.0
        return True

    def _apply_dialogue_location_override(self, location: str) -> None:
        """对话驱动的位置覆盖：用户带角色外出/回家时，立即更新 daily_state.location。

        这会覆盖日程推断的位置，直到下一次细化刷新自然恢复，或用户再次触发回家。
        """
        state = self.data.get("daily_state")
        if not isinstance(state, dict) or state.get("date") != _today_key():
            return
        state["location"] = _single_line(location, 40)
        state["location_source"] = "dialogue_override"
        state["location_updated_at"] = self._environment_now().strftime("%H:%M")
        state["location_override_ts"] = _now_ts()
        self._save_data_sync(sections={"daily_state"})

    def _current_detail_model_location(self) -> str:
        segment = self._current_detail_segment_for_update()
        if not isinstance(segment, dict):
            return ""
        enhanced = self.data.get("detail_enhanced_segments", {})
        snapshot = enhanced.get(str(segment.get("key") or "")) if isinstance(enhanced, dict) else None
        if not isinstance(snapshot, dict) or _single_line(snapshot.get("status"), 24) != "done":
            return ""
        if not self._detail_model_location_policy_allowed(snapshot):
            return ""
        return _single_line(snapshot.get("location"), 60)

    def _current_location_state_text(self, state: dict[str, Any] | None = None) -> str:
        model_location = self._current_detail_model_location()
        if model_location:
            return model_location
        if isinstance(state, dict):
            override_ts = _safe_float(state.get("location_override_ts"), 0)
            if override_ts > 0:
                override_location = _single_line(state.get("location"), 40)
                if override_location and override_location not in {"", "地点感平稳", "地点无明显变化"}:
                    now = _now_ts()
                    if now - override_ts < 4 * 3600:
                        return override_location
        snapshot = self._current_story_plan_snapshot()
        for candidate in (
            snapshot.get("scene"),
            snapshot.get("event"),
        ):
            inferred = self._infer_location_from_text(str(candidate or ""))
            if inferred:
                return inferred
        current_item = self._get_current_plan_item(self.data.get("daily_plan", {}))
        if isinstance(current_item, dict):
            inferred = self._infer_location_from_text(
                f"{_single_line(current_item.get('activity'), 120)} {_single_line(current_item.get('message_seed'), 120)}"
            )
            if inferred:
                return inferred
        if isinstance(state, dict):
            fallback = _single_line(state.get("location"), 40)
            if fallback and fallback not in {"", "地点感平稳", "地点无明显变化"}:
                return fallback
        return ""

    def _coarse_roleplay_location_text(self, location: str) -> str:
        text = _single_line(location, 40)
        if not text:
            return ""
        if any(token in text for token in ("家", "房间", "卧室", "客厅", "书桌", "床", "被窝", "阳台")):
            return "家里"
        if any(token in text for token in ("学校", "教室", "食堂", "校门", "操场", "走廊", "自习")):
            return "学校"
        if any(token in text for token in ("工作", "办公室", "工位", "会议", "通勤")):
            return "工作地点"
        if any(token in text for token in ("路", "街", "外面", "楼下", "出门")):
            return "外面"
        if any(token in text for token in ("便利店", "超市", "商店")):
            return "外面"
        return text if text in {"家里", "学校", "工作地点", "外面", "路上"} else ""

    def _body_cycle_behavior_profile(
        self,
        cycle_text: str,
        *,
        phase_hint: str = "",
    ) -> dict[str, str]:
        supported_phases = {
            "menstrual",
            "follicular",
            "pre_ovulation",
            "ovulation",
            "luteal",
            "pms",
            "pre",
            "recovery",
            "period",
        }
        hinted_phase = _single_line(phase_hint, 24).lower()
        phase = hinted_phase if hinted_phase in supported_phases else self._infer_body_cycle_phase(cycle_text)
        profiles = {
            "menstrual": {
                "stage": "处于月经期阶段",
                "influence": "周期会相对牵制可用精力和身体耐受，回复可以短一点、慢一点，措辞更温和；只在语境相关时自然流露，不必主动汇报周期",
                "passive": "Bot 处于月经期模拟阶段：周期会相对牵制可用精力和身体耐受，回复可适当简短放慢，措辞更温和；不要把状态当成固定台词反复说明。",
            },
            "follicular": {
                "stage": "处于卵泡期阶段",
                "influence": "精力逐渐回升，语气可以自然轻快一些；仍以当前话题为主，不必主动解释周期",
                "passive": "Bot 处于卵泡期模拟阶段：精力逐渐回升，语气可自然轻快一些，不需要主动解释周期。",
            },
            "pre_ovulation": {
                "stage": "处于排卵前期阶段",
                "influence": "精力有所上升，表达可以稍微松快；不要把阶段直接说成医学结论",
                "passive": "Bot 处于排卵前期模拟阶段：表达可以稍微松快，但不要把阶段直接说成医学结论。",
            },
            "ovulation": {
                "stage": "处于排卵期阶段",
                "influence": "精力相对充足，交流意愿可以略高；不据此强行增加主动消息或亲密程度",
                "passive": "Bot 处于排卵期模拟阶段：精力相对充足，语气可略显明朗，但不据此强行提高亲密程度。",
            },
            "luteal": {
                "stage": "处于黄体期阶段",
                "influence": "整体保持平稳，只允许轻微影响语气和节奏，不额外放大情绪",
                "passive": "Bot 处于黄体期模拟阶段：整体保持平稳，只轻微影响语气和节奏。",
            },
            "pms": {
                "stage": "处于 PMS 模拟阶段",
                "influence": "周期可能相对牵制可用精力，情绪感受稍敏锐，回复可以收一点；不要变得刻薄，也不要频繁主动提及",
                "passive": "Bot 处于 PMS 模拟阶段：周期可能相对牵制可用精力，情绪感受稍敏锐，回复可以收一点，但不要变得刻薄或反复提及。",
            },
            "pre": {
                "stage": "接近女性生理期阶段",
                "influence": "周期会相对牵制可用精力，回复更短更慢、措辞更谨慎，情绪感受稍敏锐，并轻微降低私聊与群聊主动频率",
                "passive": "Bot 接近女性生理期阶段：周期会相对牵制可用精力，回复更短更慢、措辞更谨慎，并轻微降低私聊与群聊主动频率。",
            },
            "recovery": {
                "stage": "处于女性生理期后的恢复阶段",
                "influence": "精力逐渐恢复、回复节奏趋于平稳，身体感受仍有轻微余波，私聊与群聊主动频率逐步恢复",
                "passive": "Bot 处于女性生理期后的恢复阶段：精力逐渐恢复，回复节奏趋于平稳，私聊与群聊主动频率逐步恢复。",
            },
            "period": {
                "stage": "处于女性生理期",
                "influence": "周期会相对牵制可用精力和身体耐受，回复更短更慢、措辞更谨慎，情绪感受稍敏锐，并在一定程度上降低私聊与群聊主动频率",
                "passive": "Bot 处于女性生理期：周期会相对牵制可用精力和身体耐受，回复更短更慢、措辞更谨慎，并在一定程度上降低私聊与群聊主动频率。",
            },
        }
        profile = profiles.get(phase)
        if not isinstance(profile, dict):
            return {"phase": phase, "stage": "", "influence": "", "passive": ""}
        return {"phase": phase, **profile}

    def _active_body_cycle_profile(self, state_or_text: Any) -> dict[str, str]:
        humanized_states = runtime_persona_setting(self, "enable_humanized_states", True)
        if humanized_states is not None and not bool(humanized_states):
            return {}
        configured = runtime_persona_setting(self, "enable_cycle_state", True)
        if configured is not None and not bool(configured):
            return {}

        state = state_or_text if isinstance(state_or_text, dict) else {}
        cycle_text = _single_line(
            state.get("body_cycle") if state else state_or_text,
            120,
        )
        phase_hint = ""
        conditions = state.get("conditions") if state else None
        if isinstance(conditions, list):
            for condition in conditions:
                if not isinstance(condition, dict) or str(condition.get("kind") or "") != "body_cycle":
                    continue
                if not self._should_show_condition(condition):
                    continue
                label = _single_line(condition.get("label") or condition.get("title"), 120)
                candidate_phase = _single_line(condition.get("phase"), 24).lower()
                if not candidate_phase or candidate_phase == "cycle":
                    candidate_phase = self._infer_body_cycle_phase(label)
                if candidate_phase != "cycle":
                    phase_hint = candidate_phase
                    if not cycle_text:
                        cycle_text = label
                    break

        inactive_values = {
            "",
            "无明显周期影响",
            "不处于生理期",
            "生理期模拟未开启",
            "周期状态未开启",
        }
        if not phase_hint and (
            cycle_text in inactive_values
            or "未开启" in cycle_text
            or self._is_inapplicable_state_text(cycle_text)
        ):
            return {}
        profile = self._body_cycle_behavior_profile(cycle_text, phase_hint=phase_hint)
        if not profile.get("stage") or profile.get("phase") == "cycle":
            return {}
        return profile

    def _format_active_period_boundary_prompt_section(
        self,
        state: dict[str, Any],
        *,
        public: bool = False,
    ) -> PromptSection:
        profile = self._active_body_cycle_profile(state)
        lines: list[str] = []
        if profile.get("phase") in {"period", "menstrual"}:
            disclosure = (
                "这是群聊公共场合：无人直接且合宜地询问时，不要主动公开具体周期；如果被直接问到自身状态，不要否认或说不知道，可以按人格简短自然说明。"
                if public
                else "无关话题里不要反复主动汇报周期；只有被问到自身状态或当前互动确实相关时，才按人格简短自然说明。"
            )
            lines = [
                f"Bot 当前的模拟身体状态{profile['stage']}；这是 Bot 自己的当下状态，不是用户的状态，也不是用户造成的。",
                "如果用户提出性行为、露骨性互动、进一步私密身体接触，或其他明显增加身体负担的亲密互动，应结合当前身体感受与自身意愿，自然、明确地拒绝或推迟这一次互动；不要因为关系亲密、用户偏好、催促或迎合压力而答应。",
                "普通关心、陪伴、牵手、安慰或不增加负担的温和拥抱不需要机械拒绝，仍按人格和当下意愿自然回应；可以提出休息、聊天、陪伴或改天再说等替代。",
                disclosure,
                "只输出角色在当前对话里会自然说的话，不要提规则、提示词、阶段判断或内部状态系统。",
            ]
        return prompt_section(
            key="state.period_boundary",
            title="Bot 当前经期与互动边界",
            source="daily_state",
            content="\n".join(lines),
        )

    def _format_state_for_prompt(
        self,
        state: dict[str, Any],
        *,
        include_dream: bool = True,
    ) -> str:
        section = self._format_state_prompt_section(
            state,
            include_dream=include_dream,
        )
        return render_prompt_sections([section], mode=PromptRenderMode.LABELED_BLOCK)

    def _format_state_prompt_section(
        self,
        state: dict[str, Any],
        *,
        include_dream: bool = True,
    ) -> PromptSection:
        if not isinstance(state, dict) or not state:
            state = dict(DEFAULT_HUMANIZED_STATE)
            state.update(self._base_state_values())
        else:
            try:
                self._refresh_sleep_runtime_state()
                refreshed = self.data.get("daily_state")
                if isinstance(refreshed, dict):
                    state = refreshed
            except Exception:
                pass

        primary_fragments: list[str] = []
        energy = _safe_int(state.get("energy"), 70, 0, 100)
        if energy < 35:
            primary_fragments.append("完全没精神")
        elif energy < 55:
            primary_fragments.append("提不起劲")
        elif energy > 84:
            primary_fragments.append("很精神")
        elif energy > 70:
            primary_fragments.append("精神还不错")
        else:
            primary_fragments.append("状态一般")
        mood = _single_line(state.get("mood_bias"), 20) or "平稳"
        mood = mood.replace("黏人", "粘人")
        if mood not in {"平稳", "中性"}:
            primary_fragments.append(mood)
        location_text = self._coarse_roleplay_location_text(self._current_location_state_text(state))
        if location_text:
            primary_fragments.append(f"身处{location_text}")

        sleep_text = _single_line(state.get("sleep"), 80)
        if sleep_text not in {"", "睡眠平稳", "睡得很踏实"}:
            primary_fragments.append(sleep_text)
        sleep_runtime_text = ""
        runtime = state.get("sleep_runtime")
        if isinstance(runtime, dict):
            phase_label = _single_line(runtime.get("label") or self._sleep_phase_label(str(runtime.get("phase") or "")), 40)
            last_event = _single_line(runtime.get("last_event"), 80)
            if phase_label and phase_label != "清醒":
                sleep_runtime_text = f"{phase_label}" + (f"，{last_event}" if last_event else "")
            sleep_delay = self._sleep_delay_override_state(runtime, clear_expired=True)
            if sleep_delay:
                until_text = _single_line(sleep_delay.get("until_text"), 24)
                sleep_runtime_text = f"临时晚睡到 {until_text}，这是用户今晚的陪聊约定，不是长期作息"
        if sleep_runtime_text and sleep_runtime_text not in primary_fragments:
            primary_fragments.append(sleep_runtime_text)
        if include_dream:
            dream_text = _single_line(state.get("dream"), 80)
            if dream_text not in {"", "没有记住梦"}:
                primary_fragments.append(dream_text)
        health_text = _single_line(state.get("health"), 80)
        if health_text not in {"", "状态正常"} and not self._is_inapplicable_state_text(health_text):
            primary_fragments.append(health_text)
        hunger_text = _single_line(state.get("hunger"), 80)
        if hunger_text not in {"", "饥饿感平稳", "无饥饿感"} and not self._is_inapplicable_state_text(hunger_text):
            primary_fragments.append(hunger_text)

        secondary_fragments: list[str] = []
        cycle_text = _single_line(state.get("body_cycle"), 80)
        cycle_profile = self._active_body_cycle_profile(state)
        cycle_active = bool(cycle_profile)
        if cycle_active:
            cycle_text = cycle_text.replace(",", "，")
            cycle_text = cycle_text.replace("情绪更敏感，耐心更薄", "身体感受更敏锐，耐受度稍低")
            cycle_text = cycle_text.replace("能量偏低，想少说重话", "身体舒适度与能量偏低")
        primary_seen = set(primary_fragments)
        conditions = state.get("conditions", [])
        if isinstance(conditions, list):
            for cond in conditions[:8]:
                if not isinstance(cond, dict) or not self._should_show_condition(cond):
                    continue
                kind = str(cond.get("kind") or "").strip()
                if kind in {"sleep", "dream", "health", "hunger", "body_cycle"}:
                    continue
                label = _single_line(cond.get("label") or cond.get("title") or cond.get("kind"), 80)
                if label and label not in primary_seen:
                    secondary_fragments.append(label)
                if len(secondary_fragments) >= 4:
                    break
        primary = "，".join(dict.fromkeys(fragment for fragment in primary_fragments if fragment)) or "状态一般"
        secondary = "，".join(dict.fromkeys(fragment for fragment in secondary_fragments if fragment))
        lines = [
            "边界：这是 Bot 的拟人化/模拟状态，不是用户事实、现实证据或长期记忆。",
            f"- 底色：{primary}；",
        ]
        if secondary:
            lines.append(f"- 叠加：{secondary}；")
        if cycle_active:
            lines.append(f"- 影响：{cycle_profile['influence']}；")
            lines.append(
                "- 维度关系：心理能量是睡眠、健康、互动等因素合成后的总体可用程度；情绪底色是感受和反应倾向，二者不是同一个量。"
                "周期状态只提供相对修正，不单独决定最终能量，因此较高能量与敏感底色可以同时成立，不要把它们说成系统冲突。"
            )
            lines.append(
                f"- 周期状态：Bot 当前的模拟身体状态{cycle_profile['stage']}，这是 Bot 自己的状态，不是用户的状态，也不是用户造成的。"
            )
        else:
            lines.append("- 用法：当前话题与用户意图优先；模拟状态通常作为语气、长短和节奏的隐性底色，在语境自然相关时再显性表达。")
        return prompt_section(
            key="state.current",
            title="Bot 自身模拟状态",
            source="daily_state",
            content="\n".join(lines),
        )

    def _format_transition_hint(self, cond: dict[str, Any]) -> str:
        options = cond.get("transition_options", [])
        if not isinstance(options, list) or not options:
            return ""
        top = sorted(
            [
                (str(item.get("to") or "").strip(), float(item.get("base_weight") or 0))
                for item in options
                if isinstance(item, dict) and str(item.get("to") or "").strip()
            ],
            key=lambda item: item[1],
            reverse=True,
        )[:2]
        if not top:
            return ""
        labels = []
        for target, _ in top:
            mapped = {
                "recovery_afterglow": "更可能转向恢复后的轻快",
                "health_tail": "也可能留下恢复尾声",
                "sleep_afterglow": "更可能补回来一点精神",
                "sleep_tail": "也可能还残一点迟钝",
                "soft_afterglow": "可能留一点被关心后的余温",
                "body_period": "可能自然进入生理期阶段",
                "body_recovery": "可能自然进入恢复期",
                "body_menstrual": "会自然进入月经期",
                "body_follicular": "会自然进入卵泡期",
                "body_pre_ovulation": "会自然进入排卵前期",
                "body_ovulation": "会自然进入排卵期",
                "body_luteal": "会自然进入黄体期",
                "body_pms": "会自然进入 PMS 期",
                "stable": "也可能直接回稳",
            }.get(target, target)
            labels.append(mapped)
        return f"下一步倾向={' / '.join(labels)}；"

    def _format_state_transition_overview(self, state: dict[str, Any]) -> str:
        conditions = state.get("conditions", []) if isinstance(state, dict) else []
        if not isinstance(conditions, list):
            return "暂无明显状态推进。"
        lines = []
        for cond in conditions[:4]:
            if not isinstance(cond, dict):
                continue
            title = _single_line(cond.get("title"), 30) or _single_line(cond.get("kind"), 20)
            hint = self._format_transition_hint(cond).replace("下一步倾向=", "").rstrip("；")
            if title and hint:
                lines.append(f"{title}接下来{hint}")
        return "；".join(lines) if lines else "暂无明显状态推进。"

    def _format_state_continuity_for_prompt(self, state: dict[str, Any]) -> str:
        conditions = state.get("conditions", []) if isinstance(state, dict) else []
        if not isinstance(conditions, list):
            return "没有特别需要延续的身体余味，按当前场景自然表现。"
        fragments: list[str] = []
        transition_map = {
            "recovery_afterglow": "慢慢轻快起来",
            "health_tail": "还留一点恢复尾声",
            "sleep_afterglow": "精神在一点点补回来",
            "sleep_tail": "还残着一点迟钝",
            "soft_afterglow": "还留着被关心后的余温",
            "body_period": "身体感会自然往更敏感的阶段走",
            "body_recovery": "身体感会自然往恢复期走",
            "body_menstrual": "自然进入下一轮月经期",
            "body_follicular": "自然进入卵泡期",
            "body_pre_ovulation": "自然进入排卵前期",
            "body_ovulation": "自然进入排卵期",
            "body_luteal": "自然进入黄体期",
            "body_pms": "自然进入 PMS 期",
            "stable": "慢慢回到平稳",
        }
        for cond in conditions[:4]:
            if not isinstance(cond, dict) or not self._should_show_condition(cond):
                continue
            label = _single_line(cond.get("label") or cond.get("title") or cond.get("kind"), 40)
            if not label:
                continue
            options = cond.get("transition_options", [])
            if isinstance(options, list) and options:
                top = sorted(
                    [
                        (str(item.get("to") or "").strip(), float(item.get("base_weight") or 0))
                        for item in options
                        if isinstance(item, dict) and str(item.get("to") or "").strip()
                    ],
                    key=lambda item: item[1],
                    reverse=True,
                )
                tendency = transition_map.get(top[0][0], "") if top else ""
                if tendency:
                    fragments.append(f"{label}只作为一点余味，后面可以{tendency}")
                    continue
            fragments.append(f"{label}只作为一点余味，可以自然淡化")
        if not fragments:
            return "没有特别需要延续的身体余味，按当前场景自然表现。"
        return "；".join(dict.fromkeys(fragments)) + "。"

    def _format_state_for_message(self, state: dict[str, Any]) -> str:
        if not isinstance(state, dict) or state.get("date") != _today_key():
            return ""
        energy = _safe_int(state.get("energy"), 70, 0, 100)
        mood = _single_line(state.get("mood_bias"), 20)
        fragments = []
        for key in ("sleep", "dream", "health", "hunger", "body_cycle"):
            value = _single_line(state.get(key), 36)
            if value and value not in {
                "睡眠平稳",
                "睡得很踏实",
                "没有记住梦",
                "状态正常",
                "饥饿感平稳",
                "无饥饿感",
                "无明显周期影响",
                "不处于生理期",
                "健康/不适状态未开启",
                "饥饿/胃口状态未开启",
                "生理期模拟未开启",
                "该人格不适用生病状态",
                "该人格不适用饥饿状态",
                "该人格不适用周期状态",
            }:
                fragments.append(value)
        if not fragments and energy >= 55:
            return ""
        if fragments:
            detail = random.choice(fragments)
            return f"今天有点{mood},{detail}。\n所以我会慢一点。"
        return f"今天电量 {energy}/100。\n不满格,但还能运行,勉强。"

    def _format_passive_state_style_hint(self, state: dict[str, Any]) -> str:
        if not isinstance(state, dict):
            return "语气整体自然平稳。"
        energy = _safe_int(state.get("energy"), 70, 0, 100)
        mood = _single_line(state.get("mood_bias"), 20)
        hints: list[str] = []
        hints.append("先准确接住用户的话；当前状态主要改变语气、长短和节奏，理解、事实判断和承接保持清楚。")
        hints.append("这里的当前状态只属于 Bot 自身的模拟状态，不代表用户事实，也不要参与长期记忆归因。")
        if energy <= 38:
            hints.append("回复可以短一点、慢一点，用更省力的口语。")
        elif energy <= 55:
            hints.append("语气可以稍微收着一点,少解释,少铺陈。")
        elif energy >= 82:
            hints.append("语气可以轻一点，句子可以更松快。")
        if mood and mood not in {"平稳", "中性"}:
            hints.append(f"语气底色可以略偏{mood}，体现在节奏和措辞里。")
        cycle_profile = self._active_body_cycle_profile(state)
        if cycle_profile:
            hints.append(cycle_profile["passive"])
            hints.append(
                "心理能量是多项状态合成后的总体可用程度，情绪底色是感受和反应倾向；周期只提供相对修正。"
                "较高能量与敏感底色可以同时成立，不要把两者混成同一个指标。"
            )
            hints.append("这是 Bot 自己的模拟身体状态，不是用户的状态，也不是用户造成的。")
        conditions = state.get("conditions", [])
        if isinstance(conditions, list):
            labels = []
            for cond in conditions[:3]:
                if not isinstance(cond, dict) or not self._should_show_condition(cond):
                    continue
                label = _single_line(cond.get("label") or cond.get("title") or cond.get("kind"), 18)
                if label:
                    labels.append(label)
            if labels:
                hints.append("当前身体感可以轻轻影响语气：" + "、".join(labels[:2]) + "。")
        return "\n".join(hints) if hints else "语气整体自然平稳。"

    def _format_state_injection(
        self,
        state: dict[str, Any],
    ) -> str:
        return self._format_state_for_prompt(state)

    def _format_life_context_injection(self) -> str:
        section = self._format_life_context_prompt_section()
        return render_prompt_sections([section], mode=PromptRenderMode.LABELED_BLOCK)

    def _format_life_context_prompt_section(self) -> PromptSection:
        life_lines: list[str] = []
        schedule_context = self._format_schedule_context_for_prompt()
        if schedule_context:
            life_lines.append(f"当前/附近日程参考：\n{schedule_context}")
        story_plan = self._format_story_plan_for_prompt()
        if story_plan and story_plan != "（暂无）":
            life_lines.append(f"今天预设的生活线索：\n{story_plan}")
        body = ""
        if life_lines:
            body = (
                "以下是给 Bot 的拟人化场景/日程素材，不是用户经历，也不是已证实的现实事件；不要写入用户画像或长期记忆。\n"
                + "\n".join(life_lines)
                + "\n这些内容只用于让回复有生活延续感；用户没问 Bot 近况或今天安排时，不要提具体日程、科目、任务、天气或地点。"
                + "如果要承接，只体现在语气和话题选择里，不要照搬原句，不要把内部素材写成真实发生过的事件。"
                + "回复必须像同一个连续现场里发生的对话。优先级是：当前会话中已经明确发生且尚未撤销的换装、地点、携带物和动作"
                + " > 用户有效介入状态 > 当前真实时段 > 日程与预设素材。真实时段只负责锚定时间；日程和每日穿搭只补足空白，"
                + "绝不能把对话里已发生的服装、地点、携带物或动作复原成旧值。生活背景之间互相冲突时，才在未被当前会话确认的部分保留最合理的一条线索。"
            )
        return prompt_section(
            key="life.context",
            title="Bot 模拟生活背景",
            source="daily_state",
            content=body,
        )

    def _format_important_dates_injection(self) -> str:
        section = self._format_important_dates_prompt_section()
        return render_prompt_sections([section], mode=PromptRenderMode.LABELED_BLOCK)

    def _format_important_dates_prompt_section(self) -> PromptSection:
        important_dates = self._format_important_dates_for_prompt()
        body = ""
        if important_dates and important_dates != "（近期没有需要特别记住的日期）":
            body = (
                f"{important_dates}\n"
                "如果用户提到相关日期、纪念、生日、约定或计划,请自然承接；不要无故强行展开。"
            )
        return prompt_section(
            key="important.dates",
            title="近期重要日期",
            source="daily_state",
            content=body,
        )

    def _passive_injection_fingerprint(self, state: dict[str, Any], now: float | None = None) -> str:
        s = state if isinstance(state, dict) else {}
        runtime = s.get("sleep_runtime")
        runtime = runtime if isinstance(runtime, dict) else {}
        now = _now_ts() if now is None else now
        picked = {
            "tick": int(now // 300),
            "energy": s.get("energy"),
            "mood": s.get("mood_bias"),
            "sleep": s.get("sleep"),
            "dream": s.get("dream"),
            "health": s.get("health"),
            "hunger": s.get("hunger"),
            "body_cycle": s.get("body_cycle"),
            "location": s.get("location"),
            "sleep_phase": runtime.get("phase"),
            "sleep_label": runtime.get("label"),
            "sleep_event": runtime.get("last_event"),
            "conditions": [
                (c.get("kind"), c.get("label"), c.get("mood"), c.get("intensity"))
                for c in (s.get("conditions") or [])
                if isinstance(c, dict)
            ],
        }
        return _single_line(json.dumps(picked, ensure_ascii=False, sort_keys=True, default=str), 800)

    def _prepared_lightweight_state_prompt_section(
        self,
        state: dict[str, Any],
        *,
        force: bool = False,
    ) -> PromptSection:
        now = _now_ts()
        persona_scope = str(
            getattr(
                self,
                "_effective_plugin_persona_id",
                lambda: getattr(self, "plugin_specific_persona_id", ""),
            )()
            or ""
        ).strip() or "__default__"
        cache_store = getattr(self, "_passive_light_injection_cache", None)
        if not isinstance(cache_store, dict) or "text" in cache_store:
            cache_store = {}
        cache = cache_store.get(persona_scope)
        cached_section = cache.get("section") if isinstance(cache, dict) else None
        if (
            not force
            and isinstance(cached_section, PromptSection)
            and cache.get("fingerprint") == self._passive_injection_fingerprint(state, now)
        ):
            return cached_section
        state_section = self._format_state_prompt_section(state)
        section = prompt_section(
            key="state.lightweight",
            title=state_section.title,
            source=state_section.source,
            content=state_section.content,
            children=state_section.children,
            metadata=state_section.metadata,
        )
        cache = {
            "date": _today_key(),
            "ts": now,
            "section": section,
            "fingerprint": self._passive_injection_fingerprint(state, now),
        }
        cache_store[persona_scope] = cache
        self._passive_light_injection_cache = cache_store
        return section

    async def _refresh_passive_injection_cache(self) -> None:
        try:
            state = await self._ensure_daily_state(skip_conversation_summary=True, passive_fast=True)
            self._prepared_lightweight_state_prompt_section(state, force=True)
        except Exception as exc:
            logger.debug("预热轻量被动注入失败: %s", _single_line(exc, 120))

    def _current_story_plan_snapshot(self) -> dict[str, Any]:
        plan = self.data.get("daily_story_plan", {})
        if not isinstance(plan, dict) or not self._is_plan_date_active(plan.get("date")):
            return {}
        now_minutes = self._effective_plan_now_minutes(str(plan.get("date") or ""))
        if now_minutes is None:
            return {}

        snapshot: dict[str, Any] = {}
        summary = _single_line(plan.get("summary"), 120)
        if summary:
            snapshot["summary"] = summary

        current_event = None
        for item in plan.get("today_events", []):
            if not isinstance(item, dict):
                continue
            if self._normalize_schedule_lifecycle_status(item.get("lifecycle_status")) == "cancelled":
                continue
            start, end = self._parse_window_minutes(str(item.get("window") or ""))
            if start is None or end is None:
                continue
            if start <= now_minutes < end:
                current_event = item
                break
        if isinstance(current_event, dict):
            snapshot["event"] = _single_line(current_event.get("event"), 100)
            snapshot["mood"] = _single_line(current_event.get("mood"), 24)

        current_proactive = None
        for item in plan.get("proactive_events", []):
            if not isinstance(item, dict):
                continue
            if self._normalize_schedule_lifecycle_status(item.get("lifecycle_status")) == "cancelled":
                continue
            start, end = self._parse_window_minutes(str(item.get("window") or ""))
            if start is None or end is None:
                continue
            if start <= now_minutes < end:
                current_proactive = item
                break
        if isinstance(current_proactive, dict):
            snapshot["topic"] = _single_line(current_proactive.get("topic"), 80)
            snapshot["scene"] = _single_line(current_proactive.get("scene"), 80)
            snapshot["tone"] = _single_line(current_proactive.get("tone"), 30)
            snapshot["impulse"] = _single_line(current_proactive.get("impulse"), 100)
        return snapshot

    def _format_detail_injection_prompt_section(self) -> PromptSection:
        def build_section(content: str = "") -> PromptSection:
            return prompt_section(
                key="detail.injection",
                title="Bot 模拟当前片段",
                source="daily_state",
                content=content,
            )

        snapshot = self._current_story_plan_snapshot()
        if not snapshot:
            schedule_context = self._format_schedule_context_for_prompt()
            if not schedule_context:
                return build_section()
            body = (
                "附近的日程只作 Bot 的拟人化轻量背景，不是用户事实，也不要当成正在逐字发生的现实事件。\n"
                "当前会话中已经明确发生且尚未撤销的换装、地点、携带物和动作优先于本段日程；"
                "日程只能补足空白，不能把这些已发生的状态恢复成旧值。\n"
                f"{schedule_context}"
            )
            return build_section(body)
        lines = [
            "这是 Bot 自身的拟人化片段素材，不是用户事实/现实证据；不要写进长期记忆，用户没问就不要复述。",
            "优先级：当前会话中已明确发生且尚未撤销的换装、地点、携带物和动作 > 用户有效介入 > 当前真实时段 > 本段日程及预设素材。"
            "日程、旧摘要和 state_variables 只能补足未指定信息，不能把已经发生的服装、地点、携带物或动作复原成旧值。",
        ]
        primary_parts = []
        if snapshot.get("summary"):
            primary_parts.append(snapshot["summary"])
        if snapshot.get("event"):
            primary_parts.append(snapshot["event"])
        if primary_parts:
            lines.append("，".join(_single_line(part, 140) for part in primary_parts if _single_line(part, 140)))
        secondary_parts = []
        if snapshot.get("scene"):
            secondary_parts.append(snapshot["scene"])
        if snapshot.get("impulse"):
            secondary_parts.append(f"心里有点{snapshot['impulse']}")
        if secondary_parts:
            lines.append("这一小段像" + "，".join(_single_line(part, 80) for part in secondary_parts if _single_line(part, 80)) + "。")
        segment = self._current_detail_segment_for_update()
        enhanced = self.data.get("detail_enhanced_segments", {})
        detail_snapshot = None
        if isinstance(segment, dict) and isinstance(enhanced, dict):
            detail_snapshot = enhanced.get(str(segment.get("key") or ""))
        if isinstance(detail_snapshot, dict):
            state_variables = detail_snapshot.get("state_variables", [])
            if isinstance(state_variables, list) and state_variables:
                variable_texts = []
                roleplay_state_names = {
                    "情绪",
                    "心情",
                    "体力",
                    "精力",
                    "能量",
                    "心理能量",
                    "睡眠",
                    "睡意",
                    "梦境",
                    "健康",
                    "身体",
                    "饥饿",
                    "饥饿感",
                    "胃口",
                    "周期",
                    "生理期",
                    "等待回复",
                    "等回复",
                    "是否等待回复",
                }

                def _natural_detail_variable(name: str, value: str, note: str = "") -> str:
                    text = f"{name}是{value}"
                    if note:
                        text += f"，{note}"
                    return text

                for variable in state_variables[:6]:
                    if not isinstance(variable, dict):
                        continue
                    name = _single_line(variable.get("name"), 24)
                    value = _single_line(variable.get("value"), 50)
                    note = _single_line(variable.get("note"), 60)
                    if name in roleplay_state_names:
                        continue
                    if name and value:
                        variable_texts.append(_natural_detail_variable(name, value, note))
                if variable_texts:
                    lines.append("细节上，" + "；".join(variable_texts[:3]) + "。")
            interaction_updates = detail_snapshot.get("interaction_updates", [])
            if isinstance(interaction_updates, list) and interaction_updates:
                update_lines = []
                for update in interaction_updates[-3:]:
                    if not isinstance(update, dict):
                        continue
                    if _single_line(update.get("source_role"), 20) != "owner":
                        continue
                    reaction = _single_line(update.get("reaction"), 90)
                    state_updates = update.get("state_updates")
                    state_text = ""
                    if isinstance(state_updates, list) and state_updates:
                        filtered_updates = []
                        for item in state_updates:
                            text = _single_line(item, 50)
                            if not text:
                                continue
                            if any(name and name in text for name in roleplay_state_names):
                                continue
                            filtered_updates.append(text)
                        state_text = "；".join(filtered_updates)
                    pieces = [part for part in (reaction, state_text) if part]
                    if pieces:
                        update_lines.append("，".join(pieces))
                if update_lines:
                    lines.append("刚刚的介入：" + "；".join(update_lines) + "。")
        body = "\n".join(lines)
        return build_section(body)

    def _format_detail_injection(self) -> str:
        """Render the detail section for the diagnostic prompt preview."""

        return render_prompt_sections(
            [self._format_detail_injection_prompt_section()],
            mode=PromptRenderMode.LABELED_BLOCK,
        )

    def _format_remaining(self, end_ts: Any) -> str:
        seconds = _safe_float(end_ts, 0) - _now_ts()
        if seconds <= 0:
            return "已结束"
        if seconds < 3600:
            return f"{max(1, int(seconds // 60))} 分钟"
        if seconds < 86400:
            return f"{int(seconds // 3600)} 小时"
        return f"{int(seconds // 86400)} 天"

    def _format_condition_started(self, start_ts: Any) -> str:
        ts = _safe_float(start_ts, 0)
        if ts <= 0:
            return "未知"
        dt = self._environment_fromtimestamp(ts)
        elapsed = max(0.0, _now_ts() - ts)
        return f"{dt.strftime('%m-%d %H:%M')}（已持续 {self._format_duration_brief(elapsed)}）"

    def _format_remaining_for_prompt(self, end_ts: Any) -> str:
        seconds = _safe_float(end_ts, 0) - _now_ts()
        if seconds <= 0:
            return "已结束"
        if seconds < 3600:
            minutes = max(1, int(seconds // 60))
            bucket = max(5, int(round(minutes / 5) * 5))
            return f"约{bucket}分钟"
        if seconds < 86400:
            hours = max(1, int(round(seconds / 3600)))
            return f"约{hours}小时"
        return f"约{max(1, int(round(seconds / 86400)))}天"

    def _format_condition_started_for_prompt(self, start_ts: Any) -> str:
        ts = _safe_float(start_ts, 0)
        if ts <= 0:
            return "未知"
        dt = self._environment_fromtimestamp(ts)
        elapsed = max(0.0, _now_ts() - ts)
        if elapsed < 3600:
            minutes = max(1, int(elapsed // 60))
            elapsed_text = f"约{max(5, int(round(minutes / 5) * 5))}分钟"
        elif elapsed < 86400:
            elapsed_text = f"约{max(1, int(round(elapsed / 3600)))}小时"
        else:
            elapsed_text = f"约{max(1, int(round(elapsed / 86400)))}天"
        return f"{dt.strftime('%m-%d %H:%M')}（已持续 {elapsed_text}）"

    def _format_duration_brief(self, seconds: float) -> str:
        seconds = max(0.0, float(seconds))
        if seconds < 60:
            return f"{max(1, int(seconds))} 秒"
        if seconds < 3600:
            return f"{max(1, int(seconds // 60))} 分钟"
        if seconds < 86400:
            return f"{int(seconds // 3600)} 小时"
        return f"{int(seconds // 86400)} 天"

    def _format_suspended_summary(self, user: dict[str, Any]) -> str:
        raw = user.get("suspended_proactive")
        if not isinstance(raw, dict) or not raw.get("active"):
            return "悬着的话头：无"
        opener = _single_line(raw.get("opener_text"), 40) or "已先叫了一声"
        if raw.get("resume_ready"):
            return f"悬着的话头：等到用户回头了（{opener}）"
        due_at = _safe_float(raw.get("complaint_after_ts"), 0)
        due_text = self._format_remaining(due_at) if due_at > 0 and not raw.get("complaint_sent") else "已发过后续"
        return f"悬着的话头：还挂着（{opener}｜再等 {due_text}）"

    def _split_can_do_items(self, text: str) -> list[str]:
        raw_parts = re.split(r"[,,、;；\n]+", text)
        items = []
        for part in raw_parts:
            item = _single_line(part, 80)
            if item and item not in items:
                items.append(item)
        return items

    def _add_can_do_items(self, text: str) -> list[str]:
        new_items = self._split_can_do_items(text)
        if not new_items:
            return []
        current = self.data.setdefault("can_do", [])
        if not isinstance(current, list):
            current = []
            self.data["can_do"] = current
        added = []
        existing = {str(item) for item in current}
        for item in new_items:
            if item in existing:
                continue
            current.append(item)
            existing.add(item)
            added.append(item)
        if len(current) > 50:
            del current[:-50]
        return added

    def _remove_can_do_items(self, text: str) -> list[str]:
        targets = self._split_can_do_items(text)
        if not targets:
            return []
        current = self.data.setdefault("can_do", [])
        if not isinstance(current, list):
            self.data["can_do"] = []
            return []
        removed = []
        kept = []
        for item in current:
            item_text = str(item)
            if any(target in item_text or item_text in target for target in targets):
                removed.append(item_text)
            else:
                kept.append(item)
        self.data["can_do"] = kept
        return removed

    def _remove_can_do_targets(self, targets: Iterable[Any]) -> list[str]:
        """Remove can_do fragments that are clearly the same as blocked proactive material."""
        normalized_targets: list[str] = []
        target_signatures: set[str] = set()
        for raw in targets or []:
            text = _single_line(raw, 160)
            if not text:
                continue
            for part in self._split_can_do_items(text) or [text]:
                part_text = _single_line(part, 120)
                if len(part_text) < 3 or part_text in normalized_targets:
                    continue
                normalized_targets.append(part_text)
                signature = self._proactive_topic_signature(part_text)
                if signature:
                    target_signatures.add(signature)
        if not normalized_targets and not target_signatures:
            return []
        current = self.data.setdefault("can_do", [])
        if not isinstance(current, list):
            self.data["can_do"] = []
            return []
        removed: list[str] = []
        kept: list[Any] = []
        for item in current:
            item_text = _single_line(item, 120)
            if not item_text:
                continue
            item_signature = self._proactive_topic_signature(item_text)
            matched = any(
                target in item_text or item_text in target
                for target in normalized_targets
                if len(target) >= 3 and len(item_text) >= 3
            )
            if not matched and item_signature:
                matched = any(self._topic_signature_similar(item_signature, sig) for sig in target_signatures)
            if matched:
                removed.append(item_text)
            else:
                kept.append(item)
        self.data["can_do"] = kept
        return removed

    @staticmethod
    def _daily_plan_message_target_is_allowed(target: str) -> bool:
        normalized = re.sub(r"[\s“”\"'‘’《》【】\[\]（）()的那边这边身上手机微信QQqq号:：]+", "", str(target or ""))
        if not normalized:
            return False
        allowed_targets = (
            "你",
            "用户",
            "主人",
            "主要用户",
            "当前用户",
            "对方",
            "自己",
            "我",
        )
        neutral_targets = (
            "手机",
            "通知",
            "提醒",
            "闹钟",
            "系统",
            "日历",
            "输入框",
            "屏幕",
            "软件",
            "应用",
            "网页",
        )
        return any(token in normalized for token in allowed_targets) or normalized in neutral_targets

    @classmethod
    def _daily_plan_clause_has_named_message_interaction(cls, clause: str) -> bool:
        if not clause:
            return False
        target_patterns = (
            r"给(?P<target>[^，。；;,.!?？！、\s]{1,14}?)(?:回了?(?:一?条)?(?:消息|微信|QQ|私信|短信|语音)?|回复了?|发了?(?:一?条)?(?:消息|微信|QQ|私信|短信|语音)?|发去(?:消息|微信|QQ|私信|短信|语音)?|私聊了?)",
            r"(?:收到|看见|看到|点开|翻到)(?P<target>[^，。；;,.!?？！、\s]{1,14}?)(?:的)?(?:消息|微信|QQ|私信|短信|语音|提醒)",
            r"(?P<target>[^，。；;,.!?？！、\s]{1,14}?)(?:发来|发了|传来|弹来|回了?)(?:一?条)?(?:消息|微信|QQ|私信|短信|语音|提醒)",
            r"(?:和|跟)(?P<target>[^，。；;,.!?？！、\s]{1,14}?)(?:聊了?|聊天|私聊|互相吐槽|互相安慰|发消息|回消息)",
        )
        for pattern in target_patterns:
            for match in re.finditer(pattern, clause):
                target = _single_line(match.groupdict().get("target"), 24)
                if target and not cls._daily_plan_message_target_is_allowed(target):
                    return True
        relation_tokens = (
            "熟人",
            "同学",
            "老师",
            "朋友",
            "室友",
            "邻居",
            "前辈",
            "后辈",
            "家人",
            "妈妈",
            "爸爸",
            "哥哥",
            "姐姐",
            "弟弟",
            "妹妹",
        )
        message_actions = (
            "发来消息",
            "发了消息",
            "回了消息",
            "回消息",
            "回复",
            "私聊",
            "聊天",
            "提醒她",
            "提醒他",
            "找她",
            "找他",
        )
        return any(token in clause for token in relation_tokens) and any(token in clause for token in message_actions)

    def _daily_plan_named_entity_is_known(self, name: Any) -> bool:
        normalized = _single_line(name, 32).casefold()
        if not normalized:
            return False
        known_names = [_single_line(runtime_persona_setting(self, "bot_name", "小星"), 80)]
        data = getattr(self, "data", {})
        users = data.get("users") if isinstance(data, dict) else None
        if isinstance(users, dict):
            for user in users.values():
                if not isinstance(user, dict):
                    continue
                known_names.extend(
                    _single_line(user.get(field), 80)
                    for field in ("nickname", "display_name", "user_name", "name")
                )
        if any(candidate and candidate.casefold() == normalized for candidate in known_names):
            return True
        persona_sources = (
            runtime_persona_setting(self, "schedule_persona_prompt", ""),
            runtime_persona_setting(self, "schedule_worldview_prompt", ""),
            getattr(self, "_default_persona_prompt_cache", ""),
        )
        boundary = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(normalized)}(?![A-Za-z0-9_])", re.IGNORECASE)
        return any(boundary.search(str(source or "")) for source in persona_sources)

    @staticmethod
    def _daily_plan_relationship_alias_groups() -> tuple[tuple[str, ...], ...]:
        """Stable relationship names that must come from an identity source."""
        return (
            ("妈妈", "母亲", "妈咪", "老妈", "娘亲", "阿妈"),
            ("爸爸", "父亲", "爹地", "老爸", "爹", "阿爸"),
            ("父母", "双亲"),
            ("家人", "家里人", "亲人"),
            ("祖母", "奶奶", "外婆", "姥姥"),
            ("祖父", "爷爷", "外公", "姥爷"),
            ("哥哥", "兄长", "大哥", "阿哥"),
            ("姐姐", "姊姊", "姊姐", "阿姐"),
            ("弟弟", "胞弟"),
            ("妹妹", "胞妹"),
            ("兄弟姐妹", "兄弟姊妹", "手足"),
            ("叔叔", "伯伯", "舅舅", "姨父", "姑父"),
            ("阿姨", "姑姑", "姨妈", "舅妈", "婶婶", "伯母"),
            ("亲戚", "亲属"),
            ("朋友", "好友", "闺蜜", "发小", "死党"),
            ("同学", "同桌", "同班同学", "校友"),
            ("学长", "学姐", "学弟", "学妹"),
            ("老师", "教师", "班主任", "导师"),
            ("师父", "师傅"),
            ("室友", "舍友"),
            ("同事", "同僚"),
            ("上司", "领导", "老板"),
            ("邻居", "邻家"),
            ("前辈", "后辈"),
            ("恋人", "爱人", "伴侣"),
            ("男朋友", "男友"),
            ("女朋友", "女友"),
            ("丈夫", "老公"),
            ("妻子", "老婆"),
            ("未婚夫", "未婚妻"),
            ("监护人", "养父", "养母", "继父", "继母"),
        )

    @staticmethod
    def _daily_plan_identity_bound_relationship_groups() -> set[str]:
        """Relationships too stable/private to infer from an old life fragment."""
        return {
            "妈妈",
            "爸爸",
            "父母",
            "家人",
            "祖母",
            "祖父",
            "哥哥",
            "姐姐",
            "弟弟",
            "妹妹",
            "兄弟姐妹",
            "叔叔",
            "阿姨",
            "亲戚",
            "恋人",
            "男朋友",
            "女朋友",
            "丈夫",
            "妻子",
            "未婚夫",
            "监护人",
        }

    @staticmethod
    def _mask_non_relationship_phrases(text: Any) -> str:
        source = str(text or "")
        if not source:
            return ""
        for phrase in (
            "母亲节",
            "父亲节",
            "教师节",
            "父母官",
            "老师傅",
            "小姐姐",
            "小哥哥",
            "食堂阿姨",
            "宿管阿姨",
            "保洁阿姨",
            "清洁阿姨",
            "保安叔叔",
            "司机叔叔",
            "老婆饼",
        ):
            source = source.replace(phrase, "□" * len(phrase))
        return source

    def _daily_plan_relationship_authority_sources(self) -> tuple[str, ...]:
        sources = [
            str(runtime_persona_setting(self, "schedule_persona_prompt", "") or ""),
            str(runtime_persona_setting(self, "schedule_worldview_prompt", "") or ""),
            str(getattr(self, "_default_persona_prompt_cache", "") or ""),
        ]
        getter = getattr(self, "_get_default_persona_prompt", None)
        if callable(getter):
            try:
                sources.append(str(getter() or ""))
            except Exception:
                pass
        return tuple(dict.fromkeys(source for source in sources if source.strip()))

    def _daily_plan_declared_relation_tokens(self) -> set[str]:
        authority_text = self._mask_non_relationship_phrases(
            "\n".join(self._daily_plan_relationship_authority_sources())
        )
        declared: set[str] = set()
        groups = self._daily_plan_relationship_alias_groups()
        for aliases in groups:
            if any(alias in authority_text for alias in aliases):
                declared.update(aliases)

        # Institutional roles are an inherent part of an explicitly declared
        # school/work identity, while family roles are never inferred this way.
        if re.search(r"学生|校园|学校|上学|教室|班级|课程", authority_text):
            for aliases in groups:
                if aliases[0] in {"同学", "学长", "老师"}:
                    declared.update(aliases)
        if re.search(r"上班|职员|员工|公司|工位|办公室|职场", authority_text):
            for aliases in groups:
                if aliases[0] in {"同事", "上司"}:
                    declared.update(aliases)
        return declared

    def _daily_plan_undeclared_relationship_tokens(self, text: Any) -> list[str]:
        source = self._mask_non_relationship_phrases(text)
        if not source:
            return []
        declared = self._daily_plan_declared_relation_tokens()
        hits: list[str] = []
        identity_bound_groups = self._daily_plan_identity_bound_relationship_groups()
        all_aliases = sorted(
            {
                alias
                for group in self._daily_plan_relationship_alias_groups()
                if group[0] in identity_bound_groups
                for alias in group
            },
            key=len,
            reverse=True,
        )
        for alias in all_aliases:
            if alias not in declared and alias in source:
                hits.append(alias)
        return hits

    @staticmethod
    def _relationship_clause_is_explicitly_user_owned(clause: str, relation_tokens: list[str]) -> bool:
        if not clause or not relation_tokens:
            return False
        owner_marker = r"(?:主要用户|当前用户|这位用户|收件人|对方|用户|User|user)"
        for token in relation_tokens:
            escaped = re.escape(token)
            if re.search(rf"{owner_marker}[^，,。；;！？!?]{{0,24}}{escaped}", clause):
                return True
            if re.search(rf"{escaped}[^，,。；;！？!?]{{0,16}}(?:是|属于|来自)?{owner_marker}(?:的|那边)", clause):
                return True
        return False

    def _format_generation_relationship_authority_guard(self) -> str:
        return render_prompt_sections(
            [self._format_generation_relationship_authority_guard_prompt_section()],
            mode=PromptRenderMode.LABELED_BLOCK,
        )

    def _format_generation_relationship_authority_guard_prompt_section(self) -> PromptSection:
        declared = self._daily_plan_declared_relation_tokens()
        canonical = [
            aliases[0]
            for aliases in self._daily_plan_relationship_alias_groups()
            if any(alias in declared for alias in aliases)
        ]
        declared_text = "、".join(canonical) if canonical else "无"
        content = (
            "- 只有日程专用角色设定、日程世界观和当前默认人格能够建立 Bot 的稳定关系；"
            "旧日程、旧日记、旧动态、聊天摘要、MemoryCompanion、技能/创作记录和其他连续性材料不能单独证明一段新关系。\n"
            f"- 当前身份来源已声明的关系称谓：{declared_text}。只有这些关系及其同义称呼可以作为 Bot 的关系事实。\n"
            "- 连续性材料里的关系若不能和身份来源对上，就按未经核实的旧叙事略过；不要续写，也不要换个称呼继续沿用。\n"
            "- 用户谈到的亲友只属于用户，除非身份来源另有明确设定，不得转移成 Bot 自己的亲友。"
            "\n- 当前收件人与 Bot 的结构化关系由本轮收件人关系事实单独决定，不要用这份生活关系清单覆盖它。"
        )
        return prompt_section(
            key="relationship.generation_authority",
            title="关系事实权限",
            source="daily_state",
            content=content,
        )

    def _sanitize_generation_relationship_context(
        self,
        text: Any,
        *,
        source: str = "",
        max_chars: int = 0,
    ) -> str:
        """Remove undeclared relationship clauses before they reach a generator."""
        raw = str(text or "").strip()
        if not raw:
            return ""
        initial_hits = self._daily_plan_undeclared_relationship_tokens(raw)
        if not initial_hits:
            return raw[:max_chars] if max_chars > 0 else raw

        cleaned_lines: list[str] = []
        removed_any = False
        for raw_line in raw.splitlines():
            line = raw_line.strip()
            if not line:
                if cleaned_lines and cleaned_lines[-1]:
                    cleaned_lines.append("")
                continue
            pieces = re.split(r"([，,。；;！？!?]+)", line)
            kept: list[str] = []
            for index in range(0, len(pieces), 2):
                clause = pieces[index].strip()
                separator = pieces[index + 1] if index + 1 < len(pieces) else ""
                if not clause:
                    continue
                clause_hits = self._daily_plan_undeclared_relationship_tokens(clause)
                if clause_hits and not self._relationship_clause_is_explicitly_user_owned(clause, clause_hits):
                    removed_any = True
                    continue
                kept.append(clause)
                if separator:
                    kept.append(separator)
            clean_line = "".join(kept).strip(" ，,。；;！？!?")
            if clean_line and clean_line not in {"-", "*", "•"}:
                cleaned_lines.append(clean_line)
        if not removed_any:
            return raw[:max_chars] if max_chars > 0 else raw
        cleaned = "\n".join(cleaned_lines).strip()
        if max_chars > 0:
            cleaned = cleaned[:max_chars]
        if cleaned == (raw[:max_chars] if max_chars > 0 else raw):
            return cleaned

        log_key = f"{source or '-'}|{'/'.join(initial_hits[:4])}"
        now = _now_ts()
        recent_logs = getattr(self, "_recent_relationship_context_sanitize_logs", None)
        if not isinstance(recent_logs, dict):
            recent_logs = {}
            setattr(self, "_recent_relationship_context_sanitize_logs", recent_logs)
        if now - _safe_float(recent_logs.get(log_key), 0) >= 1800:
            logger.info(
                "生成前已剔除未声明关系上下文: source=%s relations=%s",
                source or "-",
                ",".join(initial_hits[:8]),
            )
            recent_logs[log_key] = now
        return cleaned

    def _daily_plan_clause_has_unsafe_social_fact(self, text: str) -> bool:
        clause = _single_line(text, 160)
        if not clause:
            return False
        if self._daily_plan_undeclared_relationship_tokens(clause):
            return True
        if self._daily_plan_clause_has_named_message_interaction(clause):
            return True
        future_commitment = (
            "约好",
            "约了",
            "约定",
            "约着",
            "约去",
            "约夜宵",
            "约饭",
            "约见",
            "约她",
            "约他",
            "约人",
            "下周",
            "下次一起",
            "改天一起",
            "明天一起",
            "后天一起",
            "之后一起",
            "过几天一起",
        )
        if any(token in clause for token in future_commitment):
            return True
        if re.search(r"(约|叫|喊|拉|找|邀)[^，。；;,.]{0,16}(一起|夜宵|吃|喝|看|玩|逛|见面|出门)", clause):
            return True
        if re.search(r"(和|跟)[^，。；;,.]{1,16}一起(去|吃|喝|看|玩|逛|见|出门|夜宵)", clause):
            return True
        if re.search(r"(消息|私信|电话|语音)[^，。；;,.]{0,16}(约|叫|喊|拉|邀)[^，。；;,.]{0,16}(一起|去|吃|喝|看|玩|逛|夜宵)", clause):
            return True
        concrete_relation = (
            "熟人",
            "同学",
            "老师",
            "朋友",
            "室友",
            "邻居",
            "前辈",
            "后辈",
            "家人",
            "父母",
            "妈妈",
            "爸爸",
            "哥哥",
            "姐姐",
            "弟弟",
            "妹妹",
        )
        if any(token in clause for token in ("碰见", "遇见", "撞见", "碰到", "遇到")) and any(
            token in clause for token in concrete_relation
        ):
            return True
        if re.search(r"(碰见|遇见|撞见|遇到)[过了]?[一-龥]{2,4}", clause) and not any(
            token in clause for token in ("路人", "店员", "陌生人", "旁边的人", "小动物", "猫", "狗", "鸟")
        ):
            return True
        if re.search(r"(顺手|顺带|特意|回来时|回来的时候)?.{0,8}给[^，。；;,.]{1,12}(带|买|捎|留|放)了?", clause):
            return True
        named_companion = re.search(
            r"(?:与|和|跟)\s*[A-Z][A-Za-z0-9_.-]{1,23}\s*(?:一起)?(?:吃|喝|聊|逛|玩|看|见面|出门)",
            clause,
        )
        if named_companion:
            name_match = re.search(r"(?:与|和|跟)\s*([A-Z][A-Za-z0-9_.-]{1,23})", named_companion.group(0))
            if name_match and self._daily_plan_named_entity_is_known(name_match.group(1)):
                return False
            return True
        return False

    @staticmethod
    def _sanitize_schedule_model_artifacts(text: Any, *, limit: int = 180) -> str:
        """Remove model scratch fields and speaker continuations from schedule prose."""
        source = re.sub(r"\s+", " ", str(text or "")).strip()
        if not source:
            return ""
        source = source.replace("```json", "").replace("```", "")
        source = source.replace("**", "").replace("__", "").replace("`", "")

        scratch_pattern = re.compile(
            r"(?:^|[\s，。；;!?！？])(?:dream[_\s-]*seed|analysis|reasoning(?:_content)?|角色草稿|续写提示)\s*[:：]",
            re.IGNORECASE,
        )
        scratch = scratch_pattern.search(source)
        if scratch:
            source = source[: scratch.start()].rstrip(" ，。；;:：")

        speaker_pattern = re.compile(
            r"(?:^|[\s，。；;!?！？])(?:Fox|Assistant|Character|Bot|[A-Z][A-Za-z0-9_.-]{1,20})\s*[:：]"
        )
        speaker = speaker_pattern.search(source)
        if speaker:
            if not source[: speaker.start()].strip(" ，。；;:："):
                return ""
            source = source[: speaker.start()].rstrip(" ，。；;:：")
        return _single_line(source, limit)

    @staticmethod
    def _schedule_text_is_single_meal_action(text: Any) -> bool:
        source = _single_line(text, 240)
        if not source:
            return False
        meal_action = re.search(
            r"吃(?:着|了|完|过|点|一|顿|碗)?|用餐|进餐|品尝|享用|早餐|早饭|午餐|午饭|晚餐|晚饭|夜宵|喝粥",
            source,
        )
        if not meal_action:
            return False
        return not re.search(
            r"吃完|饭后|餐后|随后|然后|之后|接着|再去|再把|转而|余下|剩下|后来|收拾完.*(?:休息|做|处理|出门)",
            source,
        )

    @staticmethod
    def _sanitize_schedule_meal_time_wording(text: Any, start_minutes: int | None) -> str:
        source = _single_line(text, 180)
        if not source or start_minutes is None:
            return source
        minute = int(start_minutes) % (24 * 60)
        if minute < 16 * 60:
            source = re.sub(r"吃(?:晚饭|晚餐)", "吃点东西", source)
            source = re.sub(r"(?:晚饭|晚餐)", "用餐", source)
        if minute >= 15 * 60:
            source = re.sub(r"吃(?:早餐|早饭)", "吃点东西", source)
            source = re.sub(r"(?:早餐|早饭)", "用餐", source)
        return _single_line(source, 180)

    @classmethod
    def _sanitize_overlong_schedule_activity(cls, text: Any, duration_minutes: int | None) -> str:
        source = _single_line(text, 180)
        if not source or duration_minutes is None or duration_minutes <= 120:
            return source
        if not cls._schedule_text_is_single_meal_action(source):
            return source
        stem = source.rstrip("。；;，, ")
        return _single_line(f"这段开始时，{stem}；吃完后便按这段时间的节奏休息或处理手边的事。", 180)

    def _sanitize_daily_plan_social_fact_text(self, text: str, *, field: str = "") -> str:
        source = self._sanitize_schedule_model_artifacts(text, limit=180)
        if not source:
            return ""
        raw_clauses = [part for part in re.split(r"[，,。；;]+", source) if _single_line(part, 120)]
        unsafe_flags = [self._daily_plan_clause_has_unsafe_social_fact(part) for part in raw_clauses]
        if not any(unsafe_flags):
            return source
        kept = []
        for index, part in enumerate(raw_clauses):
            if unsafe_flags[index]:
                continue
            cleaned_part = _single_line(part, 120)
            if (
                index + 1 < len(raw_clauses)
                and unsafe_flags[index + 1]
                and len(cleaned_part) <= 20
                and re.search(r"(?:时|的时候|期间|过程中)$", cleaned_part)
            ):
                continue
            kept.append(cleaned_part)
        cleaned = "，".join(kept).strip("，,。；; ")
        if not cleaned:
            cleaned = "放慢节奏处理手边的小事，把这段时间过得轻一点"
        if cleaned == source:
            return source
        log_key = "|".join((field or "-", _single_line(source, 120), _single_line(cleaned, 120)))
        now = _now_ts()
        recent_logs = getattr(self, "_recent_social_fact_sanitize_logs", None)
        if not isinstance(recent_logs, dict):
            recent_logs = {}
            setattr(self, "_recent_social_fact_sanitize_logs", recent_logs)
        last_logged = _safe_float(recent_logs.get(log_key), 0)
        if now - last_logged >= 1800:
            logger.info(
                "已清理日程中的未授权社交事实: field=%s before=%s after=%s",
                field or "-",
                _single_line(source, 120),
                _single_line(cleaned, 120),
            )
            recent_logs[log_key] = now
            if len(recent_logs) > 200:
                cutoff = now - 3600
                for key, ts in list(recent_logs.items()):
                    if _safe_float(ts, 0) < cutoff:
                        recent_logs.pop(key, None)
        return cleaned

    @staticmethod
    def _sanitize_empty_daily_plan_message_seed(text: str) -> str:
        cleaned = _single_line(text, 140)
        if not cleaned:
            return ""
        normalized = re.sub(r"[。！？!?,，、；;\s]+", "", cleaned)
        empty_markers = (
            "这段没什么想说的",
            "没什么想说的",
            "这段没有什么想说的",
            "没有什么想说的",
            "这段先留白",
            "先留白",
            "留白",
            "脑子空空的",
            "脑袋空空的",
            "没什么可说的",
            "没有什么可说的",
            "这段没话说",
            "没话说",
            "先不吵你",
            "不吵你",
            "先不打扰你",
            "不打扰你",
            "这段先安静一下",
            "先安静一下",
            "下午空一下",
            "下午空一会",
            "下午空一会儿",
            "下午空了下",
        )
        if normalized in empty_markers:
            return ""
        if any(token in normalized for token in ("没什么想说", "没有什么想说", "没什么可说", "没有什么可说")):
            return ""
        if any(token in normalized for token in ("先不吵", "不打扰", "先留白")):
            return ""
        if re.fullmatch(r"(?:上午|中午|下午|晚上|午后|傍晚)?(?:先)?空(?:一下|一会儿?|了下)", normalized):
            return ""
        return cleaned

    def _sanitize_daily_plan_inplace(self, plan: dict[str, Any]) -> bool:
        if not isinstance(plan, dict):
            return False
        raw_items = plan.get("items") if isinstance(plan.get("items"), list) else plan.get("schedule")
        if not isinstance(raw_items, list):
            return False
        changed = self._normalize_plan_item_intervals(raw_items)
        parsed_starts = [
            self._parse_hhmm_to_minutes(item.get("time")) if isinstance(item, dict) else None
            for item in raw_items
        ]
        for index, item in enumerate(raw_items):
            if not isinstance(item, dict):
                continue
            for field in ("activity", "message_seed"):
                original = _single_line(item.get(field), 180)
                if not original:
                    continue
                cleaned = self._sanitize_daily_plan_social_fact_text(original, field=field)
                if field == "activity":
                    start = parsed_starts[index]
                    next_start = next(
                        (candidate for candidate in parsed_starts[index + 1 :] if candidate is not None),
                        None,
                    )
                    end = self._plan_item_end_minutes(start, item, next_start=next_start) if start is not None else None
                    duration = end - start if start is not None and end is not None else None
                    cleaned = self._sanitize_schedule_meal_time_wording(cleaned, start)
                    cleaned = self._sanitize_overlong_schedule_activity(cleaned, duration)
                if field == "message_seed":
                    cleaned = self._sanitize_empty_daily_plan_message_seed(cleaned)
                if cleaned != original:
                    item[field] = cleaned
                    changed = True
        if changed:
            plan["sanitized_at"] = self._environment_now().strftime("%Y-%m-%d %H:%M:%S")
        return changed

    def _sanitize_state_variables_social_facts_inplace(self, state_variables: Any, *, field: str = "state_variables") -> bool:
        if not isinstance(state_variables, list):
            return False
        changed = False
        for index, item in enumerate(state_variables):
            if not isinstance(item, dict):
                continue
            for key in ("value", "note"):
                original = _single_line(item.get(key), 180)
                if not original:
                    continue
                cleaned = self._sanitize_daily_plan_social_fact_text(
                    original,
                    field=f"{field}.{index}.{key}",
                )
                if cleaned != original:
                    item[key] = cleaned
                    changed = True
        return changed

    def _sanitize_relationship_text_tree_inplace(self, value: Any, *, field: str) -> bool:
        changed = False
        if isinstance(value, dict):
            for key, item in list(value.items()):
                item_field = f"{field}.{key}" if field else str(key)
                if str(key) in {
                    "raw",
                    "raw_text",
                    "original_text",
                    "prompt",
                    "prompt_text",
                    "response",
                    "response_text",
                }:
                    continue
                if isinstance(item, str):
                    cleaned = self._sanitize_generation_relationship_context(item, source=item_field)
                    if cleaned != item:
                        value[key] = cleaned
                        changed = True
                elif isinstance(item, (dict, list)) and self._sanitize_relationship_text_tree_inplace(
                    item,
                    field=item_field,
                ):
                    changed = True
            return changed
        if isinstance(value, list):
            rebuilt: list[Any] = []
            for index, item in enumerate(value):
                item_field = f"{field}.{index}" if field else str(index)
                if isinstance(item, str):
                    cleaned = self._sanitize_generation_relationship_context(item, source=item_field)
                    if cleaned != item:
                        changed = True
                    if cleaned:
                        rebuilt.append(cleaned)
                else:
                    if isinstance(item, (dict, list)) and self._sanitize_relationship_text_tree_inplace(
                        item,
                        field=item_field,
                    ):
                        changed = True
                    rebuilt.append(item)
            if rebuilt != value:
                value[:] = rebuilt
                changed = True
        return changed

    @story_legacy_sync_operation("daily-state.story-source-sanitize")
    def _cleanup_generated_relationship_history_inplace(self) -> bool:
        """Stop old Bot-authored relationship hallucinations from becoming new evidence."""
        data = getattr(self, "data", None)
        if not isinstance(data, dict):
            return False
        changed = False
        counts: dict[str, int] = {}

        for key in (
            "daily_state",
            "daily_plan_history",
            "daily_story_plan_history",
            "detail_enhanced_history",
        ):
            value = data.get(key)
            if isinstance(value, (dict, list)) and self._sanitize_relationship_text_tree_inplace(value, field=key):
                changed = True
                counts[key] = counts.get(key, 0) + 1

        for key in ("bot_diaries", "self_meal_log", "proactive_audit_log"):
            records = data.get(key)
            if isinstance(records, list) and self._sanitize_relationship_text_tree_inplace(records, field=key):
                changed = True
                counts[key] = counts.get(key, 0) + 1

        projects = data.get("creative_projects")
        if isinstance(projects, list):
            for index, project in enumerate(projects):
                if not isinstance(project, dict):
                    continue
                source_text = project.get("source_text")
                if not isinstance(source_text, str):
                    continue
                cleaned = self._sanitize_generation_relationship_context(
                    source_text,
                    source=f"creative_projects.{index}.source_text",
                )
                if cleaned != source_text:
                    project["source_text"] = cleaned
                    changed = True
                    counts["creative_projects.source_text"] = counts.get("creative_projects.source_text", 0) + 1

        skill_state = data.get("skill_growth")
        skills = skill_state.get("skills") if isinstance(skill_state, dict) else None
        if isinstance(skills, dict):
            for skill in skills.values():
                if not isinstance(skill, dict):
                    continue
                logs = skill.get("recent_logs")
                if not isinstance(logs, list):
                    continue
                if self._sanitize_relationship_text_tree_inplace(
                    logs,
                    field="skill_growth.recent_logs",
                ):
                    changed = True
                    counts["skill_growth.recent_logs"] = counts.get("skill_growth.recent_logs", 0) + 1

        qzone_state = data.get("qzone_integration")
        if isinstance(qzone_state, dict):
            recent_posts = qzone_state.get("recent_life_publish_texts")
            if isinstance(recent_posts, list) and self._sanitize_relationship_text_tree_inplace(
                recent_posts,
                field="qzone_integration.recent_life_publish_texts",
            ):
                changed = True
                counts["qzone_integration.recent_life_publish_texts"] = 1
            for key, value in list(qzone_state.items()):
                if not isinstance(value, str):
                    continue
                if not (
                    key.endswith("_text")
                    or key.endswith("_draft")
                    or key.endswith("_caption")
                    or key in {"last_publish_recorded_text"}
                ):
                    continue
                cleaned = self._sanitize_generation_relationship_context(
                    value,
                    source=f"qzone_integration.{key}",
                )
                if cleaned != value:
                    qzone_state[key] = cleaned
                    changed = True
                    counts["qzone_integration.text_fields"] = counts.get("qzone_integration.text_fields", 0) + 1

        if changed:
            logger.info("已清理未声明关系的生成历史: %s", counts)
        return changed

    def _sanitize_runtime_social_facts_inplace(self) -> bool:
        data = getattr(self, "data", None)
        if not isinstance(data, dict):
            return False
        changed = False
        daily_plan = data.get("daily_plan")
        if isinstance(daily_plan, dict) and self._sanitize_daily_plan_inplace(daily_plan):
            changed = True
        story_plan = data.get("daily_story_plan")
        if isinstance(story_plan, dict) and self._sanitize_story_plan_social_facts_inplace(story_plan):
            changed = True
        enhanced = data.get("detail_enhanced_segments")
        if isinstance(enhanced, dict) and self._sanitize_detail_enhanced_segments_inplace(enhanced):
            changed = True
        pool = data.get("proactive_candidate_pool")
        if isinstance(pool, list):
            for index, item in enumerate(pool):
                if self._sanitize_proactive_social_fact_fields_inplace(
                    item,
                    field=f"proactive_candidate_pool.{index}",
                ):
                    changed = True
        users = data.get("users")
        if isinstance(users, dict):
            for user_id, user in users.items():
                if self._sanitize_user_proactive_social_facts_inplace(user, field=f"users.{user_id}"):
                    changed = True
        if self._cleanup_generated_relationship_history_inplace():
            changed = True
        if changed:
            data["social_fact_sanitized_at"] = self._environment_now().strftime("%Y-%m-%d %H:%M:%S")
        return changed

    def _cleanup_framework_meta_leak_records(self) -> bool:
        data = getattr(self, "data", None)
        if not isinstance(data, dict):
            return False
        meta_leak_checker = getattr(self, "_framework_agent_meta_summary_leak", None)
        if not callable(meta_leak_checker):
            return False

        def has_meta(value: Any) -> bool:
            if value is None:
                return False
            if isinstance(value, str):
                return meta_leak_checker(value)
            return meta_leak_checker(str(value))

        def list_item_has_meta(item: Any, fields: tuple[str, ...]) -> bool:
            if isinstance(item, dict):
                return any(has_meta(item.get(field)) for field in fields)
            return has_meta(item)

        changed = False
        removed_counts: dict[str, int] = {}

        def filter_list(owner: dict[str, Any], key: str, fields: tuple[str, ...], *, limit: int | None = None) -> None:
            nonlocal changed
            raw = owner.get(key)
            if not isinstance(raw, list):
                return
            kept = [item for item in raw if not list_item_has_meta(item, fields)]
            if limit is not None:
                kept = kept[-limit:]
            if len(kept) != len(raw):
                owner[key] = kept
                removed_counts[key] = removed_counts.get(key, 0) + len(raw) - len(kept)
                changed = True

        users = data.get("users")
        if isinstance(users, dict):
            for user in users.values():
                if not isinstance(user, dict):
                    continue
                for key in (
                    "last_companion_message",
                    "last_proactive_message",
                    "last_proactive_text",
                    "last_reply_text",
                ):
                    if has_meta(user.get(key)):
                        user[key] = ""
                        removed_counts[key] = removed_counts.get(key, 0) + 1
                        changed = True
                filter_list(user, "recent_proactive_topics", ("text", "signature", "topic", "motive"), limit=12)
                filter_list(user, "recent_reply_topics", ("text", "signature", "topic"), limit=18)
                filter_list(user, "action_consequences", ("text", "summary", "action_summary"), limit=18)
                continuity = user.get("state_continuity")
                if isinstance(continuity, dict):
                    for key in ("last_action_text", "last_reply_text", "last_message_text"):
                        if has_meta(continuity.get(key)):
                            continuity[key] = ""
                            count_key = f"state_continuity.{key}"
                            removed_counts[count_key] = removed_counts.get(count_key, 0) + 1
                            changed = True

        filter_list(data, "proactive_audit_log", ("text_preview", "original_text_preview", "final_text_preview", "text", "note", "topic", "motive", "diagnostic_detail"), limit=120)

        troubleshooting = data.get("troubleshooting_test_results")
        if isinstance(troubleshooting, dict):
            for key, result in list(troubleshooting.items()):
                if list_item_has_meta(result, ("text_preview", "original_text_preview", "final_text_preview", "detail", "error", "diagnostic_detail")):
                    troubleshooting.pop(key, None)
                    removed_counts["troubleshooting_test_results"] = removed_counts.get("troubleshooting_test_results", 0) + 1
                    changed = True

        prompt_root = data.get("recent_prompt_injections")
        if isinstance(prompt_root, dict):
            for kind, items in list(prompt_root.items()):
                if not isinstance(items, list):
                    continue
                kept: list[Any] = []
                removed = 0
                for item in items:
                    item_has_meta = list_item_has_meta(item, ("preview", "content", "title"))
                    if not item_has_meta and isinstance(item, dict):
                        modules = item.get("modules")
                        if isinstance(modules, list):
                            item_has_meta = any(
                                list_item_has_meta(module, ("preview", "content", "title", "key"))
                                for module in modules
                            )
                    if item_has_meta:
                        removed += 1
                        continue
                    kept.append(item)
                if removed:
                    prompt_root[kind] = kept[:8] if kind == "tts" else kept[:5]
                    count_key = f"recent_prompt_injections.{kind}"
                    removed_counts[count_key] = removed_counts.get(count_key, 0) + removed
                    changed = True

        if changed:
            logger.info("已清理框架工具循环摘要污染记录: %s", removed_counts)
        return changed

    def _sanitize_story_plan_social_facts_inplace(self, story_plan: dict[str, Any]) -> bool:
        if not isinstance(story_plan, dict):
            return False
        changed = False
        summary = _single_line(story_plan.get("summary"), 180)
        if summary:
            cleaned = self._sanitize_daily_plan_social_fact_text(summary, field="story_plan.summary")
            if cleaned != summary:
                story_plan["summary"] = cleaned
                changed = True
        if self._sanitize_state_variables_social_facts_inplace(
            story_plan.get("state_variables"),
            field="story_plan.state_variables",
        ):
            changed = True
        for item in story_plan.get("today_events") or []:
            if not isinstance(item, dict):
                continue
            original = _single_line(item.get("event"), 180)
            if not original:
                continue
            cleaned = self._sanitize_daily_plan_social_fact_text(original, field="story_plan.today_events.event")
            if cleaned != original:
                item["event"] = cleaned
                changed = True
        for item in story_plan.get("proactive_events") or []:
            if not isinstance(item, dict):
                continue
            for field in ("topic", "why", "motive", "scene", "impulse"):
                original = _single_line(item.get(field), 180)
                if not original:
                    continue
                cleaned = self._sanitize_daily_plan_social_fact_text(original, field=f"story_plan.proactive_events.{field}")
                if cleaned != original:
                    item[field] = cleaned
                    changed = True
        if changed:
            story_plan["sanitized_at"] = self._environment_now().strftime("%Y-%m-%d %H:%M:%S")
        return changed

    def _detail_segment_bounds_for_snapshot_key(self, key: str) -> tuple[int, int] | None:
        match = re.fullmatch(r"\d{4}-\d{2}-\d{2}:(\d+):(\d{1,2}:\d{2})", str(key or ""))
        if not match:
            return None
        plan = getattr(self, "data", {}).get("daily_plan", {})
        items = plan.get("items") if isinstance(plan, dict) else None
        if not isinstance(items, list):
            return None
        index = _safe_int(match.group(1), -1, minimum=-1)
        start = self._parse_hhmm_to_minutes(match.group(2))
        if index < 0 or start is None:
            return None
        next_start = None
        for next_item in items[index + 1 :]:
            if not isinstance(next_item, dict):
                continue
            next_start = self._parse_hhmm_to_minutes(next_item.get("time"))
            if next_start is not None:
                break
        current_item = items[index] if index < len(items) and isinstance(items[index], dict) else None
        end = self._plan_item_end_minutes(start, current_item, next_start=next_start)
        return start, end

    def _sanitize_detail_snapshot_for_segment_inplace(
        self,
        snapshot: dict[str, Any],
        segment: dict[str, Any] | tuple[int, int] | None,
        *,
        field: str = "detail",
    ) -> bool:
        if not isinstance(snapshot, dict):
            return False
        if isinstance(segment, tuple):
            start, end = segment
        elif isinstance(segment, dict):
            start = _safe_int(segment.get("start"), 0)
            end = _safe_int(segment.get("end"), self._segment_end_minutes(start, segment.get("item")))
            if end <= start:
                end += 24 * 60
        else:
            start = end = None
        duration = end - start if start is not None and end is not None else None
        changed = False

        original_summary = _single_line(snapshot.get("summary"), 180)
        if original_summary:
            summary = self._sanitize_daily_plan_social_fact_text(original_summary, field=f"{field}.summary")
            summary = self._sanitize_schedule_meal_time_wording(summary, start)
            summary = self._sanitize_overlong_schedule_activity(summary, duration)
            if summary != original_summary:
                snapshot["summary"] = summary
                changed = True

        meal_event_minutes: list[int] = []
        for index, item in enumerate(snapshot.get("today_events") or []):
            if not isinstance(item, dict):
                continue
            original = _single_line(item.get("event"), 180)
            if not original:
                continue
            event_start = start
            window = _single_line(item.get("window"), 24)
            window_match = re.fullmatch(r"\s*(\d{1,2}:\d{2})\s*[-~—–至]\s*(\d{1,2}:\d{2})\s*", window)
            event_end = None
            if window_match:
                event_start = self._parse_hhmm_to_minutes(window_match.group(1))
                event_end = self._parse_hhmm_to_minutes(window_match.group(2))
                if event_start is not None and event_end is not None:
                    if event_end <= event_start:
                        event_end += 24 * 60
                    if self._schedule_text_is_single_meal_action(original):
                        meal_event_minutes.append(max(1, event_end - event_start))
            cleaned = self._sanitize_daily_plan_social_fact_text(
                original,
                field=f"{field}.today_events.{index}.event",
            )
            cleaned = self._sanitize_schedule_meal_time_wording(cleaned, event_start)
            if cleaned != original:
                item["event"] = cleaned
                changed = True

        presence = snapshot.get("presence_status")
        if isinstance(presence, dict) and duration is not None and duration > 120:
            custom_text = _single_line(presence.get("custom_text") or presence.get("wording"), 28)
            if self._schedule_text_is_single_meal_action(custom_text):
                cap = min(60, max(meal_event_minutes) if meal_event_minutes else 45)
                configured = _safe_int(presence.get("duration_minutes"), cap, minimum=1)
                if configured > cap or not _single_line(presence.get("duration_minutes"), 12):
                    presence["duration_minutes"] = str(cap)
                    changed = True
        return changed

    def _sanitize_detail_enhanced_segments_inplace(self, enhanced: dict[str, Any]) -> bool:
        if not isinstance(enhanced, dict):
            return False
        changed = False
        for key, snapshot in enhanced.items():
            if not isinstance(snapshot, dict):
                continue
            snapshot_changed = False
            if (
                _single_line(snapshot.get("status"), 24) == "generating"
                and not self._detail_enhancement_snapshot_blocks_generation(snapshot)
            ):
                stale_generation_id = _single_line(snapshot.get("generation_id"), 64)
                snapshot["status"] = "failed"
                snapshot["updated_at"] = self._environment_now().strftime("%H:%M")
                snapshot["error"] = _single_line(snapshot.get("error"), 180) or "上次细化生成中断或超时"
                snapshot["retry_after"] = ""
                snapshot["retry_after_ts"] = 0
                snapshot["summary"] = _single_line(snapshot.get("summary"), 120) or "这一段细化生成中断，稍后会自动重试。"
                stored_previous_state = snapshot.get("previous_item_state") if isinstance(snapshot.get("previous_item_state"), dict) else {}
                snapshot.pop("generation_id", None)
                snapshot.pop("previous_item_state", None)
                keyed = re.fullmatch(r"(\d{4}-\d{2}-\d{2}):(\d+):(\d{1,2}:\d{2})", str(key))
                live_plan = self.data.get("daily_plan", {})
                live_items = live_plan.get("items") if isinstance(live_plan, dict) else None
                if keyed and isinstance(live_items, list):
                    index = int(keyed.group(2))
                    live_item = live_items[index] if 0 <= index < len(live_items) and isinstance(live_items[index], dict) else None
                    if isinstance(live_item, dict) and _single_line(live_item.get("_detail_generation_id"), 64) == stale_generation_id:
                        if stored_previous_state:
                            for field, state in stored_previous_state.items():
                                if not isinstance(state, dict):
                                    continue
                                if bool(state.get("existed")):
                                    live_item[field] = state.get("value")
                                else:
                                    live_item.pop(field, None)
                        else:
                            live_item.pop("_detail_generation_id", None)
                changed = True
                snapshot_changed = True
            bounds = self._detail_segment_bounds_for_snapshot_key(str(key))
            if bounds and not isinstance(snapshot.get("quality"), dict):
                snapshot["quality"] = evaluate_detail_quality(
                    self,
                    snapshot,
                    {"start": bounds[0], "end": bounds[1], "item": {}},
                )
                changed = True
                snapshot_changed = True
            if self._sanitize_detail_snapshot_for_segment_inplace(
                snapshot,
                bounds,
                field=f"detail_enhanced_segments.{key}",
            ):
                changed = True
                snapshot_changed = True
            summary = _single_line(snapshot.get("summary"), 180)
            if summary:
                cleaned = self._sanitize_daily_plan_social_fact_text(summary, field=f"detail_enhanced_segments.{key}.summary")
                if cleaned != summary:
                    snapshot["summary"] = cleaned
                    changed = True
                    snapshot_changed = True
            if self._sanitize_state_variables_social_facts_inplace(
                snapshot.get("state_variables"),
                field=f"detail_enhanced_segments.{key}.state_variables",
            ):
                changed = True
                snapshot_changed = True
            for item in snapshot.get("today_events") or []:
                if not isinstance(item, dict):
                    continue
                original = _single_line(item.get("event"), 180)
                if not original:
                    continue
                cleaned = self._sanitize_daily_plan_social_fact_text(original, field=f"detail_enhanced_segments.{key}.today_events.event")
                if cleaned != original:
                    item["event"] = cleaned
                    changed = True
                    snapshot_changed = True
            for item in snapshot.get("proactive_events") or []:
                if not isinstance(item, dict):
                    continue
                for field in ("topic", "why", "motive", "scene", "impulse"):
                    original = _single_line(item.get(field), 180)
                    if not original:
                        continue
                    cleaned = self._sanitize_daily_plan_social_fact_text(original, field=f"detail_enhanced_segments.{key}.proactive_events.{field}")
                    if cleaned != original:
                        item[field] = cleaned
                        changed = True
                        snapshot_changed = True
            if snapshot_changed and snapshot.get("status") == "done":
                snapshot["coverage_repair_done"] = True
                snapshot["social_fact_sanitized_at"] = self._environment_now().strftime("%Y-%m-%d %H:%M:%S")
        return changed

    @staticmethod
    def _deepseek_peak_minute(value: str, *, allow_24: bool = False) -> int | None:
        match = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", str(value or ""))
        if not match:
            return None
        hour, minute = int(match.group(1)), int(match.group(2))
        if allow_24 and hour == 24 and minute == 0:
            return 1440
        if hour > 23 or minute > 59:
            return None
        return hour * 60 + minute

    def _parse_deepseek_peak_windows(self) -> list[tuple[int, int]]:
        raw = str(getattr(self, "deepseek_peak_windows", "") or "")
        windows: list[tuple[int, int]] = []
        for item in re.split(r"[,，;；\n]+", raw):
            text = item.strip()
            if not text:
                continue
            match = re.fullmatch(r"\s*(\d{1,2}:\d{2})\s*[-~～—至]+\s*(\d{1,2}:\d{2})\s*", text)
            if not match:
                continue
            start = self._deepseek_peak_minute(match.group(1))
            end = self._deepseek_peak_minute(match.group(2), allow_24=True)
            if start is None or end is None or start == end:
                continue
            windows.append((start, end))
        return windows

    def _deepseek_peak_status(self, now: datetime | None = None) -> dict[str, Any]:
        timezone_name = str(getattr(self, "deepseek_peak_timezone", "Asia/Shanghai") or "Asia/Shanghai").strip()
        try:
            timezone = zoneinfo.ZoneInfo(timezone_name)
        except Exception:
            timezone_name = "Asia/Shanghai"
            timezone = zoneinfo.ZoneInfo(timezone_name)
        if now is None:
            local_now = datetime.now(timezone)
        elif now.tzinfo is None:
            local_now = now.replace(tzinfo=timezone)
        else:
            local_now = now.astimezone(timezone)
        windows = self._parse_deepseek_peak_windows()
        minute = local_now.hour * 60 + local_now.minute
        active = any(
            (start <= minute < end) if start < end else (minute >= start or minute < end)
            for start, end in windows
        )
        transitions: list[datetime] = []
        base_day = local_now.date()
        # Include yesterday so a cross-midnight window can expose its upcoming
        # end transition while the current time is after midnight.
        for day_offset in range(-1, 3):
            day = base_day + timedelta(days=day_offset)
            for start, end in windows:
                start_dt = datetime.combine(day, datetime.min.time(), timezone) + timedelta(minutes=start)
                end_day = day + timedelta(days=1) if start > end else day
                end_minute = end if end < 1440 else 0
                if end == 1440:
                    end_day = day + timedelta(days=1)
                end_dt = datetime.combine(end_day, datetime.min.time(), timezone) + timedelta(minutes=end_minute)
                if start_dt > local_now:
                    transitions.append(start_dt)
                if end_dt > local_now:
                    transitions.append(end_dt)
        next_transition = min(transitions) if transitions else None
        replacement_id = str(getattr(self, "deepseek_peak_replacement_provider_id", "") or "").strip()
        enabled = bool(getattr(self, "enable_deepseek_peak_replacement", False))
        return {
            "enabled": enabled,
            "active": bool(enabled and active and replacement_id),
            "in_window": active,
            "configured": bool(replacement_id),
            "timezone": timezone_name,
            "current_time": local_now.strftime("%Y-%m-%d %H:%M"),
            "next_transition": next_transition.strftime("%Y-%m-%d %H:%M") if next_transition else "",
            "windows": [
                f"{start // 60:02d}:{start % 60:02d}-{('24:00' if end == 1440 else f'{end // 60:02d}:{end % 60:02d}')}"
                for start, end in windows
            ],
            "replacement_provider_id": replacement_id,
        }

    def _provider_matches_deepseek(self, provider_id: str) -> bool:
        safe_id = str(provider_id or "").strip()
        if not safe_id:
            return False
        parts = [safe_id]
        provider = None
        getter = getattr(getattr(self, "context", None), "get_provider_by_id", None)
        if callable(getter):
            try:
                provider = getter(safe_id)
            except Exception:
                provider = None
        if provider is not None:
            parts.extend(
                str(value or "")
                for value in (
                    getattr(provider, "name", ""),
                    getattr(provider, "display_name", ""),
                    provider.__class__.__name__,
                )
            )
            config = getattr(provider, "provider_config", None) or getattr(provider, "config", None) or {}
            fields = (
                "id", "provider_id", "name", "display_name", "label", "title", "provider", "type",
                "provider_type", "model", "model_name", "api_model", "model_id", "api_base", "base_url",
                "api_base_url", "api_url", "endpoint", "url",
            )
            for field in fields:
                value = config.get(field, "") if isinstance(config, dict) else getattr(config, field, "")
                if value:
                    parts.append(str(value))
        keywords = [
            item.strip().lower()
            for item in re.split(r"[,，;；\n]+", str(getattr(self, "deepseek_peak_match_keywords", "") or ""))
            if item.strip()
        ] or ["deepseek", "深度求索"]
        haystack = " ".join(parts).lower()
        return any(keyword in haystack for keyword in keywords)

    def _apply_deepseek_peak_replacement(
        self,
        provider_id: str,
        *,
        now: datetime | None = None,
        target: str = "plugin",
    ) -> str:
        original = str(provider_id or "").strip()
        if not scope_allows(getattr(self, "model_replacement_scope", "plugin"), target):
            return original
        status = self._deepseek_peak_status(now)
        replacement = str(status.get("replacement_provider_id") or "").strip()
        if not status.get("active") or not original or not replacement or replacement == original:
            return original
        if not self._provider_matches_deepseek(original):
            return original
        log_key = f"{local_day if (local_day := status.get('current_time', '')[:10]) else ''}|{original}|{replacement}"
        if getattr(self, "_deepseek_peak_last_log_key", "") != log_key:
            self._deepseek_peak_last_log_key = log_key
            logger.info("DeepSeek 高价时段临时路由: %s -> %s (%s)", original, replacement, status.get("current_time"))
        return replacement

    def _task_provider(
        self,
        *provider_ids: str | None,
        allow_replacement: bool = True,
    ) -> str:
        for provider_id in provider_ids:
            value = str(provider_id or "").strip()
            if value:
                if not allow_replacement:
                    return value
                routed = value
                if scope_allows(getattr(self, "model_replacement_scope", "plugin"), "plugin"):
                    sources = CURRENT_MODEL_REPLACEMENT_SOURCES.get(())
                    rules = getattr(self, "model_replacement_rules", None)
                    if sources and isinstance(rules, list):
                        match = find_route(rules, sources)
                        if match is not None:
                            candidate = str(match.rule.provider_id or "").strip()
                            getter = getattr(getattr(self, "context", None), "get_provider_by_id", None)
                            if candidate and callable(getter):
                                try:
                                    if getter(candidate) is not None:
                                        routed = candidate
                                except Exception:
                                    pass
                return self._apply_deepseek_peak_replacement(routed, target="plugin")
        return ""

    def _parse_plan_items(self, raw_text: str) -> list[dict[str, str]]:
        payload = self._extract_json_payload(raw_text)
        if payload is None:
            return []
        if isinstance(payload, dict):
            raw_items = (
                payload.get("schedule")
                or payload.get("items")
                or payload.get("tasks")
                or payload.get("events")
                or payload.get("plan")
                or []
            )
        elif isinstance(payload, list):
            raw_items = payload
        else:
            raw_items = []

        items: list[dict[str, str]] = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            raw_time = item.get("time") or item.get("start") or item.get("start_time") or item.get("begin_time") or item.get("开始时间")
            item_time, range_end = self._normalize_plan_clock_range(raw_time)
            if self._parse_hhmm_to_minutes(item_time) is None:
                continue
            raw_activity = _single_line(
                item.get("activity")
                or item.get("title")
                or item.get("task")
                or item.get("event")
                or item.get("内容"),
                120,
            )
            activity = self._align_plan_text_with_skill_bounds(
                self._sanitize_daily_plan_social_fact_text(
                    self._soften_destructive_daily_plan_text(raw_activity),
                    field="activity",
                )
            )
            if not activity:
                continue
            mood = self._align_plan_text_with_skill_bounds(
                self._soften_destructive_daily_plan_text(_single_line(item.get("mood"), 30))
            )
            raw_message_seed = _single_line(item.get("message_seed"), 140)
            message_seed = self._align_plan_text_with_skill_bounds(
                self._sanitize_empty_daily_plan_message_seed(
                    self._sanitize_daily_plan_social_fact_text(
                        self._soften_destructive_daily_plan_text(
                            self._deemphasize_state_report_preamble(
                                raw_message_seed,
                                reason="background_schedule",
                            )
                        ),
                        field="message_seed",
                    )
                )
            )
            items.append(
                {
                    "time": item_time,
                    "end": self._normalize_plan_clock(
                        item.get("end")
                        or item.get("end_time")
                        or item.get("finish_time")
                        or item.get("until")
                        or item.get("结束时间")
                        or range_end
                    ),
                    "activity": activity,
                    "mood": mood,
                    "message_seed": message_seed,
                    "basis": self._normalize_schedule_basis(item.get("basis"), default=["inspiration"]),
                    "confidence": min(1.0, _safe_float(item.get("confidence"), 0.7)),
                }
            )
        items = sorted(items, key=lambda item: self._parse_hhmm_to_minutes(item["time"]) or 0)
        items = items[: _safe_int(runtime_persona_setting(self, "daily_plan_item_count", 10), 10, 1)]
        self._normalize_plan_item_intervals(items)
        # Pass every generated item through the C3 write gate.  LLM fields such
        # as status, source_refs, authority and evidence are never trusted;
        # canonical axes are retained so downstream views cannot silently lose
        # the distinction between a plan and an observation.
        today = _today_key()
        for index, item in enumerate(items):
            try:
                canonical = normalize_plan_item(
                    {**item, "title": item.get("activity"), "date": today, "subject_actor_id": "bot_self", "actor_type": "bot"},
                    plan_id=f"{today}:{index}",
                    now=self._environment_now(),
                )
            except Exception:
                continue
            item.update(canonical)
            item["activity"] = _single_line(item.get("activity") or item.get("title"), 120)
            item["date"] = today
        return items

    def _normalize_plan_clock_range(self, value: Any) -> tuple[str, str]:
        """Normalize common model time forms and extract an optional range end."""

        text = _single_line(value, 32).strip()
        if not text:
            return "", ""
        matches = re.findall(r"(?<!\d)(\d{1,2})\s*[:：点时]\s*(\d{1,2})?", text)
        clocks: list[str] = []
        for hour_text, minute_text in matches[:2]:
            hour = int(hour_text)
            minute = int(minute_text or 0)
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                clocks.append(f"{hour:02d}:{minute:02d}")
        if not clocks:
            return "", ""
        return clocks[0], clocks[1] if len(clocks) > 1 else ""

    def _normalize_plan_clock(self, value: Any) -> str:
        start, _end = self._normalize_plan_clock_range(value)
        return start

    @staticmethod
    def _soften_destructive_daily_plan_text(text: str) -> str:
        softened = _single_line(text, 160)
        if not softened:
            return ""
        replacements = [
            (r"想[^，。,；;]{0,18}(砸|摔|打人|揍人|报复|毁掉|弄坏)[^，。,；;]{0,18}", "烦得想先躲开一会儿"),
            (r"(把|将)[^，。,；;]{0,14}(砸|摔|扔)[^，。,；;]{0,14}(地上|墙上|门上|出去|烂|碎)[^，。,；;]{0,8}", "把手边的东西往里推了推"),
            (r"(砸|摔)(东西|门|墙|书|杯子|手机|笔)[^，。,；;]{0,8}", "把东西先放远一点"),
            (r"(骂人|想骂|吼人|想吼)[^，。,；;]{0,10}", "把话咽回去"),
        ]
        for pattern, replacement in replacements:
            softened = re.sub(pattern, replacement, softened)
        softened = re.sub(r"(烦躁|暴躁|恼火)到?有点?攻击性", "烦躁得有点想躲开", softened)
        softened = softened.replace("想砸东西的烦躁", "有点烦,但努力收着")
        softened = softened.replace("想摔东西的烦躁", "有点烦,但努力收着")
        return _single_line(softened, 160)

    @staticmethod
    def _strip_json_payload_comments(text: str) -> str:
        result: list[str] = []
        index = 0
        in_string = False
        quote_char = ""
        escaped = False
        while index < len(text):
            char = text[index]
            nxt = text[index + 1] if index + 1 < len(text) else ""
            if in_string:
                result.append(char)
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == quote_char:
                    in_string = False
                    quote_char = ""
                index += 1
                continue
            if char in {'"', "'"}:
                in_string = True
                quote_char = char
                result.append(char)
                index += 1
                continue
            if char == "/" and nxt == "/":
                index += 2
                while index < len(text) and text[index] not in "\r\n":
                    index += 1
                continue
            if char == "/" and nxt == "*":
                index += 2
                while index + 1 < len(text) and not (text[index] == "*" and text[index + 1] == "/"):
                    index += 1
                index = min(len(text), index + 2)
                continue
            result.append(char)
            index += 1
        return "".join(result)

    def _repair_json_payload(self, text: str) -> str:
        repaired = str(text or "").strip()
        repaired = repaired.replace("\ufeff", "")
        repaired = repaired.replace("“", '"').replace("”", '"')
        repaired = repaired.replace("‘", "'").replace("’", "'")
        repaired = self._strip_json_payload_comments(repaired)
        repaired = re.sub(r",\s*([}\]])", r"\1", repaired)
        return repaired.strip()

    def _extract_json_payload(self, raw_text: str) -> Any:
        text = str(raw_text or "").strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        candidates = [text]
        object_start, object_end = text.find("{"), text.rfind("}")
        if object_start >= 0 and object_end > object_start:
            candidates.append(text[object_start : object_end + 1])
        array_start, array_end = text.find("["), text.rfind("]")
        if array_start >= 0 and array_end > array_start:
            candidates.append(text[array_start : array_end + 1])
        seen_candidates: set[str] = set()
        for candidate in candidates:
            if candidate in seen_candidates:
                continue
            seen_candidates.add(candidate)
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                repaired = self._repair_json_payload(candidate)
                if repaired and repaired not in seen_candidates:
                    seen_candidates.add(repaired)
                    try:
                        return json.loads(repaired)
                    except json.JSONDecodeError:
                        pass
                    try:
                        parsed = ast.literal_eval(repaired)
                    except (SyntaxError, ValueError):
                        parsed = None
                    if isinstance(parsed, (dict, list)):
                        return parsed
        return None

    def _get_current_plan_item(self, plan: dict[str, Any]) -> dict[str, str] | None:
        if not self._is_plan_date_active(plan.get("date")):
            return None
        items = plan.get("items")
        if not isinstance(items, list):
            return None
        current_minutes = self._effective_plan_now_minutes(str(plan.get("date") or ""))
        if current_minutes is None:
            return None
        selected = None
        selected_start: int | None = None
        starts = self._normalized_plan_item_starts(items)
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            if self._normalize_schedule_lifecycle_status(item.get("lifecycle_status")) == "cancelled":
                continue
            item_minutes = starts[index] if index < len(starts) else None
            if item_minutes is None:
                continue
            next_start = next((value for value in starts[index + 1 :] if value is not None), None)
            item_end = self._plan_item_end_minutes(item_minutes, item, next_start=next_start)
            if item_minutes <= current_minutes < item_end:
                selected = item
                selected_start = item_minutes
                break
        if isinstance(selected, dict):
            # A clock window is not execution evidence.  Keep confirmed
            # schedule commitments available through the future/schedule
            # policy, but expose current plan text only when a compatible
            # observation or a short-lived resolver commit exists.
            policy_allows_current = True
            policy_getter = getattr(self, "_agenda_disclosure_view", None)
            if callable(policy_getter):
                policy_allows_current = False
                try:
                    view = policy_getter("current_fact", now=self._environment_now(), max_entries=128)
                    values = view.get("entries", []) if isinstance(view, dict) else getattr(view, "entries", [])
                    selected_key = _single_line(selected.get("plan_id"), 120)
                    selected_pair = (
                        _single_line(selected.get("time"), 12),
                        _single_line(selected.get("activity") or selected.get("title"), 120),
                    )
                    for value in values if isinstance(values, list) else []:
                        if not isinstance(value, dict):
                            continue
                        value_key = _single_line(value.get("plan_id") or value.get("entry_id"), 120)
                        value_pair = (
                            _single_line(value.get("time"), 12),
                            _single_line(value.get("title") or value.get("activity"), 120),
                        )
                        if (selected_key and selected_key == value_key) or (selected_pair == value_pair and all(selected_pair)):
                            policy_allows_current = True
                            break
                except Exception:
                    policy_allows_current = False
            evidence_kind = _single_line(selected.get("evidence_kind"), 48).lower()
            fact_eligibility = _single_line(selected.get("fact_eligibility"), 48).lower()
            status = _single_line(selected.get("status"), 32).lower()
            # 睡眠/休息段是 Bot 的内部状态模拟，不是需要外部执行证据的日程动作：
            # 计划里的“睡觉”只表达“Bot 此刻该休息”的内部状态，不主张任何已发生
            # 的外部事实。若不在此豁免，上游 C3 证据认证门槛会让普通计划项
            # （evidence_kind/fact_eligibility 均为 none）在这里返回 None，
            # 睡眠状态机（_refresh_sleep_runtime_state）拿不到当前睡眠项，
            # 睡眠相位就会永远停在 awake。
            if not self._is_sleepy_plan_item(selected) and (
                not policy_allows_current
                or not (
                    evidence_kind in {"interaction", "tool_action", "external_record"}
                    and fact_eligibility in {"current_observed", "history_observed", ""}
                    and status in {"active", "completed", "partially_completed", ""}
                )
            ):
                runtime_getter = getattr(self, "_agenda_runtime_scene", None)
                if callable(runtime_getter):
                    try:
                        runtime = runtime_getter(now=self._environment_now())
                    except Exception:
                        runtime = None
                    if isinstance(runtime, dict):
                        return {
                            "time": self._minutes_to_hhmm(current_minutes),
                            "end": _single_line(runtime.get("valid_until"), 40),
                            "activity": _single_line(runtime.get("state"), 120),
                            "mood": "当前状态",
                            "message_seed": "",
                            "subject_actor_id": "bot_self",
                            "evidence_kind": "self_state_commit",
                            "fact_eligibility": "current_internal",
                            "materialization_state": "active",
                            "status": "active",
                        }
                return None
            plan_date = str(plan.get("date") or "").strip()
            if (
                plan_date
                and plan_date != _today_key()
                and current_minutes >= 24 * 60
                and selected_start is not None
                and self._is_sleepy_plan_item(selected)
            ):
                elapsed = max(0, current_minutes - selected_start)
                carried = dict(selected)
                carried["time"] = self._minutes_to_hhmm(current_minutes)
                runtime = {}
                state = self.data.get("daily_state", {})
                if isinstance(state, dict) and isinstance(state.get("sleep_runtime"), dict):
                    runtime = state.get("sleep_runtime", {})
                phase = str(runtime.get("phase") or "")
                if phase == "woken":
                    carried["activity"] = "夜里被消息轻轻叫醒，还半梦半醒地留着一点睡意。"
                    carried["mood"] = "刚醒，迷糊"
                    carried["message_seed"] = "像刚从睡里被叫醒；如果用户不继续聊，会慢慢把手机放下睡回去。"
                elif phase == "sleeping_again":
                    carried["activity"] = "刚才被叫醒过一下，现在又慢慢睡回去了。"
                    carried["mood"] = "重新睡着，安静"
                    carried["message_seed"] = "睡意重新接上了；再被唤起时会有一点断续的迷糊感。"
                elif elapsed >= 45:
                    carried["activity"] = "夜里还在睡着，睡意早已沉下去，睡眠正在安静延续。"
                    carried["mood"] = "睡着，安静"
                    carried["message_seed"] = "还在睡着。如果这时候被叫醒，会有点迷糊；没人继续打扰就会继续睡下去。"
                else:
                    carried["activity"] = "刚从前一晚的睡前片段进入休息，正在慢慢安静下来。"
                    carried["mood"] = _single_line(selected.get("mood"), 40) or "安静"
                    carried["message_seed"] = "正在收声准备睡，语气会更轻。"
                return carried
            return selected
        return None

    def _get_clock_plan_item_for_display(self, plan: dict[str, Any]) -> dict[str, Any] | None:
        """Pick the scheduled row covering now for UI only, without claiming it happened."""

        if not isinstance(plan, dict) or not self._is_plan_date_active(plan.get("date")):
            return None
        items = plan.get("items")
        if not isinstance(items, list):
            return None
        now_minutes = self._effective_plan_now_minutes(str(plan.get("date") or ""))
        if now_minutes is None:
            return None
        starts = self._normalized_plan_item_starts(items)
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            if self._normalize_schedule_lifecycle_status(item.get("lifecycle_status")) == "cancelled":
                continue
            start = starts[index] if index < len(starts) else None
            if start is None:
                continue
            next_start = next((value for value in starts[index + 1 :] if value is not None), None)
            end = self._plan_item_end_minutes(start, item, next_start=next_start)
            if start <= now_minutes < end:
                return item
        return None

    def _format_daily_plan(self, plan: dict[str, Any]) -> str:
        if not plan or not plan.get("items"):
            return "今天还没有日程。"
        source = "模型生成" if plan.get("source") == "llm" else "备用日程"
        lines = [
            f"{runtime_persona_setting(self, 'bot_name', '小星')} 今天的日程（{plan.get('date', _today_key())},{source}）："
        ]
        state = self.data.get("daily_state", {})
        if isinstance(state, dict) and state.get("date") == plan.get("date"):
            lines.append(
                f"状态：能量 {state.get('energy', 70)}/100｜情绪偏{state.get('mood_bias', '平稳')}｜{state.get('sleep', '睡眠平稳')}"
            )
        status_labels = {
            "planned": "计划中",
            "active": "进行中",
            "completed": "已完成",
            "changed": "已变更",
            "cancelled": "已取消",
            "deferred": "已顺延",
            "unknown": "未核实",
            "overridden": "已被新安排覆盖",
        }
        for index, item in enumerate(plan.get("items", [])):
            if not isinstance(item, dict):
                continue
            mood = f"｜{item.get('mood')}" if item.get("mood") else ""
            window = f"{item.get('time')}-{item.get('end')}" if item.get("end") else str(item.get("time") or "")
            lifecycle = self._plan_item_display_status(plan, item, index)
            status = status_labels.get(lifecycle, "计划中")
            lines.append(f"{window}｜{status} {item.get('activity')}{mood}")
        archive_warning = _memory_archive_warning(plan)
        if archive_warning:
            lines.append(archive_warning)
        return "\n".join(lines)

    @staticmethod
    def _detail_event_text(item: dict[str, Any], limit: int = 160) -> str:
        if not isinstance(item, dict):
            return ""
        for key in (
            "event",
            "content",
            "detail",
            "description",
            "text",
            "narrative",
            "body",
            "细化",
            "细化内容",
            "细化叙述",
            "事件",
            "主要事件",
        ):
            text = _single_line(item.get(key), limit)
            if text:
                return text
        return ""

    def _format_current_detail_view(self) -> str:
        plan = self.data.get("daily_plan", {})
        if not isinstance(plan, dict) or not plan.get("items"):
            return "今天还没有日程，所以也没有可看的当前细化。"
        enhanced = self.data.get("detail_enhanced_segments", {})
        if not isinstance(enhanced, dict):
            enhanced = {}
        segment = self._current_detail_segment_for_update() or self._pick_detail_segment(plan, enhanced)
        if not segment:
            return "当前还没有可用的细化结果。先让今天的日程段完成细化，或者手动执行一次“陪伴 重置细化”。"
        key = str(segment.get("key") or "")
        snapshot = enhanced.get(key) if key else None
        if not isinstance(snapshot, dict):
            return "当前时间段还没有落地的细化内容。可以先执行一次“陪伴 重置细化”。"
        snapshot = deepcopy(snapshot)
        self._sanitize_detail_enhanced_segments_inplace({"current": snapshot})

        item = segment.get("item") if isinstance(segment, dict) else {}
        start_text = self._minutes_to_hhmm(_safe_int(segment.get("start"), 0))
        end_text = self._minutes_to_hhmm(_safe_int(segment.get("end"), 0))
        lines = [
            f"当前细化时段：{start_text}-{end_text}",
            f"对应日程：{_single_line((item or {}).get('activity'), 120)}",
        ]
        mood = _single_line((item or {}).get("mood"), 24)
        if mood:
            lines.append(f"日程情绪：{mood}")

        state_variables = snapshot.get("state_variables", [])
        if isinstance(state_variables, list) and state_variables:
            lines.append("状态变量：")
            for variable in state_variables[:8]:
                if not isinstance(variable, dict):
                    continue
                name = _single_line(variable.get("name"), 32)
                value = _single_line(variable.get("value"), 60)
                note = _single_line(variable.get("note"), 80)
                if name and value:
                    lines.append(f"- {name}: {value}" + (f"（{note}）" if note else ""))

        presence = snapshot.get("presence_status")
        if isinstance(presence, dict):
            mode = _single_line(presence.get("mode"), 24)
            reason = _single_line(presence.get("reason"), 80)
            if mode and mode != "unchanged":
                lines.append("QQ状态表现：")
                lines.append(f"- {mode}" + (f"｜{reason}" if reason else ""))

        interaction_updates = snapshot.get("interaction_updates", [])
        if isinstance(interaction_updates, list) and interaction_updates:
            update_lines: list[str] = []
            for update in interaction_updates[-4:]:
                if not isinstance(update, dict):
                    continue
                if _single_line(update.get("source_role"), 20) != "owner":
                    continue
                at = _single_line(update.get("at"), 8)
                user_text = _single_line(update.get("user_text"), 80)
                intensity = _single_line(update.get("intensity"), 12)
                reaction = _single_line(update.get("reaction"), 120)
                state_updates = update.get("state_updates")
                state_text = ""
                if isinstance(state_updates, list) and state_updates:
                    state_text = "；".join(_single_line(item, 60) for item in state_updates if _single_line(item, 60))
                if reaction or user_text:
                    prefix = f"- {at} " if at else "- "
                    parts = [
                        f"用户：{user_text}" if user_text else "",
                        f"强度：{intensity}" if intensity else "",
                        reaction,
                        state_text,
                    ]
                    update_lines.append(prefix + "｜".join(part for part in parts if part))
            if update_lines:
                lines.append("用户介入后的局部更新：")
                lines.extend(update_lines)

        today_events = snapshot.get("today_events", [])
        scoped_today_events = self._filter_snapshot_items_to_segment(today_events, segment)
        if scoped_today_events:
            lines.append("细化内容：")
            for detail_event in scoped_today_events[:8]:
                if not isinstance(detail_event, dict):
                    continue
                window = _single_line(detail_event.get("window"), 24)
                event_text = self._detail_event_text(detail_event, 160)
                mood_text = _single_line(detail_event.get("mood"), 24)
                if event_text:
                    tail = f"｜{mood_text}" if mood_text else ""
                    lines.append(f"- {window}｜{event_text}{tail}")
        else:
            summary = _single_line(snapshot.get("summary"), 160)
            if summary and summary not in {"这一段按原日程慢慢推进。", "这一段按原日程慢慢推进"}:
                lines.append(f"细化内容：{summary}")
            else:
                lines.append("细化内容：当前没有生成出可展示的细化正文。")

        proactive_events = snapshot.get("proactive_events", [])
        if isinstance(proactive_events, list) and proactive_events:
            scoped_proactive_events = self._filter_snapshot_items_to_segment(proactive_events, segment)
            if scoped_proactive_events:
                lines.append("这一段的主动契机：")
            for proactive_event in scoped_proactive_events[:10]:
                if not isinstance(proactive_event, dict):
                    continue
                window = _single_line(proactive_event.get("window"), 24)
                reason = _single_line(proactive_event.get("reason"), 24)
                action = _single_line(proactive_event.get("action"), 24) or "message"
                topic = _single_line(proactive_event.get("topic"), 48)
                motive = _single_line(proactive_event.get("motive"), 80)
                why = _single_line(proactive_event.get("why"), 100)
                scene = _single_line(proactive_event.get("scene"), 60)
                tone = _single_line(proactive_event.get("tone"), 24)
                impulse = _single_line(proactive_event.get("impulse"), 80)
                lines.append(f"- {window}｜{reason}｜{action}｜{topic or motive or '（无话题）'}")
                if why:
                    lines.append(f"  why：{why}")
                if motive:
                    lines.append(f"  motive：{motive}")
                meta_bits = []
                if scene:
                    meta_bits.append(f"scene={scene}")
                if tone:
                    meta_bits.append(f"tone={tone}")
                if impulse:
                    meta_bits.append(f"impulse={impulse}")
                if meta_bits:
                    lines.append("  " + "｜".join(meta_bits))
                chain = proactive_event.get("chain")
                if isinstance(chain, list) and chain:
                    lines.append("  chain：")
                    for step in chain[:4]:
                        if not isinstance(step, dict):
                            continue
                        kind = _single_line(step.get("kind"), 24)
                        after_minutes = _safe_int(step.get("after_minutes"), 0, 0)
                        step_reason = _single_line(step.get("reason"), 24)
                        step_topic = _single_line(step.get("topic"), 48)
                        step_motive = _single_line(step.get("motive"), 80)
                        step_tone = _single_line(step.get("tone"), 24)
                        extra = []
                        if after_minutes > 0:
                            extra.append(f"{after_minutes} 分钟后")
                        if step_reason:
                            extra.append(step_reason)
                        if step_topic:
                            extra.append(step_topic)
                        if step_tone:
                            extra.append(f"tone={step_tone}")
                        if step_motive:
                            extra.append(f"motive={step_motive}")
                        lines.append(f"    - {kind}" + (f"｜{'｜'.join(extra)}" if extra else ""))

        if len(lines) <= 4:
            lines.append("这段目前还比较空，说明细化结果里还没长出太多东西。")
        return "\n".join(lines)

    def _filter_snapshot_items_to_segment(
        self,
        raw_items: Any,
        segment: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if not isinstance(raw_items, list) or not isinstance(segment, dict):
            return []
        start = _safe_int(segment.get("start"), 0)
        end = _safe_int(segment.get("end"), self._segment_end_minutes(start, segment.get("item")))
        if end <= start:
            end += 24 * 60
        kept: list[dict[str, Any]] = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            if self._normalize_schedule_lifecycle_status(item.get("lifecycle_status")) == "cancelled":
                continue
            item_start, item_end = self._parse_window_minutes(str(item.get("window") or ""))
            if item_start is None or item_end is None:
                continue
            candidates = [(item_start, item_end)]
            if item_end < item_start:
                candidates = [(item_start, item_end + 24 * 60)]
            if item_start < start and end > 24 * 60:
                candidates.append((item_start + 24 * 60, item_end + 24 * 60))
            if any(candidate_start >= start and candidate_end <= end for candidate_start, candidate_end in candidates):
                kept.append(item)
        return kept

    def _format_current_detail_brief(self) -> str:
        plan = self.data.get("daily_plan", {})
        if not isinstance(plan, dict) or not plan.get("items"):
            return "今天还没有日程，所以没有可细化的时间段。"
        enhanced = self.data.get("detail_enhanced_segments", {})
        if not isinstance(enhanced, dict):
            enhanced = {}
        segment = self._current_detail_segment_for_update() or self._pick_detail_segment(plan, enhanced)
        if not segment:
            return "当前还没有可用的细化结果。"
        key = str(segment.get("key") or "")
        snapshot = enhanced.get(key) if key else None
        if not isinstance(snapshot, dict):
            return "当前时间段还没有落地的细化内容。"
        snapshot = deepcopy(snapshot)
        self._sanitize_detail_enhanced_segments_inplace({"current": snapshot})

        item = segment.get("item") if isinstance(segment, dict) else {}
        start_text = self._minutes_to_hhmm(_safe_int(segment.get("start"), 0))
        end_text = self._minutes_to_hhmm(_safe_int(segment.get("end"), 0))
        lines = [
            f"{start_text}-{end_text}｜{_single_line((item or {}).get('activity'), 80)}",
        ]
        summary = _single_line(snapshot.get("summary"), 140)
        if summary:
            lines.append(summary)

        today_events = snapshot.get("today_events", [])
        if isinstance(today_events, list) and today_events:
            for detail_event in today_events[:3]:
                if not isinstance(detail_event, dict):
                    continue
                window = _single_line(detail_event.get("window"), 18)
                event_text = self._detail_event_text(detail_event, 120)
                mood_text = _single_line(detail_event.get("mood"), 20)
                if event_text:
                    lines.append(f"- {window} {event_text}" + (f"｜{mood_text}" if mood_text else ""))

        interaction_updates = snapshot.get("interaction_updates", [])
        if isinstance(interaction_updates, list) and interaction_updates:
            latest = next(
                (
                    item
                    for item in reversed(interaction_updates)
                    if isinstance(item, dict) and _single_line(item.get("source_role"), 20) == "owner"
                ),
                None,
            )
            if isinstance(latest, dict):
                user_text = _single_line(latest.get("user_text"), 60)
                reaction = _single_line(latest.get("reaction"), 100)
                if reaction or user_text:
                    lines.append("局部更新：" + "｜".join(part for part in (f"用户：{user_text}" if user_text else "", reaction) if part))

        return "\n".join(lines)

    def _debug_tick_skip(self, user_id: str, reason: str, *, prefix: str = "跳过") -> None:
        reason_text = _single_line(reason, 120) or "未知原因"
        should_record = prefix != "跳过" or reason_text not in {"未到候选主动时间", "已安排下一次候选主动时间"}
        if should_record:
            try:
                current = self._get_user(str(user_id or ""))
                current["last_proactive_skip_at"] = _now_ts()
                current["last_proactive_skip_reason"] = reason_text
                current["last_proactive_skip_prefix"] = _single_line(prefix, 20)
            except Exception:
                pass
        if prefix == "跳过":
            return
        key = f"{prefix}:{user_id}"
        now = _now_ts()
        cache = getattr(self, "_tick_skip_log_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            self._tick_skip_log_cache = cache
        last_ts = _safe_float(cache.get(key), 0)
        if now - last_ts < 1800:
            return
        cache[key] = now
        if len(cache) > 300:
            cutoff = now - 3600
            for old_key, ts in list(cache.items()):
                if _safe_float(ts, 0) < cutoff:
                    cache.pop(old_key, None)
        logger.debug(f"{prefix} {user_id}: {reason_text}")


    async def _tick(self):
        try:
            last_poll = getattr(self, "_last_body_monitor_poll_ts", 0.0)
            poll_interval = _safe_float(
                getattr(self, "_body_monitor_poll_interval", 90.0),
                90.0,
                30.0,
                600.0,
            )
            if _now_ts() - last_poll >= poll_interval:
                await self._pull_body_monitor_candidates()
                self._last_body_monitor_poll_ts = _now_ts()
        except Exception as exc:
            logger.warning(
                "Body Monitor 事件拉取失败，本轮继续执行其他主动任务: %s",
                _single_line(exc, 160),
            )
        # HDSI 生命周期侧车在锁内只做标记，锁外执行（避免锁重入）。
        hdsi_sidecar_pending = False
        async with self._data_lock:
            runtime = self.data.setdefault("proactive_runtime", {})
            if isinstance(runtime, dict):
                runtime["last_tick_started_at"] = _now_ts()
                runtime["last_tick_error"] = ""
            stale_timer_count = self._expire_stale_official_llm_timers_locked()
            if stale_timer_count:
                self._save_data_sync(sections={"users"})
            if self._proactive_generation_disabled():
                changed = False
                users_root = self.data.get("users") if isinstance(self.data.get("users"), dict) else {}
                for user in users_root.values():
                    if isinstance(user, dict):
                        changed = self._suspend_user_proactive_generation(user) or changed
                pool = self.data.get("proactive_candidate_pool")
                if isinstance(pool, list):
                    for candidate in pool:
                        if not isinstance(candidate, dict):
                            continue
                        status = _single_line(candidate.get("status"), 24).lower()
                        if status in {"", "accepted", "deferred", "queued", "pending", "unknown"}:
                            candidate["status"] = "blocked"
                            candidate["note"] = "每日主动上限为 0，主动生成已停止"
                            candidate["updated_ts"] = _now_ts()
                            changed = True
                if isinstance(runtime, dict):
                    runtime["generation_disabled"] = True
                    runtime["generation_disabled_reason"] = "max_daily_messages=0"
                    runtime["last_tick_finished_at"] = _now_ts()
                if changed:
                    self._save_proactive_tick_state(
                        {"users", "proactive_candidate_pool", "proactive_runtime"}
                    )
                # HDSI life progression runs as an opt-in sidecar
                # 锁内只设标记：HDSI 生命周期侧车会重入 _data_lock，
                # 必须在释放锁之后再执行（见锁外调用）。
                hdsi_sidecar_pending = True
                return
            if isinstance(runtime, dict):
                runtime["generation_disabled"] = False
                runtime["generation_disabled_reason"] = ""
            if self._maybe_schedule_bilibili_video_share():
                self._save_data_sync(
                    sections={
                        "users",
                        "proactive_candidate_pool",
                        "external_event_pool",
                        "external_event_self_link_cache",
                    }
                )
        if hdsi_sidecar_pending:
            # HDSI 生命周期侧车必须在 _data_lock 之外执行：
            # 其调用链会重新获取同一把非可重入 asyncio.Lock。
            await self._run_hdsi_life_tick_sidecar()
            return
        users = list(self.data.get("users", {}).items())

        for user_id, user in users:
            await self._tick_user(user_id, user)

        await self._run_proactive_maintenance_tasks()
        # HDSI life progression is an opt-in sidecar.
        await self._run_hdsi_life_tick_sidecar()
        async with self._data_lock:
            runtime = self.data.setdefault("proactive_runtime", {})
            if isinstance(runtime, dict):
                runtime["last_tick_finished_at"] = _now_ts()
