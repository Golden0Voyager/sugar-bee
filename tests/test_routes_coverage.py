"""
routes/ 模块遗漏分支补全测试

覆盖目标:
  - api_admin.py:    90% →   L38-39 (backup no file), L48-50 (restore validation), L91-96 (find_duplicates except)
  - api_meds.py:     86% →   L43-45 (add except), L61-62 (get except), L75-76 (not found), L117-119 (update except), L132-133 (delete except), L144-145 (toggle except)
  - api_chat.py:     98% →   L143 (save msg except), L152-153 (empty session_id)
  - api_user.py:     98% →   L83-84 (get_current_user except), L156-158 (delete_user non-existent)
  - api_records.py:  93% →   data validation warnings, duplicate detection, form-data branch, import_csv
  - api_dashboard.py:93% →   med frequency branches (every_n_days, weekdays, weekly, biweekly, monthly)
  - api_prediction.py:93% →   quota_exceeded, prediction_comparison type filter
"""
import datetime
import json
import sqlite3
from unittest.mock import MagicMock, patch

# ============================================================
# api_admin.py
# ============================================================

class TestAdminBackup:
    """backup_database + restore_database 遗漏分支"""

    def test_backup_db_not_found(self, client_authenticated):
        """L38-39: DB_NAME 不存在 -> 404"""
        with patch('routes.api_admin.os.path.exists', return_value=False):
            resp = client_authenticated.get('/backup_database')
            assert resp.status_code == 404

    def test_restore_no_file(self, client_authenticated):
        """L48-50: 没有上传文件 -> 400"""
        resp = client_authenticated.post('/restore_database', data={})
        assert resp.status_code == 400

    def test_restore_empty_file(self, client_authenticated):
        """空文件 -> 400"""
        with patch('routes.api_admin.os.path.getsize', return_value=0), \
             patch('routes.api_admin.os.path.exists', return_value=True):
            data = {'file': (b'', 'test.db')}
            resp = client_authenticated.post('/restore_database', data=data,
                                             content_type='multipart/form-data')
            assert resp.status_code == 400

    def test_find_duplicates_exception(self, client_authenticated):
        """L91-96: 数据库异常 -> 500"""
        with patch('routes.api_admin.get_db') as mock_get_db:
            mock_get_db.side_effect = Exception("db crash")
            resp = client_authenticated.get('/find_duplicates')
            assert resp.status_code == 500


class TestAdminDuplicates:
    """find_duplicates + delete_duplicates 正常路径"""

    def test_delete_duplicates_success(self, client_authenticated):
        with patch('routes.api_admin.get_db') as mock_get_db:
            mock_c = MagicMock()
            mock_c.fetchall.return_value = [
                ('2024-06-01 07:15', '空腹', 6.5, '1,2'),
            ]
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db
            resp = client_authenticated.post('/delete_duplicates')
            assert resp.status_code == 200

    def test_delete_duplicates_exception(self, client_authenticated):
        """L184-187: delete_duplicates SQL 异常 → 500 + rollback"""
        with patch('routes.api_admin.get_db') as mock_get_db:
            mock_c = MagicMock()
            mock_c.execute.side_effect = Exception("query error")
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db
            resp = client_authenticated.post('/delete_duplicates')
            assert resp.status_code == 500
            assert resp.json['error_type'] == 'delete_error'


# ============================================================
# api_meds.py — 异常分支 (86% → ~95%)
# ============================================================

class TestMedsExceptionBranches:
    """各蓝图函数的 except 分支"""

    def test_add_exception(self, client_authenticated):
        """L43-45: add_medication_plan 数据库异常 -> 500"""
        with patch('routes.api_meds.get_db') as mock_get_db:
            mock_get_db.side_effect = Exception("db error")
            resp = client_authenticated.post('/add_medication_plan',
                data=json.dumps({'medication_name': 'test'}),
                content_type='application/json')
            assert resp.status_code == 500

    def test_get_plans_exception(self, client_authenticated):
        """L61-62: get_medication_plans 异常 -> 500"""
        with patch('routes.api_meds.get_db') as mock_get_db:
            mock_get_db.side_effect = Exception("db error")
            resp = client_authenticated.get('/medication_plans')
            assert resp.status_code == 500

    def test_get_plan_not_found(self, client_authenticated):
        """L75-76: 找不到计划 -> 404"""
        with patch('routes.api_meds.get_db') as mock_get_db:
            mock_c = MagicMock()
            mock_c.fetchone.return_value = None
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db
            resp = client_authenticated.get('/medication_plan/999')
            assert resp.status_code == 404

    def test_update_exception(self, client_authenticated):
        """L117-119: update 异常 -> 500"""
        with patch('routes.api_meds.get_db') as mock_get_db:
            mock_get_db.side_effect = Exception("db error")
            resp = client_authenticated.post('/update_medication_plan/1',
                data=json.dumps({'medication_name': 'test'}),
                content_type='application/json')
            assert resp.status_code == 500

    def test_delete_exception(self, client_authenticated):
        """L132-133: delete 异常 -> 500"""
        with patch('routes.api_meds.get_db') as mock_get_db:
            mock_get_db.side_effect = Exception("db error")
            resp = client_authenticated.post('/delete_medication_plan/1')
            assert resp.status_code == 500

    def test_toggle_exception(self, client_authenticated):
        """L144-145: toggle 异常 -> 500"""
        with patch('routes.api_meds.get_db') as mock_get_db:
            mock_get_db.side_effect = Exception("db error")
            resp = client_authenticated.post('/toggle_medication_plan/1')
            assert resp.status_code == 500


class TestGetMedicationPlanBranches:
    """L73: get_medication_plan 计划存在路径 + L75-76: 异常处理器"""

    def test_get_plan_found(self, client_authenticated):
        """L73: 计划存在 → fetchone 返回 dict → jsonify"""
        with patch('routes.api_meds.get_db') as mock_get_db:
            row_data = {
                'id': 1, 'medication_name': '二甲双胍',
                'dosage': '500mg', 'times_per_day': 2,
            }
            mock_row = MagicMock()
            # dict(row) 需要 keys() + __getitem__ 支持
            mock_row.keys.return_value = list(row_data.keys())
            mock_row.__getitem__.side_effect = \
                lambda k: row_data.get(k) if isinstance(k, str) else None
            mock_c = MagicMock()
            mock_c.fetchone.return_value = mock_row
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db

            resp = client_authenticated.get('/medication_plan/1')
            assert resp.status_code == 200
            data = resp.json
            assert data['medication_name'] == '二甲双胍'
            assert data['dosage'] == '500mg'

    def test_get_plan_exception(self, client_authenticated):
        """L75-76: get_db 抛出异常 → 500"""
        with patch('routes.api_meds.get_db', side_effect=Exception("db crash")):
            resp = client_authenticated.get('/medication_plan/1')
            assert resp.status_code == 500
            assert 'error' in resp.json


# ============================================================
# api_chat.py (78% → ~95%) — SSE 流式 + save msg + history/new/delete
# ============================================================

class TestChatStream:
    """chat_stream SSE 流式响应全覆盖：成功路径、AI 异常、save 异常"""

    def test_chat_stream_success(self, client_authenticated):
        """SSE 流式成功：call_chat_stream 返回数据块 -> 逐块 SSE 发送 + [DONE]"""
        chunks = ["你好", "，", "有什么", "可以帮助"]

        with patch('routes.api_chat.call_chat_stream') as mock_stream, \
             patch('routes.api_chat.build_chat_context', return_value='test context'), \
             patch('routes.api_chat.get_db') as mock_get_db, \
             patch('routes.api_chat.CHAT_AVAILABLE', True):
            mock_stream.return_value = chunks
            mock_c = MagicMock()
            mock_c.fetchall.return_value = []
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db

            resp = client_authenticated.post('/api/chat/stream',
                data=json.dumps({'message': '你好', 'session_id': 'test-sid'}),
                content_type='application/json')

            assert resp.status_code == 200
            assert resp.mimetype == 'text/event-stream'
            data = resp.get_data(as_text=True)
            for chunk in chunks:
                assert f'data: {{"content": "{chunk}"}}' in data
            assert 'data: [DONE]' in data

    def test_chat_stream_ai_error(self, client_authenticated):
        """SSE 异常：call_chat_stream 抛出异常 -> error SSE + [DONE]"""
        with patch('routes.api_chat.call_chat_stream') as mock_stream, \
             patch('routes.api_chat.build_chat_context', return_value='test context'), \
             patch('routes.api_chat.get_db') as mock_get_db, \
             patch('routes.api_chat.CHAT_AVAILABLE', True):
            mock_stream.side_effect = Exception("AI service unavailable")
            mock_c = MagicMock()
            mock_c.fetchall.return_value = []
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db

            resp = client_authenticated.post('/api/chat/stream',
                data=json.dumps({'message': '你好', 'session_id': 'test-sid'}),
                content_type='application/json')

            assert resp.status_code == 200
            data = resp.get_data(as_text=True)
            assert 'data: {"error": "AI service unavailable"}' in data
            assert 'data: [DONE]' in data

    def test_chat_stream_save_exception(self, client_authenticated):
        """SSE 完成 save 异常：finally 块连接失败 -> except pass + [DONE]"""
        chunks = ["正常回复"]

        with patch('routes.api_chat.call_chat_stream') as mock_stream, \
             patch('routes.api_chat.build_chat_context', return_value='test context'), \
             patch('routes.api_chat.get_db') as mock_get_db, \
             patch('routes.api_chat.CHAT_AVAILABLE', True):
            mock_stream.return_value = chunks
            mock_c = MagicMock()
            mock_c.fetchall.return_value = []
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_db.execute.side_effect = Exception("save fail")
            mock_get_db.return_value = mock_db

            resp = client_authenticated.post('/api/chat/stream',
                data=json.dumps({'message': '你好', 'session_id': 'test-sid'}),
                content_type='application/json')

            assert resp.status_code == 200
            data = resp.get_data(as_text=True)
            assert 'data: {"content": "正常回复"}' in data
            assert 'data: [DONE]' in data


