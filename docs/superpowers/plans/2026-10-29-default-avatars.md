# Default Avatar Library Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 提供 9 个人物插画和 9 个动物插画，未设置头像时按用户 ID 固定显示，用户主动选择后持久化，同时保留原上传裁剪功能。

**Architecture:** 新建 `utils/avatars.py` 作为 18 个头像的唯一清单和校验入口。空头像通过 `UserManager.get_user_config()` 固定回退，不写老数据；主动选择通过登录保护的 `/select_avatar` 写入现有 `app_users.avatar`。前端沿用单页模板和现有头像弹窗，不引入新依赖。

**Tech Stack:** Python 3.12+、Flask、SQLite/PostgreSQL、Jinja2、原生 JavaScript、CSS、SVG、pytest、Playwright CLI。

## Global Constraints

- 使用 `uv run python ...` 和 `uv run ruff ...`，不得使用裸 `python` 或 `pip`。
- 默认头像恰好 18 个：人物、动物各 9 个，全部是本地 SVG。
- 未设置头像时按用户 ID 对 18 取模固定分配，不随机、不批量改写老用户。
- 主动选择写入现有 `app_users.avatar`；已有上传头像不得被自动分配覆盖。
- 保存接口只接受清单内的 `defaults/person_XX.svg` 或 `defaults/animal_XX.svg`，不得接受任意路径。
- 不新增数据库字段，不新增第三方依赖，不改变现有 `/upload_avatar` 流程。
- 设计依据：`docs/superpowers/specs/2026-10-29-default-avatars-design.md`。

---

### Task 1: 头像清单、18 个本地 SVG 与打包规则

**Files:**
- Create: `utils/avatars.py`
- Create: `static/avatars/defaults/person_01.svg` 至 `person_09.svg`
- Create: `static/avatars/defaults/animal_01.svg` 至 `animal_09.svg`
- Create: `tests/test_avatars.py`
- Modify: `.gitignore:35`
- Modify: `.dockerignore:40`

**Interfaces:**
- Produces: `DEFAULT_AVATARS`（字典元组，每项含 `id/group/label/filename`）。
- Produces: `default_avatar_for_user(user_id) -> str`，返回 `defaults/...svg`。
- Produces: `is_default_avatar(value) -> bool`，供保存接口做白名单校验。

- [ ] **Step 1: 写失败的清单、文件和固定分配测试**

创建 `tests/test_avatars.py`：

```python
from pathlib import Path

import utils.avatars as avatars


ROOT = Path(__file__).resolve().parents[1]


def test_catalog_has_nine_people_and_nine_animals_with_existing_svg_files():
    assert len(avatars.DEFAULT_AVATARS) == 18
    assert sum(a['group'] == 'person' for a in avatars.DEFAULT_AVATARS) == 9
    assert sum(a['group'] == 'animal' for a in avatars.DEFAULT_AVATARS) == 9
    filenames = [a['filename'] for a in avatars.DEFAULT_AVATARS]
    assert len(set(filenames)) == 18
    for filename in filenames:
        path = ROOT / 'static' / 'avatars' / filename
        assert path.is_file(), filename
        assert path.read_text(encoding='utf-8').startswith('<svg')


def test_default_avatar_is_stable_and_uses_user_id_modulo():
    assert avatars.default_avatar_for_user(1) == 'defaults/person_02.svg'
    assert avatars.default_avatar_for_user(19) == 'defaults/person_02.svg'
    assert avatars.default_avatar_for_user(17) == 'defaults/animal_09.svg'
    assert avatars.default_avatar_for_user(1) == avatars.default_avatar_for_user(1)


def test_default_avatar_whitelist_rejects_paths_and_non_strings():
    assert avatars.is_default_avatar('defaults/person_01.svg')
    assert avatars.is_default_avatar('defaults/animal_09.svg')
    for value in (None, '', 1, 'person_01.svg', '/etc/passwd', '../person_01.svg',
                  'defaults/../person_01.svg', 'defaults/unknown.svg'):
        assert not avatars.is_default_avatar(value)
```

