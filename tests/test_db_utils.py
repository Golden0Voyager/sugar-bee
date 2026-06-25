"""测试 utils/db.py 和 utils/sql_dialect.py 的纯函数"""
import datetime
import sqlite3
from unittest.mock import MagicMock, patch

import pytest


class TestInlineParams:
    """_inline_params() — 参数内联（PostgreSQL 路径）"""

    def _make_postgres(self, monkeypatch):
        """模拟 PostgreSQL 模式"""
        import core.config as config
        monkeypatch.setattr(config, 'DB_TYPE', 'postgres')

    def test_normal_strings(self, monkeypatch):
        self._make_postgres(monkeypatch)
        from utils.db import _inline_params

        sql = "SELECT * FROM records WHERE user_id = ? AND type = ?"
        params = (1, '空腹')
        result = _inline_params(sql, params)
        assert "user_id = 1" in result
        assert "type = '空腹'" in result

    def test_chinese_strings_no_latin1_error(self, monkeypatch):
        """核心修复：包含中文的活动类型不应抛 latin-1 编码错误"""
        self._make_postgres(monkeypatch)
        from utils.db import _inline_params

        sql = "INSERT INTO records (user_id, type, notes) VALUES (?, ?, ?)"
        params = (1, '跑步', '早上晨跑')
        # 之前 adapt('跑步').getquoted() 会抛 UnicodeEncodeError
        result = _inline_params(sql, params)
        assert "'跑步'" in result
        assert "'早上晨跑'" in result

    def test_string_with_single_quote(self, monkeypatch):
        self._make_postgres(monkeypatch)
        from utils.db import _inline_params

        sql = "SELECT * FROM records WHERE notes = ?"
        params = ("It's a test",)
        result = _inline_params(sql, params)
        assert "It''s" in result  # 单引号被转义

    def test_none_value(self, monkeypatch):
        self._make_postgres(monkeypatch)
        from utils.db import _inline_params

        sql = "SELECT * FROM records WHERE notes = ?"
        params = (None,)
        result = _inline_params(sql, params)
        assert "NULL" in result or "None" in result

    def test_numeric_values(self, monkeypatch):
        self._make_postgres(monkeypatch)
        from utils.db import _inline_params

        sql = "SELECT * FROM records WHERE value = ? AND heart_rate = ?"
        params = (6.5, 72)
        result = _inline_params(sql, params)
        assert "6.5" in result
        assert "72" in result

    def test_multiple_params_all_types(self, monkeypatch):
        self._make_postgres(monkeypatch)
        from utils.db import _inline_params

        sql = "INSERT INTO t VALUES (?, ?, ?, ?)"
        params = (1, '餐后2小时', 8.5, '晚上吃了米饭')
        result = _inline_params(sql, params)
        assert "1" in result
        assert "'餐后2小时'" in result
        assert "8.5" in result
        assert "'晚上吃了米饭'" in result

    def test_sql_with_chinese_like_pattern(self, monkeypatch):
        """原 bug：中文 LIKE 模式导致 psycopg2 C 扩展索引越界"""
        self._make_postgres(monkeypatch)
        from utils.db import _inline_params

        sql = "SELECT * FROM records WHERE type LIKE ?"
        params = ('%餐后2小时%',)
        result = _inline_params(sql, params)
        assert "'%餐后2小时%'" in result


class TestConvertSqliteToPg:
    """_convert_sqlite_to_pg() — SQLite 特有函数转 PostgreSQL"""

    def test_date_function(self):
        from utils.db import _convert_sqlite_to_pg

        sql = "SELECT COUNT(DISTINCT DATE(timestamp)) FROM records"
        result = _convert_sqlite_to_pg(sql)
        assert result == "SELECT COUNT(DISTINCT timestamp::date) FROM records"

    def test_date_with_table_alias(self):
        from utils.db import _convert_sqlite_to_pg

        sql = "SELECT DATE(p.timestamp) as date FROM records p"
        result = _convert_sqlite_to_pg(sql)
        assert result == "SELECT p.timestamp::date as date FROM records p"

    def test_datetime_with_minus_modifier(self):
        from utils.db import _convert_sqlite_to_pg

        sql = """SELECT id FROM records
                 WHERE timestamp BETWEEN datetime('2024-01-01 10:00:00', '-3 minutes')
                 AND datetime('2024-01-01 10:00:00', '+3 minutes')"""
        result = _convert_sqlite_to_pg(sql)
        assert "'2024-01-01 10:00:00'::timestamp - INTERVAL '3 minutes'" in result
        assert "'2024-01-01 10:00:00'::timestamp + INTERVAL '3 minutes'" in result

    def test_datetime_with_days_modifier(self):
        from utils.db import _convert_sqlite_to_pg

        sql = "SELECT * FROM records WHERE timestamp > datetime('2024-01-01', '-7 days')"
        result = _convert_sqlite_to_pg(sql)
        assert "'2024-01-01'::timestamp - INTERVAL '7 days'" in result

    def test_datetime_now(self):
        from utils.db import _convert_sqlite_to_pg

        sql = "SELECT * FROM records WHERE timestamp > datetime('now')"
        result = _convert_sqlite_to_pg(sql)
        assert "NOW()" in result

    def test_datetime_now_localtime_interval(self):
        from utils.db import _convert_sqlite_to_pg

        sql = "SELECT * FROM records WHERE timestamp > datetime('now', 'localtime', '-7 days')"
        result = _convert_sqlite_to_pg(sql)
        assert result == "SELECT * FROM records WHERE timestamp > NOW() - INTERVAL '7 days'"

    def test_sql_without_sqlite_functions_passthrough(self):
        from utils.db import _convert_sqlite_to_pg

        sql = "SELECT * FROM records WHERE user_id = 1 AND type = '空腹'"
        result = _convert_sqlite_to_pg(sql)
        assert result == sql

    def test_insert_statement_passthrough(self):
        from utils.db import _convert_sqlite_to_pg

        sql = "INSERT INTO records (user_id, type, value) VALUES (1, '空腹', 6.5)"
        result = _convert_sqlite_to_pg(sql)
        assert result == sql


