from assistent.config import Settings
from assistent.infrastructure.tools.github_tool import GitHubMCPClient
from assistent.infrastructure.tools.postgres_tool import PostgresMCPClient
from assistent.tool_config import GitHubToolConfig, PostgresToolConfig


def test_clients_keep_only_their_configuration():
    settings = Settings(_env_file=None, github_mcp_token="test-tool-token")
    github = GitHubMCPClient(GitHubToolConfig.from_settings(settings))
    postgres = PostgresMCPClient(PostgresToolConfig.from_settings(settings))
    assert not hasattr(github.settings, "postgres_password")
    assert not hasattr(postgres.settings, "github_mcp_token")
    assert not hasattr(postgres.settings, "postgres_password")
    assert "test-tool-token" not in repr(github.settings)


def test_tool_configuration_is_a_snapshot():
    settings = Settings(_env_file=None, github_allowed_repositories=["owner/repo"])
    config = GitHubToolConfig.from_settings(settings)
    settings.github_allowed_repositories.append("other/repo")
    assert config.github_allowed_repositories == ("owner/repo",)
