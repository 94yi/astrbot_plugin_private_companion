# -*- coding: utf-8 -*-
"""书架域。

由 tools/split_mixin_domain.py 从 page_api.py 机械抽取（37 个方法 + 2 个模块级名字 + 0 个类级赋值 / 1404 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApi）。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import shutil
import time
from .helpers import _path_text, _strip_internal_message_blocks, _text_similarity
from .story_authority import story_authority_controller
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from .logging_util import get_module_logger
from .page_api_shared import _page_api_host

logger = get_module_logger(__name__)



BOOKSHELF_ACCESS_TOKEN_TTL_SECONDS = 24 * 60 * 60

BOOKSHELF_ACCESS_TOKEN_MAX_PERSISTED = 8


class PrivateCompanionPageApiBookshelfMixin:
    """书架域（从 PrivateCompanionPageApi 拆出）。"""


    async def unlock_bookshelf(self) -> dict[str, Any]:
        payload = await _page_api_host.request.get_json(silent=True) or {}
        password = str(payload.get("password", "")).strip()
        try:
            expected = await self.plugin._ensure_bookshelf_password_async()
            async with self.plugin._data_lock:
                if not self._bookshelf_password_matches(password, expected):
                    return self._error("密码不对。需要在聊天里自然向 Bot 询问。")
                access_token = (self._issue_bookshelf_access_token(persist=True))
                saver = getattr(self.plugin, "_save_data_sync", None)
                if callable(saver):
                    saver(sections={"bookshelf_secret"})
                data = deepcopy(self.plugin.data)
            return self._ok({"bookshelf": await self._bookshelf_summary(data, unlocked=True, access_token=access_token)})
        except Exception as exc:
            logger.error(f"解锁资料柜夹层失败: {exc}", exc_info=True)
            return self._exception_error(str(exc))

    async def get_bookshelf_session(self) -> dict[str, Any]:
        """Restore a previously unlocked bookshelf session from the browser token.

        The browser may call this endpoint after a page reload or a plugin restart. The
        persisted record contains only a SHA-256 token digest, never the bearer token
        itself; the raw token remains available only in the current request/runtime map.
        """
        payload = await _page_api_host.request.get_json(silent=True) or {}
        access_token = (self._bookshelf_request_token(payload))
        if not self._bookshelf_access_token_valid(access_token):
            return self._error(self._bookshelf_access_error()["error"])
        try:
            async with self.plugin._data_lock:
                data = deepcopy(self.plugin.data)
            expires_at = self._bookshelf_access_token_expires_at(access_token)
            bookshelf = await self._bookshelf_summary(data, unlocked=True, access_token=access_token)
            bookshelf["access_expires_at"] = int(expires_at) if expires_at > 0 else 0
            return self._ok({"bookshelf": bookshelf})
        except Exception as exc:
            logger.error(f"恢复资料柜夹层会话失败: {exc}", exc_info=True)
            return self._error(str(exc))

    @staticmethod
    def _normalize_bookshelf_password(value: Any) -> str:
        text = _strip_internal_message_blocks(value)
        text = re.sub(r"\s+", "", text)
        text = text.strip("「」『』“”\"'` 。，,.;；:：!！?？、~～（）()[]【】")
        return text.lower()

    def _bookshelf_password_matches(self, provided: Any, expected: Any) -> bool:
        normalized_expected = self._normalize_bookshelf_password(expected)
        normalized_provided = self._normalize_bookshelf_password(provided)
        if not normalized_expected or not normalized_provided:
            return False
        if normalized_provided == normalized_expected:
            return True
        return len(normalized_expected) >= 2 and normalized_expected in normalized_provided

    def _bookshelf_album_id(self, item: Any, *, limit: int = 80) -> str:
        if not isinstance(item, dict):
            return ""
        explicit_album_id = self._single_line(item.get("album_id"), limit)
        album_id = explicit_album_id or self._single_line(item.get("id"), limit)
        if not explicit_album_id and album_id.startswith("archive-"):
            album_id = self._single_line(album_id.removeprefix("archive-"), limit)
        key = self._single_line(item.get("key"), 120)
        if not album_id and key.startswith("archive_item:"):
            album_id = self._single_line(key.split(":", 1)[1], limit)
        if not album_id and key.startswith("archive-"):
            album_id = self._single_line(key.removeprefix("archive-"), limit)
        return album_id

    def _bookshelf_diary_date_key(self, value: Any) -> str:
        text = self._single_line(value, 64)
        if not text:
            return ""
        match = re.search(
            r"(?<!\d)(\d{4})\s*(?:-|/|\.|年)\s*(\d{1,2})\s*(?:-|/|\.|月)\s*(\d{1,2})(?:日)?(?!\d)",
            text,
        )
        if match:
            try:
                return datetime(
                    int(match.group(1)),
                    int(match.group(2)),
                    int(match.group(3)),
                ).strftime("%Y-%m-%d")
            except ValueError:
                pass
        return text

    def _bookshelf_diary_entry_key(
        self,
        value: Any,
        fallback_date: Any = "",
        duplicate_index: int = 0,
    ) -> str:
        if isinstance(value, dict):
            stored_id = self._single_line(value.get("entry_key") or value.get("id"), 160)
            seed: Any = {"stored_id": stored_id} if stored_id else value
        elif isinstance(value, str):
            seed = {"body": value}
        else:
            seed = {"value": str(value)}
        key_payload = {
            "fallback_date": self._single_line(fallback_date, 64),
            "entry": seed,
        }
        if duplicate_index > 0:
            key_payload["duplicate_index"] = duplicate_index
        serialized = json.dumps(
            key_payload,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:24]
        return f"diary:{digest}"

    def _bookshelf_next_diary_entry_key(
        self,
        value: Any,
        fallback_date: Any,
        occurrences: dict[str, int],
    ) -> str:
        base_key = self._bookshelf_diary_entry_key(value, fallback_date)
        duplicate_index = occurrences.get(base_key, 0)
        occurrences[base_key] = duplicate_index + 1
        if duplicate_index == 0:
            return base_key
        return self._bookshelf_diary_entry_key(value, fallback_date, duplicate_index)

    def _bookshelf_diary_entries(self, value: Any) -> list[dict[str, Any]]:
        if isinstance(value, list):
            source = [("", item) for item in value]
            sort_by_date = False
        elif isinstance(value, dict):
            source = list(value.items())
            sort_by_date = True
        else:
            return []

        entries: list[dict[str, Any]] = []
        entry_key_occurrences: dict[str, int] = {}
        for fallback_date, raw in source:
            entry_key = self._bookshelf_next_diary_entry_key(
                raw,
                fallback_date,
                entry_key_occurrences,
            )
            if isinstance(raw, dict):
                item = deepcopy(raw)
            elif isinstance(raw, str) and raw.strip():
                item = {"body": raw.strip()}
            else:
                continue
            diary_date = self._bookshelf_diary_date_key(item.get("date") or fallback_date)
            item["date"] = diary_date or "某天"
            item["entry_key"] = entry_key
            if not item.get("body"):
                item["body"] = item.get("content") or item.get("text") or ""
            entries.append(item)
        if sort_by_date:
            entries.sort(
                key=lambda item: (
                    self._bookshelf_diary_date_key(item.get("date")) == "某天",
                    self._bookshelf_diary_date_key(item.get("date")),
                    self._single_line(item.get("entry_key"), 80),
                )
            )
        return entries

    def _is_bookshelf_archive_item(self, item: Any) -> bool:
        if not isinstance(item, dict):
            return False
        kind = self._single_line(item.get("type") or item.get("kind"), 32)
        if kind:
            return kind == "archive_item"
        key = self._single_line(item.get("key"), 120)
        return key.startswith("archive_item:") or key.startswith("archive-")

    def _bookshelf_deleted_album_ids(self, state: Any) -> set[str]:
        if not isinstance(state, dict):
            return set()
        return {
            self._single_line(value, 80)
            for value in (state.get("deleted_album_ids") if isinstance(state.get("deleted_album_ids"), list) else [])
            if self._single_line(value, 80)
        }

    def _bookshelf_deleted_title_markers(self, state: Any) -> set[str]:
        if not isinstance(state, dict):
            return set()
        return {
            marker
            for value in (state.get("deleted_titles") if isinstance(state.get("deleted_titles"), list) else [])
            if (marker := " ".join(self._single_line(value, 160).split()).casefold())
        }

    def _is_deleted_bookshelf_archive_item(self, item: Any, state: Any) -> bool:
        if not self._is_bookshelf_archive_item(item):
            return False
        album_id = self._bookshelf_album_id(item)
        if album_id:
            return album_id in self._bookshelf_deleted_album_ids(state)
        title = " ".join(self._single_line(item.get("title"), 160).split()).casefold()
        return bool(title and title in self._bookshelf_deleted_title_markers(state))

    def _mark_bookshelf_data_changed(self) -> None:
        marker = getattr(self.plugin, "_mark_bookshelf_store_changed", None)
        if callable(marker):
            marker()

    def _bookshelf_access_tokens(self) -> dict[str, Any]:
        store = getattr(self.plugin, "_bookshelf_access_tokens", None)
        if not isinstance(store, dict):
            store = {}
            setattr(self.plugin, "_bookshelf_access_tokens", store)
        now = time.time()
        current_persona = self._bookshelf_access_persona_id()
        for token, raw_entry in list(store.items()):
            if isinstance(raw_entry, dict):
                expires_at = self._float(raw_entry.get("expires_at"))
            else:
                # Runtime tokens created before persona binding are safe only in
                # single-persona mode because their original owner is unknowable.
                expires_at = self._float(raw_entry) if not current_persona else 0.0
            if self._float(expires_at) <= now:
                store.pop(token, None)
        return store

    @staticmethod
    def _bookshelf_access_token_digest(token: Any) -> str:
        token_text = str(token or "").strip()
        if not token_text:
            return ""
        return hashlib.sha256(token_text.encode("utf-8")).hexdigest()

    def _bookshelf_persisted_access_entries(self) -> list[dict[str, Any]]:
        data = getattr(self.plugin, "data", None)
        if not isinstance(data, dict):
            return []
        secret = data.get("bookshelf_secret")
        if not isinstance(secret, dict):
            return []
        state = secret.get("web_access")
        if not isinstance(state, dict):
            return []
        raw_entries = state.get("tokens")
        if not isinstance(raw_entries, list):
            # Accept the first single-token shape for forwards/backwards compatibility.
            raw_entries = [state] if state.get("token_hash") or state.get("hash") else []
        entries: list[dict[str, Any]] = []
        for raw in raw_entries:
            if not isinstance(raw, dict):
                continue
            digest = self._single_line(raw.get("token_hash") or raw.get("hash"), 128).lower()
            expires_at = self._float(raw.get("expires_at"))
            if not re.fullmatch(r"[0-9a-f]{64}", digest) or expires_at <= 0:
                continue
            persona_id = self._single_line(raw.get("persona_id"), 96)
            if not persona_id:
                # A persisted token already lives inside the active persona's data
                # profile. Bind legacy records to that profile on read.
                persona_id = self._bookshelf_access_persona_id()
            entries.append(
                {
                    "token_hash": digest,
                    "expires_at": expires_at,
                    "persona_id": persona_id,
                }
            )
        return entries

    def _persist_bookshelf_access_token(self, token: str, expires_at: float) -> None:
        data = getattr(self.plugin, "data", None)
        if not isinstance(data, dict):
            return
        secret = data.setdefault("bookshelf_secret", {})
        if not isinstance(secret, dict):
            secret = {}
            data["bookshelf_secret"] = secret
        digest = self._bookshelf_access_token_digest(token)
        if not digest or expires_at <= 0:
            return
        now = time.time()
        persona_id = self._bookshelf_access_persona_id()
        entries = [
            entry
            for entry in self._bookshelf_persisted_access_entries()
            if self._float(entry.get("expires_at")) > now
            and not hmac.compare_digest(str(entry.get("token_hash") or ""), digest)
        ]
        entries.insert(
            0,
            {
                "token_hash": digest,
                "expires_at": float(expires_at),
                "persona_id": persona_id,
            },
        )
        secret["web_access"] = {
            "version": 2,
            "tokens": entries[:BOOKSHELF_ACCESS_TOKEN_MAX_PERSISTED],
            "updated_at": now,
        }

    def _bookshelf_access_token_expires_at(self, token: Any) -> float:
        token_text = self._single_line(token, 120)
        if not token_text:
            return 0.0
        digest = self._bookshelf_access_token_digest(token_text)
        if not digest:
            return 0.0
        runtime_tokens = self._bookshelf_access_tokens()
        now = time.time()
        persona_id = self._bookshelf_access_persona_id()
        for entry in self._bookshelf_persisted_access_entries():
            entry_digest = str(entry.get("token_hash") or "")
            entry_persona = self._single_line(entry.get("persona_id"), 96)
            if entry_persona == persona_id and hmac.compare_digest(entry_digest, digest):
                expires_at = self._float(entry.get("expires_at"))
                if expires_at > now:
                    # Persisted records are authoritative for tokens that were
                    # explicitly saved, even if this process still has an older
                    # in-memory expiry cached for the same token.
                    runtime_tokens[token_text] = {
                        "expires_at": expires_at,
                        "persona_id": persona_id,
                    }
                    return expires_at
                runtime_tokens.pop(token_text, None)
                return 0.0
        runtime_entry = runtime_tokens.get(token_text)
        if isinstance(runtime_entry, dict):
            runtime_expiry = self._float(runtime_entry.get("expires_at"))
            runtime_persona = self._single_line(runtime_entry.get("persona_id"), 96)
            if runtime_persona == persona_id and runtime_expiry > now:
                return runtime_expiry
        elif not persona_id and self._float(runtime_entry) > now:
            return self._float(runtime_entry)
        return 0.0

    def _issue_bookshelf_access_token(self, *, persist: bool = False) -> str:
        token = secrets.token_urlsafe(24)
        expires_at = time.time() + BOOKSHELF_ACCESS_TOKEN_TTL_SECONDS
        self._bookshelf_access_tokens()[token] = {
            "expires_at": expires_at,
            "persona_id": self._bookshelf_access_persona_id(),
        }
        if persist:
            self._persist_bookshelf_access_token(token, expires_at)
        return token

    def _bookshelf_access_token_valid(self, token: Any) -> bool:
        return self._bookshelf_access_token_expires_at(token) > time.time()

    def _bookshelf_request_token(self, payload: dict[str, Any] | None = None) -> str:
        if isinstance(payload, dict):
            token = self._single_line(payload.get("access_token") or payload.get("token"), 120)
            if token:
                return token
        return self._single_line(_page_api_host.request.args.get("access_token") or _page_api_host.request.args.get("token"), 120)

    def _bookshelf_access_error(self) -> dict[str, str]:
        return {"error": "夹层访问已过期，请重新输入密码打开抽屉"}

    async def delete_bookshelf_item(self) -> dict[str, Any]:
        payload = await _page_api_host.request.get_json(silent=True) or {}
        access_token = (self._bookshelf_request_token(payload))
        if not self._bookshelf_access_token_valid(access_token):
            return self._error(self._bookshelf_access_error()["error"])
        kind = self._single_line(payload.get("kind"), 32)
        item_id = self._single_line(payload.get("id"), 80)
        album_payload_id = self._single_line(payload.get("album_id"), 80)
        title_payload = self._single_line(payload.get("title"), 120)
        date_key = self._single_line(payload.get("date"), 32)
        diary_date_key = self._bookshelf_diary_date_key(date_key)
        diary_entry_key = self._single_line(payload.get("entry_key") or payload.get("diary_key"), 80)
        story_authority_identity: Any | None = None
        if kind == "creative":
            story_authority_identity = (
                story_authority_controller().enter_legacy_operation(
                    "page.bookshelf.creative-delete"
                )
            )
        try:
            async with self.plugin._data_lock:
                changed = False
                changed_sections: set[str]
                if kind == "creative":
                    changed_sections = {"creative_projects"}
                    if not item_id:
                        return self._error("缺少要删除的创作标识")
                    projects = self.plugin.data.get("creative_projects", [])
                    if not isinstance(projects, list):
                        return self._error("创作记录结构异常，已停止删除以避免覆盖原数据")
                    before = len(projects)
                    kept_projects = [
                        item
                        for item in projects
                        if not (isinstance(item, dict) and self._single_line(item.get("id"), 80) == item_id)
                    ]
                    changed = len(kept_projects) != before
                    if changed:
                        self.plugin.data["creative_projects"] = kept_projects
                elif kind == "diary":
                    changed_sections = {
                        "bot_diaries",
                        "daily_diary_deleted_days",
                        "daily_diary_delete_revision",
                    }
                    if not diary_entry_key and not diary_date_key:
                        return self._error("缺少要删除的日记标识")
                    diaries = self.plugin.data.get("bot_diaries", [])
                    storage_type = type(diaries).__name__
                    deleted_dates: set[str] = set()
                    entry_key_occurrences: dict[str, int] = {}

                    def should_delete(item: Any, fallback_date: Any = "") -> bool:
                        candidate_date = self._bookshelf_diary_date_key(
                            (item.get("date") or fallback_date) if isinstance(item, dict) else fallback_date
                        )
                        if diary_entry_key:
                            candidate_entry_key = self._bookshelf_next_diary_entry_key(
                                item,
                                fallback_date,
                                entry_key_occurrences,
                            )
                            matched = candidate_entry_key == diary_entry_key
                        elif diary_date_key == "某天":
                            matched = not candidate_date
                        else:
                            matched = candidate_date == diary_date_key
                        if matched and re.fullmatch(r"\d{4}-\d{2}-\d{2}", candidate_date):
                            deleted_dates.add(candidate_date)
                        return matched

                    if isinstance(diaries, list):
                        kept = [item for item in diaries if not should_delete(item)]
                        changed = len(kept) != len(diaries)
                        if changed:
                            self.plugin.data["bot_diaries"] = kept
                    elif isinstance(diaries, dict):
                        kept_dict: dict[Any, Any] = {}
                        for stored_date, item in diaries.items():
                            if should_delete(item, stored_date):
                                changed = True
                                continue
                            kept_dict[stored_date] = item
                        if changed:
                            self.plugin.data["bot_diaries"] = kept_dict
                    else:
                        return self._error("日记记录结构异常，已停止删除以避免覆盖原数据")
                    if changed:
                        if not deleted_dates and re.fullmatch(r"\d{4}-\d{2}-\d{2}", diary_date_key):
                            deleted_dates.add(diary_date_key)
                        for deleted_date in sorted(deleted_dates):
                            self._remember_deleted_diary_day(deleted_date)
                    remaining = len(self.plugin.data.get("bot_diaries", []))
                    logger.info(
                        "日记删除: changed=%s date=%s entry=%s storage=%s remaining=%s",
                        changed,
                        diary_date_key,
                        diary_entry_key,
                        storage_type,
                        remaining,
                    )
                elif kind == "archive_item":
                    changed_sections = {
                        "bookshelf_items",
                        "reading_archive_integration",
                    }
                    album_id = album_payload_id or item_id.removeprefix("archive-")
                    album_id = album_id.removeprefix("archive-").removeprefix("archive_item:")
                    match_keys = {
                        value
                        for value in {
                            album_id,
                            item_id,
                            item_id.removeprefix("archive-"),
                            f"archive-{album_id}" if album_id else "",
                            f"archive_item:{album_id}" if album_id else "",
                        }
                        if value
                    }
                    items = self.plugin.data.get("bookshelf_items")
                    if not isinstance(items, list):
                        return self._error("夹层记录结构异常，已停止删除以避免覆盖原数据")
                    removed_pages: list[dict[str, Any]] = []
                    removed_album_ids: set[str] = set()
                    kept = []
                    for item in items:
                        if not self._is_bookshelf_archive_item(item):
                            kept.append(item)
                            continue
                        item_values = {
                            self._bookshelf_album_id(item),
                            self._single_line(item.get("id"), 80),
                            self._single_line(item.get("key"), 100),
                        }
                        title_matched = bool(
                            not match_keys
                            and title_payload
                            and self._single_line(item.get("title"), 120) == title_payload
                        )
                        if match_keys.intersection(value for value in item_values if value) or title_matched:
                            removed_album_id = self._bookshelf_album_id(item)
                            if removed_album_id:
                                removed_album_ids.add(removed_album_id)
                            if isinstance(item.get("pages"), list):
                                removed_pages.extend(page for page in item.get("pages", []) if isinstance(page, dict))
                            changed = True
                            continue
                        kept.append(item)
                    self.plugin.data["bookshelf_items"] = kept
                    state = self.plugin.data.get("reading_archive_integration")
                    if isinstance(state, dict):
                        last_album = state.get("last_album")
                        last_values = {
                            self._single_line(last_album.get("id"), 80),
                            self._single_line(last_album.get("album_id"), 80),
                            self._single_line(last_album.get("key"), 100),
                        } if isinstance(last_album, dict) else set()
                        last_title_matched = bool(
                            isinstance(last_album, dict)
                            and not match_keys
                            and title_payload
                            and self._single_line(last_album.get("title"), 120) == title_payload
                        )
                        if isinstance(last_album, dict) and (match_keys.intersection(value for value in last_values if value) or last_title_matched):
                            removed_album_id = self._single_line(last_album.get("id") or last_album.get("album_id"), 80)
                            if removed_album_id:
                                removed_album_ids.add(removed_album_id)
                            state["last_album"] = {}
                            changed = True
                    if not changed and album_id:
                        data_root = Path(str(getattr(self.plugin, "data_dir", ""))).resolve()
                        if (data_root / "bookshelf_pages" / album_id).exists():
                            removed_album_ids.add(album_id)
                            changed = True
                    if changed and (removed_album_ids or title_payload):
                        state = self.plugin.data.setdefault("reading_archive_integration", {})
                        if not isinstance(state, dict):
                            state = {}
                            self.plugin.data["reading_archive_integration"] = state
                        deleted_ids = state.setdefault("deleted_album_ids", [])
                        if not isinstance(deleted_ids, list):
                            deleted_ids = []
                            state["deleted_album_ids"] = deleted_ids
                        for removed_id in sorted(removed_album_ids):
                            if removed_id and removed_id not in deleted_ids:
                                deleted_ids.append(removed_id)
                        del deleted_ids[:-300]
                        deleted_titles = state.setdefault("deleted_titles", [])
                        if not isinstance(deleted_titles, list):
                            deleted_titles = []
                            state["deleted_titles"] = deleted_titles
                        removed_titles = [
                            self._single_line(item.get("title"), 120)
                            for item in items
                            if self._is_bookshelf_archive_item(item)
                            and self._bookshelf_album_id(item) in removed_album_ids
                        ]
                        if title_payload:
                            removed_titles.append(title_payload)
                        for removed_title in removed_titles:
                            if removed_title and removed_title not in deleted_titles:
                                deleted_titles.append(removed_title)
                        del deleted_titles[:-300]
                    self._cleanup_bookshelf_page_files(removed_pages)
                    self._cleanup_bookshelf_album_dirs(removed_album_ids)
                    logger.info(
                        "资料柜夹层移除: changed=%s id=%s album_id=%s title=%s removed=%s",
                        changed,
                        item_id,
                        album_id,
                        title_payload,
                        sorted(removed_album_ids),
                    )
                else:
                    return self._error("不支持的资料柜项目类型")
                if changed:
                    if kind == "archive_item":
                        self._mark_bookshelf_data_changed()
                    self.plugin._save_data_sync(sections=changed_sections)
                data = deepcopy(self.plugin.data)
            return self._ok({"changed": changed, "bookshelf": await self._bookshelf_summary(data, unlocked=True, access_token=access_token)})
        except Exception as exc:
            logger.error(f"删除资料柜项目失败: {exc}", exc_info=True)
            return self._exception_error(str(exc))
        finally:
            if story_authority_identity is not None:
                story_authority_controller().exit_legacy_operation(
                    story_authority_identity
                )

    def _cleanup_bookshelf_page_files(self, pages: list[dict[str, Any]]) -> None:
        data_root = Path(str(getattr(self.plugin, "data_dir", ""))).resolve()
        touched_dirs: set[Path] = set()
        for page in pages:
            path = Path(str(page.get("path") or "")).resolve()
            try:
                path.relative_to(data_root)
            except ValueError:
                continue
            if not path.exists() or not path.is_file():
                continue
            touched_dirs.add(path.parent)
            try:
                path.unlink()
            except Exception:
                pass
        for folder in touched_dirs:
            try:
                folder.relative_to(data_root / "bookshelf_pages")
            except ValueError:
                continue
            try:
                if folder.exists() and not any(folder.iterdir()):
                    shutil.rmtree(folder, ignore_errors=True)
            except Exception:
                pass

    def _cleanup_bookshelf_album_dirs(self, album_ids: set[str]) -> None:
        if not album_ids:
            return
        data_root = Path(str(getattr(self.plugin, "data_dir", ""))).resolve()
        page_root = data_root / "bookshelf_pages"
        for album_id in album_ids:
            safe_id = re.sub(r"[^0-9A-Za-z_.-]+", "_", str(album_id or ""))
            if not safe_id:
                continue
            folder = (page_root / safe_id).resolve()
            try:
                folder.relative_to(page_root.resolve())
            except ValueError:
                continue
            try:
                shutil.rmtree(folder, ignore_errors=True)
            except Exception:
                pass

    async def update_bookshelf_reading_state(self) -> dict[str, Any]:
        payload = await _page_api_host.request.get_json(silent=True) or {}
        access_token = (self._bookshelf_request_token(payload))
        if not self._bookshelf_access_token_valid(access_token):
            return self._error(self._bookshelf_access_error()["error"])
        album_id = self._single_line(payload.get("album_id") or payload.get("id"), 32)
        page = max(1, self._int(payload.get("page")))
        total_pages = max(0, self._int(payload.get("total_pages")))
        bookmark = self._single_line(payload.get("bookmark"), 120)
        if not album_id:
            return self._error("缺少 album_id")
        try:
            async with self.plugin._data_lock:
                items = self.plugin.data.get("bookshelf_items")
                if not isinstance(items, list):
                    return self._error("夹层记录结构异常，未写入阅读进度")
                target = next(
                    (
                        item for item in items
                        if self._is_bookshelf_archive_item(item)
                        and self._bookshelf_album_id(item, limit=32) == album_id
                    ),
                    None,
                )
                if target is None:
                    return self._error("没有找到这本资料归档记录")
                safe_total = total_pages or max(0, self._int(target.get("image_count")))
                if safe_total > 0:
                    page = min(page, safe_total)
                target["reading_progress_page"] = page
                target["reading_progress_total"] = safe_total
                target["reading_progress_updated_at"] = time.time()
                target["reading_started_at"] = self._float(target.get("reading_started_at")) or time.time()
                if bookmark:
                    target["reading_bookmark"] = bookmark
                elif "bookmark" in payload:
                    target["reading_bookmark"] = ""
                if safe_total > 0 and page >= safe_total:
                    target["reading_completed_at"] = self._float(target.get("reading_completed_at")) or time.time()
                self._mark_bookshelf_data_changed()
                self.plugin._save_data_sync(sections={"bookshelf_items"})
                data = deepcopy(self.plugin.data)
            return self._ok({"bookshelf": await self._bookshelf_summary(data, unlocked=True, access_token=access_token)})
        except Exception as exc:
            logger.error(f"更新资料柜阅读进度失败: {exc}", exc_info=True)
            return self._exception_error(str(exc))

    async def rate_bookshelf_item(self) -> dict[str, Any]:
        payload = await _page_api_host.request.get_json(silent=True) or {}
        access_token = (self._bookshelf_request_token(payload))
        if not self._bookshelf_access_token_valid(access_token):
            return self._error(self._bookshelf_access_error()["error"])
        album_id = self._single_line(payload.get("album_id") or payload.get("id"), 32)
        rating = self._int(payload.get("rating"))
        reason = self._single_line(payload.get("reason"), 160)
        if not album_id:
            return self._error("缺少 album_id")
        if rating < 1 or rating > 10:
            return self._error("评分必须是 1 到 10")
        try:
            async with self.plugin._data_lock:
                items = self.plugin.data.get("bookshelf_items")
                if not isinstance(items, list):
                    return self._error("夹层记录结构异常，未写入评分")
                target: dict[str, Any] | None = None
                for item in items:
                    if not self._is_bookshelf_archive_item(item):
                        continue
                    if self._bookshelf_album_id(item) == album_id:
                        item["user_rating"] = rating
                        item["user_rating_reason"] = reason
                        item["user_rated_ts"] = time.time()
                        target = item
                        break
                state = self.plugin.data.setdefault("reading_archive_integration", {})
                if isinstance(state, dict):
                    last_album = state.get("last_album")
                    if isinstance(last_album, dict) and str(last_album.get("id") or last_album.get("album_id") or "") == album_id:
                        last_album["user_rating"] = rating
                        last_album["user_rating_reason"] = reason
                        last_album["user_rated_ts"] = time.time()
                        if target is None:
                            target = last_album
                if target is None:
                    return self._error("没有找到这条资料归档记录")
                updater = getattr(self.plugin, "_update_reading_archive_preference_profile", None)
                if callable(updater):
                    updater(target)
                self._mark_bookshelf_data_changed()
                self.plugin._save_data_sync(sections={"bookshelf_items"})
                data = deepcopy(self.plugin.data)
            return self._ok({"bookshelf": await self._bookshelf_summary(data, unlocked=True, access_token=access_token)})
        except Exception as exc:
            logger.error(f"保存资料归档评分失败: {exc}", exc_info=True)
            return self._exception_error(str(exc))

    def _normalize_bookshelf_tag_list(self, value: Any, *, limit: int = 8) -> list[str]:
        raw_items: list[Any]
        if isinstance(value, str):
            raw_items = re.split(r"[,，、\s\n\r]+", value)
        elif isinstance(value, list):
            raw_items = value
        else:
            raw_items = []
        tags: list[str] = []
        seen: set[str] = set()
        for raw in raw_items:
            tag = self._single_line(raw, 24)
            if not tag:
                continue
            normalized = tag.casefold()
            if normalized in seen:
                continue
            seen.add(normalized)
            tags.append(tag)
            if len(tags) >= limit:
                break
        return tags

    def _normalize_bookshelf_page_comment(self, value: Any, *, limit: int = 100) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        page_no = self._int(value.get("page"))
        comment_text = self._single_line(value.get("comment"), limit)
        if page_no <= 0 or not comment_text:
            return None
        return {
            "page": page_no,
            "comment": comment_text,
            "raw_page": self._int(value.get("raw_page")),
            "sample_order": self._int(
                value.get("sample_order")
                or value.get("sample_index")
                or value.get("reference_index")
                or value.get("image_index")
            ),
        }

    def _merge_bookshelf_page_comments(self, *sources: Any, limit: int = 24) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        seen: set[tuple[int, str]] = set()
        for source in sources:
            if not isinstance(source, list):
                continue
            for item in source:
                normalized = self._normalize_bookshelf_page_comment(item)
                if not normalized:
                    continue
                key = (normalized["page"], normalized["comment"])
                if key in seen:
                    continue
                seen.add(key)
                merged.append(normalized)
                if len(merged) >= limit:
                    return merged
        return merged

    async def update_bookshelf_item_tags(self) -> dict[str, Any]:
        payload = await _page_api_host.request.get_json(silent=True) or {}
        access_token = (self._bookshelf_request_token(payload))
        if not self._bookshelf_access_token_valid(access_token):
            return self._error(self._bookshelf_access_error()["error"])
        album_id = self._single_line(payload.get("album_id") or payload.get("id"), 32)
        liked_tags = self._normalize_bookshelf_tag_list(payload.get("liked_tags"))
        disliked_tags_raw = self._normalize_bookshelf_tag_list(payload.get("disliked_tags"))
        liked_seen = {tag.casefold() for tag in liked_tags}
        disliked_tags = [tag for tag in disliked_tags_raw if tag.casefold() not in liked_seen]
        if not album_id:
            return self._error("缺少 album_id")
        try:
            async with self.plugin._data_lock:
                items = self.plugin.data.get("bookshelf_items")
                if not isinstance(items, list):
                    return self._error("夹层记录结构异常，未写入标签")
                target: dict[str, Any] | None = None
                for item in items:
                    if not self._is_bookshelf_archive_item(item):
                        continue
                    if self._bookshelf_album_id(item) == album_id:
                        item["user_liked_tags"] = liked_tags
                        item["user_disliked_tags"] = disliked_tags
                        item["user_tags_updated_ts"] = time.time()
                        target = item
                        break
                state = self.plugin.data.setdefault("reading_archive_integration", {})
                if isinstance(state, dict):
                    last_album = state.get("last_album")
                    if isinstance(last_album, dict) and str(last_album.get("id") or last_album.get("album_id") or "") == album_id:
                        last_album["user_liked_tags"] = liked_tags
                        last_album["user_disliked_tags"] = disliked_tags
                        last_album["user_tags_updated_ts"] = time.time()
                        if target is None:
                            target = last_album
                if target is None:
                    return self._error("没有找到这条资料归档记录")
                updater = getattr(self.plugin, "_update_reading_archive_preference_profile", None)
                if callable(updater):
                    updater(target)
                self._mark_bookshelf_data_changed()
                self.plugin._save_data_sync(sections={"bookshelf_items"})
                data = deepcopy(self.plugin.data)
            return self._ok({"bookshelf": await self._bookshelf_summary(data, unlocked=True, access_token=access_token)})
        except Exception as exc:
            logger.error(f"保存资料归档标签失败: {exc}", exc_info=True)
            return self._exception_error(str(exc))

    def _resolve_bookshelf_data_file(self, value: Any) -> Path | None:
        path_text = _path_text(value, 1000)
        if not path_text:
            return None
        data_root = Path(str(getattr(self.plugin, "data_dir", ""))).resolve()
        try:
            raw_path = Path(path_text)
            path = raw_path.resolve() if raw_path.is_absolute() else (data_root / raw_path).resolve()
            path.relative_to(data_root)
            if path.exists() and path.is_file():
                return path
        except Exception:
            return None
        return None

    async def update_bookshelf_item_comments(self) -> dict[str, Any]:
        payload = await _page_api_host.request.get_json(silent=True) or {}
        access_token = (self._bookshelf_request_token(payload))
        if not self._bookshelf_access_token_valid(access_token):
            return self._error(self._bookshelf_access_error()["error"])
        album_id = self._single_line(payload.get("album_id") or payload.get("id"), 32)
        if not album_id:
            return self._error("缺少 album_id")
        try:
            async with self.plugin._data_lock:
                items = self.plugin.data.get("bookshelf_items")
                if not isinstance(items, list):
                    return self._error("夹层记录结构异常，未读取或覆盖原数据")
                target = next(
                    (
                        item
                        for item in items
                        if self._is_bookshelf_archive_item(item)
                        and self._bookshelf_album_id(item) == album_id
                    ),
                    None,
                )
                if target is None:
                    state = self.plugin.data.get("reading_archive_integration") if isinstance(self.plugin.data.get("reading_archive_integration"), dict) else {}
                    last_album = state.get("last_album") if isinstance(state.get("last_album"), dict) else None
                    if last_album and str(last_album.get("id") or last_album.get("album_id") or "") == album_id:
                        target = last_album
                if target is None:
                    return self._error("没有找到这条资料归档记录")
                target_snapshot = deepcopy(target)
            cover_path, page_paths, sampled_pages = self._archive_item_comment_sample(target_snapshot)
            if not page_paths:
                return self._error("没有找到可用于重读的本地图片")
            vision = getattr(self.plugin, "_call_reading_archive_vision", None)
            if not callable(vision):
                return self._error("当前插件版本不支持让 Bot 重读")
            vision_result = await vision(cover_path, target_snapshot, page_paths=page_paths, sampled_pages=sampled_pages)
            if not isinstance(vision_result, dict) or not (vision_result.get("impression") or vision_result.get("page_comments")):
                return self._error("这次没有生成新的读后感或批注")
            updates: dict[str, Any] = {
                "comments_updated_ts": time.time(),
                "sampled_pages": sampled_pages,
            }
            impression = self._single_line(vision_result.get("impression"), 600)
            if impression:
                updates["impression"] = impression
                updates["reading_impression"] = impression
            rating = self._int(vision_result.get("rating"))
            if 1 <= rating <= 10:
                updates["rating"] = rating
            rating_reason = self._single_line(vision_result.get("rating_reason"), 160)
            if rating_reason:
                updates["rating_reason"] = rating_reason
            preference_tags = self._normalize_bookshelf_tag_list(vision_result.get("preference_tags"))
            if preference_tags:
                updates["preference_tags"] = preference_tags
            page_comments = vision_result.get("page_comments") if isinstance(vision_result.get("page_comments"), list) else []
            normalized_comments: list[dict[str, Any]] = []
            for comment in page_comments[:8]:
                normalized = self._normalize_bookshelf_page_comment(comment, limit=80)
                if normalized:
                    normalized_comments.append(normalized)
            if normalized_comments:
                existing_comments = target_snapshot.get("page_comments") if isinstance(target_snapshot.get("page_comments"), list) else []
                previous_comments = (
                    target_snapshot.get("page_comments_previous")
                    if isinstance(target_snapshot.get("page_comments_previous"), list)
                    else []
                )
                updates["page_comments"] = self._merge_bookshelf_page_comments(
                    existing_comments,
                    normalized_comments,
                    previous_comments,
                    limit=24,
                )
                updates["page_comments_previous"] = existing_comments[:12]
            async with self.plugin._data_lock:
                items = self.plugin.data.get("bookshelf_items")
                if not isinstance(items, list):
                    return self._error("夹层记录结构异常，未写入批注")
                written = False
                for item in items:
                    if not self._is_bookshelf_archive_item(item):
                        continue
                    if self._bookshelf_album_id(item) == album_id:
                        item.update(updates)
                        target = item
                        written = True
                        break
                state = self.plugin.data.setdefault("reading_archive_integration", {})
                if isinstance(state, dict):
                    last_album = state.get("last_album")
                    if isinstance(last_album, dict) and str(last_album.get("id") or last_album.get("album_id") or "") == album_id:
                        last_album.update(updates)
                        if not written:
                            target = last_album
                            written = True
                if not written:
                    return self._error("没有找到可写回的资料归档记录")
                updater = getattr(self.plugin, "_update_reading_archive_preference_profile", None)
                if callable(updater):
                    updater(target)
                self._mark_bookshelf_data_changed()
                self.plugin._save_data_sync(sections={"bookshelf_items"})
                data = deepcopy(self.plugin.data)
            return self._ok({"message": "Bot 已重新读过并更新读后感", "bookshelf": await self._bookshelf_summary(data, unlocked=True, access_token=access_token)})
        except Exception as exc:
            logger.error(f"更新资料归档批注失败: {exc}", exc_info=True)
            return self._exception_error(str(exc))

    def _reading_archive_summary(self, data: dict[str, Any]) -> dict[str, Any]:
        # The public package exposes no external or page-level reading source.
        state = {}
        album = {}
        available = False
        return {
            "enabled": bool(available and getattr(self.plugin, "enable_reading_archive_integration", False)),
            "boredom_read_enabled": bool(
                available and getattr(self.plugin, "enable_reading_archive_boredom_read", False)
            ),
            "ask_recommendation_enabled": bool(available and getattr(self.plugin, "enable_reading_archive_ask_recommendation", False)),
            "available": available,
            "last_read_at": "",
            "last_status": "disabled",
            "last_keyword": "",
            "last_album": {
                "id": self._single_line(album.get("id"), 32),
                "title": self._single_line(album.get("title"), 100),
                "impression": self._single_line(album.get("impression"), 160),
                "rating": self._int(album.get("rating")),
                "user_rating": self._int(album.get("user_rating")),
            },
        }

    def _bookshelf_cover_url(
        self,
        album_id: str,
        item: dict[str, Any],
        page_items: list[dict[str, Any]],
        data_root: Path,
        *,
        access_token: str = "",
    ) -> str:
        cover_path = _path_text(item.get("cover_path"), 1000)
        if cover_path:
            return self._bookshelf_image_url(
                album_id,
                data_root=data_root,
                cover=True,
                path_value=cover_path,
                access_token=access_token,
            )
        first_page = page_items[0] if page_items else {}
        if isinstance(first_page, dict):
            return self._single_line(first_page.get("src"), 500)
        return ""

    async def _bookshelf_summary(self, data: dict[str, Any], *, unlocked: bool, access_token: str = "") -> dict[str, Any]:
        if unlocked and False:
            recoverer = getattr(self.plugin, "_recover_bookshelf_items_from_local_pages_inplace", None)
            if callable(recoverer):
                try:
                    recoverer(data)
                except Exception as exc:
                    logger.debug("夹层响应内本地书页恢复失败: %s", self._single_line(exc, 160))
        projects = data.get("creative_projects") if isinstance(data.get("creative_projects"), list) else []
        diaries = self._bookshelf_diary_entries(data.get("bot_diaries"))
        shelf_items = data.get("bookshelf_items") if isinstance(data.get("bookshelf_items"), list) else []
        archive_state = data.get("reading_archive_integration") if isinstance(data.get("reading_archive_integration"), dict) else {}
        deleted_archive_ids = self._bookshelf_deleted_album_ids(archive_state)
        archive_items = [
            item
            for item in shelf_items
            if self._is_bookshelf_archive_item(item)
            and not self._is_deleted_bookshelf_archive_item(item, archive_state)
        ]
        secret_state = data.get("bookshelf_secret") if isinstance(data.get("bookshelf_secret"), dict) else {}
        reason_sanitizer = getattr(self.plugin, "_sanitize_bookshelf_password_reason", None)
        password_hint = ""
        if callable(reason_sanitizer):
            try:
                password_hint = reason_sanitizer(secret_state.get("reason"))
            except Exception:
                password_hint = ""
        else:
            password_hint = self._single_line(secret_state.get("reason"), 80)
        if not password_hint:
            password_hint = "提示会在通过“陪伴 输出夹层密码”生成后显示。"
        last_album = {}
        last_album_id = self._bookshelf_album_id(last_album)
        if (
            last_album
            and last_album_id
            and not self._is_deleted_bookshelf_archive_item(
                {**last_album, "type": last_album.get("type") or "archive_item"},
                archive_state,
            )
            and not any(self._bookshelf_album_id(item) == last_album_id for item in archive_items)
        ):
            archive_items.append(
                {
                    "type": "archive_item",
                    "title": last_album.get("title"),
                    "album_id": last_album_id,
                    "description": last_album.get("description") or last_album.get("intro") or last_album.get("summary"),
                    "keyword": last_album.get("keyword"),
                    "author": last_album.get("author"),
                    "tags": last_album.get("tags"),
                    "photo_count": last_album.get("photo_count"),
                    "impression": last_album.get("impression"),
                    "reading_impression": last_album.get("reading_impression") or last_album.get("impression"),
                    "vision": last_album.get("vision"),
                    "rating": last_album.get("rating"),
                    "rating_reason": last_album.get("rating_reason"),
                    "user_rating": last_album.get("user_rating"),
                    "user_rating_reason": last_album.get("user_rating_reason"),
                    "user_rated_ts": last_album.get("user_rated_ts"),
                    "preference_tags": last_album.get("preference_tags") if isinstance(last_album.get("preference_tags"), list) else [],
                    "user_liked_tags": last_album.get("user_liked_tags") if isinstance(last_album.get("user_liked_tags"), list) else [],
                    "user_disliked_tags": last_album.get("user_disliked_tags") if isinstance(last_album.get("user_disliked_tags"), list) else [],
                    "user_tags_updated_ts": last_album.get("user_tags_updated_ts"),
                    "page_comments": last_album.get("page_comments") if isinstance(last_album.get("page_comments"), list) else [],
                    "image_count": last_album.get("image_count"),
                    "pages": last_album.get("pages") if isinstance(last_album.get("pages"), list) else [],
                    "sampled_pages": last_album.get("sampled_pages") if isinstance(last_album.get("sampled_pages"), list) else [],
                    "created_ts": last_album.get("created_ts"),
                }
            )
        data_root = Path(str(getattr(self.plugin, "data_dir", ""))).resolve()
        covers_root = data_root / "reading_archive_covers"
        if unlocked and False:
            pages_root = data_root / "bookshelf_pages"
            known_archive_ids = {
                self._bookshelf_album_id(item)
                for item in archive_items
                if self._bookshelf_album_id(item)
            }
            preference_history = []
            profile = archive_state.get("preference_profile") if isinstance(archive_state.get("preference_profile"), dict) else {}
            if isinstance(profile.get("history"), list):
                preference_history = [item for item in profile.get("history", []) if isinstance(item, dict)]
            history_by_album: dict[str, dict[str, Any]] = {}
            for item in preference_history:
                album_id = self._single_line(item.get("album_id") or item.get("id"), 80)
                if album_id:
                    history_by_album[album_id] = {**history_by_album.get(album_id, {}), **item}
            try:
                orphan_dirs = [
                    path
                    for path in pages_root.iterdir()
                    if path.is_dir()
                    and self._single_line(path.name, 80)
                    and self._single_line(path.name, 80) not in known_archive_ids
                    and self._single_line(path.name, 80) not in deleted_archive_ids
                ] if pages_root.exists() else []
            except Exception:
                orphan_dirs = []
            for path in orphan_dirs:
                album_id = self._single_line(path.name, 80)
                try:
                    page_files = sorted(
                        file
                        for file in path.iterdir()
                        if file.is_file() and file.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".gif"}
                    )
                except Exception as exc:
                    logger.debug(
                        "跳过无法读取的夹层目录: album=%s error=%s",
                        album_id,
                        self._single_line(exc, 120),
                    )
                    continue
                if not album_id or not page_files:
                    continue
                meta = history_by_album.get(album_id, {})
                created_ts = self._float(meta.get("created_ts")) or max((file.stat().st_mtime for file in page_files), default=0.0)
                cover_path = covers_root / f"{album_id}.jpg"
                archive_items.append(
                    {
                        "type": "archive_item",
                        "album_id": album_id,
                        "title": meta.get("title") or f"资料归档 {album_id}",
                        "description": meta.get("reason") or "",
                        "tags": meta.get("terms") if isinstance(meta.get("terms"), list) else [],
                        "rating": meta.get("bot_rating") or meta.get("rating"),
                        "user_rating": meta.get("user_rating"),
                        "rating_reason": meta.get("reason") or "",
                        "user_rating_reason": meta.get("reason") or "",
                        "cover_path": str(cover_path) if cover_path.exists() else "",
                        "pages": [
                            {
                                "index": index + 1,
                                "path": str(file),
                                "name": file.name,
                            }
                            for index, file in enumerate(page_files)
                        ],
                        "image_count": len(page_files),
                        "created_ts": created_ts,
                        "source": "bookshelf_orphan_recovered",
                        "locked": True,
                    }
                )
        public_books = []
        browsing_entries = self._browsing_history_entries(data)
        for item in [project for project in projects if isinstance(project, dict)][-12:]:
            chunks = item.get("draft_chunks") if isinstance(item.get("draft_chunks"), list) else []
            full_text = "\n\n".join(
                self._single_line(chunk.get("text"), 2000)
                for chunk in chunks
                if isinstance(chunk, dict) and self._single_line(chunk.get("text"), 2000)
            )
            chunk_entries = [
                {
                    "index": index + 1,
                    "text": self._single_line(chunk.get("text"), 2000),
                    "created": self.plugin._format_timestamp_elapsed(chunk.get("created_ts", 0) or chunk.get("created_at", 0)),
                }
                for index, chunk in enumerate(chunks)
                if isinstance(chunk, dict) and self._single_line(chunk.get("text"), 2000)
            ]
            status = self._single_line(item.get("status"), 24)
            progress = f"{self._int(item.get('current_chars'))}/{self._int(item.get('target_chars')) or '-'} 字"
            public_books.append(
                {
                    "id": self._single_line(item.get("id"), 32) or f"creative-{len(public_books)}",
                    "kind": "creative",
                    "category": self._single_line(item.get("work_type"), 30) or "创作",
                    "work_type": self._single_line(item.get("work_type"), 30) or "短篇小说",
                    "title": self._single_line(item.get("title"), 60) or "未定标题",
                    "intro": self._single_line(item.get("premise"), 240) or "这本书还没整理出简介。",
                    "status": status,
                    "tone": self._single_line(item.get("tone"), 40),
                    "point_of_view": self._single_line(item.get("point_of_view"), 40) or "第三人称有限视角",
                    "progress": progress,
                    "content": full_text or self._single_line(chunks[-1].get("text") if chunks else "", 2000) or "这本书还没有正文。",
                    "chunks": chunk_entries,
                    "outline_count": len(item.get("outline") or []) if isinstance(item.get("outline"), list) else 0,
                    "character_count": len(item.get("characters") or []) if isinstance(item.get("characters"), list) else 0,
                    "review_count": len(item.get("quality_reviews") or []) if isinstance(item.get("quality_reviews"), list) else 0,
                    "manual_edit_count": len(item.get("manual_edits") or []) if isinstance(item.get("manual_edits"), list) else 0,
                    "has_story_bible": bool(item.get("story_bible")) if isinstance(item.get("story_bible"), dict) else False,
                    "last_manual_edit_summary": self._single_line(item.get("last_manual_edit_summary"), 120),
                    "created": self.plugin._format_timestamp_elapsed(item.get("created_at", 0)),
                    "cover_src": self._creative_project_cover_url(item),
                    "cover_status": self._single_line(item.get("cover_generation_status"), 24),
                }
            )
        if browsing_entries:
            latest = browsing_entries[-1]
            public_books.append(
                {
                    "id": "browsing-history-main",
                    "kind": "browsing",
                    "category": "浏览记录",
                    "title": "浏览记录",
                    "intro": f"这里收着 {len(browsing_entries)} 条新闻阅读和主动搜索记录。打开后可以选择记录。",
                    "content": self._single_line(latest.get("content"), 2000) or self._single_line(latest.get("intro"), 1200),
                    "entries": browsing_entries,
                    "created": self._single_line(latest.get("generated_at") or latest.get("date"), 32),
                    "progress": f"{len(browsing_entries)} 条记录",
                    "tags": ["新闻阅读", "主动搜索"],
                }
            )
        locked_count = (1 if diaries else 0) + len(archive_items)
        secret_books: list[dict[str, Any]] = []
        if unlocked:
            diary_entries = []
            for item in [entry for entry in diaries if isinstance(entry, dict)][-60:]:
                body = self.plugin._polish_diary_text(item.get("body"), field="body")
                summary = self.plugin._polish_diary_text(item.get("summary"), field="summary")
                share_seed = self.plugin._polish_diary_text(item.get("share_seed"), field="share")
                content = body or "\n\n".join(part for part in (summary, share_seed) if part)
                diary_entries.append(
                    {
                        "entry_key": self._single_line(item.get("entry_key"), 80),
                        "date": self._single_line(item.get("date"), 24) or "某天",
                        "generated_at": self._single_line(item.get("generated_at"), 32),
                        "title": f"{self._single_line(item.get('date'), 24) or '某天'}",
                        "intro": summary or "这一天没有留下摘要。",
                        "content": content or "这一天的日记暂时没有写出正文。",
                        "tags": [self._single_line(tag, 24) for tag in item.get("tags", [])[:8] if self._single_line(tag, 24)]
                        if isinstance(item.get("tags"), list)
                        else [],
                    }
                )
            if diary_entries:
                secret_books.append(
                    {
                        "id": "diary-main",
                        "kind": "diary",
                        "category": "日记",
                        "title": "日记本",
                        "intro": f"这里收着 {len(diary_entries)} 天的日记。打开后可以选择日期。",
                        "content": diary_entries[-1].get("content") or "这本日记暂时没有可读内容。",
                        "entries": diary_entries,
                        "created": diary_entries[-1].get("generated_at", ""),
                    }
            )
            recent_archive_items = sorted(
                archive_items,
                key=lambda item: self._float(item.get("created_ts") or item.get("created_at") or item.get("ts")),
                reverse=True,
            )[:80]
            for item in recent_archive_items:
                album_id = self._bookshelf_album_id(item, limit=32)
                pages = item.get("pages") if isinstance(item.get("pages"), list) else []
                reading_impression = self._single_line(item.get("reading_impression") or item.get("impression"), 1000)
                vision_impression = self._single_line(item.get("vision"), 1000)
                show_vision_impression = bool(
                    vision_impression
                    and (
                        not reading_impression
                        or _text_similarity(vision_impression, reading_impression) < 0.72
                    )
                )
                bot_rating = self._int(item.get("rating"))
                user_rating = self._int(item.get("user_rating"))
                rating_reason = self._single_line(item.get("rating_reason"), 180)
                user_rating_reason = self._single_line(item.get("user_rating_reason"), 180)
                album_description = self._single_line(
                    item.get("description")
                    or item.get("intro")
                    or item.get("summary")
                    or item.get("desc"),
                    600,
                )
                if not album_description:
                    detail_parts = []
                    author_text = self._single_line(item.get("author"), 40)
                    photo_count = self._int(item.get("photo_count")) or self._int(item.get("image_count"))
                    tag_text = "、".join(
                        self._single_line(tag, 24)
                        for tag in (item.get("tags") if isinstance(item.get("tags"), list) else [])[:6]
                        if self._single_line(tag, 24)
                    )
                    if author_text:
                        detail_parts.append(f"作者：{author_text}")
                    if photo_count:
                        detail_parts.append(f"页数：{photo_count}")
                    if tag_text:
                        detail_parts.append(f"标签：{tag_text}")
                    album_description = "；".join(detail_parts) or "这条阅读记录暂时没有整理出明确简介。"
                page_comment_map: dict[int, list[str]] = {}
                raw_comments = self._merge_bookshelf_page_comments(
                    item.get("page_comments") if isinstance(item.get("page_comments"), list) else [],
                    item.get("page_comments_previous") if isinstance(item.get("page_comments_previous"), list) else [],
                    limit=32,
                )
                for comment_item in raw_comments:
                    if not isinstance(comment_item, dict):
                        continue
                    page_no = self._int(comment_item.get("page"))
                    comment_text = self._single_line(comment_item.get("comment"), 100)
                    if page_no > 0 and comment_text:
                        page_comments = page_comment_map.setdefault(page_no, [])
                        if comment_text not in page_comments:
                            page_comments.append(comment_text)
                reading_progress_page = max(0, self._int(item.get("reading_progress_page")))
                reading_progress_total = max(0, self._int(item.get("reading_progress_total"))) or len(pages)
                reading_progress_updated_at = self._float(item.get("reading_progress_updated_at"))
                reading_bookmark = self._single_line(item.get("reading_bookmark"), 120)
                reading_completed_at = self._float(item.get("reading_completed_at"))
                page_items = []
                for page in pages:
                    if not isinstance(page, dict):
                        continue
                    index = self._int(page.get("index"))
                    if index <= 0:
                        continue
                    page_src = self._bookshelf_image_url(
                        album_id,
                        data_root=data_root,
                        page_index=index,
                        path_value=page.get("path"),
                        access_token=access_token,
                    )
                    page_items.append(
                        {
                            "index": index,
                            "src": page_src,
                            "comment": "\n".join(page_comment_map.get(index, [])),
                        }
                    )
                cover_src = ""
                if album_id:
                    cover_src = self._bookshelf_cover_url(album_id, item, page_items, data_root, access_token=access_token)
                secret_books.append(
                    {
                        "id": f"archive-{album_id or len(secret_books)}",
                        "kind": "archive_item",
                        "category": "资料归档",
                        "album_id": album_id,
                        "title": self._single_line(item.get("title"), 100) or "未命名阅读记录",
                        "intro": self._single_line(album_description, 600),
                        "reading_impression": reading_impression or vision_impression,
                        "rating": bot_rating,
                        "rating_reason": rating_reason,
                        "user_rating": user_rating,
                        "user_rating_reason": user_rating_reason,
                        "user_rated": bool(user_rating),
                        "author": self._single_line(item.get("author"), 40),
                        "progress": (
                            "已读完"
                            if reading_completed_at
                            else f"读至 {min(max(1, reading_progress_page), max(1, reading_progress_total))}/{max(1, reading_progress_total)} 页"
                            if reading_progress_page > 0
                            else f"{len(page_items) or self._int(item.get('image_count')) or self._int(item.get('photo_count'))} 页"
                        ),
                        "created": self.plugin._format_timestamp_elapsed(item.get("created_ts", 0)),
                        "content": "\n\n".join(
                            part
                            for part in (
                                f"读后感：{reading_impression}" if reading_impression else "",
                                f"Bot 评分：{bot_rating}/10" if bot_rating else "",
                                f"用户评分：{user_rating}/10" if user_rating else "",
                                f"评分理由：{user_rating_reason or rating_reason}" if (user_rating_reason or rating_reason) else "",
                                f"画面记录：{vision_impression}" if show_vision_impression else "",
                                f"关键词：{self._single_line(item.get('keyword'), 80)}" if self._single_line(item.get("keyword"), 80) else "",
                            )
                            if part
                        ) or "这本只留下了一点很含糊的阅读印象。",
                        "tags": [self._single_line(tag, 24) for tag in item.get("tags", [])[:8] if self._single_line(tag, 24)]
                        if isinstance(item.get("tags"), list)
                        else [],
                        "preference_tags": [
                            self._single_line(tag, 24)
                            for tag in (item.get("preference_tags") if isinstance(item.get("preference_tags"), list) else [])[:8]
                            if self._single_line(tag, 24)
                        ],
                        "user_liked_tags": [
                            self._single_line(tag, 24)
                            for tag in (item.get("user_liked_tags") if isinstance(item.get("user_liked_tags"), list) else [])[:8]
                            if self._single_line(tag, 24)
                        ],
                        "user_disliked_tags": [
                            self._single_line(tag, 24)
                            for tag in (item.get("user_disliked_tags") if isinstance(item.get("user_disliked_tags"), list) else [])[:8]
                            if self._single_line(tag, 24)
                        ],
                        "user_tags_updated": bool(item.get("user_tags_updated_ts")),
                        "cover_src": cover_src,
                        "pages": page_items,
                        "reading_progress_page": reading_progress_page,
                        "reading_progress_total": reading_progress_total,
                        "reading_progress_updated_at": reading_progress_updated_at,
                        "reading_bookmark": reading_bookmark,
                        "reading_completed_at": reading_completed_at,
                        "page_comment_count": sum(len(comments) for comments in page_comment_map.values()),
                        "page_comments": [
                            {"page": page, "comment": comment}
                            for page, comments in sorted(page_comment_map.items())
                            for comment in comments
                        ],
                    }
                )
        return {
            "unlocked": unlocked,
            "access_token": access_token if unlocked and self._bookshelf_access_token_valid(access_token) else "",
            "access_expires_in": int(max(0, self._bookshelf_access_token_expires_at(access_token) - time.time()))
            if unlocked and access_token
            else 0,
            "access_expires_at": int(self._bookshelf_access_token_expires_at(access_token))
            if unlocked and access_token
            else 0,
            "public_count": len(public_books),
            "secret_count": locked_count,
            "diary_count": 1 if diaries else 0,
            "archive_item_count": len(archive_items),
            "reading_now_count": sum(1 for item in archive_items if self._int(item.get("reading_progress_page")) > 0 and not self._float(item.get("reading_completed_at"))),
            "memo_notes": self._memo_notes_payload(data),
            "password_hint": password_hint,
            "public_books": public_books,
            "secret_books": secret_books,
        }

