"""routes/api_records.py (86%) + routes/api_prediction.py (85%) 剩余未覆盖行测试

覆盖:
  api_prediction.py: 54,90,144-146,194-196,264,271-276,292,312,316-317,
                     341-344,357-362,402-404,423-425
  api_records.py: 184-185,191-192,200-201,205,208-210,284-285,293,296,
                   310-312,322-332,355-356,359-360,363-365,387,432,441-442,
                   460-461,479,481-483,497-498,510-511,538-539,552-553,572-573,589,598-599
"""
import io
from unittest.mock import MagicMock, patch

# ============================================================
# api_prediction.py — 特有测试
# ============================================================

class TestPredictionTriggerPostExerciseSkipped:
    """trigger_prediction: post_exercise 返回 None → skipped (line 54)"""

    def test_post_exercise_not_available(self, client_authenticated):
        with patch('routes.api_prediction.get_db') as mock_get_db, \
             patch('routes.api_prediction.predict_morning_fpg'), \
             patch('routes.api_prediction.predict_post_exercise_glucose', return_value=None), \
             patch('routes.api_prediction.predict_remaining_glucose_slots', return_value=[]):
            mock_db = MagicMock()
            mock_get_db.return_value = mock_db

            result = client_authenticated.post('/trigger_prediction', json={
                'type': 'all'
            })
            assert result.status_code == 200
            data = result.json['data']['results']
            post_ex = [r for r in data if r['type'] == '运动后']
            assert len(post_ex) == 1
            assert post_ex[0]['status'] == 'skipped'

    def test_trigger_generic_exception(self, client_authenticated):
        """返回 generic 500 error (line 90)"""
        with patch('routes.api_prediction.get_db') as mock_get_db:
            mock_get_db.side_effect = Exception("db error")
            result = client_authenticated.post('/trigger_prediction', json={
                'type': '空腹'
            })
            assert result.status_code == 500
            assert result.json['error_type'] == 'prediction_error'


class TestPredictionComparisonException:
    """prediction_comparison exception handler (lines 144-146)"""

    def test_comparison_exception(self, client_authenticated):
        with patch('routes.api_prediction.get_db', side_effect=Exception("db error")):
            result = client_authenticated.get('/prediction_comparison?days=7')
            assert result.status_code == 500

    def test_accuracy_exception(self, client_authenticated):
        """prediction_accuracy exception handler (lines 194-196)"""
        with patch('routes.api_prediction.get_db', side_effect=Exception("db error")):
            result = client_authenticated.get('/prediction_accuracy?days=30')
            assert result.status_code == 500


