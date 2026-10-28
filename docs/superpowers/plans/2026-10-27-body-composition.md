# 体成分(体脂率 / 内脏脂肪 / 肌肉量)Implementation Plan

> **⚠️ 已被取代（2026-10-28）**：趋势不再单独成卡，已并入「体重 / 体成分趋势」一张卡 + 指标页签；
> 体重面板的体成分行改为默认收起。执行本计划中「新增 `#bodyCompChartCard` / `initBodyCompChart`」的
> 任务前，先读 spec 末尾的「2026-10-28 调整」章节。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让智能体脂秤截图除体重外再存入 7 项体成分指标,并在首页「体重管理」面板与新增的「体成分趋势」卡上展示,状态由本系统计算。

**Architecture:** 体成分字段作为新列挂在**已有的** `type='体重记录'` 记录行上(`value=0`),与 `weight`/`bmi` 同行。录入复用现有「图片上传 → `glucose_parser.parse_glucose_input` → `/batch_add`」通道。阈值与状态分类集中在 `settings.py`(唯一来源),由 `services/dashboard_service.py` 算好随 `stats` 注入模板;趋势图数据来自前端已有的 `timelineData`,不新增接口。

**Tech Stack:** Flask 3 + SQLite/PostgreSQL 双端、Jinja2 单页模板 + Chart.js、pytest。`uv run python` / `uv run python -m pytest`。

**Spec:** `docs/superpowers/specs/2026-10-27-body-composition-design.md`

## Global Constraints

- **基线**:本计划以「品牌个性化 + 骨密度/体检」功能**合入 main 之后**的代码为准。执行前先把 worktree 重建在最新 `main` 上;**行号会漂移,一律以函数名、元素 id、区块名为锚**,不要按行号定位。
- 新增列必须**双端可迁移**:SQLite 走 `utils/db.py` 的 `mig_cols`;Postgres 走 `_init_db_postgres` 内**新增**的 `ALTER TABLE records ADD COLUMN IF NOT EXISTS ...`(`IF NOT EXISTS` 幂等)。只改 `mig_cols` 对已存在的线上库无效。
- 不新增记录类型:体成分字段一律挂在 `type='体重记录'` 的行上,`value=0`。
- **阈值只在 Python 定义一次**。前端只消费 `stats` 里的阈值,不得复制数值(见 `AGENTS.md` 关于 `getBadgeForRate()` 的 gotcha)。
- 不新增手动录入表单;体成分只走截图识别。不改 MCP(`mcp_adapter/*`)、不改 Garmin 同步、不改 `services/timeline_service.py`。
- 手机优先:三项核心指标在手机上必须一行放下,不横向滚动。
- 仓库提交规范:**一文件一 commit**;commit message 中英双语、英文在前;conventional commits 前缀。
- 测试门槛不变(`pyproject.toml` 的 `fail_under`);新增 Python 代码必须带测试。
- 字段名单一来源:`settings.BODY_COMP_FIELDS = ('body_fat_rate', 'body_fat_mass', 'muscle_mass', 'skeletal_muscle_mass', 'protein_mass', 'visceral_fat', 'basal_metabolism')`。
- 三项核心:`settings.BODY_COMP_CORE = ('body_fat_rate', 'visceral_fat', 'muscle_mass')`。

## 测试夹具事实(已核对 `tests/conftest.py`,不要凭印象写)

- `db_info` 只把 `SUGAR_BEE_DB_PATH` 指向一个**空**临时文件,**不建表**;需要表结构时自己 `init_db()`,或用 `app` / `isolate_db` fixture。
- `client_authenticated` 建出的 `_test` 用户 **id 不一定等于 1**,一律用
  `with client.session_transaction() as sess: sess['current_user_id']` 取真实 id;
  写死 `user_id=1` 会让查询查不到数据而不报错(测试假通过)。
- `_test` 用户档案为 `birth_year=1980`(2026 年 46 岁)、`gender='male'`、`height=175`、`weight=70`。
  46 岁落在体脂率「40–59」档(标准 11–21%),所以 **21.7% 会判「偏高」** ——
  Task 6 对体脂率状态的断言故意写成「属于三个标签之一」,不要收窄成「标准」。
- `get_dashboard_stats(db, user_id)` 需要 Flask app context(`get_db()` 依赖 `g`);
  插入数据与调用统计都要包在 `with app.app_context():` 里。

## Review Focus

以下五类输入/失败模式最容易出事,spec 没有逐条展开;每条在本计划其所属任务里都配了测试。

1. **秤面文字被当成数据**:秤自己显示的「标准 / 偏高 / 优秀」不能被存进库或当作状态。归属 Task 5 的测试断言解析 schema 里没有状态字段,状态一律由 `settings` 算。
2. **体脂率(%)与体脂量(kg)串位**:两个名字接近的数字互换是很自然的误读。**注意这层风险
   靠范围校验挡不住**——21.7% 写进 `body_fat_mass` 就是 21.7 kg,仍在 0.5–100 的正常区间内;
   15.2 kg 写进 `body_fat_rate` 仍在 3–60 内。Task 3 的范围校验只能捕获**明显异常值**
   (200%、900 kg、99999 kcal)。真正的防线是 **Task 5 的提示词逐条区分率/量、肌肉/骨骼肌**,
   范围校验只是兜底。
