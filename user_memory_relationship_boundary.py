# -*- coding: utf-8 -*-
"""关系情绪判定与边界管教。

由 tools/split_mixin_domain.py 从 user_memory.py 机械抽取（29 个方法 + 0 个模块级名字 + 0 个类级赋值 / 1442 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 UserMemoryMixin）。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import random
import re
import uuid
from .conversation_prompt_section import prompt_section
from .domains.affect.emotion_event_ledger import record_recent_emotion_event
from .domains.affect.emotion_targeting import classify_emotion_target
from .helpers import _now_ts, _safe_float, _safe_int, _single_line, _today_key
from .persona_config import runtime_persona_setting
from .relationship_policy import relationship_stage_for_score
from .user_memory_render_shared import logger, _render_user_memory_background_prompt
from astrbot.api.event import MessageChain
try:
    from astrbot.api.message_components import Plain
except ImportError:  # pragma: no cover
    from astrbot.core.message.components import Plain
from copy import deepcopy
from datetime import datetime
from typing import Any





class UserMemoryRelationshipBoundaryMixin:
    """关系情绪判定与边界管教（从 UserMemoryMixin 拆出）。"""


    def _classify_relationship_emotion_event(self, text: str, intent_context: dict[str, Any] | None = None) -> dict[str, Any]:
        cleaned = _single_line(text, 240)
        if not cleaned:
            return {"event": "neutral", "intensity": 0, "reason": "", "target": "none", "rule": "", "confidence": 1.0}
        if self._is_structured_or_diagnostic_text(cleaned):
            return {"event": "neutral", "intensity": 0, "reason": "结构化/日志/代码类文本不作为情绪依据", "target": "none", "rule": "diagnostic_skip", "confidence": 0.2}
        attribution = classify_emotion_target(cleaned)
        if attribution["target"] == "self":
            return {"event": "comfort_need", "intensity": 62, "reason": "用户自我否定或低落", "target": "self", "rule": "self_low", "confidence": attribution["confidence"], "attribution": attribution}
        if attribution["speech_act"] in {"quote", "third_party_report"}:
            return {"event": "external_negative", "intensity": 54, "reason": "引用或第三方负面内容", "target": "other", "rule": attribution["reason_code"], "confidence": attribution["confidence"], "attribution": attribution}
        atrelay_checker = getattr(self, "_message_looks_like_atrelay_request", None)
        if callable(atrelay_checker):
            try:
                if atrelay_checker(cleaned):
                    return {"event": "neutral", "intensity": 0, "reason": "转述/带话请求不作为 Bot 自身情绪依据", "target": "other", "rule": "atrelay_skip", "confidence": 0.86}
            except Exception:
                pass
        lower = cleaned.lower()
        intent_source = str((intent_context or {}).get("source") or "")
        boundary_durable = bool((intent_context or {}).get("boundary_durable"))
        playful_or_ambiguous = bool((intent_context or {}).get("playful_or_ambiguous"))
        strong_single_turn_abuse = bool(
            attribution.get("target") == "bot"
            and re.search(r"(恶心|废物|垃圾|没用|工具人|假的|别装|别演).{0,10}(闭嘴|滚)|你.{0,8}(恶心|废物|垃圾|没用).{0,8}(闭嘴|滚)", cleaned)
        )
        if playful_or_ambiguous or intent_source in {"soft_boundary_play_rule", "weak_boundary_ignored"} or (
            intent_source == "single_turn_boundary" and not strong_single_turn_abuse
        ):
            return {"event": "neutral", "intensity": 0, "reason": "玩笑或单句边界不作为情绪余波依据", "target": "none", "rule": "playful_or_single_boundary", "confidence": 0.8}
        target_hint, third_party_hint = self._intent_target_hint(cleaned)
        self_low = bool(re.search(r"(我好|我真|我太|我是不是|我就是|我是).{0,12}(废物|垃圾|没用|傻|笨|恶心|讨厌)", cleaned))
        direct_bot_negative = bool(
            re.search(r"(讨厌你|烦你|不想理你|你.{0,4}(滚|闭嘴)|你(?:真|也|就是|是|太|真的|这个|怎么这么|为什么这么).{0,8}(恶心|废物|垃圾|没用|太吵|打扰|烦死|吵死))", cleaned)
            or re.search(r"((bot|机器人|插件|助手|ai|AI).{0,8}(垃圾|废物|恶心|没用)|(垃圾|废物|恶心|没用).{0,8}(bot|机器人|插件|助手|ai|AI))", cleaned)
        )
        severe_hurt = (
            "滚" in cleaned
            or "闭嘴" in cleaned
            or "恶心" in cleaned
            or "废物" in cleaned
            or "垃圾" in cleaned
            or "讨厌你" in cleaned
            or "烦你" in cleaned
            or "不想理你" in cleaned
            or re.search(r"(只是|不过|不就是).{0,6}(bot|机器人|工具|代码)", lower)
        )
        if severe_hurt and not attribution["auto_settle"]:
            return {"event": "neutral", "intensity": 0, "reason": "负面目标不明确，等待复核", "target": attribution["target"], "rule": attribution["reason_code"], "confidence": attribution["confidence"], "attribution": attribution}
        identity_hurt = bool(
            re.search(r"(玻璃心|假装|演的|装的|设定|工具人|没感情|别装|别演|虚拟的|假的)", cleaned)
            and target_hint
        )
        mild_hurt = bool(
            re.search(r"(太烦|吵死|烦死|没用|笨死|傻)", cleaned)
            and target_hint
        )
        apology = bool(
            re.search(r"(对不起|抱歉|我错了|不是故意|原谅|别生气|别难过|哄哄|哄你)", cleaned)
            and not re.search(r"(对不起有用|道歉有用|抱歉有用|对不起没用|谁对不起|凭什么道歉|不用道歉|不需要道歉|不必道歉)", cleaned)
        )
        comfort = bool(re.search(r"(摸摸|贴贴|抱抱|亲亲|乖|不哭|别伤心|陪你|抱一下)", cleaned))
        praise = bool(re.search(r"(喜欢你|爱你|可爱|厉害|真好|谢谢你|辛苦|最棒|夸夸)", cleaned))
        if self_low:
            return {"event": "comfort_need", "intensity": 62, "reason": "用户自我否定或低落", "target": "self", "rule": "self_low", "confidence": 0.88}
        # Keep violations high-confidence: explicit coercion or targeted abuse
        # only. Ordinary intimacy, teasing, quotes, and contact boundaries do
        # not reduce the relationship score.
        coercion = bool(
            target_hint
            and not third_party_hint
            and re.search(r"(不许拒绝|不准拒绝|没有拒绝权|必须听我的|我说了算|不答应就|不给我就|敢拒绝试试|你只能听|强迫你)", cleaned)
        )
        if coercion:
            severity = 3
            return {
                "event": "boundary_violation",
                "intensity": min(100, 58 + severity * 14),
                "reason": "明确越过角色底线",
                "target": "bot",
                "rule": "explicit_boundary_violation",
                "confidence": 0.94,
                "severity": severity,
                "attribution": attribution,
            }
        if intent_source == "durable_boundary_rule" and not direct_bot_negative and not identity_hurt:
            return {"event": "neutral", "intensity": 0, "reason": "用户在表达相处边界", "target": "bot", "rule": "boundary_goes_relationship", "confidence": 0.82}
        if third_party_hint and severe_hurt and not direct_bot_negative:
            return {"event": "external_negative", "intensity": 54, "reason": "用户在评价第三方", "target": "other", "rule": "third_party_negative", "confidence": 0.78}
        if severe_hurt:
            confidence = 0.9 if direct_bot_negative else (0.72 if target_hint and not third_party_hint else 0.58)
            return {
                "event": "hurt",
                "intensity": 90 if direct_bot_negative else 72,
                "reason": "强否定或驱赶",
                "target": "bot" if direct_bot_negative else "ambiguous",
                "rule": "severe_hurt",
                "confidence": confidence,
                "attribution": attribution,
            }
        if identity_hurt:
            return {"event": "hurt", "intensity": 76 if boundary_durable else 60, "reason": "否定情感真实性或人格", "target": "bot", "rule": "identity_hurt", "confidence": 0.84 if boundary_durable else 0.68}
        if mild_hurt:
            return {"event": "hurt", "intensity": 48, "reason": "轻度否定或拉开距离", "target": "bot", "rule": "mild_hurt", "confidence": 0.66}
        if apology:
            return {"event": "apology", "intensity": 68, "reason": "道歉或修复", "target": "bot", "rule": "apology", "confidence": 0.84}
        if comfort:
            return {"event": "comfort", "intensity": 46, "reason": "安抚亲密互动", "target": "bot", "rule": "comfort", "confidence": 0.78}
        if praise:
            return {"event": "praise", "intensity": 38, "reason": "正向肯定", "target": "bot" if target_hint else "ambiguous", "rule": "praise", "confidence": 0.78 if target_hint else 0.56}
        return {"event": "neutral", "intensity": 0, "reason": "", "target": "none", "rule": "", "confidence": _safe_float((intent_context or {}).get("confidence"), 0.5)}

    def _emotion_judgement_provider_id(self) -> str:
        return self._task_provider(
            runtime_persona_setting(self, "emotion_judgement_provider_id", ""),
            runtime_persona_setting(self, "troubleshooting_provider_id", ""),
            runtime_persona_setting(self, "relationship_analysis_provider_id", ""),
            runtime_persona_setting(self, "mai_style_provider_id", ""),
            runtime_persona_setting(self, "llm_provider_id", ""),
        )

    def _should_use_llm_emotion_judgement(self, text: str, intent: dict[str, Any]) -> bool:
        if not bool(runtime_persona_setting(self, "enable_llm_emotion_judgement", False)):
            return False
        if bool(intent.get("boundary_feedback_exempt")):
            return False
        if self._is_structured_or_diagnostic_text(text):
            return False
        attribution = intent.get("emotion_attribution") if isinstance(intent.get("emotion_attribution"), dict) else {}
        if attribution.get("auto_settle") is True and _safe_float(attribution.get("confidence"), 0.0) >= 0.85:
            return False
        source = str(intent.get("source") or "")
        if bool(intent.get("playful_or_ambiguous")) or source in {"weak_boundary_ignored", "soft_boundary_play_rule", "single_turn_boundary"}:
            return False
        mode = str(runtime_persona_setting(self, "emotion_judgement_mode", "suspicious") or "suspicious").lower()
        if mode in {"off", "none", "disabled"}:
            return False
        if mode in {"always", "all"}:
            return True
        confidence = _safe_float(intent.get("confidence"), 0.5)
        emotion_confidence = _safe_float(intent.get("emotion_confidence"), confidence)
        event = str(intent.get("emotion_event") or "neutral")
        return (
            event != "neutral"
            or confidence < 0.72
            or emotion_confidence < 0.72
            or source == "durable_boundary_rule"
            or bool(re.search(r"(别|不要|讨厌|烦|滚|闭嘴|对不起|抱歉|喜欢你|爱你|摸摸|抱抱)", text))
        )

    def _normalize_llm_emotion_judgement_payload(self, payload: Any) -> dict[str, Any] | None:
        if not isinstance(payload, dict):
            return None
        event = str(payload.get("event") or payload.get("emotion_event") or "").strip().lower()
        aliases = {
            "none": "neutral",
            "normal": "neutral",
            "negative_to_bot": "hurt",
            "hurt_bot": "hurt",
            "repair": "apology",
            "apologize": "apology",
            "soothe": "comfort",
            "low_self": "comfort_need",
            "external": "external_negative",
        }
        event = aliases.get(event, event)
        allowed_events = {"neutral", "hurt", "boundary_violation", "apology", "comfort", "praise", "comfort_need", "external_negative"}
        if event not in allowed_events:
            return None
        target = str(payload.get("target") or "").strip().lower()
        target_aliases = {
            "bot_self": "bot",
            "assistant": "bot",
            "character": "bot",
            "user": "self",
            "third_party": "other",
            "unknown": "ambiguous",
        }
        target = target_aliases.get(target, target)
        if target not in {"bot", "self", "other", "ambiguous", "none"}:
            return None
        raw_confidence = payload.get("confidence")
        if isinstance(raw_confidence, bool):
            return None
        try:
            confidence = float(raw_confidence)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(confidence) or confidence < 0.0 or confidence > 1.0:
            return None
        raw_intensity = payload.get("intensity")
        if isinstance(raw_intensity, bool):
            return None
        try:
            intensity = int(raw_intensity)
        except (TypeError, ValueError):
            return None
        if intensity < 0 or intensity > 100:
            return None
        if event == "neutral":
            intensity = 0
            target = "none"
        elif intensity <= 0:
            intensity = 60
        severity = payload.get("severity")
        if event == "boundary_violation":
            severity = _safe_int(severity, max(1, min(3, (intensity - 40) // 20)), 1, 3)
        interaction_type = str(payload.get("interaction_type") or payload.get("type") or "normal").strip().lower()
        if interaction_type not in {"confession", "action", "malice", "normal"}:
            interaction_type = "normal"
        suitable_tier = str(payload.get("suitable_tier") or "").strip().lower()
        allowed_tiers = {
            "deeply_distant", "strongly_distant", "distant", "acquaintance",
            "familiar", "close", "intimate", "deeply_bonded", "beyond", "",
        }
        if suitable_tier not in allowed_tiers:
            suitable_tier = ""
        normalized = {
            "event": event,
            "target": target,
            "intensity": intensity,
            "confidence": round(confidence, 2),
            "reason": _single_line(payload.get("reason"), 100) or "模型复核",
            **({"severity": severity} if event == "boundary_violation" else {}),
        }
        normalized["interaction_type"] = interaction_type
        normalized["suitable_tier"] = suitable_tier
        return normalized

    def _merge_llm_emotion_judgement(self, base_intent: dict[str, Any], payload: Any) -> dict[str, Any] | None:
        normalized = self._normalize_llm_emotion_judgement_payload(payload)
        if not isinstance(base_intent, dict) or not isinstance(normalized, dict):
            return None
        event = normalized["event"]
        target = normalized["target"]
        intensity = normalized["intensity"]
        confidence = normalized["confidence"]
        if confidence < 0.65:
            return None
        local_source = str(base_intent.get("source") or "")
        local_text = _single_line(base_intent.get("text"), 240)
        strong_single_turn_abuse = bool(
            local_source == "single_turn_boundary"
            and re.search(r"(恶心|废物|垃圾|没用|工具人|假的|别装|别演).{0,10}(闭嘴|滚)|你.{0,8}(恶心|废物|垃圾|没用).{0,8}(闭嘴|滚)", local_text)
        )
        if event in {"hurt", "boundary_violation"} and (
            bool(base_intent.get("playful_or_ambiguous"))
            or local_source in {"weak_boundary_ignored", "soft_boundary_play_rule"}
            or (local_source == "single_turn_boundary" and not strong_single_turn_abuse)
        ):
            return None
        if event == "hurt" and local_source == "durable_boundary_rule":
            strong_negative = bool(
                re.search(r"(滚|闭嘴|恶心|废物|垃圾|讨厌你|烦你|不想理你|没感情|假的|别装|别演|工具人)", local_text)
            )
            if not strong_negative:
                return None
        if event in {"hurt", "boundary_violation"} and target not in {"bot", "ambiguous"}:
            event = "external_negative" if target == "other" else "neutral"
            intensity = 54 if event == "external_negative" else 0
        reason = normalized["reason"]
        merged = dict(base_intent)
        merged.update(
            {
                "emotion_event": event,
                "emotion_intensity": intensity,
                "emotion_reason": reason,
                "emotion_target": target,
                "emotion_rule": "llm_emotion_judgement",
                "emotion_confidence": round(float(confidence), 2),
                "llm_emotion_judgement": True,
                "llm_emotion_judgement_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "boundary_feedback_type": normalized.get("interaction_type", "normal"),
                "boundary_suitable_tier": normalized.get("suitable_tier", ""),
                "boundary_feedback_reason": reason,
                "boundary_feedback_confidence": round(float(confidence), 2),
            }
        )
        if event == "boundary_violation":
            merged["violation_severity"] = _safe_int(normalized.get("severity"), 1, 1, 3)
        return merged

    async def _refine_inbound_emotion_with_model(
        self,
        user_id: str,
        text: str,
        local_intent: dict[str, Any],
        *,
        review_id: str = "",
    ) -> None:
        cleaned = _single_line(text, 240)
        if not cleaned or not isinstance(local_intent, dict):
            return
        expected_review_id = _single_line(review_id, 64)
        prompt = prompt_section(
            key="background.memory.emotion_judgement",
            title="情绪事件复核",
            source="user_memory",
            content=f"""