class TestPredictionStatusSlotMatching:
    """prediction_status: 复杂的槽位匹配分支（264,271-276,292,312,316-317）"""

    @staticmethod
    def _make_rec(id_, type_, value, timestamp, is_predicted=0, notes=None,
                  verified_by_real_id=None, prediction_error=None):
        r = MagicMock()
        data = (id_, type_, value, timestamp, is_predicted, notes, verified_by_real_id, prediction_error)
        r.__getitem__ = lambda s, k: data[k] if isinstance(k, int) else {
            'id': data[0], 'type': data[1], 'value': data[2], 'timestamp': data[3],
            'is_predicted': data[4], 'notes': data[5], 'verified_by_real_id': data[6],
            'prediction_error': data[7],
        }.get(k)
        r.__iter__ = lambda s: iter(data)
        return r

    def test_post_breakfast_excludes_lunch(self, client_authenticated):
        """Line 264: post_breakfast slot排除'午餐后'类型"""
        with patch('routes.api_prediction.get_db') as mock_get_db, \
             patch('routes.api_prediction.settings') as mock_settings:
            mock_settings.get_glucose_target.return_value = {'min': 4.0, 'max': 7.0, 'optimal_max': 6.0}
            mock_c = MagicMock()
            records = [
                self._make_rec(1, '午餐后', 8.0, '2024-06-01 12:00:00'),
            ]
            mock_c.fetchall.side_effect = [records, []]
            mock_c.fetchone.return_value = None
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db

            result = client_authenticated.get('/prediction_status')
            assert result.status_code == 200
            # post_breakfast should NOT match '午餐后'
            pb = [s for s in result.json['slots'] if s['key'] == 'post_breakfast'][0]
            assert pb['status'] == 'pending'

    def test_pre_dinner_excludes_post_exercise(self, client_authenticated):
        """Lines 271-276: pre_dinner排除'运动后'类型"""
        with patch('routes.api_prediction.get_db') as mock_get_db, \
             patch('routes.api_prediction.settings') as mock_settings:
            mock_settings.get_glucose_target.return_value = {'min': 4.0, 'max': 7.0, 'optimal_max': 6.0}
            mock_c = MagicMock()
            records = [
                self._make_rec(1, '运动后', 5.0, '2024-06-01 17:30:00'),
            ]
            mock_c.fetchall.side_effect = [records, []]
            mock_c.fetchone.return_value = None
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db

            result = client_authenticated.get('/prediction_status')
            assert result.status_code == 200
            pd_slot = [s for s in result.json['slots'] if s['key'] == 'pre_dinner'][0]
            assert pd_slot['status'] == 'pending'

    def test_cgm_with_predicted_unverified(self, client_authenticated):
        """Lines 341-344: CGM匹配 + 有predicted_record但未验证→verified+calc error"""
        with patch('routes.api_prediction.get_db') as mock_get_db, \
             patch('routes.api_prediction.settings') as mock_settings:
            mock_settings.get_glucose_target.return_value = {'min': 4.0, 'max': 7.0, 'optimal_max': 6.0}
            mock_c = MagicMock()
            records = [
                self._make_rec(1, 'CGM', 5.8, '2024-06-01 07:20:00'),
                self._make_rec(2, '空腹', 6.2, '2024-06-01 07:10:00', is_predicted=1,
                               notes='AI预测', verified_by_real_id=None),
            ]
            mock_c.fetchall.side_effect = [records, []]
            mock_c.fetchone.return_value = None
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db

            result = client_authenticated.get('/prediction_status')
            assert result.status_code == 200
            fasting = [s for s in result.json['slots'] if s['key'] == 'fasting'][0]
            assert fasting['status'] == 'verified'
            assert fasting['cgm'] is True
            # error = cgm_value (5.8) - predicted_value (6.2) = -0.4
            assert fasting['error'] == -0.4

    def test_predicted_with_verified_real(self, client_authenticated):
        """Lines 357-362: predicted_record且verified_by_real_id有值→查real_row"""
        with patch('routes.api_prediction.get_db') as mock_get_db, \
             patch('routes.api_prediction.settings') as mock_settings:
            mock_settings.get_glucose_target.return_value = {'min': 4.0, 'max': 7.0, 'optimal_max': 6.0}
            mock_c = MagicMock()
            records = [
                self._make_rec(1, '空腹', 6.2, '2024-06-01 07:15:00', is_predicted=1,
                               notes='AI预测', verified_by_real_id=5, prediction_error=0.3),
            ]
            mock_c.fetchall.side_effect = [records, []]
            # fetchone for the verified real_value query
            real_row = MagicMock()
            real_row = MagicMock()
            real_row.__getitem__ = lambda s, k: 6.5  # real value
            mock_c.fetchone.return_value = real_row
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db

            result = client_authenticated.get('/prediction_status')
            assert result.status_code == 200
            fasting = [s for s in result.json['slots'] if s['key'] == 'fasting'][0]
            assert fasting['status'] == 'verified'
            assert fasting['real_value'] == 6.5

    def test_prediction_status_exception(self, client_authenticated):
        """prediction_status exception handler (lines 402-404)"""
        with patch('routes.api_prediction.get_db', side_effect=Exception("db error")):
            result = client_authenticated.get('/prediction_status')
            assert result.status_code == 500

    def test_backfill_exception(self, client_authenticated):
        """backfill_predictions exception handler (lines 423-425)"""
        with patch('routes.api_prediction.get_db', side_effect=Exception("db error")):
            result = client_authenticated.post('/backfill_predictions', json={'days': 7})
            assert result.status_code == 500


# ============================================================
# api_records.py — 特有测试
# ============================================================

