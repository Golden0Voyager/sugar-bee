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
