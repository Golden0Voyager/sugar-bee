"""user_manager.py 扩展测试 — get_user_config, provider 绑定, 密码管理, update_user_profile_partial"""
from unittest.mock import patch, MagicMock


class TestGetUserConfig:
    """get_user_config() 和 _get_default_config() 测试"""

    def test_default_config(self):
        from user_manager import UserManager
        um = UserManager(':memory:')
        config = um._get_default_config()
        assert config['name'] == '用户'
        assert config['birth_year'] == 1964
        assert config['height'] == 170
        assert config['weight'] == 75
        assert config['gender'] == 'male'
        assert 'target' in config
        assert config['target']['fasting_min'] == 3.9

    def test_get_user_config_returns_default_when_no_user(self):
        from user_manager import UserManager
        um = UserManager(':memory:')
        with patch.object(um, 'get_user', return_value=None):
            config = um.get_user_config(1)
            assert config['name'] == '用户'

    def test_get_user_config_returns_user_data(self):
        from user_manager import UserManager
        um = UserManager(':memory:')
        mock_user = {
            'id': 1, 'name': '张三', 'display_name': '张三',
            'birth_year': 1985, 'birth_month': 6, 'birth_day': 15,
            'height': 180, 'weight': 80, 'target_weight': 75,
            'gender': 'male', 'avatar': 'avatar1.png',
            'default_meals': {'breakfast': {'calories': 400}},
            'target_ranges': {'fasting_min': 4.0, 'fasting_max': 6.5},
        }
        with patch.object(um, 'get_user', return_value=mock_user):
            config = um.get_user_config(1)
            assert config['name'] == '张三'
            assert config['height'] == 180
            assert config['weight'] == 80
            assert config['age'] > 0
            assert config['default_meals'] == {'breakfast': {'calories': 400}}

    def test_get_user_config_age_calculation(self):
        from user_manager import UserManager
        um = UserManager(':memory:')
        import datetime
        current_year = datetime.datetime.now().year
        mock_user = {
            'id': 1, 'name': 'Test', 'display_name': 'Test',
            'birth_year': 2000, 'birth_month': 1, 'birth_day': 1,
            'height': 170, 'weight': 70, 'gender': 'male',
            'default_meals': {}, 'target_ranges': None, 'avatar': None,
        }
        with patch.object(um, 'get_user', return_value=mock_user):
            config = um.get_user_config(1)
            assert config['age'] == current_year - 2000


class TestUpdateUserProfilePartial:
    """update_user_profile_partial() 部分更新测试"""

    def test_update_scalar_fields(self):
        from user_manager import UserManager
        um = UserManager(':memory:')
        with patch('user_manager.sqlite3.connect') as mock_connect:
            mock_conn = MagicMock()
            mock_connect.return_value = mock_conn

            um.update_user_profile_partial(1, {'name': '新名字', 'height': 180, 'weight': 75})
            # Should have executed an UPDATE
            assert mock_conn.cursor.return_value.execute.called
            # INSERT OR IGNORE for defensive row creation
            exec_calls = [str(c[0][0]) for c in mock_conn.cursor.return_value.execute.call_args_list]
            assert any('INSERT OR IGNORE' in c for c in exec_calls)
            assert any('UPDATE' in c for c in exec_calls)

    def test_update_json_fields(self):
        from user_manager import UserManager
        um = UserManager(':memory:')
        with patch('user_manager.sqlite3.connect') as mock_connect:
            mock_conn = MagicMock()
            mock_connect.return_value = mock_conn

            um.update_user_profile_partial(1, {
                'default_meals': {'breakfast': {'calories': 300}},
                'target_ranges': {'fasting_min': 4.0},
                'enabled_modules': ['glucose', 'exercise'],
            })
            assert mock_conn.commit.called

    def test_update_with_alias(self):
        from user_manager import UserManager
        um = UserManager(':memory:')
        with patch('user_manager.sqlite3.connect') as mock_connect:
            mock_conn = MagicMock()
            mock_connect.return_value = mock_conn

            um.update_user_profile_partial(1, {'target': {'fasting_min': 4.5}})
            exec_calls = [str(c[0][0]) for c in mock_conn.cursor.return_value.execute.call_args_list]
            assert any('target_ranges' in c for c in exec_calls)

    def test_update_empty_data_noop(self):
        from user_manager import UserManager
        um = UserManager(':memory:')
        with patch('user_manager.sqlite3.connect') as mock_connect:
            um.update_user_profile_partial(1, {})
            mock_connect.assert_not_called()

    def test_update_partial_none_data_noop(self):
        from user_manager import UserManager
        um = UserManager(':memory:')
        with patch('user_manager.sqlite3.connect') as mock_connect:
            um.update_user_profile_partial(1, None)
            mock_connect.assert_not_called()

    def test_update_target_weight(self):
        from user_manager import UserManager
        um = UserManager(':memory:')
        with patch('user_manager.sqlite3.connect') as mock_connect:
            mock_conn = MagicMock()
            mock_connect.return_value = mock_conn

            um.update_user_profile_partial(1, {'target_weight': 70})
            exec_calls = [str(c[0][0]) for c in mock_conn.cursor.return_value.execute.call_args_list]
            assert any('target_weight' in c for c in exec_calls)


