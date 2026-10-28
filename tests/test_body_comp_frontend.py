"""体成分前端锚点与首页渲染测试。"""
from pathlib import Path

INDEX = Path(__file__).resolve().parents[1] / "templates" / "index.html"

BODY_COMP_FIELDS = (
    'body_fat_rate', 'visceral_fat', 'muscle_mass', 'body_fat_mass',
    'skeletal_muscle_mass', 'protein_mass', 'basal_metabolism',
)


def test_weight_panel_has_body_comp_row_anchors():
    html = INDEX.read_text(encoding="utf-8")
    for anchor in ('id="bodyCompRow"', 'id="bodyCompTiles"', 'id="bodyCompMore"',
                   'id="bodyCompExpand"', 'id="bodyCompBody"'):
        assert anchor in html, f"缺少 {anchor}"
    for metric in BODY_COMP_FIELDS:
        assert f'data-metric="{metric}"' in html, f"缺少 {metric} 的格子"


def test_body_comp_row_is_collapsed_by_default():
    """默认只显示体重管理:体成分容器收起,展开后才是全部 7 项。"""
    html = INDEX.read_text(encoding="utf-8")
    assert 'id="bodyCompBody" style="display:none;"' in html
    assert '展开更多数据' in html
    assert '展开查看趋势' not in html


def test_toggle_body_comp_details_exists():
    html = INDEX.read_text(encoding="utf-8")
    assert 'function toggleBodyCompDetails' in html
    body = html.split('function toggleBodyCompDetails')[1].split('function ')[0]
    assert "getElementById('bodyCompBody')" in body
    assert 'display' in body


def test_index_renders_body_comp_row_when_data_present(client_authenticated, app):
    from utils.db import get_db
    with client_authenticated.session_transaction() as sess:
        uid = sess['current_user_id']
    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO records (user_id, value, unit, type, timestamp, weight, "
                   "body_fat_rate, visceral_fat, muscle_mass) "
                   "VALUES (?, 0, 'mmol/L', '体重记录', '2026-10-27 07:15:00', 70.45, 21.7, 9, 52.3)",
                   (uid,))
        db.commit()
    html = client_authenticated.get('/').data.decode('utf-8')
    assert 'id="bodyCompRow"' in html
    assert '体成分' in html
    # 趋势卡的指标页签与「更多」下拉项都在渲染结果里
    for metric in ('weight_fat', 'visceral_fat', 'muscle_mass', 'body_fat_mass',
                   'skeletal_muscle_mass', 'protein_mass', 'basal_metabolism'):
        assert f'data-weight-metric="{metric}"' in html


def test_index_hides_body_comp_row_without_data(client_authenticated, app):
    """有体重但没有体成分时,体成分行不得渲染(spec M9:按核心三项判断,不是按体重判断)。"""
    from utils.db import get_db
    with client_authenticated.session_transaction() as sess:
        uid = sess['current_user_id']
    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO records (user_id, value, unit, type, timestamp, weight) "
                   "VALUES (?, 0, 'mmol/L', '体重记录', '2026-10-27 07:15:00', 70.45)", (uid,))
        db.commit()
    html = client_authenticated.get('/').data.decode('utf-8')
    assert 'id="bodyCompRow"' not in html
    # 纯体重用户看到的仍是合并前那张卡:没有页签、没有图例
    assert 'id="weightMetricTabs"' not in html
    assert 'id="weightChartLegend"' not in html
    assert '体重 / 体成分趋势</h5>' not in html


def test_secondary_metric_without_value_is_not_clickable(client_authenticated, app):
    """没测过的次级指标:置灰,且不给 href/onclick,键盘也激活不了。"""
    from utils.db import get_db
    with client_authenticated.session_transaction() as sess:
        uid = sess['current_user_id']
    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO records (user_id, value, unit, type, timestamp, weight, body_fat_rate) "
                   "VALUES (?, 0, 'mmol/L', '体重记录', '2026-10-27 07:15:00', 70.45, 21.7)", (uid,))
        db.commit()
    html = client_authenticated.get('/').data.decode('utf-8')
    # 体脂量没值 -> span + disabled;不能出现可点击的 a 标签
    assert '<span class="dropdown-item disabled" data-weight-metric="body_fat_mass"' in html
    assert '<a class="dropdown-item" href="#" data-weight-metric="body_fat_mass"' not in html


def test_body_comp_trend_merged_into_weight_card():
    """趋势只保留一张卡:体重卡里加指标页签,不再有独立的体成分卡。"""
    html = INDEX.read_text(encoding="utf-8")
    assert 'id="bodyCompChartCard"' not in html
    assert 'id="bodyCompChart"' not in html
    for anchor in ('id="weightMetricTabs"', 'id="weightChart"',
                   'id="weightChartEmpty"', 'id="weightMoreMenu"',
                   'function selectWeightMetric', 'function initWeightChart'):
        assert anchor in html, f"缺少 {anchor}"
    for metric in ('weight_fat', 'visceral_fat', 'muscle_mass'):
        assert f'data-weight-metric="{metric}"' in html, f"缺少 {metric} 页签"
    # 次级 4 项由 Jinja 循环生成,源文件里以字段名清单出现
    for metric in ('body_fat_mass', 'skeletal_muscle_mass', 'protein_mass', 'basal_metabolism'):
        assert f"('{metric}'," in html, f"缺少 {metric} 下拉项"


def test_weight_chart_reads_thresholds_from_stats():
    """阈值只能来自后端下发,不得在模板里写死数值(spec: 阈值单一来源)。"""
    html = INDEX.read_text(encoding="utf-8")
    assert 'stats.body_comp.thresholds' in html
    assert 'BODY_COMP_THRESHOLDS' in html


def test_weight_chart_guards_missing_canvas():
    """模块关闭或缺卡时 initWeightChart 必须安全返回。"""
    html = INDEX.read_text(encoding="utf-8")
    body = html.split('function initWeightChart')[1].split('function ')[0]
    assert "getElementById('weightChart')" in body
    assert 'return' in body


def test_secondary_metrics_are_greyed_without_values():
    """没测过的次级指标在下拉里置灰,避免点开是空图。"""
    html = INDEX.read_text(encoding="utf-8")
    assert 'stats.body_comp.latest[field] is none' in html
    assert 'aria-disabled="true"' in html


def test_select_weight_metric_keeps_current_range():
    """切页签只换指标,不能把时间范围和翻页重置回默认。"""
    html = INDEX.read_text(encoding="utf-8")
    body = html.split('function selectWeightMetric')[1].split('function toggleBodyCompDetails')[0]
    assert 'initWeightChart(currentWeightDays, weightOffset)' in body


def test_empty_range_keeps_card_and_tabs():
    """区间无数据时保留卡片与全部导航，纯体重用户也不能被卡在空区间。"""
    html = INDEX.read_text(encoding="utf-8")
    body = html.split('function initWeightChart')[1].split('// Get point border color')[0]
    assert "document.getElementById('weightChartCard').style.display = ''" in body
    assert "document.getElementById('weightChartCard').style.display = 'none'" not in body
    assert "setChartEmptyState('weightChart', 'weightChartEmpty', isEmpty)" in body


def test_weight_chart_card_does_not_clip_dropdowns():
    """空状态下卡片变矮,不能让 .card 的 overflow:hidden 把下拉菜单裁掉。"""
    css = (INDEX.parents[1] / "static" / "css" / "main.css").read_text(encoding="utf-8")
    assert '#weightChartCard' in css
    rule = css.split('#weightChartCard')[1].split('}')[0]
    assert 'overflow: visible' in rule
