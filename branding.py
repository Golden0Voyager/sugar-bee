"""应用品牌个性化（App Branding）。

主品牌是「蜜蜂健康」（覆盖慢病管理与家庭健康记录），「蜜蜂控糖」作为控糖场景名保留。
每个用户还可以给自己的界面单独起名（如「妈妈的健康本」）。本模块负责：

1. 提供「使用场景模板（persona）」——每个模板自带建议名称、图标、标语和建议模块；
2. 把用户档案里的品牌字段归一化成模板可直接渲染的结构；
3. 校验前端提交的品牌配置。

品牌字段存放于 ``user_profiles``：``app_name`` / ``app_emoji`` / ``app_tagline`` / ``persona``。
四个字段全为空视为「尚未初始化」，前端据此弹出首次引导。
"""

import json

DEFAULT_APP_NAME = '蜜蜂健康'
DEFAULT_EMOJI = '🐝'
DEFAULT_TAGLINE = '记录全家人的健康'

# 功能模块白名单（与前端 index.html 的 ALL_MODULES 保持一致）
ALL_MODULES = ['glucose', 'blood_pressure', 'exercise', 'weight', 'medication', 'diet',
               'bone_density', 'checkup']

# 基础模板：慢病管理通用功能，除血糖外的所有模块
BASE_MODULES = ['blood_pressure', 'medication', 'weight', 'exercise', 'diet',
                'bone_density', 'checkup']

NAME_MAX_LEN = 16
EMOJI_MAX_LEN = 8
TAGLINE_MAX_LEN = 20

# 应用名的候选（第一个即默认值）。全家的应用都叫「蜜蜂健康」，只是功能模块不同；
# 想换成自己的叫法，改这里填进去的字或直接手输都行。
NAME_SUGGESTIONS = [DEFAULT_APP_NAME, '我的健康本', '健康日记']

# 使用场景模板。顺序即前端展示顺序（第一个是默认选中项）。
#
# 注意：模板**只决定功能模块**，不决定应用名和图标——所有模板默认都叫
# 「蜜蜂健康」🐝。`icon` 仅用于选择卡片上的小图标，让几个选项看起来不一样，
# 不会被填进用户的品牌设置。
#
# 血糖是唯一不在基础版里的模块，所以模板的差异主要落在「要不要血糖」上：
# 日常健康不含，多维控糖和为家人记录含。运动健身是基础版里偏运动/体重的子集。
PERSONAS = [
    {
        # 家庭默认：慢病管理通用版（血压/用药/体重/运动/餐食/骨密度/体检），不含血糖。
        'id': 'daily_health',
        'label': '日常健康',
        'desc': '血压、用药、体重、运动、餐食、骨密度、体检，不含血糖',
        'icon': '🌱',
        'tagline': '每天一记，心里有数',
        'modules': list(BASE_MODULES),
    },
    {
        # 糖尿病患者（如爸爸）：基础之上只多一个血糖。
        'id': 'glucose_care',
        'label': '多维控糖',
        'desc': '日常健康 + 血糖记录、预测、达标统计',
        'icon': '🐝',
        'tagline': '勤劳控糖，甜蜜生活',
        'modules': list(ALL_MODULES),
    },
    {
        # 偏运动与体重管理：只留运动、体重、餐食三块。
        'id': 'fitness',
        'label': '运动健身',
        'desc': '运动打卡、体重变化、餐食热量，看自己练得怎么样',
        'icon': '🏃',
        'tagline': '一点点，看得见',
        'modules': ['exercise', 'weight', 'diet'],
    },
    {
        # 替家里人记录：血糖、血压、吃药，够用且不杂。
        'id': 'family_care',
        'label': '为家人记录',
        'desc': '替家里人记血糖、血压、吃药',
        'icon': '💗',
        'tagline': '家人的健康，我来记',
        'modules': ['glucose', 'blood_pressure', 'medication'],
    },
]

_PERSONA_MAP = {p['id']: p for p in PERSONAS}


def get_persona(persona_id):
    """按 id 取场景模板，找不到返回 None。"""
    if not persona_id:
        return None
    return _PERSONA_MAP.get(str(persona_id))


