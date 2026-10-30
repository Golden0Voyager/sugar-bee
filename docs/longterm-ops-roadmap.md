# Sugar Bee 长久运维提升路线图（2-5 年）

> 评估日期：2026-10-30
> 评估方式：架构师 + 运维工程师并行审查 + 交叉核对，逐行实读代码
> 适用范围：单人维护的家庭健康数据系统，4 用户，腾讯云 Lighthouse 4C4G，3 Mbps 出带宽

---

## 一句话结论

**这套系统的"服务可用性"已经好于绝大多数同规模项目（守卫 + 自动回滚 + 真探活 + Bark 告警闭环），但"数据不可逆丢失"维度还有窟窿。** 未来 5 年真正会出大事的只有一类问题——**数据没了但没人知道备份不可用**；全部窟窿可用约 2-3 个工作日补完。架构演进的默认答案是"不演进"。

---

## 系统现状基线

| 维度 | 现状 |
|---|---|
| 栈 | Flask 3 + Jinja2 SSR + gunicorn + SQLite 单文件 + Docker + nginx |
| 规模 | 4 个真实用户（家人），22,123 条健康记录，约 260 条/天 |
| 维护 | 单人，能写脚本改代码，非全职运维/开发 |
| 硬件 | 腾讯云 Lighthouse 4C4G 单机，3 Mbps 出带宽，无域名无 HTTPS，IP 直连 |
| 仓库 | GitHub 公开仓库（唯一事实来源），部署 = rsync → docker build → run_container.sh |
| 测试 | 1890 pytest 全绿 |

## 已有的运维资产（2026-10-30 建成并上线）

1. `run_container.sh`：部署 + 探活三级递进判定 + 15 秒自动回滚、部署前空库硬闸门、`stop -t 35` 优雅停机、日志轮转、部署/守卫互斥锁
2. `health_guard.sh`（cron */10）：paused→unpause、非 running→start、/health 连失 3 次→分类处置（DB 故障只告警不重启，其他重启，限流 30min）、Bark 推送告警
3. `backup_host.sh` + `backup_db.py`（cron 10 3 * * *）：宿主机直读 SQLite Online Backup API 快照，业务时区命名，14 天轮转，失败 Bark 告警
4. `audit_db.py`：只读数据库体检
5. `install_ops.sh`：脚本 + nginx 配置同步（nginx -t 门禁 + 回滚）
6. `/health` 真探活（DB 文件 / 核心表 / 用户表≥1 行 / 模板编译，失败 503，明细仅内网可见）
7. 静态资源路径 hash 版本化（immutable）、HTML ETag/304
8. Bark 告警（Apple 推送）已接 4 类故障

## 三个事实校正（实读代码发现，直接改变判断）

1. **后台任务在 gunicorn master，不在 worker 里**（`gunicorn.conf.py:49` when_ready → `app.py:431` start_background_tasks，threading.Timer 链：auto_backup / auto_garmin_sync / periodic_gcs_backup）。比"跑在 worker 里"更隐蔽：master 线程无监督、不随 max_requests 重启、异常只 print、master 崩则任务全停且无人知。
2. **数据库层已内置 PostgreSQL 逃生舱**（DB_TYPE 切换 + psycopg2 连接池 + `?`↔`%s` 占位符互转 + `migrate_to_cloudsql.py` + `sync_pg_to_sqlite.py` 影子对照工具全在仓库）。"什么时候迁库"不是从零搬家，是"启动已造好的逃生舱"，成本 2-4 个周末。
3. **`services/gcs_sync.py` 是 Cloud Run 时代遗产**，Lighthouse 上未配置 GCS_BUCKET_NAME 时全链路静默跳过。**现状是"本地同盘快照有，异地备份为零"**——别产生"已有异地备份"的错觉。

---

## P0 —— 本季度还（不做迟早出大事）

约 2-3 个工作日，全部可自动化收尾。

