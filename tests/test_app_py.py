"""app.py 覆盖率测试 (48% → ~85%)

测试策略:
  - index 路由: is_garmin_user, prediction_running, exception
  - auto_backup: 正常备份, DB不存在, 旧备份清理, 异常
  - auto_garmin_sync: 无用户, 无token, 同步成功, 锁冲突, token_dir
  - start_background_tasks: 启动/带GARMIN
"""
import datetime
import os
import tempfile
import threading
from unittest.mock import DEFAULT, MagicMock, patch

# ============================================================
# index 路由 — 预测锁 + Garmin 用户 + 异常
# ============================================================

class TestIndexRoute:
    """app.index 路由: 未覆盖分支（mock render_template 避免模板依赖 stats 字段）"""

    def _patch_index(self):
        """返回所有 index 路由依赖的 mock 上下文管理器"""
        return patch.multiple(
            'app',
            render_template=DEFAULT,
            build_timeline=DEFAULT,
            get_dashboard_stats=DEFAULT,
            user_manager=DEFAULT,
            settings=DEFAULT,
            predict_morning_fpg=DEFAULT,
            predict_post_exercise_glucose=DEFAULT,
        )

    def test_index_is_garmin_user(self, isolate_db, client_authenticated):
        with patch.dict('os.environ', {'GARMIN_USER_ID': '1', 'GARMIN_EMAIL': 'test@test.com'}), \
             self._patch_index() as mocks:
            mocks['build_timeline'].return_value = ([], [])
            mocks['get_dashboard_stats'].return_value = {'user': {'avatar': ''}}
            mocks['user_manager'].get_user.return_value = {'enabled_modules': ['glucose']}
            mocks['settings'].USER_EMOJI_MAP = {}
            mocks['render_template'].return_value = 'rendered'

            result = client_authenticated.get('/')
            assert result.status_code == 200
            args, kwargs = mocks['render_template'].call_args
            assert kwargs['is_garmin_user'] is True

    def test_index_prediction_already_running(self, client_authenticated):
        from app import _prediction_running
        _prediction_running.add(1)
        try:
            with patch.dict('os.environ', {'GARMIN_USER_ID': '0', 'GARMIN_EMAIL': ''}), \
                 self._patch_index() as mocks:
                mocks['build_timeline'].return_value = ([], [])
                mocks['get_dashboard_stats'].return_value = {'user': {'avatar': ''}}
                mocks['user_manager'].get_user.return_value = {'enabled_modules': ['glucose']}
                mocks['settings'].USER_EMOJI_MAP = {}
                mocks['render_template'].return_value = 'rendered'

                result = client_authenticated.get('/')
                assert result.status_code == 200
                args, kwargs = mocks['render_template'].call_args
                assert kwargs['is_garmin_user'] is False
        finally:
            _prediction_running.discard(1)

    def test_index_exception(self, client_authenticated):
        with patch('app.get_db', side_effect=Exception("db crash")):
            result = client_authenticated.get('/')
            assert result.status_code == 500

    def test_index_with_garmin_uid_zero(self, client_authenticated):
        with patch.dict('os.environ', {'GARMIN_USER_ID': '0', 'GARMIN_EMAIL': ''}), \
             self._patch_index() as mocks:
            mocks['build_timeline'].return_value = ([], [])
            mocks['get_dashboard_stats'].return_value = {'user': {'avatar': ''}}
            mocks['user_manager'].get_user.return_value = {'enabled_modules': []}
            mocks['settings'].USER_EMOJI_MAP = {}
            mocks['render_template'].return_value = 'rendered'

            result = client_authenticated.get('/')
            assert result.status_code == 200
            args, kwargs = mocks['render_template'].call_args
            assert kwargs['is_garmin_user'] is False

    def test_index_no_enabled_modules(self, client_authenticated):
        with self._patch_index() as mocks:
            mocks['build_timeline'].return_value = ([], [])
            mocks['get_dashboard_stats'].return_value = {'user': {'avatar': ''}}
            mocks['user_manager'].get_user.return_value = None
            mocks['settings'].USER_EMOJI_MAP = {}
            mocks['render_template'].return_value = 'rendered'

            result = client_authenticated.get('/')
            assert result.status_code == 200
            args, kwargs = mocks['render_template'].call_args
            assert kwargs['enabled_modules'] == ['glucose', 'blood_pressure', 'exercise', 'weight', 'medication']


# ============================================================
# auto_backup
# ============================================================

