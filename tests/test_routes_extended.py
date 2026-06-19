"""扩展路由测试 — 覆盖 api_meds, api_admin, api_auth, api_user, api_chat, api_prediction"""
import pytest
from unittest.mock import patch, MagicMock


# ============================================================
# api_meds 测试 (58% → 85%)
# ============================================================

class TestMedicationPlansCRUD:
    """用药方案 CRUD 操作测试 — 使用 test client"""

    def test_get_medication_plans_empty(self, client_authenticated):
        with patch('routes.api_meds.get_db') as mock_get_db:
            mock_c = MagicMock()
            mock_c.fetchall.return_value = []
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db

            result = client_authenticated.get('/medication_plans')
            assert result.status_code == 200

    def test_get_medication_plan_not_found(self, client_authenticated):
        with patch('routes.api_meds.get_db') as mock_get_db:
            mock_c = MagicMock()
            mock_c.fetchone.return_value = None
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db

            result = client_authenticated.get('/medication_plan/999')
            assert result.status_code == 404

    def test_add_medication_plan(self, client_authenticated):
        with patch('routes.api_meds.get_db') as mock_get_db:
            mock_c = MagicMock()
            mock_c.lastrowid = 99
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db

            result = client_authenticated.post('/add_medication_plan', json={
                'medication_name': '二甲双胍', 'dosage': '500mg', 'times_per_day': 3
            })
            assert result.status_code == 200
            data = result.json
            assert data['data']['id'] == 99

    def test_delete_medication_plan(self, client_authenticated):
        with patch('routes.api_meds.get_db') as mock_get_db:
            mock_c = MagicMock()
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db

            result = client_authenticated.post('/delete_medication_plan/1')
            assert result.status_code == 200
            exec_calls = [str(c[0][0]) for c in mock_c.execute.call_args_list]
            assert any('medication_logs' in c for c in exec_calls)
            assert any('medication_plans' in c for c in exec_calls)

    def test_toggle_medication_plan(self, client_authenticated):
        with patch('routes.api_meds.get_db') as mock_get_db:
            mock_c = MagicMock()
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db

            result = client_authenticated.post('/toggle_medication_plan/1')
            assert result.status_code == 200


# ============================================================
# api_admin 测试 (42% → 60%)
# ============================================================

class TestAdminOperations:
    """管理员操作测试"""

    def test_find_duplicates_empty(self, client_authenticated):
        with patch('routes.api_admin.get_db') as mock_get_db:
            mock_c = MagicMock()
            mock_c.fetchall.return_value = []
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db

            result = client_authenticated.get('/find_duplicates')
            assert result.status_code == 200
            assert result.json['data']['total_groups'] == 0

    def test_find_duplicates_with_dupes(self, client_authenticated):
        with patch('routes.api_admin.get_db') as mock_get_db:
            mock_c = MagicMock()
            mock_c.fetchall.return_value = [
                ('2024-06-01 07:15', '空腹', 6.5, 2, '1,2')
            ]
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db

            result = client_authenticated.get('/find_duplicates')
            assert result.status_code == 200
            assert result.json['data']['total_groups'] == 1


# ============================================================
# api_auth 测试 (59% → 80%)
# ============================================================

