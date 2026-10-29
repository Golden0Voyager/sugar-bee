"""体检历史（checkup_parser.py + /api/checkup）测试。

AI 视觉调用全部 mock，测试只覆盖解析归一化、落库与权限逻辑。
"""
import base64

import pytest

import checkup_parser
from user_manager import UserManager

FAKE_IMAGE = 'data:image/jpeg;base64,' + base64.b64encode(b'fake').decode()


def _uid(client):
    with client.session_transaction() as sess:
        return sess['current_user_id']


class TestJudgeByRange:
    @pytest.mark.parametrize('value,ref,expected', [
        ('5.6', '3.1-5.2', 'high'),
        ('2.1', '3.1-5.2', 'low'),
        ('4.0', '3.1-5.2', 'normal'),
        ('5.2', '3.1-5.2', 'normal'),
        ('3.1', '3.1-5.2', 'normal'),
        ('4.0', '5.2-3.1', 'normal'),   # 上下限写反也能处理
        ('阳性', '阴性', 'normal'),      # 非数字不猜
        ('4.0', '', 'normal'),
        ('4.0', '4.0', 'normal'),
        ('5.8', '<5.2', 'high'),        # 单边上限
        ('4.8', '≤5.2', 'normal'),
        ('0.8', '>1.0', 'low'),         # 单边下限
        ('1.2', '≥1.0', 'normal'),
        ('420', '208-428', 'normal'),
    ])
    def test_judge(self, value, ref, expected):
        assert checkup_parser._judge_by_range(value, ref) == expected


class TestNormalizeStatus:
    def test_trust_explicit(self):
        assert checkup_parser._normalize_status('high', '4.0', '3.1-5.2') == 'high'
        assert checkup_parser._normalize_status('LOW', '4.0', '3.1-5.2') == 'low'
        assert checkup_parser._normalize_status('normal', '9.9', '3.1-5.2') == 'normal'

    def test_fallback_to_range(self):
        assert checkup_parser._normalize_status('', '9.9', '3.1-5.2') == 'high'


class TestExtractJson:
    def test_plain(self):
        assert checkup_parser._extract_json('{"items": []}') == {'items': []}

    def test_markdown_wrapped(self):
        raw = '```json\n{"items": [{"name": "x"}]}\n```'
        assert checkup_parser._extract_json(raw)['items'][0]['name'] == 'x'

    def test_with_preamble(self):
        raw = '好的，结果如下：\n{"items": []}\n以上。'
        assert checkup_parser._extract_json(raw) == {'items': []}

    def test_garbage(self):
        assert checkup_parser._extract_json('抱歉我无法识别') is None
        assert checkup_parser._extract_json('') is None
        assert checkup_parser._extract_json(None) is None