class TestRecordsAddFormDataBmiTimestamp:
    """add_record form-data path: weight+bmi, T timestamp (lines 184-210)"""

    def test_add_form_with_weight_bmi(self, client_authenticated):
        """weight + no bmi → calculate_bmi (lines 184-192)"""
        with patch('routes.api_records.get_db') as mock_get_db, \
             patch('routes.api_records.link_prediction_to_real_record'), \
             patch('routes.api_records._validate_record_data', return_value=[]), \
             patch('routes.api_records.settings.calculate_bmi', return_value=23.5):
            mock_c = MagicMock()
            mock_c.lastrowid = 500
            mock_c.fetchone.return_value = None
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db

            result = client_authenticated.post('/add', data={
                'value': '0', 'type': '体重记录', 'weight': '70.0', 'timestamp': '2024-06-01 07:00:00'
            })
            assert result.status_code in (200, 302)

    def test_add_form_empty_timestamp(self, client_authenticated):
        """空 timestamp → 默认当前时间 (lines 205-210)"""
        with patch('routes.api_records.get_db') as mock_get_db, \
             patch('routes.api_records.link_prediction_to_real_record'), \
             patch('routes.api_records._validate_record_data', return_value=[]):
            mock_c = MagicMock()
            mock_c.lastrowid = 501
            mock_c.fetchone.return_value = None
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db

            result = client_authenticated.post('/add', data={
                'value': '6.5', 'type': '空腹'
            })
            assert result.status_code in (200, 302)

    def test_add_json_t_timestamp(self, client_authenticated):
        """ISO格式T timestamp → 替换为空格 (line 208-210)"""
        with patch('routes.api_records.get_db') as mock_get_db, \
             patch('routes.api_records.link_prediction_to_real_record'):
            mock_c = MagicMock()
            mock_c.lastrowid = 502
            mock_c.fetchone.return_value = None
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db

            result = client_authenticated.post('/add', json={
                'type': '空腹', 'value': 6.5,
                'timestamp': '2024-06-01T07:15',  # missing seconds
                'user_id': 1
            })
            assert result.status_code == 200

    def test_add_form_data_value_profile_update(self, client_authenticated):
        """weight记录更新user profile (line 200-201)"""
        with patch('routes.api_records.get_db') as mock_get_db, \
             patch('routes.api_records.link_prediction_to_real_record'), \
             patch('routes.api_records.user_manager.update_user_profile_partial') as mock_update:
            mock_c = MagicMock()
            mock_c.lastrowid = 503
            mock_c.fetchone.return_value = None
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db

            result = client_authenticated.post('/add', json={
                'type': '体重记录', 'weight': 70.0,
                'timestamp': '2024-06-01 07:00:00', 'user_id': 1
            })
            assert result.status_code == 200
            mock_update.assert_called()

    def test_add_form_numeric_user_id(self, client_authenticated):
        """form-data 的 user_id 转换 (lines 191-192)"""
        with patch('routes.api_records.get_db') as mock_get_db, \
             patch('routes.api_records.link_prediction_to_real_record'), \
             patch('routes.api_records._validate_record_data', return_value=[]):
            mock_c = MagicMock()
            mock_c.lastrowid = 504
            mock_c.fetchone.return_value = None
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db

            result = client_authenticated.post('/add', data={
                'value': '6.5', 'type': '空腹', 'user_id': '1',
                'timestamp': '2024-06-01 07:15:00'
            })
            assert result.status_code in (200, 302)


class TestRecordsBatchAddConflictTypes:
    """batch_add 各种冲突类型 (lines 284-332)"""

    def test_batch_add_bp_conflict(self, client_authenticated):
        with patch('routes.api_records.get_db') as mock_get_db:
            mock_c = MagicMock()
            dup_bp = {'id': 10, 'systolic_pressure': 120, 'diastolic_pressure': 80, 'timestamp': '2024-06-01 07:13:00'}
            mock_c.fetchone.return_value = dup_bp
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db

            result = client_authenticated.post('/batch_add', json={
                'records': [{'value': 0, 'type': '血压', 'systolic_pressure': 120,
                             'diastolic_pressure': 80, 'datetime': '2024-06-01 07:15:00'}],
                'conflict_resolution': 'ask'
            })
            assert result.status_code == 200
            assert result.json['status'] == 'conflict'

    def test_batch_add_overwrite_resolution(self, client_authenticated):
        with patch('routes.api_records.get_db') as mock_get_db:
            mock_c = MagicMock()
            mock_c.lastrowid = 600
            mock_c.fetchone.return_value = None
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db

            result = client_authenticated.post('/batch_add', json={
                'records': [{'value': 6.5, 'type': '空腹', 'datetime': '2024-06-01 07:15:00'}],
                'conflict_resolution': 'overwrite'
            })
            assert result.status_code == 200
            assert result.json['data']['inserted'] == 1

    def test_batch_add_skip_resolution(self, client_authenticated):
        with patch('routes.api_records.get_db') as mock_get_db:
            mock_c = MagicMock()
            mock_c.lastrowid = 601
            mock_c.fetchone.return_value = None
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db

            result = client_authenticated.post('/batch_add', json={
                'records': [{'value': 6.5, 'type': '空腹', 'datetime': '2024-06-01 07:15:00'}],
                'conflict_resolution': 'skip'
            })
            assert result.status_code == 200

    def test_batch_add_weight_conflict(self, client_authenticated):
        """batch_add 体重冲突类型 (line 293)"""
        with patch('routes.api_records.get_db') as mock_get_db:
            mock_c = MagicMock()
            dup_wt = {'id': 11, 'weight': 70.0, 'timestamp': '2024-06-01 06:58:00'}
            mock_c.fetchone.return_value = dup_wt
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db

            result = client_authenticated.post('/batch_add', json={
                'records': [{'value': 0, 'type': '体重记录', 'weight': 70.0,
                             'datetime': '2024-06-01 07:00:00'}],
                'conflict_resolution': 'ask'
            })
            assert result.status_code == 200
            assert result.json['status'] == 'conflict'

    def test_batch_add_with_warnings(self, client_authenticated):
        """batch_add 数据校验警告 (lines 322-332)"""
        with patch('routes.api_records.get_db') as mock_get_db:
            mock_c = MagicMock()
            mock_c.lastrowid = 602
            mock_c.fetchone.return_value = None
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db

            result = client_authenticated.post('/batch_add', json={
                'records': [{'value': 50.0, 'type': '空腹', 'datetime': '2024-06-01 07:15:00'}],
                'conflict_resolution': 'overwrite'
            })
            assert result.status_code == 200
            assert 'warnings' in result.json['data']


