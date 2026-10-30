"""数据库连接与初始化工具。

支持 SQLite（本地开发 / 测试）和 PostgreSQL（Cloud Run + Cloud SQL）双模式。
业务代码统一使用 `?` 占位符；utils/db.py 中的包装器会根据 DB_TYPE 自动转换为 `%s`。
"""
from __future__ import annotations

import contextlib
import datetime
import json
import os
import sqlite3
from typing import Any

from flask import g

import branding
from core import config
from utils.sql_dialect import (
    bool_default,
    bool_type,
    current_timestamp_default,
    double_type,
    ph,
    serial_pk_sql,
    timestamp_type,
)
from utils.timezone import app_timezone_name


def get_db_type() -> str:
    """返回当前数据库类型（'sqlite' 或 'postgres'）。"""
    return config.DB_TYPE


def _normalize_sql(sql: str, db_type: str | None = None) -> str:
    """
    统一 SQL 占位符。

    - 代码层统一写 ?
    - SQLite 目标占位符：?
    - PostgreSQL 目标占位符：%s

    只替换不在 SQL 字符串字面量内的占位符，保留 strftime('%s', ...) 等用法。
    """
    if db_type is None:
        db_type = get_db_type()
    if db_type == 'sqlite':
        source, target = '%s', '?'
    elif db_type == 'postgres':
        source, target = '?', '%s'
    else:
        return sql

    if source not in sql:
        return sql

    result = []
    i = 0
    n = len(sql)
    in_str = False
    while i < n:
        ch = sql[i]
        if ch == "'":
            # 处理转义单引号 ''
            if in_str and i + 1 < n and sql[i + 1] == "'":
                result.append("''")
                i += 2
                continue
            in_str = not in_str
            result.append(ch)
        elif not in_str and sql.startswith(source, i):
            result.append(target)
            i += len(source)
            continue
        else:
            result.append(ch)
        i += 1
    return ''.join(result)


class CursorWrapper:
    """透明转换 SQL 占位符的 cursor 包装器（SQLite 路径使用）。"""

    def __init__(self, cursor, db_type=None):
        self._cursor = cursor
        self._db_type = db_type or get_db_type()
        self._lastrowid = None

    def execute(self, sql, parameters=()):
        sql = _normalize_sql(sql, self._db_type)
        self._lastrowid = None
        result = self._cursor.execute(sql, parameters)
        if 'RETURNING' in sql.upper():
            # 先读取 RETURNING 结果，避免 SQLite 上 commit 报 "statements in progress"
            try:
                row = self._cursor.fetchone()
            except Exception:
                row = None
            if row is not None:
                if hasattr(row, 'keys') and 'id' in row:
                    self._lastrowid = row['id']
                elif hasattr(row, '__getitem__'):
                    self._lastrowid = row[0]
                else:
                    self._lastrowid = row
        else:
            self._lastrowid = getattr(self._cursor, 'lastrowid', None)
        return result

    def executemany(self, sql, parameters):
        sql = _normalize_sql(sql, self._db_type)
        return self._cursor.executemany(sql, parameters)

    @property
    def lastrowid(self):
        if self._lastrowid is not None:
            return self._lastrowid
        return getattr(self._cursor, 'lastrowid', None)

    def __iter__(self):
        return iter(self._cursor)

    def __getattr__(self, name):
        return getattr(self._cursor, name)


class ConnectionWrapper:
    """透明转换 SQL 占位符的连接包装器（SQLite 路径使用）。"""

    def __init__(self, conn, db_type=None):
        self._conn = conn
        self._db_type = db_type or get_db_type()

    def cursor(self):
        return CursorWrapper(self._conn.cursor(), self._db_type)

    def execute(self, sql, parameters=()):
        sql = _normalize_sql(sql, self._db_type)
        return self._conn.execute(sql, parameters)

    def executemany(self, sql, parameters):
        sql = _normalize_sql(sql, self._db_type)
        return self._conn.executemany(sql, parameters)

    def __enter__(self):
        return self._conn.__enter__()

    def __exit__(self, exc_type, exc_val, exc_tb):
        return self._conn.__exit__(exc_type, exc_val, exc_tb)

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def __setattr__(self, name, value):
        if name in ('_conn', '_db_type'):
            object.__setattr__(self, name, value)
        else:
            setattr(self._conn, name, value)


# PostgreSQL 相关导入（延迟到需要时，但 psycopg2 已在 requirements.txt）
try:
    from psycopg2 import pool
    from psycopg2.extras import RealDictCursor
except ImportError:  # pragma: no cover（生产环境会安装）
    pool = None
    RealDictCursor = None


_connection_pool = None


def _pg_dsn() -> str:
    """把 SQLAlchemy 风格的 URL 转换成 psycopg2 可识别的 DSN。

    SQLAlchemy 使用 postgresql+psycopg2://，而 psycopg2.connect 只需要 postgresql://。
    """
    url = config.DATABASE_URL or ""
    if url.startswith("postgresql+psycopg2://"):
        return url.replace("postgresql+psycopg2://", "postgresql://", 1)
    return url


def _get_pool():
    """获取 PostgreSQL 连接池（惰性初始化）。"""
    global _connection_pool
    if _connection_pool is None:
        if pool is None:
            raise ImportError("PostgreSQL 需要 psycopg2，请安装 requirements.txt")
        _connection_pool = pool.ThreadedConnectionPool(
            minconn=1,
            maxconn=10,
            dsn=_pg_dsn(),
            cursor_factory=RealDictCursor,
            keepalives=1,
            keepalives_idle=30,
            keepalives_interval=10,
            keepalives_count=5,
            client_encoding='UTF8',
            options=f'-c timezone={app_timezone_name()}',
        )
    return _connection_pool


