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
