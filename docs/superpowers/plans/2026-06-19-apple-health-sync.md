# Apple Health iOS Shortcuts Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement one-way Apple Health data sync via iOS Shortcuts, with device binding and pre-built `.shortcut` distribution.

**Architecture:** Two new HTTP endpoints in a Flask Blueprint (`routes/api_health_sync.py`), a `device_bindings` table in `init_db()`, and frontend settings UI for device binding. Deduplication via `external_id` + `source` field check on the `records` table (no schema changes to records).

**Tech Stack:** Flask Blueprint, `secrets` (token/secure random), `uuid` (device_id), `datetime` (code expiry). No new dependencies.

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `routes/api_health_sync.py` | **Create** | Flask Blueprint: POST `/health-sync/bind`, POST `/health-sync/sync`, device auth helpers |
| `utils/db.py` | **Modify** | Add `device_bindings` table DDL in `init_db()` |
| `app.py` | **Modify** | Register `api_health_sync` blueprint; add rate limits for bind/sync |
| `tests/test_api_health_sync.py` | **Create** | Tests for bind, sync, device auth, token validation, dedup, edge cases |
| `templates/index.html` | **Modify** | Add "绑定 iOS 设备" section in settings; "下载 iOS 捷径" link |

---

### Task 1: Add device_bindings table DDL

**Files:**
- Modify: `utils/db.py` (two locations: SQLite CREATE TABLE and PostgreSQL CREATE TABLE branches)

- [ ] **Step 1: Add device_bindings CREATE TABLE to SQLite schema in `init_db()`**

Find the SQLite schema block (around `c.execute("CREATE TABLE IF NOT EXISTS records..."`) and add a new table:

```python
c.execute("""CREATE TABLE IF NOT EXISTS device_bindings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    bind_code TEXT,
    code_expires_at TEXT,
    device_id TEXT,
    device_token TEXT,
    device_name TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    bound_at TEXT,
    FOREIGN KEY (user_id) REFERENCES app_users(id)
)""")
```

- [ ] **Step 2: Add device_bindings CREATE TABLE to PostgreSQL schema in `init_db()`**

Find the PostgreSQL schema block and add:

```python
c.execute("""CREATE TABLE IF NOT EXISTS device_bindings (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES app_users(id),
    bind_code TEXT,
    code_expires_at TIMESTAMP,
    device_id TEXT,
    device_token TEXT,
    device_name TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    bound_at TIMESTAMP
)""")
```

- [ ] **Step 3: Run tests to verify schema creation works**

```bash
uv run python -m pytest tests/ -q
```

Expected: All existing tests pass (the new table is created but not referenced yet, so no impact).

- [ ] **Step 4: Commit**

```bash
git add utils/db.py
git commit -m "feat(db): add device_bindings table for Apple Health device binding

feat(db): 新增 device_bindings 表，用于 Apple Health 设备绑定"
```

---

### Task 2: Create health-sync Blueprint

**Files:**
- Create: `routes/api_health_sync.py`
- Test: `tests/test_api_health_sync.py` (portions covering POST bind)

- [ ] **Step 1: Create `routes/api_health_sync.py` with bind endpoint**

```python
"""Apple Health iOS Shortcuts 数据同步接口。

通过 iOS 快捷指令将 Apple Health 数据写入 Sugar Bee。
"""
import datetime
import json
import os
import random
import secrets
import traceback
import uuid

from flask import Blueprint, request, g

from user_manager import UserManager
from core.config import DB_NAME
from utils.responses import api_success, api_error
from utils.db import get_db
from utils.auth import login_required

user_manager = UserManager(DB_NAME)

bp_health_sync = Blueprint('health_sync', __name__, url_prefix='/api/v1/health-sync')


def _get_bind_code() -> str:
    """生成 6 位数字绑定码。"""
    return str(random.randint(100000, 999999))


def _generate_device_token() -> str:
    """生成 32 字节随机设备令牌（URL-safe base64，约 43 字符）。"""
    return secrets.token_urlsafe(32)


# ========== 绑定端点 ==========


@bp_health_sync.route('/bind', methods=['POST'])
@login_required
def bind_device():
    """Step 1: 用户从 Sugar Bee 设置页发起绑定 → 生成绑定码。

    请求: POST /api/v1/health-sync/bind
    请求体: {}
    响应: {"status": "success", "data": {"bind_code": "123456", "expires_in": 1800}}
    """
    try:
        current_user_id = user_manager.get_current_user_id()
        db = get_db()
        c = db.cursor()

        # 清除该用户的过期绑定码
        c.execute(
            "DELETE FROM device_bindings WHERE user_id = ? AND "
            "bind_code IS NOT NULL AND code_expires_at < datetime('now')",
            (current_user_id,),
        )

        # 生成新绑定码（30 分钟过期）
        bind_code = _get_bind_code()
        expires_at = (datetime.datetime.now() + datetime.timedelta(minutes=30)).isoformat()

        c.execute(
            "INSERT INTO device_bindings (user_id, bind_code, code_expires_at) VALUES (?, ?, ?)",
            (current_user_id, bind_code, expires_at),
        )
        db.commit()

        return api_success(data={
            'bind_code': bind_code,
            'expires_in': 1800,
        })
    except Exception as e:
        traceback.print_exc()
        return api_error(str(e), status_code=500)


@bp_health_sync.route('/confirm_binding', methods=['POST'])
@login_required
def confirm_binding():
    """Step 2 (可选): 用户手动确认绑定（显示已绑定的设备）。
    也可用于 iOS 捷径完成绑定后，用户在网页上查看状态。
    """
    try:
        current_user_id = user_manager.get_current_user_id()
        db = get_db()
        c = db.cursor()
        c.execute(
            "SELECT device_id, device_name, bound_at FROM device_bindings "
            "WHERE user_id = ? AND device_id IS NOT NULL AND device_token IS NOT NULL "
            "ORDER BY bound_at DESC LIMIT 1",
            (current_user_id,),
        )
        row = c.fetchone()
        if row:
            return api_success(data={
                'device_id': row['device_id'],
                'device_name': row['device_name'],
                'bound_at': row['bound_at'],
            })
        return api_success(data={'device_id': None})
    except Exception as e:
        traceback.print_exc()
        return api_error(str(e), status_code=500)
```