3. **截图信息不全**:只显示部分指标。归属 Task 6/7/8:缺项不补 0,该项色块与页签隐藏,核心三项全空时行与卡都不显示。
4. **同一张秤截图被重复上传**:应在现有「3 分钟内同体重即 409」上继续生效,而不是因为多了体成分字段就绕过。归属 Task 4。
5. **极端/垃圾数值**:体脂率 200%、基础代谢 99999。应**写库 + 给警告**,不能 500,也不能静默接受。归属 Task 3。

---

## File Structure

| 文件 | 职责 | 任务 |
|------|------|------|
| `utils/db.py` | 双端加列迁移(唯一 schema 变更点) | 1 |
| `models.py` | SQLAlchemy 镜像(与 db.py 同步) | 1 |
| `settings.py` | 体成分字段名单 + 三个状态分类函数(阈值唯一来源) | 2 |
| `routes/api_records.py` | 范围校验 + `/add`、`/batch_add` 落库 | 3, 4 |
| `glucose_parser.py` | 截图/文本 → 体成分字段的识别规则与 JSON schema | 5 |
| `services/dashboard_service.py` | 最新体成分 + 三个状态 + 图表阈值,注入 `stats` | 6 |
| `templates/index.html` | 体重面板折叠行 + 体成分趋势卡与图表 | 7, 8 |
| `tests/test_body_comp.py` | 体成分相关测试(新增,按任务逐步长) | 1–6 |
| `tests/test_body_comp_frontend.py` | 模板锚点与首页渲染断言(新增) | 7, 8 |

---

### Task 1: 数据库加列(SQLite + Postgres)+ models 镜像

**Files:**
- Modify: `utils/db.py`(`mig_cols` 列表;`_init_db_postgres` 的 `CREATE TABLE IF NOT EXISTS records` 与其后的 ALTER 段落)
- Modify: `models.py`(`Record` 类)
- Test: `tests/test_body_comp.py`

**Interfaces:**
- Consumes: 无。
- Produces: `records` 表新增 7 列 — `body_fat_rate REAL`、`body_fat_mass REAL`、`muscle_mass REAL`、`skeletal_muscle_mass REAL`、`protein_mass REAL`、`visceral_fat REAL`、`basal_metabolism INTEGER`。后续所有任务都依赖这些列名。

- [ ] **Step 1: Write the failing test**

在 `tests/test_body_comp.py` 新建:

```python
"""体成分(体脂率/内脏脂肪/肌肉量)的数据库、状态、校验与落库测试。"""

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
```

> `db_info` 只把 `SUGAR_BEE_DB_PATH` 指向一个空的临时文件,**不建表** —— 测试必须自己调 `init_db()`(或依赖 `app` / `isolate_db` fixture),否则 `PRAGMA table_info(records)` 永远是空集合,测试将永远失败。

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_body_comp.py -q`
Expected: FAIL — `records 表缺少列: [...]`,`Postgres 分支缺少 body_fat_rate`

- [ ] **Step 3: Implement the migration**

`utils/db.py` `mig_cols` 列表末尾追加(与现有 `("bmi", "REAL")` 同风格):

```python
("body_fat_rate", "REAL"),
("body_fat_mass", "REAL"),
("muscle_mass", "REAL"),
("skeletal_muscle_mass", "REAL"),
("protein_mass", "REAL"),
("visceral_fat", "REAL"),
("basal_metabolism", "INTEGER"),
```

`_init_db_postgres` 内:①在 `CREATE TABLE IF NOT EXISTS records (...)` 的列清单里按同样类型追加这 7 列(用 `{double_type()}` 表示 `REAL` 对应的双精度);②在该函数已有的 `ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS` 附近,新增一段对**已存在库**生效的迁移:

```python
for col_name, col_type in [
    ("body_fat_rate", "DOUBLE PRECISION"),
    ("body_fat_mass", "DOUBLE PRECISION"),
    ("muscle_mass", "DOUBLE PRECISION"),
    ("skeletal_muscle_mass", "DOUBLE PRECISION"),
    ("protein_mass", "DOUBLE PRECISION"),
    ("visceral_fat", "DOUBLE PRECISION"),
    ("basal_metabolism", "INTEGER"),
]:
    c.execute(f"ALTER TABLE records ADD COLUMN IF NOT EXISTS {col_name} {col_type}")
```

`models.py` 的 `Record` 类在 `source` 之后追加 7 个 `Column`(`REAL` → `Float`,`INTEGER` → `Integer`),与 `db.py` 一一对应。

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_body_comp.py -q`
Expected: PASS(2 passed)

- [ ] **Step 5: Commit(一文件一 commit)**

```bash
git add tests/test_body_comp.py && git commit -m "test(body-comp): assert records body composition columns after init_db

Cover the SQLite PRAGMA check and the Postgres ALTER branch so a missing
migration on either side fails loudly.

校验 init_db 后 records 表含 7 个体成分列,并确保 Postgres 分支有
ADD COLUMN IF NOT EXISTS 迁移。"
git add utils/db.py && git commit -m "feat(db): add body composition columns to records

Add body_fat_rate / body_fat_mass / muscle_mass / skeletal_muscle_mass /
protein_mass / visceral_fat / basal_metabolism to the SQLite migration
list, the Postgres CREATE TABLE, and a new idempotent Postgres
ADD COLUMN IF NOT EXISTS migration so existing production databases gain
the columns too.

records 表新增 7 个体成分列:SQLite 走 mig_cols,Postgres 补上对已有库
生效的 ADD COLUMN IF NOT EXISTS 迁移(此前 Postgres 分支只有建表,没有
加列通道)。"
git add models.py && git commit -m "chore(models): mirror body composition columns on Record

Keep the SQLAlchemy model in step with utils/db.py.

models.py 的 records 定义与 db.py 同步,补齐 7 个体成分字段。"
```

