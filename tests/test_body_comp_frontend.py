"""体成分前端锚点与首页渲染测试。"""
from pathlib import Path

INDEX = Path(__file__).resolve().parents[1] / "templates" / "index.html"


def test_weight_panel_has_body_comp_row_anchors():
    html = INDEX.read_text(encoding="utf-8")
    for anchor in ('id="bodyCompRow"', 'id="bodyCompTiles"', 'id="bodyCompMore"',
                   'id="bodyCompExpand"'):
        assert anchor in html, f"缺少 {anchor}"
    for metric in ('body_fat_rate', 'visceral_fat', 'muscle_mass'):
        assert f'data-metric="{metric}"' in html


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


def test_body_comp_card_anchors_and_js():
    html = INDEX.read_text(encoding="utf-8")
    for anchor in ('id="bodyCompChartCard"', 'id="bodyCompChart"',
                   'id="bodyCompRangeDropdown"', 'id="bodyCompNavNext"',
                   'id="bodyCompEmpty"',
                   'function initBodyCompChart', 'function changeBodyCompRange',
                   'function bodyCompNav', 'function selectBodyCompTab'):
        assert anchor in html, f"缺少 {anchor}"
    for metric in ('body_fat_rate', 'visceral_fat', 'muscle_mass'):
        assert f'data-body-comp-tab="{metric}"' in html


def test_body_comp_chart_reads_thresholds_from_stats():
    """阈值只能来自后端下发,不得在模板里写死数值(spec: 阈值单一来源)。"""
    html = INDEX.read_text(encoding="utf-8")
    assert 'stats.body_comp.thresholds' in html
    assert 'BODY_COMP_THRESHOLDS' in html


def test_body_comp_chart_guards_missing_canvas():
    """模块关闭或缺卡时 initBodyCompChart 必须安全返回(照 initWeightChart 的兜底)。"""
    html = INDEX.read_text(encoding="utf-8")
    body = html.split('function initBodyCompChart')[1].split('function changeBodyCompRange')[0]
    assert "getElementById('bodyCompChart')" in body
    assert 'return' in body
