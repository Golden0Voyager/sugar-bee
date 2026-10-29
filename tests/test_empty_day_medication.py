"""无记录日期也要连续显示长期用药方案。

真实问题：2026-10-09 / 2026-10-10 没有运动等健康记录时，
每日复盘直接显示“当天无记录”，长期用药方案跟着消失。
这里守住三层契约：
1. 后端把当前启用的用药方案单独传给首页；
2. 前端能按 start_date/end_date 为任意日期匹配当日用药；
3. 日历徽标、桌面日视图、手机/弹窗详情都走同一套补齐逻辑。
"""
from pathlib import Path

from services.timeline_service import get_active_medication_plans

TEMPLATE = Path(__file__).resolve().parent.parent / 'templates' / 'index.html'


class _FakeCursor:
    def __init__(self, rows):
        self.rows = rows
        self.sql = None

    def execute(self, sql, params):
        self.sql = sql
        self.params = params

    def fetchall(self):
        return self.rows


class TestGetActiveMedicationPlans:
    def test_returns_dict_rows(self):
        cursor = _FakeCursor([{
            'id': 1,
            'medication_name': '二甲双胍',
            'start_date': '2026-02-23',
            'end_date': None,
            'is_active': 1,
        }])
        plans = get_active_medication_plans(cursor, 3)
        assert plans[0]['medication_name'] == '二甲双胍'
        assert 'is_active = 1' in cursor.sql
        assert cursor.params == (3,)


class TestEmptyDayMedicationFrontend:
    @staticmethod
    def _html():
        return TEMPLATE.read_text(encoding='utf-8')

    def test_backend_injects_all_active_plans(self):
        html = self._html()
        assert 'const ACTIVE_MEDICATION_PLANS = {{ medication_plans | tojson }};' in html

    def test_empty_day_synthetic_node(self):
        html = self._html()
        assert 'function getMedicationPlansForDate(dateStr)' in html
        assert 'function getDayDataForDate(dateStr)' in html
        assert 'isEmptyDay: true' in html
        assert "end = plan.end_date || '9999-12-31'" in html

    def test_day_views_use_shared_lookup(self):
        html = self._html()
        # showDayStats + renderDayDetails 都必须走补齐逻辑，不能只改一处
        assert html.count('getDayDataForDate(dateStr)') >= 2

    def test_calendar_adds_med_badge_for_empty_days(self):
        html = self._html()
        assert 'function pushMedicationBadge(events, dateStr, medPlans)' in html
        assert '无记录日期也补用药打卡徽标' in html
        assert 'if (!loadedDates.has(dateStr))' in html

    def test_labels_use_selected_day_not_today(self):
        html = self._html()
        assert '当日用药（' in html
        assert '💊 当日用药方案' in html