class TestAutoBackup:
    """auto_backup 函数"""

    def test_backup_creates_file(self, monkeypatch):
        from app import auto_backup
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, 'test.db')
            with open(db_path, 'w') as f:
                f.write('test data')
            monkeypatch.setattr('app.DB_NAME', db_path)
            monkeypatch.setattr('app.AUTO_BACKUP_DIR', os.path.join(tmp, 'backups'))
            # Prevent timer from starting
            monkeypatch.setattr('app._auto_backup_timer', MagicMock())
            backup_started = [False]

            def fake_timer(*args, **kwargs):
                backup_started[0] = True
                return MagicMock()
            monkeypatch.setattr(threading, 'Timer', fake_timer)

            auto_backup()
            backup_dir = os.path.join(tmp, 'backups')
            files = os.listdir(backup_dir)
            assert len(files) == 1
            assert files[0].endswith('.db')

    def test_backup_db_not_exists(self, monkeypatch):
        from app import auto_backup
        monkeypatch.setattr('app.DB_NAME', '/nonexistent/db.db')
        monkeypatch.setattr('app._auto_backup_timer', MagicMock())
        backup_called = [False]
        def fake_timer(*args, **kwargs):
            backup_called[0] = True
            return MagicMock()
        monkeypatch.setattr(threading, 'Timer', fake_timer)

        auto_backup()
        assert backup_called[0]

    def test_backup_skip_if_exists(self, monkeypatch):
        from app import auto_backup
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, 'test.db')
            with open(db_path, 'w') as f:
                f.write('test data')
            backup_dir = os.path.join(tmp, 'backups')
            os.makedirs(backup_dir)
            today = datetime.date.today().strftime('%Y%m%d')
            existing = os.path.join(backup_dir, f'glucose_auto_{today}.db')
            with open(existing, 'w') as f:
                f.write('old')
            monkeypatch.setattr('app.DB_NAME', db_path)
            monkeypatch.setattr('app.AUTO_BACKUP_DIR', backup_dir)
            monkeypatch.setattr('app._auto_backup_timer', MagicMock())
            monkeypatch.setattr(threading, 'Timer', lambda *a, **kw: MagicMock())

            auto_backup()
            # file should still contain 'old' (not overwritten)
            with open(existing) as f:
                assert f.read() == 'old'

    def test_backup_cleans_old_files(self, monkeypatch):
        from app import auto_backup
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, 'test.db')
            with open(db_path, 'w') as f:
                f.write('test data')
            backup_dir = os.path.join(tmp, 'backups')
            os.makedirs(backup_dir)
            old_date = (datetime.date.today() - datetime.timedelta(days=31)).strftime('%Y%m%d')
            old_file = os.path.join(backup_dir, f'glucose_auto_{old_date}.db')
            with open(old_file, 'w') as f:
                f.write('old data')

            monkeypatch.setattr('app.DB_NAME', db_path)
            monkeypatch.setattr('app.AUTO_BACKUP_DIR', backup_dir)
            monkeypatch.setattr('app._auto_backup_timer', MagicMock())
            monkeypatch.setattr(threading, 'Timer', lambda *a, **kw: MagicMock())

            auto_backup()
            remaining = [f for f in os.listdir(backup_dir) if f.endswith('.db')]
            assert old_file.split('/')[-1] not in remaining


# ============================================================
# auto_garmin_sync
# ============================================================

