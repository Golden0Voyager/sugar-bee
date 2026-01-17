import datetime
import json
import os

CONFIG_FILE = "user_config.json"

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
    return BADGE_SYSTEM['encourage']


def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {
        "name": "用户", "weight": 75, "height": 170, "birth_year": 1964, "gender": "male",
        "target": {"fasting_min": 3.9, "fasting_max": 7.0, "postmeal_max": 7.8, "premeal_max": 6.5},
        "glucose_pattern": {"fasting_range": "6.0-7.2", "postmeal_range": "6.5-8.0"}
    }

def save_config(config):
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=4, ensure_ascii=False)

# 获取当前配置
USER_PROFILE = load_config()

def calculate_bmr():
    config = load_config()
    current_year = datetime.datetime.now().year
    age = current_year - config["birth_year"]
    s = 5 if config["gender"] == "male" else -161
    bmr = int(10 * config["weight"] + 6.25 * config["height"] - 5 * age + s)
    return bmr

def get_ai_system_prompt():
    config = load_config()
    glucose_pattern = config.get('glucose_pattern', {
        'fasting_range': '6.0-7.2',
        'postmeal_range': '6.5-8.0'
    })
    return f"""
    【用户健康档案】
    - 身份：二型糖尿病患者（{config['gender']}, {datetime.datetime.now().year - config['birth_year']}岁）
    - 控糖状态：血糖控制良好
    - 血糖特点：
      - 空腹通常在 {glucose_pattern['fasting_range']}，餐后通常在 {glucose_pattern['postmeal_range']}
    """

DAILY_ROUTINE = """
    【血糖测量时间点】
    - 07:15 空腹 | 08:45 运动后餐前 | 11:00 早餐后2小时 | 14:30 午餐后2小时 | 20:00 晚餐后2小时 | 22:00 睡前
""" 