class TestChatHistory:
    """chat_history 遗漏分支：自动查找最新会话、指定 session_id"""

    def test_chat_history_empty_session_id(self, client_authenticated):
        """L152-153: 无 session_id 且无历史 -> 空列表"""
        with patch('routes.api_chat.get_db') as mock_get_db:
            mock_c = MagicMock()
            mock_c.fetchone.return_value = None  # no recent session
            mock_c.fetchall.return_value = []
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db
            resp = client_authenticated.get('/api/chat/history')
            assert resp.status_code == 200
            data = resp.json
            assert data['data']['session_id'] == ''

    def test_chat_history_auto_latest_session(self, client_authenticated):
        """无 session_id 参数但有最新会话 -> 自动填充 session_id"""
        with patch('routes.api_chat.get_db') as mock_get_db:
            mock_c = MagicMock()
            mock_c.fetchone.return_value = ('auto-sid',)
            mock_c.fetchall.side_effect = [
                [('user', '今天血糖怎么样', '2024-06-01 07:00:00')],
                [('auto-sid', '今天血糖怎么样', '2024-06-01 07:00:00')],
            ]
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db
            resp = client_authenticated.get('/api/chat/history')
            assert resp.status_code == 200
            data = resp.json
            assert data['data']['session_id'] == 'auto-sid'
            assert len(data['data']['messages']) == 1

    def test_chat_history_with_session(self, client_authenticated):
        """指定 session_id -> 返回历史消息 + 会话列表"""
        with patch('routes.api_chat.get_db') as mock_get_db:
            mock_c = MagicMock()
            mock_c.fetchall.side_effect = [
                [('user', '你好', '2024-06-01 07:00:00'),
                 ('assistant', '你好！', '2024-06-01 07:00:05')],
                [('sid1', '你好', '2024-06-01 07:00:00')],
            ]
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db
            resp = client_authenticated.get('/api/chat/history?session_id=sid1')
            assert resp.status_code == 200
            data = resp.json
            assert data['data']['session_id'] == 'sid1'
            assert len(data['data']['messages']) == 2


class TestChatNewDelete:
    """chat_new_session + chat_delete_session"""

    def test_chat_new_session(self, client_authenticated):
        """POST /api/chat/new_session -> 返回 UUID"""
        resp = client_authenticated.post('/api/chat/new_session')
        assert resp.status_code == 200
        data = resp.json
        sid = data['data']['session_id']
        assert len(sid) == 36  # UUID v4 = 36 字符

    def test_chat_delete_session(self, client_authenticated):
        """DELETE /api/chat/session/<id> -> 删除会话"""
        with patch('routes.api_chat.get_db') as mock_get_db:
            mock_db = MagicMock()
            mock_get_db.return_value = mock_db
            resp = client_authenticated.delete('/api/chat/session/test-sid')
            assert resp.status_code == 200
            assert resp.json['status'] == 'success'
            mock_db.execute.assert_called_once()


# ============================================================
# api_user.py (83% → ~100%)
#   L21 allowed_file, L43-47 switch_user SQL, L83-84 get_current_user except,
#   L130-132 update_modules validation, L137-158 upload_avatar, L263-274 sync_garmin
# ============================================================

class TestAllowedFile:
    """allowed_file (L21): 扩展名校验"""

    def test_allowed_file_valid(self):
        from routes.api_user import allowed_file
        for ext in ['png', 'jpg', 'jpeg', 'gif']:
            assert allowed_file(f'avatar.{ext}')

    def test_allowed_file_invalid(self):
        from routes.api_user import allowed_file
        assert not allowed_file('file.txt')
        assert not allowed_file('file.pdf')
        assert not allowed_file('file')
        assert not allowed_file('')


class TestUserBranches:
    """switch_user + delete_user + get_current_user"""

    def test_get_current_user_exception(self, client_authenticated):
        """L83-84: get_user 异常 -> 500"""
        with patch('routes.api_user.user_manager.get_user', side_effect=Exception("db error")):
            resp = client_authenticated.get('/get_current_user')
            assert resp.status_code == 500
            assert 'error' in resp.json

    def test_get_current_user_default(self, client_authenticated):
        """get_user 返回 None -> 默认用户"""
        with patch('routes.api_user.user_manager.get_user', return_value=None):
            resp = client_authenticated.get('/get_current_user')
            assert resp.status_code == 200
            assert resp.json == {'id': 1, 'username': 'default'}

    def test_delete_user_not_found(self, client_authenticated):
        """L156-158: 用户不存在 -> 404"""
        with patch('routes.api_user.get_db') as mock_get_db:
            mock_c = MagicMock()
            mock_c.fetchone.return_value = None
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db

            from user_manager import UserManager
            current_id = UserManager('').get_current_user_id()
            target_id = current_id if current_id != 2 else 3

            resp = client_authenticated.post(f'/delete_user/{target_id}')
            if resp.status_code == 400:
                assert resp.json['error_type'] == 'validation_error'
            else:
                assert resp.status_code == 404

    def test_delete_user_self(self, client_authenticated):
        """删除当前登录用户 -> 400"""
        with client_authenticated.session_transaction() as sess:
            current_id = sess['current_user_id']
        resp = client_authenticated.post(f'/delete_user/{current_id}')
        assert resp.status_code == 400
        assert resp.json['error_type'] == 'validation_error'

    def test_switch_user_password_wrong(self, client_authenticated):
        """L43-47: has_password=True + 密码错误 -> 401 (含 SQL 查询路径)"""
        mock_row = MagicMock()
        mock_row.__getitem__.return_value = '_test'  # 支持 row['username']
        mock_c = MagicMock()
        mock_c.fetchone.return_value = mock_row
        mock_db = MagicMock()
        mock_db.cursor.return_value = mock_c
        with patch('routes.api_user.user_manager.has_password', return_value=True), \
             patch('routes.api_user.user_manager.get_user', return_value={'id': 1, 'username': '_test'}), \
             patch('routes.api_user.user_manager.authenticate', return_value=False), \
             patch('routes.api_user.get_db', return_value=mock_db):
            resp = client_authenticated.post('/switch_user/1',
                data=json.dumps({'password': 'wrong'}),
                content_type='application/json')
            assert resp.status_code == 401

    def test_switch_user_no_password(self, client_authenticated):
        """has_password=False -> 跳过密码验证，直接切换"""
        with patch('routes.api_user.user_manager.has_password', return_value=False), \
             patch('routes.api_user.user_manager.get_user', return_value={'id': 1, 'username': '_test'}):
            resp = client_authenticated.post('/switch_user/1',
                data=json.dumps({}),
                content_type='application/json')
            assert resp.status_code == 200

    def test_switch_user_not_found(self, client_authenticated):
        """用户不存在 -> 404"""
        with patch('routes.api_user.user_manager.get_user', return_value=None):
            resp = client_authenticated.post('/switch_user/999',
                data=json.dumps({}),
                content_type='application/json')
            assert resp.status_code == 404