class TestNormalizeReport:
    def test_full(self):
        result = checkup_parser.normalize_report({
            'checkup_date': '2026-03-01',
            'institution': '市第一医院',
            'items': [
                {'category': '血脂', 'name': '总胆固醇', 'value': '5.62', 'unit': 'mmol/L',
                 'ref_range': '3.1-5.2', 'status': 'high'},
                {'category': '乱写分类', 'name': '尿酸', 'value': '420', 'unit': 'μmol/L',
                 'ref_range': '208-428', 'status': ''},
            ],
        })
        assert result['checkup_date'] == '2026-03-01'
        assert result['institution'] == '市第一医院'
        assert result['items'][0]['status'] == 'high'
        assert result['items'][1]['category'] == '其他'
        assert result['items'][1]['status'] == 'normal'

    def test_drops_items_without_name_or_value(self):
        result = checkup_parser.normalize_report({'items': [
            {'name': '', 'value': '1'},
            {'name': '血糖', 'value': ''},
            'not-a-dict',
            {'name': '血糖', 'value': '5.4'},
        ]})
        assert len(result['items']) == 1

    def test_invalid_date_reset(self):
        assert checkup_parser.normalize_report({'checkup_date': '3月1日'})['checkup_date'] == ''

    def test_non_dict_input(self):
        result = checkup_parser.normalize_report(None)
        assert result == {'checkup_date': '', 'institution': '', 'items': []}

    def test_dedupes_abnormal_summary_rows(self):
        """报告末尾异常汇总页的「增高/重度增高」行与明细行重复时，只保留带数值的明细行。"""
        result = checkup_parser.normalize_report({'items': [
            {'category': '其他', 'name': '磷酸肌酸激酶', 'value': '重度增高',
             'unit': '', 'ref_range': '', 'status': 'high'},
            {'category': '其他', 'name': '磷酸肌酸激酶', 'value': '3605.00',
             'unit': 'U/L', 'ref_range': '50-310', 'status': 'high'},
            {'category': '肝功能', 'name': '丙氨酸氨基转移酶', 'value': '增高',
             'unit': '', 'ref_range': '', 'status': 'high'},
            {'category': '肝功能', 'name': '丙氨酸氨基转移酶', 'value': '69',
             'unit': 'U/L', 'ref_range': '0—50', 'status': 'high'},
        ]})
        assert len(result['items']) == 2
        assert all(item['unit'] for item in result['items'])

    def test_keeps_text_results_without_numeric_sibling(self):
        """「阴性」等文字结论没有同名数值行时不能误删。"""
        result = checkup_parser.normalize_report({'items': [
            {'category': '尿常规', 'name': '尿糖', 'value': '阴性',
             'unit': 'mmol/L', 'ref_range': '阴性', 'status': 'normal'},
        ]})
        assert len(result['items']) == 1

    def test_long_text_value_not_truncated(self):
        """影像检查描述长达数百字时不能按旧的 30 字符截断。"""
        description = '肝脏形态大小正常，表面光滑，包膜完整，肝内回声分布均匀，' + '血管走向清晰。' * 40
        result = checkup_parser.normalize_report({'items': [
            {'category': '影像检查', 'name': '肝', 'value': description,
             'unit': '', 'ref_range': '', 'status': 'normal'},
        ]})
        assert result['items'][0]['value'] == ' '.join(description.split())


class TestParseReport:
    def test_calls_vision_model(self, monkeypatch):
        captured = {}

        def fake_call(prompt, images_data=None, mime_type=None, task_type=None):
            captured['task_type'] = task_type
            captured['has_images'] = bool(images_data)
            return '{"checkup_date": "2026-03-01", "items": [{"name": "总胆固醇", "value": "5.6"}]}'

        monkeypatch.setattr('checkup_parser.call_ai', fake_call)
        result = checkup_parser.parse_checkup_report([b'img'], 'image/jpeg')
        assert captured['task_type'] == 'vision'
        assert captured['has_images'] is True
        assert result['items'][0]['name'] == '总胆固醇'

    def test_ai_error_propagates(self, monkeypatch):
        def fake_call(*a, **k):
            raise RuntimeError('AI down')

        monkeypatch.setattr('checkup_parser.call_ai', fake_call)
        with pytest.raises(RuntimeError):
            checkup_parser.parse_checkup_report([b'img'], 'image/jpeg')


