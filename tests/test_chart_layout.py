"""图表高度口径回归测试。

背景:血糖图容器 ``.chart-box`` 曾写死像素高度(桌面 340px / 移动端 250px),
而血压、心率、体重图统一走 Chart.js 的 ``maintainAspectRatio``
(比例来自 ``Chart.defaults.aspectRatio``:桌面 2、移动端 1.1),
结果是血糖图在手机和桌面上都明显比其它图矮,高度口径不一致。

契约:血糖 / CGM 图容器的高度必须由同一个比例变量 ``--chart-aspect-ratio``
决定,不得再写死像素高度。
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MAIN_CSS = REPO_ROOT / "static" / "css" / "main.css"
INDEX_HTML = REPO_ROOT / "static/js/app.js"


def _chart_box_block() -> str:
    """取出 main.css 中 .chart-box 规则体。"""
    css = MAIN_CSS.read_text(encoding="utf-8")
    match = re.search(r"\.chart-box\s*\{([^}]*)\}", css)
    assert match, "main.css 里应存在 .chart-box 规则"
    return match.group(1)


def test_chart_box_height_follows_shared_aspect_ratio():
    """血糖/CGM 图容器高度由共享比例变量决定。"""
    assert "aspect-ratio: var(--chart-aspect-ratio" in _chart_box_block()


def test_chart_box_has_no_hardcoded_pixel_height():
    """不得再给 .chart-box 写死像素高度(那是高度不齐的根因)。"""
    assert not re.search(r"height\s*:\s*\d+px", _chart_box_block())
    assert "height: 340px" not in MAIN_CSS.read_text(encoding="utf-8")


def test_index_publishes_aspect_ratio_to_css():
    """index.html 把 Chart.defaults.aspectRatio 写入 CSS 变量,保持单一来源。"""
    html = INDEX_HTML.read_text(encoding="utf-8")
    assert "Chart.defaults.aspectRatio" in html
    assert "setProperty('--chart-aspect-ratio'" in html
