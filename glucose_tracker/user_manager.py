"""
用户管理模块 - MVP 版本
支持多用户数据隔离和模块化配置
"""
import json
import sqlite3
from flask import session

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
                except:
                    user['enabled_modules'] = []
            else:
                user['enabled_modules'] = []

            if user.get('default_meals'):
                try:
                    user['default_meals'] = json.loads(user['default_meals'])
                except:
                    user['default_meals'] = {}
            else:
                user['default_meals'] = {}

            if user.get('target_ranges'):
                try:
                    user['target_ranges'] = json.loads(user['target_ranges'])
                except:
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

    def create_user(self, username, display_name, profile_data):
        """创建新用户"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()

        # 插入用户
        c.execute("""
            INSERT INTO app_users (username, display_name, is_active)
            VALUES (?, ?, 1)
        """, (username, display_name))
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

    def get_user_config(self, user_id):
        """获取用户配置（兼容原 settings 格式）"""
        user = self.get_user(user_id)
        if not user:
            return self._get_default_config()

        return {
            'name': user.get('name', '用户'),
            'birth_year': user.get('birth_year', 1964),
            'height': user.get('height', 170),
            'weight': user.get('weight', 75),
            'gender': user.get('gender', 'male'),
            'avatar': user.get('avatar'),
            'default_meals': user.get('default_meals', {}),
            'target': user.get('target_ranges', {
                'fasting_min': 3.9,
                'fasting_max': 7.0,
                'postmeal_max': 7.8,
                'premeal_max': 6.5
            }),
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