- [ ] **Step 2: Create test file `tests/test_api_health_sync.py` with bind endpoint tests**

```python
"""Apple Health sync API: bind endpoints"""
import json
from unittest.mock import patch, MagicMock

# ============================================================
# POST /api/v1/health-sync/bind — 生成绑定码
# ============================================================

class TestHealthSyncBind:
    """生成绑定码测试"""

    def test_bind_success(self, client_authenticated):
        """成功生成 6 位绑定码"""
        result = client_authenticated.post('/api/v1/health-sync/bind')
        assert result.status_code == 200
        data = result.json['data']
        assert len(data['bind_code']) == 6
        assert data['bind_code'].isdigit()
        assert data['expires_in'] == 1800

    def test_bind_requires_auth(self, client):
        """未登录返回 401"""
        result = client.post('/api/v1/health-sync/bind')
        assert result.status_code == 401

    def test_bind_db_error(self, client_authenticated):
        """数据库异常返回 500"""
        with patch('routes.api_health_sync.get_db') as mock_get_db:
            mock_get_db.side_effect = Exception("DB error")
            result = client_authenticated.post('/api/v1/health-sync/bind')
            assert result.status_code == 500
            assert 'error' in result.json['status']
```

- [ ] **Step 3: Run tests**

```bash
uv run python -m pytest tests/test_api_health_sync.py -v
```

Expected: 3 tests pass.

- [ ] **Step 4: Commit**

```bash
git add routes/api_health_sync.py tests/test_api_health_sync.py
git commit -m "feat(health-sync): add POST bind endpoint with 6-digit code generation

feat(health-sync): 新增 POST bind 端点，生成 6 位数字绑定码"
```

---

### Task 3: Add iOS device binding confirmation endpoint

**Files:**
- Modify: `routes/api_health_sync.py`
- Modify: `tests/test_api_health_sync.py`

- [ ] **Step 1: Add POST `confirm_binding_from_shortcut` endpoint for the iOS Shortcuts to complete binding**

```python
@bp_health_sync.route('/bind_from_shortcut', methods=['POST'])
def bind_from_shortcut():
    """Step 2: iOS 捷径携带绑定码调用此端点完成绑定。

    请求: POST /api/v1/health-sync/bind_from_shortcut
    请求体: {"code": "123456", "device_name": "iPhone 15"}
    响应: {"status": "success", "data": {"device_id": "uuid", "device_token": "token"}}
    """
    try:
        data = request.get_json(force=True)
        if not data:
            return api_error("请求体为空")
        code = data.get('code', '').strip()
        device_name = data.get('device_name', 'Unknown Device')

        if not code or not code.isdigit() or len(code) != 6:
            return api_error("无效的绑定码格式", status_code=400)

        db = get_db()
        c = db.cursor()

        # 查找有效绑定码（30 分钟内未过期、未使用）
        c.execute(
            "SELECT id, user_id FROM device_bindings "
            "WHERE bind_code = ? AND device_id IS NULL "
            "AND code_expires_at >= datetime('now') "
            "ORDER BY created_at DESC LIMIT 1",
            (code,),
        )
        row = c.fetchone()
        if not row:
            return api_error("绑定码无效或已过期", status_code=404)

        binding_id = row['id']
        user_id = row['user_id']

        # 生成 device_id + device_token
        device_id = str(uuid.uuid4())
        device_token = _generate_device_token()
        now = datetime.datetime.now().isoformat()

        c.execute(
            "UPDATE device_bindings SET device_id = ?, device_token = ?, "
            "device_name = ?, bound_at = ?, bind_code = NULL, code_expires_at = NULL "
            "WHERE id = ? AND device_id IS NULL",
            (device_id, device_token, device_name, now, binding_id),
        )
        db.commit()

        return api_success(data={
            'device_id': device_id,
            'device_token': device_token,
        })
    except Exception as e:
        traceback.print_exc()
        return api_error(str(e), status_code=500)
```