---

### Task 2: 状态分类函数(阈值唯一来源)

**Files:**
- Modify: `settings.py`(`get_bmi_category` 附近)
- Test: `tests/test_body_comp.py`

**Interfaces:**
- Consumes: 无。
- Produces:
  - `settings.BODY_COMP_FIELDS: tuple[str, ...]`
  - `settings.BODY_COMP_CORE: tuple[str, ...]`
  - `settings.BODY_FAT_BANDS: dict[str, list[tuple[int, int, float, float]]]`
  - `settings.MUSCLE_MASS_RATIO_MIN: dict[str, float]` / `MUSCLE_MASS_RATIO_EXCELLENT: dict[str, float]`
  - `settings.VISCERAL_FAT_MAX: int = 9`
  - `settings.get_body_fat_category(rate: float | None, gender: str, age: int) -> dict` → `{'label': str, 'color': str}`
  - `settings.get_visceral_fat_category(level: float | None) -> dict`
  - `settings.get_muscle_mass_category(muscle_mass: float | None, weight: float | None, gender: str) -> dict`
  - `settings.get_body_fat_upper_bound(gender: str, age: int) -> float`(给图表画上限线用)
  - `settings.get_muscle_mass_ratio_min(gender: str) -> float`

  返回结构沿用 `get_bmi_category` 的 `{'label', 'color'}`;未知一律 `{'label': '未知', 'color': '#9E9E9E'}`。

- [ ] **Step 1: Write the failing tests**

在 `tests/test_body_comp.py` 追加:

```python
import settings


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_body_comp.py -q -k "category or threshold"`
Expected: FAIL — `AttributeError: module 'settings' has no attribute 'get_body_fat_category'`

- [ ] **Step 3: Implement the functions in `settings.py`**

阈值常量(spec M1–M3):

```python
BODY_COMP_FIELDS = ('body_fat_rate', 'body_fat_mass', 'muscle_mass',
                    'skeletal_muscle_mass', 'protein_mass', 'visceral_fat', 'basal_metabolism')
BODY_COMP_CORE = ('body_fat_rate', 'visceral_fat', 'muscle_mass')

# (age_min, age_max, low_bound, high_bound):< low 偏低,在区间内 标准,> high 偏高
BODY_FAT_BANDS = {
    'male':   [(0, 39, 8.0, 19.0), (40, 59, 11.0, 21.0), (60, 200, 13.0, 24.0)],
    'female': [(0, 39, 21.0, 32.0), (40, 59, 24.0, 34.0), (60, 200, 27.0, 37.0)],
}
MUSCLE_MASS_RATIO_MIN = {'male': 0.70, 'female': 0.64}
MUSCLE_MASS_RATIO_EXCELLENT = {'male': 0.78, 'female': 0.72}
VISCERAL_FAT_MAX = 9

_UNKNOWN = {'label': '未知', 'color': '#9E9E9E'}
```

`get_body_fat_category`:按 `gender` 取 band 表(未知性别按 `male`),按 `age` 选档;`rate` 为 `None`/非正数 → `_UNKNOWN`;偏低蓝 `#42A5F5`、标准绿 `#4CAF50`、偏高橙 `#FFA726`。
`get_visceral_fat_category`:`≤9` 标准(绿)、`10–14` 偏高(橙)、`≥15` 高(红 `#EF5350`)、`None` → 未知。
`get_muscle_mass_category`:算 `ratio = muscle_mass / weight`;`weight` 或 `muscle_mass` 缺失/非正 → 未知;`ratio ≥ EXCELLENT` 优秀(绿)、`≥ MIN` 标准(绿)、否则偏低(蓝)。颜色取 `get_bmi_category` 同款色板。
`get_body_fat_upper_bound(gender, age)` 返回命中档的高界;`get_muscle_mass_ratio_min(gender)` 返回 `MUSCLE_MASS_RATIO_MIN.get(gender, MUSCLE_MASS_RATIO_MIN['male'])`。

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_body_comp.py -q`
Expected: PASS(7 passed)

- [ ] **Step 5: Commit**

```bash
git add tests/test_body_comp.py && git commit -m "test(body-comp): pin body composition status thresholds

Cover the age-banded body fat table, visceral fat bands, and the
ratio-based muscle mass classification including missing inputs.

固化体脂率按年龄分档、内脏脂肪分档、肌肉量按比例的判定边界与缺参场景。"
git add settings.py && git commit -m "feat(settings): add body composition status classifiers

Single source of truth for body fat / visceral fat / muscle mass status,
mirroring get_bmi_category. Thresholds are data, not scattered literals,
and the helpers expose the chart reference lines too.

新增体成分状态分类函数(阈值唯一来源),并暴露图表所需的参考线取值。"
```

---

### Task 3: 范围校验(写库 + 警告,不拒绝)

**Files:**
- Modify: `routes/api_records.py`(`_validate_record_data`)
- Test: `tests/test_body_comp.py`

**Interfaces:**
- Consumes: `settings.BODY_COMP_FIELDS`(Task 2)。
- Produces: `_validate_record_data(r: dict) -> list[str]` 对 7 个新字段各产出一条越界警告(沿用现有「写库但返回 warnings」语义)。

- [ ] **Step 1: Write the failing tests**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_body_comp.py -q -k validate_body_comp`
Expected: FAIL — `缺少 体脂率 的越界警告`

