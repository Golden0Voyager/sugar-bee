"""
Garmin Connect 同步服务

- 使用 garminconnect 库拉取活动数据
- 凭证从 .env 读取（GARMIN_EMAIL / GARMIN_PASSWORD）
- Token 持久化到 GARMIN_TOKEN_DIR/garmin_tokens.json，避免每次重登
- 按 external_id + source='garmin' 去重
"""
import contextlib
import os
import traceback
from datetime import date, timedelta

from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)

from utils.db import get_raw_conn, put_raw_conn

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOKEN_DIR = os.environ.get('GARMIN_TOKEN_DIR', os.path.join(_PROJECT_ROOT, '.garmin_tokens'))

# Garmin typeKey → 蜜蜂控糖 type 字符串（中文）
@contextlib.contextmanager
def _no_proxy():
    """临时清除代理环境变量，避免终端代理干扰 Garmin 直连。"""
    proxy_keys = [
        'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy',
        'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY',
    ]
    saved = {}
    for key in proxy_keys:
        if key in os.environ:
            saved[key] = os.environ.pop(key)
    try:
        yield
    finally:
        for key, value in saved.items():
            os.environ[key] = value


TYPE_MAP = {
    'running': '跑步',
    'treadmill_running': '跑步',
    'trail_running': '跑步',
    'walking': '走路',
    'hiking': '走路',
    'casual_walking': '走路',
    'cycling': '骑行',
    'road_biking': '骑行',
    'indoor_cycling': '骑行',
    'mountain_biking': '骑行',
    'swimming': '游泳',
    'lap_swimming': '游泳',
    'open_water_swimming': '游泳',
    'strength_training': '健身',
    'indoor_cardio': '健身',
    'fitness_equipment': '健身',
    'yoga': '健身',
}


def _get_client():
    """仅用持久化 token 登录。没有 token 时直接报错，不用密码兜底以免撞 Garmin 限流。
    首次登录请跑 `uv run python3 garmin_login.py` 生成 token。"""
    token_file = os.path.join(TOKEN_DIR, 'garmin_tokens.json')
    if not os.path.isfile(token_file):
        raise RuntimeError(
            f"未找到 Garmin token ({token_file})。请先执行：\n"
            f"  uv run python3 garmin_login.py"
        )
    is_cn = os.environ.get('GARMIN_IS_CN', '').lower() in ('1', 'true', 'yes')
    email = os.environ.get('GARMIN_EMAIL')
    with _no_proxy():
        client = Garmin(email=email, is_cn=is_cn)
        try:
            client.login(TOKEN_DIR)
            return client
        except (GarminConnectAuthenticationError, Exception) as e:
            raise RuntimeError(
                f"Garmin token 失效或登录失败（{e}）。请重新跑：\n"
                f"  uv run python3 garmin_login.py"
            )


def _map_activity(act, user_id):
    """Garmin activity dict → records 插入字段 dict。"""
    type_key = (act.get('activityType') or {}).get('typeKey', '')
    type_cn = TYPE_MAP.get(type_key, '运动')

    duration_sec = act.get('duration') or 0
    duration_str = f"{int(duration_sec // 60)}min" if duration_sec else None

    distance_m = act.get('distance') or 0
    distance_km = round(distance_m / 1000, 2) if distance_m else None

    avg_speed = act.get('averageSpeed')  # m/s
    pace_str = None
    if avg_speed and avg_speed > 0:
        pace_s_per_km = 1000 / avg_speed
        pace_str = f"{int(pace_s_per_km // 60):02d}:{int(pace_s_per_km % 60):02d}"

    max_speed = act.get('maxSpeed')
    max_pace_str = None
    if max_speed and max_speed > 0:
        mp_s = 1000 / max_speed
        max_pace_str = f"{int(mp_s // 60):02d}:{int(mp_s % 60):02d}"

    return {
        'user_id': user_id,
        'type': type_cn,
        'timestamp': act.get('startTimeLocal'),
        'distance': distance_km,
        'duration': duration_str,
        'heart_rate': int(act['averageHR']) if act.get('averageHR') else None,
        'max_heart_rate': int(act['maxHR']) if act.get('maxHR') else None,
        'cadence': int(act['averageRunningCadenceInStepsPerMinute']) if act.get('averageRunningCadenceInStepsPerMinute') else None,
        'calories': int(act['calories']) if act.get('calories') else 0,
        'vo2max': act.get('vO2MaxValue'),
        'steps': int(act['steps']) if act.get('steps') else None,
        'pace': pace_str,
        'max_pace': max_pace_str,
        'external_id': str(act.get('activityId')),
        'source': 'garmin',
        'notes': act.get('activityName', '') or '',
    }


def sync_activities(user_id, days=30):
    """
    同步指定用户近 N 天的 Garmin 活动。
    返回 {'inserted': N, 'skipped': M, 'total': K}
    """
    with _no_proxy():
        client = _get_client()
        end = date.today().isoformat()
        start = (date.today() - timedelta(days=days)).isoformat()

        try:
            activities = client.get_activities_by_date(start, end) or []
        except GarminConnectTooManyRequestsError:
            raise RuntimeError("Garmin 请求过于频繁，稍后再试")
        except GarminConnectConnectionError as e:
            raise RuntimeError(f"Garmin 连接错误：{e}")

        conn = get_raw_conn()
        c = conn.cursor()
        inserted = skipped = 0
        try:
            for act in activities:
                ext_id = str(act.get('activityId') or '')
                if not ext_id:
                    continue
                c.execute(
                    "SELECT id FROM records WHERE source='garmin' AND external_id=? AND user_id=?",
                    (ext_id, user_id)
                )
                if c.fetchone():
                    skipped += 1
                    continue

                try:
                    r = _map_activity(act, user_id)
                except Exception:
                    traceback.print_exc()
                    continue

                c.execute(
                    """INSERT INTO records
                       (user_id, value, unit, type, notes, timestamp, distance, duration,
                        heart_rate, max_heart_rate, cadence, calories, vo2max, steps, pace,
                        max_pace, external_id, source)
                       VALUES (?, 0, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (r['user_id'], r['type'], r['notes'], r['timestamp'], r['distance'],
                     r['duration'], r['heart_rate'], r['max_heart_rate'], r['cadence'],
                     r['calories'], r['vo2max'], r['steps'], r['pace'],
                     r['max_pace'], r['external_id'], r['source'])
                )
                inserted += 1
            conn.commit()
        finally:
            put_raw_conn(conn)

        return {'inserted': inserted, 'skipped': skipped, 'total': len(activities)}
