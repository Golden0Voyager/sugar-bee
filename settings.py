
import datetime

from utils.timezone import now as app_now

CONFIG_FILE = "user_config.json"

# ========== AI 供应商配置 ==========
# 四个任务(血糖解析/预测 = text、截图识别 = vision、报告分析 = report、健康助手 = chat)
# 共用同一条降级顺序 AI_PROVIDER_ORDER,顺序即优先级;某家没有该任务的模型时自动跳过。
# 应用内置 API Key,用户无需自行配置。
#
# 各家 extra_body 写法不同,必须分别配置:
#   扁平 {'enable_thinking': False}                          —— ModelScope 的 Qwen 系需要
#   嵌套 {'chat_template_kwargs': {'enable_thinking': ...}}  —— 端砚、Agnes 需要(扁平写法对端砚无效)
# 忘记关思考的后果不是报错,而是模型把输出全写进 reasoning_content、content 返回空字符串。

AI_PROVIDER_ORDER = ['intern', 'sensenova', 'agnes', 'modelscope']

AI_PROVIDERS = {
    # 书生·端砚(上海 AI 实验室,测试期免费额度按月发放)
    # 全系模型默认思考型,必须靠 extra_body 关掉;实测关掉后中位时延 0.59s
    'intern': {
        'name': '端砚',
        'base_url': 'https://discovery-api.intern-ai.org.cn/v1',
        'api_key_env': 'INTERN_API_KEY',
        'timeout': 60,
        'extra_body': {'chat_template_kwargs': {'enable_thinking': False}},
        'models': {
            # Sage-A1.5 免费,实测中位 0.59s;deepseek-v4.1-flash-1017 按墨点计费,作付费兜底
            # 付费兜底没进 vision:实测读图 93s,超过本家 60s 超时,白等不如直接交给
            # 后面的 SenseNova(读图 3.7s)。要用它读图就得把本家 timeout 提到 120 以上
            'text': ['Sage-A1.5', 'deepseek-v4.1-flash-1017'],
            'vision': ['Sage-A1.5'],
            'report': ['Sage-A1.5', 'deepseek-v4.1-flash-1017'],
            'chat': ['Sage-A1.5', 'deepseek-v4.1-flash-1017'],
        },
    },
    # 商汤 SenseNova。deepseek 系已支持图片输入,故 vision 不再留空
    # deepseek-v4.1-flash / kimi-k3 / sensenova-u1.5-lite 本账号不可用(403 / 429 / 404),不要加进来
    'sensenova': {
        'name': 'SenseNova',
        'base_url': 'https://token.sensenova.cn/v1',
        'api_key_env': 'SENSENOVA_API_KEY',
        'timeout': 60,
        'extra_body': None,
        'models': {
            'text': ['deepseek-flash', 'sensenova-6.8-flash-lite'],
            'vision': ['deepseek-flash', 'sensenova-6.8-flash-lite'],
            'report': ['deepseek-flash', 'sensenova-6.8-flash-lite'],
            'chat': ['deepseek-flash'],
        },
    },
    # Agnes。免费档限流很紧(10 RPM + 网关侧连接数上限),实测会 429,只适合兜底
    # agnes-3.0-flash 文本路径实测中位 26s,故排在 2.5-flash 之后
    'agnes': {
        'name': 'Agnes',
        'base_url': 'https://apihub.agnes-ai.cn/v1',
        'api_key_env': 'AGNES_API_KEY',
        'timeout': 90,
        'extra_body': {'chat_template_kwargs': {'enable_thinking': False}},
        'models': {
            'text': ['agnes-2.5-flash', 'agnes-3.0-flash'],
            'vision': ['agnes-3.0-flash', 'agnes-2.5-flash'],
            'report': ['agnes-2.5-flash'],
            'chat': ['agnes-2.5-flash', 'agnes-3.0-flash'],
        },
    },
    # 魔搭 ModelScope。按魔粒计费(每天约 125–500 次),故排在最后,只在前三家都不可用时动用
    # vision 用 Qwen3.5:同一张报告图实测 122B 3.6s、397B 11.6s,都是 8/8
    # 这两个已下架、不要写回来(400 no provider support):
    #   Qwen/Qwen3-VL-235B-A22B-Instruct、Qwen/Qwen3-VL-8B-Thinking
    'modelscope': {
        'name': 'ModelScope',
        'base_url': 'https://api-inference.modelscope.cn/v1',
        'api_key_env': 'MODELSCOPE_API_KEY',
        'timeout': 60,
        'extra_body': {'enable_thinking': False},
        'models': {
            'text': ['deepseek-ai/DeepSeek-V4.1-Flash-1017'],
            'vision': ['Qwen/Qwen3.5-122B-A10B', 'Qwen/Qwen3.5-397B-A17B'],
            'report': ['Qwen/Qwen3.5-397B-A17B'],
            'chat': ['deepseek-ai/DeepSeek-V4.1-Flash-1017'],
        },
    },
}

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
    elif '晚饭前' in glucose_type or '晚餐前' in glucose_type or '餐前' in glucose_type:
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
        cfg = UserManager().get_user_config(user_id)
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
    """根据用户档案计算基础代谢率(BMR,kcal/日)。user_id 必填。

    优先用 Katch-McArdle 体成分公式(需要 30 天内的体脂秤读数):
        BMR = 370 + 21.6 × 去脂体重kg,去脂体重 = 体重 × (1 − 体脂率/100)
    体脂数据缺失/过旧/离谱(结果超出 800–3000)时,退回 Mifflin-St Jeor:
        10×体重 + 6.25×身高 − 5×年龄 + 5(男)/−161(女)
    """
    config = load_config(user_id)
    current_year = app_now().year
    age = current_year - (config.get("birth_year") or DEFAULT_PROFILE["birth_year"])
    gender = config.get("gender") or DEFAULT_PROFILE["gender"]
    weight = config.get("weight") or DEFAULT_PROFILE["weight"]
    height = config.get("height") or DEFAULT_PROFILE["height"]
    s = 5 if gender == "male" else -161
    bmr = int(10 * weight + 6.25 * height - 5 * age + s)

    body_fat = _latest_body_fat(user_id)
    if body_fat is not None:
        rate, rec_weight = body_fat
        lbm_weight = rec_weight if rec_weight else weight
        katch = int(KATCH_MCARDLE_CONSTANT + KATCH_MCARDLE_PER_LBM * lbm_weight * (1 - rate / 100))
        if BMR_MIN_PLAUSIBLE <= katch <= BMR_MAX_PLAUSIBLE:
            return katch
    return bmr


