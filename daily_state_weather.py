# -*- coding: utf-8 -*-
"""天气域。

由 tools/split_mixin_domain.py 从 daily_state.py 机械抽取（78 个方法 + 8 个模块级名字 + 1 个类级赋值 / 2550 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 DailyStateMixin）。
"""
from __future__ import annotations

import asyncio
import hashlib
import math
import random
import re
import unicodedata
from .conversation_injection_plan import PLACEMENT_DYNAMIC_SYSTEM, PLACEMENT_TURN_TAIL, get_conversation_injection_plan
from .conversation_prompt_section import PromptRenderMode, PromptSection, prompt_section, render_prompt_sections
from .helpers import _now_ts, _safe_float, _safe_int, _single_line, _today_key
from .persona_config import runtime_persona_setting
from astrbot.api.event import AstrMessageEvent
from astrbot.api.provider import ProviderRequest
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode, urlparse

from .logging_util import get_module_logger

logger = get_module_logger(__name__)



def _openmeteo_weather_description(code: Any) -> str:
    try:
        code = int(code)
    except (TypeError, ValueError):
        return "未知"
    if code == 0:
        return "晴天"
    if code == 1:
        return "少云"
    if code == 2:
        return "多云"
    if code == 3:
        return "阴天"
    if code in {45, 48}:
        return "雾"
    if 51 <= code <= 55:
        return "毛毛雨"
    if code in {56, 57}:
        return "冻毛毛雨"
    if 61 <= code <= 65:
        return "降雨"
    if code in {66, 67}:
        return "冻雨"
    if 71 <= code <= 77:
        return "降雪"
    if 80 <= code <= 82:
        return "阵雨"
    if code in {85, 86}:
        return "阵雪"
    if 95 <= code <= 99:
        return "雷暴"
    return "未知"

# 和风天气预警接口使用独立的 API Host，并支持 JWT 或 API Key 认证。
# 保留颜色等级的顺序，供缓存层和上层提示词按最低等级筛选；解析层
# 始终保留完整数据。
_QWEATHER_ALERT_COLOR_RANK = {
    "蓝": 0,
    "蓝色": 0,
    "blue": 0,
    "yellow": 1,
    "黄": 1,
    "黄色": 1,
    "orange": 2,
    "橙": 2,
    "橙色": 2,
    "red": 3,
    "红": 3,
    "红色": 3,
}

_QWEATHER_ALERT_SEVERITY_RANK = {
    "unknown": 0,
    "minor": 1,
    "moderate": 2,
    "severe": 3,
    "extreme": 4,
}

def _qweather_alert_text(value: Any, limit: int = 512) -> str:
    """Normalize a provider field without allowing multiline/oversized cache data."""

    text = _single_line(value, limit * 2)
    if not text:
        return ""
    return text[:limit]

def _qweather_alert_first(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value is not None and value != "":
            return value
    return ""

def _qweather_alert_string_list(value: Any, *, limit: int = 16, item_limit: int = 80) -> list[str]:
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, (list, tuple, set)):
        values = list(value)
    else:
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in values:
        if isinstance(item, dict):
            item = _qweather_alert_first(item, "code", "name", "type")
        text = _qweather_alert_text(item, item_limit)
        if text and text not in seen:
            seen.add(text)
            result.append(text)
        if len(result) >= limit:
            break
    return result

def _qweather_alert_color(value: Any) -> tuple[str, str]:
    """Return a human-readable color and its provider code."""

    code = ""
    name = ""
    if isinstance(value, dict):
        code = _qweather_alert_text(value.get("code"), 32).lower()
        name = _qweather_alert_text(value.get("name"), 32)
    else:
        name = _qweather_alert_text(value, 32)
        code = name.lower()
    # QWeather's current API returns color.code (blue/yellow/orange/red), while
    # older integrations and some compatible providers return Chinese labels.
    aliases = {
        "blue": "蓝色",
        "yellow": "黄色",
        "orange": "橙色",
        "red": "红色",
        "bluealert": "蓝色",
        "yellowalert": "黄色",
        "orangealert": "橙色",
        "redalert": "红色",
    }
    normalized_code = aliases.get(code, code)
    if normalized_code in {"蓝", "蓝色"}:
        name = "蓝色"
    elif normalized_code in {"黄", "黄色"}:
        name = "黄色"
    elif normalized_code in {"橙", "橙色"}:
        name = "橙色"
    elif normalized_code in {"红", "红色"}:
        name = "红色"
    elif not name:
        name = normalized_code
    return _qweather_alert_text(name, 32), _qweather_alert_text(code, 32)

def _qweather_alert_rank(value: Any) -> int:
    text = _qweather_alert_text(value, 32).strip().lower()
    if text in _QWEATHER_ALERT_COLOR_RANK:
        return _QWEATHER_ALERT_COLOR_RANK[text]
    # A severity value is useful for non-Chinese/global warning feeds.
    return _QWEATHER_ALERT_SEVERITY_RANK.get(text, 0)