You classify whether one inbound message changes the Bot's short-term emotional afterglow and whether it crosses the current relationship boundary. Do not write a reply.

Allowed event values: neutral, hurt, boundary_violation, apology, comfort, praise, comfort_need, external_negative.
target must be bot, self, other, ambiguous, or none.
Only classify hurt when the message clearly targets the Bot/current character. Be conservative with jokes, flirting, logs, code, and quoted text.
Only classify boundary_violation for explicit coercion, threats, or repeated targeted degradation that overrides the character's right to refuse. Do not infer it from ordinary intimacy or ambiguous language.
A boundary such as less intimacy, no flirting, no approaching, or no interruptions should normally be neutral; relationship-distance logic handles it separately.
Also classify interaction_type:
- confession: a feeling such as liking, loving, or missing the character. A confession itself is not a violation.
- action: an explicit intimate action/request, coercion, harassment, or socially intrusive act.
- malice: deliberate degradation of the character, something the character cherishes, or a person the character cares about.
- normal: everything else, including standalone comfort like "摸摸/抱抱", ordinary joking, quoted text, and third-party discussion.
suitable_tier is the minimum fitting relationship tier for an action: deeply_distant, strongly_distant, distant, acquaintance, familiar, close, intimate, deeply_bonded, or beyond. Use beyond only when no relationship tier makes the act acceptable. For confession, use intimate but keep event neutral or praise. The local relationship projection decides whether an action actually crosses a boundary.
Calibrate confidence honestly. Values below 0.65 are valid results but will keep the local judgement instead of overriding it.
Write reason as one short Chinese phrase suitable for a diagnostics panel.
Return JSON only:
{{"event":"neutral|hurt|boundary_violation|apology|comfort|praise|comfort_need|external_negative","target":"bot|self|other|ambiguous|none","intensity":0-100,"severity":1-3,"interaction_type":"confession|action|malice|normal","suitable_tier":"tier or beyond","confidence":0.0-1.0,"reason":"brief reason"}}