- [ ] **Step 2: 运行测试，确认因模块尚不存在而失败**

Run: `uv run python -m pytest tests/test_avatars.py -q`
Expected: FAIL，`ModuleNotFoundError: No module named 'utils.avatars'`。

- [ ] **Step 3: 实现唯一头像清单**

创建 `utils/avatars.py`：

```python
DEFAULT_AVATARS = (
    {'id': 'person_01', 'group': 'person', 'label': '爸爸', 'filename': 'defaults/person_01.svg'},
    {'id': 'person_02', 'group': 'person', 'label': '妈妈', 'filename': 'defaults/person_02.svg'},
    {'id': 'person_03', 'group': 'person', 'label': '爷爷', 'filename': 'defaults/person_03.svg'},
    {'id': 'person_04', 'group': 'person', 'label': '奶奶', 'filename': 'defaults/person_04.svg'},
    {'id': 'person_05', 'group': 'person', 'label': '男士', 'filename': 'defaults/person_05.svg'},
    {'id': 'person_06', 'group': 'person', 'label': '男孩', 'filename': 'defaults/person_06.svg'},
    {'id': 'person_07', 'group': 'person', 'label': '女孩', 'filename': 'defaults/person_07.svg'},
    {'id': 'person_08', 'group': 'person', 'label': '医生', 'filename': 'defaults/person_08.svg'},
    {'id': 'person_09', 'group': 'person', 'label': '女士', 'filename': 'defaults/person_09.svg'},
    {'id': 'animal_01', 'group': 'animal', 'label': '蜜蜂', 'filename': 'defaults/animal_01.svg'},
    {'id': 'animal_02', 'group': 'animal', 'label': '老虎', 'filename': 'defaults/animal_02.svg'},
    {'id': 'animal_03', 'group': 'animal', 'label': '兔子', 'filename': 'defaults/animal_03.svg'},
    {'id': 'animal_04', 'group': 'animal', 'label': '小熊', 'filename': 'defaults/animal_04.svg'},
    {'id': 'animal_05', 'group': 'animal', 'label': '熊猫', 'filename': 'defaults/animal_05.svg'},
    {'id': 'animal_06', 'group': 'animal', 'label': '考拉', 'filename': 'defaults/animal_06.svg'},
    {'id': 'animal_07', 'group': 'animal', 'label': '狐狸', 'filename': 'defaults/animal_07.svg'},
    {'id': 'animal_08', 'group': 'animal', 'label': '企鹅', 'filename': 'defaults/animal_08.svg'},
    {'id': 'animal_09', 'group': 'animal', 'label': '猫头鹰', 'filename': 'defaults/animal_09.svg'},
)


def default_avatar_for_user(user_id):
    return DEFAULT_AVATARS[int(user_id) % len(DEFAULT_AVATARS)]['filename']


def is_default_avatar(value):
    return isinstance(value, str) and any(a['filename'] == value for a in DEFAULT_AVATARS)
```

- [ ] **Step 4: 生成 18 个统一风格的矢量头像**

运行以下完整脚本。它只生成 SVG 文件，不留下生成器或新增依赖：

