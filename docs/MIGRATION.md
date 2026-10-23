# Sugar Bee 服务器迁移手册

> 目标：旧轻量服务器（`124.220.181.225`，**2026-10-31 到期**）完整迁移到新服务器。
> 工具：`scripts/migration/export_server.sh`（旧机导出）+ `scripts/migration/import_server.sh`（新机导入）。

---

## 1. 现网基线（2026-10-19 实测）

| 项目 | 值 |
|---|---|
| 系统 | Ubuntu 24.04 LTS / x86_64 / 2 核 1.7G / 50G 盘（用 12G） |
| Docker | 27.5.1，Compose v2.32.4 |
| nginx | 1.24.0，`/etc/nginx/sites-available/sugar-bee`，`listen 80 default_server` |
| 应用目录 | `/opt/sugar_bee/app`（compose 工作目录，**注意不是** `/opt/sugar_bee`） |
| 代码版本 | `97a182c`（main，与本地一致，仓库公开可 clone） |
| 数据库 | `app/db_data/glucose.db`，6.4M，**22024 条记录 / 3 个用户**，`integrity_check=ok` |
| 历史快照 | `app/backups/` 共 35 份，242M |
| 容器 | `sugar-bee`，`127.0.0.1:5000->5000`，healthy |
| 定时任务 | 每天 03:10 跑 `backup_db.py` 热备 |
| 对外 | `http://124.220.181.225/` → 302 `/login`，**无域名、无 HTTPS** |

---

## 2. 要搬什么 / 不用搬什么

### 必须搬（git 里没有的）

| 资产 | 路径 | 体积 | 说明 |
|---|---|---|---|
| 数据库 | `app/db_data/glucose.db` | 6.4M | 唯一真实数据源 |
| 密钥 | `app/.env` | 1K | 12 项：Garmin 账密、4 个 AI Key、SECRET_KEY 等 |
| Garmin 凭证 | `app/.garmin_tokens/` | 8K | OAuth 长期 token，搬过去免重新登录 |
| 头像 | `app/static/avatars/` | 小 | 用户上传 |
| 用户数据 | `app/user_data/` | 小 | 用户配置 |
| 热备脚本 | `/opt/sugar_bee/backup_db.py` | 2.6K | **不在 git 里，极易漏** |
| 运维配置 | crontab、nginx 站点 | 小 | 脚本已自动抓取 |

### 不用搬

- **代码**：公开仓库可直接 clone 到 `97a182c`；归档内另带 `repo/source.tar.gz`（`git archive` 导出）作为断网兜底
- **镜像**：新机重新 `docker compose build`（旧镜像 725M，搬过去反而慢）
- **`/opt/garmin_mcp_lite`**（79M）：纯 Python venv，需要时重建即可
- **`octop.service`**（8088）、**`tat_agent.service`**：腾讯云控制台自带组件，新机自动有
- **242M 历史快照**：只搬最近 3 份，其余留在旧机到期前别删

---

## 3. 时间线

### T-7（本周内）：先演练一遍

不用等新机到位——拿任意一台临时机器（或本地 Docker）跑一遍导入脚本，确认能起得来。
**没演练过的迁移方案等于没有方案。**

### T-2：准备新机

1. 开一台新轻量服务器（建议 **2 核 2G 起**，1.7G 构建镜像容易 OOM）
2. 系统选 Ubuntu 24.04
3. 防火墙放行 **22 / 80**（腾讯云轻量：防火墙页面；CVM：安全组）
4. 配好 SSH 免密，记下新 IP

### T-1：导出

```bash
# 旧机
ssh lighthouse_sh
bash /root/export_server.sh          # 产出 /tmp/sugar_bee_migrate_<ts>.tar.gz

# 本机
mkdir -p ~/sugar_bee_migration
scp lighthouse_sh:/tmp/sugar_bee_migrate_*.tar.gz* ~/sugar_bee_migration/
cd ~/sugar_bee_migration && shasum -a 256 -c *.sha256
```

### T-0：切换（选低峰，建议晚上）

```bash
# 1) 旧机停写，避免导出后又产生新数据
ssh lighthouse_sh 'cd /opt/sugar_bee/app && docker compose stop'

# 2) 重新导出（这次是静止状态，数据绝对完整）
ssh lighthouse_sh 'bash /root/export_server.sh'
scp lighthouse_sh:/tmp/sugar_bee_migrate_*.tar.gz* ~/sugar_bee_migration/

# 3) 传到新机并导入
scp ~/sugar_bee_migration/sugar_bee_migrate_*.tar.gz* NEW_HOST:/root/
ssh NEW_HOST 'bash import_server.sh /root/sugar_bee_migrate_<ts>.tar.gz'
```

### T+1：观察

- 登录、看历史曲线、加一条记录、触发一次 Garmin 同步
- 次日 03:10 后检查 `/var/log/sugar_bee_backup.log` 是否有新快照
- 确认无误后再释放旧服务器

---

## 4. 验收清单

导入脚本结束时会打印访问地址，逐项打勾：

- [ ] `http://<新IP>/health` 返回 `{"service":"sugar-bee","status":"ok"}`
- [ ] `http://<新IP>/` 302 跳到 `/login`，能正常登录
- [ ] 记录数与 MANIFEST 一致：`docker exec sugar-bee python -c "import sqlite3;print(sqlite3.connect('/app/db_data/glucose.db').execute('select count(*) from records').fetchone()[0])"`
- [ ] 历史曲线、日历、徽章页面数据完整
- [ ] 头像正常显示（说明 `static/avatars` 属主对）
- [ ] Garmin 同步可触发（说明 `.garmin_tokens` 有效）
- [ ] AI 解析可用（说明 `.env` 里的 Key 生效）
- [ ] `docker ps` 显示 healthy
- [ ] `crontab -l` 有 `backup_db.py` 那条
- [ ] 手动跑一次热备，日志有 `SNAPSHOT=...`
- [ ] 新开一条记录后刷新页面，数据在（说明 SQLite 可写，属主没搞错）

