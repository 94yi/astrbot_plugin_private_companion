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
from .page_api_shared import _page_api_host, _page_api_host_request as request
from .page_api_diagnostics_overview import PrivateCompanionPageApiDiagnosticsOverviewMixin
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


class PrivateCompanionPageApiDiagnosticsMixin(PrivateCompanionPageApiDiagnosticsOverviewMixin):
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
        payload = await request.get_json(silent=True) or {}
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
        payload = await request.get_json(silent=True) or {}
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
