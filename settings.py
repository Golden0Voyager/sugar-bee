import json

from utils.timezone import now as app_now

CONFIG_FILE = "user_config.json"

# ========== AI 模型配置 ==========
# 降级链：ModelScope → SenseNova (text/report)
# vision 不走 SenseNova
# 应用内置 API Key，用户无需自行配置

# ModelScope 模型配置（国内可用，免费2000次/天，单模型500次/天，OpenAI 兼容接口）
# 三类任务使用不同模型，有效容量翻3倍（每模型独立500次/天）
MODELSCOPE_MODELS = {
    'text': ['deepseek-ai/DeepSeek-V4-Pro'],          # JSON解析/预测
    'vision': ['Qwen/Qwen3-VL-235B-A22B-Instruct', 'Qwen/Qwen3.5-397B-A17B', 'Qwen/Qwen3.5-122B-A10B', 'Qwen/Qwen3-VL-8B-Thinking'],  # 截图识别，能力优先降级
    'report': ['Qwen/Qwen3.5-397B-A17B'],      # 报告分析
    'chat': ['deepseek-ai/DeepSeek-V4-Pro'],   # 健康助手 fallback（ModelScope 可用）
    'extra_body': {'enable_thinking': False},        # 仅对 text 任务生效（Qwen3 标准模型需关闭思考模式）
}
MODELSCOPE_BASE_URL = 'https://api-inference.modelscope.cn/v1'

# SenseNova（商汤）模型配置
SENSENOVA_MODELS = {
    'text': ['deepseek-v4-flash'],
    'vision': [],   # 图片识别不走 SenseNova
    'report': ['deepseek-v4-flash'],
}
SENSENOVA_BASE_URL = 'https://token.sensenova.cn/v1'

# ========== 血糖值验证范围常量 ==========
# 用于数据验证和预测值范围检查，单位：mmol/L
GLUCOSE_RANGE = {
    # 有效血糖值范围（超出此范围视为无效数据）
    'valid': {'min': 2.0, 'max': 25.0},
    # 空腹血糖预测范围
    'fasting_prediction': {'min': 3.5, 'max': 10.0},
    # 餐后血糖预测范围
    'postmeal_prediction': {'min': 3.5, 'max': 15.0},
    # 运动后血糖预测范围
    'post_exercise_prediction': {'min': 3.5, 'max': 10.0},
    # 通用预测范围
    'general_prediction': {'min': 3.5, 'max': 12.0},
}


def is_valid_glucose(value):
    """检查血糖值是否在有效范围内"""
    if value is None:
        return False
    try:
        v = float(value)
        return GLUCOSE_RANGE['valid']['min'] <= v <= GLUCOSE_RANGE['valid']['max']
    except (ValueError, TypeError):
        return False


def is_valid_prediction(value, prediction_type='general'):
    """检查预测值是否在合理范围内"""
    if value is None:
        return False
    try:
        v = float(value)
        range_key = f'{prediction_type}_prediction'
        if range_key not in GLUCOSE_RANGE:
            range_key = 'general_prediction'
        r = GLUCOSE_RANGE[range_key]
        return r['min'] <= v <= r['max']
    except (ValueError, TypeError):
        return False

# 科学的血糖达标标准（基于《中国糖尿病防治指南（2024版）》）
# 单位：mmol/L
GLUCOSE_TARGETS = {
    # 空腹血糖标准
    'fasting': {
        'min': 4.4,
        'max': 7.0,
        'optimal_max': 6.1,  # 理想上限
        'name': '空腹血糖'
    },
    # 餐前血糖标准
    'premeal': {
        'min': 4.4,
        'max': 7.0,
        'optimal_max': 6.1,
        'name': '餐前血糖'
    },
    # 餐后1小时血糖标准
    'postmeal_1h': {
        'min': 4.4,
        'max': 11.1,  # 餐后1小时通常较高
        'optimal_max': 10.0,
        'name': '餐后1小时血糖'
    },
    # 餐后2小时血糖标准
    'postmeal_2h': {
        'min': 4.4,
        'max': 10.0,
        'optimal_max': 7.8,
        'name': '餐后2小时血糖'
    },
    # 睡前血糖标准
    'bedtime': {
        'min': 5.0,
        'max': 8.0,
        'optimal_max': 7.0,
        'name': '睡前血糖'
    },
    # 运动后血糖标准（较宽松，避免低血糖）
    'post_exercise': {
        'min': 4.0,
        'max': 10.0,
        'optimal_max': 8.0,
        'name': '运动后血糖'
    },
    # 通用标准（兜底）
    'default': {
        'min': 3.9,
        'max': 10.0,
        'optimal_max': 7.8,
        'name': '血糖'
    }
}

