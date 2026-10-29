"""
Garmin Connect 同步服务（多用户）

- 每个应用用户在设置页绑定自己的 Garmin 账号，绑定关系存 garmin_bindings 表
- Token 按用户分目录持久化：.garmin_tokens/user_<id>/garmin_tokens.json，互不覆盖
- 只存 token 不存密码；解绑只删本人的绑定与 token
- 老配置（GARMIN_USER_ID + 全局 garmin_tokens.json）自动登记为绑定，平滑迁移
- 同步按 external_id + source='garmin' + user_id 去重，数据只进本人账号
"""
import contextlib
import os
import time
import traceback
from datetime import timedelta

from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)

from services.gcs_sync import sync_file_from_gcs
from utils.db import get_raw_conn, put_raw_conn
from utils.timezone import today as app_today
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOKEN_DIR = os.environ.get('GARMIN_TOKEN_DIR', os.path.join(_PROJECT_ROOT, '.garmin_tokens'))
TOKEN_GCS_PATH = 'garmin_tokens/garmin_tokens.json'


def _ensure_token_from_gcs():
    """本地 Garmin token 缺失时，尝试从 GCS 恢复。"""
    token_file = os.path.join(TOKEN_DIR, 'garmin_tokens.json')
    if os.path.isfile(token_file):
        return True
    try:
        sync_file_from_gcs(TOKEN_GCS_PATH, token_file)
    except Exception as e:
        print(f'[Garmin] 从 GCS 恢复 token 失败: {e}')
        return False
    return os.path.isfile(token_file)


def _patch_garmin_timeout():
    """Monkey-patch garminconnect client 默认超时从 15s 到 30s。"""
    try:
        from garminconnect.client import Client
        orig_run_request = Client._run_request

        def _run_request_patched(self, method, path, **kwargs):
            if 'timeout' not in kwargs:
                kwargs['timeout'] = 30
            return orig_run_request(self, method, path, **kwargs)

        Client._run_request = _run_request_patched
        print('[Garmin] API 默认超时已调整为 30s')
    except Exception as e:
        print(f'[Garmin] 调整 API 超时时失败: {e}')


_patch_garmin_timeout()


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


def _legacy_user_id():
    """老配置：环境变量指定的单账户用户。未配置返回 0。"""
    return int(os.environ.get('GARMIN_USER_ID', 0) or 0)


def _legacy_token_file():
    return os.path.join(TOKEN_DIR, 'garmin_tokens.json')


def _user_token_dir(user_id):
    """每个用户独立的 token 目录，互不覆盖。"""
    return os.path.join(TOKEN_DIR, f'user_{int(user_id)}')


def _resolve_token_dir(user_id):
    """优先用该用户自己的 token；老配置用户回落到全局 token 文件。"""
    per_user = _user_token_dir(user_id)
    if os.path.isfile(os.path.join(per_user, 'garmin_tokens.json')):
        return per_user
    if int(user_id) == _legacy_user_id():
        return TOKEN_DIR
    return per_user


def get_binding(user_id):
    """查用户的 Garmin 绑定关系，未绑定返回 None。"""
    conn = get_raw_conn()
    try:
        c = conn.cursor()
        c.execute('SELECT user_id, email, is_cn FROM garmin_bindings WHERE user_id = ?', (int(user_id),))
        row = c.fetchone()
        if not row:
            return None
        return {'user_id': row['user_id'], 'email': row['email'], 'is_cn': bool(row['is_cn'])}
    finally:
        put_raw_conn(conn)


def list_bindings():
    """全部 Garmin 绑定，供定时同步遍历。"""
    conn = get_raw_conn()
    try:
        c = conn.cursor()
        c.execute('SELECT user_id, email, is_cn FROM garmin_bindings ORDER BY user_id')
        return [
            {'user_id': r['user_id'], 'email': r['email'], 'is_cn': bool(r['is_cn'])}
            for r in c.fetchall()
        ]
    finally:
        put_raw_conn(conn)


def has_garmin_binding(user_id):
    """是否可同步：有绑定记录，或命中老配置（GARMIN_USER_ID + GARMIN_EMAIL）。"""
    if get_binding(user_id):
        return True
    return int(user_id) == _legacy_user_id() and bool(os.environ.get('GARMIN_EMAIL'))


