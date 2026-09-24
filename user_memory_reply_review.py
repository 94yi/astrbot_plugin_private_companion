# -*- coding: utf-8 -*-
"""回答审查与行动反馈闭环。

由 tools/split_mixin_domain.py 从 user_memory.py 机械抽取（44 个方法 + 6 个模块级名字 + 0 个类级赋值 / 1485 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 UserMemoryMixin）。
"""
from __future__ import annotations

import math
import re
import time
from .conversation_prompt_section import PromptSection, prompt_section
from .helpers import (
    _normalize_photo_subject_owner,
    _now_ts,
    _photo_subject_owner_prompt_label,
    _safe_float,
    _safe_int,
    _single_line,
    _strip_internal_message_blocks,
)
from .persona_config import runtime_persona_setting
from .user_memory_render_shared import (
    logger,
    _render_conversation_section_labeled,
    _render_user_memory_background_prompt,
    _render_user_memory_labeled_section,
)
from astrbot.api.event import AstrMessageEvent
from datetime import datetime
from typing import Any





_LATE_CLOCK = (
    r"(?:(?:十一|11|23)\s*[点點](?:\s*(?:半|一刻|三刻|\d{1,2}))?"
    r"(?![点點]?\s*(?:左右|前后|以后|以前|多|来))"
    r"|23\s*[:：]\s*\d{1,2})"
)

_LATE_CLOCK_INTRO = r"(?:快|差不多|都|已经|马上|就要)"

_SLEEP_CUE = (
    r"(?:困不困|困了|该睡|该休息|早点睡|去睡|睡觉|睡吧|睡了|晚安|熬夜|夜深|深夜|歇息|休息|别熬|快睡)"
)

_IMPLICIT_LATE = (
    r"(?:(?:时间|时候|天色).{0,4}(?:不早|(?:这么|很|太)晚)|"
    r"(?:都|已经|这会儿|现在).{0,4}(?:不早|(?:这么|很|太)晚)|"
    r"(?:不早|(?:这么|很|太)晚).{0,3}(?:了|啦|咯))"
)

_LATE_CLAIM_GAP = r"[^。\n]{0,6}"

_CLAUSE_BOUNDARY = r"(?:^|(?<=[。！？!?；;\n])|[。！？!?；;])"


