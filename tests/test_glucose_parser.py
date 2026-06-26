"""
glucose_parser.py 非 AI 函数测试
"""
import datetime
import glucose_parser


class TestPreprocessRelativeDates:
    """相对日期预处理测试"""

    def test_relative_dates_use_app_timezone(self, monkeypatch):
        """相对日期应基于应用时区，而不是服务器 UTC 日期"""
        import utils.timezone as timezone

        monkeypatch.setenv("SUGAR_BEE_TIMEZONE", "Asia/Shanghai")
        monkeypatch.setattr(
            timezone,
            "utc_now",
            lambda: datetime.datetime(2026, 12, 31, 18, 30, 0, tzinfo=datetime.UTC),
        )

        result = glucose_parser._preprocess_relative_dates("昨天空腹6.5")

        assert "2026年12月31日" in result

    def test_replace_days_ago(self):
        datetime.datetime.now()
        result = glucose_parser._preprocess_relative_dates("60天前测的")
        assert "天前" not in result
        assert "年" in result

    def test_replace_yesterday(self):
        result = glucose_parser._preprocess_relative_dates("昨天空腹6.5")
        assert "昨天" not in result
        assert "年" in result

    def test_replace_day_before_yesterday(self):
        result = glucose_parser._preprocess_relative_dates("前天测得")
        assert "前天" not in result

    def test_replace_three_days_ago(self):
        result = glucose_parser._preprocess_relative_dates("大前天")
        assert "大前天" not in result

    def test_replace_last_week(self):
        result = glucose_parser._preprocess_relative_dates("上周运动后5.8")
        assert "上周" not in result

    def test_replace_last_month(self):
        result = glucose_parser._preprocess_relative_dates("上个月的数据")
        assert "上个月" not in result

    def test_empty_text(self):
        result = glucose_parser._preprocess_relative_dates("")
        assert result == ""

    def test_no_relative_dates(self):
        result = glucose_parser._preprocess_relative_dates("今天空腹6.5")
        assert "今天" in result

    def test_multiple_relative_dates(self):
        result = glucose_parser._preprocess_relative_dates("前天和3天前")
        assert "前天" not in result
        assert "天前" not in result


class TestSplitByEmoji:
    """Emoji 拆分测试"""

    def test_no_emoji(self):
        results = glucose_parser.split_by_emoji("空腹血糖6.5")
        assert len(results) == 1
        assert results[0]['user_id'] is None
        assert results[0]['text'] == "空腹血糖6.5"

    def test_single_emoji_tiger(self):
        results = glucose_parser.split_by_emoji("🐯空腹6.5，餐后7.2")
        assert len(results) == 1
        assert results[0]['user_id'] == 6
        assert "空腹6.5" in results[0]['text']

    def test_single_emoji_rabbit(self):
        results = glucose_parser.split_by_emoji("🐰早餐后8.0")
        assert results[0]['user_id'] == 1

    def test_multiple_emojis(self):
        results = glucose_parser.split_by_emoji("🐯空腹6.5 🐰餐后7.2")
        assert len(results) == 2
        assert results[0]['user_id'] == 6
        assert results[1]['user_id'] == 1

    def test_text_before_emoji(self):
        results = glucose_parser.split_by_emoji("今天数据 🐯空腹6.5")
        assert len(results) == 2
        assert results[0]['user_id'] is None
        assert results[1]['user_id'] == 6

    def test_empty_text(self):
        results = glucose_parser.split_by_emoji("")
        assert len(results) == 1
        assert results[0]['user_id'] is None
        assert results[0]['text'] == ""

    def test_custom_emoji_map(self):
        custom_map = {"🐻": 3}
        results = glucose_parser.split_by_emoji("🐻空腹5.0", emoji_map=custom_map)
        assert results[0]['user_id'] == 3

    def test_emoji_no_text_after(self):
        """emoji 后无有效文本"""
        results = glucose_parser.split_by_emoji("🐯")
        assert len(results) == 1
        assert results[0]['user_id'] is None

    def test_unknown_emoji(self):
        """不在映射中的 emoji"""
        results = glucose_parser.split_by_emoji("🍎空腹6.0")
        assert len(results) == 1
        assert results[0]['user_id'] is None


class TestParseGlucoseInputTime:
    """parse_glucose_input 时间上下文测试"""

    def test_prompt_current_time_uses_app_timezone(self, monkeypatch):
        """AI 提示词里的当前录入时间应使用应用时区"""
        import utils.timezone as timezone

        captured = {}

        def fake_call_ai(prompt, images_data=None, mime_type=None):
            captured["prompt"] = prompt
            return "[]"

        monkeypatch.setenv("SUGAR_BEE_TIMEZONE", "Asia/Shanghai")
        monkeypatch.setattr(
            timezone,
            "utc_now",
            lambda: datetime.datetime(2026, 6, 25, 5, 53, 25, tzinfo=datetime.UTC),
        )
        monkeypatch.setattr(glucose_parser, "call_ai", fake_call_ai)

        glucose_parser.parse_glucose_input("此时此刻体重72kg")
        assert "当前录入时间: 2026-06-25 13:53:25" in captured["prompt"]


