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


class TestEnsureWeightCaptured:
    """体重兜底检测测试"""

    def test_weight_already_present(self):
        """已有体重记录则跳过"""
        records = [{'type': '体重记录', 'weight': 75.0}]
        result = glucose_parser._ensure_weight_captured(records, "体重75kg")
        assert len(result) == 1

    def test_explicit_weight_keyword(self):
        """显式体重关键词"""
        records = [{'type': '空腹', 'value': 6.5, 'datetime': '2024-01-01 07:15:00'}]
        result = glucose_parser._ensure_weight_captured(records, "空腹6.5，体重68.85")
        assert len(result) == 2
        weight_record = [r for r in result if r['type'] == '体重记录']
        assert len(weight_record) == 1
        assert weight_record[0]['weight'] == 68.85

    def test_weight_called(self):
        """称了"""
        records = [{'type': '空腹', 'value': 7.1}]
        result = glucose_parser._ensure_weight_captured(records, "称了74.5")
        assert len(result) == 2
        assert result[1]['weight'] == 74.5

    def test_weight_fallback_datetime_uses_app_timezone(self, monkeypatch):
        """体重兜底记录没有可复用时间时，应使用应用时区当前时间"""
        import utils.timezone as timezone

        monkeypatch.setenv("SUGAR_BEE_TIMEZONE", "Asia/Shanghai")
        monkeypatch.setattr(
            timezone,
            "utc_now",
            lambda: datetime.datetime(2026, 6, 25, 5, 53, 25, tzinfo=datetime.UTC),
        )

        result = glucose_parser._ensure_weight_captured(
            [{'type': '空腹', 'value': 7.1}],
            "此时此刻体重72kg",
        )

        assert result[1]['datetime'] == "2026-06-25 13:53:25"

    def test_weight_with_kg_suffix(self):
        """带 kg 后缀"""
        records = [{'type': '空腹', 'value': 6.0}]
        result = glucose_parser._ensure_weight_captured(records, "75.2kg")
        assert len(result) == 2
        assert result[1]['weight'] == 75.2

    def test_no_weight_data(self):
        """无体重数据"""
        records = [{'type': '空腹', 'value': 6.5}]
        result = glucose_parser._ensure_weight_captured(records, "空腹6.5")
        assert len(result) == 1

    def test_weight_out_of_range(self):
        """体重超出合理范围"""
        records = [{'type': '空腹', 'value': 6.5}]
        result = glucose_parser._ensure_weight_captured(records, "体重15kg")
        assert len(result) == 1  # 不应添加

    def test_weight_with_existing_weight_record(self):
        """已有 weight 字段则跳过"""
        records = [{'type': '空腹', 'value': 6.5, 'weight': 70.0}]
        result = glucose_parser._ensure_weight_captured(records, "体重75kg")
        assert len(result) == 1
"""glucose_parser 扩展测试 — _postprocess_records, _ensure_weight_captured, _infer_meal_type"""


class TestPostprocessRecords:
    """_postprocess_records() 分类修正与字段修正测试"""

    def test_misclassified_exercise_to_meal(self):
        from glucose_parser import _postprocess_records
        records = [{
            'type': '跑步', 'value': 0, 'datetime': '2024-06-01 07:00:00',
            'carbs_grams': 50, 'gi_value': 70, 'diet_analysis': '高GI',
            'distance': None, 'heart_rate': None, 'cadence': None, 'steps': None,
        }]
        result = _postprocess_records(records)
        # Should be reclassified as a meal type (早餐 based on 07:00)
        assert result[0]['type'] in ('早餐', '午餐', '加餐', '晚餐')
        # Exercise fields should be cleared
        assert result[0].get('distance') is None

    def test_bp_heart_rate_to_pulse_rate(self):
        from glucose_parser import _postprocess_records
        records = [{
            'type': '血压测量', 'systolic_pressure': 120, 'diastolic_pressure': 80,
            'heart_rate': 72, 'pulse_rate': None,
        }]
        result = _postprocess_records(records)
        assert result[0]['pulse_rate'] == 72
        assert result[0].get('heart_rate') is None

    def test_bp_spo2_too_low_redirects_to_pulse(self):
        from glucose_parser import _postprocess_records
        records = [{
            'type': '血压测量', 'systolic_pressure': 120, 'diastolic_pressure': 80,
            'spo2': 65, 'pulse_rate': None,
        }]
        result = _postprocess_records(records)
        # spo2=65 is too low → should be moved to pulse_rate
        assert result[0]['pulse_rate'] == 65
        assert result[0].get('spo2') is None

    def test_bp_spo2_normal_kept(self):
        from glucose_parser import _postprocess_records
        records = [{
            'type': '血压测量', 'systolic_pressure': 120, 'diastolic_pressure': 80,
            'spo2': 98, 'pulse_rate': 72,
        }]
        result = _postprocess_records(records)
        assert result[0]['spo2'] == 98
        assert result[0]['pulse_rate'] == 72

    def test_bp_type_inference_fasting(self):
        from glucose_parser import _postprocess_records
        records = [{
            'type': '血压测量', 'systolic_pressure': 120, 'diastolic_pressure': 80,
        }]
        result = _postprocess_records(records, original_text='早空腹血压120/80')
        assert result[0]['type'] == '空腹血压'

    def test_bp_type_inference_postmeal(self):
        from glucose_parser import _postprocess_records
        records = [{
            'type': '血压测量', 'systolic_pressure': 130, 'diastolic_pressure': 75,
        }]
        result = _postprocess_records(records, original_text='餐后血压130/75')
        assert result[0]['type'] == '餐后血压'

    def test_bp_type_no_inference(self):
        from glucose_parser import _postprocess_records
        records = [{'type': '血压测量', 'systolic_pressure': 120, 'diastolic_pressure': 80}]
        result = _postprocess_records(records, original_text='血压120/80')
        assert result[0]['type'] == '血压测量'

    def test_meal_with_exercise_traits_preserved(self):
        from glucose_parser import _postprocess_records
        records = [{
            'type': '跑步', 'value': 0, 'datetime': '2024-06-01 07:00:00',
            'distance': 5.0, 'heart_rate': 145, 'calories': 350,
            'carbs_grams': None, 'gi_value': None, 'diet_analysis': None,
        }]
        result = _postprocess_records(records)
        # Has real exercise data → should stay as 跑步
        assert result[0]['type'] == '跑步'
        assert result[0]['distance'] == 5.0

    def test_heart_rate_already_set_kept(self):
        from glucose_parser import _postprocess_records
        records = [{
            'type': '血压测量', 'systolic_pressure': 120, 'diastolic_pressure': 80,
            'heart_rate': 80, 'pulse_rate': 72,
        }]
        result = _postprocess_records(records)
        # pulse_rate already set → heart_rate should NOT overwrite
        assert result[0]['pulse_rate'] == 72

    def test_empty_records(self):
        from glucose_parser import _postprocess_records
        result = _postprocess_records([])
        assert result == []


