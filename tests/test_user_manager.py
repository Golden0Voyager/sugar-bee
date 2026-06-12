"""
UserManager 用户管理模块测试 - 自建独立数据库避免锁冲突
"""
import pytest
import os
import tempfile
import sqlite3
from user_manager import UserManager


@pytest.fixture(scope='module')
def module_db_path():
    """模块级别的临时数据库"""
    fd, path = tempfile.mkstemp(suffix='.db')
    yield path
    os.close(fd)
    if os.path.exists(path):
        os.unlink(path)


@pytest.fixture
def um(module_db_path, monkeypatch):
    """创建 UserManager 实例，使用独立的临时数据库"""
    # Monkeypatch core.config.DB_NAME 让 UserManager 使用独立路径
    monkeypatch.setattr('core.config.DB_NAME', module_db_path)
    monkeypatch.setattr('user_manager.DB_NAME', module_db_path, raising=False)

    # 直接用 sqlite3 手动建表
    conn = sqlite3.connect(module_db_path)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS app_users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        display_name TEXT NOT NULL,
        avatar TEXT,
        is_active BOOLEAN DEFAULT 1,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        password_hash TEXT,
        phone TEXT,
        email TEXT
    )''')
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
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        target_weight REAL,
        birth_month INTEGER,
        birth_day INTEGER
    )''')
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
    conn.commit()
    conn.close()

    return UserManager(module_db_path)


class TestCreateUser:
    """创建用户测试"""

    def test_create_user(self, um):
        uid = um.create_user('new_user', '新用户',
                             {'name': '新', 'birth_year': 2000},
                             password='test1234')
        assert uid is not None
        assert uid > 0

    def test_create_user_no_password(self, um):
        uid = um.create_user('no_pass_user', '无密码用户', {})
        assert uid > 0

    def test_get_user_success(self, um):
        uid = um.create_user('test_user1', '测试用户',
                             {'name': '测试', 'birth_year': 1980, 'height': 175, 'weight': 70, 'gender': 'male'},
                             password='pass1234')
        user = um.get_user(uid)
        assert user is not None
        assert user['username'] == 'test_user1'

    def test_get_user_not_found(self, um):
        user = um.get_user(99999)
        assert user is None


class TestGetAllUsers:
    """获取所有用户测试"""

    def test_empty_users(self, um):
        """清空后应返回空列表"""
        # 删除所有已有用户
        conn = sqlite3.connect(um.db_path)
        conn.execute("DELETE FROM user_auth_providers")
        conn.execute("DELETE FROM user_profiles")
        conn.execute("DELETE FROM app_users")
        conn.commit()
        conn.close()
        users = um.get_all_users()
        assert users == []

    def test_json_field_invalid(self, um):
        """JSON 字段解析失败时的兜底"""
        uid = um.create_user('bad_json', 'Bad JSON', {})
        conn = sqlite3.connect(um.db_path)
        conn.execute("UPDATE user_profiles SET enabled_modules = 'not-valid-json' WHERE user_id = ?", (uid,))
        conn.commit()
        conn.close()
        user = um.get_user(uid)
        assert user['enabled_modules'] == []

    def test_default_meals_invalid_json(self, um):
        """L50-51: default_meals JSON 解析失败 → 兜底为空 dict"""
        uid = um.create_user('bad_default_meals', 'Bad Meals', {})
        conn = sqlite3.connect(um.db_path)
        conn.execute("UPDATE user_profiles SET default_meals = 'not-valid-json' WHERE user_id = ?", (uid,))
        conn.commit()
        conn.close()
        user = um.get_user(uid)
        assert user['default_meals'] == {}

    def test_target_ranges_invalid_json(self, um):
        """L58-59: target_ranges JSON 解析失败 → 兜底为空 dict"""
        uid = um.create_user('bad_target_ranges', 'Bad Targets', {})
        conn = sqlite3.connect(um.db_path)
        conn.execute("UPDATE user_profiles SET target_ranges = 'not-valid-json' WHERE user_id = ?", (uid,))
        conn.commit()
        conn.close()
        user = um.get_user(uid)
        assert user['target_ranges'] == {}


class TestIsModuleEnabled:
    """模块启用检查测试"""

    def test_unknown_user(self, um):
        assert um.is_module_enabled(99999, 'glucose') is True


class TestGetUserConfig:
    """获取用户配置测试"""

    def test_default_config(self, um):
        config = um._get_default_config()
        assert config['name'] == '用户'
        assert 'target' in config

    def test_get_config_nonexistent_user(self, um):
        config = um.get_user_config(99999)
        assert config['name'] == '用户'


class TestAuthentication:
    """认证测试"""

    def test_authenticate_nonexistent_user(self, um):
        uid = um.authenticate('no_such', 'pass')
        assert uid is None

    def test_has_password_false(self, um):
        uid = um.create_user('nopwd_u', '无密码', {})
        assert um.has_password(uid) is False

    def test_has_password_nonexistent(self, um):
        assert um.has_password(99999) is False

    def test_get_user_by_username_not_found(self, um):
        u = um.get_user_by_username('no_such')
        assert u is None


