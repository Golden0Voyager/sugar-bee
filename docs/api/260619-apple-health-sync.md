# Apple Health 同步 API 文档

Base URL: `/api/v1/health-sync`

---

## POST /bind — 生成绑定码

> 需要登录

为当前用户生成 6 位绑定码（30 分钟有效），同一个用户的过期绑定码会被自动清理。

**请求：**
```
POST /api/v1/health-sync/bind
Content-Type: application/json
Cookie: session=...
```

**响应 200：**
```json
{
  "status": "success",
  "data": {
    "bind_code": "123456",
    "expires_in": 1800
  }
}
```

**限速：** 5 次/分钟

---

## POST /bind_from_shortcut — 完成设备绑定

> 无需登录

iOS 捷径使用 6 位绑定码获取 `device_id` 和 `device_token`。绑定码消费后自动清除，不可重复使用。

**请求：**
```
POST /api/v1/health-sync/bind_from_shortcut
Content-Type: application/json

{"code": "123456", "device_name": "iPhone 15"}
```

**响应 200（成功）：**
```json
{
  "status": "success",
  "data": {
    "device_id": "550e8400-e29b-41d4-a716-446655440000",
    "device_token": "EW6kuEGAg7JnkvVKA6mhH-SwBqrKHCo56M7Agmjunu8"
  }
}
```

**错误响应：**

| 状态码 | 说明 |
|--------|------|
| 400 | 绑定码格式错误（非 6 位数字） |
| 404 | 绑定码无效或已过期 |
| 409 | 绑定冲突（并发使用） |

**限速：** 10 次/分钟

---

## GET /confirm_binding — 查询绑定状态

> 需要登录

返回当前用户的设备绑定信息。

**请求：**
```
GET /api/v1/health-sync/confirm_binding
Cookie: session=...
```

**响应 200（已绑定）：**
```json
{
  "status": "success",
  "data": {
    "device_id": "550e8400-...",
    "device_name": "iPhone 15",
    "bound_at": "2026-06-19T14:30:00"
  }
}
```

**响应 200（未绑定）：**
```json
{
  "status": "success",
  "data": {
    "device_id": null
  }
}
```

---

## POST /sync — 同步健康数据

> 无需 Cookie session，使用 X-Device-Id + X-Device-Token 鉴权

写入 Apple Health 数据并基于 `external_id + source` 去重。

**请求：**
```
POST /api/v1/health-sync/sync
X-Device-Id: <device_id>
X-Device-Token: <device_token>
Content-Type: application/json

{
  "records": [
    {
      "external_id": "apple_health:<uuid>",
      "type": "血糖",
      "value": 6.2,
      "unit": "mmol/L",
      "timestamp": "2026-06-19T07:15:00+08:00"
    }
  ]
}
```

**请求字段：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| records | array | 是 | 健康数据条目列表 |
| records[].type | string | 是 | 数据类型（见下方） |
| records[].value | number | 是 | 数值 |
| records[].unit | string | 否 | 单位（默认按 type 推断） |
| records[].timestamp | string | 是 | ISO 8601 时间戳 |
| records[].external_id | string | 否 | 去重 ID，不传则自动生成 |

**支持的数据类型：**

| type | 默认 unit | records 写入字段 |
|------|-----------|-----------------|
| 血糖 | mmol/L | value, type='血糖' |
| 体重 | kg | weight 列 |
| 步数 | steps | steps 列 |
| 血压收缩压 | mmHg | type='血压', systolic_pressure 列 |
| 血压舒张压 | mmHg | type='血压', diastolic_pressure 列 |
| 血氧 | % | spo2 列 |
| 心率 | bpm | heart_rate 列 |

**响应 200：**
```json
{
  "status": "success",
  "data": {
    "inserted": 3,
    "skipped": 0
  }
}
```

**错误响应：**

| 状态码 | 说明 |
|--------|------|
| 401 | 缺少或无效的 X-Device-Id / X-Device-Token |

**去重策略：** 每条记录写入后设 `source='apple_health'`，后续同 `external_id + source` 的记录自动跳过（计入 `skipped`）。

**限速：** 30 次/分钟

---

## POST /unbind — 解除绑定

> 需要登录

删除当前用户的所有设备绑定。之后需要重新走绑定流程才能同步。

**请求：**
```
POST /api/v1/health-sync/unbind
Cookie: session=...
```

**响应 200：**
```json
{
  "status": "success",
  "message": "绑定已解除"
}
```

---

## 鉴权方式总结

| 端点 | 鉴权方式 | 说明 |
|------|---------|------|
| /bind | Cookie Session | 用户在浏览器操作 |
| /bind_from_shortcut | 无（使用绑定码） | iOS 捷径没有 Cookie |
| /confirm_binding | Cookie Session | 用户在浏览器查看 |
| /sync | X-Device-Id + X-Device-Token | 设备令牌，HMAC 比对 |
| /unbind | Cookie Session | 用户在浏览器操作 |

---

## 完整流程

```
用户浏览器                        iOS 捷径                      Sugar Bee API
    │                               │                              │
    │  1. POST /bind (登录后)       │                              │
    │  ← 生成 6 位绑定码 (30min)    │                              │
    │                               │                              │
    │  用户手动复制绑定码到剪贴板     │                              │
    │                               │  2. 读取剪贴板绑定码         │
    │                               │  3. POST /bind_from_shortcut │
    │                               │  ← device_id + device_token  │
    │                               │  4. 保存到 Shortcuts 变量     │
    │                               │                              │
    │                               │  5. POST /sync (定时/手动)   │
    │                               │  ← 写入 records + 去重       │
    │                               │                              │
    │  6. GET /confirm_binding      │                              │
    │  ← 显示已绑定设备信息          │                              │
```