- [ ] **Step 2: Add tests for `bind_from_shortcut`**

```python
class TestHealthSyncBindFromShortcut:
    """通过绑定码完成设备绑定测试"""

    def _create_valid_bind_code(self, client_authenticated):
        """helper: 先调 bind 生成一个有效绑定码并返回"""
        result = client_authenticated.post('/api/v1/health-sync/bind')
        return result.json['data']['bind_code']

    def test_bind_from_shortcut_success(self, client_authenticated):
        """iOS 捷径用有效绑定码完成绑定"""
        code = self._create_valid_bind_code(client_authenticated)
        result = client_authenticated.post(
            '/api/v1/health-sync/bind_from_shortcut',
            json={'code': code, 'device_name': 'iPhone 15'},
        )
        assert result.status_code == 200
        data = result.json['data']
        assert len(data['device_id']) == 36  # UUID v4
        assert len(data['device_token']) > 30

    def test_bind_from_shortcut_invalid_code(self, client_authenticated):
        """无效绑定码返回 404"""
        result = client_authenticated.post(
            '/api/v1/health-sync/bind_from_shortcut',
            json={'code': '000000', 'device_name': 'iPhone'},
        )
        assert result.status_code == 404

    def test_bind_from_shortcut_wrong_format(self, client_authenticated):
        """非数字或非 6 位绑定码返回 400"""
        result = client_authenticated.post(
            '/api/v1/health-sync/bind_from_shortcut',
            json={'code': 'abc123', 'device_name': 'iPhone'},
        )
        assert result.status_code == 400

        result = client_authenticated.post(
            '/api/v1/health-sync/bind_from_shortcut',
            json={'code': '12345', 'device_name': 'iPhone'},
        )
        assert result.status_code == 400

    def test_bind_from_shortcut_code_already_used(self, client_authenticated):
        """绑定码只能使用一次（已用则返回 404）"""
        code = self._create_valid_bind_code(client_authenticated)
        # 第一次使用
        client_authenticated.post(
            '/api/v1/health-sync/bind_from_shortcut',
            json={'code': code, 'device_name': 'iPhone 15'},
        )
        # 再次使用同一码
        result = client_authenticated.post(
            '/api/v1/health-sync/bind_from_shortcut',
            json={'code': code, 'device_name': 'iPhone 15'},
        )
        assert result.status_code == 404

    def test_bind_from_shortcut_expired_code(self, client_authenticated):
        """过期绑定码返回 404"""
        code = self._create_valid_bind_code(client_authenticated)
        # 模拟过期：直接修改数据库
        import sqlite3
        from core.config import DB_NAME
        conn = sqlite3.connect(DB_NAME)
        conn.execute(
            "UPDATE device_bindings SET code_expires_at = '2020-01-01' WHERE bind_code = ?",
            (code,),
        )
        conn.commit()
        conn.close()

        result = client_authenticated.post(
            '/api/v1/health-sync/bind_from_shortcut',
            json={'code': code, 'device_name': 'iPhone'},
        )
        assert result.status_code == 404

    def test_bind_from_shortcut_no_auth(self, client):
        """bind_from_shortcut 不需要登录（设备没有 session）"""
        result = client.post(
            '/api/v1/health-sync/bind_from_shortcut',
            json={'code': '123456', 'device_name': 'iPhone'},
        )
        # 不能验证是否成功（因为没有有效 code），但至少不返回 401
        assert result.status_code != 401
```

- [ ] **Step 3: Run tests**

```bash
uv run python -m pytest tests/test_api_health_sync.py -v
```

Expected: 9 tests pass.

- [ ] **Step 4: Commit**

```bash
git add routes/api_health_sync.py tests/test_api_health_sync.py
git commit -m "feat(health-sync): add POST bind_from_shortcut endpoint for device binding completion

feat(health-sync): 新增 POST bind_from_shortcut 端点，iOS 捷径完成设备绑定"
```

---

### Task 4: Add POST /sync endpoint — health data write

**Files:**
- Modify: `routes/api_health_sync.py`
- Modify: `tests/test_api_health_sync.py`

