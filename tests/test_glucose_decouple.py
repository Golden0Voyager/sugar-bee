"""控糖文案/AI 身份解耦测试。

背景：品牌改成通用健康记录后，未启用血糖模块的用户不应该再被
AI 当成「二型糖尿病患者」对待。这里守住：

1. settings：AI 系统提示词按血糖模块开关切换通用/糖尿病档案；
2. 聊天上下文：非血糖用户不追加近 7 天血糖均值；
3. 健康分析：非血糖用户用全科医生角色、章节按有数据的模块生成；
4. 仪表盘统计：非血糖用户跳过血糖专项查询（前端本就不渲染这些卡片）；
5. 前端模板/JS：血糖快捷提问和血糖导入入口被 `{% if 'glucose' %}` 包住。
"""
import json
import os
import sqlite3
from unittest.mock import MagicMock, patch

import settings
from utils.timezone import now as app_now

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

GLUCOSE_MODULES = ['glucose', 'blood_pressure', 'weight']
NO_GLUCOSE_MODULES = ['blood_pressure', 'weight', 'exercise', 'medication']


def _make_user(db_path, user_id, modules):
    conn = sqlite3.connect(db_path)
    conn.execute(
        'INSERT OR REPLACE INTO app_users (id, username, display_name, is_active) VALUES (?, ?, ?, 1)',
        (user_id, f'user{user_id}', f'用户{user_id}'),
    )
    conn.execute(
        'INSERT OR REPLACE INTO user_profiles (user_id, name, enabled_modules) VALUES (?, ?, ?)',
        (user_id, f'u{user_id}', json.dumps(modules) if modules is not None else None),
    )
    conn.commit()
    conn.close()


class TestIsGlucoseModuleEnabled:
    def test_none_user_id_defaults_enabled(self):
        assert settings.is_glucose_module_enabled(None) is True

    def test_empty_modules_means_all_enabled(self, isolate_db):
        from core.config import get_db_name
        db_path = get_db_name()
        _make_user(db_path, 30, [])
        assert settings.is_glucose_module_enabled(30) is True

    def test_missing_user_defaults_enabled(self, isolate_db):
        assert settings.is_glucose_module_enabled(99999) is True

    def test_without_glucose_returns_false(self, isolate_db):
        from core.config import get_db_name
        db_path = get_db_name()
        _make_user(db_path, 31, NO_GLUCOSE_MODULES)
        assert settings.is_glucose_module_enabled(31) is False

    def test_with_glucose_returns_true(self, isolate_db):
        from core.config import get_db_name
        db_path = get_db_name()
        _make_user(db_path, 32, GLUCOSE_MODULES)
        assert settings.is_glucose_module_enabled(32) is True

    def test_user_manager_error_defaults_enabled(self, isolate_db, monkeypatch):
        """UserManager 查询抛异常时按「全部启用」兜底，不能让 AI 功能崩掉"""
        import user_manager
        monkeypatch.setattr(user_manager, 'UserManager', MagicMock(side_effect=RuntimeError('boom')))
        assert settings.is_glucose_module_enabled(1) is True


class TestAISystemPromptByModule:
    def test_non_glucose_user_gets_generic_profile(self, isolate_db):
        from core.config import get_db_name
        db_path = get_db_name()
        _make_user(db_path, 40, NO_GLUCOSE_MODULES)
        prompt = settings.get_ai_system_prompt(40)
        assert '二型糖尿病' not in prompt, "非血糖用户不能再被写成糖尿病患者"
        assert '健康记录用户' in prompt
        assert '不要主动提及血糖数据' in prompt
        assert 'male' not in prompt and '（男,' in prompt, "性别应显示为中文"

    def test_glucose_user_keeps_diabetic_profile(self, isolate_db):
        from core.config import get_db_name
        db_path = get_db_name()
        _make_user(db_path, 41, GLUCOSE_MODULES)
        prompt = settings.get_ai_system_prompt(41)
        assert '二型糖尿病' in prompt, "血糖用户的糖尿病档案不能变"
        assert '空腹通常在' in prompt

    def test_none_user_id_keeps_legacy_profile(self):
        prompt = settings.get_ai_system_prompt(user_id=None)
        assert '二型糖尿病' in prompt


