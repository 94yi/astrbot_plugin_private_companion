# -*- coding: utf-8 -*-
"""图片 / 素材 / 参考图 域页面 API。

由 tools/split_page_api_media.py 从 page_api.py 机械抽取（123 个方法 / 4287 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApi）。
"""
from __future__ import annotations

import asyncio
import io
import json
import math
import os
import time
import re
import base64
import binascii
import hashlib
import mimetypes
import secrets
import uuid
from contextlib import asynccontextmanager
from copy import copy, deepcopy
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote, urlparse
from quart import send_file
from .page_api_shared import _page_api_host, _page_api_host_request as request
from .conversation_prompt_section import (
    PromptRenderMode,
    prompt_document,
    prompt_heading_ref,
    prompt_section,
    render_prompt_content,
    render_prompt_document,
    render_prompt_sections,
)
from .diagnostic_envelope import DIAGNOSTIC_ENVELOPE_VERSION, diagnostic_test_id, normalize_diagnostic_result
from .helpers import _MISSING, _flat_get, _normalize_timezone_name, _normalize_timezone_setting, _path_text, _redact_outbound_secrets, _safe_int, _set_into_config, _strip_internal_message_blocks, _text_looks_garbled, _text_similarity, _today_key, normalize_bot_relationship_cards
from .wardrobe_assets import asset_abs_path, asset_root, load_asset_index
from .reference_asset_gate import ReferenceAssetGate
from .owned_reaction_asset_catalog import MAX_ASSET_BYTES, OwnedReactionAssetCatalog
from .photo_reference_catalog import (
    CATALOG_VERSION,
    MAX_LIBRARY_REFERENCES,
    CatalogValidationError,
    PhotoReference,
    load_catalog,
    project_reference_candidate,
    validate_and_serialize,
)
from .photo_reference_metadata import (
    build_reference_metadata_review_prompt,
    compile_reference_metadata,
    merge_reference_questionnaire_evidence,
    normalize_reviewed_reference_intent,
)
from .photo_reference_selection import SelectionResult, run_photo_selection_trial
from .reference_assets import (
    REFERENCE_ASSET_MAX_BYTES,
    REFERENCE_ASSET_MAX_PER_OWNER,
    REFERENCE_ASSET_MAX_TOTAL,
    REFERENCE_ASSET_ROLES,
    normalize_reference_asset,
    normalize_reference_asset_scope,
    normalize_reference_owner_id,
)
from .page_backend import MigrationBackupService, build_route_bindings, generation_log_candidates
from .logging_util import get_module_logger
from .page_api_media_reference import PrivateCompanionPageApiMediaReferenceMixin
from .page_api_media_diagnostics import PrivateCompanionPageApiMediaDiagnosticsMixin
try:
    from PIL import Image as PILImage
    from PIL import ImageOps as PILImageOps
except Exception:  # pragma: no cover - Pillow 缺失时回退到原图预览
    PILImage = None
    PILImageOps = None
# ---------------------------------------------------------------------------
# Host-module globals re-materialised for this mixin module.
# These names are referenced by the moved method bodies but are defined at
# module scope in page_api.py (as globals / module-level functions), so the
# mechanical split could not pick them up from import statements alone.
# No behaviours are changed: only definitions are repeated here so that the
# module can resolve its own globals through the normal module namespace.
# ---------------------------------------------------------------------------
logger = get_module_logger(__name__)



PLUGIN_NAME = "astrbot_plugin_private_companion"
PAGE_API_PREFIX = f"/{PLUGIN_NAME}/page"
IMAGE_CACHE_THUMBNAIL_MAX_EDGE = 160
IMAGE_CACHE_THUMBNAIL_QUALITY = 78
# The guided editor must never leave a WebUI request waiting forever when the
# configured main model or its upstream connection stops responding.
# Reference assets are an independent store for member/role/knowledge images. Keep
# the limits generous enough for a small visual knowledge base while preventing
# an accidental page upload from exhausting the plugin data directory.
# WebUI catalog uploads are content-addressed so repeated submissions do not
# create another copy of the same image. These limits cover abandoned uploads
# that are no longer referenced by the saved catalog.
# A 12 MiB image expands to roughly 16 MiB when Base64 encoded. Leave room for
# the data URL and JSON envelope, while rejecting oversized bodies before
# Quart parses them into memory.