| # | 项目 | 风险场景 | 方案 | 工作量 |
|---|---|---|---|---|
| ① | **热拷贝止血** | `app.py:346` auto_backup 用 `shutil.copy2` 直拷活库（非 Online Backup API）；`api_admin.py:36` 下载备份接口同款；`api_admin.py:94` 恢复接口 `.before_restore` 同款——**三处都可能产出撕裂页副本**，外观合法，恢复时选中即拿到坏库 | 摘除 auto_backup/periodic_gcs_backup Timer；把 `backup_db.py` 的 Online Backup 抽成单一 `make_backup(dest_path)`，host cron / 下载接口 / .before_restore 三处共用；存量副本批量 quick_check 后归档 | 半天 |
| ② | **部署固化 `deploy_app.sh`** | rsync 命令只存在于人脑；本地工作树还躺着 `glucose.db`(7M)/`backups/`(90M)——一次疏忽能把**线上库覆盖成旧副本**，空库闸门拦不住"非空旧库覆盖" | `deploy_app.sh` 进仓库：数据路径硬排除 + 推送前后库行数/mtime 比对，不一致即中止 | 1-2h |
| ③ | **WAL + busy_timeout 三路径** | MCP writer（`writer.py:113` sqlite3.connect 无 timeout，默认 5 秒）+ web 请求 + master 后台线程**三方写同一文件**，无 WAL 下读也挡写；MCP 批量写撞锁 5 秒即抛 OperationalError，调用方不重试 = **记录静默丢失** | db.py / writer.py / reader.py 三处统一 `PRAGMA journal_mode=WAL + busy_timeout`；WAL 落地当天全仓 grep `shutil.copy.*db\|cp .*glucose\|rsync.*db_data\|docker cp.*db` 审计留档 | 半日-1 日 |
| ④ | **异地备份（3-2-1）+ 恢复验证** | 快照与源库**同机同盘**——磁盘坏/误删/勒索 = 22,123 条不可重测的记录永久消失 | COS 每日加密推送（桶开版本控制 + 子账号无删除权限）+ 月度加密 U 盘放亲友家；每周自动 `restore_verify`（恢复→完整性→行数断言→Bark）+ 季度完整演练 | 半日-1 日 |
| ⑤ | **cookie 加固** | IP 明文 HTTP，会话 cookie 无 HttpOnly/SameSite | `HttpOnly + SameSite + 短会话`（无争议项，半天内做） | 半天 |
| ⑥ | **整型时间戳→1970 修复** | 既有 bug 持续污染统计与预测（健康数据正确性是产品生命线） | 修解析 + 存量数据 audit/修复脚本 | 1-2 天 |

---

## P1 —— 半年内

| 项目 | 说明 |
|---|---|
| 磁盘余量 + 备份新鲜度探活 | 磁盘 <15% 告警、快照 >26h 未更新告警、大小突变 >50% 告警，全走 Bark |
| **月度 Bark 心跳** | 报磁盘/快照年龄/上次演练日期/git SHA/容器 uptime——顺带**验证告警通道自己没死**（告警通道失效是所有监控里最静默的故障） |
| 外部 uptime 拨测 | 内部 healthcheck 探测不了"机器死了/网断了"。免费档 UptimeRobot 即可 |
| Runbook 进仓库 | `docs/RUNBOOK.md`：系统地图/故障树（照抄可执行的命令块）/恢复流程/密钥存放清单（只写位置不写本体）/交接段——**半年不碰仍能上手** |
| 定时任务脱离 gunicorn master | 备份/Garmin/预测改 systemd timer，web 进程只留请求处理（修掉"Timer 链静默死亡"） |
| GCS 备份安全面复核 | 或删掉遗产代码，或确认 bucket 私有 + 客户端加密 |
| 机器重建预案 | 实做一次：新机 → docker → clone → install_ops → 恢复 DB/密钥，目标 RTO <2h（好消息：状态只剩 .env/.bark_key/crontab，比想象轻） |
| HTTPS（触发条件驱动） | 家人开始在医院/公共 Wi-Fi 访问、或有域名 → 半日内上 TLS（域名约 50 元/年 + Let's Encrypt 自动续期） |
| schema 双轨归一 | init_db 启动迁移与 alembic 并存，DDL 全部收进 alembic，init_db 只调 upgrade head |

