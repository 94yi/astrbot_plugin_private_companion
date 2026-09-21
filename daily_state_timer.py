# -*- coding: utf-8 -*-
"""计时器域。

由 tools/split_mixin_domain.py 从 daily_state.py 机械抽取（45 个方法 + 0 个模块级名字 + 0 个类级赋值 / {12053, 12054, 12055, 12056, 12057, 12058, 12059, 12060, 12061, 12062, 12063, 12064, 12065, 12066, 12067, 12068, 12069, 12070, 12071, 12072, 12073, 12074, 12075, 12076, 12077, 12078, 12079, 12080, 12081, 12082, 12083, 12084, 12085, 12086, 12087, 12088, 12089, 12090, 12091, 12092, 12093, 12094, 12095, 12096, 12097, 12098, 12099, 12100, 12101, 12102, 12103, 12104, 12105, 12106, 12107, 12108, 12109, 12110, 12111, 12112, 12113, 12114, 12115, 12117, 12118, 12119, 12120, 12121, 12122, 12123, 12124, 12125, 12126, 12128, 12129, 12130, 12131, 12132, 12133, 12134, 12135, 12136, 12137, 12138, 12139, 12140, 12141, 12142, 12143, 12144, 12145, 12146, 12147, 12148, 12149, 12150, 12152, 12153, 12154, 12155, 12156, 12157, 12158, 12159, 12160, 12161, 12162, 12163, 12164, 12165, 12166, 12167, 12169, 12170, 12172, 12173, 12174, 12175, 12176, 12177, 12178, 12179, 12180, 12181, 12182, 12183, 12184, 12185, 12186, 12187, 12188, 12189, 12190, 12191, 12192, 12193, 12194, 12195, 12196, 12197, 12198, 12199, 12200, 12201, 12202, 12203, 12204, 12205, 12206, 12207, 12208, 12209, 12210, 12211, 12212, 12213, 12215, 12216, 12217, 12218, 12219, 12220, 12221, 12222, 12223, 12224, 12225, 12226, 12227, 12228, 12229, 12230, 12231, 12232, 12233, 12234, 12235, 12236, 12237, 12238, 12239, 12240, 12241, 12242, 12243, 12244, 12245, 12246, 12247, 12248, 12249, 12250, 12251, 12252, 12253, 12254, 12255, 12256, 12257, 12258, 12259, 12260, 12261, 12262, 12263, 12264, 12265, 12266, 12267, 12268, 12270, 12271, 12272, 12273, 12274, 12275, 12276, 12277, 12278, 12279, 12280, 12281, 12282, 12283, 12284, 12285, 12286, 12287, 12288, 12289, 12290, 12291, 12292, 12293, 12294, 12295, 12296, 12297, 12298, 12299, 12300, 12301, 12302, 12303, 12304, 12305, 12306, 12307, 12308, 12309, 12310, 12311, 12312, 12313, 12314, 12315, 12316, 12317, 12318, 12319, 12320, 12321, 12322, 12323, 12324, 12325, 12326, 12327, 12329, 12330, 12331, 12332, 12333, 12334, 12335, 12336, 12337, 12338, 12339, 12340, 12341, 12342, 12343, 12344, 12345, 12346, 12347, 12348, 12349, 12351, 12352, 12353, 12354, 12355, 12356, 12357, 12358, 12359, 12360, 12361, 12362, 12363, 12364, 12365, 12366, 12367, 12368, 12369, 12370, 12372, 12373, 12374, 12375, 12377, 12378, 12379, 12380, 12381, 12382, 12383, 12384, 12385, 12386, 12387, 12388, 12389, 12390, 12391, 12392, 12393, 12394, 12395, 12396, 12397, 12398, 12399, 12400, 12401, 12402, 12403, 12404, 12405, 12406, 12407, 12408, 12409, 12410, 12411, 12412, 12413, 12414, 12415, 12416, 12417, 12418, 12419, 12420, 12421, 12422, 12423, 12424, 12425, 12426, 12427, 12428, 12429, 12430, 12431, 12432, 12434, 12435, 12436, 12437, 12438, 12439, 12440, 12441, 12442, 12443, 12445, 12446, 12447, 12448, 12449, 12450, 12451, 12452, 12453, 12454, 12455, 12456, 12457, 12458, 12459, 12461, 12462, 12463, 12464, 12465, 12467, 12468, 12469, 12470, 12471, 12472, 12473, 12474, 12475, 12476, 12477, 12478, 12479, 12481, 12482, 12483, 12484, 12485, 12486, 12487, 12488, 12489, 12490, 12491, 12492, 12493, 12494, 12495, 12496, 12497, 12498, 12499, 12500, 12501, 12502, 12503, 12504, 12505, 12506, 12507, 12508, 12509, 12510, 12511, 12513, 12514, 12515, 12516, 12517, 12518, 12519, 12520, 12521, 12522, 12523, 12524, 12525, 12527, 12528, 12529, 12530, 12531, 12532, 12533, 12534, 12536, 12537, 12538, 12539, 12540, 12541, 12542, 12543, 12545, 12546, 12547, 12549, 12550, 12551, 12552, 12553, 12554, 12556, 12557, 12558, 12559, 12560, 12561, 12562, 12563, 12565, 12566, 12567, 12568, 12569, 12570, 12571, 12572, 12573, 12574, 12575, 12576, 12577, 12578, 12579, 12580, 12581, 12582, 12583, 12584, 12585, 12586, 12587, 12588, 12589, 12590, 12591, 12592, 12593, 12594, 12595, 12596, 12598, 12599, 12600, 12601, 12602, 12603, 12604, 12606, 12607, 12608, 12609, 12610, 12611, 12612, 12614, 12615, 12616, 12617, 12618, 12619, 12620, 12621, 12622, 12623, 12624, 12625, 12626, 12627, 12628, 12629, 12630, 12631, 12632, 12633, 12634, 12635, 12636, 12637, 12638, 12639, 12640, 12641, 12642, 12643, 12644, 12645, 12646, 12647, 12648, 12649, 12650, 12651, 12652, 12653, 12654, 12655, 12656, 12657, 12658, 12659, 12660, 12661, 12662, 12663, 12664, 12665, 12666, 12668, 12669, 12670, 12671, 12672, 12673, 12674, 12676, 12677, 12678, 12679, 12680, 12681, 12682, 12683, 12684, 12685, 12686, 12688, 12689, 12690, 12691, 12692, 12693, 12694, 12695, 12696, 12697, 12698, 12699, 12700, 12701, 12702, 12703, 12704, 12705, 12706, 12707, 12708, 12709, 12710, 12711, 12712, 12714, 12715, 12716, 12717, 12718, 12719, 12720, 12721, 12722, 12723, 12724, 12725, 12726, 12727, 12728, 12729, 12730, 12731, 12732, 12733, 12734, 12736, 12737, 12738, 12739, 12740, 12741, 12742, 12743, 12744, 12745, 12746, 12748, 12749, 12750, 12751, 12752, 12753, 12754, 12755, 12756, 12757, 12758, 12759, 12760, 12761, 12762, 12763, 12764, 12765, 12766, 12767, 12768, 12769, 12770, 12771, 12772, 12773, 12775, 12776, 12777, 12778, 12779, 12780, 12781, 12782, 12783, 12784, 12785, 12786, 12787, 12789, 12790, 12791, 12792, 12793, 12794, 12795, 12796, 12797, 12798, 12799, 12800, 12801, 12802, 12803, 12804, 12805, 12806, 12807, 12808, 12809, 12810, 12811, 12812, 12813, 12815, 12816, 12817, 12818, 12819, 12820, 12821, 12822, 12823, 12824, 12825, 12826, 12827, 12828, 12829, 12830, 12831, 12832, 12833, 12834, 12835, 12836, 12837, 12838, 12839, 12840, 12842, 12843, 12844, 12845, 12846, 12847, 12848, 12849, 12850, 12851, 12852, 12853, 12854, 12855, 12856, 12857, 12858, 12859, 12860, 12861, 12862, 12863, 12864, 12865, 12866, 12867, 12869, 12870, 12871, 12872, 12873, 12874, 12875, 12876, 12877, 12878, 12879, 12880, 12881, 12882, 12883, 12884, 12885, 12886, 12887, 12888, 12889, 12890, 12891, 12892, 12893, 12894, 12895, 12896, 12897, 12898, 12899, 12900, 12901, 12902, 12903, 12904, 12905, 12906, 12907, 12908, 12909, 12910, 12911, 12912, 12913, 12914, 12915, 12916, 12917, 12918, 12919, 12920, 12921, 12923, 12924, 12925, 12926, 12927, 12928, 12929, 12930, 12931, 12932, 12933, 12934, 12936, 12937, 12938, 12939, 12940, 12941, 12942, 12943, 12944, 12945, 12946, 12947, 12948, 12949, 12950, 12951, 12952, 12953, 12954, 12955, 12956, 12957, 12958, 12959, 12960, 12961, 12962, 12963, 12964, 12965, 12966, 12967, 12968, 12969, 12970, 12971, 12972, 12973, 12974, 12975, 12976, 12977, 12978, 12979, 12980, 12981, 12982, 12983, 12984, 12985, 12986, 12987, 12988, 12989, 12990, 12991, 12992, 12993, 12994, 12995, 12996, 12997, 12998, 12999, 13000, 13001, 13002, 13003, 13004, 13005, 13006, 13007, 13008, 13009, 13010, 13011, 13012, 13013, 13014, 13015, 13016, 13017, 13018, 13019, 13020, 13021, 13022, 13023, 13024, 13025, 13026, 13027, 13028, 13029, 13030, 13031, 13032, 13033, 13034, 13035, 13036, 13037, 13038, 13039, 13040, 13041, 13042, 13043, 13044, 13045, 13046, 13047, 13048, 13049, 13050, 13051, 13052, 13054, 13055, 13056, 13057, 13058, 13059, 13060, 13061, 13062, 13063, 13064, 13065, 13066, 13067, 13068, 13069, 13070, 13071, 13072, 13073, 13074, 13075, 13076, 13077, 13078, 13079, 13080, 13081, 13082, 13083, 13084, 13085, 13086, 13087, 13088, 13089, 13090, 13091, 13092, 13093, 13094, 13095, 13096, 13097, 13098, 13099, 13100, 13101, 13102, 13103, 13105, 13106, 13107, 13108, 13109, 13110, 13111, 13112, 13113, 13114, 13115, 13116, 13117, 13118, 13120, 13121, 13122, 13123, 13124, 13125, 13126, 13127, 13128, 13129, 13130, 13131, 13132, 13133, 13134, 13135, 13136, 13137, 13138, 13139, 13140, 13141, 13142, 13143, 13144, 13145, 13146, 13147, 13148, 13149, 13150, 13152, 13153, 13154, 13155, 13156, 13157, 13158, 13159, 13160, 13161, 13162, 13163, 13164, 13165, 13166, 13167, 13168, 13169, 13170, 13171, 13172, 13173, 13174, 13175, 13176, 13177, 13178, 13179, 13180, 13182, 13183, 13184, 13185, 13186, 13187, 13188, 13189, 13190, 13191, 13192, 13193, 13194, 13195, 13196, 13197, 13198, 13199, 13200, 13201, 13202, 13203, 13204, 13205, 13206, 13207, 13208, 13209, 13210, 13211, 13212, 13213, 13214, 13215, 13216, 13217, 13218, 13219, 13220, 13221, 13222, 13223, 13224, 13225, 13226, 13227, 13228, 13229, 13230, 13231, 13232, 13233, 13234, 13235, 13236, 13237, 13238, 13239, 13240, 13241, 13242, 13243, 13244, 13245, 13246, 13247, 13248, 13249, 13250, 13251, 13252, 13253, 13254, 13255, 13256, 13257, 13258, 13259, 13260, 13261, 13262, 13263, 13264, 13265, 13266, 13267, 13268, 13269, 13270, 13271, 13272, 13273, 13274, 13275, 13276, 13277, 13278, 13279, 13280, 13281, 13282, 13283, 13284, 13285, 13286, 13287, 13288, 13289, 13290, 13291, 13292, 13293, 13294, 13295, 13296, 13297, 13298, 13299, 13300, 13301, 13302, 13303, 13304, 13305, 13306, 13307, 13308, 13309, 13310, 13311, 13312, 13313, 13314, 13315, 13316, 13317, 13318, 13319, 13320, 13321, 13322, 13323, 13324, 13325, 13326, 13327, 13328, 13329, 13330, 13331, 13332, 13333, 13334, 13335, 13336, 13337, 13338, 13339, 13340, 13341, 13342, 13343, 13344, 13345, 13346, 13347, 13348, 13349, 13350, 13351, 13352, 13353, 13354, 13355, 13356, 13357, 13358, 13359, 13360, 13361, 13362, 13363, 13364, 13365, 13366, 13367, 13368, 13369, 13370, 13371, 13372, 13373, 13374, 13375, 13376, 13377, 13378, 13379, 13380, 13381, 13382, 13383, 13384, 13385, 13386, 13387, 13388, 13389, 13390, 13391, 13392, 13393, 13394, 13395, 13396, 13397, 13398, 13399, 13400, 13401, 13402, 13403, 13404, 13405, 13406, 13407, 13408, 13409, 13410, 13411, 13412, 13413, 13414, 13415, 13416, 13417, 13418, 13419, 13420, 13421, 13422, 13423, 13424, 13425, 13426, 13427, 13428, 13429, 13430, 13431, 13432, 13433, 13434, 13435, 13436, 13437, 13438, 13439, 13440, 13441, 13442, 13443, 13444, 13445, 13446, 13447, 13448, 13449, 13450, 13451, 13452, 13453, 13454, 13455, 13456, 13457} 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 DailyStateMixin）。
"""
from __future__ import annotations

