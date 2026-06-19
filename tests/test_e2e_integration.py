"""端到端集成测试：使用真实 SQLite 临时数据库走通完整流程

与现有 mock 测试不同，本文件中的测试不 patch get_db()，
而是通过 client_authenticated 使用真实的临时 SQLite 数据库。
数据通过 POST/GET 端点写入和读取，验证全链路正确性。

隔离方案：使用 conftest.py 中的 isolate_db fixture（通过 pytestmark 全局引入），
每个测试前将 core.config.DB_NAME 指向独立临时 SQLite 文件。
"""
import io
import datetime
import pytest
from unittest.mock import patch


# 所有测试自动使用独立临时数据库
pytestmark = pytest.mark.usefixtures("isolate_db")


# ============================================================
# 记录 CRUD 生命周期
# ============================================================

class TestRealDBRecordLifecycle:

    def test_add_and_read_record_json(self, client_authenticated, app):
        """JSON POST /add → 写入真实 DB → GET /record/<id> 读取"""
        with app.app_context():
            result = client_authenticated.post('/add', json={
                'value': 6.5, 'type': '空腹', 'unit': 'mmol/L',
                'notes': '空腹测试', 'timestamp': '2024-06-01 07:15:00',
                'user_id': 1
            })
            assert result.status_code == 200
            record_id = result.json['data']['id']

            from utils.db import get_db
            db = get_db()
            c = db.cursor()
            c.execute("SELECT value, type, notes FROM records WHERE id = ?", (record_id,))
            row = c.fetchone()
            assert row is not None
            assert row['value'] == 6.5
            assert row['type'] == '空腹'

    def test_add_with_bmi_calculation(self, client_authenticated, app):
        """体重记录 → BMI 自动计算"""
        with app.app_context():
            result = client_authenticated.post('/add', json={
                'type': '体重记录', 'weight': 70.0, 'timestamp': '2024-06-01 07:00:00',
                'user_id': 1
            })
            assert result.status_code == 200

            from utils.db import get_db
            db = get_db()
            c = db.cursor()
            c.execute("SELECT weight, bmi FROM records ORDER BY id DESC LIMIT 1")
            row = c.fetchone()
            assert row['weight'] == 70.0
            # BMI = 70/(1.75^2) ≈ 22.9
            assert row['bmi'] is not None
            assert round(row['bmi'], 1) == 22.9

    def test_add_and_duplicate_detection(self, client_authenticated, app):
        """同一天同类型 → 409"""
        with app.app_context():
            r1 = client_authenticated.post('/add', json={
                'value': 6.5, 'type': '空腹', 'timestamp': '2024-06-01 07:15:00', 'user_id': 1
            })
            assert r1.status_code == 200

            r2 = client_authenticated.post('/add', json={
                'value': 6.5, 'type': '空腹', 'timestamp': '2024-06-01 07:15:00', 'user_id': 1
            })
            assert r2.status_code == 409

            # 不同天 → 成功
            r3 = client_authenticated.post('/add', json={
                'value': 6.5, 'type': '空腹', 'timestamp': '2024-06-02 07:15:00', 'user_id': 1
            })
            assert r3.status_code == 200

    def test_add_multiple_types(self, client_authenticated, app):
        """多种类型记录 → 全部存在"""
        with app.app_context():
            records = [
                {'value': 7.2, 'type': '早餐后2小时', 'timestamp': '2024-06-01 09:00:00', 'user_id': 1},
                {'value': 0, 'type': '血压', 'systolic_pressure': 130, 'diastolic_pressure': 85,
                 'pulse_rate': 72, 'timestamp': '2024-06-01 08:00:00', 'user_id': 1},
                {'type': '体重记录', 'weight': 71.0, 'timestamp': '2024-06-01 07:00:00', 'user_id': 1},
                {'value': 0, 'type': '跑步', 'distance': 5.0, 'duration': '30:00',
                 'heart_rate': 145, 'calories': 300, 'timestamp': '2024-06-01 17:00:00', 'user_id': 1},
            ]
            for rec in records:
                r = client_authenticated.post('/add', json=rec)
                assert r.status_code == 200, f"Failed to add {rec['type']}: {r.data}"

            from utils.db import get_db
            db = get_db()
            c = db.cursor()
            c.execute("SELECT COUNT(*) FROM records WHERE user_id = 1")
            assert c.fetchone()[0] == 4

    def test_update_record(self, client_authenticated, app):
        """update → 修改 DB 记录"""
        with app.app_context():
            r = client_authenticated.post('/add', json={
                'value': 6.5, 'type': '空腹', 'timestamp': '2024-06-01 07:15:00', 'user_id': 1
            })
            assert r.status_code == 200
            rid = r.json['data']['id']

            ur = client_authenticated.post(f'/update/{rid}', json={
                'value': 5.8, 'type': '空腹', 'notes': '修正后'
            })
            assert ur.status_code == 200

            from utils.db import get_db
            db = get_db()
            c = db.cursor()
            c.execute("SELECT value, notes FROM records WHERE id = ?", (rid,))
            row = c.fetchone()
            assert row['value'] == 5.8
            assert row['notes'] == '修正后'

    def test_delete_record(self, client_authenticated, app):
        """delete → 从 DB 删除"""
        with app.app_context():
            r = client_authenticated.post('/add', json={
                'value': 6.5, 'type': '空腹', 'timestamp': '2024-06-01 07:15:00', 'user_id': 1
            })
            assert r.status_code == 200
            rid = r.json['data']['id']

            dr = client_authenticated.post(f'/delete/{rid}')
            assert dr.status_code == 302

            from utils.db import get_db
            db = get_db()
            c = db.cursor()
            c.execute("SELECT id FROM records WHERE id = ?", (rid,))
            assert c.fetchone() is None


