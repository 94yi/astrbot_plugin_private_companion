# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import hashlib
import html
import inspect
import json
import os
import random
import re
import shutil
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from astrbot.api.event import AstrMessageEvent
from astrbot.api.event import MessageChain
from astrbot.core.platform.message_session import MessageSession
from astrbot.core.utils.astrbot_path import get_astrbot_data_path
try:
    from astrbot.api.message_components import At, Plain
except ImportError:
    from astrbot.api.message_components import At, Plain

from .helpers import (
    _missing_optional_model_dependency,
    _now_ts,
    _path_text,
    _photo_group_request_matches,
    _redact_outbound_secrets,
    _safe_float,
    _safe_int,
    _single_line,
    _strip_internal_message_blocks,
)
from .memo_notes import apply_memo_note_action, memo_note_sort_key, normalize_memo_note
from .persona_config import runtime_persona_setting
from .conversation_prompt_section import (
    PromptRenderMode,
    PromptSection,
    prompt_section,
    render_prompt_sections,
)
from .owned_reaction_asset_catalog import OwnedReactionAssetCatalog
from .qzone_selection import (
    QzoneViewTarget,
    classify_qzone_view_owner,
    normalize_qzone_uin,
    normalize_qzone_view_target_scope,
    parse_qzone_post_selection,
    qzone_view_owner_is_pronoun_safe,
    resolve_qzone_view_target,
)
from .reaction_expression import (
    append_reaction_expression_outcome,
    classify_reaction_expression_feedback,
    ensure_reaction_expression_state,
    evaluate_reaction_expression_gate,
    reaction_expression_explicit_opt_out,
    reaction_expression_explicit_request,
    reaction_expression_auto_disabled,
    reaction_expression_high_frequency,
    reaction_expression_normalize_probability,
    sync_reaction_expression_auto_preference,
    normalize_reaction_expression_intent,
    reaction_expression_effective_probability,
    reaction_expression_image_key,
    reaction_expression_image_keys,
    reaction_expression_reservation_owned,
    reaction_expression_selection_preferences,
    reaction_expression_scope_state,
    record_reaction_expression_feedback,
    record_reaction_expression_sent,
    release_reaction_expression_image,
    release_reaction_expression_reservation,
    reserve_reaction_expression_image,
    reserve_reaction_expression_intent,
)
from .reaction_asset_library import ReactionAssetLibrary, get_reaction_asset_library
from .logging_util import get_module_logger
from .llm_tool_actions_interaction_relay import LlmToolActionsInteractionRelayMixin
from .llm_tool_actions_reaction_exec import LlmToolActionsReactionExecMixin
from .llm_tool_actions_reaction_search import LlmToolActionsReactionSearchMixin
from .llm_tool_actions_reaction_core import LlmToolActionsReactionCoreMixin
from .llm_tool_actions_photo_generate import LlmToolActionsPhotoGenerateMixin
from .llm_tool_actions_photo_prompt import LlmToolActionsPhotoPromptMixin
from .llm_tool_actions_qzone import LlmToolActionsQzoneMixin
from .interaction_tool_contract import InteractionQuery
from .interaction_query_orchestrator import execute_interaction_query
from .photo_nai_params import merge_user_photo_nai_params, recent_cached_photo_nai_params

from .llm_tool_actions_shared import (
    PHOTO_TOOL_SILENT_SENTINEL,
    _render_tool_prompt_section_labeled,
    _render_tool_prompt_section_labeled_inline,
    logger,
)