class TestAutoGarminSync:
    """auto_garmin_sync 函数"""

    def test_sync_no_user_id(self, monkeypatch):
        from app import auto_garmin_sync
        monkeypatch.setattr('os.environ', {})
        monkeypatch.setattr('app._garmin_sync_timer', MagicMock())
        monkeypatch.setattr(threading, 'Timer', lambda *a, **kw: MagicMock())
        # Should just schedule next run and return
        auto_garmin_sync()

    def test_sync_no_token_file(self, monkeypatch):
        from app import auto_garmin_sync
        with tempfile.TemporaryDirectory():
            monkeypatch.setattr('os.environ', {
                'GARMIN_USER_ID': '1',
                'GARMIN_EMAIL': 'test@test.com',
            })
            monkeypatch.setattr('app._garmin_sync_timer', MagicMock())
            monkeypatch.setattr(threading, 'Timer', lambda *a, **kw: MagicMock())
            # Point token dir to empty temp dir
            monkeypatch.setattr('app.os.path.isfile', lambda p: False)
            auto_garmin_sync()

    def test_sync_success(self, monkeypatch):
        from app import auto_garmin_sync
        with tempfile.TemporaryDirectory() as tmp:
            token_dir = os.path.join(tmp, '.tokens')
            os.makedirs(token_dir)
            token_file = os.path.join(token_dir, 'garmin_tokens.json')
            with open(token_file, 'w') as f:
                f.write('{}')

            monkeypatch.setattr('os.environ', {
                'GARMIN_USER_ID': '1',
                'GARMIN_EMAIL': 'test@test.com',
                'GARMIN_TOKEN_DIR': token_dir,
            })
            monkeypatch.setattr('app._garmin_sync_timer', MagicMock())
            monkeypatch.setattr(threading, 'Timer', lambda *a, **kw: MagicMock())
            monkeypatch.setattr('app.os.path.isfile', lambda p: p == token_file)
            # sync_activities is imported *inside* auto_garmin_sync, so patch at the actual module
            monkeypatch.setattr('services.garmin_service.sync_activities', lambda uid, days: {'inserted': 1})

            auto_garmin_sync()

    def test_sync_lock_held(self, monkeypatch):
        from app import _garmin_lock, auto_garmin_sync
        with tempfile.TemporaryDirectory() as tmp:
            token_dir = os.path.join(tmp, '.tokens')
            os.makedirs(token_dir)
            token_file = os.path.join(token_dir, 'garmin_tokens.json')
            with open(token_file, 'w') as f:
                f.write('{}')

            monkeypatch.setattr('os.environ', {
                'GARMIN_USER_ID': '1',
                'GARMIN_EMAIL': 'test@test.com',
                'GARMIN_TOKEN_DIR': token_dir,
            })
            monkeypatch.setattr('app._garmin_sync_timer', MagicMock())
            monkeypatch.setattr(threading, 'Timer', lambda *a, **kw: MagicMock())
            monkeypatch.setattr('app.os.path.isfile', lambda p: p == token_file)
            monkeypatch.setattr('services.garmin_service.sync_activities', lambda uid, days: {'inserted': 1})
            # Acquire lock before calling sync → should skip
            _garmin_lock.acquire()
            try:
                auto_garmin_sync()
                # Should not raise
            finally:
                _garmin_lock.release()

    def test_sync_with_custom_token_dir(self, monkeypatch):
        from app import auto_garmin_sync
        with tempfile.TemporaryDirectory() as tmp:
            token_dir = os.path.join(tmp, 'custom_tokens')
            os.makedirs(token_dir)
            token_file = os.path.join(token_dir, 'garmin_tokens.json')
            with open(token_file, 'w') as f:
                f.write('{}')

            monkeypatch.setattr('os.environ', {
                'GARMIN_USER_ID': '1',
                'GARMIN_EMAIL': 'test@test.com',
                'GARMIN_TOKEN_DIR': token_dir,
            })
            monkeypatch.setattr('app._garmin_sync_timer', MagicMock())
            monkeypatch.setattr(threading, 'Timer', lambda *a, **kw: MagicMock())
            monkeypatch.setattr('app.os.path.isfile', lambda p: p == token_file)
            monkeypatch.setattr('services.garmin_service.sync_activities', lambda uid, days: {'inserted': 1})
            auto_garmin_sync()


# ============================================================
# start_background_tasks
# ============================================================

class TestStartBackgroundTasks:
    """start_background_tasks"""

    def test_starts_backup_only(self, monkeypatch):
        from app import start_background_tasks
        calls = []
        monkeypatch.setattr('app.auto_backup', lambda: calls.append('backup'))
        monkeypatch.setattr('app.auto_garmin_sync', lambda: calls.append('garmin'))
        monkeypatch.setattr('os.environ', {})
        start_background_tasks()
        assert 'backup' in calls
        assert 'garmin' not in calls

    def test_starts_both_with_garmin(self, monkeypatch):
        from app import start_background_tasks
        calls = []
        monkeypatch.setattr('app.auto_backup', lambda: calls.append('backup'))
        monkeypatch.setattr('app.auto_garmin_sync', lambda: calls.append('garmin'))
        monkeypatch.setattr('os.environ', {'GARMIN_EMAIL': 'test@test.com'})
        start_background_tasks()
        assert 'backup' in calls
        assert 'garmin' in calls


# ============================================================
# 健康检查
# ============================================================

class TestHealthCheck:
    """app.health_check"""

    def test_health_check(self, client):
        result = client.get('/health')
        assert result.status_code == 200
        assert result.json['status'] == 'ok'
"""
app.py 未覆盖分支补全测试

覆盖目标:
  - L35-42:  SECRET_KEY 生产环境缺失 → RuntimeError（子进程隔离）
  - L76-78:  AI 导入失败 → AI_AVAILABLE=False + call_ai 兜底
  - L107-108: 预测线程内部异常 → except 打印日志
  - L170-171: 备份清理文件名解析异常 → except pass（内层）
  - L172-173: 备份外层异常 → except 打印（外层）
  - L208-209: Garmin 自动同步异常
"""
import importlib
import subprocess
import sys