---

## P2 —— 触发条件驱动，不预支

| 项目 | 触发条件 |
|---|---|
| **PostgreSQL 迁移** | WAL 落地后 `database is locked` 仍月度到用户视野 / 部署离开单机 / 用户 >20-30。**逃生舱已备好**，到时启动即可 |
| CDN 版本钉死 | 发新版踩过一次再说（半天） |
| MCP writer 改走 HTTP API | 开放注册/信任边界扩大时 |
| GCS 死代码清理 | 防止"以为有异地备份"的错觉复发 |
| 显式事务边界 | 批量写半途失败留部分数据（Garmin 30 天回填） |
| gthread worker + 慢请求隔离 | 用户 >10 且慢请求占满 sync worker |
| Litestream 分钟级 RPO | 真的丢过一天数据再考虑 |

---

## 恢复演练方案（两位专家共同强调的"最重要纪律"）

**每周自动验证**（无人值守，全自动）：
1. 取最新 `glucose_snap_*.db`（将来也取一份 COS 异地副本，异地必须被恢复过才算数）恢复到 `/tmp/restore_verify_$$/`
2. `PRAGMA integrity_check` = ok
3. 行数断言：`app_users`≥4、`records`≥22123（只增不减的历史数据，应单调不降）
4. 抽查 1 条已知记录
5. 失败 Bark critical。**验证失败当 P0 修，优先级高于一切新功能**

**季度完整演练**（人工，30-60 分钟）：
1. 从**异地**取快照（本机恢复练不出异地链路）
2. 恢复到干净目录，跑自动验证的全部断言
3. 起一次性容器挂恢复库（不同端口），确认 /health 绿、login 页能渲染、能登录看到数据
4. **计时**：从"开始取备份"到"/health 绿"的实际耗时 = 实测 RTO（目标 <2h）
5. 同场验证密钥可取：.env/.bark_key 的密封副本能打开
6. 边做边对 runbook 勾选——**演练同时是文档测试**
7. **加半天 PG 逃生舱空跑**：本地起 PG 跑 `migrate_to_cloudsql.py`，验证逃生舱还能用（脚本从未演练过，与"备份没恢复过"同构）

**验收标准**：RPO ≤24h、RTO ≤2h、**异地副本被实际恢复过至少 1 次**。

---

## 灾备方案对比

| 方案 | 做法 | 成本 | 判断 |
|---|---|---|---|
| 腾讯云 COS（推荐主异地） | 每日加密快照上传；桶开版本控制 + 保留策略；CAM 子账号**只给 PutObject，不给 Delete** | 库 7MB gzip 约 2-3MB/日，标准存储每月几分钱 | 首选。便宜到没有理由不做 |
| 加密 U 盘放异地（推荐第二副本） | 每月一次 age/gpg 加密包拷 U 盘，放亲友家 | U 盘一次性 50-100 元；真成本是每月 10 分钟纪律 | 对冲"云账号被盗/同厂商"风险 |
| 私有 GitHub 推加密包 | 周更加密 tarball 进私有仓库 | 0 元 | 可作第三副本；**不可作唯一异地** |
| 第二台 VPS 热备 | 对等机器 + 同步 | 约 30-60 元/月 + 多养一台机器 | **劝退**。4 个用户用不上热备 |
| Lighthouse 整机快照 | 月度整机快照 | 每月几元 | 可选兜底 |
| Litestream 实时复制 | WAL 级连续备份，RPO 到分钟 | 多一个常驻守护进程 | **暂不做**。RPO 24h 够用 |

**落地**：COS 每日（自动） + U 盘/GitHub 月度（人工） + 本地 14 天（已有）= 3 份 2 介质 1 异地。**加密密钥离线双份**（丢了密钥 = 丢了备份，这条比备份本身更容易出事）。

**勒索防护**：COS 侧靠"子账号无删除权限 + 版本控制"；U 盘靠不在线。本地快照目录对勒索软件无抵抗力，所以本地那份永远只算副本之一。

---

## Runbook 四条硬规则

