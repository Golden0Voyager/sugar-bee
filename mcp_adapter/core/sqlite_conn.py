"""SQLite 连接统一入口（MCP 侧）。

为什么单独一份：MCP writer / reader 是**独立进程**（可单独部署到
/root/health_tools），不能 import app 的 utils.db。但两边的并发参数必须一致，
否则「web 侧开了 WAL、MCP 侧还在用 5 秒默认超时」这种半吊子状态照样丢数据。

口径与 utils/db.py 的 sqlite_connect / apply_sqlite_pragmas 保持同一套：
  journal_mode=WAL  读写互不阻塞
  busy_timeout      撞锁排队等（默认 15 秒，SUGAR_BEE_BUSY_TIMEOUT_MS 可调）
  synchronous=NORMAL WAL 下的推荐值

只依赖标准库 —— 与 core/ 其他模块同一约束。
"""

from __future__ import annotations

import os
import sqlite3

BUSY_TIMEOUT_MS = int(os.environ.get('SUGAR_BEE_BUSY_TIMEOUT_MS', '15000'))


def connect(db_path: str, read_only: bool = False, timeout: float = 30) -> sqlite3.Connection:
    """打开连接并套上 WAL + busy_timeout。

    read_only=True 用 mode=ro（只读分析 server），此时不改 journal_mode
    —— 只读连接改不了，硬设会抛异常。
    """
    if read_only:
        conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True, timeout=timeout)
    else:
        conn = sqlite3.connect(db_path, timeout=timeout)
    conn.execute(f'PRAGMA busy_timeout={BUSY_TIMEOUT_MS}')
    if not read_only:
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA synchronous=NORMAL')
    return conn