# ============================================================
# L35-42: SECRET_KEY 生产环境检查
# ============================================================

class TestSecretKeyProduction:
    """SECRET_KEY 生产环境缺失检查 (L35-42)"""

    def test_secret_key_production_raises(self):
        """L35-42: 使用子进程隔离验证 app 导入时 RuntimeError"""
        project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        dotenv_path = os.path.join(project_dir, '.env')

        dotenv_moved = False
        if os.path.exists(dotenv_path):
            os.rename(dotenv_path, dotenv_path + '.bak')
            dotenv_moved = True

        try:
            code = (
                'import os\n'
                'os.environ["FLASK_ENV"] = "production"\n'
                'os.environ.pop("SECRET_KEY", None)\n'
                'try:\n'
                '    import app\n'
                '    print("NO_ERROR")\n'
                'except RuntimeError as e:\n'
                '    print(f"ERROR:{e}")\n'
            )
            subprocess_env = os.environ.copy()
            subprocess_env['FLASK_ENV'] = 'production'
            subprocess_env.pop('SECRET_KEY', None)

            result = subprocess.run(
                [sys.executable, '-c', code],
                capture_output=True, text=True, timeout=10,
                cwd=project_dir, env=subprocess_env
            )
            assert 'ERROR:' in result.stdout, (
                f"预期 RuntimeError, 得到 stdout: {result.stdout}, stderr: {result.stderr}"
            )
            assert 'SECRET_KEY' in result.stdout
        finally:
            if dotenv_moved:
                os.rename(dotenv_path + '.bak', dotenv_path)


# ============================================================
# L76-78: AI 导入失败降级
# ============================================================

class TestAIFallback:
    """AI 模块属性验证 + 导入失败降级 (L76-78)"""

    def test_ai_available_attribute_exists(self):
        """验证 app 模块有 AI_AVAILABLE 和 call_ai（正常路径）"""
        import app
        assert hasattr(app, 'AI_AVAILABLE'), "app 应导出 AI_AVAILABLE"
        assert hasattr(app, 'call_ai'), "app 应导出 call_ai"
        assert callable(app.call_ai)

    def test_ai_fallback_import_error(self):
        """L76-78: 子进程隔离 — 先正常 import app（缓存依赖），
        再删除 ai_client 属性后 reload app 触发 ImportError"""
        project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        code = (
            'import sys; import importlib\n'
            '# 先正常导入 app（services 等依赖同时被缓存）\n'
            'import app\n'
            '# 删除 ai_client 属性模拟导入失败场景\n'
            'import ai_client\n'
            'del ai_client.call_ai\n'
            'del ai_client.AI_AVAILABLE\n'
            'importlib.invalidate_caches()\n'
            '# reload app -> from ai_client import call_ai, AI_AVAILABLE 触发 ImportError\n'
            'importlib.reload(sys.modules["app"])\n'
            'print(f"AI_AVAILABLE={sys.modules["app"].AI_AVAILABLE}")\n'
            'result = sys.modules["app"].call_ai()\n'
            'print(f"call_ai_result={result}")\n'
        )
        result = subprocess.run(
            [sys.executable, '-c', code],
            capture_output=True, text=True, timeout=15,
            cwd=project_dir,
        )
        if result.returncode != 0:
            raise AssertionError(
                f"子进程异常退出 rc={result.returncode}\n"
                f"stdout: {result.stdout}\nstderr: {result.stderr}"
            )
        assert 'AI_AVAILABLE=False' in result.stdout, f"stdout: {result.stdout}"
        assert 'call_ai_result=AI Not Available' in result.stdout, f"stdout: {result.stdout}"


# ============================================================
# L107-108: index() 预测任务已在运行 + 线程异常
# ============================================================

class TestIndexPredictionRunning:
    """index 路由预测任务已在运行时"""

    def test_prediction_already_running(self, client_authenticated):
        """L107-108: current_user_id 在 _prediction_running 中 -> 跳过新线程"""
        mock_running = MagicMock()
        mock_running.__contains__.return_value = True

        with patch('app._prediction_running', mock_running), \
             patch('app._prediction_last_run', {}), \
             patch('app._prediction_lock'), \
             patch('app.get_db') as mock_get_db, \
             patch('app.build_timeline', return_value=([], [])), \
             patch('app.get_dashboard_stats'), \
             patch('app.user_manager.get_user', return_value=None), \
             patch('app.os.environ.get', return_value=''), \
             patch('app.render_template', return_value='rendered'):

            mock_c = MagicMock()
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db

            resp = client_authenticated.get('/')
            assert resp.status_code == 200