def _normalize_value(value: Any) -> Any:
    """将 PostgreSQL 返回值归一化为与 SQLite 一致的 Python 类型。"""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, datetime.datetime):
        return value.isoformat(sep=' ', timespec='seconds')
    if isinstance(value, datetime.date) and not isinstance(value, datetime.datetime):
        return value.isoformat()
    return value


class _CompatRow:
    """兼容 sqlite3.Row：同时支持 row['name'] 和 row[0] 访问，并归一化值类型。"""

    def __init__(self, row: dict, columns: list[str]):
        self._row = row
        self._cols = columns

    def __getitem__(self, key):
        if isinstance(key, int):
            return _normalize_value(self._row[self._cols[key]])
        return _normalize_value(self._row[key])

    def __contains__(self, key):
        return key in self._row

    def get(self, key, default=None):
        return _normalize_value(self._row.get(key, default))

    def items(self):
        return ((k, _normalize_value(v)) for k, v in self._row.items())

    def keys(self):
        return self._row.keys()

    def values(self):
        return (_normalize_value(v) for v in self._row.values())


def _inline_params(sql: str, params: tuple) -> str:
    """用 psycopg2 的 adapt() 将参数直接嵌入 SQL，绕过 C 扩展的 %s 处理。

    psycopg2-binary 2.9.12 的 C 扩展在 SQL 包含中文 LIKE 模式时
    （如 ``LIKE '%餐后2小时%'``）会因 `pq_escape_placeholders` 内部
    索引越界而抛 `IndexError`。将参数直接替换为转义后的值，零参数传递。
    """
    from psycopg2.extensions import adapt
    parts = sql.split('?')
    result = parts[0]
    for i, param in enumerate(params):
        if isinstance(param, str):
            # 手动处理字符串：转义单引号 + UTF-8 编码，避免 latin-1 编码错误
            escaped = param.replace("'", "''")
            quoted = "'" + escaped + "'"
        else:
            quoted = adapt(param).getquoted()
            if isinstance(quoted, bytes):
                quoted = quoted.decode('utf-8')
        result += quoted + parts[i + 1]
    return result


def _convert_sqlite_to_pg(sql: str) -> str:
    """将内联后的 SQL 中的 SQLite 特有函数转换为 PostgreSQL 兼容语法。

    该函数在 _inline_params 之后调用，此时 ? 已替换为内联字面量。
    """
    import re

    # 1. DATE(column) → column::date
    sql = re.sub(r"(?i)\bDATE\(([\w.]+)\)", r'\1::date', sql)

    # 2. datetime('now', ['localtime',] '-N unit') → NOW() - INTERVAL 'N unit'
    #    datetime('now', ['localtime',] '+N unit') → NOW() + INTERVAL 'N unit'
    #    datetime(expr, '-N unit') → expr::timestamp - INTERVAL 'N unit'
    #    datetime(expr, '+N unit') → expr::timestamp + INTERVAL 'N unit'
    def _replace_now_dt(m):
        modifier = m.group(1).strip()
        op = '+' if modifier.startswith('+') else '-'
        number, unit = modifier[1:].strip().split(' ', 1)
        return f"NOW() {op} INTERVAL '{number} {unit}'"

    sql = re.sub(
        r"(?i)datetime\(\s*'now'\s*,\s*(?:'localtime'\s*,\s*)?'([+-]\d+\s+\w+)'\s*\)",
        _replace_now_dt,
        sql,
    )

    def _replace_dt(m):
        expr = m.group(1).strip()
        # Trim surrounding quotes if present on the expr (inline after ? → str literal)
        if expr.startswith("'") and expr.endswith("'"):
            expr = expr[1:-1]  # pragma: no cover (dead code — regex 已剥离引号)
        modifier = m.group(2).strip()
        op = '+' if modifier.startswith('+') else '-'
        number, unit = modifier[1:].strip().split(' ', 1)
        return f"'{expr}'::timestamp {op} INTERVAL '{number} {unit}'"

    sql = re.sub(
        r"(?i)datetime\(\s*'([^']+)'\s*,\s*'([+-]\d+\s+\w+)'\s*\)",
        _replace_dt,
        sql,
    )

    # 3. datetime('now') / datetime('now', 'localtime') → NOW()
    sql = re.sub(r"(?i)datetime\(\s*'now'\s*(?:,\s*'localtime'\s*)?\)", 'NOW()', sql)

    return sql


class _CompatCursor:
    """包装 psycopg2 RealDictCursor，返回 _CompatRow 并处理占位符转换。"""

    def __init__(self, cursor):
        self._cursor = cursor
        self._lastrowid = None

    def execute(self, sql, parameters=None):
        self._lastrowid = None
        if parameters is None:
            parameters = ()
        if config.DB_TYPE == 'postgres' and parameters:
            sql = _inline_params(sql, parameters)
            parameters = None
        if config.DB_TYPE == 'postgres':
            sql = _convert_sqlite_to_pg(sql)
        sql = _normalize_sql(sql, config.DB_TYPE)
        result = self._cursor.execute(sql, parameters)
        if 'RETURNING' in sql.upper():
            try:
                row = self._cursor.fetchone()
                if row is not None:
                    cols = [d[0] for d in self._cursor.description]
                    self._lastrowid = _CompatRow(row, cols)['id']
            except Exception:
                pass
        return result

    def executemany(self, sql, parameters):
        sql = _normalize_sql(sql, config.DB_TYPE)
        return self._cursor.executemany(sql, parameters)

    def fetchone(self):
        row = self._cursor.fetchone()
        if row is None:
            return None
        cols = [d[0] for d in self._cursor.description]
        return _CompatRow(row, cols)

    def fetchall(self):
        rows = self._cursor.fetchall()
        if not rows:
            return []
        cols = [d[0] for d in self._cursor.description]
        return [_CompatRow(r, cols) for r in rows]

    @property
    def lastrowid(self):
        if self._lastrowid is not None:
            return self._lastrowid
        return getattr(self._cursor, 'lastrowid', None)

    def __iter__(self):
        return iter(self._cursor)

    def __getattr__(self, name):
        return getattr(self._cursor, name)


