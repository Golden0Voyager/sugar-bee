"""services/prediction_service.py 未覆盖分支测试（7行 → 100%）

覆盖目标:
  L51:     link_prediction_to_real_record — '餐前' type_condition 分支
  L149:    predict_morning_fpg — has_breakfast = True
  L300-302:predict_morning_fpg — except 异常 handler
  L325:    predict_post_exercise_glucose — 无运动记录 return None
  L450:    predict_remaining_glucose_slots — 已有预测跳过 continue
"""
import datetime
import json
import sqlite3
from unittest.mock import patch


from services.prediction_service import (
    link_prediction_to_real_record,
    predict_morning_fpg,
    predict_post_exercise_glucose,
    predict_remaining_glucose_slots,
)

USER_ID = 1


# ============================================================
# 测试辅助函数
# ============================================================

def _setup_user_and_db(db_path, user_id=USER_ID):
    """在给定 DB 路径上创建用户 + 初始化 user_profiles，返回连接"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO app_users (id, username, display_name) VALUES (?, ?, ?)",
              (user_id, 'test', '测试'))
    c.execute("INSERT OR IGNORE INTO user_profiles (user_id) VALUES (?)",
              (user_id,))
    conn.commit()
    return conn


# ============================================================
# link_prediction_to_real_record — L51: 餐前 type_condition
# ============================================================

class TestLinkPredictionCanqian:
    """L51: link_prediction_to_real_record '餐前' type_condition 分支"""

    def test_canqian_type_condition(self, isolate_db):
        """L51: record_type='餐前' → type LIKE '%餐前%' → 匹配预测记录"""
        from core.config import DB_NAME
        conn = _setup_user_and_db(DB_NAME)
        c = conn.cursor()

        # 插入预测记录 type='餐前', is_predicted=1
        c.execute(
            "INSERT INTO records (user_id, value, unit, type, notes, timestamp, is_predicted) "
            "VALUES (?, 5.5, 'mmol/L', '餐前', 'AI预测', '2024-06-01 17:00:00', 1)",
            (USER_ID,))
        pred_id = c.lastrowid
        conn.commit()

        result = link_prediction_to_real_record(
            db=conn,
            real_record_id=100,
            user_id=USER_ID,
            record_date='2024-06-01',
            record_type='餐前',
            real_value=6.0,
            record_timestamp='2024-06-01 17:00:00'
        )

        assert result is not None, "应找到预测记录并关联"
        assert result['predicted_value'] == 5.5
        assert abs(result['error'] - 0.5) < 0.001  # 6.0 - 5.5

        # 验证 UPDATE 生效
        c.execute("SELECT verified_by_real_id, prediction_error FROM records WHERE id = ?", (pred_id,))
        updated = c.fetchone()
        assert updated['verified_by_real_id'] == 100
        assert abs(updated['prediction_error'] - 0.5) < 0.001

        conn.close()


# ============================================================
# predict_morning_fpg — L149: has_breakfast = True
#                      L300-302: except 异常 handler
# ============================================================

class TestPredictMorningFpg:
    """predict_morning_fpg 函数全覆盖"""

    PATCH_AI = 'services.prediction_service.call_ai'
    PATCH_AVAIL = 'services.prediction_service.AI_AVAILABLE'

    def setup_method(self):
        """在每个测试前计算日期常量（避免模块级 import 与运行时跨午夜漂移）"""
        self.today = datetime.datetime.now()
        self.yesterday = self.today - datetime.timedelta(days=1)
        self.today_str = self.today.strftime('%Y-%m-%d')
        self.yesterday_str = self.yesterday.strftime('%Y-%m-%d')

    def _insert_yesterday_glucose(self, c, user_id, values_with_types):
        """插入昨天血糖记录"""
        for val, typ, time_suffix in values_with_types:
            c.execute(
                "INSERT INTO records (user_id, value, unit, type, notes, timestamp, is_predicted) "
                "VALUES (?, ?, 'mmol/L', ?, '', ?, 0)",
                (user_id, val, typ, f"{self.yesterday_str} {time_suffix}"))

    def _insert_recent_fpg(self, c, user_id, days_ago_values):
        """插入近 7 天空腹血糖"""
        for val, days_back in days_ago_values:
            d = (self.today - datetime.timedelta(days=days_back)).strftime('%Y-%m-%d')
            c.execute(
                "INSERT INTO records (user_id, value, unit, type, notes, timestamp, is_predicted) "
                "VALUES (?, ?, 'mmol/L', '空腹', '', ?, 0)",
                (user_id, val, f"{d} 07:15:00"))

    def _insert_yesterday_calories(self, c, user_id, meals):
        """插入昨天饮食记录（calories > 0 触发营养分析循环）"""
        for typ, cal, carbs, gi, time_suffix in meals:
            ts = f"{self.yesterday_str} {time_suffix}"
            c.execute(
                "INSERT INTO records (user_id, value, unit, type, notes, timestamp, is_predicted, calories, carbs_grams, gi_value) "
                "VALUES (?, 0, 'kcal', ?, '', ?, 0, ?, ?, ?)",
                (user_id, typ, ts, cal, carbs, gi))

    def _run_predict_morning_fpg(self, isolate_db, call_ai_return=None,
                                 call_ai_side_effect=None):
        """执行 predict_morning_fpg 的通用封装"""
        from core.config import DB_NAME
        conn = _setup_user_and_db(DB_NAME)
        c = conn.cursor()

        # 昨日血糖数据（使 glucose_summary 有内容）
        self._insert_yesterday_glucose(c, USER_ID, [
            (8.0, '晚餐后2小时', '20:00:00'),
            (7.5, '睡前', '22:00:00'),
        ])

        # 昨日饮食 — 含 '早餐' 类型触发 L149 has_breakfast = True
        self._insert_yesterday_calories(c, USER_ID, [
            ('早餐', 400, 45, 65, '07:30:00'),
            ('午餐', 600, 75, 60, '12:00:00'),
            ('晚餐', 500, 70, 55, '18:00:00'),
        ])

        # 近 7 天空腹血糖趋势
        self._insert_recent_fpg(c, USER_ID, [
            (6.2, 1), (6.5, 3), (5.8, 5),
        ])

        conn.commit()
        conn.close()

        conn2 = sqlite3.connect(DB_NAME)
        conn2.row_factory = sqlite3.Row
        with patch(self.PATCH_AVAIL, True), \
             patch(self.PATCH_AI, return_value=call_ai_return,
                   side_effect=call_ai_side_effect) as mock_ai:
            result = predict_morning_fpg(conn2, user_id=USER_ID)
            return result, conn2, mock_ai

    def test_success_has_breakfast(self, isolate_db):
        """L149: 昨日含 '早餐' 记录 → has_breakfast = True → 完整预测流程"""
        ai_response = json.dumps({"predicted_value": 5.5, "reasoning": "基于昨日数据预测"})
        result, conn, mock_ai = self._run_predict_morning_fpg(
            isolate_db, call_ai_return=ai_response)

        c = conn.cursor()
        c.execute(
            "SELECT value, type, is_predicted, notes FROM records "
            "WHERE user_id = ? AND DATE(timestamp) = ? AND type = '空腹' AND is_predicted = 1",
            (USER_ID, self.today_str))
        row = c.fetchone()
        assert row is not None, "应插入空腹预测记录"
        assert abs(row['value'] - 5.5) < 0.01
        assert row['type'] == '空腹'
        assert row['is_predicted'] == 1
        assert 'AI预测' in (row['notes'] or '')
        conn.close()

    def test_exception_handler(self, isolate_db):
        """L300-302: call_ai 抛出异常 → except 捕获 → 返回 None"""
        result, conn, mock_ai = self._run_predict_morning_fpg(
            isolate_db, call_ai_side_effect=Exception("AI service timeout"))
        assert result is None, "异常时应返回 None"
        conn.close()

    def test_no_glucose_data(self, isolate_db):
        """无昨日血糖数据时也能正常执行"""
        from core.config import DB_NAME
        conn = _setup_user_and_db(DB_NAME)
        c = conn.cursor()
        self._insert_yesterday_calories(c, USER_ID, [
            ('早餐', 400, 45, 65, '07:30:00'),
        ])
        conn.commit()
        conn.close()

        ai_response = json.dumps({"predicted_value": 5.5, "reasoning": "test"})
        conn2 = sqlite3.connect(DB_NAME)
        conn2.row_factory = sqlite3.Row
        with patch(self.PATCH_AVAIL, True), \
             patch(self.PATCH_AI, return_value=ai_response):
            predict_morning_fpg(conn2, user_id=USER_ID)

        c2 = conn2.cursor()
        c2.execute(
            "SELECT value FROM records "
            "WHERE user_id = ? AND DATE(timestamp) = ? AND type = '空腹' AND is_predicted = 1",
            (USER_ID, self.today_str))
        row = c2.fetchone()
        assert row is not None, "应有预测记录"
        conn2.close()


# ============================================================
# predict_post_exercise_glucose — L325: 无运动记录 return None
# ============================================================

class TestPredictPostExercise:
    """predict_post_exercise_glucose 函数测试"""

    PATCH_AI = 'services.prediction_service.call_ai'
    PATCH_AVAIL = 'services.prediction_service.AI_AVAILABLE'

    def setup_method(self):
        self.today = datetime.datetime.now()
        self.today_str = self.today.strftime('%Y-%m-%d')

    def _setup_basic(self, db_path, has_exercise=True):
        """通用数据设置：用户 + 基础血糖记录"""
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("INSERT OR IGNORE INTO app_users (id, username, display_name) VALUES (?, ?, ?)",
                  (USER_ID, 'test', '测试'))
        c.execute("INSERT OR IGNORE INTO user_profiles (user_id) VALUES (?)",
                  (USER_ID,))

        c.execute(
            "INSERT INTO records (user_id, value, unit, type, notes, timestamp, is_predicted) "
            "VALUES (?, 5.5, 'mmol/L', '空腹', '', ?, 0)",
            (USER_ID, f"{self.today_str} 07:15:00"))

        if has_exercise:
            c.execute(
                "INSERT INTO records (user_id, value, unit, type, notes, timestamp, is_predicted, distance, duration, heart_rate, calories) "
                "VALUES (?, 0, '', '跑步', '', ?, 0, 5.0, '30:00', 130, 350)",
                (USER_ID, f"{self.today_str} 08:00:00"))

        conn.commit()
        return conn

    def test_no_exercise_record(self, isolate_db):
        """L325: 当日无运动记录 → return None"""
        from core.config import DB_NAME
        conn = self._setup_basic(DB_NAME, has_exercise=False)
        conn.close()

        conn2 = sqlite3.connect(DB_NAME)
        conn2.row_factory = sqlite3.Row
        with patch(self.PATCH_AVAIL, True), \
             patch(self.PATCH_AI) as mock_ai:
            result = predict_post_exercise_glucose(conn2, user_id=USER_ID)
            mock_ai.assert_not_called()
        assert result is None
        conn2.close()

    def test_success(self, isolate_db):
        """有运动记录 → 生成预测并插入 DB"""
        from core.config import DB_NAME
        conn = self._setup_basic(DB_NAME, has_exercise=True)
        conn.close()

        conn2 = sqlite3.connect(DB_NAME)
        conn2.row_factory = sqlite3.Row
        ai_response = json.dumps({"predicted_value": 6.5, "reasoning": "运动后预测"})
        with patch(self.PATCH_AVAIL, True), \
             patch(self.PATCH_AI, return_value=ai_response):
            result = predict_post_exercise_glucose(conn2, user_id=USER_ID)
        assert result is not None
        assert abs(result - 6.5) < 0.01

        c2 = conn2.cursor()
        c2.execute(
            "SELECT value, type, is_predicted FROM records "
            "WHERE user_id = ? AND DATE(timestamp) = ? AND type = '运动后' AND is_predicted = 1",
            (USER_ID, self.today_str))
        row = c2.fetchone()
        assert row is not None
        assert abs(row['value'] - 6.5) < 0.01
        conn2.close()


# ============================================================
# predict_remaining_glucose_slots — L450: 已有预测跳过 continue
# ============================================================

class TestPredictRemaining:
    """predict_remaining_glucose_slots 函数测试"""

    PATCH_AI = 'services.prediction_service.call_ai'
    PATCH_AVAIL = 'services.prediction_service.AI_AVAILABLE'

    def setup_method(self):
        self.today = datetime.datetime.now()
        self.today_str = self.today.strftime('%Y-%m-%d')

    def _setup_measured_only(self, db_path):
        """插入今日实测数据 + 一条已有预测记录"""
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("INSERT OR IGNORE INTO app_users (id, username, display_name) VALUES (?, ?, ?)",
                  (USER_ID, 'test', '测试'))
        c.execute("INSERT OR IGNORE INTO user_profiles (user_id) VALUES (?)",
                  (USER_ID,))

        c.execute(
            "INSERT INTO records (user_id, value, unit, type, notes, timestamp, is_predicted) "
            "VALUES (?, 6.0, 'mmol/L', '空腹', '', ?, 0)",
            (USER_ID, f"{self.today_str} 07:15:00"))
        c.execute(
            "INSERT INTO records (user_id, value, unit, type, notes, timestamp, is_predicted) "
            "VALUES (?, 7.0, 'mmol/L', '早餐后2小时', '已有预测', ?, 1)",
            (USER_ID, f"{self.today_str} 10:30:00"))
        conn.commit()
        return conn

    def test_existing_prediction_skipped(self, isolate_db):
        """L450: call_ai 返回 '早餐后2小时' 但 DB 已有该预测 → continue 跳过"""
        from core.config import DB_NAME
        conn = self._setup_measured_only(DB_NAME)
        conn.close()

        conn2 = sqlite3.connect(DB_NAME)
        conn2.row_factory = sqlite3.Row

        ai_response = json.dumps([
            {"type": "早餐后2小时", "value": 8.0, "reasoning": "test"},
        ])
        with patch(self.PATCH_AVAIL, True), \
             patch(self.PATCH_AI, return_value=ai_response):
            results = predict_remaining_glucose_slots(conn2, user_id=USER_ID)

        assert results == [], "已有预测的槽位应被 continue 跳过"

        c2 = conn2.cursor()
        c2.execute(
            "SELECT value, notes FROM records "
            "WHERE user_id = ? AND DATE(timestamp) = ? AND type = '早餐后2小时' AND is_predicted = 1",
            (USER_ID, self.today_str))
        row = c2.fetchone()
        assert row is not None
        assert abs(row['value'] - 7.0) < 0.01, "不应被覆盖"
        assert row['notes'] == '已有预测', "notes 不应被修改"
        conn2.close()