- [ ] **Step 3: Implement**

`_validate_record_data` 在「体重校验」之后追加(范围:体脂率 3–60 %、体脂量 0.5–100 kg、肌肉量 1–100 kg、骨骼肌量 0.5–60 kg、蛋白量 0.5–30 kg、内脏脂肪 1–30、基础代谢 500–4000 kcal),每条 `f"{中文名} {值} 超出正常范围({low}-{high})"`。缺失/`None` 不产警告。

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_body_comp.py -q`
Expected: PASS(8 passed)

- [ ] **Step 5: Commit**

```bash
git add tests/test_body_comp.py && git commit -m "test(body-comp): assert out-of-range warnings per metric

A wrong column swap (percent read as kilograms) must surface as a
warning rather than passing silently.

体成分各指标越界必须产出对应警告(尤其防止百分比与公斤数串位)。"
git add routes/api_records.py && git commit -m "feat(records): validate body composition ranges

Collect per-metric out-of-range warnings for the seven body composition
fields, keeping the existing write-then-warn behaviour.

七个字段各自的越界范围校验,沿用「写库并返回 warnings」的既有语义。"
```

---

### Task 4: `/add` 与 `/batch_add` 落库

**Files:**
- Modify: `routes/api_records.py`(`/add` 的取值与 INSERT;`/batch_add` 的 INSERT)
- Test: `tests/test_body_comp.py`

**Interfaces:**
- Consumes: `records` 新列(Task 1)、`_validate_record_data`(Task 3)、`settings.BODY_COMP_FIELDS`(Task 2)。
- Produces: `POST /add` 与 `POST /batch_add` 接受并回存 7 个体成分字段;`/add` 现有「3 分钟内同体重 → 409」行为不变。该状态后续由 Task 6 读取。

- [ ] **Step 1: Write the failing tests**

```python
def test_add_weight_record_with_body_composition(client_authenticated):
    resp = client_authenticated.post('/add', json={
        'value': 0, 'unit': 'mmol/L', 'type': '体重记录', 'timestamp': '2026-10-27 07:15:00',
        'weight': 70.45, 'body_fat_rate': 21.7, 'body_fat_mass': 15.3, 'muscle_mass': 52.3,
        'skeletal_muscle_mass': 27.0, 'protein_mass': 13.8, 'visceral_fat': 9, 'basal_metabolism': 1562,
    })
    assert resp.status_code == 200
    rid = resp.get_json()['data']['id']

    got = client_authenticated.get(f'/record/{rid}').get_json()
    assert got['body_fat_rate'] == 21.7
    assert got['muscle_mass'] == 52.3
    assert got['visceral_fat'] == 9
    assert got['basal_metabolism'] == 1562


def test_batch_add_weight_record_with_body_composition(client_authenticated):
    resp = client_authenticated.post('/batch_add', json={'records': [{
        'value': 0, 'type': '体重记录', 'datetime': '2026-10-27 07:15:00', 'weight': 70.45,
        'body_fat_rate': 21.7, 'visceral_fat': 9, 'muscle_mass': 52.3,
    }]})
    assert resp.status_code == 200
    assert resp.get_json()['data']['inserted'] == 1


def test_same_weight_within_3min_still_conflicts(client_authenticated):
    """体成分不能让既有去重失效。"""
    payload = {'value': 0, 'type': '体重记录', 'timestamp': '2026-10-27 07:15:00',
               'weight': 70.45, 'body_fat_rate': 21.7, 'visceral_fat': 9, 'muscle_mass': 52.3}
    assert client_authenticated.post('/add', json=payload).status_code == 200
    assert client_authenticated.post('/add', json=payload).status_code == 409
```

> 若 `/add` 与 `/record/<id>` 在 session 下的返回结构与此不符,以仓库实际约定为准调整断言,不要改动接口。

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_body_comp.py -q -k "with_body_composition"`
Expected: FAIL — `KeyError: 'body_fat_rate'`

- [ ] **Step 3: Implement**

`/add`:JSON 与 form 两条分支各读取 7 个字段(JSON 用 `data.get(name)`,form 用 `request.form.get(name)`);`INSERT INTO records` 的列清单、`VALUES` 占位符、参数元组各追加 7 项。**注意** `/add` 用 `RETURNING id`,占位符数量必须与列数一致。

`/batch_add`:INSERT 的列清单、占位符、参数元组各追加 7 项(`r.get(name)`)。冲突检测分支不动(体重去重已覆盖)。

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_body_comp.py -q`
Expected: PASS(11 passed)

- [ ] **Step 5: Commit**

```bash
git add tests/test_body_comp.py && git commit -m "test(body-comp): round-trip body composition through add endpoints

Verify /add and /batch_add persist every new field and that the existing
3-minute duplicate guard still fires.

验证 /add 与 /batch_add 能完整存取体成分字段,且既有 3 分钟去重仍生效。"
git add routes/api_records.py && git commit -m "feat(records): persist body composition on add and batch_add

Extend both insert paths with the seven body composition columns so a
scale screenshot's parsed payload is stored on its weight record.