class TestProviderBinding:
    """手机号/邮箱绑定解绑测试"""

    def test_bind_provider_new(self):
        from user_manager import UserManager
        um = UserManager(':memory:')
        with patch('user_manager.sqlite3.connect') as mock_connect:
            mock_conn = MagicMock()
            mock_c = MagicMock()
            mock_c.fetchone.return_value = None  # not already bound
            mock_conn.cursor.return_value = mock_c
            mock_connect.return_value = mock_conn

            result = um.bind_provider(1, 'phone', '13800138000')
            assert result['ok'] is True
            mock_conn.commit.assert_called()

    def test_bind_provider_already_bound_to_same_user(self):
        from user_manager import UserManager
        um = UserManager(':memory:')
        with patch('user_manager.sqlite3.connect') as mock_connect:
            mock_conn = MagicMock()
            mock_c = MagicMock()
            mock_c.fetchone.return_value = (1,)  # already bound to user 1
            mock_conn.cursor.return_value = mock_c
            mock_connect.return_value = mock_conn

            result = um.bind_provider(1, 'phone', '13800138000')
            assert result['ok'] is False
            assert '已绑定' in result['message']

    def test_bind_provider_conflict(self):
        from user_manager import UserManager
        um = UserManager(':memory:')
        with patch('user_manager.sqlite3.connect') as mock_connect:
            mock_conn = MagicMock()
            mock_c = MagicMock()
            mock_c.fetchone.return_value = (2,)  # bound to user 2
            mock_conn.cursor.return_value = mock_c
            mock_connect.return_value = mock_conn

            result = um.bind_provider(1, 'email', 'test@test.com')
            assert result['ok'] is False
            assert '其他用户' in result['message']

    def test_unbind_provider(self):
        from user_manager import UserManager
        um = UserManager(':memory:')
        with patch('user_manager.sqlite3.connect') as mock_connect:
            mock_conn = MagicMock()
            mock_connect.return_value = mock_conn

            um.unbind_provider(1, 'phone')
            mock_conn.commit.assert_called()

    def test_find_user_by_provider_found(self):
        from user_manager import UserManager
        um = UserManager(':memory:')
        with patch('user_manager.sqlite3.connect') as mock_connect:
            mock_conn = MagicMock()
            mock_c = MagicMock()
            mock_c.fetchone.return_value = (5,)
            mock_conn.cursor.return_value = mock_c
            mock_connect.return_value = mock_conn

            uid = um.find_user_by_provider('phone', '13800138000')
            assert uid == 5

    def test_find_user_by_provider_not_found(self):
        from user_manager import UserManager
        um = UserManager(':memory:')
        with patch('user_manager.sqlite3.connect') as mock_connect:
            mock_conn = MagicMock()
            mock_c = MagicMock()
            mock_c.fetchone.return_value = None
            mock_conn.cursor.return_value = mock_c
            mock_connect.return_value = mock_conn

            uid = um.find_user_by_provider('email', 'nonexistent@test.com')
            assert uid is None

    def test_get_user_providers(self):
        from user_manager import UserManager
        um = UserManager(':memory:')
        with patch('user_manager.sqlite3.connect') as mock_connect:
            mock_conn = MagicMock()
            mock_c = MagicMock()
            mock_c.fetchall.return_value = [
                {'provider': 'phone', 'provider_uid': '13800138000', 'verified': 1, 'created_at': '2024-01-01'}
            ]
            mock_conn.cursor.return_value = mock_c
            mock_connect.return_value = mock_conn

            rows = um.get_user_providers(1)
            assert len(rows) == 1
            assert rows[0]['provider'] == 'phone'


