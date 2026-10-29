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


def make_snapshot():
    if not os.path.isfile(SRC):
        raise SystemExit(f'ERROR: source db not found: {SRC}')
    os.makedirs(DST_DIR, exist_ok=True)
    ts = local_now().strftime('%Y%m%d')
    dst = os.path.join(DST_DIR, f'{PREFIX}{ts}.db')

    src = sqlite3.connect(f'file:{SRC}?mode=ro', uri=True)
    try:
        out = sqlite3.connect(dst)
        try:
            with out:
                src.backup(out)
        finally:
            out.close()
    finally:
        src.close()

    # 完整性自检：打不开或查不动就说明这份快照有问题
    check = sqlite3.connect(dst)
    try:
        n = check.execute('select count(*) from records').fetchone()[0]
    finally:
        check.close()

    return dst, os.path.getsize(dst), n


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
