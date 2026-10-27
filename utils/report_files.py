"""报告上传文件解码：把前端传来的 base64 统一变成模型能吃的图片字节。

体检报告、骨密度报告常常是医院导出的 PDF，而不是照片。视觉模型（Qwen3-VL）
只接受图片，所以 PDF 要在服务端先渲染成图片再送进去。

约定：
- 单个请求最多处理 ``MAX_IMAGES`` 张图（PDF 按页展开后一起算），避免一次塞太多
  导致请求体过大、模型超时；
- 解码失败抛 ``ReportFileError``，由路由层转成 400，绝不静默丢掉用户的文件。
"""

import base64
import io

# 与 /api/checkup/parse、/api/bone/parse 的限制保持一致
MAX_IMAGES = 3

# PDF 渲染参数：150 DPI 对化验单上的小字已经够清楚，再高只会让请求体暴涨
PDF_DPI = 150
# PDF 单文件最多取前几页——体检报告的关键指标通常在前两页
PDF_MAX_PAGES = 3

_PDF_MAGIC = b'%PDF-'


class ReportFileError(ValueError):
    """上传内容无法识别（不是图片、不是 PDF、或 base64 损坏）。"""


def decode_base64_item(item):
    """把 `data:image/jpeg;base64,xxx` 或裸 base64 解码成字节。

    ``b64decode`` 对 `@@@` 这类垃圾串不报错、只返回空字节，所以要显式拦掉空结果。
    """
    try:
        raw = base64.b64decode(str(item).split(',')[-1])
    except Exception as exc:
        raise ReportFileError('文件数据无法识别，请重新上传') from exc
    if not raw:
        raise ReportFileError('文件数据无法识别，请重新上传')
    return raw


def is_pdf(raw):
    """按文件头判断是不是 PDF（不信前端传的 mime，安卓/微信常常给空或 octet-stream）。"""
    return bool(raw) and raw[:5] == _PDF_MAGIC


def pdf_to_images(raw, max_pages=PDF_MAX_PAGES, dpi=PDF_DPI):
    """把 PDF 前几页渲染成 JPEG 字节列表。

    PDF 损坏或加密无法打开时抛 ``ReportFileError``，提示用户改传截图。
    """
    import pypdfium2 as pdfium  # 延迟导入：只在真的传了 PDF 时才需要

    try:
        doc = pdfium.PdfDocument(raw)
    except Exception as exc:
        raise ReportFileError('PDF 打不开，可能已加密或文件损坏，可以改传截图') from exc

    try:
        page_count = min(len(doc), max_pages)
        images = []
        for index in range(page_count):
            try:
                page = doc[index]
                pil_image = page.render(scale=dpi / 72).to_pil().convert('RGB')
            except Exception as exc:
                raise ReportFileError('PDF 页面渲染失败，可以改传截图') from exc
            buf = io.BytesIO()
            pil_image.save(buf, format='JPEG', quality=85)
            images.append(buf.getvalue())
    finally:
        doc.close()

    if not images:
        raise ReportFileError('PDF 里没有可识别的页面')
    return images


def to_model_images(items, max_images=MAX_IMAGES):
    """把前端提交的 base64 列表转成模型可用的图片字节列表。

    图片原样通过，PDF 按页展开，总数截断到 ``max_images``。
    """
    images = []
    for item in items:
        if len(images) >= max_images:
            break
        raw = decode_base64_item(item)
        if is_pdf(raw):
            images.extend(pdf_to_images(raw, max_pages=max_images - len(images)))
        else:
            images.append(raw)
    return images[:max_images]
