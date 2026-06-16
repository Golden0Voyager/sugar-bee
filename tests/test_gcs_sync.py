"""测试 Google Cloud Storage 同步服务。"""
from __future__ import annotations

import builtins
import datetime
import sys
from unittest.mock import MagicMock

import pytest


def _make_blob(name: str, time_created) -> MagicMock:
    blob = MagicMock()
    blob.name = name
    blob.time_created = time_created
    return blob


@pytest.fixture
def gcs_env(monkeypatch):
    """配置 GCS 环境变量与模块级常量。"""
    from services import gcs_sync
    monkeypatch.setattr(gcs_sync, 'GCS_BUCKET_NAME', 'test-bucket')
    monkeypatch.setattr(gcs_sync, 'GCS_DB_PATH', 'db/glucose.db')
    yield gcs_sync


@pytest.fixture
def mock_storage(monkeypatch):
    """在 sys.modules 中注入 google.cloud.storage 的 mock。"""
    mock_mod = MagicMock()
    client_instance = MagicMock()
    bucket_instance = MagicMock()
    client_instance.bucket.return_value = bucket_instance
    mock_mod.Client = MagicMock(return_value=client_instance)
    monkeypatch.setitem(sys.modules, 'google.cloud.storage', mock_mod)
    yield mock_mod, client_instance, bucket_instance


class TestGetGcsBucket:
    def test_no_bucket_configured(self, gcs_env, monkeypatch):
        monkeypatch.setattr(gcs_env, 'GCS_BUCKET_NAME', '')
        assert gcs_env.get_gcs_bucket() is None

    def test_import_error_returns_none(self, gcs_env, monkeypatch):
        original_import = builtins.__import__

        def _fake_import(name, *args, **kwargs):
            if name == 'google.cloud.storage':
                raise ImportError('not installed')
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, '__import__', _fake_import)
        assert gcs_env.get_gcs_bucket() is None

    def test_returns_bucket(self, gcs_env, mock_storage):
        _, client, bucket = mock_storage
        result = gcs_env.get_gcs_bucket()
        client.bucket.assert_called_once_with('test-bucket')
        assert result is bucket


class TestRestoreDbFromGcs:
    def test_no_bucket_skips(self, gcs_env, monkeypatch):
        monkeypatch.setattr(gcs_env, 'GCS_BUCKET_NAME', '')
        gcs_env.restore_db_from_gcs()

    def test_no_blobs_creates_new_db(self, gcs_env, mock_storage, capsys):
        _, _, bucket = mock_storage
        bucket.list_blobs.return_value = []
        gcs_env.restore_db_from_gcs()
        captured = capsys.readouterr()
        assert '未找到数据库备份' in captured.out

    def test_downloads_latest_blob(self, gcs_env, mock_storage, tmp_path, monkeypatch):
        _, _, bucket = mock_storage
        db_path = tmp_path / 'glucose.db'
        monkeypatch.setattr(gcs_env, 'DB_NAME', str(db_path))
        blobs = [
            _make_blob('db/glucose_20260614.db', datetime.datetime(2026, 6, 14, 10, 0)),
            _make_blob('db/glucose_20260615.db', datetime.datetime(2026, 6, 15, 10, 0)),
        ]
        bucket.list_blobs.return_value = blobs
        gcs_env.restore_db_from_gcs()
        latest = blobs[1]
        latest.download_to_filename.assert_called_once_with(str(db_path))

    def test_list_blobs_exception_handled(self, gcs_env, mock_storage, capsys):
        _, _, bucket = mock_storage
        bucket.list_blobs.side_effect = RuntimeError('network')
        gcs_env.restore_db_from_gcs()
        captured = capsys.readouterr()
        assert '列举备份对象失败' in captured.out


class TestBackupDbToGcs:
    def test_no_bucket_skips(self, gcs_env, monkeypatch):
        monkeypatch.setattr(gcs_env, 'GCS_BUCKET_NAME', '')
        gcs_env.backup_db_to_gcs()

    def test_missing_local_db(self, gcs_env, mock_storage, tmp_path, monkeypatch, capsys):
        _, _, bucket = mock_storage
        missing = tmp_path / 'missing.db'
        monkeypatch.setattr(gcs_env, 'DB_NAME', str(missing))
        gcs_env.backup_db_to_gcs()
        captured = capsys.readouterr()
        assert '本地数据库不存在' in captured.out
        bucket.blob.assert_not_called()

    def test_uploads_current_db(self, gcs_env, mock_storage, tmp_path, monkeypatch):
        _, _, bucket = mock_storage
        db_path = tmp_path / 'glucose.db'
        db_path.write_text('data')
        monkeypatch.setattr(gcs_env, 'DB_NAME', str(db_path))
        gcs_env.backup_db_to_gcs()
        bucket.blob.assert_called_once_with('db/glucose.db')
        bucket.blob.return_value.upload_from_filename.assert_called_once_with(str(db_path))

    def test_upload_exception_handled(self, gcs_env, mock_storage, tmp_path, monkeypatch, capsys):
        _, _, bucket = mock_storage
        db_path = tmp_path / 'glucose.db'
        db_path.write_text('data')
        monkeypatch.setattr(gcs_env, 'DB_NAME', str(db_path))
        bucket.blob.return_value.upload_from_filename.side_effect = RuntimeError('upload failed')
        gcs_env.backup_db_to_gcs()
        captured = capsys.readouterr()
        assert '上传数据库失败' in captured.out


class TestSyncFile:
    def test_sync_to_gcs_missing_local(self, gcs_env, mock_storage, tmp_path):
        _, _, bucket = mock_storage
        missing = tmp_path / 'avatar.png'
        gcs_env.sync_file_to_gcs(str(missing), 'avatars/avatar.png')
        bucket.blob.assert_not_called()

    def test_sync_to_gcs_uploads(self, gcs_env, mock_storage, tmp_path):
        _, _, bucket = mock_storage
        local = tmp_path / 'avatar.png'
        local.write_text('png')
        gcs_env.sync_file_to_gcs(str(local), 'avatars/avatar.png')
        bucket.blob.assert_called_once_with('avatars/avatar.png')
        bucket.blob.return_value.upload_from_filename.assert_called_once_with(str(local))

    def test_sync_from_gcs_blob_missing(self, gcs_env, mock_storage, tmp_path):
        _, _, bucket = mock_storage
        bucket.blob.return_value.exists.return_value = False
        local = tmp_path / 'garmin_tokens.json'
        gcs_env.sync_file_from_gcs('garmin_tokens/garmin_tokens.json', str(local))
        bucket.blob.assert_called_once_with('garmin_tokens/garmin_tokens.json')
        bucket.blob.return_value.download_to_filename.assert_not_called()

    def test_sync_from_gcs_downloads(self, gcs_env, mock_storage, tmp_path):
        _, _, bucket = mock_storage
        bucket.blob.return_value.exists.return_value = True
        local = tmp_path / 'garmin_tokens.json'
        gcs_env.sync_file_from_gcs('garmin_tokens/garmin_tokens.json', str(local))
        bucket.blob.return_value.download_to_filename.assert_called_once_with(str(local))
        assert local.parent.exists()

    def test_sync_from_gcs_no_bucket(self, gcs_env, monkeypatch, tmp_path):
        monkeypatch.setattr(gcs_env, 'GCS_BUCKET_NAME', '')
        gcs_env.sync_file_from_gcs('garmin_tokens/garmin_tokens.json', str(tmp_path / 'x.json'))