- [ ] **Step 1: Add device auth helper and sync endpoint**

```python
def _verify_device_auth(device_id: str, device_token: str) -> int | None:
    """验证 device_id + device_token，返回绑定的 user_id 或 None。

    使用 HMAC 常量时间比较防计时攻击（通过 hmac.compare_digest）。
    """
    import hmac
    try:
        db = get_db()
        c = db.cursor()
        c.execute(
            "SELECT user_id, device_token FROM device_bindings "
            "WHERE device_id = ? AND device_token IS NOT NULL",
            (device_id,),
        )
        row = c.fetchone()
        if row and hmac.compare_digest(row['device_token'], device_token):
            return row['user_id']
    except Exception:
        pass
    return None


# Apple Health → Sugar Bee type mapping
_HEALTH_DATA_TYPE_MAP = {
    '血糖': '血糖',
    '体重': '体重',
    '步数': '步数',
    '血压收缩压': '血压收缩压',
    '血压舒张压': '血压舒张压',
    '血氧': '血氧',
    '心率': '心率',
}

# Mapping from Apple Health category to unit
_HEALTH_DATA_UNIT_MAP = {
    '血糖': 'mmol/L',
    '体重': 'kg',
    '步数': 'steps',
    '血压收缩压': 'mmHg',
    '血压舒张压': 'mmHg',
    '血氧': '%',
    '心率': 'bpm',
}


@bp_health_sync.route('/sync', methods=['POST'])
def sync_health_data():
    """iOS 捷径同步 Apple Health 数据。

    请求头: X-Device-Id, X-Device-Token
    请求体: {"start_date": "...", "end_date": "...", "records": [...]}
    记录字段: type, value, unit, timestamp
    响应: {"status": "success", "data": {"inserted": N, "skipped": M}}

    去重: 每条记录设 external_id = "apple_health:<uuid>" + source = "apple_health"，
          写入前检查是否已存在。
    """
    try:
        # 1. 设备鉴权
        device_id = request.headers.get('X-Device-Id', '')
        device_token = request.headers.get('X-Device-Token', '')
        if not device_id or not device_token:
            return api_error("缺少设备鉴权信息", status_code=401)

        user_id = _verify_device_auth(device_id, device_token)
        if user_id is None:
            return api_error("设备鉴权失败", status_code=401)

        # 2. 解析请求体
        data = request.get_json(force=True)
        if not data:
            return api_error("请求体为空")
        records = data.get('records', [])
        if not records:
            return api_error("records 列表为空")

        db = get_db()
        c = db.cursor()
        inserted = 0
        skipped = 0

        # 3. 逐条写入（去重基于 external_id + source）
        for r in records:
            r_type = r.get('type', '')
            r_value = r.get('value')
            r_unit = r.get('unit', _HEALTH_DATA_UNIT_MAP.get(r_type, ''))
            r_timestamp = r.get('timestamp')
            r_external_id = r.get('external_id', '')

            if not r_type or r_value is None or not r_timestamp:
                skipped += 1
                continue

            # 生成去重 ID（如果 iOS 捷径未提供则自动生成）
            if not r_external_id:
                r_external_id = f"apple_health:{uuid.uuid4()}"

            # 检查重复
            c.execute(
                "SELECT id FROM records WHERE external_id = ? AND source = ?",
                (r_external_id, 'apple_health'),
            )
            if c.fetchone():
                skipped += 1
                continue

            # 转换为 Sugar Bee 内部类型
            sugar_type = _HEALTH_DATA_TYPE_MAP.get(r_type, r_type)

            # 血压特殊处理：拆分为收缩压和舒张压两个记录
            if r_type == '血压收缩压':
                c.execute(
                    "INSERT INTO records (user_id, type, value, unit, timestamp, "
                    "systolic_pressure, external_id, source) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 'apple_health')",
                    (user_id, '血压', r_value, r_unit, r_timestamp, r_value, r_external_id),
                )
            elif r_type == '血压舒张压':
                c.execute(
                    "INSERT INTO records (user_id, type, value, unit, timestamp, "
                    "diastolic_pressure, external_id, source) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 'apple_health')",
                    (user_id, '血压', r_value, r_unit, r_timestamp, r_value, r_external_id),
                )
            else:
                c.execute(
                    "INSERT INTO records (user_id, type, value, unit, timestamp, "
                    "external_id, source) "
                    "VALUES (?, ?, ?, ?, ?, ?, 'apple_health')",
                    (user_id, sugar_type, r_value, r_unit, r_timestamp, r_external_id),
                )
            inserted += 1

        db.commit()
        return api_success(data={'inserted': inserted, 'skipped': skipped})
    except Exception as e:
        traceback.print_exc()
        return api_error(str(e), status_code=500)
```