class TestUserModulesBranches:
    """update_user_modules 验证分支"""

    def test_update_modules_not_list(self, client_authenticated):
        """L130-132: enabled_modules 不是数组 -> 400"""
        resp = client_authenticated.post('/api/user/modules',
            data=json.dumps({'enabled_modules': 'glucose'}),
            content_type='application/json')
        assert resp.status_code == 400
        assert resp.json['error_type'] == 'validation_error'


class TestUploadAvatar:
    """upload_avatar 全覆盖 (L137-158)"""

    def test_upload_no_file(self, client_authenticated):
        """无文件 -> 400"""
        resp = client_authenticated.post('/upload_avatar', data={})
        assert resp.status_code == 400

    def test_upload_invalid_ext(self, client_authenticated):
        """不支持的文件格式 -> 400"""
        data = {'avatar': (b'data', 'avatar.txt')}
        resp = client_authenticated.post('/upload_avatar',
            data=data, content_type='multipart/form-data')
        assert resp.status_code == 400

    def test_upload_success(self, client_authenticated):
        """有效文件 -> 200"""
        with patch('routes.api_user.get_db') as mock_get_db, \
             patch('routes.api_user.current_app') as mock_app, \
             patch('routes.api_user.os.path.join', return_value='/tmp/avatar_test.png'):
            mock_app.config = {'UPLOAD_FOLDER': '/tmp'}
            mock_db = MagicMock()
            mock_get_db.return_value = mock_db
            from io import BytesIO
            data = {'avatar': (BytesIO(b'png-data'), 'avatar.png')}
            resp = client_authenticated.post('/upload_avatar', data=data)
            assert resp.status_code == 200
            assert resp.json['status'] == 'success'


class TestUserProviderBindings:
    """bind_phone + bind_email + unbind_provider"""

    def test_bind_phone_invalid_format(self, client_authenticated):
        """手机号格式错误 -> 400"""
        resp = client_authenticated.post('/bind_phone',
            data=json.dumps({'phone': '123'}),
            content_type='application/json')
        assert resp.status_code == 400

    def test_bind_phone_success(self, client_authenticated):
        """有效手机号 -> 200"""
        with patch('routes.api_user.user_manager.bind_provider',
                   return_value={'ok': True}):
            resp = client_authenticated.post('/bind_phone',
                data=json.dumps({'phone': '13800138000'}),
                content_type='application/json')
            assert resp.status_code == 200

    def test_bind_phone_already_bound(self, client_authenticated):
        """手机号已被绑定 -> 400"""
        with patch('routes.api_user.user_manager.bind_provider',
                   return_value={'ok': False, 'message': '已被绑定'}):
            resp = client_authenticated.post('/bind_phone',
                data=json.dumps({'phone': '13800138000'}),
                content_type='application/json')
            assert resp.status_code == 400

    def test_bind_email_invalid_format(self, client_authenticated):
        """邮箱格式错误 -> 400"""
        resp = client_authenticated.post('/bind_email',
            data=json.dumps({'email': 'not-email'}),
            content_type='application/json')
        assert resp.status_code == 400

    def test_bind_email_success(self, client_authenticated):
        """有效邮箱 -> 200"""
        with patch('routes.api_user.user_manager.bind_provider',
                   return_value={'ok': True}):
            resp = client_authenticated.post('/bind_email',
                data=json.dumps({'email': 'test@example.com'}),
                content_type='application/json')
            assert resp.status_code == 200

    def test_unbind_invalid_provider(self, client_authenticated):
        """不支持的解绑类型 -> 400"""
        resp = client_authenticated.post('/unbind_provider',
            data=json.dumps({'provider': 'wechat'}),
            content_type='application/json')
        assert resp.status_code == 400

    def test_unbind_success(self, client_authenticated):
        """解绑手机号 -> 200"""
        with patch('routes.api_user.user_manager.unbind_provider'):
            resp = client_authenticated.post('/unbind_provider',
                data=json.dumps({'provider': 'phone'}),
                content_type='application/json')
            assert resp.status_code == 200


class TestUserSyncGarmin:
    """sync_garmin 全覆盖 (L263-274)"""

    def test_sync_not_configured(self, client_authenticated):
        """GARMIN_USER_ID=0 / GARMIN_EMAIL 未设置 -> 400"""
        with patch('routes.api_user.os.environ.get') as mock_get:
            mock_get.side_effect = lambda k, d=None: '' if k == 'GARMIN_EMAIL' else '0'
            resp = client_authenticated.post('/sync_garmin')
            assert resp.status_code == 400

    def test_sync_wrong_user(self, client_authenticated):
        """当前用户不是 Garmin 绑定用户 -> 403"""
        with patch('routes.api_user.os.environ.get') as mock_get:
            mock_get.side_effect = lambda k, d=None: {
                'GARMIN_USER_ID': '999',
                'GARMIN_EMAIL': 'test@garmin.com'
            }.get(k, d)
            resp = client_authenticated.post('/sync_garmin')
            assert resp.status_code == 403


# ============================================================
# api_records.py (93%)
# ============================================================

class TestRecordsValidateWarnings:
    """_validate_record_data 数据范围警告"""

    def test_systolic_too_low_warning(self, client_authenticated):
        """收缩压 < 60 -> 警告"""
        from routes.api_records import _validate_record_data
        warns = _validate_record_data({
            'systolic_pressure': 50, 'diastolic_pressure': 80,
            'type': '血压测量', 'value': 0
        })
        assert any('收缩压' in w for w in warns)

    def test_diastolic_too_high_warning(self):
        from routes.api_records import _validate_record_data
        warns = _validate_record_data({
            'systolic_pressure': 120, 'diastolic_pressure': 200,
        })
        assert any('舒张压' in w for w in warns)

    def test_systolic_lte_diastolic_warning(self):
        from routes.api_records import _validate_record_data
        warns = _validate_record_data({
            'systolic_pressure': 80, 'diastolic_pressure': 80,
        })
        assert any('不应小于等于' in w for w in warns)

    def test_spo2_out_of_range_warning(self):
        from routes.api_records import _validate_record_data
        warns = _validate_record_data({'spo2': 85})
        assert any('血氧饱和度' in w for w in warns)

    def test_pulse_out_of_range_warning(self):
        from routes.api_records import _validate_record_data
        warns = _validate_record_data({'pulse_rate': 250})
        assert any('脉搏' in w for w in warns)

    def test_glucose_out_of_range_warning(self):
        from routes.api_records import _validate_record_data
        warns = _validate_record_data({'value': 50, 'type': '空腹'})
        assert any('血糖值' in w for w in warns)

    def test_weight_out_of_range_warning(self):
        from routes.api_records import _validate_record_data
        warns = _validate_record_data({'weight': 350})
        assert any('体重' in w for w in warns)


class TestRecordsDuplicateDetection:
    """重复记录检测"""

    def test_bp_duplicate(self, client_authenticated):
        """血压重复 -> 409"""
        with patch('routes.api_records.get_db') as mock_get_db:
            mock_c = MagicMock()
            mock_c.fetchone.return_value = {'id': 1, 'timestamp': '2024-06-01 07:15:00'}
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db
            resp = client_authenticated.post('/add',
                data=json.dumps({
                    'systolic_pressure': 120, 'diastolic_pressure': 80,
                    'value': 0, 'type': '血压测量', 'timestamp': '2024-06-01 07:15:00'
                }),
                content_type='application/json')
            assert resp.status_code == 409

    def test_weight_duplicate(self, client_authenticated):
        """体重重复 -> 409"""
        with patch('routes.api_records.get_db') as mock_get_db:
            mock_c = MagicMock()
            mock_c.fetchone.return_value = {'id': 1, 'timestamp': '2024-06-01 07:15:00', 'weight': 70.0}
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db
            resp = client_authenticated.post('/add',
                data=json.dumps({
                    'weight': 70.0, 'value': 0, 'type': '体重记录',
                    'timestamp': '2024-06-01 07:15:00'
                }),
                content_type='application/json')
            assert resp.status_code == 409

    def test_glucose_duplicate(self, client_authenticated):
        """血糖重复 -> 409"""
        with patch('routes.api_records.get_db') as mock_get_db:
            mock_c = MagicMock()
            mock_c.fetchone.return_value = {'id': 1, 'timestamp': '2024-06-01 07:15:00', 'value': 6.5}
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db
            resp = client_authenticated.post('/add',
                data=json.dumps({
                    'value': 6.5, 'type': '空腹', 'timestamp': '2024-06-01 07:15:00'
                }),
                content_type='application/json')
            assert resp.status_code == 409