class TestPredictionThreadException:
    """预测线程内部异常 (L107-108)"""

    def test_prediction_thread_exception(self, client_authenticated):
        """L107-108: predict_morning_fpg 在线程内抛出异常 -> except 捕获

        让真实 threading.Thread 在后台执行；predict_morning_fpg 抛异常后，
        线程内部的 try/except 会捕获，index 路由仍返回 200。
        不再 patch threading.Thread，避免破坏 Flask-Limiter 的 threading.Timer。
        """
        with patch('app.predict_morning_fpg',
                   side_effect=Exception("prediction failed")), \
             patch('app.predict_post_exercise_glucose'), \
             patch('app.get_db') as mock_get_db, \
             patch('app.build_timeline', return_value=([], [])), \
             patch('app.get_dashboard_stats'), \
             patch('app.user_manager.get_user', return_value=None), \
             patch('app.os.environ.get', return_value=''), \
             patch('app.render_template', return_value='rendered'):

            mock_c = MagicMock()
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db

            mock_running = MagicMock()
            mock_running.__contains__.return_value = False

            with patch('app._prediction_running', mock_running), \
                 patch('app._prediction_last_run', {}), \
                 patch('app._prediction_lock'):
                resp = client_authenticated.get('/')
                assert resp.status_code == 200


# ============================================================
# L170-173: auto_backup 异常（内层 + 外层）
# ============================================================

class TestAutoBackupCleanup:
    """自动备份文件清理异常处理 (L170-171)"""

    def test_backup_cleanup_exception(self):
        """L170-171: 备份文件名解析失败 -> except pass"""
        import tempfile

        import app

        with tempfile.TemporaryDirectory() as tmpdir:
            bad_file = os.path.join(tmpdir, 'glucose_auto_bad_date.db')
            open(bad_file, 'w').close()

            with patch('app.AUTO_BACKUP_DIR', tmpdir), \
                 patch('app.DB_NAME', os.path.join(tmpdir, 'test.db')), \
                 patch('app.os.path.exists', return_value=True), \
                 patch('app.os.makedirs'), \
                 patch('app.shutil.copy2'), \
                 patch('app.glob_mod.glob', return_value=[bad_file]), \
                 patch('app.threading.Timer') as mock_timer:

                app.auto_backup()
                mock_timer.assert_called_once_with(86400, app.auto_backup)


class TestAutoBackupOuterExcept:
    """auto_backup 外层异常 (L172-173)"""

    def test_backup_outer_exception(self):
        """L172-173: os.makedirs 异常 -> 外层 except 捕获"""
        import app
        with patch('app.os.path.exists', return_value=True), \
             patch('app.os.makedirs', side_effect=Exception("disk full")), \
             patch('app.threading.Timer') as mock_timer, \
             patch('builtins.print') as mock_print:
            app.auto_backup()
            mock_timer.assert_called_once()
            mock_print.assert_any_call('[AutoBackup] Error: disk full')


# ============================================================
# L208-209: auto_garmin_sync 异常
# ============================================================

class TestAutoGarminSyncNoToken:
    """Garmin 自动同步无 token"""

    def test_garmin_sync_no_token(self):
        """无 token -> 跳过同步，打印提示"""
        import app

        with patch('app.os.environ.get') as mock_get, \
             patch('app.os.path.isfile', return_value=False), \
             patch('app.threading.Timer') as mock_timer:

            mock_get.side_effect = lambda k, d=None: {
                'GARMIN_USER_ID': '1',
                'GARMIN_EMAIL': 'test@garmin.com',
                'GARMIN_TOKEN_DIR': '/tmp/garmin_tokens',
            }.get(k, d)

            with patch('builtins.print') as mock_print:
                app.auto_garmin_sync()

                mock_print.assert_any_call(
                    '[Garmin] 未找到持久化 token，跳过自动同步；'
                    '请先手动触发一次以完成首次登录'
                )
                mock_timer.assert_called_once()


class TestAutoGarminSyncException:
    """auto_garmin_sync 外层异常 (L208-209)"""

    def test_garmin_sync_exception(self):
        """L208-209: GARMIN_USER_ID 非数字 -> int() ValueError -> 外层 except"""
        import app
        with patch('app.os.environ.get') as mock_get, \
             patch('app.threading.Timer') as mock_timer, \
             patch('builtins.print') as mock_print:
            mock_get.side_effect = lambda k, d=None: {
                'GARMIN_USER_ID': 'not-a-number',
                'GARMIN_EMAIL': 'test@garmin.com'
            }.get(k, d)
            app.auto_garmin_sync()
            mock_print.assert_any_call(
                "[Garmin] 同步出错: invalid literal for int() with base 10: 'not-a-number'"
            )
            mock_timer.assert_called_once()
