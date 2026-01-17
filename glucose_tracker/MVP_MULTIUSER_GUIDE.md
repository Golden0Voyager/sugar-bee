# 多用户 MVP 实施指南

## 🎯 目标

在 1-2 天内实现 2-3 个用户的独立使用，每个用户可配置不同的功能模块。

---

## 📋 实施步骤

### **第 1 步：数据库迁移**（10 分钟）

```bash
# 1. 备份当前数据库
cp glucose.db glucose.db.backup_$(date +%Y%m%d)

# 2. 执行迁移脚本
sqlite3 glucose.db < migrate_multiuser.sql

# 3. 验证迁移
sqlite3 glucose.db "SELECT * FROM app_users;"
sqlite3 glucose.db "SELECT * FROM user_profiles;"
```

---

### **第 2 步：创建用户管理模块**（30 分钟）

创建新文件 `user_manager.py`：

```python
# user_manager.py
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
            SELECT u.*, p.*
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
                user['enabled_modules'] = json.loads(user['enabled_modules'])
            if user.get('default_meals'):
                user['default_meals'] = json.loads(user['default_meals'])
            if user.get('target_ranges'):
                user['target_ranges'] = json.loads(user['target_ranges'])

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
```

---

### **第 3 步：修改 app.py**（1 小时）

#### 3.1 导入用户管理器

```python
# app.py 头部添加
from user_manager import UserManager

# 初始化
user_manager = UserManager(DB_NAME)

# 配置 session
app.secret_key = os.getenv('SECRET_KEY', 'dev-secret-key-change-in-production')
```

#### 3.2 添加用户切换 API

```python
@app.route('/switch_user/<int:user_id>', methods=['POST'])
def switch_user(user_id):
    """切换当前用户"""
    user = user_manager.get_user(user_id)
    if not user:
        return jsonify({"status": "error", "message": "用户不存在"}), 404

    user_manager.set_current_user(user_id)
    return jsonify({"status": "success", "user": user})

@app.route('/get_users', methods=['GET'])
def get_users():
    """获取所有用户列表"""
    users = user_manager.get_all_users()
    return jsonify(users)

@app.route('/get_current_user', methods=['GET'])
def get_current_user():
    """获取当前用户"""
    user_id = user_manager.get_current_user_id()
    user = user_manager.get_user(user_id)
    return jsonify(user)
```

#### 3.3 修改所有查询（添加用户过滤）

**关键修改示例**：

```python
@app.route('/')
def index():
    try:
        db = get_db()
        c = db.cursor()

        # 获取当前用户
        current_user_id = user_manager.get_current_user_id()
        current_user = user_manager.get_user(current_user_id)

        # 自动生成早晨空腹血糖预测（如果符合条件）
        predict_morning_fpg(db, current_user_id)  # 传入 user_id

        # 获取分页参数
        days = request.args.get('days', 14, type=int)

        # 1. Fetch records with user filter
        c.execute("""SELECT * FROM records
                    WHERE user_id = ?
                    AND timestamp > datetime('now', ?)
                    ORDER BY timestamp ASC""", (current_user_id, f'-{days} days'))
        rows = c.fetchall()

        # ... 其他代码保持不变，但所有查询都加上 user_id 过滤
```

**需要修改的所有函数**（搜索并添加 `user_id` 参数）：
- `index()` - 主页数据加载
- `add_record()` - 添加记录时设置 user_id
- `batch_add()` - 批量添加时设置 user_id
- `predict_morning_fpg()` - 预测时传入 user_id
- `generate_health_analysis()` - 分析时传入 user_id
- 所有 medication 相关函数
- 所有查询函数

---

### **第 4 步：前端界面修改**（1 小时）

#### 4.1 添加用户切换器（顶部导航栏）

在 `templates/index.html` 的顶部添加用户选择器：