# ============================================================
# Dashboard 数据流
# ============================================================

class TestRealDBDashboardFlow:

    def test_day_overview_shows_measured(self, client_authenticated, app):
        """多条血糖 → day_overview 显示实测值"""
        with app.app_context():
            client_authenticated.post('/add', json={
                'value': 6.5, 'type': '空腹', 'timestamp': '2024-06-01 07:15:00', 'user_id': 1
            })
            client_authenticated.post('/add', json={
                'value': 8.2, 'type': '早餐后2小时', 'timestamp': '2024-06-01 11:00:00', 'user_id': 1
            })
            client_authenticated.post('/add', json={
                'value': 6.0, 'type': '睡前', 'timestamp': '2024-06-01 22:00:00', 'user_id': 1
            })

            result = client_authenticated.get('/api/day_overview?date=2024-06-01')
            assert result.status_code == 200
            overview = {s['key']: s for s in result.json['overview']}
            assert overview['fasting']['value'] == 6.5
            assert overview['fasting']['status'] == 'measured'
            assert overview['post_breakfast']['value'] == 8.2
            assert overview['post_breakfast']['status'] == 'measured'
            assert overview['bedtime']['value'] == 6.0
            assert overview['bedtime']['status'] == 'measured'
            assert overview['post_lunch']['status'] == 'pending'

    def test_day_overview_shows_exercises(self, client_authenticated, app):
        """运动记录 → day_overview 显示"""
        with app.app_context():
            client_authenticated.post('/add', json={
                'value': 0, 'type': '跑步', 'distance': 5.0, 'duration': '30:00',
                'heart_rate': 145, 'calories': 300,
                'timestamp': '2024-06-01 17:00:00', 'user_id': 1
            })
            result = client_authenticated.get('/api/day_overview?date=2024-06-01')
            assert result.status_code == 200
            assert len(result.json['exercises']) == 1
            ex = result.json['exercises'][0]
            assert ex['type'] == '跑步'
            assert ex['distance'] == 5.0
            assert ex['calories'] == 300

    def test_day_overview_shows_bp_and_weight(self, client_authenticated, app):
        """血压 + 体重 → day_overview 显示"""
        with app.app_context():
            client_authenticated.post('/add', json={
                'value': 0, 'type': '血压测量', 'systolic_pressure': 120,
                'diastolic_pressure': 80, 'pulse_rate': 72, 'spo2': 98,
                'timestamp': '2024-06-01 08:00:00', 'user_id': 1
            })
            client_authenticated.post('/add', json={
                'type': '体重记录', 'weight': 70.0,
                'timestamp': '2024-06-01 07:00:00', 'user_id': 1
            })
            result = client_authenticated.get('/api/day_overview?date=2024-06-01')
            assert result.status_code == 200
            assert len(result.json['bps']) == 1
            assert result.json['bps'][0]['systolic'] == 120
            assert len(result.json['weights']) == 1
            assert result.json['weights'][0]['weight'] == 70.0

    def test_day_overview_compliance_calculated(self, client_authenticated, app):
        """实测血糖 → 日达标率"""
        with app.app_context():
            client_authenticated.post('/add', json={
                'value': 5.5, 'type': '空腹', 'timestamp': '2024-06-01 07:15:00', 'user_id': 1
            })
            client_authenticated.post('/add', json={
                'value': 13.5, 'type': '早餐后2小时', 'timestamp': '2024-06-01 11:00:00', 'user_id': 1
            })
            result = client_authenticated.get('/api/day_overview?date=2024-06-01')
            assert result.status_code == 200
            # 2 measured, 1 compliant → 50%
            assert result.json['compliance'] == 50

    def test_health_stats_with_real_data(self, client_authenticated, app):
        """多种数据 → health_stats 统计"""
        with app.app_context():
            client_authenticated.post('/add', json={
                'value': 6.5, 'type': '空腹', 'timestamp': '2024-06-01 07:15:00', 'user_id': 1
            })
            client_authenticated.post('/add', json={
                'value': 11.0, 'type': '晚餐后2小时', 'timestamp': '2024-06-01 20:00:00', 'user_id': 1
            })
            client_authenticated.post('/add', json={
                'value': 0, 'type': '跑步', 'distance': 5.0, 'calories': 300,
                'timestamp': '2024-06-01 17:00:00', 'user_id': 1
            })
            client_authenticated.post('/add', json={
                'value': 0, 'type': '血压测量', 'systolic_pressure': 130,
                'diastolic_pressure': 85, 'timestamp': '2024-06-01 08:00:00', 'user_id': 1
            })
            client_authenticated.post('/add', json={
                'type': '体重记录', 'weight': 70.0,
                'timestamp': '2024-06-01 07:00:00', 'user_id': 1
            })

            result = client_authenticated.get('/api/health_stats?days=all')
            assert result.status_code == 200
            data = result.json
            assert data['glucose']['avg_fasting'] > 0
            assert data['exercise']['total_distance'] == 5.0
            assert data['exercise']['total_calories'] == 300
            assert data['bp']['avg_sys'] > 0
            assert data['bp']['count'] >= 1
            assert data['weight']['latest'] == 70.0

    def test_health_stats_glucose_details(self, client_authenticated, app):
        """血糖 → max/min 详情"""
        with app.app_context():
            client_authenticated.post('/add', json={
                'value': 5.0, 'type': '空腹', 'timestamp': '2024-06-01 07:15:00', 'user_id': 1
            })
            client_authenticated.post('/add', json={
                'value': 12.0, 'type': '晚餐后2小时', 'timestamp': '2024-06-01 20:00:00', 'user_id': 1
            })
            result = client_authenticated.get('/api/health_stats?days=all')
            assert result.status_code == 200
            glucose = result.json['glucose']
            assert glucose['max'] == 12.0
            assert glucose['min'] == 5.0
            assert glucose['max_detail']['timestamp'] != ''
            assert glucose['min_detail']['timestamp'] != ''

    def test_timeline_with_real_data(self, client_authenticated, app):
        """数据 → /api/timeline"""
        with app.app_context():
            client_authenticated.post('/add', json={
                'value': 6.5, 'type': '空腹', 'timestamp': '2024-06-01 07:15:00', 'user_id': 1
            })
            client_authenticated.post('/add', json={
                'value': 8.0, 'type': '早餐后2小时', 'timestamp': '2024-06-01 11:00:00', 'user_id': 1
            })
            result = client_authenticated.get('/api/timeline?days=9999')
            assert result.status_code == 200
            assert isinstance(result.json, list)
            assert len(result.json) > 0


