"""新手使用引导（tutorial）的前端契约测试。

引导本身是纯前端逻辑，这里守住三件事：
1. 步骤数量与 `TUTORIAL_TOTAL` 一致（加步骤忘改常量会让最后一步点不掉）；
2. 关键函数齐全，且挂在正确的入口上（跳过品牌引导后也要能弹出来）；
3. 「怎么记才准」的医学要点不能丢——这是引导的核心价值。
"""
import os
import re

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _index_html():
    with open(os.path.join(REPO_ROOT, 'templates/index.html'), encoding='utf-8') as f:
        return f.read()


def _main_css():
    with open(os.path.join(REPO_ROOT, 'static/css/main.css'), encoding='utf-8') as f:
        return f.read()


class TestTutorialModal:
    def test_modal_exists(self):
        assert 'id="tutorialModal"' in _index_html()

    def test_step_count_matches_constant(self):
        """data-step 的最大值必须等于 TUTORIAL_TOTAL。"""
        html = _index_html()
        steps = [int(m) for m in re.findall(r'class="tutorial-step[^"]*" data-step="(\d+)"', html)]
        assert steps, '引导弹窗里没有找到任何步骤'
        total = int(re.search(r'const TUTORIAL_TOTAL = (\d+);', html).group(1))
        assert max(steps) == total
        assert sorted(steps) == list(range(1, total + 1)), '步骤编号必须从 1 连续'

    def test_only_first_step_visible_by_default(self):
        html = _index_html()
        hidden = re.findall(r'class="tutorial-step d-none" data-step="(\d+)"', html)
        visible = re.findall(r'class="tutorial-step" data-step="(\d+)"', html)
        assert visible == ['1']
        assert len(hidden) == int(re.search(r'const TUTORIAL_TOTAL = (\d+);', html).group(1)) - 1

    def test_step_label_present(self):
        assert 'id="tutorialStepLabel"' in _index_html()

    def test_nav_buttons_present(self):
        html = _index_html()
        assert 'id="tutorialPrev"' in html
        assert 'id="tutorialNext"' in html
        assert 'onclick="skipTutorial()"' in html


class TestTutorialLogic:
    @pytest.mark.parametrize('fn', [
        'renderTutorialStep', 'openTutorial', 'tutorialGo',
        'markTutorialSeen', 'closeTutorial', 'tutorialNext',
        'skipTutorial', 'maybeShowTutorial',
    ])
    def test_function_defined(self, fn):
        assert f'function {fn}(' in _index_html()

    def test_seen_flag_is_per_user(self):
        """每个用户单独记「看过没」，同一台设备换人还要再教一次。"""
        html = _index_html()
        assert "return 'sbTutorialSeen:' + (currentUserId || 'self');" in html

    def test_skip_marks_seen(self):
        """点「以后再说」也要记成看过，否则每次登录都弹。"""
        html = _index_html()
        body = html.split('function closeTutorial()')[1].split('function tutorialNext()')[0]
        assert 'markTutorialSeen()' in body

    def test_shown_after_onboarding(self):
        """品牌引导走完/跳过之后，接着弹使用引导。"""
        html = _index_html()
        onboarding = html.split('async function maybeShowOnboarding()')[1].split('async function')[0]
        assert onboarding.count('maybeShowTutorial()') >= 2, '已初始化/已跳过两条路径都要弹'

    def test_skip_onboarding_leads_to_tutorial(self):
        html = _index_html()
        body = html.split('function skipOnboarding()')[1].split('async function saveOnboarding')[0]
        assert 'maybeShowTutorial()' in body

    def test_last_step_button_finishes(self):
        html = _index_html()
        body = html.split('function tutorialNext()')[1].split('function skipTutorial()')[0]
        assert 'closeTutorial()' in body

    def test_reopen_from_user_menu(self):
        """随时能从右上角菜单重看。"""
        assert 'onclick="openTutorial()"' in _index_html()


class TestTutorialContent:
    """引导的核心价值是教会「怎么记准」，这些要点不能被删掉。"""

    @pytest.mark.parametrize('keyword', [
        '静坐',          # 血压测前静坐
        '心脏同高',      # 袖带位置
        '第一口',        # 餐后 2 小时从第一口算
        '空腹',          # 血糖/体重
        '同一台秤',      # 体重可比性
    ])
    def test_measurement_guidance_present(self, keyword):
        assert keyword in _index_html()

    @pytest.mark.parametrize('keyword', ['拍照', '麦克风', '确认保存'])
    def test_recording_channels_present(self, keyword):
        assert keyword in _index_html()

    def test_examples_match_real_input(self):
        """示例要和输入框真实支持的写法一致。"""
        html = _index_html()
        for example in ['空腹 6.5', '血压 135/85', '跑步 5 公里']:
            assert example in html

    def test_mentions_user_switching(self):
        """家人共用一台设备，切换用户必须讲。"""
        html = _index_html()
        assert '切换家人' in html


class TestTutorialStyles:
    @pytest.mark.parametrize('cls', ['tutorial-example', 'tutorial-tip'])
    def test_css_class_defined(self, cls):
        assert f'.{cls}' in _main_css()

    def test_elderly_mode_enlarges_text(self):
        """老人模式要放大引导文字。"""
        css = _main_css()
        assert 'body.elderly-mode .tutorial-tip' in css


class TestTutorialModuleAwareness:
    """引导内容要跟着用户开启的模块走。

    妈妈、岳母没有血糖模块，讲「长按血糖格子」等于讲了个不存在的功能。
    """

    @staticmethod
    def _step_html(n):
        html = _index_html()
        return html.split(f'data-step="{n}"')[1].split('class="tutorial-step')[0]

    @pytest.mark.parametrize('module,keyword', [
        ('glucose', '空腹 6.5'),
        ('blood_pressure', '血压 135/85'),
        ('exercise', '跑步 5 公里'),
        ('medication', '吃了降压药'),
        ('diet', '午饭 米饭 青菜 鱼'),
    ])
    def test_examples_are_conditional(self, module, keyword):
        step1 = self._step_html(1)
        idx = step1.find(keyword)
        assert idx != -1, f'第 1 步缺少示例「{keyword}」'
        # 示例必须被该模块的 if 包裹，否则非该模块用户也会看到
        before = step1[:idx]
        assert f"if '{module}' in active_modules" in before.rsplit('<div', 1)[-1] or \
               f"if '{module}' in active_modules" in before[-200:]

    @pytest.mark.parametrize('module,heading', [
        ('blood_pressure', '❤️ 血压'),
        ('weight', '⚖️ 体重'),
        ('glucose', '🩸 血糖'),
    ])
    def test_measurement_sections_are_conditional(self, module, heading):
        step2 = self._step_html(2)
        idx = step2.find(heading)
        assert idx != -1, f'第 2 步缺少「{heading}」'
        assert f"if '{module}' in active_modules" in step2[:idx][-300:]

    def test_glucose_timeline_line_conditional(self):
        step3 = self._step_html(3)
        idx = step3.find('长按')
        assert idx != -1
        assert "if 'glucose' in active_modules" in step3[:idx][-300:]

    def test_low_frequency_cards_conditional(self):
        step4 = self._step_html(4)
        idx = step4.find('骨密度、体检报告')
        assert idx != -1
        window = step4[:idx][-300:]
        assert "'bone_density' in active_modules" in window
        assert "'checkup' in active_modules" in window

