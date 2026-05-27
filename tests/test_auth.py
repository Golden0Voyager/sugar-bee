"""
认证相关测试
"""

import sqlite3


def test_login_with_invalid_credentials(client, app):
    """测试使用错误凭据登录失败"""
    response = client.post('/login', data={
        'username': 'nonexistent_user',
        'password': 'wrong_password'
    })
    assert response.status_code == 200
    assert '账号不存在' in response.data.decode('utf-8')


def test_login_rate_limit(client):
    """测试登录接口限速生效"""
    # 快速发送 15 次请求
    statuses = []
    for _ in range(15):
        response = client.post('/login', data={
            'username': 'test_user',
            'password': 'wrong'
        })
        statuses.append(response.status_code)

    # 应该有部分请求被限速返回 429
    # 注意：测试环境使用内存存储，单进程测试所有请求都会命中同一计数器
    assert 429 in statuses, f"Expected 429 in statuses, got {set(statuses)}"


def test_change_password_requires_login(client):
    """测试未登录时无法修改密码"""
    response = client.post('/change_password', json={
        'old_password': 'old',
        'new_password': 'new12345'
    })
    assert response.status_code == 401