---

## 5. 回滚

旧服务器到期前**不要停**，随时可切回：

```bash
# 新机出问题 → 旧机拉起
ssh lighthouse_sh 'cd /opt/sugar_bee/app && docker compose up -d'
```

旧机数据是停止写入那一刻的状态，新机上产生的新记录不会回流。
如果新机已用了一段时间才回滚，需要手工把新机的 `glucose.db` 拿回来覆盖：

```bash
scp NEW_HOST:/opt/sugar_bee/app/db_data/glucose.db /tmp/new.db
scp /tmp/new.db lighthouse_sh:/opt/sugar_bee/app/db_data/glucose.db
ssh lighthouse_sh 'cd /opt/sugar_bee/app && docker compose restart'
```

---

## 6. 已知坑（都已在脚本里处理，但要知道为什么）

### 6.1 `appuser` 的 UID 会变

`Dockerfile` 里是 `useradd -r`，UID 由系统分配。旧机是 `999`，新机可能是 `997`。
挂载目录属主不对 → SQLite 直接报 `attempt to write a readonly database`，应用看着起来了但存不了数据。
导入脚本会从镜像里查出真实 UID 再 `chown -R`，这是**最容易被忽略、也最难排查**的坑。

### 6.2 `backup_db.py` 不在 git 里

它躺在 `/opt/sugar_bee/backup_db.py`，只被 crontab 引用，仓库里没有。
漏搬的后果：迁移后每天热备静默失败，直到需要恢复时才发现没有备份。

### 6.3 `.env` 不在 git 里

12 项密钥全部只存在于旧机磁盘。丢了就得重新申请 Garmin 授权和 4 个 AI Key。
脚本会连同 `.env.bak.*` 一起打包。

### 6.4 新机 IP 变了

`scripts/sync_db_from_lighthouse.sh` 里硬编码了旧 IP `124.220.181.225`，
迁移后本机每周拉取会失败，记得改。

### 6.5 nginx 的 `default_server` 冲突

Ubuntu 默认站点 `/etc/nginx/sites-enabled/default` 也占着 `listen 80 default_server`，
不删掉 `nginx -t` 会报 duplicate default server。脚本里已 `rm -f`。

### 6.6 低内存构建 OOM

1.7G 内存跑 `docker compose build` 有概率被 OOM Killer 干掉。新机建议 2G 起，
或临时加 swap：

```bash
fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab
```

### 6.7 导出后数据还会变

`export_server.sh` 用的是 SQLite Online Backup API，快照本身一致，
但**导出这一刻之后**应用还在写。所以切换当天要先 `docker compose stop` 再导一次。

### 6.8 旧机是浅克隆，不能用 git bundle

`/opt/sugar_bee/app` 是 `git clone --depth 1` 的浅克隆（`.git/shallow`，只有 1 个 commit）。
`git bundle create --all` 打出来的包会引用不存在的父提交，换台机器 clone 直接报
`Failed to traverse parents of commit`。

导出脚本因此改用 `git archive HEAD` 导出工作树——内容与线上运行代码逐字节一致
（已用 `app.py` / `Dockerfile` / `docker-compose.yml` / `wsgi.py` / `gunicorn.conf.py` / `requirements.txt` 六个文件做过 sha256 比对验证）。
代价是不含 git 历史，但服务器上本来也只有 1 个 commit，无所谓。

---

## 7. 演练记录（2026-10-19 已完成）

不是纸面方案——已用真实迁移包在本地 Docker 完整跑通一次：

| 步骤 | 结果 |
|---|---|
| 归档 sha256 | `94ada45b…0f3fac` 通过 |
| 包内 23 个文件逐项 sha256 | 全部通过 |
| `repo/source.tar.gz` 还原 132 个文件 | 通过 |
| 与线上运行代码比对（6 个关键文件） | `app.py` / `Dockerfile` / `docker-compose.yml` / `wsgi.py` / `gunicorn.conf.py` / `requirements.txt` **sha256 全部一致** |
| 从归档源码 `docker build` | 成功 |
| 挂载导出的数据库启动 | `Up (healthy)` |
| `/health` | `{"service":"sugar-bee","status":"ok"}` |
| `/` | `302 → /login`，`/login` 200 |
| `PRAGMA integrity_check` | `ok` |
| 记录数 | **22024**（与 MANIFEST 一致） |
| 用户数 | 3 |
| 数据库可写性 | 建表→回滚 通过 |

> 演练中发现的唯一一个真问题就是 6.8 的浅克隆 —— `git bundle` 还原失败，
> 已改为 `git archive` 方案并复验通过。这就是为什么要先演练。

---

## 8. 迁移后可选优化

- **加域名 + HTTPS**：现在是裸 IP + HTTP，`SESSION_COOKIE_SECURE=false` 是被迫的。
  解析域名 → `certbot --nginx` → 把 compose 里改成 `true`。
- **备份出机器**：`backups/` 和数据库目前只在新机本地。建议加一条异地同步（对象存储或本机拉取）。
- **精简历史快照**：35 份 242M 的日快照保留价值有限，留 7 日 + 4 周足够。