/add 与 /batch_add 的 INSERT 补齐 7 个体成分列,使秤面截图解析出的数据随
体重记录一起落库。"
```

---

### Task 5: AI 识别(提示词 + JSON schema)

**Files:**
- Modify: `glucose_parser.py`(`体重识别规则` 一节后追加体成分规则;`JSON 结构要求` 追加字段)
- Test: `tests/test_body_comp.py`

**Interfaces:**
- Consumes: `settings.BODY_COMP_FIELDS`(Task 2,用于测试断言字段名一致)。
- Produces: `parse_glucose_input` 对含秤面数字的输入返回的每条记录**可含** 7 个新键;不再多返回状态类键。

- [ ] **Step 1: Write the failing tests**

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_body_comp.py -q -k "prompt or postprocess"`
Expected: FAIL — `提示词缺少 body_fat_rate`

- [ ] **Step 3: Implement**

在 `体重识别规则` 之后新增 `**体成分识别规则(智能体脂秤截图)**`:

- 识别特征:秤面/App 截图上出现的「体脂率 / 体脂 %」「体脂量」「肌肉量」「骨骼肌量 / 骨骼肌」「蛋白量 / 蛋白质」「内脏脂肪(等级)」「基础代谢 / BMR」等标签。
- 归属规则(逐条写死,防止串位):
  - `body_fat_rate`:体脂**率**,单位 %;`body_fat_mass`:体脂**量**,单位 kg。两者不是一回事。
  - `muscle_mass`:肌肉量(kg);`skeletal_muscle_mass`:骨骼肌量(kg)。肌肉量 ≠ 骨骼肌量。
  - `visceral_fat`:内脏脂肪**等级**,是 1–30 的无单位整数,**不是百分比**。
  - `basal_metabolism`:基础代谢,kcal。
- **只抄录数字,不要照抄秤面给出的「标准/偏高/优秀」等评价** —— 状态由本系统计算。
- 体成分字段一律挂在 `type="体重记录"` 同一条记录上,与 `weight` 同行。
- 示例:`"体重70.45 体脂率21.7% 体脂量15.3kg 肌肉量52.3kg 骨骼肌量27.0kg 蛋白量13.8kg 内脏脂肪9 基础代谢1562"` → 一条 `type="体重记录"`,`weight=70.45` + 7 个体成分字段。

`JSON 结构要求` 在 `"weight"` 之后追加 7 个键,每个带单位与类型注释。

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_body_comp.py -q`
Expected: PASS(13 passed)

- [ ] **Step 5: Commit**

```bash
git add tests/test_body_comp.py && git commit -m "test(body-comp): pin parser prompt fields and postprocess passthrough

Assert the prompt names every field, distinguishes rate from mass and
muscle from skeletal muscle, and never carries the scale's own verdict.

固化提示词字段、区分体脂率与体脂量/肌肉量与骨骼肌量,并确保不把秤面
自带评价写进 schema。"
git add glucose_parser.py && git commit -m "feat(parser): recognise smart scale body composition

Teach the AI parse prompt the seven body composition fields with explicit
disambiguation, and extend the JSON schema. Values are transcribed only;
status labels are left to the app.

提示词新增体成分识别规则(含易混淆项区分),JSON schema 补齐 7 个字段;
只抄录数值,状态由本系统计算。"
```

---

### Task 6: 首页统计(最新值 + 状态 + 图表阈值)

**Files:**
- Modify: `services/dashboard_service.py`(体重/BMI 段落之后)
- Test: `tests/test_body_comp.py`

**Interfaces:**
- Consumes: `settings.BODY_COMP_FIELDS` / `BODY_COMP_CORE` / 三个分类函数 + 两个阈值 helper(Task 2);`records` 新列(Task 1)。
- Produces: `stats['body_comp']` 形如

```python
{
  'has_data': bool,            # 三项核心至少一项有值
  'latest': {field: float | None, ... , 'timestamp': str | None},   # 逐字段取最新(spec M10)
  'statuses': {'body_fat_rate': {'label','color'},
               'visceral_fat':  {'label','color'},
               'muscle_mass':   {'label','color'}},
  'thresholds': {'body_fat_rate_max': float, 'visceral_fat_max': 9, 'muscle_mass_min_kg': float},
}
```

- [ ] **Step 1: Write the failing tests**

```python
def _session_user_id(client):
    """取当前登录用户 id —— `_test` 用户的 id 不一定等于 1,不能写死。"""
    with client.session_transaction() as sess:
        return sess['current_user_id']


def _insert_body_comp(app, user_id, timestamp='2026-10-27 07:15:00', weight=70.45, **over):
    """插一条体成分体重记录(schema 已由 `app` fixture 的 init_db 建好)。"""
    from utils.db import get_db
    vals = {'body_fat_rate': 21.7, 'body_fat_mass': 15.3, 'muscle_mass': 52.3,
            'skeletal_muscle_mass': 27.0, 'protein_mass': 13.8, 'visceral_fat': 9,
            'basal_metabolism': 1562}
    vals.update(over)
    cols = ", ".join(vals)
    ph = ", ".join("?" for _ in vals)
    with app.app_context():
        db = get_db()
        db.execute(
            f"INSERT INTO records (user_id, value, unit, type, timestamp, weight, {cols}) "
            f"VALUES (?, 0, 'mmol/L', '体重记录', ?, ?, {ph})",
            (user_id, timestamp, weight, *vals.values()))
        db.commit()