class TestRecordsExport:
    """export 路径"""

    def test_export_no_data(self, client_authenticated):
        """无数据导出 -> 返回 CSV"""
        with patch('routes.api_records.get_db') as mock_get_db:
            import pandas as pd
            mock_df = pd.DataFrame()
            mock_read = MagicMock(return_value=mock_df)
            with patch('routes.api_records.pd.read_sql_query', mock_read):
                mock_db = MagicMock()
                mock_get_db.return_value = mock_db
                resp = client_authenticated.get('/export')
                assert resp.status_code == 200
                assert resp.mimetype == 'text/csv'


# ============================================================
# api_dashboard.py (93%) — 用药频率分支
# ============================================================

class TestDayOverviewMedFrequencies:
    """day_overview 药用频率分支"""

    def test_med_every_n_days(self, client_authenticated):
        with patch('routes.api_dashboard.get_db') as mock_get_db, \
             patch('routes.api_dashboard.settings') as mock_settings:
            mock_settings.get_badge_for_rate.return_value = {'key': 'good', 'icon': '👍'}
            mock_settings.get_bmi_category.return_value = {'label': '正常', 'color': '#4CAF50'}
            mock_settings.check_glucose_compliance.return_value = {'is_compliant': True, 'level': 'optimal'}
            mock_c = MagicMock()
            mock_c.fetchall.side_effect = [
                [], [], [], [],  # records/exercises/bp/weight
                [{  # all_meds
                    'id': 1, 'medication_name': '二甲双胍', 'dosage': '500mg',
                    'dose_quantity': '1', 'dose_unit': '片', 'times_per_day': 2,
                    'timing_notes': '餐前', 'frequency': 'every_n_days',
                    'frequency_detail': '2', 'start_date': '2024-05-01',
                    'category': 'long_term', 'med_type': 'oral',
                }],
                [{'plan_id': 1, 'count': 2}],  # taken_logs
                []   # temp_meds
            ]
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db
            resp = client_authenticated.get('/api/day_overview?date=2024-06-01')
            assert resp.status_code == 200

    def test_med_weekdays(self, client_authenticated):
        with patch('routes.api_dashboard.get_db') as mock_get_db, \
             patch('routes.api_dashboard.settings') as mock_settings:
            mock_settings.get_badge_for_rate.return_value = {'key': 'good', 'icon': '👍'}
            mock_settings.get_bmi_category.return_value = {'label': '正常', 'color': '#4CAF50'}
            mock_settings.check_glucose_compliance.return_value = {'is_compliant': True, 'level': 'optimal'}
            mock_c = MagicMock()
            # 2024-06-01 is Saturday → weekdays should skip
            mock_c.fetchall.side_effect = [
                [], [], [], [],
                [{'id': 2, 'medication_name': '阿司匹林', 'dosage': '100mg',
                  'dose_quantity': '1', 'dose_unit': '片', 'times_per_day': 1,
                  'timing_notes': '睡前', 'frequency': 'weekdays',
                  'frequency_detail': '', 'start_date': None,
                  'category': 'long_term', 'med_type': 'oral'}],
                [], []
            ]
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db
            resp = client_authenticated.get('/api/day_overview?date=2024-06-01')
            assert resp.status_code == 200

    def test_med_weekly_monday(self, client_authenticated):
        """weekly with Monday default"""
        with patch('routes.api_dashboard.get_db') as mock_get_db, \
             patch('routes.api_dashboard.settings') as mock_settings:
            mock_settings.get_badge_for_rate.return_value = {'key': 'good', 'icon': '👍'}
            mock_settings.get_bmi_category.return_value = {'label': '正常', 'color': '#4CAF50'}
            mock_settings.check_glucose_compliance.return_value = {'is_compliant': True, 'level': 'optimal'}
            mock_c = MagicMock()
            mock_c.fetchall.side_effect = [
                [], [], [], [],
                [{'id': 3, 'medication_name': '维生素D', 'dosage': '400IU',
                  'dose_quantity': '1', 'dose_unit': '粒', 'times_per_day': 1,
                  'timing_notes': '早餐后', 'frequency': 'weekly',
                  'frequency_detail': 'Monday', 'start_date': None,
                  'category': 'long_term', 'med_type': 'supplement'}],
                [], []
            ]
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db
            # 2024-06-03 is Monday → should include
            resp = client_authenticated.get('/api/day_overview?date=2024-06-03')
            assert resp.status_code == 200

    def test_med_biweekly(self, client_authenticated):
        with patch('routes.api_dashboard.get_db') as mock_get_db, \
             patch('routes.api_dashboard.settings') as mock_settings:
            mock_settings.get_badge_for_rate.return_value = {'key': 'good', 'icon': '👍'}
            mock_settings.get_bmi_category.return_value = {'label': '正常', 'color': '#4CAF50'}
            mock_settings.check_glucose_compliance.return_value = {'is_compliant': True, 'level': 'optimal'}
            mock_c = MagicMock()
            mock_c.fetchall.side_effect = [
                [], [], [], [],
                [{'id': 4, 'medication_name': '叶酸', 'dosage': '5mg',
                  'dose_quantity': '1', 'dose_unit': '片', 'times_per_day': 1,
                  'timing_notes': '早餐后', 'frequency': 'biweekly',
                  'frequency_detail': 'Monday', 'start_date': '2024-05-27',
                  'category': 'long_term', 'med_type': 'supplement'}],
                [], []
            ]
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db
            # 2024-06-03 is Monday, 1 week from 2024-05-27 → weeks_diff=1, 1%2=1 → False
            resp = client_authenticated.get('/api/day_overview?date=2024-06-03')
            assert resp.status_code == 200

    def test_med_monthly(self, client_authenticated):
        with patch('routes.api_dashboard.get_db') as mock_get_db, \
             patch('routes.api_dashboard.settings') as mock_settings:
            mock_settings.get_badge_for_rate.return_value = {'key': 'good', 'icon': '👍'}
            mock_settings.get_bmi_category.return_value = {'label': '正常', 'color': '#4CAF50'}
            mock_settings.check_glucose_compliance.return_value = {'is_compliant': True, 'level': 'optimal'}
            mock_c = MagicMock()
            mock_c.fetchall.side_effect = [
                [], [], [], [],
                [{'id': 5, 'medication_name': '骨化三醇', 'dosage': '0.25μg',
                  'dose_quantity': '1', 'dose_unit': '粒', 'times_per_day': 1,
                  'timing_notes': '早餐后', 'frequency': 'monthly',
                  'frequency_detail': '1,15', 'start_date': '2024-01-01',
                  'category': 'long_term', 'med_type': 'supplement'}],
                [], []
            ]
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db
            resp = client_authenticated.get('/api/day_overview?date=2024-06-01')
            assert resp.status_code == 200


# ============================================================
# api_prediction.py (93%)
# ============================================================

class TestPredictionQuotaExceeded:
    """trigger_prediction 配额超限"""

    def test_quota_exceeded_429(self, client_authenticated):
        """429 错误 -> 返回 retry_after"""
        with patch('routes.api_prediction.get_db') as mock_get_db:
            mock_get_db.side_effect = Exception("429 Too Many Requests, retry in 30")
            resp = client_authenticated.post('/trigger_prediction',
                data=json.dumps({'type': 'all'}),
                content_type='application/json')
            assert resp.status_code == 429

    def test_prediction_comparison_with_type_filter(self, client_authenticated):
        """prediction_comparison 类型过滤"""
        with patch('routes.api_prediction.get_db') as mock_get_db:
            mock_c = MagicMock()
            mock_c.fetchall.return_value = [
                ('空腹', '2024-06-01', 6.0, 6.5, 0.5),
            ]
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db
            resp = client_authenticated.get('/prediction_comparison?type=空腹&days=7')
            assert resp.status_code == 200
            data = resp.json
            assert data['type_filter'] == '空腹'
            assert len(data['data']) == 1


# ============================================================
# api_user.py — 6 个新增测试 (缺口 L57-58, L100-101, L115-116, L195-200, L238)
# ============================================================

class TestGetUsersException:
    """L57-58: get_users exception handler"""

    def test_get_users_exception(self, client_authenticated):
        """user_manager.get_all_users 抛出异常 -> 500"""
        with patch('routes.api_user.user_manager.get_all_users',
                   side_effect=Exception("db crash")):
            resp = client_authenticated.get('/get_users')
            assert resp.status_code == 500
            assert 'error' in resp.json


class TestUpdateSettingsException:
    """L100-101: update_settings exception handler"""

    def test_update_settings_exception(self, client_authenticated):
        """update_user_profile_partial 抛出异常 -> 500"""
        with patch('routes.api_user.user_manager.update_user_profile_partial',
                   side_effect=Exception("db crash")):
            resp = client_authenticated.post('/settings',
                data=json.dumps({'name': 'test'}),
                content_type='application/json')
            assert resp.status_code == 500
            assert resp.json['status'] == 'error'


