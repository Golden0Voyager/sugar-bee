"""应用品牌个性化（branding.py）与 /api/user/branding 路由测试。"""
import os

import pytest

import branding
from user_manager import UserManager

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _current_uid(client):
    with client.session_transaction() as sess:
        return sess['current_user_id']


class TestPersonaPresets:
    def test_every_preset_has_required_fields(self):
        for p in branding.PERSONAS:
            assert p['id'] and p['label'] and p['desc']
            assert p['icon'] and p['tagline']
            assert p['modules']
            # 模块必须在白名单内，否则前端勾选不上
            assert set(p['modules']) <= set(branding.ALL_MODULES)

    def test_presets_do_not_carry_their_own_app_name(self):
        """所有场景模板的应用名默认都是「蜜蜂健康」，模板里不该各起各的名字。

        历史行为：选了「主要看血压」就叫「我的血压本」、选了「健身减重」就叫
        「我的运动日志」，一家人的界面看起来像六个不同的应用。
        """
        for p in branding.PERSONAS:
            assert 'name_suggestions' not in p
            assert 'emoji' not in p, '应用图标默认统一 🐝，模板只留卡片展示用的 icon'

    def test_persona_options_share_default_name_and_emoji(self):
        for opt in branding.persona_options():
            assert opt['emoji'] == branding.DEFAULT_EMOJI == '🐝'
            assert opt['name_suggestions'][0] == branding.DEFAULT_APP_NAME == '蜜蜂健康'

    def test_persona_options_is_copy_does_not_leak(self):
        opts = branding.persona_options()
        opts[0]['name_suggestions'].append('被改坏的名字')
        assert '被改坏的名字' not in branding.NAME_SUGGESTIONS

    def test_preset_ids_unique(self):
        ids = [p['id'] for p in branding.PERSONAS]
        assert len(ids) == len(set(ids))

    def test_exactly_four_presets(self):
        """模板固定为 4 个：日常健康 / 多维控糖 / 运动健身 / 为家人记录。

        曾经有 6 个，其中「全部功能」和「控糖」模块完全相同（纯重复），
        所以删掉了重复项，并按使用场景重排成现在的 4 个。
        """
        assert [p['id'] for p in branding.PERSONAS] == [
            'daily_health', 'glucose_care', 'fitness', 'family_care',
        ]

    def test_preset_labels(self):
        assert [p['label'] for p in branding.PERSONAS] == [
            '日常健康', '多维控糖', '运动健身', '为家人记录',
        ]

    def test_no_duplicate_module_sets(self):
        """两个模板的模块组合不能完全一样，否则等于让用户做无意义的选择。"""
        seen = [tuple(p['modules']) for p in branding.PERSONAS]
        assert len(seen) == len(set(seen))

    def test_glucose_only_in_glucose_related_presets(self):
        """血糖只出现在「多维控糖」和「为家人记录」两个模板里。"""
        with_glucose = [p['id'] for p in branding.PERSONAS if 'glucose' in p['modules']]
        assert with_glucose == ['glucose_care', 'family_care']

    def test_subset_presets_stay_within_base(self):
        """运动健身 / 为家人记录 是在基础模块上做减法，不能凭空冒出未知模块。"""
        fitness = branding.get_persona('fitness')
        assert fitness['modules'] == ['exercise', 'weight', 'diet']
        assert set(fitness['modules']) < set(branding.BASE_MODULES)

        family = branding.get_persona('family_care')
        assert family['modules'] == ['glucose', 'blood_pressure', 'medication']
        assert set(family['modules']) <= set(branding.ALL_MODULES)

    def test_base_template_covers_family_daily_use(self):
        """家庭基础模板：慢病管理通用功能（血压/用药/体重/运动/餐食/骨密度/体检），不含血糖。"""
        assert branding.BASE_MODULES == ['blood_pressure', 'medication', 'weight', 'exercise', 'diet',
                                         'bone_density', 'checkup']
        assert 'glucose' not in branding.BASE_MODULES
        # 除血糖外，其余模块都该在基础模板里
        assert set(branding.ALL_MODULES) - set(branding.BASE_MODULES) == {'glucose'}

    def test_base_persona_uses_base_modules(self):
        persona = branding.get_persona('daily_health')
        assert persona['modules'] == branding.BASE_MODULES

    def test_glucose_only_persona_includes_base(self):
        """血糖模块只给需要的成员，且在其模板里包含基础功能。"""
        persona = branding.get_persona('glucose_care')
        assert 'glucose' in persona['modules']
        assert set(branding.BASE_MODULES) <= set(persona['modules'])

    def test_get_persona(self):
        assert branding.get_persona('daily_health')['label']
        assert branding.get_persona('nope') is None
        assert branding.get_persona('') is None
        assert branding.get_persona(None) is None

    def test_persona_options_is_copy(self):
        opts = branding.persona_options()
        opts[0]['modules'].append('hacked')
        opts[0]['label'] = 'changed'
        assert 'hacked' not in branding.PERSONAS[0]['modules']
        assert branding.PERSONAS[0]['label'] != 'changed'


