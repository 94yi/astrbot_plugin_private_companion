# -*- coding: utf-8 -*-
"""主动消息 / 唤醒 / 候选 域页面 API。

由 tools/split_mixin_domain.py 从 page_api.py 机械抽取（19 个方法 / 1729 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApi）。

"""
from __future__ import annotations

import asyncio
import time
import secrets
from copy import copy, deepcopy
from datetime import date, datetime, timedelta
from typing import Any, Mapping
from quart import send_file
from .page_api_shared import _page_api_host, _page_api_host_request as request
from .constants import (
    DEFAULT_DAILY_PLAN_ITEMS,
    PAGE_FONT_NAMES,
    PAGE_THEME_NAMES,
    WORLDBOOK_IMPORTANT_MEMORY_CAPACITY,
    WORLDBOOK_PENDING_OBSERVATION_CAPACITY,
    _REASON_TEXT,
)
from .helpers import _MISSING, _flat_get, _normalize_timezone_name, _normalize_timezone_setting, _path_text, _redact_outbound_secrets, _safe_int, _set_into_config, _strip_internal_message_blocks, _text_looks_garbled, _text_similarity, _today_key, normalize_bot_relationship_cards
from .logging_util import get_module_logger

logger = get_module_logger(__name__)


class PrivateCompanionPageApiProactiveMixin:
    """主动消息 / 唤醒 / 候选 域（从 PrivateCompanionPageApi 拆出）。"""

    async def update_proactive_only_unlock(self) -> dict[str, Any]:
        try:
            payload = await request.get_json(silent=True) or {}
            key = self._single_line(payload.get("key"), 80)
            action = self._single_line(payload.get("action"), 20) or "unlock"
            sync_related = bool(payload.get("sync_related"))
            normalizer = getattr(self.plugin, "_normalize_proactive_only_unlock_key", None)
            normalized = normalizer(key) if callable(normalizer) else key
            if not normalized:
                return self._error("缺少要临时放行的功能")
            applier = getattr(self.plugin, "_apply_proactive_only_temp_unlock", None)
            if not callable(applier):
                return self._error("当前插件缺少主动专用模式临时放行接口")
            clear = action in {"clear", "remove", "cancel", "关闭", "取消"}
            message = applier(normalized, sync_related=sync_related, clear=clear)
            return self._ok(
                {
                    "message": message,
                    "proactive_only": self._proactive_only_mode_snapshot(),
                }
            )
        except Exception as exc:
            logger.error(f"更新主动专用临时放行失败: {exc}", exc_info=True)
            return self._exception_error(str(exc))
    async def delete_proactive_candidate(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        candidate_id = self._single_line(payload.get("candidate_id") or payload.get("id"), 40)
        if not candidate_id:
            return self._error("缺少 candidate_id")
        try:
            async with self.plugin._data_lock:
                raw = self.plugin.data.get("proactive_candidate_pool")
                if not isinstance(raw, list):
                    raw = []
                    self.plugin.data["proactive_candidate_pool"] = raw
                removed_item = None
                kept = []
                for item in raw:
                    if not isinstance(item, dict):
                        continue
                    if self._single_line(item.get("id"), 40) == candidate_id and removed_item is None:
                        removed_item = dict(item)
                        continue
                    kept.append(item)
                if removed_item is None:
                    return self._error("没有找到对应主动候选")
                self.plugin.data["proactive_candidate_pool"] = kept
                user_id = self._single_line(removed_item.get("user_id"), 40)
                users = self.plugin.data.get("users") if isinstance(self.plugin.data.get("users"), dict) else {}
                cleared_current_plan = False
                if user_id and isinstance(users.get(user_id), dict):
                    user = users[user_id]
                    if self._single_line(user.get("planned_candidate_id"), 40) == candidate_id:
                        clearer = getattr(self.plugin, "_clear_pending_proactive_plan", None)
                        scheduler = getattr(self.plugin, "_schedule_next_proactive", None)
                        if callable(clearer):
                            clearer(user)
                            cleared_current_plan = True
                        if callable(scheduler):
                            scheduler(user, now=time.time())
                    shrinker = getattr(self.plugin, "_shrink_user_proactive_candidates", None)
                    if callable(shrinker):
                        shrinker(user_id, note="page_delete")
                self.plugin._save_data_sync(
                    sections={"users", "proactive_candidate_pool"}
                )
                data = self._overview_data_snapshot_locked(self.plugin.data)
            message = "已删除主动候选"
            if cleared_current_plan:
                message += "，并重新安排了下一次主动检查"
            proactive_tasks = await self._proactive_task_summary_async(
                data,
                force_refresh=True,
            )
            return self._ok(
                {
                    "message": message,
                    "removed": True,
                    "cleared_current_plan": cleared_current_plan,
                    "proactive_candidates": self._proactive_candidate_summary(data),
                    "proactive_tasks": proactive_tasks,
                }
            )
        except Exception as exc:
            logger.error(f"删除主动候选失败: {exc}", exc_info=True)
            return self._exception_error(str(exc))
    async def prune_proactive_candidates(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        user_id = self._single_line(payload.get("user_id"), 40)
        if not user_id:
            return self._error("缺少 user_id")
        keep = self._int(payload.get("keep"), 160, 1, 400)
        try:
            async with self.plugin._data_lock:
                shrinker = getattr(self.plugin, "_shrink_user_proactive_candidates", None)
                if not callable(shrinker):
                    return self._error("当前插件缺少主动候选收缩能力")
                removed = int(shrinker(user_id, pending_cap=keep, note="page_prune") or 0)
                self.plugin._save_data_sync(sections={"proactive_candidate_pool"})
                data = self._overview_data_snapshot_locked(self.plugin.data)
            proactive_tasks = await self._proactive_task_summary_async(
                data,
                force_refresh=True,
            )
            return self._ok(
                {
                    "message": f"已为用户压缩 {removed} 条未发送候选",
                    "removed": removed,
                    "kept_limit": keep,
                    "proactive_candidates": self._proactive_candidate_summary(data),
                    "proactive_tasks": proactive_tasks,
                }
            )
        except Exception as exc:
            logger.error(f"压缩主动候选失败: {exc}", exc_info=True)
            return self._exception_error(str(exc))
    async def _run_proactive_message_chain_test(self, payload: dict[str, Any]) -> dict[str, Any]:
        steps: list[dict[str, str]] = []

        def add_step(name: str, status: str, detail: str = "") -> None:
            steps.append(
                {
                    "name": self._single_line(name, 40),
                    "status": self._single_line(status, 16) or "info",
                    "detail": self._single_line(detail, 180),
                }
            )

        context = getattr(self.plugin, "context", None)
        if context is None:
            add_step("上下文", "error", "AstrBot context 不可用")
            return {
                "ok": False,
                "title": "主动消息链路测试",
                "steps": steps,
                "error": "AstrBot context 不可用",
            }

        target_user_id = self._single_line(payload.get("user_id"), 80)
        async with self.plugin._data_lock:
            users = deepcopy(self.plugin.data.get("users") if isinstance(self.plugin.data.get("users"), dict) else {})
        target_user_id, target_user = self._preferred_proactive_test_user(users, target_user_id)
        if not target_user_id or not target_user:
            add_step("目标会话", "error", "没有找到已启用且带私聊会话的私聊对象")
            return {
                "ok": False,
                "title": "主动消息链路测试",
                "steps": steps,
                "error": "没有找到可用于测试的私聊对象",
            }
        stored_umo = self._single_line(target_user.get("umo"), 180)
        route_resolver = getattr(self.plugin, "_private_delivery_umo_for_user_id", None)
        try:
            resolved_umo = self._single_line(route_resolver(target_user_id), 180) if callable(route_resolver) else ""
        except Exception as exc:
            logger.warning(
                "主动消息链路测试解析当前投递会话失败: user=%s error=%s",
                self._single_line(target_user_id, 80),
                self._single_line(exc, 160),
            )
            resolved_umo = ""
        umo = resolved_umo or stored_umo
        if not umo:
            add_step("目标会话", "error", "目标用户缺少 umo")
            return {
                "ok": False,
                "title": "主动消息链路测试",
                "user_id": target_user_id,
                "steps": steps,
                "error": "目标用户缺少私聊会话",
            }
        route_note = ""
        if stored_umo and resolved_umo and stored_umo != resolved_umo:
            route_note = "（已切换到当前有效投递会话）"
        add_step("目标会话", "ok", f"用户 {target_user.get('nickname') or target_user_id} / {umo}{route_note}")

        now = time.time()
        delay_seconds = self._int(payload.get("delay_seconds"), 60, 5, 300)
        scheduled_ts = now + delay_seconds
        test_id = self._single_line(payload.get("_test_request_id"), 32) or secrets.token_hex(6)
        plan_keys = (
            "next_proactive_at",
            "planned_proactive_reason",
            "planned_proactive_action",
            "planned_proactive_source",
            "planned_proactive_kind",
            "planned_proactive_route_version",
            "planned_proactive_route_dedupe_key",
            "planned_proactive_route_review_profile",
            "planned_proactive_route_retry_profile",
            "planned_proactive_route_cancel_if_new_inbound",
            "planned_proactive_route_recent_chat_policy",
            "planned_proactive_route_allow_automatic_followup",
            "planned_proactive_route_disable_segmenting",
            "planned_proactive_response_expectation",
            "planned_proactive_origin_event_id",
            "planned_proactive_route_preflight_action",
            "planned_proactive_route_preflight_note",
            "planned_proactive_motive",
            "planned_proactive_topic",
            "planned_proactive_impulse_id",
            "planned_proactive_window_start_at",
            "planned_proactive_best_until_at",
            "planned_proactive_expire_at",
            "planned_proactive_origin_at",
            "planned_proactive_origin_key",
            "planned_proactive_freshness",
            "planned_proactive_delivery_state",
            "planned_proactive_semantic_kind",
            "planned_proactive_anchor_type",
            "planned_proactive_semantic_score",
            "planned_proactive_semantic_note",
            "planned_proactive_model_judge_signature",
            "planned_proactive_model_judge_result",
            "planned_proactive_model_judge_at",
            "planned_event_chain",
            "planned_opener_mode",
            "planned_followup_kind",
            "planned_proactive_quota_exempt",
            "planned_proactive_window_timezone",
            "planned_candidate_id",
            "planned_proactive_trigger_message_id",
            "planned_proactive_trigger_umo",
            "planned_proactive_trigger_ts",
            "planned_proactive_trigger_inbound_count",
            "planned_proactive_trigger_created_at",
            "llm_timer_event",
            "sent_today",
            "proactive_sent_count",
            "ignored_streak",
            "awaiting_reply_since",
            "last_sent",
            "last_companion_message",
            "last_proactive_reason",
            "last_proactive_action",
            "last_proactive_behavior_summary",
            "last_proactive_motive",
            "last_proactive_kind",
            "proactive_route_sent_counts",
            "recent_proactive_topics",
            "proactive_daypart_counts",
            "proactive_afterglow",
            "recent_proactive_afterglows",
            "recent_proactive_hesitations",
            "last_proactive_hesitation_at",
            "last_proactive_hesitation_note",
            "state_continuity",
            "pending_followup_event",
            "suspended_proactive",
            "poke_daily_limit",
            "photo_daily_limit",
            "screen_peek_daily_limit",
        )

        async with self.plugin._data_lock:
            current = self.plugin._get_user(target_user_id)
            if not isinstance(current, dict) or not current.get("enabled", True):
                add_step("临时任务", "error", "目标私聊对象未启用")
                return {
                    "ok": False,
                    "title": "主动消息链路测试",
                    "user_id": target_user_id,
                    "umo": umo,
                    "steps": steps,
                    "error": "目标私聊对象未启用",
                }
            if current.get("proactive_sending"):
                add_step("临时任务", "error", "该用户已有主动发送正在进行")
                return {
                    "ok": False,
                    "title": "主动消息链路测试",
                    "user_id": target_user_id,
                    "umo": umo,
                    "steps": steps,
                    "error": "该用户已有主动发送正在进行",
                }
            if (
                str(current.get("planned_proactive_source") or "") == "troubleshooting"
                and isinstance(current.get("troubleshooting_proactive_restore"), dict)
            ):
                existing_steps = current.get("troubleshooting_proactive_steps") if isinstance(current.get("troubleshooting_proactive_steps"), list) else []
                return {
                    "ok": True,
                    "pending": True,
                    "trace_id": self._single_line(current.get("troubleshooting_proactive_test_id"), 32),
                    "title": "主动消息链路测试",
                    "user_id": target_user_id,
                    "umo": umo,
                    "steps": existing_steps,
                    "detail": "已有一个排障临时主动任务在等待执行，请稍后刷新查看结果",
                    "action": "message",
                    "reason": self._single_line(current.get("planned_proactive_reason"), 40) or "check_in",
                    "error": "",
                }
            restore = {
                "values": {key: deepcopy(current[key]) for key in plan_keys if key in current},
                "missing": [key for key in plan_keys if key not in current],
            }
            current["troubleshooting_proactive_restore"] = restore
            current["troubleshooting_proactive_test_id"] = test_id
            current["troubleshooting_proactive_started_at"] = now
            current["troubleshooting_proactive_steps"] = [
                {
                    "name": "目标会话",
                    "status": "ok",
                    "detail": f"用户 {current.get('nickname') or target_user_id} / {umo}{route_note}",
                },
                {"name": "临时任务", "status": "ok", "detail": f"已预约 {delay_seconds} 秒后由主动循环执行"},
            ]
            current["user_id"] = str(current.get("user_id") or target_user_id)
            current["umo"] = umo
            self.plugin._reset_planned_proactive_delivery_state(current)
            current["next_proactive_at"] = scheduled_ts
            current["planned_proactive_reason"] = "check_in"
            current["planned_proactive_action"] = "message"
            current["planned_proactive_source"] = "troubleshooting"
            current["planned_proactive_motive"] = "对方希望你主动来找一下,轻轻开口确认主动消息链路能正常工作。"
            current["planned_proactive_topic"] = "主动来找对方一下"
            current["planned_proactive_impulse_id"] = ""
            current["planned_proactive_window_start_at"] = scheduled_ts
            timezone_resolver = getattr(self.plugin, "_proactive_window_timezone", None)
            current["planned_proactive_window_timezone"] = (
                self._single_line(timezone_resolver(), 64)
                if callable(timezone_resolver)
                else self._single_line(
                    getattr(self.plugin, "environment_perception_timezone", ""),
                    64,
                )
            ) or "Asia/Shanghai"
            try:
                active_span, grace_span = self.plugin._proactive_impulse_default_window_seconds(
                    "check_in",
                    source="troubleshooting",
                )
            except TypeError:
                active_span, grace_span = self.plugin._proactive_impulse_default_window_seconds("check_in")
            current["planned_proactive_best_until_at"] = scheduled_ts + active_span
            current["planned_proactive_expire_at"] = scheduled_ts + active_span + grace_span
            semantics = self.plugin._planned_proactive_semantics(current)
            current["planned_proactive_semantic_kind"] = self._single_line(semantics.get("kind"), 40)
            current["planned_proactive_anchor_type"] = self._single_line(semantics.get("anchor_type"), 40)
            current["planned_proactive_semantic_score"] = int(max(0.0, min(1.0, self._float(semantics.get("score")))) * 100)
            current["planned_proactive_semantic_note"] = self._single_line(semantics.get("note"), 180)
            current["planned_event_chain"] = []
            current["planned_opener_mode"] = ""
            current["planned_followup_kind"] = ""
            current["planned_proactive_quota_exempt"] = True
            current["planned_candidate_id"] = f"troubleshooting_{test_id}"
            route_store = getattr(self.plugin, "_store_planned_proactive_route_fields", None)
            if callable(route_store):
                route_store(
                    current,
                    {
                        "source": "troubleshooting",
                        "reason": "check_in",
                        "scheduled_ts": scheduled_ts,
                        "topic": current["planned_proactive_topic"],
                        "motive": current["planned_proactive_motive"],
                        "origin_event_id": current["planned_candidate_id"],
                    },
                )
            current["llm_timer_event"] = {}
            current["poke_daily_limit"] = 0
            current["photo_daily_limit"] = 0
            current["screen_peek_daily_limit"] = 0
            result = {
                "ok": True,
                "pending": True,
                "trace_id": test_id,
                "outcome_type": "waiting_schedule",
                "title": "主动消息链路测试",
                "user_id": target_user_id,
                "umo": umo,
                "steps": list(current["troubleshooting_proactive_steps"]),
                "detail": f"已预约 {delay_seconds} 秒后的排障临时主动任务；到点后由主动循环走完整生成、复核、发送和归档流程",
                "action": "message",
                "reason": "check_in",
                "error": "",
            }
            raw = self.plugin.data.setdefault("troubleshooting_test_results", {})
            if not isinstance(raw, dict):
                raw = {}
                self.plugin.data["troubleshooting_test_results"] = raw
            raw["proactive_message"] = self._sanitize_troubleshooting_test_result(result)
            self._save_plugin_sections(self.plugin, {"users", "troubleshooting_test_results"})
        wakeup_task = self._schedule_troubleshooting_proactive_wakeup(target_user_id, scheduled_ts)
        if wakeup_task is None:
            logger.info(
                "主动消息链路测试未建立单独唤醒任务，将继续等待常驻主动循环: user=%s",
                self._single_line(target_user_id, 80),
            )
        add_step("临时任务", "ok", f"已预约 {delay_seconds} 秒后由主动循环执行")
        return result
    def _preferred_proactive_test_user(
        self,
        users: dict[str, Any],
        preferred_user_id: str = "",
    ) -> tuple[str, dict[str, Any] | None]:
        if preferred_user_id:
            item = users.get(preferred_user_id)
            if isinstance(item, dict) and item.get("enabled", True) and item.get("umo"):
                item = deepcopy(item)
                item["user_id"] = str(item.get("user_id") or preferred_user_id)
                return preferred_user_id, item
        fallback: tuple[str, dict[str, Any] | None] = ("", None)
        for user_id, item in users.items():
            if not isinstance(item, dict) or not item.get("enabled", True) or not item.get("umo"):
                continue
            candidate = deepcopy(item)
            candidate["user_id"] = str(candidate.get("user_id") or user_id)
            if not fallback[0]:
                fallback = (str(user_id), candidate)
            if self.plugin._private_user_role(candidate, str(candidate.get("user_id") or user_id)) == "owner":
                return str(user_id), candidate
        return fallback
    def _proactive_candidate_block_is_normal(self, note: str) -> bool:
        text = self._single_line(note, 180)
        if not text:
            return True
        exact_normal = {
            "已有更早主动候选",
            "近期主题过于相似",
            "已有用户预约/定时主动",
            "用户明确休息中",
            "当前时段主动已足够,已避开扎堆",
            "朋友主动已按日内节奏延后",
            "已有更早主动候选",
            "用户在该问候窗口内已经活跃过",
            "潜在念头窗口已过期",
            "多来源合并",
            "群聊分享候选已过期",
        }
        if text in exact_normal:
            return True
        normal_tokens = (
            "已有更早",
            "近期主题",
            "过于相似",
            "用户明确休息",
            "休息中",
            "免打扰",
            "已避开扎堆",
            "按日内节奏延后",
            "额度",
            "冷却",
            "间隔",
            "频率",
            "调度过滤",
            "窗口已过期",
            "已经活跃过",
            "多来源合并",
            "候选已过期",
        )
        return any(token in text for token in normal_tokens)
    def _proactive_chat_summary(self, data: dict[str, Any]) -> dict[str, Any]:
        installed = False
        detector = getattr(self.plugin, "_integrated_plugin_installed", None)
        if callable(detector):
            try:
                installed = bool(detector("astrbot_plugin_proactive_chat"))
            except Exception:
                installed = False
        enabled = bool(getattr(self.plugin, "enable_proactive_chat_integration", True))
        review_mode = str(getattr(self.plugin, "proactive_chat_bridge_review_mode", "local") or "local")
        users = data.get("users") if isinstance(data.get("users"), dict) else {}
        linked_users = 0
        last_sent_at = 0.0
        for user in users.values():
            if not isinstance(user, dict):
                continue
            sent_at = self._float(user.get("proactive_chat_bridge_last_sent_at"))
            if sent_at <= 0:
                continue
            linked_users += 1
            last_sent_at = max(last_sent_at, sent_at)
        formatter = getattr(self.plugin, "_format_timestamp_elapsed", None)
        last_sent = formatter(last_sent_at) if last_sent_at > 0 and callable(formatter) else ""
        runtime_status: dict[str, Any] = {}
        runtime_bridge = getattr(self.plugin, "_proactive_chat_runtime_bridge", None)
        status_getter = getattr(runtime_bridge, "status", None)
        if callable(status_getter):
            try:
                value = status_getter()
                runtime_status = value if isinstance(value, dict) else {}
            except Exception as exc:
                runtime_status = {
                    "mode": "fallback",
                    "mode_label": "发送前兼容",
                    "last_error": self._single_line(exc, 160),
                }
        runtime_mode = str(runtime_status.get("mode") or ("fallback" if installed and enabled else "waiting"))
        runtime_label = str(runtime_status.get("mode_label") or ("发送前兼容" if installed and enabled else "等待运行实例"))
        return {
            "installed": installed,
            "enabled": enabled,
            "active": bool(installed and enabled),
            "deep_active": bool(runtime_status.get("attached")),
            "runtime_degraded": bool(runtime_status.get("degraded")),
            "runtime_mode": runtime_mode,
            "runtime_mode_label": runtime_label,
            "runtime_version": self._single_line(runtime_status.get("version"), 40),
            "runtime_method_count": self._int(runtime_status.get("method_count")),
            "runtime_methods": list(runtime_status.get("methods") or []),
            "runtime_last_event": self._single_line(runtime_status.get("last_event"), 180),
            "runtime_last_error": self._single_line(runtime_status.get("last_error"), 180),
            "runtime_missing_methods": list(runtime_status.get("missing_methods") or []),
            "runtime_open_attempts": self._int(runtime_status.get("open_attempt_count")),
            "runtime_counters": dict(runtime_status.get("counters") or {}),
            "scope": "private",
            "review_mode": review_mode,
            "review_mode_label": "跟随主动终审" if review_mode == "follow_proactive_review" else "轻量本地复核",
            "linked_user_count": linked_users,
            "last_sent_at": last_sent_at,
            "last_sent": last_sent,
        }
    def _proactive_only_mode_snapshot(self) -> dict[str, Any]:
        clearer = getattr(self.plugin, "_clear_proactive_only_temp_unlocks_if_mode_off", None)
        if callable(clearer):
            clearer()
        unlocks_getter = getattr(self.plugin, "_proactive_only_unlock_store", None)
        label_getter = getattr(self.plugin, "_proactive_only_unlock_label", None)
        related_getter = getattr(self.plugin, "_related_proactive_only_unlock_keys", None)
        unlocks = sorted(unlocks_getter() if callable(unlocks_getter) else [])

        def label(key: str) -> str:
            return label_getter(key) if callable(label_getter) else key

        related: dict[str, list[dict[str, str]]] = {}
        locked_keys = [
            "inject_passive_states",
            "enable_intent_emotion_analysis",
            "enable_passive_topic_suppression",
            "enable_environment_perception",
            "enable_message_debounce",
            "enable_recall_enhancement",
            "enable_private_image_self_recognition",
            "enable_forward_message_adaptation",
            "enable_group_companion",
            "enable_skill_growth_passive_injection",
            "enable_food_menu_recommendation",
            "enable_reading_archive_preference_influence",
            "enable_worldbook_member_recognition",
            "enable_atrelay_tools",
            "enable_livingmemory_integration",
            "enable_tts_enhancement",
            "enable_segmented_proactive_reply",
        ]
        for key in locked_keys:
            keys = related_getter(key) if callable(related_getter) else []
            related[key] = [{"key": item, "label": label(item)} for item in keys]
        return {
            "enabled": bool(getattr(self.plugin, "enable_proactive_only_mode", False)),
            "unlocked": [{"key": key, "label": label(key)} for key in unlocks],
            "related": related,
        }
    def _proactive_intensity_summary(self) -> dict[str, Any]:
        runtime_getter = getattr(self.plugin, "_proactive_intensity_runtime", None)
        runtime = runtime_getter() if callable(runtime_getter) else {}
        if not isinstance(runtime, dict):
            runtime = {}
        effects = runtime.get("effects") if isinstance(runtime.get("effects"), dict) else {}

        def call_or_attr(method_name: str, attr_name: str, default: Any) -> Any:
            method = getattr(self.plugin, method_name, None)
            if callable(method):
                try:
                    return method()
                except Exception:
                    pass
            return getattr(self.plugin, attr_name, default)

        effective_max_daily = call_or_attr("_runtime_max_daily_messages", "max_daily_messages", 0)
        effective_group_interject_max_daily = call_or_attr(
            "_effective_group_interject_max_daily",
            "group_interject_max_daily",
            2,
        )
        limit_is_unlimited = getattr(self.plugin, "_proactive_daily_limit_is_unlimited", None)
        limit_formatter = getattr(self.plugin, "_format_proactive_daily_limit", None)
        max_daily_unlimited = bool(limit_is_unlimited(effective_max_daily)) if callable(limit_is_unlimited) else False
        group_interject_unlimited = bool(limit_is_unlimited(effective_group_interject_max_daily)) if callable(limit_is_unlimited) else False
        effective = {
            "max_daily_messages": effective_max_daily,
            "max_daily_messages_text": limit_formatter(effective_max_daily) if callable(limit_formatter) else str(effective_max_daily),
            "max_daily_messages_unlimited": max_daily_unlimited,
            "idle_minutes": effects.get("idle_minutes", getattr(self.plugin, "idle_minutes", 0)),
            "min_interval_minutes": effects.get("min_interval_minutes", getattr(self.plugin, "min_interval_minutes", 0)),
            "proactive_persona_judge_send_threshold": call_or_attr(
                "_effective_proactive_persona_judge_send_threshold",
                "proactive_persona_judge_send_threshold",
                62,
            ),
            "proactive_review_strength": call_or_attr("_effective_proactive_review_strength", "proactive_review_strength", "lenient"),
            "group_wakeup_cooldown_seconds": call_or_attr(
                "_effective_group_wakeup_cooldown_seconds",
                "group_wakeup_cooldown_seconds",
                90,
            ),
            "group_high_intensity_cooldown_seconds": call_or_attr(
                "_effective_group_high_intensity_cooldown_seconds",
                "group_high_intensity_cooldown_seconds",
                150,
            ),
            "group_interject_min_interval_minutes": call_or_attr(
                "_effective_group_interject_min_interval_minutes",
                "group_interject_min_interval_minutes",
                180,
            ),
            "group_interject_max_daily": effective_group_interject_max_daily,
            "group_interject_max_daily_text": limit_formatter(effective_group_interject_max_daily) if callable(limit_formatter) else str(effective_group_interject_max_daily),
            "group_interject_max_daily_unlimited": group_interject_unlimited,
            "group_wakeup_interest_probability": call_or_attr(
                "_effective_group_wakeup_interest_probability",
                "group_wakeup_interest_probability",
                0.18,
            ),
            "group_wakeup_question_threshold": call_or_attr(
                "_effective_group_wakeup_question_threshold",
                "group_wakeup_question_threshold",
                65,
            ),
            "group_wakeup_cold_group_threshold": call_or_attr(
                "_effective_group_wakeup_cold_group_threshold",
                "group_wakeup_cold_group_threshold",
                65,
            ),
            "ignore_token_soft_limit": bool(effects.get("ignore_token_soft_limit", False)),
            "ignore_daily_limit": bool(effects.get("ignore_daily_limit", False)),
        }
        configured = {
            "max_daily_messages": getattr(self.plugin, "max_daily_messages", 0),
            "max_daily_messages_text": str(getattr(self.plugin, "max_daily_messages", 0)),
            "max_daily_messages_unlimited": False,
            "idle_minutes": getattr(self.plugin, "idle_minutes", 0),
            "min_interval_minutes": getattr(self.plugin, "min_interval_minutes", 0),
            "proactive_persona_judge_send_threshold": getattr(self.plugin, "proactive_persona_judge_send_threshold", 62),
            "proactive_review_strength": getattr(self.plugin, "proactive_review_strength", "lenient"),
            "group_wakeup_cooldown_seconds": getattr(self.plugin, "group_wakeup_cooldown_seconds", 90),
            "group_high_intensity_cooldown_seconds": getattr(self.plugin, "group_high_intensity_cooldown_seconds", 150),
            "group_interject_min_interval_minutes": getattr(self.plugin, "group_interject_min_interval_minutes", 180),
            "group_interject_max_daily": getattr(self.plugin, "group_interject_max_daily", 2),
            "group_interject_max_daily_text": str(getattr(self.plugin, "group_interject_max_daily", 2)),
            "group_interject_max_daily_unlimited": False,
            "group_wakeup_interest_probability": getattr(self.plugin, "group_wakeup_interest_probability", 0.18),
            "group_wakeup_question_threshold": getattr(self.plugin, "group_wakeup_question_threshold", 65),
            "group_wakeup_cold_group_threshold": getattr(self.plugin, "group_wakeup_cold_group_threshold", 65),
            "ignore_token_soft_limit": False,
            "ignore_daily_limit": False,
        }
        changed = [
            key
            for key, value in effective.items()
            if str(value) != str(configured.get(key))
        ]
        return {
            "preset": self._single_line(runtime.get("preset") or "off", 40),
            "enabled": bool(runtime.get("enabled")),
            "label": self._single_line(runtime.get("label") or "关闭预设", 40),
            "description": self._single_line(runtime.get("description"), 160),
            "configured": configured,
            "effective": effective,
            "changed_keys": changed,
            "note": "预设只覆盖运行态有效频率，不改写手动参数；最高档使用每日 25 条私聊配额并忽略 Token 软限额降载，但免打扰、休息、用户拒绝、隐私和每日 Token 硬限额仍然生效。",
        }
    def _proactive_template_text(self, value: Any, *, target_name: Any = "", limit: int = 220) -> str:
        text = self._single_line(value, limit)
        if not text:
            return ""
        name = self._single_line(target_name, 40) or "对方"
        return self._single_line(text.replace("{name}", name).replace("{{name}}", name), limit)
    def _proactive_reason_label(self, reason: Any, *, target_name: Any = "") -> str:
        key = self._single_line(reason, 40)
        if not key:
            return "未记录原因"
        extra = {
            "bookshelf_reading_share": "跟你提起刚翻到的漫画资料",
            "bookshelf_recommendation_request": "想问你要不要推荐阅读",
            "web_exploration_share": "分享主动搜索后的发现",
            "news_share": "分享刚读到的新闻",
            "environment_change": "注意到外面的环境突然变了",
            "weather_alert": "收到一条与当前位置有关的气象预警",
            "personal_goal_progress": "自己的一个长期目标有了新进展",
            "timer": "聊天中形成的临时约定",
            "troubleshooting_test": "排障测试触发",
        }
        return self._proactive_template_text(extra.get(key) or _REASON_TEXT.get(key) or key, target_name=target_name, limit=80)
    def _proactive_source_meta(self, source: Any) -> dict[str, str]:
        key = self._single_line(source, 40)
        if not key:
            return {"label": "插件主动", "note": ""}
        meta = {
            "random": {
                "label": "轻微想念",
                "note": "没有明确外部触发，更像安静一阵后轻轻冒出来、想靠近你一下。",
            },
            "daily_greeting": {
                "label": "日常招呼",
                "note": "到了早中晚合适的那个点，顺手来冒个头，不是专门执行问候任务。",
            },
            "pending_followup": {
                "label": "补一句",
                "note": "前面那句还留着一个具体点没落地，所以隔一阵再接一句。",
            },
            "state": {
                "label": "身体小需求",
                "note": "不是汇报状态，而是身体上那点小事挂着，顺手拿来找你说一句。",
            },
            "event": {"label": "生活事件", "note": ""},
            "story": {"label": "日常剧情", "note": ""},
            "habit": {"label": "习惯关心", "note": ""},
            "bilibili": {"label": "B站分享", "note": ""},
            "bookshelf_reading": {"label": "资料归档", "note": ""},
            "creative_writing": {"label": "创作灵感", "note": ""},
            "group_share": {"label": "群聊见闻", "note": ""},
            "web_exploration": {"label": "主动搜索", "note": ""},
            "news": {"label": "新闻阅读", "note": ""},
            "environment_change": {"label": "环境突变", "note": "实时环境出现明显变化后形成的短时主动。"},
            "weather_alert": {"label": "气象预警", "note": "官方预警出现、更新或解除后形成的主要用户提醒。"},
            "body_monitor": {"label": "身体状态联动", "note": "由 Body Monitor 提供的短时身体状态关心事件。"},
            "meal_care": {"label": "饭点关心", "note": "在合适饭点形成的低压力饮食关心。"},
            "group_ignore_complaint": {
                "label": "群内冒泡关心",
                "note": "对方暂未回复私聊、但刚在群内出现后形成的低压力关心。",
            },
            "post_goodnight_group_activity": {
                "label": "晚安后群聊活跃",
                "note": "和主要用户互道晚安后，对方仍在群里活跃时按人格低概率形成的轻调侃或关心。",
            },
            "reading_archive": {"label": "资料归档", "note": ""},
            "personal_goal": {"label": "个人目标", "note": "非创作型长期目标在真实推进、停滞或完成后形成的主动。"},
            "candidate": {"label": "主动候选", "note": ""},
            "followup": {"label": "补一句", "note": "前面的话还差个具体点，所以顺手再接一句。"},
            "external": {"label": "外部主动能力", "note": ""},
            "timer": {"label": "官方定时计划", "note": ""},
            "proactive": {"label": "插件主动", "note": ""},
            "unknown": {"label": "未记录来源", "note": ""},
        }
        return dict(meta.get(key) or {"label": key, "note": ""})
    def _proactive_source_label(self, source: Any) -> str:
        return self._proactive_source_meta(source).get("label") or "插件主动"
    def _proactive_source_note(self, source: Any) -> str:
        return self._single_line(self._proactive_source_meta(source).get("note"), 120)
    def _proactive_reason_detail(
        self,
        *,
        reason: Any,
        source: Any = "",
        topic: Any = "",
        motive: Any = "",
        note: Any = "",
        target_name: Any = "",
    ) -> str:
        label = self._proactive_reason_label(reason, target_name=target_name)
        parts = [label]
        topic_text = self._proactive_template_text(topic, target_name=target_name, limit=80)
        motive_text = self._proactive_template_text(motive, target_name=target_name, limit=140)
        note_text = self._proactive_template_text(note, target_name=target_name, limit=120)
        source_text = self._proactive_source_label(source)
        if topic_text:
            parts.append(f"话题：{topic_text}")
        if motive_text:
            parts.append(f"动机：{motive_text}")
        if note_text:
            parts.append(f"记录：{note_text}")
        if source_text:
            parts.append(f"来源：{source_text}")
        return self._single_line("；".join(parts), 220)
    def _proactive_candidate_summary(self, data: dict[str, Any]) -> dict[str, Any]:
        raw = data.get("proactive_candidate_pool") if isinstance(data.get("proactive_candidate_pool"), list) else []
        users = data.get("users") if isinstance(data.get("users"), dict) else {}
        now = time.time()
        converter = getattr(self.plugin, "_environment_fromtimestamp", None)
        try:
            current_dt = converter(now) if callable(converter) else datetime.fromtimestamp(now)
        except Exception:
            current_dt = datetime.fromtimestamp(now)
        today_start = current_dt.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        tomorrow_start = (current_dt.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).timestamp()
        today_key = current_dt.strftime("%Y-%m-%d")
        buckets: list[dict[str, Any]] = []
        counts: dict[str, int] = {}
        source_counts: dict[str, int] = {}
        user_counts: dict[str, dict[str, Any]] = {}
        total_attempts = 0
        pending_total = 0
        pool_record_total = len([item for item in raw if isinstance(item, dict)])
        today_record_total = 0
        today_merge_trigger_count = 0
        today_blocked_record_total = 0

        def pending_status(status: Any) -> bool:
            normalized = self._single_line(status, 24).lower()
            return normalized in {"accepted", "deferred", "queued", "pending", "unknown", ""}

        def repeat_limit(status: Any) -> int:
            normalized = self._single_line(status, 24).lower()
            if normalized in {"accepted", "deferred", "queued", "pending", "unknown", ""}:
                return 12
            if normalized == "sent":
                return 8
            return 6

        def normalized_repeat(item: dict[str, Any], status: str) -> int:
            count = max(1, self._int(item.get("repeat_count")))
            limit = repeat_limit(status)
            value = max(1, min(limit, count))
            if count != value:
                item["repeat_count"] = value
                item["repeat_count_capped"] = True
            return value

        def candidate_user_meta(user_id: str, user: Any) -> dict[str, str]:
            if not isinstance(user, dict):
                return {"label": user_id or "未知用户", "role": "unknown", "role_label": "未知"}
            role = self.plugin._private_user_role(user, user_id) if hasattr(self.plugin, "_private_user_role") else ""
            role_labeler = getattr(self.plugin, "_private_user_role_label", None)
            role_label = role_labeler(role) if callable(role_labeler) else ("主要用户" if role == "owner" else "次要用户")
            nickname = self._single_line(user.get("nickname"), 40)
            generic_names = {"用户", "主人", "主要用户", "默认用户"}
            if str(user_id).isdigit():
                label = nickname if nickname and nickname not in generic_names else user_id
            else:
                umo = self._single_line(
                    user.get("umo") or user.get("last_umo") or user.get("last_unified_msg_origin"),
                    180,
                )
                profile_getter = getattr(self.plugin, "_platform_profile", None)
                try:
                    platform_profile = profile_getter(umo=umo) if callable(profile_getter) else {}
                except Exception:
                    platform_profile = {}
                platform_kind = self._single_line((platform_profile or {}).get("kind"), 40)
                if platform_kind == "qq_official":
                    label = nickname if nickname and nickname not in generic_names else f"QQ 官方 · {str(user_id)[:8]}"
                else:
                    label = nickname if nickname and nickname not in generic_names else f"临时会话 · {str(user_id)[:8]}"
            return {"label": label or user_id or "未知用户", "role": role or "friend", "role_label": role_label}

        for item in raw:
            if not isinstance(item, dict):
                continue
            status = self._single_line(item.get("status"), 24) or "unknown"
            repeat_count = normalized_repeat(item, status)
            note = self._single_line(item.get("note"), 160)
            created_ts = self._float(item.get("created_ts"))
            status_ts = self._float(item.get("updated_ts")) or created_ts
            created_today = today_start <= created_ts < tomorrow_start
            if created_today:
                today_record_total += 1
            merged_by_day = item.get("merged_by_day") if isinstance(item.get("merged_by_day"), dict) else {}
            if today_key in merged_by_day:
                today_merge_trigger_count += self._int(merged_by_day.get(today_key))
            elif created_today:
                # 兼容升级前只有 repeat_count、没有逐日合并计数的候选。
                today_merge_trigger_count += max(0, repeat_count - 1)
            if status == "blocked" and today_start <= status_ts < tomorrow_start:
                today_blocked_record_total += 1
            if status == "blocked" and note in {"朋友关系不接收敏感主动", "次要用户关系不接收敏感主动"}:
                continue
            user_id = self._single_line(item.get("user_id"), 128)
            user = users.get(user_id) if isinstance(users, dict) else None
            reason_raw = self._single_line(item.get("reason"), 40)
            action_raw = self._single_line(item.get("action"), 40)
            if (
                isinstance(user, dict)
                and hasattr(self.plugin, "_friend_can_receive_proactive_reason")
                and not self.plugin._friend_can_receive_proactive_reason(user, reason_raw, action_raw)
            ):
                continue
            if status != "sent" and not bool(
                isinstance(user, dict)
                and getattr(self.plugin, "_user_enabled_for_proactive", lambda uid, profile: bool(profile and profile.get("enabled", True)))(
                    user_id,
                    user,
                )
            ):
                continue
            total_attempts += repeat_count
            if pending_status(status):
                pending_total += repeat_count
            user_meta = candidate_user_meta(user_id, user)
            user_bucket = user_counts.setdefault(
                user_id or "unknown",
                {
                    "user_id": user_id,
                    "label": user_meta["label"],
                    "role": user_meta["role"],
                    "role_label": user_meta["role_label"],
                    "total": 0,
                    "pending_total": 0,
                    "counts": {},
                },
            )
            user_bucket["total"] = self._int(user_bucket.get("total")) + repeat_count
            if pending_status(status):
                user_bucket["pending_total"] = self._int(user_bucket.get("pending_total")) + repeat_count
            bucket_counts = user_bucket.get("counts")
            if not isinstance(bucket_counts, dict):
                bucket_counts = {}
                user_bucket["counts"] = bucket_counts
            bucket_counts[status] = self._int(bucket_counts.get(status)) + repeat_count
            source = self._single_line(item.get("source"), 40) or "unknown"
            display_source = "bookshelf_reading" if source == "reading_archive" else source
            counts[status] = counts.get(status, 0) + repeat_count
            source_counts[display_source] = source_counts.get(display_source, 0) + repeat_count
            scheduled = self._float(item.get("scheduled_ts"))
            created = created_ts
            last_seen = self._float(item.get("last_seen_ts")) or created
            reason = reason_raw
            action = action_raw
            if reason == "reading_archive_share":
                reason = "bookshelf_reading_share"
            if reason == "reading_archive_recommendation_request":
                reason = "bookshelf_recommendation_request"
            if action == "reading_archive_read":
                action = "bookshelf_reading"
            signature = self._single_line(item.get("signature"), 120)
            topic = self._single_line(item.get("topic"), 100)
            motive = self._single_line(item.get("motive"), 180)
            semantic_kind = self._single_line(item.get("semantic_kind"), 40)
            semantic_anchor_type = self._single_line(item.get("semantic_anchor_type"), 40)
            semantic_score = self._int(item.get("semantic_score"))
            semantic_pressure = self._int(item.get("semantic_pressure"))
            semantic_risk = self._int(item.get("semantic_risk"))
            semantic_note = self._single_line(item.get("semantic_note"), 180)
            need_layer = self._single_line(item.get("semantic_need_layer") or item.get("need_layer"), 40)
            need_drive = self._single_line(item.get("semantic_need_drive") or item.get("need_drive"), 80)
            need_note = self._single_line(item.get("semantic_need_note") or item.get("need_note"), 120)
            need_score_bias = item.get("semantic_need_score_bias", item.get("need_score_bias"))
            need_pressure_bias = item.get("semantic_need_pressure_bias", item.get("need_pressure_bias"))
            sanitizer = getattr(self.plugin, "_sanitize_friend_proactive_plan_fields", None)
            if isinstance(user, dict) and callable(sanitizer):
                sanitized = sanitizer(
                    user,
                    reason=reason,
                    action=action,
                    topic=topic,
                    motive=motive,
                )
                reason = self._single_line(sanitized.get("reason"), 40) or reason
                action = self._single_line(sanitized.get("action"), 40) or action
                topic = self._single_line(sanitized.get("topic"), 100)
                motive = self._single_line(sanitized.get("motive"), 180)
            merged = None
            for existing in reversed(buckets):
                if existing.get("status") != status:
                    continue
                if existing.get("user_id") != user_id:
                    continue
                old_signature = str(existing.get("_signature") or "")
                if signature and old_signature:
                    similar = bool(getattr(self.plugin, "_topic_signature_similar", lambda a, b: a == b)(signature, old_signature))
                else:
                    similar = (topic or motive) == (existing.get("topic") or existing.get("motive"))
                if not similar:
                    continue
                if max(last_seen, scheduled, created) - self._float(existing.get("_first_ts")) > 36 * 3600:
                    continue
                merged = existing
                break
            if merged is None:
                buckets.append(
                    {
                        "id": self._single_line(item.get("id"), 20),
                        "user_id": user_id,
                        "user_label": user_meta["label"],
                        "user_role": user_meta["role"],
                        "user_role_label": user_meta["role_label"],
                        "source": display_source,
                        "source_label": self._proactive_source_label(display_source),
                        "source_note": self._proactive_source_note(display_source),
                        "reason": reason,
                        "reason_label": self._proactive_reason_label(reason, target_name=user_meta["label"]),
                        "reason_detail": self._proactive_reason_detail(
                            reason=reason,
                            source=display_source,
                            topic=topic,
                            motive=motive,
                            note=note,
                            target_name=user_meta["label"],
                        ),
                        "action": action,
                        "topic": topic,
                        "motive": motive,
                        "score": self._int(item.get("score")),
                        "semantic_kind": semantic_kind,
                        "semantic_anchor_type": semantic_anchor_type,
                        "semantic_score": semantic_score,
                        "semantic_pressure": semantic_pressure,
                        "semantic_risk": semantic_risk,
                        "semantic_note": semantic_note,
                        "need_layer": need_layer,
                        "need_level": need_layer,
                        "need_drive": need_drive,
                        "need_note": need_note,
                        "need_score_bias": need_score_bias,
                        "need_pressure_bias": need_pressure_bias,
                        "status": status,
                        "note": note,
                        "repeat_count": repeat_count,
                        "created_ts": created,
                        "last_seen_ts": last_seen,
                        "scheduled_ts": scheduled,
                        "is_due": bool(scheduled and scheduled <= now),
                        "_signature": signature,
                        "_first_ts": max(created, scheduled, last_seen),
                    }
                )
                continue
            previous_repeat = self._int(merged.get("repeat_count"))
            merged_repeat_limit = repeat_limit(status)
            merged["repeat_count"] = min(merged_repeat_limit, previous_repeat + repeat_count)
            if previous_repeat + repeat_count > merged_repeat_limit:
                merged["repeat_count_capped"] = True
            merged["last_seen_ts"] = max(self._float(merged.get("last_seen_ts")), last_seen, created)
            merged["scheduled_ts"] = max(self._float(merged.get("scheduled_ts")), scheduled)
            merged["score"] = max(self._int(merged.get("score")), self._int(item.get("score")))
            if semantic_score >= self._int(merged.get("semantic_score")):
                merged["semantic_kind"] = semantic_kind
                merged["semantic_anchor_type"] = semantic_anchor_type
                merged["semantic_score"] = semantic_score
                merged["semantic_pressure"] = semantic_pressure
                merged["semantic_risk"] = semantic_risk
                merged["semantic_note"] = semantic_note
                merged["need_layer"] = need_layer
                merged["need_level"] = need_layer
                merged["need_drive"] = need_drive
                merged["need_note"] = need_note
                merged["need_score_bias"] = need_score_bias
                merged["need_pressure_bias"] = need_pressure_bias
            if topic:
                merged["topic"] = topic
            if motive:
                merged["motive"] = motive
            if note and note != merged.get("note"):
                merged["note"] = "多来源合并"
            merged["is_due"] = bool(merged.get("scheduled_ts") and self._float(merged.get("scheduled_ts")) <= now)
        items: list[dict[str, Any]] = []
        for item in buckets:
            created = self._float(item.get("created_ts"))
            last_seen = self._float(item.get("last_seen_ts")) or created
            scheduled = self._float(item.get("scheduled_ts"))
            item.pop("_signature", None)
            item.pop("_first_ts", None)
            item["created"] = self.plugin._format_timestamp_elapsed(created)
            item["last_seen"] = self.plugin._format_timestamp_elapsed(last_seen)
            item["scheduled"] = self.plugin._format_timestamp_elapsed(scheduled)
            items.append(item)
        items.sort(key=lambda item: item.get("last_seen_ts") or item.get("scheduled_ts") or 0, reverse=True)
        display_limit = 60
        displayed_items = items[:display_limit]
        return {
            "total": total_attempts,
            "pending_total": pending_total,
            "record_total": pool_record_total,
            "pool_record_total": pool_record_total,
            "today_record_total": today_record_total,
            "today_merge_trigger_count": today_merge_trigger_count,
            "today_blocked_record_total": today_blocked_record_total,
            "visible_total": len(items),
            "list_total": len(items),
            "list_displayed_total": len(displayed_items),
            "list_limit": display_limit,
            "list_truncated": len(items) > len(displayed_items),
            "counts": counts,
            "source_counts": source_counts,
            "source_labels": {key: self._proactive_source_label(key) for key in source_counts},
            "users": sorted(
                user_counts.values(),
                key=lambda item: (self._int(item.get("total")), self._single_line(item.get("label"), 40)),
                reverse=True,
            ),
            "items": displayed_items,
        }
    def _proactive_motivation_runtime_summary(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}

        def part(raw: Any) -> dict[str, Any]:
            if not isinstance(raw, dict):
                return {}
            result: dict[str, Any] = {
                "score": round(self._float(raw.get("score")), 3),
                "label": self._single_line(raw.get("label"), 60),
                "detail": self._single_line(raw.get("detail"), 180),
            }
            if "level" in raw:
                result["level"] = round(self._float(raw.get("level")), 3)
            return result

        return {
            "score": round(self._float(value.get("score")), 3),
            "label": self._single_line(value.get("label"), 60),
            "detail": self._single_line(value.get("detail"), 220),
            "drive": part(value.get("drive")),
            "temperature": part(value.get("temperature")),
            "incentive": part(value.get("incentive")),
            "arousal": part(value.get("arousal")),
        }
    async def _proactive_task_summary_async(
        self,
        data: dict[str, Any],
        *,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        """Build the SQLite-backed summary off-loop with one shared flight."""

        now = time.monotonic()
        if force_refresh:
            self._proactive_task_summary_generation += 1
        if (
            not force_refresh
            and self._proactive_task_summary_cache_ready
            and now - self._proactive_task_summary_cache_at
            < max(
                1.0,
                float(self.TROUBLESHOOTING_PROACTIVE_SUMMARY_CACHE_SECONDS),
            )
        ):
            return deepcopy(self._proactive_task_summary_cache)

        task = None if force_refresh else self._proactive_task_summary_task
        if task is None or task.done():
            snapshot = deepcopy(data)
            generation = self._proactive_task_summary_generation

            async def compute() -> dict[str, Any]:
                try:
                    result = await asyncio.to_thread(
                        self._build_troubleshooting_proactive_summary,
                        snapshot,
                    )
                    if not isinstance(result, dict):
                        raise TypeError("proactive task summary returned a non-object")
                except Exception as exc:
                    logger.warning(
                        "[PrivateCompanionPage] 主动任务摘要已降级: error_type=%s",
                        type(exc).__name__,
                        exc_info=True,
                    )
                    fallback = (
                        deepcopy(self._proactive_task_summary_cache)
                        if self._proactive_task_summary_cache_ready
                        else {
                            "total": 0,
                            "pending_total": 0,
                            "items": [],
                            "users": [],
                        }
                    )
                    fallback["degraded"] = True
                    fallback["diagnostic"] = {
                        "code": "proactive_task_summary_unavailable",
                        "error_type": type(exc).__name__,
                        "using_last_good": self._proactive_task_summary_cache_ready,
                    }
                    return fallback
                if generation == self._proactive_task_summary_generation:
                    self._proactive_task_summary_cache = deepcopy(result)
                    self._proactive_task_summary_cache_at = time.monotonic()
                    self._proactive_task_summary_cache_ready = True
                return result

            task = asyncio.create_task(
                compute(),
                name="private-companion-proactive-task-summary",
            )
            self._proactive_task_summary_task = task

            def clear_finished(completed: asyncio.Task[dict[str, Any]]) -> None:
                if self._proactive_task_summary_task is completed:
                    self._proactive_task_summary_task = None

            task.add_done_callback(clear_finished)
        return deepcopy(await asyncio.shield(task))
    def _proactive_task_summary(self, data: dict[str, Any]) -> dict[str, Any]:
        users = data.get("users") if isinstance(data.get("users"), dict) else {}
        now = time.time()
        items: list[dict[str, Any]] = []
        source_counts: dict[str, int] = {}
        status_counts: dict[str, int] = {}
        audit_items: list[dict[str, Any]] = []
        audit_status_counts: dict[str, int] = {}
        user_states: list[dict[str, Any]] = []

        def should_show_user_state(
            user_id: str,
            user: dict[str, Any],
            summary: dict[str, Any],
            *,
            next_ts: float,
            effective_limit: Any,
        ) -> bool:
            """Keep empty transient sessions out of the proactive dashboard."""
            display_name = self._single_line(summary.get("display_name"), 80)
            if not display_name.startswith("临时会话"):
                return True
            proactive_gate = getattr(self.plugin, "_user_enabled_for_proactive", None)
            if callable(proactive_gate):
                try:
                    if self._int(effective_limit) > 0 and proactive_gate(user_id, user):
                        return True
                except Exception:
                    pass
            timer_event = user.get("llm_timer_event") if isinstance(user.get("llm_timer_event"), dict) else {}
            timer_ts = self._float(timer_event.get("scheduled_ts"))
            if next_ts > now or timer_ts > now:
                return True
            recent_cutoff = now - 7 * 24 * 3600
            # Group traffic can refresh a temporary identity. Only private
            # activity should keep it visible in the proactive dashboard.
            recent_private_activity = max(
                self._float(user.get("last_private_seen")),
                self._float(user.get("last_private_activity_at")),
                self._float(user.get("last_private_reply_at")),
            )
            if recent_private_activity >= recent_cutoff:
                return True
            recent_proactive_activity = max(
                self._float(user.get("last_proactive_sent_at")),
                self._float(user.get("last_proactive_skip_at")),
                self._float(user.get("last_proactive_message_at")),
            )
            if recent_proactive_activity >= recent_cutoff:
                return True
            afterglow = user.get("proactive_afterglow")
            return isinstance(afterglow, dict) and self._float(afterglow.get("ts")) >= recent_cutoff

        def readiness_snapshot(user: dict[str, Any]) -> dict[str, Any]:
            getter = getattr(self.plugin, "_proactive_inner_readiness", None)
            if not callable(getter):
                return {}
            try:
                raw = getter(user, now=now)
            except Exception:
                return {}
            if not isinstance(raw, dict):
                return {}
            drive = raw.get("drive") if isinstance(raw.get("drive"), dict) else {}
            temperature = raw.get("temperature") if isinstance(raw.get("temperature"), dict) else {}
            return {
                "score": round(self._float(raw.get("score")), 3),
                "label": self._single_line(raw.get("label"), 80),
                "detail": self._single_line(raw.get("detail"), 220),
                "drive_score": round(self._float(drive.get("score")), 3) if drive else 0,
                "drive_label": self._single_line(drive.get("label"), 40) if drive else "",
                "drive_detail": self._single_line(drive.get("detail"), 140) if drive else "",
                "temperature_score": round(self._float(temperature.get("score")), 3) if temperature else 0,
                "temperature_label": self._single_line(temperature.get("label"), 40) if temperature else "",
                "temperature_detail": self._single_line(temperature.get("detail"), 140) if temperature else "",
                "motivation": self._proactive_motivation_runtime_summary(raw.get("motivation")),
            }

        def planned_semantic_snapshot(user: dict[str, Any]) -> dict[str, Any]:
            semantic_kind = self._single_line(user.get("planned_proactive_semantic_kind"), 40)
            anchor_type = self._single_line(user.get("planned_proactive_anchor_type"), 40)
            semantic_score = self._int(user.get("planned_proactive_semantic_score"))
            semantic_note = self._single_line(user.get("planned_proactive_semantic_note"), 180)
            need_layer = self._single_line(user.get("planned_proactive_need_layer"), 40)
            need_drive = self._single_line(user.get("planned_proactive_need_drive"), 80)
            need_note = self._single_line(user.get("planned_proactive_need_note"), 120)
            need_score_bias = None
            need_pressure_bias = None
            pressure = 0
            risk = 0
            getter = getattr(self.plugin, "_planned_proactive_semantics", None)
            if callable(getter):
                try:
                    semantics = getter(user)
                except Exception:
                    semantics = {}
                if isinstance(semantics, dict):
                    semantic_kind = semantic_kind or self._single_line(semantics.get("kind"), 40)
                    anchor_type = anchor_type or self._single_line(semantics.get("anchor_type"), 40)
                    if semantic_score <= 0:
                        semantic_score = int(max(0.0, min(1.0, self._float(semantics.get("score")))) * 100)
                    pressure = int(max(0.0, min(1.0, self._float(semantics.get("pressure")))) * 100)
                    risk = int(max(0.0, min(1.0, self._float(semantics.get("risk")))) * 100)
                    semantic_note = semantic_note or self._single_line(semantics.get("note"), 180)
                    need_layer = need_layer or self._single_line(semantics.get("need_layer"), 40)
                    need_drive = need_drive or self._single_line(semantics.get("need_drive"), 80)
                    need_note = need_note or self._single_line(semantics.get("need_note"), 120)
                    need_score_bias = semantics.get("need_score_bias")
                    need_pressure_bias = semantics.get("need_pressure_bias")
            return {
                "semantic_kind": semantic_kind,
                "semantic_anchor_type": anchor_type,
                "semantic_score": semantic_score,
                "semantic_pressure": pressure,
                "semantic_risk": risk,
                "semantic_note": semantic_note,
                "need_layer": need_layer,
                "need_level": need_layer,
                "need_drive": need_drive,
                "need_note": need_note,
                "need_score_bias": need_score_bias,
                "need_pressure_bias": need_pressure_bias,
            }

        def planned_window_snapshot(user: dict[str, Any]) -> dict[str, Any]:
            start_ts = self._float(user.get("planned_proactive_window_start_at"))
            best_until_ts = self._float(user.get("planned_proactive_best_until_at"))
            expire_ts = self._float(user.get("planned_proactive_expire_at"))
            phase = ""
            detail = ""
            phase_getter = getattr(self.plugin, "_planned_impulse_window_phase", None)
            if callable(phase_getter):
                try:
                    phase, detail = phase_getter(user, now=now)
                except Exception:
                    phase, detail = "", ""
            impulse_value = 0
            value_getter = getattr(self.plugin, "_planned_impulse_value", None)
            if callable(value_getter):
                try:
                    impulse_value = int(max(0.0, min(1.0, self._float(value_getter(user, now=now)))) * 100)
                except Exception:
                    impulse_value = 0
            return {
                "window_start_ts": start_ts,
                "window_start": self.plugin._format_timestamp_elapsed(start_ts),
                "best_until_ts": best_until_ts,
                "best_until": self.plugin._format_timestamp_elapsed(best_until_ts),
                "expire_ts": expire_ts,
                "expire": self.plugin._format_timestamp_elapsed(expire_ts),
                "window_phase": self._single_line(phase, 24),
                "window_detail": self._single_line(detail, 160),
                "impulse_value": impulse_value,
            }

        def afterglow_snapshot(user: dict[str, Any]) -> dict[str, Any]:
            raw = user.get("proactive_afterglow")
            if not isinstance(raw, dict):
                return {}
            ts = self._float(raw.get("ts"))
            return {
                "ts": ts,
                "time": self.plugin._format_timestamp_elapsed(ts),
                "status": self._single_line(raw.get("status"), 32),
                "label": self._single_line(raw.get("label"), 180),
                "next_tendency": self._single_line(raw.get("next_tendency"), 180),
                "reason": self._single_line(raw.get("reason"), 50),
                "action": self._single_line(raw.get("action"), 50),
                "semantic_kind": self._single_line(raw.get("semantic_kind"), 40),
                "anchor_type": self._single_line(raw.get("anchor_type"), 40),
                "semantic_score": self._int(raw.get("semantic_score")),
            }

        def hesitation_snapshot(user: dict[str, Any]) -> dict[str, Any]:
            raw = user.get("recent_proactive_hesitations")
            latest = raw[-1] if isinstance(raw, list) and raw and isinstance(raw[-1], dict) else {}
            ts = self._float(latest.get("ts") if isinstance(latest, dict) else 0) or self._float(user.get("last_proactive_hesitation_at"))
            note = self._single_line(latest.get("note") if isinstance(latest, dict) else "", 160) or self._single_line(user.get("last_proactive_hesitation_note"), 160)
            if not ts and not note:
                return {}
            return {
                "ts": ts,
                "time": self.plugin._format_timestamp_elapsed(ts),
                "note": note,
                "count": self._int(latest.get("count") if isinstance(latest, dict) else 0),
                "topic": self._single_line(latest.get("topic") if isinstance(latest, dict) else "", 80),
                "motive": self._single_line(latest.get("motive") if isinstance(latest, dict) else "", 140),
            }

        for user_id, user in users.items():
            if not isinstance(user, dict):
                continue
            readiness = readiness_snapshot(user)
            afterglow = afterglow_snapshot(user)
            hesitation = hesitation_snapshot(user)
            user_summary_for_state = self._user_summary(str(user_id), user)
            effective_limit = (
                self.plugin._effective_user_daily_limit(user)
                if hasattr(self.plugin, "_effective_user_daily_limit")
                else getattr(self.plugin, "max_daily_messages", 0)
            )
            next_for_state = self._float(user.get("next_proactive_at"))
            proactive_gate = getattr(self.plugin, "_user_enabled_for_proactive", None)
            if callable(proactive_gate):
                try:
                    proactive_enabled = bool(proactive_gate(str(user_id), user))
                except Exception:
                    proactive_enabled = False
            else:
                capabilities = user.get("unified_profile_capabilities")
                proactive_enabled = bool(
                    user.get("proactive_private_enabled") is True
                    or (
                        isinstance(capabilities, dict)
                        and capabilities.get("proactive_private_enabled") is True
                    )
                )
            if bool(user.get("enabled", True)) and proactive_enabled:
                if should_show_user_state(
                    str(user_id),
                    user,
                    user_summary_for_state,
                    next_ts=next_for_state,
                    effective_limit=effective_limit,
                ):
                    user_states.append(
                        {
                            "user_id": str(user_id),
                            "user_label": user_summary_for_state.get("display_name") or str(user_id),
                            "user_role": user_summary_for_state.get("relationship_role") or "",
                            "user_role_label": user_summary_for_state.get("relationship_role_label") or "",
                            "sent_today": self._int(user.get("sent_today")),
                            "effective_daily_limit": effective_limit,
                            "effective_daily_limit_text": (
                                self.plugin._format_proactive_daily_limit(effective_limit)
                                if hasattr(self.plugin, "_format_proactive_daily_limit")
                                else str(effective_limit)
                            ),
                            "effective_daily_limit_unlimited": (
                                self.plugin._proactive_daily_limit_is_unlimited(effective_limit)
                                if hasattr(self.plugin, "_proactive_daily_limit_is_unlimited")
                                else False
                            ),
                            "next_proactive_ts": next_for_state,
                            "next_proactive": self.plugin._format_timestamp_elapsed(next_for_state),
                            "proactive_sending": bool(user.get("proactive_sending")),
                            "last_skip_ts": self._float(user.get("last_proactive_skip_at")),
                            "last_skip": self.plugin._format_timestamp_elapsed(user.get("last_proactive_skip_at", 0)),
                            "last_skip_reason": self._single_line(user.get("last_proactive_skip_reason"), 160),
                            "last_skip_prefix": self._single_line(user.get("last_proactive_skip_prefix"), 20),
                            "last_sent_ts": self._float(user.get("last_sent")),
                            "last_sent": self.plugin._format_timestamp_elapsed(user.get("last_sent", 0)),
                            "inner_readiness": readiness,
                            "afterglow": afterglow,
                            "hesitation": hesitation,
                        }
                    )
            scheduled_ts = self._float(user.get("next_proactive_at"))
            timer_event = user.get("llm_timer_event") if isinstance(user.get("llm_timer_event"), dict) else {}
            if scheduled_ts <= 0 and timer_event:
                scheduled_ts = self._float(timer_event.get("scheduled_ts"))
            if scheduled_ts <= 0:
                continue
            source = self._single_line(user.get("planned_proactive_source"), 40)
            if not source and timer_event:
                source = "timer"
            source = source or "proactive"
            status = "due" if scheduled_ts <= now else "scheduled"
            if scheduled_ts < now - 15 * 60:
                status = "overdue"
            if timer_event and self._single_line(timer_event.get("backend"), 40) == "astrbot_cron" and scheduled_ts <= now:
                status = "handed_off"
            timer_status = self._single_line(timer_event.get("status"), 40)
            if timer_status in {"failed", "cancelled", "cancel_failed", "cancel_skipped"}:
                status = timer_status
            user_summary = self._user_summary(str(user_id), user)
            source_counts[source] = source_counts.get(source, 0) + 1
            status_counts[status] = status_counts.get(status, 0) + 1
            action = (
                self._single_line(user.get("planned_proactive_action"), 40)
                or self._single_line(timer_event.get("action"), 40)
                or "message"
            )
            reason = (
                self._single_line(user.get("planned_proactive_reason"), 40)
                or self._single_line(timer_event.get("reason"), 40)
            )
            topic = (
                self._single_line(user.get("planned_proactive_topic"), 80)
                or self._single_line(timer_event.get("topic"), 80)
            )
            motive = (
                self._single_line(user.get("planned_proactive_motive"), 180)
                or self._single_line(timer_event.get("motive"), 180)
            )
            sanitizer = getattr(self.plugin, "_sanitize_friend_proactive_plan_fields", None)
            if callable(sanitizer):
                sanitized = sanitizer(
                    user,
                    reason=reason,
                    action=action,
                    topic=topic,
                    motive=motive,
                )
                reason = self._single_line(sanitized.get("reason"), 40) or reason
                action = self._single_line(sanitized.get("action"), 40) or action
                topic = self._single_line(sanitized.get("topic"), 80)
                motive = self._single_line(sanitized.get("motive"), 180)
            semantic = planned_semantic_snapshot(user)
            window = planned_window_snapshot(user)
            model_result = user.get("planned_proactive_model_judge_result")
            if not isinstance(model_result, dict):
                model_result = {}
            judged_at = self._float(user.get("planned_proactive_model_judge_at"))
            items.append(
                {
                    "user_id": str(user_id),
                    "user_label": user_summary.get("display_name") or str(user_id),
                    "user_role": user_summary.get("relationship_role") or "",
                    "user_role_label": user_summary.get("relationship_role_label") or "",
                    "source": source,
                    "source_label": self._proactive_source_label(source),
                    "source_note": self._proactive_source_note(source),
                    "status": status,
                    "action": action,
                    "reason": reason,
                    "reason_label": self._proactive_reason_label(reason, target_name=user_summary.get("display_name") or str(user_id)),
                    "reason_detail": self._proactive_reason_detail(
                        reason=reason,
                        source=source,
                        topic=topic,
                        motive=motive,
                        note=user.get("last_proactive_skip_reason"),
                        target_name=user_summary.get("display_name") or str(user_id),
                    ),
                    "topic": topic,
                    "motive": motive,
                    "planned_impulse_id": self._single_line(user.get("planned_proactive_impulse_id"), 40),
                    "planned_candidate_id": self._single_line(user.get("planned_candidate_id"), 40),
                    **semantic,
                    **window,
                    "inner_readiness": readiness,
                    "afterglow": afterglow,
                    "hesitation": hesitation,
                    "model_judge": {
                        "decision": self._single_line(model_result.get("decision"), 24),
                        "score": self._int(model_result.get("score")),
                        "reason": self._single_line(model_result.get("reason"), 140),
                        "cached": bool(model_result.get("cached")),
                        "judged_ts": judged_at,
                        "judged": self.plugin._format_timestamp_elapsed(judged_at),
                    } if model_result or judged_at > 0 else {},
                    "scheduled_ts": scheduled_ts,
                    "scheduled": self.plugin._format_timestamp_elapsed(scheduled_ts),
                    "last_skip": self.plugin._format_timestamp_elapsed(user.get("last_proactive_skip_at", 0)),
                    "last_skip_ts": self._float(user.get("last_proactive_skip_at")),
                    "last_skip_reason": self._single_line(user.get("last_proactive_skip_reason"), 120),
                    "last_skip_prefix": self._single_line(user.get("last_proactive_skip_prefix"), 20),
                    "created_ts": self._float(timer_event.get("created_at")),
                    "created": self.plugin._format_timestamp_elapsed(timer_event.get("created_at", 0)),
                    "origin": self._single_line(timer_event.get("origin"), 40),
                    "backend": self._single_line(timer_event.get("backend"), 40),
                    "job_id": self._single_line(timer_event.get("job_id"), 80),
                    "timer_status": timer_status,
                    "timer_error": self._single_line(timer_event.get("error") or timer_event.get("replace_error"), 180),
                    "activity": self._single_line(timer_event.get("activity"), 60),
                    "estimated_minutes": self._int(timer_event.get("estimated_minutes")),
                    "followup_intensity": self._int(timer_event.get("followup_intensity")),
                    "replaced_job_id": self._single_line(timer_event.get("replaced_job_id"), 80),
                    "cancelled_job_id": self._single_line(timer_event.get("cancelled_job_id"), 80),
                    "raw_time": self._single_line(timer_event.get("raw_time"), 40),
                    "trigger_message_id": self._single_line(timer_event.get("trigger_message_id"), 120),
                    "trigger_umo": self._single_line(timer_event.get("trigger_umo"), 160),
                    "has_timer_event": bool(timer_event),
                    "silence_until_due": bool(timer_event.get("silence_until_due")) if timer_event else False,
                }
            )

        items.sort(key=lambda item: self._float(item.get("scheduled_ts")))
        raw_audit = data.get("proactive_audit_log") if isinstance(data.get("proactive_audit_log"), list) else []
        seen_audit_signatures: dict[str, dict[str, Any]] = {}
        for raw in raw_audit:
            if not isinstance(raw, dict):
                continue
            meta_leak_checker = getattr(self.plugin, "_framework_agent_meta_summary_leak", None)
            if callable(meta_leak_checker) and (
                meta_leak_checker(str(raw.get("text_preview") or ""))
                or meta_leak_checker(str(raw.get("original_text_preview") or ""))
                or meta_leak_checker(str(raw.get("final_text_preview") or ""))
                or meta_leak_checker(str(raw.get("text") or ""))
                or meta_leak_checker(str(raw.get("note") or ""))
                or meta_leak_checker(str(raw.get("diagnostic_detail") or ""))
            ):
                continue
            user_id = str(raw.get("user_id") or "")
            user = users.get(user_id) if isinstance(users.get(user_id), dict) else {}
            user_summary = self._user_summary(user_id, user) if user_id else {}
            status = self._single_line(raw.get("status"), 32) or "unknown"
            note = self._single_line(raw.get("note"), 180)
            diagnostic_detail = self._single_line(raw.get("diagnostic_detail"), 2400)
            obsolete_checker = getattr(self.plugin, "_proactive_audit_note_is_obsolete_fixed_error", None)
            if callable(obsolete_checker) and obsolete_checker(note):
                status = "obsolete"
                note = "旧版本主动发送变量错误，当前版本已修复"
            if status == "obsolete":
                continue
            audit_reason = self._single_line(raw.get("reason"), 40)
            audit_action = self._single_line(raw.get("action"), 60)
            audit_topic = self._single_line(raw.get("topic"), 100)
            audit_motive = self._single_line(raw.get("motive"), 180)
            sanitizer = getattr(self.plugin, "_sanitize_friend_proactive_plan_fields", None)
            if isinstance(user, dict) and callable(sanitizer):
                sanitized = sanitizer(
                    user,
                    reason=audit_reason,
                    action=audit_action,
                    topic=audit_topic,
                    motive=audit_motive,
                )
                audit_reason = self._single_line(sanitized.get("reason"), 40) or audit_reason
                audit_action = self._single_line(sanitized.get("action"), 60) or audit_action
                audit_topic = self._single_line(sanitized.get("topic"), 100)
                audit_motive = self._single_line(sanitized.get("motive"), 180)
            updated_ts = self._float(raw.get("updated_ts"))
            bucket = int(updated_ts // 300) if updated_ts > 0 else 0
            signature = "|".join(
                self._single_line(value, 120)
                for value in (
                    user_id,
                    status,
                    self._single_line(raw.get("source"), 40),
                    audit_reason,
                    audit_action,
                    audit_topic,
                    audit_motive,
                    note,
                    bucket,
                )
            )
            text_preview = self._display_message_text(raw.get("text_preview"), 180)
            original_text_preview = self._display_message_text(raw.get("original_text_preview"), 180)
            final_text_preview = self._display_message_text(raw.get("final_text_preview"), 180)
            existing = seen_audit_signatures.get(signature)
            if existing is not None:
                previous_updated = self._float(existing.get("updated_ts"))
                existing["updated_ts"] = max(previous_updated, updated_ts)
                existing["updated"] = self.plugin._format_timestamp_elapsed(existing["updated_ts"])
                existing["duplicate_count"] = max(1, self._int(existing.get("duplicate_count"))) + max(1, self._int(raw.get("duplicate_count")))
                if updated_ts >= previous_updated:
                    if text_preview:
                        existing["text_preview"] = text_preview
                    if original_text_preview:
                        existing["original_text_preview"] = original_text_preview
                    if final_text_preview:
                        existing["final_text_preview"] = final_text_preview
                    if diagnostic_detail:
                        existing["diagnostic_detail"] = diagnostic_detail
                continue
            audit_status_counts[status] = audit_status_counts.get(status, 0) + 1
            item = {
                "id": self._single_line(raw.get("id"), 40),
                "user_id": user_id,
                "user_label": user_summary.get("display_name") or user_id,
                "user_role": user_summary.get("relationship_role") or "",
                "user_role_label": user_summary.get("relationship_role_label") or "",
                "status": status,
                "source": self._single_line(raw.get("source"), 40),
                "source_label": self._proactive_source_label(raw.get("source")),
                "source_note": self._proactive_source_note(raw.get("source")),
                "reason": audit_reason,
                "reason_label": self._proactive_reason_label(audit_reason, target_name=user_summary.get("display_name") or user_id),
                "reason_detail": self._proactive_reason_detail(
                    reason=audit_reason,
                    source=raw.get("source"),
                    topic=audit_topic,
                    motive=audit_motive,
                    note="",
                    target_name=user_summary.get("display_name") or user_id,
                ),
                "action": audit_action,
                "topic": audit_topic,
                "motive": audit_motive,
                "semantic_kind": self._single_line(raw.get("semantic_kind"), 40),
                "semantic_anchor_type": self._single_line(raw.get("semantic_anchor_type"), 40),
                "semantic_score": self._int(raw.get("semantic_score")),
                "semantic_pressure": self._int(raw.get("semantic_pressure")),
                "semantic_risk": self._int(raw.get("semantic_risk")),
                "semantic_note": self._single_line(raw.get("semantic_note"), 180),
                "need_layer": self._single_line(raw.get("need_layer") or raw.get("semantic_need_layer"), 40),
                "need_level": self._single_line(raw.get("need_layer") or raw.get("semantic_need_layer"), 40),
                "need_drive": self._single_line(raw.get("need_drive") or raw.get("semantic_need_drive"), 80),
                "need_note": self._single_line(raw.get("need_note") or raw.get("semantic_need_note"), 120),
                "need_score_bias": raw.get("need_score_bias", raw.get("semantic_need_score_bias")),
                "need_pressure_bias": raw.get("need_pressure_bias", raw.get("semantic_need_pressure_bias")),
                "note": note,
                "diagnostic_detail": diagnostic_detail,
                "text_preview": text_preview,
                "original_text_preview": original_text_preview,
                "final_text_preview": final_text_preview,
                "scheduled_ts": self._float(raw.get("scheduled_ts")),
                "scheduled": self.plugin._format_timestamp_elapsed(raw.get("scheduled_ts", 0)),
                "created_ts": self._float(raw.get("created_ts")),
                "created": self.plugin._format_timestamp_elapsed(raw.get("created_ts", 0)),
                "updated_ts": updated_ts,
                "updated": self.plugin._format_timestamp_elapsed(raw.get("updated_ts", 0)),
                "expects_reply": bool(raw.get("expects_reply")),
                "outcome": self._single_line(raw.get("outcome"), 32),
                "outcome_at": self._float(raw.get("outcome_at")),
                "outcome_latency_seconds": self._int(raw.get("outcome_latency_seconds")),
                "candidate_id": self._single_line(raw.get("candidate_id"), 40),
                "has_image": bool(_path_text(raw.get("image_path"), 1000)),
                "extra_count": self._int(raw.get("extra_count")),
                "duplicate_count": max(1, self._int(raw.get("duplicate_count"))),
            }
            seen_audit_signatures[signature] = item
            audit_items.append(item)
        audit_items.sort(key=lambda item: self._float(item.get("updated_ts") or item.get("created_ts")), reverse=True)
        runtime = data.get("proactive_runtime") if isinstance(data.get("proactive_runtime"), dict) else {}
        last_tick_started = self._float(runtime.get("last_tick_started_at")) if runtime else 0
        last_tick_finished = self._float(runtime.get("last_tick_finished_at")) if runtime else 0
        tick_age = now - max(last_tick_started, last_tick_finished) if max(last_tick_started, last_tick_finished) > 0 else -1
        expected_interval = max(30, self._int(getattr(self.plugin, "check_interval_seconds", 60)))
        review_summary_getter = getattr(self.plugin, "_proactive_review_audit_summary", None)
        review_summary = review_summary_getter(now=now, window_days=7) if callable(review_summary_getter) else {}
        if not isinstance(review_summary, dict):
            review_summary = {}
        return {
            "total": len(items),
            "source_counts": source_counts,
            "status_counts": status_counts,
            "items": items[:30],
            "audit_total": len(audit_items),
            "audit_status_counts": audit_status_counts,
            "audit_items": audit_items[:40],
            "review_summary": review_summary,
            "user_states": sorted(
                user_states,
                key=lambda item: (
                    0 if item.get("user_role") == "owner" else 1,
                    self._float(item.get("next_proactive_ts")) if self._float(item.get("next_proactive_ts")) > 0 else 9999999999,
                    self._single_line(item.get("user_label"), 40),
                ),
            )[:40],
            "runtime": {
                "last_tick_started_ts": last_tick_started,
                "last_tick_started": self.plugin._format_timestamp_elapsed(last_tick_started),
                "last_tick_finished_ts": last_tick_finished,
                "last_tick_finished": self.plugin._format_timestamp_elapsed(last_tick_finished),
                "tick_age_seconds": round(tick_age, 1) if tick_age >= 0 else -1,
                "expected_interval_seconds": expected_interval,
                "healthy": bool(tick_age >= 0 and tick_age <= max(180, expected_interval * 4)),
                "last_tick_error": self._single_line(runtime.get("last_tick_error"), 180) if runtime else "",
            },
        }
