"""血糖预测的模块门禁测试。

背景（真实 bug）：首页每次加载都会在后台跑空腹血糖预测，却没判断血糖模块是否开启，
于是给不测血糖的家人凭空插了一条「空腹 6.x mmol/L」，还带一段 AI 解释，
在「每日详情」里看起来就像他自己测的。用户的账号甚至攒了 4 条纯 AI 编的记录。

这里守住三层：
1. 服务层：非血糖用户调用预测函数不产生任何记录；
2. 路由层：`/trigger_prediction` 直接拒绝；
3. 数据层：历史脏数据由一次性迁移清掉，且只删 `is_predicted = 1`。
"""
import json
import os
import sqlite3

import branding
from services.prediction_service import glucose_module_enabled

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

GLUCOSE_MODULES = ['glucose', 'blood_pressure', 'weight']
NO_GLUCOSE_MODULES = ['blood_pressure', 'weight', 'exercise', 'medication']


def _set_modules(db_path, user_id, modules):
    conn = sqlite3.connect(db_path)
    conn.execute(
        'INSERT OR REPLACE INTO user_profiles (user_id, name, enabled_modules) VALUES (?, ?, ?)',
        (user_id, f'u{user_id}', json.dumps(modules) if modules is not None else None),
    )
    conn.commit()
    conn.close()


def _count_records(db_path, user_id, predicted=None):
    conn = sqlite3.connect(db_path)
    sql = 'SELECT COUNT(*) FROM records WHERE user_id = ?'
    params = [user_id]
    if predicted is not None:
        sql += ' AND is_predicted = ?'
        params.append(1 if predicted else 0)
    n = conn.execute(sql, params).fetchone()[0]
    conn.close()
    return n


def _make_user(db_path, user_id, modules):
    conn = sqlite3.connect(db_path)
    conn.execute(
        'INSERT OR REPLACE INTO app_users (id, username, display_name, is_active) VALUES (?, ?, ?, 1)',
        (user_id, f'user{user_id}', f'用户{user_id}'),
    )
    conn.commit()
    conn.close()
    _set_modules(db_path, user_id, modules)


def _reset_prediction_state():
    """清掉首页后台预测的进程级节流状态（冷却时间 / 运行锁 / 失败计数）。

    这些状态挂在 `app` 模块的字典里，按 user_id 索引。而 `client_authenticated`
    每个测试都是「先删 `_test` 再重建」，SQLite 的 INTEGER PRIMARY KEY 会把删掉的
    最大 id 重新分配出去——于是上一个测试留下的冷却时间会算到新用户头上，首页
    直接判定「刚跑过，跳过」，测试就会假失败。断言「预测有没有被触发」之前必须清场。
    """
    import app as app_mod

    with app_mod._prediction_lock:
        app_mod._prediction_running.clear()
        app_mod._prediction_last_run.clear()
        app_mod._prediction_fail_count.clear()


class TestGlucoseModuleEnabled:
    def test_disabled_returns_false(self, isolate_db):
        from core.config import get_db_name

        db_path = get_db_name()
        _make_user(db_path, 10, NO_GLUCOSE_MODULES)
        conn = sqlite3.connect(db_path)
        assert glucose_module_enabled(conn.cursor(), 10) is False
        conn.close()

    def test_enabled_returns_true(self, isolate_db):
        from core.config import get_db_name

        db_path = get_db_name()
        _make_user(db_path, 11, GLUCOSE_MODULES)
        conn = sqlite3.connect(db_path)
        assert glucose_module_enabled(conn.cursor(), 11) is True
        conn.close()

    def test_empty_modules_means_all_enabled(self, isolate_db):
        """空列表是历史兼容语义「全部启用」，不能当成关闭。"""
        from core.config import get_db_name

        db_path = get_db_name()
        _make_user(db_path, 12, [])
        conn = sqlite3.connect(db_path)
        assert glucose_module_enabled(conn.cursor(), 12) is True
        conn.close()

    def test_missing_profile_treated_as_enabled(self, isolate_db):
        from core.config import get_db_name

        conn = sqlite3.connect(get_db_name())
        assert glucose_module_enabled(conn.cursor(), 99999) is True
        conn.close()

    def test_corrupt_json_treated_as_enabled(self, isolate_db):
        from core.config import get_db_name

        db_path = get_db_name()
        _make_user(db_path, 13, ['weight'])
        conn = sqlite3.connect(db_path)
        conn.execute("UPDATE user_profiles SET enabled_modules = 'not-json' WHERE user_id = 13")
        conn.commit()
        assert glucose_module_enabled(conn.cursor(), 13) is True
        conn.close()


