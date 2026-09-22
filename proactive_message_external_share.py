# -*- coding: utf-8 -*-
"""external_share 域。

由 tools/split_mixin_domain.py 从 proactive_message.py 机械抽取（34 个方法 + 0 个模块级名字 + 0 个类级赋值 / 1236 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveMessageMixin）。
"""
from __future__ import annotations

import re
from .conversation_prompt_section import (
    PromptDocument,
    PromptRenderMode,
    PromptSection,
    prompt_document,
    prompt_section,
    render_prompt_document,
    render_prompt_sections,
)
from .helpers import _now_ts, _safe_float, _safe_int, _single_line, _today_key
from .persona_config import runtime_persona_setting
from .proactive_message_shared import _PROACTIVE_DOCUMENT_RENDER, _persona_provider_id, _proactive_prompt_part
from typing import Any
from urllib.parse import urlparse



# ---- 宿主全局转发层（由 tmp/refactor/autofix_domain_globals.py 生成）----
# ==== 需实时转发（可被 patch）：同名函数转发宿主 ====
def _now_ts(*args, **kwargs):
    from . import proactive_message as _host
    return getattr(_host, "_now_ts")(*args, **kwargs)
# ---- 宿主全局转发层结束 ----

class ProactiveMessageExternalShareMixin:
    """external_share 域（从 ProactiveMessageMixin 拆出）。"""


    @staticmethod
    def _looks_like_internal_provider_error_text(text: Any) -> bool:
        cleaned = _single_line(text, 1000).lower()
        if not cleaned:
            return False
        normalized = re.sub(r"[^a-z0-9\u4e00-\u9fff_]+", " ", cleaned).strip()
        compact = re.sub(r"[^a-z0-9\u4e00-\u9fff_]+", "", cleaned)
        direct_markers = (
            "all chat models failed",
            "all llm providers failed",
            "prompt could not be submitted",
            "prompt was not submitted",
            "try rephrasing the prompt",
            "generative ai prohibited use policy",
            "prompt contains sensitive words",
            "badrequesterror",
            "api connection error",
            "apiconnectionerror",
            "api status error",
            "apistatuserror",
            "authenticationerror",
            "permissiondeniederror",
            "ratelimiterror",
            "notfounderror",
            "internalservererror",
            "provider api error",
            "unable to submit request",
            "invalid_request",
            "invalid request error",
            "主动消息专用模式下",
            "普通被动回复不可使用 private companion 工具",
            "主动渲染阶段不可使用 private companion 工具",
            "has sent the result directly to the user",
            "error code: 400",
            "error code 400",
            "400 bad request",
            "模型调用失败",
            "工具调用失败",
            "函数工具调用失败",
            "api 调用失败",
            "api调用失败",
            "provider 调用失败",
            "provider调用失败",
            "没有返回值，或者已将结果直接发送给用户",
            "没有返回值,或者已将结果直接发送给用户",
        )
        compact_markers = (
            "allchatmodelsfailed",
            "allllmprovidersfailed",
            "promptcouldnotbesubmitted",
            "promptwasnotsubmitted",
            "tryrephrasingtheprompt",
            "generativeaiprohibitedusepolicy",
            "promptcontainssensitivewords",
            "badrequesterror",
            "apiconnectionerror",
            "apistatuserror",
            "authenticationerror",
            "permissiondeniederror",
            "ratelimiterror",
            "notfounderror",
            "internalservererror",
            "providerapierror",
            "unabletosubmitrequest",
            "invalid_request",
            "invalidrequesterror",
            "主动消息专用模式下",
            "普通被动回复不可使用privatecompanion工具",
            "主动渲染阶段不可使用privatecompanion工具",
            "hassenttheresultdirectlytotheuser",
            "errorcode400",
            "400badrequest",
            "模型调用失败",
            "工具调用失败",
            "函数工具调用失败",
            "api调用失败",
            "provider调用失败",
            "没有返回值或者已将结果直接发送给用户",
        )
        if any(marker in cleaned or marker in normalized for marker in direct_markers):
            return True
        if any(marker in compact for marker in compact_markers):
            return True
        provider_error_context = any(
            token in compact
            for token in (
                "providerapierror",
                "errorcode",
                "statuscode",
                "badrequest",
                "invalidrequest",
                "requestfailed",
                "请求失败",
                "调用失败",
                "模型调用失败",
                "工具调用失败",
            )
        )
        if "errorcode" in compact and any(
            token in compact
            for token in (
                "badrequest",
                "invalidrequest",
                "provider",
                "apierror",
                "functiondeclaration",
            )
        ):
            return True
        if "functiondeclaration" in compact and provider_error_context and any(
            token in compact for token in ("schema", "properties", "parameters", "tool", "tools", "badrequest", "invalidrequest")
        ):
            return True
        if any(token in compact for token in ("schemadidntspecify", "toolschema", "image_url", "invalidparameter")) and provider_error_context:
            return True
        if "aisearch" in cleaned and any(
            marker in cleaned
            for marker in (
                "failed",
                "badrequest",
                "invalid_request",
                "unable to submit",
                "provider api",
            )
        ):
            return True
        return False

    def _clean_external_share_source_field(self, value: Any, limit: int = 160) -> str:
        text = _single_line(value, limit)
        if not text:
            return ""
        if self._looks_like_internal_provider_error_text(text):
            return ""
        if self._framework_agent_meta_summary_leak(text):
            return ""
        return text

    def _format_bilibili_video_action_context(self, user: dict[str, Any]) -> str:
        video = user.get("bilibili_video_context")
        if not isinstance(video, dict):
            return ""
        if _now_ts() - _safe_float(video.get("created_ts"), 0) > 6 * 3600:
            return ""
        title = self._clean_external_share_source_field(video.get("title"), 80)
        bvid = self._clean_external_share_source_field(video.get("bvid"), 32)
        up_name = self._clean_external_share_source_field(video.get("up_name"), 40)
        score = _safe_int(video.get("score"), 0, 0, 10)
        mood = self._clean_external_share_source_field(video.get("mood"), 24)
        comment = self._clean_external_share_source_field(video.get("comment"), 120)
        review = self._clean_external_share_source_field(video.get("review"), 180)
        source = self._clean_external_share_source_field(video.get("source"), 40)
        memory_context = video.get("memory_context") if isinstance(video.get("memory_context"), list) else []
        memory_lines = [
            text
            for item in memory_context
            for text in [self._clean_external_share_source_field(item, 160)]
            if text
        ][:3]
        if not title and not bvid and not comment and not review:
            return ""
        parts = [
            "B站视频分享线索",
            f"标题：{title}" if title else "",
            f"链接：https://www.bilibili.com/video/{bvid}" if bvid else "",
            f"UP：{up_name}" if up_name else "",
            f"评分：{score}/10" if score else "",
            f"心情：{mood}" if mood else "",
            f"短评：{comment}" if comment else "",
            f"回味：{review}" if review else "",
            f"来源：{source}" if source else "",
            "BiliBot记忆：" + " / ".join(memory_lines) if memory_lines else "",
        ]
        return "\n".join(part for part in parts if part)

    def _format_news_action_context(self, user: dict[str, Any]) -> str:
        news = user.get("news_context")
        if not isinstance(news, dict):
            return ""
        if _now_ts() - _safe_float(news.get("created_ts"), 0) > 8 * 3600:
            return ""
        topic = self._clean_external_share_source_field(news.get("topic"), 60)
        headline = self._clean_external_share_source_field(news.get("headline"), 100)
        source = self._clean_external_share_source_field(news.get("selected_source"), 40)
        impression = self._clean_external_share_source_field(news.get("impression"), 240)
        link = self._clean_external_share_source_field(news.get("selected_link"), 400)
        self_link = news.get("self_link") if isinstance(news.get("self_link"), dict) else {}
        self_link_text = self._clean_external_share_source_field(self_link.get("self_link") if isinstance(self_link, dict) else "", 180)
        self_link_tone = self._clean_external_share_source_field(news.get("share_tone") or (self_link.get("tone") if isinstance(self_link, dict) else ""), 80)
        self_link_boundary = self._clean_external_share_source_field(news.get("share_boundary") or (self_link.get("boundary") if isinstance(self_link, dict) else ""), 160)
        if not topic and not headline and not link and not impression:
            return ""
        parts = [
            "新闻阅读线索",
            f"话题：{topic}" if topic else "",
            f"标题：{headline}" if headline else "",
            f"来源：{source}" if source else "",
            f"内部印象：{impression}" if impression else "",
            f"和自己有关的地方：{self_link_text}" if self_link_text else "",
            f"表达气质：{self_link_tone}" if self_link_tone else "",
            f"额外边界：{self_link_boundary}" if self_link_boundary else "",
            f"链接：{link}" if link else "",
            "表达要求：不要像播报新闻,不要夸大或补充未知事实；按人格正常说话即可。",
        ]
        return "\n".join(part for part in parts if part)

    def _format_web_exploration_action_context(self, user: dict[str, Any]) -> str:
        exploration = user.get("web_exploration_context")
        if not isinstance(exploration, dict):
            return ""
        if _now_ts() - _safe_float(exploration.get("created_ts"), 0) > 10 * 3600:
            return ""
        query = self._clean_external_share_source_field(exploration.get("query"), 80)
        topic = self._clean_external_share_source_field(exploration.get("topic"), 80)
        note = self._clean_external_share_source_field(exploration.get("note"), 260)
        source_title = self._clean_external_share_source_field(exploration.get("source_title"), 120)
        source_url = self._clean_external_share_source_field(exploration.get("source_url"), 420)
        source_platform = self._external_share_platform_from_url(source_url)
        reason = self._clean_external_share_source_field(exploration.get("reason"), 140)
        self_link = exploration.get("self_link") if isinstance(exploration.get("self_link"), dict) else {}
        self_link_text = self._clean_external_share_source_field(self_link.get("self_link") if isinstance(self_link, dict) else "", 180)
        self_link_tone = self._clean_external_share_source_field(exploration.get("share_tone") or (self_link.get("tone") if isinstance(self_link, dict) else ""), 80)
        self_link_boundary = self._clean_external_share_source_field(exploration.get("share_boundary") or (self_link.get("boundary") if isinstance(self_link, dict) else ""), 160)
        if not query and not topic and not note and not source_title and not source_url:
            return ""
        parts = [
            "网页探索线索",
            f"搜索词：{query}" if query else "",
            f"为什么想查：{reason}" if reason else "",
            f"探索主题：{topic}" if topic else "",
            f"留下的印象：{note}" if note else "",
            f"和自己有关的地方：{self_link_text}" if self_link_text else "",
            f"表达气质：{self_link_tone}" if self_link_tone else "",
            f"额外边界：{self_link_boundary}" if self_link_boundary else "",
            f"参考来源：{source_title}" if source_title else "",
            f"来源平台（以链接域名为准）：{source_platform}" if source_platform else "",
            f"链接：{source_url}" if source_url else "",
            "表达要求：自然地向用户分享自己刚看的这条内容。标题、印象和链接只是事实参考，按当前人格正常说话，不要照抄字段。",
        ]
        return "\n".join(part for part in parts if part)

    @staticmethod
    def _external_share_platform_from_url(url: Any) -> str:
        value = str(url or "").strip()
        if not value:
            return ""
        try:
            parsed = urlparse(value if "://" in value else f"//{value}")
            hostname = str(parsed.hostname or "").strip().lower().rstrip(".")
        except Exception:
            hostname = ""
        if not hostname:
            return ""
        platform_domains = (
            (("bilibili.com", "b23.tv"), "B站"),
            (("douyin.com", "iesdouyin.com"), "抖音"),
            (("xiaohongshu.com", "xhslink.com"), "小红书"),
            (("weibo.com", "weibo.cn"), "微博"),
            (("zhihu.com",), "知乎"),
            (("youtube.com", "youtu.be"), "YouTube"),
            (("reddit.com", "redd.it"), "Reddit"),
            (("github.com",), "GitHub"),
            (("toutiao.com",), "今日头条"),
        )
        for domains, label in platform_domains:
            if any(hostname == domain or hostname.endswith(f".{domain}") for domain in domains):
                return label
        return ""

    @staticmethod
    def _external_share_claimed_platform(text: Any) -> str:
        value = _single_line(text, 320)
        patterns = (
            (r"(?:刚|在|从|刷到|看到|翻到).{0,8}(?:B站|哔哩哔哩)|(?:B站|哔哩哔哩)(?:视频|上|里|《)", "B站"),
            (r"(?:刚|在|从|刷到|看到|翻到).{0,8}抖音|抖音(?:视频|上|里|《)", "抖音"),
            (r"(?:刚|在|从|刷到|看到|翻到).{0,8}小红书|小红书(?:笔记|上|里|《)", "小红书"),
            (r"(?:刚|在|从|刷到|看到|翻到).{0,8}微博|微博(?:上|里|《)", "微博"),
            (r"(?:刚|在|从|刷到|看到|翻到).{0,8}知乎|知乎(?:上|里|《)", "知乎"),
            (r"(?:刚|在|从|刷到|看到|翻到).{0,8}YouTube|YouTube(?:上|里)", "YouTube"),
            (r"(?:刚|在|从|刷到|看到|翻到).{0,8}Reddit|Reddit(?:上|里|《)", "Reddit"),
        )
        for pattern, label in patterns:
            if re.search(pattern, value, flags=re.I):
                return label
        return ""

    def _proactive_link_platform_mismatch_reason(self, text: Any) -> str:
        cleaned = _single_line(text, 600)
        claimed_platform = self._external_share_claimed_platform(cleaned)
        if not cleaned or not claimed_platform:
            return ""
        links = re.findall(r"https?://[^\s，。！？!?；;）)】\]》>]+", cleaned, flags=re.I)
        for link in links:
            actual_platform = self._external_share_platform_from_url(link)
            if actual_platform == claimed_platform:
                continue
            try:
                hostname = str(urlparse(link).hostname or "").strip().lower()
            except Exception:
                hostname = ""
            actual_label = actual_platform or hostname or "未知域名"
            return f"正文声称来源为{claimed_platform}，但链接实际属于{actual_label}"
        return ""

    def _user_asks_ai_daily_context(self, inbound_text: str) -> bool:
        text = str(inbound_text or "").strip()
        if not text:
            return False
        if any(
            token in text
            for token in (
                "AI日报", "ai日报", "AI 日报", "ai 日报",
                "AI早报", "ai早报", "AI 早报", "ai 早报",
                "大模型日报", "大模型早报", "人工智能日报", "人工智能早报",
            )
        ):
            return True
        lowered = text.lower()
        if any(token in lowered for token in ("ai daily", "daily ai", "llm daily", "ai digest")):
            return True
        return bool(("日报" in text or "早报" in text) and re.search(r"(ai|llm|大模型|人工智能|模型)", text, flags=re.IGNORECASE))

    def _ai_daily_query_requires_freshness(self, inbound_text: str) -> bool:
        text = str(inbound_text or "").strip()
        if not text:
            return False
        return bool(re.search(r"(今天|今日|今早|刚刚|刚才|最新|现在)", text, flags=re.IGNORECASE))

    def _select_ai_daily_digest_item(self, ai_state: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        digest = ai_state.get("last_digest") if isinstance(ai_state.get("last_digest"), dict) else {}
        selected_item: dict[str, Any] = {}
        digest_items = digest.get("items") if isinstance(digest.get("items"), list) else []
        selected_key = _single_line(digest.get("selected_key"), 80)
        for candidate in digest_items:
            if not isinstance(candidate, dict):
                continue
            if selected_key and _single_line(candidate.get("key"), 80) == selected_key:
                selected_item = candidate
                break
        if not selected_item and digest_items and isinstance(digest_items[0], dict):
            selected_item = digest_items[0]
        return digest, selected_item

    def _user_asks_news_context(self, inbound_text: str) -> bool:
        text = str(inbound_text or "").strip()
        if not text:
            return False
        if any(token in text for token in ("新闻", "早报", "热点", "时讯", "资讯", "新消息")):
            return True
        lowered = text.lower()
        return any(token in lowered for token in ("ai news", "llm news", "daily ai", "tech news"))

    def _user_asks_web_exploration_context(self, inbound_text: str) -> bool:
        text = str(inbound_text or "").strip()
        if not text:
            return False
        if re.search(r"(主动搜索|网页探索|搜索记录|浏览记录|上网).{0,16}(什么|啥|哪|记录|看|查|搜|了解|发现)|((最近|刚才|今天|这两天|这会儿).{0,16}(搜|查|上网|浏览|了解|看了啥|看了什么|发现了什么))|你.{0,12}(搜了什么|查了什么|上网看了什么|上网看了啥|发现了什么新东西)", text):
            return True
        lowered = text.lower()
        return any(token in lowered for token in ("web exploration", "recent search", "search history", "browsing history"))

    def _format_recent_web_exploration_context_for_reply(
        self,
        inbound_text: str = "",
    ) -> str:
        section = self._format_recent_web_exploration_context_prompt_section(inbound_text)
        if section is None:
            return ""
        return render_prompt_sections(
            [section],
            mode=PromptRenderMode.LABELED_BLOCK,
        )

    def _format_recent_web_exploration_context_prompt_section(
        self,
        inbound_text: str = "",
    ) -> PromptSection | None:
        if not runtime_persona_setting(self, "enable_web_exploration", False):
            return None
        if not self._user_asks_web_exploration_context(inbound_text):
            return None
        def build_section(content: str) -> PromptSection:
            return prompt_section(
                key="web_exploration.recent",
                title="主动搜索上下文",
                source="web_exploration",
                content=content,
            )

        state = self.data.get("web_exploration") if isinstance(self.data.get("web_exploration"), dict) else {}
        digest = state.get("last_digest") if isinstance(state.get("last_digest"), dict) else {}
        notes = state.get("notes") if isinstance(state.get("notes"), list) else []
        latest_results = state.get("latest_results") if isinstance(state.get("latest_results"), list) else []
        if not digest and not notes and not latest_results:
            body = (
                "用户正在询问你最近主动搜索/上网探索过什么,但当前没有可用的主动搜索记录。请自然说明自己最近还没搜到能说的东西,不要编造搜索内容。"
            )
            return build_section(body)
        rows: list[str] = []
        if digest:
            rows.append(
                "最近一次搜索："
                + "｜".join(
                    part
                    for part in (
                        f"搜索词：{_single_line(digest.get('query'), 90)}" if _single_line(digest.get("query"), 90) else "",
                        f"主题：{_single_line(digest.get('topic'), 90)}" if _single_line(digest.get("topic"), 90) else "",
                        f"动机：{_single_line(digest.get('reason'), 140)}" if _single_line(digest.get("reason"), 140) else "",
                        f"笔记：{_single_line(digest.get('note'), 240)}" if _single_line(digest.get("note"), 240) else "",
                        f"来源：{_single_line(digest.get('source_title'), 120)}" if _single_line(digest.get("source_title"), 120) else "",
                    )
                    if part
                )
            )
        for item in reversed([item for item in notes if isinstance(item, dict)][-4:]):
            query = _single_line(item.get("query"), 90)
            topic = _single_line(item.get("topic"), 90)
            note = _single_line(item.get("note") or item.get("summary") or item.get("impression"), 180)
            reason = _single_line(item.get("reason"), 100)
            if query or topic or note:
                rows.append("- " + "｜".join(part for part in (f"搜索词：{query}" if query else "", topic, reason, note) if part))
        if latest_results:
            result_rows = []
            for item in latest_results[:4]:
                if not isinstance(item, dict):
                    continue
                title = _single_line(item.get("title"), 120)
                snippet = _single_line(item.get("snippet"), 160)
                if title:
                    result_rows.append("- " + "｜".join(part for part in (title, snippet) if part))
            if result_rows:
                rows.append("最近一次结果摘录：")
                rows.extend(result_rows)
        body = (
            "用户正在询问你最近主动搜索/网页探索过什么。下面是真实搜索记录；回答只能基于这些内容,不要编造额外搜索、来源或结论。"
            "可以用第一人称自然概括“我刚查了/我之前搜到”,但不要说成后台系统日志。\n"
            + "\n".join(rows[:12])
        )
        return build_section(body)

    def _format_recent_ai_daily_context_for_reply(
        self,
        inbound_text: str = "",
    ) -> str:
        section = self._format_recent_ai_daily_context_prompt_section(inbound_text)
        if section is None:
            return ""
        return render_prompt_sections(
            [section],
            mode=PromptRenderMode.LABELED_BLOCK,
        )

    def _format_recent_ai_daily_context_prompt_section(
        self,
        inbound_text: str = "",
    ) -> PromptSection | None:
        if not runtime_persona_setting(self, "enable_news_integration", False):
            return None
        if not self._user_asks_ai_daily_context(inbound_text):
            return None
        def build_section(content: str) -> PromptSection:
            return prompt_section(
                key="news.ai_daily_context",
                title="新闻阅读上下文",
                source="news",
                content=content,
            )

        state = self.data.get("news_integration") if isinstance(self.data.get("news_integration"), dict) else {}
        ai_state = state.get("ai_daily") if isinstance(state.get("ai_daily"), dict) else {}
        digest, selected_item = self._select_ai_daily_digest_item(ai_state)
        record_date = _single_line(ai_state.get("last_success_date"), 20) or _single_line(ai_state.get("date"), 20)
        source_name = _single_line(ai_state.get("last_source_name"), 40)
        source_author = _single_line(ai_state.get("last_source_author"), 60)
        source_schedule = _single_line(ai_state.get("last_source_schedule"), 10)
        video_title = _single_line(ai_state.get("last_video_title"), 120)
        video_link = _single_line(ai_state.get("last_video_link"), 360)
        text_link = _single_line(ai_state.get("last_text_link"), 360)
        headline = _single_line(digest.get("headline") or digest.get("topic"), 120)
        impression = _single_line(digest.get("impression"), 220)
        read_basis = _single_line(ai_state.get("last_read_basis"), 40)
        text_readable_raw = ai_state.get("last_text_readable")
        text_readable = bool(text_readable_raw) if isinstance(text_readable_raw, bool) else bool(selected_item.get("article_readable") and selected_item.get("article_text"))
        subtitle_status = _single_line(ai_state.get("last_video_subtitle_status") or selected_item.get("video_subtitle_status"), 40)
        if not any((record_date, source_name, video_title, headline, impression, video_link, text_link)):
            body = (
                "用户正在询问 AI 日报/早报,但当前没有可用的 AI 日报记录。请直接说明最近还没读到可确认的 AI 日报,不要编造。"
            )
            return build_section(body)
        today = _today_key()
        rows: list[str] = []
        if record_date:
            if record_date != today and self._ai_daily_query_requires_freshness(inbound_text):
                rows.append(f"时间说明：今天是 {today}；最近一次可用 AI 日报记录日期是 {record_date}，不是今天。")
            else:
                rows.append(f"记录日期：{record_date}")
        if source_name or source_author or source_schedule:
            rows.append("来源：" + "｜".join(part for part in (source_name, source_author, source_schedule) if part))
        if video_title:
            rows.append(f"视频标题：{video_title}")
        if headline:
            rows.append(f"摘要重点：{headline}")
        if impression:
            rows.append(f"阅读印象：{impression}")
        if read_basis:
            rows.append(f"整理依据：{read_basis}")
        if text_link:
            rows.append(f"文字版链接：{text_link}")
        elif video_link:
            rows.append(f"视频链接：{video_link}")
        rows.append(f"正文可读：{'是' if text_readable else '否'}")
        if subtitle_status:
            rows.append(f"字幕状态：{subtitle_status}")
        body = (
            "用户正在询问 AI 日报/早报。下面是最近一次真实读到的 AI 日报记录；如果日期不是今天，请明确说出具体日期，不要说成今天刚读到。"
            "回答只能基于这些内容，不要编造额外新闻。\n"
            + "\n".join(rows[:10])
        )
        return build_section(body)

    def _format_recent_news_context_for_reply(
        self,
        inbound_text: str = "",
    ) -> str:
        section = self._format_recent_news_context_prompt_section(inbound_text)
        if section is None:
            return ""
        return render_prompt_sections(
            [section],
            mode=PromptRenderMode.LABELED_BLOCK,
        )

    def _format_recent_news_context_prompt_section(
        self,
        inbound_text: str = "",
    ) -> PromptSection | None:
        if not runtime_persona_setting(self, "enable_news_integration", False):
            return None
        ai_daily_context = self._format_recent_ai_daily_context_prompt_section(inbound_text)
        if ai_daily_context is not None:
            return ai_daily_context
        if not self._user_asks_news_context(inbound_text):
            return None
        def build_section(content: str) -> PromptSection:
            return prompt_section(
                key="news.recent",
                title="新闻阅读上下文",
                source="news",
                content=content,
            )

        state = self.data.get("news_integration") if isinstance(self.data.get("news_integration"), dict) else {}
        digest = state.get("last_digest") if isinstance(state.get("last_digest"), dict) else {}
        digests = state.get("digests") if isinstance(state.get("digests"), list) else []
        latest_items = state.get("latest_items") if isinstance(state.get("latest_items"), list) else []
        if not digest and not digests and not latest_items:
            body = (
                "用户正在询问今天的新闻/AI 新闻,但当前还没有可用的新闻阅读记录。请自然说明自己还没读到今天的新闻,不要编造新闻。"
            )
            return build_section(body)
        rows: list[str] = []
        if digest:
            rows.append(
                "最近一次整理："
                + "｜".join(
                    part
                    for part in (
                        _single_line(digest.get("headline") or digest.get("topic"), 120),
                        _single_line(digest.get("selected_source"), 40),
                        _single_line(digest.get("impression"), 220),
                        _single_line(digest.get("selected_link"), 360),
                    )
                    if part
                )
            )
        for item in reversed([item for item in digests if isinstance(item, dict)][-4:]):
            headline = _single_line(item.get("headline") or item.get("topic"), 120)
            impression = _single_line(item.get("impression"), 180)
            source = _single_line(item.get("selected_source"), 40)
            if headline or impression:
                rows.append("- " + "｜".join(part for part in (headline, source, impression) if part))
        if latest_items:
            rows.append("候选标题：")
            for item in latest_items[:6]:
                if not isinstance(item, dict):
                    continue
                title = _single_line(item.get("title"), 120)
                source = _single_line(item.get("source"), 40)
                summary = _single_line(item.get("summary"), 160)
                if title:
                    rows.append("- " + "｜".join(part for part in (title, source, summary) if part))
        body = (
            "用户正在询问今天的新闻/AI 新闻。下面是 Bot 近期真实读过或抓到的新闻记录；回答时只能基于这些内容,不要编造额外新闻。"
            "可以按人格自然概括,如果记录不够新或不完整,要直接说明。\n"
            + "\n".join(rows[:12])
        )
        return build_section(body)

    def _format_news_digest_for_command(self) -> str:
        state = self.data.get("news_integration") if isinstance(self.data.get("news_integration"), dict) else {}
        if not runtime_persona_setting(self, "enable_news_integration", False):
            return "新闻阅读功能没有开启。"
        status = _single_line(state.get("last_status"), 60) or "未知"
        digest = state.get("last_digest") if isinstance(state.get("last_digest"), dict) else {}
        latest_items = state.get("latest_items") if isinstance(state.get("latest_items"), list) else []
        if not digest and not latest_items:
            return f"这次没有读到可用新闻。\n状态：{status}"
        lines = ["今日新闻见闻："]
        if digest:
            headline = _single_line(digest.get("headline") or digest.get("topic"), 120)
            source = _single_line(digest.get("selected_source"), 40)
            impression = _single_line(digest.get("impression"), 260)
            link = _single_line(digest.get("selected_link"), 420)
            if headline:
                lines.append(f"- 重点：{headline}")
            if source:
                lines.append(f"- 来源：{source}")
            if impression:
                lines.append(f"- 印象：{impression}")
            if link:
                lines.append(f"- 链接：{link}")
        if latest_items:
            lines.append("候选标题：")
            for item in latest_items[:6]:
                if not isinstance(item, dict):
                    continue
                title = _single_line(item.get("title"), 100)
                source = _single_line(item.get("source"), 30)
                if title:
                    lines.append(f"- {title}" + (f"（{source}）" if source else ""))
        return "\n".join(lines)

    def _format_ai_daily_digest_for_command(self) -> str:
        state = self.data.get("news_integration") if isinstance(self.data.get("news_integration"), dict) else {}
        if not runtime_persona_setting(self, "enable_news_integration", False):
            return "新闻阅读功能没有开启。"
        if not runtime_persona_setting(self, "enable_ai_daily_watch", True):
            return "AI 日报/早报追踪没有开启。"
        ai_state = state.get("ai_daily") if isinstance(state.get("ai_daily"), dict) else {}
        digest, selected_item = self._select_ai_daily_digest_item(ai_state)
        record_date = _single_line(ai_state.get("last_success_date"), 20) or _single_line(ai_state.get("date"), 20)
        source_name = _single_line(ai_state.get("last_source_name"), 40)
        source_author = _single_line(ai_state.get("last_source_author"), 60)
        source_schedule = _single_line(ai_state.get("last_source_schedule"), 10)
        video_title = _single_line(ai_state.get("last_video_title"), 120)
        video_link = _single_line(ai_state.get("last_video_link"), 420)
        text_link = _single_line(ai_state.get("last_text_link"), 420)
        headline = _single_line(digest.get("headline") or digest.get("topic"), 120)
        impression = _single_line(digest.get("impression"), 260)
        read_basis = _single_line(ai_state.get("last_read_basis"), 40) or ("完整文字版正文" if bool(selected_item.get("article_readable") and selected_item.get("article_text")) else "视频标题/简介")
        if not any((record_date, source_name, video_title, headline, impression, video_link, text_link)):
            status = _single_line(ai_state.get("status"), 60) or "未知"
            return f"最近还没有可用的 AI 日报记录。\n状态：{status}"
        today = _today_key()
        lines = ["最近的 AI 日报/早报："]
        if record_date:
            lines.append(f"- 日期：{record_date}")
            if record_date != today:
                lines.append(f"- 说明：今天是 {today}，最近一次成功记录不是今天。")
        if source_name or source_author or source_schedule:
            lines.append("- 来源：" + "｜".join(part for part in (source_name, source_author, source_schedule) if part))
        if video_title:
            lines.append(f"- 视频：{video_title}")
        if headline:
            lines.append(f"- 重点：{headline}")
        if impression:
            lines.append(f"- 印象：{impression}")
        if read_basis:
            lines.append(f"- 整理依据：{read_basis}")
        if text_link:
            lines.append(f"- 文字版：{text_link}")
        elif video_link:
            lines.append(f"- 视频链接：{video_link}")
        return "\n".join(lines)

    def _format_ai_daily_status_for_command(self) -> str:
        state = self.data.get("news_integration") if isinstance(self.data.get("news_integration"), dict) else {}
        ai_state = state.get("ai_daily") if isinstance(state.get("ai_daily"), dict) else {}
        status_labels = {
            "read": "已阅读",
            "waiting_schedule": "等待定时",
            "all_sources_done": "今日来源已处理",
            "waiting_window": "等待窗口",
            "checking": "正在检查",
            "waiting_today_video": "等待今日视频",
            "today_video_without_text": "今日视频暂无文字版",
            "already_read_today_video": "今日已读",
            "missed_today_ai_daily": "今日窗口已过",
            "digest_failed": "整理失败",
        }
        status = _single_line(ai_state.get("status"), 60) or "未知"
        lines = [
            "AI 日报/早报测试结果：",
            f"- 新闻集成：{'开启' if runtime_persona_setting(self, 'enable_news_integration', False) else '关闭'}",
            f"- AI日报/早报追踪：{'开启' if runtime_persona_setting(self, 'enable_ai_daily_watch', True) else '关闭'}",
            f"- 状态：{status_labels.get(status, status)}",
        ]
        sources = ai_state.get("sources") if isinstance(ai_state.get("sources"), list) else []
        configured_sources = str(runtime_persona_setting(self, "ai_daily_sources", "") or "").strip()
        if configured_sources:
            lines.append("- 来源计划：")
            for raw_line in configured_sources.splitlines()[:8]:
                parts = [part.strip() for part in raw_line.split("|")]
                if len(parts) >= 5:
                    lines.append(f"  - {parts[0]}｜{parts[1]}｜{parts[4]}｜UID {parts[2]}")
        elif sources:
            lines.append("- 来源计划：")
            for item in sources[:8]:
                if not isinstance(item, dict):
                    continue
                lines.append(
                    f"  - {_single_line(item.get('name'), 30)}｜{_single_line(item.get('author_name'), 40)}"
                    f"｜{_single_line(item.get('schedule'), 10)}｜UID {_single_line(item.get('mid'), 32)}"
                )
        date = _single_line(ai_state.get("date"), 20)
        checked = self._format_timestamp_elapsed(ai_state.get("last_checked_at", 0))
        success_date = _single_line(ai_state.get("last_success_date"), 20)
        title = _single_line(ai_state.get("last_video_title"), 120)
        video_link = _single_line(ai_state.get("last_video_link"), 420)
        text_link = _single_line(ai_state.get("last_text_link"), 420)
        candidate_count = _safe_int(ai_state.get("last_candidate_count"), 0, 0)
        digest, selected_item = self._select_ai_daily_digest_item(ai_state)
        if date:
            lines.append(f"- 状态日期：{date}")
        if checked:
            lines.append(f"- 最近检查：{checked}")
        if success_date:
            lines.append(f"- 最近成功日期：{success_date}")
        last_source = _single_line(ai_state.get("last_source_name"), 40)
        last_author = _single_line(ai_state.get("last_source_author"), 60)
        last_schedule = _single_line(ai_state.get("last_source_schedule"), 10)
        if last_source or last_author:
            lines.append(
                "- 最近来源："
                + "｜".join(part for part in (last_source, last_author, last_schedule) if part)
            )
        if title:
            lines.append(f"- 视频：{title}")
        if video_link:
            lines.append(f"- 视频链接：{video_link}")
        owner_name = _single_line(ai_state.get("last_video_owner_name") or selected_item.get("video_owner_name"), 80)
        tname = _single_line(ai_state.get("last_video_tname") or selected_item.get("video_tname"), 60)
        duration = _safe_int(ai_state.get("last_video_duration") or selected_item.get("video_duration"), 0, 0)
        video_context_chars = _safe_int(ai_state.get("last_video_context_chars"), 0, 0)
        if not video_context_chars and selected_item:
            video_context_chars = len(str(selected_item.get("video_context_text") or ""))
        video_tags = ai_state.get("last_video_tags") if isinstance(ai_state.get("last_video_tags"), list) else selected_item.get("video_tags")
        video_tags = [_single_line(tag, 30) for tag in video_tags if _single_line(tag, 30)] if isinstance(video_tags, list) else []
        video_comments = ai_state.get("last_video_hot_comments") if isinstance(ai_state.get("last_video_hot_comments"), list) else selected_item.get("video_hot_comments")
        video_comments = [_single_line(comment, 60) for comment in video_comments if _single_line(comment, 60)] if isinstance(video_comments, list) else []
        meta_parts = []
        if owner_name:
            meta_parts.append(f"UP主 {owner_name}")
        if tname:
            meta_parts.append(f"分区 {tname}")
        if duration:
            meta_parts.append(f"时长 {duration // 60}分{duration % 60}秒")
        if meta_parts:
            lines.append("- 视频信息：" + "｜".join(meta_parts))
        if video_context_chars:
            lines.append(f"- 视频公开信息：已读取 {video_context_chars} 字")
        if video_tags:
            lines.append(f"- 视频标签：{'、'.join(video_tags[:8])}")
        if video_comments:
            lines.append(f"- 热门评论：已读取 {len(video_comments)} 条")
        if text_link:
            lines.append(f"- 文字版链接：{text_link}")
        text_readable_raw = ai_state.get("last_text_readable")
        text_readable = bool(text_readable_raw) if isinstance(text_readable_raw, bool) else bool(selected_item.get("article_readable") and selected_item.get("article_text"))
        text_chars = _safe_int(ai_state.get("last_text_chars"), 0, 0)
        if not text_chars and selected_item:
            text_chars = len(str(selected_item.get("article_text") or ""))
        subtitle_readable_raw = ai_state.get("last_video_subtitle_readable")
        subtitle_readable = bool(subtitle_readable_raw) if isinstance(subtitle_readable_raw, bool) else bool(selected_item.get("video_subtitle_readable") and selected_item.get("video_subtitle_text"))
        subtitle_chars = _safe_int(ai_state.get("last_video_subtitle_chars"), 0, 0)
        if not subtitle_chars and selected_item:
            subtitle_chars = len(str(selected_item.get("video_subtitle_text") or ""))
        subtitle_status = _single_line(ai_state.get("last_video_subtitle_status") or selected_item.get("video_subtitle_status"), 40)
        subtitle_status_labels = {
            "read": "已读取字幕",
            "missing": "公开视频暂无字幕",
            "unavailable": "字幕不可用",
        }
        read_basis = _single_line(ai_state.get("last_read_basis"), 40) or ("完整文字版正文" if text_readable else "视频标题/简介")
        if text_link or selected_item or video_link:
            lines.append(f"- 文字版读取：{'已读取完整正文' if text_readable else '未读取到正文'}")
        if text_chars:
            lines.append(f"- 文字版正文字数：{text_chars}")
        if video_link or selected_item:
            lines.append(f"- 字幕读取：{subtitle_status_labels.get(subtitle_status, '已读取字幕' if subtitle_readable else '未读取到字幕')}")
        if subtitle_chars:
            lines.append(f"- 字幕字数：{subtitle_chars}")
        if read_basis:
            lines.append(f"- 整理依据：{read_basis}")
        if candidate_count:
            lines.append(f"- 候选数量：{candidate_count}")
        source_states = ai_state.get("source_states") if isinstance(ai_state.get("source_states"), dict) else {}
        if source_states:
            lines.append("来源状态：")
            for item in source_states.values():
                if not isinstance(item, dict):
                    continue
                source_title = _single_line(item.get("last_video_title"), 80)
                lines.append(
                    f"- {_single_line(item.get('name'), 30) or '来源'}｜{_single_line(item.get('schedule'), 10) or '未定时'}"
                    f"｜{status_labels.get(_single_line(item.get('status'), 60), _single_line(item.get('status'), 60) or '未知')}"
                    + (f"｜{source_title}" if source_title else "")
                )
        if digest:
            headline = _single_line(digest.get("headline") or digest.get("topic"), 120)
            impression = _single_line(digest.get("impression"), 220)
            if headline:
                lines.append(f"- 摘要重点：{headline}")
            if impression:
                lines.append(f"- 阅读印象：{impression}")
        candidates = ai_state.get("last_candidates") if isinstance(ai_state.get("last_candidates"), list) else []
        if candidates:
            lines.append("最近候选：")
            for item in candidates[:5]:
                if not isinstance(item, dict):
                    continue
                title_line = _single_line(item.get("title"), 90) or "未命名"
                published = _single_line(item.get("published"), 24) or "无发布时间"
                today_mark = "今天" if item.get("is_today") else "非今天"
                lines.append(f"- [{today_mark}] {published}｜{title_line}")
        if not runtime_persona_setting(self, "enable_news_integration", False):
            lines.append("提示：新闻集成关闭时不会执行抓取。")
        elif not runtime_persona_setting(self, "enable_ai_daily_watch", True):
            lines.append("提示：AI 日报/早报追踪关闭时不会执行抓取。")
        return "\n".join(lines)

    def _format_creative_share_action_context(self, user: dict[str, Any]) -> str:
        creative = user.get("creative_share_context")
        if not isinstance(creative, dict):
            return ""
        if _now_ts() - _safe_float(creative.get("created_ts"), 0) > 8 * 3600:
            return ""
        title = _single_line(creative.get("title"), 50)
        work_type = _single_line(creative.get("work_type"), 30) or "作品"
        premise = _single_line(creative.get("premise"), 140)
        tone = _single_line(creative.get("tone"), 40)
        source = _single_line(creative.get("source"), 120)
        snippet = _single_line(creative.get("snippet"), 260)
        current_chars = _safe_int(creative.get("current_chars"), 0, 0)
        target_chars = _safe_int(creative.get("target_chars"), 0, 0)
        parts = [
            "创作分享线索",
            f"作品类型：{work_type}" if work_type else "",
            f"标题：{title}" if title else "",
            f"设定：{premise}" if premise else "",
            f"灵感来源：{source}" if source else "",
            f"行文气质：{tone}" if tone else "",
            f"披露类型：{_single_line(creative.get('disclosure_kind'), 30) or 'milestone'}",
            f"节点：{_single_line(creative.get('milestone'), 30)}" if creative.get("milestone") else "",
            f"当前进度：约 {current_chars}/{target_chars} 字" if current_chars and target_chars else "",
            f"刚写到的片段：{snippet}" if snippet else "",
        ]
        return "\n".join(part for part in parts if part)

    @staticmethod
    def _creative_share_excerpt_prompt_section() -> PromptSection:
        return prompt_section(
            key="proactive.creative_excerpt",
            title="创作分享的正文边界",
            source="proactive_message",
            content=(
                "- 如果要把作品原文发给对方，只能从“刚写到的片段”中连续截取，不得改写、拼接或另编一段冒充原文。\n"
                "- 把实际作品摘录完整放在一组成对的 `「...」` 中；`「」` 内只放作品原文，聊天式引入、感受、提问和收尾都放在引号外。\n"
                "- 不要把整条聊天都包进 `「」`。如果本轮只聊创作进度、没有实际摘录作品，就不要使用 `「」`。\n"
                "- `「...」` 会作为一个完整作品气泡发送；它前后的普通聊天仍按自然聊天节奏分段。"
            ),
        )

    @classmethod
    def _creative_share_excerpt_prompt_hint(cls) -> str:
        return render_prompt_sections(
            [cls._creative_share_excerpt_prompt_section()],
            mode=PromptRenderMode.LABELED_BLOCK,
        )

    @staticmethod
    def _screen_narration_prompt_document(
        *,
        screen_term: str,
        worldview_adaptation: str,
        cleaned_context: str,
    ) -> PromptDocument:
        return prompt_document(
            user_render=_PROACTIVE_DOCUMENT_RENDER,
            user=(
                _proactive_prompt_part(prompt_section(
                    key="background.screen_narration",
                    title="屏幕观察内部摘要",
                    source="proactive_message",
                    template=(
                        "请把下面的{screen_term}观察结果转成“视觉识别后的内部摘要”,供角色继续私聊使用。\n"
                        "要求：\n"
                        "1. 只描述视觉上看出来的内容,不要猜测工具调用过程,不要输出工具名、action 名、报错栈。\n"
                        "2. 只概括用户大概正在看什么、做什么、情绪上是否像在忙,不要复述完整文字、账号、聊天原文、隐私细节。\n"
                        "3. 绝对不要直接对用户说话,不要安慰、提醒、陪伴、劝休息,不要写成一条完整回复。\n"
                        "4. 要像看了一眼{screen_term}后留在脑子里的印象,不要写成建议列表。\n"
                        "5. 50 字以内,只输出摘要本身。\n\n"
                        "{worldview_adaptation}\n\n"
                        "原始结果：\n"
                        "{cleaned_context}"
                    ),
                    variables={
                        "screen_term": screen_term,
                        "worldview_adaptation": worldview_adaptation,
                        "cleaned_context": cleaned_context,
                    },
                ), mode=PromptRenderMode.BODY_ONLY),
            ),
            metadata={"task": "screen_narration"},
        )

    async def _narrate_action_context(self, action: str, action_context: str) -> str:
        narration_provider_id = _persona_provider_id(
            self, "NARRATION_PROVIDER_ID", "narration_provider_id", "fast"
        )
        if not narration_provider_id:
            return self._sanitize_action_context_text(action, action_context)
        if action in {"message", "photo_text", "poke", "voice"} or "photo_text" in action or "voice" in action or "poke" in action or not action_context:
            return self._sanitize_action_context_text(action, action_context)
        cleaned_context = self._sanitize_action_context_text(action, action_context)
        terms = self._worldview_terms()
        worldview_adaptation = self._format_worldview_adaptation_prompt()
        prompt = render_prompt_document(
            self._screen_narration_prompt_document(
                screen_term=terms["screen"],
                worldview_adaptation=worldview_adaptation,
                cleaned_context=cleaned_context,
            )
        )["user"]
        text = await self._llm_call(
            prompt,
            max_tokens=80,
            provider_id=narration_provider_id,
            task="screen_narration",
        )
        return _single_line(text, 120) if text else cleaned_context

    def _sanitize_action_context_text(self, action: str, action_context: str) -> str:
        text = str(action_context or "").strip()
        if "screen_peek" not in action:
            return text
        text = re.sub(r"^screen_peek[:：]\s*", "", text).strip()
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            return ""
        cleaned_lines = []
        for line in lines:
            if re.search(r"(我会一直在这里陪着你|要注意休息|记得休息|辛苦|别太累|我会陪着你)", line):
                continue
            line = re.sub(r"^(?:还在|你还在|感觉|看起来)", "", line).strip(",。！？ ")
            cleaned_lines.append(line)
        collapsed = ",".join(line for line in cleaned_lines if line)
        collapsed = collapsed.replace("用户", "")
        collapsed = re.sub(r"\s+", " ", collapsed).strip(",。！？ ")
        if not collapsed:
            collapsed = lines[0]
        return _single_line(collapsed, 140)

    def _external_share_source_consistency_decision(
        self,
        user: dict[str, Any],
        text: str,
        *,
        reason: str = "",
        topic: str = "",
        motive: str = "",
        action_context: str = "",
    ) -> dict[str, Any] | None:
        cleaned = _single_line(text, 240)
        if not cleaned:
            return None
        source_text = self._external_share_anchor_text(
            user,
            reason=reason,
            topic=topic,
            motive=motive,
            action_context=action_context,
        )
        if not source_text:
            return {
                "decision": "drop",
                "reason": "外界分享缺少可见来源",
                "hard": True,
            }
        source_link_match = re.search(r"https?://[^\s；，。！？!?]+", source_text, flags=re.I)
        source_link = source_link_match.group(0).rstrip("）)】]》>。.") if source_link_match else ""
        expected_platform = self._external_share_platform_from_url(source_link)
        claimed_platform = self._external_share_claimed_platform(cleaned)
        platform_mismatch = bool(
            source_link
            and claimed_platform
            and (not expected_platform or claimed_platform != expected_platform)
        )
        if platform_mismatch:
            expected_label = expected_platform or "该网页来源"
            reference = self._external_share_fallback_reference(source_text)
            if reference:
                return {
                    "decision": "rewrite",
                    "reason": f"来源平台错配：链接属于{expected_label}，正文却写成{claimed_platform}",
                    "reference_text": reference,
                    "source_text": source_text,
                    "hard": True,
                }
            return {
                "decision": "drop",
                "reason": f"来源平台错配：应为{expected_label}而不是{claimed_platform}",
                "hard": True,
            }
        require_source_link = bool(
            runtime_persona_setting(self, "external_share_require_source_link", True)
        )
        if require_source_link and source_link and source_link not in cleaned:
            reference = self._external_share_fallback_reference(source_text)
            if reference:
                return {
                    "decision": "rewrite",
                    "reason": "外界分享正文遗漏真实来源链接",
                    "reference_text": reference,
                    "source_text": source_text,
                    "hard": True,
                }
        if self._external_share_text_mentions_source(cleaned, source_text):
            return None
        reference = self._external_share_fallback_reference(source_text)
        if reference:
            return {
                "decision": "rewrite",
                "reason": "外界分享正文偏离来源",
                "reference_text": reference,
                "source_text": source_text,
                "hard": True,
            }
        return {
            "decision": "defer",
            "reason": "外界分享缺少可承接来源",
            "delay_minutes": 75,
            "hard": True,
        }

    def _external_share_anchor_text(
        self,
        user: dict[str, Any],
        *,
        reason: str = "",
        topic: str = "",
        motive: str = "",
        action_context: str = "",
    ) -> str:
        parts: list[str] = []

        def add(value: Any, limit: int = 160) -> None:
            text = self._clean_external_share_source_field(value, limit)
            if text and text not in parts:
                parts.append(text)

        add(topic, 180)
        if action_context:
            for raw_line in str(action_context or "").splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                if self._looks_like_internal_provider_error_text(line):
                    continue
                if re.match(
                    r"^(?:标题|话题|摘要重点|搜索词|参考来源|来源|链接|UP|短评|回味|内部印象|留下的印象)[:：]",
                    line,
                    flags=re.I,
                ):
                    add(line, 220)
        if isinstance(user, dict):
            context_keys = {
                "bili_video_share": ("bilibili_video_context",),
                "news_share": ("news_context",),
                "web_exploration_share": ("web_exploration_context",),
            }.get(str(reason or "").strip(), ())
            for key in context_keys:
                payload = user.get(key)
                if not isinstance(payload, dict):
                    continue
                prefixed_fields = {
                    "topic": "话题",
                    "headline": "标题",
                    "title": "标题",
                    "source": "来源",
                    "selected_source": "来源",
                    "source_title": "参考来源",
                    "selected_link": "链接",
                    "source_url": "链接",
                    "link": "链接",
                    "url": "链接",
                }
                for field, prefix in prefixed_fields.items():
                    value = payload.get(field)
                    if value:
                        add(f"{prefix}：{value}", 180)
                for field in ("summary", "impression", "comment", "review", "bvid"):
                    add(payload.get(field), 180)
        if not parts:
            add(motive, 140)
        return _single_line("；".join(parts), 760)

    def _external_share_is_vague_pointer(self, text: str) -> bool:
        message = _single_line(text, 260)
        if not message:
            return False
        if re.search(r"https?://|(?:^|[^A-Za-z0-9])BV[0-9A-Za-z]{8,16}(?:$|[^A-Za-z0-9])", message):
            return False
        if re.search(r"[《“\"『「][^》”\"』」]{2,80}[》”\"』」]", message):
            return False
        compact = re.sub(r"[\s，,。！？!?、~～…]+", "", message)
        vague_patterns = (
            "你快看这个",
            "快看这个",
            "看这个",
            "你看看这个",
            "看看这个",
            "给你看个东西",
            "刷到个东西",
            "这个也太",
            "这个太",
            "这个好",
            "这条也太",
            "这条太",
            "这也太",
            "居然这么",
        )
        if any(pattern in compact for pattern in vague_patterns):
            return True
        if len(compact) <= 26 and any(token in compact for token in ("这个", "这条", "那条", "东西")) and any(
            token in compact for token in ("离谱", "逆天", "好笑", "绷不住", "惊了", "怪")
        ):
            return True
        return False

    def _external_share_text_mentions_source(self, text: str, source_text: str) -> bool:
        message = _single_line(text, 260).lower()
        source = _single_line(source_text, 760).lower()
        if not message or not source:
            return False
        if self._looks_like_internal_provider_error_text(message):
            return False
        if self._external_share_is_vague_pointer(message):
            return False
        anchor_tokens = self._external_share_anchor_tokens(source)
        for token in anchor_tokens:
            if token and token in message:
                return True
        return False

    def _external_share_anchor_tokens(self, source_text: str) -> list[str]:
        text = _single_line(source_text, 760)
        if not text:
            return []
        tokens: list[str] = []

        def add(value: str) -> None:
            clean = value.strip(" \t\r\n，。！？；：、,.!?;:()（）[]【】《》“”\"'")
            if len(clean) >= 2 and clean not in tokens:
                tokens.append(clean)

        for item in re.findall(r"[A-Za-z]+[-_A-Za-z0-9]*|[0-9]+(?:多年|年|月|日|次|个|%)?", text):
            add(item.lower())
        for chunk in re.split(r"[\s，。！？；：、,.!?;:|｜/\\\\()（）\\[\\]【】《》“”\"']+", text):
            chunk = chunk.strip()
            if not chunk:
                continue
            if re.fullmatch(r"[\u4e00-\u9fff]{2,12}", chunk):
                add(chunk)
                if len(chunk) > 4:
                    for size in (4, 3, 2):
                        for index in range(0, max(0, len(chunk) - size + 1)):
                            add(chunk[index:index + size])
            elif re.search(r"[\u4e00-\u9fff]", chunk):
                for item in re.findall(r"[\u4e00-\u9fff]{2,8}", chunk):
                    add(item)
        generic = {
            "标题", "视频", "新闻", "文章", "资料", "来源", "分享", "短评", "回味", "评分", "链接",
            "这个", "这条", "那条", "那个", "东西", "内容", "感觉", "有点", "刚刚", "刚才",
            "离谱", "逆天", "好笑", "有趣", "震惊", "惊了", "神奇", "奇怪", "贴", "轻轻",
            "刚刷到一个视频", "刚刷到", "刷到一", "到一个", "一个视", "个视频", "一个视频",
            "b站视频分享线索", "站视频分享线索", "视频分享线索", "分享线索",
            "新闻阅读线索", "阅读线索", "刚扫过", "扫过几", "几条新", "条新闻",
            "网页探索线索", "探索线索", "内部探索笔记", "探索笔记",
            "http", "https", "www", "com", "cn", "bilibili", "video",
        }
        generic_phrases = (
            "b站视频分享线索刚刷到一个视频",
            "新闻阅读线索刚扫过几条新闻其中一条让自己有点想私下提一句",
            "网页探索线索bot刚刚按自己的兴趣主动搜索并了解了一点新东西这是一条内部探索笔记",
            "表达要求不要像播报新闻不要夸大或补充未知事实",
        )
        return [
            token
            for token in tokens
            if token not in generic and not any(token in phrase for phrase in generic_phrases)
        ][:24]

    def _external_share_fallback_reference(self, source_text: str) -> str:
        source = _single_line(source_text, 760)
        if not source:
            return ""
        title = ""
        link = ""
        link_match = re.search(r"https?://[^\s；，。！？!?]+", source, flags=re.I)
        if link_match:
            link = _single_line(link_match.group(0).rstrip("）)】]》>。."), 220)
        source_platform = self._external_share_platform_from_url(link)
        bvid_match = re.search(r"\bBV[0-9A-Za-z]{8,16}\b", source)
        if not link and bvid_match:
            link = f"https://www.bilibili.com/video/{bvid_match.group(0)}"
        reference_match = re.search(r"(?:参考来源|source_title)[:：]\s*([^；。\n\r|｜]{2,90})", source, flags=re.I)
        if reference_match:
            title = _single_line(reference_match.group(1), 64)
        book_match = re.search(r"[《“\"『「]([^》”\"』」]{2,90})[》”\"』」]", source)
        if not title and book_match:
            title = _single_line(book_match.group(1), 64)
        for pattern in (
            r"(?:标题|摘要重点|话题|参考来源|source_title|headline|topic)[:：]\s*([^；。\n\r|｜]{2,90})",
            r"^([^；。\n\r]{4,90})",
        ):
            if title:
                break
            match = re.search(pattern, source, flags=re.I)
            if match:
                title = _single_line(match.group(1), 64)
                title = re.split(
                    r"\s+(?:链接|UP|评分|心情|短评|回味|来源|内部印象|表达气质|额外边界|参考来源|搜索词)[:：]",
                    title,
                    maxsplit=1,
                    flags=re.I,
                )[0]
                break
        if not title:
            if link:
                return _single_line(link, 260)
            return ""
        title = title.strip(" ，。！？；：、,.!?;:|｜")
        if not title:
            if link:
                return _single_line(link, 260)
            return ""
        if self._looks_like_internal_provider_error_text(title):
            return ""
        impression_match = re.search(
            r"(?:留下的印象|内部印象|短评|回味)[:：]\s*([^；\n\r]{4,70})",
            source,
            flags=re.I,
        )
        impression = _single_line(impression_match.group(1), 42).rstrip("。！？!?；;，,") if impression_match else ""
        impression = re.sub(r"让人", "让我", impression)
        if source_platform:
            base = f"刚在{source_platform}刷到“{title}”"
        else:
            base = f"刚看到“{title}”这条内容"
        if impression and impression != title and len(base) + len(impression) <= 96:
            base = f"{base}，{impression}"
        else:
            base = f"{base}，有点想给你看看"
        if link:
            base = f"{base}。{link}"
        else:
            base = f"{base}。"
        return _single_line(base, 300)