class TestChatContextNoGlucoseLeak:
    """build_chat_context：非血糖用户不追加近 7 天血糖均值，档案为通用身份"""

    def _insert_glucose_record(self, db_path, user_id, value, timestamp):
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO records (user_id, value, unit, type, notes, timestamp, is_predicted) "
            "VALUES (?, ?, 'mmol/L', '空腹', '', ?, 0)",
            (user_id, value, timestamp),
        )
        conn.commit()
        conn.close()

    def test_non_glucose_user_no_glucose_avg(self, isolate_db):
        from core.config import get_db_name
        from routes.api_chat import build_chat_context
        db_path = get_db_name()
        _make_user(db_path, 50, NO_GLUCOSE_MODULES)
        today_str = app_now().strftime('%Y-%m-%d')
        self._insert_glucose_record(db_path, 50, 6.5, f"{today_str} 07:15:00")
        self._insert_glucose_record(db_path, 50, 6.0, f"{today_str} 12:15:00")

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        ctx = build_chat_context(conn, 50)
        conn.close()

        assert '血糖均值' not in ctx, "非血糖用户不该被塞血糖均值"
        assert '健康记录用户' in ctx, "档案应为通用健康身份"
        assert '二型糖尿病' not in ctx

    def test_glucose_user_still_gets_glucose_avg(self, isolate_db):
        from core.config import get_db_name
        from routes.api_chat import build_chat_context
        db_path = get_db_name()
        _make_user(db_path, 51, GLUCOSE_MODULES)
        today_str = app_now().strftime('%Y-%m-%d')
        self._insert_glucose_record(db_path, 51, 6.5, f"{today_str} 07:15:00")

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        ctx = build_chat_context(conn, 51)
        conn.close()

        assert '血糖均值' in ctx
        assert '二型糖尿病' in ctx


class TestHealthAnalysisGenericRole:
    @patch('services.health_service.AI_AVAILABLE', True)
    @patch('services.health_service.settings.is_glucose_module_enabled', return_value=False)
    @patch('services.health_service.call_ai')
    def test_non_glucose_user_generic_role_and_sections(self, mock_ai, _mock_flag):
        from services.health_service import generate_health_analysis
        mock_ai.return_value = "整体不错\n综合健康得分: 88"
        mock_c = MagicMock()
        mock_c.fetchone.return_value = None
        # fetchall 顺序：glucose, bp, exercise, diet, medications, temp_med, adherence, weight
        mock_c.fetchall.side_effect = [
            [],
            [(120, 80, 72, '2024-06-01 08:00:00', 98)],
            [(5.0, '30min', 145, 175, 350, '5:00', 160, 5000, 42.0, '2024-06-01 07:00:00')],
            [(300, 45.0, 65, '', '', '早餐', '2024-06-01 09:00:00')],
            [('二甲双胍', '500mg', '1', '片', 2, '餐前', 'long_term', '2024-01-01', '')],
            [], [],
            [(70.0, 22.5, '2024-06-01 08:00:00')],
        ]
        mock_db = MagicMock()
        mock_db.cursor.return_value = mock_c

        result = generate_health_analysis(mock_db, user_id=1)
        assert result['success'] is True
        prompt = mock_ai.call_args[0][0]
        assert '糖尿病' not in prompt, "非血糖用户的分析角色不能再是糖尿病专家"
        assert '全科医生和健康管理专家' in prompt
        assert '## 1. 血糖数据' not in prompt, "章节不应再从血糖开始"
        # 章节按有数据的模块动态编号
        assert '## 1. 血压' in prompt
        assert '## 2. 运动' in prompt
        assert '## 3. 饮食' in prompt
        assert '## 4. 体重' in prompt
        assert '## 5. 用药' in prompt

    @patch('services.health_service.AI_AVAILABLE', True)
    @patch('services.health_service.settings.is_glucose_module_enabled', return_value=False)
    @patch('services.health_service.call_ai')
    def test_non_glucose_user_no_data_sections(self, mock_ai, _mock_flag):
        """非血糖用户且各模块都无数据 → 提示词里给出占位说明而不是空章节"""
        from services.health_service import generate_health_analysis
        mock_ai.return_value = "综合健康得分: 70"
        mock_c = MagicMock()
        mock_c.fetchone.return_value = None
        mock_c.fetchall.side_effect = [[], [], [], [], [], [], [], []]
        mock_db = MagicMock()
        mock_db.cursor.return_value = mock_c

        result = generate_health_analysis(mock_db, user_id=1)
        assert result['success'] is True
        prompt = mock_ai.call_args[0][0]
        assert '暂无已启用模块的健康记录' in prompt

    @patch('services.health_service.AI_AVAILABLE', True)
    @patch('services.health_service.settings.is_glucose_module_enabled', return_value=True)
    @patch('services.health_service.call_ai')
    def test_glucose_user_keeps_diabetes_role(self, mock_ai, _mock_flag):
        from services.health_service import generate_health_analysis
        mock_ai.return_value = "综合健康得分: 85"
        mock_c = MagicMock()
        mock_c.fetchone.return_value = None
        mock_c.fetchall.side_effect = [
            [(6.5, '空腹', '2024-06-01 07:15:00', None, None)],
            [], [], [], [], [], [], [],
        ]
        mock_db = MagicMock()
        mock_db.cursor.return_value = mock_c

        result = generate_health_analysis(mock_db, user_id=1)
        assert result['success'] is True
        prompt = mock_ai.call_args[0][0]
        assert '糖尿病管理专家和全科医生' in prompt
        assert '## 1. 血糖数据' in prompt