1. **唯一可信备份产物** = `make_backup()` / `glucose_snap_*`；`glucose_auto_*` / 旧 `glucose_backup_*` / `*.before_restore` **一律不可信**，用前 `quick_check`
2. **恢复规范序**（固定序，不带条件分支）：
   ```
   stop 容器（硬前置）→ 删除残留 -wal/-shm → 替换主文件 → quick_check → 启动
   ```
   先删后换的原因：即使有人跳过停写步骤，这个顺序也安全
3. 恢复凭证改名 `.before_restore.db`，修复后才配"可信"标注
4. 存量副本批量 quick_check 后归档（过的留、不过的删）

---

## 实施约束（`make_backup` 单一实现）

- `backup_db.py` 由**宿主机裸 python3** 运行（无 venv、install_ops.sh 复制到 `/opt/sugar_bee/` 独立于 app 树），api_admin.py 在容器 `/app` 内
- 共享函数必须 **stdlib-only**（不得引入 utils.timezone 等 app 依赖）
- 定稿方案：`make_backup()` 留在 `backup_db.py` 内，api_admin 经 sys.path 导入调用；CLI 入口（backup_host.sh 依赖）保持不变
- **防回归护栏**：文件头注释写死"宿主机裸 python3 运行，禁止 app/第三方依赖"；加 10 行 pytest 用 AST 断言其 import 全在 stdlib 白名单

---

## 明确不做（点名劝退）

| 不做 | 为什么 |
|---|---|
| K8s / 容器编排 | 4C4G 单机 4 用户，控制面就吃掉内存预算，纯负资产 |
| CI/CD（GitHub Actions 自动部署） | 部署频率月级，加 CI = 多一套 secrets 管理和新的失败面。等"部署把线上弄坏"两次以上再议 |
| 监控全家桶（Prometheus/Grafana/ELK） | 4 个用户单体 SSR，Bark + 宿主机日志 + /health 明细已覆盖 |
| 蓝绿 / 金丝雀 / 零停机发布 | 家人凌晨被闪断 30 秒无感。已有 15 秒自动回滚 |
| 负载均衡 / CDN / 多可用区 / 异地双活 | 3Mbps 下 4 用户根本打不满 |
| 微服务 | 单人维护，服务间调用/分布式事务全是净负债 |
| GraphQL | SSR + `window.__BOOT__` 已打包首屏数据契约 |
| 前端 SPA 重构（React/Vue） | 8,767 行 vanilla JS 拆 static 走 immutable 缓存是已验证的合理终态 |
| 独立向量库（Milvus/Qdrant） | 无语义检索需求；真需要时 SQLite-vec/pgvector 顺手 |
| 消息队列 | 定时任务用 systemd timer + DB 表足够 |
| Redis / 读写分离 / 分库分表 | HTTP 缓存头 + 单库足够 |
| 多租户 SaaS 化 | 家族仍是一个租户 |
| 迁 PostgreSQL（预支） | WAL + 备份纪律对 4-5 人够用；逃生舱已备好，按信号触发 |

**唯一保留意见**：用户 >10 人、或出现第二个部署环境、或单人换人——CI/CD 与监控的优先级再上调。

---

## 数据层演进判断

**4-10 人规模"WAL + 备份纪律 + SQLite"是正解而非妥协。数据量永远不是迁移理由。**

50 万条（5 年后）对 SQLite 是玩具量级：45 列 records 表 500K 行约 300-500MB，现有索引下点查/范围查仍毫秒级；gzip 后备份约 100-150MB，3Mbps 上传约 5-8 分钟。真实约束是**写并发形态**（MCP 外部进程 + web + master 后台线程三方写一文件），这是现在就痛、与 5 年后数据量无关的点。

**必须迁 PG 的信号**（满足任一即启动，不等数据量）：
1. WAL + busy_timeout 落地后，`database is locked` 仍以月度以上频率到达用户视野
2. 部署形态离开单机（web/DB 分离、第二台机器）
3. 用户 >20-30 且 Garmin 同步窗口出现任务积压
4. 出现 SQLite 表达不了的需求（行级权限、强外键级联、复杂分析 SQL）

