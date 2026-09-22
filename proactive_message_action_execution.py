# -*- coding: utf-8 -*-
"""action_execution 域。

由 tools/split_mixin_domain.py 从 proactive_message.py 机械抽取（43 个方法 + 0 个模块级名字 + 0 个类级赋值 / 1409 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveMessageMixin）。
"""
from __future__ import annotations

import asyncio
import importlib
import math
import os
import random
import re
import sys
from .conversation_prompt_section import (
    PromptDocument,
    PromptRenderMode,
    prompt_document,
    prompt_section,
    render_prompt_document,
    render_prompt_sections,
)
from .helpers import _now_ts, _safe_float, _safe_int, _single_line, _today_key
from .persona_config import runtime_persona_setting
from .proactive_message_shared import _PROACTIVE_DOCUMENT_RENDER, _proactive_prompt_part
from astrbot.api.event import AstrMessageEvent
from copy import deepcopy
from typing import Any

from .logging_util import get_module_logger

logger = get_module_logger(__name__)





# ---- 宿主全局转发层（由 tmp/refactor/autofix_domain_globals.py 生成）----
# ==== 需实时转发（可被 patch）：同名函数转发宿主 ====
def _now_ts(*args, **kwargs):
    from . import proactive_message as _host
    return getattr(_host, "_now_ts")(*args, **kwargs)
# ---- 宿主全局转发层结束 ----

