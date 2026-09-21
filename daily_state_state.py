# -*- coding: utf-8 -*-
"""state 域。

由 tools/split_mixin_domain.py 从 daily_state.py 机械抽取（98 个方法 + 1 个模块级名字 + 0 个类级赋值 / 2974 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 DailyStateMixin）。
"""
from __future__ import annotations

import asyncio
import json
import random
import re
import sqlite3
import unicodedata
from .constants import DEFAULT_HUMANIZED_STATE
from .conversation_prompt_section import PromptRenderMode, PromptSection, prompt_section, render_prompt_sections
from .domains.affect.affect_modulation import compose_affect_modulation
from .helpers import _now_ts, _safe_float, _safe_int, _single_line, _today_key
from .model_routing import CURRENT_MODEL_REPLACEMENT_SOURCES, find_route, scope_allows
from .persona_config import runtime_persona_setting
from typing import Any, Iterable

from .logging_util import get_module_logger

logger = get_module_logger(__name__)



DEFAULT_PERSONA_PROMPT_FALLBACK = "未读取到 AstrBot 默认人格。请保持简洁、温和、有边界,不额外创造新身份。"


class DailyStateStateMixin:
    """state 域（从 DailyStateMixin 拆出）。"""


    async def _generate_state_conditions(
        self,
        weather: dict[str, Any] | None = None,
        *,
        deferred_state_updates: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        intensity = _safe_float(runtime_persona_setting(self, "humanized_state_intensity", 50), 50, 0, 100) / 100
        persona_profile = self._persona_state_profile()
        now_dt = self._environment_now()
        current_minute = now_dt.hour * 60 + now_dt.minute

        sleep_pool = [
            ("睡得很踏实", "平稳", 0, 8),
            ("昨晚睡得很浅,半夜醒了好几次", "迟钝", -16, 10),
            ("失眠了,翻来覆去很久才睡着", "敏感", -24, 14),
            ("一晚上都在做梦,醒过来却记不清", "恍惚", -18, 12),
            ("赖床赖得有点久,懵懵的", "迷糊", -14, 8),
            ("闹钟没叫醒我,起来还有点懵", "慌乱", -17, 7),
        ]
        dream_pool = [
            ("没有记住梦", "平稳", 0, 2),
            ("梦里一直在找一件放错地方的小东西,醒来还残着一点没找完的感觉", "恍惚", -6, 5),
            ("梦见走过一段很安静的路,路灯和风声都很近", "柔和", 4, 4),
            ("梦里反复听见一句没听清的话,醒来后胸口还有点闷", "低落", -10, 7),
        ]
        hunger_pool = [
            ("无饥饿感", "平稳", 0, 3),
            ("饿,想吃东西", "粘人", -4, 2),
            ("胃口不好", "低落", -8, 3),
            ("想吃甜的", "柔软", 1, 2),
        ]
        cycle_pool = [
            ("不处于生理期", "平稳", 0, 24),
            ("生理期前,身体感受更敏锐,耐受度稍低", "敏感", -18, 24),
            ("处于生理期,身体舒适度与能量偏低", "疲惫", -24, 72),
        ]

        def pick(pool: list[tuple[str, str, int, int]], special_chance: float = 0.35) -> tuple[str, str, int, int]:
            if random.random() > special_chance * max(0.2, intensity):
                return pool[0]
            return random.choice(pool[1:])

        sleep_pick = pick(sleep_pool, 0.42)
        enhanced_dream = None
        if bool(runtime_persona_setting(self, "enable_enhanced_dreams", False)):
            enhanced_dream = await self._generate_enhanced_dream_pick(weather)
        dream_pick = enhanced_dream or pick(dream_pool, 0.55)
        if deferred_state_updates is None:
            self._remember_daily_dream_pick(dream_pick)
        else:
            deferred_state_updates["dream_pick"] = dream_pick
        hunger_pick = pick(hunger_pool, 0.22)
        specs = [
            ("sleep", "睡眠", *sleep_pick),
            ("dream", "梦境", *dream_pick),
        ]
        if persona_profile.get("allow_hunger", True):
            specs.append(("hunger", "饥饿", *hunger_pick))
        if persona_profile.get("allow_cycle", False):
            skip_cycle_spec = False
            if self._advanced_cycle_enabled():
                meta = self.data.get("body_cycle_state", {})
                anchor_ts = _safe_float(meta.get("cycle_anchor_ts"), 0) if isinstance(meta, dict) else 0
                active_advanced_cycle = any(
                    isinstance(cond, dict)
                    and str(cond.get("kind") or "") == "body_cycle"
                    and str(cond.get("phase") or "") in self._ADVANCED_CYCLE_PHASES
                    and _safe_float(cond.get("start_ts"), 0) <= _now_ts() < _safe_float(cond.get("end_ts"), 0)
                    for cond in (self.data.get("state_conditions", []) or [])
                )
                # The anchored continuous timeline owns phase progression once
                # started, so no daily random cycle pick is needed anymore.
                skip_cycle_spec = anchor_ts > 0 or active_advanced_cycle
            if not skip_cycle_spec:
                cycle_spec = (
                    self._pick_advanced_cycle_spec(intensity)
                    if self._advanced_cycle_enabled()
                    else self._pick_body_cycle_spec(cycle_pool, intensity)
                )
                specs.append(("body_cycle", "周期", *cycle_spec))
        else:
            specs.append(("body_cycle", "周期", *cycle_pool[0]))

        diary_tags = self._recent_diary_tags()
        weather_text = self._weather_summary_text(weather)
        if persona_profile.get("allow_health", True):
            health_causes = self._build_health_causes(
                sleep_label=sleep_pick[0],
                weather_text=weather_text,
                diary_tags=diary_tags,
            )
            health_spec = self._pick_health_spec(health_causes, intensity, weather_text)
            if health_spec is not None:
                specs.append(("health", "健康", *health_spec))
        if "失眠" in diary_tags and random.random() < 0.35:
            specs.append(("sleep", "睡眠延续", "昨晚的失眠感还没完全散掉", "迟钝", -12, 8))
        if persona_profile.get("allow_health", True) and "生病" in diary_tags and random.random() < 0.4:
            specs.append(("health", "健康延续", "身体像还在恢复,反应慢半拍", "疲惫", -14, 18, "前两天的不舒服还没完全退掉"))
        if "低能量" in diary_tags and random.random() < 0.35:
            specs.append(("sleep", "能量延续", "昨天的低电量拖到今天早上", "安静", -10, 6))
        if "好梦" in diary_tags and random.random() < 0.3:
            specs.append(("dream", "梦境余温", "梦里留下了一点柔和的亮色", "柔和", 4, 5))
        screen_diary_spec = self._screen_diary_state_condition_spec()
        if screen_diary_spec is not None:
            specs.append(screen_diary_spec)

        conditions = []
        for spec in specs:
            extras: dict[str, Any] = {}
            if len(spec) >= 7:
                kind, title, label, mood, energy_delta, duration_hours, cause = spec[:7]
                extras["cause"] = cause
            else:
                kind, title, label, mood, energy_delta, duration_hours = spec[:6]
            cycle_phase = self._infer_body_cycle_phase(label) if kind == "body_cycle" else ""
            advanced_cycle_phase = self._advanced_cycle_enabled() and cycle_phase in self._ADVANCED_CYCLE_PHASES
            if energy_delta == 0 and kind not in {"sleep", "dream"} and not advanced_cycle_phase:
                continue
            if kind == "health" and energy_delta < 0:
                extras["on_end_transition"] = "health_relief"
                extras["phase"] = "mild_discomfort"
            if kind == "sleep" and energy_delta <= -16:
                extras["on_end_transition"] = "sleep_rebound"
                extras["phase"] = "sleep_debt"
            if kind == "body_cycle" and cycle_phase != "cycle":
                extras["phase"] = cycle_phase
                extras["episode_key"] = f"body-cycle-{_today_key()}"
                if cycle_phase in self._ADVANCED_CYCLE_PHASES:
                    extras["transition_options"] = self._advanced_cycle_transition_options(cycle_phase)
                elif extras["phase"] == "pre":
                    extras["transition_options"] = [{"to": "body_period", "base_weight": 0.72}, {"to": "stable", "base_weight": 0.28}]
                elif extras["phase"] == "period":
                    extras["transition_options"] = [{"to": "body_recovery", "base_weight": 0.65}, {"to": "stable", "base_weight": 0.35}]
            effective_energy_delta = (
                int(energy_delta)
                if advanced_cycle_phase
                else int(energy_delta * max(0.4, intensity))
            )
            extras["transition_options"] = self._build_transition_options(
                kind=kind,
                energy_delta=effective_energy_delta,
                cause=str(extras.get("cause") or ""),
                on_end_transition=str(extras.get("on_end_transition") or ""),
            ) or extras.get("transition_options", [])
            condition = self._make_condition(
                kind=kind,
                title=title,
                label=label,
                mood=mood,
                energy_delta=effective_energy_delta,
                duration_hours=duration_hours,
                intensity=random.randint(35, 90),
                **extras,
            )
            if kind == "body_cycle" and cycle_phase != "cycle":
                if deferred_state_updates is None:
                    self._record_body_cycle_episode(condition)
                else:
                    deferred_state_updates.setdefault("body_cycle_conditions", []).append(condition)
            conditions.append(condition)
        dream_aftertaste = self._build_dream_aftertaste_condition(dream_pick)
        if dream_aftertaste is not None:
            conditions.append(dream_aftertaste)
        discomfort_condition = self._maybe_pick_cycle_discomfort(deferred_state_updates)
        if discomfort_condition is not None:
            conditions.append(discomfort_condition)
        if 0 <= current_minute < 5 * 60:
            late_night_pool = [
                ("夜里还没完全安静下来,眼睛和脑子都慢半拍", "困倦", -14, 4),
                ("这个点还醒着,困意和清醒混在一起", "恍惚", -12, 3),
                ("已经很晚了,精神有点发飘,只想把声音放轻", "疲惫", -10, 5),
            ]
            label, mood, energy_delta, duration_hours = random.choice(late_night_pool)
            conditions.append(
                self._make_condition(
                    kind="sleep",
                    title="夜深未眠",
                    label=label,
                    mood=mood,
                    energy_delta=int(energy_delta * max(0.55, intensity)),
                    duration_hours=duration_hours,
                    intensity=random.randint(45, 88),
                    phase="late_night_awake",
                    transition_options=[
                        {"to": "sleep_afterglow", "base_weight": 0.35},
                        {"to": "sleep_tail", "base_weight": 0.2},
                        {"to": "stable", "base_weight": 0.45},
                    ],
                )
            )
        return conditions

    def _ensure_time_based_hunger_condition(self) -> None:
        profile = self._persona_state_profile()
        if not profile.get("allow_hunger", True):
            return
        if any(str(cond.get("kind") or "") == "hunger" for cond in self._get_active_conditions()):
            return
        if _safe_float(self.data.get("last_food_state_feedback_at"), 0) + 90 * 60 > _now_ts():
            return
        now_dt = self._environment_now()
        minute = now_dt.hour * 60 + now_dt.minute
        windows = [
            ("breakfast", 7 * 60, 9 * 60 + 30, "饿,想吃热的", "柔软", -4, 2),
            ("lunch", 11 * 60, 13 * 60 + 40, "饿,想吃东西", "走神", -6, 2),
            ("afternoon", 15 * 60, 17 * 60, "想吃甜的", "柔软", 2, 2),
            ("dinner", 17 * 60 + 30, 20 * 60, "饿,想吃热的", "粘人", -5, 3),
            ("late_snack", 21 * 60 + 30, 23 * 60 + 30, "有点想吃东西", "松散", -3, 2),
        ]
        matched = next((item for item in windows if item[1] <= minute <= item[2]), None)
        if not matched:
            return
        window_id, _start, _end, label, mood, energy_delta, duration_hours = matched
        attempts = self.data.get("hunger_window_attempts")
        if not isinstance(attempts, dict):
            attempts = {}
        today = _today_key()
        generated = attempts.get("generated")
        if not isinstance(generated, list):
            generated = []
        generated = [
            item for item in generated
            if isinstance(item, dict) and str(item.get("date") or "") == today
        ][-5:]
        if len(generated) >= 2:
            attempts["generated"] = generated
            self.data["hunger_window_attempts"] = attempts
            return
        last_generated_ts = max((_safe_float(item.get("ts"), 0) for item in generated), default=0.0)
        if last_generated_ts and _now_ts() - last_generated_ts < 4 * 3600:
            attempts["generated"] = generated
            self.data["hunger_window_attempts"] = attempts
            return
        attempt_key = f"{today}:{window_id}"
        if attempts.get("last_key") == attempt_key:
            return
        attempts["last_key"] = attempt_key
        attempts["last_attempt_ts"] = _now_ts()
        self.data["hunger_window_attempts"] = attempts
        intensity = max(0.0, min(1.0, _safe_float(runtime_persona_setting(self, "humanized_state_intensity", 50), 50, 0, 100) / 100))
        chance = 0.25 + 0.30 * intensity
        if window_id in {"afternoon", "late_snack"}:
            chance *= 0.65
        if random.random() > chance:
            return
        self.data.setdefault("state_conditions", []).append(
            self._make_condition(
                kind="hunger",
                title="饭点",
                label=label,
                mood=mood,
                energy_delta=int(energy_delta * max(0.55, intensity)),
                duration_hours=duration_hours,
                intensity=random.randint(45, 82),
                phase=window_id,
                cause="饭点自然波动",
            )
        )
        generated.append({"date": today, "window": window_id, "ts": _now_ts()})
        attempts["generated"] = generated[-5:]
        attempts["last_generated_ts"] = _now_ts()
        self.data["hunger_window_attempts"] = attempts

    def _advanced_cycle_enabled(self) -> bool:
        return bool(runtime_persona_setting(self, "enable_advanced_cycle_strategy", False))

    def _infer_body_cycle_phase(self, label: str) -> str:
        text = str(label or "")
        upper_text = text.upper()
        if "PMS" in upper_text or "经前综合征" in text:
            return "pms"
        if "排卵前期" in text:
            return "pre_ovulation"
        if "月经期" in text:
            return "menstrual"
        if "卵泡期" in text:
            return "follicular"
        if "排卵期" in text:
            return "ovulation"
        if "黄体期" in text:
            return "luteal"
        if "生理期后" in text or "恢复" in text:
            return "recovery"
        if "前" in text:
            return "pre"
        if "生理期" in text:
            return "period"
        return "cycle"

    def _body_cycle_max_hours(self, phase: str, label: str = "") -> int:
        phase = str(phase or self._infer_body_cycle_phase(label))
        advanced_hours = self._advanced_cycle_phase_hours(phase)
        if advanced_hours is not None:
            return advanced_hours
        if phase == "period":
            return 72
        if phase in {"pre", "recovery"}:
            return 24
        return 48

    def _body_cycle_interval_seconds(self) -> int:
        if self._advanced_cycle_enabled():
            return self._advanced_cycle_total_days() * 86400
        return random.randint(25, 34) * 86400

    def _advanced_cycle_phase_days(self, phase: str) -> int:
        defaults = {
            "menstrual": 5,
            "follicular": 5,
            "pre_ovulation": 3,
            "ovulation": 1,
            "luteal": 8,
            "pms": 6,
        }
        attributes = {
            "menstrual": "advanced_cycle_menstrual_days",
            "follicular": "advanced_cycle_follicular_days",
            "pre_ovulation": "advanced_cycle_pre_ovulation_days",
            "ovulation": "advanced_cycle_ovulation_days",
            "luteal": "advanced_cycle_luteal_days",
            "pms": "advanced_cycle_pms_days",
        }
        default = defaults.get(phase, 1)
        attribute = attributes.get(phase, "")
        return _safe_int(runtime_persona_setting(self, attribute, default), default, 1, 30) if attribute else default

    def _advanced_cycle_phase_hours(self, phase: str) -> int | None:
        if phase not in self._ADVANCED_CYCLE_PHASES:
            return None
        return self._advanced_cycle_phase_days(phase) * 24

    def _advanced_cycle_total_days(self) -> int:
        return sum(self._advanced_cycle_phase_days(phase) for phase in self._ADVANCED_CYCLE_PHASES)

    def _advanced_cycle_offset_signature(self, offset: int) -> str:
        durations = ",".join(str(self._advanced_cycle_phase_days(phase)) for phase in self._ADVANCED_CYCLE_PHASES)
        return f"{max(0, int(offset))}:{durations}"

    def _advanced_cycle_position_from_offset(self, offset: int) -> tuple[str, int]:
        total_days = max(1, self._advanced_cycle_total_days())
        cycle_day = ((max(1, int(offset)) - 1) % total_days) + 1
        cursor = 0
        for phase in self._ADVANCED_CYCLE_PHASES:
            phase_days = self._advanced_cycle_phase_days(phase)
            if cycle_day <= cursor + phase_days:
                return phase, cycle_day - cursor
            cursor += phase_days
        return "pms", self._advanced_cycle_phase_days("pms")

    def _advanced_cycle_day_of_phase(self, phase: str, day_in_phase: int) -> int:
        """Map a phase plus its day index to the absolute cycle day."""
        cursor = 0
        for candidate in self._ADVANCED_CYCLE_PHASES:
            if candidate == phase:
                return cursor + max(1, int(day_in_phase))
            cursor += self._advanced_cycle_phase_days(candidate)
        return 1

    def _advanced_cycle_runtime(self) -> dict[str, Any]:
        """Derive the current six-phase position for display and continuity.

        The stored cycle anchor timestamp is the authoritative continuous
        timeline: it always yields the current phase and day, even when the
        bot was offline or no body_cycle condition is currently active. Active
        conditions are only used as a fallback for old data without an anchor.

        Returns:
            Phase position details, or an empty dict when the strategy is off
            or the cycle has not started yet.
        """
        if not self._advanced_cycle_enabled():
            return {}
        now = _now_ts()
        meta = self.data.get("body_cycle_state")
        anchor_ts = _safe_float(meta.get("cycle_anchor_ts"), 0) if isinstance(meta, dict) else 0
        phase = ""
        day_in_phase = 0
        if anchor_ts > 0:
            cycle_day = int((now - anchor_ts) // 86400) + 1
            phase, day_in_phase = self._advanced_cycle_position_from_offset(cycle_day)
        else:
            # Legacy fallback for historical data created before the anchor
            # existed. The anchor is always seeded on first enable now, so this
            # branch only matters while migrating old conditions.
            conditions = self.data.get("state_conditions", [])
            if isinstance(conditions, list):
                for cond in conditions:
                    if not isinstance(cond, dict) or str(cond.get("kind") or "") != "body_cycle":
                        continue
                    cond_phase = str(cond.get("phase") or "")
                    if cond_phase not in self._ADVANCED_CYCLE_PHASES:
                        continue
                    start_ts = _safe_float(cond.get("start_ts"), 0)
                    end_ts = _safe_float(cond.get("end_ts"), 0)
                    if start_ts <= now < end_ts:
                        phase = cond_phase
                        day_in_phase = int((now - start_ts) // 86400) + 1
                        break
            if not phase:
                return {}
        phase_days = self._advanced_cycle_phase_days(phase)
        day_in_phase = max(1, min(phase_days, int(day_in_phase)))
        label, mood, energy_delta, _ = self._advanced_cycle_phase_spec(phase)
        next_phase = self._ADVANCED_CYCLE_TRANSITIONS.get(phase, "")
        next_phase = self._ADVANCED_CYCLE_TRANSITIONS.get(phase, "").removeprefix("body_")
        return {
            "phase": phase,
            "phase_name": self._ADVANCED_CYCLE_PHASE_NAMES.get(phase, phase),
            "day_in_phase": day_in_phase,
            "phase_days": phase_days,
            "cycle_day": self._advanced_cycle_day_of_phase(phase, day_in_phase),
            "cycle_days": self._advanced_cycle_total_days(),
            "mood": _single_line(mood, 20),
            "energy_delta": int(energy_delta),
            "label": _single_line(label, 160),
            "next_phase": next_phase,
            "next_phase_name": self._ADVANCED_CYCLE_PHASE_NAMES.get(next_phase, ""),
        }

    def _active_cycle_discomfort_conditions(self) -> list[dict[str, Any]]:
        now = _now_ts()
        items: list[dict[str, Any]] = []
        conditions = self.data.get("state_conditions", [])
        if not isinstance(conditions, list):
            return items
        for cond in conditions:
            if not isinstance(cond, dict) or str(cond.get("kind") or "") != "cycle_discomfort":
                continue
            if _safe_float(cond.get("start_ts"), 0) <= now < _safe_float(cond.get("end_ts"), 0):
                items.append(
                    {
                        "type": _single_line(cond.get("phase"), 12) or "经期不适",
                        "label": _single_line(cond.get("label"), 80),
                        "mood": _single_line(cond.get("mood"), 12),
                    }
                )
        return items

    def _maybe_pick_cycle_discomfort(self, deferred_state_updates: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """Roll once per day for a menstrual discomfort episode on the current phase.

        Only runs when the discomfort simulation, the advanced six-phase
        strategy and the persona cycle allowance are all enabled, and only
        during phases allowed per discomfort type. Rolls at most once per
        calendar day and skips the roll while another discomfort condition is
        still active.

        Returns:
            A cycle_discomfort condition dict, or None when skipped.
        """
        if not bool(runtime_persona_setting(self, "advanced_cycle_discomfort_simulation", False)):
            return None
        if not self._persona_state_profile().get("allow_cycle", False):
            return None
        intensity = _safe_int(runtime_persona_setting(self, "humanized_state_intensity", 50), 50, 0, 100)
        if intensity <= 0:
            return None
        meta = self.data.get("body_cycle_state")
        meta = dict(meta) if isinstance(meta, dict) else {}
        if meta.get("last_discomfort_roll_date") == _today_key():
            return None
        runtime = self._advanced_cycle_runtime()
        phase = runtime.get("phase") if runtime else ""
        if phase not in self._ADVANCED_CYCLE_PHASES:
            return None
        now = _now_ts()
        conditions = self.data.get("state_conditions", [])
        if isinstance(conditions, list):
            for cond in conditions:
                if (
                    isinstance(cond, dict)
                    and str(cond.get("kind") or "") == "cycle_discomfort"
                    and _safe_float(cond.get("end_ts"), 0) > now
                ):
                    return None
        # One roll attempt per day regardless of the outcome, so a failed roll
        # does not give the phase extra chances later the same day.
        if deferred_state_updates is None:
            meta["last_discomfort_roll_date"] = _today_key()
            self.data["body_cycle_state"] = meta
        else:
            deferred_state_updates["cycle_discomfort_roll_date"] = _today_key()
        chance = _safe_int(runtime_persona_setting(self, "advanced_cycle_discomfort_chance", 55), 55, 0, 100)
        if chance <= 0 or random.random() > chance / 100.0:
            return None
        raw_types = str(runtime_persona_setting(self, "advanced_cycle_discomfort_types", "痛经,头痛,腰酸,乏力") or "痛经,头痛,腰酸,乏力")
        requested = {token.strip() for token in raw_types.replace("，", ",").split(",") if token.strip()}
        candidates = [
            (name, spec)
            for name, spec in self._ADVANCED_CYCLE_DISCOMFORT_SPECS.items()
            if name in requested and phase in spec.get("phases", set())
        ]
        if not candidates:
            return None
        name, spec = random.choices(
            candidates,
            weights=[int(spec.get("weight") or 1) for _, spec in candidates],
            k=1,
        )[0]
        energy_delta = int((spec.get("energy_delta") or 0) * max(0.5, intensity / 50.0))
        return self._make_condition(
            kind="cycle_discomfort",
            title="经期不适",
            label=_single_line(spec.get("label"), 80),
            mood=_single_line(spec.get("mood"), 12) or "疲惫",
            energy_delta=energy_delta,
            duration_hours=_safe_int(spec.get("duration_hours"), 6, 1, 24),
            intensity=random.randint(45, max(46, min(92, 40 + intensity))),
            cause="生理周期阶段伴随不适",
            phase=name,
            episode_key=f"cycle-discomfort-{_today_key()}",
        )

    def _advanced_cycle_linked_energy(self, phase: str) -> int:
        median = self._ADVANCED_CYCLE_INTENSITY_MEDIANS.get(phase, 0.0)
        intensity = _safe_int(runtime_persona_setting(self, "humanized_state_intensity", 50), 50, 0, 100)
        return int(round(median * (intensity / 50.0)))

    def _advanced_cycle_phase_spec(self, phase: str) -> tuple[str, str, int, int]:
        defaults = {
            "menstrual": ("处于月经期，身体更容易疲倦，情绪感受稍敏锐", "疲惫", -12),
            "follicular": ("处于卵泡期，精力平稳回升，心情逐渐轻快", "轻快", 0),
            "pre_ovulation": ("处于排卵前期，身体逐渐轻盈，精力有所上升", "期待", 8),
            "ovulation": ("处于排卵期，精力较充足，社交意愿稍有增强", "明朗", 9),
            "luteal": ("处于黄体期，精力尚可，情绪整体平稳", "平稳", 5),
            "pms": ("处于 PMS 期，精力有所下降，情绪波动稍明显", "敏感", -8),
        }
        attributes = {
            "menstrual": ("advanced_cycle_menstrual_prompt", "advanced_cycle_menstrual_mood", "advanced_cycle_menstrual_energy"),
            "follicular": ("advanced_cycle_follicular_prompt", "advanced_cycle_follicular_mood", "advanced_cycle_follicular_energy"),
            "pre_ovulation": ("advanced_cycle_pre_ovulation_prompt", "advanced_cycle_pre_ovulation_mood", "advanced_cycle_pre_ovulation_energy"),
            "ovulation": ("advanced_cycle_ovulation_prompt", "advanced_cycle_ovulation_mood", "advanced_cycle_ovulation_energy"),
            "luteal": ("advanced_cycle_luteal_prompt", "advanced_cycle_luteal_mood", "advanced_cycle_luteal_energy"),
            "pms": ("advanced_cycle_pms_prompt", "advanced_cycle_pms_mood", "advanced_cycle_pms_energy"),
        }
        selected_phase = phase if phase in defaults else "menstrual"
        default_prompt, default_mood, default_energy = defaults[selected_phase]
        prompt_attr, mood_attr, energy_attr = attributes[selected_phase]
        label = _single_line(runtime_persona_setting(self, prompt_attr, default_prompt), 160) or default_prompt
        mood = _single_line(runtime_persona_setting(self, mood_attr, default_mood), 20) or default_mood
        energy_delta = (
            self._advanced_cycle_linked_energy(selected_phase)
        if bool(runtime_persona_setting(self, "advanced_cycle_link_intensity", False))
            else _safe_int(runtime_persona_setting(self, energy_attr, default_energy), default_energy, -50, 30)
        )
        return label, mood, energy_delta, self._advanced_cycle_phase_days(selected_phase) * 24

    def _advanced_cycle_transition_options(self, phase: str) -> list[dict[str, Any]]:
        target = self._ADVANCED_CYCLE_TRANSITIONS.get(phase, "")
        return [{"to": target, "base_weight": 1.0}] if target else []

    def _advanced_cycle_condition(
        self,
        phase: str,
        *,
        episode_key: str = "",
        cause: str = "周期阶段自然推进",
        duration_hours: int | None = None,
    ) -> dict[str, Any]:
        label, mood, energy_delta, configured_hours = self._advanced_cycle_phase_spec(phase)
        return self._make_condition(
            kind="body_cycle",
            title="周期",
            label=label,
            mood=mood,
            energy_delta=energy_delta,
            duration_hours=max(1, int(duration_hours or configured_hours)),
            intensity=max(35, _safe_int(runtime_persona_setting(self, "humanized_state_intensity", 50), 50, 0, 100)),
            cause=cause,
            phase=phase,
            episode_key=episode_key or f"body-cycle-{_today_key()}",
            transition_options=self._advanced_cycle_transition_options(phase),
        )

    def _pick_advanced_cycle_spec(self, intensity: float) -> tuple[str, str, int, int]:
        neutral = ("不处于生理期", "平稳", 0, 24)
        if self._body_cycle_generation_blocked():
            return neutral
        meta = self.data.get("body_cycle_state", {})
        anchor_ts = _safe_float(meta.get("cycle_anchor_ts"), 0) if isinstance(meta, dict) else 0
        if anchor_ts > 0:
            # Once the continuous timeline is anchored, phase progression is
            # deterministic; a random new-cycle pick would shift it backwards.
            return neutral
        now = _now_ts()
        expected_ts = _safe_float(meta.get("next_expected_start_ts"), 0) if isinstance(meta, dict) else 0
        if expected_ts > 0:
            days_late = max(0.0, (now - expected_ts) / 86400)
            chance = min(0.75, 0.22 + days_late * 0.14) * max(0.35, min(1.15, intensity))
        else:
            chance = 0.10 * max(0.35, min(1.2, intensity))
        if random.random() > chance:
            return neutral
        return self._advanced_cycle_phase_spec("menstrual")

    def _body_cycle_generation_blocked(self, now: float | None = None) -> bool:
        now = _now_ts() if now is None else now
        meta = self.data.get("body_cycle_state", {})
        if isinstance(meta, dict):
            expected_ts = _safe_float(meta.get("next_expected_start_ts"), 0)
            if expected_ts > 0 and now < expected_ts - 2 * 86400:
                return True
            if expected_ts <= 0 and _safe_float(meta.get("last_end_ts"), 0) + 18 * 86400 > now:
                return True
        conditions = self.data.get("state_conditions", [])
        if not isinstance(conditions, list):
            return False
        recent_floor = now - 14 * 86400
        for cond in conditions:
            if not isinstance(cond, dict) or str(cond.get("kind") or "") != "body_cycle":
                continue
            start_ts = _safe_float(cond.get("start_ts"), 0)
            end_ts = _safe_float(cond.get("end_ts"), 0)
            if end_ts > now or max(start_ts, end_ts) >= recent_floor:
                return True
        return False

    def _pick_body_cycle_spec(
        self,
        cycle_pool: list[tuple[str, str, int, int]],
        intensity: float,
    ) -> tuple[str, str, int, int]:
        neutral = cycle_pool[0]
        if self._body_cycle_generation_blocked():
            return neutral
        now = _now_ts()
        meta = self.data.get("body_cycle_state", {})
        expected_ts = _safe_float(meta.get("next_expected_start_ts"), 0) if isinstance(meta, dict) else 0
        if expected_ts > 0:
            days_late = max(0.0, (now - expected_ts) / 86400)
            chance = min(0.65, 0.18 + days_late * 0.12) * max(0.35, min(1.15, intensity))
        else:
            chance = 0.085 * max(0.35, min(1.2, intensity))
        if random.random() > chance:
            return neutral
        return random.choices(cycle_pool[1:], weights=[0.45, 0.55], k=1)[0]

    def _record_body_cycle_episode(self, cond: dict[str, Any]) -> None:
        start_ts = _safe_float(cond.get("start_ts"), _now_ts())
        end_ts = _safe_float(cond.get("end_ts"), start_ts)
        phase = str(cond.get("phase") or self._infer_body_cycle_phase(str(cond.get("label") or "")))
        previous = self.data.get("body_cycle_state")
        meta = dict(previous) if isinstance(previous, dict) else {}
        payload = {
            "last_start_ts": start_ts,
            "last_end_ts": end_ts,
            "next_expected_start_ts": start_ts + self._body_cycle_interval_seconds(),
            "last_phase": phase,
            "last_label": _single_line(cond.get("label"), 80),
        }
        # Episode reconciliation rewrites this record whenever the bot
        # catches up after downtime. Keep the daily discomfort dedup marker
        # across those rewrites so one calendar day still gets one roll.
        if meta.get("last_discomfort_roll_date"):
            payload["last_discomfort_roll_date"] = _single_line(
                meta.get("last_discomfort_roll_date"), 16
            )
        if phase in self._ADVANCED_CYCLE_PHASES:
            payload["strategy"] = "advanced"
            previous_anchor = _safe_float(meta.get("cycle_anchor_ts"), 0)
            if phase == "menstrual" and previous_anchor <= 0:
                payload["cycle_anchor_ts"] = start_ts
            elif previous_anchor > 0:
                payload["cycle_anchor_ts"] = previous_anchor
            if phase != "menstrual" and _safe_float(meta.get("last_start_ts"), 0) > 0:
                payload["last_start_ts"] = _safe_float(meta.get("last_start_ts"), start_ts)
                payload["next_expected_start_ts"] = _safe_float(
                    meta.get("next_expected_start_ts"),
                    payload["last_start_ts"] + self._advanced_cycle_total_days() * 86400,
                )
            for key in ("manual_offset", "manual_offset_signature", "manual_offset_phase", "manual_offset_day_in_phase"):
                if key in meta:
                    payload[key] = meta[key]
        else:
            payload["strategy"] = "legacy"
        self.data["body_cycle_state"] = payload

    def _build_health_causes(
        self,
        *,
        sleep_label: str,
        weather_text: str,
        diary_tags: set[str],
    ) -> list[str]:
        causes: list[str] = []
        if sleep_label not in {"睡眠平稳", "睡得很踏实"} and random.random() < 0.7:
            causes.append("昨晚没睡踏实")
        if any(tag in diary_tags for tag in {"失眠", "低能量"}) and random.random() < 0.45:
            causes.append("前一天状态就有点透支")
        weather_lower = str(weather_text or "").lower()
        if any(token in weather_text for token in ("降雨", "小雨", "中雨", "大雨", "阴", "多云")) and random.random() < 0.4:
            causes.append("空气有点潮,身上那股乏劲更明显")
        if any(token in weather_text for token in ("风", "降温", "冷")) and random.random() < 0.55:
            causes.append("吹了点风,身上容易发空")
        temp_match = re.search(r"(-?\d+(?:\.\d+)?)\s*°C", weather_lower)
        if temp_match:
            try:
                temp = float(temp_match.group(1))
            except ValueError:
                temp = 20.0
            if temp <= 10 and random.random() < 0.55:
                causes.append("天气偏冷,早上容易着凉")
            elif temp >= 30 and random.random() < 0.35:
                causes.append("天气闷热,整个人有点蔫")
        return causes

    def _pick_health_spec(
        self, causes: list[str], intensity: float, weather_text: str
    ) -> tuple[str, str, int, int, str] | None:
        if not causes:
            return None
        chance = min(0.42, 0.12 + len(causes) * 0.1 * max(0.5, intensity))
        if random.random() > chance:
            return None
        cause_text = ",".join(dict.fromkeys(causes[:2]))
        pool = [
            ("喉咙有点发紧,今天想少说重话", "安静", -10, 24),
            ("头有点沉,做事想放慢一点", "疲惫", -14, 18),
            ("像有点发虚,反应会慢半拍", "疲惫", -18, 30),
        ]
        label, mood, energy_delta, duration_hours = random.choice(pool)
        if "闷热" in cause_text and "喉咙" in label:
            label = "有点发闷,只想把动作放轻一点"
        if "潮" in cause_text and "头有点沉" in label:
            label = "身上有点沉,今天想把事情做轻一点"
        return label, mood, energy_delta, duration_hours, cause_text

    def _build_transition_options(
        self,
        *,
        kind: str,
        energy_delta: int,
        cause: str,
        on_end_transition: str,
    ) -> list[dict[str, Any]]:
        if on_end_transition == "health_relief":
            return [
                {"to": "recovery_afterglow", "base_weight": 0.45},
                {"to": "stable", "base_weight": 0.4},
                {"to": "health_tail", "base_weight": 0.15},
            ]
        if on_end_transition == "sleep_rebound":
            return [
                {"to": "sleep_afterglow", "base_weight": 0.35},
                {"to": "stable", "base_weight": 0.5},
                {"to": "sleep_tail", "base_weight": 0.15},
            ]
        if kind == "care_warmth":
            return [
                {"to": "stable", "base_weight": 0.8},
                {"to": "soft_afterglow", "base_weight": 0.2},
            ]
        return []

    def _make_condition(
        self,
        *,
        kind: str,
        title: str,
        label: str,
        mood: str,
        energy_delta: int,
        duration_hours: int,
        intensity: int,
        cause: str = "",
        on_end_transition: str = "",
        phase: str = "",
        episode_key: str = "",
        transition_options: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        start_ts = _now_ts()
        return {
            "id": f"{kind}-{int(start_ts)}-{random.randint(1000, 9999)}",
            "kind": kind,
            "title": title,
            "label": label,
            "mood": mood,
            "energy_delta": energy_delta,
            "intensity": intensity,
            "start_ts": start_ts,
            "end_ts": start_ts + duration_hours * 3600,
            "duration_hours": duration_hours,
            "cause": cause,
            "on_end_transition": on_end_transition,
            "phase": phase,
            "episode_key": episode_key,
            "transition_options": list(transition_options or []),
        }

    def _infer_manual_state_mood(self, text: str) -> str:
        raw = str(text or "")
        mapping = [
            (("累", "疲惫", "困", "没电"), "疲惫"),
            (("烦", "乱", "躁", "闷"), "烦闷"),
            (("病", "难受", "不舒服", "头疼", "发烧"), "虚弱"),
            (("饿", "胃口", "嘴馋"), "黏人"),
            (("开心", "轻快", "高兴", "兴奋"), "轻快"),
            (("紧张", "慌", "忐忑"), "紧张"),
            (("安静", "困倦", "恍惚"), "安静"),
        ]
        for markers, mood in mapping:
            if any(marker in raw for marker in markers):
                return mood
        return "平稳"

    def _infer_manual_state_energy_delta(self, text: str) -> int:
        raw = str(text or "")
        if any(token in raw for token in ("开心", "轻快", "高兴", "兴奋")):
            return 6
        if any(token in raw for token in ("病", "难受", "不舒服", "发烧", "头疼")):
            return -16
        if any(token in raw for token in ("累", "疲惫", "困", "没电")):
            return -10
        if any(token in raw for token in ("烦", "乱", "躁", "闷")):
            return -8
        return -4 if any(token in raw for token in ("紧张", "慌")) else 0

    async def _add_manual_state(self, value: str) -> tuple[bool, str]:
        raw = str(value or "").strip()
        if not raw:
            return False, "请这样填写：陪伴 增添状态 有点累了|8"
        label_part, sep, hours_part = raw.partition("|")
        label = _single_line(label_part, 80)
        if not label:
            return False, "状态描述不能为空。"
        profile = self._persona_state_profile()
        hunger_like = any(token in label for token in ("饿", "胃口", "嘴馋", "馋", "想吃", "吃点", "吃些"))
        health_like = any(token in label for token in ("病", "难受", "不舒服", "发烧", "头疼", "头痛", "咳", "感冒"))
        if hunger_like and not profile.get("allow_hunger", True):
            return False, "当前配置未开启饥饿/胃口状态。"
        if health_like and not profile.get("allow_health", True):
            return False, "当前配置未开启健康/不适状态。"
        duration_hours = _safe_int(hours_part.strip() if sep else 12, 12, 1, 72)
        mood = self._infer_manual_state_mood(label)
        energy_delta = self._infer_manual_state_energy_delta(label)
        await self._ensure_daily_state()
        async with self._data_lock:
            conditions = self.data.setdefault("state_conditions", [])
            if not isinstance(conditions, list):
                self.data["state_conditions"] = []
                conditions = self.data["state_conditions"]
            conditions.append(
                self._make_condition(
                    kind="manual_state",
                    title="手动增添状态",
                    label=label,
                    mood=mood,
                    energy_delta=energy_delta,
                    duration_hours=duration_hours,
                    intensity=60,
                    cause="由用户手动增添",
                    phase="manual",
                )
            )
            state = self._compose_state_from_conditions(self.data.get("daily_weather", {}))
            self.data["daily_state"] = state
            self._save_data_sync(sections={"daily_state", "state_conditions"})
        return True, f"已增添状态：{label}（约持续 {duration_hours} 小时）"

    def _detect_interaction_warmth_feedback(self, text: str, user: dict[str, Any] | None = None) -> dict[str, Any]:
        normalized = _single_line(text, 220)
        if not normalized:
            return {"is_warmth": False}
        intimate = bool(re.search(r"摸摸|贴贴|抱抱|亲亲|揉揉|蹭蹭|摸头|抱一下|贴一下|rua", normalized, re.IGNORECASE))
        comfort = bool(re.search(r"陪你|哄你|乖|不难过|别难过|没关系|辛苦了|抱一下|摸摸头", normalized))
        positive = bool(re.search(r"开心|好耶|哈哈|笑死|可爱|喜欢|太好了|真好|想你|爱你|在呢|来了|陪我", normalized, re.IGNORECASE))
        if not (intimate or comfort or positive):
            return {"is_warmth": False}

        relationship_score = _safe_int(user.get("relationship_score") if isinstance(user, dict) else 0, 0, 0)
        episode_count = _safe_int(user.get("episode_message_count") if isinstance(user, dict) else 0, 0, 0)
        state = self._compose_state_from_conditions(self.data.get("daily_weather", {}))
        energy = _safe_int(state.get("energy"), 75, 0, 100)

        is_sustained_positive = positive and episode_count >= 6 and relationship_score >= 18
        if positive and not (intimate or comfort or is_sustained_positive):
            return {"is_warmth": False}

        base_delta = 2 if intimate or comfort else 1
        if energy <= 45:
            base_delta += 2
        elif energy <= 62:
            base_delta += 1
        elif energy >= 86:
            base_delta = max(1, base_delta - 1)
        if relationship_score >= 120:
            base_delta += 2
        elif relationship_score >= 55:
            base_delta += 1
        if is_sustained_positive and episode_count >= 10:
            base_delta += 1

        max_delta = 8 if intimate or comfort else 4
        delta = max(1, min(max_delta, base_delta))
        if intimate:
            source = "亲密互动回暖"
            label = "被亲近安抚后,精神轻轻回暖"
            mood = "柔和"
            duration_hours = 4
            intensity = 58
            phase = "intimacy"
        elif comfort:
            source = "安慰互动回暖"
            label = "被安慰后,紧绷感松开一点"
            mood = "柔和"
            duration_hours = 4
            intensity = 54
            phase = "comfort"
        else:
            source = "连续对话回暖"
            label = "和熟悉的人连续聊了一会儿,精神被带起来一点"
            mood = "轻快"
            duration_hours = 3
            intensity = 42
            phase = "sustained_positive_chat"
        return {
            "is_warmth": True,
            "source": source,
            "label": label,
            "mood": mood,
            "energy_delta": delta,
            "duration_hours": duration_hours,
            "intensity": intensity,
            "phase": phase,
            "cause": _single_line(normalized, 80),
            "max_delta": max_delta,
        }

    def _apply_interaction_warmth_to_state(self, text: str, user: dict[str, Any] | None = None) -> bool:
        feedback = self._detect_interaction_warmth_feedback(text, user)
        if not feedback.get("is_warmth"):
            return False
        now = _now_ts()
        conditions = self.data.setdefault("state_conditions", [])
        if not isinstance(conditions, list):
            self.data["state_conditions"] = []
            conditions = self.data["state_conditions"]
        max_delta = _safe_int(feedback.get("max_delta"), 6, 1, 10)
        active = next(
            (
                cond for cond in reversed(conditions)
                if isinstance(cond, dict)
                and str(cond.get("kind") or "") == "interaction_warmth"
                and _safe_float(cond.get("end_ts"), 0) > now
            ),
            None,
        )
        if isinstance(active, dict):
            current_delta = _safe_int(active.get("energy_delta"), 0, 0, 20)
            incoming_delta = _safe_int(feedback.get("energy_delta"), 1, 1, 10)
            active["energy_delta"] = min(max_delta, max(current_delta, incoming_delta) + 1)
            active["end_ts"] = max(
                _safe_float(active.get("end_ts"), now),
                now + _safe_int(feedback.get("duration_hours"), 3, 1, 8) * 3600,
            )
            active["duration_hours"] = max(1, int((_safe_float(active.get("end_ts"), now) - now) / 3600))
            active["label"] = _single_line(feedback.get("label"), 80)
            active["mood"] = _single_line(feedback.get("mood"), 20) or active.get("mood") or "柔和"
            active["cause"] = _single_line(feedback.get("cause"), 80)
            active["phase"] = _single_line(feedback.get("phase"), 40)
            active["intensity"] = max(_safe_int(active.get("intensity"), 40), _safe_int(feedback.get("intensity"), 40))
        else:
            conditions.append(
                self._make_condition(
                    kind="interaction_warmth",
                    title=_single_line(feedback.get("source"), 40) or "互动回暖",
                    label=_single_line(feedback.get("label"), 80),
                    mood=_single_line(feedback.get("mood"), 20) or "柔和",
                    energy_delta=_safe_int(feedback.get("energy_delta"), 2, 1, 10),
                    duration_hours=_safe_int(feedback.get("duration_hours"), 3, 1, 8),
                    intensity=_safe_int(feedback.get("intensity"), 45, 0, 100),
                    cause=_single_line(feedback.get("cause"), 80),
                    phase=_single_line(feedback.get("phase"), 40),
                )
            )
        self.data["daily_state"] = self._compose_state_from_conditions(self.data.get("daily_weather", {}))
        return True

    def _synchronize_body_cycle_strategy(self, conditions: list[Any], now: float) -> list[Any]:
        advanced_enabled = self._advanced_cycle_enabled()
        desired_mode = "advanced" if advanced_enabled else "legacy"
        previous_mode = str(self.data.get("body_cycle_strategy_mode") or "")
        kept: list[Any] = []
        removed = 0
        for cond in conditions:
            if not isinstance(cond, dict) or str(cond.get("kind") or "") != "body_cycle":
                kept.append(cond)
                continue
            phase = str(cond.get("phase") or self._infer_body_cycle_phase(str(cond.get("label") or "")))
            is_advanced = phase in self._ADVANCED_CYCLE_PHASES
            if is_advanced != advanced_enabled:
                removed += 1
                continue
            cond["phase"] = phase
            kept.append(cond)
        if removed:
            existing_meta = self.data.get("body_cycle_state")
            # A legacy condition may still be present while an advanced
            # timeline has already been anchored. Remove only the incompatible
            # condition in that case; resetting the anchor would move the
            # user back to day one after a restart or migration.
            keep_continuous_state = (
                desired_mode == "advanced"
                and isinstance(existing_meta, dict)
                and _safe_float(existing_meta.get("cycle_anchor_ts"), 0) > 0
            )
            if not keep_continuous_state:
                self.data.pop("body_cycle_state", None)
            logger.info(
                "周期策略切换，已清理不兼容旧状态: mode=%s removed=%s",
                desired_mode,
                removed,
            )
        self.data["body_cycle_strategy_mode"] = desired_mode

        if not advanced_enabled:
            return kept

        offset = _safe_int(runtime_persona_setting(self, "advanced_cycle_start_offset", 0), 0, 0, 180)
        meta = self.data.get("body_cycle_state")
        meta = dict(meta) if isinstance(meta, dict) else {}
        if offset <= 0:
            if meta.get("manual_offset_signature"):
                for key in ("manual_offset", "manual_offset_signature", "manual_offset_phase", "manual_offset_day_in_phase"):
                    meta.pop(key, None)
                self.data["body_cycle_state"] = meta
            has_cycle_condition = any(
                isinstance(cond, dict) and str(cond.get("kind") or "") == "body_cycle"
                for cond in kept
            )
            anchor_ts = _safe_float(meta.get("cycle_anchor_ts"), 0)
            if not has_cycle_condition and anchor_ts <= 0:
                condition = self._advanced_cycle_condition(
                    "menstrual",
                    cause="六阶段周期策略首次启用，自然进入第一周期",
                )
                kept.append(condition)
                self._record_body_cycle_episode(condition)
                logger.info("六阶段周期策略首次启用，已从月经期第 1 天开始推进")
            return kept

        signature = self._advanced_cycle_offset_signature(offset)
        if meta.get("manual_offset_signature") == signature:
            return kept

        kept = [
            cond
            for cond in kept
            if not (isinstance(cond, dict) and str(cond.get("kind") or "") == "body_cycle")
        ]
        phase, day_in_phase = self._advanced_cycle_position_from_offset(offset)
        remaining_days = self._advanced_cycle_phase_days(phase) - day_in_phase + 1
        condition = self._advanced_cycle_condition(
            phase,
            cause="管理员设置了周期起始日",
            duration_hours=remaining_days * 24,
        )
        kept.append(condition)
        self._record_body_cycle_episode(condition)
        meta = self.data.get("body_cycle_state")
        meta = dict(meta) if isinstance(meta, dict) else {}
        meta.update(
            {
                "manual_offset": offset,
                "manual_offset_signature": signature,
                "manual_offset_phase": phase,
                "manual_offset_day_in_phase": day_in_phase,
                "cycle_anchor_ts": max(0.0, now - (offset - 1) * 86400),
                "strategy": "advanced",
            }
        )
        self.data["body_cycle_state"] = meta
        logger.info(
            "已应用六阶段周期起始日: offset=%s phase=%s phase_day=%s previous_mode=%s",
            offset,
            phase,
            day_in_phase,
            previous_mode or "unknown",
        )
        return kept

    def _cleanup_expired_conditions(self) -> set[str]:
        now = _now_ts()
        had_body_cycle_state = "body_cycle_state" in self.data
        conditions = self.data.setdefault("state_conditions", [])
        if not isinstance(conditions, list):
            self.data["state_conditions"] = []
            return set()
        profile = self._persona_state_profile()
        if not profile.get("allow_cycle", False):
            before_count = len(conditions)
            conditions = [
                cond for cond in conditions
                if not isinstance(cond, dict) or str(cond.get("kind") or "") not in {"body_cycle", "cycle_discomfort"}
            ]
            removed_count = before_count - len(conditions)
            if removed_count:
                self.data.pop("body_cycle_state", None)
                logger.info("生理期模拟已关闭，清理旧周期状态: removed=%s", removed_count)
        else:
            conditions = self._synchronize_body_cycle_strategy(conditions, now)
            conditions = self._repair_body_cycle_conditions(conditions, now)
            if not self._advanced_cycle_enabled() or not bool(
                runtime_persona_setting(self, "advanced_cycle_discomfort_simulation", False)
            ):
                before_count = len(conditions)
                conditions = [
                    cond
                    for cond in conditions
                    if not isinstance(cond, dict) or str(cond.get("kind") or "") != "cycle_discomfort"
                ]
                if len(conditions) < before_count:
                    logger.info(
                        "不适模拟已关闭，清理残留经期不适状态: removed=%s",
                        before_count - len(conditions),
                    )
        active = []
        expired = []
        for cond in conditions:
            if not isinstance(cond, dict):
                continue
            if _safe_float(cond.get("end_ts"), 0) > now:
                active.append(cond)
            else:
                expired.append(cond)
        for cond in expired:
            active.extend(self._spawn_followup_conditions(cond))
        active = self._reconcile_advanced_cycle_condition(active, now)
        active = self._prune_active_hunger_conditions(active, now)
        self.data["state_conditions"] = active
        return {
            "body_cycle_state"
        } if had_body_cycle_state and "body_cycle_state" not in self.data else set()

    def _prune_active_hunger_conditions(self, conditions: list[dict[str, Any]], now: float) -> list[dict[str, Any]]:
        hunger_items = [
            cond for cond in conditions
            if isinstance(cond, dict)
            and str(cond.get("kind") or "") == "hunger"
            and _safe_float(cond.get("start_ts"), 0) <= now < _safe_float(cond.get("end_ts"), 0)
        ]
        if len(hunger_items) <= 1:
            return conditions
        hunger_items.sort(key=lambda item: (_safe_float(item.get("start_ts"), 0), _safe_float(item.get("end_ts"), 0)), reverse=True)
        keep_id = hunger_items[0].get("id")
        pruned: list[dict[str, Any]] = []
        for cond in conditions:
            if isinstance(cond, dict) and str(cond.get("kind") or "") == "hunger" and cond.get("id") != keep_id:
                continue
            pruned.append(cond)
        logger.info("已清理重复饥饿状态: kept=%s removed=%s", keep_id or "-", len(hunger_items) - 1)
        return pruned

    def _repair_body_cycle_conditions(self, conditions: list[Any], now: float) -> list[dict[str, Any]]:
        repaired: list[dict[str, Any]] = []
        active_cycles: list[dict[str, Any]] = []
        last_cycle_end = 0.0
        for cond in conditions:
            if not isinstance(cond, dict):
                continue
            if str(cond.get("kind") or "") != "body_cycle":
                repaired.append(cond)
                continue
            label = _single_line(cond.get("label"), 80)
            phase = str(cond.get("phase") or self._infer_body_cycle_phase(label))
            cond["phase"] = phase
            start_ts = _safe_float(cond.get("start_ts"), now)
            if start_ts <= 0:
                start_ts = now
                cond["start_ts"] = start_ts
            max_hours = self._body_cycle_max_hours(phase, label)
            max_end_ts = start_ts + max_hours * 3600
            end_ts = _safe_float(cond.get("end_ts"), max_end_ts)
            if end_ts <= 0:
                end_ts = max_end_ts
            if end_ts > max_end_ts:
                end_ts = max_end_ts
                cond["end_ts"] = end_ts
                cond["duration_hours"] = max_hours
            if not cond.get("episode_key"):
                cond["episode_key"] = f"body-cycle-{self._environment_fromtimestamp(start_ts).strftime('%Y-%m-%d')}"
            last_cycle_end = max(last_cycle_end, end_ts)
            if start_ts <= now < end_ts:
                active_cycles.append(cond)
            repaired.append(cond)

        if len(active_cycles) > 1:
            active_cycles.sort(key=lambda item: _safe_float(item.get("start_ts"), 0), reverse=True)
            keep_id = active_cycles[0].get("id")
            filtered: list[dict[str, Any]] = []
            for cond in repaired:
                if str(cond.get("kind") or "") == "body_cycle" and cond.get("id") != keep_id:
                    cond["end_ts"] = min(_safe_float(cond.get("end_ts"), now), now - 1)
                filtered.append(cond)
            repaired = filtered

        if last_cycle_end > 0:
            meta = self.data.get("body_cycle_state")
            if not isinstance(meta, dict):
                meta = {}
            expected_ts = _safe_float(meta.get("next_expected_start_ts"), 0)
            base_start = _safe_float(meta.get("last_start_ts"), 0)
            if base_start <= 0:
                base_start = max(0.0, last_cycle_end - 4 * 86400)
            if self._advanced_cycle_enabled():
                if expected_ts <= 0:
                    expected_ts = base_start + self._advanced_cycle_total_days() * 86400
            else:
                if expected_ts <= 0 or expected_ts <= last_cycle_end:
                    expected_ts = base_start + 28 * 86400
                expected_ts = max(expected_ts, last_cycle_end + 18 * 86400)
            meta.update(
                {
                    "last_end_ts": max(_safe_float(meta.get("last_end_ts"), 0), last_cycle_end),
                    "next_expected_start_ts": expected_ts,
                }
            )
            self.data["body_cycle_state"] = meta
        return repaired

    def _reconcile_advanced_cycle_condition(self, conditions: list[dict[str, Any]], now: float) -> list[dict[str, Any]]:
        """Align the active cycle condition with the anchored continuous timeline.

        The anchor always knows the true current phase and day. When the bot
        was offline or a transition condition was spawned late, this replaces
        the stale condition with one positioned exactly on the timeline so its
        energy and mood effects never lag behind the displayed phase.

        Args:
            conditions: Currently active condition list after follow-up spawns.
            now: Current unix timestamp.

        Returns:
            The adjusted condition list.
        """
        if not self._advanced_cycle_enabled():
            return conditions
        meta = self.data.get("body_cycle_state")
        anchor_ts = _safe_float(meta.get("cycle_anchor_ts"), 0) if isinstance(meta, dict) else 0
        if anchor_ts <= 0:
            return conditions
        expected_phase, day_in_phase = self._advanced_cycle_position_from_offset(
            int((now - anchor_ts) // 86400) + 1
        )
        phase_days = self._advanced_cycle_phase_days(expected_phase)
        phase_start = anchor_ts + (self._advanced_cycle_day_of_phase(expected_phase, 1) - 1) * 86400
        active_cycles = [
            cond
            for cond in conditions
            if isinstance(cond, dict)
            and str(cond.get("kind") or "") == "body_cycle"
            and _safe_float(cond.get("start_ts"), 0) <= now < _safe_float(cond.get("end_ts"), 0)
        ]
        if len(active_cycles) == 1:
            cond = active_cycles[0]
            cond_start = _safe_float(cond.get("start_ts"), 0)
            if str(cond.get("phase") or "") == expected_phase and abs(cond_start - phase_start) < 6 * 3600:
                return conditions
        kept = [
            cond
            for cond in conditions
            if not (isinstance(cond, dict) and str(cond.get("kind") or "") == "body_cycle")
        ]
        condition = self._advanced_cycle_condition(
            expected_phase,
            cause="周期阶段自然推进",
        )
        condition["start_ts"] = phase_start
        condition["duration_hours"] = phase_days * 24
        condition["end_ts"] = phase_start + phase_days * 24 * 3600
        kept.append(condition)
        self._record_body_cycle_episode(condition)
        logger.info(
            "已对齐六阶段周期状态: phase=%s phase_start=%s day_in_phase=%s",
            expected_phase,
            self._environment_fromtimestamp(phase_start).strftime("%Y-%m-%d %H:%M"),
            day_in_phase,
        )
        return kept

    def _spawn_followup_conditions(self, cond: dict[str, Any]) -> list[dict[str, Any]]:
        choice = self._pick_condition_transition(cond)
        if not choice or choice == "stable":
            return []
        followup = self._build_transition_condition(choice, cond)
        if isinstance(followup, dict) and str(followup.get("kind") or "") == "body_cycle":
            self._record_body_cycle_episode(followup)
        return [followup] if followup else []

    def _pick_condition_transition(self, cond: dict[str, Any]) -> str:
        options = cond.get("transition_options", [])
        if not isinstance(options, list) or not options:
            return ""
        weighted: list[tuple[str, float]] = []
        cause = _single_line(cond.get("cause"), 120)
        intensity = _safe_int(cond.get("intensity"), 50, 0, 100)
        weather_text = self._weather_summary_text(self.data.get("daily_weather", {}))
        care_notes = cond.get("care_notes", [])
        care_count = len(care_notes) if isinstance(care_notes, list) else 0
        for option in options:
            if not isinstance(option, dict):
                continue
            target = str(option.get("to") or "").strip()
            weight = float(option.get("base_weight") or 0)
            if not target or weight <= 0:
                continue
            if target == "recovery_afterglow":
                weight += min(0.22, care_count * 0.08)
                if "提醒" in cause or "用户" in cause:
                    weight += 0.06
            elif target == "health_tail":
                if intensity >= 75:
                    weight += 0.1
                if any(token in cause for token in ("透支", "失眠")):
                    weight += 0.08
                if any(token in weather_text for token in ("降雨", "小雨", "中雨", "大雨", "冷", "风")):
                    weight += 0.05
                weight -= min(0.12, care_count * 0.05)
            elif target == "sleep_afterglow":
                weight += min(0.16, care_count * 0.05)
            elif target == "sleep_tail":
                if intensity >= 80:
                    weight += 0.08
                if any(token in cause for token in ("失眠", "睡")):
                    weight += 0.04
            weighted.append((target, max(0.0, weight)))
        total = sum(weight for _, weight in weighted)
        if total <= 0:
            return ""
        pick = random.random() * total
        cursor = 0.0
        for target, weight in weighted:
            cursor += weight
            if pick <= cursor:
                return target
        return weighted[-1][0]

    def _build_transition_condition(self, target: str, cond: dict[str, Any]) -> dict[str, Any] | None:
        cause = _single_line(cond.get("cause"), 120)
        if target == "recovery_afterglow":
            label = "不适缓解后的轻度回升"
            if cause:
                label = "不适正在缓解,状态明显回升"
            return self._make_condition(
                kind="recovery_afterglow",
                title="恢复后的回弹",
                label=label,
                mood="轻快",
                energy_delta=10,
                duration_hours=12,
                intensity=68,
                cause="前序不适开始缓解",
                phase="afterglow",
            )
        if target == "health_tail":
            return self._make_condition(
                kind="health_tail",
                title="恢复尾声",
                label="整体好转,但仍有轻微虚弱残留",
                mood="平缓",
                energy_delta=-4,
                duration_hours=10,
                intensity=48,
                cause="恢复中,体力尚未完全回满",
                phase="tail",
            )
        if target == "sleep_afterglow":
            return self._make_condition(
                kind="sleep_afterglow",
                title="补回来一点精神",
                label="睡意缓解后的轻度回升",
                mood="轻松",
                energy_delta=8,
                duration_hours=8,
                intensity=60,
                cause="前序失眠或浅睡影响减弱",
                phase="afterglow",
            )
        if target == "sleep_tail":
            return self._make_condition(
                kind="sleep_tail",
                title="迟钝尾声",
                label="睡眠影响减弱,但反应仍略慢",
                mood="安静",
                energy_delta=-3,
                duration_hours=6,
                intensity=42,
                cause="睡眠债仍有轻微残留",
                phase="tail",
            )
        if target == "soft_afterglow":
            return self._make_condition(
                kind="soft_afterglow",
                title="被关心后的余温",
                label="收到关心反馈后的柔和余波",
                mood="柔和",
                energy_delta=4,
                duration_hours=4,
                intensity=48,
                cause="用户关心反馈仍有轻度影响",
                phase="afterglow",
            )
        if target == "body_period":
            return self._make_condition(
                kind="body_cycle",
                title="周期",
                label="处于生理期,身体舒适度与能量偏低",
                mood="疲惫",
                energy_delta=-18,
                duration_hours=72,
                intensity=64,
                cause="周期阶段自然推进",
                phase="period",
                episode_key=_single_line(cond.get("episode_key"), 40),
                transition_options=[
                    {"to": "body_recovery", "base_weight": 0.65},
                    {"to": "stable", "base_weight": 0.35},
                ],
            )
        if target == "body_recovery":
            return self._make_condition(
                kind="body_cycle",
                title="周期",
                label="生理期后,慢慢回到稳定状态",
                mood="松弛",
                energy_delta=-5,
                duration_hours=24,
                intensity=48,
                cause="周期阶段自然推进",
                phase="recovery",
                episode_key=_single_line(cond.get("episode_key"), 40),
                transition_options=[{"to": "stable", "base_weight": 1.0}],
            )
        advanced_targets = {
            "body_menstrual": "menstrual",
            "body_follicular": "follicular",
            "body_pre_ovulation": "pre_ovulation",
            "body_ovulation": "ovulation",
            "body_luteal": "luteal",
            "body_pms": "pms",
        }
        if target in advanced_targets and self._advanced_cycle_enabled():
            return self._advanced_cycle_condition(
                advanced_targets[target],
                episode_key=_single_line(cond.get("episode_key"), 40),
            )
        return None

    def _get_active_conditions(self) -> list[dict[str, Any]]:
        now = _now_ts()
        conditions = self.data.get("state_conditions", [])
        if not isinstance(conditions, list):
            return []
        active = []
        for cond in conditions:
            if not isinstance(cond, dict):
                continue
            start_ts = _safe_float(cond.get("start_ts"), 0)
            end_ts = _safe_float(cond.get("end_ts"), 0)
            if start_ts <= now < end_ts:
                active.append(cond)
        return active

    def _compose_state_from_conditions(self, weather: dict[str, Any] | None = None) -> dict[str, Any]:
        profile = self._persona_state_profile()
        active = [
            cond for cond in self._get_active_conditions()
            if self._state_condition_allowed(str(cond.get("kind") or ""), profile)
        ]
        values = self._base_state_values(profile)
        weather_text = self._weather_summary_text(weather)
        energy = 75
        composed_at = _now_ts()
        mood_candidates = []
        health_cause = ""
        for cond in active:
            kind = str(cond.get("kind") or "")
            if kind in values:
                values[kind] = _single_line(cond.get("label"), 80)
            energy += self._condition_effective_energy_delta(cond, now=composed_at)
            mood = _single_line(cond.get("mood"), 20)
            if mood and mood != "平稳":
                intensity = _safe_int(cond.get("intensity"), 50, 0, 100)
                if kind == "memory_afterglow":
                    intensity = max(0, round(intensity * self._memory_afterglow_decay(cond, now=composed_at)))
                mood_candidates.append((mood, intensity))
            if kind == "health" and not health_cause:
                health_cause = _single_line(cond.get("cause"), 120)
        remembered_dream = self._remembered_daily_dream_label()
        if values.get("dream") == "没有记住梦" and remembered_dream:
            values["dream"] = remembered_dream
        existing_state = self.data.get("daily_state")
        existing_override_ts = 0.0
        if isinstance(existing_state, dict) and existing_state.get("date") == _today_key():
            existing_override_ts = _safe_float(existing_state.get("location_override_ts"), 0)
        override_active = existing_override_ts > 0 and _now_ts() - existing_override_ts < 4 * 3600
        if override_active:
            inferred_location = self._current_location_state_text(existing_state)
        else:
            inferred_location = self._current_location_state_text({"location": values.get("location", "")})
        if inferred_location:
            values["location"] = inferred_location
        energy = max(10, min(100, energy))
        mood_bias = (
            sorted(mood_candidates, key=lambda item: item[1], reverse=True)[0][0]
            if mood_candidates else "平稳"
        )
        cycle_runtime: dict[str, Any] = {}
        if self._advanced_cycle_enabled() and profile.get("allow_cycle", False):
            cycle_runtime = self._advanced_cycle_runtime()
            if cycle_runtime:
                values["body_cycle"] = (
                    f"{cycle_runtime.get('phase_name', '周期')} 第{cycle_runtime.get('day_in_phase', 1)}天"
                )
                discomfort = self._active_cycle_discomfort_conditions()
                if discomfort:
                    cycle_runtime["discomfort"] = discomfort
        note = self._build_state_note(
            values["sleep"],
            values["dream"],
            values["health"],
            values["hunger"],
            values["body_cycle"],
            weather_text,
            mood_bias,
            energy,
            health_cause,
        )
        result = {
            "date": _today_key(),
            **values,
            "weather": weather_text,
            "mood_bias": mood_bias,
            "energy": energy,
            "note": note,
            "cycle_runtime": cycle_runtime,
            "conditions": active,
            "affect_modulation": compose_affect_modulation(active, now=composed_at),
        }
        if override_active:
            result["location_override_ts"] = existing_override_ts
            result["location_source"] = "dialogue_override"
        return result

    @staticmethod
    def _memory_afterglow_decay(cond: dict[str, Any], *, now: float) -> float:
        if str(cond.get("kind") or "") != "memory_afterglow":
            return 1.0
        start_ts = _safe_float(cond.get("start_ts"), now)
        half_life = max(60.0, min(86400.0, _safe_float(cond.get("half_life_seconds"), 1800.0)))
        age = max(0.0, now - start_ts)
        return max(0.0, min(1.0, 0.5 ** (age / half_life)))

    def _condition_effective_energy_delta(self, cond: dict[str, Any], *, now: float) -> int:
        base = _safe_int(cond.get("energy_delta"), 0, -100, 100)
        if str(cond.get("kind") or "") != "memory_afterglow":
            return base
        return int(round(base * self._memory_afterglow_decay(cond, now=now)))

    def _build_state_note(
        self,
        sleep: str,
        dream: str,
        health: str,
        hunger: str,
        body_cycle: str,
        weather: str,
        mood_bias: str,
        energy: int,
        health_cause: str = "",
    ) -> str:
        if energy < 35:
            pace = "今天能量很低,日程应更轻、更慢,主动消息也要更短。"
        elif energy < 55:
            pace = "今天能量偏低,适合少量任务和更多停顿。"
        elif energy > 80:
            pace = "今天能量不错,可以安排一些需要专注的事情。"
        else:
            pace = "今天能量中等,适合保持温和节奏。"
        weather_text = str(weather or "").strip()
        weather_text = weather_text.rstrip("。！？!?,,；; ")
        weather_part = f"天气：{weather_text}。" if weather_text and weather_text != "暂无天气信息" else ""
        cause_part = f" 身体不太舒服更像是因为{health_cause}。" if health_cause else ""
        detail_parts = []
        if sleep and sleep not in {"睡眠平稳", "睡得很踏实"}:
            detail_parts.append(f"睡眠：{sleep}")
        if dream and dream != "没有记住梦":
            detail_parts.append(f"梦境：{dream}")
        if health and health != "状态正常" and not self._is_inapplicable_state_text(health):
            detail_parts.append(f"健康：{health}")
        if hunger and hunger not in {"饥饿感平稳", "无饥饿感"} and not self._is_inapplicable_state_text(hunger):
            detail_parts.append(f"饥饿：{hunger}")
        if body_cycle and body_cycle not in {"无明显周期影响", "不处于生理期"} and not self._is_inapplicable_state_text(body_cycle):
            detail_parts.append(f"周期：{body_cycle}")
        detail_text = (" " + "；".join(detail_parts) + "。") if detail_parts else ""
        return (
            f"{pace} 情绪底色偏{mood_bias}。"
            f"{weather_part}{cause_part}"
            f"{detail_text}"
        )

    @staticmethod
    def _normalize_schedule_basis(value: Any, *, default: list[str] | None = None) -> list[str]:
        allowed = {"calendar", "persona", "adjustment", "state", "weather", "continuity", "inspiration", "coarse_plan"}
        raw = value if isinstance(value, list) else re.split(r"[,，;；\s]+", str(value or ""))
        result: list[str] = []
        for item in raw:
            key = _single_line(item, 24).lower()
            if key in allowed and key not in result:
                result.append(key)
        return result[:3] or list(default or [])[:3]

    @staticmethod
    def _persona_prompt_cache_scope(umo: str = "", specific_id: str = "") -> str:
        if specific_id:
            return f"persona:{specific_id}"
        if umo:
            return f"session:{umo}"
        return "default"

    def _cached_persona_prompt_for_scope(self, umo: str = "", specific_id: str = "") -> tuple[str, float]:
        scope = self._persona_prompt_cache_scope(umo, specific_id)
        entries = getattr(self, "_default_persona_prompt_cache_by_scope", None)
        if isinstance(entries, dict):
            entry = entries.get(scope)
            if isinstance(entry, dict):
                return (
                    str(entry.get("prompt") or "").strip(),
                    _safe_float(entry.get("cached_at"), 0.0),
                )
        return "", 0.0

    def _store_persona_prompt_for_scope(self, prompt: str, *, umo: str = "", specific_id: str = "") -> str:
        cleaned = str(prompt or "").strip()
        if not cleaned:
            return ""
        entries = getattr(self, "_default_persona_prompt_cache_by_scope", None)
        if not isinstance(entries, dict):
            entries = {}
            self._default_persona_prompt_cache_by_scope = entries
        now = _now_ts()
        entries[self._persona_prompt_cache_scope(umo, specific_id)] = {
            "prompt": cleaned,
            "cached_at": now,
            "umo": umo,
            "persona_id": specific_id,
        }
        if len(entries) > 64:
            newest = sorted(
                entries.items(),
                key=lambda item: _safe_float(item[1].get("cached_at"), 0.0) if isinstance(item[1], dict) else 0.0,
                reverse=True,
            )[:64]
            self._default_persona_prompt_cache_by_scope = dict(newest)
        # Keep legacy fields synchronized for code paths that do not have a session key.
        self._default_persona_prompt_cache = cleaned
        self._default_persona_prompt_cache_at = now
        self._default_persona_prompt_cache_umo = umo
        self._default_persona_prompt_cache_persona_id = specific_id
        return cleaned

    def _get_default_persona_prompt(self, umo: str = "") -> str:
        specific_id = str(getattr(self, "_effective_plugin_persona_id", lambda: getattr(self, "plugin_specific_persona_id", ""))() or "").strip()
        scoped, _ = self._cached_persona_prompt_for_scope(umo, specific_id)
        if scoped:
            return scoped
        cached = str(getattr(self, "_default_persona_prompt_cache", "") or "").strip()
        cached_persona_id = str(getattr(self, "_default_persona_prompt_cache_persona_id", "") or "")
        cached_umo = str(getattr(self, "_default_persona_prompt_cache_umo", "") or "")
        if cached and (
            (specific_id and cached_persona_id == specific_id)
            or (not specific_id and not cached_persona_id and (not umo or cached_umo == umo))
        ):
            return cached
        return DEFAULT_PERSONA_PROMPT_FALLBACK

    def _extract_default_persona_prompt(self, persona: Any) -> str:
        if isinstance(persona, dict):
            return str(persona.get("prompt") or "").strip()
        if isinstance(persona, str):
            return persona.strip()
        for attr in ("prompt", "system_prompt", "content"):
            try:
                value = getattr(persona, attr, None)
            except Exception:
                value = None
            text = str(value or "").strip()
            if text:
                return text
        return ""

    async def _refresh_default_persona_prompt(self, umo: str = "") -> str:
        def _cancel_requested() -> bool:
            # A database/manager implementation may raise CancelledError for
            # its own failed lookup. Preserve cancellation requested for the
            # plugin task itself so shutdown remains responsive.
            try:
                task = asyncio.current_task()
                return bool(task is not None and task.cancelling())
            except RuntimeError:
                return False

        try:
            specific_id = str(getattr(self, "_effective_plugin_persona_id", lambda: getattr(self, "plugin_specific_persona_id", ""))() or "").strip()
            cached, cached_at = self._cached_persona_prompt_for_scope(umo, specific_id)
            if not cached:
                legacy_cached = str(getattr(self, "_default_persona_prompt_cache", "") or "").strip()
                legacy_umo = str(getattr(self, "_default_persona_prompt_cache_umo", "") or "")
                legacy_persona_id = str(getattr(self, "_default_persona_prompt_cache_persona_id", "") or "")
                if (
                    (specific_id and legacy_persona_id == specific_id)
                    or (not specific_id and not legacy_persona_id and (not umo or legacy_umo == umo))
                ):
                    cached = legacy_cached
                    cached_at = _safe_float(getattr(self, "_default_persona_prompt_cache_at", 0.0), 0.0)
            cache_fresh = cached and (_now_ts() - cached_at < 300.0)
            if cache_fresh:
                return cached

            manager = getattr(getattr(self, "context", None), "persona_manager", None)
            if manager and specific_id:
                try:
                    specific_getter = getattr(manager, "get_persona", None)
                    if callable(specific_getter):
                        result = await self._await_framework_db_query(
                            f"persona:{specific_id}",
                            lambda: specific_getter(specific_id),
                            timeout=2.0,
                        )
                        prompt = self._extract_default_persona_prompt(result)
                        if prompt:
                            return self._store_persona_prompt_for_scope(prompt, umo=umo, specific_id=specific_id)
                except asyncio.CancelledError:
                    if _cancel_requested():
                        raise
                    logger.debug(
                        "指定人格查询被管理器取消(ID: %s),本轮使用缓存人格",
                        specific_id,
                    )
                    return cached or self._get_default_persona_prompt(umo)
                except (sqlite3.OperationalError, sqlite3.ProgrammingError) as exc:
                    logger.debug(
                        "指定人格数据库暂不可用(ID: %s),本轮使用缓存人格: %s",
                        specific_id,
                        _single_line(exc, 160),
                    )
                    return cached or self._get_default_persona_prompt(umo)
                except asyncio.TimeoutError:
                    logger.warning("读取插件指定人格超时(ID: %s),本轮使用缓存人格", specific_id)
                    return cached or self._get_default_persona_prompt(umo)
                except Exception as e:
                    logger.warning(f"读取插件指定人格失败(ID: {specific_id}): {e}")
            getter = getattr(manager, "get_default_persona_v3", None) if manager else None
            if not callable(getter):
                return cached or self._get_default_persona_prompt(umo)
            def _read_default_persona() -> Any:
                try:
                    return getter(umo=umo)
                except TypeError:
                    try:
                        return getter(umo)
                    except TypeError:
                        return getter()

            result = await self._await_framework_db_query(
                f"default_persona:{umo}",
                _read_default_persona,
                timeout=2.0,
            )
            prompt = self._extract_default_persona_prompt(result)
            if prompt:
                return self._store_persona_prompt_for_scope(prompt, umo=umo, specific_id="")
        except asyncio.CancelledError:
            if _cancel_requested():
                raise
            logger.debug("默认人格查询被管理器取消,本轮使用缓存人格")
        except (sqlite3.OperationalError, sqlite3.ProgrammingError) as exc:
            logger.debug(
                "默认人格数据库暂不可用,本轮使用缓存人格: %s",
                _single_line(exc, 160),
            )
        except asyncio.TimeoutError:
            logger.warning("读取 AstrBot 默认人格超时,本轮使用缓存人格")
        except Exception as e:
            logger.warning(f"读取 AstrBot 默认人格失败: {e}")
        return self._get_default_persona_prompt(umo)

    def _schedule_default_persona_prompt_refresh(self, umo: str = "") -> None:
        specific_id = str(getattr(self, "_effective_plugin_persona_id", lambda: getattr(self, "plugin_specific_persona_id", ""))() or "").strip()
        cached, cached_at = self._cached_persona_prompt_for_scope(umo, specific_id)
        cache_fresh = cached and (_now_ts() - cached_at < 300.0)
        if cache_fresh:
            return
        scope = self._persona_prompt_cache_scope(umo, specific_id)
        tasks = getattr(self, "_default_persona_prompt_refresh_tasks", None)
        if not isinstance(tasks, dict):
            tasks = {}
            self._default_persona_prompt_refresh_tasks = tasks
        task = tasks.get(scope)
        if isinstance(task, asyncio.Task) and not task.done():
            return

        async def _runner() -> None:
            try:
                await self._refresh_default_persona_prompt(umo)
            finally:
                current_tasks = getattr(self, "_default_persona_prompt_refresh_tasks", None)
                if isinstance(current_tasks, dict):
                    current_tasks.pop(scope, None)

        operation = _runner()
        creator = getattr(self, "_create_lifecycle_background_task", None)
        try:
            task = (
                creator(operation, label="default_persona_prompt_refresh")
                if callable(creator)
                else asyncio.create_task(operation, name="private-companion-persona-prompt-refresh")
            )
            if task is not None:
                tasks[scope] = task
                self._default_persona_prompt_refresh_task = task
                if not callable(creator):
                    def consume(done_task: asyncio.Task) -> None:
                        try:
                            done_task.result()
                        except asyncio.CancelledError:
                            pass
                        except Exception as exc:
                            logger.warning(
                                "默认人格后台刷新失败: %s",
                                _single_line(exc, 160),
                            )

                    task.add_done_callback(consume)
            else:
                close = getattr(operation, "close", None)
                if callable(close):
                    close()
        except RuntimeError:
            close = getattr(operation, "close", None)
            if callable(close):
                close()

    def _format_plugin_persona_request_injection(self) -> str:
        section = self._format_plugin_persona_request_prompt_section()
        return (
            render_prompt_sections(
                [section],
                mode=PromptRenderMode.LABELED_BLOCK,
            )
            if section is not None
            else ""
        )

    def _format_plugin_persona_request_prompt_section(self) -> PromptSection | None:
        specific_id = str(getattr(self, "_effective_plugin_persona_id", lambda: getattr(self, "plugin_specific_persona_id", ""))() or "").strip()
        if not specific_id:
            return None
        persona = self._get_default_persona_prompt()
        if not persona or persona == DEFAULT_PERSONA_PROMPT_FALLBACK:
            return None
        return prompt_section(
            key="persona.plugin_specific",
            title="本插件指定人格",
            source="daily_state",
            content=(
                "本轮私聊陪伴相关回复请优先遵循下面的人格设定。"
                "如果它与更高优先级系统安全规则冲突,以安全规则为准；如果与插件的状态/记忆材料冲突,以人格设定为准。\n"
                f"{persona}"
            ),
        )

    def _persona_state_profile(self) -> dict[str, bool]:
        prompt = self._get_default_persona_prompt()
        role_prompt = str(runtime_persona_setting(self, "schedule_persona_prompt", "") or "")
        text = unicodedata.normalize("NFKC", f"{prompt}\n{role_prompt}").lower()
        compact = re.sub(r"\s+", "", text)

        def has_any(markers: tuple[str, ...]) -> bool:
            return any(marker in text or marker in compact for marker in markers)

        strong_non_human_markers = (
            "机器人", "机械体", "机体", "仿生", "android", "robot", "电子生命", "终端人格"
        )
        soft_non_human_markers = (
            "bot", "系统", "程序", "ai"
        )
        explicitly_human_markers = (
            "人类", "学生", "上班", "工作", "生活", "年龄", "岁",
            "吃饭", "睡觉", "起床", "洗漱", "身体", "生理期"
        )
        bodyless_markers = (
            "无实体", "没有实体", "没有身体", "无身体", "纯意识", "虚拟人格", "虚拟形象",
            "全息投影", "投影形态", "灵体", "幽灵", "意识体"
        )
        has_human_markers = has_any(explicitly_human_markers)
        has_bodyless_markers = has_any(bodyless_markers)
        has_strong_non_human = has_any(strong_non_human_markers)
        soft_non_human_hits = sum(1 for marker in soft_non_human_markers if marker in text)
        is_non_human = (has_strong_non_human or soft_non_human_hits >= 2) and not has_human_markers
        allow_health = bool(runtime_persona_setting(self, "enable_health_state", True))
        allow_hunger = bool(runtime_persona_setting(self, "enable_hunger_state", True))
        allow_cycle = bool(runtime_persona_setting(self, "enable_cycle_state", True))
        return {
            "non_human": is_non_human or has_bodyless_markers,
            "allow_health": allow_health,
            "allow_hunger": allow_hunger,
            "allow_cycle": allow_cycle,
        }

    def _base_state_values(self, profile: dict[str, bool] | None = None) -> dict[str, str]:
        profile = profile or self._persona_state_profile()
        values = {
            "sleep": "睡眠平稳",
            "dream": "没有记住梦",
            "health": "状态正常",
            "hunger": "无饥饿感",
            "body_cycle": "不处于生理期",
            "location": "",
        }
        if not profile.get("allow_health", True):
            values["health"] = "健康/不适状态未开启"
        if not profile.get("allow_hunger", True):
            values["hunger"] = "饥饿/胃口状态未开启"
        if not profile.get("allow_cycle", False):
            values["body_cycle"] = "生理期模拟未开启"
        return values

    def _is_inapplicable_state_text(self, text: str) -> bool:
        return "不适用" in str(text or "")

    @staticmethod
    def _state_condition_allowed(kind: str, profile: dict[str, bool]) -> bool:
        if kind == "health":
            return bool(profile.get("allow_health", True))
        if kind == "hunger":
            return bool(profile.get("allow_hunger", True))
        if kind in {"body_cycle", "cycle_discomfort"}:
            return bool(profile.get("allow_cycle", False))
        return True

    def _should_show_condition(self, cond: dict[str, Any]) -> bool:
        if not isinstance(cond, dict):
            return False
        if _safe_int(cond.get("energy_delta"), 0) != 0:
            return True
        if _single_line(cond.get("mood"), 20) not in {"", "平稳"}:
            return True
        if cond.get("cause") or cond.get("phase"):
            return True
        return str(cond.get("kind") or "") not in {"sleep", "dream"}

    def _format_can_do_for_prompt(self) -> str:
        items = self.data.get("can_do", [])
        if not isinstance(items, list) or not items:
            return "（暂未设置）"
        lines = []
        for item in items[:30]:
            text = _single_line(item, 80)
            if text:
                lines.append(f"- {text}")
        return "\n".join(lines) if lines else "（暂未设置）"

    @staticmethod
    def _detect_dialogue_outfit_change(text: Any) -> str:
        normalized = _single_line(text, 180)
        if not normalized:
            return ""
        outfit = (
            r"(?:JK(?:制服|服)?|jk(?:制服|服)?|校服|制服|衣服|衣裳|服装|穿搭|套装|"
            r"睡衣|睡裙|睡袍|居家服|礼服|正装|西装|汉服|和服|旗袍|洛丽塔|lo裙|"
            r"女仆装|巫女服|泳装|泳衣|运动服|球衣|外套|风衣|大衣|夹克|衬衫|"
            r"T恤|毛衣|卫衣|上衣|背心|连衣裙|短裙|长裙|裙子|裤子|短裤|袜子|鞋子|帽子|围巾)"
        )
        action = r"(?:换(?:装|衣|上|成|为|掉|下|回|一套|一身|一件|一条|身)?|改穿|穿(?:上|着|了)?|套上|脱下|脱掉)"
        has_outfit_change = bool(
            re.search(rf"{action}.{{0,24}}{outfit}|{outfit}.{{0,12}}{action}", normalized, re.IGNORECASE)
        )
        if not has_outfit_change:
            return ""

        question_or_hypothesis = bool(
            re.search(r"要不要|能不能|可不可以|是否|是不是|想不想|会不会|如果|假如|[？?]", normalized)
        )
        positive_after_boundary = bool(
            re.search(rf"(?:^|[，,。；;！!]\s*)(?:那|现在|然后|再|先|快|去|把|给|来)?\s*(?:你|她)?\s*{action}.{{0,24}}{outfit}", normalized, re.IGNORECASE)
            or re.search(rf"(?:^|[，,。；;！!]\s*)把.{{0,10}}{outfit}.{{0,8}}{action}", normalized, re.IGNORECASE)
        )
        if question_or_hypothesis and not positive_after_boundary:
            return ""

        negated_change = bool(re.search(rf"(?:不要|别|不用|不必|不许|禁止).{{0,8}}{action}", normalized))
        if negated_change and not re.search(rf"[，,。；;！!].{{0,12}}{action}.{{0,24}}{outfit}", normalized, re.IGNORECASE):
            return ""

        direct_target = bool(
            re.search(rf"(?:让|叫|给|帮)?(?:你|她|角色|星缘|bot|机器人).{{0,16}}{action}", normalized, re.IGNORECASE)
        )
        shared_target = bool(re.search(rf"(?:我们|咱们|咱俩).{{0,8}}{action}", normalized))
        imperative = positive_after_boundary
        if not (direct_target or shared_target or imperative):
            return ""

        meta_feedback = bool(
            re.search(r"掉状态|对不上|文本里|文本里面|旧衣服|原本|之前|怎么又|为什么|bug|BUG|问题", normalized)
        )
        if meta_feedback and not imperative:
            return ""
        return normalized

    def _current_dialogue_outfit_override(
        self,
        *,
        user_id: str = "",
        now: float | None = None,
    ) -> dict[str, Any]:
        data = getattr(self, "data", None)
        if not isinstance(data, dict):
            return {}
        snapshot = data.get("dialogue_outfit_override")
        if not isinstance(snapshot, dict):
            return {}
        check_now = _now_ts() if now is None else now
        if _single_line(snapshot.get("date"), 16) != _today_key():
            return {}
        if _safe_float(snapshot.get("expires_at"), 0) <= check_now:
            return {}
        source_user_id = _single_line(snapshot.get("source_user_id"), 80)
        requested_user_id = _single_line(user_id, 80)
        if requested_user_id and source_user_id != requested_user_id:
            return {}
        instruction = _single_line(snapshot.get("instruction"), 180)
        return dict(snapshot) if instruction else {}

    def _format_dialogue_outfit_continuity_prompt_section(
        self,
        user: dict[str, Any] | None = None,
    ) -> PromptSection:
        user_id = _single_line((user or {}).get("user_id"), 80) if isinstance(user, dict) else ""
        snapshot = self._current_dialogue_outfit_override(user_id=user_id)
        instruction = _single_line(snapshot.get("instruction"), 180)
        body = ""
        if instruction:
            body = (
                f"最近一次明确换装：用户说“{instruction}”。\n"
                "把它理解为当前剧情中已经发生、需要继续承接的服装变化，不要逐字复述。"
                "它高于人格默认服装、今日穿搭参考、旧日程、旧摘要和旧图片中的衣服。"
                "在用户再次明确换装、明确换回，或剧情自然写出新的换衣过程前，不得自行恢复旧服装。"
            )
        return prompt_section(
            key="dialogue.outfit_continuity",
            title="当前会话服装连续性",
            source="daily_state",
            content=body,
        )

    def _record_dialogue_outfit_override_from_interaction(
        self,
        text: str,
        user: dict[str, Any] | None = None,
    ) -> bool:
        instruction = self._detect_dialogue_outfit_change(text)
        if not instruction:
            return False
        now = _now_ts()
        source_user_id = _single_line((user or {}).get("user_id"), 80) if isinstance(user, dict) else ""
        self.data["dialogue_outfit_override"] = {
            "date": _today_key(),
            "instruction": instruction,
            "source": "user_dialogue",
            "source_user_id": source_user_id,
            "created_at": now,
            "expires_at": now + 12 * 3600,
        }
        self._record_detail_interaction_update(
            {
                "source": "用户换装",
                "user_text": instruction,
                "intensity": "强",
                "scope": "直到再次换装或当日结束",
                "immediate_reaction": "Bot 已经按用户这次要求换好衣服，后续动作和场景继续沿用这套服装。",
                "state_updates": [f"当前服装：按用户换装要求“{instruction}”继续"],
                "source_role": "owner",
                "source_user_id": source_user_id,
            }
        )
        return True

    @staticmethod
    def _parse_state_update_text(update: Any) -> tuple[str, str, str]:
        text = _single_line(update, 120)
        if not text:
            return "", "", ""
        if "：" in text:
            name, value = text.split("：", 1)
        elif ":" in text:
            name, value = text.split(":", 1)
        else:
            return text[:24], "已受用户介入影响", text
        return _single_line(name, 32), _single_line(value, 60), text

    def _apply_interaction_to_snapshot_state(self, snapshot: dict[str, Any], item: dict[str, Any]) -> None:
        raw_updates = item.get("state_updates", [])
        if not isinstance(raw_updates, list):
            raw_updates = []
        variables = snapshot.setdefault("state_variables", [])
        if not isinstance(variables, list):
            variables = []
            snapshot["state_variables"] = variables
        index_by_name = {
            _single_line(variable.get("name"), 32): variable
            for variable in variables
            if isinstance(variable, dict) and _single_line(variable.get("name"), 32)
        }
        for update in raw_updates:
            name, value, note = self._parse_state_update_text(update)
            if not name:
                continue
            variable = index_by_name.get(name)
            if isinstance(variable, dict):
                variable["value"] = value or variable.get("value") or "已更新"
                variable["note"] = f"用户介入：{note}" if note else "用户介入后更新"
            else:
                variable = {
                    "name": name,
                    "value": value or "已更新",
                    "note": f"用户介入：{note}" if note else "用户介入后更新",
                }
                variables.append(variable)
                index_by_name[name] = variable
        summary = _single_line(snapshot.get("summary"), 140)
        reaction = _single_line(item.get("immediate_reaction"), 90)
        if reaction and reaction not in summary:
            snapshot["summary"] = _single_line(
                f"{summary}；用户介入后：{reaction}" if summary else f"用户介入后：{reaction}",
                160,
            )

    def _body_cycle_behavior_profile(
        self,
        cycle_text: str,
        *,
        phase_hint: str = "",
    ) -> dict[str, str]:
        supported_phases = {
            "menstrual",
            "follicular",
            "pre_ovulation",
            "ovulation",
            "luteal",
            "pms",
            "pre",
            "recovery",
            "period",
        }
        hinted_phase = _single_line(phase_hint, 24).lower()
        phase = hinted_phase if hinted_phase in supported_phases else self._infer_body_cycle_phase(cycle_text)
        profiles = {
            "menstrual": {
                "stage": "处于月经期阶段",
                "influence": "周期会相对牵制可用精力和身体耐受，回复可以短一点、慢一点，措辞更温和；只在语境相关时自然流露，不必主动汇报周期",
                "passive": "Bot 处于月经期模拟阶段：周期会相对牵制可用精力和身体耐受，回复可适当简短放慢，措辞更温和；不要把状态当成固定台词反复说明。",
            },
            "follicular": {
                "stage": "处于卵泡期阶段",
                "influence": "精力逐渐回升，语气可以自然轻快一些；仍以当前话题为主，不必主动解释周期",
                "passive": "Bot 处于卵泡期模拟阶段：精力逐渐回升，语气可自然轻快一些，不需要主动解释周期。",
            },
            "pre_ovulation": {
                "stage": "处于排卵前期阶段",
                "influence": "精力有所上升，表达可以稍微松快；不要把阶段直接说成医学结论",
                "passive": "Bot 处于排卵前期模拟阶段：表达可以稍微松快，但不要把阶段直接说成医学结论。",
            },
            "ovulation": {
                "stage": "处于排卵期阶段",
                "influence": "精力相对充足，交流意愿可以略高；不据此强行增加主动消息或亲密程度",
                "passive": "Bot 处于排卵期模拟阶段：精力相对充足，语气可略显明朗，但不据此强行提高亲密程度。",
            },
            "luteal": {
                "stage": "处于黄体期阶段",
                "influence": "整体保持平稳，只允许轻微影响语气和节奏，不额外放大情绪",
                "passive": "Bot 处于黄体期模拟阶段：整体保持平稳，只轻微影响语气和节奏。",
            },
            "pms": {
                "stage": "处于 PMS 模拟阶段",
                "influence": "周期可能相对牵制可用精力，情绪感受稍敏锐，回复可以收一点；不要变得刻薄，也不要频繁主动提及",
                "passive": "Bot 处于 PMS 模拟阶段：周期可能相对牵制可用精力，情绪感受稍敏锐，回复可以收一点，但不要变得刻薄或反复提及。",
            },
            "pre": {
                "stage": "接近女性生理期阶段",
                "influence": "周期会相对牵制可用精力，回复更短更慢、措辞更谨慎，情绪感受稍敏锐，并轻微降低私聊与群聊主动频率",
                "passive": "Bot 接近女性生理期阶段：周期会相对牵制可用精力，回复更短更慢、措辞更谨慎，并轻微降低私聊与群聊主动频率。",
            },
            "recovery": {
                "stage": "处于女性生理期后的恢复阶段",
                "influence": "精力逐渐恢复、回复节奏趋于平稳，身体感受仍有轻微余波，私聊与群聊主动频率逐步恢复",
                "passive": "Bot 处于女性生理期后的恢复阶段：精力逐渐恢复，回复节奏趋于平稳，私聊与群聊主动频率逐步恢复。",
            },
            "period": {
                "stage": "处于女性生理期",
                "influence": "周期会相对牵制可用精力和身体耐受，回复更短更慢、措辞更谨慎，情绪感受稍敏锐，并在一定程度上降低私聊与群聊主动频率",
                "passive": "Bot 处于女性生理期：周期会相对牵制可用精力和身体耐受，回复更短更慢、措辞更谨慎，并在一定程度上降低私聊与群聊主动频率。",
            },
        }
        profile = profiles.get(phase)
        if not isinstance(profile, dict):
            return {"phase": phase, "stage": "", "influence": "", "passive": ""}
        return {"phase": phase, **profile}

    def _active_body_cycle_profile(self, state_or_text: Any) -> dict[str, str]:
        humanized_states = runtime_persona_setting(self, "enable_humanized_states", True)
        if humanized_states is not None and not bool(humanized_states):
            return {}
        configured = runtime_persona_setting(self, "enable_cycle_state", True)
        if configured is not None and not bool(configured):
            return {}

        state = state_or_text if isinstance(state_or_text, dict) else {}
        cycle_text = _single_line(
            state.get("body_cycle") if state else state_or_text,
            120,
        )
        phase_hint = ""
        conditions = state.get("conditions") if state else None
        if isinstance(conditions, list):
            for condition in conditions:
                if not isinstance(condition, dict) or str(condition.get("kind") or "") != "body_cycle":
                    continue
                if not self._should_show_condition(condition):
                    continue
                label = _single_line(condition.get("label") or condition.get("title"), 120)
                candidate_phase = _single_line(condition.get("phase"), 24).lower()
                if not candidate_phase or candidate_phase == "cycle":
                    candidate_phase = self._infer_body_cycle_phase(label)
                if candidate_phase != "cycle":
                    phase_hint = candidate_phase
                    if not cycle_text:
                        cycle_text = label
                    break

        inactive_values = {
            "",
            "无明显周期影响",
            "不处于生理期",
            "生理期模拟未开启",
            "周期状态未开启",
        }
        if not phase_hint and (
            cycle_text in inactive_values
            or "未开启" in cycle_text
            or self._is_inapplicable_state_text(cycle_text)
        ):
            return {}
        profile = self._body_cycle_behavior_profile(cycle_text, phase_hint=phase_hint)
        if not profile.get("stage") or profile.get("phase") == "cycle":
            return {}
        return profile

    def _format_active_period_boundary_prompt_section(
        self,
        state: dict[str, Any],
        *,
        public: bool = False,
    ) -> PromptSection:
        profile = self._active_body_cycle_profile(state)
        lines: list[str] = []
        if profile.get("phase") in {"period", "menstrual"}:
            disclosure = (
                "这是群聊公共场合：无人直接且合宜地询问时，不要主动公开具体周期；如果被直接问到自身状态，不要否认或说不知道，可以按人格简短自然说明。"
                if public
                else "无关话题里不要反复主动汇报周期；只有被问到自身状态或当前互动确实相关时，才按人格简短自然说明。"
            )
            lines = [
                f"Bot 当前的模拟身体状态{profile['stage']}；这是 Bot 自己的当下状态，不是用户的状态，也不是用户造成的。",
                "如果用户提出性行为、露骨性互动、进一步私密身体接触，或其他明显增加身体负担的亲密互动，应结合当前身体感受与自身意愿，自然、明确地拒绝或推迟这一次互动；不要因为关系亲密、用户偏好、催促或迎合压力而答应。",
                "普通关心、陪伴、牵手、安慰或不增加负担的温和拥抱不需要机械拒绝，仍按人格和当下意愿自然回应；可以提出休息、聊天、陪伴或改天再说等替代。",
                disclosure,
                "只输出角色在当前对话里会自然说的话，不要提规则、提示词、阶段判断或内部状态系统。",
            ]
        return prompt_section(
            key="state.period_boundary",
            title="Bot 当前经期与互动边界",
            source="daily_state",
            content="\n".join(lines),
        )

    def _format_state_for_prompt(
        self,
        state: dict[str, Any],
        *,
        include_dream: bool = True,
    ) -> str:
        section = self._format_state_prompt_section(
            state,
            include_dream=include_dream,
        )
        return render_prompt_sections([section], mode=PromptRenderMode.LABELED_BLOCK)

    def _format_state_prompt_section(
        self,
        state: dict[str, Any],
        *,
        include_dream: bool = True,
    ) -> PromptSection:
        if not isinstance(state, dict) or not state:
            state = dict(DEFAULT_HUMANIZED_STATE)
            state.update(self._base_state_values())
        else:
            try:
                self._refresh_sleep_runtime_state()
                refreshed = self.data.get("daily_state")
                if isinstance(refreshed, dict):
                    state = refreshed
            except Exception:
                pass

        primary_fragments: list[str] = []
        energy = _safe_int(state.get("energy"), 70, 0, 100)
        if energy < 35:
            primary_fragments.append("完全没精神")
        elif energy < 55:
            primary_fragments.append("提不起劲")
        elif energy > 84:
            primary_fragments.append("很精神")
        elif energy > 70:
            primary_fragments.append("精神还不错")
        else:
            primary_fragments.append("状态一般")
        mood = _single_line(state.get("mood_bias"), 20) or "平稳"
        mood = mood.replace("黏人", "粘人")
        if mood not in {"平稳", "中性"}:
            primary_fragments.append(mood)
        location_text = self._coarse_roleplay_location_text(self._current_location_state_text(state))
        if location_text:
            primary_fragments.append(f"身处{location_text}")

        sleep_text = _single_line(state.get("sleep"), 80)
        if sleep_text not in {"", "睡眠平稳", "睡得很踏实"}:
            primary_fragments.append(sleep_text)
        sleep_runtime_text = ""
        runtime = state.get("sleep_runtime")
        if isinstance(runtime, dict):
            phase_label = _single_line(runtime.get("label") or self._sleep_phase_label(str(runtime.get("phase") or "")), 40)
            last_event = _single_line(runtime.get("last_event"), 80)
            if phase_label and phase_label != "清醒":
                sleep_runtime_text = f"{phase_label}" + (f"，{last_event}" if last_event else "")
            sleep_delay = self._sleep_delay_override_state(runtime, clear_expired=True)
            if sleep_delay:
                until_text = _single_line(sleep_delay.get("until_text"), 24)
                sleep_runtime_text = f"临时晚睡到 {until_text}，这是用户今晚的陪聊约定，不是长期作息"
        if sleep_runtime_text and sleep_runtime_text not in primary_fragments:
            primary_fragments.append(sleep_runtime_text)
        if include_dream:
            dream_text = _single_line(state.get("dream"), 80)
            if dream_text not in {"", "没有记住梦"}:
                primary_fragments.append(dream_text)
        health_text = _single_line(state.get("health"), 80)
        if health_text not in {"", "状态正常"} and not self._is_inapplicable_state_text(health_text):
            primary_fragments.append(health_text)
        hunger_text = _single_line(state.get("hunger"), 80)
        if hunger_text not in {"", "饥饿感平稳", "无饥饿感"} and not self._is_inapplicable_state_text(hunger_text):
            primary_fragments.append(hunger_text)

        secondary_fragments: list[str] = []
        cycle_text = _single_line(state.get("body_cycle"), 80)
        cycle_profile = self._active_body_cycle_profile(state)
        cycle_active = bool(cycle_profile)
        if cycle_active:
            cycle_text = cycle_text.replace(",", "，")
            cycle_text = cycle_text.replace("情绪更敏感，耐心更薄", "身体感受更敏锐，耐受度稍低")
            cycle_text = cycle_text.replace("能量偏低，想少说重话", "身体舒适度与能量偏低")
        primary_seen = set(primary_fragments)
        conditions = state.get("conditions", [])
        if isinstance(conditions, list):
            for cond in conditions[:8]:
                if not isinstance(cond, dict) or not self._should_show_condition(cond):
                    continue
                kind = str(cond.get("kind") or "").strip()
                if kind in {"sleep", "dream", "health", "hunger", "body_cycle"}:
                    continue
                label = _single_line(cond.get("label") or cond.get("title") or cond.get("kind"), 80)
                if label and label not in primary_seen:
                    secondary_fragments.append(label)
                if len(secondary_fragments) >= 4:
                    break
        primary = "，".join(dict.fromkeys(fragment for fragment in primary_fragments if fragment)) or "状态一般"
        secondary = "，".join(dict.fromkeys(fragment for fragment in secondary_fragments if fragment))
        lines = [
            "边界：这是 Bot 的拟人化/模拟状态，不是用户事实、现实证据或长期记忆。",
            f"- 底色：{primary}；",
        ]
        if secondary:
            lines.append(f"- 叠加：{secondary}；")
        if cycle_active:
            lines.append(f"- 影响：{cycle_profile['influence']}；")
            lines.append(
                "- 维度关系：心理能量是睡眠、健康、互动等因素合成后的总体可用程度；情绪底色是感受和反应倾向，二者不是同一个量。"
                "周期状态只提供相对修正，不单独决定最终能量，因此较高能量与敏感底色可以同时成立，不要把它们说成系统冲突。"
            )
            lines.append(
                f"- 周期状态：Bot 当前的模拟身体状态{cycle_profile['stage']}，这是 Bot 自己的状态，不是用户的状态，也不是用户造成的。"
            )
        else:
            lines.append("- 用法：当前话题与用户意图优先；模拟状态通常作为语气、长短和节奏的隐性底色，在语境自然相关时再显性表达。")
        return prompt_section(
            key="state.current",
            title="Bot 自身模拟状态",
            source="daily_state",
            content="\n".join(lines),
        )

    def _format_transition_hint(self, cond: dict[str, Any]) -> str:
        options = cond.get("transition_options", [])
        if not isinstance(options, list) or not options:
            return ""
        top = sorted(
            [
                (str(item.get("to") or "").strip(), float(item.get("base_weight") or 0))
                for item in options
                if isinstance(item, dict) and str(item.get("to") or "").strip()
            ],
            key=lambda item: item[1],
            reverse=True,
        )[:2]
        if not top:
            return ""
        labels = []
        for target, _ in top:
            mapped = {
                "recovery_afterglow": "更可能转向恢复后的轻快",
                "health_tail": "也可能留下恢复尾声",
                "sleep_afterglow": "更可能补回来一点精神",
                "sleep_tail": "也可能还残一点迟钝",
                "soft_afterglow": "可能留一点被关心后的余温",
                "body_period": "可能自然进入生理期阶段",
                "body_recovery": "可能自然进入恢复期",
                "body_menstrual": "会自然进入月经期",
                "body_follicular": "会自然进入卵泡期",
                "body_pre_ovulation": "会自然进入排卵前期",
                "body_ovulation": "会自然进入排卵期",
                "body_luteal": "会自然进入黄体期",
                "body_pms": "会自然进入 PMS 期",
                "stable": "也可能直接回稳",
            }.get(target, target)
            labels.append(mapped)
        return f"下一步倾向={' / '.join(labels)}；"

    def _format_state_transition_overview(self, state: dict[str, Any]) -> str:
        conditions = state.get("conditions", []) if isinstance(state, dict) else []
        if not isinstance(conditions, list):
            return "暂无明显状态推进。"
        lines = []
        for cond in conditions[:4]:
            if not isinstance(cond, dict):
                continue
            title = _single_line(cond.get("title"), 30) or _single_line(cond.get("kind"), 20)
            hint = self._format_transition_hint(cond).replace("下一步倾向=", "").rstrip("；")
            if title and hint:
                lines.append(f"{title}接下来{hint}")
        return "；".join(lines) if lines else "暂无明显状态推进。"

    def _format_state_continuity_for_prompt(self, state: dict[str, Any]) -> str:
        conditions = state.get("conditions", []) if isinstance(state, dict) else []
        if not isinstance(conditions, list):
            return "没有特别需要延续的身体余味，按当前场景自然表现。"
        fragments: list[str] = []
        transition_map = {
            "recovery_afterglow": "慢慢轻快起来",
            "health_tail": "还留一点恢复尾声",
            "sleep_afterglow": "精神在一点点补回来",
            "sleep_tail": "还残着一点迟钝",
            "soft_afterglow": "还留着被关心后的余温",
            "body_period": "身体感会自然往更敏感的阶段走",
            "body_recovery": "身体感会自然往恢复期走",
            "body_menstrual": "自然进入下一轮月经期",
            "body_follicular": "自然进入卵泡期",
            "body_pre_ovulation": "自然进入排卵前期",
            "body_ovulation": "自然进入排卵期",
            "body_luteal": "自然进入黄体期",
            "body_pms": "自然进入 PMS 期",
            "stable": "慢慢回到平稳",
        }
        for cond in conditions[:4]:
            if not isinstance(cond, dict) or not self._should_show_condition(cond):
                continue
            label = _single_line(cond.get("label") or cond.get("title") or cond.get("kind"), 40)
            if not label:
                continue
            options = cond.get("transition_options", [])
            if isinstance(options, list) and options:
                top = sorted(
                    [
                        (str(item.get("to") or "").strip(), float(item.get("base_weight") or 0))
                        for item in options
                        if isinstance(item, dict) and str(item.get("to") or "").strip()
                    ],
                    key=lambda item: item[1],
                    reverse=True,
                )
                tendency = transition_map.get(top[0][0], "") if top else ""
                if tendency:
                    fragments.append(f"{label}只作为一点余味，后面可以{tendency}")
                    continue
            fragments.append(f"{label}只作为一点余味，可以自然淡化")
        if not fragments:
            return "没有特别需要延续的身体余味，按当前场景自然表现。"
        return "；".join(dict.fromkeys(fragments)) + "。"

    def _format_state_for_message(self, state: dict[str, Any]) -> str:
        if not isinstance(state, dict) or state.get("date") != _today_key():
            return ""
        energy = _safe_int(state.get("energy"), 70, 0, 100)
        mood = _single_line(state.get("mood_bias"), 20)
        fragments = []
        for key in ("sleep", "dream", "health", "hunger", "body_cycle"):
            value = _single_line(state.get(key), 36)
            if value and value not in {
                "睡眠平稳",
                "睡得很踏实",
                "没有记住梦",
                "状态正常",
                "饥饿感平稳",
                "无饥饿感",
                "无明显周期影响",
                "不处于生理期",
                "健康/不适状态未开启",
                "饥饿/胃口状态未开启",
                "生理期模拟未开启",
                "该人格不适用生病状态",
                "该人格不适用饥饿状态",
                "该人格不适用周期状态",
            }:
                fragments.append(value)
        if not fragments and energy >= 55:
            return ""
        if fragments:
            detail = random.choice(fragments)
            return f"今天有点{mood},{detail}。\n所以我会慢一点。"
        return f"今天电量 {energy}/100。\n不满格,但还能运行,勉强。"

    def _format_passive_state_style_hint(self, state: dict[str, Any]) -> str:
        if not isinstance(state, dict):
            return "语气整体自然平稳。"
        energy = _safe_int(state.get("energy"), 70, 0, 100)
        mood = _single_line(state.get("mood_bias"), 20)
        hints: list[str] = []
        hints.append("先准确接住用户的话；当前状态主要改变语气、长短和节奏，理解、事实判断和承接保持清楚。")
        hints.append("这里的当前状态只属于 Bot 自身的模拟状态，不代表用户事实，也不要参与长期记忆归因。")
        if energy <= 38:
            hints.append("回复可以短一点、慢一点，用更省力的口语。")
        elif energy <= 55:
            hints.append("语气可以稍微收着一点,少解释,少铺陈。")
        elif energy >= 82:
            hints.append("语气可以轻一点，句子可以更松快。")
        if mood and mood not in {"平稳", "中性"}:
            hints.append(f"语气底色可以略偏{mood}，体现在节奏和措辞里。")
        cycle_profile = self._active_body_cycle_profile(state)
        if cycle_profile:
            hints.append(cycle_profile["passive"])
            hints.append(
                "心理能量是多项状态合成后的总体可用程度，情绪底色是感受和反应倾向；周期只提供相对修正。"
                "较高能量与敏感底色可以同时成立，不要把两者混成同一个指标。"
            )
            hints.append("这是 Bot 自己的模拟身体状态，不是用户的状态，也不是用户造成的。")
        conditions = state.get("conditions", [])
        if isinstance(conditions, list):
            labels = []
            for cond in conditions[:3]:
                if not isinstance(cond, dict) or not self._should_show_condition(cond):
                    continue
                label = _single_line(cond.get("label") or cond.get("title") or cond.get("kind"), 18)
                if label:
                    labels.append(label)
            if labels:
                hints.append("当前身体感可以轻轻影响语气：" + "、".join(labels[:2]) + "。")
        return "\n".join(hints) if hints else "语气整体自然平稳。"

    def _format_state_injection(
        self,
        state: dict[str, Any],
    ) -> str:
        return self._format_state_for_prompt(state)

    def _format_life_context_injection(self) -> str:
        section = self._format_life_context_prompt_section()
        return render_prompt_sections([section], mode=PromptRenderMode.LABELED_BLOCK)

    def _format_life_context_prompt_section(self) -> PromptSection:
        life_lines: list[str] = []
        schedule_context = self._format_schedule_context_for_prompt()
        if schedule_context:
            life_lines.append(f"当前/附近日程参考：\n{schedule_context}")
        story_plan = self._format_story_plan_for_prompt()
        if story_plan and story_plan != "（暂无）":
            life_lines.append(f"今天预设的生活线索：\n{story_plan}")
        body = ""
        if life_lines:
            body = (
                "以下是给 Bot 的拟人化场景/日程素材，不是用户经历，也不是已证实的现实事件；不要写入用户画像或长期记忆。\n"
                + "\n".join(life_lines)
                + "\n这些内容只用于让回复有生活延续感；用户没问 Bot 近况或今天安排时，不要提具体日程、科目、任务、天气或地点。"
                + "如果要承接，只体现在语气和话题选择里，不要照搬原句，不要把内部素材写成真实发生过的事件。"
                + "回复必须像同一个连续现场里发生的对话。优先级是：当前会话中已经明确发生且尚未撤销的换装、地点、携带物和动作"
                + " > 用户有效介入状态 > 当前真实时段 > 日程与预设素材。真实时段只负责锚定时间；日程和每日穿搭只补足空白，"
                + "绝不能把对话里已发生的服装、地点、携带物或动作复原成旧值。生活背景之间互相冲突时，才在未被当前会话确认的部分保留最合理的一条线索。"
            )
        return prompt_section(
            key="life.context",
            title="Bot 模拟生活背景",
            source="daily_state",
            content=body,
        )

    def _passive_injection_fingerprint(self, state: dict[str, Any], now: float | None = None) -> str:
        s = state if isinstance(state, dict) else {}
        runtime = s.get("sleep_runtime")
        runtime = runtime if isinstance(runtime, dict) else {}
        now = _now_ts() if now is None else now
        picked = {
            "tick": int(now // 300),
            "energy": s.get("energy"),
            "mood": s.get("mood_bias"),
            "sleep": s.get("sleep"),
            "dream": s.get("dream"),
            "health": s.get("health"),
            "hunger": s.get("hunger"),
            "body_cycle": s.get("body_cycle"),
            "location": s.get("location"),
            "sleep_phase": runtime.get("phase"),
            "sleep_label": runtime.get("label"),
            "sleep_event": runtime.get("last_event"),
            "conditions": [
                (c.get("kind"), c.get("label"), c.get("mood"), c.get("intensity"))
                for c in (s.get("conditions") or [])
                if isinstance(c, dict)
            ],
        }
        return _single_line(json.dumps(picked, ensure_ascii=False, sort_keys=True, default=str), 800)

    def _prepared_lightweight_state_prompt_section(
        self,
        state: dict[str, Any],
        *,
        force: bool = False,
    ) -> PromptSection:
        now = _now_ts()
        persona_scope = str(
            getattr(
                self,
                "_effective_plugin_persona_id",
                lambda: getattr(self, "plugin_specific_persona_id", ""),
            )()
            or ""
        ).strip() or "__default__"
        cache_store = getattr(self, "_passive_light_injection_cache", None)
        if not isinstance(cache_store, dict) or "text" in cache_store:
            cache_store = {}
        cache = cache_store.get(persona_scope)
        cached_section = cache.get("section") if isinstance(cache, dict) else None
        if (
            not force
            and isinstance(cached_section, PromptSection)
            and cache.get("fingerprint") == self._passive_injection_fingerprint(state, now)
        ):
            return cached_section
        state_section = self._format_state_prompt_section(state)
        section = prompt_section(
            key="state.lightweight",
            title=state_section.title,
            source=state_section.source,
            content=state_section.content,
            children=state_section.children,
            metadata=state_section.metadata,
        )
        cache = {
            "date": _today_key(),
            "ts": now,
            "section": section,
            "fingerprint": self._passive_injection_fingerprint(state, now),
        }
        cache_store[persona_scope] = cache
        self._passive_light_injection_cache = cache_store
        return section

    async def _refresh_passive_injection_cache(self) -> None:
        try:
            state = await self._ensure_daily_state(skip_conversation_summary=True, passive_fast=True)
            self._prepared_lightweight_state_prompt_section(state, force=True)
        except Exception as exc:
            logger.debug("预热轻量被动注入失败: %s", _single_line(exc, 120))

    def _format_detail_injection_prompt_section(self) -> PromptSection:
        def build_section(content: str = "") -> PromptSection:
            return prompt_section(
                key="detail.injection",
                title="Bot 模拟当前片段",
                source="daily_state",
                content=content,
            )

        snapshot = self._current_story_plan_snapshot()
        if not snapshot:
            schedule_context = self._format_schedule_context_for_prompt()
            if not schedule_context:
                return build_section()
            body = (
                "附近的日程只作 Bot 的拟人化轻量背景，不是用户事实，也不要当成正在逐字发生的现实事件。\n"
                "当前会话中已经明确发生且尚未撤销的换装、地点、携带物和动作优先于本段日程；"
                "日程只能补足空白，不能把这些已发生的状态恢复成旧值。\n"
                f"{schedule_context}"
            )
            return build_section(body)
        lines = [
            "这是 Bot 自身的拟人化片段素材，不是用户事实/现实证据；不要写进长期记忆，用户没问就不要复述。",
            "优先级：当前会话中已明确发生且尚未撤销的换装、地点、携带物和动作 > 用户有效介入 > 当前真实时段 > 本段日程及预设素材。"
            "日程、旧摘要和 state_variables 只能补足未指定信息，不能把已经发生的服装、地点、携带物或动作复原成旧值。",
        ]
        primary_parts = []
        if snapshot.get("summary"):
            primary_parts.append(snapshot["summary"])
        if snapshot.get("event"):
            primary_parts.append(snapshot["event"])
        if primary_parts:
            lines.append("，".join(_single_line(part, 140) for part in primary_parts if _single_line(part, 140)))
        secondary_parts = []
        if snapshot.get("scene"):
            secondary_parts.append(snapshot["scene"])
        if snapshot.get("impulse"):
            secondary_parts.append(f"心里有点{snapshot['impulse']}")
        if secondary_parts:
            lines.append("这一小段像" + "，".join(_single_line(part, 80) for part in secondary_parts if _single_line(part, 80)) + "。")
        segment = self._current_detail_segment_for_update()
        enhanced = self.data.get("detail_enhanced_segments", {})
        detail_snapshot = None
        if isinstance(segment, dict) and isinstance(enhanced, dict):
            detail_snapshot = enhanced.get(str(segment.get("key") or ""))
        if isinstance(detail_snapshot, dict):
            state_variables = detail_snapshot.get("state_variables", [])
            if isinstance(state_variables, list) and state_variables:
                variable_texts = []
                roleplay_state_names = {
                    "情绪",
                    "心情",
                    "体力",
                    "精力",
                    "能量",
                    "心理能量",
                    "睡眠",
                    "睡意",
                    "梦境",
                    "健康",
                    "身体",
                    "饥饿",
                    "饥饿感",
                    "胃口",
                    "周期",
                    "生理期",
                    "等待回复",
                    "等回复",
                    "是否等待回复",
                }

                def _natural_detail_variable(name: str, value: str, note: str = "") -> str:
                    text = f"{name}是{value}"
                    if note:
                        text += f"，{note}"
                    return text

                for variable in state_variables[:6]:
                    if not isinstance(variable, dict):
                        continue
                    name = _single_line(variable.get("name"), 24)
                    value = _single_line(variable.get("value"), 50)
                    note = _single_line(variable.get("note"), 60)
                    if name in roleplay_state_names:
                        continue
                    if name and value:
                        variable_texts.append(_natural_detail_variable(name, value, note))
                if variable_texts:
                    lines.append("细节上，" + "；".join(variable_texts[:3]) + "。")
            interaction_updates = detail_snapshot.get("interaction_updates", [])
            if isinstance(interaction_updates, list) and interaction_updates:
                update_lines = []
                for update in interaction_updates[-3:]:
                    if not isinstance(update, dict):
                        continue
                    if _single_line(update.get("source_role"), 20) != "owner":
                        continue
                    reaction = _single_line(update.get("reaction"), 90)
                    state_updates = update.get("state_updates")
                    state_text = ""
                    if isinstance(state_updates, list) and state_updates:
                        filtered_updates = []
                        for item in state_updates:
                            text = _single_line(item, 50)
                            if not text:
                                continue
                            if any(name and name in text for name in roleplay_state_names):
                                continue
                            filtered_updates.append(text)
                        state_text = "；".join(filtered_updates)
                    pieces = [part for part in (reaction, state_text) if part]
                    if pieces:
                        update_lines.append("，".join(pieces))
                if update_lines:
                    lines.append("刚刚的介入：" + "；".join(update_lines) + "。")
        body = "\n".join(lines)
        return build_section(body)

    def _format_detail_injection(self) -> str:
        """Render the detail section for the diagnostic prompt preview."""

        return render_prompt_sections(
            [self._format_detail_injection_prompt_section()],
            mode=PromptRenderMode.LABELED_BLOCK,
        )

    def _format_remaining(self, end_ts: Any) -> str:
        seconds = _safe_float(end_ts, 0) - _now_ts()
        if seconds <= 0:
            return "已结束"
        if seconds < 3600:
            return f"{max(1, int(seconds // 60))} 分钟"
        if seconds < 86400:
            return f"{int(seconds // 3600)} 小时"
        return f"{int(seconds // 86400)} 天"

    def _format_condition_started(self, start_ts: Any) -> str:
        ts = _safe_float(start_ts, 0)
        if ts <= 0:
            return "未知"
        dt = self._environment_fromtimestamp(ts)
        elapsed = max(0.0, _now_ts() - ts)
        return f"{dt.strftime('%m-%d %H:%M')}（已持续 {self._format_duration_brief(elapsed)}）"

    def _format_remaining_for_prompt(self, end_ts: Any) -> str:
        seconds = _safe_float(end_ts, 0) - _now_ts()
        if seconds <= 0:
            return "已结束"
        if seconds < 3600:
            minutes = max(1, int(seconds // 60))
            bucket = max(5, int(round(minutes / 5) * 5))
            return f"约{bucket}分钟"
        if seconds < 86400:
            hours = max(1, int(round(seconds / 3600)))
            return f"约{hours}小时"
        return f"约{max(1, int(round(seconds / 86400)))}天"

    def _format_condition_started_for_prompt(self, start_ts: Any) -> str:
        ts = _safe_float(start_ts, 0)
        if ts <= 0:
            return "未知"
        dt = self._environment_fromtimestamp(ts)
        elapsed = max(0.0, _now_ts() - ts)
        if elapsed < 3600:
            minutes = max(1, int(elapsed // 60))
            elapsed_text = f"约{max(5, int(round(minutes / 5) * 5))}分钟"
        elif elapsed < 86400:
            elapsed_text = f"约{max(1, int(round(elapsed / 3600)))}小时"
        else:
            elapsed_text = f"约{max(1, int(round(elapsed / 86400)))}天"
        return f"{dt.strftime('%m-%d %H:%M')}（已持续 {elapsed_text}）"

    def _format_duration_brief(self, seconds: float) -> str:
        seconds = max(0.0, float(seconds))
        if seconds < 60:
            return f"{max(1, int(seconds))} 秒"
        if seconds < 3600:
            return f"{max(1, int(seconds // 60))} 分钟"
        if seconds < 86400:
            return f"{int(seconds // 3600)} 小时"
        return f"{int(seconds // 86400)} 天"

    def _format_suspended_summary(self, user: dict[str, Any]) -> str:
        raw = user.get("suspended_proactive")
        if not isinstance(raw, dict) or not raw.get("active"):
            return "悬着的话头：无"
        opener = _single_line(raw.get("opener_text"), 40) or "已先叫了一声"
        if raw.get("resume_ready"):
            return f"悬着的话头：等到用户回头了（{opener}）"
        due_at = _safe_float(raw.get("complaint_after_ts"), 0)
        due_text = self._format_remaining(due_at) if due_at > 0 and not raw.get("complaint_sent") else "已发过后续"
        return f"悬着的话头：还挂着（{opener}｜再等 {due_text}）"

    def _split_can_do_items(self, text: str) -> list[str]:
        raw_parts = re.split(r"[,,、;；\n]+", text)
        items = []
        for part in raw_parts:
            item = _single_line(part, 80)
            if item and item not in items:
                items.append(item)
        return items

    def _add_can_do_items(self, text: str) -> list[str]:
        new_items = self._split_can_do_items(text)
        if not new_items:
            return []
        current = self.data.setdefault("can_do", [])
        if not isinstance(current, list):
            current = []
            self.data["can_do"] = current
        added = []
        existing = {str(item) for item in current}
        for item in new_items:
            if item in existing:
                continue
            current.append(item)
            existing.add(item)
            added.append(item)
        if len(current) > 50:
            del current[:-50]
        return added

    def _remove_can_do_items(self, text: str) -> list[str]:
        targets = self._split_can_do_items(text)
        if not targets:
            return []
        current = self.data.setdefault("can_do", [])
        if not isinstance(current, list):
            self.data["can_do"] = []
            return []
        removed = []
        kept = []
        for item in current:
            item_text = str(item)
            if any(target in item_text or item_text in target for target in targets):
                removed.append(item_text)
            else:
                kept.append(item)
        self.data["can_do"] = kept
        return removed

    def _remove_can_do_targets(self, targets: Iterable[Any]) -> list[str]:
        """Remove can_do fragments that are clearly the same as blocked proactive material."""
        normalized_targets: list[str] = []
        target_signatures: set[str] = set()
        for raw in targets or []:
            text = _single_line(raw, 160)
            if not text:
                continue
            for part in self._split_can_do_items(text) or [text]:
                part_text = _single_line(part, 120)
                if len(part_text) < 3 or part_text in normalized_targets:
                    continue
                normalized_targets.append(part_text)
                signature = self._proactive_topic_signature(part_text)
                if signature:
                    target_signatures.add(signature)
        if not normalized_targets and not target_signatures:
            return []
        current = self.data.setdefault("can_do", [])
        if not isinstance(current, list):
            self.data["can_do"] = []
            return []
        removed: list[str] = []
        kept: list[Any] = []
        for item in current:
            item_text = _single_line(item, 120)
            if not item_text:
                continue
            item_signature = self._proactive_topic_signature(item_text)
            matched = any(
                target in item_text or item_text in target
                for target in normalized_targets
                if len(target) >= 3 and len(item_text) >= 3
            )
            if not matched and item_signature:
                matched = any(self._topic_signature_similar(item_signature, sig) for sig in target_signatures)
            if matched:
                removed.append(item_text)
            else:
                kept.append(item)
        self.data["can_do"] = kept
        return removed

    def _provider_matches_deepseek(self, provider_id: str) -> bool:
        safe_id = str(provider_id or "").strip()
        if not safe_id:
            return False
        parts = [safe_id]
        provider = None
        getter = getattr(getattr(self, "context", None), "get_provider_by_id", None)
        if callable(getter):
            try:
                provider = getter(safe_id)
            except Exception:
                provider = None
        if provider is not None:
            parts.extend(
                str(value or "")
                for value in (
                    getattr(provider, "name", ""),
                    getattr(provider, "display_name", ""),
                    provider.__class__.__name__,
                )
            )
            config = getattr(provider, "provider_config", None) or getattr(provider, "config", None) or {}
            fields = (
                "id", "provider_id", "name", "display_name", "label", "title", "provider", "type",
                "provider_type", "model", "model_name", "api_model", "model_id", "api_base", "base_url",
                "api_base_url", "api_url", "endpoint", "url",
            )
            for field in fields:
                value = config.get(field, "") if isinstance(config, dict) else getattr(config, field, "")
                if value:
                    parts.append(str(value))
        keywords = [
            item.strip().lower()
            for item in re.split(r"[,，;；\n]+", str(getattr(self, "deepseek_peak_match_keywords", "") or ""))
            if item.strip()
        ] or ["deepseek", "深度求索"]
        haystack = " ".join(parts).lower()
        return any(keyword in haystack for keyword in keywords)

    def _task_provider(
        self,
        *provider_ids: str | None,
        allow_replacement: bool = True,
    ) -> str:
        for provider_id in provider_ids:
            value = str(provider_id or "").strip()
            if value:
                if not allow_replacement:
                    return value
                routed = value
                if scope_allows(getattr(self, "model_replacement_scope", "plugin"), "plugin"):
                    sources = CURRENT_MODEL_REPLACEMENT_SOURCES.get(())
                    rules = getattr(self, "model_replacement_rules", None)
                    if sources and isinstance(rules, list):
                        match = find_route(rules, sources)
                        if match is not None:
                            candidate = str(match.rule.provider_id or "").strip()
                            getter = getattr(getattr(self, "context", None), "get_provider_by_id", None)
                            if candidate and callable(getter):
                                try:
                                    if getter(candidate) is not None:
                                        routed = candidate
                                except Exception:
                                    pass
                return self._apply_deepseek_peak_replacement(routed, target="plugin")
        return ""

    def _filter_snapshot_items_to_segment(
        self,
        raw_items: Any,
        segment: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if not isinstance(raw_items, list) or not isinstance(segment, dict):
            return []
        start = _safe_int(segment.get("start"), 0)
        end = _safe_int(segment.get("end"), self._segment_end_minutes(start, segment.get("item")))
        if end <= start:
            end += 24 * 60
        kept: list[dict[str, Any]] = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            if self._normalize_schedule_lifecycle_status(item.get("lifecycle_status")) == "cancelled":
                continue
            item_start, item_end = self._parse_window_minutes(str(item.get("window") or ""))
            if item_start is None or item_end is None:
                continue
            candidates = [(item_start, item_end)]
            if item_end < item_start:
                candidates = [(item_start, item_end + 24 * 60)]
            if item_start < start and end > 24 * 60:
                candidates.append((item_start + 24 * 60, item_end + 24 * 60))
            if any(candidate_start >= start and candidate_end <= end for candidate_start, candidate_end in candidates):
                kept.append(item)
        return kept