class TestRecordsExceptions:
    """各路由的 exception handler"""

    def test_delete_exception(self, client_authenticated):
        with patch('routes.api_records.get_db', side_effect=Exception("db error")):
            result = client_authenticated.post('/delete/1',
                                               headers={'X-Requested-With': 'XMLHttpRequest'})
            assert result.status_code == 500

    def test_get_record_exception(self, client_authenticated):
        with patch('routes.api_records.get_db', side_effect=Exception("db error")):
            result = client_authenticated.get('/record/1')
            assert result.status_code == 500

    def test_update_record_exception(self, client_authenticated):
        with patch('routes.api_records.get_db', side_effect=Exception("db error")):
            result = client_authenticated.post('/update/1', json={'value': 7.0})
            assert result.status_code == 500

    def test_export_exception(self, client_authenticated):
        with patch('routes.api_records.get_db', side_effect=Exception("db error")):
            result = client_authenticated.get('/export')
            assert result.status_code == 500

    def test_import_exception(self, client_authenticated):
        with patch('routes.api_records.get_db', side_effect=Exception("db error")), \
             patch('routes.api_records.pd') as mock_pd:
            mock_pd.read_csv.return_value = []
            csv_data = 'value,type\n6.5,空腹'.encode()
            data = {'file': (io.BytesIO(csv_data), 'test.csv')}
            result = client_authenticated.post('/import', data=data,
                                               content_type='multipart/form-data')
            assert result.status_code == 500

    def test_preview_import_exception(self, client_authenticated):
        with patch('routes.api_records.pd.read_csv', side_effect=Exception("parse error")):
            csv_data = 'value,type\n6.5,空腹'.encode()
            data = {'file': (io.BytesIO(csv_data), 'test.csv')}
            result = client_authenticated.post('/preview_import', data=data,
                                               content_type='multipart/form-data')
            assert result.status_code == 500

    def test_parse_ai_exception(self, client_authenticated):
        with patch('routes.api_records.get_db', side_effect=Exception("db error")):
            result = client_authenticated.post('/parse_ai', json={'text': 'hello'})
            assert result.status_code == 500

    def test_batch_add_no_data(self, client_authenticated):
        """batch_add 空数据"""
        result = client_authenticated.post('/batch_add', json={'records': []})
        assert result.status_code == 400

    def test_batch_add_exception(self, client_authenticated):
        with patch('routes.api_records.get_db', side_effect=Exception("db error")):
            result = client_authenticated.post('/batch_add', json={
                'records': [{'value': 6.5, 'type': '空腹'}]
            })
            assert result.status_code == 500


