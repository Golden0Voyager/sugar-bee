"""写服务的 AGENT_API_TOKEN 取值顺序。

历史背景：OpenCode 的 MCP 配置里曾经硬编码了一份明文 token，同一个密钥在
``sugar_bee/.env``（权限 600）和 ``~/.config/opencode/opencode.jsonc``（644）
各存一份。改成「环境变量优先，缺失才回退 .env」后，明文只需留在 .env 一处。

线上（Octop）跑的是 /root/health_tools 下的副本，该目录没有 .env，
所以回退逻辑在线上是空操作——这几个用例把这一点钉住。
"""
import pytest

from mcp_adapter.servers import writer


@pytest.fixture
def point_project_root_to(monkeypatch):
    """把 writer.PROJECT_ROOT 指到临时目录，隔离真实仓库的 .env。"""

    def _point(path):
        monkeypatch.setattr(writer, "PROJECT_ROOT", str(path))

    return _point


class TestResolveToken:
    def test_env_var_wins_over_dotenv(self, monkeypatch, tmp_path, point_project_root_to):
        (tmp_path / ".env").write_text("AGENT_API_TOKEN=from-dotenv\n", encoding="utf-8")
        point_project_root_to(tmp_path)
        monkeypatch.setenv("AGENT_API_TOKEN", "from-env")

        assert writer._resolve_token() == "from-env"

    def test_falls_back_to_dotenv_when_env_missing(
        self, monkeypatch, tmp_path, point_project_root_to
    ):
        (tmp_path / ".env").write_text(
            "SECRET_KEY=should-not-leak\nAGENT_API_TOKEN=from-dotenv\n", encoding="utf-8"
        )
        point_project_root_to(tmp_path)
        monkeypatch.delenv("AGENT_API_TOKEN", raising=False)

        assert writer._resolve_token() == "from-dotenv"

    def test_empty_string_when_no_env_file(self, monkeypatch, tmp_path, point_project_root_to):
        """线上就是这条：/root/health_tools 没有 .env。"""
        point_project_root_to(tmp_path)
        monkeypatch.delenv("AGENT_API_TOKEN", raising=False)

        assert writer._resolve_token() == ""

    def test_empty_string_when_key_absent_from_dotenv(
        self, monkeypatch, tmp_path, point_project_root_to
    ):
        (tmp_path / ".env").write_text("SECRET_KEY=only-this\n", encoding="utf-8")
        point_project_root_to(tmp_path)
        monkeypatch.delenv("AGENT_API_TOKEN", raising=False)

        assert writer._resolve_token() == ""

    def test_empty_string_when_dotenv_unavailable(
        self, monkeypatch, tmp_path, point_project_root_to
    ):
        """没装 python-dotenv（或文件读不了）时不得抛异常，否则写服务起不来。"""
        (tmp_path / ".env").write_text("AGENT_API_TOKEN=from-dotenv\n", encoding="utf-8")
        point_project_root_to(tmp_path)
        monkeypatch.delenv("AGENT_API_TOKEN", raising=False)

        def _boom(*_args, **_kwargs):
            raise ImportError("no dotenv")

        monkeypatch.setattr("dotenv.dotenv_values", _boom)

        assert writer._resolve_token() == ""


class TestDotenvDoesNotPolluteProcessEnv:
    def test_only_token_key_is_read(self, monkeypatch, tmp_path, point_project_root_to):
        """.env 里的其他变量（尤其数据库地址）不得进入进程环境。"""
        (tmp_path / ".env").write_text(
            "AGENT_API_TOKEN=from-dotenv\n"
            "SUGAR_BEE_DATABASE_URL=postgresql://should-not-appear/db\n",
            encoding="utf-8",
        )
        point_project_root_to(tmp_path)
        monkeypatch.delenv("AGENT_API_TOKEN", raising=False)
        monkeypatch.delenv("SUGAR_BEE_DATABASE_URL", raising=False)

        assert writer._token_from_dotenv() == "from-dotenv"
        assert "SUGAR_BEE_DATABASE_URL" not in __import__("os").environ