# ============================================================
# 批量操作
# ============================================================

class TestRealDBBatchFlow:

    def test_batch_add_multiple_records(self, client_authenticated, app):
        """batch_add 一次写入多条"""
        with app.app_context():
            result = client_authenticated.post('/batch_add', json={
                'records': [
                    {'value': 6.5, 'type': '空腹', 'datetime': '2024-06-01 07:15:00'},
                    {'value': 8.0, 'type': '早餐后2小时', 'datetime': '2024-06-01 11:00:00'},
                    {'value': 5.5, 'type': '睡前', 'datetime': '2024-06-01 22:00:00'},
                ],
                'conflict_resolution': 'overwrite'
            })
            assert result.status_code == 200
            assert result.json['data']['inserted'] == 3

            from utils.db import get_db
            db = get_db()
            c = db.cursor()
            c.execute("SELECT COUNT(*) FROM records WHERE user_id = 1")
            assert c.fetchone()[0] == 3

    def test_batch_add_conflict_detection(self, client_authenticated, app):
        """冲突检测 → conflict"""
        with app.app_context():
            client_authenticated.post('/add', json={
                'value': 6.5, 'type': '空腹', 'timestamp': '2024-06-01 07:15:00', 'user_id': 1
            })
            result = client_authenticated.post('/batch_add', json={
                'records': [
                    {'value': 6.3, 'type': '空腹', 'datetime': '2024-06-01 07:15:00'},
                ],
                'conflict_resolution': 'ask'
            })
            assert result.status_code == 200
            data = result.json
            assert data.get('status') == 'conflict'
            assert len(data['conflicts']) == 1

    def test_batch_add_with_blood_pressure(self, client_authenticated, app):
        """batch_add 血压 + 体重"""
        with app.app_context():
            result = client_authenticated.post('/batch_add', json={
                'records': [
                    {'value': 0, 'type': '血压测量', 'systolic_pressure': 120,
                     'diastolic_pressure': 80, 'datetime': '2024-06-01 08:00:00'},
                    {'type': '体重记录', 'weight': 70.5, 'datetime': '2024-06-01 07:00:00', 'value': 0},
                ],
                'conflict_resolution': 'overwrite'
            })
            assert result.status_code == 200
            assert result.json['data']['inserted'] == 2


