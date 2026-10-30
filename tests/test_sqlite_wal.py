"""WAL + busy_timeout 三路径守卫。

三个写入口（web / master 后台线程 / 外部 MCP 进程）必须用同一套参数：
  - journal_mode=WAL  读写互不阻塞
  - busy_timeout      撞锁排队等，而不是 5 秒就抛 OperationalError

任何一处退回裸 sqlite3.connect()，撞锁丢记录的洞就重新打开。
"""
import sqlite3

import pytest


@pytest.fixture
def db_file(tmp_path):
    path = tmp_path / 'wal_test.db'
    conn = sqlite3.connect(path)
    conn.execute('CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)')
    conn.commit()
    conn.close()
    return str(path)


def test_sqlite_connect_sets_wal_and_busy_timeout(db_file):
    from utils.db import SQLITE_BUSY_TIMEOUT_MS, sqlite_connect

    conn = sqlite_connect(db_file)
    try:
        assert conn.execute('PRAGMA journal_mode').fetchone()[0].lower() == 'wal'
        assert conn.execute('PRAGMA busy_timeout').fetchone()[0] == SQLITE_BUSY_TIMEOUT_MS
        assert SQLITE_BUSY_TIMEOUT_MS >= 5000
    finally:
        conn.close()


def test_sqlite_connect_read_only_skips_journal_mode(db_file):
    """只读连接不能改 journal_mode，硬设会抛异常 —— 必须跳过。"""
    from utils.db import sqlite_connect

    conn = sqlite_connect(db_file, read_only=True)
    try:
        # 只读连接仍能读
        assert conn.execute('SELECT count(*) FROM t').fetchone()[0] == 0
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO t (v) VALUES ('x')")
    finally:
        conn.close()


def test_sqlite_connect_wal_survives_new_connections(db_file):
    """journal_mode 写进库文件头：第二、第三条连接不设也是 WAL。"""
    from utils.db import sqlite_connect

    sqlite_connect(db_file).close()
    raw = sqlite3.connect(db_file)
    try:
        assert raw.execute('PRAGMA journal_mode').fetchone()[0].lower() == 'wal'
    finally:
        raw.close()


def test_mcp_writer_uses_shared_conn(db_file, monkeypatch):
    from mcp_adapter.servers import writer

    monkeypatch.setattr(writer, 'DB_PATH', db_file)
    conn = writer._db()
    try:
        assert conn.execute('PRAGMA journal_mode').fetchone()[0].lower() == 'wal'
        assert conn.execute('PRAGMA busy_timeout').fetchone()[0] > 0
    finally:
        conn.close()


def test_mcp_reader_conn_is_read_only_with_timeout(db_file, monkeypatch):
    from mcp_adapter.servers import reader

    monkeypatch.setattr(reader, 'DB_PATH', db_file)
    conn = reader._connect()
    try:
        assert conn.execute('PRAGMA busy_timeout').fetchone()[0] > 0
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO t (v) VALUES ('x')")
    finally:
        conn.close()


def test_user_manager_explicit_path_gets_wal(db_file):
    """user_manager 的显式路径分支也是写路径，不能裸 connect。"""
    from user_manager import _db_conn

    with _db_conn(db_file) as conn:
        assert conn.execute('PRAGMA journal_mode').fetchone()[0].lower() == 'wal'
