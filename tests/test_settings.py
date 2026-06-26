"""
settings.py 配置和工具函数测试
"""
from unittest.mock import MagicMock, patch

import settings


class TestGlucoseValidation:
    """血糖值验证测试"""

    def test_is_valid_glucose_normal(self):
        """测试正常血糖值"""
        assert settings.is_valid_glucose(5.6) is True
        assert settings.is_valid_glucose(2.0) is True
        assert settings.is_valid_glucose(25.0) is True

    def test_is_valid_glucose_too_low(self):
        """测试过低的血糖值"""
        assert settings.is_valid_glucose(1.9) is False

    def test_is_valid_glucose_too_high(self):
        """测试过高的血糖值"""
        assert settings.is_valid_glucose(25.1) is False

    def test_is_valid_glucose_none(self):
        """测试 None 输入"""
        assert settings.is_valid_glucose(None) is False

    def test_is_valid_glucose_string(self):
        """测试字符串数字"""
        assert settings.is_valid_glucose("5.6") is True

    def test_is_valid_glucose_invalid_string(self):
        """测试无效字符串"""
        assert settings.is_valid_glucose("abc") is False


class TestPredictionValidation:
    """预测值验证测试"""

    def test_is_valid_prediction_fasting(self):
        """测试空腹预测范围"""
        assert settings.is_valid_prediction(5.0, 'fasting') is True
        assert settings.is_valid_prediction(3.0, 'fasting') is False
        assert settings.is_valid_prediction(11.0, 'fasting') is False

    def test_is_valid_prediction_postmeal(self):
        """测试餐后预测范围"""
        assert settings.is_valid_prediction(10.0, 'postmeal') is True
        assert settings.is_valid_prediction(16.0, 'postmeal') is False

    def test_is_valid_prediction_post_exercise(self):
        """测试运动后预测范围"""
        assert settings.is_valid_prediction(8.0, 'post_exercise') is True

    def test_is_valid_prediction_general(self):
        """测试通用预测范围"""
        assert settings.is_valid_prediction(6.0, 'unknown_type') is True
        assert settings.is_valid_prediction(6.0) is True  # default

    def test_is_valid_prediction_none(self):
        """测试 None"""
        assert settings.is_valid_prediction(None) is False

    def test_is_valid_prediction_invalid(self):
        """测试无效值"""
        assert settings.is_valid_prediction("abc") is False


class TestGlucoseTargets:
    """血糖达标标准测试"""

    def test_get_fasting_target(self):
        target = settings.get_glucose_target('空腹')
        assert target['name'] == '空腹血糖'
        assert target['min'] == 4.4
        assert target['max'] == 7.0

    def test_get_postmeal_1h_target(self):
        target = settings.get_glucose_target('餐后1小时')
        assert target['max'] == 11.1

    def test_get_postmeal_2h_target(self):
        target = settings.get_glucose_target('餐后2小时')
        assert target['max'] == 10.0

    def test_get_postmeal_generic_target(self):
        """餐后不带小时 → 默认餐后2小时"""
        target = settings.get_glucose_target('餐后')
        assert target['name'] == '餐后2小时血糖'

    def test_get_premeal_target(self):
        target = settings.get_glucose_target('餐前')
        assert target['name'] == '餐前血糖'

    def test_get_dinner_pre_target(self):
        target = settings.get_glucose_target('晚饭前')
        assert target['name'] == '餐前血糖'

    def test_get_bedtime_target(self):
        target = settings.get_glucose_target('睡前')
        assert target['name'] == '睡前血糖'

    def test_get_post_exercise_target(self):
        target = settings.get_glucose_target('运动后')
        assert target['name'] == '运动后血糖'

    def test_get_default_target(self):
        target = settings.get_glucose_target('未知类型')
        assert target['name'] == '血糖'

    def test_get_none_target(self):
        target = settings.get_glucose_target(None)
        assert target['name'] == '血糖'

    def test_get_empty_target(self):
        target = settings.get_glucose_target('')
        assert target['name'] == '血糖'

    def test_postmeal_1h_lowercase(self):
        """餐后1h（小写）"""
        target = settings.get_glucose_target('餐后1h')
        assert target['max'] == 11.1

    def test_postmeal_2h_lowercase(self):
        target = settings.get_glucose_target('餐后2h')
        assert target['max'] == 10.0

    def test_dinner_pre_alt(self):
        """晚餐前"""
        target = settings.get_glucose_target('晚餐前')
        assert target['name'] == '餐前血糖'

    def test_blood_pressure_not_confused(self):
        """含'血压'的类型不匹配血糖"""
        target = settings.get_glucose_target('空腹血压')
        assert target['name'] == '血糖'  # 不匹配空腹


