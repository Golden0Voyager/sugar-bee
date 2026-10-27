"""骨密度（bone_density.py / bone_parser.py / /api/bone）测试。"""
import base64

import pytest

import bone_density
import bone_parser
from user_manager import UserManager

FAKE_IMAGE = 'data:image/jpeg;base64,' + base64.b64encode(b'fake').decode()


def _uid(client):
    with client.session_transaction() as sess:
        return sess['current_user_id']


class TestGrade:
    @pytest.mark.parametrize('t,expected', [
        (1.0, 'normal'),
        (-0.9, 'normal'),
        (-1.0, 'normal'),
        (-1.1, 'osteopenia'),
        (-2.4, 'osteopenia'),
        (-2.5, 'osteoporosis'),
        (-3.8, 'osteoporosis'),
    ])
    def test_grade_boundaries(self, t, expected):
        assert bone_density.grade_t(t)['key'] == expected

    def test_grade_invalid(self):
        assert bone_density.grade_t(None) is None
        assert bone_density.grade_t('') is None
        assert bone_density.grade_t('abc') is None

    def test_grade_has_label_and_color(self):
        g = bone_density.grade_t(-3)
        assert g['label'] and g['color'].startswith('#')


class TestNormalizeSite:
    def test_exact(self):
        assert bone_density.normalize_site('腰椎 L1-L4') == '腰椎 L1-L4'

    def test_alias(self):
        assert bone_density.normalize_site('腰椎') == '腰椎 L1-L4'
        assert bone_density.normalize_site('髋部') == '髋部（全髋）'
        assert bone_density.normalize_site('股骨颈') == '股骨颈'
        assert bone_density.normalize_site('前臂') == '桡骨（前臂）'

    def test_empty_defaults_to_first(self):
        assert bone_density.normalize_site('') == bone_density.SITES[0]
        assert bone_density.normalize_site(None) == bone_density.SITES[0]

    def test_unknown_passthrough(self):
        assert bone_density.normalize_site('某医院自定义') == '某医院自定义'


class TestBuildSummary:
    def test_empty(self):
        s = bone_density.build_summary([])
        assert s['latest'] is None
        assert s['records'] == []
        assert s['trend'] == []
        assert s['total'] == 0

    def test_with_records(self):
        rows = [
            {'id': 1, 'measure_date': '2025-01-01', 'site': '腰椎', 't_score': '-2.6',
             'z_score': '-1.0', 'bmd': '0.780', 'institution': '', 'notes': ''},
            {'id': 2, 'measure_date': '2026-01-01', 'site': '股骨颈', 't_score': -1.8,
             'z_score': None, 'bmd': None, 'institution': '市医院', 'notes': ''},
        ]
        s = bone_density.build_summary(rows)
        assert s['total'] == 2
        assert s['latest']['measure_date'] == '2026-01-01'
        assert s['records'][0]['t_score'] == -2.6
        assert s['records'][0]['grade']['key'] == 'osteoporosis'
        assert s['records'][0]['site'] == '腰椎 L1-L4'
        assert len(s['trend']) == 2

    def test_trend_skips_missing_t(self):
        rows = [{'id': 1, 'measure_date': '2026-01-01', 'site': '腰椎', 't_score': None,
                 'z_score': None, 'bmd': 0.9}]
        assert bone_density.build_summary(rows)['trend'] == []

    def test_latest_picks_worst_site_same_day(self):
        """同一天多个部位时，「最近一次」应取最低 T 值（临床诊断口径）。"""
        rows = [
            {'id': 1, 'measure_date': '2026-03-01', 'site': '腰椎', 't_score': -2.7,
             'z_score': -1.4, 'bmd': 0.782},
            {'id': 2, 'measure_date': '2026-03-01', 'site': '股骨颈', 't_score': -1.8,
             'z_score': -0.9, 'bmd': 0.65},
            {'id': 3, 'measure_date': '2026-03-01', 'site': '髋部（全髋）', 't_score': -1.2,
             'z_score': -0.6, 'bmd': 0.72},
        ]
        latest = bone_density.build_summary(rows)['latest']
        assert latest['site'] == '腰椎 L1-L4'
        assert latest['t_score'] == -2.7
        assert latest['grade']['key'] == 'osteoporosis'

    def test_latest_falls_back_when_no_t_score(self):
        rows = [
            {'id': 1, 'measure_date': '2026-03-01', 'site': '腰椎', 't_score': -1.0,
             'z_score': None, 'bmd': 0.8},
            {'id': 2, 'measure_date': '2026-03-02', 'site': '股骨颈', 't_score': None,
             'z_score': None, 'bmd': 0.7},
        ]
        latest = bone_density.build_summary(rows)['latest']
        assert latest['measure_date'] == '2026-03-02'
        assert latest['t_score'] is None