import asyncio
import json
import re
import uuid
import zoneinfo
from .constants import SUPPORTED_TIMER_FORMATS, TIMER_TAG_PATTERN
from .conversation_prompt_section import PromptSection, prompt_section
from .helpers import _now_ts, _safe_float, _safe_int, _single_line, normalize_legacy_tag_text
from .persona_config import runtime_persona_setting
from astrbot.api.event import AstrMessageEvent
from copy import deepcopy
from datetime import datetime
from typing import Any

from .logging_util import get_module_logger

logger = get_module_logger(__name__)





# ---- 宿主 patch 兼容层（由 tools/inject_host_patch_shim.py 注入）----
# PyTest 里 patch("...daily_state._today_key") 期望改动能被本模块感知。
# 原 import 会被下面的同名函数覆盖，方法体调用时实时转发到宿主模块。
def _now_ts(*args, **kwargs):
    from . import daily_state as _host
    return getattr(_host, "_now_ts")(*args, **kwargs)

class DailyStateTimerMixin:
    """计时器域（从 DailyStateMixin 拆出）。"""


    def _format_timer_scheduling_prompt_section(
        self,
        user: dict[str, Any] | None = None,
    ) -> PromptSection:
        def build_section(content: str = "") -> PromptSection:
            return prompt_section(
                key="timer.scheduling",
                title="临时预约与动作回访",
                source="daily_state",
                content=content,
            )

        if not self.enable_llm_timer_scheduling:
            return build_section()
        current_user = user if isinstance(user, dict) else {}
        role = self._private_user_role(current_user) if isinstance(user, dict) else "owner"
        followup_policy = self._activity_followup_quota_policy(current_user)
        tier = _safe_int(followup_policy.get("tier"), 3, 0, 5)
        tier_label = _single_line(followup_policy.get("tier_label"), 30) or f"L{tier}"
        max_intensity = _safe_int(followup_policy.get("max_intensity"), 1, 1, 3)
        completion_buffer = _safe_int(followup_policy.get("completion_buffer_minutes"), 0, 0, 30)
        role_note = (
            "当前是主要用户；强度 3 仍须人格资料明确支持监督、黏人或查岗倾向。"
            if role == "owner"
            else "当前是次要用户；动作回访强度必须为 1，只做普通朋友式轻问候。"
        )
        timing_note = (
            f"预约时间至少应落在预计完成后约 {completion_buffer} 分钟，给用户留出自然收尾空间。"
            if completion_buffer > 0
            else "预约时间可落在预计完成附近，但不能早于预计完成时间。"
        )
        current_time = self._environment_fromtimestamp(_now_ts()).strftime("%Y-%m-%d %H:%M:%S")
        reality_consented = getattr(self, "_reality_touch_audio_consented", lambda _: False)(current_user)
        enabled_getter = getattr(self, "_reality_companion_enabled", None)
        reality_ready = bool(callable(enabled_getter) and enabled_getter() and reality_consented)
        reality_touch_rule = (
            "用户已经具备现实触及音频授权。只有用户明确要求‘用现实触及/本机音响/电脑扬声器提醒’时，"
            "不要调用 `future_task`，必须只输出：\n"
            '<timer>{"time":"YYYY-MM-DD HH:MM:SS","delivery":"reality_touch","reason":"custom_reminder","topic":"要提醒的具体事项"}</timer>\n'
            "这种标签仍会注册为 AstrBot 官方一次性 Cron，由官方任务到点调用现实触及；普通提醒不得擅自改成现实触及。"
            if reality_ready
            else "当前用户没有可用的现实触及音频授权。即使用户提到音响，也不得承诺本机播放；普通提醒仍使用官方 `future_task`。"
        )
        body = f"""
当前本地时间：{current_time}。所有 time 都必须据此换算为未来的绝对时间。
一、明确约定：用户明确要求稍后提醒/叫醒/回头说，或双方形成明确临时约定时，若本轮提供 AstrBot 官方 `future_task` 工具，优先调用该工具；只有没有官方工具可用时，才在回复末尾写：
<timer>{{"time":"YYYY-MM-DD HH:MM:SS","topic":"约定内容"}}</timer>

同一约定只能选择 `future_task` 或 `<timer>` 其中一种，绝对不能同时创建。用户明确说“便签/便笺/备忘/待办/帮我记一下/记下来”时，应使用 `pc_manage_memo`；带提醒时间的便签由便签自身提醒，不得再调用 `future_task` 或输出 `<timer>`。

现实触及交付：{reality_touch_rule}

二、动作回访：用户明确说自己暂时离开去做一个有自然结束点的具体动作（如洗澡、吃饭、拿快递、短时出门办事），即使没有主动要求提醒，也可以形成一个“忙完后想问一句”的主动念头。生成念头的同一轮必须估计合理耗时并直接预约下一次主动消息：
<timer>{{"time":"YYYY-MM-DD HH:MM:SS","reason":"activity_followup","activity":"洗澡","estimated_minutes":30,"topic":"洗完澡后问问回来了没有","motive":"记得用户刚去洗澡，估计差不多结束后想自然问一句","followup_intensity":1,"style":"轻松自然"}}</timer>

估时应结合动作和用户给出的线索：洗澡通常 20-40 分钟，吃饭通常 30-60 分钟，短途办事通常 45-120 分钟；用户给了时长或返回时间时以用户信息为准。睡觉、上班、上学、长时间学习、旅行等没有可靠结束点的动作，不得擅自估时回访，除非用户给了明确时长或要求联系。用户只说“我在忙/没空/晚点聊”是在表达边界，不是可估时动作：不要创建回访，安静等待用户回来。
当前主动配额为 L{tier}（{tier_label}）：{followup_policy.get("generation_rule")} 最大回访强度为 {max_intensity}/3；{timing_note}
强度 1 是轻轻问一句；2 可以更直接、更有存在感；3 仅限主要用户且当前人格和关系明确支持的轻度监督感。无论强度都只发一次，不得命令、指责、施压、连续追发或假装看见用户现实状态。{role_note}

改时间直接写新时间；取消普通约定时写：<timer>{{"action":"cancel"}}</timer>；取消现实触及提醒时必须保留交付类型，写：<timer>{{"action":"cancel","delivery":"reality_touch","topic":"要取消的提醒事项"}}</timer>。
        除上述动作回访外，时间和约定不明确就不要写。标签不应出现在可见回复中，只会被转写为 AstrBot 官方一次性定时计划。"""
        body = body.lstrip("\n")
        return build_section(body)

    def _extract_timer_directives(self, text: str) -> tuple[str, list[dict[str, Any]]]:
        raw_text = str(text or "")
        payloads: list[dict[str, Any]] = []
        for match in TIMER_TAG_PATTERN.finditer(raw_text):
            payload = self._parse_timer_directive(match.group(1))
            if payload:
                payloads.append(payload)
        cleaned = TIMER_TAG_PATTERN.sub("", raw_text)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
        return cleaned, payloads

    def _llm_response_has_official_timer_tool(self, resp: Any) -> bool:
        names = getattr(resp, "tools_call_name", None)
        if isinstance(names, str) and names.strip() == "future_task":
            return True
        if isinstance(names, (list, tuple, set)) and any(str(name).strip() == "future_task" for name in names):
            return True
        raw_completion = getattr(resp, "raw_completion", None)
        candidates = [
            getattr(raw_completion, "model_extra", None),
            getattr(raw_completion, "additional_kwargs", None),
            getattr(resp, "metadata", None),
            getattr(resp, "extra_content", None),
        ]
        for candidate in candidates:
            if not candidate:
                continue
            try:
                text = json.dumps(candidate, ensure_ascii=False)
            except Exception:
                text = str(candidate)
            if "future_task" in text:
                return True
        return False

    def _text_mentions_official_timer_created(self, text: str) -> bool:
        cleaned = _single_line(text, 500)
        if not cleaned:
            return False
        lower = cleaned.lower()
        has_explicit_job_id = bool(
            re.search(r"(?:job[_\s-]?id|任务\s*id|future task|cron job)\s*[:：#]?\s*[A-Za-z0-9_-]{6,}", cleaned, re.I)
        )
        if not has_explicit_job_id:
            return False
        if "future_task" in lower or "future task" in lower or "cron job" in lower or "cronjob" in lower:
            if any(token in lower for token in ("scheduled", "created", "job_id", "task")):
                return True
        official_markers = ("官方定时", "定时计划", "定时任务", "预约任务", "任务ID", "任务 id")
        success_markers = ("已创建", "已添加", "已登记", "已安排", "创建成功", "登记成功", "安排好了")
        return any(marker in cleaned for marker in official_markers) and any(marker in cleaned for marker in success_markers)

    def _should_skip_timer_capture_for_official_task(self, resp: Any, text: str) -> bool:
        return self._llm_response_has_official_timer_tool(resp) or self._text_mentions_official_timer_created(text)

    @staticmethod
    def _record_future_task_result(
        event: AstrMessageEvent,
        tool: Any,
        tool_args: Any,
        tool_result: Any,
    ) -> bool:
        if _single_line(getattr(tool, "name", ""), 80) != "future_task":
            return False
        action = _single_line((tool_args or {}).get("action") if isinstance(tool_args, dict) else "", 20).lower()
        success_prefixes = {
            "create": "Scheduled future task ",
            "edit": "Updated future task ",
            "delete": "Deleted cron job ",
        }
        expected_prefix = success_prefixes.get(action)
        if not expected_prefix and action != "list":
            return False
        try:
            setattr(event, "private_companion_future_task_result_observed", True)
            setattr(event, "private_companion_future_task_action", action)
        except Exception:
            return False
        if action == "list":
            return False
        if tool_result is None or bool(getattr(tool_result, "isError", False)):
            return False
        content = getattr(tool_result, "content", None)
        if not isinstance(content, list):
            return False
        result_text = "\n".join(
            str(getattr(item, "text", "") or "")
            for item in content
            if getattr(item, "text", None) is not None
        ).strip()
        if not result_text.startswith(expected_prefix):
            return False
        try:
            setattr(event, "private_companion_future_task_succeeded", True)
        except Exception:
            return False
        return True

    async def _schedule_llm_timer_after_response_dedup(
        self,
        event: AstrMessageEvent,
        resp: Any,
        user_id: str,
        payload: dict[str, Any],
        *,
        source_text: str,
        visible_text: str,
        trigger_message_id: str = "",
        trigger_umo: str = "",
    ) -> str:
        if bool(getattr(event, "private_companion_memo_reminder_saved", False)):
            logger.info(
                "跳过对话临时预约转写: 本轮已保存带提醒的便签 session=%s",
                _single_line(trigger_umo, 120) or "unknown",
            )
            return "memo_reminder"
        if bool(getattr(event, "private_companion_future_task_succeeded", False)):
            logger.info(
                "跳过对话临时预约转写: 本轮 AstrBot future_task 已执行成功 session=%s",
                _single_line(trigger_umo, 120) or "unknown",
            )
            return "official_task"
        future_task_result_observed = bool(
            getattr(event, "private_companion_future_task_result_observed", False)
        )
        if not future_task_result_observed and self._should_skip_timer_capture_for_official_task(resp, visible_text):
            logger.info(
                "跳过对话临时预约转写: 本轮疑似已由 AstrBot 官方定时计划处理 session=%s",
                _single_line(trigger_umo, 120) or "unknown",
            )
            return "official_task"
        if _single_line(payload.get("delivery"), 32).lower() == "reality_touch":
            scheduler = getattr(self, "_schedule_reality_touch_official_reminder", None)
            if not callable(scheduler):
                logger.warning("当前实例不支持现实触及官方提醒")
                return "reality_touch_unavailable"
            scheduled = await scheduler(
                user_id,
                payload,
                source_text=source_text,
                trigger_umo=trigger_umo,
            )
            return "reality_touch_official" if scheduled else "reality_touch_unavailable"
        await self._schedule_llm_timer(
            user_id,
            payload,
            source_text=source_text,
            source_origin="llm_response",
            trigger_message_id=trigger_message_id,
            trigger_umo=trigger_umo,
        )
        return "scheduled"

    def _parse_timer_directive(self, raw: str) -> dict[str, Any] | None:
        content = str(raw or "").strip()
        if not content:
            return None
        payload: dict[str, Any]
        if content.startswith("{") and content.endswith("}"):
            try:
                loaded = json.loads(content)
            except Exception:
                return None
            if not isinstance(loaded, dict):
                return None
            payload = {str(key): value for key, value in loaded.items()}
        else:
            payload = {"time": content}

        action_text = str(payload.get("action") or payload.get("operation") or "").strip().lower()
        cancel_requested = bool(payload.get("cancel")) or action_text in {"cancel", "delete", "remove", "取消", "删除", "撤销"}
        if cancel_requested:
            return {
                "cancel": True,
                "action": "cancel",
                "topic": _single_line(payload.get("topic") or payload.get("reason"), 60),
                "delivery": _single_line(payload.get("delivery"), 32).lower(),
                "reminder_id": _single_line(payload.get("reminder_id") or payload.get("id"), 40),
            }

        time_text = ""
        for key in ("time", "timer", "at", "datetime", "date"):
            candidate = payload.get(key)
            if candidate:
                time_text = str(candidate).strip()
                break
        if not time_text:
            return None
        scheduled_ts = self._parse_timer_timestamp(time_text)
        if scheduled_ts <= 0:
            return None
        parsed: dict[str, Any] = {"scheduled_ts": scheduled_ts, "raw_time": time_text}
        for key in ("reason", "topic", "motive", "action", "style", "activity"):
            value = payload.get(key)
            if value is not None:
                parsed[key] = _single_line(value, 140 if key == "motive" else 60)
        delivery = _single_line(payload.get("delivery"), 32).lower()
        if delivery == "reality_touch":
            parsed["delivery"] = delivery
            parsed["delivery_mode"] = _single_line(payload.get("delivery_mode"), 32).lower()
            parsed["playback_volume"] = _safe_int(payload.get("playback_volume"), -1, -1, 100)
            parsed["fade_in_ms"] = _safe_int(payload.get("fade_in_ms"), -1, -1, 5000)
        if _single_line(parsed.get("reason"), 40) == "activity_followup":
            parsed["estimated_minutes"] = _safe_int(payload.get("estimated_minutes"), 0, 0, 720)
            parsed["followup_intensity"] = self._normalize_activity_followup_intensity(
                payload.get("followup_intensity")
            )
        chain = self._normalize_chain_steps(payload.get("chain"))
        if chain:
            parsed["chain"] = chain
        return parsed

    def _normalize_chain_steps(self, raw_chain: Any) -> list[dict[str, Any]]:
        if not isinstance(raw_chain, list):
            return []
        normalized_chain: list[dict[str, Any]] = []
        for step in raw_chain[:4]:
            if not isinstance(step, dict):
                continue
            kind = _single_line(step.get("kind"), 32)
            if not kind:
                continue
            normalized_chain.append(
                {
                    "kind": kind,
                    "after_minutes": _safe_int(step.get("after_minutes"), 0, 0, 240),
                    "reason": _single_line(step.get("reason"), 40),
                    "topic": _single_line(step.get("topic"), 80),
                    "motive": _single_line(step.get("motive"), 100),
                    "tone": _single_line(step.get("tone"), 30),
                }
            )
        return normalized_chain

    @staticmethod
    def _normalize_activity_followup_intensity(value: Any) -> int:
        text = str(value or "").strip().lower()
        aliases = {
            "soft": 1,
            "gentle": 1,
            "轻": 1,
            "轻柔": 1,
            "normal": 2,
            "direct": 2,
            "标准": 2,
            "直接": 2,
            "firm": 3,
            "strong": 3,
            "强": 3,
            "强势": 3,
        }
        if text in aliases:
            return aliases[text]
        return _safe_int(value, 1, 1, 3)

    def _activity_followup_intensity_for_user(self, value: Any, user: dict[str, Any]) -> int:
        intensity = self._normalize_activity_followup_intensity(value)
        policy = self._activity_followup_quota_policy(user)
        return min(intensity, _safe_int(policy.get("max_intensity"), 1, 1, 3))

    def _activity_followup_quota_policy(self, user: dict[str, Any] | None) -> dict[str, Any]:
        current_user = user if isinstance(user, dict) else {}
        quota_policy: dict[str, Any] = {}
        policy_getter = getattr(self, "_proactive_quota_policy", None)
        if callable(policy_getter):
            try:
                result = policy_getter(current_user)
                if isinstance(result, dict):
                    quota_policy = result
            except Exception:
                quota_policy = {}

        tier = _safe_int(quota_policy.get("tier"), 3, 0, 5)
        tier_label = _single_line(quota_policy.get("label"), 30) or {
            0: "已关闭",
            1: "克制",
            2: "轻陪伴",
            3: "稳定陪伴",
            4: "亲密陪伴",
            5: "持续在线",
        }.get(tier, "稳定陪伴")
        tier_rules = {
            0: (1, 15, "主动消息已关闭，不应自行创建动作回访。"),
            1: (1, 15, "只在动作非常具体、短时且有明确自然终点时才创建；宁可不追问。"),
            2: (1, 8, "仅对明确的短时动作创建轻量回访，不把普通离开都变成追问。"),
            3: (2, 3, "可对明确短时动作自然回访，语气应随关系而变化。"),
            4: (2, 0, "可更积极承接明确短时动作，但仍只形成一次自然回访。"),
            5: (3, 0, "明确短时动作可优先承接为回访，但不能把每次离开都解释成需要查岗。"),
        }
        max_intensity, buffer_minutes, generation_rule = tier_rules[tier]
        role = self._private_user_role(current_user)
        ignored = _safe_int(current_user.get("ignored_streak"), 0, 0)
        if role != "owner" or ignored > 0:
            max_intensity = 1
        if ignored > 0:
            buffer_minutes = max(buffer_minutes, 10)

        if max_intensity >= 3:
            persona_text = " ".join(
                (
                    str(runtime_persona_setting(self, "schedule_persona_prompt", "") or ""),
                    str(runtime_persona_setting(self, "persona_proactive_voice_prompt", "") or ""),
                    str(current_user.get("style") or ""),
                )
            )
            strong_markers = ("查岗", "监督", "管着", "管束", "强势", "严格", "占有", "黏人", "粘人")
            if not any(marker in persona_text for marker in strong_markers):
                max_intensity = 2

        return {
            "tier": tier,
            "tier_label": tier_label,
            "max_intensity": max_intensity,
            "completion_buffer_minutes": buffer_minutes,
            "generation_rule": generation_rule,
        }

    def _parse_timer_timestamp(self, time_text: str) -> float:
        normalized = str(time_text or "").strip()
        if not normalized:
            return 0.0
        for fmt in SUPPORTED_TIMER_FORMATS:
            try:
                return datetime.strptime(normalized, fmt).timestamp()
            except ValueError:
                continue
        return 0.0

    def _infer_timer_reason(self, scheduled_ts: float, source_text: str = "") -> str:
        dt = self._environment_fromtimestamp(scheduled_ts)
        minute = dt.hour * 60 + dt.minute
        lowered = str(source_text or "")
        if 8 * 60 <= minute <= 10 * 60 + 30:
            return "morning_greeting"
        if 12 * 60 <= minute <= 13 * 60 + 50:
            return "noon_greeting"
        if 21 * 60 <= minute <= 23 * 60 + 10:
            return "evening_greeting"
        if any(token in lowered for token in ("照片", "风景", "云", "雨", "光", "晚霞", "猫")):
            return "activity_share"
        if any(token in lowered for token in ("记下来", "那句话", "写下", "日记")):
            return "diary_share"
        return "check_in"

    def _timer_default_topic(self, reason: str, user: dict[str, Any], source_text: str = "") -> str:
        source = _single_line(source_text, 48)
        if source:
            return source
        return self._choose_proactive_topic(reason, user)

    def _timer_default_motive(
        self,
        reason: str,
        user: dict[str, Any],
        *,
        source_text: str = "",
        topic: str = "",
    ) -> str:
        if topic:
            return self._normalize_internal_motive_text(f"关于“{topic}”还有一点后续内容,适合稍后补充")
        if source_text:
            return self._normalize_internal_motive_text("刚才的话题还有一点后续内容,适合稍后补充")
        return self._choose_proactive_motive(reason, user, action="message")

    def _timer_source_implies_user_unavailable(self, source_text: str, payload: dict[str, Any] | None = None) -> bool:
        text = f"{source_text or ''} {_single_line((payload or {}).get('topic'), 80)} {_single_line((payload or {}).get('motive'), 120)}"
        if not text.strip():
            return False
        rest_tokens = (
            "睡觉",
            "睡会",
            "睡一会",
            "午睡",
            "补觉",
            "休息",
            "躺会",
            "躺一会",
            "眯一会",
            "小憩",
            "闭眼",
            "一起睡",
            "一起休息",
        )
        wake_tokens = (
            "叫我",
            "叫醒",
            "喊我",
            "喊醒",
            "起床",
            "醒来",
            "准时",
            "到点",
            "提醒我",
        )
        return any(token in text for token in rest_tokens) and any(token in text for token in wake_tokens)

    def _get_active_llm_timer(self, user: dict[str, Any]) -> dict[str, Any] | None:
        raw = user.get("llm_timer_event")
        if not isinstance(raw, dict) or not raw:
            return None
        if _single_line(raw.get("backend"), 40) != "astrbot_cron":
            return None
        status = _single_line(raw.get("status"), 40)
        if status not in {"pending", "registering", "replacing", "scheduled"}:
            return None
        scheduled_ts = _safe_float(raw.get("scheduled_ts"), 0)
        if scheduled_ts <= 0:
            return None
        return raw

    def _due_internal_llm_timer_id(self, user: dict[str, Any], *, now: float | None = None) -> str:
        event = self._get_active_llm_timer(user)
        if not isinstance(event, dict) or not self._llm_timer_can_use_internal_scheduler(event):
            return ""
        check_now = _now_ts() if now is None else now
        if check_now < _safe_float(event.get("scheduled_ts"), 0):
            return ""
        return _single_line(event.get("id"), 40)

    def _has_due_llm_timer(self, user: dict[str, Any], now: float | None = None) -> bool:
        event = self._get_active_llm_timer(user)
        if not isinstance(event, dict):
            return False
        if not self._llm_timer_can_use_internal_scheduler(event):
            return False
        now = now or _now_ts()
        return now >= _safe_float(event.get("scheduled_ts"), 0)

    def _llm_timer_can_use_internal_scheduler(self, event: dict[str, Any] | None) -> bool:
        """LLM timer is now a compatibility layer; execution belongs to AstrBot cron."""
        return False

    def _clear_llm_timer_internal_plan_fields(self, user: dict[str, Any]) -> None:
        if not isinstance(user, dict):
            return
        if normalize_legacy_tag_text(user.get("planned_proactive_source")) != "timer":
            return
        self._clear_pending_proactive_plan(user)

    def _clear_llm_timer_event(self, user: dict[str, Any], *, event_id: str = "") -> None:
        raw = user.get("llm_timer_event")
        if not isinstance(raw, dict):
            user["llm_timer_event"] = {}
            return
        if event_id and str(raw.get("id") or "") != event_id:
            return
        user["llm_timer_event"] = {}

    def _format_llm_timer_context(self, user: dict[str, Any], *, now: float | None = None) -> str:
        event = self._get_active_llm_timer(user)
        if not isinstance(event, dict):
            return ""
        now = now or _now_ts()
        scheduled_ts = _safe_float(event.get("scheduled_ts"), 0)
        if scheduled_ts <= 0:
            return ""
        summary_parts = ["这是你之前自己留给自己的一个回头时间。"]
        topic = _single_line(event.get("topic"), 36)
        motive = _single_line(event.get("motive"), 60)
        seed = _single_line(event.get("seed_text"), 60)
        if topic:
            summary_parts.append(f"话题线索是“{topic}”。")
        elif seed:
            summary_parts.append(f"当时留下来的那句线索是：{seed}")
        if motive:
            summary_parts.append(f"当时心里的余味：{motive}")
        deferred = event.get("deferred_context")
        if isinstance(deferred, dict) and deferred:
            deferred_topic = _single_line(deferred.get("topic"), 40)
            deferred_motive = _single_line(deferred.get("motive"), 80)
            deferred_reason = _single_line(deferred.get("reason"), 30)
            deferred_text = deferred_topic or deferred_motive or deferred_reason
            if deferred_text:
                summary_parts.append(
                    f"这段静默期间原本还有一个顺带话头被留到了现在：{deferred_text}。"
                    "本次回复必须先完成预约/叫醒本意，再把这个话头当成一句顺带内容自然接上；不要单独展开成长篇。"
                )
        if now < scheduled_ts:
            summary_parts.append(f"现在离约好的时间还差 {self._format_duration_brief(scheduled_ts - now)}。")
        return " ".join(summary_parts)

    def _llm_timer_timezone_name(self) -> str:
        timezone_name = _single_line(getattr(self, "environment_perception_timezone", ""), 64) or "Asia/Shanghai"
        try:
            zoneinfo.ZoneInfo(timezone_name)
            return timezone_name
        except Exception:
            return "Asia/Shanghai"

    def _llm_timer_run_at(self, scheduled_ts: float) -> datetime:
        timezone_name = self._llm_timer_timezone_name()
        try:
            tzinfo = zoneinfo.ZoneInfo(timezone_name)
        except Exception:
            tzinfo = zoneinfo.ZoneInfo("Asia/Shanghai")
        return datetime.fromtimestamp(scheduled_ts, tzinfo)

    def _format_official_timer_note(
        self,
        *,
        scheduled_ts: float,
        reason: str,
        action: str,
        topic: str,
        motive: str,
        source_text: str,
        style: str = "",
        activity: str = "",
        estimated_minutes: int = 0,
        followup_intensity: int = 1,
    ) -> str:
        when = self._environment_fromtimestamp(scheduled_ts).strftime("%Y-%m-%d %H:%M")
        lines = [
            "这是 PrivateCompanion 从聊天中确认出的临时约定。到点后请按约定自然联系用户,不要解释这是定时任务。",
            f"约定时间：{when}",
        ]
        if topic:
            lines.append(f"约定内容：{topic}")
        if motive:
            lines.append(f"补充语境：{motive}")
        if reason:
            lines.append(f"类型：{reason}")
        if reason == "activity_followup":
            lines[0] = "这是 PrivateCompanion 根据用户暂时离开的动作生成的一次动作查岗主动消息。到点后自然联系用户,不要解释定时任务或内部判断。"
            if activity:
                lines.append(f"用户动作：{activity}")
            if estimated_minutes > 0:
                lines.append(f"生成念头时的预计耗时：{estimated_minutes} 分钟")
            lines.append(f"查岗强度：{followup_intensity}/3")
            intensity_rules = {
                1: "轻轻问一句动作是否结束或人是否回来了，不要求立即回复。",
                2: "可以更直接、更有存在感地问一句，但保持亲近和可拒绝。",
                3: "可带符合人格的轻度监督感或小小不满，但不得命令、指责、威胁或连续追发。",
            }
            lines.append(f"表达要求：{intensity_rules.get(followup_intensity, intensity_rules[1])}")
            proactive_voice = ""
            formatter = getattr(self, "_format_proactive_voice_prompt", None)
            if callable(formatter):
                proactive_voice = _single_line(formatter(), 500)
            if proactive_voice:
                lines.append(f"人格化主动风格：{proactive_voice}")
        if style:
            lines.append(f"语气参考：{style}")
        if action and action != "message":
            lines.append(f"期望动作：{action}")
        seed = _single_line(source_text, 180)
        if seed:
            lines.append(f"聊天线索：{seed}")
        lines.append("执行方式：使用 send_message_to_user 给原会话发一条简短自然的消息；如果是叫醒/提醒,直接完成提醒。只发送一次，不因用户未回复而自行追加。")
        return "\n".join(lines)

    def _official_cron_manager(self) -> Any | None:
        context = getattr(self, "context", None)
        manager = getattr(context, "cron_manager", None)
        if manager is not None:
            return manager
        nested = getattr(context, "context", None)
        return getattr(nested, "cron_manager", None)

    def _llm_timer_operation_lock(self, user_id: str) -> asyncio.Lock:
        locks = getattr(self, "_llm_timer_operation_locks", None)
        if not isinstance(locks, dict):
            locks = {}
            setattr(self, "_llm_timer_operation_locks", locks)
        key = _single_line(user_id, 120) or "_unknown"
        lock = locks.get(key)
        if not isinstance(lock, asyncio.Lock):
            lock = asyncio.Lock()
            locks[key] = lock
        return lock

    async def _official_llm_timer_job_runtime(self, job_id: str) -> tuple[bool, str]:
        """Return whether runtime lookup is supported and the current official status."""
        normalized_job_id = _single_line(job_id, 80)
        if not normalized_job_id:
            return True, "missing"
        cron_mgr = self._official_cron_manager()
        if cron_mgr is None:
            return False, ""
        getter = getattr(cron_mgr, "get_job", None)
        if not callable(getter):
            getter = getattr(getattr(cron_mgr, "db", None), "get_cron_job", None)
        if not callable(getter):
            return False, ""
        try:
            job = await getter(normalized_job_id)
        except Exception as exc:
            logger.debug(
                "查询官方定时任务状态失败: job=%s error=%s",
                normalized_job_id,
                _single_line(exc, 160),
            )
            return False, ""
        if job is None:
            return True, "missing"
        return True, _single_line(getattr(job, "status", ""), 40).lower() or "scheduled"

    @staticmethod
    def _official_llm_timer_event_metadata(event: Any) -> dict[str, str]:
        getter = getattr(event, "get_extra", None)
        if not callable(getter):
            return {}
        try:
            payload = getter("cron_payload", {})
            cron_job = getter("cron_job", {})
        except Exception:
            return {}
        if not isinstance(payload, dict) or payload.get("origin") != "private_companion_timer":
            return {}
        private_payload = payload.get("private_companion")
        if not isinstance(private_payload, dict):
            return {}
        timer_id = _single_line(private_payload.get("timer_id"), 40)
        user_id = _single_line(payload.get("sender_id"), 120)
        job_id = _single_line(cron_job.get("id"), 80) if isinstance(cron_job, dict) else ""
        if not timer_id or not user_id:
            return {}
        return {"timer_id": timer_id, "user_id": user_id, "job_id": job_id}

    @staticmethod
    def _official_llm_timer_matches(current: Any, metadata: dict[str, str]) -> bool:
        if not isinstance(current, dict) or not metadata:
            return False
        if _single_line(current.get("backend"), 40) != "astrbot_cron":
            return False
        if _single_line(current.get("id"), 40) != metadata.get("timer_id"):
            return False
        current_job_id = _single_line(current.get("job_id") or current.get("candidate_job_id"), 80)
        event_job_id = metadata.get("job_id", "")
        return not (current_job_id and event_job_id and current_job_id != event_job_id)

    async def _acknowledge_official_llm_timer_trigger(self, event: Any) -> bool:
        metadata = self._official_llm_timer_event_metadata(event)
        if not metadata:
            return False
        user_id = metadata["user_id"]
        async with self._llm_timer_operation_lock(user_id):
            async with self._data_lock:
                users = self.data.get("users")
                current_user = users.get(user_id) if isinstance(users, dict) else None
                current = current_user.get("llm_timer_event") if isinstance(current_user, dict) else None
                if not self._official_llm_timer_matches(current, metadata):
                    return False
                current["status"] = "triggered"
                current["triggered_at"] = _now_ts()
                if metadata.get("job_id"):
                    current["job_id"] = metadata["job_id"]
                    current["cron_job_id"] = metadata["job_id"]
                self._clear_llm_timer_internal_plan_fields(current_user)
                self._save_data_sync(sections={"users"})
        logger.info(
            "官方临时预约开始执行: user=%s timer=%s job=%s",
            user_id,
            metadata["timer_id"],
            metadata.get("job_id") or "-",
        )
        return True

    @staticmethod
    def _official_llm_timer_tool_result_succeeded(tool_result: Any) -> bool:
        if tool_result is None or bool(getattr(tool_result, "isError", False)):
            return False
        content = getattr(tool_result, "content", None)
        if not isinstance(content, list):
            return False
        result_text = "\n".join(
            str(getattr(item, "text", "") or "")
            for item in content
            if getattr(item, "text", None) is not None
        ).strip()
        return result_text.startswith("Message sent to session ")

    async def _record_official_llm_timer_tool_result(
        self,
        event: Any,
        tool: Any,
        tool_result: Any,
    ) -> bool:
        if _single_line(getattr(tool, "name", ""), 80) != "send_message_to_user":
            return False
        metadata = self._official_llm_timer_event_metadata(event)
        if not metadata:
            return False
        succeeded = self._official_llm_timer_tool_result_succeeded(tool_result)
        user_id = metadata["user_id"]
        async with self._llm_timer_operation_lock(user_id):
            async with self._data_lock:
                users = self.data.get("users")
                current_user = users.get(user_id) if isinstance(users, dict) else None
                current = current_user.get("llm_timer_event") if isinstance(current_user, dict) else None
                if not self._official_llm_timer_matches(current, metadata):
                    return False
                current["status"] = "delivered" if succeeded else "delivery_failed"
                current["delivery_at"] = _now_ts()
                current["delivery_error"] = "" if succeeded else "send_message_to_user 未确认发送成功"
                self._save_data_sync(sections={"users"})
        return True

    async def _complete_official_llm_timer_event(self, event: Any) -> bool:
        metadata = self._official_llm_timer_event_metadata(event)
        if not metadata:
            return False
        user_id = metadata["user_id"]
        async with self._llm_timer_operation_lock(user_id):
            async with self._data_lock:
                users = self.data.get("users")
                current_user = users.get(user_id) if isinstance(users, dict) else None
                current = current_user.get("llm_timer_event") if isinstance(current_user, dict) else None
                if not self._official_llm_timer_matches(current, metadata):
                    return False
                status = _single_line(current.get("status"), 40)
                if status == "delivered":
                    current["status"] = "completed"
                    current["delivery_status"] = "sent"
                elif status == "triggered":
                    current["status"] = "completed_without_delivery"
                    current["delivery_status"] = "not_confirmed"
                elif status == "delivery_failed":
                    current["delivery_status"] = "failed"
                else:
                    return False
                current["completed_at"] = _now_ts()
                self._save_data_sync(sections={"users"})
        return True

    def _expire_stale_official_llm_timers_locked(self, *, now: float | None = None) -> int:
        check_now = _now_ts() if now is None else now
        users = self.data.get("users")
        if not isinstance(users, dict):
            return 0
        changed = 0
        for user in users.values():
            if not isinstance(user, dict):
                continue
            timer = user.get("llm_timer_event")
            if not isinstance(timer, dict) or _single_line(timer.get("backend"), 40) != "astrbot_cron":
                continue
            status = _single_line(timer.get("status"), 40)
            scheduled_ts = _safe_float(timer.get("scheduled_ts"), 0)
            triggered_at = _safe_float(timer.get("triggered_at"), 0)
            if status in {"pending", "registering", "replacing", "scheduled"} and scheduled_ts > 0 and check_now - scheduled_ts > 30 * 60:
                timer["status"] = "expired_unconfirmed"
                timer["expired_at"] = check_now
                timer["error"] = "官方任务已过期，但插件未收到执行回执"
                changed += 1
            elif status == "triggered" and triggered_at > 0 and check_now - triggered_at > 2 * 3600:
                timer["status"] = "triggered_unconfirmed"
                timer["expired_at"] = check_now
                timer["error"] = "官方任务已开始，但插件未收到完成回执"
                changed += 1
        return changed

    async def _add_official_llm_timer_job(
        self,
        *,
        user_id: str,
        user: dict[str, Any],
        timer_event: dict[str, Any],
        note: str,
        trigger_umo: str,
    ) -> tuple[str, str]:
        cron_mgr = self._official_cron_manager()
        if cron_mgr is None:
            return "", "AstrBot 官方定时计划不可用"
        scheduled_ts = _safe_float(timer_event.get("scheduled_ts"), 0)
        if scheduled_ts <= 0:
            return "", "预约时间无效"
        run_at = self._llm_timer_run_at(scheduled_ts)
        session = _single_line(trigger_umo, 180) or _single_line(user.get("umo"), 180)
        if not session:
            return "", "缺少私聊会话"
        payload = {
            "session": session,
            "sender_id": str(user_id),
            "note": note,
            "origin": "private_companion_timer",
            "private_companion": {
                "timer_id": _single_line(timer_event.get("id"), 40),
                "reason": _single_line(timer_event.get("reason"), 40),
                "action": _single_line(timer_event.get("action"), 40),
                "topic": _single_line(timer_event.get("topic"), 80),
                "activity": _single_line(timer_event.get("activity"), 60),
                "estimated_minutes": _safe_int(timer_event.get("estimated_minutes"), 0, 0, 720),
                "followup_intensity": _safe_int(timer_event.get("followup_intensity"), 1, 1, 3),
            },
        }
        try:
            job = await cron_mgr.add_active_job(
                name=(
                    "PrivateCompanion 动作查岗"
                    if _single_line(timer_event.get("reason"), 40) == "activity_followup"
                    else "PrivateCompanion 临时约定"
                ),
                cron_expression=None,
                payload=payload,
                description=_single_line(timer_event.get("topic") or note, 180),
                timezone=self._llm_timer_timezone_name(),
                enabled=True,
                persistent=True,
                run_once=True,
                run_at=run_at,
            )
        except Exception as exc:
            return "", _single_line(exc, 180) or repr(exc)
        return _single_line(getattr(job, "job_id", ""), 80), ""

    async def _delete_official_llm_timer_job(self, job_id: str) -> tuple[bool, str]:
        normalized_job_id = _single_line(job_id, 80)
        if not normalized_job_id:
            return False, "缺少官方任务 ID"
        cron_mgr = self._official_cron_manager()
        if cron_mgr is None:
            return False, "AstrBot 官方定时计划不可用"
        try:
            await cron_mgr.delete_job(normalized_job_id)
        except Exception as exc:
            return False, _single_line(exc, 180) or repr(exc)
        return True, ""

    async def _cancel_llm_timer(
        self,
        user_id: str,
        payload: dict[str, Any],
        *,
        source_text: str,
        source_origin: str,
        trigger_message_id: str = "",
        trigger_umo: str = "",
    ) -> bool:
        normalized_user_id = _single_line(user_id, 120)
        async with self._llm_timer_operation_lock(normalized_user_id):
            now_ts = _now_ts()
            async with self._data_lock:
                user = self._get_user(normalized_user_id)
                existing_raw = user.get("llm_timer_event")
                existing = deepcopy(existing_raw) if isinstance(existing_raw, dict) else {}
                expected_event_id = _single_line(payload.get("_expected_event_id"), 40)
                existing_event_id = _single_line(existing.get("id"), 40)
                if expected_event_id and existing_event_id != expected_event_id:
                    return False
                existing_status = _single_line(existing.get("status"), 40)
                existing_job_id = _single_line(
                    existing.get("job_id") or existing.get("candidate_job_id"),
                    80,
                )
                existing_active = (
                    _single_line(existing.get("backend"), 40) == "astrbot_cron"
                    and existing_status in {"pending", "registering", "replacing", "scheduled"}
                    and bool(existing_job_id)
                )
                if not existing_active:
                    if expected_event_id:
                        return False
                    user["llm_timer_event"] = {
                        "id": uuid.uuid4().hex,
                        "scheduled_ts": _safe_float(existing.get("scheduled_ts"), 0) or now_ts,
                        "action": "cancel",
                        "topic": _single_line(payload.get("topic") or "取消临时约定", 60),
                        "motive": _single_line(source_text, 140),
                        "origin": source_origin,
                        "created_at": now_ts,
                        "backend": "astrbot_cron",
                        "status": "cancel_skipped",
                        "error": "没有可取消的对话临时预约",
                    }
                    self._save_data_sync(sections={"users"})
                    return False

            runtime_supported, runtime_status = await self._official_llm_timer_job_runtime(existing_job_id)
            if runtime_supported and runtime_status in {"running", "completed", "failed", "missing"}:
                async with self._data_lock:
                    user = self._get_user(normalized_user_id)
                    current = user.get("llm_timer_event")
                    if not isinstance(current, dict) or _single_line(current.get("id"), 40) != existing_event_id:
                        return False
                    if _single_line(current.get("job_id") or current.get("candidate_job_id"), 80) != existing_job_id:
                        return False
                    if runtime_status == "running":
                        current["status"] = "triggered"
                        current["triggered_at"] = _safe_float(current.get("triggered_at"), 0) or now_ts
                        current["cancel_status"] = "too_late"
                        current["cancel_error"] = "官方任务已经开始执行，无法确认取消"
                    else:
                        current["status"] = "expired_unconfirmed"
                        current["cancel_status"] = "not_found"
                        current["cancel_error"] = "官方任务已结束或不存在，无法确认取消"
                    current.pop("cancel_requested_at", None)
                    self._save_data_sync(sections={"users"})
                return False

            async with self._data_lock:
                user = self._get_user(normalized_user_id)
                current = user.get("llm_timer_event")
                if not isinstance(current, dict) or _single_line(current.get("id"), 40) != existing_event_id:
                    return False
                if _single_line(current.get("job_id") or current.get("candidate_job_id"), 80) != existing_job_id:
                    return False
                current["status"] = "cancel_pending"
                current["cancel_requested_at"] = now_ts
                current["cancel_origin"] = source_origin
                current["cancel_topic"] = _single_line(payload.get("topic") or "取消临时约定", 60)
                current["cancel_source_text"] = _single_line(source_text, 140)
                self._save_data_sync(sections={"users"})

            ok, error = await self._delete_official_llm_timer_job(existing_job_id)
            async with self._data_lock:
                user = self._get_user(normalized_user_id)
                current = user.get("llm_timer_event")
                if not isinstance(current, dict) or _single_line(current.get("id"), 40) != existing_event_id:
                    return False
                if _single_line(current.get("job_id") or current.get("candidate_job_id"), 80) != existing_job_id:
                    return False
                if ok:
                    current["status"] = "cancelled"
                    current["cancelled_at"] = _now_ts()
                    current["cancelled_job_id"] = existing_job_id
                    current["cancel_status"] = "cancelled"
                    current["error"] = ""
                    current.pop("cancel_requested_at", None)
                    self._clear_llm_timer_internal_plan_fields(user)
                else:
                    restored = deepcopy(existing)
                    restored["cancel_status"] = "failed"
                    restored["cancel_error"] = error or "官方任务删除失败"
                    restored["cancel_failed_at"] = _now_ts()
                    restored.pop("cancel_requested_at", None)
                    user["llm_timer_event"] = restored
                self._save_data_sync(sections={"users"})
            logger.info(
                "对话临时预约取消%s: user=%s job=%s error=%s",
                "完成" if ok else "失败",
                normalized_user_id,
                existing_job_id,
                error or "-",
            )
            return ok

    def _queue_official_llm_timer_cancel(
        self,
        user_id: str,
        timer_event: dict[str, Any],
        *,
        source_text: str,
        source_origin: str,
        trigger_umo: str = "",
    ) -> bool:
        if not isinstance(timer_event, dict) or _single_line(timer_event.get("backend"), 40) != "astrbot_cron":
            return False
        if _single_line(timer_event.get("status"), 40) not in {"pending", "registering", "replacing", "scheduled"}:
            return False
        timer_id = _single_line(timer_event.get("id"), 40)
        normalized_user_id = _single_line(user_id or timer_event.get("user_id"), 120)
        if not timer_id or not normalized_user_id or _safe_float(timer_event.get("cancel_requested_at"), 0) > 0:
            return False
        timer_event["cancel_requested_at"] = _now_ts()
        operation = self._cancel_llm_timer(
            normalized_user_id,
            {
                "cancel": True,
                "topic": "用户已在问候时段自然出现，取消冲突问候",
                "_expected_event_id": timer_id,
            },
            source_text=source_text,
            source_origin=source_origin,
            trigger_umo=trigger_umo,
        )
        creator = getattr(self, "_create_lifecycle_background_task", None)
        try:
            if callable(creator):
                task = creator(operation, label=f"official_timer_cancel:{normalized_user_id}")
            else:
                task = asyncio.create_task(operation)
        except Exception:
            try:
                operation.close()
            except Exception:
                pass
            timer_event.pop("cancel_requested_at", None)
            return False
        if task is None:
            try:
                operation.close()
            except Exception:
                pass
            timer_event.pop("cancel_requested_at", None)
            return False
        return True

    def _has_active_activity_followup_timer(
        self,
        user: dict[str, Any] | None,
        *,
        trigger_message_id: str = "",
    ) -> bool:
        if not isinstance(user, dict):
            return False
        event = self._get_active_llm_timer(user)
        if not isinstance(event, dict) or _single_line(event.get("reason"), 40) != "activity_followup":
            return False
        current_message_id = _single_line(trigger_message_id, 120)
        original_message_id = _single_line(event.get("trigger_message_id"), 120)
        return not (current_message_id and original_message_id and current_message_id == original_message_id)

    async def _cancel_activity_followup_on_user_return(
        self,
        user_id: str,
        *,
        trigger_message_id: str = "",
        trigger_umo: str = "",
        source_text: str = "",
    ) -> bool:
        async with self._data_lock:
            user = self._get_user(user_id)
            should_cancel = self._has_active_activity_followup_timer(
                user,
                trigger_message_id=trigger_message_id,
            )
            event = self._get_active_llm_timer(user) if should_cancel else None
            expected_event_id = _single_line((event or {}).get("id"), 40)
        if not should_cancel:
            return False
        await self._cancel_llm_timer(
            user_id,
            {
                "cancel": True,
                "topic": "用户已提前回来，取消动作查岗",
                "_expected_event_id": expected_event_id,
            },
            source_text=_single_line(source_text, 140) or "用户在动作查岗到点前发来了新消息",
            source_origin="user_returned_before_activity_followup",
            trigger_message_id=trigger_message_id,
            trigger_umo=trigger_umo,
        )
        return True

    async def _schedule_llm_timer(
        self,
        user_id: str,
        payload: dict[str, Any],
        *,
        source_text: str,
        source_origin: str,
        trigger_message_id: str = "",
        trigger_umo: str = "",
    ) -> None:
        if bool(payload.get("cancel")):
            await self._cancel_llm_timer(
                user_id,
                payload,
                source_text=source_text,
                source_origin=source_origin,
                trigger_message_id=trigger_message_id,
                trigger_umo=trigger_umo,
            )
            return
        async with self._llm_timer_operation_lock(user_id):
            await self._schedule_llm_timer_locked(
                user_id,
                payload,
                source_text=source_text,
                source_origin=source_origin,
                trigger_message_id=trigger_message_id,
                trigger_umo=trigger_umo,
            )

    async def _schedule_llm_timer_locked(
        self,
        user_id: str,
        payload: dict[str, Any],
        *,
        source_text: str,
        source_origin: str,
        trigger_message_id: str = "",
        trigger_umo: str = "",
    ) -> None:
        scheduled_ts = max(_now_ts() + 30, _safe_float(payload.get("scheduled_ts"), 0))
        if scheduled_ts <= 0:
            return
        timer_event: dict[str, Any] | None = None
        note = ""
        user_snapshot: dict[str, Any] = {}
        replaced_job_id = ""
        existing_snapshot: dict[str, Any] = {}
        existing_event_id = ""
        operation_id = uuid.uuid4().hex
        async with self._data_lock:
            user = self._get_user(user_id)
            if not self._user_enabled_for_proactive(user_id, user):
                self._clear_pending_proactive_plan(user)
                self._save_data_sync(sections={"users"})
                return
            reason = _single_line(payload.get("reason"), 40) or self._infer_timer_reason(
                scheduled_ts,
                source_text,
            )
            if reason == "activity_followup":
                scheduling_now = _now_ts()
                estimated_minutes = _safe_int(payload.get("estimated_minutes"), 0, 0, 720)
                if estimated_minutes <= 0:
                    estimated_minutes = max(5, min(720, int(round((scheduled_ts - scheduling_now) / 60))))
                followup_policy = self._activity_followup_quota_policy(user)
                completion_buffer_minutes = _safe_int(
                    followup_policy.get("completion_buffer_minutes"),
                    0,
                    0,
                    30,
                )
                scheduled_ts = max(
                    scheduled_ts,
                    scheduling_now + 5 * 60,
                    scheduling_now + (estimated_minutes + completion_buffer_minutes) * 60,
                )
            else:
                estimated_minutes = 0
            action = _single_line(payload.get("action"), 24) or "message"
            if action not in {"message", "screen_peek", "photo_text", "voice"}:
                action = "message"
            if not self._friend_can_receive_proactive_reason(user, reason, action):
                reason = "check_in"
                action = "message"
            topic = _single_line(payload.get("topic"), 60) or self._timer_default_topic(
                reason,
                user,
                source_text,
            )
            motive = _single_line(payload.get("motive"), 140) or self._timer_default_motive(
                reason,
                user,
                source_text=source_text,
                topic=topic,
            )
            existing = user.get("llm_timer_event") if isinstance(user.get("llm_timer_event"), dict) else {}
            existing_active = (
                isinstance(existing, dict)
                and _single_line(existing.get("backend"), 40) == "astrbot_cron"
                and _single_line(existing.get("status"), 40) in {"scheduled", "pending", "registering", "replacing"}
                and bool(_single_line(existing.get("job_id") or existing.get("candidate_job_id"), 80))
            )
            if (
                reason == "activity_followup"
                and existing_active
                and _single_line(existing.get("reason"), 40) != "activity_followup"
            ):
                logger.info(
                    "保留已有明确预约,跳过自动动作查岗: user=%s existing=%s topic=%s",
                    user_id,
                    _single_line(existing.get("reason"), 40) or "appointment",
                    _single_line(existing.get("topic"), 80) or "-",
                )
                return
            if (
                existing_active
            ):
                replaced_job_id = _single_line(existing.get("job_id") or existing.get("candidate_job_id"), 80)
            existing_snapshot = deepcopy(existing) if isinstance(existing, dict) else {}
            existing_event_id = _single_line(existing_snapshot.get("id"), 40)
            activity = _single_line(payload.get("activity"), 60) if reason == "activity_followup" else ""
            followup_intensity = (
                self._activity_followup_intensity_for_user(payload.get("followup_intensity"), user)
                if reason == "activity_followup"
                else 1
            )
            timer_event = {
                "id": uuid.uuid4().hex,
                "scheduled_ts": scheduled_ts,
                "raw_time": _single_line(payload.get("raw_time"), 32),
                "reason": reason,
                "action": action,
                "topic": topic,
                "motive": self._normalize_internal_motive_text(motive),
                "style": _single_line(payload.get("style"), 40),
                "activity": activity,
                "estimated_minutes": estimated_minutes,
                "followup_intensity": followup_intensity,
                "seed_text": _single_line(source_text, 80),
                "origin": source_origin,
                "created_at": _now_ts(),
                "trigger_message_id": _single_line(trigger_message_id, 120),
                "trigger_umo": _single_line(trigger_umo, 160),
                "trigger_ts": _now_ts() if trigger_message_id else 0,
                "chain": list(payload.get("chain") or []) if isinstance(payload.get("chain"), list) else [],
                "silence_until_due": self._timer_source_implies_user_unavailable(source_text, payload),
                "backend": "astrbot_cron",
                "status": "replacing" if replaced_job_id else "registering",
                "operation_id": operation_id,
                "replaced_job_id": replaced_job_id,
                "previous_timer_id": existing_event_id,
            }
            note = self._format_official_timer_note(
                scheduled_ts=scheduled_ts,
                reason=reason,
                action=action,
                topic=topic,
                motive=timer_event["motive"],
                source_text=source_text,
                style=timer_event["style"],
                activity=activity,
                estimated_minutes=estimated_minutes,
                followup_intensity=followup_intensity,
            )
            user_snapshot = dict(user)
            user["llm_timer_event"] = deepcopy(timer_event)
            self._clear_llm_timer_internal_plan_fields(user)
            self._save_data_sync(sections={"users"})

        previous_running_job_id = ""
        if replaced_job_id:
            runtime_supported, runtime_status = await self._official_llm_timer_job_runtime(replaced_job_id)
            if runtime_supported and runtime_status == "running":
                previous_running_job_id = replaced_job_id
                replaced_job_id = ""
            elif runtime_supported and runtime_status in {"completed", "failed", "missing"}:
                replaced_job_id = ""

        job_id, error = await self._add_official_llm_timer_job(
            user_id=user_id,
            user=user_snapshot,
            timer_event=timer_event,
            note=note,
            trigger_umo=trigger_umo,
        )
        if not job_id:
            async with self._data_lock:
                user = self._get_user(user_id)
                current = user.get("llm_timer_event")
                if (
                    isinstance(current, dict)
                    and _single_line(current.get("id"), 40) == _single_line(timer_event.get("id"), 40)
                    and _single_line(current.get("operation_id"), 40) == operation_id
                ):
                    if existing_snapshot:
                        restored = deepcopy(existing_snapshot)
                        restored["last_replace_error"] = error or "新官方任务登记失败"
                        restored["last_replace_failed_at"] = _now_ts()
                        user["llm_timer_event"] = restored
                    else:
                        timer_event["status"] = "failed"
                        timer_event["error"] = error or "官方定时计划登记失败"
                        timer_event.pop("operation_id", None)
                        user["llm_timer_event"] = timer_event
                    self._save_data_sync(sections={"users"})
            logger.warning(
                "LLM 临时预约登记失败,已保留原任务: user=%s old_job=%s error=%s",
                user_id,
                replaced_job_id or previous_running_job_id or "-",
                error or "官方定时计划登记失败",
            )
            return

        async with self._data_lock:
            user = self._get_user(user_id)
            current = user.get("llm_timer_event")
            reservation_current = bool(
                isinstance(current, dict)
                and _single_line(current.get("id"), 40) == _single_line(timer_event.get("id"), 40)
                and _single_line(current.get("operation_id"), 40) == operation_id
            )
            if reservation_current:
                current["candidate_job_id"] = job_id
                self._save_data_sync(sections={"users"})
        if not reservation_current:
            await self._delete_official_llm_timer_job(job_id)
            logger.warning(
                "LLM 临时预约预留已失效,已回收新官方任务: user=%s job=%s",
                user_id,
                job_id,
            )
            return

        replace_error = ""
        rollback_error = ""
        if replaced_job_id:
            replaced_ok, replace_error = await self._delete_official_llm_timer_job(replaced_job_id)
            if not replaced_ok:
                rollback_ok, rollback_error = await self._delete_official_llm_timer_job(job_id)
                async with self._data_lock:
                    user = self._get_user(user_id)
                    current = user.get("llm_timer_event")
                    if (
                        isinstance(current, dict)
                        and _single_line(current.get("id"), 40) == _single_line(timer_event.get("id"), 40)
                        and _single_line(current.get("operation_id"), 40) == operation_id
                    ):
                        if rollback_ok and existing_snapshot:
                            restored = deepcopy(existing_snapshot)
                            restored["last_replace_error"] = replace_error or "旧官方任务删除失败"
                            restored["last_replace_failed_at"] = _now_ts()
                            user["llm_timer_event"] = restored
                        else:
                            current["status"] = "replace_rollback_failed"
                            current["job_id"] = replaced_job_id
                            current["candidate_job_id"] = job_id
                            current["replace_error"] = replace_error or "旧官方任务删除失败"
                            current["rollback_error"] = rollback_error or "新官方任务回滚失败"
                        self._save_data_sync(sections={"users"})
                logger.warning(
                    "LLM 临时预约替换失败,新任务回滚%s: user=%s old_job=%s new_job=%s error=%s rollback_error=%s",
                    "完成" if rollback_ok else "失败",
                    user_id,
                    replaced_job_id,
                    job_id,
                    replace_error or "-",
                    rollback_error or "-",
                )
                return

        async with self._data_lock:
            user = self._get_user(user_id)
            current = user.get("llm_timer_event")
            reservation_current = bool(
                isinstance(current, dict)
                and _single_line(current.get("id"), 40) == _single_line(timer_event.get("id"), 40)
                and _single_line(current.get("operation_id"), 40) == operation_id
            )
            if reservation_current:
                timer_event["job_id"] = job_id
                timer_event["status"] = "scheduled"
                timer_event["note"] = _single_line(note, 220)
                timer_event["replaced_job_id"] = replaced_job_id
                if previous_running_job_id:
                    timer_event["previous_running_job_id"] = previous_running_job_id
                timer_event.pop("candidate_job_id", None)
                timer_event.pop("operation_id", None)
                user["llm_timer_event"] = timer_event
                self._clear_llm_timer_internal_plan_fields(user)
                self._save_data_sync(sections={"users"})
        if not reservation_current:
            await self._delete_official_llm_timer_job(job_id)
            return
        logger.info(
            "LLM 临时预约已转写到官方定时计划: user=%s time=%s reason=%s action=%s topic=%s job=%s replaced=%s error=%s replace_error=%s",
            user_id,
            self._environment_fromtimestamp(scheduled_ts).strftime("%m-%d %H:%M:%S"),
            reason,
            action,
            topic,
            job_id or "-",
            replaced_job_id or "-",
            "-",
            replace_error or "-",
        )

