# -*- coding: utf-8 -*-
"""生图诊断域。

由 tools/split_mixin_domain.py 从 page_api_media.py 机械抽取（27 个方法 + 0 个模块级名字 + 0 个类级赋值 / 1621 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApiMediaMixin）。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import secrets
import time
from .diagnostic_envelope import diagnostic_test_id
from .helpers import _path_text, _redact_outbound_secrets
from .page_api_shared import _page_api_host_request as request
from .page_backend import generation_log_candidates
from .photo_reference_catalog import CATALOG_VERSION, CatalogValidationError, PhotoReference, load_catalog
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

from .logging_util import get_module_logger

logger = get_module_logger(__name__)



class PrivateCompanionPageApiMediaDiagnosticsMixin:
    """生图诊断域（从 PrivateCompanionPageApiMediaMixin 拆出）。"""


    async def swap_image_api_settings(self) -> dict[str, Any]:
        try:
            payload = await request.get_json(silent=True) or {}
            force = self._normalize_bool_value(payload.get("force"))
            normalizer = getattr(self.plugin, "_normalize_external_image_api_endpoints", None)
            raw_endpoints = self._config_get_raw("external_image_api_endpoints", [])
            endpoints = normalizer(raw_endpoints) if callable(normalizer) else (raw_endpoints if isinstance(raw_endpoints, list) else [])
            if endpoints:
                if len(endpoints) < 2:
                    return self._error("在线生图 API 队列少于 2 条，无法交换优先级。")
                second = endpoints[1] if isinstance(endpoints[1], dict) else {}
                second_missing = [
                    label
                    for key, label in (
                        ("base_url", "第二条 API 地址"),
                        ("api_key", "第二条 API Key"),
                        ("model", "第二条图片模型"),
                    )
                    if not str(second.get(key) or "").strip()
                ]
                if (not second.get("enabled", True) or second_missing) and not force:
                    reason = "第二条 API 已关闭" if not second.get("enabled", True) else "、".join(second_missing)
                    return self._error(f"第二条在线图片 API 不可用，不能切换：{reason}")
                changed = list(endpoints)
                changed[0], changed[1] = changed[1], changed[0]
                async with self._image_api_runtime_lock():
                    self._apply_config_value("external_image_api_endpoints", changed)
                    config_saved = await self._save_config_if_possible()
                overview = await self.get_overview()
                if self._is_http_error_response(overview):
                    return overview
                if overview.get("success"):
                    data = overview.get("data") if isinstance(overview.get("data"), dict) else {}
                    data["changed"] = {"external_image_api_endpoints": changed}
                    data["config_saved"] = config_saved
                    data["message"] = "已交换在线图片 API 队列前两项。"
                    overview["data"] = data
                return overview
            pairs = (
                ("external_image_api_platform", "backup_external_image_api_platform"),
                ("EXTERNAL_IMAGE_API_BASE_URL", "BACKUP_EXTERNAL_IMAGE_API_BASE_URL"),
                ("EXTERNAL_IMAGE_API_KEY", "BACKUP_EXTERNAL_IMAGE_API_KEY"),
                ("EXTERNAL_IMAGE_API_MODEL", "BACKUP_EXTERNAL_IMAGE_API_MODEL"),
                ("external_image_api_size", "backup_external_image_api_size"),
                ("external_image_api_timeout_seconds", "backup_external_image_api_timeout_seconds"),
                ("external_image_api_custom_headers", "backup_external_image_api_custom_headers"),
            )

            current: dict[str, Any] = {}
            for primary_key, backup_key in pairs:
                current[primary_key] = self._normalize_setting_value(primary_key, self._config_get(primary_key))
                current[backup_key] = self._normalize_setting_value(backup_key, self._config_get(backup_key))

            backup_required = (
                "BACKUP_EXTERNAL_IMAGE_API_BASE_URL",
                "BACKUP_EXTERNAL_IMAGE_API_KEY",
                "BACKUP_EXTERNAL_IMAGE_API_MODEL",
            )
            missing_backup = [key for key in backup_required if not str(current.get(key) or "").strip()]
            if missing_backup and not force:
                labels = {
                    "BACKUP_EXTERNAL_IMAGE_API_BASE_URL": "备选在线 API 地址",
                    "BACKUP_EXTERNAL_IMAGE_API_KEY": "备选在线 API Key",
                    "BACKUP_EXTERNAL_IMAGE_API_MODEL": "备选在线图片模型",
                }
                return self._error("备选在线图片 API 未配置完整，不能切换：" + "、".join(labels.get(key, key) for key in missing_backup))

            changed: dict[str, Any] = {}
            for primary_key, backup_key in pairs:
                changed[primary_key] = current.get(backup_key)
                changed[backup_key] = current.get(primary_key)

            old_primary_complete = bool(
                str(current.get("EXTERNAL_IMAGE_API_BASE_URL") or "").strip()
                and str(current.get("EXTERNAL_IMAGE_API_KEY") or "").strip()
                and str(current.get("EXTERNAL_IMAGE_API_MODEL") or "").strip()
            )
            changed["enable_backup_external_image_api"] = old_primary_complete

            async with self._image_api_runtime_lock():
                for key, value in changed.items():
                    self._apply_config_value(key, value, changed)
                self._sync_photo_generation_runtime_config()
                config_saved = await self._save_config_if_possible()
            overview = await self.get_overview()
            if self._is_http_error_response(overview):
                return overview
            if overview.get("success"):
                data = overview.get("data") if isinstance(overview.get("data"), dict) else {}
                data["changed"] = changed
                data["config_saved"] = config_saved
                data["message"] = "已交换主在线图片 API 与备选在线图片 API。"
                overview["data"] = data
            return overview
        except Exception as exc:
            logger.error(f"切换在线生图 API 失败: {exc}", exc_info=True)
            return self._exception_error(str(exc))

    def _image_api_endpoint_test_key(self, endpoint: dict[str, Any]) -> str:
        custom_headers = str(endpoint.get("custom_headers") or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        identity = json.dumps(
            [
                bool(endpoint.get("enabled", True)),
                str(endpoint.get("platform") or "auto").strip().lower(),
                str(endpoint.get("base_url") or "").strip().rstrip("/"),
                str(endpoint.get("model") or "").strip(),
                str(endpoint.get("api_key") or "").strip(),
                str(endpoint.get("size") or "1024x1024").strip().lower(),
                str(endpoint.get("ratio") or "").strip().lower(),
                self._int(endpoint.get("timeout_seconds"), 180, 20, 600),
                custom_headers,
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return f"image_api_endpoint_{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:12]}"

    def _troubleshooting_safe_image_api_url(self, value: Any) -> str:
        raw = str(value or "").strip()
        if not raw:
            return ""
        try:
            parsed = urlparse(raw)
            if not parsed.scheme or not parsed.hostname:
                return self._single_line(raw.split("?", 1)[0].split("#", 1)[0], 180)
            host = parsed.hostname
            if ":" in host and not host.startswith("["):
                host = f"[{host}]"
            try:
                port = f":{parsed.port}" if parsed.port else ""
            except ValueError:
                port = ""
            return self._single_line(f"{parsed.scheme}://{host}{port}{parsed.path or ''}", 180)
        except Exception:
            return self._single_line(raw.split("?", 1)[0].split("#", 1)[0], 180)

    def _redact_image_api_test_text(self, value: Any, endpoint: Any = None, limit: int = 220) -> str:
        cleaned = _redact_outbound_secrets(value, self.plugin)
        endpoint_data = endpoint if isinstance(endpoint, dict) else {}
        secrets_to_hide = [str(endpoint_data.get("api_key") or "").strip()]
        custom_headers = str(endpoint_data.get("custom_headers") or "")
        for line in custom_headers.splitlines():
            _, separator, raw_value = line.partition(":")
            if separator:
                secrets_to_hide.append(raw_value.strip())
        for secret in secrets_to_hide:
            if len(secret) >= 4:
                cleaned = cleaned.replace(secret, "[密钥已隐藏]")
        return self._single_line(cleaned, limit)

    def _image_api_test_artifact_path(self, value: Any) -> Path | None:
        raw = str(value or "").strip()
        data_dir = str(getattr(self.plugin, "data_dir", "") or "").strip()
        if not raw or not data_dir:
            return None
        try:
            path = Path(raw).resolve(strict=False)
            root = (Path(data_dir) / "generated_photos").resolve(strict=False)
            path.relative_to(root)
        except (OSError, ValueError):
            return None
        if not path.name.startswith("private_companion_troubleshooting_"):
            return None
        if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            return None
        return path

    async def _cleanup_image_api_test_artifact(self, value: Any) -> bool:
        path = self._image_api_test_artifact_path(value)
        if path is None or not path.exists():
            return False
        try:
            await asyncio.to_thread(path.unlink)
            return True
        except OSError as exc:
            logger.warning(
                "清理生图 API 测试图片失败: path=%s error=%s",
                self._single_line(path, 180),
                self._single_line(exc, 160),
            )
            return False

    async def _prune_stale_image_api_test_artifacts(self, max_age_seconds: int = 3600) -> int:
        data_dir = str(getattr(self.plugin, "data_dir", "") or "").strip()
        if not data_dir:
            return 0
        root = Path(data_dir) / "generated_photos"
        cutoff = time.time() - max(300, int(max_age_seconds))

        def prune() -> int:
            if not root.exists():
                return 0
            removed = 0
            for path in root.glob("private_companion_troubleshooting_*"):
                if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
                    continue
                try:
                    if path.is_file() and path.stat().st_mtime < cutoff:
                        path.unlink()
                        removed += 1
                except OSError:
                    continue
            return removed

        return await asyncio.to_thread(prune)

    @staticmethod
    def _image_api_endpoint_configuration_note(endpoint: dict[str, Any]) -> str:
        if not bool(endpoint.get("enabled", True)):
            return "该端点已停用"
        missing: list[str] = []
        if not str(endpoint.get("base_url") or "").strip():
            missing.append("API 地址")
        if not str(endpoint.get("api_key") or "").strip():
            missing.append("API Key")
        if not str(endpoint.get("model") or "").strip():
            missing.append("图片模型")
        return f"缺少{'、'.join(missing)}" if missing else ""

    def _troubleshooting_image_api_endpoints(self) -> list[dict[str, Any]]:
        getter = getattr(self.plugin, "_external_image_api_endpoint_queue", None)
        if not callable(getter):
            return []
        try:
            endpoints = getter(include_incomplete=True, include_disabled=True)
        except Exception:
            return []
        items: list[dict[str, Any]] = []
        for index, endpoint in enumerate(endpoints[:12] if isinstance(endpoints, list) else []):
            if isinstance(endpoint, dict):
                items.append(self._troubleshooting_image_api_endpoint_summary(endpoint, index))
        return items

    def _troubleshooting_image_api_endpoint_summary(self, endpoint: dict[str, Any], index: int) -> dict[str, Any]:
        platform_labels = {
            "auto": "自动识别",
            "openai": "OpenAI 兼容",
            "openrouter": "OpenRouter",
            "agnes": "Agnes Image",
            "sensenova": "SenseNova 日日新",
            "bailian": "阿里云百炼",
            "modelscope": "魔搭社区",
            "doubao": "豆包/火山方舟",
            "gemini": "Gemini",
            "minimax": "MiniMax",
        }
        note = self._image_api_endpoint_configuration_note(endpoint)
        platform = self._single_line(endpoint.get("platform"), 30).lower() or "auto"
        enabled = bool(endpoint.get("enabled", True))
        return {
            "index": index,
            "test_key": self._image_api_endpoint_test_key(endpoint),
            "name": self._single_line(endpoint.get("name"), 80) or f"在线 API {index + 1}",
            "enabled": enabled,
            "ready": not note,
            "status": "disabled" if not enabled else ("incomplete" if note else "ready"),
            "status_text": note or "配置完整，尚未单独测试",
            "platform": platform,
            "platform_label": platform_labels.get(platform, platform or "自动识别"),
            "base_url": self._troubleshooting_safe_image_api_url(endpoint.get("base_url")),
            "model": self._single_line(endpoint.get("model"), 100),
            "size": self._single_line(endpoint.get("size"), 40) or "1024x1024",
            "ratio": self._single_line(endpoint.get("ratio"), 20),
            "timeout_seconds": self._int(endpoint.get("timeout_seconds"), 180, 20, 600),
        }

    def _legacy_recent_photo_generation_debug(self, *, event_limit: int = 240) -> dict[str, Any]:
        """读取生图 trace 的最近事件，供生图父面板按需展开查看。

        文件内容已经由生图运行时统一按 JSONL 写入并执行默认脱敏。页面只返回最近
        的一段事件，避免把历史日志或无界请求体一次性送到浏览器。
        """
        data_dir = str(getattr(self.plugin, "data_dir", "") or "").strip()
        root = Path(data_dir) if data_dir else None
        if root is None:
            return {
                "enabled": False,
                "available": False,
                "path": "",
                "latest": {},
                "traces": [],
                "events": [],
            }
        candidates = generation_log_candidates(root)
        paths = [item for item in candidates if item.is_file()]
        if not paths:
            return {
                "enabled": False,
                "available": False,
                "path": str(root / "photo_generation_trace.txt"),
                "latest": {},
                "traces": [],
                "events": [],
            }
        events: list[dict[str, Any]] = []
        try:
            per_file_limit = max(1, min(1000, int(event_limit) * 4))
            for path in paths:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
                for line in lines[-per_file_limit:]:
                    try:
                        value = json.loads(line)
                    except (TypeError, ValueError, json.JSONDecodeError):
                        continue
                    if isinstance(value, dict) and (value.get("trace") or value.get("trace_id")):
                        if not value.get("trace"):
                            value["trace"] = value.get("trace_id")
                        value["source_file"] = path.name
                        events.append(value)
        except (OSError, UnicodeError):
            return {
                "enabled": True,
                "available": False,
                "path": str(paths[0]),
                "latest": {},
                "traces": [],
                "events": [],
            }
        events.sort(key=lambda item: (
            self._photo_debug_event_timestamp(item),
            self._photo_debug_event_sequence(item),
        ))
        events = events[-max(1, min(240, int(event_limit))):]
        trace_rows: dict[str, dict[str, Any]] = {}
        for event in events:
            trace_id = self._single_line(event.get("trace"), 80)
            if not trace_id:
                continue
            row = trace_rows.pop(trace_id, None)
            if row is None:
                row = {
                    "trace": trace_id,
                    "started_at": event.get("time") or event.get("ts"),
                    "last_at": event.get("time") or event.get("ts"),
                    "stage": "",
                    "status": "",
                    "event_count": 0,
                }
            row["last_at"] = event.get("time") or event.get("ts")
            row["stage"] = self._single_line(event.get("stage"), 80)
            row["status"] = self._single_line(event.get("status"), 30)
            row["event_count"] = self._int(row.get("event_count"), 0) + 1
            trace_rows[trace_id] = row
        traces = list(trace_rows.values())[-24:]
        latest_trace = events[-1].get("trace") if events else ""
        latest = next((row for row in reversed(events) if row.get("trace") == latest_trace), {}) if latest_trace else {}
        return {
            "enabled": True,
            "available": bool(events),
            "path": str(paths[0]),
            "sources": [str(item) for item in paths],
            "latest": latest,
            "traces": traces,
            "events": events,
        }

    def _image_debug_data_roots(self) -> list[Path]:
        """Resolve the image extension's actual debug roots for this owner."""
        roots: list[str] = []
        getter = getattr(self.plugin, "_image_companion_api", None)
        try:
            api = getter() if callable(getter) else None
            resolver = getattr(api, "debug_data_dirs", None)
            if callable(resolver):
                values = resolver(self.plugin)
                if isinstance(values, (list, tuple)):
                    roots.extend(str(item or "").strip() for item in values)
        except Exception:
            pass
        fallback = str(getattr(self.plugin, "data_dir", "") or "").strip()
        if fallback:
            roots.append(fallback)
        result: list[Path] = []
        seen: set[str] = set()
        for raw in roots:
            if not raw:
                continue
            try:
                root = Path(raw).expanduser().resolve(strict=False)
            except (OSError, RuntimeError, ValueError):
                continue
            key = str(root).casefold()
            if key not in seen:
                seen.add(key)
                result.append(root)
        return result

    @staticmethod
    def _photo_debug_event_timestamp(value: Mapping[str, Any]) -> float:
        try:
            timestamp = float(value.get("ts") or 0)
        except (TypeError, ValueError, OverflowError):
            return 0.0
        return timestamp if math.isfinite(timestamp) and timestamp > 0 else 0.0

    @staticmethod
    def _photo_debug_event_sequence(value: Mapping[str, Any]) -> int:
        """Parse an event sequence without letting malformed logs abort reads."""
        try:
            raw = value.get("seq")
            if raw in (None, ""):
                return 0
            return int(raw)
        except (TypeError, ValueError, OverflowError):
            return 0

    @staticmethod
    def _photo_debug_event_fingerprint(value: Mapping[str, Any]) -> str:
        """Return the source-independent portion of a generation event.

        Legacy TXT and unified JSONL records deliberately differ in envelope
        fields such as sequence, timestamp and severity. The bridge writes
        both records for one operation, so use only the shared semantic fields
        to find those pairs. Optional route metadata remains included when it
        is present, preventing unrelated engine events from being coalesced.
        """
        fingerprint: dict[str, Any] = {
            "stage": str(value.get("stage") or ""),
            "status": str(value.get("status") or ""),
            "context": value.get("context"),
            "data": value.get("data"),
        }
        for key in (
            "operation",
            "workflow",
            "backend",
            "route",
            "attempt",
            "error_code",
            "failure_stage",
        ):
            item = value.get(key)
            if item not in (None, ""):
                fingerprint[key] = item
        return hashlib.sha256(
            json.dumps(
                fingerprint,
                ensure_ascii=False,
                sort_keys=True,
                default=str,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

    def _recent_photo_generation_debug(
        self,
        *,
        event_limit: int = 240,
        trace_id: str = "",
        summary_only: bool = False,
    ) -> dict[str, Any]:
        """Read generation events from the resolved data roots.

        The collapsed status endpoint reads only file tails and returns a
        summary. Full events are read only after the user opens the detail.
        """
        roots = self._image_debug_data_roots()
        if not roots:
            return {"enabled": False, "available": False, "path": "", "latest": {}, "traces": [], "events": []}
        candidates: list[tuple[Path, str, int]] = []
        for root in roots:
            paths = generation_log_candidates(root)
            for path in paths:
                try:
                    resolved = path.resolve(strict=True)
                    if path.is_symlink() or not resolved.is_file():
                        continue
                    if root not in resolved.parents:
                        continue
                    logical = str(resolved.relative_to(root)).replace("\\", "/")
                    candidates.append((resolved, logical, 2 if logical.startswith("photo_debug/generation") else 1))
                except (OSError, RuntimeError, ValueError):
                    continue
        unique: dict[str, tuple[Path, str, int]] = {
            str(path).casefold(): (path, logical, rank)
            for path, logical, rank in candidates
        }
        paths = list(unique.values())
        if not paths:
            return {"enabled": False, "available": False, "path": "", "latest": {}, "traces": [], "events": []}
        requested_trace = self._single_line(trace_id, 80)
        # Preserve same-source repetitions: retries and fallbacks can visit
        # the same stage more than once. Only combine the paired records that
        # the migration bridge writes to the legacy and unified log formats.
        records: list[tuple[int, dict[str, Any]]] = []
        records_by_event_id: dict[tuple[str, str], int] = {}
        records_by_fingerprint: dict[tuple[str, str], list[int]] = {}
        try:
            for path, logical, rank in paths:
                for line in self._read_debug_lines(path, tail_lines=256 if summary_only else None):
                    try:
                        value = json.loads(line)
                    except (TypeError, ValueError, json.JSONDecodeError):
                        continue
                    if not isinstance(value, dict):
                        continue
                    trace = self._single_line(value.get("trace") or value.get("trace_id"), 80)
                    if not trace or (requested_trace and trace != requested_trace):
                        continue
                    value["trace"] = trace
                    value["source_file"] = logical
                    event_id = self._single_line(value.get("event_id"), 120)
                    seq = self._photo_debug_event_sequence(value)
                    if event_id:
                        event_key = (trace, event_id)
                        existing_index = records_by_event_id.get(event_key)
                        if existing_index is not None:
                            existing_rank, _existing = records[existing_index]
                            if rank >= existing_rank:
                                records[existing_index] = (rank, value)
                            continue

                    fingerprint = self._photo_debug_event_fingerprint(value)
                    pair_key = (trace, fingerprint)
                    timestamp = self._photo_debug_event_timestamp(value)
                    bridge_index: int | None = None
                    bridge_distance: tuple[int, float] | None = None
                    for candidate_index in records_by_fingerprint.get(pair_key, []):
                        candidate_rank, candidate = records[candidate_index]
                        if candidate_rank == rank:
                            continue
                        candidate_seq = self._photo_debug_event_sequence(candidate)
                        candidate_timestamp = self._photo_debug_event_timestamp(candidate)
                        # A bridge normally keeps the sequence aligned. A
                        # short timestamp window covers migration paths with
                        # independent counters while preserving older repeats.
                        same_seq = bool(seq and candidate_seq and seq == candidate_seq)
                        close_in_time = bool(
                            timestamp
                            and candidate_timestamp
                            and abs(timestamp - candidate_timestamp) <= 1.0
                        )
                        if not same_seq and not close_in_time:
                            continue
                        distance = (0 if same_seq else 1, abs(timestamp - candidate_timestamp))
                        if bridge_distance is None or distance < bridge_distance:
                            bridge_index = candidate_index
                            bridge_distance = distance
                    if bridge_index is not None:
                        existing_rank, _existing = records[bridge_index]
                        if rank >= existing_rank:
                            records[bridge_index] = (rank, value)
                        if event_id:
                            records_by_event_id[(trace, event_id)] = bridge_index
                        continue

                    record_index = len(records)
                    records.append((rank, value))
                    if event_id:
                        records_by_event_id[(trace, event_id)] = record_index
                    records_by_fingerprint.setdefault(pair_key, []).append(record_index)
        except (OSError, UnicodeError):
            return {"enabled": True, "available": False, "path": paths[0][1], "latest": {}, "traces": [], "events": []}
        # New JSONL events win when the same legacy event was dual-written,
        # while older unique legacy events remain available for a complete
        # trace across a migration boundary.
        events = [value for _rank, value in records]
        events.sort(key=lambda item: (
            self._photo_debug_event_timestamp(item),
            self._photo_debug_event_sequence(item),
        ))
        limit = max(1, min(1000, int(event_limit)))
        events = events[-limit:]
        trace_rows: dict[str, dict[str, Any]] = {}
        for event in events:
            trace = self._single_line(event.get("trace"), 80)
            row = trace_rows.pop(trace, None)
            if row is None:
                row = {
                    "trace": trace,
                    "started_at": event.get("time") or event.get("ts"),
                    "last_at": event.get("time") or event.get("ts"),
                    "stage": "",
                    "status": "",
                    "event_count": 0,
                }
            row["last_at"] = event.get("time") or event.get("ts")
            row["stage"] = self._single_line(event.get("stage"), 80)
            row["status"] = self._single_line(event.get("status"), 30)
            row["event_count"] = self._int(row.get("event_count"), 0) + 1
            # Reinsert on every event so insertion order reflects each Trace's
            # most recent event rather than its first appearance.
            trace_rows[trace] = row
        traces = list(trace_rows.values())[-24:]
        latest = events[-1] if events else {}
        if summary_only:
            latest = {
                key: latest.get(key)
                for key in (
                    "trace", "trace_id", "request_id", "seq", "time", "ts", "elapsed_ms",
                    "stage", "status", "severity", "backend", "workflow", "route", "attempt",
                    "error_code", "failure_stage",
                )
                if key in latest
            }
        return {
            "enabled": True,
            "available": bool(events),
            "path": paths[0][1],
            "sources": [logical for _path, logical, _rank in paths],
            "latest": latest,
            "traces": traces,
            "events": [] if summary_only else events,
        }

    async def get_image_debug(self) -> dict[str, Any]:
        """Return full recent image debug events only when the panel expands them."""
        try:
            limit = self._int(request.args.get("limit"), 240, 1, 1000)
            trace_id = self._single_line(request.args.get("trace"), 80)
            payload = self._recent_photo_generation_debug(
                event_limit=limit,
                trace_id=trace_id,
            )
            if trace_id:
                summary = self._recent_photo_generation_debug(
                    event_limit=240,
                    summary_only=True,
                )
                # Keep the recent trace chooser intact after loading one
                # selected trace; only the event body is trace-scoped.
                payload["traces"] = summary.get("traces", [])
                payload["latest"] = payload["events"][-1] if payload["events"] else {}
            if isinstance(payload.get("events"), list):
                self._attach_debug_payload_contents(
                    payload["events"],
                    self._image_debug_data_roots(),
                )
            payload["requested_trace"] = trace_id
            return self._ok(payload)
        except Exception as exc:
            logger.warning(
                "读取生图 debug 失败: %s",
                self._single_line(exc, 180),
                exc_info=True,
            )
            return self._exception_error("读取生图 debug 失败")

    async def get_image_api_status(self) -> dict[str, Any]:
        try:
            async with self.plugin._data_lock:
                raw_results = deepcopy(self.plugin.data.get("troubleshooting_test_results", {}))
            stored = raw_results if isinstance(raw_results, dict) else {}
            items = self._troubleshooting_image_api_endpoints()
            for item in items:
                raw_result = stored.get(str(item.get("test_key") or ""))
                item["result"] = self._sanitize_troubleshooting_test_result(raw_result) if isinstance(raw_result, dict) else {}
            return self._ok(
                {
                    "items": items,
                    "backend": self._single_line(getattr(self.plugin, "photo_generation_backend", "auto"), 30) or "auto",
                }
            )
        except Exception as exc:
            logger.warning("获取生图 API 状态失败: %s", self._single_line(exc, 160), exc_info=True)
            return self._exception_error(str(exc))

    async def test_image_api_endpoint(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        request_id = secrets.token_hex(6)
        started = time.time()
        logger.info("[test:%s][type:image_api_endpoint] 开始执行测试", request_id)
        try:
            result = await self._run_image_api_endpoint_test(payload)
        except Exception as exc:
            endpoint = payload.get("endpoint") if isinstance(payload.get("endpoint"), dict) else {}
            safe_error = self._redact_image_api_test_text(exc, endpoint, 220)
            logger.warning("生图 API 单独测试失败: %s", safe_error)
            result = {
                "ok": False,
                "title": "在线图片 API 单独测试",
                "error": safe_error,
                "exception_type": exc.__class__.__name__,
            }
        result["type"] = "image_api_endpoint"
        result["elapsed_ms"] = self._int(result.get("elapsed_ms")) or int((time.time() - started) * 1000)
        result["ran_at"] = time.time()
        result["ran_at_text"] = self.plugin._format_timestamp_elapsed(result["ran_at"])
        result.setdefault("request_id", request_id)
        result = self._finalize_test_diagnostics(
            "image_api_endpoint",
            result,
            started,
            title="在线图片 API 单独测试",
            finished_at=result["ran_at"],
        )
        result = self._diagnostic_envelope(
            result,
            test_type="image_api_endpoint",
            duration_ms=self._int(result.get("elapsed_ms")),
            test_id=diagnostic_test_id("image_api_endpoint"),
        )
        result_key = self._single_line(result.get("test_key"), 80) or "image_api_endpoint"
        await self._remember_troubleshooting_test_result(result_key, result)
        logger.info(
            "[test:%s][type:image_api_endpoint] 测试结束: status=%s elapsed_ms=%s",
            result.get("request_id"),
            result.get("test_status"),
            result.get("elapsed_ms"),
        )
        return self._ok(result)

    async def _run_image_api_endpoint_test(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            endpoint_index = int(payload.get("endpoint_index"))
        except (TypeError, ValueError):
            endpoint_index = -1
        submitted_endpoint = payload.get("endpoint")
        if isinstance(submitted_endpoint, dict):
            if endpoint_index < 0:
                endpoint_index = 0
            normalizer = getattr(self.plugin, "_normalize_external_image_api_endpoint", None)
            endpoint = normalizer(submitted_endpoint, index=endpoint_index) if callable(normalizer) else dict(submitted_endpoint)
            if not isinstance(endpoint, dict) or not endpoint:
                return {"ok": False, "title": "在线图片 API 单独测试", "error": "提交的生图 API 配置无效"}
            summary = self._troubleshooting_image_api_endpoint_summary(endpoint, endpoint_index)
        else:
            summaries = self._troubleshooting_image_api_endpoints()
            getter = getattr(self.plugin, "_external_image_api_endpoint_queue", None)
            try:
                endpoints = getter(include_incomplete=True, include_disabled=True) if callable(getter) else []
            except Exception:
                endpoints = []
            if endpoint_index < 0 or endpoint_index >= len(endpoints) or endpoint_index >= len(summaries):
                return {
                    "ok": False,
                    "title": "在线图片 API 单独测试",
                    "error": "指定的在线图片 API 不存在或配置队列已变化，请刷新后重试",
                }
            endpoint = endpoints[endpoint_index]
            summary = summaries[endpoint_index]
        base_result = {
            "test_key": summary["test_key"],
            "title": f"{summary['name']} 单独测试",
            "backend": "在线图片 API（单独）",
            "endpoint_index": endpoint_index,
            "endpoint_name": summary["name"],
            "endpoint_platform": summary["platform_label"],
            "endpoint_url": summary["base_url"],
            "endpoint_status": summary["status"],
            "image_model": summary["model"],
            "image_size": summary["size"],
            "endpoint_timeout_seconds": summary["timeout_seconds"],
            "backend_preference": "external_endpoint",
            "warnings": [
                "本次是纯文单端点验证：只调用所选在线 API，不会尝试队列中的其他 API，也不会回退到 ComfyUI 或 SDGen。",
                "不会上传参考图，也不覆盖自拍、改图、角色一致性或长提示词的真实调用；这些场景请在排障页运行“测试自拍”。",
            ],
        }
        configuration_note = self._image_api_endpoint_configuration_note(endpoint)
        if configuration_note:
            return {
                **base_result,
                "ok": False,
                "detail": configuration_note,
                "error": configuration_note,
            }
        runner = getattr(self.plugin, "_image_companion_test_endpoint", None)
        if not callable(runner):
            return {
                **base_result,
                "ok": False,
                "error": "插件缺少单端点在线生图入口",
            }

        prompt_text = self._single_line(payload.get("prompt"), 600) or (
            "A small green check mark sticker on a clean white desk beside a warm table lamp, "
            "clear composition, realistic photo, no people, no text, no watermark"
        )
        endpoint_timeout = self._int(summary.get("timeout_seconds"), 180, 20, 600)
        test_timeout = min(900, max(45, endpoint_timeout * 2 + 30))
        queue_timeout = min(900, max(60, test_timeout))
        started = time.time()
        lock = self._image_api_runtime_lock()
        wait_started = time.monotonic()
        lock_acquired = False
        try:
            try:
                await asyncio.wait_for(lock.acquire(), timeout=queue_timeout)
                lock_acquired = True
            except asyncio.TimeoutError:
                queue_wait_ms = int((time.monotonic() - wait_started) * 1000)
                elapsed_ms = int((time.time() - started) * 1000)
                return {
                    **base_result,
                    "ok": False,
                    "prompt": prompt_text,
                    "timeout_seconds": test_timeout,
                    "queue_wait_ms": queue_wait_ms,
                    "elapsed_ms": elapsed_ms,
                    "detail": f"等待其他生图任务释放队列超过 {queue_timeout}s，所选接口尚未开始调用",
                    "error": f"生图测试排队超时（{queue_timeout}s）",
                }

            queue_wait_ms = int((time.monotonic() - wait_started) * 1000)
            try:
                external_result = await asyncio.wait_for(
                    runner(endpoint, prompt_text),
                    timeout=test_timeout,
                )
                external_result = external_result if isinstance(external_result, dict) else {}
                image_path = _path_text(
                    external_result.get("image_path"),
                    1000,
                )
                note = self._single_line(
                    external_result.get("message") or external_result.get("detail"),
                    500,
                )
            except asyncio.TimeoutError:
                elapsed_ms = int((time.time() - started) * 1000)
                return {
                    **base_result,
                    "ok": False,
                    "prompt": prompt_text,
                    "timeout_seconds": test_timeout,
                    "queue_wait_ms": queue_wait_ms,
                    "elapsed_ms": elapsed_ms,
                    "detail": f"接口开始调用后 {test_timeout}s 内仍未完成",
                    "error": f"单端点接口测试超时（{test_timeout}s）",
                }
            except Exception as exc:
                elapsed_ms = int((time.time() - started) * 1000)
                safe_error = self._redact_image_api_test_text(exc, endpoint, 220)
                logger.warning(
                    "在线图片 API 单端点测试失败: endpoint=%s error=%s",
                    self._single_line(summary["name"], 80),
                    safe_error,
                )
                return {
                    **base_result,
                    "ok": False,
                    "prompt": prompt_text,
                    "timeout_seconds": test_timeout,
                    "queue_wait_ms": queue_wait_ms,
                    "elapsed_ms": elapsed_ms,
                    "error": safe_error or "单端点调用失败",
                }
        finally:
            if lock_acquired:
                lock.release()

        elapsed_ms = int((time.time() - started) * 1000)
        if external_result.get("unsupported"):
            detail = self._redact_image_api_test_text(
                external_result.get("detail") or note,
                endpoint,
                1200,
            )
            return {
                **base_result,
                "ok": False,
                "unsupported": True,
                "test_status": "unsupported",
                "code": self._single_line(
                    external_result.get("code"),
                    80,
                ) or "image_current_contract_endpoint_test_unsupported",
                "error": detail or "新版 Image 扩展不支持旧式单端点测试",
                "detail": detail or "当前配置未被判定为错误，旧式单端点测试未执行",
                "next_step": self._single_line(
                    external_result.get("next_step"),
                    600,
                ) or "到排障页运行完整图片生成链路测试。",
                "prompt": prompt_text,
                "timeout_seconds": test_timeout,
                "queue_wait_ms": queue_wait_ms,
                "elapsed_ms": elapsed_ms,
            }
        exists = False
        file_size = 0
        if image_path:
            try:
                image_file = Path(str(image_path))
                exists = image_file.exists()
                file_size = image_file.stat().st_size if exists else 0
            except Exception:
                exists = False
        ok = bool(image_path and exists)
        safe_note = self._redact_image_api_test_text(note, endpoint, 220)
        artifact_cleaned = await self._cleanup_image_api_test_artifact(image_path)
        await self._prune_stale_image_api_test_artifacts()
        return {
            **base_result,
            "ok": ok,
            "path": "" if artifact_cleaned else _path_text(image_path, 1000),
            "file_size": file_size,
            "detail": safe_note or ("已生成图片" if ok else "接口未返回有效图片文件"),
            "prompt": prompt_text,
            "timeout_seconds": test_timeout,
            "queue_wait_ms": queue_wait_ms,
            "elapsed_ms": elapsed_ms,
            "error": "" if ok else (safe_note or "接口未返回有效图片文件"),
        }

    async def _run_image_generation_chain_test(self, payload: dict[str, Any]) -> dict[str, Any]:
        async with self._image_api_runtime_lock():
            self._sync_photo_generation_runtime_config()
        called_plugin = self._image_generation_called_plugin_diagnostics()
        structured_generator = getattr(self.plugin, "_generate_photo_image_result", None)
        legacy_generator = getattr(self.plugin, "_generate_photo_image", None)
        generator = structured_generator if callable(structured_generator) else legacy_generator
        if not callable(generator):
            return {
                "ok": False,
                "title": "图片生成链路测试",
                "error": "插件缺少图片生成入口 _generate_photo_image",
                **called_plugin,
            }
        reference_enabled = bool(getattr(self.plugin, "enable_photo_reference_image", False))
        reference_getter = getattr(self.plugin, "_photo_persona_reference_image_path", None)
        reference_image_path = ""
        if reference_enabled and callable(reference_getter):
            try:
                reference_image_path = _path_text(reference_getter(), 1000)
            except Exception:
                reference_image_path = ""
        persona_reference = next(
            (
                item
                for item in (getattr(self.plugin, "photo_reference_catalog", ()) or ())
                if isinstance(item, PhotoReference) and item.kind == "persona"
            ),
            None,
        )
        configured_reference = _path_text(persona_reference.source if persona_reference is not None else "", 1000)
        has_reference_source = bool(reference_enabled and (reference_image_path or re.match(r"^https?://", configured_reference, flags=re.I)))
        workflow_kind = self._single_line(payload.get("workflow_kind"), 20)
        if not workflow_kind:
            workflow_kind = "selfie" if has_reference_source else "text2img"
        if workflow_kind in {"selfie", "portrait", "自拍", "人像"}:
            async_reference_getter = getattr(self.plugin, "_photo_persona_reference_image_for_kind_async", None)
            if callable(async_reference_getter):
                try:
                    reference_image_path = _path_text(
                        await async_reference_getter(workflow_kind, allow_daily_outfit=True),
                        1000,
                    )
                except Exception as exc:
                    logger.info(
                        "自拍排障参考图解析失败: %s",
                        self._single_line(exc, 160),
                    )
        prompt_text = self._single_line(payload.get("prompt"), 600)
        if not prompt_text and workflow_kind in {"selfie", "portrait", "自拍", "人像"}:
            if reference_image_path:
                prompt_text = (
                    "排障测试自拍图，保持参考图中的人物身份和外观一致，手机随手自拍构图，"
                    "自然室内光，画面干净清晰，真实摄影风格，不包含文字水印"
                )
            else:
                prompt_text = (
                    "排障测试自拍图，一名角色面向镜头，手机随手自拍构图，"
                    "自然室内光，画面干净清晰，真实摄影风格，不包含文字水印"
                )
        if not prompt_text:
            role_appearance = self._troubleshooting_role_appearance_prompt()
            if role_appearance:
                prompt_text = (
                    f"画面主体是这个角色：{role_appearance}。"
                    "角色面向镜头，手举一个简洁的小牌子，牌子上只有一个绿色对钩符号，"
                    "室内日常背景，画面干净清晰，构图自然，真实摄影或精致插画质感；"
                    "不要出现其他文字、水印、额外人物或变形手。"
                )
            else:
                prompt_text = (
                    "排障测试图，一枚小小的绿色对勾贴纸放在白色桌面上，旁边有柔和台灯光，"
                    "画面干净清晰，真实摄影风格，不包含人物、不包含文字水印"
                )
        started = time.time()
        diagnostics = self._image_generation_timeout_diagnostics(
            workflow_kind=workflow_kind,
            has_reference_source=has_reference_source,
            reference_image_path=reference_image_path,
        )
        diagnostics = {**called_plugin, **diagnostics}
        logger.info(
            "图片生成排障测试开始: workflow_kind=%s prompt_chars=%s reference=%s timeout=%ss estimated=%ss prompt=%s",
            self._single_line(workflow_kind, 40),
            len(str(prompt_text or "")),
            has_reference_source,
            diagnostics.get("test_timeout_seconds"),
            diagnostics.get("estimated_timeout_seconds"),
            self._single_line(prompt_text, 180),
        )
        timeout = self._int(diagnostics.get("test_timeout_seconds"), 240, 45, 900)
        try:
            generation_output = await asyncio.wait_for(
                generator(
                    workflow_kind=workflow_kind,
                    prompt_text=prompt_text,
                    session_key="private_companion_troubleshooting",
                    reference_image_path=reference_image_path,
                ),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            elapsed_ms = int((time.time() - started) * 1000)
            warnings = list(diagnostics.get("warnings") or [])
            timeout_targets = "在线 API、备用 API、参考图接口或本地工作流队列" if reference_image_path else "在线 API、备用 API 或本地工作流队列"
            warnings.insert(0, f"测试等待 {timeout}s 后仍未返回；实际链路可能卡在{timeout_targets}。")
            return {
                "ok": False,
                "title": "图片生成链路测试",
                "backend": "",
                "path": "",
                "file_size": 0,
                "detail": f"测试超时（{timeout}s）",
                "prompt": self._single_line(prompt_text, 220),
                "workflow_kind": self._single_line(workflow_kind, 20),
                "reference_image": _path_text(reference_image_path, 1000),
                "used_reference": False,
                "image_model": self._single_line(getattr(self.plugin, "external_image_api_model", ""), 80),
                "elapsed_ms": elapsed_ms,
                "error": f"测试超时（{timeout}s）",
                **diagnostics,
                "warnings": warnings[:8],
            }
        generation_metadata: dict[str, Any] = {}
        if hasattr(generation_output, "as_legacy_tuple"):
            backend_name, image_path, note = generation_output.as_legacy_tuple()
            generation_metadata = {
                "used_reference": bool(getattr(generation_output, "reference_used", False)),
                "reference_image": _path_text(getattr(generation_output, "reference_selected_path", ""), 1000),
                "reference_id": self._single_line(getattr(generation_output, "reference_id", ""), 60),
                "reference_kind": self._single_line(getattr(generation_output, "reference_kind", ""), 40),
                "reference_roles": list(getattr(generation_output, "reference_roles", ()) or ()),
                "wardrobe_mode": self._single_line(getattr(generation_output, "wardrobe_mode", ""), 40),
                "wardrobe_category": self._single_line(getattr(generation_output, "wardrobe_category", ""), 40),
                "outfit_locked": bool(getattr(generation_output, "outfit_locked", False)),
                "daily_outfit_removed": bool(getattr(generation_output, "daily_outfit_removed", False)),
                "final_presets": list(getattr(generation_output, "preset_names", ()) or ())[:1],
                "prompt_hash": self._single_line(getattr(generation_output, "prompt_hash", ""), 80),
                "prompt_path": _path_text(getattr(generation_output, "prompt_path", ""), 1000),
                "reference_requested_roles": list(getattr(generation_output, "reference_requested_roles", ()) or ()),
                "reference_excluded_roles": list(getattr(generation_output, "reference_excluded_roles", ()) or ()),
                "continuity_mode": self._single_line(getattr(generation_output, "continuity_mode", ""), 30),
                "reference_confidence": getattr(generation_output, "reference_confidence", 0.0),
                "reference_plan": list(getattr(generation_output, "reference_plan", ()) or ()),
                "reference_fulfilled_roles": list(getattr(generation_output, "reference_fulfilled_roles", ()) or ()),
                "reference_missing_roles": list(getattr(generation_output, "reference_missing_roles", ()) or ()),
                "reference_fallback_message": self._single_line(getattr(generation_output, "reference_fallback_message", ""), 260),
            }
        else:
            backend_name, image_path, note = generation_output
        elapsed_ms = int((time.time() - started) * 1000)
        exists = False
        file_size = 0
        if image_path:
            try:
                image_file = Path(str(image_path))
                exists = image_file.exists()
                file_size = image_file.stat().st_size if exists else 0
            except Exception:
                exists = False
        logger.info(
            "图片生成排障测试结束: ok=%s backend=%s elapsed=%sms path=%s exists=%s size=%s note=%s",
            bool(image_path and exists),
            self._single_line(backend_name, 80),
            elapsed_ms,
            self._single_line(image_path, 180),
            exists,
            file_size,
            self._single_line(note, 180),
        )
        return {
            "ok": bool(image_path and exists),
            "title": "图片生成链路测试",
            "backend": self._single_line(backend_name, 80),
            "path": _path_text(image_path, 1000),
            "file_size": file_size,
            "detail": self._single_line(note, 220) or ("已生成图片" if image_path else "未返回图片路径"),
            "prompt": self._single_line(prompt_text, 220),
            "workflow_kind": self._single_line(workflow_kind, 20),
            "reference_image": _path_text(generation_metadata.get("reference_image") or reference_image_path, 1000),
            "used_reference": (
                bool(generation_metadata.get("used_reference"))
                if generation_metadata
                else self._image_generation_result_used_reference(
                    workflow_kind=workflow_kind,
                    image_path=image_path,
                    image_exists=exists,
                    note=note,
                )
            ),
            "reference_id": self._single_line(generation_metadata.get("reference_id"), 60),
            "reference_kind": self._single_line(generation_metadata.get("reference_kind"), 40),
            "reference_roles": list(generation_metadata.get("reference_roles") or [])[:8],
            "reference_intent": {
                "requested_roles": list(generation_metadata.get("reference_requested_roles") or [])[:8],
                "excluded_roles": list(generation_metadata.get("reference_excluded_roles") or [])[:8],
                "continuity_mode": self._single_line(generation_metadata.get("continuity_mode"), 30),
                "confidence": generation_metadata.get("reference_confidence", 0.0),
            },
            "reference_plan": list(generation_metadata.get("reference_plan") or [])[:8],
            "reference_fulfilled_roles": list(generation_metadata.get("reference_fulfilled_roles") or [])[:8],
            "reference_missing_roles": list(generation_metadata.get("reference_missing_roles") or [])[:8],
            "reference_fallback_message": self._single_line(generation_metadata.get("reference_fallback_message"), 260),
            "wardrobe_mode": self._single_line(generation_metadata.get("wardrobe_mode"), 40),
            "wardrobe_category": self._single_line(generation_metadata.get("wardrobe_category"), 40),
            "outfit_locked": bool(generation_metadata.get("outfit_locked")),
            "daily_outfit_removed": bool(generation_metadata.get("daily_outfit_removed")),
            "final_presets": list(generation_metadata.get("final_presets") or [])[:1],
            "prompt_hash": self._single_line(generation_metadata.get("prompt_hash"), 80),
            "prompt_path": _path_text(generation_metadata.get("prompt_path"), 1000),
            "image_model": self._single_line(getattr(self.plugin, "external_image_api_model", ""), 80),
            "elapsed_ms": elapsed_ms,
            "error": "" if image_path and exists else (self._single_line(note, 220) or "图片生成未返回有效文件"),
            **diagnostics,
        }

    def _image_generation_timeout_diagnostics(
        self,
        *,
        workflow_kind: str,
        has_reference_source: bool,
        reference_image_path: str,
    ) -> dict[str, Any]:
        preferred = self._single_line(getattr(self.plugin, "photo_generation_backend", "auto"), 30).lower() or "auto"
        external_timeout = self._int(getattr(self.plugin, "external_image_api_timeout_seconds", 180), 180, 20, 600)
        backup_timeout = self._int(getattr(self.plugin, "backup_external_image_api_timeout_seconds", 180), 180, 20, 600)
        comfyui_wait = self._int(getattr(self.plugin, "comfyui_photo_wait_seconds", 90), 90, 5, 600)
        endpoint_queue: list[dict[str, Any]] = []
        queue_getter = getattr(self.plugin, "_external_image_api_endpoint_queue", None)
        if callable(queue_getter):
            try:
                endpoint_queue = [
                    endpoint
                    for endpoint in queue_getter(include_incomplete=True)
                    if isinstance(endpoint, dict) and endpoint.get("enabled", True)
                ]
            except Exception:
                endpoint_queue = []
        primary_external_configured = bool(
            getattr(self.plugin, "external_image_api_base_url", "")
            and getattr(self.plugin, "external_image_api_key", "")
            and getattr(self.plugin, "external_image_api_model", "")
        )
        endpoint_unavailable_note = getattr(
            self.plugin,
            "_external_image_api_endpoint_unavailable_note",
            None,
        )
        ready_endpoint_queue = []
        for endpoint in endpoint_queue:
            try:
                unavailable_note = (
                    endpoint_unavailable_note(endpoint)
                    if callable(endpoint_unavailable_note)
                    else "" if all(
                        str(endpoint.get(key) or "").strip()
                        for key in ("base_url", "api_key", "model")
                    ) else "incomplete"
                )
            except Exception:
                unavailable_note = ""
            if not unavailable_note:
                ready_endpoint_queue.append(endpoint)
        extension_status = self._image_generation_extension_status()
        generation_status = extension_status.get("generation")
        generation_status = generation_status if isinstance(generation_status, dict) else {}
        generation_state = self._single_line(generation_status.get("state"), 20).lower()
        generation_schema = self._single_line(
            generation_status.get("status_schema_version")
            or extension_status.get("status_schema_version"),
            80,
        )
        generation_backends = generation_status.get("backends")
        generation_backends = generation_backends if isinstance(generation_backends, dict) else {}
        has_explicit_generation_status = bool(
            generation_schema and generation_state in {"ready", "unavailable"}
        )
        if has_explicit_generation_status:
            external_available = bool(generation_backends.get("external"))
            backup_available = bool(generation_backends.get("backup_external"))
            comfyui_available = bool(generation_backends.get("comfyui"))
            sdgen_available = bool(generation_backends.get("sdgen"))
            availability_source = generation_schema
        else:
            reported_backup_available = bool(
                getattr(self.plugin, "_backup_external_photo_available", lambda: False)()
            )
            reported_external_available = bool(
                getattr(self.plugin, "_external_photo_available", lambda: False)()
            )
            external_available = bool(
                reported_external_available
                or ready_endpoint_queue
                or primary_external_configured
            )
            backup_available = bool(
                reported_backup_available
                or len(ready_endpoint_queue) > 1
            )
            comfyui_available = bool(getattr(self.plugin, "_comfyui_photo_available", lambda: False)())
            sdgen_available = bool(getattr(self.plugin, "_sdgen_photo_available", lambda: False)())
            if ready_endpoint_queue:
                availability_source = "endpoint_queue"
            elif primary_external_configured:
                availability_source = "legacy_config"
            else:
                availability_source = "legacy_runtime_probe"
        tool_call_timeout = self._image_generation_tool_call_timeout_seconds()

        segments: list[tuple[str, int]] = []
        warnings: list[str] = []
        if preferred == "nai":
            segments.append(("NAI 生图直连", external_timeout * 2))
            warnings.append("NAI 直连超时与重试由 NAI 生图插件内部控制，本插件只能估算耗时。")
        if preferred == "anima_master":
            segments.append(("Anima 绘图大师", 420))
            warnings.append("Anima 的生成、启动等待与重试由绘图大师配置控制，测试耗时为估算值。")
        if preferred in {"auto", "external"} and external_available:
            if ready_endpoint_queue:
                for index, endpoint in enumerate(ready_endpoint_queue[:12]):
                    timeout_seconds = self._int(endpoint.get("timeout_seconds"), external_timeout, 20, 600)
                    name = self._single_line(endpoint.get("name") or endpoint.get("model") or f"在线图片 API #{index + 1}", 40)
                    segments.append((name, timeout_seconds * 2))
                if len(ready_endpoint_queue) > 1:
                    warnings.append(
                        f"检测到 {len(ready_endpoint_queue)} 条可用在线生图 API：会按优先级逐条失败后再试下一条，完整失败链路会比单接口测试更慢。"
                    )
            else:
                segments.append(("主在线图片 API", external_timeout * 2))
                if backup_available:
                    segments.append(("备选在线图片 API", backup_timeout * 2))
                    warnings.append("已启用备选在线图片 API：主接口失败或超时后会再跑一轮备选接口，实际耗时可能明显长于单次测试观感。")
        if preferred in {"auto", "comfyui"} and comfyui_available:
            segments.append(("ComfyUI", comfyui_wait))
        if preferred in {"auto", "sdgen"} and sdgen_available:
            segments.append(("SDGen", 180))
            warnings.append("SDGen 调用由 SDGen 插件自身控制，本插件只能估算耗时，无法完全保证外层超时。")

        estimated = sum(seconds for _, seconds in segments) + 30
        if not segments:
            estimated = max(45, external_timeout + 30, comfyui_wait + 30)
        test_timeout = min(900, max(45, estimated + 30))
        if estimated + 30 > test_timeout:
            warnings.append(f"估算完整回退链路约 {estimated}s，排障测试外层最多等待 {test_timeout}s；极端慢链路仍可能被测试层截断。")
        if preferred == "auto" and len(segments) > 1:
            warnings.append("当前为自动后端：真实出图可能按在线 API、ComfyUI、SDGen 依次回退，测试通过只代表本次命中的那条链路可用。")
        if has_reference_source and workflow_kind not in {"selfie", "portrait", "自拍", "人像"}:
            warnings.append("已配置参考图，但本次测的是文生图；自拍、表情包、改图或 QQ 空间人物配图仍需单独测试参考图链路。")
        if workflow_kind in {"selfie", "portrait", "自拍", "人像"} and not reference_image_path and has_reference_source:
            warnings.append("检测到参考图配置，但本次没有解析到可用本地参考图；真实自拍可能会因下载/路径问题失败。")
        if external_available:
            warnings.append("在线图片 API 使用全局串行锁；多个生图请求同时发生时，后来的请求会先排队，单独点击测试无法覆盖排队等待。")
        if tool_call_timeout and estimated > tool_call_timeout:
            warnings.append(
                f"自然语言/主链工具调用可能受 AstrBot tool_call_timeout={tool_call_timeout}s 限制；"
                f"当前完整链路估算约 {estimated}s，测试能等到不代表 pc_generate_photo 工具一定不会超时。"
            )
        warnings.append("排障生图只检查生成文件，不覆盖后续发图、QQ 空间发布、记忆回写和主链工具调用耗时。")

        return {
            **self._image_generation_called_plugin_diagnostics(extension_status),
            "timeout_seconds": test_timeout,
            "test_timeout_seconds": test_timeout,
            "estimated_timeout_seconds": estimated,
            "timeout_budget": " + ".join(f"{name}{seconds}s" for name, seconds in segments) or "未命中可用后端",
            "backend_preference": preferred,
            "external_timeout_seconds": external_timeout,
            "backup_external_timeout_seconds": backup_timeout,
            "comfyui_wait_seconds": comfyui_wait,
            "backup_external": backup_available,
            "external_queue_lock": external_available,
            "availability_source": availability_source,
            "endpoint_count": len(endpoint_queue),
            "ready_endpoint_count": len(ready_endpoint_queue),
            "tool_call_timeout_seconds": tool_call_timeout,
            "warnings": warnings[:8],
        }

    def _image_generation_tool_call_timeout_seconds(self) -> int:
        context = getattr(self.plugin, "context", None)
        getter = getattr(context, "get_config", None)
        if not callable(getter):
            return 120
        try:
            cfg = getter()
        except Exception:
            return 120
        provider_settings = cfg.get("provider_settings", {}) if isinstance(cfg, dict) else {}
        if not isinstance(provider_settings, dict):
            return 120
        return self._int(provider_settings.get("tool_call_timeout"), 120, 1, 3600)

    def _photo_prompt_debug_payload(self, value: Any) -> dict[str, Any]:
        raw_path = _path_text(value, 1000)
        if not raw_path:
            return {}
        try:
            root = (Path(self.plugin.data_dir) / "photo_prompt_debug").resolve()
            path = Path(raw_path).expanduser().resolve()
            if path.parent != root or path.suffix.lower() != ".json" or not path.is_file():
                return {}
            if path.stat().st_size > 256 * 1024:
                return {}
            payload = json.loads(path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return {}

    def _recent_photo_generation_summary(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        raw = data.get("recent_photo_generations")
        if not isinstance(raw, list):
            return []

        def compact_audit(values: Any) -> list[dict[str, str]]:
            if not isinstance(values, list):
                return []
            result: list[dict[str, str]] = []
            for value in values[:24]:
                if not isinstance(value, dict):
                    continue
                record = {
                    key: self._single_line(value.get(key), 120 if key == "preview" else 80)
                    for key in ("source", "section", "rule", "category", "action", "preview", "sha256")
                    if self._single_line(value.get(key), 120 if key == "preview" else 80)
                }
                if record:
                    result.append(record)
            return result

        items: list[dict[str, Any]] = []
        for item in raw[:8]:
            if not isinstance(item, dict):
                continue
            ts = self._float(item.get("ts"))
            prompt = str(item.get("prompt") or "")
            debug_payload = self._photo_prompt_debug_payload(item.get("prompt_path"))
            full_prompt = str(debug_payload.get("final_prompt") or "")
            raw_workflow_fixed = item.get("workflow_fixed_prompt")
            if not isinstance(raw_workflow_fixed, dict):
                raw_workflow_fixed = debug_payload.get("workflow_fixed_prompt")
            if not isinstance(raw_workflow_fixed, dict):
                raw_workflow_fixed = {}
            items.append(
                {
                    "ts": ts,
                    "time": self.plugin._format_timestamp_elapsed(ts),
                    "trace": self._single_line(item.get("trace"), 40),
                    "session": self._single_line(item.get("session"), 100),
                    "kind": self._single_line(item.get("kind"), 30),
                    "backend": self._single_line(item.get("backend"), 80),
                    "ok": bool(item.get("ok")),
                    "prompt_format": self._single_line(item.get("prompt_format"), 30),
                    "prompt": prompt[:500],
                    "full_prompt": full_prompt[:12000],
                    "prompt_preview": self._single_line(prompt, 180),
                    "prompt_hash": self._single_line(
                        item.get("prompt_hash") or debug_payload.get("final_prompt_sha256"),
                        80,
                    ),
                    "prompt_path": _path_text(item.get("prompt_path"), 1000),
                    "path": _path_text(item.get("path"), 1000),
                    "note": self._single_line(item.get("note"), 220),
                    "reference": bool(item.get("reference")),
                    "reference_used": bool(item.get("reference_used")),
                    "reference_path": _path_text(item.get("reference_path"), 1000),
                    "reference_id": self._single_line(item.get("reference_id"), 60),
                    "reference_kind": self._single_line(item.get("reference_kind"), 40),
                    "reference_roles": [
                        self._single_line(role, 40)
                        for role in (item.get("reference_roles") if isinstance(item.get("reference_roles"), list) else [])
                        if self._single_line(role, 40)
                    ][:8],
                    "reference_outfit_category": self._single_line(item.get("reference_outfit_category"), 40),
                    "image_size": self._single_line(item.get("image_size"), 40),
                    "elapsed_ms": self._int(item.get("elapsed_ms")),
                    "trigger": self._single_line(item.get("trigger"), 40),
                    "intent_kind": self._single_line(item.get("intent_kind"), 30),
                    "sent": bool(item.get("sent")),
                    "caption": self._single_line(item.get("caption"), 120),
                    "scene_preset": self._single_line(item.get("scene_preset"), 80),
                    "preset_hint": self._single_line(item.get("preset_hint"), 80),
                    "preset_source": self._single_line(item.get("preset_source"), 40),
                    "suggestion_status": self._single_line(item.get("suggestion_status"), 60),
                    "wardrobe_mode": self._single_line(item.get("wardrobe_mode"), 40),
                    "wardrobe_source": self._single_line(item.get("wardrobe_source"), 40),
                    "wardrobe_category": self._single_line(item.get("wardrobe_category"), 40),
                    "outfit_locked": bool(item.get("outfit_locked")),
                    "daily_outfit_removed": bool(item.get("daily_outfit_removed")),
                    "wardrobe_reason": self._single_line(item.get("wardrobe_reason"), 240),
                    "conflicts": [
                        self._single_line(value, 120)
                        for value in (item.get("conflicts") if isinstance(item.get("conflicts"), list) else [])
                        if self._single_line(value, 120)
                    ][:12],
                    "removed_conflicts": [
                        self._single_line(value, 120)
                        for value in (item.get("removed_conflicts") if isinstance(item.get("removed_conflicts"), list) else [])
                        if self._single_line(value, 120)
                    ][:12],
                    "residual_conflicts": [
                        self._single_line(value, 120)
                        for value in (
                            item.get("residual_conflicts")
                            if isinstance(item.get("residual_conflicts"), list)
                            else []
                        )
                        if self._single_line(value, 120)
                    ][:12],
                    "reference_removed": bool(item.get("reference_removed")),
                    "reference_removal": (
                        dict(item.get("reference_removal"))
                        if isinstance(item.get("reference_removal"), dict)
                        else {}
                    ),
                    "sanitizer_version": self._int(item.get("sanitizer_version")),
                    "workflow_fixed_prompt": {
                        "scope": self._single_line(raw_workflow_fixed.get("scope"), 30),
                        "config_key": self._single_line(
                            raw_workflow_fixed.get("config_key"), 80
                        ),
                        "configured": bool(raw_workflow_fixed.get("configured")),
                        "normalized": bool(raw_workflow_fixed.get("normalized")),
                        "normalization_changed": bool(
                            raw_workflow_fixed.get("normalization_changed")
                        ),
                        "conflict_cleaned": bool(
                            raw_workflow_fixed.get("conflict_cleaned")
                        ),
                        "cleaned": bool(raw_workflow_fixed.get("cleaned")),
                        "applied": bool(raw_workflow_fixed.get("applied")),
                        "raw_length": self._int(raw_workflow_fixed.get("raw_length")),
                        "normalized_length": self._int(
                            raw_workflow_fixed.get("normalized_length")
                        ),
                        "applied_length": self._int(
                            raw_workflow_fixed.get("applied_length")
                        ),
                        "removed_rules": [
                            self._single_line(value, 80)
                            for value in (
                                raw_workflow_fixed.get("removed_rules")
                                if isinstance(raw_workflow_fixed.get("removed_rules"), list)
                                else []
                            )
                            if self._single_line(value, 80)
                        ][:12],
                    },
                    "detected_conflicts": compact_audit(item.get("detected_conflicts")),
                    "removed_conflict_details": compact_audit(item.get("removed_conflict_details")),
                    "residual_conflict_details": compact_audit(item.get("residual_conflict_details")),
                    "tool_name": self._single_line(item.get("tool_name"), 60),
                    "presets": [
                        self._single_line(name, 40)
                        for name in (item.get("presets") if isinstance(item.get("presets"), list) else [])
                        if self._single_line(name, 40)
                    ][:1],
                }
            )
        return items

    def _sync_legacy_external_image_api_config_from_endpoints(self, endpoints: list[dict[str, Any]]) -> None:
        normalized = endpoints if isinstance(endpoints, list) else []
        first = normalized[0] if len(normalized) >= 1 and isinstance(normalized[0], dict) else {}
        second = normalized[1] if len(normalized) >= 2 and isinstance(normalized[1], dict) else {}

        def endpoint_complete(endpoint: dict[str, Any]) -> bool:
            return bool(
                endpoint
                and endpoint.get("enabled", True)
                and str(endpoint.get("base_url") or "").strip()
                and str(endpoint.get("api_key") or "").strip()
                and str(endpoint.get("model") or "").strip()
            )

        updates = {
            "external_image_api_platform": first.get("platform", "auto") if first else "auto",
            "EXTERNAL_IMAGE_API_BASE_URL": first.get("base_url", "") if first else "",
            "EXTERNAL_IMAGE_API_KEY": first.get("api_key", "") if first else "",
            "EXTERNAL_IMAGE_API_MODEL": first.get("model", "") if first else "",
            "external_image_api_size": first.get("size", "1024x1024") if first else "1024x1024",
            "external_image_api_timeout_seconds": self._int(first.get("timeout_seconds"), 180, 20, 600) if first else 180,
            "external_image_api_custom_headers": first.get("custom_headers", "") if first else "",
            "enable_backup_external_image_api": endpoint_complete(second),
            "backup_external_image_api_platform": second.get("platform", "auto") if second else "auto",
            "BACKUP_EXTERNAL_IMAGE_API_BASE_URL": second.get("base_url", "") if second else "",
            "BACKUP_EXTERNAL_IMAGE_API_KEY": second.get("api_key", "") if second else "",
            "BACKUP_EXTERNAL_IMAGE_API_MODEL": second.get("model", "") if second else "",
            "backup_external_image_api_size": second.get("size", "1024x1024") if second else "1024x1024",
            "backup_external_image_api_timeout_seconds": self._int(second.get("timeout_seconds"), 180, 20, 600) if second else 180,
            "backup_external_image_api_custom_headers": second.get("custom_headers", "") if second else "",
        }
        attr_map = {
            "external_image_api_platform": "external_image_api_platform",
            "EXTERNAL_IMAGE_API_BASE_URL": "external_image_api_base_url",
            "EXTERNAL_IMAGE_API_KEY": "external_image_api_key",
            "EXTERNAL_IMAGE_API_MODEL": "external_image_api_model",
            "external_image_api_size": "external_image_api_size",
            "external_image_api_timeout_seconds": "external_image_api_timeout_seconds",
            "external_image_api_custom_headers": "external_image_api_custom_headers",
            "enable_backup_external_image_api": "enable_backup_external_image_api",
            "backup_external_image_api_platform": "backup_external_image_api_platform",
            "BACKUP_EXTERNAL_IMAGE_API_BASE_URL": "backup_external_image_api_base_url",
            "BACKUP_EXTERNAL_IMAGE_API_KEY": "backup_external_image_api_key",
            "BACKUP_EXTERNAL_IMAGE_API_MODEL": "backup_external_image_api_model",
            "backup_external_image_api_size": "backup_external_image_api_size",
            "backup_external_image_api_timeout_seconds": "backup_external_image_api_timeout_seconds",
            "backup_external_image_api_custom_headers": "backup_external_image_api_custom_headers",
        }
        for key, value in updates.items():
            self._set_config_value(key, value)
            setattr(self.plugin, attr_map[key], value)

    def _sync_photo_generation_runtime_config(self) -> None:
        reference_enabled = self._config_get("enable_photo_reference_image")
        if reference_enabled not in ("", None):
            self.plugin.enable_photo_reference_image = self._normalize_bool_value(reference_enabled)
        structured_enabled = self._config_get("enable_p5_structured_reference_assets")
        if structured_enabled not in ("", None):
            self.plugin.enable_p5_structured_reference_assets = self._normalize_bool_value(structured_enabled)
        relationship_enabled = self._config_get("enable_bot_relationship_network")
        if relationship_enabled not in ("", None):
            self.plugin.enable_bot_relationship_network = self._normalize_bool_value(relationship_enabled)
        enabled_backup = self._config_get("enable_backup_external_image_api")
        if enabled_backup not in ("", None):
            self.plugin.enable_backup_external_image_api = self._normalize_bool_value(enabled_backup)
        use_environment_proxy = self._config_get("external_image_download_use_environment_proxy")
        if use_environment_proxy not in ("", None):
            self.plugin.external_image_download_use_environment_proxy = self._normalize_bool_value(use_environment_proxy)
        mapping = {
            "photo_generation_backend": "photo_generation_backend",
            "custom_photo_tool_name": "custom_photo_tool_name",
            "custom_photo_tool_prompt_param": "custom_photo_tool_prompt_param",
            "custom_photo_tool_kind_param": "custom_photo_tool_kind_param",
            "custom_photo_tool_reference_param": "custom_photo_tool_reference_param",
            "custom_photo_tool_extra_params": "custom_photo_tool_extra_params",
            "COMFYUI_TEXT2IMG_WORKFLOW_NAME": "comfyui_text2img_workflow_name",
            "COMFYUI_SELFIE_WORKFLOW_NAME": "comfyui_selfie_workflow_name",
            "photo_reference_catalog": "photo_reference_catalog",
            "photo_persona_reference_image_path": "photo_persona_reference_image_path",
            "photo_reference_library": "photo_reference_library",
            "enable_wardrobe": "enable_wardrobe",
            "wardrobe_tendency": "wardrobe_tendency",
            "enable_wardrobe_prompt": "enable_wardrobe_prompt",
            "wardrobe_prompt_max_items": "wardrobe_prompt_max_items",
            "wardrobe_image_max_count": "wardrobe_image_max_count",
            "wardrobe_image_prompt": "wardrobe_image_prompt",
            "WARDROBE_VISION_PROVIDER_ID": "wardrobe_vision_provider_id",
            "wardrobe_items": "wardrobe_items",
            "wardrobe_photo_source": "wardrobe_photo_source",
            "daily_outfit_photo_prompt": "daily_outfit_photo_prompt",
            "daily_outfit_rotation_days": "daily_outfit_rotation_days",
            "external_image_api_platform": "external_image_api_platform",
            "EXTERNAL_IMAGE_API_BASE_URL": "external_image_api_base_url",
            "EXTERNAL_IMAGE_API_KEY": "external_image_api_key",
            "EXTERNAL_IMAGE_API_MODEL": "external_image_api_model",
            "external_image_api_size": "external_image_api_size",
            "external_image_api_custom_headers": "external_image_api_custom_headers",
            "external_image_download_proxy": "external_image_download_proxy",
            "backup_external_image_api_platform": "backup_external_image_api_platform",
            "BACKUP_EXTERNAL_IMAGE_API_BASE_URL": "backup_external_image_api_base_url",
            "BACKUP_EXTERNAL_IMAGE_API_KEY": "backup_external_image_api_key",
            "BACKUP_EXTERNAL_IMAGE_API_MODEL": "backup_external_image_api_model",
            "backup_external_image_api_size": "backup_external_image_api_size",
            "backup_external_image_api_custom_headers": "backup_external_image_api_custom_headers",
            "external_image_api_endpoints": "external_image_api_endpoints",
            "photo_generation_prompt_format": "photo_generation_prompt_format",
            "photo_generation_style": "photo_generation_style",
            "photo_generation_style_custom_prompt": "photo_generation_style_custom_prompt",
            "photo_generation_negative_prompt_mode": "photo_generation_negative_prompt_mode",
            "photo_generation_negative_prompt": "photo_generation_negative_prompt",
            "photo_generation_text2img_negative_prompt": "photo_generation_text2img_negative_prompt",
            "photo_generation_selfie_negative_prompt": "photo_generation_selfie_negative_prompt",
            "photo_generation_edit_negative_prompt": "photo_generation_edit_negative_prompt",
            "photo_generation_fixed_prompt": "photo_generation_fixed_prompt",
            "photo_generation_text2img_fixed_prompt": "photo_generation_text2img_fixed_prompt",
            "photo_generation_selfie_fixed_prompt": "photo_generation_selfie_fixed_prompt",
            "photo_generation_edit_fixed_prompt": "photo_generation_edit_fixed_prompt",
            "photo_generation_scene_presets": "photo_generation_scene_presets",
            "bot_relationship_cards": "bot_relationship_cards",
        }
        for key, attr in mapping.items():
            value = self._config_get_raw(key) if key in {"external_image_api_endpoints", "photo_reference_catalog", "photo_reference_library", "bot_relationship_cards"} else self._config_get(key)
            if key == "external_image_api_endpoints":
                normalizer = getattr(self.plugin, "_normalize_external_image_api_endpoints", None)
                endpoints = normalizer(value) if callable(normalizer) else (value if isinstance(value, list) else [])
                self.plugin.external_image_api_endpoints = endpoints
                continue
            if key == "photo_reference_catalog":
                try:
                    serialized_catalog = self._normalize_setting_value(key, value)
                    loaded_catalog = load_catalog(
                        serialized_catalog,
                        catalog_version=CATALOG_VERSION,
                        preset_names=self._photo_reference_preset_names(),
                    )
                    self.plugin.photo_reference_catalog = loaded_catalog.references
                    self.plugin.photo_reference_catalog_version = CATALOG_VERSION
                    self.plugin.photo_reference_catalog_read_only = loaded_catalog.read_only
                except CatalogValidationError as exc:
                    logger.warning("忽略无效的运行时参考图目录同步: %s", self._single_line(exc, 180))
                continue
            if key == "photo_reference_library":
                normalized_library = self._normalize_setting_value(key, value)
                setattr(self.plugin, attr, normalized_library if isinstance(normalized_library, list) else [])
                continue
            if key == "bot_relationship_cards":
                normalized_cards = self._normalize_setting_value(key, value)
                setattr(self.plugin, attr, normalized_cards if isinstance(normalized_cards, list) else [])
                continue
            if key == "photo_persona_reference_image_path":
                setattr(self.plugin, attr, str(value or "").strip())
                continue
            if value not in ("", None):
                text = str(value).strip()
                if key == "photo_generation_backend":
                    text = text.lower()
                    if text not in {"auto", "comfyui", "sdgen", "external", "tool_call", "nai", "anima_master"}:
                        text = "auto"
                elif key == "photo_generation_prompt_format":
                    normalizer = getattr(self.plugin, "_normalize_photo_generation_prompt_format", None)
                    text = normalizer(text) if callable(normalizer) else text.lower()
                    if text not in {"traditional", "natural_language", "nai"}:
                        text = "traditional"
                elif key == "photo_generation_negative_prompt_mode":
                    normalizer = getattr(self.plugin, "_normalize_photo_generation_negative_prompt_mode", None)
                    text = normalizer(text) if callable(normalizer) else text.lower()
                    if text not in {"safe_default", "merge", "replace"}:
                        text = "safe_default"
                elif key in {"external_image_api_platform", "backup_external_image_api_platform"}:
                    normalizer = getattr(self.plugin, "_normalize_external_image_api_platform", None)
                    text = normalizer(text) if callable(normalizer) else text.lower()
                    if text not in {"auto", "openai", "openrouter", "agnes", "sensenova", "bailian", "modelscope", "doubao", "gemini", "minimax"}:
                        text = "auto"
                setattr(self.plugin, attr, text)
        timeout = self._config_get("external_image_api_timeout_seconds")
        if timeout not in ("", None):
            try:
                self.plugin.external_image_api_timeout_seconds = max(20, min(600, int(float(timeout))))
            except Exception:
                pass
        backup_timeout = self._config_get("backup_external_image_api_timeout_seconds")
        if backup_timeout not in ("", None):
            try:
                self.plugin.backup_external_image_api_timeout_seconds = max(20, min(600, int(float(backup_timeout))))
            except Exception:
                pass
        wait_seconds = self._config_get("comfyui_photo_wait_seconds")
        if wait_seconds not in ("", None):
            try:
                self.plugin.comfyui_photo_wait_seconds = max(5, min(600, int(float(wait_seconds))))
            except Exception:
                pass