class TestGetUserModulesException:
    """L115-116: get_user_modules exception handler"""

    def test_get_user_modules_exception(self, client_authenticated):
        """user_manager.get_user 抛出异常 -> 500"""
        with patch('routes.api_user.user_manager.get_user',
                   side_effect=Exception("db crash")):
            resp = client_authenticated.get('/api/user/modules')
            assert resp.status_code == 500
            assert resp.json['status'] == 'error'


class TestBindEmailAlreadyBound:
    """L238: bind_email 绑定已存在 -> 400"""

    def test_bind_email_already_bound(self, client_authenticated):
        """bind_provider 返回 {'ok': False} -> 400"""
        with patch('routes.api_user.user_manager.bind_provider',
                   return_value={'ok': False, 'message': '已被绑定'}):
            resp = client_authenticated.post('/bind_email',
                data=json.dumps({'email': 'test@example.com'}),
                content_type='application/json')
            assert resp.status_code == 400
            assert resp.json['error_type'] == 'validation_error'


class TestDeleteUserRealDb:
    """L195-200: delete_user 使用真实 DB — 自删 + 正常删除"""

    def test_delete_user_self(self, isolate_db, client_authenticated):
        """L195-196: 自删 -> 400 (使用真实 DB 验证路径)"""
        with client_authenticated.session_transaction() as sess:
            current_id = sess['current_user_id']
        resp = client_authenticated.post(f'/delete_user/{current_id}')
        assert resp.status_code == 400
        assert resp.json['error_type'] == 'validation_error'

    def test_delete_user_active(self, isolate_db, client_authenticated):
        """L195-200: 删除活跃用户 -> 200 + 软删除生效"""
        from core.config import DB_NAME
        # 创建第二个用户（非当前登录用户）
        conn = sqlite3.connect(DB_NAME)
        conn.execute("INSERT INTO app_users (id, username, display_name, is_active) VALUES (99, 'delete_me', 'To Delete', 1)")
        conn.commit()
        conn.close()

        resp = client_authenticated.post('/delete_user/99')
        assert resp.status_code == 200
        assert resp.json['status'] == 'success'

        # 验证软删除
        conn2 = sqlite3.connect(DB_NAME)
        row = conn2.execute("SELECT is_active FROM app_users WHERE id = 99").fetchone()
        assert row is not None
        assert row[0] == 0, "用户应被软删除 (is_active = 0)"
        conn2.close()


# ============================================================
# api_chat.py — build_chat_context 核心路径 (P0: ~40 行)
# ============================================================

class TestBuildChatContext:
    """build_chat_context 使用真实 SQLite 全覆盖"""

    PATCH_AVAIL = 'routes.api_chat.CHAT_AVAILABLE'

    def setup_method(self):
        self.today = datetime.datetime.now()
        self.today_str = self.today.strftime('%Y-%m-%d')

    def _setup_user(self, db_path, user_id=1):
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("INSERT OR IGNORE INTO app_users (id, username, display_name) VALUES (?, ?, ?)",
                  (user_id, 'test', '测试'))
        c.execute("INSERT OR IGNORE INTO user_profiles (user_id, birth_year, height, weight, gender) VALUES (?, 1964, 170, 75, 'male')",
                  (user_id,))
        conn.commit()
        return conn

    def _insert_record(self, db_path, user_id, record_type, value, timestamp,
                       systolic=None, diastolic=None, distance=None, calories=None,
                       weight=None, medication_name=None, notes=None):
        conn = sqlite3.connect(db_path)
        c = conn.cursor()
        c.execute(
            "INSERT INTO records (user_id, value, unit, type, notes, timestamp, is_predicted, "
            "systolic_pressure, diastolic_pressure, distance, calories, weight, medication_name) "
            "VALUES (?, ?, 'mmol/L', ?, ?, ?, 0, ?, ?, ?, ?, ?, ?)",
            (user_id, value, record_type, notes or '', timestamp,
             systolic, diastolic, distance, calories, weight, medication_name))
        conn.commit()
        conn.close()

    def _call_build_context(self, db_path, user_id=1):
        """调用 build_chat_context 并返回上下文文本"""
        from routes.api_chat import build_chat_context
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        result = build_chat_context(conn, user_id)
        conn.close()
        return result

    def test_no_records(self, isolate_db):
        """无任何记录 → 仅返回用户健康档案"""
        from core.config import DB_NAME
        self._setup_user(DB_NAME)
        ctx = self._call_build_context(DB_NAME)
        assert '【用户健康档案】' in ctx, "应包含用户健康档案"
        assert '今日' not in ctx, "无今日记录不应包含今日标题"
        assert '血糖均值' not in ctx, "无血糖记录不应包含血糖均值"

    def test_with_glucose_record(self, isolate_db):
        """今日血糖记录 → 含数值 + 血糖均值"""
        from core.config import DB_NAME
        self._setup_user(DB_NAME)
        # 今日血糖记录
        self._insert_record(DB_NAME, 1, '空腹', 6.5, f"{self.today_str} 07:15:00")
        # 近 7 天血糖记录
        past = (self.today - datetime.timedelta(days=2)).strftime('%Y-%m-%d')
        self._insert_record(DB_NAME, 1, '空腹', 6.0, f"{past} 07:15:00")
        self._insert_record(DB_NAME, 1, '空腹', 5.8, f"{past} 07:15:00")

        ctx = self._call_build_context(DB_NAME)
        assert '今日' in ctx, "应包含今日记录"
        assert '数值 6.5' in ctx, "应包含血糖数值"
        assert '血糖均值' in ctx, "应包含近7天血糖均值"

    def test_with_bp_record(self, isolate_db):
        """今日血压记录 → 含血压信息"""
        from core.config import DB_NAME
        self._setup_user(DB_NAME)
        self._insert_record(DB_NAME, 1, '血压测量', 0, f"{self.today_str} 07:15:00",
                           systolic=120, diastolic=80)

        ctx = self._call_build_context(DB_NAME)
        assert '今日' in ctx
        assert '血压' in ctx
        assert '120/80' in ctx

    def test_with_exercise_record(self, isolate_db):
        """今日运动记录 → 含距离 + 热量"""
        from core.config import DB_NAME
        self._setup_user(DB_NAME)
        self._insert_record(DB_NAME, 1, '跑步', 0, f"{self.today_str} 08:00:00",
                           distance=5.0, calories=350)

        ctx = self._call_build_context(DB_NAME)
        assert '今日' in ctx
        assert '距离' in ctx
        assert '5.00km' in ctx or '5.0km' in ctx
        assert '热量' in ctx

    def test_with_weight_medication_notes(self, isolate_db):
        """L73+L75+L77: 体重 + 用药 + 备注 三个分支"""
        from core.config import DB_NAME
        self._setup_user(DB_NAME)
        self._insert_record(DB_NAME, 1, '空腹', 6.5, f"{self.today_str} 07:15:00",
                           weight=72.5, medication_name='二甲双胍', notes='餐前服用')
        past = (self.today - datetime.timedelta(days=2)).strftime('%Y-%m-%d')
        self._insert_record(DB_NAME, 1, '空腹', 6.0, f"{past} 07:15:00")

        ctx = self._call_build_context(DB_NAME)
        assert '体重' in ctx
        assert '72.5kg' in ctx
        assert '用药' in ctx
        assert '二甲双胍' in ctx
        assert '备注' in ctx
        assert '餐前服用' in ctx


class TestDeleteUserException:
    """L198-200: delete_user exception handler"""

    def test_delete_user_exception(self, client_authenticated):
        """get_db 抛出异常 → except 捕获 → 500"""
        with patch('routes.api_user.get_db', side_effect=Exception("db crash")):
            resp = client_authenticated.post('/delete_user/999')
            assert resp.status_code == 500
            assert resp.json['error_type'] == 'user_error'

# ============================================================
# api_records.py — 深层业务逻辑分支 (~29 行)
# ============================================================

