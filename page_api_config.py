# -*- coding: utf-8 -*-
"""配置/设置域。

由 tools/split_mixin_domain.py 从 page_api.py 机械抽取（8 个方法 + 2 个模块级名字 + 0 个类级赋值 / 2871 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApi）。
"""
from __future__ import annotations

import asyncio
import re
import time
from .companion_interaction_expression import current_interaction_projection, normalize_normal_interaction_band_cap
from .constants import PAGE_FONT_NAMES, PAGE_THEME_NAMES
from .helpers import _normalize_timezone_name, _normalize_timezone_setting
from .model_routing import build_rules, normalize_scope
from .page_backend import build_route_bindings
from .persona_config import runtime_persona_setting
from .photo_reference_catalog import CATALOG_VERSION, CatalogValidationError, load_catalog, validate_and_serialize
from .relationship_ledger import migrate_relationship_positive_stage_cap, normalize_relationship_positive_stage_cap_key
from .relationship_policy import (
    normalize_relationship_stage_policy,
    normalize_relationship_stage_provider_routes,
    relationship_stage_policy_json,
)
from .runtime_config_dispatcher import TTS_RUNTIME_KEYS
from .story_authority import StoryAuthorityError, story_authority_controller
from copy import deepcopy
from quart import request
from typing import Any

from .logging_util import get_module_logger

logger = get_module_logger(__name__)



PAGE_PRIVATE_CONFIG_KEYS = frozenset({"standalone_webui_access_token"})

CYCLE_SETTING_KEYS = (
    "enable_cycle_state",
    "enable_advanced_cycle_strategy",
    "advanced_cycle_link_intensity",
    "advanced_cycle_start_offset",
    "advanced_cycle_menstrual_days",
    "advanced_cycle_menstrual_prompt",
    "advanced_cycle_menstrual_mood",
    "advanced_cycle_menstrual_energy",
    "advanced_cycle_follicular_days",
    "advanced_cycle_follicular_prompt",
    "advanced_cycle_follicular_mood",
    "advanced_cycle_follicular_energy",
    "advanced_cycle_pre_ovulation_days",
    "advanced_cycle_pre_ovulation_prompt",
    "advanced_cycle_pre_ovulation_mood",
    "advanced_cycle_pre_ovulation_energy",
    "advanced_cycle_ovulation_days",
    "advanced_cycle_ovulation_prompt",
    "advanced_cycle_ovulation_mood",
    "advanced_cycle_ovulation_energy",
    "advanced_cycle_luteal_days",
    "advanced_cycle_luteal_prompt",
    "advanced_cycle_luteal_mood",
    "advanced_cycle_luteal_energy",
    "advanced_cycle_pms_days",
    "advanced_cycle_pms_prompt",
    "advanced_cycle_pms_mood",
    "advanced_cycle_pms_energy",
    "advanced_cycle_discomfort_simulation",
    "advanced_cycle_discomfort_chance",
    "advanced_cycle_discomfort_types",
)