class TestCleaners:
    def test_clean_name(self):
        assert branding.clean_name('  妈妈 的 血糖本 ') == '妈妈 的 血糖本'
        assert branding.clean_name(None) == ''
        assert branding.clean_name('') == ''
        assert len(branding.clean_name('长' * 50)) == branding.NAME_MAX_LEN

    def test_clean_emoji(self):
        assert branding.clean_emoji(' 🐝 ') == '🐝'
        assert branding.clean_emoji(None) == ''
        assert branding.clean_emoji('') == ''
        assert branding.clean_emoji('x' * (branding.EMOJI_MAX_LEN + 1)) == ''

    def test_clean_tagline(self):
        assert branding.clean_tagline(' 每天  一记 ') == '每天 一记'
        assert branding.clean_tagline(None) == ''
        assert len(branding.clean_tagline('标' * 40)) == branding.TAGLINE_MAX_LEN


class TestResolveBranding:
    def test_empty_user_falls_back_to_default_brand(self):
        result = branding.resolve_branding(None)
        assert result['app_name'] == branding.DEFAULT_APP_NAME == '蜜蜂健康'
        assert result['tagline'] == branding.DEFAULT_TAGLINE == '记录全家人的健康'
        assert result['emoji'] == branding.DEFAULT_EMOJI
        assert result['onboarded'] is False
        assert result['customized'] is False

    def test_customized_name(self):
        result = branding.resolve_branding({'app_name': '妈妈的血糖本', 'app_emoji': '💗',
                                            'app_tagline': '家人的健康我来记'})
        assert result['app_name'] == '妈妈的血糖本'
        assert result['emoji'] == '💗'
        assert result['tagline'] == '家人的健康我来记'
        assert result['customized'] is True
        assert result['onboarded'] is True

    def test_persona_supplies_tagline_but_not_emoji(self):
        """标语可以跟场景走（「每天一记，心里有数」），图标不跟。"""
        result = branding.resolve_branding({'app_name': '我的健康本', 'persona': 'daily_health'})
        persona = branding.get_persona('daily_health')
        assert result['tagline'] == persona['tagline']
        assert result['emoji'] == branding.DEFAULT_EMOJI
        assert result['persona'] == 'daily_health'

    def test_unknown_persona_is_ignored(self):
        result = branding.resolve_branding({'app_name': 'X', 'persona': 'ghost'})
        assert result['persona'] == ''
        assert result['emoji'] == branding.DEFAULT_EMOJI

    def test_enabled_modules_exposed_for_onboarding(self):
        """引导弹窗要拿到现有模块，否则会套用模板冲掉老用户的血糖。"""
        result = branding.resolve_branding({
            'app_name': '爸爸的血糖本',
            'enabled_modules': ['glucose', 'blood_pressure'],
        })
        assert result['enabled_modules'] == ['glucose', 'blood_pressure']

    def test_enabled_modules_empty_means_all(self):
        assert branding.resolve_branding({'app_name': 'X'})['enabled_modules'] == []
        assert branding.resolve_branding(
            {'app_name': 'X', 'enabled_modules': []})['enabled_modules'] == []


class TestNormalizeModules:
    def test_accepts_json_string_from_db(self):
        """user_profiles 里存的是 JSON 字符串，解析后要还原成列表。"""
        assert branding.normalize_modules('["glucose", "weight"]') == ['glucose', 'weight']

    def test_accepts_parsed_list(self):
        assert branding.normalize_modules(['glucose', 'weight']) == ['glucose', 'weight']

    def test_drops_unknown_modules(self):
        """历史脏数据里的未知模块名要丢掉，否则白名单会永久失效。"""
        assert branding.normalize_modules(['glucose', 'legacy_module']) == ['glucose']

    def test_dedupes_preserving_order(self):
        assert branding.normalize_modules(['weight', 'glucose', 'weight']) == ['weight', 'glucose']

    @pytest.mark.parametrize('value', [None, '', '[]', 'not-json', 0, {}, 'null'])
    def test_degenerate_values_become_empty(self, value):
        """空值/坏数据统一返回 []，语义是「不限制 = 全部启用」。"""
        assert branding.normalize_modules(value) == []


