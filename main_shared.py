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