class TestDashboardSkipsGlucoseQueries:
    @patch('services.dashboard_service.UserManager')
    @patch('services.dashboard_service.settings')
    def test_non_glucose_user_skips_glucose_stats(self, mock_settings, mock_um):
        mock_settings.get_bmi_category.return_value = {'label': '正常', 'color': '#4CAF50'}
        mock_settings.get_badge_for_rate.return_value = {'key': 'encourage', 'icon': '💪'}
        mock_settings.GLUCOSE_TARGETS = {}
        mock_settings.BADGE_SYSTEM = {}
        mock_settings.check_glucose_compliance.return_value = {'is_compliant': True, 'level': 'optimal'}
        mock_settings.calculate_bmi.return_value = 23.0
        mock_um_instance = MagicMock()
        mock_um_instance.is_module_enabled.return_value = False
        mock_um.return_value = mock_um_instance

        # 血糖查询被跳过：fetchone 少 glucose_stats 一项，fetchall 少 compliance_glucose 一项
        mock_c = MagicMock()
        mock_c.fetchone.side_effect = [
            (0,),                # total_records
            (None,) * 4,         # exercise_stats
            None,                # vo2max
            (None,) * 7,         # bp_stats
            None,                # latest_weight
            (None,),             # avg_weight
            None,                # health_analyses
        ]
        mock_c.fetchall.side_effect = [
            [],   # today_weights
            [],   # today_records
            [],   # today_exercises
            [],   # today_bps
            [],   # all_meds
            [],   # taken_logs
            [],   # temp_meds
        ]
        mock_db = MagicMock()
        mock_db.cursor.return_value = mock_c

        from services.dashboard_service import get_dashboard_stats
        result = get_dashboard_stats(mock_db, user_id=1)

        assert result['avg_fasting_7d'] == 0
        assert result['max_glucose_7d'] == 0
        assert result['compliance'] == 0
        # 血糖专项 SQL 一次都不该出现
        executed_sql = ' '.join(call.args[0] for call in mock_c.execute.call_args_list)
        assert 'max_glucose' not in executed_sql
        assert 'check_glucose' not in executed_sql


def _read_source(relpath):
    with open(os.path.join(REPO_ROOT, relpath), encoding='utf-8') as f:
        return f.read()


class TestFrontendGlucoseGating:
    """前端模板/JS 源码级守卫：血糖提问与血糖导入入口必须被模块条件包住"""

    def test_glucose_chip_guarded_in_template(self):
        html = _read_source('templates/index.html')
        chip_pos = html.index("chatSendQuick('我的血糖控制得怎么样？')")
        guard_pos = html.rindex("{% if 'glucose' in active_modules %}", 0, chip_pos)
        assert guard_pos >= 0, "血糖快捷提问必须包在 glucose 模块判断里"

    def test_bp_and_weight_chips_added(self):
        html = _read_source('templates/index.html')
        assert "chatSendQuick('我的血压控制得怎么样？')" in html
        assert "chatSendQuick('最近体重趋势如何？')" in html

    def test_import_card_guarded_and_relabeled(self):
        html = _read_source('templates/index.html')
        import_pos = html.index('导入血糖数据')
        guard_pos = html.rindex("{% if 'glucose' in active_modules %}", 0, import_pos)
        assert guard_pos >= 0, "血糖导入入口必须只对血糖用户显示"

    def test_export_text_is_generic(self):
        html = _read_source('templates/index.html')
        assert '导出当前用户的全部健康记录' in html
        assert '导出当前用户的血糖记录' not in html

    def test_chat_js_chips_module_aware(self):
        js = _read_source('static/js/chat.js')
        assert 'function chatWelcomeHtml()' in js
        assert "mods.includes('glucose')" in js
        assert "mods.includes('blood_pressure')" in js

    def test_ai_input_placeholder_module_aware(self):
        """自然语言录入框的提示例子按模块切换，非血糖用户不再看到「空腹6.5」"""
        html = _read_source('templates/index.html')
        pos = html.index('id="aiInput"')
        snippet = html[pos:pos + 700]
        assert "'glucose' in active_modules" in snippet, "提示例子必须按血糖模块条件切换"
        assert '空腹6.5' in snippet, "血糖用户的例子保留血糖"
        non_glucose_part = snippet.split('{% else %}')[1]
        assert '空腹' not in non_glucose_part, "兜底例子里不该再出现血糖"