class TestBuildPayload:
    def test_valid(self):
        payload, error = branding.build_branding_payload({
            'app_name': ' 妈妈的血糖本 ', 'app_emoji': '💗', 'app_tagline': '家人健康',
            'persona': 'glucose_care', 'enabled_modules': ['glucose', 'weight', 'bogus'],
        })
        assert error is None
        assert payload['app_name'] == '妈妈的血糖本'
        assert payload['enabled_modules'] == ['glucose', 'weight']
        assert payload['persona'] == 'glucose_care'

    def test_name_required(self):
        payload, error = branding.build_branding_payload({'app_name': '   '})
        assert payload is None and '不能为空' in error

    def test_name_too_long(self):
        payload, error = branding.build_branding_payload({'app_name': '长' * (branding.NAME_MAX_LEN + 1)})
        assert payload is None and '最多' in error

    def test_unknown_persona_rejected(self):
        payload, error = branding.build_branding_payload({'app_name': 'X', 'persona': 'ghost'})
        assert payload is None and '使用场景' in error

    def test_modules_must_be_list(self):
        payload, error = branding.build_branding_payload({'app_name': 'X', 'enabled_modules': 'glucose'})
        assert payload is None and '数组' in error

    def test_modules_cannot_be_empty(self):
        payload, error = branding.build_branding_payload({'app_name': 'X', 'enabled_modules': ['bogus']})
        assert payload is None and '至少' in error

    def test_modules_optional(self):
        payload, error = branding.build_branding_payload({'app_name': 'X'})
        assert error is None and 'enabled_modules' not in payload

    def test_none_payload(self):
        payload, error = branding.build_branding_payload(None)
        assert payload is None and error


class TestBrandingApi:
    def test_get_requires_login(self, client):
        resp = client.get('/api/user/branding', headers={'X-Requested-With': 'XMLHttpRequest'})
        assert resp.status_code == 401

    def test_get_defaults(self, client_authenticated):
        resp = client_authenticated.get('/api/user/branding')
        assert resp.status_code == 200
        data = resp.get_json()['data']
        assert data['branding']['app_name'] == branding.DEFAULT_APP_NAME
        assert data['branding']['onboarded'] is False
        assert len(data['personas']) == len(branding.PERSONAS)
        assert data['all_modules'] == branding.ALL_MODULES
        assert data['limits']['name_max_len'] == branding.NAME_MAX_LEN

    def test_post_persists_branding_and_modules(self, client_authenticated):
        resp = client_authenticated.post('/api/user/branding', json={
            'app_name': '老爸健康本',
            'app_emoji': '🏃',
            'app_tagline': '每天一记',
            'persona': 'daily_health',
            'enabled_modules': ['blood_pressure', 'weight', 'exercise'],
        })
        assert resp.status_code == 200
        assert resp.get_json()['data']['branding']['app_name'] == '老爸健康本'

        user = UserManager().get_user(_current_uid(client_authenticated))
        assert user['app_name'] == '老爸健康本'
        assert user['persona'] == 'daily_health'
        assert sorted(user['enabled_modules']) == ['blood_pressure', 'exercise', 'weight']

    def test_post_without_modules_keeps_existing(self, client_authenticated):
        client_authenticated.post('/api/user/modules', json={'enabled_modules': ['glucose', 'weight']})
        client_authenticated.post('/api/user/branding', json={'app_name': '新名字'})

        user = UserManager().get_user(_current_uid(client_authenticated))
        assert user['app_name'] == '新名字'
        assert sorted(user['enabled_modules']) == ['glucose', 'weight']

    def test_post_empty_name_rejected(self, client_authenticated):
        resp = client_authenticated.post('/api/user/branding', json={'app_name': '  '})
        assert resp.status_code == 400
        assert resp.get_json()['error_type'] == 'validation_error'

    def test_get_handles_db_error(self, client_authenticated, monkeypatch):
        from routes import api_user

        def _boom(uid):
            raise RuntimeError('boom')

        monkeypatch.setattr(api_user.user_manager, 'get_user', _boom)
        resp = client_authenticated.get('/api/user/branding')
        assert resp.status_code == 500

    def test_post_handles_db_error(self, client_authenticated, monkeypatch):
        from routes import api_user

        def _boom(*args, **kwargs):
            raise RuntimeError('boom')

        monkeypatch.setattr(api_user.user_manager, 'update_user_profile_partial', _boom)
        resp = client_authenticated.post('/api/user/branding', json={'app_name': 'X'})
        assert resp.status_code == 500


class TestIndexRendersBrand:
    def test_index_uses_default_brand_for_new_user(self, client_authenticated):
        html = client_authenticated.get('/').data.decode('utf-8')
        assert branding.DEFAULT_APP_NAME in html

    def test_index_uses_custom_brand(self, client_authenticated):
        UserManager().update_user_profile_partial(_current_uid(client_authenticated), {
            'app_name': '妈妈的血糖本',
            'app_emoji': '💗',
            'app_tagline': '家人健康我来记',
        })
        html = client_authenticated.get('/').data.decode('utf-8')
        assert '妈妈的血糖本' in html
        assert '家人健康我来记' in html
        # 自定义名称后不再显示原品牌英文 slogan
        assert 'Health for the Whole Family' not in html


