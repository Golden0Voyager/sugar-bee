"""数据库连接与初始化工具。

支持 SQLite（本地开发 / 测试）和 PostgreSQL（Cloud Run + Cloud SQL）双模式。
业务代码统一使用 `?` 占位符；utils/db.py 中的包装器会根据 DB_TYPE 自动转换为 `%s`。
"""
from __future__ import annotations

import datetime
import os
import sqlite3
from typing import Any

from flask import g

from core import config
from utils.sql_dialect import (
    bool_default,
    bool_type,
    current_timestamp_default,
    double_type,
    serial_pk_sql,
    timestamp_type,
)


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
                if hasattr(row, 'keys') and 'id' in row.keys():
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


class _CompatCursor:
    """包装 psycopg2 RealDictCursor，返回 _CompatRow 并处理占位符转换。"""

    def __init__(self, cursor):
        self._cursor = cursor
        self._lastrowid = None

    def execute(self, sql, parameters=None):
        sql = _normalize_sql(sql, config.DB_TYPE)
        self._lastrowid = None
        if parameters is None:
            parameters = ()
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
            raw_conn = sqlite3.connect(config.DB_NAME)
            raw_conn.row_factory = sqlite3.Row
            db = g._database = ConnectionWrapper(raw_conn)
    return db


def get_raw_conn():
    """获取一个不受 Flask g 管理的数据库连接，供后台线程使用。调用方须用 put_raw_conn 归还。"""
    if config.DB_TYPE == 'postgres':
        pool_obj = _get_pool()
        conn = pool_obj.getconn()
        return _PgConnectionWrapper(conn, pool_obj)
    import sqlite3
    conn = sqlite3.connect(config.DB_NAME)
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


def _init_db_postgres():
    """PostgreSQL schema 初始化（一次性创建完整表结构）。"""
    import psycopg2

    conn = psycopg2.connect(_pg_dsn())
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
                source TEXT
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
                birth_day INTEGER
            )
        ''')

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

        # 性能索引
        c.execute('CREATE INDEX IF NOT EXISTS idx_records_user_ts ON records(user_id, timestamp DESC)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_records_user_pred ON records(user_id, is_predicted, timestamp)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_records_weight ON records(user_id, weight, timestamp DESC)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_records_bp ON records(user_id, systolic_pressure, timestamp DESC)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_analyses_user ON health_analyses(user_id, created_at DESC)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_medlogs_plan ON medication_logs(plan_id, log_date)')

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
        conn = sqlite3.connect(config.DB_NAME)
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
            ("source", "TEXT")
        ]

        for col_name, col_type in mig_cols:
            try:
                c.execute(f"ALTER TABLE records ADD COLUMN {col_name} {col_type}")
            except sqlite3.OperationalError:
                pass

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
            try:
                c.execute(f"ALTER TABLE medication_plans ADD COLUMN {col_name} {col_type}")
            except sqlite3.OperationalError:
                pass

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
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP)''')

        # 用户档案表迁移：目标体重（按用户存储，避免全局污染）
        try:
            c.execute("ALTER TABLE user_profiles ADD COLUMN target_weight REAL")
        except sqlite3.OperationalError:
            pass

        # 用户档案表迁移：出生年月日
        for col_name, col_type in [("birth_month", "INTEGER"), ("birth_day", "INTEGER")]:
            try:
                c.execute(f"ALTER TABLE user_profiles ADD COLUMN {col_name} {col_type}")
            except sqlite3.OperationalError:
                pass

        # 用户表迁移
        try:
            c.execute("ALTER TABLE app_users ADD COLUMN password_hash TEXT")
        except sqlite3.OperationalError:
            pass

        for col in ['phone TEXT', 'email TEXT']:
            try:
                c.execute(f"ALTER TABLE app_users ADD COLUMN {col}")
            except sqlite3.OperationalError:
                pass

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

        # ========== 性能索引 ==========
        c.execute('CREATE INDEX IF NOT EXISTS idx_records_user_ts ON records(user_id, timestamp DESC)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_records_user_pred ON records(user_id, is_predicted, timestamp)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_records_weight ON records(user_id, weight, timestamp DESC)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_records_bp ON records(user_id, systolic_pressure, timestamp DESC)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_analyses_user ON health_analyses(user_id, created_at DESC)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_medlogs_plan ON medication_logs(plan_id, log_date)')

        conn.commit()
    except Exception as e:
        import traceback
        print(f"SQLite DB Init Error: {e}")
        traceback.print_exc()
    finally:
        if conn:
            conn.close()