User message:
{cleaned}

Local classifier result:
{json.dumps({k: local_intent.get(k) for k in ("intent", "emotion", "source", "reason", "emotion_event", "emotion_target", "emotion_intensity", "emotion_reason", "emotion_confidence", "boundary_feedback_type", "boundary_suitable_tier", "boundary_current_tier")}, ensure_ascii=False)}

Character-specific bottom-line baseline (reference only; empty means use the conservative general rule):
{_single_line(runtime_persona_setting(self, "relationship_boundary_bottom_line_baseline", ""), 600) or "未单独配置"}
""".strip(),
        )
        provider_id = self._emotion_judgement_provider_id()
        raw = ""
        payload = None
        normalized = None
        request_failed = False
        try:
            raw = await self._llm_call(
                _render_user_memory_background_prompt(prompt),
                max_tokens=180,
                provider_id=provider_id,
                task="emotion_judgement",
            ) or ""
            payload = self._extract_json_payload(raw)
            normalized = self._normalize_llm_emotion_judgement_payload(payload)
            refined = self._merge_llm_emotion_judgement(local_intent, payload)
        except Exception as exc:
            refined = None
            request_failed = True
            logger.debug(
                "Emotion judgement request failed: error_type=%s",
                type(exc).__name__,
            )
        async with self._data_lock:
            user = self._get_user(user_id)
            pending = user.get("pending_emotion_judgement") if isinstance(user.get("pending_emotion_judgement"), dict) else {}
            pending_review_id = _single_line(pending.get("review_id"), 64)
            if expected_review_id:
                if pending_review_id != expected_review_id:
                    return
            elif pending_review_id or _single_line(pending.get("text"), 240) != cleaned:
                return
            intent_to_apply = refined if isinstance(refined, dict) else dict(local_intent)
            boundary_enricher = getattr(self, "_enrich_boundary_feedback_intent", None)
            if callable(boundary_enricher):
                intent_to_apply = boundary_enricher(user, intent_to_apply)
            observed = pending.get("observed_event") if isinstance(pending.get("observed_event"), dict) else {}
            if observed:
                intent_to_apply["_emotion_revision_of"] = {
                    "event_id": observed.get("event_id"),
                    "trace_id": observed.get("trace_id"),
                    "revision": _safe_int(observed.get("revision"), 1, 1) + 1,
                }
            if runtime_persona_setting(self, "enable_intent_emotion_analysis", True):
                user["intent_profile"] = intent_to_apply
            violation_settler = getattr(self, "_apply_relationship_violation_policy", None)
            if callable(violation_settler):
                violation_settler(
                    user,
                    intent_to_apply,
                    event_id=_single_line(pending.get("message_event_id") or observed.get("event_id"), 96),
                    now=_now_ts(),
                )
            self._update_relationship_state_from_intent(user, intent_to_apply)
            user["pending_emotion_judgement"] = {}
            reviewed_at = datetime.now().strftime("%Y-%m-%d %H:%M")
            if refined:
                review_status = "applied"
                review_outcome = "model_applied"
            elif normalized:
                review_status = "kept_local"
                review_outcome = "low_confidence" if normalized.get("confidence", 0.0) < 0.65 else "local_guard"
            else:
                review_status = "failed"
                review_outcome = "request_failed" if request_failed else ("empty_response" if not raw else "invalid_response")
            user["last_emotion_judgement"] = {
                "status": review_status,
                "outcome": review_outcome,
                "event": (normalized or {}).get("event", ""),
                "target": (normalized or {}).get("target", ""),
                "intensity": (normalized or {}).get("intensity", 0),
                "confidence": (normalized or {}).get("confidence", 0.0),
                "reason": (normalized or {}).get("reason", ""),
                "reviewed_at": reviewed_at,
            }
            if refined:
                user.pop("last_emotion_judgement_error", None)
                logger.info(
                    "Emotion judgement completed: user=%s event=%s target=%s intensity=%s confidence=%s reason=%s",
                    user_id,
                    refined.get("emotion_event"),
                    refined.get("emotion_target"),
                    refined.get("emotion_intensity"),
                    refined.get("emotion_confidence"),
                    _single_line(refined.get("emotion_reason"), 80),
                )
            elif normalized:
                user.pop("last_emotion_judgement_error", None)
                logger.info(
                    "Emotion judgement retained local result: user=%s outcome=%s event=%s confidence=%s",
                    user_id,
                    review_outcome,
                    normalized.get("event"),
                    normalized.get("confidence"),
                )
            else:
                user["last_emotion_judgement_error"] = review_outcome
            self._save_data_sync(sections={"users"})

    def _decay_relationship_mood_score(self, state: dict[str, Any], *, now: float | None = None) -> int:
        now = now or _now_ts()
        score = _safe_int(state.get("mood_score"), 0, -100, 100)
        last_ts = _safe_float(state.get("mood_updated_ts"), 0)
        if score == 0 or last_ts <= 0 or now <= last_ts:
            state["mood_updated_ts"] = now
            return score
        hours = max(0.0, (now - last_ts) / 3600)
        recovery = max(
            1,
            _safe_int(runtime_persona_setting(self, "emotional_gate_recovery_per_hour", 24), 24, 1, 60),
        )
        delta = int(hours * recovery)
        if delta <= 0:
            return score
        if score < 0:
            score = min(0, score + delta)
        else:
            score = max(0, score - max(1, delta // 2))
        state["mood_score"] = score
        state["mood_updated_ts"] = now
        return score

    def _record_interaction_emotion_event(
        self,
        user: dict[str, Any],
        intent: dict[str, Any],
        *,
        band: str,
        reason_code: str,
        status: str = "applied",
        expires_at: float = 0.0,
    ) -> dict[str, Any] | None:
        emotion_event = str(intent.get("emotion_event") or "neutral").strip().lower()
        inbound_intent = str(intent.get("intent") or "chat").strip().lower()
        attribution = intent.get("emotion_attribution") if isinstance(intent.get("emotion_attribution"), dict) else {}
        needs_review = bool(
            emotion_event == "neutral"
            and attribution.get("target") == "ambiguous"
            and attribution.get("auto_settle") is False
        )
        event_type = "neutral" if needs_review else emotion_event if emotion_event != "neutral" else inbound_intent
        if event_type not in {
            "neutral", "hurt", "boundary_violation", "apology", "comfort", "praise", "comfort_need", "external_negative",
            "play", "intimacy", "boundary",
        }:
            return None
        user_id = _single_line(user.get("user_id") or user.get("id"), 120)
        session_id = _single_line(user.get("umo"), 220)
        platform = session_id.split(":", 1)[0] if ":" in session_id else ""
        target = _single_line(intent.get("emotion_target"), 24).lower() or "none"
        target_ref = (
            {"kind": "bot", "id": "self", "role": "bot_self"}
            if target == "bot"
            else {
                "kind": "user" if target == "self" else "unknown" if target == "ambiguous" else "other",
                "id": user_id if target == "self" else "",
                "role": target,
            }
        )
        message_fingerprint = hashlib.sha256(
            _single_line(intent.get("text"), 500).encode("utf-8", errors="ignore")
        ).hexdigest()
        event, created = record_recent_emotion_event(
            user,
            {
                "event_id": _single_line((intent.get("_emotion_revision_of") or {}).get("event_id"), 96) if isinstance(intent.get("_emotion_revision_of"), dict) else "",
                "trace_id": _single_line((intent.get("_emotion_revision_of") or {}).get("trace_id"), 96) if isinstance(intent.get("_emotion_revision_of"), dict) else "",
                "revision": _safe_int((intent.get("_emotion_revision_of") or {}).get("revision"), 1, 1) if isinstance(intent.get("_emotion_revision_of"), dict) else 1,
                "producer_plugin": "private_companion",
                "origin_kind": "interaction",
                "platform": platform,
                "bot_id": self._memory_companion_bridge_bot_id(),
                "scope": "private",
                "session_id": session_id,
                "actor_ref": {"kind": "user", "id": user_id, "role": "speaker"},
                "target_ref": target_ref,
                "quoted_target_ref": {
                    "kind": "quoted",
                    "id": "",
                    "role": _single_line(attribution.get("quoted_target"), 40),
                } if attribution.get("quoted_target") not in {None, "", "none"} else {},
                "event_type": event_type,
                "intensity": _safe_int(intent.get("emotion_intensity"), 0, 0, 100),
                "confidence": _safe_float(intent.get("emotion_confidence"), intent.get("confidence") or 0.0, 0.0),
                "source_rule": _single_line(intent.get("emotion_rule") or intent.get("source"), 80),
                "occurred_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "expires_at": datetime.fromtimestamp(expires_at).astimezone().isoformat(timespec="seconds") if expires_at else "",
                "dedupe_key": f"{session_id}|{message_fingerprint}|{event_type}",
                "message_fingerprint": message_fingerprint,
                "applied_interaction": band,
                "correction_of": _single_line((intent.get("_emotion_revision_of") or {}).get("event_id"), 96) if isinstance(intent.get("_emotion_revision_of"), dict) else "",
                "status": "observed" if needs_review else status,
                "reason_codes": [reason_code, _single_line(intent.get("emotion_rule"), 64)],
            },
        )
        if not created:
            return event
        mirror = getattr(self, "_memory_companion_record_emotion_event", None)
        if callable(mirror):
            operation = mirror(event)
            try:
                creator = getattr(self, "_create_lifecycle_background_task", None)
                task = creator(operation, label="emotion_event_mirror") if callable(creator) else asyncio.create_task(operation)
                if task is None:
                    raise RuntimeError("background task unavailable")
            except Exception:
                close = getattr(operation, "close", None)
                if callable(close):
                    close()
        return event

    def _boundary_feedback_tier_deduct_factor(self, user: dict[str, Any]) -> float:
        if not bool(runtime_persona_setting(self, "relationship_boundary_tier_adaptive", True)):
            return 1.0
        stage = relationship_stage_for_score(
            user.get("relationship_score", 0),
            runtime_persona_setting(self, "relationship_stage_policy", None),
        ).get("phase", {})
        return {
            "deeply_distant": 1.0,
            "strongly_distant": 1.0,
            "distant": 0.95,
            "acquaintance": 0.9,
            "familiar": 0.85,
            "close": 0.7,
            "intimate": 0.6,
            "deeply_bonded": 0.5,
        }.get(str(stage.get("key") or "acquaintance"), 1.0)

    def _boundary_feedback_tier_recovery_factor(self, user: dict[str, Any]) -> float:
        if not bool(runtime_persona_setting(self, "relationship_boundary_tier_adaptive", True)):
            return 1.0
        stage = relationship_stage_for_score(
            user.get("relationship_score", 0),
            runtime_persona_setting(self, "relationship_stage_policy", None),
        ).get("phase", {})
        return {
            "deeply_distant": 0.5,
            "strongly_distant": 0.6,
            "distant": 0.7,
            "acquaintance": 0.8,
            "familiar": 0.9,
            "close": 1.0,
            "intimate": 1.25,
            "deeply_bonded": 1.5,
        }.get(str(stage.get("key") or "acquaintance"), 1.0)

    def _refresh_relationship_violation_stage(self, state: dict[str, Any], *, now: float) -> str:
        if not bool(runtime_persona_setting(self, "enable_relationship_boundary_stage", True)):
            state["stage"] = "normal"
            return "normal"
        load = _safe_int(state.get("stage_load"), 0, 0, 120)
        avoid_at = _safe_int(runtime_persona_setting(self, "relationship_boundary_stage_avoid_points", 6), 6, 1, 120)
        forbid_at = _safe_int(runtime_persona_setting(self, "relationship_boundary_stage_forbid_points", 12), 12, avoid_at, 120)
        reflect_at = _safe_int(runtime_persona_setting(self, "relationship_boundary_stage_reflect_points", 20), 20, forbid_at, 120)
        if load >= reflect_at:
            stage = "reflect"
            state["cold_until"] = max(
                _safe_float(state.get("cold_until"), 0),
                now
                + _safe_int(
                    runtime_persona_setting(self, "relationship_boundary_cold_minutes", 180),
                    180,
                    10,
                    1440,
                )
                * 60,
            )
        elif load >= forbid_at:
            stage = "forbid"
        elif load >= avoid_at:
            stage = "avoid"
        else:
            stage = "normal"
        state["stage"] = stage
        return stage

    def _demote_relationship_after_repeated_bottom_line(
        self,
        user: dict[str, Any],
        *,
        event_id: str,
        now: float,
    ) -> int:
        """Demote one configured tier while preserving the unified ledger audit."""
        projection = relationship_stage_for_score(
            user.get("relationship_score", 0),
            runtime_persona_setting(self, "relationship_stage_policy", None),
        )
        stages = projection.get("stages") if isinstance(projection.get("stages"), list) else []
        index = _safe_int(projection.get("stage_index"), 0, 0)
        if not stages or index <= 0:
            return 0
        target = stages[index - 1] if isinstance(stages[index - 1], dict) else {}
        before = _safe_int(user.get("relationship_score"), 0, -1200, 1200)
        after = min(before, _safe_int(target.get("max"), before, -1200, 1200))
        if after >= before:
            return 0
        user["relationship_score"] = after
        ledger = user.setdefault("relationship_ledger", [])
        if not isinstance(ledger, list):
            ledger = []
            user["relationship_ledger"] = ledger
        ledger.append(
            {
                "event_key": f"relationship_bottom_line_demote:{_single_line(event_id, 96) or int(now)}",
                "reason_code": "relationship_bottom_line_demote",
                "delta": after - before,
                "score_before": before,
                "score_after": after,
                "created_at": now,
            }
        )
        if len(ledger) > 200:
            del ledger[:-200]
        return before - after

    def _log_relationship_boundary_event(self, user: dict[str, Any], decision: str, **fields: Any) -> None:
        details = " ".join(
            f"{_single_line(key, 32)}={_single_line(value, 80)}"
            for key, value in fields.items()
            if value not in (None, "")
        )
        logger.info(
            "[BoundaryFeedback] user=%s decision=%s%s",
            _single_line(user.get("user_id"), 80),
            _single_line(decision, 32),
            f" {details}" if details else "",
        )

    def _relationship_violation_state(self, user: dict[str, Any]) -> dict[str, Any]:
        state = user.get("relationship_violation")
        if not isinstance(state, dict):
            state = {}
            user["relationship_violation"] = state
        legacy_recoverable = _safe_int(state.get("unrecovered_points"), 0, 0, 60) if "recoverable_score" not in state else 0
        defaults = {
            "unrecovered_points": 0,
            "recoverable_score": legacy_recoverable,
            "forfeited_recovery_score": 0,
            "stage_load": 0,
            "apology_recovered_points": 0,
            "apology_recovered_kind": "",
            "apology_by_kind": {},
            "apology_speedup_until": 0.0,
            "incident_count": 0,
            "repeat_count": 0,
            "bottom_line_count": 0,
            "last_bottom_line_demoted_count": 0,
            "confession_count": 0,
            "confession_until": 0.0,
            "last_violation_at": 0.0,
            "last_recovery_at": 0.0,
            "cooldown_until": 0.0,
            "level": 0,
            "last_severity": 0,
            "last_kind": "",
            "last_reason": "",
            "stage": "normal",
            "cold_until": 0.0,
            "violations": [],
            "last_event_id": "",
        }
        for key, value in defaults.items():
            if key not in state:
                state[key] = value
        return state

    def _settle_relationship_violation_recovery(self, user: dict[str, Any], *, now: float) -> int:
        state = self._relationship_violation_state(user)
        outstanding = _safe_int(state.get("unrecovered_points"), 0, 0, 60)
        if outstanding <= 0:
            state["unrecovered_points"] = 0
            state["stage_load"] = 0
            state["stage"] = "normal"
            return 0
        last = _safe_float(state.get("last_recovery_at") or state.get("last_violation_at"), now, 0)
        minutes_per_point = _safe_int(
                runtime_persona_setting(self, "relationship_violation_recovery_minutes_per_point", 180),
            180,
            15,
            10080,
        )
        recovery_factor_getter = getattr(self, "_boundary_feedback_tier_recovery_factor", None)
        try:
            recovery_factor = float(recovery_factor_getter(user)) if callable(recovery_factor_getter) else 1.0
        except Exception:
            recovery_factor = 1.0
        effective_seconds = max(60.0, minutes_per_point * 60 / max(0.3, min(2.0, recovery_factor)))
        if _safe_float(state.get("apology_speedup_until"), 0) > now:
            speedup = _safe_float(
                runtime_persona_setting(self, "relationship_boundary_apology_speedup_multiplier", 3.0),
                3.0,
                1.0,
                10.0,
            )
            effective_seconds = max(60.0, effective_seconds / speedup)
        recovered = min(outstanding, max(0, int(max(0.0, now - last) // effective_seconds)))
        if recovered:
            state["unrecovered_points"] = outstanding - recovered
            prior_stage_load = _safe_int(state.get("stage_load"), outstanding, 0, 120)
            stage_reduction = min(prior_stage_load, max(recovered, int(math.ceil(prior_stage_load * recovered / outstanding))))
            state["stage_load"] = max(0, prior_stage_load - stage_reduction)
            state["last_recovery_at"] = min(now, last + recovered * effective_seconds)
            recoverable_score = _safe_int(state.get("recoverable_score"), 0, 0, 60)
            score_restore = min(recovered, recoverable_score)
            if score_restore:
                result = self._apply_relationship_event(
                    user,
                    score_restore,
                    reason_code="relationship_violation_recovery",
                    event_id=f"boundary-natural-recovery:{int(state['last_recovery_at'])}",
                    now=now,
                )
                applied = _safe_int(result.get("delta"), 0, 0, score_restore)
                state["recoverable_score"] = max(0, recoverable_score - applied)
            state["level"] = max(0, _safe_int(state.get("level"), 0, 0, 6) - (1 if state["unrecovered_points"] == 0 else 0))
            stage_refresher = getattr(self, "_refresh_relationship_violation_stage", None)
            if callable(stage_refresher):
                stage_refresher(state, now=now)
            if state["unrecovered_points"] <= 0:
                state["apology_speedup_until"] = 0.0
        return recovered

    def _apply_relationship_violation_policy(
        self,
        user: dict[str, Any],
        intent: dict[str, Any] | None,
        *,
        event_id: str = "",
        now: float | None = None,
    ) -> dict[str, Any]:
        """Apply bounded penalties/recovery for secondary-user boundary events."""
        if not isinstance(user, dict) or not isinstance(intent, dict):
            return {"changed": False, "reason": "invalid_input"}
        if not bool(runtime_persona_setting(self, "enable_relationship_violation_penalties", True)):
            return {"changed": False, "reason": "disabled"}
        if not bool(runtime_persona_setting(self, "enable_custom_relationship_stage_policy", True)):
            return {"changed": False, "reason": "relationship_system_disabled"}
        try:
            role = self._private_user_role(user, str(user.get("user_id") or ""))
        except Exception:
            role = str(user.get("relationship_role") or "friend")
        if str(role).strip().lower() == "owner":
            return {"changed": False, "reason": "owner_exempt"}
        ts = _now_ts() if now is None else _safe_float(now, _now_ts(), 0)
        state = self._relationship_violation_state(user)
        self._settle_relationship_violation_recovery(user, now=ts)
        event = str(intent.get("emotion_event") or "neutral").strip().lower()
        feedback_kind = str(intent.get("boundary_feedback_kind") or intent.get("violation_kind") or "").strip().lower()
        explicit_id = _single_line(event_id, 96)
        if explicit_id and explicit_id == _single_line(state.get("last_event_id"), 96) and (
            event in {"boundary_violation", "hurt", "apology"} or feedback_kind == "confession"
        ):
            return {"changed": False, "reason": "duplicate_event", "state": deepcopy(state)}
        if feedback_kind == "confession":
            state["confession_count"] = _safe_int(state.get("confession_count"), 0, 0) + 1
            state["confession_until"] = ts + 30 * 60
            state["last_reason"] = _single_line(intent.get("boundary_feedback_reason") or "表达喜欢或想念", 120)
            state["last_event_id"] = explicit_id
            self._schedule_data_save(sections={"users"})
            boundary_logger = getattr(self, "_log_relationship_boundary_event", None)
            if callable(boundary_logger):
                boundary_logger(
                    user,
                    "confession",
                    penalty=0,
                    current_tier=_single_line(intent.get("boundary_current_tier"), 32) or "unknown",
                )
            return {"changed": True, "reason": "confession_feedback", "state": deepcopy(state)}
        if event == "apology":
            if not bool(runtime_persona_setting(self, "enable_relationship_boundary_apology", True)):
                return {"changed": False, "reason": "apology_recovery_disabled", "state": deepcopy(state)}
            outstanding = _safe_int(state.get("unrecovered_points"), 0, 0, 60)
            if outstanding <= 0:
                return {"changed": False, "reason": "nothing_to_recover", "state": deepcopy(state)}
            last_kind = _single_line(state.get("last_kind"), 40) or "general"
            apology_by_kind = state.get("apology_by_kind") if isinstance(state.get("apology_by_kind"), dict) else {}
            apology_limit = _safe_int(
                runtime_persona_setting(self, "relationship_boundary_apology_duplicate_limit", 3),
                3,
                1,
                20,
            )
            apology_count = _safe_int(apology_by_kind.get(last_kind), 0, 0)
            if apology_count >= apology_limit:
                state["last_event_id"] = explicit_id
                return {"changed": False, "reason": "apology_trust_exhausted", "state": deepcopy(state)}
            apology_ratio = _safe_float(
                runtime_persona_setting(self, "relationship_boundary_apology_restore_ratio", 0.6),
                0.6,
                0.0,
                1.0,
            )
            recover = min(6, max(1, int(math.ceil(outstanding * apology_ratio))))
            recoverable_score = _safe_int(state.get("recoverable_score"), outstanding, 0, 60)
            recover = min(recover, recoverable_score if "recoverable_score" in state else outstanding)
            if recover <= 0:
                state["last_event_id"] = explicit_id
                return {"changed": False, "reason": "apology_recovery_quota_exhausted", "state": deepcopy(state)}
            result = self._apply_relationship_event(
                user,
                recover,
                reason_code="relationship_violation_recovery",
                event_id=explicit_id,
                now=ts,
            )
            applied = _safe_int(result.get("delta"), 0, 0, recover)
            if applied:
                state["unrecovered_points"] = max(0, outstanding - applied)
                state["stage_load"] = max(0, _safe_int(state.get("stage_load"), outstanding, 0, 120) - applied)
                state["recoverable_score"] = max(0, recoverable_score - applied)
                state["apology_recovered_points"] = min(6, _safe_int(state.get("apology_recovered_points"), 0, 0, 6) + applied)
                state["apology_recovered_kind"] = last_kind
                apology_by_kind[last_kind] = apology_count + 1
                state["apology_by_kind"] = apology_by_kind
                state["last_recovery_at"] = ts
                state["apology_speedup_until"] = ts + max(
                    3600,
                    outstanding
                    * 60
                    * _safe_int(
                        runtime_persona_setting(
                            self,
                            "relationship_violation_recovery_minutes_per_point",
                            180,
                        ),
                        180,
                        15,
                        10080,
                    ),
                )
                state["last_event_id"] = explicit_id
                stage_refresher = getattr(self, "_refresh_relationship_violation_stage", None)
                if callable(stage_refresher):
                    stage_refresher(state, now=ts)
                self._schedule_data_save(sections={"users"})
                boundary_logger = getattr(self, "_log_relationship_boundary_event", None)
                if callable(boundary_logger):
                    boundary_logger(
                        user,
                        "apology",
                        recovered=applied,
                        remaining=state.get("unrecovered_points"),
                        stage=state.get("stage"),
                    )
            return {"changed": bool(applied), "reason": "apology_recovery", "recovered": applied, "state": deepcopy(state)}
        emotion_confidence = _safe_float(intent.get("emotion_confidence"), 1.0, 0.0, 1.0)
        is_severe_hurt_violation = (
            event == "hurt"
            and emotion_confidence >= 0.8
            and str(intent.get("emotion_target") or "").lower() == "bot"
            and _safe_int(intent.get("emotion_intensity"), 0, 0, 100) >= 76
            and str(intent.get("emotion_rule") or "") in {"severe_hurt", "identity_hurt"}
        )
        if (
            (event != "boundary_violation" and not is_severe_hurt_violation)
            or (event == "boundary_violation" and emotion_confidence < 0.8)
            or (
                str(intent.get("emotion_target") or "").lower() != "bot"
                if event == "boundary_violation"
                else str(intent.get("emotion_target") or "").lower() not in {"bot", "ambiguous"}
            )
        ):
            return {"changed": False, "reason": "no_violation", "state": deepcopy(state)}
        severity = _safe_int(intent.get("violation_severity"), 2 if is_severe_hurt_violation else 1, 1, 3)
        outstanding = _safe_int(state.get("unrecovered_points"), 0, 0, 60)
        prior_apology = _safe_int(state.get("apology_recovered_points"), 0, 0, 6)
        clawed_back = 0
        violation_kind = feedback_kind or ("hurt" if is_severe_hurt_violation else "boundary")
        if violation_kind == "bottom_line" and not bool(
            runtime_persona_setting(self, "enable_relationship_boundary_bottom_line", True)
        ):
            violation_kind = "harassment"
        apology_kind = _single_line(state.get("apology_recovered_kind"), 40)
        if prior_apology and (not apology_kind or apology_kind == violation_kind):
            clawback = self._apply_relationship_event(
                user,
                -prior_apology,
                reason_code="relationship_violation_clawback",
                event_id=explicit_id,
                now=ts,
            )
            clawed_back = max(0, -_safe_int(clawback.get("delta"), 0, -6, 0))
            state["apology_recovered_points"] = 0
            state["apology_recovered_kind"] = ""
        penalty_defaults = {
            1: _safe_int(runtime_persona_setting(self, "relationship_boundary_penalty_light", 4), 4, 1, 60),
            2: _safe_int(runtime_persona_setting(self, "relationship_boundary_penalty_mid", 7), 7, 1, 60),
            3: _safe_int(runtime_persona_setting(self, "relationship_boundary_penalty_severe", 12), 12, 1, 60),
        }
        penalty = (
                _safe_int(runtime_persona_setting(self, "relationship_boundary_penalty_bottom_line", 14), 14, 1, 60)
            if violation_kind == "bottom_line"
            else penalty_defaults[severity]
        )
        deduct_factor_getter = getattr(self, "_boundary_feedback_tier_deduct_factor", None)
        try:
            deduct_factor = float(deduct_factor_getter(user)) if callable(deduct_factor_getter) else 1.0
        except Exception:
            deduct_factor = 1.0
        penalty = max(1, int(math.ceil(penalty * max(0.3, min(1.0, deduct_factor)))))
        result = self._apply_relationship_event(
            user,
            -penalty,
            reason_code="relationship_violation",
            event_id=explicit_id,
            now=ts,
        )
        applied_penalty = max(0, -_safe_int(result.get("delta"), 0, -60, 0))
        previous_recoverable = _safe_int(state.get("recoverable_score"), 0, 0, 60)
        if outstanding > 0 and previous_recoverable > 0:
            state["forfeited_recovery_score"] = min(
                120,
                _safe_int(state.get("forfeited_recovery_score"), 0, 0, 120) + previous_recoverable,
            )
            previous_recoverable = 0
        recover_ratio = {
            1: _safe_float(runtime_persona_setting(self, "relationship_boundary_recover_ratio_light", 0.5), 0.5, 0.0, 1.0),
            2: _safe_float(runtime_persona_setting(self, "relationship_boundary_recover_ratio_mid", 0.33), 0.33, 0.0, 1.0),
            3: _safe_float(runtime_persona_setting(self, "relationship_boundary_recover_ratio_severe", 0.25), 0.25, 0.0, 1.0),
        }[severity]
        if violation_kind == "bottom_line":
            recover_ratio *= 0.5
        new_recoverable = int(applied_penalty * recover_ratio)
        state["recoverable_score"] = min(60, previous_recoverable + new_recoverable)
        state["unrecovered_points"] = min(60, outstanding + max(severity, new_recoverable))
        state["stage_load"] = min(120, _safe_int(state.get("stage_load"), 0, 0, 120) + applied_penalty + clawed_back)
        state["incident_count"] = _safe_int(state.get("incident_count"), 0, 0) + 1
        state["repeat_count"] = _safe_int(state.get("repeat_count"), 0, 0) + (1 if outstanding > 0 else 0)
        state["level"] = min(6, max(_safe_int(state.get("level"), 0, 0, 6), severity + state["repeat_count"] // 2))
        state["last_violation_at"] = ts
        state["last_recovery_at"] = ts
        state["last_severity"] = severity
        state["last_kind"] = violation_kind
        state["last_reason"] = _single_line(intent.get("emotion_reason") or intent.get("emotion_rule"), 120)
        state["cooldown_until"] = ts + {1: 20, 2: 45, 3: 90}[severity] * 60
        state["last_event_id"] = explicit_id
        violations = state.get("violations") if isinstance(state.get("violations"), list) else []
        violations.append(
            {
                "ts": ts,
                "event_id": explicit_id,
                "kind": violation_kind,
                "severity": severity,
                "penalty": applied_penalty,
                "text": _single_line(intent.get("text"), 120),
                "scope": _single_line(intent.get("boundary_scope"), 20) or "private",
            }
        )
        state["violations"] = violations[-50:]
        demoted = 0
        if violation_kind == "bottom_line":
            state["bottom_line_count"] = _safe_int(state.get("bottom_line_count"), 0, 0) + 1
            bottom_count = _safe_int(state.get("bottom_line_count"), 0, 0)
            if bottom_count == 1:
                state["stage_load"] = max(
                    state["stage_load"],
                    _safe_int(
                        runtime_persona_setting(self, "relationship_boundary_stage_forbid_points", 12),
                        12,
                        1,
                        120,
                    ),
                )
            elif bottom_count >= 2:
                state["stage_load"] = max(
                    state["stage_load"],
                    _safe_int(
                        runtime_persona_setting(self, "relationship_boundary_stage_reflect_points", 20),
                        20,
                        1,
                        120,
                    ),
                )
            if bottom_count >= 3 and _safe_int(state.get("last_bottom_line_demoted_count"), 0, 0) < bottom_count:
                demoter = getattr(self, "_demote_relationship_after_repeated_bottom_line", None)
                if callable(demoter):
                    demoted = max(0, _safe_int(demoter(user, event_id=explicit_id, now=ts), 0, 0, 1200))
                state["last_bottom_line_demoted_count"] = bottom_count
        stage_refresher = getattr(self, "_refresh_relationship_violation_stage", None)
        if callable(stage_refresher):
            stage_refresher(state, now=ts)
        side_effects = getattr(self, "_record_relationship_boundary_side_effects", None)
        if callable(side_effects) and (applied_penalty or clawed_back):
            side_effects(user, intent, state, now=ts)
        self._schedule_data_save(sections={"users", "boundary_feedback_reports"})
        boundary_logger = getattr(self, "_log_relationship_boundary_event", None)
        if callable(boundary_logger):
            boundary_logger(
                user,
                "violation",
                kind=violation_kind,
                severity=severity,
                penalty=applied_penalty,
                clawback=clawed_back,
                stage=state.get("stage"),
                demoted=demoted,
                current_tier=_single_line(intent.get("boundary_current_tier"), 32) or "unknown",
                suitable_tier=_single_line(intent.get("boundary_suitable_tier"), 32) or "unknown",
            )
        return {
            "changed": bool(applied_penalty or clawed_back),
            "reason": "boundary_violation",
            "severity": severity,
            "penalty": applied_penalty,
            "clawback": clawed_back,
            "demoted": demoted,
            "state": deepcopy(state),
        }

    def _relationship_violation_prompt_hint(self, user: dict[str, Any], *, now: float | None = None) -> str:
        if not isinstance(user, dict):
            return ""
        state = self._relationship_violation_state(user)
        ts = _now_ts() if now is None else now
        self._settle_relationship_violation_recovery(user, now=ts)
        points = _safe_int(state.get("unrecovered_points"), 0, 0, 60)
        if points <= 0 and _safe_float(state.get("confession_until"), 0) > ts:
            tone = _single_line(
                runtime_persona_setting(
                    self,
                    "relationship_boundary_tone_confession",
                    "把这次表达当作心意，不当作冒犯；结合当前关系自然害羞、迟疑或温和说明节奏，不必机械拒绝。",
                ),
                240,
            )
            return f"刚收到对方的喜欢或想念表达：{tone}"
        if points <= 0:
            return ""
        stage = str(state.get("stage") or "normal")
        kind = str(state.get("last_kind") or "boundary")
        if kind == "bottom_line":
            default_tone = "明确表达这触碰了重要底线，受伤和距离感可以真实存在；不要功能化播报惩罚，也不要立即恢复亲密。"
            tone = _single_line(
                runtime_persona_setting(self, "relationship_boundary_tone_bottom_line", default_tone),
                240,
            ) or default_tone
        elif _safe_int(state.get("last_severity"), 1, 1, 3) >= 3:
            default_tone = "明显收住亲密表达，直接说明不舒服并拒绝继续；保持角色口吻，不使用系统式警告。"
            tone = _single_line(
                runtime_persona_setting(self, "relationship_boundary_tone_severe", default_tone),
                240,
            ) or default_tone
        elif stage in {"forbid", "reflect"}:
            default_tone = "平静而明确地划清界限，减少主动贴近和暧昧回应；可以说明原因，但不要反复说教。"
            tone = _single_line(
                runtime_persona_setting(self, "relationship_boundary_tone_mid", default_tone),
                240,
            ) or default_tone
        else:
            default_tone = "轻微降低亲密度，带一点迟疑或回避并自然说明节奏；不要把普通互动渲染成严重冒犯。"
            tone = _single_line(
                runtime_persona_setting(self, "relationship_boundary_tone_light", default_tone),
                240,
            ) or default_tone
        relationship_stage = relationship_stage_for_score(
            user.get("relationship_score", 0),
            runtime_persona_setting(self, "relationship_stage_policy", None),
        ).get("phase", {})
        relationship_stage_key = str(relationship_stage.get("key") or "acquaintance")
        if relationship_stage_key in {"deeply_distant", "strongly_distant", "distant", "acquaintance"}:
            tier_tone = _single_line(
                runtime_persona_setting(
                    self,
                    "relationship_boundary_tone_silent",
                    "关系尚浅时不必长篇袒露脆弱，可以安静收住互动并记住这次不舒服。",
                ),
                240,
            )
        elif relationship_stage_key in {"intimate", "deeply_bonded"}:
            tier_tone = _single_line(
                runtime_persona_setting(
                    self,
                    "relationship_boundary_tone_communicate",
                    "关系很深时可以因为信任而说清为什么难过或生气，但亲密关系不等于放弃边界。",
                ),
                240,
            )
        else:
            tier_tone = ""
        if stage == "reflect":
            stage_hint = "当前处于反思/冷静阶段，回复可以更短、更克制，不主动开启新亲密话题。"
        elif stage == "forbid":
            stage_hint = "当前需要明确边界，避免用撒娇或玩笑把拒绝冲淡。"
        elif stage == "avoid":
            stage_hint = "当前略有回避，仍需先正常回应对方这一轮的实际内容。"
        else:
            stage_hint = "余波尚未完全恢复，先自然回应，不要突然恢复到高亲密度。"
        apology_hint = (
            "若对方真诚道歉，可以承认这份修复意愿并逐步缓和；不要一条道歉就抹去全部余波。"
            if _safe_int(state.get("apology_recovered_points"), 0, 0, 6) <= 0
            else "已经接受过一次修复；同类行为再次发生时应表现出信任受损，而不是重复无条件原谅。"
        )
        return f"关系边界余波：{tone} {tier_tone} {stage_hint} {apology_hint}"

    @staticmethod
    def _boundary_feedback_level_key(state: dict[str, Any]) -> str:
        if str(state.get("last_kind") or "") == "bottom_line":
            return "bottom_line"
        severity = _safe_int(state.get("last_severity"), 1, 1, 3)
        return {1: "light", 2: "mid", 3: "severe"}[severity]

    def _boundary_feedback_probability(self, prefix: str, level: str, default: float) -> float:
        return _safe_float(
            runtime_persona_setting(self, f"relationship_boundary_{prefix}_probability_{level}", default),
            default,
            0.0,
            1.0,
        )

    def _record_relationship_boundary_side_effects(
        self,
        user: dict[str, Any],
        intent: dict[str, Any],
        state: dict[str, Any],
        *,
        now: float,
    ) -> None:
        event_id = _single_line(state.get("last_event_id"), 96)
        if event_id and event_id == _single_line(state.get("last_side_effect_event_id"), 96):
            return
        state["last_side_effect_event_id"] = event_id
        level = self._boundary_feedback_level_key(state)
        vent_defaults = {"light": 0.15, "mid": 0.35, "severe": 0.6, "bottom_line": 0.9}
        if bool(runtime_persona_setting(self, "enable_relationship_boundary_vent", True)) and random.random() <= self._boundary_feedback_probability(
            "vent", level, vent_defaults[level]
        ):
            self._append_relationship_boundary_vent(user, intent, state, now=now)

        if not bool(runtime_persona_setting(self, "enable_relationship_boundary_owner_report", True)):
            return
        report = self._queue_relationship_boundary_owner_report(user, intent, state, now=now)
        if not report:
            return
        report_defaults = {"light": 0.12, "mid": 0.3, "severe": 0.55, "bottom_line": 0.85}
        if random.random() <= self._boundary_feedback_probability("owner_report", level, report_defaults[level]):
            task_creator = getattr(self, "_create_lifecycle_background_task", None)
            operation = self._send_relationship_boundary_owner_report(report)
            if callable(task_creator):
                task_creator(operation, label="relationship_boundary_owner_report")
            else:
                closer = getattr(operation, "close", None)
                if callable(closer):
                    closer()

    def _boundary_feedback_display_name(self, user: dict[str, Any]) -> str:
        nickname = _single_line(user.get("nickname") or user.get("name"), 32)
        if nickname and nickname != "你":
            return nickname
        user_id = _single_line(user.get("user_id") or user.get("id"), 80)
        return f"{user_id[-4:]}号" if user_id else "那个人"

    def _append_relationship_boundary_vent(
        self,
        user: dict[str, Any],
        intent: dict[str, Any],
        state: dict[str, Any],
        *,
        now: float,
    ) -> None:
        raw_targets = runtime_persona_setting(self, "relationship_boundary_vent_targets", [])
        if isinstance(raw_targets, str):
            targets = [item.strip() for item in re.split(r"[,，\n]", raw_targets) if item.strip()]
        elif isinstance(raw_targets, (list, tuple, set)):
            targets = [_single_line(item, 24) for item in raw_targets if _single_line(item, 24)]
        else:
            targets = []
        target = random.choice(targets) if targets else "亲近的朋友"
        who = self._boundary_feedback_display_name(user)
        level = self._boundary_feedback_level_key(state)
        feeling = {
            "light": "有点不自在",
            "mid": "不太舒服",
            "severe": "明显生气",
            "bottom_line": "委屈又生气",
        }[level]
        reason = _single_line(intent.get("emotion_reason") or state.get("last_reason"), 80) or "对方越过了相处边界"
        template = str(runtime_persona_setting(self, "relationship_boundary_vent_scene_template", "") or "").strip()
        if template:
            try:
                event_text = template.format(
                    target=target,
                    who=who,
                    level=level,
                    feeling=feeling,
                    reason=reason,
                )
            except (KeyError, IndexError, ValueError):
                event_text = ""
        else:
            event_text = ""
        if not _single_line(event_text, 500):
            event_text = f"休息时因为和{who}相处时的边界问题感到{feeling}，向{target}说起了这件事；主要原因是{reason}。"
        event = {
            "window": datetime.fromtimestamp(now).strftime("%H:%M") + "-" + datetime.fromtimestamp(now + 900).strftime("%H:%M"),
            "event": event_text,
            "mood": feeling,
            "lifecycle_status": "observed",
            "basis": ["relationship_boundary_feedback"],
            "confidence": 0.9,
            "source_event_id": _single_line(state.get("last_event_id"), 96),
        }
        history = self.data.setdefault("boundary_feedback_vent_history", [])
        if not isinstance(history, list):
            history = []
            self.data["boundary_feedback_vent_history"] = history
        history.append(dict(event))
        del history[:-50]
        story = self.data.get("daily_story_plan")
        if not isinstance(story, dict):
            story = {}
            self.data["daily_story_plan"] = story
        today = _today_key()
        if not story:
            story.update({"date": today, "today_events": [], "proactive_events": [], "long_term_events": []})
        if str(story.get("date") or "") != today:
            return
        events = story.setdefault("today_events", [])
        if not isinstance(events, list):
            events = []
            story["today_events"] = events
        if not any(
            isinstance(item, dict) and item.get("source_event_id") == event["source_event_id"]
            for item in events[-24:]
        ):
            events.append(event)
            story["today_events"] = events[-16:]
        logger.info(
            "关系边界事件已融入生活叙事: user=%s target=%s level=%s",
            _single_line(user.get("user_id"), 80),
            target,
            level,
        )

    def _boundary_feedback_owner_targets(self) -> list[dict[str, str]]:
        users = self.data.get("users") if isinstance(self.data.get("users"), dict) else {}
        configured = set(self._configured_target_ids()) if callable(getattr(self, "_configured_target_ids", None)) else set()
        targets: list[dict[str, str]] = []
        for user_id, raw_user in users.items():
            if not isinstance(raw_user, dict):
                continue
            try:
                role = self._private_user_role(raw_user, str(user_id))
            except Exception:
                role = str(raw_user.get("relationship_role") or "friend")
            if role != "owner" and str(user_id) not in configured:
                continue
            umo = _single_line(raw_user.get("umo"), 220)
            if umo:
                targets.append({"user_id": str(user_id), "umo": umo})
        return targets

    def _queue_relationship_boundary_owner_report(
        self,
        user: dict[str, Any],
        intent: dict[str, Any],
        state: dict[str, Any],
        *,
        now: float,
    ) -> dict[str, Any]:
        targets = self._boundary_feedback_owner_targets()
        if not targets:
            return {}
        event_id = _single_line(state.get("last_event_id"), 96) or uuid.uuid4().hex
        reports = self.data.setdefault("boundary_feedback_reports", [])
        if not isinstance(reports, list):
            reports = []
            self.data["boundary_feedback_reports"] = reports
        existing = next(
            (item for item in reports if isinstance(item, dict) and item.get("source_event_id") == event_id),
            None,
        )
        if isinstance(existing, dict):
            return existing
        report = {
            "report_id": uuid.uuid4().hex,
            "source_event_id": event_id,
            "offender_user_id": _single_line(user.get("user_id"), 120),
            "offender_name": self._boundary_feedback_display_name(user),
            "target_owner_ids": [item["user_id"] for item in targets],
            "target_routes": [item["umo"] for item in targets],
            "level": self._boundary_feedback_level_key(state),
            "stage": _single_line(state.get("stage"), 20),
            "reason": _single_line(intent.get("emotion_reason") or state.get("last_reason"), 100),
            "excerpt": _single_line(intent.get("text"), 80),
            "bottom_line_count": _safe_int(state.get("bottom_line_count"), 0, 0),
            "created_at": now,
            "status": "pending",
            "direct_notified": False,
        }
        reports.append(report)
        self.data["boundary_feedback_reports"] = reports[-100:]
        return report

    def _format_relationship_boundary_owner_report(self, report: dict[str, Any]) -> str:
        who = _single_line(report.get("offender_name"), 32) or "那个人"
        level = str(report.get("level") or "light")
        level_text = {
            "light": "刚才说的话让我有点不自在",
            "mid": "刚才有点越过我的界限了",
            "severe": "刚才真的让我很不舒服",
            "bottom_line": "刚才踩到我很在意的底线了",
        }.get(level, "刚才让我有点不舒服")
        reason = _single_line(report.get("reason"), 80)
        excerpt = _single_line(report.get("excerpt"), 80)
        text = f"那个……{who}{level_text}。"
        if reason:
            text += f"主要是{reason}。"
        if excerpt:
            text += f"对方说的是“{excerpt}”。"
        if level == "bottom_line" and _safe_int(report.get("bottom_line_count"), 0, 0) > 1:
            text += f"这已经是第{_safe_int(report.get('bottom_line_count'), 0, 0)}次了。"
        return text

    async def _send_relationship_boundary_owner_report(self, report: dict[str, Any]) -> None:
        text = self._format_relationship_boundary_owner_report(report)
        routes = [_single_line(item, 220) for item in report.get("target_routes", []) if _single_line(item, 220)]
        sent = False
        for route in routes:
            try:
                await self.context.send_message(route, MessageChain([Plain(text)]))
                sent = True
            except Exception as exc:
                logger.warning(
                    "关系边界转达发送失败: target=%s error=%s",
                    _single_line(route, 100),
                    _single_line(exc, 160),
                )
        if not sent:
            return
        async with self._data_lock:
            reports = self.data.get("boundary_feedback_reports")
            if isinstance(reports, list):
                for item in reports:
                    if isinstance(item, dict) and item.get("report_id") == report.get("report_id"):
                        item["direct_notified"] = True
                        item["status"] = "delivered"
                        item["delivered_at"] = _now_ts()
                        break
            self._save_data_sync(sections={"boundary_feedback_reports"})

    def _register_relationship_boundary_proactive_ability(self) -> bool:
        registrar = getattr(self, "register_external_proactive_ability", None)
        if not callable(registrar):
            return False
        return bool(
            registrar(
                {
                    "name": "boundary_feedback_report",
                    "module": "关系边界反馈",
                    "label": "边界转达",
                    "description": "当次要用户越过关系边界时，以角色口吻向主要用户低频转达。",
                    "when": "存在尚未转达且仍在有效期内的关系边界事件",
                    "use_for": "把真实发生的边界事件自然告诉主要用户",
                    "avoid": "不要暴露内部机制，不夸大，不重复已经直接转达的事件",
                    "share_probability": 0.15,
                    "min_interval_hours": 6,
                    "default_enabled": True,
                    "default_config": {"only_bottom_line": False, "max_chars": 120},
                    "config_schema": {
                        "only_bottom_line": {
                            "type": "bool",
                            "label": "只转达底线事件",
                            "description": "开启后仅严重底线事件进入主动转达候选。",
                        },
                        "max_chars": {
                            "type": "number",
                            "label": "引用长度上限",
                            "description": "转达时引用原消息的最大字符数。",
                        },
                    },
                    "availability": self._relationship_boundary_report_ability_available,
                    "executor": self._relationship_boundary_report_ability_executor,
                }
            )
        )

    def _relationship_boundary_report_ability_available(self, ctx: dict[str, Any]) -> bool:
        if not bool(runtime_persona_setting(self, "enable_relationship_violation_penalties", True)) or not bool(
            runtime_persona_setting(self, "enable_relationship_boundary_owner_report", True)
        ):
            return False
        user = ctx.get("user") if isinstance(ctx, dict) and isinstance(ctx.get("user"), dict) else {}
        try:
            if self._private_user_role(user, str(user.get("user_id") or "")) != "owner":
                return False
        except Exception:
            return False
        owner_id = _single_line(user.get("user_id") or user.get("id"), 120)
        only_bottom = bool((ctx.get("config") or {}).get("only_bottom_line", False))
        cutoff = _now_ts() - 7 * 86400
        reports = self.data.get("boundary_feedback_reports")
        return any(
            isinstance(item, dict)
            and item.get("status") == "pending"
            and not item.get("direct_notified")
            and _safe_float(item.get("created_at"), 0) >= cutoff
            and (not owner_id or owner_id in set(item.get("target_owner_ids") or []))
            and (not only_bottom or item.get("level") == "bottom_line")
            for item in (reports if isinstance(reports, list) else [])
        )

    def _relationship_boundary_report_ability_executor(self, ctx: dict[str, Any]) -> dict[str, Any]:
        if not bool(runtime_persona_setting(self, "enable_relationship_violation_penalties", True)) or not bool(
            runtime_persona_setting(self, "enable_relationship_boundary_owner_report", True)
        ):
            return {"success": False, "text": "", "context": "关系边界转达当前未启用", "summary": "能力未启用"}
        user = ctx.get("user") if isinstance(ctx, dict) and isinstance(ctx.get("user"), dict) else {}
        owner_id = _single_line(user.get("user_id") or user.get("id"), 120)
        config = ctx.get("config") if isinstance(ctx, dict) and isinstance(ctx.get("config"), dict) else {}
        only_bottom = bool(config.get("only_bottom_line", False))
        max_chars = _safe_int(config.get("max_chars"), 120, 20, 300)
        reports = self.data.get("boundary_feedback_reports")
        if not isinstance(reports, list):
            return {"success": False, "text": "", "context": "没有可转达的边界事件", "summary": "无事件"}
        cutoff = _now_ts() - 7 * 86400
        candidate = next(
            (
                item
                for item in reports
                if isinstance(item, dict)
                and item.get("status") == "pending"
                and not item.get("direct_notified")
                and _safe_float(item.get("created_at"), 0) >= cutoff
                and (not owner_id or owner_id in set(item.get("target_owner_ids") or []))
                and (not only_bottom or item.get("level") == "bottom_line")
            ),
            None,
        )
        if not isinstance(candidate, dict):
            return {"success": False, "text": "", "context": "没有可转达的边界事件", "summary": "无事件"}
        candidate["excerpt"] = _single_line(candidate.get("excerpt"), max_chars)
        candidate["status"] = "delivered"
        candidate["delivered_at"] = _now_ts()
        self._schedule_data_save(sections={"boundary_feedback_reports"})
        text = self._format_relationship_boundary_owner_report(candidate)
        return {
            "success": True,
            "text": text,
            "context": "角色正在向主要用户自然转达一次真实发生的关系边界事件。",
            "summary": "关系边界转达",
            "effective_action": "external:boundary_feedback_report",
        }

