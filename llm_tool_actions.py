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

class LlmToolActionsMixin(LlmToolActionsQzoneMixin, LlmToolActionsPhotoPromptMixin, LlmToolActionsPhotoGenerateMixin, LlmToolActionsReactionCoreMixin, LlmToolActionsReactionSearchMixin, LlmToolActionsReactionExecMixin):
    """Implementation bodies for LLM tools registered in main.py."""

    def _reaction_asset_library(self):
        return get_reaction_asset_library(self)

    def _cross_user_memory_query_prompt_section(self) -> PromptSection | None:
        if not (self.enabled and getattr(self, "enable_cross_user_memory_bridge", False)):
            return None
        body = """用户在私聊里问“你和某人聊了什么”“最近和某群互动怎样”“某人在群里说过什么”时，可以用 `pc_query_interaction` 读取近期互动摘要。
- 只用于查询，不发送消息。
- 优先传 scope=private/group、user_hint 或 group_hint；不确定时传原始称呼给 hint。
- “最近和他私聊说了什么”传 scope=private,user_hint=对象；“他在群里说了什么”传 scope=group,user_hint=对象，有具体群再加 group_hint。
- 回答时概括最近互动和重点即可，不要大段复述原文。
""".strip()
        return prompt_section(
            key="tools.cross_user_memory",
            title="跨用户记忆互通",
            source="tools",
            content=body,
        )

    def _cross_user_memory_query_instruction(self) -> str:
        return _render_tool_prompt_section_labeled(
            self._cross_user_memory_query_prompt_section()
        )

    def _relation_lookup_prompt_section(self) -> PromptSection | None:
        if not (self.enabled and runtime_persona_setting(self, 'enable_worldbook_member_recognition', False)):
            return None
        body = """用户明确要求“查一下关系网/帮我查某个 QQ 或昵称”时，可以用 `pc_query_relation_person` 查询关系网。
- 如果刚用 LivingMemory/长期记忆召回到某个人名、昵称、QQ 或群成员别名,并且需要判断 TA 是谁、和用户什么关系、能不能套用某段关系时,也可以先查关系网再回答。
- 只用于确认是否认识和读取稳定称呼、别名、简短身份备注；不要发送消息。
- 参数用 keyword 传 QQ 号、昵称、别名或用户原话里最像名字的部分。
- 查不到就自然说明没在关系网里确认过，不要编造。
""".strip()
        return prompt_section(
            key="tools.relation_lookup",
            title="关系网查询",
            source="tools",
            content=body,
        )

    def _relation_lookup_instruction(self) -> str:
        return _render_tool_prompt_section_labeled(
            self._relation_lookup_prompt_section()
        )

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

    def _interaction_query_platform(self, event: AstrMessageEvent) -> str:
        origin = str(getattr(event, "unified_msg_origin", "") or "")
        platform = origin.split(":", 1)[0] if ":" in origin else ""
        return platform or getattr(self, "target_platform", "") or "aiocqhttp"

    def _interaction_query_private_targets(self, hint: str = "") -> list[dict[str, str]]:
        query = _single_line(hint, 128)
        users = self.data.get("users") if isinstance(self.data.get("users"), dict) else {}
        profiles = self.data.get("worldbook_member_profiles") if isinstance(self.data.get("worldbook_member_profiles"), dict) else {}
        targets: dict[str, dict[str, str]] = {}

        def add(user_id: str, label: str = "", source: str = "") -> None:
            user_id = _single_line(user_id, 128)
            if not user_id:
                return
            existing = targets.setdefault(user_id, {"user_id": user_id, "label": "", "source": ""})
            if label and (not existing.get("label") or existing.get("label") == user_id):
                existing["label"] = label
            if source and not existing.get("source"):
                existing["source"] = source

        if query and query.isdigit():
            add(query, query, "qq")
        configured_ids = set(self._configured_target_ids()) if callable(getattr(self, "_configured_target_ids", None)) else set()
        for configured_id in configured_ids:
            uid = _single_line(configured_id, 128)
            if uid and (not query or query == uid or query in uid):
                add(uid, uid, "target_config")
        for user_id, user in users.items():
            if not isinstance(user, dict):
                continue
            uid = _single_line(user.get("user_id") or user_id, 128)
            try:
                uid = self._canonical_private_user_id(uid)
            except Exception:
                pass
            if not uid or not self._is_target_private_user(uid, user) or not bool(user.get("enabled", True)):
                continue
            tokens = [
                uid,
                user.get("nickname"),
                user.get("display_name"),
                user.get("last_display_name"),
                user.get("stable_name"),
                *(user.get("observed_display_names") if isinstance(user.get("observed_display_names"), list) else []),
                *(user.get("aliases") if isinstance(user.get("aliases"), list) else []),
            ]
            clean_tokens = [_single_line(token, 60) for token in tokens if _single_line(token, 60)]
            if not query or any(query == token or (query and query in token) for token in clean_tokens):
                label = next((token for token in clean_tokens if token and token != uid), uid)
                add(uid, label, "private_user")
        for user_id, profile in profiles.items():
            if not isinstance(profile, dict):
                continue
            uid = _single_line(profile.get("linked_qq_user_id") or profile.get("user_id") or user_id, 40)
            if not uid or not uid.isdigit():
                continue
            try:
                uid = self._canonical_private_user_id(uid)
            except Exception:
                pass
            linked_user = users.get(uid) if isinstance(users, dict) else None
            configured_target = uid in configured_ids
            if not configured_target and not (
                isinstance(linked_user, dict)
                and self._is_target_private_user(uid, linked_user)
                and bool(linked_user.get("enabled", True))
            ):
                continue
            tokens = [
                uid,
                profile.get("name"),
                *(profile.get("aliases") if isinstance(profile.get("aliases"), list) else []),
                *(profile.get("observed_names") if isinstance(profile.get("observed_names"), list) else []),
            ]
            clean_tokens = [_single_line(token, 60) for token in tokens if _single_line(token, 60)]
            if not query or any(query == token or (query and query in token) for token in clean_tokens):
                label = next((token for token in clean_tokens if token and token != uid), uid)
                add(uid, label, "worldbook")
        return list(targets.values())[:12]

    async def _interaction_query_group_targets(self, event: AstrMessageEvent, hint: str = "") -> list[dict[str, str]]:
        query = _single_line(hint, 80)
        targets: dict[str, dict[str, str]] = {}

        def group_allowed(group_id: str) -> bool:
            checker = getattr(self, "_group_enabled_for_event", None)
            if not callable(checker):
                return False
            try:
                return bool(checker(group_id))
            except Exception:
                return False

        def add(group_id: str, label: str = "", source: str = "") -> None:
            group_id = _single_line(group_id, 40)
            if not group_id or not group_allowed(group_id):
                return
            existing = targets.setdefault(group_id, {"group_id": group_id, "label": "", "source": ""})
            if label and (not existing.get("label") or existing.get("label") == group_id):
                existing["label"] = label
            if source and not existing.get("source"):
                existing["source"] = source

        if query and query.isdigit():
            add(query, query, "group_id")
        groups = self.data.get("groups") if isinstance(self.data.get("groups"), dict) else {}
        for group_id, group in groups.items():
            if not isinstance(group, dict):
                continue
            gid = _single_line(group.get("group_id") or group_id, 40)
            tokens = [
                gid,
                group.get("name"),
                group.get("group_name"),
                group.get("display_name"),
                group.get("nickname"),
            ]
            clean_tokens = [_single_line(token, 80) for token in tokens if _single_line(token, 80)]
            if not query or any(query == token or (query and query in token) for token in clean_tokens):
                label = next((token for token in clean_tokens if token and token != gid), gid)
                add(gid, label, "plugin_group")
        profiles = self.data.get("worldbook_group_profiles") if isinstance(self.data.get("worldbook_group_profiles"), dict) else {}
        for group_id, profile in profiles.items():
            if not isinstance(profile, dict):
                continue
            gid = _single_line(profile.get("group_id") or group_id, 40)
            tokens = [gid, profile.get("name"), profile.get("title"), profile.get("display_name")]
            clean_tokens = [_single_line(token, 80) for token in tokens if _single_line(token, 80)]
            if not query or any(query == token or (query and query in token) for token in clean_tokens):
                label = next((token for token in clean_tokens if token and token != gid), gid)
                add(gid, label, "worldbook_group")
        return list(targets.values())[:12]

    async def _interaction_query_read_history(self, umo: str, *, limit: int = 40, hours: int = 72) -> list[dict[str, Any]]:
        getter = getattr(self, "_get_current_conversation_safely", None)
        try:
            if callable(getter):
                conv = await getter(umo, label="cross_user_memory_query")
            else:
                conv_id = await self.context.conversation_manager.get_curr_conversation_id(umo)
                if not conv_id:
                    return []
                conv = await self.context.conversation_manager.get_conversation(umo, conv_id)
        except Exception:
            return []
        history = self._load_conversation_history_items(conv)
        if not history:
            return []
        max_items = max(5, min(120, _safe_int(limit, 40, 5)))
        cutoff = _now_ts() - max(1, min(24 * 30, _safe_int(hours, 72, 1))) * 3600
        dated: list[dict[str, Any]] = []
        undated: list[dict[str, Any]] = []
        for item in history:
            if not isinstance(item, dict):
                continue
            if not self._history_item_content_text(item):
                continue
            ts = self._history_item_timestamp(item)
            if ts is None:
                undated.append(item)
            elif ts >= cutoff:
                dated.append(item)
        selected = dated[-max_items:]
        return [item for item in selected if isinstance(item, dict)][-max_items:]

    def _interaction_query_lines(self, history: list[dict[str, Any]], *, limit: int = 24) -> list[str]:
        lines: list[str] = []
        for item in history[-max(1, limit):]:
            line = self._format_history_item_for_summary(item)
            if not line:
                continue
            line = re.sub(r"\s+", " ", line).strip()
            if line and line not in lines:
                lines.append(line)
        return lines

    def _interaction_query_user_filter_tokens(self, user_hint: str = "") -> tuple[set[str], set[str]]:
        user_hint = _single_line(user_hint, 128)
        ids: set[str] = set()
        names: set[str] = set()
        if user_hint:
            if user_hint.isdigit():
                ids.add(user_hint)
            else:
                names.add(user_hint)
        for target in self._interaction_query_private_targets(user_hint):
            user_id = _single_line(target.get("user_id"), 40)
            label = _single_line(target.get("label"), 60)
            if user_id:
                ids.add(user_id)
            if label and label != user_id:
                names.add(label)
        return ids, names

    def _interaction_query_group_recent_lines(self, group_id: str, *, limit: int = 24, user_hint: str = "", hours: int = 72) -> list[str]:
        groups = self.data.get("groups") if isinstance(self.data.get("groups"), dict) else {}
        group = groups.get(str(group_id))
        if not isinstance(group, dict):
            return []
        checker = getattr(self, "_group_enabled_for_event", None)
        if not callable(checker):
            return []
        try:
            if not checker(str(group_id)):
                return []
        except Exception:
            return []
        recent = group.get("recent_messages") if isinstance(group.get("recent_messages"), list) else []
        filter_ids, filter_names = self._interaction_query_user_filter_tokens(user_hint)
        cutoff = _now_ts() - max(1, min(24 * 30, _safe_int(hours, 72, 1))) * 3600
        lines: list[str] = []
        for item in recent[-max(1, limit):]:
            if not isinstance(item, dict):
                continue
            sender_id = _single_line(item.get("sender_id") or item.get("user_id"), 40)
            speaker = _single_line(item.get("identity_name") or item.get("name") or item.get("sender_name") or sender_id, 40) or "群友"
            if user_hint:
                speaker_hit = any(token and (token == speaker or token in speaker) for token in filter_names)
                if not ((sender_id and sender_id in filter_ids) or speaker_hit):
                    continue
            text = _single_line(item.get("text") or item.get("message"), 220)
            if not text:
                continue
            ts = _safe_float(item.get("ts") or item.get("time") or item.get("timestamp"), 0)
            if ts > 10_000_000_000:
                ts /= 1000
            if ts <= 0 or ts < cutoff:
                continue
            prefix = ""
            if ts > 0:
                try:
                    prefix = self._environment_fromtimestamp(ts).strftime("%m-%d %H:%M") + " "
                except Exception:
                    prefix = ""
            lines.append(f"{prefix}{speaker}: {text}")
        return lines

    def _interaction_query_group_user_recent_lines(self, user_hint: str, *, limit: int = 36, hours: int = 72) -> list[str]:
        user_hint = _single_line(user_hint, 128)
        if not user_hint:
            return []
        groups = self.data.get("groups") if isinstance(self.data.get("groups"), dict) else {}
        lines: list[str] = []
        per_group_limit = max(4, min(12, limit // 3 or 8))
        for group_id, group in groups.items():
            if not isinstance(group, dict):
                continue
            group_label = _single_line(group.get("name") or group.get("group_name") or group_id, 60)
            group_lines = self._interaction_query_group_recent_lines(
                str(group_id),
                limit=per_group_limit,
                user_hint=user_hint,
                hours=hours,
            )
            for line in group_lines:
                lines.append(f"{group_label}｜{line}")
        return lines[-max(1, limit):]

    async def _pc_query_interaction_impl(self, event: AstrMessageEvent, **kwargs) -> str:
        if not getattr(self, "enable_cross_user_memory_bridge", False):
            return json.dumps({"status": "disabled", "message": "跨用户记忆互通未启用"}, ensure_ascii=False)
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
        owner_only = bool(getattr(self, "cross_user_memory_owner_only", True))
        owner_allowed = bool(requester_id and self._is_private_companion_owner_user_id(requester_id))
        admin_allowed = bool(requester_id and self._is_configured_admin_user_id(requester_id))
        allowed = owner_allowed or (not owner_only and admin_allowed)
        forbidden_message = "只有配置的主要用户可以查询 Bot 与其他人的互动。" if owner_only else "只有配置的主要用户或 AstrBot 全局管理员可以查询 Bot 与其他人的互动。"
        if not is_private or not allowed:
            logger.info(
                "跨用户互动查询权限未通过: sender=%s owner=%s admin=%s owner_only=%s umo=%s",
                requester_id or "-",
                owner_allowed,
                admin_allowed,
                owner_only,
                _single_line(getattr(event, "unified_msg_origin", ""), 120),
            )
            return json.dumps({"status": "forbidden", "message": forbidden_message}, ensure_ascii=False)
        query = InteractionQuery.parse(kwargs)
        scope = query.scope
        user_hint = query.user_hint
        group_hint = query.group_hint
        hint = query.hint
        hours = query.hours
        limit = query.limit

        return await execute_interaction_query(self, event, query)


    async def _pc_get_group_id_by_name_impl(self, event: AstrMessageEvent, **kwargs) -> str:
        if not self.enable_atrelay_tools:
            return json.dumps({"status": "disabled", "message": "跨群转述工具未启用"}, ensure_ascii=False)
        group_name = kwargs.get("group_name") or kwargs.get("name") or kwargs.get("keyword") or kwargs.get("group_id") or ""
        keyword = _single_line(group_name, 80)
        cached = self._atrelay_cached_group_matches(keyword)
        if cached:
            return json.dumps(
                {
                    "status": "success",
                    "count": len(cached),
                    "groups": cached[:20],
                    "source": "local_cache",
                    "message": "已从插件群缓存/关系网群档案匹配，未依赖平台群列表。",
                },
                ensure_ascii=False,
            )
        bot = getattr(event, "bot", None)
        api = getattr(bot, "api", None)
        call_action = getattr(api, "call_action", None)
        if not callable(call_action):
            return json.dumps({"status": "error", "message": "当前平台不支持获取群列表，本地群缓存/关系网群档案也未命中"}, ensure_ascii=False)
        try:
            groups = await call_action("get_group_list")
            matches = []
            for item in groups if isinstance(groups, list) else []:
                group_id = str(item.get("group_id") or "")
                name = _single_line(item.get("group_name") or item.get("group_remark"), 100)
                if not keyword or keyword in name or keyword in group_id:
                    matches.append({"group_id": group_id, "group_name": name})
            return json.dumps({"status": "success", "count": len(matches), "groups": matches[:20]}, ensure_ascii=False)
        except Exception as exc:
            return json.dumps({"status": "error", "message": f"获取群列表失败: {_single_line(exc, 120)}"}, ensure_ascii=False)

    def _relation_lookup_authorized(self, event: AstrMessageEvent) -> bool:
        try:
            is_private = bool(getattr(event, "is_private_chat", lambda: False)())
        except Exception:
            is_private = ":FriendMessage:" in str(getattr(event, "unified_msg_origin", "") or "")
        if not is_private:
            return False
        try:
            identity_for_event = getattr(self, "_event_permission_identity_id", None)
            requester_id = (
                identity_for_event(event)
                if callable(identity_for_event)
                else self._permission_identity_id(event.get_sender_id())
            )
        except Exception:
            requester_id = ""
        owner_allowed = bool(requester_id and self._is_private_companion_owner_user_id(requester_id))
        admin_allowed = bool(requester_id and self._is_configured_admin_user_id(requester_id))
        allowed = owner_allowed or admin_allowed
        if not allowed:
            logger.info(
                "关系网查询权限未通过: private=%s sender=%s owner=%s admin=%s umo=%s",
                is_private,
                requester_id or "-",
                owner_allowed,
                admin_allowed,
                _single_line(getattr(event, "unified_msg_origin", ""), 120),
            )
        return allowed

    def _relation_lookup_clean_keyword(self, value: Any) -> str:
        text = _single_line(value, 120)
        if not text:
            return ""
        match = re.search(r"\d{5,12}", text)
        if match:
            return match.group(0)
        text = re.sub(r"(这个|那个|此人|这人|那人|用户|群友|qq号|QQ号|QQ|qq)", "", text, flags=re.I)
        text = re.sub(r"(你认识吗|认识吗|认得吗|知道吗|是谁呀|是谁啊|是谁|什么人|哪位|吗|呀|啊|呢)", "", text)
        return _single_line(text.strip(" ：:，,。？?"), 60)

    async def _pc_query_relation_person_impl(self, event: AstrMessageEvent, **kwargs) -> str:
        if not runtime_persona_setting(self, 'enable_worldbook_member_recognition', False):
            return json.dumps({"status": "disabled", "message": "关系网未启用"}, ensure_ascii=False)
        if not self._relation_lookup_authorized(event):
            return json.dumps({"status": "forbidden", "message": "关系网查询只允许主要用户/管理员在私聊中使用"}, ensure_ascii=False)
        keyword = self._relation_lookup_clean_keyword(
            kwargs.get("keyword")
            or kwargs.get("name")
            or kwargs.get("user")
            or kwargs.get("user_id")
            or kwargs.get("nickname")
            or kwargs.get("query")
            or ""
        )
        if not keyword:
            return json.dumps({"status": "error", "message": "缺少要查询的 QQ 号、昵称或别名"}, ensure_ascii=False)

        matches: list[dict[str, Any]] = []
        if keyword.isdigit():
            matches.extend(self._resolve_worldbook_member_by_name(keyword))
            if not matches:
                users = self.data.get("users") if isinstance(self.data.get("users"), dict) else {}
                user = users.get(keyword) if isinstance(users, dict) else None
                if isinstance(user, dict):
                    label = _single_line(
                        user.get("stable_name") or user.get("nickname") or user.get("display_name") or user.get("last_display_name"),
                        60,
                    )
                    matches.append({"user_id": keyword, "name": label or keyword, "source": "private_user"})
        else:
            matches.extend(self._resolve_worldbook_member_by_name(keyword))
            existing_ids = {str(item.get("user_id") or "") for item in matches}
            for target in self._interaction_query_private_targets(keyword):
                uid = _single_line(target.get("user_id"), 40)
                if uid and uid not in existing_ids and target.get("source") != "qq":
                    matches.append({
                        "user_id": uid,
                        "name": _single_line(target.get("label"), 60) or uid,
                        "source": target.get("source") or "private_user",
                    })
                    existing_ids.add(uid)

        if not matches:
            logger.info("关系网查询未命中: keyword=%s", keyword)
            return json.dumps({"status": "not_found", "keyword": keyword, "message": "关系网里没有确认匹配对象"}, ensure_ascii=False)
        status = "success" if len(matches) == 1 else "ambiguous"
        logger.info("关系网查询命中: keyword=%s count=%s", keyword, len(matches))
        return json.dumps(
            {
                "status": status,
                "keyword": keyword,
                "count": len(matches),
                "matches": matches[:8],
            },
            ensure_ascii=False,
        )

    async def _pc_get_user_id_by_name_impl(self, event: AstrMessageEvent, **kwargs) -> str:
        if not self.enable_atrelay_tools:
            return json.dumps({"status": "disabled", "message": "跨群转述工具未启用"}, ensure_ascii=False)
        group_id = kwargs.get("group_id") or kwargs.get("group") or kwargs.get("group_name") or ""
        nickname = kwargs.get("nickname") or kwargs.get("name") or kwargs.get("keyword") or kwargs.get("user_name") or kwargs.get("user") or ""
        target_group = _single_line(group_id, 40) or self._extract_group_id_from_event(event)
        query = _single_line(nickname, 128)
        if not query:
            return json.dumps({"status": "error", "message": "缺少 nickname/name 参数"}, ensure_ascii=False)
        resolved = await self._resolve_atrelay_target_user(event, target_group, query)
        if resolved.get("ambiguous"):
            return json.dumps({"status": "ambiguous", "message": "匹配到多个群友,需要用户补充 QQ 或更明确称呼", "matches": resolved.get("matches", [])}, ensure_ascii=False)
        if resolved.get("user_id"):
            return json.dumps({"status": "success", **resolved}, ensure_ascii=False)
        return json.dumps({"status": "not_found", "message": "未找到匹配群友"}, ensure_ascii=False)

    async def _pc_get_specified_group_members_impl(self, event: AstrMessageEvent, **kwargs) -> str:
        if not self.enable_atrelay_tools:
            return json.dumps({"status": "disabled", "message": "跨群转述工具未启用"}, ensure_ascii=False)
        group_id = kwargs.get("group_id") or kwargs.get("group") or kwargs.get("group_name") or ""
        keyword = kwargs.get("keyword") or kwargs.get("name") or kwargs.get("nickname") or kwargs.get("user") or ""
        target_group = _single_line(group_id, 40) or self._extract_group_id_from_event(event)
        if not target_group:
            return json.dumps({"status": "error", "message": "未指定群号且当前不在群聊环境中"}, ensure_ascii=False)
        query = _single_line(keyword, 60)
        try:
            members = await self._get_group_member_list_for_tool(event, target_group)
            formatted = [self._format_atrelay_member(item) for item in members]
            if query:
                formatted = [
                    item for item in formatted
                    if query in item.get("user_id", "")
                    or query in item.get("nickname", "")
                    or query in item.get("group_card", "")
                    or query in item.get("relation_name", "")
                ]
            if runtime_persona_setting(self, 'enable_worldbook_member_recognition', True):
                async with self._data_lock:
                    self._save_data_sync(sections={"worldbook_member_profiles"})
            return json.dumps({"status": "success", "group_id": target_group, "count": len(formatted), "members": formatted[:80]}, ensure_ascii=False)
        except Exception as exc:
            return json.dumps({"status": "error", "message": f"查询群成员失败: {_single_line(exc, 120)}"}, ensure_ascii=False)

    def _atrelay_platform_prefix_candidates(self, event: AstrMessageEvent) -> list[str]:
        prefixes: list[str] = []

        def add(value: Any) -> None:
            text = _single_line(value, 80)
            if not text:
                return
            prefix = text.split(":", 1)[0] if ":" in text else text
            if prefix and prefix not in prefixes:
                prefixes.append(prefix)

        add(getattr(event, "unified_msg_origin", ""))
        try:
            sender_id = str(event.get_sender_id())
        except Exception:
            sender_id = ""
        resolver = getattr(self, "_private_user_id_for_event", None)
        if callable(resolver) and sender_id:
            try:
                sender_id = _single_line(resolver(event, sender_id), 160) or sender_id
            except Exception:
                pass
        users = self.data.get("users") if isinstance(self.data.get("users"), dict) else {}
        user = users.get(sender_id) if sender_id and isinstance(users, dict) else None
        if isinstance(user, dict):
            add(user.get("umo"))
        add(getattr(self, "target_platform", ""))
        manager = getattr(getattr(self, "context", None), "platform_manager", None)
        if manager is not None:
            try:
                platforms = list(manager.get_insts())
            except Exception:
                platforms = list(getattr(manager, "platform_insts", []) or [])
            for platform in platforms:
                try:
                    meta = platform.meta()
                except Exception:
                    continue
                add(getattr(meta, "id", ""))
                add(getattr(meta, "name", ""))
        return prefixes

    def _atrelay_target_umo_candidates(self, event: AstrMessageEvent, message_type: str, target_id: str) -> list[str]:
        message_type = "GroupMessage" if message_type == "group" else "FriendMessage"
        target = _single_line(target_id, 40 if message_type == "GroupMessage" else 128)
        if not target:
            return []
        candidates: list[str] = []

        def add_umo(value: Any) -> None:
            umo = _single_line(value, 160)
            if not umo or f":{message_type}:{target}" not in umo:
                return
            if umo not in candidates:
                candidates.append(umo)

        if message_type == "GroupMessage":
            groups = self.data.get("groups") if isinstance(self.data.get("groups"), dict) else {}
            group = groups.get(target) if isinstance(groups, dict) else None
            if isinstance(group, dict):
                add_umo(group.get("umo"))
        else:
            users = self.data.get("users") if isinstance(self.data.get("users"), dict) else {}
            user = users.get(target) if isinstance(users, dict) else None
            if isinstance(user, dict):
                add_umo(user.get("umo"))
        for prefix in self._atrelay_platform_prefix_candidates(event):
            add_umo(f"{prefix}:{message_type}:{target}")
        return candidates

    async def _send_atrelay_chain_to_target(
        self,
        event: AstrMessageEvent,
        *,
        message_type: str,
        target_id: str,
        chain: list[Any],
    ) -> tuple[bool, str, str]:
        errors: list[str] = []
        candidates = self._atrelay_target_umo_candidates(event, message_type, target_id)
        if not candidates:
            return False, "没有可用目标会话", ""
        for umo in candidates:
            session = self._parse_message_session(umo)
            platform = self._get_platform_for_session(session) if session else None
            if session and platform:
                try:
                    session_obj = MessageSession(
                        platform_name=str(getattr(session, "platform_id", "") or ""),
                        message_type=self._message_type_for_session(session),
                        session_id=str(getattr(session, "session_id", "") or ""),
                    )
                    await platform.send_by_session(session_obj, MessageChain(chain))
                    logger.info("转述已通过精确平台发送: umo=%s", _single_line(umo, 160))
                    return True, "", umo
                except Exception as exc:
                    errors.append(f"{umo}: 精确发送失败 {self._format_send_exception(exc)}")
                try:
                    result = await self.context.send_message(umo, MessageChain(chain))
                    if result is not False:
                        logger.info("转述已通过 AstrBot 核心发送: umo=%s", _single_line(umo, 160))
                        return True, "", umo
                    errors.append(f"{umo}: 核心发送返回 False")
                except Exception as exc:
                    errors.append(f"{umo}: 核心发送失败 {self._format_send_exception(exc)}")
            elif session:
                errors.append(f"{umo}: 未找到匹配平台，跳过 AstrBot 核心发送")
            else:
                errors.append(f"{umo}: UMO 无法解析，跳过 AstrBot 核心发送")
            try:
                direct_ok, direct_error = await self._send_chain_components_via_onebot_direct(umo, session, chain)
            except Exception as exc:
                direct_ok, direct_error = False, self._format_send_exception(exc)
            if direct_ok:
                return True, "", umo
            if direct_error:
                errors.append(f"{umo}: OneBot 兜底失败 {direct_error}")
        return False, "；".join(errors[-5:]) or "所有发送链路都失败", candidates[0]

    async def _pc_relay_message_impl(self, event: AstrMessageEvent, **kwargs) -> str:
        if not self.enable_atrelay_tools:
            return json.dumps({"status": "disabled", "message": "跨会话转述工具未启用"}, ensure_ascii=False)
        authorized, _requester_id = self._atrelay_tool_authorization(event)
        if not authorized:
            return json.dumps({"status": "forbidden", "message": "跨会话转述仅允许主人使用"}, ensure_ascii=False)
        destination_raw = _single_line(
            kwargs.get("destination")
            or kwargs.get("target_scope")
            or kwargs.get("scope")
            or kwargs.get("target_type")
            or kwargs.get("type")
            or "auto",
            40,
        ).lower()
        group_hint = kwargs.get("group_hint") or kwargs.get("group_id") or kwargs.get("group") or kwargs.get("target_group") or ""
        recipient_hint = (
            kwargs.get("recipient_hint")
            or kwargs.get("recipient")
            or kwargs.get("to")
            or kwargs.get("at_user")
            or kwargs.get("target_user")
            or kwargs.get("user_id")
            or kwargs.get("nickname")
            or kwargs.get("name")
            or ""
        )
        message = kwargs.get("message") or kwargs.get("text") or kwargs.get("content") or kwargs.get("msg") or ""
        relay_mode = kwargs.get("relay_mode") or kwargs.get("mode") or ""
        sensitive_confirmed = kwargs.get("sensitive_confirmed", kwargs.get("confirmed", False))
        delay_until_seen = self._atrelay_bool_flag(
            kwargs.get("delay_until_recipient_seen", kwargs.get("delay", kwargs.get("wait_until_seen", False)))
        )
        need_receipt = self._atrelay_bool_flag(
            kwargs.get("need_receipt", kwargs.get("wait_for_reply", kwargs.get("receipt", kwargs.get("report_back", False))))
        )
        confirm_before_report = self._atrelay_bool_flag(
            kwargs.get("confirm_before_report", kwargs.get("require_reply_confirmation", kwargs.get("confirm_reply", False)))
        )
        at_recipient = self._atrelay_bool_flag(kwargs.get("at_recipient", kwargs.get("at", False)))
        expire_hours = kwargs.get("expire_hours", kwargs.get("ttl_hours", 24))

        text = self._normalize_atrelay_text(message, limit=800)
        recipient = _single_line(recipient_hint, 128)
        if not text:
            return json.dumps({"status": "error", "message": "缺少 message/text 内容"}, ensure_ascii=False)

        if destination_raw in {"group", "groups", "群", "群聊", "send_group", "to_group"}:
            destination = "group"
        elif destination_raw in {"private", "user", "friend", "私聊", "私发", "私信", "to_user", "dm"}:
            destination = "private"
        else:
            if group_hint:
                destination = "group"
            elif recipient:
                destination = "private"
            else:
                destination = "auto"

        if destination == "auto":
            return json.dumps({"status": "need_target", "message": "需要说明发到哪个群或私聊给谁"}, ensure_ascii=False)

        boundary = self._atrelay_boundary_guard(text)
        if boundary:
            return json.dumps({"status": "error", "message": boundary}, ensure_ascii=False)
        guard = self._atrelay_confirmation_guard(
            text,
            relay_mode=self._normalize_atrelay_relay_mode(relay_mode),
            sensitive_confirmed=self._atrelay_bool_flag(sensitive_confirmed) or self._atrelay_event_confirms_sensitive_send(event),
        )
        if guard:
            return json.dumps({"status": "need_confirm", "message": guard}, ensure_ascii=False)

        if destination == "group":
            group_result = {}
            current_group_id = self._extract_group_id_from_event(event)
            if not _single_line(group_hint, 80) and recipient:
                group_result = await self._resolve_atrelay_active_group_for_recipient(
                    event,
                    recipient,
                    exclude_current_group=bool(current_group_id),
                )
            if not _single_line(group_hint, 80) and current_group_id and not group_result:
                return json.dumps(
                    {
                        "status": "need_group",
                        "message": "需要补充要发到哪个群；群聊里不会默认发回当前群。",
                    },
                    ensure_ascii=False,
                )
            if not group_result:
                group_result = await self._resolve_atrelay_target_group(event, group_hint)
            if group_result.get("status") != "success":
                return json.dumps(group_result, ensure_ascii=False)
            group_id = _single_line(group_result.get("group_id"), 40)
            group_guard = self._atrelay_target_group_allowed(group_id, event)
            if group_guard:
                return json.dumps({"status": "forbidden", "message": group_guard}, ensure_ascii=False)
            send_text = await self._rewrite_atrelay_message_with_llm(
                event,
                destination="group",
                recipient_hint=recipient,
                text=text,
                relay_mode=relay_mode,
            )
            send_text = self._normalize_atrelay_text(send_text, limit=800)
            if delay_until_seen:
                if not recipient:
                    return json.dumps({"status": "need_recipient", "message": "延迟转述需要目标群友"}, ensure_ascii=False)
                result = await self._pc_schedule_group_relay_impl(
                    event,
                    group_id=group_id,
                    at_user=recipient,
                    message=send_text,
                    relay_mode=relay_mode,
                    sensitive_confirmed=sensitive_confirmed,
                    expire_hours=expire_hours,
                )
                return json.dumps({"status": "scheduled" if result.startswith("已挂起") else "error", "message": result}, ensure_ascii=False)
            result = await self._pc_send_to_group_impl(
                event,
                group_id=group_id,
                message=send_text,
                at_user=recipient if (recipient and (at_recipient or recipient)) else "",
                relay_mode=relay_mode,
                sensitive_confirmed=sensitive_confirmed,
            )
            ok = result.startswith("消息已发送")
            if ok:
                setattr(
                    event,
                    "private_companion_atrelay_tool_result",
                    {
                        "status": "success",
                        "destination": "group",
                        "final_reply": "带到了。",
                        "final_reply_reference": "参考意图：转述已经成功发到目标群；只给用户一个很短的成功回执，不要复述转述正文，也不要写工具执行状态。",
                        "sent_text": send_text,
                        "recipient": recipient,
                        "group_id": group_id,
                    },
                )
            return json.dumps(
                {
                    "status": "success" if ok else "error",
                    "message": "带到了。" if ok else result,
                    "final_reply": "带到了。" if ok else "",
                    "final_reply_reference": "参考意图：转述已经成功发到目标群；只给用户一个很短的成功回执，不要复述转述正文，也不要写工具执行状态。" if ok else "",
                    "sent_text": send_text if ok else "",
                },
                ensure_ascii=False,
            )

        target_user = recipient
        if not target_user:
            return json.dumps({"status": "need_recipient", "message": "需要补充私聊目标用户 ID 或称呼"}, ensure_ascii=False)
        if not target_user.isdigit():
            resolved = await self._resolve_atrelay_target_user(event, "", target_user)
            if not resolved.get("user_id") and not resolved.get("ambiguous"):
                group_result = await self._resolve_atrelay_target_group(event, group_hint)
            else:
                group_result = {}
            group_id = _single_line(group_result.get("group_id"), 40) if group_result.get("status") == "success" else ""
            if not group_id and self._extract_group_id_from_event(event):
                group_id = self._extract_group_id_from_event(event)
            if not resolved.get("user_id") and not resolved.get("ambiguous") and not group_id:
                return json.dumps(
                    {
                        "status": "need_group_or_user_id",
                        "message": "关系网里没有唯一确认这个称呼；请补充目标所在群号/群名，或直接提供用户 ID。",
                    },
                    ensure_ascii=False,
                )
            if not resolved.get("user_id") and not resolved.get("ambiguous"):
                resolved = await self._resolve_atrelay_target_user(event, group_id, target_user)
            if resolved.get("ambiguous"):
                return json.dumps(
                    {
                        "status": "ambiguous",
                        "message": "匹配到多个用户，请补充用户 ID",
                        "matches": resolved.get("matches", [])[:8],
                    },
                    ensure_ascii=False,
                )
            target_user = _single_line(resolved.get("user_id"), 128)
            if not target_user:
                return json.dumps({"status": "not_found", "message": "未找到私聊目标"}, ensure_ascii=False)
        send_text = await self._rewrite_atrelay_message_with_llm(
            event,
            destination="private",
            recipient_hint=target_user,
            text=text,
            relay_mode=relay_mode,
        )
        send_text = self._normalize_atrelay_text(send_text, limit=800)
        result = await self._pc_send_to_private_user_impl(
            event,
            user_id=target_user,
            message=send_text,
            relay_mode=relay_mode,
            sensitive_confirmed=sensitive_confirmed,
            need_receipt=need_receipt,
            confirm_before_report=confirm_before_report,
            receipt_expire_hours=expire_hours,
        )
        ok = result.startswith("已向")
        if ok:
            setattr(
                event,
                "private_companion_atrelay_tool_result",
                {
                        "status": "success",
                        "destination": "private",
                        "final_reply": "带到了。" if not need_receipt else "带到了，有回复我再告诉你。",
                        "final_reply_reference": (
                            "参考意图：转述已经成功发给目标私聊用户，并且如果对方回复会再告诉当前用户；只给一个很短的成功回执。"
                            if need_receipt
                            else "参考意图：转述已经成功发给目标私聊用户；只给用户一个很短的成功回执，不要复述转述正文，也不要写工具执行状态。"
                        ),
                        "sent_text": send_text,
                        "recipient": target_user,
                    },
                )
        return json.dumps(
            {
                "status": "success" if ok else "error",
                "message": "带到了，有回复我再告诉你。" if ok and need_receipt else ("带到了。" if ok else result),
                "final_reply": "带到了，有回复我再告诉你。" if ok and need_receipt else ("带到了。" if ok else ""),
                "final_reply_reference": (
                    "参考意图：转述已经成功发给目标私聊用户，并且如果对方回复会再告诉当前用户；只给一个很短的成功回执。"
                    if ok and need_receipt
                    else (
                        "参考意图：转述已经成功发给目标私聊用户；只给用户一个很短的成功回执，不要复述转述正文，也不要写工具执行状态。"
                        if ok
                        else ""
                    )
                ),
                "sent_text": send_text if ok else "",
            },
            ensure_ascii=False,
        )

    async def _pc_send_to_group_impl(self, event: AstrMessageEvent, **kwargs) -> str:
        if not self.enable_atrelay_tools:
            return "发送失败：跨群转述工具未启用"
        authorized, _requester_id = self._atrelay_tool_authorization(event)
        if not authorized:
            return "发送失败：跨会话转述仅允许主人使用"
        group_id = kwargs.get("group_id") or kwargs.get("group") or kwargs.get("target_group") or ""
        message = kwargs.get("message") or kwargs.get("text") or kwargs.get("content") or kwargs.get("msg") or ""
        at_user = kwargs.get("at_user") or kwargs.get("at") or kwargs.get("target_user") or kwargs.get("user_id") or ""
        at_qq_list = kwargs.get("at_qq_list") or kwargs.get("at_users") or kwargs.get("at_list")
        if not at_user and isinstance(at_qq_list, list) and at_qq_list:
            at_user = str(at_qq_list[0])
        relay_mode = kwargs.get("relay_mode") or kwargs.get("mode") or ""
        sensitive_confirmed = kwargs.get("sensitive_confirmed", kwargs.get("confirmed", False))
        target_group = self._normalize_atrelay_group_target_id(group_id)
        group_guard = self._atrelay_target_group_allowed(target_group, event)
        if group_guard:
            return group_guard
        text = self._normalize_atrelay_text(message, limit=800)
        relay_mode_normalized = self._normalize_atrelay_relay_mode(relay_mode)
        if not target_group:
            return "发送失败：群 ID 格式不正确"
        if not text:
            return "发送失败：消息内容为空"
        boundary = self._atrelay_boundary_guard(text)
        if boundary:
            return boundary
        duplicate = self._atrelay_duplicate_guard("group", target_group, text, at_user)
        if duplicate:
            return duplicate
        guard = self._atrelay_confirmation_guard(
            text,
            relay_mode=relay_mode_normalized,
            sensitive_confirmed=self._atrelay_bool_flag(sensitive_confirmed) or self._atrelay_event_confirms_sensitive_send(event),
        )
        if guard:
            return guard
        at_qq = ""
        at_label = ""
        if _single_line(at_user, 60):
            resolved = await self._resolve_atrelay_target_user(event, target_group, at_user)
            if resolved.get("ambiguous"):
                names = "、".join(_single_line(item.get("name") or item.get("relation_name") or item.get("nickname") or item.get("user_id"), 30) for item in resolved.get("matches", [])[:5] if isinstance(item, dict))
                return f"发送失败：@ 对象不唯一，请补充 QQ。候选：{names or '多个成员'}"
            at_qq = _single_line(resolved.get("user_id"), 40)
            at_label = _single_line(resolved.get("name"), 60)
            if not at_qq:
                return "发送失败：未找到要 @ 的群友"
            resting = self._atrelay_target_resting_reason(at_qq)
            if resting:
                return f"发送失败：{resting}，不会在群里继续 @ 打扰；可以改用延迟转述，等对方出现时再说。"
        chain: list[Any] = []
        if at_qq:
            chain.extend([At(qq=at_qq), Plain(" ")])
        chain.append(Plain(text))
        ok, error, used_umo = await self._send_atrelay_chain_to_target(
            event,
            message_type="group",
            target_id=target_group,
            chain=chain,
        )
        if not ok:
            logger.warning(
                "跨群转述发送失败: group=%s at=%s error=%s",
                target_group,
                at_qq or at_user or "-",
                _single_line(error, 240),
            )
            return f"发送失败：{_single_line(error, 180)}"
        self._note_atrelay_send("group", target_group, text, at_qq or at_user, event=event)
        self._save_data_sync(sections={"recent_atrelay_contexts", "atrelay_send_log"})
        logger.info(
            "跨群转述发送完成: group=%s at=%s umo=%s",
            target_group,
            at_qq or at_user or "-",
            _single_line(used_umo, 160),
        )
        return f"消息已发送到群 {target_group}" + (f", 已 @ {at_label or at_qq}" if at_qq else "")

    async def _pc_send_to_private_user_impl(self, event: AstrMessageEvent, **kwargs) -> str:
        if not self.enable_atrelay_tools:
            return "发送失败：跨群转述工具未启用"
        authorized, _requester_id = self._atrelay_tool_authorization(event)
        if not authorized:
            return "发送失败：跨会话转述仅允许主人使用"
        user_id = kwargs.get("user_id") or kwargs.get("qq") or kwargs.get("target_user") or kwargs.get("target") or ""
        message = kwargs.get("message") or kwargs.get("text") or kwargs.get("content") or kwargs.get("msg") or ""
        relay_mode = kwargs.get("relay_mode") or kwargs.get("mode") or ""
        sensitive_confirmed = kwargs.get("sensitive_confirmed", kwargs.get("confirmed", False))
        need_receipt = self._atrelay_bool_flag(
            kwargs.get("need_receipt", kwargs.get("wait_for_reply", kwargs.get("receipt", kwargs.get("report_back", False))))
        )
        confirm_before_report = self._atrelay_bool_flag(
            kwargs.get("confirm_before_report", kwargs.get("require_reply_confirmation", kwargs.get("confirm_reply", False)))
        )
        receipt_expire_hours = kwargs.get("receipt_expire_hours", kwargs.get("expire_hours", kwargs.get("ttl_hours", 12)))
        target_user = self._normalize_atrelay_private_target_id(user_id)
        text = self._normalize_atrelay_text(message, limit=800)
        relay_mode_normalized = self._normalize_atrelay_relay_mode(relay_mode)
        if not target_user:
            return "发送失败：目标用户 ID 无效或尚未登记"
        if not text:
            return "发送失败：消息内容为空"
        boundary = self._atrelay_boundary_guard(text)
        if boundary:
            return boundary
        resting = self._atrelay_target_resting_reason(target_user)
        if resting:
            return f"私聊发送失败：{resting}，不会私聊叫醒；可以改成延迟转述或等对方醒来后再发。"
        duplicate = self._atrelay_duplicate_guard("private", target_user, text)
        if duplicate:
            return duplicate
        guard = self._atrelay_confirmation_guard(
            text,
            relay_mode=relay_mode_normalized,
            sensitive_confirmed=self._atrelay_bool_flag(sensitive_confirmed) or self._atrelay_event_confirms_sensitive_send(event),
        )
        if guard:
            return guard
        ok, error, used_umo = await self._send_atrelay_chain_to_target(
            event,
            message_type="private",
            target_id=target_user,
            chain=[Plain(text)],
        )
        if not ok:
            logger.warning(
                "私聊转述发送失败: user=%s error=%s",
                target_user,
                _single_line(error, 240),
            )
            return f"私聊发送失败：{_single_line(error, 180)}"
        self._note_atrelay_send("private", target_user, text, event=event)
        if need_receipt:
            self._note_atrelay_private_receipt_task(
                event,
                target_user=target_user,
                question=text,
                sent_text=text,
                confirm_before_report=confirm_before_report,
                expire_hours=receipt_expire_hours,
            )
        self._save_data_sync(sections={"pending_atrelay_receipts", "recent_atrelay_contexts", "atrelay_send_log"})
        logger.info(
            "私聊转述发送完成: user=%s umo=%s",
            target_user,
            _single_line(used_umo, 160),
        )
        return f"已向 {target_user} 发送私聊消息" + ("，会等待对方回复后带回回执" if need_receipt else "")

    async def _pc_send_to_groups_impl(self, event: AstrMessageEvent, **kwargs) -> str:
        group_ids = kwargs.get("group_ids") or kwargs.get("groups") or kwargs.get("group_id") or kwargs.get("targets") or ""
        message = kwargs.get("message") or kwargs.get("text") or kwargs.get("content") or kwargs.get("msg") or ""
        at_user = kwargs.get("at_user") or kwargs.get("at") or kwargs.get("target_user") or kwargs.get("user_id") or ""
        relay_mode = kwargs.get("relay_mode") or kwargs.get("mode") or ""
        sensitive_confirmed = kwargs.get("sensitive_confirmed", kwargs.get("confirmed", False))
        targets = []
        for item in self._parse_atrelay_target_list(group_ids, limit=self.atrelay_multi_target_limit):
            target = self._normalize_atrelay_group_target_id(item)
            if target and target not in targets:
                targets.append(target)
        if not targets:
            return "发送失败：没有有效群 ID"
        results = []
        for group_id in targets:
            result = await self._pc_send_to_group_impl(
                event,
                group_id=group_id,
                message=message,
                at_user=at_user,
                relay_mode=relay_mode,
                sensitive_confirmed=sensitive_confirmed,
            )
            results.append(f"{group_id}: {result}")
        return "多群通知完成：\n" + "\n".join(results[: self.atrelay_multi_target_limit])

    async def _pc_send_to_private_users_impl(self, event: AstrMessageEvent, **kwargs) -> str:
        user_ids = kwargs.get("user_ids") or kwargs.get("users") or kwargs.get("user_id") or kwargs.get("targets") or ""
        message = kwargs.get("message") or kwargs.get("text") or kwargs.get("content") or kwargs.get("msg") or ""
        relay_mode = kwargs.get("relay_mode") or kwargs.get("mode") or ""
        sensitive_confirmed = kwargs.get("sensitive_confirmed", kwargs.get("confirmed", False))
        targets = []
        for item in self._parse_atrelay_target_list(user_ids, limit=self.atrelay_multi_target_limit):
            target = self._normalize_atrelay_private_target_id(item)
            if target and target not in targets:
                targets.append(target)
        if not targets:
            return "发送失败：没有有效私聊目标用户 ID"
        results = []
        for user_id in targets:
            result = await self._pc_send_to_private_user_impl(
                event,
                user_id=user_id,
                message=message,
                relay_mode=relay_mode,
                sensitive_confirmed=sensitive_confirmed,
            )
            results.append(f"{user_id}: {result}")
        return "多人转述完成：\n" + "\n".join(results[: self.atrelay_multi_target_limit])

    async def _pc_schedule_group_relay_impl(self, event: AstrMessageEvent, **kwargs) -> str:
        if not self.enable_atrelay_tools:
            return "挂起失败：跨群转述工具未启用"
        authorized, _requester_id = self._atrelay_tool_authorization(event)
        if not authorized:
            return "挂起失败：跨会话转述仅允许主人使用"
        group_id = kwargs.get("group_id") or kwargs.get("group") or kwargs.get("target_group") or ""
        at_user = kwargs.get("at_user") or kwargs.get("target_user") or kwargs.get("user_id") or kwargs.get("name") or kwargs.get("nickname") or ""
        message = kwargs.get("message") or kwargs.get("text") or kwargs.get("content") or kwargs.get("msg") or ""
        relay_mode = kwargs.get("relay_mode") or kwargs.get("mode") or ""
        sensitive_confirmed = kwargs.get("sensitive_confirmed", kwargs.get("confirmed", False))
        expire_hours = kwargs.get("expire_hours", kwargs.get("ttl_hours", 24))
        target_group = self._normalize_atrelay_group_target_id(group_id) or self._extract_group_id_from_event(event)
        group_guard = self._atrelay_target_group_allowed(target_group, event)
        if group_guard:
            return group_guard.replace("发送失败", "挂起失败", 1)
        text = self._normalize_atrelay_text(message, limit=800)
        if not target_group:
            return "挂起失败：群 ID 格式不正确"
        if not text:
            return "挂起失败：消息内容为空"
        boundary = self._atrelay_boundary_guard(text)
        if boundary:
            return boundary.replace("发送失败", "挂起失败", 1)
        relay_mode_normalized = self._normalize_atrelay_relay_mode(relay_mode)
        guard = self._atrelay_confirmation_guard(
            text,
            relay_mode=relay_mode_normalized,
            sensitive_confirmed=self._atrelay_bool_flag(sensitive_confirmed) or self._atrelay_event_confirms_sensitive_send(event),
        )
        if guard:
            return guard.replace("不能直接转述", "不能直接挂起转述")
        resolved = await self._resolve_atrelay_target_user(event, target_group, at_user)
        if resolved.get("ambiguous"):
            names = "、".join(
                _single_line(item.get("name") or item.get("relation_name") or item.get("nickname") or item.get("user_id"), 30)
                for item in resolved.get("matches", [])[:5]
                if isinstance(item, dict)
            )
            return f"挂起失败：目标不唯一，请补充 QQ。候选：{names or '多个成员'}"
        target_user = _single_line(resolved.get("user_id"), 40)
        target_name = _single_line(resolved.get("name"), 60) or target_user
        if not target_user:
            return "挂起失败：未找到目标群友"
        now = _now_ts()
        expire_seconds = max(1, min(168, _safe_int(expire_hours, 24, 1, 168))) * 3600
        source_user, source_name = self._atrelay_source_snapshot_for_event(event)
        async with self._data_lock:
            group = self._get_group(target_group)
            tasks = group.setdefault("pending_atrelay_tasks", [])
            if not isinstance(tasks, list):
                tasks = []
                group["pending_atrelay_tasks"] = tasks
            signature = self._atrelay_send_signature("delayed_group", target_group, text, target_user)
            for task in tasks:
                if isinstance(task, dict) and task.get("signature") == signature and _safe_float(task.get("expires_at"), 0) > now:
                    return f"已存在相同延迟转述：等 {target_name} 在群 {target_group} 出现时发送"
            tasks.append(
                {
                    "id": uuid.uuid4().hex[:12],
                    "created_at": now,
                    "expires_at": now + expire_seconds,
                    "target_user_id": target_user,
                    "target_name": target_name,
                    "message": text,
                    "source_user": source_user,
                    "source_name": source_name,
                    "relay_mode": relay_mode_normalized,
                    "sensitive_confirmed": self._atrelay_bool_flag(sensitive_confirmed) or self._atrelay_event_confirms_sensitive_send(event),
                    "signature": signature,
                }
            )
            del tasks[:-30]
            self._save_data_sync(sections={"groups"})
        return f"已挂起：等 {target_name} 在群 {target_group} 出现时转述"