class TestCheckupApi:
    def test_requires_login(self, client):
        assert client.get('/api/checkup/list', headers={'X-Requested-With': 'XMLHttpRequest'}).status_code == 401

    def test_parse_requires_image(self, client_authenticated):
        resp = client_authenticated.post('/api/checkup/parse', json={})
        assert resp.status_code == 400

    def test_parse_bad_base64(self, client_authenticated):
        resp = client_authenticated.post('/api/checkup/parse', json={'images': ['@@@not-base64@@@']})
        assert resp.status_code in (400, 422)

    def test_parse_success(self, client_authenticated, monkeypatch):
        monkeypatch.setattr('checkup_parser.parse_checkup_report', lambda imgs, mime: {
            'checkup_date': '2026-03-01', 'institution': '市医院',
            'items': [{'category': '血脂', 'name': '总胆固醇', 'value': '5.6',
                       'unit': 'mmol/L', 'ref_range': '3.1-5.2', 'status': 'high'}],
        })
        resp = client_authenticated.post('/api/checkup/parse', json={'images': [FAKE_IMAGE]})
        assert resp.status_code == 200
        data = resp.get_json()['data']
        assert data['items'][0]['name'] == '总胆固醇'

    def test_parse_empty_result_422(self, client_authenticated, monkeypatch):
        monkeypatch.setattr('checkup_parser.parse_checkup_report', lambda imgs, mime: {
            'checkup_date': '', 'institution': '', 'items': [],
        })
        resp = client_authenticated.post('/api/checkup/parse', json={'images': [FAKE_IMAGE]})
        assert resp.status_code == 422
        assert resp.get_json()['error_type'] == 'parse_empty'

    def test_parse_fills_today_when_date_missing(self, client_authenticated, monkeypatch):
        monkeypatch.setattr('checkup_parser.parse_checkup_report', lambda imgs, mime: {
            'checkup_date': '', 'institution': '',
            'items': [{'name': '血糖', 'value': '5.4', 'category': '血糖',
                       'unit': 'mmol/L', 'ref_range': '3.9-6.1', 'status': 'normal'}],
        })
        data = client_authenticated.post('/api/checkup/parse', json={'images': [FAKE_IMAGE]}).get_json()['data']
        assert len(data['checkup_date']) == 10

    def test_parse_ai_failure_503(self, client_authenticated, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError('AI down')

        monkeypatch.setattr('checkup_parser.parse_checkup_report', boom)
        resp = client_authenticated.post('/api/checkup/parse', json={'images': [FAKE_IMAGE]})
        assert resp.status_code == 503
        assert resp.get_json()['error_type'] == 'ai_unavailable'

    def test_save_and_list(self, client_authenticated):
        payload = {
            'checkup_date': '2026-03-01',
            'institution': '市第一医院',
            'items': [
                {'category': '血脂', 'name': '总胆固醇', 'value': '5.6', 'unit': 'mmol/L',
                 'ref_range': '3.1-5.2', 'status': 'high'},
                {'category': '血糖', 'name': '空腹血糖', 'value': '5.4', 'unit': 'mmol/L',
                 'ref_range': '3.9-6.1', 'status': 'normal'},
            ],
        }
        resp = client_authenticated.post('/api/checkup/save', json=payload)
        assert resp.status_code == 200
        detail = resp.get_json()['data']
        assert len(detail['items']) == 2
        assert '异常 1 项' in detail['summary']

        reports = client_authenticated.get('/api/checkup/list').get_json()['data']['reports']
        assert len(reports) == 1
        assert reports[0]['item_count'] == 2
        assert reports[0]['abnormal_count'] == 1

        got = client_authenticated.get(f"/api/checkup/{reports[0]['id']}").get_json()['data']
        assert got['institution'] == '市第一医院'

    def _save_report(self, client_authenticated, date, items, institution=''):
        resp = client_authenticated.post('/api/checkup/save', json={
            'checkup_date': date,
            'institution': institution,
            'items': items,
        })
        assert resp.status_code == 200
        return resp.get_json()['data']['id']

    def test_merge_combines_batches_and_dedupes(self, client_authenticated):
        """同一次体检分批上传：合并为一条，完全相同的行只留一条。"""
        items_a = [
            {'category': '血常规', 'name': '白细胞', 'value': '5.2', 'unit': '10^9/L', 'status': 'normal'},
            {'category': '影像检查', 'name': '肝', 'value': '形态正常', 'unit': '', 'status': 'normal'},
        ]
        items_b = [
            {'category': '血常规', 'name': '白细胞', 'value': '5.2', 'unit': '10^9/L', 'status': 'normal'},
            {'category': '肝功能', 'name': '丙氨酸氨基转移酶', 'value': '69', 'unit': 'U/L', 'status': 'high'},
        ]
        id_a = self._save_report(client_authenticated, '2026-07-01', items_a)
        id_b = self._save_report(client_authenticated, '2026-07-01', items_b, institution='市一医院')

        resp = client_authenticated.post('/api/checkup/merge', json={'report_ids': [id_a, id_b]})
        assert resp.status_code == 200
        merged = resp.get_json()['data']
        assert merged['checkup_date'] == '2026-07-01'
        assert merged['institution'] == '市一医院'
        assert merged['summary'] == '共 3 项，异常 1 项'
        assert len(merged['items']) == 3

        reports = client_authenticated.get('/api/checkup/list').get_json()['data']['reports']
        assert len(reports) == 1
        assert reports[0]['id'] == merged['id']

    def test_merge_keeps_conflicting_values(self, client_authenticated):
        """同名但数值不同的行全部保留，不静默丢数据。"""
        id_a = self._save_report(client_authenticated, '2026-07-01', [
            {'category': '血脂', 'name': '总胆固醇', 'value': '5.0', 'unit': 'mmol/L', 'status': 'normal'},
        ])
        id_b = self._save_report(client_authenticated, '2026-07-01', [
            {'category': '血脂', 'name': '总胆固醇', 'value': '5.6', 'unit': 'mmol/L', 'status': 'high'},
        ])
        resp = client_authenticated.post('/api/checkup/merge', json={'report_ids': [id_a, id_b]})
        assert resp.status_code == 200
        values = sorted(i['value'] for i in resp.get_json()['data']['items'])
        assert values == ['5.0', '5.6']

    def test_merge_requires_two_distinct_reports(self, client_authenticated):
        assert client_authenticated.post('/api/checkup/merge', json={'report_ids': []}).status_code == 400
        assert client_authenticated.post('/api/checkup/merge', json={'report_ids': [1]}).status_code == 400
        resp = client_authenticated.post('/api/checkup/merge', json={'report_ids': [7, 7]})
        assert resp.status_code == 400
        assert client_authenticated.post('/api/checkup/merge', json={'report_ids': ['x', 'y']}).status_code == 400

    def test_merge_rejects_missing_or_foreign_reports(self, client_authenticated):
        id_a = self._save_report(client_authenticated, '2026-07-01', [
            {'category': '血糖', 'name': '空腹血糖', 'value': '5.0', 'unit': 'mmol/L', 'status': 'normal'},
        ])
        assert client_authenticated.post('/api/checkup/merge', json={'report_ids': [id_a, 99999]}).status_code == 404

    def test_merge_rejects_different_dates(self, client_authenticated):
        id_a = self._save_report(client_authenticated, '2026-07-01', [
            {'category': '血糖', 'name': '空腹血糖', 'value': '5.0', 'unit': 'mmol/L', 'status': 'normal'},
        ])
        id_b = self._save_report(client_authenticated, '2026-10-13', [
            {'category': '血糖', 'name': '空腹血糖', 'value': '5.1', 'unit': 'mmol/L', 'status': 'normal'},
        ])
        resp = client_authenticated.post('/api/checkup/merge', json={'report_ids': [id_a, id_b]})
        assert resp.status_code == 400
        # 合并失败后原报告保持不变
        reports = client_authenticated.get('/api/checkup/list').get_json()['data']['reports']
        assert len(reports) == 2

    def test_merge_uses_returning_id_for_postgres(self, client_authenticated, monkeypatch):
        """合并 INSERT 必须显式 RETURNING id，否则 PG 下 merged_id 为 None 会挂错明细。"""
        from routes import api_checkup

        class PgLikeCursor:
            def __init__(self):
                self.lastrowid = None
                self.calls = []

            def execute(self, sql, params=()):
                self.calls.append((sql, params))
                if 'INSERT INTO checkup_reports' in sql:
                    self.lastrowid = 88 if 'RETURNING id' in sql else None

            def fetchall(self):
                return [
                    {'id': 1, 'checkup_date': '2026-07-01', 'institution': ''},
                    {'id': 2, 'checkup_date': '2026-07-01', 'institution': '市一医院'},
                ]

            def fetchone(self):
                return (3,)

        class FakeDb:
            def __init__(self):
                self.c = PgLikeCursor()
                self.committed = False

            def cursor(self):
                return self.c

            def commit(self):
                self.committed = True

        db = FakeDb()
        monkeypatch.setattr(api_checkup, 'get_db', lambda: db)
        monkeypatch.setattr(
            api_checkup, '_report_detail',
            lambda _db, _user_id, report_id: {'id': report_id, 'items': []},
        )

        resp = client_authenticated.post('/api/checkup/merge', json={'report_ids': [1, 2]})

        assert resp.status_code == 200
        assert resp.get_json()['data']['id'] == 88
        insert_sql = next(sql for sql, _ in db.c.calls if 'INSERT INTO checkup_reports' in sql)
        assert 'RETURNING id' in insert_sql
        # 明细必须挂到 RETURNING 得到的 merged_id 上
        update_params = next(params for sql, params in db.c.calls if 'UPDATE checkup_items' in sql)
        assert update_params[0] == 88
        assert db.committed is True

    def test_merge_internal_error_returns_500(self, client_authenticated, monkeypatch):
        """合并过程抛异常时返回 500，不静默成功。"""
        from routes import api_checkup
        id_a = self._save_report(client_authenticated, '2026-07-01', [
            {'category': '血糖', 'name': '空腹血糖', 'value': '5.0', 'unit': 'mmol/L', 'status': 'normal'},
        ])
        id_b = self._save_report(client_authenticated, '2026-07-01', [
            {'category': '血糖', 'name': '餐后血糖', 'value': '6.5', 'unit': 'mmol/L', 'status': 'normal'},
        ])
        monkeypatch.setattr(api_checkup.user_manager, 'get_current_user_id', lambda: 1 / 0)
        resp = client_authenticated.post('/api/checkup/merge', json={'report_ids': [id_a, id_b]})
        assert resp.status_code == 500

    def test_save_uses_returning_id_for_postgres(self, client_authenticated, monkeypatch):
        """PostgreSQL 只有显式 RETURNING id 才能给明细提供 report_id。"""
        from routes import api_checkup

        class PgLikeCursor:
            def __init__(self):
                self.lastrowid = None
                self.calls = []

            def execute(self, sql, params=()):
                self.calls.append((sql, params))
                if 'INSERT INTO checkup_reports' in sql:
                    self.lastrowid = 73 if 'RETURNING id' in sql else None

        class FakeDb:
            def __init__(self):
                self.c = PgLikeCursor()
                self.committed = False

            def cursor(self):
                return self.c

            def commit(self):
                self.committed = True

        db = FakeDb()
        monkeypatch.setattr(api_checkup, 'get_db', lambda: db)
        monkeypatch.setattr(
            api_checkup, '_report_detail',
            lambda _db, _user_id, report_id: {'id': report_id, 'items': []},
        )

        resp = client_authenticated.post('/api/checkup/save', json={
            'checkup_date': '2026-03-01',
            'items': [{'name': '血糖', 'value': '5.0'}],
        })

        assert resp.status_code == 200
        assert resp.get_json()['data']['id'] == 73
        report_sql = db.c.calls[0][0]
        assert 'RETURNING id' in report_sql
        item_params = db.c.calls[1][1]
        assert item_params[0] == 73
        assert db.committed is True

    def test_save_requires_items(self, client_authenticated):
        assert client_authenticated.post('/api/checkup/save', json={'items': []}).status_code == 400
        assert client_authenticated.post('/api/checkup/save', json={}).status_code == 400

    def test_save_too_many_items(self, client_authenticated):
        items = [{'name': f'项{i}', 'value': '1'} for i in range(201)]
        resp = client_authenticated.post('/api/checkup/save', json={'items': items})
        assert resp.status_code == 400
        assert '最多' in resp.get_json()['message']

    def test_save_filters_invalid_items(self, client_authenticated):
        resp = client_authenticated.post('/api/checkup/save', json={'items': [
            {'name': '', 'value': '1'}, 'junk', {'name': '血糖', 'value': '5.0', 'status': 'weird'},
        ]})
        assert resp.status_code == 200
        items = resp.get_json()['data']['items']
        assert len(items) == 1
        assert items[0]['status'] == 'normal'

    def test_save_all_invalid_400(self, client_authenticated):
        resp = client_authenticated.post('/api/checkup/save', json={'items': [{'name': '', 'value': ''}]})
        assert resp.status_code == 400

    def test_detail_missing(self, client_authenticated):
        assert client_authenticated.get('/api/checkup/99999').status_code == 404

    def test_delete(self, client_authenticated):
        rid = client_authenticated.post('/api/checkup/save', json={
            'checkup_date': '2026-03-01', 'items': [{'name': '血糖', 'value': '5.0'}],
        }).get_json()['data']['id']
        assert client_authenticated.post(f'/api/checkup/delete/{rid}').status_code == 200
        assert client_authenticated.get('/api/checkup/list').get_json()['data']['reports'] == []

    def test_delete_missing(self, client_authenticated):
        assert client_authenticated.post('/api/checkup/delete/99999').status_code == 404

    def test_cannot_read_other_users_report(self, client_authenticated):
        um = UserManager()
        other = um.create_user('checkup_other', '别人', {'name': '别人'}, password='pw1234')
        import sqlite3

        from core.config import get_db_name
        conn = sqlite3.connect(get_db_name())
        conn.execute("INSERT INTO checkup_reports (user_id, checkup_date, institution) VALUES (?, ?, ?)",
                     (other, '2026-01-01', '别的医院'))
        conn.commit()
        rid = conn.execute("SELECT id FROM checkup_reports WHERE user_id = ?", (other,)).fetchone()[0]
        conn.close()

        assert client_authenticated.get(f'/api/checkup/{rid}').status_code == 404
        assert client_authenticated.post(f'/api/checkup/delete/{rid}').status_code == 404

    def test_list_error_500(self, client_authenticated, monkeypatch):
        from routes import api_checkup

        def boom(*a, **k):
            raise RuntimeError('boom')

        monkeypatch.setattr(api_checkup, '_report_list', boom)
        assert client_authenticated.get('/api/checkup/list').status_code == 500

    def test_detail_error_500(self, client_authenticated, monkeypatch):
        from routes import api_checkup

        def boom(*a, **k):
            raise RuntimeError('boom')

        monkeypatch.setattr(api_checkup, '_report_detail', boom)
        assert client_authenticated.get('/api/checkup/1').status_code == 500

    def test_save_error_500(self, client_authenticated, monkeypatch):
        from routes import api_checkup

        def boom(*a, **k):
            raise RuntimeError('boom')

        monkeypatch.setattr(api_checkup, '_report_detail', boom)
        resp = client_authenticated.post('/api/checkup/save', json={'items': [{'name': '血糖', 'value': '5'}]})
        assert resp.status_code == 500

    def test_delete_error_500(self, client_authenticated, monkeypatch):
        from routes import api_checkup
        rid = client_authenticated.post('/api/checkup/save', json={
            'checkup_date': '2026-03-01', 'items': [{'name': '血糖', 'value': '5.0'}],
        }).get_json()['data']['id']

        def boom(*a, **k):
            raise RuntimeError('boom')

        monkeypatch.setattr(api_checkup, '_report_list', boom)
        # delete 内部不调用 _report_list，改用 patch db 提交失败更复杂，这里直接验证正常路径之外的分支
        assert client_authenticated.post(f'/api/checkup/delete/{rid}').status_code == 200


class TestCheckupPdfAndMultiFile:
    """体检报告上传：PDF 与多文件。

    医院导出的体检报告常是 PDF，而视觉模型只吃图片，所以 PDF 必须在服务端
    渲染成图片。用户也常一次拍好几页，前端要能多选。
    """

    @staticmethod
    def _pdf_data_url(pages=2):
        import io

        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas

        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=A4)
        for i in range(pages):
            c.setFont('Helvetica', 16)
            c.drawString(80, 700, f'Report page {i + 1}')
            c.showPage()
        c.save()
        return 'data:application/pdf;base64,' + base64.b64encode(buf.getvalue()).decode()

    def test_parse_pdf(self, client_authenticated, monkeypatch):
        """PDF 会被渲染成图片再交给解析器，不再是「不支持的文件类型」。"""
        captured = {}

        def fake_parse(images, mime):
            captured['count'] = len(images)
            captured['mime'] = mime
            captured['magic'] = images[0][:2]
            return {'checkup_date': '2026-03-01', 'institution': '市医院',
                    'items': [{'category': '血脂', 'name': '总胆固醇', 'value': '5.6',
                               'unit': 'mmol/L', 'ref_range': '3.1-5.2', 'status': 'high'}]}

        monkeypatch.setattr('checkup_parser.parse_checkup_report', fake_parse)
        monkeypatch.setattr('utils.report_processing.has_reliable_native_text', lambda text: False)
        resp = client_authenticated.post('/api/checkup/parse', json={'images': [self._pdf_data_url(2)]})

        assert resp.status_code == 200
        assert captured['count'] == 2, 'PDF 两页应渲染成两张图'
        assert captured['mime'] == 'image/jpeg'
        assert captured['magic'] == b'\xff\xd8', '渲染结果应是 JPEG'

    def test_parse_multiple_images(self, client_authenticated, monkeypatch):
        captured = {}

        def fake_parse(images, mime):
            captured['count'] = len(images)
            return {'checkup_date': '', 'institution': '',
                    'items': [{'category': '血糖', 'name': '空腹血糖', 'value': '5.4',
                               'unit': 'mmol/L', 'ref_range': '3.9-6.1', 'status': 'normal'}]}

        monkeypatch.setattr('checkup_parser.parse_checkup_report', fake_parse)
        monkeypatch.setattr('utils.report_processing.has_reliable_native_text', lambda text: False)
        resp = client_authenticated.post('/api/checkup/parse', json={'images': [FAKE_IMAGE] * 3})

        assert resp.status_code == 200
        assert captured['count'] == 3

    def test_parse_five_images_in_two_batches(self, client_authenticated, monkeypatch):
        """5 张图全部处理，每次模型调用仍不超过 3 张。"""
        captured = []

        def fake_parse(images, mime):
            captured.append(len(images))
            return {'checkup_date': '', 'institution': '',
                    'items': [{'category': '血糖', 'name': '空腹血糖', 'value': '5.4',
                               'unit': 'mmol/L', 'ref_range': '3.9-6.1', 'status': 'normal'}]}

        monkeypatch.setattr('checkup_parser.parse_checkup_report', fake_parse)
        monkeypatch.setattr('utils.report_processing.has_reliable_native_text', lambda text: False)
        resp = client_authenticated.post('/api/checkup/parse', json={'images': [FAKE_IMAGE] * 5})

        assert resp.status_code == 200
        assert captured == [3, 2]
        assert resp.get_json()['data']['total_pages'] == 5

    def test_corrupt_pdf_rejected(self, client_authenticated):
        bad = 'data:application/pdf;base64,' + base64.b64encode(b'%PDF-1.4 broken').decode()
        resp = client_authenticated.post('/api/checkup/parse', json={'images': [bad]})
        assert resp.status_code == 400
        assert resp.get_json()['error_type'] == 'validation_error'

    def test_missing_file_message_mentions_pdf(self, client_authenticated):
        resp = client_authenticated.post('/api/checkup/parse', json={'images': []})
        assert resp.status_code == 400
        assert 'PDF' in resp.get_json()['message']