class ProactiveMessageActionExecutionMixin:
    """action_execution 域（从 ProactiveMessageMixin 拆出）。"""


    async def _execute_proactive_action(
        self,
        action: str,
        user: dict[str, Any],
        name: str,
        reason: str,
    ) -> dict[str, Any]:
        normalized = str(action or "message").strip() or "message"
        parts = [part.strip() for part in normalized.split("+") if part.strip()]
        if not parts:
            parts = ["message"]
        contexts: list[str] = []
        extra_components: list[Any] = []
        summary_parts: list[str] = []
        effective_parts: list[str] = []
        for part in parts:
            payload = await self._execute_single_action(part, user, name, reason)
            contexts.append(str(payload.get("context") or "").strip())
            extra_components.extend(list(payload.get("extra_components") or []))
            summary = _single_line(payload.get("summary") or part, 60)
            if summary:
                summary_parts.append(summary)
            effective_action = _single_line(payload.get("effective_action") or part, 40)
            if effective_action:
                effective_parts.append(effective_action)
            if not bool(payload.get("success", True)):
                return {
                    "success": False,
                    "context": "\n".join(item for item in contexts if item),
                    "extra_components": [],
                    "summary": " + ".join(summary_parts) or normalized,
                    "effective_action": "+".join(effective_parts) or normalized,
                }
        return {
            "success": True,
            "context": "\n".join(item for item in contexts if item) or "message：只发送私聊文本",
            "extra_components": extra_components,
            "summary": " + ".join(summary_parts) or normalized,
            "effective_action": "+".join(effective_parts) or normalized,
        }

    async def _execute_single_action(
        self,
        action: str,
        user: dict[str, Any],
        name: str,
        reason: str,
    ) -> dict[str, Any]:
        fallback_action = self._fallback_action_for_unavailable(action, user)
        if fallback_action != action:
            logger.info(
                "主动行为依赖不可用,已回退: requested=%s fallback=%s user=%s",
                action,
                fallback_action,
                str(user.get("user_id") or ""),
            )
            if fallback_action == "message":
                return {
                    "success": True,
                    "context": "message：只发送普通私聊文本",
                    "extra_components": [],
                    "summary": "文字",
                    "effective_action": "message",
                }
            return await self._execute_single_action(fallback_action, user, name, reason)
        if action == "screen_peek":
            context = await self._run_screen_peek_action(
                user,
                name,
                reason,
                quota_exempt=bool(user.get("planned_proactive_quota_exempt")),
            )
            return {
                "success": not self._is_unusable_screen_peek_context(context),
                "context": context,
                "extra_components": [],
                "summary": "窥屏",
                "effective_action": "screen_peek",
            }
        if action == "photo_text":
            context = await self._run_photo_text_action(user, name, reason)
            image_ready = "真实图片" in context and "图片路径：" in context
            if not image_ready and reason == "birthday_celebration":
                return {
                    "success": True,
                    "context": "message：生日卡未生成，改为只发送生日祝福正文",
                    "extra_components": [],
                    "summary": "生日祝福文字",
                    "effective_action": "message",
                }
            return {
                "success": image_ready,
                "context": context,
                "extra_components": [],
                "summary": "发图",
                "effective_action": "photo_text",
            }
        if action == "poke":
            context = await self._run_poke_action(user, name, reason)
            return {
                "success": context.startswith("poke：已"),
                "context": context,
                "extra_components": [],
                "summary": "戳了你一下",
                "effective_action": "poke",
            }
        if "voice" in action and "photo_text" not in action:
            payload = await self._run_voice_action(user, name, reason)
            payload.setdefault("summary", "留了句语音")
            payload.setdefault("effective_action", "voice")
            return payload
        if action.startswith("external:"):
            return await self._execute_external_proactive_ability(action.split(":", 1)[1], user, name, reason)
        return {"success": True, "context": "message：只发送私聊文本", "extra_components": [], "summary": "文字", "effective_action": "message"}

    async def _execute_external_proactive_ability(
        self,
        ability_name: str,
        user: dict[str, Any],
        display_name: str,
        reason: str,
    ) -> dict[str, Any]:
        user = user if isinstance(user, dict) else {}
        name = self._normalize_external_ability_name(ability_name)
        runtime = self._external_proactive_abilities.get(name)
        if not isinstance(runtime, dict) or not callable(runtime.get("executor")):
            return {"success": False, "context": "external：外部主动能力未注册或不可用", "extra_components": [], "summary": "外部能力不可用", "effective_action": "message"}
        user_key = _single_line(
            user.get("user_id") or user.get("id") or user.get("umo"),
            180,
        ) or "global"
        lock_key = f"{name}:{user_key}"
        locks = getattr(self, "_external_ability_execution_locks", None)
        if not isinstance(locks, dict):
            locks = {}
            self._external_ability_execution_locks = locks
        lock = locks.get(lock_key)
        if not isinstance(lock, asyncio.Lock):
            if len(locks) >= 512:
                for old_key, old_lock in list(locks.items()):
                    if isinstance(old_lock, asyncio.Lock) and not old_lock.locked():
                        locks.pop(old_key, None)
                    if len(locks) < 384:
                        break
            lock = asyncio.Lock()
            locks[lock_key] = lock
        async with lock:
            runtime = self._external_proactive_abilities.get(name)
            if not isinstance(runtime, dict) or not callable(runtime.get("executor")):
                return {
                    "success": False,
                    "context": "external：外部主动能力未注册或不可用",
                    "extra_components": [],
                    "summary": "外部能力不可用",
                    "effective_action": "message",
                }
            return await self._execute_external_proactive_ability_locked(
                name,
                runtime,
                user,
                display_name,
                reason,
            )

    async def _execute_external_proactive_ability_locked(
        self,
        name: str,
        runtime: dict[str, Any],
        user: dict[str, Any],
        display_name: str,
        reason: str,
    ) -> dict[str, Any]:
        available = {
            self._normalize_external_ability_name(item.get("name"))
            for item in self._available_external_proactive_abilities(user)
            if isinstance(item, dict)
        }
        if name not in available:
            return {
                "success": False,
                "context": f"external:{name}：当前不可用或仍在冷却",
                "extra_components": [],
                "summary": "外部能力暂不可用",
                "effective_action": "message",
            }
        config = self._external_ability_config(name)
        call_context = {
            "user": dict(user or {}),
            "display_name": display_name,
            "reason": reason,
            "bot_name": runtime_persona_setting(self, "bot_name", "小星"),
            "state": deepcopy(self.data.get("daily_state", {})),
            "current_plan_item": deepcopy(self._proactive_current_plan_item(self.data.get("daily_plan", {})) or {}),
            "config": config,
            "plugin": self,
        }
        try:
            result = runtime["executor"](call_context)
            if hasattr(result, "__await__"):
                result = await result
        except Exception as exc:
            logger.warning("外部主动能力执行失败: %s: %s", name, exc, exc_info=True)
            self._note_external_ability_execution(
                name,
                user=user,
                success=False,
                status=f"执行失败: {exc}",
            )
            return {"success": False, "context": f"external:{name}：执行失败", "extra_components": [], "summary": "外部能力失败", "effective_action": f"external:{name}"}
        payload = result if isinstance(result, dict) else {"text": str(result or "")}
        success = bool(payload.get("ok", payload.get("success", True)))
        text = _single_line(payload.get("text"), 500)
        context = str(payload.get("context") or payload.get("summary") or text or "").strip()
        image_path = str(payload.get("image_path") or "").strip()
        extra_components = list(payload.get("extra_components") or []) if isinstance(payload.get("extra_components"), list) else []
        if image_path and os.path.exists(image_path):
            extra_components.extend(self._build_outbound_chain("", image_path))
        snapshot = payload.get("photo_snapshot") if isinstance(payload.get("photo_snapshot"), dict) else {}
        if success and image_path and os.path.exists(image_path) and snapshot:
            remember = getattr(self, "_remember_recent_photo_share_snapshot", None)
            if callable(remember):
                remember(
                    user,
                    caption=_single_line(snapshot.get("caption"), 260),
                    topic=_single_line(snapshot.get("topic"), 100),
                    motive=_single_line(snapshot.get("motive"), 180),
                    reason=_single_line(snapshot.get("reason"), 40) or name,
                    subject_owner=_single_line(snapshot.get("subject_owner"), 20),
                )
        memory = _single_line(payload.get("memory"), 500)
        if memory:
            user.setdefault("external_proactive_memory", [])
            memories = user.get("external_proactive_memory")
            if not isinstance(memories, list):
                memories = []
                user["external_proactive_memory"] = memories
            memories.append({"name": name, "ts": _now_ts(), "memory": memory})
            del memories[:-12]
        self._note_external_ability_execution(
            name,
            user=user,
            success=success,
            status=_single_line(payload.get("status") or context, 120),
            summary=_single_line(payload.get("summary") or text, 120),
        )
        return {
            "success": success,
            "context": f"external:{name}：{context or '外部能力已执行'}",
            "extra_components": extra_components,
            "summary": _single_line(payload.get("summary") or runtime.get("label") or name, 60),
            "effective_action": f"external:{name}",
        }

    def _note_external_ability_execution(
        self,
        name: str,
        *,
        user: dict[str, Any] | None = None,
        success: bool,
        status: str = "",
        summary: str = "",
    ) -> None:
        try:
            store = self._external_ability_store()
            item = store.get(name) if isinstance(store.get(name), dict) else {"name": name}
            executed_at = _now_ts()
            item["last_executed_ts"] = executed_at
            item["last_status"] = status
            item["last_summary"] = summary
            item["success_count"] = _safe_int(item.get("success_count"), 0, 0) + (1 if success else 0)
            item["failure_count"] = _safe_int(item.get("failure_count"), 0, 0) + (0 if success else 1)
            store[name] = item
            if isinstance(user, dict):
                user_last = user.setdefault("external_proactive_ability_last", {})
                if not isinstance(user_last, dict):
                    user_last = {}
                    user["external_proactive_ability_last"] = user_last
                user_last[name] = executed_at
            save_sections = {"external_proactive_abilities"}
            if isinstance(user, dict):
                save_sections.add("users")
            self._save_data_sync(sections=save_sections)
        except Exception:
            pass

    def _is_unusable_screen_peek_context(self, context: str) -> bool:
        text = str(context or "").strip()
        if not text:
            return True
        fail_tokens = (
            "screen_peek：失败",
            "屏幕插件不可用",
            "未授权",
            "不可用",
            "Invalid base64 image_url",
            "图片预处理结果为空",
            "所有视觉链路都失败",
            "视觉 provider 调用失败",
            "当前 provider 不支持原生视频上传",
            "没看清",
            "稍后再让我看看",
            "没有得到屏幕观察结果",
            "识屏分析失败",
        )
        return any(token in text for token in fail_tokens)

    def _is_screen_peek_provider_failure(self, context: str) -> bool:
        text = str(context or "")
        fail_tokens = (
            "Invalid base64 image_url",
            "图片预处理结果为空",
            "所有视觉链路都失败",
            "视觉 provider 调用失败",
            "Asset upload returned",
            "BadRequest",
            "InvalidParameter",
        )
        return any(token in text for token in fail_tokens)

    @staticmethod
    def _goodnight_screen_check_reply_matches(text: Any) -> bool:
        cleaned = _single_line(text, 240)
        if not cleaned:
            return False
        return bool(re.search(r"晚安|好梦|早点睡|睡吧|休息吧|明天见", cleaned))

    def _maybe_schedule_goodnight_screen_check(
        self,
        user: dict[str, Any],
        bot_reply: Any,
        *,
        now: float | None = None,
    ) -> bool:
        """Schedule one private screen check after a mutual goodnight."""
        if not bool(runtime_persona_setting(self, "enable_screen_glance_action", False)) or not bool(
            runtime_persona_setting(self, "enable_goodnight_screen_check", False)
        ):
            return False
        if not isinstance(user, dict) or not self._goodnight_screen_check_reply_matches(bot_reply):
            return False
        user_id = _single_line(user.get("user_id") or user.get("id"), 128)
        if not user_id or self._private_user_role(user, user_id) != "owner":
            return False
        umo = _single_line(user.get("umo"), 240)
        if not umo or ":FriendMessage:" not in umo or not user.get("enabled", True):
            return False

        rest_kind = _single_line(user.get("user_rest_kind"), 24).lower()
        rest_set_at = _safe_float(user.get("user_rest_set_at"), 0)
        rest_reason = _single_line(user.get("user_rest_reason"), 240)
        if rest_kind != "sleep" or rest_set_at <= 0:
            return False
        quiet_checker = getattr(self, "_user_rest_signal_should_block_current_reply", None)
        if callable(quiet_checker) and quiet_checker(rest_reason):
            return False

        check_now = _now_ts() if now is None else float(now)
        if check_now + 0.001 < rest_set_at or check_now - rest_set_at > 30 * 60:
            return False
        episode_key = f"{user_id}:{rest_set_at:.3f}"
        if _single_line(user.get("goodnight_screen_check_episode_key"), 180) == episode_key:
            return False
        if _single_line(user.get("goodnight_screen_check_checked_episode_key"), 180) == episode_key:
            return False

        delay_minutes = max(
            1,
            min(
                180,
                _safe_int(
                    runtime_persona_setting(
                        self, "goodnight_screen_check_delay_minutes", 45
                    ),
                    45,
                    1,
                    180,
                ),
            ),
        )
        user["goodnight_screen_check_due_at"] = check_now + delay_minutes * 60
        user["goodnight_screen_check_episode_at"] = rest_set_at
        user["goodnight_screen_check_episode_key"] = episode_key
        user["goodnight_screen_check_scheduled_at"] = check_now
        user["goodnight_screen_check_checked_at"] = 0
        user["goodnight_screen_check_state"] = "scheduled"
        return True

    def _goodnight_screen_check_block_reason(
        self,
        user_id: str,
        user: dict[str, Any],
        *,
        episode_at: float,
        now: float,
        require_screen: bool,
    ) -> str:
        if not bool(runtime_persona_setting(self, "enable_screen_glance_action", False)):
            return "screen_glance_disabled"
        if not bool(runtime_persona_setting(self, "enable_goodnight_screen_check", False)):
            return "goodnight_screen_check_disabled"
        if not isinstance(user, dict) or self._private_user_role(user, user_id) != "owner":
            return "not_primary_user"
        enabled_checker = getattr(self, "_user_enabled_for_proactive", None)
        if callable(enabled_checker) and not enabled_checker(user_id, user):
            return "private_proactive_disabled"
        umo = _single_line(user.get("umo"), 240)
        if not umo or ":FriendMessage:" not in umo:
            return "private_route_unavailable"
        generation_disabled = getattr(self, "_proactive_generation_disabled", None)
        if callable(generation_disabled) and generation_disabled(user):
            return "proactive_generation_disabled"

        rest_set_at = _safe_float(user.get("user_rest_set_at"), 0)
        if _single_line(user.get("user_rest_kind"), 24).lower() != "sleep" or abs(rest_set_at - episode_at) > 0.01:
            return "goodnight_episode_ended"
        rest_reason = _single_line(user.get("user_rest_reason"), 240)
        quiet_checker = getattr(self, "_user_rest_signal_should_block_current_reply", None)
        if callable(quiet_checker) and quiet_checker(rest_reason):
            return "explicit_do_not_disturb"
        latest_activity = max(
            _safe_float(user.get("last_activity_at"), 0),
            _safe_float(user.get("last_user_message_at"), 0),
        )
        if latest_activity > episode_at + 0.001:
            return "user_active_after_goodnight"
        rest_until_getter = getattr(self, "_user_rest_silence_until", None)
        if callable(rest_until_getter) and rest_until_getter(user, now=now) <= now:
            return "rest_window_ended"

        reset_daily = getattr(self, "_reset_daily_counter_if_needed", None)
        if callable(reset_daily):
            reset_daily(user)
        daily_limit_getter = getattr(self, "_effective_user_daily_limit", None)
        daily_limit = daily_limit_getter(user) if callable(daily_limit_getter) else 0
        unlimited_checker = getattr(self, "_proactive_daily_limit_is_unlimited", None)
        unlimited = bool(unlimited_checker(daily_limit)) if callable(unlimited_checker) else False
        if daily_limit <= 0 or (not unlimited and _safe_int(user.get("sent_today"), 0) >= daily_limit):
            return "daily_proactive_limit"

        expression_builder = getattr(self, "_build_expression_decision_for_user", None)
        if not callable(expression_builder):
            return "expression_decision_unavailable"
        try:
            decision = expression_builder(
                user,
                proactive_candidate={"eligible": True, "dynamic_allowance": daily_limit, "current_ts": now},
                message_intent={"requested_content_tier": "normal"},
                now=now,
            )
            expression = decision.to_dict() if hasattr(decision, "to_dict") else dict(decision or {})
        except Exception:
            return "expression_decision_unavailable"
        if _single_line(expression.get("blocker"), 40):
            return f"expression_{_single_line(expression.get('blocker'), 40)}"
        if _safe_float(expression.get("proactive_cooldown_until"), 0) > now:
            return "expression_proactive_cooldown"
        if _safe_int(expression.get("proactive_budget"), 0, 0) <= 0:
            return "expression_proactive_budget_zero"
        if bool(user.get("proactive_sending")):
            return "another_proactive_message_is_sending"
        if require_screen and not self._screen_glance_available(user):
            return "screen_glance_unavailable"
        return ""

    @staticmethod
    def _goodnight_screen_check_prompt_document() -> PromptDocument:
        return prompt_document(
            user_render=_PROACTIVE_DOCUMENT_RENDER,
            user=(
                _proactive_prompt_part(prompt_section(
                    key="background.goodnight_screen_check",
                    title="晚安后屏幕状态判断",
                    source="proactive_message",
                    content=(
                        "这是一次用户已授权的晚安后单次状态确认，只用于决定是否需要轻声提醒休息。"
                        "请只判断当前画面是否能明确证明用户仍在主动使用电脑，不要转述或摘录任何屏幕内容。"
                        "active 仅用于存在明确持续操作或正在进行活动的证据；画面静止、锁屏、黑屏、无人操作、"
                        "证据不足或无法判断都输出 inactive 或 uncertain。"
                        '只输出 JSON：{"state":"active|inactive|uncertain","reason":"不含隐私的极短判断依据"}。'
                        "reason 禁止包含应用名、窗口名、账号、联系人、文件名、聊天内容、网页内容或屏幕文字。"
                    ),
                ), mode=PromptRenderMode.BODY_ONLY),
            ),
            metadata={"task": "goodnight_screen_check"},
        )

    @staticmethod
    def _goodnight_screen_check_history_text(name: str) -> str:
        section = prompt_section(
            key="background.goodnight_screen_check.history",
            title="晚安后单次确认历史问题",
            source="proactive_message",
            content=f"晚安后单次确认 {name or '用户'} 是否仍在主动使用电脑。",
        )
        return render_prompt_sections([section], mode=PromptRenderMode.BODY_ONLY)

    @staticmethod
    def _screen_peek_history_text(name: str) -> str:
        section = prompt_section(
            key="background.screen_peek.history",
            title="主动屏幕观察历史问题",
            source="proactive_message",
            content=f"主动陪伴想轻轻看一眼 {name} 现在在忙什么。",
        )
        return render_prompt_sections([section], mode=PromptRenderMode.BODY_ONLY)

    async def _classify_goodnight_screen_activity(
        self,
        user_id: str,
        user: dict[str, Any],
        *,
        name: str,
    ) -> str:
        plugin = self._get_screen_companion_plugin()
        if plugin is None or not callable(getattr(plugin, "_invoke_screen_skill", None)):
            return "uncertain"
        async with self._data_lock:
            current = self._get_user(user_id)
            self._note_screen_peek_attempt(user_id, reason="goodnight_screen_check", count_daily=True)
            self._save_data_sync(sections={"users"})

        event = None
        target = _single_line(user.get("umo"), 240)
        if target and hasattr(plugin, "_create_virtual_event"):
            try:
                event = plugin._create_virtual_event(target)
            except Exception as exc:
                logger.debug("创建晚安识屏虚拟事件失败: %s", _single_line(exc, 160))
        prompt = render_prompt_document(
            self._goodnight_screen_check_prompt_document()
        )["user"]
        try:
            result = await plugin._invoke_screen_skill(
                event,
                request_prompt=prompt,
                history_user_text=self._goodnight_screen_check_history_text(name),
                task_id="private_companion_goodnight_screen_check",
            )
        except Exception as exc:
            context = f"goodnight_screen_check：失败,{_single_line(exc, 240)}"
            logger.warning("晚安识屏判断失败: %s", _single_line(exc, 180))
            if self._is_screen_peek_provider_failure(context):
                self._note_screen_peek_failure(user, context)
            return "uncertain"
        if self._is_screen_peek_provider_failure(str(result or "")):
            self._note_screen_peek_failure(user, _single_line(result, 180))
            return "uncertain"
        parser = getattr(self, "_parse_json_object", None)
        parsed = parser(result) if callable(parser) else None
        if not isinstance(parsed, dict) and isinstance(result, dict):
            parsed = result
        state = _single_line(parsed.get("state"), 24).lower() if isinstance(parsed, dict) else ""
        return state if state in {"active", "inactive", "uncertain"} else "uncertain"

    async def _maybe_process_goodnight_screen_checks(self) -> None:
        now = _now_ts()
        claimed: list[tuple[str, float, str]] = []
        changed = False
        async with self._data_lock:
            users = self.data.get("users")
            if not isinstance(users, dict):
                return
            for raw_user_id, user in users.items():
                if not isinstance(user, dict):
                    continue
                due_at = _safe_float(user.get("goodnight_screen_check_due_at"), 0)
                if due_at <= 0 or due_at > now:
                    continue
                user_id = _single_line(user.get("user_id") or raw_user_id, 128)
                episode_at = _safe_float(user.get("goodnight_screen_check_episode_at"), 0)
                episode_key = _single_line(user.get("goodnight_screen_check_episode_key"), 180)
                user["goodnight_screen_check_due_at"] = 0
                user["goodnight_screen_check_checked_at"] = now
                user["goodnight_screen_check_checked_episode_key"] = episode_key
                user["goodnight_screen_check_state"] = "claimed"
                changed = True
                if user_id and episode_at > 0:
                    claimed.append((user_id, episode_at, episode_key))
            if changed:
                self._save_data_sync(sections={"users"})

        for user_id, episode_at, episode_key in claimed:
            async with self._data_lock:
                user = self._get_user(user_id)
                block_reason = self._goodnight_screen_check_block_reason(
                    user_id,
                    user,
                    episode_at=episode_at,
                    now=_now_ts(),
                    require_screen=True,
                )
                if block_reason:
                    user["goodnight_screen_check_state"] = block_reason
                    self._save_data_sync(sections={"users"})
                    continue
                name = _single_line(user.get("nickname"), 40) or user_id

            state = await self._classify_goodnight_screen_activity(user_id, user, name=name)
            async with self._data_lock:
                current = self._get_user(user_id)
                current["goodnight_screen_check_state"] = state
                current["goodnight_screen_check_result_at"] = _now_ts()
                self._save_data_sync(sections={"users"})
            if state != "active":
                continue

            async with self._data_lock:
                current = self._get_user(user_id)
                block_reason = self._goodnight_screen_check_block_reason(
                    user_id,
                    current,
                    episode_at=episode_at,
                    now=_now_ts(),
                    require_screen=False,
                )
                if block_reason:
                    current["goodnight_screen_check_state"] = block_reason
                    self._save_data_sync(sections={"users"})
                    continue
                current["proactive_sending"] = True
                current["proactive_sending_started_at"] = _now_ts()
                user = current
                name = _single_line(current.get("nickname"), 40) or user_id
                umo = _single_line(current.get("umo"), 240)
                self._save_data_sync(sections={"users"})

            motive = "互道晚安后仍有明确活动迹象，轻声提醒一次早点休息，不要求回复"
            safe_context = "内部状态判断：晚安后仍有明确活动迹象；没有提供任何屏幕内容或应用信息"
            try:
                text = await self._generate_proactive_message_with_llm(
                    user,
                    name,
                    "goodnight_screen_check",
                    action_context=safe_context,
                    action="message",
                    motive=motive,
                )
                if not text:
                    continue
                review = await self._review_proactive_message_send_decision(
                    user,
                    text,
                    reason="goodnight_screen_check",
                    action="message",
                    motive=motive,
                    topic="早点休息",
                    action_summary=safe_context,
                )
                decision = _single_line(review.get("decision"), 20).lower()
                if decision in {"drop", "defer"}:
                    continue
                if decision == "rewrite" and _single_line(review.get("text"), 500):
                    text = _single_line(review.get("text"), 500)
                outcome = await self._send_proactive_message_chain(umo, text)
                if not bool(getattr(outcome, "delivered", False)):
                    continue
                delivered_text = str(
                    getattr(outcome, "delivered_text", "") or text
                ).strip()
                delivery_umo = str(
                    getattr(outcome, "delivery_umo", "") or umo
                ).strip()
                assistant_archive_text = self._delivered_assistant_text_from_chain(
                    list(getattr(outcome, "delivered_chain", ()) or ()),
                    fallback_text=delivered_text,
                )
                if getattr(self, "context", None) is not None:
                    await self._archive_proactive_message_to_conversation(
                        user=user,
                        umo=delivery_umo,
                        user_prompt=self._build_proactive_archive_user_prompt(
                            reason="goodnight_screen_check",
                            action="message",
                            motive=motive,
                            action_summary=safe_context,
                        ),
                        assistant_response=assistant_archive_text,
                    )
                await self._record_final_assistant_in_livingmemory(
                    umo=delivery_umo,
                    assistant_response=assistant_archive_text,
                    delivery_id=f"goodnight:{user_id}:{_now_ts():.6f}",
                )
                memory_companion_recorder = getattr(
                    self,
                    "_memory_companion_record_proactive_message",
                    None,
                )
                if callable(memory_companion_recorder):
                    await memory_companion_recorder(
                        user=user,
                        user_id=user_id,
                        text=delivered_text,
                        umo=delivery_umo,
                        reason="goodnight_screen_check",
                        action="message",
                        motive=motive,
                        action_summary=safe_context,
                    )
                sent_at = _now_ts()
                visible = self._visible_text_without_tts_reading(delivered_text, limit=500)
                async with self._data_lock:
                    current = self._get_user(user_id)
                    self._reset_daily_counter_if_needed(current)
                    current["last_sent"] = sent_at
                    current["last_proactive_sent_at"] = sent_at
                    current["last_proactive_message"] = _single_line(visible, 500)
                    current["last_companion_message"] = _single_line(visible, 500)
                    current["last_companion_message_at"] = sent_at
                    current["last_proactive_reason"] = "goodnight_screen_check"
                    current["last_proactive_action"] = "message"
                    current["last_proactive_motive"] = motive
                    current["last_proactive_delivery_umo"] = delivery_umo
                    current["last_proactive_delivery_inbound_count"] = _safe_int(current.get("inbound_count"), 0)
                    current["goodnight_screen_check_reminded_at"] = sent_at
                    current["goodnight_screen_check_state"] = "reminded"
                    current["goodnight_screen_check_reminded_episode_key"] = episode_key
                    current["sent_today"] = _safe_int(current.get("sent_today"), 0) + 1
                    current["proactive_sent_count"] = _safe_int(current.get("proactive_sent_count"), 0) + 1
                    self._save_data_sync(sections={"users"})
            finally:
                async with self._data_lock:
                    current = self._get_user(user_id)
                    current["proactive_sending"] = False
                    current["proactive_sending_started_at"] = 0
                    self._save_data_sync(sections={"users"})

    @staticmethod
    def _screen_peek_prompt_document(reason: str) -> PromptDocument:
        return prompt_document(
            user_render=_PROACTIVE_DOCUMENT_RENDER,
            user=(
                _proactive_prompt_part(prompt_section(
                    key="background.screen_peek",
                    title="主动屏幕观察",
                    source="proactive_message",
                    template=(
                        "这是一次用户已授权的主动陪伴行为。请只做视觉观察,"
                        "用很短的话描述用户电脑当前大概在看什么、做什么、是不是像在忙。"
                        "不要直接对用户说话,不要安慰、提醒、关心、陪伴,不要输出隐私细节、账号、完整文本、聊天内容。"
                        "只留一个内部观察印象。主动原因：{reason}"
                    ),
                    variables={"reason": reason},
                ), mode=PromptRenderMode.BODY_ONLY),
            ),
            metadata={"task": "screen_peek"},
        )

    async def _run_screen_peek_action(
        self,
        user: dict[str, Any],
        name: str,
        reason: str,
        *,
        quota_exempt: bool = False,
    ) -> str:
        if not runtime_persona_setting(self, "enable_screen_glance_action", False):
            return "screen_peek：未授权,跳过"
        plugin = self._get_screen_companion_plugin()
        if plugin is None:
            return "screen_peek：屏幕插件不可用"
        target = str(user.get("umo") or "").strip()
        if not self._screen_glance_available(user, ignore_daily_limit=quota_exempt):
            return "screen_peek：今日额度或冷却未满足,跳过"
        async with self._data_lock:
            self._note_screen_peek_attempt(
                str(user.get("user_id") or user.get("umo") or name),
                reason=reason,
                count_daily=not quota_exempt,
            )
            self._save_data_sync(sections={"users"})
        event = None
        if target and hasattr(plugin, "_create_virtual_event"):
            try:
                event = plugin._create_virtual_event(target)
            except Exception as e:
                logger.debug(f"创建屏幕虚拟事件失败: {e}")
        prompt = render_prompt_document(
            self._screen_peek_prompt_document(reason)
        )["user"]
        try:
            result = await plugin._invoke_screen_skill(
                event,
                request_prompt=prompt,
                history_user_text=self._screen_peek_history_text(name),
                task_id="private_companion_screen_peek",
            )
            context = "screen_peek：\n" + (_single_line(result, 300) if result else "没有得到屏幕观察结果")
            if self._is_screen_peek_provider_failure(context):
                self._note_screen_peek_failure(user, context)
            return context
        except Exception as e:
            error_text = _single_line(e, 240)
            logger.warning(f"screen_peek 主动行为失败: {error_text}")
            context = f"screen_peek：失败,{error_text}"
            if self._is_screen_peek_provider_failure(context):
                self._note_screen_peek_failure(user, context)
            return context

    def _get_screen_companion_plugin(self) -> Any:
        # During a hot reload the registry may already contain the current
        # ScreenCompanion instance while the module singleton still points to
        # the previous one. Prefer the instance AstrBot dispatches, then keep
        # the module lookup as a compatibility fallback for older hosts.
        context = getattr(self, "context", None)
        get_one = getattr(context, "get_registered_star", None)
        if callable(get_one):
            try:
                metadata = get_one("astrbot_plugin_screen_companion")
            except Exception:
                metadata = None
            if metadata is not None and bool(getattr(metadata, "activated", True)):
                instance = getattr(metadata, "star_cls", None)
                if instance is not None and callable(getattr(instance, "_invoke_screen_skill", None)):
                    return instance
        for module_name in ("astrbot_plugin_screen_companion.main", "data.plugins.astrbot_plugin_screen_companion.main"):
            try:
                module = importlib.import_module(module_name)
                plugin = getattr(module, "_screen_companion_tool_plugin", None)
                if plugin is not None and callable(getattr(plugin, "_invoke_screen_skill", None)):
                    return plugin
            except Exception:
                continue
        for module in list(sys.modules.values()):
            try:
                plugin = getattr(module, "_screen_companion_tool_plugin", None)
                if plugin is not None and callable(getattr(plugin, "_invoke_screen_skill", None)):
                    return plugin
            except Exception:
                continue
        return None

    def _poke_action_cooldown_remaining(self, user: dict[str, Any] | None, *, now: float | None = None) -> float:
        if not isinstance(user, dict):
            return 0.0
        current_ts = float(now if now is not None else _now_ts())
        inflight_until = _safe_float(user.get("poke_action_inflight_until"), 0.0)
        if inflight_until > current_ts:
            return inflight_until - current_ts
        cooldown_seconds = max(
            0,
            _safe_int(
                runtime_persona_setting(self, "poke_action_cooldown_minutes", 30),
                30,
                0,
                1440,
            ),
        ) * 60
        last_at = _safe_float(user.get("last_poke_action_at"), 0.0)
        return max(0.0, last_at + cooldown_seconds - current_ts) if cooldown_seconds > 0 and last_at > 0 else 0.0

    async def _send_single_poke(self, client: Any, *, user_id: str, group_id: str) -> None:
        if group_id and callable(getattr(client, "group_poke", None)):
            await client.group_poke(group_id=int(group_id), user_id=int(user_id))
            return
        if not group_id and callable(getattr(client, "friend_poke", None)):
            await client.friend_poke(user_id=int(user_id))
            return
        try:
            from data.plugins.astrbot_plugin_pokepro.core.send_poke import PokeSender
        except Exception:
            from astrbot_plugin_pokepro.core.send_poke import PokeSender
        await PokeSender.poke_func(client=client, user_id=user_id, group_id=group_id or None)

    async def _run_poke_action(
        self,
        user: dict[str, Any],
        name: str,
        reason: str,
        *,
        explicit_count: int | None = None,
    ) -> str:
        if not runtime_persona_setting(self, "enable_poke_action", False):
            return "poke：未启用"
        user_umo = str(user.get("umo") or "")
        platform_supports = getattr(self, "_platform_supports", None)
        if callable(platform_supports) and not platform_supports("poke", umo=user_umo):
            return "poke：当前平台不支持戳一戳，已改用普通文字"
        client = self._resolve_aiocqhttp_client()
        if client is None:
            return "poke：未找到可用的 QQ 客户端"
        user_id = str(user.get("user_id") or "").strip()
        if not user_id.isdigit():
            return "poke：目标 QQ 号无效"
        group_id = self._extract_group_id_from_umo(str(user.get("umo") or ""))
        max_count = min(3, max(0, self._effective_user_poke_daily_limit(user)))
        if max_count <= 0:
            return "poke：当前用户未允许主动戳一戳"
        requested_count = int(explicit_count) if explicit_count is not None else self._choose_poke_repeat_count(user, reason)
        poke_count = max(1, min(max_count, requested_count))
        reserved = False
        try:
            async with self._data_lock:
                current = self._get_user(user_id)
                now = _now_ts()
                remaining = self._poke_action_cooldown_remaining(current, now=now)
                if remaining > 0:
                    return f"poke：冷却中，约 {max(1, math.ceil(remaining / 60))} 分钟后可再次执行"
                current["poke_action_inflight_until"] = now + max(30.0, poke_count * 3.0)
                current["poke_echo_suppress_until"] = now + max(30.0, poke_count * 3.0)
                self._save_data_sync(sections={"users"})
                reserved = True
            for index in range(poke_count):
                await self._send_single_poke(client, user_id=user_id, group_id=group_id)
                if index + 1 < poke_count:
                    await asyncio.sleep(random.uniform(0.35, 0.9))
            async with self._data_lock:
                current = self._get_user(user_id)
                current["last_poke_action_at"] = _now_ts()
                current["poke_action_inflight_until"] = 0
                self._save_data_sync(sections={"users"})
            if poke_count <= 1:
                return f"poke：已轻轻戳了 {name} 一下\n主动原因：{reason}"
            return f"poke：已轻轻连着戳了 {name} {poke_count} 下\n主动原因：{reason}"
        except Exception as e:
            if reserved:
                try:
                    async with self._data_lock:
                        current = self._get_user(user_id)
                        current["poke_action_inflight_until"] = 0
                        self._save_data_sync(sections={"users"})
                except Exception:
                    pass
            logger.warning(f"poke 主动行为失败: {e}")
            return f"poke：失败,{e}"

    def _choose_poke_repeat_count(self, user: dict[str, Any], reason: str) -> int:
        max_times = self._effective_user_poke_daily_limit(user)
        if max_times <= 0:
            return 0
        if max_times <= 1:
            return 1
        motive = _single_line(
            user.get("planned_proactive_motive") or user.get("last_proactive_motive"),
            120,
        )
        profile = self._persona_action_profile()
        weights: list[tuple[int, float]] = [(1, 1.0)]
        second_weight = 0.45
        third_weight = 0.12
        if profile.get("playful"):
            second_weight += 0.22
            third_weight += 0.1
        if profile.get("clingy"):
            second_weight += 0.12
            third_weight += 0.06
        if reason in {"quiet_care", "check_in"}:
            second_weight += 0.08
        if any(token in motive for token in ("轻轻叫你", "刷存在感", "碰你一下", "没忍住", "冒个头")):
            second_weight += 0.15
        if any(token in motive for token in ("偷偷看", "放心不下", "想起你", "不想吵你")):
            third_weight += 0.04
        weights.append((2, second_weight))
        if max_times >= 3:
            weights.append((3, third_weight))
        return int(self._weighted_choice([(str(count), weight) for count, weight in weights]))

    def _choose_pre_message_poke_count(
        self,
        user: dict[str, Any],
        reason: str,
        *,
        action: str = "message",
        motive: str = "",
    ) -> int:
        if "poke" in {part.strip() for part in str(action or "").split("+") if part.strip()}:
            return 0
        if (
            not self._poke_available()
            or self._effective_user_poke_daily_limit(user) <= 0
            or self._poke_action_cooldown_remaining(user) > 0
        ):
            return 0
        profile = self._persona_action_profile()
        probability = 0.12
        if reason in {"check_in", "quiet_care", "important_date_share"}:
            probability += 0.22
        if profile.get("playful"):
            probability += 0.14
        if profile.get("clingy"):
            probability += 0.08
        if action in {"voice", "photo_text"}:
            probability -= 0.02
        motive_text = str(motive or user.get("planned_proactive_motive") or "")
        if any(token in motive_text for token in ("轻轻叫你", "戳", "碰碰你", "确认一下", "放心不下", "叫你一声")):
            probability += 0.08
        probability = max(0.0, min(0.72, probability))
        if random.random() >= probability:
            return 0
        return self._choose_poke_repeat_count(user, reason)

    async def _maybe_run_pre_message_poke(
        self,
        user: dict[str, Any],
        name: str,
        reason: str,
        *,
        action: str = "message",
        motive: str = "",
    ) -> tuple[int, str]:
        poke_count = self._choose_pre_message_poke_count(
            user,
            reason,
            action=action,
            motive=motive,
        )
        if poke_count <= 0:
            return 0, ""
        context = await self._run_poke_action(user, name, reason, explicit_count=poke_count)
        if not context.startswith("poke：已"):
            return 0, context
        return poke_count, context

    async def _run_voice_action(self, user: dict[str, Any], name: str, reason: str) -> dict[str, Any]:
        if not runtime_persona_setting(self, "enable_voice_action", False):
            return {"success": False, "context": "voice：未启用", "extra_components": [], "summary": "语音"}
        target = str(user.get("umo") or "").strip()
        if not target:
            return {"success": False, "context": "voice：缺少目标会话,无法发送语音", "extra_components": [], "summary": "语音"}
        voice_text = await self._build_voice_note_text(user, name, reason, target=target)
        touch_allowed = getattr(self, "_reality_touch_proactive_voice_allowed", lambda _: False)(user)
        components, audio_note = await self._create_voice_record_component(
            target,
            voice_text,
            defer_local_playback=touch_allowed,
        )
        if not components:
            return {
                "success": False,
                "context": (
                    "voice：语音生成失败\n"
                    f"想说的话：{voice_text}\n"
                    f"失败原因：{_single_line(audio_note, 160)}"
                ),
                "extra_components": [],
                "summary": "语音",
            }
        touch_player = getattr(self, "_mirror_reality_touch_proactive_voice", None)
        touched = bool(await touch_player(user, audio_note)) if touch_allowed and callable(touch_player) else False
        return {
            "success": True,
            "context": (
                "voice：已生成真实语音\n"
                f"语音内容：{self._strip_tts_markup(voice_text)}\n"
                f"真实语音文件：{audio_note}\n"
                f"现实触及：{'已同步到所选电脑音频设备' if touched else '未同步到电脑音频设备'}"
            ),
            "extra_components": components,
            "summary": "留了句语音",
        }

    def _resolve_aiocqhttp_client(self) -> Any:
        platform_manager = getattr(self.context, "platform_manager", None)
        platforms: list[Any] = []
        if platform_manager is not None:
            try:
                platforms = list(platform_manager.get_insts())
            except Exception:
                platforms = list(getattr(platform_manager, "platform_insts", []) or [])
        for platform in platforms:
            platform_names = set()
            try:
                meta = platform.meta()
                platform_names.add(str(getattr(meta, "id", "") or "").strip())
                platform_names.add(str(getattr(meta, "name", "") or "").strip())
            except Exception:
                pass
            platform_desc = f"{platform.__class__.__module__}.{platform.__class__.__name__}".lower()
            for attr in ("bot", "client", "_bot", "_client", "cqhttp"):
                client = getattr(platform, attr, None)
                client_desc = f"{client.__class__.__module__}.{client.__class__.__name__}".lower() if client is not None else ""
                if client is not None and (
                    "aiocqhttp" in platform_names
                    or "default(aiocqhttp)" in platform_names
                    or "aiocqhttp" in platform_desc
                    or "aiocqhttp" in client_desc
                    or (hasattr(client, "send_private_msg") and hasattr(client, "send_group_msg"))
                    or hasattr(client, "friend_poke")
                    or hasattr(client, "group_poke")
                ):
                    return client
        return None

    def _onebot_action_result_ok(self, result: Any) -> bool:
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
        return True

    @staticmethod
    def _onebot_action_reported_success(action: str, result_or_error: Any) -> bool:
        if str(action or "").strip().lower() != "set_online_status":
            return False
        text = _single_line(result_or_error, 500).lower()
        return "set status success" in text or "set online status success" in text

    def _delivery_outcome_is_uncertain(self, error: Any) -> bool:
        if isinstance(error, (asyncio.TimeoutError, TimeoutError, ConnectionError)):
            return True
        text = _single_line(error, 500).lower()
        return any(
            token in text
            for token in (
                "timed out",
                "timeout",
                "deadline exceeded",
                "read timeout",
                "write timeout",
                "connection reset",
                "connection closed",
                "server disconnected",
                "remote disconnected",
                "broken pipe",
                "回执超时",
                "响应超时",
                "连接被重置",
                "连接已关闭",
            )
        )

    def _log_uncertain_onebot_submission(self, action: str, error: Any) -> None:
        logger.warning(
            "OneBot 动作回执不确定，为避免同一内容被别名立即重复提交，本次按已提交处理: action=%s error=%s",
            action,
            self._format_send_exception(error),
        )

    async def _call_onebot_action(self, client: Any, action: str, **params: Any) -> bool:
        ok, _ = await self._call_onebot_action_with_error(client, action, **params)
        return ok

    async def _call_onebot_action_with_error(
        self,
        client: Any,
        action: str,
        *,
        at_most_once: bool = False,
        **params: Any,
    ) -> tuple[bool, str]:
        candidates = (
            "call_action",
            "call_api",
            "api",
        )
        last_error = ""
        for attr in candidates:
            func = getattr(client, attr, None)
            if not callable(func):
                continue
            try:
                result = func(action, **params)
            except TypeError:
                try:
                    result = func(action, params)
                except Exception as exc:
                    if self._onebot_action_reported_success(action, exc):
                        return True, "协议端已设置状态"
                    if self._is_onebot_event_checker_send_rejection(exc):
                        return False, self._onebot_event_checker_rejection_summary()
                    if at_most_once and self._delivery_outcome_is_uncertain(exc):
                        self._log_uncertain_onebot_submission(action, exc)
                        return True, "回执不确定，已停止立即重试"
                    last_error = self._format_send_exception(exc)
                    if at_most_once:
                        return False, last_error
                    continue
            except Exception as exc:
                if self._onebot_action_reported_success(action, exc):
                    return True, "协议端已设置状态"
                if self._is_onebot_event_checker_send_rejection(exc):
                    return False, self._onebot_event_checker_rejection_summary()
                if at_most_once and self._delivery_outcome_is_uncertain(exc):
                    self._log_uncertain_onebot_submission(action, exc)
                    return True, "回执不确定，已停止立即重试"
                last_error = self._format_send_exception(exc)
                if at_most_once:
                    return False, last_error
                continue
            try:
                if hasattr(result, "__await__"):
                    result = await result
            except Exception as exc:
                if self._onebot_action_reported_success(action, exc):
                    return True, "协议端已设置状态"
                if self._is_onebot_event_checker_send_rejection(exc):
                    return False, self._onebot_event_checker_rejection_summary()
                if at_most_once and self._delivery_outcome_is_uncertain(exc):
                    self._log_uncertain_onebot_submission(action, exc)
                    return True, "回执不确定，已停止立即重试"
                last_error = self._format_send_exception(exc)
                if at_most_once:
                    return False, last_error
                continue
            if self._onebot_action_result_ok(result) or self._onebot_action_reported_success(action, result):
                return True, ""
            last_error = f"{attr} 返回失败: {_single_line(result, 180)}"
            if self._is_onebot_event_checker_send_rejection(result):
                return False, self._onebot_event_checker_rejection_summary()
            if at_most_once:
                return False, last_error
        func = getattr(client, action, None)
        if callable(func):
            try:
                result = func(**params)
            except Exception as exc:
                if self._onebot_action_reported_success(action, exc):
                    return True, "协议端已设置状态"
                if at_most_once and self._delivery_outcome_is_uncertain(exc):
                    self._log_uncertain_onebot_submission(action, exc)
                    return True, "回执不确定，已停止立即重试"
                return False, self._format_send_exception(exc)
            try:
                if hasattr(result, "__await__"):
                    result = await result
            except Exception as exc:
                if self._onebot_action_reported_success(action, exc):
                    return True, "协议端已设置状态"
                if at_most_once and self._delivery_outcome_is_uncertain(exc):
                    self._log_uncertain_onebot_submission(action, exc)
                    return True, "回执不确定，已停止立即重试"
                return False, self._format_send_exception(exc)
            if self._onebot_action_result_ok(result) or self._onebot_action_reported_success(action, result):
                return True, ""
            return False, f"{action} 返回失败: {_single_line(result, 180)}"
        return False, last_error or f"OneBot 客户端不支持动作 {action}"

    def _input_status_user_id_from_umo(self, umo: str) -> str:
        if not umo or ":FriendMessage:" not in str(umo):
            return ""
        platform_supports = getattr(self, "_platform_supports", None)
        if callable(platform_supports) and not platform_supports("input_status", umo=umo):
            return ""
        session = self._parse_message_session(umo)
        if not session:
            return ""
        user_id = str(getattr(session, "session_id", "") or "").strip()
        return user_id if user_id.isdigit() else ""

    def _prune_last_input_status_at(self, now: float) -> None:
        cache = getattr(self, "_last_input_status_at", None)
        if not isinstance(cache, dict) or not cache:
            return
        ttl = 24 * 3600
        stale = [k for k, v in cache.items() if now - _safe_float(v, 0) >= ttl]
        for k in stale:
            cache.pop(k, None)
        max_items = 512
        if len(cache) > max_items:
            for k in sorted(cache, key=cache.get)[: len(cache) - max_items]:
                cache.pop(k, None)

    async def _send_input_status_once(self, user_id: str, *, client: Any | None = None) -> bool:
        user_id = str(user_id or "").strip()
        if not user_id.isdigit():
            return False
        if client is None:
            client = self._resolve_aiocqhttp_client()
        if client is None:
            return False
        variants = (
            {"user_id": int(user_id), "event_type": 1},
            {"user_id": int(user_id), "status": 1},
            {"user_id": int(user_id), "typing": True},
        )
        for params in variants:
            if await self._call_onebot_action(client, "set_input_status", **params):
                self._prune_last_input_status_at(_now_ts())
                self._last_input_status_at[user_id] = _now_ts()
                return True
        return False

    async def _maybe_send_input_status(self, umo: str, text: str = "") -> None:
        user_id = self._input_status_user_id_from_umo(umo)
        if not user_id:
            return
        now = _now_ts()
        self._prune_last_input_status_at(now)
        last_at = _safe_float(self._last_input_status_at.get(user_id), 0)
        if now - last_at < 45:
            return
        duration = max(1.2, min(4.5, len(str(text or "")) / 18))
        if not await self._send_input_status_once(user_id):
            return
        self._last_input_status_at[user_id] = now
        await asyncio.sleep(random.uniform(duration * 0.55, duration))

    async def _passive_input_status_loop(self, user_id: str, *, max_seconds: float = 90.0) -> None:
        user_id = str(user_id or "").strip()
        if not user_id.isdigit():
            return
        client = self._resolve_aiocqhttp_client()
        if client is None:
            return
        started_at = _now_ts()
        while not bool(getattr(self, "_stop_event", asyncio.Event()).is_set()):
            if _now_ts() - started_at > max_seconds:
                return
            try:
                await self._send_input_status_once(user_id, client=client)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.debug("私聊输入状态刷新失败: %s", _single_line(exc, 120))
                return
            await asyncio.sleep(random.uniform(3.2, 4.8))

    def _start_passive_input_status_loop(self, event: AstrMessageEvent, user_id: str = "") -> None:
        umo = str(getattr(event, "unified_msg_origin", "") or "")
        parsed_user_id = self._input_status_user_id_from_umo(umo)
        # ``user_id`` is normally the platform-scoped profile storage key.  It
        # may contain a namespace/digest for a QQ-official event, while the
        # OneBot transport still requires the numeric sender from the UMO.
        storage_user_id = str(user_id or parsed_user_id or "").strip()
        if not parsed_user_id or not parsed_user_id.isdigit():
            return
        task_key = storage_user_id or parsed_user_id
        tasks = getattr(self, "_passive_input_status_tasks", None)
        if not isinstance(tasks, dict):
            tasks = {}
            self._passive_input_status_tasks = tasks
        old_task = tasks.get(task_key)
        if isinstance(old_task, asyncio.Task) and not old_task.done():
            old_task.cancel()
        task = asyncio.create_task(self._passive_input_status_loop(parsed_user_id))
        tasks[task_key] = task
        try:
            # Keep the scoped key on the event so stop/cleanup remains
            # isolated, but expose the numeric transport ID for diagnostics.
            setattr(event, "private_companion_input_status_user_id", task_key)
            setattr(event, "private_companion_input_status_transport_id", parsed_user_id)
        except Exception:
            pass

        def _cleanup(done_task: asyncio.Task) -> None:
            current = tasks.get(task_key)
            if current is done_task:
                tasks.pop(task_key, None)

        task.add_done_callback(_cleanup)

    def _stop_passive_input_status_loop(self, event_or_user: Any) -> None:
        user_id = ""
        if isinstance(event_or_user, str):
            user_id = event_or_user.strip()
        else:
            user_id = str(getattr(event_or_user, "private_companion_input_status_user_id", "") or "").strip()
            if not user_id:
                try:
                    user_id = str(event_or_user.get_sender_id()).strip()
                except Exception:
                    user_id = ""
        if not user_id:
            return
        tasks = getattr(self, "_passive_input_status_tasks", None)
        if not isinstance(tasks, dict):
            return
        task = tasks.pop(user_id, None)
        if isinstance(task, asyncio.Task) and not task.done():
            task.cancel()

    def _qq_presence_codes(self, mode: str) -> tuple[int, int, str]:
        normalized = str(mode or "").strip().lower()
        table = {
            "online": (10, 0, "在线"),
            "away": (30, 0, "离开"),
            "busy": (50, 0, "忙碌"),
            "invisible": (40, 0, "隐身"),
        }
        return table.get(normalized, table["online"])

    async def _set_qq_online_presence(self, mode: str) -> tuple[bool, str]:
        client = self._resolve_aiocqhttp_client()
        if client is None:
            return False, "未找到可用 QQ 客户端"
        status, ext_status, label = self._qq_presence_codes(mode)
        ok, error = await self._call_onebot_action_with_error(
            client,
            "set_online_status",
            at_most_once=True,
            status=status,
            ext_status=ext_status,
            battery_status=0,
        )
        if ok:
            return True, label
        return False, f"平台不支持 set_online_status：{label}（{_single_line(error, 100)}）"

    async def _set_qq_custom_presence(self, text: str) -> tuple[bool, str]:
        if not getattr(self, "enable_qq_custom_presence_sync", False):
            return False, "QQ 自定义短状态未开启"
        client = self._resolve_aiocqhttp_client()
        if client is None:
            return False, "未找到可用 QQ 客户端"
        custom_text = _single_line(text, 8)
        if not custom_text:
            return False, "自定义状态文本为空,跳过同步"
        # Avoid set_custom_online_status: some OneBot adapters disconnect on this unsupported extension API.
        ok, error = await self._call_onebot_action_with_error(
            client,
            "set_diy_online_status",
            at_most_once=True,
            face_id=21,
            face_type=1,
            wording=custom_text,
        )
        if ok:
            return True, f"自定义状态：{custom_text}"
        return False, f"平台不支持自定义状态：{custom_text}（{_single_line(error, 100)}）"

    async def _reset_stale_qq_presence_if_needed(self) -> None:
        if not self.enable_qq_presence_sync:
            return
        await asyncio.sleep(2)
        try:
            await self._ensure_current_detail_presence_status()
        except Exception as exc:
            logger.debug("启动同步当前 QQ 状态失败: %s", exc)
        async with self._data_lock:
            state = self.data.get("qq_presence_state", {})
            if not isinstance(state, dict) or str(state.get("date") or "") == _today_key():
                return
            previous_mode = str(state.get("mode") or "")
        ok, note = await self._set_qq_online_presence("online")
        async with self._data_lock:
            state = self.data.setdefault("qq_presence_state", {})
            if not isinstance(state, dict):
                state = {}
                self.data["qq_presence_state"] = state
            state.update(
                {
                    "date": _today_key(),
                    "plan_date": "",
                    "detail_key": "",
                    "mode": "online",
                    "custom_text": "",
                    "reason": "清理跨日 QQ 状态",
                    "updated_at": _now_ts(),
                    "ok": bool(ok),
                    "note": _single_line(f"跨日重置：{previous_mode or 'unknown'} -> {note}", 120),
                }
            )
            self._save_data_sync(sections={"qq_presence_state"})