```bash
uv run python - <<'PY'
from pathlib import Path

out = Path('static/avatars/defaults')
out.mkdir(parents=True, exist_ok=True)
people = [
    ('C7D2FE', '4F46E5', '8D5524', '2F1B12', ''),
    ('FECDD3', 'EC4899', 'C68642', '4A2518', '<path d="M38 43q12-13 24 0" fill="none" stroke="#4A2518" stroke-width="7"/>'),
    ('FDE68A', 'D97706', 'E0AC69', 'E5E7EB', '<path d="M42 49h7m6 0h7m-13 0h6" stroke="#475569" stroke-width="2"/><circle cx="45" cy="49" r="5" fill="none" stroke="#475569"/><circle cx="59" cy="49" r="5" fill="none" stroke="#475569"/>'),
    ('BBF7D0', '16A34A', 'E0AC69', 'D1D5DB', '<circle cx="64" cy="36" r="8" fill="#D1D5DB"/>'),
    ('BAE6FD', '0284C7', 'C68642', '1F2937', ''),
    ('DDD6FE', '8B5CF6', 'E0AC69', '3F2D20', ''),
    ('FED7AA', 'F97316', 'E0AC69', '4A2518', '<circle cx="36" cy="40" r="6" fill="#4A2518"/><circle cx="68" cy="40" r="6" fill="#4A2518"/>'),
    ('A7F3D0', '059669', 'C68642', '1F2937', '<path d="M43 68v8q9 10 18 0v-8" fill="none" stroke="#475569" stroke-width="2"/><circle cx="52" cy="79" r="2" fill="#475569"/>'),
    ('FEF08A', 'CA8A04', 'C68642', '4A2518', '<path d="M35 51q2-25 17-25t17 25v18H35z" fill="#4A2518" opacity=".9"/><circle cx="52" cy="48" r="14" fill="#C68642"/>'),
]
person = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 104 104"><circle cx="52" cy="52" r="50" fill="#{bg}"/><path d="M22 94q4-29 30-29t30 29" fill="#{shirt}"/><circle cx="52" cy="47" r="20" fill="#{skin}"/><path d="M32 45q0-23 20-23t20 23q-8-13-20-13t-20 13" fill="#{hair}"/><circle cx="45" cy="48" r="2" fill="#1F2937"/><circle cx="59" cy="48" r="2" fill="#1F2937"/><path d="M45 58q7 6 14 0" fill="none" stroke="#7C2D12" stroke-width="2" stroke-linecap="round"/>{extra}</svg>'''
for i, (bg, shirt, skin, hair, extra) in enumerate(people, 1):
    (out / f'person_{i:02}.svg').write_text(person.format(bg=bg, shirt=shirt, skin=skin, hair=hair, extra=extra), encoding='utf-8')

animals = [
    ('FEF08A', 'FACC15', '<path d="M40 29L32 15m32 14l8-14" stroke="#475569" stroke-width="3"/>', '<path d="M35 45h34M32 56h40" stroke="#475569" stroke-width="6"/>'),
    ('FED7AA', 'F97316', '<path d="M28 39L25 20l18 12m33 7l3-19-18 12" fill="#F97316"/>', '<path d="M47 30l3 9m7-9l-3 9M35 43l9 4m25-4l-9 4" stroke="#7C2D12" stroke-width="3"/>'),
    ('FECDD3', 'F9A8D4', '<ellipse cx="37" cy="25" rx="9" ry="22" fill="#F9A8D4"/><ellipse cx="67" cy="25" rx="9" ry="22" fill="#F9A8D4"/>', '<circle cx="52" cy="59" r="3" fill="#BE185D"/>'),
    ('DDD6FE', 'A78B71', '<circle cx="33" cy="34" r="13" fill="#A78B71"/><circle cx="71" cy="34" r="13" fill="#A78B71"/>', '<ellipse cx="52" cy="57" rx="14" ry="11" fill="#D6C3B3"/>'),
    ('E5E7EB', 'F9FAFB', '<circle cx="34" cy="34" r="13" fill="#1F2937"/><circle cx="70" cy="34" r="13" fill="#1F2937"/>', '<ellipse cx="43" cy="48" rx="7" ry="9" fill="#1F2937"/><ellipse cx="61" cy="48" rx="7" ry="9" fill="#1F2937"/>'),
    ('BBF7D0', '9CA3AF', '<circle cx="29" cy="39" r="16" fill="#9CA3AF"/><circle cx="75" cy="39" r="16" fill="#9CA3AF"/>', '<ellipse cx="52" cy="55" rx="8" ry="10" fill="#374151"/>'),
    ('FED7AA', 'F97316', '<path d="M27 43L27 17l21 18m29 8V17L56 35" fill="#F97316"/>', '<path d="M34 62q18 20 36 0" fill="#FFF7ED"/>'),
    ('BAE6FD', '1F2937', '<ellipse cx="52" cy="54" rx="23" ry="27" fill="#F9FAFB"/>', '<path d="M46 57h12l-6 7z" fill="#F59E0B"/>'),
    ('FDE68A', 'B45309', '<path d="M28 42L25 21l20 14m31 7l3-21-20 14" fill="#B45309"/>', '<circle cx="42" cy="48" r="11" fill="#FEF3C7"/><circle cx="62" cy="48" r="11" fill="#FEF3C7"/><circle cx="42" cy="48" r="4" fill="#1F2937"/><circle cx="62" cy="48" r="4" fill="#1F2937"/>'),
]
animal = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 104 104"><circle cx="52" cy="52" r="50" fill="#{bg}"/>{ears}<circle cx="52" cy="53" r="29" fill="{face}"/><circle cx="43" cy="49" r="2.5" fill="#1F2937"/><circle cx="61" cy="49" r="2.5" fill="#1F2937"/><path d="M49 59h6l-3 4z" fill="#374151"/>{extra}</svg>'''
for i, (bg, face, ears, extra) in enumerate(animals, 1):
    (out / f'animal_{i:02}.svg').write_text(animal.format(bg=bg, face=face, ears=ears, extra=extra), encoding='utf-8')
PY
```

