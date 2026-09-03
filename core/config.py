import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 数据库连接配置：本地/测试默认 SQLite；Cloud Run 生产通过 Secret Manager 注入 PostgreSQL URL
DATABASE_URL = os.environ.get("SUGAR_BEE_DATABASE_URL", f"sqlite:///{os.path.join(BASE_DIR, 'glucose.db')}")
DB_TYPE = os.environ.get(
    "SUGAR_BEE_DB_TYPE",
    "postgres" if DATABASE_URL.startswith("postgresql") else "sqlite",
)

# DB_NAME 通过函数动态读取环境变量，确保测试中 db_info fixture 设置的
# SUGAR_BEE_DB_PATH 能正确生效（即使 core.config 被提前 import 缓存）。
def _get_db_name() -> str:
    """获取当前数据库路径（始终从环境变量读取）。"""
    return os.environ.get("SUGAR_BEE_DB_PATH", os.path.join(BASE_DIR, "glucose.db"))


def get_db_name() -> str:
    """获取当前数据库路径（始终从环境变量读取）。

    供需要实时读取 DB_NAME 的场景使用（如测试隔离）。
    普通模块变量 DB_NAME 在 import 时固定，多数据库测试需用此函数。
    """
    return _get_db_name()


# 模块级常量（import 时固定；测试中需通过 get_db_name() 或 monkeypatch 获取正确值）
DB_NAME: str = _get_db_name()

AVATAR_FOLDER = os.path.join(BASE_DIR, "static", "avatars")
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg'}

# Google Cloud 相关配置（Cloud Run 部署时使用）
GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "")
GCS_BUCKET_NAME = os.environ.get("GCS_BUCKET_NAME", "")
# 数据库在 GCS 中的路径，默认保留当前数据库对象；备份对象以前缀匹配
GCS_DB_PATH = os.environ.get("GCS_DB_PATH", "db/glucose.db")
# Cloud Scheduler 等内部调用使用的鉴权令牌
INTERNAL_API_TOKEN = os.environ.get("INTERNAL_API_TOKEN", "")
