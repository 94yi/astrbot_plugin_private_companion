# -*- coding: utf-8 -*-
"""诊断 / 故障排查 / 统计 域页面 API。

由 tools/split_mixin_domain.py 从 page_api.py 机械抽取（45 个方法 / 3066 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApi）。

"""
from __future__ import annotations

import asyncio
import functools
import time
import re
import hashlib
import secrets
import uuid
from copy import copy, deepcopy
from typing import Any, Mapping
from quart import send_file
from .page_api_shared import _page_api_host
from .conversation_prompt_section import (
    PromptRenderMode,
    prompt_section,
    render_prompt_sections,
)
from .diagnostic_envelope import DIAGNOSTIC_ENVELOPE_VERSION, diagnostic_test_id, normalize_diagnostic_result
from .helpers import _MISSING, _flat_get, _normalize_timezone_name, _normalize_timezone_setting, _path_text, _redact_outbound_secrets, _safe_int, _set_into_config, _strip_internal_message_blocks, _text_looks_garbled, _text_similarity, _today_key, normalize_bot_relationship_cards
from .persona_config import runtime_persona_setting
from .planning import evaluate_daily_plan_quality, generate_daily_plan, generate_detail_enhancement
from .logging_util import get_module_logger

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


logger = get_module_logger(__name__)


def _render_page_background_prompt(
    *,
    key: str,
    title: str,
    content: str,
) -> str:
    return render_prompt_sections(
        [
            prompt_section(
                key=key,
                title=title,
                source="page_api",
                content=content,
            )
        ],
        mode=PromptRenderMode.BODY_ONLY,
    )


