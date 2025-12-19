import google.generativeai as genai
import os
import json
import datetime
from dotenv import load_dotenv

load_dotenv()

# Configure API
api_key = os.getenv("GEMINI_API_KEY")
if api_key:
    genai.configure(api_key=api_key)

def parse_glucose_input(text):
    if not api_key:
        return {"error": "API Key not found"}

    model = genai.GenerativeModel('gemini-flash-latest') # Updated model name
    
    current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    prompt = f"""
    你是一个血糖数据分析助手。你的任务是从自然语言文本中提取血糖读数。
    
    当前参考时间: {current_time}
    
    输入文本: "{text}"
    
    指示:
    1. 识别所有血糖测量值。
    2. 提取数值（float类型）、单位（"mmol/L" 或 "mg/dL"，如果未明确说明，根据数值大小推断：如果数值通常小于30，默认"mmol/L"；如果数值通常大于30，默认"mg/dL"）、测量类型（例如："空腹", "餐前", "餐后1小时", "睡前", "运动后"等，简短中文描述）。
    3. 推断日期时间（YYYY-MM-DD HH:MM:SS格式），应基于“当前参考时间”和文本中的关键词（如“今天早上”、“昨天下午”、“三小时前”等）。如果无法推断，使用当前参考时间。
    4. 提取与测量相关的备注（例如：吃的食物、进行的运动、药物等）。
    5. 你必须只返回一个JSON数组，即使没有提取到任何数据，也返回一个空数组 `[]`。
    6. 不要包含任何 markdown 格式化（例如，不要在JSON前后使用```json）。
    7. 如果文本中包含多个血糖记录，请全部提取。
    
    JSON 结构示例:
    [
        {{
            "value": float,
            "unit": "mmol/L" 或 "mg/dL",
            "type": "string (简短中文描述，如 '空腹', '餐前', '餐后')",
            "datetime": "YYYY-MM-DD HH:MM:SS",
            "notes": "string (与测量相关的备注，中文)"
        }}
    ]
    """
    
    try:
        response = model.generate_content(prompt)
        print(f"DEBUG: Raw AI response text: {response.text}") # Debugging line
        # Clean potential markdown ```json ... ```
        clean_text = response.text.replace("```json", "").replace("```", "").strip()
        data = json.loads(clean_text)
        return data
    except Exception as e:
        print(f"Error parsing AI response: {e}")
        return []

if __name__ == "__main__":
    # Test
    print(parse_glucose_input("今天空腹6.5，中午吃了面条，餐后8.2"))
