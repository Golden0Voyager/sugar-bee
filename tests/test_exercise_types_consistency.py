"""运动类型口径一致性测试。

背景（真实用户反馈）：每日复盘日历里，yzq0535 的运动标签只显示「Xkm」
（跑步），力量训练（Garmin 同步为「健身」，58 条）整类被静默漏掉。
根因是前端各自硬编码 `['跑步', '运动']` 判断运动，而后端口径是 6 类
（跑步/运动/走路/骑行/游泳/健身）。

这里守住：
1. app.js 有唯一的 EXERCISE_TYPES 清单，含全部 6 类；
2. app.js 不再出现 `['跑步', '运动']` 硬编码字面量；
3. 日历徽章对无距离运动（健身等）单独出 🏋️ 徽章；
4. 前端清单与后端 services/timeline_service.py 的口径一致；
5. dashboard_service 7 天运动统计的 WHERE / total_cal 用全量 6 类
   （avg_hr 刻意只算跑步/运动——混入力量训练心率会拉偏均值，不许「顺手统一」）。
"""
import os
import re

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_JS = os.path.join(REPO_ROOT, 'static', 'js', 'app.js')
TIMELINE_SERVICE = os.path.join(REPO_ROOT, 'services', 'timeline_service.py')
DASHBOARD_SERVICE = os.path.join(REPO_ROOT, 'services', 'dashboard_service.py')

CANONICAL = ["跑步", "运动", "走路", "骑行", "游泳", "健身"]


def _read(path):
    with open(path, encoding='utf-8') as f:
        return f.read()


def test_app_js_has_canonical_exercise_types():
    src = _read(APP_JS)
    m = re.search(r"const EXERCISE_TYPES = \[([^\]]*)\]", src)
    assert m, "app.js 必须定义唯一的 EXERCISE_TYPES 清单"
    defined = [t.strip().strip("'\"") for t in m.group(1).split(',')]
    assert defined == CANONICAL


def test_app_js_no_hardcoded_two_type_lists():
    src = _read(APP_JS)
    assert "['跑步', '运动']" not in src, (
        "不许再硬编码 ['跑步', '运动']——力量训练等会漏；统一用 EXERCISE_TYPES"
    )


def test_calendar_badge_covers_strength_training():
    src = _read(APP_JS)
    # 日历运动徽章的过滤口径必须走 EXERCISE_TYPES
    badge_block = re.search(
        r"// 3\. 运动标记 Badge.*?// 4\. 用药打卡", src, re.DOTALL)
    assert badge_block, "找不到日历运动徽章代码块"
    block = badge_block.group(0)
    assert 'EXERCISE_TYPES.includes(e.type)' in block
    # 无距离运动（健身/力量训练）必须单独出徽章，不能被静默吞掉
    assert '🏋️' in block


def test_frontend_backend_exercise_lists_match():
    app_src = _read(APP_JS)
    timeline_src = _read(TIMELINE_SERVICE)
    backend = re.search(r"in (\[[^\]]*跑步[^\]]*\])", timeline_src).group(1)
    for t in CANONICAL:
        assert t in backend, f"后端 timeline_service 口径缺 {t}"
        assert f"'{t}'" in app_src or f'"{t}"' in app_src


def test_dashboard_exercise_stats_use_full_list():
    src = _read(DASHBOARD_SERVICE)
    stats_block = re.search(
        r"# === 3\. 运动统计（7天）\s*===.*?exercise_stats = c\.fetchone\(\)", src, re.DOTALL)
    assert stats_block, "找不到 7 天运动统计查询"
    block = stats_block.group(0)
    # WHERE 和 total_cal 必须用全量 6 类（健身无距离，漏了就整类消失）
    assert "_exercise_types" in block
    for t in CANONICAL:
        assert t in block
    # avg_hr 刻意保持 跑步/运动：混入力量训练心率会把均值拉偏
    assert re.search(r"type = '跑步' OR type = '运动'[^\n]*avg_hr", block)
