# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import base64
import hashlib
import html
import io
import json
import os
import re
import shutil
import tempfile
import time
import urllib.request
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlparse, urlsplit, urlunparse, urlunsplit

from astrbot.api.event import AstrMessageEvent
try:
    from astrbot.api.message_components import Image, Plain
except ImportError:
    from astrbot.api.message_components import Image, Plain
from astrbot.api.provider import ProviderRequest
from astrbot.core.agent.message import AssistantMessageSegment, TextPart, UserMessageSegment
from astrbot.core import file_token_service
from astrbot.core.astr_main_agent import MainAgentBuildConfig, build_main_agent
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .conversation_injection_plan import (
    PLACEMENT_DYNAMIC_SYSTEM,
    get_conversation_injection_plan,
)
from .conversation_prompt_section import (
    PromptRenderMode,
    PromptSection,
    prompt_section,
    exact_text,
    render_prompt_sections,
)
from .helpers import _missing_optional_model_dependency, _safe_float, _safe_int, _single_line, _strip_internal_message_blocks, _strip_outbound_control_blocks, _today_key, _url_host_is_public
from .persona_config import runtime_persona_setting
from .segmented_message import (
    component_kind,
    component_order_from_owner,
    component_strategies_from_owner,
    plan_component_chunks,
    sanitize_llm_segment_control_tokens,
)
from .logging_util import get_module_logger
from .private_image_transcribe_group import PrivateImageTranscribeGroupMixin
from .private_image_reply_send import PrivateImageReplySendMixin
from .private_image_persona_visual import PrivateImagePersonaVisualMixin
from .private_image_placeholder_buffer import PrivateImagePlaceholderBufferMixin
from .private_image_review_delivery import PrivateImageReviewDeliveryMixin
from .private_image_provider_governance import PrivateImageProviderGovernanceMixin
from .private_image_model_capability import PrivateImageModelCapabilityMixin

from .private_image_shared import logger, _private_image_host, PREPARED_IMAGE_MAX_AGE_SECONDS, CONTEXT_IMAGE_FAILURE_COOLDOWN_SECONDS


