import google.generativeai as genai
import os
import json
import datetime
import re
from dotenv import load_dotenv
import settings

load_dotenv()

# Configure API
api_key = os.getenv("GEMINI_API_KEY")
if api_key:
    genai.configure(api_key=api_key)

def parse_glucose_input(text, history_context=None, image_data=None, mime_type=None):
    if not api_key:
        print("Error: API Key not found")
        return []

    # Upgrade to the latest Gemini 3 Flash Preview (Released Dec 17, 2025)
    model = genai.GenerativeModel('gemini-3-flash-preview')
    
    current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    context_str = "无历史数据参考，请使用通用医疗标准。"
    if history_context:
        context_str = f"""
        用户近期血糖统计（作为预测基准）：
        - 空腹平均值: {history_context.get('avg_fasting', '未知')}
        - 餐后平均值: {history_context.get('avg_postmeal', '未知')}
        - 最近一次测量: {history_context.get('last_value', '未知')} ({history_context.get('last_type', '')})
        """

    # 用户健康档案 (从配置中动态获取)
    user_profile = settings.get_ai_system_prompt()
        
    # User Daily Routine Context (从配置中获取)
    routine_str = settings.DAILY_ROUTINE

    prompt = f"""
    你是一个血糖、血压、运动与用药数据分析助手。你的任务是从自然语言文本或图片中提取数据。

    当前录入时间: {current_time}
    {context_str}
    {user_profile}
    {routine_str}

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
         - 提取：距离(km)、时长、心率、配速、步频。
         - **时间**：务必提取截图中的运动开始时间。
         - 类型设为 "跑步" 或 "运动"。
       - **情况 B：食物/餐饮照片** → **必须生成2条记录！**
         - **记录1：餐食记录**
           - value = 0
           - 类型根据时间或文字说明设为："晨跑前"/"早餐"/"午餐"/"晚餐"
           - notes = 食物名称和份量
           - calories = 估算热量(kcal)
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
    - 示例：
      - "今天开始服用二甲双胍，每天两次，早晚餐前" → medication_is_new_plan: true
      - "刚吃了一片二甲双胍" → medication_is_new_plan: false
      - "医生开了新药：阿卡波糖100mg，三餐餐中服用" → medication_is_new_plan: true

    **血压识别规则**:
    - 识别格式：137/73、"高压137低压73"、"收缩压137舒张压73"
    - 提取信息：
      - systolic_pressure: 收缩压/高压（正常范围90-140 mmHg）
      - diastolic_pressure: 舒张压/低压（正常范围60-90 mmHg）
      - pulse_rate: 脉搏（可选，正常范围60-100 bpm）
      - type: "血压测量"、"空腹血压"、"餐后血压"等
    - 示例：
      - "早晨空腹基础血压137/73" → type="空腹血压", systolic_pressure=137, diastolic_pressure=73, datetime=今天07:15
      - "中午13:10测的124/68" → type="血压测量", systolic_pressure=124, diastolic_pressure=68, datetime=今天13:10
      - "餐后血压130/75，脉搏82" → type="餐后血压", systolic_pressure=130, diastolic_pressure=75, pulse_rate=82

    **血糖预测规则（用于计算 predicted_value）**:
    - 预测值必须基于用户历史数据（空腹均值、餐后均值）合理推算
    - **累积效应**：如果用户在短时间内多次进食，预测值应适当上调
    - **低GI食物**：预测值应持平或微升0.1-0.3
    - **高GI食物**：预测值应在基准上增加0.5-1.5
    - **始终生成预测值**：无论用户是否提供了真实血糖值，你都必须计算 `predicted_value`。
      - 场景1（有真实值）：用户输入"餐后7.2"。输出: `value`: 7.2, `predicted_value`: 6.8 (你的估算), `is_predicted`: false
      - 场景2（无真实值）：用户输入"吃了面条"。输出: `value`: 8.5 (你的估算), `predicted_value`: 8.5, `is_predicted`: true

    2. **文本/语音数据识别**:
       - **关键：如果输入同时包含餐食、血糖、药物，必须拆分成多条记录！**
       - 例如："吃了二甲双胍后，喝了一杯酸奶，运动后血糖6.2"应拆分为：
         - 记录1：medication_name="二甲双胍", medication_is_new_plan=false
         - 记录2：type="晨跑前", value=0, notes="一杯酸奶"
         - 记录3：type="运动后餐前", value=6.2

       - **时间推断规则**（参考用户作息时间表）：
         - "空腹" / "早空腹" -> 07:15
         - "晨跑前" / "运动前吃的" -> 07:00（餐食记录）
         - "运动后餐前" / "运动后" / "早餐前" -> 08:45
         - "早餐" -> 09:00（餐食记录）
         - "早餐后" / "早餐后2小时" -> 11:00
         - "午餐" -> 11:30（餐食记录）
         - "午餐后" -> 13:30
         - "晚餐" -> 18:00（餐食记录）
         - "晚餐后" -> 20:00
         - "睡前" -> 22:00

    3. **记录类型分类**:
       - 血糖测量：'空腹', '运动后餐前', '餐后2小时', '睡前'
       - 血压测量：'空腹血压', '血压测量', '餐后血压'
       - 餐食记录：'早餐', '午餐', '晚餐', '加餐'（value=0）
       - 运动记录：'跑步', '运动'（value=0）
       - 药物记录：medication_name 不为空

    4. 日期时间 (`datetime`):
       - 图片优先：使用图片中的时间。
       - 文本推断：基于作息时间表。
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
            "calories": int,
            "diet_analysis": "string",
            "is_predicted": boolean,
            "distance": float,
            "duration": "string",
            "heart_rate": int,
            "pace": "string",
            "cadence": int,
            "systolic_pressure": int (收缩压/高压，可选),
            "diastolic_pressure": int (舒张压/低压，可选),
            "pulse_rate": int (脉搏，可选),
            "medication_name": "string (药物名称，可选)",
            "medication_dosage": "string (剂量，可选)",
            "medication_timing": "string (服用时机，可选)",
            "medication_is_new_plan": boolean (是否为新的长期用药方案)
        }}
    ]
    你必须只返回一个JSON数组，不要包含任何 markdown 格式化。
    """
    
    try:
        content = [prompt]
        if image_data:
            content.append({"mime_type": mime_type, "data": image_data})
        else:
            content.append(text if text else "")

        response = model.generate_content(content)
        raw_text = response.text
        print(f"DEBUG: Raw AI response text: {raw_text}")
        
        # More robust extraction
        match = re.search(r'(\[[\s\S]*\])', raw_text)
        if match:
            return json.loads(match.group(1))
        return []
            
    except Exception as e:
        print(f"Error parsing AI response: {e}")
        return []

if __name__ == "__main__":
    # Test
    print(parse_glucose_input("今天空腹6.5"))
