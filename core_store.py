# -*- coding: utf-8 -*-
from .core_store_shared import (
    DEFAULT_AI_DAILY_NEWS_SOURCE,
    DEFAULT_NEWS_SOURCES,
    LEGACY_DEFAULT_NEWS_SOURCES,
    PREVIOUS_TECH_DEFAULT_NEWS_SOURCES,
    _ALMANAC_JI,
    _ALMANAC_YI,
    _DURABLE_SECTION_NAMES,
    _EVENT_DATA_SAVE_BATCH,
    _EVENT_DATA_SAVE_BATCH_ATTR,
    _FULL_SAVE_SCOPES,
    _LUNAR_DAY_NAMES,
    _LUNAR_MONTH_NAMES,
    _PLATFORM_DISPLAY_NAMES,
    _SOLAR_TERM_DATES,
    logger,
)
# 门面保留拆分前定义在宿主里的 story / 存储相关名字：tests 通过
# monkeypatch.setattr(core_store, "story_authority_controller" /
# "preflight_story_handoff_sections") 注入替身，并直接取用
# core_store.StoreManager。域 mixin 经 _shared 的宿主代理按调用时解析，
# 保证 patch 生效。
from .core_store_shared import (
    StoreManager,
    preflight_story_handoff_sections,
    story_authority_controller,
)
from .core_store_write_schedule import CoreStoreWriteScheduleMixin
from .core_store_user_profile_scope import CoreStoreUserProfileScopeMixin
from .core_store_sync_io import CoreStoreSyncIoMixin
from .core_store_save_state_capture import CoreStoreSaveStateCaptureMixin
from .core_store_private_user_state import CoreStorePrivateUserStateMixin
from .core_store_new_store_defaults import CoreStoreNewStoreDefaultsMixin
from .core_store_identity_merge import CoreStoreIdentityMergeMixin
from .core_store_event_batch_startup import CoreStoreEventBatchStartupMixin
from .core_store_cleanup_compact import CoreStoreCleanupCompactMixin
class CoreStoreMixin(CoreStoreCleanupCompactMixin,
    CoreStoreEventBatchStartupMixin,
    CoreStoreIdentityMergeMixin,
    CoreStoreNewStoreDefaultsMixin,
    CoreStorePrivateUserStateMixin,
    CoreStoreSaveStateCaptureMixin,
    CoreStoreSyncIoMixin,
    CoreStoreUserProfileScopeMixin,
    CoreStoreWriteScheduleMixin,
):
    """配置、数据存储、用户/群组基础访问"""

import time  # re-export for tests patching core_store.time.monotonic