class TestCompliance:
    """血糖达标检查测试"""

    def test_optimal(self):
        result = settings.check_glucose_compliance(5.5, '空腹')
        assert result['is_compliant'] is True
        assert result['is_optimal'] is True
        assert result['level'] == 'optimal'

    def test_acceptable(self):
        result = settings.check_glucose_compliance(6.5, '空腹')
        assert result['is_compliant'] is True
        assert result['is_optimal'] is False
        assert result['level'] == 'acceptable'

    def test_high(self):
        result = settings.check_glucose_compliance(8.0, '空腹')
        assert result['is_compliant'] is False
        assert result['level'] == 'high'

    def test_low(self):
        result = settings.check_glucose_compliance(3.0, '空腹')
        assert result['is_compliant'] is False
        assert result['level'] == 'low'

    def test_strict_mode(self):
        """严格模式使用 optimal_max"""
        result = settings.check_glucose_compliance(6.5, '空腹', strict=True)
        assert result['is_compliant'] is False  # 6.5 > optimal_max 6.1


class TestBadgeSystem:
    """徽章系统测试"""

    def test_gold_badge(self):
        badge = settings.get_badge_for_rate(100)
        assert badge['key'] == 'gold'
        assert badge['icon'] == '🥇'

    def test_silver_badge(self):
        badge = settings.get_badge_for_rate(85)
        assert badge['key'] == 'silver'

    def test_bronze_badge(self):
        badge = settings.get_badge_for_rate(70)
        assert badge['key'] == 'bronze'

    def test_encourage_badge(self):
        badge = settings.get_badge_for_rate(50)
        assert badge['key'] == 'encourage'

    def test_zero_rate(self):
        badge = settings.get_badge_for_rate(0)
        assert badge['key'] == 'encourage'


class TestBMI:
    """BMI 计算测试"""

    def test_calculate_bmi_normal(self):
        bmi = settings.calculate_bmi(70, 170)
        assert abs(bmi - 24.2) < 0.1

    def test_calculate_bmi_no_height(self):
        """无身高返回 None"""
        bmi = settings.calculate_bmi(70)
        assert bmi is None

    def test_calculate_bmi_zero_weight(self):
        bmi = settings.calculate_bmi(0, 170)
        assert bmi is None

    def test_calculate_bmi_negative_height(self):
        bmi = settings.calculate_bmi(70, -10)
        assert bmi is None

    def test_get_bmi_category_underweight(self):
        cat = settings.get_bmi_category(17.0)
        assert cat['label'] == '偏瘦'

    def test_get_bmi_category_normal(self):
        cat = settings.get_bmi_category(22.0)
        assert cat['label'] == '正常'

    def test_get_bmi_category_overweight(self):
        cat = settings.get_bmi_category(26.0)
        assert cat['label'] == '超重'

    def test_get_bmi_category_obese(self):
        cat = settings.get_bmi_category(30.0)
        assert cat['label'] == '肥胖'

    def test_get_bmi_category_none(self):
        cat = settings.get_bmi_category(None)
        assert cat['label'] == '未知'