class _PgConnectionWrapper:
    """PostgreSQL 连接包装器：cursor() 返回 _CompatCursor，close() 将连接归还连接池。"""

    def __init__(self, conn, pool_obj):
        self._conn = conn
        self._pool = pool_obj

    def cursor(self):
        return _CompatCursor(self._conn.cursor())

    def execute(self, sql, parameters=()):
        cur = self.cursor()
        cur.execute(sql, parameters)
        return cur

    def executemany(self, sql, parameters):
        cur = self.cursor()
        return cur.executemany(sql, parameters)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._pool.putconn(self._conn)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self.rollback()
        else:
            self.commit()
        self.close()
        return False


# ========== SQLite 并发参数（WAL + 等锁）==========
# 同一时刻有三个写入口在写同一个库文件：web 请求、gunicorn master 的后台线程、
# 外部 MCP 进程。默认的 rollback journal 下读会挡住写，撞锁直接抛
# "database is locked"（sqlite3 默认只等 5 秒）——MCP 批量写撞上就整批失败，
# 调用方不重试 = 记录静默丢失。
#
#   journal_mode=WAL  读写互不阻塞（写还是串行，但读不再挡写），是多方并发的前提
#   busy_timeout      撞锁时排队等，而不是立刻报错（默认 15 秒，可环境变量调）
#   synchronous=NORMAL WAL 下的推荐值：掉电最多丢最近几个事务，不会损库
#
# 注意：journal_mode 是**写进库文件头**的持久设置，设一次就生效；
# 但每次连接都设一遍才安全（历史库第一次连接时才会被切过去）。
SQLITE_BUSY_TIMEOUT_MS = int(os.environ.get('SUGAR_BEE_BUSY_TIMEOUT_MS', '15000'))


def apply_sqlite_pragmas(conn, read_only=False):
    """给连接套上 WAL + 等锁参数。只读连接不能改 journal_mode，跳过。"""
    conn.execute(f'PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}')
    if not read_only:
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA synchronous=NORMAL')
    return conn


def sqlite_connect(db_path=None, timeout=30, read_only=False):
    """SQLite 统一连接入口（全仓唯一）：自动带 WAL + busy_timeout。

    db_path 为 None 时用当前配置的库路径。read_only=True 时用 mode=ro 打开，
    供只读分析 / 备份使用。
    """
    path = db_path or config.get_db_name()
    if read_only:
        conn = sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=timeout)
    else:
        conn = sqlite3.connect(path, timeout=timeout)
    return apply_sqlite_pragmas(conn, read_only=read_only)


def get_db():
    """
    获取数据库连接。

    Flask 应用上下文中连接缓存在 g 对象；PostgreSQL 使用连接池，
    SQLite 使用 sqlite3 连接。返回的连接统一使用 `?` 占位符。
    """
    db = getattr(g, '_database', None)
    if db is None:
        if config.DB_TYPE == 'postgres':
            pool_obj = _get_pool()
            conn = pool_obj.getconn()
            db = g._database = _PgConnectionWrapper(conn, pool_obj)
        else:
            raw_conn = sqlite_connect()
            raw_conn.row_factory = sqlite3.Row
            db = g._database = ConnectionWrapper(raw_conn)
    return db


def get_raw_conn():
    """获取一个不受 Flask g 管理的数据库连接，供后台线程使用。调用方须用 put_raw_conn 归还。"""
    if config.DB_TYPE == 'postgres':
        pool_obj = _get_pool()
        conn = pool_obj.getconn()
        return _PgConnectionWrapper(conn, pool_obj)
    conn = sqlite_connect()
    conn.row_factory = sqlite3.Row
    return ConnectionWrapper(conn)


def put_raw_conn(conn):
    """归还有 get_raw_conn 获取的连接（PostgreSQL 回池，SQLite 关闭）。"""
    conn.close()


def close_db(exception=None):
    """关闭当前请求上下文中的数据库连接。"""
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()
        g._database = None


def init_db():
    """初始化数据库表，按 DB_TYPE 自动选择 SQLite 或 PostgreSQL 分支。"""
    if config.DB_TYPE == 'postgres':
        _init_db_postgres()
    else:
        _init_db_sqlite()


# 一次性数据迁移的标记名（写进 app_migrations 表，确保只跑一次）
MIGRATION_NEW_MODULES = 'backfill_new_modules_v1'
# 这些模块是「模块白名单」扩容后新增的，老账号的 JSON 里不会有
BACKFILL_MODULES = ('bone_density', 'checkup')