class PrivateCompanionPageApiDiagnosticsMixin:
    """诊断 / 故障排查 / 统计 域（从 PrivateCompanionPageApi 拆出）。"""

    def _build_troubleshooting_proactive_summary(
        self,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        """Resolve relationship snapshots once before rendering diagnostics."""

        users = data.get("users") if isinstance(data.get("users"), dict) else {}
        resolved_users: dict[str, Any] = {}
        for user_id, raw_user in users.items():
            if not isinstance(raw_user, dict):
                resolved_users[user_id] = raw_user
                continue
            user = dict(raw_user)
            try:
                snapshot_getter = getattr(
                    self.plugin,
                    "_req041_relationship_snapshot_view",
                    None,
                )
                snapshot = (
                    snapshot_getter(user, source="troubleshooting_summary")
                    if callable(snapshot_getter)
                    else user
                )
                if isinstance(snapshot, dict):
                    user = dict(snapshot)
            except Exception:
                user = dict(raw_user)
            user["_req041_relationship_snapshot_resolved"] = True
            resolved_users[user_id] = user
        scoped = dict(data)
        scoped["users"] = resolved_users
        return self._proactive_task_summary(scoped)
    def _troubleshooting_proactive_wakeup_tasks(self) -> dict[str, asyncio.Task[Any]]:
        tasks = getattr(self.plugin, "_troubleshooting_proactive_wakeup_tasks", None)
        if not isinstance(tasks, dict):
            tasks = {}
            self.plugin._troubleshooting_proactive_wakeup_tasks = tasks
        return tasks
    def _cancel_troubleshooting_proactive_wakeup(self, user_id: str) -> bool:
        tasks = self._troubleshooting_proactive_wakeup_tasks()
        task = tasks.pop(str(user_id or ""), None)
        if not isinstance(task, asyncio.Task) or task.done():
            return False
        task.cancel()
        return True
    def _schedule_troubleshooting_proactive_wakeup(
        self,
        user_id: str,
        scheduled_ts: float,
    ) -> asyncio.Task[Any] | None:
        user_key = str(user_id or "").strip()
        kicker = getattr(self.plugin, "_kick_proactive_loop_once", None)
        if not user_key or not callable(kicker):
            return None
        tasks = self._troubleshooting_proactive_wakeup_tasks()
        existing = tasks.get(user_key)
        if isinstance(existing, asyncio.Task) and not existing.done():
            return existing

        async def wake_when_due() -> None:
            try:
                await asyncio.sleep(max(0.0, float(scheduled_ts) - time.time()))
                await kicker()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "主动消息链路测试到点唤醒失败: user=%s error=%s",
                    self._single_line(user_key, 80),
                    self._single_line(exc, 160),
                )
            finally:
                current = tasks.get(user_key)
                if current is asyncio.current_task():
                    tasks.pop(user_key, None)

        task = self._create_page_background_task(
            wake_when_due(),
            label=f"troubleshooting_proactive_{user_key[:40]}",
        )
        if task is None:
            return None
        tasks[user_key] = task
        return task
    def _overview_section_value(
        self,
        section: str,
        builder: Any,
        *,
        fallback: Any = None,
        degraded_sections: list[str] | None = None,
    ) -> Any:
        """Keep one optional dashboard section from taking down the overview."""
        try:
            return builder()
        except Exception as exc:
            section_name = self._single_line(section, 80) or "unknown"
            if isinstance(degraded_sections, list) and section_name not in degraded_sections:
                degraded_sections.append(section_name)
            logger.warning(
                "总览区块读取失败: section=%s error=%s",
                section_name,
                self._single_line(exc, 200),
                exc_info=True,
            )
            return deepcopy(fallback)
    @_multi_persona_page_context
    async def get_overview(self) -> dict[str, Any]:
        start = time.perf_counter()
        degraded_sections: list[str] = []
        try:
            async with self.plugin._data_lock:
                data = self._overview_section_value(
                    "data_snapshot",
                    lambda: self._overview_data_snapshot_locked(self.plugin.data),
                    fallback={"users": {}, "groups": {}},
                    degraded_sections=degraded_sections,
                )
                reaction_expression = self._overview_section_value(
                    "reaction_expression",
                    lambda: self._reaction_expression_runtime_summary(self.plugin.data),
                    fallback={},
                    degraded_sections=degraded_sections,
                )
                token_stats = self._overview_section_value(
                    "token_stats",
                    lambda: self._token_overview_payload(
                        self.plugin.data.get("token_usage", {}),
                        self.plugin.data.get("balance_awareness", {}),
                    ),
                    fallback={},
                    degraded_sections=degraded_sections,
                )
            plan = deepcopy(data.get("daily_plan"))
            if isinstance(plan, dict):
                data["daily_plan"] = plan
                plan_sanitizer = getattr(self.plugin, "_sanitize_daily_plan_inplace", None)
                if callable(plan_sanitizer):
                    self._overview_section_value(
                        "daily_plan_sanitizer",
                        lambda: plan_sanitizer(plan),
                        fallback=False,
                        degraded_sections=degraded_sections,
                    )
                if isinstance(plan.get("items"), list) and not isinstance(plan.get("quality"), dict):
                    quality = self._overview_section_value(
                        "daily_plan_quality",
                        lambda: evaluate_daily_plan_quality(self.plugin, plan.get("items")),
                        fallback=None,
                        degraded_sections=degraded_sections,
                    )
                    if isinstance(quality, dict):
                        plan["quality"] = quality
            enhanced = deepcopy(data.get("detail_enhanced_segments"))
            if isinstance(enhanced, dict):
                data["detail_enhanced_segments"] = enhanced
                detail_sanitizer = getattr(self.plugin, "_sanitize_detail_enhanced_segments_inplace", None)
                if callable(detail_sanitizer):
                    self._overview_section_value(
                        "detail_segments_sanitizer",
                        lambda: detail_sanitizer(enhanced),
                        fallback=False,
                        degraded_sections=degraded_sections,
                    )
            story = deepcopy(data.get("daily_story_plan"))
            if isinstance(story, dict):
                data["daily_story_plan"] = story
                story_sanitizer = getattr(self.plugin, "_sanitize_story_plan_social_facts_inplace", None)
                if callable(story_sanitizer):
                    self._overview_section_value(
                        "story_plan_sanitizer",
                        lambda: story_sanitizer(story),
                        fallback=False,
                        degraded_sections=degraded_sections,
                    )
            users = data.get("users") if isinstance(data.get("users"), dict) else {}
            groups = data.get("groups") if isinstance(data.get("groups"), dict) else {}
            enabled_users = len(users)
            proactive_enabled_users = sum(
                1
                for item in users.values()
                if isinstance(item, dict)
                and (
                    item.get("proactive_private_enabled") is True
                    or (
                        isinstance(item.get("unified_profile_capabilities"), dict)
                        and item["unified_profile_capabilities"].get("proactive_private_enabled") is True
                    )
                )
            )
            visible_groups = {
                str(group_id): group
                for group_id, group in groups.items()
                if isinstance(group, dict) and not self._looks_like_member_shadow_group(str(group_id), group)
            }
            enabled_groups = sum(1 for item in visible_groups.values() if isinstance(item, dict) and item.get("enabled", True))
            group_access_mode = str(getattr(self.plugin, "group_access_mode", "whitelist") or "whitelist")
            group_whitelist = self._overview_section_value(
                "group_whitelist",
                lambda: list(self.plugin._configured_group_ids()),
                fallback=[],
                degraded_sections=degraded_sections,
            )
            group_blacklist = self._overview_section_value(
                "group_blacklist",
                lambda: list(self.plugin._configured_group_blacklist_ids()),
                fallback=[],
                degraded_sections=degraded_sections,
            )
            effective_group_count = sum(
                1
                for group_id, item in visible_groups.items()
                if isinstance(item, dict)
                and item.get("enabled", True)
                and self.plugin._group_allowed_by_access_mode(str(group_id))
            )
            group_access_warning = ""
            if bool(getattr(self.plugin, "enable_group_companion", False)) and group_access_mode == "whitelist" and not group_whitelist:
                group_access_warning = "群聊观察已开启，但白名单为空，当前不会接收任何群聊观察。"

            def section(name: str, builder: Any, fallback: Any = None) -> Any:
                return self._overview_section_value(
                    name,
                    builder,
                    fallback=fallback,
                    degraded_sections=degraded_sections,
                )

            try:
                bookshelf = await self._bookshelf_summary(data, unlocked=False)
            except Exception as exc:
                degraded_sections.append("bookshelf")
                logger.warning(
                    "总览区块读取失败: section=bookshelf error=%s",
                    self._single_line(exc, 200),
                    exc_info=True,
                )
                bookshelf = {"available": False, "degraded": True, "reason": "summary_unavailable"}
            proactive_tasks = await self._proactive_task_summary_async(data)
            if proactive_tasks.get("degraded"):
                degraded_sections.append("proactive_tasks")

            payload = {
                "plugin": {
                    "enabled": bool(getattr(self.plugin, "enabled", False)),
                    "bot_name": runtime_persona_setting(
                        self.plugin,
                        "bot_name",
                        getattr(self.plugin, "bot_name", ""),
                    ),
                    "data_file": getattr(self.plugin, "data_file", ""),
                    "storage_backend": getattr(self.plugin, "storage_backend", "json"),
                    "storage_sqlite_path": getattr(
                        self.plugin,
                        "storage_sqlite_effective_path",
                        getattr(self.plugin, "storage_sqlite_path", ""),
                    ),
                    "enable_store_control_tag_sanitization": bool(
                        getattr(self.plugin, "enable_store_control_tag_sanitization", True)
                    ),
                    "data_version": data.get("version"),
                },
                "primary_store_ownership": deepcopy(
                    data.get("primary_store_ownership")
                    if isinstance(data.get("primary_store_ownership"), dict)
                    else {}
                ),
                "companion_plugins": section("companion_plugins", self._companion_plugins_summary, {}),
                "private": {
                    "user_count": len(users),
                    "enabled_user_count": enabled_users,
                    "proactive_enabled_user_count": proactive_enabled_users,
                    "require_opt_in": bool(getattr(self.plugin, "require_private_opt_in", True)),
                    "admin_ids": section(
                        "admin_ids",
                        lambda: list(self.plugin._configured_admin_ids()),
                        [],
                    ) if hasattr(self.plugin, "_configured_admin_ids") else [],
                    "target_user_ids": section(
                        "target_user_ids",
                        lambda: list(self.plugin._configured_target_ids()),
                        [],
                    ) if hasattr(self.plugin, "_configured_target_ids") else [],
                    "relationship_owner_ids": section(
                        "relationship_owner_ids",
                        lambda: list(self.plugin._relationship_owner_user_ids()),
                        [],
                    ) if hasattr(self.plugin, "_relationship_owner_user_ids") else [],
                    "max_daily_messages": getattr(self.plugin, "max_daily_messages", 0),
                    "idle_minutes": getattr(self.plugin, "idle_minutes", 0),
                    "min_interval_minutes": getattr(self.plugin, "min_interval_minutes", 0),
                },
                "group": {
                    "enabled": bool(getattr(self.plugin, "enable_group_companion", False)),
                    "group_count": len(visible_groups),
                    "enabled_group_count": enabled_groups,
                    "effective_group_count": effective_group_count,
                    "shadow_group_count": max(0, len(groups) - len(visible_groups)),
                    "access_mode": group_access_mode,
                    "access_warning": group_access_warning,
                    "whitelist": group_whitelist,
                    "blacklist": group_blacklist,
                    "interjection_enabled": bool(getattr(self.plugin, "enable_group_interjection", False)),
                    "repeat_follow_enabled": bool(getattr(self.plugin, "enable_group_repeat_follow", False)),
                },
                "platform_adaptation": section(
                    "platform_adaptation",
                    self.plugin._platform_adaptation_overview,
                    {},
                ) if hasattr(self.plugin, "_platform_adaptation_overview") else {},
                "features": section("features", self._feature_flags, {}),
                "reaction_expression": reaction_expression,
                "proactive_intensity": section("proactive_intensity", self._proactive_intensity_summary, {}),
                "proactive_only": section("proactive_only", self._proactive_only_mode_snapshot, {}),
                "proactive_chat": section("proactive_chat", lambda: self._proactive_chat_summary(data), {}),
                "body_monitor_integration": section("body_monitor_integration", self._body_monitor_integration_summary, {}),
                "expression_scope": section("expression_scope", lambda: self._expression_learning_scope_summary(data), {}),
                "providers": section("providers", self._provider_settings, {}),
                "settings": section("settings", self._runtime_settings, {}),
                "deepseek_peak_routing": section("deepseek_peak_routing", self._deepseek_peak_routing_summary, {}),
                "cache": section("cache", lambda: self._cache_summary(data), {}),
                "livingmemory": section("livingmemory", self._livingmemory_summary, {}),
                "screen_companion": section("screen_companion", lambda: self._screen_companion_summary(data), {}),
                "knowledge": section("knowledge", self.plugin._roleplay_knowledge_summary, {}),
                "worldbook": section("worldbook", lambda: self._worldbook_summary(data), {}),
                "proactive_candidates": section("proactive_candidates", lambda: self._proactive_candidate_summary(data), {}),
                "proactive_tasks": proactive_tasks,
                "message_debounce": section("message_debounce", lambda: self._message_debounce_summary(data), {}),
                "bilibili": section("bilibili", lambda: self._bilibili_summary(data), {}),
                "news": section("news", lambda: self._news_summary(data), {}),
                "web_exploration": section("web_exploration", lambda: self._web_exploration_summary(data), {}),
                "qzone": section("qzone", lambda: self._qzone_summary(data), {}),
                "reading_archive": section("reading_archive", lambda: self._reading_archive_summary(data), {}),
                "creative": section("creative", lambda: self._creative_summary(data), {}),
                "bookshelf": bookshelf,
                "skill_growth": section("skill_growth", lambda: self._skill_growth_summary(data), {}),
                "personal_goals": section("personal_goals", lambda: self._personal_goal_summary(data), {}),
                "food_menu": section("food_menu", lambda: self._food_menu_summary(data), {}),
                "external_abilities": section("external_abilities", lambda: self._external_ability_summary(data), {}),
                "life_observation": section("life_observation", lambda: self._life_observation_summary(data), {}),
                "daily_state": section("daily_state", lambda: self._daily_state_summary(data.get("daily_state")), {}),
                "daily_timeline": section("daily_timeline", lambda: self._daily_timeline_summary(data), {}),
                "daily_outfit": section("daily_outfit", lambda: self._daily_outfit_summary(data), {}),
                "token_stats": token_stats,
                "multi_persona": section(
                    "multi_persona",
                    getattr(self.plugin, "_multi_persona_status", lambda: {"enabled": False}),
                    {"enabled": False},
                ),
                "req041": section("req041", self._req041_runtime_summary, {}),
                "overview_health": {
                    "degraded": bool(degraded_sections),
                    "sections": degraded_sections,
                },
            }
            if not payload.get("multi_persona", {}).get("enabled"):
                payload.pop("multi_persona", None)
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            if elapsed_ms > 1200:
                logger.warning(
                    "总览接口耗时较高: elapsed=%sms users=%s groups=%s",
                    elapsed_ms,
                    len(users),
                    len(visible_groups),
                )
            return self._ok(payload)
        except Exception as exc:
            logger.error(f"获取总览失败: {exc}", exc_info=True)
            return self._exception_error("获取总览失败")
    def _token_overview_payload(self, usage: Any, balance_state: Any = None) -> dict[str, Any]:
        if not isinstance(usage, dict):
            usage = {}
        today_key = _today_key()
        totals = self._token_bucket(usage.get("totals"))
        today_bucket = usage.get("by_day", {}).get(today_key, {}) if isinstance(usage.get("by_day"), dict) else {}
        today_total_tokens = self._int(today_bucket.get("total_tokens")) if isinstance(today_bucket, dict) else 0
        exempt_by_day = usage.get("budget_exempt_by_day") if isinstance(usage.get("budget_exempt_by_day"), dict) else {}
        today_exempt_bucket = exempt_by_day.get(today_key, {}) if isinstance(exempt_by_day, dict) else {}
        today_exempt_tokens = self._int(today_exempt_bucket.get("total_tokens")) if isinstance(today_exempt_bucket, dict) else 0
        if today_exempt_tokens <= 0:
            by_day_task = usage.get("by_day_task") if isinstance(usage.get("by_day_task"), dict) else {}
            today_tasks = by_day_task.get(today_key, {}) if isinstance(by_day_task, dict) else {}
            if isinstance(today_tasks, dict):
                is_exempt_task = getattr(self.plugin, "_is_llm_budget_exempt_task", None)
                today_exempt_tokens = sum(
                    self._int(bucket.get("total_tokens"))
                    for task, bucket in today_tasks.items()
                    if isinstance(bucket, dict)
                    and (
                        (callable(is_exempt_task) and is_exempt_task(task))
                        or (not callable(is_exempt_task) and str(task) in {"proactive_framework", "voice_framework"})
                    )
                )
        today_tokens = max(0, today_total_tokens - today_exempt_tokens)
        daily_limit = self._int(getattr(self.plugin, "daily_token_limit", 0))
        soft_limit = self._int(getattr(self.plugin, "daily_token_soft_limit", 0))
        soft_enabled = bool(getattr(self.plugin, "enable_daily_token_soft_limit", True))
        budget_skips = usage.get("budget_skips", {}) if isinstance(usage.get("budget_skips"), dict) else {}
        today_skips = budget_skips.get(today_key, {}) if isinstance(budget_skips, dict) else {}
        budget = {
            "day": today_key,
            "limit": daily_limit,
            "soft_limit": soft_limit,
            "soft_enabled": soft_enabled,
            "soft_active": bool(soft_enabled and soft_limit > 0 and today_tokens >= soft_limit),
            "used": today_tokens,
            "total_used": today_total_tokens,
            "exempt_used": today_exempt_tokens,
            "remaining": max(0, daily_limit - today_tokens) if daily_limit > 0 else None,
            "soft_remaining": max(0, soft_limit - today_tokens) if soft_enabled and soft_limit > 0 else None,
            "ratio": round(today_tokens / daily_limit, 4) if daily_limit > 0 else 0,
            "soft_ratio": round(today_tokens / soft_limit, 4) if soft_enabled and soft_limit > 0 else 0,
            "exceeded": bool(daily_limit > 0 and today_tokens >= daily_limit),
            "deferred_calls": (
                self._int(today_skips.get("daily_token_soft_limit_deferred"))
                + self._int(today_skips.get("maintenance_token_saver_deferred"))
            )
            if isinstance(today_skips, dict)
            else 0,
            "skipped_calls": self._int(today_skips.get("count")) if isinstance(today_skips, dict) else 0,
        }
        payload = {
            "updated_at": self._single_line(usage.get("updated_at"), 24),
            "totals": totals,
            "budget": budget,
            "balance": self._balance_status_payload(balance_state),
            "memory_plugin": self._token_memory_plugin_payload(self._memory_plugin_token_usage_raw()),
            "together_plugin": self._token_memory_plugin_payload(self._safe_together_plugin_token_usage_raw()),
            "partial": True,
        }
        self._attach_multi_persona_token_stats(payload)
        return payload
    def _overview_data_snapshot_locked(self, raw_data: Any) -> dict[str, Any]:
        """Build a light read-only snapshot for the dashboard.

        The full data store can contain large image/news/bookshelf/history blobs. The
        overview only needs recent slices and counters, so avoid deepcopying the
        entire store while holding the data lock.
        """
        if not isinstance(raw_data, dict):
            return {}

        def shallow_dict(value: Any) -> dict[str, Any]:
            return dict(value) if isinstance(value, dict) else {}

        def list_tail(value: Any, limit: int) -> list[Any]:
            if not isinstance(value, list):
                return []
            if limit <= 0:
                return []
            return list(value[-limit:])

        scalar_user_keys = (
            "enabled",
            "manual_enabled",
            "manual_disabled",
            "nickname",
            "style",
            "umo",
            "relationship_role",
            "last_seen",
            "last_sent",
            "proactive_chat_bridge_last_sent_at",
            "sent_today",
            "sent_day",
            "last_proactive_skip_at",
            "last_proactive_skip_reason",
            "last_proactive_skip_prefix",
            "proactive_daily_limit",
            "proactive_idle_minutes",
            "proactive_min_interval_minutes",
            "photo_daily_limit",
            "screen_peek_daily_limit",
            "poke_daily_limit",
            "proactive_boundary_note",
            "inbound_count",
            "reply_count",
            "proactive_sent_count",
            "relationship_score",
            "planned_proactive_reason",
            "planned_proactive_action",
            "planned_proactive_source",
            "planned_proactive_topic",
            "planned_proactive_motive",
            "planned_proactive_impulse_id",
            "planned_candidate_id",
            "planned_proactive_semantic_kind",
            "planned_proactive_anchor_type",
            "planned_proactive_semantic_score",
            "planned_proactive_semantic_note",
            "planned_proactive_window_start_at",
            "planned_proactive_best_until_at",
            "planned_proactive_expire_at",
            "planned_proactive_window_timezone",
            "planned_proactive_model_judge_at",
            "next_proactive_at",
            "proactive_sending",
            "last_proactive_hesitation_at",
            "last_proactive_hesitation_note",
        )

        def user_snapshot(value: Any) -> dict[str, Any]:
            if not isinstance(value, dict):
                return {}
            snapshot = {key: value.get(key) for key in scalar_user_keys if key in value}
            for key in (
                "relationship_state",
                "persona_relationship",
                "llm_timer_event",
                "planned_proactive_model_judge_result",
                "proactive_afterglow",
            ):
                raw = value.get(key)
                if isinstance(raw, dict):
                    snapshot[key] = dict(raw)
            aliases = value.get("alias_user_ids")
            if isinstance(aliases, list):
                snapshot["alias_user_ids"] = list(aliases[:8])
            hesitations = value.get("recent_proactive_hesitations")
            if isinstance(hesitations, list):
                snapshot["recent_proactive_hesitations"] = [
                    dict(item) for item in hesitations[-3:] if isinstance(item, dict)
                ]
            return snapshot

        data: dict[str, Any] = {
            "version": raw_data.get("version"),
            "users": {str(key): user_snapshot(value) for key, value in raw_data.get("users", {}).items() if isinstance(value, dict)}
            if isinstance(raw_data.get("users"), dict)
            else {},
            "groups": {
                str(key): {
                    "enabled": bool(value.get("enabled", True)),
                    "name": value.get("name") or value.get("group_name") or "",
                }
                for key, value in raw_data.get("groups", {}).items()
                if isinstance(value, dict)
            }
            if isinstance(raw_data.get("groups"), dict)
            else {},
        }

        for key in (
            "daily_state",
            "daily_plan",
            "daily_story_plan",
            "daily_dream",
            "daily_outfit_photo",
            "detail_enhanced_segments",
            "qq_presence_state",
            "screen_diary_context",
            "worldbook_import_state",
            "worldbook_group_profiles",
            "qzone_integration",
            "reading_archive_integration",
            "reading_archive_state",
            "skill_growth",
            "personal_goal_state",
            "food_menu",
            "external_proactive_abilities",
            "proactive_runtime",
            "message_debounce",
            "smart_message_debounce",
            "expression_voice_profile",
        ):
            value = raw_data.get(key)
            if isinstance(value, dict):
                data[key] = dict(value)
            elif value is not None:
                data[key] = value

        candidate_pool = raw_data.get("proactive_candidate_pool")
        data["proactive_candidate_pool"] = (
            [dict(item) if isinstance(item, dict) else item for item in candidate_pool]
            if isinstance(candidate_pool, list)
            else []
        )

        for key, limit in (
            ("proactive_audit_log", 120),
            ("bot_diaries", 8),
            ("dream_fragments", 40),
            ("schedule_adjustments", 24),
            ("bookshelf_items", 80),
            ("creative_projects", 24),
            ("personal_goals", 80),
            ("external_event_pool", 80),
        ):
            data[key] = list_tail(raw_data.get(key), limit)

        image_cache = raw_data.get("private_image_vision_cache")
        data["private_image_vision_cache_count"] = len(image_cache) if isinstance(image_cache, dict) else 0
        data["private_image_vision_cache"] = {}

        profiles = raw_data.get("worldbook_member_profiles")
        if isinstance(profiles, dict):
            profile_items = [(str(key), value) for key, value in profiles.items() if isinstance(value, dict)]
            data["worldbook_member_profile_count"] = len(profile_items)
            data["worldbook_enabled_member_profile_count"] = sum(1 for _, value in profile_items if bool(value.get("enabled", True)))
            data["worldbook_pending_observation_total"] = sum(
                len(value.get("pending_observations"))
                for _, value in profile_items
                if isinstance(value.get("pending_observations"), list)
            )
            data["worldbook_member_profiles"] = {key: value for key, value in profile_items[:160]}
        else:
            data["worldbook_member_profile_count"] = 0
            data["worldbook_enabled_member_profile_count"] = 0
            data["worldbook_pending_observation_total"] = 0
            data["worldbook_member_profiles"] = {}
        worldbook_groups = raw_data.get("worldbook_group_profiles")
        if isinstance(worldbook_groups, dict):
            group_items = [(str(key), value) for key, value in worldbook_groups.items() if isinstance(value, dict)]
            data["worldbook_group_profile_count"] = len(group_items)
            data["worldbook_group_profiles"] = {key: value for key, value in group_items[:120]}
        else:
            data["worldbook_group_profile_count"] = 0
            data["worldbook_group_profiles"] = {}
        entries = raw_data.get("worldbook_entries")
        data["worldbook_entry_count"] = len(entries) if isinstance(entries, list) else 0
        data["worldbook_entries"] = list_tail(entries, 300) if isinstance(entries, list) else []

        news_state = shallow_dict(raw_data.get("news_integration"))
        if news_state:
            news_state["latest_items"] = list_tail(news_state.get("latest_items"), 12)
            news_state["digests"] = list_tail(news_state.get("digests"), 40)
            ai_daily = shallow_dict(news_state.get("ai_daily"))
            if ai_daily:
                ai_digest = shallow_dict(ai_daily.get("last_digest"))
                if ai_digest:
                    ai_digest["items"] = list_tail(ai_digest.get("items"), 3)
                    ai_daily["last_digest"] = ai_digest
                news_state["ai_daily"] = ai_daily
            data["news_integration"] = news_state

        web_state = shallow_dict(raw_data.get("web_exploration"))
        if web_state:
            web_state["notes"] = list_tail(web_state.get("notes"), 40)
            web_state["latest_results"] = list_tail(web_state.get("latest_results"), 8)
            data["web_exploration"] = web_state

        bilibili_state = shallow_dict(raw_data.get("bilibili_integration"))
        if bilibili_state:
            data["bilibili_integration"] = bilibili_state

        return data
    def _cache_summary(self, data: dict[str, Any]) -> dict[str, Any]:
        image_cache = data.get("private_image_vision_cache") if isinstance(data.get("private_image_vision_cache"), dict) else {}
        image_cache_count = self._int(data.get("private_image_vision_cache_count")) if "private_image_vision_cache_count" in data else len(image_cache)
        metrics = data.get("cache_metrics") if isinstance(data.get("cache_metrics"), dict) else {}

        def metric_row(name: str) -> dict[str, Any]:
            item = metrics.get(name) if isinstance(metrics.get(name), dict) else {}
            hits = 0
            misses = 0
            try:
                hits = max(0, int(item.get("hits") or 0))
            except (TypeError, ValueError):
                hits = 0
            try:
                misses = max(0, int(item.get("misses") or 0))
            except (TypeError, ValueError):
                misses = 0
            total = hits + misses
            return {
                "hits": hits,
                "misses": misses,
                "total": total,
                "hit_rate": round(hits / total, 4) if total else 0,
                "last_hit_at": self.plugin._format_timestamp_elapsed(item.get("last_hit_ts", 0)),
                "last_miss_at": self.plugin._format_timestamp_elapsed(item.get("last_miss_ts", 0)),
            }

        atrelay_cache = getattr(self.plugin, "_atrelay_member_cache", {})
        atrelay_count = len(atrelay_cache) if isinstance(atrelay_cache, dict) else 0
        weather = data.get("daily_weather") if isinstance(data.get("daily_weather"), dict) else {}
        weather_age = self.plugin._format_timestamp_elapsed(weather.get("fetched_ts", 0)) if weather else ""
        qweather_location = data.get("qweather_location") if isinstance(data.get("qweather_location"), dict) else {}
        location_label = ""
        weather_key_getter = getattr(self.plugin, "_weather_context_config_key", None)
        try:
            current_weather_key = self._single_line(weather_key_getter(), 96) if callable(weather_key_getter) else ""
        except Exception:
            current_weather_key = ""
        if not current_weather_key or self._single_line(weather.get("config_key"), 96) == current_weather_key:
            location_label = self._single_line(weather.get("location_label"), 120)
        location_key_getter = getattr(self.plugin, "_qweather_location_cache_key", None)
        has_location_key_getter = callable(location_key_getter)
        try:
            current_location_key = self._single_line(location_key_getter(), 96) if has_location_key_getter else ""
        except Exception:
            current_location_key = ""
        if (
            not location_label
            and (
                not has_location_key_getter
                or (
                    current_location_key
                    and self._single_line(qweather_location.get("config_key"), 96) == current_location_key
                )
            )
        ):
            weather_source = str(getattr(self.plugin, "weather_source", "qweather") or "qweather").strip().lower()
            qweather_location_allowed = weather_source == "qweather" or bool(
                getattr(self.plugin, "enable_weather_alerts", False)
            )
            if qweather_location_allowed:
                location_label = self._single_line(qweather_location.get("label"), 120)
        weather_alert_cache = data.get("weather_alerts") if isinstance(data.get("weather_alerts"), dict) else {}
        raw_alerts = weather_alert_cache.get("alerts") if isinstance(weather_alert_cache.get("alerts"), list) else []
        alert_cache_matches = True
        alert_config_getter = getattr(self.plugin, "_weather_alert_config_key", None)
        if callable(alert_config_getter):
            try:
                current_alert_config = self._single_line(alert_config_getter(), 96)
            except Exception:
                current_alert_config = ""
            if not current_alert_config or self._single_line(weather_alert_cache.get("config_key"), 96) != current_alert_config:
                alert_cache_matches = False
                raw_alerts = []
        alert_filter = getattr(self.plugin, "_filter_weather_alerts", None)
        try:
            visible_alerts = alert_filter(raw_alerts, getattr(self.plugin, "weather_alert_min_severity", "blue")) if callable(alert_filter) else raw_alerts
        except Exception:
            visible_alerts = raw_alerts
        alerts_enabled = bool(getattr(self.plugin, "enable_weather_context", True)) and bool(getattr(self.plugin, "enable_weather_alerts", False))
        if not alerts_enabled:
            visible_alerts = []
        alert_rank = getattr(self.plugin, "_qweather_alert_rank", None)
        if callable(alert_rank):
            try:
                visible_alerts = sorted(
                    [item for item in visible_alerts if isinstance(item, dict)],
                    key=lambda item: alert_rank(item.get("color_code") or item.get("color") or item.get("severity")),
                    reverse=True,
                )
            except Exception:
                visible_alerts = [item for item in visible_alerts if isinstance(item, dict)]
        weather_alert_age = self.plugin._format_timestamp_elapsed(weather_alert_cache.get("fetched_ts", 0)) if weather_alert_cache else ""
        top_alert = visible_alerts[0] if visible_alerts else {}
        alert_attributions = weather_alert_cache.get("attributions") if alert_cache_matches and isinstance(weather_alert_cache.get("attributions"), list) else []
        alert_attributions = [self._single_line(item, 320) for item in alert_attributions if self._single_line(item, 320)][:4]
        provider_runtime: dict[str, Any] = {}
        runtime_getter = getattr(self.plugin, "_private_image_visual_provider_runtime_summary", None)
        if callable(runtime_getter):
            try:
                provider_runtime = runtime_getter()
            except Exception as exc:
                provider_runtime = {"error": self._single_line(exc, 160)}
        return {
            "private_image_vision": {
                "enabled": bool(getattr(self.plugin, "enable_private_image_vision_cache", False)),
                "items": image_cache_count,
                "max_items": int(getattr(self.plugin, "private_image_vision_cache_max_items", 0) or 0),
                "private": metric_row("image_vision:private_image"),
                "group": metric_row("image_vision:group_image"),
                "forward": metric_row("image_vision:forward_image"),
                "provider_runtime": provider_runtime,
            },
            "atrelay_member_cache": {
                "items": atrelay_count,
                "ttl_minutes": int(getattr(self.plugin, "atrelay_member_cache_minutes", 0) or 0),
            },
            "weather": {
                "cached": bool(weather),
                "age": weather_age,
                "source": self._single_line(weather.get("source"), 40),
                "summary": self._single_line(weather.get("prompt"), 120),
                "location_label": location_label,
                "alerts_enabled": alerts_enabled,
                "alerts_cached": bool(weather_alert_cache) and alert_cache_matches,
                "alerts_count": len(visible_alerts),
                "alerts_highest_level": self._single_line(top_alert.get("color") or top_alert.get("severity"), 24),
                "alerts_age": weather_alert_age,
                "alerts_stale": bool(weather_alert_cache.get("stale")),
                "alerts_error": self._single_line(weather_alert_cache.get("error"), 100),
                "alerts_attributions": alert_attributions,
            },
        }
    def _troubleshooting_warning_type(self, scope: str, *parts: Any) -> str:
        source = "\x1f".join(
            self._single_line(part, 180).strip().lower()
            for part in (scope, *parts)
            if self._single_line(part, 180).strip()
        )
        digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:20]
        return f"warning:{digest}"
    def _troubleshooting_semantic_warning_type(self, code: Any) -> str:
        normalized = re.sub(r"[^a-z0-9_.:-]+", "_", self._single_line(code, 120).lower()).strip("_.:-")
        return self._troubleshooting_warning_type("semantic", normalized or "unknown")
    def _troubleshooting_proactive_warning_code(self, kind: str, item: dict[str, Any], note: str) -> str:
        text = " ".join(
            self._single_line(value, 180).lower()
            for value in (item.get("reason"), item.get("source"), item.get("action"), note)
            if value
        )
        categories = (
            ("timeout", ("超时", "timeout", "timed out")),
            ("provider", ("provider", "模型", "不可用", "api", "鉴权", "401", "403")),
            ("send", ("发送失败", "投递失败", "send", "发送异常")),
            ("storage", ("保存失败", "写入失败", "database", "sqlite", "locked", "存储")),
            ("media", ("生图", "图片", "photo", "image", "参考图", "下载")),
            ("voice", ("tts", "语音", "音频")),
            ("tool", ("工具", "tool", "调用失败")),
        )
        category = next((name for name, tokens in categories if any(token in text for token in tokens)), "other")
        action = re.sub(r"[^a-z0-9_]+", "_", self._single_line(item.get("action"), 40).lower()).strip("_") or "message"
        return f"proactive.{kind}.{category}.{action}"
    def _troubleshooting_chain_warning_code(self, test_type: str, text: Any) -> str:
        warning = self._single_line(text, 360).lower()
        categories = (
            ("timeout_budget", ("测试外层最多等待", "测试层截断")),
            ("fallback_delay", ("备选在线图片 api", "回退链路")),
            ("sdgen_timeout", ("sdgen",)),
            ("auto_backend", ("当前为自动后端",)),
            ("reference_missing", ("没有解析到可用本地参考图",)),
            ("reference_scope", ("参考图", "文生图")),
            ("serial_queue", ("全局串行锁", "先排队")),
            ("gateway_buffer", ("cloudflare", "网关", "缓冲")),
            ("test_scope", ("只检查生成文件", "不覆盖后续")),
        )
        category = next((name for name, tokens in categories if any(token in warning for token in tokens)), "other")
        normalized_test = re.sub(r"[^a-z0-9_]+", "_", self._single_line(test_type, 60).lower()).strip("_") or "chain"
        return f"chain.{normalized_test}.{category}"
    def _troubleshooting_chain_tests_with_warning_items(
        self,
        results: dict[str, Any],
        suppressed_keys: set[str],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        decorated = deepcopy(results) if isinstance(results, dict) else {}
        all_items: list[dict[str, Any]] = []
        for test_type, raw_result in decorated.items():
            if not isinstance(raw_result, dict):
                continue
            warning_items: list[dict[str, Any]] = []
            for warning in raw_result.get("warnings", []) if isinstance(raw_result.get("warnings"), list) else []:
                text = self._single_line(warning, 360)
                if not text:
                    continue
                code = self._troubleshooting_chain_warning_code(str(test_type), text)
                warning_items.append(
                    {
                        "level": "warn",
                        "title": self._single_line(text, 90),
                        "text": text,
                        "source": "链路测试",
                        "warning_code": code,
                        "warning_type": self._troubleshooting_semantic_warning_type(code),
                    }
                )
            all_items.extend(warning_items)
            visible_items = self._filter_suppressed_troubleshooting_warnings(warning_items, suppressed_keys)
            raw_result["warning_items"] = visible_items
            raw_result["warnings"] = [item["text"] for item in visible_items]
        return decorated, all_items
    def _troubleshooting_legacy_warning_code(self, title: Any) -> str:
        normalized = self._single_line(title, 90)
        aliases = {
            "TTS 配置已开但 provider 不可用": "tts.provider_unavailable",
            "TTS 强化已开但会话 TTS 未启用": "tts.provider_unavailable",
            "TTS 强化开启但合成 provider 不可用": "tts.provider_unavailable",
            "暂无启用的私聊对象": "proactive.no_enabled_users",
            "主动消息没有私聊对象": "proactive.no_enabled_users",
            "私聊主动已关闭": "proactive.daily_limit_zero",
            "私聊主动总额度为 0": "proactive.daily_limit_zero",
            "Token 软限额正在暂缓后台任务": "token.soft_limit_active",
            "每日 Token 软限额已接管": "token.soft_limit_active",
            "SQLite 并发状态需要关注": "sqlite.wal",
            "主动循环心跳不新鲜": "proactive.loop_stale",
            "私聊图片识别调度状态读取失败": "vision.runtime_unreadable",
            "私聊图片识别暂无可用模型": "vision.no_available_provider",
            "有识图模型被临时降权": "vision.provider_cooldown",
            "配置诊断仍有待处理项": "diagnostic.pending",
        }
        return aliases.get(normalized, "")
    def _troubleshooting_warning_records(self, data: dict[str, Any] | None) -> list[dict[str, Any]]:
        raw = data.get("troubleshooting_suppressed_warning_types") if isinstance(data, dict) else []
        records: list[dict[str, Any]] = []
        seen: set[str] = set()
        seen_raw: set[str] = set()
        for item in raw if isinstance(raw, list) else []:
            record = item if isinstance(item, dict) else {"key": item}
            raw_key = self._single_line(record.get("key"), 64)
            if raw_key and raw_key in seen_raw:
                continue
            if raw_key:
                seen_raw.add(raw_key)
            code = re.sub(r"[^a-z0-9_.:-]+", "_", self._single_line(record.get("code"), 120).lower()).strip("_.:-")
            if not code:
                code = self._troubleshooting_legacy_warning_code(record.get("title"))
            key = self._troubleshooting_semantic_warning_type(code) if code else raw_key
            if not re.fullmatch(r"warning:[0-9a-f]{20}", key) or key in seen:
                continue
            seen.add(key)
            records.append(
                {
                    "key": key,
                    "title": self._single_line(record.get("title"), 90) or "未命名警告类型",
                    "source": self._single_line(record.get("source"), 40) or "排障检查",
                    "suppressed_at": self._float(record.get("suppressed_at")),
                    "code": code,
                }
            )
        return records[:120]
    def _troubleshooting_diagnostics_with_types(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        decorated: list[dict[str, Any]] = []
        for raw in items:
            if not isinstance(raw, dict):
                continue
            item = dict(raw)
            code = self._single_line(item.get("warning_code"), 120)
            item["warning_type"] = self._single_line(item.get("warning_type"), 64) or (
                self._troubleshooting_semantic_warning_type(code)
                if code
                else self._troubleshooting_warning_type("diagnostic", item.get("title"))
            )
            decorated.append(item)
        return decorated
    def _filter_suppressed_troubleshooting_warnings(
        self,
        items: list[dict[str, Any]],
        suppressed_keys: set[str],
    ) -> list[dict[str, Any]]:
        return [
            item
            for item in items
            if not (
                self._single_line(item.get("level"), 12) == "warn"
                and self._single_line(item.get("warning_type"), 64) in suppressed_keys
            )
        ]
    def _troubleshooting_suppression_payload(
        self,
        records: list[dict[str, Any]],
        *item_groups: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        active_counts: dict[str, int] = {}
        for items in item_groups:
            for item in items:
                if not isinstance(item, dict) or self._single_line(item.get("level"), 12) != "warn":
                    continue
                key = self._single_line(item.get("warning_type"), 64)
                if key:
                    active_counts[key] = active_counts.get(key, 0) + 1
        return [{**record, "current_count": active_counts.get(record["key"], 0)} for record in records]
    async def update_troubleshooting_warning_suppression(self) -> dict[str, Any]:
        payload = await _page_api_host.request.get_json(silent=True) or {}
        action = self._single_line(payload.get("action"), 24).lower()
        if action not in {"suppress", "restore", "restore_all"}:
            return self._error("action 只能是 suppress、restore 或 restore_all")
        key = self._single_line(payload.get("key"), 64)
        code = re.sub(r"[^a-z0-9_.:-]+", "_", self._single_line(payload.get("code"), 120).lower()).strip("_.:-")
        if action == "suppress" and code:
            key = self._troubleshooting_semantic_warning_type(code)
        if action != "restore_all" and not re.fullmatch(r"warning:[0-9a-f]{20}", key):
            return self._error("无效的警告类型")
        try:
            async with self.plugin._data_lock:
                records = self._troubleshooting_warning_records(self.plugin.data)
                previous = list(records)
                if action == "suppress":
                    record = {
                        "key": key,
                        "title": self._single_line(payload.get("title"), 90) or "未命名警告类型",
                        "source": self._single_line(payload.get("source"), 40) or "排障检查",
                        "suppressed_at": time.time(),
                        "code": code,
                    }
                    records = [item for item in records if item.get("key") != key]
                    records.append(record)
                    records = records[-120:]
                elif action == "restore":
                    records = [item for item in records if item.get("key") != key]
                else:
                    records = []
                changed = records != previous
                self.plugin.data["troubleshooting_suppressed_warning_types"] = records
                if changed:
                    self.plugin._save_data_sync(sections={"troubleshooting_suppressed_warning_types"})
            return self._ok(
                {
                    "items": records,
                    "count": len(records),
                    "changed": changed,
                    "message": "已屏蔽此类警告" if action == "suppress" else ("已恢复全部警告类型" if action == "restore_all" else "已恢复此类警告"),
                }
            )
        except Exception as exc:
            logger.error("更新排障警告屏蔽失败: %s", self._single_line(exc, 160), exc_info=True)
            return self._exception_error(str(exc))
    async def get_diagnostics(self) -> dict[str, Any]:
        try:
            async with self.plugin._data_lock:
                raw_users = self.plugin.data.get("users") if isinstance(self.plugin.data.get("users"), dict) else {}
                raw_groups = self.plugin.data.get("groups") if isinstance(self.plugin.data.get("groups"), dict) else {}
                users = {str(key): dict(value) for key, value in raw_users.items() if isinstance(value, dict)}
                groups = {str(key): dict(value) for key, value in raw_groups.items() if isinstance(value, dict)}
                suppression_records = self._troubleshooting_warning_records(self.plugin.data)
            tune_result = await self._maybe_apply_personality_iteration_auto_tune(users, groups)
            items = self._build_diagnostics(users, groups)
            tune_item = self._personality_auto_tune_diagnostic_item(tune_result)
            if tune_item:
                items.append(tune_item)
            items = self._troubleshooting_diagnostics_with_types(items)
            visible_items = self._filter_suppressed_troubleshooting_warnings(
                items,
                {record["key"] for record in suppression_records},
            )
            extension_status_getter = getattr(
                getattr(self.plugin, "extension_api", None),
                "extension_control_plane_status",
                None,
            )
            if callable(extension_status_getter):
                try:
                    extension_status = extension_status_getter()
                except Exception as exc:
                    logger.warning("扩展控制面自检失败: %s", self._single_line(exc, 160))
                    extension_status = {"issues": ["control_plane_self_check_failed"]}
            else:
                extension_status = {"issues": ["control_plane_unavailable"]}
            return self._ok(
                {
                    "items": visible_items,
                    "suppressed_warning_types": self._troubleshooting_suppression_payload(suppression_records, items),
                    "extension_control_plane": extension_status,
                }
            )
        except Exception as exc:
            logger.error(f"获取诊断失败: {exc}", exc_info=True)
            return self._exception_error(str(exc))
    async def get_troubleshooting(self) -> dict[str, Any]:
        try:
            await self._recover_stale_troubleshooting_proactive_test()
            async with self.plugin._data_lock:
                data = deepcopy(self.plugin.data)
                default_data = deepcopy(getattr(self.plugin, "_data_default", {}) or {})
            users = data.get("users") if isinstance(data.get("users"), dict) else {}
            groups = data.get("groups") if isinstance(data.get("groups"), dict) else {}
            tune_result = await self._maybe_apply_personality_iteration_auto_tune(users, groups)
            suppression_records = self._troubleshooting_warning_records(data)
            suppressed_keys = {record["key"] for record in suppression_records}
            all_diagnostics = self._build_diagnostics(users, groups)
            tune_item = self._personality_auto_tune_diagnostic_item(tune_result)
            if tune_item:
                all_diagnostics.append(tune_item)
            all_diagnostics = self._troubleshooting_diagnostics_with_types(all_diagnostics)
            diagnostics = self._filter_suppressed_troubleshooting_warnings(all_diagnostics, suppressed_keys)
            proactive_tasks = await self._proactive_task_summary_async(data)
            proactive_candidates = self._proactive_candidate_summary(data)
            token_stats = self._token_stats_payload(data.get("token_usage", {}))
            cache = self._cache_summary(data)
            tts = self._tts_runtime_summary(users)
            sqlite_status = await self._sqlite_wal_status_summary()
            all_sqlite_items: list[dict[str, Any]] = []
            for raw_item in sqlite_status.get("items", []) if isinstance(sqlite_status.get("items"), list) else []:
                if not isinstance(raw_item, dict):
                    continue
                item = dict(raw_item)
                if self._single_line(item.get("level"), 12) == "warn":
                    item["warning_code"] = "sqlite.wal"
                    item["warning_type"] = self._troubleshooting_semantic_warning_type("sqlite.wal")
                all_sqlite_items.append(item)
            sqlite_status = {**sqlite_status, "items": all_sqlite_items}
            passive_no_reply = self._passive_no_reply_summary(data)
            routing_root = default_data.get("persona_routing_warnings")
            persona_routing_warnings = (
                routing_root.get("items", [])
                if isinstance(routing_root, dict) and isinstance(routing_root.get("items"), list)
                else []
            )
            persona_routing_warnings = self._active_persona_routing_warnings(
                persona_routing_warnings
            )
            screen_companion = self._screen_companion_summary(data)
            qzone = self._qzone_summary(data)
            all_recent_events = self._troubleshooting_recent_events(
                diagnostics=diagnostics,
                proactive_tasks=proactive_tasks,
                proactive_candidates=proactive_candidates,
                token_stats=token_stats,
                passive_no_reply=passive_no_reply,
                persona_routing_warnings=persona_routing_warnings,
            )
            all_checks = self._troubleshooting_checks(
                data=data,
                users=users,
                groups=groups,
                diagnostics=diagnostics,
                proactive_tasks=proactive_tasks,
                proactive_candidates=proactive_candidates,
                token_stats=token_stats,
                cache=cache,
                tts=tts,
                sqlite_status=sqlite_status,
            )
            recent_events = self._filter_suppressed_troubleshooting_warnings(all_recent_events, suppressed_keys)
            checks = self._filter_suppressed_troubleshooting_warnings(all_checks, suppressed_keys)
            visible_sqlite_items = self._filter_suppressed_troubleshooting_warnings(all_sqlite_items, suppressed_keys)
            visible_sqlite_status = {**sqlite_status, "items": visible_sqlite_items}
            chain_tests, all_chain_warning_items = self._troubleshooting_chain_tests_with_warning_items(
                self._troubleshooting_test_results(data),
                suppressed_keys,
            )
            suppression_payload = self._troubleshooting_suppression_payload(
                suppression_records,
                all_diagnostics,
                all_recent_events,
                all_checks,
                all_sqlite_items,
                all_chain_warning_items,
            )
            active_suppressed_count = sum(self._int(item.get("current_count")) for item in suppression_payload)
            counts = {
                "error": sum(1 for item in recent_events if item.get("level") == "error") + sum(1 for item in checks if item.get("level") == "error"),
                "warn": sum(1 for item in recent_events if item.get("level") == "warn") + sum(1 for item in checks if item.get("level") == "warn"),
                "info": sum(1 for item in recent_events if item.get("level") == "info") + sum(1 for item in checks if item.get("level") == "info"),
                "ok": sum(1 for item in checks if item.get("level") == "ok"),
            }
            headline_level = "error" if counts["error"] else ("warn" if counts["warn"] else "ok")
            headline = "发现需要处理的异常" if headline_level == "error" else (
                "有可关注项"
                if headline_level == "warn"
                else ("未发现未屏蔽异常" if active_suppressed_count else "运行状态正常")
            )
            return self._ok(
                {
                    "summary": {
                        "level": headline_level,
                        "headline": headline,
                        "counts": counts,
                        "suppressed_count": active_suppressed_count,
                        "suppressed_types": len(suppression_payload),
                        "generated_at": self.plugin._format_timestamp_elapsed(time.time()),
                    },
                    "recent_events": recent_events[:80],
                    "checks": checks,
                    "diagnostics": diagnostics,
                    "sqlite": visible_sqlite_status,
                    "chain_tests": chain_tests,
                    "image_api_endpoints": self._troubleshooting_image_api_endpoints(),
                    "recent_photo_generations": self._recent_photo_generation_summary(data),
                    "passive_no_reply": passive_no_reply,
                    "prompt_injections": self._prompt_injection_summary(data),
                    "screen_companion": screen_companion,
                    "qzone": qzone,
                    "proactive_intensity": self._proactive_intensity_summary(),
                    "proactive_runtime": proactive_tasks.get("runtime", {}),
                    "token_budget": token_stats.get("budget", {}),
                    "cache": cache,
                    "tts": tts,
                    "suppressed_warning_types": suppression_payload,
                }
            )
        except Exception as exc:
            logger.error(f"获取排障信息失败: {exc}", exc_info=True)
            return self._exception_error(str(exc))
    async def _recover_stale_troubleshooting_proactive_test(self, *, max_age_seconds: int = 120) -> int:
        recovered = 0
        now = time.time()
        async with self.plugin._data_lock:
            users = self.plugin.data.get("users")
            if not isinstance(users, dict):
                return 0
            for user_id, user in list(users.items()):
                if not isinstance(user, dict):
                    continue
                if str(user.get("planned_proactive_source") or "") != "troubleshooting":
                    continue
                if not isinstance(user.get("troubleshooting_proactive_restore"), dict):
                    continue
                started = self._float(user.get("troubleshooting_proactive_started_at"))
                if started <= 0 or now - started <= max_age_seconds:
                    continue
                self.plugin._append_troubleshooting_proactive_step(
                    user,
                    "等待结果",
                    "error",
                    f"超过 {max_age_seconds} 秒仍未完成，已停止等待并恢复原主动计划",
                )
                self.plugin._record_troubleshooting_proactive_result(
                    str(user_id),
                    user,
                    ok=False,
                    detail="主动消息测试等待超时，已恢复原主动计划",
                    error=f"超过 {max_age_seconds} 秒仍未完成；请确认主动循环是否启动、目标用户是否有私聊会话、发送端是否可用",
                    action=str(user.get("planned_proactive_action") or "message"),
                    reason=str(user.get("planned_proactive_reason") or "check_in"),
                )
                user["proactive_sending"] = False
                user["proactive_sending_started_at"] = 0
                self.plugin._restore_troubleshooting_proactive_plan(user)
                self._cancel_troubleshooting_proactive_wakeup(str(user_id))
                recovered += 1
            if recovered:
                self.plugin._save_data_sync(
                    sections={"users", "troubleshooting_test_results"}
                )
        return recovered
    def _safe_test_diagnostic_text(self, value: Any, limit: int = 1200) -> str:
        if value in (None, ""):
            return ""
        try:
            cleaned = _redact_outbound_secrets(str(value), getattr(self, "plugin", None))
        except Exception:
            return "测试详情脱敏失败，请根据测试编号查看 AstrBot 后端日志"
        return self._multi_line(cleaned, max(80, limit))
    def _finalize_test_diagnostics(
        self,
        test_type: str,
        result: dict[str, Any] | None,
        started_at: float,
        *,
        title: str = "",
        finished_at: float | None = None,
    ) -> dict[str, Any]:
        item = dict(result or {})
        ended_at = float(finished_at or time.time())
        elapsed_ms = self._int(item.get("elapsed_ms")) or max(0, int((ended_at - started_at) * 1000))
        resolved_title = self._single_line(item.get("title"), 80) or self._single_line(title, 80) or self._troubleshooting_test_title(test_type)
        item["title"] = resolved_title
        for key, limit in (("error", 1600), ("delivery_error", 1200), ("detail", 1200), ("diagnostic_detail", 4000)):
            if item.get(key):
                item[key] = self._safe_test_diagnostic_text(item.get(key), limit)
        item["warnings"] = [
            message
            for warning in (item.get("warnings") if isinstance(item.get("warnings"), list) else [])[:8]
            if (message := self._safe_test_diagnostic_text(warning, 800))
        ]
        if item.get("exception_type"):
            item["exception_type"] = self._single_line(item.get("exception_type"), 120)

        normalized_steps: list[dict[str, Any]] = []
        status_aliases = {"success": "ok", "passed": "ok", "failed": "error", "warning": "warn", "pending": "info"}
        raw_steps = item.get("steps") if isinstance(item.get("steps"), list) else []
        for raw_step in raw_steps[:24]:
            if not isinstance(raw_step, dict):
                continue
            step_status = self._single_line(raw_step.get("status"), 16).lower() or "info"
            step_status = status_aliases.get(step_status, step_status)
            if step_status not in {"ok", "error", "warn", "info"}:
                step_status = "info"
            normalized_steps.append(
                {
                    "name": self._single_line(raw_step.get("name"), 60) or "执行阶段",
                    "status": step_status,
                    "detail": self._safe_test_diagnostic_text(raw_step.get("detail"), 800),
                    "elapsed_ms": self._int(raw_step.get("elapsed_ms")),
                }
            )
        if not normalized_steps:
            summary = item.get("error") or item.get("detail") or ("测试已通过" if item.get("ok") else "测试未通过")
            normalized_steps.append(
                {
                    "name": "执行测试",
                    "status": "info" if item.get("pending") or item.get("unsupported") else ("ok" if item.get("ok") else "error"),
                    "detail": self._safe_test_diagnostic_text(summary, 800),
                    "elapsed_ms": elapsed_ms,
                }
            )
        item["steps"] = normalized_steps

        failure = self._classify_test_failure(test_type, item)
        item.update(failure)
        item["diagnostic_version"] = 1
        request_id = self._single_line(item.get("request_id") or item.get("trace_id"), 32) or uuid.uuid4().hex[:12]
        item["request_id"] = request_id
        item["trace_id"] = request_id
        supplied_status = self._single_line(item.get("test_status"), 16).lower()
        item["test_status"] = supplied_status if supplied_status in {"unsupported", "skipped"} else (
            "pending" if item.get("pending") else ("passed" if item.get("ok") else "failed")
        )
        item["started_at"] = float(started_at)
        item["finished_at"] = ended_at
        item["elapsed_ms"] = elapsed_ms

        entries: list[dict[str, Any]] = [
            {
                "elapsed_ms": 0,
                "level": "info",
                "stage": "开始",
                "message": f"开始执行{resolved_title}",
            }
        ]
        for warning in (item.get("warnings") if isinstance(item.get("warnings"), list) else [])[:8]:
            message = self._safe_test_diagnostic_text(warning, 800)
            if message:
                entries.append({"elapsed_ms": 0, "level": "warn", "stage": "范围说明", "message": message})
        for step in normalized_steps:
            entries.append(
                {
                    "elapsed_ms": self._int(step.get("elapsed_ms")),
                    "level": step.get("status") or "info",
                    "stage": step.get("name") or "执行阶段",
                    "message": step.get("detail") or "",
                }
            )
        final_message = item.get("error") or item.get("detail") or (
            "测试仍在等待异步任务完成" if item.get("pending") else "测试完成"
        )
        entries.append(
            {
                "elapsed_ms": elapsed_ms,
                "level": "info" if item.get("pending") or item.get("unsupported") else ("ok" if item.get("ok") else "error"),
                "stage": "结果",
                "message": self._safe_test_diagnostic_text(final_message, 1200),
            }
        )
        item["diagnostic_entries"] = entries[:32]
        return item
    async def run_troubleshooting_test(self) -> dict[str, Any]:
        payload = await _page_api_host.request.get_json(silent=True) or {}
        test_type = self._single_line(payload.get("type"), 40)
        request_id = secrets.token_hex(6)
        payload["_test_request_id"] = request_id
        result_key = test_type
        start = time.time()
        logger.info(
            "[test:%s][type:%s] 开始执行测试",
            request_id,
            test_type or "unknown",
        )
        try:
            if test_type == "proactive_message":
                await self._recover_stale_troubleshooting_proactive_test(max_age_seconds=120)
            if test_type in {"image_generation", "image_generation_text2img", "image_generation_selfie"}:
                image_payload = dict(payload)
                if test_type == "image_generation_text2img":
                    image_payload["workflow_kind"] = "text2img"
                elif test_type == "image_generation_selfie":
                    image_payload["workflow_kind"] = "selfie"
                result = await self._run_image_generation_chain_test(image_payload)
            elif test_type == "image_api_endpoint":
                result = await self._run_image_api_endpoint_test(payload)
                result_key = self._single_line(result.get("test_key"), 80) or test_type
            elif test_type == "tts_generation":
                result = await self._run_tts_generation_chain_test(payload)
            elif test_type == "screen_peek":
                result = await self._run_screen_peek_chain_test(payload)
            elif test_type == "qzone_integration":
                result = await self._run_qzone_chain_test(payload)
            elif test_type == "proactive_message":
                result = await self._run_proactive_message_chain_test(payload)
            elif test_type in {"skill_similarity", "model_diagnostics"}:
                result = await self._run_model_diagnostics_check(payload)
            elif test_type in {"weather_api", "balance_api", "web_search"}:
                result = await self._run_external_api_test(test_type, payload)
            else:
                return self._error("未知排障测试类型")
        except Exception as exc:
            if test_type in {"weather_api", "balance_api", "web_search"}:
                raw_settings = payload.get("settings") if isinstance(payload.get("settings"), dict) else {}
                safe_error = self._redact_external_api_test_text(
                    exc,
                    tester=self.plugin,
                    settings=raw_settings,
                    limit=1600,
                )
                logger.warning(
                    "外部接口排障测试失败: type=%s err=%s",
                    test_type,
                    self._single_line(safe_error, 160),
                )
            else:
                safe_error = self._safe_test_diagnostic_text(exc, 1600)
                logger.warning(
                    "排障链路测试失败: %s",
                    self._single_line(exc, 160),
                    exc_info=True,
                )
            result = {
                "type": test_type,
                "ok": False,
                "title": self._troubleshooting_test_title(test_type),
                "error": safe_error,
                "exception_type": exc.__class__.__name__,
            }
            if test_type in {"image_generation", "image_generation_text2img", "image_generation_selfie"}:
                result.update(self._image_generation_called_plugin_diagnostics())
        result["type"] = test_type
        result["elapsed_ms"] = self._int(result.get("elapsed_ms")) or int((time.time() - start) * 1000)
        result["ran_at"] = time.time()
        result["ran_at_text"] = self.plugin._format_timestamp_elapsed(result["ran_at"])
        result.setdefault("request_id", request_id)
        result = self._finalize_test_diagnostics(test_type, result, start, finished_at=result["ran_at"])
        result = self._diagnostic_envelope(
            result,
            test_type=test_type,
            duration_ms=self._int(result.get("elapsed_ms")),
            test_id=diagnostic_test_id(test_type),
        )
        await self._remember_troubleshooting_test_result(result_key, result)
        logger.info(
            "[test:%s][type:%s] 测试结束: status=%s elapsed_ms=%s",
            result.get("request_id"),
            test_type,
            result.get("test_status"),
            result.get("elapsed_ms"),
        )
        return self._ok(result)
    def _troubleshooting_role_appearance_prompt(self) -> str:
        persona = str(getattr(self.plugin, "schedule_persona_prompt", "") or self._config_get("schedule_persona_prompt") or "")
        recognition = str(
            getattr(self.plugin, "private_image_self_recognition_hint", "")
            or self._config_get("private_image_self_recognition_hint")
            or ""
        )
        if not persona and not recognition:
            return ""
        label_prefix = {
            "姓名": "角色名",
            "种族": "种族",
            "性别": "性别",
            "识别点": "主要识别点",
            "外貌": "外貌",
            "主要识别点": "主要识别点",
            "发型发色": "发型发色",
            "发色": "发色",
            "发型": "发型",
            "瞳色": "瞳色",
            "眼睛": "眼睛",
            "服饰风格": "服饰风格",
            "服装": "服装",
            "衣着": "衣着",
        }
        visual_labels = {
            "识别点",
            "外貌",
            "主要识别点",
            "发型发色",
            "发色",
            "发型",
            "瞳色",
            "眼睛",
            "服饰风格",
            "服装",
            "衣着",
        }
        parts: list[str] = []
        has_visual = bool(recognition)
        for line in str(persona or "").replace("\r", "\n").split("\n"):
            text = line.strip()
            if not text or ("：" not in text and ":" not in text):
                continue
            label, value = text.split("：", 1) if "：" in text else text.split(":", 1)
            label = label.strip()
            value = self._single_line(value, 140)
            if label in label_prefix and value:
                if label in visual_labels:
                    has_visual = True
                parts.append(f"{label_prefix[label]}：{value}")
        if recognition:
            parts.append(f"补充识别线索{self._single_line(recognition, 180)}")
        if not has_visual:
            return ""
        seen: set[str] = set()
        unique = []
        for item in parts:
            if item in seen:
                continue
            seen.add(item)
            unique.append(item)
        return self._single_line("，".join(unique), 420)
    async def _run_model_diagnostics_check(self, payload: dict[str, Any]) -> dict[str, Any]:
        started = time.time()
        use_model = self._normalize_bool_value(payload.get("use_model", True))
        async with self.plugin._data_lock:
            data = deepcopy(self.plugin.data)
        skill_items = self._skill_similarity_local_candidates(data)
        slang_items = self._model_diagnostics_slang_candidates(data)
        pending_items = self._model_diagnostics_pending_observation_candidates(data)
        memory_items = self._model_diagnostics_companion_memory_candidates(data)
        expression_items = self._model_diagnostics_expression_candidates(data)
        total_local = len(skill_items) + len(slang_items) + len(pending_items) + len(memory_items) + len(expression_items)
        sections: list[dict[str, Any]] = [
            {
                "key": "skills",
                "title": "技能相似项",
                "local_count": len(skill_items),
                "model_count": 0,
                "suggestions": [
                    f"技能｜{item.get('a')} ↔ {item.get('b')}：{item.get('reason')}"
                    for item in skill_items[:4]
                ],
            },
            {
                "key": "slang",
                "title": "群黑话杂音",
                "local_count": len(slang_items),
                "model_count": 0,
                "suggestions": [
                    f"黑话｜{item.get('group_name') or item.get('group_id')}｜{item.get('term')}：{item.get('reason')}"
                    for item in slang_items[:4]
                ],
            },
            {
                "key": "worldbook",
                "title": "关系网待确认观察",
                "local_count": len(pending_items),
                "model_count": 0,
                "suggestions": [
                    f"关系网｜{item.get('name') or item.get('user_id')}：{item.get('reason')}｜{item.get('evidence')}"
                    for item in pending_items[:4]
                ],
            },
            {
                "key": "memory",
                "title": "本地画像噪音",
                "local_count": len(memory_items),
                "model_count": 0,
                "suggestions": [
                    f"本地画像｜{item.get('name') or item.get('user_id')}｜{item.get('field')}：{item.get('reason')}｜{item.get('text')}"
                    for item in memory_items[:4]
                ],
            },
            {
                "key": "expression",
                "title": "表达规则重复与污染",
                "local_count": len(expression_items),
                "model_count": 0,
                "suggestions": [
                    f"表达学习｜{item.get('name') or item.get('user_id')}：{item.get('reason')}｜{item.get('text')}"
                    for item in expression_items[:4]
                ],
            },
        ]
        steps: list[dict[str, str]] = [
            {
                "name": "本地规则",
                "status": "ok" if total_local else "info",
                "detail": (
                    (
                        f"发现 {total_local} 条候选：技能 {len(skill_items)}、黑话 {len(slang_items)}、"
                        f"关系网 {len(pending_items)}、本地画像 {len(memory_items)}、表达学习 {len(expression_items)}"
                    )
                    if total_local
                    else "未发现明显技能冲突、黑话杂音、无效关系观察或私聊学习污染"
                ),
            }
        ]
        model_items: list[str] = []
        provider_id = ""
        if use_model and total_local:
            caller = getattr(self.plugin, "_llm_call", None)
            if callable(caller):
                provider_selector = getattr(self.plugin, "_task_provider", None)
                if callable(provider_selector):
                    provider_id = provider_selector(
                        getattr(self.plugin, "troubleshooting_provider_id", ""),
                        getattr(self.plugin, "response_review_provider_id", ""),
                        getattr(self.plugin, "mai_style_provider_id", ""),
                        getattr(self.plugin, "llm_provider_id", ""),
                    )
                else:
                    provider_id = str(
                        getattr(self.plugin, "troubleshooting_provider_id", "")
                        or getattr(self.plugin, "response_review_provider_id", "")
                        or getattr(self.plugin, "mai_style_provider_id", "")
                        or getattr(self.plugin, "llm_provider_id", "")
                        or ""
                    )
                prompt = self._model_diagnostics_review_prompt(
                    data,
                    skill_items,
                    slang_items,
                    pending_items,
                    memory_items,
                    expression_items,
                )
                try:
                    raw = await caller(
                        prompt,
                        max_tokens=700,
                        provider_id=provider_id,
                        task="troubleshooting_model_diagnostics",
                    )
                    model_items = self._parse_skill_similarity_model_result(raw)
                    self._attach_model_diagnostics_section_suggestions(sections, model_items)
                    steps.append(
                        {
                            "name": "模型复核",
                            "status": "ok" if model_items else "info",
                            "detail": f"模型给出 {len(model_items)} 条建议" if model_items else "模型未给出额外排障建议",
                        }
                    )
                except Exception as exc:
                    steps.append({"name": "模型复核", "status": "warn", "detail": f"调用失败: {self._single_line(exc, 120)}"})
            else:
                steps.append({"name": "模型复核", "status": "warn", "detail": "插件缺少 _llm_call，无法调用模型"})
        elif not use_model:
            steps.append({"name": "模型复核", "status": "info", "detail": "本次按请求仅执行本地规则检查"})
        elif not total_local:
            steps.append({"name": "模型复核", "status": "info", "detail": "没有本地候选，未调用模型"})

        local_preview = []
        for section in sections:
            local_preview.extend(section.get("suggestions") if isinstance(section.get("suggestions"), list) else [])
        all_suggestions = [*local_preview]
        for item in model_items:
            if item not in all_suggestions:
                all_suggestions.append(item)
        detail = (
            f"本地发现 {total_local} 条候选，模型给出 {len(model_items)} 条建议"
            if all_suggestions
            else "模型相关数据目前没有明显杂音"
        )
        return {
            "ok": True,
            "title": "模型数据排障",
            "provider": self._single_line(provider_id, 100),
            "detail": self._single_line(detail, 220),
            "text_preview": self._single_line(" | ".join(all_suggestions[:5]), 220),
            "suggestions": [self._single_line(item, 220) for item in all_suggestions[:12]],
            "sections": sections,
            "local_count": total_local,
            "model_count": len(model_items),
            "suggestion_count": len(all_suggestions),
            "extra_count": max(0, len(all_suggestions) - 5),
            "steps": steps,
            "elapsed_ms": int((time.time() - started) * 1000),
            "error": "",
        }
    def _model_diagnostics_slang_candidates(self, data: dict[str, Any]) -> list[dict[str, str]]:
        groups = data.get("groups") if isinstance(data.get("groups"), dict) else {}
        common_terms = {
            "什么",
            "这个",
            "那个",
            "就是",
            "可以",
            "没有",
            "真的",
            "一下",
            "今天",
            "明天",
            "昨天",
            "然后",
            "现在",
            "等等",
            "不是",
            "因为",
            "所以",
            "但是",
            "感觉",
            "可能",
            "应该",
            "好像",
            "知道",
            "看看",
            "消息",
            "图片",
        }
        reaction_terms = {"哈哈", "哈哈哈", "笑死", "草", "好的", "收到", "嗯嗯", "啊啊", "救命", "失败", "报错"}
        media_markers = ("[图片]", "[视频]", "[语音]", "[表情]", "[文件]", "[CQ:", "http://", "https://")
        candidates: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for group_id, group in groups.items():
            if not isinstance(group, dict):
                continue
            group_name = self._single_line(group.get("name") or group.get("group_name") or group_id, 40)
            terms = group.get("slang_terms") if isinstance(group.get("slang_terms"), list) else []
            meanings = group.get("slang_meanings") if isinstance(group.get("slang_meanings"), dict) else {}
            indexed: list[dict[str, Any]] = []
            for raw in terms:
                if isinstance(raw, dict):
                    indexed.append(raw)
                else:
                    indexed.append({"term": raw})
            for raw in indexed[:160]:
                term = self._single_line(raw.get("term") if isinstance(raw, dict) else raw, 50)
                if not term:
                    continue
                if isinstance(raw, dict) and self._single_line(raw.get("source"), 20) == "manual":
                    continue
                compact = re.sub(r"\s+", "", term)
                reason = ""
                if any(marker.lower() in term.lower() for marker in media_markers):
                    reason = "像媒体占位或链接，不像黑话"
                elif re.fullmatch(r"\d{5,}", compact):
                    reason = "像纯数字 ID，不像黑话"
                elif compact in common_terms:
                    reason = "像普通高频词，不像群内黑话"
                elif compact in reaction_terms:
                    reason = "像一次性语气/反应词，容易污染黑话"
                elif len(compact) <= 1 and not re.fullmatch(r"[a-zA-Z]+", compact):
                    reason = "过短，缺少可解释语义"
                elif len(compact) >= 18 and re.search(r"[。！？!?，,]", term):
                    reason = "像整句聊天内容，不像词条"
                else:
                    raw_meaning = meanings.get(term) if isinstance(meanings.get(term), dict) else {}
                    meaning = self._single_line(raw_meaning.get("meaning"), 80) if isinstance(raw_meaning, dict) else ""
                    confidence = self._float(raw_meaning.get("confidence")) if isinstance(raw_meaning, dict) else 0.0
                    count = self._int(raw.get("count")) if isinstance(raw, dict) else 0
                    if meaning and confidence < 0.35 and count <= 2:
                        reason = "低频且释义置信度很低"
                    elif meaning and any(marker in meaning for marker in ("无法判断", "语境不明", "不确定")) and count <= 2:
                        reason = "释义长期不确定，建议复核"
                if not reason:
                    continue
                key = (str(group_id), term)
                if key in seen:
                    continue
                seen.add(key)
                candidates.append(
                    {
                        "group_id": self._single_line(group_id, 40),
                        "group_name": group_name,
                        "term": term,
                        "reason": reason,
                        "count": str(self._int(raw.get("count")) if isinstance(raw, dict) else 0),
                    }
                )
                if len(candidates) >= 24:
                    return candidates
        return candidates
    def _model_diagnostics_pending_observation_candidates(self, data: dict[str, Any]) -> list[dict[str, str]]:
        profiles = data.get("worldbook_member_profiles") if isinstance(data.get("worldbook_member_profiles"), dict) else {}
        generic_reactions = {"哈哈", "哈哈哈", "笑死", "草", "好的", "收到", "嗯嗯", "啊啊啊", "救命", "离谱", "不是吧"}
        log_markers = ("Traceback", "[ERROR]", "[WARN]", "Exception", "File \"", "```", "ERROR", "WARN")
        candidates: list[dict[str, str]] = []
        seen_global: set[str] = set()
        for user_id, profile in profiles.items():
            if not isinstance(profile, dict):
                continue
            name = self._single_line(profile.get("name") or user_id, 40)
            pending = profile.get("pending_observations") if isinstance(profile.get("pending_observations"), list) else []
            seen_local: set[str] = set()
            for raw in pending[:12]:
                if not isinstance(raw, dict):
                    continue
                evidence = self._single_line(raw.get("evidence") or raw.get("content") or raw.get("title"), 140)
                content = self._single_line(raw.get("content") or evidence, 180)
                compact = re.sub(r"[\s，。！？!?~～…、,.]+", "", evidence)
                norm = compact.lower()
                reason = ""
                if not compact or len(compact) <= 2:
                    reason = "内容过短，难以沉淀成人物观察"
                elif compact in generic_reactions:
                    reason = "像临时语气反应，不像稳定人物信息"
                elif any(marker in evidence or marker in content for marker in log_markers):
                    reason = "像日志/代码片段，不适合写入关系网"
                elif norm in seen_local:
                    reason = "同一成员下重复观察"
                elif norm in seen_global:
                    reason = "跨成员重复泛化观察，可能没有辨识度"
                elif re.fullmatch(r"[\dA-Za-z_-]{8,}", compact):
                    reason = "像 ID 或文件名，不像人物观察"
                elif any(marker in evidence for marker in ("今天", "刚刚", "现在", "一会儿", "等下")) and self._int(raw.get("count")) <= 1:
                    reason = "像临时状态，缺少长期价值"
                seen_local.add(norm)
                seen_global.add(norm)
                if not reason:
                    continue
                candidates.append(
                    {
                        "user_id": self._single_line(user_id, 40),
                        "name": name,
                        "title": self._single_line(raw.get("title"), 50),
                        "evidence": evidence,
                        "reason": reason,
                    }
                )
                if len(candidates) >= 24:
                    return candidates
        return candidates
    def _model_diagnostics_companion_memory_candidates(self, data: dict[str, Any]) -> list[dict[str, str]]:
        users = data.get("users") if isinstance(data.get("users"), dict) else {}
        temporary_tokens = ("今天", "刚刚", "刚才", "现在", "今晚", "明天", "这次", "暂时", "一会儿", "等会儿", "刚睡醒", "刚下课")
        joke_tokens = ("开玩笑", "不是认真的", "随口", "口嗨", "逗你的", "反话", "阴阳怪气")
        log_markers = ("Traceback", "Error code:", "Exception", "[INFO]", "[WARN]", "[ERRO]", "[Core]", "```", "git ", "python ", "node ")
        internal_markers = ("提示词", "系统提示", "内部控制标签", "插件配置", "schema", "token", "cache hit", "PCTTS", "<pc_tts")
        profile_fields = {
            "strong_memories": "强记忆",
            "weak_preferences": "弱偏好",
            "user_traits": "用户画像",
            "interests": "兴趣/偏好",
            "boundaries": "边界/雷点",
            "relationship_notes": "关系线索",
            "speaking_style": "说话习惯",
        }

        def reason_for(text: str) -> str:
            if any(marker.lower() in text.lower() for marker in log_markers):
                return "像日志/代码/报错内容，不适合本地画像"
            if any(marker.lower() in text.lower() for marker in internal_markers):
                return "像系统或插件内部文本，不适合本地画像"
            if any(token in text for token in joke_tokens):
                return "带有玩笑/反讽不确定性，建议人工确认"
            if any(token in text for token in temporary_tokens) and not any(token in text for token in ("以后", "长期", "一直", "固定", "默认", "记住", "记得")):
                return "像临时状态，不像本地画像"
            if re.fullmatch(r"[\dA-Za-z_-]{8,}", re.sub(r"\s+", "", text)):
                return "像 ID 或文件名，不像用户画像"
            return ""

        candidates: list[dict[str, str]] = []
        for user_id, user in users.items():
            if not isinstance(user, dict):
                continue
            name = self._single_line(user.get("nickname") or user.get("name") or user_id, 40)
            memory = user.get("companion_memory") if isinstance(user.get("companion_memory"), dict) else {}
            raw_items = memory.get("items") if isinstance(memory.get("items"), list) else []
            for raw in raw_items[:24]:
                if not isinstance(raw, dict):
                    continue
                text = self._single_line(raw.get("text"), 160)
                reason = reason_for(text)
                if not reason:
                    continue
                candidates.append(
                    {
                        "user_id": self._single_line(user_id, 40),
                        "name": name,
                        "field": self._single_line(raw.get("kind") or "原始记录", 30),
                        "text": text,
                        "reason": reason,
                    }
                )
                if len(candidates) >= 24:
                    return candidates
            profile = memory.get("profile") if isinstance(memory.get("profile"), dict) else {}
            for key, label in profile_fields.items():
                value = profile.get(key)
                values = value if isinstance(value, list) else ([value] if value else [])
                for raw_text in values[:8]:
                    text = self._single_line(raw_text, 160)
                    reason = reason_for(text)
                    if not reason:
                        continue
                    candidates.append(
                        {
                            "user_id": self._single_line(user_id, 40),
                            "name": name,
                            "field": label,
                            "text": text,
                            "reason": reason,
                        }
                    )
                    if len(candidates) >= 24:
                        return candidates
        return candidates
    def _model_diagnostics_review_prompt(
        self,
        data: dict[str, Any],
        skill_candidates: list[dict[str, str]],
        slang_candidates: list[dict[str, str]],
        pending_candidates: list[dict[str, str]],
        memory_candidates: list[dict[str, str]],
        expression_candidates: list[dict[str, str]],
    ) -> str:
        skill_lines = [f"- {item.get('a')} / {item.get('b')}：{item.get('reason')}" for item in skill_candidates[:12]]
        slang_lines = [
            f"- {item.get('group_name') or item.get('group_id')}｜{item.get('term')}｜{item.get('reason')}｜次数:{item.get('count') or 0}"
            for item in slang_candidates[:18]
        ]
        pending_lines = [
            f"- {item.get('name') or item.get('user_id')}｜{item.get('reason')}｜{item.get('evidence')}"
            for item in pending_candidates[:18]
        ]
        memory_lines = [
            f"- {item.get('name') or item.get('user_id')}｜{item.get('field')}｜{item.get('reason')}｜{item.get('text')}"
            for item in memory_candidates[:18]
        ]
        expression_lines = [
            f"- {item.get('name') or item.get('user_id')}｜{item.get('reason')}｜{item.get('text')}"
            for item in expression_candidates[:18]
        ]
        return _render_page_background_prompt(
            key="background.troubleshooting.model_diagnostics",
            title="模型数据排障复核",
            content=(
                "你是陪伴插件的数据排障助手。请复核下面这些由本地规则挑出的候选项，只指出明显会影响模型理解的杂音。\n"
            "范围包括：技能相似项、群黑话杂音、关系网待确认观察、本地画像噪音、表达规则重复与污染。不要修改数据，不要发散，不要把正常口癖或真实群梗误报。\n"
            "表达学习候选由本地规则预筛：污染项关注日志、复制模型格式、政治敏感内容和过度标点；重复项关注同一来源内已启用/待审核规则的同模板、同证据或高相似变体，以及运行时规则预算占用。\n"
            "不要因为两条规则语气相似就建议删除；模板虽然相同但意图、关系阶段或情绪边界不兼容时应保留。auto_merge=false 的近似项只建议人工复核，不得声称已经合并。\n"
            "输出 1-10 条短建议，每条不超过 45 字，必须用分类前缀：技能｜、黑话｜、关系网｜、本地画像｜、表达学习｜。\n"
            "如果某一类没有明显问题，不要为了凑数输出。若全部无明显问题，输出“未发现明显模型数据杂音”。\n\n"
            "技能候选：\n" + ("\n".join(skill_lines) if skill_lines else "- 无") + "\n\n"
            "群黑话候选：\n" + ("\n".join(slang_lines) if slang_lines else "- 无") + "\n\n"
            "关系网待确认观察候选：\n" + ("\n".join(pending_lines) if pending_lines else "- 无") + "\n\n"
            "本地画像候选：\n" + ("\n".join(memory_lines) if memory_lines else "- 无") + "\n\n"
                "表达学习候选：\n" + ("\n".join(expression_lines) if expression_lines else "- 无")
            ),
        )
    def _attach_model_diagnostics_section_suggestions(self, sections: list[dict[str, Any]], suggestions: list[str]) -> None:
        section_by_key = {str(section.get("key")): section for section in sections if isinstance(section, dict)}
        prefix_map = {
            "技能": "skills",
            "黑话": "slang",
            "关系网": "worldbook",
            "本地画像": "memory",
            "表达学习": "expression",
        }
        for suggestion in suggestions:
            prefix = self._single_line(str(suggestion).split("｜", 1)[0], 20)
            key = prefix_map.get(prefix)
            section = section_by_key.get(key or "")
            if not section:
                continue
            items = section.setdefault("suggestions", [])
            if isinstance(items, list):
                items.append(self._single_line(suggestion, 180))
            section["model_count"] = self._int(section.get("model_count")) + 1
    async def _remember_troubleshooting_test_result(self, test_type: str, result: dict[str, Any]) -> None:
        if not test_type:
            return
        try:
            async with self.plugin._data_lock:
                raw = self.plugin.data.setdefault("troubleshooting_test_results", {})
                if not isinstance(raw, dict):
                    raw = {}
                    self.plugin.data["troubleshooting_test_results"] = raw
                raw[test_type] = self._sanitize_troubleshooting_test_result(result)
                if test_type.startswith("image_api_endpoint_"):
                    endpoint_results = sorted(
                        (
                            (key, value)
                            for key, value in raw.items()
                            if str(key).startswith("image_api_endpoint_") and isinstance(value, dict)
                        ),
                        key=lambda item: self._float(item[1].get("ran_at")),
                        reverse=True,
                    )
                    for stale_key, _ in endpoint_results[24:]:
                        raw.pop(stale_key, None)
                self.plugin._save_data_sync(sections={"troubleshooting_test_results"})
        except Exception as exc:
            logger.warning("保存排障测试结果失败: %s", self._single_line(exc, 120))
    def _troubleshooting_test_results(self, data: dict[str, Any]) -> dict[str, Any]:
        raw = data.get("troubleshooting_test_results")
        if not isinstance(raw, dict):
            return {}
        results: dict[str, Any] = {}
        users = data.get("users") if isinstance(data.get("users"), dict) else {}
        active_proactive_test = any(
            isinstance(user, dict)
            and str(user.get("planned_proactive_source") or "") == "troubleshooting"
            and isinstance(user.get("troubleshooting_proactive_restore"), dict)
            for user in users.values()
        )
        for key, value in raw.items():
            if not isinstance(value, dict):
                continue
            item = self._sanitize_troubleshooting_test_result(value)
            if key == "proactive_message" and item.get("pending") and not active_proactive_test:
                item.update(
                    {
                        "ok": False,
                        "pending": False,
                        "error": item.get("error") or "主动消息测试任务状态已丢失，请重新测试",
                        "detail": item.get("detail") or "没有找到正在等待执行的临时主动任务",
                        "ran_at": time.time(),
                        "ran_at_text": self.plugin._format_timestamp_elapsed(time.time()),
                    }
                )
            finished_at = self._float(item.get("finished_at")) or self._float(item.get("ran_at")) or time.time()
            elapsed_seconds = max(0.0, self._int(item.get("elapsed_ms")) / 1000.0)
            started_at = self._float(item.get("started_at")) or max(0.0, finished_at - elapsed_seconds)
            if not item.get("request_id") and not item.get("trace_id"):
                legacy_seed = f"{key}:{finished_at:.6f}:{item.get('title') or ''}"
                item["request_id"] = hashlib.sha256(legacy_seed.encode("utf-8")).hexdigest()[:12]
            item = self._finalize_test_diagnostics(
                str(key),
                item,
                started_at,
                finished_at=finished_at,
            )
            results[key] = item
        return results
    def _diagnostic_envelope(
        self,
        result: dict[str, Any] | None,
        *,
        test_type: str = "",
        duration_ms: int = 0,
        test_id: str = "",
    ) -> dict[str, Any]:
        source = dict(result) if isinstance(result, dict) else {}
        contract_version = DIAGNOSTIC_ENVELOPE_VERSION
        plugin = getattr(self, "plugin", None)
        getter = getattr(plugin, "_diagnostic_operations_contract", None)
        if callable(getter):
            try:
                contract = getter()
                if isinstance(contract, dict):
                    contract_version = self._single_line(contract.get("version"), 60) or contract_version
            except Exception:
                pass
        source_request_id = self._single_line(
            source.get("request_id") or source.get("trace_id"),
            32,
        )
        resolved_test_id = test_id or self._single_line(source.get("test_id"), 100)
        if not resolved_test_id and re.fullmatch(r"[a-fA-F0-9]{12,32}", source_request_id):
            resolved_test_id = diagnostic_test_id(
                test_type or source.get("type"),
                token=source_request_id.lower(),
            )
        envelope = normalize_diagnostic_result(
            source,
            test_type=test_type,
            duration_ms=duration_ms,
            test_id=resolved_test_id,
            contract_version=contract_version,
        )

        # The public envelope intentionally strips arbitrary model output. The
        # local operations page still needs bounded, redacted delivery details
        # and the legacy request id to make a failed test actionable.
        for key, limit in (
            ("request_id", 32),
            ("trace_id", 32),
            ("test_status", 16),
            ("error_code", 40),
            ("code", 80),
            ("exception_type", 120),
            ("title", 80),
            ("delivery_umo", 180),
            ("called_plugin", 100),
            ("called_plugin_name", 100),
            ("called_plugin_version", 60),
            ("called_plugin_api_version", 80),
            ("called_plugin_status_schema", 80),
            ("availability_source", 80),
        ):
            if source.get(key) not in (None, ""):
                envelope[key] = self._single_line(source.get(key), limit)
        for key, limit in (
            ("error", 1600),
            ("delivery_error", 1200),
            ("detail", 1200),
            ("diagnostic_detail", 4000),
            ("suggestion", 600),
            ("next_step", 600),
        ):
            if source.get(key) not in (None, ""):
                envelope[key] = self._safe_test_diagnostic_text(source.get(key), limit)
        for key in ("generated", "delivered"):
            if key in source:
                envelope[key] = bool(source.get(key))
        if "unsupported" in source:
            envelope["unsupported"] = bool(source.get("unsupported"))

        warnings = source.get("warnings") if isinstance(source.get("warnings"), list) else []
        envelope["warnings"] = [
            text
            for item in warnings[:8]
            if (text := self._safe_test_diagnostic_text(item, 800))
        ]
        envelope["steps"] = [
            {
                "key": self._single_line(step.get("key"), 40),
                "name": self._single_line(step.get("name"), 60) or "执行阶段",
                "status": self._single_line(step.get("status"), 16) or "info",
                "detail": self._safe_test_diagnostic_text(step.get("detail"), 800),
                "elapsed_ms": self._int(step.get("elapsed_ms")),
            }
            for step in (source.get("steps") if isinstance(source.get("steps"), list) else [])[:24]
            if isinstance(step, dict)
        ]
        envelope["diagnostic_entries"] = [
            {
                "elapsed_ms": self._int(entry.get("elapsed_ms")),
                "level": self._single_line(entry.get("level"), 16),
                "stage": self._single_line(entry.get("stage"), 60),
                "message": self._safe_test_diagnostic_text(entry.get("message"), 1200),
            }
            for entry in (
                source.get("diagnostic_entries")
                if isinstance(source.get("diagnostic_entries"), list)
                else []
            )[:32]
            if isinstance(entry, dict)
        ]
        envelope["suggestions"] = [
            text
            for item in (
                source.get("suggestions")
                if isinstance(source.get("suggestions"), list)
                else []
            )[:12]
            if (text := self._safe_test_diagnostic_text(item, 220))
        ]
        envelope["sections"] = [
            {
                "key": self._single_line(section.get("key"), 40),
                "title": self._single_line(section.get("title"), 60),
                "local_count": self._int(section.get("local_count")),
                "model_count": self._int(section.get("model_count")),
                "suggestions": [
                    text
                    for item in (
                        section.get("suggestions")
                        if isinstance(section.get("suggestions"), list)
                        else []
                    )[:8]
                    if (text := self._safe_test_diagnostic_text(item, 220))
                ],
            }
            for section in (
                source.get("sections") if isinstance(source.get("sections"), list) else []
            )[:6]
            if isinstance(section, dict)
        ]
        for key in ("started_at", "finished_at"):
            if source.get(key) not in (None, ""):
                envelope[key] = self._float(source.get(key))
        return envelope
    def _sanitize_troubleshooting_test_result(self, result: dict[str, Any]) -> dict[str, Any]:
        return self._diagnostic_envelope(result)
    @staticmethod
    def _troubleshooting_test_title(test_type: str) -> str:
        return {
            "image_generation": "图片生成链路测试",
            "image_generation_text2img": "文生图链路测试",
            "image_generation_selfie": "自拍参考图链路测试",
            "image_api_endpoint": "在线图片 API 单独测试",
            "tts_generation": "TTS 生成与投递测试",
            "screen_peek": "窥屏链路测试",
            "qzone_integration": "QQ 空间链路测试",
            "proactive_message": "主动消息链路测试",
            "model_diagnostics": "模型数据排障",
            "skill_similarity": "技能相似项检查",
            "weather_api": "天气 API 请求测试",
            "balance_api": "余额接口请求测试",
            "web_search": "搜索接口请求测试",
        }.get(test_type, "排障链路测试")
    def _troubleshooting_recent_events(
        self,
        *,
        diagnostics: list[dict[str, Any]],
        proactive_tasks: dict[str, Any],
        proactive_candidates: dict[str, Any],
        token_stats: dict[str, Any],
        passive_no_reply: dict[str, Any] | None = None,
        persona_routing_warnings: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []

        def add(
            level: str,
            source: str,
            title: str,
            detail: str = "",
            *,
            ts: float = 0,
            action: str = "",
            jump: str = "",
            warning_type: str = "",
            warning_code: str = "",
        ) -> None:
            resolved_type = warning_type or (
                self._troubleshooting_semantic_warning_type(warning_code)
                if warning_code
                else self._troubleshooting_warning_type("event", source, title)
            )
            events.append(
                {
                    "level": level,
                    "source": source,
                    "title": self._single_line(title, 90),
                    "detail": self._single_line(detail, 220),
                    "action": self._single_line(action, 160),
                    "jump": self._single_line(jump, 40),
                    "ts": self._float(ts),
                    "time": self.plugin._format_timestamp_elapsed(ts) if ts else "",
                    "warning_type": resolved_type,
                    "warning_code": warning_code,
                }
            )

        for item in diagnostics:
            level = self._single_line(item.get("level"), 12)
            if level not in {"error", "warn"}:
                continue
            add(
                level,
                "配置诊断",
                item.get("title", ""),
                item.get("text", ""),
                action=item.get("action", ""),
                jump="troubleshooting",
                warning_type=self._single_line(item.get("warning_type"), 64),
                warning_code=self._single_line(item.get("warning_code"), 120),
            )

        for item in self._active_persona_routing_warnings(persona_routing_warnings)[:120]:
            if not isinstance(item, dict):
                continue
            requested = self._single_line(item.get("requested_persona_id"), 96) or "未解析"
            active = self._single_line(item.get("active_persona_id"), 96) or "未激活"
            reason = self._single_line(item.get("reason_code"), 100) or "unknown"
            count = max(1, self._int(item.get("count")))
            disposition = self._single_line(item.get("disposition"), 24) or "fallback"
            warning_code = self._single_line(item.get("code"), 120) or "persona.route.unknown"
            # An empty plugin-specific persona is valid in single-persona
            # mode: AstrBot's selected conversation persona is authoritative.
            # Hide records written by older versions so the panel does not
            # keep showing a resolved, non-actionable warning forever.
            if (
                warning_code == "persona.route.plugin_persona_unspecified"
                and not bool(getattr(self.plugin, "enable_multi_persona_mode", False))
            ):
                continue
            detail = "；".join(
                part
                for part in (
                    f"AstrBot 人格 {requested}",
                    f"插件人格 {active}",
                    f"原因 {reason}",
                    f"已合并 {count} 次" if count > 1 else "",
                    f"会话 {self._single_line(item.get('window_key'), 100)}" if item.get("window_key") else "",
                )
                if part
            )
            title = (
                "插件人格未指定"
                if warning_code == "persona.route.plugin_persona_unspecified"
                else "被动消息已回退主人格"
                if disposition.startswith("fallback")
                else "旧插件窗口绑定已忽略"
                if disposition == "ignored"
                else "主动消息人格校验未通过"
            )
            add(
                self._single_line(item.get("level"), 12) or "warn",
                "人格路由",
                title,
                detail,
                ts=self._float(item.get("last_ts")),
                action="检查 AstrBot 会话规则、已启用人格和人格配置健康状态",
                jump="config",
                warning_code=warning_code,
            )

        for item in proactive_tasks.get("audit_items", [])[:40]:
            status = self._single_line(item.get("status"), 24)
            if status not in {"failed", "dropped", "deferred"}:
                continue
            note = self._single_line(item.get("note"), 180)
            if status in {"dropped", "deferred"} and not self._proactive_audit_note_needs_troubleshooting(note):
                continue
            level = "error" if status == "failed" else "warn"
            title = item.get("topic") or item.get("reason") or item.get("note") or "主动执行异常"
            detail = "；".join(
                part
                for part in [
                    f"用户 {item.get('user_label') or item.get('user_id') or '-'}",
                    f"动作 {item.get('action') or 'message'}",
                    note,
                    item.get("text_preview") or "",
                ]
                if part
            )
            add(
                level,
                "主动审计",
                title,
                detail,
                ts=self._float(item.get("updated_ts") or item.get("created_ts")),
                jump="proactive",
                warning_code=self._troubleshooting_proactive_warning_code("audit", item, note),
            )

        for item in proactive_candidates.get("items", [])[:40]:
            if self._single_line(item.get("status"), 24) != "blocked":
                continue
            note = self._single_line(item.get("note"), 180)
            if self._proactive_candidate_block_is_normal(note):
                continue
            title = item.get("topic") or item.get("reason") or "主动候选被拦截"
            detail = "；".join(
                part
                for part in [
                    f"用户 {item.get('user_label') or item.get('user_id') or '-'}",
                    f"动作 {item.get('action') or 'message'}",
                    note,
                ]
                if part
            )
            add(
                "warn",
                "主动候选",
                title,
                detail,
                ts=self._float(item.get("last_seen_ts") or item.get("created_ts")),
                jump="proactive",
                warning_code=self._troubleshooting_proactive_warning_code("candidate", item, note),
            )

        for item in self._active_token_failures(token_stats.get("recent", []), limit=50):
            title = f"{self._token_task_label(item.get('task'))}失败"
            detail = "；".join(
                part
                for part in [
                    f"Provider {item.get('provider') or '-'}",
                    item.get("error") or "无错误详情",
                ]
                if part
            )
            add(
                "error",
                "模型调用",
                title,
                detail,
                ts=self._float(item.get("ts")),
                jump="tokens",
                warning_code=f"model_call.{re.sub(r'[^a-z0-9_]+', '_', self._single_line(item.get('task'), 40).lower()).strip('_') or 'unknown'}",
            )

        passive_items = passive_no_reply.get("items", []) if isinstance(passive_no_reply, dict) else []
        for item in passive_items[:40]:
            if not isinstance(item, dict):
                continue
            count = self._int(item.get("count"))
            source = self._single_line(item.get("source"), 40) or "被动未回复"
            reason = self._single_line(item.get("reason"), 100) or "未说明原因"
            inbound = self._single_line(item.get("last_inbound"), 100)
            detail = "；".join(
                part
                for part in [
                    f"已合并 {count} 次" if count > 1 else "最近 1 次",
                    f"会话 {self._single_line(item.get('last_session'), 80)}" if item.get("last_session") else "",
                    f"消息 {inbound}" if inbound else "",
                    self._single_line(item.get("last_detail"), 120),
                ]
                if part
            )
            add(
                self._single_line(item.get("level"), 12) or "info",
                source,
                reason,
                detail,
                ts=self._float(item.get("last_ts")),
                action="同类原因已合并计数，刷新后可查看最近样本",
                jump="troubleshooting",
                warning_code=f"passive_no_reply.{re.sub(r'[^a-z0-9_]+', '_', self._single_line(item.get('key'), 40).lower()).strip('_') or hashlib.sha256(reason.encode('utf-8')).hexdigest()[:12]}",
            )

        events.sort(key=lambda item: self._float(item.get("ts")), reverse=True)
        return events
    def _proactive_audit_note_needs_troubleshooting(self, note: str) -> bool:
        text = self._single_line(note, 180)
        if not text:
            return False
        normal_tokens = (
            "主动行为失败或不适合发送",
            "不适合发送",
            "用户明确休息",
            "休息中",
            "免打扰",
            "已有更早",
            "调度过滤",
            "已避开扎堆",
            "按日内节奏延后",
            "冷却",
            "间隔",
            "频率",
            "额度",
            "低分",
            "未达到阈值",
            "窗口已过期",
            "已经活跃过",
            "多来源合并",
            "候选已过期",
        )
        if any(token in text for token in normal_tokens):
            return False
        error_tokens = (
            "异常",
            "报错",
            "Traceback",
            "Error",
            "Exception",
            "provider",
            "模型",
            "超时",
            "不可用",
            "发送失败",
            "生成失败",
            "调用失败",
            "保存失败",
            "处理失败",
            "database is locked",
        )
        return any(token in text for token in error_tokens)
    def _troubleshooting_checks(
        self,
        *,
        data: dict[str, Any],
        users: dict[str, Any],
        groups: dict[str, Any],
        diagnostics: list[dict[str, Any]],
        proactive_tasks: dict[str, Any],
        proactive_candidates: dict[str, Any],
        token_stats: dict[str, Any],
        cache: dict[str, Any],
        tts: dict[str, Any],
        sqlite_status: dict[str, Any],
    ) -> list[dict[str, Any]]:
        checks: list[dict[str, Any]] = []

        def add(level: str, title: str, text: str, action: str = "", jump: str = "", warning_code: str = "") -> None:
            item = {
                "level": level,
                "title": self._single_line(title, 90),
                "text": self._single_line(text, 240),
                "action": self._single_line(action, 180),
                "jump": self._single_line(jump, 40),
                "warning_code": warning_code,
                "warning_type": self._troubleshooting_semantic_warning_type(warning_code)
                if warning_code
                else self._troubleshooting_warning_type("check", title),
            }
            checks.append(item)

        llm_blocks = []
        raw_llm_blocks = data.get("group_llm_reply_blocks")
        if isinstance(raw_llm_blocks, dict):
            for group_id, item in raw_llm_blocks.items():
                if not isinstance(item, dict) or not bool(item.get("enabled")):
                    continue
                gid = self._single_line(item.get("group_id") or group_id, 80)
                if not gid:
                    continue
                group = groups.get(gid) if isinstance(groups, dict) else None
                name = self._single_line((group or {}).get("name") if isinstance(group, dict) else "", 40)
                llm_blocks.append({"group_id": gid, "name": name, "updated_at": item.get("updated_at")})
        if llm_blocks:
            first = llm_blocks[0]
            label = f"{first.get('name')}({first.get('group_id')})" if first.get("name") else str(first.get("group_id") or "-")
            add(
                "warn",
                "存在群级 LLM 回复熔断",
                f"{len(llm_blocks)} 个群已关闭所有 LLM 回复，首个：{label}。这是手动开关状态，不会被最近记录清理。",
                "在对应群发送：陪伴群 开启LLM",
                "troubleshooting",
                "group.llm_breaker",
            )

        enabled_users = [
            item
            for item in users.values()
            if isinstance(item, dict)
            and (
                item.get("proactive_private_enabled") is True
                or (
                    isinstance(item.get("unified_profile_capabilities"), dict)
                    and item["unified_profile_capabilities"].get("proactive_private_enabled") is True
                )
            )
        ]
        runtime = proactive_tasks.get("runtime", {})
        daily_limit = self._int(getattr(self.plugin, "max_daily_messages", 0))
        budget = token_stats.get("budget", {})
        if not enabled_users:
            add("warn", "主动消息没有私聊对象", "当前没有启用的私聊对象，主动消息不会有目标。", "到私聊页新增或启用对象", "private", "proactive.no_enabled_users")
        elif daily_limit <= 0:
            add("warn", "私聊主动总额度为 0", "每日主动上限为 0 时，主动念头、候选、主动行为生成与发送均已停止。", "到模块配置调高每日主动上限", "modules", "proactive.daily_limit_zero")
        elif not runtime.get("healthy"):
            add("warn", "主动循环心跳不新鲜", runtime.get("last_tick_error") or "最近没有检测到主动循环心跳。", "查看主动页的循环状态", "proactive", "proactive.loop_stale")
        else:
            add("ok", "主动循环可运行", f"启用对象 {len(enabled_users)} 个，最近心跳 {runtime.get('last_tick_started') or '-'}。", "", "proactive")

        if budget.get("exceeded"):
            add("error", "今日 Token 硬限额已耗尽", f"今日已用 {budget.get('used')}，硬限额 {budget.get('limit')}。", "调高每日 Token 限额或等待明日重置", "tokens")
        elif budget.get("soft_active"):
            add("warn", "Token 软限额正在暂缓后台任务", f"今日已用 {budget.get('used')}，软限额 {budget.get('soft_limit')}；主动生图、新闻、创作等低优先级任务会延后。", "到 Token 页或模块配置检查限额", "tokens", "token.soft_limit_active")
        else:
            add("ok", "Token 预算未阻塞", f"今日已用 {budget.get('used', 0)}；软限额剩余 {budget.get('soft_remaining') if budget.get('soft_remaining') is not None else '不限'}。", "", "tokens")

        photo_enabled = bool(getattr(self.plugin, "enable_photo_text_action", False))
        photo_available = bool(getattr(self.plugin, "_photo_text_available", lambda *args, **kwargs: False)())
        proactive_scope_limit_getter = getattr(self.plugin, "_photo_generation_scope_daily_limit", None)
        proactive_scope_limit = (
            self._int(proactive_scope_limit_getter("proactive"), -1, -1, 100)
            if callable(proactive_scope_limit_getter)
            else self._int(getattr(self.plugin, "photo_generation_proactive_max_daily", -1), -1, -1, 100)
        )
        proactive_scope_unlimited = proactive_scope_limit < 0
        photo_blocked = [
            item for item in proactive_candidates.get("items", [])
            if "photo_text" in str(item.get("action") or "") and str(item.get("status") or "") == "blocked"
        ]
        photo_blocked_abnormal = [
            item for item in photo_blocked
            if not self._proactive_candidate_block_is_normal(self._single_line(item.get("note"), 180))
        ]
        if not photo_enabled:
            add("warn", "主动带图功能未开启", "enable_photo_text_action 关闭时不会生成主动图片。", "到功能开关打开主动拍照/生图", "config", "image.proactive_disabled")
        elif not photo_available:
            if proactive_scope_unlimited:
                add(
                    "warn",
                    "主动带图当前不可用",
                    "Bot 主动生图范围额度为不限量（-1）；当前阻塞不是该范围额度耗尽，"
                    "请继续检查生图后端、Token 软限额、每用户主动带图上限或用户关系角色。",
                    "检查生图后端、Token 预算和“每日主动带图上限”",
                    "modules",
                    "image.proactive_backend_unavailable",
                )
            else:
                add(
                    "warn",
                    "主动带图当前不可用",
                    f"Bot 主动生图范围额度为 {proactive_scope_limit}；可能是范围额度已用完、生图后端不可用，"
                    "Token 软限额暂缓，或当前对象不允许 photo_text。",
                    "检查生图后端、每日生图上限和用户关系角色",
                    "modules",
                    "image.proactive_backend_unavailable",
                )
        elif photo_blocked_abnormal:
            add("warn", "近期带图候选被拦截", self._single_line(photo_blocked_abnormal[0].get("note"), 160) or "最近 photo_text 候选没有进入发送。", "到主动页筛选 photo_text", "proactive", "image.proactive_candidate_blocked")
        else:
            add("ok", "主动带图链路可尝试", "开关和可用性检查通过；是否出现取决于主动动机、天气/日程和候选权重。", "", "proactive")

        if bool(tts.get("enhancement_enabled")):
            if bool(tts.get("provider_available")):
                add("ok", "TTS 合成后端可用", f"模式 {tts.get('mode')}，语种 {tts.get('language')}，后端 {tts.get('provider_label') or '-'}。", "", "modules")
            else:
                add("warn", "TTS 强化开启但合成后端不可用", "插件能处理 TTS 标签，但当前选择的 AstrBot TTS Provider 或 MiMo Voice Clone 联动不可用。", "检查 TTS 合成后端；MiMo 模式需启用目标插件并保留 mimo_tts_speak 工具", "modules", "tts.provider_unavailable")
        else:
            add("info", "TTS 强化未开启", "模型不应被要求生成 TTS 标签；如仍出现标签，发送前会清理。", "", "modules")

        sqlite_bad = [item for item in sqlite_status.get("items", []) if item.get("level") in {"warn", "error"}]
        if sqlite_bad:
            add("warn", "SQLite 并发状态需要关注", sqlite_bad[0].get("text") or "有数据库未处于 WAL 或检查失败。", "重启插件后查看是否仍有 database is locked", "troubleshooting", "sqlite.wal")
        else:
            add("ok", "SQLite WAL 检查通过", f"已检查 {len(sqlite_status.get('items', []))} 个数据库文件。", "", "troubleshooting")

        token_errors = self._active_token_failures(token_stats.get("recent", []))
        if token_errors:
            first = token_errors[0]
            add("error", "最近存在模型调用失败", first.get("error") or f"{first.get('task') or '任务'} 调用失败。", "到 Token 页查看失败任务和 provider", "tokens")
        else:
            add("ok", "近期模型调用无待处理失败", "近 30 分钟内没有未恢复的模型调用失败；历史失败可在 Token 页查看。", "", "tokens")

        image_cache = cache.get("private_image_vision", {})
        if image_cache.get("enabled"):
            add("ok", "图片视觉缓存已开启", f"当前缓存 {image_cache.get('items', 0)}/{image_cache.get('max_items') or '不限'} 条。", "", "image-cache")
        else:
            add("info", "图片视觉缓存未开启", "重复表情包会重复调用视觉模型，但不影响首次识图。", "到模块配置开启重复图片缓存", "modules")
        provider_runtime = image_cache.get("provider_runtime") if isinstance(image_cache.get("provider_runtime"), dict) else {}
        provider_candidates = provider_runtime.get("candidates") if isinstance(provider_runtime.get("candidates"), list) else []
        usable_vision = [
            item for item in provider_candidates
            if isinstance(item, dict) and item.get("available") and item.get("supports_image") and not item.get("cooldown")
        ]
        provider_cooldowns = provider_runtime.get("cooldowns") if isinstance(provider_runtime.get("cooldowns"), list) else []
        last_success = provider_runtime.get("last_success") if isinstance(provider_runtime.get("last_success"), dict) else {}
        vision_priority = self._single_line(provider_runtime.get("priority"), 40) or "astrbot_first"
        vision_priority_label = {
            "astrbot_first": "AstrBot 图片转文字优先",
            "plugin_first": "插件识图模型优先",
            "recent_success_first": "近期成功模型优先",
        }.get(vision_priority, "AstrBot 图片转文字优先")
        if provider_runtime.get("error"):
            add("warn", "私聊图片识别调度状态读取失败", provider_runtime.get("error") or "无法读取当前视觉模型状态。", "刷新排障页或查看日志", "troubleshooting", "vision.runtime_unreadable")
        elif not usable_vision:
            add(
                "warn",
                "私聊图片识别暂无可用模型",
                f"候选 {len(provider_candidates)} 个，但没有同时满足可用、支持图片且不在冷却的模型。",
                "检查快速配置/精准配置里的插件识图模型，或等待临时降权结束",
                "config",
                "vision.no_available_provider",
            )
        elif last_success.get("provider_id"):
            add(
                "ok",
                f"私聊图片识别：{vision_priority_label}",
                f"最近成功视觉模型：{last_success.get('provider_id')}（{last_success.get('source') or '来源未知'}，{last_success.get('time') or '刚刚'}）；当前可用 {len(usable_vision)} 个。首选失败时会继续切换后续候选。",
                "",
                "troubleshooting",
            )
        else:
            first_provider = usable_vision[0].get("provider_id") if usable_vision and isinstance(usable_vision[0], dict) else "-"
            add("info", "私聊图片识别候选模型可用", f"当前可用 {len(usable_vision)} 个，首选 {first_provider}；成功一次后会记录为视觉恢复候选。", "", "troubleshooting")
        if provider_cooldowns:
            first_cooldown = provider_cooldowns[0] if isinstance(provider_cooldowns[0], dict) else {}
            add(
                "warn",
                "有识图模型被临时降权",
                f"{first_cooldown.get('provider_id') or '-'}：{first_cooldown.get('error') or '最近调用失败'}；到期 {first_cooldown.get('until') or '-'}。",
                "如果反复出现，换掉插件识图模型或调高单次超时",
                "config",
                "vision.provider_cooldown",
            )

        diag_warns = [item for item in diagnostics if item.get("level") in {"warn", "error"}]
        if diag_warns:
            add("warn", "配置诊断仍有待处理项", f"{len(diag_warns)} 项需要关注：{diag_warns[0].get('title') or '-'}。", diag_warns[0].get("action") or "查看下方最近异常", "troubleshooting", "diagnostic.pending")
        else:
            add("ok", "配置诊断无待处理警告", "现有未屏蔽诊断项没有 warn/error。", "", "dashboard")
        return checks
    @_multi_persona_page_context
    async def get_token_stats(self) -> dict[str, Any]:
        try:
            async with self.plugin._data_lock:
                usage = deepcopy(self.plugin.data.get("token_usage", {}))
                balance_state = deepcopy(self.plugin.data.get("balance_awareness", {}))
            stats = self._token_stats_payload(usage, balance_state)
            self._attach_multi_persona_token_stats(stats)
            return self._ok(stats)
        except Exception as exc:
            logger.error(f"获取 Token 统计失败: {exc}", exc_info=True)
            return self._exception_error(str(exc))
    @_multi_persona_page_context
    async def reset_token_stats(self) -> dict[str, Any]:
        try:
            async with self.plugin._data_lock:
                self.plugin.data["token_usage"] = {}
                balance_state = deepcopy(self.plugin.data.get("balance_awareness", {}))
                self.plugin._save_data_sync(sections={"token_usage"})
            return self._ok(self._token_stats_payload({}, balance_state))
        except Exception as exc:
            logger.error(f"重置 Token 统计失败: {exc}", exc_info=True)
            return self._exception_error(str(exc))
    def _build_diagnostics(self, users: dict[str, Any], groups: dict[str, Any]) -> list[dict[str, str]]:
        items: list[dict[str, str]] = []

        def add(level: str, title: str, text: str, action: str = "", warning_code: str = "") -> None:
            item = {"level": level, "title": title, "text": text, "action": action}
            if warning_code:
                item["warning_code"] = warning_code
                item["warning_type"] = self._troubleshooting_semantic_warning_type(warning_code)
            items.append(item)

        features = self._feature_flags()
        providers = self._provider_settings()
        group_mode = str(getattr(self.plugin, "group_access_mode", "whitelist") or "whitelist")
        whitelist = self.plugin._configured_group_ids()
        blacklist = self.plugin._configured_group_blacklist_ids()
        enabled_users = sum(
            1
            for item in users.values()
            if isinstance(item, dict)
            and (
                item.get("proactive_private_enabled") is True
                or (
                    isinstance(item.get("unified_profile_capabilities"), dict)
                    and item["unified_profile_capabilities"].get("proactive_private_enabled") is True
                )
            )
        )
        enabled_groups = sum(1 for item in groups.values() if isinstance(item, dict) and item.get("enabled", True))

        if getattr(self.plugin, "enabled", False):
            add("ok", "插件已启用", "后台主动检查与事件处理会正常运行")
        else:
            add("error", "插件未启用", "当前不会进行私聊主动陪伴或群聊观察", "在配置中打开 enabled")

        if providers.get("LLM_PROVIDER_ID") or getattr(self.plugin, "llm_provider_id", ""):
            add("ok", "主模型可见", providers.get("LLM_PROVIDER_ID") or "运行态已配置")
        else:
            add("info", "主模型留空", "会回退到 AstrBot 默认模型；建议为陪伴插件单独配置主模型")

        intensity = self._proactive_intensity_summary()
        if intensity.get("enabled"):
            effective = intensity.get("effective") if isinstance(intensity.get("effective"), dict) else {}
            add(
                "warn",
                f"正在使用主动强度预设：{intensity.get('label') or intensity.get('preset')}",
                (
                    f"私聊有效上限 {effective.get('max_daily_messages_text') or effective.get('max_daily_messages')}，"
                    f"空闲 {effective.get('idle_minutes')} 分钟，"
                    f"最小间隔 {effective.get('min_interval_minutes')} 分钟；"
                    f"群唤醒冷却 {effective.get('group_wakeup_cooldown_seconds')} 秒，"
                    f"群插话间隔 {effective.get('group_interject_min_interval_minutes')} 分钟，"
                    f"群插话上限 {effective.get('group_interject_max_daily_text') or effective.get('group_interject_max_daily')}，"
                    f"兴趣唤醒概率 {round(float(effective.get('group_wakeup_interest_probability') or 0) * 100)}%。"
                    f"{'当前最高档会忽略 Token 软限额降载；' if effective.get('ignore_token_soft_limit') else ''}"
                    "预设只覆盖运行态有效频率，不改写手动参数，也不会绕过免打扰、休息、用户拒绝、隐私和硬限额。"
                ),
                "需要恢复原配置时，将“主动强度预设”改为关闭",
                "proactive.intensity_preset",
            )
        else:
            add(
                "info",
                "主动强度预设未启用",
                "当前完全沿用手动主动频率参数；如果想要高频主动，可在配置 → 功能开关 → 通用能力里选择预设。",
            )

        tts_summary = self._tts_runtime_summary(users)
        if tts_summary.get("enhancement_enabled"):
            if tts_summary.get("provider_available"):
                add(
                    "ok",
                    "TTS 语音链路可用",
                    f"模式 {tts_summary.get('mode')}，语种 {tts_summary.get('language')}，真实合成后端：{tts_summary.get('provider_label')}",
                )
            elif tts_summary.get("settings_enabled"):
                add(
                    "warn",
                    "TTS 配置已开但合成后端不可用",
                    f"会话 {tts_summary.get('umo') or '-'} 已启用 TTS 设置，但当前取不到 AstrBot TTS Provider 或 MiMo Voice Clone 服务",
                    "检查 TTS 合成后端；MiMo 模式需启用目标插件并保留 mimo_tts_speak 工具",
                    "tts.provider_unavailable",
                )
            else:
                add(
                    "warn",
                    "TTS 强化已开但没有可用合成后端",
                    "本插件能处理 <tts> 标签和文本转换；真正合成音频需要 AstrBot 会话 TTS Provider 或 MiMo Voice Clone 插件",
                    "在 TTS 配置中选择并启用一种真实语音合成后端",
                    "tts.provider_unavailable",
                )
        else:
            add(
                "info",
                "TTS 强化未开启",
                "VOICE_PROMPT_PROVIDER_ID / TTS文本转换模型都是文本模型；语音合成模型请在 AstrBot TTS provider 中配置",
            )

        if enabled_users:
            add("ok", "私聊对象已就绪", f"已启用 {enabled_users} 个私聊对象")
        else:
            add("warn", "暂无启用的私聊对象", "私聊主动陪伴没有明确目标", "在私聊页新增对象或配置 target_user_ids", "proactive.no_enabled_users")

        max_daily = int(getattr(self.plugin, "max_daily_messages", 0) or 0)
        effective_max_daily = max_daily
        max_daily_getter = getattr(self.plugin, "_runtime_max_daily_messages", None)
        if callable(max_daily_getter):
            try:
                effective_max_daily = int(max_daily_getter() or 0)
            except Exception:
                effective_max_daily = max_daily
        if effective_max_daily > 0:
            limit_formatter = getattr(self.plugin, "_format_proactive_daily_limit", None)
            limit_is_unlimited = getattr(self.plugin, "_proactive_daily_limit_is_unlimited", None)
            effective_limit_text = limit_formatter(effective_max_daily) if callable(limit_formatter) else str(effective_max_daily)
            detail = f"每日有效上限 {effective_limit_text}"
            if effective_max_daily != max_daily:
                detail += f"（手动配置 {max_daily} 条）"
            if callable(limit_is_unlimited) and limit_is_unlimited(effective_max_daily):
                detail += "，当前使用最高档主动策略"
            add("ok", "私聊主动额度可用", detail)
        else:
            add("warn", "私聊主动已关闭", "每日主动上限为 0", "在模块配置里调高每日主动上限", "proactive.daily_limit_zero")

        if getattr(self.plugin, "enable_daily_token_soft_limit", True):
            soft_limit = int(getattr(self.plugin, "daily_token_soft_limit", 0) or 0)
            today_tokens = int(getattr(self.plugin, "_today_llm_token_total", lambda: 0)() or 0)
            ignore_soft_limit = bool(self._proactive_intensity_summary().get("effective", {}).get("ignore_token_soft_limit"))
            if soft_limit > 0 and today_tokens >= soft_limit:
                if ignore_soft_limit:
                    add(
                        "warn",
                        "Token 软限额已触发但最高档放行",
                        f"今日已用约 {today_tokens} Token；当前主动强度最高档会忽略软限额降载，低优先级任务仍可继续运行",
                        warning_code="token.soft_limit_ignored",
                    )
                else:
                    add(
                        "warn",
                        "每日 Token 软限额已接管",
                        f"今日已用约 {today_tokens} Token，低优先级后台 LLM 任务会暂缓",
                        warning_code="token.soft_limit_active",
                    )
            elif soft_limit > 0:
                add("ok", "每日 Token 软限额已启用", f"软限额 {soft_limit}，当前约 {today_tokens}")
            else:
                add("info", "每日 Token 软限额未设置", "只使用每日硬限额")
        else:
            add("info", "每日 Token 软限额已关闭", "功能全开时后台任务会按各自开关正常运行")

        if features.get("enable_companion_memory"):
            add("ok", "私聊本地画像已启用", "按私聊对象整理偏好、边界、关系线索和重要事实")
        else:
            add("info", "私聊本地画像已关闭", "不会新增私聊本地画像，已有资料仍可管理")
        if features.get("enable_expression_learning"):
            add("ok", "通用表达学习已启用", "按学习页设置的来源与范围用于私聊、主动私聊和群聊")
        else:
            add("info", "通用表达学习已关闭", "不会继续归纳或注入表达规则")

        if features.get("enable_livingmemory_integration"):
            living_summary = self._livingmemory_summary()
            living_level = "warn" if living_summary.get("conflict") else ("ok" if living_summary.get("compatible_available") else "warn")
            if living_summary.get("conflict_warning"):
                living_text = str(living_summary.get("conflict_warning") or "")
            elif living_summary.get("selected_plugin_name"):
                living_text = f"当前使用：{living_summary.get('selected_plugin_name')}"
            else:
                living_text = "已启用协同，但当前未检测到可用记忆插件"
            add(
                living_level,
                "记忆插件协同",
                living_text,
                warning_code="memory.integration_conflict" if living_summary.get("conflict") else "memory.integration_unavailable",
            )

        if features.get("enable_bilibili_integration"):
            bili_available = bool(getattr(self.plugin, "_bilibili_available", lambda: False)())
            add(
                "ok" if bili_available else "info",
                "B站 AI Bot 联动",
                "已检测到 B站 AI Bot 或观看日志" if bili_available else "联动开关已开，但暂未检测到 B站 AI Bot 实例或日志",
            )

        if features.get("enable_reading_archive_integration"):
            archive_available = bool(getattr(self.plugin, "_reading_archive_available", lambda: False)())
            add(
                "ok" if archive_available else "info",
                "资料归档素材",
                "已检测到可用素材能力" if archive_available else "开关已开，但暂未检测到可用素材能力",
            )

        if features.get("enable_photo_text_action") and getattr(self.plugin, "enable_local_photo_load_guard", False):
            load_state = getattr(self.plugin, "_local_photo_generation_load_state", lambda: {})()
            if isinstance(load_state, dict):
                if load_state.get("available"):
                    add(
                        "warn" if load_state.get("busy") else "ok",
                        "本地生图负载保护",
                        str(load_state.get("reason") or "负载正常"),
                        warning_code="image.local_load_busy" if load_state.get("busy") else "",
                    )
                else:
                    add("info", "本地生图负载保护未采样", str(load_state.get("reason") or "无法读取系统负载"))

        if features.get("enable_photo_text_action") and getattr(self.plugin, "_external_photo_available", lambda: False)():
            model_checker = getattr(self.plugin, "_external_image_model_misconfiguration_note", None)
            model_note = model_checker() if callable(model_checker) else ""
            if model_note:
                add(
                    "error",
                    "在线图片模型配置错误",
                    model_note,
                    "把 EXTERNAL_IMAGE_API_MODEL 改成该平台的图片模型名，不要填聊天模型",
                )

        llm_perception_available = bool(getattr(self.plugin, "_llmperception_available", lambda: False)())
        if llm_perception_available:
            if features.get("enable_environment_perception"):
                add(
                    "warn",
                    "检测到 LLMPerception 插件",
                    "本插件已内置时间、节假日、农历节气和平台环境感知；两者同时启用会重复注入并增加 Token 消耗",
                    "建议手动二选一；本插件不会再自动改写 enable_environment_perception",
                    "integration.environment_duplicate",
                )
            else:
                add("ok", "环境感知由外部插件接管", "检测到 LLMPerception，且本插件内置环境感知当前为关闭")

        if features.get("enable_creative_writing"):
            projects = self._creative_summary({"creative_projects": getattr(self.plugin, "data", {}).get("creative_projects", [])})
            active = projects.get("active_projects", 0)
            add(
                "ok" if active else "info",
                "私下创作行为",
                f"当前进行中创作 {active} 个" if active else "已开启；会在生活/梦境触发后慢慢开坑",
            )

        if getattr(self.plugin, "enable_group_companion", False):
            if group_mode == "whitelist" and not whitelist:
                add("warn", "群聊白名单为空", "白名单模式下所有群都会被拦截", "在配置页加入群号或切换为黑名单模式", "group.whitelist_empty")
            elif group_mode == "blacklist":
                add("ok", "群聊黑名单模式", f"已屏蔽 {len(blacklist)} 个群，其余群可观察")
            else:
                add("ok", "群聊白名单模式", f"允许 {len(whitelist)} 个群")

            if enabled_groups:
                add("ok", "已有群聊观测数据", f"已启用 {enabled_groups} 个群")
            else:
                add("info", "暂无群聊观测数据", "收到群消息后会逐步建立群内观察")
        else:
            add("info", "群聊陪伴未开启", "当前不会记录群聊上下文")

        if features.get("enable_group_interjection"):
            limit_getter = getattr(self.plugin, "_effective_group_interject_max_daily", None)
            limit = int(limit_getter() if callable(limit_getter) else getattr(self.plugin, "group_interject_max_daily", 0) or 0)
            if limit > 0:
                limit_formatter = getattr(self.plugin, "_format_proactive_daily_limit", None)
                limit_text = limit_formatter(limit) if callable(limit_formatter) else str(limit)
                suffix = "" if limit_text == "不限" else " 次"
                add("ok", "群聊插话可用", f"每群每日上限 {limit_text}{suffix}")
            else:
                add("warn", "群聊插话开关已开但额度为 0", "功能不会真正触发", "在模块配置里调高每群每日插话上限", "group.interject_limit_zero")
        elif getattr(self.plugin, "enable_group_companion", False):
            add("info", "群聊以观察为主", "当前只积累群上下文，不主动插话")

        context_aware_installed = bool(getattr(self.plugin, "_context_aware_available", lambda: False)())
        if context_aware_installed:
            if features.get("enable_group_scene_awareness"):
                add(
                    "warn",
                    "检测到上下文场景感知增强插件",
                    "不会造成代码级冲突，但若两个插件同时注入群聊场景，会增加重复上下文和 Token 消耗",
                    "建议手动二选一；本插件不会再自动改写 enable_group_scene_awareness",
                    "integration.context_aware_duplicate",
                )
            else:
                add("ok", "群聊场景感知由外部插件接管", "检测到 context_aware，且本插件对应内置功能当前为关闭")

        atrelay_installed = bool(getattr(self.plugin, "_atrelay_plugin_available", lambda: False)())
        if atrelay_installed:
            if features.get("enable_atrelay_tools"):
                add(
                    "warn",
                    "检测到艾特群友插件",
                    "本插件已内置跨群转述与 @ 群友工具；两者同时启用可能让模型看到重复工具",
                    "建议手动二选一；本插件不会再自动改写 enable_atrelay_tools",
                    "integration.atrelay_duplicate",
                )
            else:
                add("ok", "跨群转述由外部插件接管", "检测到 atrelay，且本插件对应内置工具当前为关闭")

        if not features.get("enable_group_privacy_guard"):
            add("warn", "群聊隐私保护未开启", "私聊记忆注入群聊时缺少额外防护", "建议打开 enable_group_privacy_guard", "group.privacy_guard_disabled")

        refresh_minutes = int(getattr(self.plugin, "memory_refresh_interval_minutes", 0) or 0)
        if refresh_minutes and refresh_minutes < 60:
            add("warn", "长期记忆整理过于频繁", f"当前 {refresh_minutes} 分钟，可能增加模型调用量", "建议设置为 120 分钟以上", "memory.refresh_too_frequent")

        if features.get("enable_personality_iteration_experiment"):
            suggestions = self._personality_iteration_suggestions(users, groups)
            if suggestions:
                for suggestion in suggestions:
                    dimension = self._single_line(suggestion.get("dimension"), 40)
                    dimension_code = hashlib.sha256(dimension.encode("utf-8")).hexdigest()[:12]
                    add(
                        self._single_line(suggestion.get("level"), 12) or "info",
                        f"角色贴合校准：{dimension}",
                        self._single_line(suggestion.get("text"), 260),
                        self._single_line(suggestion.get("action"), 160),
                        f"personality.{dimension_code}",
                    )
            else:
                add(
                    "ok",
                    "角色贴合校准",
                    "已启用理论检查，暂未从运行态观察到需要调整的角色贴合问题；该功能只帮助用户定位调整方向，不会自动修改 AstrBot 人格。",
                )

        return items
    def _token_stats_payload(self, usage: Any, balance_state: Any = None) -> dict[str, Any]:
        if not isinstance(usage, dict):
            usage = {}
        external_usage = usage.get("external") if isinstance(usage.get("external"), dict) else {}
        memory_plugin_usage = self._memory_plugin_token_usage_raw()
        together_plugin_usage = self._safe_together_plugin_token_usage_raw()
        totals = self._token_bucket(usage.get("totals"))
        by_provider = self._token_ranked_map(usage.get("by_provider"))
        by_task = self._token_ranked_map(usage.get("by_task"))
        by_day = self._token_series_map(usage.get("by_day"), limit=30)
        by_day_provider_raw = usage.get("by_day_provider") if isinstance(usage.get("by_day_provider"), dict) else {}
        by_day_task_raw = usage.get("by_day_task") if isinstance(usage.get("by_day_task"), dict) else {}
        by_day_detail = []
        for item in by_day:
            day_key = item.get("key", "")
            providers = self._token_ranked_map(by_day_provider_raw.get(day_key))[:5]
            tasks = self._token_ranked_map(by_day_task_raw.get(day_key))[:6]
            by_day_detail.append({**item, "providers": providers, "tasks": tasks})
        by_hour = self._token_series_map(usage.get("by_hour"), limit=48)
        today_key = _today_key()
        today_bucket = usage.get("by_day", {}).get(today_key, {}) if isinstance(usage.get("by_day"), dict) else {}
        today_total_tokens = self._int(today_bucket.get("total_tokens")) if isinstance(today_bucket, dict) else 0
        exempt_by_day = usage.get("budget_exempt_by_day") if isinstance(usage.get("budget_exempt_by_day"), dict) else {}
        today_exempt_bucket = exempt_by_day.get(today_key, {}) if isinstance(exempt_by_day, dict) else {}
        today_exempt_tokens = self._int(today_exempt_bucket.get("total_tokens")) if isinstance(today_exempt_bucket, dict) else 0
        if today_exempt_tokens <= 0:
            today_tasks = by_day_task_raw.get(today_key, {}) if isinstance(by_day_task_raw, dict) else {}
            if isinstance(today_tasks, dict):
                is_exempt_task = getattr(self.plugin, "_is_llm_budget_exempt_task", None)
                today_exempt_tokens = sum(
                    self._int(bucket.get("total_tokens"))
                    for task, bucket in today_tasks.items()
                    if (
                        (
                            callable(is_exempt_task)
                            and is_exempt_task(task)
                        )
                        or (
                            not callable(is_exempt_task)
                            and str(task) in {"proactive_framework", "voice_framework"}
                        )
                    )
                    and isinstance(bucket, dict)
                )
        today_tokens = max(0, today_total_tokens - today_exempt_tokens)
        daily_limit = self._int(getattr(self.plugin, "daily_token_limit", 0))
        soft_limit = self._int(getattr(self.plugin, "daily_token_soft_limit", 0))
        soft_enabled = bool(getattr(self.plugin, "enable_daily_token_soft_limit", True))
        budget_skips = usage.get("budget_skips", {})
        today_skips = budget_skips.get(today_key, {}) if isinstance(budget_skips, dict) else {}
        budget = {
            "day": today_key,
            "limit": daily_limit,
            "soft_limit": soft_limit,
            "soft_enabled": soft_enabled,
            "soft_active": bool(soft_enabled and soft_limit > 0 and today_tokens >= soft_limit),
            "used": today_tokens,
            "total_used": today_total_tokens,
            "exempt_used": today_exempt_tokens,
            "remaining": max(0, daily_limit - today_tokens) if daily_limit > 0 else None,
            "soft_remaining": max(0, soft_limit - today_tokens) if soft_enabled and soft_limit > 0 else None,
            "ratio": round(today_tokens / daily_limit, 4) if daily_limit > 0 else 0,
            "soft_ratio": round(today_tokens / soft_limit, 4) if soft_enabled and soft_limit > 0 else 0,
            "exceeded": bool(daily_limit > 0 and today_tokens >= daily_limit),
            "deferred_calls": (
                self._int(today_skips.get("daily_token_soft_limit_deferred"))
                + self._int(today_skips.get("maintenance_token_saver_deferred"))
            )
            if isinstance(today_skips, dict)
            else 0,
            "skipped_calls": self._int(today_skips.get("count")) if isinstance(today_skips, dict) else 0,
        }
        recent_raw = usage.get("recent")
        recent = []
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
                        "task": self._single_line(item.get("task"), 40),
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
                        "budget_exempt": bool(item.get("budget_exempt", False)),
                        "request_max_attempts": self._int(item.get("request_max_attempts")),
                        "request_retry_source": self._single_line(item.get("request_retry_source"), 32),
                        "request_retry_supported": item.get("request_retry_supported") if type(item.get("request_retry_supported")) is bool else None,
                        "provider_attempts": None,
                        "retry_after": self._float(item.get("retry_after")),
                    }
                )
        return {
            "updated_at": self._single_line(usage.get("updated_at"), 24),
            "totals": totals,
            "by_provider": by_provider,
            "by_task": by_task,
            "by_day": by_day,
            "by_day_detail": by_day_detail,
            "by_hour": by_hour,
            "budget": budget,
            "balance": self._balance_status_payload(balance_state),
            "recent": recent,
            "external": self._token_external_payload(external_usage),
            "memory_plugin": self._token_memory_plugin_payload(memory_plugin_usage),
            "together_plugin": self._token_memory_plugin_payload(together_plugin_usage),
        }