class TestPredictionEntryPointsSkipNonGlucoseUser:
    """真实入口（首页后台任务 / backfill）不能给非血糖用户生成预测。

    注意：门禁放在入口而不是 `predict_morning_fpg` 内部——那些函数被大量测试用
    精确的 `fetchone.side_effect` 序列驱动，内部多一次查询会把序列整体错位。
    """

    @staticmethod
    def _uid(client):
        with client.session_transaction() as sess:
            return sess['current_user_id']

    def test_index_does_not_start_prediction_for_non_glucose(self, client_authenticated, monkeypatch):
        import time

        import app as app_mod
        from core.config import get_db_name

        called = []
        monkeypatch.setattr(app_mod, 'predict_morning_fpg', lambda *a, **k: called.append('fpg'))
        monkeypatch.setattr(app_mod, 'predict_post_exercise_glucose', lambda *a, **k: called.append('post'))

        _set_modules(get_db_name(), self._uid(client_authenticated), NO_GLUCOSE_MODULES)
        _reset_prediction_state()
        client_authenticated.get('/')
        time.sleep(0.4)

        assert called == [], '没开血糖模块的首页不应触发任何血糖预测'

    def test_index_starts_prediction_for_glucose_user(self, client_authenticated, monkeypatch):
        import time

        import app as app_mod
        from core.config import get_db_name

        called = []
        monkeypatch.setattr(app_mod, 'predict_morning_fpg', lambda *a, **k: called.append('fpg'))

        _set_modules(get_db_name(), self._uid(client_authenticated), GLUCOSE_MODULES)
        _reset_prediction_state()
        client_authenticated.get('/')
        time.sleep(0.4)

        assert called == ['fpg'], '血糖用户的预测不能被误伤'

    def test_backfill_skips_non_glucose_user(self, isolate_db):
        from core.config import get_db_name
        from services.prediction_service import backfill_post_exercise_predictions

        db_path = get_db_name()
        _make_user(db_path, 23, NO_GLUCOSE_MODULES)
        conn = sqlite3.connect(db_path)
        result = backfill_post_exercise_predictions(conn, 23, days=7)
        conn.close()

        assert result['success'] == 0
        assert result['skipped'] == 7


class TestTriggerPredictionRoute:
    @staticmethod
    def _uid(client):
        with client.session_transaction() as sess:
            return sess['current_user_id']

    def test_rejects_non_glucose_user(self, client_authenticated):
        from core.config import get_db_name

        _set_modules(get_db_name(), self._uid(client_authenticated), NO_GLUCOSE_MODULES)

        resp = client_authenticated.post('/trigger_prediction', json={'type': 'all'})
        assert resp.status_code == 400
        body = resp.get_json()
        assert body.get('error_type') == 'module_disabled'
        assert '血糖' in body.get('message', '')

    def test_allows_glucose_user(self, client_authenticated, monkeypatch):
        import routes.api_prediction as mod

        monkeypatch.setattr(mod, 'predict_morning_fpg', lambda *a, **k: None)
        monkeypatch.setattr(mod, 'predict_post_exercise_glucose', lambda *a, **k: None)
        monkeypatch.setattr(mod, 'predict_remaining_glucose_slots', lambda *a, **k: [])

        from core.config import get_db_name

        _set_modules(get_db_name(), self._uid(client_authenticated), GLUCOSE_MODULES)

        resp = client_authenticated.post('/trigger_prediction', json={'type': 'all'})
        assert resp.status_code == 200


