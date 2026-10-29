"""
Garmin 交互式登录脚本（一次性跑）

用法：
    uv run python3 garmin_login.py                  # 老配置：单账户（GARMIN_USER_ID）
    uv run python3 garmin_login.py --user-id 2      # 给指定应用用户绑定（推荐，支持 MFA 场景）

流程：
1. 从 .env 读 GARMIN_EMAIL / GARMIN_PASSWORD / GARMIN_IS_CN
2. 尝试登录；若 Garmin 要 MFA 则交互式提示输入验证码
3. 成功后保存 token：
   - 默认存到 .garmin_tokens/garmin_tokens.json（老配置）
   - --user-id N 时存到 .garmin_tokens/user_N/ 并登记绑定关系，该用户即可独立同步

首次成功后就不用再跑这个脚本了（token 有效期约 1 年）。
"""
import os
import sys

from dotenv import load_dotenv

load_dotenv(override=True)

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TOKEN_DIR = os.environ.get('GARMIN_TOKEN_DIR', os.path.join(_SCRIPT_DIR, '.garmin_tokens'))


def main():
    email = os.environ.get('GARMIN_EMAIL')
    password = os.environ.get('GARMIN_PASSWORD')
    if not email or not password:
        print('❌ 请先在 .env 配置 GARMIN_EMAIL / GARMIN_PASSWORD')
        sys.exit(1)

    # --user-id N：给指定应用用户绑定（多用户模式），token 存到该用户自己的目录
    user_id = None
    if '--user-id' in sys.argv:
        try:
            user_id = int(sys.argv[sys.argv.index('--user-id') + 1])
            if user_id <= 0:
                raise ValueError
        except (IndexError, ValueError):
            print('❌ --user-id 需要一个正整数，例如：uv run python3 garmin_login.py --user-id 2')
            sys.exit(1)

    is_cn = os.environ.get('GARMIN_IS_CN', '').lower() in ('1', 'true', 'yes')
    domain_hint = 'garmin.cn' if is_cn else 'garmin.com'
    print(f'🔐 尝试登录 Garmin ({domain_hint}): {email}')

    try:
        from garminconnect import Garmin, GarminConnectAuthenticationError
    except ImportError:
        print('❌ garminconnect 未安装。先跑 `uv pip install garminconnect`')
        sys.exit(1)

    client = Garmin(email=email, password=password, is_cn=is_cn)
    token_dir = TOKEN_DIR
    if user_id:
        from services.garmin_service import _user_token_dir
        token_dir = _user_token_dir(user_id)
        os.makedirs(token_dir, exist_ok=True)

    try:
        client.login(token_dir)
    except GarminConnectAuthenticationError as e:
        print(f'❌ 认证失败：{e}')
        print('可能原因：账号密码错误 / 账号被临时锁定 / 浏览器登录一次清除人机验证')
        sys.exit(1)
    except Exception as e:
        msg = str(e)
        if '429' in msg or 'rate' in msg.lower():
            print(f'❌ IP 被 Garmin 限流：{e}')
            print('建议：')
            print('  1. 安装 curl_cffi 启用更多登录策略：uv pip install curl-cffi')
            print('  2. 换手机热点 / 等 6-12 小时')
            print('  3. 先浏览器登录 https://connect.garmin.com 或 https://connect.garmin.cn')
            print('  4. 如果账号是中国区，在 .env 添加 GARMIN_IS_CN=true')
        else:
            print(f'❌ 登录异常：{e}')
        sys.exit(1)

    # 登录成功，库已自动保存 token
    try:
        full_name = client.get_full_name()
        print(f'✅ 登录成功：{full_name}')
    except Exception:
        print('✅ 登录成功')
    print(f'📁 Token 已保存到 {token_dir}/garmin_tokens.json')
    if user_id:
        from services.garmin_service import _upsert_binding
        _upsert_binding(user_id, email, is_cn)
        print(f'🔗 已为应用用户 #{user_id} 登记 Garmin 绑定')
    print('\n下一步：启动应用或跑同步命令，会自动用 token，不再需要密码。')
    print('  uv run python3 -c "import services.garmin_service as g; print(g.sync_activities(1, 30))"')


if __name__ == '__main__':
    main()
