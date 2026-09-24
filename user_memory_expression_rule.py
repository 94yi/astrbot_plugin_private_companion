# -*- coding: utf-8 -*-
"""表达规则归一去重归并。

由 tools/split_mixin_domain.py 从 user_memory.py 机械抽取（36 个方法 + 0 个模块级名字 + 0 个类级赋值 / 1436 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 UserMemoryMixin）。
"""
from __future__ import annotations

import hashlib
import re
from .helpers import _now_ts, _safe_float, _safe_int, _single_line
from .persona_config import runtime_persona_setting
from copy import deepcopy
from datetime import datetime
from typing import Any



class UserMemoryExpressionRuleMixin:
    """表达规则归一去重归并（从 UserMemoryMixin 拆出）。"""


    @staticmethod
    def _expression_length_bucket(length: Any) -> str:
        value = _safe_int(length, 0, 0)
        if value <= 6:
            return "2-6"
        if value <= 12:
            return "7-12"
        if value <= 20:
            return "13-20"
        if value <= 36:
            return "21-36"
        return "37+"

    def _group_expression_pattern_signature(self, item: dict[str, Any]) -> str:
        scene = _single_line(item.get("scene"), 32)
        if scene not in {"acknowledgement", "question", "request", "tease", "emotion", "casual"}:
            scene = "casual"
        raw_features = item.get("features")
        features = sorted({
            _single_line(feature, 32)
            for feature in raw_features
            if _single_line(feature, 32)
        }) if isinstance(raw_features, list) else []
        distinctive = [feature for feature in features if feature not in {"short", "question"}]
        if scene == "casual" and not distinctive:
            return ""
        marks = item.get("punctuation") if isinstance(item.get("punctuation"), dict) else {}
        mark_keys = sorted(str(mark) for mark, count in marks.items() if _safe_int(count, 0, 0) > 0)
        length_bucket = self._expression_length_bucket(item.get("length"))
        return "|".join((scene, length_bucket, ",".join(features), ",".join(mark_keys)))

    def _group_expression_pattern_samples(self, profile: dict[str, Any], *, now: float | None = None) -> list[dict[str, Any]]:
        if not isinstance(profile, dict):
            return []
        now = now or _now_ts()
        cutoff = now - 30 * 86400
        raw_samples = profile.get("samples") if isinstance(profile.get("samples"), list) else []
        buckets: dict[str, dict[str, Any]] = {}
        for raw in raw_samples:
            if not isinstance(raw, dict) or _safe_float(raw.get("ts"), now) < cutoff:
                continue
            signature = self._group_expression_pattern_signature(raw)
            if not signature:
                continue
            evidence = _safe_int(raw.get("evidence_count"), 1, 1, 9999)
            length = _safe_int(raw.get("length"), 0, 0)
            length_total = _safe_int(raw.get("length_total"), length * evidence, 0)
            ts = _safe_float(raw.get("ts"), now)
            first_seen_ts = _safe_float(raw.get("first_seen_ts"), ts)
            features = [
                _single_line(feature, 32)
                for feature in raw.get("features", [])
                if _single_line(feature, 32)
            ] if isinstance(raw.get("features"), list) else []
            marks = raw.get("punctuation") if isinstance(raw.get("punctuation"), dict) else {}
            bucket = buckets.setdefault(
                signature,
                {
                    "id": hashlib.sha1(signature.encode("utf-8")).hexdigest()[:12],
                    "ts": ts,
                    "first_seen_ts": first_seen_ts,
                    "scene": _single_line(raw.get("scene"), 32) or "casual",
                    "features": list(dict.fromkeys(features)),
                    "length_bucket": self._expression_length_bucket(length),
                    "length_total": 0,
                    "evidence_count": 0,
                    "punctuation": {},
                },
            )
            bucket["ts"] = max(_safe_float(bucket.get("ts"), 0.0), ts)
            bucket["first_seen_ts"] = min(_safe_float(bucket.get("first_seen_ts"), ts), first_seen_ts)
            bucket["length_total"] += length_total
            bucket["evidence_count"] += evidence
            bucket_marks = bucket["punctuation"]
            for mark, count in marks.items():
                value = _safe_int(count, 0, 0)
                if value > 0:
                    bucket_marks[str(mark)] = _safe_int(bucket_marks.get(str(mark)), 0, 0) + value
        patterns = []
        for bucket in buckets.values():
            evidence = max(1, _safe_int(bucket.get("evidence_count"), 1, 1))
            bucket["length"] = round(_safe_int(bucket.get("length_total"), 0, 0) / evidence)
            bucket["pattern_status"] = "active" if evidence >= 2 else "observing"
            patterns.append(bucket)
        patterns.sort(key=lambda item: (-_safe_int(item.get("evidence_count"), 0, 0), -_safe_float(item.get("ts"), 0.0)))
        return patterns[: runtime_persona_setting(self, "max_learned_expression_items", 60)]

    def _normalize_group_expression_profile(self, profile: dict[str, Any], *, now: float | None = None) -> bool:
        if not isinstance(profile, dict):
            return False
        before = profile.get("samples") if isinstance(profile.get("samples"), list) else []
        patterns = self._group_expression_pattern_samples(profile, now=now)
        previous_by_id = {
            _single_line(item.get("id"), 40): item
            for item in before if isinstance(item, dict) and _single_line(item.get("id"), 40)
        }
        for pattern in patterns:
            previous = previous_by_id.get(_single_line(pattern.get("id"), 40))
            binding = previous.get("scope_binding") if isinstance(previous, dict) and isinstance(previous.get("scope_binding"), dict) else None
            if binding is None:
                continue
            pattern["scope_binding"] = deepcopy(binding)
            old_content = dict(previous)
            old_content.pop("scope_binding", None)
            new_content = dict(pattern)
            new_content.pop("scope_binding", None)
            if old_content != new_content:
                pattern["scope_binding"]["revision"] = max(
                    1, _safe_int(pattern["scope_binding"].get("revision"), 1, 1) + 1,
                )
        changed = before != patterns
        profile["samples"] = patterns
        profile["pattern_count"] = len(patterns)
        profile["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        self._refresh_expression_profile_legacy_summary(profile)
        return changed

    @staticmethod
    def _expression_rule_signature(item: dict[str, Any]) -> str:
        def compact(value: Any, limit: int) -> str:
            return re.sub(
                r"[\s，。！？!?、；;：:‘’“”\"']",
                "",
                _single_line(value, limit).lower(),
            )

        parts = (
            compact(item.get("kind"), 16),
            compact(item.get("situation"), 80),
            compact(item.get("pattern"), 100),
            compact(item.get("instruction"), 140),
        )
        return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _expression_rule_source_parts(source_text: str, *, source_kind: str) -> tuple[list[str], set[str]]:
        utterances: list[str] = []
        speaker_names: set[str] = set()
        for raw_line in str(source_text or "").splitlines():
            line = raw_line.strip()
            match = re.match(
                r"^(?:\d{2}-\d{2}\s+\d{2}:\d{2}\s+)?([^:：]{1,40})[:：]\s*(.*)$",
                line,
            )
            if not match:
                continue
            speaker, content = match.groups()
            speaker = speaker.strip()
            content = _single_line(content, 260)
            if not content:
                continue
            if source_kind == "private":
                if speaker != "用户":
                    continue
            else:
                if speaker:
                    speaker_names.add(speaker)
            utterances.append(content)
        return utterances, speaker_names

    @staticmethod
    def _expression_rule_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        return _single_line(value, 16).lower() in {"1", "true", "yes", "on", "是", "有", "冲突"}

    @staticmethod
    def _normalize_expression_rule_values(
        value: Any,
        *,
        allowed: set[str],
        aliases: dict[str, str],
        defaults: list[str],
    ) -> list[str]:
        if isinstance(value, str):
            raw_values = re.split(r"[,，/、|\s]+", value)
        elif isinstance(value, list):
            raw_values = value
        else:
            raw_values = []
        normalized: list[str] = []
        for raw in raw_values:
            item = _single_line(raw, 24).lower()
            item = aliases.get(item, item)
            if item == "all":
                item = "any"
            if item in allowed and item not in normalized:
                normalized.append(item)
        return normalized or list(defaults)

    def _normalize_expression_rule_channels(self, value: Any, *, source_kind: str) -> list[str]:
        # 来源与使用范围是两件事。群聊里学到的脱敏表达默认也可以用于
        # 已配置的私聊/主动消息目标，最终仍会经过频道、关系和审核门控。
        defaults = ["private", "group", "proactive"] if source_kind == "group" else ["private", "proactive"]
        return self._normalize_expression_rule_values(
            value,
            allowed={"private", "group", "proactive", "qzone", "tts"},
            aliases={
                "私聊": "private",
                "群聊": "group",
                "主动": "proactive",
                "主动消息": "proactive",
                "空间": "qzone",
                "qq空间": "qzone",
                "说说": "qzone",
                "语音": "tts",
            },
            defaults=defaults,
        )

    def _normalize_expression_relationship_stages(self, value: Any) -> list[str]:
        return self._normalize_expression_rule_values(
            value,
            allowed={"any", "stranger", "familiar", "close"},
            aliases={"不限": "any", "任意": "any", "陌生": "stranger", "熟悉": "familiar", "亲近": "close"},
            defaults=["any"],
        )

    def _normalize_expression_emotion_gates(self, value: Any) -> list[str]:
        return self._normalize_expression_rule_values(
            value,
            allowed={"any", "normal", "positive", "low", "guarded"},
            aliases={
                "不限": "any",
                "任意": "any",
                "普通": "normal",
                "中性": "normal",
                "轻松": "positive",
                "积极": "positive",
                "低落": "low",
                "安抚": "low",
                "防备": "guarded",
                "边界": "guarded",
            },
            defaults=["any"],
        )

    @staticmethod
    def _normalize_expression_intent(value: Any) -> str:
        intent = _single_line(value, 32).lower()
        aliases = {
            "不限": "any",
            "任意": "any",
            "确认": "acknowledgement",
            "提问": "question",
            "请求": "request",
            "求助": "help",
            "安抚": "comfort",
            "玩笑": "play",
            "亲近": "intimacy",
            "边界": "boundary",
            "情绪": "emotion",
            "闲聊": "casual",
            "主动": "proactive",
        }
        intent = aliases.get(intent, intent)
        allowed = {
            "any",
            "acknowledgement",
            "question",
            "request",
            "help",
            "comfort",
            "play",
            "tease",
            "intimacy",
            "boundary",
            "emotion",
            "casual",
            "proactive",
        }
        return intent if intent in allowed else "any"

    @staticmethod
    def _expression_rule_payload_candidates(payload: Any) -> list[dict[str, Any]]:
        """兼容旧 expression_rules，并优先接收 WaifuBot 式的双分类结果。"""
        if not isinstance(payload, dict):
            return []
        result: list[dict[str, Any]] = []
        sections = (
            ("style_expressions", "style"),
            ("grammar_expressions", "grammar"),
            ("expression_rules", ""),
        )
        for key, forced_kind in sections:
            values = payload.get(key)
            if not isinstance(values, list):
                continue
            for raw in values:
                if not isinstance(raw, dict):
                    continue
                item = dict(raw)
                if forced_kind:
                    item["kind"] = forced_kind
                pattern = _single_line(item.get("pattern") or item.get("style"), 120)
                if pattern and not _single_line(item.get("instruction"), 160):
                    if _single_line(item.get("kind"), 16).lower() == "grammar":
                        item["instruction"] = f"在匹配情境中采用“{pattern}”的句法，内容仍按当前事实生成"
                    else:
                        item["instruction"] = f"在匹配情境中自然使用或轻微改写“{pattern}”，不要机械复读"
                if "keywords" not in item and isinstance(item.get("tags"), list):
                    item["keywords"] = list(item.get("tags") or [])
                result.append(item)
                if len(result) >= 12:
                    return result
        return result

    def _expression_rule_generation_reference(
        self,
        profile: Any,
        *,
        hint: str = "",
        limit: int = 14,
    ) -> str:
        if not isinstance(profile, dict):
            return "- 暂无已有规则；只在证据充分时新增，不要为了凑数输出。"
        query = _single_line(hint, 6000).lower()
        query_key = self._expression_rule_pattern_key(query)
        rows: list[tuple[float, str]] = []
        for storage_key, status in (("learned_rules", "已启用"), ("pending_rules", "待审核")):
            rules = profile.get(storage_key) if isinstance(profile.get(storage_key), list) else []
            for raw in rules:
                if not isinstance(raw, dict) or not self._expression_rule_definition_is_valid(raw):
                    continue
                rule_id = _single_line(raw.get("id"), 40)
                kind = _single_line(raw.get("kind"), 16).lower()
                situation = _single_line(raw.get("situation"), 70)
                pattern = _single_line(raw.get("pattern") or raw.get("style"), 80)
                if not rule_id or not situation or not pattern:
                    continue
                keywords = [
                    _single_line(value, 24)
                    for value in (raw.get("keywords") if isinstance(raw.get("keywords"), list) else [])
                    if _single_line(value, 24)
                ][:5]
                matched = sum(1 for keyword in keywords if query and keyword.lower() in query)
                pattern_key = self._expression_rule_pattern_key(pattern)
                if query_key and pattern_key and pattern_key in query_key:
                    matched += 2
                score = (
                    matched * 20
                    + min(12, _safe_int(raw.get("evidence_count"), 0, 0))
                    + min(5, _safe_int(raw.get("use_count"), 0, 0))
                    + min(3.0, _safe_float(raw.get("last_seen_ts"), 0.0) / max(1.0, _now_ts()) * 3.0)
                )
                intent = self._normalize_expression_intent(raw.get("intent"))
                rows.append((
                    score,
                    f"- {status} {kind} id={rule_id}｜情境：{situation}｜模板：{pattern}"
                    + (f"｜意图：{intent}" if intent != "any" else "")
                    + (f"｜标签：{'、'.join(keywords)}" if keywords else ""),
                ))
        if not rows:
            return "- 暂无已有规则；只在证据充分时新增，不要为了凑数输出。"
        rows.sort(key=lambda item: item[0], reverse=True)
        return "\n".join(text for _, text in rows[: max(4, min(20, limit))])

    @staticmethod
    def _expression_style_pattern_is_reusable(pattern: str) -> bool:
        value = _single_line(pattern, 100)
        if len(value) < 2 or len(value) > 64:
            return False
        quoted = re.fullmatch(r"[“\"‘'](.{2,48})[”\"’']", value)
        if quoted:
            value = quoted.group(1).strip()
        compact = re.sub(r"[\s，。！？!?、；;：:]", "", value)
        if compact in {
            "短句", "长句", "柔和收尾", "轻松语气", "自然表达", "口语化表达",
            "先确认再补充", "先接住再延续", "简短回应", "语气自然",
        }:
            return False
        has_template_marker = bool(re.search(r"_{2,}|\[[^\]]{1,20}\]|[（(][^）)]{1,20}[）)]|[“\"].{1,30}[”\"]", value))
        meta_description = bool(
            re.search(
                r"(?:偏好|习惯|倾向|通常|经常|多用|常用|口语化|书面化|"
                r"语气|风格|句式|句法|字数|主语|拆句|铺垫|柔和收尾|"
                r"表达内容|表达方式|回应时|回复时|句子结构|长篇大论)",
                value,
            )
        )
        looks_like_instruction = bool(
            re.match(
                r"^(?:先|使用|采用|保持|表达|回复|回应|开头|结尾|收尾|"
                r"语气|句式|句法|短句|长句|直接|简短|自然|柔和)",
                value,
            )
        )
        if meta_description or looks_like_instruction:
            return False
        return has_template_marker or len(value) <= 32

    @staticmethod
    def _expression_grammar_pattern_is_specific(pattern: str) -> bool:
        value = _single_line(pattern, 100)
        if len(value) < 4 or len(value) > 80:
            return False
        if re.search(r"(?:语气自然|自然表达|口语化表达|表达简洁|说话直接)$", value):
            return False
        return bool(
            re.search(
                r"(?:主语|省略|\d+\s*[—–~-]\s*\d+\s*字|\d+\s*字|"
                r"[一二三四五六七八九十]+\s*[—–~-]\s*[一二三四五六七八九十]+\s*字|"
                r"短句|长句|单句|双句|两句|拆句|断句|反问|祈使|问句|"
                r"感叹句|陈述句|倒装|重复|叠词|标点|停顿|句首|句尾|连接词)",
                value,
            )
        )

    def _expression_rule_definition_is_valid(self, raw_rule: Any) -> bool:
        if not isinstance(raw_rule, dict):
            return False
        kind = _single_line(raw_rule.get("kind") or raw_rule.get("type"), 16).lower()
        situation = _single_line(raw_rule.get("situation"), 100)
        pattern = _single_line(raw_rule.get("pattern") or raw_rule.get("style"), 100)
        instruction = _single_line(raw_rule.get("instruction"), 160)
        if kind not in {"style", "grammar"} or not situation or not pattern or not instruction:
            return False
        if kind == "style":
            return self._expression_style_pattern_is_reusable(pattern)
        return self._expression_grammar_pattern_is_specific(pattern)

    def _prune_invalid_expression_rules(self, profile: dict[str, Any]) -> bool:
        if not isinstance(profile, dict):
            return False
        changed = False
        for storage_key in ("pending_rules", "learned_rules"):
            existing = profile.get(storage_key)
            if not isinstance(existing, list):
                continue
            kept = [
                item
                for item in existing
                if (
                    self._expression_rule_definition_is_valid(item)
                    and _safe_int(item.get("evidence_count"), 0, 0) >= 1
                )
            ]
            if kept != existing:
                profile[storage_key] = kept
                changed = True
            if self._assign_expression_rule_families(kept):
                changed = True
            if self._deduplicate_expression_rule_families(kept):
                profile[storage_key] = kept
                changed = True
        return changed

    @staticmethod
    def _expression_rule_evidence_key(value: Any) -> str:
        text = _single_line(value, 96).lower()
        return re.sub(r"[\s，。！？!?、；;：:‘’“”\"'（）()【】\[\]<>《》~～…—–_-]", "", text)

    @staticmethod
    def _expression_rule_text_similarity(left: Any, right: Any) -> float:
        def grams(value: Any) -> set[str]:
            compact = re.sub(
                r"[\s，。！？!?、；;：:‘’“”\"'（）()【】\[\]<>《》~～…—–_-]",
                "",
                _single_line(value, 160).lower(),
            )
            if not compact:
                return set()
            if len(compact) == 1:
                return {compact}
            return {compact[index:index + 2] for index in range(len(compact) - 1)}

        left_grams = grams(left)
        right_grams = grams(right)
        if not left_grams or not right_grams:
            return 0.0
        return len(left_grams & right_grams) / max(1, len(left_grams | right_grams))

    @staticmethod
    def _expression_rule_pattern_key(value: Any) -> str:
        text = _single_line(value, 120).lower()
        text = re.sub(r"_{2,}|\[[^\]]{1,24}\]|[（(][^）)]{1,24}[）)]", "<slot>", text)
        return re.sub(
            r"[\s，。！？!?、；;：:‘’“”\"'~～…—–_-]",
            "",
            text,
        )

    @staticmethod
    def _expression_rule_value_set(value: Any, *, limit: int = 24) -> set[str]:
        if not isinstance(value, list):
            return set()
        return {
            normalized
            for item in value
            if (normalized := _single_line(item, limit).lower())
        }

    def _expression_rule_contexts_compatible(self, left: dict[str, Any], right: dict[str, Any]) -> bool:
        def compatible(field: str) -> bool:
            left_values = self._expression_rule_value_set(left.get(field))
            right_values = self._expression_rule_value_set(right.get(field))
            if not left_values or not right_values or "any" in left_values or "any" in right_values:
                return True
            return bool(left_values & right_values)

        if not all(compatible(field) for field in ("channels", "relationship_stages", "emotion_gates")):
            return False
        left_intent = self._normalize_expression_intent(left.get("intent"))
        right_intent = self._normalize_expression_intent(right.get("intent"))
        if "any" in {left_intent, right_intent} or left_intent == right_intent:
            return True
        compatible_intent_groups = (
            {"play", "tease", "intimacy"},
            {"question", "request", "help"},
            {"comfort", "emotion"},
            {"acknowledgement", "casual"},
        )
        return any({left_intent, right_intent}.issubset(group) for group in compatible_intent_groups)

    def _expression_rule_duplicate_analysis(
        self,
        left: Any,
        right: Any,
    ) -> dict[str, Any]:
        if not isinstance(left, dict) or not isinstance(right, dict):
            return {}
        left_kind = _single_line(left.get("kind"), 16).lower()
        right_kind = _single_line(right.get("kind"), 16).lower()
        if left_kind not in {"style", "grammar"} or left_kind != right_kind:
            return {}

        left_pattern = left.get("pattern") or left.get("style")
        right_pattern = right.get("pattern") or right.get("style")
        left_key = self._expression_rule_pattern_key(left_pattern)
        right_key = self._expression_rule_pattern_key(right_pattern)
        if not left_key or not right_key:
            return {}

        context_compatible = self._expression_rule_contexts_compatible(left, right)
        manually_edited = bool(left.get("manually_edited") or right.get("manually_edited"))
        left_examples = {
            key
            for value in (left.get("evidence_examples") if isinstance(left.get("evidence_examples"), list) else [])
            if (key := self._expression_rule_evidence_key(value))
        }
        right_examples = {
            key
            for value in (right.get("evidence_examples") if isinstance(right.get("evidence_examples"), list) else [])
            if (key := self._expression_rule_evidence_key(value))
        }
        shared_evidence = bool(left_examples & right_examples)
        pattern_similarity = self._expression_rule_text_similarity(left_pattern, right_pattern)
        situation_similarity = self._expression_rule_text_similarity(
            left.get("situation"),
            right.get("situation"),
        )
        left_keywords = self._expression_rule_value_set(left.get("keywords") or left.get("tags"))
        right_keywords = self._expression_rule_value_set(right.get("keywords") or right.get("tags"))
        keyword_overlap = len(left_keywords & right_keywords)

        if left_key == right_key:
            return {
                "code": "same_pattern" if context_compatible else "same_pattern_distinct_context",
                "confidence": 0.99 if context_compatible else 0.72,
                "auto_merge": bool(context_compatible and not manually_edited),
                "pattern_similarity": 1.0,
                "situation_similarity": situation_similarity,
                "shared_evidence": shared_evidence,
                "reason": (
                    "同类规则使用相同表达模板，适用上下文兼容"
                    if context_compatible
                    else "同类规则使用相同模板，但适用上下文存在差异"
                ),
            }
        if shared_evidence and pattern_similarity >= 0.62:
            return {
                "code": "shared_evidence_variant",
                "confidence": 0.96 if context_compatible else 0.82,
                "auto_merge": bool(context_compatible and not manually_edited),
                "pattern_similarity": pattern_similarity,
                "situation_similarity": situation_similarity,
                "shared_evidence": True,
                "reason": "同类规则由相同支持片段归纳，模板只是占位符或语气变体",
            }
        if pattern_similarity >= 0.78 and (situation_similarity >= 0.25 or keyword_overlap >= 1):
            return {
                "code": "near_pattern_context",
                "confidence": min(0.93, 0.72 + pattern_similarity * 0.14 + situation_similarity * 0.12),
                "auto_merge": False,
                "pattern_similarity": pattern_similarity,
                "situation_similarity": situation_similarity,
                "shared_evidence": shared_evidence,
                "reason": "模板和适用情境高度相近，建议人工确认是否保留两个规则组",
            }
        if pattern_similarity >= 0.84:
            return {
                "code": "near_pattern",
                "confidence": min(0.86, 0.68 + pattern_similarity * 0.18),
                "auto_merge": False,
                "pattern_similarity": pattern_similarity,
                "situation_similarity": situation_similarity,
                "shared_evidence": shared_evidence,
                "reason": "表达模板高度相似，但现有情境证据不足以自动合并",
            }
        return {}

    def _merge_expression_rule_duplicate_metadata(
        self,
        target: dict[str, Any],
        incoming: dict[str, Any],
    ) -> None:
        scope_before = dict(target)
        scope_before.pop("scope_binding", None)
        for field, limit in (("keywords", 8), ("tags", 8), ("evidence_examples", 3), ("source_kinds", 8)):
            left_values = target.get(field) if isinstance(target.get(field), list) else []
            right_values = incoming.get(field) if isinstance(incoming.get(field), list) else []
            target[field] = list(dict.fromkeys([
                *[str(item) for item in left_values if str(item).strip()],
                *[str(item) for item in right_values if str(item).strip()],
            ]))[:limit]
        if target.get("keywords"):
            target["tags"] = list(target["keywords"])
        for field in ("channels", "relationship_stages", "emotion_gates"):
            target[field] = list(dict.fromkeys([
                *sorted(self._expression_rule_value_set(target.get(field))),
                *sorted(self._expression_rule_value_set(incoming.get(field))),
            ]))[:8]

        target_intent = self._normalize_expression_intent(target.get("intent"))
        incoming_intent = self._normalize_expression_intent(incoming.get("intent"))
        if target_intent == "any":
            target["intent"] = incoming_intent
        elif incoming_intent == "any" or target_intent == incoming_intent:
            target["intent"] = target_intent
        else:
            target["intent"] = "any"
        incoming_avoid = _single_line(incoming.get("avoid"), 160)
        if incoming_avoid and len(incoming_avoid) > len(_single_line(target.get("avoid"), 160)):
            target["avoid"] = incoming_avoid
        if not _single_line(target.get("label"), 100) and _single_line(incoming.get("label"), 100):
            target["label"] = _single_line(incoming.get("label"), 100)
        target["persona_conflict"] = bool(
            self._expression_rule_bool(target.get("persona_conflict"))
            or self._expression_rule_bool(incoming.get("persona_conflict"))
        )

        target_batch = _single_line(target.get("last_batch_key"), 80)
        incoming_batch = _single_line(incoming.get("last_batch_key"), 80)
        target_evidence = _safe_int(target.get("evidence_count"), 0, 0)
        incoming_evidence = _safe_int(incoming.get("evidence_count"), 0, 0)
        if target_batch and incoming_batch and target_batch == incoming_batch:
            target["evidence_count"] = max(target_evidence, incoming_evidence)
        else:
            target["evidence_count"] = min(99, target_evidence + incoming_evidence)
        for field, ceiling in (("positive_feedback", 999), ("negative_feedback", 999), ("use_count", 99999)):
            target[field] = min(
                ceiling,
                _safe_int(target.get(field), 0, 0) + _safe_int(incoming.get(field), 0, 0),
            )
        target["last_seen_ts"] = max(
            _safe_float(target.get("last_seen_ts"), 0.0),
            _safe_float(incoming.get("last_seen_ts"), 0.0),
        )
        target["last_used_ts"] = max(
            _safe_float(target.get("last_used_ts"), 0.0),
            _safe_float(incoming.get("last_used_ts"), 0.0),
        )
        created_values = [
            value
            for value in (
                _safe_float(target.get("created_ts"), 0.0),
                _safe_float(incoming.get("created_ts"), 0.0),
            )
            if value > 0
        ]
        if created_values:
            target["created_ts"] = min(created_values)
        if _safe_float(incoming.get("last_seen_ts"), 0.0) >= _safe_float(target.get("last_seen_ts"), 0.0):
            if incoming_batch:
                target["last_batch_key"] = incoming_batch

        source_refs = []
        seen_refs: set[tuple[str, str, str]] = set()
        for raw_ref in [
            *(target.get("source_refs") if isinstance(target.get("source_refs"), list) else []),
            *(incoming.get("source_refs") if isinstance(incoming.get("source_refs"), list) else []),
        ]:
            if not isinstance(raw_ref, dict):
                continue
            ref = {
                "source_kind": _single_line(raw_ref.get("source_kind"), 24),
                "source_id": _single_line(raw_ref.get("source_id"), 80),
                "rule_id": _single_line(raw_ref.get("rule_id"), 40),
            }
            key = (ref["source_kind"], ref["source_id"], ref["rule_id"])
            if all(key) and key not in seen_refs:
                seen_refs.add(key)
                source_refs.append(ref)
        if source_refs:
            target["source_refs"] = source_refs[:24]
        scope_binding = target.get("scope_binding") if isinstance(target.get("scope_binding"), dict) else None
        scope_after = dict(target)
        scope_after.pop("scope_binding", None)
        if scope_binding is not None and scope_before != scope_after:
            scope_binding["revision"] = max(1, _safe_int(scope_binding.get("revision"), 1, 1) + 1)

    @staticmethod
    def _expression_rule_family_priority(items: list[dict[str, Any]]) -> tuple[int, int, int, float]:
        return (
            sum(_safe_int(item.get("evidence_count"), 0, 0) for item in items),
            sum(_safe_int(item.get("use_count"), 0, 0) for item in items),
            sum(1 for item in items if re.search(r"_{2,}|\[[^\]]+\]", _single_line(item.get("pattern"), 100))),
            max((_safe_float(item.get("last_seen_ts"), 0.0) for item in items), default=0.0),
        )

    def _deduplicate_expression_rule_families(self, rules: Any) -> bool:
        if not isinstance(rules, list) or len(rules) < 2:
            return False
        self._assign_expression_rule_families(rules)
        groups = self._expression_rule_groups(rules)
        kept: list[list[dict[str, Any]]] = []
        changed = False

        def anchor(items: list[dict[str, Any]]) -> dict[str, Any] | None:
            return next(
                (item for item in items if _single_line(item.get("kind"), 16).lower() == "style"),
                next((item for item in items if isinstance(item, dict)), None),
            )

        for group in groups:
            current_anchor = anchor(group)
            if current_anchor is None:
                continue
            matched_index = -1
            for index, existing_group in enumerate(kept):
                existing_anchor = anchor(existing_group)
                analysis = self._expression_rule_duplicate_analysis(existing_anchor, current_anchor)
                if analysis.get("auto_merge"):
                    matched_index = index
                    break
            if matched_index < 0:
                kept.append(group)
                continue

            target_group = kept[matched_index]
            incoming_group = group
            if self._expression_rule_family_priority(incoming_group) > self._expression_rule_family_priority(target_group):
                target_group, incoming_group = incoming_group, target_group
                kept[matched_index] = target_group
            target_by_kind = {
                _single_line(item.get("kind"), 16).lower(): item
                for item in target_group
                if isinstance(item, dict)
            }
            for incoming in incoming_group:
                if not isinstance(incoming, dict):
                    continue
                kind = _single_line(incoming.get("kind"), 16).lower()
                target = target_by_kind.get(kind)
                if target is None:
                    target_group.append(incoming)
                    target_by_kind[kind] = incoming
                else:
                    self._merge_expression_rule_duplicate_metadata(target, incoming)
            family_key = next(
                (
                    _single_line(item.get("family_key"), 80).lower()
                    for item in target_group
                    if _single_line(item.get("family_key"), 80)
                ),
                "",
            )
            if not family_key:
                seed = "|".join(sorted(_single_line(item.get("id"), 100) for item in target_group))
                family_key = f"merged_{hashlib.sha1(seed.encode('utf-8')).hexdigest()[:12]}"
            for item in target_group:
                item["family_key"] = family_key
            changed = True

        if not changed:
            return False
        rules[:] = [item for group in kept for item in group]
        self._assign_expression_rule_families(rules)
        return True

    def _expression_rule_pair_score(self, style: dict[str, Any], grammar: dict[str, Any]) -> float:
        style_family = _single_line(style.get("family_id"), 64)
        grammar_family = _single_line(grammar.get("family_id"), 64)
        if style_family and style_family == grammar_family and not style_family.startswith("xs-"):
            return 1000.0

        style_key = _single_line(style.get("family_key"), 80).lower()
        grammar_key = _single_line(grammar.get("family_key"), 80).lower()
        if style_key and style_key == grammar_key:
            return 900.0

        style_examples = {
            key
            for value in (style.get("evidence_examples") if isinstance(style.get("evidence_examples"), list) else [])
            if (key := self._expression_rule_evidence_key(value))
        }
        grammar_examples = {
            key
            for value in (grammar.get("evidence_examples") if isinstance(grammar.get("evidence_examples"), list) else [])
            if (key := self._expression_rule_evidence_key(value))
        }
        overlap_count = len(style_examples & grammar_examples)
        overlap_ratio = overlap_count / max(1, max(len(style_examples), len(grammar_examples)))
        situation_similarity = self._expression_rule_text_similarity(
            style.get("situation"),
            grammar.get("situation"),
        )
        style_keywords = {
            _single_line(value, 24).lower()
            for value in (style.get("keywords") if isinstance(style.get("keywords"), list) else [])
            if _single_line(value, 24)
        }
        grammar_keywords = {
            _single_line(value, 24).lower()
            for value in (grammar.get("keywords") if isinstance(grammar.get("keywords"), list) else [])
            if _single_line(value, 24)
        }
        keyword_overlap = len(style_keywords & grammar_keywords)
        same_batch = bool(
            _single_line(style.get("last_batch_key"), 80)
            and _single_line(style.get("last_batch_key"), 80)
            == _single_line(grammar.get("last_batch_key"), 80)
        )
        same_evidence_count = _safe_int(style.get("evidence_count"), 0, 0) == _safe_int(
            grammar.get("evidence_count"), 0, 0
        )

        # 旧规则没有 family_key，只在支持片段确实重叠时自动配对；
        # 同批次、情境高度相近只作为辅助，避免把同一批中的不同规则强行绑在一起。
        if overlap_ratio >= 0.45:
            return 500.0 + overlap_ratio * 100 + situation_similarity * 20 + min(2, keyword_overlap) * 5
        if overlap_count and same_batch and situation_similarity >= 0.3:
            return 430.0 + situation_similarity * 40 + min(2, keyword_overlap) * 5
        if same_batch and same_evidence_count and situation_similarity >= 0.72 and keyword_overlap:
            return 320.0 + situation_similarity * 40 + min(2, keyword_overlap) * 5
        return -1.0

    def _assign_expression_rule_families(
        self,
        rules: Any,
        *,
        batch_key: str = "",
    ) -> bool:
        if not isinstance(rules, list):
            return False
        valid_rules = [item for item in rules if isinstance(item, dict)]
        if not valid_rules:
            return False
        for item in valid_rules:
            if batch_key and not _single_line(item.get("last_batch_key"), 80):
                item["last_batch_key"] = _single_line(batch_key, 80)
            family_key = _single_line(item.get("family_key"), 80).lower()
            if family_key:
                item["family_key"] = family_key

        styles = [item for item in valid_rules if _single_line(item.get("kind"), 16).lower() == "style"]
        grammars = [item for item in valid_rules if _single_line(item.get("kind"), 16).lower() == "grammar"]
        pair_candidates: list[tuple[float, dict[str, Any], dict[str, Any]]] = []
        for style in styles:
            for grammar in grammars:
                score = self._expression_rule_pair_score(style, grammar)
                if score >= 0:
                    pair_candidates.append((score, style, grammar))
        pair_candidates.sort(key=lambda item: item[0], reverse=True)

        paired_ids: set[int] = set()
        family_by_object: dict[int, str] = {}
        for _, style, grammar in pair_candidates:
            if id(style) in paired_ids or id(grammar) in paired_ids:
                continue
            rule_ids = sorted([
                value
                for value in (
                    _single_line(style.get("id"), 100) or self._expression_rule_signature(style),
                    _single_line(grammar.get("id"), 100) or self._expression_rule_signature(grammar),
                )
                if value
            ])
            family_seed = "|".join(rule_ids)
            family_id = f"xf-{hashlib.sha1(family_seed.encode('utf-8')).hexdigest()[:16]}"
            family_by_object[id(style)] = family_id
            family_by_object[id(grammar)] = family_id
            paired_ids.update({id(style), id(grammar)})

        changed = False
        for item in valid_rules:
            family_id = family_by_object.get(id(item))
            if not family_id:
                rule_id = _single_line(item.get("id"), 100) or self._expression_rule_signature(item)
                family_id = f"xs-{hashlib.sha1(rule_id.encode('utf-8')).hexdigest()[:16]}"
            if _single_line(item.get("family_id"), 64) != family_id:
                item["family_id"] = family_id
                changed = True
        return changed

    def _backfill_expression_rule_families(self, profile: Any) -> bool:
        if not isinstance(profile, dict):
            return False
        changed = False
        for storage_key in ("pending_rules", "learned_rules"):
            rules = profile.get(storage_key)
            if isinstance(rules, list) and self._assign_expression_rule_families(rules):
                changed = True
        return changed

    def _expression_rule_groups(self, rules: Any) -> list[list[dict[str, Any]]]:
        if not isinstance(rules, list):
            return []
        self._assign_expression_rule_families(rules)
        groups: dict[str, list[dict[str, Any]]] = {}
        order: list[str] = []
        for item in rules:
            if not isinstance(item, dict):
                continue
            family_id = _single_line(item.get("family_id"), 64)
            if not family_id:
                continue
            if family_id not in groups:
                groups[family_id] = []
                order.append(family_id)
            groups[family_id].append(item)
        return [groups[family_id] for family_id in order]

    def _expression_rule_runtime_bundle(self, group: Any) -> dict[str, Any]:
        items = [dict(item) for item in group if isinstance(item, dict)] if isinstance(group, list) else []
        if not items:
            return {}
        items.sort(key=lambda item: 0 if _single_line(item.get("kind"), 16).lower() == "style" else 1)
        style_rule = next((item for item in items if _single_line(item.get("kind"), 16).lower() == "style"), None)
        grammar_rule = next((item for item in items if _single_line(item.get("kind"), 16).lower() == "grammar"), None)
        primary = style_rule or grammar_rule or items[0]
        family_id = _single_line(primary.get("family_id"), 64)
        bundle = dict(primary)
        bundle["id"] = family_id if len(items) > 1 else _single_line(primary.get("id"), 100)
        bundle["family_id"] = family_id
        bundle["kind"] = "combined" if style_rule and grammar_rule else _single_line(primary.get("kind"), 16).lower()
        bundle["component_kinds"] = [
            kind for kind in ("style", "grammar")
            if any(_single_line(item.get("kind"), 16).lower() == kind for item in items)
        ]
        bundle["component_count"] = len(items)
        bundle["component_rules"] = items
        bundle["style_rule"] = dict(style_rule) if style_rule else None
        bundle["grammar_rule"] = dict(grammar_rule) if grammar_rule else None
        bundle["evidence_count"] = max(_safe_int(item.get("evidence_count"), 0, 0) for item in items)
        bundle["positive_feedback"] = max(_safe_int(item.get("positive_feedback"), 0, 0) for item in items)
        bundle["negative_feedback"] = max(_safe_int(item.get("negative_feedback"), 0, 0) for item in items)
        bundle["use_count"] = max(_safe_int(item.get("use_count"), 0, 0) for item in items)
        bundle["last_seen_ts"] = max(_safe_float(item.get("last_seen_ts"), 0.0) for item in items)
        bundle["last_used_ts"] = max(_safe_float(item.get("last_used_ts"), 0.0) for item in items)
        for field, limit in (("keywords", 8), ("tags", 8), ("signals", 8), ("channels", 8), ("relationship_stages", 8), ("emotion_gates", 8)):
            values: list[str] = []
            for item in items:
                for value in item.get(field, []) if isinstance(item.get(field), list) else []:
                    normalized = _single_line(value, 32)
                    if normalized and normalized not in values:
                        values.append(normalized)
            bundle[field] = values[:limit]
        examples: list[str] = []
        refs: list[dict[str, str]] = []
        ref_keys: set[tuple[str, str, str]] = set()
        for item in items:
            for example in item.get("evidence_examples", []) if isinstance(item.get("evidence_examples"), list) else []:
                value = _single_line(example, 80)
                if value and value not in examples:
                    examples.append(value)
            for raw_ref in item.get("source_refs", []) if isinstance(item.get("source_refs"), list) else []:
                if not isinstance(raw_ref, dict):
                    continue
                ref = {
                    "source_kind": _single_line(raw_ref.get("source_kind"), 16).lower(),
                    "source_id": _single_line(raw_ref.get("source_id"), 80),
                    "rule_id": _single_line(raw_ref.get("rule_id"), 40),
                }
                key = (ref["source_kind"], ref["source_id"], ref["rule_id"])
                if all(key) and key not in ref_keys:
                    ref_keys.add(key)
                    refs.append(ref)
        bundle["evidence_examples"] = examples[:6]
        bundle["source_refs"] = refs
        avoid_values = [
            _single_line(item.get("avoid"), 160)
            for item in items
            if _single_line(item.get("avoid"), 160)
        ]
        bundle["avoid"] = "；".join(dict.fromkeys(avoid_values))[:240]
        bundle["persona_conflict"] = any(self._expression_rule_bool(item.get("persona_conflict")) for item in items)
        return bundle

    @staticmethod
    def _normalize_expression_evidence_examples(
        value: Any,
        *,
        source_kind: str,
        source_names: set[str],
    ) -> list[str]:
        if not isinstance(value, list):
            return []
        result: list[str] = []
        for raw in value:
            example = _single_line(raw, 72)
            example = re.sub(r"^[^:：]{1,32}[:：]\s*", "", example).strip()
            if not example or re.search(r"https?://|@|\b\d{5,}\b|QQ|群号|用户ID", example, re.IGNORECASE):
                continue
            if source_kind == "group" and any(name and name in example for name in source_names):
                continue
            if example not in result:
                result.append(example)
            if len(result) >= 3:
                break
        return result

    def _normalize_expression_rule_candidates(
        self,
        raw_rules: Any,
        *,
        source_kind: str,
        source_text: str = "",
    ) -> list[dict[str, Any]]:
        if not isinstance(raw_rules, list):
            return []
        source_utterances, source_names = self._expression_rule_source_parts(
            source_text,
            source_kind=source_kind,
        )
        compact_utterances = {
            re.sub(r"\s+", "", line).lower()
            for line in source_utterances
            if line.strip()
        }
        result: list[dict[str, Any]] = []
        for raw in raw_rules[:12]:
            if not isinstance(raw, dict):
                continue
            kind = _single_line(raw.get("kind") or raw.get("type"), 16).lower()
            if kind not in {"style", "grammar"}:
                continue
            situation = _single_line(raw.get("situation"), 80)
            pattern = _single_line(raw.get("pattern") or raw.get("style"), 100)
            instruction = _single_line(raw.get("instruction"), 140)
            avoid = _single_line(raw.get("avoid"), 160)
            evidence_examples = self._normalize_expression_evidence_examples(
                raw.get("evidence_examples") or raw.get("examples"),
                source_kind=source_kind,
                source_names=source_names,
            )
            evidence = _safe_int(raw.get("evidence_count"), len(evidence_examples) or 1, 1, 20)
            if not situation or not pattern or not instruction:
                continue
            if kind == "style" and not self._expression_style_pattern_is_reusable(pattern):
                continue
            if kind == "grammar" and not self._expression_grammar_pattern_is_specific(pattern):
                continue
            if source_utterances:
                evidence = min(evidence, len(source_utterances))
            if evidence < 1:
                continue
            combined_rule = f"{situation} {pattern} {instruction}"
            if any(marker in combined_rule for marker in ("SELF", "系统提示", "提示词", "用户ID", "群号", "QQ号")):
                continue
            if re.search(r"@|\b\d{5,}\b|QQ|昵称为|ID为", combined_rule, re.IGNORECASE):
                continue
            if source_kind == "group":
                if any(name and name in combined_rule for name in source_names):
                    continue
                # 允许保留短而有辨识度的表达或占位模板，这是 WaifuBot 式学习的核心；
                # 仍拒绝长句照搬、成员身份和账号等不可迁移内容。
                compact_pattern = re.sub(r"\s+", "", pattern).lower()
                if len(pattern) > 48 and any(len(line) >= 16 and line in compact_pattern for line in compact_utterances):
                    continue
                quoted = re.findall(r"[“\"‘']([^”\"’']{2,40})[”\"’']", combined_rule)
                if any(
                    len(quote) > 24 and re.sub(r"\s+", "", quote).lower() in line
                    for quote in quoted
                    for line in compact_utterances
                ):
                    continue
            raw_keywords = raw.get("keywords") if isinstance(raw.get("keywords"), list) else raw.get("tags")
            keywords = []
            if isinstance(raw_keywords, list):
                for keyword in raw_keywords:
                    value = _single_line(keyword, 24)
                    if source_kind == "group" and (
                        value in source_names
                        or re.search(r"\d{5,}", value)
                        or re.sub(r"\s+", "", value).lower() in compact_utterances
                    ):
                        continue
                    if len(value) >= 2 and value not in keywords:
                        keywords.append(value)
                    if len(keywords) >= 8:
                        break
            item = {
                "kind": kind,
                "situation": situation,
                "pattern": pattern,
                "instruction": instruction,
                "keywords": keywords,
                "tags": list(keywords),
                "evidence_examples": evidence_examples,
                "evidence_count": evidence,
                "source_kind": source_kind,
                "family_key": _single_line(raw.get("family_key"), 80).lower(),
                "merge_into_id": _single_line(raw.get("merge_into_id"), 40),
                "channels": self._normalize_expression_rule_channels(raw.get("channels"), source_kind=source_kind),
                "relationship_stages": self._normalize_expression_relationship_stages(raw.get("relationship_stages")),
                "emotion_gates": self._normalize_expression_emotion_gates(raw.get("emotion_gates")),
                "intent": self._normalize_expression_intent(raw.get("intent")),
                "avoid": avoid or "事实、工具结果、安全边界或人格发生冲突时不用",
                "persona_conflict": self._expression_rule_bool(raw.get("persona_conflict")) or bool(
                    re.search(
                        r"(?:假装|谎称|声称).{0,12}(?:成功|完成|已发|发过)|"
                        r"(?:无视|覆盖|改写).{0,8}(?:人格|安全|事实|工具结果)|"
                        r"(?:必须|永远|无条件).{0,10}(?:服从|同意|答应)",
                        combined_rule,
                        re.IGNORECASE,
                    )
                ),
                "positive_feedback": 0,
                "negative_feedback": 0,
                "use_count": 0,
            }
            item["id"] = self._expression_rule_signature(item)
            duplicate = next((old for old in result if old.get("id") == item["id"]), None)
            if duplicate is not None:
                duplicate["evidence_count"] = max(
                    _safe_int(duplicate.get("evidence_count"), 0, 0),
                    evidence,
                )
                continue
            result.append(item)
        return result[:6]

    def _merge_learned_expression_rules(
        self,
        profile: dict[str, Any],
        candidates: list[dict[str, Any]],
        *,
        batch_key: str,
        now: float,
        pending: bool = False,
    ) -> bool:
        if not isinstance(profile, dict) or not candidates:
            return False
        candidates = [item for item in candidates if self._expression_rule_definition_is_valid(item)]
        if not candidates:
            return False
        self._assign_expression_rule_families(candidates, batch_key=batch_key)
        storage_key = "pending_rules" if pending else "learned_rules"
        approved_changed = False
        if pending:
            approved_rules = [
                dict(item)
                for item in profile.get("learned_rules", [])
                if isinstance(item, dict)
            ]
            approved_by_id = {
                _single_line(item.get("id"), 40): item
                for item in approved_rules
                if _single_line(item.get("id"), 40)
            }
            pending_candidates: list[dict[str, Any]] = []
            for candidate in candidates:
                target = None
                requested_merge_id = _single_line(candidate.get("merge_into_id"), 40)
                requested_target = approved_by_id.get(requested_merge_id) if requested_merge_id else None
                if requested_target is not None and not requested_target.get("manually_edited"):
                    analysis = self._expression_rule_duplicate_analysis(requested_target, candidate)
                    if (
                        analysis.get("auto_merge")
                        or (
                            analysis.get("confidence", 0.0) >= 0.78
                            and self._expression_rule_contexts_compatible(requested_target, candidate)
                        )
                    ):
                        target = requested_target
                if target is None:
                    for approved in approved_rules:
                        analysis = self._expression_rule_duplicate_analysis(approved, candidate)
                        if analysis.get("auto_merge"):
                            target = approved
                            break
                if target is None:
                    pending_candidates.append(candidate)
                    continue
                incoming = dict(candidate)
                incoming["last_seen_ts"] = now
                incoming["last_batch_key"] = batch_key
                self._merge_expression_rule_duplicate_metadata(target, incoming)
                approved_changed = True
            if approved_changed:
                self._deduplicate_expression_rule_families(approved_rules)
                approved_rules.sort(
                    key=lambda item: (
                        -_safe_int(item.get("evidence_count"), 0, 0),
                        -_safe_float(item.get("last_seen_ts"), 0.0),
                    )
                )
                profile["learned_rules"] = approved_rules[: runtime_persona_setting(self, "max_learned_expression_items", 60)]
            candidates = pending_candidates
            if not candidates:
                return approved_changed
        existing = [dict(item) for item in profile.get(storage_key, []) if isinstance(item, dict)]
        families_changed = self._assign_expression_rule_families(existing)
        by_id = {_single_line(item.get("id"), 40): item for item in existing if _single_line(item.get("id"), 40)}

        def semantic_key(item: dict[str, Any]) -> str:
            kind = _single_line(item.get("kind"), 16).lower()
            situation = re.sub(
                r"[\s，。！？!?、；;：:‘’“”\"']",
                "",
                _single_line(item.get("situation"), 80).lower(),
            )
            pattern = re.sub(
                r"[\s，。！？!?、；;：:‘’“”\"']",
                "",
                _single_line(item.get("pattern") or item.get("style"), 100).lower(),
            )
            return f"{kind}|{situation}|{pattern}"

        by_semantic_key = {
            semantic_key(item): item
            for item in existing
            if semantic_key(item) != "||"
        }
        changed = bool(families_changed or approved_changed)
        for candidate in candidates:
            rule_id = _single_line(candidate.get("id"), 40)
            requested_merge_id = _single_line(candidate.get("merge_into_id"), 40)
            requested_target = by_id.get(requested_merge_id) if requested_merge_id else None
            if requested_target is not None:
                analysis = self._expression_rule_duplicate_analysis(requested_target, candidate)
                if not (
                    analysis.get("auto_merge")
                    or (
                        analysis.get("confidence", 0.0) >= 0.78
                        and self._expression_rule_contexts_compatible(requested_target, candidate)
                    )
                ):
                    requested_target = None
            old = requested_target or by_id.get(rule_id) or by_semantic_key.get(semantic_key(candidate))
            if old is None:
                old = dict(candidate)
                old.pop("merge_into_id", None)
                if pending:
                    old["review_status"] = "pending"
                old["created_ts"] = now
                old["last_seen_ts"] = now
                old["last_batch_key"] = batch_key
                existing.append(old)
                by_id[rule_id] = old
                by_semantic_key[semantic_key(old)] = old
                changed = True
                continue
            scope_before = dict(old)
            scope_before.pop("scope_binding", None)
            old["last_seen_ts"] = now
            incoming_family_key = _single_line(candidate.get("family_key"), 80).lower()
            if incoming_family_key and not _single_line(old.get("family_key"), 80):
                old["family_key"] = incoming_family_key
            old["keywords"] = list(dict.fromkeys([
                *[str(item) for item in old.get("keywords", []) if str(item).strip()],
                *[str(item) for item in candidate.get("keywords", []) if str(item).strip()],
            ]))[:8]
            old["tags"] = list(old["keywords"])
            old["evidence_examples"] = list(dict.fromkeys([
                *[str(item) for item in old.get("evidence_examples", []) if str(item).strip()],
                *[str(item) for item in candidate.get("evidence_examples", []) if str(item).strip()],
            ]))[:3]
            for field in ("channels", "relationship_stages", "emotion_gates"):
                old_values = old.get(field) if isinstance(old.get(field), list) else []
                candidate_values = candidate.get(field) if isinstance(candidate.get(field), list) else []
                old[field] = list(dict.fromkeys([
                    *[_single_line(item, 24).lower() for item in old_values if _single_line(item, 24)],
                    *[_single_line(item, 24).lower() for item in candidate_values if _single_line(item, 24)],
                ]))[:8]
            old_intent = self._normalize_expression_intent(old.get("intent"))
            candidate_intent = self._normalize_expression_intent(candidate.get("intent"))
            if old_intent == "any":
                old["intent"] = candidate_intent
            elif candidate_intent == "any" or old_intent == candidate_intent:
                old["intent"] = old_intent
            else:
                old["intent"] = "any"
            candidate_avoid = _single_line(candidate.get("avoid"), 160)
            if candidate_avoid and len(candidate_avoid) > len(_single_line(old.get("avoid"), 160)):
                old["avoid"] = candidate_avoid
            old["persona_conflict"] = bool(
                self._expression_rule_bool(old.get("persona_conflict"))
                or self._expression_rule_bool(candidate.get("persona_conflict"))
            )
            old["positive_feedback"] = _safe_int(old.get("positive_feedback"), 0, 0)
            old["negative_feedback"] = _safe_int(old.get("negative_feedback"), 0, 0)
            old["use_count"] = _safe_int(old.get("use_count"), 0, 0)
            if _single_line(old.get("last_batch_key"), 40) != batch_key:
                old["evidence_count"] = min(
                    99,
                    _safe_int(old.get("evidence_count"), 0, 0) + _safe_int(candidate.get("evidence_count"), 0, 0),
                )
                old["last_batch_key"] = batch_key
            else:
                old["evidence_count"] = max(
                    _safe_int(old.get("evidence_count"), 0, 0),
                    _safe_int(candidate.get("evidence_count"), 0, 0),
                )
            scope_binding = old.get("scope_binding") if isinstance(old.get("scope_binding"), dict) else None
            scope_after = dict(old)
            scope_after.pop("scope_binding", None)
            if scope_binding is not None and scope_before != scope_after:
                scope_binding["revision"] = max(1, _safe_int(scope_binding.get("revision"), 1, 1) + 1)
            changed = True
        if self._deduplicate_expression_rule_families(existing):
            changed = True
        if self._assign_expression_rule_families(existing, batch_key=batch_key):
            changed = True
        existing.sort(
            key=lambda item: (
                -_safe_int(item.get("evidence_count"), 0, 0),
                -_safe_float(item.get("last_seen_ts"), 0.0),
            )
        )
        profile[storage_key] = existing[: runtime_persona_setting(self, "max_learned_expression_items", 60)]
        return changed

    def _select_learned_expression_rules(
        self,
        rules: Any,
        *,
        hint: str = "",
        limit: int = 2,
        context: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if not isinstance(rules, list):
            return []
        self._assign_expression_rule_families(rules)
        query = _single_line(hint, 300).lower()
        context = context if isinstance(context, dict) else {}
        channel = _single_line(context.get("channel"), 24).lower()
        relationship_stage = _single_line(context.get("relationship_stage"), 24).lower()
        emotion_gate = _single_line(context.get("emotion_gate"), 24).lower()
        current_intent = self._normalize_expression_intent(context.get("intent"))
        now = _now_ts()
        ranked: list[tuple[float, dict[str, Any]]] = []
        for raw in rules:
            if not isinstance(raw, dict) or _safe_int(raw.get("evidence_count"), 0, 0) < 1:
                continue
            if not self._expression_rule_definition_is_valid(raw):
                continue
            review_status = _single_line(raw.get("review_status"), 24).lower()
            if review_status in {"pending", "needs_review", "rejected"}:
                continue
            if self._expression_rule_bool(raw.get("persona_conflict")):
                continue
            negative_feedback = _safe_int(raw.get("negative_feedback"), 0, 0)
            positive_feedback = _safe_int(raw.get("positive_feedback"), 0, 0)
            if negative_feedback >= 2 and negative_feedback > positive_feedback:
                continue

            context_score = 0
            channels = raw.get("channels") if isinstance(raw.get("channels"), list) else []
            normalized_channels = {_single_line(item, 24).lower() for item in channels if _single_line(item, 24)}
            if normalized_channels and channel and channel not in normalized_channels:
                continue
            if normalized_channels and channel in normalized_channels:
                context_score += 4

            relationship_stages = raw.get("relationship_stages") if isinstance(raw.get("relationship_stages"), list) else []
            normalized_relationships = {
                _single_line(item, 24).lower() for item in relationship_stages if _single_line(item, 24)
            }
            if normalized_relationships and "any" not in normalized_relationships:
                if relationship_stage and relationship_stage not in normalized_relationships:
                    continue
                if relationship_stage in normalized_relationships:
                    context_score += 3

            emotion_gates = raw.get("emotion_gates") if isinstance(raw.get("emotion_gates"), list) else []
            normalized_emotions = {_single_line(item, 24).lower() for item in emotion_gates if _single_line(item, 24)}
            if normalized_emotions and "any" not in normalized_emotions:
                if emotion_gate and emotion_gate not in normalized_emotions:
                    continue
                if emotion_gate in normalized_emotions:
                    context_score += 3

            rule_intent = self._normalize_expression_intent(raw.get("intent"))
            intent_equivalents = {
                "help": {"help", "request", "question"},
                "comfort": {"comfort", "emotion"},
                "play": {"play", "tease"},
                "tease": {"play", "tease"},
                "intimacy": {"intimacy", "emotion"},
                "boundary": {"boundary"},
                "acknowledgement": {"acknowledgement", "casual"},
                "question": {"question", "help"},
                "request": {"request", "help"},
                "emotion": {"emotion", "comfort", "intimacy"},
                "casual": {"casual", "acknowledgement"},
                "proactive": {"proactive", "casual"},
            }
            if rule_intent != "any" and current_intent != "any":
                if rule_intent not in intent_equivalents.get(current_intent, {current_intent}):
                    continue
                context_score += 5
            keywords = [
                _single_line(item, 24).lower()
                for item in raw.get("keywords", [])
                if _single_line(item, 24)
            ] if isinstance(raw.get("keywords"), list) else []
            matched = sum(1 for keyword in keywords if query and keyword in query)
            if query and keywords and matched <= 0 and context_score <= 0:
                continue
            last_seen_ts = _safe_float(raw.get("last_seen_ts"), 0.0)
            age_days = max(0.0, (now - last_seen_ts) / 86400) if last_seen_ts > 0 else 0.0
            freshness = max(0.01, 1.0 - age_days / 30.0)
            feedback_score = min(8, positive_feedback * 1.5) - min(16, negative_feedback * 4)
            score = (
                matched * 10
                + context_score
                + min(9, _safe_int(raw.get("evidence_count"), 0, 0)) * freshness
                + feedback_score
            )
            ranked.append((score, raw))
        ranked.sort(key=lambda pair: pair[0], reverse=True)
        grouped_ranked: dict[str, dict[str, Any]] = {}
        group_order: list[str] = []
        for score, item in ranked:
            family_id = _single_line(item.get("family_id"), 64)
            if not family_id:
                family_id = f"xs-{hashlib.sha1(_single_line(item.get('id'), 100).encode('utf-8')).hexdigest()[:16]}"
            if family_id not in grouped_ranked:
                grouped_ranked[family_id] = {"score": score, "items": []}
                group_order.append(family_id)
            grouped_ranked[family_id]["score"] = max(_safe_float(grouped_ranked[family_id].get("score"), score), score)
            grouped_ranked[family_id]["items"].append(item)

        ranked_groups: list[tuple[float, dict[str, Any]]] = []
        for family_id in group_order:
            entry = grouped_ranked[family_id]
            bundle = self._expression_rule_runtime_bundle(entry.get("items"))
            if not bundle:
                continue
            complement_bonus = min(1.5, max(0, _safe_int(bundle.get("component_count"), 1, 1) - 1) * 0.75)
            ranked_groups.append((_safe_float(entry.get("score"), 0.0) + complement_bonus, bundle))
        ranked_groups.sort(key=lambda pair: pair[0], reverse=True)

        limit = max(1, limit)
        selected: list[dict[str, Any]] = []
        selected_ids: set[str] = set()
        seen_kinds: set[str] = set()
        # 同源的表达与语法作为一个规则组占一个名额；独立规则仍优先覆盖两种能力。
        for _, bundle in ranked_groups:
            component_kinds = {
                _single_line(item, 16).lower()
                for item in bundle.get("component_kinds", [])
                if _single_line(item, 16).lower() in {"style", "grammar"}
            }
            if component_kinds and component_kinds.issubset(seen_kinds):
                continue
            selected.append(bundle)
            selected_ids.add(_single_line(bundle.get("family_id"), 64) or _single_line(bundle.get("id"), 100))
            seen_kinds.update(component_kinds)
            if len(selected) >= limit:
                return selected
        for _, bundle in ranked_groups:
            bundle_id = _single_line(bundle.get("family_id"), 64) or _single_line(bundle.get("id"), 100)
            if bundle_id in selected_ids:
                continue
            selected.append(bundle)
            if len(selected) >= limit:
                break
        return selected