class PrivateCompanionPageApiMediaMixin(PrivateCompanionPageApiMediaDiagnosticsMixin, PrivateCompanionPageApiMediaReferenceMixin):
    """图片 / 素材 / 参考图 域（从 PrivateCompanionPageApi 拆出）。"""

    def _image_api_runtime_lock(self) -> asyncio.Lock:
        lock = getattr(self.plugin, "_external_image_api_runtime_lock", None)
        if lock is None:
            lock = asyncio.Lock()
            self.plugin._external_image_api_runtime_lock = lock
        return lock
    @staticmethod
    def _page_asset_prefix() -> str:
        """Keep generated asset URLs on the transport serving this request."""
        try:
            if str(request.path or "").startswith("/api/v1/"):
                return "/api/v1"
        except RuntimeError:
            pass
        return PAGE_API_PREFIX
    def _image_extension_contract_status(self, api: Any | None = None) -> dict[str, Any]:
        """Expose the effective Companion/Image contract without invoking generation."""
        getter = getattr(self.plugin, "_image_companion_contract", None)
        if not callable(getter):
            return {}
        try:
            mode, _current, generation, reason = getter(api=api) if api is not None else getter()
        except TypeError:
            try:
                mode, _current, generation, reason = getter()
            except Exception:
                return {"mode": "unknown", "generation": 0, "available": False, "reason": "contract_probe_failed"}
        except Exception:
            return {"mode": "unknown", "generation": 0, "available": False, "reason": "contract_probe_failed"}
        return {
            "mode": self._single_line(mode, 30),
            "generation": self._int(generation),
            "available": mode in {"current", "current_compat"},
            "reason": self._single_line(reason, 120),
        }
    def _image_generation_extension_status(self) -> dict[str, Any]:
        """Read the public Image extension snapshot without making it mandatory."""
        getter = getattr(self.plugin, "_image_companion_status", None)
        if not callable(getter):
            return {}
        try:
            status = getter()
        except Exception:
            return {}
        return dict(status) if isinstance(status, dict) else {}
    def _image_generation_called_plugin_diagnostics(
        self,
        status: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        snapshot = dict(status) if isinstance(status, dict) else self._image_generation_extension_status()
        generation = snapshot.get("generation")
        generation = generation if isinstance(generation, dict) else {}
        has_image_bridge = callable(getattr(self.plugin, "_image_companion_status", None))
        plugin_id = self._single_line(
            generation.get("plugin_id") or snapshot.get("plugin_id"),
            100,
        )
        if not plugin_id and has_image_bridge:
            plugin_id = "astrbot_plugin_image_companion"
        plugin_name = self._single_line(
            generation.get("plugin_name") or snapshot.get("plugin_name"),
            100,
        )
        if not plugin_name and plugin_id == "astrbot_plugin_image_companion":
            plugin_name = "我会画给你看"
        return {
            "called_plugin": plugin_id,
            "called_plugin_name": plugin_name,
            "called_plugin_version": self._single_line(
                generation.get("plugin_version") or snapshot.get("plugin_version"),
                60,
            ),
            "called_plugin_api_version": self._single_line(
                generation.get("api_version") or snapshot.get("api_version"),
                80,
            ),
            "called_plugin_status_schema": self._single_line(
                generation.get("status_schema_version") or snapshot.get("status_schema_version"),
                80,
            ),
        }
    def _daily_outfit_image_path(self) -> Path | None:
        data = getattr(self.plugin, "data", {}) if isinstance(getattr(self.plugin, "data", {}), dict) else {}
        item = data.get("daily_outfit_photo") if isinstance(data.get("daily_outfit_photo"), dict) else {}
        path_text = self._single_line(item.get("path"), 500)
        if not path_text:
            return None
        try:
            path = Path(path_text).resolve()
        except Exception:
            return None
        if not path.exists() or not path.is_file():
            return None
        return path
    async def get_daily_outfit_image(self):
        path = self._daily_outfit_image_path()
        if path is None:
            return self._error("今日穿搭图片不存在")
        response = await send_file(str(path))
        response.headers["Cache-Control"] = "private, max-age=3600"
        return response
    async def get_daily_outfit_image_data(self) -> dict[str, Any]:
        path = self._daily_outfit_image_path()
        if path is None:
            return self._error("今日穿搭图片不存在")
        try:
            mime = mimetypes.guess_type(str(path))[0] or "image/png"
            raw = await asyncio.to_thread(path.read_bytes)
            return self._ok(
                {
                    "mime": mime,
                    "size": len(raw),
                    "data_url": f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}",
                }
            )
        except Exception as exc:
            logger.warning("读取每日穿搭图片失败: %s", self._single_line(exc, 160))
            return self._exception_error("读取每日穿搭图片失败")
    @staticmethod
    def _vision_provider_test_image_data_url() -> str:
        # Keep the connection test cheap while satisfying visual providers such
        # as Qwen-VL that reject 1x1 placeholders (both sides are 32 pixels).
        return (
            "data:image/png;base64,"
            "iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAIAAAD8GO2jAAAAmElEQVR42mP8//8/Ay0BEwONwdC3gAWZo5e4kCqGXpofP0A+wLSfVIAZBqOpiJw4wBWsaHGDS5w0HyBHGjHsEVhUIIcvMWxyIhmXfiLz4/COA73EhcSUr/iVDYIgwu8Jgl5kIiP9kKSSqHyANZThglTIBxCDsIYGQV+yEB8CaBYQGYAsDGRV5RTVB9RqW9ApHzCOtk0H3AIAj19C2ZNGr00AAAAASUVORK5CYII="
        )
    def _visual_provider_for_test(self, provider_id: str) -> Any:
        getter = getattr(self.plugin, "_private_image_provider_by_id", None)
        provider = getter(provider_id) if callable(getter) else None
        if provider is not None:
            return provider
        context = getattr(self.plugin, "context", None)
        context_getter = getattr(context, "get_provider_by_id", None)
        return context_getter(provider_id) if callable(context_getter) else None
    def _visual_provider_test_timeout(self, provider_id: str, key: str, requested: float | None) -> float:
        if requested is not None:
            return requested
        getter = getattr(self.plugin, "_private_image_provider_timeout_seconds", None)
        if callable(getter):
            source = "plugin_vision" if key == "PLUGIN_VISION_PROVIDER_ID" else "reading_archive_vision"
            return max(0.0, float(getter(provider_id, source)))
        return 30.0
    def _visual_call_error_text(self, exc: Exception, *, timeout: float = 0.0) -> str:
        if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
            duration = f"（{timeout:g} 秒）" if timeout > 0 else ""
            return f"视觉模型图片请求超时{duration}"
        unsupported = getattr(self.plugin, "_exception_indicates_image_input_unsupported", None)
        if callable(unsupported) and unsupported(exc):
            return "Provider 拒绝图片输入，请确认所选模型支持视觉"
        detail = self._single_line(exc, 220)
        return detail or f"视觉模型调用失败（{exc.__class__.__name__}）"
    async def get_reaction_library_image_data(self) -> dict[str, Any]:
        item_id = self._single_line(request.args.get("id"), 64)
        if not item_id:
            return self._error("缺少表情包 id")
        try:
            result = await asyncio.to_thread(self._reaction_library().get_image_data, item_id)
            return self._ok(result) if result else self._error("表情包不存在或图片文件已丢失")
        except Exception as exc:
            logger.error("读取表情包图片失败: %s", exc, exc_info=True)
            return self._error(str(exc))
    async def list_image_cache(self) -> dict[str, Any]:
        try:
            scope_filter = self._single_line(request.args.get("scope"), 40)
            keyword = self._single_line(request.args.get("q"), 120).lower()
            limit = self._query_int("limit", 80, 1, 300)
            offset = self._query_int("offset", 0, 0, 100000)
            async with self.plugin._data_lock:
                data = deepcopy(self.plugin.data)
            cache = data.get("private_image_vision_cache") if isinstance(data.get("private_image_vision_cache"), dict) else {}
            rows: list[dict[str, Any]] = []
            scopes: set[str] = set()
            for key, raw in cache.items():
                if not isinstance(raw, dict):
                    continue
                item = self._image_cache_item_summary(str(key), raw)
                scope = item.get("scope") or "private_image"
                scopes.add(str(scope))
                if scope_filter and scope_filter != "all" and scope != scope_filter:
                    continue
                if keyword:
                    haystack = " ".join(
                        str(item.get(name) or "")
                        for name in (
                            "key",
                            "text",
                            "provider_id",
                            "scope",
                            "image_keys_text",
                            "image_aliases_text",
                            "image_type",
                            "ownership",
                            "intent",
                        )
                    ).lower()
                    if keyword not in haystack:
                        continue
                rows.append(item)
            rows.sort(
                key=lambda item: (
                    self._float(item.get("last_hit_ts"))
                    or self._float(item.get("created_ts")),
                    self._float(item.get("created_ts")),
                ),
                reverse=True,
            )
            total = len(rows)
            return self._ok(
                {
                    "items": rows[offset : offset + limit],
                    "total": total,
                    "offset": offset,
                    "limit": limit,
                    "scopes": sorted(scopes),
                    "enabled": bool(getattr(self.plugin, "enable_private_image_vision_cache", False)),
                    "max_items": int(getattr(self.plugin, "private_image_vision_cache_max_items", 0) or 0),
                }
            )
        except Exception as exc:
            logger.error(f"获取图片缓存失败: {exc}", exc_info=True)
            return self._exception_error(str(exc))
    async def update_image_cache_item(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        key = self._single_line(payload.get("key"), 120)
        action = self._single_line(payload.get("action"), 20).lower() or "regenerate"
        if not key:
            return self._error("缺少缓存 key")
        text = str(payload.get("text") or "").strip()
        if not text:
            return self._error("视觉摘要不能为空")
        try:
            async with self.plugin._data_lock:
                cache = self.plugin.data.get("private_image_vision_cache")
                if not isinstance(cache, dict) or not isinstance(cache.get(key), dict):
                    return self._error("缓存条目不存在")
                item = cache[key]
                scope = self._single_line(item.get("scope"), 40) or "private_image"
                item["text"] = self._single_line(text, 900 if scope == "forward_image" else 600)
                if "provider_id" in payload:
                    item["provider_id"] = self._single_line(payload.get("provider_id"), 160)
                if "scope" in payload:
                    next_scope = self._single_line(payload.get("scope"), 40)
                    if next_scope:
                        item["scope"] = next_scope
                item["edited_ts"] = time.time()
                self.plugin._save_data_sync(sections={"private_image_vision_cache"})
                updated = deepcopy(item)
            return self._ok(self._image_cache_item_summary(key, updated))
        except Exception as exc:
            logger.error(f"更新图片缓存失败: {exc}", exc_info=True)
            return self._exception_error(str(exc))
    def _image_cache_preview_dir(self) -> Path:
        return (Path(getattr(self.plugin, "data_dir", "")) / "private_image_cache_previews").resolve()
    @staticmethod
    def _image_cache_clean_key(key: str) -> str:
        return re.sub(r"[^0-9A-Za-z_.-]+", "_", str(key or ""))[:80]
    def _image_cache_preview_file(self, key: str, raw: dict[str, Any] | None = None) -> Path | None:
        clean_key = self._image_cache_clean_key(key)
        if not clean_key:
            return None
        base = self._image_cache_preview_dir()
        candidates: list[Path] = []
        preview_path = self._single_line((raw or {}).get("preview_path"), 260) if isinstance(raw, dict) else ""
        if preview_path:
            try:
                candidates.append(Path(preview_path).resolve())
            except Exception:
                pass
        candidates.extend(base / f"{clean_key}{suffix}" for suffix in (".jpg", ".jpeg", ".png", ".webp", ".gif"))
        for candidate in candidates:
            try:
                if candidate.is_file() and candidate.is_relative_to(base):
                    return candidate
            except Exception:
                continue
        return None
    def _image_cache_thumbnail_file(self, key: str) -> Path | None:
        clean_key = self._image_cache_clean_key(key)
        if not clean_key:
            return None
        return self._image_cache_preview_dir() / ".thumbnails" / f"{clean_key}.webp"
    async def _get_or_create_image_cache_thumbnail(self, key: str, source: Path) -> Path | None:
        return await asyncio.to_thread(self._build_image_cache_thumbnail_sync, key, source)
    def _build_image_cache_thumbnail_sync(self, key: str, source: Path) -> Path | None:
        if PILImage is None:
            return None
        target = self._image_cache_thumbnail_file(key)
        if target is None:
            return None
        try:
            if target.is_file() and target.stat().st_mtime >= source.stat().st_mtime:
                return target
        except OSError:
            pass

        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with PILImage.open(source) as image:
                if PILImageOps is not None:
                    image = PILImageOps.exif_transpose(image)
                image.seek(0)
                if image.mode in {"RGBA", "LA"} or (image.mode == "P" and "transparency" in image.info):
                    rgba = image.convert("RGBA")
                    background = PILImage.new("RGBA", rgba.size, (255, 255, 255, 255))
                    background.alpha_composite(rgba)
                    image = background.convert("RGB")
                else:
                    image = image.convert("RGB")
                resampling = getattr(PILImage, "Resampling", PILImage)
                image.thumbnail(
                    (IMAGE_CACHE_THUMBNAIL_MAX_EDGE, IMAGE_CACHE_THUMBNAIL_MAX_EDGE),
                    resampling.LANCZOS,
                )
                image.save(
                    temporary,
                    format="WEBP",
                    quality=IMAGE_CACHE_THUMBNAIL_QUALITY,
                    method=6,
                )
            temporary.replace(target)
            return target
        except Exception as exc:
            logger.warning(
                "生成图片缓存缩略图失败: key=%s error=%s",
                self._single_line(key, 80),
                self._single_line(exc, 160),
            )
            return None
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
    def _restore_image_cache_preview_metadata(self, key: str, raw: dict[str, Any]) -> bool:
        preview = self._image_cache_preview_file(key, raw)
        if preview is None:
            return False
        changed = False
        if self._single_line(raw.get("preview_path"), 260) != str(preview):
            raw["preview_path"] = str(preview)
            changed = True
        try:
            preview_size = preview.stat().st_size
        except Exception:
            preview_size = 0
        if _safe_int(raw.get("preview_size"), 0, 0) != preview_size:
            raw["preview_size"] = preview_size
            changed = True
        return changed
    async def _resolve_image_cache_preview_for_request(self) -> tuple[str, Path] | tuple[dict[str, Any], int]:
        key = self._single_line(request.args.get("key"), 120)
        if not key:
            return self._error("缺少缓存 key")
        async with self.plugin._data_lock:
            cache = self.plugin.data.get("private_image_vision_cache")
            item = cache.get(key) if isinstance(cache, dict) else None
            if not isinstance(item, dict):
                return self._error("缓存条目不存在")
            path = self._image_cache_preview_file(key, item)
            if path is None:
                return self._error("缓存预览文件不存在")
            if self._restore_image_cache_preview_metadata(key, item):
                self.plugin._save_data_sync(sections={"private_image_vision_cache"})
        return key, path
    @staticmethod
    async def _encode_image_cache_file_data_url(
        path: Path,
        mime: str = "",
        *,
        max_bytes: int = 0,
    ) -> dict[str, str]:
        def read_file() -> bytes:
            if max_bytes <= 0:
                return path.read_bytes()
            with path.open("rb") as stream:
                payload = stream.read(max_bytes + 1)
            if len(payload) > max_bytes:
                raise ValueError(f"预览文件超过 {max_bytes} bytes 上限")
            return payload

        raw = await asyncio.to_thread(read_file)
        content_type = mime or mimetypes.guess_type(str(path))[0] or "image/jpeg"
        encoded = base64.b64encode(raw).decode("ascii")
        return {"data_url": f"data:{content_type};base64,{encoded}", "mime": content_type}
    async def describe_wardrobe_image(self) -> dict[str, Any]:
        """Describe one garment image with the vision model for the wardrobe panel."""

        payload = await request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            return self._error("请求体必须是 JSON 对象")
        describer = getattr(self.plugin, "_wardrobe_describe_image", None)
        if not callable(describer):
            return self._error("当前插件实例不支持衣柜识图")
        raw_source = _path_text(payload.get("source") or payload.get("path"), 1200)
        if not raw_source:
            return self._error("缺少图片路径")
        source = self._wardrobe_page_local_path(raw_source)
        if source is None:
            return self._error("只能描述已上传到插件目录的 PNG、JPEG 或 WebP 图片")
        note = self._single_line(payload.get("note"), 200)
        # 面板可以先选模型再识图，不必等保存；这里只把候选排到最前，
        # 无效或不支持图片的 id 会被下游跳过并回退到已配置模型。
        preferred = self._single_line(payload.get("provider_id"), 160)
        try:
            parsed, error = await describer(
                [str(source)], note=note, umo="", provider_id=preferred
            )
        except Exception as exc:
            logger.warning("衣柜识图接口失败: %s", self._single_line(exc, 160), exc_info=True)
            return self._error("识图失败，请稍后再试")
        if parsed is None:
            return self._error(error or "识图模型没有返回可用的衣物描述")
        return self._ok(
            {
                "source": str(source),
                # 类型决定去向：散件进 wardrobe_items，整套/参考进 wardrobe_outfits
                "kind": str(parsed.get("kind") or ""),
                "slot": str(parsed.get("slot") or ""),
                "name": parsed.get("name", ""),
                "description": parsed.get("description", ""),
                "tags": list(parsed.get("tags") or []),
            }
        )
    def _wardrobe_asset_local_path(self, asset_id: Any) -> Path | None:
        """Resolve one asset id to a file inside this plugin's wardrobe asset store."""

        clean_id = self._single_line(asset_id, 80)
        data_dir = str(getattr(self.plugin, "data_dir", "") or "")
        if not clean_id or not data_dir:
            return None
        try:
            record = load_asset_index(data_dir).get(clean_id)
            if record is None:
                return None
            root = asset_root(data_dir).expanduser().resolve()
            path = asset_abs_path(data_dir, record).expanduser().resolve()
            if not path.is_file():
                return None
            # 索引里的 path 是相对路径，但被手改成 ../.. 就能读到插件之外，
            # 所以这里必须按目录兜住（与 _wardrobe_page_local_path 同一思路）。
            if path != root and root not in path.parents:
                return None
            return path
        except (OSError, ValueError):
            return None
    async def get_wardrobe_asset_image(self) -> dict[str, Any]:
        """Return one wardrobe asset as a data URL for the draft queue thumbnails."""

        payload = await request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            return self._error("请求体必须是 JSON 对象")
        asset_id = self._single_line(payload.get("asset_id"), 80)
        path = self._wardrobe_asset_local_path(asset_id)
        if path is None:
            return self._error("找不到这个素材，或它不是插件目录里的图片")
        try:
            # 与同文件其它素材接口一致\uff1a未知后缀不能猜成 png\u3002
            mime = mimetypes.guess_type(str(path))[0] or ""
            if not mime.startswith("image/") or mime not in self.WARDROBE_ASSET_IMAGE_MIMES:
                return self._error("这个素材不是可以预览的图片")
            # 先 stat 再读：导入期有上限，但手工放进目录、手改 index 的文件不受约束，
            # 整份读进内存再拒绝会白白分配这份内存（与同文件其它素材接口一致）。
            try:
                size = int((await asyncio.to_thread(path.stat)).st_size)
            except OSError:
                return self._error("这个素材已经读不到了")
            if size > self.WARDROBE_ASSET_IMAGE_MAX_BYTES:
                return self._error("素材图片过大，无法在面板里预览")
            raw = await asyncio.to_thread(path.read_bytes)
            # 读完再比一次：文件可能在 stat 与 read 之间被换掉。
            if len(raw) > self.WARDROBE_ASSET_IMAGE_MAX_BYTES:
                return self._error("素材图片过大，无法在面板里预览")
            return self._ok(
                {
                    "asset_id": asset_id,
                    "mime": mime,
                    "size": len(raw),
                    "data_url": f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}",
                }
            )
        except Exception as exc:
            logger.warning("读取衣柜素材图片失败: %s", self._single_line(exc, 160))
            return self._exception_error("读取衣柜素材图片失败")
    def _owned_reaction_asset_catalog(self) -> OwnedReactionAssetCatalog:
        return OwnedReactionAssetCatalog(getattr(self.plugin, "data_dir", ""))
    def _owned_reaction_asset_entries(self) -> list[dict[str, Any]]:
        entries = getattr(self.plugin, "owned_reaction_assets", [])
        if not isinstance(entries, list):
            return []
        return [dict(entry) for entry in entries if isinstance(entry, dict)][:96]
    async def list_owned_reaction_assets(self) -> dict[str, Any]:
        try:
            catalog = self._owned_reaction_asset_catalog()
            projection = catalog.public_projection(self._owned_reaction_asset_entries())
            return self._ok(
                {
                    "enabled": bool(
                        getattr(
                            self.plugin,
                            "enable_owned_reaction_asset_workbench",
                            False,
                        )
                    ),
                    **projection,
                }
            )
        except Exception:
            logger.error("owned reaction asset status failed")
            return self._exception_error("无法读取自有反应图素材状态")
    async def get_owned_reaction_asset_image_data(self) -> dict[str, Any]:
        asset_id = self._single_line(request.args.get("id"), 80)
        if not asset_id:
            return self._error("缺少素材 id")
        try:
            asset = self._owned_reaction_asset_catalog().resolve(
                self._owned_reaction_asset_entries(),
                asset_id,
            )
            if asset is None:
                return self._error("素材不存在、未登记或校验失败")
            mime = mimetypes.guess_type(str(asset.path))[0] or ""
            if not mime.startswith("image/"):
                return self._error("素材文件类型不受支持")
            return self._ok(
                await self._encode_image_cache_file_data_url(
                    asset.path,
                    mime,
                    max_bytes=MAX_ASSET_BYTES,
                )
            )
        except Exception:
            logger.error("owned reaction asset preview failed")
            return self._exception_error("无法读取自有反应图预览")
    async def get_image_cache_preview(self) -> Any:
        try:
            resolved = await self._resolve_image_cache_preview_for_request()
            if self._is_http_error_response(resolved):
                return resolved
            _key, path = resolved
            response = await send_file(str(path))
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response
        except Exception as exc:
            logger.error(f"获取图片缓存预览失败: {exc}", exc_info=True)
            return self._exception_error(str(exc))
    async def get_image_cache_preview_data(self) -> dict[str, Any]:
        try:
            resolved = await self._resolve_image_cache_preview_for_request()
            if self._is_http_error_response(resolved):
                return resolved
            _key, path = resolved
            return self._ok(await self._encode_image_cache_file_data_url(path))
        except Exception as exc:
            logger.error(f"获取图片缓存预览数据失败: {exc}", exc_info=True)
            return self._exception_error(str(exc))
    async def get_image_cache_thumbnail_data(self) -> dict[str, Any]:
        try:
            resolved = await self._resolve_image_cache_preview_for_request()
            if self._is_http_error_response(resolved):
                return resolved
            key, source = resolved
            thumbnail = await self._get_or_create_image_cache_thumbnail(key, source)
            if thumbnail is not None:
                return self._ok(await self._encode_image_cache_file_data_url(thumbnail, "image/webp"))
            return self._ok(await self._encode_image_cache_file_data_url(source))
        except Exception as exc:
            logger.error(f"获取图片缓存缩略图失败: {exc}", exc_info=True)
            return self._exception_error(str(exc))
    async def delete_image_cache_item(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        key = self._single_line(payload.get("key"), 120)
        if not key:
            return self._error("缺少缓存 key")
        try:
            preview_path = ""
            async with self.plugin._data_lock:
                cache = self.plugin.data.get("private_image_vision_cache")
                if not isinstance(cache, dict) or key not in cache:
                    return self._error("缓存条目不存在")
                removed = cache.pop(key, None)
                if isinstance(removed, dict):
                    preview_path = self._single_line(removed.get("preview_path"), 260)
                self.plugin._save_data_sync(sections={"private_image_vision_cache"})
                remaining = len(cache)
            self._remove_image_cache_preview_file(preview_path, key)
            return self._ok({"key": key, "remaining": remaining})
        except Exception as exc:
            logger.error(f"删除图片缓存失败: {exc}", exc_info=True)
            return self._exception_error(str(exc))
    async def bulk_delete_image_cache_items(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        raw_keys = payload.get("keys")
        if not isinstance(raw_keys, list):
            return self._error("keys 必须是缓存 key 数组")
        if payload.get("confirm") is not True:
            return self._error("批量删除需要 confirm=true")
        keys: list[str] = []
        for value in raw_keys[:500]:
            key = self._single_line(value, 120)
            if key and key not in keys:
                keys.append(key)
        if not keys:
            return self._error("没有选择缓存条目")
        try:
            removed_items: list[tuple[str, str]] = []
            missing_keys: list[str] = []
            async with self.plugin._data_lock:
                cache = self.plugin.data.get("private_image_vision_cache")
                if not isinstance(cache, dict):
                    return self._error("图片缓存不存在")
                for key in keys:
                    if key not in cache:
                        missing_keys.append(key)
                        continue
                    removed = cache.pop(key, None)
                    removed_items.append(
                        (
                            key,
                            self._single_line(removed.get("preview_path"), 260)
                            if isinstance(removed, dict)
                            else "",
                        )
                    )
                if removed_items:
                    self.plugin._save_data_sync(sections={"private_image_vision_cache"})
                remaining = len(cache)
            for key, preview_path in removed_items:
                self._remove_image_cache_preview_file(preview_path, key)
            return self._ok(
                {
                    "removed": len(removed_items),
                    "removed_keys": [key for key, _path in removed_items],
                    "missing_keys": missing_keys,
                    "remaining": remaining,
                }
            )
        except Exception as exc:
            logger.error(f"批量删除图片缓存失败: {exc}", exc_info=True)
            return self._exception_error(str(exc))
    def _remove_image_cache_preview_file(self, preview_path: str, key: str = "") -> None:
        path: Path | None = None
        try:
            base = self._image_cache_preview_dir()
            if preview_path:
                path = Path(preview_path).resolve()
            if path is not None and path.is_file() and path.is_relative_to(base):
                path.unlink(missing_ok=True)
        except Exception:
            pass
        thumbnail = self._image_cache_thumbnail_file(key or (path.stem if path is not None else ""))
        if thumbnail is None:
            return
        try:
            if thumbnail.is_relative_to(self._image_cache_preview_dir()):
                thumbnail.unlink(missing_ok=True)
        except Exception:
            pass
    def _image_cache_item_summary(self, key: str, raw: dict[str, Any]) -> dict[str, Any]:
        text = str(raw.get("text") or "").strip()
        image_keys = [str(value).strip() for value in raw.get("image_keys", []) if str(value or "").strip()]
        image_aliases = [str(value).strip() for value in raw.get("image_aliases", []) if str(value or "").strip()]
        scope = self._single_line(raw.get("scope"), 40) or "private_image"
        created_ts = self._float(raw.get("created_ts"))
        last_hit_ts = self._float(raw.get("last_hit_ts"))
        edited_ts = self._float(raw.get("edited_ts"))
        preview_file = self._image_cache_preview_file(key, raw)
        preview_exists = preview_file is not None
        return {
            "key": self._single_line(key, 120),
            "text": self._single_line(text, 900 if scope == "forward_image" else 600),
            "provider_id": self._single_line(raw.get("provider_id"), 160),
            "scope": scope,
            "prompt_sig": self._single_line(raw.get("prompt_sig"), 32),
            "image_keys": image_keys[:8],
            "image_aliases": image_aliases[:12],
            "image_keys_text": " ".join(image_keys[:8]),
            "image_aliases_text": " ".join(image_aliases[:12]),
            "image_count": _safe_int(raw.get("image_count"), len(image_keys), 0),
            "preview_url": f"{self._page_asset_prefix()}/image_cache/preview?key={quote(key, safe='')}" if preview_exists else "",
            "preview_endpoint": f"/image_cache/preview_data?key={quote(key, safe='')}" if preview_exists else "",
            "thumbnail_endpoint": f"/image_cache/thumbnail_data?key={quote(key, safe='')}" if preview_exists else "",
            "preview_size": _safe_int(raw.get("preview_size"), 0, 0),
            "preview_width": _safe_int(raw.get("preview_width"), 0, 0),
            "preview_height": _safe_int(raw.get("preview_height"), 0, 0),
            "hits": _safe_int(raw.get("hits"), 0, 0),
            "created_ts": created_ts,
            "last_hit_ts": last_hit_ts,
            "edited_ts": edited_ts,
            "created": self.plugin._format_timestamp_elapsed(created_ts),
            "last_hit": self.plugin._format_timestamp_elapsed(last_hit_ts),
            "edited": self.plugin._format_timestamp_elapsed(edited_ts),
            "image_type": self._extract_labeled_text(text, "图片类型", 40),
            "visible": self._extract_labeled_text(text, "可见内容", 180),
            "intent": self._extract_labeled_text(text, "图像表达意图", 180),
            "ownership": self._extract_labeled_text(text, "图像归属判断", 80),
        }
    async def get_image_extension_status(self) -> dict[str, Any]:
        """Expose the split image runtime through the companion-owned page."""
        getter = getattr(self.plugin, "_image_companion_api", None)
        try:
            api = getter() if callable(getter) else None
        except Exception as exc:
            logger.warning(
                "生图扩展发现失败: %s",
                self._single_line(exc, 160),
                exc_info=True,
            )
            api = None
        if api is None:
            return self._ok(
                {
                    "installed": False,
                    "enabled": False,
                    "available": False,
                    "reason": "image_companion_unavailable",
                    "state": "unavailable",
                    "generation_count": 0,
                    "last_generation": {},
                    "unified_engine": {},
                    "metrics": {},
                }
            )
        status_getter = getattr(api, "status", None)
        if not callable(status_getter):
            return self._ok(
                {
                    "installed": True,
                    "enabled": False,
                    "available": False,
                    "reason": "status_api_unavailable",
                    "state": "unavailable",
                    "generation_count": 0,
                    "last_generation": {},
                    "unified_engine": {},
                    "metrics": {},
                }
            )
        try:
            value = status_getter()
        except Exception as exc:
            logger.warning(
                "获取生图扩展状态失败: %s",
                self._single_line(exc, 160),
                exc_info=True,
            )
            return self._exception_error("获取生图扩展状态失败")
        status = dict(value) if isinstance(value, dict) else {}
        status.setdefault("installed", True)
        status.setdefault("available", bool(status.get("enabled")))
        status.setdefault("state", "managed")
        image_contract = self._image_extension_contract_status(api)
        if image_contract:
            status["companion_contract"] = image_contract
            if not image_contract["available"]:
                status["available"] = False
                status["state"] = "incompatible"
                status["reason"] = image_contract["reason"] or "image_contract_incompatible"
        debug_summary = self._recent_photo_generation_debug(
            event_limit=240,
            summary_only=True,
        )
        status["photo_debug"] = debug_summary
        return self._ok(status)
    @staticmethod
    def _image_generation_result_used_reference(
        *,
        workflow_kind: str,
        image_path: str,
        image_exists: bool,
        note: Any,
    ) -> bool:
        if not image_path or not image_exists:
            return False
        if str(workflow_kind or "").strip().lower() not in {
            "selfie", "portrait", "自拍", "人像", "edit", "改图", "修图", "重绘", "p图"
        }:
            return False
        note_text = str(note or "")
        return bool(
            re.search(
                r"(?:已使用|已提交|成功提交|已带入)[^；。]{0,16}参考图|参考图[^；。]{0,16}(?:已使用|已提交|成功提交|已带入)",
                note_text,
                flags=re.I,
            )
            or "已使用本地人设参考图" in note_text
        )
    async def get_bookshelf_image(self):
        if not self._bookshelf_access_token_valid(self._bookshelf_request_token()):
            return self._error(self._bookshelf_access_error()["error"])
        resolved = await self._resolve_bookshelf_image_path_from_request()
        if isinstance(resolved, dict):
            return self._error(str(resolved.get("error") or "图片不存在"))
        path = resolved
        try:
            response = await send_file(path)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response
        except Exception as exc:
            logger.error(f"读取资料柜图片失败: {exc}", exc_info=True)
            return self._exception_error(str(exc))
    async def get_bookshelf_image_data(self) -> dict[str, Any]:
        if not self._bookshelf_access_token_valid(self._bookshelf_request_token()):
            return self._error(self._bookshelf_access_error()["error"])
        resolved = await self._resolve_bookshelf_image_path_from_request()
        if isinstance(resolved, dict):
            return self._error(str(resolved.get("error") or "图片不存在"))
        path = resolved
        try:
            mime = mimetypes.guess_type(str(path))[0] or "image/jpeg"
            data = await self._read_file_base64(path)
            return self._ok(
                {
                    "mime": mime,
                    "data_url": f"data:{mime};base64,{data}",
                    "size": path.stat().st_size,
                    "mtime": int(path.stat().st_mtime),
                }
            )
        except Exception as exc:
            logger.error(f"读取资料柜图片数据失败: {exc}", exc_info=True)
            return self._exception_error(str(exc))
    async def _resolve_bookshelf_image_path_from_request(self) -> Path | dict[str, str]:
        album_id = self._single_line(request.args.get("album_id"), 40)
        page_index = self._int(request.args.get("page"))
        cover_requested = str(request.args.get("cover") or "").lower() in {"1", "true", "yes"}
        if not album_id or (page_index < 1 and not cover_requested):
            return {"error": "缺少图片参数"}
        try:
            async with self.plugin._data_lock:
                data = deepcopy(self.plugin.data)
            archive_state = data.get("reading_archive_integration") if isinstance(data.get("reading_archive_integration"), dict) else {}
            deleted_ids = self._bookshelf_deleted_album_ids(archive_state)
            if album_id in deleted_ids:
                return {"error": "图片不存在"}
            shelf_items = data.get("bookshelf_items") if isinstance(data.get("bookshelf_items"), list) else []
            target = None
            for item in shelf_items:
                if not self._is_bookshelf_archive_item(item):
                    continue
                if self._bookshelf_album_id(item, limit=40) == album_id:
                    target = item
                    break
            if target is None:
                last_album = archive_state.get("last_album") if isinstance(archive_state.get("last_album"), dict) else {}
                if self._single_line(last_album.get("id") or last_album.get("album_id"), 40) == album_id:
                    target = last_album
            pages = target.get("pages") if isinstance(target, dict) and isinstance(target.get("pages"), list) else []
            data_root = Path(str(getattr(self.plugin, "data_dir", ""))).resolve()
            path: Path | None = None
            if cover_requested and isinstance(target, dict):
                cover_path = _path_text(target.get("cover_path"), 1000)
                if cover_path:
                    path = Path(cover_path).resolve()
            if path is None:
                page = next((item for item in pages if isinstance(item, dict) and self._int(item.get("index")) == page_index), None)
                if cover_requested and not isinstance(page, dict):
                    page = next((item for item in pages if isinstance(item, dict) and self._int(item.get("index")) > 0), None)
                if not isinstance(page, dict):
                    return {"error": "图片不存在"}
                path = Path(str(page.get("path") or "")).resolve()
            try:
                path.relative_to(data_root)
            except ValueError:
                return {"error": "图片路径不在资料柜目录内"}
            if not path.exists() or not path.is_file():
                return {"error": "图片文件不存在"}
            return path
        except Exception as exc:
            logger.error(f"读取资料柜图片失败: {exc}", exc_info=True)
            return {"error": str(exc)}
    def _persona_style_reference_text(self, questionnaire: dict[str, Any]) -> str:
        if not isinstance(questionnaire, dict):
            return ""
        raw = questionnaire.get("style_reference_text")
        if raw is None:
            raw = questionnaire.get("supplement_text")
        return self._multi_line_head_tail(raw, 4000)
    def _bookshelf_image_url(
        self,
        album_id: str,
        *,
        data_root: Path,
        page_index: int = 0,
        cover: bool = False,
        path_value: Any = "",
        access_token: str = "",
    ) -> str:
        if not album_id or (page_index < 1 and not cover):
            return ""
        url = f"{self._page_asset_prefix()}/bookshelf/image?album_id={quote(str(album_id), safe='')}"
        if cover:
            url += "&cover=1"
        elif page_index > 0:
            url += f"&page={page_index}"
        if access_token:
            url += f"&access_token={quote(str(access_token), safe='')}"
        raw_path = self._single_line(path_value, 500)
        if raw_path:
            try:
                path = Path(raw_path).resolve()
                path.relative_to(data_root)
                if path.exists() and path.is_file():
                    stat = path.stat()
                    url += f"&v={int(stat.st_mtime)}-{stat.st_size}"
            except Exception:
                pass
        return url
