"""报告上传解码、PDF 原生文字提取与按需页面渲染。

每次模型调用仍最多接收 ``MAX_IMAGES`` 页，但整份报告不再只取前三页。
PDF 页面保留原始字节，只有需要视觉识别时才渲染 JPEG，避免长报告占用过多内存。
"""

import base64
import io
import unicodedata
from dataclasses import dataclass, field

# 单次模型调用的页数上限；整份报告的安全上限由 MAX_REPORT_PAGES 控制。
MAX_IMAGES = 3
MAX_REPORT_PAGES = 50
PDF_DPI = 150
# 保留旧常量名，语义改为单份 PDF 的安全上限，不再是静默截断页数。
PDF_MAX_PAGES = MAX_REPORT_PAGES

_PDF_MAGIC = b'%PDF-'
_READABLE_PUNCTUATION = set("，。；：、（）()[]{}<>《》/\\|_+-–—=.%‰↑↓~～×*#@:'\"?")


class ReportFileError(ValueError):
    """上传内容无法识别、损坏或超过安全上限。"""


@dataclass
class ReportPage:
    """一页报告；PDF 的 JPEG 采用惰性渲染，文本层只作为解析输入。"""

    file_index: int
    page_number: int
    raw: bytes = field(repr=False)
    is_pdf_page: bool = False
    native_text: str | None = None
    position: int = 0
    _image: bytes | None = field(default=None, init=False, repr=False)

    @property
    def label(self):
        return f'文件 {self.file_index} 第 {self.page_number} 页'

    def render_image(self, dpi=PDF_DPI):
        """返回本页 JPEG；普通照片直接返回原字节。"""
        if self._image is not None:
            return self._image
        if not self.is_pdf_page:
            self._image = self.raw
            return self._image

        import pypdfium2 as pdfium  # 延迟导入：只有视觉识别 PDF 时才需要

        try:
            doc = pdfium.PdfDocument(self.raw)
            try:
                page = doc[self.page_number - 1]
                pil_image = page.render(scale=dpi / 72).to_pil().convert('RGB')
                buf = io.BytesIO()
                pil_image.save(buf, format='JPEG', quality=85)
                self._image = buf.getvalue()
            finally:
                doc.close()
        except Exception as exc:
            raise ReportFileError(f'{self.label}渲染失败，可以改传清晰照片') from exc
        return self._image


def decode_base64_item(item):
    """把 data URL 或裸 base64 解码成字节，并拒绝空结果。"""
    try:
        raw = base64.b64decode(str(item).split(',')[-1])
    except Exception as exc:
        raise ReportFileError('文件数据无法识别，请重新上传') from exc
    if not raw:
        raise ReportFileError('文件数据无法识别，请重新上传')
    return raw


def is_pdf(raw):
    """按文件头判断 PDF，不信任浏览器提供的 mime。"""
    return bool(raw) and raw[:5] == _PDF_MAGIC


def has_reliable_native_text(text):
    """判断文字层是否适合送给文本模型；扫描件和乱码应走视觉识别。"""
    normalized = unicodedata.normalize('NFKC', text or '')
    compact = ''.join(normalized.split())
    if len(compact) < 4:
        return False

    readable = sum(
        char.isalnum() or char in _READABLE_PUNCTUATION
        for char in compact
    )
    if readable / len(compact) < 0.85:
        return False
    if sum(char.isalnum() for char in compact) < 4:
        return False
    # 单个字符反复出现通常不是有效报告文字层。
    return not (len(compact) >= 12 and len(set(compact)) <= 2)


def _extract_native_text(page):
    """文字提取失败不否决整个 PDF，后续仍可使用视觉模型。"""
    text_page = None
    try:
        text_page = page.get_textpage()
        return text_page.get_text_range() or ''
    except Exception:
        return ''
    finally:
        if text_page is not None:
            text_page.close()


def _pdf_to_pages(raw, file_index):
    import pypdfium2 as pdfium

    try:
        doc = pdfium.PdfDocument(raw)
    except Exception as exc:
        raise ReportFileError('PDF 打不开，可能已加密或文件损坏，可以改传截图') from exc

    try:
        page_count = len(doc)
        if not page_count:
            raise ReportFileError('PDF 里没有可识别的页面')
        if page_count > MAX_REPORT_PAGES:
            raise ReportFileError(f'单份 PDF 最多支持 {MAX_REPORT_PAGES} 页，请拆分后上传')
        return [
            ReportPage(
                file_index=file_index,
                page_number=index + 1,
                raw=raw,
                is_pdf_page=True,
                native_text=_extract_native_text(doc[index]),
            )
            for index in range(page_count)
        ]
    finally:
        doc.close()


def to_report_pages(items, max_pages=MAX_REPORT_PAGES):
    """展开所有文件为页面，不静默截断；总页数超限会明确报错。"""
    if not isinstance(items, (list, tuple)):
        raise ReportFileError('文件列表格式不正确，请重新上传')

    pages = []
    for file_index, item in enumerate(items, start=1):
        raw = decode_base64_item(item)
        pages.extend(_pdf_to_pages(raw, file_index) if is_pdf(raw) else [
            ReportPage(file_index=file_index, page_number=1, raw=raw),
        ])
        if len(pages) > max_pages:
            raise ReportFileError(f'一次最多识别 {max_pages} 页，请拆分后上传')

    for position, page in enumerate(pages, start=1):
        page.position = position
    return pages


def pdf_to_images(raw, max_pages=PDF_MAX_PAGES, dpi=PDF_DPI):
    """兼容旧的直接调用：按显式上限渲染 PDF 页面。"""
    pages = _pdf_to_pages(raw, 1)
    if len(pages) > max_pages:
        pages = pages[:max_pages]
    return [page.render_image(dpi=dpi) for page in pages]


def to_model_images(items, max_images=MAX_IMAGES):
    """兼容包装：超过单次上限时拒绝，而不是像旧版那样静默丢页。"""
    pages = to_report_pages(items)
    if len(pages) > max_images:
        raise ReportFileError(f'单次模型调用最多 {max_images} 页，请使用分批解析')
    return [page.render_image() for page in pages]