class TestProviders:
    """手机号/邮箱绑定测试"""

    def test_find_user_by_provider_not_found(self, um):
        found = um.find_user_by_provider('phone', '00000000000')
        assert found is None
"""
api_user.py 剩余未覆盖分支测试 (18 行)

覆盖目标:
  L45-47:   switch_user 外层 except -> 500
  L83-84:   create_user 外层 except -> 500
  L130-132: update_user_modules 外层 except -> 500
  L156-158: upload_avatar 外层 except -> 500
  L265-274: sync_garmin 成功路径 + 异常 -> 200/500
"""
import json
from unittest.mock import patch, MagicMock

import pytest


class TestUserSwitchExcept:
    """api_user.py L45-47: switch_user except 分支"""

    def test_switch_user_exception(self, client_authenticated):
        """L45-47: get_user 抛出异常 -> 500"""
        with patch('routes.api_user.user_manager.get_user',
                   side_effect=Exception("db crash")):
            resp = client_authenticated.post('/switch_user/1',
                data=json.dumps({}),
                content_type='application/json')
            assert resp.status_code == 500
            assert resp.json['status'] == 'error'


class TestUserCreateExcept:
    """api_user.py L83-84: create_user except 分支"""

    def test_create_user_exception(self, client_authenticated):
        """L83-84: create_user 抛出异常 -> 500"""
        with patch('routes.api_user.user_manager.create_user',
                   side_effect=Exception("create failed")):
            resp = client_authenticated.post('/create_user',
                data=json.dumps({
                    'username': 'test', 'display_name': '测试',
                    'password': 'test123',
                }),
                content_type='application/json')
            assert resp.status_code == 500
            assert resp.json['status'] == 'error'


class TestUserModulesExcept:
    """api_user.py L130-132: update_user_modules except 分支"""

    def test_update_modules_exception(self, client_authenticated):
        """L130-132: set_enabled_modules 抛出异常 -> 500"""
        with patch('routes.api_user.user_manager.set_enabled_modules',
                   side_effect=Exception("update failed")):
            resp = client_authenticated.post('/api/user/modules',
                data=json.dumps({'enabled_modules': ['glucose']}),
                content_type='application/json')
            assert resp.status_code == 500
            assert resp.json['status'] == 'error'


class TestUserUploadAvatarExcept:
    """api_user.py L156-158: upload_avatar except 分支"""

    def test_upload_avatar_save_fails(self, client_authenticated):
        """L156-158: 文件保存失败 -> except -> 500"""
        with patch('routes.api_user.os.path.join',
                   return_value='/nonexistent_dir/avatar_test.png'), \
             patch('routes.api_user.current_app') as mock_app:
            mock_app.config = {'UPLOAD_FOLDER': '/tmp'}
            # 发送有效文件 -> 验证通过 -> file.save 因目录不存在而失败
            from io import BytesIO
            data = {'avatar': (BytesIO(b'fake-png-data'), 'avatar.png')}
            resp = client_authenticated.post('/upload_avatar',
                data=data, content_type='multipart/form-data')
            assert resp.status_code == 500
            assert resp.json['status'] == 'error'


class TestUserSyncGarmin:
    """api_user.py L265-274: sync_garmin 完整路径"""

    def _get_current_user_id(self, client_authenticated):
        """从 session 中读取当前用户 ID"""
        with client_authenticated.session_transaction() as sess:
            return sess.get('current_user_id')

    def test_sync_garmin_success(self, client_authenticated, app):
        """L265-271: Garmin 同步成功 -> 200"""
        current_uid = self._get_current_user_id(client_authenticated)

        with patch('routes.api_user.os.environ.get') as mock_get, \
             patch('services.garmin_service.sync_activities') as mock_sync:
            # 动态匹配当前用户的 ID
            mock_get.side_effect = lambda k, d=None: {
                'GARMIN_USER_ID': str(current_uid),
                'GARMIN_EMAIL': 'test@garmin.com'
            }.get(k, d)
            mock_sync.return_value = {'inserted': 5, 'skipped': 2}

            resp = client_authenticated.post('/sync_garmin')
            assert resp.status_code == 200, \
                f"预期 200, 得到 {resp.status_code}: {resp.data}"
            data = resp.json['data']
            assert data['inserted'] == 5
            assert data['skipped'] == 2

    def test_sync_garmin_exception(self, client_authenticated):
        """L272-274: sync_activities 抛出异常 -> 500"""
        current_uid = self._get_current_user_id(client_authenticated)

        with patch('routes.api_user.os.environ.get') as mock_get, \
             patch('services.garmin_service.sync_activities') as mock_sync:
            mock_get.side_effect = lambda k, d=None: {
                'GARMIN_USER_ID': str(current_uid),
                'GARMIN_EMAIL': 'test@garmin.com'
            }.get(k, d)
            mock_sync.side_effect = Exception("Garmin API timeout")

            resp = client_authenticated.post('/sync_garmin')
            assert resp.status_code == 500, \
                f"预期 500, 得到 {resp.status_code}: {resp.data}"
            assert 'Garmin 同步失败' in resp.json['message']