# ============================================================
# 用药流程
# ============================================================

class TestRealDBMedicationFlow:

    def test_add_and_list_medication_plan(self, client_authenticated, app):
        """add_medication_plan → list → 验证"""
        with app.app_context():
            plan_data = {
                'medication_name': '二甲双胍', 'dosage': '500mg',
                'times_per_day': 3, 'timing_notes': '餐后',
                'start_date': '2024-01-01', 'frequency': 'daily', 'category': 'long_term',
            }
            r = client_authenticated.post('/add_medication_plan', json=plan_data)
            assert r.status_code == 200
            plan_id = r.json['data']['id']

            r2 = client_authenticated.get('/medication_plans')
            assert r2.status_code == 200
            plans = r2.json
            assert len(plans) >= 1
            plan = [p for p in plans if p['id'] == plan_id][0]
            assert plan['medication_name'] == '二甲双胍'
            assert plan['dosage'] == '500mg'

    def test_update_medication_plan_logs_history(self, client_authenticated, app):
        """更新剂量 → dosage_history 记录"""
        with app.app_context():
            r = client_authenticated.post('/add_medication_plan', json={
                'medication_name': '二甲双胍', 'dosage': '500mg',
                'start_date': '2024-01-01', 'times_per_day': 3,
            })
            pid = r.json['data']['id']

            r2 = client_authenticated.post(f'/update_medication_plan/{pid}', json={
                'medication_name': '二甲双胍', 'dosage': '850mg',
                'start_date': '2024-01-01', 'times_per_day': 3,
            })
            assert r2.status_code == 200

            from utils.db import get_db
            db = get_db()
            c = db.cursor()
            c.execute("SELECT old_dosage, new_dosage FROM dosage_history WHERE plan_id = ?", (pid,))
            row = c.fetchone()
            assert row is not None
            assert '500mg' in row['old_dosage']
            assert '850mg' in row['new_dosage']

    def test_day_overview_shows_medication_plans(self, client_authenticated, app):
        """用药方案 + 临时用药 → day_overview"""
        with app.app_context():
            client_authenticated.post('/add_medication_plan', json={
                'medication_name': '二甲双胍', 'dosage': '500mg',
                'times_per_day': 3, 'timing_notes': '餐后',
                'start_date': '2024-01-01', 'frequency': 'daily',
            })
            # 添加临时用药（通过 batch_add — /add 的 INSERT 不含 medication_name）
            client_authenticated.post('/batch_add', json={
                'records': [{
                    'value': 0, 'type': '临时用药', 'medication_name': '布洛芬',
                    'notes': '头疼', 'datetime': '2024-06-01 14:00:00'
                }],
                'conflict_resolution': 'overwrite'
            })
            result = client_authenticated.get('/api/day_overview?date=2024-06-01')
            assert result.status_code == 200
            med_status = result.json['med_status']
            assert len(med_status['plans']) >= 1
            assert any(p['name'] == '二甲双胍' for p in med_status['plans'])
            assert len(med_status['temp_medications']) >= 1


