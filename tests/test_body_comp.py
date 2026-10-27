"""体成分(体脂率/内脏脂肪/肌肉量)的数据库、状态、校验与落库测试。"""

import settings

BODY_COMP_COLUMNS = [
    "body_fat_rate", "body_fat_mass", "muscle_mass",
    "skeletal_muscle_mass", "protein_mass", "visceral_fat", "basal_metabolism",
]


def test_records_table_has_body_comp_columns(db_info):
    """init_db 后 records 表应包含全部 7 个体成分列。"""
    import sqlite3

    from utils.db import init_db
    init_db()  # db_info 只提供临时库路径,不建表
    conn = sqlite3.connect(db_info["path"])
    cols = {row[1] for row in conn.execute("PRAGMA table_info(records)")}
    conn.close()
    missing = [c for c in BODY_COMP_COLUMNS if c not in cols]
    assert missing == [], f"records 表缺少列: {missing}"


def test_postgres_init_sql_has_body_comp_alter():
    """Postgres 分支必须含 records 的 ADD COLUMN IF NOT EXISTS 迁移(否则线上库永远缺列)。"""
    from pathlib import Path
    src = Path(__file__).resolve().parents[1].joinpath("utils/db.py").read_text(encoding="utf-8")
    pg_block = src.split("def _init_db_postgres")[1].split("\ndef _init_db_sqlite")[0]
    # 关键断言:必须是 records 的迁移。仅断言 "ADD COLUMN IF NOT EXISTS" 会被
    # 已有的 user_profiles 迁移误命中,等于没测。
    assert "ALTER TABLE records ADD COLUMN IF NOT EXISTS" in pg_block, (
        "Postgres 分支缺少 records 的 ADD COLUMN IF NOT EXISTS 迁移;"
        "只改 CREATE TABLE 对已存在的线上库无效"
    )
    for col in BODY_COMP_COLUMNS:
        assert col in pg_block, f"Postgres 分支缺少 {col}"


def test_body_fat_category_by_age_band_male():
    # 62 岁男,21.7% 落在 ≥60 档的 13–24 内 → 标准(用户已确认接受与草图的差异)
    assert settings.get_body_fat_category(21.7, "male", 62)["label"] == "标准"
    assert settings.get_body_fat_category(25.0, "male", 62)["label"] == "偏高"
    assert settings.get_body_fat_category(12.0, "male", 62)["label"] == "偏低"
    # 30 岁男用 8–19 档,所以 20% 偏高
    assert settings.get_body_fat_category(20.0, "male", 30)["label"] == "偏高"


def test_body_fat_category_female_and_unknown():
    assert settings.get_body_fat_category(25.0, "female", 30)["label"] == "标准"
    assert settings.get_body_fat_category(None, "male", 62)["label"] == "未知"
    assert settings.get_body_fat_category(21.7, "male", 62)["color"].startswith("#")


def test_visceral_fat_category():
    assert settings.get_visceral_fat_category(9)["label"] == "标准"
    assert settings.get_visceral_fat_category(10)["label"] == "偏高"
    assert settings.get_visceral_fat_category(15)["label"] == "高"
    assert settings.get_visceral_fat_category(None)["label"] == "未知"


def test_muscle_mass_category_is_ratio_based():
    # 52.3 / 70.45 = 74.2% → 标准
    assert settings.get_muscle_mass_category(52.3, 70.45, "male")["label"] == "标准"
    assert settings.get_muscle_mass_category(47.0, 70.45, "male")["label"] == "偏低"   # 66.7%
    assert settings.get_muscle_mass_category(56.0, 70.45, "male")["label"] == "优秀"   # 79.5%
    assert settings.get_muscle_mass_category(52.3, 0, "male")["label"] == "未知"
    assert settings.get_muscle_mass_category(None, 70.45, "male")["label"] == "未知"


def test_body_comp_threshold_helpers():
    assert settings.get_body_fat_upper_bound("male", 62) == 24
    assert settings.get_body_fat_upper_bound("male", 30) == 19
    assert settings.get_muscle_mass_ratio_min("male") == 0.70
    assert settings.get_muscle_mass_ratio_min("female") == 0.64


def test_validate_body_comp_ranges():
    from routes.api_records import _validate_record_data
    ok = _validate_record_data({'weight': 70.45, 'body_fat_rate': 21.7, 'visceral_fat': 9,
                                'muscle_mass': 52.3, 'basal_metabolism': 1562})
    assert ok == []

    bad = _validate_record_data({'weight': 70.45, 'body_fat_rate': 200, 'body_fat_mass': 0.1,
                                 'visceral_fat': 99, 'muscle_mass': 900,
                                 'skeletal_muscle_mass': 0.2, 'protein_mass': 99,
                                 'basal_metabolism': 99999})
    joined = "; ".join(bad)
    for field in ('体脂率', '体脂量', '内脏脂肪', '肌肉量', '骨骼肌量', '蛋白量', '基础代谢'):
        assert field in joined, f"缺少 {field} 的越界警告"