# 体成分 BMR 口径:体脂读数超过这个天数视为过旧,退回 Mifflin-St Jeor
BODY_FAT_BMR_MAX_AGE_DAYS = 30
KATCH_MCARDLE_CONSTANT = 370
KATCH_MCARDLE_PER_LBM = 21.6
# Katch-McArdle 结果落在这个区间外说明体脂/体重读数离谱,不采用
BMR_MIN_PLAUSIBLE = 800
BMR_MAX_PLAUSIBLE = 3000


def _latest_body_fat(user_id):
    """取该用户最近一条带体脂率的体成分记录。

    Returns:
        (rate, weight) 元组;rate 必为 float,weight 记录里没有时为 None;
        无记录/记录超过 30 天/任何异常一律返回 None(调用方退回 Mifflin)。
    """
    try:
        # 延迟 import 避免循环依赖(utils.db 可能反向 import settings)
        from utils.db import get_raw_conn, put_raw_conn
        conn = get_raw_conn()
        try:
            c = conn.cursor()
            c.execute(
                "SELECT body_fat_rate, weight, timestamp FROM records "
                "WHERE user_id = ? AND body_fat_rate IS NOT NULL "
                "AND body_fat_rate > 0 AND body_fat_rate < 70 "
                "ORDER BY timestamp DESC LIMIT 1",
                (user_id,),
            )
            row = c.fetchone()
        finally:
            put_raw_conn(conn)
        if not row:
            return None
        measured = datetime.datetime.strptime(str(row['timestamp'])[:19], '%Y-%m-%d %H:%M:%S')
        if (app_now() - measured).days > BODY_FAT_BMR_MAX_AGE_DAYS:
            return None
        return float(row['body_fat_rate']), (float(row['weight']) if row['weight'] else None)
    except Exception:
        return None


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


