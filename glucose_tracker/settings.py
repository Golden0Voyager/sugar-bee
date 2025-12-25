import datetime
import json
import os

CONFIG_FILE = "user_config.json"

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
    return f"""
    【用户健康档案】
    - 身份：二型糖尿病患者（{config['gender']}, {datetime.datetime.now().year - config['birth_year']}岁）
    - 控糖状态：血糖控制良好
    - 血糖特点：
      - 空腹通常在 {config['glucose_pattern']['fasting_range']}，餐后通常在 {config['glucose_pattern']['postmeal_range']}
    """

DAILY_ROUTINE = """
    【血糖测量时间点】
    - 07:15 空腹 | 08:45 运动后餐前 | 11:00 早餐后2小时 | 14:30 午餐后2小时 | 20:00 晚餐后2小时 | 22:00 睡前
""" 