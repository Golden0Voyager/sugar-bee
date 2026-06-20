"""数据库方言助手模块。

为 SQLite（本地开发/测试）和 PostgreSQL（Cloud Run 生产）提供统一的 SQL 片段生成，
避免在业务代码中散落 `if DB_TYPE == 'postgres'` 判断。
"""
from __future__ import annotations

from core import config


# 占位符：代码层统一写 `?`，utils/db.py 中的包装器会自动转换为 `%s`（PostgreSQL）。
def ph() -> str:
    """返回当前数据库类型的占位符。"""
    return "%s" if config.DB_TYPE == "postgres" else "?"


def now_sql() -> str:
    """返回当前时间的 SQL 表达式（北京时间）。

    SQLite 用 `'localtime'` 修饰符，依赖进程时区（容器 TZ=Asia/Shanghai）；
    PostgreSQL 的 NOW() 依赖连接会话时区（见 utils/db.py 连接池 options）。
    """
    return "NOW()" if config.DB_TYPE == "postgres" else "datetime('now', 'localtime')"


def interval_sql(days: int) -> str:
    """返回 `days` 天前的时间 SQL 表达式（days 必须为整数）。"""
    if not isinstance(days, int):
        raise TypeError("interval_sql only accepts int")
    if config.DB_TYPE == "postgres":
        return f"NOW() - INTERVAL '{days} days'"
    return f"datetime('now', 'localtime', '-{days} days')"


# Python strftime 格式 → PostgreSQL TO_CHAR 格式 的常用映射
_FMT_MAP = {
    "%Y-%m-%d %H:%M": "YYYY-MM-DD HH24:MI",
    "%Y-%m-%d": "YYYY-MM-DD",
    "%H:%M": "HH24:MI",
    "%Y-%m": "YYYY-MM",
    "%Y": "YYYY",
}


def date_format_sql(column: str, fmt: str) -> str:
    """返回按格式格式化时间列的 SQL 表达式。

    Args:
        column: 列名或表达式。
        fmt: Python strftime 风格格式字符串，目前支持常见格式；
             未收录的格式会抛出 NotImplementedError。
    """
    if config.DB_TYPE == "postgres":
        pg_fmt = _FMT_MAP.get(fmt)
        if pg_fmt is None:
            raise NotImplementedError(f"Unsupported date format for PostgreSQL: {fmt}")
        return f"TO_CHAR({column}, '{pg_fmt}')"
    return f"strftime('{fmt}', {column})"


def date_sql(column: str) -> str:
    """返回提取日期部分的 SQL 表达式。"""
    if config.DB_TYPE == "postgres":
        return f"{column}::date"
    return f"DATE({column})"


def epoch_sql(column: str) -> str:
    """返回时间戳 epoch 秒数的 SQL 表达式。"""
    if config.DB_TYPE == "postgres":
        return f"EXTRACT(EPOCH FROM {column})"
    return f"strftime('%s', {column})"


def insert_or_ignore_sql(table: str, columns: list[str], conflict_col: str | None = None) -> str:
    """返回 INSERT OR IGNORE 风格的 SQL（PostgreSQL 使用 ON CONFLICT DO NOTHING）。

    Args:
        table: 目标表名。
        columns: 要插入的列名列表。
        conflict_col: PostgreSQL 下冲突目标列；未提供时不指定列（不推荐）。
    """
    cols = ", ".join(columns)
    placeholders = ", ".join([ph() for _ in columns])
    if config.DB_TYPE == "postgres":
        conflict = f" ({conflict_col})" if conflict_col else ""
        return f"INSERT INTO {table} ({cols}) VALUES ({placeholders}) ON CONFLICT{conflict} DO NOTHING"
    return f"INSERT OR IGNORE INTO {table} ({cols}) VALUES ({placeholders})"


def serial_pk_sql() -> str:
    """返回自增主键列定义。"""
    return "SERIAL PRIMARY KEY" if config.DB_TYPE == "postgres" else "INTEGER PRIMARY KEY AUTOINCREMENT"


def bool_type() -> str:
    return "SMALLINT"


def double_type() -> str:
    return "DOUBLE PRECISION" if config.DB_TYPE == "postgres" else "REAL"


def timestamp_type() -> str:
    return "TIMESTAMP" if config.DB_TYPE == "postgres" else "DATETIME"


def current_timestamp_default() -> str:
    return "DEFAULT CURRENT_TIMESTAMP"


def boolean_literal(value: bool) -> str:
    """返回布尔字面量（用于必须内嵌在 SQL 字符串中的场景，优先使用参数占位符）。"""
    if config.DB_TYPE == "postgres":
        return "TRUE" if value else "FALSE"
    return "1" if value else "0"


def bool_default(value: bool) -> str:
    """返回布尔默认值约束。"""
    return f"DEFAULT {boolean_literal(value)}"


def group_concat_sql(column: str, separator: str = ",") -> str:
    """返回聚合拼接函数的 SQL 表达式。"""
    if config.DB_TYPE == "postgres":
        return f"STRING_AGG({column}, '{separator}')"
    return f"GROUP_CONCAT({column}, '{separator}')"