class TestInferMealType:
    """餐食类型推断测试"""

    def test_breakfast(self):
        assert glucose_parser._infer_meal_type("2024-01-01 07:00:00") == '早餐'
        assert glucose_parser._infer_meal_type("2024-01-01 09:59:00") == '早餐'

    def test_lunch(self):
        assert glucose_parser._infer_meal_type("2024-01-01 11:00:00") == '午餐'
        assert glucose_parser._infer_meal_type("2024-01-01 13:59:00") == '午餐'

    def test_snack(self):
        assert glucose_parser._infer_meal_type("2024-01-01 14:00:00") == '加餐'
        assert glucose_parser._infer_meal_type("2024-01-01 16:59:00") == '加餐'

    def test_dinner(self):
        assert glucose_parser._infer_meal_type("2024-01-01 18:00:00") == '晚餐'
        assert glucose_parser._infer_meal_type("2024-01-01 22:00:00") == '晚餐'

    def test_invalid_format(self):
        """无效格式默认午餐"""
        assert glucose_parser._infer_meal_type("invalid") == '午餐'
        assert glucose_parser._infer_meal_type("") == '午餐'


class TestPostprocessRecords:
    """后处理测试"""

    def test_fix_meal_misclassified_as_exercise(self):
        """被误标为运动的餐食记录应该修正"""
        records = [{
            'type': '跑步',
            'carbs_grams': 50,
            'gi_value': 65,
            'diet_analysis': '中等GI',
            'datetime': '2024-01-01 12:00:00',
            'distance': None,
            'heart_rate': None,
        }]
        result = glucose_parser._postprocess_records(records)
        assert result[0]['type'] == '午餐'  # 根据时间推断

    def test_fix_misclassified_no_exercise_data(self):
        """无运动数据的记录不应保留运动字段"""
        records = [{
            'type': '运动',
            'carbs_grams': 45,
            'datetime': '2024-01-01 08:00:00',
            'distance': None,
            'heart_rate': None,
            'cadence': None,
            'steps': None,
        }]
        result = glucose_parser._postprocess_records(records)
        assert result[0]['type'] == '早餐'

    def test_keep_valid_exercise_record(self):
        """有效的运动记录不应被修改"""
        records = [{
            'type': '跑步',
            'distance': 5.0,
            'heart_rate': 140,
            'datetime': '2024-01-01 07:00:00',
        }]
        result = glucose_parser._postprocess_records(records)
        assert result[0]['type'] == '跑步'
        assert result[0]['distance'] == 5.0

    def test_keep_meal_record(self):
        """餐食记录不应被修改"""
        records = [{
            'type': '午餐',
            'carbs_grams': 50,
            'value': 0,
        }]
        result = glucose_parser._postprocess_records(records)
        assert result[0]['type'] == '午餐'

    def test_fix_bp_pulse_in_spo2(self):
        """血氧值<90应为脉搏"""
        records = [{
            'type': '血压测量',
            'systolic_pressure': 120,
            'diastolic_pressure': 80,
            'spo2': 72,  # 不可能是血氧
        }]
        result = glucose_parser._postprocess_records(records)
        assert result[0]['pulse_rate'] == 72
        assert result[0].get('spo2') is None

    def test_fix_bp_heart_rate_to_pulse(self):
        """血压记录中 heart_rate 应移到 pulse_rate"""
        records = [{
            'type': '血压测量',
            'systolic_pressure': 120,
            'diastolic_pressure': 80,
            'heart_rate': 65,
        }]
        result = glucose_parser._postprocess_records(records)
        assert result[0]['pulse_rate'] == 65
        assert result[0].get('heart_rate') is None

    def test_fix_bp_type_from_text(self):
        """根据原始文本修正血压类型"""
        records = [{
            'type': '血压测量',
            'systolic_pressure': 137,
            'diastolic_pressure': 73,
        }]
        result = glucose_parser._postprocess_records(records, "早晨空腹血压137/73")
        assert result[0]['type'] == '空腹血压'

    def test_fix_bp_type_postmeal(self):
        records = [{
            'type': '血压测量',
            'systolic_pressure': 130,
            'diastolic_pressure': 75,
        }]
        result = glucose_parser._postprocess_records(records, "餐后血压130/75")
        assert result[0]['type'] == '餐后血压'

    def test_keep_valid_spo2(self):
        """正常血氧值应保留"""
        records = [{
            'type': '血压测量',
            'systolic_pressure': 120,
            'diastolic_pressure': 80,
            'spo2': 98,
        }]
        result = glucose_parser._postprocess_records(records)
        assert result[0]['spo2'] == 98
    def test_empty_records(self):
        assert glucose_parser._postprocess_records([]) == []

    def test_bp_type_no_inference(self):
        """泛泛文本不推断血压类型"""
        records = [{'type': '血压测量', 'systolic_pressure': 120, 'diastolic_pressure': 80}]
        result = glucose_parser._postprocess_records(records, original_text='血压120/80')
        assert result[0]['type'] == '血压测量'

    def test_heart_rate_already_set_not_overwritten(self):
        """已有 pulse_rate 则 heart_rate 不应覆盖"""
        records = [{
            'type': '血压测量', 'systolic_pressure': 120, 'diastolic_pressure': 80,
            'heart_rate': 80, 'pulse_rate': 72,
        }]
        result = glucose_parser._postprocess_records(records)
        assert result[0]['pulse_rate'] == 72
