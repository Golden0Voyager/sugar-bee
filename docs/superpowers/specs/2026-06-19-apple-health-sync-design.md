# Apple Health iOS Shortcuts 同步接入设计

## 概述

通过 iOS 快捷指令（Shortcuts）将 Apple Health 数据单向写入 Sugar Bee，实现血糖、体重、步数、血压等健康数据自动同步，减少手动录入。

## 架构

```
iOS Shortcuts → POST /api/v1/health-sync/* → Sugar Bee App → records 表
```

## 绑定流程

1. 用户在 Sugar Bee 设置页点击"绑定 iOS 设备"
2. 系统生成 6 位数字绑定码（有效期 30 分钟）
3. 短码显示在页面并自动复制到剪贴板
4. 用户运行 iOS 捷径
5. 捷径从剪贴板读取绑定码
6. 捷径 POST `{"code": "ABC123", "device_name": "iPhone 15"}` 到 `/health-sync/bind`
7. 服务器校验绑定码，返回 `device_id`（UUID） + `device_token`（32 位随机字符串）
8. 捷径将 `device_id` / `device_token` 保存在本地
9. 绑定码标记为已使用

**绑定码存储：** 临时方案存 `settings` 或新建 `device_bindings` 表（取决于方案评审结论），30 分钟过期。

## 同步流程

每次用户手动运行捷径：

1. 捷径读取 Apple Health 中指定时间范围的数据
2. 组装 JSON POST 到 `/health-sync/sync`
3. 请求头携带 `X-Device-Id` + `X-Device-Token`
4. 服务器校验身份后，逐条写入 `records` 表
5. 基于 `external_id`（`apple_health:<uuid>`）+ `source='apple_health'` 去重
6. 返回 `{"status": "success", "inserted": N, "skipped": M}`
7. 捷径展示同步结果

## API 端点

### POST /api/v1/health-sync/bind
请求：`{"code": "string", "device_name": "string"}`
响应：`{"device_id": "uuid", "device_token": "string"}`

### POST /api/v1/health-sync/sync
请求头：`X-Device-Id` + `X-Device-Token`
请求体：
```json
{
  "start_date": "2026-06-19T00:00:00",
  "end_date": "2026-06-19T23:59:59",
  "records": [
    {
      "type": "血糖|体重|步数|血压|血氧|心率",
      "value": 6.2,
      "unit": "mmol/L",
      "timestamp": "2026-06-19T07:15:00+08:00"
    }
  ]
}
```

## 数据映射

| Apple Health 类型 | Sugar Bee type | unit |
|------------------|----------------|------|
| Blood Glucose | 血糖 | mmol/L |
| Body Mass | 体重 | kg |
| Step Count | 步数 | steps |
| Blood Pressure Systolic | 血压收缩压 | mmHg |
| Blood Pressure Diastolic | 血压舒张压 | mmHg |
| Oxygen Saturation | 血氧 | % |
| Heart Rate | 心率 | bpm |

## 安全性

- 绑定码 6 位数字，有效期 30 分钟
- device_token 为 `secrets.token_urlsafe(32)` 生成
- Token 使用 HMAC 对比
- 用户可在设置页重置 device 绑定

## 去重策略

每条记录设置 `external_id = apple_health:<uuid_v4>`，`source = 'apple_health'`。
同步时按 `external_id` + `source` 查询，存在则跳过。

## 不涉及变更

- 数据库 schema（已有 `external_id`、`source` 字段）
- 现有路由和页面
- 不需要前端页面改动（除设置页加一个按钮）

## 后续可扩展

- 定时自动同步（iOS 自动化触发）
- 双向同步
- 多设备绑定