def _account_for(user_id):
    """取登录所需 email/is_cn：绑定记录优先，老配置回落环境变量。"""
    binding = get_binding(user_id)
    if binding:
        return binding
    if int(user_id) == _legacy_user_id() and os.environ.get('GARMIN_EMAIL'):
        return {
            'user_id': int(user_id),
            'email': os.environ['GARMIN_EMAIL'],
            'is_cn': os.environ.get('GARMIN_IS_CN', '').lower() in ('1', 'true', 'yes'),
        }
    return None


def ensure_legacy_binding():
    """老配置一次性登记进 garmin_bindings，token 仍用全局文件，无需重登。"""
    uid = _legacy_user_id()
    email = os.environ.get('GARMIN_EMAIL')
    if not uid or not email or get_binding(uid):
        return
    if not os.path.isfile(_legacy_token_file()):
        return
    _upsert_binding(uid, email, os.environ.get('GARMIN_IS_CN', '').lower() in ('1', 'true', 'yes'))


def _upsert_binding(user_id, email, is_cn):
    conn = get_raw_conn()
    try:
        c = conn.cursor()
        # DELETE + INSERT 两步写，SQLite / PostgreSQL 行为一致，不依赖方言 UPSERT
        c.execute('DELETE FROM garmin_bindings WHERE user_id = ?', (int(user_id),))
        c.execute('INSERT INTO garmin_bindings (user_id, email, is_cn) VALUES (?, ?, ?)',
                  (int(user_id), email, bool(is_cn)))
        conn.commit()
    finally:
        put_raw_conn(conn)


def bind_account(user_id, email, password, is_cn=False):
    """用 Garmin 账号密码登录并把 token 存到该用户自己的目录，只存 token 不存密码。"""
    token_dir = _user_token_dir(user_id)
    os.makedirs(token_dir, exist_ok=True)
    with _no_proxy():
        client = Garmin(email=email, password=password, is_cn=bool(is_cn))
        try:
            client.login(token_dir)
        except GarminConnectAuthenticationError as e:
            raise RuntimeError(
                f'Garmin 认证失败：请检查账号密码；若账号开了二次验证，'
                f'请改用命令行 garmin_login.py --user-id {int(user_id)} 完成绑定（{e}）'
            ) from e
        except Exception as e:
            raise RuntimeError(f'Garmin 登录失败：{e}') from e
    _upsert_binding(user_id, email, is_cn)
    return {'user_id': int(user_id), 'email': email, 'is_cn': bool(is_cn)}


def unbind_account(user_id):
    """解绑：删绑定记录和该用户的 token，不影响其他用户。

    老环境变量是部署级配置，网页解绑无法停用它；明确拒绝以免下次自动绑回。
    """
    if int(user_id) == _legacy_user_id() and os.environ.get('GARMIN_EMAIL'):
        raise RuntimeError('当前 Garmin 由服务器旧配置管理；请先在服务器移除旧配置，再解绑')
    import shutil
    conn = get_raw_conn()
    try:
        c = conn.cursor()
        c.execute('DELETE FROM garmin_bindings WHERE user_id = ?', (int(user_id),))
        conn.commit()
    finally:
        put_raw_conn(conn)
    shutil.rmtree(_user_token_dir(user_id), ignore_errors=True)


# 设置页滑块的可选档位（天），与前端保持一致
SYNC_DAY_OPTIONS = (30, 60, 90, 180, 210, 240)
DEFAULT_SYNC_DAYS = 30


def get_sync_days(user_id):
    """读取该用户的同步天数：未设置/非法值回落到 30，越界夹到 30~240 并就近吸附档位。"""
    try:
        from user_manager import UserManager  # 延迟导入，避免与 app 初始化循环依赖
        cfg = UserManager().get_user_config(user_id) or {}
        raw = cfg.get('garmin_sync_days')
        days = int(raw) if raw else DEFAULT_SYNC_DAYS
    except Exception:
        days = DEFAULT_SYNC_DAYS
    days = max(SYNC_DAY_OPTIONS[0], min(SYNC_DAY_OPTIONS[-1], days))
    return min(SYNC_DAY_OPTIONS, key=lambda x: abs(x - days))


