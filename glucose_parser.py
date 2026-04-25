import json
import datetime
import re
import settings
from ai_client import call_ai


def _preprocess_relative_dates(text):
    """将相对日期（如"60天前"、"昨天"）预计算为绝对日期，避免小模型算术错误"""
    now = datetime.datetime.now()

    # "X天前" → 绝对日期
    def replace_days_ago(match):
        days = int(match.group(1))
        target = now - datetime.timedelta(days=days)
        return target.strftime('%Y年%m月%d日')

    text = re.sub(r'(\d+)\s*天前', replace_days_ago, text)
    text = re.sub(r'昨天', (now - datetime.timedelta(days=1)).strftime('%Y年%m月%d日'), text)
    text = re.sub(r'前天', (now - datetime.timedelta(days=2)).strftime('%Y年%m月%d日'), text)
    text = re.sub(r'大前天', (now - datetime.timedelta(days=3)).strftime('%Y年%m月%d日'), text)
    text = re.sub(r'上周', (now - datetime.timedelta(days=7)).strftime('%Y年%m月%d日'), text)
    text = re.sub(r'上个月', (now - datetime.timedelta(days=30)).strftime('%Y年%m月%d日'), text)
    return text


def parse_glucose_input(text, history_context=None, images_data=None, mime_type=None, user_id: int | None = None):
    current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 预处理：将相对日期转为绝对日期
    if text:
        text = _preprocess_relative_dates(text)

    context_str = "无历史数据参考，请使用通用医疗标准。"
    if history_context:
        context_str = f"""
        用户近期血糖统计（作为预测基准）：
        - 空腹平均值: {history_context.get('avg_fasting', '未知')}
        - 餐后平均值: {history_context.get('avg_postmeal', '未知')}
        - 最近一次测量: {history_context.get('last_value', '未知')} ({history_context.get('last_type', '')})
        """

    # 用户健康档案 (从配置中动态获取)
    user_profile = settings.get_ai_system_prompt(user_id)

    # User Daily Routine Context (从配置中获取)
    routine_str = settings.DAILY_ROUTINE

    # 判断是否为多张图片的同一餐情况
    num_images = len(images_data) if images_data else 0
    multi_image_note = ""
    if num_images > 1:
        multi_image_note = f"""

    **重要提示：用户上传了 {num_images} 张照片**
    这很可能是同一餐的多个菜品照片（例如：午餐的主食、蔬菜、肉类各拍了一张）。

    **累加规则**：
    1. **餐食记录**：为每张照片生成单独的餐食记录，记录具体食物内容
    2. **血糖预测**：只生成一条餐后血糖预测记录，预测值需要考虑所有照片中食物的累积效应
    3. **卡路里累加**：计算所有照片中食物的总卡路里
    4. **升糖预测**：基于所有食物的总GI和总热量，预测餐后2小时血糖
       - 如果总热量超过500kcal，或含有多种高GI食物，预测值应适当上调
       - 如果以低GI食物为主，预测值可以持平或微升

    **示例输出**（3张午餐照片：米饭、西兰花、鸡胸肉）：
    [
        {{"value": 0, "type": "午餐", "notes": "一碗白米饭（约150g）", "calories": 200, "carbs_grams": 45, "gi_value": 73, "datetime": "2024-12-31 11:30:00", "is_predicted": false}},
        {{"value": 0, "type": "午餐", "notes": "清炒西兰花（约100g）", "calories": 50, "carbs_grams": 5, "gi_value": 25, "datetime": "2024-12-31 11:30:00", "is_predicted": false}},
        {{"value": 0, "type": "午餐", "notes": "鸡胸肉（约100g）", "calories": 165, "carbs_grams": 0, "gi_value": 0, "datetime": "2024-12-31 11:30:00", "is_predicted": false}},
        {{"value": 7.8, "predicted_value": 7.8, "type": "餐后2小时", "notes": "基于午餐总热量415kcal、总碳水50g的预测", "datetime": "2024-12-31 13:30:00", "is_predicted": true}}
    ]
    """

    prompt = f"""
    你是一个血糖、血压、运动与用药数据分析助手。你的任务是从自然语言文本或图片中提取数据。

    当前录入时间: {current_time}
    {context_str}
    {user_profile}
    {routine_str}
    {multi_image_note}

    输入文本: "{text if text else '见图片内容'}"

    **核心原则：餐食记录、血糖记录、血压记录、药物记录必须分开！**
    - 餐食记录：value=0，记录食物内容和热量
    - 血糖记录：value=实际测量值，不包含食物信息
    - 血压记录：systolic_pressure和diastolic_pressure存在，记录血压数据
    - 药物记录：medication_name存在，记录药物信息

    指示:
    1. **智能识别图片内容 (如果提供了图片)**:
       - **优先参考用户的文字说明**！文字可能包含：
         - 具体时间："这是今天午餐" → 类型=午餐，时间=11:30
         - 补充信息："吃了半碗" → 调整热量估算
         - 血糖值："餐后7.2" → 额外生成实际血糖记录
         - 血压值："137/73" → 生成血压记录
         - 纠正信息："不是午餐，是早餐" → 按早餐处理

       - **情况 A：运动/健康App截图**
         - 提取：距离(km)、时长、心率(平均+最大)、配速、步频、步数、消耗卡路里。
         - **配速(pace)**：必须提取"平均配速"（如"6'30''"），不要使用"最快配速"。截图中通常有两个配速值，取标注为"平均配速"/"avg pace"的那个。
         - **最快配速(max_pace)**：如截图中有"最快配速"/"best pace"数值，提取到 max_pace 字段。
         - **最大摄氧量(VO2max)**：仅当截图中有明确标注"最大摄氧量"或"VO2max"字样的数值时才提取，合理范围 20–90 mL/kg/min。若无明确标注、或数值不在此范围内，不填此字段（设为 null）。
         - **时间**：务必提取截图中的运动开始时间。
         - 类型设为 "跑步" 或 "运动"。
         - **重要**：运动记录只记录运动数据本身，不要生成运动后血糖预测！血糖预测由系统统一处理。
       - **情况 B：食物/餐饮照片** → **必须生成2条记录！**
         - **记录1：餐食记录**
           - value = 0
           - 类型根据时间或文字说明设为："晨跑前"/"早餐"/"午餐"/"晚餐"
           - notes = 食物名称和份量
           - calories = 估算热量(kcal)
           - carbs_grams = 估算碳水化合物含量(g)
           - gi_value = 食物的升糖指数(0-100)，需要根据食物类型估算：
             - 低GI (<55): 全麦面包、糙米、豆类、大部分蔬菜、苹果
             - 中GI (55-70): 白米饭、土豆、玉米、香蕉
             - 高GI (>70): 白面包、糯米、西瓜、油条、稀饭
           - diet_analysis = 升糖指数(GI)及营养评价
           - is_predicted = false
         - **记录2：血糖记录**
           - 如果用户提供了数值：value = 用户数值, is_predicted = false
           - 如果用户未提供数值：value = 预测值, is_predicted = true
           - **predicted_value** = 始终填入你基于食物GI预测的数值（用于系统调试对比）
           - 类型 = "餐后2小时" (除非指定其他)
           - datetime = 餐食时间 + 2小时
       - **情况 C：药物/药盒照片**
         - 识别药物名称、剂量信息
         - 生成药物记录，设置 medication_name
       - **情况 D：血压计照片**
         - 识别收缩压(高压)和舒张压(低压)
         - 生成血压记录，设置 systolic_pressure 和 diastolic_pressure

    **药物识别规则**:
    - 识别关键词：服用/吃了/用了 + 药物名称（二甲双胍、阿卡波糖、胰岛素等）
    - 提取信息：
      - medication_name: 药物名称
      - medication_dosage: 剂量（如"500mg"）
      - medication_timing: 服用时机（如"餐前30分钟"）
      - medication_is_new_plan: true/false (true表示这是长期用药方案，false表示临时用药)
      - medication_action: "take"(默认,服用) / "stop"(停药/暂停/停用) / "resume"(恢复用药)
    - **停药/恢复识别规则（重要！）**:
      - 关键词：停药/停用/暂停/不吃了/不再服用/取消用药 → medication_action: "stop"
      - 关键词：恢复/重新服用/继续吃 → medication_action: "resume"
      - 停药时每种药物生成一条独立记录，每条记录的 medication_action 都是 "stop"
      - notes 中记录停药原因（如有）
    - 示例：
      - "今天开始服用二甲双胍，每天两次，早晚餐前" → medication_is_new_plan: true, medication_action: "take"
      - "刚吃了一片二甲双胍" → medication_is_new_plan: false, medication_action: "take"
      - "医生开了新药：阿卡波糖100mg，三餐餐中服用" → medication_is_new_plan: true, medication_action: "take"
      - "今天因流感停药了达格列净、二甲双胍" → 生成2条记录，每条 medication_action: "stop", notes: "因流感停药"
      - "暂停立普妥" → medication_action: "stop", notes: "暂停用药"
      - "恢复服用二甲双胍" → medication_action: "resume"

    **血压识别规则**:
    - 识别格式：137/73、"高压137低压73"、"收缩压137舒张压73"
    - **关键：A/B 格式（如125/68）一定是血压，不是血糖！其中的数字只能用作血压，严禁同时当成血糖值再做 mg/dL→mmol/L 转换！**
    - 提取信息：
      - systolic_pressure: 收缩压/高压（正常范围90-140 mmHg）
      - diastolic_pressure: 舒张压/低压（正常范围60-90 mmHg）
      - pulse_rate: 脉搏（可选，正常范围60-100 bpm）
      - spo2: 血氧饱和度（可选，正常范围95-100%）
      - type: "血压测量"、"空腹血压"、"餐后血压"等
    - 示例：
      - "早晨空腹基础血压137/73" → type="空腹血压", systolic_pressure=137, diastolic_pressure=73, datetime=今天07:15
      - "中午13:10测的124/68" → type="血压测量", systolic_pressure=124, diastolic_pressure=68, datetime=今天13:10
      - "餐后血压130/75，脉搏82" → type="餐后血压", systolic_pressure=130, diastolic_pressure=75, pulse_rate=82
      - "血压137/73 血氧98" → type="血压测量", systolic_pressure=137, diastolic_pressure=73, spo2=98
      - "血压119/70、68" → type="血压测量", systolic_pressure=119, diastolic_pressure=70, pulse_rate=68（血压后紧跟的第三个数字为脉搏/心率）

    **体重识别规则**:
    - 识别特征：包含关键词（体重/称/kg/公斤）；或者在包含血压、血糖等体征数列中，出现合理范围内的无单位数值（通常在 40.0 - 150.0 之间，且多带小数），应自动判定为体重！
    - 提取信息：
      - weight: 体重值（单位kg，合理范围30-200kg）
      - type: "体重记录"
      - value: 0（体重记录的血糖值为0）
    - BMI由系统自动计算，不需要AI填写
    - 示例：
      - "体重75kg" → type="体重记录", weight=75.0
      - "今天称了74.5" → type="体重记录", weight=74.5
      - "早空腹血压122/71、61，68.60，7.1" → 必须拆分：独立记录血压(122/71, 脉搏61)；独立记录体重(68.6)；独立血糖('空腹', 7.1)
      - "今天早空腹125/68、66，68.85" → 只有2条记录：血压(125/68, 脉搏66) + 体重(68.85)。注意：没有明确的血糖数值就不要生成血糖记录！"早空腹"是时间描述，不是"早餐"！

    **血糖预测规则（用于计算 predicted_value）**:
    - 预测值必须基于用户历史数据（空腹均值、餐后均值）合理推算
    - **累积效应**：如果用户在短时间内多次进食，预测值应适当上调
    - **低GI食物**：预测值应持平或微升0.1-0.3
    - **高GI食物**：预测值应在基准上增加0.5-1.5
    - **始终生成预测值**：无论用户是否提供了真实血糖值，你都必须计算 `predicted_value`。
      - 场景1（有真实值）：用户输入"餐后7.2"。输出: `value`: 7.2, `predicted_value`: 6.8 (你的估算), `is_predicted`: false
      - 场景2（无真实值）：用户输入"吃了面条"。输出: `value`: 8.5 (你的估算), `predicted_value`: 8.5, `is_predicted`: true

    2. **文本/语音数据识别**:
       - **关键：如果输入同时包含餐食、血糖、体征数列、药物，必须拆分成多条记录！**
       - **严禁凭空生成记录**：只对输入中明确出现的数据生成记录！没有血糖数值就不要生成血糖记录，没有食物就不要生成餐食记录。"早空腹"中的"早"是时间描述（早上），不是"早餐"。
       - **严禁重复消费数值**：一个数值只能用于一个记录。血压中的收缩压（如125）不可同时解读为 mg/dL 血糖值。
       - **无标签多维体征推断**：当用户在一句话中连续输入多个无单位数值时，务必根据数值常理范围进行独立拆分（例如："130/80 75 66.5 5.8" → 血压 130/80，脉搏 75，体重 66.5，血糖 5.8），并各自生成独立记录！
       - 例如："吃了二甲双胍后，喝了一杯酸奶，运动后血糖6.2"应拆分为：
         - 记录1：medication_name="二甲双胍", medication_is_new_plan=false
         - 记录2：type="晨跑前", value=0, notes="一杯酸奶"
         - 记录3：type="运动后", value=6.2

       - **时间推断规则**（参考用户作息时间表）：
         - "空腹" / "早空腹" -> 07:15
         - "晨跑前" / "运动前吃的" -> 07:00（餐食记录）
         - "运动后" / "运动后" / "早餐前" -> 08:45
         - "早餐" -> 09:00（餐食记录）
         - "早餐后" / "早餐后2小时" -> 11:00
         - "午餐" -> 11:30（餐食记录）
         - "午餐后" -> 13:30
         - "晚饭前" / "晚餐前" -> 17:30
         - "晚餐" -> 18:00（餐食记录）
         - "晚餐后" -> 20:00
         - "睡前" -> 22:00

    3. **记录类型分类（必须严格使用以下标准类型，不要创造新类型）**:
       - 血糖测量：'空腹', '餐前', '运动后', '餐后1小时', '餐后2小时', '晚饭前', '睡前', '运动后'
         **重要**:
         - 早餐后/午餐后/晚餐后测量 → 统一使用 '餐后2小时'（默认）或 '餐后1小时'
         - 早饭前/午饭前 → 统一使用 '餐前'
         - 晚饭前/晚餐前 → 使用 '晚饭前'（17:30 时间点）
       - 血压测量：'空腹血压', '血压测量', '餐后血压'
       - 体重记录：'体重记录'（value=0, weight字段存体重值）
       - 餐食记录：'早餐', '午餐', '晚餐', '加餐'（value=0）
       - 运动记录：'跑步', '运动'（value=0）
       - 药物记录：medication_name 不为空

    4. 日期时间 (`datetime`):
       - 图片优先：使用图片中的时间。
       - 文本推断：基于作息时间表。
       - **相对日期计算**（基于当前时间 {current_time}）：
         - "昨天" → 当前日期 - 1天
         - "前天" → 当前日期 - 2天
         - "3天前" / "三天前" → 当前日期 - 3天
         - "X天前" → 当前日期 - X天（X为任意数字，如"90天前"→当前日期-90天）
         - "上周" → 当前日期 - 7天
         - "上个月" → 当前日期 - 30天
         - 必须准确计算日期，不要默认使用今天！
       - 格式必须是 "YYYY-MM-DD HH:MM:SS"。

    JSON 结构要求:
    [
        {{
            "value": float (血糖值，若是纯运动/餐食/药物/血压记录则设为0),
            "predicted_value": float (AI基于食物和历史估算的预测值，必填，无预测则null),
            "unit": "mmol/L" 或 "mg/dL",
            "type": "string",
            "datetime": "YYYY-MM-DD HH:MM:SS",
            "notes": "string",
            "calories": int (热量kcal，餐食记录为食物热量，运动记录为消耗热量),
            "carbs_grams": float (碳水化合物含量，单位g，仅餐食记录需要),
            "gi_value": float (升糖指数0-100，仅餐食记录需要),
            "diet_analysis": "string",
            "is_predicted": boolean,
            "distance": float,
            "duration": "string",
            "heart_rate": int (平均心率，仅运动记录),
            "max_heart_rate": int (最大心率，仅运动记录，可选),
            "pace": "string (平均配速，格式必须为 X'XX\"/km，如 6'30\"/km，注意不是最快配速)",
            "max_pace": "string (最快配速，格式必须为 X'XX\"/km，如 5'45\"/km，可选)",
            "cadence": int,
            "steps": int (步数，仅运动记录，可选),
            "vo2max": float (最大摄氧量 ml/kg/min，仅运动记录，可选),
            "systolic_pressure": int (收缩压/高压，可选),
            "diastolic_pressure": int (舒张压/低压，可选),
            "pulse_rate": int (脉搏，可选),
            "spo2": int (血氧饱和度%，可选),
            "medication_name": "string (药物名称，可选)",
            "medication_dosage": "string (剂量，可选)",
            "medication_timing": "string (服用时机，可选)",
            "medication_is_new_plan": boolean (是否为新的长期用药方案),
            "medication_action": "string (take/stop/resume，默认take，可选)",
            "weight": float (体重值kg，仅体重记录需要)
        }}
    ]
    你必须只返回一个JSON数组，不要包含任何 markdown 格式化。
    """

    try:
        raw_text = call_ai(prompt, images_data=images_data, mime_type=mime_type)

        # More robust extraction
        match = re.search(r'(\[[\s\S]*\])', raw_text)
        if match:
            results = json.loads(match.group(1))
            return _postprocess_records(results, text)
        return []

    except Exception as e:
        print(f"Error parsing AI response: {e}")
        return []