# ============================================================
# CSV 导出/导入
# ============================================================

class TestRealDBCSVFlow:

    def test_export_csv_contains_data(self, client_authenticated, app):
        """插入 → CSV 导出包含数据"""
        with app.app_context():
            client_authenticated.post('/add', json={
                'value': 6.5, 'type': '空腹', 'timestamp': '2024-06-01 07:15:00',
                'notes': 'e2e测试', 'user_id': 1
            })
            client_authenticated.post('/add', json={
                'value': 8.0, 'type': '早餐后2小时', 'timestamp': '2024-06-01 11:00:00',
                'notes': '早餐后', 'user_id': 1
            })
            result = client_authenticated.get('/export')
            assert result.status_code == 200
            assert result.mimetype == 'text/csv'
            csv_text = result.data.decode('utf-8-sig')
            assert '6.5' in csv_text
            assert '空腹' in csv_text
            assert '8.0' in csv_text

    def test_export_and_reimport_roundtrip(self, client_authenticated, app):
        """导出 → 导入 → 验证一致性"""
        with app.app_context():
            client_authenticated.post('/add', json={
                'value': 6.5, 'type': '空腹', 'timestamp': '2024-06-01 07:15:00',
                'notes': 'roundtrip', 'user_id': 1
            })
            client_authenticated.post('/add', json={
                'value': 7.2, 'type': '早餐后2小时', 'timestamp': '2024-06-01 11:00:00',
                'notes': '早餐后', 'user_id': 1
            })
            export_r = client_authenticated.get('/export')
            assert export_r.status_code == 200
            csv_content = export_r.data

            import_r = client_authenticated.post('/import', data={
                'file': (io.BytesIO(csv_content), 'roundtrip.csv'),
            })
            assert import_r.status_code == 200

            from utils.db import get_db
            db = get_db()
            c = db.cursor()
            c.execute("SELECT COUNT(*) FROM records WHERE user_id = 1")
            assert c.fetchone()[0] == 4

    def test_preview_import_with_real_db_context(self, client_authenticated):
        """CSV 预览"""
        csv_content = b'value,type,timestamp\n6.5,\xe7\xa9\xba\xe8\x85\xb9,2024-06-01 07:15:00\n'
        result = client_authenticated.post('/preview_import', data={
            'file': (io.BytesIO(csv_content), 'test.csv'),
        }, content_type='multipart/form-data')
        assert result.status_code == 200
        data = result.json['data']
        assert 'columns' in data
        assert 'rows' in data


# ============================================================
# AI 解析（Mock AI 层，真实 DB 层）
# ============================================================

class TestRealDBAIParseFlow:

    def test_parse_ai_and_dashboard(self, client_authenticated, app):
        """Mock AI → parse_ai + batch_add → day_overview 展示"""
        with app.app_context():
            import json as _json
            mock_result = _json.dumps([{
                'value': 6.5, 'type': '空腹', 'unit': 'mmol/L',
                'datetime': '2024-06-01 07:15:00', 'notes': '模拟AI解析',
                'is_predicted': False, 'predicted_value': None
            }])
            with patch('routes.api_records.parse_glucose_input', return_value=_json.loads(mock_result)):
                result = client_authenticated.post('/parse_ai', json={'text': '早上空腹6.5'})
                assert result.status_code == 200
                parsed = result.json
                assert len(parsed) >= 1
                assert parsed[0]['value'] == 6.5

                batch_r = client_authenticated.post('/batch_add', json={
                    'records': [{
                        'value': 6.5, 'type': '空腹', 'unit': 'mmol/L',
                        'datetime': '2024-06-01 07:15:00', 'notes': '模拟AI解析',
                        'is_predicted': False
                    }],
                    'conflict_resolution': 'overwrite'
                })
                assert batch_r.status_code == 200
                assert batch_r.json['data']['inserted'] == 1

                day_r = client_authenticated.get('/api/day_overview?date=2024-06-01')
                assert day_r.status_code == 200
                overview = {s['key']: s for s in day_r.json['overview']}
                assert overview['fasting']['value'] == 6.5
                assert overview['fasting']['status'] == 'measured'


# ============================================================
# 端到端用户旅程
# ============================================================