class TestStrayPredictionCleanup:
    """一次性迁移：清掉非血糖用户名下的 AI 预测记录。"""

    @staticmethod
    def _seed(db_path, user_id, modules, predicted, real):
        _make_user(db_path, user_id, modules)
        conn = sqlite3.connect(db_path)
        for _ in range(predicted):
            conn.execute(
                "INSERT INTO records (user_id, value, unit, type, notes, timestamp, is_predicted) "
                "VALUES (?, 6.6, 'mmol/L', '空腹', 'AI预测: 测试', '2026-01-01 07:15:00', 1)",
                (user_id,),
            )
        for _ in range(real):
            conn.execute(
                "INSERT INTO records (user_id, value, unit, type, timestamp, is_predicted) "
                "VALUES (?, 130, 'mmHg', '血压测量', '2026-01-01 08:00:00', 0)",
                (user_id,),
            )
        conn.commit()
        conn.close()

    def _run_cleanup(self, db_path, monkeypatch):
        import core.config as config
        import utils.db as db_mod

        monkeypatch.setenv('SUGAR_BEE_DB_PATH', db_path)
        monkeypatch.setattr(config, 'DB_TYPE', 'sqlite')
        conn = sqlite3.connect(db_path)
        conn.execute('DELETE FROM app_migrations')  # 模拟「尚未跑过」
        conn.commit()
        db_mod._apply_prediction_cleanup(conn.cursor())
        conn.commit()
        conn.close()

    def test_deletes_predicted_keeps_real(self, isolate_db, monkeypatch):
        from core.config import get_db_name

        db_path = get_db_name()
        self._seed(db_path, 30, NO_GLUCOSE_MODULES, predicted=3, real=5)

        self._run_cleanup(db_path, monkeypatch)

        assert _count_records(db_path, 30, predicted=True) == 0
        assert _count_records(db_path, 30, predicted=False) == 5, '用户自己的记录一条都不能动'

    def test_keeps_predictions_for_glucose_user(self, isolate_db, monkeypatch):
        from core.config import get_db_name

        db_path = get_db_name()
        self._seed(db_path, 31, GLUCOSE_MODULES, predicted=4, real=2)

        self._run_cleanup(db_path, monkeypatch)

        assert _count_records(db_path, 31, predicted=True) == 4

    def test_runs_only_once(self, isolate_db, monkeypatch):
        """用户以后重新开启血糖、又攒了预测，重启不能把新预测也删掉。"""
        from core.config import get_db_name

        db_path = get_db_name()
        self._seed(db_path, 32, NO_GLUCOSE_MODULES, predicted=2, real=0)
        self._run_cleanup(db_path, monkeypatch)
        assert _count_records(db_path, 32, predicted=True) == 0

        # 之后又出现预测记录（比如用户重新开了血糖）
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO records (user_id, value, unit, type, timestamp, is_predicted) "
            "VALUES (32, 6.0, 'mmol/L', '空腹', '2026-02-01 07:15:00', 1)"
        )
        conn.commit()
        conn.close()

        # 再跑一次（标记已存在，应直接返回）
        import utils.db as db_mod

        conn = sqlite3.connect(db_path)
        db_mod._apply_prediction_cleanup(conn.cursor())
        conn.commit()
        conn.close()

        assert _count_records(db_path, 32, predicted=True) == 1


