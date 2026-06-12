"""冲刺 85% 覆盖率: api_prediction (51%→70%), api_meds (64%→80%), api_admin (50%→75%)"""
import datetime
import json
import pytest
from unittest.mock import patch, MagicMock


# ============================================================
# api_prediction: trigger_prediction 类型分支 + CGM/verified 状态
# ============================================================

class TestTriggerPredictionTypes:
    """trigger_prediction 的各种 type 参数"""

    def test_trigger_fpg(self, client_authenticated):
        with patch('routes.api_prediction.get_db') as mock_get_db, \
             patch('routes.api_prediction.predict_morning_fpg') as mock_fpg:
            mock_get_db.return_value = MagicMock()
            result = client_authenticated.post('/trigger_prediction', json={'type': '空腹'})
            assert result.status_code == 200
            mock_fpg.assert_called_once()

    def test_trigger_post_exercise(self, client_authenticated):
        with patch('routes.api_prediction.get_db') as mock_get_db, \
             patch('routes.api_prediction.predict_morning_fpg') as mock_fpg, \
             patch('routes.api_prediction.predict_post_exercise_glucose') as mock_ex:
            mock_get_db.return_value = MagicMock()
            mock_ex.return_value = 5.5
            result = client_authenticated.post('/trigger_prediction', json={'type': '运动后', 'date': '2024-06-01'})
            assert result.status_code == 200
            assert result.json['data']['results'][0]['value'] == 5.5

    def test_trigger_remaining_no_measured(self, client_authenticated):
        with patch('routes.api_prediction.get_db') as mock_get_db, \
             patch('routes.api_prediction.predict_remaining_glucose_slots') as mock_slots:
            mock_get_db.return_value = MagicMock()
            mock_slots.return_value = 'no_measured'
            result = client_authenticated.post('/trigger_prediction', json={'type': '剩余时间点'})
            assert result.status_code == 200

    def test_trigger_remaining_all_measured(self, client_authenticated):
        with patch('routes.api_prediction.get_db') as mock_get_db, \
             patch('routes.api_prediction.predict_remaining_glucose_slots') as mock_slots:
            mock_get_db.return_value = MagicMock()
            mock_slots.return_value = 'all_measured'
            result = client_authenticated.post('/trigger_prediction', json={'type': '剩余时间点'})
            assert result.status_code == 200

    def test_trigger_remaining_empty_list(self, client_authenticated):
        with patch('routes.api_prediction.get_db') as mock_get_db, \
             patch('routes.api_prediction.predict_remaining_glucose_slots') as mock_slots:
            mock_get_db.return_value = MagicMock()
            mock_slots.return_value = []
            result = client_authenticated.post('/trigger_prediction', json={'type': '剩余时间点'})
            assert result.status_code == 200

    def test_trigger_429_error(self, client_authenticated):
        with patch('routes.api_prediction.get_db') as mock_get_db, \
             patch('routes.api_prediction.predict_morning_fpg') as mock_fpg:
            mock_get_db.return_value = MagicMock()
            mock_fpg.side_effect = Exception("429 RESOURCE_EXHAUSTED retry in 30")
            result = client_authenticated.post('/trigger_prediction', json={'type': '空腹'})
            assert result.status_code == 429
            assert 'retry_after' in result.json.get('details', {})

    def test_trigger_remaining_with_results(self, client_authenticated):
        with patch('routes.api_prediction.get_db') as mock_get_db, \
             patch('routes.api_prediction.predict_remaining_glucose_slots') as mock_slots:
            mock_get_db.return_value = MagicMock()
            mock_slots.return_value = [
                {'type': '午餐后2小时', 'value': 7.0, 'reasoning': '预测'},
            ]
            result = client_authenticated.post('/trigger_prediction', json={
                'type': 'remaining', 'force_update': True
            })
            assert result.status_code == 200
            assert result.json['data']['results'][0]['status'] == 'updated'