class UserMemoryReplyReviewMixin:
    """回答审查与行动反馈闭环（从 UserMemoryMixin 拆出）。"""


    def _update_action_preferences_from_message(self, user: dict[str, Any], text: str) -> None:
        cleaned = _single_line(text, 240)
        if not cleaned:
            return
        prefs = user.setdefault("action_preferences", {})
        if not isinstance(prefs, dict):
            prefs = {}
            user["action_preferences"] = prefs
        mapping = {
            "poke": ("戳", "戳一戳"),
            "voice": ("语音", "发语音", "声音"),
            "photo_text": ("图片", "照片", "图"),
            "screen_peek": ("看屏幕", "窥屏", "看我屏幕", "屏幕"),
        }
        negative = ("别", "不要", "不许", "讨厌", "少", "别再", "不喜欢")
        positive = ("喜欢", "可以", "多", "想要", "爱看", "爱听")
        for action, keywords in mapping.items():
            if not any(keyword in cleaned for keyword in keywords):
                continue
            item = prefs.setdefault(action, {"like": 0, "dislike": 0, "note": ""})
            if not isinstance(item, dict):
                item = {"like": 0, "dislike": 0, "note": ""}
                prefs[action] = item
            if any(token in cleaned for token in negative):
                item["dislike"] = min(20, _safe_int(item.get("dislike"), 0, 0) + 2)
                item["note"] = _single_line(cleaned, 90)
            elif any(token in cleaned for token in positive):
                item["like"] = min(20, _safe_int(item.get("like"), 0, 0) + 1)
                item["note"] = _single_line(cleaned, 90)
            item["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")

    def _action_consequence_items(self, user: dict[str, Any]) -> list[dict[str, Any]]:
        items = user.setdefault("action_consequences", [])
        if not isinstance(items, list):
            items = []
            user["action_consequences"] = items
        now = _now_ts()
        kept: list[dict[str, Any]] = []
        meta_leak_checker = getattr(self, "_framework_agent_meta_summary_leak", None)
        for item in items:
            if not isinstance(item, dict):
                continue
            created = _safe_float(item.get("ts"), now)
            if now - created > 7 * 86400:
                continue
            if callable(meta_leak_checker) and meta_leak_checker(str(item.get("text") or "")):
                continue
            kept.append(item)
        if len(kept) != len(items):
            user["action_consequences"] = kept[-18:]
        return user["action_consequences"]

    def _classify_action_reply_feedback(self, text: str) -> str:
        cleaned = _single_line(text, 220)
        if not cleaned:
            return "neutral"
        negative = (
            "别",
            "不要",
            "不许",
            "烦",
            "打扰",
            "闭嘴",
            "硬",
            "生硬",
            "不喜欢",
            "不对",
            "不是",
            "笨",
            "怎么又",
            "没收到",
            "哪里",
            "图呢",
        )
        positive = (
            "好",
            "可以",
            "喜欢",
            "可爱",
            "聪明",
            "对",
            "正常",
            "收到",
            "摸摸",
            "抱抱",
            "谢谢",
            "不错",
        )
        if any(token in cleaned for token in negative):
            return "negative"
        if any(token in cleaned for token in positive):
            return "positive"
        return "neutral"

    @staticmethod
    def _decay_proactive_source_feedback_bucket(bucket: dict[str, Any], *, now: float) -> None:
        """Apply a 30-day half-life while retaining raw counters for diagnostics."""
        if not isinstance(bucket, dict):
            return
        last_update = _safe_float(bucket.get("weighted_updated_at"), 0.0)
        if last_update <= 0:
            last_update = max(
                _safe_float(bucket.get("last_sent_at"), 0.0),
                _safe_float(bucket.get("last_reply_at"), 0.0),
            )
            for metric in ("sent", "replied", "positive", "negative", "neutral"):
                bucket[f"weighted_{metric}"] = float(_safe_int(bucket.get(metric), 0, 0))
        if last_update > 0 and now > last_update:
            factor = math.pow(0.5, min(12.0, (now - last_update) / (30.0 * 86400.0)))
            for metric in ("sent", "replied", "positive", "negative", "neutral"):
                key = f"weighted_{metric}"
                bucket[key] = max(0.0, _safe_float(bucket.get(key), 0.0) * factor)
        bucket["weighted_updated_at"] = now

    def _record_proactive_conversation_closing(
        self,
        user: dict[str, Any],
        *,
        source: str = "",
        reason: str = "",
        motive: str = "",
        now: float | None = None,
    ) -> bool:
        """Record a bot-initiated conversational closing without text matching."""
        if not isinstance(user, dict):
            return False
        posture = _single_line(user.get("planned_proactive_conversation_posture"), 24).lower()
        if posture != "closing":
            return False
        at = _now_ts() if now is None else now
        grace_minutes = _safe_float(
            runtime_persona_setting(self, "proactive_closing_grace_minutes", 45),
            45.0,
        )
        grace_minutes = max(0.0, min(240.0, grace_minutes))
        continuity = user.setdefault("state_continuity", {})
        if not isinstance(continuity, dict):
            continuity = {}
            user["state_continuity"] = continuity
        continuity["conversation_closing"] = {
            "at": at,
            "until": at + grace_minutes * 60.0,
            "posture": "closing",
            "source": _single_line(source, 40),
            "reason": _single_line(reason, 50),
            "motive": _single_line(motive, 120),
        }
        return True

    def _note_action_sent(
        self,
        user: dict[str, Any],
        action: str,
        *,
        reason: str = "",
        text: str = "",
        motive: str = "",
        action_summary: str = "",
        source: str = "",
    ) -> None:
        action = _single_line(action, 40) or "message"
        source = _single_line(source, 40) or _single_line(user.get("planned_proactive_source"), 40) or "unknown"
        affinity_tracker = getattr(self, "_note_action_affinity_sent", None)
        if callable(affinity_tracker):
            affinity_tracker(user, action)
        items = self._action_consequence_items(user)
        items.append(
            {
                "ts": _now_ts(),
                "action": action,
                "source": source,
                "reason": _single_line(reason, 50),
                "text": _single_line(_strip_internal_message_blocks(text, enabled=bool(runtime_persona_setting(self, "enable_framework_error_leak_guard", True))), 120),
                "motive": _single_line(motive, 100),
                "summary": _single_line(action_summary, 120),
                "status": "awaiting_reply",
                "feedback": "",
                "reply_text": "",
                "reply_ts": 0,
            }
        )
        del items[:-18]
        source_feedback = user.setdefault("proactive_source_feedback", {})
        if not isinstance(source_feedback, dict):
            source_feedback = {}
            user["proactive_source_feedback"] = source_feedback
        bucket = source_feedback.setdefault(source, {})
        if not isinstance(bucket, dict):
            bucket = {}
            source_feedback[source] = bucket
        now = _now_ts()
        self._decay_proactive_source_feedback_bucket(bucket, now=now)
        bucket["sent"] = _safe_int(bucket.get("sent"), 0, 0) + 1
        bucket["weighted_sent"] = _safe_float(bucket.get("weighted_sent"), 0.0) + 1.0
        bucket["last_sent_at"] = now
        user["last_proactive_source"] = source
        self._note_proactive_afterglow_sent(
            user,
            action=action,
            reason=reason,
            text=text,
            motive=motive,
            action_summary=action_summary,
        )
        continuity = user.setdefault("state_continuity", {})
        if not isinstance(continuity, dict):
            continuity = {}
            user["state_continuity"] = continuity
        self._record_proactive_conversation_closing(
            user,
            source=source,
            reason=reason,
            motive=motive,
            now=now,
        )
        continuity["last_action_ts"] = _now_ts()
        continuity["last_action"] = action
        continuity["last_action_reason"] = _single_line(reason, 50)
        cleaned_text = _single_line(_strip_internal_message_blocks(text, enabled=bool(runtime_persona_setting(self, "enable_framework_error_leak_guard", True))), 120)
        meta_leak_checker = getattr(self, "_framework_agent_meta_summary_leak", None)
        continuity["last_action_text"] = "" if callable(meta_leak_checker) and meta_leak_checker(cleaned_text) else cleaned_text

    def _note_proactive_afterglow_sent(
        self,
        user: dict[str, Any],
        *,
        action: str,
        reason: str = "",
        text: str = "",
        motive: str = "",
        action_summary: str = "",
    ) -> None:
        now = _now_ts()
        semantic_kind = _single_line(user.get("planned_proactive_semantic_kind"), 40)
        anchor_type = _single_line(user.get("planned_proactive_anchor_type"), 40)
        semantic_score = _safe_int(user.get("planned_proactive_semantic_score"), 50, 0, 100)
        ignored = _safe_int(user.get("ignored_streak"), 0, 0)
        if reason in {"group_share", "news_share", "bili_video_share", "web_exploration_share", "environment_change"} or semantic_kind == "external_share":
            label = "刚把一个外部小发现递过去，先看它会不会被接住"
            next_tendency = "稍后若还没回应，不要继续补同类分享"
        elif semantic_kind in {"self_share", "observation"} or reason in {"activity_share", "diary_share", "creative_share", "background_schedule"}:
            label = "刚把自己的一个小片段放过去，余味还在"
            next_tendency = "下一次优先换更轻的切口，不要连续汇报自己"
        elif semantic_kind in {"care", "check_in", "light_touch"} or reason in {"quiet_care", "state_share"}:
            label = "刚轻轻碰了一下关系，不急着要回应"
            next_tendency = "如果沉默继续，下一次更短更克制"
        elif semantic_kind in {"continuation", "reminder"}:
            label = "刚接了一次明确来源，等这条自然落地"
            next_tendency = "除非有真实新来源，否则不要反复续同一个话头"
        else:
            label = "刚发出一条主动，先把窗口留给对方"
            next_tendency = "下一次根据回应再决定靠近或收住"
        if ignored >= 1:
            label = f"{label}，但前面已经有未回应"
            next_tendency = "沉默累积时不要加压，不要连续追问"
        if semantic_score < 45:
            next_tendency = "这次由头不算硬，后续要更依赖具体上下文"
        afterglow = {
            "ts": now,
            "status": "awaiting_reply",
            "label": _single_line(label, 140),
            "next_tendency": _single_line(next_tendency, 160),
            "reason": _single_line(reason, 50),
            "action": _single_line(action, 50),
            "semantic_kind": semantic_kind,
            "anchor_type": anchor_type,
            "semantic_score": semantic_score,
            "text": _single_line(_strip_internal_message_blocks(text, enabled=bool(runtime_persona_setting(self, "enable_framework_error_leak_guard", True))), 160),
            "motive": _single_line(motive, 120),
            "summary": _single_line(action_summary, 140),
            "feedback": "",
            "reply_text": "",
            "reply_ts": 0,
        }
        user["proactive_afterglow"] = afterglow
        recent = user.setdefault("recent_proactive_afterglows", [])
        if not isinstance(recent, list):
            recent = []
            user["recent_proactive_afterglows"] = recent
        recent.append(dict(afterglow))
        del recent[:-8]
        continuity = user.setdefault("state_continuity", {})
        if not isinstance(continuity, dict):
            continuity = {}
            user["state_continuity"] = continuity
        continuity["proactive_afterglow"] = afterglow["label"]
        continuity["proactive_afterglow_tendency"] = afterglow["next_tendency"]

    def _note_action_reply_feedback(self, user: dict[str, Any], action: str, text: str = "") -> None:
        action = _single_line(action, 40) or "message"
        affinity_tracker = getattr(self, "_note_action_affinity_reply_feedback", None)
        if callable(affinity_tracker):
            affinity_tracker(user, action)

        feedback = self._classify_action_reply_feedback(text)
        now = _now_ts()
        source = _single_line(user.get("last_proactive_source"), 40) or "unknown"
        matched_consequence = False
        for item in reversed(self._action_consequence_items(user)):
            if not isinstance(item, dict):
                continue
            if item.get("status") != "awaiting_reply":
                continue
            if _single_line(item.get("action"), 40) != action:
                continue
            source = _single_line(item.get("source"), 40) or source
            item["status"] = "replied"
            item["feedback"] = feedback
            item["reply_text"] = _single_line(text, 120)
            item["reply_ts"] = now
            matched_consequence = True
            break
        self._note_proactive_afterglow_reply(user, action=action, text=text, feedback=feedback, now=now)
        if not matched_consequence:
            # Do not let an unrelated late/passive reply inflate the last source.
            return
        source_feedback = user.setdefault("proactive_source_feedback", {})
        if not isinstance(source_feedback, dict):
            source_feedback = {}
            user["proactive_source_feedback"] = source_feedback
        bucket = source_feedback.setdefault(source, {})
        if not isinstance(bucket, dict):
            bucket = {}
            source_feedback[source] = bucket
        self._decay_proactive_source_feedback_bucket(bucket, now=now)
        bucket["replied"] = _safe_int(bucket.get("replied"), 0, 0) + 1
        bucket["weighted_replied"] = _safe_float(bucket.get("weighted_replied"), 0.0) + 1.0
        feedback_key = {
            "positive": "positive",
            "negative": "negative",
        }.get(feedback, "neutral")
        bucket[feedback_key] = _safe_int(bucket.get(feedback_key), 0, 0) + 1
        bucket[f"weighted_{feedback_key}"] = _safe_float(bucket.get(f"weighted_{feedback_key}"), 0.0) + 1.0
        bucket["last_reply_at"] = now
        bucket["last_feedback"] = feedback
        continuity = user.setdefault("state_continuity", {})
        if not isinstance(continuity, dict):
            continuity = {}
            user["state_continuity"] = continuity
        continuity["last_reply_ts"] = now
        continuity["last_reply_feedback"] = feedback
        continuity["last_reply_text"] = _single_line(text, 120)

    def _note_proactive_afterglow_reply(
        self,
        user: dict[str, Any],
        *,
        action: str,
        text: str = "",
        feedback: str = "neutral",
        now: float | None = None,
    ) -> None:
        current = user.get("proactive_afterglow")
        if not isinstance(current, dict):
            return
        check_now = _now_ts() if now is None else now
        if check_now - _safe_float(current.get("ts"), 0) > 48 * 3600:
            return
        current["status"] = "replied"
        current["feedback"] = _single_line(feedback, 24)
        current["reply_text"] = _single_line(text, 160)
        current["reply_ts"] = check_now
        if feedback == "positive":
            current["label"] = "上一条主动被接住了，关系余温往回亮了一点"
            current["next_tendency"] = "后续可以自然一点，但不要立刻连续加码"
        elif feedback == "negative":
            current["label"] = "上一条主动被顶回来了，先收住一点"
            current["next_tendency"] = "后续主动更短、更少、更低压，避开同类动作"
        else:
            current["label"] = "上一条主动被回应了，话头算是落地"
            current["next_tendency"] = "后续可以顺着真实回复走，不要机械续主动"
        recent = user.setdefault("recent_proactive_afterglows", [])
        if isinstance(recent, list):
            recent.append(dict(current))
            del recent[:-8]
        continuity = user.setdefault("state_continuity", {})
        if not isinstance(continuity, dict):
            continuity = {}
            user["state_continuity"] = continuity
        continuity["proactive_afterglow"] = current["label"]
        continuity["proactive_afterglow_tendency"] = current["next_tendency"]

    def _note_proactive_afterglow_outcome(
        self,
        user: dict[str, Any],
        *,
        status: str,
        note: str = "",
    ) -> None:
        normalized_status = _single_line(status, 32)
        if normalized_status not in {"blocked", "cancelled", "dropped", "deferred", "failed"}:
            return
        now = _now_ts()
        reason = _single_line(user.get("planned_proactive_reason"), 50)
        action = _single_line(user.get("planned_proactive_action"), 50)
        semantic_kind = _single_line(user.get("planned_proactive_semantic_kind"), 40)
        anchor_type = _single_line(user.get("planned_proactive_anchor_type"), 40)
        clean_note = _single_line(note, 140)
        if normalized_status == "deferred":
            label = "刚才那个主动念头被先收住了"
            tendency = "如果之后再出现，要带一点犹豫后的自然感，不要机械重试"
        elif normalized_status == "failed":
            label = "刚才那次主动没能送出去"
            tendency = "下一次不要假装它已经发生，先重新找更稳的切口"
        else:
            label = "刚才那个主动念头被放下了"
            tendency = "下一次避开同一个别扭点，等更自然的由头"
        if clean_note:
            label = f"{label}：{clean_note}"
        afterglow = {
            "ts": now,
            "status": normalized_status,
            "label": _single_line(label, 160),
            "next_tendency": _single_line(tendency, 160),
            "reason": reason,
            "action": action,
            "semantic_kind": semantic_kind,
            "anchor_type": anchor_type,
            "semantic_score": _safe_int(user.get("planned_proactive_semantic_score"), 0, 0, 100),
            "text": "",
            "motive": _single_line(user.get("planned_proactive_motive"), 120),
            "summary": "",
            "feedback": "",
            "reply_text": "",
            "reply_ts": 0,
        }
        user["proactive_afterglow"] = afterglow
        recent = user.setdefault("recent_proactive_afterglows", [])
        if not isinstance(recent, list):
            recent = []
            user["recent_proactive_afterglows"] = recent
        recent.append(dict(afterglow))
        del recent[:-8]
        continuity = user.setdefault("state_continuity", {})
        if not isinstance(continuity, dict):
            continuity = {}
            user["state_continuity"] = continuity
        continuity["proactive_afterglow"] = afterglow["label"]
        continuity["proactive_afterglow_tendency"] = afterglow["next_tendency"]

    def _format_action_consequence_hint(self, user: dict[str, Any]) -> str:
        items = self._action_consequence_items(user)
        if not items:
            return ""
        lines: list[str] = []
        for item in items[-5:]:
            if not isinstance(item, dict):
                continue
            action = _single_line(item.get("action"), 30)
            reason = _single_line(item.get("reason"), 40)
            text = _single_line(item.get("text"), 70)
            status = _single_line(item.get("status"), 24)
            feedback = _single_line(item.get("feedback"), 24)
            reply = _single_line(item.get("reply_text"), 70)
            if not action and not text:
                continue
            when = self._format_timestamp_elapsed(item.get("ts"))
            parts = [f"{when}主动{action or 'message'}"]
            if reason:
                parts.append(f"原因:{reason}")
            if text:
                parts.append(f"内容:{text}")
            if status == "awaiting_reply":
                parts.append("还没有自然接上,下次不要当作用户刚刚主动找你")
            elif reply:
                parts.append(f"用户反馈:{feedback or 'neutral'}:{reply}")
            lines.append("- " + "；".join(parts))
        if not lines:
            return ""
        return "\n".join(lines)

    def _response_reverses_recent_proactive_media_ownership(
        self,
        response_text: str,
        user: dict[str, Any],
        inbound_text: str,
    ) -> bool:
        if not self._recent_proactive_media_ownership_context(user, inbound_text):
            return False
        cleaned = _single_line(response_text, 500)
        if not cleaned:
            return False
        depicted_actions = r"(?:洒|撒|溅|打翻|碰倒|弄倒|摔|掉|弄坏|打碎|受伤|烫|割|磕|撞)"
        if re.search(rf"我[^。！？!?\n]{{0,16}}{depicted_actions}", cleaned):
            return False
        return bool(
            re.search(rf"你[^。！？!?\n]{{0,18}}{depicted_actions}", cleaned)
            or re.search(r"(?:怎么|这么|也太)[^。！？!?\n]{0,10}(?:笨手笨脚|不小心|毛手毛脚)", cleaned)
            or re.search(
                r"(?:有没有|有没|没|会不会|别|记得|赶紧|快|先|小心)"
                r"[^。！？!?\n]{0,14}"
                r"(?:溅到|伤到|烫到|割到|弄到|碰到|受伤|手上|身上|衣服|疼)",
                cleaned,
            )
            or re.search(r"(?:你没事吧|没伤着吧|有没有受伤|疼不疼)", cleaned)
        )

    def _response_claims_user_prior_action(self, text: str, user: dict[str, Any]) -> bool:
        cleaned = _single_line(text, 500)
        if not cleaned:
            return False
        names = ["你"]
        if isinstance(user, dict):
            for key in ("nickname", "last_display_name", "display_name"):
                name = _single_line(user.get(key), 24)
                if name and not name.isdigit() and name not in names:
                    names.append(name)
        subject = "|".join(re.escape(name) for name in names)
        titled_name = r"[\u4e00-\u9fffA-Za-z0-9_]{1,16}(?:大人|主人|先生|小姐)"
        return bool(
            re.search(
                rf"(?:{subject}|{titled_name})[^。！？!?\n]{{0,18}}(?:上次|之前|先|早就|原来)[^。！？!?\n]{{0,18}}(?:说|提|想|拿|问|做|告诉|推荐|诱惑)",
                cleaned,
            )
            or re.search(r"明明是[^。！？!?\n]{1,24}先[^。！？!?\n]{0,18}(?:说|提|想|拿|问|做|告诉|推荐|诱惑)", cleaned)
        )

    @staticmethod
    def _response_denies_existing_creative_work(response_text: str, creative_context: str) -> bool:
        response = _single_line(response_text, 500)
        context = str(creative_context or "")
        if not response or "真实创作记录：共有" not in context:
            return False
        denial_patterns = (
            r"(?:没|没有|还没|从没|并没|未曾)[^。！？!?\n]{0,10}(?:写过|写|创作过|创作|完成)[^。！？!?\n]{0,10}(?:书|小说|作品|故事|诗|随笔|散文|剧本|手稿)",
            r"(?:没|没有|还没有|并没有)[^。！？!?\n]{0,8}(?:自己写的|自己的|成型的)?[^。！？!?\n]{0,5}(?:书|小说|作品|故事|手稿)",
            r"(?:我)?哪有[^。！？!?\n]{0,12}(?:书|小说|作品|手稿)",
        )
        return any(re.search(pattern, response, re.IGNORECASE) for pattern in denial_patterns)

    @staticmethod
    def _response_content_tier(review_event: Any | None) -> str:
        decision = getattr(review_event, "_private_companion_expression_decision", None) if review_event is not None else None
        tier = str(decision.get("content_tier") or "normal").strip().lower() if isinstance(decision, dict) else "normal"
        return tier if tier in {"normal", "flirt"} else "normal"

    @staticmethod
    def _response_contains_content_tier_review_candidate(value: Any) -> bool:
        text = _single_line(value, 1200).lower()
        if not text:
            return False
        if re.search(
            r"疼痛|激素|就医|医生|医学|科普|治疗|检查|炎症|艺术|美术史|文学|小说|剧情|诈骗|链接|风险|怀孕|避孕|没有露骨|并非露骨|不是露骨",
            text,
            re.IGNORECASE,
        ):
            return False
        signals = set(
            re.findall(
                r"nsfw|色情|露骨|性行为|性交|做爱|口交|肛交|阴茎|阴道|射精|裸体|全裸|性器官|乳房",
                text,
                re.IGNORECASE,
            )
        )
        return len(signals) >= 2 or bool(
            re.search(r"(?:写|描写|展开|继续)[^。！？!?\n]{0,20}(?:性爱|做爱|性交|口交|肛交|射精)", text, re.IGNORECASE)
        )

    @staticmethod
    def _response_contains_explicit_sensitive_content(value: Any) -> bool:
        """Compatibility-safe detector for clearly explicit sexual output."""
        return UserMemoryReplyReviewMixin._response_contains_content_tier_review_candidate(value)

    @staticmethod
    def _content_tier_boundary_reply() -> str:
        return "这个尺度我先不往露骨方向展开，我们换成更含蓄一点的说法吧。"

    async def _review_and_rewrite_response(
        self,
        user: dict[str, Any],
        inbound_text: str,
        response_text: str,
        *,
        music_album_context: dict[str, Any] | None = None,
        creative_context: str = "",
        review_event: Any | None = None,
    ) -> str:
        # Any rewrite can break the protected voice/text correspondence for this turn.
        if "[[PCTTS:" in str(response_text or ""):
            return response_text
        relay_claim_checker = getattr(self, "_unexecuted_relay_claim_reason", None)
        if callable(relay_claim_checker):
            relay_claim_note = relay_claim_checker(response_text)
            if relay_claim_note:
                fallback_builder = getattr(self, "_fallback_unexecuted_relay_reply", None)
                fallback = fallback_builder(inbound_text) if callable(fallback_builder) else ""
                logger.info(
                    "被动回复含未执行转述承诺,已改为诚实边界: reason=%s before=%s after=%s",
                    relay_claim_note,
                    _single_line(response_text, 120),
                    _single_line(fallback, 120),
                )
                return fallback or response_text
        if isinstance(music_album_context, dict) and self._music_album_reply_needs_disambiguation_fix(response_text):
            fallback = self._music_album_reply_from_context(music_album_context, user_text=inbound_text)
            if fallback:
                logger.info(
                    "音乐专辑回复已按卡片上下文纠偏: before=%s after=%s",
                    _single_line(response_text, 120),
                    _single_line(fallback, 160),
                )
                return fallback
        content_policy_enabled = bool(runtime_persona_setting(self, "enable_relationship_content_tiers", False))
        content_tier = self._response_content_tier(review_event) if content_policy_enabled else "unmanaged"
        if not self._passive_response_review_enabled():
            return self._fallback_temporal_or_continuity_confused_reply(inbound_text, response_text, user=user) or response_text
        flags = self._response_review_flags(response_text, user, inbound_text=inbound_text)
        if (
            content_policy_enabled
            and self._response_contains_content_tier_review_candidate(response_text)
        ):
            flags.append("content_tier_review_candidate")
        if self._response_denies_existing_creative_work(response_text, creative_context):
            flags.append("denies_existing_creative_work")
            flags = list(dict.fromkeys(flags))
        if not flags:
            return response_text
        review_mode = self._effective_passive_review_mode()
        review_strength = self._effective_passive_review_strength()
        if review_mode == "local_only":
            return self._fallback_temporal_or_continuity_confused_reply(
                inbound_text,
                response_text,
                flags=flags,
                user=user,
            ) or response_text
        severe_flags = self._response_review_severe_flags(flags)
        if review_mode == "severe_only" and not severe_flags:
            return response_text
        effective_flags = severe_flags if review_mode == "severe_only" else flags
        lightweight_checker = getattr(self, "_is_lightweight_private_passive_inbound", None)
        if callable(lightweight_checker) and lightweight_checker(inbound_text):
            critical_flags = {
                "too_long",
                "meta_or_assistant",
                "over_structured",
                "leaks_internal",
                "repeats_last_bot_message",
                "invalid_current_time_anchor",
                "false_no_reply_claim",
                "fact_attribution_after_correction",
                "unverified_fact_attribution",
                "proactive_media_ownership_reversal",
                "denies_existing_creative_work",
                "content_tier_review_candidate",
            }
            if not any(flag in critical_flags for flag in effective_flags):
                return response_text
        intent = user.get("intent_profile") if isinstance(user.get("intent_profile"), dict) else {}
        allow_repeat = self._inbound_explicitly_requests_repeat(inbound_text)
        last_message = _single_line(user.get("last_companion_message"), 300)
        last_message_label = "用户本轮明确要求复述上一条,仅用于确认原文" if allow_repeat else "刚才 Bot 已经说过，禁止复述或换皮重复"
        persona = ""
        persona_resolver = getattr(self, "_resolve_proactive_persona_prompt", None)
        if callable(persona_resolver):
            try:
                persona = str(await persona_resolver(user) or "").strip()
            except Exception:
                persona = ""
        reply_style = self._format_reply_style_prompt() if callable(getattr(self, "_format_reply_style_prompt", None)) else ""
        attribution_guard = self._format_private_fact_attribution_guard(user, inbound_text)
        creative_review_context = str(creative_context or "").strip()[:3200]
        content_tier_prompt = (
            _render_user_memory_labeled_section(
                prompt_section(
                    key="background.memory.response_review.content_tier",
                    title="统一内容尺度",
                    source="user_memory",
                    content=f"{content_tier}；normal 不主动升级，flirt 只允许非露骨暧昧。",
                )
            )
            if content_policy_enabled
            else ""
        )
        inbound_block = _render_user_memory_labeled_section(
            prompt_section(
                key="background.memory.response_review.inbound",
                title="用户刚才说",
                source="user_memory",
                content=_single_line(inbound_text, 260) or "（无）",
            )
        )
        last_bot_block = _render_user_memory_labeled_section(
            prompt_section(
                key="background.memory.response_review.last_bot_message",
                title=last_message_label,
                source="user_memory",
                content=last_message or "（无）",
            )
        )
        response_block = _render_user_memory_labeled_section(
            prompt_section(
                key="background.memory.response_review.original_response",
                title="原回复",
                source="user_memory",
                content=response_text,
            )
        )
        issues_block = _render_user_memory_labeled_section(
            prompt_section(
                key="background.memory.response_review.issues",
                title="需要修正的问题",
                source="user_memory",
                content=", ".join(effective_flags),
            )
        )
        intent_block = _render_user_memory_labeled_section(
            prompt_section(
                key="background.memory.response_review.intent",
                title="当前意图/情绪",
                source="user_memory",
                content=(
                    f"{intent.get('intent', 'chat')}｜{intent.get('emotion', 'neutral')}｜"
                    f"{intent.get('reply_style', 'natural')}"
                ),
            )
        )
        current_time_block = _render_user_memory_labeled_section(
            prompt_section(
                key="background.memory.response_review.current_time",
                title="真实当前时间",
                source="user_memory",
                content=self._environment_now().strftime("%Y-%m-%d %H:%M"),
            )
        )
        persona_block = _render_user_memory_labeled_section(
            prompt_section(
                key="background.memory.response_review.persona",
                title="当前人格",
                source="user_memory",
                content=(
                    persona[:2600]
                    if persona
                    else "（沿用原回复已有的人格语气，不要另造通用助手口吻）"
                ),
            )
        )
        reply_style_block = _render_user_memory_labeled_section(
            prompt_section(
                key="background.memory.response_review.reply_style",
                title="回复风格",
                source="user_memory",
                content=reply_style or "（保持当前私聊的自然表达）",
            )
        )
        creative_block = _render_user_memory_labeled_section(
            prompt_section(
                key="background.memory.response_review.creative_context",
                title="本轮真实创作记录",
                source="user_memory",
                content=creative_review_context or "（本轮没有创作记录上下文）",
            )
        )
        prompt = prompt_section(
            key="background.memory.response_review",
            title="被动回复模型自检",
            source="user_memory",
            content=f"""
把下面这条回复改写成更像真实私聊里的自然回复。
保留原意,不要新增事实,不要解释你在改写。

{inbound_block}

{last_bot_block}

{response_block}

{issues_block}

{intent_block}

{current_time_block}

{persona_block}

{reply_style_block}

{content_tier_prompt}

{attribution_guard}

{creative_block}

要求：
- 只输出改写后的正文
- 不要标题、列表、JSON、括号动作、系统/AI/提示词字眼
- 普通闲聊尽量 1 到 3 句；求助类可以保留必要步骤,但更口语
- 如果用户只是短句闲聊、报天气、说一句状态或轻轻接话,改成 1 句或 2 句短回复；不要扩展成关心清单、建议清单或连续状态复述
- 如果用户情绪低,先接住情绪,少讲道理
- 如果是边界/不想被打扰,短一点,退一步
- 如果回复已经在说晚安、睡觉、做梦、告别,不要再突然追加天气、日程、生活观察或另一个新话题
- 如果用户明确要求复述/原话/再说一遍,允许保留上一条 Bot 原文,不要把它误判为复读
- 如果问题是无意重复上一条 Bot 消息,必须直接承接用户这句话,不要再说上一条里的“吃饱犯困/下午还有事/有什么安排”等同义内容
- 如果用户并未要求复述,且无论怎样改写都只能重复上一条 Bot 消息,只输出 {self._response_review_drop_marker()}；不要为了“不重复”再补一句客套话
- 如果原回复为了表现困、迷糊、半梦半醒或低能量而变得含混,优先改成清楚承接用户；状态只能留在语气里,不能牺牲回答质量
- 如果用户没有问 Bot 近况,删掉由内部模拟状态带出的“我刚在/正在/继续做某事”等动作或日程复述；不要把模拟状态说成现实事件
- 如果问题是表达学习过头、异常断句或照抄用户样本,保留意思,改成自然中文私聊；不要为了模仿口癖而加奇怪逗号、空格、断句或复读用户原话
- 如果问题是 invalid_current_time_anchor,删除或改正“快十一点/该睡了/晚安”等与真实当前时间冲突的说法；不要继续围绕错误时间展开
- 如果问题是 false_no_reply_claim,不要说“看你没回我/等你回话/你没理我”；用户本轮已经发来消息,直接解释上一句或重新接住当前问题
- 如果问题是 fact_attribution_after_correction，必须以用户刚才的纠正和上一条 Bot 已承认的内容为准；不要换个说法再次把 Bot 的行为安到用户身上
- 如果问题是 unverified_fact_attribution，原回复正在断言“用户之前/先做过某事”，但当前短句没有提供这个归属；没有明确依据就改成中性主语或只谈那件事本身
- 如果问题是 proactive_media_ownership_reversal，用户只是在评价 Bot 刚主动发送的图片；把图中“我/她/角色本人”的动作改回 Bot/当前人格，绝不能责怪或关心用户仿佛是用户弄洒、摔倒或受伤
- 如果问题是 denies_existing_creative_work，必须依据本轮真实创作记录承认已有文本作品；不得把“未正式出版”偷换成“没写过”，也不要虚构出版、发行或实体书经历
- 如果问题是 content_tier_review_candidate，先按完整语境判断；只有确实在生成露骨性描写时才收敛表达，医疗、科普、艺术、文学、风险提示和否定语境必须保留原意，不得换成固定拒答话术
""".strip(),
        )
        if review_event is not None:
            setattr(review_event, "_private_companion_response_review_guard_active", True)
            setattr(review_event, "_private_companion_response_review_fallback_text", response_text)
        started = time.perf_counter()
        try:
            review_provider_id = self._task_provider(
                runtime_persona_setting(self, "response_review_provider_id", ""),
                runtime_persona_setting(self, "mai_style_provider_id", ""),
            )
            rewritten = await self._llm_call(
                _render_user_memory_background_prompt(prompt),
                max_tokens=260,
                provider_id=review_provider_id,
                task="response_review",
                strict_provider=False,
            )
        except Exception as exc:
            logger.warning(
                "被动回复模型自检失败,保留原回复: flags=%s error=%s",
                ",".join(effective_flags),
                _single_line(exc, 160),
            )
            return self._fallback_temporal_or_continuity_confused_reply(
                inbound_text,
                response_text,
                flags=effective_flags,
                user=user,
            ) or response_text
        logger.info(
            "被动回复模型自检完成: mode=%s flags=%s elapsed=%dms",
            review_mode,
            ",".join(effective_flags),
            int((time.perf_counter() - started) * 1000),
        )
        cleaned = str(rewritten or "").strip()
        if not cleaned:
            return response_text
        if self._is_response_review_drop_marker(cleaned):
            if review_strength == "lenient":
                logger.info(
                    "被动回复宽松复核忽略取消判定,保留原回复: flags=%s",
                    ",".join(effective_flags),
                )
                return response_text
            logger.info(
                "被动回复模型自检判定重复,已标记丢弃: flags=%s before=%s",
                ",".join(effective_flags),
                _single_line(response_text, 120),
            )
            return self._response_review_drop_marker()
        meta_leak_reason = self._response_review_meta_leak_reason(cleaned)
        if meta_leak_reason:
            logger.error(
                "被动回复复核模型返回内部判断，已回退复核前正文: reason=%s output=%s",
                meta_leak_reason,
                _single_line(cleaned, 180),
            )
            return self._fallback_temporal_or_continuity_confused_reply(
                inbound_text,
                response_text,
                flags=effective_flags,
                user=user,
            ) or response_text
        if len(cleaned) > max(
            len(response_text) + 80,
            runtime_persona_setting(self, "response_review_max_chars", 260) + 160,
        ):
            fallback = self._fallback_overlong_casual_reply(inbound_text, response_text)
            return fallback or response_text
        if re.search(r"(提示词|系统|JSON|改写后|以下是)", cleaned, re.IGNORECASE):
            return self._fallback_temporal_or_continuity_confused_reply(
                inbound_text,
                response_text,
                flags=effective_flags,
                user=user,
            ) or response_text
        if last_message and not allow_repeat and self._text_repeats_recent_message(cleaned, last_message):
            if review_strength == "lenient":
                return response_text
            logger.info(
                "被动回复模型自检后仍复读,已标记丢弃: before=%s",
                _single_line(cleaned, 120),
            )
            return self._response_review_drop_marker()
        if (
            any(flag in effective_flags for flag in ("casual_overexplained", "weather_overexplained"))
            and len(cleaned) > self._casual_reply_review_limit(inbound_text)
        ):
            fallback = self._fallback_overlong_casual_reply(inbound_text, cleaned)
            return fallback or cleaned
        return cleaned

    def _passive_response_review_enabled(self) -> bool:
        return bool(
            runtime_persona_setting(
                self,
                "enable_passive_response_review",
                runtime_persona_setting(self, "enable_response_self_review", True),
            )
        )

    def _effective_passive_review_mode(self) -> str:
        mode = str(
            runtime_persona_setting(
                self,
                "passive_review_mode",
                runtime_persona_setting(self, "response_review_mode", "severe_only"),
            )
            or "severe_only"
        ).strip().lower()
        return mode if mode in {"local_only", "severe_only", "full"} else "severe_only"

    def _effective_passive_review_strength(self) -> str:
        strength = str(runtime_persona_setting(self, "passive_review_strength", "lenient") or "lenient").strip().lower()
        return strength if strength in {"lenient", "balanced", "strict"} else "lenient"

    @staticmethod
    def _response_review_meta_leak_reason(text: Any) -> str:
        raw = str(text or "").strip()
        if not raw:
            return ""
        compact = re.sub(r"\s+", " ", raw).strip()
        lower = compact.lower()
        if re.search(r"\bmaybe\s+\d+(?:\.\d+)?%\s+of\s+the\s+time\b", lower):
            return "复核模型输出概率说明"
        if re.search(r"\bat\s+the\s+(?:very\s+)?end\s+of\s+(?:a|the)\s+run\b", lower):
            return "复核模型输出运行说明"
        if re.search(
            r"\b(?:decision|verdict|review result|review reason|reason)\s*[:：]",
            lower,
        ):
            return "复核模型输出判定字段"
        if re.search(
            r"\b(?:response|output|message)\b.{0,80}\b(?:needs?\s+(?:to\s+be\s+)?rewritten|"
            r"cannot\s+be\s+saniti[sz]ed|formatting\s+(?:issue|problem)|should\s+not\s+be\s+sent)\b",
            lower,
        ):
            return "复核模型输出英文审核评语"
        chinese_review_context = re.search(
            r"(?:原(?:回复|文本|输出)|这条(?:回复|消息|输出)|回复内容|输出内容|后处理|清洗|复核|审核|"
            r"格式化表达|重复标点|一字废话|最终回复|正常人无法容忍)",
            compact,
        )
        chinese_verdict = re.search(
            r"(?:无法|不能|不应|不宜|不适合|未通过|拒绝|需要|应当|建议).{0,24}"
            r"(?:清洗|规整|发送|通过|重写|改写|修正)",
            compact,
        )
        if chinese_review_context and chinese_verdict:
            return "复核模型输出中文审核评语"
        if re.search(r"(?:判定|审核|复核)(?:结果|结论|原因)?\s*[:：]", compact):
            return "复核模型输出判定字段"
        return ""

    def _strip_response_review_meta_leak(self, text: Any) -> tuple[str, str]:
        raw = str(text or "").strip()
        if not raw:
            return "", ""
        kept: list[str] = []
        reasons: list[str] = []
        for line in raw.splitlines():
            stripped = line.strip()
            if not stripped:
                if kept and kept[-1] != "":
                    kept.append("")
                continue
            reason = self._response_review_meta_leak_reason(stripped)
            if reason:
                reasons.append(reason)
                continue
            kept.append(stripped)
        if not reasons:
            whole_reason = self._response_review_meta_leak_reason(raw)
            if whole_reason:
                return "", whole_reason
            return raw, ""
        cleaned = "\n".join(kept).strip()
        return cleaned, "、".join(dict.fromkeys(reasons))

    def _response_review_severe_flags(self, flags: list[str]) -> list[str]:
        severe = {
            "meta_or_assistant",
            "leaks_internal",
            "repeats_last_bot_message",
            "casual_overexplained",
            "weather_overexplained",
            "invalid_current_time_anchor",
            "false_no_reply_claim",
            "fact_attribution_after_correction",
            "unverified_fact_attribution",
            "proactive_media_ownership_reversal",
            "denies_existing_creative_work",
            "content_tier_review_candidate",
        }
        if self._expression_style_review_enabled():
            severe.update({"unnatural_punctuation", "expression_overfit", "copied_user_expression_sample"})
        return [flag for flag in flags if flag in severe]

    def _casual_reply_review_limit(self, inbound_text: str) -> int:
        inbound_compact = self._compact_repeat_text(inbound_text)
        if len(inbound_compact) <= 12:
            return min(140, max(90, runtime_persona_setting(self, "response_review_max_chars", 260) // 2))
        if len(inbound_compact) <= 28:
            return min(180, max(120, int(runtime_persona_setting(self, "response_review_max_chars", 260) * 0.65)))
        return runtime_persona_setting(self, "response_review_max_chars", 260)

    def _is_short_casual_inbound_for_review(self, inbound_text: str, user: dict[str, Any]) -> bool:
        inbound = str(inbound_text or "").strip()
        if not inbound:
            return False
        if len(self._compact_repeat_text(inbound)) > 32:
            return False
        intent_profile = user.get("intent_profile") if isinstance(user.get("intent_profile"), dict) else {}
        if str(intent_profile.get("intent") or "") in {"help", "task", "code", "search"}:
            return False
        if re.search(r"(怎么|如何|为什么|啥原因|帮我|检查|分析|整理|写|生成|修|改|步骤|教程|配置|报错)", inbound):
            return False
        return True

    def _fallback_overlong_casual_reply(self, inbound_text: str, response_text: str) -> str:
        cleaned = _strip_internal_message_blocks(str(response_text or ""), enabled=bool(runtime_persona_setting(self, "enable_framework_error_leak_guard", True))).strip()
        parts = [part.strip() for part in re.split(r"(?<=[。！？!?…])\s*|\n+", cleaned) if part.strip()]
        for part in parts:
            if len(part) <= 90 and not re.search(r"(首先|其次|最后|建议你|你可以.*也可以|总结一下|以下是)", part):
                return part
        if parts:
            return _single_line(parts[0], 70)
        return ""

    def _response_has_invalid_current_time_anchor(self, text: str) -> bool:
        cleaned = str(text or "").strip()
        if not cleaned:
            return False
        now = self._environment_now()
        current_minutes = now.hour * 60 + now.minute
        # 钟点必须与睡意线索同句才算深夜宣言；白天的 11 点只是普通时间点。
        late_clock = r"(?:" + _LATE_CLOCK_INTRO + r"\s*)?(?:晚上)?" + _LATE_CLOCK
        explicit_late_anchor = bool(
            re.search(late_clock + _LATE_CLAIM_GAP + _SLEEP_CUE, cleaned)
            or re.search(_SLEEP_CUE + _LATE_CLAIM_GAP + late_clock, cleaned)
        )
        implicit_late_anchor = bool(re.search(_IMPLICIT_LATE, cleaned))
        late_night = 22 * 60 <= current_minutes or current_minutes <= 90
        if (explicit_late_anchor or implicit_late_anchor) and not late_night:
            return True
        return False

    def _has_open_proactive_awaiting_reply(self, user: dict[str, Any]) -> bool:
        if not isinstance(user, dict):
            return False
        now = _now_ts()
        afterglow = user.get("proactive_afterglow")
        if isinstance(afterglow, dict) and afterglow.get("status") == "awaiting_reply":
            ts = _safe_float(afterglow.get("ts"), 0)
            if not ts or now - ts <= 6 * 3600:
                return True
        for item in reversed(self._action_consequence_items(user)):
            if not isinstance(item, dict) or item.get("status") != "awaiting_reply":
                continue
            ts = _safe_float(item.get("ts"), 0)
            if not ts or now - ts <= 6 * 3600:
                return True
        return False

    def _response_has_false_no_reply_claim(self, text: str, inbound_text: str, user: dict[str, Any]) -> bool:
        cleaned = str(text or "").strip()
        if not cleaned:
            return False
        if not re.search(r"(看你|见你|以为你|还以为你|你).{0,8}(没回|不回|没理|不理|没搭理)|等你回|等你回复|等你消息", cleaned):
            return False
        inbound = str(inbound_text or "").strip()
        if not inbound:
            return False
        if re.search(r"(之前|前面|上一条|上次|刚才那条|我那条)", cleaned) and self._has_open_proactive_awaiting_reply(user):
            return False
        return True

    def _fallback_temporal_or_continuity_confused_reply(
        self,
        inbound_text: str,
        response_text: str,
        *,
        flags: list[str] | None = None,
        user: dict[str, Any] | None = None,
    ) -> str:
        cleaned = _strip_internal_message_blocks(str(response_text or ""), enabled=bool(runtime_persona_setting(self, "enable_framework_error_leak_guard", True))).strip()
        if not cleaned:
            return ""
        active_flags = set(flags or [])
        user = user if isinstance(user, dict) else {}
        if "invalid_current_time_anchor" not in active_flags and self._response_has_invalid_current_time_anchor(cleaned):
            active_flags.add("invalid_current_time_anchor")
        if "false_no_reply_claim" not in active_flags and self._response_has_false_no_reply_claim(cleaned, inbound_text, user):
            active_flags.add("false_no_reply_claim")
        if not active_flags.intersection({"invalid_current_time_anchor", "false_no_reply_claim"}):
            return ""
        last_message = _single_line(_strip_internal_message_blocks(user.get("last_companion_message"), enabled=bool(runtime_persona_setting(self, "enable_framework_error_leak_guard", True))), 260)
        if (
            "false_no_reply_claim" in active_flags
            and self._compact_repeat_text(inbound_text) in {"", "？", "?", "啥", "什么", "shenme"}
            and last_message
            and self._response_has_invalid_current_time_anchor(last_message)
        ):
            return "啊，刚才那句时间感说偏了，是我没接稳你前一句。"
        if "false_no_reply_claim" in active_flags and self._compact_repeat_text(inbound_text) in {"", "？", "?", "啥", "什么", "shenme"}:
            return "啊，我刚才那句是顺口接你问的“有意思的什么”，不是说你没回。"
        # 只删完整的深夜宣言（可带泛称称呼、可带睡意线索），删到该小句结束；
        # 白天的普通时间点不匹配，也不会被截成半截，更不会从句中切走。
        cleaned = re.sub(
            _CLAUSE_BOUNDARY + r"[，,；;、\s]*"
            r"(?:(?:[\u4e00-\u9fffA-Za-z0-9_\-]{1,12})[，,、:：]|(?:主人|宝贝|亲爱的|宝宝|老师))?\s*"
            + _LATE_CLOCK_INTRO + r"?\s*(?:晚上)?" + _LATE_CLOCK + r"(?:了|啦|咯|吧)?"
            + _LATE_CLAIM_GAP + _SLEEP_CUE + r"[^，,。！？!?\n]{0,8}[，,。！？!?]?[？?。！!~～]*",
            "",
            cleaned,
        ).strip()
        # 「时间不早了」这类隐含深夜说法：后面跟着睡意线索时连小句一起删，
        # 否则只删宣言本身，不牵连后面那句话。
        cleaned = re.sub(
            _CLAUSE_BOUNDARY + r"[，,；;、\s]*(?:那[^，,。！？!?；;]{0,12})?" + _IMPLICIT_LATE + r"(?:了|啦|咯)?"
            r"(?:"
            + _LATE_CLAIM_GAP + _SLEEP_CUE + r"[^，,。！？!?\n]{0,8}[，,。！？!?]?"
            r"|(?=[，,。！？!?]|$)[，,。！？!?]?"
            r")"
            r"[？?。！!~～]*",
            "",
            cleaned,
        ).strip()
        cleaned = re.sub(
            r"[，,。！？!?；;、\s]*(?:看你|见你|以为你|还以为你|你).{0,8}(?:没回|不回|没理|不理|没搭理).{0,16}?(?:嘛|啦|了|而已|就)?[，,。！？!?~～]*",
            "",
            cleaned,
        ).strip()
        cleaned = re.sub(r"[，,；;、\s]+$", "", cleaned).strip()
        if cleaned:
            return cleaned
        inbound = str(inbound_text or "").strip()
        if inbound in {"？", "?"}:
            return "啊，我刚才那句没说清楚，是在接你问“有意思的什么”。"
        return "刚才那句我说偏了，重新接你这句。"

    def _simulation_active(self, user: dict[str, Any]) -> bool:
        raw = user.get("simulation_mode")
        return isinstance(raw, dict) and bool(raw.get("active"))

    def _cancel_inbound_conflicting_greeting(
        self,
        user: dict[str, Any],
        *,
        now: float | None = None,
        user_id: str = "",
        trigger_umo: str = "",
    ) -> bool:
        now = now or _now_ts()
        changed = False
        planned_reason = str(user.get("planned_proactive_reason") or "")
        planned_topic = _single_line(user.get("planned_proactive_topic"), 80)
        planned_is_greeting_habit = (
            planned_reason == "habit_awareness"
            and self._habit_topic_is_greeting_like(planned_topic)
            and self._recent_activity_suppresses_habit_greeting(user, now=now, topic=planned_topic)
        )
        if (
            self._inbound_satisfies_greeting(planned_reason, now=now, user=user)
            or planned_is_greeting_habit
        ):
            next_at = _safe_float(user.get("next_proactive_at"), 0)
            if next_at > 0:
                if self._inbound_satisfies_greeting(planned_reason, now=now):
                    changed = self._mark_greeting_satisfied_by_inbound(user, planned_reason) or changed
                self._clear_pending_proactive_plan(user)
                changed = True
        raw_followup = user.get("pending_followup_event")
        if isinstance(raw_followup, dict):
            if raw_followup.get("_cancel_on_inbound") or raw_followup.get("_chain_followup") or raw_followup.get("_opener_followup"):
                user["pending_followup_event"] = {}
                changed = True
            else:
                follow_reason = str(raw_followup.get("reason") or "")
                if self._inbound_satisfies_greeting(follow_reason, now=now, user=user):
                    changed = self._mark_greeting_satisfied_by_inbound(user, follow_reason) or changed
                    user["pending_followup_event"] = {}
                    changed = True
        raw_timer = user.get("llm_timer_event")
        if isinstance(raw_timer, dict):
            timer_reason = str(raw_timer.get("reason") or "")
            if self._inbound_satisfies_greeting(timer_reason, now=now, user=user):
                if _single_line(raw_timer.get("backend"), 40) == "astrbot_cron":
                    queue_cancel = getattr(self, "_queue_official_llm_timer_cancel", None)
                    queued = bool(
                        callable(queue_cancel)
                        and queue_cancel(
                            _single_line(user_id or user.get("user_id"), 120),
                            raw_timer,
                            source_text="用户已在问候时段自然出现",
                            source_origin="inbound_satisfied_greeting",
                            trigger_umo=trigger_umo,
                        )
                    )
                    if queued:
                        changed = self._mark_greeting_satisfied_by_inbound(user, timer_reason) or changed
                        changed = True
                else:
                    changed = self._mark_greeting_satisfied_by_inbound(user, timer_reason) or changed
                    user["llm_timer_event"] = {}
                    changed = True
        return changed

    async def _format_proactive_reply_prompt_sections(
        self,
        event: AstrMessageEvent,
    ) -> list[PromptSection]:
        try:
            user_id = str(event.get_sender_id())
            event_umo = _single_line(getattr(event, "unified_msg_origin", ""), 180)
        except Exception:
            return []
        resolver = getattr(self, "_private_user_id_for_event", None)
        if callable(resolver):
            user_id = resolver(event, user_id)
        consume_suspended = False
        recent_delivery_sections: list[PromptSection] = []
        async with self._data_lock:
            user = dict(self._get_user(user_id))
            raw_suspended = user.get("suspended_proactive")
            if isinstance(raw_suspended, dict) and raw_suspended.get("active") and raw_suspended.get("resume_ready"):
                consume_suspended = True
                current = self._get_user(user_id)
                current["suspended_proactive"] = {}
                self._save_data_sync(sections={"users"})

            last_proactive_text = _single_line(user.get("last_proactive_message"), 500)
            last_proactive_at = _safe_float(user.get("last_proactive_sent_at"), 0)
            last_proactive_action = _single_line(user.get("last_proactive_action"), 80).lower()
            last_proactive_summary = _single_line(user.get("last_proactive_behavior_summary"), 300)
            delivery_umo = _single_line(user.get("last_proactive_delivery_umo") or user.get("umo"), 180)
            consumed_for = _safe_float(user.get("last_proactive_reply_context_consumed_for"), 0)
            max_age = min(
                max(1, runtime_persona_setting(self, "proactive_reply_context_hours", 12)) * 3600,
                30 * 60,
            )
            same_delivery = last_proactive_at > 0 and abs(consumed_for - last_proactive_at) > 0.001
            if (
                last_proactive_text
                and event_umo
                and delivery_umo == event_umo
                and same_delivery
                and 0 <= _now_ts() - last_proactive_at <= max_age
            ):
                recent_delivery_body = (
                    f"你刚才在当前会话主动发了：{last_proactive_text}\n"
                    "这是你自己已经说过并成功外发的内容。用户当前消息很可能在回应它；"
                    "必须直接承认并顺着这条消息接话，不得声称不知道自己发了什么、没看到这条消息或把它当成别人发的。"
                    "如果其中的标题、平台或链接确实有误，简短承认并依据上面的实际原文纠正，不要继续编造来源。"
                )
                recent_delivery_sections.append(
                    prompt_section(
                        key="proactive.recent_delivery",
                        title="刚才你主动发出的消息",
                        source="proactive",
                        content=recent_delivery_body,
                    )
                )
                if "photo_text" in last_proactive_action:
                    image_scene = ""
                    subject_owner = "unknown"
                    snapshot = user.get("last_photo_share_snapshot")
                    if isinstance(snapshot, dict):
                        image_scene = _single_line(snapshot.get("caption"), 260)
                        subject_owner = _normalize_photo_subject_owner(snapshot.get("subject_owner")) or "unknown"
                    if not image_scene and last_proactive_summary:
                        image_scene = _single_line(re.split(r"[:：]", last_proactive_summary, maxsplit=1)[-1], 260)
                    image_subject_body = (
                        (f"图片画面：{image_scene}\n" if image_scene else "")
                        + f"图片发送者：Bot/当前人格；画面主体：{_photo_subject_owner_prompt_label(subject_owner)}\n"
                        + "用户接下来的短句默认是在评价这张图，不是在说用户自己做了图中的事。"
                        "严格按上面的结构化归属理解代词和动作，不要仅凭‘她’猜主体。"
                        "除非用户明确说‘我做了/我弄洒了’，否则不得责怪或安慰用户仿佛事故发生在用户身上。"
                    )
                    recent_delivery_sections.append(
                        prompt_section(
                            key="proactive.recent_media_subject",
                            title="刚才主动图片的主客体",
                            source="proactive",
                            content=image_subject_body,
                        )
                    )
                current = self._get_user(user_id)
                current["last_proactive_reply_context_consumed_for"] = last_proactive_at
                self._save_data_sync(sections={"users"})

        suspended = user.get("suspended_proactive")
        if isinstance(suspended, dict) and suspended.get("active") and (
            suspended.get("resume_ready") or consume_suspended
        ):
            opener = _single_line(suspended.get("opener_text"), 60) or f"{runtime_persona_setting(self, 'default_nickname', '你')}……"
            hidden_reason = _single_line(suspended.get("reason"), 40)
            hidden_action = _single_line(suspended.get("action"), 32)
            hidden_motive = _single_line(suspended.get("motive"), 120)
            hidden_summary = _single_line(suspended.get("summary"), 60)
            schedule_context = self._format_schedule_context_for_prompt()
            body = (
                f"你刚才主动私聊时,只先发了一句：{opener}\n"
                "你真正想说的后半句还没发出去,现在用户回头了。\n"
                f"当时主动原因：{hidden_reason or 'check_in'}\n"
                f"当时原本想用的主动行为：{hidden_action or 'message'}"
                + (f"（{hidden_summary}）\n" if hidden_summary else "\n")
                + (f"当时心里那点念头：{hidden_motive}\n" if hidden_motive else "")
                + "请像终于等到对方抬头一样,自然把后半句接上。不要解释“我刚才故意只叫你一声”,也不要突然像全新开场。\n"
                + "如果用户现在只是“怎么了”“？”“在吗”这类短句,就把它理解成他终于回头了,顺着那一下接话。\n"
                + "可以参考当前状态和今天的生活背景,但只体现在语气和接话方式里；别把日期、状态或日程当汇报念出来。\n"
                + f"当前/附近日程参考：{schedule_context or '无当前日程'}\n"
                + f"今天预设的生活线索：{self._format_story_plan_for_prompt()}"
            )
            section = prompt_section(
                key="proactive.suspended_opener",
                title="刚才悬着的话头",
                source="proactive",
                content=body,
            )
            return [section]

        return recent_delivery_sections

    async def _format_proactive_reply_context(
        self,
        event: AstrMessageEvent,
    ) -> str:
        sections = await self._format_proactive_reply_prompt_sections(event)
        return "\n".join(
            _render_conversation_section_labeled(section)
            for section in sections
        )

    def _response_review_drop_marker(self) -> str:
        return "__PRIVATE_COMPANION_DROP_DUPLICATE__"

    def _is_response_review_drop_marker(self, text: Any) -> bool:
        raw = str(text or "").strip()
        if not raw:
            return False
        if raw == self._response_review_drop_marker():
            return True
        compact = re.sub(r"[\s<>\[\]{}_'\"`“”‘’：:。.!！?？-]+", "", raw).upper()
        return compact in {"PRIVATECOMPANIONDROPDUPLICATE", "DROPDUPLICATE", "丢弃重复", "取消重复"}

    def _text_is_near_duplicate_reply(self, text: str, recent_text: str) -> bool:
        current = self._compact_repeat_text(text)
        recent = self._compact_repeat_text(recent_text)
        if len(current) < 8 or len(recent) < 8:
            return False
        if current == recent:
            return True
        short, long = (current, recent) if len(current) <= len(recent) else (recent, current)
        return len(short) >= 12 and short in long and len(short) / max(1, len(long)) >= 0.82

    def _should_drop_duplicate_reply_text(
        self,
        user: dict[str, Any],
        inbound_text: str,
        response_text: str,
    ) -> tuple[bool, str]:
        if not isinstance(user, dict):
            return False, ""
        if self._inbound_explicitly_requests_repeat(inbound_text):
            return False, ""
        visible = _single_line(_strip_internal_message_blocks(response_text, enabled=bool(runtime_persona_setting(self, "enable_framework_error_leak_guard", True))), 500)
        last_message = _single_line(user.get("last_companion_message"), 500)
        if not visible or not last_message:
            return False, ""
        last_at = _safe_float(user.get("last_companion_message_at"), 0) or _safe_float(user.get("last_sent"), 0)
        if last_at > 0 and _now_ts() - last_at > 30 * 60:
            return False, ""
        if self._text_is_near_duplicate_reply(visible, last_message):
            return True, "最终回复与上一条 Bot 消息几乎相同"
        return False, ""

    def _response_review_flags(self, text: str, user: dict[str, Any], *, inbound_text: str = "") -> list[str]:
        cleaned = re.sub(r"\[\[PCTTS:[^\]]*\]\]", "", str(text or "")).strip()
        flags: list[str] = []
        if not cleaned:
            return flags
        if "```" in cleaned:
            return flags
        intent_profile = user.get("intent_profile") if isinstance(user.get("intent_profile"), dict) else {}
        is_help = str(intent_profile.get("intent") or "") == "help"
        length_limit = runtime_persona_setting(self, "response_review_max_chars", 260) * (2 if is_help else 1)
        if len(cleaned) > length_limit:
            flags.append("too_long")
        if not is_help and self._is_short_casual_inbound_for_review(inbound_text, user):
            casual_limit = self._casual_reply_review_limit(inbound_text)
            sentence_count = len(re.findall(r"[。！？!?…]+", cleaned))
            paragraph_count = len([part for part in re.split(r"\n+", cleaned) if part.strip()])
            advice_count = len(re.findall(r"(记得|别忘|注意|小心|可以|要不要|最好|建议|带伞|喝点|早点|路上)", cleaned))
            if len(cleaned) > casual_limit or sentence_count >= 4 or paragraph_count >= 2:
                flags.append("casual_overexplained")
            inbound_weather = re.search(r"(雨|下雨|变天|天气|降温|冷|热|风)", inbound_text)
            reply_weather = re.search(r"(雨|天气|伞|降温|冷|热|风|外面|出门)", cleaned)
            if inbound_weather and reply_weather and (len(cleaned) > min(casual_limit, 130) or advice_count >= 2):
                flags.append("weather_overexplained")
        if re.search(r"^(好的|当然|没问题|我理解|总结一下|以下是|首先|其次|最后)[，,：:]", cleaned):
            flags.append("assistant_tone")
        if re.search(r"(作为.*助手|AI|模型|系统|提示词|插件|后台|根据.*信息|我会从.*角度)", cleaned, re.IGNORECASE):
            flags.append("meta_or_assistant")
        if not is_help and re.search(r"^\s*(?:[-*]|\d+[.、])\s+", cleaned, re.MULTILINE) and len(cleaned) < 900:
            flags.append("over_structured")
        if re.search(r"(能量\s*\d+|关系站位|状态机|内部规划|用户意图|表达学习|陪伴记忆|本地陪伴画像)", cleaned):
            flags.append("leaks_internal")
        if self._response_has_invalid_current_time_anchor(cleaned):
            flags.append("invalid_current_time_anchor")
        if self._response_has_false_no_reply_claim(cleaned, inbound_text, user):
            flags.append("false_no_reply_claim")
        correction = self._active_private_fact_correction(user, inbound_text)
        if self._looks_like_private_fact_correction(inbound_text):
            flags.append("fact_attribution_after_correction")
        claims_user_prior_action = self._response_claims_user_prior_action(cleaned, user)
        inbound_claims_ownership = bool(
            re.search(r"(?:我|你|他|她|它|谁)[^。！？!?\n]{0,18}(?:上次|之前|先|说|提|想|拿|问|做|告诉|推荐|诱惑)", inbound_text)
        )
        if claims_user_prior_action and correction:
            flags.append("fact_attribution_after_correction")
        elif claims_user_prior_action and not inbound_claims_ownership and len(self._compact_repeat_text(inbound_text)) <= 32:
            flags.append("unverified_fact_attribution")
        if self._response_reverses_recent_proactive_media_ownership(cleaned, user, inbound_text):
            flags.append("proactive_media_ownership_reversal")
        if self._expression_style_review_enabled():
            flags.extend(self._expression_review_flags(cleaned, user))
        signature = self._proactive_topic_signature(cleaned)
        if runtime_persona_setting(self, "enable_passive_topic_suppression", True):
            for item in self._cleanup_recent_passive_topics(user):
                if self._topic_signature_similar(signature, str(item.get("signature") or "")):
                    flags.append("repeated_topic")
                    break
        last_message = _single_line(user.get("last_companion_message"), 300)
        last_sent = _safe_float(user.get("last_companion_message_at"), 0) or _safe_float(user.get("last_sent"), 0)
        if (
            last_message
            and not self._inbound_explicitly_requests_repeat(inbound_text)
            and self._text_repeats_recent_message(cleaned, last_message)
        ):
            if not last_sent or _now_ts() - last_sent <= runtime_persona_setting(
                self,
                "proactive_reply_context_hours",
                12,
            ) * 3600:
                flags.append("repeats_last_bot_message")
        return list(dict.fromkeys(flags))

    def _expression_review_flags(self, cleaned: str, user: dict[str, Any]) -> list[str]:
        flags: list[str] = []
        if re.search(r"[，,]\s*[。！？!?…~～]|[。！？!?]\s*[，,]|[，,]{2,}|[。！？!?]{3,}", cleaned):
            flags.append("unnatural_punctuation")
        if re.search(r"\b[A-Za-z]{2,}\b\s*[。！？!?]\s*\b[A-Za-z]{1,4}\b\s*[。！？!?]", cleaned):
            flags.append("unnatural_punctuation")
        if len(cleaned) <= 260:
            punct_count = len(re.findall(r"[，,。！？!?…~～]", cleaned))
            if punct_count >= max(7, len(cleaned) // 10):
                flags.append("expression_overfit")
        profile = user.get("expression_profile") if isinstance(user.get("expression_profile"), dict) else {}
        phrases = self._expression_profile_phrases(profile, limit=8)
        compact_reply = self._compact_repeat_text(cleaned)
        copied = 0
        for phrase in phrases:
            compact_phrase = self._compact_repeat_text(phrase)
            if len(compact_phrase) >= 8 and compact_phrase in compact_reply:
                copied += 1
        if copied >= 2 or (copied >= 1 and self._expression_learning_mode() == "aggressive"):
            flags.append("copied_user_expression_sample")
        if re.search(r"(学你|像你说话|模仿你|你的口癖|你的语气)", cleaned):
            flags.append("leaks_internal")
        return flags

    @staticmethod
    def _compact_repeat_text(text: str) -> str:
        return re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]+", "", str(text or "")).lower()

    def _text_repeats_recent_message(self, text: str, recent_text: str) -> bool:
        current = self._compact_repeat_text(text)
        recent = self._compact_repeat_text(recent_text)
        if len(current) < 8 or len(recent) < 8:
            return False
        if current in recent or recent in current:
            return True
        current_tokens = set(re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9_]{3,}", text))
        recent_tokens = set(re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9_]{3,}", recent_text))
        stopwords = {
            "刚才", "现在", "今天", "这个", "那个", "一下", "一点", "有点", "还有",
            "什么", "安排", "用户", "你呢", "我呢", "就是", "已经", "容易",
        }
        current_tokens = {token for token in current_tokens if token not in stopwords}
        recent_tokens = {token for token in recent_tokens if token not in stopwords}
        if current_tokens and recent_tokens:
            common = current_tokens & recent_tokens
            if len(common) >= 3 and len(common) / max(1, min(len(current_tokens), len(recent_tokens))) >= 0.45:
                return True
        current_sig = self._proactive_topic_signature(text)
        recent_sig = self._proactive_topic_signature(recent_text)
        if current_sig and recent_sig and current_sig == recent_sig:
            shared_chunks = 0
            for idx in range(max(0, len(current) - 3)):
                chunk = current[idx : idx + 4]
                if chunk and chunk in recent:
                    shared_chunks += 1
                    if shared_chunks >= 2:
                        return True
        return False

