"""
用户管理模块 - MVP 版本
支持多用户数据隔离和模块化配置
"""
import json
import sqlite3
from flask import session
from werkzeug.security import generate_password_hash, check_password_hash

class UserManager:
    """简化版用户管理器"""

    def __init__(self, db_path='glucose.db'):
        self.db_path = db_path

    def get_all_users(self):
        """获取所有活跃用户"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("""
            SELECT u.id, u.username, u.display_name, u.avatar, u.is_active,
                   u.phone, u.email,
                   p.name, p.birth_year, p.height, p.weight, p.gender,
                   p.default_meals, p.target_ranges, p.enabled_modules
            FROM app_users u
            LEFT JOIN user_profiles p ON u.id = p.user_id
            WHERE u.is_active = 1
            ORDER BY u.id
        """)
        users = [dict(row) for row in c.fetchall()]
        conn.close()

        # 解析 JSON 字段
        for user in users:
            if user.get('enabled_modules'):
                try:
                    user['enabled_modules'] = json.loads(user['enabled_modules'])
                except Exception:
                    user['enabled_modules'] = []
            else:
                user['enabled_modules'] = []

            if user.get('default_meals'):
                try:
                    user['default_meals'] = json.loads(user['default_meals'])
                except Exception:
                    user['default_meals'] = {}
            else:
                user['default_meals'] = {}

            if user.get('target_ranges'):
                try:
                    user['target_ranges'] = json.loads(user['target_ranges'])
                except Exception:
                    user['target_ranges'] = {}
            else:
                user['target_ranges'] = {}

        return users

    def get_user(self, user_id):
        """获取指定用户"""
        users = self.get_all_users()
        for user in users:
            if user['id'] == user_id:
                return user
        return None

    def get_current_user_id(self):
        """从 session 获取当前用户 ID"""
        return session.get('current_user_id', 1)  # 默认用户 1

    def set_current_user(self, user_id):
        """设置当前用户"""
        session['current_user_id'] = user_id

    def is_module_enabled(self, user_id, module_name):
        """检查用户是否启用了某个模块"""
        user = self.get_user(user_id)
        if not user or not user.get('enabled_modules'):
            return True  # 默认全部启用
        return module_name in user['enabled_modules']

    def create_user(self, username, display_name, profile_data, password=None):
        """创建新用户"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()

        # 插入用户（含密码哈希）
        password_hash = generate_password_hash(password) if password else None
        c.execute("""
            INSERT INTO app_users (username, display_name, is_active, password_hash)
            VALUES (?, ?, 1, ?)
        """, (username, display_name, password_hash))
        user_id = c.lastrowid

        # 插入配置
        c.execute("""
            INSERT INTO user_profiles (
                user_id, name, birth_year, height, weight, gender, enabled_modules
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id,
            profile_data.get('name'),
            profile_data.get('birth_year'),
            profile_data.get('height'),
            profile_data.get('weight'),
            profile_data.get('gender'),
            json.dumps(profile_data.get('enabled_modules', []))
        ))

        conn.commit()
        conn.close()
        return user_id

    def update_user_profile(self, user_id, profile_data):
        """更新用户配置"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()

        # 准备 JSON 数据
        enabled_modules = json.dumps(profile_data.get('enabled_modules', []))
        default_meals = json.dumps(profile_data.get('default_meals', {}))
        target_ranges = json.dumps(profile_data.get('target_ranges', {}))

        c.execute("""
            UPDATE user_profiles SET
                name = ?,
                birth_year = ?,
                height = ?,
                weight = ?,
                gender = ?,
                enabled_modules = ?,
                default_meals = ?,
                target_ranges = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE user_id = ?
        """, (
            profile_data.get('name'),
            profile_data.get('birth_year'),
            profile_data.get('height'),
            profile_data.get('weight'),
            profile_data.get('gender'),
            enabled_modules,
            default_meals,
            target_ranges,
            user_id
        ))

        conn.commit()
        conn.close()

    def set_enabled_modules(self, user_id, modules):
        """仅更新用户的启用模块列表（不影响其他 profile 字段）"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("""
            UPDATE user_profiles SET enabled_modules = ?, updated_at = CURRENT_TIMESTAMP
            WHERE user_id = ?
        """, (json.dumps(modules or []), user_id))
        conn.commit()
        conn.close()

    def get_user_config(self, user_id):
        """获取用户配置（兼容原 settings 格式）"""
        user = self.get_user(user_id)
        if not user:
            return self._get_default_config()

        return {
            'name': user.get('name') or user.get('display_name') or '用户',
            'birth_year': user.get('birth_year') or 1964,
            'height': user.get('height') or 170,
            'weight': user.get('weight') or 75,
            'gender': user.get('gender') or 'male',
            'avatar': user.get('avatar'),
            'default_meals': user.get('default_meals') or {},
            'target': user.get('target_ranges') or {
                'fasting_min': 3.9,
                'fasting_max': 7.0,
                'postmeal_max': 7.8,
                'premeal_max': 6.5
            },
            'glucose_pattern': {
                'fasting_range': '6.0-7.2',
                'postmeal_range': '6.5-8.0'
            }
        }

    def _get_default_config(self):
        """默认配置"""
        return {
            'name': '用户',
            'birth_year': 1964,
            'height': 170,
            'weight': 75,
            'gender': 'male',
            'default_meals': {},
            'target': {
                'fasting_min': 3.9,
                'fasting_max': 7.0,
                'postmeal_max': 7.8,
                'premeal_max': 6.5
            },
            'glucose_pattern': {
                'fasting_range': '6.0-7.2',
                'postmeal_range': '6.5-8.0'
            }
        }

    def authenticate(self, username, password):
        """验证用户名密码，返回 user_id 或 None"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT id, password_hash FROM app_users WHERE username = ? AND is_active = 1", (username,))
        row = c.fetchone()
        conn.close()
        if not row:
            return None
        if not row['password_hash']:
            # 未设置密码的旧用户，返回 user_id 但标记需要设置密码
            return row['id']
        if check_password_hash(row['password_hash'], password):
            return row['id']
        return None

    def has_password(self, user_id):
        """检查用户是否已设置密码"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT password_hash FROM app_users WHERE id = ? AND is_active = 1", (user_id,))
        row = c.fetchone()
        conn.close()
        if not row:
            return False
        return bool(row['password_hash'])

    def set_password(self, user_id, password):
        """设置用户密码"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("UPDATE app_users SET password_hash = ? WHERE id = ?",
                  (generate_password_hash(password), user_id))
        conn.commit()
        conn.close()

    def get_user_by_username(self, username):
        """根据用户名获取用户"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT id, username, display_name, password_hash FROM app_users WHERE username = ? AND is_active = 1", (username,))
        row = c.fetchone()
        conn.close()
        return dict(row) if row else None

    def get_user_by_username_or_id(self, user_id):
        """根据用户 ID 获取用户（含 password_hash，用于登录验证）"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT id, username, display_name, password_hash FROM app_users WHERE id = ? AND is_active = 1", (user_id,))
        row = c.fetchone()
        conn.close()
        return dict(row) if row else None

    def change_username(self, user_id, new_username):
        """修改用户名"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("UPDATE app_users SET username = ? WHERE id = ?", (new_username, user_id))
        conn.commit()
        conn.close()

    # ========== 手机号/邮箱绑定 ==========

    def bind_provider(self, user_id, provider, provider_uid):
        """绑定手机号/邮箱/第三方账号"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        # 检查是否已被其他用户绑定
        c.execute("SELECT user_id FROM user_auth_providers WHERE provider = ? AND provider_uid = ?",
                  (provider, provider_uid))
        existing = c.fetchone()
        if existing:
            conn.close()
            if existing[0] == user_id:
                return {'ok': False, 'message': '该账号已绑定'}
            return {'ok': False, 'message': '该账号已被其他用户绑定'}
        # 删除当前用户同类型旧绑定
        c.execute("DELETE FROM user_auth_providers WHERE user_id = ? AND provider = ?",
                  (user_id, provider))
        # 插入新绑定
        c.execute("INSERT INTO user_auth_providers (user_id, provider, provider_uid, verified) VALUES (?, ?, ?, 1)",
                  (user_id, provider, provider_uid))
        # 同步更新 app_users 便捷字段
        if provider in ('phone', 'email'):
            c.execute(f"UPDATE app_users SET {provider} = ? WHERE id = ?", (provider_uid, user_id))
        conn.commit()
        conn.close()
        return {'ok': True}

    def unbind_provider(self, user_id, provider):
        """解绑手机号/邮箱/第三方账号"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("DELETE FROM user_auth_providers WHERE user_id = ? AND provider = ?",
                  (user_id, provider))
        # 清空 app_users 便捷字段
        if provider in ('phone', 'email'):
            c.execute(f"UPDATE app_users SET {provider} = NULL WHERE id = ?", (user_id,))
        conn.commit()
        conn.close()

    def find_user_by_provider(self, provider, provider_uid):
        """通过绑定信息查找用户，返回 user_id 或 None"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("""SELECT uap.user_id FROM user_auth_providers uap
                     JOIN app_users u ON uap.user_id = u.id
                     WHERE uap.provider = ? AND uap.provider_uid = ? AND u.is_active = 1""",
                  (provider, provider_uid))
        row = c.fetchone()
        conn.close()
        return row[0] if row else None

    def get_user_providers(self, user_id):
        """获取用户已绑定的所有 provider"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT provider, provider_uid, verified, created_at FROM user_auth_providers WHERE user_id = ?",
                  (user_id,))
        rows = [dict(r) for r in c.fetchall()]
        conn.close()
        return rows