def test_add_weight_record_with_body_composition(client_authenticated):
    resp = client_authenticated.post('/add', json={
        'value': 0, 'unit': 'mmol/L', 'type': '体重记录', 'timestamp': '2026-10-27 07:15:00',
        'weight': 70.45, 'body_fat_rate': 21.7, 'body_fat_mass': 15.3, 'muscle_mass': 52.3,
        'skeletal_muscle_mass': 27.0, 'protein_mass': 13.8, 'visceral_fat': 9, 'basal_metabolism': 1562,
    })
    assert resp.status_code == 200
    rid = resp.get_json()['data']['id']

    got = client_authenticated.get(f'/record/{rid}').get_json()
    # 7 个字段逐个断言:三个相邻 REAL 列(body_fat_mass / skeletal_muscle_mass / protein_mass)
    # 过去没有落库断言,互换位置也不会有测试变红
    assert got['body_fat_rate'] == 21.7
    assert got['body_fat_mass'] == 15.3
    assert got['muscle_mass'] == 52.3
    assert got['skeletal_muscle_mass'] == 27.0
    assert got['protein_mass'] == 13.8
    assert got['visceral_fat'] == 9
    assert got['basal_metabolism'] == 1562


def test_batch_add_weight_record_with_body_composition(client_authenticated, db_info):
    resp = client_authenticated.post('/batch_add', json={'records': [{
        'value': 0, 'type': '体重记录', 'datetime': '2026-10-27 07:15:00', 'weight': 70.45,
        'body_fat_rate': 21.7, 'body_fat_mass': 15.3, 'muscle_mass': 52.3,
        'skeletal_muscle_mass': 27.0, 'protein_mass': 13.8, 'visceral_fat': 9, 'basal_metabolism': 1562,
    }]})
    assert resp.status_code == 200
    assert resp.get_json()['data']['inserted'] == 1

    # /batch_add 不回传 id，直接读回临时库确认字段真的落库(否则 INSERT 少列也能通过)。
    # 按名取 7 列成元组整体比对:只数值相等不够,还要位置对得上——
    # 把 skeletal_muscle_mass 与 protein_mass 在参数元组里互换,单看数量仍通过,这里会红。
    import sqlite3
    conn = sqlite3.connect(db_info['path'])
    try:
        row = conn.execute(
            "SELECT body_fat_rate, body_fat_mass, muscle_mass, skeletal_muscle_mass,"
            " protein_mass, visceral_fat, basal_metabolism FROM records"
        ).fetchone()
    finally:
        conn.close()
    assert row == (21.7, 15.3, 52.3, 27.0, 13.8, 9, 1562)


def test_same_weight_within_3min_still_conflicts(client_authenticated):
    """体成分不能让既有去重失效。"""
    payload = {'value': 0, 'type': '体重记录', 'timestamp': '2026-10-27 07:15:00',
               'weight': 70.45, 'body_fat_rate': 21.7, 'visceral_fat': 9, 'muscle_mass': 52.3}
    assert client_authenticated.post('/add', json=payload).status_code == 200
    assert client_authenticated.post('/add', json=payload).status_code == 409


def test_body_comp_rules_in_prompt():
    """提示词必须点名 7 个字段并强调易混淆项。"""
    import glucose_parser
    prompt = glucose_parser.build_parse_prompt() if hasattr(glucose_parser, "build_parse_prompt") else None
    if prompt is None:
        # 提示词内联在 parse_glucose_input 里时,直接读源码做锚点断言
        from pathlib import Path
        prompt = Path(glucose_parser.__file__).read_text(encoding="utf-8")
    for field in settings.BODY_COMP_FIELDS:
        assert field in prompt, f"提示词缺少 {field}"
    for phrase in ("体脂率", "体脂量", "骨骼肌量", "内脏脂肪", "基础代谢"):
        assert phrase in prompt
    # 秤面自带的状态不得进入 JSON schema(status 只能由 settings 算)
    schema = prompt.split("JSON 结构要求")[1]
    for key in ("status", "status_label", "rating"):
        assert f'"{key}"' not in schema, f"schema 不应包含 {key}"
    # 提示词必须明确「状态由本系统计算」,否则模型会把秤面评价一起抄回来
    assert "由本系统计算" in prompt


def test_parsed_body_comp_survives_postprocess():
    """_postprocess_records 不得丢弃新字段。"""
    import glucose_parser
    raw = [{
        "value": 0, "unit": "mmol/L", "type": "体重记录", "datetime": "2026-10-27 07:15:00",
        "weight": 70.45, "body_fat_rate": 21.7, "visceral_fat": 9, "muscle_mass": 52.3,
        "basal_metabolism": 1562,
    }]
    out = glucose_parser._postprocess_records(raw, "")
    assert out[0]["body_fat_rate"] == 21.7
    assert out[0]["visceral_fat"] == 9
    assert out[0]["muscle_mass"] == 52.3
