# -*- coding: utf-8 -*-
"""生图提示与当前媒体域。

由 tools/split_mixin_domain.py 从 llm_tool_actions.py 机械抽取（24 个方法 + 8 个模块级名字 + 0 个类级赋值 / 1034 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 LlmToolActionsMixin）。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import time
import uuid
from .conversation_prompt_section import PromptSection, prompt_section
from .helpers import _redact_outbound_secrets, _single_line, _strip_internal_message_blocks
from .llm_tool_actions_shared import (
    PHOTO_TOOL_SILENT_SENTINEL,
    _render_tool_prompt_section_labeled,
    _render_tool_prompt_section_labeled_inline,
    logger,
)
from .persona_config import runtime_persona_setting
from .reaction_expression import (
    reaction_expression_explicit_opt_out,
    reaction_expression_explicit_request,
    reaction_expression_high_frequency,
    reaction_expression_normalize_probability,
)
from astrbot.api.event import AstrMessageEvent
from astrbot.core.utils.astrbot_path import get_astrbot_data_path
from pathlib import Path
from typing import Any



_PHOTO_TOOL_REDACTED_LOCAL_PATH = "[本地路径已隐藏]"

_PHOTO_TOOL_WINDOWS_PATH_START_RE = re.compile(
    r"(?<!\w)(?:[A-Za-z]:[\\/]|\\\\(?=[^\\/]))"
)

_PHOTO_TOOL_POSIX_PATH_START_RE = re.compile(
    r"(?<![\w/])/(?=(?:"
    r"(?:Users|home|tmp|var|etc|opt|srv|root|mnt|run|private|usr|workspace|workspaces|app|data)/"
    r"|(?:[^/\s]+/){2,}[^/\s]+"
    r"|[^/\s]+/[^/\s]+\.[A-Za-z0-9]{1,12}(?:\s|$|[),;，；。])"
    r"))",
    flags=re.I,
)

_PHOTO_TOOL_RELATIVE_PATH_START_RE = re.compile(
    r"(?<![A-Za-z0-9.:/\\])(?:\.{1,2}[\\/])?(?:[^\\/\s,，;；]+[\\/]){2,}"
    r"[^\\/\s,，;；]+\.[A-Za-z0-9]{1,12}",
    flags=re.I,
)

_PHOTO_TOOL_HTTP_URL_RE = re.compile(
    r"https?://[^\s<>\[\]{}\"']+",
    flags=re.I,
)

_CURRENT_MEDIA_IMAGE_SUFFIXES = frozenset(
    {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".jfif", ".avif"}
)

_CURRENT_MEDIA_MAX_BYTES = 32 * 1024 * 1024

_CURRENT_MEDIA_MAX_AGE_SECONDS = 30 * 60


class LlmToolActionsPhotoPromptMixin:
    """生图提示与当前媒体域（从 LlmToolActionsMixin 拆出）。"""


    @staticmethod
    def _character_photo_request_matches(text: Any) -> bool:
        compact = re.sub(r"\s+", "", str(text or ""))
        if not compact:
            return False
        if any(
            marker in compact
            for marker in (
                "腿照",
                "脚照",
                "手照",
                "全身照",
                "半身照",
                "近照",
                "生活照",
                "穿搭照",
            )
        ):
            return True
        return bool(
            re.search(
                r"(?:看看|看下|看一下|想看|要看|让我看看|给我看看|发来看看).{0,10}"
                r"(?:腿|脚|手|脸|全身|半身|穿搭|衣服|样子)",
                compact,
                flags=re.I,
            )
        )

    def _photo_generation_instruction_matches(self, text: Any) -> bool:
        compact = re.sub(r"\s+", "", str(text or ""))
        if not compact:
            return False
        clauses = [
            part
            for part in re.split(
                r"(?:[，,。！？!?；;]+|但是|不过|然而|然后)", compact
            )
            if part
        ] or [compact]
        non_reaction_tokens = (
                "生图",
                "画图",
                "绘图",
                "生成图片",
                "出图",
                "画一张",
                "画张",
                "来张图",
                "来一张图",
                "自拍",
                "拍照",
                "照片",
                "相片",
                "头像",
                "壁纸",
                "改图",
                "修图",
                "重绘",
                "P图",
                "p图",
                "参考图",
                "穿搭图",
                "COS",
                "cosplay",
        )
        generated_reaction = re.compile(
            r"(?:生成|制作|做|画|绘制|设计|重绘).{0,10}"
            r"(?:表情包|贴纸|反应图|梗图)"
            r"|(?:表情包|贴纸|反应图|梗图).{0,10}"
            r"(?:生成|制作|做|画|绘制|设计|重绘)",
            flags=re.I,
        )
        rejected_generation = re.compile(
            r"(?:别|不要|不用).{0,8}(?:生成|制作|做|画|绘制|设计|重绘).{0,10}"
            r"(?:表情包|贴纸|反应图|梗图)",
            flags=re.I,
        )
        for clause in clauses:
            if reaction_expression_explicit_opt_out(clause):
                continue
            if self._character_photo_request_matches(clause):
                return True
            if any(token in clause for token in non_reaction_tokens):
                return True
            if rejected_generation.search(clause):
                continue
            if reaction_expression_explicit_request(clause):
                return True
            if generated_reaction.search(clause):
                return True
            if clause in {"斗图", "来斗图", "开始斗图"}:
                return True
        return False

    def _plaintext_photo_recovery_intent_matches(self, text: Any) -> bool:
        compact = re.sub(r"\s+", "", str(text or ""))
        if not compact:
            return False
        explicit_request = bool(
            re.search(
                r"(?:帮我|给我|替我|想要|想看|要看|拍|生成|画|绘制|做|来|发).{0,10}"
                r"(?:照片|图片|自拍|头像|表情包|贴纸|壁纸|穿搭|腿|脚|手|脸|全身|半身)",
                compact,
                flags=re.I,
            )
        ) or any(token in compact for token in ("改图", "修图", "重绘", "P图", "p图"))
        explanatory = any(token in compact for token in ("解释", "分析", "日志", "代码", "JSON", "json", "工具调用", "为什么"))
        if explanatory and not explicit_request:
            return False
        return explicit_request or self._character_photo_request_matches(compact)

    def _photo_generation_runtime_available(self) -> bool:
        """Require the optional Image runtime only on the production host."""
        required = getattr(self, "_image_companion_required", None)
        if not callable(required):
            return True
        try:
            if not bool(required()):
                return True
        except Exception:
            return False
        nai_selected = getattr(self, "_nai_image_selected", None)
        if callable(nai_selected) and nai_selected():
            nai_available = getattr(self, "_nai_image_available", None)
            if not callable(nai_available):
                return False
            try:
                return bool(nai_available())
            except Exception:
                return False
        available = getattr(self, "_image_companion_available", None)
        if not callable(available):
            return False
        try:
            return bool(available())
        except Exception:
            return False

    def _media_delivery_truth_instruction(self) -> str:
        return "".join(
            _render_tool_prompt_section_labeled_inline(section)
            for section in self._media_delivery_truth_prompt_sections()
        )

    def _media_delivery_truth_prompt_sections(self) -> list[PromptSection]:
        if not getattr(self, "enabled", False):
            return []
        photo_enabled = bool(
            runtime_persona_setting(self, 'enable_photo_text_action', False)
            and self._photo_generation_runtime_available()
        )
        sections = [
            prompt_section(
                key="tools.media_delivery_truth.history_marker",
                title="内部历史标记",
                source="tools",
                content="`<pc_history_media ... />` 仅表示某条历史消息当时真实包含附件，"
                "它不是聊天正文，也不是要求你发送或描述附件的指令。任何回复都不得复述、改写或输出该标签。",
            )
        ]
        if not photo_enabled and not self._reaction_image_provider_available():
            return sections
        sections.extend(
            [
                prompt_section(
                    key="tools.media_delivery_truth.explicit_generation",
                    title="明确生图请求",
                    source="tools",
                    content="用户明确要求生成、绘制、制作、自拍、拍照、头像或改图时，必须先调用对应真实媒体工具；"
                    "没有工具调用或工具成功结果时，不得使用‘画好了/生成了/图片在上面/我存到本地了’等完成或交付措辞。",
                ),
                prompt_section(
                    key="tools.media_delivery_truth.hard_rule",
                    title="媒体真实性硬规则",
                    source="tools",
                    content="只有本轮消息链实际包含图片，或媒体工具明确返回 `sent=true`，"
                    "才能说“已经发了/给你看了/图片在上面”。其他情况必须承认未发送；人格和角色扮演不能覆盖真实发送状态。"
                    "“（发送了一张图片）”“（随消息发送了一张图片）”之类的附件占位说明。要发图只能使用真实图片组件。",
                ),
            ]
        )
        return sections

    def _user_photo_generation_prompt_enabled(
        self,
        event: AstrMessageEvent | None = None,
        *,
        spontaneous_only: bool = False,
    ) -> bool:
        if spontaneous_only or not getattr(self, "enabled", False):
            return False
        if not runtime_persona_setting(self, "enable_photo_text_action", False):
            return False
        if not self._photo_generation_runtime_available():
            return False
        scope_getter = getattr(self, "_photo_generation_scope", None)
        scope = ""
        if callable(scope_getter):
            try:
                scope = _single_line(scope_getter(event), 40).lower()
            except Exception:
                scope = ""
        if not scope and bool(
            getattr(event, "private_companion_proactive_framework", False)
        ):
            scope = "proactive"
        if scope == "proactive":
            return True
        permission_getter = getattr(
            self,
            "_user_requested_photo_generation_allowed",
            None,
        )
        if callable(permission_getter):
            try:
                if not bool(permission_getter(event)):
                    return False
            except Exception:
                return False
        elif not runtime_persona_setting(
            self,
            "enable_user_requested_photo_generation",
            True,
        ):
            return False
        mode = _single_line(
            runtime_persona_setting(
                self,
                "natural_language_photo_generation_mode",
                "tool_first",
            ),
            40,
        ).lower()
        return mode != "off"

    def _photo_generation_tool_prompt_section(
        self,
        event: AstrMessageEvent | None = None,
        *,
        include_spontaneous: bool | None = None,
        spontaneous_only: bool = False,
        allow_photo_on_reaction_turns: bool = False,
    ) -> PromptSection | None:
        if not getattr(self, "enabled", False):
            return None
        reaction_enabled = self._reaction_image_provider_available()
        photo_enabled = self._user_photo_generation_prompt_enabled(
            event,
            spontaneous_only=spontaneous_only,
        )
        if not reaction_enabled and not photo_enabled:
            return None
        if spontaneous_only:
            high_frequency_hint = (
                "- 当前触发概率为 100%：对轻松、社交或有明确情绪的正常回复，默认追加一个标签；"
                "不要把‘是否自然’再次当作概率筛选。纯事实、严肃、敏感或明确边界场景仍只发正文。"
                if reaction_expression_high_frequency(
                    runtime_persona_setting(self, 'reaction_expression_trigger_probability', 0.2)
                )
                else "- 轻松闲聊、玩笑、安慰、撒娇、庆祝、惊讶、接梗、轻吐槽，或‘收到/好的/笑死’这类语义明确的短回应，通常应在完整回复末尾追加内部标签。只有纯事实答复、严肃或敏感情境，或确实没有合适情绪时才省略。"
            )
            media_tool_boundary = (
                "不要使用 Markdown 代码块，不要解释标签，也不要调用图片或生图工具。"
                if not allow_photo_on_reaction_turns
                else "不要使用 Markdown 代码块，不要解释标签。"
            )
            spontaneous_lines = [
                    "- 先完成一条正常、完整、可以独立发送的文字回复。表情图片只能作为文字后的补充，绝对不能替代文字回复。",
                    "- 本轮已经由插件完成概率抽样并获得一次表情表达机会；不要再次按概率决定，也不要因为‘不确定’而默认省略标签。",
                    high_frequency_hint,
                    '-最小标签格式为 `<pc_reaction_expression>{"purpose":"轻吐槽","emotion":"无语","intensity":2}</pc_reaction_expression>`。',
                    "- `purpose` 写沟通用途，`emotion` 写希望传达的情绪，`intensity` 为 0-5；需要帮助检索时可选填 `candidate_queries`，提供 1-3 个简短说法。不要填写图片路径。",
                    f"- 每轮最多写一个标签，必须放在全部可见文字和 TTS 标签之后；{media_tool_boundary}",
                    "- 即使图库最终没有匹配、图片重复或发送失败，前面的完整文字也必须仍然自然成立。",
                ]
            if allow_photo_on_reaction_turns:
                spontaneous_lines.append(
                    "- 用户本轮明确提出“看看你/看照片/看自拍/看穿搭/展示穿着”等看图请求时，可以直接调用 `pc_generate_photo`（工具已在请求中提供），把完整正文写进 `caption` 随图发送；调用生图工具时不要再额外写表情标签。"
                )
                spontaneous_lines.append(
                    "- 没有明确看图请求时，不得主动调用 `pc_generate_photo`，只写文字或表情标签。"
                )
            return prompt_section(
                key="tools.reaction_expression",
                title="实验性表情表达",
                source="tools",
                content="\n".join(spontaneous_lines).strip(),
            )
        lines: list[str] = []
        # Only describe the gallery when its runtime provider is actually usable.
        if reaction_enabled and not spontaneous_only:
            reaction_availability = (
                "- 表情包素材库当前已有可用素材，用户请求现成表情包或反应图时可直接调用 `pc_find_reaction_image` 检索。"
                if reaction_enabled
                else "- 表情包素材库可能暂无可用的现成素材，用户仍可尝试调用 `pc_find_reaction_image` 检索；库为空时工具会返回对应提示。"
            )
            raw_probability = runtime_persona_setting(
                self, 'reaction_expression_trigger_probability', 0.2
            )
            if reaction_expression_high_frequency(raw_probability):
                spontaneous_hint = (
                    "- 自动追加表情包目前为高频触发：对轻松、社交或有明确情绪的动作/表情描述回复"
                    "（如[委屈巴巴地缩了缩脖子]、[开心地蹦跶了两下]），默认在正文后调用 `pc_find_reaction_image` "
                    "追加一个匹配表情包；纯事实、严肃、敏感或明确边界场景仍只发文字，不追加。"
                )
            else:
                chance = reaction_expression_normalize_probability(raw_probability, 0.2)
                spontaneous_hint = (
                    f"- 自动追加表情包是低概率点缀而非每轮默认动作：当前配置下约 {int(round(chance * 100))}% 的情境才自然带一个匹配表情包。"
                    "若回复中出现动作或表情描述（如[委屈巴巴地缩了缩脖子]、[开心地蹦跶了两下]），是典型的追加时机，"
                    "但请按上述概率自然把握：不要每轮都加，也不要因偶尔没加而向用户解释。"
                )
            lines.extend(
                [
                    reaction_availability,
                    "- 用户要“找/发/来一张已有表情包”、要用现成反应图回应当前语境时，优先使用 `pc_find_reaction_image`，把需求和当前语境写进 `query/search_context`。",
                    "- `pc_find_reaction_image` 在 `send=true` 时必须填写 `caption`，内容应是一条完整、自然、可独立成立的正文；图片只能追加在正文后，不能替代、缩短或省略正文。",
                    "- 决定调用 `pc_find_reaction_image` 时，把可见正文只放进 `caption` 参数；发起工具调用的同一轮不要再额外输出正文或声称图片已经发送。拿到工具结果后再按结果完成最终回复。",
                    "- 图库未匹配时可以自然改用文字回应，不要擅自声称已发图。",
                    spontaneous_hint,
                ]
            )
        if reaction_enabled:
            experiment_enabled = bool(
                runtime_persona_setting(self, 'enable_reaction_expression_experiment', False)
            )
            spontaneous_enabled = experiment_enabled and (
                include_spontaneous is not False
            )
            if spontaneous_enabled:
                lines.extend(
                    [
                        "- 普通闲聊中，先生成一条完整、可独立成立的文字回复；只有在正文之后追加表情图能明显补足语气、且符合本轮关系边界时，才可把 `spontaneous=true` 调用 `pc_find_reaction_image`。图片不能替代、缩短或省略正文；不要每轮调用，不确定时只保留自然文字回复。",
                        "- 发起自发表情工具调用时，把这条完整正文只放进 `caption` 参数，同一工具调用轮不要再额外输出正文或提前描述发送结果；等待工具结果后再完成最终回复。",
                        "- 自发表情调用应填写 `purpose`（沟通用途）、`emotion`（想传达的情绪）、`intensity`（0-5）与 `candidate_queries`（少量候选检索说法）；这些是本轮结构化表达意图，不要另行解释给用户。",
                        "- 自发表情允许因概率、冷却、重复或图库不匹配而返回 `decision=skip`。遇到跳过时不要解释内部原因，继续按原语境自然文字回复即可。",
                    ]
                )
        if photo_enabled:
            lines.append(
                "- 只有用户明确要求“生成/画/制作”新的角色表情包或贴纸时，才使用 `pc_generate_photo(kind=\"sticker\")`。不要把普通的现成表情包请求误当成生图。"
            )
            lines.extend(
                [
                    "- 用户明确要求生成图片、画图、出图、自拍、拍照、头像，或要求基于参考图改图时，可以使用 `pc_generate_photo`。",
                    '- 普通场景/物件/风景：仅当画面中不出现角色本人时，传 `{"prompt":"画面描述","kind":"text2img"}`，可用 `scene_preset` 建议“可拍画面/房间日常”；该字段只是建议，不会覆盖用户原话或参考图约束。把它写成角色镜头看到的画面，不要擅自加入拍摄者、陌生女孩或人物背影。纯梗图或无角色贴纸才用 `text2img + scene_preset="表情包场景"`。',
                    '- 角色本人以任何形式出镜，包括自拍、背影、侧脸、环境人像、头像、穿搭或 COS：传 `{"prompt":"画面要求","kind":"selfie"}`，可用 `scene_preset` 建议“角色自拍/COS自拍/日常穿搭/居家睡衣/镜前穿搭/头像特写”；明确睡衣、睡裙、睡袍或睡前卧室自拍时优先建议“居家睡衣”，普通穿搭才建议“日常穿搭”，只有明确“镜前/对镜/镜子”时才建议镜前穿搭；最终只采用一个兼容预设。只有开启参考图一致性时，未传参考图才会自动使用配置的人设参考图或今日穿搭参考图。',
                    '- 自拍也应延续角色此刻的生活状态。结合本轮已有的当前日程、位置和对话判断：如果角色正在上课、通勤或处理别的事，而用户想看海边、旅行地等明显不在当前现场的自拍，优先保持生活连续性，不要让角色像瞬间换了地点。用户只是想看这类画面时，通常可以自然理解为分享之前拍的、相册里的照片；仍可调用 `pc_generate_photo`，在 prompt 中说明按此前拍摄的照片呈现，并在 `caption` 里用角色口吻轻轻交代来源。',
                    '- 这不是固定拒绝规则。当前状态没有明显冲突、用户是在延续刚才的拍摄情境，或语境本来就是设想/COS/创作时，可以照常生成；只有用户明确强调“现在、立刻、现场拍”且与当前活动明显不合适时，再自然商量晚点拍。不要向用户复述内部日程判断或规则。',
                    '- 用户引用上一张角色照片并要求“比个心、看镜头、换个动作/表情/角度、再来一张”等自然续拍时，仍使用 `kind="selfie"`，并在 prompt 中说明只改变这次要求的部分、其余人物穿搭与场景继续保持；工具会读取本轮引用图片，不要猜测或手填图片路径。若本轮没有引用或携带图片，则按普通新自拍处理，选图器不会自动复用上一张成图。明确换装、换地点、换人物或另起主题时按新要求生成。',
                    '- 合影、合照、双人或多人同框必须有可验证的其他人物参考图：优先使用本轮携带或引用的图片；若请求明确点名了已在 Bot 关系网角色卡中绑定可用参考图的角色，也可直接调用 `pc_generate_photo` 并让工具自动选图，不要填写或猜测路径。Bot 单人人设图、今日穿搭图和纯文字关系卡都不算其他人物参考，模型自行填写的本地路径/URL 也不能单独授权合影。两类参考来源都没有时不要调用生图，也不要凭文字捏造另一张脸；可以说明需要先为该角色绑定参考图，或让用户发送/引用人物图片。',
                    '- 如果前几轮文字剧情已经明确让角色换装，而本轮只说“继续、再拍一张、保持刚才的穿搭”等，不要把它理解成恢复今日穿搭。必须把仍有效的具体服装展开写进 prompt，例如“角色当前仍穿 JK 校服，保持本轮地点和人物连续性”；当前对话已发生的换装高于日程、人格默认衣着、每日穿搭参考图和旧图片。',
                    '- “JK”在服装语境下请规范写成“JK 校服/JK 制服”；只有用户明确改变服装时才替换连续状态，提问、假设或用户自己换衣不算角色已换装。',
                    '- 角色表情包/贴纸：传 `{"prompt":"表情和画面要求","kind":"sticker"}`；默认走自拍/人像链路并使用“表情包场景”预设，让角色仍可识别。',
                    '- 改图/重绘：当前消息或引用消息已经带图时，传 `{"prompt":"修改要求","kind":"edit"}`，不要猜测、抄写或回传本地临时路径，插件会从当前事件安全取图。只有明确使用公网图片 URL 或插件已管理的参考图时才传 `reference_image_path`；多图职责组合可传 `reference_image_paths` 数组，并在 prompt 中说明每张图承担的脸、衣服、姿势等职责。没有任何当前/引用/已管理参考图时不要调用改图。',
                    "- `pc_generate_photo` 会自行发送成图，调用它之后绝对不要再调用 `pc_send_current_media`。如果另一个生图工具或图像编辑工具已经生成或编辑图片并明确返回了本地图片路径、且结果没有确认图片已发送，必须立即把该路径交给 `pc_send_current_media` 投递一次；不得回答“没法直接发”“图片存好了以后再看”。",
                    "- `pc_send_current_media` 只承接本轮或紧邻上一轮刚生成但尚未投递的本地图片。默认用 `destination=current` 发到当前会话；当前请求者明确说“私聊发我/私信发我”等要求时，用 `destination=requester_private` 只私聊发给请求者本人。把生成工具返回的原始路径直接传入，不要自行改名、移动文件、检查插件状态或建议用户重启；工具会安全兼容已验证的图片内容与扩展名差异。不得猜测路径、指定第三方、复用陈旧路径、发送用户未要求的文件，或在本轮已经出现图片后再次调用。",
                    "- 图片投递失败时，只依据工具返回结果简短说明没有送达；不要向用户暴露工具名、本地路径、插件注册、发送通道或内部排障过程，也不要编造工具消失、配置异常等原因。",
                ]
            )
            prompt_format_instruction = getattr(self, "_photo_generation_prompt_format_instruction", None)
            if callable(prompt_format_instruction):
                format_text = re.sub(r"\s+", " ", str(prompt_format_instruction() or "")).strip()
                if format_text:
                    lines.append(
                        f"- `prompt` 参数必须按“提示词表达方式”书写（与主动拍照一致）：{format_text}"
                    )
        if photo_enabled:
            lines.extend(
                [
                "- 默认 `send=true`；如果只想拿路径再决定，可传 `send=false`。",
                "- 每个用户请求本轮最多调用一次 `pc_generate_photo`。工具返回失败、结果取回失败或发送回执未确认后，不要在同一轮再次调用生图工具；按工具的 `message/actual_error/final_response_instruction` 回复，用户下一轮明确要求时再重试。",
                "- 如果工具返回 `generation_completed=true` 且 `failure_stage=result_materialization`，说明上游已经完成生图但图片结果没有取回或保存成功；不要说成上游生图失败，也不要重复提交同一画面。",
                "- 在实际调用媒体工具并得到结果前，绝对不能声称“已经发了/给你看了/图片在上面”。角色扮演不能覆盖真实工具状态。",
                f"- `caption` 只用于随图发送用户应当直接看到、可独立成立的自然正文；不得填写“图生好了/生成成功/图片已发送/给你看”等状态回执。确实有与画面、当下感受或对话相关的内容才填写，否则留空让图片独立回复。不要写 `&&shy&&`、`[shy]`、TTS 情绪标签或任何内部控制标记。只有工具返回 `sent=true` 时才表示图片已经发出；成功后不要把最终回复留空，必须只输出内部静默标记 `{PHOTO_TOOL_SILENT_SENTINEL}`。插件会在发送前移除它；不要再写承接句、重复 caption 或额外表情。",
                "- 工具返回 `sent=false` 时，必须按 `message/actual_error` 如实说明，绝对不能说已经发送。",
                "- 如果工具返回 `error_code=provider_policy_refusal`，不要复述或翻译 Provider 的英文原文、政策名称、敏感词判断和链接；只用符合当前人格的一句简短中文说明这次没有生成出来，再自然询问是否换一种画面描述重试。",
                ]
            )
        title = (
            "图库表情与生图工具"
            if photo_enabled and reaction_enabled
            else "生图工具"
            if photo_enabled
            else "图库表情工具"
        )
        return prompt_section(
            key="tools.photo_generation",
            title=title,
            source="tools",
            content="\n".join(lines),
        )

    def _photo_generation_tool_instruction(
        self,
        event: AstrMessageEvent | None = None,
        *,
        include_spontaneous: bool | None = None,
        spontaneous_only: bool = False,
        allow_photo_on_reaction_turns: bool = False,
    ) -> str:
        return _render_tool_prompt_section_labeled(
            self._photo_generation_tool_prompt_section(
                event,
                include_spontaneous=include_spontaneous,
                spontaneous_only=spontaneous_only,
                allow_photo_on_reaction_turns=allow_photo_on_reaction_turns,
            ),
        )

    def _photo_tool_followup_is_redundant(self, sent_caption: Any, followup_text: Any) -> bool:
        """Only catch clear repeats of a caption already delivered with the image."""

        def compact(value: Any) -> str:
            text = _strip_internal_message_blocks(str(value or ""), enabled=bool(runtime_persona_setting(self, "enable_framework_error_leak_guard", True))).lower()
            return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", text)

        caption = compact(sent_caption)
        followup = compact(followup_text)
        if caption and caption == followup:
            return True
        if len(caption) < 6 or len(followup) < 6:
            return False
        shorter, longer = sorted((caption, followup), key=len)
        return shorter in longer and len(shorter) / max(1, len(longer)) >= 0.45

    def _sanitize_photo_tool_caption(self, value: Any, *, limit: int = 120) -> str:
        """Keep synthesis and internal control cues out of visible image captions."""
        if not bool(runtime_persona_setting(self, "enable_framework_error_leak_guard", True)):
            return _single_line(value, max(1, int(limit or 120)))
        cleaned = _strip_internal_message_blocks(
            str(value or ""),
            tts_enabled=bool(runtime_persona_setting(self, "enable_tts_enhancement", False)),
        )
        cleaned = re.sub(r"&&[A-Za-z_][A-Za-z0-9_ -]{0,31}&&", "", cleaned)
        cue_cleaner = getattr(self, "_strip_visible_tts_emotion_cues", None)
        if bool(runtime_persona_setting(self, "enable_tts_enhancement", False)) and callable(cue_cleaner):
            cleaned = cue_cleaner(cleaned)
        return _single_line(cleaned, max(1, int(limit or 120)))

    @staticmethod
    def _photo_caption_is_generic(value: Any) -> bool:
        text = re.sub(
            r"[\s。.!！?？,，；;:：、~～…\"'“”‘’（）()【】\[\]]+",
            "",
            str(value or ""),
        ).casefold()
        if not text:
            return True
        polite_tail = r"(?:啦|了|哦|噢|喔|呀|哈|呢)*"
        handoff_tail = (
            r"(?:(?:给|发|送)你(?:看|看看|了)?|"
            r"给你看(?:看)?|请查收)?" + polite_tail
        )
        return any(
            re.fullmatch(pattern, text)
            for pattern in (
                rf"(?:我)?(?:按(?:你|您)(?:的)?要求|按要求)?(?:这张)?"
                rf"(?:图|图片|照片|画面)(?:已经|已|刚刚)?"
                rf"(?:生|生成|画|绘制|改|修改|做|拍|处理|出)?"
                rf"(?:成功|好了?|完成|完毕|出来了?){handoff_tail}",
                rf"(?:我)?(?:按(?:你|您)(?:的)?要求|按要求)?(?:已经|已|刚刚)?"
                rf"(?:生图|出图|生成|画|绘制|改|修改|做好|做|拍|处理)"
                rf"(?:成功|好了?|完成|完毕|出来了?){handoff_tail}",
                rf"(?:图|图片|照片)?(?:已经|已)?(?:发送|发出|送达)"
                rf"(?:成功|完成|好了?)?{polite_tail}",
                rf"(?:已经|已)?(?:发|发送|送)给你{polite_tail}",
                rf"(?:给|发)(?:你)?(?:看|看看){polite_tail}",
                rf"(?:完成|完成了|好了|成功){polite_tail}",
            )
        )

    @staticmethod
    def _photo_generation_policy_refusal(value: Any) -> bool:
        """Recognize a provider refusal without judging the user's prompt locally."""
        normalized = re.sub(r"\s+", " ", str(value or "")).strip().casefold()
        if not normalized:
            return False
        refusal_markers = (
            "prompt could not be submitted",
            "prompt was not submitted",
            "try rephrasing the prompt",
            "request was rejected",
            "request was blocked",
            "内容政策拒绝",
            "内容策略拒绝",
            "安全策略拒绝",
            "请求被安全策略拦截",
        )
        policy_markers = (
            "generative ai prohibited use policy",
            "content policy violation",
            "sensitive words",
            "violates google's",
            "violates the policy",
            "policy violation",
            "不符合内容政策",
            "违反内容政策",
            "敏感词",
        )
        return any(marker in normalized for marker in refusal_markers) and any(
            marker in normalized for marker in policy_markers
        )

    def _sanitize_photo_tool_result_payload(
        self,
        value: Any,
        *,
        known_paths: tuple[Any, ...] = (),
    ) -> Any:
        """Remove local filesystem details from the model-visible tool receipt."""

        absolute_known_paths: list[str] = []
        for candidate in known_paths:
            text = str(candidate or "").strip()
            if not text:
                continue
            if text.lower().startswith(("http://", "https://", "data:")):
                continue
            if (
                _PHOTO_TOOL_WINDOWS_PATH_START_RE.match(text)
                or _PHOTO_TOOL_POSIX_PATH_START_RE.match(text)
                or (len(text) >= 3 and ("/" in text or "\\" in text))
            ):
                absolute_known_paths.append(text)
        absolute_known_paths.sort(key=len, reverse=True)

        def redact_text(raw: Any) -> str:
            cleaned = _redact_outbound_secrets(raw, self)
            protected_urls: dict[str, str] = {}

            def protect_url(match: re.Match[str]) -> str:
                token = f"PCPHOTOURL{uuid.uuid4().hex}TOKEN"
                protected_urls[token] = match.group(0)
                return token

            cleaned = _PHOTO_TOOL_HTTP_URL_RE.sub(protect_url, cleaned)
            for path in absolute_known_paths:
                cleaned = cleaned.replace(path, _PHOTO_TOOL_REDACTED_LOCAL_PATH)
            starts = [
                match.start()
                for pattern in (
                    _PHOTO_TOOL_WINDOWS_PATH_START_RE,
                    _PHOTO_TOOL_POSIX_PATH_START_RE,
                    _PHOTO_TOOL_RELATIVE_PATH_START_RE,
                )
                if (match := pattern.search(cleaned)) is not None
            ]
            if starts:
                prefix = cleaned[: min(starts)].rstrip()
                cleaned = f"{prefix} {_PHOTO_TOOL_REDACTED_LOCAL_PATH}".strip()
            for token, url in protected_urls.items():
                cleaned = cleaned.replace(token, url)
            return cleaned

        sensitive_path_keys = {
            "path",
            "paths",
            "image_path",
            "image_paths",
            "reference_path",
            "reference_paths",
            "reference_image_path",
            "reference_image_paths",
            "resolved_path",
            "prompt_path",
        }

        def sanitize(item: Any) -> Any:
            if isinstance(item, dict):
                cleaned_dict: dict[Any, Any] = {}
                for key, child in item.items():
                    normalized_key = re.sub(r"[^a-z0-9]+", "_", str(key or "").lower()).strip("_")
                    if normalized_key in sensitive_path_keys or normalized_key.endswith("_local_path"):
                        continue
                    cleaned_dict[key] = sanitize(child)
                return cleaned_dict
            if isinstance(item, (list, tuple, set)):
                return [sanitize(child) for child in item]
            if isinstance(item, str):
                return redact_text(item)
            return item

        return sanitize(value)

    @staticmethod
    def _current_turn_has_delivered_media(event: AstrMessageEvent) -> bool:
        if bool(getattr(event, "_private_companion_photo_tool_sent", False)):
            return True
        chains = getattr(event, "_private_companion_confirmed_send_chains", None)
        if not isinstance(chains, list):
            return False
        for chain in chains:
            if not isinstance(chain, (list, tuple)):
                continue
            for component in chain:
                component_name = type(component).__name__.casefold()
                if component_name in {"image", "file", "video", "record", "audio"}:
                    return True
        return False

    @staticmethod
    def _referenced_media_edit_instruction_matches(text: Any) -> bool:
        compact = re.sub(r"\s+", "", str(text or ""))
        if not compact:
            return False
        return bool(
            re.search(
                r"(?:把|将|给|帮我|替我).{0,18}"
                r"(?:改成|变成|换成|调成|染成|改为|变为|换为|调为)"
                r"|(?:改|换|调|染).{0,10}(?:颜色|色调|背景|尺寸|大小|亮度|对比度|饱和度)",
                compact,
                flags=re.I,
            )
        )

    @staticmethod
    def _current_media_private_delivery_instruction_matches(text: Any) -> bool:
        compact = re.sub(r"\s+", "", str(text or "")).casefold()
        if not compact:
            return False
        return bool(
            re.search(
                r"(?:私聊|私信|私发|dm).{0,8}(?:发|给|传|丢|送)?(?:给)?我"
                r"|(?:发|给|传|丢|送).{0,8}(?:到|去)?(?:我)?(?:私聊|私信|dm)",
                compact,
                flags=re.I,
            )
        )

    @classmethod
    def _current_media_delivery_instruction_matches(cls, text: Any) -> bool:
        compact = re.sub(r"\s+", "", str(text or ""))
        if not compact:
            return False
        if cls._current_media_private_delivery_instruction_matches(compact):
            return True
        if re.search(
            r"(?:把|将|给|帮我|麻烦)?(?:这张|那张|刚才的|上面的|改好的)?"
            r"(?:图|图片|照片|成图|表情包|表情|贴纸|反应图|梗图).{0,8}(?:发|传|给|贴|丢)(?:出来|过来|给我|我)?"
            r"|(?:发|传|给|贴|丢).{0,8}(?:这张|那张|刚才的|上面的|改好的)?"
            r"(?:图|图片|照片|成图|表情包|表情|贴纸|反应图|梗图)",
            compact,
            flags=re.I,
        ):
            return True
        # Follow-up requests often refer to the failed result indirectly, for
        # example "不要生成新图，把刚刚没发出来的发给我". Keep the
        # delivery tool available when the same sentence still contains an
        # image anchor, a recent-result anchor, and an actual send instruction.
        has_media_anchor = bool(
            re.search(
                r"(?:图|图片|照片|成图|图像|画面|表情包|贴纸|反应图|梗图|这张|那张|这一张|那一张)",
                compact,
                flags=re.I,
            )
        )
        has_recent_anchor = bool(
            re.search(
                r"(?:刚才|刚刚|之前|上次|前面|上面|原来|已有|现成|生成|画好|做好|改好|没发|未发|没送|未送)",
                compact,
                flags=re.I,
            )
        )
        has_delivery_action = bool(
            re.search(
                r"(?:发|传|贴|丢|送)(?:出来|过来|给我|我|一下|一次)?",
                compact,
                flags=re.I,
            )
        )
        return has_media_anchor and has_recent_anchor and has_delivery_action

    def _current_media_allowed_roots(self) -> list[Path]:
        roots: list[Path] = []

        def add(candidate: Any) -> None:
            text = str(candidate or "").strip()
            if not text:
                return
            try:
                resolved = Path(text).expanduser().resolve()
            except Exception:
                return
            if resolved not in roots:
                roots.append(resolved)

        try:
            add(Path(get_astrbot_data_path()) / "temp")
        except Exception:
            pass
        data_dir = str(getattr(self, "data_dir", "") or "").strip()
        if data_dir:
            add(Path(data_dir) / "generated_photos")
        return roots

    @staticmethod
    def _current_media_image_signature_suffix(path: Path) -> str:
        try:
            with path.open("rb") as handle:
                header = handle.read(16)
        except OSError:
            return ""
        if header.startswith(b"\xff\xd8\xff"):
            return ".jpg"
        if header.startswith(b"\x89PNG\r\n\x1a\n"):
            return ".png"
        if header.startswith((b"GIF87a", b"GIF89a")):
            return ".gif"
        if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
            return ".webp"
        if header.startswith(b"BM"):
            return ".bmp"
        if len(header) >= 12 and header[4:12] in {b"ftypavif", b"ftypavis"}:
            return ".avif"
        return ""

    @classmethod
    def _normalize_current_media_image_suffix(cls, path: Path) -> Path | None:
        actual_suffix = cls._current_media_image_signature_suffix(path)
        if not actual_suffix:
            return None
        current_suffix = path.suffix.casefold()
        if current_suffix == actual_suffix or {
            current_suffix,
            actual_suffix,
        } <= {".jpg", ".jpeg", ".jfif"}:
            return path

        temporary: Path | None = None
        try:
            stat = path.stat()
            fingerprint = hashlib.sha256(
                f"{path}:{stat.st_size}:{stat.st_mtime_ns}".encode("utf-8")
            ).hexdigest()[:12]
            normalized = path.with_name(
                f"{path.stem}.pc-media-{fingerprint}{actual_suffix}"
            )
            temporary = normalized.with_name(
                f"{normalized.name}.{uuid.uuid4().hex}.tmp"
            )
            shutil.copyfile(path, temporary)
            os.replace(temporary, normalized)
            return normalized.resolve(strict=True)
        except Exception as exc:
            try:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            except Exception:
                pass
            logger.warning(
                "当前媒体扩展名规范化失败: file=%s error=%s",
                path.name,
                _single_line(exc, 160),
            )
            return None

    def _resolve_current_media_image(self, value: Any) -> tuple[Path | None, str]:
        raw = str(value or "").strip().strip('"').strip("'")
        if not raw or raw.casefold().startswith(("http://", "https://", "data:", "base64://")):
            return None, "只支持本轮工具返回的本地图片路径"
        try:
            path = Path(raw).expanduser().resolve(strict=True)
        except Exception:
            return None, "本轮生成的图片文件不存在"
        if not path.is_file() or path.suffix.casefold() not in _CURRENT_MEDIA_IMAGE_SUFFIXES:
            return None, "只允许发送本轮生成的常见图片文件"
        if not any(path.is_relative_to(root) for root in self._current_media_allowed_roots()):
            return None, "图片不在允许的 AstrBot 临时目录或本插件成图目录内"
        try:
            stat = path.stat()
        except OSError:
            return None, "无法读取本轮生成的图片文件"
        if stat.st_size <= 0 or stat.st_size > _CURRENT_MEDIA_MAX_BYTES:
            return None, "图片为空或超过 32 MB 发送上限"
        age = time.time() - float(stat.st_mtime or 0)
        if age < -60 or age > _CURRENT_MEDIA_MAX_AGE_SECONDS:
            return None, "图片不是本轮近期生成的文件"
        normalized_path = self._normalize_current_media_image_suffix(path)
        if normalized_path is None:
            return None, "文件内容不是支持的实际图片格式"
        return normalized_path, ""

    async def _pc_send_current_media_impl(
        self,
        event: AstrMessageEvent,
        *,
        media_path: str = "",
        caption: str = "",
        destination: str = "current",
        **kwargs: Any,
    ) -> str:
        if self._current_turn_has_delivered_media(event):
            setattr(event, "_private_companion_photo_tool_sent", True)
            setattr(event, "_private_companion_photo_tool_sent_caption", "")
            return json.dumps(
                {
                    "status": "already_sent",
                    "success": True,
                    "sent": True,
                    "message": "本轮已经发送过媒体，不再重复投递。",
                    "same_turn_retry_allowed": False,
                    "final_response_instruction": f"不要追加回执或正文，只输出 {PHOTO_TOOL_SILENT_SENTINEL}。",
                },
                ensure_ascii=False,
            )
        raw_path = media_path or kwargs.get("image_path") or kwargs.get("path")
        path, rejection = self._resolve_current_media_image(raw_path)
        if path is None:
            return json.dumps(
                {
                    "status": "invalid_media",
                    "success": False,
                    "sent": False,
                    "message": rejection or "图片不可用",
                    "must_not_claim_sent": True,
                    "same_turn_retry_allowed": False,
                    "final_response_instruction": "不要再次猜测或改写本地路径；如实说明这次图片没有发送。",
                },
                ensure_ascii=False,
            )
        destination_raw = _single_line(
            destination or kwargs.get("target_scope") or kwargs.get("scope") or "current",
            40,
        ).casefold()
        requester_private = destination_raw in {
            "requester_private",
            "requester-private",
            "private",
            "private_requester",
            "dm",
            "私聊",
            "私信",
        }
        if requester_private and not self._current_media_private_delivery_instruction_matches(
            getattr(event, "message_str", "")
        ):
            return json.dumps(
                {
                    "status": "destination_not_confirmed",
                    "success": False,
                    "sent": False,
                    "message": "当前消息没有明确要求把图片私聊发给请求者",
                    "must_not_claim_sent": True,
                    "same_turn_retry_allowed": False,
                    "final_response_instruction": "不要私聊发送，也不要声称已经发送；按当前会话自然回复。",
                },
                ensure_ascii=False,
            )
        sent_paths = getattr(event, "_private_companion_current_media_sent_paths", None)
        if not isinstance(sent_paths, set):
            sent_paths = set()
            setattr(event, "_private_companion_current_media_sent_paths", sent_paths)
        destination_key = "requester_private" if requester_private else "current"
        path_key = f"{destination_key}:{path}".casefold()
        if path_key in sent_paths:
            setattr(event, "_private_companion_photo_tool_sent", True)
            return json.dumps(
                {
                    "status": "already_sent",
                    "success": True,
                    "sent": True,
                    "message": "这张图片本轮已经投递，不再重复发送。",
                    "same_turn_retry_allowed": False,
                    "final_response_instruction": f"不要追加回执或正文，只输出 {PHOTO_TOOL_SILENT_SENTINEL}。",
                },
                ensure_ascii=False,
            )
        visible_caption = self._sanitize_photo_tool_caption(caption, limit=120)
        try:
            if requester_private:
                try:
                    target_user = _single_line(event.get_sender_id(), 128)
                except Exception:
                    target_user = ""
                sender = getattr(self, "_send_atrelay_chain_to_target", None)
                chain_builder = getattr(self, "_build_outbound_chain", None)
                if not target_user:
                    delivery = {
                        "sent": False,
                        "destination": "requester_private",
                        "message": "无法识别当前请求者，图片没有私聊发送",
                    }
                elif not callable(sender) or not callable(chain_builder):
                    delivery = {
                        "sent": False,
                        "destination": "requester_private",
                        "message": "当前平台没有可用的私聊图片投递链路",
                    }
                else:
                    chain = chain_builder(visible_caption, str(path))
                    ok, error, used_umo = await sender(
                        event,
                        message_type="private",
                        target_id=target_user,
                        chain=chain,
                    )
                    delivery = {
                        "sent": bool(ok),
                        "destination": "requester_private",
                        "message": (
                            "图片已私聊发送给当前请求者"
                            if ok
                            else f"图片私聊发送失败：{_single_line(error, 180) or '没有可用私聊会话'}"
                        ),
                        "target_umo": _single_line(used_umo, 160),
                    }
            else:
                delivery = await self._deliver_generated_image_to_event(
                    event,
                    image_path=str(path),
                    caption=visible_caption,
                )
        except Exception as exc:
            delivery = {
                "sent": False,
                "uncertain": isinstance(exc, (asyncio.TimeoutError, TimeoutError, ConnectionError)),
                "destination": destination_key,
                "message": f"图片发送失败：{_single_line(exc, 180) or '未知错误'}",
            }
        sent = bool(delivery.get("sent"))
        uncertain = bool(delivery.get("uncertain"))
        if sent:
            sent_paths.add(path_key)
            setattr(event, "_private_companion_photo_tool_sent", True)
            setattr(event, "_private_companion_photo_tool_sent_caption", visible_caption)
        payload = {
            "status": "success" if sent else "delivery_uncertain" if uncertain else "delivery_failed",
            "success": sent,
            "sent": sent,
            "delivery_uncertain": uncertain,
            "delivery": _single_line(delivery.get("destination"), 30),
            "message": _single_line(delivery.get("message"), 220) or ("图片已发送" if sent else "图片发送失败"),
            "must_not_claim_sent": not sent,
            "same_turn_retry_allowed": False,
            "final_response_instruction": (
                f"图片及可选 caption 已作为本轮唯一可见回复发送，只输出 {PHOTO_TOOL_SILENT_SENTINEL}。"
                if sent
                else "不要再次发送或重新生成；按 message 如实说明当前投递结果。"
            ),
        }
        return json.dumps(payload, ensure_ascii=False)

    async def _recover_plaintext_photo_tool_call(
        self,
        event: AstrMessageEvent,
        resp: Any,
        text: Any,
    ) -> tuple[str, dict[str, Any] | None]:
        raw = str(text or "")
        if bool(getattr(event, "_private_companion_plaintext_tool_checked", False)):
            previous = getattr(event, "_private_companion_plaintext_tool_recovery", None)
            return raw, previous if isinstance(previous, dict) else None
        cleaned, calls = self._strip_plaintext_tool_call_envelopes(raw)
        if not calls:
            return raw, None
        setattr(event, "_private_companion_plaintext_tool_checked", True)
        logger.warning(
            "检测到模型将工具调用写入普通正文，已阻止外发: session=%s tools=%s",
            _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            ",".join(call.get("name", "") for call in calls),
        )
        recovery: dict[str, Any] = {
            "status": "sanitized_only",
            "sent": False,
            "tools": [call.get("name", "") for call in calls],
        }
        setattr(event, "_private_companion_plaintext_tool_recovery", recovery)
        photo_calls = [call for call in calls if call.get("name") == "pc_generate_photo"]
        if len(calls) != 1 or len(photo_calls) != 1:
            return cleaned, recovery
        try:
            called_names = getattr(resp, "tools_call_name", None)
            if isinstance(called_names, str) and called_names.strip() == "pc_generate_photo":
                recovery["status"] = "already_called"
                return cleaned, recovery
            if isinstance(called_names, (list, tuple, set)) and "pc_generate_photo" in {str(item) for item in called_names}:
                recovery["status"] = "already_called"
                return cleaned, recovery
            if self._proactive_only_blocks_passive_event(event, "pc_generate_photo"):
                recovery["status"] = "blocked"
                return cleaned, recovery
        except Exception:
            pass
        inbound_text = str(getattr(event, "message_str", "") or "")
        if not self._plaintext_photo_recovery_intent_matches(inbound_text):
            recovery["status"] = "intent_mismatch"
            return cleaned, recovery

        raw_parameters = photo_calls[0].get("parameters")
        parameters = dict(raw_parameters) if isinstance(raw_parameters, dict) else {}
        allowed_keys = {
            "prompt",
            "kind",
            "reference_image_path",
            "reference_image_paths",
            "image_size",
            "caption",
            "scene_preset",
        }
        parameters = {key: value for key, value in parameters.items() if key in allowed_keys}
        parameters["send"] = True
        try:
            result_raw = await self._pc_generate_photo_impl(event, **parameters)
            try:
                result = json.loads(result_raw) if isinstance(result_raw, str) else dict(result_raw or {})
            except Exception:
                result = {"status": "error", "sent": False, "message": "生图工具返回无法解析"}
        except Exception as exc:
            logger.error(
                "明文生图工具调用恢复失败: session=%s error=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                _single_line(exc, 160),
                exc_info=True,
            )
            result = {"status": "error", "sent": False, "message": "图片生成调用失败"}
        sent = bool(result.get("sent"))
        recovery.update({"status": "recovered" if sent else "failed", "sent": sent, "result": result})
        setattr(event, "_private_companion_plaintext_tool_recovery", recovery)
        if sent:
            setattr(event, "_private_companion_plaintext_photo_sent", True)
            logger.info(
                "已恢复并执行明文生图工具调用: session=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            )
            return cleaned, recovery
        failure = _single_line(result.get("message") or result.get("actual_error") or "图片没有生成成功", 180)
        failure = _redact_outbound_secrets(failure, self)
        failure_text = f"这次图片没能发出来：{failure}" if failure else "这次图片没能发出来。"
        cleaned = "\n".join(part for part in (cleaned, failure_text) if str(part or "").strip()).strip()
        return cleaned, recovery