class TestAuthRoutes:
    """认证路由测试"""

    def test_login_page_get(self):
        from routes.api_auth import login
        from flask import Flask
        app = Flask(__name__)

        with app.test_request_context('/login', method='GET'), \
             patch('routes.api_auth.session', {}), \
             patch('routes.api_auth.render_template') as mock_render:
            mock_render.return_value = 'login page'
            result = login()
            assert result == 'login page'

    def test_login_post_empty_username(self):
        from routes.api_auth import login
        from flask import Flask
        app = Flask(__name__)

        with app.test_request_context('/login', method='POST',
                                       data={'username': '', 'password': '123'}):
            with patch('routes.api_auth.render_template') as mock_render:
                mock_render.return_value = 'error'
                login()
                assert 'error' in str(mock_render.call_args[1])

    def test_login_post_wrong_password(self):
        from routes.api_auth import login
        from flask import Flask
        from werkzeug.security import generate_password_hash
        app = Flask(__name__)

        pw_hash = generate_password_hash('correct123')
        with patch('routes.api_auth.user_manager.get_user_by_username') as mock_gubu:
            mock_gubu.return_value = {'id': 1, 'username': 'test', 'password_hash': pw_hash}

            with app.test_request_context('/login', method='POST',
                                           data={'username': 'test', 'password': 'wrong'}):
                with patch('routes.api_auth.render_template') as mock_render:
                    mock_render.return_value = 'error page'
                    login()
                    assert mock_render.call_args[1].get('error') == '密码错误'

    def test_login_no_password_set(self):
        from routes.api_auth import login
        from flask import Flask
        app = Flask(__name__)

        with patch('routes.api_auth.user_manager.get_user_by_username') as mock_gubu:
            mock_gubu.return_value = {'id': 1, 'username': 'test', 'password_hash': None}

            with app.test_request_context('/login', method='POST',
                                           data={'username': 'test', 'password': 'any'}):
                with patch('routes.api_auth.render_template') as mock_render:
                    mock_render.return_value = 'set password'
                    login()
                    assert mock_render.call_args[1].get('set_password_mode') is True

    def test_login_phone_lookup(self):
        from routes.api_auth import login
        from flask import Flask
        app = Flask(__name__)

        with app.test_request_context('/login', method='POST',
                                       data={'username': '13800138000', 'password': '123'}):
            with patch('routes.api_auth.user_manager.find_user_by_provider') as mock_find, \
                 patch('routes.api_auth.user_manager.get_user_by_username_or_id') as mock_get, \
                 patch('routes.api_auth.render_template') as mock_render:
                mock_find.return_value = 1
                mock_get.return_value = None
                mock_render.return_value = 'not found'
                login()
                mock_find.assert_called_with('phone', '13800138000')

    @pytest.mark.skip(reason="logout redirects to url_for('auth.login'), needs full app context")
    def test_logout_clears_session(self):
        from routes.api_auth import logout
        from flask import Flask
        app = Flask(__name__)

        with app.test_request_context('/logout'):
            with patch('routes.api_auth.session') as mock_sess:
                logout()
                mock_sess.clear.assert_called_once()

    def test_set_password(self):
        from routes.api_auth import set_password
        from flask import Flask
        app = Flask(__name__)

        with patch('routes.api_auth.user_manager.get_user_by_username') as mock_gubu, \
             patch('routes.api_auth.get_db') as mock_get_db:
            mock_gubu.return_value = {'id': 1, 'username': 'test'}
            mock_conn = MagicMock()
            mock_get_db.return_value = mock_conn

            with app.test_request_context('/set_password', method='POST',
                                           data={'username': 'test', 'password': 'newpass123'}):
                with patch('routes.api_auth.render_template') as mock_render:
                    mock_render.return_value = 'success'
                    set_password()
                    assert mock_render.call_args[1].get('success') == '密码设置成功，请登录'


# ============================================================
# api_user 测试 (60% → 75%)
# ============================================================