- [ ] **Step 5: 让默认头像进入 git 和 Docker 构建**

将 `.gitignore` 中的 `static/avatars/` 改为：

```gitignore
static/avatars/*
!static/avatars/defaults/
!static/avatars/defaults/*.svg
```

将 `.dockerignore` 中的 `static/avatars/` 改为：

```dockerignore
static/avatars/*
!static/avatars/defaults/
!static/avatars/defaults/*.svg
```

- [ ] **Step 6: 验证清单测试并检查打包文件**

Run: `uv run python -m pytest tests/test_avatars.py -q`
Expected: 3 passed。
Run: `git check-ignore -v static/avatars/defaults/person_01.svg; test $? -eq 1`
Expected: 无输出且命令成功，表示默认头像未被忽略。

- [ ] **Step 7: 提交本任务**

```bash
git add utils/avatars.py tests/test_avatars.py static/avatars/defaults .gitignore .dockerignore
git commit -m "feat: add eighteen default avatar assets"
```

---

### Task 2: 空头像按用户 ID 固定回退

**Files:**
- Modify: `user_manager.py:257-296`
- Modify: `tests/test_avatars.py`

**Interfaces:**
- Consumes: `default_avatar_for_user(user_id) -> str`。
- Produces: `get_user_config()` 的 `avatar` 永远优先使用数据库值，空值才回退默认文件名。

- [ ] **Step 1: 添加失败测试，覆盖空头像与已有上传头像**

追加到 `tests/test_avatars.py`：

```python
def _profile_with_avatar(avatar):
    return {
        'id': 1, 'name': '测试', 'display_name': '测试',
        'birth_year': 1980, 'birth_month': 1, 'birth_day': 1,
        'height': 170, 'weight': 70, 'gender': 'male', 'avatar': avatar,
        'default_meals': {}, 'target_ranges': None,
    }


def test_blank_avatar_uses_stable_default_without_overwriting_database_value():
    from unittest.mock import patch
    from user_manager import UserManager

    um = UserManager(':memory:')
    with patch.object(um, 'get_user', return_value=_profile_with_avatar(None)):
        config = um.get_user_config(1)
    assert config['avatar'] == 'defaults/person_02.svg'


def test_uploaded_avatar_is_not_replaced_by_default():
    from unittest.mock import patch
    from user_manager import UserManager

    um = UserManager(':memory:')
    with patch.object(um, 'get_user', return_value=_profile_with_avatar('avatar_custom.png')):
        config = um.get_user_config(1)
    assert config['avatar'] == 'avatar_custom.png'
```

