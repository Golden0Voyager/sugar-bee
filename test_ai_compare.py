"""
Gemini vs ZhipuAI 对比测试
测试内容：血糖录入解析的响应质量、速度、JSON格式合规性
"""
import json
import os
import re
import time

from dotenv import load_dotenv

load_dotenv(override=True)

# ========== 测试用例 ==========
TEST_CASES = [
    {
        'name': '简单血糖录入',
        'input': '今天空腹6.5，运动后5.8',
        'expect_count': 2,
        'expect_types': ['空腹', '运动后'],
    },
    {
        'name': '复合录入（餐食+血糖+药物）',
        'input': '午饭吃了一碗面条和一个鸡蛋，餐后2小时8.1，吃了一片二甲双胍',
        'expect_count': 3,  # 餐食 + 血糖 + 药物
        'expect_types': ['午餐', '餐后2小时'],
    },
    {
        'name': '血压+体重录入',
        'input': '早晨空腹血压134/72，脉搏64，体重69.1kg',
        'expect_count': 2,
        'expect_types': ['空腹血压', '体重记录'],
    },
    {
        'name': '中文口语化输入',
        'input': '晚饭前测了一下5.9，晚上吃的红烧肉配米饭，大概六七两',
        'expect_count': 2,  # 血糖 + 餐食（+可能的预测）
        'expect_types': ['晚饭前', '晚餐'],
    },
]

# ========== Gemini 调用 ==========
def call_gemini(prompt):
    from google import genai
    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=prompt
    )
    return response.text

# ========== ZhipuAI 调用 ==========
def call_zhipuai(prompt):
    from openai import OpenAI
    client = OpenAI(
        api_key=os.getenv("ZHIPUAI_API_KEY"),
        base_url='https://open.bigmodel.cn/api/paas/v4/'
    )
    response = client.chat.completions.create(
        model='glm-4.7-flash',
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7
    )
    return response.choices[0].message.content

# ========== 构建 Prompt ==========
def build_prompt(text):
    import datetime
    current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return f"""
    你是一个血糖、血压、运动与用药数据分析助手。从自然语言中提取数据。

    当前录入时间: {current_time}

    输入文本: "{text}"

    核心原则：餐食记录、血糖记录、血压记录、药物记录必须分开！
    - 餐食记录：value=0，记录食物内容和热量
    - 血糖记录：value=实际测量值
    - 血压记录：systolic_pressure和diastolic_pressure
    - 药物记录：medication_name存在
    - 体重记录：type="体重记录", weight=体重值, value=0

    时间推断规则：
    - "空腹" -> 07:15, "运动后" -> 08:45, "午餐" -> 11:30
    - "餐后2小时" -> 根据对应餐+2h, "晚饭前" -> 17:30, "晚餐" -> 18:00

    类型必须使用标准类型：
    - 血糖：'空腹', '餐前', '运动后', '餐后2小时', '晚饭前', '睡前'
    - 血压：'空腹血压', '血压测量', '餐后血压'
    - 体重：'体重记录'
    - 餐食：'早餐', '午餐', '晚餐', '加餐'
    - 药物：medication_name 不为空

    返回 JSON 数组，每条记录包含:
    {{"value": float, "type": "string", "datetime": "YYYY-MM-DD HH:MM:SS",
      "notes": "string", "calories": int, "is_predicted": boolean,
      "systolic_pressure": int, "diastolic_pressure": int, "pulse_rate": int,
      "medication_name": "string", "weight": float,
      "predicted_value": float, "carbs_grams": float, "gi_value": float}}

    只返回 JSON 数组，不要任何 markdown 格式化。
    """

# ========== 评估结果 ==========
def evaluate(raw_text, test_case):
    """评估 AI 返回结果的质量"""
    scores = {}

    # 1. JSON 解析
    try:
        match = re.search(r'(\[[\s\S]*\])', raw_text)
        if match:
            data = json.loads(match.group(1))
        else:
            return {'json_valid': False, 'total_score': 0, 'error': '无法提取JSON数组'}
        scores['json_valid'] = True
    except json.JSONDecodeError as e:
        return {'json_valid': False, 'total_score': 0, 'error': str(e)}

    # 2. 记录数量
    scores['record_count'] = len(data)
    scores['count_match'] = len(data) >= test_case['expect_count']

    # 3. 类型匹配
    actual_types = [r.get('type', '') for r in data]
    matched_types = sum(1 for et in test_case['expect_types'] if any(et in at for at in actual_types))
    scores['type_match_rate'] = f"{matched_types}/{len(test_case['expect_types'])}"
    scores['actual_types'] = actual_types

    # 4. 字段完整性
    required_fields = ['value', 'type', 'datetime']
    field_scores = []
    for record in data:
        present = sum(1 for f in required_fields if record.get(f) is not None)
        field_scores.append(present / len(required_fields))
    scores['field_completeness'] = f"{sum(field_scores)/len(field_scores)*100:.0f}%" if field_scores else "0%"

    # 5. datetime 格式
    datetime_ok = 0
    for record in data:
        dt = record.get('datetime', '')
        if re.match(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}', str(dt)):
            datetime_ok += 1
    scores['datetime_format'] = f"{datetime_ok}/{len(data)}"

    # 6. 综合评分 (0-100)
    total = 0
    total += 25 if scores['json_valid'] else 0
    total += 25 if scores['count_match'] else 10
    total += 25 * (matched_types / len(test_case['expect_types']))
    total += 25 * (sum(field_scores) / len(field_scores)) if field_scores else 0
    scores['total_score'] = int(total)

    return scores

