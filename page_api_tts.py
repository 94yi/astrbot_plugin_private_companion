# -*- coding: utf-8 -*-
"""TTS/语音域。

由 tools/split_mixin_domain.py 从 page_api.py 机械抽取（25 个方法 + 3 个模块级名字 + 0 个类级赋值 / 974 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApi）。
"""
from __future__ import annotations

import asyncio
import json
import re
import secrets
import time
from .helpers import _redact_outbound_secrets
from astrbot.api.event import MessageChain
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from .logging_util import get_module_logger
from .page_api_shared import _page_api_host, _page_api_host_request as request

logger = get_module_logger(__name__)



TTS_PROVIDER_SYSTEM_KEYS = {"id", "provider", "type", "provider_type", "enable", "hint", "provider_source_id"}

TTS_PROVIDER_SECRET_KEYS = {
    "api_key",
    "azure_tts_subscription_key",
    "gemini_tts_api_key",
    "proxy",
}

FISH_AUDIO_MODEL_OPTIONS = [
    {"value": "s2.1-pro-free", "label": "S2.1 Pro Free"},
    {"value": "s2.1-pro", "label": "S2.1 Pro"},
    {"value": "s2-pro", "label": "S2 Pro"},
    {"value": "s1", "label": "S1 旧版"},
]


