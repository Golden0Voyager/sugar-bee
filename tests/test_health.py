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
