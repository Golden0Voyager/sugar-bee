"""Octop 配置库本地加密冷备的守卫测试。

P0 的核心约束不是「能备份」，而是「**绝不能把含活凭据的库传上公网**」：
Golden0Voyager/sugar-bee 是 public 仓库，octop.db 里有微信 iLink bot token、
飞书 app_secret、password_hash 和 secrets 表。用户 2026-11-12 选择本地留存。

这些断言就是那份清单，谁把上传逻辑加回来，测试当场红。
"""
import importlib.util
import os
import sqlite3
import subprocess
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OPS = os.path.join(REPO_ROOT, 'scripts', 'ops')
SCRIPT = os.path.join(OPS, 'backup_octop_cold.sh')
BACKUP_DB = os.path.join(OPS, 'backup_db.py')
INSTALL_OPS = os.path.join(OPS, 'install_ops.sh')

# 一旦出现这些字样，说明有人把凭据往公网搬，必须拦住
FORBIDDEN = [
    'api.github.com',
    '.github_token',
    'releases',
    'curl -X POST',
    'upload_url',
    'browser_download_url',
]


@pytest.fixture(scope='module')
def script_text():
    with open(SCRIPT, encoding='utf-8') as f:
        return f.read()


@pytest.fixture(scope='module')
def backup_module():
    spec = importlib.util.spec_from_file_location('sb_backup_db_cold_test', BACKUP_DB)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_script_exists_and_is_executable():
    assert os.path.isfile(SCRIPT)
    assert os.access(SCRIPT, os.X_OK), 'backup_octop_cold.sh 必须可执行'


