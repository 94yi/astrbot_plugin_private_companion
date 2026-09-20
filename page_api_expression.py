# -*- coding: utf-8 -*-
"""表达 / 表达库 域页面 API。

由 tools/split_mixin_domain.py 从 page_api.py 机械抽取（27 个方法 / 2388 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApi）。

"""
from __future__ import annotations

import json
import time
import re
import hmac
import hashlib
from copy import copy, deepcopy
from datetime import date, datetime, timedelta
from typing import Any, Mapping
from quart import request, send_file
from .helpers import _MISSING, _flat_get, _normalize_timezone_name, _normalize_timezone_setting, _path_text, _redact_outbound_secrets, _safe_int, _set_into_config, _strip_internal_message_blocks, _text_looks_garbled, _text_similarity, _today_key, normalize_bot_relationship_cards
from .expression_scope_ownership import (
    ExpressionScopeError,
    bind_expression_item,
    bind_expression_profile,
    validate_expression_scope_binding,
)
from .reaction_asset_library import get_reaction_asset_library
from .logging_util import get_module_logger

logger = get_module_logger(__name__)


class PrivateCompanionPageApiExpressionMixin:
    """表达 / 表达库 域（从 PrivateCompanionPageApi 拆出）。"""

    def _reaction_expression_runtime_summary(self, raw_data: Any) -> dict[str, Any]:
        """Aggregate experiment metrics without exposing user or image details."""
        raw_runtime = getattr(self.plugin, "_reaction_expression_runtime", None)
        runtime = raw_runtime if isinstance(raw_runtime, dict) else {}
        runtime_payload: dict[str, Any] = {}
        for key in (
            "attempts",
            "offers",
            "model_omissions",
            "local_fallbacks",
            "lookups",
            "cache_hits",
            "sent",
            "skipped",
        ):
            if key in runtime:
                runtime_payload[key] = self._int(runtime.get(key), 0, 0)
        trigger_modes = runtime.get("trigger_modes")
        if isinstance(trigger_modes, dict):
            runtime_payload["trigger_modes"] = {
                self._single_line(key, 40): self._int(value, 0, 0)
                for key, value in trigger_modes.items()
                if self._single_line(key, 40)
            }
        for key in ("last_latency_ms", "total_lookup_ms"):
            if key in runtime:
                runtime_payload[key] = round(self._float(runtime.get(key), 0.0, 0.0), 2)
        if "last_reason" in runtime:
            runtime_payload["last_reason"] = self._single_line(runtime.get("last_reason"), 120)

        users = raw_data.get("users") if isinstance(raw_data, dict) else {}
        if not isinstance(users, dict):
            users = {}
        tracked_user_count = 0
        recent_attempt_count = 0
        recent_sent_count = 0
        recent_skipped_count = 0
        positive_feedback_count = 0
        negative_feedback_count = 0
        last_activity_at = 0.0
        skip_reason_counts: dict[str, int] = {}
        for user in users.values():
            if not isinstance(user, dict):
                continue
            state = user.get("reaction_expression")
            if not isinstance(state, dict):
                continue
            tracked_user_count += 1
            outcomes = state.get("recent_outcomes")
            if isinstance(outcomes, list):
                for outcome in outcomes:
                    if not isinstance(outcome, dict):
                        continue
                    recent_attempt_count += 1
                    status = self._single_line(outcome.get("status"), 32).lower()
                    if status == "sent":
                        recent_sent_count += 1
                    elif status == "skipped":
                        recent_skipped_count += 1
                        reason = self._single_line(outcome.get("reason"), 120) or "unknown"
                        skip_reason_counts[reason] = skip_reason_counts.get(reason, 0) + 1
                    last_activity_at = max(
                        last_activity_at,
                        self._float(outcome.get("at"), 0.0, 0.0),
                    )
            preference = state.get("preference")
            if isinstance(preference, dict):
                positive_feedback_count += self._int(preference.get("positive_count"), 0, 0)
                negative_feedback_count += self._int(preference.get("negative_count"), 0, 0)

        ordered_reasons = sorted(
            skip_reason_counts.items(),
            key=lambda item: (-item[1], item[0]),
        )[:12]
        try:
            library = get_reaction_asset_library(self.plugin)
            library_summary = library.summary() if library is not None else {}
            embedding_provider_id = self._single_line(
                getattr(self.plugin, "reaction_expression_embedding_provider_id", "")
                or getattr(self.plugin, "_reaction_embedding_active_provider_id", ""),
                160,
            )
            embedding_summary = (
                library.embedding_status(embedding_provider_id)
                if library is not None and embedding_provider_id
                else {"provider_id": embedding_provider_id, "indexed": 0, "missing": 0, "total": 0}
            )
        except Exception:
            library_summary = {}
            embedding_summary = {}
        return {
            "enabled": bool(getattr(self.plugin, "enable_reaction_expression_experiment", False)),
            "library": library_summary,
            "embedding": {
                "enabled": bool(getattr(self.plugin, "reaction_expression_embedding_enabled", False)),
                **embedding_summary,
            },
            "runtime": runtime_payload,
            "recent": {
                "tracked_user_count": tracked_user_count,
                "attempt_count": recent_attempt_count,
                "sent_count": recent_sent_count,
                "skipped_count": recent_skipped_count,
                "skip_reasons": {reason: count for reason, count in ordered_reasons},
                "last_activity_at": last_activity_at,
                "positive_feedback_count": positive_feedback_count,
                "negative_feedback_count": negative_feedback_count,
                "partial": True,
            },
        }
    def _model_diagnostics_expression_duplicate_candidates(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        analyzer = getattr(self.plugin, "_expression_rule_duplicate_analysis", None)
        candidates: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str, str]] = set()

        def fallback_analysis(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
            if self._single_line(left.get("kind"), 16).lower() != self._single_line(right.get("kind"), 16).lower():
                return {}
            compact = lambda value: re.sub(
                r"[\s，。！？!?、；;：:‘’“”\"'~～…—–_-]",
                "",
                self._single_line(value, 120).lower(),
            )
            if compact(left.get("pattern") or left.get("style")) != compact(right.get("pattern") or right.get("style")):
                return {}
            return {
                "code": "same_pattern",
                "confidence": 0.9,
                "auto_merge": False,
                "reason": "同类规则使用相同表达模板",
            }

        def analyze(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
            if callable(analyzer):
                try:
                    result = analyzer(left, right)
                    if isinstance(result, dict):
                        return result
                except Exception:
                    pass
            return fallback_analysis(left, right)

        def inspect_source(
            *,
            source_type: str,
            source_id: str,
            source_name: str,
            profile: Any,
        ) -> None:
            if not isinstance(profile, dict):
                return
            rows: list[dict[str, Any]] = []
            for storage_key, storage_label in (("learned_rules", "已启用"), ("pending_rules", "待审核")):
                values = profile.get(storage_key) if isinstance(profile.get(storage_key), list) else []
                for raw in values:
                    if isinstance(raw, dict):
                        rows.append({**raw, "_storage_key": storage_key, "_storage_label": storage_label})
            duplicate_count = 0
            duplicate_families: set[str] = set()
            parents = list(range(len(rows)))
            edges: list[tuple[int, int, dict[str, Any]]] = []

            def find(index: int) -> int:
                while parents[index] != index:
                    parents[index] = parents[parents[index]]
                    index = parents[index]
                return index

            def union(left_index: int, right_index: int) -> None:
                left_root = find(left_index)
                right_root = find(right_index)
                if left_root != right_root:
                    parents[right_root] = left_root

            for index, left in enumerate(rows):
                for right_index in range(index + 1, len(rows)):
                    right = rows[right_index]
                    left_family = self._single_line(left.get("family_id"), 100)
                    right_family = self._single_line(right.get("family_id"), 100)
                    if left_family and left_family == right_family:
                        continue
                    analysis = analyze(left, right)
                    confidence = self._float(analysis.get("confidence"))
                    if confidence < 0.78:
                        continue
                    left_id = self._single_line(left.get("id"), 100)
                    right_id = self._single_line(right.get("id"), 100)
                    pair_key = (
                        source_type,
                        source_id,
                        min(left_id, right_id),
                        max(left_id, right_id),
                    )
                    if pair_key in seen:
                        continue
                    seen.add(pair_key)
                    union(index, right_index)
                    edges.append((index, right_index, analysis))

            component_members: dict[int, set[int]] = {}
            involved = {value for left_index, right_index, _ in edges for value in (left_index, right_index)}
            for index in involved:
                component_members.setdefault(find(index), set()).add(index)
            for members in component_members.values():
                component_edges = [
                    analysis
                    for left_index, right_index, analysis in edges
                    if left_index in members and right_index in members
                ]
                if not component_edges:
                    continue
                duplicate_count += 1
                member_rows = [rows[index] for index in sorted(members)]
                family_ids = list(dict.fromkeys(
                    value
                    for item in member_rows
                    if (value := self._single_line(item.get("family_id"), 100))
                ))
                duplicate_families.update(family_ids)
                patterns = list(dict.fromkeys(
                    value
                    for item in member_rows
                    if (value := self._single_line(item.get("pattern") or item.get("style"), 80))
                ))
                storage_text = " / ".join(dict.fromkeys(
                    self._single_line(item.get("_storage_label"), 20)
                    for item in member_rows
                    if self._single_line(item.get("_storage_label"), 20)
                ))
                auto_merge = all(bool(item.get("auto_merge")) for item in component_edges)
                action_text = "可保守合并证据与适用边界" if auto_merge else "应人工确认是否保留这些情境"
                reasons = list(dict.fromkeys(
                    self._single_line(item.get("reason"), 120)
                    for item in component_edges
                    if self._single_line(item.get("reason"), 120)
                ))
                rule_ids = list(dict.fromkeys(
                    value
                    for item in member_rows
                    if (value := self._single_line(item.get("id"), 100))
                ))
                confidence = max((self._float(item.get("confidence")) for item in component_edges), default=0.0)
                pattern_text = " / ".join(patterns[:4])
                if len(patterns) > 4:
                    pattern_text += f" 等 {len(patterns)} 种模板"
                if len(member_rows) > len(patterns):
                    pattern_text += f"（共 {len(member_rows)} 条规则）"
                candidates.append({
                    "category": "duplicate_rule",
                    "source_type": source_type,
                    "source_id": source_id,
                    "user_id": source_id,
                    "name": source_name,
                    "text": pattern_text,
                    "reason": f"{'；'.join(reasons[:2]) or '疑似近义规则'}；{action_text}",
                    "confidence": round(confidence, 3),
                    "storage": storage_text,
                    "rule_ids": rule_ids,
                    "family_ids": family_ids,
                    "auto_merge": auto_merge,
                })
                if len(candidates) >= 24:
                    return
            learned = profile.get("learned_rules") if isinstance(profile.get("learned_rules"), list) else []
            rule_limit = max(1, self._int(getattr(self.plugin, "max_learned_expression_items", 60)) or 60)
            if duplicate_count and len(learned) >= rule_limit:
                candidates.append({
                    "category": "rule_budget",
                    "source_type": source_type,
                    "source_id": source_id,
                    "user_id": source_id,
                    "name": source_name,
                    "text": f"已启用 {len(learned)}/{rule_limit} 条，重复候选涉及 {len(duplicate_families)} 个规则组",
                    "reason": "表达规则已占满来源预算，近义规则会挤掉其他有效表达",
                    "confidence": 0.98,
                    "auto_merge": False,
                })

        for collection_key, source_type in (("users", "private"), ("groups", "group")):
            collection = data.get(collection_key) if isinstance(data.get(collection_key), dict) else {}
            for source_id, owner in collection.items():
                if not isinstance(owner, dict):
                    continue
                name = self._single_line(
                    owner.get("nickname") or owner.get("name") or owner.get("group_name") or source_id,
                    40,
                )
                inspect_source(
                    source_type=source_type,
                    source_id=self._single_line(source_id, 80),
                    source_name=name,
                    profile=owner.get("expression_profile"),
                )
                if len(candidates) >= 24:
                    return candidates[:24]

        runtime = data.get("expression_voice_profile") if isinstance(data.get("expression_voice_profile"), dict) else {}
        runtime_rules = runtime.get("learned_rules") if isinstance(runtime.get("learned_rules"), list) else []
        runtime_limit = max(1, self._int(getattr(self.plugin, "max_learned_expression_items", 60)) or 60)
        runtime_parents = list(range(len(runtime_rules)))
        runtime_involved: set[int] = set()

        def runtime_find(index: int) -> int:
            while runtime_parents[index] != index:
                runtime_parents[index] = runtime_parents[runtime_parents[index]]
                index = runtime_parents[index]
            return index

        def runtime_union(left_index: int, right_index: int) -> None:
            left_root = runtime_find(left_index)
            right_root = runtime_find(right_index)
            if left_root != right_root:
                runtime_parents[right_root] = left_root

        for index, left in enumerate(runtime_rules):
            if not isinstance(left, dict):
                continue
            for right_index in range(index + 1, len(runtime_rules)):
                right = runtime_rules[right_index]
                if not isinstance(right, dict):
                    continue
                analysis = analyze(left, right)
                if self._float(analysis.get("confidence")) >= 0.9:
                    runtime_union(index, right_index)
                    runtime_involved.update((index, right_index))
        runtime_duplicates = len({runtime_find(index) for index in runtime_involved})
        if runtime_duplicates and len(runtime_rules) >= runtime_limit:
            candidates.append({
                "category": "runtime_budget",
                "source_type": "runtime",
                "source_id": "expression_voice_profile",
                "user_id": "expression_voice_profile",
                "name": "运行时表达池",
                "text": f"当前 {len(runtime_rules)}/{runtime_limit} 条，含 {runtime_duplicates} 组高置信重复候选",
                "reason": "运行时规则池已达上限，重复项正在占用召回槽位",
                "confidence": 0.99,
                "auto_merge": False,
            })
        return candidates[:24]
    def _model_diagnostics_expression_candidates(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        log_markers = ("Traceback", "Error code:", "Exception", "[INFO]", "[WARN]", "[ERRO]", "[Core]", "```", "commit ", "diff ")
        model_markers = ("<pc_tts", "</pc_tts>", "[[PCTTS:", "send_message_to_user", "assistant", "system prompt", "提示词")
        political_markers = (
            "习近平",
            "共产党",
            "中共",
            "六四",
            "天安门",
            "法轮功",
            "台独",
            "港独",
            "藏独",
            "疆独",
            "民主运动",
            "政治敏感",
        )

        def reason_for(text: str) -> str:
            lowered = text.lower()
            if any(marker.lower() in lowered for marker in log_markers):
                return "像日志/代码/报错内容，不该作为表达习惯"
            if any(marker.lower() in lowered for marker in model_markers):
                return "像模型输出格式或内部标签，不该作为表达习惯"
            if any(marker in text for marker in political_markers):
                return "含政治敏感内容，不适合进入表达学习"
            if text.count("…") + text.count("～") + text.count("~") >= 5:
                return "标点留白过多，容易污染表达节奏"
            if text.count("？") + text.count("?") + text.count("！") + text.count("!") >= 6:
                return "疑问/感叹标点过多，容易污染表达节奏"
            return ""

        candidates = self._model_diagnostics_expression_duplicate_candidates(data)
        seen_pollution: set[tuple[str, str, str]] = set()
        for collection_key, source_type in (("users", "private"), ("groups", "group")):
            collection = data.get(collection_key) if isinstance(data.get(collection_key), dict) else {}
            for source_id, owner in collection.items():
                if not isinstance(owner, dict):
                    continue
                name = self._single_line(
                    owner.get("nickname") or owner.get("name") or owner.get("group_name") or source_id,
                    40,
                )
                profile = owner.get("expression_profile") if isinstance(owner.get("expression_profile"), dict) else {}
                samples = [
                    *(profile.get("samples") if isinstance(profile.get("samples"), list) else []),
                    *(profile.get("pending_samples") if isinstance(profile.get("pending_samples"), list) else []),
                ]
                for raw in samples[:48]:
                    if not isinstance(raw, dict):
                        continue
                    text = self._single_line(raw.get("text") or raw.get("phrase") or raw.get("ending"), 120)
                    marks = raw.get("punctuation") if isinstance(raw.get("punctuation"), dict) else {}
                    pause_total = sum(self._int(marks.get(mark)) for mark in ("…", "～", "~"))
                    strong_total = sum(self._int(marks.get(mark)) for mark in ("？", "?", "！", "!"))
                    direct_reason = ""
                    if pause_total >= 5:
                        direct_reason = "标点留白过多，容易污染表达节奏"
                    elif strong_total >= 6:
                        direct_reason = "疑问/感叹标点过多，容易污染表达节奏"
                    if not text:
                        text = " ".join(
                            f"{mark}×{self._int(count)}"
                            for mark, count in marks.items()
                            if self._int(count) > 0
                        )
                    reason = direct_reason or reason_for(text)
                    key = (source_type, self._single_line(source_id, 80), f"{reason}|{text}")
                    if not reason or key in seen_pollution:
                        continue
                    seen_pollution.add(key)
                    candidates.append({
                        "category": "pollution",
                        "source_type": source_type,
                        "source_id": self._single_line(source_id, 80),
                        "user_id": self._single_line(source_id, 80),
                        "name": name,
                        "text": text,
                        "reason": reason,
                    })
                    if len(candidates) >= 32:
                        return candidates
                phrase_values = [
                    *(profile.get("recent_phrases") if isinstance(profile.get("recent_phrases"), list) else []),
                    *(profile.get("endings") if isinstance(profile.get("endings"), list) else []),
                ]
                for raw_text in phrase_values[:36]:
                    text = self._single_line(raw_text, 120)
                    reason = reason_for(text)
                    key = (source_type, self._single_line(source_id, 80), f"{reason}|{text}")
                    if not reason or key in seen_pollution:
                        continue
                    seen_pollution.add(key)
                    candidates.append({
                        "category": "pollution",
                        "source_type": source_type,
                        "source_id": self._single_line(source_id, 80),
                        "user_id": self._single_line(source_id, 80),
                        "name": name,
                        "text": text,
                        "reason": reason,
                    })
                    if len(candidates) >= 32:
                        return candidates
                for storage_key in ("learned_rules", "pending_rules"):
                    rules = profile.get(storage_key) if isinstance(profile.get(storage_key), list) else []
                    for raw in rules[:60]:
                        if not isinstance(raw, dict):
                            continue
                        text = "｜".join(filter(None, (
                            self._single_line(raw.get("situation"), 80),
                            self._single_line(raw.get("pattern") or raw.get("style"), 100),
                            self._single_line(raw.get("instruction"), 120),
                        )))
                        reason = reason_for(text)
                        key = (source_type, self._single_line(source_id, 80), f"{reason}|{text}")
                        if not reason or key in seen_pollution:
                            continue
                        seen_pollution.add(key)
                        candidates.append({
                            "category": "pollution",
                            "source_type": source_type,
                            "source_id": self._single_line(source_id, 80),
                            "user_id": self._single_line(source_id, 80),
                            "name": name,
                            "text": text,
                            "reason": reason,
                        })
                        if len(candidates) >= 32:
                            return candidates
        return candidates
    def _expression_learning_scope_summary(self, data: dict[str, Any]) -> dict[str, Any]:
        scope_ids = getattr(self.plugin, "_expression_scope_ids", None)

        def ids(key: str, *, group: bool = False) -> list[str]:
            if callable(scope_ids):
                try:
                    return sorted(scope_ids(key, group=group))
                except Exception:
                    pass
            raw = getattr(self.plugin, key, [])
            return sorted(self._normalize_id_list(raw))

        voice = data.get("expression_voice_profile") if isinstance(data.get("expression_voice_profile"), dict) else {}
        actions = voice.get("actions") if isinstance(voice.get("actions"), list) else []
        runtime = data.get("expression_learning_runtime") if isinstance(data.get("expression_learning_runtime"), dict) else {}
        by_day = runtime.get("group_batches_by_day") if isinstance(runtime.get("group_batches_by_day"), dict) else {}
        group_daily_limit = self._int(getattr(self.plugin, "expression_group_learning_daily_batch_limit", 6)) or 6
        group_used_today = self._int(by_day.get(_today_key()))
        return {
            "enabled": bool(getattr(self.plugin, "enable_expression_learning", False)),
            "private_learning": {
                "mode": self._single_line(getattr(self.plugin, "expression_private_learning_source_mode", "owner"), 20),
                "ids": ids("expression_private_learning_source_ids"),
            },
            "group_learning": {
                "mode": self._single_line(getattr(self.plugin, "expression_group_learning_source_mode", "disabled"), 20),
                "ids": ids("expression_group_learning_source_ids", group=True),
            },
            "private_application": {
                "mode": self._single_line(getattr(self.plugin, "expression_private_application_mode", "all"), 20),
                "ids": ids("expression_private_application_user_ids"),
            },
            "group_application": {
                "mode": self._single_line(getattr(self.plugin, "expression_group_application_mode", "all"), 20),
                "ids": ids("expression_group_application_ids", group=True),
            },
            "group_budget": {
                "daily_limit": group_daily_limit,
                "used_today": group_used_today,
                "remaining_today": max(0, group_daily_limit - group_used_today),
                "min_new_messages": self._int(
                    getattr(self.plugin, "expression_group_learning_min_new_messages", 20)
                ) or 20,
                "last_batch_at": self._single_line(runtime.get("last_group_batch_at"), 30),
                "last_defer_reason": self._single_line(runtime.get("last_group_defer_reason"), 40),
            },
            "voice": {
                "sample_count": self._int(voice.get("sample_count")),
                "private_source_count": self._int(voice.get("private_source_count")),
                "group_source_count": self._int(voice.get("group_source_count")),
                "actions": [self._single_line(item, 120) for item in actions[:4] if self._single_line(item, 120)],
                "updated_at": self._single_line(voice.get("updated_at"), 30),
            },
        }
    def _expression_rule_group_rows(self, rules: Any) -> list[dict[str, Any]]:
        rows = [dict(item) for item in rules if isinstance(item, dict)] if isinstance(rules, list) else []
        if not rows:
            return []
        grouper = getattr(self.plugin, "_expression_rule_groups", None)
        bundler = getattr(self.plugin, "_expression_rule_runtime_bundle", None)
        raw_groups = grouper(rows) if callable(grouper) else [[item] for item in rows]
        result: list[dict[str, Any]] = []
        for raw_group in raw_groups:
            if not isinstance(raw_group, list) or not raw_group:
                continue
            bundle = bundler(raw_group) if callable(bundler) else dict(raw_group[0])
            if not isinstance(bundle, dict) or not bundle:
                continue
            items = [dict(item) for item in raw_group if isinstance(item, dict)]
            style_rule = next((item for item in items if item.get("kind") == "style"), None)
            grammar_rule = next((item for item in items if item.get("kind") == "grammar"), None)
            review_statuses = {self._single_line(item.get("review_status"), 24).lower() for item in items}
            review_status = "needs_review" if "needs_review" in review_statuses else (
                "pending" if "pending" in review_statuses else "approved"
            )
            group_row = {
                **bundle,
                "id": self._single_line(bundle.get("family_id") or bundle.get("id"), 100),
                "family_id": self._single_line(bundle.get("family_id"), 100),
                "label": self._single_line(
                    (style_rule or {}).get("label") or (grammar_rule or {}).get("label") or bundle.get("label"),
                    100,
                ),
                "situation": self._single_line(
                    (style_rule or {}).get("situation") or (grammar_rule or {}).get("situation") or bundle.get("situation"),
                    100,
                ),
                "kind": "combined" if style_rule and grammar_rule else self._single_line(bundle.get("kind"), 24),
                "kind_label": "组合规则" if style_rule and grammar_rule else (
                    "情境表达" if style_rule else "语法习惯"
                ),
                "component_count": len(items),
                "component_kinds": [kind for kind in ("style", "grammar") if any(item.get("kind") == kind for item in items)],
                "items": items,
                "component_rules": items,
                "style_rule": dict(style_rule) if style_rule else None,
                "grammar_rule": dict(grammar_rule) if grammar_rule else None,
                "review_status": review_status,
                "review_reason": self._single_line(
                    next((item.get("review_reason") for item in items if item.get("review_reason")), ""),
                    180,
                ),
                "evidence_count": max(self._int(item.get("evidence_count")) for item in items),
                "item_revisions": {
                    self._single_line(item.get("id"), 100): self._int(item.get("item_revision"))
                    for item in items if self._single_line(item.get("id"), 100)
                },
            }
            result.append(group_row)
        result.sort(key=lambda item: (-self._int(item.get("evidence_count")), self._single_line(item.get("situation"), 100)))
        return result
    def _expression_profile_summary(self, user: dict[str, Any], *, source_type: str = "private") -> dict[str, Any]:
        profile = user.get("expression_profile") if isinstance(user.get("expression_profile"), dict) else {}
        scene_label = getattr(self.plugin, "_expression_scene_label", None)
        feature_labels = {
            "short": "短句",
            "casual_opener": "随口开头",
            "playful": "轻松感",
            "laugh_marker": "笑声口语",
            "reduplication": "自然叠词",
            "soft_wave": "波浪收束",
            "soft_ending": "柔和收尾",
            "pause": "留白停顿",
            "question": "问句推进",
        }

        def sample_row(item: Any, index: int) -> dict[str, Any]:
            raw = item if isinstance(item, dict) else {}
            text = self._single_line(raw.get("text") or raw.get("phrase") or raw.get("ending"), 120)
            punctuation = raw.get("punctuation") if isinstance(raw.get("punctuation"), dict) else {}
            marks = "".join(f"{key}{value}" for key, value in punctuation.items() if self._int(value) > 0)
            scene = self._single_line(
                scene_label(raw.get("scene")) if callable(scene_label) else raw.get("scene"),
                32,
            )
            feature_values = [
                self._single_line(feature_labels.get(str(feature), str(feature)), 24)
                for feature in raw.get("features", [])
                if self._single_line(feature, 24)
            ] if isinstance(raw.get("features"), list) else []
            distinctive_features = [item for item in feature_values if item not in {"短句", "问句推进"}]
            pattern_label = scene
            if source_type == "group":
                if distinctive_features:
                    pattern_label = f"{scene or '日常交流'}中的{'与'.join(distinctive_features[:2])}"
                elif scene:
                    pattern_label = f"{scene}表达模式"
                pattern_details = []
                length_bucket = self._single_line(raw.get("length_bucket"), 20)
                if length_bucket:
                    pattern_details.append(f"{length_bucket} 字")
                mark_types = "".join(
                    str(mark)
                    for mark, count in punctuation.items()
                    if self._int(count) > 0
                )
                if mark_types:
                    pattern_details.append(f"含 {mark_types}")
                if pattern_details:
                    pattern_label = f"{pattern_label or '日常交流'} · {' · '.join(pattern_details)}"
            observation_status = "supported" if self._int(raw.get("evidence_count")) >= 2 else "single"
            scope_binding = raw.get("scope_binding") if isinstance(raw.get("scope_binding"), dict) else {}
            return {
                "id": self._single_line(raw.get("id"), 40) or str(index),
                "index": index,
                "text": text,
                "phrase": self._single_line(raw.get("phrase"), 80),
                "ending": self._single_line(raw.get("ending"), 20),
                "scene": scene,
                "features": feature_values,
                "length": self._int(raw.get("length")),
                "length_bucket": self._single_line(raw.get("length_bucket"), 20),
                "punctuation": marks,
                "evidence_count": max(1, self._int(raw.get("evidence_count"))),
                "pattern_status": observation_status,
                "observation_status": observation_status,
                "pattern_label": pattern_label,
                "created_at": self._single_line(raw.get("created_at"), 30),
                "ts": self._float(raw.get("ts")),
                "time": self.plugin._format_timestamp_elapsed(raw.get("ts", 0)),
                "item_revision": self._int(scope_binding.get("revision")),
            }

        samples = profile.get("samples") if isinstance(profile.get("samples"), list) else []
        pending = profile.get("pending_samples") if isinstance(profile.get("pending_samples"), list) else []
        formatter = getattr(self.plugin, "_format_expression_profile_for_prompt", None)
        try:
            prompt_preview = formatter(user) if callable(formatter) else ""
        except Exception:
            prompt_preview = ""
        rules: list[dict[str, Any]] = []
        def semantic_rule_row(raw_rule: dict[str, Any], *, pending_review: bool) -> dict[str, Any] | None:
            if not isinstance(raw_rule, dict):
                return None
            validator = getattr(self.plugin, "_expression_rule_definition_is_valid", None)
            if callable(validator) and not validator(raw_rule):
                return None
            evidence_count = self._int(raw_rule.get("evidence_count"))
            if evidence_count < 1:
                return None
            situation = self._single_line(raw_rule.get("situation"), 100)
            instruction = self._single_line(raw_rule.get("instruction"), 300)
            pattern = self._single_line(raw_rule.get("pattern"), 180)
            kind = self._single_line(raw_rule.get("kind"), 24).lower()
            if kind not in {"style", "grammar"} or not situation or not pattern or not instruction:
                return None
            review_status = self._single_line(raw_rule.get("review_status"), 24).lower()
            if not review_status:
                review_status = "pending" if pending_review else "approved"
            scope_binding = raw_rule.get("scope_binding") if isinstance(raw_rule.get("scope_binding"), dict) else {}
            return {
                "id": self._single_line(raw_rule.get("id"), 100),
                "family_id": self._single_line(raw_rule.get("family_id"), 100),
                "family_key": self._single_line(raw_rule.get("family_key"), 80),
                "scene": self._single_line(raw_rule.get("kind"), 24),
                "label": self._single_line(raw_rule.get("label"), 100) or situation or "语义表达规则",
                "situation": situation,
                "pattern": pattern,
                "instruction": instruction,
                "evidence_count": evidence_count,
                "confidence": min(0.98, round(0.52 + min(8, evidence_count) * 0.055, 2)),
                "signals": [
                    self._single_line(item, 24)
                    for item in (raw_rule.get("keywords") or raw_rule.get("tags") or [])
                    if self._single_line(item, 24)
                ] if isinstance(raw_rule.get("keywords") or raw_rule.get("tags"), list) else [],
                "rule_type": "semantic",
                "kind": kind,
                "kind_label": "情境表达" if kind == "style" else "语法习惯",
                "evidence_examples": [
                    self._single_line(item, 80)
                    for item in raw_rule.get("evidence_examples", [])
                    if self._single_line(item, 80)
                ][:3] if isinstance(raw_rule.get("evidence_examples"), list) else [],
                "pattern_status": review_status if pending_review else "active",
                "review_status": review_status,
                "review_reason": self._single_line(raw_rule.get("review_reason"), 180),
                "channels": [
                    self._single_line(item, 24).lower()
                    for item in raw_rule.get("channels", [])
                    if self._single_line(item, 24)
                ] if isinstance(raw_rule.get("channels"), list) else [],
                "relationship_stages": [
                    self._single_line(item, 24).lower()
                    for item in raw_rule.get("relationship_stages", [])
                    if self._single_line(item, 24)
                ] if isinstance(raw_rule.get("relationship_stages"), list) else [],
                "emotion_gates": [
                    self._single_line(item, 24).lower()
                    for item in raw_rule.get("emotion_gates", [])
                    if self._single_line(item, 24)
                ] if isinstance(raw_rule.get("emotion_gates"), list) else [],
                "intent": self._single_line(raw_rule.get("intent"), 32).lower() or "any",
                "avoid": self._single_line(raw_rule.get("avoid"), 220),
                "persona_conflict": raw_rule.get("persona_conflict") is True
                or self._single_line(raw_rule.get("persona_conflict"), 12).lower() in {"1", "true", "yes", "on", "是", "冲突"},
                "positive_feedback": self._int(raw_rule.get("positive_feedback")),
                "negative_feedback": self._int(raw_rule.get("negative_feedback")),
                "use_count": self._int(raw_rule.get("use_count")),
                "last_used_time": self.plugin._format_timestamp_elapsed(raw_rule.get("last_used_ts", 0))
                if self._float(raw_rule.get("last_used_ts")) > 0 else "",
                "item_revision": self._int(scope_binding.get("revision")),
            }

        learned_rules = profile.get("learned_rules") if isinstance(profile.get("learned_rules"), list) else []
        for raw_rule in learned_rules:
            row = semantic_rule_row(raw_rule, pending_review=False)
            if row:
                rules.append(row)
        pending_rules = profile.get("pending_rules") if isinstance(profile.get("pending_rules"), list) else []
        pending_rule_rows = [
            row
            for raw_rule in pending_rules
            if (row := semantic_rule_row(raw_rule, pending_review=True)) is not None
        ]
        rules.sort(key=lambda item: (-self._int(item.get("evidence_count")), self._single_line(item.get("scene"), 32)))
        rule_groups = self._expression_rule_group_rows(rules)
        pending_rule_groups = self._expression_rule_group_rows(pending_rule_rows)
        raw_usage = profile.get("usage") if isinstance(profile.get("usage"), dict) else {}
        raw_last_injection = raw_usage.get("last_injection") if isinstance(raw_usage.get("last_injection"), dict) else {}
        usage = {
            "injected_count": self._int(raw_usage.get("injected_count")),
            "visible_match_count": self._int(raw_usage.get("visible_match_count")),
            "semantic_injected_count": self._int(raw_usage.get("semantic_injected_count")),
            "feedback_positive": self._int(raw_usage.get("feedback_positive")),
            "feedback_negative": self._int(raw_usage.get("feedback_negative")),
            "last_injection": {
                "time": self.plugin._format_timestamp_elapsed(raw_last_injection.get("ts", 0)) if raw_last_injection else "",
                "at": self._single_line(raw_last_injection.get("at"), 30),
                "rule_id": self._single_line(raw_last_injection.get("rule_id"), 100),
                "scene": self._single_line(raw_last_injection.get("scene"), 32),
                "label": self._single_line(raw_last_injection.get("label"), 32),
                "instruction": self._single_line(raw_last_injection.get("instruction"), 300),
                "evidence_count": self._int(raw_last_injection.get("evidence_count")),
                "confidence": max(0.0, min(1.0, self._float(raw_last_injection.get("confidence")))),
                "expected_signals": [
                    self._single_line(feature_labels.get(str(signal), str(signal)), 24)
                    for signal in raw_last_injection.get("expected_signals", [])
                    if self._single_line(signal, 24)
                ] if isinstance(raw_last_injection.get("expected_signals"), list) else [],
                "visible_signals": [
                    self._single_line(feature_labels.get(str(signal), str(signal)), 24)
                    for signal in raw_last_injection.get("visible_signals", [])
                    if self._single_line(signal, 24)
                ] if isinstance(raw_last_injection.get("visible_signals"), list) else [],
                "rule_type": self._single_line(raw_last_injection.get("rule_type"), 24),
                "semantic_rule_count": self._int(raw_last_injection.get("semantic_rule_count")),
                "channel": self._single_line(raw_last_injection.get("channel"), 24),
                "relationship_stage": self._single_line(raw_last_injection.get("relationship_stage"), 24),
                "emotion_gate": self._single_line(raw_last_injection.get("emotion_gate"), 24),
                "intent": self._single_line(raw_last_injection.get("intent"), 32),
            } if raw_last_injection else {},
        }
        raw_scene_profiles = profile.get("scene_profiles") if isinstance(profile.get("scene_profiles"), dict) else {}
        scene_profiles = []
        for scene, item in raw_scene_profiles.items():
            if not isinstance(item, dict):
                continue
            count = self._int(item.get("count"))
            if count <= 0:
                continue
            label = scene_label(scene) if callable(scene_label) else self._single_line(scene, 32)
            scene_profiles.append(
                {
                    "scene": self._single_line(scene, 32),
                    "label": self._single_line(label, 32),
                    "count": count,
                    "short_ratio": float(item.get("short_ratio") or 0),
                    "feature_counts": item.get("feature_counts") if isinstance(item.get("feature_counts"), dict) else {},
                }
            )
        scene_profiles.sort(key=lambda item: (-self._int(item.get("count")), self._single_line(item.get("scene"), 32)))
        sample_limit = max(
            4,
            min(60, self._int(getattr(self.plugin, "max_learned_expression_items", 60)) or 60),
        )
        return {
            "enabled": bool(getattr(self.plugin, "enable_expression_learning", False)),
            "mode": self._single_line(getattr(self.plugin, "expression_learning_mode", "balanced"), 20),
            "manual_review": bool(getattr(self.plugin, "enable_expression_manual_review", False)),
            "style_review": bool(getattr(self.plugin, "enable_expression_style_review", True)),
            "updated_at": self._single_line(profile.get("updated_at"), 30),
            "scope_revision": self._int(profile.get("scope_revision")),
            "sample_count": len(samples),
            "observation_count": len(samples),
            "observation_evidence_count": sum(max(1, self._int(item.get("evidence_count"))) for item in samples if isinstance(item, dict)),
            "pattern_count": len(rules),
            "rule_count": len(rules),
            "rule_evidence_count": sum(self._int(item.get("evidence_count")) for item in rule_groups),
            "style_rule_count": sum(1 for item in rules if item.get("kind") == "style"),
            "grammar_rule_count": sum(1 for item in rules if item.get("kind") == "grammar"),
            "rule_group_count": len(rule_groups),
            "pending_count": len(pending),
            "pending_rule_count": len(pending_rule_rows),
            "pending_rule_group_count": len(pending_rule_groups),
            "pending_style_count": sum(1 for item in pending_rule_rows if item.get("kind") == "style"),
            "pending_grammar_count": sum(1 for item in pending_rule_rows if item.get("kind") == "grammar"),
            "short_count": self._int(profile.get("short_count")),
            "endings": [self._single_line(item, 20) for item in (profile.get("endings") if isinstance(profile.get("endings"), list) else [])[:8]],
            "recent_phrases": [self._single_line(item, 80) for item in (profile.get("recent_phrases") if isinstance(profile.get("recent_phrases"), list) else [])[:8]],
            "scene_profiles": scene_profiles[:6],
            "rules": rules[: max(6, min(60, sample_limit * 2))],
            "pending_rules": pending_rule_rows[: max(6, min(60, sample_limit * 2))],
            "rule_groups": rule_groups[: max(6, min(60, sample_limit * 2))],
            "pending_rule_groups": pending_rule_groups[: max(6, min(60, sample_limit * 2))],
            "usage": usage,
            "samples": [sample_row(item, idx) for idx, item in enumerate(samples[:sample_limit])],
            "pending_samples": [sample_row(item, idx) for idx, item in enumerate(pending[:24])],
            "prompt_preview": self._multi_line(prompt_preview, 500),
        }
    def _expression_library_summary(self, data: dict[str, Any]) -> dict[str, Any]:
        samples: list[dict[str, Any]] = []
        pending_samples: list[dict[str, Any]] = []
        pending_rules: list[dict[str, Any]] = []
        rules: list[dict[str, Any]] = []
        pending_rule_groups: list[dict[str, Any]] = []
        rule_groups: list[dict[str, Any]] = []
        sources: list[dict[str, Any]] = []
        scene_totals: dict[str, dict[str, Any]] = {}
        sample_count = 0
        observation_evidence_count = 0
        rule_evidence_count = 0
        pending_count = 0
        injected_count = 0
        positive_feedback_count = 0
        negative_feedback_count = 0
        style_rule_count = 0
        grammar_rule_count = 0
        pending_style_count = 0
        pending_grammar_count = 0

        def collect(source_type: str, source_id: str, item: dict[str, Any]) -> None:
            nonlocal sample_count, observation_evidence_count, rule_evidence_count, pending_count
            nonlocal injected_count, positive_feedback_count, negative_feedback_count
            nonlocal style_rule_count, grammar_rule_count, pending_style_count, pending_grammar_count
            summary = self._expression_profile_summary(item, source_type=source_type)
            if source_type == "persona":
                source_name = "当前人格全局规则"
                active = True
                source_kind_label = "人格全局"
            elif source_type == "group":
                source_name = self._single_line(
                    item.get("name") or item.get("group_name") or item.get("display_name"),
                    80,
                ) or "未命名群聊"
                active = bool(self.plugin._expression_group_learning_source_enabled(source_id))
                source_kind_label = "群聊"
            else:
                source_name = self._single_line(
                    item.get("display_name") or item.get("nickname") or item.get("name"),
                    80,
                ) or source_id
                active = bool(self.plugin._expression_private_learning_source_enabled(item, source_id))
                source_kind_label = "私聊"
            source = {
                "source_type": source_type,
                "source_kind_label": source_kind_label,
                "source_id": source_id,
                "source_name": source_name,
                "source_active": active,
                "scope_revision": self._int(summary.get("scope_revision")),
            }
            source_sample_count = self._int(summary.get("sample_count"))
            source_pending_sample_count = self._int(summary.get("pending_count"))
            source_pending_rule_count = self._int(summary.get("pending_rule_count"))
            source_pending_count = source_pending_sample_count + source_pending_rule_count
            source_rules = summary.get("rules") if isinstance(summary.get("rules"), list) else []
            source_pending_rules = summary.get("pending_rules") if isinstance(summary.get("pending_rules"), list) else []
            source_rule_groups = summary.get("rule_groups") if isinstance(summary.get("rule_groups"), list) else []
            source_pending_rule_groups = summary.get("pending_rule_groups") if isinstance(summary.get("pending_rule_groups"), list) else []
            if source_sample_count <= 0 and source_pending_count <= 0 and not source_rules and not source_pending_rules:
                return
            sources.append(
                {
                    **source,
                    "sample_count": source_sample_count,
                    "observation_count": source_sample_count,
                    "pending_count": source_pending_count,
                    "pending_rule_count": source_pending_rule_count,
                    "rule_count": len(source_rules),
                    "rule_group_count": len(source_rule_groups),
                    "pending_rule_group_count": len(source_pending_rule_groups),
                    "style_rule_count": self._int(summary.get("style_rule_count")),
                    "grammar_rule_count": self._int(summary.get("grammar_rule_count")),
                }
            )
            sample_count += source_sample_count
            observation_evidence_count += self._int(summary.get("observation_evidence_count"))
            rule_evidence_count += self._int(summary.get("rule_evidence_count"))
            pending_count += source_pending_count
            style_rule_count += self._int(summary.get("style_rule_count"))
            grammar_rule_count += self._int(summary.get("grammar_rule_count"))
            pending_style_count += self._int(summary.get("pending_style_count"))
            pending_grammar_count += self._int(summary.get("pending_grammar_count"))
            injected_count += self._int((summary.get("usage") or {}).get("injected_count"))
            for row in summary.get("samples") or []:
                if isinstance(row, dict):
                    samples.append({**row, **source})
            for row in summary.get("pending_samples") or []:
                if isinstance(row, dict):
                    pending_samples.append({**row, **source})
            for row in source_pending_rules:
                if isinstance(row, dict):
                    pending_rules.append({**row, **source})
            for row in source_pending_rule_groups:
                if isinstance(row, dict):
                    pending_rule_groups.append({**row, **source})
            for row in source_rules:
                if isinstance(row, dict):
                    rules.append({**row, **source})
                    if row.get("rule_type") == "semantic":
                        positive_feedback_count += self._int(row.get("positive_feedback"))
                        negative_feedback_count += self._int(row.get("negative_feedback"))
            for row in source_rule_groups:
                if isinstance(row, dict):
                    rule_groups.append({**row, **source})
            for scene in summary.get("scene_profiles") or []:
                if not isinstance(scene, dict):
                    continue
                key = self._single_line(scene.get("scene") or scene.get("label"), 32)
                if not key:
                    continue
                bucket = scene_totals.setdefault(
                    key,
                    {
                        "scene": key,
                        "label": self._single_line(scene.get("label") or key, 32),
                        "count": 0,
                    },
                )
                bucket["count"] += self._int(scene.get("count"))

        users = data.get("users") if isinstance(data.get("users"), dict) else {}
        for user_id, user in users.items():
            if isinstance(user, dict):
                collect("private", self._single_line(user_id, 80), user)
        groups = data.get("groups") if isinstance(data.get("groups"), dict) else {}
        for group_id, group in groups.items():
            if isinstance(group, dict):
                collect("group", self._single_line(group_id, 80), group)
        global_profile = data.get("_req041_persona_expression_profile")
        if isinstance(global_profile, dict):
            collect(
                "persona", "current-persona",
                {"expression_profile": global_profile, "display_name": "当前人格全局规则"},
            )

        samples.sort(key=lambda row: (-self._float(row.get("ts")), row.get("source_type") or "", row.get("source_id") or ""))
        pending_samples.sort(key=lambda row: (-self._float(row.get("ts")), row.get("source_type") or "", row.get("source_id") or ""))
        pending_rules.sort(key=lambda row: (-self._int(row.get("evidence_count")), row.get("source_type") or "", row.get("source_id") or ""))
        rules.sort(key=lambda row: (-self._int(row.get("evidence_count")), row.get("source_type") or "", row.get("source_id") or ""))
        pending_rule_groups.sort(key=lambda row: (-self._int(row.get("evidence_count")), row.get("source_type") or "", row.get("source_id") or ""))
        rule_groups.sort(key=lambda row: (-self._int(row.get("evidence_count")), row.get("source_type") or "", row.get("source_id") or ""))
        sources.sort(key=lambda row: (not bool(row.get("source_active")), row.get("source_type") or "", row.get("source_name") or ""))
        scene_profiles = sorted(
            scene_totals.values(),
            key=lambda row: (-self._int(row.get("count")), row.get("label") or ""),
        )
        return {
            "enabled": bool(getattr(self.plugin, "enable_expression_learning", False)),
            "mode": self._single_line(getattr(self.plugin, "expression_learning_mode", "balanced"), 20),
            "manual_review": bool(getattr(self.plugin, "enable_expression_manual_review", False)),
            "style_review": bool(getattr(self.plugin, "enable_expression_style_review", True)),
            "sample_count": sample_count,
            "observation_count": sample_count,
            "observation_evidence_count": observation_evidence_count,
            "pattern_count": len(rules),
            "rule_count": len(rules),
            "rule_group_count": len(rule_groups),
            "style_rule_count": style_rule_count,
            "grammar_rule_count": grammar_rule_count,
            "rule_evidence_count": rule_evidence_count,
            "evidence_count": rule_evidence_count,
            "pending_count": pending_count,
            "source_count": len(sources),
            "private_source_count": sum(1 for source in sources if source.get("source_type") == "private"),
            "group_source_count": sum(1 for source in sources if source.get("source_type") == "group"),
            "persona_source_count": sum(1 for source in sources if source.get("source_type") == "persona"),
            "active_source_count": sum(1 for source in sources if source.get("source_active")),
            "samples": samples,
            "pending_samples": pending_samples,
            "pending_rules": pending_rules,
            "pending_rule_count": len(pending_rules),
            "pending_rule_groups": pending_rule_groups,
            "pending_rule_group_count": len(pending_rule_groups),
            "pending_style_count": pending_style_count,
            "pending_grammar_count": pending_grammar_count,
            "rules": rules,
            "rule_groups": rule_groups,
            "scene_profiles": scene_profiles[:8],
            "sources": sources,
            "usage": {
                "injected_count": injected_count,
                "feedback_positive": positive_feedback_count,
                "feedback_negative": negative_feedback_count,
            },
        }
    @staticmethod
    def _expression_share_value_list(value: Any, *, limit: int = 8) -> list[str]:
        if not isinstance(value, list):
            return []
        result: list[str] = []
        for raw in value:
            item = re.sub(r"\s+", " ", str(raw or "")).strip()[:32]
            if item and item not in result:
                result.append(item)
            if len(result) >= limit:
                break
        return result
    def _expression_share_rule(self, raw: Any) -> tuple[dict[str, Any] | None, str]:
        if not isinstance(raw, dict):
            return None, "规则格式无效"
        rule = {
            "kind": self._single_line(raw.get("kind") or raw.get("type"), 16).lower(),
            "label": self._single_line(raw.get("label"), 100),
            "situation": self._single_line(raw.get("situation"), 100),
            "pattern": self._single_line(raw.get("pattern") or raw.get("style"), 100),
            "instruction": self._single_line(raw.get("instruction"), 160),
            "keywords": self._expression_share_value_list(raw.get("keywords") or raw.get("tags")),
            "signals": self._expression_share_value_list(raw.get("signals")),
            "channels": self._expression_share_value_list(raw.get("channels")),
            "relationship_stages": self._expression_share_value_list(raw.get("relationship_stages")),
            "emotion_gates": self._expression_share_value_list(raw.get("emotion_gates")),
            "intent": self._single_line(raw.get("intent"), 32).lower() or "any",
            "avoid": self._single_line(raw.get("avoid"), 160),
            "persona_conflict": bool(raw.get("persona_conflict", False)),
        }
        validator = getattr(self.plugin, "_expression_rule_definition_is_valid", None)
        if callable(validator) and not validator(rule):
            return None, "规则不是有效的可复用表达或具体语法"
        serialized = json.dumps(rule, ensure_ascii=False).lower()
        unsafe_markers = (
            "ignore previous", "ignore all previous", "system prompt", "developer message",
            "忽略之前", "忽略以上", "无视之前", "系统提示词", "开发者消息",
            "调用工具", "tool_calls", "<system", "</system",
        )
        if any(marker in serialized for marker in unsafe_markers):
            return None, "规则含有指令污染内容"
        return rule, ""
    def _expression_share_target(
        self,
        source_type: Any,
        source_id: Any,
        *,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        target_type = self._single_line(source_type, 16).lower()
        target_id = self._single_line(source_id, 80)
        root = data if isinstance(data, dict) else self.plugin.data
        collection = root.get("groups" if target_type == "group" else "users")
        if target_type not in {"private", "group"} or not target_id or not isinstance(collection, dict):
            return None
        target = collection.get(target_id)
        return target if isinstance(target, dict) else None
    def _normalize_expression_share_pack(self, raw_pack: Any) -> dict[str, Any]:
        pack = raw_pack if isinstance(raw_pack, dict) else {}
        if self._single_line(pack.get("schema"), 80) != "private-companion-expression-pack":
            raise ValueError("不是可识别的表达分享文件")
        if self._int(pack.get("version")) != 1:
            raise ValueError("表达分享文件版本不受支持")
        raw_groups = pack.get("rule_groups")
        if not isinstance(raw_groups, list) or not raw_groups:
            raise ValueError("分享文件中没有表达规则")
        if len(raw_groups) > 200:
            raise ValueError("单次最多导入 200 个表达规则组")
        groups: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        seen_group_signatures: set[str] = set()
        for index, raw_group in enumerate(raw_groups):
            if not isinstance(raw_group, dict):
                rejected.append({"index": index, "reason": "规则组格式无效"})
                continue
            raw_rules = raw_group.get("rules")
            if not isinstance(raw_rules, list) or not raw_rules:
                rejected.append({"index": index, "reason": "规则组为空"})
                continue
            rules: list[dict[str, Any]] = []
            kinds: set[str] = set()
            for raw_rule in raw_rules[:4]:
                normalized, reason = self._expression_share_rule(raw_rule)
                if normalized is None:
                    rejected.append({"index": index, "reason": reason})
                    continue
                kind = normalized["kind"]
                if kind in kinds:
                    rejected.append({"index": index, "reason": f"规则组含有重复的 {kind} 组件"})
                    continue
                kinds.add(kind)
                rules.append(normalized)
            if not rules:
                continue
            signature = hashlib.sha256(
                json.dumps(rules, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest()
            if signature in seen_group_signatures:
                continue
            seen_group_signatures.add(signature)
            groups.append(
                {
                    "id": f"shared-{signature[:16]}",
                    "label": self._single_line(raw_group.get("label"), 100)
                    or rules[0].get("label")
                    or rules[0].get("situation"),
                    "signature": signature,
                    "rules": rules,
                }
            )
        if not groups:
            reason = rejected[0].get("reason") if rejected else "没有可导入的有效规则"
            raise ValueError(str(reason))
        return {
            "schema": "private-companion-expression-pack",
            "version": 1,
            "title": self._single_line(pack.get("title"), 80) or "表达分享包",
            "rule_groups": groups,
            "rejected": rejected,
        }
    def _expression_import_candidates(self, normalized_pack: dict[str, Any]) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        imported_at = datetime.now().strftime("%Y-%m-%d %H:%M")
        for group in normalized_pack.get("rule_groups", []):
            if not isinstance(group, dict):
                continue
            family_key = f"shared_{self._single_line(group.get('signature'), 24)}"
            for raw_rule in group.get("rules", []):
                if not isinstance(raw_rule, dict):
                    continue
                fingerprint = hashlib.sha256(
                    json.dumps(raw_rule, ensure_ascii=False, sort_keys=True).encode("utf-8")
                ).hexdigest()
                rule = dict(raw_rule)
                rule.update(
                    {
                        "id": f"shared-{fingerprint[:20]}",
                        "family_key": family_key,
                        "evidence_count": 1,
                        "review_status": "pending",
                        "shared_import": True,
                        "imported_at": imported_at,
                    }
                )
                candidates.append(rule)
        assigner = getattr(self.plugin, "_assign_expression_rule_families", None)
        if callable(assigner):
            assigner(candidates)
        return candidates
    def _expression_import_preview(
        self,
        normalized_pack: dict[str, Any],
        target: dict[str, Any],
    ) -> dict[str, Any]:
        profile = target.get("expression_profile") if isinstance(target.get("expression_profile"), dict) else {}
        existing = [
            item
            for storage in ("learned_rules", "pending_rules")
            for item in (profile.get(storage) if isinstance(profile.get(storage), list) else [])
            if isinstance(item, dict)
        ]
        duplicate_analyzer = getattr(self.plugin, "_expression_rule_duplicate_analysis", None)
        signature_getter = getattr(self.plugin, "_expression_rule_signature", None)
        candidates = self._expression_import_candidates(normalized_pack)
        duplicate_ids: set[str] = set()
        for candidate in candidates:
            candidate_signature = signature_getter(candidate) if callable(signature_getter) else ""
            for current in existing:
                current_signature = signature_getter(current) if callable(signature_getter) else ""
                if candidate_signature and candidate_signature == current_signature:
                    duplicate_ids.add(str(candidate.get("id") or ""))
                    break
                analysis = duplicate_analyzer(current, candidate) if callable(duplicate_analyzer) else {}
                if isinstance(analysis, dict) and (
                    analysis.get("auto_merge") or self._float(analysis.get("confidence")) >= 0.9
                ):
                    duplicate_ids.add(str(candidate.get("id") or ""))
                    break
        importable = [item for item in candidates if str(item.get("id") or "") not in duplicate_ids]
        family_ids = {
            self._single_line(item.get("family_id"), 100)
            for item in importable
            if self._single_line(item.get("family_id"), 100)
        }
        return {
            "title": normalized_pack.get("title") or "表达分享包",
            "group_count": len(normalized_pack.get("rule_groups", [])),
            "rule_count": len(candidates),
            "importable_group_count": len(family_ids),
            "importable_rule_count": len(importable),
            "duplicate_rule_count": len(duplicate_ids),
            "rejected_count": len(normalized_pack.get("rejected", [])),
            "rejected": normalized_pack.get("rejected", [])[:20],
            "candidates": importable,
        }
    def _expression_import_preview_signature(
        self,
        normalized_pack: dict[str, Any],
        *,
        source_type: str,
        source_id: str,
        scope_revision: int,
        preview: dict[str, Any],
    ) -> str:
        material = {
            "pack": normalized_pack,
            "source_type": source_type,
            "source_id": source_id,
            "scope_revision": int(scope_revision),
            "candidate_ids": sorted(
                self._single_line(item.get("id"), 100)
                for item in preview.get("candidates", []) if isinstance(item, dict)
            ),
        }
        return hashlib.sha256(
            json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
    async def share_expression_library(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        raw_items = payload.get("items")
        if raw_items is not None and not isinstance(raw_items, list):
            return self._error("分享范围格式无效")
        if isinstance(raw_items, list) and not raw_items:
            return self._error("当前范围没有可分享的已启用表达")
        if isinstance(raw_items, list) and len(raw_items) > 200:
            return self._error("单次最多分享 200 个表达规则组")
        try:
            async with self.plugin._data_lock:
                snapshot = deepcopy(self.plugin.data)
            selected = {
                (
                    self._single_line(item.get("source_type"), 16),
                    self._single_line(item.get("source_id"), 80),
                    self._single_line(item.get("rule_family_id"), 100),
                )
                for item in raw_items or []
                if isinstance(item, dict)
            }
            groups: list[dict[str, Any]] = []
            seen: set[str] = set()
            for source_type, collection_key in (("private", "users"), ("group", "groups")):
                collection = snapshot.get(collection_key)
                if not isinstance(collection, dict):
                    continue
                for source_id, source in collection.items():
                    profile = source.get("expression_profile") if isinstance(source, dict) else None
                    learned = profile.get("learned_rules") if isinstance(profile, dict) and isinstance(profile.get("learned_rules"), list) else []
                    try:
                        managed, scope_context = self._expression_admin_scope_context(
                            source_type, self._single_line(source_id, 80), source,
                        )
                        if managed:
                            bound = self._expression_prepare_admin_profile(source, scope_context)
                            learned = [
                                item for item in bound.get("learned_rules", [])
                                if isinstance(item, dict)
                                and validate_expression_scope_binding(
                                    item.get("scope_binding"), scope_context, approval_state="approved",
                                )
                            ]
                    except (ExpressionScopeError, ValueError):
                        continue
                    raw_groups = self.plugin._expression_rule_groups(learned) if callable(getattr(self.plugin, "_expression_rule_groups", None)) else [[item] for item in learned]
                    for raw_group in raw_groups:
                        family_id = self._single_line((raw_group[0] if raw_group else {}).get("family_id"), 100)
                        if selected and (source_type, self._single_line(source_id, 80), family_id) not in selected:
                            continue
                        rules: list[dict[str, Any]] = []
                        for raw_rule in raw_group:
                            rule, _ = self._expression_share_rule(raw_rule)
                            if rule is not None:
                                rules.append(rule)
                        if not rules:
                            continue
                        signature = hashlib.sha256(
                            json.dumps(rules, ensure_ascii=False, sort_keys=True).encode("utf-8")
                        ).hexdigest()
                        if signature in seen:
                            continue
                        seen.add(signature)
                        groups.append(
                            {
                                "id": f"group-{len(groups) + 1:03d}",
                                "label": rules[0].get("label") or rules[0].get("situation"),
                                "rules": rules,
                            }
                        )
            if not groups:
                return self._error("当前范围没有可分享的已启用表达")
            pack = {
                "schema": "private-companion-expression-pack",
                "version": 1,
                "title": self._single_line(payload.get("title"), 80) or "我的表达分享",
                "exported_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "privacy": "仅包含抽象表达规则，不包含用户、群聊、原始消息、证据、反馈或使用记录。",
                "rule_groups": groups,
            }
            return self._ok({"package": pack, "group_count": len(groups), "rule_count": sum(len(item["rules"]) for item in groups)})
        except Exception as exc:
            logger.error(f"生成表达分享包失败: {exc}", exc_info=True)
            return self._exception_error("生成表达分享包失败")
    async def preview_expression_library_import(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        try:
            normalized = self._normalize_expression_share_pack(payload.get("package"))
            async with self.plugin._data_lock:
                source_type = self._single_line(payload.get("target_source_type"), 16).lower()
                source_id = self._single_line(payload.get("target_source_id"), 80)
                target = self._expression_share_target(source_type, source_id)
                if target is None:
                    return self._error("请选择有效的导入目标")
                managed, scope_context = self._expression_admin_scope_context(source_type, source_id, target)
                scope_changed = False
                if managed:
                    before_scope = deepcopy(target.get("expression_profile") or {})
                    self._expression_prepare_admin_profile(target, scope_context)
                    scope_changed = before_scope != (target.get("expression_profile") or {})
                preview = self._expression_import_preview(normalized, target)
                scope_revision = self._int((target.get("expression_profile") or {}).get("scope_revision"))
                preview["target_scope_revision"] = scope_revision
                preview["preview_signature"] = self._expression_import_preview_signature(
                    normalized, source_type=source_type, source_id=source_id,
                    scope_revision=scope_revision, preview=preview,
                )
                if scope_changed:
                    self.plugin._save_data_sync(
                        sections={"groups" if source_type == "group" else "users"}
                    )
            return self._ok(preview)
        except ValueError as exc:
            return self._error(str(exc))
        except Exception as exc:
            logger.error(f"预览表达导入失败: {exc}", exc_info=True)
            return self._exception_error("预览表达导入失败")
    async def apply_expression_library_import(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        destination = self._single_line(payload.get("destination"), 16).lower() or "pending"
        if destination not in {"pending", "learned"}:
            return self._error("导入方式无效")
        try:
            normalized = self._normalize_expression_share_pack(payload.get("package"))
            async with self.plugin._data_lock:
                source_type = self._single_line(payload.get("target_source_type"), 16).lower()
                source_id = self._single_line(payload.get("target_source_id"), 80)
                target = self._expression_share_target(source_type, source_id)
                if target is None:
                    return self._error("请选择有效的导入目标")
                managed, scope_context = self._expression_admin_scope_context(source_type, source_id, target)
                if managed:
                    prepared = self._expression_prepare_admin_profile(target, scope_context)
                    expected_revision = self._int(payload.get("expected_scope_revision"))
                    if expected_revision != self._int(prepared.get("scope_revision")):
                        raise ValueError("导入目标已被其他操作更新，请重新预览")
                preview = self._expression_import_preview(normalized, target)
                if managed:
                    expected_signature = self._expression_import_preview_signature(
                        normalized, source_type=source_type, source_id=source_id,
                        scope_revision=self._int(prepared.get("scope_revision")), preview=preview,
                    )
                    if not hmac.compare_digest(
                        self._single_line(payload.get("preview_signature"), 80), expected_signature,
                    ):
                        raise ValueError("导入预览已失效，请重新预览")
                candidates = [dict(item) for item in preview.get("candidates", []) if isinstance(item, dict)]
                if not candidates:
                    result = self._expression_library_summary(deepcopy(self.plugin.data))
                    result["message"] = "没有需要导入的新表达，目标中已存在相同规则"
                    result["import"] = preview
                    return self._ok(result)
                profile = target.setdefault("expression_profile", {})
                if not isinstance(profile, dict):
                    profile = {}
                    target["expression_profile"] = profile
                before = deepcopy(profile)
                if destination == "learned":
                    for item in candidates:
                        item["review_status"] = "approved"
                        item["approved_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                    merger = getattr(self.plugin, "_merge_learned_expression_rules", None)
                    if callable(merger):
                        merger(
                            profile,
                            candidates,
                            batch_key=f"share-import:{hashlib.sha1(json.dumps(normalized, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()[:20]}",
                            now=time.time(),
                        )
                    else:
                        learned = profile.get("learned_rules") if isinstance(profile.get("learned_rules"), list) else []
                        profile["learned_rules"] = candidates + learned
                    refresher = getattr(self.plugin, "_refresh_expression_voice_profile", None)
                    if callable(refresher):
                        refresher()
                else:
                    pending = profile.get("pending_rules") if isinstance(profile.get("pending_rules"), list) else []
                    profile["pending_rules"] = candidates + pending
                    backfiller = getattr(self.plugin, "_backfill_expression_rule_families", None)
                    if callable(backfiller):
                        backfiller(profile)
                    deduper = getattr(self.plugin, "_deduplicate_expression_rule_families", None)
                    if callable(deduper):
                        deduper(profile["pending_rules"])
                limit = max(24, self._int(getattr(self.plugin, "max_learned_expression_items", 60)) * 2)
                storage_key = "learned_rules" if destination == "learned" else "pending_rules"
                stored = profile.get(storage_key) if isinstance(profile.get(storage_key), list) else []
                profile[storage_key] = stored[:limit]
                profile["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                if managed:
                    try:
                        self._expression_finalize_admin_profile(target, before, scope_context)
                    except Exception:
                        target["expression_profile"] = before
                        raise
                save_sections = {
                    "groups" if source_type == "group" else "users"
                }
                if destination == "learned":
                    save_sections.add("expression_voice_profile")
                self.plugin._save_data_sync(sections=save_sections)
                snapshot = deepcopy(self.plugin.data)
            result = self._expression_library_summary(snapshot)
            imported_groups = self._int(preview.get("importable_group_count"))
            result["message"] = (
                f"已导入 {imported_groups} 个表达规则组并直接启用"
                if destination == "learned"
                else f"已将 {imported_groups} 个表达规则组加入审核队列"
            )
            result["import"] = {**preview, "destination": destination}
            return self._ok(result)
        except ValueError as exc:
            return self._error(str(exc))
        except Exception as exc:
            logger.error(f"导入表达分享包失败: {exc}", exc_info=True)
            return self._exception_error("导入表达分享包失败")
    def _expression_admin_scope_context(
        self,
        source_type: str,
        source_id: str,
        owner: dict[str, Any],
    ) -> tuple[bool, Any | None]:
        managed = getattr(self.plugin, "req041_scoped_projection_sync", None) is not None
        if not managed:
            return False, None
        if source_type == "persona":
            if source_id != "current-persona":
                raise ValueError("人格全局规则来源标识无效")
            resolver = getattr(self.plugin, "_req041_persona_global_context", None)
            context = resolver(purpose="rule_write") if callable(resolver) else None
        elif source_type == "private":
            resolver = getattr(self.plugin, "_req041_scoped_context_for_user", None)
            context = resolver(owner, kind="private", purpose="rule_write") if callable(resolver) else None
        elif source_type == "group":
            normalize = getattr(self.plugin, "_normalize_group_identity_id", None)
            normalized_source = self._single_line(normalize(source_id) if callable(normalize) else source_id, 160)
            raw_owner_group = owner.get("group_id") or source_id
            normalized_owner = self._single_line(
                normalize(raw_owner_group) if callable(normalize) else raw_owner_group, 160,
            )
            if not normalized_source or normalized_source != normalized_owner:
                raise ValueError("表达群来源标识与正式作用域不一致")
            resolver = getattr(self.plugin, "_req041_scoped_group_context", None)
            context = resolver(normalized_owner, purpose="rule_write") if callable(resolver) else None
        else:
            raise ValueError("表达来源类型无效")
        if context is None:
            raise ValueError("表达来源没有可写的正式身份作用域")
        return True, context
    def _expression_prepare_admin_profile(
        self,
        owner: dict[str, Any],
        context: Any,
    ) -> dict[str, Any]:
        profile = owner.get("expression_profile") if isinstance(owner.get("expression_profile"), dict) else {}
        binder = getattr(self.plugin, "_expression_bind_profile_scope", None)
        try:
            bound = binder(profile, context, bump_revision=False) if callable(binder) else bind_expression_profile(
                profile, context, bump_revision=False,
            )
        except (ExpressionScopeError, TypeError, ValueError) as exc:
            raise ValueError(f"表达来源作用域校验失败：{exc}") from exc
        owner["expression_profile"] = bound
        return bound
    def _expression_validate_admin_revision(
        self,
        profile: dict[str, Any],
        payload: dict[str, Any],
    ) -> None:
        raw_expected = payload.get("expected_scope_revision")
        if raw_expected in (None, ""):
            raise ValueError("缺少表达资料版本，请刷新页面后重试")
        expected = self._int(raw_expected)
        current = max(1, self._int(profile.get("scope_revision")))
        if expected != current:
            raise ValueError("表达资料已被其他操作更新，请刷新页面后重试")
        raw_items = payload.get("expected_item_revisions")
        if not isinstance(raw_items, dict):
            raise ValueError("缺少表达项版本，请刷新页面后重试")
        requested = {self._single_line(key, 100): self._int(value) for key, value in raw_items.items() if self._single_line(key, 100)}
        if not requested:
            raise ValueError("缺少表达项版本，请刷新页面后重试")
        found: dict[str, int] = {}
        target_ids: set[str] = set()
        target_rule = self._single_line(payload.get("rule_id"), 100)
        target_family = self._single_line(payload.get("rule_family_id"), 100)
        target_sample = self._single_line(payload.get("sample_id"), 100)
        for storage_key in ("samples", "pending_samples", "learned_rules", "pending_rules"):
            items = profile.get(storage_key) if isinstance(profile.get(storage_key), list) else []
            for index, item in enumerate(items):
                if not isinstance(item, dict):
                    continue
                item_id = self._single_line(item.get("id"), 100) or f"{storage_key}:{index}"
                binding = item.get("scope_binding") if isinstance(item.get("scope_binding"), dict) else {}
                if (
                    (target_rule and item_id == target_rule)
                    or (target_family and self._single_line(item.get("family_id"), 100) == target_family)
                    or (target_sample and item_id == target_sample)
                ):
                    target_ids.add(item_id)
                if item_id in requested:
                    found[item_id] = self._int(binding.get("revision"))
        if found != requested or (target_ids and set(requested) != target_ids):
            raise ValueError("表达项已被其他操作更新，请刷新页面后重试")
    @staticmethod
    def _expression_item_content(item: dict[str, Any]) -> dict[str, Any]:
        result = deepcopy(item)
        result.pop("scope_binding", None)
        return result
    def _expression_finalize_admin_profile(
        self,
        owner: dict[str, Any],
        before: dict[str, Any],
        context: Any,
    ) -> None:
        profile = owner.get("expression_profile") if isinstance(owner.get("expression_profile"), dict) else {}
        previous: dict[str, tuple[str, dict[str, Any]]] = {}
        for storage_key in (
            "samples", "pending_samples", "learned_rules", "pending_rules",
            "rejected_samples", "revoked_samples", "rejected_rules", "revoked_rules",
        ):
            for index, item in enumerate(before.get(storage_key) if isinstance(before.get(storage_key), list) else []):
                if isinstance(item, dict):
                    item_id = self._single_line(item.get("id"), 100) or f"{storage_key}:{index}"
                    previous[item_id] = (storage_key, item)
        states = {
            "samples": ("approved", "administrator"),
            "pending_samples": ("pending", ""),
            "learned_rules": ("approved", "administrator"),
            "pending_rules": ("pending", ""),
            "rejected_samples": ("rejected", "administrator"),
            "revoked_samples": ("revoked", "administrator"),
            "rejected_rules": ("rejected", "administrator"),
            "revoked_rules": ("revoked", "administrator"),
        }
        for storage_key, (approval_state, actor) in states.items():
            items = profile.get(storage_key) if isinstance(profile.get(storage_key), list) else []
            rebound: list[dict[str, Any]] = []
            for index, item in enumerate(items):
                if not isinstance(item, dict):
                    continue
                item_id = self._single_line(item.get("id"), 100) or f"{storage_key}:{index}"
                old_storage, old = previous.get(item_id, ("", {}))
                changed = bool(
                    not old
                    or old_storage != storage_key
                    or self._expression_item_content(old) != self._expression_item_content(item)
                )
                existing = item.get("scope_binding") if isinstance(item.get("scope_binding"), dict) else {}
                old_binding = old.get("scope_binding") if isinstance(old.get("scope_binding"), dict) else {}
                already_advanced = bool(
                    old and self._int(existing.get("revision")) > self._int(old_binding.get("revision"))
                )
                state_changed = bool(
                    old_binding and self._single_line(old_binding.get("approval_state"), 24) != approval_state
                )
                approved_by = actor if changed and approval_state == "approved" else self._single_line(
                    existing.get("approved_by"), 80,
                )
                if approval_state == "approved" and not approved_by:
                    approved_by = "legacy_migration"
                rebound.append(bind_expression_item(
                    item, context, approval_state=approval_state,
                    approved_by=approved_by,
                    bump_revision=state_changed or (changed and not already_advanced),
                ))
            profile[storage_key] = rebound
        owner["expression_profile"] = bind_expression_profile(profile, context, bump_revision=True)
    @staticmethod
    def _expression_promotion_confirmation(
        *, operation_id: str, source_profile: dict[str, Any], target_profile: dict[str, Any],
        family_id: str, rules: list[dict[str, Any]],
    ) -> str:
        material = {
            "action": "promote_rule_group",
            "operation_id": operation_id,
            "family_id": family_id,
            "source_scope": source_profile.get("scope_ownership"),
            "source_revision": int(source_profile.get("scope_revision") or 0),
            "target_scope": target_profile.get("scope_ownership"),
            "target_revision": int(target_profile.get("scope_revision") or 0),
            "rules": rules,
        }
        return hashlib.sha256(
            json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
    def _expression_global_promotion_state(
        self,
        *,
        source_type: str,
        source_id: str,
        family_id: str,
        operation_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        collection = self.plugin.data.get("groups" if source_type == "group" else "users")
        source = collection.get(source_id) if isinstance(collection, dict) else None
        if source_type not in {"private", "group"} or not isinstance(source, dict):
            raise ValueError("表达规则来源不存在")
        managed, source_context = self._expression_admin_scope_context(source_type, source_id, source)
        if not managed or source_context is None:
            raise ValueError("表达来源没有可写的正式身份作用域")
        source_owner = {
            "expression_profile": deepcopy(source.get("expression_profile"))
            if isinstance(source.get("expression_profile"), dict) else {}
        }
        source_profile = self._expression_prepare_admin_profile(source_owner, source_context)
        self._expression_validate_admin_revision(source_profile, payload)
        matched = [
            item for item in source_profile.get("learned_rules", [])
            if isinstance(item, dict)
            and self._single_line(item.get("family_id"), 100) == family_id
        ]
        if not matched:
            raise ValueError("没有找到要提升的已审核规则组")
        sanitized: list[dict[str, Any]] = []
        for item in matched:
            validate_expression_scope_binding(
                item.get("scope_binding"), source_context, approval_state="approved",
            )
            clean, reason = self._expression_share_rule(item)
            if clean is None:
                raise ValueError(reason or "规则不能安全提升")
            sanitized.append(clean)
        persona_context_getter = getattr(self.plugin, "_req041_persona_global_context", None)
        persona_context = persona_context_getter(purpose="rule_write") if callable(persona_context_getter) else None
        if persona_context is None:
            raise ValueError("当前人格全局规则作用域不可用")
        raw_global = self.plugin.data.get("_req041_persona_expression_profile")
        global_owner = {
            "expression_profile": deepcopy(raw_global) if isinstance(raw_global, dict) else {}
        }
        target_profile = self._expression_prepare_admin_profile(global_owner, persona_context)
        family_fingerprint = hashlib.sha256(
            json.dumps(sanitized, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        promoted: list[dict[str, Any]] = []
        for index, clean in enumerate(sanitized):
            rule_fingerprint = hashlib.sha256(
                json.dumps(clean, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            candidate = {
                **clean,
                "id": f"persona-{rule_fingerprint[:20]}",
                "family_id": f"persona-{family_fingerprint[:20]}",
                "family_key": f"persona_{family_fingerprint[:20]}",
                "evidence_count": 1,
                "review_status": "approved",
                "explicit_global_promotion": True,
                "component_index": index,
            }
            promoted.append(bind_expression_item(
                candidate, persona_context, approval_state="approved",
                approved_by="administrator",
            ))
        confirmation = self._expression_promotion_confirmation(
            operation_id=operation_id,
            source_profile=source_profile,
            target_profile=target_profile,
            family_id=family_id,
            rules=promoted,
        )
        return {
            "source_profile": source_profile,
            "persona_context": persona_context,
            "global_owner": global_owner,
            "target_profile": target_profile,
            "rules": promoted,
            "confirmation_token": confirmation,
        }
    async def get_expression_library(self) -> dict[str, Any]:
        try:
            async with self.plugin._data_lock:
                # Read endpoints may prepare a disposable view, but must never repair
                # or persist the live runtime state as a side effect of a GET request.
                snapshot = deepcopy(self.plugin.data)
                normalizer = getattr(self.plugin, "_normalize_group_expression_profile", None)
                pruner = getattr(self.plugin, "_prune_invalid_expression_rules", None)
                family_backfiller = getattr(self.plugin, "_backfill_expression_rule_families", None)
                for collection_key in ("users", "groups"):
                    collection = snapshot.get(collection_key)
                    if not isinstance(collection, dict):
                        continue
                    source_type = "group" if collection_key == "groups" else "private"
                    for source_id, item in collection.items():
                        profile = item.get("expression_profile") if isinstance(item, dict) else None
                        if not isinstance(profile, dict):
                            continue
                        if collection_key == "groups" and callable(normalizer):
                            normalizer(profile)
                        if callable(pruner):
                            pruner(profile)
                        if callable(family_backfiller):
                            family_backfiller(profile)
                        try:
                            managed, scope_context = self._expression_admin_scope_context(
                                source_type, self._single_line(source_id, 80), item,
                            )
                            if managed:
                                self._expression_prepare_admin_profile(item, scope_context)
                        except (ExpressionScopeError, ValueError):
                            # Pending/unresolved legacy sources remain visible but cannot be mutated.
                            pass
            return self._ok(self._expression_library_summary(snapshot))
        except Exception as exc:
            logger.error(f"获取统一表达学习库失败: {exc}", exc_info=True)
            return self._exception_error("获取统一表达学习库失败")
    async def update_expression_library(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        source_type = self._single_line(payload.get("source_type"), 16)
        source_id = self._single_line(payload.get("source_id"), 80)
        action = self._single_line(payload.get("expression_action"), 40)
        if action == "promote_rule_group":
            family_id = self._single_line(payload.get("rule_family_id"), 100)
            operation_id = self._single_line(payload.get("operation_id"), 120)
            confirmation_token = self._single_line(payload.get("confirmation_token"), 80)
            if "dry_run" in payload and type(payload.get("dry_run")) is not bool:
                return self._error("dry_run 必须是 JSON 布尔值")
            dry_run = payload.get("dry_run", True)
            if not source_id or not family_id or not operation_id:
                return self._error("缺少规则来源、规则组或操作标识")
            if not dry_run and not confirmation_token:
                return self._error("提升为全局规则前必须先生成预览")
            try:
                async with self.plugin._data_lock:
                    operations = self.plugin.data.get("_req041_expression_promotion_operations")
                    if not isinstance(operations, dict):
                        operations = {}
                    prior = operations.get(operation_id)
                    token_hash = hashlib.sha256(confirmation_token.encode("utf-8")).hexdigest()
                    if not dry_run and isinstance(prior, dict):
                        if not hmac.compare_digest(
                            self._single_line(prior.get("confirmation_token_hash"), 80), token_hash
                        ):
                            return self._error("操作标识已用于另一份全局提升请求")
                        snapshot = deepcopy(self.plugin.data)
                        result = self._expression_library_summary(snapshot)
                        result["promotion"] = {
                            "ok": True,
                            "code": "persona_global_promotion_replayed",
                            "rule_count": self._int(prior.get("rule_count")),
                        }
                        result["message"] = "该全局提升已完成，无需重复操作"
                        return self._ok(result)
                    prepared = self._expression_global_promotion_state(
                        source_type=source_type,
                        source_id=source_id,
                        family_id=family_id,
                        operation_id=operation_id,
                        payload=payload,
                    )
                    expected = prepared["confirmation_token"]
                    if dry_run:
                        return self._ok({
                            "promotion": {
                                "ok": True,
                                "code": "persona_global_promotion_preview",
                                "rule_count": len(prepared["rules"]),
                                "target_scope_revision": self._int(
                                    prepared["target_profile"].get("scope_revision")
                                ),
                                "confirmation_token": expected,
                            }
                        })
                    if not hmac.compare_digest(confirmation_token, expected):
                        return self._error("规则来源或全局规则库已变化，请重新预览")
                    global_owner = prepared["global_owner"]
                    before = deepcopy(prepared["target_profile"])
                    learned = before.get("learned_rules") if isinstance(before.get("learned_rules"), list) else []
                    existing_ids = {
                        self._single_line(item.get("id"), 100)
                        for item in learned if isinstance(item, dict)
                    }
                    inserted = [
                        {**item, "approved_at": datetime.now().strftime("%Y-%m-%d %H:%M")}
                        for item in prepared["rules"]
                        if self._single_line(item.get("id"), 100) not in existing_ids
                    ]
                    if inserted:
                        learned = inserted + learned
                        limit = max(12, int(getattr(self.plugin, "max_learned_expression_items", 60) or 60))
                        global_owner["expression_profile"] = {
                            **before,
                            "learned_rules": learned[:limit],
                            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                        }
                        self._expression_finalize_admin_profile(
                            global_owner, before, prepared["persona_context"],
                        )
                        self.plugin.data["_req041_persona_expression_profile"] = global_owner["expression_profile"]
                        operations[operation_id] = {
                            "confirmation_token_hash": token_hash,
                            "rule_count": len(inserted),
                            "completed_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                        }
                        while len(operations) > 128:
                            operations.pop(next(iter(operations)))
                        self.plugin.data["_req041_expression_promotion_operations"] = operations
                        self.plugin._save_data_sync(
                            sections={
                                "_req041_persona_expression_profile",
                                "_req041_expression_promotion_operations",
                            }
                        )
                    snapshot = deepcopy(self.plugin.data)
                result = self._expression_library_summary(snapshot)
                result["promotion"] = {
                    "ok": True,
                    "code": "persona_global_promoted" if inserted else "persona_global_already_present",
                    "rule_count": len(inserted),
                }
                result["message"] = (
                    f"已将 {len(inserted)} 条规则显式提升为当前人格全局规则"
                    if inserted else "当前人格全局规则库已包含同一规则组"
                )
                return self._ok(result)
            except (ExpressionScopeError, ValueError) as exc:
                return self._error(str(exc))
            except Exception as exc:
                logger.error("提升人格全局表达规则失败: %s", exc, exc_info=True)
                return self._exception_error("提升人格全局表达规则失败")
        if action in {"batch_approve_rule_groups", "batch_reject_rule_groups"}:
            raw_items = payload.get("items")
            if not isinstance(raw_items, list) or not raw_items:
                return self._error("请至少选择一个待审核规则组")
            if len(raw_items) > 200:
                return self._error("单次最多审核 200 个规则组")
            try:
                async with self.plugin._data_lock:
                    results: list[dict[str, Any]] = []
                    seen: set[tuple[str, str, str]] = set()
                    changed = False
                    save_sections: set[str] = set()
                    for raw_item in raw_items:
                        if not isinstance(raw_item, dict):
                            continue
                        target_type = self._single_line(raw_item.get("source_type"), 16)
                        target_id = self._single_line(raw_item.get("source_id"), 80)
                        family_id = self._single_line(raw_item.get("rule_family_id"), 100)
                        target_key = (target_type, target_id, family_id)
                        if target_key in seen:
                            continue
                        seen.add(target_key)
                        if target_type not in {"private", "group"} or not target_id or not family_id:
                            results.append({"status": "skipped", "reason": "目标标识不完整"})
                            continue
                        collection_key = "groups" if target_type == "group" else "users"
                        collection = self.plugin.data.get(collection_key)
                        item = collection.get(target_id) if isinstance(collection, dict) else None
                        if not isinstance(item, dict):
                            results.append({"status": "skipped", "reason": "来源不存在", "source_id": target_id})
                            continue
                        mutation_before = deepcopy(item.get("expression_profile") or {})
                        if target_type == "group":
                            normalizer = getattr(self.plugin, "_normalize_group_expression_profile", None)
                            profile = item.get("expression_profile")
                            if callable(normalizer) and isinstance(profile, dict):
                                normalizer(profile)
                        try:
                            managed, scope_context = self._expression_admin_scope_context(
                                target_type, target_id, item,
                            )
                            if managed:
                                prepared = self._expression_prepare_admin_profile(item, scope_context)
                                self._expression_validate_admin_revision(prepared, raw_item)
                            before = deepcopy(item.get("expression_profile") or {})
                            result_message = self._apply_expression_profile_action(
                                item,
                                {
                                "expression_action": "approve_rule_group"
                                if action == "batch_approve_rule_groups" else "reject_rule_group",
                                "rule_family_id": family_id,
                                },
                            )
                            profile_changed = mutation_before != (item.get("expression_profile") or {})
                            if managed and profile_changed:
                                try:
                                    self._expression_finalize_admin_profile(item, before, scope_context)
                                except Exception:
                                    item["expression_profile"] = before
                                    raise
                        except (ExpressionScopeError, ValueError) as exc:
                            results.append({
                                "status": "skipped", "reason": str(exc),
                                "source_type": target_type, "source_id": target_id,
                                "rule_family_id": family_id,
                            })
                            continue
                        succeeded = not result_message.startswith(("没有找到", "缺少", "规则组中没有"))
                        if profile_changed:
                            save_sections.add(collection_key)
                            if succeeded:
                                changed = True
                        results.append(
                            {
                                "status": "success" if succeeded else "skipped",
                                "source_type": target_type,
                                "source_id": target_id,
                                "rule_family_id": family_id,
                                "message": result_message,
                            }
                        )
                    if changed and action == "batch_approve_rule_groups":
                        refresher = getattr(self.plugin, "_refresh_expression_voice_profile", None)
                        if callable(refresher):
                            refresher()
                        save_sections.add("expression_voice_profile")
                    if save_sections:
                        self.plugin._save_data_sync(sections=save_sections)
                    snapshot = deepcopy(self.plugin.data)
                result = self._expression_library_summary(snapshot)
                success_count = sum(1 for item in results if item.get("status") == "success")
                skipped_count = len(results) - success_count
                verb = "通过" if action == "batch_approve_rule_groups" else "拒绝"
                result["message"] = f"已{verb} {success_count} 个规则组" + (f"，跳过 {skipped_count} 个" if skipped_count else "")
                result["batch"] = {
                    "requested": len(seen),
                    "succeeded": success_count,
                    "skipped": skipped_count,
                    "results": results,
                }
                return self._ok(result)
            except Exception as exc:
                logger.error(f"批量审核表达规则失败: {exc}", exc_info=True)
                return self._error(str(exc))
        if action == "clear_all_pending":
            try:
                async with self.plugin._data_lock:
                    cleared = 0
                    save_sections: set[str] = set()
                    for collection_key in ("users", "groups"):
                        collection = self.plugin.data.get(collection_key)
                        if not isinstance(collection, dict):
                            continue
                        source_type = "group" if collection_key == "groups" else "private"
                        for source_id, item in collection.items():
                            if not isinstance(item, dict):
                                continue
                            profile = item.get("expression_profile")
                            pending = profile.get("pending_samples") if isinstance(profile, dict) else None
                            pending_rules = profile.get("pending_rules") if isinstance(profile, dict) else None
                            item_count = (len(pending) if isinstance(pending, list) else 0) + (
                                len(pending_rules) if isinstance(pending_rules, list) else 0
                            )
                            if item_count:
                                try:
                                    mutation_before = deepcopy(item.get("expression_profile") or {})
                                    managed, scope_context = self._expression_admin_scope_context(
                                        source_type, self._single_line(source_id, 80), item,
                                    )
                                    if managed:
                                        self._expression_prepare_admin_profile(item, scope_context)
                                    before = deepcopy(item.get("expression_profile") or {})
                                    self._apply_expression_profile_action(item, {"expression_action": "clear_pending"})
                                    if managed and before != (item.get("expression_profile") or {}):
                                        try:
                                            self._expression_finalize_admin_profile(item, before, scope_context)
                                        except Exception:
                                            item["expression_profile"] = before
                                            raise
                                    if mutation_before != (item.get("expression_profile") or {}):
                                        save_sections.add(collection_key)
                                    cleared += item_count
                                except (ExpressionScopeError, ValueError):
                                    continue
                    if save_sections:
                        self.plugin._save_data_sync(sections=save_sections)
                    snapshot = deepcopy(self.plugin.data)
                result = self._expression_library_summary(snapshot)
                result["message"] = f"已清空 {cleared} 条待审核表达资料"
                return self._ok(result)
            except Exception as exc:
                logger.error(f"清空统一表达待审样本失败: {exc}", exc_info=True)
                return self._exception_error("清空统一表达待审样本失败")
        if source_type not in {"private", "group", "persona"} or not source_id:
            return self._error("缺少有效的表达样本来源")
        if action not in {
            "approve", "reject", "approve_rule", "reject_rule", "delete_sample", "delete_rule",
            "approve_rule_group", "reject_rule_group", "delete_rule_group", "update_rule_group",
        }:
            return self._error("不支持的表达样本操作")
        try:
            async with self.plugin._data_lock:
                persona_target = source_type == "persona"
                collection_key = "groups" if source_type == "group" else "users"
                collection = self.plugin.data.get(collection_key)
                item = (
                    {
                        "expression_profile": deepcopy(
                            self.plugin.data.get("_req041_persona_expression_profile")
                        )
                    }
                    if persona_target
                    and source_id == "current-persona"
                    and isinstance(self.plugin.data.get("_req041_persona_expression_profile"), dict)
                    else collection.get(source_id) if isinstance(collection, dict) else None
                )
                if not isinstance(item, dict):
                    return self._error("表达样本来源不存在")
                mutation_before = deepcopy(item.get("expression_profile") or {})
                if source_type == "group":
                    normalizer = getattr(self.plugin, "_normalize_group_expression_profile", None)
                    profile = item.get("expression_profile")
                    if callable(normalizer) and isinstance(profile, dict):
                        normalizer(profile)
                managed, scope_context = self._expression_admin_scope_context(
                    source_type, source_id, item,
                )
                if managed:
                    prepared = self._expression_prepare_admin_profile(item, scope_context)
                    self._expression_validate_admin_revision(prepared, payload)
                before = deepcopy(item.get("expression_profile") or {})
                payload["source_type"] = source_type
                action_message = self._apply_expression_profile_action(item, payload)
                if managed and before != (item.get("expression_profile") or {}):
                    try:
                        self._expression_finalize_admin_profile(item, before, scope_context)
                    except Exception:
                        item["expression_profile"] = before
                        raise
                mutation_changed = mutation_before != (item.get("expression_profile") or {})
                if persona_target:
                    self.plugin.data["_req041_persona_expression_profile"] = item["expression_profile"]
                voice_changed = False
                if action in {
                    "approve", "approve_rule", "approve_rule_group", "delete_sample", "delete_rule", "delete_rule_group",
                    "update_rule_group",
                }:
                    voice_before = deepcopy(self.plugin.data.get("expression_voice_profile"))
                    voice_refresher = getattr(self.plugin, "_refresh_expression_voice_profile", None)
                    if callable(voice_refresher):
                        voice_refresher()
                        voice_changed = voice_before != self.plugin.data.get("expression_voice_profile")
                save_sections = {
                    "_req041_persona_expression_profile" if persona_target else collection_key
                } if mutation_changed else set()
                if voice_changed:
                    save_sections.add("expression_voice_profile")
                if save_sections:
                    self.plugin._save_data_sync(sections=save_sections)
                snapshot = deepcopy(self.plugin.data)
            result = self._expression_library_summary(snapshot)
            result["message"] = action_message
            return self._ok(result)
        except ValueError as exc:
            return self._error(str(exc))
        except Exception as exc:
            logger.error(f"更新统一表达学习库失败: {exc}", exc_info=True)
            return self._exception_error("更新统一表达学习库失败")
    def _apply_expression_profile_action(self, user: dict[str, Any], payload: dict[str, Any]) -> str:
        profile = user.setdefault("expression_profile", {})
        if not isinstance(profile, dict):
            profile = {}
            user["expression_profile"] = profile
        action = self._single_line(payload.get("expression_action"), 40)
        family_backfiller = getattr(self.plugin, "_backfill_expression_rule_families", None)
        if callable(family_backfiller):
            family_backfiller(profile)
        pending = profile.get("pending_samples") if isinstance(profile.get("pending_samples"), list) else []
        pending_rules = profile.get("pending_rules") if isinstance(profile.get("pending_rules"), list) else []
        samples = profile.get("samples") if isinstance(profile.get("samples"), list) else []
        sample_id = self._single_line(payload.get("sample_id"), 40)
        rule_id = self._single_line(payload.get("rule_id"), 100)
        rule_family_id = self._single_line(payload.get("rule_family_id"), 100)
        sample_index = self._int(payload.get("sample_index"))

        def archive_items(storage_key: str, items: list[Any], state: str) -> None:
            archived = profile.get(storage_key) if isinstance(profile.get(storage_key), list) else []
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
            for raw in items:
                if not isinstance(raw, dict):
                    continue
                item = dict(raw)
                item["review_status"] = state
                item[f"{state}_at"] = stamp
                archived.insert(0, item)
            limit = max(24, int(getattr(self.plugin, "max_learned_expression_items", 60) or 60) * 2)
            profile[storage_key] = archived[:limit]

        def find_index(items: list[Any]) -> int:
            if sample_id:
                for idx, item in enumerate(items):
                    if isinstance(item, dict) and self._single_line(item.get("id"), 40) == sample_id:
                        return idx
            if 0 <= sample_index < len(items):
                return sample_index
            return -1

        def find_rule_index(items: list[Any]) -> int:
            if not rule_id:
                return -1
            for idx, item in enumerate(items):
                if isinstance(item, dict) and self._single_line(item.get("id"), 100) == rule_id:
                    return idx
            return -1

        if action == "clear_pending":
            archive_items("rejected_samples", pending, "rejected")
            archive_items("rejected_rules", pending_rules, "rejected")
            profile["pending_samples"] = []
            profile["pending_rules"] = []
            profile["pending_count"] = 0
            profile["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            return "已清空待审核表达资料"
        if action in {"approve", "reject"}:
            idx = find_index(pending)
            if idx < 0:
                return "没有找到待审核样本"
            item = pending.pop(idx)
            profile["pending_samples"] = pending
            profile["pending_count"] = len(pending)
            if action == "reject":
                archive_items("rejected_samples", [item], "rejected")
                profile["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                return "已删除待审核样本"
            if isinstance(item, dict):
                approved = dict(item)
                approved.pop("review_status", None)
                approved["approved_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                samples.insert(0, approved)
                limit = max(12, int(getattr(self.plugin, "max_learned_expression_items", 60) or 60))
                profile["samples"] = samples[:limit]
                refresher = getattr(self.plugin, "_refresh_expression_profile_legacy_summary", None)
                if callable(refresher):
                    refresher(profile)
                profile["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                voice_refresher = getattr(self.plugin, "_refresh_expression_voice_profile", None)
                if callable(voice_refresher):
                    voice_refresher()
                return "已通过表达样本"
            return "待审核样本格式异常"
        if action in {"approve_rule", "reject_rule"}:
            idx = find_rule_index(pending_rules)
            if idx < 0:
                return "没有找到待审核规则"
            item = pending_rules.pop(idx)
            profile["pending_rules"] = pending_rules
            if action == "reject_rule":
                archive_items("rejected_rules", [item], "rejected")
                profile["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                return "已拒绝归纳规则"
            if not isinstance(item, dict):
                return "待审核规则格式异常"
            validator = getattr(self.plugin, "_expression_rule_definition_is_valid", None)
            if callable(validator) and not validator(item):
                profile["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                return "该规则不是可复用表达或具体语法，已从待审核区移除"
            approved = dict(item)
            approved["review_status"] = "approved"
            if self._single_line(item.get("review_status"), 24).lower() == "needs_review":
                approved["negative_feedback_before_review"] = self._int(item.get("negative_feedback"))
                approved["negative_feedback"] = 0
                approved["review_cycles"] = self._int(item.get("review_cycles")) + 1
                approved.pop("review_reason", None)
            approved["approved_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            merger = getattr(self.plugin, "_merge_learned_expression_rules", None)
            if callable(merger):
                merger(
                    profile,
                    [approved],
                    batch_key=f"approve:{self._single_line(item.get('last_batch_key'), 40) or rule_id}",
                    now=time.time(),
                )
            else:
                learned_rules = profile.get("learned_rules") if isinstance(profile.get("learned_rules"), list) else []
                learned_rules.insert(0, approved)
                profile["learned_rules"] = learned_rules[: max(12, int(getattr(self.plugin, "max_learned_expression_items", 60) or 60))]
            profile["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            return "已通过表达规则，后续匹配情境时可以使用"
        if action == "update_rule_group":
            if not rule_family_id:
                raise ValueError("缺少规则组标识")
            rule_storage = self._single_line(payload.get("rule_storage"), 16).lower()
            if rule_storage not in {"pending", "learned"}:
                raise ValueError("缺少有效的规则组状态")
            storage_key = "pending_rules" if rule_storage == "pending" else "learned_rules"
            stored_rules = profile.get(storage_key) if isinstance(profile.get(storage_key), list) else []
            matched_indexes = [
                idx
                for idx, item in enumerate(stored_rules)
                if isinstance(item, dict) and self._single_line(item.get("family_id"), 100) == rule_family_id
            ]
            if not matched_indexes:
                raise ValueError("没有找到要编辑的表达规则组，可能已在其他页面中被处理，请刷新后重试")

            situation = self._single_line(payload.get("situation"), 100)
            label = self._single_line(payload.get("label"), 100) or situation
            avoid = self._single_line(payload.get("avoid"), 160) or "事实、工具结果、安全边界或人格发生冲突时不用"
            if not situation:
                raise ValueError("适用情境不能为空")

            raw_signals = payload.get("signals")
            if isinstance(raw_signals, str):
                raw_signals = re.split(r"[,，/、|\s]+", raw_signals)
            signals: list[str] = []
            for raw_signal in raw_signals if isinstance(raw_signals, list) else []:
                signal = self._single_line(raw_signal, 24)
                if signal and signal not in signals:
                    signals.append(signal)
                if len(signals) >= 8:
                    break

            component_payloads = {
                "style": payload.get("style_rule") if isinstance(payload.get("style_rule"), dict) else None,
                "grammar": payload.get("grammar_rule") if isinstance(payload.get("grammar_rule"), dict) else None,
            }
            validator = getattr(self.plugin, "_expression_rule_definition_is_valid", None)
            updated_rules: list[tuple[int, dict[str, Any]]] = []
            edited_at = datetime.now().strftime("%Y-%m-%d %H:%M")
            for idx in matched_indexes:
                current = stored_rules[idx]
                kind = self._single_line(current.get("kind"), 16).lower()
                component = component_payloads.get(kind)
                if kind not in {"style", "grammar"} or not isinstance(component, dict):
                    raise ValueError("规则组组件不完整，请刷新页面后重试")
                pattern = self._single_line(component.get("pattern"), 100)
                instruction = self._single_line(component.get("instruction"), 160)
                candidate = dict(current)
                candidate.update(
                    {
                        "label": label,
                        "situation": situation,
                        "pattern": pattern,
                        "instruction": instruction,
                        "keywords": list(signals),
                        "tags": list(signals),
                        "avoid": avoid,
                        "manually_edited": True,
                        "edited_at": edited_at,
                    }
                )
                if callable(validator) and not validator(candidate):
                    kind_label = "可复用表达" if kind == "style" else "句法结构"
                    raise ValueError(f"{kind_label}不符合可复用规则要求，请补全具体结构和使用指令")
                updated_rules.append((idx, candidate))

            for idx, candidate in updated_rules:
                stored_rules[idx] = candidate
            profile[storage_key] = stored_rules
            profile["updated_at"] = edited_at
            return f"已保存表达规则组，共更新 {len(updated_rules)} 条互补规则"
        if action in {"approve_rule_group", "reject_rule_group"}:
            if not rule_family_id:
                return "缺少规则组标识"
            matched = [
                item
                for item in pending_rules
                if isinstance(item, dict) and self._single_line(item.get("family_id"), 100) == rule_family_id
            ]
            if not matched:
                return "没有找到待审核规则组"
            profile["pending_rules"] = [
                item
                for item in pending_rules
                if not isinstance(item, dict) or self._single_line(item.get("family_id"), 100) != rule_family_id
            ]
            if action == "reject_rule_group":
                archive_items("rejected_rules", matched, "rejected")
                profile["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                return f"已拒绝规则组中的 {len(matched)} 条归纳规则"
            validator = getattr(self.plugin, "_expression_rule_definition_is_valid", None)
            approved_items: list[dict[str, Any]] = []
            approved_at = datetime.now().strftime("%Y-%m-%d %H:%M")
            for item in matched:
                if callable(validator) and not validator(item):
                    continue
                approved = dict(item)
                approved["review_status"] = "approved"
                if self._single_line(item.get("review_status"), 24).lower() == "needs_review":
                    approved["negative_feedback_before_review"] = self._int(item.get("negative_feedback"))
                    approved["negative_feedback"] = 0
                    approved["review_cycles"] = self._int(item.get("review_cycles")) + 1
                    approved.pop("review_reason", None)
                approved["approved_at"] = approved_at
                approved_items.append(approved)
            if not approved_items:
                profile["updated_at"] = approved_at
                return "规则组中没有可复用规则，已从待审核区移除"
            merger = getattr(self.plugin, "_merge_learned_expression_rules", None)
            if callable(merger):
                merger(
                    profile,
                    approved_items,
                    batch_key=f"approve-family:{rule_family_id}",
                    now=time.time(),
                )
            else:
                learned_rules = profile.get("learned_rules") if isinstance(profile.get("learned_rules"), list) else []
                learned_rules[0:0] = approved_items
                profile["learned_rules"] = learned_rules[: max(12, int(getattr(self.plugin, "max_learned_expression_items", 60) or 60))]
            profile["updated_at"] = approved_at
            return f"已通过规则组，共启用 {len(approved_items)} 条互补规则"
        if action == "delete_sample":
            idx = find_index(samples)
            if idx < 0:
                return "没有找到已入库样本"
            removed_item = samples.pop(idx)
            archive_items("revoked_samples", [removed_item], "revoked")
            profile["samples"] = samples
            refresher = getattr(self.plugin, "_refresh_expression_profile_legacy_summary", None)
            if callable(refresher):
                refresher(profile)
            profile["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            voice_refresher = getattr(self.plugin, "_refresh_expression_voice_profile", None)
            if callable(voice_refresher):
                voice_refresher()
            return "已删除表达样本"
        if action == "delete_rule":
            learned_rules = profile.get("learned_rules") if isinstance(profile.get("learned_rules"), list) else []
            removed_rules = [
                item for item in learned_rules
                if isinstance(item, dict) and self._single_line(item.get("id"), 100) == rule_id
            ]
            kept = [
                item
                for item in learned_rules
                if not isinstance(item, dict) or self._single_line(item.get("id"), 100) != rule_id
            ]
            if len(kept) == len(learned_rules):
                return "没有找到归纳规则"
            archive_items("revoked_rules", removed_rules, "revoked")
            profile["learned_rules"] = kept
            profile["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            return "已删除归纳规则"
        if action == "delete_rule_group":
            if not rule_family_id:
                return "缺少规则组标识"
            learned_rules = profile.get("learned_rules") if isinstance(profile.get("learned_rules"), list) else []
            kept = [
                item
                for item in learned_rules
                if not isinstance(item, dict) or self._single_line(item.get("family_id"), 100) != rule_family_id
            ]
            removed = len(learned_rules) - len(kept)
            if removed <= 0:
                return "没有找到归纳规则组"
            archive_items("revoked_rules", [
                item for item in learned_rules
                if isinstance(item, dict) and self._single_line(item.get("family_id"), 100) == rule_family_id
            ], "revoked")
            profile["learned_rules"] = kept
            profile["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            return f"已删除规则组中的 {removed} 条规则"
        return "未知表达样本操作"