class DailyStateWeatherMixin:
    """天气域（从 DailyStateMixin 拆出）。"""


    def _pick_weather_window(self, weather_kind: str) -> str:
        hour = self._environment_now().hour
        if weather_kind == "rain":
            if 6 <= hour < 11:
                return "08:20-10:40"
            if 11 <= hour < 17:
                return "12:40-16:30"
            if 17 <= hour < 23:
                return "18:10-21:10"
            return "09:00-10:30"
        if 16 <= hour < 20:
            return "17:10-19:20"
        return "15:30-18:10"

    def _environment_weather_observation(self, weather: dict[str, Any] | None) -> dict[str, Any]:
        text = self._weather_summary_text(weather)
        if text == "暂无天气信息":
            return {}
        compact = text.lower()
        category = "other"
        severity = 1
        category_rules = (
            ("thunder", 4, ("雷暴", "雷阵雨", "打雷", "雷电")),
            ("heavy_rain", 4, ("暴雨", "大暴雨", "特大暴雨", "大雨")),
            ("snow", 4, ("暴雪", "大雪", "中雪", "小雪", "雨夹雪", "降雪")),
            ("dust", 4, ("沙尘暴", "扬沙", "浮尘")),
            ("rain", 3, ("阵雨", "中雨", "小雨", "降雨", "下雨", "雨天")),
            ("fog", 3, ("大雾", "浓雾", "雾霾", "雾")),
            ("wind", 3, ("大风", "强风", "狂风", "阵风")),
            ("clear", 1, ("晴朗", "晴天", "晴")),
            ("cloud", 1, ("阴天", "多云", "阴")),
        )
        for candidate, candidate_severity, markers in category_rules:
            if any(marker in compact for marker in markers):
                category = candidate
                severity = candidate_severity
                break
        temperature = None
        match = re.search(r"(-?\d+(?:\.\d+)?)\s*(?:°\s*c|℃|摄氏度)", compact, flags=re.I)
        if match:
            try:
                temperature = float(match.group(1))
            except (TypeError, ValueError):
                temperature = None
        return {
            "text": _single_line(text, 120),
            "category": category,
            "severity": severity,
            "temperature": temperature,
            "fetched_ts": _safe_float((weather or {}).get("fetched_ts"), 0),
        }

    def _detect_environment_weather_change(
        self,
        previous: dict[str, Any] | None,
        current: dict[str, Any] | None,
    ) -> dict[str, Any]:
        before = self._environment_weather_observation(previous)
        after = self._environment_weather_observation(current)
        if not before or not after:
            return {}
        old_category = _single_line(before.get("category"), 24)
        new_category = _single_line(after.get("category"), 24)
        old_temp = before.get("temperature")
        new_temp = after.get("temperature")
        category_labels = {
            "thunder": "突然开始打雷",
            "heavy_rain": "雨势突然变大",
            "rain": "外面开始下雨",
            "snow": "外面开始下雪",
            "fog": "外面突然起雾",
            "wind": "外面的风突然变大",
            "dust": "外面出现沙尘天气",
            "clear": "外面的天突然放晴",
            "cloud": "外面的天色明显转阴",
        }
        wet_categories = {"rain", "heavy_rain", "thunder", "snow"}
        kind = ""
        topic = ""
        score = 0
        if old_category != new_category and new_category in category_labels:
            if old_category in wet_categories and new_category in {"clear", "cloud", "other"}:
                kind = "precipitation_stopped"
                topic = "外面的雨雪停了"
                score = 78
            elif new_category in {"thunder", "heavy_rain", "snow", "dust"}:
                kind = f"weather_to_{new_category}"
                topic = category_labels[new_category]
                score = 92
            elif new_category in {"rain", "fog", "wind"}:
                kind = f"weather_to_{new_category}"
                topic = category_labels[new_category]
                score = 86
            elif old_category in wet_categories and new_category == "clear":
                kind = "weather_cleared"
                topic = category_labels[new_category]
                score = 76

        temperature_delta = None
        if isinstance(old_temp, (int, float)) and isinstance(new_temp, (int, float)):
            temperature_delta = float(new_temp) - float(old_temp)
            if abs(temperature_delta) >= 5.0 and not kind:
                kind = "temperature_jump"
                topic = "气温突然升高了" if temperature_delta > 0 else "气温突然降下来了"
                score = 84 if abs(temperature_delta) >= 8.0 else 78
        if not kind:
            return {}
        fingerprint = f"{kind}:{old_category}>{new_category}:{round(float(new_temp), 1) if isinstance(new_temp, (int, float)) else 'na'}"
        return {
            "kind": kind,
            "topic": topic,
            "score": score,
            "fingerprint": fingerprint,
            "previous": before,
            "current": after,
            "temperature_delta": temperature_delta,
        }

    def _environment_change_owner_users(self) -> list[tuple[str, dict[str, Any]]]:
        users = self.data.get("users") if isinstance(getattr(self, "data", None), dict) else {}
        if not isinstance(users, dict):
            return []
        targets: list[tuple[str, dict[str, Any]]] = []
        for raw_user_id, user in users.items():
            user_id = str(raw_user_id or "").strip()
            if not user_id or not isinstance(user, dict) or not user.get("umo"):
                continue
            if self._private_user_role(user, user_id) != "owner":
                continue
            if not self._user_enabled_for_proactive(user_id, user):
                continue
            targets.append((user_id, user))
        return targets

    def _queue_environment_change_candidates_locked(self, change: dict[str, Any], *, now: float) -> int:
        if not isinstance(change, dict) or not _single_line(change.get("topic"), 80):
            return 0
        current = self._environment_fromtimestamp(now)
        minute = current.hour * 60 + current.minute
        if not (6 * 60 <= minute < 23 * 60 + 30):
            return 0
        offered = 0
        for user_id, user in self._environment_change_owner_users():
            scheduled = now + random.uniform(1.0, 3.0) * 60.0
            change_fingerprint = _single_line(change.get("fingerprint"), 160)
            candidate = {
                "source": "environment_change",
                "reason": "environment_change",
                "action": "message",
                "scheduled_ts": scheduled,
                "window_start_at": scheduled,
                "preferred_ts": scheduled,
                "best_until_at": scheduled + 20 * 60,
                "expire_at": scheduled + 45 * 60,
                "topic": _single_line(change.get("topic"), 80),
                "motive": "刚注意到外界环境发生了明显变化，想趁变化还新鲜时自然提一句",
                "score": _safe_int(change.get("score"), 82, 0, 100),
                "origin_event_id": (
                    "environment:" + hashlib.sha1(change_fingerprint.encode("utf-8", errors="ignore")).hexdigest()[:24]
                    if change_fingerprint
                    else ""
                ),
                "context_key": "planned_environment_change_context",
                "context": deepcopy(change),
            }
            if self._offer_proactive_candidate(user_id, user, candidate):
                offered += 1
        return offered

    def _format_environment_change_prompt(self, user: dict[str, Any], *, reason: str = "") -> str:
        section = self._format_environment_change_prompt_section(user, reason=reason)
        return render_prompt_sections([section], mode=PromptRenderMode.LABELED_BLOCK)

    def _format_environment_change_prompt_section(
        self,
        user: dict[str, Any],
        *,
        reason: str = "",
    ) -> PromptSection:
        def build_section(content: str = "") -> PromptSection:
            return prompt_section(
                key="environment.change",
                title="刚发生的环境变化",
                source="daily_state",
                content=content,
            )

        if reason != "environment_change" or not isinstance(user, dict):
            return build_section()
        context = user.get("planned_environment_change_context")
        if not isinstance(context, dict):
            return build_section()
        before = context.get("previous") if isinstance(context.get("previous"), dict) else {}
        after = context.get("current") if isinstance(context.get("current"), dict) else {}
        body = (
            f"- 变化前：{_single_line(before.get('text'), 120) or '无可靠信息'}\n"
            f"- 当前：{_single_line(after.get('text'), 120) or '无可靠信息'}\n"
            f"- 可用切口：{_single_line(context.get('topic'), 80)}\n"
            "这是刚刷新到的实时环境变化，只能贴着上述事实自然说一句。不要说监测、接口、天气缓存或系统提醒；"
            "不要扩写成天气预报，也不要虚构用户正在室外。"
        )
        return build_section(body)

    def _format_weather_alert_prompt(self, user: dict[str, Any], *, reason: str = "") -> str:
        """Render one structured alert for the proactive generation prompt."""

        section = self._format_weather_alert_prompt_section(user, reason=reason)
        return render_prompt_sections([section], mode=PromptRenderMode.LABELED_BLOCK)

    def _format_weather_alert_prompt_section(
        self,
        user: dict[str, Any],
        *,
        reason: str = "",
    ) -> PromptSection:
        def build_section(content: str = "") -> PromptSection:
            return prompt_section(
                key="weather.alert",
                title="当前气象预警",
                source="daily_state",
                content=content,
            )

        if reason != "weather_alert" or not isinstance(user, dict):
            return build_section()
        context = user.get("planned_weather_alert_context")
        if not isinstance(context, dict):
            return build_section()
        alert = context.get("alert") if isinstance(context.get("alert"), dict) else context
        if not isinstance(alert, dict):
            return build_section()
        kind = _single_line(context.get("kind"), 20)
        status = _single_line(context.get("status"), 32) or {
            "new": "刚发布",
            "updated": "刚更新",
            "cancelled": "已解除",
            "resolved": "已解除",
            "expired": "已过期",
        }.get(kind, "有变化")
        level = _single_line(alert.get("color") or alert.get("severity"), 24)
        event = _single_line(alert.get("event") or "天气", 48)
        headline = _single_line(alert.get("headline") or alert.get("description"), 220)
        instruction = _single_line(alert.get("instruction"), 500)
        sender = _single_line(alert.get("sender"), 100)
        expire = _single_line(alert.get("expire_time"), 60)
        lines = [
            f"- 状态：{status}",
            f"- 等级/现象：{level + '｜' if level else ''}{event}",
            f"- 标题：{headline or '暂无标题'}",
        ]
        if instruction:
            lines.append(f"- 防护建议：{instruction}")
        if sender:
            lines.append(f"- 发布方：{sender}")
        if expire:
            lines.append(f"- 预计结束：{expire}")
        lines.append(
            "这是一条与主要用户所在地点相关的结构化预警事实。只把它转成一句自然、克制、及时的私聊；"
            "可以提醒减少外出、留意雷雨或按防护建议行动，但不要虚构用户正在室外、已经受灾或一定会发生的结果。"
            "不要说监测、接口、缓存、轮询、API、数据源或内部字段，也不要把普通天气背景和这条预警混成播报清单。"
        )
        return build_section("\n".join(lines))

    async def _maybe_refresh_environment_change(self) -> None:
        if not bool(runtime_persona_setting(self, "enable_environment_change_proactive", True)) or not runtime_persona_setting(self, "enable_weather_context", True):
            return
        lock = getattr(self, "_environment_change_lock", None)
        if not isinstance(lock, asyncio.Lock):
            lock = asyncio.Lock()
            self._environment_change_lock = lock
        async with lock:
            now = _now_ts()
            state = self.data.setdefault("environment_change_awareness", {})
            if not isinstance(state, dict):
                state = {}
                self.data["environment_change_awareness"] = state
            if now < _safe_float(state.get("next_check_at"), 0):
                return
            initialized = bool(state.get("initialized"))
            interval_minutes = max(
                5,
                    _safe_int(runtime_persona_setting(self, "environment_change_check_minutes", 10), 10, 5, 60),
            )
            state["next_check_at"] = now + interval_minutes * 60
            previous = deepcopy(self.data.get("daily_weather", {}))
            current = await self._ensure_weather_context(force=True)
            state["last_check_at"] = now
            state["last_observation"] = self._environment_weather_observation(current)
            state["initialized"] = True
            if not initialized:
                self._schedule_data_save(
                    sections={"environment_change_awareness", "daily_weather"},
                    delay=0.5,
                )
                return
            change = self._detect_environment_weather_change(previous, current)
            if not change:
                self._schedule_data_save(
                    sections={"environment_change_awareness", "daily_weather"},
                    delay=0.5,
                )
                return
            fingerprint = _single_line(change.get("fingerprint"), 160)
            cooldown_minutes = max(
                20,
                    _safe_int(runtime_persona_setting(self, "environment_change_cooldown_minutes", 90), 90, 20, 360),
            )
            last_prompted_at = _safe_float(state.get("last_prompted_at"), 0)
            recent_fingerprints = state.get("recent_fingerprints")
            if not isinstance(recent_fingerprints, dict):
                recent_fingerprints = {}
                state["recent_fingerprints"] = recent_fingerprints
            cutoff = now - cooldown_minutes * 60
            for old_key, old_ts in list(recent_fingerprints.items()):
                if _safe_float(old_ts, 0) < cutoff:
                    recent_fingerprints.pop(old_key, None)
            recently_repeated = _safe_float(recent_fingerprints.get(fingerprint), 0) >= cutoff
            # 通用冷却：距上一次环境突变提示 < cooldown 内，非紧急（score<90）的变化不
            # 重复开口。不同变化（雨停/下雨/雨势变大）指纹不同，recently_repeated 拦不住；
            # 旧代码用写死的 20 分钟，cooldown_minutes 只对同指纹去重，360 分钟设置等于
            # 形同虚设（实测 30-40 分钟就 offer 一条）。score>=90 的极端变化保留逃生口。
            too_soon = now - last_prompted_at < cooldown_minutes * 60 and _safe_int(change.get("score"), 0) < 90
            if recently_repeated or too_soon:
                self._schedule_data_save(
                    sections={"environment_change_awareness", "daily_weather"},
                    delay=0.5,
                )
                return
            offered = 0
            async with self._data_lock:
                offered = self._queue_environment_change_candidates_locked(change, now=now)
                state["last_change"] = deepcopy(change)
                state["last_change_at"] = now
                if offered:
                    state["last_fingerprint"] = fingerprint
                    state["last_prompted_at"] = now
                    state["last_prompted_users"] = offered
                    recent_fingerprints[fingerprint] = now
                self._save_data_sync(
                    sections={
                        "environment_change_awareness",
                        "daily_weather",
                        "users",
                        "proactive_candidate_pool",
                    }
                )
            if offered:
                logger.info(
                    "环境突变已进入即时主动候选: kind=%s targets=%s topic=%s",
                    _single_line(change.get("kind"), 40),
                    offered,
                    _single_line(change.get("topic"), 80),
                )

    def _weather_context_config_key(self) -> str:
        """Return a credential-free identity for the active weather place."""

        source = str(runtime_persona_setting(self, "weather_source", "qweather") or "qweather").strip().lower()
        parts = [source, self._weather_window_timezone()]
        if source == "qweather":
            parts.extend((self._qweather_weather_api_host(), self._qweather_location_identity()))
        elif source == "amap":
            parts.append(_single_line(runtime_persona_setting(self, "weather_amap_city", ""), 80).casefold())
        elif source == "openweathermap":
            city = _single_line(runtime_persona_setting(self, "weather_city", ""), 120).casefold()
            if city:
                parts.append("city:" + city)
            else:
                location = self._qweather_legacy_weather_location()
                parts.append("coordinates:" + repr(location))
        elif source == "openmeteo":
            parts.append("coordinates:" + repr(self._qweather_legacy_weather_location()))
        raw = "|".join(parts)
        return hashlib.sha256(raw.encode("utf-8", "ignore")).hexdigest()[:24]

    def _weather_window_timezone(self) -> str:
        """Resolve the effective timezone without requiring ProactiveMixin."""

        resolver = getattr(self, "_proactive_window_timezone", None)
        if callable(resolver):
            try:
                resolved = _single_line(resolver(), 64)
            except Exception:
                resolved = ""
            if resolved:
                return resolved
        return _single_line(
            getattr(self, "environment_perception_timezone", ""),
            64,
        ) or "Asia/Shanghai"

    async def _ensure_weather_context(self, force: bool = False) -> dict[str, Any]:
        today = _today_key()
        if not runtime_persona_setting(self, "enable_weather_context", True):
            return {"date": today, "prompt": "暂无天气信息", "source": "disabled"}
        weather_source = runtime_persona_setting(self, "weather_source", "qweather")
        config_key = self._weather_context_config_key()
        cached = self.data.get("daily_weather", {})
        if isinstance(cached, dict):
            fetched_at = _safe_float(cached.get("fetched_ts"), 0)
            if (
                not force
                and cached.get("date") == today
                and cached.get("weather_source", "qweather") == weather_source
                and cached.get("config_key") == config_key
                and _now_ts() - fetched_at < _safe_int(runtime_persona_setting(self, "weather_refresh_minutes", 90), 90, 1) * 60
            ):
                return cached
        prompt = "暂无天气信息"
        source = "none"
        own_result = await self._fetch_own_weather_prompt()
        text = _single_line(own_result.get("prompt"), 120) if isinstance(own_result, dict) else ""
        location_label = _single_line(own_result.get("location_label"), 120) if isinstance(own_result, dict) else ""
        if not location_label and str(weather_source).strip().lower() == "qweather":
            location_label = _single_line(self._qweather_location_snapshot().get("label"), 120)
        if text:
            prompt = text
            source = str(own_result.get("source") or "private_companion")
        else:
            plugin = self._get_screen_companion_plugin()
            if plugin is not None and hasattr(plugin, "_get_weather_prompt"):
                try:
                    result = await plugin._get_weather_prompt()
                    text = _single_line(result, 120)
                    if text:
                        prompt = text
                        source = "screen_companion"
                except Exception as e:
                    logger.debug(f"获取天气信息失败: {e}")
        weather = {
            "date": today,
            "prompt": prompt,
            "source": source,
            "weather_source": weather_source,
            "config_key": config_key,
            "location_label": location_label,
            "fetched_ts": _now_ts(),
        }
        async with self._data_lock:
            self.data["daily_weather"] = weather
            self._save_data_sync(sections={"daily_weather"})
        return weather

    @staticmethod
    def _user_asks_current_weather(text: Any) -> bool:
        """识别用户是否在询问本地当前天气，而不是讨论天气功能本身。"""

        cleaned = _single_line(text, 180)
        if not cleaned:
            return False
        compact = re.sub(r"[\s，。！？!?,.、~～…：:；;]+", "", cleaned).casefold()
        if not compact:
            return False

        meta_terms = (
            "天气api",
            "天气接口",
            "天气插件",
            "天气功能",
            "天气配置",
            "天气设置",
            "天气日志",
            "天气代码",
            "和风天气api",
            "和风天气接口",
            "和风天气配置",
        )
        if any(term in compact for term in meta_terms) or re.search(
            r"天气.{0,3}(?:api|接口|插件|功能|配置|设置|日志|代码)",
            compact,
            re.IGNORECASE,
        ):
            return False
        if "天气" in compact and any(
            term in compact
            for term in ("怎么接入", "如何接入", "怎么配置", "如何配置", "报错", "排障", "调试")
        ):
            return False

        # 当前实况接口不能证明未来天气，混合或未来预报问题交给真正的预报能力处理。
        if any(term in compact for term in ("明天", "后天", "大后天", "未来", "下周", "周末")):
            return False

        # “我这边”明确指向用户所在地，不能拿 Bot 配置地点的实况代答。
        if any(term in compact for term in ("我这边", "我们这边", "俺这边", "我这里", "我们这里")):
            return False
        current_terms = ("现在", "当前", "今天", "今日", "此刻", "这会儿", "外面", "当地", "这边", "你那边")
        asks_now = any(term in compact for term in current_terms)
        if "天气" in compact:
            if any(term in compact for term in ("喜欢什么天气", "讨厌什么天气", "什么天气最", "天气原理", "天气形成", "天气变化的原因")):
                return False
            return asks_now or len(compact) <= 12 or any(
                term in compact for term in ("天气怎么样", "天气如何", "天气咋样", "什么天气", "查天气", "看看天气", "天气好吗", "天气呢")
            )
        if re.search(r"(?:多少|几)(?:度|°c?|摄氏度)", compact, re.IGNORECASE):
            return True
        if any(term in compact for term in ("气温", "温度")):
            return asks_now or len(compact) <= 10
        if any(term in compact for term in ("要带伞", "需要带伞", "用带伞", "下雨吗", "在下雨", "下雪吗", "在下雪")):
            return asks_now or len(compact) <= 10
        if asks_now and any(term in compact for term in ("冷不冷", "热不热", "冷吗", "热吗")):
            return True
        return False

    async def _append_weather_query_context_to_request(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        *,
        current_user: dict[str, Any] | None = None,
    ) -> bool:
        """显式天气查询时按需获取实况，并把可信结果注入当前请求。"""

        marker = "<!-- private_companion_weather_query_v1 -->"
        if self._request_has_managed_prompt_marker(req, marker):
            return True
        inbound_text = _single_line(
            getattr(event, "private_companion_group_text", "")
            or getattr(event, "message_str", "")
            or getattr(req, "prompt", ""),
            180,
        )
        if not self._user_asks_current_weather(inbound_text):
            return False
        if not bool(runtime_persona_setting(self, "enable_weather_context", True)):
            return False

        weather = await self._ensure_weather_context(force=False)
        prompt = self._weather_summary_text(weather)
        valid = bool(prompt and prompt != "暂无天气信息")
        role = ""
        location_label = ""
        fetched_at = _safe_float(weather.get("fetched_ts"), 0) if isinstance(weather, dict) else 0
        configured_source = str(runtime_persona_setting(self, "weather_source", "qweather") or "qweather").strip().lower()
        expected_source = {
            "qweather": "qweather",
            "amap": "amap",
            "openmeteo": "openmeteo",
            "openweathermap": "private_companion",
        }.get(configured_source, configured_source)
        actual_source = _single_line(weather.get("source"), 40) if isinstance(weather, dict) else ""
        using_fallback = bool(valid and expected_source and actual_source != expected_source)
        if (not valid or using_fallback) and (fetched_at <= 0 or _now_ts() - fetched_at >= 60):
            weather = await self._ensure_weather_context(force=True)
            prompt = self._weather_summary_text(weather)
            valid = bool(prompt and prompt != "暂无天气信息")

        if valid:
            injection_title = "本轮当前天气查询"
            source = _single_line(weather.get("source"), 40) if isinstance(weather, dict) else ""
            source_label = {
                "qweather": "和风天气",
                "amap": "高德天气",
                "openmeteo": "Open-Meteo",
                "private_companion": "OpenWeatherMap",
                "screen_companion": "天气联动来源",
            }.get(source, "已配置的天气来源")
            details = [f"实况：{prompt}", f"来源：{source_label}"]
            role = self._private_user_role(current_user or {}) if isinstance(current_user, dict) else ""
            location_label = _single_line(weather.get("location_label"), 80) if isinstance(weather, dict) else ""
            if role == "owner" and location_label:
                details.insert(0, f"地点：{location_label}")
            fetched_ts = _safe_float(weather.get("fetched_ts"), 0) if isinstance(weather, dict) else 0
            if fetched_ts > 0:
                details.append(f"数据获取时间：{datetime.fromtimestamp(fetched_ts).strftime('%H:%M')}")
            injection = (
                "用户本轮明确询问当前天气。以下数据由本插件配置的天气来源直接取得，是本轮回答依据：\n"
                + "\n".join(details)
                + "\n请直接结合用户问题自然回答，不要再调用搜索、浏览器、地图、记忆或其他天气工具。"
                "只能陈述以上数据能够证明的当前实况；不要据此编造未来预报，也不要向用户提及系统提示词或注入过程。"
            )
        else:
            injection_title = "本轮天气查询暂未取得实况"
            injection = (
                "用户本轮明确询问当前天气，但本插件已尝试读取配置的天气来源，本轮没有取得有效实况。\n"
                "不要编造天气，也不要调用搜索、浏览器、地图、记忆或多个工具反复查找。"
                "如果当前确有一个明确、专用的天气工具，最多尝试一次；否则简短说明暂时没有取到天气即可。"
            )

        weather_section = prompt_section(
            key="weather.query",
            title=injection_title,
            source="weather_query",
            content=injection,
        )
        placement = "prompt" if self._append_turn_prompt_fragment_by_position(
            req,
            marker,
            weather_section,
            priority=24,
            force_dynamic=True,
        ) else "system_prompt"
        plan = get_conversation_injection_plan(req)
        if placement == "system_prompt":
            if plan is not None:
                plan.materialize_system_block(
                    req,
                    section=weather_section,
                    marker=marker,
                    priority=24,
                    placement=PLACEMENT_DYNAMIC_SYSTEM,
                )
        elif plan is not None and not plan.contains_marker(marker):
            plan.add(
                section=weather_section,
                marker=marker,
                priority=24,
                placement=PLACEMENT_TURN_TAIL,
            )
        recorder = getattr(self, "_record_request_prompt_fragment", None)
        if callable(recorder):
            await recorder(
                event,
                title="当前天气查询注入",
                key="weather.query",
                text=injection,
                source="weather_query",
                metadata={"注入位置": placement, "获取成功": valid},
            )
        logger.info(
            "当前天气查询已处理: session=%s success=%s source=%s location_visible=%s",
            _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            valid,
            _single_line(weather.get("source"), 40) if isinstance(weather, dict) else "none",
            bool(valid and isinstance(current_user, dict) and role == "owner" and location_label),
        )
        return True

    def _weather_summary_text(self, weather: dict[str, Any] | None) -> str:
        if not isinstance(weather, dict):
            return "暂无天气信息"
        text = _single_line(weather.get("prompt"), 120)
        return text or "暂无天气信息"

    @staticmethod
    def _qweather_alert_rank(value: Any) -> int:
        return _qweather_alert_rank(value)

    @staticmethod
    def _normalize_qweather_api_host(value: Any) -> str:
        """Normalize a QWeather API Host while refusing malformed URLs."""

        raw = str(value or "").strip()
        if not raw:
            return ""
        if "://" not in raw:
            raw = "https://" + raw
        try:
            parsed = urlparse(raw)
        except Exception:
            return ""
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
            return ""
        if parsed.scheme.lower() == "http":
            # QWeather credentials must not be sent to a remote plaintext
            # endpoint. Keep local development proxies usable without
            # weakening the default for arbitrary hosts.
            hostname = (parsed.hostname or "").strip("[]").lower()
            if hostname not in {"localhost", "127.0.0.1", "::1"}:
                return ""
        # Credentials in an API Host are never useful and could accidentally
        # be written to logs or persisted with the cache.
        if parsed.username or parsed.password:
            return ""
        path = (parsed.path or "").rstrip("/")
        for suffix in (
            "/geo/v2/city/lookup",
            "/geo/v2/city",
            "/geo/v2",
            "/weatheralert/v1/current",
            "/weatheralert/v1",
            "/v7/weather/now",
            "/v7/weather",
            "/v7/warning",
            "/v7",
        ):
            if path.lower().endswith(suffix):
                path = path[: -len(suffix)].rstrip("/")
                break
        return f"{parsed.scheme.lower()}://{parsed.netloc}{path}".rstrip("/")

    def _qweather_alert_api_host(self) -> str:
        return self._normalize_qweather_api_host(
            getattr(self, "weather_api_host", "")
            or getattr(self, "qweather_api_host", "")
            or getattr(self, "weather_alert_api_host", "")
        )

    def _qweather_alert_token(self) -> str:
        # weather_alert_token is the documented name. Keep the aliases for
        # older local configs, but never persist or log the resulting
        # credential.
        raw = (
            getattr(self, "weather_token", "")
            or getattr(self, "qweather_token", "")
            or getattr(self, "weather_alert_token", "")
            or getattr(self, "weather_alert_jwt", "")
            or getattr(self, "weather_alert_api_key", "")
        )
        token = str(raw or "").strip()
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        return token

    def _qweather_alert_credential_kind(self, credential: Any = None) -> str:
        """Choose the QWeather auth header without exposing the credential.

        The current Weather Alert API accepts either a JWT or an API Key, but
        the two schemes must never be sent together.  Normal configuration
        uses the unambiguous JWT shape (three dot-separated segments) and
        treats other non-empty values as API Keys.
        """

        value = self._qweather_alert_token() if credential is None else str(credential or "").strip()
        if value.lower().startswith("bearer "):
            value = value[7:].strip()
        if not value:
            return ""
        segments = value.split(".")
        if len(segments) == 3 and all(segment.strip() for segment in segments):
            return "jwt"
        return "api_key"

    @staticmethod
    def _qweather_alert_coordinate(value: Any, *, minimum: float, maximum: float) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(number) or number < minimum or number > maximum:
            return None
        return number

    def _qweather_configured_location(self) -> str:
        raw = _single_line(runtime_persona_setting(self, "weather_location", ""), 180)
        if not raw:
            return ""
        return unicodedata.normalize("NFKC", raw).strip().replace("，", ",")

    def _qweather_coordinates_from_text(self, value: Any) -> tuple[float, float] | None:
        """Parse QWeather's documented ``longitude,latitude`` format."""

        text = unicodedata.normalize("NFKC", str(value or "")).strip().replace("，", ",")
        pieces = [part.strip() for part in text.split(",")]
        if len(pieces) != 2:
            return None
        longitude = self._qweather_alert_coordinate(pieces[0], minimum=-180, maximum=180)
        latitude = self._qweather_alert_coordinate(pieces[1], minimum=-90, maximum=90)
        if latitude is None or longitude is None or (latitude == 0 and longitude == 0):
            return None
        return latitude, longitude

    @staticmethod
    def _qweather_is_location_id(value: Any) -> bool:
        # Current QWeather LocationIDs are numeric (for example 101010100).
        # Keeping the range bounded avoids treating a short numeric city name
        # or an arbitrary long identifier as a provider ID.
        return bool(re.fullmatch(r"\d{7,18}", str(value or "").strip()))

    def _qweather_legacy_weather_location(self) -> tuple[float, float] | None:
        latitude = self._qweather_alert_coordinate(
            runtime_persona_setting(self, "weather_lat", 0),
            minimum=-90,
            maximum=90,
        )
        longitude = self._qweather_alert_coordinate(
            runtime_persona_setting(self, "weather_lon", 0),
            minimum=-180,
            maximum=180,
        )
        if latitude is None or longitude is None or (latitude == 0 and longitude == 0):
            return None
        return latitude, longitude

    def _qweather_location_identity(self) -> str:
        configured = self._qweather_configured_location()
        if configured:
            coordinates = self._qweather_coordinates_from_text(configured)
            if coordinates is not None:
                latitude, longitude = coordinates
                return "coordinates:" + ",".join(
                    (
                        self._qweather_alert_coordinate_text(longitude),
                        self._qweather_alert_coordinate_text(latitude),
                    )
                )
            return "configured:" + configured.casefold()
        legacy = self._qweather_legacy_weather_location()
        if legacy is None:
            return ""
        latitude, longitude = legacy
        return "legacy:" + ",".join(
            (
                self._qweather_alert_coordinate_text(longitude),
                self._qweather_alert_coordinate_text(latitude),
            )
        )

    def _qweather_location_cache_key(self) -> str:
        identity = self._qweather_location_identity()
        host = self._qweather_alert_api_host()
        if not identity or not host:
            return ""
        raw = f"{host}|{identity}"
        return hashlib.sha256(raw.encode("utf-8", "ignore")).hexdigest()[:24]

    def _qweather_cached_location(self, *, allow_stale: bool = True) -> dict[str, Any]:
        data = getattr(self, "data", None)
        cached = data.get("qweather_location") if isinstance(data, dict) else None
        if not isinstance(cached, dict) or cached.get("config_key") != self._qweather_location_cache_key():
            return {}
        location_id = _single_line(cached.get("location_id"), 40)
        latitude = self._qweather_alert_coordinate(cached.get("lat"), minimum=-90, maximum=90)
        longitude = self._qweather_alert_coordinate(cached.get("lon"), minimum=-180, maximum=180)
        if latitude is None or longitude is None or (latitude == 0 and longitude == 0):
            return {}
        fetched_ts = _safe_float(cached.get("fetched_ts"), 0)
        if not allow_stale and (fetched_ts <= 0 or _now_ts() - fetched_ts > 30 * 24 * 60 * 60):
            return {}
        return {
            "version": 1,
            "config_key": str(cached.get("config_key") or ""),
            "location_id": location_id,
            "lat": latitude,
            "lon": longitude,
            "label": _single_line(cached.get("label"), 120),
            "fetched_ts": fetched_ts,
        }

    def _qweather_direct_location(self) -> dict[str, Any]:
        configured = self._qweather_configured_location()
        config_key = self._qweather_location_cache_key()
        if configured:
            coordinates = self._qweather_coordinates_from_text(configured)
            if coordinates is not None:
                latitude, longitude = coordinates
                label = ",".join(
                    (
                        self._qweather_alert_coordinate_text(longitude),
                        self._qweather_alert_coordinate_text(latitude),
                    )
                )
                return {
                    "version": 1,
                    "config_key": config_key,
                    "location_id": "",
                    "lat": latitude,
                    "lon": longitude,
                    "label": label,
                    "fetched_ts": _now_ts(),
                }
            if self._qweather_is_location_id(configured):
                return {
                    "version": 1,
                    "config_key": config_key,
                    "location_id": configured,
                    "lat": None,
                    "lon": None,
                    "label": configured,
                    "fetched_ts": 0,
                }
            return {}
        legacy = self._qweather_legacy_weather_location()
        if legacy is None:
            return {}
        latitude, longitude = legacy
        label = ",".join(
            (
                self._qweather_alert_coordinate_text(longitude),
                self._qweather_alert_coordinate_text(latitude),
            )
        )
        return {
            "version": 1,
            "config_key": config_key,
            "location_id": "",
            "lat": latitude,
            "lon": longitude,
            "label": label,
            "fetched_ts": _now_ts(),
        }

    def _qweather_location_snapshot(self) -> dict[str, Any]:
        cached = self._qweather_cached_location()
        if cached:
            return cached
        return self._qweather_direct_location()

    def _build_qweather_geo_lookup_url(self, query: Any = None) -> str:
        host = self._qweather_alert_api_host()
        configured = self._qweather_configured_location() if query is None else _single_line(query, 180)
        if not host or not configured:
            return ""
        params: dict[str, str | int] = {"location": configured, "number": 1, "lang": "zh"}
        if not self._qweather_is_location_id(configured) and self._qweather_coordinates_from_text(configured) is None:
            pieces = [part.strip() for part in configured.split(",")]
            if len(pieces) == 2 and all(pieces):
                params["location"] = pieces[0]
                params["adm"] = pieces[1]
        return host + "/geo/v2/city/lookup?" + urlencode(params)

    def _parse_qweather_location_payload(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict) or str(payload.get("code") or "") != "200":
            return {}
        locations = payload.get("location")
        item = locations[0] if isinstance(locations, list) and locations else None
        if not isinstance(item, dict):
            return {}
        latitude = self._qweather_alert_coordinate(item.get("lat"), minimum=-90, maximum=90)
        longitude = self._qweather_alert_coordinate(item.get("lon"), minimum=-180, maximum=180)
        if latitude is None or longitude is None or (latitude == 0 and longitude == 0):
            return {}
        labels: list[str] = []
        for key in ("name", "adm2", "adm1"):
            value = _single_line(item.get(key), 60)
            if value and value not in labels:
                labels.append(value)
        return {
            "location_id": _single_line(item.get("id"), 40),
            "lat": latitude,
            "lon": longitude,
            "label": "，".join(labels) or self._qweather_configured_location(),
        }

    async def _fetch_qweather_location_lookup(self, query: str) -> dict[str, Any]:
        url = self._build_qweather_geo_lookup_url(query)
        token = self._qweather_alert_token()
        if not url or not token:
            return {}
        try:
            import aiohttp

            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(
                    url,
                    headers=self._qweather_alert_headers(),
                    allow_redirects=False,
                ) as response:
                    if response.status != 200:
                        logger.debug("和风天气地点解析请求失败: %s", response.status)
                        return {}
                    try:
                        payload = await response.json()
                    except TypeError:
                        payload = await response.json(content_type=None)
        except asyncio.TimeoutError:
            logger.warning("和风天气地点解析请求超时")
            return {}
        except Exception as exc:
            logger.debug("和风天气地点解析失败: %s", _single_line(exc, 160))
            return {}
        return self._parse_qweather_location_payload(payload)

    async def _store_qweather_location(
        self,
        resolved: dict[str, Any],
        *,
        expected_config_key: str = "",
    ) -> dict[str, Any]:
        current_config_key = self._qweather_location_cache_key()
        config_key = str(expected_config_key or current_config_key)
        latitude = self._qweather_alert_coordinate(resolved.get("lat"), minimum=-90, maximum=90)
        longitude = self._qweather_alert_coordinate(resolved.get("lon"), minimum=-180, maximum=180)
        if (
            not config_key
            or (expected_config_key and current_config_key != expected_config_key)
            or latitude is None
            or longitude is None
            or (latitude == 0 and longitude == 0)
        ):
            return {}
        record = {
            "version": 1,
            "config_key": config_key,
            "location_id": _single_line(resolved.get("location_id"), 40),
            "lat": latitude,
            "lon": longitude,
            "label": _single_line(resolved.get("label"), 120),
            "fetched_ts": _now_ts(),
        }

        stored = False

        def store() -> None:
            nonlocal stored
            data = getattr(self, "data", None)
            # Config can change while waiting for the shared data lock. Keep
            # the record tied to the request that produced it, never to a new
            # location selected while the request was in flight.
            if not isinstance(data, dict) or self._qweather_location_cache_key() != config_key:
                return
            data["qweather_location"] = deepcopy(record)
            stored = True
            saver = getattr(self, "_save_data_sync", None)
            if callable(saver):
                try:
                    saver(sections={"qweather_location"})
                except Exception as exc:
                    logger.debug("保存和风天气地点缓存失败: %s", _single_line(exc, 160))

        data_lock = getattr(self, "_data_lock", None)
        if isinstance(data_lock, asyncio.Lock):
            async with data_lock:
                store()
        else:
            store()
        return record if stored else {}

    async def _resolve_qweather_location(self) -> dict[str, Any]:
        """Resolve one shared location for current weather and official alerts."""

        config_key_before_read = self._qweather_location_cache_key()
        cached = self._qweather_cached_location(allow_stale=False)
        if cached and self._qweather_location_cache_key() == config_key_before_read:
            return cached
        lock = getattr(self, "_qweather_location_resolve_lock", None)
        if not isinstance(lock, asyncio.Lock):
            lock = asyncio.Lock()
            self._qweather_location_resolve_lock = lock
        async with lock:
            # A few rapid page saves can race with one request. Retrying a
            # small bounded number keeps the latest location responsive while
            # still falling back safely under continuous configuration churn.
            for _attempt in range(3):
                expected_config_key = self._qweather_location_cache_key()
                cached = self._qweather_cached_location(allow_stale=False)
                if cached and self._qweather_location_cache_key() == expected_config_key:
                    return cached
                stale = self._qweather_cached_location(allow_stale=True)
                direct = self._qweather_direct_location()
                configured = self._qweather_configured_location()
                if direct and (
                    not configured or self._qweather_coordinates_from_text(configured) is not None
                ):
                    stored = await self._store_qweather_location(
                        direct,
                        expected_config_key=expected_config_key,
                    )
                    if stored:
                        return stored
                    if self._qweather_location_cache_key() != expected_config_key:
                        continue
                    return {}
                if configured:
                    looked_up = await self._fetch_qweather_location_lookup(configured)
                    if self._qweather_location_cache_key() != expected_config_key:
                        continue
                    if looked_up:
                        stored = await self._store_qweather_location(
                            looked_up,
                            expected_config_key=expected_config_key,
                        )
                        if stored:
                            return stored
                        if self._qweather_location_cache_key() != expected_config_key:
                            continue
                        return {}
                    if stale:
                        return stale
                    # A LocationID remains valid for ordinary weather even
                    # when GeoAPI is temporarily unavailable. Alerts wait for
                    # its coordinates instead of guessing a different place.
                    if direct and direct.get("location_id"):
                        return direct
                    return {}
                return {}
            return {}

    def _qweather_alert_location(self, resolved: Any = None) -> tuple[float, float] | None:
        """Return (latitude, longitude), preferring dedicated alert fields."""

        if isinstance(resolved, dict):
            latitude = self._qweather_alert_coordinate(resolved.get("lat"), minimum=-90, maximum=90)
            longitude = self._qweather_alert_coordinate(resolved.get("lon"), minimum=-180, maximum=180)
            if latitude is not None and longitude is not None and (latitude != 0 or longitude != 0):
                return latitude, longitude
        if self._qweather_configured_location():
            snapshot = self._qweather_location_snapshot()
            latitude = self._qweather_alert_coordinate(snapshot.get("lat"), minimum=-90, maximum=90)
            longitude = self._qweather_alert_coordinate(snapshot.get("lon"), minimum=-180, maximum=180)
            if latitude is None or longitude is None or (latitude == 0 and longitude == 0):
                return None
            return latitude, longitude

        lat_value = getattr(self, "weather_alert_lat", None)
        lon_value = getattr(self, "weather_alert_lon", None)
        if lat_value in (None, "") or lon_value in (None, ""):
            lat_value = runtime_persona_setting(self, "weather_lat", lat_value)
            lon_value = runtime_persona_setting(self, "weather_lon", lon_value)
        lat = self._qweather_alert_coordinate(lat_value, minimum=-90, maximum=90)
        lon = self._qweather_alert_coordinate(lon_value, minimum=-180, maximum=180)
        if lat is None or lon is None or (lat == 0 and lon == 0):
            # A few callers keep a location in a single "lat,lon" setting.
            raw_location = str(getattr(self, "weather_alert_location", "") or "").strip()
            pieces = [part.strip() for part in raw_location.split(",")]
            if len(pieces) == 2:
                lat = self._qweather_alert_coordinate(pieces[0], minimum=-90, maximum=90)
                lon = self._qweather_alert_coordinate(pieces[1], minimum=-180, maximum=180)
        if lat is None or lon is None or (lat == 0 and lon == 0):
            return None
        return lat, lon

    @staticmethod
    def _qweather_alert_coordinate_text(value: float, *, decimals: int = 6) -> str:
        try:
            precision = max(0, min(6, int(decimals)))
        except (TypeError, ValueError):
            precision = 6
        text = f"{value:.{precision}f}".rstrip("0").rstrip(".")
        return "0" if text in {"", "-0"} else text

    def _build_qweather_alert_url(self, resolved: Any = None) -> str:
        host = self._qweather_alert_api_host()
        location = self._qweather_alert_location(resolved)
        if not host or location is None:
            return ""
        latitude, longitude = location
        # This is the current API Host route. The parser below also accepts
        # the legacy /v7/warning/now response for compatible gateways.
        path = "/weatheralert/v1/current/" + "/".join(
            (
                # The current endpoint documents a maximum of two decimal
                # places for path coordinates; keep the cache key precise,
                # but send the provider-compatible representation.
                self._qweather_alert_coordinate_text(latitude, decimals=2),
                self._qweather_alert_coordinate_text(longitude, decimals=2),
            )
        )
        return host + path + "?" + urlencode({"localTime": "true", "lang": "zh"})

    def _qweather_alert_headers(self) -> dict[str, str]:
        token = self._qweather_alert_token()
        if self._qweather_alert_credential_kind(token) == "api_key":
            return {
                "X-QW-Api-Key": token,
                "Accept": "application/json",
            }
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }

    @staticmethod
    def _normalize_weather_alert(raw: Any, *, source: str = "qweather") -> dict[str, Any]:
        """Normalize current and legacy QWeather alert objects."""

        if not isinstance(raw, dict):
            return {}
        message_type_raw = raw.get("messageType")
        message_type_code = ""
        supersedes: list[str] = []
        if isinstance(message_type_raw, dict):
            message_type_code = _qweather_alert_text(
                _qweather_alert_first(message_type_raw, "code", "name", "type"), 64
            )
            supersedes = _qweather_alert_string_list(message_type_raw.get("supersedes"), limit=32)
        else:
            message_type_code = _qweather_alert_text(message_type_raw, 64)
        if not supersedes:
            supersedes = _qweather_alert_string_list(raw.get("supersedes"), limit=32)
        event_type_raw = raw.get("eventType")
        event_name = ""
        event_code = ""
        if isinstance(event_type_raw, dict):
            event_name = _qweather_alert_text(_qweather_alert_first(event_type_raw, "name", "title"), 80)
            event_code = _qweather_alert_text(event_type_raw.get("code"), 64)
        else:
            event_name = _qweather_alert_text(event_type_raw, 80)
        if not event_name:
            event_name = _qweather_alert_text(
                _qweather_alert_first(raw, "typeName", "eventName", "type", "event"), 80
            )
        if not event_code:
            event_code = _qweather_alert_text(_qweather_alert_first(raw, "typeCode", "eventCode"), 64)
        color_name, color_code = _qweather_alert_color(
            raw.get("color", _qweather_alert_first(raw, "level", "warningLevel", "colorName"))
        )
        alert_id = _qweather_alert_text(
            _qweather_alert_first(raw, "id", "alertId", "warningId", "identifier"), 160
        )
        issued_time = _qweather_alert_text(
            _qweather_alert_first(raw, "issuedTime", "pubTime", "publishTime", "issuedAt"), 80
        )
        effective_time = _qweather_alert_text(
            _qweather_alert_first(raw, "effectiveTime", "effective", "startTime", "validFrom"), 80
        )
        onset_time = _qweather_alert_text(
            _qweather_alert_first(raw, "onsetTime", "onset", "beginTime"), 80
        )
        expire_time = _qweather_alert_text(
            _qweather_alert_first(raw, "expireTime", "expires", "expiresTime", "endTime", "ends"), 80
        )
        headline = _qweather_alert_text(
            _qweather_alert_first(raw, "headline", "title", "summary"), 240
        )
        description = _qweather_alert_text(
            _qweather_alert_first(raw, "description", "detail", "text"), 2000
        )
        sender = _qweather_alert_text(
            _qweather_alert_first(raw, "senderName", "sender", "publisher", "source"), 160
        )
        severity = _qweather_alert_text(raw.get("severity"), 32)
        status = _qweather_alert_text(raw.get("status"), 32)
        lower_message_type = message_type_code.lower()
        is_cancelled = any(token in lower_message_type for token in ("cancel", "撤销", "解除"))
        response_types = _qweather_alert_string_list(
            _qweather_alert_first(raw, "responseTypes", "response_types", "instructionTypes"),
            limit=16,
        )
        instruction = _qweather_alert_text(
            _qweather_alert_first(raw, "instruction", "instructions", "advice"), 1200
        )
        criteria = _qweather_alert_text(raw.get("criteria"), 600)
        fingerprint_source = "|".join(
            (
                alert_id,
                event_code or event_name,
                headline,
                sender,
                issued_time,
                message_type_code,
                color_code,
                severity,
                effective_time,
                onset_time,
                expire_time,
                description,
                instruction,
                ",".join(supersedes),
            )
        )
        fingerprint = hashlib.sha256(fingerprint_source.encode("utf-8", "ignore")).hexdigest()[:24]
        return {
            "id": alert_id,
            "source": _qweather_alert_text(source, 32) or "qweather",
            "sender": sender,
            "issued_time": issued_time,
            "message_type": message_type_code,
            "supersedes": supersedes,
            "event": event_name,
            "event_code": event_code,
            "urgency": _qweather_alert_text(raw.get("urgency"), 32),
            "severity": severity,
            "certainty": _qweather_alert_text(raw.get("certainty"), 32),
            "color": color_name,
            "color_code": color_code,
            "effective_time": effective_time,
            "onset_time": onset_time,
            "expire_time": expire_time,
            "headline": headline,
            "description": description,
            "criteria": criteria,
            "response_types": response_types,
            "instruction": instruction,
            "status": status,
            "is_cancelled": is_cancelled,
            "fingerprint": fingerprint,
        }

    @classmethod
    def _parse_qweather_alert_payload(cls, payload: Any) -> dict[str, Any]:
        """Parse a provider response, retaining all normalized alerts."""

        if not isinstance(payload, dict):
            return {"ok": False, "alerts": [], "error": "invalid_payload"}
        provider_code = str(payload.get("code") or "").strip()
        if provider_code and provider_code not in {"200", "0"}:
            return {
                "ok": False,
                "alerts": [],
                "error": "provider_code_" + _qweather_alert_text(provider_code, 32),
            }
        raw_alerts = payload.get("alerts")
        if raw_alerts is None:
            raw_alerts = payload.get("warning")
        if raw_alerts is None and isinstance(payload.get("data"), dict):
            raw_alerts = payload["data"].get("alerts", payload["data"].get("warning"))
        if not isinstance(raw_alerts, list):
            raw_alerts = []
        alerts = [cls._normalize_weather_alert(item) for item in raw_alerts]
        alerts = [item for item in alerts if item]
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        if not metadata and isinstance(payload.get("data"), dict) and isinstance(payload["data"].get("metadata"), dict):
            metadata = payload["data"]["metadata"]
        update_time = _qweather_alert_text(
            _qweather_alert_first(payload, "updateTime", "updatedAt")
            or _qweather_alert_first(metadata, "updateTime", "updatedAt"),
            80,
        )
        tag = _qweather_alert_text(
            _qweather_alert_first(metadata, "tag") or payload.get("tag"),
            160,
        )
        attributions = _qweather_alert_string_list(
            metadata.get("attributions") or payload.get("attributions"),
            limit=4,
            item_limit=320,
        )
        zero_result = bool(
            metadata.get("zeroResult")
            or payload.get("zeroResult")
            or not alerts
        )
        return {
            "ok": True,
            "alerts": alerts,
            "zero_result": zero_result,
            "provider_update_time": update_time,
            "provider_tag": tag,
            "provider_attributions": attributions,
        }

    @classmethod
    def _normalize_qweather_alert_payload(cls, payload: Any) -> list[dict[str, Any]]:
        """Compatibility shorthand for callers that only need the list."""

        result = cls._parse_qweather_alert_payload(payload)
        return result.get("alerts", []) if result.get("ok") else []

    @classmethod
    def _dedupe_weather_alerts(cls, alerts: Any, *, limit: int = 64) -> list[dict[str, Any]]:
        if not isinstance(alerts, list):
            return []
        by_identity: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        for raw in alerts:
            item = raw if isinstance(raw, dict) and "fingerprint" in raw else cls._normalize_weather_alert(raw)
            if not item:
                continue
            identity = str(item.get("id") or item.get("fingerprint") or "").strip()
            if not identity:
                continue
            if identity not in by_identity:
                order.append(identity)
                by_identity[identity] = item
                continue
            previous = by_identity[identity]
            # Prefer the newer revision, then the richer description. This
            # keeps an update from being hidden by a repeated old object.
            previous_issued = str(previous.get("issued_time") or "")
            current_issued = str(item.get("issued_time") or "")
            previous_richness = len(str(previous.get("description") or "")) + len(
                str(previous.get("instruction") or "")
            )
            current_richness = len(str(item.get("description") or "")) + len(
                str(item.get("instruction") or "")
            )
            if current_issued > previous_issued or (
                current_issued == previous_issued and current_richness > previous_richness
            ):
                by_identity[identity] = item
        return [by_identity[key] for key in order[: max(1, int(limit))]]

    @classmethod
    def _filter_weather_alerts(
        cls,
        alerts: Any,
        min_severity: Any = "blue",
    ) -> list[dict[str, Any]]:
        """Filter a normalized list without mutating the full cached list."""

        if not isinstance(alerts, list):
            return []
        threshold_text = _qweather_alert_text(min_severity, 32).strip().lower()
        if threshold_text in {"", "all", "any", "全部", "全部等级"}:
            return [item for item in alerts if isinstance(item, dict)]
        threshold = _qweather_alert_rank(threshold_text)
        result: list[dict[str, Any]] = []
        for item in alerts:
            if not isinstance(item, dict):
                continue
            color = item.get("color_code") or item.get("color") or item.get("level")
            if color:
                # 颜色等级为准（蓝<黄<橙<红）。不能与 severity 取 max——qweather 的
                # severity 是国际档位（minor/moderate/severe/extreme），与国内颜色
                # 错位一档（黄色预警 severity=moderate），max 会让黄色顶穿 orange 阈值
                # （实测 08-14 黄色暴雨/雷电被误发）。颜色缺失（非中文/全球预警源）才
                # 退回 severity。
                rank = _qweather_alert_rank(color)
            else:
                rank = _qweather_alert_rank(item.get("severity"))
            if rank >= threshold:
                result.append(item)
        return result

    @classmethod
    def _weather_alert_identity(cls, alert: Any) -> str:
        if not isinstance(alert, dict):
            return ""
        return str(alert.get("id") or alert.get("fingerprint") or "").strip()

    @classmethod
    def _weather_alert_terminal_identity(cls, alert: Any) -> str:
        """Normalize resolved/cancelled variants to the warning they terminate."""
        if not isinstance(alert, dict):
            return ""
        supersedes = alert.get("supersedes")
        if isinstance(supersedes, list):
            for value in supersedes:
                identity = str(value or "").strip()
                if identity:
                    return identity
        return cls._weather_alert_identity(alert)

    @classmethod
    def _merge_weather_alert_cache(
        cls,
        cached: Any,
        alerts: Any,
        *,
        fetched_ts: float | None = None,
        config_key: str = "",
        provider_update_time: str = "",
        provider_tag: str = "",
        provider_attributions: Any = None,
        zero_result: bool = False,
    ) -> dict[str, Any]:
        """Build a bounded cache and expose changes for a future caller.

        ``new_alert_ids`` and ``resolved_alert_ids`` are metadata only; this
        helper does not send anything and the full ``alerts`` list is retained.
        """

        old_alerts = cached.get("alerts", []) if isinstance(cached, dict) else []
        old_items = cls._dedupe_weather_alerts(old_alerts)
        new_items = cls._dedupe_weather_alerts(alerts)
        old_by_id = {cls._weather_alert_identity(item): item for item in old_items}
        new_by_id = {cls._weather_alert_identity(item): item for item in new_items}
        old_ids = set(old_by_id)
        new_ids = set(new_by_id)
        updated_ids = {
            identity
            for identity in old_ids & new_ids
            if old_by_id[identity].get("fingerprint") != new_by_id[identity].get("fingerprint")
        }
        now = _safe_float(fetched_ts, _now_ts())
        if provider_attributions is None and isinstance(cached, dict):
            provider_attributions = cached.get("attributions")
        normalized_attributions = _qweather_alert_string_list(
            provider_attributions,
            limit=4,
            item_limit=320,
        )
        return {
            "version": 1,
            "source": "qweather",
            "config_key": _qweather_alert_text(config_key, 96),
            "alerts": new_items,
            "fetched_ts": now,
            "last_success_ts": now,
            "last_attempt_ts": now,
            "stale": False,
            "error": "",
            "zero_result": bool(zero_result or not new_items),
            "provider_update_time": _qweather_alert_text(provider_update_time, 80),
            "provider_tag": _qweather_alert_text(provider_tag, 160),
            "attributions": normalized_attributions,
            "new_alert_ids": sorted(identity for identity in new_ids - old_ids if identity),
            "updated_alert_ids": sorted(identity for identity in updated_ids if identity),
            "resolved_alert_ids": sorted(identity for identity in old_ids - new_ids if identity),
        }

    def _weather_alert_config_key(self) -> str:
        configured = self._qweather_configured_location()
        if configured:
            # Hash the configured identity rather than a resolved coordinate
            # so a city change invalidates alerts before the next GeoAPI call.
            location_text = self._qweather_location_identity()
        else:
            location = self._qweather_alert_location()
            if location is None:
                location_text = ""
            else:
                location_text = ",".join(self._qweather_alert_coordinate_text(value) for value in location)
        # Token changes do not alter the data location. Omitting it prevents a
        # credential-derived value from being persisted in the cache key.
        raw = "|".join(
            (
                self._qweather_alert_api_host(),
                location_text,
                self._weather_window_timezone(),
            )
        )
        return hashlib.sha256(raw.encode("utf-8", "ignore")).hexdigest()[:24] if raw else ""

    def _weather_alert_cache_fresh(self, cache: Any, *, now: float | None = None) -> bool:
        if not isinstance(cache, dict) or cache.get("source") != "qweather":
            return False
        if cache.get("config_key") != self._weather_alert_config_key():
            return False
        current = _safe_float(now, _now_ts())
        # Failed requests use a short backoff based on last_attempt_ts; a
        # successful cache uses fetched_ts and remains available while stale.
        stamp = _safe_float(cache.get("last_attempt_ts"), 0)
        if not cache.get("stale"):
            stamp = _safe_float(cache.get("fetched_ts"), stamp)
        refresh_minutes = _safe_int(
            runtime_persona_setting(self, "weather_alert_refresh_minutes", 10),
            10,
            5,
            60,
        )
        return stamp > 0 and current - stamp < refresh_minutes * 60

    async def _fetch_qweather_alerts(self) -> dict[str, Any]:
        """Fetch and parse current alerts from QWeather's API Host route."""

        if not bool(runtime_persona_setting(self, "enable_weather_alerts", False)):
            return {"ok": False, "disabled": True, "alerts": [], "error": "disabled", "source": "qweather"}
        host = self._qweather_alert_api_host()
        token = self._qweather_alert_token()
        resolved = None
        if self._qweather_configured_location():
            resolved = await self._resolve_qweather_location()
        url = self._build_qweather_alert_url(resolved)
        if not host or not token or not url:
            return {
                "ok": False,
                "configured": False,
                "alerts": [],
                "error": "not_configured",
                "source": "qweather",
            }
        try:
            import aiohttp

            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(
                    url,
                    headers=self._qweather_alert_headers(),
                    allow_redirects=False,
                ) as response:
                    if response.status == 204:
                        return {
                            "ok": True,
                            "alerts": [],
                            "zero_result": True,
                            "provider_update_time": "",
                            "provider_tag": "",
                            "source": "qweather",
                        }
                    if response.status != 200:
                        logger.debug("和风天气预警请求失败: %s", response.status)
                        return {
                            "ok": False,
                            "alerts": [],
                            "error": f"http_{response.status}",
                            "source": "qweather",
                        }
                    try:
                        payload = await response.json()
                    except TypeError:
                        # Some aiohttp-compatible test doubles and gateways do
                        # not expose a content type; allow the documented JSON.
                        payload = await response.json(content_type=None)
        except asyncio.TimeoutError:
            logger.warning("和风天气预警请求超时")
            return {"ok": False, "alerts": [], "error": "timeout", "source": "qweather"}
        except Exception as exc:
            logger.debug("和风天气预警获取失败: %s", _single_line(exc, 160))
            return {"ok": False, "alerts": [], "error": "request_failed", "source": "qweather"}
        parsed = self._parse_qweather_alert_payload(payload)
        parsed["source"] = "qweather"
        return parsed

    # Alias kept intentionally small so integrations can use the generic name.
    async def _fetch_weather_alerts(self) -> dict[str, Any]:
        return await self._fetch_qweather_alerts()

    async def _ensure_weather_alert_context(self, force: bool = False) -> dict[str, Any]:
        """Refresh the structured alert cache without dispatching messages."""

        if not bool(runtime_persona_setting(self, "enable_weather_alerts", False)):
            return {
                "version": 1,
                "source": "disabled",
                "alerts": [],
                "fetched_ts": 0,
                "stale": False,
                "error": "disabled",
            }
        data = getattr(self, "data", None)
        cached = data.get("weather_alerts", {}) if isinstance(data, dict) else {}
        if not force and self._weather_alert_cache_fresh(cached):
            result = deepcopy(cached)
            result["refreshed"] = False
            return result
        attempt_ts = _now_ts()
        current_config_key = self._weather_alert_config_key()
        fetched = await self._fetch_qweather_alerts()
        if fetched.get("ok"):
            result = self._merge_weather_alert_cache(
                cached,
                fetched.get("alerts", []),
                fetched_ts=attempt_ts,
                config_key=current_config_key,
                provider_update_time=fetched.get("provider_update_time", ""),
                provider_tag=fetched.get("provider_tag", ""),
                provider_attributions=fetched.get("provider_attributions"),
                zero_result=bool(fetched.get("zero_result")),
            )
            result["refreshed"] = True
        else:
            # Keep the last successful alerts during a provider outage. This
            # prevents a transient network error from erasing useful context,
            # but never carries alerts across a location/host change.
            same_config = isinstance(cached, dict) and cached.get("config_key") == current_config_key
            fetch_error = _qweather_alert_text(fetched.get("error"), 80)
            retain_after_failure = fetch_error not in {"not_configured", "disabled"}
            if same_config and cached.get("alerts") and retain_after_failure:
                result = deepcopy(cached)
                result["stale"] = True
                result["last_attempt_ts"] = attempt_ts
                result["error"] = fetch_error or "request_failed"
            else:
                result = {
                    "version": 1,
                    "source": "qweather",
                    "config_key": current_config_key,
                    "alerts": [],
                    "fetched_ts": 0,
                    "last_success_ts": 0,
                    "last_attempt_ts": attempt_ts,
                    "stale": bool(isinstance(cached, dict) and cached.get("alerts")),
                    "error": fetch_error or "request_failed",
                    "zero_result": False,
                    "attributions": [],
                    "new_alert_ids": [],
                    "updated_alert_ids": [],
                    "resolved_alert_ids": [],
                }
            result["refreshed"] = False
        if isinstance(data, dict):
            stored = deepcopy(result)
            stored.pop("refreshed", None)
            data["weather_alerts"] = stored
            saver = getattr(self, "_save_data_sync", None)
            if callable(saver):
                try:
                    saver(sections={"weather_alerts"})
                except Exception as exc:
                    logger.debug("保存天气预警缓存失败: %s", _single_line(exc, 160))
        return result

    def _weather_alert_time_ts(self, value: Any) -> float:
        """Convert a provider time to the plugin's local epoch when possible."""

        if isinstance(value, (int, float)) and not isinstance(value, bool):
            try:
                parsed = float(value)
                return parsed if math.isfinite(parsed) and parsed > 0 else 0.0
            except (TypeError, ValueError):
                return 0.0
        text = _qweather_alert_text(value, 96)
        if not text:
            return 0.0
        try:
            parsed = float(text)
            if math.isfinite(parsed) and parsed > 0:
                return parsed
        except (TypeError, ValueError):
            pass
        normalized = text.strip()
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        try:
            current = datetime.fromisoformat(normalized)
        except (TypeError, ValueError):
            return 0.0
        if current.tzinfo is None:
            now_getter = getattr(self, "_environment_now", None)
            try:
                zone = now_getter().tzinfo if callable(now_getter) else None
            except Exception:
                zone = None
            current = current.replace(tzinfo=zone or timezone.utc)
        try:
            return float(current.timestamp())
        except (TypeError, ValueError, OSError):
            return 0.0

    def _weather_alert_is_expired(self, alert: Any, *, now: float | None = None) -> bool:
        if not isinstance(alert, dict):
            return True
        expire_ts = self._weather_alert_time_ts(alert.get("expire_time"))
        return expire_ts > 0 and expire_ts <= (_safe_float(now, _now_ts()))

    @staticmethod
    def _weather_alert_is_cancelled(alert: Any) -> bool:
        if not isinstance(alert, dict):
            return False
        if bool(alert.get("is_cancelled")):
            return True
        text = " ".join(
            _single_line(alert.get(key), 60).lower()
            for key in ("message_type", "status", "headline", "event")
        )
        return any(token in text for token in ("cancel", "撤销", "解除", "取消"))

    def _active_weather_alerts(
        self,
        alerts: Any,
        *,
        now: float | None = None,
        include_cancelled: bool = False,
    ) -> list[dict[str, Any]]:
        current = _safe_float(now, _now_ts())
        result: list[dict[str, Any]] = []
        for alert in self._dedupe_weather_alerts(alerts):
            if not isinstance(alert, dict):
                continue
            if not include_cancelled and self._weather_alert_is_cancelled(alert):
                continue
            if not include_cancelled and self._weather_alert_is_expired(alert, now=current):
                continue
            result.append(alert)
        return result

    def _weather_alert_owner_users(self) -> list[tuple[str, dict[str, Any]]]:
        users = self.data.get("users") if isinstance(getattr(self, "data", None), dict) else {}
        if not isinstance(users, dict):
            return []
        targets: list[tuple[str, dict[str, Any]]] = []
        for raw_user_id, user in users.items():
            user_id = str(raw_user_id or "").strip()
            if not user_id or not isinstance(user, dict) or not user.get("umo"):
                continue
            role_getter = getattr(self, "_private_user_role", None)
            try:
                role = role_getter(user, user_id) if callable(role_getter) else str(user.get("relationship_role") or "")
            except TypeError:
                role = role_getter(user) if callable(role_getter) else str(user.get("relationship_role") or "")
            except Exception:
                role = str(user.get("relationship_role") or "")
            if str(role or "").strip().lower() != "owner":
                continue
            enabled_getter = getattr(self, "_user_enabled_for_proactive", None)
            if callable(enabled_getter):
                try:
                    if not enabled_getter(user_id, user):
                        continue
                except Exception:
                    continue
            targets.append((user_id, user))
        return targets

    @staticmethod
    def _weather_alert_event_key(kind: Any, alert: Any) -> str:
        if not isinstance(alert, dict):
            return ""
        identity = _single_line(alert.get("id") or alert.get("fingerprint"), 180)
        fingerprint = _single_line(alert.get("fingerprint"), 80)
        return ":".join(part for part in (_single_line(kind, 20), identity, fingerprint) if part)

    def _weather_alert_context_for_event(
        self,
        alert: dict[str, Any],
        *,
        kind: str,
        now: float,
    ) -> dict[str, Any]:
        level = _qweather_alert_text(alert.get("color") or alert.get("color_code") or alert.get("severity"), 24)
        event = _qweather_alert_text(alert.get("event") or "天气", 48)
        title = _qweather_alert_text(alert.get("headline") or alert.get("description"), 220)
        instruction = _qweather_alert_text(alert.get("instruction"), 500)
        if kind in {"cancelled", "resolved"}:
            status = "已解除"
        elif kind == "expired" or self._weather_alert_is_expired(alert, now=now):
            status = "已过期或解除"
        elif kind == "updated":
            status = "刚更新"
        else:
            status = "刚发布"
        return {
            "kind": _single_line(kind, 20),
            "status": status,
            "alert": deepcopy(alert),
            "id": _single_line(alert.get("id") or alert.get("fingerprint"), 180),
            "level": level,
            "event": event,
            "title": title,
            "instruction": instruction,
            "captured_at": now,
        }

    def _weather_alert_event_candidates(
        self,
        previous_cache: Any,
        current_cache: dict[str, Any],
        *,
        now: float,
        initialized: bool,
    ) -> list[dict[str, Any]]:
        """Turn a cache transition into bounded, deduplicated pending events."""

        if not initialized or not isinstance(current_cache, dict):
            return []
        old_items = self._dedupe_weather_alerts(
            previous_cache.get("alerts", []) if isinstance(previous_cache, dict) else []
        )
        current_items = self._dedupe_weather_alerts(current_cache.get("alerts", []))
        old_by_id = {self._weather_alert_identity(item): item for item in old_items if self._weather_alert_identity(item)}
        current_by_id = {self._weather_alert_identity(item): item for item in current_items if self._weather_alert_identity(item)}
        new_ids = set(current_cache.get("new_alert_ids") or [])
        updated_ids = set(current_cache.get("updated_alert_ids") or [])
        resolved_ids = set(current_cache.get("resolved_alert_ids") or [])
        events: list[dict[str, Any]] = []
        threshold = runtime_persona_setting(self, "weather_alert_min_severity", "blue")

        # QWeather represents an updated warning as a new object whose
        # ``messageType.supersedes`` points at the previous warning ID.  Keep
        # that transition as one update event instead of emitting a new
        # warning followed by a misleading "old warning resolved" notice.
        superseded_by_current: dict[str, dict[str, Any]] = {}
        for current_item in current_items:
            supersedes = current_item.get("supersedes")
            if not isinstance(supersedes, list):
                continue
            for superseded_id in supersedes:
                identity = str(superseded_id or "").strip()
                if identity and identity in old_by_id:
                    superseded_by_current[identity] = current_item
        handled_current_ids: set[str] = set()

        def is_update(item: dict[str, Any]) -> bool:
            message_type = _qweather_alert_text(item.get("message_type"), 64).lower()
            if any(token in message_type for token in ("update", "amend", "extend", "replace", "续发", "变更")):
                return True
            return bool(item.get("supersedes"))

        def add(
            kind: str,
            item: dict[str, Any],
            *,
            policy_item: dict[str, Any] | None = None,
        ) -> None:
            if not isinstance(item, dict):
                return
            # Cancellation is useful even though it is not an active warning;
            # all other events must pass the configured minimum color/severity.
            if policy_item is None and self._weather_alert_is_cancelled(item):
                supersedes = item.get("supersedes") if isinstance(item.get("supersedes"), list) else []
                policy_item = next(
                    (old_by_id.get(str(value)) for value in supersedes if str(value) in old_by_id),
                    None,
                )
            if not self._filter_weather_alerts([policy_item or item], threshold):
                return
            key = self._weather_alert_event_key(kind, item)
            if not key or any(existing.get("event_key") == key for existing in events):
                return
            context = self._weather_alert_context_for_event(item, kind=kind, now=now)
            context["event_key"] = key
            events.append(context)

        for identity in sorted(new_ids):
            item = current_by_id.get(str(identity))
            if item:
                kind = "cancelled" if self._weather_alert_is_cancelled(item) else ("updated" if is_update(item) else "new")
                add(kind, item)
                handled_current_ids.add(str(identity))
        for identity in sorted(updated_ids):
            item = current_by_id.get(str(identity))
            if item:
                add("cancelled" if self._weather_alert_is_cancelled(item) else "updated", item)
                handled_current_ids.add(str(identity))
        for identity in sorted(resolved_ids):
            item = old_by_id.get(str(identity))
            if not item:
                continue
            if str(identity) in superseded_by_current:
                # The replacement event above carries the current facts and
                # is the only user-facing transition needed.
                continue
            # A provider can remove an item a few seconds before its explicit
            # expiry. Use the old object as the factual basis either way.
            add("expired" if self._weather_alert_is_expired(item, now=now) else "resolved", item)
        # A replacement may explicitly reference an older warning ID even if
        # the provider still returns both objects for one response.
        for item in current_items:
            current_identity = self._weather_alert_identity(item)
            if current_identity in handled_current_ids:
                continue
            supersedes = item.get("supersedes") if isinstance(item.get("supersedes"), list) else []
            if not supersedes:
                continue
            if self._weather_alert_is_cancelled(item):
                add("cancelled", item, policy_item=next(
                    (old_by_id.get(str(value)) for value in supersedes if str(value) in old_by_id),
                    None,
                ))
            elif any(str(value) in old_by_id for value in supersedes):
                add("updated", item)
        rank_getter = lambda value: _qweather_alert_rank(value.get("color_code") or value.get("severity"))
        events.sort(key=lambda value: rank_getter(value.get("alert", {})), reverse=True)
        return events[:12]

    def _weather_alert_event_captured_at(self, event: dict[str, Any]) -> float:
        """Return the best available observation time for a pending alert."""
        if not isinstance(event, dict):
            return 0.0
        captured_at = _safe_float(event.get("captured_at"), 0)
        if captured_at > 0:
            return captured_at
        alert = event.get("alert") if isinstance(event.get("alert"), dict) else {}
        return self._weather_alert_time_ts(
            alert.get("issued_time") or alert.get("effective_time") or alert.get("onset_time")
        )

    def _weather_alert_append_pending_events(self, events: list[dict[str, Any]]) -> None:
        state = self.data.setdefault("weather_alert_awareness", {})
        if not isinstance(state, dict):
            state = {}
            self.data["weather_alert_awareness"] = state
        pending = state.get("pending_events")
        if not isinstance(pending, list):
            pending = []
            state["pending_events"] = pending
        terminal_history = state.get("terminal_event_identities")
        if not isinstance(terminal_history, dict):
            terminal_history = {}
            state["terminal_event_identities"] = terminal_history
        terminal_cutoff = _now_ts() - 7 * 24 * 3600
        for identity, captured_at in list(terminal_history.items()):
            if _safe_float(captured_at, 0) < terminal_cutoff:
                terminal_history.pop(identity, None)
        known = {
            _single_line(item.get("event_key"), 260)
            for item in pending
            if isinstance(item, dict) and _single_line(item.get("event_key"), 260)
        }
        for event in events:
            if not isinstance(event, dict):
                continue
            key = _single_line(event.get("event_key"), 260)
            kind = _single_line(event.get("kind"), 20)
            alert = event.get("alert") if isinstance(event.get("alert"), dict) else {}
            terminal_identity = self._weather_alert_terminal_identity(alert) if kind in {
                "cancelled", "resolved", "expired"
            } else ""
            if terminal_identity and terminal_identity in terminal_history:
                continue
            if key and key not in known:
                pending.append(deepcopy(event))
                known.add(key)
        # A provider can emit both a resolved and an expired representation for
        # the same warning in one refresh. Keep only the first terminal event.
        terminal_seen: set[str] = set()
        compact_pending: list[dict[str, Any]] = []
        for item in pending:
            if not isinstance(item, dict):
                continue
            kind = _single_line(item.get("kind"), 20)
            alert = item.get("alert") if isinstance(item.get("alert"), dict) else {}
            terminal_identity = self._weather_alert_terminal_identity(alert) if kind in {
                "cancelled", "resolved", "expired"
            } else ""
            if terminal_identity:
                if terminal_identity in terminal_seen:
                    continue
                terminal_seen.add(terminal_identity)
            compact_pending.append(item)
        pending[:] = compact_pending
        # Weather changes are time-sensitive. An outage or a disabled daily
        # quota must not turn yesterday's alert into today's proactive message.
        cutoff = _now_ts() - 6 * 3600
        pending[:] = [
            item
            for item in pending
            if isinstance(item, dict)
            and (
                self._weather_alert_event_captured_at(item) <= 0
                or self._weather_alert_event_captured_at(item) >= cutoff
            )
        ]
        pending.sort(
            key=lambda item: _qweather_alert_rank(
                (
                    (item.get("alert") or {}).get("color_code")
                    or (item.get("alert") or {}).get("color")
                    or (item.get("alert") or {}).get("severity")
                )
                if isinstance(item.get("alert"), dict)
                else ""
            ),
            reverse=True,
        )
        del pending[20:]

    def _weather_alert_candidate_delay(self, event: dict[str, Any], *, now: float) -> tuple[float, float]:
        alert = event.get("alert") if isinstance(event.get("alert"), dict) else {}
        rank = _qweather_alert_rank(alert.get("color_code") or alert.get("color") or alert.get("severity"))
        if event.get("kind") in {"cancelled", "resolved", "expired"}:
            return now + random.uniform(1.0, 4.0) * 60.0, 90 * 60.0
        if rank >= 3:
            return now + random.uniform(20.0, 90.0), 30 * 60.0
        if rank >= 2:
            return now + random.uniform(1.0, 5.0) * 60.0, 60 * 60.0
        return now + random.uniform(5.0, 18.0) * 60.0, 3 * 3600.0

    def _queue_weather_alert_pending_events(self, *, now: float) -> int:
        offer = getattr(self, "_offer_proactive_candidate", None)
        if not callable(offer):
            return 0
        if callable(getattr(self, "_proactive_generation_disabled", None)):
            try:
                if self._proactive_generation_disabled():
                    return 0
            except Exception:
                pass
        state = self.data.get("weather_alert_awareness")
        if not isinstance(state, dict):
            return 0
        pending = state.get("pending_events")
        if not isinstance(pending, list) or not pending:
            return 0
        owners = self._weather_alert_owner_users()
        if not owners:
            return 0
        offered = 0
        remaining: list[dict[str, Any]] = []
        owner_ids = {user_id for user_id, _ in owners}
        for event in pending:
            if not isinstance(event, dict):
                continue
            alert = event.get("alert") if isinstance(event.get("alert"), dict) else {}
            captured_at = self._weather_alert_event_captured_at(event)
            if captured_at > 0 and now - captured_at > 6 * 3600:
                continue
            if alert and self._weather_alert_is_expired(alert, now=now) and event.get("kind") not in {"cancelled", "resolved", "expired"}:
                continue
            delivered = event.get("delivered_user_ids")
            if not isinstance(delivered, list):
                delivered = []
                event["delivered_user_ids"] = delivered
            for user_id, user in owners:
                if user_id in delivered:
                    continue
                scheduled, lifetime = self._weather_alert_candidate_delay(event, now=now)
                level = _qweather_alert_text(alert.get("color") or alert.get("color_code") or alert.get("severity"), 24)
                topic = _qweather_alert_text(
                    f"{level}{event.get('event') or '天气'}{event.get('status') or '有变化'}",
                    90,
                )
                alert_event_key = _single_line(event.get("event_key"), 260)
                candidate = {
                    "source": "weather_alert",
                    "reason": "weather_alert",
                    "action": "message",
                    "window_timezone": self._weather_window_timezone(),
                    "scheduled_ts": scheduled,
                    "window_start_at": scheduled,
                    "preferred_ts": scheduled,
                    "best_until_at": scheduled + min(lifetime, 60 * 60),
                    "expire_at": scheduled + lifetime,
                    "topic": topic,
                    "motive": "刚收到一条与当前位置有关的官方气象预警，想把最重要的一点及时告诉主要用户",
                    "score": max(72, min(100, 70 + _qweather_alert_rank(alert.get("color_code") or alert.get("severity")) * 10)),
                    "origin_event_id": (
                        "weather:" + hashlib.sha1(alert_event_key.encode("utf-8", errors="ignore")).hexdigest()[:24]
                        if alert_event_key
                        else ""
                    ),
                    "context_key": "planned_weather_alert_context",
                    "context": deepcopy(event),
                }
                if offer(user_id, user, candidate):
                    delivered.append(user_id)
                    if event.get("kind") in {"cancelled", "resolved", "expired"}:
                        terminal_history = state.setdefault("terminal_event_identities", {})
                        if isinstance(terminal_history, dict):
                            terminal_identity = self._weather_alert_terminal_identity(alert)
                            if terminal_identity:
                                terminal_history[terminal_identity] = now
                    offered += 1
                elif candidate.get("lifecycle_status") in {"skipped", "expired"}:
                    # Consume terminal candidates. Otherwise an old-timezone
                    # terminal alert would be rebuilt on every refresh.
                    delivered.append(user_id)
                    lifecycle_note = _single_line(candidate.get("lifecycle_note"), 180)
                    if lifecycle_note:
                        skip_reasons = event.setdefault("terminal_skip_reasons", {})
                        if isinstance(skip_reasons, dict):
                            skip_reasons[user_id] = lifecycle_note
                    if event.get("kind") in {"cancelled", "resolved", "expired"}:
                        terminal_history = state.setdefault("terminal_event_identities", {})
                        if isinstance(terminal_history, dict):
                            terminal_identity = self._weather_alert_terminal_identity(alert)
                            if terminal_identity:
                                terminal_history[terminal_identity] = now
            if owner_ids and owner_ids.issubset(set(delivered)):
                continue
            remaining.append(event)
        state["pending_events"] = remaining
        return offered

    async def _maybe_refresh_weather_alerts(self, *, force: bool = False) -> dict[str, Any]:
        """Refresh QWeather alerts and enqueue owner-only, deduplicated notices."""

        if not bool(runtime_persona_setting(self, "enable_weather_context", True)) or not bool(runtime_persona_setting(self, "enable_weather_alerts", False)):
            return {}
        lock = getattr(self, "_weather_alert_refresh_lock", None)
        if not isinstance(lock, asyncio.Lock):
            lock = asyncio.Lock()
            self._weather_alert_refresh_lock = lock
        async with lock:
            now = _now_ts()
            state = self.data.setdefault("weather_alert_awareness", {})
            if not isinstance(state, dict):
                state = {}
                self.data["weather_alert_awareness"] = state
            next_check = _safe_float(state.get("next_check_at"), 0)
            if not force and next_check > now:
                self._queue_weather_alert_pending_events(now=now)
                return deepcopy(self.data.get("weather_alerts", {}))
            interval_minutes = _safe_int(
                runtime_persona_setting(self, "weather_alert_refresh_minutes", 10),
                10,
                5,
                60,
            )
            state["next_check_at"] = now + interval_minutes * 60
            previous_cache = deepcopy(self.data.get("weather_alerts", {}))
            current_config_key = self._weather_alert_config_key()
            previous_config_key = _qweather_alert_text(
                previous_cache.get("config_key") if isinstance(previous_cache, dict) else "",
                96,
            )
            state_config_key = _qweather_alert_text(state.get("config_key"), 96)
            has_previous_alerts = bool(
                isinstance(previous_cache, dict)
                and isinstance(previous_cache.get("alerts"), list)
                and previous_cache.get("alerts")
            )
            config_changed = bool(
                (has_previous_alerts and previous_config_key != current_config_key)
                or (state_config_key and state_config_key != current_config_key)
            )
            if config_changed:
                # A location/host change invalidates both the baseline and
                # undelivered events from the previous place.
                state["initialized"] = False
                state["baseline_ids"] = []
                state["pending_events"] = []
            state["config_key"] = current_config_key
            result = await self._ensure_weather_alert_context(force=True)
            state["last_check_at"] = now
            if result.get("refreshed"):
                if config_changed:
                    state["initialized"] = False
                    state["pending_events"] = []
                initialized = bool(state.get("initialized"))
                if not initialized:
                    state["initialized"] = True
                    state["baseline_ids"] = [
                        self._weather_alert_identity(item)
                        for item in result.get("alerts", [])
                        if self._weather_alert_identity(item)
                    ][:64]
                else:
                    events = self._weather_alert_event_candidates(
                        previous_cache,
                        result,
                        now=now,
                        initialized=True,
                    )
                    self._weather_alert_append_pending_events(events)
                state["last_success_ts"] = _safe_float(result.get("last_success_ts"), now)
                state["last_error"] = ""
            else:
                state["last_error"] = _single_line(result.get("error"), 100)
                # A failed request retries sooner than a normal refresh but is
                # still bounded to avoid a tight loop during an outage.
                state["next_check_at"] = now + max(5 * 60, min(interval_minutes * 60, 30 * 60))
            offered = self._queue_weather_alert_pending_events(now=now)
            state["last_offered_count"] = offered
            saver = getattr(self, "_save_data_sync", None)
            if callable(saver):
                saver(
                    sections={
                        "weather_alert_awareness",
                        "users",
                        "proactive_candidate_pool",
                    }
                )
            return deepcopy(result)

    @classmethod
    def _weather_alerts_summary_text(
        cls,
        alerts: Any,
        *,
        min_severity: Any = "blue",
        max_items: int = 4,
    ) -> str:
        """Render a short, factual summary for a caller's prompt/context."""

        visible = cls._filter_weather_alerts(alerts, min_severity)
        lines: list[str] = []
        for alert in visible[: max(1, int(max_items))]:
            if not isinstance(alert, dict):
                continue
            level = _qweather_alert_text(alert.get("color") or alert.get("color_code") or alert.get("severity"), 24)
            event = _qweather_alert_text(alert.get("event") or "天气", 48)
            headline = _qweather_alert_text(alert.get("headline") or alert.get("description"), 160)
            if headline:
                lines.append(f"{level + ' ' if level else ''}{event}：{headline}")
        return "；".join(lines)

    def _qweather_weather_api_host(self) -> str:
        return self._normalize_qweather_api_host(
            getattr(self, "weather_api_host", "")
            or getattr(self, "qweather_api_host", "")
            or getattr(self, "weather_alert_api_host", "")
        )

    def _qweather_weather_token(self) -> str:
        raw = (
            getattr(self, "weather_token", "")
            or getattr(self, "qweather_token", "")
            or getattr(self, "weather_alert_token", "")
            or getattr(self, "weather_alert_jwt", "")
            or getattr(self, "weather_alert_api_key", "")
        )
        token = str(raw or "").strip()
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        return token

    def _qweather_weather_headers(self) -> dict[str, str]:
        token = self._qweather_weather_token()
        if self._qweather_alert_credential_kind(token) == "api_key":
            return {"X-QW-Api-Key": token, "Accept": "application/json"}
        return {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    def _qweather_weather_location(self, resolved: Any = None) -> tuple[float, float] | None:
        """Return (latitude, longitude) for the QWeather coordinate query."""

        snapshot = resolved if isinstance(resolved, dict) else self._qweather_location_snapshot()
        lat = self._qweather_alert_coordinate(snapshot.get("lat"), minimum=-90, maximum=90)
        lon = self._qweather_alert_coordinate(snapshot.get("lon"), minimum=-180, maximum=180)
        if lat is None or lon is None or (lat == 0 and lon == 0):
            return None
        return lat, lon

    def _build_qweather_weather_url(self, resolved: Any = None) -> str:
        host = self._qweather_weather_api_host()
        snapshot = resolved if isinstance(resolved, dict) else self._qweather_location_snapshot()
        location_id = _single_line(snapshot.get("location_id"), 40)
        location = self._qweather_weather_location(snapshot)
        if not host or (not location_id and location is None):
            return ""
        if location_id:
            location_text = location_id
        else:
            latitude, longitude = location
            # QWeather expects longitude first for coordinate locations.
            location_text = ",".join(
                (
                    self._qweather_alert_coordinate_text(longitude),
                    self._qweather_alert_coordinate_text(latitude),
                )
            )
        return host + "/v7/weather/now?" + urlencode(
            {"location": location_text, "lang": "zh", "unit": "m"}
        )

    # Keep a descriptive alias for integrations that use the "now" naming.
    _build_qweather_now_url = _build_qweather_weather_url

    @staticmethod
    def _parse_qweather_weather_payload(payload: Any) -> dict[str, str]:
        if not isinstance(payload, dict) or str(payload.get("code") or "") != "200":
            return {"prompt": "", "source": ""}
        current = payload.get("now")
        if not isinstance(current, dict):
            return {"prompt": "", "source": ""}
        description = _single_line(current.get("text"), 80)
        if not description:
            return {"prompt": "", "source": ""}
        try:
            temperature = float(current.get("temp"))
        except (TypeError, ValueError):
            return {"prompt": "", "source": ""}
        if not math.isfinite(temperature):
            return {"prompt": "", "source": ""}
        details: list[str] = []
        optional_fields = (
            ("feelsLike", "体感", "°C"),
            ("windDir", "", ""),
            ("windScale", "风力", "级"),
            ("humidity", "湿度", "%"),
        )
        for key, label, suffix in optional_fields:
            value = _single_line(current.get(key), 24)
            if not value:
                continue
            if key in {"feelsLike", "humidity"}:
                try:
                    numeric = float(value)
                except (TypeError, ValueError):
                    continue
                if not math.isfinite(numeric):
                    continue
                value = f"{numeric:g}"
            if key == "windDir":
                details.append(value)
            else:
                details.append(f"{label} {value}{suffix}")
        detail_text = "，" + "，".join(details) if details else ""
        return {
            "prompt": f"当前天气 {description}，约 {temperature:g}°C{detail_text}。",
            "source": "qweather",
        }

    async def _fetch_qweather_weather(self) -> dict[str, str]:
        host = self._qweather_weather_api_host()
        token = self._qweather_weather_token()
        resolved = await self._resolve_qweather_location()
        url = self._build_qweather_weather_url(resolved)
        if not host or not token or not url:
            return {"prompt": "", "source": ""}
        try:
            import aiohttp

            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(
                    url,
                    headers=self._qweather_weather_headers(),
                    allow_redirects=False,
                ) as response:
                    if response.status != 200:
                        logger.debug("QWeather 实时天气请求失败: %s", response.status)
                        return {"prompt": "", "source": ""}
                    try:
                        payload = await response.json()
                    except TypeError:
                        payload = await response.json(content_type=None)
        except asyncio.TimeoutError:
            logger.warning("QWeather 实时天气请求超时")
            return {"prompt": "", "source": ""}
        except Exception as exc:
            logger.debug("QWeather 实时天气获取失败: %s", _single_line(exc, 160))
            return {"prompt": "", "source": ""}
        parsed = self._parse_qweather_weather_payload(payload)
        if parsed.get("prompt"):
            label = _single_line(resolved.get("label") if isinstance(resolved, dict) else "", 120)
            if label:
                parsed["location_label"] = label
        return parsed

    async def _fetch_openmeteo_weather(self) -> dict[str, str]:
        try:
            lat = float(runtime_persona_setting(self, "weather_lat", 0))
            lon = float(runtime_persona_setting(self, "weather_lon", 0))
        except (TypeError, ValueError):
            return {"prompt": "", "source": ""}
        if (
            not math.isfinite(lat)
            or not math.isfinite(lon)
            or not -90 <= lat <= 90
            or not -180 <= lon <= 180
            or lat == 0 and lon == 0
        ):
            return {"prompt": "", "source": ""}
        params = urlencode(
            {
                "latitude": lat,
                "longitude": lon,
                "current": "temperature_2m,weather_code",
            }
        )
        url = f"https://api.open-meteo.com/v1/forecast?{params}"
        try:
            import aiohttp

            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        logger.debug(f"Open-Meteo 天气请求失败: {response.status}")
                        return {"prompt": "", "source": ""}
                    weather_data = await response.json()
        except Exception as e:
            logger.debug(f"Open-Meteo 天气获取失败: {e}")
            return {"prompt": "", "source": ""}
        try:
            if not isinstance(weather_data, dict):
                return {"prompt": "", "source": ""}
            current = weather_data.get("current")
            if not isinstance(current, dict):
                return {"prompt": "", "source": ""}
            temperature = float(current["temperature_2m"])
            weather_code = int(current["weather_code"])
            if not math.isfinite(temperature):
                return {"prompt": "", "source": ""}
            description = _openmeteo_weather_description(weather_code)
            return {
                "prompt": f"当前天气 {description},约 {temperature:g}°C。",
                "source": "openmeteo",
            }
        except (AttributeError, KeyError, TypeError, ValueError):
            return {"prompt": "", "source": ""}

    async def _fetch_amap_weather(self) -> dict[str, str]:
        key = str(getattr(self, "weather_amap_api_key", "") or "").strip()
        city = str(runtime_persona_setting(self, "weather_amap_city", "") or "").strip()
        if not key or not city:
            return {"prompt": "", "source": ""}
        url = "https://restapi.amap.com/v3/weather/weatherInfo?" + urlencode(
            {"key": key, "city": city, "extensions": "base", "output": "JSON"}
        )
        try:
            import aiohttp

            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        logger.debug(f"高德天气请求失败: {response.status}")
                        return {"prompt": "", "source": ""}
                    weather_data = await response.json()
        except Exception as e:
            logger.debug(f"高德天气获取失败: {e}")
            return {"prompt": "", "source": ""}
        try:
            if not isinstance(weather_data, dict) or str(weather_data.get("status")) != "1":
                return {"prompt": "", "source": ""}
            lives = weather_data.get("lives")
            live = lives[0] if isinstance(lives, list) and lives else None
            if not isinstance(live, dict) or not str(live.get("weather") or "").strip():
                return {"prompt": "", "source": ""}
            temperature = float(live["temperature"])
            if not math.isfinite(temperature):
                return {"prompt": "", "source": ""}
            return {
                "prompt": f"当前天气 {live['weather']}，约 {temperature:g}°C。",
                "source": "amap",
            }
        except (KeyError, TypeError, ValueError):
            return {"prompt": "", "source": ""}

    async def _fetch_own_weather_prompt(self) -> dict[str, str]:
        weather_source = str(runtime_persona_setting(self, "weather_source", "qweather") or "qweather").strip().lower()
        if weather_source == "qweather":
            return await self._fetch_qweather_weather()
        if weather_source == "amap":
            return await self._fetch_amap_weather()
        if weather_source == "openmeteo":
            return await self._fetch_openmeteo_weather()
        if not self.weather_api_key:
            return {"prompt": "", "source": ""}
        url = self._build_weather_url()
        if not url:
            return {"prompt": "", "source": ""}
        try:
            import aiohttp

            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        logger.debug(f"天气请求失败: {response.status}")
                        return {"prompt": "", "source": ""}
                    weather_data = await response.json()
        except Exception as e:
            logger.debug(f"私有天气获取失败: {e}")
            return {"prompt": "", "source": ""}
        try:
            weather_desc = weather_data.get("weather", [{}])[0].get("description", "")
            temp = weather_data.get("main", {}).get("temp", 0)
            if weather_desc:
                return {
                    "prompt": f"当前天气 {weather_desc},约 {temp}°C。",
                    "source": "private_companion",
                }
        except Exception:
            pass
        return {"prompt": "", "source": ""}

    def _build_weather_url(self) -> str:
        key = self.weather_api_key
        city = runtime_persona_setting(self, "weather_city", "")
        lat = runtime_persona_setting(self, "weather_lat", 0)
        lon = runtime_persona_setting(self, "weather_lon", 0)
        params = {
            "appid": key,
            "units": "metric",
            "lang": "zh_cn",
        }
        if city:
            params["q"] = city
            return f"https://api.openweathermap.org/data/2.5/weather?{urlencode(params)}"
        if -90 <= lat <= 90 and -180 <= lon <= 180 and lat != 0 and lon != 0:
            params["lat"] = lat
            params["lon"] = lon
            return f"https://api.openweathermap.org/data/2.5/weather?{urlencode(params)}"
        return ""