```html
<!-- 在 app-logo 旁边添加用户切换器 -->
<div class="d-flex justify-content-between align-items-center mb-4">
    <div class="app-logo">
        <!-- 现有 logo 代码 -->
    </div>

    <!-- 用户切换器 -->
    <div class="d-flex align-items-center gap-3">
        <div class="dropdown">
            <button class="btn btn-outline-primary dropdown-toggle"
                    type="button"
                    id="userSwitcher"
                    data-bs-toggle="dropdown">
                <i class="bi bi-person-circle me-2"></i>
                <span id="currentUserName">加载中...</span>
            </button>
            <ul class="dropdown-menu dropdown-menu-end" id="userList">
                <!-- 动态加载用户列表 -->
            </ul>
        </div>

        <!-- 现有的用户下拉菜单 -->
        <div class="dropdown">
            <!-- ... -->
        </div>
    </div>
</div>
```

#### 4.2 添加 JavaScript 逻辑

```javascript
// 页面加载时初始化用户
document.addEventListener('DOMContentLoaded', async function() {
    await loadCurrentUser();
    await loadUserList();
});

// 加载当前用户
async function loadCurrentUser() {
    try {
        const res = await fetch('/get_current_user');
        const user = await res.json();
        document.getElementById('currentUserName').textContent = user.display_name;

        // 根据用户启用的模块显示/隐藏功能
        updateModuleVisibility(user.enabled_modules || []);
    } catch (e) {
        console.error('Load current user error:', e);
    }
}

// 加载用户列表
async function loadUserList() {
    try {
        const res = await fetch('/get_users');
        const users = await res.json();

        const userList = document.getElementById('userList');
        userList.innerHTML = users.map(user => `
            <li>
                <a class="dropdown-item" href="#" onclick="switchUser(${user.id}); return false;">
                    <i class="bi bi-person${user.id === currentUserId ? '-fill' : ''} me-2"></i>
                    ${user.display_name}
                    ${user.id === currentUserId ? '<i class="bi bi-check-circle-fill ms-2 text-success"></i>' : ''}
                </a>
            </li>
        `).join('');
    } catch (e) {
        console.error('Load user list error:', e);
    }
}

// 切换用户
async function switchUser(userId) {
    try {
        const res = await fetch(`/switch_user/${userId}`, { method: 'POST' });
        const result = await res.json();

        if (result.status === 'success') {
            location.reload();  // 刷新页面加载新用户数据
        } else {
            alert('切换失败: ' + result.message);
        }
    } catch (e) {
        alert('切换失败: ' + e.message);
    }
}

// 根据模块配置显示/隐藏功能
function updateModuleVisibility(enabledModules) {
    // 默认全部隐藏
    const allModules = ['glucose', 'blood_pressure', 'exercise', 'diet', 'weight'];

    allModules.forEach(module => {
        const elements = document.querySelectorAll(`[data-module="${module}"]`);
        const isEnabled = enabledModules.includes(module);

        elements.forEach(el => {
            if (isEnabled) {
                el.classList.remove('d-none');
            } else {
                el.classList.add('d-none');
            }
        });
    });
}
```

#### 4.3 给功能模块添加标识

```html
<!-- 血糖模块 -->
<div class="col-xl-3 col-lg-6 col-12" data-module="glucose">
    <!-- 血糖统计卡片 -->
</div>

<!-- 血压模块 -->
<div class="col-xl-3 col-lg-6 col-12" data-module="blood_pressure">
    <!-- 血压统计卡片 -->
</div>

<!-- 运动模块 -->
<div class="col-xl-3 col-lg-6 col-12" data-module="exercise">
    <!-- 运动统计卡片 -->
</div>

<!-- 体重模块 -->
<div class="col-xl-3 col-lg-6 col-12" data-module="weight">
    <!-- 体重统计卡片 -->
</div>
```

---

### **第 5 步：添加用户管理界面**（可选，30 分钟）

创建简单的用户管理页面：