class LlmToolActionsMixin(LlmToolActionsQzoneMixin, LlmToolActionsPhotoPromptMixin, LlmToolActionsPhotoGenerateMixin, LlmToolActionsReactionCoreMixin, LlmToolActionsReactionSearchMixin, LlmToolActionsReactionExecMixin, LlmToolActionsInteractionRelayMixin):
    """Implementation bodies for LLM tools registered in main.py."""

    def _reaction_asset_library(self):
        return get_reaction_asset_library(self)

    def _creative_work_tool_prompt_section(self) -> PromptSection | None:
        if not self.enabled or not runtime_persona_setting(self, 'enable_creative_work_read_guard', True):
            return None
        body = """当用户询问能否看到资料柜/书架、资料柜是否为空、里面有什么或有几篇作品时，必须先调用 `pc_view_creative_work`，action=list。list 返回的是插件当前真实保存的资料柜库存；主要用户还会得到日记、资料归档和便签的分类数量。
当用户询问你自己的某篇创作写了什么、某一部分/片段的内容、你如何看待这篇创作、为什么这样写，或要求你结合原文讲讲时，必须先调用 `pc_view_creative_work` 读取真实创作，再依据工具结果回答。
- 按标题读取：action=get，selector 传用户提到的作品标题；只有用户明确指定“第 N 部分/第 N 段”时才传 part=N。
- 不确定有哪些作品或用户泛问“最近写了什么”：先 action=list；拿到准确标题后，如需正文再 action=get。
- 讨论整篇作品时 part=0，工具会按顺序返回预算内的正文；结果若 truncated=true，可继续用 next_part 读取。
- 工具返回 success 前，不要说“我看过了/我刚检查了”；也不要先发送“我先去看看”等准备动作。直接调用工具，取得结果后一次性自然回答。
- 回复必须直接说读取结果，不要用“（翻了翻资料柜）”“（挠挠头）”之类括号动作代替结果。
- 不得把被动提示中的短片段、长期记忆或聊天印象冒充完整原文；找不到作品或部分时如实说明，并可根据 candidates 请用户进一步说明。
- 这是只读工具，不能修改、续写或删除创作。
- 用户只是让你讲一个、编一个或说一个新故事，或泛泛地让你讲“你的故事”时，不是在读取资料柜作品，不要调用此工具；只有用户明确提到你写过的故事、某篇作品、资料柜内容、原文或具体章节时才读取。
- 用户要求查看配置文件、数据文件、日志、源码、代码、脚本、插件目录或配置项时，不是在读取资料柜作品；即使文件或配置名称中包含“创作”“作品”等词，也不要调用此工具，不要把技术文件问答改写成创作原文读取失败。
""".strip()
        return prompt_section(
            key="tools.creative_work",
            title="资料柜与自己的创作读取工具",
            source="tools",
            content=body,
        )

    def _creative_work_tool_instruction(self) -> str:
        return _render_tool_prompt_section_labeled(
            self._creative_work_tool_prompt_section()
        )

    @staticmethod
    def _creative_work_inventory_query_matches(text: Any) -> bool:
        normalized = _single_line(text, 260)
        if not normalized or any(
            token in normalized
            for token in ("资料柜密码", "书架密码", "夹层密码", "抽屉密码", "输出密码", "重置密码")
        ):
            return False
        shelf_terms = ("资料柜", "书架", "作品柜", "创作柜")
        query_terms = (
            "能看到", "看得到", "能看见", "可以看到", "能不能看", "能读到",
            "看看", "看一下", "查一下", "查查", "查询", "检索", "列一下", "列出",
            "里面有什么", "有什么", "有哪些",
            "有几", "多少", "空不空", "是不是空", "还是空", "空的", "现在有",
        )
        return any(token in normalized for token in shelf_terms) and any(
            token in normalized for token in query_terms
        )

    def _creative_work_query_instruction_matches(self, text: Any) -> bool:
        normalized = _single_line(text, 260)
        if not normalized:
            return False
        technical_file_terms = (
            "配置文件", "数据文件", "日志文件", "代码文件", "项目文件", "插件文件",
            "配置项", "配置键", "配置目录", "插件目录", "文件目录", "文件夹",
            "源码", "源代码", "代码", "脚本", "仓库", "数据库", "报错日志",
        )
        technical_extensions = re.search(
            r"(?:^|[\\/\s])[^\\/\s]{1,100}\.(?:json|ya?ml|toml|ini|cfg|conf|env|py|js|ts|tsx|jsx|md|txt|log|db|sqlite3?)\b",
            normalized,
            flags=re.IGNORECASE,
        )
        if any(token in normalized for token in technical_file_terms) or technical_extensions:
            return False
        if self._creative_work_inventory_query_matches(normalized):
            return True

        # “故事”也常用于临时讲述或现场创作。只有句子同时指向一篇已经
        # 存在的作品时，才把它当作资料柜读取请求。
        if "故事" in normalized:
            existing_story_anchors = (
                "你写的", "你写过的", "你以前写的", "你之前写的", "你最近写的",
                "你创作的", "你创作过的", "自己写的", "自己创作的",
                "那篇", "这篇", "哪篇", "那部", "这部", "哪部",
                "那篇故事", "这篇故事", "哪篇故事", "那个故事", "这个故事",
                "上次的故事", "之前的故事", "资料柜里的故事", "书架里的故事",
                "故事原文", "故事正文", "故事全文", "故事片段", "故事章节",
                "故事的原文", "故事的正文", "故事的全文", "故事的片段", "故事的章节",
                "故事第", "故事写了什么", "故事写的什么", "写过什么故事",
                "写了什么故事", "创作过什么故事", "创作了什么故事",
            )
            has_existing_story_anchor = any(
                token in normalized for token in existing_story_anchors
            ) or bool(
                re.search(r"《[^》]{1,80}》", normalized)
                or re.search(r"故事.{0,12}第\s*[一二三四五六七八九十百零两\d]+\s*(?:部分|章|节|段)", normalized)
            )
            if not has_existing_story_anchor:
                return False
        work_terms = (
            "创作", "作品", "写作", "札记", "随笔", "散文", "小说", "故事",
            "诗", "歌词", "剧本", "手稿", "草稿", "正文", "片段", "章节",
        )
        query_terms = (
            "讲讲", "说说", "看看", "看一下", "读", "回顾", "总结", "内容",
            "写了什么", "写过什么", "写的什么", "创作过什么",
            "怎么看", "看待", "觉得", "想法", "为什么",
            "第", "部分", "哪一段", "这一段", "那一段", "原文", "全文",
        )
        return any(token in normalized for token in work_terms) and any(
            token in normalized for token in query_terms
        )

    @staticmethod
    def _creative_work_tool_result_payload(tool_result: Any) -> dict[str, Any]:
        """Extract the plugin JSON from AstrBot's CallToolResult wrapper."""
        pending: list[Any] = [tool_result]
        seen: set[int] = set()
        while pending and len(seen) < 24:
            value = pending.pop(0)
            if value is None:
                continue
            marker = id(value)
            if marker in seen:
                continue
            seen.add(marker)
            if isinstance(value, dict):
                if "status" in value:
                    return dict(value)
                for key in (
                    "structuredContent", "structured_content", "result", "data", "content", "text",
                ):
                    if key in value:
                        pending.append(value.get(key))
                continue
            if isinstance(value, (list, tuple)):
                pending.extend(value)
                continue
            if isinstance(value, str):
                text = value.strip()
                if text.startswith("```"):
                    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE).strip()
                try:
                    parsed = json.loads(text)
                except Exception:
                    parsed = None
                if isinstance(parsed, dict):
                    if "status" in parsed:
                        return parsed
                    pending.append(parsed)
                continue
            for attr in (
                "structuredContent", "structured_content", "result", "data", "content", "text",
            ):
                try:
                    nested = getattr(value, attr, None)
                except Exception:
                    nested = None
                if nested is not None:
                    pending.append(nested)
        return {}

    @staticmethod
    def _record_creative_work_tool_result(
        event: AstrMessageEvent,
        tool: Any,
        tool_args: Any,
        tool_result: Any,
    ) -> bool:
        if _single_line(getattr(tool, "name", ""), 80) != "pc_view_creative_work":
            return False
        try:
            setattr(event, "private_companion_creative_work_tool_attempted", True)
            action = _single_line(
                (tool_args or {}).get("action") if isinstance(tool_args, dict) else "",
                20,
            ).lower() or "get"
            payload = LlmToolActionsMixin._creative_work_tool_result_payload(tool_result)
            success = bool(
                action in {"list", "get"}
                and _single_line(payload.get("status"), 24).lower() == "success"
                and not bool(getattr(tool_result, "isError", False))
            )
            setattr(event, "private_companion_creative_work_read_success", success)
            setattr(event, "private_companion_creative_work_tool_action", action)
            setattr(event, "private_companion_creative_work_tool_status", _single_line(payload.get("status"), 24))
            setattr(
                event,
                "private_companion_bookshelf_inventory_complete",
                bool(action == "list" and isinstance(payload.get("bookshelf"), dict)),
            )
        except Exception:
            pass
        return True

    @staticmethod
    def _strip_bookshelf_stage_directions(text: Any) -> str:
        raw = str(text or "").strip()
        if not raw:
            return ""
        action_terms = (
            "查", "看", "翻", "找", "确认", "检查", "扫", "数", "挠头", "挠挠头",
            "点头", "摇头", "眨眼", "歪头", "低头", "抬头", "叹气", "笑", "脸红",
            "不好意思", "认真", "仔细", "凑近", "摊手", "耸肩",
        )
        pattern = re.compile(r"(?:^|\n)\s*[（(]([^（）()\n]{1,80})[）)]\s*")

        def replace(match: re.Match[str]) -> str:
            content = match.group(1)
            return "\n" if any(token in content for token in action_terms) else match.group(0)

        cleaned = pattern.sub(replace, raw)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
        return cleaned

    @staticmethod
    def _bookshelf_requester_is_owner(event: AstrMessageEvent, plugin: Any) -> bool:
        try:
            requester = event.get_sender_id()
        except Exception:
            requester = ""
        identity_for_event = getattr(plugin, "_event_permission_identity_id", None)
        identity = getattr(plugin, "_permission_identity_id", None)
        if callable(identity_for_event):
            try:
                requester = identity_for_event(event)
            except Exception:
                requester = ""
        elif callable(identity):
            try:
                requester = identity(requester)
            except Exception:
                requester = ""
        checker = getattr(plugin, "_is_private_companion_owner_user_id", None)
        if not requester or not callable(checker):
            return False
        try:
            return bool(checker(requester))
        except Exception:
            return False

    def _bookshelf_inventory_snapshot(
        self,
        event: AstrMessageEvent,
        projects: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        source_projects = projects
        if source_projects is None:
            raw_projects = self.data.get("creative_projects") if isinstance(getattr(self, "data", None), dict) else []
            source_projects = list(raw_projects) if isinstance(raw_projects, list) else []
        eligible = self._creative_work_project_candidates(source_projects, "")
        snapshot: dict[str, Any] = {
            "scope": "public",
            "creative_count": len(eligible),
            "creative_projects": [
                self._creative_work_project_summary(project, index)
                for index, project in enumerate(eligible[-20:], start=max(1, len(eligible) - 19))
            ],
        }
        if not self._bookshelf_requester_is_owner(event, self):
            return snapshot

        raw_diaries = self.data.get("bot_diaries") if isinstance(self.data.get("bot_diaries"), list) else []
        diaries = [item for item in raw_diaries if isinstance(item, dict)]
        raw_shelf_items = self.data.get("bookshelf_items") if isinstance(self.data.get("bookshelf_items"), list) else []
        reading_items: list[dict[str, Any]] = []
        raw_notes = self.data.get("memo_notes") if isinstance(self.data.get("memo_notes"), list) else []
        notes = [note for note in (normalize_memo_note(item) for item in raw_notes) if note]
        snapshot.update(
            {
                "scope": "owner",
                "diary_count": len(diaries),
                "reading_archive_count": len(reading_items),
                "reading_archive_titles": [
                    _single_line(item.get("title"), 80) or "未命名阅读记录"
                    for item in reading_items[-8:]
                ],
                "memo_active_count": sum(1 for note in notes if note.get("status") == "active"),
                "memo_completed_count": sum(1 for note in notes if note.get("status") == "completed"),
            }
        )
        return snapshot

    def _format_bookshelf_inventory_reply(self, event: AstrMessageEvent) -> str:
        snapshot = self._bookshelf_inventory_snapshot(event)
        creative_projects = snapshot.get("creative_projects") if isinstance(snapshot.get("creative_projects"), list) else []
        titles = [
            _single_line(item.get("title"), 60)
            for item in creative_projects[-5:]
            if isinstance(item, dict) and _single_line(item.get("title"), 60)
        ]
        creative_count = _safe_int(snapshot.get("creative_count"), 0, 0)
        sections: list[str] = []
        if creative_count:
            title_text = f"，最近的是{'、'.join(f'《{title}》' for title in titles)}" if titles else ""
            sections.append(f"创作区有 {creative_count} 篇带正文的作品{title_text}")
        else:
            sections.append("创作区暂时没有带正文的作品")
        if snapshot.get("scope") == "owner":
            sections.extend(
                (
                    f"日记本有 {_safe_int(snapshot.get('diary_count'), 0, 0)} 天记录",
                    f"资料归档有 {_safe_int(snapshot.get('reading_archive_count'), 0, 0)} 条记录",
                    f"便签区有 {_safe_int(snapshot.get('memo_active_count'), 0, 0)} 张进行中便签",
                )
            )
        return "能看到。现在" + "；".join(sections) + "。"

    def _bookshelf_reply_conflicts_with_inventory(self, event: AstrMessageEvent, text: Any) -> bool:
        cleaned = _single_line(text, 500)
        if not cleaned:
            return True
        snapshot = self._bookshelf_inventory_snapshot(event)
        visible_count = _safe_int(snapshot.get("creative_count"), 0, 0)
        if snapshot.get("scope") == "owner":
            visible_count += _safe_int(snapshot.get("diary_count"), 0, 0)
            visible_count += _safe_int(snapshot.get("reading_archive_count"), 0, 0)
            visible_count += _safe_int(snapshot.get("memo_active_count"), 0, 0)
            visible_count += _safe_int(snapshot.get("memo_completed_count"), 0, 0)
        claims_empty = bool(
            re.search(
                r"(?:资料柜|书架)?[^。！？!?\n]{0,12}(?:还是|仍然|依旧|目前|现在)?"
                r"(?:空空的|是空的|空着|什么都没有|没有东西|没东西|没有内容)",
                cleaned,
            )
        )
        return visible_count > 0 and claims_empty

    def _guard_unread_creative_work_response(self, event: AstrMessageEvent, text: Any) -> str:
        raw = str(text or "")
        if not runtime_persona_setting(self, 'enable_creative_work_read_guard', True):
            return raw
        if not bool(getattr(event, "private_companion_creative_work_tool_required", False)):
            return raw
        inbound_text = str(getattr(event, "message_str", "") or "")
        inventory_query = self._creative_work_inventory_query_matches(inbound_text)
        cleaned = self._strip_bookshelf_stage_directions(raw) if inventory_query else raw.strip()
        read_success = bool(getattr(event, "private_companion_creative_work_read_success", False))
        inventory_complete = bool(getattr(event, "private_companion_bookshelf_inventory_complete", False))
        if read_success and cleaned and not (
            inventory_query
            and (
                not inventory_complete
                or self._bookshelf_reply_conflicts_with_inventory(event, cleaned)
            )
        ):
            return cleaned
        if inventory_query:
            logger.warning(
                "资料柜查询未形成可信正文，已按本地真实库存回答: attempted=%s status=%s inventory_complete=%s session=%s",
                bool(getattr(event, "private_companion_creative_work_tool_attempted", False)),
                _single_line(getattr(event, "private_companion_creative_work_tool_status", ""), 24) or "none",
                inventory_complete,
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            )
            return self._format_bookshelf_inventory_reply(event)
        if bool(getattr(event, "private_companion_creative_work_tool_attempted", False)):
            return "我这次没能实际读取到对应的创作原文，先不凭印象乱讲。你可以再告诉我准确标题或第几部分，我读到后再认真和你说。"
        logger.warning(
            "指定创作问答未实际调用读取工具，已阻止凭片段作答: session=%s",
            _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
        )
        return "我这次还没能实际读取到对应的创作原文，先不凭印象乱讲。你可以再告诉我准确标题或第几部分，我读到后再认真和你说。"

    @staticmethod
    def _plaintext_tool_call_from_object(value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        function = value.get("function")
        source = function if isinstance(function, dict) else value
        name = _single_line(source.get("name") or value.get("tool_name"), 80)
        # TODO: derive this allowlist from the @filter.llm_tool registrations in
        # main.py once AstrBot exposes a stable registry during decoration.
        known_names = {
            "pc_qzone_view_feed",
            "pc_qzone_publish_feed",
            "pc_generate_photo",
            "pc_send_current_media",
            "pc_find_reaction_image",
            "pc_manage_memo",
            "pc_manage_schedule",
            "pc_view_creative_work",
            "pc_get_group_id_by_name",
            "pc_get_user_id_by_name",
            "pc_query_relation_person",
            "pc_get_specified_group_members",
            "pc_query_wardrobe_detail",
            "pc_set_outfit_intent",
            "pc_query_interaction",
            "pc_relay_message",
            "pc_send_to_group",
            "pc_send_to_private_user",
            "pc_send_to_groups",
            "pc_send_to_private_users",
            "pc_schedule_group_relay",
            "future_task",
            "send_message_to_user",
        }
        if name not in known_names:
            return None
        parameters = source.get("parameters")
        if parameters is None:
            parameters = source.get("arguments")
        if parameters is None:
            parameters = source.get("args")
        if parameters is None:
            parameters = value.get("parameters", value.get("arguments", value.get("args", {})))
        if isinstance(parameters, str):
            try:
                parameters = json.loads(parameters)
            except Exception:
                return None
        if not isinstance(parameters, dict):
            return None
        return {"name": name, "parameters": dict(parameters)}

    @staticmethod
    def _creative_work_project_candidates(
        projects: list[dict[str, Any]],
        selector: Any,
    ) -> list[dict[str, Any]]:
        eligible = [
            item
            for item in projects
            if isinstance(item, dict)
            and str(item.get("status") or "") in {"drafting", "finished"}
            and isinstance(item.get("draft_chunks"), list)
            and any(
                isinstance(chunk, dict) and str(chunk.get("text") or "").strip()
                for chunk in item.get("draft_chunks", [])
            )
        ]
        value = _single_line(selector, 120)
        if not value:
            return eligible
        folded = value.casefold()
        exact = [
            item
            for item in eligible
            if folded
            in {
                _single_line(item.get("id"), 40).casefold(),
                _single_line(item.get("title"), 80).casefold(),
            }
        ]
        if exact:
            return exact
        contains = [
            item
            for item in eligible
            if folded in _single_line(item.get("title"), 80).casefold()
            or _single_line(item.get("title"), 80).casefold() in folded
        ]
        if contains:
            return contains
        number_match = re.fullmatch(r"(?:第\s*)?(\d+)(?:\s*(?:个|篇|项))?", value)
        if number_match:
            index = _safe_int(number_match.group(1), 0) - 1
            if 0 <= index < len(eligible):
                return [eligible[index]]
        return []

    @staticmethod
    def _creative_work_project_summary(project: dict[str, Any], index: int = 0) -> dict[str, Any]:
        chunks = project.get("draft_chunks") if isinstance(project.get("draft_chunks"), list) else []
        valid_chunks = [
            chunk
            for chunk in chunks
            if isinstance(chunk, dict) and str(chunk.get("text") or "").strip()
        ]
        return {
            "index": index,
            "id": _single_line(project.get("id"), 40),
            "title": _single_line(project.get("title"), 80) or "未定标题",
            "work_type": _single_line(project.get("work_type"), 40) or "文本作品",
            "status": _single_line(project.get("status"), 24),
            "part_count": len(valid_chunks),
            "current_chars": _safe_int(project.get("current_chars"), 0, 0),
        }

    async def _pc_view_creative_work_impl(
        self,
        event: AstrMessageEvent,
        *,
        action: str = "get",
        selector: str = "",
        part: int = 0,
        max_chars: int = 6000,
    ) -> str:
        try:
            is_private = bool(getattr(event, "is_private_chat", lambda: False)())
        except Exception:
            is_private = ":FriendMessage:" in str(getattr(event, "unified_msg_origin", "") or "")
        if not is_private:
            return json.dumps(
                {"status": "forbidden", "message": "创作正文只允许在私聊中读取。"},
                ensure_ascii=False,
            )

        normalized_action = _single_line(action, 20).lower() or "get"
        if normalized_action not in {"list", "get"}:
            return json.dumps(
                {"status": "invalid_action", "message": "action 仅支持 list/get。"},
                ensure_ascii=False,
            )
        async with self._data_lock:
            raw_projects = self.data.get("creative_projects")
            projects = list(raw_projects) if isinstance(raw_projects, list) else []
            eligible = self._creative_work_project_candidates(projects, "")
            if normalized_action == "list":
                summaries = [
                    self._creative_work_project_summary(project, index)
                    for index, project in enumerate(eligible, start=1)
                ]
                return json.dumps(
                    {
                        "status": "success",
                        "action": "list",
                        "count": len(summaries),
                        "projects": summaries[-20:],
                        "bookshelf": self._bookshelf_inventory_snapshot(event, projects),
                        "instruction": "直接依据这份真实库存回答，不要写查找动作，也不要把未列出的内容补成存在。",
                    },
                    ensure_ascii=False,
                )

            matches = self._creative_work_project_candidates(projects, selector)
            if not _single_line(selector, 120):
                matches = eligible[-1:] if eligible else []
            if not matches:
                candidates = [
                    self._creative_work_project_summary(project, index)
                    for index, project in enumerate(eligible[-10:], start=max(1, len(eligible) - 9))
                ]
                return json.dumps(
                    {
                        "status": "not_found",
                        "message": "没有找到对应的创作。",
                        "selector": _single_line(selector, 120),
                        "candidates": candidates,
                    },
                    ensure_ascii=False,
                )
            if len(matches) > 1:
                return json.dumps(
                    {
                        "status": "ambiguous",
                        "message": "匹配到多篇创作，请使用准确标题或 id 再读取。",
                        "candidates": [
                            self._creative_work_project_summary(project, index)
                            for index, project in enumerate(matches[:10], start=1)
                        ],
                    },
                    ensure_ascii=False,
                )

            project = matches[0]
            chunks = [
                chunk
                for chunk in project.get("draft_chunks", [])
                if isinstance(chunk, dict) and str(chunk.get("text") or "").strip()
            ]
            requested_part = _safe_int(part, 0, 0)
            if part and not (1 <= requested_part <= len(chunks)):
                return json.dumps(
                    {
                        "status": "part_not_found",
                        "message": f"这篇创作目前只有 {len(chunks)} 个正文部分。",
                        "title": _single_line(project.get("title"), 80) or "未定标题",
                        "part_count": len(chunks),
                    },
                    ensure_ascii=False,
                )

            budget = _safe_int(max_chars, 6000, 600, 12000)
            selected_parts: list[dict[str, Any]] = []
            used_chars = 0
            start_index = requested_part - 1 if requested_part > 0 else 0
            for index in range(start_index, len(chunks)):
                if requested_part > 0 and index != start_index:
                    break
                text_value = str(chunks[index].get("text") or "").strip()
                remaining = budget - used_chars
                if remaining <= 0:
                    break
                shown_text = text_value[:remaining]
                selected_parts.append(
                    {
                        "part": index + 1,
                        "text": shown_text,
                        "chars": len(text_value),
                        "truncated": len(shown_text) < len(text_value),
                    }
                )
                used_chars += len(shown_text)
                if len(shown_text) < len(text_value):
                    break
            last_part = selected_parts[-1]["part"] if selected_parts else 0
            truncated = bool(
                selected_parts
                and (
                    selected_parts[-1].get("truncated")
                    or (requested_part == 0 and last_part < len(chunks))
                )
            )
            payload = {
                "status": "success",
                "action": "get",
                "project": self._creative_work_project_summary(project),
                "premise": _single_line(project.get("premise"), 500),
                "tone": _single_line(project.get("tone"), 120),
                "parts": selected_parts,
                "truncated": truncated,
                "next_part": last_part + 1 if truncated and last_part < len(chunks) else 0,
                "instruction": "只能依据返回的真实正文讨论，不要补写未读取内容。",
            }
            return json.dumps(payload, ensure_ascii=False)

    def _strip_plaintext_tool_call_envelopes(self, text: Any) -> tuple[str, list[dict[str, Any]]]:
        raw = str(text or "")
        if not raw or "{" not in raw:
            return raw, []
        decoder = json.JSONDecoder()
        calls: list[dict[str, Any]] = []
        ranges: list[tuple[int, int]] = []
        cursor = 0
        while cursor < len(raw):
            start = raw.find("{", cursor)
            if start < 0:
                break
            try:
                value, consumed = decoder.raw_decode(raw[start:])
            except Exception:
                cursor = start + 1
                continue
            end = start + consumed
            call = self._plaintext_tool_call_from_object(value)
            if call is None:
                cursor = start + 1
                continue
            calls.append(call)
            ranges.append((start, end))
            cursor = end
        if not ranges:
            return raw, []
        pieces: list[str] = []
        cursor = 0
        for start, end in ranges:
            pieces.append(raw[cursor:start])
            cursor = end
        pieces.append(raw[cursor:])
        cleaned = "".join(pieces)
        cleaned = re.sub(r"</?(?:tool_call|function_call)\b[^>]*>", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"(?im)^[ \t]*```(?:json)?[ \t]*$", "", cleaned)
        cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
        return cleaned, calls

    @staticmethod
    def _memo_management_instruction_matches(text: Any) -> bool:
        value = str(text or "")
        return bool(
            re.search(
                r"便签|便笺|备忘录?|待办|帮我记(?:一下|下来)?|记(?:一下|下来)|"
                r"(?:确认|确定|取消)(?:删除|删掉|移除)|"
                r"(?:完成|恢复|置顶|取消置顶|删除|删掉).{0,4}(?:第?\s*\d+|这张|那张)|"
                r"第?\s*\d+(?:张|条|个)?.{0,8}(?:完成|恢复|置顶|删除|删掉|改到|改成)|"
                r"(?:只看|查看|看看).{0,4}(?:已完成|进行中|全部)",
                value,
                flags=re.I,
            )
        )

    def _remove_future_task_for_memo_request(self, req: Any, text: Any) -> bool:
        """明确的便签操作只保留便签工具，避免同轮再创建官方定时任务。"""
        if not self._memo_management_instruction_matches(text):
            return False
        tool_set = getattr(req, "func_tool", None)
        if tool_set is None:
            return False
        has_future_task = False
        get_tool = getattr(tool_set, "get_tool", None)
        if callable(get_tool):
            try:
                has_future_task = get_tool("future_task") is not None
            except Exception:
                pass
        tools = getattr(tool_set, "tools", None)
        if not has_future_task and isinstance(tools, list):
            has_future_task = any(
                _single_line(getattr(tool, "name", ""), 120) == "future_task"
                for tool in tools
            )
        if not has_future_task:
            return False
        remove_tool = getattr(tool_set, "remove_tool", None)
        try:
            if callable(remove_tool):
                remove_tool("future_task")
            elif isinstance(tools, list):
                tool_set.tools = [
                    tool
                    for tool in tools
                    if _single_line(getattr(tool, "name", ""), 120) != "future_task"
                ]
            else:
                return False
        except Exception as exc:
            logger.warning("便签请求移除 future_task 失败: %s", _single_line(exc, 160))
            return False
        return True

    @staticmethod
    def _scope_reaction_media_tools_for_request(
        req: Any,
        *,
        explicit_media_request: bool,
        reaction_authorized: bool,
        reaction_evaluated: bool,
        allow_photo_on_reaction_turns: bool = False,
    ) -> list[str]:
        """Keep ordinary experimental replies on the single-pass intent path."""
        if explicit_media_request:
            return []
        # Current-media delivery is only meaningful after an explicit request
        # caused another tool to produce a local image in this same turn.
        blocked = {"pc_send_current_media"}
        # Distinguish "not evaluated" (ordinary passive turn, keep the media
        # tools for regular regeneration/expression use) from "evaluated":
        # an evaluated reaction turn must hide the automatic reaction-media
        # tools regardless of whether it was authorized. An authorized turn
        # switches to the internal response tag, while a denied turn must not
        # fall through the legacy media-tool path. Explicit media requests
        # were already returned above and keep every tool visible.
        if reaction_evaluated:
            # The reaction gallery lookup always stays hidden on evaluated
            # turns: its automatic path is the internal response tag, and
            # leaving both available would produce duplicate images.
            blocked.add("pc_find_reaction_image")
            # By default an evaluated turn keeps the photo tool hidden too.
            # When allow_photo_on_reaction_turns is enabled (opt-in only),
            # the photo tool stays in the declaration so the model can answer
            # colloquial see-photo requests on authorized reaction turns; all
            # quotas, daily caps and content boundaries still run inside the
            # tool itself.
            if not allow_photo_on_reaction_turns:
                blocked.add("pc_generate_photo")
        tool_set = getattr(req, "func_tool", None)
        if tool_set is None:
            return []
        tools = getattr(tool_set, "tools", None)
        names = {
            _single_line(getattr(tool, "name", ""), 120)
            for tool in tools
        } if isinstance(tools, list) else set()
        get_tool = getattr(tool_set, "get_tool", None)
        if callable(get_tool):
            for name in blocked:
                try:
                    if get_tool(name) is not None:
                        names.add(name)
                except Exception:
                    pass
        remove_tool = getattr(tool_set, "remove_tool", None)
        removed: list[str] = []
        for name in sorted(blocked):
            if name not in names and isinstance(tools, list):
                continue
            try:
                if callable(remove_tool):
                    remove_tool(name)
                elif isinstance(tools, list):
                    tool_set.tools = [
                        tool
                        for tool in tool_set.tools
                        if _single_line(getattr(tool, "name", ""), 120) != name
                    ]
                else:
                    continue
                removed.append(name)
            except Exception as exc:
                logger.debug(
                    "裁剪实验性表情工具失败: tool=%s error=%s",
                    name,
                    _single_line(exc, 160),
                )
        return removed

    @staticmethod
    def _mark_memo_request_tool_boundary(event: AstrMessageEvent, req: Any) -> None:
        try:
            setattr(event, "private_companion_explicit_memo_request", True)
            setattr(event, "_private_companion_memo_provider_request", req)
        except Exception:
            pass

    def _finalize_memo_request_tool_boundary(self, event: AstrMessageEvent) -> bool:
        """在 AstrBot 补齐内置工具后再次执行便签/定时工具互斥。"""
        if not bool(getattr(event, "private_companion_explicit_memo_request", False)):
            return False
        req = getattr(event, "_private_companion_memo_provider_request", None)
        get_extra = getattr(event, "get_extra", None)
        if callable(get_extra):
            try:
                final_req = get_extra("provider_request")
            except Exception:
                final_req = None
            if final_req is not None:
                req = final_req
        if req is None:
            return False
        return self._remove_future_task_for_memo_request(
            req,
            getattr(event, "message_str", ""),
        )

    def _memo_management_tool_prompt_section(self) -> PromptSection:
        body = """主要用户在私聊里要求新增、查看、修改、完成、恢复、置顶或删除便签时，使用 `pc_manage_memo`，不要只用口头承诺代替实际操作。
- 只有用户明确说“便签/便笺/备忘/待办/帮我记一下/记下来”或正在继续操作已有便签时，才把请求路由到本工具。普通“提醒我/叫醒我/定时/半小时后通知我/别忘了”属于临时提醒，不要擅自建成便签。
- 新增：action=create，title/content 至少传一项；提醒时间传 due_at，可传 `2026-07-15 09:00`，也支持“明早9点”“两小时后”“周五下午3点”等常见表达。
- 查看：action=list；默认 status=active，可用 status=completed/all 查看已完成或全部便签，query 可按标题/正文筛选。列表正文只是预览，需要完整正文时用 action=get + selector。后续用编号操作时要传回相同 status，优先使用返回的 id。
- 修改/完成/恢复/置顶：action=update/complete/reopen/pin/unpin，并用 selector 传便签标题、编号或工具返回的 id。匹配到多张时必须让用户进一步指定，不能自行选择。
- 删除：首次 action=delete 只会返回 confirmation_required，必须让用户回复“确认删除”或“取消删除”；确认时把 confirmation_token 原样传给下一次 delete，取消时 action=cancel_delete。不能绕过确认。
- 含 due_at 且开启提醒的便签，其提醒已经由便签自身负责；成功保存后不得再调用 `future_task`，也不得再输出 `<timer>`，否则会重复提醒。
- 只有工具明确返回 `saved=true`，才能说便签已经新增、修改、完成、恢复、置顶或删除；cancel_delete 返回 `cancelled=true` 时才能说已取消删除。其他 `saved=false`、失败、歧义或等待确认必须如实说明。
- 便签是待办，不是已经发生的经历；不要把未完成事项说成用户已经做过。
""".strip()
        return prompt_section(
            key="tools.memo_management",
            title="备忘便签工具",
            source="tools",
            content=body,
        )

    def _memo_management_tool_instruction(self) -> str:
        return _render_tool_prompt_section_labeled(
            self._memo_management_tool_prompt_section()
        )

    @staticmethod
    def _schedule_management_instruction_matches(text: Any) -> bool:
        compact = re.sub(r"\s+", "", _single_line(text, 240))
        if not compact:
            return False
        operation = bool(re.search(r"(重置|重做|重新细化|重新生成|刷新|取消|删除|删掉|移除|去掉)", compact))
        target = bool(
            re.search(r"(日程|行程|安排|计划|时段|时间段|这段|那段|第[一二两三四五六七八九十\d]+段)", compact)
            or re.search(r"(?:凌晨|早上|上午|中午|下午|傍晚|晚上|今晚)?(?:\d{1,2}|[一二两三四五六七八九十]+)(?:点|时|:|：).{0,10}(?:那段|的安排|的计划)", compact)
        )
        return bool(operation and target)

    def _schedule_management_tool_prompt_section(self) -> PromptSection:
        body = """主要用户在私聊中明确要求重置、重做、重新细化、取消或删除某一段今日日程时，使用 `pc_manage_schedule`，不要只口头承诺。
- 重新细化：action=regenerate；取消/删除/移除：action=cancel。“删除”采用取消语义，保留历史依据，但不会再作为当前活动、细化重试或主动消息契机。
- selector 必须保留用户明确给出的时间、序号或活动关键词，例如“下午三点”“第二段”“整理房间”；不要自行猜一个日程段。工具返回歧义或未命中时，把候选自然列给用户继续选择。
- 只有用户明确要求操作已有日程时才调用。普通聊天中的“我下午出门”“今晚想晚点睡”“你可以休息”等生活信息仍按对话和柔性日程调整理解，不得擅自取消或重置日程。
- 只有工具返回 `saved=true` 才能说操作已经完成；失败、歧义或未找到时必须如实说明。
""".strip()
        return prompt_section(
            key="tools.schedule_management",
            title="指定日程管理工具",
            source="tools",
            content=body,
        )

    def _schedule_management_tool_instruction(self) -> str:
        return _render_tool_prompt_section_labeled(
            self._schedule_management_tool_prompt_section()
        )

    def _memo_tool_authorization(self, event: AstrMessageEvent) -> tuple[bool, str]:
        try:
            is_private = bool(getattr(event, "is_private_chat", lambda: False)())
        except Exception:
            is_private = ":FriendMessage:" in str(getattr(event, "unified_msg_origin", "") or "")
        try:
            identity_for_event = getattr(self, "_event_permission_identity_id", None)
            requester_id = (
                identity_for_event(event)
                if callable(identity_for_event)
                else self._permission_identity_id(event.get_sender_id())
            )
        except Exception:
            requester_id = ""
        allowed = bool(is_private and requester_id and self._is_private_companion_owner_user_id(requester_id))
        if not allowed:
            logger.info(
                "便签管理权限未通过: private=%s sender=%s umo=%s",
                is_private,
                requester_id or "-",
                _single_line(getattr(event, "unified_msg_origin", ""), 120),
            )
        return allowed, requester_id

    def _parse_memo_due_time(self, value: Any, *, now: float) -> tuple[float, str]:
        if value is None or value == "":
            return 0.0, ""
        if isinstance(value, (int, float)):
            timestamp = float(value)
            if timestamp > 10_000_000_000:
                timestamp /= 1000.0
            return (timestamp, "") if timestamp > 0 else (0.0, "提醒时间无效")

        text = _single_line(value, 100).strip()
        if not text:
            return 0.0, ""
        if re.fullmatch(r"\d+(?:\.\d+)?", text):
            timestamp = float(text)
            if timestamp > 10_000_000_000:
                timestamp /= 1000.0
            return (timestamp, "") if timestamp > 0 else (0.0, "提醒时间无效")

        base = self._environment_fromtimestamp(now)
        normalized = text.replace("／", "/").replace("：", ":").strip()
        try:
            parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
            if parsed.tzinfo is None and base.tzinfo is not None:
                parsed = parsed.replace(tzinfo=base.tzinfo)
            if re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}", normalized):
                parsed = parsed.replace(hour=9)
            return parsed.timestamp(), ""
        except ValueError:
            pass
        for fmt in ("%Y/%m/%d %H:%M", "%Y-%m-%d %H:%M", "%Y/%m/%d", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(normalized, fmt)
            except ValueError:
                continue
            if parsed.tzinfo is None and base.tzinfo is not None:
                parsed = parsed.replace(tzinfo=base.tzinfo)
            if fmt in {"%Y/%m/%d", "%Y-%m-%d"}:
                parsed = parsed.replace(hour=9)
            return parsed.timestamp(), ""

        def natural_number(raw: str) -> float:
            if re.fullmatch(r"\d+(?:\.\d+)?", raw):
                return float(raw)
            digits = {
                "零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
                "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
            }
            if raw == "十":
                return 10.0
            if "十" in raw:
                left, right = raw.split("十", 1)
                return float((digits.get(left, 1) * 10) + digits.get(right, 0))
            return float(digits.get(raw, 0))

        relative_day_offset: int | None = None
        duration_match = re.search(r"(\d+(?:\.\d+)?|[一二两三四五六七八九十]+)\s*(分钟|小时|天|周)后", normalized)
        if duration_match:
            amount = natural_number(duration_match.group(1))
            unit = duration_match.group(2)
            seconds = amount * {"分钟": 60, "小时": 3600, "天": 86400, "周": 7 * 86400}[unit]
            has_clock = bool(re.search(r"点|时|:\d|早上|上午|中午|下午|傍晚|晚上|凌晨", normalized))
            if unit in {"天", "周"} and has_clock and amount.is_integer():
                relative_day_offset = int(amount) * (7 if unit == "周" else 1)
            else:
                return now + seconds, ""
        if "半小时后" in normalized:
            return now + 1800, ""

        day_offset: int | None = relative_day_offset
        if "大后天" in normalized:
            day_offset = 3
        elif "后天" in normalized:
            day_offset = 2
        elif any(token in normalized for token in ("明天", "明早", "明晚", "明日下午", "明日上午")):
            day_offset = 1
        elif any(token in normalized for token in ("今天", "今早", "今晚", "今夜", "今日")):
            day_offset = 0

        target_date = (base + timedelta(days=day_offset or 0)).date()
        weekday_match = re.search(r"(下|本|这)?\s*(?:周|星期)([一二三四五六日天])", normalized)
        if weekday_match:
            target_weekday = "一二三四五六日".index("日" if weekday_match.group(2) == "天" else weekday_match.group(2))
            prefix = weekday_match.group(1) or ""
            if prefix == "下":
                days = 7 - base.weekday() + target_weekday
            else:
                days = target_weekday - base.weekday()
                if days < 0:
                    days += 7
            target_date = (base + timedelta(days=days)).date()
            day_offset = days
        else:
            month_day_match = re.search(r"(?:(\d{4})年)?(\d{1,2})月(\d{1,2})(?:日|号)?", normalized)
            if month_day_match:
                year = int(month_day_match.group(1) or base.year)
                month = int(month_day_match.group(2))
                day = int(month_day_match.group(3))
                try:
                    candidate = base.replace(year=year, month=month, day=day).date()
                except ValueError:
                    return 0.0, "提醒日期无效"
                if not month_day_match.group(1) and candidate < base.date():
                    try:
                        candidate = base.replace(year=base.year + 1, month=month, day=day).date()
                    except ValueError:
                        return 0.0, "提醒日期无效"
                target_date = candidate
                day_offset = (candidate - base.date()).days

        clock_number = r"(?:\d{1,2}|[零〇一二两三四五六七八九十]{1,3})"
        time_match = re.search(
            rf"(?<!\d)({clock_number})\s*(?:点|时|:)(?:\s*({clock_number})\s*分?)?",
            normalized,
        )
        has_half = bool(re.search(r"(?:点|时)\s*半", normalized))
        quarter_match = re.search(r"(?:点|时)\s*([一三])刻", normalized)
        if time_match:
            hour = int(natural_number(time_match.group(1)))
            if has_half:
                minute = 30
            elif quarter_match:
                minute = 15 if quarter_match.group(1) == "一" else 45
            else:
                minute = int(natural_number(time_match.group(2) or "0"))
            if hour > 23 or minute > 59:
                return 0.0, "提醒时间无效"
        elif day_offset is not None:
            if "凌晨" in normalized:
                hour, minute = 0, 0
            elif "中午" in normalized:
                hour, minute = 12, 0
            elif "下午" in normalized:
                hour, minute = 15, 0
            elif "傍晚" in normalized:
                hour, minute = 18, 0
            elif any(token in normalized for token in ("晚上", "今晚", "今夜", "明晚")):
                hour, minute = 20, 0
            else:
                hour, minute = 9, 0
        else:
            return 0.0, "无法识别提醒时间，请提供例如“明早9点”或“2026-07-15 09:00”"

        evening = any(token in normalized for token in ("晚上", "今晚", "今夜", "明晚"))
        if evening and hour in {0, 12}:
            hour = 0
            target_date += timedelta(days=1)
        elif any(token in normalized for token in ("下午", "傍晚")) and hour < 12:
            hour += 12
        elif evening and hour < 12:
            hour += 12
        elif "中午" in normalized and hour < 11:
            hour += 12
        elif "凌晨" in normalized and hour == 12:
            hour = 0
        try:
            parsed = base.replace(
                year=target_date.year,
                month=target_date.month,
                day=target_date.day,
                hour=hour,
                minute=minute,
                second=0,
                microsecond=0,
            )
        except ValueError:
            return 0.0, "提醒时间无效"
        if weekday_match and parsed.timestamp() <= now:
            parsed += timedelta(days=7)
        elif day_offset is None and parsed.timestamp() <= now:
            parsed += timedelta(days=1)
        return parsed.timestamp(), ""

    def _memo_tool_note_view(
        self,
        note: dict[str, Any],
        *,
        number: int = 0,
        content_limit: int = 240,
    ) -> dict[str, Any]:
        due_at = _safe_float(note.get("due_at"), 0.0)
        due_text = ""
        if due_at > 0:
            try:
                due_text = self._environment_fromtimestamp(due_at).strftime("%Y-%m-%d %H:%M")
            except Exception:
                due_text = datetime.fromtimestamp(due_at).strftime("%Y-%m-%d %H:%M")
        raw_content = str(note.get("content") or "")
        result = {
            "id": _single_line(note.get("id"), 64),
            "title": _single_line(note.get("title"), 60),
            "content": raw_content[:content_limit],
            "content_truncated": len(raw_content) > content_limit,
            "status": _single_line(note.get("status"), 20) or "active",
            "due_at": due_at,
            "due_text": due_text,
            "repeat": _single_line(note.get("repeat"), 20) or "none",
            "remind_enabled": bool(note.get("remind_enabled")),
            "pinned": bool(note.get("pinned")),
            "color": _single_line(note.get("color"), 20) or "yellow",
        }
        if number > 0:
            result["number"] = number
        return result

    def _memo_tool_find_matches(
        self,
        notes: list[dict[str, Any]],
        selector: Any,
        *,
        status: str = "",
    ) -> list[dict[str, Any]]:
        eligible = [item for item in notes if not status or item.get("status") == status]
        value = _single_line(selector, 100).strip(" \t\r\n‘’“”'\"《》【】[]")
        if not value:
            return []
        exact_id = [item for item in eligible if str(item.get("id") or "") == value]
        if exact_id:
            return exact_id
        number_match = re.fullmatch(r"第?\s*(\d+)\s*(?:张|条|个)?", value)
        if number_match:
            index = int(number_match.group(1)) - 1
            return [eligible[index]] if 0 <= index < len(eligible) else []
        folded = value.casefold()
        exact_title = [item for item in eligible if _single_line(item.get("title"), 60).casefold() == folded]
        if exact_title:
            return exact_title
        return [
            item
            for item in eligible
            if folded in f"{item.get('title', '')}\n{item.get('content', '')}".casefold()
        ]

    async def _pc_manage_memo_impl(
        self,
        event: AstrMessageEvent,
        *,
        action: str = "list",
        title: str = "",
        content: str = "",
        selector: str = "",
        due_at: Any = "",
        repeat: str = "",
        color: str = "",
        remind_enabled: bool | None = None,
        include_completed: bool = False,
        status: str = "",
        query: str = "",
        clear_due: bool = False,
        clear_content: bool = False,
        confirmation_token: str = "",
    ) -> str:
        allowed, requester_id = self._memo_tool_authorization(event)
        if not allowed:
            return json.dumps(
                {"status": "forbidden", "saved": False, "message": "便签只允许配置的主要用户在私聊中管理。"},
                ensure_ascii=False,
            )
        action_key = _single_line(action, 30).lower()
        aliases = {
            "": "list", "查看": "list", "列表": "list", "查询": "list", "list": "list",
            "详情": "get", "查看详情": "get", "get": "get",
            "新增": "create", "添加": "create", "创建": "create", "记录": "create", "create": "create", "add": "create",
            "修改": "update", "编辑": "update", "update": "update", "edit": "update",
            "完成": "complete", "办完": "complete", "complete": "complete", "done": "complete",
            "恢复": "reopen", "重新打开": "reopen", "reopen": "reopen",
            "删除": "delete", "delete": "delete", "remove": "delete",
            "取消删除": "cancel_delete", "cancel_delete": "cancel_delete", "cancel": "cancel_delete",
            "置顶": "pin", "pin": "pin", "取消置顶": "unpin", "unpin": "unpin",
        }
        action_key = aliases.get(action_key, action_key)
        if action_key not in {"list", "get", "create", "update", "complete", "reopen", "delete", "cancel_delete", "pin", "unpin"}:
            return json.dumps({"status": "invalid_action", "saved": False, "message": "不支持的便签操作"}, ensure_ascii=False)

        now = time.time()
        status_key = _single_line(status, 20).lower()
        status_aliases = {
            "": "all" if include_completed else "active",
            "active": "active", "进行中": "active", "未完成": "active", "待办": "active",
            "completed": "completed", "完成": "completed", "已完成": "completed", "历史": "completed",
            "all": "all", "全部": "all",
        }
        status_key = status_aliases.get(status_key, status_key)
        if status_key not in {"active", "completed", "all"}:
            return json.dumps({"status": "invalid_status", "saved": False, "message": "便签状态只支持 active/completed/all"}, ensure_ascii=False)
        if action_key == "list":
            async with self._data_lock:
                raw_notes = self.data.get("memo_notes")
                source_notes = raw_notes if isinstance(raw_notes, list) else []
                notes = [item for item in (normalize_memo_note(raw, now=now) for raw in source_notes) if item]
            if status_key != "all":
                notes = [item for item in notes if item.get("status") == status_key]
            query_text = _single_line(query, 100).casefold()
            if query_text:
                notes = [
                    item for item in notes
                    if query_text in f"{item.get('title', '')}\n{item.get('content', '')}".casefold()
                ]
            notes.sort(key=lambda item: memo_note_sort_key(item, now=now))
            items = [self._memo_tool_note_view(item, number=index) for index, item in enumerate(notes[:20], start=1)]
            return json.dumps(
                {
                    "status": "success",
                    "saved": False,
                    "action": "list",
                    "view": status_key,
                    "query": query_text,
                    "count": len(notes),
                    "shown_count": len(items),
                    "truncated": len(notes) > len(items),
                    "items": items,
                    "message": "当前没有便签" if not notes else f"找到 {len(notes)} 张便签",
                },
                ensure_ascii=False,
            )

        due_timestamp = 0.0
        if action_key == "create" or due_at not in (None, ""):
            due_timestamp, due_error = self._parse_memo_due_time(due_at, now=now)
            if due_error:
                return json.dumps({"status": "invalid_time", "saved": False, "message": due_error}, ensure_ascii=False)

        pending_store = getattr(self, "_memo_delete_confirmations", None)
        if not isinstance(pending_store, dict):
            pending_store = {}
            setattr(self, "_memo_delete_confirmations", pending_store)
        for token, pending in list(pending_store.items()):
            if not isinstance(pending, dict) or _safe_float(pending.get("expires_at"), 0.0) <= now:
                pending_store.pop(token, None)

        token = _single_line(confirmation_token, 100)
        if action_key == "cancel_delete":
            removable = [
                key for key, pending in pending_store.items()
                if isinstance(pending, dict)
                and pending.get("requester_id") == requester_id
                and (not token or key == token)
            ]
            for key in removable:
                pending_store.pop(key, None)
            return json.dumps(
                {
                    "status": "success" if removable else "nothing_pending",
                    "saved": False,
                    "cancelled": bool(removable),
                    "action": "cancel_delete",
                    "message": "已取消删除，便签没有变化。" if removable else "当前没有等待确认的便签删除。",
                },
                ensure_ascii=False,
            )

        confirmed_delete_id = ""
        confirmed_pending: dict[str, Any] | None = None
        if action_key == "delete" and token:
            pending = pending_store.get(token)
            if not isinstance(pending, dict) or pending.get("requester_id") != requester_id:
                return json.dumps({"status": "confirmation_expired", "saved": False, "message": "删除确认已失效，请重新指定便签。"}, ensure_ascii=False)
            confirmed_pending = pending
            confirmed_delete_id = _single_line(pending.get("note_id"), 64)

        try:
            async with self._data_lock:
                raw_notes = self.data.get("memo_notes")
                source_notes = raw_notes if isinstance(raw_notes, list) else []
                notes = [item for item in (normalize_memo_note(raw, now=now) for raw in source_notes) if item]
                notes.sort(key=lambda item: memo_note_sort_key(item, now=now))
                if action_key == "create":
                    payload: dict[str, Any] = {
                        "action": "save",
                        "title": title,
                        "content": content,
                        "due_at": due_timestamp,
                        "repeat": repeat or "none",
                        "color": color or "yellow",
                        "pinned": False,
                        "remind_enabled": True if remind_enabled is None else remind_enabled,
                    }
                    updated_notes, affected = apply_memo_note_action(
                        raw_notes,
                        payload,
                        now=now,
                        fromtimestamp=self._environment_fromtimestamp,
                    )
                else:
                    match_status = "" if status_key == "all" else status_key
                    if not status:
                        match_status = "completed" if action_key == "reopen" else "active" if action_key == "complete" else ""
                    matches = self._memo_tool_find_matches(
                        notes,
                        confirmed_delete_id or selector,
                        status=match_status,
                    )
                    if not matches:
                        return json.dumps({"status": "not_found", "saved": False, "message": "没有找到匹配的便签"}, ensure_ascii=False)
                    if len(matches) > 1:
                        return json.dumps(
                            {
                                "status": "ambiguous",
                                "saved": False,
                                "message": "匹配到多张便签，请用编号、完整标题或 id 进一步指定。",
                                "matches": [self._memo_tool_note_view(item) for item in matches[:8]],
                            },
                            ensure_ascii=False,
                        )
                    target = matches[0]
                    if action_key == "get":
                        return json.dumps(
                            {
                                "status": "success",
                                "saved": False,
                                "action": "get",
                                "note": self._memo_tool_note_view(target, content_limit=800),
                            },
                            ensure_ascii=False,
                        )
                    if confirmed_pending is not None and _safe_float(target.get("updated_at"), 0.0) != _safe_float(confirmed_pending.get("updated_at"), 0.0):
                        pending_store.pop(token, None)
                        return json.dumps(
                            {
                                "status": "confirmation_stale",
                                "saved": False,
                                "message": "便签在确认前发生了变化，请重新发起删除并确认。",
                                "note": self._memo_tool_note_view(target),
                            },
                            ensure_ascii=False,
                        )
                    if action_key == "delete" and not confirmed_delete_id:
                        token = uuid.uuid4().hex
                        pending_store[token] = {
                            "requester_id": requester_id,
                            "note_id": target.get("id"),
                            "updated_at": _safe_float(target.get("updated_at"), 0.0),
                            "expires_at": now + 180,
                        }
                        return json.dumps(
                            {
                                "status": "confirmation_required",
                                "saved": False,
                                "message": "这张便签尚未删除，请让用户回复“确认删除”或“取消删除”。",
                                "note": self._memo_tool_note_view(target),
                                "confirmation_token": token,
                                "expires_in_seconds": 180,
                            },
                            ensure_ascii=False,
                        )
                    payload = {"action": action_key, "id": target.get("id")}
                    partial = False
                    if action_key == "update":
                        payload["action"] = "save"
                        partial = True
                        if title:
                            payload["title"] = title
                        if content or clear_content:
                            payload["content"] = "" if clear_content else content
                        if due_at not in (None, "") or clear_due:
                            payload["due_at"] = 0.0 if clear_due else due_timestamp
                        if repeat:
                            payload["repeat"] = repeat
                        elif clear_due:
                            payload["repeat"] = "none"
                        if color:
                            payload["color"] = color
                        if remind_enabled is not None:
                            payload["remind_enabled"] = remind_enabled
                        if len(payload) <= 2:
                            return json.dumps({"status": "need_changes", "saved": False, "message": "没有提供要修改的内容"}, ensure_ascii=False)
                    updated_notes, affected = apply_memo_note_action(
                        raw_notes,
                        payload,
                        now=now,
                        fromtimestamp=self._environment_fromtimestamp,
                        partial=partial,
                    )

                previous_notes = raw_notes
                self.data["memo_notes"] = updated_notes
                try:
                    self._save_data_sync(sections={"memo_notes"})
                except Exception:
                    self.data["memo_notes"] = previous_notes
                    raise
            if action_key == "delete" and token:
                pending_store.pop(token, None)
            if (
                action_key in {"create", "update"}
                and isinstance(affected, dict)
                and _single_line(affected.get("status"), 20) == "active"
                and _safe_float(affected.get("due_at"), 0.0) > 0
                and bool(affected.get("remind_enabled", True))
            ):
                try:
                    setattr(event, "private_companion_memo_reminder_saved", True)
                except Exception as exc:
                    logger.warning(
                        "便签提醒已保存但无法写入本轮去重标记: user=%s error=%s",
                        requester_id,
                        _single_line(exc, 160),
                    )
                else:
                    logger.info(
                        "便签提醒已保存,本轮将抑制重复临时定时: user=%s note=%s action=%s",
                        requester_id,
                        _single_line(affected.get("id"), 64) or "-",
                        action_key,
                    )
            return json.dumps(
                {
                    "status": "success",
                    "saved": True,
                    "action": action_key,
                    "message": {
                        "create": "便签已新增",
                        "update": "便签已更新",
                        "complete": "便签已完成",
                        "reopen": "便签已恢复",
                        "delete": "便签已删除",
                        "pin": "便签已置顶",
                        "unpin": "已取消便签置顶",
                    }[action_key],
                    "note": self._memo_tool_note_view(affected),
                },
                ensure_ascii=False,
            )
        except ValueError as exc:
            return json.dumps({"status": "invalid", "saved": False, "message": str(exc)}, ensure_ascii=False)
        except Exception as exc:
            logger.error("聊天便签操作失败: %s", _single_line(exc, 160), exc_info=True)
            return json.dumps({"status": "error", "saved": False, "message": f"便签操作失败: {_single_line(exc, 120)}"}, ensure_ascii=False)

    async def _note_photo_tool_quota_attempt(
        self,
        event: AstrMessageEvent,
        *,
        requester_id: str,
        requester: dict[str, Any] | None,
        photo_scope: str,
        image_path: str = "",
    ) -> None:
        if not str(requester_id or "").strip():
            return

        def update_counters() -> bool:
            changed = False
            user = requester
            user_getter = getattr(self, "_get_user", None)
            if not isinstance(user, dict) and callable(user_getter):
                user = user_getter(requester_id)
            if photo_scope == "proactive":
                proactive_notifier = getattr(self, "_note_photo_generation_attempt", None)
                if callable(proactive_notifier):
                    proactive_notifier(requester_id, image_path=image_path)
                    changed = True
            else:
                command_notifier = getattr(self, "_note_command_photo_generation_attempt", None)
                if callable(command_notifier) and isinstance(user, dict):
                    command_notifier(user, image_path=image_path)
                    changed = True
            scope_notifier = getattr(self, "_note_photo_generation_scope_attempt", None)
            if callable(scope_notifier):
                scope_notifier(
                    event,
                    user=user if isinstance(user, dict) else None,
                    user_id=requester_id,
                    scope=photo_scope,
                )
                changed = True
            if changed:
                saver = getattr(self, "_save_data_sync", None)
                if callable(saver):
                    saver(sections={"users", "photo_generation_scope_attempts"})
            return changed

        data_lock = getattr(self, "_data_lock", None)
        if data_lock is not None:
            async with data_lock:
                update_counters()
        else:
            update_counters()