# ========== 体成分状态阈值(唯一来源)==========
# 体成分记录的 7 个字段与核心 3 项(仪表盘/图表消费)
BODY_COMP_FIELDS = ('body_fat_rate', 'body_fat_mass', 'muscle_mass',
                    'skeletal_muscle_mass', 'protein_mass', 'visceral_fat', 'basal_metabolism')
BODY_COMP_CORE = ('body_fat_rate', 'visceral_fat', 'muscle_mass')

# 体脂率分档表(按性别、年龄):
#   (age_min, age_max, low_bound, high_bound):< low 偏低,在区间内 标准,> high 偏高
BODY_FAT_BANDS = {
    'male':   [(0, 39, 8.0, 19.0), (40, 59, 11.0, 21.0), (60, 200, 13.0, 24.0)],
    'female': [(0, 39, 21.0, 32.0), (40, 59, 24.0, 34.0), (60, 200, 27.0, 37.0)],
}
# 肌肉量按比例(muscle_mass / weight)判定,绝不按绝对 kg(秤的"肌肉量"含水)
MUSCLE_MASS_RATIO_MIN = {'male': 0.70, 'female': 0.64}
MUSCLE_MASS_RATIO_EXCELLENT = {'male': 0.78, 'female': 0.72}
VISCERAL_FAT_MAX = 9

_UNKNOWN = {'label': '未知', 'color': '#9E9E9E'}


def _body_fat_band(gender: str, age: int):
    """按性别取分档表(未知性别按 male),按 age 命中档位;无命中回落最后一档。"""
    bands = BODY_FAT_BANDS.get(gender, BODY_FAT_BANDS['male'])
    for band in bands:
        if band[0] <= age <= band[1]:
            return band
    return bands[-1]


def get_body_fat_category(rate: float | None, gender: str, age: int) -> dict:
    """按性别 + 年龄分档判定体脂率状态。

    Returns:
        dict: {'label': str, 'color': str};rate 缺失/非正数/非数值 → 未知
    """
    if rate is None:
        return dict(_UNKNOWN)
    try:
        rate = float(rate)
    except (TypeError, ValueError):
        return dict(_UNKNOWN)
    if rate <= 0:
        return dict(_UNKNOWN)
    _, _, low, high = _body_fat_band(gender, age)
    if rate < low:
        return {'label': '偏低', 'color': '#42A5F5'}
    if rate > high:
        return {'label': '偏高', 'color': '#FFA726'}
    return {'label': '标准', 'color': '#4CAF50'}


def get_body_fat_upper_bound(gender: str, age: int) -> float:
    """返回命中分档的高界(给图表画上限线用)。"""
    return _body_fat_band(gender, age)[3]


def get_visceral_fat_category(level: float | None) -> dict:
    """内脏脂肪等级判定:≤9 标准、10–14 偏高、≥15 高。"""
    if level is None:
        return dict(_UNKNOWN)
    try:
        level = float(level)
    except (TypeError, ValueError):
        return dict(_UNKNOWN)
    if level <= 0:
        return dict(_UNKNOWN)
    if level <= VISCERAL_FAT_MAX:
        return {'label': '标准', 'color': '#4CAF50'}
    if level <= 14:
        return {'label': '偏高', 'color': '#FFA726'}
    return {'label': '高', 'color': '#EF5350'}