```html
<!-- 在用户下拉菜单中添加 -->
<li>
    <a class="dropdown-item" href="#" data-bs-toggle="modal" data-bs-target="#manageUsersModal">
        <i class="bi bi-people me-2"></i>用户管理
    </a>
</li>

<!-- 用户管理模态框 -->
<div class="modal fade" id="manageUsersModal" tabindex="-1">
    <div class="modal-dialog modal-lg">
        <div class="modal-content">
            <div class="modal-header">
                <h5 class="modal-title">用户管理</h5>
                <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
            </div>
            <div class="modal-body">
                <div class="mb-3">
                    <button class="btn btn-primary" onclick="showAddUserForm()">
                        <i class="bi bi-plus-circle me-2"></i>添加新用户
                    </button>
                </div>

                <!-- 用户列表 -->
                <div class="table-responsive">
                    <table class="table">
                        <thead>
                            <tr>
                                <th>用户名</th>
                                <th>姓名</th>
                                <th>启用模块</th>
                                <th>操作</th>
                            </tr>
                        </thead>
                        <tbody id="userManagementTable">
                            <!-- 动态加载 -->
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    </div>
</div>
```

---

## 🎨 **功能模块配置示例**

### **用户 1：愚群（糖尿病患者）**
```json
{
    "enabled_modules": ["glucose", "blood_pressure", "exercise", "diet"]
}
```

### **用户 2：家人（只关注体重和血压）**
```json
{
    "enabled_modules": ["weight", "blood_pressure", "diet"]
}
```

### **用户 3：另一位用户（健身爱好者）**
```json
{
    "enabled_modules": ["weight", "exercise", "diet"]
}
```

---

## ⚡ **快速启动**

### **添加新用户（通过 SQL）**

```sql
-- 1. 添加用户基本信息
INSERT INTO app_users (username, display_name, is_active)
VALUES ('zhangsan', '张三', 1);

-- 2. 添加用户配置
INSERT INTO user_profiles (
    user_id, name, birth_year, height, weight, gender, enabled_modules
) VALUES (
    last_insert_rowid(),
    '张三',
    1990,
    175,
    70,
    'male',
    '["weight", "blood_pressure", "diet"]'
);
```

---

## 📊 **数据隔离验证**

```sql
-- 查看每个用户的记录数
SELECT
    u.display_name,
    COUNT(r.id) as record_count
FROM app_users u
LEFT JOIN records r ON u.id = r.user_id
GROUP BY u.id;

-- 查看特定用户的数据
SELECT * FROM records WHERE user_id = 2 LIMIT 10;
```

---

## 🔄 **回滚方案**

如果出现问题，可以恢复备份：

```bash
# 恢复备份
cp glucose.db.backup_YYYYMMDD glucose.db

# 或者删除 user_id 列（仅测试环境）
sqlite3 glucose.db "ALTER TABLE records DROP COLUMN user_id;"
```

---

## ✅ **测试清单**

- [ ] 数据库迁移成功
- [ ] 用户切换功能正常
- [ ] 每个用户只能看到自己的数据
- [ ] 模块隐藏/显示正常
- [ ] 添加记录时正确分配 user_id
- [ ] 健康分析按用户独立生成
- [ ] 用户配置独立保存

---

## 🎯 **优势**

1. ✅ **极简设计**：无需登录密码，适合家庭使用
2. ✅ **快速实施**：1-2 天即可完成
3. ✅ **数据隔离**：每个用户数据完全独立
4. ✅ **灵活配置**：每个用户可选择需要的功能
5. ✅ **向后兼容**：现有数据自动归属到用户 1
6. ✅ **易于扩展**：未来可升级到完整多租户系统

---

## 🚀 **下一步计划**

完成 MVP 后，根据实际使用情况可以考虑：

1. 添加密码保护（可选）
2. 添加数据导入/导出
3. 添加用户数据共享（查看家人数据）
4. 添加用户头像上传
5. 添加更多功能模块（睡眠、心情等）