def _apply_module_backfill(c):
    """给老账号的模块白名单补上新增模块（骨密度 / 体检），只执行一次。

    ``enabled_modules`` 为空表示「全部启用」，无需处理；非空才是白名单。
    老账号的白名单是升级前存的，里面没有新增模块，不补的话升级后看不到新功能，
    用户会以为功能丢了。

    用 ``app_migrations`` 记一笔而不是每次启动都跑，否则用户主动关掉
    骨密度后，下次重启又会被打开。
    """
    c.execute('CREATE TABLE IF NOT EXISTS app_migrations (name TEXT PRIMARY KEY, applied_at TEXT)')
    if c.execute(
        f"SELECT 1 FROM app_migrations WHERE name = '{MIGRATION_NEW_MODULES}'"
    ).fetchone():
        return

    rows = c.execute('SELECT user_id, enabled_modules FROM user_profiles').fetchall()
    for row in rows:
        user_id, raw = row[0], row[1]
        modules = branding.normalize_modules(raw)
        if not modules:
            # 空 = 全部启用，或数据损坏 → 保持原样，不动
            continue
        added = [m for m in BACKFILL_MODULES if m not in modules]
        if not added:
            continue
        merged = modules + added
        placeholder = ph()
        c.execute(
            f'UPDATE user_profiles SET enabled_modules = {placeholder} '
            f'WHERE user_id = {placeholder}',
            (json.dumps(merged), user_id),
        )

    c.execute(
        f"INSERT INTO app_migrations (name, applied_at) VALUES "
        f"('{MIGRATION_NEW_MODULES}', CURRENT_TIMESTAMP)"
    )


MIGRATION_DROP_STRAY_PREDICTIONS = 'drop_stray_predictions_v1'


def _apply_prediction_cleanup(c):
    """删掉「没开血糖模块」的用户名下由 AI 预测生成的血糖记录（只执行一次）。

    历史 bug：首页每次加载都会给当前用户跑空腹血糖预测，却没判断血糖模块是否开启，
    于是给不测血糖的家人凭空插了一条「空腹 6.x mmol/L」，还带一段 AI 解释。

    只删 ``is_predicted = 1`` 的记录——这些全部由 AI 生成，用户从未录入；
    用户自己的数据（包括真实血糖）一条都不动。
    """
    c.execute('CREATE TABLE IF NOT EXISTS app_migrations (name TEXT PRIMARY KEY, applied_at TEXT)')
    if c.execute(
        f"SELECT 1 FROM app_migrations WHERE name = '{MIGRATION_DROP_STRAY_PREDICTIONS}'"
    ).fetchone():
        return

    placeholder = ph()
    removed = 0
    for row in c.execute('SELECT user_id, enabled_modules FROM user_profiles').fetchall():
        user_id, raw = row[0], row[1]
        modules = branding.normalize_modules(raw)
        # 空列表＝全部启用，包含血糖，不动
        if not modules or 'glucose' in modules:
            continue
        cur = c.execute(
            f'DELETE FROM records WHERE user_id = {placeholder} AND is_predicted = 1',
            (user_id,),
        )
        removed += cur.rowcount or 0

    c.execute(
        f"INSERT INTO app_migrations (name, applied_at) VALUES "
        f"('{MIGRATION_DROP_STRAY_PREDICTIONS}', CURRENT_TIMESTAMP)"
    )
    if removed:
        print(f'[migrate] 已清理非血糖用户的 AI 预测记录 {removed} 条')


MIGRATION_LINK_STALE_PREDICTIONS = 'link_stale_predictions_v1'


def _apply_stale_prediction_link(c):
    """给「预测值与实测值完全相同」的历史预测补标已验证（只执行一次）。

    历史遗留：预测验证机制上线前的旧预测从没被标记，实测录入后界面上
    同一天同一时间会并排显示两条一样的数值。前端会把「已验证预测」折叠，
    所以补标 verified_by_real_id 后这些重复条目就不再显示。
    只 UPDATE is_predicted = 1 的行，实测记录一条不动。
    """
    c.execute('CREATE TABLE IF NOT EXISTS app_migrations (name TEXT PRIMARY KEY, applied_at TEXT)')
    if c.execute(
        f"SELECT 1 FROM app_migrations WHERE name = '{MIGRATION_LINK_STALE_PREDICTIONS}'"
    ).fetchone():
        return

    c.execute("""
        UPDATE records SET
            verified_by_real_id = (
                SELECT MIN(r.id) FROM records r
                WHERE r.user_id = records.user_id
                  AND r.timestamp = records.timestamp
                  AND r.value = records.value
                  AND (r.is_predicted = 0 OR r.is_predicted IS NULL)
            ),
            prediction_error = 0
        WHERE is_predicted = 1 AND verified_by_real_id IS NULL
          AND EXISTS (
              SELECT 1 FROM records r
              WHERE r.user_id = records.user_id
                AND r.timestamp = records.timestamp
                AND r.value = records.value
                AND (r.is_predicted = 0 OR r.is_predicted IS NULL)
          )
    """)

    c.execute(
        f"INSERT INTO app_migrations (name, applied_at) VALUES "
        f"('{MIGRATION_LINK_STALE_PREDICTIONS}', CURRENT_TIMESTAMP)"
    )