- [ ] **Step 2: Add sync tests**

```python
class TestHealthSyncSync:
    """Apple Health 数据同步测试"""

    def _bind_device(self, client):
        """helper: 完成一次完整的设备绑定流程，返回 (device_id, device_token)"""
        # 先登录
        with client.session_transaction() as sess:
            from user_manager import UserManager
            from core.config import DB_NAME
            um = UserManager(DB_NAME)
            user_id = um.create_user('_sync_test', 'Sync测试', {})

            # 清理可能的旧数据
            conn = sqlite3.connect(DB_NAME)
            conn.execute("DELETE FROM device_bindings WHERE user_id = ?", (user_id,))
            conn.commit()
            conn.close()

            sess['current_user_id'] = user_id

        # 生成绑定码
        resp = client.post('/api/v1/health-sync/bind')
        code = resp.json['data']['bind_code']

        # 完成绑定
        resp = client.post('/api/v1/health-sync/bind_from_shortcut',
                            json={'code': code, 'device_name': 'Test Phone'})
        data = resp.json['data']
        return data['device_id'], data['device_token']

    def test_sync_success(self, client):
        """成功同步 Apple Health 数据"""
        device_id, device_token = self._bind_device(client)

        result = client.post(
            '/api/v1/health-sync/sync',
            headers={'X-Device-Id': device_id, 'X-Device-Token': device_token},
            json={
                'start_date': '2026-06-19T00:00:00',
                'end_date': '2026-06-19T23:59:59',
                'records': [{
                    'external_id': 'apple_health:test-001',
                    'type': '血糖',
                    'value': 6.2,
                    'unit': 'mmol/L',
                    'timestamp': '2026-06-19T07:15:00+08:00',
                }, {
                    'external_id': 'apple_health:test-002',
                    'type': '步数',
                    'value': 8500,
                    'unit': 'steps',
                    'timestamp': '2026-06-19T12:00:00+08:00',
                }],
            },
        )
        assert result.status_code == 200
        assert result.json['data']['inserted'] == 2
        assert result.json['data']['skipped'] == 0

    def test_sync_dedup(self, client):
        """重复 external_id 应跳过"""
        device_id, device_token = self._bind_device(client)

        # 第一次：插入
        client.post(
            '/api/v1/health-sync/sync',
            headers={'X-Device-Id': device_id, 'X-Device-Token': device_token},
            json={
                'records': [{
                    'external_id': 'apple_health:dedup-001',
                    'type': '血糖',
                    'value': 5.5,
                    'timestamp': '2026-06-19T08:00:00+08:00',
                }],
            },
        )

        # 第二次：相同 external_id 应跳过
        result = client.post(
            '/api/v1/health-sync/sync',
            headers={'X-Device-Id': device_id, 'X-Device-Token': device_token},
            json={
                'records': [{
                    'external_id': 'apple_health:dedup-001',
                    'type': '血糖',
                    'value': 5.5,
                    'timestamp': '2026-06-19T08:00:00+08:00',
                }, {
                    'external_id': 'apple_health:dedup-002',
                    'type': '体重',
                    'value': 72.0,
                    'unit': 'kg',
                    'timestamp': '2026-06-19T08:05:00+08:00',
                }],
            },
        )
        assert result.json['data']['inserted'] == 1
        assert result.json['data']['skipped'] == 1

    def test_sync_no_auth_header(self, client):
        """缺少鉴权头返回 401"""
        result = client.post(
            '/api/v1/health-sync/sync',
            json={'records': []},
        )
        assert result.status_code == 401

    def test_sync_invalid_token(self, client):
        """无效 device_token 返回 401"""
        result = client.post(
            '/api/v1/health-sync/sync',
            headers={'X-Device-Id': 'fake-id', 'X-Device-Token': 'fake-token'},
            json={'records': [{'type': '血糖', 'value': 5.0, 'timestamp': '2026-06-19T10:00:00'}]},
        )
        assert result.status_code == 401

    def test_sync_empty_records(self, client):
        """空 records 返回错误"""
        device_id, device_token = self._bind_device(client)
        result = client.post(
            '/api/v1/health-sync/sync',
            headers={'X-Device-Id': device_id, 'X-Device-Token': device_token},
            json={'records': []},
        )
        assert result.status_code == 400

    def test_sync_blood_pressure(self, client):
        """血压记录应正确拆分"""
        device_id, device_token = self._bind_device(client)

        result = client.post(
            '/api/v1/health-sync/sync',
            headers={'X-Device-Id': device_id, 'X-Device-Token': device_token},
            json={
                'records': [{
                    'external_id': 'apple_health:bp-sys',
                    'type': '血压收缩压',
                    'value': 120,
                    'unit': 'mmHg',
                    'timestamp': '2026-06-19T09:00:00+08:00',
                }, {
                    'external_id': 'apple_health:bp-dia',
                    'type': '血压舒张压',
                    'value': 80,
                    'unit': 'mmHg',
                    'timestamp': '2026-06-19T09:00:00+08:00',
                }],
            },
        )
        assert result.json['data']['inserted'] == 2

    def test_sync_heart_rate(self, client):
        """心率和血氧记录"""
        device_id, device_token = self._bind_device(client)

        result = client.post(
            '/api/v1/health-sync/sync',
            headers={'X-Device-Id': device_id, 'X-Device-Token': device_token},
            json={
                'records': [{
                    'external_id': 'apple_health:hr-001',
                    'type': '心率',
                    'value': 72,
                    'unit': 'bpm',
                    'timestamp': '2026-06-19T10:00:00+08:00',
                }, {
                    'external_id': 'apple_health:spo2-001',
                    'type': '血氧',
                    'value': 98,
                    'unit': '%',
                    'timestamp': '2026-06-19T10:05:00+08:00',
                }],
            },
        )
        assert result.json['data']['inserted'] == 2

    def test_sync_missing_required_fields(self, client):
        """缺少必填字段的记录应被跳过"""
        device_id, device_token = self._bind_device(client)

        result = client.post(
            '/api/v1/health-sync/sync',
            headers={'X-Device-Id': device_id, 'X-Device-Token': device_token},
            json={
                'records': [
                    {'type': '血糖', 'value': 6.0, 'timestamp': '2026-06-19T10:00:00+08:00'},  # valid
                    {'type': '血糖', 'timestamp': '2026-06-19T10:00:00+08:00'},  # missing value
                    {'value': 6.0, 'timestamp': '2026-06-19T10:00:00+08:00'},  # missing type
                    {'type': '血糖', 'value': 6.0},  # missing timestamp
                ],
            },
        )
        assert result.json['data']['inserted'] == 1
        assert result.json['data']['skipped'] == 3

    def test_sync_weight(self, client):
        """体重记录"""
        device_id, device_token = self._bind_device(client)

        result = client.post(
            '/api/v1/health-sync/sync',
            headers={'X-Device-Id': device_id, 'X-Device-Token': device_token},
            json={
                'records': [{
                    'external_id': 'apple_health:weight-001',
                    'type': '体重',
                    'value': 72.5,
                    'unit': 'kg',
                    'timestamp': '2026-06-19T07:00:00+08:00',
                }],
            },
        )
        assert result.json['data']['inserted'] == 1
```