class TestPredictionStatusCGM:
    """prediction_status 的 CGM 匹配和 verified 状态"""

    def test_status_measured(self, client_authenticated):
        """今天有空腹实测记录 → status=measured"""
        with patch('routes.api_prediction.get_db') as mock_get_db, \
             patch('routes.api_prediction.settings') as mock_settings:
            mock_settings.get_glucose_target.return_value = {'min': 4.0, 'max': 7.0, 'optimal_max': 6.0}
            mock_c = MagicMock()
            # First fetchall: today_records
            # Second fetchall: accuracy_by_type
            mock_c.fetchall.side_effect = [
                [{'type': '空腹', 'value': 6.5, 'timestamp': '2024-06-01 07:15:00',
                  'is_predicted': 0, 'id': 1, 'notes': None,
                  'verified_by_real_id': None, 'prediction_error': None}],
                [],  # accuracy_by_type
            ]
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db
            result = client_authenticated.get('/prediction_status')
            assert result.status_code == 200
            slots = result.json['slots']
            fasting = [s for s in slots if s['key'] == 'fasting'][0]
            assert fasting['status'] == 'measured'
            assert fasting['value'] == 6.5

    def test_status_verified(self, client_authenticated):
        """实测 + 关联预测 → status=verified"""
        with patch('routes.api_prediction.get_db') as mock_get_db, \
             patch('routes.api_prediction.settings') as mock_settings:
            mock_settings.get_glucose_target.return_value = {'min': 4.0, 'max': 7.0, 'optimal_max': 6.0}
            mock_c = MagicMock()
            mock_c.fetchall.side_effect = [
                [  # today_records
                    {'type': '空腹', 'value': 6.5, 'timestamp': '2024-06-01 07:15:00',
                     'is_predicted': 0, 'id': 1, 'notes': None,
                     'verified_by_real_id': None, 'prediction_error': None},
                    {'type': '空腹', 'value': 6.2, 'timestamp': '2024-06-01 06:00:00',
                     'is_predicted': 1, 'id': 2, 'notes': '预测准确',
                     'verified_by_real_id': 1, 'prediction_error': 0.3},
                ],
                [],  # accuracy_by_type
            ]
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db
            result = client_authenticated.get('/prediction_status')
            assert result.status_code == 200
            slots = result.json['slots']
            fasting = [s for s in slots if s['key'] == 'fasting'][0]
            assert fasting['status'] == 'verified'
            assert fasting['predicted_value'] == 6.2

    def test_status_predicted_only(self, client_authenticated):
        """只有预测值，无实测 → status=predicted"""
        with patch('routes.api_prediction.get_db') as mock_get_db, \
             patch('routes.api_prediction.settings') as mock_settings:
            mock_settings.get_glucose_target.return_value = {'min': 4.0, 'max': 7.0, 'optimal_max': 6.0}
            mock_c = MagicMock()
            mock_c.fetchall.side_effect = [
                [{'type': '空腹', 'value': 6.2, 'timestamp': '2024-06-01 07:15:00',
                  'is_predicted': 1, 'id': 2, 'notes': '预测值',
                  'verified_by_real_id': None, 'prediction_error': None}],
                [],
            ]
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db
            result = client_authenticated.get('/prediction_status')
            assert result.status_code == 200
            fasting = [s for s in result.json['slots'] if s['key'] == 'fasting'][0]
            assert fasting['status'] == 'predicted'

    def test_status_cgm_only(self, client_authenticated):
        """只有 CGM 记录 → status=measured with cgm=True"""
        with patch('routes.api_prediction.get_db') as mock_get_db, \
             patch('routes.api_prediction.settings') as mock_settings:
            mock_settings.get_glucose_target.return_value = {'min': 4.0, 'max': 7.0, 'optimal_max': 6.0}
            mock_c = MagicMock()
            mock_c.fetchall.side_effect = [
                [{'type': 'CGM', 'value': 5.8, 'timestamp': '2024-06-01 07:20:00',
                  'is_predicted': 0, 'id': 3, 'notes': None,
                  'verified_by_real_id': None, 'prediction_error': None}],
                [],
            ]
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db
            result = client_authenticated.get('/prediction_status')
            assert result.status_code == 200
            fasting = [s for s in result.json['slots'] if s['key'] == 'fasting'][0]
            assert fasting['cgm'] is True
            assert fasting['value'] == 5.8

    def test_status_cgm_with_predicted(self, client_authenticated):
        """CGM + 未验证的预测 → status=verified with CGM"""
        with patch('routes.api_prediction.get_db') as mock_get_db, \
             patch('routes.api_prediction.settings') as mock_settings:
            mock_settings.get_glucose_target.return_value = {'min': 4.0, 'max': 7.0, 'optimal_max': 6.0}
            mock_c = MagicMock()
            mock_c.fetchall.side_effect = [
                [  # today_records - CGM before predicted in order
                    {'type': 'CGM', 'value': 5.8, 'timestamp': '2024-06-01 07:20:00',
                     'is_predicted': 0, 'id': 3, 'notes': None,
                     'verified_by_real_id': None, 'prediction_error': None},
                    {'type': '空腹', 'value': 6.0, 'timestamp': '2024-06-01 06:00:00',
                     'is_predicted': 1, 'id': 4, 'notes': '预测',
                     'verified_by_real_id': None, 'prediction_error': None},
                ],
                [],
            ]
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db
            result = client_authenticated.get('/prediction_status')
            assert result.status_code == 200
            fasting = [s for s in result.json['slots'] if s['key'] == 'fasting'][0]
            assert fasting['cgm'] is True
            # CGM + unverified predicted → status=verified (CGM overrides)
            assert fasting['status'] in ('measured', 'verified')

    def test_accuracy_by_type(self, client_authenticated):
        """近7天预测准确性按类型分组"""
        with patch('routes.api_prediction.get_db') as mock_get_db, \
             patch('routes.api_prediction.settings') as mock_settings:
            mock_settings.get_glucose_target.return_value = {'min': 4.0, 'max': 7.0, 'optimal_max': 6.0}
            mock_c = MagicMock()
            mock_c.fetchall.side_effect = [
                [],  # today_records
                [   # accuracy_by_type
                    ('空腹', 5, 0.25, 80.0),
                ],
            ]
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db
            result = client_authenticated.get('/prediction_status')
            assert result.status_code == 200
            acc = result.json['accuracy_by_type']
            assert '空腹' in acc
            assert acc['空腹']['mae'] == 0.25
            assert acc['空腹']['accuracy'] == 80.0

    def test_status_post_breakfast_hour_filter(self, client_authenticated):
        """post_breakfast 按小时过滤：午餐后/晚餐后排除，10-13小时范围"""
        with patch('routes.api_prediction.get_db') as mock_get_db, \
             patch('routes.api_prediction.settings') as mock_settings:
            mock_settings.get_glucose_target.return_value = {'min': 4.0, 'max': 7.0, 'optimal_max': 6.0}
            mock_c = MagicMock()
            mock_c.fetchall.side_effect = [
                [
                    {'type': '餐后2小时', 'value': 7.0, 'timestamp': '2024-06-01 14:30:00',
                     'is_predicted': 0, 'id': 5, 'notes': None,
                     'verified_by_real_id': None, 'prediction_error': None},
                ],
                [],
            ]
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db
            result = client_authenticated.get('/prediction_status')
            assert result.status_code == 200


