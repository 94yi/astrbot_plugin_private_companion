# -*- coding: utf-8 -*-
"""宿主私有辅助设施（从 main.py 提升而来，供宿主与各域 mixin 共用）。

只放 **被多个模块共同依赖** 的模块级辅助件：单人设事件上下文装饰器及其
实例判定依赖。放在独立模块可避免 mixin 反向 import main.py 造成循环导入，
同时保持「一份实现」的单一所有者。

`_private_companion_runtime` 是通过 `sys.modules` 键共享的模块对象，
此处按同一键取用，拿到的是同一个对象，不产生第二份状态。
"""
from __future__ import annotations

import functools
import inspect
import sys
import threading
from types import ModuleType
from typing import Any
import contextvars
import base64
from pathlib import Path

try:
    from astrbot.api.message_components import BaseMessageComponent, ComponentType
except ImportError:
    from astrbot.core.message.components import BaseMessageComponent, ComponentType
from .persona_config import load_scope_manifest

_PRIVATE_COMPANION_RUNTIME_KEY = "ASTROBOT_PRIVATE_COMPANION_RUNTIME"


def _new_private_companion_runtime() -> ModuleType:
    runtime = ModuleType(_PRIVATE_COMPANION_RUNTIME_KEY)
    runtime.lock = threading.RLock()
    runtime.active_plugin = None
    return runtime


_private_companion_runtime = sys.modules.setdefault(
    _PRIVATE_COMPANION_RUNTIME_KEY,
    _new_private_companion_runtime(),
)


def _plugin_instance_root(instance: Any) -> str:
    """Return the data/plugins directory name that imported an instance."""
    module_name = str(getattr(type(instance), "__module__", "") or "")
    parts = module_name.split(".")
    if len(parts) >= 3 and parts[:2] == ["data", "plugins"]:
        return parts[2]
    return ""


def _plugin_instance_can_dispatch(instance: Any) -> bool:
    if bool(getattr(instance, "_private_companion_duplicate_instance", False)):
        return False
    if not bool(getattr(instance, "_private_companion_instance_guard_enabled", False)):
        return True
    with _private_companion_runtime.lock:
        return (
            _private_companion_runtime.active_plugin is None
            or _private_companion_runtime.active_plugin is instance
        )


def _multi_persona_event_context(function):
    """Bind one event task to one persona profile for the complete event lifetime."""
    if inspect.isasyncgenfunction(function):
        @functools.wraps(function)
        async def asyncgen_wrapper(self, event, *args, **kwargs):
            if not _plugin_instance_can_dispatch(self):
                return
            scope_checker = getattr(self, "_bot_scope_allows_event", None)
            if callable(scope_checker) and not scope_checker(event):
                return
            activator = getattr(self, "_activate_persona_for_event_context", None)
            if not callable(activator):
                activator = getattr(self, "_activate_persona_for_event", None)
            activation = activator(event) if callable(activator) else (None, "")
            if inspect.isawaitable(activation):
                activation = await activation
            token, _ = activation
            try:
                async for item in function(self, event, *args, **kwargs):
                    yield item
            finally:
                deactivator = getattr(self, "_deactivate_persona_for_event", None)
                if callable(deactivator):
                    deactivator(token)
        return asyncgen_wrapper

    @functools.wraps(function)
    async def async_wrapper(self, event, *args, **kwargs):
        if not _plugin_instance_can_dispatch(self):
            return None
        scope_checker = getattr(self, "_bot_scope_allows_event", None)
        if callable(scope_checker) and not scope_checker(event):
            return None
        activator = getattr(self, "_activate_persona_for_event_context", None)
        if not callable(activator):
            activator = getattr(self, "_activate_persona_for_event", None)
        activation = activator(event) if callable(activator) else (None, "")
        if inspect.isawaitable(activation):
            activation = await activation
        token, _ = activation
        try:
            return await function(self, event, *args, **kwargs)
        finally:
            deactivator = getattr(self, "_deactivate_persona_for_event", None)
            if callable(deactivator):
                deactivator(token)
    return async_wrapper

_PROACTIVE_ONLY_TEMP_UNLOCK_ALIASES = {

    "全部": "all",

    "all": "all",

    "被动": "all",

    "被动链路": "all",

    "状态": "inject_passive_states",

    "状态注入": "inject_passive_states",

    "被动状态": "inject_passive_states",

    "图片": "enable_private_image_self_recognition",

    "识图": "enable_private_image_self_recognition",

    "私聊图片": "enable_private_image_self_recognition",

    "合并消息": "enable_forward_message_adaptation",

    "转发": "enable_forward_message_adaptation",

    "转发消息": "enable_forward_message_adaptation",

    "防抖": "enable_message_debounce",

    "智能防抖": "enable_message_debounce",

    "撤回": "enable_recall_enhancement",

    "撤回增强": "enable_recall_enhancement",

    "tts": "enable_tts_enhancement",

    "TTS": "enable_tts_enhancement",

    "语音": "enable_tts_enhancement",

    "分段": "enable_segmented_proactive_reply",

    "回复分段": "enable_segmented_proactive_reply",

    "群聊": "enable_group_companion",

    "群聊观察": "enable_group_companion",

    "技能": "enable_skill_growth_passive_injection",

    "技能注入": "enable_skill_growth_passive_injection",

    "吃什么": "enable_food_menu_recommendation",

    "吃什么候选": "enable_food_menu_recommendation",

    "候选菜单": "enable_food_menu_recommendation",

    "饭点关心": "enable_meal_care_proactive",

    "吃饭关心": "enable_meal_care_proactive",

    "关系网": "enable_worldbook_member_recognition",

    "跨用户记忆": "enable_cross_user_memory_bridge",

    "跨用户记忆互通": "enable_cross_user_memory_bridge",

    "互动查询": "enable_cross_user_memory_bridge",

    "跨群转述": "enable_atrelay_tools",

    "转述工具": "enable_atrelay_tools",

    "livingmemory": "enable_livingmemory_integration",

    "lmem": "enable_livingmemory_integration",

    "记忆插件": "enable_livingmemory_integration",

    "记忆协同": "enable_livingmemory_integration",

}

_ACTIVE_PERSONA_ID = contextvars.ContextVar("private_companion_active_persona_id", default="")

_PERSONA_SETTING_MANIFEST = load_scope_manifest()

_PERSONA_PROFILE_FORBIDDEN_FILENAME_CHARS = frozenset('<>:"/\\|?*%')

_WINDOWS_RESERVED_FILENAME_STEMS = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{index}" for index in range(1, 10)),
        *(f"LPT{index}" for index in range(1, 10)),
    }
)

class _OneBotReactionImage(BaseMessageComponent):
    """OneBot image segment that preserves QQ's emoji-image subtype flag."""

    type: ComponentType = ComponentType.Image
    file: str
    path: str
    url: str = ""
    sub_type: int = 1
    payload_file: str
    _private_companion_reaction_expression = True

    def __init__(self, path: str) -> None:
        resolved = str(Path(path).resolve(strict=True))
        payload = base64.b64encode(Path(resolved).read_bytes()).decode("ascii")
        super().__init__(
            file=resolved,
            path=resolved,
            url="",
            sub_type=1,
            payload_file=f"base64://{payload}",
        )

    def toDict(self) -> dict[str, Any]:
        return {
            "type": "image",
            "data": {"file": self.payload_file, "sub_type": self.sub_type},
        }

    async def to_dict(self) -> dict[str, Any]:
        return self.toDict()

    def __repr__(self) -> str:
        return f"_OneBotReactionImage(path={self.path!r}, sub_type={self.sub_type})"