"""
健康检查端点测试
"""


def test_health_check(client):
    """测试 /health 端点返回正常"""
    response = client.get('/health')
    assert response.status_code == 200
    data = response.get_json()
    assert data['status'] == 'ok'
    assert data['service'] == 'sugar-bee'


def test_login_page_loads(client):
    """测试登录页面能正常加载"""
    response = client.get('/login')
    assert response.status_code == 200
    assert '蜜蜂控糖' in response.data.decode('utf-8')


# ============================================================
# (Merged from test_utils_core_coverage.py) — init_db + config + responses
# ============================================================

class TestInitDbError:
    """init_db 异常处理分支"""

    def test_init_db_connection_error(self, monkeypatch):
        import sqlite3

        from utils.db import init_db
        def broken_connect(*args, **kwargs):
            raise Exception("disk I/O error")
        monkeypatch.setattr(sqlite3, 'connect', broken_connect)
        init_db()

    def test_init_db_cursor_error(self, monkeypatch):
        import sqlite3

        from utils.db import init_db
        mock_conn = MagicMock()
        mock_conn.cursor.side_effect = Exception("cursor error")
        monkeypatch.setattr(sqlite3, 'connect', lambda *a, **kw: mock_conn)
        init_db()


class TestConfigEnvOverride:
    """core/config.py 环境变量覆盖"""

    def test_db_name_from_env(self, monkeypatch):
        monkeypatch.setenv('SUGAR_BEE_DB_PATH', '/tmp/test_custom.db')
        import core.config
        importlib.reload(core.config)
        assert core.config.DB_NAME == '/tmp/test_custom.db'

    def test_db_name_default(self, monkeypatch):
        monkeypatch.delenv('SUGAR_BEE_DB_PATH', raising=False)
        import core.config
        importlib.reload(core.config)
        assert core.config.DB_NAME.endswith('glucose.db')


class TestResponsesEdgeCases:
    """utils/responses.py 边界确认"""

    def test_api_success_no_data_no_message(self, app):
        from utils.responses import api_success
        with app.app_context():
            resp, code = api_success()
            assert code == 200
            data = resp.get_json()
            assert data['status'] == 'success'
            assert 'timestamp' in data

    def test_api_error_with_details(self, app):
        from utils.responses import api_error
        with app.app_context():
            resp, code = api_error("test error", status_code=503, error_type="db",
                                   details={"table": "records"})
            assert code == 503
            data = resp.get_json()
            assert data['status'] == 'error'
            assert data['message'] == 'test error'
            assert data['error_type'] == 'db'
            assert data['details'] == {"table": "records"}

    def test_success_res_alias(self, app):
        from utils.responses import success_res
        with app.app_context():
            resp, code = success_res(data=[1, 2, 3])
            assert code == 200
            assert resp.get_json()['data'] == [1, 2, 3]

    def test_error_res_alias(self, app):
        from utils.responses import error_res
        with app.app_context():
            resp, code = error_res("alias error", status_code=500)
            assert code == 500
            assert resp.get_json()['message'] == 'alias error'


# ============================================================
# (Merged from test_responses.py) — 响应工具函数全面测试
# ============================================================

def test_api_success_no_data(client):
    from utils.responses import api_success
    resp = api_success()
    data = resp[0].get_json()
    assert resp[1] == 200
    assert data['status'] == 'success'
    assert 'timestamp' in data

def test_api_success_with_data(client):
    from utils.responses import api_success
    resp = api_success(data={"key": "value"})
    assert resp[1] == 200
    data = resp[0].get_json()
    assert data['status'] == 'success'
    assert data['data'] == {"key": "value"}

def test_api_success_with_message(client):
    from utils.responses import api_success
    resp = api_success(message="操作成功")
    data = resp[0].get_json()
    assert data['message'] == "操作成功"

def test_api_success_with_data_and_message(client):
    from utils.responses import api_success
    resp = api_success(data={"id": 1}, message="创建成功")
    data = resp[0].get_json()
    assert data['data'] == {"id": 1}
    assert data['message'] == "创建成功"

def test_api_error_default(client):
    from utils.responses import api_error
    resp = api_error("出错了")
    data = resp[0].get_json()
    assert resp[1] == 400
    assert data['status'] == 'error'
    assert data['message'] == "出错了"

