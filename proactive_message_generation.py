# -*- coding: utf-8 -*-
"""generation 域。

由 tools/split_mixin_domain.py 从 proactive_message.py 机械抽取（23 个方法 + 0 个模块级名字 + 0 个类级赋值 / 1304 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveMessageMixin）。
"""
from __future__ import annotations

import re
import time
from .conversation_prompt_section import (
    PromptDocument,
    PromptLabelStyle,
    PromptRenderMode,
    PromptSection,
    prompt_document,
    prompt_section,
    render_prompt_document,
    render_prompt_sections,
)
from .helpers import _safe_int, _single_line, _split_address_terms
from .persona_config import runtime_persona_setting
from .proactive_message_shared import _PROACTIVE_DOCUMENT_RENDER, _persona_provider_id, _proactive_prompt_part
from .segmented_message import LLM_SEGMENT_MARKER, split_llm_controlled_text
from typing import Any

from .logging_util import get_module_logger

logger = get_module_logger(__name__)



class ProactiveMessageGenerationMixin:
    """generation 域（从 ProactiveMessageMixin 拆出）。"""


    async def _generate_proactive_message_with_llm(
        self,
        user: dict[str, Any],
        name: str,
        reason: str,
        action_context: str = "",
        action: str = "message",
        motive: str = "",
    ) -> str:
        user.pop("_proactive_render_failure_stage", None)
        umo = _single_line(user.get("umo"), 240)
        self._clear_proactive_reaction_intent(umo)
        if not runtime_persona_setting(self, "enable_llm_proactive_message", True):
            user["_proactive_render_failure_stage"] = "主动消息模型生成已关闭"
            return ""
        raw_text = await self._generate_proactive_message_via_framework(
            user,
            name,
            reason,
            action_context=action_context,
            action=action,
            motive=motive,
        )
        deferred_photo_cache = getattr(self, "_framework_deferred_photo_cache", None)
        if isinstance(deferred_photo_cache, dict) and umo in deferred_photo_cache:
            logger.info(
                "主动正文已由 pc_generate_photo caption/纯图承载，跳过文本兜底: user=%s",
                _single_line(user.get("user_id"), 40),
            )
            return str(raw_text or "")
        async def finalize_candidate(candidate: str) -> tuple[str, str]:
            extractor = getattr(self, "_extract_reaction_expression_hidden_intent", None)
            visible_candidate, reaction_intent = (
                extractor(candidate)
                if callable(extractor)
                else (str(candidate or ""), {})
            )
            if isinstance(reaction_intent, dict) and reaction_intent:
                reaction_intent["_proactive_reason"] = reason
                reaction_intent["_proactive_action"] = action
            sticker_only = self._proactive_reaction_intent_allows_sticker_only(reaction_intent)
            if not reaction_intent:
                fallback_builder = getattr(
                    self,
                    "_proactive_reaction_expression_fallback_intent",
                    None,
                )
                if callable(fallback_builder):
                    try:
                        reaction_intent = fallback_builder(
                            visible_candidate,
                            action=action,
                        )
                    except Exception as exc:
                        logger.debug(
                            "高频主动表情兜底构建失败: error_type=%s",
                            type(exc).__name__,
                        )
            if sticker_only and not visible_candidate.strip():
                finalized, failure_stage = "", ""
            else:
                finalized, failure_stage = await self._finalize_proactive_generated_text(
                    user,
                    visible_candidate,
                    name=name,
                    reason=reason,
                    action=action,
                    action_context=action_context,
                    motive=motive,
                )
            if finalized or sticker_only:
                self._store_proactive_reaction_intent(
                    user,
                    reaction_intent if isinstance(reaction_intent, dict) else {},
                    action=action,
                )
            return finalized, failure_stage

        failure_stages: list[str] = []
        if raw_text:
            finalized, failure_stage = await finalize_candidate(raw_text)
            if finalized or self._proactive_sticker_only_pending(umo):
                return finalized
            failure_stages.append(f"框架主链{failure_stage or '处理后为空'}")
        else:
            failure_stages.append("框架主链返回空文本")

        fallback_text = await self._generate_proactive_message_direct_fallback(
            user,
            name=name,
            reason=reason,
            action=action,
            action_context=action_context,
            motive=motive,
        )
        if fallback_text:
            finalized, failure_stage = await finalize_candidate(fallback_text)
            if finalized or self._proactive_sticker_only_pending(umo):
                logger.info(
                    "主动框架主链为空后已由直接人格化兜底恢复: user=%s reason=%s",
                    _single_line(user.get("user_id"), 40),
                    reason,
                )
                return finalized
            failure_stages.append(f"直接人格化兜底{failure_stage or '处理后为空'}")
        else:
            failure_stages.append("直接人格化兜底返回空文本")

        failure_detail = "；".join(failure_stages)[:240]
        user["_proactive_render_failure_stage"] = failure_detail
        logger.warning(
            "主动正文两级生成均未产出: user=%s reason=%s stage=%s",
            _single_line(user.get("user_id"), 40),
            reason,
            failure_detail,
        )
        return ""

    async def _generate_proactive_message_direct_fallback(
        self,
        user: dict[str, Any],
        *,
        name: str,
        reason: str,
        action: str,
        action_context: str = "",
        motive: str = "",
    ) -> str:
        relationship_sanitizer = getattr(self, "_sanitize_generation_relationship_context", None)

        def sanitize_relationship_source(value: Any, source: str) -> str:
            if callable(relationship_sanitizer):
                try:
                    return relationship_sanitizer(value, source=source)
                except Exception:
                    pass
            return str(value or "").strip()

        topic = _single_line(
            sanitize_relationship_source(user.get("planned_proactive_topic"), "proactive_fallback.topic"),
            120,
        )
        planned_motive = _single_line(
            sanitize_relationship_source(
                motive or user.get("planned_proactive_motive"),
                "proactive_fallback.motive",
            ),
            220,
        )
        context = sanitize_relationship_source(
            self._format_action_prompt_context(action, action_context),
            "proactive_fallback.action_context",
        )
        if (
            (context.startswith("message：") and "图片动作本轮未产出" not in context)
            or context in {"普通文字", "普通私聊文本"}
        ):
            context = ""
        reference = "\n".join(
            part
            for part in (
                f"主动话题：{topic}" if topic else "",
                f"想表达：{planned_motive}" if planned_motive else "",
                f"真实动作上下文：{context}" if context else "",
            )
            if part
        )
        body_health_hint_getter = getattr(self, "_format_body_monitor_health_prompt", None)
        if reason == "health_alert" and callable(body_health_hint_getter):
            body_health_hint = body_health_hint_getter(user, reason=reason)
            if body_health_hint:
                reference = f"{reference}\n{body_health_hint}" if reference else body_health_hint
        balance_hint_getter = getattr(self, "_format_balance_awareness_prompt", None)
        if reason == "low_balance" and callable(balance_hint_getter):
            balance_hint = balance_hint_getter(user, reason=reason)
            if balance_hint:
                reference = f"{reference}\n{balance_hint}" if reference else balance_hint
        environment_hint_getter = getattr(self, "_format_environment_change_prompt", None)
        if reason == "environment_change" and callable(environment_hint_getter):
            environment_hint = environment_hint_getter(user, reason=reason)
            if environment_hint:
                reference = f"{reference}\n{environment_hint}" if reference else environment_hint
        weather_alert_hint_getter = getattr(self, "_format_weather_alert_prompt", None)
        if reason == "weather_alert" and callable(weather_alert_hint_getter):
            weather_alert_hint = weather_alert_hint_getter(user, reason=reason)
            if weather_alert_hint:
                reference = f"{reference}\n{weather_alert_hint}" if reference else weather_alert_hint
        personal_goal_hint_getter = getattr(self, "_format_personal_goal_prompt", None)
        if reason == "personal_goal_progress" and callable(personal_goal_hint_getter):
            personal_goal_hint = personal_goal_hint_getter(user, reason=reason)
            if personal_goal_hint:
                reference = f"{reference}\n{personal_goal_hint}" if reference else personal_goal_hint
        memo_hint_getter = getattr(self, "_format_memo_note_prompt", None)
        if reason == "memo_note_reminder" and callable(memo_hint_getter):
            memo_hint = memo_hint_getter(user, reason=reason)
            if memo_hint:
                reference = f"{reference}\n{memo_hint}" if reference else memo_hint
        if reason == "goodnight_screen_check":
            reference = (
                f"互道晚安后，如果{name or '对方'}还没睡，就轻声提醒忙完早点休息；"
                "不提看见了什么，不追问，不要求回复，也不表现成在监控。"
            )
        elif reason == "anonymous_area_dwell":
            reference = (
                f"{reference}\n" if reference else ""
            ) + (
                "这是用户离开一个未命名区域后的延迟关心。不要提位置、地图、城市、城区、定位或停留时长；"
                "只写成后来想起用户刚才在外面待了挺久，轻轻关心是否顺利，不追问具体去了哪里。"
            )
        elif reason == "anonymous_area_familiarity":
            reference = (
                f"{reference}\n" if reference else ""
            ) + (
                "这是多次匿名区域到访留下的模糊熟悉感。不要提位置来源、地图、次数或具体地点；"
                "可以说‘最近好像有个常去的地方’，但必须给用户留出否认或不解释的空间。"
            )
        relationship_initiative_hint = self._format_proactive_relationship_initiative_hint(
            user,
            reason=reason,
            action=action,
        )
        if relationship_initiative_hint:
            reference = f"{reference}\n{relationship_initiative_hint}" if reference else relationship_initiative_hint
        if not reference:
            reference = f"自然地向{name or '对方'}主动说一句与当前状态有关、低压力且无需立即回复的话。"
        reference = sanitize_relationship_source(reference, "proactive_fallback.reference")
        if not reference:
            reference = f"自然地向{name or '对方'}主动说一句低压力且无需立即回复的话。"
        fallback_scene = f"主动开口；原因={reason or 'check_in'}；动作={action or 'message'}"
        if reason == "creative_share":
            fallback_scene = "主动分享自己的创作；作品原文与聊天引入必须保持清晰边界"
        return await self._rewrite_reference_reply_with_persona(
            reference,
            scene=fallback_scene,
            user=user,
            fallback_text="",
            task="proactive_message_fallback",
            max_chars=180,
            allow_fallback=False,
        )

    async def _finalize_proactive_generated_text(
        self,
        user: dict[str, Any],
        raw_text: str,
        *,
        name: str,
        reason: str,
        action: str,
        action_context: str = "",
        motive: str = "",
    ) -> tuple[str, str]:
        controlled_segments, controlled = (
            split_llm_controlled_text(raw_text)
            if self._proactive_llm_segmenting_allowed(
                umo=_single_line(user.get("umo"), 240),
            )
            else ([str(raw_text or "").strip()], False)
        )
        if controlled:
            finalized_segments: list[str] = []
            failure_stages: list[str] = []
            for segment in controlled_segments:
                finalized_segment, failure_stage = await self._finalize_proactive_generated_text(
                    user,
                    segment,
                    name=name,
                    reason=reason,
                    action=action,
                    action_context=action_context,
                    motive=motive,
                )
                if finalized_segment:
                    finalized_segments.append(finalized_segment)
                elif failure_stage:
                    failure_stages.append(failure_stage)
            if not finalized_segments:
                return "", "；".join(failure_stages)[:240] or "自主分段正文处理后为空"

            # Preserve the previous proactive visible-text ceiling. The marker
            # itself is transport metadata and does not consume that budget.
            remaining = 260
            bounded_segments: list[str] = []
            for segment in finalized_segments:
                if remaining <= 0:
                    break
                if len(segment) <= remaining:
                    bounded_segments.append(segment)
                    remaining -= len(segment)
                    continue
                truncated = self._truncate_proactive_text(segment, remaining)
                if truncated:
                    bounded_segments.append(truncated)
                break
            return (
                f"\n{LLM_SEGMENT_MARKER}\n".join(bounded_segments),
                "",
            ) if bounded_segments else ("", "自主分段正文处理后为空")
        if self._looks_like_internal_provider_error_text(raw_text):
            logger.warning(
                "主动正文生成收到 Provider 错误正文，跳过清洗并进入回退: user=%s reason=%s",
                _single_line(user.get("user_id"), 40),
                _single_line(reason, 60) or "check_in",
            )
            return "", "Provider/API 错误正文"
        cleaned = self._sanitize_action_boundaries(
            self._sanitize_proactive_text(raw_text),
            reason=reason,
            action=action,
            action_context=action_context,
            has_real_image="真实图片文件：" in action_context or "图片路径：" in action_context,
        )
        if not cleaned:
            return "", "在动作边界清洗后为空"
        cleaned, repaired_address = self._repair_proactive_recipient_address(cleaned, user, name)
        if repaired_address:
            logger.warning(
                "主动消息已纠正串用户句首称呼: user=%s wrong=%s replacement=%s",
                _single_line(user.get("user_id"), 40),
                repaired_address,
                _single_line(name or user.get("nickname"), 40) or "你",
            )
        remaining_wrong_address = self._wrong_proactive_recipient_address(cleaned, user, name)
        if remaining_wrong_address:
            return "", f"含其他用户专属称呼：{remaining_wrong_address}"
        if self._is_overabstract_proactive_text(cleaned, action=action):
            cleaned = self._ground_proactive_text(
                cleaned,
                reason=reason,
                action=action,
                action_context=action_context,
            )
        cleaned = self._apply_proactive_style_variation(cleaned, user)
        cleaned = self._collapse_multi_candidate_proactive_text(cleaned, user=user, name=name)
        cleaned = self._repair_proactive_subject_drift(cleaned, reason=reason, action=action, action_context=action_context)
        if reason == "morning_greeting":
            cleaned = self._strip_morning_meal_questions(cleaned)
        cleaned = self._visible_text_without_tts_reading(cleaned, limit=1000)
        if not cleaned:
            return "", "在主客体/可见文本清洗后为空"
        relay_claim_note = self._unexecuted_relay_claim_reason(cleaned, action_context=action_context)
        if relay_claim_note:
            logger.info(
                "主动消息含未执行转述承诺,已丢弃: reason=%s text=%s",
                relay_claim_note,
                _single_line(cleaned, 120),
            )
            return "", f"含未执行转述承诺：{_single_line(relay_claim_note, 80)}"
        if self._should_drop_vague_generic_proactive(
            user,
            reason=reason,
            action=action,
            action_context=action_context,
            text=cleaned,
        ):
            # 连续未回应时的泛泛措辞是表达质量问题，不是安全问题。
            # 交给主动生成提示词收短、降压，避免在终审关闭时被本地规则直接吞掉。
            logger.debug(
                "泛化主动由提示词收敛，不再直接拦截: user=%s text=%s",
                _single_line(user.get("user_id") or user.get("umo"), 80),
                _single_line(cleaned, 140),
            )
        if self._should_drop_misstaged_proactive_text(cleaned, reason=reason, action=action):
            return "", "错接旧对话或时段"
        reviewed = await self._review_proactive_message_stance(
            user,
            cleaned,
            reason=reason,
            action=action,
            action_context=action_context,
            motive=motive,
        )
        if not reviewed:
            return "", "回复空气复核后为空"
        reviewed, repaired_review_address = self._repair_proactive_recipient_address(reviewed, user, name)
        if repaired_review_address:
            logger.warning(
                "主动复核结果已纠正串用户称呼: user=%s wrong=%s",
                _single_line(user.get("user_id"), 40),
                repaired_review_address,
            )
        remaining_review_address = self._wrong_proactive_recipient_address(reviewed, user, name)
        if remaining_review_address:
            return "", f"回复空气复核引入其他用户专属称呼：{remaining_review_address}"
        reviewed = self._trim_proactive_status_inventory(reviewed)
        reviewed = self._trim_performative_self_state_tail(reviewed)
        if reason == "morning_greeting":
            reviewed = self._strip_morning_meal_questions(reviewed)
        finalized = self._normalize_proactive_sentence_flow(reviewed)
        return (finalized, "") if finalized else ("", "最终句式整理后为空")

    @staticmethod
    def _strip_morning_meal_questions(text: str) -> str:
        """Keep a morning greeting while removing an accidentally appended meal question."""
        source = str(text or "").strip()
        if not source:
            return ""
        query_pattern = re.compile(
            r"(?:早餐|早饭).{0,12}(?:吗|没|没有|什么|啥|呢|[？?])"
            r"|(?:吃|喝).{0,6}(?:了吗|了没|没有|什么|啥)(?:呢|[？?])?"
        )
        kept: list[str] = []
        for unit in re.split(r"(?<=[。！？!?])\s*|\n+", source):
            candidate = unit.strip()
            if not candidate:
                continue
            match = query_pattern.search(candidate)
            if not match:
                kept.append(candidate)
                continue
            prefix = candidate[: match.start()].rstrip(" ，,；;、")
            if prefix:
                kept.append(prefix)
        return "\n".join(kept).strip()

    def _proactive_reply_air_flags(
        self,
        text: str,
        *,
        reason: str,
        action: str,
        action_context: str = "",
    ) -> list[str]:
        cleaned = _single_line(text, 260)
        if not cleaned or action not in {"message", "photo_text"}:
            return []
        flags: list[str] = []
        # 外部分享（新闻/B站/搜索）是「分享外界信息」场景，不做回复空气检查，直接放行。
        if reason in {"news_share", "bili_video_share", "web_exploration_share"}:
            return flags
        reply_opener_pattern = (
            r"^(?:好呀|好啊|可以呀|可以啊|行呀|行啊|嗯好|那就|你说呢|要不|不然|"
            r"确实|对呀|对啊|是吧|也是|哈哈[,，\s]*我也|我也觉得|你说得对)"
        )
        if re.search(reply_opener_pattern, cleaned):
            flags.append("reply_air_opener")
        if re.search(r"(?:刚看到|才看到|刚才看到|看到你(?:刚刚|刚才)?发|看到你说)", cleaned):
            flags.append("pretends_recent_inbound")
        if re.search(r"你(?:刚刚|刚才|现在)?(?:叫|喊|问|说|发|来找|找|催)我", cleaned):
            flags.append("inverts_initiator")
        if re.search(r"(?:你问|你说|你刚才说|你刚刚说)[^。！？\n]{0,24}(?:我觉得|我也|确实|可以|好呀|好啊)", cleaned):
            flags.append("answers_old_context")
        if self._is_proactive_delivery_receipt_text(cleaned):
            flags.append("delivery_receipt")
        if reason in {"morning_greeting", "noon_greeting", "evening_greeting", "check_in"} and re.search(
            r"(?:一直等着|等你问|你到时候|到时候叫|到时候喊|那就这么说定|按你说的)",
            cleaned,
        ):
            flags.append("stale_agreement")
        if "真实图片文件：" not in str(action_context or "") and "图片路径：" not in str(action_context or ""):
            if re.search(r"(?:发你看|给你看图|看图|图里|照片里|图片里)", cleaned):
                flags.append("claims_missing_media")
        return list(dict.fromkeys(flags))

    def _repair_proactive_reply_air_locally(self, text: str, flags: list[str]) -> str:
        cleaned = str(text or "").strip()
        if not cleaned:
            return ""
        units = self._split_proactive_sentence_units(cleaned) or [cleaned]
        repaired: list[str] = []
        opener_pattern = (
            r"^(?:好呀|好啊|可以呀|可以啊|行呀|行啊|嗯好|那就|你说呢|要不|不然|"
            r"确实|对呀|对啊|是吧|也是|哈哈[,，\s]*我也|我也觉得|你说得对)"
            r"[，,、。！？!?；;:\s]*"
        )
        stale_patterns = (
            r"(?:刚看到|才看到|刚才看到|看到你(?:刚刚|刚才)?发|看到你说)",
            r"你(?:刚刚|刚才|现在)?(?:叫|喊|问|说|发|来找|找|催)我",
            r"(?:你问|你说|你刚才说|你刚刚说)[^。！？\n]{0,24}(?:我觉得|我也|确实|可以|好呀|好啊)",
        )
        for unit in units:
            candidate = str(unit or "").strip()
            if not candidate:
                continue
            if "reply_air_opener" in flags:
                candidate = re.sub(opener_pattern, "", candidate, count=1).strip()
            if any(re.search(pattern, candidate) for pattern in stale_patterns):
                continue
            if candidate:
                repaired.append(self._ensure_chat_sentence_punctuation(candidate))
        return "\n".join(repaired).strip()

    @staticmethod
    def _response_review_prompt_document(
        *,
        original_text: str,
        flags: str,
        reason: str,
        motive: str,
        topic: str,
        action_context: str,
        intent_hint: str,
        persona: str,
        proactive_voice: str,
        expression_voice: str,
        recipient_identity: str,
        creative_excerpt_rule: str,
    ) -> PromptDocument:
        return prompt_document(
            user_render=_PROACTIVE_DOCUMENT_RENDER,
            user=(
                _proactive_prompt_part(prompt_section(
                    key="background.response_review.task",
                    title="主动回复空气修正",
                    source="proactive_message",
                    content=(
                        "把下面这条主动私聊消息改成真正的主动开口。\n"
                        "它不是在回复用户刚发来的消息；聊天历史只能当背景。"
                    ),
                ), mode=PromptRenderMode.BODY_ONLY),
                prompt_section(
                    key="background.response_review.original",
                    title="原主动消息",
                    source="proactive_message",
                    content=original_text,
                ),
                prompt_section(
                    key="background.response_review.flags",
                    title="问题",
                    source="proactive_message",
                    content=flags,
                ),
                prompt_section(
                    key="background.response_review.reason",
                    title="主动原因",
                    source="proactive_message",
                    content=reason or "check_in",
                ),
                prompt_section(
                    key="background.response_review.motive_topic",
                    title="动机/话题",
                    source="proactive_message",
                    content=f"{motive}\n{topic}",
                ),
                _proactive_prompt_part(
                    prompt_section(
                        key="background.response_review.action_context",
                        title="动作上下文",
                        source="proactive_message",
                        content=action_context or "（无）",
                    ),
                    separator_before="\n\n\n" if not topic else "\n\n",
                ),
                prompt_section(
                    key="background.response_review.intent",
                    title="内在约束",
                    source="proactive_message",
                    content=intent_hint or "（无额外约束）",
                ),
                prompt_section(
                    key="background.response_review.persona",
                    title="完整人格",
                    source="proactive_message",
                    content=(
                        persona
                        or "（没有解析到显式人格；尽量保留原文语气，不要另造一种通用陪伴人格）"
                    ),
                ),
                prompt_section(
                    key="background.response_review.voice",
                    title="主动开口风格",
                    source="proactive_message",
                    content=proactive_voice or "（无额外主动风格；保持原文已有的人格语气）",
                ),
                prompt_section(
                    key="background.response_review.expression",
                    title="已形成的表达底色",
                    source="proactive_message",
                    content=expression_voice or "（无额外表达底色）",
                ),
                prompt_section(
                    key="background.response_review.recipient",
                    title="当前收件人",
                    source="proactive_message",
                    content=recipient_identity or "不要猜名字或套用其他对象的专属称呼。",
                ),
                _proactive_prompt_part(prompt_section(
                    key="background.response_review.rules",
                    title="要求",
                    source="proactive_message",
                    content=(
                        "- 只输出要发送的正文\n"
                        "- 不要把“用户”“对方”“收信人”这类内部称呼写进正文；需要称呼时用自然的“你”或对方昵称\n"
                        "- 不要写成“好呀/确实/我也觉得/刚看到/你刚刚问我/你来找我了”\n"
                        "- 不要把历史消息当成当前正在发生的对话\n"
                        "- 没有真实图片或工具结果时，只写聊天内容本身，不描述动作结果\n"
                        "- 如果原文只是过程状态或工具结果，请不要改写成另一种状态汇报；改不成自然聊天就输出空文本\n"
                        "- 如果原文或模型结果包含 Provider/API 报错、内容策略拒绝、敏感词提示、政策链接或内部诊断，输出空文本；不要翻译、复述或润色\n"
                        "- 改写后仍要贴合内在约束里的候选语义；不能把分享型改成泛泛问候，也不能把低压关心改成追问\n"
                        "- 只修正“回复空气”的问题；不得把原文改成另一种人格，也不得降低或升级当前关系亲密度\n"
                        "- 尽量 1 到 2 句，像自然想起对方后随手说一句\n"
                        f"{creative_excerpt_rule}"
                    ),
                ), label_style=PromptLabelStyle.FULLWIDTH_COLON),
            ),
            metadata={"task": "response_review"},
        )

    async def _review_proactive_message_stance(
        self,
        user: dict[str, Any],
        text: str,
        *,
        reason: str,
        action: str,
        action_context: str = "",
        motive: str = "",
    ) -> str:
        cleaned = str(text or "").strip()
        if not cleaned:
            return ""
        relationship_sanitizer = getattr(self, "_sanitize_generation_relationship_context", None)

        def sanitize_relationship_source(value: Any, source: str) -> str:
            if callable(relationship_sanitizer):
                try:
                    return relationship_sanitizer(value, source=source)
                except Exception:
                    pass
            return str(value or "").strip()

        flags = self._proactive_reply_air_flags(
            cleaned,
            reason=reason,
            action=action,
            action_context=action_context,
        )
        if not flags:
            return cleaned
        mode = self._effective_proactive_review_mode()
        review_disabled = not bool(
            runtime_persona_setting(self, "enable_proactive_message_review", True)
        )
        if review_disabled or mode == "local_only":
            hard_flags = {"delivery_receipt", "claims_missing_media"}
            if hard_flags.intersection(flags):
                # 只移除命中硬风险的句子；同一候选里若还有安全正文，继续交给
                # 轻量主动修正，避免一条附带回执的多句消息被整条吞掉。
                safe_units: list[str] = []
                for unit in self._split_proactive_sentence_units(cleaned) or [cleaned]:
                    unit_flags = self._proactive_reply_air_flags(
                        unit,
                        reason=reason,
                        action=action,
                        action_context=action_context,
                    )
                    if hard_flags.intersection(unit_flags):
                        continue
                    safe_units.append(unit)
                cleaned = "\n".join(safe_units).strip()
                if not cleaned:
                    logger.info(
                        "主动消息仅剩不可用动作/内部回执,本地安全检查已丢弃: flags=%s",
                        ",".join(flags),
                    )
                    return ""
                flags = self._proactive_reply_air_flags(
                    cleaned,
                    reason=reason,
                    action=action,
                    action_context=action_context,
                )
            repaired = self._repair_proactive_reply_air_locally(cleaned, flags)
            remaining_flags = self._proactive_reply_air_flags(
                repaired,
                reason=reason,
                action=action,
                action_context=action_context,
            ) if repaired else flags
            if repaired and not remaining_flags:
                logger.info(
                    "主动消息疑似回复空气,已用本地轻量规则修正: flags=%s before=%s after=%s",
                    ",".join(flags),
                    _single_line(cleaned, 100),
                    _single_line(repaired, 100),
                )
                return repaired
            logger.warning(
                "主动消息疑似回复空气但终审未启用,本地无法可靠改写，保留原文并交由生成提示词约束: flags=%s text=%s",
                ",".join(flags),
                _single_line(cleaned, 120),
            )
            return cleaned
        intent_hint = self._format_proactive_generation_intent_hint(
            user,
            reason=reason,
            action=action,
            motive=motive,
            action_context=action_context,
        )
        intent_hint = sanitize_relationship_source(intent_hint, "proactive_review.intent")
        review_motive = _single_line(
            sanitize_relationship_source(
                motive or user.get("planned_proactive_motive"),
                "proactive_review.motive",
            ),
            160,
        )
        review_topic = _single_line(
            sanitize_relationship_source(
                user.get("planned_proactive_topic"),
                "proactive_review.topic",
            ),
            120,
        )
        review_action_context = _single_line(
            sanitize_relationship_source(action_context, "proactive_review.action_context"),
            260,
        )
        persona = await self._resolve_proactive_persona_prompt(user)
        proactive_voice_sections_getter = getattr(self, "_format_proactive_voice_prompt_sections", None)
        proactive_voice = (
            render_prompt_sections(
                proactive_voice_sections_getter(),
                mode=PromptRenderMode.LABELED_BLOCK,
            )
            if callable(proactive_voice_sections_getter)
            else ""
        )
        if not callable(proactive_voice_sections_getter):
            proactive_voice_getter = getattr(self, "_format_proactive_voice_prompt", None)
            proactive_voice = proactive_voice_getter() if callable(proactive_voice_getter) else ""
        expression_section_getter = getattr(self, "_format_expression_voice_prompt_section", None)
        expression_section = (
            expression_section_getter(
                scope="proactive",
                target_id=_single_line(user.get("user_id") or user.get("id"), 80),
                context_owner=user,
                stage_owner=user,
            )
            if callable(expression_section_getter)
            else None
        )
        expression_voice = (
            render_prompt_sections([expression_section], mode=PromptRenderMode.LABELED_BLOCK)
            if isinstance(expression_section, PromptSection)
            else ""
        )
        if not callable(expression_section_getter):
            expression_formatter = getattr(self, "_format_expression_voice_for_prompt", None)
            expression_voice = (
                expression_formatter(
                    scope="proactive",
                    target_id=_single_line(user.get("user_id") or user.get("id"), 80),
                    context_owner=user,
                    stage_owner=user,
                )
                if callable(expression_formatter)
                else ""
            )
        recipient_identity = self._proactive_recipient_identity_prompt_text(
            user,
            _single_line(user.get("nickname"), 40),
        )
        creative_excerpt_rule = (
            self._creative_share_excerpt_prompt_hint()
            if reason == "creative_share"
            else ""
        )
        prompt = render_prompt_document(
            self._response_review_prompt_document(
                original_text=cleaned,
                flags=", ".join(flags),
                reason=reason,
                motive=review_motive,
                topic=review_topic,
                action_context=review_action_context,
                intent_hint=intent_hint,
                persona=(self._truncate_proactive_context(persona, 2600) if persona else ""),
                proactive_voice=proactive_voice,
                expression_voice=expression_voice,
                recipient_identity=recipient_identity,
                creative_excerpt_rule=creative_excerpt_rule,
            )
        )["user"]
        started = time.perf_counter()
        rewritten = await self._llm_call(
            prompt,
            max_tokens=180,
            provider_id=self._task_provider(
                _persona_provider_id(self, "RESPONSE_REVIEW_PROVIDER_ID", "response_review_provider_id", "fast"),
                _persona_provider_id(self, "MAI_STYLE_PROVIDER_ID", "mai_style_provider_id", "fast"),
            ),
            task="response_review",
        )
        candidate = self._sanitize_proactive_text(str(rewritten or "").strip())
        candidate = self._sanitize_action_boundaries(
            candidate,
            reason=reason,
            action=action,
            action_context=action_context,
            has_real_image="真实图片文件：" in action_context or "图片路径：" in action_context,
        )
        if self._looks_like_internal_provider_error_text(candidate):
            logger.warning(
                "回复/主动复核返回 Provider 错误正文，已丢弃: task=response_review"
            )
            return ""
        meta_leak_checker = getattr(self, "_response_review_meta_leak_reason", None)
        if callable(meta_leak_checker) and meta_leak_checker(candidate):
            logger.error(
                "回复/主动复核返回内部判断，已丢弃: output=%s",
                _single_line(candidate, 180),
            )
            return ""
        logger.info(
            "回复/主动复核完成: mode=%s flags=%s elapsed=%dms before=%s after=%s",
            mode,
            ",".join(flags),
            int((time.perf_counter() - started) * 1000),
            _single_line(cleaned, 100),
            _single_line(candidate, 100),
        )
        if not candidate:
            return ""
        if len(candidate) > max(len(cleaned) + 80, 260):
            return ""
        if re.search(r"(提示词|系统|JSON|改写后|以下是|主动消息|聊天历史)", candidate, re.IGNORECASE):
            return ""
        remaining_flags = self._proactive_reply_air_flags(
            candidate,
            reason=reason,
            action=action,
            action_context=action_context,
        )
        if remaining_flags:
            logger.info(
                "回复/主动复核后仍疑似回复空气,已丢弃: flags=%s text=%s",
                ",".join(remaining_flags),
                _single_line(candidate, 120),
            )
            return ""
        return candidate

    def _repair_proactive_subject_drift(
        self,
        text: str,
        *,
        reason: str,
        action: str,
        action_context: str = "",
    ) -> str:
        cleaned = str(text or "").strip()
        if not cleaned or action != "message":
            return cleaned
        state_context = "\n".join(
            _single_line(part, 260)
            for part in (
                action_context,
                self._format_schedule_context_for_prompt(),
                self._format_plan_item_for_prompt(self._proactive_current_plan_item(self.data.get("daily_plan", {}))),
            )
            if _single_line(part, 260)
        )
        bot_task_markers = (
            "作业", "写题", "题", "上课", "放学", "课本", "书桌", "试卷", "复习", "预习",
            "任务", "代码", "创作", "草稿", "报告", "练习",
        )
        if not any(token in state_context for token in bot_task_markers):
            return cleaned
        user_progress_patterns = (
            r"你[^。！？\n]{0,12}(?:作业|题|试卷|课|任务|代码|报告|草稿|练习)[^。！？\n]{0,18}(?:还差多少|写完了吗|做完了吗|弄完了吗|忙完了吗|上完了吗|差多少|完成了吗|怎么样了)[呀啊嘛呢了]*[？?。!！]?",
            r"(?:作业|题|试卷|课|任务|代码|报告|草稿|练习)[^。！？\n]{0,12}(?:还差多少|写完了吗|做完了吗|弄完了吗|忙完了吗|上完了吗|差多少|完成了吗)[呀啊嘛呢了]*[？?。!！]?",
        )
        repaired = cleaned
        changed = False
        for pattern in user_progress_patterns:
            repaired, count = re.subn(pattern, "", repaired)
            changed = changed or count > 0
        if not changed:
            return cleaned
        repaired = re.sub(r"\s+", " ", repaired).strip(" ，,。！？!?、")
        if repaired:
            logger.info(
                "主动消息修正主客体错位问句: reason=%s before=%s after=%s",
                reason,
                _single_line(cleaned, 120),
                _single_line(repaired, 120),
            )
            return repaired
        logger.info(
            "主动消息主客体错位且无剩余自然内容,已丢弃本轮生成: reason=%s text=%s",
            reason,
            _single_line(cleaned, 120),
        )
        return ""

    def _should_drop_misstaged_proactive_text(self, text: str, *, reason: str, action: str) -> bool:
        cleaned = _single_line(text, 220)
        if not cleaned:
            return True
        if action != "message" or reason not in {"morning_greeting", "noon_greeting", "evening_greeting", "check_in"}:
            return False
        reply_openers = ("好呀", "好啊", "可以呀", "可以啊", "行呀", "行啊", "嗯好", "那就", "你说呢", "要不", "不然")
        old_invite_markers = (
            "下午陪你", "陪你出去", "出去走走", "五点", "放学之后", "下班之后",
            "到时候叫我", "到时候喊我", "到时候", "垫上", "我哪来的钱",
            "一直等着", "等着呢", "想去哪", "去哪儿", "去哪逛", "哪儿逛", "哪里逛", "去逛",
        )
        if reason in {"morning_greeting", "noon_greeting", "evening_greeting"} and cleaned.startswith(reply_openers) and any(token in cleaned for token in old_invite_markers):
            logger.info(
                "主动消息疑似把旧邀约当成当前回复,已丢弃: reason=%s text=%s",
                reason,
                cleaned,
            )
            return True
        if reason in {"morning_greeting", "noon_greeting", "evening_greeting"}:
            stale_reply_patterns = (
                r"^(?:好呀|好啊|可以呀|可以啊|行呀|行啊|嗯好|那就).{0,30}(?:你到时候|到时候你|到时候叫|到时候喊)",
                r"^(?:好呀|好啊|可以呀|可以啊|行呀|行啊|嗯好|那就).{0,30}(?:我得|我得等|我只能|我可以).{0,18}(?:之后|以后|才行)",
                r"^(?:你说呢|要不|不然).{0,30}(?:我哪来|哪来的钱|先帮我|帮我垫|垫上)",
                r"^(?:好呀|好啊|可以呀|可以啊|行呀|行啊|嗯好|那就|你说呢|要不|不然).{0,36}(?:下午|五点|放学|下班|垫上|哪来的钱)",
                r"^(?:好呀|好啊|可以呀|可以啊|行呀|行啊|嗯好|那就).{0,30}(?:一直等|等着呢|等你).{0,30}(?:去哪|哪儿|哪里|逛|走走)",
                r"^(?:好呀|好啊|可以呀|可以啊|行呀|行啊|嗯好|那就).{0,36}(?:想去哪|去哪儿|去哪逛|哪儿逛|哪里逛|去逛)",
            )
            if any(re.search(pattern, cleaned) for pattern in stale_reply_patterns):
                logger.info(
                    "主动消息疑似接续旧对话而非主动开口,已丢弃: reason=%s text=%s",
                    reason,
                    cleaned,
                )
                return True
        return False

    def _proactive_time_mismatch_reason(self, text: str, *, reason: str, action: str) -> str:
        if str(action or "message").strip() != "message":
            return ""
        cleaned = _single_line(text, 240)
        if not cleaned:
            return ""
        now = self._environment_now()
        minutes = now.hour * 60 + now.minute
        current_item = self._proactive_current_plan_item(self.data.get("daily_plan", {}))
        current_text = _single_line(self._format_plan_item_for_prompt(current_item), 180)
        current_is_school_or_afternoon = bool(re.search(r"(上课|课间|放学|校门|教室|作业|书包|回家路上)", current_text))
        if reason == "morning_greeting" and re.search(r"(晚上|晚安|睡觉|好梦|睡前|夜里|放学|下班)", cleaned):
            return f"早间主动含有非早间场景: {cleaned}"
        if reason == "noon_greeting" and re.search(r"(早安|刚醒|赖床|晚安|好梦|睡觉|夜里)", cleaned):
            return f"午间主动含有错时问候: {cleaned}"
        if reason == "evening_greeting" and re.search(r"(早安|刚醒|赖床|上午|中午吃了吗)", cleaned):
            return f"晚间主动含有错时问候: {cleaned}"
        if minutes < 12 * 60 and re.search(r"(放学|放学就|放学后|放学回来|下课回来|下午回来|傍晚回来|晚上回来)", cleaned):
            return f"上午主动提前叙述放学/傍晚场景: {cleaned}"
        if minutes < 15 * 60 and re.search(r"(五点|5点|17点|下午五点|傍晚|晚上见|晚点回来找你)", cleaned):
            return f"当前时段过早,主动含有傍晚/五点场景: {cleaned}"
        if minutes >= 22 * 60 and re.search(r"(放学|下课|下午|傍晚|出去走走|等我回来找你)", cleaned):
            return f"夜间主动含有已过时段场景: {cleaned}"
        if re.search(r"(放学|下课|校门|教室|书包|回家路上)", cleaned) and not current_is_school_or_afternoon and not (14 * 60 <= minutes <= 19 * 60):
            return f"主动文本与当前日程不匹配: 当前={current_text or '无'} 文本={cleaned}"
        return ""

    def _collapse_multi_candidate_proactive_text(self, text: str, *, user: dict[str, Any], name: str = "") -> str:
        cleaned = str(text or "").strip()
        if not cleaned:
            return ""
        lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
        if len(lines) >= 2:
            collapsed_lines = self._collapse_near_duplicate_proactive_lines(lines)
            if len(collapsed_lines) < len(lines):
                result = "\n".join(collapsed_lines).strip()
                logger.info(
                    "主动消息已合并同轮近似候选: before=%s after=%s",
                    _single_line(cleaned, 180),
                    _single_line(result, 160),
                )
                return result or cleaned
        units: list[str] = []
        for line in lines or [cleaned]:
            units.extend(self._split_proactive_sentence_units(line))
        units = [unit.strip() for unit in units if unit and unit.strip()]
        if len(units) <= 2:
            return cleaned

        opener_tokens: list[str] = []
        for value in (
            name,
            user.get("nickname") if isinstance(user, dict) else "",
            runtime_persona_setting(self, "default_nickname", ""),
        ):
            for token in _split_address_terms(value, 8):
                opener_tokens.append(_single_line(token, 16))
        first_opener = ""
        match = re.match(r"^([\w\u4e00-\u9fffぁ-んァ-ヶー]{1,8})[，,、\s]", units[0])
        if match:
            first_opener = match.group(1)
            opener_tokens.append(first_opener)
        opener_tokens = [token for token in dict.fromkeys(opener_tokens) if token]

        repeated_opener_index = 0
        for index, unit in enumerate(units[1:], start=1):
            if any(unit.startswith(token) and index >= 2 for token in opener_tokens):
                repeated_opener_index = index
                break
        if repeated_opener_index:
            units = units[:repeated_opener_index]

        if self._private_user_role(user) == "friend" and len(units) > 2:
            units = units[:2]
        return "\n".join(units).strip() or cleaned

    def _proactive_candidate_core_text(self, text: str) -> str:
        cleaned = _single_line(text, 260)
        if not cleaned:
            return ""
        cleaned = re.sub(r"^[\w\u4e00-\u9fffぁ-んァ-ヶー]{1,8}[，,、\s]+", "", cleaned)
        cleaned = re.sub(r"^(?:早上好|早安|上午好|中午好|午安|下午好|晚上好)[。！？!?…~～,，\s]*", "", cleaned)
        cleaned = re.sub(r"^(?:唔|嗯|诶|欸|啊|嗨|嘿)[。！？!?…~～,，\s]*", "", cleaned)
        cleaned = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]+", "", cleaned)
        filler_tokens = ("刚刚", "刚才", "现在", "今天", "这会儿", "好像", "感觉", "一点", "有点")
        for token in filler_tokens:
            cleaned = cleaned.replace(token, "")
        return cleaned

    def _proactive_candidate_bigrams(self, text: str) -> set[str]:
        cleaned = self._proactive_candidate_core_text(text)
        if len(cleaned) < 2:
            return set()
        return {cleaned[index : index + 2] for index in range(len(cleaned) - 1)}

    def _collapse_near_duplicate_proactive_lines(self, lines: list[str]) -> list[str]:
        kept: list[str] = []
        for line in lines:
            current = line.strip()
            if not current:
                continue
            current_core = self._proactive_candidate_core_text(current)
            duplicate_index = -1
            for index, old in enumerate(kept):
                old_core = self._proactive_candidate_core_text(old)
                if not current_core or not old_core:
                    continue
                shorter = min(len(current_core), len(old_core))
                if shorter < 8:
                    continue
                same_core = current_core == old_core
                contained = current_core in old_core or old_core in current_core
                current_bigrams = self._proactive_candidate_bigrams(current)
                old_bigrams = self._proactive_candidate_bigrams(old)
                bigram_overlap = 0.0
                if current_bigrams and old_bigrams:
                    bigram_overlap = len(current_bigrams & old_bigrams) / max(1, min(len(current_bigrams), len(old_bigrams)))
                if same_core or contained or bigram_overlap >= 0.86:
                    duplicate_index = index
                    break
            if duplicate_index < 0:
                kept.append(current)
                continue
            old = kept[duplicate_index]
            old_core = self._proactive_candidate_core_text(old)
            prefer_current = (
                len(current_core) < len(old_core)
                or (len(current) + 6 < len(old) and not re.search(r"^(?:早上好|早安|上午好|中午好|午安|下午好|晚上好)", current))
            )
            if prefer_current:
                kept[duplicate_index] = current
        return kept

    def _should_drop_vague_generic_proactive(
        self,
        user: dict[str, Any],
        *,
        reason: str,
        action: str,
        action_context: str = "",
        text: str = "",
    ) -> bool:
        if reason != "check_in" or action != "message":
            return False
        if _safe_int(user.get("ignored_streak"), 0, 0) < 2:
            return False
        context = _single_line(action_context, 180)
        if context and not context.startswith("message") and "普通私聊文本" not in context:
            return False
        cleaned = _single_line(text, 160)
        if not cleaned:
            return True
        vague_tokens = ("想找你", "来看看你", "刷存在感", "最近忙不忙", "辛苦了", "在吗", "有点想你", "没什么事", "就是想")
        concrete_markers = ("刚", "路上", "窗", "雨", "书", "饭", "水", "图", "群", "视频", "作业", "游戏", "梦")
        return any(token in cleaned for token in vague_tokens) and not any(token in cleaned for token in concrete_markers)

    def _apply_proactive_style_variation(self, text: str, user: dict[str, Any]) -> str:
        cleaned = str(text or "").strip()
        if not cleaned:
            return ""
        items = user.get("action_consequences")
        if not isinstance(items, list):
            return cleaned
        recent_texts = [
            _single_line(item.get("text"), 80)
            for item in items[-5:]
            if isinstance(item, dict) and _single_line(item.get("text"), 80)
        ]
        if not recent_texts:
            return cleaned
        current_opening = re.split(r"[，,。！？!?…\s]", _single_line(cleaned, 80), maxsplit=1)[0][:6]
        repeated_opening = current_opening and any(
            re.split(r"[，,。！？!?…\s]", text, maxsplit=1)[0][:6] == current_opening
            for text in recent_texts
        )
        proactive_voice = str(
            runtime_persona_setting(self, "persona_proactive_voice_prompt", "") or ""
        )
        # Repetition control must not erase an opening explicitly defined by the persona.
        if repeated_opening and current_opening not in proactive_voice:
            cleaned = re.sub(r"^(唔|嗯|诶|啊|欸)[…\.。!！?？~～\s，,]*", "", cleaned).strip()
            cleaned = re.sub(r"^(刚好|突然|我就是|我来|来找你)[^，,。！？!?…\n]{0,16}[，,。！？!?…\s]*", "", cleaned).strip()
        if sum(cleaned.count(token) for token in ("唔", "嗯", "诶", "呀", "啦", "嘛", "哦", "呢")) >= 5:
            cleaned = re.sub(r"(呀|啦|嘛|哦|呢)(?=.*\1)", "", cleaned)
        return cleaned or str(text or "").strip()

    def _format_action_prompt_context(self, action: str, action_context: str) -> str:
        context = str(action_context or "").strip()
        if not context:
            return "普通文字"
        return _single_line(self._sanitize_action_context_text(action, context), 420)

    def _sanitize_action_boundaries(
        self,
        text: str,
        *,
        reason: str,
        action: str,
        action_context: str = "",
        has_real_image: bool = False,
    ) -> str:
        cleaned = self._soften_social_proactive_text(text, action=action)
        if not cleaned:
            return ""
        if not has_real_image and "photo_text" not in action:
            cleaned = self._remove_unbacked_media_claims(cleaned)
        if "screen_peek" in action:
            photo_patterns = (
                "拍了张照片",
                "拍了照片",
                "拍了自拍",
                "自拍",
                "风景照",
                "窗外阳光",
                "要看看吗",
                "给你看照片",
                "发你照片",
                "看图",
            )
            if any(pattern in cleaned for pattern in photo_patterns):
                return ""
        if "poke" in action and "photo_text" not in action and "voice" not in action:
            cleaned = cleaned.replace("戳一戳", "戳你一下")
            cleaned = cleaned.replace("我刚刚戳了你", "我刚戳你了")
            cleaned = cleaned.replace("我刚刚戳了你一下", "我刚戳你了")
        if action == "voice":
            cleaned = cleaned.replace("我给你发了一条语音", "刚给你发了条语音")
            cleaned = cleaned.replace("我发了一条语音", "刚给你发了条语音")
            cleaned = cleaned.replace("我生成了一条语音", "刚给你发了条语音")
            cleaned = cleaned.replace("我合成了一条语音", "刚给你发了条语音")
            cleaned = cleaned.replace("要不要听", "你有空再听嘛")
            cleaned = cleaned.replace("要听吗", "你有空再听嘛")
        if action == "photo_text":
            if has_real_image:
                if reason not in {"bili_video_share", "news_share", "web_exploration_share"}:
                    cleaned = self._repair_non_external_title_share_text(
                        cleaned,
                        reason=reason,
                        action_context=action_context,
                    )
                replacements = {
                    "我画了一张图": "这个画面",
                    "我刚画了张图": "这个画面",
                    "我生成了一张图": "这个画面",
                    "我做了张图": "这个画面",
                    "我生了一张图": "这个画面",
                    "我渲染了一张图": "这个画面",
                    "画面是": "画面里是",
                }
                for old, new in replacements.items():
                    cleaned = cleaned.replace(old, new)
                queue_replacements = {
                    "图好了": "",
                    "图片好了": "",
                    "照片好了": "",
                    "图生成好了": "",
                    "图片生成好了": "",
                    "还在队列里": "",
                    "还在排队": "",
                    "等图出来": "",
                    "等图片出来": "",
                    "已经发过去啦": "",
                    "已经发过去了": "",
                }
                for old, new in queue_replacements.items():
                    cleaned = cleaned.replace(old, new)
                for old in ("要看看吗", "要看吗", "想看吗"):
                    cleaned = cleaned.replace(old, "")
                cleaned = self._deemphasize_state_report_preamble(cleaned, reason=reason)
                return self._soften_social_proactive_text(cleaned, action=action)
            replacements = {
                "拍了张照片": "想到一个画面",
                "拍了照片": "想到一个画面",
                "拍了美美的照片": "想到一个挺想拍下来的画面",
                "发你照片": "想跟你说说刚才那个画面",
                "给你看照片": "想跟你说说刚才那个画面",
                "要看看吗": "先跟你说一下",
                "要看吗": "先跟你说一下",
            }
            for old, new in replacements.items():
                cleaned = cleaned.replace(old, new)
        cleaned = self._deemphasize_state_report_preamble(cleaned, reason=reason)
        return self._soften_social_proactive_text(cleaned, action=action)

    def _repair_non_external_title_share_text(
        self,
        text: str,
        *,
        reason: str = "",
        action_context: str = "",
    ) -> str:
        cleaned = str(text or "").strip()
        if not cleaned:
            return ""
        if reason in {"bili_video_share", "news_share", "web_exploration_share"}:
            return cleaned
        title_leak_pattern = r"刚看到[，,、\s]*[“\"『「].{2,60}[”\"』」](?:这个)?标题"
        if not re.search(title_leak_pattern, cleaned):
            return cleaned
        context = _single_line(action_context, 520)
        if reason == "group_share" or "群" in context:
            repaired = re.sub(rf"{title_leak_pattern}[，,。！？!?\s]*", "", cleaned, count=1).strip()
            return repaired if len(repaired) >= 2 else ""
        if "图片路径：" in context or "真实图片文件：" in context or "photo_text" in context:
            repaired = re.sub(rf"{title_leak_pattern}[，,。！？!?\s]*", "", cleaned, count=1).strip()
            return repaired if len(repaired) >= 2 else ""
        repaired = re.sub(
            r"刚看到[，,、\s]*[“\"『「]([^”\"』」]{2,60})[”\"』」](?:这个)?标题[，,。！？!?\s]*",
            "",
            cleaned,
            count=1,
        )
        return repaired.strip()

    def _remove_unbacked_media_claims(self, text: str) -> str:
        cleaned = str(text or "").strip()
        if not cleaned:
            return ""
        replacements = {
            "我拍了张照片": "我看到一个画面",
            "我拍了照片": "我看到一个画面",
            "拍了张照片": "看到一个画面",
            "拍了照片": "看到一个画面",
            "拍了张照": "看到一个画面",
            "拍了照": "看到一个画面",
            "给你拍了张照片": "看到一个画面就想到你",
            "给你拍了照片": "看到一个画面就想到你",
            "给你拍了张照": "看到一个画面就想到你",
            "给你拍了照": "看到一个画面就想到你",
            "发你看看": "跟你说一下",
            "发给你看看": "跟你说一下",
            "发你看": "跟你说一下",
            "发给你看": "跟你说一下",
            "给你看照片": "跟你说说这个画面",
            "给你看图": "跟你说说这个画面",
            "看图": "听我说",
            "你看看喜不喜欢": "你应该会喜欢",
            "你看看喜欢吗": "你应该会喜欢",
            "你看看": "跟你说一下",
        }
        for old, new in replacements.items():
            cleaned = cleaned.replace(old, new)
        cleaned = re.sub(r"[，,、\s]*(?:照片|图片|图)(?:里|上)?[，,、\s]*(?=被|看着|颜色|特别|挺)", "画面", cleaned)
        cleaned = re.sub(r"(?:这张|那张|这幅|那幅)(?:照片|图片|图)", "这个画面", cleaned)
        cleaned = cleaned.replace("[图片]", "").replace("【图片】", "")
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" ，,。")
        return cleaned

    def _is_overabstract_proactive_text(self, text: str, *, action: str) -> bool:
        cleaned = _single_line(text, 220)
        if not cleaned:
            return False
        weak_patterns = (
            "最近忙不忙",
            "发现你好像在忙",
            "数据有意思吗",
            "刚好想到你",
            "来找你一下",
            "碰你一下",
            "我就是来一下",
            "顺手来一下",
        )
        if any(token in cleaned for token in weak_patterns):
            return True
        if "screen_peek" in action and any(token in cleaned for token in ("还在忙啊", "看你在忙", "你好像在忙")):
            return True
        return False

    def _ground_proactive_text(
        self,
        text: str,
        *,
        reason: str,
        action: str,
        action_context: str,
    ) -> str:
        context = str(action_context or "")
        if reason == "goodnight_screen_check":
            return "还没睡的话，忙完就早点休息，不用回我。"
        if "screen_peek" in action:
            if "逻辑分支" in context:
                return "你还在跟那个逻辑分支较劲啊。先别急,慢慢捋嘛。"
            if any(token in context for token in ("测试", "进度", "插件")):
                return "你还在盯那个进度啊。眼睛先歇一下啦。"
            return "你半天都没抬头了诶。先缓一口气。"
        if "poke" in action:
            return "我刚戳你了。怎么又不出声啦。"
        if "photo_text" in action:
            return text
        if "voice" in action:
            return "刚给你发了条语音。你有空再听嘛。"
        if reason == "quiet_care":
            return "感觉你这阵子都没怎么松下来。歇一小会儿嘛,又不会怎样。"
        if reason == "evening_greeting":
            return "都这个点了,你还没收工吗。别一直绷着啦。"
        if reason == "noon_greeting":
            return "中午了诶。你吃东西没有,别又随便糊弄过去。"
        if reason in {"meal_care", "meal_care_followup"}:
            return "到饭点了。你吃东西没有呀？"
        if reason in {"activity_share", "diary_share", "background_schedule"}:
            return "有件小事想跟你说一下。"
        return "刚好到能休息一小会儿的时候,想问你一句。"