- [ ] **Step 2: 确认新测试失败**

Run: `uv run python -m pytest tests/test_avatars.py -q`
Expected: 1 failed，空头像测试当前得到 `None`；已有上传头像测试应在修改前后都通过，用于防止误覆盖。

- [ ] **Step 3: 在唯一配置出口添加固定回退**

在 `user_manager.py` 顶部添加：

```python
from utils.avatars import default_avatar_for_user
```

将 `get_user_config()` 返回字典中的：

```python
'avatar': user.get('avatar'),
```

改为：

```python
'avatar': user.get('avatar') or default_avatar_for_user(user_id),
```

- [ ] **Step 4: 运行本任务及原用户配置测试**

Run: `uv run python -m pytest tests/test_avatars.py tests/test_user_manager_extended.py -q`
Expected: 全部 PASS。

- [ ] **Step 5: 提交本任务**

```bash
git add user_manager.py tests/test_avatars.py
git commit -m "feat: assign stable default avatars to users"
```

---

### Task 3: 默认头像选择保存接口

**Files:**
- Modify: `routes/api_user.py:182-207`
- Modify: `tests/test_avatars.py`

**Interfaces:**
- Consumes: `is_default_avatar(value) -> bool`。
- Produces: `POST /select_avatar`，JSON 输入 `{'avatar': 'defaults/person_01.svg'}`；成功更新当前 session 用户。

- [ ] **Step 1: 添加持久化、非法输入、未登录和异常测试**

在 `tests/test_avatars.py` 顶部增加 `import sqlite3` 和 `from unittest.mock import patch`，然后追加：

```python
import pytest


def test_select_avatar_persists_for_current_user(client_authenticated, db_info):
    response = client_authenticated.post('/select_avatar', json={'avatar': 'defaults/animal_03.svg'})
    assert response.status_code == 200
    assert response.json['data'] == {
        'avatar': 'defaults/animal_03.svg',
        'avatar_url': '/static/avatars/defaults/animal_03.svg',
    }
    with client_authenticated.session_transaction() as session:
        user_id = session['current_user_id']
    conn = sqlite3.connect(db_info['path'])
    try:
        stored = conn.execute('SELECT avatar FROM app_users WHERE id = ?', (user_id,)).fetchone()[0]
    finally:
        conn.close()
    assert stored == 'defaults/animal_03.svg'


@pytest.mark.parametrize('value', [None, '', 123, '/etc/passwd', '../x.svg', 'defaults/unknown.svg'])
def test_select_avatar_rejects_invalid_values(client_authenticated, value):
    response = client_authenticated.post('/select_avatar', json={'avatar': value})
    assert response.status_code == 400
    assert response.json['error_type'] == 'validation_error'


def test_select_avatar_requires_login(client):
    response = client.post('/select_avatar', json={'avatar': 'defaults/person_01.svg'})
    assert response.status_code == 401


def test_select_avatar_database_error_returns_500(client_authenticated):
    with patch('routes.api_user.get_db', side_effect=Exception('db down')):
        response = client_authenticated.post('/select_avatar', json={'avatar': 'defaults/person_01.svg'})
    assert response.status_code == 500
```

- [ ] **Step 2: 确认接口测试失败**

Run: `uv run python -m pytest tests/test_avatars.py -q`
Expected: 新增接口测试失败，当前 `/select_avatar` 返回 404。

- [ ] **Step 3: 实现白名单保存接口**

在 `routes/api_user.py` 顶部添加：

```python
from utils.avatars import is_default_avatar
```

在 `upload_avatar()` 前添加：

```python
@bp_user.route('/select_avatar', methods=['POST'])
@login_required
def select_avatar():
    try:
        avatar = (request.json or {}).get('avatar')
        if not is_default_avatar(avatar):
            return api_error('请选择有效的默认头像', status_code=400, error_type='validation_error')

        user_id = user_manager.get_current_user_id()
        db = get_db()
        db.execute('UPDATE app_users SET avatar = ? WHERE id = ?', (avatar, user_id))
        db.commit()
        return api_success(
            data={'avatar': avatar, 'avatar_url': f'/static/avatars/{avatar}'},
            message='头像已更新',
        )
    except Exception as e:
        traceback.print_exc()
        return api_error(str(e), status_code=500)
```