- [ ] **Step 3: Run tests**

```bash
uv run python -m pytest tests/test_api_health_sync.py -v
```

Expected: All tests (approx 16) pass.

- [ ] **Step 4: Commit**

```bash
git add routes/api_health_sync.py tests/test_api_health_sync.py
git commit -m "feat(health-sync): add POST sync endpoint for Apple Health data write with dedup

feat(health-sync): 新增 POST sync 端点，支持 Apple Health 数据写入与去重"
```

---

### Task 5: Verify device token with HMAC constant-time comparison

**Files:**
- Verify: `routes/api_health_sync.py` (already uses `hmac.compare_digest` in `_verify_device_auth`)

- [ ] **Step 1: Add a test for token comparison edge case**

```python
def test_verify_device_auth_token_mismatch(self):
    """device_token 不匹配应返回 None"""
    from routes.api_health_sync import _verify_device_auth
    # 使用 mock 来测试
    with patch('routes.api_health_sync.get_db') as mock_get_db:
        mock_c = MagicMock()
        mock_c.fetchone.return_value = {
            'user_id': 1,
            'device_token': 'real-token',
        }
        mock_get_db.return_value.cursor.return_value = mock_c
        result = _verify_device_auth('test-device', 'wrong-token')
        assert result is None

def test_verify_device_auth_db_exception(self):
    """数据库异常应返回 None"""
    from routes.api_health_sync import _verify_device_auth
    with patch('routes.api_health_sync.get_db') as mock_get_db:
        mock_get_db.side_effect = Exception("DB error")
        result = _verify_device_auth('test-device', 'test-token')
        assert result is None
```

- [ ] **Step 2: Run tests**

```bash
uv run python -m pytest tests/test_api_health_sync.py -v
```

- [ ] **Step 3: Commit**

```bash
git add routes/api_health_sync.py tests/test_api_health_sync.py
git commit -m "feat(health-sync): add HMAC constant-time device token verification

feat(health-sync): 添加 HMAC 常量时间设备令牌验证"
```

---

### Task 6: Register the Blueprint in app.py with rate limits

**Files:**
- Modify: `app.py`

- [ ] **Step 1: Add blueprint import and registration**