class TestNormalizeSql:
    """_normalize_sql() — 占位符转换"""

    def test_sqlite_passthrough_question_mark(self):
        from utils.db import _normalize_sql

        sql = "SELECT * FROM records WHERE user_id = ? AND type = ?"
        result = _normalize_sql(sql, 'sqlite')
        # SQLite: ? 保持不变
        assert result == sql

    def test_sqlite_converts_percent_s(self):
        from utils.db import _normalize_sql

        sql = "SELECT * FROM records WHERE user_id = %s AND type = %s"
        result = _normalize_sql(sql, 'sqlite')
        # SQLite: %s → ?
        assert result == "SELECT * FROM records WHERE user_id = ? AND type = ?"

    def test_postgres_converts_question_mark(self):
        from utils.db import _normalize_sql

        sql = "SELECT * FROM records WHERE user_id = ? AND type = ?"
        result = _normalize_sql(sql, 'postgres')
        # PostgreSQL: ? → %s
        assert result == "SELECT * FROM records WHERE user_id = %s AND type = %s"

    def test_sqlite_keeps_strftime(self):
        """strftime 内的 %s 不应被替换"""
        from utils.db import _normalize_sql

        sql = "SELECT strftime('%s', timestamp) FROM records WHERE user_id = ?"
        result = _normalize_sql(sql, 'sqlite')
        # 字符串内 %s 不变，外部的 ? 保持不变
        assert "strftime('%s', timestamp)" in result
        assert "user_id = ?" in result

    def test_postgres_keeps_strftime(self):
        from utils.db import _normalize_sql

        sql = "SELECT strftime('%s', timestamp) FROM records WHERE user_id = ?"
        result = _normalize_sql(sql, 'postgres')
        # 字符串内 %s 不变，外部的 ? → %s
        assert "strftime('%s', timestamp)" in result
        assert "user_id = %s" in result

    def test_normalize_sql_default_db_type(self, monkeypatch):
        """未传 db_type 时使用默认值"""
        import core.config as config
        monkeypatch.setattr(config, 'DB_TYPE', 'sqlite')
        from utils.db import _normalize_sql

        sql = "SELECT * FROM t WHERE id = %s"
        result = _normalize_sql(sql)
        assert "id = ?" in result

    def test_normalize_sql_no_source_passthrough(self):
        """SQL 中没有需要替换的占位符时原样返回（line 48）"""
        from utils.db import _normalize_sql

        sql = "SELECT 1"
        result = _normalize_sql(sql, 'postgres')
        assert result == sql

    def test_normalize_sql_unknown_db_type(self):
        """未知 db_type 原样返回（line 42 else branch）"""
        from utils.db import _normalize_sql

        sql = "SELECT * FROM t WHERE id = ?"
        result = _normalize_sql(sql, 'unknown')
        assert result == sql

    def test_normalize_sql_escaped_quotes(self, monkeypatch):
        """字符串内 '' 转义不应中断占位符匹配（lines 62-64）"""
        import core.config as config
        monkeypatch.setattr(config, 'DB_TYPE', 'postgres')
        from utils.db import _normalize_sql

        sql = "SELECT * FROM t WHERE name = 'it''s' AND id = ?"
        result = _normalize_sql(sql, 'postgres')
        assert "id = %s" in result
        assert "it''s" in result or "it's" in result


