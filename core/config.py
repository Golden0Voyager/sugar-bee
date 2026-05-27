import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 支持通过环境变量自定义数据库路径（Docker / 生产环境常用）
DB_NAME = os.environ.get("SUGAR_BEE_DB_PATH", os.path.join(BASE_DIR, "glucose.db"))
AVATAR_FOLDER = os.path.join(BASE_DIR, "static", "avatars")
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg'}
