"""报告文件解码（utils/report_files.py）测试。

覆盖两类真实场景：
1. 医院导出的 PDF 报告（视觉模型只吃图片，必须服务端渲染）；
2. 用户一次选多张照片（后端限制 3 张，多出来的要截断而不是报错）。
"""
import base64
import io

import pytest
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from utils.report_files import (
    MAX_IMAGES,
    ReportFileError,
    decode_base64_item,
    is_pdf,
    pdf_to_images,
    to_model_images,
)


def make_pdf(pages=2, text='Cholesterol 5.62 mmol/L'):
    """造一个多页 PDF，用于验证渲染页数与内容。"""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    for index in range(pages):
        c.setFont('Helvetica', 18)
        c.drawString(80, 720, f'Checkup Report Page {index + 1}')
        c.drawString(80, 680, text)
        c.showPage()
    c.save()
    return buf.getvalue()


def data_url(raw, mime='image/jpeg'):
    return f'data:{mime};base64,{base64.b64encode(raw).decode()}'


class TestDecodeBase64Item:
    def test_data_url(self):
        assert decode_base64_item(data_url(b'abc')) == b'abc'

    def test_bare_base64(self):
        assert decode_base64_item(base64.b64encode(b'abc').decode()) == b'abc'

    def test_garbage_raises(self):
        # b64decode 对 '@@@' 不报错、只返回空字节，必须显式拦掉
        with pytest.raises(ReportFileError):
            decode_base64_item('@@@not-base64@@@')

    def test_empty_raises(self):
        with pytest.raises(ReportFileError):
            decode_base64_item('')


class TestIsPdf:
    def test_pdf_magic(self):
        assert is_pdf(make_pdf(1)) is True

    def test_jpeg_is_not_pdf(self):
        assert is_pdf(b'\xff\xd8\xff\xe0jpeg-bytes') is False

    def test_empty(self):
        assert is_pdf(b'') is False


class TestPdfToImages:
    def test_renders_all_pages(self):
        images = pdf_to_images(make_pdf(2))
        assert len(images) == 2
        # 渲染出来的是 JPEG
        assert images[0][:2] == b'\xff\xd8'

    def test_respects_max_pages(self):
        """5 页的 PDF 只取前 2 页，避免一次塞爆请求体。"""
        images = pdf_to_images(make_pdf(5), max_pages=2)
        assert len(images) == 2

    def test_corrupt_pdf_raises(self):
        with pytest.raises(ReportFileError):
            pdf_to_images(b'%PDF-1.4 not really a pdf')

    def test_rendered_image_is_readable(self):
        from PIL import Image

        image = Image.open(io.BytesIO(pdf_to_images(make_pdf(1))[0]))
        assert image.width > 500 and image.height > 500


class TestToModelImages:
    def test_images_pass_through(self):
        raw = b'\xff\xd8\xff\xe0jpeg'
        assert to_model_images([data_url(raw)]) == [raw]

    def test_pdf_expanded_to_pages(self):
        images = to_model_images([data_url(make_pdf(2), 'application/pdf')])
        assert len(images) == 2

    def test_total_capped(self):
        """3 张图 + 1 个多页 PDF，总数仍然不超过 MAX_IMAGES。"""
        raw = b'\xff\xd8\xff\xe0jpeg'
        items = [data_url(raw) for _ in range(3)] + [data_url(make_pdf(3), 'application/pdf')]
        assert len(to_model_images(items)) == MAX_IMAGES

    def test_pdf_cannot_exceed_cap(self):
        """单个多页 PDF 也不能超过上限。"""
        assert len(to_model_images([data_url(make_pdf(6), 'application/pdf')])) == MAX_IMAGES

    def test_bad_item_raises(self):
        with pytest.raises(ReportFileError):
            to_model_images(['@@@'])

    def test_empty_list(self):
        assert to_model_images([]) == []