class TestFrontendGating:
    """前端也要挡住历史脏数据，不能让「预测」徽章和 AI 说明漏出来。"""

    @staticmethod
    def _html():
        with open(os.path.join(REPO_ROOT, 'templates/index.html'), encoding='utf-8') as f:
            return f.read()

    def test_predicted_badge_requires_glucose_module(self):
        html = self._html()
        assert html.count("r.is_predicted && ACTIVE_MODULES.has('glucose')") >= 2, \
            '每日详情和时间轴两处徽章都要判断血糖模块'

    def test_ai_note_hidden_without_glucose(self):
        html = self._html()
        assert html.count("r.notes.indexOf('AI预测:') === 0") >= 2

    def test_predicted_entries_skipped_entirely(self):
        """只藏徽章不够：数值 + 类型（空腹）+ AI 解释会一起漏出来，看着像用户自己测的。

        时间轴和每日详情两个渲染循环都必须整条跳过预测条目。
        """
        html = self._html()
        assert html.count("if (r.is_predicted && !ACTIVE_MODULES.has('glucose')) return;") >= 2

    def test_base_modules_exclude_glucose(self):
        assert 'glucose' not in branding.BASE_MODULES


class TestStalePredictionLink:
    """「预测与实测同值」的历史预测补标已验证的一次性迁移。

    背景：预测验证机制上线前的旧预测从没被标记 verified_by_real_id，
    实测录入后同一天同一时间并排显示两条一样的数值。补标后前端折叠已验证预测，
    重复条目不再显示。只 UPDATE is_predicted = 1 的行，实测记录一条不动。
    """

    @staticmethod
    def _run(db_path, monkeypatch):
        import core.config as config
        import utils.db as db_mod

        monkeypatch.setenv('SUGAR_BEE_DB_PATH', db_path)
        monkeypatch.setattr(config, 'DB_TYPE', 'sqlite')
        conn = sqlite3.connect(db_path)
        conn.execute('DELETE FROM app_migrations')  # 模拟「尚未跑过」
        conn.commit()
        db_mod._apply_stale_prediction_link(conn.cursor())
        conn.commit()
        conn.close()

    @staticmethod
    def _link_state(db_path, record_id):
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            'SELECT is_predicted, verified_by_real_id, prediction_error FROM records WHERE id = ?',
            (record_id,),
        ).fetchone()
        conn.close()
        return row

    def test_identical_prediction_gets_verified(self, isolate_db, monkeypatch):
        """预测与实测同人同时同值 → 预测补标 verified，实测不受影响。"""
        from core.config import get_db_name

        db_path = get_db_name()
        conn = sqlite3.connect(db_path)
        # 实测记录
        conn.execute(
            "INSERT INTO records (user_id, value, unit, type, timestamp, is_predicted) "
            "VALUES (40, 6.9, 'mmol/L', '空腹', '2026-06-20 07:15:00', 0)"
        )
        real_id = conn.execute("SELECT MAX(id) FROM records").fetchone()[0]
        # 同人同时同值的预测（历史遗留未标记）
        conn.execute(
            "INSERT INTO records (user_id, value, unit, type, timestamp, is_predicted) "
            "VALUES (40, 6.9, 'mmol/L', '空腹', '2026-06-20 07:15:00', 1)"
        )
        pred_id = conn.execute("SELECT MAX(id) FROM records").fetchone()[0]
        conn.commit()
        conn.close()

        self._run(db_path, monkeypatch)

        pred = self._link_state(db_path, pred_id)
        assert pred[1] == real_id, '预测应补标指向实测记录'
        assert pred[2] == 0, '同值预测误差为 0'

    def test_different_value_prediction_untouched(self, isolate_db, monkeypatch):
        """预测值与实测不同 → 不算重复，不动。"""
        from core.config import get_db_name

        db_path = get_db_name()
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO records (user_id, value, unit, type, timestamp, is_predicted) "
            "VALUES (41, 6.5, 'mmol/L', '空腹', '2026-06-21 07:15:00', 1)"
        )
        pred_id = conn.execute("SELECT MAX(id) FROM records").fetchone()[0]
        conn.execute(
            "INSERT INTO records (user_id, value, unit, type, timestamp, is_predicted) "
            "VALUES (41, 7.1, 'mmol/L', '空腹', '2026-06-21 07:15:00', 0)"
        )
        conn.commit()
        conn.close()

        self._run(db_path, monkeypatch)

        pred = self._link_state(db_path, pred_id)
        assert pred[1] is None, '不同值的预测是正常预测，不应被标记'

    def test_already_verified_prediction_preserved(self, isolate_db, monkeypatch):
        """已验证的预测 verified_by_real_id 不能被改写。"""
        from core.config import get_db_name

        db_path = get_db_name()
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO records (user_id, value, unit, type, timestamp, is_predicted) "
            "VALUES (42, 8.0, 'mmol/L', '早餐后2小时', '2026-06-22 11:00:00', 0)"
        )
        conn.execute(
            "INSERT INTO records (user_id, value, unit, type, timestamp, is_predicted, "
            "verified_by_real_id, prediction_error) VALUES (42, 8.0, 'mmol/L', '早餐后2小时', "
            "'2026-06-22 11:00:00', 1, 99999, -0.3)"
        )
        pred_id = conn.execute("SELECT MAX(id) FROM records").fetchone()[0]
        conn.commit()
        conn.close()

        self._run(db_path, monkeypatch)

        pred = self._link_state(db_path, pred_id)
        assert pred[1] == 99999, '已验证预测的关联不能被改写'
        assert pred[2] == -0.3

    def test_real_record_never_modified(self, isolate_db, monkeypatch):
        """实测记录（is_predicted=0）无论如何都不会被迁移动 verified 字段。"""
        from core.config import get_db_name

        db_path = get_db_name()
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO records (user_id, value, unit, type, timestamp, is_predicted) "
            "VALUES (43, 6.9, 'mmol/L', '空腹', '2026-06-23 07:15:00', 0)"
        )
        real_id = conn.execute("SELECT MAX(id) FROM records").fetchone()[0]
        conn.commit()
        conn.close()

        self._run(db_path, monkeypatch)

        real = self._link_state(db_path, real_id)
        assert real[0] == 0
        assert real[1] is None

    def test_runs_only_once(self, isolate_db, monkeypatch):
        """标记写入后重跑直接返回：之后新产生的同值预测不再被自动标记。"""
        from core.config import get_db_name

        db_path = get_db_name()
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO records (user_id, value, unit, type, timestamp, is_predicted) "
            "VALUES (44, 6.9, 'mmol/L', '空腹', '2026-06-24 07:15:00', 1)"
        )
        pred_id = conn.execute("SELECT MAX(id) FROM records").fetchone()[0]
        conn.execute(
            "INSERT INTO records (user_id, value, unit, type, timestamp, is_predicted) "
            "VALUES (44, 6.9, 'mmol/L', '空腹', '2026-06-24 07:15:00', 0)"
        )
        conn.commit()
        conn.close()

        self._run(db_path, monkeypatch)

        # 之后又出现一条同值预测（比如老版本应用写入的）
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO records (user_id, value, unit, type, timestamp, is_predicted) "
            "VALUES (44, 6.9, 'mmol/L', '空腹', '2026-06-25 07:15:00', 1)"
        )
        conn.execute(
            "INSERT INTO records (user_id, value, unit, type, timestamp, is_predicted) "
            "VALUES (44, 6.9, 'mmol/L', '空腹', '2026-06-25 07:15:00', 0)"
        )
        new_pred = conn.execute("SELECT MAX(id) FROM records WHERE is_predicted = 1").fetchone()[0]
        conn.commit()
        conn.close()

        # 再跑一次（标记已存在，应直接返回）
        import utils.db as db_mod

        conn = sqlite3.connect(db_path)
        db_mod._apply_stale_prediction_link(conn.cursor())
        conn.commit()
        conn.close()

        new = self._link_state(db_path, new_pred)
        assert new[1] is None, '迁移只跑一次，新数据不被追溯处理'
        old = self._link_state(db_path, pred_id)
        assert old[1] is not None, '第一次跑时已标记的应保持'