# 徽章奖励系统
BADGE_SYSTEM = {
    'gold': {
        'min_rate': 100,
        'icon': '🥇',
        'name': '金牌',
        'color': '#FFD700',
        'bg_color': '#FFF8DC',
        'text_color': '#B8860B',
        'description': '完美达标！继续保持！'
    },
    'silver': {
        'min_rate': 80,
        'icon': '🥈',
        'name': '银牌',
        'color': '#C0C0C0',
        'bg_color': '#F5F5F5',
        'text_color': '#696969',
        'description': '非常优秀！再接再厉！'
    },
    'bronze': {
        'min_rate': 60,
        'icon': '🥉',
        'name': '铜牌',
        'color': '#CD7F32',
        'bg_color': '#FFF5EE',
        'text_color': '#8B4513',
        'description': '不错的表现！继续努力！'
    },
    'encourage': {
        'min_rate': 0,
        'icon': '💪',
        'name': '加油',
        'color': '#FF6B6B',
        'bg_color': '#FFF0F0',
        'text_color': '#DC143C',
        'description': '别灰心，调整饮食和运动！'
    }
}


def get_glucose_target(glucose_type):
    """根据血糖类型获取对应的达标标准"""
    glucose_type = glucose_type or ''

    if '空腹' in glucose_type and '血压' not in glucose_type:
        return GLUCOSE_TARGETS['fasting']
    elif '餐后1小时' in glucose_type or '餐后1h' in glucose_type.lower():
        return GLUCOSE_TARGETS['postmeal_1h']
    elif '餐后2小时' in glucose_type or '餐后2h' in glucose_type.lower() or '餐后' in glucose_type:
        return GLUCOSE_TARGETS['postmeal_2h']
    elif '晚饭前' in glucose_type or '晚餐前' in glucose_type:
        return GLUCOSE_TARGETS['premeal']
    elif '餐前' in glucose_type:
        return GLUCOSE_TARGETS['premeal']
    elif '睡前' in glucose_type:
        return GLUCOSE_TARGETS['bedtime']
    elif '运动后' in glucose_type:
        return GLUCOSE_TARGETS['post_exercise']
    else:
        return GLUCOSE_TARGETS['default']


def check_glucose_compliance(value, glucose_type, strict=False):
    """
    检查血糖值是否达标

    Args:
        value: 血糖值 (mmol/L)
        glucose_type: 血糖类型
        strict: 是否使用严格标准（optimal_max）

    Returns:
        dict: {'is_compliant': bool, 'level': str, 'target': dict}
    """
    target = get_glucose_target(glucose_type)
    max_val = target['optimal_max'] if strict else target['max']

    is_compliant = target['min'] <= value <= max_val
    is_optimal = target['min'] <= value <= target['optimal_max']

    if value < target['min']:
        level = 'low'  # 低血糖
    elif value <= target['optimal_max']:
        level = 'optimal'  # 理想
    elif value <= target['max']:
        level = 'acceptable'  # 可接受
    else:
        level = 'high'  # 偏高

    return {
        'is_compliant': is_compliant,
        'is_optimal': is_optimal,
        'level': level,
        'target': target
    }


def get_badge_for_rate(compliance_rate):
    """根据达标率获取对应的徽章"""
    for badge_key in ['gold', 'silver', 'bronze', 'encourage']:
        badge = BADGE_SYSTEM[badge_key]
        if compliance_rate >= badge['min_rate']:
            return {
                'key': badge_key,
                **badge
            }



# ========== Emoji 用户映射 ==========
# 用于批量输入时按 emoji 前缀识别用户身份
EMOJI_USER_MAP: dict[str, int] = {
    "🐯": 6,  # 妈妈
    "🐰": 1,  # 爸爸
}

# 反向映射：user_id → emoji，用于前端展示
USER_EMOJI_MAP: dict[int, str] = {v: k for k, v in EMOJI_USER_MAP.items()}


# ========== 用户档案默认值常量 ==========
# 由 load_config / calculate_bmr / get_ai_system_prompt 等在用户档案缺字段时兜底使用
DEFAULT_PROFILE = {
    "name": "用户",
    "weight": 75,
    "height": 170,
    "birth_year": 1964,
    "gender": "male",
    "target_weight": None,
}

DEFAULT_TARGET = {
    "fasting_min": 3.9,
    "fasting_max": 7.0,
    "postmeal_max": 7.8,
    "premeal_max": 6.5,
}

DEFAULT_GLUCOSE_PATTERN = {
    "fasting_range": "6.0-7.2",
    "postmeal_range": "6.5-8.0",
}

DEFAULT_MEALS = {
    "breakfast": {
        "calories": 300,
        "carbs_grams": 45,
        "gi_value": 65,
        "time": "09:00",
        "enabled": True,
    },
    "lunch": {
        "calories": 500,
        "carbs_grams": 75,
        "gi_value": 60,
        "time": "11:30",
        "enabled": True,
    },
    "dinner": {
        "calories": 500,
        "carbs_grams": 75,
        "gi_value": 60,
        "time": "18:00",
        "enabled": True,
    },
}