class TestPasswordManagement:
    """密码管理测试"""

    def test_set_password(self):
        from user_manager import UserManager
        um = UserManager(':memory:')
        with patch('user_manager.sqlite3.connect') as mock_connect:
            mock_conn = MagicMock()
            mock_connect.return_value = mock_conn

            um.set_password(1, 'newpass123')
            mock_conn.commit.assert_called()

    def test_has_password_true(self):
        from user_manager import UserManager
        um = UserManager(':memory:')
        with patch('user_manager.sqlite3.connect') as mock_connect:
            mock_conn = MagicMock()
            mock_c = MagicMock()
            mock_c.fetchone.return_value = {'password_hash': 'hashed_value'}
            mock_conn.cursor.return_value = mock_c
            mock_connect.return_value = mock_conn

            assert um.has_password(1) is True

    def test_has_password_false(self):
        from user_manager import UserManager
        um = UserManager(':memory:')
        with patch('user_manager.sqlite3.connect') as mock_connect:
            mock_conn = MagicMock()
            mock_c = MagicMock()
            mock_c.fetchone.return_value = None
            mock_conn.cursor.return_value = mock_c
            mock_connect.return_value = mock_conn

            assert um.has_password(1) is False

    def test_has_password_empty_hash(self):
        from user_manager import UserManager
        um = UserManager(':memory:')
        with patch('user_manager.sqlite3.connect') as mock_connect:
            mock_conn = MagicMock()
            mock_c = MagicMock()
            mock_c.fetchone.return_value = {'password_hash': None}
            mock_conn.cursor.return_value = mock_c
            mock_connect.return_value = mock_conn

            assert um.has_password(1) is False

    def test_authenticate_success(self):
        from user_manager import UserManager
        from werkzeug.security import generate_password_hash
        um = UserManager(':memory:')
        pw_hash = generate_password_hash('correct')

        with patch('user_manager.sqlite3.connect') as mock_connect:
            mock_conn = MagicMock()
            mock_c = MagicMock()
            mock_c.fetchone.return_value = {'id': 1, 'password_hash': pw_hash}
            mock_conn.cursor.return_value = mock_c
            mock_connect.return_value = mock_conn

            result = um.authenticate('testuser', 'correct')
            assert result == 1

    def test_authenticate_wrong_password(self):
        from user_manager import UserManager
        from werkzeug.security import generate_password_hash
        um = UserManager(':memory:')
        pw_hash = generate_password_hash('correct')

        with patch('user_manager.sqlite3.connect') as mock_connect:
            mock_conn = MagicMock()
            mock_c = MagicMock()
            mock_c.fetchone.return_value = {'id': 1, 'password_hash': pw_hash}
            mock_conn.cursor.return_value = mock_c
            mock_connect.return_value = mock_conn

            result = um.authenticate('testuser', 'wrong')
            assert result is None

    def test_authenticate_user_not_found(self):
        from user_manager import UserManager
        um = UserManager(':memory:')
        with patch('user_manager.sqlite3.connect') as mock_connect:
            mock_conn = MagicMock()
            mock_c = MagicMock()
            mock_c.fetchone.return_value = None
            mock_conn.cursor.return_value = mock_c
            mock_connect.return_value = mock_conn

            result = um.authenticate('nonexistent', 'any')
            assert result is None

    def test_authenticate_no_password_set(self):
        from user_manager import UserManager
        um = UserManager(':memory:')
        with patch('user_manager.sqlite3.connect') as mock_connect:
            mock_conn = MagicMock()
            mock_c = MagicMock()
            mock_c.fetchone.return_value = {'id': 1, 'password_hash': None}
            mock_conn.cursor.return_value = mock_c
            mock_connect.return_value = mock_conn

            result = um.authenticate('testuser', 'any')
            # No password set → returns user_id (old users need to set password)
            assert result == 1