class TestRealDBUserJourney:

    def test_full_morning_routine(self, client_authenticated, app):
        """测空腹 → 跑步 → 早餐后 → Dashboard → CSV"""
        with app.app_context():
            r = client_authenticated.post('/add', json={
                'value': 6.5, 'type': '空腹', 'timestamp': '2024-06-01 07:15:00', 'user_id': 1
            })
            assert r.status_code == 200
            fasting_id = r.json['data']['id']

            client_authenticated.post('/add', json={
                'value': 0, 'type': '跑步', 'distance': 5.0, 'duration': '30:00',
                'heart_rate': 145, 'calories': 300,
                'timestamp': '2024-06-01 08:45:00', 'user_id': 1
            })
            client_authenticated.post('/add', json={
                'value': 8.5, 'type': '早餐后2小时', 'timestamp': '2024-06-01 11:00:00', 'user_id': 1
            })

            overview_r = client_authenticated.get('/api/day_overview?date=2024-06-01')
            assert overview_r.status_code == 200
            overview = {s['key']: s for s in overview_r.json['overview']}
            assert overview['fasting']['value'] == 6.5
            assert overview['fasting']['status'] == 'measured'
            assert overview['post_exercise']['status'] == 'pending'
            assert overview['post_breakfast']['value'] == 8.5
            assert overview['post_breakfast']['status'] == 'measured'
            assert len(overview_r.json['exercises']) == 1

            stats_r = client_authenticated.get('/api/health_stats?days=all')
            assert stats_r.status_code == 200
            data = stats_r.json
            assert data['glucose']['avg_fasting'] >= 6.5
            assert data['exercise']['total_distance'] == 5.0

            csv_r = client_authenticated.get('/export')
            assert csv_r.status_code == 200
            csv_text = csv_r.data.decode('utf-8-sig')
            assert '6.5' in csv_text
            assert '8.5' in csv_text
            assert '5.0' in csv_text

            record_r = client_authenticated.get(f'/record/{fasting_id}')
            assert record_r.status_code == 200
            assert record_r.json['value'] == 6.5
            assert record_r.json['type'] == '空腹'

    def test_medication_journey(self, client_authenticated, app):
        """创建方案 → 列表 → 更新 → Dashboard"""
        with app.app_context():
            r = client_authenticated.post('/add_medication_plan', json={
                'medication_name': '二甲双胍', 'dosage': '500mg',
                'times_per_day': 3, 'timing_notes': '餐后',
                'start_date': '2024-01-01', 'frequency': 'daily',
            })
            assert r.status_code == 200

            r2 = client_authenticated.post('/add_medication_plan', json={
                'medication_name': '达格列净', 'dosage': '10mg',
                'times_per_day': 1, 'timing_notes': '早餐后',
                'start_date': '2024-01-01', 'frequency': 'daily',
            })
            assert r2.status_code == 200
            dap_id = r2.json['data']['id']

            plans_r = client_authenticated.get('/medication_plans')
            assert plans_r.status_code == 200
            assert len(plans_r.json) >= 2

            update_r = client_authenticated.post(f'/update_medication_plan/{dap_id}', json={
                'medication_name': '达格列净', 'dosage': '12.5mg',
                'times_per_day': 1, 'timing_notes': '早餐后',
                'start_date': '2024-01-01',
            })
            assert update_r.status_code == 200

            overview_r = client_authenticated.get('/api/day_overview?date=2024-06-01')
            assert overview_r.status_code == 200
            med_plans = overview_r.json['med_status']['plans']
            assert any(p['name'] == '二甲双胍' for p in med_plans)
            assert any(p['name'] == '达格列净' for p in med_plans)

    def test_data_import_and_verify(self, client_authenticated, app):
        """CSV 导入 → Dashboard 验证"""
        with app.app_context():
            csv_content = (
                'value,type,timestamp,notes\n'
                '5.8,空腹,2024-06-01 07:15:00,导入测试1\n'
                '9.2,早餐后2小时,2024-06-01 11:00:00,导入测试2\n'
                '7.0,午餐后2小时,2024-06-01 14:30:00,导入测试3\n'
            ).encode('utf-8-sig')

            import_r = client_authenticated.post('/import', data={
                'file': (io.BytesIO(csv_content), 'import_test.csv'),
            })
            assert import_r.status_code == 200

            overview_r = client_authenticated.get('/api/day_overview?date=2024-06-01')
            assert overview_r.status_code == 200
            overview = {s['key']: s for s in overview_r.json['overview']}
            assert overview['fasting']['value'] == 5.8
            assert overview['post_breakfast']['value'] == 9.2
            assert overview['post_lunch']['value'] == 7.0

    def test_prediction_status_with_real_data(self, client_authenticated, app):
        """实测 + 预测 → prediction_status 正确"""
        today_str = datetime.date.today().strftime('%Y-%m-%d')
        with app.app_context():
            client_authenticated.post('/add', json={
                'value': 6.5, 'type': '空腹',
                'timestamp': f'{today_str} 07:15:00', 'user_id': 1
            })
            client_authenticated.post('/batch_add', json={
                'records': [{
                    'value': 6.0, 'type': '空腹',
                    'datetime': f'{today_str} 07:15:01',
                    'is_predicted': True
                }],
                'conflict_resolution': 'overwrite'
            })

            result = client_authenticated.get('/prediction_status')
            assert result.status_code == 200
            slots = result.json['slots']
            fasting_slot = [s for s in slots if s['key'] == 'fasting'][0]
            assert fasting_slot['status'] in ('measured', 'verified')


