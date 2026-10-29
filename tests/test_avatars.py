import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

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


def test_svg_color_attributes_are_valid_hex_colors():
    import xml.etree.ElementTree as ET

    for avatar in avatars.DEFAULT_AVATARS:
        path = ROOT / 'static' / 'avatars' / avatar['filename']
        root = ET.fromstring(path.read_text(encoding='utf-8'))
        for element in root.iter():
            for attribute in ('fill', 'stroke'):
                value = element.get(attribute)
                if value is not None and value != 'none':
                    assert value.startswith('#'), (path.name, attribute, value)


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


def test_index_renders_all_default_avatar_choices(client_authenticated):
    response = client_authenticated.get('/')
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert html.count('data-avatar="defaults/') == 18
    assert html.count('data-avatar="defaults/person_') == 9
    assert html.count('data-avatar="defaults/animal_') == 9
    # 分区标题替代旧的小灰字分组标签
    assert '选一个现成头像' in html
    assert '或上传自己的照片' in html
    assert '默认头像 · 人物' not in html
    assert '默认头像 · 动物' not in html
    assert 'function selectDefaultAvatar' in html
    assert "fetch('/select_avatar'" in html
    assert 'function cropAndUploadAvatar' in html
    # 统一保存入口：右下角一个保存按钮，按选中内容分派
    assert 'function saveAvatar' in html
    assert 'saveDefaultAvatarBtn' not in html