class TestSqlDialect:
    """utils/sql_dialect.py 方言助手"""

    def _set_db_type(self, monkeypatch, db_type):
        import core.config as config
        monkeypatch.setattr(config, 'DB_TYPE', db_type)

    def test_ph_sqlite(self, monkeypatch):
        self._set_db_type(monkeypatch, 'sqlite')
        from utils.sql_dialect import ph
        assert ph() == '?'

    def test_ph_postgres(self, monkeypatch):
        self._set_db_type(monkeypatch, 'postgres')
        from utils.sql_dialect import ph
        assert ph() == '%s'

    def test_now_sql_sqlite(self, monkeypatch):
        self._set_db_type(monkeypatch, 'sqlite')
        from utils.sql_dialect import now_sql
        assert now_sql() == "datetime('now', 'localtime')"

    def test_now_sql_postgres(self, monkeypatch):
        self._set_db_type(monkeypatch, 'postgres')
        from utils.sql_dialect import now_sql
        assert now_sql() == "NOW()"

    def test_interval_sql_sqlite(self, monkeypatch):
        self._set_db_type(monkeypatch, 'sqlite')
        from utils.sql_dialect import interval_sql
        assert interval_sql(7) == "datetime('now', 'localtime', '-7 days')"

    def test_interval_sql_postgres(self, monkeypatch):
        self._set_db_type(monkeypatch, 'postgres')
        from utils.sql_dialect import interval_sql
        assert interval_sql(7) == "NOW() - INTERVAL '7 days'"

    def test_interval_sql_raises_on_non_int(self, monkeypatch):
        self._set_db_type(monkeypatch, 'sqlite')
        from utils.sql_dialect import interval_sql
        with pytest.raises(TypeError):
            interval_sql("7")  # type: ignore

    def test_date_format_sql_sqlite(self, monkeypatch):
        self._set_db_type(monkeypatch, 'sqlite')
        from utils.sql_dialect import date_format_sql
        assert date_format_sql('timestamp', '%Y-%m-%d') == "strftime('%Y-%m-%d', timestamp)"

    def test_date_format_sql_postgres(self, monkeypatch):
        self._set_db_type(monkeypatch, 'postgres')
        from utils.sql_dialect import date_format_sql
        assert date_format_sql('timestamp', '%Y-%m-%d') == "TO_CHAR(timestamp, 'YYYY-MM-DD')"

    def test_date_format_sql_postgres_parameter_cast(self, monkeypatch):
        self._set_db_type(monkeypatch, 'postgres')
        from utils.sql_dialect import date_format_sql
        assert date_format_sql('?', '%Y-%m-%d %H:%M') == "TO_CHAR(?::timestamp, 'YYYY-MM-DD HH24:MI')"

    def test_date_sql_sqlite(self, monkeypatch):
        self._set_db_type(monkeypatch, 'sqlite')
        from utils.sql_dialect import date_sql
        assert date_sql('timestamp') == "DATE(timestamp)"

    def test_date_sql_postgres(self, monkeypatch):
        self._set_db_type(monkeypatch, 'postgres')
        from utils.sql_dialect import date_sql
        assert date_sql('timestamp') == "timestamp::date"

    def test_epoch_sql_sqlite(self, monkeypatch):
        self._set_db_type(monkeypatch, 'sqlite')
        from utils.sql_dialect import epoch_sql
        assert epoch_sql('timestamp') == "strftime('%s', timestamp)"

    def test_epoch_sql_postgres(self, monkeypatch):
        self._set_db_type(monkeypatch, 'postgres')
        from utils.sql_dialect import epoch_sql
        assert epoch_sql('timestamp') == "EXTRACT(EPOCH FROM timestamp)"

    def test_insert_or_ignore_sqlite(self, monkeypatch):
        self._set_db_type(monkeypatch, 'sqlite')
        from utils.sql_dialect import insert_or_ignore_sql
        result = insert_or_ignore_sql('records', ['id', 'val'], 'id')
        assert result == "INSERT OR IGNORE INTO records (id, val) VALUES (?, ?)"

    def test_insert_or_ignore_postgres(self, monkeypatch):
        self._set_db_type(monkeypatch, 'postgres')
        from utils.sql_dialect import insert_or_ignore_sql
        result = insert_or_ignore_sql('records', ['id', 'val'], 'id')
        assert result == "INSERT INTO records (id, val) VALUES (%s, %s) ON CONFLICT (id) DO NOTHING"

    def test_bool_type_sqlite(self, monkeypatch):
        self._set_db_type(monkeypatch, 'sqlite')
        from utils.sql_dialect import bool_type
        assert bool_type() == "SMALLINT"

    def test_bool_type_postgres(self, monkeypatch):
        self._set_db_type(monkeypatch, 'postgres')
        from utils.sql_dialect import bool_type
        assert bool_type() == "SMALLINT"

    def test_double_type_sqlite(self, monkeypatch):
        self._set_db_type(monkeypatch, 'sqlite')
        from utils.sql_dialect import double_type
        assert double_type() == "REAL"

    def test_double_type_postgres(self, monkeypatch):
        self._set_db_type(monkeypatch, 'postgres')
        from utils.sql_dialect import double_type
        assert double_type() == "DOUBLE PRECISION"

    def test_timestamp_type_sqlite(self, monkeypatch):
        self._set_db_type(monkeypatch, 'sqlite')
        from utils.sql_dialect import timestamp_type
        assert timestamp_type() == "DATETIME"

    def test_timestamp_type_postgres(self, monkeypatch):
        self._set_db_type(monkeypatch, 'postgres')
        from utils.sql_dialect import timestamp_type
        assert timestamp_type() == "TIMESTAMP"

    def test_serial_pk_sqlite(self, monkeypatch):
        self._set_db_type(monkeypatch, 'sqlite')
        from utils.sql_dialect import serial_pk_sql
        assert serial_pk_sql() == "INTEGER PRIMARY KEY AUTOINCREMENT"

    def test_serial_pk_postgres(self, monkeypatch):
        self._set_db_type(monkeypatch, 'postgres')
        from utils.sql_dialect import serial_pk_sql
        assert serial_pk_sql() == "SERIAL PRIMARY KEY"

    def test_group_concat_sqlite(self, monkeypatch):
        self._set_db_type(monkeypatch, 'sqlite')
        from utils.sql_dialect import group_concat_sql
        assert group_concat_sql('type', ',') == "GROUP_CONCAT(type, ',')"

    def test_group_concat_postgres(self, monkeypatch):
        self._set_db_type(monkeypatch, 'postgres')
        from utils.sql_dialect import group_concat_sql
        result = group_concat_sql('type', ',')
        assert result == "STRING_AGG(type, ',')"

    def test_boolean_literal(self, monkeypatch):
        from utils.sql_dialect import boolean_literal

        self._set_db_type(monkeypatch, 'sqlite')
        assert boolean_literal(True) == "1"
        assert boolean_literal(False) == "0"

        self._set_db_type(monkeypatch, 'postgres')
        assert boolean_literal(True) == "TRUE"
        assert boolean_literal(False) == "FALSE"

    def test_pg_dsn_strips_plus_psycopg2(self):
        """_pg_dsn() 应去掉 sqlalchemy 风格的 +psycopg2"""
        import core.config as config
        from utils.db import _pg_dsn

        original = config.DATABASE_URL
        try:
            config.DATABASE_URL = "postgresql+psycopg2://user:pass@host/db"
            assert _pg_dsn() == "postgresql://user:pass@host/db"
        finally:
            config.DATABASE_URL = original