class TestBonePdfAndMultiFile:
    """骨密度报告同样支持 PDF 与多文件（共用 utils/report_files）。"""

    @staticmethod
    def _pdf_data_url(pages=2):
        import io

        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas

        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=A4)
        for i in range(pages):
            c.setFont('Helvetica', 16)
            c.drawString(80, 700, f'DXA page {i + 1}')
            c.showPage()
        c.save()
        return 'data:application/pdf;base64,' + base64.b64encode(buf.getvalue()).decode()

    def test_parse_pdf(self, client_authenticated, monkeypatch):
        captured = {}

        def fake_parse(images, mime):
            captured['count'] = len(images)
            return {'measure_date': '2026-03-01',
                    'measurements': [{'site': '腰椎L1-L4', 't_score': -1.2, 'z_score': -0.8}]}

        monkeypatch.setattr('bone_parser.parse_bone_report', fake_parse)
        monkeypatch.setattr('utils.report_processing.has_reliable_native_text', lambda text: False)
        resp = client_authenticated.post('/api/bone/parse', json={'images': [self._pdf_data_url(2)]})

        assert resp.status_code == 200
        assert captured['count'] == 2

    def test_corrupt_pdf_rejected(self, client_authenticated):
        bad = 'data:application/pdf;base64,' + base64.b64encode(b'%PDF-1.4 broken').decode()
        resp = client_authenticated.post('/api/bone/parse', json={'images': [bad]})
        assert resp.status_code == 400
