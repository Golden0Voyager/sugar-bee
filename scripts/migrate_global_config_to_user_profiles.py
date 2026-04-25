"""一次性数据迁移:user_config.json 全局配置 → user_profiles 表(按用户隔离)。

历史成因:
  /settings POST 路由原本走 settings.save_config(),写入唯一的 user_config.json。
  当一个家庭账户里多人切换登录时,各人保存的身高/体重会互相覆盖,最后一次写入者
  污染所有人的全局值。本次重构把读写都迁移到 user_profiles[user_id] 后,需要把
  user_config.json 中残留的最后一份数据回写到对应用户行,然后弃用全局文件。

默认目标用户:user_id=6 (金虎),因为当前 user_config.json 内容就是她的档案。
如需迁移到其它用户,使用 --user-id 参数覆盖。

执行:
  cd /Users/hainingyu/Code/sugar_bee
  uv run python scripts/migrate_global_config_to_user_profiles.py
执行后(成功或文件不存在均会):user_config.json -> user_config.json.legacy.<timestamp>
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from pathlib import Path

# 将项目根目录加入 sys.path,使脚本可独立运行
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from user_manager import UserManager  # noqa: E402
from core.config import DB_NAME  # noqa: E402

CONFIG_FILE = PROJECT_ROOT / "user_config.json"

# 仅迁移这些字段(其余如 default_meals/target_ranges 不迁,避免覆盖用户当前 DB 设置)
MIGRATABLE_FIELDS = {"name", "birth_year", "height", "weight", "gender", "target_weight"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--user-id", type=int, default=6, help="目标 user_id(默认 6 = 金虎)")
    parser.add_argument("--dry-run", action="store_true", help="只打印将要写入的内容,不实际更新数据库")
    parser.add_argument("--keep-legacy", action="store_true", help="迁移成功后不重命名 user_config.json")
    args = parser.parse_args()

    if not CONFIG_FILE.exists():
        print(f"[skip] 全局配置文件不存在: {CONFIG_FILE}")
        return 0

    try:
        with CONFIG_FILE.open("r", encoding="utf-8") as f:
            global_cfg = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"[error] 读取 {CONFIG_FILE} 失败: {e}")
        return 1

    payload = {k: v for k, v in global_cfg.items() if k in MIGRATABLE_FIELDS}
    if not payload:
        print("[skip] 全局配置无可迁移字段(name/birth_year/height/weight/gender/target_weight)")
        return 0

    um = UserManager(str(PROJECT_ROOT / DB_NAME))
    user = um.get_user(args.user_id)
    if not user:
        print(f"[error] 目标用户 user_id={args.user_id} 不存在或未激活,中止")
        return 1

    print(f"[plan] 目标用户:{user.get('display_name') or user.get('username')} (user_id={args.user_id})")
    print(f"[plan] 当前 DB 值: height={user.get('height')}, weight={user.get('weight')}, target_weight={user.get('target_weight')}")
    print(f"[plan] 待写入字段: {payload}")

    if args.dry_run:
        print("[dry-run] 不写库,退出")
        return 0

    um.update_user_profile_partial(args.user_id, payload)
    refreshed = um.get_user(args.user_id)
    print(f"[ok] 已写入 user_profiles[user_id={args.user_id}]")
    print(f"[ok] 校验:height={refreshed.get('height')}, weight={refreshed.get('weight')}, target_weight={refreshed.get('target_weight')}")

    if not args.keep_legacy:
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        legacy = CONFIG_FILE.with_suffix(f".json.legacy.{ts}")
        os.rename(CONFIG_FILE, legacy)
        print(f"[ok] 已弃用全局文件:{CONFIG_FILE.name} -> {legacy.name}")
    else:
        print("[ok] --keep-legacy:保留 user_config.json 原位")

    return 0


if __name__ == "__main__":
    sys.exit(main())
