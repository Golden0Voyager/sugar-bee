"""原生文字优先、视觉回退与整份报告分批解析测试。"""

import base64
import io
import threading
import time

import pytest
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

import bone_parser
import checkup_parser
from utils.report_files import (
    MAX_REPORT_PAGES,
    ReportFileError,
    ReportPage,
    has_reliable_native_text,
    to_report_pages,
)
from utils.report_processing import (
    ReportParseError,
    ReportProcessingError,
    process_report_pages,
    source_has_value,
)


def make_pdf(pages=2, text='Total Cholesterol 5.62 mmol/L'):
    buf = io.BytesIO()
    report = canvas.Canvas(buf, pagesize=A4)
    for index in range(pages):
        report.setFont('Helvetica', 16)
        report.drawString(80, 720, f'Checkup Report Page {index + 1}')
        report.drawString(80, 680, text)
        report.showPage()
    report.save()
    return buf.getvalue()


def pdf_item(pages=2):
    encoded = base64.b64encode(make_pdf(pages)).decode()
    return f'data:application/pdf;base64,{encoded}'


def image_page(position, native_text=None):
    return ReportPage(
        file_index=1,
        page_number=position,
        raw=b'image-bytes',
        native_text=native_text,
        position=position,
    )


def checkup_result(name='总胆固醇'):
    return {
        'checkup_date': '',
        'institution': '',
        'items': [{'name': name, 'value': '5.62'}],
    }


class TestNativeTextQuality:
    @pytest.mark.parametrize('text', [
        'Total Cholesterol 5.62 mmol/L',
        '健康体检报告',
        '腰椎 L1-L4 T 值 -2.7 BMD 0.782',
    ])
    def test_reliable_text(self, text):
        assert has_reliable_native_text(text) is True

    @pytest.mark.parametrize('text', [None, '', '12', '\x00\x01\x02\x03\x04', 'aaaaaaaaaaaa'])
    def test_unreliable_text(self, text):
        assert has_reliable_native_text(text) is False

    def test_pdf_native_text_is_extracted_without_rendering(self):
        pages = to_report_pages([pdf_item(2)])
        assert len(pages) == 2
        assert 'Total Cholesterol' in pages[0].native_text
        assert pages[0]._image is None

    def test_all_eight_pdf_pages_are_kept(self):
        pages = to_report_pages([pdf_item(8)])
        assert [page.position for page in pages] == list(range(1, 9))

    def test_over_fifty_pages_is_an_explicit_error(self):
        with pytest.raises(ReportFileError, match='50 页'):
            to_report_pages([pdf_item(MAX_REPORT_PAGES + 1)])

    def test_multiple_files_over_limit_are_an_explicit_error(self):
        item = 'data:image/jpeg;base64,' + base64.b64encode(b'image').decode()
        with pytest.raises(ReportFileError, match='50 页'):
            to_report_pages([item] * (MAX_REPORT_PAGES + 1))

    def test_invalid_file_list_is_rejected(self):
        with pytest.raises(ReportFileError, match='文件列表'):
            to_report_pages('not-a-list')


