"""scripts/ops/backup_db.py 测试。

两个目的：
  1. 行为：make_backup() 产出的事务一致快照真的可用（quick_check + 行数）；
  2. 护栏：这个文件必须保持 **stdlib-only**（宿主机裸 python3 跑，无 venv），
     谁 import 了 app / 第三方库，这条测试当场红。
"""
import ast
import importlib.util
import os
import sqlite3

import pytest

BACKUP_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'scripts', 'ops', 'backup_db.py',
)

# 允许的 stdlib 白名单：改这里等于放宽约束，必须先想清楚宿主机有没有这个库
STDLIB_ALLOW = {
    'datetime', 'glob', 'os', 'sqlite3', 'sys', 'zoneinfo', 'time', 'shutil', 'pathlib',
}


def load_backup_module():
    spec = importlib.util.spec_from_file_location('sb_backup_db_under_test', BACKUP_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def src_db(tmp_path):
    """一个带 records 表的源库。"""
    path = tmp_path / 'src.db'
    conn = sqlite3.connect(path)
    conn.execute('CREATE TABLE records (id INTEGER PRIMARY KEY, v TEXT)')
    conn.executemany('INSERT INTO records (v) VALUES (?)', [('a',), ('b',), ('c',)])
    conn.commit()
    conn.close()
    return path


def test_make_backup_explicit_dest(tmp_path, src_db):
    mod = load_backup_module()
    dest = tmp_path / 'snap.db'

    path, size, records = mod.make_backup(dest_path=str(dest), src_path=str(src_db))

    assert path == str(dest)
    assert dest.exists()
    assert size == os.path.getsize(dest)
    assert records == 3


def test_make_backup_default_dated_name(tmp_path, src_db, monkeypatch):
    mod = load_backup_module()
    monkeypatch.setattr(mod, 'SRC', str(src_db))
    monkeypatch.setattr(mod, 'DST_DIR', str(tmp_path / 'backups'))

    path, _size, records = mod.make_backup()

    assert os.path.basename(path).startswith('glucose_snap_')
    assert records == 3


def test_make_backup_overwrites_stale_dest(tmp_path, src_db):
    """目标已存在时必须先删，否则旧页会混进新快照。"""
    mod = load_backup_module()
    dest = tmp_path / 'snap.db'
    dest.write_bytes(b'garbage-not-a-db')

    path, _size, records = mod.make_backup(dest_path=str(dest), src_path=str(src_db))

    assert records == 3
    assert sqlite3.connect(path).execute('select count(*) from records').fetchone()[0] == 3


def test_make_backup_missing_source(tmp_path):
    mod = load_backup_module()
    with pytest.raises(FileNotFoundError):
        mod.make_backup(dest_path=str(tmp_path / 'x.db'), src_path=str(tmp_path / 'nope.db'))


def test_verify_snapshot_rejects_broken_file(tmp_path):
    mod = load_backup_module()
    bad = tmp_path / 'bad.db'
    bad.write_bytes(b'not a database at all')

    with pytest.raises((RuntimeError, sqlite3.DatabaseError)):
        mod.verify_snapshot(str(bad))


def test_make_snapshot_keeps_old_signature(tmp_path, src_db, monkeypatch):
    mod = load_backup_module()
    monkeypatch.setattr(mod, 'SRC', str(src_db))
    monkeypatch.setattr(mod, 'DST_DIR', str(tmp_path))

    path, size, records = mod.make_snapshot()

    assert os.path.exists(path)
    assert size > 0
    assert records == 3


def test_backup_module_is_stdlib_only():
    """宿主机裸 python3 运行，禁止 app / 第三方依赖。"""
    with open(BACKUP_PATH, encoding='utf-8') as f:
        tree = ast.parse(f.read())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split('.')[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                imported.add(node.module.split('.')[0])

    illegal = imported - STDLIB_ALLOW
    assert not illegal, f'backup_db.py 引入了非 stdlib 依赖: {sorted(illegal)}'