def _init_db_postgres():
    """PostgreSQL schema 初始化（一次性创建完整表结构）。"""
    import psycopg2

    conn = psycopg2.connect(
        _pg_dsn(),
        client_encoding='UTF8',
        options=f'-c timezone={app_timezone_name()}',
    )
    try:
        c = conn.cursor()

        c.execute(f'''
            CREATE TABLE IF NOT EXISTS records (
                id {serial_pk_sql()},
                value {double_type()},
                unit TEXT,
                type TEXT,
                notes TEXT,
                timestamp {timestamp_type()},
                created_at {timestamp_type()} {current_timestamp_default()},
                calories INTEGER DEFAULT 0,
                diet_analysis TEXT DEFAULT '',
                is_predicted {bool_type()} {bool_default(False)},
                distance {double_type()},
                duration TEXT,
                heart_rate INTEGER,
                pace TEXT,
                cadence INTEGER,
                systolic_pressure INTEGER,
                diastolic_pressure INTEGER,
                pulse_rate INTEGER,
                spo2 INTEGER,
                weight {double_type()},
                bmi {double_type()},
                verified_by_real_id INTEGER,
                prediction_error {double_type()},
                carbs_grams {double_type()},
                gi_value {double_type()},
                medication_name TEXT,
                vo2max {double_type()},
                max_heart_rate INTEGER,
                steps INTEGER,
                max_pace TEXT,
                user_id INTEGER DEFAULT 1,
                external_id TEXT,
                source TEXT,
                body_fat_rate {double_type()},
                body_fat_mass {double_type()},
                muscle_mass {double_type()},
                skeletal_muscle_mass {double_type()},
                protein_mass {double_type()},
                visceral_fat {double_type()},
                basal_metabolism INTEGER,
                total_sets INTEGER,
                total_reps INTEGER,
                training_load {double_type()},
                intensity_minutes INTEGER,
                te_aerobic {double_type()},
                te_anaerobic {double_type()},
                elevation_gain {double_type()}
            )
        ''')

        c.execute(f'''
            CREATE TABLE IF NOT EXISTS medication_plans (
                id {serial_pk_sql()},
                medication_name TEXT NOT NULL,
                dosage TEXT,
                times_per_day INTEGER DEFAULT 1,
                timing_notes TEXT,
                start_date DATE NOT NULL,
                end_date DATE,
                is_active {bool_type()} {bool_default(True)},
                notes TEXT,
                created_at {timestamp_type()} {current_timestamp_default()},
                user_id INTEGER DEFAULT 1,
                frequency TEXT DEFAULT 'daily',
                frequency_detail TEXT,
                category TEXT DEFAULT 'long_term',
                dose_quantity TEXT DEFAULT '1',
                dose_unit TEXT DEFAULT '片',
                med_type TEXT DEFAULT ''
            )
        ''')

        c.execute(f'''
            CREATE TABLE IF NOT EXISTS dosage_history (
                id {serial_pk_sql()},
                plan_id INTEGER NOT NULL,
                old_dosage TEXT,
                new_dosage TEXT,
                changed_at {timestamp_type()} {current_timestamp_default()},
                FOREIGN KEY (plan_id) REFERENCES medication_plans(id)
            )
        ''')

        c.execute(f'''
            CREATE TABLE IF NOT EXISTS medication_logs (
                id {serial_pk_sql()},
                plan_id INTEGER NOT NULL,
                log_date DATE NOT NULL,
                timestamp {timestamp_type()} NOT NULL,
                taken {bool_type()} {bool_default(True)},
                notes TEXT,
                created_at {timestamp_type()} {current_timestamp_default()},
                user_id INTEGER DEFAULT 1,
                FOREIGN KEY (plan_id) REFERENCES medication_plans(id)
            )
        ''')

        c.execute(f'''
            CREATE TABLE IF NOT EXISTS health_analyses (
                id {serial_pk_sql()},
                analysis_date DATE NOT NULL,
                health_score INTEGER,
                glucose_summary TEXT,
                blood_pressure_summary TEXT,
                exercise_summary TEXT,
                medication_summary TEXT,
                recommendations TEXT,
                full_analysis TEXT,
                is_auto_generated {bool_type()} {bool_default(False)},
                created_at {timestamp_type()} {current_timestamp_default()},
                user_id INTEGER DEFAULT 1,
                days INTEGER DEFAULT 7
            )
        ''')

        c.execute(f'''
            CREATE TABLE IF NOT EXISTS app_users (
                id {serial_pk_sql()},
                username TEXT UNIQUE NOT NULL,
                display_name TEXT NOT NULL,
                avatar TEXT,
                is_active {bool_type()} {bool_default(True)},
                created_at {timestamp_type()} {current_timestamp_default()},
                password_hash TEXT,
                phone TEXT,
                email TEXT
            )
        ''')

        # Garmin 绑定表：每个用户一份 Garmin 账号绑定，token 按用户分目录存储
        c.execute(f'''
            CREATE TABLE IF NOT EXISTS garmin_bindings (
                user_id INTEGER PRIMARY KEY REFERENCES app_users(id),
                email TEXT NOT NULL,
                is_cn {bool_type()} {bool_default(False)},
                created_at {timestamp_type()} {current_timestamp_default()}
            )
        ''')

        c.execute(f'''
            CREATE TABLE IF NOT EXISTS user_profiles (
                user_id INTEGER PRIMARY KEY REFERENCES app_users(id),
                name TEXT,
                birth_year INTEGER,
                height INTEGER,
                weight INTEGER,
                gender TEXT,
                default_meals TEXT,
                target_ranges TEXT,
                enabled_modules TEXT,
                created_at {timestamp_type()} {current_timestamp_default()},
                updated_at {timestamp_type()} {current_timestamp_default()},
                target_weight {double_type()},
                birth_month INTEGER,
                birth_day INTEGER,
                app_name TEXT,
                app_emoji TEXT,
                app_tagline TEXT,
                persona TEXT,
                garmin_sync_days INTEGER DEFAULT 30
            )
        ''')

        # 品牌个性化字段：老库补列（PostgreSQL 9.6+ 支持 ADD COLUMN IF NOT EXISTS）
        for col_name in ('app_name', 'app_emoji', 'app_tagline', 'persona'):
            with contextlib.suppress(Exception):
                c.execute(f'ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS {col_name} TEXT')

        # Garmin 同步天数（每个用户可自定义，30~240）
        with contextlib.suppress(Exception):
            c.execute('ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS garmin_sync_days INTEGER DEFAULT 30')

        # 体成分字段:对已存在的线上库补列(CREATE TABLE IF NOT EXISTS 对老库无效)
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

        # 训练负荷字段（力量训练：组数/次数/训练负荷/强度分钟/有氧无氧效果）
        for col_name, col_type in [
            ("total_sets", "INTEGER"),
            ("total_reps", "INTEGER"),
            ("training_load", "DOUBLE PRECISION"),
            ("intensity_minutes", "INTEGER"),
            ("te_aerobic", "DOUBLE PRECISION"),
            ("te_anaerobic", "DOUBLE PRECISION"),
            ("elevation_gain", "DOUBLE PRECISION"),
        ]:
            c.execute(f"ALTER TABLE records ADD COLUMN IF NOT EXISTS {col_name} {col_type}")

        c.execute(f'''
            CREATE TABLE IF NOT EXISTS chat_messages (
                id {serial_pk_sql()},
                user_id INTEGER NOT NULL,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at {timestamp_type()} {current_timestamp_default()}
            )
        ''')
        c.execute('CREATE INDEX IF NOT EXISTS idx_chat_user_session ON chat_messages(user_id, session_id, created_at)')

        c.execute(f'''
            CREATE TABLE IF NOT EXISTS user_auth_providers (
                id {serial_pk_sql()},
                user_id INTEGER NOT NULL,
                provider TEXT NOT NULL,
                provider_uid TEXT NOT NULL,
                verified {bool_type()} {bool_default(False)},
                created_at {timestamp_type()} {current_timestamp_default()},
                FOREIGN KEY (user_id) REFERENCES app_users(id)
            )
        ''')
        c.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_auth_provider_uid ON user_auth_providers(provider, provider_uid)')

        # 骨密度检查记录（低频检查，独立于 records 主表）
        c.execute(f'''
            CREATE TABLE IF NOT EXISTS bone_density_records (
                id {serial_pk_sql()},
                user_id INTEGER NOT NULL,
                measure_date TEXT NOT NULL,
                site TEXT,
                t_score {double_type()},
                z_score {double_type()},
                bmd {double_type()},
                institution TEXT,
                notes TEXT,
                created_at {timestamp_type()} {current_timestamp_default()}
            )
        ''')
        c.execute('CREATE INDEX IF NOT EXISTS idx_bone_user_date ON bone_density_records(user_id, measure_date)')

        # 体检报告（一次体检一份）+ 明细指标
        c.execute(f'''
            CREATE TABLE IF NOT EXISTS checkup_reports (
                id {serial_pk_sql()},
                user_id INTEGER NOT NULL,
                checkup_date TEXT NOT NULL,
                institution TEXT,
                summary TEXT,
                created_at {timestamp_type()} {current_timestamp_default()}
            )
        ''')
        c.execute('CREATE INDEX IF NOT EXISTS idx_checkup_user_date ON checkup_reports(user_id, checkup_date)')

        c.execute(f'''
            CREATE TABLE IF NOT EXISTS checkup_items (
                id {serial_pk_sql()},
                report_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                category TEXT,
                name TEXT NOT NULL,
                value TEXT,
                unit TEXT,
                ref_range TEXT,
                status TEXT,
                created_at {timestamp_type()} {current_timestamp_default()}
            )
        ''')
        c.execute('CREATE INDEX IF NOT EXISTS idx_checkup_items_report ON checkup_items(report_id)')

        # 设备绑定表（Apple Health 同步）
        c.execute(f'''
            CREATE TABLE IF NOT EXISTS device_bindings (
                id {serial_pk_sql()},
                user_id INTEGER NOT NULL,
                bind_code TEXT,
                code_expires_at {timestamp_type()},
                device_id TEXT,
                device_token TEXT,
                device_name TEXT,
                created_at {timestamp_type()} {current_timestamp_default()},
                bound_at {timestamp_type()},
                FOREIGN KEY (user_id) REFERENCES app_users(id)
            )
        ''')

        # 性能索引
        c.execute('CREATE INDEX IF NOT EXISTS idx_records_user_ts ON records(user_id, timestamp DESC)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_records_user_pred ON records(user_id, is_predicted, timestamp)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_records_weight ON records(user_id, weight, timestamp DESC)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_records_bp ON records(user_id, systolic_pressure, timestamp DESC)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_analyses_user ON health_analyses(user_id, created_at DESC)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_medlogs_plan ON medication_logs(plan_id, log_date)')

        # 同步所有 SERIAL 序列，防止迁移后序列落后于实际数据
        for tbl in ('records', 'medication_plans', 'dosage_history', 'medication_logs',
                    'health_analyses', 'chat_messages', 'app_users', 'user_auth_providers'):
            with contextlib.suppress(Exception):
                c.execute(f"SELECT setval('{tbl}_id_seq', COALESCE((SELECT MAX(id) FROM {tbl}), 1))")

        # 一次性数据迁移：老账号补新增模块（骨密度 / 体检）
        _apply_module_backfill(c)
        # 一次性数据迁移：清理非血糖用户被 AI 塞进来的假血糖记录
        _apply_prediction_cleanup(c)
        # 一次性数据迁移：给「预测与实测同值」的历史预测补标已验证，消除重复显示
        _apply_stale_prediction_link(c)

        conn.commit()
    except Exception as e:
        import traceback
        print(f"PostgreSQL DB Init Error: {e}")
        traceback.print_exc()
    finally:
        conn.close()


