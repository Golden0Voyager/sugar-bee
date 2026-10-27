"""骨密度（bone density）领域逻辑。

骨密度检查是低频事件（半年到一年一次），数据不进 records 主表，单独存
``bone_density_records``，避免污染日常记录的时间线与统计口径。

判读口径按世界卫生组织（WHO）T 值分级，与国内骨质疏松诊疗指南一致：
- T ≥ -1.0            正常
- -2.5 < T < -1.0     骨量减少（低骨量）
- T ≤ -2.5            骨质疏松
- T ≤ -2.5 且有脆性骨折史  严重骨质疏松（本模块只标 T 值，不判定骨折史）
"""

SITES = ['腰椎 L1-L4', '髋部（全髋）', '股骨颈', '桡骨（前臂）', '全身']

# T 值分级阈值
OSTEOPOROSIS_T = -2.5
OSTEOPENIA_T = -1.0

GRADES = {
    'normal': {'label': '正常', 'color': '#16A34A', 'level': 0},
    'osteopenia': {'label': '骨量减少', 'color': '#F59E0B', 'level': 1},
    'osteoporosis': {'label': '骨质疏松', 'color': '#DC2626', 'level': 2},
}


def grade_t(t_score):
    """按 T 值返回分级信息，无效输入返回 None。"""
    if t_score is None or t_score == '':
        return None
    try:
        t = float(t_score)
    except (TypeError, ValueError):
        return None
    if t <= OSTEOPOROSIS_T:
        key = 'osteoporosis'
    elif t < OSTEOPENIA_T:
        key = 'osteopenia'
    else:
        key = 'normal'
    return {'key': key, **GRADES[key]}


def normalize_site(site):
    """把部位名归一化到 SITES 之一，无法识别时原样返回（去空白）。"""
    text = ' '.join(str(site or '').split())
    if not text:
        return SITES[0]
    for s in SITES:
        if text == s:
            return s
    # 宽松匹配：含「腰椎」「髋」「股骨」「桡骨」「全身」等关键词
    aliases = {
        '腰椎': '腰椎 L1-L4',
        '髋': '髋部（全髋）',
        '股骨': '股骨颈',
        '桡骨': '桡骨（前臂）',
        '前臂': '桡骨（前臂）',
        '全身': '全身',
    }
    for key, canonical in aliases.items():
        if key in text:
            return canonical
    return text


def build_summary(rows):
    """把骨密度记录整理成前端要用的结构：最新一次 + 趋势 + 部位分组。

    rows 为按 measure_date 升序的字典列表。
    """
    records = []
    for r in rows:
        item = dict(r)
        item['t_score'] = _to_float(item.get('t_score'))
        item['z_score'] = _to_float(item.get('z_score'))
        item['bmd'] = _to_float(item.get('bmd'))
        item['site'] = normalize_site(item.get('site'))
        item['grade'] = grade_t(item['t_score'])
        records.append(item)

    latest = _pick_latest(records)
    trend = [
        {
            'date': r['measure_date'],
            't_score': r['t_score'],
            'z_score': r['z_score'],
            'site': r['site'],
        }
        for r in records if r['t_score'] is not None
    ]
    return {
        'records': records,
        'latest': latest,
        'trend': trend,
        'sites': SITES,
        'total': len(records),
    }


def _pick_latest(records):
    """取「最近一次检查」的代表记录。

    一次 DXA 会同时测腰椎、股骨颈、全髋，诊断取**最低 T 值**那个部位，
    所以同一天有多条时选 T 值最低的，而不是最后插入的那条。
    """
    if not records:
        return None
    max_date = max(r['measure_date'] for r in records)
    same_day = [r for r in records if r['measure_date'] == max_date]
    with_t = [r for r in same_day if r['t_score'] is not None]
    if with_t:
        return min(with_t, key=lambda r: r['t_score'])
    return same_day[-1]


def _to_float(value):
    if value is None or value == '':
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def validate_payload(data):
    """校验新增记录，返回 (payload, error)。"""
    data = data or {}
    measure_date = str(data.get('measure_date') or '').strip()
    if not measure_date:
        return None, '请填写检查日期'
    if len(measure_date) > 10:
        measure_date = measure_date[:10]

    t_raw = data.get('t_score')
    t_score = _to_float(t_raw)
    if t_raw not in (None, '') and t_score is None:
        return None, 'T 值必须是数字'
    if t_score is not None and not (-10 <= t_score <= 10):
        return None, 'T 值超出合理范围（-10 ~ 10）'

    z_score = _to_float(data.get('z_score'))
    if data.get('z_score') not in (None, '') and z_score is None:
        return None, 'Z 值必须是数字'

    bmd = _to_float(data.get('bmd'))
    if data.get('bmd') not in (None, '') and bmd is None:
        return None, '骨密度值必须是数字'

    if t_score is None and bmd is None:
        return None, 'T 值或骨密度值至少填一个'

    return {
        'measure_date': measure_date,
        'site': normalize_site(data.get('site')),
        't_score': t_score,
        'z_score': z_score,
        'bmd': bmd,
        'institution': ' '.join(str(data.get('institution') or '').split())[:50],
        'notes': ' '.join(str(data.get('notes') or '').split())[:200],
    }, None
