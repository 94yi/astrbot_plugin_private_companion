# -*- coding: utf-8 -*-
"""PrivateImageIngestCacheMixin。

由 tools/split_mixin_domain.py 从 private_image.py 机械抽取（37 个方法 + 0 个模块级名字 + 0 个类级赋值 / 1054 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateImageMixin）。
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import html
import io
import os
import re
import shutil
import tempfile
import urllib.request
from .conversation_injection_plan import PLACEMENT_DYNAMIC_SYSTEM, get_conversation_injection_plan
from .conversation_prompt_section import PromptSection
from .helpers import _missing_optional_model_dependency, _safe_float, _safe_int, _single_line
from .persona_config import runtime_persona_setting
from .private_image_shared import PREPARED_IMAGE_MAX_AGE_SECONDS, _private_image_host, logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.provider import ProviderRequest
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlparse, urlsplit, urlunparse, urlunsplit



class PrivateImageIngestCacheMixin:
    """PrivateImageIngestCacheMixin（从 PrivateImageMixin 拆出）。"""


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