class TestCompatRow:
    """_CompatRow — 类型归一化"""

    def test_bool_to_int(self):
        from utils.db import _CompatRow

        row = _CompatRow({'is_predicted': True}, ['is_predicted'])
        assert row['is_predicted'] == 1
        assert row[0] == 1

    def test_datetime_to_string(self):
        from utils.db import _CompatRow

        row = _CompatRow(
            {'timestamp': datetime.datetime(2024, 6, 17, 14, 30, 0)},
            ['timestamp'],
        )
        assert row['timestamp'] == '2024-06-17 14:30:00'

    def test_int_column(self):
        from utils.db import _CompatRow

        row = _CompatRow({'id': 42}, ['id'])
        assert row['id'] == 42
        assert row[0] == 42

    def test_float_column(self):
        from utils.db import _CompatRow

        row = _CompatRow({'value': 6.5}, ['value'])
        assert row['value'] == 6.5

    def test_none_column(self):
        from utils.db import _CompatRow

        row = _CompatRow({'value': None}, ['value'])
        assert row['value'] is None

    def test_get_method(self):
        from utils.db import _CompatRow

        row = _CompatRow({'id': 1}, ['id'])
        assert row.get('id') == 1
        assert row.get('nonexistent', 'default') == 'default'

    def test_keys_values_items(self):
        from utils.db import _CompatRow

        row = _CompatRow({'a': True, 'b': datetime.datetime(2024, 1, 1, 0, 0, 0)}, ['a', 'b'])
        keys = list(row.keys())
        vals = list(row.values())
        items = list(row.items())
        assert set(keys) == {'a', 'b'}
        assert 1 in vals  # True -> 1
        assert '2024-01-01 00:00:00' in vals
        assert ('a', 1) in items

    def test_contains(self):
        from utils.db import _CompatRow

        row = _CompatRow({'id': 1}, ['id'])
        assert 'id' in row
        assert 'nonexistent' not in row