# ============================================================
# Apple Health 同步
# ============================================================

class TestRealDBHealthSyncFlow:

    def test_bind_and_sync_flow(self, client_authenticated):
        """完整的绑定 + 同步流程"""
        # 1. 生成绑定码
        bind_resp = client_authenticated.post('/api/v1/health-sync/bind')
        assert bind_resp.status_code == 200
        bind_code = bind_resp.json['data']['bind_code']
        assert len(bind_code) == 6

        # 2. 完成绑定（模拟 iOS 捷径）
        bind_shortcut_resp = client_authenticated.post(
            '/api/v1/health-sync/bind_from_shortcut',
            json={'code': bind_code, 'device_name': 'E2E Test iPhone'},
        )
        assert bind_shortcut_resp.status_code == 200
        device_id = bind_shortcut_resp.json['data']['device_id']
        device_token = bind_shortcut_resp.json['data']['device_token']
        assert len(device_id) == 36

        # 3. 查询绑定状态
        confirm_resp = client_authenticated.get('/api/v1/health-sync/confirm_binding')
        assert confirm_resp.status_code == 200
        assert confirm_resp.json['data']['device_id'] == device_id
        assert confirm_resp.json['data']['device_name'] == 'E2E Test iPhone'

        # 4. 同步血糖 + 步数
        sync_resp = client_authenticated.post(
            '/api/v1/health-sync/sync',
            headers={'X-Device-Id': device_id, 'X-Device-Token': device_token},
            json={'records': [
                {'external_id': 'e2e:glucose-1', 'type': '血糖', 'value': 6.2,
                 'unit': 'mmol/L', 'timestamp': '2024-06-01T07:15:00'},
                {'external_id': 'e2e:steps-1', 'type': '步数', 'value': 8500,
                 'unit': 'steps', 'timestamp': '2024-06-01T12:00:00'},
            ]},
        )
        assert sync_resp.status_code == 200
        assert sync_resp.json['data']['inserted'] == 2
        assert sync_resp.json['data']['skipped'] == 0

        # 5. 去重验证：同一 external_id 再次同步应跳过
        dedup_resp = client_authenticated.post(
            '/api/v1/health-sync/sync',
            headers={'X-Device-Id': device_id, 'X-Device-Token': device_token},
            json={'records': [
                {'external_id': 'e2e:glucose-1', 'type': '血糖', 'value': 6.2,
                 'unit': 'mmol/L', 'timestamp': '2024-06-01T07:15:00'},
                {'external_id': 'e2e:new-record', 'type': '体重', 'value': 72.0,
                 'unit': 'kg', 'timestamp': '2024-06-01T08:00:00'},
            ]},
        )
        assert dedup_resp.status_code == 200
        assert dedup_resp.json['data']['inserted'] == 1
        assert dedup_resp.json['data']['skipped'] == 1

        # 6. 解除绑定
        unbind_resp = client_authenticated.post('/api/v1/health-sync/unbind')
        assert unbind_resp.status_code == 200

        # 7. 确认已解除
        final_confirm = client_authenticated.get('/api/v1/health-sync/confirm_binding')
        assert final_confirm.json['data']['device_id'] is None