def test_dashboard_exposes_body_comp(app, client_authenticated):
    from services.dashboard_service import get_dashboard_stats
    uid = _session_user_id(client_authenticated)
    _insert_body_comp(app, uid)
    with app.app_context():
        from utils.db import get_db
        stats = get_dashboard_stats(get_db(), uid)
    bc = stats['body_comp']
    assert bc['has_data'] is True
    assert bc['latest']['body_fat_rate'] == 21.7
    assert bc['latest']['basal_metabolism'] == 1562
    assert bc['statuses']['body_fat_rate']['label'] in ('偏低', '标准', '偏高')
    assert bc['statuses']['visceral_fat']['label'] == '标准'
    assert bc['thresholds']['visceral_fat_max'] == 9
    assert bc['thresholds']['muscle_mass_min_kg'] == 49.3  # 0.70 × 70.45


def test_dashboard_body_comp_hidden_without_core_metrics(app, client_authenticated):
    from services.dashboard_service import get_dashboard_stats
    uid = _session_user_id(client_authenticated)
    _insert_body_comp(app, uid, body_fat_rate=None, visceral_fat=None, muscle_mass=None)
    with app.app_context():
        from utils.db import get_db
        stats = get_dashboard_stats(get_db(), uid)
    assert stats['body_comp']['has_data'] is False


def test_dashboard_body_comp_latest_is_per_field(app, client_authenticated):
    """逐字段取最新:较旧记录里的蛋白量也要能被取到(spec M10)。"""
    from services.dashboard_service import get_dashboard_stats
    uid = _session_user_id(client_authenticated)
    _insert_body_comp(app, uid, protein_mass=13.1, body_fat_rate=22.5)
    # 后插一条不完整记录,只带体脂率
    _insert_body_comp(app, uid, timestamp='2026-10-28 07:15:00', weight=70.2,
                      protein_mass=None, body_fat_rate=22.9)
    with app.app_context():
        from utils.db import get_db
        stats = get_dashboard_stats(get_db(), uid)
    assert stats['body_comp']['latest']['body_fat_rate'] == 22.9   # 取最新
    assert stats['body_comp']['latest']['protein_mass'] == 13.1    # 保留更早的值
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_body_comp.py -q -k dashboard`
Expected: FAIL — `KeyError: 'body_comp'`

- [ ] **Step 3: Implement**

在体重/BMI 段落之后:逐字段查最新值(一次 `ORDER BY timestamp DESC` 扫描或按字段各查一次,取第一个非空),组装 `latest`;`has_data = any(latest[f] is not None for f in settings.BODY_COMP_CORE)`;性别/年龄取已有 `user_config` 的 `gender` 与 `birth_year`(`age = app_now().year - (birth_year or DEFAULT_PROFILE['birth_year'])`,照抄 `calculate_bmr`);算 `statuses`;`thresholds` 用 `get_body_fat_upper_bound`、`VISCERAL_FAT_MAX`、`round(get_muscle_mass_ratio_min(gender) * latest_weight, 1)`(无体重时该键为 `None`)。把 `body_comp` 加进返回的 stats 字典。

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_body_comp.py -q`
Expected: PASS(16 passed)

- [ ] **Step 5: Commit**

```bash
git add tests/test_body_comp.py && git commit -m "test(body-comp): assert dashboard stats shape and per-field latest

Cover has_data gating on the three core metrics and the per-field latest
scan (a newer partial record must not blank an older value).

验证 stats 结构、核心三项的 has_data 门禁,以及逐字段取最新(较新的不完整
记录不得抹掉更早的值)。"
git add services/dashboard_service.py && git commit -m "feat(dashboard): expose body composition stats

Compute latest body composition values, the three status classifiers and
the chart reference thresholds, and ship them in stats so the template
consumes thresholds from one place.

在首页统计中给出最新体成分值、三个状态与图表参考阈值,阈值统一由后端下发。"
```

---

### Task 7: 体重面板内的体成分行

**Files:**
- Modify: `templates/index.html`(`weight-stats` 面板,`stats.latest_weight` 分支末尾)
- Test: `tests/test_body_comp_frontend.py`

**Interfaces:**
- Consumes: `stats.body_comp`(Task 6)。
- Produces: 元素锚点供 Task 8 使用 —— 行容器 `id="bodyCompRow"`、三项色块容器 `id="bodyCompTiles"`、更多指标行 `id="bodyCompMore"`、展开链接 `id="bodyCompExpand"`;每个色块带 `data-metric="<字段名>"`。

- [ ] **Step 1: Write the failing test**

```python
"""体成分前端锚点与首页渲染测试。"""
from pathlib import Path

INDEX = Path(__file__).resolve().parents[1] / "templates" / "index.html"


def test_weight_panel_has_body_comp_row_anchors():
    html = INDEX.read_text(encoding="utf-8")
    for anchor in ('id="bodyCompRow"', 'id="bodyCompTiles"', 'id="bodyCompMore"',
                   'id="bodyCompExpand"'):
        assert anchor in html, f"缺少 {anchor}"
    for metric in ('body_fat_rate', 'visceral_fat', 'muscle_mass'):
        assert f'data-metric="{metric}"' in html


def test_index_renders_body_comp_row_when_data_present(client_authenticated, app):
    from utils.db import get_db
    with client_authenticated.session_transaction() as sess:
        uid = sess['current_user_id']
    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO records (user_id, value, unit, type, timestamp, weight, "
                   "body_fat_rate, visceral_fat, muscle_mass) "
                   "VALUES (?, 0, 'mmol/L', '体重记录', '2026-10-27 07:15:00', 70.45, 21.7, 9, 52.3)",
                   (uid,))
        db.commit()
    html = client_authenticated.get('/').data.decode('utf-8')
    assert 'id="bodyCompRow"' in html
    assert '体成分' in html


def test_index_hides_body_comp_row_without_data(client_authenticated, app):
    """有体重但没有体成分时,体成分行不得渲染(spec M9:按核心三项判断,不是按体重判断)。"""
    from utils.db import get_db
    with client_authenticated.session_transaction() as sess:
        uid = sess['current_user_id']
    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO records (user_id, value, unit, type, timestamp, weight) "
                   "VALUES (?, 0, 'mmol/L', '体重记录', '2026-10-27 07:15:00', 70.45)", (uid,))
        db.commit()
    html = client_authenticated.get('/').data.decode('utf-8')
    assert 'id="bodyCompRow"' not in html
```

