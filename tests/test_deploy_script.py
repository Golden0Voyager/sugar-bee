"""scripts/ops/deploy_app.sh 守卫。

部署脚本的价值全在"哪些东西绝对不能推上去"。这些断言就是那份清单：
数据目录、密钥、本地大文件一旦漏掉排除，一次手滑就能把线上库覆盖成旧副本。
"""
import os
import subprocess

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO_ROOT, 'scripts', 'ops', 'deploy_app.sh')

# 必须被排除的路径（相对仓库根）
MUST_EXCLUDE = [
    'db_data/',
    'backups/',
    '.env',
    '.garmin_tokens/',
    'user_data/',
    'static/avatars/',
    '*.db',
    '*.db-wal',
    '*.db-shm',
    '.git/',
    '.worktrees/',
]


@pytest.fixture(scope='module')
def script_text():
    with open(SCRIPT, encoding='utf-8') as f:
        return f.read()


def test_script_exists_and_is_executable():
    assert os.path.isfile(SCRIPT)
    assert os.access(SCRIPT, os.X_OK), 'deploy_app.sh 必须可执行'


def test_bash_syntax_ok():
    result = subprocess.run(['bash', '-n', SCRIPT], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('pattern', MUST_EXCLUDE)
def test_excludes_data_and_secrets(script_text, pattern):
    # 脚本里可能带引号（--exclude='*.db'），两种写法都算
    assert (f'--exclude={pattern}' in script_text
            or f"--exclude='{pattern}'" in script_text), f'deploy 脚本必须排除 {pattern}'


def test_dry_run_needs_no_server_and_changes_nothing():
    """--dry-run 不连服务器、不需要 SB_HOST，也不写任何文件。"""
    env = {k: v for k, v in os.environ.items() if not k.startswith('SB_')}
    result = subprocess.run(
        ['bash', SCRIPT, '--dry-run'],
        capture_output=True, text=True, env=env, cwd=REPO_ROOT,
    )
    assert result.returncode == 0, result.stderr
    assert 'dry-run' in result.stdout
    assert 'rsync' in result.stdout


def test_requires_host_without_dry_run(tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith('SB_')}
    env['HOME'] = str(tmp_path)          # 屏蔽 ~/.sugar_bee_host 的兜底
    result = subprocess.run(
        ['bash', SCRIPT], capture_output=True, text=True, env=env, cwd=REPO_ROOT,
    )
    assert result.returncode != 0
    assert 'SB_HOST' in result.stderr


def test_compares_db_fingerprint_before_and_after(script_text):
    """推送前后必须比对线上库指纹，不一致就中止。"""
    assert 'db_fingerprint' in script_text
    assert 'COUNT=' in script_text and 'MTIME=' in script_text
    assert '线上库指纹变了' in script_text
