"""
路由层综合测试 - 简化版，专注核心路径
"""


class TestHealthRoutes:
    """健康检查路由测试"""

    def test_health_check_unauthenticated(self, client):
        resp = client.get('/health')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['status'] == 'ok'

    def test_health_analyses_requires_login(self, client):
        resp = client.get('/health_analyses')
        assert resp.status_code in (401, 302)

    def test_health_analyses_authenticated(self, client_authenticated):
        resp = client_authenticated.get('/health_analyses?limit=5')
        assert resp.status_code == 200

    def test_get_latest_analysis_no_data(self, client_authenticated):
        resp = client_authenticated.get('/get_latest_analysis')
        assert resp.status_code in (404, 200)

    def test_analyze_health_requires_login(self, client):
        resp = client.post('/analyze_health', json={'days': 7})
        assert resp.status_code in (401, 302)


class TestAuthRoutes:
    """认证路由测试"""

    def test_login_page_get(self, client):
        resp = client.get('/login')
        assert resp.status_code == 200
        assert '蜜蜂控糖' in resp.data.decode('utf-8')

    def test_login_page_already_logged_in(self, client_authenticated):
        resp = client_authenticated.get('/login')
        assert resp.status_code == 302

    def test_login_empty_username(self, client):
        resp = client.post('/login', data={'username': '', 'password': 'pass'})
        assert resp.status_code == 200
        content = resp.data.decode('utf-8')
        assert '请输入用户名' in content

    def test_login_nonexistent_user(self, client):
        resp = client.post('/login', data={'username': 'no_one_xyz', 'password': 'pass'})
        assert '账号不存在' in resp.data.decode('utf-8')

    def test_logout(self, client_authenticated):
        resp = client_authenticated.get('/logout', follow_redirects=False)
        assert resp.status_code == 302

    def test_set_password_missing_params(self, client):
        resp = client.post('/set_password', data={'username': '', 'password': ''})
        assert resp.status_code == 200
        content = resp.data.decode('utf-8')
        assert '参数错误' in content or '错误' in content

    def test_set_password_user_not_found(self, client):
        resp = client.post('/set_password', data={'username': 'ghost_user_xyz', 'password': 'test1234'})
        content = resp.data.decode('utf-8')
        assert '用户不存在' in content or '错误' in content

    def test_change_password_unauthenticated(self, client):
        resp = client.post('/change_password', json={'old_password': 'old', 'new_password': 'new12345'})
        assert resp.status_code in (401, 302)

    def test_change_password_too_short(self, client_authenticated):
        resp = client_authenticated.post('/change_password', json={'old_password': '', 'new_password': 'ab'})
        assert resp.status_code == 400

    def test_login_rate_limit(self, client):
        statuses = []
        for _ in range(15):
            resp = client.post('/login', data={'username': 'x', 'password': 'y'})
            statuses.append(resp.status_code)
        assert 429 in statuses


class TestUserRoutes:
    """用户路由测试"""

    def test_get_users_requires_login(self, client):
        resp = client.get('/get_users')
        assert resp.status_code in (401, 302)

    def test_get_users_authenticated(self, client_authenticated):
        resp = client_authenticated.get('/get_users')
        assert resp.status_code == 200

    def test_get_current_user(self, client_authenticated):
        resp = client_authenticated.get('/get_current_user')
        assert resp.status_code == 200

    def test_switch_user_not_found(self, client_authenticated):
        resp = client_authenticated.post('/switch_user/99999')
        assert resp.status_code == 404

    def test_create_user_requires_login(self, client):
        resp = client.post('/create_user', json={'username': 'x', 'display_name': 'X', 'password': 'test'})
        assert resp.status_code in (401, 302)

    def test_create_user_missing_fields(self, client_authenticated):
        resp = client_authenticated.post('/create_user', json={'username': 'x'})
        assert resp.status_code == 400

    def test_get_settings(self, client_authenticated):
        resp = client_authenticated.get('/settings')
        assert resp.status_code == 200

    def test_get_user_modules(self, client_authenticated):
        resp = client_authenticated.get('/api/user/modules')
        assert resp.status_code == 200

    def test_update_user_modules(self, client_authenticated):
        resp = client_authenticated.post('/api/user/modules', json={'enabled_modules': ['glucose', 'weight']})
        assert resp.status_code == 200

    def test_update_user_modules_invalid(self, client_authenticated):
        resp = client_authenticated.post('/api/user/modules', json={'enabled_modules': 'not-list'})
        assert resp.status_code == 400

    def test_change_username_requires_login(self, client):
        resp = client.post('/change_username', json={'new_username': 'new'})
        assert resp.status_code in (401, 302)

    def test_change_username_empty(self, client_authenticated):
        resp = client_authenticated.post('/change_username', json={'new_username': ''})
        assert resp.status_code == 400

    def test_delete_nonexistent_user(self, client_authenticated):
        resp = client_authenticated.post('/delete_user/99999')
        assert resp.status_code == 404

    def test_get_user_providers(self, client_authenticated):
        resp = client_authenticated.get('/get_user_providers')
        assert resp.status_code == 200

    def test_bind_phone_invalid(self, client_authenticated):
        resp = client_authenticated.post('/bind_phone', json={'phone': '123'})
        assert resp.status_code == 400

    def test_bind_email_invalid(self, client_authenticated):
        resp = client_authenticated.post('/bind_email', json={'email': 'not-email'})
        assert resp.status_code == 400

    def test_unbind_provider_invalid(self, client_authenticated):
        resp = client_authenticated.post('/unbind_provider', json={'provider': 'wechat'})
        assert resp.status_code == 400

    def test_sync_garmin_not_configured(self, client_authenticated, monkeypatch):
        # 确保 Garmin 未配置
        monkeypatch.delenv('GARMIN_USER_ID', raising=False)
        monkeypatch.delenv('GARMIN_EMAIL', raising=False)
        resp = client_authenticated.post('/sync_garmin')
        assert resp.status_code == 400


class TestMedsRoutes:
    """用药路由测试"""

    def test_get_medication_plans_requires_login(self, client):
        resp = client.get('/medication_plans')
        assert resp.status_code in (401, 302)

    def test_get_medication_plans_empty(self, client_authenticated):
        resp = client_authenticated.get('/medication_plans')
        assert resp.status_code == 200

    def test_add_medication_plan(self, client_authenticated):
        resp = client_authenticated.post('/add_medication_plan', json={
            'medication_name': '测试药品',
            'dosage': '500mg',
            'times_per_day': 2,
            'start_date': '2024-01-01',
        })
        assert resp.status_code == 200

    def test_get_medication_plan_not_found(self, client_authenticated):
        resp = client_authenticated.get('/medication_plan/99999')
        assert resp.status_code == 404

    def test_delete_nonexistent_plan(self, client_authenticated):
        resp = client_authenticated.post('/delete_medication_plan/99999')
        assert resp.status_code == 200