**「永不迁 SQLite」是否合理**：4-5 人家庭合理（WAL + 恢复演练 + 异地备份即可）；40 人不合理——届时按信号迁，而非现在预支。

---

## 扩展性边界（4→40 人，最先崩的三处）

1. **写并发与同步风暴**：40 个 Garmin 绑定集中时段回填 + web 写 + MCP 写 → 锁风暴。现在做 WAL + 批量写事务化；到时做同步错峰/队列表、迁 PG
2. **账号治理与信任模型**：管理员名单 + 无邀请/找回/审计是家庭信任模型；40 人 = 半开放系统。现在什么都不做；到时做邀请制注册 + 会话审计
3. **3Mbps 出口 + 单机资源**：40 人并发浏览（gzip 后 50KB/页，约 8 并发即打满出口）+ PDF 导出吃 CPU。现在什么都不做；到时升带宽套餐（运维动作非架构改造）

---

## 运维原则与教训

1. **静态核对替代不了运行时验证**——2026-10-30 的生产事故：review 修复时 `userAge` 行尾误用 Python 风格 `#` 注释，JS 对象字面量里 `#` 是语法错误 → `window.__BOOT__` 赋值失败 → app.js 中断 → 图表全空。三位专家核对了 20 键等价性但没跑一次浏览器。Flask 模板里 `{{ }}` 外的 JS 代码段，注释必须用 `//` 或 `/* */`。
2. **改一处阈值必须 grep 同名常量的全部消费点**——P1c 把预载 90→30 天，但 `app.js` 6 处守卫仍是 `> 90`，导致 60/90 天趋势图静默只画 30 天。
3. **pytest 警告不是噪音**——批量改测试时 `return` 插在断言前，pytest 报了 `ReturnNotNoneWarning` 但没深究，一个回归守卫变成空壳。
4. **备份"在"不等于备份"可用"**——`auto_backup` 用 `shutil.copy2` 直拷活库，产出外观合法但可能撕裂的副本，混在可信备份目录里。从未恢复验证过 = 等于没有备份。
5. **告警通道本身也会死**——Bark key 失效/推送被墙时所有告警静默消失。月度心跳是最低成本的自检。
6. **部署/守卫必须互斥**——不加锁时，部署窗口（最长 90s）内 cron 撞进来会误判"容器不存在"并误重启。
7. **专家推断拓扑行为前要实测**——3 位专家共同报"/health 明细公网泄露（docker-proxy 导致脱敏是死代码）"判 BLOCKER，实测真公网 `GET /health` 无 checks；机制是 `wsgi.py` 的 `ProxyFix(x_for=1)` 已还原真实客户端 IP。

---

## 决策与权衡（专家分歧点的裁决）

| 分歧 | 架构意见 | 运维意见 | 裁决 |
|---|---|---|---|
| HTTPS 优先级 | P0 | P1 首位 | **P1 首位**（用户答：基本只在家用 + 不想买域名）→ 触发条件驱动；cookie 加固先做 |
| WAL 优先级 | P0 | P1 | **P0**——三方写并发是现在就痛的常态路径，且 MCP writer 无 timeout 是丢数据口子 |
| PostgreSQL | 按信号触发 | 不迁 | **一致：不预支**，逃生舱已备好 |
| `auto_backup` 处置 | 摘除 | 改造成 Online Backup | **摘除**——两个备份源 = 恢复时判断题，单一可信源更安全 |
| 恢复顺序 | 先删 -wal/-shm 再换主文件 | 换主文件再删 | **先删后换**——即使跳过停写也安全，不给读者条件分支 |

---

## 实施顺序（无分歧）

```
①热拷贝止血（不等任何前置）→ ②部署固化 → ③WAL 三路径 → ④异地备份+恢复验证
→ ⑤cookie 加固 → ⑥时间戳修复
```

P0 完成后进入 P1（runbook / 磁盘探活 / 外部拨测 / Timer 迁 cron / 机器重建预案）。

---

*本文档由架构师（梁承宇）与运维工程师（陆上线）并行评估 + 交叉核对后汇编。所有结论基于实读代码，不确定项已标注。*