def persona_options():
    """返回给前端的场景模板列表（副本，避免调用方改动常量）。

    `emoji` / `name_suggestions` 由这里统一注入，而不是写在模板里——所有模板的
    默认应用名都是「蜜蜂健康」、默认图标都是 🐝，模板之间只有功能模块和标语不同。
    """
    return [dict(
        p,
        modules=list(p['modules']),
        emoji=DEFAULT_EMOJI,
        name_suggestions=list(NAME_SUGGESTIONS),
    ) for p in PERSONAS]


def clean_name(value):
    """归一化应用名：去首尾空白、压缩连续空白、截断超长。空值返回 ''。"""
    if value is None:
        return ''
    text = ' '.join(str(value).split())
    return text[:NAME_MAX_LEN]


def clean_emoji(value):
    """归一化图标：只保留单个 emoji/短字符，空值返回 ''。"""
    if value is None:
        return ''
    text = str(value).strip()
    if not text or len(text) > EMOJI_MAX_LEN:
        return ''
    return text


def clean_tagline(value):
    """归一化标语（副标题）。"""
    if value is None:
        return ''
    text = ' '.join(str(value).split())
    return text[:TAGLINE_MAX_LEN]


def normalize_modules(value):
    """把档案里的 enabled_modules 归一化成有效模块列表。

    兼容三种来源：``user_profiles`` 里的 JSON 字符串、``user_manager`` 解析后的
    list、以及前端提交的数组。返回 ``[]`` 表示「未限制」，即全部模块启用。
    未知模块名会被丢弃，避免历史脏数据让白名单永久失效。
    """
    if not value:
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return []
    if not isinstance(value, (list, tuple)):
        return []

    result = []
    for item in value:
        if item in ALL_MODULES and item not in result:
            result.append(item)
    return result


def resolve_branding(user):
    """把用户档案转成模板可直接使用的品牌信息。

    未自定义名称时回落到产品默认品牌，并标记 ``onboarded=False``，
    前端据此弹出首次引导。``enabled_modules`` 一并返回，让引导弹窗能
    沿用老账号已有的模块勾选，而不是套用模板把用户原有模块冲掉。

    图标只在用户自己填过时才用他自己的，否则一律用品牌默认 🐝——
    图标不再跟着场景模板变（历史行为：选了「主要看血压」就变 ❤️，
    结果一家六口六个图标，看着像六个不同的应用）。
    """
    user = user or {}
    persona = get_persona(user.get('persona'))
    app_name = clean_name(user.get('app_name'))

    emoji = clean_emoji(user.get('app_emoji')) or DEFAULT_EMOJI
    tagline = clean_tagline(user.get('app_tagline')) or (persona['tagline'] if persona else DEFAULT_TAGLINE)

    return {
        'app_name': app_name or DEFAULT_APP_NAME,
        'emoji': emoji,
        'tagline': tagline,
        'persona': persona['id'] if persona else '',
        'customized': bool(app_name),
        'onboarded': bool(app_name),
        'enabled_modules': normalize_modules(user.get('enabled_modules')),
    }


def build_branding_payload(data):
    """校验并归一化前端提交的品牌配置。

    返回 ``(payload, error)``：成功时 error 为 None；失败时 payload 为 None。
    ``enabled_modules`` 允许缺省（缺省表示不改动模块配置）。
    """
    data = data or {}
    raw_name = data.get('app_name')
    app_name = clean_name(raw_name)
    if not app_name:
        return None, '应用名称不能为空'
    if len(str(raw_name).strip()) > NAME_MAX_LEN:
        return None, f'应用名称最多 {NAME_MAX_LEN} 个字'

    persona_id = (data.get('persona') or '').strip()
    persona = get_persona(persona_id)
    if persona_id and not persona:
        return None, '未知的使用场景'

    payload = {
        'app_name': app_name,
        'app_emoji': clean_emoji(data.get('app_emoji')),
        'app_tagline': clean_tagline(data.get('app_tagline')),
        'persona': persona_id,
    }

    if 'enabled_modules' in data:
        modules = data.get('enabled_modules')
        if not isinstance(modules, list):
            return None, 'enabled_modules 必须是数组'
        valid = [m for m in modules if m in ALL_MODULES]
        if not valid:
            return None, '请至少保留一个功能模块'
        payload['enabled_modules'] = valid

    return payload, None