def load_config(user_id: int | None = None) -> dict:
    """加载用户配置。

    - 传入 user_id:从 SQLite user_profiles 读取该用户配置(推荐路径)。
      缺失字段用 DEFAULT_* 常量补齐(default_meals/target/glucose_pattern)。
    - 不传 user_id:返回 DEFAULT_PROFILE 全量(已废弃的全局兜底)。新代码不应再走此分支。
    """
    if user_id is not None:
        # 延迟 import 避免循环依赖(user_manager 可能反向 import settings)
        from user_manager import UserManager
        from core.config import DB_NAME
        cfg = UserManager(DB_NAME).get_user_config(user_id)
        # 缺失字段补齐
        if not cfg.get('default_meals'):
            cfg['default_meals'] = dict(DEFAULT_MEALS)
        if not cfg.get('target'):
            cfg['target'] = dict(DEFAULT_TARGET)
        if not cfg.get('glucose_pattern'):
            cfg['glucose_pattern'] = dict(DEFAULT_GLUCOSE_PATTERN)
        return cfg
    # 兜底:返回默认 profile 全量(legacy 路径)
    return {
        **DEFAULT_PROFILE,
        "target": dict(DEFAULT_TARGET),
        "glucose_pattern": dict(DEFAULT_GLUCOSE_PATTERN),
        "default_meals": dict(DEFAULT_MEALS),
    }

def calculate_bmr(user_id: int) -> int:
    """根据用户档案计算基础代谢率(BMR,kcal/日)。user_id 必填。"""
    config = load_config(user_id)
    current_year = app_now().year
    age = current_year - (config.get("birth_year") or DEFAULT_PROFILE["birth_year"])
    gender = config.get("gender") or DEFAULT_PROFILE["gender"]
    weight = config.get("weight") or DEFAULT_PROFILE["weight"]
    height = config.get("height") or DEFAULT_PROFILE["height"]
    s = 5 if gender == "male" else -161
    return int(10 * weight + 6.25 * height - 5 * age + s)


def calculate_bmi(weight_kg, height_cm=None, user_id: int | None = None):
    """计算 BMI 值。

    Args:
        weight_kg: 体重(kg)
        height_cm: 身高(cm),优先使用
        user_id: 当未提供 height_cm 时,从该用户档案读身高;未提供则返回 None(不再回退全局)

    Returns:
        float | None: BMI 值(保留1位小数);任一参数无效返回 None
    """
    if height_cm is None and user_id is not None:
        cfg = load_config(user_id)
        height_cm = cfg.get("height")
    if not height_cm or height_cm <= 0 or not weight_kg or weight_kg <= 0:
        return None
    height_m = height_cm / 100
    return round(weight_kg / (height_m * height_m), 1)


def get_bmi_category(bmi):
    """根据中国标准返回 BMI 分类

    中国成人标准：偏瘦<18.5, 正常18.5-24, 超重24-28, 肥胖>=28

    Returns:
        dict: {'label': str, 'color': str}
    """
    if bmi is None:
        return {'label': '未知', 'color': '#9E9E9E'}
    if bmi < 18.5:
        return {'label': '偏瘦', 'color': '#42A5F5'}
    elif bmi < 24:
        return {'label': '正常', 'color': '#4CAF50'}
    elif bmi < 28:
        return {'label': '超重', 'color': '#FFA726'}
    else:
        return {'label': '肥胖', 'color': '#EF5350'}


def get_ai_system_prompt(user_id: int | None = None) -> str:
    """生成 AI 系统提示词。user_id 缺失时使用 DEFAULT_PROFILE 兜底(避免崩溃)。"""
    config = load_config(user_id) if user_id is not None else {
        **DEFAULT_PROFILE,
        "glucose_pattern": dict(DEFAULT_GLUCOSE_PATTERN),
    }
    glucose_pattern = config.get('glucose_pattern') or dict(DEFAULT_GLUCOSE_PATTERN)
    gender = config.get('gender') or DEFAULT_PROFILE['gender']
    birth_year = config.get('birth_year') or DEFAULT_PROFILE['birth_year']
    return f"""
    【用户健康档案】
    - 身份：二型糖尿病患者（{gender}, {app_now().year - birth_year}岁）
    - 控糖状态：血糖控制良好
    - 血糖特点：
      - 空腹通常在 {glucose_pattern['fasting_range']}，餐后通常在 {glucose_pattern['postmeal_range']}
    """

DAILY_ROUTINE = """
    【血糖测量时间点】
    - 07:15 空腹 | 08:45 运动后 | 11:00 早餐后2小时 | 14:30 午餐后2小时 | 17:30 晚饭前 | 20:00 晚餐后2小时 | 22:00 睡前
"""