class TestRecordsDeepLogic:
    """深层业务逻辑: BMI 异常、timestamp T 格式、parse_ai 多用户"""

    def test_timestamp_t_format_without_seconds(self, client_authenticated):
        """L200-201: ISO 8601 'T' 格式, 16 位(缺秒) -> 补 ':00'"""
        with patch('routes.api_records.get_db') as mock_get_db:
            mock_c = MagicMock()
            mock_c.lastrowid = 100
            mock_c.fetchone.return_value = None  # no duplicate
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db
            resp = client_authenticated.post('/add',
                data=json.dumps({
                    'value': 6.5, 'type': '空腹',
                    'timestamp': '2024-06-01T07:15',
                }),
                content_type='application/json')
            assert resp.status_code == 200

    def test_form_value_none_returns_400(self, client_authenticated):
        """L191-192: form-data 中 value 为空 -> 400"""
        resp = client_authenticated.post('/add', data={'value': '', 'type': '空腹'})
        assert resp.status_code == 400

    def test_parse_ai_prediction_match(self, client_authenticated):
        """L355-356: parse_ai 发现现有预测记录 -> 填充 predicted_value"""
        with patch('routes.api_records.get_db') as mock_get_db, \
             patch('routes.api_records.parse_glucose_input') as mock_parse, \
             patch('routes.api_records.get_user_stats') as mock_stats:
            mock_parse.return_value = [{
                'value': 6.5, 'type': '空腹', 'datetime': '2024-06-01 07:15:00'
            }]
            mock_stats.return_value = {}
            mock_c = MagicMock()
            mock_c.fetchone.return_value = (6.0,)  # existing_prediction found
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db
            resp = client_authenticated.post('/parse_ai',
                data=json.dumps({'text': '空腹 6.5'}),
                content_type='application/json')
            assert resp.status_code == 200

    def test_parse_ai_prediction_match_exception(self, client_authenticated):
        """L359-360: parse_ai 预测匹配异常 -> 打印日志"""
        with patch('routes.api_records.get_db') as mock_get_db, \
             patch('routes.api_records.parse_glucose_input') as mock_parse, \
             patch('routes.api_records.get_user_stats') as mock_stats:
            mock_parse.return_value = [{'value': 6.5, 'datetime': 'bad-format!'}]
            mock_stats.return_value = {}
            mock_c = MagicMock()
            mock_c.fetchone.side_effect = Exception("parse error")
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db
            resp = client_authenticated.post('/parse_ai',
                data=json.dumps({'text': '空腹 6.5'}),
                content_type='application/json')
            # Exception caught, results still returned
            assert resp.status_code == 200

    def test_parse_ai_multi_user_with_emoji(self, client_authenticated):
        """L310+ : emoji 检测 -> split_by_emoji -> 多用户路径"""
        with patch('routes.api_records.get_db') as mock_get_db, \
             patch('routes.api_records.settings.EMOJI_USER_MAP', {'🐯': 6, '🐰': 1}), \
             patch('routes.api_records.split_by_emoji') as mock_split, \
             patch('routes.api_records.parse_glucose_input') as mock_parse, \
             patch('routes.api_records.get_user_stats') as mock_stats:
            mock_split.return_value = [
                {'user_id': 6, 'text': '空腹 5.5'},
                {'user_id': 1, 'text': '餐后 7.0'},
            ]
            mock_parse.return_value = [{'value': 5.5, 'type': '空腹', 'datetime': '2024-06-01 07:15:00'}]
            mock_stats.return_value = {}
            mock_c = MagicMock()
            mock_c.fetchone.return_value = None
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db
            resp = client_authenticated.post('/parse_ai',
                data=json.dumps({'text': '🐯空腹 5.5 🐰餐后 7.0'}),
                content_type='application/json')
            assert resp.status_code == 200


class TestRecordsBatchAddDeep:
    """batch_add 深层分支"""

    def test_batch_add_record_missing_fields_continue(self, client_authenticated):
        """L387+L432: 记录缺少 value/type -> continue"""
        with patch('routes.api_records.get_db') as mock_get_db:
            mock_c = MagicMock()
            mock_c.fetchone.return_value = None
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db
            resp = client_authenticated.post('/batch_add',
                data=json.dumps({
                    'records': [{'type': '空腹'}],  # missing value
                }),
                content_type='application/json')
            assert resp.status_code == 200

    def test_batch_add_bmi_calc_exception(self, client_authenticated):
        """L460-461: BMI 计算异常 -> except pass"""
        with patch('routes.api_records.get_db') as mock_get_db, \
             patch('routes.api_records.settings.calculate_bmi', side_effect=ValueError("invalid")):
            mock_c = MagicMock()
            mock_c.fetchone.return_value = None
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db
            resp = client_authenticated.post('/batch_add',
                data=json.dumps({
                    'records': [{
                        'value': 6.5, 'type': '空腹', 'datetime': '2024-06-01 07:15:00',
                        'weight': 70,
                    }],
                }),
                content_type='application/json')
            assert resp.status_code == 200

    def test_batch_add_conflict_ask(self, client_authenticated):
        """冲突检测 -> ask 模式 -> 返回冲突信息"""
        with patch('routes.api_records.get_db') as mock_get_db:
            mock_c = MagicMock()
            mock_c.fetchone.return_value = {'id': 1, 'value': 6.5, 'type': '空腹',
                                            'timestamp': '2024-06-01 07:15:00', 'notes': ''}
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db
            resp = client_authenticated.post('/batch_add',
                data=json.dumps({
                    'records': [{
                        'value': 6.5, 'type': '空腹', 'datetime': '2024-06-01 07:15:00',
                    }],
                    'conflict_resolution': 'ask',
                }),
                content_type='application/json')
            assert resp.status_code == 200
            data = resp.json
            assert data['status'] == 'conflict'

    def test_batch_add_overwrite(self, client_authenticated):
        """冲突 resolution=overwrite -> 覆盖旧记录"""
        with patch('routes.api_records.get_db') as mock_get_db:
            mock_c = MagicMock()
            mock_c.fetchone.side_effect = [None, {'id': 1, 'value': 6.5, 'type': '空腹',
                                                  'timestamp': '2024-06-01 07:15:00', 'notes': ''}]
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db
            resp = client_authenticated.post('/batch_add',
                data=json.dumps({
                    'records': [{
                        'value': 6.5, 'type': '空腹', 'datetime': '2024-06-01 07:15:00',
                    }],
                    'conflict_resolution': 'overwrite',
                }),
                content_type='application/json')
            assert resp.status_code == 200

    def test_batch_add_skip(self, client_authenticated):
        """冲突 resolution=skip -> 跳过现有记录"""
        with patch('routes.api_records.get_db') as mock_get_db:
            mock_c = MagicMock()
            mock_c.fetchone.side_effect = [None, {'id': 1, 'value': 6.5, 'type': '空腹',
                                                  'timestamp': '2024-06-01 07:15:00', 'notes': ''}]
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db
            resp = client_authenticated.post('/batch_add',
                data=json.dumps({
                    'records': [{
                        'value': 6.5, 'type': '空腹', 'datetime': '2024-06-01 07:15:00',
                    }],
                    'conflict_resolution': 'skip',
                }),
                content_type='application/json')
            assert resp.status_code == 200


class TestRecordsImportCsv:
    """L561: import_csv No file 分支"""

    def test_import_no_file(self, client_authenticated):
        """POST /import 无文件 -> 400"""
        resp = client_authenticated.post('/import', data={})
        assert resp.status_code == 400
        assert resp.json['status'] == 'error'
        assert 'No file' in resp.json.get('message', '')


class TestRecordsEmptyGlucose:
    """value=0 时无血糖校验警告"""

    def test_validate_empty_glucose_no_warning(self):
        """value 为 0 且非血压/体重 -> 无血糖校验"""
        from routes.api_records import _validate_record_data
        warns = _validate_record_data({'value': 0, 'type': '运动'})
        assert warns == []


# ============================================================
# api_dashboard.py — 深层业务逻辑分支 (~20 行)
# ============================================================

class TestDashboardTimelineExcept:
    """api_timeline exception 处理器"""

    def test_timeline_exception_returns_500(self, client_authenticated):
        """L28-30: timeline get_db 异常 -> 500"""
        with patch('routes.api_dashboard.get_db') as mock_get_db:
            mock_get_db.side_effect = Exception("timeline crash")
            resp = client_authenticated.get('/api/timeline')
            assert resp.status_code == 500


class TestDashboardDayOverviewExcept:
    """api_day_overview exception 处理器"""

    def test_day_overview_exception_returns_500(self, client_authenticated):
        """L217-219: day_overview get_db 异常 -> 500"""
        with patch('routes.api_dashboard.get_db') as mock_get_db:
            mock_get_db.side_effect = Exception("overview crash")
            resp = client_authenticated.get('/api/day_overview?date=2024-06-01')
            assert resp.status_code == 500


