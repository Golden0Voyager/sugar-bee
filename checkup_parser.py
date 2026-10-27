"""体检报告 AI 解析：报告照片 → 结构化指标。

体检报告格式五花八门（不同医院、不同排版），解析策略：
1. 让模型只做「抄录」不做医学判断——数值、单位、参考范围、箭头（↑/↓）按报告原文抄；
2. 归一化时用参考范围自己判正常/偏高/偏低，避免模型自由发挥；
3. 解析失败返回空列表，由前端提示用户手工补录，绝不静默丢数据。
"""
import json
import re

from ai_client import call_ai

# 常见分类，用于前端分组展示；模型识别不出的归到「其他」
CATEGORIES = ['血常规', '尿常规', '肝功能', '肾功能', '血脂', '血糖', '甲状腺', '电解质', '影像检查', '其他']

_PROMPT = """你是医疗报告抄录助手。请从这张体检报告照片中逐项抄录检查指标。

要求：
1. 只抄录报告上真实出现的内容，不要推测、不要补全、不要做医学判断；
2. 每项包含：category（分类）、name（项目名）、value（结果值，原样抄）、unit（单位）、
   ref_range（参考范围，原样抄，没有就空字符串）、status（报告上的箭头含义：normal/high/low，
   没标就按参考范围判断，判断不了填 normal）；
3. category 只能取以下之一：{categories}；
4. checkup_date 取报告上的检查日期（YYYY-MM-DD），找不到填空字符串；
5. institution 取医院/体检机构名称，找不到填空字符串。

只返回一个 JSON 对象，不要包含 markdown 代码块，格式：
{{
  "checkup_date": "YYYY-MM-DD",
  "institution": "string",
  "items": [
    {{"category": "血脂", "name": "总胆固醇", "value": "5.62", "unit": "mmol/L",
      "ref_range": "3.1-5.2", "status": "high"}}
  ]
}}"""


def _extract_json(raw_text):
    """从模型输出里抠出 JSON 对象，容忍 markdown 代码块包裹。"""
    if not raw_text:
        return None
    text = str(raw_text).strip()
    text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text, flags=re.MULTILINE).strip()
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass
    match = re.search(r'\{[\s\S]*\}', text)
    if match:
        try:
            return json.loads(match.group(0))
        except (json.JSONDecodeError, TypeError):
            return None
    return None


def _normalize_status(status, value, ref_range):
    """状态归一化：优先信任报告箭头，其次用参考范围推算。"""
    s = str(status or '').strip().lower()
    if s in ('high', 'low', 'normal'):
        return s
    return _judge_by_range(value, ref_range)


def _judge_by_range(value, ref_range):
    """按参考范围判断偏高/偏低；判断不了返回 normal。

    只取正数（化验参考范围几乎都是正数），避免把区间连字符误读成负号
    （如 "3.1-5.2" 曾被解析成 3.1 和 -5.2）。
    """
    try:
        v = float(str(value).strip())
    except (TypeError, ValueError):
        return 'normal'

    text = str(ref_range or '').strip()
    nums = re.findall(r'\d+(?:\.\d+)?', text)
    if not nums:
        return 'normal'

    # 单边参考值："<5.2" / "≤5.2" / ">1.0"
    if len(nums) == 1:
        bound = float(nums[0])
        if text.startswith(('<', '≤')):
            return 'normal' if v <= bound else 'high'
        if text.startswith(('>', '≥')):
            return 'normal' if v >= bound else 'low'
        return 'normal'

    try:
        low, high = float(nums[0]), float(nums[1])
    except (TypeError, ValueError):
        return 'normal'
    if low > high:
        low, high = high, low
    if v < low:
        return 'low'
    if v > high:
        return 'high'
    return 'normal'


def _clean_text(value, limit=50):
    return ' '.join(str(value or '').split())[:limit]


def normalize_report(data):
    """把模型输出归一化成落库结构；无有效指标时 items 为空列表。"""
    data = data if isinstance(data, dict) else {}
    raw_items = data.get('items')
    items = []
    if isinstance(raw_items, list):
        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            name = _clean_text(raw.get('name'), 40)
            value = _clean_text(raw.get('value'), 30)
            if not name or not value:
                continue
            category = _clean_text(raw.get('category'), 20) or '其他'
            if category not in CATEGORIES:
                category = '其他'
            ref_range = _clean_text(raw.get('ref_range'), 40)
            items.append({
                'category': category,
                'name': name,
                'value': value,
                'unit': _clean_text(raw.get('unit'), 20),
                'ref_range': ref_range,
                'status': _normalize_status(raw.get('status'), value, ref_range),
            })

    date = _clean_text(data.get('checkup_date'), 10)
    if not re.match(r'^\d{4}-\d{2}-\d{2}$', date):
        date = ''

    return {
        'checkup_date': date,
        'institution': _clean_text(data.get('institution'), 50),
        'items': items,
    }


def parse_checkup_report(images_data=None, mime_type=None):
    """调用视觉模型解析体检报告照片，返回归一化结构。

    AI 不可用或解析失败时抛异常，由路由层转成 503/500，避免静默返回空报告。
    """
    prompt = _PROMPT.format(categories='、'.join(CATEGORIES))
    raw_text = call_ai(prompt, images_data=images_data, mime_type=mime_type, task_type='vision')
    return normalize_report(_extract_json(raw_text))