class PrivateCompanionPageApiTtsMixin:
    """TTS/语音域（从 PrivateCompanionPageApi 拆出）。"""


    async def _run_tts_generation_chain_test(self, payload: dict[str, Any]) -> dict[str, Any]:
        context = getattr(self.plugin, "context", None)
        if context is None:
            error = "AstrBot context 不可用"
            return {
                "ok": False,
                "title": "TTS 生成与投递测试",
                "generated": False,
                "delivered": False,
                "delivery_umo": "",
                "delivery_error": error,
                "error": error,
            }
        async with self.plugin._data_lock:
            users = deepcopy(self.plugin.data.get("users") if isinstance(self.plugin.data.get("users"), dict) else {})
        umo = self._preferred_tts_test_umo(
            users,
            owner_only=True,
            resolve_delivery_route=True,
        )
        if not umo:
            error = "没有找到主要用户的有效私聊会话；请先设置主要用户并让 Bot 收到一条该用户的私聊消息"
            return {
                "ok": False,
                "title": "TTS 生成与投递测试",
                "generated": False,
                "delivered": False,
                "delivery_umo": "",
                "delivery_error": error,
                "error": error,
            }
        requested_umo = self._single_line(payload.get("umo"), 180)
        if requested_umo and requested_umo != umo:
            logger.info(
                "TTS 排障测试忽略非主要用户目标: requested=%s owner=%s",
                requested_umo,
                umo,
            )
        config: dict[str, Any] = {}
        getter = getattr(context, "get_config", None)
        if callable(getter):
            try:
                config = getter(umo) if umo else getter()
                if not isinstance(config, dict):
                    config = {}
            except Exception:
                config = {}
        provider_getter = getattr(context, "get_using_tts_provider", None)
        tts_provider = None
        if callable(provider_getter):
            try:
                tts_provider = provider_getter(umo) if umo else provider_getter()
            except Exception:
                tts_provider = None
        resolver = getattr(self.plugin, "_resolve_tts_synthesis_provider", None)
        if callable(resolver):
            try:
                tts_provider = resolver(SimpleNamespace(unified_msg_origin=umo), tts_provider)
            except Exception:
                pass
        if tts_provider is None:
            error = "当前没有可用的 AstrBot TTS Provider 或 MiMo Voice Clone 联动"
            return {
                "ok": False,
                "title": "TTS 生成与投递测试",
                "umo": umo,
                "generated": False,
                "delivered": False,
                "delivery_umo": umo,
                "delivery_error": error,
                "error": error,
            }
        provider_settings = dict((config or {}).get("provider_tts_settings", {}) or {})
        spoken = self._single_line(payload.get("text"), 240) or "这是一条排障测试语音，用来确认 TTS 生成链路可以跑通。"
        record_builder = getattr(self.plugin, "_tts_record_component", None)
        started = time.time()
        if callable(record_builder):
            component = await record_builder(
                spoken,
                tts_provider,
                provider_settings,
                config,
                source_text=spoken,
            )
            refs_getter = getattr(self.plugin, "_tts_record_refs", None)
            refs = refs_getter(component) if callable(refs_getter) and component is not None else []
        else:
            audio_path = await tts_provider.get_audio(spoken)
            component = None
            refs = [str(audio_path)] if audio_path else []
        audio_ref = self._single_line(refs[0] if refs else "", 260)
        exists = False
        file_size = 0
        if audio_ref and not re.match(r"^https?://", audio_ref, flags=re.IGNORECASE):
            try:
                audio_file = Path(audio_ref)
                exists = audio_file.exists()
                file_size = audio_file.stat().st_size if exists else 0
            except Exception:
                exists = False
        else:
            exists = bool(audio_ref)
        generated = bool(component is not None and audio_ref and exists)
        delivered = False
        delivery_error = ""
        steps = [
            {
                "name": "生成音频",
                "status": "ok" if generated else "error",
                "detail": "TTS Provider 已返回有效语音组件" if generated else "TTS Provider 未返回可投递的有效语音组件",
            }
        ]
        if generated:
            delivered, delivery_error = await self._deliver_tts_test_component(umo, component)
            steps.append(
                {
                    "name": "投递语音",
                    "status": "ok" if delivered else "error",
                    "detail": "测试语音已发送到主要用户私聊" if delivered else (delivery_error or "发送链路未返回成功回执"),
                }
            )
            if delivered:
                logger.info(
                    "TTS 排障测试语音投递成功: umo=%s",
                    self._single_line(umo, 140),
                )
            else:
                logger.warning(
                    "TTS 排障测试语音投递失败: umo=%s error=%s",
                    self._single_line(umo, 140),
                    self._single_line(delivery_error, 180),
                )
        elapsed_ms = int((time.time() - started) * 1000)
        provider_id = self._provider_id(tts_provider)
        provider_label = self._provider_name(tts_provider, provider_id) if provider_id else getattr(tts_provider, "__class__", type(tts_provider)).__name__
        error = ""
        if not generated:
            error = "TTS provider 未返回可投递的有效语音组件"
        elif not delivered:
            error = delivery_error or "测试语音投递失败"
        return {
            "ok": bool(generated and delivered),
            "title": "TTS 生成与投递测试",
            "umo": umo,
            "provider": self._single_line(provider_label, 100),
            "path": audio_ref,
            "file_size": file_size,
            "generated": generated,
            "delivered": delivered,
            "delivery_umo": umo,
            "delivery_error": self._single_line(delivery_error, 220),
            "steps": steps,
            "detail": "已生成语音组件并发送到主要用户私聊" if delivered else "语音组件已生成，但未能发送到主要用户私聊" if generated else "未生成可投递的语音组件",
            "text": spoken,
            "elapsed_ms": elapsed_ms,
            "error": self._single_line(error, 220),
        }

    async def _deliver_tts_test_component(self, umo: str, component: Any) -> tuple[bool, str]:
        sender = getattr(self.plugin, "_send_chain_components", None)
        if callable(sender):
            try:
                sent = await sender(
                    umo,
                    [component],
                    apply_decorating_hooks=False,
                )
            except Exception as exc:
                return False, self._single_line(_redact_outbound_secrets(str(exc), self.plugin), 600) or exc.__class__.__name__
            if sent is True:
                return True, ""
            return False, "插件发送链路返回 False，平台未确认接收测试语音"

        context = getattr(self.plugin, "context", None)
        fallback = getattr(context, "send_message", None) if context is not None else None
        if not callable(fallback):
            return False, "插件发送链路和 AstrBot context.send_message 均不可用"
        try:
            sent = await fallback(umo, MessageChain([component]))
        except Exception as exc:
            return False, self._single_line(_redact_outbound_secrets(str(exc), self.plugin), 600) or exc.__class__.__name__
        if sent is False:
            return False, "AstrBot 核心发送返回 False，平台未确认接收测试语音"
        return True, ""

    def _preferred_tts_test_umo(
        self,
        users: dict[str, Any],
        *,
        owner_only: bool = False,
        resolve_delivery_route: bool = False,
    ) -> str:
        fallback = ""
        for user_id, item in users.items():
            if not isinstance(item, dict) or not item.get("enabled", True):
                continue
            resolved_user_id = str(item.get("user_id") or user_id)
            stored_umo = self._single_line(item.get("umo"), 180)
            if not stored_umo and not resolve_delivery_route:
                continue
            role = self.plugin._private_user_role(item, resolved_user_id)
            if role != "owner":
                if not owner_only and not fallback:
                    fallback = stored_umo
                continue
            candidates: list[str] = []
            if resolve_delivery_route:
                route_resolver = getattr(self.plugin, "_private_delivery_umo_for_user_id", None)
                if callable(route_resolver):
                    try:
                        candidates.append(self._single_line(route_resolver(resolved_user_id), 180))
                    except Exception as exc:
                        logger.warning(
                            "TTS 排障测试解析主要用户投递会话失败: user=%s error=%s",
                            self._single_line(resolved_user_id, 80),
                            self._single_line(exc, 160),
                        )
            candidates.append(stored_umo)
            for candidate in candidates:
                if not owner_only or self._is_private_test_umo(candidate):
                    return candidate
            if not owner_only and not fallback:
                fallback = stored_umo
        return "" if owner_only else fallback

    def _available_tts_provider_items(self) -> list[dict[str, Any]]:
        context = getattr(self.plugin, "context", None)
        get_all = getattr(context, "get_all_tts_providers", None)
        try:
            providers = list(get_all() or []) if callable(get_all) else []
        except Exception:
            providers = []
        if not providers:
            manager = getattr(context, "provider_manager", None)
            providers = list(getattr(manager, "tts_provider_insts", None) or [])

        using_id = ""
        get_using = getattr(context, "get_using_tts_provider", None)
        if callable(get_using):
            try:
                using_id = self._provider_id(get_using())
            except Exception:
                using_id = ""

        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        for provider in providers:
            provider_id = self._provider_id(provider)
            if not provider_id or provider_id in seen:
                continue
            seen.add(provider_id)
            items.append(
                {
                    "id": provider_id,
                    "name": self._provider_name(provider, provider_id),
                    "type": self._provider_type(provider),
                    "model": self._provider_model(provider),
                    "is_default": provider_id == using_id,
                }
            )
        items.sort(key=lambda item: (not item["is_default"], item["name"].lower(), item["id"].lower()))
        return items

    def _tts_provider_manager(self) -> Any:
        context = getattr(self.plugin, "context", None)
        manager = getattr(context, "provider_manager", None)
        if manager is None:
            raise RuntimeError("当前 AstrBot 未提供 ProviderManager")
        return manager

    @staticmethod
    def _is_tts_provider_config(config: Any) -> bool:
        if not isinstance(config, dict):
            return False
        provider_type = str(config.get("provider_type") or "").strip().lower()
        return provider_type in {"text_to_speech", "tts"}

    @staticmethod
    def _tts_provider_field_type(value: Any, metadata: dict[str, Any]) -> str:
        field_type = str(metadata.get("type") or "").strip().lower()
        if field_type:
            return field_type
        if isinstance(value, bool):
            return "bool"
        if isinstance(value, int):
            return "int"
        if isinstance(value, float):
            return "float"
        if isinstance(value, dict):
            return "object"
        if isinstance(value, list):
            return "list"
        return "string"

    @staticmethod
    def _tts_provider_field_group(key: str) -> str:
        lowered = str(key or "").lower()
        if lowered in {"api_base", "api_key", "proxy", "timeout", "appid", "minimax-group-id"}:
            return "connection"
        if any(
            marker in lowered
            for marker in (
                "model",
                "voice",
                "character",
                "reference",
                "emotion",
                "language",
                "text_lang",
                "prompt_text_lang",
                "style",
                "role",
                "format",
                "dialect",
                "speed",
                "pitch",
                "volume",
                "rate",
            )
        ):
            return "voice"
        return "advanced"

    @staticmethod
    def _tts_provider_secret_field(key: str) -> bool:
        lowered = str(key or "").strip().lower()
        return lowered in TTS_PROVIDER_SECRET_KEYS or any(
            marker in lowered
            for marker in (
                "api-key",
                "api_key",
                "api_token",
                "access_token",
                "access_key",
                "secret_key",
                "subscription_key",
                "password",
            )
        ) or lowered in {
            "key",
            "token",
            "secret",
        }

    @staticmethod
    def _tts_provider_fallback_label(key: str) -> str:
        labels = {
            "api_key": "API Key",
            "api_base": "API 地址",
            "proxy": "代理地址",
            "timeout": "超时时间",
            "model": "模型",
            "appid": "App ID",
            "openai-tts-voice": "音色",
            "mimo-tts-voice": "音色",
            "mimo-tts-format": "输出格式",
            "mimo-tts-style-prompt": "风格提示词",
            "mimo-tts-dialect": "方言",
            "mimo-tts-seed-text": "种子文本",
            "edge-tts-voice": "音色",
            "rate": "语速",
            "volume": "音量",
            "pitch": "音调",
            "fishaudio-tts-character": "角色名称",
            "fishaudio-tts-reference-id": "参考模型 ID",
            "dashscope_tts_voice": "音色",
            "azure_tts_subscription_key": "订阅密钥",
            "azure_tts_region": "服务区域",
            "volcengine_cluster": "集群",
            "volcengine_voice_type": "音色 ID",
            "gemini_tts_api_key": "API Key",
            "gemini_tts_api_base": "API 地址",
            "gemini_tts_timeout": "超时时间",
            "gemini_tts_model": "模型",
            "gemini_tts_prefix": "朗读前缀",
            "gemini_tts_voice_name": "音色",
            "elevenlabs-tts-voice-id": "Voice ID",
            "elevenlabs-tts-output-format": "输出格式",
            "elevenlabs-tts-stability": "稳定度",
            "elevenlabs-tts-similarity-boost": "相似度增强",
            "elevenlabs-tts-style": "风格强度",
            "elevenlabs-tts-use-speaker-boost": "启用说话人增强",
        }
        if key in labels:
            return labels[key]
        return str(key or "").replace("_", " ").replace("-", " ").strip()

    @staticmethod
    def _tts_provider_fallback_hint(key: str) -> str:
        hints = {
            "api_key": "密钥不会在页面回显；留空保存会保留现有值。",
            "api_base": "服务接口地址，使用官方服务时通常保持默认。",
            "proxy": "可选代理地址，支持 HTTP、HTTPS 或 SOCKS5；留空保存会保留现有值。",
            "timeout": "单次语音合成请求的超时时间。",
        }
        return hints.get(str(key or ""), "")

    @staticmethod
    def _tts_provider_metadata_unresolved(value: Any) -> bool:
        text = str(value or "").strip()
        if not text:
            return False
        lowered = text.lower()
        return (
            lowered.startswith("provider_group.")
            or lowered.startswith("config.")
            or lowered.endswith(".description")
            or lowered.endswith(".hint")
        )

    def _tts_provider_schema_bundle(self) -> dict[str, Any]:
        config_templates: dict[str, Any] = {}
        field_metadata: dict[str, Any] = {}
        try:
            from astrbot.core.config.default import CONFIG_METADATA_2

            provider_metadata = (
                CONFIG_METADATA_2.get("provider_group", {})
                .get("metadata", {})
                .get("provider", {})
            )
            config_templates = deepcopy(provider_metadata.get("config_template", {}) or {})
            field_metadata = deepcopy(provider_metadata.get("items", {}) or {})
        except Exception as exc:
            logger.warning(
                "读取 AstrBot TTS Provider 模板失败，将使用运行态字段: %s",
                self._single_line(exc, 160),
            )

        templates: list[dict[str, Any]] = []
        by_type: dict[str, dict[str, Any]] = {}
        for display_name, source in config_templates.items():
            if not self._is_tts_provider_config(source):
                continue
            defaults = deepcopy(source)
            provider_type = self._single_line(defaults.get("type"), 80)
            if not provider_type:
                continue
            if provider_type == "fishaudio_tts_api":
                defaults.setdefault("model", "s2.1-pro-free")

            fields: list[dict[str, Any]] = []
            for key, default in defaults.items():
                if key in TTS_PROVIDER_SYSTEM_KEYS:
                    continue
                metadata = deepcopy(field_metadata.get(key, {}) or {})
                if provider_type == "fishaudio_tts_api" and key == "model":
                    metadata.update(
                        {
                            "type": "string",
                            "description": "Fish Audio 模型",
                            "hint": "S2/S2.1 使用方括号自然语言情绪控制；S1 使用旧版圆括号控制。",
                            "options": deepcopy(FISH_AUDIO_MODEL_OPTIONS),
                        }
                    )
                options = metadata.get("options") if isinstance(metadata.get("options"), list) else []
                option_labels = metadata.get("labels") if isinstance(metadata.get("labels"), list) else []
                normalized_options: list[dict[str, str]] = []
                for index, option in enumerate(options):
                    if isinstance(option, dict):
                        value = str(option.get("value", "") or "")
                        label = str(option.get("label", value) or value)
                    else:
                        value = str(option or "")
                        label = str(option_labels[index] or value) if index < len(option_labels) else value
                    normalized_options.append({"value": value, "label": label})
                slider = metadata.get("slider") if isinstance(metadata.get("slider"), dict) else {}
                fallback_label = self._tts_provider_fallback_label(key)
                raw_label = self._single_line(metadata.get("description"), 120)
                if (
                    not raw_label
                    or self._tts_provider_metadata_unresolved(raw_label)
                    or (re.search(r"[\u4e00-\u9fff]", fallback_label) and not re.search(r"[\u4e00-\u9fff]", raw_label))
                ):
                    raw_label = fallback_label
                raw_hint = self._multi_line(metadata.get("hint"), 600)
                if self._tts_provider_metadata_unresolved(raw_hint):
                    raw_hint = self._tts_provider_fallback_hint(key)
                fields.append(
                    {
                        "key": key,
                        "label": raw_label,
                        "type": self._tts_provider_field_type(default, metadata),
                        "hint": raw_hint,
                        "options": normalized_options,
                        "default": deepcopy(default),
                        "secret": self._tts_provider_secret_field(key),
                        "group": self._tts_provider_field_group(key),
                        "min": metadata.get("min", slider.get("min")),
                        "max": metadata.get("max", slider.get("max")),
                        "step": metadata.get("step", slider.get("step")),
                    }
                )

            template = {
                "name": self._single_line(display_name, 120),
                "type": provider_type,
                "provider": self._single_line(defaults.get("provider"), 80),
                "hint": self._multi_line(defaults.get("hint"), 600),
                "default_id": self._single_line(defaults.get("id"), 80),
                "defaults": {
                    key: deepcopy(value)
                    for key, value in defaults.items()
                    if key not in {"hint"}
                },
                "fields": fields,
            }
            templates.append(template)
            by_type[provider_type] = template

        templates.sort(key=lambda item: (item["name"].lower(), item["type"]))
        return {"templates": templates, "by_type": by_type}

    def _tts_provider_runtime_configs(self) -> list[dict[str, Any]]:
        manager = self._tts_provider_manager()
        configs = list(getattr(manager, "providers_config", None) or [])
        return [deepcopy(item) for item in configs if self._is_tts_provider_config(item)]

    def _serialize_tts_provider_config(
        self,
        config: dict[str, Any],
        schema_bundle: dict[str, Any],
    ) -> dict[str, Any]:
        manager = self._tts_provider_manager()
        provider_id = self._single_line(config.get("id"), 160)
        provider_type = self._single_line(config.get("type"), 80)
        template = schema_bundle.get("by_type", {}).get(provider_type, {})
        merged = deepcopy(config)
        merger = getattr(manager, "get_merged_provider_config", None)
        if callable(merger):
            try:
                merged = merger(config)
            except Exception:
                merged = deepcopy(config)

        fields = deepcopy(template.get("fields", []) or [])
        known_keys = {str(field.get("key") or "") for field in fields}
        for key, value in merged.items():
            if key in TTS_PROVIDER_SYSTEM_KEYS or key in known_keys:
                continue
            fields.append(
                {
                    "key": key,
                    "label": self._tts_provider_fallback_label(key),
                    "type": self._tts_provider_field_type(value, {}),
                    "hint": "",
                    "options": [],
                    "default": "",
                    "secret": self._tts_provider_secret_field(key),
                    "group": self._tts_provider_field_group(key),
                }
            )

        values: dict[str, Any] = {}
        secret_configured: dict[str, bool] = {}
        for field in fields:
            key = str(field.get("key") or "")
            value = deepcopy(merged.get(key, field.get("default", "")))
            if field.get("secret"):
                secret_configured[key] = value not in (None, "", [], {})
                value = ""
            values[key] = value

        loaded = provider_id in (getattr(manager, "inst_map", {}) or {})
        using_id = ""
        context = getattr(self.plugin, "context", None)
        getter = getattr(context, "get_using_tts_provider", None)
        if callable(getter):
            try:
                using_id = self._provider_id(getter())
            except Exception:
                using_id = ""
        return {
            "id": provider_id,
            "name": self._single_line(template.get("name"), 120) or provider_id,
            "type": provider_type,
            "provider": self._single_line(config.get("provider"), 80),
            "provider_source_id": self._single_line(config.get("provider_source_id"), 160),
            "enable": bool(config.get("enable", False)),
            "loaded": loaded,
            "is_default": provider_id == using_id,
            "model": self._single_line(merged.get("model") or merged.get("gemini_tts_model"), 160),
            "values": values,
            "secret_configured": secret_configured,
            "fields": fields,
        }

    def _tts_provider_management_payload(self) -> dict[str, Any]:
        schema_bundle = self._tts_provider_schema_bundle()
        items = [
            self._serialize_tts_provider_config(config, schema_bundle)
            for config in self._tts_provider_runtime_configs()
        ]
        items.sort(key=lambda item: (not item["enable"], not item["loaded"], item["name"].lower(), item["id"].lower()))
        return {
            "items": items,
            "templates": schema_bundle.get("templates", []),
            "fish_audio_models": deepcopy(FISH_AUDIO_MODEL_OPTIONS),
            "total": len(items),
            "enabled": sum(1 for item in items if item.get("enable")),
            "loaded": sum(1 for item in items if item.get("loaded")),
        }

    @staticmethod
    def _coerce_tts_provider_field(value: Any, field: dict[str, Any]) -> Any:
        field_type = str(field.get("type") or "string").lower()
        if field_type == "bool":
            if isinstance(value, bool):
                return value
            return str(value or "").strip().lower() in {"1", "true", "yes", "on"}
        if field_type == "int":
            try:
                return int(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{field.get('label') or field.get('key')} 必须是整数") from exc
        if field_type == "float":
            try:
                return float(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{field.get('label') or field.get('key')} 必须是数字") from exc
        if field_type in {"object", "list"}:
            parsed = value
            if isinstance(value, str):
                text = value.strip()
                if not text:
                    return {} if field_type == "object" else []
                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{field.get('label') or field.get('key')} 不是有效 JSON") from exc
            if field_type == "object" and not isinstance(parsed, dict):
                raise ValueError(f"{field.get('label') or field.get('key')} 必须是 JSON 对象")
            if field_type == "list" and not isinstance(parsed, list):
                raise ValueError(f"{field.get('label') or field.get('key')} 必须是 JSON 数组")
            return parsed
        return str(value or "").strip()[:12000]

    def _normalized_tts_provider_update(
        self,
        current: dict[str, Any],
        incoming: dict[str, Any],
        schema_bundle: dict[str, Any],
    ) -> dict[str, Any]:
        provider_type = self._single_line(current.get("type"), 80)
        template = schema_bundle.get("by_type", {}).get(provider_type, {})
        fields = list(template.get("fields", []) or [])
        field_map = {str(field.get("key") or ""): field for field in fields}
        for key, value in current.items():
            if key in TTS_PROVIDER_SYSTEM_KEYS or key in field_map:
                continue
            field_map[key] = {
                "key": key,
                "label": self._tts_provider_fallback_label(key),
                "type": self._tts_provider_field_type(value, {}),
                "secret": self._tts_provider_secret_field(key),
            }

        normalized = deepcopy(current)
        if "enable" in incoming:
            normalized["enable"] = self._coerce_tts_provider_field(
                incoming.get("enable"), {"key": "enable", "label": "启用", "type": "bool"}
            )
        values = incoming.get("values") if isinstance(incoming.get("values"), dict) else {}
        for key, value in values.items():
            field = field_map.get(str(key))
            if not field:
                continue
            if field.get("secret") and value in (None, ""):
                continue
            normalized[str(key)] = self._coerce_tts_provider_field(value, field)

        if provider_type == "fishaudio_tts_api" and "model" in values:
            model = str(normalized.get("model") or "").strip().lower()
            allowed = {str(item["value"]) for item in FISH_AUDIO_MODEL_OPTIONS}
            if model not in allowed:
                raise ValueError("Fish Audio 模型不在支持列表中")
            normalized["model"] = model
        normalized["id"] = self._single_line(current.get("id"), 160)
        normalized["type"] = provider_type
        normalized["provider_type"] = "text_to_speech"
        return normalized

    async def list_tts_provider_configs(self) -> dict[str, Any]:
        try:
            return self._ok(self._tts_provider_management_payload())
        except Exception as exc:
            logger.error("获取 TTS Provider 配置失败: %s", exc, exc_info=True)
            return self._error(self._single_line(exc, 240))

    async def create_tts_provider_config(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        provider_type = self._single_line(payload.get("type"), 80)
        provider_id = self._single_line(payload.get("id"), 80)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,79}", provider_id):
            return self._error("Provider ID 只能包含字母、数字、点、下划线、冒号和短横线")
        try:
            manager = self._tts_provider_manager()
            schema_bundle = self._tts_provider_schema_bundle()
            template = schema_bundle.get("by_type", {}).get(provider_type)
            if not isinstance(template, dict):
                return self._error("不支持该 TTS Provider 类型")
            config = deepcopy(template.get("defaults", {}) or {})
            config.update(
                {
                    "id": provider_id,
                    "type": provider_type,
                    "provider": self._single_line(template.get("provider"), 80),
                    "provider_type": "text_to_speech",
                    "enable": False,
                }
            )
            if provider_type == "fishaudio_tts_api":
                config["model"] = "s2.1-pro-free"
            creator = getattr(manager, "create_provider", None)
            if not callable(creator):
                return self._error("当前 AstrBot 不支持动态创建 Provider")
            await creator(config)
            return self._ok(self._tts_provider_management_payload())
        except Exception as exc:
            return self._error(self._single_line(_redact_outbound_secrets(str(exc)), 240))

    @staticmethod
    def _tts_language_clone_provider_id(
        source_provider_id: str,
        language: str,
        existing_ids: set[str],
    ) -> str:
        source = re.sub(r"[^A-Za-z0-9._:-]+", "-", str(source_provider_id or "")).strip("._:-") or "tts"
        language = language if language in {"zh", "ja", "en"} else "voice"
        existing_lower = {str(item or "").lower() for item in existing_ids}
        for index in range(1, 1000):
            suffix = f"-{language}" if index == 1 else f"-{language}-{index}"
            base = source[: max(1, 80 - len(suffix))].rstrip("._:-") or "tts"
            candidate = f"{base}{suffix}"
            if candidate.lower() not in existing_lower:
                return candidate
        raise ValueError("无法生成不重复的语种专用 Provider ID")

    async def clone_tts_provider_config(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        source_provider_id = self._single_line(payload.get("source_provider_id"), 160)
        language = self._single_line(payload.get("language"), 8).lower()
        incoming = payload.get("config") if isinstance(payload.get("config"), dict) else {}
        if not source_provider_id:
            return self._error("缺少源 TTS Provider ID")
        if language not in {"zh", "ja", "en"}:
            return self._error("语种必须是 zh、ja 或 en")
        try:
            manager = self._tts_provider_manager()
            getter = getattr(manager, "get_provider_config_by_id", None)
            current = getter(source_provider_id) if callable(getter) else next(
                (deepcopy(item) for item in self._tts_provider_runtime_configs() if item.get("id") == source_provider_id),
                None,
            )
            if not self._is_tts_provider_config(current):
                return self._error("源 TTS Provider 不存在")
            runtime_configs = self._tts_provider_runtime_configs()
            clone_id = self._tts_language_clone_provider_id(
                source_provider_id,
                language,
                {self._single_line(item.get("id"), 160) for item in runtime_configs},
            )
            normalized = self._normalized_tts_provider_update(
                current,
                incoming,
                self._tts_provider_schema_bundle(),
            )
            normalized["id"] = clone_id
            creator = getattr(manager, "create_provider", None)
            if not callable(creator):
                return self._error("当前 AstrBot 不支持动态创建 Provider")
            await creator(normalized)
            result = self._tts_provider_management_payload()
            result.update(
                {
                    "provider_id": clone_id,
                    "source_provider_id": source_provider_id,
                    "language": language,
                }
            )
            logger.info(
                "已复制语种专用 TTS Provider: language=%s source=%s clone=%s",
                language,
                source_provider_id,
                clone_id,
            )
            return self._ok(result)
        except Exception as exc:
            logger.warning(
                "复制语种专用 TTS Provider 失败: language=%s source=%s error=%s",
                language,
                source_provider_id,
                self._single_line(_redact_outbound_secrets(str(exc)), 240),
            )
            return self._error(self._single_line(_redact_outbound_secrets(str(exc)), 240))

    async def update_tts_provider_config(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        provider_id = self._single_line(payload.get("provider_id"), 160)
        incoming = payload.get("config") if isinstance(payload.get("config"), dict) else {}
        if not provider_id:
            return self._error("缺少 TTS Provider ID")
        try:
            manager = self._tts_provider_manager()
            getter = getattr(manager, "get_provider_config_by_id", None)
            current = getter(provider_id) if callable(getter) else next(
                (deepcopy(item) for item in self._tts_provider_runtime_configs() if item.get("id") == provider_id),
                None,
            )
            if not self._is_tts_provider_config(current):
                return self._error("TTS Provider 不存在")
            schema_bundle = self._tts_provider_schema_bundle()
            normalized = self._normalized_tts_provider_update(current, incoming, schema_bundle)
            if normalized == current:
                return self._ok(self._tts_provider_management_payload())
            updater = getattr(manager, "update_provider", None)
            if not callable(updater):
                return self._error("当前 AstrBot 不支持动态更新 Provider")
            await updater(provider_id, normalized)
            return self._ok(self._tts_provider_management_payload())
        except Exception as exc:
            logger.warning(
                "TTS Provider 保存失败: provider=%s error=%s",
                provider_id,
                self._single_line(_redact_outbound_secrets(str(exc)), 240),
            )
            return self._error(self._single_line(_redact_outbound_secrets(str(exc)), 240))

    async def test_tts_provider_config(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        provider_id = self._single_line(payload.get("provider_id"), 160)
        request_id = secrets.token_hex(6)
        start = time.time()
        logger.info("[test:%s][type:tts_provider_connection] 开始执行测试", request_id)
        try:
            manager = self._tts_provider_manager()
            config_getter = getattr(manager, "get_provider_config_by_id", None)
            config = config_getter(provider_id) if callable(config_getter) else None
            if not self._is_tts_provider_config(config):
                result = {
                    "ok": False,
                    "provider_id": provider_id,
                    "error": "TTS Provider 不存在",
                    "steps": [{"name": "配置检查", "status": "error", "detail": "没有找到对应的 TTS Provider 配置"}],
                }
            else:
                provider = (getattr(manager, "inst_map", {}) or {}).get(provider_id)
                if provider is None:
                    result = {
                        "ok": False,
                        "provider_id": provider_id,
                        "error": "Provider 尚未启用或加载失败，请先保存并启用",
                        "steps": [
                            {"name": "配置检查", "status": "ok", "detail": "已找到 TTS Provider 配置"},
                            {"name": "实例加载", "status": "error", "detail": "Provider 尚未启用或实例加载失败"},
                        ],
                    }
                else:
                    tester = getattr(provider, "test", None)
                    if not callable(tester):
                        result = {
                            "ok": False,
                            "provider_id": provider_id,
                            "error": "该 Provider 不支持测试",
                            "steps": [
                                {"name": "配置检查", "status": "ok", "detail": "已找到并加载 TTS Provider"},
                                {"name": "自检能力", "status": "error", "detail": "Provider 没有提供 test() 自检入口"},
                            ],
                        }
                    else:
                        call_started = time.time()
                        await asyncio.wait_for(tester(), timeout=90.0)
                        result = {
                            "ok": True,
                            "provider_id": provider_id,
                            "detail": "TTS Provider 自检调用完成",
                            "steps": [
                                {"name": "配置检查", "status": "ok", "detail": "已找到并加载 TTS Provider"},
                                {
                                    "name": "Provider 自检",
                                    "status": "ok",
                                    "detail": "test() 调用成功完成",
                                    "elapsed_ms": int((time.time() - call_started) * 1000),
                                },
                            ],
                        }
        except Exception as exc:
            safe_error = self._safe_test_diagnostic_text(exc, 1600)
            if isinstance(exc, asyncio.TimeoutError) and not safe_error:
                safe_error = "TTS Provider 自检超过 90 秒仍未完成"
            result = {
                "ok": False,
                "provider_id": provider_id,
                "error": safe_error or "TTS Provider 自检失败",
                "exception_type": exc.__class__.__name__,
            }
        result["elapsed_ms"] = int((time.time() - start) * 1000)
        result["request_id"] = request_id
        result = self._finalize_test_diagnostics(
            "tts_provider_connection",
            result,
            start,
            title="TTS Provider 连接测试",
        )
        logger.info(
            "[test:%s][type:tts_provider_connection] 测试结束: status=%s elapsed_ms=%s",
            request_id,
            result.get("test_status"),
            result.get("elapsed_ms"),
        )
        return self._ok(result)

    def _tts_runtime_summary(self, users: dict[str, Any]) -> dict[str, Any]:
        umo = self._preferred_tts_test_umo(
            users,
            owner_only=True,
            resolve_delivery_route=True,
        )
        config: dict[str, Any] = {}
        provider_settings: dict[str, Any] = {}
        provider = None
        context = getattr(self.plugin, "context", None)
        if context is not None:
            getter = getattr(context, "get_config", None)
            if callable(getter):
                try:
                    config = getter(umo) if umo else getter()
                    if not isinstance(config, dict):
                        config = {}
                except Exception:
                    config = {}
            provider_settings = dict((config or {}).get("provider_tts_settings", {}) or {})
            provider_getter = getattr(context, "get_using_tts_provider", None)
            if callable(provider_getter):
                try:
                    provider = provider_getter(umo) if umo else provider_getter()
                except Exception:
                    provider = None
        synthesis_backend = self._single_line(
            getattr(self.plugin, "tts_synthesis_backend", ""),
            32,
        ) or "astrbot_provider"
        synthesis_resolver = getattr(self.plugin, "_resolve_tts_synthesis_provider", None)
        effective_provider = provider
        if callable(synthesis_resolver):
            try:
                effective_provider = synthesis_resolver(None, provider)
            except Exception:
                effective_provider = provider
        mimo_adapter = (
            effective_provider
            if effective_provider is not None
            and effective_provider.__class__.__name__ == "_MimoVoiceCloneTtsAdapter"
            else None
        )
        provider_label = ""
        if mimo_adapter is not None:
            provider_label = "MiMo TTS Voice Clone 插件"
        elif effective_provider is not None:
            provider_id = self._provider_id(effective_provider)
            provider_label = self._provider_name(effective_provider, provider_id) if provider_id else getattr(effective_provider, "__class__", type(effective_provider)).__name__
        return {
            "enhancement_enabled": bool(getattr(self.plugin, "enable_tts_enhancement", False)),
            "synthesis_backend": synthesis_backend,
            "mimo_voice_clone_available": mimo_adapter is not None,
            "mimo_tool_name": self._single_line(
                getattr(self.plugin, "tts_mimo_tool_name", ""),
                120,
            ) or "mimo_tts_speak",
            "mode": self._single_line(getattr(self.plugin, "tts_generation_mode", ""), 24) or "fast_tag",
            "language": self.plugin._tts_language_label() if hasattr(self.plugin, "_tts_language_label") else "",
            "fishaudio_model": self._single_line(getattr(self.plugin, "tts_fishaudio_model", ""), 32) or "auto",
            "fishaudio_emotion_mode": self._single_line(
                getattr(self.plugin, "tts_fishaudio_emotion_mode", ""),
                24,
            ) or "balanced",
            "delivery_mode": self._single_line(getattr(self.plugin, "tts_delivery_mode", ""), 32) or "voice_and_text",
            "foreign_text_mode": self._single_line(getattr(self.plugin, "tts_foreign_text_mode", ""), 32) or "translation",
            "message_scope": self._single_line(getattr(self.plugin, "tts_message_scope", ""), 32) or "replies_only",
            "conversion_scope": self._single_line(getattr(self.plugin, "tts_conversion_scope", ""), 24) or "partial",
            "umo": umo,
            "settings_enabled": bool(provider_settings.get("enable", False)) or synthesis_backend == "mimo_voice_clone",
            "provider_available": effective_provider is not None,
            "provider_label": self._single_line(provider_label, 80) or "未知 provider",
        }

