# RUNBOOK —— Sugar Bee 运维手册

> 写给"半年没碰这套系统的人"（包括未来的你）。
> 每条命令都能直接复制粘贴；不知道从哪下手时，先看「5 分钟自检」。
> 密钥只写**存放位置**，不写本体。

---

## 1. 系统地图

| 东西 | 在哪 | 说明 |
|---|---|---|
| 代码仓库 | GitHub（唯一事实来源） | 线上代码由 `scripts/ops/deploy_app.sh` 推上去 |
| 服务器 | 腾讯云 Lighthouse 4C4G（IP 直连，无域名无 HTTPS） | 登录方式见 §6 |
| 应用目录 | `/opt/sugar_bee/app` | 代码 + `.env` |
| 运维脚本 | `/opt/sugar_bee/` | `backup_db.py` `backup_host.sh` `health_guard.sh` `run_container.sh` `.bark_key` |
| 数据库 | `/opt/sugar_bee/app/db_data/glucose.db` | SQLite 单文件，**WAL 模式**（旁边会有 `-wal` / `-shm`） |
| 备份 | `/opt/sugar_bee/app/backups/` | `glucose_snap_YYYYMMDD.db` 保留 14 天 |
| 容器 | `sugar-bee`（镜像 `sugar-bee:latest`） | 只监听 `127.0.0.1:5000`，公网经 nginx |
| 日志 | `/var/log/sugar_bee_backup.log`、`/var/log/sugar_bee_health.log`、`docker logs sugar-bee` | 健康时守卫**不写**日志，有日志=有异常 |
| 告警 | Bark（Apple 推送） | key 在 `/opt/sugar_bee/.bark_key` |
| 定时任务 | root 的 crontab | `10 3 * * *` 备份、`*/10 * * * *` 健康守卫 |

进程关系（重要）：gunicorn master 起后台线程跑 **Garmin 同步**；**备份不在应用里**，
由宿主机 cron 直读库文件跑 —— 容器停着也照跑。

---

## 2. 5 分钟自检（怀疑出问题时先跑这个）

```bash
# 1) 服务活着吗（本机探活，不经过公网）
ssh root@<IP> 'curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:5000/health'   # 期望 200

# 2) 容器状态
ssh root@<IP> 'docker ps --filter name=sugar-bee --format "{{.Status}}"'

# 3) 最近一次快照是什么时候、多大
ssh root@<IP> 'ls -lh --time-style=long-iso /opt/sugar_bee/app/backups/glucose_snap_* | tail -3'

# 4) 有没有新告警日志（有内容就说明出过事）
ssh root@<IP> 'tail -20 /var/log/sugar_bee_health.log; tail -20 /var/log/sugar_bee_backup.log'

# 5) 磁盘余量（低于 15% 要处理）
ssh root@<IP> 'df -h /opt'
```

四项全绿 = 系统正常。任一项不对，去 §4 故障树按症状找。

---

## 3. 日常动作

### 3.1 发新版（唯一入口，别手打 rsync）

```bash
cd <本地仓库>
git log --oneline -1                      # 确认要发的是哪个 commit
scripts/ops/deploy_app.sh --dry-run       # 先看会做什么（不连服务器）
scripts/ops/deploy_app.sh                 # 推代码 + 重建容器 + 探活
```

脚本会做三件防手滑的事：

1. **硬排除** `db_data/ backups/ .env .garmin_tokens/ user_data/ static/avatars/ *.db*`
   —— 代码归代码，数据归数据；
2. 推送**前后**比对线上库指纹（行数/大小/mtime），不一致立刻中止；
3. 调 `run_container.sh`，它自带三级探活 + 15 秒自动回滚。

目标机地址来自 `~/.sugar_bee_host`，也可 `SB_HOST=root@1.2.3.4` 覆盖。

### 3.2 改运维脚本（备份/守卫/部署）

```bash
scripts/ops/install_ops.sh --dry-run      # 看会同步哪些文件
scripts/ops/install_ops.sh                # scp 到 /opt/sugar_bee/ + 装 cron
```

### 3.3 手动拍一份快照（改数据前必做）

```bash
ssh root@<IP> 'SB_DB=/opt/sugar_bee/app/db_data/glucose.db \
  SB_BACKUP_DIR=/opt/sugar_bee/app/backups python3 /opt/sugar_bee/backup_db.py'
```

输出 `SNAPSHOT=... RECORDS=...` —— 记下 RECORDS 数字，回滚时用它比对。

### 3.4 从线上把库拉到本地看（只读分析）