def test_api_error_custom_status(client):
    from utils.responses import api_error
    resp = api_error("未找到", status_code=404)
    assert resp[1] == 404
    data = resp[0].get_json()
    assert data['message'] == "未找到"

def test_api_error_with_type_and_details(client):
    from utils.responses import api_error
    resp = api_error("验证失败", status_code=422, error_type="validation_error", details={"field": "name"})
    assert resp[1] == 422
    data = resp[0].get_json()
    assert data['error_type'] == "validation_error"
    assert data['details'] == {"field": "name"}

def test_api_error_no_optional_fields(client):
    from utils.responses import api_error
    resp = api_error("简单错误")
    data = resp[0].get_json()
    assert 'error_type' not in data
    assert 'details' not in data

def test_success_res_alias_free(client):
    from utils.responses import success_res
    resp = success_res(data={"ok": True})
    assert resp[1] == 200
    data = resp[0].get_json()
    assert data['status'] == 'success'

def test_error_res_alias_free(client):
    from utils.responses import error_res
    resp = error_res("失败", status_code=500)
    assert resp[1] == 500
    data = resp[0].get_json()
    assert data['status'] == 'error'

def test_api_success_none_data(client):
    from utils.responses import api_success
    resp = api_success(data=None)
    data = resp[0].get_json()
    assert 'data' not in data

def test_api_success_empty_message(client):
    from utils.responses import api_success
    resp = api_success(message="")
    data = resp[0].get_json()
    assert 'message' not in data


# ============================================================
# auto_backup 带 GCS 路径（line 172）
# ============================================================

class TestAutoBackupGCS:
    """auto_backup 函数 — GCS 分支"""

    def test_backup_uploads_to_gcs(self, monkeypatch):
        monkeypatch.setenv('GCS_BUCKET_NAME', 'test-bucket')
        monkeypatch.setattr('app._auto_backup_timer', MagicMock())
        monkeypatch.setattr('os.path.exists', lambda p: True)
        monkeypatch.setattr('os.makedirs', lambda p, exist_ok: None)
        monkeypatch.setattr('shutil.copy2', lambda *a: None)
        import app as app_mod
        mock_backup_to_gcs = MagicMock()
        monkeypatch.setattr(app_mod, 'backup_db_to_gcs', mock_backup_to_gcs)
        # 设置 DB_NAME 存在
        monkeypatch.setattr('os.path.isfile', lambda p: True)
        # 需要让 os.path.exists(DB_NAME) 返回 True
        from core.config import DB_NAME
        monkeypatch.setattr('os.path.exists', lambda p: True if p == DB_NAME else False)
        app_mod.auto_backup()
        mock_backup_to_gcs.assert_called_once()

    def test_backup_no_gcs_skips_upload(self, monkeypatch):
        monkeypatch.delenv('GCS_BUCKET_NAME', raising=False)
        monkeypatch.setattr('app._auto_backup_timer', MagicMock())
        monkeypatch.setattr('os.path.exists', lambda p: True)
        monkeypatch.setattr('os.makedirs', lambda p, exist_ok: None)
        monkeypatch.setattr('shutil.copy2', lambda *a: None)
        from core.config import DB_NAME
        monkeypatch.setattr('os.path.exists', lambda p: True if p == DB_NAME else False)
        import app as app_mod
        mock_backup_to_gcs = MagicMock()
        monkeypatch.setattr(app_mod, 'backup_db_to_gcs', mock_backup_to_gcs)
        app_mod.auto_backup()
        mock_backup_to_gcs.assert_not_called()


# ============================================================
# periodic_gcs_backup（lines 201-214）
# ============================================================

