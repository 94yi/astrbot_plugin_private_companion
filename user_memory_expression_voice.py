# -*- coding: utf-8 -*-
"""表达声线与规则场景装配。

由 tools/split_mixin_domain.py 从 user_memory.py 机械抽取（38 个方法 + 0 个模块级名字 + 0 个类级赋值 / 1089 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 UserMemoryMixin）。
"""
from __future__ import annotations

import hashlib
import re
from .conversation_prompt_section import PromptSection, prompt_section
from .expression_scope_ownership import bind_expression_item, bind_expression_profile
from .helpers import _now_ts, _safe_float, _safe_int, _single_line, _strip_internal_message_blocks
from .persona_config import runtime_persona_setting
from .scoped_runtime_view import scoped_approved_expression_rules
from .user_memory_render_shared import _render_conversation_section_labeled
from copy import deepcopy
from datetime import datetime
from typing import Any



class UserMemoryExpressionVoiceMixin:
    """表达声线与规则场景装配（从 UserMemoryMixin 拆出）。"""


    def _expression_scope_mode(self, key: str, allowed: set[str], default: str) -> str:
        value = str(runtime_persona_setting(self, key, default) or default).strip().lower()
        return value if value in allowed else default

    def _expression_scope_ids(self, key: str, *, group: bool = False) -> set[str]:
        raw = runtime_persona_setting(self, key, [])
        parser = getattr(self, "_parse_group_id_list" if group else "_parse_text_list_config", None)
        try:
            values = parser(raw) if callable(parser) else (raw if isinstance(raw, list) else [])
        except Exception:
            values = raw if isinstance(raw, list) else []
        normalized: set[str] = set()
        for item in values:
            value = _single_line(item, 80)
            if not value:
                continue
            if not group:
                value = self._expression_private_scope_id(value)
            if value:
                normalized.add(value)
        return normalized

    def _expression_private_scope_id(self, user_id: Any) -> str:
        """Normalize configured private IDs so aliases follow the same identity boundary."""
        value = _single_line(user_id, 80)
        normalizer = getattr(self, "_canonical_private_user_id", None)
        if value and callable(normalizer):
            try:
                value = _single_line(normalizer(value), 80) or value
            except Exception:
                pass
        return value

    def _expression_private_learning_source_enabled(self, user: dict[str, Any], user_id: Any = "") -> bool:
        mode = self._expression_scope_mode(
            "expression_private_learning_source_mode",
            {"owner", "selected", "all"},
            "owner",
        )
        user_id = self._expression_private_scope_id(user_id or user.get("user_id"))
        if mode == "all":
            return True
        if mode == "selected":
            return bool(user_id and user_id in self._expression_scope_ids("expression_private_learning_source_ids"))
        role_getter = getattr(self, "_private_user_role", None)
        try:
            role = role_getter(user, user_id) if callable(role_getter) else str(user.get("relationship_role") or "")
        except Exception:
            role = str(user.get("relationship_role") or "")
        return str(role or "").strip().lower() == "owner"

    def _expression_group_learning_source_enabled(self, group_id: Any) -> bool:
        mode = self._expression_scope_mode(
            "expression_group_learning_source_mode",
            {"disabled", "selected", "all"},
            "disabled",
        )
        group_id = _single_line(group_id, 80)
        if mode == "all":
            return bool(group_id)
        if mode == "selected":
            return bool(group_id and group_id in self._expression_scope_ids("expression_group_learning_source_ids", group=True))
        return False

    def _expression_private_application_enabled(self, user_id: Any) -> bool:
        mode = self._expression_scope_mode(
            "expression_private_application_mode",
            {"all", "selected"},
            "all",
        )
        user_id = self._expression_private_scope_id(user_id)
        return mode == "all" or bool(user_id and user_id in self._expression_scope_ids("expression_private_application_user_ids"))

    def _expression_group_application_enabled(self, group_id: Any) -> bool:
        mode = self._expression_scope_mode(
            "expression_group_application_mode",
            {"disabled", "all", "selected"},
            "all",
        )
        group_id = _single_line(group_id, 80)
        if mode == "all":
            return bool(group_id)
        if mode == "selected":
            return bool(group_id and group_id in self._expression_scope_ids("expression_group_application_ids", group=True))
        return False

    def _expression_scope_signature(self) -> str:
        parts = [
            self._expression_scope_mode("expression_private_learning_source_mode", {"owner", "selected", "all"}, "owner"),
            ",".join(sorted(self._expression_scope_ids("expression_private_learning_source_ids"))),
            self._expression_scope_mode("expression_group_learning_source_mode", {"disabled", "selected", "all"}, "disabled"),
            ",".join(sorted(self._expression_scope_ids("expression_group_learning_source_ids", group=True))),
            self._expression_scope_mode("expression_private_application_mode", {"all", "selected"}, "all"),
            ",".join(sorted(self._expression_scope_ids("expression_private_application_user_ids"))),
            self._expression_scope_mode("expression_group_application_mode", {"disabled", "all", "selected"}, "all"),
            ",".join(sorted(self._expression_scope_ids("expression_group_application_ids", group=True))),
        ]
        return "|".join(parts)

    @staticmethod
    def _expression_voice_actions(
        sample_count: int,
        short_ratio: float,
        feature_counts: dict[str, Any],
        *,
        limit: int = 3,
    ) -> list[str]:
        if sample_count < 2:
            return []
        actions: list[str] = []
        if short_ratio >= 0.55:
            actions.append("优先用一两句完整短句，保持即时聊天感")
        elif short_ratio <= 0.2:
            actions.append("可以说完整一点，但不要写成说明书")
        if _safe_int(feature_counts.get("casual_opener"), 0, 0) >= 2:
            actions.append("开头可以自然地随口起一句，避开客服式开场")
        if _safe_int(feature_counts.get("laugh_marker"), 0, 0) >= 2:
            actions.append("轻松时可放一个笑声式口语标记，不要连续堆叠")
        elif _safe_int(feature_counts.get("soft_wave"), 0, 0) >= 2:
            actions.append("轻松时可用一个轻微波浪号收束，不要每句都加")
        elif _safe_int(feature_counts.get("playful"), 0, 0) >= 2:
            actions.append("保留一点轻松口语感，但不要硬塞口癖")
        if _safe_int(feature_counts.get("soft_ending"), 0, 0) >= 2:
            actions.append("收尾可以放轻一点，不必强行加语气词")
        if _safe_int(feature_counts.get("reduplication"), 0, 0) >= 2:
            actions.append("亲近轻松的话题里可偶尔用一个自然叠词，不要生造")
        if _safe_int(feature_counts.get("pause"), 0, 0) >= 2:
            actions.append("允许留一点停顿感，最多一个省略号")
        return actions[: max(1, limit)]

    def _refresh_expression_voice_profile(self) -> dict[str, Any]:
        data = getattr(self, "data", None)
        if not isinstance(data, dict):
            return {}
        now = _now_ts()
        cutoff = now - 30 * 86400
        refresh_day = datetime.now().strftime("%Y-%m-%d")
        total_samples = 0
        total_short = 0
        private_sources = 0
        group_sources = 0
        feature_counts: dict[str, int] = {}
        scene_profiles: dict[str, dict[str, Any]] = {}
        semantic_rules: dict[str, dict[str, Any]] = {}

        def collect(profile: Any, *, source_kind: str, source_id: str) -> None:
            nonlocal total_samples, total_short, private_sources, group_sources
            if not isinstance(profile, dict):
                return
            self._backfill_expression_rule_families(profile)
            if source_kind == "group":
                samples = [
                    item
                    for item in self._group_expression_pattern_samples(profile, now=now)
                    if _safe_int(item.get("evidence_count"), 1, 1) >= 2
                ]
            else:
                raw_samples = profile.get("samples")
                samples = [
                    item
                    for item in (raw_samples if isinstance(raw_samples, list) else [])
                    if isinstance(item, dict) and _safe_float(item.get("ts"), now) >= cutoff
                ]
            learned_rules = [
                item
                for item in (profile.get("learned_rules") if isinstance(profile.get("learned_rules"), list) else [])
                if (
                    isinstance(item, dict)
                    and _safe_int(item.get("evidence_count"), 0, 0) >= 1
                    and self._expression_rule_definition_is_valid(item)
                )
            ]
            if not samples and not learned_rules:
                return
            if source_kind == "private":
                private_sources += 1
            else:
                group_sources += 1
            for item in samples:
                evidence = _safe_int(item.get("evidence_count"), 1, 1)
                weight = min(evidence, 6) if source_kind == "group" else 1
                total_samples += weight
                length = _safe_int(item.get("length"), 0, 0)
                if 0 < length <= 18:
                    total_short += weight
                scene = _single_line(item.get("scene"), 32)
                if scene not in {"acknowledgement", "question", "request", "tease", "emotion", "casual"}:
                    scene = self._expression_scene_from_text(item.get("text") or item.get("phrase"))
                bucket = scene_profiles.setdefault(scene, {"count": 0, "short_count": 0, "feature_counts": {}})
                bucket["count"] += weight
                if 0 < length <= 18:
                    bucket["short_count"] += weight
                raw_features = item.get("features")
                features = raw_features if isinstance(raw_features, list) else self._expression_style_features_from_text(item.get("text") or item.get("phrase"))
                for feature in features:
                    key = _single_line(feature, 32)
                    if not key:
                        continue
                    feature_counts[key] = _safe_int(feature_counts.get(key), 0, 0) + weight
                    bucket_features = bucket["feature_counts"]
                    bucket_features[key] = _safe_int(bucket_features.get(key), 0, 0) + weight
            for item in learned_rules:
                # 只汇总已经审核通过的规则。pattern 是脱敏后的可复用表达模板，
                # 与 evidence_examples 不同，可以进入召回；支持片段永远只留在审核页。
                kind = _single_line(item.get("kind"), 16).lower()
                situation = _single_line(item.get("situation"), 80)
                pattern = _single_line(item.get("pattern") or item.get("style"), 100)
                instruction = _single_line(item.get("instruction"), 140)
                if kind not in {"style", "grammar"} or not situation or not pattern or not instruction:
                    continue
                if not self._safe_expression_phrase(pattern, 100):
                    continue
                family_id = _single_line(item.get("family_id"), 64)
                signature_text = "|".join(
                    (
                        family_id,
                        kind,
                        re.sub(r"[\s，。！？!?、；;：:]", "", situation).lower(),
                        re.sub(r"[\s，。！？!?、；;：:]", "", pattern).lower(),
                    )
                )
                signature = hashlib.sha1(signature_text.encode("utf-8")).hexdigest()[:16]
                evidence = min(6, _safe_int(item.get("evidence_count"), 0, 0))
                bucket = semantic_rules.setdefault(
                    signature,
                    {
                        "id": signature,
                        "family_id": family_id,
                        "kind": kind,
                        "situation": situation,
                        "pattern": pattern,
                        "instruction": instruction,
                        "keywords": [],
                        "evidence_count": 0,
                        "source_kinds": [],
                        "source_refs": [],
                        "channels": [],
                        "relationship_stages": [],
                        "emotion_gates": [],
                        "intent": "",
                        "avoid": "",
                        "persona_conflict": False,
                        "positive_feedback": 0,
                        "negative_feedback": 0,
                        "use_count": 0,
                        "last_seen_ts": 0.0,
                    },
                )
                bucket["evidence_count"] = min(99, _safe_int(bucket.get("evidence_count"), 0, 0) + evidence)
                bucket["last_seen_ts"] = max(
                    _safe_float(bucket.get("last_seen_ts"), 0.0),
                    _safe_float(item.get("last_seen_ts"), now),
                )
                if source_kind not in bucket["source_kinds"]:
                    bucket["source_kinds"].append(source_kind)
                source_ref = {
                    "source_kind": source_kind,
                    "source_id": _single_line(source_id, 80),
                    "rule_id": _single_line(item.get("id"), 40),
                }
                if source_ref["source_id"] and source_ref["rule_id"] and source_ref not in bucket["source_refs"]:
                    bucket["source_refs"].append(source_ref)
                for field in ("channels", "relationship_stages", "emotion_gates"):
                    values = item.get(field) if isinstance(item.get(field), list) else []
                    for value in values:
                        normalized = _single_line(value, 24).lower()
                        if normalized and normalized not in bucket[field]:
                            bucket[field].append(normalized)
                incoming_intent = _single_line(item.get("intent"), 32).lower()
                if incoming_intent:
                    if bucket["intent"] and bucket["intent"] != incoming_intent:
                        bucket["intent"] = "any"
                    else:
                        bucket["intent"] = incoming_intent
                incoming_avoid = _single_line(item.get("avoid"), 160)
                if incoming_avoid and len(incoming_avoid) > len(bucket["avoid"]):
                    bucket["avoid"] = incoming_avoid
                bucket["persona_conflict"] = bool(bucket["persona_conflict"] or item.get("persona_conflict"))
                bucket["positive_feedback"] = min(
                    999,
                    _safe_int(bucket.get("positive_feedback"), 0, 0)
                    + _safe_int(item.get("positive_feedback"), 0, 0),
                )
                bucket["negative_feedback"] = min(
                    999,
                    _safe_int(bucket.get("negative_feedback"), 0, 0)
                    + _safe_int(item.get("negative_feedback"), 0, 0),
                )
                bucket["use_count"] = min(
                    99999,
                    _safe_int(bucket.get("use_count"), 0, 0)
                    + _safe_int(item.get("use_count"), 0, 0),
                )
                for keyword in item.get("keywords", []) if isinstance(item.get("keywords"), list) else []:
                    value = _single_line(keyword, 24)
                    if value and value not in bucket["keywords"]:
                        bucket["keywords"].append(value)
                bucket["keywords"] = bucket["keywords"][:8]

        users = data.get("users") if isinstance(data.get("users"), dict) else {}
        for user_id, user in users.items():
            if isinstance(user, dict) and self._expression_private_learning_source_enabled(user, user_id):
                collect(user.get("expression_profile"), source_kind="private", source_id=str(user_id))
        groups = data.get("groups") if isinstance(data.get("groups"), dict) else {}
        for group_id, group in groups.items():
            if isinstance(group, dict) and self._expression_group_learning_source_enabled(group_id):
                collect(group.get("expression_profile"), source_kind="group", source_id=str(group_id))

        runtime_rules = list(semantic_rules.values())
        self._deduplicate_expression_rule_families(runtime_rules)
        profile = {
            "sample_count": total_samples,
            "private_source_count": private_sources,
            "group_source_count": group_sources,
            "short_ratio": round(total_short / max(1, total_samples), 2),
            "feature_counts": feature_counts,
            "scene_profiles": {
                scene: {
                    "count": _safe_int(bucket.get("count"), 0, 0),
                    "short_ratio": round(_safe_int(bucket.get("short_count"), 0, 0) / max(1, _safe_int(bucket.get("count"), 0, 0)), 2),
                    "feature_counts": dict(bucket.get("feature_counts") or {}),
                }
                for scene, bucket in scene_profiles.items()
                if _safe_int(bucket.get("count"), 0, 0) > 0
            },
            "learned_rules": sorted(
                runtime_rules,
                key=lambda item: (
                    -_safe_int(item.get("evidence_count"), 0, 0),
                    -_safe_float(item.get("last_seen_ts"), 0.0),
                ),
            )[: runtime_persona_setting(self, "max_learned_expression_items", 60)],
            "scope_signature": self._expression_scope_signature(),
            "refresh_day": refresh_day,
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        }
        profile["actions"] = self._expression_voice_actions(
            total_samples,
            _safe_float(profile.get("short_ratio"), 0.0),
            feature_counts,
            limit=4,
        )
        data["expression_voice_profile"] = profile
        return profile

    def _expression_voice_profile(self) -> dict[str, Any]:
        data = getattr(self, "data", None)
        if not isinstance(data, dict):
            return {}
        profile = data.get("expression_voice_profile")
        refresh_day = datetime.now().strftime("%Y-%m-%d")
        if (
            not isinstance(profile, dict)
            or profile.get("scope_signature") != self._expression_scope_signature()
            or profile.get("refresh_day") != refresh_day
        ):
            profile = self._refresh_expression_voice_profile()
        return profile if isinstance(profile, dict) else {}

    def _expression_companion_context(
        self,
        *,
        scope: str,
        target_id: str = "",
        inbound_text: str = "",
        context_owner: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        channel = _single_line(scope, 24).lower() or "private"
        owner = context_owner if isinstance(context_owner, dict) else None
        if owner is None and channel in {"private", "proactive", "tts"}:
            users = self.data.get("users") if isinstance(getattr(self, "data", None), dict) else {}
            candidate = users.get(str(target_id)) if isinstance(users, dict) else None
            owner = candidate if isinstance(candidate, dict) else None

        relationship_stage = "any"
        if isinstance(owner, dict) and channel in {"private", "proactive", "tts"}:
            level = _single_line(self._relationship_profile(owner).get("level"), 24).lower()
            relationship_stage = {
                "陌生": "stranger",
                "stranger": "stranger",
                "熟悉": "familiar",
                "familiar": "familiar",
                "亲近": "close",
                "close": "close",
            }.get(level, "any")

        intent_profile: dict[str, Any] = {}
        if inbound_text:
            try:
                intent_profile = self._analyze_inbound_intent(inbound_text)
            except Exception:
                intent_profile = {}
        elif isinstance(owner, dict) and isinstance(owner.get("intent_profile"), dict):
            intent_profile = owner.get("intent_profile") or {}
        intent = _single_line(intent_profile.get("intent"), 32).lower()
        if channel == "proactive" and not inbound_text:
            intent = "proactive"
        elif channel == "qzone":
            intent = "emotion" if re.search(r"(低落|委屈|难受|emo|情绪)", inbound_text, re.IGNORECASE) else "casual"
        elif intent in {"", "chat", "empty"}:
            intent = self._expression_scene_from_text(inbound_text) if inbound_text else "casual"

        emotion_gate = "normal"
        expression_band = ""
        expression_builder = getattr(self, "_build_expression_decision_for_user", None)
        if isinstance(owner, dict) and callable(expression_builder):
            try:
                decision = expression_builder(owner, passive_reengagement=True)
                projection = decision.to_dict() if hasattr(decision, "to_dict") else dict(decision or {})
                expression_band = _single_line(projection.get("expression_band"), 24).lower()
            except Exception:
                expression_band = ""
        intent_emotion = _single_line(intent_profile.get("emotion"), 24).lower()
        if expression_band in {"avoidant", "hurt"} or intent == "boundary" or intent_emotion == "resistant":
            emotion_gate = "guarded"
        elif intent in {"comfort", "emotion"} or intent_emotion == "low":
            emotion_gate = "low"
        elif expression_band in {"lively", "warm", "close", "affectionate"} or intent in {"play", "intimacy"} or intent_emotion in {"light", "close", "positive"}:
            emotion_gate = "positive"

        return {
            "channel": channel,
            "relationship_stage": relationship_stage,
            "emotion_gate": emotion_gate,
            "intent": intent or "casual",
        }

    @staticmethod
    def _format_expression_rule_bundle_line(rule: Any) -> str:
        if not isinstance(rule, dict):
            return ""
        style_rule = rule.get("style_rule") if isinstance(rule.get("style_rule"), dict) else None
        grammar_rule = rule.get("grammar_rule") if isinstance(rule.get("grammar_rule"), dict) else None
        if style_rule is None and _single_line(rule.get("kind"), 16).lower() == "style":
            style_rule = rule
        if grammar_rule is None and _single_line(rule.get("kind"), 16).lower() == "grammar":
            grammar_rule = rule
        situation = _single_line(
            (style_rule or {}).get("situation") or (grammar_rule or {}).get("situation") or rule.get("situation"),
            80,
        )
        if not situation:
            return ""
        parts: list[str] = []
        if style_rule:
            pattern = _single_line(style_rule.get("pattern") or style_rule.get("style"), 100)
            instruction = _single_line(style_rule.get("instruction"), 140)
            if pattern and instruction:
                parts.append(f"可复用表达“{pattern}”（{instruction}）")
        if grammar_rule:
            pattern = _single_line(grammar_rule.get("pattern") or grammar_rule.get("style"), 100)
            instruction = _single_line(grammar_rule.get("instruction"), 140)
            if pattern and instruction:
                parts.append(f"句法习惯“{pattern}”（{instruction}）")
        if not parts:
            return ""
        avoid = _single_line(rule.get("avoid"), 200)
        if avoid:
            parts.append(f"边界：{avoid}")
        kind_label = "组合规则" if style_rule and grammar_rule else ("情境表达" if style_rule else "语法习惯")
        return f"- {kind_label}｜当“{situation}”时：" + "；".join(parts)

    def _expression_voice_selection(
        self,
        *,
        scope: str,
        target_id: str = "",
        inbound_text: str = "",
        context_owner: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not bool(runtime_persona_setting(self, "enable_expression_learning", True)):
            return {"prompt": "", "rules": [], "context": {}}
        if scope in {"private", "proactive"} and not self._expression_private_application_enabled(target_id):
            return {"prompt": "", "rules": [], "context": {}}
        if scope == "group" and not self._expression_group_application_enabled(target_id):
            return {"prompt": "", "rules": [], "context": {}}
        scoped_rules = scoped_approved_expression_rules(context_owner)
        profile = self._expression_voice_profile() if scoped_rules is None else {}
        context = self._expression_companion_context(
            scope=scope,
            target_id=target_id,
            inbound_text=inbound_text,
            context_owner=context_owner,
        )
        learned_rules = self._select_learned_expression_rules(
            profile.get("learned_rules") if scoped_rules is None else scoped_rules,
            hint=inbound_text,
            limit=2,
            context=context,
        )
        if not learned_rules:
            return {"prompt": "", "rules": [], "context": context}
        guidance: list[str] = []
        for rule in learned_rules:
            line = self._format_expression_rule_bundle_line(rule)
            if line:
                guidance.append(line)
        if not guidance:
            return {"prompt": "", "rules": [], "context": context}
        scope_label = {"private": "私聊回复", "proactive": "私聊主动消息", "group": "群聊回复"}.get(scope, "当前回复")
        evidence_count = sum(_safe_int(item.get("evidence_count"), 0, 0) for item in learned_rules)
        source_label = (
            "当前私聊/群聊命名空间内"
            if scoped_rules is not None else "已允许的私聊/群聊来源"
        )
        body = (
            f"这些规则只来自{source_label}，共 {evidence_count} 条支持证据。当前用于{scope_label}：\n"
            + "\n".join(guidance[:4])
            + "\n执行优先级：工具与事实结果 > 安全及能力边界 > AstrBot 人格 > 当前关系与情绪 > 已审核表达规则 > 装饰性口癖/标点。"
            + "任何冲突都舍弃较低优先级；工具失败时绝不能声称已发送、已完成或已成功。"
            + "情境表达可以改写或替换占位符，语法习惯只控制句法；不要机械复读。"
            + "句尾括号或颜文字后缀必须与所属句保持同一行；规则要求括号前无标点时，不得补逗号或其他标点。"
            + "不得带出来源身份、称呼、账号、关系、事实、秘密或支持片段。"
        )
        section = prompt_section(
            key="expression.voice",
            title="已审核的表达学习规则",
            source="expression",
            content=body,
        )
        return {
            "prompt": _render_conversation_section_labeled(section),
            "section": section,
            "rules": [dict(item) for item in learned_rules],
            "context": context,
            "selection_scope": "current_namespace" if scoped_rules is not None else "legacy_aggregate",
        }

    def _format_expression_voice_for_prompt(
        self,
        *,
        scope: str,
        target_id: str = "",
        inbound_text: str = "",
        context_owner: dict[str, Any] | None = None,
        stage_owner: dict[str, Any] | None = None,
    ) -> str:
        section = self._format_expression_voice_prompt_section(
            scope=scope,
            target_id=target_id,
            inbound_text=inbound_text,
            context_owner=context_owner,
            stage_owner=stage_owner,
        )
        return _render_conversation_section_labeled(section)

    def _format_expression_voice_prompt_section(
        self,
        *,
        scope: str,
        target_id: str = "",
        inbound_text: str = "",
        context_owner: dict[str, Any] | None = None,
        stage_owner: dict[str, Any] | None = None,
    ) -> PromptSection | None:
        selection = self._expression_voice_selection(
            scope=scope,
            target_id=target_id,
            inbound_text=inbound_text,
            context_owner=context_owner,
        )
        if isinstance(stage_owner, dict) and selection.get("rules"):
            profile = stage_owner.setdefault("expression_profile", {})
            if isinstance(profile, dict):
                profile["staged_semantic_selection"] = {
                    "ts": _now_ts(),
                    "rules": [dict(item) for item in selection.get("rules", []) if isinstance(item, dict)][:2],
                    "context": dict(selection.get("context") or {}),
                }
        section = selection.get("section")
        return section if isinstance(section, PromptSection) else None

    def _update_expression_profile_from_message(self, user: dict[str, Any], text: str) -> None:
        if not runtime_persona_setting(self, "enable_expression_learning", True):
            return
        cleaned = _single_line(_strip_internal_message_blocks(text, enabled=bool(runtime_persona_setting(self, "enable_framework_error_leak_guard", True))), self._expression_sample_max_chars())
        if not cleaned:
            return
        if self._should_skip_expression_sample(cleaned):
            return
        managed, scope_context = self._expression_formal_scope_for_owner(user, source_kind="private")
        if managed and scope_context is None:
            return
        profile = user.setdefault("expression_profile", {})
        if not isinstance(profile, dict):
            profile = {}
            user["expression_profile"] = profile
        now = _now_ts()
        samples = profile.get("samples")
        if not isinstance(samples, list):
            samples = []
            legacy_count = _safe_int(profile.get("samples"), 0, 0)
            legacy_short = _safe_int(profile.get("short_count"), 0, 0)
            legacy_punctuation = profile.get("punctuation") if isinstance(profile.get("punctuation"), dict) else {}
            legacy_endings = profile.get("endings") if isinstance(profile.get("endings"), list) else []
            legacy_phrases = profile.get("recent_phrases") if isinstance(profile.get("recent_phrases"), list) else []
            punctuation_items = [
                (str(mark), _safe_int(count, 0, 0))
                for mark, count in legacy_punctuation.items()
                if _safe_int(count, 0, 0) > 0
            ]
            if legacy_count:
                migrate_count = min(legacy_count, runtime_persona_setting(self, "max_learned_expression_items", 60))
                for idx in range(migrate_count):
                    punctuation = {}
                    if punctuation_items:
                        mark, count = punctuation_items[idx % len(punctuation_items)]
                        punctuation[mark] = min(3, max(1, count // max(1, migrate_count)))
                    samples.append(
                        {
                            "ts": now - (idx + 1) * 3600,
                            "length": 12 if idx < legacy_short else 32,
                            "punctuation": punctuation,
                            "ending": _single_line(legacy_endings[idx], 12) if idx < len(legacy_endings) else "",
                            "phrase": _single_line(legacy_phrases[idx], 40) if idx < len(legacy_phrases) else "",
                        }
                    )
        samples = [item for item in samples if isinstance(item, dict)]
        cutoff = now - 30 * 86400
        samples = [item for item in samples if _safe_float(item.get("ts"), now) >= cutoff]
        profile["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        sample = self._expression_sample_from_text(cleaned, now)
        if scope_context is not None:
            pending_review = self._expression_manual_review_enabled()
            sample = bind_expression_item(
                sample, scope_context,
                approval_state="pending" if pending_review else "approved",
                approved_by="" if pending_review else "automatic_policy",
            )
        if self._expression_manual_review_enabled():
            profile["samples"] = samples[: runtime_persona_setting(self, "max_learned_expression_items", 60)]
            self._queue_expression_pending_sample(profile, sample, cleaned)
            self._refresh_expression_profile_legacy_summary(profile)
            if scope_context is not None:
                user["expression_profile"] = self._expression_bind_profile_scope(
                    profile, scope_context, bump_revision=True,
                )
            return
        samples.insert(0, sample)
        profile["samples"] = samples[: runtime_persona_setting(self, "max_learned_expression_items", 60)]
        self._refresh_expression_profile_legacy_summary(profile)
        if scope_context is not None:
            user["expression_profile"] = self._expression_bind_profile_scope(
                profile, scope_context, bump_revision=True,
            )

    def _update_group_expression_profile_from_message(self, group: dict[str, Any], text: str) -> None:
        if not runtime_persona_setting(self, "enable_expression_learning", True):
            return
        cleaned = _single_line(_strip_internal_message_blocks(text, enabled=bool(runtime_persona_setting(self, "enable_framework_error_leak_guard", True))), self._expression_sample_max_chars())
        if not cleaned or self._should_skip_expression_sample(cleaned):
            return
        managed, scope_context = self._expression_formal_scope_for_owner(group, source_kind="group")
        if managed and scope_context is None:
            return
        profile = group.setdefault("expression_profile", {})
        if not isinstance(profile, dict):
            profile = {}
            group["expression_profile"] = profile
        now = _now_ts()
        samples = profile.get("samples") if isinstance(profile.get("samples"), list) else []
        sample = self._expression_sample_from_text(cleaned, now)
        # Group sources retain only aggregate-safe metadata, never a group member's original phrasing.
        for key in ("text", "phrase", "ending"):
            sample.pop(key, None)
        sample["evidence_count"] = 1
        if scope_context is not None:
            sample = bind_expression_item(
                sample, scope_context, approval_state="approved", approved_by="automatic_policy",
            )
        samples.insert(0, sample)
        profile["samples"] = samples
        profile["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        self._normalize_group_expression_profile(profile, now=now)
        if scope_context is not None:
            group["expression_profile"] = self._expression_bind_profile_scope(
                profile, scope_context, bump_revision=True,
            )

    def _expression_sample_from_text(self, cleaned: str, now: float | None = None) -> dict[str, Any]:
        now = now or _now_ts()
        punctuation = {}
        for mark in ("！", "!", "？", "?", "~", "～", "…", "。"):
            count = cleaned.count(mark)
            if count:
                punctuation[mark] = count
        stripped = cleaned.rstrip("。！？!?~～… ")
        ending = ""
        if 2 <= len(stripped) <= 80:
            ending = stripped[-min(6, max(2, len(stripped))):]
        phrase = ""
        phrase_limit = 56 if self._expression_learning_mode() == "aggressive" else 40
        if 2 <= len(cleaned) <= phrase_limit and not re.search(r"https?://|<[^>]+>", cleaned):
            phrase = cleaned
        return {
            "id": hashlib.sha1(f"{now}:{cleaned}".encode("utf-8")).hexdigest()[:12],
            "ts": now,
            "text": cleaned,
            "length": len(cleaned),
            "punctuation": punctuation,
            "ending": ending,
            "phrase": phrase,
            "scene": self._expression_scene_from_text(cleaned),
            "features": self._expression_style_features_from_text(cleaned),
        }

    @staticmethod
    def _expression_scene_label(scene: Any) -> str:
        labels = {
            "acknowledgement": "短确认",
            "question": "提问/追问",
            "request": "提出请求",
            "tease": "玩笑/打趣",
            "emotion": "情绪表达",
            "casual": "普通闲聊",
        }
        return labels.get(_single_line(scene, 32), "普通闲聊")

    def _expression_scene_from_text(self, text: Any) -> str:
        cleaned = _single_line(text, 180)
        if not cleaned:
            return "casual"
        stripped = cleaned.rstrip("。！？!?~～… ").lower()
        if re.search(r"(?:帮我|给我|麻烦|能不能|可不可以|要不|请你|记得|别忘|提醒我|帮忙)", cleaned):
            return "request"
        if re.search(r"(?:难过|委屈|烦|好累|累死|想哭|哭了|生气|不开心|emo|破防|崩溃|害怕|焦虑)", cleaned, re.I):
            return "emotion"
        if re.search(r"(?:笨蛋|坏蛋|哼|才不要|你又|真是你|可恶)", cleaned):
            return "tease"
        if "？" in cleaned or "?" in cleaned or re.search(r"(?:怎么|为什么|啥|什么|是不是|对吗|行吗|好不好)$", stripped):
            return "question"
        if len(stripped) <= 28 and re.search(r"^(?:嗯|好|行|可以|知道|收到|对|没事|好吧|好呀|行吧|确实|原来|懂了|哦|啊|诶)", stripped):
            return "acknowledgement"
        return "casual"

    @staticmethod
    def _expression_style_features_from_text(text: Any) -> list[str]:
        cleaned = _single_line(text, 180)
        if not cleaned:
            return []
        stripped = cleaned.rstrip("。！？!?~～… ")
        features: list[str] = []
        if len(cleaned) <= 18:
            features.append("short")
        if re.match(r"^(?:嗯|啊|诶|欸|唔|哎|哈哈|嘿嘿|哼|唉)", stripped):
            features.append("casual_opener")
        lowered = cleaned.lower()
        if any(marker in lowered for marker in ("哈哈", "嘿嘿", "hh", "www")):
            features.append("laugh_marker")
        if not any(marker in lowered for marker in ("哈哈", "嘿嘿")) and re.search(r"([\u4e00-\u9fff])\1", stripped):
            features.append("reduplication")
        if "~" in cleaned or "～" in cleaned:
            features.append("soft_wave")
        if any(marker in lowered for marker in ("哈哈", "嘿嘿", "hh", "www", "~", "～", "捏", "哼")):
            features.append("playful")
        if stripped.endswith(("吧", "呀", "啦", "嘛", "呢", "哦", "诶")):
            features.append("soft_ending")
        if "…" in cleaned or "..." in cleaned:
            features.append("pause")
        if "？" in cleaned or "?" in cleaned:
            features.append("question")
        return features

    def _expression_learning_mode(self) -> str:
        mode = str(runtime_persona_setting(self, "expression_learning_mode", "balanced") or "balanced").strip().lower()
        if mode not in {"light", "balanced", "aggressive"}:
            return "balanced"
        return mode

    def _expression_formal_scope_for_owner(
        self,
        owner: dict[str, Any],
        *,
        source_kind: str,
    ) -> tuple[bool, Any | None]:
        """Return (scoped-managed, formal context); managed failures are fail-closed."""
        managed = getattr(self, "req041_scoped_projection_sync", None) is not None
        if not managed or not isinstance(owner, dict):
            return managed, None
        if source_kind == "private":
            resolver = getattr(self, "_req041_scoped_context_for_user", None)
            context = resolver(owner, kind="private", purpose="rule_write") if callable(resolver) else None
        elif source_kind == "group":
            resolver = getattr(self, "_req041_scoped_group_context", None)
            group_id = _single_line(owner.get("group_id"), 160)
            context = resolver(group_id, purpose="rule_write") if callable(resolver) and group_id else None
        else:
            context = None
        return managed, context

    def _expression_bind_profile_scope(
        self,
        profile: dict[str, Any],
        context: Any,
        *,
        bump_revision: bool,
    ) -> dict[str, Any]:
        """Bind durable evidence/rules, migrating stale runtime scope metadata.

        Profiles live under a stable user/group record, but their ownership
        envelope also contains persona and migration-epoch metadata.  A persona
        switch or an upgrade can therefore leave an otherwise valid profile
        carrying an old envelope.  Rebinding the envelope is safe at this
        storage boundary and preserves the learned content; explicit callers
        that bind an item/profile directly still retain strict validation.
        """
        try:
            result = bind_expression_profile(profile, context, bump_revision=bump_revision)
        except ValueError as exc:
            if str(exc) != "expression_profile_scope_mismatch":
                raise
            result = deepcopy(profile)
            # The profile remains in the same owner record, so keep its
            # monotonic revision while replacing only stale scope metadata.
            result.pop("scope_ownership", None)
            result["scope_revision"] = max(1, _safe_int(result.get("scope_revision"), 1, 1))
            for key in (
                "samples", "pending_samples", "expression_rules", "pending_rules",
                "learned_rules", "rejected_samples", "revoked_samples",
                "rejected_rules", "revoked_rules",
            ):
                items = result.get(key)
                if not isinstance(items, list):
                    continue
                migrated: list[Any] = []
                for item in items:
                    if isinstance(item, dict):
                        item = deepcopy(item)
                        item.pop("scope_binding", None)
                    migrated.append(item)
                result[key] = migrated
            result = bind_expression_profile(result, context, bump_revision=False)
        collections = (
            ("samples", "approved", "automatic_policy"),
            ("pending_samples", "pending", ""),
            ("expression_rules", "pending", ""),
            ("pending_rules", "pending", ""),
            ("learned_rules", "approved", "legacy_migration"),
            ("rejected_samples", "rejected", "administrator"),
            ("revoked_samples", "revoked", "administrator"),
            ("rejected_rules", "rejected", "administrator"),
            ("revoked_rules", "revoked", "administrator"),
        )
        for key, approval_state, default_actor in collections:
            items = result.get(key)
            if not isinstance(items, list):
                continue
            bound: list[Any] = []
            for raw in items:
                if not isinstance(raw, dict):
                    raw = {"legacy_value": deepcopy(raw)}
                existing = raw.get("scope_binding") if isinstance(raw.get("scope_binding"), dict) else {}
                actor = _single_line(existing.get("approved_by"), 80) or default_actor
                bound.append(bind_expression_item(
                    raw, context, approval_state=approval_state, approved_by=actor,
                ))
            result[key] = bound
        return result

    def _expression_sample_max_chars(self) -> int:
        return 180 if self._expression_learning_mode() == "aggressive" else 120

    def _expression_style_review_enabled(self) -> bool:
        return bool(
            runtime_persona_setting(self, "enable_expression_learning", True)
            and runtime_persona_setting(self, "enable_expression_style_review", True)
        )

    def _expression_manual_review_enabled(self) -> bool:
        return bool(
            runtime_persona_setting(self, "enable_expression_learning", True)
            and runtime_persona_setting(self, "enable_expression_manual_review", False)
        )

    def _queue_expression_pending_sample(self, profile: dict[str, Any], sample: dict[str, Any], cleaned: str) -> None:
        pending = profile.get("pending_samples")
        if not isinstance(pending, list):
            pending = []
        compact = self._compact_repeat_text(cleaned)
        kept: list[dict[str, Any]] = []
        for item in pending:
            if not isinstance(item, dict):
                continue
            old_text = _single_line(item.get("text") or item.get("phrase"), 180)
            if old_text and self._compact_repeat_text(old_text) == compact:
                continue
            kept.append(item)
        item = dict(sample)
        item["review_status"] = "pending"
        item["created_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        kept.insert(0, item)
        profile["pending_samples"] = kept[: min(80, max(12, runtime_persona_setting(self, "max_learned_expression_items", 60) * 2))]
        profile["pending_count"] = len(profile["pending_samples"])
        profile["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")

    def _should_skip_expression_sample(self, cleaned: str) -> bool:
        if len(cleaned) > self._expression_sample_max_chars():
            return True
        if re.search(r"https?://|www\.|```|Traceback|Error code:|Exception|\[INFO\]|\[WARN\]|\[ERRO\]|\[Core\]", cleaned, re.IGNORECASE):
            return True
        if re.search(r"^\s*(?:/|!|！|陪伴\s|sudo\b|git\b|python\b|node\b|npm\b|pnpm\b|pip\b)", cleaned, re.IGNORECASE):
            return True
        if cleaned.count("\n") >= 2 or cleaned.count("[") + cleaned.count("]") >= 6:
            return True
        if re.search(r"(傻逼|滚|闭嘴|垃圾|废物|妈的|草泥马|操你|死全家)", cleaned):
            return True
        if re.search(r"(习近平|共产党|中共|六四|天安门|法轮功|台独|港独|藏独|疆独|民主运动|政治敏感)", cleaned):
            return True
        if re.search(r"(复制|日志|报错|堆栈|代码|配置|schema|版本号|commit|diff|traceback)", cleaned, re.IGNORECASE):
            return True
        if re.search(r"^\s*(?:我叫|我是|叫我)[^。！？!?\n]{1,40}", cleaned):
            return True
        return False

    def _safe_expression_phrase(self, phrase: Any, limit: int = 56) -> str:
        text = _single_line(phrase, limit)
        if not text or len(text) < 2:
            return ""
        if self._should_skip_expression_sample(text):
            return ""
        if re.search(r"<[^>]{1,120}>|@[A-Za-z0-9_\-\u4e00-\u9fff]{1,32}|QQ|群聊|群友|私聊", text, re.IGNORECASE):
            return ""
        if re.search(r"(你是|我是|他是|她是|叫我|叫你|名字|主人|主要用户|次要用户|朋友|同学|老师|室友|父母|妈妈|爸爸|哥哥|姐姐|弟弟|妹妹)", text):
            return ""
        return text

    def _expression_profile_phrases(self, profile: dict[str, Any], *, limit: int = 4) -> list[str]:
        raw = profile.get("recent_phrases") if isinstance(profile, dict) else []
        if not isinstance(raw, list):
            return []
        phrases: list[str] = []
        for item in raw:
            phrase = self._safe_expression_phrase(item, 56)
            if phrase and phrase not in phrases:
                phrases.append(phrase)
            if len(phrases) >= limit:
                break
        return phrases

    def _expression_profile_endings(self, profile: dict[str, Any], *, limit: int = 4) -> list[str]:
        raw = profile.get("endings") if isinstance(profile, dict) else []
        if not isinstance(raw, list):
            return []
        endings: list[str] = []
        for item in raw:
            ending = self._safe_expression_phrase(item, 12)
            if ending and ending not in endings:
                endings.append(ending)
            if len(endings) >= limit:
                break
        return endings

    def _refresh_expression_profile_legacy_summary(self, profile: dict[str, Any]) -> None:
        samples = profile.get("samples")
        if not isinstance(samples, list):
            return
        profile["pattern_count"] = len(samples)
        profile["sample_count"] = sum(
            _safe_int(item.get("evidence_count"), 1, 1)
            for item in samples
            if isinstance(item, dict)
        )
        profile["short_count"] = sum(
            _safe_int(item.get("evidence_count"), 1, 1)
            for item in samples
            if isinstance(item, dict) and _safe_int(item.get("length"), 0, 0) <= 18
        )
        punctuation: dict[str, int] = {}
        endings: list[str] = []
        phrases: list[str] = []
        scene_stats: dict[str, dict[str, Any]] = {}
        fingerprint_features: dict[str, int] = {}
        for item in samples:
            if not isinstance(item, dict):
                continue
            evidence = _safe_int(item.get("evidence_count"), 1, 1)
            marks = item.get("punctuation")
            if isinstance(marks, dict):
                for mark, count in marks.items():
                    punctuation[str(mark)] = punctuation.get(str(mark), 0) + _safe_int(count, 0, 0)
            ending = _single_line(item.get("ending"), 12)
            if ending and ending not in endings:
                endings.append(ending)
            phrase = _single_line(item.get("phrase"), 40)
            if phrase and phrase not in phrases:
                phrases.append(phrase)
            sample_text = _single_line(item.get("text") or phrase, 180)
            scene = _single_line(item.get("scene"), 32)
            if scene not in {"acknowledgement", "question", "request", "tease", "emotion", "casual"}:
                scene = self._expression_scene_from_text(sample_text)
                item["scene"] = scene
            raw_features = item.get("features")
            features = [
                _single_line(feature, 32)
                for feature in raw_features
                if _single_line(feature, 32)
            ] if isinstance(raw_features, list) else self._expression_style_features_from_text(sample_text)
            item["features"] = list(dict.fromkeys(features))
            bucket = scene_stats.setdefault(
                scene,
                {"count": 0, "short_count": 0, "feature_counts": {}, "latest_ts": 0.0},
            )
            bucket["count"] += evidence
            if _safe_int(item.get("length"), len(sample_text), 0) <= 18:
                bucket["short_count"] += evidence
            bucket["latest_ts"] = max(_safe_float(bucket.get("latest_ts"), 0.0), _safe_float(item.get("ts"), 0.0))
            feature_counts = bucket["feature_counts"]
            for feature in item["features"]:
                feature_counts[feature] = _safe_int(feature_counts.get(feature), 0, 0) + evidence
                fingerprint_features[feature] = _safe_int(fingerprint_features.get(feature), 0, 0) + evidence
        profile["punctuation"] = punctuation
        profile["endings"] = endings[: runtime_persona_setting(self, "max_learned_expression_items", 60)]
        profile["recent_phrases"] = phrases[: runtime_persona_setting(self, "max_learned_expression_items", 60)]
        profile["scene_profiles"] = {
            scene: {
                "count": _safe_int(bucket.get("count"), 0, 0),
                "short_ratio": round(
                    _safe_int(bucket.get("short_count"), 0, 0) / max(1, _safe_int(bucket.get("count"), 0, 0)),
                    2,
                ),
                "feature_counts": dict(bucket.get("feature_counts") or {}),
                "latest_ts": _safe_float(bucket.get("latest_ts"), 0.0),
            }
            for scene, bucket in scene_stats.items()
            if _safe_int(bucket.get("count"), 0, 0) > 0
        }
        profile["style_fingerprint"] = {
            "short_ratio": round(profile["short_count"] / max(1, profile["sample_count"]), 2),
            "feature_counts": fingerprint_features,
        }
        profile["expression_rules"] = self._expression_rules_from_scene_profiles(profile["scene_profiles"])

    def _expression_rule_details_for_scene(
        self,
        scene: Any,
        scene_profile: dict[str, Any],
    ) -> dict[str, Any]:
        normalized_scene = _single_line(scene, 32)
        count = _safe_int(scene_profile.get("count"), 0, 0)
        if normalized_scene not in {"acknowledgement", "question", "request", "tease", "emotion", "casual"} or count < 2:
            return {}
        base_rules = {
            "acknowledgement": "先用简短口语确认接住，不把一个短确认扩写成长说明",
            "question": "先直接回应核心，再自然接下去，不绕成客服式解释",
            "request": "先给明确答复或行动，再补必要说明",
            "tease": "保持轻松有来有回，不突然说教或端着",
            "emotion": "先接住情绪，短一点、慢一点，不急着讲道理",
            "casual": "从眼前话头直接接，不套客气开场",
        }
        short_ratio = _safe_float(scene_profile.get("short_ratio"), 0.0)
        raw_feature_counts = scene_profile.get("feature_counts")
        feature_counts = raw_feature_counts if isinstance(raw_feature_counts, dict) else {}
        feature_threshold = 2
        actions = [base_rules[normalized_scene]]
        signals: list[str] = []

        if short_ratio >= 0.6:
            actions.append("长度控制在一两句，保留即时聊天感")
            signals.append("short")
        elif short_ratio <= 0.2 and normalized_scene in {"emotion", "casual"}:
            actions.append("可以完整一点，但不要写成说明书")
        if _safe_int(feature_counts.get("casual_opener"), 0, 0) >= feature_threshold:
            actions.append("开头可自然地随口起一句，避开客服式开场")
            signals.append("casual_opener")
        if _safe_int(feature_counts.get("laugh_marker"), 0, 0) >= feature_threshold:
            actions.append("轻松时可放一个笑声式口语标记，不要连续堆叠")
            signals.append("laugh_marker")
        elif _safe_int(feature_counts.get("soft_wave"), 0, 0) >= feature_threshold:
            actions.append("轻松时可用一个轻微波浪号收束，不要每句都加")
            signals.append("soft_wave")
        elif _safe_int(feature_counts.get("playful"), 0, 0) >= feature_threshold:
            actions.append("保留一点轻松口语感，但不要硬塞口癖")
            signals.append("playful")
        if _safe_int(feature_counts.get("soft_ending"), 0, 0) >= feature_threshold:
            actions.append("收尾可以放轻一点，不必强行加语气词")
            signals.append("soft_ending")
        if _safe_int(feature_counts.get("reduplication"), 0, 0) >= feature_threshold:
            actions.append("亲近轻松的话题里可偶尔用一个自然叠词，不要生造")
            signals.append("reduplication")
        if _safe_int(feature_counts.get("pause"), 0, 0) >= feature_threshold:
            actions.append("允许留一点停顿感，最多一个省略号")
            signals.append("pause")

        signals = list(dict.fromkeys(signals))
        rule_id = f"{normalized_scene}:{'.'.join(signals[:3]) or 'scene'}"
        return {
            "id": rule_id,
            "scene": normalized_scene,
            "label": self._expression_scene_label(normalized_scene),
            "evidence_count": count,
            "confidence": min(0.96, round(0.45 + min(count, 8) * 0.06, 2)),
            "actions": actions[:4],
            "signals": signals[:4],
            "instruction": "；".join(actions[:4]) + "。",
        }

    def _expression_rules_from_scene_profiles(self, scene_profiles: Any) -> list[dict[str, Any]]:
        if not isinstance(scene_profiles, dict):
            return []
        rules: list[dict[str, Any]] = []
        for scene, raw_profile in scene_profiles.items():
            if not isinstance(raw_profile, dict):
                continue
            details = self._expression_rule_details_for_scene(scene, raw_profile)
            if details:
                rules.append(details)
        rules.sort(key=lambda item: (-_safe_int(item.get("evidence_count"), 0, 0), _single_line(item.get("scene"), 32)))
        return rules[:6]

    def _expression_rule_details_for_inbound(self, profile: dict[str, Any], inbound_text: Any) -> dict[str, Any]:
        scene = self._expression_scene_from_text(inbound_text)
        raw_profiles = profile.get("scene_profiles") if isinstance(profile, dict) else {}
        scene_profiles = raw_profiles if isinstance(raw_profiles, dict) else {}
        scene_profile = scene_profiles.get(scene) if isinstance(scene_profiles.get(scene), dict) else {}
        return self._expression_rule_details_for_scene(scene, scene_profile)

    def _expression_scene_rule_for_inbound(self, profile: dict[str, Any], inbound_text: Any) -> str:
        if self._expression_learning_mode() == "light":
            return ""
        details = self._expression_rule_details_for_inbound(profile, inbound_text)
        if not details:
            return ""
        return (
            f"当前场景「{details['label']}」已有 {details['evidence_count']} 条表达证据："
            f"{details['instruction']}"
        )