def _postprocess_records(records, original_text=None):
    """后处理：修正 AI 常见的分类错误 + 兜底补漏"""
    exercise_types = {'跑步', '运动'}
    # 餐食特征字段
    meal_fields = ('carbs_grams', 'gi_value', 'diet_analysis')
    # 运动数据字段（需有实质数值才算有效）
    exercise_fields = ('distance', 'heart_rate', 'cadence', 'steps')

    for r in records:
        record_type = r.get('type', '')

        # 检测：被标为运动，但实际是餐食记录
        # 条件：有餐食特征（碳水/GI/饮食分析）且无实质运动数据
        has_meal_traits = any(r.get(f) for f in meal_fields)
        has_real_exercise_data = any(r.get(f) for f in exercise_fields)

        if record_type in exercise_types and has_meal_traits and not has_real_exercise_data:
            # 根据时间推断正确的餐食类型
            dt_str = r.get('datetime', '')
            r['type'] = _infer_meal_type(dt_str)
            # 清除无意义的运动字段
            for field in ('distance', 'duration', 'heart_rate', 'max_heart_rate',
                          'pace', 'max_pace', 'cadence', 'steps', 'vo2max'):
                if field in r:
                    r[field] = None
            print(f"[parser] 修正分类: '{record_type}' → '{r['type']}' (检测到餐食特征)")

    # 兜底：从原始文本中检测 AI 遗漏的体重数据
    if original_text:
        records = _ensure_weight_captured(records, original_text)

    return records