class TestTextParsers:
    def test_checkup_text_uses_text_model_and_normalizes(self, monkeypatch):
        captured = {}

        def fake_call(prompt, images_data=None, mime_type=None, task_type=None):
            captured['task_type'] = task_type
            captured['images'] = images_data
            return '{"checkup_date":"2026-03-01","items":[{"name":"总胆固醇","value":"5.62","ref_range":"3.1-5.2"}]}'

        monkeypatch.setattr('checkup_parser.call_ai', fake_call)
        result = checkup_parser.parse_checkup_text('总胆固醇 5.62 mmol/L 3.1-5.2')
        assert captured == {'task_type': 'text', 'images': None}
        assert result['items'][0]['status'] == 'high'

    @pytest.mark.parametrize('raw', [
        'not-json',
        '{"items":[{"name":"总胆固醇","value":"9.99"}]}',
        '{"items":[{"name":"虚构项目","value":"5.62"}]}',
        '{"items":[]}',
    ])
    def test_checkup_untrustworthy_text_output_raises(self, monkeypatch, raw):
        monkeypatch.setattr('checkup_parser.call_ai', lambda *args, **kwargs: raw)
        with pytest.raises(ReportParseError):
            checkup_parser.parse_checkup_text('总胆固醇 5.62 mmol/L 3.1-5.2')

    def test_legitimate_empty_cover_does_not_raise(self, monkeypatch):
        monkeypatch.setattr(
            'checkup_parser.call_ai',
            lambda *args, **kwargs: '{"items":[]}',
        )
        assert checkup_parser.parse_checkup_text('Annual Health Checkup Report')['items'] == []

    def test_bone_text_validates_all_measurements(self, monkeypatch):
        monkeypatch.setattr(
            'bone_parser.call_ai',
            lambda *args, **kwargs: '{"measurements":[{"site":"腰椎 L1-L4","t_score":"-2.70","bmd":"0.782"}]}',
        )
        result = bone_parser.parse_bone_text('腰椎 L1-L4 T 值 -2.7 BMD 0.782')
        assert result['measurements'][0]['t_score'] == -2.7

    def test_bone_hallucinated_value_raises(self, monkeypatch):
        monkeypatch.setattr(
            'bone_parser.call_ai',
            lambda *args, **kwargs: '{"measurements":[{"site":"腰椎 L1-L4","t_score":"-9.9"}]}',
        )
        with pytest.raises(ReportParseError):
            bone_parser.parse_bone_text('腰椎 L1-L4 T 值 -2.7 BMD 0.782')

    def test_bone_empty_cover_is_allowed(self, monkeypatch):
        monkeypatch.setattr('bone_parser.call_ai', lambda *args, **kwargs: '{"measurements":[]}')
        assert bone_parser.parse_bone_text('DXA Bone Density Report')['measurements'] == []

    def test_numeric_evidence_accepts_equivalent_decimals(self):
        assert source_has_value('-2.70', 'T score -2.7') is True
        assert source_has_value('-2.71', 'T score -2.7') is False


class TestBatchProcessing:
    def test_reliable_native_pdf_never_calls_vision(self, monkeypatch):
        pages = to_report_pages([pdf_item(2)])
        monkeypatch.setattr(
            ReportPage,
            'render_image',
            lambda self: (_ for _ in ()).throw(AssertionError('不应渲染')),
        )
        result = process_report_pages(
            pages,
            text_parser=lambda texts: checkup_result(),
            vision_parser=lambda images, mime: (_ for _ in ()).throw(AssertionError('不应走视觉')),
            item_key='items',
            date_key='checkup_date',
            max_workers=1,
        )
        assert result['native_text_pages'] == [1, 2]
        assert result['vision_pages'] == []
        assert result['parsed_pages'] == 2

    def test_garbled_native_text_uses_vision(self):
        pages = [image_page(1, '\x00\x01\x02\x03\x04')]
        result = process_report_pages(
            pages,
            text_parser=lambda texts: (_ for _ in ()).throw(AssertionError('不应走文本')),
            vision_parser=lambda images, mime: checkup_result(),
            item_key='items',
            date_key='checkup_date',
        )
        assert result['vision_pages'] == [1]

    def test_text_failure_automatically_falls_back_to_vision(self):
        def fail_text(texts):
            raise ReportParseError('bad output')

        result = process_report_pages(
            [image_page(1, 'Total Cholesterol 5.62 mmol/L')],
            text_parser=fail_text,
            vision_parser=lambda images, mime: checkup_result(),
            item_key='items',
            date_key='checkup_date',
        )
        assert result['native_text_pages'] == []
        assert result['vision_pages'] == [1]

    def test_eight_pages_use_three_three_two_and_keep_order(self):
        pages = to_report_pages([pdf_item(8)])
        batch_sizes = []

        def parse_text(texts):
            batch_sizes.append(len(texts))
            result = checkup_result()
            result['items'] = [
                {'name': text.splitlines()[0], 'value': '5.62'}
                for text in texts
            ]
            return result

        result = process_report_pages(
            pages,
            text_parser=parse_text,
            vision_parser=lambda images, mime: checkup_result(),
            item_key='items',
            date_key='checkup_date',
        )
        assert sorted(batch_sizes) == [2, 3, 3]
        assert len(result['items']) == 8
        assert [item['name'] for item in result['items']] == [
            f'Checkup Report Page {index}' for index in range(1, 9)
        ]

    def test_concurrency_matches_configured_workers(self):
        """实际并发不超过 MAX_PARSE_WORKERS。

        现在配成 1:ModelScope 在 2 并发下会 429,一份 27 页报告丢过 9/27 页,
        串行后零 429。断言引用常量,别再写死数字。
        """
        from utils.report_processing import MAX_PARSE_WORKERS

        lock = threading.Lock()
        active = 0
        maximum = 0

        def parse_vision(images, mime):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.05)
            with lock:
                active -= 1
            return checkup_result()

        result = process_report_pages(
            [image_page(index) for index in range(1, 8)],
            text_parser=lambda texts: checkup_result(),
            vision_parser=parse_vision,
            item_key='items',
            date_key='checkup_date',
        )
        assert maximum == MAX_PARSE_WORKERS
        assert result['parsed_pages'] == 7

    def test_partial_failure_has_page_metadata_and_warning(self):
        calls = 0

        def parse_vision(images, mime):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError('model unavailable')
            return checkup_result()

        result = process_report_pages(
            [image_page(index) for index in range(1, 5)],
            text_parser=lambda texts: checkup_result(),
            vision_parser=parse_vision,
            item_key='items',
            date_key='checkup_date',
            max_workers=1,
        )
        assert result['parsed_pages'] == 3
        assert result['failed_pages'][0]['page'] == 4
        assert result['failed_pages'][0]['source_page'] == 4
        assert '文件 1 第 4 页' in result['warnings'][0]
        assert 'model unavailable' in result['failed_pages'][0]['error']

    def test_all_pages_failed_raises(self):
        def fail_vision(images, mime):
            raise RuntimeError('down')

        with pytest.raises(ReportProcessingError, match='均识别失败'):
            process_report_pages(
                [image_page(1)],
                text_parser=lambda texts: checkup_result(),
                vision_parser=fail_vision,
                item_key='items',
                date_key='checkup_date',
            )

    def test_empty_pages_raise(self):
        with pytest.raises(ReportProcessingError, match='没有可识别'):
            process_report_pages(
                [],
                text_parser=lambda texts: checkup_result(),
                vision_parser=lambda images, mime: checkup_result(),
                item_key='items',
                date_key='checkup_date',
            )


