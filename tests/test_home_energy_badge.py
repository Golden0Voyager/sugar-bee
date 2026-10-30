"""能量统计（减糖减脂 / 能量缺口）只在真记了饮食或运动时显示。

历史 bug：后端恒按 `净热量 = 摄入 - (基础代谢 + 运动消耗)` 计算，用户什么都没记
的那天 `净热量 = 0 - 基础代谢` 也恒为负，于是日历徽章和每日详情里天天写着
「👍 减糖减脂 1417」「⚖️ 能量缺口 1417」，等于告诉用户「你今天躺着就减了 1417 大卡」。

三个渲染点共用 `hasEnergyData()`，这个测试守住「三处都加了判断」和「判断口径一致」。
前端没有单测环境，所以这里断言源码契约（同 test_tutorial.py 的做法）。
"""
import os
import re

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _index_html():
    with open(os.path.join(REPO_ROOT, 'static/js/app.js'), encoding='utf-8') as f:
        return f.read()


class TestHasEnergyDataHelper:
    def test_helper_defined(self):
        assert 'function hasEnergyData(stats)' in _index_html()

    def test_helper_checks_intake_or_exercise(self):
        html = _index_html()
        start = html.index('function hasEnergyData(stats)')
        body = html[start:start + 400]
        assert 'cal_in' in body
        assert 'cal_out_exercise' in body

    def test_helper_tolerates_missing_stats(self):
        """stats 缺字段（老数据/接口变更）时不能抛错，按「没有能量数据」处理。"""
        html = _index_html()
        start = html.index('function hasEnergyData(stats)')
        body = html[start:start + 400]
        assert 'if (!stats) return false;' in body


class TestAllThreePlacesGuarded:
    """日历徽章、日视图侧栏 chips、详情弹窗统计行，三处都要判断。"""

    def test_calendar_badge_uses_helper(self):
        html = _index_html()
        # 找到能量徽章那段（在 generateCalendarEvents 里，紧跟血糖徽章之后）
        start = html.index('// 2. 能量状态 Badge')
        block = html[start:start + 300]
        assert 'if (hasEnergyData(stats))' in block
        assert 'stats.net_calories !== 0' not in block, (
            '净热量恒不为 0，用它当开关等于天天显示'
        )

    def test_day_view_chips_guarded(self):
        html = _index_html()
        start = html.index('// ── 1. 概要统计 Chips ──')
        block = html[start:start + 300]
        assert 'if (hasEnergyData(stats))' in block

    def test_day_modal_stats_guarded(self):
        html = _index_html()
        start = html.index('// 1. Render Stats Header')
        block = html[start:start + 400]
        assert 'if (hasEnergyData(stats))' in block

    def test_helper_used_exactly_three_times(self):
        """多一处调用就要确认那一处也是「有没有能量数据」的语义。"""
        calls = re.findall(r'if \(hasEnergyData\(stats\)\)', _index_html())
        assert len(calls) == 3, f'hasEnergyData 调用点数量变了：{len(calls)}'