def get_muscle_mass_category(muscle_mass: float | None, weight: float | None, gender: str) -> dict:
    """按比例 muscle_mass / weight 判定肌肉量状态(绝不按绝对 kg)。"""
    if muscle_mass is None or weight is None:
        return dict(_UNKNOWN)
    try:
        muscle_mass = float(muscle_mass)
        weight = float(weight)
    except (TypeError, ValueError):
        return dict(_UNKNOWN)
    if muscle_mass <= 0 or weight <= 0:
        return dict(_UNKNOWN)
    ratio = muscle_mass / weight
    if ratio >= MUSCLE_MASS_RATIO_EXCELLENT.get(gender, MUSCLE_MASS_RATIO_EXCELLENT['male']):
        return {'label': '优秀', 'color': '#4CAF50'}
    if ratio >= MUSCLE_MASS_RATIO_MIN.get(gender, MUSCLE_MASS_RATIO_MIN['male']):
        return {'label': '标准', 'color': '#4CAF50'}
    return {'label': '偏低', 'color': '#42A5F5'}


def get_muscle_mass_ratio_min(gender: str) -> float:
    """返回肌肉量比例的最低标准(未知性别按 male)。"""
    return MUSCLE_MASS_RATIO_MIN.get(gender, MUSCLE_MASS_RATIO_MIN['male'])


def is_glucose_module_enabled(user_id: int | None) -> bool:
    """判断用户是否启用了血糖模块（供 AI 提示词等按模块切换行为）。

    user_id 为 None、查询出错、用户不存在、或 enabled_modules 为空
    （历史兼容语义「全部启用」）时一律返回 True，保持旧行为；
    只有在能确认配置里明确不含 glucose 时才返回 False。
    """
    if user_id is None:
        return True
    try:
        # 延迟 import 避免循环依赖（load_config 同款做法）
        from user_manager import UserManager
        return bool(UserManager().is_module_enabled(user_id, 'glucose'))
    except Exception:
        return True


def get_ai_system_prompt(user_id: int | None = None) -> str:
    """生成 AI 系统提示词。user_id 缺失时使用 DEFAULT_PROFILE 兜底(避免崩溃)。

    血糖模块关闭的用户拿到通用健康档案，不再被写成「二型糖尿病患者」；
    血糖模块开启的用户保持原糖尿病档案不变。
    """
    config = load_config(user_id) if user_id is not None else {
        **DEFAULT_PROFILE,
        "glucose_pattern": dict(DEFAULT_GLUCOSE_PATTERN),
    }
    gender = config.get('gender') or DEFAULT_PROFILE['gender']
    birth_year = config.get('birth_year') or DEFAULT_PROFILE['birth_year']
    age = app_now().year - birth_year
    gender_label = '男' if gender == 'male' else '女'
    if not is_glucose_module_enabled(user_id):
        return f"""
    【用户健康档案】
    - 身份：健康记录用户（{gender_label}, {age}岁）
    - 管理方向：日常健康记录与慢病管理（用户未启用血糖管理模块，不要假设其患有糖尿病，也不要主动提及血糖数据）
    """
    glucose_pattern = config.get('glucose_pattern') or dict(DEFAULT_GLUCOSE_PATTERN)
    return f"""
    【用户健康档案】
    - 身份：二型糖尿病患者（{gender_label}, {age}岁）
    - 控糖状态：血糖控制良好
    - 血糖特点：
      - 空腹通常在 {glucose_pattern['fasting_range']}，餐后通常在 {glucose_pattern['postmeal_range']}
    """

DAILY_ROUTINE = """
    【血糖测量时间点】
    - 07:15 空腹 | 08:45 运动后 | 11:00 早餐后2小时 | 14:30 午餐后2小时 | 17:30 晚饭前 | 20:00 晚餐后2小时 | 22:00 睡前
"""
