# -*- coding: utf-8 -*-
"""人格对齐/模型评审域。

由 tools/split_mixin_domain.py 从 proactive_engine.py 机械抽取（20 个方法 + 0 个模块级名字 + 0 个类级赋值 / 1048 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveEngineMixin）。
"""
from __future__ import annotations

import hashlib
import time
from .conversation_prompt_section import PromptRenderMode, PromptSection, prompt_section, render_prompt_sections
from .helpers import _now_ts, _safe_float, _safe_int, _single_line, _today_key
from .memory_context_policy import core_memory_usage_contract_section
from .persona_config import runtime_persona_setting
from .proactive_engine_shared import _persona_provider_id
from .proactive_routes import PROACTIVE_ROUTE_REGISTRY
from datetime import datetime
from typing import Any

from .logging_util import get_module_logger

logger = get_module_logger(__name__)



class ProactiveEnginePersonaMixin:
    """人格对齐/模型评审域（从 ProactiveEngineMixin 拆出）。"""


    def _proactive_source_feedback_modifier(self, user: dict[str, Any], source: str) -> float:
        """Bias candidates using reply quality for their own source, not global silence."""
        normalized = _single_line(source, 40) or "unknown"
        feedback = user.get("proactive_source_feedback") if isinstance(user, dict) else None
        bucket = feedback.get(normalized) if isinstance(feedback, dict) else None
        if not isinstance(bucket, dict):
            return 0.0
        sent = _safe_float(bucket.get("weighted_sent"), _safe_float(bucket.get("sent"), 0.0), 0.0)
        if sent < 2.0:
            return 0.0
        replied = min(sent, _safe_float(bucket.get("weighted_replied"), _safe_float(bucket.get("replied"), 0.0), 0.0))
        positive = min(replied, _safe_float(bucket.get("weighted_positive"), _safe_float(bucket.get("positive"), 0.0), 0.0))
        negative = min(replied, _safe_float(bucket.get("weighted_negative"), _safe_float(bucket.get("negative"), 0.0), 0.0))
        reply_rate = replied / max(1, sent)
        feedback_rate = (positive - negative) / max(1, sent)
        # Keep the learnt effect bounded; the route and hard gates remain authoritative.
        return max(-0.18, min(0.18, (reply_rate - 0.35) * 0.24 + feedback_rate * 0.08))

    def _proactive_persona_alignment(
        self,
        user: dict[str, Any],
        *,
        reason: str,
        action: str,
        motive: str,
        topic: str = "",
        source: str = "",
        now: float | None = None,
    ) -> dict[str, Any]:
        role = self._private_user_role(user)
        normalized_reason = str(reason or "check_in")
        normalized_action = str(action or "message").strip() or "message"
        normalized_motive = self._normalize_internal_motive_text(_single_line(motive, 180))
        normalized_topic = _single_line(topic, 80)
        normalized_source = _single_line(source, 40)
        text = f"{normalized_reason} {normalized_action} {normalized_topic} {normalized_motive}"
        profile = self._persona_action_profile()
        score = 0.66
        notes: list[str] = []
        blocker = False

        def note(text_value: str) -> None:
            clean = _single_line(text_value, 60)
            if clean and clean not in notes:
                notes.append(clean)

        intimate = (
            self._proactive_reason_is_intimate(normalized_reason)
            or self._proactive_action_is_intimate(normalized_action)
            or self._proactive_text_is_intimate(normalized_reason, normalized_action, normalized_motive, normalized_topic)
        )
        if role == "friend":
            score += 0.02
            if self._friend_sensitive_proactive_reason(normalized_reason) or self._friend_sensitive_proactive_action(normalized_action):
                blocker = True
                score -= 0.45
                note("次要用户关系不适合这个主动来源/能力")
            if intimate:
                score -= 0.22
                note("次要用户关系下亲密度偏高")
            if self._is_vague_seek_user_motive(normalized_reason, normalized_action, normalized_motive, normalized_topic):
                score -= 0.12
                note("次要用户关系下动机太像索取回应")
        else:
            if normalized_reason == "special_day_greeting":
                ritual_markers = ("节日", "仪式", "纪念", "浪漫", "庆祝", "情人", "七夕")
                if any(marker in str(self._get_default_persona_prompt() or "") for marker in ritual_markers):
                    score += 0.07
                    note("人格对节日/纪念性表达有承载空间")
                else:
                    score -= 0.04
                    note("人格不偏节日仪式，表达应收成平常口吻")
            elif normalized_reason == "insomnia_night" and not (profile.get("clingy") or profile.get("voicey")):
                score -= 0.03
                note("人格主动温度偏低，失眠关怀只留很短一句")
            if intimate and (profile.get("clingy") or profile.get("voicey")):
                score += 0.07
                note("亲近型人格可承载这个主动")
            if self._is_vague_seek_user_motive(normalized_reason, normalized_action, normalized_motive, normalized_topic):
                score -= 0.07
                note("动机略空,需要更具体的生活钩子")

        action_parts = {part.strip() for part in normalized_action.split("+") if part.strip()}
        if "screen_peek" in action_parts:
            if profile.get("observant"):
                score += 0.07
                note("观察型人格适合轻观察")
            else:
                score -= 0.05
                note("观察能力和人格标记不强")
        if "photo_text" in action_parts:
            if profile.get("visual"):
                score += 0.08
                note("视觉表达贴合人格")
            else:
                score -= 0.04
                note("图片表达缺少人格支撑")
        if "voice" in action_parts:
            if profile.get("voicey"):
                score += 0.08
                note("语音表达贴合人格")
            else:
                score -= 0.04
                note("语音表达缺少人格支撑")
        if "poke" in action_parts:
            if profile.get("playful") or profile.get("clingy"):
                score += 0.06
                note("轻互动贴合俏皮/依恋人格")
            else:
                score -= 0.06
                note("戳一戳不像当前人格的自然动作")

        if normalized_reason in {"activity_share", "diary_share", "background_schedule"}:
            if profile.get("playful") or profile.get("visual") or profile.get("observant"):
                score += 0.04
                note("轻分享和人格气质相容")
        if not normalized_topic and not normalized_motive and normalized_reason in {"check_in", "quiet_care", "state_share"}:
            score -= 0.08
            note("念头缺少具体来源")

        leak_tokens = ("模型", "插件", "action", "模块", "接口", "提示词", "LLM", "prompt", "后台任务", "系统调度")
        if any(token in text for token in leak_tokens):
            score -= 0.28
            blocker = True
            note("内部机制泄露风险")
        worldview_mode = str(
            runtime_persona_setting(self, "worldview_adaptation_mode", "auto") or "auto"
        )
        if worldview_mode in {"fantasy", "sci_fi", "custom"} and any(token in text for token in ("现实网络", "现实设备", "影响现实", "控制设备")):
            score -= 0.25
            blocker = True
            note("世界观边界风险")

        mode = self._current_emotion_gate_mode(user, now=now) or self._current_relationship_gate_mode(user, now=now)
        if mode in {"careful", "hurt", "refusing", "backoff"} and intimate and normalized_source != "timer":
            score -= 0.22
            if mode in {"refusing", "backoff"}:
                blocker = True
            note(f"关系状态 {mode} 不适合亲密主动")

        score = max(0.0, min(1.0, score))
        if not notes:
            note("动机、动作和当前关系基本贴合")
        return {
            "score": score,
            "note": "；".join(notes[:3]),
            "blocker": blocker,
        }

    def _maslow_motivation_profile(
        self,
        user: dict[str, Any],
        *,
        reason: str,
        action: str,
        motive: str,
        topic: str = "",
        source: str = "",
        semantic_kind: str = "",
        anchor_type: str = "",
        anchor_score: float = 0.5,
        evidence_text: str = "",
    ) -> dict[str, Any]:
        text = f"{reason} {action} {topic} {motive} {source} {semantic_kind} {anchor_type} {evidence_text}"
        action_parts = {part.strip() for part in str(action or "").split("+") if part.strip()}
        ignored_streak = _safe_int(user.get("ignored_streak"), 0, 0)

        def has_any(tokens: tuple[str, ...]) -> bool:
            return any(token in text for token in tokens)

        layer = "belonging"
        drive = "维持连接"
        score_bias = 0.02
        pressure_bias = 0.0

        if reason == "insomnia_night" or has_any(("困", "睡", "熬夜", "失眠", "休息", "生病", "头疼", "不舒服", "饿", "胃口", "吃点")):
            layer = "physiological"
            drive = "状态照料"
            score_bias = 0.04
            pressure_bias = -0.02
        elif action_parts & {"screen_peek"} or ignored_streak > 0 or has_any(("边界", "别回", "不用回", "忙", "别打扰", "沉默", "未回复")):
            layer = "safety"
            drive = "确认边界"
            score_bias = -0.02 if ignored_streak >= 2 else 0.01
            pressure_bias = 0.04 + min(0.04, ignored_streak * 0.015)
        elif reason == "important_date_share" or has_any(("生日", "纪念", "考试", "面试", "项目", "成绩", "努力", "鼓励", "夸", "辛苦")):
            layer = "esteem"
            drive = "认可支持"
            score_bias = 0.06
            pressure_bias = -0.03
        elif has_any(("意义", "存在", "世界观", "宇宙", "星空", "命运", "现实边界", "精神", "信念")):
            layer = "meaning"
            drive = "意义连接"
            score_bias = 0.03
            pressure_bias = -0.01
        elif reason in {"creative_share", "diary_share", "news_share", "web_exploration_share", "bili_video_share", "activity_share"} or has_any(
            ("学习", "创作", "灵感", "作品", "研究", "新闻", "搜索", "阅读", "视频", "日记", "见闻")
        ):
            layer = "growth"
            drive = "探索成长"
            score_bias = 0.03
            pressure_bias = -0.02 if anchor_score >= 0.5 else 0.02
        elif source in {"pending_followup", "followup"} or semantic_kind == "continuation" or anchor_type == "recent_context":
            layer = "belonging"
            drive = "续接共同话题"
            score_bias = 0.07
            pressure_bias = -0.06
        elif semantic_kind in {"greeting", "light_touch"} or reason in {"morning_greeting", "noon_greeting", "evening_greeting"}:
            layer = "belonging"
            drive = "轻量陪伴仪式"
            score_bias = 0.03
            pressure_bias = -0.02
        elif reason in {"quiet_care", "check_in"} and anchor_score < 0.45:
            layer = "belonging"
            drive = "无明确由头的关心"
            score_bias = -0.03
            pressure_bias = 0.03

        if action_parts & {"poke", "voice"}:
            pressure_bias += 0.02
        if self._private_user_role(user) == "friend" and layer in {"belonging", "esteem"}:
            score_bias -= 0.02
            pressure_bias += 0.02

        labels = {
            "physiological": "状态",
            "safety": "安全",
            "belonging": "归属",
            "esteem": "尊重",
            "growth": "成长",
            "meaning": "意义",
        }
        return {
            "layer": layer,
            "drive": drive,
            "score_bias": max(-0.12, min(0.12, score_bias)),
            "pressure_bias": max(-0.12, min(0.12, pressure_bias)),
            "note": f"{labels.get(layer, layer)}/{drive}",
        }

    def _proactive_semantic_evidence_text(self, value: Any, *, limit: int = 260) -> str:
        parts: list[str] = []

        def collect(item: Any, depth: int = 0) -> None:
            if len(parts) >= 8 or depth > 2:
                return
            if isinstance(item, dict):
                priority = (
                    "title",
                    "topic",
                    "summary",
                    "text",
                    "content",
                    "reason",
                    "why",
                    "scene",
                    "impulse",
                    "tone",
                    "group_name",
                    "sender_name",
                    "share_decision",
                    "share_tone",
                    "share_boundary",
                )
                for key in priority:
                    if key in item:
                        collect(item.get(key), depth + 1)
                if len(parts) < 4:
                    for key, nested in list(item.items())[:8]:
                        if key not in priority:
                            collect(nested, depth + 1)
            elif isinstance(item, list):
                for nested in item[:5]:
                    collect(nested, depth + 1)
            else:
                text = _single_line(item, 80)
                if text and text not in parts:
                    parts.append(text)

        collect(value)
        return _single_line(" ".join(parts), limit)

    def _proactive_semantic_chain_text(self, chain: list[dict[str, Any]] | None) -> str:
        if not isinstance(chain, list):
            return ""
        parts: list[str] = []
        for step in chain[:4]:
            if not isinstance(step, dict):
                continue
            bits = [
                _single_line(step.get("kind"), 30),
                _single_line(step.get("reason"), 40),
                _single_line(step.get("topic"), 60),
                _single_line(step.get("motive"), 80),
                _single_line(step.get("tone"), 40),
            ]
            line = _single_line(" ".join(bit for bit in bits if bit), 120)
            if line:
                parts.append(line)
        return _single_line(" ".join(parts), 240)

    def _planned_proactive_model_judge_signature(self, user: dict[str, Any]) -> str:
        persona = _single_line(str(self._get_default_persona_prompt() or ""), 800)
        worldview = _single_line(str(self._format_worldview_adaptation_prompt() or ""), 400)
        interaction = user.get("current_interaction") if isinstance(user.get("current_interaction"), dict) else {}
        contact = user.get("contact_preference") if isinstance(user.get("contact_preference"), dict) else {}
        semantics = self._planned_proactive_semantics(user)
        ignored = _safe_int(user.get("ignored_streak"), 0, 0)
        parts = [
            self._private_user_role(user),
            self._normalize_legacy_proactive_text(user.get("planned_proactive_source"), limit=40),
            self._normalize_legacy_proactive_text(user.get("planned_proactive_reason"), limit=40),
            self._normalize_legacy_proactive_text(user.get("planned_proactive_action"), limit=40),
            _single_line(user.get("planned_proactive_topic"), 80).casefold(),
            _single_line(user.get("planned_proactive_motive"), 180).casefold(),
            _single_line(semantics.get("kind"), 40),
            _single_line(semantics.get("anchor_type"), 40),
            _single_line(semantics.get("need_layer"), 40),
            _single_line(semantics.get("need_drive"), 80),
            f"semantic={int(_safe_float(semantics.get('score'), 0.5) * 5)}",
            f"pressure={int(_safe_float(semantics.get('pressure'), 0.4) * 5)}",
            f"risk={int(_safe_float(semantics.get('risk'), 0.0) * 5)}",
            interaction.get("expression_band") or "",
            contact.get("mode") or "",
            "ignored=0" if ignored <= 0 else "ignored=1" if ignored == 1 else "ignored=2+",
            _single_line(user.get("last_user_message"), 160).casefold(),
            f"last_user_at={int(_safe_float(user.get('last_user_message_at'), 0))}",
            persona,
            worldview,
        ]
        raw = "\n".join(_single_line(part, 1000) for part in parts)
        return hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()

    def _cached_proactive_model_judgement(
        self,
        user: dict[str, Any],
        *,
        signature: str,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        if not signature:
            return None
        check_now = _now_ts() if now is None else now
        ttl = max(
            5,
            _safe_int(
                runtime_persona_setting(self, "proactive_persona_judge_cache_minutes", 180),
                180,
                5,
                720,
            ),
        ) * 60
        cache = user.get("proactive_persona_judge_cache")
        if isinstance(cache, dict):
            entry = cache.get(signature)
            if isinstance(entry, dict):
                judged_at = _safe_float(entry.get("judged_at"), 0)
                cached = entry.get("result")
                if judged_at > 0 and check_now - judged_at <= ttl and isinstance(cached, dict):
                    return dict(cached)
        if _single_line(user.get("planned_proactive_model_judge_signature"), 80) == signature:
            judged_at = _safe_float(user.get("planned_proactive_model_judge_at"), 0)
            cached = user.get("planned_proactive_model_judge_result")
            if judged_at > 0 and check_now - judged_at <= ttl and isinstance(cached, dict):
                return dict(cached)
        return None

    def _proactive_persona_judge_calls_today(self) -> int:
        usage = self.data.get("token_usage") if isinstance(getattr(self, "data", None), dict) else {}
        by_day_task = usage.get("by_day_task") if isinstance(usage, dict) else {}
        today_tasks = by_day_task.get(_today_key()) if isinstance(by_day_task, dict) else {}
        task = today_tasks.get("proactive_persona_judge") if isinstance(today_tasks, dict) else {}
        return _safe_int(task.get("calls"), 0, 0) if isinstance(task, dict) else 0

    def _local_proactive_persona_judgement(self, user: dict[str, Any]) -> dict[str, Any] | None:
        if self._private_user_role(user) == "friend" or _safe_int(user.get("ignored_streak"), 0, 0) > 0:
            return None
        semantics = self._planned_proactive_semantics(user)
        alignment = self._planned_proactive_persona_alignment(user)
        if (
            not semantics.get("blocker")
            and not alignment.get("blocker")
            and _safe_float(semantics.get("score"), 0.5) >= 0.78
            and _safe_float(semantics.get("pressure"), 0.4) <= 0.30
            and _safe_float(semantics.get("risk"), 0.0) <= 0.10
            and _safe_float(alignment.get("score"), 0.55) >= 0.78
        ):
            return {"decision": "send", "score": 90, "reason": "本地高置信人格判定", "local": True}
        return None

    def _normalize_proactive_model_judgement(self, payload: dict[str, Any] | None) -> dict[str, Any] | None:
        if not isinstance(payload, dict):
            return None
        decision = str(payload.get("decision") or "").strip().lower()
        if decision not in {"send", "rewrite", "defer", "drop"}:
            return None
        score = _safe_int(payload.get("score"), 0, 0, 100)
        threshold_getter = getattr(self, "_effective_proactive_persona_judge_send_threshold", None)
        threshold = (
            threshold_getter()
            if callable(threshold_getter)
            else _safe_int(
                runtime_persona_setting(self, "proactive_persona_judge_send_threshold", 62),
                62,
                0,
                100,
            )
        )
        reason = self._normalize_legacy_proactive_text(payload.get("reason"), limit=140) or "模型人格判定"
        if decision == "send" and score > 0 and score < threshold:
            reason = self._normalize_legacy_proactive_text(
                f"{reason}；分数低于建议阈值，正文生成时收敛",
                limit=140,
            )
        result = {
            "decision": decision,
            "score": score,
            "reason": reason,
            "delay_minutes": _safe_int(payload.get("delay_minutes"), 90, 20, 360),
            "reason_field": self._normalize_legacy_proactive_text(payload.get("planned_reason") or payload.get("reason_field"), limit=40),
            "action": self._normalize_legacy_proactive_text(payload.get("action"), limit=40),
            "topic": _single_line(payload.get("topic"), 80),
            "motive": self._normalize_internal_motive_text(_single_line(payload.get("motive"), 180)),
        }
        if decision == "rewrite" and not any(
            _single_line(result.get(key), 180)
            for key in ("reason_field", "action", "topic", "motive")
        ):
            result["decision"] = "send"
            result["reason"] = self._normalize_legacy_proactive_text(
                f"{reason}；未给出可应用的计划字段，交给正文生成收敛",
                limit=140,
            )
        return result

    def _proactive_model_judgement_requires_hard_block(
        self,
        user: dict[str, Any],
        judgement: dict[str, Any],
    ) -> bool:
        decision = _single_line(judgement.get("decision"), 20).lower()
        if decision not in {"defer", "drop"}:
            return False
        semantics = self._planned_proactive_semantics(user)
        alignment = self._planned_proactive_persona_alignment(user)
        if (
            bool(semantics.get("blocker"))
            or _safe_float(semantics.get("risk"), 0.0) >= 0.70
            or bool(alignment.get("blocker"))
        ):
            return True
        note = _single_line(judgement.get("reason"), 180)
        hard_markers = (
            "用户明确拒绝",
            "对方明确拒绝",
            "不要再发",
            "不想收到",
            "免打扰",
            "用户明确休息",
            "对方明确休息",
            "用户正在睡",
            "隐私泄露",
            "关系越界",
            "串用户",
            "其他用户专属",
            "内部机制",
            "工具名",
            "插件",
            "提示词",
            "后台任务",
            "系统任务",
            "世界观边界",
            "无真实来源",
            "捏造事实",
            "虚构事实",
            "不安全",
        )
        return any(marker in note for marker in hard_markers)

    def _apply_proactive_model_judgement_policy(
        self,
        user: dict[str, Any],
        judgement: dict[str, Any],
    ) -> dict[str, Any]:
        result = dict(judgement)
        decision = _single_line(result.get("decision"), 20).lower()
        if decision not in {"defer", "drop"}:
            return result
        if self._proactive_model_judgement_requires_hard_block(user, result):
            result["hard"] = True
            return result
        original_reason = _single_line(result.get("reason"), 120) or "质量建议"
        has_rewrite = any(
            _single_line(result.get(key), 180)
            for key in ("reason_field", "action", "topic", "motive")
        )
        result["advisory_decision"] = decision
        result["decision"] = "rewrite" if has_rewrite else "send"
        result["delay_minutes"] = 0
        result["reason"] = self._normalize_legacy_proactive_text(
            f"软质量建议已交给正文生成：{original_reason}",
            limit=140,
        )
        return result

    def _proactive_source_model_hint_section(
        self,
        user: dict[str, Any],
    ) -> PromptSection | None:
        source = self._normalize_legacy_proactive_text(user.get("planned_proactive_source"), limit=40)
        reason = self._normalize_legacy_proactive_text(user.get("planned_proactive_reason"), limit=40)

        def authored_hint(key: str, title: str, lines: tuple[str, ...]) -> PromptSection:
            return prompt_section(
                key=key,
                title=title,
                source="proactive_engine",
                content="\n".join(lines),
            )

        if source in {"pending_followup", "followup"}:
            return authored_hint(
                "proactive.source.followup",
                "来源专项改写：补一句",
                (
                    "这类来源不是重新开话题，而是前一句还有一个具体点没落地。",
                    "如果现在的 topic/motive 只是“再说一句/补一句/轻轻放一句/顺着那股劲”，优先 rewrite，不要直接 send。",
                    "rewrite 后必须补出一个实质点：提醒、遗漏信息、没说完的小重点，或前一句里还挂着的小事。",
                    "情绪可以很轻，但只允许当底色：一点点不甘心、惦记，或认真；不要把情绪本身写成内容。",
                ),
            )
        if source == "daily_greeting" or reason in {"morning_greeting", "noon_greeting", "evening_greeting"}:
            return authored_hint(
                "proactive.source.greeting",
                "来源专项改写：日常招呼",
                (
                    "这类来源的价值在“当天这个时段自然出现的一次招呼”，不是机械签到，也不是在任何空档里补一句模板问候。",
                    "不要因为今天先发过其他话题就默认 morning_greeting 已经完成；应判断此前正文里是否真的自然说过早安或明确打过晨间招呼。已经说过就不重复，尚未说过且仍在合适窗口内则可以顺着当前生活片段自然开口。",
                    "用户先自然来聊不等于 Bot 已经醒来，也不必因此取消首次起床问候；但若双方已经在早晨连续聊了一阵，就避免突兀地补正式早安。",
                    "noon_greeting/evening_greeting 仍要避开刚刚发生的来回互动。",
                    "rewrite 后必须落在当前时段的一个小片段上：早晨刚醒/洗漱/出门前，中午刚吃完/发懒/准备午休，晚上收尾/回家/窝下来。",
                    "最终效果要像这个时段第一次顺手冒头，不像模板化签到，也不像聊到一半又补来的礼貌问候。",
                ),
            )
        if source == "random":
            return authored_hint(
                "proactive.source.random",
                "来源专项改写：轻微想念",
                (
                    "规则层已经判断这次“想来找一下”成立，但正文不能只剩关系姿态。",
                    "如果现在的 topic/motive 只有“想你了/来看看你/在不在/忙不忙”，优先 rewrite。",
                    "rewrite 后要补出一个很小的具体钩子：当前时段的小片段、刚冒出来的小想法，或一句能自然开口的话。",
                    "这类来源只能轻，不要写成索取回应，也不要写成无缘由的空泛表白。",
                ),
            )
        if source == "state" or reason == "state_share":
            return authored_hint(
                "proactive.source.state",
                "来源专项改写：身体小需求",
                (
                    "这类来源不是汇报状态，而是身体上的那点小事带出来的话头。",
                    "如果现在的 topic/motive 像“我饿了/我累了/状态不好”，优先 rewrite。",
                    "rewrite 后要把它改成一个具体可聊的小需求，比如吃什么、要不要垫一口、想不想来点甜的；不要写成状态播报。",
                    "语气要自然，不要像健康汇报、撒娇表演或硬找人陪。",
                ),
            )
        if source == "mobile_location" or _single_line(user.get("planned_mobile_location_event_type"), 32):
            event_type = _single_line(user.get("planned_mobile_location_event_type"), 32)
            if event_type == "home_arrival":
                return authored_hint(
                    "proactive.source.home_arrival",
                    "来源专项改写：回家后的自然开口",
                    (
                        "用户刚进入已标记的家，允许自然提到“刚到家/回来了/先歇一会儿”这类生活片段。",
                        "不要提定位、坐标、手机、设备、监听或内部判断，也不要写成系统通知；像你自己顺手想到后说一句。",
                        "优先一句短而具体的话，避免连续追问“到家了吗/在家吗”。",
                    ),
                )
            return authored_hint(
                "proactive.source.location",
                "来源专项改写：位置转场",
                (
                    "只能把已确认的地点变化当作轻量生活背景，不暴露定位或设备细节。",
                ),
            )
        return None

    def _format_proactive_source_model_hint(self, user: dict[str, Any]) -> str:
        section = self._proactive_source_model_hint_section(user)
        return (
            render_prompt_sections([section], mode=PromptRenderMode.LABELED_BLOCK)
            if section is not None
            else ""
        )

    @staticmethod
    def _format_proactive_user_message_freshness(
        user: dict[str, Any],
        *,
        now: float | None = None,
    ) -> str:
        check_now = _now_ts() if now is None else now
        current_time = datetime.fromtimestamp(check_now).astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
        last_user_at = _safe_float(user.get("last_user_message_at"), 0)
        if last_user_at <= 0:
            return "\n".join(
                (
                    f"- 当前时间：{current_time}",
                    "- 最近用户消息时间：未知；只能把消息原文当历史记录，不能推断为刚刚发生。",
                )
            )
        age_seconds = max(0.0, check_now - last_user_at)
        if age_seconds < 60:
            age_text = f"{int(age_seconds)} 秒前"
        elif age_seconds < 3600:
            age_text = f"{age_seconds / 60:.1f} 分钟前"
        elif age_seconds < 86400:
            age_text = f"{age_seconds / 3600:.2f} 小时前"
        else:
            age_text = f"{age_seconds / 86400:.2f} 天前"
        last_user_time = datetime.fromtimestamp(last_user_at).astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
        return "\n".join(
            (
                f"- 当前时间：{current_time}",
                f"- 最近用户消息时间：{last_user_time}（{age_text}）",
                "- 最近用户消息是带时间的历史原文，不等于用户当前仍处于当时状态。跨越明显时段后，旧的晚安、吃饭、出门、忙碌等内容不能改写成用户刚刚说过或正在发生。",
            )
        )

    def _format_proactive_model_judge_prompt(
        self,
        user: dict[str, Any],
        *,
        now: float | None = None,
    ) -> str:
        persona = str(self._get_default_persona_prompt() or "").strip()
        worldview_sections_getter = getattr(self, "_format_worldview_adaptation_prompt_sections", None)
        worldview_sections = (
            list(worldview_sections_getter() or ())
            if callable(worldview_sections_getter)
            else []
        )
        worldview = (
            str(self._format_worldview_adaptation_prompt() or "").strip()
            if not worldview_sections
            else ""
        )
        boundary_section_getter = getattr(self, "_format_private_user_boundary_prompt_section", None)
        boundary_section = (
            boundary_section_getter(user)
            if isinstance(user, dict) and callable(boundary_section_getter)
            else None
        )
        if boundary_section is None and isinstance(user, dict):
            boundary_fallback = getattr(self, "_format_private_user_boundary_hint", None)
            if callable(boundary_fallback):
                fallback_text = str(boundary_fallback(user) or "").strip()
                if fallback_text:
                    boundary_section = prompt_section(
                        key="proactive.judge.relationship_boundary",
                        title="关系边界",
                        source="proactive_engine",
                        content=fallback_text,
                    )
        rel_summary = ""
        formatter = getattr(self, "_format_relationship_summary", None)
        if callable(formatter):
            try:
                rel_summary = _single_line(formatter(user), 220)
            except Exception:
                rel_summary = ""
        local_alignment = self._planned_proactive_persona_alignment(user)
        semantics = self._planned_proactive_semantics(user)
        window_phase, window_detail = self._planned_impulse_window_phase(user)
        inner_readiness = self._proactive_inner_readiness(user)
        source_hint_section = self._proactive_source_model_hint_section(user)
        voice_section_getter = getattr(self, "_format_persona_voice_channel_prompt_section", None)
        planning_voice_section = voice_section_getter("planning") if callable(voice_section_getter) else None
        inner_voice_section = voice_section_getter("inner") if callable(voice_section_getter) else None
        role = self._private_user_role(user)
        nickname = _single_line(user.get("nickname"), 40) or runtime_persona_setting(
            self, "default_nickname", "你"
        )
        message_freshness = self._format_proactive_user_message_freshness(user, now=now)
        calendar_constraint_section = None
        calendar_hint_getter = getattr(self, "_format_proactive_calendar_constraint_hint_section", None)
        if callable(calendar_hint_getter):
            try:
                calendar_constraint_section = calendar_hint_getter()
            except Exception:
                calendar_constraint_section = None
        location_section_getter = getattr(
            self,
            "_format_mobile_user_location_context_for_proactive_prompt_section",
            None,
        )
        try:
            location_section = (
                location_section_getter(user)
                if callable(location_section_getter)
                else None
            )
        except Exception:
            location_section = None
        if location_section is None:
            location_fallback = getattr(self, "_format_mobile_user_location_context_for_proactive", None)
            try:
                fallback_text = str(location_fallback(user) or "").strip() if callable(location_fallback) else ""
            except Exception:
                fallback_text = ""
            if fallback_text:
                location_section = prompt_section(
                    key="proactive.judge.mobile_location",
                    title="主动场景位置线索",
                    source="proactive_engine",
                    content=fallback_text,
                )
        planned_route = PROACTIVE_ROUTE_REGISTRY.route_for(
            reason=user.get("planned_proactive_reason"),
            source=user.get("planned_proactive_source"),
            semantic_kind=user.get("planned_proactive_semantic_kind"),
            kind=user.get("planned_proactive_kind"),
        )
        instruction = prompt_section(
            key="proactive.judge.instructions",
            title="主动消息人格判定任务",
            source="proactive_engine",
            content=(
                "你是“主动消息人格/世界观判定器”。只判断这个主动计划是否像当前角色会自然产生的念头,不要写最终聊天正文。\n\n"
                "输出必须是 JSON 对象,不要 Markdown,不要解释：\n"
                '{"decision":"send|rewrite|defer|drop","score":0,"reason":"20字以内原因","delay_minutes":90,"planned_reason":"","action":"","topic":"","motive":""}\n\n'
                "判定含义：\n"
                "- send：计划自然,可以进入生成。\n"
                "- rewrite：方向有价值,但动机/话题/动作需要更贴合角色；只改 planned_reason/action/topic/motive,不要写最终聊天正文。\n"
                " - defer：只用于用户明确休息、拒绝主动或当前存在无法通过改写解决的硬时机冲突。\n"
                " - drop：只用于关系/隐私/世界观硬越界、内部机制泄露或无真实来源且无法改写的计划。\n\n"
                "硬要求：\n"
                "- 不得放行内部机制泄露、工具名、模型、插件、提示词、后台任务。\n"
                "- 不得新增事实、现实能力或用户没给过的关系信息。\n"
                "- 次要用户关系必须普通、低频、不过度亲密；主要用户/亲近关系也要尊重休息和拒绝。\n"
                "- 世界观表达必须贴合设定；能力只能作为角色内自然动机,不能露出调用过程。\n"
                " - 低价值、动机偏虚、人格贴合度一般或表达温度偏低都不是硬拦截理由；优先 rewrite，给出一个具体且低压力的 topic/motive。\n"
                " - 如果只是“想你了/来看看/在不在/忙不忙”且没有具体由头,必须优先 rewrite，而不是 defer/drop。\n"
                " - 连续未回应只影响语气和长度：改成一句低压、完整、不追问的表达，不能仅凭未回应就 defer/drop。\n"
                f" - 当前完整产生/发送路线是 {planned_route.key}（{planned_route.label}）。rewrite 只能优化这条路线内部的 action/topic/motive；不要把 planned_reason 改成另一类路线，也不要把事务、安全或续聊改写成普通关怀。\n"
                " - planned_reason 是内部原因键；没有同路线的现有原因键可用时保持原值，不得把“用户已道晚安”之类自然语言判断写进 planned_reason。\n"
                " - 角色设定、世界观、记忆摘要和旧消息都不能证明用户当前做过什么。只有带时间的最近用户原文明确支持时，才能写“用户刚刚说过/正在做”；否则保留原计划方向或只改 topic/motive。"
            ),
        )
        voice_bodies = []
        for section, fallback in (
            (planning_voice_section, "（无单独计划风格）"),
            (inner_voice_section, "（无单独内心活动风格）"),
        ):
            voice_bodies.append(
                render_prompt_sections([section], mode=PromptRenderMode.BODY_ONLY)
                if section is not None
                else fallback
            )
        worldview_content = (
            render_prompt_sections(
                worldview_sections[:1],
                mode=PromptRenderMode.BODY_ONLY,
            )
            if worldview_sections
            else _single_line(worldview, 1000) or "（无额外世界观适配）"
        )
        worldview_section = prompt_section(
            key="proactive.judge.worldview",
            title="世界观/适配",
            source="proactive_engine",
            content=worldview_content,
            children=tuple(worldview_sections[1:]),
        )
        sections = [
            prompt_section(
                key="proactive.judge.persona",
                title="角色设定",
                source="proactive_engine",
                content=_single_line(persona, 1800) or "（未读取到显式人格,按自然私聊陪伴角色处理）",
            ),
            worldview_section,
            prompt_section(
                key="proactive.judge.voice",
                title="人格标准化：计划/内心通道",
                source="proactive_engine",
                content=(
                    "\n".join(voice_bodies)
                    + "\n使用方式：这里只判断“这个念头/安排是否像角色自然产生”,不要把内心活动当成最终聊天正文,也不要因为风格规则而新增事实。"
                ),
            ),
        ]
        if boundary_section is not None:
            sections.append(boundary_section)
        sections.extend(
            (
                prompt_section(
                    key="proactive.judge.target",
                    title="当前对象",
                    source="proactive_engine",
                    content=(
                        f"- 昵称：{nickname}\n"
                        f"- 关系角色：{role}\n"
                        f"- 关系摘要：{rel_summary or '暂无'}\n"
                        f"- 连续未回应：{_safe_int(user.get('ignored_streak'), 0, 0)}\n"
                        f"- 最近用户消息：{_single_line(user.get('last_user_message'), 160) or '（无）'}\n"
                        f"- 最近 Bot 消息：{_single_line(user.get('last_companion_message'), 160) or '（无）'}\n"
                        f"- Bot 当前开口欲/主动表达温度：{_safe_float(inner_readiness.get('score'), 0.55):.2f}｜{_single_line(inner_readiness.get('label'), 60)}｜{_single_line(inner_readiness.get('detail'), 180)}"
                    ),
                ),
                prompt_section(
                    key="proactive.judge.freshness",
                    title="消息时效",
                    source="proactive_engine",
                    content=message_freshness,
                ),
            )
        )
        for optional_section in (calendar_constraint_section, location_section):
            if optional_section is not None:
                sections.append(optional_section)
        sections.append(
            prompt_section(
                key="proactive.judge.plan",
                title="当前主动计划",
                source="proactive_engine",
                content=(
                    f"- route：{planned_route.key}（{planned_route.label}）\n"
                    f"- source：{self._normalize_legacy_proactive_text(user.get('planned_proactive_source'), limit=40) or 'unknown'}\n"
                    f"- reason：{self._normalize_legacy_proactive_text(user.get('planned_proactive_reason'), limit=40) or 'check_in'}\n"
                    f"- action：{_single_line(user.get('planned_proactive_action'), 40) or 'message'}\n"
                    f"- topic：{_single_line(user.get('planned_proactive_topic'), 100) or '无'}\n"
                    f"- motive：{_single_line(user.get('planned_proactive_motive'), 220) or '无'}\n"
                    f"- 候选语义：{_single_line(semantics.get('kind'), 40)}/{_single_line(semantics.get('anchor_type'), 40)}｜score={_safe_float(semantics.get('score'), 0.5):.2f}｜pressure={_safe_float(semantics.get('pressure'), 0.4):.2f}｜risk={_safe_float(semantics.get('risk'), 0.0):.2f}｜{_single_line(semantics.get('note'), 140)}\n"
                    f"- 念头窗口：{window_phase}｜{window_detail}\n"
                    f"- 本地粗判：{_safe_float(local_alignment.get('score'), 0.0):.2f}｜{_single_line(local_alignment.get('note'), 140)}"
                ),
            )
        )
        if source_hint_section is not None:
            sections.append(source_hint_section)
        return "\n\n".join(
            (
                render_prompt_sections([instruction], mode=PromptRenderMode.BODY_ONLY),
                render_prompt_sections(sections, mode=PromptRenderMode.LABELED_BLOCK),
            )
        )

    async def _review_planned_proactive_with_model(
        self,
        user: dict[str, Any],
        *,
        now: float | None = None,
    ) -> dict[str, Any]:
        check_now = _now_ts() if now is None else now
        if not bool(runtime_persona_setting(self, "enable_llm_proactive_persona_judge", True)):
            return {"decision": "send", "score": 100, "reason": "模型人格判定关闭"}
        if self._normalize_legacy_proactive_text(user.get("planned_proactive_source"), limit=40) in {"timer", "troubleshooting", "simulation"}:
            return {"decision": "send", "score": 100, "reason": "特权计划跳过模型人格判定"}
        signature = self._planned_proactive_model_judge_signature(user)
        cached = self._cached_proactive_model_judgement(user, signature=signature, now=check_now)
        if isinstance(cached, dict):
            cached = self._apply_proactive_model_judgement_policy(user, cached)
            cached["cached"] = True
            return cached
        local_result = self._local_proactive_persona_judgement(user)
        if isinstance(local_result, dict):
            return local_result
        daily_limit = _safe_int(
            runtime_persona_setting(self, "proactive_persona_judge_max_daily", 12),
            12,
            0,
            100,
        )
        if daily_limit <= 0 or self._proactive_persona_judge_calls_today() >= daily_limit:
            return {"decision": "send", "score": 0, "reason": "模型日预算已满，使用本地规则", "local": True}
        prompt = self._format_proactive_model_judge_prompt(user, now=check_now)
        memory_getter = getattr(self, "_memory_companion_compose_feature_context", None)
        if callable(memory_getter):
            user_id = _single_line(user.get("user_id") or user.get("id"), 80)
            query = " ".join(
                part
                for part in (
                    "主动消息适合性",
                    _single_line(user.get("planned_proactive_reason"), 80),
                    _single_line(user.get("planned_proactive_topic"), 120),
                    _single_line(user.get("planned_proactive_motive"), 180),
                    "用户习惯 上次主动回应 边界 当前穿搭 当前日程 最近状态",
                )
                if part
            )
            memory_context = await memory_getter(
                kind="proactive_review",
                query=query,
                user=user,
                user_id=user_id,
                top_k=5,
                max_chars=800,
            )
            if memory_context:
                memory_sections = [
                    prompt_section(
                        key="proactive.judge.memory",
                        title="我会牢牢记住你 相关记忆",
                        source="proactive_engine",
                        content=(
                            "<!-- private_companion_memory_review_context_v1 -->\n"
                            f"{memory_context}\n"
                            "使用方式：只辅助判断是否适合主动、是否需要改写或延后；不要在理由里暴露检索过程。"
                        ),
                    )
                ]
                core_memory_section = core_memory_usage_contract_section(
                    memory_context,
                    stage="review",
                )
                if core_memory_section is not None:
                    memory_sections.append(core_memory_section)
                prompt = f"{prompt.rstrip()}\n\n{render_prompt_sections(memory_sections, mode=PromptRenderMode.LABELED_BLOCK)}"
        started = time.perf_counter()
        raw = await self._llm_call(
            prompt,
            max_tokens=260,
            provider_id=self._task_provider(
                _persona_provider_id(
                    self,
                    "PROACTIVE_PERSONA_JUDGE_PROVIDER_ID",
                    "proactive_persona_judge_provider_id",
                    "complex",
                ),
                _persona_provider_id(
                    self, "RESPONSE_REVIEW_PROVIDER_ID", "response_review_provider_id", "fast"
                ),
                _persona_provider_id(self, "MAI_STYLE_PROVIDER_ID", "mai_style_provider_id", "fast"),
            ),
            task="proactive_persona_judge",
        )
        parsed = self._parse_json_object(raw)
        result = self._normalize_proactive_model_judgement(parsed)
        if not isinstance(result, dict):
            logger.info("模型人格判定无有效 JSON,降级本地判定")
            return {"decision": "send", "score": 0, "reason": "模型判定失败,降级本地"}
        result["signature"] = signature
        result = self._apply_proactive_model_judgement_policy(user, result)
        result["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
        logger.info(
            "主动模型人格判定: decision=%s score=%s reason=%s elapsed=%sms",
            result.get("decision"),
            result.get("score"),
            _single_line(result.get("reason"), 100),
            result.get("elapsed_ms"),
        )
        return result

    def _cache_proactive_model_judgement(
        self,
        user: dict[str, Any],
        judgement: dict[str, Any],
        *,
        now: float | None = None,
    ) -> None:
        signature = _single_line(judgement.get("signature"), 80) or self._planned_proactive_model_judge_signature(user)
        user["planned_proactive_model_judge_signature"] = signature
        user["planned_proactive_model_judge_result"] = {
            key: value
            for key, value in judgement.items()
            if key in {"decision", "score", "reason", "hard", "delay_minutes", "reason_field", "action", "topic", "motive"}
        }
        judged_at = _now_ts() if now is None else now
        user["planned_proactive_model_judge_at"] = judged_at
        cache = user.get("proactive_persona_judge_cache")
        cache = dict(cache) if isinstance(cache, dict) else {}
        ttl = max(
            5,
            _safe_int(
                runtime_persona_setting(self, "proactive_persona_judge_cache_minutes", 180),
                180,
                5,
                720,
            ),
        ) * 60
        cache = {
            key: value for key, value in cache.items()
            if isinstance(value, dict) and judged_at - _safe_float(value.get("judged_at"), 0) <= ttl
        }
        cache[signature] = {"judged_at": judged_at, "result": dict(user["planned_proactive_model_judge_result"])}
        if len(cache) > 16:
            newest = sorted(cache.items(), key=lambda item: _safe_float(item[1].get("judged_at"), 0), reverse=True)[:16]
            cache = dict(newest)
        user["proactive_persona_judge_cache"] = cache

    def _apply_proactive_model_rewrite(self, user: dict[str, Any], judgement: dict[str, Any]) -> bool:
        changed = False
        new_reason = self._normalize_legacy_proactive_text(judgement.get("reason_field"), limit=40)
        new_action = self._normalize_legacy_proactive_text(judgement.get("action"), limit=40)
        new_topic = _single_line(judgement.get("topic"), 80)
        new_motive = self._normalize_internal_motive_text(_single_line(judgement.get("motive"), 180))
        current_reason = self._normalize_legacy_proactive_text(user.get("planned_proactive_reason"), limit=40)
        current_action = self._normalize_legacy_proactive_text(user.get("planned_proactive_action"), limit=40)
        if new_reason:
            current_route = PROACTIVE_ROUTE_REGISTRY.route_for(
                reason=current_reason,
                source=user.get("planned_proactive_source"),
                semantic_kind=user.get("planned_proactive_semantic_kind"),
                kind=user.get("planned_proactive_kind"),
            )
            rewritten_route = PROACTIVE_ROUTE_REGISTRY.route_for(
                reason=new_reason,
                source=user.get("planned_proactive_source"),
                semantic_kind=user.get("planned_proactive_semantic_kind"),
            )
            if rewritten_route.key != current_route.key:
                new_reason = ""
        if new_reason and new_reason != current_reason:
            user["planned_proactive_reason"] = new_reason
            changed = True
        if new_action and self._action_is_available(new_action, user) and new_action != current_action:
            user["planned_proactive_action"] = new_action
            changed = True
        if new_topic and new_topic != _single_line(user.get("planned_proactive_topic"), 80):
            user["planned_proactive_topic"] = new_topic
            changed = True
        if new_motive and new_motive != _single_line(user.get("planned_proactive_motive"), 180):
            user["planned_proactive_motive"] = new_motive
            changed = True
        if changed and self._private_user_role(user) == "friend":
            sanitized = self._sanitize_friend_proactive_plan_fields(
                user,
                reason=self._normalize_legacy_proactive_text(user.get("planned_proactive_reason"), limit=40) or "check_in",
                action=self._normalize_legacy_proactive_text(user.get("planned_proactive_action"), limit=40) or "message",
                topic=_single_line(user.get("planned_proactive_topic"), 80),
                motive=_single_line(user.get("planned_proactive_motive"), 180),
            )
            user["planned_proactive_reason"] = sanitized["reason"]
            user["planned_proactive_action"] = sanitized["action"]
            user["planned_proactive_topic"] = sanitized["topic"]
            user["planned_proactive_motive"] = sanitized["motive"]
        if changed:
            route_store = getattr(self, "_store_planned_proactive_route_fields", None)
            if callable(route_store):
                route_store(
                    user,
                    {
                        "source": user.get("planned_proactive_source"),
                        "reason": user.get("planned_proactive_reason"),
                        "action": user.get("planned_proactive_action"),
                        "topic": user.get("planned_proactive_topic"),
                        "motive": user.get("planned_proactive_motive"),
                        "origin_event_id": user.get("planned_proactive_origin_event_id"),
                    },
                )
        return changed

    def _persona_action_profile(self) -> dict[str, bool]:
        text = str(self._get_default_persona_prompt() or "")
        playful_markers = ("恶作剧", "小恶魔", "腹黑", "俏皮", "捉弄", "欺负", "调皮")
        clingy_markers = ("依赖", "依恋", "特殊的情感", "知心朋友", "关心", "体贴", "想念", "共犯")
        observant_markers = ("看透", "观察", "温柔", "安静", "留意", "敏锐")
        visual_markers = ("自拍", "照片", "景色", "表情包", "外观", "外形", "穿搭", "发型", "发饰")
        voice_markers = ("悄悄说", "口语化", "抽空回复", "亲切感", "温柔", "顺从")
        return {
            "playful": any(marker in text for marker in playful_markers),
            "clingy": any(marker in text for marker in clingy_markers),
            "observant": any(marker in text for marker in observant_markers),
            "visual": any(marker in text for marker in visual_markers),
            "voicey": any(marker in text for marker in voice_markers),
        }

