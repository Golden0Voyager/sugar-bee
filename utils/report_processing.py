"""报告页面分批解析、文本到视觉回退、顺序合并与失败页元数据。"""

import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal, InvalidOperation

from utils.report_files import MAX_IMAGES, has_reliable_native_text

# 报告页面分批解析的并发度。实测:ModelScope 在 2 并发下会触发
# "Request rate increased too quickly" 的 429 —— 一份 27 页报告丢过 9/27 页;
# 改成串行后,六款模型共 54 批,零 429、零丢页。
# 并发只影响「多页 PDF」这类任务(单张照片本来就只有一批,并发度为 1),
# 而体检报告一年才传一次,多等 1-2 分钟换「不丢页」是划算的。
MAX_PARSE_WORKERS = 1
_NUMBER = re.compile(r'[-+]?\d+(?:\.\d+)?')


class ReportParseError(ValueError):
    """文本模型输出结构不完整，或指标缺乏原文依据。"""


class ReportProcessingError(RuntimeError):
    """所有页面均识别失败，无法形成可信的部分结果。"""


def compact_source_text(value):
    return ''.join(unicodedata.normalize('NFKC', str(value or '')).split()).casefold()


def source_has_value(value, source_text):
    """数值按数值等价比较，其他结果按去空白后的原文比较。"""
    compact_value = compact_source_text(value).replace('−', '-').replace('—', '-')
    if not compact_value:
        return False
    source = compact_source_text(source_text).replace('−', '-').replace('—', '-')
    if re.fullmatch(r'[-+]?\d+(?:\.\d+)?', compact_value):
        try:
            expected = Decimal(compact_value)
        except InvalidOperation:
            return False
        for token in _NUMBER.findall(source):
            try:
                if Decimal(token) == expected:
                    return True
            except InvalidOperation:
                continue
        return False
    return compact_value in source


def _make_batches(pages):
    """相邻且解析方式相同的页面组成一批，每批最多三页。"""
    batches = []
    for page in pages:
        use_text = has_reliable_native_text(page.native_text)
        if not batches or batches[-1][1] != use_text or len(batches[-1][0]) >= MAX_IMAGES:
            batches.append(([], use_text))
        batches[-1][0].append(page)
    return batches


def _parse_batch(batch, text_parser, vision_parser):
    pages, use_text = batch
    if use_text:
        try:
            result = text_parser([page.native_text for page in pages])
            if not isinstance(result, dict):
                raise ReportParseError('文本模型没有返回有效报告结构')
            return result, 'text', None
        except Exception:
            # 文本层质量合格不代表模型输出可信；任何文本失败都回退视觉。
            pass

    try:
        images = [page.render_image() for page in pages]
        result = vision_parser(images, 'image/jpeg')
        if not isinstance(result, dict):
            raise ReportParseError('视觉模型没有返回有效报告结构')
        return result, 'vision', None
    except Exception as exc:
        return None, 'vision', exc


def process_report_pages(
    pages,
    *,
    text_parser,
    vision_parser,
    item_key,
    date_key,
    max_workers=MAX_PARSE_WORKERS,
):
    """并发解析页面批次，再按原页序合并；部分失败必须显式返回。"""
    if not pages:
        raise ReportProcessingError('没有可识别的报告页面')

    batches = _make_batches(pages)
    outcomes = [None] * len(batches)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_indexes = {
            executor.submit(_parse_batch, batch, text_parser, vision_parser): index
            for index, batch in enumerate(batches)
        }
        for future, index in future_indexes.items():
            outcomes[index] = future.result()

    merged = {date_key: '', 'institution': '', item_key: []}
    native_text_pages = []
    vision_pages = []
    failed_pages = []

    for (batch_pages, _), (result, mode, error) in zip(batches, outcomes, strict=True):
        if error is not None:
            for page in batch_pages:
                failed_pages.append({
                    'page': page.position,
                    'file': page.file_index,
                    'source_page': page.page_number,
                    'label': page.label,
                    'error': (str(error) or error.__class__.__name__)[:200],
                })
            continue

        parsed_pages = native_text_pages if mode == 'text' else vision_pages
        parsed_pages.extend(page.position for page in batch_pages)
        if not merged[date_key] and result.get(date_key):
            merged[date_key] = result[date_key]
        if not merged['institution'] and result.get('institution'):
            merged['institution'] = result['institution']
        merged[item_key].extend(result.get(item_key) or [])

    parsed_count = len(native_text_pages) + len(vision_pages)
    if not parsed_count:
        labels = '、'.join(failure['label'] for failure in failed_pages)
        raise ReportProcessingError(f'报告页面均识别失败：{labels}')

    merged.update({
        'total_pages': len(pages),
        'parsed_pages': parsed_count,
        'failed_pages': failed_pages,
        'native_text_pages': native_text_pages,
        'vision_pages': vision_pages,
        'warnings': [
            f'{failure["label"]}识别失败，本页未纳入结果，请重新上传对应页面'
            for failure in failed_pages
        ],
    })
    return merged