```bash
scripts/sync_db_from_lighthouse.sh        # 拉到本地，不动线上
```

---

## 4. 故障树（症状 → 判定 → 处置）

### 4.1 网页打不开 / 一直转圈

```bash
docker ps --filter name=sugar-bee          # 容器在不在 running
docker logs --tail 100 sugar-bee           # 看最后 100 行
curl -s http://127.0.0.1:5000/health       # 内网探活
```

| 现象 | 判定 | 处置 |
|---|---|---|
| 容器不存在 / Exited | 崩溃或被停 | `docker start sugar-bee`；再看日志找原因 |
| 容器 Up 但 /health 503 且明细里 `db` 项失败 | 数据库问题 | **不要重启**（重启解决不了），按 4.2 处理 |
| 容器 Up 但 /health 503 且 `template` 失败 | 代码/模板坏 | `./run_container.sh` 重建；仍失败则回滚代码 |
| 容器 Up、/health 200，但公网访问不通 | nginx / 防火墙 | `nginx -t && systemctl reload nginx`；查安全组 |

守卫（cron 每 10 分钟）会自动处理前两种的"其他类故障"，并限流 30 分钟一次。
**如果守卫没动静**：先确认 cron 在跑（`crontab -l`），再确认 `.bark_key` 还有效（见 4.5）。

### 4.2 报 `database is locked`

WAL + 15 秒等锁已开启，理论上不该再频繁出现。真出现了：

```bash
# 谁在占着库（fuser 看不到就查 docker）
ssh root@<IP> 'fuser -v /opt/sugar_bee/app/db_data/glucose.db* 2>&1'
ssh root@<IP> 'docker exec sugar-bee ps aux | grep -c gunicorn'
```

- 偶发一次：等 15 秒重试，通常自愈；
- **月度以上频率**：这是迁 PostgreSQL 的触发信号（逃生舱脚本已备好，见 `scripts/migrate_to_cloudsql.py`），别再硬扛。

### 4.3 磁盘满 / 库文件异常增大

```bash
df -h /opt
du -sh /opt/sugar_bee/app/backups/*
```

- 备份目录该只有 14 份 `glucose_snap_*` + 最多 2 份 `glucose_before_*`；
  多了说明轮转没跑（查 `/var/log/sugar_bee_backup.log`）；
- `-wal` 文件长期不收缩：正常，下次 checkpoint 会回收；若 >1GB 且不降，重启容器触发 checkpoint。

### 4.4 数据看着"少了一截"（最严重）

**第一原则：先停写，再动手。**

```bash
# 1) 停容器（硬前置，不能跳过）
ssh root@<IP> 'docker stop -t 35 sugar-bee'
# 2) 立刻拍一份现场（即使你觉得它是坏的，也要留证）
ssh root@<IP> 'cp -a /opt/sugar_bee/app/db_data/glucose.db /opt/sugar_bee/app/backups/glucose_before_incident_$(date +%Y%m%d%H%M%S).db'
# 3) 看各份快照各有多少条记录，挑"行数对得上"的那份
ssh root@<IP> 'for f in /opt/sugar_bee/app/backups/glucose_snap_*.db; do \
  echo -n "$f "; python3 -c "import sqlite3,sys;print(sqlite3.connect(sys.argv[1]).execute(\"select count(*) from records\").fetchone()[0])" "$f"; done'
```

挑好之后按 §5 恢复。

### 4.5 一条告警都没收到（最阴的故障）

告警通道自己也会死。自检：

```bash
ssh root@<IP> 'test -s /opt/sugar_bee/.bark_key && echo "key 存在" || echo "!! key 丢了"'
# 手动推一条测试
ssh root@<IP> 'curl -s -X POST "https://api.day.app/$(cat /opt/sugar_bee/.bark_key)" \
  -H "Content-Type: application/json" -d "{\"title\":\"自检\",\"body\":\"收到说明通道正常\"}"'
```

手机没响 = 通道坏了（key 失效 / 网络问题），修好之前**所有告警都是哑的**。

---

## 5. 恢复流程（固定序，不要带条件分支）

```
停容器 → 删残留 -wal/-shm → 替换主文件 → quick_check → 启动 → 核对行数
```

