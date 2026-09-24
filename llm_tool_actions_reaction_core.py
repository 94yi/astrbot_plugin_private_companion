# -*- coding: utf-8 -*-
"""表情表达核心域。

由 tools/split_mixin_domain.py 从 llm_tool_actions.py 机械抽取（26 个方法 + 7 个模块级名字 + 0 个类级赋值 / 1351 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 LlmToolActionsMixin）。
"""
from __future__ import annotations

import hashlib
import html
import json
import random
import re
import uuid
from .helpers import _now_ts, _safe_float, _safe_int, _single_line, _strip_internal_message_blocks
from .llm_tool_actions_shared import logger
from .owned_reaction_asset_catalog import OwnedReactionAssetCatalog
from .persona_config import runtime_persona_setting
from .reaction_expression import (
    ensure_reaction_expression_state,
    evaluate_reaction_expression_gate,
    normalize_reaction_expression_intent,
    reaction_expression_auto_disabled,
    reaction_expression_effective_probability,
    reaction_expression_explicit_opt_out,
    reaction_expression_explicit_request,
    reaction_expression_high_frequency,
    reaction_expression_normalize_probability,
    reaction_expression_scope_state,
)
from typing import Any



_REACTION_LOG_STAGES = frozenset(
    {
        "gate",
        "authorization",
        "decision",
        "lookup",
        "reservation",
        "attachment",
        "delivery",
        "intent",
        "feedback",
        "degrade",
    }
)

_REACTION_LOG_DECISIONS = frozenset(
    {
        "allow",
        "deny",
        "skip",
        "hit",
        "miss",
        "prepared",
        "sent",
        "failed",
        "uncertain",
        "accepted",
        "discarded",
        "recorded",
        "scoped",
        "omit",
    }
)

_REACTION_LOG_REASONS = frozenset(
    {
        "allowed",
        "vision_rejected",
        "experiment_disabled",
        "provider_unavailable",
        "private_disabled",
        "group_disabled",
        "unknown_disabled",
        "missing_user",
        "probability",
        "cooldown",
        "in_progress",
        "repeated_intent",
        "not_preauthorized",
        "not_authorized",
        "authorization_consumed",
        "authorization_expired",
        "authorization_user_mismatch",
        "authorization_scope_mismatch",
        "send_disabled",
        "missing_visible_text",
        "missing_visible_caption",
        "existing_image",
        "proactive_only",
        "gate",
        "not_found",
        "unavailable",
        "error",
        "lookup_error",
        "missing_query",
        "library_unavailable",
        "missing_file",
        "matched",
        "reservation_lost",
        "duplicate_image",
        "attachment_state_failed",
        "attachment_appended",
        "attachment_prepared",
        "delivered_before_primary",
        "attachment_file_missing",
        "attachment_component_failed",
        "attachment_removed",
        "platform_not_sent",
        "primary_not_delivered",
        "delivery_not_started",
        "awaiting_platform_send",
        "delivered",
        "delivery_uncertain",
        "delivery_failed",
        "append_failed",
        "not_sent",
        "scene_snapshot_failed",
        "usage_mark_failed",
        "intent_extracted",
        "intent_discarded",
        "model_omitted_intent",
        "local_fallback_intent",
        "media_tools_scoped",
        "feedback_private",
        "feedback_group",
        "explicit_opt_out",
        "semantic_cooldown",
    }
)

_REACTION_LOG_STATUSES = frozenset(
    {
        "success",
        "prepared",
        "need_query",
        "unavailable",
        "not_found",
        "error",
        "missing_file",
        "delivery_failed",
        "delivery_uncertain",
        "disabled",
        "missing_user",
        "not_sent",
    }
)

_REACTION_LOG_DELIVERY_CODES = frozenset(
    {
        "current",
        "group",
        "private",
        "blocked",
        "error",
        "platform_sent",
        "delivered",
        "append_failed",
        "attachment_removed",
        "platform_not_sent",
        "attachment_file_missing",
        "attachment_component_failed",
    }
)

_REACTION_LOG_MATCH_BASES = frozenset(
    {"tags_emotions_intents", "provider_score"}
)

_REACTION_LOG_TRIGGER_MODES = frozenset(
    {
        "probability",
        "feedback_bias",
        "semantic_rule",
        "strong_emotion",
        "explicit_opt_out",
    }
)