def sync_all_bindings(days=None):
    """遍历所有绑定用户逐个同步，单个失败不影响其他人。返回 {user_id: 结果}。

    days 为空时按每个用户自己的设置（默认 30 天）同步。
    """
    results = {}
    seen = set()
    for binding in list_bindings():
        uid = binding['user_id']
        seen.add(uid)
        try:
            results[uid] = sync_activities(uid, days=days or get_sync_days(uid))
        except Exception as e:
            results[uid] = {'error': str(e)}
    legacy_uid = _legacy_user_id()
    if legacy_uid and legacy_uid not in seen and os.environ.get('GARMIN_EMAIL'):
        try:
            results[legacy_uid] = sync_activities(legacy_uid, days=days or get_sync_days(legacy_uid))
        except Exception as e:
            results[legacy_uid] = {'error': str(e)}
    return results


def _get_client(user_id=None):
    """用持久化 token 登录。user_id 为空时走老配置（GARMIN_USER_ID 全局 token）。

    没有 token 时老配置先尝试从 GCS 恢复，再报错。
    首次登录请跑 `uv run python3 garmin_login.py` 或在设置页绑定。"""
    uid = int(user_id) if user_id else _legacy_user_id()
    account = _account_for(uid) if user_id else None
    if user_id and not account:
        raise RuntimeError('当前用户未绑定 Garmin 账号，请先在设置页绑定')
    token_dir = _resolve_token_dir(uid) if user_id else TOKEN_DIR
    token_file = os.path.join(token_dir, 'garmin_tokens.json')
    if not os.path.isfile(token_file):
        # 老配置（全局 token）保留 GCS 恢复路径，多用户 token 不依赖 GCS
        legacy_token = (not user_id) or (uid == _legacy_user_id() and token_dir == TOKEN_DIR)
        if legacy_token and _ensure_token_from_gcs():
            pass
        else:
            raise RuntimeError(
                f"未找到 Garmin token ({token_file})。请先执行：\n"
                f"  uv run python3 garmin_login.py\n"
                f"或在设置页绑定 Garmin 账号"
            )
    if user_id:
        email, is_cn = account['email'], account['is_cn']
    else:
        email = os.environ.get('GARMIN_EMAIL')
        is_cn = os.environ.get('GARMIN_IS_CN', '').lower() in ('1', 'true', 'yes')
    with _no_proxy():
        client = Garmin(email=email, is_cn=is_cn)
        try:
            client.login(token_dir)
            return client
        except (GarminConnectAuthenticationError, Exception) as e:
            raise RuntimeError(
                f"Garmin token 失效或登录失败（{e}）。请重新跑：\n"
                f"  uv run python3 garmin_login.py\n"
                f"或在设置页重新绑定"
            ) from e


def _get_activities_with_retry(client, start, end):
    """调用 get_activities_by_date，对网络瞬态错误做指数退避重试。"""
    delays = [2, 4, 8]
    last_exc = None
    for attempt, delay in enumerate(delays):
        try:
            return client.get_activities_by_date(start, end) or []
        except GarminConnectTooManyRequestsError:
            raise
        except (GarminConnectConnectionError, TimeoutError, ConnectionError) as e:
            last_exc = e
            if attempt < len(delays) - 1:
                print(f'[Garmin] API 瞬态错误，{delay}s 后重试: {e}')
                time.sleep(delay)
            else:
                break
    raise last_exc


