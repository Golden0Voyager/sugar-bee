#!/usr/bin/env python3
"""Sugar Bee 数据库体检（只读）。

用法：
    python3 scripts/audit_db.py /path/to/glucose.db
    python3 scripts/audit_db.py /path/to/glucose.db --verbose   # 打印样本行

为什么要有它：
    迁移、合并三源数据、批量补录之后，"条数对不对"只能说明没丢，说明不了"对不对"。
    时间戳 UTC/本地混写、AI 预测值写坏、用药方案 end_date 早于 start_date 这类问题
    不会让页面报错，但会让趋势图歪掉。这里把可疑数据一次性摊开。

只读：以 mode=ro 打开，不写任何内容。退出码 = 发现的问题类数（0 表示干净）。
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timedelta

# 各类型的生理合理区间。超出不一定是错，但值得人看一眼。
RANGES = {
    'value': [  # (适用判定列, 值, 下限, 上限, 说明)
        ("type in ('血糖','血糖值','blood_glucose')", None, 1.0, 35.0, '血糖 mmol/L'),
        ("type = '体重'", None, 20.0, 250.0, '体重 kg'),
        ("type = 'SpO2'", None, 50.0, 100.0, '血氧 %'),
        ("type = '体脂率'", None, 2.0, 70.0, '体脂率 %'),
    ],
}


def q(cur, sql, args=()):
    try:
        return cur.execute(sql, args).fetchall()
    except sqlite3.Error as e:
        return [('SQLERR', str(e))]


def section(title):
    print(f'\n{"=" * 62}\n{title}\n{"=" * 62}')


STATS = {'rows': 0, 'cats': 0}


def show(rows, limit, verbose, label=''):
    if not rows:
        print('  ✅ 无')
        return 0
    STATS['rows'] += len(rows)
    STATS['cats'] += 1
    print(f'  ❌ {len(rows)} 条{label}')
    if verbose:
        for r in rows[:limit]:
            print('     ' + ' | '.join(str(x) for x in r))
    else:
        for r in rows[:3]:
            print('     ' + ' | '.join(str(x) for x in r))
        if len(rows) > 3:
            print(f'     ... 其余 {len(rows) - 3} 条')
    return len(rows)


def main(db_path: str, verbose: bool, limit: int) -> int:
    conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
    cur = conn.cursor()
    issues = 0

    print(f'数据库: {db_path}')
    print(f'时间  : {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')

    section('一、总览')
    tables = [r[0] for r in cur.execute(
        "select name from sqlite_master where type='table' order by name")]
    for t in tables:
        n = cur.execute(f'select count(*) from "{t}"').fetchone()[0]
        print(f'  {t:<24} {n:>7}')

    total = cur.execute('select count(*) from records').fetchone()[0]
    pred = cur.execute('select count(*) from records where is_predicted=1').fetchone()[0]
    print(f'\n  records 合计 {total}，其中 AI 预测值 {pred}，真实 {total - pred}')

    users = cur.execute(
        'select user_id, count(*) c from records group by user_id order by c desc').fetchall()
    print(f'  按用户：{"  ".join(f"{u}:{c}" for u, c in users)}')

    section('二、时间戳')
    issues += show(q(cur,
        'select id, user_id, type, timestamp, created_at from records '
        "where timestamp is null or trim(timestamp)=''"), limit, verbose, ' timestamp 为空')

    now = datetime.now()
    future = (now + timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S')
    issues += show(q(cur,
        'select id, user_id, type, timestamp from records where timestamp > ? '
        'order by timestamp desc', (future,)), limit, verbose, ' 时间在未来')

    issues += show(q(cur,
        "select id, user_id, type, timestamp from records "
        "where timestamp < '2015-01-01' order by timestamp"), limit, verbose, ' 时间早于 2015')

    # created_at 早于 timestamp：多为批量导入时倒填写入时间，只影响审计不影响展示
    early = cur.execute(
        "select count(*) from records where created_at is not null and created_at <> '' "
        "and created_at < timestamp").fetchone()[0]
    print(f'  ℹ️  {early} 条 created_at 早于 timestamp（批量导入倒填，属正常）')

    # ±8 小时的特征偏差 = UTC/本地混写的典型指纹（±16h 是二次换算）
    issues += show(q(cur,
        "select id, user_id, type, timestamp, created_at, "
        "round((julianday(created_at)-julianday(timestamp))*24, 2) as gap_h "
        "from records where created_at is not null and created_at <> '' "
        "and (abs(abs((julianday(created_at)-julianday(timestamp))*24) - 8) < 0.5 "
        "  or abs(abs((julianday(created_at)-julianday(timestamp))*24) - 16) < 0.5) "
        "order by id"), limit, verbose, ' created_at 偏差 ≈8h/16h（疑 UTC 混写）')

    backfill = cur.execute(
        "select count(*) from records where created_at is not null and created_at <> '' "
        "and julianday(created_at)-julianday(timestamp) > 1").fetchone()[0]
    print(f'  ℹ️  {backfill} 条为历史回填（created_at 晚于 timestamp 超 24h，属正常）')

    section('三、数值越界')
    issues += show(q(cur,
        "select id, user_id, type, value, timestamp from records "
        "where (type in ('血糖','血糖值') and (value < 1 or value > 35)) "
        "   or (type='体重' and (value < 20 or value > 250)) "
        "   or (type='SpO2' and (value < 50 or value > 100)) "
        "order by type, value"), limit, verbose, ' value 超出合理范围')

    issues += show(q(cur,
        'select id, user_id, systolic_pressure, diastolic_pressure, timestamp from records '
        'where systolic_pressure is not null and ('
        '  systolic_pressure < 50 or systolic_pressure > 260 '
        '  or diastolic_pressure < 30 or diastolic_pressure > 160 '
        '  or systolic_pressure <= diastolic_pressure)'), limit, verbose, ' 血压异常')

    issues += show(q(cur,
        'select id, user_id, heart_rate, timestamp from records '
        'where heart_rate is not null and (heart_rate < 25 or heart_rate > 230)'),
        limit, verbose, ' 心率异常')

    issues += show(q(cur,
        'select id, user_id, pulse_rate, timestamp from records '
        'where pulse_rate = 0 and heart_rate = 0'), limit, verbose, ' pulse_rate=0（表单默认值写进了库）')

    issues += show(q(cur,
        'select id, user_id, steps, timestamp from records '
        'where steps is not null and (steps < 0 or steps > 150000)'),
        limit, verbose, ' 步数异常')

    issues += show(q(cur,
        'select id, user_id, body_fat_rate, timestamp from records '
        'where body_fat_rate is not null and (body_fat_rate < 2 or body_fat_rate > 70)'),
        limit, verbose, ' 体脂率异常')

    section('四、重复与孤儿')
    issues += show(q(cur,
        'select user_id, type, timestamp, value, count(*) c from records '
        'group by user_id, type, timestamp, value having c > 1 order by c desc'),
        limit, verbose, ' 同用户/类型/时间/数值完全重复')

    issues += show(q(cur,
        'select distinct r.user_id from records r '
        'left join app_users u on u.id = r.user_id where u.id is null'),
        limit, verbose, ' user_id 在 app_users 里不存在')

    section('五、关联表')
    issues += show(q(cur,
        'select l.id, l.plan_id from medication_logs l '
        'left join medication_plans p on p.id = l.plan_id where p.id is null'),
        limit, verbose, ' medication_logs 指向不存在的 plan')

    issues += show(q(cur,
        'select id, start_date, end_date from medication_plans '
        "where end_date is not null and end_date <> '' and end_date < start_date"),
        limit, verbose, ' 用药方案 end_date 早于 start_date')

    issues += show(q(cur,
        'select id, user_id, medication_name, start_date from medication_plans '
        "where (end_date is null or end_date='') and start_date > date('now','localtime')"),
        limit, verbose, ' 用药方案开始日期在未来且未结束')

    section('六、来源分布')
    for src, c in cur.execute(
            "select coalesce(source,'(空)'), count(*) from records "
            'group by 1 order by 2 desc').fetchall():
        print(f'  {src:<20} {c}')

    section('七、最近 24 小时写入')
    for r in cur.execute(
            "select timestamp, type, value, user_id from records "
            "where timestamp > datetime('now','localtime','-1 day') "
            'order by timestamp desc limit 10').fetchall():
        print('  ' + ' | '.join(str(x) for x in r))

    conn.close()
    section('结论')
    if STATS['cats'] == 0:
        print('  ✅ 未发现异常')
    else:
        print(f"  ⚠️  {STATS['cats']} 类问题，涉及 {STATS['rows']} 条记录，见上文")
    return 0 if STATS['cats'] == 0 else min(STATS['cats'], 99)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('db')
    ap.add_argument('--verbose', action='store_true', help='打印全部样本行')
    ap.add_argument('--limit', type=int, default=200)
    a = ap.parse_args()
    sys.exit(main(a.db, a.verbose, a.limit))