class TestValidatePayload:
    def test_valid(self):
        payload, err = bone_density.validate_payload({
            'measure_date': '2026-03-01', 'site': '腰椎', 't_score': '-2.1', 'z_score': '-1.2',
            'bmd': '0.812', 'institution': ' 市 医院 ', 'notes': '年度复查',
        })
        assert err is None
        assert payload['t_score'] == -2.1
        assert payload['z_score'] == -1.2
        assert payload['bmd'] == 0.812
        assert payload['institution'] == '市 医院'
        assert payload['site'] == '腰椎 L1-L4'

    def test_date_required(self):
        payload, err = bone_density.validate_payload({'t_score': -1})
        assert payload is None and '日期' in err

    def test_date_truncated(self):
        payload, _ = bone_density.validate_payload({'measure_date': '2026-03-01T10:00:00', 't_score': -1})
        assert payload['measure_date'] == '2026-03-01'

    def test_t_must_be_number(self):
        payload, err = bone_density.validate_payload({'measure_date': '2026-03-01', 't_score': 'abc'})
        assert payload is None and '数字' in err

    def test_t_out_of_range(self):
        payload, err = bone_density.validate_payload({'measure_date': '2026-03-01', 't_score': 99})
        assert payload is None and '范围' in err

    def test_z_must_be_number(self):
        payload, err = bone_density.validate_payload({'measure_date': '2026-03-01', 't_score': -1, 'z_score': 'x'})
        assert payload is None and 'Z 值' in err

    def test_bmd_must_be_number(self):
        payload, err = bone_density.validate_payload({'measure_date': '2026-03-01', 'bmd': 'x'})
        assert payload is None and '骨密度' in err

    def test_needs_t_or_bmd(self):
        payload, err = bone_density.validate_payload({'measure_date': '2026-03-01'})
        assert payload is None and '至少' in err


class TestBoneApi:
    def test_requires_login(self, client):
        resp = client.get('/api/bone', headers={'X-Requested-With': 'XMLHttpRequest'})
        assert resp.status_code == 401

    def test_empty_list(self, client_authenticated):
        resp = client_authenticated.get('/api/bone')
        assert resp.status_code == 200
        data = resp.get_json()['data']
        assert data['records'] == []
        assert data['latest'] is None
        assert data['sites'] == bone_density.SITES

    def test_create_and_list(self, client_authenticated):
        resp = client_authenticated.post('/api/bone', json={
            'measure_date': '2026-03-01', 'site': '股骨颈', 't_score': -2.7, 'z_score': -1.1,
        })
        assert resp.status_code == 200
        data = resp.get_json()['data']
        assert data['latest']['t_score'] == -2.7
        assert data['latest']['grade']['key'] == 'osteoporosis'

        listed = client_authenticated.get('/api/bone').get_json()['data']
        assert listed['total'] == 1

    def test_create_invalid(self, client_authenticated):
        resp = client_authenticated.post('/api/bone', json={'measure_date': ''})
        assert resp.status_code == 400
        assert resp.get_json()['error_type'] == 'validation_error'

    def test_delete(self, client_authenticated):
        created = client_authenticated.post('/api/bone', json={
            'measure_date': '2026-03-01', 't_score': -1.5,
        }).get_json()['data']
        rid = created['latest']['id']
        resp = client_authenticated.post(f'/api/bone/delete/{rid}')
        assert resp.status_code == 200
        assert resp.get_json()['data']['total'] == 0

    def test_delete_missing(self, client_authenticated):
        resp = client_authenticated.post('/api/bone/delete/99999')
        assert resp.status_code == 404

    def test_delete_other_user_record_forbidden(self, client_authenticated):
        """不能删别人的记录（按 user_id 过滤）。"""
        um = UserManager()
        other = um.create_user('bone_other', '别人', {'name': '别人'}, password='pw1234')
        import sqlite3

        from core.config import get_db_name
        conn = sqlite3.connect(get_db_name())
        conn.execute("INSERT INTO bone_density_records (user_id, measure_date, t_score) VALUES (?, ?, ?)",
                     (other, '2026-01-01', -3.0))
        conn.commit()
        rid = conn.execute("SELECT id FROM bone_density_records WHERE user_id = ?", (other,)).fetchone()[0]
        conn.close()

        resp = client_authenticated.post(f'/api/bone/delete/{rid}')
        assert resp.status_code == 404

    def test_list_handles_db_error(self, client_authenticated, monkeypatch):
        from routes import api_bone

        def _boom(*a, **k):
            raise RuntimeError('boom')

        monkeypatch.setattr(api_bone, '_rows_for_user', _boom)
        assert client_authenticated.get('/api/bone').status_code == 500

    def test_create_handles_db_error(self, client_authenticated, monkeypatch):
        from routes import api_bone

        def _boom(*a, **k):
            raise RuntimeError('boom')

        monkeypatch.setattr(api_bone, '_rows_for_user', _boom)
        resp = client_authenticated.post('/api/bone', json={'measure_date': '2026-03-01', 't_score': -1})
        assert resp.status_code == 500

    def test_delete_handles_db_error(self, client_authenticated, monkeypatch):
        from routes import api_bone
        created = client_authenticated.post('/api/bone', json={'measure_date': '2026-03-01', 't_score': -1})
        rid = created.get_json()['data']['latest']['id']

        def _boom(*a, **k):
            raise RuntimeError('boom')

        monkeypatch.setattr(api_bone, '_rows_for_user', _boom)
        assert client_authenticated.post(f'/api/bone/delete/{rid}').status_code == 500