class TestPeriodicGCSBackup:
    """periodic_gcs_backup 函数"""

    def test_periodic_gcs_backup_called(self, monkeypatch):
        monkeypatch.setenv('GCS_BUCKET_NAME', 'test-bucket')
        import app as app_mod
        mock_backup = MagicMock()
        monkeypatch.setattr(app_mod, 'backup_db_to_gcs', mock_backup)
        mock_timer = MagicMock()
        monkeypatch.setattr(threading, 'Timer', lambda interval, func, **kw: mock_timer)

        app_mod.periodic_gcs_backup()
        mock_backup.assert_called_once()
        mock_timer.start.assert_called_once()
        assert mock_timer.daemon is True

    def test_periodic_gcs_backup_no_bucket(self, monkeypatch):
        monkeypatch.delenv('GCS_BUCKET_NAME', raising=False)
        import app as app_mod
        mock_backup = MagicMock()
        monkeypatch.setattr(app_mod, 'backup_db_to_gcs', mock_backup)
        mock_timer = MagicMock()
        monkeypatch.setattr(threading, 'Timer', lambda interval, func, **kw: mock_timer)

        app_mod.periodic_gcs_backup()
        mock_backup.assert_not_called()
        mock_timer.start.assert_called_once()

    def test_periodic_gcs_backup_with_token_sync(self, monkeypatch):
        monkeypatch.setenv('GCS_BUCKET_NAME', 'test-bucket')
        monkeypatch.setenv('GARMIN_TOKEN_DIR', '/tmp/garmin_tokens')
        import app as app_mod
        mock_backup = MagicMock()
        monkeypatch.setattr(app_mod, 'backup_db_to_gcs', mock_backup)
        mock_sync = MagicMock()
        monkeypatch.setattr(app_mod, 'sync_file_to_gcs', mock_sync)
        mock_timer = MagicMock()
        monkeypatch.setattr(threading, 'Timer', lambda interval, func, **kw: mock_timer)
        # 模拟 token 文件存在
        original_isfile = os.path.isfile
        monkeypatch.setattr('os.path.isfile',
                            lambda p: True if 'garmin_tokens.json' in str(p) else original_isfile(p))

        app_mod.periodic_gcs_backup()
        mock_backup.assert_called_once()
        mock_sync.assert_called_once()


# ============================================================
# start_background_tasks 带 GCS（line 270）
# ============================================================

class TestStartBackgroundTasksGCS:
    """start_background_tasks — GCS 路径"""

    def test_starts_gcs_backup(self, monkeypatch):
        monkeypatch.setenv('GCS_BUCKET_NAME', 'test-bucket')
        monkeypatch.delenv('GARMIN_EMAIL', raising=False)
        import app as app_mod
        calls = []
        monkeypatch.setattr(app_mod, 'auto_backup', lambda: calls.append('backup'))
        mock_periodic = MagicMock()
        monkeypatch.setattr(app_mod, 'periodic_gcs_backup', mock_periodic)

        app_mod.start_background_tasks()
        assert 'backup' in calls
        mock_periodic.assert_called_once()


class TestPeriodicGCSBackupError:
    """periodic_gcs_backup 异常处理（line 209-210）"""

    def test_periodic_gcs_backup_exception(self, monkeypatch):
        """备份抛异常时被 except 捕获"""
        monkeypatch.setenv('GCS_BUCKET_NAME', 'test-bucket')
        import app as app_mod
        mock_backup = MagicMock(side_effect=Exception("GCS error"))
        monkeypatch.setattr(app_mod, 'backup_db_to_gcs', mock_backup)
        mock_timer = MagicMock()
        monkeypatch.setattr(threading, 'Timer', lambda interval, func, **kw: mock_timer)
        with patch('builtins.print'):
            app_mod.periodic_gcs_backup()
            mock_timer.start.assert_called_once()


# ============================================================
# AI 预测闭包异常路径（lines 111-112）
# ============================================================

class TestAiPredictionError:
    """后台预测线程的异常处理"""

    def test_prediction_thread_error(self, monkeypatch, app):
        """预测函数抛异常时被闭包 except 捕获"""
        from app import _prediction_running, _prediction_last_run

        _prediction_running.clear()
        _prediction_last_run.clear()
        mock_conn = MagicMock()
        monkeypatch.setattr('app.get_raw_conn', lambda: mock_conn)
        monkeypatch.setattr('app.put_raw_conn', lambda c: None)
        monkeypatch.setattr('app.predict_morning_fpg',
                            MagicMock(side_effect=ValueError("pred fail")))
        monkeypatch.setattr('app.predict_post_exercise_glucose', MagicMock())

        with patch('builtins.print') as mock_print:
            with patch('app.render_template', return_value=''):
                with patch('app.build_timeline', return_value=([], [])):
                    with patch('app.get_dashboard_stats', return_value={}):
                        mock_user = MagicMock()
                        mock_user.get.return_value = ['glucose', 'blood_pressure']
                        monkeypatch.setattr('app.user_manager.get_user',
                                            lambda uid: mock_user)
                        monkeypatch.setattr('app.settings', MagicMock())
                        monkeypatch.setattr('app.settings.USER_EMOJI_MAP', {})

                        with app.test_client() as client:
                            with client.session_transaction() as sess:
                                sess['current_user_id'] = 1
                            client.get('/')

        import time
        time.sleep(0.3)
        print_calls = [c for c in mock_print.call_args_list
                       if '后台预测出错' in str(c)]
        assert len(print_calls) > 0
