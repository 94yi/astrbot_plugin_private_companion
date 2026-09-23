# -*- coding: utf-8 -*-
"""private_passive_prompt。

由 tools/split_main_domain.py 从 main.py 机械抽取（33 个方法 / 1235 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPlugin）。
"""
from __future__ import annotations

import asyncio
import hashlib
import re
from .conversation_prompt_section import PromptRenderMode, PromptSection, prompt_section, render_prompt_sections
from .helpers import _now_ts, _safe_float, _safe_int, _single_line, _today_key
from .persona_config import runtime_persona_setting
from .prompt_surface import CollectedPromptContext, PromptSurface
from .segmented_message import flatten_component_chunks
from astrbot.api.event import AstrMessageEvent
from astrbot.api.provider import ProviderRequest
from typing import Any

from .logging_util import get_module_logger

logger = get_module_logger(__name__)

class PrivateCompanionPluginPrivatePassivePromptMixin:
    """private_passive_prompt（从 PrivateCompanionPlugin 拆出）。"""

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