- [ ] **Step 4: 运行接口与原上传测试**

Run: `uv run python -m pytest tests/test_avatars.py tests/test_routes_coverage.py::TestUploadAvatar tests/test_user_manager.py::TestUserUploadAvatarExcept -q`
Expected: 全部 PASS。

- [ ] **Step 5: 提交本任务**

```bash
git add routes/api_user.py tests/test_avatars.py
git commit -m "feat: persist default avatar selection"
```

---

### Task 4: 在现有弹窗加入 18 个头像选择项

**Files:**
- Modify: `app.py:15-32,187-191`
- Modify: `templates/index.html:2514-2545,8350-8459`
- Modify: `static/css/main.css`（文件末尾）
- Modify: `tests/test_avatars.py`

**Interfaces:**
- Consumes: `DEFAULT_AVATARS` 和 `POST /select_avatar`。
- Produces: `selectDefaultAvatar(filename, button)` 与 `saveDefaultAvatar()`，共用现有弹窗但不改变裁剪上传函数。

- [ ] **Step 1: 添加真实渲染和前端契约测试**

追加到 `tests/test_avatars.py`：

```python
def test_index_renders_all_default_avatar_choices(client_authenticated):
    response = client_authenticated.get('/')
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert html.count('data-avatar="defaults/') == 18
    assert html.count('data-avatar="defaults/person_') == 9
    assert html.count('data-avatar="defaults/animal_') == 9
    assert '默认头像 · 人物' in html
    assert '默认头像 · 动物' in html
    assert 'function selectDefaultAvatar' in html
    assert "fetch('/select_avatar'" in html
    assert 'function cropAndUploadAvatar' in html
```

- [ ] **Step 2: 确认界面测试失败**

Run: `uv run python -m pytest tests/test_avatars.py::test_index_renders_all_default_avatar_choices -q`
Expected: FAIL，当前页面没有 `data-avatar` 选择项。

- [ ] **Step 3: 向首页模板注入统一清单**

在 `app.py` 的 E402 导入区添加：

```python
from utils.avatars import DEFAULT_AVATARS  # noqa: E402
```

在 `render_template(...)` 调用中加入：

```python
avatar_catalog=DEFAULT_AVATARS,
```

保持现有参数顺序，只在 `user_emoji_map` 前增加该参数。

- [ ] **Step 4: 添加两组选择按钮和预览**

在 `avatarModal` 的 `.modal-body` 开头、`avatarInput` 之前插入：

```html
<div class="mb-3">
    <div class="small text-muted mb-2">默认头像 · 人物</div>
    <div class="avatar-choice-grid mb-3">
        {% for item in avatar_catalog if item.group == 'person' %}
        <button type="button" class="avatar-choice" data-avatar="{{ item.filename }}"
                aria-label="选择{{ item.label }}" onclick="selectDefaultAvatar('{{ item.filename }}', this)">
            <img src="/static/avatars/{{ item.filename }}" alt="{{ item.label }}">
        </button>
        {% endfor %}
    </div>
    <div class="small text-muted mb-2">默认头像 · 动物</div>
    <div class="avatar-choice-grid">
        {% for item in avatar_catalog if item.group == 'animal' %}
        <button type="button" class="avatar-choice" data-avatar="{{ item.filename }}"
                aria-label="选择{{ item.label }}" onclick="selectDefaultAvatar('{{ item.filename }}', this)">
            <img src="/static/avatars/{{ item.filename }}" alt="{{ item.label }}">
        </button>
        {% endfor %}
    </div>
    <div class="d-flex align-items-center gap-3 mt-3">
        <div id="defaultAvatarPreview" class="avatar-default-preview" aria-live="polite"></div>
        <button type="button" class="btn btn-primary flex-fill" id="saveDefaultAvatarBtn"
                onclick="saveDefaultAvatar()" disabled>保存所选头像</button>
    </div>
</div>
<hr>
<label for="avatarInput" class="form-label small text-muted">上传自己的图片</label>
```

