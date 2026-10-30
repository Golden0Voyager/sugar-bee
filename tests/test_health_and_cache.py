"""P1a/P1b/P1c 的安全与缓存底线回归（全量 review 判定为放行门禁）。

这些行为如果静默回退，现有 1800+ 测试不会变红，但生产会劣化：
- /health 判据坏了 → 回滚失明（坏容器被当健康留在生产）
- /v/ 校验坏了 → 全站静态资源 404 或可猜未来文件名
- ETag/Vary 丢了 → 健康数据进共享缓存、每次全量重下
"""
import os
import re

import pytest


# ---------- A. /health 失败注入 ----------

def test_health_503_when_min_users_not_met(client, monkeypatch):
    """空 app_users + SB_MIN_USERS=1 → 503；内网可见判据明细（中文）。"""
    monkeypatch.setenv('SB_MIN_USERS', '1')
    r = client.get('/health')
    assert r.status_code == 503
    body = r.get_data(as_text=True)
    assert '用户表' in body
    assert '"db"' in body


def test_health_503_when_db_file_missing(client, monkeypatch):
    """DB 路径不存在 → 503（SQLite 会自动建空库，必须靠表/用户判据挡住）。"""
    monkeypatch.setenv('SB_MIN_USERS', '0')
    monkeypatch.setenv('SUGAR_BEE_DB_PATH', '/nonexistent/dir/glucose.db')
    r = client.get('/health')
    assert r.status_code == 503


def test_health_503_when_db_file_empty(client, monkeypatch, tmp_path):
    """0 字节 DB 文件 → 503。"""
    empty = tmp_path / 'empty.db'
    empty.write_bytes(b'')
    monkeypatch.setenv('SB_MIN_USERS', '0')
    monkeypatch.setenv('SUGAR_BEE_DB_PATH', str(empty))
    r = client.get('/health')
    assert r.status_code == 503


def test_health_hides_details_from_public(client, monkeypatch):
    """外网（XFF 非内网）503 时不得回吐异常明细/绝对路径。"""
    monkeypatch.setenv('SB_MIN_USERS', '1')
    r = client.get('/health', headers={'X-Forwarded-For': '8.8.8.8'})
    assert r.status_code == 503
    body = r.get_data(as_text=True)
    assert 'checks' not in body
    assert '用户表' not in body


# ---------- B. /v/ 版本化路由 ----------

def test_static_url_emits_hashed_path():
    """static_url 必须产出 /v/<base>.<8位hex>.<ext>，与 _V_PATH_RE 对齐。"""
    from app import app as flask_app, static_url
    with flask_app.test_request_context():
        url = static_url('js/app.js')
    assert re.fullmatch(r'/v/js/app\.[0-9a-f]{8}\.js', url), url


def test_versioned_static_serves_with_immutable(client):
    from app import app as flask_app, static_url
    with flask_app.test_request_context():
        url = static_url('js/app.js')
    r = client.get(url)
    assert r.status_code == 200
    assert 'immutable' in r.headers.get('Cache-Control', '')


def test_versioned_static_rejects_bad_or_missing_hash(client):
    """hash 不符或缺失一律 404——这是「旧版本立即失效」的安全底线。"""
    assert client.get('/v/js/app.deadbeef.js').status_code == 404
    assert client.get('/v/js/app.js').status_code == 404


def test_versioned_static_rejects_traversal(client):
    assert client.get('/v/../secrets.00000000.txt').status_code == 404


# ---------- C. HTML 缓存正确性 ----------

def test_html_etag_gives_304(client_authenticated):
    r1 = client_authenticated.get('/')
    assert r1.status_code == 200
    etag = r1.headers.get('ETag')
    assert etag, 'HTML 必须带 ETag（Flask 3 不再自动生成，需自己算）'
    r2 = client_authenticated.get('/', headers={'If-None-Match': etag})
    assert r2.status_code == 304
    assert r2.data == b''


def test_html_cache_headers_privacy(client_authenticated):
    """个人健康数据不得进共享缓存（private），且必须按 Cookie 区分。"""
    r = client_authenticated.get('/')
    cc = r.headers.get('Cache-Control', '')
    assert 'private' in cc and 'no-cache' in cc
    assert 'Cookie' in r.headers.get('Vary', '')


# ---------- D. __BOOT__ ↔ app.js 键契约 ----------

def test_boot_keys_cover_appjs_references(client_authenticated):
    """app.js 里 B.<key> 的每个键都必须在 __BOOT__ 里存在。

    改名任一侧（比如模板删掉 activeModules 的复数 s）现有测试全绿，
    运行时 new Set(B.activeModules) 抛 TypeError、整页交互全灭，
    错误还指向 app.js 而非真凶——必须由这条契约挡住。
    """
    import re as _re
    from pathlib import Path as _P

    body = client_authenticated.get('/').get_data(as_text=True)
    m = _re.search(r'window\.__BOOT__ = \{([\s\S]*?)\};', body)
    assert m, '渲染输出里找不到 window.__BOOT__'
    boot_keys = set(_re.findall(r'^\s*(\w+):', m.group(1), _re.M))
    assert len(boot_keys) >= 15, f'__BOOT__ 键数异常偏少: {sorted(boot_keys)}'

    js = _P(__file__).resolve().parents[1].joinpath('static/js/app.js').read_text(encoding='utf-8')
    refs = set(_re.findall(r'\bB\.(\w+)', js))
    missing = refs - boot_keys
    assert not missing, f'app.js 引用了 __BOOT__ 里不存在的键: {sorted(missing)}'