class TestOtherMethods:
    """其他方法测试"""

    def test_change_username(self):
        from user_manager import UserManager
        um = UserManager(':memory:')
        with patch('user_manager.sqlite3.connect') as mock_connect:
            mock_conn = MagicMock()
            mock_connect.return_value = mock_conn

            um.change_username(1, 'newname')
            mock_conn.commit.assert_called()

    def test_set_enabled_modules(self):
        from user_manager import UserManager
        um = UserManager(':memory:')
        with patch('user_manager.sqlite3.connect') as mock_connect:
            mock_conn = MagicMock()
            mock_connect.return_value = mock_conn

            um.set_enabled_modules(1, ['glucose', 'exercise'])
            mock_conn.commit.assert_called()

    def test_get_user_by_username_found(self):
        from user_manager import UserManager
        um = UserManager(':memory:')
        with patch('user_manager.sqlite3.connect') as mock_connect:
            mock_conn = MagicMock()
            mock_c = MagicMock()
            mock_c.fetchone.return_value = {'id': 1, 'username': 'test', 'display_name': 'Test'}
            mock_conn.cursor.return_value = mock_c
            mock_connect.return_value = mock_conn

            user = um.get_user_by_username('test')
            assert user['username'] == 'test'

    def test_get_user_by_username_not_found(self):
        from user_manager import UserManager
        um = UserManager(':memory:')
        with patch('user_manager.sqlite3.connect') as mock_connect:
            mock_conn = MagicMock()
            mock_c = MagicMock()
            mock_c.fetchone.return_value = None
            mock_conn.cursor.return_value = mock_c
            mock_connect.return_value = mock_conn

            user = um.get_user_by_username('nonexistent')
            assert user is None

    def test_get_user_by_username_or_id(self):
        from user_manager import UserManager
        um = UserManager(':memory:')
        with patch('user_manager.sqlite3.connect') as mock_connect:
            mock_conn = MagicMock()
            mock_c = MagicMock()
            mock_c.fetchone.return_value = {'id': 1, 'username': 'test', 'display_name': 'Test'}
            mock_conn.cursor.return_value = mock_c
            mock_connect.return_value = mock_conn

            user = um.get_user_by_username_or_id(1)
            assert user['id'] == 1

    def test_is_module_enabled_default_true(self):
        from user_manager import UserManager
        um = UserManager(':memory:')
        with patch.object(um, 'get_user', return_value={'enabled_modules': None}):
            assert um.is_module_enabled(1, 'glucose') is True

    def test_is_module_enabled_explicit(self):
        from user_manager import UserManager
        um = UserManager(':memory:')
        with patch.object(um, 'get_user', return_value={'enabled_modules': ['glucose']}):
            assert um.is_module_enabled(1, 'glucose') is True
            assert um.is_module_enabled(1, 'exercise') is False

    def test_set_current_user(self):
        from user_manager import UserManager
        um = UserManager(':memory:')
        with patch('user_manager.session', {}) as mock_session:
            um.set_current_user(5)
            assert mock_session['current_user_id'] == 5