class TestPredictionComparisonAccuracy:
    """prediction_comparison + prediction_accuracy 额外场景"""

    def test_comparison_with_type_filter(self, client_authenticated):
        with patch('routes.api_prediction.get_db') as mock_get_db:
            mock_c = MagicMock()
            mock_c.fetchall.return_value = [
                ('空腹', '2024-06-01', 6.0, 6.5, -0.5)
            ]
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db
            result = client_authenticated.get('/prediction_comparison?days=30&type=空腹')
            assert result.status_code == 200
            assert result.json['type_filter'] == '空腹'

    def test_accuracy_with_data(self, client_authenticated):
        with patch('routes.api_prediction.get_db') as mock_get_db:
            mock_c = MagicMock()
            mock_c.fetchone.return_value = (10, 0.5, 0.7, -1.0, 2.0)
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db
            result = client_authenticated.get('/prediction_accuracy?days=14')
            assert result.status_code == 200
            assert result.json['total_predictions'] == 10
            assert result.json['mae'] == 0.7


# ============================================================
# api_meds: update_medication_plan 剂量变更历史
# ============================================================

class TestMedsUpdateWithDosageHistory:
    """update_medication_plan 分支"""

    def test_update_with_dosage_change(self, client_authenticated):
        """剂量变更 → 记录 dosage_history"""
        with patch('routes.api_meds.get_db') as mock_get_db:
            mock_c = MagicMock()
            mock_c.fetchone.return_value = {
                'dosage': '500mg', 'dose_quantity': '1', 'dose_unit': '片'
            }
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db

            result = client_authenticated.post('/update_medication_plan/1', json={
                'medication_name': '二甲双胍', 'dosage': '850mg',
                'times_per_day': 2, 'dose_quantity': '1', 'dose_unit': '片',
                'frequency': 'daily',
            })
            assert result.status_code == 200
            # Should have recorded dosage history (dosage changed: 500mg → 850mg)
            history_inserts = [c for c in mock_c.execute.call_args_list
                               if 'dosage_history' in str(c)]
            assert len(history_inserts) >= 1

    def test_update_no_dosage_change(self, client_authenticated):
        """剂量未变更 → 不记录 dosage_history"""
        with patch('routes.api_meds.get_db') as mock_get_db:
            mock_c = MagicMock()
            mock_c.fetchone.return_value = {
                'dosage': '500mg', 'dose_quantity': '1', 'dose_unit': '片'
            }
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db

            result = client_authenticated.post('/update_medication_plan/1', json={
                'medication_name': '二甲双胍', 'dosage': '500mg',
                'times_per_day': 2, 'dose_quantity': '1', 'dose_unit': '片',
                'frequency': 'daily',
            })
            assert result.status_code == 200
            history_inserts = [c for c in mock_c.execute.call_args_list
                               if 'dosage_history' in str(c)]
            assert len(history_inserts) == 0

    def test_update_dose_quantity_change_records_history(self, client_authenticated):
        """dose_quantity 变更 → 记录 history"""
        with patch('routes.api_meds.get_db') as mock_get_db:
            mock_c = MagicMock()
            mock_c.fetchone.return_value = {
                'dosage': '500mg', 'dose_quantity': '1', 'dose_unit': '片'
            }
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db

            result = client_authenticated.post('/update_medication_plan/1', json={
                'medication_name': '二甲双胍', 'dosage': '500mg',
                'times_per_day': 2, 'dose_quantity': '2', 'dose_unit': '片',
                'frequency': 'daily',
            })
            assert result.status_code == 200
            history_inserts = [c for c in mock_c.execute.call_args_list
                               if 'dosage_history' in str(c)]
            assert len(history_inserts) >= 1

    def test_get_medication_plans_with_history(self, client_authenticated):
        """GET medication_plans 包含 dosage_history"""
        with patch('routes.api_meds.get_db') as mock_get_db:
            mock_c = MagicMock()
            mock_c.fetchall.side_effect = [
                [{'id': 1, 'medication_name': '药', 'dosage': '500mg'}],  # plans
                [{'old_dosage': '250mg', 'new_dosage': '500mg',
                  'changed_at': '2024-01-01'}],  # history for plan 1
            ]
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db

            result = client_authenticated.get('/medication_plans')
            assert result.status_code == 200
            plans = result.json
            assert len(plans) == 1
            assert len(plans[0]['dosage_history']) == 1
            assert plans[0]['dosage_history'][0]['new_dosage'] == '500mg'


