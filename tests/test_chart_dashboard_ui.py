"""健康趋势图的统一视觉、空状态与可访问性契约。"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
INDEX = REPO_ROOT / "templates" / "index.html"
CSS = REPO_ROOT / "static" / "css" / "main.css"

CHART_CANVASES = (
    "predictionComparisonChart",
    "glucoseChart",
    "cgmChart",
    "bloodPressureChart",
    "pulseChart",
    "weightChart",
    "boneTrendChart",
)


def _function_body(html: str, name: str, next_name: str) -> str:
    return html.split(f"function {name}", 1)[1].split(f"function {next_name}", 1)[0]


def test_every_chart_has_accessible_name_and_stable_empty_state():
    html = INDEX.read_text(encoding="utf-8")
    for canvas_id in CHART_CANVASES:
        match = re.search(rf'<canvas id="{canvas_id}"[^>]*>', html)
        assert match, f"缺少 {canvas_id}"
        canvas = match.group(0)
        assert 'role="img"' in canvas, f"{canvas_id} 缺少读屏角色"
        assert 'aria-label="' in canvas, f"{canvas_id} 缺少读屏名称"
        assert "height=" not in canvas, f"{canvas_id} 不应独立写死高度"


def test_all_charts_share_theme_and_reduced_motion():
    html = INDEX.read_text(encoding="utf-8")
    assert html.count("= createHealthChart(") == 8  # 血糖有正常/空区间两个配置
    assert "maintainAspectRatio: true" not in html
    assert "prefers-reduced-motion: reduce" in html
    assert "const isContinuous = canvas.id === 'cgmChart'" in html
    assert "tension: isContinuous ? 0.25 : 0" in html


def test_prediction_empty_state_does_not_destroy_canvas():
    html = INDEX.read_text(encoding="utf-8")
    body = _function_body(html, "renderPredictionComparisonChart", "renderComparisonStats")
    assert "parentElement.innerHTML" not in body
    assert "setChartEmptyState('predictionComparisonChart'" in body
    assert "predictionComparisonChart = null" in body


def test_empty_ranges_keep_navigation_available():
    html = INDEX.read_text(encoding="utf-8")
    bp_body = _function_body(html, "initBloodPressureChart", "initWeightChart")
    weight_body = _function_body(html, "initWeightChart", "getPointBorderColor")
    assert "bloodPressureChartCard').style.display = 'none'" not in bp_body
    assert "weightChartCard').style.display = 'none'" not in weight_body
    assert "setChartEmptyState('bloodPressureChart'" in bp_body
    assert "setChartEmptyState('weightChart'" in weight_body


def test_cgm_recent_range_does_not_silently_jump_to_stale_data():
    html = INDEX.read_text(encoding="utf-8")
    body = _function_body(html, "initCgmChart", "initBloodPressureChart")
    assert "latestCgm" not in body
    assert "自动锚定" not in body
    assert "该区间暂无连续血糖记录，可点击左上角 ← 查看更早数据" in body


def test_chart_controls_have_touch_targets_and_visible_focus():
    css = CSS.read_text(encoding="utf-8")
    assert "min-width: 44px" in css
    assert "min-height: 44px" in css
    assert ".btn-overview-nav:focus-visible" in css
    assert "outline: 3px solid rgba(99, 102, 241, 0.3)" in css
