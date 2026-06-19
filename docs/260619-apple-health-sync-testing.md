# Apple Health 同步 — 手动测试指南

## 前提

- 本地启动应用：`uv run python app.py`
- 浏览器打开 `http://localhost:5001`
- 准备一台 iPhone（用于运行 iOS 捷径）或使用 curl/Postman 模拟

---

## 测试流程

### 1. 生成绑定码

**方式 A：通过页面操作**

1. 登录蜜蜂控糖
2. 点右上角 ⚙️ → **数据管理** Tab
3. 找到 "Apple Health 同步" 卡片 → 点 **绑定** 按钮
4. 页面显示 6 位绑定码 + 复制按钮

**方式 B：通过 curl**

```bash
# 先登录获取 session（浏览器 DevTools → Application → Cookies 复制 session）
curl -X POST http://localhost:5001/api/v1/health-sync/bind \
  -H "Cookie: session=..." \
  -H "Content-Type: application/json"
```

预期响应：
```json
{"status":"success","data":{"bind_code":"123456","expires_in":1800}}
```

---

### 2. 完成设备绑定

模拟 iOS 捷径调用 `/bind_from_shortcut`：

```bash
curl -X POST http://localhost:5001/api/v1/health-sync/bind_from_shortcut \
  -H "Content-Type: application/json" \
  -d '{"code":"123456","device_name":"iPhone 15"}'
```

预期响应：
```json
{"status":"success","data":{"device_id":"uuid-格式","device_token":"token-格式"}}
```

**保存 device_id 和 device_token**，后面同步要用。

错误情况：
- 无效码 → 404
- 过期码 → 404
- 重复使用 → 404（已绑定的码被清空）
- 并发冲突 → 409

---

### 3. 查询绑定状态

```bash
curl -X GET http://localhost:5001/api/v1/health-sync/confirm_binding \
  -H "Cookie: session=..."
```

预期响应（已绑定）：
```json
{"status":"success","data":{"device_id":"...","device_name":"iPhone 15","bound_at":"2026-06-19T..."}}
```

预期响应（未绑定）：
```json
{"status":"success","data":{"device_id":null}}
```

---

### 4. 同步健康数据

```bash
curl -X POST http://localhost:5001/api/v1/health-sync/sync \
  -H "X-Device-Id: <上一步得到的 device_id>" \
  -H "X-Device-Token: <上一步得到的 device_token>" \
  -H "Content-Type: application/json" \
  -d '{
    "records": [
      {
        "external_id": "apple_health:manual-test-001",
        "type": "血糖",
        "value": 6.2,
        "unit": "mmol/L",
        "timestamp": "2026-06-19T07:15:00+08:00"
      },
      {
        "external_id": "apple_health:manual-test-002",
        "type": "步数",
        "value": 8500,
        "unit": "steps",
        "timestamp": "2026-06-19T12:00:00+08:00"
      },
      {
        "external_id": "apple_health:manual-test-003",
        "type": "体重",
        "value": 72.5,
        "unit": "kg",
        "timestamp": "2026-06-19T07:00:00+08:00"
      }
    ]
  }'
```

预期响应：
```json
{"status":"success","data":{"inserted":3,"skipped":0}}
```

**去重验证**：用相同 external_id 再发一次 → `inserted: 0, skipped: 1`

**支持的 type 列表**：

| type | unit | 说明 |
|------|------|------|
| 血糖 | mmol/L | 血糖值 |
| 体重 | kg | 体重 |
| 步数 | steps | 步数 |
| 血压收缩压 | mmHg | 拆分存储，type='血压', systolic_pressure 字段 |
| 血压舒张压 | mmHg | 拆分存储，type='血压', diastolic_pressure 字段 |
| 血氧 | % | 血氧饱和度 |
| 心率 | bpm | 心率/脉搏 |

---

### 5. 解除绑定

```bash
curl -X POST http://localhost:5001/api/v1/health-sync/unbind \
  -H "Cookie: session=..."
```

预期响应：
```json
{"status":"success","message":"绑定已解除"}
```

解除后 `confirm_binding` 返回 `device_id: null`。

---

## iOS 捷径（Shortcuts）集成

### 捷径工作流

```
1. 从剪贴板获取绑定码
2. POST /bind_from_shortcut → 获取 device_id + device_token
3. 保存 device_id / device_token 到本地变量
4. 读取 Apple Health 数据
5. POST /sync → 写入蜜蜂控糖
6. 显示同步结果
```

### 捷径文件

`.shortcut` 文件需要用 iPhone 上的 **快捷指令 App** 手动创建并导出，目前还没有预制的文件。如果需要可以后续在 iPhone 上制作。

---

## 测试中常见问题

| 问题 | 可能原因 |
|------|---------|
| POST /bind 返回 401 | 未登录 session |
| POST /bind 返回 404 | 路由未注册（检查 app.py） |
| POST /sync 返回 401 | X-Device-Id 或 X-Device-Token 缺失或错误 |
| POST /sync 返回 200 但 inserted=0 | 全部记录因 external_id 重复被跳过，或字段缺失 |
| 前端页面绑定按钮无反应 | 检查浏览器控制台是否有网络错误 |