def _map_activity(act, user_id):
    """Garmin activity dict → records 插入字段 dict。"""
    type_key = (act.get('activityType') or {}).get('typeKey', '')
    type_cn = TYPE_MAP.get(type_key, '运动')

    duration_sec = act.get('duration') or 0
    duration_str = f"{int(duration_sec // 60)}min" if duration_sec else None

    distance_m = act.get('distance') or 0
    distance_km = round(distance_m / 1000, 2) if distance_m else None

    avg_speed = act.get('averageSpeed')  # m/s
    # 游泳习惯用 /100m 而不是 /km，否则会出现「30:00/km」这种失真的配速
    is_swim = 'swimming' in type_key
    base_m = 100 if is_swim else 1000
    suffix = '/100m' if is_swim else ''

    def _fmt_pace(speed):
        if not speed or speed <= 0:
            return None
        sec = base_m / speed
        return f"{int(sec // 60):02d}:{int(sec % 60):02d}{suffix}"

    pace_str = _fmt_pace(avg_speed)
    max_pace_str = _fmt_pace(act.get('maxSpeed'))

    # 力量/训练类专属指标：Garmin 提供但过去被丢弃
    moderate_min = act.get('moderateIntensityMinutes') or 0
    vigorous_min = act.get('vigorousIntensityMinutes') or 0
    intensity_minutes = (moderate_min + vigorous_min) or None
    total_sets = act.get('totalSets') or act.get('activeSets')
    total_reps = act.get('totalReps')

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
        'total_sets': int(total_sets) if total_sets else None,
        'total_reps': int(total_reps) if total_reps else None,
        'training_load': round(float(act['activityTrainingLoad']), 2) if act.get('activityTrainingLoad') else None,
        'intensity_minutes': int(intensity_minutes) if intensity_minutes else None,
        'te_aerobic': round(float(act['aerobicTrainingEffect']), 2) if act.get('aerobicTrainingEffect') is not None else None,
        'te_anaerobic': round(float(act['anaerobicTrainingEffect']), 2) if act.get('anaerobicTrainingEffect') is not None else None,
        'elevation_gain': round(float(act['elevationGain']), 1) if act.get('elevationGain') else None,
    }


def sync_activities(user_id, days=30):
    """
    同步指定用户近 N 天的 Garmin 活动。
    返回 {'inserted': N, 'skipped': M, 'total': K}
    """
    with _no_proxy():
        client = _get_client(user_id)
        end_date = app_today()
        end = end_date.isoformat()
        start = (end_date - timedelta(days=days)).isoformat()

        try:
            activities = _get_activities_with_retry(client, start, end)
        except GarminConnectTooManyRequestsError:
            raise RuntimeError("Garmin 请求过于频繁，稍后再试") from None
        except (GarminConnectConnectionError, TimeoutError, ConnectionError) as e:
            raise RuntimeError(f"Garmin 连接超时：{e}") from e

        conn = get_raw_conn()
        c = conn.cursor()
        inserted = skipped = enriched = 0
        try:
            for act in activities:
                ext_id = str(act.get('activityId') or '')
                if not ext_id:
                    continue
                c.execute(
                    "SELECT id FROM records WHERE source='garmin' AND external_id=? AND user_id=?",
                    (ext_id, user_id)
                )
                existing = c.fetchone()
                if existing:
                    # 老记录补齐新字段（只在空值时填，避免覆盖用户已编辑的内容）
                    try:
                        r = _map_activity(act, user_id)
                        filled = c.execute(
                            """UPDATE records SET
                                 total_sets = COALESCE(total_sets, ?),
                                 total_reps = COALESCE(total_reps, ?),
                                 training_load = COALESCE(training_load, ?),
                                 intensity_minutes = COALESCE(intensity_minutes, ?),
                                 te_aerobic = COALESCE(te_aerobic, ?),
                                 te_anaerobic = COALESCE(te_anaerobic, ?),
                                 elevation_gain = COALESCE(elevation_gain, ?)
                               WHERE id = ?""",
                            (r['total_sets'], r['total_reps'], r['training_load'],
                             r['intensity_minutes'], r['te_aerobic'], r['te_anaerobic'],
                             r['elevation_gain'],
                             existing[0] if not hasattr(existing, 'keys') else existing['id'])
                        ).rowcount
                        if filled:
                            enriched += 1
                    except Exception:
                        traceback.print_exc()
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
                        max_pace, external_id, source,
                        total_sets, total_reps, training_load, intensity_minutes,
                        te_aerobic, te_anaerobic, elevation_gain)
                       VALUES (?, 0, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (r['user_id'], r['type'], r['notes'], r['timestamp'], r['distance'],
                     r['duration'], r['heart_rate'], r['max_heart_rate'], r['cadence'],
                     r['calories'], r['vo2max'], r['steps'], r['pace'],
                     r['max_pace'], r['external_id'], r['source'],
                     r['total_sets'], r['total_reps'], r['training_load'],
                     r['intensity_minutes'], r['te_aerobic'], r['te_anaerobic'],
                     r['elevation_gain'])
                )
                inserted += 1
            conn.commit()
        finally:
            put_raw_conn(conn)

        return {'inserted': inserted, 'skipped': skipped, 'enriched': enriched,
                'total': len(activities)}