def _init_db_sqlite():
    """SQLite schema 初始化（保留现有迁移逻辑）。"""
    conn = None
    try:
        conn = sqlite_connect()
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS records
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      value REAL,
                      unit TEXT,
                      type TEXT,
                      notes TEXT,
                      timestamp DATETIME,
                      created_at DATETIME DEFAULT CURRENT_TIMESTAMP)''')

        # 迁移：血糖记录增加列
        mig_cols = [
            ("calories", "INTEGER DEFAULT 0"),
            ("diet_analysis", "TEXT DEFAULT ''"),
            ("is_predicted", "BOOLEAN DEFAULT 0"),
            ("distance", "REAL"),
            ("duration", "TEXT"),
            ("heart_rate", "INTEGER"),
            ("pace", "TEXT"),
            ("cadence", "INTEGER"),
            ("systolic_pressure", "INTEGER"),
            ("diastolic_pressure", "INTEGER"),
            ("pulse_rate", "INTEGER"),
            ("spo2", "INTEGER"),
            ("weight", "REAL"),
            ("bmi", "REAL"),
            ("verified_by_real_id", "INTEGER"),
            ("prediction_error", "REAL"),
            ("carbs_grams", "REAL"),
            ("gi_value", "REAL"),
            ("medication_name", "TEXT"),
            ("vo2max", "REAL"),
            ("max_heart_rate", "INTEGER"),
            ("steps", "INTEGER"),
            ("max_pace", "TEXT"),
            ("user_id", "INTEGER DEFAULT 1"),
            ("external_id", "TEXT"),
            ("source", "TEXT"),
            ("body_fat_rate", "REAL"),
            ("body_fat_mass", "REAL"),
            ("muscle_mass", "REAL"),
            ("skeletal_muscle_mass", "REAL"),
            ("protein_mass", "REAL"),
            ("visceral_fat", "REAL"),
            ("basal_metabolism", "INTEGER"),
            ("total_sets", "INTEGER"),
            ("total_reps", "INTEGER"),
            ("training_load", "REAL"),
            ("intensity_minutes", "INTEGER"),
            ("te_aerobic", "REAL"),
            ("te_anaerobic", "REAL"),
            ("elevation_gain", "REAL"),
        ]

        for col_name, col_type in mig_cols:
            with contextlib.suppress(sqlite3.OperationalError):
                c.execute(f"ALTER TABLE records ADD COLUMN {col_name} {col_type}")

        # 用药方案表
        c.execute('''CREATE TABLE IF NOT EXISTS medication_plans
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      medication_name TEXT NOT NULL,
                      dosage TEXT,
                      times_per_day INTEGER DEFAULT 1,
                      timing_notes TEXT,
                      start_date DATE NOT NULL,
                      end_date DATE,
                      is_active BOOLEAN DEFAULT 1,
                      notes TEXT,
                      created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                      user_id INTEGER DEFAULT 1)''')

        # 用药方案迁移
        plan_cols = [
            ("frequency", "TEXT DEFAULT 'daily'"),
            ("frequency_detail", "TEXT"),
            ("category", "TEXT DEFAULT 'long_term'"),
            ("dose_quantity", "TEXT DEFAULT '1'"),
            ("dose_unit", "TEXT DEFAULT '片'"),
            ("med_type", "TEXT DEFAULT ''")
        ]
        for col_name, col_type in plan_cols:
            with contextlib.suppress(sqlite3.OperationalError):
                c.execute(f"ALTER TABLE medication_plans ADD COLUMN {col_name} {col_type}")

        # 剂量记录表
        c.execute('''CREATE TABLE IF NOT EXISTS dosage_history
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      plan_id INTEGER NOT NULL,
                      old_dosage TEXT,
                      new_dosage TEXT,
                      changed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                      FOREIGN KEY (plan_id) REFERENCES medication_plans(id))''')

        # 用药历史表
        c.execute('''CREATE TABLE IF NOT EXISTS medication_logs
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      plan_id INTEGER NOT NULL,
                      log_date DATE NOT NULL,
                      timestamp DATETIME NOT NULL,
                      taken BOOLEAN DEFAULT 1,
                      notes TEXT,
                      created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                      user_id INTEGER DEFAULT 1,
                      FOREIGN KEY (plan_id) REFERENCES medication_plans(id))''')

        # 健康分析记录表
        c.execute('''CREATE TABLE IF NOT EXISTS health_analyses
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      analysis_date DATE NOT NULL,
                      health_score INTEGER,
                      glucose_summary TEXT,
                      blood_pressure_summary TEXT,
                      exercise_summary TEXT,
                      medication_summary TEXT,
                      recommendations TEXT,
                      full_analysis TEXT,
                      is_auto_generated BOOLEAN DEFAULT 0,
                      created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                      user_id INTEGER DEFAULT 1,
                      days INTEGER DEFAULT 7)''')

        # 用户表
        c.execute('''CREATE TABLE IF NOT EXISTS app_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            display_name TEXT NOT NULL,
            avatar TEXT,
            is_active BOOLEAN DEFAULT 1,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP)''')

        # Garmin 绑定表：每个用户一份 Garmin 账号绑定，token 按用户分目录存储
        c.execute('''CREATE TABLE IF NOT EXISTS garmin_bindings (
            user_id INTEGER PRIMARY KEY REFERENCES app_users(id),
            email TEXT NOT NULL,
            is_cn BOOLEAN DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP)''')

        # 用户档案表
        c.execute('''CREATE TABLE IF NOT EXISTS user_profiles (
            user_id INTEGER PRIMARY KEY REFERENCES app_users(id),
            name TEXT,
            birth_year INTEGER,
            height INTEGER,
            weight INTEGER,
            gender TEXT,
            default_meals TEXT,
            target_ranges TEXT,
            enabled_modules TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            app_name TEXT,
            app_emoji TEXT,
            app_tagline TEXT,
            persona TEXT)''')

        # 用户档案表迁移：目标体重（按用户存储，避免全局污染）
        with contextlib.suppress(sqlite3.OperationalError):
            c.execute("ALTER TABLE user_profiles ADD COLUMN target_weight REAL")

        # 用户档案表迁移：出生年月日
        for col_name, col_type in [("birth_month", "INTEGER"), ("birth_day", "INTEGER")]:
            with contextlib.suppress(sqlite3.OperationalError):
                c.execute(f"ALTER TABLE user_profiles ADD COLUMN {col_name} {col_type}")

        # 用户档案表迁移：品牌个性化（应用名 / 图标 / 标语 / 使用场景）
        for col_name in ("app_name", "app_emoji", "app_tagline", "persona"):
            with contextlib.suppress(sqlite3.OperationalError):
                c.execute(f"ALTER TABLE user_profiles ADD COLUMN {col_name} TEXT")

        # 用户档案表迁移：Garmin 同步天数（30~240，默认 30）
        with contextlib.suppress(sqlite3.OperationalError):
            c.execute("ALTER TABLE user_profiles ADD COLUMN garmin_sync_days INTEGER DEFAULT 30")

        # 用户表迁移
        with contextlib.suppress(sqlite3.OperationalError):
            c.execute("ALTER TABLE app_users ADD COLUMN password_hash TEXT")

        for col in ['phone TEXT', 'email TEXT']:
            with contextlib.suppress(sqlite3.OperationalError):
                c.execute(f"ALTER TABLE app_users ADD COLUMN {col}")

        # 骨密度检查记录（低频检查，独立于 records 主表）
        c.execute('''CREATE TABLE IF NOT EXISTS bone_density_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            measure_date TEXT NOT NULL,
            site TEXT,
            t_score REAL,
            z_score REAL,
            bmd REAL,
            institution TEXT,
            notes TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP)''')
        c.execute('CREATE INDEX IF NOT EXISTS idx_bone_user_date ON bone_density_records(user_id, measure_date)')

        # 体检报告 + 明细指标
        c.execute('''CREATE TABLE IF NOT EXISTS checkup_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            checkup_date TEXT NOT NULL,
            institution TEXT,
            summary TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP)''')
        c.execute('CREATE INDEX IF NOT EXISTS idx_checkup_user_date ON checkup_reports(user_id, checkup_date)')

        c.execute('''CREATE TABLE IF NOT EXISTS checkup_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            category TEXT,
            name TEXT NOT NULL,
            value TEXT,
            unit TEXT,
            ref_range TEXT,
            status TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP)''')
        c.execute('CREATE INDEX IF NOT EXISTS idx_checkup_items_report ON checkup_items(report_id)')

        # 聊天记录表
        c.execute('''CREATE TABLE IF NOT EXISTS chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        c.execute('CREATE INDEX IF NOT EXISTS idx_chat_user_session ON chat_messages(user_id, session_id, created_at)')

        # 认证提供商表
        c.execute('''CREATE TABLE IF NOT EXISTS user_auth_providers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            provider TEXT NOT NULL,
            provider_uid TEXT NOT NULL,
            verified BOOLEAN DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES app_users(id)
        )''')
        c.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_auth_provider_uid ON user_auth_providers(provider, provider_uid)')

        # 设备绑定表（Apple Health 同步）
        c.execute('''CREATE TABLE IF NOT EXISTS device_bindings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            bind_code TEXT,
            code_expires_at TEXT,
            device_id TEXT,
            device_token TEXT,
            device_name TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            bound_at DATETIME,
            FOREIGN KEY (user_id) REFERENCES app_users(id)
        )''')

        # ========== 性能索引 ==========
        c.execute('CREATE INDEX IF NOT EXISTS idx_records_user_ts ON records(user_id, timestamp DESC)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_records_user_pred ON records(user_id, is_predicted, timestamp)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_records_weight ON records(user_id, weight, timestamp DESC)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_records_bp ON records(user_id, systolic_pressure, timestamp DESC)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_analyses_user ON health_analyses(user_id, created_at DESC)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_medlogs_plan ON medication_logs(plan_id, log_date)')

        # 一次性数据迁移：老账号补新增模块（骨密度 / 体检）
        _apply_module_backfill(c)
        # 一次性数据迁移：清理非血糖用户被 AI 塞进来的假血糖记录
        _apply_prediction_cleanup(c)
        # 一次性数据迁移：给「预测与实测同值」的历史预测补标已验证，消除重复显示
        _apply_stale_prediction_link(c)

        conn.commit()
    except Exception as e:
        import traceback
        print(f"SQLite DB Init Error: {e}")
        traceback.print_exc()
    finally:
        if conn:
            conn.close()