class TestConnectionWrapper:
    """ConnectionWrapper / CursorWrapper — SQLite 路径"""

    def test_connection_wrapper_init(self):
        from utils.db import ConnectionWrapper

        raw = sqlite3.connect(':memory:')
        raw.row_factory = sqlite3.Row
        wrapper = ConnectionWrapper(raw)
        assert wrapper._conn is raw
        assert wrapper._db_type == 'sqlite'

    def test_cursor_wrapper_execute(self):
        from utils.db import ConnectionWrapper

        raw = sqlite3.connect(':memory:')
        raw.row_factory = sqlite3.Row
        raw.execute("CREATE TABLE t (id INT, name TEXT)")
        wrapper = ConnectionWrapper(raw)
        c = wrapper.cursor()
        c.execute("INSERT INTO t VALUES (?, ?)", (1, 'test'))
        wrapper.commit()
        # CursorWrapper.execute 返回原始 cursor
        result = c.execute("SELECT name FROM t")
        row = result.fetchone()
        assert row['name'] == 'test'

    def test_cursor_wrapper_fetchall(self):
        from utils.db import ConnectionWrapper

        raw = sqlite3.connect(':memory:')
        raw.execute("CREATE TABLE t (id INT)")
        raw.execute("INSERT INTO t VALUES (1)")
        raw.execute("INSERT INTO t VALUES (2)")
        wrapper = ConnectionWrapper(raw)
        c = wrapper.cursor()
        rows = c.execute("SELECT * FROM t").fetchall()
        assert len(rows) == 2

    def test_executemany(self):
        from utils.db import ConnectionWrapper

        raw = sqlite3.connect(':memory:')
        raw.execute("CREATE TABLE t (id INT, name TEXT)")
        wrapper = ConnectionWrapper(raw)
        c = wrapper.cursor()
        c.executemany("INSERT INTO t VALUES (?, ?)", [(1, 'a'), (2, 'b')])
        wrapper.commit()
        assert len(c.execute("SELECT * FROM t").fetchall()) == 2

    def test_context_manager(self):
        from utils.db import ConnectionWrapper

        raw = sqlite3.connect(':memory:')
        raw.execute("CREATE TABLE t (id INT)")
        wrapper = ConnectionWrapper(raw)
        with wrapper:
            c = wrapper.cursor()
            c.execute("INSERT INTO t VALUES (1)")

    def test_connection_wrapper_getattr(self):
        from utils.db import ConnectionWrapper

        raw = sqlite3.connect(':memory:')
        raw.row_factory = sqlite3.Row
        wrapper = ConnectionWrapper(raw)
        assert hasattr(wrapper, 'total_changes')

    def test_connection_wrapper_setattr_non_conn(self):
        from utils.db import ConnectionWrapper

        raw = MagicMock()
        wrapper = ConnectionWrapper(raw)
        wrapper.some_attr = 42
        assert raw.some_attr == 42

    def test_connection_wrapper_setattr_conn(self):
        from utils.db import ConnectionWrapper

        raw = MagicMock()
        wrapper = ConnectionWrapper(raw)
        wrapper._db_type = 'sqlite'
        assert wrapper._db_type == 'sqlite'

    def test_cursor_iter(self):
        from utils.db import ConnectionWrapper

        raw = sqlite3.connect(':memory:')
        raw.row_factory = sqlite3.Row
        raw.execute("CREATE TABLE t (id INT)")
        raw.execute("INSERT INTO t VALUES (1)")
        raw.execute("INSERT INTO t VALUES (2)")
        wrapper = ConnectionWrapper(raw)
        c = wrapper.cursor()
        c.execute("SELECT id FROM t ORDER BY id")
        ids = [row['id'] for row in c]
        assert ids == [1, 2]

    def test_cursor_wrapper_iter_delegates(self):
        from utils.db import CursorWrapper

        mock_cur = MagicMock()
        c = CursorWrapper(mock_cur, 'sqlite')
        # iter() 不应抛异常
        result = iter(c)
        assert result is not None

    def test_cursor_wrapper_lastrowid_fallback(self):
        from utils.db import CursorWrapper

        mock_cur = MagicMock()
        mock_cur.lastrowid = 42
        c = CursorWrapper(mock_cur, 'sqlite')
        assert c.lastrowid == 42

    def test_cursor_wrapper_returning_exception(self):
        from utils.db import CursorWrapper

        mock_cur = MagicMock()
        mock_cur.lastrowid = None
        mock_cur.fetchone.side_effect = Exception("fetch error")
        c = CursorWrapper(mock_cur, 'sqlite')
        c.execute("INSERT INTO t VALUES (1) RETURNING id")
        assert c.lastrowid is None

    def test_cursor_wrapper_returning_row_fallback(self):
        """RETURNING 行没有 keys() 时 fallback 到 __getitem__（lines 98-101）"""
        from utils.db import CursorWrapper

        mock_cur = MagicMock()
        mock_cur.lastrowid = None
        mock_cur.fetchone.return_value = (42,)  # tuple, not sqlite3.Row
        c = CursorWrapper(mock_cur, 'sqlite')
        c.execute("INSERT INTO t VALUES (1) RETURNING id")
        assert c.lastrowid == 42

    def test_cursor_wrapper_returning_no_getitem(self):
        """RETURNING 行没有 __getitem__ 时直接赋值原始 row"""
        from utils.db import CursorWrapper

        class CustomRow:
            pass

        mock_cur = MagicMock()
        mock_cur.lastrowid = None
        custom = CustomRow()
        mock_cur.fetchone.return_value = custom
        c = CursorWrapper(mock_cur, 'sqlite')
        c.execute("INSERT INTO t VALUES (1) RETURNING id")
        assert c.lastrowid is custom

    def test_connection_wrapper_executemany(self):
        from utils.db import ConnectionWrapper

        raw = sqlite3.connect(':memory:')
        raw.execute("CREATE TABLE t (id INT, name TEXT)")
        wrapper = ConnectionWrapper(raw)
        wrapper.executemany("INSERT INTO t VALUES (?, ?)", [(1, 'a'), (2, 'b')])
        raw.row_factory = sqlite3.Row
        rows = raw.execute("SELECT * FROM t").fetchall()
        assert len(rows) == 2