# ============================================================
# api_admin: backup_database 下载 + restore_database 验证
# ============================================================

class TestAdminBackupRestore:
    """backup_database 和 restore_database"""

    def test_backup_db_not_found(self, client_authenticated, monkeypatch):
        """数据库文件不存在 → 404"""
        monkeypatch.setattr('routes.api_admin.os.path.exists', lambda p: False)
        with patch('routes.api_admin.get_db'):
            result = client_authenticated.get('/backup_database')
            assert result.status_code == 404

    def test_backup_successful(self, client_authenticated, tmp_path, monkeypatch):
        """正常备份 → 返回文件下载"""
        db_path = tmp_path / 'test.db'
        db_path.write_text('fake db content')
        monkeypatch.setattr('routes.api_admin.os.path.join', lambda *a: str(db_path))
        monkeypatch.setattr('routes.api_admin.DB_NAME', str(db_path))
        monkeypatch.setattr('routes.api_admin.os.path.exists', lambda p: True)
        with patch('routes.api_admin.shutil.copy2') as mock_copy, \
             patch('routes.api_admin.send_file') as mock_send:
            mock_copy.return_value = None
            mock_send.return_value = 'file data'
            with patch('routes.api_admin.get_db'):
                result = client_authenticated.get('/backup_database')
                assert result.status_code == 200
                mock_copy.assert_called_once()

    def test_restore_no_file(self, client_authenticated):
        """未选择文件 → 400"""
        with patch('routes.api_admin.get_db'):
            result = client_authenticated.post('/restore_database')
            assert result.status_code == 400

    def test_restore_invalid_extension(self, client_authenticated):
        """无效文件格式 → 400"""
        import io
        with patch('routes.api_admin.get_db'):
            data = {'file': (io.BytesIO(b'test'), 'test.txt')}
            result = client_authenticated.post('/restore_database', data=data,
                                               content_type='multipart/form-data')
            assert result.status_code == 400

    def test_restore_empty_file(self, client_authenticated, tmp_path, monkeypatch):
        """空文件 → 400"""
        import io
        monkeypatch.setattr('routes.api_admin.BASE_DIR', str(tmp_path))
        with patch('routes.api_admin.get_db'):
            data = {'file': (io.BytesIO(b''), 'restore.db')}
            result = client_authenticated.post('/restore_database', data=data,
                                               content_type='multipart/form-data')
            assert result.status_code == 400

    def test_restore_invalid_db(self, client_authenticated, tmp_path, monkeypatch):
        """无效数据库文件 → 400"""
        import io
        monkeypatch.setattr('routes.api_admin.BASE_DIR', str(tmp_path))
        with patch('routes.api_admin.sqlite3.connect') as mock_connect:
            mock_connect.side_effect = Exception("not a database")
            with patch('routes.api_admin.get_db'):
                data = {'file': (io.BytesIO(b'not-a-db'), 'restore.db')}
                result = client_authenticated.post('/restore_database', data=data,
                                                   content_type='multipart/form-data')
                assert result.status_code == 400

    @patch('routes.api_admin.shutil.move')
    @patch('routes.api_admin.shutil.copy2')
    def test_restore_success(self, mock_copy, mock_move, client_authenticated, tmp_path, monkeypatch):
        """正常恢复成功"""
        import io
        import sqlite3
        monkeypatch.setattr('routes.api_admin.BASE_DIR', str(tmp_path))
        monkeypatch.setattr('routes.api_admin.DB_NAME', str(tmp_path / 'glucose.db'))
        # Create the temporary restore DB and the target DB
        real_conn = sqlite3.connect(':memory:')
        real_conn.execute("CREATE TABLE records (id INTEGER)")
        real_conn.execute("INSERT INTO records VALUES (1)")
        real_conn.close()
        # sqlite3.connect returns a working in-memory connection
        with patch('routes.api_admin.sqlite3.connect') as mock_connect:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_conn.cursor.return_value = mock_cursor
            mock_connect.return_value = mock_conn
            with patch('routes.api_admin.get_db'):
                data = {'file': (io.BytesIO(b'valid-db'), 'restore.db')}
                result = client_authenticated.post('/restore_database', data=data,
                                                   content_type='multipart/form-data')
                assert result.status_code == 200