@pytest.mark.parametrize('script', [SCRIPT, BACKUP_DB, INSTALL_OPS])
def test_no_syntax_errors(script):
    if script.endswith('.sh'):
        result = subprocess.run(['bash', '-n', script], capture_output=True, text=True)
    else:
        result = subprocess.run([sys.executable, '-m', 'py_compile', script],
                                capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('token', FORBIDDEN)
def test_never_uploads_to_public_github(script_text, token):
    assert token not in script_text, f'本地冷备脚本绝不能出现 {token}'


def test_uses_the_single_online_backup_implementation(script_text):
    """不能自己再写一份拷贝逻辑，必须调 backup_db.py --cold。

    只看「真正会执行的行」，注释里解释为什么不能直接 cp 的句子不算违规。
    """
    assert '--cold' in script_text
    assert 'backup_db.py' in script_text
    code_lines = [ln.strip() for ln in script_text.splitlines()
                  if ln.strip() and not ln.strip().startswith('#')]
    assert not any(ln.startswith('cp ') or 'shutil.copy' in ln for ln in code_lines), \
        '不许直接 cp / shutil.copy 活库，必须走 Online Backup API'


def test_verifies_by_decrypting_back(script_text):
    """只检查文件存在不算验证，必须解回一个能 integrity_check 的库。"""
    assert 'openssl enc -d' in script_text
    assert 'integrity_check' in script_text
    assert 'users' in script_text
    assert 'SNAP_ROWS' in script_text


def test_plaintext_snapshot_is_cleaned_up(script_text):
    assert "trap 'rm -rf \"$TMP\"'" in script_text
    assert 'mktemp -d' in script_text


def test_dry_run_does_not_persist(script_text):
    assert '--dry-run' in script_text
    assert '未落盘' in script_text


def test_install_ops_registers_script_and_cron():
    with open(INSTALL_OPS, encoding='utf-8') as f:
        text = f.read()
    assert 'backup_octop_cold.sh' in text
    assert 'bash -n backup_octop_cold.sh' in text
    assert '25 3 * * * /opt/sugar_bee/backup_octop_cold.sh' in text
    assert 'chmod 755' in text and 'backup_octop_cold.sh' in text


# ---- backup_db.py --cold 的行为 ----

@pytest.fixture
def wal_db(tmp_path):
    """造一个 WAL 模式的活库，带 users 表、不带 records 表。"""
    path = tmp_path / 'octop.db'
    con = sqlite3.connect(path)
    con.execute('pragma journal_mode=WAL')
    con.execute('CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)')
    con.execute('CREATE TABLE agents (id TEXT)')
    con.execute('CREATE TABLE channels (id TEXT)')
    con.execute('CREATE TABLE settings (k TEXT, v TEXT)')
    con.executemany('INSERT INTO users (name) VALUES (?)',
                    [('dad',), ('me',), ('erin',)])
    con.commit()
    # 留一个未 checkpoint 的事务在 -wal 里，模拟真实活库
    con.execute("INSERT INTO users (name) VALUES ('uncommitted-in-wal')")
    con.commit()
    yield path
    con.close()


def test_cold_snapshot_is_self_contained(backup_module, wal_db, tmp_path):
    dest = tmp_path / 'cold' / 'octop.db'
    path, size, rows = backup_module.make_cold_snapshot(str(wal_db), str(dest), 'users')

    assert os.path.isfile(path) and size > 0
    assert rows == 4
    # 快照必须是自包含单文件：切回 DELETE 模式，不带 -wal 也能读全
    con = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    assert con.execute('pragma integrity_check').fetchone()[0] == 'ok'
    assert con.execute('pragma journal_mode').fetchone()[0].lower() == 'delete'
    assert con.execute('select count(*) from users').fetchone()[0] == 4
    assert 'uncommitted-in-wal' in [r[0] for r in con.execute('select name from users')]
    con.close()
    assert not os.path.exists(str(dest) + '-wal')


def test_cold_snapshot_rejects_sql_injection_in_table_name(backup_module, wal_db, tmp_path):
    with pytest.raises(ValueError):
        backup_module.make_cold_snapshot(
            str(wal_db), str(tmp_path / 'x.db'), 'users; DROP TABLE users')


def test_cold_snapshot_default_table_is_users(backup_module, wal_db, tmp_path):
    _path, _size, rows = backup_module.make_cold_snapshot(
        str(wal_db), str(tmp_path / 'default.db'))
    assert rows == 4


def test_cold_cli_reports_rows(tmp_path, wal_db):
    dest = tmp_path / 'cli.db'
    result = subprocess.run(
        [sys.executable, BACKUP_DB, '--cold', str(wal_db), str(dest), 'users'],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert 'ROWS=4' in result.stdout
    assert dest.exists()


def test_cold_cli_rejects_bad_args():
    result = subprocess.run([sys.executable, BACKUP_DB, '--cold', 'only-two'],
                            capture_output=True, text=True)
    assert result.returncode == 2
    assert '用法' in result.stderr


def test_make_backup_still_uses_records_by_default(backup_module, tmp_path):
    """新增 --cold 不能改变 glucose 原有路径（默认表名必须还是 records）。"""
    src = tmp_path / 'glucose.db'
    con = sqlite3.connect(src)
    con.execute('CREATE TABLE records (id INTEGER PRIMARY KEY)')
    con.executemany('INSERT INTO records VALUES (?)', [(1,), (2,), (3,)])
    con.commit()
    con.close()
    _path, _size, rows = backup_module.make_backup(
        dest_path=str(tmp_path / 'snap.db'), src_path=str(src))
    assert rows == 3


# ---- 端到端：伪造一套环境，真跑一次 --dry-run ----

def test_dry_run_end_to_end_without_network(tmp_path, wal_db):
    root = tmp_path / 'root'
    root.mkdir()
    (root / 'backup_db.py').write_bytes(open(BACKUP_DB, 'rb').read())
    pass_file = tmp_path / 'pass'
    pass_file.write_text('a-test-only-passphrase-not-a-real-secret\n')
    cold_dir = tmp_path / 'cold_store'
    scratch = tmp_path / 'scratch'
    scratch.mkdir()

    env = dict(os.environ)
    env.update({
        'SB_ROOT': str(root),
        'SB_OCTOP_DB': str(wal_db),
        'SB_PASS_FILE': str(pass_file),
        'SB_COLD_DIR': str(cold_dir),
        'SB_BARK_KEY_FILE': str(tmp_path / 'no-such-bark-key'),
        'TMPDIR': str(scratch),
    })
    result = subprocess.run(['bash', SCRIPT, '--dry-run'],
                            capture_output=True, text=True, env=env, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert '回读验证通过' in result.stdout
    assert 'dry-run' in result.stdout
    assert not cold_dir.exists(), 'dry-run 绝不能落盘'
    # 明文快照/解密回读文件只存在于临时目录，trap 必须把它们全删掉
    assert list(scratch.iterdir()) == [], '临时目录里残留了明文快照'


def test_real_run_produces_encrypted_artifact(tmp_path, wal_db):
    root = tmp_path / 'root'
    root.mkdir()
    (root / 'backup_db.py').write_bytes(open(BACKUP_DB, 'rb').read())
    pass_file = tmp_path / 'pass'
    pass_file.write_text('a-test-only-passphrase-not-a-real-secret\n')
    cold_dir = tmp_path / 'cold_store'

    env = dict(os.environ)
    env.update({
        'SB_ROOT': str(root),
        'SB_OCTOP_DB': str(wal_db),
        'SB_PASS_FILE': str(pass_file),
        'SB_COLD_DIR': str(cold_dir),
        'SB_BARK_KEY_FILE': str(tmp_path / 'no-such-bark-key'),
    })
    result = subprocess.run(['bash', SCRIPT],
                            capture_output=True, text=True, env=env, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assets = list(cold_dir.glob('octop_*.db.gz.enc'))
    assert len(assets) == 1
    assert assets[0].stat().st_size > 0
    assert oct(assets[0].stat().st_mode & 0o777) == '0o600'
    assert oct(cold_dir.stat().st_mode & 0o777) == '0o700'