class TestNewUserDefaults:
    def test_create_user_defaults_to_base_modules(self, client_authenticated):
        """新成员默认拿到基础模板，不会被血糖面板淹没。"""
        um = UserManager()
        uid = um.create_user('family_member', '家人', {'name': '家人'}, password='pw1234')
        user = um.get_user(uid)
        assert sorted(user['enabled_modules']) == sorted(branding.BASE_MODULES)
        assert 'glucose' not in user['enabled_modules']

    def test_create_user_respects_explicit_modules(self, client_authenticated):
        um = UserManager()
        uid = um.create_user('glucose_user', '爸爸', {'name': '爸爸',
                                                     'enabled_modules': ['glucose', 'medication']},
                             password='pw1234')
        assert sorted(um.get_user(uid)['enabled_modules']) == ['glucose', 'medication']


class TestFrontendContract:
    @staticmethod
    def _index_html():
        with open(os.path.join(REPO_ROOT, 'templates/index.html'), encoding='utf-8') as f:
            return f.read()

    @pytest.mark.parametrize('module', branding.ALL_MODULES)
    def test_all_modules_present_in_frontend(self, module):
        """ALL_MODULES 必须与前端 index.html 的模块列表一致。"""
        assert f"'{module}'" in self._index_html()

    def test_personas_do_not_reference_unknown_modules(self):
        html = self._index_html()
        for p in branding.PERSONAS:
            for m in p['modules']:
                assert f'{m}:' in html


class TestModuleSemanticsConsistency:
    """模块白名单的「空数组」语义必须全链路一致。

    历史 bug：`enabled_modules` 空数组的语义是「从未限制 = 全部启用」。
    首页和 /api/user/modules 都按这个语义解释，但引导弹窗拿到的
    `/api/user/branding` 直接给了 `[]`，前端就以为「一个都没开」，
    于是套用第一个模板（不含血糖），用户点保存就把血糖模块关掉了。
    """

    def test_modules_endpoint_rejects_empty_list(self, client_authenticated):
        """传 [] 想关掉所有模块，不能变成「全部启用」的反向语义。"""
        resp = client_authenticated.post('/api/user/modules', json={'enabled_modules': []})
        assert resp.status_code == 400
        assert resp.json['error_type'] == 'validation_error'
        assert '至少' in resp.json['message']

    def test_modules_endpoint_keeps_modules_after_rejected_empty(self, client_authenticated):
        um = UserManager()
        uid = _current_uid(client_authenticated)
        before = um.get_user(uid)['enabled_modules']
        client_authenticated.post('/api/user/modules', json={'enabled_modules': []})
        assert um.get_user(uid)['enabled_modules'] == before

    def test_branding_response_reflects_saved_modules(self, client_authenticated):
        """只改名字、不提交模块时，响应里的模块不能凭空变成空数组。"""
        client_authenticated.post('/api/user/branding', json={
            'app_name': '妈妈的血糖本',
            'enabled_modules': ['glucose', 'weight'],
        })
        resp = client_authenticated.post('/api/user/branding', json={'app_name': '改个名'})
        assert resp.status_code == 200
        assert resp.json['data']['branding']['enabled_modules'] == ['glucose', 'weight']

    def test_onboarding_treats_empty_modules_as_all_enabled(self):
        """引导弹窗必须把「空」读成「全部启用」，不能读成「全不勾」。"""
        with open(os.path.join(REPO_ROOT, 'templates/index.html'), encoding='utf-8') as f:
            html = f.read()
        assert "const existing = raw.length ? raw : (data.all_modules || []);" in html


class TestTutorialCopyMatchesModules:
    """使用引导不能讲用户没有的功能。"""

    @staticmethod
    def _html():
        with open(os.path.join(REPO_ROOT, 'templates/index.html'), encoding='utf-8') as f:
            return f.read()

    def test_compliance_line_only_for_glucose_users(self):
        """达标率只在血糖面板里存在，无血糖用户不该被告知有这个东西。"""
        html = self._html()
        idx_glucose = html.index("<strong>今日概览</strong>：血糖一天 7 个时间点")
        idx_compliance = html.index("<strong>达标率</strong>")
        idx_endif = html.index('{% endif %}', idx_glucose)
        assert idx_glucose < idx_compliance < idx_endif, '达标率那句必须包在血糖条件里'

    def test_step2_has_fallback_when_no_measurement_module(self):
        """只开用药/餐食的用户，第 2 步不能变成空白页。"""
        html = self._html()
        assert ("{% if 'blood_pressure' not in active_modules and 'weight' not in active_modules "
                "and 'glucose' not in active_modules %}") in html
