import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_NAME = os.path.join(BASE_DIR, "glucose.db")
AVATAR_FOLDER = os.path.join(BASE_DIR, "static", "avatars")
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg'}
