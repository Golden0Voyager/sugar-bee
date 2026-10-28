"""首页趋势卡片必须跟着模块开关走。

历史 bug：`glucoseMonitorCard` 无条件渲染，没开血糖模块的家人（基础版）打开首页
会看到一张只有绿色目标区间、一条数据都没有的「血糖趋势」图，像是自己的记录丢了。
血压、体重两张卡片是同一个毛病（它们靠「没数据就隐藏」兜底，但只要历史上留过
一条记录，关了模块也照样冒出来）。

这里从真实渲染结果断言三张卡片的 `id` 属性在不在，而不是断言 id 字符串——
JS 里本来就有 `getElementById('bloodPressureChartCard')`，字符串一定存在。
"""
import sqlite3

import branding
from core.config import get_db_name
from user_manager import UserManager


def _set_modules(username, modules):
    """把指定用户的模块白名单改成 `modules`。"""
    conn = sqlite3.connect(get_db_name())
    try:
        row = conn.execute(
            "SELECT id FROM app_users WHERE username = ?", (username,)
        ).fetchone()
    finally:
        conn.close()
    assert row, f'测试用户 {username} 不存在'
    UserManager().set_enabled_modules(row[0], list(modules))


def _home_html(client):
    resp = client.get('/')
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


GLUCOSE_CARD = 'id="glucoseMonitorCard"'
BP_CARD = 'id="bloodPressureChartCard"'
WEIGHT_CARD = 'id="weightChartCard"'


class TestBaseModulesHideGlucoseCard:
    """基础版（BASE_MODULES，不含血糖）不该看到血糖趋势卡片。"""

    def test_base_modules_hide_glucose_card(self, client_authenticated):
        _set_modules('_test', branding.BASE_MODULES)
        html = _home_html(client_authenticated)
        assert GLUCOSE_CARD not in html

    def test_base_modules_keep_bp_and_weight_cards(self, client_authenticated):
        """血压/体重在基础版里是开的，卡片要留着（没数据时由 JS 自己隐藏）。"""
        _set_modules('_test', branding.BASE_MODULES)
        html = _home_html(client_authenticated)
        assert BP_CARD in html
        assert WEIGHT_CARD in html

    def test_glucose_module_shows_card(self, client_authenticated):
        _set_modules('_test', branding.ALL_MODULES)
        html = _home_html(client_authenticated)
        assert GLUCOSE_CARD in html


class TestEveryTrendCardFollowsItsModule:
    """三张趋势卡片各自跟着自己的模块开关，不是只看血糖。"""

    def test_only_exercise_and_diet_hides_all_trend_cards(self, client_authenticated):
        _set_modules('_test', ['exercise', 'diet'])
        html = _home_html(client_authenticated)
        assert GLUCOSE_CARD not in html
        assert BP_CARD not in html
        assert WEIGHT_CARD not in html

    def test_weight_off_hides_weight_card(self, client_authenticated):
        modules = [m for m in branding.ALL_MODULES if m != 'weight']
        _set_modules('_test', modules)
        html = _home_html(client_authenticated)
        assert WEIGHT_CARD not in html
        assert GLUCOSE_CARD in html
        assert BP_CARD in html

    def test_blood_pressure_off_hides_bp_card(self, client_authenticated):
        modules = [m for m in branding.ALL_MODULES if m != 'blood_pressure']
        _set_modules('_test', modules)
        html = _home_html(client_authenticated)
        assert BP_CARD not in html
        assert GLUCOSE_CARD in html
        assert WEIGHT_CARD in html

    def test_empty_modules_means_all_enabled(self, client_authenticated):
        """历史兼容：空数组 = 全部启用，三张卡片都该在。"""
        _set_modules('_test', [])
        html = _home_html(client_authenticated)
        assert GLUCOSE_CARD in html
        assert BP_CARD in html
        assert WEIGHT_CARD in html


class TestChartInitGuards:
    """卡片不渲染时，图表初始化函数必须自己短路，不能抛 TypeError。"""

    def test_init_functions_guard_missing_canvas(self):
        import os

        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(repo_root, 'templates/index.html'), encoding='utf-8') as f:
            html = f.read()

        for fn, canvas in [
            ('initGlucoseChart', 'glucoseChart'),
            ('initBloodPressureChart', 'bloodPressureChart'),
            ('initWeightChart', 'weightChart'),
        ]:
            start = html.index(f'function {fn}(')
            head = html[start:start + 400]
            assert f"if (!document.getElementById('{canvas}')) return;" in head, (
                f'{fn} 缺少 canvas 存在性检查'
            )


class TestGlucoseTrendOnlyPlotsGlucose:
    """趋势图只画真血糖。历史 bug：空腹血压记录的 value 被填成收缩压（124），
    趋势图不过滤，把血压画成 120+ 的「其他」点，Y 轴被拉飞、正常数据挤成一条线。"""

    def test_non_cgm_entries_pass_through_is_glucose_entry(self):
        """非 CGM 记录必须经过与日历/后端均值一致的 isGlucoseEntry 过滤。"""
        import os

        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(repo_root, 'templates/index.html'), encoding='utf-8') as f:
            html = f.read()
        start = html.index('function initGlucoseChart(')
        end = html.index('// 计算x轴边界', start)
        body = html[start:end]
        assert 'dayNonCgmRecords.push(entry)' in body
        assert 'else if (isGlucoseEntry(entry))' in body, (
            '趋势图收集非 CGM 记录时未走 isGlucoseEntry 过滤，血压/运动等会混进血糖图'
        )


# 头像菜单里的入口（首页已不再放卡片）
MENU_MEDS = '💊 药物管理'
MENU_BONE = 'onclick="openBoneModal()"'
MENU_CHECKUP = 'onclick="openCheckupModal()"'


class TestLowFrequencyEntriesInUserMenu:
    """骨密度 / 体检历史一年才用一次，入口收进右上角头像菜单，不占首页版面。"""

    def test_home_no_longer_has_entry_cards(self, client_authenticated):
        _set_modules('_test', branding.ALL_MODULES)
        html = _home_html(client_authenticated)
        assert 'entry-card' not in html
        assert 'onclick="openBoneModal()"' in html  # 但入口还在，只是挪进菜单了

    def test_menu_items_present(self, client_authenticated):
        _set_modules('_test', branding.ALL_MODULES)
        html = _home_html(client_authenticated)
        assert MENU_BONE in html
        assert MENU_CHECKUP in html

    def test_menu_order_below_medication(self, client_authenticated):
        """药物管理 → 骨密度 → 体检历史。"""
        _set_modules('_test', branding.ALL_MODULES)
        html = _home_html(client_authenticated)
        meds = html.index(MENU_MEDS)
        assert meds < html.index(MENU_BONE) < html.index(MENU_CHECKUP)

    def test_entries_follow_their_modules(self, client_authenticated):
        """运动健身模板没有用药/骨密度/体检，三个入口都不该出现。"""
        _set_modules('_test', ['exercise', 'weight', 'diet'])
        html = _home_html(client_authenticated)
        assert MENU_MEDS not in html
        assert MENU_BONE not in html
        assert MENU_CHECKUP not in html

    def test_summary_placeholders_kept_for_js(self, client_authenticated):
        """菜单项里的摘要占位符要留着——JS 靠这两个 id 填「最近一次」信息。"""
        _set_modules('_test', branding.ALL_MODULES)
        html = _home_html(client_authenticated)
        assert 'id="boneEntrySummary"' in html
        assert 'id="checkupEntrySummary"' in html