class LlmToolActionsReactionCoreMixin:
    """表情表达核心域（从 LlmToolActionsMixin 拆出）。"""


    def _find_owned_reaction_asset(
        self,
        query: str,
        *,
        search_context: str = "",
        meme_only: bool = True,
    ) -> dict[str, Any] | None:
        if not bool(runtime_persona_setting(self, 'enable_owned_reaction_asset_workbench', False)):
            return None
        catalog = OwnedReactionAssetCatalog(getattr(self, "data_dir", ""))
        asset, status, confidence = catalog.find(
            runtime_persona_setting(self, 'owned_reaction_assets', []),
            query=query,
            search_context=search_context,
            meme_only=bool(meme_only),
        )
        if asset is None:
            logger.debug(
                "Q6 自有反应图未命中: status=%s",
                status,
            )
            return None
        return {
            "success": True,
            "status": "success",
            "source": "owned_reaction_assets",
            "path": str(asset.path),
            "image_id": asset.asset_id,
            "tags": list(asset.tags),
            "need": _single_line(query, 220),
            "reason": "管理员登记的受管自有反应图标签命中",
            "confidence": confidence,
        }

    def _reaction_image_provider_available(self) -> bool:
        library = self._reaction_asset_library()
        return bool(
            library and library.has_enabled_assets()
        ) or bool(
            runtime_persona_setting(self, 'enable_owned_reaction_asset_workbench', False)
            and runtime_persona_setting(self, 'owned_reaction_assets', [])
        )

    @staticmethod
    def _reaction_expression_opt_out_requested(text: Any) -> bool:
        return reaction_expression_explicit_opt_out(text)

    @staticmethod
    def _reaction_expression_explicit_request_matches(text: Any) -> bool:
        return reaction_expression_explicit_request(text)

    def _reaction_expression_event_storage_id(self, event: Any, user_id: Any) -> str:
        """Resolve an event sender to the platform/account-scoped users key."""
        raw_id = _single_line(user_id, 160)
        if not raw_id:
            return ""
        # Callers may feed the already-resolved storage key back into a later
        # state step. Do not namespace that key a second time.
        try:
            event_sender = _single_line(event.get_sender_id(), 160)
        except Exception:
            event_sender = ""
        platform_getter = getattr(self, "_platform_kind_for_event", None)
        try:
            platform = _single_line(platform_getter(event), 40).lower() if callable(platform_getter) else ""
        except Exception:
            platform = ""
        if event_sender and raw_id != event_sender and platform and raw_id.startswith(f"{platform}:"):
            return raw_id
        resolver = getattr(self, "_private_user_id_for_event", None)
        if callable(resolver):
            try:
                resolved = _single_line(resolver(event, raw_id), 160)
            except Exception:
                resolved = ""
            if resolved:
                return resolved
        return raw_id

    def _reaction_expression_feedback_user(
        self,
        user_id: Any,
        text: Any,
        *,
        create_for_opt_out: bool = False,
        event: Any = None,
    ) -> dict[str, Any] | None:
        """Resolve the canonical user that owns per-conversation feedback."""
        normalized_id = _single_line(user_id, 160)
        if event is not None and self._reaction_expression_scope(event) == "group":
            return self._reaction_expression_state_owner(
                event,
                normalized_id,
                create=bool(create_for_opt_out and reaction_expression_explicit_opt_out(text)),
            )
        if event is not None:
            normalized_id = self._reaction_expression_event_storage_id(event, normalized_id)
        data = getattr(self, "data", None)
        users = data.get("users") if isinstance(data, dict) else None
        if not normalized_id or not isinstance(users, dict):
            return None

        canonical_id = normalized_id
        canonicalizer = getattr(self, "_canonical_private_user_id", None)
        if callable(canonicalizer):
            try:
                canonical_id = (
                    _single_line(canonicalizer(normalized_id), 160)
                    or normalized_id
                )
            except Exception:
                canonical_id = normalized_id

        for candidate_id in dict.fromkeys((normalized_id, canonical_id)):
            candidate = users.get(candidate_id)
            if isinstance(candidate, dict):
                return candidate
        for candidate in users.values():
            if not isinstance(candidate, dict):
                continue
            aliases = candidate.get("alias_user_ids")
            if (
                _single_line(candidate.get("user_id"), 160) == normalized_id
                or isinstance(aliases, list) and normalized_id in aliases
            ):
                return candidate

        if not (
            create_for_opt_out
            and reaction_expression_explicit_opt_out(text)
        ):
            return None
        getter = getattr(self, "_get_user", None)
        if not callable(getter):
            return None
        try:
            created = getter(canonical_id)
        except Exception:
            return None
        return created if isinstance(created, dict) else None

    def _mark_reaction_asset_used(
        self,
        image_id: Any,
        *,
        event: Any = None,
        trace_id: str = "",
    ) -> None:
        normalized = _single_line(image_id, 160)
        if not normalized.startswith("pc-local:"):
            return
        library = self._reaction_asset_library()
        if library is None:
            return
        try:
            library.mark_used(normalized)
        except Exception as exc:
            self._log_reaction_expression_event(
                event,
                trace_id=trace_id,
                stage="degrade",
                decision="failed",
                reason="usage_mark_failed",
                image_id=normalized,
                error_type=type(exc).__name__,
            )

    @staticmethod
    def _mark_private_companion_skip_reaction_expression(event: Any) -> None:
        """Mark this event after a real image delivery to avoid a second reaction image."""
        if event is None:
            return
        try:
            setattr(event, "_private_companion_skip_reaction_expression", True)
        except Exception:
            pass
        setter = getattr(event, "set_extra", None)
        if callable(setter):
            try:
                setter("private_companion_skip_reaction_expression", True)
            except Exception:
                pass

    def _reaction_expression_has_visible_text(self, value: Any) -> bool:
        """Require actual reply text before an experimental image may be attached."""
        text = _strip_internal_message_blocks(str(value or ""), enabled=bool(runtime_persona_setting(self, "enable_framework_error_leak_guard", True)))
        text = re.sub(r"<[^>]{1,240}>", "", text, flags=re.DOTALL)
        return bool(re.search(r"\w", text, flags=re.UNICODE))

    def _extract_reaction_expression_hidden_intent(
        self,
        value: Any,
    ) -> tuple[str, dict[str, Any]]:
        """Remove the internal expression tag and parse at most one valid intent."""
        source = str(value or "")
        if not source:
            return "", {}

        literal_open = r"(?:<|\\<)\s*pc_reaction_expression\s*(?:>|\\>)"
        literal_close = r"(?:<|\\<)\s*/\s*pc_reaction_expression\s*(?:>|\\>)"
        escaped_open = r"&lt;\s*pc_reaction_expression\s*&gt;"
        escaped_close = r"&lt;\s*/\s*pc_reaction_expression\s*&gt;"
        complete_pattern = re.compile(
            rf"(?:{literal_open}(.*?){literal_close}|{escaped_open}(.*?){escaped_close})",
            flags=re.IGNORECASE | re.DOTALL,
        )
        parsed_intent: dict[str, Any] = {}

        def parse_payload(payload: str) -> dict[str, Any]:
            candidates = [str(payload or "").strip()]
            unescaped = html.unescape(candidates[0]).strip()
            if unescaped and unescaped not in candidates:
                candidates.append(unescaped)
            for candidate in list(candidates):
                if "\\\"" in candidate:
                    candidates.append(candidate.replace("\\\"", '"'))
            for candidate in candidates:
                if not candidate:
                    continue
                try:
                    payload_obj: Any = json.loads(candidate)
                    if isinstance(payload_obj, str):
                        payload_obj = json.loads(payload_obj)
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
                if not isinstance(payload_obj, dict):
                    continue
                raw_queries = payload_obj.get("candidate_queries")
                normalized = normalize_reaction_expression_intent(
                    query=payload_obj.get("query", ""),
                    context=payload_obj.get("context", ""),
                    purpose=payload_obj.get("purpose", ""),
                    emotion=payload_obj.get("emotion", ""),
                    intensity=payload_obj.get("intensity", 0),
                    candidate_queries=raw_queries,
                    candidate_limit=_safe_int(
                        runtime_persona_setting(self, 'reaction_expression_candidate_limit', 6),
                        6,
                        1,
                        16,
                    ),
                )
                if self._reaction_expression_bool_arg(payload_obj.get("sticker_only"), False):
                    normalized["sticker_only"] = True
                meaningful = any(
                    str(payload_obj.get(key) or "").strip()
                    for key in ("query", "purpose", "emotion")
                ) or bool(normalized.get("candidate_queries"))
                if not meaningful:
                    continue
                return normalized
            return {}

        def remove_complete(match: re.Match[str]) -> str:
            nonlocal parsed_intent
            if not parsed_intent:
                parsed_intent = parse_payload(match.group(1) or match.group(2) or "")
            return ""

        cleaned = complete_pattern.sub(remove_complete, source)
        # A malformed or truncated internal tag must never become visible chat text.
        cleaned = re.sub(literal_close, "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(escaped_close, "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(
            r"(?:<|\\<|&lt;)\s*/?\s*pc[_-]?reaction.*$",
            "",
            cleaned,
            flags=re.IGNORECASE | re.DOTALL,
        )
        cleaned = re.sub(r"[ \t]+(?=\r?$)", "", cleaned, flags=re.MULTILINE)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
        return cleaned, parsed_intent

    @staticmethod
    def _reaction_expression_bool_arg(value: Any, default: bool) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        normalized = str(value).strip().lower()
        if normalized in {"1", "true", "yes", "on", "是", "发送"}:
            return True
        if normalized in {"0", "false", "no", "off", "否", "不发送"}:
            return False
        return default

    @staticmethod
    def _reaction_expression_scope(event: Any) -> str:
        checker = getattr(event, "is_private_chat", None)
        if callable(checker):
            try:
                if bool(checker()):
                    return "private"
            except Exception:
                pass
        origin = str(getattr(event, "unified_msg_origin", "") or "")
        if ":FriendMessage:" in origin:
            return "private"
        if ":GroupMessage:" in origin:
            return "group"
        return "group" if callable(checker) else "unknown"

    @classmethod
    def _reaction_expression_scope_key(cls, event: Any, user_id: str = "") -> str:
        origin = _single_line(getattr(event, "unified_msg_origin", ""), 240)
        if origin:
            return origin
        scope = cls._reaction_expression_scope(event)
        return f"{scope}:{_single_line(user_id, 160) or 'unknown'}"

    def _reaction_expression_state_owner(
        self,
        event: Any,
        user_id: Any,
        *,
        create: bool = True,
        scope: str = "",
        scope_key: str = "",
    ) -> dict[str, Any] | None:
        """Return the state owner without turning group senders into private users."""
        normalized_id = _single_line(user_id, 160)
        if not normalized_id:
            return None
        resolved_scope = _single_line(scope, 16).casefold()
        if not resolved_scope:
            resolved_scope = self._reaction_expression_scope(event) if event is not None else "private"
        if resolved_scope != "group":
            if event is not None:
                normalized_id = self._reaction_expression_event_storage_id(event, normalized_id)
            getter = getattr(self, "_get_user", None)
            if not callable(getter):
                return None
            try:
                owner = getter(normalized_id)
            except Exception:
                return None
            return owner if isinstance(owner, dict) else None

        data = getattr(self, "data", None)
        if not isinstance(data, dict):
            return None
        resolved_scope_key = _single_line(scope_key, 240)
        if not resolved_scope_key:
            resolved_scope_key = self._reaction_expression_scope_key(event, normalized_id)
        state_key = _single_line(f"{resolved_scope_key}|sender:{normalized_id}", 420)
        if not state_key:
            return None
        states = data.get("reaction_expression_group_states")
        if not isinstance(states, dict):
            if not create:
                return None
            states = {}
            data["reaction_expression_group_states"] = states
        owner = states.get(state_key)
        if not isinstance(owner, dict):
            if not create:
                return None
            owner = {}
            states[state_key] = owner
        return owner

    @staticmethod
    def _reaction_expression_authorization(event: Any) -> dict[str, Any]:
        raw = getattr(
            event,
            "_private_companion_reaction_expression_authorization",
            None,
        )
        if isinstance(raw, dict):
            return raw
        getter = getattr(event, "get_extra", None)
        if callable(getter):
            try:
                raw = getter("private_companion_reaction_expression_authorization")
            except Exception:
                raw = None
        if not isinstance(raw, dict):
            extras = getattr(event, "extras", None)
            raw = (
                extras.get("private_companion_reaction_expression_authorization")
                if isinstance(extras, dict)
                else None
            )
        return raw if isinstance(raw, dict) else {}

    @staticmethod
    def _set_reaction_expression_authorization(
        event: Any, authorization: dict[str, Any]
    ) -> None:
        try:
            setattr(
                event,
                "_private_companion_reaction_expression_authorization",
                authorization,
            )
        except Exception:
            pass
        setter = getattr(event, "set_extra", None)
        if callable(setter):
            try:
                setter(
                    "private_companion_reaction_expression_authorization",
                    authorization,
                )
            except Exception:
                pass

    def _reaction_expression_trace_id(self, event: Any) -> str:
        authorization = self._reaction_expression_authorization(event)
        trace_id = _single_line(
            authorization.get("trace_id") or authorization.get("nonce"),
            12,
        ).casefold()
        if not re.fullmatch(r"[0-9a-f]{12}", trace_id):
            trace_id = _single_line(
                getattr(
                    event,
                    "_private_companion_reaction_expression_trace_id",
                    "",
                ),
                12,
            ).casefold()
        if not re.fullmatch(r"[0-9a-f]{12}", trace_id):
            trace_id = uuid.uuid4().hex[:12]
            try:
                setattr(
                    event,
                    "_private_companion_reaction_expression_trace_id",
                    trace_id,
                )
            except Exception:
                pass
        return trace_id

    def _log_reaction_expression_event(
        self,
        event: Any,
        *,
        stage: str,
        decision: str,
        trace_id: Any = "",
        reason: Any = "",
        scope: Any = "",
        status: Any = "",
        found: bool | None = None,
        sent: bool | None = None,
        image_id: Any = "",
        confidence: Any = None,
        cache_hit: bool | None = None,
        latency_ms: Any = None,
        delivery: Any = "",
        match_basis: Any = "",
        error_type: Any = "",
        feedback_signal: Any = "",
        feedback_score: Any = None,
        trigger_mode: Any = "",
        trigger_confidence: Any = None,
        configured_probability: Any = None,
        effective_probability: Any = None,
        cooldown_seconds: Any = None,
    ) -> None:
        """Write one privacy-safe, correlation-friendly reaction runtime event."""

        def safe_code(value: Any, allowed: frozenset[str]) -> str:
            normalized = _single_line(value, 80).casefold()
            if not normalized:
                return ""
            return normalized if normalized in allowed else "other"

        try:
            normalized_trace_id = _single_line(trace_id, 12).casefold()
            if not re.fullmatch(r"[0-9a-f]{12}", normalized_trace_id):
                normalized_trace_id = self._reaction_expression_trace_id(event)
            payload: dict[str, Any] = {
                "trace_id": normalized_trace_id,
                "stage": safe_code(stage, _REACTION_LOG_STAGES) or "decision",
                "decision": safe_code(decision, _REACTION_LOG_DECISIONS) or "skip",
            }
            for key, value, allowed in (
                ("status", status, _REACTION_LOG_STATUSES),
                ("reason", reason, _REACTION_LOG_REASONS),
                ("delivery", delivery, _REACTION_LOG_DELIVERY_CODES),
                ("match_basis", match_basis, _REACTION_LOG_MATCH_BASES),
            ):
                normalized = safe_code(value, allowed)
                if normalized:
                    payload[key] = normalized
            normalized_scope = safe_code(
                scope,
                frozenset({"private", "group", "unknown"}),
            )
            if normalized_scope:
                payload["scope"] = normalized_scope
            normalized_image_id = _single_line(image_id, 160)
            if normalized_image_id:
                if re.fullmatch(
                    r"pc-local:[0-9a-f]{16,64}",
                    normalized_image_id,
                    flags=re.I,
                ):
                    payload["asset_ref"] = normalized_image_id
                else:
                    payload["asset_ref"] = hashlib.sha256(
                        normalized_image_id.encode("utf-8", errors="replace")
                    ).hexdigest()[:12]
            normalized_error_type = _single_line(error_type, 80)
            if normalized_error_type and re.fullmatch(
                r"[A-Za-z_][A-Za-z0-9_.]{0,79}",
                normalized_error_type,
            ):
                payload["error_type"] = normalized_error_type
            normalized_feedback_signal = safe_code(
                feedback_signal,
                frozenset({"positive", "negative", "neutral"}),
            )
            if normalized_feedback_signal:
                payload["feedback_signal"] = normalized_feedback_signal
            normalized_trigger_mode = safe_code(
                trigger_mode,
                _REACTION_LOG_TRIGGER_MODES,
            )
            if normalized_trigger_mode:
                payload["trigger_mode"] = normalized_trigger_mode
            for key, value in (
                ("found", found),
                ("sent", sent),
                ("cache_hit", cache_hit),
            ):
                if value is not None:
                    payload[key] = bool(value)
            if feedback_score is not None:
                payload["feedback_score"] = max(
                    -20,
                    min(20, _safe_int(feedback_score, 0, -20, 20)),
                )
            for key, value, maximum, digits in (
                ("confidence", confidence, 1.0, 3),
                ("latency_ms", latency_ms, 3_600_000.0, 2),
                ("configured_probability", configured_probability, 1.0, 4),
                ("effective_probability", effective_probability, 1.0, 4),
                ("cooldown_seconds", cooldown_seconds, 86_400.0, 2),
            ):
                if value is not None:
                    payload[key] = round(
                        _safe_float(value, 0.0, 0.0, maximum),
                        digits,
                    )
            logger.info(
                "[ReactionExpression] %s",
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            )
        except Exception:
            # Diagnostics must never alter the reply or delivery path.
            return

    @staticmethod
    def _reaction_expression_match_basis(lookup: Any) -> str:
        if not isinstance(lookup, dict):
            return ""
        provider = _single_line(lookup.get("provider"), 80)
        image_id = _single_line(lookup.get("image_id"), 160)
        if provider == "private_companion_library" or image_id.startswith("pc-local:"):
            return "tags_emotions_intents"
        return "provider_score" if lookup.get("success") else ""

    def _reaction_expression_local_trigger(
        self,
        event: Any,
        user: dict[str, Any],
        *,
        configured_probability: float,
        scope_key: str = "",
    ) -> dict[str, Any]:
        """Resolve a local trigger layer without another model pass.

        The inbound pipeline already maintains a small intent/emotion profile.
        Reuse only high-confidence, non-boundary signals here.  A configured
        probability of zero remains an explicit opt-out; cooldown and duplicate
        protection are still enforced by ``evaluate_reaction_expression_gate``.
        """
        base_probability = reaction_expression_normalize_probability(
            configured_probability,
            0.2,
        )
        default = {
            "mode": "probability",
            "reason": "random_offer",
            "source": "configured_probability",
            "confidence": 0.0,
            "bypass_probability": False,
        }
        if not isinstance(user, dict):
            return default
        inbound_text = ""
        try:
            inbound_text = _single_line(getattr(event, "message_str", ""), 500)
        except Exception:
            inbound_text = ""
        if reaction_expression_explicit_opt_out(inbound_text):
            return {
                "mode": "explicit_opt_out",
                "reason": "explicit_opt_out",
                "source": "user_message",
                "confidence": 1.0,
                "bypass_probability": False,
            }
        state = ensure_reaction_expression_state(user)
        # Keep an explicit user boundary across turns. A direct request for a
        # particular reaction image is still allowed and handled by the tool
        # path; it does not silently re-enable automatic attachments.
        auto_disabled = reaction_expression_auto_disabled(state, scope_key)
        if auto_disabled and not reaction_expression_explicit_request(inbound_text):
            return {
                "mode": "explicit_opt_out",
                "reason": "explicit_opt_out_persisted",
                "source": "user_preference",
                "confidence": 1.0,
                "bypass_probability": False,
            }
        if auto_disabled and reaction_expression_explicit_request(inbound_text):
            # Explicit requests use the ordinary tool path for this turn but
            # do not erase the persisted automatic-attachment boundary.
            return {
                "mode": "explicit_request",
                "reason": "explicit_request",
                "source": "user_message",
                "confidence": 1.0,
                "bypass_probability": False,
            }
        if base_probability <= 0:
            return default
        if not bool(runtime_persona_setting(self, 'reaction_expression_semantic_trigger_enabled', True)):
            return default

        profile = user.get("intent_profile")
        if not isinstance(profile, dict) or not profile:
            return default
        profile_text = re.sub(r"\s+", "", _single_line(profile.get("text"), 500))
        current_text = re.sub(r"\s+", "", inbound_text)
        if profile_text and (not current_text or profile_text != current_text):
            return default
        intent = _single_line(profile.get("intent"), 32).casefold()
        emotion_event = _single_line(profile.get("emotion_event"), 40).casefold()
        emotion_target = _single_line(profile.get("emotion_target"), 40).casefold()
        source = _single_line(profile.get("source"), 48).casefold()
        confidence = max(
            _safe_float(profile.get("confidence"), 0.0, 0.0, 1.0),
            _safe_float(profile.get("emotion_confidence"), 0.0, 0.0, 1.0),
        )
        intensity = _safe_float(profile.get("emotion_intensity"), 0.0, 0.0, 100.0)
        profile_snapshot = {
            "intent": intent,
            "emotion_event": emotion_event,
            "emotion_target": emotion_target,
            "emotion_intensity": intensity,
            "confidence": _safe_float(
                profile.get("confidence"), 0.0, 0.0, 1.0
            ),
            "emotion_confidence": _safe_float(
                profile.get("emotion_confidence"), 0.0, 0.0, 1.0
            ),
            "source": source,
            "boundary_durable": bool(profile.get("boundary_durable")),
            "text": _single_line(profile.get("text"), 240),
        }
        semantic_bypass_blocked = bool(
            profile_snapshot["boundary_durable"]
            or intent in {"boundary", "help", "task", "code", "search", "empty"}
            or source
            in {
                "diagnostic_skip",
                "durable_boundary_rule",
                "single_turn_boundary",
                "weak_boundary_ignored",
            }
            or emotion_event in {"hurt", "external_negative"}
        )
        preference = state.get("preference")
        preference_score = (
            _safe_int(preference.get("score"), 0, -20, 20)
            if isinstance(preference, dict)
            else 0
        )

        # A negative reaction history should not be overridden by a semantic
        # shortcut.  It still participates in the ordinary probability path.
        if preference_score < 0:
            return {
                "mode": "feedback_bias",
                "reason": "negative_feedback_respect",
                "source": "feedback_preference",
                "confidence": 0.0,
                "bypass_probability": False,
                "profile_snapshot": profile_snapshot,
            }

        # These are existing local classifier outcomes, not a second model
        # judgement.  Deliberately exclude hurt/boundary/diagnostic signals so
        # a tense conversation does not receive an unwanted reaction image.
        positive_events = {"comfort_need", "comfort", "praise", "apology"}
        positive_intents = {"play", "intimacy"}
        target_allows_event = emotion_target in {"", "self", "bot"}
        if emotion_event == "praise" and emotion_target not in {"", "bot"}:
            target_allows_event = False
        strong_event = (
            emotion_event in positive_events
            and target_allows_event
            and intensity >= 38
            and confidence >= 0.62
        )
        strong_intent = intent in positive_intents and confidence >= 0.72
        if not semantic_bypass_blocked and (strong_event or strong_intent):
            return {
                "mode": "strong_emotion" if strong_event else "semantic_rule",
                "reason": "local_emotion_signal" if strong_event else "local_intent_signal",
                "source": source or ("emotion_event" if strong_event else "intent"),
                "confidence": round(confidence, 3),
                "bypass_probability": True,
                "profile_snapshot": profile_snapshot,
            }

        if preference_score > 0:
            return {
                "mode": "feedback_bias",
                "reason": "positive_feedback_bias",
                "source": "feedback_preference",
                "confidence": min(1.0, preference_score / 10.0),
                "bypass_probability": False,
                "profile_snapshot": profile_snapshot,
            }
        default["profile_snapshot"] = profile_snapshot
        return default

    def _reaction_expression_local_fallback_intent(
        self,
        event: Any,
        visible_text: Any,
        authorization: dict[str, Any],
    ) -> dict[str, Any]:
        """Build a conservative intent when the model omits the hidden tag.

        This reuses the inbound classifier state that already authorized the
        opportunity. Normal rates stay limited to high-confidence social or
        emotional turns; the explicit 100% mode also covers a plain social
        reply when the model omitted its optional tag.
        """
        if not isinstance(authorization, dict) or not authorization.get("authorized"):
            return {}
        if authorization.get("consumed"):
            return {}
        trigger_mode = _single_line(authorization.get("trigger_mode"), 40).casefold()
        high_frequency = bool(authorization.get("high_frequency_mode")) or reaction_expression_high_frequency(
            authorization.get(
                "configured_probability",
                runtime_persona_setting(self, 'reaction_expression_trigger_probability', 0.2),
            )
        )
        allowed_modes = {"semantic_rule", "strong_emotion"}
        if high_frequency:
            # At 100% the probability gate has already granted the opportunity;
            # do not make delivery depend on the model remembering an optional
            # hidden tag. Boundary and feedback checks below still apply.
            allowed_modes.add("probability")
        if trigger_mode not in allowed_modes:
            return {}
        try:
            user_id = _single_line(
                authorization.get("user_id")
                or getattr(event, "get_sender_id", lambda: "")(),
                160,
            )
        except Exception:
            user_id = ""
        if not user_id:
            return {}
        profile = authorization.get("profile_snapshot")
        if not isinstance(profile, dict) or not profile:
            user = self._reaction_expression_state_owner(
                event,
                user_id,
                create=False,
            )
            if not isinstance(user, dict):
                user = None
            profile = user.get("intent_profile") if isinstance(user, dict) else None
        if not isinstance(profile, dict) or not profile:
            if not high_frequency:
                return {}
            context_text = _single_line(visible_text, 700)
            if not self._reaction_expression_has_visible_text(context_text):
                return {}
            return normalize_reaction_expression_intent(
                query="开心回应",
                context=context_text,
                purpose="日常回应",
                emotion="开心",
                intensity=2,
                candidate_queries=["开心回应", "轻松互动", "日常分享"],
                candidate_limit=_safe_int(
                    runtime_persona_setting(self, 'reaction_expression_candidate_limit', 6),
                    6,
                    1,
                    16,
                ),
            )
        profile_text = re.sub(r"\s+", "", _single_line(profile.get("text"), 500))
        current_text = re.sub(
            r"\s+",
            "",
            _single_line(getattr(event, "message_str", ""), 500),
        )
        if profile_text and (not current_text or profile_text != current_text):
            return {}
        intent_name = _single_line(profile.get("intent"), 32).casefold()
        emotion_event = _single_line(profile.get("emotion_event"), 40).casefold()
        emotion_target = _single_line(profile.get("emotion_target"), 40).casefold()
        confidence = max(
            _safe_float(profile.get("confidence"), 0.0, 0.0, 1.0),
            _safe_float(profile.get("emotion_confidence"), 0.0, 0.0, 1.0),
        )
        intensity = _safe_float(profile.get("emotion_intensity"), 0.0, 0.0, 100.0)
        confidence_floor = 0.62 if high_frequency else 0.72
        if confidence < confidence_floor or bool(profile.get("boundary_durable")):
            return {}
        if emotion_target not in {"", "self", "bot"}:
            return {}

        presets: dict[str, tuple[str, str, list[str]]] = {
            "play": ("接住玩笑", "轻松", ["轻松接梗", "开心吐槽", "无语摊手"]),
            "intimacy": ("回应亲近", "亲昵", ["害羞亲近", "撒娇回应", "温柔陪伴"]),
            "comfort": ("温柔安慰", "心疼", ["安慰陪伴", "温柔抱抱", "心疼安慰"]),
        }
        event_presets: dict[str, tuple[str, str, list[str]]] = {
            "praise": ("回应夸奖", "开心", ["开心被夸", "害羞开心", "收到夸奖"]),
            "apology": ("温和回应道歉", "温柔", ["温柔原谅", "轻轻安慰", "没关系"]),
            "comfort_need": ("接住低落", "温柔", ["安慰陪伴", "抱抱安慰", "温柔鼓励"]),
        }
        if emotion_event == "comfort" and emotion_target in {"", "bot"}:
            preset = (
                "回应安抚",
                "安心",
                ["被安慰后安心", "收到安抚", "温柔回应关心"],
            )
        else:
            preset = event_presets.get(emotion_event) or presets.get(intent_name)
            if not preset and high_frequency and intent_name in {
                "chat",
                "social",
                "conversation",
                "greeting",
            }:
                preset = (
                    "日常回应",
                    "开心",
                    ["开心回应", "轻松互动", "日常分享"],
                )
        if not preset:
            return {}
        purpose_text, emotion_text, candidates = preset
        level = max(0, min(5, int(round(intensity / 20.0))))
        if level <= 0:
            level = 2 if trigger_mode == "semantic_rule" else 3
        context_text = _single_line(visible_text, 700)
        inbound_text = _single_line(profile.get("text"), 240)
        if inbound_text and inbound_text not in context_text:
            context_text = _single_line(
                f"用户语境：{inbound_text}；回复正文：{context_text}",
                1000,
            )
        return normalize_reaction_expression_intent(
            query=candidates[0],
            context=context_text,
            purpose=purpose_text,
            emotion=emotion_text,
            intensity=level,
            candidate_queries=candidates,
            candidate_limit=_safe_int(
                runtime_persona_setting(self, 'reaction_expression_candidate_limit', 6),
                6,
                1,
                16,
            ),
        )

    async def _preauthorize_reaction_expression_prompt(
        self, event: Any
    ) -> bool:
        now = _now_ts()
        existing = self._reaction_expression_authorization(event)
        if existing:
            if now > _safe_float(existing.get("expires_at"), 0.0):
                self._log_reaction_expression_event(
                    event,
                    stage="gate",
                    decision="deny",
                    reason="authorization_expired",
                    scope=existing.get("scope") or self._reaction_expression_scope(event),
                )
                return False
            return bool(existing.get("authorized") and not existing.get("consumed"))
        scope = self._reaction_expression_scope(event)
        authorization: dict[str, Any] = {
            "authorized": False,
            "reason": "experiment_disabled",
            "authorized_at": now,
            "expires_at": now + 600.0,
            "consumed": False,
            "model_omission_recorded": False,
            "scope": scope,
            "trace_id": self._reaction_expression_trace_id(event),
        }
        if not bool(runtime_persona_setting(self, 'enable_reaction_expression_experiment', False)):
            self._set_reaction_expression_authorization(event, authorization)
            self._log_reaction_expression_event(
                event,
                stage="gate",
                decision="deny",
                reason=authorization["reason"],
                scope=scope,
            )
            return False
        if not self._reaction_image_provider_available():
            authorization["reason"] = "provider_unavailable"
            self._set_reaction_expression_authorization(event, authorization)
            self._log_reaction_expression_event(
                event,
                stage="gate",
                decision="deny",
                reason=authorization["reason"],
                scope=scope,
            )
            return False

        allowed = (
            bool(runtime_persona_setting(self, 'reaction_expression_private_enabled', True))
            if scope == "private"
            else bool(runtime_persona_setting(self, 'reaction_expression_group_enabled', False))
            if scope == "group"
            else False
        )
        if not allowed:
            authorization["reason"] = f"{scope}_disabled"
            self._set_reaction_expression_authorization(event, authorization)
            self._log_reaction_expression_event(
                event,
                stage="gate",
                decision="deny",
                reason=authorization["reason"],
                scope=scope,
            )
            return False
        try:
            user_id = _single_line(event.get_sender_id(), 160)
        except Exception:
            user_id = ""
        if scope == "private" and user_id:
            user_id = self._reaction_expression_event_storage_id(event, user_id)
        if not user_id:
            authorization["reason"] = "missing_user"
            self._set_reaction_expression_authorization(event, authorization)
            self._log_reaction_expression_event(
                event,
                stage="gate",
                decision="deny",
                reason=authorization["reason"],
                scope=scope,
            )
            return False

        scope_key = self._reaction_expression_scope_key(event, user_id)
        configured_probability = reaction_expression_normalize_probability(
            runtime_persona_setting(self, 'reaction_expression_trigger_probability', 0.2),
            0.2,
        )
        cooldown = _safe_float(
            runtime_persona_setting(self, 'reaction_expression_cooldown_seconds', 180),
            180.0,
            0.0,
            86400.0,
        )
        async with self._data_lock:
            user = self._reaction_expression_state_owner(event, user_id)
            if not isinstance(user, dict):
                return False
            state = ensure_reaction_expression_state(user)
            scoped_state = reaction_expression_scope_state(state, scope_key)
            probability = reaction_expression_effective_probability(
                state, configured_probability
            )
            swing_probability = getattr(self, "_swing_probability", None)
            if callable(swing_probability):
                probability = swing_probability(probability, user=user)
            trigger = self._reaction_expression_local_trigger(
                event,
                user,
                configured_probability=configured_probability,
                scope_key=scope_key,
            )
            gate_probability = (
                1.0 if bool(trigger.get("bypass_probability")) else probability
            )
            if trigger.get("mode") == "explicit_opt_out":
                gate = {"allowed": False, "reason": "explicit_opt_out", "probability": gate_probability}
            else:
                semantic_offer_cooldown = (
                    min(60.0, cooldown) if cooldown > 0 and trigger.get("bypass_probability") else 0.0
                )
                last_offer_at = _safe_float(scoped_state.get("last_offer_at"), 0.0)
                if (
                    semantic_offer_cooldown > 0
                    and last_offer_at > 0
                    and now - last_offer_at < semantic_offer_cooldown
                ):
                    gate = {
                        "allowed": False,
                        "reason": "semantic_cooldown",
                        "probability": gate_probability,
                    }
                else:
                    gate = evaluate_reaction_expression_gate(
                        scoped_state,
                        {"signature": ""},
                        now=now,
                        probability=gate_probability,
                        cooldown_seconds=cooldown,
                        # Avoid drawing random state for deterministic semantic tiers;
                        # this keeps ordinary probability behavior and testability
                        # unchanged while making the bypass explicit in diagnostics.
                        random_value=random.random() if gate_probability < 1.0 else 0.0,
                    )
        authorization.update(
            {
                "authorized": bool(gate.get("allowed")),
                "reason": _single_line(gate.get("reason"), 80) or "gate",
                "user_id": user_id,
                "scope": scope,
                "scope_key": scope_key,
                "nonce": uuid.uuid4().hex,
                "configured_probability": configured_probability,
                "effective_probability": probability,
                "high_frequency_mode": reaction_expression_high_frequency(
                    configured_probability
                ),
                "gate_probability": gate_probability,
                "trigger_mode": _single_line(trigger.get("mode"), 40) or "probability",
                "trigger_reason": _single_line(trigger.get("reason"), 80),
                "trigger_source": _single_line(trigger.get("source"), 80),
                "trigger_confidence": _safe_float(
                    trigger.get("confidence"), 0.0, 0.0, 1.0
                ),
                "profile_snapshot": (
                    dict(trigger.get("profile_snapshot"))
                    if isinstance(trigger.get("profile_snapshot"), dict)
                    else {}
                ),
            }
        )
        self._set_reaction_expression_authorization(event, authorization)
        if authorization["authorized"]:
            if authorization.get("trigger_mode") in {"semantic_rule", "strong_emotion"}:
                async with self._data_lock:
                    user = self._reaction_expression_state_owner(event, user_id)
                    if not isinstance(user, dict):
                        return bool(authorization["authorized"])
                    state = ensure_reaction_expression_state(user)
                    reaction_expression_scope_state(state, scope_key)["last_offer_at"] = now
                    self._persist_reaction_expression_state(
                        sections={"reaction_expression_group_states"}
                        if scope == "group"
                        else {"users"}
                    )
            self._note_reaction_expression_runtime(offers=1, last_reason="offered")
        self._note_reaction_expression_runtime(
            trigger_mode=authorization.get("trigger_mode"),
            last_reason=authorization.get("trigger_reason") or authorization.get("reason"),
        )
        self._log_reaction_expression_event(
            event,
            stage="gate",
            decision="allow" if authorization["authorized"] else "deny",
            reason=authorization["reason"],
            scope=scope,
            configured_probability=configured_probability,
            effective_probability=gate_probability,
            cooldown_seconds=cooldown,
            trigger_mode=trigger.get("mode"),
            trigger_confidence=trigger.get("confidence"),
        )
        return bool(authorization["authorized"])

    def _consume_reaction_expression_authorization(
        self,
        event: Any,
        *,
        user_id: str,
        scope_key: str,
    ) -> tuple[bool, str]:
        authorization = self._reaction_expression_authorization(event)
        if not authorization:
            return False, "not_preauthorized"
        reason = _single_line(authorization.get("reason"), 80) or "not_preauthorized"
        if not authorization.get("authorized"):
            return False, reason
        if authorization.get("consumed"):
            return False, "authorization_consumed"
        if _now_ts() > _safe_float(authorization.get("expires_at"), 0.0):
            return False, "authorization_expired"
        if _single_line(authorization.get("user_id"), 160) != user_id:
            return False, "authorization_user_mismatch"
        if _single_line(authorization.get("scope_key"), 240) != scope_key:
            return False, "authorization_scope_mismatch"
        authorization["consumed"] = True
        self._set_reaction_expression_authorization(event, authorization)
        return True, "authorized"

    def _reaction_expression_skip_result(
        self,
        reason: str,
        *,
        event: Any = None,
        stage: str = "decision",
        scope: str = "",
        message: str = "本轮不使用表情表达，继续自然文字回复即可",
        **extra: Any,
    ) -> dict[str, Any]:
        self._note_reaction_expression_runtime(skipped=1, last_reason=reason)
        payload: dict[str, Any] = {
            "status": "skipped",
            "success": True,
            "found": False,
            "sent": False,
            "experimental": True,
            "decision": "skip",
            "skip_reason": _single_line(reason, 80),
            "message": _single_line(message, 240),
            "must_not_claim_sent": True,
            "final_response_instruction": "无需向用户解释跳过原因，按原语境继续自然文字回复。",
        }
        payload.update(extra)
        self._log_reaction_expression_event(
            event,
            stage=stage,
            decision="skip",
            reason=reason,
            scope=scope or (self._reaction_expression_scope(event) if event is not None else ""),
            found=bool(payload.get("found")),
            sent=False,
            image_id=payload.get("image_id"),
            confidence=payload.get("confidence") if "confidence" in payload else None,
            cache_hit=payload.get("cache_hit") if "cache_hit" in payload else None,
            latency_ms=(
                payload.get("lookup_latency_ms")
                if "lookup_latency_ms" in payload
                else None
            ),
            delivery=payload.get("delivery"),
        )
        return payload

    def _note_reaction_expression_runtime(
        self,
        *,
        attempts: int = 0,
        offers: int = 0,
        model_omissions: int = 0,
        local_fallbacks: int = 0,
        lookups: int = 0,
        cache_hits: int = 0,
        sent: int = 0,
        skipped: int = 0,
        last_reason: str = "",
        trigger_mode: Any = "",
        latency_ms: float | None = None,
        lookup_elapsed_ms: float = 0.0,
    ) -> None:
        runtime = getattr(self, "_reaction_expression_runtime", None)
        if not isinstance(runtime, dict):
            runtime = {}
            setattr(self, "_reaction_expression_runtime", runtime)
        for key, increment in (
            ("attempts", attempts),
            ("offers", offers),
            ("model_omissions", model_omissions),
            ("local_fallbacks", local_fallbacks),
            ("lookups", lookups),
            ("cache_hits", cache_hits),
            ("sent", sent),
            ("skipped", skipped),
        ):
            if increment:
                runtime[key] = max(0, _safe_int(runtime.get(key), 0)) + max(
                    0, _safe_int(increment, 0)
                )
        if lookup_elapsed_ms > 0:
            runtime["total_lookup_ms"] = round(
                max(0.0, _safe_float(runtime.get("total_lookup_ms"), 0.0))
                + max(0.0, _safe_float(lookup_elapsed_ms, 0.0)),
                2,
            )
        if latency_ms is not None:
            runtime["last_latency_ms"] = round(
                max(0.0, _safe_float(latency_ms, 0.0)), 2
            )
        if last_reason:
            runtime["last_reason"] = _single_line(last_reason, 120)
        normalized_trigger_mode = _single_line(trigger_mode, 40).casefold()
        if normalized_trigger_mode in _REACTION_LOG_TRIGGER_MODES:
            trigger_counts = runtime.get("trigger_modes")
            if not isinstance(trigger_counts, dict):
                trigger_counts = {}
                runtime["trigger_modes"] = trigger_counts
            trigger_counts[normalized_trigger_mode] = (
                max(0, _safe_int(trigger_counts.get(normalized_trigger_mode), 0)) + 1
            )
        runtime["last_at"] = _now_ts()

    def _persist_reaction_expression_state(
        self,
        *,
        sections: set[str] | None = None,
    ) -> None:
        scheduler = getattr(self, "_schedule_data_save", None)
        if callable(scheduler):
            try:
                scheduler(sections=sections)
                return
            except Exception:
                pass
        saver = getattr(self, "_save_data_sync", None)
        if callable(saver):
            requested = sections or {"users", "reaction_expression_group_states"}
            try:
                saver(sections=requested)
            except TypeError:
                saver()