class TestRecordsAddExceptionJson:
    """add_record 的 JSON except 分支 (line 387)"""

    def test_add_json_record_error(self, client_authenticated):
        with patch('routes.api_records.get_db') as mock_get_db:
            mock_c = MagicMock()
            mock_c.lastrowid = 700
            mock_c.fetchone.side_effect = Exception("insert error")
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db

            result = client_authenticated.post('/add', json={
                'type': '空腹', 'value': 6.5, 'timestamp': '2024-06-01 07:15:00', 'user_id': 1
            })
            assert result.status_code == 500
            assert result.json['error_type'] == 'add_record_error'

    def test_add_form_exception(self, client_authenticated):
        """add_record form-data except → string 500 (line 387)"""
        with patch('routes.api_records.get_db') as mock_get_db:
            mock_c = MagicMock()
            mock_c.execute.side_effect = Exception("insert error")
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db

            result = client_authenticated.post('/add', data={
                'value': '6.5', 'type': '空腹', 'timestamp': '2024-06-01 07:15:00'
            })
            assert result.status_code == 500
"""api_prediction.py prediction_status 深层业务逻辑测试

覆盖目标:
  - L264:   post_breakfast slot 含 '午餐后'/'晚餐后' -> unmatched
  - L271:   pre_dinner slot 含 '运动后' -> unmatched
  - L273-276: pre_dinner '餐前' 时间不在 16-19 -> unmatched
  - L292:   post_dinner fallback '餐后2小时' in 19-23 -> matched
  - L312:   CGM 记录无时间部分 -> continue
  - L316-317: CGM 时间解析异常 -> continue
  - L341-344: CGM + predicted 有 verified_by_real_id -> verified
"""



class TestPredictionStatusDeep:
    """prediction_status 深层 slot 匹配全覆盖"""

    def _make_record(self, **overrides):
        """工厂方法：创建模拟数据库记录（sqlite3.Row 风格 dict）"""
        rec = {
            'id': 1,
            'type': '空腹',
            'value': 6.5,
            'timestamp': '2024-06-01 07:15:00',
            'is_predicted': 0,
            'notes': None,
            'verified_by_real_id': None,
            'prediction_error': None,
        }
        rec.update(overrides)
        return rec

    def _fetch_json(self, client_authenticated, today_records, accuracy_rows=None):
        """Mock get_db 并调用 prediction_status，返回 JSON"""
        if accuracy_rows is None:
            accuracy_rows = []
        mock_c = MagicMock()
        mock_c.fetchall.side_effect = [today_records, accuracy_rows]
        mock_db = MagicMock()
        mock_db.cursor.return_value = mock_c
        with patch('routes.api_prediction.get_db', return_value=mock_db):
            resp = client_authenticated.get('/prediction_status')
        assert resp.status_code == 200, f"预期 200, 得到 {resp.status_code}: {resp.data}"
        return resp.json

    # ---------- L264 ----------

    def test_post_breakfast_unmatched_by_lunch(self, client_authenticated):
        """L264: post_breakfast 匹配后因 type 含 '晚餐后' -> unmatched"""
        data = self._fetch_json(client_authenticated, [
            self._make_record(type='晚餐后 餐后2小时', value=7.0, timestamp='2024-06-01 11:30:00'),
        ])
        pb = next(s for s in data['slots'] if s['key'] == 'post_breakfast')
        # '晚餐后 餐后2小时' 先匹配 '餐后2小时' 模式，再被 '晚餐后' 剔除
        assert pb['status'] == 'pending', f"应未匹配, 得到 {pb}"

    # ---------- L271-276 ----------

    def test_pre_dinner_unmatched_by_exercise(self, client_authenticated):
        """L271: pre_dinner 匹配后因 type 含 '运动后' -> unmatched"""
        data = self._fetch_json(client_authenticated, [
            self._make_record(type='运动后 晚饭前', value=6.0, timestamp='2024-06-01 17:30:00'),
        ])
        pd_slot = next(s for s in data['slots'] if s['key'] == 'pre_dinner')
        # '晚饭前' 匹配 pre_dinner patterns -> matched=True
        # 但 '运动后' in type -> L271: matched = False
        assert pd_slot['status'] == 'pending'

    def test_pre_dinner_can_qian_outside_range(self, client_authenticated):
        """L273-276: pre_dinner '餐前' 时间 15:00（不在 16-19）-> unmatched"""
        data = self._fetch_json(client_authenticated, [
            self._make_record(type='餐前', value=5.5, timestamp='2024-06-01 15:00:00'),
        ])
        pd_slot = next(s for s in data['slots'] if s['key'] == 'pre_dinner')
        # '餐前' 匹配 pre_dinner patterns -> matched = True
        # 但 15:00 (hour=15) < 16 -> hour < 16 -> matched = False
        assert pd_slot['status'] == 'pending'

    # ---------- L292 ----------

    def test_post_dinner_fallback_matched(self, client_authenticated):
        """L292: post_dinner fallback '餐后2小时' at 20:00 -> matched"""
        data = self._fetch_json(client_authenticated, [
            self._make_record(type='餐后2小时', value=7.5, timestamp='2024-06-01 20:00:00'),
        ])
        pd_slot = next(s for s in data['slots'] if s['key'] == 'post_dinner')
        assert pd_slot['status'] == 'measured', f"应匹配为 measured, 得到 {pd_slot}"

    # ---------- L312 ----------

    def test_cgm_no_timestamp_time(self, client_authenticated):
        """L312: CGM 记录 timestamp 无时间部分 -> continue"""
        data = self._fetch_json(client_authenticated, [
            self._make_record(type='CGM', value=5.8, timestamp='2024-06-01'),
        ])
        # CGM 时间解析失败 -> continue，所有槽位为 pending
        assert all(s['status'] == 'pending' for s in data['slots']), \
            f"存在已匹配的槽位: {[s for s in data['slots'] if s['status'] != 'pending']}"

    # ---------- L316-317 ----------

    def test_cgm_time_parse_error(self, client_authenticated):
        """L316-317: CGM 时间 'bad:time' 解析异常 -> continue"""
        data = self._fetch_json(client_authenticated, [
            self._make_record(type='CGM', value=5.8, timestamp='2024-06-01 bad:time'),
        ])
        assert all(s['status'] == 'pending' for s in data['slots']), \
            f"存在已匹配的槽位: {[s for s in data['slots'] if s['status'] != 'pending']}"

    # ---------- L341-344 ----------

    def test_cgm_verified_with_prediction(self, client_authenticated):
        """L341-344: CGM 匹配空腹槽 + predicted 有 verified_by_real_id -> verified"""
        data = self._fetch_json(client_authenticated, [
            self._make_record(id=2, type='CGM', value=6.0, timestamp='2024-06-01 07:15:00'),
            self._make_record(id=3, type='空腹', value=5.5, timestamp='2024-06-01 07:15:00',
                              is_predicted=1, verified_by_real_id=2, prediction_error=0.5),
        ])
        fasting = next(s for s in data['slots'] if s['key'] == 'fasting')
        assert fasting['status'] == 'verified', f"应为 verified, 得到 {fasting}"
        assert fasting.get('cgm') is True, "应标记为 CGM 来源"