class _PublicOnlyRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Re-check redirects so a public image URL cannot pivot into local networks."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        if not _url_host_is_public(newurl):
            logger.warning(
                "remote image redirect rejected: url=%s",
                _single_line(newurl, 160),
            )
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class PrivateImageMixin(PrivateImageModelCapabilityMixin, PrivateImageProviderGovernanceMixin, PrivateImageReviewDeliveryMixin, PrivateImagePlaceholderBufferMixin, PrivateImagePersonaVisualMixin, PrivateImageReplySendMixin, PrivateImageTranscribeGroupMixin):
    """Methods split from main.PrivateCompanionPlugin."""

    def _private_image_setting(self, key: str, default: Any = None) -> Any:
        """Read a config key in the active persona without mutating shared attrs."""
        return runtime_persona_setting(self, key, default)

    @staticmethod
    def _register_materialized_private_image_context(
        req: ProviderRequest,
        *,
        section: PromptSection,
        marker: str = "",
        priority: int,
    ) -> bool:
        plan = get_conversation_injection_plan(req)
        if plan is None:
            raise RuntimeError("conversation injection plan is unavailable")
        if marker and plan.contains_marker(marker):
            return False
        return plan.materialize_system_block(
            req,
            section=section,
            marker=marker,
            priority=priority,
            placement=PLACEMENT_DYNAMIC_SYSTEM,
        )

    def _private_image_framework_context(self) -> Any | None:
        resolver = getattr(self, "_proactive_framework_context", None)
        if callable(resolver):
            return resolver()
        return getattr(self, "context", None)

    def _private_event_has_image(self, event: AstrMessageEvent) -> bool:
        for comp in self._event_components(event):
            class_name = comp.__class__.__name__.lower()
            if isinstance(comp, dict):
                class_name = str(comp.get("type") or "").lower()
            if class_name == "image":
                return True
        return bool(self._raw_private_image_sources(event))

    def _private_event_has_image_safe(self, event: AstrMessageEvent, *, label: str = "") -> bool:
        try:
            return self._private_event_has_image(event)
        except Exception as exc:
            missing = _missing_optional_model_dependency(exc)
            if missing:
                logger.warning(
                    "私聊图片存在性检测缺少可选模型依赖，已按无图片继续: label=%s module=%s err=%s",
                    _single_line(label, 40) or "-",
                    missing,
                    _single_line(exc, 160),
                )
                return False
            logger.warning(
                "私聊图片存在性检测失败，已按无图片继续: label=%s err=%s",
                _single_line(label, 40) or "-",
                _single_line(exc, 160),
            )
            return False

    def _private_event_has_nontext_content(self, event: AstrMessageEvent) -> bool:
        """Keep non-text message segments available to AstrBot's default chain.

        File-only private messages commonly have an empty ``message_str``. They
        must not be mistaken for an empty adapter event, otherwise the
        companion's empty-message guard prevents the framework and file-aware
        tools from receiving the attachment at all.
        """
        try:
            components = self._event_components(event)
        except Exception:
            return False
        for component in components:
            if isinstance(component, dict):
                type_name = str(component.get("type") or component.get("post_type") or "").strip().lower()
            else:
                type_name = component.__class__.__name__.strip().lower()
            if type_name and type_name not in {"plain", "text"}:
                return True
        return False

    def _is_private_image_only_message(self, event: AstrMessageEvent, text: str) -> bool:
        cleaned = _single_line(text, 120)
        if cleaned and cleaned not in {"[图片]", "【图片】", "图片"}:
            return False
        components = self._event_components(event)
        if not components:
            return False
        has_image = False
        for comp in components:
            class_name = comp.__class__.__name__.lower()
            if class_name == "image":
                has_image = True
                continue
            if class_name in {"at", "reply"}:
                continue
            comp_text = _single_line(
                getattr(comp, "text", "")
                or getattr(comp, "message", "")
                or getattr(comp, "content", ""),
                120,
            )
            if comp_text and comp_text not in {"[图片]", "【图片】", "图片"}:
                return False
        return has_image

    def _image_component_source(self, comp: Any) -> str:
        data = getattr(comp, "data", None)
        if not isinstance(data, dict):
            data = comp.get("data") if isinstance(comp, dict) and isinstance(comp.get("data"), dict) else {}
        candidates: list[Any] = []
        for source in (data, comp if isinstance(comp, dict) else None):
            if not isinstance(source, dict):
                continue
            nested = source.get("data")
            if isinstance(nested, dict):
                candidates.append(nested)
            candidates.append(source)
        attrs = (
            "url",
            "origin_url",
            "source_url",
            "src",
            "path",
            "image_path",
            "file_path",
            "local_path",
            "file",
        )
        for attr in attrs:
            for candidate in candidates:
                value = candidate.get(attr)
                text = str(value or "").strip()
                if text:
                    return text
            value = getattr(comp, attr, None)
            text = str(value or "").strip()
            if text:
                return text
        return ""

    def _raw_private_image_sources(self, event: AstrMessageEvent) -> list[str]:
        message_obj = getattr(event, "message_obj", None)
        raw_values = [
            getattr(message_obj, "raw_message", None) if message_obj is not None else None,
            getattr(message_obj, "message", None) if message_obj is not None else None,
            getattr(event, "message_str", None),
        ]
        sources: list[str] = []

        def add(value: Any) -> None:
            text = str(value or "").strip()
            if text and text not in sources:
                sources.append(text)

        def visit(value: Any) -> None:
            if isinstance(value, list):
                for item in value:
                    visit(item)
                return
            if isinstance(value, dict):
                item_type = str(value.get("type") or value.get("post_type") or "").lower()
                data = value.get("data") if isinstance(value.get("data"), dict) else value
                if item_type == "image":
                    add(self._extract_image_url_from_segment_data(data))
                    for key in ("url", "origin_url", "source_url", "path", "image_path", "file_path", "local_path", "file"):
                        add(data.get(key))
                for key in ("message", "messages", "content", "data"):
                    nested = value.get(key)
                    if nested is not value:
                        visit(nested)
                return
            raw_text = str(value or "")
            for match in re.finditer(r"\[CQ:image,([^\]]+)\]", raw_text):
                fields: dict[str, str] = {}
                for part in match.group(1).split(","):
                    if "=" not in part:
                        continue
                    key, val = part.split("=", 1)
                    fields[key.strip()] = html.unescape(val.strip())
                add(self._extract_image_url_from_segment_data(fields))
                for key in ("url", "path", "file"):
                    add(fields.get(key))

        for raw in raw_values:
            visit(raw)
        return [source for source in sources if source]

    def _private_image_local_path_is_allowed(self, path: Path) -> bool:
        """Allow image files only from plugin, AstrBot, or temporary storage roots."""
        try:
            resolved = path.resolve()
        except Exception:
            return False
        roots: list[Path] = []
        for candidate in (getattr(self, "data_dir", ""), tempfile.gettempdir()):
            if candidate:
                try:
                    roots.append(Path(candidate).resolve())
                except Exception:
                    continue
        try:
            astrbot_root = Path(_private_image_host.get_astrbot_data_path()).resolve()
        except Exception:
            astrbot_root = None
        if astrbot_root is not None:
            roots.append(astrbot_root)
        for root in roots:
            try:
                if resolved.is_relative_to(root):
                    return True
            except AttributeError:
                if str(resolved) == str(root) or str(resolved).startswith(str(root) + os.sep):
                    return True
        return False

    @staticmethod
    def _private_image_local_path_from_source(source: Any) -> Path | None:
        """Normalize plain and file-URI paths, including Windows drive URIs."""

        text = str(source or "").strip().strip('"')
        if not text:
            return None
        if text.lower().startswith("file:"):
            try:
                parsed = urlsplit(text)
                path_text = unquote(parsed.path or "")
                netloc = unquote(parsed.netloc or "")
                if re.fullmatch(r"[A-Za-z]:", netloc):
                    path_text = netloc + path_text
                elif netloc and netloc.lower() != "localhost":
                    path_text = f"//{netloc}{path_text}"
                if os.name == "nt" and re.match(r"^/[A-Za-z]:[\\/]", path_text):
                    path_text = path_text[1:]
                text = path_text
            except (UnicodeError, ValueError):
                return None
        else:
            text = unquote(text)
        try:
            return Path(text).expanduser()
        except (OSError, ValueError):
            return None

    async def _persist_private_inbound_images(self, event: AstrMessageEvent, user_id: str) -> list[str]:
        # Image files are private user state. Resolve the sender against the
        # current platform/adapter/bot account before choosing the debounce
        # directory so equal raw IDs cannot share cached media.
        resolver = getattr(self, "_private_user_id_for_event", None)
        if callable(resolver):
            try:
                raw_sender = event.get_sender_id()
            except Exception:
                raw_sender = ""
            if raw_sender:
                try:
                    scoped = _single_line(resolver(event, raw_sender), 160)
                except Exception:
                    scoped = ""
                if scoped:
                    user_id = scoped
        result: list[str] = []
        target_dir = Path(self.data_dir) / "private_inbound_images" / re.sub(r"[^0-9A-Za-z_.-]+", "_", str(user_id or "unknown"))
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            return result
        now_ms = int(_private_image_host._now_ts() * 1000)

        async def resolve_source(comp: Any) -> str:
            source = self._image_component_source(comp)
            if source:
                return source
            converter = getattr(comp, "convert_to_file_path", None)
            if callable(converter):
                try:
                    maybe = converter()
                    return str(await maybe if hasattr(maybe, "__await__") else maybe or "").strip()
                except Exception as exc:
                    logger.debug("私聊图片组件转换失败: %s", exc)
            return ""

        for index, comp in enumerate(self._event_components(event), 1):
            class_name = comp.__class__.__name__.lower()
            if isinstance(comp, dict):
                class_name = str(comp.get("type") or "").lower()
            if class_name != "image":
                continue
            source = await resolve_source(comp)
            if not source:
                data = getattr(comp, "data", None)
                data_keys = ",".join(sorted(str(key) for key in data.keys())) if isinstance(data, dict) else ""
                logger.info(
                    "私聊图片组件未能解析出文件路径: class=%s data_keys=%s",
                    comp.__class__.__name__,
                    data_keys or "-",
                )
                continue
            source_path = Path(source)
            if source_path.exists() and source_path.is_file():
                if not self._private_image_local_path_is_allowed(source_path):
                    logger.warning(
                        "private image local path rejected: path=%s",
                        _single_line(source, 200),
                    )
                    continue
                suffix = source_path.suffix.lower() if source_path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".gif"} else ".jpg"
                target = target_dir / f"{now_ms}_{index}{suffix}"
                try:
                    shutil.copy2(source_path, target)
                    result.append(str(target))
                    continue
                except Exception as exc:
                    logger.debug("私聊图片暂存失败: %s", exc)
            if re.match(r"^https?://", source, flags=re.I):
                persisted = await self._persist_private_remote_image_source(
                    source,
                    target_dir,
                    f"{now_ms}_{index}",
                    public_hosts_only=True,
                )
                if persisted:
                    result.append(persisted)
                    continue
            if re.match(r"^(?:data|file|base64)://", source, flags=re.I):
                result.append(source)
        if not result:
            for source in self._raw_private_image_sources(event):
                if not source or source in result:
                    continue
                persisted = await self._persist_private_remote_image_source(
                    source,
                    target_dir,
                    f"{now_ms}_raw_{len(result) + 1}",
                    public_hosts_only=True,
                )
                if persisted:
                    result.append(persisted)
                    continue
                if re.match(r"^https?://", source, flags=re.I):
                    continue
                if self._private_image_source_to_model_url(source):
                    result.append(source)
        return result

    async def _persist_private_remote_image_source(
        self,
        source: str,
        target_dir: Path,
        stem: str,
        *,
        public_hosts_only: bool = False,
    ) -> str:
        text = str(source or "").strip()
        if not re.match(r"^https?://", text, flags=re.I):
            return ""
        if public_hosts_only and not await asyncio.to_thread(_private_image_host._url_host_is_public, text):
            logger.warning(
                "remote image host rejected: url=%s",
                _single_line(text, 160),
            )
            return ""

        request_url = self._private_image_request_url(text)
        if not request_url:
            return ""

        def download() -> str:
            try:
                request = urllib.request.Request(
                    request_url,
                    headers={
                        "User-Agent": "Mozilla/5.0 AstrBot PrivateCompanion/5.0.0",
                        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
                    },
                )
                opener = urllib.request.build_opener(_private_image_host._PublicOnlyRedirectHandler()) if public_hosts_only else None
                response_cm = opener.open(request, timeout=15) if opener is not None else urllib.request.urlopen(request, timeout=15)
                with response_cm as response:
                    content_type = str(response.headers.get("Content-Type") or "").lower()
                    length = _safe_int(response.headers.get("Content-Length"), 0, 0)
                    max_bytes = 12 * 1024 * 1024
                    if length and length > max_bytes:
                        logger.info("私聊远程图片过大,跳过下载: size=%s url=%s", length, _single_line(text, 120))
                        return ""
                    chunks: list[bytes] = []
                    total = 0
                    while True:
                        chunk = response.read(1024 * 256)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > max_bytes:
                            logger.info("私聊远程图片下载超过限制,已中止: url=%s", _single_line(text, 120))
                            return ""
                        chunks.append(chunk)
                data = b"".join(chunks)
                if not data:
                    logger.info("私聊远程图片响应为空,跳过: url=%s", _single_line(text, 120))
                    return ""
                prefix = data[:16]
                suffix = ".jpg"
                if prefix.startswith(b"\x89PNG\r\n\x1a\n") or "png" in content_type:
                    suffix = ".png"
                elif (prefix.startswith(b"RIFF") and b"WEBP" in data[:32]) or "webp" in content_type:
                    suffix = ".webp"
                elif prefix.startswith(b"GIF8") or "gif" in content_type:
                    suffix = ".gif"
                elif prefix.startswith(b"\xff\xd8\xff") or "jpeg" in content_type or "jpg" in content_type:
                    suffix = ".jpg"
                elif "image/" not in content_type:
                    logger.info("私聊远程图片响应不是图片,跳过: content_type=%s url=%s", content_type or "-", _single_line(text, 120))
                    return ""
                target = target_dir / f"{re.sub(r'[^0-9A-Za-z_.-]+', '_', stem)}{suffix}"
                target.write_bytes(data)
                return str(target)
            except Exception as exc:
                logger.warning("私聊远程图片下载失败: %s url=%s", _single_line(exc, 120), _single_line(text, 120))
                return ""

        return await asyncio.to_thread(download)

    @staticmethod
    def _private_image_request_url(source: str) -> str:
        text = str(source or "").strip()
        if not re.match(r"^https?://", text, flags=re.I):
            return ""
        try:
            parsed = urlsplit(text)
            hostname = str(parsed.hostname or "")
            if not hostname:
                return ""
            ascii_hostname = hostname.encode("idna").decode("ascii")
            host = f"[{ascii_hostname}]" if ":" in ascii_hostname and not ascii_hostname.startswith("[") else ascii_hostname
            if parsed.port is not None:
                host = f"{host}:{parsed.port}"
            userinfo = ""
            if parsed.username is not None:
                userinfo = quote(parsed.username, safe="%")
                if parsed.password is not None:
                    userinfo += f":{quote(parsed.password, safe='%')}"
                userinfo += "@"
            netloc = f"{userinfo}{host}"
            return urlunsplit((
                parsed.scheme.lower(),
                netloc,
                quote(parsed.path, safe="/%:@!$&'()*+,;=-._~"),
                quote(parsed.query, safe="=&%:@/?+;,!$'()*-._~"),
                quote(parsed.fragment, safe="=&%:@/?+;,!$'()*-._~"),
            ))
        except (UnicodeError, ValueError):
            return ""

    async def _prepare_private_image_sources_for_model(self, image_sources: list[str], *, namespace: str = "vision") -> list[str]:
        target_dir = Path(self.data_dir) / "private_inbound_images" / re.sub(r"[^0-9A-Za-z_.-]+", "_", str(namespace or "vision"))
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            return []
        self._sweep_stale_prepared_image_files(target_dir)
        prepared: list[str] = []
        now_ms = int(_private_image_host._now_ts() * 1000)
        for index, source in enumerate([str(item).strip() for item in (image_sources or []) if str(item or "").strip()][:12], 1):
            if re.match(r"^https?://", source, flags=re.I):
                persisted = await self._persist_private_remote_image_source(
                    source,
                    target_dir,
                    f"{now_ms}_{index}",
                    public_hosts_only=True,
                )
                if persisted and persisted not in prepared:
                    prepared.append(persisted)
                continue
            local_path = self._private_image_local_path_from_source(source)
            normalized_source = str(local_path) if local_path is not None else source
            if not self._private_image_source_to_model_url(normalized_source):
                logger.info(
                    "本地图片源不可读,已跳过: namespace=%s source=%s",
                    namespace,
                    _single_line(source, 160),
                )
                continue
            if normalized_source not in prepared:
                prepared.append(normalized_source)
        return prepared

    def _sweep_stale_prepared_image_files(self, target_dir: Path) -> int:
        """Remove stale downloaded images left behind by cancellation or errors."""
        removed = 0
        try:
            deadline = _private_image_host._now_ts() - PREPARED_IMAGE_MAX_AGE_SECONDS
            for path in target_dir.iterdir():
                try:
                    if path.is_file() and path.stat().st_mtime < deadline:
                        path.unlink(missing_ok=True)
                        removed += 1
                except Exception:
                    continue
        except Exception:
            return removed
        if removed:
            logger.info("stale prepared images removed: dir=%s removed=%s", target_dir.name, removed)
        return removed

    def _cleanup_prepared_image_sources(self, sources: list[str], *, namespace: str) -> None:
        """Remove only temporary files downloaded into this plugin's vision namespace."""
        try:
            base = (
                Path(self.data_dir)
                / "private_inbound_images"
                / re.sub(r"[^0-9A-Za-z_.-]+", "_", str(namespace or "vision"))
            ).resolve()
        except Exception:
            return
        for source in sources or []:
            text = str(source or "").strip()
            if not text or text.startswith(("data:", "base64://", "http://", "https://")):
                continue
            if text.startswith("file://"):
                text = text[len("file://"):]
            try:
                path = Path(text).resolve()
                if path.is_file() and path.is_relative_to(base):
                    path.unlink(missing_ok=True)
            except Exception:
                continue

    def _private_image_sources_for_astrbot_request(self, image_sources: list[str]) -> list[str]:
        refs: list[str] = []
        for source in [str(item).strip() for item in (image_sources or []) if str(item or "").strip()][:5]:
            text = source
            if text.startswith("data:") or text.startswith("base64://"):
                continue
            if re.match(r"^https?://", text, flags=re.I):
                continue
            path = self._private_image_local_path_from_source(text)
            if path is None:
                continue
            if not path.exists() or not path.is_file() or not self._private_image_local_path_is_allowed(path):
                continue
            ref = str(path.resolve())
            if ref not in refs:
                refs.append(ref)
        return refs

    def _private_image_source_to_model_url(self, source: str) -> str:
        text = str(source or "").strip()
        if not text:
            return ""
        if re.match(r"^https?://", text, flags=re.I) or text.startswith("data:"):
            return text
        if text.startswith("base64://"):
            return f"data:image/jpeg;base64,{text[len('base64://'):]}"
        path = self._private_image_local_path_from_source(text)
        if path is None:
            return ""
        if not path.exists() or not path.is_file():
            return ""
        if not self._private_image_local_path_is_allowed(path):
            return ""
        suffix = path.suffix.lower()
        mime = "image/png" if suffix == ".png" else "image/webp" if suffix == ".webp" else "image/gif" if suffix == ".gif" else "image/jpeg"
        try:
            return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"
        except Exception as exc:
            logger.debug("私聊图片转 data url 失败: %s", exc)
            return ""

    def _private_image_source_cache_key(self, source: str) -> str:
        text = str(source or "").strip()
        if not text:
            return ""
        try:
            if text.startswith("data:") and "," in text:
                meta, payload = text.split(",", 1)
                raw = base64.b64decode(payload, validate=False) if ";base64" in meta.lower() else payload.encode("utf-8", errors="ignore")
                return "sha256:" + hashlib.sha256(raw).hexdigest()
            if text.startswith("base64://"):
                raw = base64.b64decode(text[len("base64://"):], validate=False)
                return "sha256:" + hashlib.sha256(raw).hexdigest()
            path = self._private_image_local_path_from_source(text)
            if path is None:
                return ""
            if path.exists() and path.is_file() and self._private_image_local_path_is_allowed(path):
                return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        except Exception as exc:
            logger.debug("私聊图片缓存键生成失败: %s", exc)
        if re.match(r"^https?://", text, flags=re.I):
            return self._private_image_normalized_url_cache_key(text)
        return ""

    def _private_image_normalized_url_cache_key(self, source: str) -> str:
        text = str(source or "").strip()
        if not text:
            return ""
        try:
            parsed = urlparse(text)
            volatile_keys = {
                "term", "is_origin", "spec", "rkey", "token", "sign", "expires", "expire", "ts",
                "timestamp", "t", "time", "cache", "cache_key", "ck", "rand", "random", "nonce",
                "download", "disposition", "file_size", "size", "width", "height", "w", "h",
                "quality", "format", "fmt", "x-oss-process", "imageView2", "imageMogr2",
            }
            query_parts = []
            for key, value in parse_qsl(parsed.query, keep_blank_values=True):
                lowered = key.lower()
                if lowered in volatile_keys or lowered.startswith("utm_"):
                    continue
                query_parts.append((key, value))
            normalized = urlunparse((
                parsed.scheme.lower() or "https",
                parsed.netloc.lower(),
                parsed.path,
                "",
                urlencode(sorted(query_parts)),
                "",
            ))
            return "url:" + hashlib.sha1(normalized.encode("utf-8", errors="ignore")).hexdigest()
        except Exception:
            return "url:" + hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()

    def _private_image_source_cache_aliases(self, source: str) -> list[str]:
        text = str(source or "").strip()
        aliases: list[str] = []

        def add(value: str) -> None:
            item = str(value or "").strip()
            if item and item not in aliases:
                aliases.append(item)

        primary = self._private_image_source_cache_key(text)
        add(primary)
        if re.match(r"^https?://", text, flags=re.I):
            add(self._private_image_normalized_url_cache_key(text))
            try:
                parsed = urlparse(text)
                name = unquote((parsed.path or "").rsplit("/", 1)[-1]).lower()
                stem = re.sub(r"\.(?:jpg|jpeg|png|webp|gif|bmp)$", "", name, flags=re.I)
                for token in re.findall(r"[a-f0-9]{16,64}", stem):
                    add("urlhex:" + token)
            except Exception:
                pass
        raw = self._private_image_source_bytes_for_cache_alias(text)
        if raw:
            for alias in self._private_image_visual_cache_aliases_from_bytes(raw):
                add(alias)
        return aliases[:8]

    def _private_image_source_bytes_for_cache_alias(self, source: str) -> bytes:
        text = str(source or "").strip()
        if not text:
            return b""
        try:
            if text.startswith("data:") and "," in text:
                meta, payload = text.split(",", 1)
                return base64.b64decode(payload, validate=False) if ";base64" in meta.lower() else payload.encode("utf-8", errors="ignore")
            if text.startswith("base64://"):
                return base64.b64decode(text[len("base64://"):], validate=False)
            if text.startswith("file://"):
                text = text[len("file://"):]
            if re.match(r"^https?://", text, flags=re.I):
                return b""
            path = Path(text)
            if path.exists() and path.is_file() and self._private_image_local_path_is_allowed(path):
                return path.read_bytes()
        except Exception as exc:
            logger.debug("私聊图片缓存别名字节读取失败: %s", exc)
        return b""

    def _private_image_visual_cache_aliases_from_bytes(self, raw: bytes) -> list[str]:
        if not raw:
            return []
        try:
            from PIL import Image as PILImage
        except Exception:
            return []
        try:
            with PILImage.open(io.BytesIO(raw)) as image:
                frame_total = int(getattr(image, "n_frames", 1) or 1)
                if bool(getattr(image, "is_animated", False) or frame_total > 1):
                    return []
                width, height = image.size
                if width <= 0 or height <= 0:
                    return []
                gray = image.convert("L")
                ahash_image = gray.resize((8, 8))
                ahash_reader = getattr(ahash_image, "get_flattened_data", None)
                ahash_pixels = list(ahash_reader() if callable(ahash_reader) else ahash_image.getdata())
                average = sum(ahash_pixels) / max(1, len(ahash_pixels))
                ahash_bits = "".join("1" if value >= average else "0" for value in ahash_pixels)
                dhash_image = gray.resize((9, 8))
                dhash_reader = getattr(dhash_image, "get_flattened_data", None)
                dhash_pixels = list(dhash_reader() if callable(dhash_reader) else dhash_image.getdata())
                dhash_bits = []
                for row in range(8):
                    offset = row * 9
                    for col in range(8):
                        dhash_bits.append("1" if dhash_pixels[offset + col] > dhash_pixels[offset + col + 1] else "0")
                ahash = f"{int(ahash_bits, 2):016x}"
                dhash = f"{int(''.join(dhash_bits), 2):016x}"
                aspect_bucket = max(1, min(999, int(round((width / max(1, height)) * 100))))
                return [f"pxhash:v1:a{aspect_bucket}:ah{ahash}:dh{dhash}"]
        except Exception as exc:
            logger.debug("私聊图片视觉指纹生成失败: %s", exc)
        return []

    def _private_image_cache_preview_dir(self) -> Path:
        return Path(self.data_dir) / "private_image_cache_previews"

    def _remove_private_image_cache_preview_file(self, preview_path: str) -> None:
        if not preview_path:
            return
        try:
            path = Path(preview_path).resolve()
            base = self._private_image_cache_preview_dir().resolve()
            if not path.is_relative_to(base):
                return
            path.unlink(missing_ok=True)
            (base / ".thumbnails" / f"{path.stem}.webp").unlink(missing_ok=True)
        except Exception:
            pass

    def _private_image_cache_preview_from_sources(
        self,
        cache_key: str,
        sources: list[str],
    ) -> dict[str, Any]:
        clean_key = re.sub(r"[^0-9A-Za-z_.-]+", "_", str(cache_key or ""))[:80]
        if not clean_key:
            return {}
        try:
            from PIL import Image as PILImage, ImageOps
        except Exception:
            return {}
        for source in [str(item).strip() for item in (sources or []) if str(item or "").strip()][:6]:
            raw = self._private_image_source_bytes_for_cache_alias(source)
            if not raw:
                continue
            try:
                with PILImage.open(io.BytesIO(raw)) as image:
                    image.seek(0)
                    image = ImageOps.exif_transpose(image)
                    if image.mode not in {"RGB", "L"}:
                        image = image.convert("RGBA")
                        background = PILImage.new("RGBA", image.size, (255, 255, 255, 255))
                        background.alpha_composite(image)
                        image = background.convert("RGB")
                    else:
                        image = image.convert("RGB")
                    image.thumbnail((320, 320))
                    target_dir = self._private_image_cache_preview_dir()
                    target_dir.mkdir(parents=True, exist_ok=True)
                    target = target_dir / f"{clean_key}.jpg"
                    image.save(target, format="JPEG", quality=72, optimize=True, progressive=True)
                    try:
                        file_size = target.stat().st_size
                    except Exception:
                        file_size = 0
                    return {
                        "preview_path": str(target),
                        "preview_width": int(image.width),
                        "preview_height": int(image.height),
                        "preview_size": int(file_size),
                    }
            except Exception as exc:
                logger.debug("图片缓存预览生成失败: %s", exc)
        return {}

    def _private_image_cache_aliases_for_sources(self, sources: list[str]) -> list[str]:
        aliases: list[str] = []
        for source in [str(item).strip() for item in (sources or []) if str(item or "").strip()][:5]:
            for alias in self._private_image_source_cache_aliases(source):
                if alias and alias not in aliases:
                    aliases.append(alias)
        return aliases[:24]

    def _private_image_cache_image_keys(self, sources: list[str]) -> list[str]:
        keys: list[str] = []
        for source in sources or []:
            key = self._private_image_source_cache_key(source)
            if key and key not in keys:
                keys.append(key)
        return keys[:5]

    def _private_image_vision_cache_store(self) -> dict[str, Any]:
        cache = self.data.setdefault("private_image_vision_cache", {})
        if not isinstance(cache, dict):
            cache = {}
            self.data["private_image_vision_cache"] = cache
        return cache

    @staticmethod
    def _private_image_vision_cache_prompt_sig(prompt: str = "") -> str:
        """Return the compact signature stored alongside a vision cache item."""
        value = str(prompt or "")
        return hashlib.sha1(value.encode("utf-8", errors="ignore")).hexdigest()[:16] if value else ""

    def _private_image_vision_cache_key(self, image_keys: list[str], provider_id: str, prompt: str = "", *, scope: str = "private_image") -> str:
        clean_keys = [str(item).strip() for item in image_keys if str(item or "").strip()]
        if not clean_keys:
            return ""
        prompt_sig = self._private_image_vision_cache_prompt_sig(prompt)
        raw = "v3|" + _single_line(scope, 40) + "|" + str(provider_id or "") + "|" + prompt_sig + "|" + "|".join(clean_keys)
        return hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()

    def _get_private_image_vision_cache(
        self,
        cache_key: str,
        *,
        provider_id: str = "",
        image_keys: list[str] | None = None,
        image_aliases: list[str] | None = None,
        image_count: int = 0,
        scope: str = "private_image",
        allow_image_key_fallback: bool = True,
        prompt: str = "",
    ) -> str:
        if not bool(self._private_image_setting("enable_private_image_vision_cache", True)):
            return ""
        cache = self._private_image_vision_cache_store()
        clean_image_keys = [str(item).strip() for item in (image_keys or []) if str(item or "").strip()]
        clean_aliases = {str(item).strip() for item in (image_aliases or []) if str(item or "").strip()}
        expected_count = max(0, int(image_count or 0))
        expected_prompt_sig = self._private_image_vision_cache_prompt_sig(prompt)

        def prompt_matches(item: dict[str, Any]) -> bool:
            """Allow unsigned legacy entries, but never cross prompt variants."""
            if not expected_prompt_sig:
                return True
            cached_prompt_sig = _single_line(item.get("prompt_sig"), 32)
            return not cached_prompt_sig or cached_prompt_sig == expected_prompt_sig

        def use_item(key: str, item: dict[str, Any], *, fallback: bool = False, detail: str = "") -> str:
            if not prompt_matches(item):
                return ""
            text = _single_line(item.get("text"), 900 if scope == "forward_image" else self._private_image_vision_text_limit(expected_count))
            if not text:
                cache.pop(key, None)
                return ""
            item["hits"] = _safe_int(item.get("hits"), 0, 0) + 1
            item["last_hit_ts"] = _private_image_host._now_ts()
            if fallback and cache_key and key != cache_key:
                item.setdefault("migrated_from", key)
                cache[cache_key] = item
                cache.pop(key, None)
            self._record_cache_metric(f"image_vision:{scope}", hit=True, detail=detail or ("fallback" if fallback else "direct"))
            return text

        item = cache.get(cache_key)
        if isinstance(item, dict):
            text = use_item(cache_key, item)
            if text:
                return text

        if allow_image_key_fallback and clean_image_keys:
            expected_provider = _single_line(provider_id, 160)
            expected_scope = _single_line(scope, 40)
            provider_fallback: tuple[str, dict[str, Any]] | None = None
            for key, item in list(cache.items()):
                if key == cache_key or not isinstance(item, dict):
                    continue
                cached_keys = [str(value).strip() for value in item.get("image_keys", []) if str(value or "").strip()]
                if cached_keys != clean_image_keys:
                    continue
                cached_scope = _single_line(item.get("scope"), 40)
                if cached_scope and expected_scope and cached_scope != expected_scope:
                    continue
                if not prompt_matches(item):
                    continue
                cached_provider = _single_line(item.get("provider_id"), 160)
                if expected_provider and cached_provider and cached_provider != expected_provider:
                    if provider_fallback is None:
                        provider_fallback = (key, item)
                    continue
                text = use_item(key, item, fallback=True)
                if text:
                    return text
            if provider_fallback is not None:
                key, item = provider_fallback
                text = use_item(key, item, fallback=True, detail="provider_fallback")
                if text:
                    return text

            if clean_aliases and expected_count == 1:
                alias_provider_fallback: tuple[str, dict[str, Any]] | None = None
                for key, item in list(cache.items()):
                    if key == cache_key or not isinstance(item, dict):
                        continue
                    cached_scope = _single_line(item.get("scope"), 40)
                    if cached_scope and expected_scope and cached_scope != expected_scope:
                        continue
                    if not prompt_matches(item):
                        continue
                    cached_count = _safe_int(item.get("image_count"), 0, 0)
                    if cached_count <= 0:
                        cached_count = 1 if len([value for value in item.get("image_keys", []) if str(value or "").strip()]) == 1 else 0
                    if cached_count != 1:
                        continue
                    cached_aliases = {str(value).strip() for value in item.get("image_aliases", []) if str(value or "").strip()}
                    if not (cached_aliases & clean_aliases):
                        continue
                    cached_provider = _single_line(item.get("provider_id"), 160)
                    if expected_provider and cached_provider and cached_provider != expected_provider:
                        if alias_provider_fallback is None:
                            alias_provider_fallback = (key, item)
                        continue
                    text = use_item(key, item, fallback=True, detail="alias_fallback")
                    if text:
                        return text
                if alias_provider_fallback is not None:
                    key, item = alias_provider_fallback
                    text = use_item(key, item, fallback=True, detail="alias_provider_fallback")
                    if text:
                        return text

        self._record_cache_metric(f"image_vision:{scope}", hit=False, detail="miss")
        return ""

    def _set_private_image_vision_cache(
        self,
        cache_key: str,
        text: str,
        *,
        provider_id: str,
        image_keys: list[str],
        image_aliases: list[str] | None = None,
        image_count: int = 0,
        prompt: str = "",
        scope: str = "private_image",
        preview: dict[str, Any] | None = None,
    ) -> None:
        if not bool(self._private_image_setting("enable_private_image_vision_cache", True)):
            return
        cleaned = _single_line(text, 900 if scope == "forward_image" else self._private_image_vision_text_limit(image_count))
        if not cache_key or not cleaned:
            return
        cache = self._private_image_vision_cache_store()
        clean_image_keys = [str(item) for item in image_keys[:5] if str(item or "").strip()]
        clean_scope = _single_line(scope, 40)
        clean_provider = _single_line(provider_id, 160)
        clean_aliases = [str(item).strip() for item in (image_aliases or []) if str(item or "").strip()]
        clean_aliases = list(dict.fromkeys(clean_aliases))[:24]
        clean_count = max(0, int(image_count or 0))
        if clean_count <= 0:
            clean_count = len(clean_image_keys)
        prompt_sig = self._private_image_vision_cache_prompt_sig(prompt)
        removed_variants = 0
        for old_key, old_item in list(cache.items()):
            if old_key == cache_key or not isinstance(old_item, dict):
                continue
            old_keys = [str(value).strip() for value in old_item.get("image_keys", []) if str(value or "").strip()]
            old_scope = _single_line(old_item.get("scope"), 40)
            old_provider = _single_line(old_item.get("provider_id"), 160)
            old_prompt_sig = _single_line(old_item.get("prompt_sig"), 32)
            same_reusable_image = old_keys == clean_image_keys and old_scope == clean_scope
            old_aliases = {str(value).strip() for value in old_item.get("image_aliases", []) if str(value or "").strip()}
            old_count = _safe_int(old_item.get("image_count"), 0, 0)
            same_single_alias = clean_count == 1 and old_count == 1 and bool(old_aliases & set(clean_aliases)) and old_scope == clean_scope
            same_reusable_image = same_reusable_image or same_single_alias
            same_provider_variant = same_reusable_image and old_provider == clean_provider
            stale_prompt_variant = same_provider_variant and old_prompt_sig != prompt_sig
            duplicate_provider_variant = same_reusable_image and old_provider and old_provider != clean_provider and _safe_int(old_item.get("hits"), 0, 0) == 0
            if stale_prompt_variant or duplicate_provider_variant:
                if isinstance(old_item, dict):
                    self._remove_private_image_cache_preview_file(_single_line(old_item.get("preview_path"), 260))
                cache.pop(old_key, None)
                removed_variants += 1
        existing_preview_path = ""
        existing_item = cache.get(cache_key)
        if isinstance(existing_item, dict):
            existing_preview_path = _single_line(existing_item.get("preview_path"), 260)
        item = {
            "text": cleaned,
            "provider_id": clean_provider,
            "image_keys": clean_image_keys,
            "image_aliases": clean_aliases,
            "image_count": clean_count,
            "scope": clean_scope,
            "prompt_sig": prompt_sig,
            "created_ts": _private_image_host._now_ts(),
            "last_hit_ts": 0,
            "hits": 0,
        }
        if isinstance(preview, dict) and preview.get("preview_path"):
            item.update(
                {
                    "preview_path": _single_line(preview.get("preview_path"), 260),
                    "preview_width": _safe_int(preview.get("preview_width"), 0, 0),
                    "preview_height": _safe_int(preview.get("preview_height"), 0, 0),
                    "preview_size": _safe_int(preview.get("preview_size"), 0, 0),
                }
            )
            if existing_preview_path and existing_preview_path != item["preview_path"]:
                self._remove_private_image_cache_preview_file(existing_preview_path)
        elif isinstance(existing_item, dict) and existing_preview_path:
            item.update(
                {
                    "preview_path": existing_preview_path,
                    "preview_width": _safe_int(existing_item.get("preview_width"), 0, 0),
                    "preview_height": _safe_int(existing_item.get("preview_height"), 0, 0),
                    "preview_size": _safe_int(existing_item.get("preview_size"), 0, 0),
                }
            )
        cache[cache_key] = item
        if removed_variants:
            self._record_cache_metric(f"image_vision:{scope}", hit=True, detail=f"dedupe:{removed_variants}")
        max_items = int(self._private_image_setting("private_image_vision_cache_max_items", 300) or 0)
        if max_items > 0 and len(cache) > max_items:
            stale = sorted(
                cache.items(),
                key=lambda item: (
                    _safe_int((item[1] if isinstance(item[1], dict) else {}).get("hits"), 0, 0),
                    _safe_float((item[1] if isinstance(item[1], dict) else {}).get("last_hit_ts"), 0)
                    or _safe_float((item[1] if isinstance(item[1], dict) else {}).get("created_ts"), 0),
                ),
            )
            evicted = 0
            for key, _ in stale[: max(1, len(cache) - max_items)]:
                removed = cache.pop(key, None)
                if isinstance(removed, dict):
                    self._remove_private_image_cache_preview_file(_single_line(removed.get("preview_path"), 260))
                evicted += 1
            if evicted:
                self._record_cache_metric(f"image_vision:{scope}", hit=False, detail=f"evict:{evicted}")
        try:
            self._save_data_sync(sections={"private_image_vision_cache"})
        except Exception as exc:
            logger.debug("私聊图片视觉缓存保存失败: %s", exc)

    def _invalidate_private_image_vision_cache_by_image_keys(self, image_keys: list[str], *, image_aliases: list[str] | None = None, reason: str = "") -> int:
        targets = {str(item) for item in image_keys or [] if str(item or "").strip()}
        alias_targets = {str(item).strip() for item in (image_aliases or []) if str(item or "").strip()}
        if not targets and not alias_targets:
            return 0
        cache = self._private_image_vision_cache_store()
        removed = 0
        for key, item in list(cache.items()):
            if not isinstance(item, dict):
                continue
            cached_keys = {str(value) for value in item.get("image_keys", []) if str(value or "").strip()}
            cached_aliases = {str(value).strip() for value in item.get("image_aliases", []) if str(value or "").strip()}
            if (cached_keys & targets) or (cached_aliases & alias_targets):
                removed_item = cache.pop(key, None)
                if isinstance(removed_item, dict):
                    self._remove_private_image_cache_preview_file(
                        _single_line(removed_item.get("preview_path"), 260)
                    )
                removed += 1
        if removed:
            logger.info("私聊图片视觉缓存已因负反馈失效: removed=%s reason=%s", removed, _single_line(reason, 120))
            try:
                self._save_data_sync(sections={"private_image_vision_cache"})
            except Exception as exc:
                logger.debug("私聊图片视觉缓存失效保存失败: %s", exc)
        return removed

    def _is_private_image_vision_negative_feedback(self, text: str) -> bool:
        cleaned = _single_line(text, 160)
        if not cleaned:
            return False
        negative_patterns = (
            r"(识别|看|理解|读|认).{0,8}(错|不对|不准|偏了|歪了)",
            r"(不是|不对|错了).{0,12}(这个意思|这样|这意思|你说的|图里|图片|表情包)",
            r"(你|bot|机器人).{0,8}(看错|认错|理解错|识别错)",
            r"(不是.{0,8}你|不是.{0,8}bot|不是.{0,8}本人|不是.{0,8}这个)",
        )
        return any(re.search(pattern, cleaned, flags=re.I) for pattern in negative_patterns)

    def _apply_private_image_vision_negative_feedback(self, user: dict[str, Any], text: str) -> bool:
        if not self._is_private_image_vision_negative_feedback(text):
            return False
        target = user.get("last_private_image_vision_feedback_target")
        if not isinstance(target, dict):
            return False
        ts = _safe_float(target.get("ts"), 0)
        if ts <= 0 or _private_image_host._now_ts() - ts > 180:
            return False
        image_keys = [str(item) for item in target.get("image_keys", []) if str(item or "").strip()]
        image_aliases = [str(item) for item in target.get("image_aliases", []) if str(item or "").strip()]
        removed = self._invalidate_private_image_vision_cache_by_image_keys(image_keys, image_aliases=image_aliases, reason=text)
        target["negative_feedback_ts"] = _private_image_host._now_ts()
        target["negative_feedback_text"] = _single_line(text, 160)
        target["invalidated_cache_items"] = removed
        logger.info(
            "私聊图片视觉负反馈记录: user_image_keys=%s removed=%s text=%s",
            len(image_keys),
            removed,
            _single_line(text, 120),
        )
        return bool(removed or image_keys)

    def _route_private_image_caption_with_keyword_router(
        self, event: AstrMessageEvent, vision_text: str
    ) -> bool:
        """让绕过标准流水线的纯图片 Agent 也能应用关键词模型路由。"""
        caption = _single_line(vision_text, 8000)
        if not caption:
            return False
        context = self._private_image_framework_context()
        getter = getattr(context, "get_registered_star", None)
        if not callable(getter):
            return False
        try:
            metadata = getter("astrbot_plugin_keyword_model_router")
            router = getattr(metadata, "star_cls", None) if metadata is not None else None
            route = getattr(router, "route_companion_image_caption", None)
            if not callable(route):
                return False
            setattr(event, "private_companion_image_caption_route_text", caption)
            return bool(route(event, caption))
        except Exception as exc:
            logger.debug(
                "调用关键词模型路由失败，保留原 Provider: %s",
                _single_line(exc, 120),
            )
            return False

    def _take_buffered_private_image_context_for_event(self, event: AstrMessageEvent) -> dict[str, Any]:
        try:
            sender_id = str(event.get_sender_id())
        except Exception:
            sender_id = ""
        if not sender_id:
            return {}
        resolver = getattr(self, "_private_user_id_for_event", None)
        if callable(resolver):
            try:
                sender_id = _single_line(resolver(event, sender_id), 160) or sender_id
            except Exception:
                pass
        key = self._semantic_buffer_key(f"private:{sender_id}", sender_id)
        now = _private_image_host._now_ts()
        handoffs = self._cleanup_private_image_vision_handoffs(now=now)
        buffers = getattr(self, "_semantic_message_buffers", None)
        buffer = buffers.get(key) if isinstance(buffers, dict) else None
        max_live_age = max(30.0, self._message_debounce_seconds("image") + 30.0)
        live_updated_ts = (
            _safe_float(buffer.get("updated_ts"), buffer.get("first_ts"), 0)
            if isinstance(buffer, dict)
            else 0.0
        )
        if isinstance(buffer, dict) and now - live_updated_ts <= max_live_age:
            handoffs.pop(key, None)
            # 标记图片上下文已被本轮文字请求认领，防抖 finalizer 会跳过二次派发。
            buffer["vision_context_claimed_ts"] = now
            images = buffer.pop("images", [])
            image_limit = self._private_image_vision_text_limit(len(images))
            return {
                "images": [str(item) for item in images[:5] if str(item or "").strip()],
                "image_mode": _single_line(buffer.pop("image_mode", ""), 20),
                "vision_task": buffer.pop("vision_task", None),
                "vision_text": _single_line(buffer.pop("vision_text", ""), image_limit),
                "from_handoff": False,
            }

        handoff = handoffs.get(key)
        if not isinstance(handoff, dict):
            return {}
        stored_session = _single_line(handoff.get("session"), 500)
        current_session = self._private_image_vision_handoff_session(event)
        if stored_session != current_session:
            logger.info(
                "私聊图片视觉交接会话不匹配,保留给原会话: sender=%s stored=%s current=%s",
                sender_id,
                stored_session,
                current_session or "-",
            )
            return {}
        handoffs.pop(key, None)
        images = handoff.get("images") if isinstance(handoff.get("images"), list) else []
        image_limit = self._private_image_vision_text_limit(len(images))
        vision_task = handoff.get("vision_task")
        vision_text = _single_line(handoff.get("vision_text"), image_limit)
        if not vision_text:
            vision_text = _single_line(
                self._completed_private_image_vision_task_text(vision_task),
                image_limit,
            )
        logger.info(
            "私聊补充文字已领取延迟图片视觉交接: sender=%s images=%s has_vision=%s pending=%s",
            sender_id,
            len(images),
            bool(vision_text),
            isinstance(vision_task, asyncio.Task) and not vision_task.done(),
        )
        return {
            "images": [str(item) for item in images[:5] if str(item or "").strip()],
            "image_mode": _single_line(handoff.get("image_mode"), 20),
            "vision_task": vision_task,
            "vision_text": vision_text,
            "from_handoff": True,
        }

    def _private_image_context_user_message(self, *, vision_text: str, image_count: int = 1) -> str:
        count = max(1, int(image_count or 1))
        image_label = "一张图片" if count == 1 else f"{count} 张图片"
        summary = _single_line(vision_text, self._private_image_vision_text_limit(count))
        if summary:
            return f"用户发送了{image_label}。[图片内容：{summary}]"
        return f"用户发送了{image_label}，但当前没有获得可靠视觉摘要。"

    @staticmethod
    def _private_image_history_content_text(value: Any) -> str:
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, list):
            parts: list[str] = []
            for item in value:
                if isinstance(item, dict):
                    item_type = str(item.get("type") or "").lower()
                    if item_type in {"text", "plain"}:
                        parts.append(str(item.get("text") or item.get("content") or ""))
                    elif item_type in {"image", "image_url"}:
                        parts.append("[图片]")
                elif isinstance(item, str):
                    parts.append(item)
                else:
                    item_type = str(getattr(item, "type", "") or "").lower()
                    item_text = getattr(item, "text", None)
                    if item_type in {"text", "plain"} and item_text:
                        parts.append(str(item_text))
                    elif "image" in item_type or "image" in item.__class__.__name__.lower():
                        parts.append("[图片]")
            return " ".join(part for part in parts if part).strip()
        if isinstance(value, dict):
            return PrivateImageMixin._private_image_history_content_text(
                value.get("content") or value.get("text") or value.get("message") or ""
            )
        item_type = str(getattr(value, "type", "") or "").lower()
        item_text = getattr(value, "text", None)
        if item_text:
            return str(item_text).strip()
        if "image" in item_type or "image" in value.__class__.__name__.lower():
            return "[图片]"
        return ""

    @staticmethod
    def _private_image_history_item_role(item: Any) -> str:
        if isinstance(item, dict):
            return str(item.get("role") or item.get("type") or "").strip().lower()
        return str(getattr(item, "role", "") or "").strip().lower()

    @staticmethod
    def _private_image_history_item_content(item: Any) -> Any:
        if isinstance(item, dict):
            return item.get("content")
        return getattr(item, "content", None)

    def _private_image_append_history_marker(self, item: Any, marker: str) -> bool:
        content = self._private_image_history_item_content(item)
        current_text = self._private_image_history_content_text(content)
        if marker in current_text:
            return False
        if isinstance(content, str):
            new_content = f"{content}\n{marker}".strip()
            if isinstance(item, dict):
                item["content"] = new_content
            else:
                item.content = new_content
            return True
        if isinstance(content, list):
            for part in reversed(content):
                if isinstance(part, dict) and str(part.get("type") or "").lower() in {"text", "plain"}:
                    part["text"] = f"{part.get('text') or part.get('content') or ''}\n{marker}".strip()
                    part.pop("content", None)
                    return True
                if str(getattr(part, "type", "") or "").lower() in {"text", "plain"}:
                    part.text = f"{getattr(part, 'text', '') or ''}\n{marker}".strip()
                    return True
            content.append(TextPart(text=marker))
            return True
        new_content = marker
        if isinstance(item, dict):
            item["content"] = new_content
        else:
            item.content = new_content
        return True

    @staticmethod
    def _private_image_history_summary_line(summary: str) -> str:
        cleaned = _single_line(summary, 2400)
        return f"[图片内容：{cleaned}]" if cleaned else ""

    def _private_image_history_user_matches_event(
        self,
        item: Any,
        event: AstrMessageEvent,
    ) -> bool:
        content = self._private_image_history_content_text(
            self._private_image_history_item_content(item)
        )
        if not content:
            return False
        event_text = _single_line(getattr(event, "message_str", ""), 600)
        normalized_content = re.sub(r"\s+", "", content)
        normalized_event = re.sub(r"\s+", "", event_text)
        if normalized_event and normalized_event not in {"[图片]", "图片", "【图片】"}:
            return normalized_event in normalized_content or normalized_content in normalized_event
        return "图片" in normalized_content or "[CQ:image" in normalized_content.lower()

    async def _persist_private_image_vision_summary_to_history(
        self,
        event: AstrMessageEvent,
    ) -> bool:
        """Attach the vision result to the current user history turn.

        The caption provider is an auxiliary call and its result is not part of
        AstrBot's normal request history.  Persisting a bounded, visible user
        text marker makes the next turn able to recover what the image showed,
        while keeping the original image segment and assistant reply intact.
        """
        summary = ""
        for field_name in (
            "private_companion_delayed_image_vision_text",
            "private_companion_reply_image_vision_text",
            "private_companion_image_caption_route_text",
        ):
            summary = _single_line(getattr(event, field_name, ""), 2400)
            if summary:
                break
        marker = self._private_image_history_summary_line(summary)
        if not marker:
            return False
        umo = _single_line(getattr(event, "unified_msg_origin", ""), 200)
        manager = getattr(getattr(self, "context", None), "conversation_manager", None)
        if not umo:
            return False
        requested_cid = _single_line(
            getattr(event, "_private_companion_response_conversation_id", ""),
            160,
        )

        async def write() -> bool:
            # The core serializes this live context after the send hooks.  Try
            # it first so the marker cannot be lost to a stale database copy.
            run_context = getattr(event, "_private_companion_run_context", None)
            run_messages = getattr(run_context, "messages", None)
            if isinstance(run_messages, list):
                for item in reversed(run_messages):
                    if self._private_image_history_item_role(item) != "user":
                        continue
                    if not self._private_image_history_user_matches_event(item, event):
                        continue
                    current_text = self._private_image_history_content_text(
                        self._private_image_history_item_content(item)
                    )
                    if marker in current_text:
                        return False
                    if self._private_image_append_history_marker(item, marker):
                        logger.info("已将图片视觉摘要附加到当前用户消息，交由 AstrBot 核心保存: session=%s", umo)
                        return True

            if manager is None:
                return False
            conversation_id = requested_cid or _single_line(
                await manager.get_curr_conversation_id(umo),
                160,
            )
            if not conversation_id:
                return False
            conversation = await manager.get_conversation(umo, conversation_id)
            if conversation is None:
                return False
            raw_history = getattr(conversation, "history", "[]")
            if isinstance(raw_history, str):
                history = json.loads(raw_history or "[]")
            elif isinstance(raw_history, list):
                history = list(raw_history)
            else:
                history = []

            for item in reversed(history):
                if not isinstance(item, dict) or str(item.get("role") or "") != "user":
                    continue
                if not self._private_image_history_user_matches_event(item, event):
                    continue
                content = item.get("content")
                current_text = self._private_image_history_content_text(content)
                if marker in current_text:
                    return False
                self._private_image_append_history_marker(item, marker)
                await manager.update_conversation(umo, conversation_id, history=history)
                logger.info("已将图片视觉摘要写入当前用户 history: session=%s", umo)
                return True
            logger.debug("未找到可附加图片视觉摘要的当前用户 history: session=%s", umo)
            return False

        db_operation = getattr(self, "_conversation_db_operation", None)
        try:
            result = db_operation("persist_private_image_vision", write) if callable(db_operation) else write()
            if hasattr(result, "__await__"):
                result = await result
            return bool(result)
        except Exception as exc:
            logger.warning("图片视觉摘要写入会话 history 失败: %s", _single_line(exc, 160))
            return False

    def _private_image_context_assistant_message(self, reply: str) -> str:
        if not bool(runtime_persona_setting(self, "enable_framework_error_leak_guard", True)):
            return _single_line(reply, 1200)
        cleaner = getattr(self, "_visible_text_without_tts_reading", None)
        if callable(cleaner):
            try:
                cleaned = str(cleaner(reply, limit=1200) or "").strip()
            except Exception:
                cleaned = ""
        else:
            cleaned = ""
        if not cleaned:
            cleaned = re.sub(r"</?(?:pc[_-]?tts|t{2,}s)\b[^>]*>", "", str(reply or ""), flags=re.IGNORECASE).strip()
        # This text is persisted into AstrBot's user-visible conversation
        # history; remove plugin-only markers before it reaches that store.
        return _single_line(
            sanitize_llm_segment_control_tokens(
                _strip_outbound_control_blocks(
                    cleaned or reply,
                    tts_enabled=bool(runtime_persona_setting(self, "enable_tts_enhancement", False)),
                )
            ),
            1200,
        )

    async def _archive_private_image_turn_to_conversation(
        self,
        event: AstrMessageEvent,
        *,
        user_message: str,
        assistant_message: str,
    ) -> None:
        umo = _single_line(getattr(event, "unified_msg_origin", ""), 200)
        if not umo or not user_message or not assistant_message:
            return
        conv_mgr = getattr(getattr(self, "context", None), "conversation_manager", None)
        if conv_mgr is None:
            return
        ensure_conv = getattr(self, "_ensure_conversation_id_for_umo", None)
        db_operation = getattr(self, "_conversation_db_operation", None)
        for attempt in range(4):
            try:
                user_msg_obj = UserMessageSegment(content=str(user_message or ""))
                assistant_msg_obj = AssistantMessageSegment(content=str(assistant_message or ""))

                async def _write() -> bool:
                    if callable(ensure_conv):
                        conv_id = await ensure_conv(umo, title="Private Companion 图片对话")
                    else:
                        conv_id = await conv_mgr.get_curr_conversation_id(umo)
                        if not conv_id:
                            try:
                                conv_id = await conv_mgr.new_conversation(umo, title="Private Companion 图片对话")
                            except TypeError:
                                conv_id = await conv_mgr.new_conversation(umo)
                    if not conv_id:
                        return False
                    await conv_mgr.add_message_pair(
                        cid=conv_id,
                        user_message=user_msg_obj,
                        assistant_message=assistant_msg_obj,
                    )
                    return True

                written = await db_operation("archive_private_image_turn", _write) if callable(db_operation) else await _write()
                if written:
                    logger.info("已将私聊图片回复写入 AstrBot 会话历史: %s", umo)
                else:
                    logger.warning("私聊图片回复写入会话历史失败: 无法获取或创建 AstrBot 会话 history umo=%s", umo)
                return
            except Exception as exc:
                text = str(exc or "").lower()
                if ("database is locked" in text or "sqlite3.operationalerror" in text) and attempt < 3:
                    await asyncio.sleep(0.25 * (attempt + 1))
                    continue
                logger.warning("私聊图片回复写入会话历史失败: %s", _single_line(exc, 160))
                return

    async def _memory_companion_record_private_image_visible_turn(
        self,
        event: AstrMessageEvent,
        *,
        user_id: str,
        user_message: str,
        assistant_message: str,
        vision_text: str = "",
        image_count: int = 1,
    ) -> None:
        bridge_getter = getattr(self, "_memory_companion_bridge", None)
        try:
            bridge = bridge_getter() if callable(bridge_getter) else None
        except Exception as exc:
            optional_failed = getattr(self, "_memory_companion_optional_dependency_failed", None)
            if callable(optional_failed) and optional_failed(exc, where="private_image_visible_turn_bridge"):
                return
            logger.debug("MemoryCompanion 桥接读取失败，跳过私聊图片可见上下文写入: %s", _single_line(exc, 120))
            return
        recorder = getattr(bridge, "record_visible_turn", None) if bridge is not None else None
        if not callable(recorder) or not user_message or not assistant_message:
            return
        session_id = _single_line(getattr(event, "unified_msg_origin", ""), 200)
        if not session_id:
            return
        platform = session_id.split(":", 1)[0] if ":" in session_id else ""
        user_name = ""
        try:
            user_name = _single_line(self._sender_display_name(event), 80)
        except Exception:
            user_name = _single_line(user_id, 80)
        turn_id = uuid.uuid4().hex
        summary = _single_line(vision_text, self._private_image_vision_text_limit(image_count))
        base_metadata = {
            "source": "private_companion_private_image_turn",
            "image_count": max(1, int(image_count or 1)),
            "summary": summary,
            "conversation_turn": "private_image",
        }
        try:
            await recorder(
                role="user",
                content=user_message,
                scope="private",
                session_id=session_id,
                platform=platform,
                user_id=str(user_id or ""),
                user_name=user_name,
                message_id=f"private_companion_image_turn_{turn_id}_user",
                source="private_companion_private_image_turn",
                metadata={**base_metadata, "turn_role": "user"},
            )
            await recorder(
                role="assistant",
                content=assistant_message,
                scope="private",
                session_id=session_id,
                platform=platform,
                user_id=str(user_id or ""),
                user_name=user_name,
                message_id=f"private_companion_image_turn_{turn_id}_assistant",
                source="private_companion_private_image_turn",
                metadata={**base_metadata, "turn_role": "assistant"},
            )
            logger.info("已将私聊图片回复同步为 MemoryCompanion 可见上下文: session=%s", session_id)
        except Exception as exc:
            optional_failed = getattr(self, "_memory_companion_optional_dependency_failed", None)
            if callable(optional_failed) and optional_failed(exc, where="record_private_image_visible_turn"):
                return
            logger.debug("MemoryCompanion 私聊图片可见上下文写入失败: %s", _single_line(exc, 120))

    async def _archive_private_image_turn_context(
        self,
        event: AstrMessageEvent,
        *,
        user_id: str,
        vision_text: str,
        reply: str,
        image_count: int = 1,
    ) -> None:
        assistant_message = self._private_image_context_assistant_message(reply)
        if not assistant_message:
            return
        user_message = self._private_image_context_user_message(vision_text=vision_text, image_count=image_count)
        await self._archive_private_image_turn_to_conversation(
            event,
            user_message=user_message,
            assistant_message=assistant_message,
        )
        await self._memory_companion_record_private_image_visible_turn(
            event,
            user_id=user_id,
            user_message=user_message,
            assistant_message=assistant_message,
            vision_text=vision_text,
            image_count=image_count,
        )
        livingmemory_recorder = getattr(
            self,
            "_record_final_assistant_in_livingmemory",
            None,
        )
        if callable(livingmemory_recorder):
            message_id_getter = getattr(self, "_event_message_id", None)
            message_id = (
                _single_line(message_id_getter(event), 120)
                if callable(message_id_getter)
                else ""
            )
            await livingmemory_recorder(
                umo=str(getattr(event, "unified_msg_origin", "") or ""),
                assistant_response=assistant_message,
                delivery_id=(
                    f"private_image:{message_id or user_id}:"
                    f"{_private_image_host._now_ts():.6f}"
                ),
            )

    async def _record_private_image_vision_feedback_target(
        self,
        *,
        user_id: str,
        image_sources: list[str],
        vision_text: str,
        reply: str,
        ownership: str = "",
        intent: str = "",
    ) -> None:
        raw_sources = [str(item) for item in image_sources[:5] if str(item or "").strip()]
        image_keys = self._private_image_cache_image_keys(raw_sources)
        if not image_keys:
            return
        image_aliases = self._private_image_cache_aliases_for_sources(raw_sources)
        image_limit = self._private_image_vision_text_limit(len(raw_sources))
        try:
            async with self._data_lock:
                user = self._get_user(user_id)
                user["last_private_image_vision_feedback_target"] = {
                    "ts": _private_image_host._now_ts(),
                    "image_keys": image_keys,
                    "image_aliases": image_aliases,
                    "vision_text": _single_line(vision_text, image_limit),
                    "reply": _single_line(reply, 300),
                    "ownership": _single_line(ownership, 120),
                    "intent": _single_line(intent, 160),
                }
                self._save_data_sync(sections={"users"})
        except Exception as exc:
            logger.debug("私聊图片视觉反馈目标记录失败: %s", exc)

    async def _send_delayed_private_image_only_event(
        self,
        event: AstrMessageEvent,
        user_id: str,
        buffer: dict[str, Any],
    ) -> None:
        feature_checker = getattr(self, "_feature_enabled_or_temp_unlocked", None)
        feature_enabled = (
            feature_checker("enable_private_image_self_recognition")
            if callable(feature_checker)
            else bool(self._private_image_setting("enable_private_image_self_recognition", True))
        )
        if not feature_enabled:
            logger.info(
                "私聊单图处理期间图片转述增强已关闭,但原事件已接管,继续完成本轮回复: user=%s",
                user_id,
            )
        images = buffer.get("images") if isinstance(buffer.get("images"), list) else []
        vision_task = buffer.get("vision_task")
        image_limit = self._private_image_vision_text_limit(len(images))
        vision_text = _single_line(buffer.get("vision_text"), image_limit)
        vision_wait_timed_out = False
        if not vision_text and isinstance(vision_task, asyncio.Task):
            timeout = self._private_image_vision_wait_budget_seconds()
            try:
                if timeout > 0:
                    logger.info("私聊单图等待视觉转述完成: user=%s timeout=%.1fs", user_id, timeout)
                    vision_text = _single_line(await asyncio.wait_for(asyncio.shield(vision_task), timeout=timeout), image_limit)
            except asyncio.TimeoutError:
                vision_wait_timed_out = True
                logger.warning("私聊单图延迟处理时视觉转述仍未完成: user=%s timeout=%.1fs", user_id, timeout)
            except Exception as exc:
                logger.warning("私聊单图延迟视觉转述失败: user=%s error=%s", user_id, _single_line(exc, 120))
        ownership_line = self._private_image_ownership_line(vision_text)
        intent_line = self._private_image_intent_line(vision_text)
        reply_objective = self._private_image_reply_objective(ownership_line, vision_text=vision_text)
        prompt = _single_line(getattr(event, "message_str", ""), 120)
        if not prompt or prompt == "[图片]":
            prompt = (
                "用户刚刚只发了一张图片,没有补充文字。"
                "图片内容已在系统提示的本轮图片视觉摘要中给出；请直接回应那张图,不要说没看到图片。"
                "本轮只回应当前图片和用户发图可能表达的态度/梗/疑问；"
                "但如果最近对话里用户明确规定了这张/下一张图片的回复方式（例如只回复某句话、不要回复其他内容）,必须优先照做。"
                "除此之外,聊天历史只作语气背景,不要续写、答应或安排旧话题。"
                if vision_text
                else (
                    "用户刚刚只发了一张图片,没有补充文字；但当前没有可靠视觉摘要。"
                    "不要描述图片内容、场景、天气、人物、表情或文字，也不要根据聊天历史猜图。"
                    "如果最近对话里用户明确规定了这张/下一张图片的回复方式,必须优先照做；否则只用一句自然短回复说明这边没看清/没识别出来，并请用户补一句想让你看哪里。"
                )
            )
        logger.info(
            "私聊单图准备进入主链: user=%s images=%s has_vision=%s intent=%s ownership=%s objective=%s vision_preview=%s",
            user_id,
            len(images),
            bool(vision_text),
            intent_line or "无",
            ownership_line or "无",
            _single_line(reply_objective, 120),
            _single_line(vision_text, 220),
        )
        raw_image_sources = [str(item) for item in images[:5] if str(item or "").strip()]
        image_items = self._private_image_model_image_items(raw_image_sources)
        model_image_urls = [url for _, url in image_items]
        request_image_refs = self._private_image_sources_for_astrbot_request(raw_image_sources)
        try:
            umo = str(getattr(event, "unified_msg_origin", "") or "")
            framework_context = self._private_image_framework_context()
            framework_event = event
            if umo and framework_context is not None:
                try:
                    from astrbot.core.platform.message_session import MessageSession
                    from .proactive_message import SyntheticPrivateWakeEvent

                    session = MessageSession.from_str(umo)
                    sender_name = ""
                    try:
                        sender_name = _single_line(event.get_sender_name(), 60)
                    except Exception:
                        sender_name = ""
                    framework_event = SyntheticPrivateWakeEvent(
                        context=framework_context,
                        session=session,
                        message="[图片]",
                        sender_name=sender_name or "PrivateCompanion",
                    )
                    try:
                        selected_provider = event.get_extra("selected_provider")
                        if selected_provider:
                            framework_event.set_extra("selected_provider", selected_provider)
                    except Exception:
                        pass
                    logger.info("私聊单图主链使用合成私聊事件执行: user=%s session=%s", user_id, umo)
                except Exception as exc:
                    framework_event = event
                    logger.info("私聊单图合成私聊事件创建失败,回退原事件: user=%s error=%s", user_id, _single_line(exc, 160))
            elif umo:
                logger.warning(
                    "私聊单图主链未取得 AstrBot 原生 Context,已直接转入视觉摘要兜底: user=%s",
                    user_id,
                )
            setattr(framework_event, "private_companion_deferred_private_image_only_ready", True)
            setattr(framework_event, "private_companion_deferred_private_image_only", False)
            setattr(framework_event, "private_companion_skip_external_token_stats", True)
            setattr(framework_event, "private_companion_delayed_image_vision_text", vision_text)
            setattr(framework_event, "private_companion_delayed_image_sources", list(request_image_refs))
            if vision_text:
                self._route_private_image_caption_with_keyword_router(
                    framework_event, vision_text
                )
            buffered_image_mode = _single_line(buffer.get("image_mode"), 20)
            main_provider_supports_image = self._event_main_provider_supports_image(framework_event)
            has_visual_provider = self._has_private_image_visual_provider(umo)
            has_dynamic_gif_sources = (
                bool(self._private_image_setting("enable_private_image_gif_enhancement", True))
                and self._private_image_sources_include_gif(raw_image_sources)
            )
            resolved_image_mode = self._private_image_delivery_mode(
                has_visual_provider=has_visual_provider,
                main_provider_supports_image=main_provider_supports_image,
                has_dynamic_gif=has_dynamic_gif_sources,
            )
            direct_image_mode = bool(
                request_image_refs
                and buffered_image_mode == "direct"
                and resolved_image_mode == "direct"
            )
            direct_provider_id = ""
            direct_provider_source = "current_main_provider"
            if direct_image_mode:
                try:
                    direct_provider_id = _single_line(framework_event.get_extra("selected_provider"), 160)
                except Exception:
                    direct_provider_id = ""
                if not direct_provider_id:
                    direct_provider_id = "current_main_provider"
                setattr(framework_event, "private_companion_delayed_image_mode", "direct")
            elif request_image_refs:
                setattr(framework_event, "private_companion_delayed_image_mode", "caption" if has_visual_provider else "no_vision")
            if not direct_image_mode and has_visual_provider and not vision_text and images:
                completed_vision = self._completed_private_image_vision_task_text(vision_task)
                if completed_vision:
                    vision_text = _single_line(completed_vision, self._private_image_vision_text_limit(len(images)))
                    logger.info(
                        "私聊单图主链前取到后台视觉摘要: user=%s preview=%s",
                        user_id,
                        _single_line(vision_text, 220),
                    )
                elif not vision_wait_timed_out:
                    vision_text = _single_line(await self._transcribe_private_inbound_images(images, umo=umo), self._private_image_vision_text_limit(len(images)))
                else:
                    logger.warning("私聊单图识图等待已超时,主链不再重复发起视觉转述: user=%s", user_id)
                    setattr(framework_event, "private_companion_delayed_image_mode", "no_vision")
                if vision_text:
                    setattr(framework_event, "private_companion_delayed_image_vision_text", vision_text)
                    self._route_private_image_caption_with_keyword_router(
                        framework_event, vision_text
                    )
                    ownership_line = self._private_image_ownership_line(vision_text)
                    intent_line = self._private_image_intent_line(vision_text)
                    reply_objective = self._private_image_reply_objective(ownership_line, vision_text=vision_text)
            if has_dynamic_gif_sources and request_image_refs:
                logger.info(
                    "私聊单图检测到动态 GIF,已改用抽帧视觉摘要链路: user=%s has_vision=%s",
                    user_id,
                    bool(vision_text),
            )
            conv = None
            if umo:
                getter = getattr(self, "_get_current_conversation_safely", None)
                if callable(getter):
                    conv = await getter(umo, label="private_image_framework_read")
                else:
                    conv_id = await self.context.conversation_manager.get_curr_conversation_id(umo)
                    if conv_id:
                        conv = await self.context.conversation_manager.get_conversation(umo, conv_id)
            config_context = framework_context or self.context
            cfg = config_context.get_config(umo=umo) if umo else config_context.get_config()
            provider_settings = cfg.get("provider_settings", {}) if isinstance(cfg, dict) else {}
            build_cfg = MainAgentBuildConfig(
                tool_call_timeout=int(provider_settings.get("tool_call_timeout", 120) or 120),
                llm_safety_mode=False,
                streaming_response=False,
            )
            # The single-image response is a plugin-owned task even though it
            # runs through AstrBot's framework agent. Keep the custom rule in
            # this task request body; never mutate the main conversation system
            # prompt or the stored conversation configuration.
            prompt_applier = getattr(self, "_apply_task_prompt_override_for_call", None)
            if callable(prompt_applier):
                prompt, _unused_system_prompt = prompt_applier(
                    "private_image_only_framework",
                    prompt,
                    None,
                    flatten_system_prompt=True,
                )
            req = ProviderRequest(
                prompt=prompt,
                conversation=conv,
                session_id=getattr(framework_event, "session_id", None) or umo,
            )
            try:
                selected_model = framework_event.get_extra("selected_model")
            except Exception:
                selected_model = None
            if isinstance(selected_model, str) and selected_model.strip():
                # This path passes an explicit request to build_main_agent, so the
                # framework cannot copy selected_model from the event for us.
                req.model = selected_model.strip()
            previous_selected_provider = ""
            selected_provider_changed = False
            if direct_image_mode:
                req.image_urls = list(request_image_refs)
            await self.inject_humanized_state(framework_event, req)
            boundary_intro = (
                "用户当前只发了一张图片,没有文字补充；但当前没有可靠视觉摘要,本轮也没有把图片直接交给主模型。"
                "你不能看见图片内容,不要猜测画面、天气、地点、人物、表情、截图文字或图片类型。"
                "只允许短句请用户补一句想让你看哪里。\n"
                if not vision_text and not direct_image_mode
                else "用户当前只发了一张图片,没有文字补充。你的当前任务是回应这张图片本身和用户借图表达的态度/梗/疑问。\n"
            )
            boundary_prompt = (
                f"{boundary_intro}"
                "用户没有明确问‘图里是什么/写了什么/有几个人’时，不要逐项描述主体、衣服、背景和文字；"
                "把图当作对方递来的一句话，按人格自然评价、接梗、回应情绪或追问一个重点，最多顺带点出一个最显眼细节。\n"
                "如果最近对话上下文里有用户对本轮图片或下一张图片的明确回复限制,例如“只回复某句话”“不要回复其他内容”,必须优先遵守；这不是旧话题。\n"
                "不要把聊天历史、长期记忆、主动消息、旧 TTS 文本或压缩摘要里的邀约当成当前输入；"
                "不要顺便提下午、五点、放学、出去走走、陪你、到时候叫我等旧约定。"
            )
            boundary_section = prompt_section(
                key="private.image_reply_boundary",
                title="本轮图片回复边界",
                source="private_image",
                content=boundary_prompt,
            )
            recent_group_context = self._format_recent_group_messages_for_private_image_prompt_section(
                user_id
            )
            boundary_children: list[PromptSection] = []
            if str(recent_group_context.content or "").strip():
                boundary_children.append(recent_group_context)
            if boundary_children:
                boundary_section = prompt_section(
                    key=boundary_section.key,
                    title=boundary_section.title,
                    source=boundary_section.source,
                    content=boundary_section.content,
                    children=boundary_children,
                )
            self._register_materialized_private_image_context(
                req,
                section=boundary_section,
                marker="",
                priority=31,
            )
            segmenting_injector = getattr(
                self,
                "inject_llm_controlled_segmenting_instruction",
                None,
            )
            if callable(segmenting_injector):
                try:
                    await segmenting_injector(framework_event, req)
                except Exception as exc:
                    logger.debug(
                        "私聊单图分段说明注入失败，继续生成正文: %s",
                        _single_line(exc, 120),
                    )
            request_plan = get_conversation_injection_plan(req, create=False)
            if request_plan is not None:
                request_plan.render_into(req)
            if direct_image_mode:
                existing = getattr(req, "image_urls", None)
                if not isinstance(existing, list):
                    existing = []
                for image_ref in request_image_refs:
                    if image_ref not in existing:
                        existing.append(image_ref)
                req.image_urls = existing
                logger.info(
                    "私聊单图主链已挂载图片: user=%s provider=%s source=%s images=%s has_vision=%s",
                    user_id,
                    direct_provider_id,
                    direct_provider_source,
                    len(existing),
                    bool(vision_text),
                )
            start = time.time()
            captured_tool_sends = []
            llm_resp = None
            try:
                async def _runner_factory():
                    if framework_context is None:
                        return None
                    built = await build_main_agent(
                        event=framework_event,
                        plugin_context=framework_context,
                        config=build_cfg,
                        req=req,
                    )
                    return built

                capture_runner = getattr(self, "_capture_framework_send_message_calls", None)
                framework_lock = getattr(self, "_framework_agent_lock", None)
                if not isinstance(framework_lock, asyncio.Lock):
                    framework_lock = asyncio.Lock()
                    self._framework_agent_lock = framework_lock
                async with framework_lock:
                    if callable(capture_runner) and umo:
                        result, captured_tool_sends = await capture_runner(
                            target_session=umo,
                            runner_factory=_runner_factory,
                        )
                        if captured_tool_sends:
                            logger.info(
                                "私聊单图主链拦截到框架工具直发: user=%s count=%s",
                                user_id,
                                len(captured_tool_sends),
                            )
                    else:
                        result = await _runner_factory()
                        runner_for_step = getattr(result, "agent_runner", None) if result else None
                        if runner_for_step is not None and hasattr(runner_for_step, "step_until_done"):
                            async for _ in runner_for_step.step_until_done(20):
                                pass
            except Exception as exc:
                if direct_image_mode and self._exception_indicates_image_input_unsupported(exc):
                    logger.warning(
                        "私聊单图主链模型不支持图片输入,已降级为视觉摘要兜底: user=%s provider=%s error=%s",
                        user_id,
                        direct_provider_id,
                        _single_line(exc, 180),
                    )
                    direct_image_mode = False
                    reply = ""
                    reply_source = "image_input_unsupported_fallback"
                    result = None
                elif self._exception_indicates_tool_schema_invalid(exc):
                    logger.warning(
                        "私聊单图主链工具 schema 不兼容,已转入兜底回复: user=%s error=%s",
                        user_id,
                        _single_line(exc, 180),
                    )
                    direct_image_mode = False
                    reply = ""
                    reply_source = "tool_schema_invalid_fallback"
                    result = None
                else:
                    logger.warning(
                        "私聊单图主链异常,已转入人格兜底: user=%s error=%s",
                        user_id,
                        _single_line(exc, 180),
                        exc_info=True,
                    )
                    direct_image_mode = False
                    reply = ""
                    reply_source = "main_chain_exception_fallback"
                    result = None
            finally:
                if selected_provider_changed:
                    try:
                        framework_event.set_extra("selected_provider", previous_selected_provider)
                    except Exception:
                        pass
            runner = getattr(result, "agent_runner", None) if result else None
            if llm_resp is None:
                llm_resp = runner.get_final_llm_resp() if runner else None
            if "reply" not in locals():
                reply = self._private_image_framework_response_text(llm_resp)
                if reply and not str(getattr(llm_resp, "completion_text", "") or "").strip():
                    logger.info(
                        "私聊单图主链 completion_text 为空,已从 result_chain 恢复可见文本: user=%s preview=%s",
                        user_id,
                        _single_line(reply, 180),
                    )
            if "reply_source" not in locals():
                reply_source = "main_chain"
            reply = self._restore_private_image_framework_tts_reply(
                reply,
                framework_event,
            )
            if reply and self._private_image_reply_is_internal_error(reply):
                logger.warning(
                    "私聊单图主链返回内部错误文本,已拦截转入兜底: user=%s preview=%s",
                    user_id,
                    _single_line(reply, 180),
                )
                reply = ""
                reply_source = "internal_error_fallback"
            if reply and direct_image_mode and self._private_image_reply_denies_image_capability(reply):
                logger.warning(
                    "私聊单图主链返回无法看图声明,已转视觉摘要兜底: user=%s provider=%s preview=%s",
                    user_id,
                    direct_provider_id,
                    _single_line(reply, 180),
                )
                reply = ""
                direct_image_mode = False
                reply_source = "image_capability_denial_fallback"
            if not reply and captured_tool_sends:
                captured_text_parts: list[str] = []
                sanitizer = getattr(self, "_sanitize_captured_plain_text", None)
                for call in reversed(captured_tool_sends):
                    messages = getattr(call, "messages", [])
                    if not isinstance(messages, list):
                        continue
                    for item in messages:
                        if not isinstance(item, dict):
                            continue
                        if str(item.get("type") or "").strip().lower() != "plain":
                            continue
                        raw_text = item.get("text")
                        text_value = sanitizer(raw_text) if callable(sanitizer) else _single_line(raw_text, 260)
                        if text_value:
                            captured_text_parts.append(text_value)
                    if captured_text_parts:
                        break
                reply = _single_line("\n".join(captured_text_parts), 500)
                if reply:
                    reply_source = "main_chain_tool_capture"
                    logger.info(
                        "私聊单图主链工具直发文本已转为普通回复: user=%s chars=%s reply_preview=%s",
                        user_id,
                        len(reply),
                        _single_line(reply, 180),
                    )
            if reply and vision_text and self._private_image_reply_ignores_vision_summary(reply):
                logger.info(
                    "私聊单图主链疑似忽略视觉摘要,转入兜底回复: user=%s reply_preview=%s",
                    user_id,
                    _single_line(reply, 180),
                )
                reply = ""
            if reply and self._private_image_reply_drifts_to_stale_context(reply):
                trimmed_reply = self._trim_private_image_stale_context_tail(reply)
                if trimmed_reply and trimmed_reply != reply and not self._private_image_reply_drifts_to_stale_context(trimmed_reply):
                    logger.info(
                        "私聊单图主链回复夹带旧上下文,已裁剪: user=%s before=%s after=%s",
                        user_id,
                        _single_line(reply, 180),
                        _single_line(trimmed_reply, 180),
                    )
                    reply = trimmed_reply
                else:
                    logger.info(
                        "私聊单图主链回复夹带旧上下文,转入兜底回复: user=%s reply_preview=%s",
                        user_id,
                        _single_line(reply, 180),
                    )
                    reply = ""
            if reply:
                reply_preview = reply
                preview_cleaner = getattr(self, "_sanitize_orphan_tts_placeholders", None)
                if callable(preview_cleaner):
                    try:
                        reply_preview = preview_cleaner(reply_preview)
                    except Exception:
                        reply_preview = reply
                logger.info(
                    "私聊单图主链回复生成: user=%s chars=%s intent=%s ownership=%s reply_preview=%s",
                    user_id,
                    len(reply),
                    intent_line or "无",
                    ownership_line or "无",
                    _single_line(reply_preview, 180),
                )
            if not reply:
                if not vision_text and images and has_visual_provider:
                    vision_text = self._completed_private_image_vision_task_text(vision_task)
                    if vision_text:
                        logger.info(
                            "私聊单图兜底前取到后台视觉摘要: user=%s preview=%s",
                            user_id,
                            _single_line(vision_text, 220),
                        )
                    elif not vision_wait_timed_out:
                        vision_text = _single_line(await self._transcribe_private_inbound_images(images, umo=umo), self._private_image_vision_text_limit(len(images)))
                    else:
                        logger.info("私聊单图兜底阶段跳过重复视觉转述: user=%s", user_id)
                    setattr(event, "private_companion_delayed_image_vision_text", vision_text)
                    ownership_line = self._private_image_ownership_line(vision_text)
                    intent_line = self._private_image_intent_line(vision_text)
                    reply_objective = self._private_image_reply_objective(ownership_line, vision_text=vision_text)
                fallback_system_prompt = str(getattr(req, "system_prompt", "") or "").strip()
                reply, reply_source = await self._generate_private_image_fallback_reply(
                    vision_text=vision_text,
                    reply_objective=reply_objective,
                    system_prompt=fallback_system_prompt,
                    user_id=user_id,
                )
                if not vision_text:
                    logger.info(
                        "私聊单图无可靠视觉摘要,已尝试人格兜底回复: user=%s chars=%s reply_preview=%s",
                        user_id,
                        len(reply),
                        _single_line(reply, 180),
                    )
                else:
                    logger.info(
                        "私聊单图兜底回复生成: user=%s chars=%s intent=%s ownership=%s objective=%s reply_preview=%s",
                        user_id,
                        len(reply),
                        intent_line or "无",
                        ownership_line or "无",
                        _single_line(reply_objective, 120),
                        _single_line(reply, 180),
                    )
                if not reply:
                    logger.warning(
                        "私聊单图原生链路与兜底 LLM 均未生成有效回复,不启用本地静态兜底: user=%s images=%s has_vision=%s",
                        user_id,
                        len(images),
                        bool(vision_text),
                    )
                    self._record_llm_usage(
                        provider_id="framework",
                        task="private_image_only_framework",
                        prompt=prompt,
                        completion="",
                        elapsed_ms=int((time.time() - start) * 1000),
                        success=False,
                        resp=llm_resp,
                        budget_exempt=True,
                    )
                    return
                logger.info("私聊单图原生链路回复为空,已使用兜底 LLM 回复: user=%s images=%s", user_id, len(images))
            self._record_llm_usage(
                provider_id="framework",
                task="private_image_only_framework",
                prompt=prompt,
                completion=reply,
                elapsed_ms=int((time.time() - start) * 1000),
                success=True,
                resp=llm_resp,
                budget_exempt=True,
            )
            await self._record_private_image_vision_feedback_target(
                user_id=user_id,
                image_sources=raw_image_sources,
                vision_text=vision_text,
                reply=reply,
                ownership=ownership_line,
                intent=intent_line,
            )
            sent_reply = await self._send_private_image_reply_text(event, reply)
            buffer["delayed_reply_sent"] = bool(sent_reply)
            buffer["delayed_reply_sent_ts"] = _private_image_host._now_ts() if sent_reply else 0.0
            if sent_reply:
                await self._archive_private_image_turn_context(
                    event,
                    user_id=user_id,
                    vision_text=vision_text,
                    reply=sent_reply,
                    image_count=len(images),
                )
            if reply_source == "main_chain":
                logger.info("私聊单图无补充说明,已由原生 LLM 链路回复: user=%s images=%s", user_id, len(images))
            else:
                logger.info(
                    "私聊单图无补充说明,原生链路为空,已由兜底回复发送: user=%s images=%s source=%s",
                    user_id,
                    len(images),
                    reply_source,
                )
        except Exception as exc:
            logger.warning("私聊单图延迟回复失败: user=%s error=%s", user_id, _single_line(exc, 180), exc_info=True)

    async def _finalize_private_image_buffer_after_wait(self, key: str, user_id: str, first_ts: float) -> None:
        wait = self._message_debounce_seconds("image")
        remaining = max(0.0, first_ts + wait - _private_image_host._now_ts())
        if remaining > 0:
            await asyncio.sleep(remaining)
        buffers = getattr(self, "_semantic_message_buffers", None)
        buffer = buffers.get(key) if isinstance(buffers, dict) else None
        if not isinstance(buffer, dict):
            return
        messages = buffer.get("messages") if isinstance(buffer.get("messages"), list) else []
        placeholder = "用户刚刚先单独发送了一张图片,可能马上会补充说明。"
        has_followup = any(
            isinstance(item, dict)
            and (cleaned := _single_line(item.get("text"), 260))
            and cleaned != placeholder
            for item in messages
        )
        if has_followup:
            logger.info("私聊单图已由补充消息接管: user=%s", user_id)
            return
        claimed_ts = _safe_float(buffer.get("vision_context_claimed_ts"), 0.0)
        if claimed_ts > 0:
            logger.info(
                "私聊单图上下文已由补充文字请求认领,跳过延迟派发: user=%s claimed_ago=%.1fs",
                user_id,
                max(0.0, _private_image_host._now_ts() - claimed_ts),
            )
            buffers.pop(key, None)
            return
        original_event = buffer.get("original_event")
        delayed_buffer = dict(buffer)
        delayed_buffer["images"] = list(buffer.get("images") or [])
        delayed_buffer["messages"] = list(messages)
        handoff = (
            self._remember_private_image_vision_handoff(key, original_event, delayed_buffer)
            if isinstance(original_event, _private_image_host.AstrMessageEvent)
            else None
        )
        buffers.pop(key, None)
        if isinstance(original_event, _private_image_host.AstrMessageEvent):
            try:
                await self._send_delayed_private_image_only_event(original_event, user_id, delayed_buffer)
            finally:
                if isinstance(handoff, dict):
                    handoff["delayed_dispatch_finished_ts"] = _private_image_host._now_ts()
                    handoff["delayed_reply_sent"] = bool(delayed_buffer.get("delayed_reply_sent"))
                    handoff["delayed_reply_sent_ts"] = _safe_float(
                        delayed_buffer.get("delayed_reply_sent_ts"),
                        0.0,
                    )
                    completed_vision = self._completed_private_image_vision_task_text(handoff.get("vision_task"))
                    if completed_vision:
                        handoff["vision_text"] = _single_line(
                            completed_vision,
                            self._private_image_vision_text_limit(len(handoff.get("images") or [])),
                        )
            return
        vision_task = delayed_buffer.get("vision_task")
        if isinstance(vision_task, asyncio.Task) and not vision_task.done():
            vision_task.cancel()
        logger.info("私聊单图等待补充后无文字指示,但原事件不可用: user=%s", user_id)

