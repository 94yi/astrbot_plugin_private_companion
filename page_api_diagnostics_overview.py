# -*- coding: utf-8 -*-
"""概览与统计域。

由 tools/split_mixin_domain.py 从 page_api_diagnostics.py 机械抽取（9 个方法 + 1 个模块级名字 + 0 个类级赋值 / 1187 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApiDiagnosticsMixin）。
"""
from __future__ import annotations

import functools
import hashlib
import time
from .helpers import _today_key
from .persona_config import runtime_persona_setting
from .planning import evaluate_daily_plan_quality
from copy import deepcopy
from typing import Any

from .logging_util import get_module_logger

logger = get_module_logger(__name__)



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


class PrivateCompanionPageApiDiagnosticsOverviewMixin:
    """概览与统计域（从 PrivateCompanionPageApiDiagnosticsMixin 拆出）。"""


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