class TestPgDsn:
    """_pg_dsn() — DSN 转换"""

    def test_normal_sqlalchemy_url(self, monkeypatch):
        import core.config as config

        monkeypatch.setattr(config, 'DATABASE_URL',
                            'postgresql+psycopg2://user:pass@host/db')
        monkeypatch.setattr(config, 'DB_TYPE', 'postgres')
        from utils.db import _pg_dsn
        assert _pg_dsn() == 'postgresql://user:pass@host/db'

    def test_already_postgresql_url(self, monkeypatch):
        import core.config as config

        monkeypatch.setattr(config, 'DATABASE_URL', 'postgresql://user:pass@host/db')
        monkeypatch.setattr(config, 'DB_TYPE', 'postgres')
        from utils.db import _pg_dsn
        assert _pg_dsn() == 'postgresql://user:pass@host/db'

    def test_empty_url(self, monkeypatch):
        import core.config as config

        monkeypatch.setattr(config, 'DATABASE_URL', '')
        monkeypatch.setattr(config, 'DB_TYPE', 'postgres')
        from utils.db import _pg_dsn
        assert _pg_dsn() == ''


class TestNormalizeValue:
    """_normalize_value() — 类型归一化"""

    def test_bool_returns_int(self):
        from utils.db import _normalize_value
        assert _normalize_value(True) == 1
        assert _normalize_value(False) == 0

    def test_datetime_returns_iso_string(self):
        from utils.db import _normalize_value
        dt = datetime.datetime(2024, 6, 17, 14, 30, 0)
        assert _normalize_value(dt) == '2024-06-17 14:30:00'

    def test_date_returns_iso_string(self):
        from utils.db import _normalize_value
        d = datetime.date(2024, 6, 17)
        assert _normalize_value(d) == '2024-06-17'

    def test_none_passthrough(self):
        from utils.db import _normalize_value
        assert _normalize_value(None) is None

    def test_int_passthrough(self):
        from utils.db import _normalize_value
        assert _normalize_value(42) == 42

    def test_float_passthrough(self):
        from utils.db import _normalize_value
        assert _normalize_value(3.14) == 3.14


