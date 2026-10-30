"""骨密度与体检报告页面的 HTML 输出转义回归测试。"""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
# P1b：内联 JS 已拆到 static/js/app.js，页面 JS 函数改从那里读。
HTML = (REPO_ROOT / 'static/js/app.js').read_text(encoding='utf-8')
REPORT_RENDERERS = HTML[
    HTML.index('function renderBone()'):HTML.index('function cancelCheckupPreview()')
]


def test_escape_html_also_escapes_attribute_quotes():
    """同一个helper同时用于文字和双引号属性，引号必须实体化。"""
    assert "return div.innerHTML.replace(/\"/g, '&quot;').replace(/'/g, '&#39;');" in HTML


def test_report_renderers_do_not_interpolate_untrusted_values_directly():
    raw_interpolations = [
        '${r.measure_date}', '${r.site}', '${r.institution}',
        '${m.site}', '${it.name}', '${it.category}',
        '${it.value}', "${it.ref_range || '-'}", "${it.unit || ''}",
    ]
    for raw in raw_interpolations:
        assert raw not in REPORT_RENDERERS


def test_checkup_preview_escapes_text_and_input_value():
    assert '${escapeHtml(it.name)}' in REPORT_RENDERERS
    assert 'value="${escapeHtml(it.value)}"' in REPORT_RENDERERS
    assert "${escapeHtml(it.ref_range || '-')} ${escapeHtml(it.unit || '')}" in REPORT_RENDERERS


def test_report_upload_does_not_silently_drop_selected_files():
    assert 'return await Promise.all(files.map(readFileAsDataUrl));' in REPORT_RENDERERS
    assert 'files.slice(0, REPORT_MAX_FILES)' not in REPORT_RENDERERS


def test_partial_failure_labels_are_escaped_before_toast():
    assert "failed.map(p => escapeHtml(p.label || `第 ${p.page} 页`))" in REPORT_RENDERERS