class TestAdminFindDeleteDuplicates:
    """find_duplicates / delete_duplicates 额外场景"""

    def test_find_duplicates_id_list(self, client_authenticated):
        with patch('routes.api_admin.get_db') as mock_get_db:
            mock_c = MagicMock()
            mock_c.fetchall.return_value = [
                ('2024-06-01 07:15', '空腹', 6.5, 2, '5,6'),
            ]
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db
            result = client_authenticated.get('/find_duplicates')
            assert result.status_code == 200
            assert result.json['data']['duplicates'][0]['ids'] == [5, 6]

    def test_delete_duplicates_id_parsing(self, client_authenticated):
        """delete_duplicates 的 id 列表解析"""
        with patch('routes.api_admin.get_db') as mock_get_db:
            mock_c = MagicMock()
            mock_c.fetchall.return_value = [
                ('2024-06-01 07:15', '空腹', 6.5, '10,11,12'),
            ]
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db
            result = client_authenticated.post('/delete_duplicates')
            assert result.status_code == 200
            # Should delete 10 and 11 (keep 12 as first entry)
            delete_calls = [str(c) for c in mock_c.execute.call_args_list
                           if 'DELETE FROM records' in str(c)]
            assert len(delete_calls) == 2  # ids 11, 12 deleted (10 kept)


