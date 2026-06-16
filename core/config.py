import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 支持通过环境变量自定义数据库路径（Docker / 生产环境常用）
DB_NAME = os.environ.get("SUGAR_BEE_DB_PATH", os.path.join(BASE_DIR, "glucose.db"))
AVATAR_FOLDER = os.path.join(BASE_DIR, "static", "avatars")
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg'}

# Google Cloud 相关配置（Cloud Run 部署时使用）
GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "")
GCS_BUCKET_NAME = os.environ.get("GCS_BUCKET_NAME", "")
# 数据库在 GCS 中的路径，默认保留当前数据库对象；备份对象以前缀匹配
GCS_DB_PATH = os.environ.get("GCS_DB_PATH", "db/glucose.db")
# Cloud Scheduler 等内部调用使用的鉴权令牌
INTERNAL_API_TOKEN = os.environ.get("INTERNAL_API_TOKEN", "")
