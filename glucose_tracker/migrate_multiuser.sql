-- ========================================
-- 多用户 MVP 数据库迁移脚本
-- ========================================

-- 1. 创建用户表（极简版）
CREATE TABLE IF NOT EXISTS app_users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    display_name TEXT NOT NULL,
    avatar TEXT,
    is_active BOOLEAN DEFAULT 1,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 2. 创建用户配置表
CREATE TABLE IF NOT EXISTS user_profiles (
    user_id INTEGER PRIMARY KEY REFERENCES app_users(id),
    name TEXT,
    birth_year INTEGER,
    height INTEGER,
    weight INTEGER,
    gender TEXT,
    default_meals TEXT,  -- JSON string
    target_ranges TEXT,  -- JSON string
    enabled_modules TEXT,  -- JSON: ["glucose", "blood_pressure", "exercise", "diet", "weight"]
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 3. 给现有表添加 user_id（如果不存在）
-- records 表
ALTER TABLE records ADD COLUMN user_id INTEGER DEFAULT 1 REFERENCES app_users(id);

-- medication_plans 表
ALTER TABLE medication_plans ADD COLUMN user_id INTEGER DEFAULT 1 REFERENCES app_users(id);

-- health_analyses 表
ALTER TABLE health_analyses ADD COLUMN user_id INTEGER DEFAULT 1 REFERENCES app_users(id);

-- 4. 创建索引（性能优化）
CREATE INDEX IF NOT EXISTS idx_records_user_id ON records(user_id);
CREATE INDEX IF NOT EXISTS idx_medication_user_id ON medication_plans(user_id);
CREATE INDEX IF NOT EXISTS idx_health_analyses_user_id ON health_analyses(user_id);

-- 5. 插入默认用户（迁移现有数据）
INSERT INTO app_users (id, username, display_name, is_active)
VALUES (1, 'yuqun', '愚群', 1);

-- 6. 插入默认用户配置
INSERT INTO user_profiles (user_id, name, birth_year, height, weight, gender, enabled_modules)
SELECT
    1,
    '愚群',
    1964,
    170,
    74,
    'male',
    '["glucose", "blood_pressure", "exercise", "diet"]'
WHERE NOT EXISTS (SELECT 1 FROM user_profiles WHERE user_id = 1);

-- 7. 示例：添加第二个用户
INSERT INTO app_users (id, username, display_name, is_active)
VALUES (2, 'family_member', '家人', 1);

INSERT INTO user_profiles (user_id, name, birth_year, height, weight, gender, enabled_modules)
VALUES (
    2,
    '家人',
    1970,
    165,
    60,
    'female',
    '["weight", "blood_pressure", "diet"]'
);

-- 8. 示例：添加第三个用户
INSERT INTO app_users (id, username, display_name, is_active)
VALUES (3, 'another_user', '另一位用户', 1);

INSERT INTO user_profiles (user_id, name, birth_year, height, weight, gender, enabled_modules)
VALUES (
    3,
    '另一位用户',
    1985,
    175,
    70,
    'male',
    '["glucose", "weight", "exercise"]'
);

-- ========================================
-- 迁移完成提示
-- ========================================
-- 执行此脚本后：
-- 1. 所有现有数据归属到用户1（愚群）
-- 2. 可以添加新用户并独立使用
-- 3. 每个用户可以配置自己需要的功能模块