def _ensure_weight_captured(records, text):
    """兜底检测：如果原始文本含体重数据但 AI 未生成体重记录，自动补上"""
    # 已有体重记录则跳过
    if any(r.get('weight') or r.get('type') == '体重记录' for r in records):
        return records

    # 模式1：显式关键词 "体重68.85" / "称了74.5" / "75.2kg"
    weight_val = None
    m = re.search(r'(?:体重|称了?)\s*(\d{2,3}(?:\.\d{1,2})?)', text)
    if m:
        weight_val = float(m.group(1))
    else:
        # 模式2：末尾的 "XXkg" / "XX公斤"
        m = re.search(r'(\d{2,3}(?:\.\d{1,2})?)\s*(?:kg|公斤|千克)', text, re.IGNORECASE)
        if m:
            weight_val = float(m.group(1))

    if weight_val and 30 <= weight_val <= 200:
        # 复用已有记录的时间，没有则用当前时间
        dt = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        for r in records:
            if r.get('datetime'):
                dt = r['datetime']
                break
        records.append({
            'value': 0,
            'type': '体重记录',
            'weight': weight_val,
            'datetime': dt,
            'notes': '',
            'is_predicted': False
        })
        print(f"[parser] 兜底补漏: 从文本检测到体重 {weight_val}kg，AI 未生成，已自动补充")

    return records


def _infer_meal_type(dt_str):
    """根据时间推断餐食类型"""
    try:
        hour = int(dt_str.split(' ')[1].split(':')[0])
    except (IndexError, ValueError):
        hour = 12  # 默认按午餐处理
    if hour < 10:
        return '早餐'
    elif hour < 14:
        return '午餐'
    elif hour < 17:
        return '加餐'
    else:
        return '晚餐'

if __name__ == "__main__":
    # Test
    print(parse_glucose_input("今天空腹6.5"))