> 第三个测试要求渲染层的显示条件与 `stats.body_comp.has_data` 一致(spec M9)。

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_body_comp_frontend.py -q`
Expected: FAIL — `缺少 id="bodyCompRow"`

- [ ] **Step 3: Implement**

在 `weight-stats` 面板里 `stats.latest_weight` 分支的统计行之后,`{% if stats.body_comp and stats.body_comp.has_data %}` 包一整块:

- 虚线分隔 + 标题行 `🧬 体成分` + 右侧链接 `<a id="bodyCompExpand" href="#" onclick="...scrollIntoView...;return false;">展开查看趋势 ⌄</a>`。
- 三个色块(仅当该字段有值时渲染),各带 `data-metric`:值 + 中文名 + `stats.body_comp.statuses[字段].label`,背景/文字色取 `statuses[字段].color`(用 `{{ color }}20` 做浅底,与 BMI 徽章同款写法)。
- `id="bodyCompMore"`,`display:none`,内容为「更多指标(仅最新值):」+ 体脂量/骨骼肌量/蛋白量/基础代谢(spec M10,逐字段最新)。
- 三色块用 `col-4` 栅格,保证手机一行放下。

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_body_comp_frontend.py -q`
Expected: PASS(3 passed)

- [ ] **Step 5: Commit**

```bash
git add tests/test_body_comp_frontend.py && git commit -m "test(body-comp): assert weight panel row anchors and rendering gate

Assert the row renders only when body_comp.has_data, keeping the display
rule identical to the trend card's.

验证体成分行仅在有数据时渲染,显隐条件与趋势卡一致。"
git add templates/index.html && git commit -m "feat(ui): add body composition row to the weight panel

Show body fat rate, visceral fat and muscle mass tiles with computed
status colours, expand to the full latest-value list, and keep the three
core tiles on one row on mobile.

体重管理面板新增体成分折叠行:三项核心色块 + 状态色,展开显示更多指标最新值。"
```

---

### Task 8: 体成分趋势卡(三页签 + 时间范围 + 翻页)

**Files:**
- Modify: `templates/index.html`(`weightChartCard` 之后新增卡片;新增 `initBodyCompChart` 等函数)
- Test: `tests/test_body_comp_frontend.py`

**Interfaces:**
- Consumes: `stats.body_comp.thresholds`(Task 6)、`timelineData`(页面已有)、`ensureTimelineData`(已有)。
- Produces: 卡片 `id="bodyCompChartCard"`、画布 `id="bodyCompChart"`、页签按钮 `data-body-comp-tab="<字段名>"`、范围下拉 `#bodyCompRangeDropdown` / `#bodyCompRangeLabel`、翻页 `bodyCompNav(±1)` / `#bodyCompCalLabel` / `#bodyCompNavNext`;JS `initBodyCompChart(days = 30, offset = 0)`、`changeBodyCompRange(days)`、`selectBodyCompTab(metric)`。

- [ ] **Step 1: Write the failing tests**

```python
def test_body_comp_card_anchors_and_js():
    html = INDEX.read_text(encoding="utf-8")
    for anchor in ('id="bodyCompChartCard"', 'id="bodyCompChart"',
                   'id="bodyCompRangeDropdown"', 'id="bodyCompNavNext"',
                   'function initBodyCompChart', 'function changeBodyCompRange',
                   'function bodyCompNav', 'function selectBodyCompTab'):
        assert anchor in html, f"缺少 {anchor}"
    for metric in ('body_fat_rate', 'visceral_fat', 'muscle_mass'):
        assert f'data-body-comp-tab="{metric}"' in html


def test_body_comp_chart_reads_thresholds_from_stats():
    """阈值只能来自后端下发,不得在模板里写死数值(spec: 阈值单一来源)。"""
    html = INDEX.read_text(encoding="utf-8")
    assert 'stats.body_comp.thresholds' in html
    assert 'BODY_COMP_THRESHOLDS' in html


def test_body_comp_chart_guards_missing_canvas():
    """模块关闭或缺卡时 initBodyCompChart 必须安全返回(照 initWeightChart 的兜底)。"""
    html = INDEX.read_text(encoding="utf-8")
    body = html.split('function initBodyCompChart')[1].split('function changeBodyCompRange')[0]
    assert "getElementById('bodyCompChart')" in body
    assert 'return' in body
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_body_comp_frontend.py -q -k card`
Expected: FAIL — `缺少 id="bodyCompChartCard"`

- [ ] **Step 3: Implement**

