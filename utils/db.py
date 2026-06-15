import os
import sqlite3
from flask import g


def get_db_type():
    """根据环境变量判断当前数据库类型。"""
    database_url = os.getenv('DATABASE_URL', '')
    if database_url.startswith('postgres'):
        return 'postgres'
    return os.getenv('DB_TYPE', 'sqlite')


def _normalize_sql(sql, db_type=None):
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
    """透明转换 SQL 占位符的 cursor 包装器。"""

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
    """透明转换 SQL 占位符的连接包装器。"""

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


# DB_NAME is imported lazily within functions to allow runtime path changes (e.g. testing)
def get_db():
    """
    获取数据库连接。
    在 Flask 应用上下文中，数据库连接存储在 g 对象中。
    返回的连接会自动处理 SQLite / PostgreSQL 占位符差异。
    """
    from core.config import DB_NAME as db_name
    db = getattr(g, '_database', None)
    if db is None:
        raw_conn = sqlite3.connect(db_name)
        raw_conn.row_factory = sqlite3.Row
        db = g._database = ConnectionWrapper(raw_conn)
    return db

def close_db(exception=None):
    """
    关闭数据库连接。
    """
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def init_db():
    """
    初始化数据库表和执行必要的迁移。
    """
    from core.config import DB_NAME as db_name
    conn = None
    try:
        conn = sqlite3.connect(db_name)
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

        # 聊天记录表
        c.execute('''CREATE TABLE IF NOT EXISTS chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        c.execute('CREATE INDEX IF NOT EXISTS idx_chat_user_session ON chat_messages(user_id, session_id, created_at)')

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
        print(f"DB Init Error: {e}")
        traceback.print_exc()
    finally:
        if conn:
            conn.close()