class TestValidationBoundaries:
    def test_empty_and_qualitative_value_evidence(self):
        assert source_has_value('', '阴性') is False
        assert source_has_value('阴性', '尿糖 阴性') is True

    def test_non_dict_text_result_falls_back_to_vision(self):
        result = process_report_pages(
            [image_page(1, 'Total Cholesterol 5.62 mmol/L')],
            text_parser=lambda texts: [],
            vision_parser=lambda images, mime: checkup_result(),
            item_key='items',
            date_key='checkup_date',
        )
        assert result['vision_pages'] == [1]

    def test_non_dict_vision_result_is_a_page_failure(self):
        with pytest.raises(ReportProcessingError):
            process_report_pages(
                [image_page(1)],
                text_parser=lambda texts: checkup_result(),
                vision_parser=lambda images, mime: [],
                item_key='items',
                date_key='checkup_date',
            )

    def test_bone_rejects_invalid_json_structure(self, monkeypatch):
        monkeypatch.setattr('bone_parser.call_ai', lambda *args, **kwargs: '{}')
        with pytest.raises(ReportParseError, match='measurements'):
            bone_parser.parse_bone_text('腰椎 L1-L4 T 值 -2.7')

    def test_bone_rejects_site_not_in_source(self, monkeypatch):
        monkeypatch.setattr(
            'bone_parser.call_ai',
            lambda *args, **kwargs: '{"measurements":[{"site":"股骨颈","t_score":"-2.7"}]}',
        )
        with pytest.raises(ReportParseError, match='部位'):
            bone_parser.parse_bone_text('腰椎 L1-L4 T 值 -2.7')

    def test_bone_rejects_empty_measurements_when_source_has_scores(self, monkeypatch):
        monkeypatch.setattr('bone_parser.call_ai', lambda *args, **kwargs: '{"measurements":[]}')
        with pytest.raises(ReportParseError, match='空列表'):
            bone_parser.parse_bone_text('腰椎 L1-L4 T 值 -2.7')

    def test_checkup_detects_numeric_results_without_units(self):
        assert checkup_parser._native_text_suggests_results('总胆固醇 5.62 3.1-5.2') is True

    def test_non_pdf_image_render_is_cached(self):
        page = image_page(1)
        assert page.render_image() == b'image-bytes'
        assert page.render_image() == b'image-bytes'