1. 在 `weightChartCard` 的 `{% endif %}` 之后新增卡片(同样包在 `{% if 'weight' in active_modules %}` 与 `{% if stats.body_comp and stats.body_comp.has_data %}` 里),结构复制 `weightChartCard`:左侧 `bodyCompNav(-1)` + `#bodyCompCalLabel`(显示 `30d`)+ 标题「体成分趋势」+ `#bodyCompNavNext`;右侧范围下拉(7/14/30/60/90/全部)。卡内:三个页签按钮(体脂率 / 内脏脂肪 / 肌肉量,默认选中体脂率)+ `<canvas id="bodyCompChart">` + 底部「更多指标(仅最新值)」。
2. 在 `Chart.defaults` 与 `timelineData` 之后加 `const BODY_COMP_THRESHOLDS = {{ stats.body_comp.thresholds | tojson }};`(与 `GLUCOSE_TARGETS` 同款写法;`stats.body_comp` 缺失时用 `{{ (stats.body_comp.thresholds if stats.body_comp else {}) | tojson }}`)。
3. `initBodyCompChart(days = 30, offset = 0)`:照抄 `initWeightChart` 的 `if (!document.getElementById('bodyCompChart')) return;` 兜底、日期区间与 `ensureTimelineData` 懒加载、自隐(`display:none` / `''`);从 `timelineData` 收集当前页签字段非空的点(`{x: entry.timestamp, y: entry[metric]}`);Y 轴范围按当前指标取整;参考线用 `BODY_COMP_THRESHOLDS`(`body_fat_rate`/`visceral_fat` 画上限虚线,`muscle_mass` 画 `muscle_mass_min_kg` 下限虚线;该值为 `None` 时不画)。
4. `changeBodyCompRange(days)`(照抄 `changeWeightRange`,含 `days === null || days > 90` 时 `await ensureTimelineData(days)`)、`bodyCompNav(delta)`(照抄 `weightNav`)、`selectBodyCompTab(metric)`(切换选中态并重画)。

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_body_comp_frontend.py -q`
Expected: PASS(6 passed)

- [ ] **Step 5: Run the full suite**

Run: `uv run python -m pytest tests/ -q`
Expected: 全绿(基线数量 + 新增用例),无回归

- [ ] **Step 6: Commit**

```bash
git add tests/test_body_comp_frontend.py && git commit -m "test(body-comp): assert trend card anchors and threshold sourcing

Assert the card, tabs, range control and guard exist, and that the chart
reads thresholds from stats rather than hard-coded numbers.

验证趋势卡、页签、范围控件与兜底存在,且阈值取自后端下发而非写死。"
git add templates/index.html && git commit -m "feat(ui): add tabbed body composition trend card

Add a dedicated card mirroring the weight trend card: three switchable
metrics, day navigation, range dropdown, per-metric reference line, and a
latest-values footer. Data comes from the existing timeline payload.

新增体成分趋势卡:三指标页签切换、天数翻页、范围下拉、按指标的参考线,以及
更多指标最新值;数据复用现有时间线,不新增接口。"
```

---

## Self-Review

**1. Spec coverage**

| Spec 章节 | 任务 |
|---|---|
| 概述 / 8 项指标 | 1(列)、5(识别) |
| 目标:一眼可见 + 系统算状态 | 6、7 |
| 目标:独立趋势面板 + 范围/翻页 | 8 |
| 非目标(无表单/无导入/不动 MCP/Garmin) | 全局约束 |
| 反目标(不照抄秤面状态、不藏体重面板) | 5(测试)、7(在原面板内) |
| 约束:双端迁移 | 1 |
| 约束:挂在体重记录上 | 4 |
| 约束:阈值只在 Python | 2、6、8(测试断言不发散) |
| 已定方案:录入路径 / 折叠行 / 独立卡 | 4、5、7、8 |
| M1 体脂率按年龄分档(用户已定) | 2 |
| M2 内脏脂肪 | 2 |
| M3 肌肉量按比例 | 2、6(换算参考线) |
| M4 列名 | 1 |
| M5 数据来源 timelineData | 8 |
| M6 展开=滚动 | 7 |
| M7 截图不全不补 0 | 3、6、7 |
| M8 阈值下发 + 参考线方向 | 6、8 |
| M9 行与卡显示条件一致 | 7(测试) |
| M10 逐字段最新 | 6(测试) |
| 实施要点:glucose_parser / api_records / settings / dashboard | 3、4、5、6 |
| 实施要点:不改 timeline_service | 全局约束 |
| 测试清单 | 各任务 Step 1 |

无遗漏。

**2. Step scan**:每个 Step 都是单一动作(写测试 / 跑测试看失败 / 实现 / 跑测试看通过 / 提交),实现步骤给的是签名、字段名、锚点 id 与精确阈值,没有代写函数体。

**3. Type consistency**:`BODY_COMP_FIELDS` / `BODY_COMP_CORE` 在 Task 2 定义,Task 3–6 引用同一批名字;`stats['body_comp']` 的键(`has_data`/`latest`/`statuses`/`thresholds`)在 Task 6 定义,Task 7–8 引用一致;`data-metric` 与 `data-body-comp-tab` 分别用于行与卡,均取 `settings.BODY_COMP_CORE` 的字段名。

**4. Review Focus**:五条风险各自落到 Task 5(不抄秤面状态、率/量与肌肉/骨骼肌的提示词区分)、
Task 3(明显异常值的范围兜底)、Task 6+7(信息不全)、Task 4(重复上传)的测试。
「串位」只有 Task 5 挡得住,Task 3 只是兜底 —— 见 Review Focus 第 2 条。

**5. Proportion**:计划约 480 行 vs spec 约 245 行,主要是逐步 TDD 步骤与提交命令;代码块只含测试断言与常量表,无实现函数体。