class TestUserRoutes:
    """用户管理路由测试"""

    def test_get_users(self, client_authenticated):
        with patch('routes.api_user.user_manager.get_all_users') as mock_all:
            mock_all.return_value = [{'id': 1, 'username': 'user1'}, {'id': 2, 'username': 'user2'}]
            result = client_authenticated.get('/get_users')
            data = result.json
            assert len(data) == 2

    def test_get_current_user(self, client_authenticated):
        result = client_authenticated.get('/get_current_user')
        assert result.status_code == 200
        assert 'username' in result.json

    def test_create_user_success(self, client_authenticated):
        with patch('routes.api_user.user_manager.create_user') as mock_create:
            mock_create.return_value = 5
            result = client_authenticated.post('/create_user', json={
                'username': 'newuser', 'display_name': '新用户', 'password': 'pass123'
            })
            assert result.status_code == 200
            assert result.json['data']['id'] == 5

    def test_create_user_empty(self, client_authenticated):
        result = client_authenticated.post('/create_user', json={
            'username': '', 'display_name': '', 'password': ''
        })
        assert result.status_code == 400

    def test_get_user_modules(self, client_authenticated):
        result = client_authenticated.get('/api/user/modules')
        assert result.status_code == 200
        assert 'enabled_modules' in result.json['data']

    def test_update_user_modules_invalid(self, client_authenticated):
        result = client_authenticated.post('/api/user/modules', json={
            'enabled_modules': 'not_a_list'
        })
        assert result.status_code == 400

    def test_update_user_modules(self, client_authenticated):
        result = client_authenticated.post('/api/user/modules', json={
            'enabled_modules': ['glucose', 'exercise']
        })
        assert result.status_code == 200

    def test_delete_user_self(self, client_authenticated):
        # Deleting self (current user) should be blocked
        # client_authenticated session has current_user_id set to the test user
        with client_authenticated.session_transaction() as sess:
            my_id = sess['current_user_id']
        result = client_authenticated.post(f'/delete_user/{my_id}')
        assert result.status_code == 400

    def test_bind_phone_invalid(self, client_authenticated):
        result = client_authenticated.post('/bind_phone', json={'phone': '12345'})
        assert result.status_code == 400

    def test_bind_phone_success(self, client_authenticated):
        with patch('routes.api_user.user_manager.bind_provider') as mock_bind:
            mock_bind.return_value = {'ok': True}
            result = client_authenticated.post('/bind_phone', json={'phone': '13800138000'})
            assert result.status_code == 200

    def test_bind_email_invalid(self, client_authenticated):
        result = client_authenticated.post('/bind_email', json={'email': 'bad'})
        assert result.status_code == 400

    def test_bind_email_success(self, client_authenticated):
        with patch('routes.api_user.user_manager.bind_provider') as mock_bind:
            mock_bind.return_value = {'ok': True}
            result = client_authenticated.post('/bind_email', json={'email': 'test@test.com'})
            assert result.status_code == 200

    def test_unbind_invalid(self, client_authenticated):
        result = client_authenticated.post('/unbind_provider', json={'provider': 'wechat'})
        assert result.status_code == 400

    def test_unbind_success(self, client_authenticated):
        with patch('routes.api_user.user_manager.unbind_provider'):
            result = client_authenticated.post('/unbind_provider', json={'provider': 'phone'})
            assert result.status_code == 200

    def test_change_username(self, client_authenticated):
        with patch('routes.api_user.user_manager.get_user_by_username') as mock_gubu, \
             patch('routes.api_user.user_manager.change_username'):
            mock_gubu.return_value = None
            result = client_authenticated.post('/change_username', json={'new_username': 'newname'})
            assert result.status_code == 200

    def test_change_username_conflict(self, client_authenticated):
        with patch('routes.api_user.user_manager.get_user_by_username') as mock_gubu:
            mock_gubu.return_value = {'id': 2, 'username': 'taken'}
            result = client_authenticated.post('/change_username', json={'new_username': 'taken'})
            assert result.status_code == 400

    def test_get_settings(self, client_authenticated):
        result = client_authenticated.get('/settings')
        assert result.status_code == 200

    def test_update_settings(self, client_authenticated):
        result = client_authenticated.post('/settings', json={'name': '新名字'})
        assert result.status_code == 200


# ============================================================
# api_chat 测试 (42% → 50%)
# ============================================================

class TestChatRoutes:
    """聊天路由测试"""

    def test_new_session(self, client_authenticated):
        result = client_authenticated.post('/api/chat/new_session')
        assert result.status_code == 200

    def test_delete_session(self, client_authenticated):
        result = client_authenticated.delete('/api/chat/session/test-123')
        assert result.status_code == 200

    def test_chat_stream_missing_params(self, client_authenticated):
        with patch('routes.api_chat.CHAT_AVAILABLE', True):
            result = client_authenticated.post('/api/chat/stream', json={
                'message': '', 'session_id': ''
            })
        assert result.status_code == 400


# ============================================================
# api_prediction 测试 (55% → 60%)
# ============================================================

class TestPredictionRoutes:
    """预测路由测试"""

    def test_prediction_comparison(self, client_authenticated):
        with patch('routes.api_prediction.get_db') as mock_get_db:
            mock_c = MagicMock()
            mock_c.fetchall.return_value = []
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db

            result = client_authenticated.get('/prediction_comparison?days=7')
            assert result.status_code == 200

    def test_prediction_accuracy(self, client_authenticated):
        with patch('routes.api_prediction.get_db') as mock_get_db:
            mock_c = MagicMock()
            mock_c.fetchone.return_value = None
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db

            result = client_authenticated.get('/prediction_accuracy?days=30')
            assert result.status_code == 200

    def test_prediction_status(self, client_authenticated):
        with patch('routes.api_prediction.get_db') as mock_get_db:
            mock_c = MagicMock()
            mock_c.fetchall.return_value = []
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db

            result = client_authenticated.get('/prediction_status')
            assert result.status_code == 200
            assert len(result.json['slots']) == 7
