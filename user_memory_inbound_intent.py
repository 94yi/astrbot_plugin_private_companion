# -*- coding: utf-8 -*-
"""入站意图习惯与沉默决策。

由 tools/split_mixin_domain.py 从 user_memory.py 机械抽取（35 个方法 + 0 个模块级名字 + 0 个类级赋值 / 1466 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 UserMemoryMixin）。
"""
from __future__ import annotations

import asyncio
import hashlib
import random
import re
import time
from .companion_interaction_expression import current_interaction_projection
from .conversation_prompt_section import prompt_section
from .domains.affect.interaction_dynamics import settle_interaction_dynamics
from .helpers import _now_ts, _safe_float, _safe_int, _single_line, _today_key
from .persona_config import runtime_persona_setting
from .relationship_policy import relationship_stage_for_score
from .user_memory_render_shared import logger, _render_user_memory_background_prompt, _render_user_memory_labeled_section
from datetime import datetime
from typing import Any





class UserMemoryInboundIntentMixin:
    """入站意图习惯与沉默决策（从 UserMemoryMixin 拆出）。"""


    def _time_bucket_for_user_habit(self, when: datetime | None = None) -> tuple[str, int]:
        when = when or datetime.now()
        minute = when.hour * 60 + when.minute
        buckets = (
            ("凌晨", 0, 6 * 60),
            ("早晨", 6 * 60, 9 * 60),
            ("上午", 9 * 60, 11 * 60 + 30),
            ("中午", 11 * 60 + 30, 14 * 60),
            ("下午", 14 * 60, 18 * 60),
            ("傍晚", 18 * 60, 20 * 60),
            ("夜晚", 20 * 60, 23 * 60),
            ("深夜", 23 * 60, 24 * 60),
        )
        for label, start, end in buckets:
            if start <= minute < end:
                return label, minute
        return "凌晨", minute

    def _classify_user_habit_message(self, text: str) -> tuple[str, str, str]:
        cleaned = _single_line(text, 220)
        lowered = cleaned.lower()
        if not cleaned:
            return "", "", ""
        compact = re.sub(r"\s+", "", cleaned)
        if self._user_habit_message_is_noise(cleaned):
            return "", "", ""
        category = ""
        topic = ""
        profile = self._detect_private_user_retrieval_habit(cleaned)
        if profile:
            category = "固定检索"
            topic = _single_line(profile.get("topic"), 80) or cleaned
        elif re.fullmatch(r"(?:早|早安|早上好|午安|中午好|晚上好|晚安)(?:呀|啊|哦|喔|啦|～|~|！|!)?", compact):
            category = "互动习惯"
            topic = "日常问候"
        elif re.fullmatch(r"(?:摸摸|抱抱|贴贴|亲亲){1,4}(?:呀|啊|哦|啦|～|~|！|!)?", compact):
            category = "互动习惯"
            topic = "亲昵互动"
        elif re.fullmatch(r"(?:你)?(?:在干嘛|在做什么|做什么呢|在吗)(?:呀|啊|呢|？|\?)?", compact):
            category = "互动习惯"
            topic = "询问近况"
        elif any(token in cleaned for token in ("喜欢", "讨厌", "想要", "以后", "每天", "经常", "总是", "习惯")):
            category = "偏好习惯"
            topic = cleaned
        elif self._user_habit_has_self_state(cleaned, "饮食"):
            category = "饮食节奏"
            if any(token in cleaned for token in ("还没", "没吃", "没来得及", "没饭", "没到饭点")):
                topic = "还没吃/饭点偏晚"
            elif any(token in cleaned for token in ("吃了", "刚吃", "吃完", "饱")):
                topic = "已经吃过饭"
            else:
                topic = "吃饭相关"
        elif self._user_habit_has_self_state(cleaned, "作息"):
            category = "作息节奏"
            if any(token in cleaned for token in ("还没睡", "睡不着", "熬夜")):
                topic = "夜里还没睡"
            elif any(token in cleaned for token in ("起床", "刚醒", "醒了")):
                topic = "起床/刚醒"
            else:
                topic = "睡眠相关"
        elif self._user_habit_has_self_state(cleaned, "学习工作"):
            category = "学习工作"
            topic = "学习/工作节奏"
        elif self._user_habit_has_self_state(cleaned, "娱乐"):
            category = "娱乐习惯"
            topic = "娱乐/刷内容"
        if not category or not topic:
            return "", "", ""
        signature_core = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9_]+", "", topic.casefold())[:80]
        signature = f"{category}|{signature_core}"
        return category, _single_line(topic, 80), signature

    @staticmethod
    def _user_habit_message_is_noise(text: str) -> bool:
        cleaned = _single_line(text, 240)
        lowered = cleaned.lower()
        if not cleaned or len(cleaned) > 180:
            return True
        if any(token in lowered for token in (
            "bili_live_probe", "bili状态", "photo_share", "private_companion", "<timer", "<tts",
            "我转发了一段聊天记录", "你看看里面在说什么", "合并转发", "聊天记录",
        )):
            return True
        if re.search(r"^(?:私聊|群聊)?(?:告诉|转告|提醒|叫|转发给).{1,40}", cleaned):
            return True
        if re.search(r"(?:帮我|能去|可以去).{0,12}(?:告诉|叫|转告|提醒).{1,40}", cleaned):
            return True
        return False

    @staticmethod
    def _user_habit_has_self_state(text: str, kind: str) -> bool:
        cleaned = _single_line(text, 180)
        if not cleaned or re.search(r"(?:你|他|她|它|别人|群里).{0,8}", cleaned[:20]):
            return False
        markers = {
            "饮食": r"(?:我.{0,10}(?:吃|饿|饱)|(?:还没吃|没吃|刚吃|吃完|饿了|好饿|饱了|去吃饭|准备吃))",
            "作息": r"(?:我.{0,10}(?:睡|醒|困|起床|熬夜)|(?:睡觉啦|准备睡|去睡了|刚睡醒|醒了|起床了|困了|睡不着|还没睡))",
            "学习工作": r"(?:我.{0,12}(?:学习|上班|下班|工作|上课|下课|写作业|考试|摸鱼)|(?:去上班|下班了|上课了|下课了|写作业|准备考试))",
            "娱乐": r"(?:我.{0,12}(?:玩|看|刷|追)|(?:在玩|去玩|在看|刚看|最近看|正在刷|准备看).{0,20}(?:游戏|视频|番|漫画|小说|直播)?)",
        }
        return bool(re.search(markers.get(kind, r"$^"), cleaned))

    def _detect_private_user_retrieval_habit(self, text: str) -> dict[str, Any]:
        cleaned = _single_line(text, 220)
        compact = re.sub(r"\s+", "", cleaned).lower()
        if not compact:
            return {}
        is_question = bool(re.search(r"[？?]|什么|啥|哪|几|多少|有没有|吗|呢|了没|了吗|颜色|色", compact))
        if not is_question:
            return {}
        if any(token in compact for token in ("衣服", "穿搭", "穿着", "穿什么", "穿了什么", "裙子", "外套", "上衣", "校服", "裤子", "鞋子")) and any(
            token in compact for token in ("颜色", "什么色", "啥色", "什么颜色", "穿什么", "穿了什么", "今天穿", "现在穿")
        ):
            return {
                "intent": "current_outfit_query",
                "topic": "询问 Bot 当前穿着/衣服颜色",
                "query_anchors": ["当前穿搭", "今日穿搭", "每日穿搭", "衣服颜色", "穿什么", "穿了什么", "daily_outfit", "persona_life"],
                "answer_hints": ["优先检索今日穿搭图、当前日程和最近自我生活记忆", "回答时直接说当前准确穿着和颜色,不要泛泛说可能"],
            }
        if any(token in compact for token in ("吃了什么", "吃什么", "晚饭", "午饭", "早餐", "夜宵")) and any(token in compact for token in ("你", "bot", "星缘", "今天", "刚才", "现在")):
            return {
                "intent": "current_meal_query",
                "topic": "询问 Bot 最近吃了什么",
                "query_anchors": ["self_meal", "吃了什么", "午餐", "晚餐", "早餐", "夜宵", "persona_life"],
                "answer_hints": ["优先检索 Bot 自我进食记录和当前日程", "如果没有准确记录,说明没记清,不要编具体食物"],
            }
        if any(token in compact for token in ("在干嘛", "在做什么", "忙什么", "现在做", "刚才做")) and any(token in compact for token in ("你", "bot", "星缘", "现在", "刚才", "今天")):
            return {
                "intent": "current_activity_query",
                "topic": "询问 Bot 当前/最近在做什么",
                "query_anchors": ["当前日程", "日程细化", "self_timeline", "persona_life", "在做什么"],
                "answer_hints": ["优先检索当前日程、日程细化和自我时间线", "按最近准确记录回答,不要把很久前的状态当现在"],
            }
        return {}

    def _update_user_behavior_habits_from_message(self, user: dict[str, Any], text: str) -> None:
        if not runtime_persona_setting(self, "enable_user_habit_learning", True):
            return
        cleaned = _single_line(text, 220)
        if not cleaned or cleaned.startswith(("/", "!", "！", "#")):
            return
        sleep_delay_detector = getattr(self, "_detect_sleep_delay_request", None)
        if callable(sleep_delay_detector):
            try:
                if sleep_delay_detector(cleaned):
                    return
            except Exception:
                pass
        category, topic, signature = self._classify_user_habit_message(cleaned)
        if not category or not signature:
            return
        now_dt = datetime.now()
        day_key = now_dt.strftime("%Y-%m-%d")
        bucket, minute = self._time_bucket_for_user_habit(now_dt)
        habits = user.setdefault("behavior_habits", {})
        if not isinstance(habits, dict):
            habits = {}
            user["behavior_habits"] = habits
        patterns = habits.setdefault("patterns", [])
        if not isinstance(patterns, list):
            patterns = []
            habits["patterns"] = patterns
        self._sanitize_user_behavior_habit_patterns(user)
        patterns = habits.get("patterns") if isinstance(habits.get("patterns"), list) else []
        key = f"{bucket}|{category}|{signature}"
        matched = None
        for item in patterns:
            if isinstance(item, dict) and str(item.get("key") or "") == key:
                matched = item
                break
        if matched is None:
            matched = {
                "key": key,
                "bucket": bucket,
                "category": category,
                "topic": topic,
                "signature": signature,
                "count": 0,
                "avg_minute": minute,
                "examples": [],
                "created_ts": _now_ts(),
            }
            patterns.append(matched)
        retrieval_profile = self._detect_private_user_retrieval_habit(cleaned)
        if retrieval_profile:
            matched["intent"] = _single_line(retrieval_profile.get("intent"), 60)
            matched["query_anchors"] = [
                _single_line(item, 40)
                for item in retrieval_profile.get("query_anchors", [])
                if _single_line(item, 40)
            ][:12]
            matched["answer_hints"] = [
                _single_line(item, 80)
                for item in retrieval_profile.get("answer_hints", [])
                if _single_line(item, 80)
            ][:8]
            matched["memory_key"] = hashlib.sha1(
                f"{str(user.get('user_id') or user.get('id') or '')}|{key}".encode("utf-8", errors="ignore")
            ).hexdigest()[:20]
        count = _safe_int(matched.get("count"), 0, 0) + 1
        old_avg = _safe_float(matched.get("avg_minute"), minute)
        matched["count"] = min(999, count)
        matched["avg_minute"] = round((old_avg * max(0, count - 1) + minute) / max(1, count), 1)
        matched["last_seen_ts"] = _now_ts()
        matched["last_seen_text"] = cleaned
        evidence_days = matched.get("evidence_days")
        if not isinstance(evidence_days, list):
            evidence_days = []
        evidence_days.append(day_key)
        matched["evidence_days"] = list(dict.fromkeys(str(item) for item in evidence_days if str(item)))[-30:]
        examples = matched.get("examples")
        if not isinstance(examples, list):
            examples = []
        examples.insert(0, cleaned)
        matched["examples"] = list(dict.fromkeys(_single_line(item, 90) for item in examples if _single_line(item, 90)))[:5]
        patterns.sort(
            key=lambda item: (
                _safe_int(item.get("count"), 0, 0) if isinstance(item, dict) else 0,
                _safe_float(item.get("last_seen_ts"), 0) if isinstance(item, dict) else 0,
            ),
            reverse=True,
        )
        del patterns[runtime_persona_setting(self, "user_habit_max_items", 24):]
        habits["updated_at"] = now_dt.strftime("%Y-%m-%d %H:%M")
        self._maybe_sync_user_behavior_habit_to_memory_companion(user, matched)

    def _sanitize_user_behavior_habit_patterns(self, user: dict[str, Any]) -> bool:
        habits = user.get("behavior_habits") if isinstance(user, dict) else None
        if not isinstance(habits, dict):
            return False
        patterns = habits.get("patterns")
        if not isinstance(patterns, list):
            return False
        allowed_categories = {
            "固定检索", "互动习惯", "偏好习惯", "饮食节奏", "作息节奏", "学习工作", "娱乐习惯",
        }
        kept: list[dict[str, Any]] = []
        for item in patterns:
            if not isinstance(item, dict) or str(item.get("category") or "") not in allowed_categories:
                continue
            evidence_days = item.get("evidence_days")
            if not isinstance(evidence_days, list) or not any(str(day) for day in evidence_days):
                continue
            kept.append(item)
        if len(kept) == len(patterns):
            return False
        habits["patterns"] = kept[: runtime_persona_setting(self, "user_habit_max_items", 24)]
        habits["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        return True

    def _maybe_sync_user_behavior_habit_to_memory_companion(self, user: dict[str, Any], habit: dict[str, Any]) -> None:
        if not isinstance(user, dict) or not isinstance(habit, dict):
            return
        if str(habit.get("category") or "") != "固定检索":
            return
        min_count = max(2, runtime_persona_setting(self, "user_habit_min_count", 3))
        if _safe_int(habit.get("count"), 0, 0) < min_count:
            return
        now = _now_ts()
        if now - _safe_float(habit.get("memory_synced_at"), 0) < 12 * 3600:
            return
        recorder = getattr(self, "_memory_companion_record_user_habit", None)
        if not callable(recorder):
            return
        user_id = _single_line(user.get("user_id") or user.get("id"), 80)
        if not user_id:
            return
        habit["memory_synced_at"] = now
        operation = recorder(user=user, user_id=user_id, habit=dict(habit))
        try:
            creator = getattr(self, "_create_lifecycle_background_task", None)
            task = (
                creator(operation, label="user_habit_memory_sync")
                if callable(creator)
                else asyncio.create_task(operation, name="private-companion-user-habit-memory-sync")
            )
            if task is None:
                raise RuntimeError("background task unavailable")
            if not callable(creator):
                def consume(done_task: asyncio.Task) -> None:
                    try:
                        done_task.result()
                    except asyncio.CancelledError:
                        pass
                    except Exception as exc:
                        logger.warning(
                            "用户习惯记忆同步后台任务失败: %s",
                            _single_line(exc, 160),
                        )

                task.add_done_callback(consume)
        except Exception:
            close = getattr(operation, "close", None)
            if callable(close):
                close()
            habit["memory_synced_at"] = 0

    def _format_user_habit_time(self, minute_value: Any) -> str:
        minute = int(max(0, min(1439, round(_safe_float(minute_value, 0)))))
        return f"{minute // 60:02d}:{minute % 60:02d}"

    @staticmethod
    def _minute_distance(a: float, b: float) -> float:
        diff = abs(float(a) - float(b)) % 1440
        return min(diff, 1440 - diff)

    def _user_habit_effective_score(self, item: dict[str, Any], *, now: float | None = None) -> float:
        now = now or _now_ts()
        evidence_days = item.get("evidence_days")
        count = (
            len(set(str(day) for day in evidence_days if str(day)))
            if isinstance(evidence_days, list)
            else 0
        )
        age_days = max(0.0, (now - _safe_float(item.get("last_seen_ts"), now)) / 86400)
        if age_days <= 7:
            recency = 1.0
        elif age_days <= 30:
            recency = max(0.2, 1.0 - (age_days - 7) / 23 * 0.8)
        else:
            recency = 0.0
        return count * recency

    def _qualified_user_behavior_habits(self, user: dict[str, Any]) -> list[dict[str, Any]]:
        self._sanitize_user_behavior_habit_patterns(user)
        habits = user.get("behavior_habits")
        if not isinstance(habits, dict):
            return []
        patterns = habits.get("patterns")
        if not isinstance(patterns, list):
            return []
        now = _now_ts()
        min_count = max(2, runtime_persona_setting(self, "user_habit_min_count", 3))
        kept = []
        for item in patterns:
            if not isinstance(item, dict):
                continue
            if now - _safe_float(item.get("last_seen_ts"), now) > 30 * 86400:
                continue
            if _safe_int(item.get("count"), 0, 0) < min_count:
                continue
            evidence_days = item.get("evidence_days")
            if not isinstance(evidence_days, list) or len(set(str(day) for day in evidence_days if str(day))) < min_count:
                continue
            if self._user_habit_effective_score(item, now=now) < max(1.6, min_count * 0.45):
                continue
            kept.append(item)
        kept.sort(
            key=lambda item: (
                self._user_habit_effective_score(item, now=now),
                _safe_float(item.get("last_seen_ts"), 0),
            ),
            reverse=True,
        )
        return kept

    def _user_habit_related_to_text(self, item: dict[str, Any], text: str) -> bool:
        cleaned = _single_line(text, 260)
        if not cleaned:
            return False
        category = str(item.get("category") or "")
        topic = _single_line(item.get("topic"), 80)
        mapping = {
            "饮食节奏": ("吃", "饭", "早餐", "午饭", "晚饭", "夜宵", "饿", "饱", "零食", "喝"),
            "作息节奏": ("睡", "醒", "起床", "熬夜", "困", "晚安", "早安", "梦"),
            "学习工作": ("作业", "上课", "下课", "考试", "题", "学习", "上班", "下班", "工作", "摸鱼"),
            "娱乐习惯": ("游戏", "视频", "番", "漫画", "小说", "直播", "刷", "看"),
            "固定提问": ("？", "?", "什么", "多少", "吗", "呢", "怎么", "有没有", "要不要"),
            "偏好习惯": ("喜欢", "讨厌", "想要", "以后", "每天", "经常", "总是", "习惯"),
        }
        if any(token in cleaned for token in mapping.get(category, ())):
            return True
        tokens = re.findall(r"[\u4e00-\u9fff]{2,8}|[A-Za-z0-9_]{3,24}", topic)
        return any(token and token in cleaned for token in tokens)

    def _natural_user_habit_line(self, item: dict[str, Any]) -> str:
        bucket = _single_line(item.get("bucket"), 12)
        category = _single_line(item.get("category"), 20)
        topic = _single_line(item.get("topic"), 80)
        if not topic:
            return ""
        if category == "饮食节奏":
            if "还没吃" in topic or "饭点偏晚" in topic:
                return f"{bucket}时对方常会提到还没吃饭，聊到吃的可以轻轻接住，不用像提醒。"
            if "已经吃过" in topic:
                return f"{bucket}时对方常已经吃过饭，别每次都追问吃没吃。"
            return f"{bucket}时对方容易聊到吃饭，相关时顺手接住就好。"
        if category == "作息节奏":
            if "夜里还没睡" in topic:
                return f"{bucket}时对方常还醒着，聊到睡觉时少一点催促，多一点顺着接。"
            if "起床" in topic or "刚醒" in topic:
                return f"{bucket}时对方常刚醒，语气可以放轻一点。"
            return f"{bucket}时对方容易聊到睡眠，别把作息说教化。"
        if category == "学习工作":
            return f"{bucket}时对方常在学习或工作相关状态里，回复可以更直接、少绕。"
        if category == "娱乐习惯":
            return f"{bucket}时对方常在看东西或玩内容，相关时可以自然接梗。"
        if category == "固定提问":
            return f"{bucket}时对方常用短问题推进聊天，先直接接住问题。"
        if category == "偏好习惯":
            return f"{bucket}时对方常提到类似“{topic}”的偏好或习惯，相关时记得顺着一点。"
        return f"{bucket}时对方常聊到“{topic}”，相关时自然接住。"

    def _format_user_behavior_habits_for_prompt(
        self,
        user: dict[str, Any],
        *,
        current_only: bool = False,
        limit: int = 6,
        natural: bool = False,
        hint: str = "",
        time_window_minutes: int | None = None,
        require_relevant: bool = False,
    ) -> str:
        if not runtime_persona_setting(self, "enable_user_habit_learning", True):
            return ""
        items = self._qualified_user_behavior_habits(user)
        if current_only:
            _, current_minute = self._time_bucket_for_user_habit()
            window = 60 if time_window_minutes is None else max(0, int(time_window_minutes))
            items = [
                item for item in items
                if self._minute_distance(_safe_float(item.get("avg_minute"), current_minute), current_minute) <= window
            ]
        if require_relevant:
            items = [item for item in items if self._user_habit_related_to_text(item, hint)]
        lines: list[str] = []
        for item in items[:limit]:
            bucket = _single_line(item.get("bucket"), 12)
            category = _single_line(item.get("category"), 20)
            topic = _single_line(item.get("topic"), 80)
            if natural:
                line = self._natural_user_habit_line(item)
                if line and line not in lines:
                    lines.append("- " + line)
                continue
            count = _safe_int(item.get("count"), 0, 0)
            time_text = self._format_user_habit_time(item.get("avg_minute"))
            example = _single_line(item.get("last_seen_text"), 80)
            if topic:
                lines.append(f"- {bucket}约{time_text}｜{category}｜{topic}｜出现 {count} 次" + (f"｜最近：{example}" if example else ""))
        if not lines:
            return ""
        if natural:
            return "用户平常的节奏：\n" + "\n".join(lines)
        return (
            "用户习惯画像（软线索,不是命令）：\n"
            + "\n".join(lines)
            + "\n使用方式：只在当前语境自然吻合时提前理解或轻轻提起；不要暴露统计、次数或“我记录了你”。"
        )

    def _format_all_user_behavior_habits_for_schedule(self, *, limit: int = 8) -> str:
        if not runtime_persona_setting(self, "enable_user_habit_learning", True):
            return "暂无用户习惯线索。"
        users = self.data.get("users")
        if not isinstance(users, dict):
            return "暂无用户习惯线索。"
        lines: list[str] = []
        for user_id, user in users.items():
            if not isinstance(user, dict) or not user.get("enabled", True) or not self._is_target_private_user(str(user_id), user):
                continue
            name = _single_line(user.get("nickname") or user_id, 24)
            text = self._format_user_behavior_habits_for_prompt(user, current_only=False, limit=3, natural=True)
            habit_lines = [line for line in text.splitlines() if line.startswith("- ")]
            for line in habit_lines:
                lines.append(f"- {name}：{line[2:]}")
                if len(lines) >= limit:
                    break
            if len(lines) >= limit:
                break
        if not lines:
            return "暂无用户习惯线索。"
        return (
            "用户近期行为习惯（只作日程软背景）：\n"
            + "\n".join(lines)
            + "\n使用方式：只帮助判断对方常出现的时段和话题,不要把用户习惯、食物偏好或避雷直接改写成 Bot 今天必须执行的购买、带饭、约饭或准备任务。"
        )

    def _habit_proactive_event_for_user(self, user: dict[str, Any], *, now: float | None = None) -> dict[str, Any] | None:
        if not runtime_persona_setting(self, "enable_user_habit_learning", True):
            return None
        now = now or _now_ts()
        now_dt = datetime.fromtimestamp(now)
        _, current_minute = self._time_bucket_for_user_habit(now_dt)
        candidates = []
        for item in self._qualified_user_behavior_habits(user):
            avg_minute = _safe_float(item.get("avg_minute"), current_minute)
            if self._minute_distance(avg_minute, current_minute) > 75:
                continue
            count = _safe_int(item.get("count"), 0, 0)
            candidates.append((self._user_habit_effective_score(item, now=now), count, item))
        if not candidates:
            return None
        candidates.sort(key=lambda pair: (pair[0], pair[1]), reverse=True)
        item = candidates[0][2]
        category = _single_line(item.get("category"), 20)
        topic = _single_line(item.get("topic"), 70)
        if self._habit_topic_is_greeting_like(topic or category) and self._recent_activity_suppresses_habit_greeting(
            user,
            now=now,
            topic=topic or category,
        ):
            return None
        bucket = _single_line(item.get("bucket"), 12)
        delay_minutes = random.randint(4, 28)
        return {
            "date": _today_key(),
            "window": self._window_from_delay_minutes(delay_minutes, width_minutes=20),
            "reason": "habit_awareness",
            "action": "message",
            "why": f"用户最近常在{bucket}出现“{category}”相关话题或行为,这会儿自然想提前理解一下。",
            "topic": topic or category or "用户习惯",
            "motive": f"这会儿像是用户平常会提到“{topic or category}”的时候,想自然接住,不用说自己在统计。",
            "scene": f"{bucket}的惯常互动时段",
            "tone": "熟悉,提前一步",
            "impulse": "像真的记得对方生活节奏一样,轻轻提前接住",
            "_scheduled_ts": now + delay_minutes * 60,
            "_habit_awareness": True,
        }

    def _habit_topic_is_greeting_like(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", _single_line(text, 80))
        if not compact:
            return False
        if re.fullmatch(r"(?:早|早安|早上好|上午好|午安|中午好|晚上好|晚安)", compact):
            return True
        if len(compact) > 16:
            return False
        return bool(
            re.search(r"(?:早安|早上好|上午好|午安|中午好|晚上好|晚安|早间|早晨|早上)", compact)
            and re.search(r"(?:问候|打招呼|招呼|寒暄|开场|醒来|起床)", compact)
        )

    def _recent_activity_suppresses_habit_greeting(self, user: dict[str, Any], *, now: float, topic: str = "") -> bool:
        compact_topic = re.sub(r"\s+", "", _single_line(topic, 80))
        try:
            current_minute = self._environment_fromtimestamp(now).hour * 60 + self._environment_fromtimestamp(now).minute
        except Exception:
            current_minute = datetime.fromtimestamp(now).hour * 60 + datetime.fromtimestamp(now).minute
        if compact_topic in {"早", "早安", "早上好"} and current_minute >= 11 * 60:
            return True
        recent_at = self._latest_private_user_activity_ts(user)
        recent_any = max(
            recent_at,
            _safe_float(user.get("last_user_message_at"), 0),
            _safe_float(user.get("last_companion_message_at"), 0),
            _safe_float(user.get("last_sent"), 0),
        )
        if recent_any > 0 and now - recent_any < max(90, self._effective_user_greeting_idle_minutes(user)) * 60:
            return True
        suppressed = user.get("greetings_suppressed_by_inbound", [])
        if not isinstance(suppressed, list):
            return False
        return any(
            reason in suppressed and self._inbound_satisfies_greeting(reason, now=now, user=user)
            for reason in ("morning_greeting", "noon_greeting", "evening_greeting")
        )

    def _is_structured_or_diagnostic_text(self, text: str) -> bool:
        cleaned = _single_line(text, 260)
        if not cleaned:
            return False
        if re.search(r"https?://|```|Traceback|Error code:|Exception|\[INFO\]|\[WARN\]|\[ERRO\]|\[Core\]", cleaned, re.IGNORECASE):
            return True
        if re.search(r"^\s*(?:/|!|！|陪伴\s|git\b|python\b|node\b|npm\b|pnpm\b|pip\b)", cleaned, re.IGNORECASE):
            return True
        if cleaned.count("[") + cleaned.count("]") >= 6:
            return True
        if re.search(r"(日志|堆栈|traceback)", cleaned, re.IGNORECASE):
            return True
        return False

    def _intent_target_hint(self, text: str) -> tuple[bool, bool]:
        cleaned = _single_line(text, 260)
        target_hint = bool(re.search(r"(你|bot|机器人|插件|星缘|老老老|助手|ai|AI)", cleaned))
        third_party_hint = bool(re.search(r"(数学|作业|代码|报错|他|她|它|他们|她们|别人|群友|那个人|这个人|用户|豆腐|蛙蛙|小水月)", cleaned))
        return target_hint, third_party_hint

    def _is_soft_playful_boundary(self, text: str) -> bool:
        cleaned = _single_line(text, 260)
        return bool(
            re.search(r"(别闹|别这样|不要啊|别呀|不要嘛|讨厌啦|烦啦)", cleaned)
            and re.search(r"(哈|哈哈|hhh|笑死|啦|嘛|呀|哦|捏|~|～|w)", cleaned, re.IGNORECASE)
        )

    def _is_playful_or_ambiguous_boundary(self, text: str) -> bool:
        cleaned = _single_line(text, 260)
        if not cleaned:
            return False
        if self._is_soft_playful_boundary(cleaned):
            return True
        return bool(
            re.search(r"(开玩笑|闹着玩|不是认真的|别当真|随口|口嗨|逗你|玩梗)", cleaned)
            or re.search(r"(哈哈|呵呵|hhh|hha|笑死|绷不住|乐了|233|~|～|qwq|w$)", cleaned, re.IGNORECASE)
        )

    def _action_preference_hint(self, user: dict[str, Any] | None = None) -> str:
        if not isinstance(user, dict):
            return ""
        prefs = user.get("action_preferences")
        if not isinstance(prefs, dict) or not prefs:
            return ""
        labels = {
            "poke": "戳一戳",
            "voice": "语音",
            "photo_text": "图片",
            "screen_peek": "看屏幕",
        }
        lines = []
        for action, item in prefs.items():
            if not isinstance(item, dict):
                continue
            like = _safe_int(item.get("like"), 0, 0)
            dislike = _safe_int(item.get("dislike"), 0, 0)
            note = _single_line(item.get("note"), 60)
            if dislike > like:
                lines.append(f"- {labels.get(action, action)}：用户可能不喜欢或希望少用。{note}")
            elif like > dislike:
                lines.append(f"- {labels.get(action, action)}：用户接受度较高。{note}")
        return "\n".join(lines)

    def _analyze_inbound_intent(self, text: str) -> dict[str, Any]:
        cleaned = _single_line(text, 240)
        if not cleaned:
            return {"intent": "empty", "emotion": "neutral", "pressure": 0, "reply_style": "short", "confidence": 1.0, "source": "empty", "reason": ""}
        if self._is_structured_or_diagnostic_text(cleaned):
            return {
                "intent": "chat",
                "emotion": "neutral",
                "pressure": 0,
                "reply_style": "natural",
                "confidence": 0.2,
                "source": "diagnostic_skip",
                "reason": "结构化/日志/代码类文本不作为情绪依据",
                "emotion_event": "neutral",
                "emotion_intensity": 0,
                "emotion_reason": "",
                "emotion_target": "none",
                "emotion_rule": "diagnostic_skip",
                "emotion_confidence": 0.2,
                "text": cleaned,
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            }
        lower = cleaned.lower()
        intent = "chat"
        emotion = "neutral"
        pressure = 0
        reply_style = "natural"
        confidence = 0.55
        source = "default"
        reason = ""
        target_hint, third_party_hint = self._intent_target_hint(cleaned)
        weak_boundary = bool(re.search(r"(别|不要|讨厌|烦)", cleaned))
        soft_play_boundary = self._is_soft_playful_boundary(cleaned)
        playful_or_ambiguous = self._is_playful_or_ambiguous_boundary(cleaned)
        durable_boundary = bool(
            target_hint
            and not third_party_hint
            and not playful_or_ambiguous
            and re.search(
                r"(?:以后|之后).{0,8}(?:别|不要)|(?:别再|不要再|不许).{0,12}(?:这样|烦|吵|打扰|靠近|贴|撒娇|叫我|问我|说话)|(?:不想|不愿).{0,10}(?:理你|跟你聊|继续聊)|(?:离我远点|别打扰我|别靠近|别贴|别撒娇)",
                cleaned,
            )
        )
        single_turn_boundary = bool(
            target_hint
            and not third_party_hint
            and not playful_or_ambiguous
            and re.search(r"(别|不要|讨厌|烦|闭嘴|滚|离远点)", cleaned)
        )
        if durable_boundary:
            intent = "boundary"
            emotion = "resistant"
            pressure += 3
            reply_style = "back_off"
            confidence = 0.9
            source = "durable_boundary_rule"
            reason = "用户明确、持续地对 Bot 表达边界"
        elif single_turn_boundary:
            reply_style = "short"
            confidence = 0.58
            source = "single_turn_boundary"
            reason = "单句负向表达，先按当下语境短答，不写入长期关系状态"
        elif not playful_or_ambiguous and re.search(r"(烦|累|难受|崩溃|不想|想哭|emo|压力|焦虑|失眠|疼|委屈)", cleaned, re.IGNORECASE):
            intent = "comfort"
            emotion = "low"
            pressure += 2
            reply_style = "soft"
            confidence = 0.82
            source = "comfort_rule"
            reason = "用户表达低落或压力"
        elif re.search(r"(怎么|如何|为什么|帮我|能不能|可以.*吗|教程|代码|报错|分析|解释)", cleaned):
            intent = "help"
            reply_style = "useful"
            pressure += 1
            confidence = 0.78
            source = "help_rule"
            reason = "用户在请求解释或帮助"
        elif re.search(r"(抱抱|亲亲|摸摸|陪我|想你|喜欢你|爱你|贴贴)", cleaned):
            intent = "intimacy"
            emotion = "close"
            reply_style = "warm_short"
            confidence = 0.84
            source = "intimacy_rule"
            reason = "用户表达亲近或陪伴需求"
        elif re.search(r"(哈哈|笑死|草|绷|乐|hhh|233|好玩|乐了)", lower) or soft_play_boundary:
            intent = "play"
            emotion = "light"
            reply_style = "playful"
            confidence = 0.7 if soft_play_boundary else 0.76
            source = "soft_boundary_play_rule" if soft_play_boundary else "play_rule"
            reason = "软边界更像玩笑语气" if soft_play_boundary else "用户在玩梗或轻松表达"
        elif weak_boundary:
            confidence = 0.35
            source = "weak_boundary_ignored"
            reason = "边界词未明显指向 Bot,不硬判为拉开距离"
        if len(cleaned) <= 6 and intent == "chat":
            reply_style = "very_short"
            confidence = 0.62
            source = "short_chat_rule"
            reason = "短句普通接话"
        emotion_event = self._classify_relationship_emotion_event(
            cleaned,
            intent_context={
                "confidence": confidence,
                "source": source,
                "boundary_durable": durable_boundary,
                "playful_or_ambiguous": playful_or_ambiguous,
            },
        )
        boundary_feedback = self._classify_local_boundary_feedback_signal(
            cleaned,
            target_hint=target_hint,
            third_party_hint=third_party_hint,
            playful_or_ambiguous=playful_or_ambiguous,
        )
        return {
            "intent": intent,
            "emotion": emotion,
            "pressure": min(5, pressure),
            "reply_style": reply_style,
            "confidence": round(float(confidence), 2),
            "source": source,
            "reason": reason,
            "emotion_event": emotion_event.get("event", "neutral"),
            "emotion_intensity": emotion_event.get("intensity", 0),
            "emotion_reason": emotion_event.get("reason", ""),
            "emotion_target": emotion_event.get("target", "none"),
            "emotion_rule": emotion_event.get("rule", ""),
            "emotion_confidence": round(_safe_float(emotion_event.get("confidence"), 0.0), 2),
            "violation_severity": _safe_int(emotion_event.get("severity"), 0, 0, 3),
            "boundary_feedback_type": boundary_feedback.get("type", "normal"),
            "boundary_suitable_tier": boundary_feedback.get("suitable_tier", ""),
            "boundary_feedback_reason": boundary_feedback.get("reason", ""),
            "boundary_feedback_confidence": round(_safe_float(boundary_feedback.get("confidence"), 0.0), 2),
            "emotion_attribution": dict(emotion_event.get("attribution")) if isinstance(emotion_event.get("attribution"), dict) else {},
            "boundary_durable": durable_boundary,
            "playful_or_ambiguous": playful_or_ambiguous,
            "text": cleaned,
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        }

    def _classify_local_boundary_feedback_signal(
        self,
        text: str,
        *,
        target_hint: bool = False,
        third_party_hint: bool = False,
        playful_or_ambiguous: bool = False,
    ) -> dict[str, Any]:
        """Find only high-confidence boundary candidates; relationship tiers decide the outcome later."""
        cleaned = _single_line(text, 240)
        if not cleaned or playful_or_ambiguous or self._is_structured_or_diagnostic_text(cleaned):
            return {"type": "normal", "suitable_tier": "", "reason": "", "confidence": 1.0}
        if third_party_hint and not target_hint:
            return {"type": "normal", "suitable_tier": "", "reason": "", "confidence": 0.9}

        # A feeling is not an offence. It only gives the character a short,
        # relationship-aware reaction hint and never reduces affinity.
        if re.search(
            r"(?:^|[，。！？\s])(我(?:真的|一直|最)?(?:喜欢|爱|想念)你|好想你|最喜欢你|真的喜欢你|爱你|想你)(?:呀|啦|呢|哦|啊)?(?:$|[，。！？\s])",
            cleaned,
        ):
            return {
                "type": "confession",
                "suitable_tier": "intimate",
                "reason": "表达喜欢或想念",
                "confidence": 0.9,
            }

        deliberate_malice = bool(
            target_hint
            and not third_party_hint
            and re.search(
                r"(你的(?:家人|朋友|作品|努力|梦想).{0,8}(?:去死|毁掉|一文不值|垃圾)|"
                r"(?:你就是|你根本是).{0,8}(?:废物|垃圾|不配活|没救了))",
                cleaned,
            )
        )
        if deliberate_malice:
            return {
                "type": "malice",
                "suitable_tier": "beyond",
                "reason": "恶意贬低珍视对象或人格",
                "confidence": 0.9,
            }

        explicit_coercion = bool(
            re.search(
                r"(不许拒绝|不准拒绝|没有拒绝权|必须听我的|我说了算|不答应就|不给我就|敢拒绝试试|"
                r"你只能听|强迫你|别想跑|逃不掉)",
                cleaned,
            )
        )
        explicit_harassment = bool(
            re.search(
                r"(脱(?:衣服)?给我看|发(?:裸照|私密照|黄图)|看(?:胸|腿|内衣)|开房|一夜情|做爱|上床)",
                cleaned,
            )
        )
        if explicit_coercion or explicit_harassment:
            return {
                "type": "action",
                "suitable_tier": "beyond",
                "reason": "强迫、纠缠或露骨要求",
                "confidence": 0.94,
            }

        # Keep ordinary comfort such as a standalone "摸摸/抱抱" out of this
        # rule. Only an explicit request or enacted intimate action is a tiered
        # boundary candidate.
        intimate_action = bool(
            re.search(
                r"(给我(?:亲亲|抱抱|晚安吻)|让我(?:亲|抱|搂|摸)|我(?:要|想)(?:亲你|抱住你|搂着你|摸你|牵你的手)|"
                r"(?:亲你|抱住你|搂住你|摸你的脸|牵你的手)(?:一下|一会儿|不放)?)",
                cleaned,
            )
        )
        if intimate_action:
            return {
                "type": "action",
                "suitable_tier": "intimate",
                "reason": "明确提出或实施亲密动作",
                "confidence": 0.88,
            }
        return {"type": "normal", "suitable_tier": "", "reason": "", "confidence": 0.8}

    def _enrich_boundary_feedback_intent(
        self,
        user: dict[str, Any],
        intent: dict[str, Any],
    ) -> dict[str, Any]:
        """Project a candidate onto the current unified relationship tier."""
        if not isinstance(user, dict) or not isinstance(intent, dict):
            return intent
        if not bool(runtime_persona_setting(self, "enable_relationship_boundary_feedback", True)):
            return intent
        try:
            role = self._private_user_role(user, str(user.get("user_id") or ""))
        except Exception:
            role = str(user.get("relationship_role") or "friend")
        if str(role).strip().lower() == "owner":
            intent["boundary_feedback_exempt"] = True
            return intent

        feedback_type = str(intent.get("boundary_feedback_type") or "normal").strip().lower()
        suitable_tier = str(intent.get("boundary_suitable_tier") or "").strip().lower()
        confidence = _safe_float(intent.get("boundary_feedback_confidence"), 0.0, 0.0, 1.0)
        if feedback_type == "confession":
            intent["boundary_feedback_kind"] = "confession"
            return intent
        if feedback_type not in {"action", "malice"} or confidence < 0.72:
            return intent

        tier_order = (
            "deeply_distant", "strongly_distant", "distant", "acquaintance",
            "familiar", "close", "intimate", "deeply_bonded",
        )
        stage = relationship_stage_for_score(
            user.get("relationship_score", 0),
            runtime_persona_setting(self, "relationship_stage_policy", None),
            previous_stage_key=user.get("relationship_phase_key", ""),
        ).get("phase", {})
        current_tier = str(stage.get("key") or "acquaintance")
        intent["boundary_current_tier"] = current_tier
        if feedback_type == "malice":
            severity = 3
            kind = "bottom_line"
        else:
            current_index = tier_order.index(current_tier) if current_tier in tier_order else 3
            if suitable_tier == "beyond":
                gap = 3
            elif suitable_tier in tier_order:
                gap = tier_order.index(suitable_tier) - current_index
            else:
                gap = 0
            if gap <= 0:
                intent["boundary_feedback_kind"] = "accepted_for_tier"
                return intent
            severity = 1 if gap == 1 else 2 if gap == 2 else 3
            kind = "harassment" if suitable_tier == "beyond" else "intimate_overreach"

        intent.update(
            {
                "emotion_event": "boundary_violation",
                "emotion_target": "bot",
                "emotion_intensity": min(100, 58 + severity * 14),
                "emotion_reason": _single_line(
                    intent.get("boundary_feedback_reason") or "超出当前关系边界",
                    100,
                ),
                "emotion_rule": "relationship_boundary_feedback",
                "emotion_confidence": round(confidence, 2),
                "violation_severity": severity,
                "violation_kind": kind,
                "boundary_feedback_kind": kind,
            }
        )
        return intent

    def _settle_current_interaction_from_intent(self, user: dict[str, Any], intent: dict[str, Any]) -> None:
        """Settle the short-term expression authority from one private-chat event.

        The legacy relationship-state projection is still maintained below for
        compatibility and diagnostics, but it no longer drives expression.
        """
        emotion_enabled = bool(runtime_persona_setting(self, "enable_emotion_simulation", True))
        relation_enabled = bool(runtime_persona_setting(self, "enable_relationship_state_machine", True))
        if not (emotion_enabled or relation_enabled):
            return
        now = _now_ts()
        role_getter = getattr(self, "_private_user_role", None)
        try:
            role = role_getter(user, str(user.get("user_id") or "")) if callable(role_getter) else str(user.get("relationship_role") or "friend")
        except Exception:
            role = str(user.get("relationship_role") or "friend")
        relationship_mode = str(user.get("relationship_mode") or "normal")
        existing = current_interaction_projection(
            user.get("current_interaction"),
            relationship_role=role,
            relationship_mode=relationship_mode,
            relationship_score=user.get("relationship_score"),
            normal_interaction_band_cap=runtime_persona_setting(self, "normal_interaction_band_cap", "warm"),
            now=now,
        )
        inbound_intent = str(intent.get("intent") or "chat").strip().lower()
        intent_confidence = _safe_float(intent.get("confidence"), 0.5, 0.0)
        emotion_event = str(intent.get("emotion_event") or "neutral").strip().lower()
        emotion_confidence = _safe_float(intent.get("emotion_confidence"), intent_confidence, 0.0)
        intensity = _safe_int(intent.get("emotion_intensity"), 0, 0, 100)
        target = _single_line(intent.get("emotion_target"), 24).lower() or "none"
        pressure = _safe_int(intent.get("pressure"), 0, 0, 5)
        hurt_threshold = _safe_int(runtime_persona_setting(self, "emotional_gate_hurt_threshold", 70), 70, 10, 100)
        avoidant_threshold = _safe_int(runtime_persona_setting(self, "emotional_gate_refuse_threshold", 90), 90, 20, 100)
        if avoidant_threshold <= hurt_threshold:
            avoidant_threshold = min(100, hurt_threshold + 5)
        recovery_per_hour = _safe_int(runtime_persona_setting(self, "emotional_gate_recovery_per_hour", 24), 24, 1, 60)
        max_hurt_minutes = _safe_int(runtime_persona_setting(self, "emotional_gate_max_hurt_minutes", 90), 90, 10, 720)
        boundary_durable = bool(intent.get("boundary_durable"))
        contact = user.get("contact_preference")
        contact_state = dict(contact) if isinstance(contact, dict) else {}
        contact_active = bool(
            contact_state.get("active")
            or contact_state.get("no_contact")
            or contact_state.get("backoff")
            or str(contact or "").strip().lower() in {"no_contact", "backoff", "avoid", "stop"}
        )
        boundary_event = relation_enabled and inbound_intent == "boundary" and boundary_durable and intent_confidence >= 0.82
        explicit_recovery = (
            (relation_enabled and inbound_intent in {"intimacy", "play"} and intent_confidence >= 0.68)
            or (emotion_enabled and emotion_event in {"apology", "comfort", "praise"} and emotion_confidence >= 0.65)
        )
        manual_override_active = bool(
            existing.get("manual_override")
            and (
                not existing.get("expires_at")
                or _safe_float(existing.get("expires_at"), 0) > now
            )
        )
        if boundary_event:
            contact_active = True
            user["contact_preference"] = {
                "mode": "no_contact",
                "active": True,
                "no_contact": True,
                "source": "automatic",
                "reason_code": "explicit_user_boundary",
                "updated_at": now,
            }
        elif manual_override_active:
            if contact_active and str(existing.get("expression_band") or "relaxed") != "avoidant":
                contact_active = False
                user["contact_preference"] = {
                    "mode": "normal",
                    "active": False,
                    "no_contact": False,
                    "backoff": False,
                    "source": "manual",
                    "reason_code": "manual_interaction_override_retained",
                    "updated_at": now,
                }
            event_recorder = getattr(self, "_record_interaction_emotion_event", None)
            if callable(event_recorder):
                event_recorder(
                    user, intent,
                    band=str(existing.get("expression_band") or "relaxed"),
                    reason_code="manual_override_retained",
                    status="ignored",
                    expires_at=_safe_float(existing.get("expires_at"), 0),
                )
            user["current_interaction"] = existing
            return
        elif contact_active and explicit_recovery:
            contact_active = False
            user["contact_preference"] = {
                "mode": "normal",
                "active": False,
                "no_contact": False,
                "source": "automatic",
                "reason_code": "explicit_user_reengagement",
                "updated_at": now,
            }

        band = "relaxed"
        expires_at = 0.0
        reason_code = "interaction_neutral"
        if contact_active:
            band = "avoidant"
            reason_code = "contact_boundary_active"
        elif (
            emotion_enabled
            and emotion_event in {"hurt", "boundary_violation"}
            and target in {"bot", "ambiguous"}
            and emotion_confidence >= 0.65
            and intensity >= hurt_threshold
        ):
            violation_severity = _safe_int(intent.get("violation_severity"), 1, 1, 3)
            if emotion_event == "boundary_violation":
                intensity = max(intensity, 58 + violation_severity * 14)
            band = "avoidant" if intensity >= avoidant_threshold or violation_severity >= 3 else "hurt"
            recovery_load = recovery_per_hour + max(0, intensity - hurt_threshold)
            recovery_minutes = max(10, (recovery_load * 60 + recovery_per_hour - 1) // recovery_per_hour)
            expires_at = now + min(max_hurt_minutes, recovery_minutes) * 60
            reason_code = "boundary_violation" if emotion_event == "boundary_violation" else ("severe_hurt_event" if band == "avoidant" else "hurt_event")
        elif relation_enabled and inbound_intent == "play" and intent_confidence >= 0.68:
            band = "lively"
            expires_at = now + 6 * 3600
            reason_code = "playful_interaction"
        elif relation_enabled and inbound_intent == "intimacy" and intent_confidence >= 0.68:
            band = "close" if role == "owner" and relationship_mode == "owner_exclusive" else "warm"
            expires_at = now + 6 * 3600
            reason_code = "intimate_interaction"
        elif emotion_enabled and emotion_event in {"apology", "comfort", "praise", "comfort_need", "external_negative"} and emotion_confidence >= 0.65:
            band = "lively" if emotion_event == "praise" else "warm"
            expires_at = now + (6 * 3600 if emotion_event in {"praise", "comfort"} else 4 * 3600)
            reason_code = f"emotion_{emotion_event}"
        elif relation_enabled and pressure >= 2 and intent_confidence >= 0.65:
            band = "relaxed"
            expires_at = now + 2 * 3600
            reason_code = "interaction_pressure"
        elif (
            existing.get("source") == "automatic"
            and _safe_float(existing.get("expires_at"), 0) > now
            and str(existing.get("expression_band") or "relaxed") != "relaxed"
        ):
            event_recorder = getattr(self, "_record_interaction_emotion_event", None)
            if callable(event_recorder):
                event_recorder(
                    user, intent,
                    band=str(existing.get("expression_band") or "relaxed"),
                    reason_code="active_interaction_retained",
                    status="ignored",
                    expires_at=_safe_float(existing.get("expires_at"), 0),
                )
            user["current_interaction"] = existing
            return

        dynamics: dict[str, Any] = {}
        dynamics_kind = emotion_event if emotion_event != "neutral" else inbound_intent
        prior_expires_at = _safe_float(existing.get("expires_at"), 0)
        if not contact_active and dynamics_kind in {"hurt", "apology", "comfort", "praise", "intimacy", "play"}:
            dynamics = settle_interaction_dynamics(
                existing,
                requested_band=band,
                event_kind=dynamics_kind,
                intensity=intensity or pressure * 20,
                now=now,
            )
            if dynamics:
                band = str(dynamics.get("expression_band") or band)
                hard_expires_at = expires_at
                try:
                    negative_dynamics = float(dynamics.get("polarity") or 0) < 0
                except (TypeError, ValueError):
                    negative_dynamics = False
                if negative_dynamics and prior_expires_at > now:
                    hard_expires_at = min(hard_expires_at, prior_expires_at) if hard_expires_at > 0 else prior_expires_at
                dynamic_expires_at = _safe_float(dynamics.get("expires_at"), hard_expires_at)
                if hard_expires_at > 0:
                    dynamics["hard_expires_at"] = hard_expires_at
                    dynamics["expires_at"] = min(dynamic_expires_at, hard_expires_at)
                    expires_at = hard_expires_at
                else:
                    expires_at = dynamic_expires_at

        event_recorder = getattr(self, "_record_interaction_emotion_event", None)
        emotion_event_record = event_recorder(
            user,
            intent,
            band=band,
            reason_code=reason_code,
            status="applied",
            expires_at=expires_at,
        ) if callable(event_recorder) else None
        interaction_payload = {
            "expression_band": band,
            "source": "automatic",
            "reason": reason_code,
            "updated_at": now,
            "expires_at": expires_at,
            "manual_override": False,
            "last_event_id": (emotion_event_record or {}).get("event_id", ""),
            "trace_id": (emotion_event_record or {}).get("trace_id", ""),
        }
        if dynamics:
            interaction_payload.update(dynamics)
        user["current_interaction"] = current_interaction_projection(
            interaction_payload,
            relationship_role=role,
            relationship_mode=relationship_mode,
            relationship_score=user.get("relationship_score"),
            normal_interaction_band_cap=runtime_persona_setting(self, "normal_interaction_band_cap", "warm"),
            now=now,
        )
        logger.info(
            "互动状态已统一结算: band=%s reason=%s expires=%s",
            band,
            reason_code,
            int(expires_at) if expires_at else 0,
        )

    def _update_relationship_state_from_intent(self, user: dict[str, Any], intent: dict[str, Any]) -> None:
        if not isinstance(intent, dict):
            return
        if not bool(runtime_persona_setting(self, "enable_custom_relationship_stage_policy", True)):
            return
        # REQ-040: the seven-band interaction projection is the only durable
        # relationship-expression state.  Legacy relationship_state is not
        # produced or consumed any more.
        self._settle_current_interaction_from_intent(user, intent)
        user.pop("relationship_state", None)
        return

    def _remember_passive_reply_topic(self, user: dict[str, Any], text: str, inbound_text: str = "") -> None:
        if not runtime_persona_setting(self, "enable_passive_topic_suppression", True):
            return
        signature = self._proactive_topic_signature(text, inbound_text)
        if not signature:
            return
        recent = self._cleanup_recent_passive_topics(user)
        recent.append({"ts": _now_ts(), "signature": signature, "text": _single_line(text, 120)})
        del recent[:-18]

    @staticmethod
    def _music_album_reply_needs_disambiguation_fix(text: str) -> bool:
        compact = re.sub(r"\s+", "", str(text or ""))
        if not compact:
            return False
        return any(
            token in compact
            for token in (
                "哪个专辑",
                "哪一个专辑",
                "我不太确定你说的是哪一个",
                "你说的是哪一个",
                "发到哪里",
                "私聊里还是群里",
            )
        )

    @staticmethod
    def _music_album_reply_from_context(context: dict[str, Any], *, user_text: str = "") -> str:
        album = _single_line(context.get("album"), 60)
        artist = _single_line(context.get("artist"), 40)
        platform = _single_line(context.get("platform"), 24)
        parts: list[str] = []
        if artist and album:
            parts.append(f"看到了，这是 {artist} 的《{album}》专辑。")
        elif album:
            parts.append(f"看到了，这张是《{album}》专辑。")
        elif artist:
            parts.append(f"看到了，这是 {artist} 的专辑卡。")
        else:
            parts.append("看到了，这是一张音乐专辑卡。")
        if platform:
            parts.append(f"来源是{platform}。")
        if re.search(r"(发|列|整理|曲目|歌单|几首歌)", str(user_text or "")):
            parts.append("如果你要，我可以直接把这张专辑的曲目列出来。")
        if re.search(r"(发到哪里|私聊|群里)", str(user_text or "")):
            parts.append("你要是愿意，也可以告诉我发到私聊还是群里。")
        else:
            parts.append("你要是愿意，我也可以直接帮你把曲目列出来。")
        return "".join(parts)

    def _smart_silence_trigger_reason(self, inbound_text: str) -> str:
        cleaned = _single_line(inbound_text, 260)
        if not cleaned:
            return ""
        compact = re.sub(r"\s+", "", cleaned)
        if not compact:
            return ""
        direct_markers = (
            "别聊这个",
            "不要聊这个",
            "不聊这个",
            "别说这个",
            "不要说这个",
            "别提这个",
            "不要提这个",
            "不想聊这个",
            "不想说这个",
            "不想继续",
            "别继续",
            "不要继续",
            "别问了",
            "不要问了",
            "别追问",
            "不要追问",
            "到此为止",
            "这个话题到此为止",
            "结束这个话题",
            "结束话题",
            "换个话题",
            "跳过这个",
            "略过这个",
            "打住",
            "停一下",
            "先别说了",
            "先不说了",
            "别说了",
            "不要回复",
            "不用回复",
            "别回了",
            "不必回复",
        )
        for marker in direct_markers:
            if marker in compact:
                return marker
        topic_patterns = (
            r"(这个|这件事|这事|这话|这个话题|这话题).{0,8}(算了|别聊|别说|别提|不聊|不说|不提|跳过|略过|到此为止)",
            r"(算了|够了|停|打住).{0,8}(别聊|别说|别问|别提|不聊|不说|不问|不提)",
            r"(别|不要|不用).{0,6}(安慰|解释|分析|劝|讲道理|追问)",
        )
        for pattern in topic_patterns:
            if re.search(pattern, compact):
                return "topic_boundary"
        return ""

    def _smart_silence_contextual_trigger_reason(
        self,
        inbound_text: str,
        response_text: str = "",
        *,
        session_kind: str = "",
    ) -> str:
        boundary = self._smart_silence_trigger_reason(inbound_text)
        if boundary:
            return boundary
        mode = str(runtime_persona_setting(self, "smart_silence_judge_mode", "boundary_only") or "boundary_only").strip().lower()
        if mode != "contextual":
            return ""
        inbound = _single_line(inbound_text, 260)
        response = _single_line(response_text, 600)
        compact = re.sub(r"\s+", "", inbound)
        response_compact = re.sub(r"\s+", "", response)
        if not compact or not response_compact:
            return ""
        if len(compact) <= 16 and re.fullmatch(r"(嗯+|恩+|哦+|噢+|喔+|行|好|好吧|可以|算了|没事|不用了|随便|先这样|就这样|知道了|了解了|收到|ok|OK|嗯嗯|啊这|呃|em+|额)", compact, flags=re.I):
            if re.search(r"(吗|呢|吧|要不要|需不需要|可以.*吗|要是|如果|我可以|我帮你|继续|再|还|解释|分析|建议|聊|说)", response_compact):
                return "short_disengage"
        if re.search(r"(算了|没事|不用了|先这样|就这样|不管了|随便吧|无所谓了)", compact):
            if re.search(r"(那我|我来|我帮|可以继续|继续|再说|要不要|需不需要|解释|分析|建议|追问|为什么|怎么)", response_compact):
                return "soft_disengage"
        if re.search(r"(困了|睡了|睡觉|去睡|先睡|晚安|下了|走了|忙去了|开会|上课|工作了|不方便)", compact):
            if re.search(r"(吗|呢|要不要|继续|再聊|我陪|我等|说说|聊聊|解释|分析|建议)", response_compact):
                return "leaving_or_busy"
        if session_kind == "group" and len(compact) <= 12 and re.fullmatch(r"(哈哈+|草+|笑死|乐|绷|6+|？+|\\?+|啊？|啥|什么鬼|不是吧|好家伙)", compact):
            if len(response_compact) >= 18 and re.search(r"(我觉得|可能|其实|要不|建议|可以|因为|所以|解释|分析)", response_compact):
                return "group_reaction_not_request"
        return ""

    async def _decide_smart_silence(
        self,
        *,
        inbound_text: str,
        response_text: str,
        user: dict[str, Any] | None = None,
        session_kind: str = "",
        recent_context: list[str] | None = None,
    ) -> dict[str, Any]:
        if not bool(runtime_persona_setting(self, "enable_smart_silence", True)):
            return {"decision": "send", "reason": "disabled", "confidence": 0.0, "source": "disabled"}
        inbound = _single_line(inbound_text, 320)
        response = _single_line(response_text, 600)
        trigger = self._smart_silence_contextual_trigger_reason(
            inbound,
            response,
            session_kind=session_kind,
        )
        if not trigger:
            return {"decision": "send", "reason": "no_boundary_trigger", "confidence": 0.0, "source": "prefilter"}
        if not response:
            return {"decision": "send", "reason": "empty_response", "confidence": 0.0, "source": "prefilter"}
        cache_key = hashlib.sha1(
            f"{session_kind}\n{inbound}\n{response[:240]}".encode("utf-8", errors="ignore")
        ).hexdigest()
        cache = getattr(self, "_smart_silence_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            setattr(self, "_smart_silence_cache", cache)
        now = _now_ts()
        cached = cache.get(cache_key)
        if isinstance(cached, dict) and now - _safe_float(cached.get("ts"), 0) <= 120:
            result = dict(cached.get("result") or {})
            result["source"] = "cache"
            return result
        if len(cache) > 256:
            for key, item in list(cache.items())[:64]:
                if not isinstance(item, dict) or now - _safe_float(item.get("ts"), 0) > 120:
                    cache.pop(key, None)

        provider_id = self._task_provider(
            runtime_persona_setting(self, "smart_silence_provider_id", ""),
            runtime_persona_setting(self, "response_review_provider_id", ""),
            runtime_persona_setting(self, "smart_message_debounce_provider_id", ""),
            runtime_persona_setting(self, "mai_style_provider_id", ""),
            runtime_persona_setting(self, "llm_provider_id", ""),
        )
        if not provider_id:
            return {"decision": "send", "reason": "no_provider", "confidence": 0.0, "source": "prefilter"}

        last_companion = _single_line((user or {}).get("last_companion_message"), 260) if isinstance(user, dict) else ""
        recent_lines = []
        for item in (recent_context or [])[-6:]:
            line = _single_line(item, 120)
            if line:
                recent_lines.append(f"- {line}")
        recent_block = _render_user_memory_labeled_section(
            prompt_section(
                key="background.memory.smart_silence.recent_context",
                title="最近上下文",
                source="user_memory",
                content="\n".join(recent_lines) or "（无）",
            )
        )
        last_bot_block = _render_user_memory_labeled_section(
            prompt_section(
                key="background.memory.smart_silence.last_bot_message",
                title="Bot 上次发出的话",
                source="user_memory",
                content=last_companion or "（无）",
            )
        )
        inbound_block = _render_user_memory_labeled_section(
            prompt_section(
                key="background.memory.smart_silence.inbound",
                title="用户刚才说",
                source="user_memory",
                content=inbound,
            )
        )
        response_block = _render_user_memory_labeled_section(
            prompt_section(
                key="background.memory.smart_silence.response",
                title="待发送回复",
                source="user_memory",
                content=response,
            )
        )
        prompt = prompt_section(
            key="background.memory.smart_silence",
            title="智能沉默判定",
            source="user_memory",
            content=f"""
你是聊天回复发送前的智能沉默判定器。判断用户是否在表达“不要继续这个话题/不要再追问/先别回复/换掉当前话题”，或上下文已经明显适合安静收住，从而应该直接不发这条待发送回复。

只输出 JSON：{{"decision":"send|silent","confidence":0-1,"reason":"不超过20字"}}

判定原则：
- 用户明确说别聊、别问、别继续、到此为止、算了别说了、换个话题，且待发送回复仍在确认、安慰、解释、追问或继续这个话题，decision=silent。
- 当触发词是 short_disengage、soft_disengage、leaving_or_busy 或 group_reaction_not_request 时，要结合上下文判断：用户只是短促收尾、要离开、忙了、敷衍回应，且待发送回复还在追问、解释、建议、延长话题，才 silent。
- 如果用户同一句已经开启了新请求或新问题，例如“算了，帮我看这个”“换个话题，今天吃什么”，且待发送回复是在处理新请求，decision=send。
- 如果待发送回复只是“好，那不聊这个了”“嗯我闭嘴了”这类对边界的重复确认，通常 silent；真实聊天里安静退开更自然。
- 如果待发送回复是必要的信息回答、用户明确提问的答案、工具结果、约定确认或安全提醒，decision=send。
- 不要因为用户说“算了”两个字就一定沉默，要看它是不是结束当前话题，而不是普通口头禅。
- 不确定时 send。

会话类型：{_single_line(session_kind, 40) or "未知"}
触发词：{trigger}

{recent_block}

{last_bot_block}

{inbound_block}

{response_block}
""".strip(),
        )
        timeout_seconds = max(
            0.2,
            min(
                5.0,
                _safe_float(
                    runtime_persona_setting(self, "smart_silence_model_timeout_seconds", 1.2),
                    1.2,
                    0.2,
                ),
            ),
        )
        started = time.perf_counter()
        raw = ""
        timeout_getter = getattr(self, "_model_timeout_seconds_for_call", None)
        timeout_override = (
            timeout_getter(
                task="smart_silence",
                provider_id=provider_id,
                timeout_key="SMART_SILENCE_PROVIDER_ID",
            )
            if callable(timeout_getter)
            else None
        )
        if timeout_override is not None:
            timeout_seconds = float(timeout_override)
        try:
            raw = await asyncio.wait_for(
                self._llm_call(
                    _render_user_memory_background_prompt(prompt),
                    max_tokens=100,
                    provider_id=provider_id,
                    task="smart_silence",
                ),
                timeout=timeout_seconds,
            ) or ""
        except asyncio.TimeoutError:
            result = {"decision": "send", "reason": f"timeout>{timeout_seconds:.1f}s", "confidence": 0.0, "source": "timeout"}
            cache[cache_key] = {"ts": now, "result": result}
            logger.warning(
                "智能沉默判定超时,默认放行: trigger=%s timeout=%.1fs text=%s",
                trigger,
                timeout_seconds,
                _single_line(inbound, 100),
            )
            return result
        except Exception as exc:
            result = {"decision": "send", "reason": _single_line(exc, 80), "confidence": 0.0, "source": "error"}
            cache[cache_key] = {"ts": now, "result": result}
            logger.warning("智能沉默判定失败,默认放行: %s", _single_line(exc, 120))
            return result

        payload = self._extract_json_payload(raw or "")
        if not isinstance(payload, dict):
            result = {"decision": "send", "reason": "invalid_json", "confidence": 0.0, "source": "model"}
            cache[cache_key] = {"ts": now, "result": result}
            return result
        decision = str(payload.get("decision") or "").strip().lower()
        if decision not in {"send", "silent"}:
            decision = "send"
        confidence = max(0.0, min(1.0, _safe_float(payload.get("confidence"), 0.0, 0.0)))
        reason = _single_line(payload.get("reason"), 80) or "模型判定"
        threshold = max(
            0.0,
            min(
                1.0,
                _safe_float(runtime_persona_setting(self, "smart_silence_min_confidence", 0.66), 0.66, 0.0),
            ),
        )
        if decision == "silent" and confidence < threshold:
            decision = "send"
            reason = f"低置信度:{reason}"
        result = {
            "decision": decision,
            "reason": reason,
            "confidence": confidence,
            "source": "model",
            "trigger": trigger,
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
        }
        cache[cache_key] = {"ts": now, "result": result}
        logger.info(
            "智能沉默判定: decision=%s confidence=%.2f trigger=%s elapsed=%dms reason=%s user=%s reply=%s",
            decision,
            confidence,
            trigger,
            result["elapsed_ms"],
            reason,
            _single_line(inbound, 120),
            _single_line(response, 140),
        )
        return result