class TestBoneParser:
    def test_extract_json_variants(self):
        assert bone_parser._extract_json('{"measurements": []}') == {'measurements': []}
        assert bone_parser._extract_json('```json\n{"measurements": []}\n```') == {'measurements': []}
        assert bone_parser._extract_json('结果：\n{"measurements": []}\n以上') == {'measurements': []}
        assert bone_parser._extract_json('无法识别') is None
        assert bone_parser._extract_json(None) is None

    def test_to_float_handles_unicode_minus(self):
        assert bone_parser._to_float('−2.7') == -2.7
        assert bone_parser._to_float('—1.5') == -1.5
        assert bone_parser._to_float('-2.7') == -2.7
        assert bone_parser._to_float('') is None
        assert bone_parser._to_float('abc') is None

    def test_normalize_multi_site(self):
        result = bone_parser.normalize_report({
            'measure_date': '2026-03-01',
            'institution': '市第一医院',
            'measurements': [
                {'site': '腰椎', 't_score': '-2.7', 'z_score': '-1.4', 'bmd': '0.782'},
                {'site': '股骨颈', 't_score': '-1.8', 'z_score': '-0.9', 'bmd': '0.650'},
                {'site': '全髋', 't_score': '', 'z_score': '', 'bmd': '0.720'},
            ],
        })
        assert result['measure_date'] == '2026-03-01'
        assert result['institution'] == '市第一医院'
        assert len(result['measurements']) == 3
        assert result['measurements'][0]['site'] == '腰椎 L1-L4'
        assert result['measurements'][0]['t_score'] == -2.7
        assert result['measurements'][2]['t_score'] is None

    def test_normalize_drops_items_without_t_or_bmd(self):
        result = bone_parser.normalize_report({'measurements': [
            {'site': '腰椎'}, 'junk', {'site': '股骨颈', 't_score': '-2.0'},
        ]})
        assert len(result['measurements']) == 1

    def test_normalize_bad_date_and_missing_list(self):
        result = bone_parser.normalize_report({'measure_date': '3月1日', 'measurements': 'nope'})
        assert result['measure_date'] == ''
        assert result['measurements'] == []

    def test_normalize_none(self):
        assert bone_parser.normalize_report(None) == {
            'measure_date': '', 'institution': '', 'measurements': [],
        }

    def test_default_site_when_missing(self):
        result = bone_parser.normalize_report({'measurements': [{'t_score': '-1.0'}]})
        assert result['measurements'][0]['site'] == bone_density.SITES[0]

    def test_parse_uses_vision(self, monkeypatch):
        captured = {}

        def fake_call(prompt, images_data=None, mime_type=None, task_type=None):
            captured['task'] = task_type
            captured['images'] = bool(images_data)
            return '{"measurements": [{"site": "腰椎", "t_score": "-2.7"}]}'

        monkeypatch.setattr('bone_parser.call_ai', fake_call)
        result = bone_parser.parse_bone_report([b'img'], 'image/jpeg')
        assert captured == {'task': 'vision', 'images': True}
        assert result['measurements'][0]['t_score'] == -2.7

    def test_parse_propagates_error(self, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError('AI down')

        monkeypatch.setattr('bone_parser.call_ai', boom)
        with pytest.raises(RuntimeError):
            bone_parser.parse_bone_report([b'img'], 'image/jpeg')


class TestBoneParseApi:
    def test_requires_login(self, client):
        resp = client.post('/api/bone/parse', json={}, headers={'X-Requested-With': 'XMLHttpRequest'})
        assert resp.status_code == 401

    def test_requires_image(self, client_authenticated):
        resp = client_authenticated.post('/api/bone/parse', json={})
        assert resp.status_code == 400

    def test_bad_base64(self, client_authenticated):
        resp = client_authenticated.post('/api/bone/parse', json={'images': ['@@@']})
        assert resp.status_code == 400

    def test_success(self, client_authenticated, monkeypatch):
        monkeypatch.setattr('bone_parser.parse_bone_report', lambda imgs, mime: {
            'measure_date': '2026-03-01', 'institution': '市医院',
            'measurements': [
                {'site': '腰椎 L1-L4', 't_score': -2.7, 'z_score': -1.4, 'bmd': 0.782},
                {'site': '股骨颈', 't_score': -1.8, 'z_score': -0.9, 'bmd': 0.65},
            ],
        })
        resp = client_authenticated.post('/api/bone/parse', json={'images': [FAKE_IMAGE]})
        assert resp.status_code == 200
        data = resp.get_json()['data']
        assert len(data['measurements']) == 2
        assert data['measure_date'] == '2026-03-01'

    def test_empty_result_422(self, client_authenticated, monkeypatch):
        monkeypatch.setattr('bone_parser.parse_bone_report', lambda imgs, mime: {
            'measure_date': '', 'institution': '', 'measurements': [],
        })
        resp = client_authenticated.post('/api/bone/parse', json={'images': [FAKE_IMAGE]})
        assert resp.status_code == 422
        assert resp.get_json()['error_type'] == 'parse_empty'

    def test_fills_today_when_date_missing(self, client_authenticated, monkeypatch):
        monkeypatch.setattr('bone_parser.parse_bone_report', lambda imgs, mime: {
            'measure_date': '', 'institution': '',
            'measurements': [{'site': '腰椎 L1-L4', 't_score': -1.2, 'z_score': None, 'bmd': None}],
        })
        data = client_authenticated.post('/api/bone/parse', json={'images': [FAKE_IMAGE]}).get_json()['data']
        assert len(data['measure_date']) == 10

    def test_ai_failure_503(self, client_authenticated, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError('AI down')

        monkeypatch.setattr('bone_parser.parse_bone_report', boom)
        resp = client_authenticated.post('/api/bone/parse', json={'images': [FAKE_IMAGE]})
        assert resp.status_code == 503
        assert resp.get_json()['error_type'] == 'ai_unavailable'


class TestBonePageEntry:
    def test_entry_card_rendered_when_module_enabled(self, client_authenticated):
        UserManager().update_user_profile_partial(_uid(client_authenticated), {
            'app_name': '我的健康本',
            'enabled_modules': ['blood_pressure', 'bone_density', 'checkup'],
        })
        html = client_authenticated.get('/').data.decode('utf-8')
        assert '骨密度' in html
        assert '体检历史' in html

    def test_entry_card_hidden_when_module_disabled(self, client_authenticated):
        UserManager().update_user_profile_partial(_uid(client_authenticated), {
            'app_name': '我的健康本',
            'enabled_modules': ['blood_pressure'],
        })
        html = client_authenticated.get('/').data.decode('utf-8')
        assert 'id="boneEntrySummary"' not in html
        assert 'id="checkupEntrySummary"' not in html
