"""文件导入回归测试：三诺中文列、CGM 模式、单位换算与重复检查。"""
import io

import pandas as pd
import pytest

from utils.db import get_db


def _upload(client, content, filename, **form):
    return client.post(
        "/import",
        data={"file": (io.BytesIO(content), filename), **form},
        content_type="multipart/form-data",
    )


def _rows():
    return get_db().cursor().execute(
        "SELECT value, unit, type, timestamp, notes FROM records ORDER BY timestamp, id"
    ).fetchall()


def _content(frame, extension):
    if extension == "csv":
        return frame.to_csv(index=False).encode("utf-8-sig")
    buffer = io.BytesIO()
    frame.to_excel(buffer, index=False)
    return buffer.getvalue()


@pytest.mark.parametrize("extension", ["csv", "xlsx"])
def test_sannuo_columns_import_as_cgm(client_authenticated, extension):
    frame = pd.DataFrame({
        "血糖时间": ["2024-06-01 07:15:00", "2024-06-01 07:18:00"],
        "血糖值 mmol/L": ["5.8", "6.1"],
        "说明": ["", ""],
    })

    result = _upload(
        client_authenticated, _content(frame, extension), f"cgm.{extension}",
        mode="cgm", unit="mmol/L",
    )

    assert result.status_code == 200
    assert result.json["data"] == {"imported": 2, "skipped": 0}
    assert [tuple(row) for row in _rows()] == [
        (5.8, "mmol/L", "CGM", "2024-06-01 07:15:00", ""),
        (6.1, "mmol/L", "CGM", "2024-06-01 07:18:00", ""),
    ]


def test_cgm_mode_overrides_source_type(client_authenticated):
    content = "value,type,timestamp\n5.8,空腹,2024-06-01 07:15:00\n".encode("utf-8-sig")

    result = _upload(
        client_authenticated, content, "cgm.csv", mode="cgm", unit="mmol/L"
    )

    assert result.json["data"] == {"imported": 1, "skipped": 0}
    assert [tuple(row) for row in _rows()] == [
        (5.8, "mmol/L", "CGM", "2024-06-01 07:15:00", "")
    ]


def test_mg_dl_is_converted_before_range_validation(client_authenticated):
    content = "value,type,timestamp\n108,CGM,2024-06-01 07:15:00\n".encode("utf-8-sig")

    result = _upload(
        client_authenticated, content, "cgm.csv", mode="cgm", unit="mg/dL"
    )

    assert result.json["data"] == {"imported": 1, "skipped": 0}
    assert _rows()[0]["value"] == pytest.approx(6.0)
    assert _rows()[0]["unit"] == "mmol/L"


def test_duplicate_import_is_skipped(client_authenticated):
    content = "value,type,timestamp\n5.8,CGM,2024-06-01 07:15:00\n".encode("utf-8-sig")

    first = _upload(client_authenticated, content, "cgm.csv", mode="cgm", unit="mmol/L")
    second = _upload(client_authenticated, content, "cgm.csv", mode="cgm", unit="mmol/L")

    assert first.json["data"] == {"imported": 1, "skipped": 0}
    assert second.json["data"] == {"imported": 0, "skipped": 1}
    assert len(_rows()) == 1


def test_same_timestamp_with_different_value_is_not_duplicate(client_authenticated):
    content = (
        "value,type,timestamp\n"
        "5.8,CGM,2024-06-01 07:15:00\n"
        "6.0,CGM,2024-06-01 07:15:00\n"
    ).encode("utf-8-sig")

    result = _upload(client_authenticated, content, "cgm.csv", mode="cgm", unit="mmol/L")

    assert result.json["data"] == {"imported": 2, "skipped": 0}
    assert len(_rows()) == 2


def test_unrecognized_columns_fail_instead_of_writing_empty_records(client_authenticated):
    content = "时间,说明\n2024-06-01 07:15:00,测试\n".encode("utf-8-sig")

    result = _upload(client_authenticated, content, "bad.csv", mode="cgm")

    assert result.status_code == 400
    assert "血糖值" in result.json["message"]
    assert len(_rows()) == 0


def test_invalid_rows_are_skipped_without_stopping_valid_rows(client_authenticated):
    content = (
        "value,type,timestamp\n"
        "5.8,CGM,2024-06-01 07:15:00\n"
        ",CGM,2024-06-01 07:18:00\n"
        "999,CGM,bad-time\n"
    ).encode("utf-8-sig")

    result = _upload(client_authenticated, content, "mixed.csv", mode="cgm")

    assert result.status_code == 200
    assert result.json["data"] == {"imported": 1, "skipped": 2}
    assert len(_rows()) == 1


def test_normal_mode_keeps_source_type_and_notes(client_authenticated):
    content = (
        "value,type,timestamp,notes\n"
        "6.5,空腹,2024-06-01 07:15:00,早餐前\n"
    ).encode("utf-8-sig")

    result = _upload(
        client_authenticated, content, "normal.csv", mode="normal", unit="mmol/L"
    )

    assert result.json["data"] == {"imported": 1, "skipped": 0}
    assert [tuple(row) for row in _rows()] == [
        (6.5, "mmol/L", "空腹", "2024-06-01 07:15:00", "早餐前")
    ]