- [ ] **Step 5: 添加响应式选择样式**

在 `static/css/main.css` 末尾添加：

```css
.avatar-choice-grid {
    display: grid;
    grid-template-columns: repeat(9, minmax(48px, 1fr));
    gap: 10px;
}
.avatar-choice {
    width: 52px;
    height: 52px;
    padding: 2px;
    border: 2px solid transparent;
    border-radius: 50%;
    background: transparent;
}
.avatar-choice img,
.avatar-default-preview img {
    width: 100%;
    height: 100%;
    border-radius: 50%;
    object-fit: cover;
}
.avatar-choice.is-selected {
    border-color: var(--md-primary);
    box-shadow: 0 0 0 3px var(--md-primary-light);
}
.avatar-choice:focus-visible {
    outline: 3px solid var(--md-primary);
    outline-offset: 2px;
}
.avatar-default-preview {
    width: 52px;
    height: 52px;
    flex: 0 0 52px;
}
@media (max-width: 576px) {
    .avatar-choice-grid {
        grid-template-columns: repeat(5, 48px);
        justify-content: space-between;
    }
}
```

- [ ] **Step 6: 实现选择、保存和重置行为**

在头像 JS 的 `let avatarModal = null;` 后添加：

```javascript
let selectedDefaultAvatar = null;

function selectDefaultAvatar(filename, button) {
    selectedDefaultAvatar = filename;
    document.querySelectorAll('.avatar-choice').forEach(el => {
        const selected = el === button;
        el.classList.toggle('is-selected', selected);
        el.setAttribute('aria-pressed', selected ? 'true' : 'false');
    });
    document.getElementById('defaultAvatarPreview').innerHTML =
        `<img src="/static/avatars/${filename}" alt="已选头像预览">`;
    document.getElementById('saveDefaultAvatarBtn').disabled = false;
}

async function saveDefaultAvatar() {
    if (!selectedDefaultAvatar) return;
    const button = document.getElementById('saveDefaultAvatarBtn');
    button.disabled = true;
    try {
        const res = await fetch('/select_avatar', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({avatar: selectedDefaultAvatar})
        });
        const result = await res.json();
        if (!res.ok || result.status !== 'success') {
            throw new Error(result.message || '保存失败');
        }
        avatarModal.hide();
        window.location.reload();
    } catch (e) {
        alert('头像保存失败: ' + e.message);
        button.disabled = false;
    }
}
```

在 `resetAvatarModal()` 末尾添加：

```javascript
selectedDefaultAvatar = null;
document.querySelectorAll('.avatar-choice').forEach(el => {
    el.classList.remove('is-selected');
    el.setAttribute('aria-pressed', 'false');
});
document.getElementById('defaultAvatarPreview').innerHTML = '';
document.getElementById('saveDefaultAvatarBtn').disabled = true;
```

- [ ] **Step 7: 运行界面和相关后端测试**

Run: `uv run python -m pytest tests/test_avatars.py tests/test_app_py.py::TestIndexRoute tests/test_chart_dashboard_ui.py -q`
Expected: 全部 PASS。

- [ ] **Step 8: 提交本任务**

```bash
git add app.py templates/index.html static/css/main.css tests/test_avatars.py
git commit -m "feat: add default avatar picker to profile modal"
```

---

### Task 5: 文档、浏览器持久化与全量验收

**Files:**
- Modify: `AGENTS.md`（Gotchas 增加一条头像约定）
- Modify: `tests/test_avatars.py`（仅在浏览器验收发现问题时补回归测试）