class TestPgPathsMocked:
    """PostgreSQL 代码路径（mock psycopg2）"""

    def test_get_pool_import_error(self, monkeypatch):
        """_get_pool 在没有 psycopg2 时抛 ImportError"""
        import core.config as config

        monkeypatch.setattr(config, 'DB_TYPE', 'postgres')
        import utils.db as db_mod
        db_mod._connection_pool = None
        monkeypatch.setitem(db_mod.__dict__, 'pool', None)
        monkeypatch.setitem(db_mod.__dict__, 'RealDictCursor', None)
        with pytest.raises(ImportError):
            db_mod._get_pool()

    def test_get_pool_creates_pool(self, monkeypatch):
        """_get_pool 正常创建连接池"""
        import core.config as config

        monkeypatch.setattr(config, 'DB_TYPE', 'postgres')
        monkeypatch.setattr(config, 'DATABASE_URL', 'postgresql://user:pass@host/db')
        import utils.db as db_mod
        db_mod._connection_pool = None

        with patch('psycopg2.pool.ThreadedConnectionPool') as mock_cls:
            mock_pool = MagicMock()
            mock_cls.return_value = mock_pool
            pool = db_mod._get_pool()
            assert pool is mock_pool
            call_kwargs = mock_cls.call_args[1]
            assert call_kwargs.get('client_encoding') == 'UTF8'
            assert call_kwargs.get('options') == '-c timezone=Asia/Shanghai'

    def test_get_pool_cached(self, monkeypatch):
        """_get_pool 重复调用返回缓存"""
        import core.config as config

        monkeypatch.setattr(config, 'DB_TYPE', 'postgres')
        import utils.db as db_mod
        mock_pool = MagicMock()
        db_mod._connection_pool = mock_pool
        assert db_mod._get_pool() is mock_pool

    def test_pg_connection_wrapper(self, monkeypatch):
        """_PgConnectionWrapper 基本方法"""
        import core.config as config

        monkeypatch.setattr(config, 'DB_TYPE', 'postgres')
        from utils.db import _PgConnectionWrapper

        mock_conn = MagicMock()
        mock_pool = MagicMock()
        wrapper = _PgConnectionWrapper(mock_conn, mock_pool)
        assert wrapper._conn is mock_conn

        c = wrapper.cursor()
        assert c is not None

        wrapper.commit()
        mock_conn.commit.assert_called_once()
        wrapper.rollback()
        mock_conn.rollback.assert_called_once()

        wrapper.close()
        mock_pool.putconn.assert_called_once_with(mock_conn)

        mock_conn.reset_mock()
        mock_pool.reset_mock()
        with wrapper:
            pass
        mock_conn.commit.assert_called_once()
        mock_conn.rollback.assert_not_called()

        mock_conn.reset_mock()
        mock_pool.reset_mock()
        wrapper2 = _PgConnectionWrapper(mock_conn, mock_pool)
        try:
            with wrapper2:
                raise ValueError("test error")
        except ValueError:
            pass
        mock_conn.rollback.assert_called_once()

    def test_get_db_postgres(self, monkeypatch):
        """get_db() 在 PostgreSQL 模式下返回 _PgConnectionWrapper"""
        import core.config as config

        monkeypatch.setattr(config, 'DB_TYPE', 'postgres')
        monkeypatch.setattr(config, 'DATABASE_URL', 'postgresql://u:p@h/d')
        import utils.db as db_mod
        db_mod._connection_pool = None

        mock_conn = MagicMock()
        mock_pool = MagicMock()
        mock_pool.getconn.return_value = mock_conn

        with patch('psycopg2.pool.ThreadedConnectionPool',
                   return_value=mock_pool):
            from app import app
            with app.app_context():
                from flask import g
                if '_database' in g:
                    del g._database
                db = db_mod.get_db()
                assert hasattr(db, '_conn')
                assert db._conn is mock_conn

    def test_get_raw_conn_postgres(self, monkeypatch):
        """get_raw_conn() 在 PostgreSQL 模式下返回 _PgConnectionWrapper"""
        import core.config as config

        monkeypatch.setattr(config, 'DB_TYPE', 'postgres')
        monkeypatch.setattr(config, 'DATABASE_URL', 'postgresql://u:p@h/d')
        import utils.db as db_mod
        db_mod._connection_pool = None

        mock_conn = MagicMock()
        mock_pool = MagicMock()
        mock_pool.getconn.return_value = mock_conn

        with patch('psycopg2.pool.ThreadedConnectionPool',
                   return_value=mock_pool):
            db = db_mod.get_raw_conn()
            assert hasattr(db, '_conn')

    def test_compat_cursor_execute_returning(self, monkeypatch):
        """_CompatCursor 的 RETURNING 子句处理"""
        import core.config as config

        monkeypatch.setattr(config, 'DB_TYPE', 'postgres')
        from utils.db import _CompatCursor

        mock_cur = MagicMock()
        mock_cur.description = [('id', None, None, None, None, None, None)]
        mock_cur.fetchone.return_value = {'id': 42}
        c = _CompatCursor(mock_cur)
        c.execute("INSERT INTO t VALUES (1) RETURNING id")
        assert c.lastrowid == 42

    def test_compat_cursor_executemany(self, monkeypatch):
        """_CompatCursor.executemany()"""
        import core.config as config

        monkeypatch.setattr(config, 'DB_TYPE', 'sqlite')
        from utils.db import _CompatCursor

        mock_cur = MagicMock()
        c = _CompatCursor(mock_cur)
        sql = "INSERT INTO t VALUES (?, ?)"
        params = [(1, 'a'), (2, 'b')]
        c.executemany(sql, params)
        mock_cur.executemany.assert_called_once()

    def test_compat_cursor_fetchone_returns_none(self, monkeypatch):
        """_CompatCursor.fetchone() 返回 None（line 336）"""
        import core.config as config

        monkeypatch.setattr(config, 'DB_TYPE', 'postgres')
        from utils.db import _CompatCursor

        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = None
        c = _CompatCursor(mock_cur)
        assert c.fetchone() is None

    def test_compat_cursor_fetchall_returns_empty(self, monkeypatch):
        """_CompatCursor.fetchall() 返回空列表（line 343）"""
        import core.config as config

        monkeypatch.setattr(config, 'DB_TYPE', 'postgres')
        from utils.db import _CompatCursor

        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = []
        c = _CompatCursor(mock_cur)
        assert c.fetchall() == []

    def test_compat_cursor_fetchone_returns_row(self, monkeypatch):
        """_CompatCursor.fetchone() 返回 _CompatRow"""
        import core.config as config

        monkeypatch.setattr(config, 'DB_TYPE', 'postgres')
        from utils.db import _CompatCursor

        mock_cur = MagicMock()
        mock_cur.description = [('id', None, None, None, None, None, None),
                                ('name', None, None, None, None, None, None)]
        mock_cur.fetchone.return_value = {'id': 1, 'name': 'test'}
        c = _CompatCursor(mock_cur)
        row = c.fetchone()
        assert row['id'] == 1
        assert row['name'] == 'test'

    def test_compat_cursor_execute_with_params(self, monkeypatch):
        """_CompatCursor.execute() 传参时走 _inline_params（lines 313-314）"""
        import core.config as config

        monkeypatch.setattr(config, 'DB_TYPE', 'postgres')
        from utils.db import _CompatCursor

        mock_cur = MagicMock()
        mock_cur.description = [('id', None, None, None, None, None, None)]
        mock_cur.fetchone.return_value = {'id': 1}
        c = _CompatCursor(mock_cur)
        # 传参触发 _inline_params
        c.execute("INSERT INTO t VALUES (?, ?)", (1, 'test'))
        mock_cur.execute.assert_called_once()

    def test_compat_cursor_returning_exception(self, monkeypatch):
        """RETURNING 行 fetchone 抛异常时 silent 处理（lines 325-326）"""
        import core.config as config

        monkeypatch.setattr(config, 'DB_TYPE', 'postgres')
        from utils.db import _CompatCursor

        mock_cur = MagicMock()
        mock_cur.lastrowid = None
        mock_cur.fetchone.side_effect = Exception("fetch error")
        c = _CompatCursor(mock_cur)
        # 不应抛异常
        c.execute("INSERT INTO t VALUES (1) RETURNING id")
        assert c.lastrowid is None

    def test_convert_sqlite_to_pg_datetime_quoted_expr(self):
        """_convert_sqlite_to_pg datetime 表达式的引号剥离（line 280）"""
        from utils.db import _convert_sqlite_to_pg

        # 带引号的 date 表达式
        sql = "SELECT * FROM t WHERE ts > datetime('2024-01-01', '-7 days')"
        result = _convert_sqlite_to_pg(sql)
        assert "::timestamp - INTERVAL '7 days'" in result

    def test_init_db_postgres(self, monkeypatch):
        """_init_db_postgres() 执行时不抛异常（mock psycopg2.connect）"""
        import core.config as config

        monkeypatch.setattr(config, 'DB_TYPE', 'postgres')
        import utils.db as db_mod

        mock_conn = MagicMock()
        with patch('psycopg2.connect', return_value=mock_conn):
            db_mod._init_db_postgres()
            import psycopg2
            psycopg2.connect.assert_called_once()

    def test_init_db_postgres_error(self, monkeypatch):
        """_init_db_postgres() 异常路径（lines 631-634）"""
        import core.config as config
        monkeypatch.setattr(config, 'DB_TYPE', 'postgres')
        import utils.db as db_mod
        mock_conn = MagicMock()
        mock_conn.cursor.side_effect = Exception("DDL error")
        with patch('psycopg2.connect', return_value=mock_conn), patch('builtins.print'):
            db_mod._init_db_postgres()

    def test_init_db_dispatches_to_postgres(self, monkeypatch):
        """init_db() 在 PG 模式下调用 _init_db_postgres()（line 448）"""
        import core.config as config
        monkeypatch.setattr(config, 'DB_TYPE', 'postgres')
        import utils.db as db_mod
        mock_conn = MagicMock()
        with patch('psycopg2.connect', return_value=mock_conn):
            with patch('builtins.print'):
                db_mod.init_db()
            import psycopg2
            psycopg2.connect.assert_called_once()

    def test_pg_conn_wrapper_methods(self, monkeypatch):
        """_PgConnectionWrapper execute/executemany 委托"""
        import core.config as config
        monkeypatch.setattr(config, 'DB_TYPE', 'postgres')
        from utils.db import _PgConnectionWrapper

        mock_conn = MagicMock()
        mock_pool = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        wrapper = _PgConnectionWrapper(mock_conn, mock_pool)
        wrapper.execute("SELECT 1")
        mock_cur.execute.assert_called()
        wrapper.executemany("INSERT INTO t VALUES (%s, %s)", [(1, 'a'), (2, 'b')])
        mock_cur.executemany.assert_called()
        c = wrapper.cursor()
        assert c is not None
        _ = iter(c)
        c.fetchone()
        c.fetchall()
        _ = c.lastrowid
        _ = c.any_attr