class TestEnsureWeightCaptured:
    """_ensure_weight_captured() 体重兜底检测测试"""

    def test_weight_already_present_skips(self):
        from glucose_parser import _ensure_weight_captured
        records = [{'type': '体重记录', 'weight': 70.0, 'datetime': '2024-06-01 07:00:00'}]
        result = _ensure_weight_captured(records, '体重70kg')
        assert len(result) == 1

    def test_explicit_weight_keyword(self):
        from glucose_parser import _ensure_weight_captured
        records = [{'type': '空腹', 'value': 6.5, 'datetime': '2024-06-01 07:15:00'}]
        result = _ensure_weight_captured(records, '体重68.85')
        assert len(result) == 2
        assert result[1]['type'] == '体重记录'
        assert result[1]['weight'] == 68.85

    def test_weight_with_kg_suffix(self):
        from glucose_parser import _ensure_weight_captured
        records = [{'type': '空腹', 'value': 6.0, 'datetime': '2024-06-01 07:00:00'}]
        result = _ensure_weight_captured(records, '今天75kg')
        assert len(result) == 2
        assert result[1]['weight'] == 75

    def test_weight_after_comma(self):
        from glucose_parser import _ensure_weight_captured
        records = [{'type': '空腹', 'value': 6.0, 'datetime': '2024-06-01 07:15:00'}]
        result = _ensure_weight_captured(records, '血压120/80，54.50')
        # 54.50 is in range 40-150, should be captured as weight
        assert len(result) == 2
        assert result[1]['weight'] == 54.50

    def test_no_weight_found(self):
        from glucose_parser import _ensure_weight_captured
        records = [{'type': '空腹', 'value': 6.5, 'datetime': '2024-06-01 07:00:00'}]
        result = _ensure_weight_captured(records, '空腹6.5')
        assert len(result) == 1

    def test_weight_out_of_range_skipped(self):
        from glucose_parser import _ensure_weight_captured
        records = [{'type': '空腹', 'value': 6.0}]
        result = _ensure_weight_captured(records, '体重15kg')
        assert len(result) == 1

    def test_weigh_keyword(self):
        from glucose_parser import _ensure_weight_captured
        records = [{'type': '空腹', 'value': 6.0, 'datetime': '2024-06-01 07:00:00'}]
        result = _ensure_weight_captured(records, '称了74.5公斤')
        assert len(result) == 2
        assert result[1]['weight'] == 74.5


class TestInferMealType:
    """_infer_meal_type() 时间推断测试"""

    def test_breakfast(self):
        from glucose_parser import _infer_meal_type
        assert _infer_meal_type('2024-06-01 07:00:00') == '早餐'
        assert _infer_meal_type('2024-06-01 09:59:00') == '早餐'

    def test_lunch(self):
        from glucose_parser import _infer_meal_type
        assert _infer_meal_type('2024-06-01 11:30:00') == '午餐'
        assert _infer_meal_type('2024-06-01 13:59:00') == '午餐'

    def test_snack(self):
        from glucose_parser import _infer_meal_type
        assert _infer_meal_type('2024-06-01 15:00:00') == '加餐'
        assert _infer_meal_type('2024-06-01 16:59:00') == '加餐'

    def test_dinner(self):
        from glucose_parser import _infer_meal_type
        assert _infer_meal_type('2024-06-01 18:00:00') == '晚餐'
        assert _infer_meal_type('2024-06-01 23:00:00') == '晚餐'

    def test_invalid_datetime_defaults_to_lunch(self):
        from glucose_parser import _infer_meal_type
        assert _infer_meal_type('invalid') == '午餐'
        assert _infer_meal_type('') == '午餐'
