"""Google Cloud Storage 同步工具。

用于 Cloud Run 环境持久化 SQLite 数据库、头像、备份和 Garmin token。
未配置 GCS_BUCKET_NAME 或缺少 google-cloud-storage 时，所有函数静默跳过。
"""
from __future__ import annotations

import datetime
import os
from typing import Any

from core.config import DB_NAME, GCS_BUCKET_NAME, GCS_DB_PATH


def _get_storage_client() -> Any | None:
    """延迟导入 google-cloud-storage，避免本地开发环境强依赖。"""
    try:
        from google.cloud import storage
    except ImportError:  # pragma: no cover（CI 与生产均会安装）
        return None
    return storage.Client()


def get_gcs_bucket() -> Any | None:
    """获取配置中指定的 GCS 存储桶对象；未配置或 SDK 不可用时返回 None。"""
    if not GCS_BUCKET_NAME:
        return None
    client = _get_storage_client()
    if client is None:
        return None
    try:
        return client.bucket(GCS_BUCKET_NAME)
    except Exception as e:  # pragma: no cover
        print(f"[GCS] 获取存储桶失败: {e}")
        return None


def _db_prefix() -> str:
    """数据库备份对象前缀，默认 db/glucose。"""
    # GCS_DB_PATH 如 db/glucose.db，去掉扩展名得到前缀
    return GCS_DB_PATH.rsplit(".", 1)[0]


def restore_db_from_gcs() -> None:
    """从 GCS 恢复最新的数据库文件到本地 DB_NAME。

    首次运行（GCS 中无备份）时直接返回，由 init_db() 创建新库。
    """
    bucket = get_gcs_bucket()
    if bucket is None:
        return

    try:
        blobs = list(bucket.list_blobs(prefix=_db_prefix()))
    except Exception as e:  # pragma: no cover
        print(f"[GCS] 列举备份对象失败: {e}")
        return

    if not blobs:
        print("[GCS] 未找到数据库备份，将创建新库")
        return

    latest = max(blobs, key=lambda b: b.time_created or datetime.datetime.min.replace(tzinfo=None))
    target_dir = os.path.dirname(DB_NAME)
    if target_dir:
        os.makedirs(target_dir, exist_ok=True)

    try:
        latest.download_to_filename(DB_NAME)
        print(f"[GCS] 已从 {latest.name} 恢复数据库到 {DB_NAME}")
    except Exception as e:  # pragma: no cover
        print(f"[GCS] 下载数据库失败: {e}")


def backup_db_to_gcs(blob_name: str | None = None) -> None:
    """将本地数据库上传到 GCS。

    Args:
        blob_name: GCS 目标对象路径，默认使用 GCS_DB_PATH。
    """
    bucket = get_gcs_bucket()
    if bucket is None:
        return
    if not os.path.isfile(DB_NAME):
        print(f"[GCS] 本地数据库不存在，跳过备份: {DB_NAME}")
        return

    target = blob_name or GCS_DB_PATH
    try:
        blob = bucket.blob(target)
        blob.upload_from_filename(DB_NAME)
        print(f"[GCS] 数据库已备份到 {target}")
    except Exception as e:  # pragma: no cover
        print(f"[GCS] 上传数据库失败: {e}")


def sync_file_to_gcs(local_path: str, gcs_path: str) -> None:
    """将本地文件上传到 GCS（如头像、Garmin token）。"""
    bucket = get_gcs_bucket()
    if bucket is None or not os.path.isfile(local_path):
        return
    try:
        bucket.blob(gcs_path).upload_from_filename(local_path)
        print(f"[GCS] 已上传 {local_path} -> {gcs_path}")
    except Exception as e:  # pragma: no cover
        print(f"[GCS] 上传 {local_path} 失败: {e}")


def sync_file_from_gcs(gcs_path: str, local_path: str) -> None:
    """从 GCS 下载文件到本地，本地目录不存在时自动创建。"""
    bucket = get_gcs_bucket()
    if bucket is None:
        return
    blob = bucket.blob(gcs_path)
    try:
        if not blob.exists():
            return
    except Exception as e:  # pragma: no cover
        print(f"[GCS] 检查 {gcs_path} 是否存在失败: {e}")
        return

    local_dir = os.path.dirname(local_path)
    if local_dir:
        os.makedirs(local_dir, exist_ok=True)
    try:
        blob.download_to_filename(local_path)
        print(f"[GCS] 已下载 {gcs_path} -> {local_path}")
    except Exception as e:  # pragma: no cover
        print(f"[GCS] 下载 {gcs_path} 失败: {e}")
