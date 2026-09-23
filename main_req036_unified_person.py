# -*- coding: utf-8 -*-
"""REQ036 统一人格域。

由 tools/split_main_domain.py 从 main.py 机械抽取（33 个方法 / 1525 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPlugin）。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import time
from .companion_interaction_expression import build_expression_decision, content_intent_from_text, expression_decision_prompt_section
from .context_orchestration import build_context, project_context
from .conversation_injection_plan import PLACEMENT_DYNAMIC_SYSTEM
from .domains.affect.reply_temperature import compose_reply_temperature, reply_temperature_prompt_section
from .helpers import _now_ts, _safe_float, _safe_int, _single_line
from .identity_namespace import NamespaceContext
from .main_shared import _multi_persona_event_context
from .message_pipeline import event_data_save_boundary
from .p4_affinity_confinement import apply_legacy_relationship_delta
from .p4_live_runtime import decide_live_request
from .p4_runtime_gate import SAFE_CONFINEMENT_REPLY
from .p4_shadow import build_p4_shadow
from .person_context_contract import (
    CONTRACT_NAME as PERSON_CONTRACT_NAME,
    CONTRACT_VERSION as PERSON_CONTRACT_VERSION,
    P3_CONTRACT_NAME,
    P3_CONTRACT_VERSION,
    build_identity_key,
)
from .persona_config import runtime_persona_setting
from .plugin_identity import PLUGIN_ID
from .unified_person_registry import UnifiedPersonRegistry
from .unified_profile_contract import (
    build_person_ref as req036_build_person_ref,
    build_portrait_request as req036_build_portrait_request,
    build_profile_dto as req036_build_profile_dto,
    validate_profile_dto as req036_validate_profile_dto,
)
from .unified_profile_service import (
    DEFAULT_UNAUTHORIZED_PRIVATE_REPLY,
    capability_summary as req036_capability_summary,
    private_companion_gate as req036_private_companion_gate,
    proactive_private_gate as req036_proactive_private_gate,
    update_capabilities as req036_update_capabilities,
)
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest
from typing import Any

from .logging_util import get_module_logger

logger = get_module_logger(__name__)

class PrivateCompanionPluginReq036UnifiedPersonMixin:
    """REQ036 统一人格域（从 PrivateCompanionPlugin 拆出）。"""

    def _active_unified_person_registry(self) -> UnifiedPersonRegistry:
        """Bind identity operations to the store selected by the current persona context."""
        store = self.data
        registry = getattr(self, "unified_person_registry", None)
        if isinstance(registry, UnifiedPersonRegistry) and registry.is_bound_to(store):
            return registry
        return UnifiedPersonRegistry(store)

    def _req036_source_event_anchor(self, event: Any) -> str:
        """Build an event-local, content-free anchor when an adapter omits message IDs."""
        cached = _single_line(
            getattr(event, "_private_companion_req036_source_event_anchor", ""),
            80,
        )
        if cached:
            return cached

        raw_reader = getattr(self, "_event_raw_payload", None)
        try:
            raw = raw_reader(event) if callable(raw_reader) else {}
        except Exception:
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
        message_obj = getattr(event, "message_obj", None)
        metadata: dict[str, str] = {
            "origin": _single_line(getattr(event, "unified_msg_origin", ""), 200),
            "event_type": _single_line(type(event).__qualname__, 120),
            "runtime_event_ref": f"{id(event):x}",
        }
        for key in (
            "post_type",
            "message_type",
            "notice_type",
            "sub_type",
            "time",
            "timestamp",
            "user_id",
            "group_id",
            "self_id",
        ):
            value = _single_line(raw.get(key), 120)
            if value:
                metadata[f"raw_{key}"] = value
        for attr in ("time", "timestamp"):
            value = _single_line(getattr(message_obj, attr, ""), 120)
            if value:
                metadata[f"message_{attr}"] = value
            event_value = _single_line(getattr(event, attr, ""), 120)
            if event_value:
                metadata[f"event_{attr}"] = event_value

        inbound_ts = _single_line(
            getattr(event, "_private_companion_inbound_ts", ""),
            80,
        )
        if not inbound_ts:
            inbound_reader = getattr(self, "_event_inbound_activity_ts", None)
            try:
                inbound_ts = _single_line(
                    inbound_reader(event) if callable(inbound_reader) else _now_ts(),
                    80,
                )
            except Exception:
                inbound_ts = _single_line(_now_ts(), 80)
        metadata["observed_at"] = inbound_ts

        anchor = hashlib.sha256(
            json.dumps(
                metadata,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        try:
            setattr(event, "_private_companion_req036_source_event_anchor", anchor)
        except Exception:
            pass
        return anchor

    def create_unified_person(
        self,
        identity: dict[str, Any],
        *,
        profile: dict[str, Any] | None = None,
        operation_id: str = "",
    ) -> dict[str, Any]:
        registry = self._active_unified_person_registry()
        result = registry.create_or_link(
            identity,
            profile=profile,
            operation_id=operation_id,
            actor_id="companion",
        )
        self._req041_emit_identity_dual_write(
            result,
            action="create",
            operation_id=operation_id,
            registry=registry,
        )
        return result

    def get_unified_person_projection(self, person_id: str) -> dict[str, Any] | None:
        return self._active_unified_person_registry().read_projection(person_id)

    def _req036_private_gate_for_user(self, user: Any) -> dict[str, Any]:
        return req036_private_companion_gate(user)

    def _req036_migrate_configured_target_capability(self, user_id: Any, user: Any) -> bool:
        """Repair migration-only false gates for configured targets and legacy owners."""
        if not isinstance(user, dict):
            return False
        if bool(user.get("manual_disabled")):
            return False
        canonicalizer = getattr(self, "_canonical_private_user_id", None)
        identity_normalizer = getattr(self, "_normalize_private_identity_id", None)

        def normalized_identity(value: Any) -> str:
            candidate = _single_line(value, 160)
            if callable(identity_normalizer):
                try:
                    candidate = _single_line(identity_normalizer(candidate), 160) or candidate
                except Exception:
                    return ""
            if callable(canonicalizer):
                try:
                    candidate = _single_line(canonicalizer(candidate), 160)
                except Exception:
                    return ""
            return candidate

        configured_targets = getattr(self, "_configured_target_ids", None)
        target_ids: set[str] = set()
        try:
            if callable(configured_targets):
                target_ids = {
                    target_id
                    for target_id in (normalized_identity(target) for target in configured_targets())
                    if target_id
                }
        except Exception:
            return False

        stamped_subject = normalized_identity(user.get("identity_subject_id"))
        if stamped_subject:
            # Once a record has a stamped platform subject, its storage key
            # and historical transport aliases are no longer authorization
            # candidates. This prevents a target-named shadow row from
            # inheriting the target's active capability for another person.
            identity_candidates = {stamped_subject}
        else:
            identity_candidates = {
                candidate
                for candidate in (
                    normalized_identity(user_id),
                    normalized_identity(user.get("user_id")),
                )
                if candidate
            }
        # ``alias_user_ids`` are transport/history hints, never authorization
        # credentials.  Using them here allowed a renamed or migrated identity
        # to reopen active permission for another stable user.
        configured_match = bool(target_ids.intersection(identity_candidates))

        # A bare numeric/openid target must not grant the same identifier on a
        # different platform. Adapter instance changes inside the configured
        # platform remain compatible and are handled by the scoped profile.
        if configured_match:
            platform_normalizer = getattr(self, "_normalize_platform_kind", None)
            configured_platform_raw = _single_line(getattr(self, "target_platform", ""), 80).lower()
            observed_platform = _single_line(user.get("identity_platform_kind"), 40).lower()
            configured_platform = ""
            if callable(platform_normalizer) and configured_platform_raw:
                try:
                    configured_platform = _single_line(platform_normalizer(configured_platform_raw), 40).lower()
                except Exception:
                    configured_platform = ""
            if (
                configured_platform
                and configured_platform != "generic"
                and observed_platform
                and observed_platform != "generic"
                and configured_platform != observed_platform
            ):
                configured_match = False
            elif configured_platform == "generic" and configured_platform_raw:
                observed_adapter = _single_line(user.get("identity_adapter_instance_id"), 120).lower()
                if observed_adapter and configured_platform_raw not in {observed_adapter, observed_adapter.split(":", 1)[0]}:
                    configured_match = False

        owner_match = False
        if not configured_match:
            return False

        capabilities = user.get("unified_profile_capabilities")
        if isinstance(capabilities, dict) and capabilities.get("private_companion_enabled") is True:
            return False
        grant_source = (
            _single_line(capabilities.get("grant_source"), 80).lower()
            if isinstance(capabilities, dict)
            else ""
        )
        explicit_sources = {
            "admin",
            "administrator",
            "manual",
            "page_administrator",
            "page_administrator_update",
        }
        if grant_source in explicit_sources or "administrator" in grant_source:
            return False

        # Audit provenance is authoritative even when an older writer failed
        # to keep grant_source synchronized.
        audit = user.get("unified_profile_capability_audit")
        if isinstance(audit, list):
            for entry in reversed(audit[-64:]):
                if not isinstance(entry, dict):
                    continue
                changed = entry.get("changed")
                private_change = changed.get("private_companion_enabled") if isinstance(changed, dict) else None
                if not isinstance(private_change, dict) or "to" not in private_change:
                    continue
                if private_change.get("to") is True:
                    break
                actor = _single_line(entry.get("actor_id"), 80).lower()
                reason = _single_line(entry.get("reason_code"), 80).lower()
                compatibility_change = any(
                    token in f"{actor} {reason}"
                    for token in ("migration", "compatibility", "reconciliation", "startup")
                )
                if private_change.get("to") is False and not compatibility_change:
                    return False
                break

        repairable_sources = {
            "",
            "default_closed",
            "group_observation",
            "legacy_effective_migration",
            "legacy_configured_target_migration",
            "owner_default_enabled",
        }
        if (
            isinstance(capabilities, dict)
            and grant_source not in repairable_sources
            and not bool(user.get("manual_enabled"))
        ):
            return False

        proactive_enabled = bool(
            owner_match
            or (isinstance(capabilities, dict) and capabilities.get("proactive_private_enabled") is True)
            or user.get("proactive_private_enabled") is True
            or _safe_int(user.get("proactive_daily_limit"), 0, 0) > 0
        )
        source = (
            "owner_capability_reconciliation"
            if owner_match and not configured_match
            else "configured_target_capability_reconciliation"
            if isinstance(capabilities, dict)
            else "legacy_configured_target_migration"
        )
        result = req036_update_capabilities(
            user,
            {
                "private_companion_enabled": True,
                "proactive_private_enabled": proactive_enabled,
            },
            actor_authorized=True,
            grant_source=source,
            actor_id="compatibility_migration",
            target_identity=normalized_identity(user.get("identity_subject_id")) or normalized_identity(user_id),
            reason_code=source,
        )
        return bool(result.get("ok"))

    def _req036_capability_summary_for_user(self, user: Any) -> dict[str, Any]:
        bridge = self._memory_companion_bridge()
        portrait_backend_available = callable(getattr(bridge, "read_unified_profile_portrait", None))
        return req036_capability_summary(
            user,
            global_portrait_mode=runtime_persona_setting(self, 'portrait_global_mode', "disabled"),
            portrait_backend_available=portrait_backend_available,
        )

    def _req036_proactive_private_allowed(self, user: Any) -> bool:
        return bool(req036_proactive_private_gate(user).get("allowed"))

    def _req036_update_capabilities(
        self,
        user: dict[str, Any],
        changes: dict[str, Any],
        *,
        actor_id: str = "page_administrator",
        target_identity: str = "",
        reason_code: str = "administrator_update",
    ) -> dict[str, Any]:
        requested_mode = _single_line(changes.get("portrait_mode"), 40).lower() if isinstance(changes, dict) else ""
        if requested_mode and requested_mode not in {"disabled", "off", "follow_global"}:
            bridge = self._memory_companion_bridge()
            if not callable(getattr(bridge, "read_unified_profile_portrait", None)):
                return {
                    "ok": False,
                    "code": "memory_companion_required",
                    "message": "需要安装并启用 MemoryCompanion",
                    "capabilities": self._req036_capability_summary_for_user(user),
                }
        return req036_update_capabilities(
            user,
            changes,
            actor_authorized=True,
            grant_source="administrator",
            actor_id=actor_id,
            target_identity=target_identity,
            reason_code=reason_code,
        )

    def _req036_attach_unified_profile_context(
        self,
        event: Any,
        *,
        user: dict[str, Any] | None = None,
        group_id: str = "",
        source: str = "observation",
    ) -> dict[str, Any]:
        """Attach the smallest exact-person context for Memory's read-only use."""
        identity = self._unified_person_event_identity(event)
        if not identity:
            return {"state": "identity_pending", "code": "identity_pending"}
        resolution = self.resolve_unified_person_identity(identity)
        if resolution.get("state") != "resolved":
            profile = user if isinstance(user, dict) else {}
            created = self.create_unified_person_for_event(
                event,
                operation_id=f"req036.{source}:{str(resolution.get('identity_key') or '')[-24:]}",
                profile={
                    "display_name": (
                        _single_line(profile.get("nickname"), 80) if not group_id else ""
                    ),
                    "preferred_address": (
                        _single_line(profile.get("nickname"), 40) if not group_id else ""
                    ),
                    "style": _single_line(profile.get("style"), 40) if not group_id else "",
                    "profile_origin": _single_line(profile.get("profile_origin"), 60),
                    "auto_profile_created": bool(profile.get("auto_profile_created", False)),
                    "affinity_score": _safe_int(profile.get("relationship_score"), 0, -1200, 1200),
                    "owner_mode": "owner" if _single_line(profile.get("relationship_role"), 40) == "owner" else "not_owner",
                    "relation_policy_id": _single_line(profile.get("relationship_mode"), 40) or "default_friend",
                },
            )
            if created.get("state") != "resolved":
                return {"state": "identity_pending", "code": str(created.get("code") or "identity_pending")}
            resolution = self.resolve_unified_person_identity(identity)
        projection = resolution.get("projection") if isinstance(resolution.get("projection"), dict) else None
        if not isinstance(projection, dict):
            return {"state": "identity_pending", "code": "projection_missing"}
        person_id = _single_line(projection.get("person_id"), 80)
        if isinstance(user, dict) and person_id:
            user["unified_person_id"] = person_id
            user["unified_profile_projection_revision"] = int(projection.get("projection_revision") or 1)
            if not group_id:
                private_facts = {
                    "style": _single_line(user.get("style"), 40),
                    "profile_origin": _single_line(user.get("profile_origin"), 60),
                    "auto_profile_created": bool(user.get("auto_profile_created", False)),
                }
                private_name = _single_line(user.get("nickname"), 80)
                if private_name:
                    private_facts["display_name"] = private_name
                    private_facts["preferred_address"] = private_name[:40]
                fact_signature = hashlib.sha256(
                    json.dumps(private_facts, ensure_ascii=False, sort_keys=True).encode("utf-8")
                ).hexdigest()[:32]
                self._req041_update_unified_profile_facts(
                    user,
                    private_facts,
                    operation_id=f"req041-private-profile-observation-{person_id[-16:]}-{fact_signature}",
                    actor_id="private_observation",
                    schedule_save=True,
                )
        group_scope = ""
        if group_id and person_id:
            platform = _single_line(identity.get("subject_namespace"), 80).split(":", 1)[0]
            group_scope = self._unified_wire_group_scope(platform, group_id)
            if group_scope:
                self._active_unified_person_registry().upsert_group_overlay(
                    person_id,
                    group_scope,
                    {
                        "alias": _single_line((user or {}).get("nickname"), 80),
                        "source": "group_observation",
                        "public": True,
                    },
                    operation_id=f"req036.overlay:{person_id[-12:]}:{_single_line(group_id, 40)}",
                    actor_id="companion",
                )
        if person_id:
            event_id = _single_line(self._event_message_id(event), 120)
            event_anchor = event_id or self._req036_source_event_anchor(event)
            source_fingerprint = hashlib.sha256(
                f"req036:{source}:{group_scope or 'private'}:{event_anchor}".encode("utf-8", errors="ignore")
            ).hexdigest()
            self._active_unified_person_registry().record_identity_source_event(
                person_id,
                _single_line(projection.get("resolved_identity_key"), 160),
                group_scope or "private",
                source_fingerprint,
                operation_id=f"req036.source:{source}:{source_fingerprint[:24]}",
            )
        portrait_namespace_getter = getattr(self, "_req041_scoped_context_for_user", None)
        if callable(portrait_namespace_getter) and isinstance(user, dict):
            try:
                portrait_namespace = portrait_namespace_getter(
                    user,
                    kind="group_member" if group_id else "private",
                    group_id=group_id,
                    purpose="profile_read",
                )
            except Exception:
                portrait_namespace = None
            if isinstance(portrait_namespace, NamespaceContext) and not portrait_namespace.errors():
                try:
                    setattr(
                        event,
                        "private_companion_namespace_context",
                        portrait_namespace.to_dict(),
                    )
                except Exception:
                    pass
        dto = req036_build_profile_dto(
            person_ref=req036_build_person_ref(projection),
            identity_summary={"display_name": _single_line((user or {}).get("nickname"), 80)},
            expression_summary={
                "relationship_score": _safe_int((user or {}).get("relationship_score"), 0, -1200, 1200),
                "relationship_role": _single_line((user or {}).get("relationship_role"), 40) or "friend",
            },
            capability_summary=self._req036_capability_summary_for_user(user),
            context_overlays={"group_scope": group_scope} if group_scope else {},
            bridge_status={"state": "ready", "source": "companion"},
        )
        errors = req036_validate_profile_dto(dto)
        if errors:
            return {"state": "degraded", "code": "bridge_contract_mismatch", "errors": errors}
        try:
            setattr(event, "private_companion_unified_profile_context", dto)
        except Exception:
            return {"state": "degraded", "code": "bridge_unavailable"}
        return {"state": "profile_exact", "code": "profile_exact", "dto": dto, "person_id": person_id}

    async def _req036_reject_unauthorized_private_event(self, event: Any, gate: dict[str, Any]) -> None:
        """Reply before any LLM, bridge, tool, portrait, or relationship path."""
        inbound_checker = getattr(self, "_event_is_inbound_chat_message", None)
        if callable(inbound_checker) and not inbound_checker(event):
            return
        if bool(getattr(event, "private_companion_req036_denied", False)):
            try:
                event.stop_event()
            except Exception:
                pass
            return
        try:
            setattr(event, "private_companion_req036_denied", True)
            setattr(event, "private_companion_req036_denial_code", str(gate.get("code") or "private_companion_disabled"))
        except Exception:
            pass
        try:
            event.stop_event()
        except Exception:
            pass

        # Adapter redelivery can reconstruct the same genuine message as a
        # different event object.  Deduplicate only by its stable platform
        # message identity; this is not a time-based user rate limit.
        message_id = ""
        message_id_getter = getattr(self, "_event_message_id", None)
        if callable(message_id_getter):
            try:
                message_id = _single_line(message_id_getter(event), 120)
            except Exception:
                message_id = ""
        denial_cache_key = ""
        denial_cache: dict[str, float] | None = None
        denial_cache_stamp = time.monotonic()
        if message_id:
            try:
                sender_id = _single_line(event.get_sender_id(), 120)
            except Exception:
                sender_id = ""
            platform_getter = getattr(self, "_platform_kind_for_event", None)
            try:
                platform = _single_line(platform_getter(event), 80) if callable(platform_getter) else ""
            except Exception:
                platform = ""
            scope_getter = getattr(self, "_event_req036_scope", None)
            try:
                denial_scope = _single_line(scope_getter(event), 480) if callable(scope_getter) else ""
            except Exception:
                denial_scope = ""
            denial_scope = denial_scope or f"{platform or 'unknown'}:{sender_id or 'unknown'}"
            denial_cache_key = f"{denial_scope}:{message_id}"
            denial_cache = getattr(self, "_req036_recent_denial_message_ids", None)
            if not isinstance(denial_cache, dict):
                denial_cache = {}
                self._req036_recent_denial_message_ids = denial_cache
            for cache_key, cached_at in list(denial_cache.items()):
                age = denial_cache_stamp - _safe_float(cached_at, 0.0)
                if age < 0 or age > 180.0:
                    denial_cache.pop(cache_key, None)
            if denial_cache_key in denial_cache:
                logger.debug(
                    "已忽略重复的未授权私聊拒绝: sender=%s message_id=%s",
                    sender_id or "-",
                    message_id,
                )
                return
            denial_cache[denial_cache_key] = denial_cache_stamp
        reply_text = str(gate.get("reply") or DEFAULT_UNAUTHORIZED_PRIVATE_REPLY)
        echo_entry = None
        echo_remember = getattr(self, "_remember_req036_denial_echo", None)
        if callable(echo_remember):
            try:
                echo_entry = echo_remember(event, reply_text)
            except Exception:
                echo_entry = None

        def release_reply_reservations() -> None:
            if denial_cache is not None and denial_cache_key and denial_cache.get(denial_cache_key) == denial_cache_stamp:
                denial_cache.pop(denial_cache_key, None)
            echo_forget = getattr(self, "_forget_req036_denial_echo", None)
            if callable(echo_forget) and echo_entry is not None:
                try:
                    echo_forget(echo_entry)
                except Exception:
                    pass

        try:
            reply_result = await self._reply(event, reply_text)
        except Exception:
            release_reply_reservations()
            raise
        if reply_result is False:
            release_reply_reservations()
            logger.debug("未授权私聊拒绝未发送，已释放回流与消息去重占位")
            return
        echo_confirm = getattr(self, "_confirm_req036_denial_echo", None)
        if callable(echo_confirm) and echo_entry is not None:
            try:
                echo_confirm(echo_entry)
            except Exception:
                pass

    @staticmethod
    def _req036_group_portrait_query_kind(text: Any) -> str:
        value = _single_line(text, 240)
        # A preference phrase followed by advice or a conclusion is ordinary
        # group chatter, not a request to summarize anyone's profile.
        ordinary_statement_patterns = (
            r"(?:^|[\s，,：:@])(?:自己|按自己|个人|各自)\s*(?:喜欢|爱)(?:吃|喝|玩|看|听)?什么\s*(?:就|便|吧|呀|喵|都|随便)",
            r"(?:喜欢|爱)(?:吃|喝|玩|看|听)?什么\s*(?:就|便|吧|呀|喵|都|随便)",
            # Questions about choosing/feeding an item are ordinary chatter,
            # not requests to summarize a person's preference profile.
            r"(?:要|该|应该|可以|能|想|准备)?\s*(?:喂|选|挑|买|点|吃|喝|做|换).{0,8}(?:什么|啥|哪种|哪个)口味",
            # Negative interest statements ("现在干啥都提不起兴趣" etc.) are
            # ordinary venting chatter. Without this the stray 啥 in 干啥
            # before 兴趣 false-positives as a third-party portrait probe.
            r"(?:提不起|不感|没(?:有|啥|什么)?|毫无|失去|缺(?:乏)?)(?:任何的?\s*)?兴趣",
        )
        if any(re.search(pattern, value) for pattern in ordinary_statement_patterns):
            return ""
        probe_patterns = (
            r"喜欢(?:吃|喝|玩|看|听)?什么",
            r"爱(?:吃|喝|玩|看|听)什么",
            r"(?:爱好|兴趣|偏好|习惯|口味|画像)(?:是|有|包括)?(?:什么|啥|哪些|怎么样)",
            r"(?:什么|啥|哪些|有啥|有哪些).{0,8}(?:爱好|兴趣|偏好|习惯|口味)",
            r"(?:说说|看看|查查|总结|整理).{0,12}(?:爱好|兴趣|偏好|习惯|口味|画像)",
        )
        if not any(re.search(pattern, value) for pattern in probe_patterns):
            return ""
        self_subject = r"(?:我自己|我的|我|本人自己|本人的|本人|俺自己|俺的|俺|咱自己|咱的|咱)"
        self_predicate = (
            r"(?:平时|一般|通常|到底|最)?(?:"
            r"喜欢(?:吃|喝|玩|看|听)?什么|爱(?:吃|喝|玩|看|听)什么|"
            r"(?:有|有什么|有啥|有哪些).{0,8}(?:爱好|兴趣|偏好|习惯)|"
            r"(?:的)?(?:爱好|兴趣|偏好|习惯|口味|画像)(?:是|有|包括)?(?:什么|啥|哪些|怎么样)"
            r")"
        )
        direct_self_query = rf"{self_subject}\s*{self_predicate}"
        reflective_self_query = (
            rf"(?:^|[\s，,：:@])我\s*(?:想知道|想问|想看看|想了解)\s*"
            rf"(?:一下)?\s*(?:自己|我自己|我的)\s*{self_predicate}"
        )
        if re.search(direct_self_query, value) or re.search(reflective_self_query, value):
            return "self"
        bot_subject = r"(?:你自己|你的|你)"
        direct_bot_query = rf"{bot_subject}\s*{self_predicate}"
        reflective_bot_query = (
            rf"(?:^|[\s，,：:@])我\s*(?:想知道|想问|想看看|想了解)\s*"
            rf"(?:一下)?\s*(?:你自己|你的|你)\s*{self_predicate}"
        )
        if re.search(direct_bot_query, value) or re.search(reflective_bot_query, value):
            return "bot_self"
        # An omitted subject is ambiguous in natural group speech. Let the
        # normal reply chain decide whether the user means the Bot, instead of
        # treating a prompt such as "喜欢什么发型" as a third-party probe.
        subjectless_query = (
            r"^(?:@[^\s]+\s*)?(?:(?:你觉得|你认为|请问|我想(?:知道|问|看看|了解))(?:一下)?\s*)?"
            r"(?:喜欢|爱)(?:吃|喝|玩|看|听)?什么"
            r"|^(?:@[^\s]+\s*)?(?:(?:你觉得|你认为|请问|我想(?:知道|问|看看|了解))(?:一下)?\s*)?"
            r"(?:什么|啥|哪些).{0,8}(?:爱好|兴趣|偏好|习惯|口味|画像)"
        )
        if re.search(subjectless_query, value):
            return ""
        return "third_party"

    def _req036_group_portrait_query_is_directed(self, event: Any) -> bool:
        """Use adapter addressing metadata so ordinary group chatter never triggers this guard."""
        # ``is_wake`` only means that some handler accepted the event; it does
        # not prove that the user addressed this Bot. Keep the more specific
        # command flag and structured At/Reply evidence below.
        if bool(getattr(event, "is_at_or_wake_command", False)):
            return True
        try:
            signals = self._event_scene_signals(event)
        except Exception:
            signals = {}
        if not isinstance(signals, dict):
            return False
        if any(
            isinstance(item, dict) and bool(item.get("is_bot"))
            for item in (signals.get("at_targets") or [])
        ):
            return True
        self_id = _single_line(signals.get("self_id"), 80)
        return bool(self_id and _single_line(signals.get("reply_to_id"), 80) == self_id)

    async def _req036_read_group_self_portrait(self, event: Any) -> str:
        dto = getattr(event, "private_companion_unified_profile_context", None)
        if not isinstance(dto, dict):
            return "这部分画像暂时不可用。"
        capabilities = dto.get("capability_summary")
        if not isinstance(capabilities, dict) or capabilities.get("portrait_usage_enabled") is not True:
            return "智能画像当前未开启。"
        person_ref = dto.get("person_ref") if isinstance(dto.get("person_ref"), dict) else {}
        person_id = _single_line(person_ref.get("person_id"), 80)
        overlays = dto.get("context_overlays") if isinstance(dto.get("context_overlays"), dict) else {}
        scope = _single_line(overlays.get("group_scope"), 80)
        if not scope.startswith("group:"):
            return "这部分画像暂时不可用。"
        request = req036_build_portrait_request(
            person_ref=person_ref,
            requester_person_id=person_id,
            target_person_id=person_id,
            scope=scope,
            purpose="summarize_to_subject",
        )
        namespace_context = getattr(event, "private_companion_namespace_context", None)
        if isinstance(namespace_context, dict):
            request["namespace_context"] = dict(namespace_context)
        bridge = self._memory_companion_bridge()
        reader = getattr(bridge, "read_unified_profile_portrait", None) if bridge is not None else None
        if not callable(reader):
            return "这部分画像暂时不可用。"
        try:
            result = reader(request, limit=5)
            if asyncio.iscoroutine(result) or hasattr(result, "__await__"):
                result = await result
        except Exception:
            return "这部分画像暂时不可用。"
        if not isinstance(result, dict) or not result.get("ok"):
            return "这部分画像暂时不可用。"
        summaries = [
            _single_line(item.get("summary"), 80)
            for item in result.get("items", [])
            if isinstance(item, dict) and _single_line(item.get("summary"), 80)
        ]
        return "我目前只记得这些公开的低敏偏好：" + "；".join(summaries[:5]) if summaries else "我还没有整理出可公开的低敏画像。"

    async def _req036_portrait_bridge_status_for_user(self, user: Any) -> dict[str, Any]:
        """Read synchronization state only; facts stay in Memory's admin UI."""
        source = user if isinstance(user, dict) else {}
        person_id = _single_line(source.get("unified_person_id"), 80)
        if not person_id:
            return {"available": False, "code": "identity_pending", "last_synced_at": "", "portrait_revision": 0}
        bridge = self._memory_companion_bridge()
        reader = getattr(bridge, "unified_profile_portrait_status", None) if bridge is not None else None
        if not callable(reader):
            return {"available": False, "code": "bridge_unavailable", "last_synced_at": "", "portrait_revision": 0}
        try:
            result = reader(person_id)
            if asyncio.iscoroutine(result) or hasattr(result, "__await__"):
                result = await result
        except Exception:
            return {"available": False, "code": "bridge_degraded", "last_synced_at": "", "portrait_revision": 0}
        if not isinstance(result, dict):
            return {"available": False, "code": "bridge_degraded", "last_synced_at": "", "portrait_revision": 0}
        response = {
            "available": bool(result.get("ok")),
            "code": _single_line(result.get("code"), 80) or "bridge_degraded",
            "last_synced_at": _single_line(result.get("last_synced_at"), 80),
            "portrait_revision": _safe_int(result.get("portrait_revision"), 0, 0),
        }
        projection = self.get_unified_person_projection(person_id)
        portrait_reader = getattr(bridge, "read_unified_profile_portrait", None) if bridge is not None else None
        if not response["available"] or not isinstance(projection, dict) or not callable(portrait_reader):
            return response
        try:
            request = req036_build_portrait_request(
                person_ref=req036_build_person_ref(projection),
                requester_person_id=person_id,
                target_person_id=person_id,
                scope="private",
                purpose="summarize_to_subject",
            )
            namespace_getter = getattr(self, "_req041_scoped_context_for_user", None)
            if callable(namespace_getter):
                namespace_context = namespace_getter(
                    source, kind="private", purpose="profile_read"
                )
                if isinstance(namespace_context, NamespaceContext) and not namespace_context.errors():
                    request["namespace_context"] = namespace_context.to_dict()
            portrait = portrait_reader(request, limit=3)
            if asyncio.iscoroutine(portrait) or hasattr(portrait, "__await__"):
                portrait = await portrait
            response["summaries"] = [
                _single_line(item.get("summary"), 80)
                for item in (portrait.get("items", []) if isinstance(portrait, dict) else [])
                if isinstance(item, dict) and _single_line(item.get("summary"), 80)
            ][:3]
        except Exception:
            response["summaries"] = []
        return response

    async def _req036_preferred_address_from_portrait(self, user: Any) -> str:
        """Resolve this exact private subject's latest explicit address hint."""
        source = user if isinstance(user, dict) else {}
        capabilities = self._req036_capability_summary_for_user(source)
        if capabilities.get("portrait_usage_enabled") is not True:
            return ""
        person_id = _single_line(source.get("unified_person_id"), 80)
        if not person_id:
            return ""
        projection = self.get_unified_person_projection(person_id)
        if not isinstance(projection, dict):
            return ""
        bridge = self._memory_companion_bridge()
        reader = (
            getattr(bridge, "read_unified_profile_portrait", None)
            if bridge is not None
            else None
        )
        if not callable(reader):
            return ""
        request = req036_build_portrait_request(
            person_ref=req036_build_person_ref(projection),
            requester_person_id=person_id,
            target_person_id=person_id,
            scope="private",
            purpose="summarize_to_subject",
        )
        namespace_getter = getattr(self, "_req041_scoped_context_for_user", None)
        if callable(namespace_getter):
            namespace_context = namespace_getter(
                source, kind="private", purpose="profile_read"
            )
            if (
                isinstance(namespace_context, NamespaceContext)
                and not namespace_context.errors()
            ):
                request["namespace_context"] = namespace_context.to_dict()
        result = reader(request, limit=8)
        if asyncio.iscoroutine(result) or hasattr(result, "__await__"):
            result = await result
        if not isinstance(result, dict) or not result.get("ok"):
            return ""
        for item in result.get("items", []):
            if not isinstance(item, dict):
                continue
            if _single_line(item.get("dimension"), 80) != "preferred_address":
                continue
            summary = _single_line(item.get("summary"), 180)
            match = re.fullmatch(r"希望被称为\s+(.+)", summary)
            preferred = _single_line(match.group(1) if match else "", 24)
            if preferred:
                return preferred
        return ""

    def read_p4_effect_state(self, person_id: str) -> dict[str, Any]:
        return self._active_unified_person_registry().read_p4_effect_state(person_id)

    def read_p4_live_state(self, person_id: str) -> dict[str, Any]:
        return self._active_unified_person_registry().read_p4_live_state(person_id)

    def _p4_b_apply_legacy_relationship_delta(
        self,
        user: dict[str, Any],
        delta: int,
        *,
        reason_code: str = "",
    ) -> bool:
        del reason_code
        return apply_legacy_relationship_delta(
            user,
            delta,
            isolate=bool(getattr(self, "enable_p4_b_legacy_score_isolation", False)),
        )

    def _p4_live_state_for_event(self, event: Any) -> dict[str, Any] | None:
        try:
            if not bool(event.is_private_chat()):
                return None
        except Exception:
            return None
        resolution = self.resolve_unified_person_for_event(event)
        if resolution.get("state") != "resolved":
            return None
        person_id = _single_line(resolution.get("person_id"), 160)
        if not person_id:
            return None
        result = self.read_p4_live_state(person_id)
        if result.get("ok") is not True:
            return {"_p4_live_invalid": True}
        return result.get("state")

    def _bounded_p4_reply_temperature_signals(self, event: Any) -> dict[str, Any]:
        """Return transient, bounded advisory inputs for the P4 reply projection."""
        data = getattr(self, "data", None)
        daily_state = data.get("daily_state") if isinstance(data, dict) else None
        energy = daily_state.get("energy") if isinstance(daily_state, dict) else None
        if (
            isinstance(energy, bool)
            or not isinstance(energy, (int, float))
            or not math.isfinite(float(energy))
        ):
            energy = None
        else:
            energy = max(0, min(100, energy))
        mood = ""
        if isinstance(daily_state, dict):
            mood = _single_line(daily_state.get("mood_bias") or daily_state.get("mood"), 64)

        schedule_parts: list[str] = []
        segment_getter = getattr(self, "_current_detail_segment_for_update", None)
        if callable(segment_getter):
            try:
                segment = segment_getter()
            except Exception:
                segment = None
            if isinstance(segment, dict):
                schedule_parts.extend(
                    _single_line(segment.get(key), 80)
                    for key in ("title", "name", "summary", "activity", "location")
                    if _single_line(segment.get(key), 80)
                )
        if not schedule_parts and isinstance(data, dict):
            try:
                current_item = self._get_current_plan_item(data.get("daily_plan", {}))
            except Exception:
                current_item = None
            if isinstance(current_item, dict):
                schedule_parts.extend(
                    _single_line(current_item.get(key), 80)
                    for key in ("title", "name", "summary", "activity", "location")
                    if _single_line(current_item.get(key), 80)
                )

        return {
            "energy": energy,
            "mood": mood or None,
            "schedule": " ".join(schedule_parts)[:240] or None,
            "context": _single_line(getattr(event, "message_str", ""), 280) or None,
        }

    def record_p4_effect_event(
        self,
        person_id: str,
        event: dict[str, Any],
        *,
        operation_id: str,
        actor_id: str = "system",
    ) -> dict[str, Any]:
        result = self._active_unified_person_registry().record_p4_effect_event(
            person_id,
            event,
            operation_id=operation_id,
            actor_id=actor_id,
        )
        if result.get("ok") and result.get("changed"):
            saver = getattr(self, "_schedule_data_save", None)
            if callable(saver):
                saver(sections={"unified_person"})
        return result

    def create_unified_person_for_event(
        self,
        event: Any | None = None,
        *,
        operation_id: str = "",
        profile: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        identity = self._unified_person_event_identity(event)
        if not identity:
            return {"ok": False, "state": "pending", "code": "event_identity_missing", "person_id": ""}
        try:
            identity_key = build_identity_key(identity)
        except (TypeError, ValueError):
            return {"ok": False, "state": "invalid", "code": "identity_invalid", "person_id": ""}
        user_profile = dict(profile) if isinstance(profile, dict) else {}
        if event is not None and not user_profile.get("display_name"):
            name_getter = getattr(self, "_sender_display_name", None)
            if callable(name_getter):
                try:
                    user_profile["display_name"] = _single_line(name_getter(event), 80)
                except Exception:
                    pass
        return self.create_unified_person(
            identity,
            profile=user_profile,
            operation_id=operation_id or f"companion.person.create:{identity_key[-24:]}",
        )

    def build_unified_person_context(self, event: Any | None = None) -> dict[str, Any]:
        identity = self._unified_person_event_identity(event)
        resolution = self.resolve_unified_person_identity(identity) if identity else {
            "state": "pending", "identity_key": "", "person_id": "", "errors": ["event_identity_missing"],
        }
        state = str(resolution.get("state") or "pending")
        projection = resolution.get("projection") if isinstance(resolution.get("projection"), dict) else None
        scope = "unknown"
        if event is not None:
            try:
                scope = "private" if bool(event.is_private_chat()) else "group"
            except Exception:
                scope = "unknown"
        platform = str(identity.get("subject_namespace") or "").split(":", 1)[0] if identity else ""
        group_id = ""
        if scope == "group":
            group_getter = getattr(self, "_extract_group_id_from_event", None)
            if callable(group_getter):
                try:
                    group_id = _single_line(group_getter(event), 160)
                except Exception:
                    group_id = ""
        group_scope = self._unified_wire_group_scope(platform, group_id)
        group_overlay = None
        if state == "resolved" and group_scope and resolution.get("person_id"):
            group_overlay = self._active_unified_person_registry().read_group_overlay(
                str(resolution.get("person_id") or ""), group_scope
            )
        person_payload = {
            key: projection.get(key)
            for key in (
                "person_id", "identity_assurance", "profile_status", "relation_policy_id",
                "relation_label", "owner_mode", "affinity_band", "projection_revision",
                "group_overlay_ref",
            )
            if projection is not None and projection.get(key) not in (None, "", [], {})
        }
        p3 = build_context(
            persona={"companion_instance_id": self._unified_persona_scoped_value(PLUGIN_ID)},
            runtime={"platform": platform, "scope": scope, "adapter_instance_id": identity.get("adapter_instance_id", "")},
            person=person_payload,
            scene={
                "scope": scope,
                "group_scope": group_scope,
                "group_id_present": bool(group_id),
                "group_overlay_revision": group_overlay.get("revision") if isinstance(group_overlay, dict) else 0,
            },
            bridge_available=True,
        )
        if state != "resolved":
            p3["state"] = state if state in {"pending", "invalid", "degraded", "legacy_local"} else "degraded"
            p3["warnings"] = list(p3.get("warnings") or []) + list(resolution.get("errors") or [f"person_{state}"])[:8]
            person_slot = p3.get("slots", {}).get("person")
            if isinstance(person_slot, dict):
                person_slot["state"] = p3["state"]
        p3 = project_context(p3)
        p4 = build_p4_shadow(
            source_kind="companion",
            target_kind="memory_bridge",
            authority="companion",
            reason_code="projection_ready" if state == "resolved" else f"person_{state}",
            safe_reference=str(resolution.get("person_id") or ""),
            operation_id=f"person.context:{str(resolution.get('identity_key') or 'pending')[-24:]}",
            status="shadow" if state == "resolved" else "degraded",
        )
        return {
            "contract_name": PERSON_CONTRACT_NAME,
            "contract_version": PERSON_CONTRACT_VERSION,
            "p3_contract_name": P3_CONTRACT_NAME,
            "p3_contract_version": P3_CONTRACT_VERSION,
            "state": state,
            "identity": {"identity_key": str(resolution.get("identity_key") or ""), "person_id": str(resolution.get("person_id") or "")},
            "projection": projection,
            "p3": p3,
            "p4_shadow": p4,
            "scope": scope,
            "group_scope": group_scope,
        }

    async def archive_unified_person(
        self,
        person_id: str,
        *,
        operation_id: str,
        confirmation_token: str = "",
        dry_run: bool = True,
        actor_id: str = "page_administrator",
        reason_code: str = "person_archive",
    ) -> dict[str, Any]:
        """Run the request-bound, resumable person archive saga."""
        clean_person = _single_line(person_id, 80)
        clean_operation = _single_line(operation_id, 120)
        if not clean_person or not clean_operation or type(dry_run) is not bool:
            return {"ok": False, "state": "invalid", "code": "invalid_request"}
        async with self._data_lock:
            registry = self._active_unified_person_registry()
            prepared = registry.prepare_person_archive(
                clean_person, operation_id=clean_operation,
                actor_id=actor_id, reason_code=reason_code,
            )
            if not prepared.get("ok"):
                return prepared
            self._req041_persist_archive_saga_locked(
                sections={"unified_person"},
            )
            if prepared.get("code") == "person_archived":
                subjects = registry.archived_identity_subjects(clean_person)
                removed = self._req041_erase_person_private_auxiliary_locked(clean_person, subjects)
                if sum(removed.values()) > 0:
                    self._req041_persist_archive_saga_locked(
                        sections={
                            name
                            for name, count in removed.items()
                            if int(count or 0) > 0
                        },
                    )
                return prepared
            if dry_run:
                return prepared
            if not confirmation_token or confirmation_token != prepared.get("confirmation_token"):
                return {
                    "ok": False, "state": "prepared", "code": "archive_confirmation_mismatch",
                    "person_id": clean_person, "operation_id": clean_operation, "changed": False,
                }
            archive_available = getattr(self, "_req041_scoped_archive_available", None)
            if callable(archive_available) and not archive_available():
                return {
                    "ok": False, "state": "prepared", "code": "scoped_identity_archive_unavailable",
                    "person_id": clean_person, "operation_id": clean_operation, "changed": False,
                }
            confirmed = registry.confirm_person_archive(
                clean_person, clean_operation, confirmation_token,
                actor_id=actor_id, reason_code=reason_code,
            )
            if not confirmed.get("ok"):
                return confirmed
            self._req041_persist_archive_saga_locked(
                sections={"unified_person"},
            )
            context = self._req041_scoped_private_context_for_person(clean_person)
            synchronizer = getattr(self, "req041_scoped_projection_sync", None)
            relationship_store = getattr(self, "req041_relationship_store", None)
            outbox = getattr(self, "req041_migration_outbox", None)
            if context is None or synchronizer is None:
                return {
                    "ok": False, "state": "prepared", "code": "scoped_identity_archive_unavailable",
                    "person_id": clean_person, "operation_id": clean_operation, "changed": False,
                }
            if relationship_store is None or not callable(getattr(relationship_store, "tombstone_account", None)):
                synchronizer.mark_dirty()
                return {
                    "ok": False, "state": "prepared", "code": "relationship_archive_unavailable",
                    "person_id": clean_person, "operation_id": clean_operation, "changed": False,
                }
            if outbox is None or not callable(getattr(outbox, "retire_streams", None)):
                synchronizer.mark_dirty()
                return {
                    "ok": False, "state": "prepared", "code": "archive_outbox_unavailable",
                    "person_id": clean_person, "operation_id": clean_operation, "changed": False,
                }
            try:
                stream_receipt = outbox.retire_streams(
                    [f"identity:{clean_person}", f"relationship:{clean_person}"],
                    synchronizer.migration_epoch,
                    operation_id=f"req041-streams-{clean_operation}", reason_code=reason_code,
                )
            except Exception as exc:
                synchronizer.mark_dirty()
                return {
                    "ok": False, "state": "prepared",
                    "code": _single_line(exc, 120) or "archive_stream_retirement_failed",
                    "person_id": clean_person, "operation_id": clean_operation, "changed": False,
                }
            scoped_receipt = synchronizer.archive_identity_scopes(
                context, operation_id=f"req041-scoped-{clean_operation}", reason_code=reason_code,
            )
            if not scoped_receipt.get("ok"):
                return {
                    "ok": False, "state": "prepared",
                    "code": str(scoped_receipt.get("code") or "scoped_identity_archive_failed")[:120],
                    "person_id": clean_person, "operation_id": clean_operation, "changed": False,
                }
            try:
                relationship_receipt = relationship_store.tombstone_account(
                    context, operation_id=f"req041-relationship-{clean_operation}",
                    reason_code=reason_code, actor="administrator",
                )
            except Exception as exc:
                return {
                    "ok": False, "state": "prepared",
                    "code": _single_line(exc, 120) or "relationship_archive_failed",
                    "person_id": clean_person, "operation_id": clean_operation, "changed": False,
                }
            legacy_subjects = [
                _single_line(item.get("identity_subject_id") or item.get("user_id"), 160)
                for item in (self.data.get("users") or {}).values()
                if isinstance(item, dict)
                and _single_line(item.get("unified_person_id"), 80) == clean_person
            ] if isinstance(self.data.get("users"), dict) else []
            auxiliary_counts = self._req041_erase_person_private_auxiliary_locked(
                clean_person, legacy_subjects,
            )
            result = registry.finalize_person_archive(
                clean_person, clean_operation, confirmation_token,
                scoped_receipt, relationship_receipt, stream_receipt,
                actor_id=actor_id, reason_code=reason_code,
            )
            if result.get("changed"):
                result["auxiliary_removed_record_count"] = sum(auxiliary_counts.values())
                coordinator = getattr(self, "req041_migration_coordinator", None)
                rollback = getattr(coordinator, "rollback_identity", None)
                if callable(rollback):
                    rollback(clean_person, reason_code="person_archived")
                self._req041_persist_archive_saga_locked(
                    sections={
                        "unified_person",
                        *(
                            name
                            for name, count in auxiliary_counts.items()
                            if int(count or 0) > 0
                        ),
                    },
                )
            return result

    async def purge_unified_person(
        self,
        person_id: str,
        *,
        operation_id: str,
        confirmation_token: str = "",
        dry_run: bool = True,
        actor_id: str = "page_administrator",
        reason_code: str = "person_delete",
    ) -> dict[str, Any]:
        clean_person = _single_line(person_id, 80)
        clean_operation = _single_line(operation_id, 120)
        if not clean_person or not clean_operation or type(dry_run) is not bool:
            return {"ok": False, "state": "invalid", "code": "invalid_request"}
        async with self._data_lock:
            registry = self._active_unified_person_registry()
            prepared = registry.prepare_person_purge(
                clean_person, operation_id=clean_operation,
                actor_id=actor_id, reason_code=reason_code,
            )
            if not prepared.get("ok"):
                return prepared
            self._req041_persist_archive_saga_locked(
                sections={"unified_person"},
            )
            if prepared.get("code") == "person_purged":
                return prepared
            if dry_run:
                return prepared
            if not confirmation_token or confirmation_token != prepared.get("confirmation_token"):
                return {
                    "ok": False, "state": "prepared", "code": "purge_confirmation_mismatch",
                    "person_id": clean_person, "operation_id": clean_operation, "changed": False,
                }
            confirmed = registry.confirm_person_purge(
                clean_person, clean_operation, confirmation_token,
                actor_id=actor_id, reason_code=reason_code,
            )
            if not confirmed.get("ok"):
                return confirmed
            self._req041_persist_archive_saga_locked(
                sections={"unified_person"},
            )
            subjects = registry.archived_identity_subjects(clean_person)
            if int(prepared.get("detached_identity_count") or 0) > 0 and not subjects:
                return {
                    "ok": False, "state": "confirmed", "code": "purge_identity_subjects_invalid",
                    "person_id": clean_person, "operation_id": clean_operation, "changed": False,
                }
            outbox = getattr(self, "req041_migration_outbox", None)
            synchronizer = getattr(self, "req041_scoped_projection_sync", None)
            if outbox is None or synchronizer is None or not callable(getattr(outbox, "purge_retired_streams", None)):
                return {
                    "ok": False, "state": "confirmed", "code": "purge_outbox_unavailable",
                    "person_id": clean_person, "operation_id": clean_operation, "changed": False,
                }
            try:
                outbox_receipt = outbox.purge_retired_streams(
                    [f"identity:{clean_person}", f"relationship:{clean_person}"],
                    synchronizer.migration_epoch,
                    operation_id=f"req041-purge-streams-{clean_operation}", reason_code=reason_code,
                )
            except Exception as exc:
                return {
                    "ok": False, "state": "confirmed",
                    "code": _single_line(exc, 120) or "purge_outbox_failed",
                    "person_id": clean_person, "operation_id": clean_operation, "changed": False,
                }
            auxiliary_counts = self._req041_erase_person_private_auxiliary_locked(clean_person, subjects)
            legacy_changed_sections: set[str] = set()
            legacy_counts = self._req041_purge_legacy_person_locked(
                clean_person,
                subjects,
                changed_sections=legacy_changed_sections,
            )
            result = registry.finalize_person_purge(
                clean_person, clean_operation, confirmation_token, outbox_receipt,
                actor_id=actor_id, reason_code=reason_code,
            )
            if result.get("changed"):
                result["legacy_removed_record_count"] = int(legacy_counts.get("records") or 0)
                result["auxiliary_removed_record_count"] = sum(auxiliary_counts.values())
                if legacy_changed_sections:
                    # Legacy identity records may live under unregistered roots.
                    # This administrator-confirmed purge is therefore an explicit
                    # full-store repair boundary rather than an implicit fallback.
                    self._req041_persist_archive_saga_locked(
                        full_scope="admin_import_export",
                    )
                else:
                    self._req041_persist_archive_saga_locked(
                        sections={"unified_person"}
                        | {
                            name
                            for name, count in auxiliary_counts.items()
                            if int(count or 0) > 0
                        },
                    )
            return result

    def _text_looks_like_relation_lookup_question(self, text: str) -> bool:
        cleaned = _single_line(text, 180)
        if not cleaned:
            return False
        compact = re.sub(r"\s+", "", cleaned)
        has_query_word = any(
            token in compact
            for token in (
                "认识吗",
                "认得吗",
                "知道吗",
                "是谁",
                "哪位",
                "什么人",
                "这个人",
                "这人",
                "那个人",
                "那人",
                "qq号",
                "QQ号",
                "QQ",
                "qq",
            )
        )
        if re.search(r"\d{5,12}", compact):
            return has_query_word
        if has_query_word:
            try:
                return bool(self._select_worldbook_member_profiles_for_private_text(compact, limit=1))
            except Exception:
                return True
        return False

    async def _private_reply_only_relation_lookup_text(self, event: AstrMessageEvent) -> str:
        try:
            message_id, raw_message = await self._reply_raw_message_for_event(event)
        except Exception as exc:
            logger.info("私聊引用关系网问题预读取失败: %s", _single_line(exc, 120))
            return ""
        if raw_message is None:
            return ""
        try:
            info = self._extract_reply_rich_card_info(raw_message)
        except Exception as exc:
            logger.info("私聊引用关系网问题解析失败: message_id=%s error=%s", message_id or "-", _single_line(exc, 120))
            return ""
        texts = [_single_line(item, 120) for item in info.get("texts", []) if _single_line(item, 120)]
        if not texts:
            return ""
        quoted_text = _single_line("；".join(texts[:3]), 180)
        if not self._text_looks_like_relation_lookup_question(quoted_text):
            return ""
        logger.info(
            "私聊纯引用关系网问题已补触发文本: message_id=%s text=%s",
            message_id or "-",
            _single_line(quoted_text, 120),
        )
        return quoted_text

    @filter.on_llm_request(priority=220000)
    @_multi_persona_event_context
    async def guard_req036_private_capability_before_llm(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        *args,
        **kwargs,
    ):
        """Compatibility hook retained after removing passive private-chat gating."""
        return

    @filter.on_llm_request(priority=-30000)
    @_multi_persona_event_context
    async def enforce_p4_live_confinement_before_enrichment(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        *args,
        **kwargs,
    ):
        """Block only an already-resolved private person with an invalid or active P4 state."""
        if self is None or req is None or not bool(getattr(self, "enabled", False)):
            return
        state = self._p4_live_state_for_event(event)
        decision = decide_live_request(state)
        if decision.get("decision") == "skip":
            return
        if decision.get("decision") == "block":
            # This runs before P5/Memory and the enrichment collectors. Leave
            # no request route to original prompt, tool, bridge, or context data.
            for attribute, value in (
                ("system_prompt", SAFE_CONFINEMENT_REPLY),
                ("prompt", ""),
                ("contexts", []),
                ("extra_user_content_parts", []),
                ("func_tool", None),
                ("tools", []),
                ("images", []),
                ("image_urls", []),
            ):
                try:
                    setattr(req, attribute, value)
                except Exception:
                    pass
            try:
                setattr(event, "private_companion_p4_blocked", True)
                setattr(event, "private_companion_p4_block_code", decision.get("code", "p4_state_invalid"))
            except Exception:
                pass
            await self._reply(event, SAFE_CONFINEMENT_REPLY)
            event.stop_event()
            return
        temperature = compose_reply_temperature(
            decision.get("warmth_projection", {}).get("tier"),
            **self._bounded_p4_reply_temperature_signals(event),
        )
        try:
            setattr(req, "_private_companion_reply_temperature", temperature)
        except Exception:
            pass
        if hasattr(req, "system_prompt"):
            section = reply_temperature_prompt_section(temperature)
            self._materialize_conversation_system_block(
                req,
                section=section,
                marker="[Reply boundary]",
                priority=5,
                placement=PLACEMENT_DYNAMIC_SYSTEM,
            )

    @filter.on_llm_request(priority=-30000)
    @_multi_persona_event_context
    async def inject_unified_relationship_expression(self, event: AstrMessageEvent, req: ProviderRequest, *args, **kwargs):
        """Inject one fail-closed relationship expression decision before Memory enrichment."""
        if self is None or req is None or not bool(getattr(self, "enabled", False)):
            return
        if not bool(runtime_persona_setting(self, 'enable_custom_relationship_stage_policy', False)):
            return
        is_private = self._safe_event_is_private(event)
        group_id = "" if is_private else self._extract_group_id_from_event(event)
        if not is_private and not group_id:
            return
        raw_sender_id = self._safe_event_sender_id(event)
        current_user = None
        if is_private:
            try:
                resolver = getattr(self, "_private_user_id_for_event", None)
                sender_id = (
                    resolver(event)
                    if callable(resolver)
                    else self._canonical_private_user_id(raw_sender_id)
                )
            except Exception:
                sender_id = ""
            users = self.data.get("users", {}) if isinstance(getattr(self, "data", None), dict) else {}
            current_user = users.get(sender_id) if sender_id and isinstance(users, dict) else None
        else:
            projection_getter = getattr(self, "_req039_group_observation_projection", None)
            if callable(projection_getter):
                current_user = projection_getter(
                    event,
                    sender_id=raw_sender_id,
                    sender_name=self._sender_display_name(event),
                )
        if not isinstance(current_user, dict):
            return
        fixture_user = self._lab_fixture_relationship_view(event, current_user)
        fixture_relationship_applied = fixture_user is not current_user
        current_user = fixture_user
        expression_builder = getattr(self, "_build_expression_decision_for_user", None)
        if not callable(expression_builder):
            return
        try:
            expression_args = {
                "passive_reengagement": True,
                "bot_state": {
                    "energy": current_user.get("bot_energy", 70),
                    "mood": current_user.get("bot_mood", ""),
                },
                "message_intent": content_intent_from_text(getattr(event, "message_str", "")),
                "content_policy": {
                    "enabled": bool(runtime_persona_setting(self, 'enable_relationship_content_tiers', False)),
                    "flirt_enabled": bool(runtime_persona_setting(self, 'enable_flirt_content_tier', True)),
                    "private_chat": is_private,
                },
                "channel_scope": "private" if is_private else "group",
            }
            if fixture_relationship_applied:
                expression_args["_authoritative_relationship_view"] = True
            expression = expression_builder(current_user, **expression_args)
            projection = expression.to_dict() if hasattr(expression, "to_dict") else dict(expression or {})
            if is_private:
                violation_hint_getter = getattr(self, "_relationship_violation_prompt_hint", None)
                if callable(violation_hint_getter):
                    hint = violation_hint_getter(current_user, now=_now_ts())
                    if hint:
                        projection["relationship_violation_hint"] = hint
        except Exception as exc:
            logger.debug("统一表达决策生成失败，使用日常保守默认值: %s", _single_line(exc, 120))
            projection = build_expression_decision({}).to_dict()
        try:
            setattr(req, "_private_companion_expression_decision", projection)
            setattr(event, "_private_companion_expression_decision", projection)
        except Exception:
            pass
        section = expression_decision_prompt_section(projection)
        self._append_turn_prompt_fragment_by_position(
            req,
            "<!-- private_companion_expression_decision_v2 -->",
            section,
            priority=5,
            force_dynamic=True,
        )

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE, priority=220000)
    @_multi_persona_event_context
    @event_data_save_boundary
    async def guard_req036_private_capability_early(self, event: AstrMessageEvent, *args, **kwargs):
        """Reject an unauthorized private event before any normal message plugin runs."""
        if self is None or bool(getattr(event, "private_companion_req036_denied", False)):
            return
        inbound_checker = getattr(self, "_event_is_inbound_chat_message", None)
        if callable(inbound_checker) and not inbound_checker(event):
            logger.debug("非入站聊天事件跳过私聊档案预建")
            return
        try:
            user_id = str(event.get_sender_id())
        except Exception:
            user_id = ""
        self_id = self._event_self_id(event)
        if user_id and self_id and user_id == self_id:
            return
        sender_display_name = _single_line(self._sender_display_name(event), 40)
        async with self._data_lock:
            private_user, auto_profile_created = self._ensure_auto_private_user_profile(
                event,
                user_id=user_id,
                sender_display_name=sender_display_name,
                now=_now_ts(),
            )
            if isinstance(private_user, dict):
                user_id = _single_line(private_user.get("user_id"), 160) or user_id
            migrator = getattr(self, "_req036_migrate_configured_target_capability", None)
            migrated = bool(migrator(user_id, private_user)) if callable(migrator) else False
            if migrated:
                self._schedule_data_save(sections={"users"})
        if auto_profile_created:
            logger.info(
                "已建立最小用户档案: user=%s platform=%s",
                _single_line(self._canonical_private_user_id(user_id), 80),
                _single_line(self._platform_kind_for_event(event), 40),
            )

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE, priority=180000)
    @_multi_persona_event_context
    @event_data_save_boundary
    async def guard_req036_group_portrait_queries(self, event: AstrMessageEvent, *args, **kwargs):
        """Reject third-party portrait probing before any retrieval or LLM hook."""
        if self is None or bool(getattr(event, "_private_companion_member_safety_blocked", False)):
            return
        if not bool(runtime_persona_setting(self, 'enable_group_third_party_portrait_guard', True)):
            return
        group_id = self._extract_group_id_from_event(event)
        if not group_id:
            return
        if not self._req036_group_portrait_query_is_directed(event):
            return
        text = self._group_observation_event_text(event)
        kind = self._req036_group_portrait_query_kind(text)
        if not kind:
            return
        if kind == "bot_self":
            return
        if kind == "third_party":
            logger.info(
                "群聊第三方画像查询已拦截: reason=explicit_third_party_query group_hash=%s text_hash=%s text_len=%s",
                hashlib.sha256(str(group_id).encode("utf-8", errors="ignore")).hexdigest()[:12],
                hashlib.sha256(str(text).encode("utf-8", errors="ignore")).hexdigest()[:12],
                len(str(text)),
            )
            event.stop_event()
            await self._reply(event, "这个我不方便替别人整理啦。")
            return
        # An observation-disabled group must not become a wording bypass.  It
        # still does not receive normal group capture; this narrow explicit
        # self-query only prepares a minimal identity/scene reference for the
        # low-sensitivity, same-person Memory request below.
        if not isinstance(getattr(event, "private_companion_unified_profile_context", None), dict):
            try:
                raw_sender_id = str(event.get_sender_id())
                resolver = getattr(self, "_event_private_user_storage_id", None)
                sender_id = (
                    resolver(event, raw_sender_id)
                    if callable(resolver)
                    else self._canonical_private_user_id(raw_sender_id)
                )
            except Exception:
                sender_id = ""
            async with self._data_lock:
                users = self.data.get("users", {}) if isinstance(self.data, dict) else {}
                user = users.get(sender_id) if sender_id and isinstance(users, dict) else None
                self._req036_attach_unified_profile_context(
                    event,
                    user=user if isinstance(user, dict) else None,
                    group_id=group_id,
                    source="group_portrait_query",
                )
                self._schedule_data_save(sections={"users", "unified_person"})
        event.stop_event()
        await self._reply(event, await self._req036_read_group_self_portrait(event))

