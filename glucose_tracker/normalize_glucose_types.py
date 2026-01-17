#!/usr/bin/env python3
"""
数据规范化脚本 - 统一血糖测量类型

功能：将历史数据中的类型变体统一为标准类型
- "早餐后2小时" / "午餐后2小时" / "晚餐后2小时" → "餐后2小时"
- "早饭前" / "午饭前" / "晚饭前" → "餐前"

警告：此脚本会修改数据库！建议先备份 glucose.db
使用方法：python normalize_glucose_types.py [--dry-run]
"""

import sqlite3
import sys
import os
import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME = os.path.join(BASE_DIR, "glucose.db")

def normalize_glucose_type(type_str):
    """将血糖类型变体统一为标准类型"""
    if not type_str:
        return type_str

    # 餐后时间变体 → 标准类型
    if '早餐后2小时' in type_str or '午餐后2小时' in type_str or '晚餐后2小时' in type_str:
        return '餐后2小时'
    if '早餐后1小时' in type_str or '午餐后1小时' in type_str or '晚餐后1小时' in type_str:
        return '餐后1小时'

    # 餐前变体 → 标准类型
    if type_str in ['晚饭前', '午饭前', '早饭前']:
        return '餐前'

    # 其他模糊匹配
    if '餐后' in type_str and type_str not in ['餐后1小时', '餐后2小时']:
        return '餐后2小时'  # 默认归为2小时

    if '餐前' in type_str and type_str != '餐前' and '运动' not in type_str:
        return '餐前'

    return type_str  # 保持原样

def main():
    dry_run = '--dry-run' in sys.argv

    if not os.path.exists(DB_NAME):
        print(f"错误：数据库文件不存在: {DB_NAME}")
        return

    # 备份提醒
    if not dry_run:
        print("⚠️  警告：此操作将修改数据库！")
        backup_name = f"glucose_backup_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
        print(f"建议先备份数据库: cp {DB_NAME} {backup_name}")
        confirm = input("是否继续？(yes/no): ")
        if confirm.lower() != 'yes':
            print("操作已取消")
            return

    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    # 查询所有需要规范化的记录
    c.execute("""
        SELECT id, type, value, timestamp
        FROM records
        WHERE value > 0
        ORDER BY timestamp DESC
    """)

    records = c.fetchall()
    changes = []

    for record_id, old_type, value, timestamp in records:
        new_type = normalize_glucose_type(old_type)
        if new_type != old_type:
            changes.append((record_id, old_type, new_type, value, timestamp))

    if not changes:
        print("✓ 所有记录类型已经是标准格式，无需清理")
        conn.close()
        return

    # 显示变更预览
    print(f"\n发现 {len(changes)} 条需要规范化的记录：")
    print("-" * 80)
    for record_id, old_type, new_type, value, timestamp in changes:
        print(f"ID {record_id:3d} | {timestamp} | {value} mmol/L | '{old_type}' → '{new_type}'")
    print("-" * 80)

    if dry_run:
        print("\n[预览模式] 以上为变更预览，实际未修改数据库")
        print("移除 --dry-run 参数以执行实际修改")
        conn.close()
        return

    # 执行更新
    for record_id, old_type, new_type, value, timestamp in changes:
        c.execute("UPDATE records SET type = ? WHERE id = ?", (new_type, record_id))

    conn.commit()
    print(f"\n✓ 成功更新 {len(changes)} 条记录")

    # 验证结果
    c.execute("""
        SELECT DISTINCT type
        FROM records
        WHERE value > 0
        ORDER BY type
    """)
    print("\n当前数据库中的血糖类型：")
    for (type_name,) in c.fetchall():
        print(f"  - {type_name}")

    conn.close()

if __name__ == '__main__':
    main()
