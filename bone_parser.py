"""骨密度报告 AI 解析：DXA 报告图片（照片或 PDF 渲染的页）→ 结构化测量值。

一份 DXA 报告通常同时给出腰椎 L1-L4、股骨颈、全髋等多个部位的 T 值/Z 值，
所以解析结果是「一次检查 → 多条测量」，由用户勾选要保存哪些部位。

与体检解析一致的原则：模型只抄录报告上的数字，不做医学判断；
部位归一化和分级都交给 bone_density 的纯函数，便于单测。
"""
import json
import re

from ai_client import call_ai
from bone_density import SITES, normalize_site

_PROMPT = """你是医疗报告抄录助手。请从这张骨密度（DXA / 双能 X 线）报告图片中抄录测量结果。

要求：
1. 只抄录报告上真实出现的数字，不要推测、不要换算、不要给诊断结论；
2. 一份报告通常有多个部位，每个部位单独一条；
3. 部位优先归一到这几个名称之一：{sites}；报告写法不同就保留原文；
4. 没有的字段填空字符串，不要填 0；
5. 日期取报告上的检查日期（YYYY-MM-DD），找不到填空字符串。

只返回一个 JSON 对象，不要包含 markdown 代码块，格式：
{{
  "measure_date": "YYYY-MM-DD",
  "institution": "string",
  "measurements": [
    {{"site": "腰椎 L1-L4", "t_score": "-2.7", "z_score": "-1.4", "bmd": "0.782"}}
  ]
}}"""


def _extract_json(raw_text):
    """从模型输出里抠出 JSON 对象，容忍 markdown 代码块包裹。"""
    if not raw_text:
        return None
    text = re.sub(r'^```(?:json)?\s*|\s*```$', '', str(raw_text).strip(), flags=re.MULTILINE).strip()
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


def _to_float(value):
    if value is None or value == '':
        return None
    try:
        return float(str(value).replace('−', '-').replace('—', '-').strip())
    except (TypeError, ValueError):
        return None


def _clean_text(value, limit=50):
    return ' '.join(str(value or '').split())[:limit]


def normalize_report(data):
    """把模型输出归一化成「一次检查 + 多条测量」。"""
    data = data if isinstance(data, dict) else {}

    date = _clean_text(data.get('checkup_date') or data.get('measure_date'), 10)
    if not re.match(r'^\d{4}-\d{2}-\d{2}$', date):
        date = ''

    raw_items = data.get('measurements')
    if not isinstance(raw_items, list):
        raw_items = []

    measurements = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        t_score = _to_float(raw.get('t_score'))
        z_score = _to_float(raw.get('z_score'))
        bmd = _to_float(raw.get('bmd'))
        # T 值和骨密度都没有的条目视为噪声，直接丢
        if t_score is None and bmd is None:
            continue
        site = normalize_site(raw.get('site')) if raw.get('site') else SITES[0]
        measurements.append({
            'site': site,
            't_score': t_score,
            'z_score': z_score,
            'bmd': bmd,
        })

    return {
        'measure_date': date,
        'institution': _clean_text(data.get('institution'), 50),
        'measurements': measurements,
    }


def parse_bone_report(images_data=None, mime_type=None):
    """调用视觉模型解析骨密度报告，返回归一化结构。AI 失败时抛异常。"""
    prompt = _PROMPT.format(sites='、'.join(SITES))
    raw_text = call_ai(prompt, images_data=images_data, mime_type=mime_type, task_type='vision')
    return normalize_report(_extract_json(raw_text))