**Interfaces:**
- Consumes: 前四个任务的全部界面与接口。
- Produces: 可部署、可重复验证的完整功能。

- [ ] **Step 1: 记录头像清单与打包约定**

在 `AGENTS.md` 的 Gotchas 中加入：

```markdown
- **默认头像唯一清单是 `utils/avatars.py`**：人物/动物各 9 个，文件在 `static/avatars/defaults/`；空头像按用户 ID 固定回退，主动选择写 `app_users.avatar`，已有上传图不可被回退覆盖。新增默认头像必须同步 SVG、清单和测试；`.gitignore` 与 `.dockerignore` 只给 `defaults/` 开例外，用户上传文件仍忽略。
```

- [ ] **Step 2: 用数据库副本启动验收服务**

```bash
cp glucose.db .pytest_tmp/avatar-ui.db
SUGAR_BEE_DB_PATH=.pytest_tmp/avatar-ui.db uv run python - <<'PY'
from user_manager import UserManager
UserManager().set_password(1, 'avatar-ui-test')
PY
SUGAR_BEE_DB_PATH=.pytest_tmp/avatar-ui.db FLASK_ENV=testing SECRET_KEY=avatar-ui-only \
  uv run flask --app app run --no-reload --port 5002
```

服务保持运行，另开命令执行浏览器步骤；所有写入只发生在 `.pytest_tmp/avatar-ui.db`。

- [ ] **Step 3: 浏览器检查 18 个头像和选择保存**

```bash
playwright-cli -s=avatar-ui open http://127.0.0.1:5002
playwright-cli -s=avatar-ui snapshot
playwright-cli -s=avatar-ui fill "getByRole('textbox', { name: '用户名 / 手机号 / 邮箱' })" yzq0535
playwright-cli -s=avatar-ui fill "getByRole('textbox', { name: '请输入密码' })" avatar-ui-test
playwright-cli -s=avatar-ui click "getByRole('button', { name: '登录' })"
playwright-cli -s=avatar-ui eval "openAvatarModal()"
playwright-cli -s=avatar-ui --raw eval "document.querySelectorAll('.avatar-choice').length"
```

Expected: `18`。

选择动物兔子并保存：

```bash
playwright-cli -s=avatar-ui click "getByRole('button', { name: '选择兔子' })"
playwright-cli -s=avatar-ui click "getByRole('button', { name: '保存所选头像' })"
playwright-cli -s=avatar-ui --raw eval "document.querySelector('#unifiedUserDropdown img').getAttribute('src')"
```

Expected: `/static/avatars/defaults/animal_03.svg`。

再次刷新并检查持久化：

```bash
playwright-cli -s=avatar-ui reload
playwright-cli -s=avatar-ui --raw eval "document.querySelector('#unifiedUserDropdown img').getAttribute('src')"
playwright-cli -s=avatar-ui resize 390 844
playwright-cli -s=avatar-ui eval "openAvatarModal()"
playwright-cli -s=avatar-ui --raw eval "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
playwright-cli -s=avatar-ui console warning
```

Expected: 刷新后仍是同一 URL；窄屏无横向溢出（返回 `true`）；控制台无错误。

- [ ] **Step 4: 关闭浏览器、停止服务并删除副本**

先关闭浏览器并停止第 2 步的 Flask 进程（在该进程终端按 Ctrl+C，或停止对应后台任务），确认服务已退出后再删除副本：

```bash
playwright-cli -s=avatar-ui close
rm -f .pytest_tmp/avatar-ui.db
```

- [ ] **Step 5: 运行全量验证**

```bash
uv run python -m pytest tests/ -q
uv run ruff check utils/avatars.py user_manager.py routes/api_user.py app.py tests/test_avatars.py
git diff --check
git status --short
```

Expected: pytest 0 failed；ruff 和 diff 检查通过；没有临时数据库、浏览器日志或生成器文件混入提交。

- [ ] **Step 6: 提交文档约定**

```bash
git add AGENTS.md
git commit -m "docs: record default avatar conventions"
```