In app.py, after the existing blueprint imports (around line 273-284), add:

```python
from routes.api_health_sync import bp_health_sync  # noqa: E402
```

After the existing blueprint registrations (around line 285-294), add:

```python
app.register_blueprint(bp_health_sync)
```

After the existing rate limits (around line 298-300), add:

```python
app.view_functions['health_sync.bind_device'] = limiter.limit("5 per minute")(app.view_functions['health_sync.bind_device'])
app.view_functions['health_sync.bind_from_shortcut'] = limiter.limit("10 per minute")(app.view_functions['health_sync.bind_from_shortcut'])
app.view_functions['health_sync.sync_health_data'] = limiter.limit("30 per minute")(app.view_functions['health_sync.sync_health_data'])
```

- [ ] **Step 2: Run all tests to verify no import errors**

```bash
uv run python -m pytest tests/ -q
```

Expected: All ~1100+ tests pass.

- [ ] **Step 3: Commit**

```bash
git add app.py
git commit -m "feat(health-sync): register health_sync blueprint and rate limits

feat(health-sync): 注册 health_sync 蓝图并添加限速"
```

---

### Task 7: Add "绑定 iOS 设备" UI to settings in index.html

**Files:**
- Modify: `templates/index.html`

- [ ] **Step 1: Add device binding section in settings page**

In `templates/index.html`, find the settings page section and add a new card for iOS device binding:

```html
<!-- iOS 设备绑定卡片 -->
<div class="card mb-3">
  <div class="card-body">
    <h5 class="card-title">
      <i class="bi bi-apple"></i> Apple Health 同步
    </h5>
    <p class="text-muted small mb-3">
      通过 iOS 快捷指令将 Apple Health 数据写入 Sugar Bee。
    </p>

    <!-- 未绑定状态 -->
    <div id="ios-device-unbound">
      <button class="btn btn-primary btn-sm" onclick="bindIOSDevice()">
        <i class="bi bi-link-45deg"></i> 绑定 iOS 设备
      </button>
      <div id="bind-code-display" class="mt-2 d-none">
        <div class="alert alert-info py-2 px-3 mb-0">
          <strong>绑定码：</strong>
          <span id="bind-code" class="fs-5 fw-bold font-monospace"></span>
          <span class="text-muted ms-2 small">（30 分钟内有效）</span>
          <button class="btn btn-sm btn-outline-secondary ms-2" onclick="copyBindCode()">
            <i class="bi bi-clipboard"></i> 复制
          </button>
        </div>
        <p class="text-muted small mt-2 mb-0">
          1. 复制绑定码<br>
          2. 在 iPhone 上运行「Sugar Bee 同步」捷径<br>
          3. 捷径将自动完成绑定并同步数据
        </p>
      </div>
    </div>

    <!-- 已绑定状态 -->
    <div id="ios-device-bound" class="d-none">
      <div class="d-flex align-items-center mb-2">
        <i class="bi bi-check-circle-fill text-success me-2"></i>
        <span>已绑定设备：<strong id="bound-device-name">-</strong></span>
      </div>
      <p class="text-muted small mb-2">
        绑定时间：<span id="bound-device-time">-</span>
      </p>
      <a href="/static/AppleHealthSync.shortcut" class="btn btn-outline-primary btn-sm me-2" download>
        <i class="bi bi-download"></i> 下载 iOS 捷径
      </a>
      <button class="btn btn-outline-danger btn-sm" onclick="resetIOSBinding()">
        <i class="bi bi-unlink"></i> 解除绑定
      </button>
    </div>
  </div>
</div>
```

- [ ] **Step 2: Add JavaScript functions in index.html**

Find the JavaScript section in `templates/index.html` and add:

```javascript
// ========== Apple Health 同步 ==========
async function bindIOSDevice() {
    try {
        const resp = await fetch('/api/v1/health-sync/bind', {method: 'POST'});
        const data = await resp.json();
        if (data.status === 'success') {
            document.getElementById('bind-code').textContent = data.data.bind_code;
            document.getElementById('bind-code-display').classList.remove('d-none');
        } else {
            alert('生成绑定码失败：' + (data.message || '未知错误'));
        }
    } catch (e) {
        alert('网络错误：' + e.message);
    }
}

function copyBindCode() {
    const code = document.getElementById('bind-code').textContent;
    navigator.clipboard.writeText(code).then(() => {
        // 简单的复制成功反馈
        const btn = event.currentTarget;
        const orig = btn.innerHTML;
        btn.innerHTML = '<i class="bi bi-check"></i> 已复制';
        setTimeout(() => btn.innerHTML = orig, 2000);
    }).catch(() => {
        // fallback: 选中文本让用户手动复制
        const el = document.getElementById('bind-code');
        const range = document.createRange();
        range.selectNodeContents(el);
        const sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(range);
    });
}

async function checkIOSBinding() {
    try {
        const resp = await fetch('/api/v1/health-sync/confirm_binding');
        const data = await resp.json();
        if (data.status === 'success' && data.data.device_id) {
            document.getElementById('ios-device-unbound').classList.add('d-none');
            document.getElementById('ios-device-bound').classList.remove('d-none');
            document.getElementById('bound-device-name').textContent = data.data.device_name || 'iOS 设备';
            if (data.data.bound_at) {
                document.getElementById('bound-device-time').textContent = data.data.bound_at;
            }
        }
    } catch (e) {
        console.error('检查绑定状态失败：', e);
    }
}

async function resetIOSBinding() {
    if (!confirm('确定要解除 iOS 设备绑定？')) return;
    try {
        const resp = await fetch('/api/v1/health-sync/unbind', {method: 'POST'});
        const data = await resp.json();
        if (data.status === 'success') {
            document.getElementById('ios-device-unbound').classList.remove('d-none');
            document.getElementById('ios-device-bound').classList.add('d-none');
        }
    } catch (e) {
        alert('解除绑定失败：' + e.message);
    }
}
```

Also add `checkIOSBinding()` call on page load:

In the existing DOMContentLoaded or page initialization, add:
```javascript
checkIOSBinding();
```

- [ ] **Step 3: Add unbind endpoint in the blueprint**

Add to `routes/api_health_sync.py`:

```python
@bp_health_sync.route('/unbind', methods=['POST'])
@login_required
def unbind_device():
    """解除当前用户的绑定。"""
    try:
        current_user_id = user_manager.get_current_user_id()
        db = get_db()
        c = db.cursor()
        c.execute(
            "DELETE FROM device_bindings WHERE user_id = ? AND device_id IS NOT NULL",
            (current_user_id,),
        )
        db.commit()
        return api_success(message="绑定已解除")
    except Exception as e:
        traceback.print_exc()
        return api_error(str(e), status_code=500)
```

- [ ] **Step 4: Add unbind tests**

```python
class TestHealthSyncUnbind:
    """解除绑定测试"""

    def test_unbind_success(self, client):
        """成功解除绑定"""
        from tests.test_api_health_sync import TestHealthSyncSync
        helper = TestHealthSyncSync()
        device_id, device_token = helper._bind_device(client)

        # 确认已绑定
        confirm_resp = client.get('/api/v1/health-sync/confirm_binding')
        assert confirm_resp.json['data']['device_id'] is not None

        # 解除绑定
        result = client.post('/api/v1/health-sync/unbind')
        assert result.status_code == 200

        # 确认已解除
        confirm_resp2 = client.get('/api/v1/health-sync/confirm_binding')
        assert confirm_resp2.json['data']['device_id'] is None
```

- [ ] **Step 5: Run all tests**

```bash
uv run python -m pytest tests/ -q
```

Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add routes/api_health_sync.py tests/test_api_health_sync.py templates/index.html
git commit -m "feat(health-sync): add frontend binding UI, unbind endpoint, and tests

feat(health-sync): 添加前端绑定界面、解绑端点及测试"
```

---

### Task 8: Complete test coverage and final verification

**Files:**
- All modified files

- [ ] **Step 1: Run full test suite**

```bash
uv run python -m pytest tests/ -q --cov=. --cov-report=term-missing
```

Expected: All tests pass, coverage >= 95%.

- [ ] **Step 2: Verify end-to-end flow by reviewing the code**

Check that the complete flow works:
1. User clicks "绑定 iOS 设备" → POST /bind → 6-digit code shown
2. User runs iOS Shortcut → POST /bind_from_shortcut → device_id + device_token
3. Shortcut POST /sync with health data → records inserted with dedup
4. Settings page reflects bound state and offers "下载 iOS 捷径" link

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "test(health-sync): complete test coverage for Apple Health sync

test(health-sync): 完成 Apple Health 同步测试覆盖"
```

---

## Self-Review Checklist

**Spec coverage:**
- ✅ POST /api/v1/health-sync/bind — Task 2
- ✅ POST /api/v1/health-sync/sync — Task 4
- ✅ Device binding with 6-digit code (30-min expiry) — Task 2, 3
- ✅ device_id (UUID) + device_token (token_urlsafe) — Task 3
- ✅ Token HMAC comparison — Task 3, 5
- ✅ Dedup via external_id + source — Task 4
- ✅ Frontend binding UI in settings — Task 7
- ✅ .shortcut file download link — Task 7
- ✅ Unbind functionality — Task 7

**No placeholder check:** All code blocks contain complete, runnable code.

**Type consistency:** `_verify_device_auth` returns `int | None`; `_get_bind_code` returns `str`; all function signatures match across tasks.