# ========== 主测试 ==========
def run_test():
    print("=" * 70)
    print("  Gemini (gemini-2.5-flash) vs ZhipuAI (glm-4.7-flash) 对比测试")
    print("=" * 70)

    gemini_results = []
    zhipuai_results = []

    for i, tc in enumerate(TEST_CASES):
        print(f"\n{'─' * 60}")
        print(f"测试 {i+1}: {tc['name']}")
        print(f"输入: \"{tc['input']}\"")
        print(f"期望: {tc['expect_count']}条记录, 类型含 {tc['expect_types']}")
        print(f"{'─' * 60}")

        prompt = build_prompt(tc['input'])

        # --- Gemini ---
        print("\n  [Gemini gemini-2.5-flash]")
        try:
            t0 = time.time()
            gemini_raw = call_gemini(prompt)
            gemini_time = time.time() - t0
            gemini_eval = evaluate(gemini_raw, tc)
            gemini_eval['time'] = gemini_time
            print(f"    耗时: {gemini_time:.2f}s")
            print(f"    JSON有效: {gemini_eval.get('json_valid')}")
            print(f"    记录数: {gemini_eval.get('record_count')} (期望>={tc['expect_count']})")
            print(f"    类型匹配: {gemini_eval.get('type_match_rate')}")
            print(f"    字段完整: {gemini_eval.get('field_completeness')}")
            print(f"    时间格式: {gemini_eval.get('datetime_format')}")
            print(f"    综合评分: {gemini_eval.get('total_score')}/100")
            if gemini_eval.get('actual_types'):
                print(f"    实际类型: {gemini_eval['actual_types']}")
        except Exception as e:
            gemini_eval = {'total_score': 0, 'time': 0, 'error': str(e)}
            print(f"    错误: {e}")
        gemini_results.append(gemini_eval)

        # --- ZhipuAI ---
        print("\n  [ZhipuAI glm-4.7-flash]")
        if i > 0:
            print("    (等待10秒避免限流...)")
            time.sleep(10)
        try:
            t0 = time.time()
            zhipuai_raw = call_zhipuai(prompt)
            zhipuai_time = time.time() - t0
            zhipuai_eval = evaluate(zhipuai_raw, tc)
            zhipuai_eval['time'] = zhipuai_time
            print(f"    耗时: {zhipuai_time:.2f}s")
            print(f"    JSON有效: {zhipuai_eval.get('json_valid')}")
            print(f"    记录数: {zhipuai_eval.get('record_count')} (期望>={tc['expect_count']})")
            print(f"    类型匹配: {zhipuai_eval.get('type_match_rate')}")
            print(f"    字段完整: {zhipuai_eval.get('field_completeness')}")
            print(f"    时间格式: {zhipuai_eval.get('datetime_format')}")
            print(f"    综合评分: {zhipuai_eval.get('total_score')}/100")
            if zhipuai_eval.get('actual_types'):
                print(f"    实际类型: {zhipuai_eval['actual_types']}")
        except Exception as e:
            zhipuai_eval = {'total_score': 0, 'time': 0, 'error': str(e)}
            print(f"    错误: {e}")
        zhipuai_results.append(zhipuai_eval)

    # ========== 汇总 ==========
    print(f"\n{'=' * 70}")
    print("  汇总对比")
    print(f"{'=' * 70}")

    g_scores = [r.get('total_score', 0) for r in gemini_results]
    z_scores = [r.get('total_score', 0) for r in zhipuai_results]
    g_times = [r.get('time', 0) for r in gemini_results]
    z_times = [r.get('time', 0) for r in zhipuai_results]

    print(f"\n  {'指标':<16} {'Gemini':>12} {'ZhipuAI':>12} {'差距':>10}")
    print(f"  {'─' * 52}")
    print(f"  {'平均评分':<14} {sum(g_scores)/len(g_scores):>10.1f} {sum(z_scores)/len(z_scores):>12.1f} {sum(z_scores)/len(z_scores)-sum(g_scores)/len(g_scores):>+10.1f}")
    print(f"  {'平均耗时(s)':<13} {sum(g_times)/len(g_times):>10.2f} {sum(z_times)/len(z_times):>12.2f} {sum(z_times)/len(z_times)-sum(g_times)/len(g_times):>+10.2f}")
    print(f"  {'JSON成功率':<13} {sum(1 for r in gemini_results if r.get('json_valid'))/len(gemini_results)*100:>9.0f}% {sum(1 for r in zhipuai_results if r.get('json_valid'))/len(zhipuai_results)*100:>11.0f}%")

    for i, tc in enumerate(TEST_CASES):
        print(f"\n  测试{i+1} ({tc['name']}): Gemini={g_scores[i]}, ZhipuAI={z_scores[i]}  |  Gemini={g_times[i]:.1f}s, ZhipuAI={z_times[i]:.1f}s")

    print(f"\n{'=' * 70}")
    if sum(g_scores) > sum(z_scores):
        diff_pct = (sum(g_scores) - sum(z_scores)) / sum(g_scores) * 100
        print(f"  结论: Gemini 整体略优，领先 {diff_pct:.1f}%")
    elif sum(z_scores) > sum(g_scores):
        diff_pct = (sum(z_scores) - sum(g_scores)) / sum(z_scores) * 100
        print(f"  结论: ZhipuAI 整体略优，领先 {diff_pct:.1f}%")
    else:
        print("  结论: 两者表现相当")

    if sum(z_scores)/len(z_scores) >= 70:
        print("  ZhipuAI 可作为国内部署的可靠替代方案 ✓")
    else:
        print("  ZhipuAI 质量可能需要关注，建议增加 prompt 适配")
    print(f"{'=' * 70}")


if __name__ == '__main__':
    run_test()