class TestConfigConstants:
    """配置常量测试"""

    def test_default_profile_keys(self):
        assert 'name' in settings.DEFAULT_PROFILE
        assert 'weight' in settings.DEFAULT_PROFILE
        assert 'height' in settings.DEFAULT_PROFILE
        assert 'birth_year' in settings.DEFAULT_PROFILE
        assert 'gender' in settings.DEFAULT_PROFILE

    def test_default_target_keys(self):
        assert 'fasting_min' in settings.DEFAULT_TARGET
        assert 'fasting_max' in settings.DEFAULT_TARGET

    def test_default_meals_keys(self):
        assert 'breakfast' in settings.DEFAULT_MEALS
        assert 'lunch' in settings.DEFAULT_MEALS
        assert 'dinner' in settings.DEFAULT_MEALS

    def test_glucose_targets_completeness(self):
        """所有达标标准完整"""
        for key in ['fasting', 'premeal', 'postmeal_1h', 'postmeal_2h', 'bedtime', 'post_exercise', 'default']:
            t = settings.GLUCOSE_TARGETS[key]
            assert 'min' in t
            assert 'max' in t
            assert 'optimal_max' in t
            assert 'name' in t

    def test_badge_system_order(self):
        """徽章按达标率降序"""
        keys = list(settings.BADGE_SYSTEM.keys())
        assert keys == ['gold', 'silver', 'bronze', 'encourage']

    def test_glucose_range_keys(self):
        for key in ['valid', 'fasting_prediction', 'postmeal_prediction', 'post_exercise_prediction', 'general_prediction']:
            assert key in settings.GLUCOSE_RANGE

    def test_emoji_user_map(self):
        assert settings.EMOJI_USER_MAP == {"🐯": 6, "🐰": 1}
        assert settings.USER_EMOJI_MAP == {6: "🐯", 1: "🐰"}

    def test_ai_model_config(self):
        """AI 模型配置完整性"""
        assert 'text' in settings.MODELSCOPE_MODELS
        assert 'vision' in settings.MODELSCOPE_MODELS
        assert 'report' in settings.MODELSCOPE_MODELS


class TestGetAISystemPrompt:
    """AI 系统提示词测试"""

    def test_with_none_user_id(self):
        prompt = settings.get_ai_system_prompt(user_id=None)
        assert '二型糖尿病' in prompt
        assert '空腹通常在' in prompt

    def test_daily_routine(self):
        assert '07:15 空腹' in settings.DAILY_ROUTINE
        assert '22:00 睡前' in settings.DAILY_ROUTINE


class TestBMRCalculation:
    """BMR 计算测试"""

    @patch('settings.load_config')
    def test_calculate_bmr_male(self, mock_load):
        mock_load.return_value = {
            'birth_year': 1964,
            'gender': 'male',
            'weight': 75,
            'height': 170,
        }
        bmr = settings.calculate_bmr(1)
        # BMR = 10*75 + 6.25*170 - 5*age + 5
        assert bmr > 0

    @patch('settings.load_config')
    def test_calculate_bmr_female(self, mock_load):
        mock_load.return_value = {
            'birth_year': 1990,
            'gender': 'female',
            'weight': 60,
            'height': 165,
        }
        bmr = settings.calculate_bmr(1)
        # BMR = 10*60 + 6.25*165 - 5*age - 161
        assert bmr > 0


class TestLoadConfig:
    """load_config 测试"""

    def test_load_config_no_user_id(self):
        config = settings.load_config()
        assert config['name'] == '用户'
        assert 'target' in config
        assert 'glucose_pattern' in config
        assert 'default_meals' in config

    @patch('user_manager.UserManager')
    def test_load_config_with_user_id(self, mock_um_cls):
        mock_um = MagicMock()
        mock_um.get_user_config.return_value = {
            'name': '测试用户',
            'birth_year': 1980,
            'gender': 'male',
            'height': 175,
            'weight': 80,
        }
        mock_um_cls.return_value = mock_um

        config = settings.load_config(user_id=1)
        assert config['name'] == '测试用户'
        # 缺失字段应补齐
        assert 'default_meals' in config
        assert 'target' in config
        assert 'glucose_pattern' in config

    @patch('user_manager.UserManager')
    def test_load_config_with_user_id_no_missing(self, mock_um_cls):
        """用户配置完整时不应覆盖"""
        mock_um = MagicMock()
        mock_um.get_user_config.return_value = {
            'name': '完整用户',
            'default_meals': {'custom': True},
            'target': {'fasting_min': 4.0},
            'glucose_pattern': {'fasting_range': '5.0-6.0'},
        }
        mock_um_cls.return_value = mock_um

        config = settings.load_config(user_id=1)
        assert config['default_meals'] == {'custom': True}
        assert config['target'] == {'fasting_min': 4.0}
        assert config['glucose_pattern'] == {'fasting_range': '5.0-6.0'}
