# -*- coding: utf-8 -*-
"""page_api 域家族共享工具。

宿主延迟代理：域 mixin 模块内通过 `_page_api_host.<name>` 访问宿主模块
(page_api) 的全局名字。直接 `from .page_api import name` 拷贝的是值绑定，
测试对 `astrbot_plugin_private_companion.page_api.<name>` 的 patch 将不生效；
属性式延迟解析保证 patch 始终路由到宿主模块的当前绑定。
"""
from __future__ import annotations


class _PageApiHostRef:
    """延迟引用宿主 page_api 模块，保证 monkey-patch 宿主全局名对全部域生效。"""

    def __getattr__(self, name: str):
        from . import page_api as _host_module

        return getattr(_host_module, name)


_page_api_host = _PageApiHostRef()