class TestAddRecordUnauthenticated:
    """add_record/batch_add 未登录路径（lines 183-185）"""

    def test_add_no_session_json(self, client):
        """JSON 请求未登录 -> 401"""
        resp = client.post('/add',
            json={'type': '空腹', 'value': 6.0})
        assert resp.status_code == 401

    def test_add_no_session_form(self, client):
        """表单请求未登录 -> redirect"""
        resp = client.post('/add',
            data={'type': '空腹', 'value': 6.0})
        assert resp.status_code == 302

    def test_batch_add_no_session(self, client):
        """batch_add 未登录 -> 401"""
        resp = client.post('/batch_add',
            json={'records': [{'type': '空腹', 'value': 6.0}]})
        assert resp.status_code == 401

    def test_add_handler_session_check_json(self, client_authenticated):
        """已登录但 user_id None → handler JSON 返回 401（line 183）"""
        with patch('routes.api_records.user_manager.get_current_user_id',
                   return_value=None):
            resp = client_authenticated.post('/add',
                json={'type': '空腹', 'value': 6.0})
        assert resp.status_code == 401

    def test_add_handler_session_check_form(self, client_authenticated):
        """已登录但 user_id None → handler 表单返回 redirect（line 185）"""
        with patch('routes.api_records.user_manager.get_current_user_id',
                   return_value=None):
            resp = client_authenticated.post('/add',
                data={'type': '空腹', 'value': 6.0})
        assert resp.status_code == 302

    def test_batch_add_handler_session_check(self, client_authenticated):
        """已登录但 user_id None → batch_add 返回 401（line 381）"""
        with patch('routes.api_records.user_manager.get_current_user_id',
                   return_value=None):
            resp = client_authenticated.post('/batch_add',
                json={'records': [{'type': '空腹', 'value': 6.0}]})
        assert resp.status_code == 401
        assert resp.status_code == 401