```bash
# 1) 停（硬前置）
docker stop -t 35 sugar-bee

# 2) 删残留（顺序不能反：先删后换，即使有人跳过停写也安全）
cd /opt/sugar_bee/app/db_data
rm -f glucose.db-wal glucose.db-shm

# 3) 换主文件（用你挑好的那份快照）
cp /opt/sugar_bee/app/backups/glucose_snap_20261030.db glucose.db
chown 999:999 glucose.db

# 4) 校验（必须 ok，否则换一份快照重来）
python3 -c "import sqlite3;print(sqlite3.connect('glucose.db').execute('pragma quick_check').fetchone())"

# 5) 启动（自带探活 + 回滚）
/opt/sugar_bee/run_container.sh

# 6) 核对
python3 -c "import sqlite3;print(sqlite3.connect('glucose.db').execute('select count(*) from records').fetchone()[0])"
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:5000/health
```

**Web 界面里的"恢复数据库"按钮**同样走这套逻辑（先留底 `.before_restore`、删残留、再替换），
但它是在服务运行中替换文件，风险高于上面的停机流程 —— 能停机就用命令行。

---

## 6. 密钥与凭据清单（只记位置）

| 东西 | 位置 | 丢了会怎样 |
|---|---|---|
| Flask `SECRET_KEY` | 服务器 `/opt/sugar_bee/app/.env` | 所有人被登出，重新登录即可 |
| AI 各家 API key | 同上 `.env` | 识别/预测功能降级 |
| `AGENT_API_TOKEN` | 同上 `.env` | iOS 快捷指令 / MCP 调用 401 |
| Garmin 登录态 | `/opt/sugar_bee/app/.garmin_tokens/` | 需本机跑 `garmin_login.py` 重新授权 |
| Bark 告警 key | `/opt/sugar_bee/.bark_key` | 所有告警静默 |
| SSH 私钥 | 本地 `~/.ssh/sugar_bee_new_agent` | 进不去服务器（走控制台重置） |
| 服务器登录 | 腾讯云控制台（密钥/密码在密码管理器里） | —— |

`.env` 与 `.bark_key` 都不进 git，也**不在备份包里**（备份只备数据库）。
换机器时必须单独搬这三样：`.env`、`.bark_key`、`.garmin_tokens/`。

---

## 7. 备份可信度规则（别在恢复时做判断题）

| 产物 | 可信？ | 说明 |
|---|---|---|
| `glucose_snap_*.db` | ✅ 唯一可信 | 由 `make_backup()`（SQLite Online Backup API）生成，落盘即自检 |
| `glucose_before_*.db` | ⚠️ 看来源 | 手动拍的通常是 `make_backup`，早期可能是 `cp` —— 用前跑 `quick_check` |
| `glucose_auto_*.db` | ❌ 不可信 | 旧版 app 内 `shutil.copy2` 直拷活库的遗留产物，可能撕裂。**已从代码移除** |
| `*.before_restore` | ⚠️ 修复后才可信 | Web 恢复前留底，同样跑过 Online Backup，但恢复过程本身可能出问题 |
| `glucose_backup_*.db` | ❌ 不可信 | 旧下载接口的直拷产物 |

一次性把存量副本过一遍：

```bash
for f in /opt/sugar_bee/app/backups/*.db; do
  r=$(python3 -c "import sqlite3,sys;print(sqlite3.connect(sys.argv[1]).execute('pragma quick_check').fetchone()[0])" "$f" 2>/dev/null)
  echo "$r  $f"
done
# ok 的留，不是 ok 的删（或移走存档）
```

**验收标准**：RPO ≤ 24h（每天一份）、RTO ≤ 2h（照 §5 计时）、
异地副本**被实际恢复过至少 1 次**才算数。

---

## 8. 交接段（换人 / 半年后重上手）

1. 读这份文档 §1 和 §2，能在 10 分钟内判断系统是否健康；
2. 跑一次 `deploy_app.sh --dry-run`，确认本地到服务器的链路是通的；
3. 在**非生产**目录做一次恢复演练（§5 的 1-5 步，用一份旧快照，最后删掉演练库），
   计时并记在下面；
4. 确认 §6 每一行都能拿到（尤其 `.env` 和 `.bark_key`）；
5. 把下面这张表填上，你就接手完了。

| 项目 | 值 |
|---|---|
| 最近一次恢复演练 | （日期 / 实测耗时） |
| 最近一次异地备份验证 | （日期） |
| 最近一次 PG 逃生舱空跑 | （日期） |
| 当前版本 | `git log --oneline -1` |

---

## 9. 明确不做（别在这里加复杂度）

K8s、CI/CD 自动部署、Prometheus/Grafana、蓝绿发布、微服务、Redis、读写分离 ——
4 个用户 4C4G 单机，加这些全是净负债。理由见 `docs/longterm-ops-roadmap.md`
「明确不做（点名劝退）」。