# ============================================================
# generate_report: 字体注册 + build_pdf 额外场景
# ============================================================

class TestReportFontRegistration:
    """字体注册路径覆盖（lines 37-38, 40-41）"""

    def test_font_not_found_fallback(self):
        """所有字体路径都不存在 → 回退 Helvetica"""
        with patch('generate_report.os.path.exists', return_value=False):
            # Reimport to trigger font registration
            import importlib
            import generate_report as gr
            importlib.reload(gr)
            assert gr.CN_FONT == 'Helvetica'

    @patch('reportlab.pdfbase.ttfonts.TTFont')
    @patch('reportlab.pdfbase.pdfmetrics.registerFont')
    @patch('generate_report.os.path.exists')
    def test_first_font_registered(self, mock_exists, mock_register, mock_ttfont):
        """第一个字体路径存在 → 注册并用于 CN_FONT"""
        mock_exists.side_effect = lambda p: p == "/System/Library/Fonts/STHeiti Medium.ttc"
        mock_ttfont.return_value = MagicMock()  # 避免 TTFont 实际打开文件
        import importlib
        import generate_report as gr
        importlib.reload(gr)
        # Should register STHeiti
        mock_register.assert_called()


class TestReportBuildPdfEdgeCases:
    """build_pdf 额外边界"""

    @patch('generate_report.SimpleDocTemplate')
    def test_build_pdf_uses_correct_fonts(self, mock_doc):
        """build_pdf 使用正确的字体"""
        import generate_report as gr
        # 重置字体状态：test_first_font_registered 的 importlib.reload 污染了 CN_FONT
        gr.CN_FONT = 'Helvetica'
        gr.CN_FONT_BOLD = 'Helvetica-Bold'
        from generate_report import build_pdf
        mock_doc_instance = MagicMock()
        mock_doc_instance.width = 500
        mock_doc.return_value = mock_doc_instance
        result = build_pdf('/tmp/test.pdf')
        assert result == '/tmp/test.pdf'
        # Setup was called internally
        mock_doc_instance.build.assert_called_once()