class TestDashboardSlotMatchingDeep:
    """day_overview slot 匹配深层分支"""

    def test_slot_hour_parse_exception(self, client_authenticated):
        """L271-272: 时间小时解析异常 -> rh=-1 (时间部分含冒号但小时非数字)"""
        with patch('routes.api_dashboard.get_db') as mock_get_db, \
             patch('routes.api_dashboard.settings') as mock_settings:
            mock_settings.get_badge_for_rate.return_value = {'key': 'good', 'icon': '👍'}
            mock_settings.get_bmi_category.return_value = {'label': '正常', 'color': '#4CAF50'}
            mock_settings.check_glucose_compliance.return_value = {'is_compliant': True, 'level': 'optimal'}
            mock_c = MagicMock()
            mock_c.fetchall.side_effect = [
                [{'value': 6.0, 'type': '空腹', 'timestamp': '2024-06-01 bad:00', 'is_predicted': 0}],
                [], [], [], [], [{'plan_id': 1, 'count': 1}], []
            ]
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db
            resp = client_authenticated.get('/api/day_overview?date=2024-06-01')
            assert resp.status_code == 200

    def test_cgm_not_type_continue(self, client_authenticated):
        """L306: CGM 循环中非 CGM 类型 -> continue"""
        with patch('routes.api_dashboard.get_db') as mock_get_db, \
             patch('routes.api_dashboard.settings') as mock_settings:
            mock_settings.get_badge_for_rate.return_value = {'key': 'good', 'icon': '👍'}
            mock_settings.get_bmi_category.return_value = {'label': '正常', 'color': '#4CAF50'}
            mock_settings.check_glucose_compliance.return_value = {'is_compliant': True, 'level': 'optimal'}
            mock_c = MagicMock()
            # records: 空腹 + CGM 都在, CGM 排第二
            mock_c.fetchall.side_effect = [
                [
                    {'value': 6.0, 'type': '空腹',   'timestamp': '2024-06-01 07:15:00', 'is_predicted': 0},
                    {'value': 5.8, 'type': 'CGM',     'timestamp': '2024-06-01 07:20:00', 'is_predicted': 0},
                ],
                [], [], [], [], [{'plan_id': 1, 'count': 1}], []
            ]
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db
            resp = client_authenticated.get('/api/day_overview?date=2024-06-01')
            assert resp.status_code == 200
            fasting = [s for s in resp.json['overview'] if s['key'] == 'fasting'][0]
            # CGM should have matched the fasting slot (5.8 at 07:20, 5 min from 07:15)
            assert fasting.get('cgm') is True
            assert fasting['value'] == 5.8

    def test_cgm_time_parse_exception(self, client_authenticated):
        """L310-311: CGM 时间解析异常 -> continue"""
        with patch('routes.api_dashboard.get_db') as mock_get_db, \
             patch('routes.api_dashboard.settings') as mock_settings:
            mock_settings.get_badge_for_rate.return_value = {'key': 'good', 'icon': '👍'}
            mock_settings.get_bmi_category.return_value = {'label': '正常', 'color': '#4CAF50'}
            mock_settings.check_glucose_compliance.return_value = {'is_compliant': True, 'level': 'optimal'}
            mock_c = MagicMock()
            mock_c.fetchall.side_effect = [
                [
                    {'value': 6.0, 'type': '空腹', 'timestamp': '2024-06-01 07:15:00', 'is_predicted': 0},
                    {'value': 5.8, 'type': 'CGM',  'timestamp': '2024-06-01 ab:cd', 'is_predicted': 0},
                    {'value': 6.2, 'type': 'CGM',  'timestamp': '2024-06-01 07:20:00', 'is_predicted': 0},
                ],
                [], [], [], [], [{'plan_id': 1, 'count': 1}], []
            ]
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db
            resp = client_authenticated.get('/api/day_overview?date=2024-06-01')
            assert resp.status_code == 200
            # The bad CGM should be skipped, good CGM should match
            fasting = [s for s in resp.json['overview'] if s['key'] == 'fasting'][0]
            assert fasting.get('cgm') is True
            assert fasting['value'] == 6.2
"""api_admin.py 深层未覆盖分支测试

覆盖目标（根据实际行号）:
  - L38-39:  backup cleanup() 内部 except Exception: pass
  - L48-50:  backup_database 外层 except → 500
  - L57-58:  restore_database 无文件 → 400
  - L91-96:  restore_database 外层 except → 500
"""



class TestAdminBackupDeep:
    """backup_database 深层异常分支"""

    def test_backup_cleanup_remove_fails(self, client_authenticated):
        """L38-39: backup cleanup 中 os.remove 失败 -> except pass"""
        with patch('routes.api_admin.os.path.exists', return_value=True), \
             patch('routes.api_admin.shutil.copy2'), \
             patch('routes.api_admin.os.remove', side_effect=OSError("permission denied")), \
             patch('routes.api_admin.send_file', return_value=('ok', 200)):
            resp = client_authenticated.get('/backup_database')
            # 消费响应体以触发 @after_this_request 钩子
            resp.get_data()
            assert resp.status_code == 200

    def test_backup_database_exception(self, client_authenticated):
        """L48-50: backup_database 内部异常 -> 500"""
        with patch('routes.api_admin.os.path.exists', return_value=True), \
             patch('routes.api_admin.shutil.copy2', side_effect=PermissionError("no write access")):
            resp = client_authenticated.get('/backup_database')
            assert resp.status_code == 500


class TestAdminRestoreDeep:
    """restore_database 深层分支"""

    def test_restore_no_file(self, client_authenticated):
        """L57-58: 没有上传文件 -> 400"""
        resp = client_authenticated.post('/restore_database', data={})
        assert resp.status_code == 400
        data = resp.get_json()
        assert data is not None
        assert 'message' in data

    def test_restore_move_fails(self, client_authenticated):
        """L91-96: restore 中 shutil.move 失败 -> 外层 except 500"""
        from io import BytesIO
        data = {'file': (BytesIO(b'valid-db-content'), 'restore.db')}

        with patch('routes.api_admin.os.path.getsize', return_value=100), \
             patch('routes.api_admin.sqlite3.connect') as mock_connect, \
             patch('routes.api_admin.shutil.move', side_effect=OSError("move failed")), \
             patch('routes.api_admin.os.path.exists') as mock_exists, \
             patch('routes.api_admin.os.remove'):  # except handler 中的清理
            mock_exists.return_value = True

            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.fetchone.return_value = (1,)
            mock_conn.cursor.return_value = mock_cursor
            mock_connect.return_value = mock_conn

            resp = client_authenticated.post('/restore_database',
                data=data, content_type='multipart/form-data')
            assert resp.status_code == 500
            assert resp.json['message'] is not None


# ============================================================
# api_auth.py (74% → 100%) — 21 条缺口
#   L35-37: email 登录路径
#   L48-50: 登录成功 session 设置 + 重定向
#   L90-93: change_password 用户不存在
#   L95-103: change_password 旧密码校验
#   L106-107: change_password 成功
# ============================================================

class TestAuthLoginEmail:
    """L35-37: 邮箱登录路径"""

    def test_login_with_email(self, client):
        """邮箱格式登录 -> find_user_by_provider('email')"""
        with patch('routes.api_auth.user_manager.find_user_by_provider') as mock_find, \
             patch('routes.api_auth.user_manager.get_user_by_username_or_id') as mock_get:
            mock_find.return_value = 1
            mock_get.return_value = None
            resp = client.post('/login', data={'username': 'test@example.com', 'password': 'pass'})
            assert resp.status_code == 200
            mock_find.assert_called_with('email', 'test@example.com')


class TestAuthLoginSuccess:
    """L48-50: 登录成功 -> session 设置 + 重定向"""

    def test_login_success(self, client):
        """正确密码 -> session 设值 + redirect"""
        from werkzeug.security import generate_password_hash
        pw_hash = generate_password_hash('正确的密码')
        with patch('routes.api_auth.user_manager.get_user_by_username') as mock_gubu:
            mock_gubu.return_value = {'id': 42, 'username': 'test', 'password_hash': pw_hash}
            resp = client.post('/login', data={'username': 'test', 'password': '正确的密码'},
                              follow_redirects=False)
            assert resp.status_code == 302
            with client.session_transaction() as sess:
                assert sess['current_user_id'] == 42
                assert sess['username'] == 'test'


