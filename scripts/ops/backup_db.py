#!/usr/bin/env python3
"""Sugar Bee 数据库一致性快照（在线热备）。

用法（宿主机直跑，不依赖容器）：
    SB_DB=/opt/sugar_bee/app/db_data/glucose.db \
    SB_BACKUP_DIR=/opt/sugar_bee/app/backups \
    python3 scripts/ops/backup_db.py

也可在容器内执行：docker exec -i sugar-bee python - < backup_db.py

为什么不用 shutil.copy2：
  SQLite 是多文件数据库(含 -wal / -shm)，直接拷贝可能拿到写了一半的页，
  而且会漏掉还留在 -wal 里的事务。这里用 sqlite3 官方的 Online Backup API，
  可以在应用正常读写的情况下得到一份事务一致的快照。

时间基准：
  文件名与轮转基准一律用业务时区（默认 Asia/Shanghai），**不用 UTC**。
  历史上用 UTC 时，00:00~08:00 之间生成的快照会被命名成前一天
  （例：10-30 00:01 生成的文件叫 glucose_snap_20261029.db），
  与 app.py 的 glucose_auto_* 命名对不上，人工回溯时极易看错日期。

输出：一行 "SNAPSHOT=<路径> SIZE=<字节> RECORDS=<条数> REMOVED=<n>"

唯一可信实现：
  make_backup() 是**全仓唯一**的备份实现。宿主机 cron（backup_host.sh）、
  web 下载接口（routes/api_admin.py）、恢复前留底，三处都调它。
  任何地方再出现 shutil.copy2(活库) 都属于回归 —— 那会产出"外观合法但可能撕裂"
  的副本，恢复时选中即拿到坏库。

约束：**宿主机裸 python3 运行（无 venv），禁止 import app / 第三方库**。
      只允许 stdlib，改这里前先看 tests/test_backup_db.py 的 AST 守卫。
"""
import datetime
import glob
import os
import sqlite3
import sys

SRC = os.environ.get('SB_DB', '/app/db_data/glucose.db')
DST_DIR = os.environ.get('SB_BACKUP_DIR', '/app/backups')
PREFIX = 'glucose_snap_'
KEEP_DAYS = int(os.environ.get('SB_KEEP_DAYS', '14'))

try:  # 优先用真实时区库；镜像/宿主机都可能缺 tzdata
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:  # pragma: no cover - py<3.9
    ZoneInfo = None

DEFAULT_TZ = 'Asia/Shanghai'
FALLBACK_TZ = datetime.timezone(datetime.timedelta(hours=8), 'CST')


def app_tz():
    name = os.environ.get('SB_TIMEZONE') or os.environ.get('TZ') or DEFAULT_TZ
    if ZoneInfo is None:
        return FALLBACK_TZ
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return FALLBACK_TZ


def local_now():
    """业务时区的 naive 本地时间（文件名 / 轮转统一用它）。"""
    return datetime.datetime.now(app_tz()).replace(tzinfo=None)


def make_backup(dest_path=None, src_path=None):
    """生成一份事务一致的快照，返回 (目标路径, 字节数, records 行数)。

    dest_path 为 None 时落到默认快照目录、按业务日期命名（CLI 用）。
    显式给 dest_path 时用该路径（下载接口 / 恢复前留底用）。

    用 SQLite Online Backup API 而不是 shutil.copy2：
    应用正常读写时也能拿到一致快照，且不会漏掉还在 -wal 里的事务。
    """
    src_path = src_path or SRC
    if not os.path.isfile(src_path):
        raise FileNotFoundError(f'source db not found: {src_path}')

    if dest_path is None:
        os.makedirs(DST_DIR, exist_ok=True)
        dest_path = os.path.join(DST_DIR, f'{PREFIX}{local_now().strftime("%Y%m%d")}.db')
    else:
        parent = os.path.dirname(os.path.abspath(dest_path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        # 覆盖前先删：残留文件里的旧页会和新快照混在一起
        if os.path.exists(dest_path):
            os.remove(dest_path)

    _copy_consistent(src_path, dest_path)
    n = verify_snapshot(dest_path)
    return dest_path, os.path.getsize(dest_path), n


def _copy_consistent(src_path, dest_path):
    """用 Online Backup API 拷一份一致快照。

    源库是 WAL 模式时，只读打开可能因缺 -shm 失败（容器停着、-wal 还在），
    所以先试只读、失败退回读写打开 —— 后者能自己重建 -shm 索引。
    拷完把快照切回 DELETE 模式：WAL 模式的单文件不带 -wal 就是
    "看起来完整其实少最后几个事务"，交付物必须是自包含文件。
    """
    last_err = None
    for read_only in (True, False):
        try:
            src = (sqlite3.connect(f'file:{src_path}?mode=ro', uri=True)
                   if read_only else sqlite3.connect(src_path))
        except sqlite3.Error as e:  # pragma: no cover - 权限问题
            last_err = e
            continue
        try:
            out = sqlite3.connect(dest_path)
            try:
                with out:
                    src.backup(out)
                out.execute('PRAGMA journal_mode=DELETE')
            finally:
                out.close()
            return
        except sqlite3.Error as e:
            last_err = e
        finally:
            src.close()
    raise RuntimeError(f'backup failed: {last_err}')


def verify_snapshot(path):
    """校验快照可用：quick_check 必须 ok，再读一次 records 行数。

    坏快照直接抛异常，绝不返回"看起来正常"的产物。
    """
    conn = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    try:
        rows = [r[0] for r in conn.execute('pragma quick_check').fetchall()]
        if rows != ['ok']:
            raise RuntimeError(f'snapshot quick_check failed: {rows[:3]}')
        return conn.execute('select count(*) from records').fetchone()[0]
    finally:
        conn.close()


def make_snapshot():
    """CLI 用的带日期快照（保持旧签名：返回 (路径, 字节数, 行数)）。"""
    return make_backup()


def rotate():
    cutoff = local_now() - datetime.timedelta(days=KEEP_DAYS)
    removed = []
    for f in glob.glob(os.path.join(DST_DIR, f'{PREFIX}*.db')):
        name = os.path.basename(f).replace(PREFIX, '').replace('.db', '')
        try:
            d = datetime.datetime.strptime(name, '%Y%m%d')
        except ValueError:
            continue
        if d < cutoff:
            try:
                os.remove(f)
                removed.append(os.path.basename(f))
            except OSError:
                pass
    return removed


def main():
    path, size, n = make_snapshot()
    removed = rotate()
    print(f'SNAPSHOT={path} SIZE={size} RECORDS={n} REMOVED={len(removed)}')
    for r in removed:
        print(f'  rotated out: {r}')


if __name__ == '__main__':
    try:
        main()
    except Exception as e:  # noqa: BLE001
        print(f'ERROR: {type(e).__name__}: {e}', file=sys.stderr)
        sys.exit(1)
