"""将 Cloud SQL PostgreSQL 的增量数据同步到本地 SQLite。

用法：
    uv run python scripts/sync_pg_to_sqlite.py

环境变量：
    SUGAR_BEE_DATABASE_URL：Cloud SQL PostgreSQL 连接 URL
        示例：postgresql://user:pass@localhost:5433/sugar_bee
    SUGAR_BEE_DB_PATH：本地 SQLite 路径，默认项目根目录 glucose.db

首次运行会全量拉取；后续运行基于各表最后同步的 id 做增量拉取。
对于会更新的小表（app_users / user_profiles），每次全量同步。
"""
from __future__ import annotations

import contextlib
import datetime
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

import psycopg2
from psycopg2.extras import RealDictCursor

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOCAL_DB = PROJECT_ROOT / "glucose.db"
STATE_FILE = PROJECT_ROOT / ".pg_sync_state.json"

# 同步配置：表名 → (主键列, 游标列, 是否全量同步)
SYNC_CONFIG: dict[str, tuple[str, str | None, bool]] = {
    # 主键    游标列    是否全量
    "app_users": ("id", None, True),
    "user_profiles": ("user_id", None, True),
    "records": ("id", "id", False),
    "medication_plans": ("id", "id", False),
    "dosage_history": ("id", "id", False),
    "medication_logs": ("id", "id", False),
    "health_analyses": ("id", "id", False),
    "chat_messages": ("id", "id", False),
    "user_auth_providers": ("id", "id", False),
}


def _normalize_value(value: Any) -> Any:
    """将 PostgreSQL 返回值转换为 SQLite 可接受的格式。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    # datetime/date 用固定格式，避免 str() 带微秒或时区后缀，与 App 的
    # '%Y-%m-%d %H:%M:%S' 存储格式不一致（全项目统一北京时间 naive 墙钟）。
    if isinstance(value, datetime.datetime):
        return value.strftime('%Y-%m-%d %H:%M:%S')
    if isinstance(value, datetime.date):
        return value.strftime('%Y-%m-%d')
    return str(value)


def load_state() -> dict[str, Any]:
    """加载同步状态。"""
    if STATE_FILE.exists():
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(state: dict[str, Any]) -> None:
    """保存同步状态。"""
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def get_pg_url() -> str:
    """从环境变量获取 PostgreSQL URL。"""
    url = os.environ.get("SUGAR_BEE_DATABASE_URL")
    if not url:
        print(
            "[Error] 请设置 SUGAR_BEE_DATABASE_URL 环境变量，\n"
            "        例如：postgresql://user:pass@localhost:5433/sugar_bee"
        )
        sys.exit(1)
    return url


def get_local_db_path() -> str:
    """获取本地 SQLite 路径。"""
    return os.environ.get("SUGAR_BEE_DB_PATH") or str(DEFAULT_LOCAL_DB)


def ensure_sqlite_table(conn: sqlite3.Connection, table: str) -> bool:
    """确认本地 SQLite 中存在目标表。"""
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    )
    return cur.fetchone() is not None


def fetch_columns(pg_cur: RealDictCursor, table: str) -> list[str]:
    """获取 PostgreSQL 表的列名。"""
    pg_cur.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = %s
        ORDER BY ordinal_position
        """,
        (table,),
    )
    return [row["column_name"] for row in pg_cur.fetchall()]


def sync_table(
    pg_cur: RealDictCursor,
    sqlite_conn: sqlite3.Connection,
    table: str,
    pk_col: str,
    cursor_col: str | None,
    full_sync: bool,
    state: dict[str, Any],
) -> int:
    """同步单表，返回同步行数。"""
    if not ensure_sqlite_table(sqlite_conn, table):
        print(f"[{table}] 本地 SQLite 中不存在该表，跳过")
        return 0

    columns = fetch_columns(pg_cur, table)
    if not columns:
        print(f"[{table}] PostgreSQL 中未找到列信息，跳过")
        return 0

    last_cursor = state.get(table, 0)

    if full_sync or cursor_col is None:
        pg_cur.execute(f"SELECT {', '.join(columns)} FROM {table} ORDER BY {pk_col}")
        print(f"[{table}] 全量同步")
    else:
        pg_cur.execute(
            f"SELECT {', '.join(columns)} FROM {table} "
            f"WHERE {cursor_col} > %s ORDER BY {cursor_col}",
            (last_cursor,),
        )
        print(f"[{table}] 增量同步（{cursor_col} > {last_cursor}）")

    rows = pg_cur.fetchall()
    if not rows:
        print(f"[{table}] 没有新数据")
        return 0

    placeholders = ",".join(["?"] * len(columns))
    col_names = ",".join(columns)

    # 全量同步的小表先清空本地数据，再写入
    if full_sync:
        sqlite_conn.execute(f"DELETE FROM {table}")

    new_last_cursor = last_cursor
    inserted = 0
    for row in rows:
        values = [_normalize_value(row[col]) for col in columns]
        sqlite_conn.execute(
            f"INSERT OR REPLACE INTO {table} ({col_names}) VALUES ({placeholders})",
            values,
        )
        inserted += 1
        if cursor_col and row[cursor_col] is not None:
            with contextlib.suppress(TypeError, ValueError):
                new_last_cursor = max(new_last_cursor, int(row[cursor_col]))

    if cursor_col:
        state[table] = new_last_cursor

    print(f"[{table}] 写入 {inserted} 行")
    return inserted


def main() -> int:
    pg_url = get_pg_url()
    local_db = get_local_db_path()

    print(f"[Sync] PostgreSQL → {local_db}")

    state = load_state()
    total = 0

    pg_conn = psycopg2.connect(pg_url)
    sqlite_conn = sqlite3.connect(local_db)
    sqlite_conn.row_factory = sqlite3.Row

    try:
        with pg_conn.cursor(cursor_factory=RealDictCursor) as pg_cur:
            for table, (pk_col, cursor_col, full_sync) in SYNC_CONFIG.items():
                try:
                    count = sync_table(
                        pg_cur, sqlite_conn, table, pk_col, cursor_col, full_sync, state
                    )
                    # 单表独立提交：成功才落盘
                    sqlite_conn.commit()
                    total += count
                except Exception as e:  # noqa: BLE001
                    # 单表失败回滚，避免全量表（先 DELETE 再插）被清空，且不影响其他表
                    sqlite_conn.rollback()
                    print(f"[{table}] 同步失败，已回滚: {e}")
                    # 继续同步其他表
    finally:
        pg_conn.close()
        sqlite_conn.close()

    save_state(state)
    print(f"[Sync] 完成，共写入 {total} 行")
    return 0


if __name__ == "__main__":
    sys.exit(main())