class PrivateCompanionPageApiConfigMixin:
    """配置/设置域（从 PrivateCompanionPageApi 拆出）。"""


    def route_bindings(self) -> list[tuple[str, Any, list[str], str]]:
        """Return the wrapped page handlers used by every transport."""
        routes = [
            ("/overview", self.get_overview, ["GET"], "Private Companion Page overview"),
            ("/calendar", self.get_calendar, ["GET"], "Private Companion Page long-lived calendar"),
            ("/calendar/conflicts", self.get_calendar_conflicts, ["GET"], "Private Companion Page calendar conflicts"),
            ("/calendar/preview", self.preview_calendar, ["POST"], "Private Companion Page preview calendar record"),
            ("/calendar/upsert", self.upsert_calendar, ["POST"], "Private Companion Page create or update calendar record"),
            ("/calendar/cancel", self.cancel_calendar, ["POST"], "Private Companion Page cancel calendar record"),
            ("/calendar/delete", self.cancel_calendar, ["POST"], "Private Companion Page cancel calendar record alias"),
            ("/calendar/candidates/confirm", self.confirm_calendar_candidate, ["POST"], "Private Companion Page confirm calendar candidate"),
            ("/calendar/candidates/reject", self.reject_calendar_candidate, ["POST"], "Private Companion Page reject calendar candidate"),
            ("/extension-migration-notice", self.get_extension_migration_notice, ["GET"], "Private Companion Page extension migration notice preference"),
            ("/extension-migration-notice/update", self.update_extension_migration_notice, ["POST"], "Private Companion Page update extension migration notice preference"),
            ("/task-prompts", self.get_task_prompts, ["GET"], "Private Companion Page plugin task prompt catalog"),
            ("/task-prompts/update", self.update_task_prompts, ["POST"], "Private Companion Page update plugin task prompt overrides"),
            ("/expression-library", self.get_expression_library, ["GET"], "Private Companion Page expression library"),
            ("/expression-library/update", self.update_expression_library, ["POST"], "Private Companion Page update expression library"),
            ("/expression-library/share", self.share_expression_library, ["POST"], "Private Companion Page share expression library"),
            ("/expression-library/import/preview", self.preview_expression_library_import, ["POST"], "Private Companion Page preview expression library import"),
            ("/expression-library/import/apply", self.apply_expression_library_import, ["POST"], "Private Companion Page apply expression library import"),
            ("/users", self.list_users, ["GET"], "Private Companion Page users"),
            ("/user", self.get_user, ["GET"], "Private Companion Page user detail"),
            ("/user/update", self.update_user, ["POST"], "Private Companion Page update user"),
            ("/user/delete", self.delete_user, ["POST"], "Private Companion Page delete user"),
            ("/user/identity/link", self.link_unified_identity, ["POST"], "Private Companion Page detached identity relink preview/apply"),
            ("/user/identity/unlink", self.unlink_unified_identity, ["POST"], "Private Companion Page unified identity unlink preview/apply"),
            ("/user/identity/archive", self.archive_unified_person, ["POST"], "Private Companion Page unified person archive preview/apply"),
            ("/user/identity/delete", self.delete_unified_person, ["POST"], "Private Companion Page archived person physical purge preview/apply"),
            ("/user/identity/pending", self.update_pending_identity_review, ["POST"], "Private Companion Page defer/restore pending identity review"),
            ("/user/identity/merge-preview", self.preview_unified_identity_merge, ["POST"], "Private Companion Page unified person merge preview"),
            ("/groups", self.list_groups, ["GET"], "Private Companion Page groups"),
            ("/group", self.get_group, ["GET"], "Private Companion Page group detail"),
            ("/group/update", self.update_group, ["POST"], "Private Companion Page update group"),
            ("/group/delete", self.delete_group, ["POST"], "Private Companion Page delete group"),
            ("/group/slang/update", self.update_group_slang, ["POST"], "Private Companion Page update group slang"),
            ("/group/member-safety", self.get_group_member_safety, ["GET"], "Private Companion Page group member safety"),
            ("/group/member-safety/action", self.update_group_member_safety, ["POST"], "Private Companion Page update group member safety"),
            ("/settings/update", self.update_settings, ["POST"], "Private Companion Page update settings"),
            ("/reality-touch", self.get_reality_touch, ["GET"], "Private Companion Page reality touch status"),
            ("/reality-touch/update", self.update_reality_touch, ["POST"], "Private Companion Page update reality touch alarm"),
            ("/settings/swap_image_api", self.swap_image_api_settings, ["POST"], "Private Companion Page swap image API settings"),
            ("/extensions/image/status", self.get_image_extension_status, ["GET"], "Private Companion Page image extension status"),
            ("/image/debug", self.get_image_debug, ["GET"], "Private Companion Page image generation debug trace"),
            ("/image_api/status", self.get_image_api_status, ["GET"], "Private Companion Page image API status"),
            ("/image_api/test", self.test_image_api_endpoint, ["POST"], "Private Companion Page test one image API endpoint"),
            ("/config/export", self.export_migration_config, ["GET"], "Private Companion Page export migration config"),
            ("/config/backups", self.list_migration_backups, ["GET"], "Private Companion Page list migration backups"),
            ("/config/restore", self.restore_migration_backup, ["POST"], "Private Companion Page restore migration backup"),
            ("/config/import/preview", self.preview_migration_config_import, ["POST"], "Private Companion Page preview migration config import"),
            ("/config/import/apply", self.apply_migration_config_import, ["POST"], "Private Companion Page apply migration config import"),
            ("/proactive_only/unlock", self.update_proactive_only_unlock, ["POST"], "Private Companion Page proactive-only temporary unlock"),
            ("/proactive/candidate/delete", self.delete_proactive_candidate, ["POST"], "Private Companion Page delete proactive candidate"),
            ("/proactive/candidate/prune", self.prune_proactive_candidates, ["POST"], "Private Companion Page prune proactive candidates"),
            ("/extensions/status", self.get_extension_control_plane_status, ["GET"], "Private Companion Page extension control-plane status"),
            ("/diagnostics", self.get_diagnostics, ["GET"], "Private Companion Page diagnostics"),
            ("/troubleshooting", self.get_troubleshooting, ["GET"], "Private Companion Page troubleshooting"),
            ("/daily-review", self.get_daily_review, ["GET"], "Private Companion Page daily review"),
            ("/daily-review/run", self.run_daily_review, ["POST"], "Private Companion Page run daily review"),
            ("/daily-review/guidance", self.update_daily_review_guidance, ["POST"], "Private Companion Page update daily review guidance"),
            ("/troubleshooting/warnings/update", self.update_troubleshooting_warning_suppression, ["POST"], "Private Companion Page update troubleshooting warning suppression"),
            ("/troubleshooting/test", self.run_troubleshooting_test, ["POST"], "Private Companion Page troubleshooting test"),
            ("/token/stats", self.get_token_stats, ["GET"], "Private Companion Page token stats"),
            ("/token/reset", self.reset_token_stats, ["POST"], "Private Companion Page reset token stats"),
            ("/image_cache/list", self.list_image_cache, ["GET"], "Private Companion Page image cache list"),
            ("/image_cache/preview", self.get_image_cache_preview, ["GET"], "Private Companion Page image cache preview"),
            ("/image_cache/preview_data", self.get_image_cache_preview_data, ["GET"], "Private Companion Page image cache preview data"),
            ("/image_cache/thumbnail_data", self.get_image_cache_thumbnail_data, ["GET"], "Private Companion Page image cache thumbnail data"),
            ("/image_cache/update", self.update_image_cache_item, ["POST"], "Private Companion Page update image cache item"),
            ("/image_cache/delete", self.delete_image_cache_item, ["POST"], "Private Companion Page delete image cache item"),
            ("/image_cache/bulk_delete", self.bulk_delete_image_cache_items, ["POST"], "Private Companion Page bulk delete image cache items"),
            ("/reaction_library/list", self.list_reaction_library, ["GET"], "Private Companion Page reaction library list"),
            ("/reaction_library/image_data", self.get_reaction_library_image_data, ["GET"], "Private Companion Page reaction library image data"),
            ("/reaction_library/import", self.import_reaction_library, ["POST"], "Private Companion Page reaction library import"),
            ("/reaction_library/analyze", self.analyze_reaction_library, ["POST"], "Private Companion Page reaction library analyze"),
            ("/reaction_library/update", self.update_reaction_library, ["POST"], "Private Companion Page reaction library update"),
            ("/reaction_library/delete", self.delete_reaction_library, ["POST"], "Private Companion Page reaction library delete"),
            ("/reaction_library/rescan", self.rescan_reaction_library, ["POST"], "Private Companion Page reaction library rescan"),
            ("/reaction_assets/list", self.list_owned_reaction_assets, ["GET"], "Private Companion Page owned reaction assets"),
            ("/reaction_assets/image_data", self.get_owned_reaction_asset_image_data, ["GET"], "Private Companion Page owned reaction asset image"),
            ("/photo_reference/list", self.list_photo_references, ["GET"], "Private Companion Page photo reference list"),
            ("/photo_reference/image_data", self.get_photo_reference_image_data, ["GET"], "Private Companion Page photo reference image data"),
            ("/photo_reference/upload", self.upload_photo_reference, ["POST"], "Private Companion Page upload photo reference image"),
            ("/wardrobe/describe", self.describe_wardrobe_image, ["POST"], "Private Companion Page describe wardrobe garment image"),
            ("/wardrobe/outfit-preview", self.preview_wardrobe_outfit, ["POST"], "Private Companion Page preview wardrobe outfit injection"),
            ("/wardrobe/drafts", self.list_wardrobe_drafts, ["POST"], "Private Companion Page list wardrobe drafts"),
            ("/wardrobe/draft-apply", self.confirm_wardrobe_draft, ["POST"], "Private Companion Page apply wardrobe draft"),
            ("/wardrobe/draft-reject", self.reject_wardrobe_draft, ["POST"], "Private Companion Page reject wardrobe draft"),
            ("/wardrobe/asset-image", self.get_wardrobe_asset_image, ["POST"], "Private Companion Page wardrobe asset image data"),
            ("/wardrobe/intent", self.get_wardrobe_intent, ["POST"], "Private Companion Page wardrobe session intent"),
            ("/wardrobe/intent-clear", self.clear_wardrobe_intent, ["POST"], "Private Companion Page clear wardrobe session intent"),
            ("/photo_reference/metadata/compile", self.compile_photo_reference_metadata, ["POST"], "Compile guided photo reference metadata"),
            ("/photo_reference/metadata/review", self.review_photo_reference_metadata, ["POST"], "Review and merge guided photo reference answers"),
            ("/photo_reference/selection_trial", self.run_photo_reference_selection_trial, ["POST"], "Run side-effect-free photo reference selection trial"),
            ("/reference_asset/list", self.list_reference_assets, ["GET"], "Private Companion Page scoped visual reference assets"),
            ("/reference_asset/image_data", self.get_reference_asset_image_data, ["GET"], "Private Companion Page scoped visual reference image data"),
            ("/reference_asset/upload", self.upload_reference_asset, ["POST"], "Private Companion Page upload scoped visual reference"),
            ("/reference_asset/update", self.update_reference_asset, ["POST"], "Private Companion Page update scoped visual reference"),
            ("/reference_asset/delete", self.delete_reference_asset, ["POST"], "Private Companion Page delete scoped visual reference"),
            ("/worldbook/member/reference/list", self.list_reference_assets, ["GET"], "Private Companion Page worldbook member reference list"),
            ("/worldbook/member/reference/upload", self.upload_reference_asset, ["POST"], "Private Companion Page worldbook member reference upload"),
            ("/worldbook/member/reference/update", self.update_reference_asset, ["POST"], "Private Companion Page worldbook member reference update"),
            ("/worldbook/member/reference/delete", self.delete_reference_asset, ["POST"], "Private Companion Page worldbook member reference delete"),
            ("/knowledge/reference/list", self.list_reference_assets, ["GET"], "Private Companion Page knowledge reference list"),
            ("/knowledge/reference/upload", self.upload_reference_asset, ["POST"], "Private Companion Page knowledge reference upload"),
            ("/knowledge/reference/update", self.update_reference_asset, ["POST"], "Private Companion Page knowledge reference update"),
            ("/knowledge/reference/delete", self.delete_reference_asset, ["POST"], "Private Companion Page knowledge reference delete"),
            ("/relationship/role/reference/list", self.list_reference_assets, ["GET"], "Private Companion Page relationship role reference list"),
            ("/relationship/role/reference/image_data", self.get_reference_asset_image_data, ["GET"], "Private Companion Page relationship role reference image data"),
            ("/relationship/role/reference/upload", self.upload_reference_asset, ["POST"], "Private Companion Page relationship role reference upload"),
            ("/relationship/role/reference/update", self.update_reference_asset, ["POST"], "Private Companion Page relationship role reference update"),
            ("/relationship/role/reference/delete", self.delete_reference_asset, ["POST"], "Private Companion Page relationship role reference delete"),
            ("/photo_reference/assets", self.list_photo_reference_assets, ["GET"], "Private Companion Page visual reference assets"),
            ("/photo_reference/assets/list", self.list_photo_reference_assets, ["GET"], "Private Companion Page visual reference asset list"),
            ("/photo_reference/assets/image_data", self.get_photo_reference_asset_image_data, ["GET"], "Private Companion Page visual reference asset image data"),
            ("/photo_reference/assets/upload", self.upload_photo_reference_asset, ["POST"], "Private Companion Page upload visual reference asset"),
            ("/photo_reference/assets/update", self.update_photo_reference_asset, ["POST"], "Private Companion Page update visual reference asset"),
            ("/photo_reference/assets/delete", self.delete_photo_reference_asset, ["POST"], "Private Companion Page delete visual reference asset"),
            ("/daily_outfit/image", self.get_daily_outfit_image, ["GET"], "Private Companion Page daily outfit image"),
            ("/daily_outfit/image_data", self.get_daily_outfit_image_data, ["GET"], "Private Companion Page daily outfit image data"),
            ("/bookshelf/unlock", self.unlock_bookshelf, ["POST"], "Private Companion Page unlock bookshelf"),
            ("/bookshelf/session", self.get_bookshelf_session, ["GET", "POST"], "Private Companion Page restore bookshelf session"),
            ("/bookshelf/image", self.get_bookshelf_image, ["GET"], "Private Companion Page bookshelf image"),
            ("/bookshelf/image_data", self.get_bookshelf_image_data, ["GET"], "Private Companion Page bookshelf image data"),
            ("/bookshelf/delete", self.delete_bookshelf_item, ["POST"], "Private Companion Page delete bookshelf item"),
            ("/bookshelf/rate", self.rate_bookshelf_item, ["POST"], "Private Companion Page rate bookshelf item"),
            ("/bookshelf/tags", self.update_bookshelf_item_tags, ["POST"], "Private Companion Page update bookshelf item tags"),
            ("/bookshelf/comments/update", self.update_bookshelf_item_comments, ["POST"], "Private Companion Page update bookshelf item comments"),
            ("/bookshelf/reading_state", self.update_bookshelf_reading_state, ["POST"], "Private Companion Page update bookshelf reading state"),
            ("/memo/list", self.list_memo_notes, ["GET"], "Private Companion Page list memo notes"),
            ("/memo/update", self.update_memo_note, ["POST"], "Private Companion Page update memo note"),
            ("/qzone/status", self.get_qzone_status, ["GET"], "Private Companion Page qzone status"),
            ("/qzone/health", self.get_qzone_status, ["GET"], "Private Companion Page qzone status alias"),
            ("/qzone/summary", self.get_qzone_status, ["GET"], "Private Companion Page qzone status alias"),
            ("/qzone/state", self.get_qzone_status, ["GET"], "Private Companion Page qzone status alias"),
            ("/qzone/feed", self.get_qzone_feed, ["GET"], "Private Companion Page qzone feed"),
            ("/qzone/feeds", self.get_qzone_feed, ["GET"], "Private Companion Page qzone feed alias"),
            ("/qzone/list", self.get_qzone_feed, ["GET"], "Private Companion Page qzone feed alias"),
            ("/qzone/detail", self.get_qzone_detail, ["GET"], "Private Companion Page qzone detail"),
            ("/qzone/post", self.get_qzone_detail, ["GET"], "Private Companion Page qzone detail alias"),
            ("/qzone/item", self.get_qzone_detail, ["GET"], "Private Companion Page qzone detail alias"),
            ("/qzone/refresh_cookies", self.refresh_qzone_cookies, ["POST"], "Private Companion Page qzone refresh cookies"),
            ("/qzone/refresh-cookies", self.refresh_qzone_cookies, ["POST"], "Private Companion Page qzone refresh cookies alias"),
            ("/qzone/cookies/refresh", self.refresh_qzone_cookies, ["POST"], "Private Companion Page qzone refresh cookies alias"),
            ("/qzone/cookie/refresh", self.refresh_qzone_cookies, ["POST"], "Private Companion Page qzone refresh cookies alias"),
            ("/qzone/refresh", self.refresh_qzone_cookies, ["POST"], "Private Companion Page qzone refresh cookies alias"),
            ("/qzone/publish", self.publish_qzone_post, ["POST"], "Private Companion Page qzone publish"),
            ("/qzone/post/publish", self.publish_qzone_post, ["POST"], "Private Companion Page qzone publish alias"),
            ("/qzone/post", self.publish_qzone_post, ["POST"], "Private Companion Page qzone publish alias"),
            ("/qzone/like", self.like_qzone_post, ["POST"], "Private Companion Page qzone like"),
            ("/qzone/post/like", self.like_qzone_post, ["POST"], "Private Companion Page qzone like alias"),
            ("/qzone/comment", self.comment_qzone_post, ["POST"], "Private Companion Page qzone comment"),
            ("/qzone/post/comment", self.comment_qzone_post, ["POST"], "Private Companion Page qzone comment alias"),
            ("/qzone/delete", self.delete_qzone_post, ["POST"], "Private Companion Page qzone delete"),
            ("/qzone/post/delete", self.delete_qzone_post, ["POST"], "Private Companion Page qzone delete alias"),
            ("/creative/project", self.get_creative_project, ["GET"], "Private Companion Page creative project detail"),
            ("/creative/project/cover", self.get_creative_project_cover, ["GET"], "Private Companion Page creative project cover"),
            ("/creative/project/cover_data", self.get_creative_project_cover_data, ["GET"], "Private Companion Page creative project cover data"),
            ("/creative/project/update", self.update_creative_project, ["POST"], "Private Companion Page update creative project"),
            ("/creative/project/chunk/update", self.update_creative_chunk, ["POST"], "Private Companion Page update creative chunk"),
            ("/creative/project/outline/update", self.update_creative_outline, ["POST"], "Private Companion Page update creative outline"),
            ("/creative/project/characters/update", self.update_creative_characters, ["POST"], "Private Companion Page update creative characters"),
            ("/creative/project/reanalyze", self.reanalyze_creative_project, ["POST"], "Private Companion Page reanalyze creative project"),
            ("/creative/project/rebuild_memory", self.rebuild_creative_memory, ["POST"], "Private Companion Page rebuild creative memory"),
            ("/creative/project/delete", self.delete_creative_project, ["POST"], "Private Companion Page delete creative project"),
            ("/worldbook/import", self.import_worldbook, ["POST"], "Private Companion Page import worldbook"),
            ("/worldbook/member/livingmemory", self.get_worldbook_member_livingmemory, ["GET"], "Private Companion Page worldbook member LivingMemory"),
            ("/worldbook/member/update", self.update_worldbook_member, ["POST"], "Private Companion Page update worldbook member"),
            ("/worldbook/observations/clear", self.clear_worldbook_pending_observations, ["POST"], "Private Companion Page clear worldbook pending observations"),
            ("/worldbook/group/update", self.update_worldbook_group, ["POST"], "Private Companion Page update worldbook group"),
            ("/skill/update", self.update_skill_growth, ["POST"], "Private Companion Page update skill growth"),
            ("/personal_goal/update", self.update_personal_goal, ["POST"], "Private Companion Page update personal goal"),
            ("/food_menu/update", self.update_food_menu, ["POST"], "Private Companion Page update food menu"),
            ("/food_menu/bulk_update", self.bulk_update_food_menu, ["POST"], "Private Companion Page bulk update food menu"),
            ("/food_menu/bulk_delete", self.bulk_delete_food_menu, ["POST"], "Private Companion Page bulk delete food menu"),
            ("/external_ability/update", self.update_external_ability, ["POST"], "Private Companion Page update external proactive ability"),
            ("/setup/apply", self.apply_setup_guide, ["POST"], "Private Companion Page apply first setup guide"),
            ("/setup/daily/run", self.run_setup_daily_generation, ["POST"], "Private Companion Page setup guide daily generation"),
            ("/daily/detail/regenerate", self.regenerate_daily_detail_segment, ["POST"], "Private Companion Page regenerate one daily detail segment"),
            ("/roleplay/personas", self.list_roleplay_personas, ["GET"], "Private Companion Page roleplay personas"),
            ("/persona/migrate", self.migrate_persona_profile, ["POST"], "Private Companion Page migrate persona profile"),
            ("/persona/reset-current", self.reset_current_persona, ["POST"], "Private Companion Page reset current persona"),
            ("/persona/config-state", self.get_persona_config_state, ["GET"], "Private Companion Page persona config state"),
            ("/persona/config/create", self.create_persona_config, ["POST"], "Private Companion Page create persona config"),
            ("/persona/settings/update", self.update_persona_settings, ["POST"], "Private Companion Page update persona settings"),
            ("/persona/config/detach-preview", self.preview_persona_config_detach, ["POST"], "Private Companion Page preview persona config detach"),
            ("/persona/config/detach-apply", self.apply_persona_config_detach, ["POST"], "Private Companion Page apply persona config detach"),
            ("/roleplay/draft_from_persona", self.generate_roleplay_draft_from_persona, ["POST"], "Private Companion Page roleplay draft from persona"),
            ("/roleplay/standardize_persona", self.standardize_persona_from_questionnaire, ["POST"], "Private Companion Page roleplay persona standardization"),
            ("/roleplay/persona_style_scenarios", self.generate_persona_style_scenarios, ["POST"], "Private Companion Page roleplay persona style scenarios"),
            ("/roleplay/persona_style_scenario_retry", self.retry_persona_style_scenario, ["POST"], "Private Companion Page roleplay persona style scenario retry"),
            ("/roleplay/persona_style_summary", self.generate_persona_style_summary, ["POST"], "Private Companion Page roleplay persona style summary"),
            ("/preset/apply", self.apply_preset, ["POST"], "Private Companion Page apply preset"),
            ("/providers/available", self.list_available_providers, ["GET"], "Private Companion Page available providers"),
            ("/provider/test", self.test_provider, ["POST"], "Private Companion Page test provider"),
            ("/tts/providers", self.list_tts_provider_configs, ["GET"], "Private Companion Page TTS provider configs"),
            ("/tts/provider/create", self.create_tts_provider_config, ["POST"], "Private Companion Page create TTS provider"),
            ("/tts/provider/clone", self.clone_tts_provider_config, ["POST"], "Private Companion Page clone TTS provider"),
            ("/tts/provider/update", self.update_tts_provider_config, ["POST"], "Private Companion Page update TTS provider"),
            ("/tts/provider/test", self.test_tts_provider_config, ["POST"], "Private Companion Page test TTS provider"),
        ]
        persona_control_routes = {
            "/extension-migration-notice",
            "/extension-migration-notice/update",
            "/task-prompts",
            "/task-prompts/update",
            "/roleplay/personas",
            "/persona/migrate",
            "/persona/reset-current",
            "/persona/config-state",
            "/persona/config/create",
            "/persona/settings/update",
            "/persona/config/detach-preview",
            "/persona/config/detach-apply",
        }
        return build_route_bindings(
            routes,
            persona_control_routes=persona_control_routes,
            persona_wrapper=self._persona_scoped_route_handler,
            http_wrapper=self._http_status_route_handler,
        )

    async def update_settings(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        mode_transition_snapshot: dict[str, Any] = {}
        mode_transition_committed = False
        story_authority_identity: Any | None = None
        primary_data_warning: dict[str, Any] = {}
        try:
            active_getter = getattr(self.plugin, "_active_persona_scope", None)
            active_persona = str(active_getter() if callable(active_getter) else "").strip()
            primary_getter = getattr(self.plugin, "_primary_persona_id", None)
            primary_persona = str(
                primary_getter()
                if callable(primary_getter)
                else getattr(self.plugin, "plugin_specific_persona_id", "")
            ).strip()
            primary_persona_before = primary_persona
            if (
                active_persona
                and bool(getattr(self.plugin, "enable_multi_persona_mode", False))
                and active_persona != primary_persona
            ):
                # Secondary persona edits must never write AstrBot's shared
                # config.  Reuse the dedicated sparse settings transaction.
                changes: dict[str, Any] = {}
                if "group_access_mode" in payload:
                    mode = str(payload.get("group_access_mode") or "").strip().lower()
                    if mode not in {"whitelist", "blacklist"}:
                        return self._error("group_access_mode 只能是 whitelist 或 blacklist")
                    changes["group_access_mode"] = mode
                if "group_whitelist_ids" in payload:
                    changes["group_whitelist_ids"] = self._normalize_id_list(payload.get("group_whitelist_ids"))
                if "group_blacklist_ids" in payload:
                    changes["group_blacklist_ids"] = self._normalize_id_list(payload.get("group_blacklist_ids"))
                for key, value in (payload.get("features") or {}).items():
                    changes[key] = self._normalize_bool_value(value)
                for key, value in (payload.get("providers") or {}).items():
                    changes[key] = self._single_line(value, 160)
                for key, value in (payload.get("settings") or {}).items():
                    changes[key] = self._normalize_setting_value(key, value)
                manifest_getter = getattr(self.plugin, "_persona_scope_manifest", None)
                manifest = manifest_getter() if callable(manifest_getter) else {}
                persona_changes = {
                    key: value
                    for key, value in changes.items()
                    if isinstance(manifest.get(key), dict) and manifest[key].get("scope") == "persona"
                }
                common_changes = {
                    key: value
                    for key, value in changes.items()
                    if key not in persona_changes
                }
                updater = getattr(self.plugin, "_update_persona_settings_async", None)
                if not callable(updater):
                    return self._error("当前版本不支持人格独立配置", status_code=503)
                persona_result = {"ok": True, "changed": []}
                if persona_changes or payload.get("follow_primary_keys"):
                    persona_result = await updater(
                        active_persona,
                        changes=persona_changes,
                        follow_primary_keys=payload.get("follow_primary_keys") or [],
                        expected_revision=payload.get("expected_revision"),
                    )
                    if not persona_result.get("ok"):
                        return self._error(
                            persona_result.get("message") or "人格配置更新失败",
                            status_code=int(persona_result.get("status_code") or 400),
                        )
                if not common_changes:
                    overview = await self.get_overview()
                    if overview.get("success"):
                        overview["data"]["changed"] = persona_result.get("changed", [])
                        overview["data"]["config_saved"] = True
                    return overview
                # Continue through the established shared-config transaction
                # for common keys while preserving the persona update above.
                payload = {
                    **payload,
                    "features": {key: value for key, value in (payload.get("features") or {}).items() if key in common_changes},
                    "providers": {key: value for key, value in (payload.get("providers") or {}).items() if key in common_changes},
                    "settings": {key: value for key, value in (payload.get("settings") or {}).items() if key in common_changes},
                    "follow_primary_keys": [],
                }
            changed: dict[str, Any] = {}
            if "group_access_mode" in payload:
                mode = str(payload.get("group_access_mode") or "").strip().lower()
                if mode not in {"whitelist", "blacklist"}:
                    return self._error("group_access_mode 只能是 whitelist 或 blacklist")
                changed["group_access_mode"] = mode
            if "group_whitelist_ids" in payload:
                changed["group_whitelist_ids"] = self._normalize_id_list(payload.get("group_whitelist_ids"))
            if "group_blacklist_ids" in payload:
                changed["group_blacklist_ids"] = self._normalize_id_list(payload.get("group_blacklist_ids"))
            for key, value in (payload.get("features") or {}).items():
                if key in self._allowed_feature_keys():
                    changed[key] = self._normalize_bool_value(value)
                elif key in self._schema_bool_keys() and key in self._allowed_setting_keys():
                    changed[key] = self._normalize_bool_value(value)
            provider_payload: dict[str, Any] = {}
            for key, value in (payload.get("providers") or {}).items():
                if key in self._allowed_provider_keys():
                    provider_payload[key] = self._single_line(value, 160)
            for key, value in (payload.get("settings") or {}).items():
                if key in self._allowed_setting_keys():
                    changed[key] = self._normalize_setting_value(key, value)
            if provider_payload:
                if bool(payload.get("overwrite_provider_modes")):
                    mode_value = changed.get("provider_config_mode") or self._config_get("provider_config_mode") or getattr(self.plugin, "provider_config_mode", "quick")
                    provider_payload = self._expand_provider_overwrite_bundle(str(mode_value), provider_payload)
                changed.update(provider_payload)
            # Enabling depends on the single authoritative primary ID being
            # installed before the mode transition.
            if bool(changed.get("enable_multi_persona_mode")):
                ordered_changed: dict[str, Any] = {}
                for dependency_key in ("plugin_specific_persona_id", "multi_persona_ids"):
                    if dependency_key in changed:
                        ordered_changed[dependency_key] = changed[dependency_key]
                ordered_changed.update(changed)
                changed = ordered_changed
            mode_transition_changed = "enable_multi_persona_mode" in changed and (
                bool(changed.get("enable_multi_persona_mode"))
                != bool(getattr(self.plugin, "enable_multi_persona_mode", False))
            )
            storage_changed = bool({"storage_backend", "storage_sqlite_path"} & set(changed))
            if storage_changed or mode_transition_changed:
                story_authority_identity = (
                    story_authority_controller().enter_legacy_operation(
                        "page.settings.store-persona-transaction"
                    )
                )
            req041_config_snapshot = self._req041_config_runtime_snapshot(changed)
            apply_overrides = dict(changed)
            apply_overrides["__defer_relationship_data_save"] = True
            if storage_changed or mode_transition_changed:
                apply_overrides["__defer_storage_rebuild"] = True
                flush_save = getattr(self.plugin, "_flush_scheduled_data_save", None)
                if callable(flush_save):
                    await flush_save()
            if mode_transition_changed:
                mode_transition_snapshot = self._multi_persona_transition_snapshot()
            if self.IMAGE_API_RUNTIME_SETTING_KEYS & set(changed):
                async with self._image_api_runtime_lock():
                    for key, value in changed.items():
                        self._apply_config_value(key, value, apply_overrides)
            else:
                for key, value in changed.items():
                    self._apply_config_value(key, value, apply_overrides)
            if apply_overrides.get("__relationship_profile_batch"):
                try:
                    await self._apply_relationship_profile_config_batch(apply_overrides)
                except Exception:
                    self._restore_relationship_config_values(apply_overrides)
                    raise
            if "enable_body_monitor_integration" in changed:
                runtime_task = getattr(self.plugin, "_body_monitor_integration_toggle_task", None)
                if isinstance(runtime_task, asyncio.Task):
                    await runtime_task
            expression_scope_keys = {
                "expression_private_learning_source_mode",
                "expression_private_learning_source_ids",
                "expression_group_learning_source_mode",
                "expression_group_learning_source_ids",
                "expression_private_application_mode",
                "expression_private_application_user_ids",
                "expression_group_application_mode",
                "expression_group_application_ids",
            }
            relationship_data_changed = bool(apply_overrides.get("__relationship_data_changed"))
            if expression_scope_keys & set(changed) or relationship_data_changed:
                save_sections: set[str] = set()
                if expression_scope_keys & set(changed):
                    save_sections.add("expression_learning_runtime")
                if relationship_data_changed:
                    save_sections.add("users")
                async with self.plugin._data_lock:
                    expression_refresher = getattr(self.plugin, "_refresh_expression_voice_profile", None)
                    if expression_scope_keys & set(changed) and callable(expression_refresher):
                        expression_refresher()
                        save_sections.add("expression_voice_profile")
                    self.plugin._save_data_sync(sections=save_sections)
            if storage_changed:
                rebuild = getattr(self.plugin, "_rebuild_store_manager", None)
                if callable(rebuild):
                    rebuild(reload_data=True)
            await self._record_personality_auto_tune_manual_values(changed)
            personality_restore: dict[str, Any] = {}
            if (
                ("enable_personality_iteration_experiment" in changed and not bool(changed.get("enable_personality_iteration_experiment")))
                or ("enable_personality_iteration_auto_tune" in changed and not bool(changed.get("enable_personality_iteration_auto_tune")))
            ):
                personality_restore = await self._restore_personality_iteration_auto_tune(
                    "角色贴合校准或自主调节已关闭"
                )
                restored_values = personality_restore.get("restored") if isinstance(personality_restore, dict) else {}
                if isinstance(restored_values, dict):
                    changed.update(restored_values)
            if any(key in self._allowed_provider_keys() for key in changed) or "provider_config_mode" in changed:
                apply_quick = getattr(self.plugin, "_apply_quick_provider_defaults", None)
                if callable(apply_quick):
                    apply_quick()
            clearer = getattr(self.plugin, "_clear_proactive_only_temp_unlocks_if_mode_off", None)
            if callable(clearer):
                clearer()
            config_saved = True
            if changed:
                try:
                    config_saved = await self._save_config_if_possible()
                except Exception as save_exc:
                    if req041_config_snapshot:
                        rollback_saved = await self._rollback_req041_config_runtime(req041_config_snapshot)
                        if not rollback_saved:
                            raise RuntimeError(
                                "配置写入失败，REQ-041 运行值已恢复，但旧配置重新持久化失败"
                            ) from save_exc
                    if apply_overrides.get("__relationship_profile_transaction"):
                        await self._rollback_relationship_config_transaction(apply_overrides)
                    raise
                if apply_overrides.get("__relationship_profile_transaction"):
                    if not config_saved:
                        if req041_config_snapshot:
                            rollback_saved = await self._rollback_req041_config_runtime(req041_config_snapshot)
                            if not rollback_saved:
                                raise RuntimeError(
                                    "配置保存失败，REQ-041 运行值已恢复，但旧配置重新持久化失败"
                                )
                        await self._rollback_relationship_config_transaction(apply_overrides)
                        raise RuntimeError("配置保存失败，关系配置、人格资料及 REQ-041 关键运行值已回滚")
                    else:
                        apply_overrides.pop("__relationship_profile_transaction", None)
                if not config_saved and req041_config_snapshot:
                    rollback_saved = await self._rollback_req041_config_runtime(req041_config_snapshot)
                    if not rollback_saved:
                        raise RuntimeError(
                            "配置保存失败，REQ-041 运行值已恢复，但旧配置重新持久化失败"
                        )
                    raise RuntimeError("配置保存失败，REQ-041 关键运行值已回滚")
                if not config_saved and mode_transition_snapshot:
                    raise RuntimeError("配置保存失败，多人格模式切换已回滚")
                if config_saved and mode_transition_snapshot:
                    mode_transition_committed = True
            if (
                config_saved
                and "plugin_specific_persona_id" in changed
                and primary_persona_before != str(changed.get("plugin_specific_persona_id") or "").strip()
            ):
                recorder = getattr(self.plugin, "_record_primary_persona_change", None)
                if callable(recorder):
                    primary_data_warning = recorder(
                        primary_persona_before,
                        changed.get("plugin_specific_persona_id"),
                    ) or {}
            overview = await self.get_overview()
            if self._is_http_error_response(overview):
                return overview
            if overview.get("success"):
                overview["data"]["changed"] = changed
                overview["data"]["config_saved"] = config_saved
                if personality_restore:
                    overview["data"]["personality_auto_tune_restore"] = personality_restore
                if primary_data_warning:
                    overview["data"]["primary_store_ownership_warning"] = primary_data_warning
                data = overview.get("data") if isinstance(overview.get("data"), dict) else {}
                features = data.get("features") if isinstance(data.get("features"), dict) else {}
                settings = data.get("settings") if isinstance(data.get("settings"), dict) else {}
                if config_saved:
                    for key, value in changed.items():
                        if key in self._allowed_feature_keys():
                            features[key] = self._normalize_bool_value(value)
                        if key in self._allowed_setting_keys():
                            settings[key] = value
            return overview
        except StoryAuthorityError:
            raise
        except CatalogValidationError as exc:
            detail = next(
                (
                    f"{field}：{message}"
                    for field, messages in exc.errors.items()
                    for message in messages
                ),
                "",
            )
            logger.warning("参考图目录字段校验失败: %s", self._single_line(exc, 240))
            return {
                "success": False,
                "error": f"参考图目录存在无效字段：{detail}" if detail else "参考图目录存在无效字段",
                "field_errors": exc.errors,
                "ts": int(time.time()),
            }
        except Exception as exc:
            if mode_transition_snapshot and not mode_transition_committed:
                try:
                    await self._rollback_multi_persona_transition(mode_transition_snapshot)
                except Exception as rollback_exc:
                    logger.error(
                        "多人格模式切换回滚失败: %s",
                        rollback_exc,
                        exc_info=True,
                    )
                    return self._exception_error(
                        f"{exc}；多人格模式切换回滚失败: {rollback_exc}"
                    )
            logger.error(f"更新设置失败: {exc}", exc_info=True)
            return self._exception_error(str(exc))
        finally:
            if story_authority_identity is not None:
                story_authority_controller().exit_legacy_operation(
                    story_authority_identity
                )

    async def apply_setup_guide(self) -> dict[str, Any]:
        """Persist the first setup guide draft into plugin config and data."""
        payload = await request.get_json(silent=True) or {}
        draft = payload.get("draft") if isinstance(payload.get("draft"), dict) else payload
        if not isinstance(draft, dict):
            return self._error("缺少首次配置草稿")

        def bool_value(key: str, default: bool = False) -> bool:
            if key not in draft:
                return default
            return self._normalize_bool_value(draft.get(key))

        def text_value(key: str, limit: int = 2000) -> str:
            return str(draft.get(key) or "").strip()[:limit]

        def number_value(key: str, default: Any = 0) -> Any:
            value = draft.get(key, default)
            return default if value in (None, "") else value

        raw_target_ids = draft.get("targetUserIds", draft.get("target_user_ids", []))
        if isinstance(raw_target_ids, str):
            target_ids = self._normalize_private_target_id_list(re.split(r"[\s,，;；、]+", raw_target_ids))
        else:
            target_ids = self._normalize_private_target_id_list(raw_target_ids)
        if not target_ids:
            return self._error("请先填写目标用户 ID")

        proactive_private = bool_value("proactivePrivate", True)
        proactive_group = bool_value("proactiveGroup", False)
        group_interjection = self._single_line(draft.get("groupInterjection"), 40) or "observe"
        group_wake_enhancement = proactive_group and bool_value("groupWakeEnhancement", False)
        worldbook_enabled = bool_value("worldbookEnabled", True)
        target_platform = text_value("targetPlatform", 80) or "aiocqhttp"

        settings: dict[str, Any] = {
            "provider_config_mode": "quick",
            "enable_llm_streaming": bool_value("enable_llm_streaming", False),
            "target_user_ids": target_ids,
            "target_platform": target_platform,
            "quiet_hours": text_value("quietHours", 80) or "23:00-08:30",
            "require_private_opt_in": bool_value("requirePrivateOptIn", True),
            "proactive_intensity_preset": self._single_line(draft.get("privateIntensity"), 40) or "off",
            "max_daily_messages": number_value("privateMaxDailyMessages", 8) if proactive_private else 0,
            "idle_minutes": number_value("privateIdleMinutes", 60),
            "min_interval_minutes": number_value("privateMinIntervalMinutes", 120),
            "proactive_persona_judge_send_threshold": number_value("privatePersonaJudgeThreshold", 62),
            "proactive_review_strength": self._single_line(draft.get("privateReviewStrength"), 40) or "lenient",
            "proactive_unanswered_slowdown_start": number_value("privateUnansweredSlowdownStart", 1),
            "proactive_unanswered_max_interval_multiplier": number_value("privateUnansweredMaxIntervalMultiplier", 2.2),
            "friend_unanswered_max_cooldown_hours": number_value("privateFriendUnansweredMaxCooldownHours", 60),
            "group_wakeup_direct_words": text_value("groupWakeDirectWords", 1200),
            "group_wakeup_owner_direct_words": text_value("groupWakeOwnerDirectWords", 1200),
            "group_wakeup_context_words": text_value("groupWakeContextWords", 1200),
            "group_wakeup_interest_keywords": text_value("groupWakeInterestKeywords", 1200),
            "group_wakeup_interest_probability": number_value("groupWakeInterestProbability", 18),
            "group_wakeup_question_threshold": number_value("groupWakeQuestionThreshold", 65),
            "group_wakeup_cold_group_threshold": number_value("groupWakeColdGroupThreshold", 65),
            "group_wakeup_cold_group_idle_minutes": number_value("groupWakeColdGroupIdleMinutes", 45),
            "group_wakeup_cooldown_seconds": number_value("groupWakeCooldownSeconds", 90),
            "group_wakeup_generated_keyword_limit": number_value("groupWakeGeneratedKeywordLimit", 8),
            "group_wakeup_topic_interest_max_boost": number_value("groupWakeTopicInterestMaxBoost", 50),
            "group_wakeup_debounce_pending_penalty": number_value("groupWakeDebouncePendingPenalty", 30),
            "group_wakeup_fatigue_limit": number_value("groupWakeFatigueLimit", 5),
            "group_wakeup_fatigue_decay_minutes": number_value("groupWakeFatigueDecayMinutes", 20),
            "group_wakeup_short_text_wait_seconds": number_value("groupWakeShortTextWaitSeconds", 8),
            "group_interject_min_interval_minutes": number_value("groupInterjectMinIntervalMinutes", 180),
            "group_interject_max_daily": number_value("groupInterjectMaxDaily", 2),
            "worldbook_self_registration": bool_value("worldbookSelfRegistration", True),
        }
        if text_value("worldKnowledgePersona", 5000):
            settings["schedule_persona_prompt"] = text_value("worldKnowledgePersona", 5000)
        if text_value("worldKnowledgeWorld", 5000):
            settings["schedule_worldview_prompt"] = text_value("worldKnowledgeWorld", 5000)
        if text_value("worldKnowledgeUser", 5000):
            settings["roleplay_user_profile_prompt"] = text_value("worldKnowledgeUser", 5000)
        world_knowledge_extra = text_value("worldKnowledgeExtra", 5000)
        image_hint_match = re.search(r"自我识别提示[:：]\s*(.+?)(?:\n\s*\n|$)", world_knowledge_extra, flags=re.S)
        if image_hint_match:
            settings["private_image_self_recognition_hint"] = self._multi_line(image_hint_match.group(1), 1200)
        translation_match = re.search(r"翻译词[:：]\s*(.+?)(?:\n\s*\n|$)", world_knowledge_extra, flags=re.S)
        if translation_match:
            settings["worldview_adaptation_mode"] = "custom"
            settings["worldview_adaptation_prompt"] = self._multi_line(translation_match.group(1), 2000)

        features: dict[str, bool] = {
            "enable_llm_proactive_message": proactive_private,
            "enable_llm_proactive_persona_judge": proactive_private and bool_value(
                "enable_llm_proactive_persona_judge",
                bool(getattr(self.plugin, "enable_llm_proactive_persona_judge", True)),
            ),
            "enable_passive_response_review": proactive_private and bool_value(
                "enable_passive_response_review",
                bool(getattr(self.plugin, "enable_passive_response_review", True)),
            ),
            "enable_proactive_message_review": proactive_private and bool_value(
                "enable_proactive_message_review",
                bool(getattr(self.plugin, "enable_proactive_message_review", True)),
            ),
            "enable_group_companion": proactive_group,
            "enable_group_context_injection": proactive_group,
            "enable_group_injection_guard": proactive_group,
            "enable_group_wakeup_enhancement": group_wake_enhancement,
            "enable_group_wakeup_question": group_wake_enhancement and bool_value("groupWakeQuestion", True),
            "enable_group_wakeup_cold_group": group_wake_enhancement and bool_value("groupWakeColdGroup", False),
            "enable_group_interjection": proactive_group and group_interjection == "low",
            "enable_group_interjection_feedback": proactive_group and group_interjection == "low" and bool_value("groupInterjectionFeedback", True),
            "enable_worldbook_member_recognition": worldbook_enabled,
        }

        providers = {
            key: self._single_line(draft.get(key), 160)
            for key in (
                "FAST_RESPONSE_PROVIDER_ID",
                "COMPLEX_REASONING_PROVIDER_ID",
                "CREATIVE_MODEL_PROVIDER_ID",
                "PLUGIN_VISION_PROVIDER_ID",
            )
            if self._single_line(draft.get(key), 160)
        }
        worldbook_user_id = self._normalize_worldbook_member_id(text_value("worldbookUserId", 80))
        worldbook_name = self._single_line(draft.get("worldbookNickname"), 80)
        worldbook_should_save = bool(worldbook_enabled and worldbook_user_id and (worldbook_name or text_value("worldbookContent", 2000)))
        if worldbook_should_save and not self._worldbook_setup_member_id_valid(
            worldbook_user_id,
            target_ids=target_ids,
            target_platform=target_platform,
        ):
            return self._error("关系网词条必须使用有效 QQ 号、平台身份 ID 或外部身份键")
        worldbook_aliases = [
            self._single_line(item, 40)
            for item in re.split(r"[\n,，、;；]+", text_value("worldbookAliases", 1200))
            if self._single_line(item, 40)
        ][:20]

        changed: dict[str, Any] = {}
        try:
            for key, value in features.items():
                if key in self._allowed_feature_keys():
                    changed[key] = self._normalize_bool_value(value)
            for key, value in settings.items():
                if key in self._allowed_setting_keys():
                    changed[key] = self._normalize_setting_value(key, value)
            for key, value in providers.items():
                if key in self._allowed_provider_keys():
                    changed[key] = value

            for key, value in changed.items():
                self._apply_config_value(key, value, changed)

            if providers or changed.get("provider_config_mode"):
                apply_quick = getattr(self.plugin, "_apply_quick_provider_defaults", None)
                if callable(apply_quick):
                    apply_quick()

            sync_targets = getattr(self.plugin, "_sync_configured_targets", None)
            if callable(sync_targets):
                async with self.plugin._data_lock:
                    sync_targets()
                    self.plugin._save_data_sync(sections={"users"})

            worldbook_saved = False
            if worldbook_should_save:
                async with self.plugin._data_lock:
                    profiles = self.plugin.data.setdefault("worldbook_member_profiles", {})
                    if not isinstance(profiles, dict):
                        profiles = {}
                        self.plugin.data["worldbook_member_profiles"] = profiles
                    profile = profiles.get(worldbook_user_id)
                    if not isinstance(profile, dict):
                        profile = {
                            "user_id": worldbook_user_id,
                            "important_memories": [],
                            "priority": 120,
                            "source_entries": ["首次配置引导"],
                            "observed_names": [],
                        }
                        profiles[worldbook_user_id] = profile
                    profile.update(
                        {
                            "user_id": worldbook_user_id,
                            "identity_type": "qq" if worldbook_user_id.isdigit() else "external",
                            "enabled": bool_value("worldbookEnabled", True),
                            "name": worldbook_name or worldbook_user_id,
                            "gender": self._single_line(draft.get("worldbookGender"), 40),
                            "aliases": worldbook_aliases,
                            "content": text_value("worldbookContent", 2000),
                            "identity_note": text_value("worldbookIdentityNote", 2000),
                            "boundary_note": text_value("worldbookBoundaryNote", 1200),
                            "manual_edit_ts": time.time(),
                            "setup_guide_ts": time.time(),
                        }
                    )
                    deleted = self.plugin.data.setdefault("worldbook_deleted_member_ids", [])
                    if isinstance(deleted, list) and worldbook_user_id in deleted:
                        self.plugin.data["worldbook_deleted_member_ids"] = [
                            item for item in deleted if str(item) != worldbook_user_id
                        ]
                    self.plugin._save_data_sync(
                        sections={"worldbook_member_profiles", "worldbook_deleted_member_ids"}
                    )
                    worldbook_saved = True

            async with self.plugin._data_lock:
                self.plugin.data["setup_guide_completed_at"] = time.time()
                self.plugin.data["setup_guide_completed_version"] = "5.7.2-first-setup"
                self.plugin._save_data_sync(
                    sections={"setup_guide_completed_at", "setup_guide_completed_version"}
                )

            config_saved = True
            if changed:
                config_saved = await self._save_config_if_possible()
            overview = await self.get_overview()
            if self._is_http_error_response(overview):
                return overview
            if overview.get("success"):
                data = overview.get("data") if isinstance(overview.get("data"), dict) else {}
                data["setup_applied"] = True
                data["setup_changed"] = changed
                data["setup_worldbook_saved"] = worldbook_saved
                data["config_saved"] = config_saved
            return overview
        except Exception as exc:
            logger.error(f"首次配置落地失败: {exc}", exc_info=True)
            return self._exception_error("首次配置落地失败")

    def _feature_flags(self) -> dict[str, bool]:
        keys = [
            "enable_proactive_only_mode",
            "enable_proactive_chat_integration",
            "enable_mai_style_integration",
            "enable_companion_memory",
            "enable_expression_learning",
            "enable_intent_emotion_analysis",
            "enable_passive_response_review",
            "enable_framework_error_leak_guard",
            "enable_outbound_secret_redaction",
            "enable_proactive_message_review",
            "enable_smart_silence",
            "enable_llm_proactive_message",
            "enable_llm_proactive_persona_judge",
            "enable_reaction_expression_experiment",
            "enable_maslow_motivation_experiment",
            "enable_experimental_motivation_model",
            "enable_experimental_bluetooth_wakeup",
            "enable_personality_iteration_experiment",
            "enable_daily_case_review_experiment",
            "enable_passive_topic_suppression",
            "enable_custom_relationship_stage_policy",
            "enable_group_relationship_affinity",
            "enable_relationship_content_tiers",
            "enable_relationship_analysis",
            "enable_relationship_state_machine",
            "enable_emotion_simulation",
            "enable_dialogue_episode_memory",
            "enable_open_loop_tracking",
            "enable_user_habit_learning",
            "enable_food_menu_recommendation",
            "enable_personal_goals",
            "enable_humanized_states",
            "enable_health_state",
            "enable_hunger_state",
            "enable_segmented_proactive_reply",
            "enable_proactive_quote_trigger_message",
            "enable_quote_group_reply",
            "enable_quote_group_interjection",
            "enable_quote_private_proactive",
            "enable_photo_text_action",
            "enable_screen_glance_action",
            "enable_goodnight_screen_check",
            "enable_poke_action",
            "enable_voice_action",
            "enable_photo_reference_image",
            "inject_passive_states",
            "enable_passive_state_delta_injection",
            "enable_passive_state_continuity_anchor",
            "enable_cycle_state",
            "enable_skill_growth_simulation",
            "enable_skill_growth_passive_injection",
            "enable_personal_goals",
            "enable_message_debounce",
            "enable_smart_message_debounce",
            "enable_recall_enhancement",
            "enable_recall_cancel_reply",
            "enable_recall_message_cache",
            "enable_recall_transcribe_command",
            "enable_forbidden_word_recall",
            "enable_semantic_message_debounce",
            "enable_environment_perception",
            "enable_balance_awareness",
            "enable_holiday_perception",
            "enable_platform_perception",
            "enable_model_perception",
            "enable_worldview_perception",
            "enable_lunar_perception",
            "enable_solar_term_perception",
            "enable_almanac_perception",
            "enable_group_companion",
            "enable_group_social_context",
            "enable_group_member_safety",
            "enable_group_slang_learning",
            "enable_group_member_profiles",
            "enable_group_context_injection",
            "enable_group_history_injection",
            "enable_group_image_understanding",
            "enable_group_image_wakeup",
            "enable_group_injection_guard",
            "enable_group_persona_denoise",
            "enable_forward_message_adaptation",
            "enable_group_scene_awareness",
            "enable_group_reality_promise_guard",
            "enable_group_wakeup_enhancement",
            "enable_group_wakeup_question",
            "enable_group_wakeup_cold_group",
            "enable_group_high_intensity_mode",
            "enable_group_air_reply_guard",
            "enable_private_image_self_recognition",
            "enable_backup_external_image_api",
            "enable_private_image_gif_enhancement",
            "enable_group_conversation_followup",
            "enable_group_interjection",
            "enable_group_repeat_follow",
            "group_repeat_count_distinct_users_only",
            "enable_group_topic_threads",
            "enable_group_episode_memory",
            "enable_group_interjection_feedback",
            "enable_group_slang_meanings",
            "enable_group_slang_web_search",
            "enable_group_relationship_graph",
            "enable_group_privacy_guard",
            "enable_group_third_party_portrait_guard",
            "enable_worldbook_member_recognition",
            "enable_cross_user_memory_bridge",
            "enable_atrelay_tools",
            "enable_livingmemory_integration",
            "enable_bilibili_integration",
            "enable_bilibili_boredom_watch",
            "enable_news_integration",
            "enable_news_daily_hot_read",
            "enable_news_boredom_read",
            "enable_ai_daily_watch",
            "enable_external_event_self_link",
            "enable_body_monitor_integration",
            "enable_web_exploration",
            "enable_web_exploration_boredom_search",
            "enable_qzone_integration",
            "enable_qzone_life_publish",
            "enable_qzone_generated_image_publish",
            "enable_qzone_comment_inbox",
            "enable_qzone_emotional_vent_publish",
            "enable_reading_archive_integration",
            "enable_reading_archive_boredom_read",
            "enable_reading_archive_ask_recommendation",
            "enable_reading_archive_vision",
            "enable_reading_archive_page_comments",
            "enable_reading_archive_rating",
            "enable_reading_archive_preference_influence",
            "enable_unanswered_screen_peek_followup",
            "enable_goodnight_screen_check",
            "enable_screen_glance_action",
            "enable_poke_action",
            "enable_voice_action",
            "enable_yesterday_screen_diary_context",
            "enable_tts_enhancement",
            "enable_creative_writing",
            "enable_creative_work_read_guard",
            "creative_hidden_mode",
            "enable_reply_interception_forward",
        ]
        values = {
            key: bool(
                runtime_persona_setting(
                    self.plugin,
                    key,
                    getattr(self.plugin, key, False),
                )
            )
            for key in keys
        }
        try:
            reality_api_getter = getattr(self.plugin, "_reality_companion_api", None)
            reality_api = reality_api_getter() if callable(reality_api_getter) else None
            reality_status_getter = getattr(reality_api, "status", None) if reality_api is not None else None
            reality_status = reality_status_getter() if callable(reality_status_getter) else None
            if isinstance(reality_status, dict):
                values["enable_experimental_bluetooth_wakeup"] = bool(reality_status.get("enabled"))
        except Exception:
            pass
        try:
            bilibili_available = bool(getattr(self.plugin, "_bilibili_available", lambda: False)())
        except Exception:
            bilibili_available = False
        try:
            screen_companion_available = bool(self._screen_companion_available())
        except Exception:
            screen_companion_available = False
        try:
            reading_archive_available = bool(getattr(self.plugin, "_reading_archive_available", lambda: False)())
        except Exception:
            reading_archive_available = False
        values["enable_livingmemory_integration"] = bool(getattr(self.plugin, "enable_livingmemory_integration", False))
        values["enable_bilibili_integration"] = bool(bilibili_available and getattr(self.plugin, "enable_bilibili_integration", False))
        values["enable_bilibili_boredom_watch"] = bool(bilibili_available and getattr(self.plugin, "enable_bilibili_boredom_watch", False))
        values["enable_qzone_integration"] = bool(getattr(self.plugin, "enable_qzone_integration", False))
        values["enable_qzone_life_publish"] = bool(getattr(self.plugin, "enable_qzone_life_publish", False))
        values["enable_qzone_generated_image_publish"] = bool(getattr(self.plugin, "enable_qzone_generated_image_publish", False))
        values["enable_qzone_comment_inbox"] = bool(getattr(self.plugin, "enable_qzone_comment_inbox", False))
        values["enable_qzone_emotional_vent_publish"] = bool(getattr(self.plugin, "enable_qzone_emotional_vent_publish", False))
        values["enable_yesterday_screen_diary_context"] = bool(screen_companion_available and getattr(self.plugin, "enable_yesterday_screen_diary_context", False))
        values["enable_reading_archive_integration"] = bool(
            reading_archive_available and getattr(self.plugin, "enable_reading_archive_integration", False)
        )
        values["enable_reading_archive_boredom_read"] = bool(
            reading_archive_available and getattr(self.plugin, "enable_reading_archive_boredom_read", False)
        )
        values["enable_reading_archive_ask_recommendation"] = bool(reading_archive_available and getattr(self.plugin, "enable_reading_archive_ask_recommendation", False))
        values["enable_reading_archive_vision"] = bool(reading_archive_available and getattr(self.plugin, "enable_reading_archive_vision", True))
        values["enable_reading_archive_page_comments"] = bool(reading_archive_available and getattr(self.plugin, "enable_reading_archive_page_comments", True))
        values["enable_reading_archive_rating"] = bool(reading_archive_available and getattr(self.plugin, "enable_reading_archive_rating", True))
        values["enable_reading_archive_preference_influence"] = bool(reading_archive_available and getattr(self.plugin, "enable_reading_archive_preference_influence", True))
        return values

    def _runtime_settings(self) -> dict[str, Any]:
        keys = [
            "bot_name",
            "page_font_family",
            "page_theme",
            "provider_config_mode",
            "model_timeout_overrides",
            "background_llm_request_max_attempts",
            "model_request_max_attempts_overrides",
            "model_token_limit_overrides",
            "model_fallback_overrides",
            "model_replacement_scope",
            "model_replacement_rules",
            "enable_sensitive_model_replacement",
            "sensitive_replacement_keywords",
            "enable_deepseek_peak_replacement",
            "enable_llm_streaming",
            "enable_body_monitor_integration",
            "deepseek_peak_windows",
            "deepseek_peak_timezone",
            "deepseek_peak_match_keywords",
            "plugin_specific_persona_id",
            "enable_multi_persona_mode",
            "multi_persona_ids",
            "target_user_ids",
            "private_user_aliases",
            "private_user_delivery_aliases",
            "target_platform",
            "require_private_opt_in",
            "environment_perception_timezone",
            "holiday_country",
            "enable_environment_perception",
            "enable_balance_awareness",
            "enable_holiday_perception",
            "enable_platform_perception",
            "enable_model_perception",
            "enable_worldview_perception",
            "enable_lunar_perception",
            "enable_solar_term_perception",
            "enable_almanac_perception",
            "default_nickname",
            "enable_auto_user_profile_creation",
            "portrait_global_mode",
            "auto_profile_platforms",
            "default_nickname_strategy",
            "default_proactive_enabled",
            "default_proactive_daily_limit",
            "default_interaction_band",
            "enable_custom_relationship_stage_policy",
            "relationship_stage_policy",
            "relationship_positive_stage_cap_key",
            "normal_interaction_band_cap",
            "owner_group_relationship_projection",
            "owner_group_interaction_projection",
            "enable_relationship_content_tiers",
            "enable_flirt_content_tier",
            "owner_exclusive_label",
            "owner_exclusive_tone",
            "owner_exclusive_address_style",
            "owner_exclusive_proactive_limit",
            "enable_group_relationship_affinity",
            "group_relationship_affinity_allowlist",
            "group_relationship_daily_net_cap",
            "group_relationship_window_minutes",
            "group_relationship_window_absolute_cap",
            "group_relationship_person_daily_absolute_cap",
            "group_relationship_scope_daily_absolute_cap",
            "relationship_event_window_minutes",
            "relationship_positive_event_cap",
            "relationship_negative_event_cap",
            "relationship_positive_daily_cap",
            "relationship_decay_grace_days",
            "relationship_decay_early_per_day",
            "relationship_decay_middle_per_day",
            "relationship_decay_late_per_day",
            "default_style",
            "reply_style_prompt",
            "enable_persona_voice_channels",
            "persona_conversation_voice_prompt",
            "persona_creative_voice_prompt",
            "persona_planning_voice_prompt",
            "persona_inner_voice_prompt",
            "persona_proactive_voice_prompt",
            "proactive_prompt_template",
            "proactive_persona_judge_send_threshold",
            "proactive_persona_judge_cache_minutes",
            "proactive_persona_judge_max_daily",
            "schedule_persona_prompt",
            "schedule_worldview_prompt",
            "roleplay_user_profile_prompt",
            "roleplay_knowledge_source_ids",
            "worldview_adaptation_mode",
            "worldview_adaptation_prompt",
            "quiet_hours",
            "passive_injection_position",
            "passive_review_mode",
            "passive_review_strength",
            "proactive_review_mode",
            "smart_silence_judge_mode",
            "smart_silence_min_confidence",
            "smart_silence_model_timeout_seconds",
            "proactive_review_strength",
            "proactive_review_hard_risk_threshold",
            "proactive_review_low_score_threshold",
            "proactive_review_pressure_threshold",
            "response_review_max_chars",
            "passive_topic_memory_hours",
            "tts_synthesis_backend",
            "tts_provider_id_zh",
            "tts_provider_id_ja",
            "tts_provider_id_en",
            "tts_mimo_tool_name",
            "tts_mimo_voice_name",
            "tts_mimo_style_prompt",
            "tts_generation_mode",
            "tts_voice_language",
            "tts_fishaudio_model",
            "tts_fishaudio_emotion_mode",
            "tts_delivery_mode",
            "tts_foreign_text_mode",
            "tts_message_scope",
            "tts_conversion_scope",
            "tts_conversion_provider_id",
            "tts_extra_prompt",
            "tts_trigger_keywords",
            "tts_frequency_control_mode",
            "tts_constraint_mode",
            "tts_session_min_interval_seconds",
            "tts_private_min_interval_seconds",
            "tts_group_min_interval_seconds",
            "tts_trigger_probability",
            "tts_private_trigger_probability",
            "tts_group_trigger_probability",
            "enable_tts_local_playback",
            "enable_tts_local_playback_live_only",
            "enable_tts_live_subtitle_sync",
            "tts_live_subtitle_url",
            "tts_local_playback_volume",
            "tts_local_playback_min_interval_seconds",
            "auto_voice_enabled",
            "auto_voice_full_conversion_enabled",
            "auto_voice_probability",
            "auto_voice_max_chars",
            "auto_voice_cooldown_seconds",
            "main_user_voice_probability",
            "main_user_mention_voice_keywords",
            "main_user_mention_voice_probability",
            "main_user_mention_voice_prompt",
            "daily_token_limit",
            "enable_daily_token_soft_limit",
            "daily_token_soft_limit",
            "humanized_state_intensity",
            "enable_health_state",
            "enable_hunger_state",
            *CYCLE_SETTING_KEYS,
            "enable_rest_reply_simulation",
            "rest_reply_mode",
            "rest_reply_probability",
            "rest_reply_llm_threshold",
            "rest_reply_active_windows",
            "rest_reply_awake_grace_minutes",
            "enable_rest_backlog_reply",
            "rest_backlog_max_messages",
            "REST_WAKEUP_PROVIDER_ID",
            "enable_busy_reply_gate",
            "busy_reply_min_delay_seconds",
            "busy_reply_max_delay_seconds",
            "busy_reply_proactive_resume_buffer_minutes",
            "check_interval_seconds",
            "proactive_intensity_preset",
            "idle_minutes",
            "min_interval_minutes",
            "proactive_unanswered_slowdown_start",
            "proactive_unanswered_max_interval_multiplier",
            "friend_unanswered_max_cooldown_hours",
            "max_daily_messages",
            "proactive_photo_text_probability",
            "inbound_message_debounce_seconds",
            "enable_message_debounce",
            "enable_smart_message_debounce",
            "SMART_MESSAGE_DEBOUNCE_PROVIDER_ID",
            "smart_message_debounce_model_timeout_seconds",
            "smart_message_debounce_wait_seconds",
            "smart_message_debounce_learning_window_seconds",
            "smart_message_debounce_examples_limit",
            "enable_recall_enhancement",
            "enable_recall_cancel_reply",
            "enable_recall_message_cache",
            "enable_recall_transcribe_command",
            "enable_forbidden_word_recall",
            "recall_forbidden_words",
            "recall_forbidden_scope",
            "recall_forbidden_word_case_sensitive",
            "text_message_debounce_seconds",
            "image_message_debounce_seconds",
            "forward_message_debounce_seconds",
            "text_message_debounce_max_wait_seconds",
            "message_debounce_max_merge_messages",
            "enable_semantic_message_debounce",
            "semantic_message_debounce_seconds",
            "enable_proactive_quote_trigger_message",
            "enable_quote_group_reply",
            "enable_quote_group_interjection",
            "enable_quote_private_proactive",
            "quote_skip_short_reply_chars",
            "quote_target_strategy",
            "enable_photo_text_action",
            "enable_user_requested_photo_generation",
            "allow_generate_photo_on_reaction_turns",
            "enable_photo_reference_image",
            "photo_action_max_daily",
            "enable_generated_photo_cleanup",
            "generated_photo_retention_days",
            "generated_photo_max_mb",
            "photo_generation_backend",
            "custom_photo_tool_name",
            "custom_photo_tool_prompt_param",
            "custom_photo_tool_kind_param",
            "custom_photo_tool_reference_param",
            "custom_photo_tool_extra_params",
            "COMFYUI_TEXT2IMG_WORKFLOW_NAME",
            "COMFYUI_SELFIE_WORKFLOW_NAME",
            "photo_reference_catalog",
            "photo_persona_reference_image_path",
            "photo_reference_library",
            "enable_wardrobe",
            "wardrobe_tendency",
            "enable_wardrobe_prompt",
            "wardrobe_prompt_max_items",
            "wardrobe_image_max_count",
            "wardrobe_image_prompt",
            "WARDROBE_VISION_PROVIDER_ID",
            "wardrobe_outfit_mode",
            "wardrobe_injection_detail",
            "wardrobe_outfit_rotation_days",
            "enable_wardrobe_outfit_generate",
            "WARDROBE_OUTFIT_PROVIDER_ID",
            "wardrobe_items",
            "wardrobe_outfits",
            "wardrobe_photo_source",
            "enable_daily_outfit_photo",
            "enable_creative_cover_generation",
            "daily_outfit_photo_prompt",
            "daily_outfit_rotation_days",
            "enable_natural_language_photo_generation",
            "natural_language_photo_generation_mode",
            "command_photo_generation_max_daily",
            "natural_language_photo_generation_max_daily",
            "natural_language_photo_extra_prompt",
            "comfyui_photo_wait_seconds",
            "enable_local_photo_load_guard",
            "local_photo_cpu_busy_percent",
            "local_photo_memory_busy_percent",
            "local_photo_defer_minutes",
            "external_image_api_platform",
            "EXTERNAL_IMAGE_API_BASE_URL",
            "EXTERNAL_IMAGE_API_KEY",
            "EXTERNAL_IMAGE_API_MODEL",
            "external_image_api_size",
            "external_image_api_timeout_seconds",
            "external_image_api_custom_headers",
            "external_image_download_proxy",
            "external_image_download_use_environment_proxy",
            "external_image_api_endpoints",
            "enable_backup_external_image_api",
            "backup_external_image_api_platform",
            "BACKUP_EXTERNAL_IMAGE_API_BASE_URL",
            "BACKUP_EXTERNAL_IMAGE_API_KEY",
            "BACKUP_EXTERNAL_IMAGE_API_MODEL",
            "backup_external_image_api_size",
            "backup_external_image_api_timeout_seconds",
            "backup_external_image_api_custom_headers",
            "photo_generation_prompt_format",
            "photo_generation_style",
            "photo_generation_style_custom_prompt",
            "photo_generation_negative_prompt_mode",
            "photo_generation_negative_prompt",
            "photo_generation_text2img_negative_prompt",
            "photo_generation_selfie_negative_prompt",
            "photo_generation_edit_negative_prompt",
            "photo_generation_fixed_prompt",
            "photo_generation_text2img_fixed_prompt",
            "photo_generation_selfie_fixed_prompt",
            "photo_generation_edit_fixed_prompt",
            "photo_generation_scene_presets",
            "enable_bot_relationship_network",
            "bot_relationship_cards",
            "private_image_vision_wait_seconds",
            "private_image_provider_timeout_seconds",
            "private_image_provider_failure_cooldown_seconds",
            "private_image_vision_provider_priority",
            "private_image_vision_custom_prompt",
            "private_image_vision_max_chars",
            "enable_context_image_captioning",
            "context_image_caption_max_items",
            "context_image_caption_timeout_seconds",
            "enable_private_image_gif_enhancement",
            "private_image_gif_max_frames",
            "enable_private_image_self_recognition",
            "private_image_self_recognition_hint",
            "enable_private_image_vision_cache",
            "private_image_vision_cache_max_items",
            "enable_group_image_understanding",
            "enable_group_image_wakeup",
            "group_image_vision_wait_seconds",
            "group_image_max_images",
            "screen_diary_context_max_chars",
            "enable_segmented_proactive_reply",
            "segmented_proactive_scope",
            "segmented_proactive_chat_scope",
            "enable_segmented_proactive_chat_profiles",
            "segmented_proactive_private_enabled",
            "segmented_proactive_private_scope",
            "segmented_proactive_private_threshold",
            "segmented_proactive_private_min_segment_chars",
            "segmented_proactive_private_max_segments",
            "segmented_proactive_private_send_as_forward",
            "segmented_proactive_private_interval_method",
            "segmented_proactive_private_interval_min",
            "segmented_proactive_private_interval_max",
            "segmented_proactive_private_log_base",
            "segmented_proactive_group_enabled",
            "segmented_proactive_group_scope",
            "segmented_proactive_group_threshold",
            "segmented_proactive_group_min_segment_chars",
            "segmented_proactive_group_max_segments",
            "segmented_proactive_group_send_as_forward",
            "segmented_proactive_group_interval_method",
            "segmented_proactive_group_interval_min",
            "segmented_proactive_group_interval_max",
            "segmented_proactive_group_log_base",
            "segmented_proactive_threshold",
            "segmented_proactive_min_segment_chars",
            "segmented_proactive_max_segments",
            "segmented_proactive_send_as_forward",
            "segmented_proactive_voice_strategy",
            "segmented_proactive_image_strategy",
            "segmented_proactive_at_strategy",
            "segmented_proactive_face_strategy",
            "segmented_proactive_component_order",
            "segmented_proactive_other_strategy",
            "segmented_proactive_split_mode",
            "segmented_proactive_regex",
            "segmented_proactive_split_words",
            "enable_segmented_proactive_content_cleanup",
            "segmented_proactive_content_cleanup_scope",
            "segmented_proactive_content_cleanup_rule",
            "segmented_proactive_content_cleanup_words",
            "enable_segmented_proactive_content_replacement",
            "segmented_proactive_content_replacements",
            "segmented_proactive_interval_method",
            "segmented_proactive_interval_min",
            "segmented_proactive_interval_max",
            "segmented_proactive_log_base",
            "group_conversation_followup_seconds",
            "group_conversation_followup_max_turns",
            "enable_group_conversation_followup",
            "enable_group_repeat_follow",
            "group_repeat_trigger_threshold",
            "group_repeat_count_distinct_users_only",
            "group_interject_min_interval_minutes",
            "group_interject_max_daily",
            "group_repeat_follow_probability",
            "group_repeat_interrupt_probability",
            "group_repeat_interrupt_probability_step",
            "group_repeat_interrupt_text",
            "group_repeat_interrupt_image_path",
            "group_scene_recent_limit",
            "enable_group_history_injection",
            "group_scene_recent_max_chars",
            "enable_group_reality_promise_guard",
            "group_wakeup_direct_words",
            "group_wakeup_owner_direct_words",
            "group_wakeup_context_words",
            "group_wakeup_interest_keywords",
            "group_wakeup_interest_probability",
            "group_wakeup_short_text_wait_seconds",
            "group_wakeup_question_threshold",
            "group_wakeup_cold_group_threshold",
            "group_wakeup_cooldown_seconds",
            "group_wakeup_cold_group_idle_minutes",
            "group_wakeup_generated_keyword_limit",
            "group_wakeup_topic_interest_max_boost",
            "group_wakeup_debounce_pending_penalty",
            "group_wakeup_fatigue_limit",
            "group_wakeup_fatigue_decay_minutes",
            "group_wakeup_log_limit",
            "enable_group_high_intensity_mode",
            "group_high_intensity_wakeup_window_seconds",
            "group_high_intensity_wakeup_threshold",
            "group_high_intensity_cooldown_seconds",
            "group_high_intensity_merge_seconds",
            "group_high_intensity_max_merge_messages",
            "group_high_intensity_merge_scope",
            "enable_forward_message_adaptation",
            "forward_message_mode",
            "forward_message_max_messages",
            "forward_message_max_chars",
            "forward_message_parse_nested",
            "forward_message_image_vision",
            "forward_message_image_limit",
            "enable_recall_cancel_reply",
            "enable_recall_message_cache",
            "enable_recall_transcribe_command",
            "recall_message_cache_ttl_seconds",
            "recall_message_cache_max_items",
            "recall_message_image_cache_max_mb",
            "enable_forbidden_word_recall",
            "recall_forbidden_words",
            "recall_forbidden_scope",
            "recall_forbidden_word_case_sensitive",
            "screen_diary_context_max_chars",
            "max_group_recent_messages",
            "max_group_slang_terms",
            "group_slang_web_search_terms",
            "group_slang_web_search_results",
            "memory_refresh_interval_minutes",
            "episode_memory_refresh_messages",
            "episode_memory_refresh_minutes",
            "max_companion_memory_items",
            "max_learned_expression_items",
            "expression_learning_mode",
            "enable_expression_manual_review",
            "enable_expression_style_review",
            "expression_private_learning_source_mode",
            "expression_private_learning_source_ids",
            "expression_group_learning_source_mode",
            "expression_group_learning_source_ids",
            "expression_group_learning_daily_batch_limit",
            "expression_group_learning_min_new_messages",
            "expression_private_application_mode",
            "expression_private_application_user_ids",
            "expression_group_application_mode",
            "expression_group_application_ids",
            "max_dialogue_episodes",
            "user_habit_min_count",
            "user_habit_max_items",
            "emotional_gate_hurt_threshold",
            "emotional_gate_refuse_threshold",
            "emotional_gate_recovery_per_hour",
            "emotional_gate_max_hurt_minutes",
            "enable_llm_emotion_judgement",
            "emotion_judgement_mode",
            "enable_skill_growth_simulation",
            "skill_growth_rate",
            "skill_growth_custom_skills",
            "enable_skill_growth_passive_injection",
            "enable_skill_growth_schedule_influence",
            "skill_growth_schedule_influence_strength",
            "enable_bilibili_integration",
            "enable_bilibili_boredom_watch",
            "bilibili_boredom_min_interval_hours",
            "bilibili_share_probability",
            "bilibili_share_min_score",
            "enable_news_integration",
            "enable_news_boredom_read",
            "enable_news_daily_hot_read",
            "enable_ai_daily_watch",
            "ai_daily_sources",
            "ai_daily_prefer_text_version",
            "enable_external_event_self_link",
            "news_min_interval_hours",
            "news_share_probability",
            "external_event_self_link_probability",
            "external_event_self_link_cooldown_hours",
            "external_link_share_cooldown_hours",
            "news_max_items_per_source",
            "news_sources",
            "news_hot_sources",
            "news_hot_max_items",
            "enable_web_exploration",
            "enable_web_exploration_boredom_search",
            "web_exploration_min_interval_hours",
            "web_exploration_share_probability",
            "web_exploration_max_results",
            "web_exploration_interests",
            "WEB_EXPLORATION_API_BASE_URL",
            "WEB_EXPLORATION_API_KEY",
            "WEB_EXPLORATION_API_MODEL",
            "enable_qzone_integration",
            "QZONE_COOKIE",
            "enable_qzone_life_publish",
            "qzone_life_publish_min_interval_hours",
            "qzone_life_publish_probability",
            "qzone_life_publish_max_daily",
            "qzone_life_publish_window_mode",
            "qzone_life_publish_windows",
            "qzone_life_publish_allow_insomnia_night",
            "qzone_life_publish_intra_day_gap_minutes",
            "qzone_life_publish_similarity_threshold",
            "qzone_publish_style_prompt",
            "enable_qzone_generated_image_publish",
            "qzone_generated_image_probability",
            "qzone_publish_image_style_prompt",
            "enable_qzone_comment_inbox",
            "qzone_comment_inbox_interval_minutes",
            "qzone_comment_inbox_recent_posts",
            "qzone_comment_inbox_max_replies_per_tick",
            "enable_qzone_emotional_vent_publish",
            "qzone_emotional_vent_threshold",
            "qzone_emotional_vent_cooldown_hours",
            "qzone_emotional_vent_probability",
            "enable_reading_archive_integration",
            "enable_reading_archive_boredom_read",
            "enable_reading_archive_ask_recommendation",
            "enable_reading_archive_vision",
            "enable_reading_archive_page_comments",
            "enable_reading_archive_rating",
            "enable_reading_archive_preference_influence",
            "enable_unanswered_screen_peek_followup",
            "unanswered_screen_peek_after_minutes",
            "unanswered_screen_peek_cooldown_minutes",
            "enable_goodnight_screen_check",
            "goodnight_screen_check_delay_minutes",
            "reading_archive_min_interval_hours",
            "reading_archive_max_photo_count",
            "reading_archive_share_probability",
            "reading_archive_ask_probability",
            "reading_archive_preference_min_ratings",
            "reading_archive_preference_max_terms",
            "reading_archive_default_keywords",
            "reading_archive_blocked_tags",
            "enable_unanswered_screen_peek_followup",
            "unanswered_screen_peek_after_minutes",
            "unanswered_screen_peek_cooldown_minutes",
            "enable_goodnight_screen_check",
            "goodnight_screen_check_delay_minutes",
            "enable_creative_writing",
            "enable_creative_work_read_guard",
            "creative_inspiration_probability",
            "creative_share_probability",
            "creative_chars_per_session",
            "creative_max_active_projects",
            "creative_hidden_mode",
            "creative_direction_prompt",
            "enable_worldbook_member_recognition",
            "worldbook_auto_import",
            "worldbook_member_match_aliases",
            "worldbook_self_registration",
            "worldbook_self_registration_block_words",
            "worldbook_self_registration_block_reply",
            "worldbook_auto_pending_observations",
            "worldbook_member_inject_limit",
            "worldbook_config_paths",
            "cross_user_memory_owner_only",
            "enable_atrelay_tools",
            "atrelay_require_worldbook_first",
            "atrelay_member_cache_minutes",
            "atrelay_sensitive_confirm",
            "enable_atrelay_llm_rewrite",
            "atrelay_default_relay_style",
            "atrelay_multi_target_limit",
        ]
        # Keep cycle settings in the overview even when a long-lived AstrBot
        # process has not rebuilt its schema index after a plugin upgrade.
        # The settings endpoint already accepts this explicit contract; the
        # runtime snapshot must expose the same fields for the panel editor.
        for key in CYCLE_SETTING_KEYS:
            if key not in keys:
                keys.append(key)
        provider_keys = self._schema_provider_keys(public_only=True)
        for key in sorted(self._schema_setting_keys(public_only=True) - PAGE_PRIVATE_CONFIG_KEYS):
            if key not in keys and key not in provider_keys:
                keys.append(key)
        values = {
            key: runtime_persona_setting(
                self.plugin,
                key,
                getattr(self.plugin, key, self._config_get(key)),
            )
            for key in keys
        }
        # This is the persisted primary-persona authority. It must remain
        # stable when the WebUI is viewing a secondary persona; the resolver's
        # active scope is only for persona-scoped settings.
        primary_getter = getattr(self.plugin, "_primary_persona_id", None)
        primary_id = (
            primary_getter()
            if callable(primary_getter)
            else getattr(self.plugin, "plugin_specific_persona_id", "")
        )
        values["plugin_specific_persona_id"] = self._single_line(primary_id, 120)
        values["environment_perception_timezone"] = getattr(
            self.plugin,
            "environment_perception_timezone_setting",
            self._config_get("environment_perception_timezone") or "global",
        )
        values["environment_perception_timezone_effective"] = getattr(
            self.plugin,
            "environment_perception_timezone",
            "Asia/Shanghai",
        )
        if "photo_reference_catalog" in values:
            try:
                values["photo_reference_catalog"] = validate_and_serialize(
                    self._photo_reference_catalog_snapshot(sync_runtime=True),
                    preset_names=self._photo_reference_preset_names(),
                )
            except CatalogValidationError as exc:
                logger.warning(
                    "总览参考图目录序列化失败: %s",
                    self._single_line(exc, 180),
                )
                persisted_catalog = self._config_get_raw("photo_reference_catalog", [])
                values["photo_reference_catalog"] = persisted_catalog if isinstance(persisted_catalog, list) else []
        for key in self._schema_bool_keys():
            if key not in values:
                continue
            persisted = self._config_get_raw(key, None)
            if persisted not in (None, ""):
                values[key] = self._normalize_bool_value(persisted)
        busy_reply_defaults = {
            "enable_busy_reply_gate": False,
            "busy_reply_min_delay_seconds": 60,
            "busy_reply_max_delay_seconds": 300,
            "busy_reply_proactive_resume_buffer_minutes": 10,
        }
        for key, default in busy_reply_defaults.items():
            persisted = self._config_get_raw(key, None)
            if persisted in (None, ""):
                values[key] = default
            elif values.get(key) in (None, ""):
                values[key] = persisted
        segmented_setting_keys = (
            "enable_segmented_proactive_reply",
            "enable_llm_controlled_segmenting",
            "llm_controlled_segmenting_prompt",
            "enable_segmented_plugin_rules",
            "segmented_proactive_scope",
            "segmented_proactive_chat_scope",
            "enable_segmented_proactive_chat_profiles",
            "segmented_proactive_private_enabled",
            "segmented_proactive_private_scope",
            "segmented_proactive_private_threshold",
            "segmented_proactive_private_min_segment_chars",
            "segmented_proactive_private_max_segments",
            "segmented_proactive_private_send_as_forward",
            "segmented_proactive_private_interval_method",
            "segmented_proactive_private_interval_min",
            "segmented_proactive_private_interval_max",
            "segmented_proactive_private_log_base",
            "segmented_proactive_group_enabled",
            "segmented_proactive_group_scope",
            "segmented_proactive_group_threshold",
            "segmented_proactive_group_min_segment_chars",
            "segmented_proactive_group_max_segments",
            "segmented_proactive_group_send_as_forward",
            "segmented_proactive_group_interval_method",
            "segmented_proactive_group_interval_min",
            "segmented_proactive_group_interval_max",
            "segmented_proactive_group_log_base",
            "segmented_proactive_threshold",
            "segmented_proactive_min_segment_chars",
            "segmented_proactive_max_segments",
            "segmented_proactive_send_as_forward",
            "segmented_proactive_voice_strategy",
            "segmented_proactive_image_strategy",
            "segmented_proactive_at_strategy",
            "segmented_proactive_face_strategy",
            "segmented_proactive_component_order",
            "segmented_proactive_other_strategy",
            "segmented_proactive_split_mode",
            "segmented_proactive_regex",
            "segmented_proactive_split_words",
            "enable_segmented_proactive_content_cleanup",
            "segmented_proactive_content_cleanup_scope",
            "segmented_proactive_content_cleanup_rule",
            "segmented_proactive_content_cleanup_words",
            "enable_segmented_proactive_content_replacement",
            "segmented_proactive_content_replacements",
            "segmented_proactive_interval_method",
            "segmented_proactive_interval_min",
            "segmented_proactive_interval_max",
            "segmented_proactive_log_base",
        )
        for key in segmented_setting_keys:
            persisted = self._config_get_raw(key, None)
            if persisted not in (None, ""):
                values[key] = deepcopy(persisted)
        values["private_user_aliases"] = self._private_alias_config_text("private_user_aliases")
        values["private_user_delivery_aliases"] = self._private_alias_config_text("private_user_delivery_aliases")
        values.update(
            {
                "enable_reading_archive_integration": bool(getattr(self.plugin, "enable_reading_archive_integration", False)),
                "enable_reading_archive_boredom_read": bool(getattr(self.plugin, "enable_reading_archive_boredom_read", False)),
                "enable_reading_archive_ask_recommendation": bool(getattr(self.plugin, "enable_reading_archive_ask_recommendation", False)),
                "enable_reading_archive_vision": bool(getattr(self.plugin, "enable_reading_archive_vision", True)),
                "enable_reading_archive_page_comments": bool(getattr(self.plugin, "enable_reading_archive_page_comments", True)),
                "enable_reading_archive_rating": bool(getattr(self.plugin, "enable_reading_archive_rating", True)),
                "enable_reading_archive_preference_influence": bool(getattr(self.plugin, "enable_reading_archive_preference_influence", True)),
                "reading_archive_min_interval_hours": getattr(self.plugin, "reading_archive_min_interval_hours", 18),
                "reading_archive_max_photo_count": getattr(self.plugin, "reading_archive_max_photo_count", 60),
                "reading_archive_share_probability": getattr(self.plugin, "reading_archive_share_probability", 0.18),
                "reading_archive_ask_probability": getattr(self.plugin, "reading_archive_ask_probability", 0.16),
                "reading_archive_preference_min_ratings": getattr(self.plugin, "reading_archive_preference_min_ratings", 5),
                "reading_archive_preference_max_terms": getattr(self.plugin, "reading_archive_preference_max_terms", 8),
                "reading_archive_default_keywords": getattr(self.plugin, "reading_archive_default_keywords", ""),
                "reading_archive_blocked_tags": getattr(self.plugin, "reading_archive_blocked_tags", "連載中,長篇,青年漫"),
                "group_repeat_trigger_threshold": int(getattr(self.plugin, "group_repeat_trigger_threshold", 4) or 4),
                "group_repeat_count_distinct_users_only": bool(getattr(self.plugin, "group_repeat_count_distinct_users_only", False)),
                "group_repeat_follow_probability": int(round(float(getattr(self.plugin, "group_repeat_follow_probability", 0.18) or 0) * 100)),
                "group_repeat_interrupt_probability": int(round(float(getattr(self.plugin, "group_repeat_interrupt_probability", 0.10) or 0) * 100)),
                "group_repeat_interrupt_probability_step": int(round(float(getattr(self.plugin, "group_repeat_interrupt_probability_step", 0.12) or 0) * 100)),
            }
        )
        def _percent_attr(name: str, default: float = 0.0, *, inherit: bool = False) -> int:
            try:
                raw = float(getattr(self.plugin, name, default) or 0.0)
            except (TypeError, ValueError):
                raw = default
            if inherit and raw < 0:
                return -1
            if raw <= 1:
                raw *= 100
            return max(-1 if inherit else 0, min(100, int(round(raw))))

        values.update(
            {
                "tts_trigger_probability": _percent_attr("tts_trigger_probability", 0.25),
                "tts_private_trigger_probability": _percent_attr("tts_private_trigger_probability", -0.01, inherit=True),
                "tts_group_trigger_probability": _percent_attr("tts_group_trigger_probability", -0.01, inherit=True),
                "auto_voice_probability": _percent_attr("auto_voice_probability", 0.25),
                "main_user_voice_probability": _percent_attr("main_user_voice_probability", -0.01, inherit=True),
                "main_user_mention_voice_probability": _percent_attr("main_user_mention_voice_probability", 0.0),
                "rest_reply_probability": _percent_attr("rest_reply_probability", 0.18),
            }
        )
        for key in self._schema_bool_keys():
            if key in values:
                values[key] = self._normalize_bool_value(values[key])
        for key in PAGE_PRIVATE_CONFIG_KEYS:
            values.pop(key, None)
        active_getter = getattr(self.plugin, "_active_persona_scope", None)
        active_persona = str(active_getter() if callable(active_getter) else "").strip()
        primary_getter = getattr(self.plugin, "_primary_persona_id", None)
        primary_persona = str(
            primary_getter()
            if callable(primary_getter)
            else getattr(self.plugin, "plugin_specific_persona_id", "")
        ).strip()
        manifest_getter = getattr(self.plugin, "_persona_scope_manifest", None)
        setting_getter = getattr(self.plugin, "get_persona_setting", None)
        if active_persona and active_persona != primary_persona and callable(manifest_getter) and callable(setting_getter):
            manifest = manifest_getter()
            for key in tuple(values):
                entry = manifest.get(key) if isinstance(manifest, dict) else None
                if isinstance(entry, dict) and entry.get("scope") == "persona":
                    try:
                        values[key] = setting_getter(key, active_persona, values[key])
                    except Exception:
                        pass
        return values

    def _apply_config_value(self, key: str, value: Any, overrides: dict[str, Any] | None = None) -> None:
        if key == "relationship_stage_provider_routes":
            normalized = normalize_relationship_stage_provider_routes(value)
            self._set_config_value(key, normalized)
            self.plugin.relationship_stage_provider_routes = normalized
            return
        if key == "relationship_stage_policy":
            normalized = normalize_relationship_stage_policy(value)
            self._set_config_value(key, relationship_stage_policy_json(normalized))
            self.plugin.relationship_stage_policy = normalized
            return
        if key == "relationship_positive_stage_cap_key":
            old_key = normalize_relationship_positive_stage_cap_key(
                getattr(self.plugin, key, "close")
            )
            new_key = normalize_relationship_positive_stage_cap_key(value)
            self._set_config_value(key, new_key)
            self.plugin.relationship_positive_stage_cap_key = new_key
            if isinstance(overrides, dict) and overrides.get("__defer_relationship_data_save"):
                overrides["__relationship_positive_cap_change"] = (old_key, new_key)
                overrides["__relationship_profile_batch"] = True
                return
            users = self.plugin.data.get("users", {}) if isinstance(getattr(self.plugin, "data", None), dict) else {}
            touched = False
            if isinstance(users, dict):
                for user in users.values():
                    if not isinstance(user, dict):
                        continue
                    previous_cap = user.get("relationship_positive_stage_cap_key")
                    result = migrate_relationship_positive_stage_cap(
                        user,
                        old_cap_key=old_key,
                        new_cap_key=new_key,
                        now=time.time(),
                    )
                    touched = touched or previous_cap != new_key or bool(result.get("changed"))
            if touched:
                if isinstance(overrides, dict) and overrides.get("__defer_relationship_data_save"):
                    overrides["__relationship_data_changed"] = True
                else:
                    self.plugin._save_data_sync(sections={"users"})
            return
        if key == "normal_interaction_band_cap":
            previous_runtime_cap = normalize_normal_interaction_band_cap(getattr(self.plugin, key, "warm"))
            normalized = normalize_normal_interaction_band_cap(value)
            self._set_config_value(key, normalized)
            self.plugin.normal_interaction_band_cap = normalized
            if isinstance(overrides, dict) and overrides.get("__defer_relationship_data_save"):
                overrides["__relationship_interaction_cap_change"] = (
                    previous_runtime_cap,
                    normalized,
                )
                overrides["__relationship_profile_batch"] = True
                return
            users = self.plugin.data.get("users", {}) if isinstance(getattr(self.plugin, "data", None), dict) else {}
            touched = False
            if isinstance(users, dict):
                for user in users.values():
                    if not isinstance(user, dict):
                        continue
                    before = deepcopy(user.get("current_interaction"))
                    previous_cap = user.get("normal_interaction_band_cap")
                    user["current_interaction"] = current_interaction_projection(
                        before,
                        relationship_role=user.get("relationship_role"),
                        relationship_mode=user.get("relationship_mode"),
                        relationship_score=user.get("relationship_score"),
                        normal_interaction_band_cap=normalized,
                        now=time.time(),
                    )
                    user["normal_interaction_band_cap"] = normalized
                    touched = touched or previous_cap != normalized or before != user["current_interaction"]
            if touched:
                if isinstance(overrides, dict) and overrides.get("__defer_relationship_data_save"):
                    overrides["__relationship_data_changed"] = True
                else:
                    self.plugin._save_data_sync(sections={"users"})
            return
        if key in {"owner_group_relationship_projection", "owner_group_interaction_projection"}:
            normalized = self._normalize_bool_value(value)
            self._set_config_value(key, normalized)
            setattr(self.plugin, key, normalized)
            return
        self._set_config_value(key, value)
        if key == "enable_llm_streaming":
            self.plugin.enable_llm_streaming = self._normalize_bool_value(value)
            return
        if key == "enable_body_monitor_integration":
            enabled = self._normalize_bool_value(value)
            self._forward_runtime_config_effects(key, enabled, overrides)
            return
        if key == "enable_multi_persona_mode":
            enabled = self._normalize_bool_value(value)
            self._forward_runtime_config_effects(key, enabled, overrides)
            primary_getter = getattr(self.plugin, "_primary_persona_id", None)
            primary = primary_getter() if callable(primary_getter) else ""
            if primary:
                self.plugin._page_current_persona_id = primary
            return
        if key == "plugin_specific_persona_id":
            normalizer = getattr(self.plugin, "_sanitize_persona_id", None)
            normalized = normalizer(value) if callable(normalizer) else str(value or "").strip()[:96]
            if bool(getattr(self.plugin, "enable_multi_persona_mode", False)):
                current_getter = getattr(self.plugin, "_primary_persona_id", None)
                current = current_getter() if callable(current_getter) else ""
                if current and normalized != current:
                    raise ValueError("多人格模式下不能切换主人格，请先关闭多人格模式")
            self.plugin.plugin_specific_persona_id = normalized
            self.plugin._page_current_persona_id = normalized
            if normalized:
                self.plugin._multi_persona_primary_requires_configuration = False
                self.plugin._multi_persona_primary_invalid = False
                self.plugin._multi_persona_enable_requested = bool(
                    getattr(self.plugin, "_multi_persona_enable_requested", False)
                    or getattr(self.plugin, "enable_multi_persona_mode", False)
                )
            return
        if key == "multi_persona_ids":
            self.plugin.multi_persona_ids = self.plugin._configured_multi_persona_ids()
            return
        if key == "photo_reference_catalog":
            loaded = load_catalog(
                value,
                catalog_version=CATALOG_VERSION,
                preset_names=self._photo_reference_preset_names(),
            )
            self._set_config_value("photo_reference_catalog_version", CATALOG_VERSION)
            self._set_config_value("photo_reference_catalog_user_cleared", not bool(loaded.references))
            self.plugin.photo_reference_catalog = loaded.references
            self.plugin.photo_reference_catalog_version = CATALOG_VERSION
            self.plugin.photo_reference_catalog_user_cleared = not bool(loaded.references)
            self.plugin.photo_reference_catalog_read_only = loaded.read_only
            return
        if key == "external_image_api_endpoints":
            normalizer = getattr(self.plugin, "_normalize_external_image_api_endpoints", None)
            endpoints = normalizer(value) if callable(normalizer) else (value if isinstance(value, list) else [])
            self.plugin.external_image_api_endpoints = endpoints
            self._sync_legacy_external_image_api_config_from_endpoints(endpoints)
            return
        if key == "external_image_download_use_environment_proxy":
            self.plugin.external_image_download_use_environment_proxy = self._normalize_bool_value(value)
            return
        if key == "photo_generation_negative_prompt_mode":
            # This setting is displayed through ``runtime_persona_setting``.
            # Keep the hot-applied runtime value in sync with the persisted
            # schema value so the panel does not revert to the startup default
            # until the next plugin restart.
            normalizer = getattr(self.plugin, "_normalize_photo_generation_negative_prompt_mode", None)
            normalized = (
                normalizer(value)
                if callable(normalizer)
                else self._normalize_setting_value(key, value)
            )
            self.plugin.photo_generation_negative_prompt_mode = normalized
            return
        if key == "provider_config_mode":
            normalizer = getattr(self.plugin, "_normalize_provider_config_mode", None)
            self.plugin.provider_config_mode = (
                normalizer(value, getattr(self.plugin, "config", None))
                if callable(normalizer)
                else str(value or "quick").strip().lower()
            )
            return
        if key == "background_llm_request_max_attempts":
            self.plugin.background_llm_request_max_attempts = self.plugin._normalize_request_max_attempts(value)
            return
        if key == "model_request_max_attempts_overrides":
            self.plugin.model_request_max_attempts_overrides = self.plugin._normalize_model_request_max_attempts_overrides(value)
            return
        if key == "model_timeout_overrides":
            normalizer = getattr(self.plugin, "_normalize_model_timeout_overrides", None)
            self.plugin.model_timeout_overrides = normalizer(value) if callable(normalizer) else {}
            return
        if key == "model_token_limit_overrides":
            normalizer = getattr(self.plugin, "_normalize_model_token_limit_overrides", None)
            self.plugin.model_token_limit_overrides = normalizer(value) if callable(normalizer) else {}
            return
        if key == "model_fallback_overrides":
            normalizer = getattr(self.plugin, "_normalize_model_fallback_overrides", None)
            self.plugin.model_fallback_overrides = normalizer(value) if callable(normalizer) else {}
            return
        if key == "model_replacement_scope":
            self.plugin.model_replacement_scope = normalize_scope(value)
            return
        if key == "model_replacement_rules":
            rules, warnings = build_rules(value)
            self.plugin.model_replacement_rules = rules
            for warning in warnings:
                logger.warning("模型替换规则：%s", self._single_line(warning, 180))
            return
        if key == "enable_sensitive_model_replacement":
            self.plugin.enable_sensitive_model_replacement = self._normalize_bool_value(value)
            return
        if key == "sensitive_replacement_keywords":
            self.plugin.sensitive_replacement_keywords = str(value or "").strip()
            return
        if key == "environment_perception_timezone":
            previous_timezone = str(
                getattr(self.plugin, "environment_perception_timezone", "") or ""
            )
            timezone_setting = _normalize_timezone_setting(value)
            resolver = getattr(self.plugin, "_resolve_environment_perception_timezone", None)
            timezone_name = resolver(timezone_setting) if callable(resolver) else _normalize_timezone_name(timezone_setting)
            self.plugin.environment_perception_timezone_setting = timezone_setting
            self.plugin.environment_perception_timezone = timezone_name
            runtime_overrides = overrides if isinstance(overrides, dict) else {}
            runtime_overrides["__previous_environment_perception_timezone"] = previous_timezone
            self._forward_runtime_config_effects(
                key,
                timezone_setting,
                runtime_overrides,
            )
            return
        if key == "enable_deepseek_peak_replacement":
            self.plugin.enable_deepseek_peak_replacement = self._normalize_bool_value(value)
            return
        if key == "enable_llm_streaming":
            self.plugin.enable_llm_streaming = self._normalize_bool_value(value)
            return
        if key in {"deepseek_peak_windows", "deepseek_peak_timezone", "deepseek_peak_match_keywords"}:
            setattr(self.plugin, key, str(value or "").strip())
            return
        if key == "proactive_intensity_preset":
            normalizer = getattr(self.plugin, "_normalize_proactive_intensity_preset", None)
            self.plugin.proactive_intensity_preset = (
                normalizer(value)
                if callable(normalizer)
                else str(value or "off").strip().lower()
            )
            return
        if key == "max_daily_messages":
            self.plugin.max_daily_messages = max(0, self._int(value))
            self._forward_runtime_config_effects(
                key,
                self.plugin.max_daily_messages,
                overrides,
            )
            return
        if key == "page_font_family":
            text = str(value or "original").strip().lower()
            self.plugin.page_font_family = text if text in PAGE_FONT_NAMES else "original"
            return
        if key == "page_theme":
            text = str(value or "classic").strip().lower()
            self.plugin.page_theme = text if text in PAGE_THEME_NAMES else "classic"
            return
        if key == "storage_backend":
            backend = str(value or "json").strip().lower() or "json"
            self.plugin.storage_backend = backend if backend in {"json", "sqlite"} else "json"
            self._forward_runtime_config_effects(
                key,
                self.plugin.storage_backend,
                overrides,
            )
            return
        if key == "storage_sqlite_path":
            self.plugin.storage_sqlite_path = str(value or "").strip()
            self._forward_runtime_config_effects(
                key,
                self.plugin.storage_sqlite_path,
                overrides,
            )
            return
        attr_map = {
            "FAST_RESPONSE_PROVIDER_ID": "fast_response_provider_id",
            "COMPLEX_REASONING_PROVIDER_ID": "complex_reasoning_provider_id",
            "CREATIVE_MODEL_PROVIDER_ID": "creative_model_provider_id",
            "EMBEDDING_PROVIDER_ID": "embedding_provider_id",
            "LLM_PROVIDER_ID": "llm_provider_id",
            "MAI_STYLE_PROVIDER_ID": "mai_style_provider_id",
            "DAILY_PLAN_PROVIDER_ID": "daily_plan_provider_id",
            "DETAIL_ENHANCEMENT_PROVIDER_ID": "detail_enhancement_provider_id",
            "DREAM_DIARY_PROVIDER_ID": "dream_diary_provider_id",
            "CREATIVE_PROVIDER_ID": "creative_provider_id",
            "CREATIVE_OUTLINE_PROVIDER_ID": "creative_outline_provider_id",
            "CREATIVE_REVIEW_PROVIDER_ID": "creative_review_provider_id",
            "VOICE_PROMPT_PROVIDER_ID": "voice_prompt_provider_id",
            "PHOTO_PROMPT_PROVIDER_ID": "photo_prompt_provider_id",
            "NARRATION_PROVIDER_ID": "narration_provider_id",
            "HISTORY_SUMMARY_PROVIDER_ID": "history_summary_provider_id",
            "RESPONSE_REVIEW_PROVIDER_ID": "response_review_provider_id",
            "SMART_SILENCE_PROVIDER_ID": "smart_silence_provider_id",
            "PROACTIVE_PERSONA_JUDGE_PROVIDER_ID": "proactive_persona_judge_provider_id",
            "TROUBLESHOOTING_PROVIDER_ID": "troubleshooting_provider_id",
            "DAILY_REVIEW_PROVIDER_ID": "daily_review_provider_id",
            "RELATIONSHIP_ANALYSIS_PROVIDER_ID": "relationship_analysis_provider_id",
            "EMOTION_JUDGEMENT_PROVIDER_ID": "emotion_judgement_provider_id",
            "COMPANION_MEMORY_PROVIDER_ID": "companion_memory_provider_id",
            "DIALOGUE_EPISODE_PROVIDER_ID": "dialogue_episode_provider_id",
            "GROUP_INTERJECT_PROVIDER_ID": "group_interject_provider_id",
            "GROUP_EPISODE_PROVIDER_ID": "group_episode_provider_id",
            "GROUP_SLANG_PROVIDER_ID": "group_slang_provider_id",
            "GROUP_FOLLOWUP_JUDGE_PROVIDER_ID": "group_followup_judge_provider_id",
            "GROUP_MEMBER_SAFETY_PROVIDER_ID": "group_member_safety_provider_id",
            "FORWARD_MESSAGE_PROVIDER_ID": "forward_message_provider_id",
            "PLUGIN_VISION_PROVIDER_ID": "plugin_vision_provider_id",
            "REACTION_EXPRESSION_EMBEDDING_PROVIDER_ID": "reaction_expression_embedding_provider_id",
            "NEWS_PROVIDER_ID": "news_provider_id",
            "WEB_EXPLORATION_PROVIDER_ID": "web_exploration_provider_id",
            "DEEPSEEK_PEAK_REPLACEMENT_PROVIDER_ID": "deepseek_peak_replacement_provider_id",
            "SENSITIVE_REPLACEMENT_PROVIDER_ID": "sensitive_replacement_provider_id",
            "WEB_EXPLORATION_API_BASE_URL": "web_exploration_api_base_url",
            "WEB_EXPLORATION_API_KEY": "web_exploration_api_key",
            "WEB_EXPLORATION_API_MODEL": "web_exploration_api_model",
            "SMART_MESSAGE_DEBOUNCE_PROVIDER_ID": "smart_message_debounce_provider_id",
            "REST_WAKEUP_PROVIDER_ID": "rest_wakeup_provider_id",
            "COMFYUI_TEXT2IMG_WORKFLOW_NAME": "comfyui_text2img_workflow_name",
            "COMFYUI_SELFIE_WORKFLOW_NAME": "comfyui_selfie_workflow_name",
            "external_image_api_platform": "external_image_api_platform",
            "EXTERNAL_IMAGE_API_BASE_URL": "external_image_api_base_url",
            "EXTERNAL_IMAGE_API_KEY": "external_image_api_key",
            "EXTERNAL_IMAGE_API_MODEL": "external_image_api_model",
            "external_image_api_custom_headers": "external_image_api_custom_headers",
            "external_image_download_proxy": "external_image_download_proxy",
            "backup_external_image_api_platform": "backup_external_image_api_platform",
            "BACKUP_EXTERNAL_IMAGE_API_BASE_URL": "backup_external_image_api_base_url",
            "BACKUP_EXTERNAL_IMAGE_API_KEY": "backup_external_image_api_key",
            "BACKUP_EXTERNAL_IMAGE_API_MODEL": "backup_external_image_api_model",
            "backup_external_image_api_custom_headers": "backup_external_image_api_custom_headers",
        }
        if key in attr_map:
            setattr(self.plugin, attr_map[key], str(value or "").strip())
            if key == "DREAM_DIARY_PROVIDER_ID":
                shared = str(value or "").strip()
                self.plugin.dream_provider_id = shared
                self.plugin.diary_provider_id = shared
            return
        if key == "group_access_mode":
            self.plugin.group_access_mode = str(value or "whitelist").lower()
            return
        if key == "group_whitelist_ids":
            self.plugin.group_whitelist_ids = list(value or [])
            return
        if key == "group_blacklist_ids":
            self.plugin.group_blacklist_ids = list(value or [])
            return
        if key == "target_user_ids":
            self.plugin.target_user_ids = self._normalize_private_target_id_list(value)
            return
        if key == "QZONE_COOKIE":
            self.plugin.qzone_cookie = str(value or "").strip()
            return
        if key == "roleplay_knowledge_source_ids":
            normalizer = getattr(self.plugin, "_normalize_roleplay_knowledge_source_ids", None)
            self.plugin.roleplay_knowledge_source_ids = normalizer(value) if callable(normalizer) else list(value or [])
            return
        reading_archive_attr_map = {
            "enable_reading_archive_integration": "enable_reading_archive_integration",
            "enable_reading_archive_boredom_read": "enable_reading_archive_boredom_read",
            "enable_reading_archive_ask_recommendation": "enable_reading_archive_ask_recommendation",
            "enable_reading_archive_vision": "enable_reading_archive_vision",
            "enable_reading_archive_page_comments": "enable_reading_archive_page_comments",
            "enable_reading_archive_rating": "enable_reading_archive_rating",
            "enable_reading_archive_preference_influence": "enable_reading_archive_preference_influence",
            "reading_archive_min_interval_hours": "reading_archive_min_interval_hours",
            "reading_archive_max_photo_count": "reading_archive_max_photo_count",
            "reading_archive_share_probability": "reading_archive_share_probability",
            "reading_archive_ask_probability": "reading_archive_ask_probability",
            "reading_archive_preference_min_ratings": "reading_archive_preference_min_ratings",
            "reading_archive_preference_max_terms": "reading_archive_preference_max_terms",
            "reading_archive_default_keywords": "reading_archive_default_keywords",
            "reading_archive_blocked_tags": "reading_archive_blocked_tags",
        }
        if key in reading_archive_attr_map:
            setattr(self.plugin, reading_archive_attr_map[key], value)
            return
        if key == "plugin_specific_persona_id":
            self.plugin.plugin_specific_persona_id = str(value or "").strip()
            self.plugin._default_persona_prompt_cache = ""
            self.plugin._default_persona_prompt_cache_persona_id = ""
            self.plugin._default_persona_prompt_cache_by_scope = {}
            return
        if key == "private_user_aliases":
            self.plugin.private_user_aliases = self.plugin._parse_private_user_aliases(value)
            if self.plugin._merge_private_user_alias_records():
                self.plugin._save_data_sync(
                    sections={"users", "private_user_alias_merge_backups"}
                )
            return
        if key == "private_user_delivery_aliases":
            self.plugin.private_user_delivery_aliases = self.plugin._parse_private_user_aliases(value)
            users = self.plugin.data.get("users", {})
            if isinstance(users, dict):
                for raw_user_id, user in users.items():
                    if isinstance(user, dict):
                        self.plugin._ensure_private_user_umo(str(raw_user_id), user)
                self.plugin._save_data_sync(sections={"users"})
            return
        if key == "worldbook_self_registration_block_words":
            parser = getattr(self.plugin, "_parse_text_list_config", None)
            if callable(parser):
                self.plugin.worldbook_self_registration_block_words = parser(value, limit=120)
            else:
                self.plugin.worldbook_self_registration_block_words = value
            return
        if key == "worldbook_self_registration_block_reply":
            reply = str(value or "").strip()
            self.plugin.worldbook_self_registration_block_reply = "这个称呼我不记。" if reply in {"这个称呼我先不记。", "你是小猪"} else reply
            return
        if key in {"group_repeat_follow_probability", "group_repeat_interrupt_probability", "group_repeat_interrupt_probability_step"}:
            raw = float(value or 0)
            setattr(self.plugin, key, max(0.0, min(1.0, raw / 100.0 if raw > 1 else raw)))
            return
        if key == "rest_reply_probability":
            raw = float(value or 0)
            setattr(self.plugin, key, max(0.0, min(1.0, raw / 100.0 if raw > 1 else raw)))
            return
        if key in {"proactive_photo_text_probability", "proactive_share_probability"}:
            raw = float(value or 0)
            setattr(self.plugin, key, max(0.0, min(1.0, raw / 100.0 if raw > 1 else raw)))
            return
        if key == "enable_tts_enhancement" or key in TTS_RUNTIME_KEYS:
            self._forward_runtime_config_effects(key, value, overrides)
            if key == "tts_generation_mode":
                # Keep the live value authoritative even when AstrBot's config wrapper
                # still exposes a stale grouped/default value during the same request.
                normalized_mode = self._normalize_setting_value("tts_generation_mode", value)
                self.plugin.tts_generation_mode = normalized_mode
            return
        if key == "enable_passive_response_review":
            normalized = self._normalize_bool_value(value)
            self.plugin.enable_passive_response_review = normalized
            self.plugin.enable_response_self_review = normalized
            return
        if key == "passive_review_mode":
            normalized = self._normalize_setting_value(key, value)
            self.plugin.passive_review_mode = normalized
            self.plugin.response_review_mode = normalized
            return
        if key in self._allowed_feature_keys():
            normalized = self._normalize_bool_value(value)
            setattr(self.plugin, key, normalized)
            self._forward_runtime_config_effects(key, normalized, overrides)
            return
        if key in self._allowed_setting_keys():
            setattr(self.plugin, key, value)

    def _allowed_feature_keys(self) -> set[str]:
        return {
            "enable_proactive_only_mode",
            "enable_auto_user_profile_creation",
            "enable_mai_style_integration",
            "enable_companion_memory",
            "enable_expression_learning",
            "enable_intent_emotion_analysis",
            "enable_response_self_review",
            "enable_passive_response_review",
            "enable_framework_error_leak_guard",
            "enable_outbound_secret_redaction",
            "enable_proactive_message_review",
            "enable_smart_silence",
            "enable_llm_proactive_message",
            "enable_llm_proactive_persona_judge",
            "enable_reaction_expression_experiment",
            "enable_maslow_motivation_experiment",
            "enable_experimental_motivation_model",
            "enable_experimental_bluetooth_wakeup",
            "enable_personality_iteration_experiment",
            "enable_daily_case_review_experiment",
            "enable_passive_topic_suppression",
            "enable_custom_relationship_stage_policy",
            "enable_relationship_stage_provider_routing",
            "enable_group_relationship_affinity",
            "enable_relationship_content_tiers",
            "enable_relationship_analysis",
            "enable_relationship_state_machine",
            "enable_emotion_simulation",
            "enable_dialogue_episode_memory",
            "enable_open_loop_tracking",
            "enable_user_habit_learning",
            "enable_food_menu_recommendation",
            "enable_personal_goals",
            "enable_humanized_states",
            "enable_health_state",
            "enable_hunger_state",
            "enable_segmented_proactive_reply",
            "enable_proactive_quote_trigger_message",
            "enable_quote_group_reply",
            "enable_quote_group_interjection",
            "enable_quote_private_proactive",
            "enable_photo_text_action",
            "inject_passive_states",
            "enable_passive_state_delta_injection",
            "enable_passive_state_continuity_anchor",
            "enable_cycle_state",
            "enable_skill_growth_simulation",
            "enable_skill_growth_passive_injection",
            "enable_message_debounce",
            "enable_smart_message_debounce",
            "enable_recall_enhancement",
            "enable_recall_cancel_reply",
            "enable_recall_message_cache",
            "enable_recall_transcribe_command",
            "enable_forbidden_word_recall",
            "enable_semantic_message_debounce",
            "enable_environment_perception",
            "enable_balance_awareness",
            "enable_holiday_perception",
            "enable_platform_perception",
            "enable_model_perception",
            "enable_worldview_perception",
            "enable_lunar_perception",
            "enable_solar_term_perception",
            "enable_almanac_perception",
            "enable_group_companion",
            "enable_group_social_context",
            "enable_group_member_safety",
            "enable_group_slang_learning",
            "enable_group_member_profiles",
            "enable_group_context_injection",
            "enable_group_history_injection",
            "enable_group_image_understanding",
            "enable_group_image_wakeup",
            "enable_group_injection_guard",
            "enable_group_persona_denoise",
            "enable_forward_message_adaptation",
            "enable_group_reality_promise_guard",
            "enable_group_wakeup_enhancement",
            "enable_group_wakeup_question",
            "enable_group_wakeup_cold_group",
            "enable_group_high_intensity_mode",
            "enable_group_air_reply_guard",
            "enable_private_image_self_recognition",
            "enable_private_image_gif_enhancement",
            "enable_group_conversation_followup",
            "enable_group_interjection",
            "enable_group_repeat_follow",
            "group_repeat_count_distinct_users_only",
            "enable_group_topic_threads",
            "enable_group_episode_memory",
            "enable_group_interjection_feedback",
            "enable_group_slang_meanings",
            "enable_group_slang_web_search",
            "enable_group_relationship_graph",
            "enable_group_privacy_guard",
            "enable_group_third_party_portrait_guard",
            "enable_worldbook_member_recognition",
            "enable_cross_user_memory_bridge",
            "enable_group_scene_awareness",
            "enable_group_reality_promise_guard",
            "enable_atrelay_tools",
            "enable_livingmemory_integration",
            "enable_bilibili_integration",
            "enable_bilibili_boredom_watch",
            "enable_news_integration",
            "enable_news_boredom_read",
            "enable_news_daily_hot_read",
            "enable_ai_daily_watch",
            "enable_external_event_self_link",
            "enable_body_monitor_integration",
            "enable_web_exploration",
            "enable_web_exploration_boredom_search",
            "enable_qzone_integration",
            "enable_qzone_life_publish",
            "enable_qzone_generated_image_publish",
            "enable_qzone_comment_inbox",
            "enable_qzone_emotional_vent_publish",
            "enable_reading_archive_integration",
            "enable_reading_archive_boredom_read",
            "enable_reading_archive_ask_recommendation",
            "enable_reading_archive_vision",
            "enable_reading_archive_page_comments",
            "enable_reading_archive_rating",
            "enable_reading_archive_preference_influence",
            "enable_unanswered_screen_peek_followup",
            "enable_goodnight_screen_check",
            "enable_screen_glance_action",
            "enable_poke_action",
            "enable_voice_action",
            "enable_yesterday_screen_diary_context",
            "enable_tts_enhancement",
            "enable_creative_writing",
            "enable_creative_work_read_guard",
            "creative_hidden_mode",
            "enable_reply_interception_forward",
        }

    def _allowed_setting_keys(self) -> set[str]:
        keys = {
            "expression_private_learning_source_mode",
            "expression_private_learning_source_ids",
            "expression_group_learning_source_mode",
            "expression_group_learning_source_ids",
            "expression_group_learning_daily_batch_limit",
            "expression_group_learning_min_new_messages",
            "expression_private_application_mode",
            "expression_private_application_user_ids",
            "expression_group_application_mode",
            "expression_group_application_ids",
            "bot_name",
            "page_font_family",
            "page_theme",
            "provider_config_mode",
            "model_timeout_overrides",
            "background_llm_request_max_attempts",
            "model_request_max_attempts_overrides",
            "model_token_limit_overrides",
            "model_fallback_overrides",
            "model_replacement_scope",
            "model_replacement_rules",
            "enable_sensitive_model_replacement",
            "sensitive_replacement_keywords",
            "enable_deepseek_peak_replacement",
            "enable_llm_streaming",
            "deepseek_peak_windows",
            "deepseek_peak_timezone",
            "deepseek_peak_match_keywords",
            "enable_proactive_only_mode",
            "enable_body_monitor_integration",
            "plugin_specific_persona_id",
            "enable_multi_persona_mode",
            "multi_persona_ids",
            "target_user_ids",
            "private_user_aliases",
            "private_user_delivery_aliases",
            "target_platform",
            "require_private_opt_in",
            "environment_perception_timezone",
            "holiday_country",
            "enable_environment_perception",
            "enable_holiday_perception",
            "enable_platform_perception",
            "enable_model_perception",
            "enable_worldview_perception",
            "enable_lunar_perception",
            "enable_solar_term_perception",
            "enable_almanac_perception",
            "default_nickname",
            "enable_auto_user_profile_creation",
            "portrait_global_mode",
            "auto_profile_platforms",
            "default_nickname_strategy",
            "default_proactive_enabled",
            "default_proactive_daily_limit",
            "default_interaction_band",
            "enable_custom_relationship_stage_policy",
            "relationship_stage_policy",
            "relationship_stage_provider_routes",
            "relationship_positive_stage_cap_key",
            "normal_interaction_band_cap",
            "owner_group_relationship_projection",
            "owner_group_interaction_projection",
            "enable_relationship_content_tiers",
            "enable_flirt_content_tier",
            "owner_exclusive_label",
            "owner_exclusive_tone",
            "owner_exclusive_address_style",
            "owner_exclusive_proactive_limit",
            "enable_group_relationship_affinity",
            "group_relationship_affinity_allowlist",
            "group_relationship_daily_net_cap",
            "group_relationship_window_minutes",
            "group_relationship_window_absolute_cap",
            "group_relationship_person_daily_absolute_cap",
            "group_relationship_scope_daily_absolute_cap",
            "relationship_event_window_minutes",
            "relationship_positive_event_cap",
            "relationship_negative_event_cap",
            "relationship_positive_daily_cap",
            "relationship_decay_grace_days",
            "relationship_decay_early_per_day",
            "relationship_decay_middle_per_day",
            "relationship_decay_late_per_day",
            "default_style",
            "reply_style_prompt",
            "enable_persona_voice_channels",
            "persona_conversation_voice_prompt",
            "persona_creative_voice_prompt",
            "persona_planning_voice_prompt",
            "persona_inner_voice_prompt",
            "persona_proactive_voice_prompt",
            "response_review_mode",
            "passive_review_mode",
            "passive_review_strength",
            "proactive_review_mode",
            "smart_silence_judge_mode",
            "smart_silence_min_confidence",
            "smart_silence_model_timeout_seconds",
            "proactive_review_strength",
            "proactive_review_hard_risk_threshold",
            "proactive_review_low_score_threshold",
            "proactive_review_pressure_threshold",
            "response_review_max_chars",
            "enable_personal_goal_auto_progress",
            "personal_goal_share_cooldown_hours",
            "personal_goal_stall_days",
            "enable_llm_emotion_judgement",
            "emotion_judgement_mode",
            "schedule_persona_prompt",
            "schedule_worldview_prompt",
            "roleplay_user_profile_prompt",
            "roleplay_knowledge_source_ids",
            "worldview_adaptation_mode",
            "worldview_adaptation_prompt",
            "quiet_hours",
            "proactive_intensity_preset",
            "proactive_prompt_template",
            "proactive_persona_judge_send_threshold",
            "proactive_persona_judge_cache_minutes",
            "proactive_persona_judge_max_daily",
            "enable_experimental_motivation_model",
            "enable_experimental_bluetooth_wakeup",
            "enable_personality_iteration_experiment",
            "enable_personality_iteration_auto_tune",
            "enable_maslow_schedule_influence",
            "maslow_motivation_strength",
            "proactive_photo_text_probability",
            "memory_companion_context_timeout_seconds",
            "enable_memory_companion_emotional_drift",
            "enable_memory_companion_cross_window_emotion",
            "enable_memory_companion_dream_fragment",
            "enable_memory_companion_open_loop_search",
            "enable_memory_companion_feature_context",
            "memory_companion_context_top_k",
            "memory_companion_context_max_chars",
            "passive_topic_memory_hours",
            "tts_synthesis_backend",
            "tts_provider_id_zh",
            "tts_provider_id_ja",
            "tts_provider_id_en",
            "tts_mimo_tool_name",
            "tts_mimo_voice_name",
            "tts_mimo_style_prompt",
            "tts_generation_mode",
            "tts_voice_language",
            "tts_fishaudio_model",
            "tts_fishaudio_emotion_mode",
            "tts_delivery_mode",
            "tts_foreign_text_mode",
            "tts_message_scope",
            "tts_conversion_scope",
            "tts_conversion_provider_id",
            "tts_extra_prompt",
            "tts_trigger_keywords",
            "tts_frequency_control_mode",
            "tts_constraint_mode",
            "tts_session_min_interval_seconds",
            "tts_private_min_interval_seconds",
            "tts_group_min_interval_seconds",
            "tts_trigger_probability",
            "tts_private_trigger_probability",
            "tts_group_trigger_probability",
            "enable_tts_local_playback",
            "enable_tts_local_playback_live_only",
            "enable_tts_live_subtitle_sync",
            "tts_live_subtitle_url",
            "tts_local_playback_volume",
            "tts_local_playback_min_interval_seconds",
            "auto_voice_enabled",
            "auto_voice_full_conversion_enabled",
            "auto_voice_probability",
            "auto_voice_max_chars",
            "auto_voice_cooldown_seconds",
            "main_user_voice_probability",
            "main_user_mention_voice_keywords",
            "main_user_mention_voice_probability",
            "main_user_mention_voice_prompt",
            "daily_token_limit",
            "enable_daily_token_soft_limit",
            "daily_token_soft_limit",
            "humanized_state_intensity",
            "passive_injection_position",
            "enable_rest_reply_simulation",
            "rest_reply_mode",
            "rest_reply_probability",
            "rest_reply_llm_threshold",
            "rest_reply_active_windows",
            "rest_reply_awake_grace_minutes",
            "enable_rest_backlog_reply",
            "rest_backlog_max_messages",
            "REST_WAKEUP_PROVIDER_ID",
            "enable_busy_reply_gate",
            "busy_reply_min_delay_seconds",
            "busy_reply_max_delay_seconds",
            "busy_reply_proactive_resume_buffer_minutes",
            "check_interval_seconds",
            "idle_minutes",
            "min_interval_minutes",
            "proactive_unanswered_slowdown_start",
            "proactive_unanswered_max_interval_multiplier",
            "friend_unanswered_max_cooldown_hours",
            "max_daily_messages",
            "inbound_message_debounce_seconds",
            "enable_message_debounce",
            "enable_smart_message_debounce",
            "SMART_MESSAGE_DEBOUNCE_PROVIDER_ID",
            "smart_message_debounce_model_timeout_seconds",
            "smart_message_debounce_wait_seconds",
            "smart_message_debounce_learning_window_seconds",
            "smart_message_debounce_examples_limit",
            "text_message_debounce_seconds",
            "image_message_debounce_seconds",
            "forward_message_debounce_seconds",
            "text_message_debounce_max_wait_seconds",
            "message_debounce_max_merge_messages",
            "enable_semantic_message_debounce",
            "semantic_message_debounce_seconds",
            "enable_proactive_quote_trigger_message",
            "enable_quote_group_reply",
            "enable_quote_group_interjection",
            "enable_quote_private_proactive",
            "quote_skip_short_reply_chars",
            "quote_target_strategy",
            "photo_action_max_daily",
            "enable_generated_photo_cleanup",
            "generated_photo_retention_days",
            "generated_photo_max_mb",
            "photo_generation_backend",
            "custom_photo_tool_name",
            "custom_photo_tool_prompt_param",
            "custom_photo_tool_kind_param",
            "custom_photo_tool_reference_param",
            "custom_photo_tool_extra_params",
            "COMFYUI_TEXT2IMG_WORKFLOW_NAME",
            "COMFYUI_SELFIE_WORKFLOW_NAME",
            "enable_photo_reference_image",
            "photo_reference_catalog",
            "photo_persona_reference_image_path",
            "photo_reference_library",
            "enable_daily_outfit_photo",
            "enable_creative_cover_generation",
            "daily_outfit_photo_prompt",
            "daily_outfit_rotation_days",
            "enable_wardrobe",
            "wardrobe_tendency",
            "enable_wardrobe_prompt",
            "wardrobe_prompt_max_items",
            "wardrobe_image_max_count",
            "wardrobe_image_prompt",
            "WARDROBE_VISION_PROVIDER_ID",
            "wardrobe_items",
            "wardrobe_outfits",
            "WARDROBE_OUTFIT_PROVIDER_ID",
            "wardrobe_photo_source",
            "enable_user_requested_photo_generation",
            "allow_generate_photo_on_reaction_turns",
            "enable_natural_language_photo_generation",
            "natural_language_photo_generation_mode",
            "command_photo_generation_max_daily",
            "natural_language_photo_generation_max_daily",
            "natural_language_photo_extra_prompt",
            "comfyui_photo_wait_seconds",
            "enable_local_photo_load_guard",
            "local_photo_cpu_busy_percent",
            "local_photo_memory_busy_percent",
            "local_photo_defer_minutes",
            "external_image_api_platform",
            "EXTERNAL_IMAGE_API_BASE_URL",
            "EXTERNAL_IMAGE_API_KEY",
            "EXTERNAL_IMAGE_API_MODEL",
            "external_image_api_size",
            "external_image_api_timeout_seconds",
            "external_image_api_custom_headers",
            "external_image_download_proxy",
            "external_image_download_use_environment_proxy",
            "external_image_api_endpoints",
            "enable_backup_external_image_api",
            "backup_external_image_api_platform",
            "BACKUP_EXTERNAL_IMAGE_API_BASE_URL",
            "BACKUP_EXTERNAL_IMAGE_API_KEY",
            "BACKUP_EXTERNAL_IMAGE_API_MODEL",
            "backup_external_image_api_size",
            "backup_external_image_api_timeout_seconds",
            "backup_external_image_api_custom_headers",
            "photo_generation_prompt_format",
            "photo_generation_style",
            "photo_generation_style_custom_prompt",
            "photo_generation_negative_prompt_mode",
            "photo_generation_negative_prompt",
            "photo_generation_text2img_negative_prompt",
            "photo_generation_selfie_negative_prompt",
            "photo_generation_edit_negative_prompt",
            "photo_generation_fixed_prompt",
            "photo_generation_text2img_fixed_prompt",
            "photo_generation_selfie_fixed_prompt",
            "photo_generation_edit_fixed_prompt",
            "photo_generation_scene_presets",
            "enable_bot_relationship_network",
            "bot_relationship_cards",
            "private_image_vision_wait_seconds",
            "private_image_provider_timeout_seconds",
            "private_image_provider_failure_cooldown_seconds",
            "private_image_vision_provider_priority",
            "private_image_vision_custom_prompt",
            "private_image_vision_max_chars",
            "enable_context_image_captioning",
            "context_image_caption_max_items",
            "context_image_caption_timeout_seconds",
            "enable_private_image_gif_enhancement",
            "private_image_gif_max_frames",
            "enable_private_image_self_recognition",
            "private_image_self_recognition_hint",
            "enable_private_image_vision_cache",
            "private_image_vision_cache_max_items",
            "enable_group_image_understanding",
            "enable_group_image_wakeup",
            "group_image_vision_wait_seconds",
            "group_image_max_images",
            "enable_segmented_proactive_reply",
            "enable_llm_controlled_segmenting",
            "llm_controlled_segmenting_prompt",
            "enable_segmented_plugin_rules",
            "segmented_proactive_scope",
            "segmented_proactive_chat_scope",
            "enable_segmented_proactive_chat_profiles",
            "segmented_proactive_private_enabled",
            "segmented_proactive_private_scope",
            "segmented_proactive_private_threshold",
            "segmented_proactive_private_min_segment_chars",
            "segmented_proactive_private_max_segments",
            "segmented_proactive_private_send_as_forward",
            "segmented_proactive_private_interval_method",
            "segmented_proactive_private_interval_min",
            "segmented_proactive_private_interval_max",
            "segmented_proactive_private_log_base",
            "segmented_proactive_group_enabled",
            "segmented_proactive_group_scope",
            "segmented_proactive_group_threshold",
            "segmented_proactive_group_min_segment_chars",
            "segmented_proactive_group_max_segments",
            "segmented_proactive_group_send_as_forward",
            "segmented_proactive_group_interval_method",
            "segmented_proactive_group_interval_min",
            "segmented_proactive_group_interval_max",
            "segmented_proactive_group_log_base",
            "segmented_proactive_threshold",
            "segmented_proactive_min_segment_chars",
            "segmented_proactive_max_segments",
            "segmented_proactive_send_as_forward",
            "segmented_proactive_voice_strategy",
            "segmented_proactive_image_strategy",
            "segmented_proactive_at_strategy",
            "segmented_proactive_face_strategy",
            "segmented_proactive_component_order",
            "segmented_proactive_other_strategy",
            "segmented_proactive_split_mode",
            "segmented_proactive_regex",
            "segmented_proactive_split_words",
            "enable_segmented_proactive_content_cleanup",
            "segmented_proactive_content_cleanup_scope",
            "segmented_proactive_content_cleanup_rule",
            "segmented_proactive_content_cleanup_words",
            "enable_segmented_proactive_content_replacement",
            "segmented_proactive_content_replacements",
            "segmented_proactive_interval_method",
            "segmented_proactive_interval_min",
            "segmented_proactive_interval_max",
            "segmented_proactive_log_base",
            "group_conversation_followup_seconds",
            "group_conversation_followup_max_turns",
            "enable_group_conversation_followup",
            "enable_group_repeat_follow",
            "group_repeat_trigger_threshold",
            "group_repeat_count_distinct_users_only",
            "group_interject_min_interval_minutes",
            "group_interject_max_daily",
            "group_repeat_follow_probability",
            "group_repeat_interrupt_probability",
            "group_repeat_interrupt_probability_step",
            "group_repeat_interrupt_text",
            "group_repeat_interrupt_image_path",
            "group_scene_recent_limit",
            "enable_group_history_injection",
            "group_scene_recent_max_chars",
            "enable_group_injection_guard",
            "enable_group_persona_denoise",
            "group_wakeup_direct_words",
            "group_wakeup_owner_direct_words",
            "group_wakeup_context_words",
            "group_wakeup_interest_keywords",
            "group_wakeup_interest_probability",
            "group_wakeup_short_text_wait_seconds",
            "group_wakeup_question_threshold",
            "group_wakeup_cold_group_threshold",
            "group_wakeup_cooldown_seconds",
            "group_wakeup_cold_group_idle_minutes",
            "group_wakeup_generated_keyword_limit",
            "group_wakeup_topic_interest_max_boost",
            "group_wakeup_debounce_pending_penalty",
            "group_wakeup_fatigue_limit",
            "group_wakeup_fatigue_decay_minutes",
            "group_wakeup_log_limit",
            "enable_group_high_intensity_mode",
            "group_high_intensity_wakeup_window_seconds",
            "group_high_intensity_wakeup_threshold",
            "group_high_intensity_cooldown_seconds",
            "group_high_intensity_merge_seconds",
            "group_high_intensity_max_merge_messages",
            "group_high_intensity_merge_scope",
            "enable_forward_message_adaptation",
            "forward_message_mode",
            "forward_message_max_messages",
            "forward_message_max_chars",
            "forward_message_parse_nested",
            "forward_message_image_vision",
            "forward_message_image_limit",
            "enable_recall_cancel_reply",
            "enable_recall_message_cache",
            "enable_recall_transcribe_command",
            "recall_message_cache_ttl_seconds",
            "recall_message_cache_max_items",
            "recall_message_image_cache_max_mb",
            "enable_forbidden_word_recall",
            "recall_forbidden_words",
            "recall_forbidden_scope",
            "recall_forbidden_word_case_sensitive",
            "screen_diary_context_max_chars",
            "max_group_recent_messages",
            "max_group_slang_terms",
            "group_slang_web_search_terms",
            "group_slang_web_search_results",
            "memory_refresh_interval_minutes",
            "episode_memory_refresh_messages",
            "episode_memory_refresh_minutes",
            "max_companion_memory_items",
            "max_learned_expression_items",
            "expression_learning_mode",
            "enable_expression_manual_review",
            "enable_expression_style_review",
            "max_dialogue_episodes",
            "user_habit_min_count",
            "user_habit_max_items",
            "emotional_gate_hurt_threshold",
            "emotional_gate_refuse_threshold",
            "emotional_gate_recovery_per_hour",
            "emotional_gate_max_hurt_minutes",
            "enable_llm_emotion_judgement",
            "emotion_judgement_mode",
            "enable_skill_growth_simulation",
            "skill_growth_rate",
            "skill_growth_custom_skills",
            "enable_skill_growth_passive_injection",
            "enable_skill_growth_schedule_influence",
            "skill_growth_schedule_influence_strength",
            "enable_bilibili_integration",
            "enable_bilibili_boredom_watch",
            "bilibili_boredom_min_interval_hours",
            "bilibili_share_probability",
            "bilibili_share_min_score",
            "enable_news_integration",
            "enable_news_boredom_read",
            "enable_news_daily_hot_read",
            "enable_ai_daily_watch",
            "ai_daily_sources",
            "ai_daily_prefer_text_version",
            "enable_external_event_self_link",
            "news_min_interval_hours",
            "news_share_probability",
            "external_event_self_link_probability",
            "external_event_self_link_cooldown_hours",
            "external_link_share_cooldown_hours",
            "news_max_items_per_source",
            "news_sources",
            "news_hot_sources",
            "news_hot_max_items",
            "enable_web_exploration",
            "enable_web_exploration_boredom_search",
            "web_exploration_min_interval_hours",
            "web_exploration_share_probability",
            "external_event_self_link_probability",
            "external_event_self_link_cooldown_hours",
            "web_exploration_max_results",
            "web_exploration_interests",
            "WEB_EXPLORATION_API_BASE_URL",
            "WEB_EXPLORATION_API_KEY",
            "WEB_EXPLORATION_API_MODEL",
            "enable_qzone_integration",
            "QZONE_COOKIE",
            "enable_qzone_life_publish",
            "qzone_life_publish_min_interval_hours",
            "qzone_life_publish_probability",
            "qzone_life_publish_max_daily",
            "qzone_life_publish_window_mode",
            "qzone_life_publish_windows",
            "qzone_life_publish_allow_insomnia_night",
            "qzone_life_publish_intra_day_gap_minutes",
            "qzone_life_publish_similarity_threshold",
            "qzone_publish_style_prompt",
            "enable_qzone_generated_image_publish",
            "qzone_generated_image_probability",
            "qzone_publish_image_style_prompt",
            "enable_qzone_comment_inbox",
            "qzone_comment_inbox_interval_minutes",
            "qzone_comment_inbox_recent_posts",
            "qzone_comment_inbox_max_replies_per_tick",
            "enable_qzone_emotional_vent_publish",
            "qzone_emotional_vent_threshold",
            "qzone_emotional_vent_cooldown_hours",
            "qzone_emotional_vent_probability",
            "enable_reading_archive_integration",
            "enable_reading_archive_boredom_read",
            "enable_reading_archive_ask_recommendation",
            "enable_reading_archive_vision",
            "enable_reading_archive_page_comments",
            "enable_reading_archive_rating",
            "reading_archive_min_interval_hours",
            "reading_archive_max_photo_count",
            "reading_archive_share_probability",
            "reading_archive_ask_probability",
            "reading_archive_preference_min_ratings",
            "reading_archive_preference_max_terms",
            "reading_archive_default_keywords",
            "reading_archive_blocked_tags",
            "enable_unanswered_screen_peek_followup",
            "unanswered_screen_peek_after_minutes",
            "unanswered_screen_peek_cooldown_minutes",
            "enable_goodnight_screen_check",
            "goodnight_screen_check_delay_minutes",
            "enable_creative_writing",
            "enable_creative_work_read_guard",
            "creative_inspiration_probability",
            "creative_share_probability",
            "creative_chars_per_session",
            "creative_max_active_projects",
            "creative_hidden_mode",
            "creative_direction_prompt",
            "enable_worldbook_member_recognition",
            "worldbook_auto_import",
            "worldbook_member_match_aliases",
            "worldbook_self_registration",
            "worldbook_self_registration_block_words",
            "worldbook_self_registration_block_reply",
            "worldbook_auto_pending_observations",
            "worldbook_member_inject_limit",
            "worldbook_config_paths",
            "cross_user_memory_owner_only",
            "enable_atrelay_tools",
            "atrelay_require_worldbook_first",
            "atrelay_member_cache_minutes",
            "atrelay_sensitive_confirm",
            "enable_atrelay_llm_rewrite",
            "atrelay_default_relay_style",
            "atrelay_multi_target_limit",
        }
        keys.update(self._schema_setting_keys(public_only=True) - PAGE_PRIVATE_CONFIG_KEYS)
        keys.update(CYCLE_SETTING_KEYS)
        keys.difference_update(PAGE_PRIVATE_CONFIG_KEYS)
        return keys