class TestAuthChangePassword:
    """change_password 各分支 (L90-107)"""

    def test_change_password_user_not_found(self, client_authenticated):
        """L90-93: 用户不存在 -> 404"""
        with patch('routes.api_auth.user_manager.get_user', return_value=None):
            resp = client_authenticated.post('/change_password',
                data=json.dumps({'old_password': '', 'new_password': 'newpass123'}),
                content_type='application/json')
            assert resp.status_code == 404

    def test_change_password_wrong_old(self, client_authenticated):
        """L95-103: has_password=True + 旧密码错误 -> 400"""
        mock_row = MagicMock()
        mock_row.__getitem__.return_value = '_test'
        mock_c = MagicMock()
        mock_c.fetchone.return_value = mock_row
        mock_db = MagicMock()
        mock_db.cursor.return_value = mock_c
        with patch('routes.api_auth.user_manager.has_password', return_value=True), \
             patch('routes.api_auth.user_manager.get_user', return_value={'id': 1, 'username': '_test'}), \
             patch('routes.api_auth.user_manager.authenticate', return_value=False), \
             patch('routes.api_auth.get_db', return_value=mock_db):
            resp = client_authenticated.post('/change_password',
                data=json.dumps({'old_password': 'wrong', 'new_password': 'newpass123'}),
                content_type='application/json')
            assert resp.status_code == 400
            assert resp.json['error_type'] == 'auth_error'

    def test_change_password_success_with_password(self, client_authenticated):
        """L106-107: has_password=True + 旧密码正确 -> 200"""
        mock_row = MagicMock()
        mock_row.__getitem__.return_value = '_test'
        mock_c = MagicMock()
        mock_c.fetchone.return_value = mock_row
        mock_db = MagicMock()
        mock_db.cursor.return_value = mock_c
        with patch('routes.api_auth.user_manager.has_password', return_value=True), \
             patch('routes.api_auth.user_manager.get_user', return_value={'id': 1, 'username': '_test'}), \
             patch('routes.api_auth.user_manager.authenticate', return_value=True), \
             patch('routes.api_auth.user_manager.set_password'), \
             patch('routes.api_auth.get_db', return_value=mock_db):
            resp = client_authenticated.post('/change_password',
                data=json.dumps({'old_password': 'correct', 'new_password': 'newpass123'}),
                content_type='application/json')
            assert resp.status_code == 200

    def test_change_password_success_no_password(self, client_authenticated):
        """L106-107: has_password=False -> 直接设置新密码 -> 200"""
        with patch('routes.api_auth.user_manager.has_password', return_value=False), \
             patch('routes.api_auth.user_manager.get_user', return_value={'id': 1, 'username': '_test'}), \
             patch('routes.api_auth.user_manager.set_password'):
            resp = client_authenticated.post('/change_password',
                data=json.dumps({'old_password': '', 'new_password': 'newpass123'}),
                content_type='application/json')
            assert resp.status_code == 200


# ============================================================
# api_health.py (62% → 100%) — 23 条缺口
#   L18-24: analyze_health 成功路径
#   L25-26: analyze_health 失败路径
#   L27-29: analyze_health except 处理器
#   L44-53: get_latest_analysis 有数据
#   L52:    recommendations 解析异常 -> pass
#   L67-68: get_health_analyses except 处理器
# ============================================================

class TestHealthAnalyze:
    """analyze_health 全路径 (L18-29)"""

    def test_analyze_health_success(self, client_authenticated):
        """L18-24: generate_health_analysis 成功 -> 200 + analysis_id"""
        with patch('routes.api_health.get_db') as mock_get_db, \
             patch('routes.api_health.generate_health_analysis') as mock_gen:
            mock_gen.return_value = {
                'success': True,
                'analysis_id': 123,
                'result': {'summary': '一切正常'}
            }
            mock_db = MagicMock()
            mock_get_db.return_value = mock_db
            resp = client_authenticated.post('/analyze_health',
                data=json.dumps({'days': 7}),
                content_type='application/json')
            assert resp.status_code == 200
            data = resp.json
            assert data['status'] == 'success'
            assert data['data']['analysis_id'] == 123

    def test_analyze_health_failure(self, client_authenticated):
        """L25-26: generate_health_analysis 返回错误 -> 500"""
        with patch('routes.api_health.get_db') as mock_get_db, \
             patch('routes.api_health.generate_health_analysis') as mock_gen:
            mock_gen.return_value = {
                'success': False,
                'error': '数据库异常'
            }
            mock_db = MagicMock()
            mock_get_db.return_value = mock_db
            resp = client_authenticated.post('/analyze_health',
                data=json.dumps({'days': 7}),
                content_type='application/json')
            assert resp.status_code == 500
            assert resp.json['status'] == 'error'

    def test_analyze_health_exception(self, client_authenticated):
        """L27-29: get_db 抛出异常 -> 500"""
        with patch('routes.api_health.get_db', side_effect=Exception("db crash")):
            resp = client_authenticated.post('/analyze_health',
                data=json.dumps({'days': 7}),
                content_type='application/json')
            assert resp.status_code == 500
            assert resp.json['status'] == 'error'

    def test_analyze_health_skipped(self, client_authenticated):
        """今日已生成分析 -> 200"""
        with patch('routes.api_health.get_db') as mock_get_db, \
             patch('routes.api_health.generate_health_analysis') as mock_gen:
            mock_gen.return_value = {'skipped': True, 'message': '今日已生成分析'}
            mock_db = MagicMock()
            mock_get_db.return_value = mock_db
            resp = client_authenticated.post('/analyze_health',
                data=json.dumps({'days': 7}), content_type='application/json')
            assert resp.status_code == 200
            assert resp.json['message'] == '今日已生成分析'

    def test_analyze_health_quota_exception(self, client_authenticated):
        """AI 配额用尽 -> 429"""
        with patch('routes.api_health.get_db',
                   side_effect=Exception("429 quota exceeded")):
            resp = client_authenticated.post('/analyze_health',
                data=json.dumps({'days': 7}), content_type='application/json')
            assert resp.status_code == 429
            assert resp.json['status'] == 'error'


class TestHealthLatestAnalysis:
    """get_latest_analysis 全路径 (L44-53)"""

    def test_get_latest_analysis_exception(self, client_authenticated):
        """L52-53: get_db 异常 -> 500"""
        with patch('routes.api_health.get_db', side_effect=Exception("db crash")):
            resp = client_authenticated.get('/get_latest_analysis')
            assert resp.status_code == 500
            assert 'error' in resp.json


    def test_get_latest_analysis_with_data(self, client_authenticated):
        """L44-53: 有分析记录 -> 返回 dict"""
        with patch('routes.api_health.get_db') as mock_get_db:
            mock_row = MagicMock()
            # dict(row) 支持
            row_data = {
                'id': 1, 'user_id': 1, 'score': 85,
                'summary': '良好', 'recommendations': '["多运动"]',
                'created_at': '2024-06-01 07:00:00'
            }
            mock_row.keys.return_value = list(row_data.keys())
            mock_row.__getitem__.side_effect = \
                lambda k: row_data.get(k) if isinstance(k, str) else None
            mock_c = MagicMock()
            mock_c.fetchone.return_value = mock_row
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db
            resp = client_authenticated.get('/get_latest_analysis')
            assert resp.status_code == 200
            data = resp.json
            assert data['id'] == 1
            assert data['score'] == 85

    def test_get_latest_analysis_recommendations_bad_json(self, client_authenticated):
        """L52: recommendations 非 JSON -> except pass"""
        with patch('routes.api_health.get_db') as mock_get_db:
            row_data = {
                'id': 1, 'user_id': 1, 'score': 80,
                'summary': '尚可', 'recommendations': 'bad json string',
                'created_at': '2024-06-01 07:00:00'
            }
            mock_row = MagicMock()
            mock_row.keys.return_value = list(row_data.keys())
            mock_row.__getitem__.side_effect = \
                lambda k: row_data.get(k) if isinstance(k, str) else None
            mock_c = MagicMock()
            mock_c.fetchone.return_value = mock_row
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db
            resp = client_authenticated.get('/get_latest_analysis')
            assert resp.status_code == 200
            # recommendations 非 JSON -> except pass, 保留原始字符串
            assert resp.json['recommendations'] == 'bad json string'

    def test_get_latest_analysis_no_data(self, client_authenticated):
        """L54: 无分析记录 -> 404"""
        with patch('routes.api_health.get_db') as mock_get_db:
            mock_c = MagicMock()
            mock_c.fetchone.return_value = None
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db
            resp = client_authenticated.get('/get_latest_analysis')
            assert resp.status_code == 404


class TestHealthAnalysesExcept:
    """L67-68: get_health_analyses except 处理器"""

    def test_health_analyses_exception(self, client_authenticated):
        """get_db 异常 -> 500"""
        with patch('routes.api_health.get_db', side_effect=Exception("db crash")):
            resp = client_authenticated.get('/health_analyses')
            assert resp.status_code == 500
            assert 'error' in resp.json
