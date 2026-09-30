#!/usr/bin/env python3
"""Sugar Bee 数据库一致性快照（在线热备）。

用法：docker exec -i sugar-bee python - < backup_db.py

为什么不用 shutil.copy2：
  SQLite 是多文件数据库(含 -wal / -shm)，直接拷贝可能拿到写了一半的页。
  这里用 sqlite3 官方的 Online Backup API，可以在应用正常读写的情况下
  得到一份事务一致的快照。

输出：一行 "SNAPSHOT=<路径> SIZE=<字节>"，交给外层脚本解析。
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


def make_snapshot():
    if not os.path.isfile(SRC):
        raise SystemExit(f'ERROR: source db not found: {SRC}')
    os.makedirs(DST_DIR, exist_ok=True)
    # 显式用 UTC：容器是否设了 TZ 不影响快照文件名与轮转基准
    ts = datetime.datetime.now(datetime.UTC).strftime('%Y%m%d')
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
    # 与文件名（%Y%m%d，naive）比较，cutoff 必须同样去掉 tzinfo
    cutoff = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=KEEP_DAYS)).replace(tzinfo=None)
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


if __name__ == '__main__':
    try:
        main()
    except Exception as e:  # noqa: BLE001
        print(f'ERROR: {type(e).__name__}: {e}', file=sys.stderr)
        sys.exit(1)
