# -*- coding: utf-8 -*-
"""meal 域。

由 tools/split_mixin_domain.py 从 daily_state.py 机械抽取（33 个方法 + 0 个模块级名字 + 0 个类级赋值 / 1135 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 DailyStateMixin）。
"""
from __future__ import annotations

import hashlib
import random
import re
import uuid
from .conversation_prompt_section import PromptSection, prompt_section
from .helpers import _now_ts, _safe_float, _safe_int, _single_line, _today_key
from .persona_config import runtime_persona_setting
from datetime import datetime
from typing import Any

from .logging_util import get_module_logger

logger = get_module_logger(__name__)



class DailyStateMealMixin:
    """meal 域（从 DailyStateMixin 拆出）。"""


    def _meal_log_date_key(self, ts: float | None = None) -> str:
        try:
            return self._environment_fromtimestamp(ts or _now_ts()).strftime("%Y-%m-%d")
        except Exception:
            return _today_key()

    def _meal_log_iso_time(self, ts: float | None = None) -> str:
        try:
            return self._environment_fromtimestamp(ts or _now_ts()).isoformat(timespec="seconds")
        except Exception:
            return datetime.fromtimestamp(ts or _now_ts()).isoformat(timespec="seconds")

    def _extract_self_meal_events_from_text(
        self,
        text: Any,
        *,
        default_meal: str = "",
        source: str = "",
    ) -> list[dict[str, Any]]:
        raw = _single_line(text, 260)
        if not raw:
            return []
        if not any(token in raw for token in ("吃", "喝", "点了", "煮了", "做了", "买了", "饭", "餐", "夜宵", "便当", "外卖")):
            return []
        if re.search(r"(想吃|想喝|要不要|吃什么|吃啥|没吃|还没吃|准备吃|等会吃|待会吃|可能吃|可以吃|推荐|建议)", raw):
            return []
        action_match = re.search(
            r"(?:我|她|星缘)?(?:刚刚|刚|已经|中午|晚上|早上|午后|夜里|下午|早餐|午餐|晚餐|夜宵|这顿)?"
            r"(?:吃了|吃过|吃完|喝了|点了|煮了|做了|买了|啃了|咬了|尝了|解决了)"
            r"([^，。；、\n]{1,36})",
            raw,
        )
        meal_match = re.search(r"(早餐|早饭|午餐|午饭|晚餐|晚饭|夜宵|加餐|下午茶)", raw)
        meal = _single_line((meal_match.group(1) if meal_match else "") or default_meal, 20)
        food = ""
        if action_match:
            food = _single_line(action_match.group(1), 40)
            food = re.sub(r"^(点|些|个|一点|一点儿|一份|一碗|一杯|一口|点儿)", "", food).strip()
            food = re.sub(r"(之后|以后|然后|顺手|才发现|的时候).*$", "", food).strip()
        if not food:
            simple = re.search(r"(?:早餐|早饭|午餐|午饭|晚餐|晚饭|夜宵|下午茶)[^，。；、\n]{0,8}(?:是|吃|喝|点)([^，。；、\n]{1,32})", raw)
            if simple:
                food = _single_line(simple.group(1), 40)
        if not food or food in {"饭", "东西", "一点", "点东西"}:
            return []
        return [
            {
                "meal": meal or "加餐",
                "food": food,
                "source": _single_line(source, 40),
                "evidence": raw,
            }
        ]

    def _collect_self_meal_events_from_detail(
        self,
        *,
        segment: dict[str, Any],
        plan: dict[str, Any],
        detail: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if not isinstance(detail, dict):
            return []
        default_meal = ""
        item = segment.get("item") if isinstance(segment.get("item"), dict) else {}
        schedule_text = " ".join(
            _single_line(part, 120)
            for part in (
                item.get("time") if isinstance(item, dict) else "",
                item.get("activity") if isinstance(item, dict) else "",
                detail.get("summary"),
            )
            if _single_line(part, 120)
        )
        if any(token in schedule_text for token in ("早餐", "早饭")):
            default_meal = "早餐"
        elif any(token in schedule_text for token in ("午餐", "午饭", "中午")):
            default_meal = "午餐"
        elif any(token in schedule_text for token in ("晚餐", "晚饭", "晚上")):
            default_meal = "晚餐"
        elif "夜宵" in schedule_text:
            default_meal = "夜宵"
        rows: list[dict[str, Any]] = []
        rows.extend(self._extract_self_meal_events_from_text(detail.get("summary"), default_meal=default_meal, source="detail.summary"))
        for list_key in ("today_events", "state_variables"):
            raw_items = detail.get(list_key)
            if not isinstance(raw_items, list):
                continue
            for raw in raw_items[:12]:
                if isinstance(raw, dict):
                    text = raw.get("event") or raw.get("text") or raw.get("name") or raw.get("value") or raw.get("note")
                else:
                    text = raw
                rows.extend(self._extract_self_meal_events_from_text(text, default_meal=default_meal, source=f"detail.{list_key}"))
        deduped: list[dict[str, Any]] = []
        seen: set[str] = set()
        for meal_event in rows:
            key = f"{meal_event.get('meal')}:{meal_event.get('food')}"
            if key in seen:
                continue
            seen.add(key)
            deduped.append(meal_event)
        return deduped[:4]

    def _append_self_meal_log(
        self,
        meal_events: list[dict[str, Any]],
        *,
        segment: dict[str, Any] | None = None,
        plan: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if not meal_events:
            return []
        now_ts = _now_ts()
        date_text = _single_line((plan or {}).get("date"), 16) or self._meal_log_date_key(now_ts)
        time_text = ""
        if isinstance(segment, dict):
            item = segment.get("item") if isinstance(segment.get("item"), dict) else {}
            time_text = _single_line(item.get("time") if isinstance(item, dict) else "", 20)
        log = self.data.setdefault("self_meal_log", [])
        if not isinstance(log, list):
            log = []
            self.data["self_meal_log"] = log
        existing_ids = {str(item.get("id") or "") for item in log if isinstance(item, dict)}
        added: list[dict[str, Any]] = []
        for meal_event in meal_events:
            meal = _single_line(meal_event.get("meal"), 20) or "加餐"
            food = _single_line(meal_event.get("food"), 60)
            if not food:
                continue
            base_id = hashlib.sha1(f"{date_text}|{time_text}|{meal}|{food}".encode("utf-8", errors="ignore")).hexdigest()[:16]
            meal_id = f"meal-{base_id}"
            if meal_id in existing_ids:
                continue
            entry = {
                "id": meal_id,
                "date": date_text,
                "time": time_text,
                "ts": now_ts,
                "occurred_at": self._meal_log_iso_time(now_ts),
                "meal": meal,
                "food": food,
                "source": _single_line(meal_event.get("source"), 40),
                "evidence": _single_line(meal_event.get("evidence"), 180),
                "memory_recorded": False,
                "memory_id": "",
            }
            log.append(entry)
            existing_ids.add(meal_id)
            added.append(entry)
        if len(log) > 160:
            del log[:-160]
        return added

    async def _memory_companion_record_self_meal(self, entry: dict[str, Any]) -> None:
        if not isinstance(entry, dict) or entry.get("memory_recorded"):
            return
        bridge = self._memory_companion_bridge()
        recorder = getattr(bridge, "record_persona_life", None) if bridge is not None else None
        if not callable(recorder):
            return
        date_text = _single_line(entry.get("date"), 16)
        time_text = _single_line(entry.get("time"), 20)
        meal = _single_line(entry.get("meal"), 20) or "加餐"
        food = _single_line(entry.get("food"), 60)
        if not food:
            return
        when = " ".join(part for part in (date_text, time_text) if part)
        content = f"Bot 在{when or date_text or '今天'}的{meal}吃了{food}。"
        try:
            memory_id = await recorder(
                content=content,
                scope="unknown",
                session_id="private_companion:self_meal",
                message_id=_single_line(entry.get("id"), 120),
                memory_id=f"private_companion_{_single_line(entry.get('id'), 80)}",
                metadata={
                    "date": date_text,
                    "time": time_text,
                    "event_type": "self_meal",
                    "action_label": "进食记录",
                    "meal": meal,
                    "food": food,
                    "evidence": _single_line(entry.get("evidence"), 180),
                    "source": _single_line(entry.get("source"), 40),
                    "query_anchors": ["self_meal", "吃了什么", "刚才吃了什么", "午餐", "晚餐", "早餐", "夜宵", food],
                },
                source_plugin="private_companion",
                confidence=0.78,
                importance=0.5,
                tags=["self_meal", "persona_life", "food", meal],
                occurred_at=_single_line(entry.get("occurred_at"), 80),
            )
        except Exception as exc:
            logger.debug("MemoryCompanion 进食记忆写入失败: %s", _single_line(exc, 120))
            return
        entry["memory_recorded"] = True
        entry["memory_id"] = _single_line(memory_id, 120)

    def _detect_care_feedback(self, text: str) -> dict[str, Any]:
        normalized = str(text or "").strip()
        if not normalized:
            return {"is_care": False, "tags": []}
        care_actions = r"吃药|喝药|去拿药|按时吃药|喝水|多喝热水|热水|温水|休息|早点睡|快睡|去睡|别熬夜|多睡会|睡一觉|保暖|别着凉|穿厚|加衣服|盖好|难受|还好吗|没事吧|注意身体|照顾好自己|心疼"
        self_report = bool(
            re.search(
                rf"(?:我|俺|本人|这边|我们|咱们|咱).{{0,12}}(?:{care_actions})",
                normalized,
            )
        )
        if self_report:
            return {"is_care": False, "tags": []}
        tags: list[str] = []
        if re.search(r"吃药|喝药|去拿药|按时吃药", normalized):
            tags.append("medicine")
        if re.search(r"喝水|多喝热水|热水|温水", normalized):
            tags.append("water")
        if re.search(r"休息|早点睡|快睡|去睡|别熬夜|多睡会|睡一觉", normalized):
            tags.append("rest")
        if re.search(r"保暖|别着凉|穿厚|加衣服|盖好", normalized):
            tags.append("warm")
        if re.search(r"难受|还好吗|没事吧|注意身体|照顾好自己|心疼", normalized):
            tags.append("concern")
        tags = list(dict.fromkeys(tags))
        return {"is_care": bool(tags), "tags": tags}

    def _apply_care_feedback_to_state(self, text: str) -> bool:
        feedback = self._detect_care_feedback(text)
        if not feedback.get("is_care"):
            return False
        tags = feedback.get("tags", [])
        changed = False
        now = _now_ts()
        conditions = self.data.setdefault("state_conditions", [])
        if not isinstance(conditions, list):
            self.data["state_conditions"] = []
            conditions = self.data["state_conditions"]
        for cond in conditions:
            if not isinstance(cond, dict):
                continue
            if str(cond.get("kind") or "") != "health":
                continue
            if _safe_float(cond.get("end_ts"), 0) <= now:
                continue
            remaining = max(0, _safe_float(cond.get("end_ts"), now) - now)
            shorten_ratio = 0.0
            if "medicine" in tags:
                shorten_ratio += 0.35
            if "rest" in tags:
                shorten_ratio += 0.2
            if "water" in tags:
                shorten_ratio += 0.12
            if "warm" in tags:
                shorten_ratio += 0.12
            if shorten_ratio > 0:
                cond["end_ts"] = now + remaining * max(0.35, 1 - min(shorten_ratio, 0.55))
                cond["duration_hours"] = max(
                    1,
                    int((cond["end_ts"] - _safe_float(cond.get("start_ts"), now)) / 3600),
                )
                cond["energy_delta"] = min(-2, int(_safe_int(cond.get("energy_delta"), -8) * 0.75))
                cond["label"] = "收到照顾提醒后,不适强度略有下降"
                changed = True
            notes = cond.setdefault("care_notes", [])
            if isinstance(notes, list):
                care_note = "用户提供了关心反馈"
                if "medicine" in tags:
                    care_note = "用户提醒用药"
                elif "rest" in tags:
                    care_note = "用户提醒休息"
                elif "water" in tags:
                    care_note = "用户提醒补水"
                if care_note not in notes:
                    notes.append(care_note)
            cond["cause"] = _single_line(
                f"{_single_line(cond.get('cause'), 80)}；用户提供了照顾提醒".strip("；"),
                120,
            )
        if changed and random.random() < 0.72:
            conditions.append(
                self._make_condition(
                    kind="care_warmth",
                    title="被关心后的回暖",
                    label="收到用户关心后的轻度回暖",
                    mood="柔和",
                    energy_delta=6,
                    duration_hours=6,
                    intensity=72,
                    cause="用户提供了关心反馈",
                    phase="care_feedback",
                    transition_options=self._build_transition_options(
                        kind="care_warmth",
                        energy_delta=6,
                        cause="用户提供了关心反馈",
                        on_end_transition="",
                    ),
                )
            )
        return changed

    def _detect_food_feedback(self, text: str) -> dict[str, Any]:
        normalized = _single_line(text, 220)
        if not normalized:
            return {"is_food": False}
        food_markers = (
            "吃饭", "吃点", "吃些", "吃个", "吃什么", "吃啥", "晚饭", "晚餐", "午饭", "午餐",
            "早饭", "早餐", "夜宵", "外卖", "点餐", "做饭", "煮", "炒", "饭", "面", "粥",
            "汤", "菜", "肉", "蛋", "奶茶", "甜品", "水果", "火锅", "烧烤", "便当", "饺子",
            "馄饨", "米粉", "汉堡", "披萨", "三明治", "咖啡", "零食", "吃了", "吃过",
            "吃完", "吃饱", "饱了", "没吃", "还没吃", "饿", "嘴馋", "投喂", "喂你", "喂给你", "请你吃"
        )
        if not any(marker in normalized for marker in food_markers):
            return {"is_food": False}
        already_ate = bool(
            re.search(r"(我|俺|本人|这边|我们|咱们|咱).{0,10}(吃了|吃过|吃完|吃饱|饱了|喝了|喝过|喝完)", normalized)
            or re.search(r"^(吃了|吃过了|吃完了|吃饱了|饱了|喝完了)$", normalized)
        )
        food_nouns = r"(饭|菜|粥|汤|面|粉|饺子|馄饨|便当|外卖|夜宵|早餐|早饭|午餐|午饭|晚餐|晚饭|奶茶|咖啡|水果|零食|甜品|汉堡|披萨|三明治|火锅|烧烤|蛋|肉|吃的|喝的)"
        bot_subject = r"(你|bot|机器人|助手|ai|AI|宝宝|宝贝)"
        feeding = bool(
            re.search(r"(投喂|喂你|喂给你|给你投喂)", normalized, re.IGNORECASE)
            or re.search(fr"(给你|送你|递你|分你|留给你|请你|带你|陪你).{{0,12}}(吃|喝|点|买|做|煮|留|带|拿|叫|尝|来).{{0,12}}{food_nouns}?", normalized, re.IGNORECASE)
            or re.search(fr"(这个|这份|这杯|这碗|这口|这些).{{0,8}}(给你|分你|留给你).{{0,8}}(吃|喝|尝)?", normalized, re.IGNORECASE)
        )
        bot_food_question = bool(
            re.search(fr"{bot_subject}.{{0,10}}(想|要|打算|准备|喜欢|爱不爱|能不能|可以不可以)?.{{0,8}}(吃|喝|点).{{0,8}}(什么|啥|吗|嘛|么|哪[个家种些]?)", normalized, re.IGNORECASE)
            or re.search(fr"{bot_subject}.{{0,8}}(饿了吗|饿不饿|吃饭了吗|吃了没|吃没吃|吃过了吗|想吃吗|要吃吗|喝吗)", normalized, re.IGNORECASE)
            or re.search(fr"{bot_subject}.{{0,10}}(要不要|想不想|吃不吃|喝不喝|点不点|饿不饿).{{0,10}}(吃|喝|点|饭|外卖|夜宵|奶茶|咖啡)?", normalized, re.IGNORECASE)
        )
        bot_directed = (not bot_food_question) and bool(
            re.search(fr"{bot_subject}.{{0,12}}(先|去|也|就|可以|要不|不如|还是|记得|别忘了|快|赶紧)?.{{0,12}}(吃|喝|点|煮|买|做|叫|尝)", normalized, re.IGNORECASE)
            or re.search(fr"(推荐|建议).{{0,8}}{bot_subject}.{{0,12}}(吃|喝|点|煮|买|做|叫|尝)", normalized, re.IGNORECASE)
            or re.search(fr"(吃|喝|点|煮|买|做|叫|尝).{{0,10}}(给|给点|给买|给做).{{0,4}}{bot_subject}", normalized, re.IGNORECASE)
        )
        user_self_intent = bool(
            re.search(r"(我|俺|本人|这边|我们|咱们|咱).{0,14}(去|先|准备|要|想|打算|正在|刚|已经)?.{0,14}(吃|喝|点|买|做|煮|叫)", normalized)
            or re.search(r"(给我|帮我|我该|我要|我想|我能|我可以).{0,12}(吃|喝|点|买|做|煮|叫|推荐)", normalized)
        )
        user_menu_query = bool(
            re.search(r"(吃什么|吃啥|点什么|点啥|推荐).{0,10}(我|给我|一下)?", normalized)
            and re.search(r"(我|给我|帮我|吃什么|吃啥|点什么|点啥)", normalized)
        )
        implicit_bot_suggestion = bool(
            not already_ate
            and not bot_food_question
            and not user_self_intent
            and not user_menu_query
            and (
                re.search(r"(先|去|快|赶紧|记得|别忘了).{0,10}(吃|喝|点|买|做|煮|叫)", normalized)
                or re.search(r"(吃点|吃些|喝点|喝些).{0,8}(吧|呀|哦|噢)?$", normalized)
                or re.search(fr"(要不|不如|可以|试试).{{0,12}}(吃|喝|点|买|做|煮|叫).{{0,12}}{food_nouns}?", normalized)
            )
        )
        suggestion = bool(feeding or bot_directed or implicit_bot_suggestion)
        meal = ""
        for token, label in (("早餐", "早餐"), ("早饭", "早餐"), ("午餐", "午餐"), ("午饭", "午餐"), ("晚餐", "晚餐"), ("晚饭", "晚餐"), ("夜宵", "夜宵")):
            if token in normalized:
                meal = label
                break
        if not meal:
            hour = self._environment_now().hour
            if 10 <= hour < 15:
                meal = "午餐"
            elif 15 <= hour < 21:
                meal = "晚餐"
            elif hour >= 21 or hour < 3:
                meal = "夜宵"
            else:
                meal = "加餐"
        return {
            "is_food": True,
            "suggestion": suggestion,
            "actionable": suggestion,
            "already_ate": already_ate,
            "user_ate": already_ate,
            "feeding": feeding,
            "bot_directed": bot_directed,
            "bot_food_question": bot_food_question,
            "implicit_bot_suggestion": implicit_bot_suggestion,
            "meal": meal,
            "food_hint": _single_line(normalized, 80),
        }

    def _apply_food_feedback_to_state(self, text: str) -> bool:
        feedback = self._detect_food_feedback(text)
        if not feedback.get("is_food") or not feedback.get("actionable"):
            return False
        now = _now_ts()
        self.data["last_food_state_feedback_at"] = now
        self.data["last_food_state_feedback_text"] = _single_line(feedback.get("food_hint"), 120)
        changed = False
        conditions = self.data.setdefault("state_conditions", [])
        if not isinstance(conditions, list):
            self.data["state_conditions"] = []
            conditions = self.data["state_conditions"]
        for cond in conditions:
            if not isinstance(cond, dict) or str(cond.get("kind") or "") != "hunger":
                continue
            if _safe_float(cond.get("end_ts"), 0) <= now:
                continue
            remaining = max(0.0, _safe_float(cond.get("end_ts"), now) - now)
            if feedback.get("feeding"):
                target_remaining = max(5 * 60, min(remaining * 0.15, 12 * 60))
                label = "收到用户投喂后,饥饿感很快回落"
                cause = "用户投喂或分享吃的"
                energy_ratio = 0.25
            elif feedback.get("bot_directed"):
                target_remaining = max(8 * 60, min(remaining * 0.2, 20 * 60))
                label = "被提醒先吃点东西后,饥饿感开始回落"
                cause = "用户提醒去吃东西"
                energy_ratio = 0.35
            else:
                target_remaining = max(12 * 60, min(remaining * 0.3, 35 * 60))
                label = "有了吃什么的方向,饥饿感开始回落"
                cause = "用户给了饮食建议"
                energy_ratio = 0.45
            cond["end_ts"] = now + min(remaining, target_remaining)
            cond["duration_hours"] = max(1, int((cond["end_ts"] - _safe_float(cond.get("start_ts"), now)) / 3600))
            cond["mood"] = "回稳"
            cond["label"] = _single_line(label, 80)
            cond["cause"] = cause
            cond["phase"] = "food_feedback_resolving"
            current_delta = _safe_int(cond.get("energy_delta"), 0, -100, 100)
            if current_delta < 0:
                cond["energy_delta"] = min(0, int(current_delta * energy_ratio))
            changed = True
        if changed:
            conditions.append(
                self._make_condition(
                    kind="care_warmth",
                    title="饮食照顾回暖",
                    label="收到用户的投喂或吃饭提醒后,状态轻轻回稳",
                    mood="柔和",
                    energy_delta=4 if feedback.get("feeding") else 3,
                    duration_hours=2,
                    intensity=55,
                    cause=_single_line(feedback.get("food_hint"), 80),
                    phase="food_feedback",
                    transition_options=self._build_transition_options(
                        kind="care_warmth",
                        energy_delta=4 if feedback.get("feeding") else 3,
                        cause=_single_line(feedback.get("food_hint"), 80),
                        on_end_transition="",
                    ),
                )
            )
            self.data["daily_state"] = self._compose_state_from_conditions(self.data.get("daily_weather", {}))
        return changed

    def _meal_care_active_context(self, user: dict[str, Any], *, now: float | None = None) -> dict[str, Any]:
        if not isinstance(user, dict):
            return {}
        context = user.get("meal_check_context")
        if not isinstance(context, dict) or not context.get("active"):
            return {}
        check_now = _now_ts() if now is None else now
        if str(context.get("date") or "") != _today_key() or (
            _safe_float(context.get("expires_at"), 0) > 0
            and check_now > _safe_float(context.get("expires_at"), 0)
        ):
            user["meal_check_context"] = {}
            return {}
        return context

    @staticmethod
    def _meal_reply_is_not_eaten(text: str) -> bool:
        compact = re.sub(r"\s+", "", _single_line(text, 160))
        return bool(
            re.search(r"(?:还|一直|今天|刚刚|我)?没(?:有)?(?:吃|吃饭|吃上|来得及吃)|没呢|还没呢|没来得及|不准备吃|不想吃", compact)
        )

    @staticmethod
    def _meal_reply_is_non_food_consumption(text: str) -> bool:
        compact = re.sub(r"\s+", "", _single_line(text, 160))
        if not compact:
            return False
        prefix = r"(?:我|俺|咱|本人|今天|刚刚|刚才|已经|又|还|这次|这回)*"
        suffix = r"(?:了|啦|呢|呀|啊|哦|噢|哈)?"
        expression = (
            r"(?:吃(?:了|过|到)?(?:个|一(?:个|点|些))?"
            r"(?:亏|大亏|哑巴亏|苦头|闭门羹|官司|教训|排头|败仗|处分|罚单|巴掌|拳头|耳光|一惊|一吓|瘪|土|枪药))"
            r"|(?:(?:吃|服|喝)(?:了|过|完)?(?:点|些|一(?:片|粒|颗|包|支|瓶))?"
            r"(?:感冒药|退烧药|止痛药|消炎药|安眠药|胃药|中药|西药|处方药|降压药|抗生素|药片|药|胶囊|维生素|保健品|补剂))"
        )
        return bool(re.fullmatch(prefix + r"(?:才|就|可算)?" + expression + suffix, compact))

    @staticmethod
    def _meal_reply_confirms_eaten(text: str) -> bool:
        compact = re.sub(r"\s+", "", _single_line(text, 160))
        if (
            not compact
            or DailyStateMealMixin._meal_reply_is_not_eaten(compact)
            or DailyStateMealMixin._meal_reply_is_non_food_consumption(compact)
        ):
            return False
        return bool(
            re.search(r"(?:我|俺|咱|已经|刚刚|刚|早就|这边)?(?:吃了|吃过|吃完|吃饱|吃上了|用过餐|喝了|喝过)", compact)
            or re.search(r"(?:我|俺|咱)?(?:正在吃|在吃|开吃了)", compact)
            or compact in {"吃了", "吃过了", "吃完了", "吃饱了", "饱了", "刚吃", "刚吃完"}
        )

    def _meal_reply_food_items(self, text: str, *, active_context: bool = False) -> list[str]:
        normalized = _single_line(text, 220)
        if (
            not normalized
            or self._meal_reply_is_not_eaten(normalized)
            or self._meal_reply_is_non_food_consumption(normalized)
        ):
            return []
        explicit_self = bool(
            re.search(r"(?:我|俺|咱|本人|今天|刚刚|刚才|早上|中午|晚上|早餐|早饭|午饭|午餐|晚饭|晚餐).{0,12}(?:吃了|吃的是|吃的|吃过|正在吃|在吃|点了|做了|喝了)", normalized)
            or re.search(r"^(?:吃了|吃的是|吃的|正在吃|在吃|点了|喝了)", normalized)
        )
        if not active_context and not explicit_self:
            return []
        existing_hits: list[str] = []
        for item in self._food_menu_items():
            terms = [item.get("name"), *item.get("aliases", [])]
            if any(term and str(term) in normalized for term in terms):
                name = _single_line(item.get("name"), 40)
                if name and name not in existing_hits:
                    existing_hits.append(name)
        capture_patterns = (
            r"(?:早餐|早饭|午饭|午餐|晚饭|晚餐|夜宵)?(?:我|俺|咱|本人)?(?:刚刚|刚才|已经|就)?(?:吃了|吃的是|吃的|吃过|正在吃|在吃|点了|做了|喝了)\s*([^。！？!?]+)",
            r"(?:早餐|早饭|午饭|午餐|晚饭|晚餐|夜宵)\s*(?:是|有|吃)?\s*([^。！？!?]+)",
        )
        raw_candidate = ""
        for pattern in capture_patterns:
            match = re.search(pattern, normalized)
            if match:
                raw_candidate = _single_line(match.group(1), 80)
                break
        bare_context_reply = False
        if not raw_candidate and active_context and len(normalized) <= 28:
            raw_candidate = normalized
            bare_context_reply = True
        raw_candidate = re.split(r"[。！？!?；;]|(?:，|,)(?:不过|但是|然后|感觉|味道|还行|挺|有点)", raw_candidate, maxsplit=1)[0]
        raw_candidate = re.sub(r"^(?:我|俺|咱|今天|刚刚|刚才|已经|就是|吃了|吃的是|吃的|正在吃|在吃|点了|喝了)+", "", raw_candidate).strip()
        raw_candidate = re.sub(r"(?:了|啦|呢|呀|啊|哦|噢|哈|来着)$", "", raw_candidate).strip(" ，,、")
        generic = {
            "", "饭", "东西", "吃的", "喝的", "一点", "一些", "一口", "随便", "不知道", "忘了",
            "还行", "挺好", "吃完", "吃饱", "饱", "完", "过", "是", "有", "没", "没有",
        }
        if bare_context_reply and not explicit_self and not existing_hits:
            food_like_markers = (
                "饭", "面", "粉", "粥", "汤", "饺", "馄饨", "包", "馒头", "饼", "肉", "鸡", "鸭", "鱼", "虾", "蟹",
                "蛋", "菜", "瓜", "豆", "笋", "菇", "火锅", "烧烤", "麻辣烫", "冒菜", "砂锅", "便当", "外卖", "汉堡",
                "披萨", "三明治", "牛排", "食堂", "餐厅", "店", "馆", "奶", "茶", "咖啡", "果", "甜品", "蛋糕", "酸奶",
                "血旺", "螺蛳", "米线", "盖浇", "煲仔", "咖喱", "炸", "烤", "炒", "蒸", "煮",
            )
            if not any(marker in raw_candidate for marker in food_like_markers):
                raw_candidate = ""
        learned: list[str] = list(existing_hits)
        for part in re.split(r"[、，,/+]|还有|以及|配了|配着", raw_candidate):
            item = _single_line(part, 30).strip(" 的")
            if (
                item in generic
                or len(item) < 2
                or len(item) > 24
                or re.search(r"(?:什么|啥|吗|嘛|怎么|为啥|你呢|你吃|不告诉|不记得)", item)
                or re.fullmatch(
                    r"(?:个|一|一点|一些|不少)?(?:大|小|哑巴|闷)?"
                    r"(?:亏|苦头|官司|闭门羹|败仗|教训|处分|罚单|巴掌|拳头|耳光|一惊)",
                    item,
                )
                or re.fullmatch(
                    r"(?:感冒药|退烧药|止痛药|消炎药|安眠药|胃药|中药|西药|处方药|降压药|抗生素|药片|药|胶囊|维生素|保健品|补剂)",
                    item,
                )
            ):
                continue
            if item not in learned:
                learned.append(item)
        return learned[:5]

    @staticmethod
    def _meal_food_inferred_fields(name: str) -> dict[str, Any]:
        text = _single_line(name, 40)
        item_type = "drink_snack" if any(token in text for token in ("奶茶", "咖啡", "甜品", "蛋糕", "水果", "零食", "饮料", "酸奶")) else "dish"
        category_rules = (
            ("面食", ("面", "粉", "馄饨", "饺子", "抄手", "米线")),
            ("米饭", ("饭", "便当", "煲仔", "咖喱", "盖浇")),
            ("快餐", ("汉堡", "炸鸡", "披萨", "麦当劳", "肯德基")),
            ("甜口", ("奶茶", "甜品", "蛋糕", "水果", "酸奶")),
            ("热锅", ("火锅", "麻辣烫", "冒菜", "砂锅", "关东煮")),
        )
        category = next((label for label, tokens in category_rules if any(token in text for token in tokens)), "")
        tags: list[str] = []
        for tag, tokens in (
            ("热乎", ("面", "粉", "粥", "汤", "火锅", "砂锅")),
            ("快", ("便当", "汉堡", "炸鸡", "外卖")),
            ("清淡", ("粥", "汤", "沙拉", "蒸")),
            ("辣", ("辣", "火锅", "冒菜", "麻辣烫")),
            ("甜", ("奶茶", "甜品", "蛋糕", "水果", "酸奶")),
            ("顶饱", ("饭", "面", "粉", "汉堡", "便当")),
        ):
            if any(token in text for token in tokens):
                tags.append(tag)
        return {"type": item_type, "category": category, "tags": tags}

    def _learn_food_menu_from_meal_reply(self, foods: list[str], *, meal_key: str, now: float) -> list[str]:
        if not foods or not bool(runtime_persona_setting(self, "enable_food_menu_recommendation", True)):
            return []
        state = self.data.setdefault("food_menu", {})
        if not isinstance(state, dict):
            state = {}
            self.data["food_menu"] = state
        items = state.setdefault("items", [])
        if not isinstance(items, list):
            items = []
            state["items"] = items
        learned: list[str] = []
        for raw_name in foods[:5]:
            name = _single_line(raw_name, 40)
            if not name:
                continue
            existing = next(
                (
                    item for item in items
                    if isinstance(item, dict)
                    and (
                        _single_line(item.get("name"), 40) == name
                        or name in self._food_menu_list(item.get("aliases"), limit=12, item_limit=24)
                    )
                ),
                None,
            )
            if isinstance(existing, dict):
                existing["use_count"] = _safe_int(existing.get("use_count"), 0, 0) + 1
                existing["last_used_at"] = now
                existing["updated_ts"] = now
                times = self._food_menu_list(existing.get("times"), limit=5, item_limit=16)
                if meal_key and meal_key not in times:
                    times.append(meal_key)
                existing["times"] = times[:5]
            else:
                inferred = self._meal_food_inferred_fields(name)
                items.append(
                    {
                        "id": f"food-auto-{uuid.uuid4().hex[:12]}",
                        "name": name,
                        "type": inferred["type"],
                        "category": inferred["category"],
                        "tags": inferred["tags"],
                        "times": [meal_key] if meal_key else [],
                        "avoid": [],
                        "aliases": [],
                        "note": "从用户实际吃过的内容自动回填",
                        "favorite": False,
                        "hidden": False,
                        "use_count": 1,
                        "last_used_at": now,
                        "created_ts": now,
                        "updated_ts": now,
                        "source": "meal_care_reply",
                    }
                )
            learned.append(name)
        if learned:
            state["updated_ts"] = now
            state["last_auto_learned_at"] = now
            state["last_auto_learned_items"] = learned
        return learned

    def _cancel_planned_meal_care_followup(self, user: dict[str, Any], *, note: str = "") -> bool:
        if not isinstance(user, dict):
            return False
        changed = False
        pending = user.get("pending_followup_event")
        if isinstance(pending, dict) and pending.get("_meal_care_followup"):
            user["pending_followup_event"] = {}
            changed = True
        impulses = user.get("proactive_impulses")
        if isinstance(impulses, list):
            kept = [
                item for item in impulses
                if not (
                    isinstance(item, dict)
                    and _single_line(item.get("reason"), 40) == "meal_care_followup"
                    and str(item.get("state") or "queued") in {"queued", "deferred"}
                )
            ]
            if len(kept) != len(impulses):
                user["proactive_impulses"] = kept
                changed = True
        if self._normalize_legacy_proactive_text(user.get("planned_proactive_reason"), limit=40) == "meal_care_followup":
            marker = getattr(self, "_mark_planned_candidate_status", None)
            if callable(marker):
                marker(user, "cancelled", _single_line(note, 120) or "用户已回应饭点关心")
            clearer = getattr(self, "_clear_pending_proactive_plan", None)
            if callable(clearer):
                clearer(user)
            else:
                user["next_proactive_at"] = 0
                user["planned_proactive_reason"] = ""
            changed = True
        return changed

    def _handle_meal_care_inbound(self, user: dict[str, Any], text: str, *, now: float | None = None) -> dict[str, Any]:
        check_now = _now_ts() if now is None else now
        normalized = _single_line(text, 220)
        if not isinstance(user, dict) or not normalized:
            return {"kind": "none"}
        context = self._meal_care_active_context(user, now=check_now)
        meal_key = _single_line(context.get("meal_key"), 20) or self._meal_key_from_text(normalized) or self._current_food_time_key()
        meal_label = _single_line(context.get("meal_label"), 12) or self._food_menu_time_label(meal_key) or "这顿饭"
        followup_already_sent = _safe_int(context.get("followup_count"), 0, 0, 1) >= 1 if context else False
        foods = self._meal_reply_food_items(normalized, active_context=bool(context))
        self._learn_food_menu_from_meal_reply(foods, meal_key=meal_key, now=check_now)
        if not context:
            # A spontaneous, current-day meal report (for example
            # "我午饭吃了咖喱鸡饭") also resolves that meal slot.  Previously it
            # only populated the menu, so the scheduler could ask the same meal
            # question again later.
            historical_report = bool(
                re.search(r"(?:昨天|昨晚|前天|大前天|上次|那天|之前|以前|前几天)", normalized)
            )
            if foods and not historical_report and meal_key in {"breakfast", "lunch", "dinner"}:
                today = _today_key()
                if str(user.get("meal_care_day") or "") != today:
                    user["meal_care_day"] = today
                    user["meal_care_asked"] = []
                    user["meal_care_satisfied"] = []
                satisfied = user.setdefault("meal_care_satisfied", [])
                if not isinstance(satisfied, list):
                    satisfied = []
                    user["meal_care_satisfied"] = satisfied
                if meal_key not in satisfied:
                    satisfied.append(meal_key)

                planned_reason = self._normalize_legacy_proactive_text(
                    user.get("planned_proactive_reason"),
                    limit=40,
                )
                planned_context = (
                    user.get("planned_meal_care_context")
                    if isinstance(user.get("planned_meal_care_context"), dict)
                    else {}
                )
                planned_meal_key = _single_line(planned_context.get("meal_key"), 20)
                if planned_reason == "meal_care" and (not planned_meal_key or planned_meal_key == meal_key):
                    marker = getattr(self, "_mark_planned_candidate_status", None)
                    if callable(marker):
                        marker(user, "cancelled", "用户已主动说明这顿饭吃了什么")
                    clearer = getattr(self, "_clear_pending_proactive_plan", None)
                    if callable(clearer):
                        clearer(user)
                    user["planned_meal_care_context"] = {}
                impulses = user.get("proactive_impulses")
                if isinstance(impulses, list):
                    kept_impulses = []
                    for impulse in impulses:
                        if not isinstance(impulse, dict):
                            kept_impulses.append(impulse)
                            continue
                        impulse_reason = _single_line(impulse.get("reason"), 40)
                        impulse_context = impulse.get("context")
                        impulse_meal_key = (
                            _single_line(impulse_context.get("meal_key"), 20)
                            if isinstance(impulse_context, dict)
                            else ""
                        )
                        is_pending_same_meal = (
                            impulse_reason in {"meal_care", "meal_care_followup"}
                            and str(impulse.get("state") or "queued") in {"queued", "deferred"}
                            and (not impulse_meal_key or impulse_meal_key == meal_key)
                        )
                        if not is_pending_same_meal:
                            kept_impulses.append(impulse)
                    if len(kept_impulses) != len(impulses):
                        user["proactive_impulses"] = kept_impulses
            return {"kind": "specific" if foods else "none", "foods": foods}
        followup_minutes = _safe_int(runtime_persona_setting(self, "meal_care_followup_minutes", 45), 45, 15, 180)
        kind = "unrelated"
        if foods:
            kind = "specific"
            context.update({"active": False, "stage": "resolved", "resolved_at": check_now, "foods": foods})
            satisfied = user.setdefault("meal_care_satisfied", [])
            if isinstance(satisfied, list) and meal_key not in satisfied:
                satisfied.append(meal_key)
            self._cancel_planned_meal_care_followup(user, note="用户已经说明具体吃了什么")
        elif self._meal_reply_is_not_eaten(normalized):
            kind = "not_eaten_final" if followup_already_sent else "not_eaten"
            context.update(
                {
                    "active": not followup_already_sent,
                    "stage": "resolved_no_meal" if followup_already_sent else "not_eaten",
                    "last_reply_at": check_now,
                    "followup_due_at": check_now + followup_minutes * 60,
                }
            )
        elif self._meal_reply_confirms_eaten(normalized):
            kind = "ate_without_detail_final" if followup_already_sent else "ate_without_detail"
            context.update(
                {
                    "active": not followup_already_sent,
                    "stage": "resolved_without_detail" if followup_already_sent else "awaiting_detail",
                    "last_reply_at": check_now,
                    "followup_due_at": check_now + followup_minutes * 60,
                }
            )
        if kind in {"not_eaten", "ate_without_detail"} and not followup_already_sent:
            # The current passive reply is explicitly instructed to ask the one
            # allowed follow-up, so cancel the scheduled proactive duplicate.
            context["followup_count"] = 1
            context["followup_via_reply_at"] = check_now
            context["followup_due_at"] = 0
            self._cancel_planned_meal_care_followup(user, note="当前被动回复已承担唯一一次吃饭补问")
        elif kind == "unrelated":
            # The user has replied but deliberately did not continue the meal
            # topic (for example, "我在忙"). Treat that as a soft refusal and
            # stop this check-in instead of turning it into another proactive
            # "吃了吗" message later.
            context.update(
                {
                    "active": False,
                    "stage": "closed_unrelated",
                    "last_reply_at": check_now,
                    "closed_at": check_now,
                    "followup_due_at": 0,
                }
            )
            self._cancel_planned_meal_care_followup(user, note="用户未承接饮食话题，本轮饭点关心已结束")
        user["meal_check_context"] = context
        if kind != "unrelated":
            user["meal_care_reply_hint"] = {
                "kind": kind,
                "meal_label": meal_label,
                "foods": foods,
                "text": normalized,
                "ts": check_now,
            }
        return {"kind": kind, "foods": foods, "meal_key": meal_key, "meal_label": meal_label}

    def _meal_care_requires_full_reply(self, user: dict[str, Any], text: str) -> bool:
        if not self._meal_care_active_context(user):
            return False
        normalized = _single_line(text, 160)
        return bool(
            self._meal_reply_is_not_eaten(normalized)
            or self._meal_reply_confirms_eaten(normalized)
            or self._meal_reply_food_items(normalized, active_context=True)
        )

    def _format_meal_care_reply_prompt_section(
        self,
        user: dict[str, Any],
        text: str,
    ) -> PromptSection:
        def build_section(content: str = "") -> PromptSection:
            return prompt_section(
                key="meal.care_reply",
                title="吃饭关心承接",
                source="daily_state",
                content=content,
            )

        if not isinstance(user, dict):
            return build_section()
        hint = user.get("meal_care_reply_hint")
        if not isinstance(hint, dict) or _now_ts() - _safe_float(hint.get("ts"), 0) > 10 * 60:
            return build_section()
        hint_text = _single_line(hint.get("text"), 220)
        current_text = _single_line(text, 260)
        if hint_text and not (hint_text == current_text or hint_text in current_text):
            return build_section()
        kind = _single_line(hint.get("kind"), 30)
        meal_label = _single_line(hint.get("meal_label"), 12) or "这顿饭"
        foods = [_single_line(item, 30) for item in hint.get("foods", []) if _single_line(item, 30)] if isinstance(hint.get("foods"), list) else []
        body = ""
        if kind == "specific" and foods:
            body = f"用户已经明确说{meal_label}吃了{'、'.join(foods)}。自然接住这个具体内容，不要再问吃了什么；这些内容已回填到吃什么候选。"
        elif kind == "ate_without_detail":
            body = f"用户只确认{meal_label}吃过了，但没有说具体吃了什么。先接住当前语气，再自然追问一句具体吃了什么；只问一次，不审问。"
        elif kind == "ate_without_detail_final":
            body = f"用户再次只确认{meal_label}吃过了，仍没有提供具体内容。到这里就接住并收住，不要第三次追问吃了什么。"
        elif kind == "not_eaten":
            body = f"用户明确说{meal_label}还没吃。不要追问“吃了什么”，改为关心准备什么时候吃、想吃什么；如果下方有吃饭候选，只给少量选择，不要一次报菜单。"
        elif kind == "not_eaten_final":
            body = f"用户补问后仍说{meal_label}没吃。简短关心一句就收住，不再继续追问；不要责怪或说教。"
        return build_section(body)

    @staticmethod
    def _food_menu_type_label(value: Any) -> str:
        key = str(value or "").strip().lower()
        return {
            "dish": "菜品",
            "restaurant": "菜馆",
            "takeout": "外卖",
            "drink_snack": "饮品/零食",
            "snack": "饮品/零食",
            "emergency": "应急",
        }.get(key, "候选")

    @staticmethod
    def _food_menu_time_label(value: Any) -> str:
        key = str(value or "").strip().lower()
        return {
            "breakfast": "早餐",
            "lunch": "午餐",
            "dinner": "晚餐",
            "late_night": "夜宵",
            "snack": "加餐",
        }.get(key, _single_line(value, 12))

    @staticmethod
    def _food_menu_list(value: Any, *, limit: int = 12, item_limit: int = 20) -> list[str]:
        raw_items = value if isinstance(value, list) else re.split(r"[,，、\n/|]+", str(value or ""))
        items: list[str] = []
        for raw in raw_items:
            item = _single_line(raw, item_limit)
            if item and item not in items:
                items.append(item)
        return items[:limit]

    def _current_food_time_key(self) -> str:
        hour = self._environment_now().hour
        if 5 <= hour < 10:
            return "breakfast"
        if 10 <= hour < 15:
            return "lunch"
        if 17 <= hour < 21:
            return "dinner"
        if hour >= 21 or hour < 3:
            return "late_night"
        return "snack"

    @staticmethod
    def _meal_key_from_text(text: str) -> str:
        normalized = _single_line(text, 120)
        if any(token in normalized for token in ("早餐", "早饭", "早上吃")):
            return "breakfast"
        if any(token in normalized for token in ("午饭", "午餐", "中午吃")):
            return "lunch"
        if any(token in normalized for token in ("晚饭", "晚餐", "晚上吃")):
            return "dinner"
        if any(token in normalized for token in ("夜宵", "宵夜")):
            return "late_night"
        return ""

    def _food_menu_query_profile(self, text: str, user: dict[str, Any] | None = None) -> dict[str, Any]:
        query = _single_line(text, 220)
        if not query:
            return {"is_query": False}
        meal_context = self._meal_care_active_context(user) if isinstance(user, dict) else {}
        meal_stage = _single_line(meal_context.get("stage"), 24)
        meal_not_eaten = meal_stage == "not_eaten" and self._meal_reply_is_not_eaten(query)
        feature_discussion_markers = (
            "功能", "候选", "开关", "配置", "页面", "注入", "触发", "保存", "管理",
            "不好用", "好用", "误判", "优化", "逻辑", "模块", "面板",
        )
        natural_food_need = bool(
            re.search(r"(今天|现在|这顿|中午|晚上|早上|早饭|早餐|午饭|午餐|晚饭|晚餐|夜宵|宵夜|外卖|点餐|饿|嘴馋|想吃|吃点|吃些|点什么|点啥|吃什么|吃啥)", query)
            and not re.search(r"(功能|开关|配置|页面|注入|触发|保存|管理|模块|面板)", query)
        )
        if any(marker in query for marker in feature_discussion_markers) and not natural_food_need and not meal_not_eaten:
            return {"is_query": False}
        feedback = self._detect_food_feedback(query)
        if feedback.get("already_ate") and not re.search(r"(什么|啥|推荐|点什么|点啥|再吃|还吃)", query):
            return {"is_query": False}
        food_question = bool(
            re.search(r"(吃|点|买|喝|叫).{0,8}(什么|啥|哪[个家]|哪种|推荐|好|合适)", query)
            or re.search(r"(什么|啥).{0,4}(好吃|能吃|可吃|适合吃)", query)
            or re.search(r"(不知道|纠结|想不到|随便).{0,8}(吃|点|买|喝)", query)
            or re.search(r"(推荐|来|整|安排).{0,6}(外卖|夜宵|午饭|晚饭|早餐|吃的|喝的)", query)
            or re.search(r"(饿了|好饿|有点饿|嘴馋|馋了)", query)
            or any(token in query for token in ("吃什么", "吃啥", "点什么", "点啥", "外卖吃", "夜宵吃", "午饭吃", "晚饭吃", "早餐吃"))
            or (len(query) <= 16 and any(token in query for token in ("外卖", "夜宵", "午饭", "晚饭", "早餐")))
        )
        if not food_question and not meal_not_eaten:
            return {"is_query": False}
        preferred_type = ""
        if any(token in query for token in ("外卖", "点餐", "点什么", "点啥", "叫个", "叫点")):
            preferred_type = "takeout"
        elif any(token in query for token in ("出去吃", "店", "馆", "附近", "堂食")):
            preferred_type = "restaurant"
        elif any(token in query for token in ("喝", "奶茶", "咖啡", "饮料", "零食", "甜品")):
            preferred_type = "drink_snack"
        desired_tags: list[str] = []
        tag_map = {
            "清淡": ("清淡", "不油", "少油", "胃不舒服"),
            "热乎": ("热", "暖", "汤", "热乎", "暖和"),
            "快": ("快", "省事", "随便", "懒得", "不想纠结"),
            "辣": ("辣", "重口", "麻辣"),
            "甜": ("甜", "甜品", "奶茶"),
            "顶饱": ("饱", "顶饱", "管饱"),
            "便宜": ("便宜", "省钱", "实惠"),
        }
        for tag, markers in tag_map.items():
            if any(marker in query for marker in markers) and tag not in desired_tags:
                desired_tags.append(tag)
        return {
            "is_query": True,
            "text": query,
            "preferred_type": preferred_type,
            "time_key": _single_line(meal_context.get("meal_key"), 20) or self._current_food_time_key(),
            "meal": _single_line(meal_context.get("meal_label"), 12) or feedback.get("meal") or "",
            "desired_tags": desired_tags,
        }

    def _food_menu_items(self) -> list[dict[str, Any]]:
        state = self.data.get("food_menu") if isinstance(self.data.get("food_menu"), dict) else {}
        items = state.get("items") if isinstance(state.get("items"), list) else []
        normalized: list[dict[str, Any]] = []
        for raw in items:
            if not isinstance(raw, dict):
                continue
            name = _single_line(raw.get("name"), 40)
            if not name:
                continue
            item = dict(raw)
            item["name"] = name
            item["type"] = _single_line(item.get("type"), 20) or "dish"
            item["category"] = _single_line(item.get("category"), 24)
            item["tags"] = self._food_menu_list(item.get("tags"), limit=10, item_limit=16)
            item["times"] = self._food_menu_list(item.get("times"), limit=5, item_limit=16)
            item["avoid"] = self._food_menu_list(item.get("avoid"), limit=8, item_limit=24)
            item["aliases"] = self._food_menu_list(item.get("aliases"), limit=10, item_limit=24)
            item["note"] = _single_line(item.get("note"), 80)
            item["favorite"] = bool(item.get("favorite"))
            item["hidden"] = bool(item.get("hidden"))
            normalized.append(item)
        return normalized

    def _score_food_menu_item(self, item: dict[str, Any], profile: dict[str, Any]) -> float:
        query = str(profile.get("text") or "")
        if item.get("hidden"):
            return -999.0
        for token in item.get("avoid", []):
            if token and token in query:
                return -999.0
        score = 1.0
        if item.get("favorite"):
            score += 1.2
        preferred_type = str(profile.get("preferred_type") or "")
        if preferred_type and str(item.get("type") or "") == preferred_type:
            score += 2.4
        times = item.get("times") if isinstance(item.get("times"), list) else []
        if times:
            score += 1.5 if profile.get("time_key") in times else -0.8
        desired_tags = profile.get("desired_tags") if isinstance(profile.get("desired_tags"), list) else []
        tags = item.get("tags") if isinstance(item.get("tags"), list) else []
        score += sum(
            0.9
            for desired in desired_tags
            if any(desired == tag or desired in tag or tag in desired for tag in tags)
        )
        category = str(item.get("category") or "")
        searchable = [item.get("name"), category, item.get("note"), *item.get("aliases", []), *tags]
        if any(part and str(part) in query for part in searchable):
            score += 2.8
        last = _safe_float(item.get("last_recommended_at"), 0, 0)
        if last > 0:
            age_hours = max(0.0, (_now_ts() - last) / 3600)
            if age_hours < 8:
                score -= 1.4
            elif age_hours < 36:
                score -= 0.5
        score += min(0.8, _safe_int(item.get("use_count"), 0, 0) * 0.04)
        return score

    def _mark_food_menu_items_recommended(self, candidates: list[dict[str, Any]]) -> None:
        ids = {
            _single_line(item.get("id"), 48)
            for item in candidates
            if isinstance(item, dict) and _single_line(item.get("id"), 48)
        }
        if not ids:
            return
        state = self.data.get("food_menu") if isinstance(self.data.get("food_menu"), dict) else {}
        items = state.get("items") if isinstance(state.get("items"), list) else []
        if not items:
            return
        now = _now_ts()
        changed = False
        for item in items:
            if not isinstance(item, dict):
                continue
            if _single_line(item.get("id"), 48) in ids:
                item["last_recommended_at"] = now
                item["updated_ts"] = now
                changed = True
        if changed:
            state["updated_ts"] = now
            self.data["food_menu"] = state
            self._save_data_sync(sections={"food_menu"})

    def _food_menu_candidates_for_prompt(self, text: str, *, limit: int = 3, user: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        profile = self._food_menu_query_profile(text, user=user)
        if not profile.get("is_query"):
            return []
        scored: list[tuple[float, dict[str, Any]]] = []
        for item in self._food_menu_items():
            score = self._score_food_menu_item(item, profile)
            if score > -100:
                scored.append((score, item))
        scored.sort(key=lambda pair: (pair[0], bool(pair[1].get("favorite")), _safe_int(pair[1].get("use_count"), 0, 0)), reverse=True)
        return [item for _, item in scored[: max(1, min(5, limit))]]

    def _format_food_menu_reply_prompt_section(
        self,
        text: str,
        *,
        limit: int = 3,
        user: dict[str, Any] | None = None,
    ) -> PromptSection:
        def build_section(content: str = "") -> PromptSection:
            return prompt_section(
                key="meal.food_candidates",
                title="吃饭候选",
                source="daily_state",
                content=content,
            )

        profile = self._food_menu_query_profile(text, user=user)
        if not profile.get("is_query"):
            return build_section()
        candidates = self._food_menu_candidates_for_prompt(text, limit=limit, user=user)
        if not candidates:
            return build_section()
        self._mark_food_menu_items_recommended(candidates)
        lines: list[str] = []
        for item in candidates:
            parts = [item.get("name")]
            label = self._food_menu_type_label(item.get("type"))
            category = _single_line(item.get("category"), 18)
            if category:
                label = f"{label}/{category}"
            meta = [label]
            times = [self._food_menu_time_label(value) for value in item.get("times", []) if self._food_menu_time_label(value)]
            if times:
                meta.append("适合" + "、".join(times[:3]))
            tags = item.get("tags", [])[:4]
            if tags:
                meta.append("偏" + "、".join(tags))
            note = _single_line(item.get("note"), 54)
            detail = "，".join(meta)
            line = f"{parts[0]}（{detail}）"
            if note:
                line += f"：{note}"
            lines.append(line)
        meal = _single_line(profile.get("meal"), 12) or self._food_menu_time_label(profile.get("time_key")) or "这顿"
        body = f"这轮用户在问{meal}吃什么。可参考：" + "；".join(lines) + "。"
        return build_section(body)

    def _mark_food_menu_item_used_from_text(self, text: str) -> list[str]:
        query = _single_line(text, 220)
        if not query:
            return []
        state = self.data.get("food_menu") if isinstance(self.data.get("food_menu"), dict) else {}
        items = state.get("items") if isinstance(state.get("items"), list) else []
        if not items:
            return []
        now = _now_ts()
        matched: list[str] = []
        for item in items:
            if not isinstance(item, dict) or item.get("hidden"):
                continue
            terms = [item.get("name"), *self._food_menu_list(item.get("aliases"), limit=10, item_limit=24)]
            if any(term and str(term) in query for term in terms):
                item["use_count"] = _safe_int(item.get("use_count"), 0, 0) + 1
                item["last_used_at"] = now
                matched.append(_single_line(item.get("name"), 40))
        if matched:
            state["updated_ts"] = now
            self.data["food_menu"] = state
        return matched[:5]

