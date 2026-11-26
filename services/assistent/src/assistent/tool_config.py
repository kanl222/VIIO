"""Explicit runtime configuration passed to individual tools; no environment reads."""
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from assistent.config import Settings


@dataclass
class GitHubToolConfig:
    github_mcp_enabled: bool
    github_mcp_token: str = field(repr=False)
    github_repository: str
    github_allowed_repositories: tuple[str, ...]
    github_mcp_max_chars: int
    github_mcp_timeout_seconds: int
    coding_allowed_roles: tuple[str, ...]

    @classmethod
    def from_settings(cls, settings: "Settings | GitHubToolConfig") -> "GitHubToolConfig":
        if isinstance(settings, cls):
            return settings
        return cls(
            github_mcp_enabled=settings.github_mcp_enabled,
            github_mcp_token=settings.github_mcp_token,
            github_repository=settings.github_repository,
            github_allowed_repositories=tuple(settings.github_allowed_repositories),
            github_mcp_max_chars=settings.github_mcp_max_chars,
            github_mcp_timeout_seconds=settings.github_mcp_timeout_seconds,
            coding_allowed_roles=tuple(settings.coding_allowed_roles),
        )


@dataclass(frozen=True)
class PostgresToolConfig:
    pgsql_enabled: bool
    pgsql_mcp_url: str
    pgsql_max_rows: int
    pgsql_max_chars: int
    pgsql_timeout_seconds: int

    @classmethod
    def from_settings(cls, settings: "Settings | PostgresToolConfig") -> "PostgresToolConfig":
        return cls(
            pgsql_enabled=settings.pgsql_enabled,
            pgsql_mcp_url=settings.pgsql_mcp_url,
            pgsql_max_rows=settings.pgsql_max_rows,
            pgsql_max_chars=settings.pgsql_max_chars,
            pgsql_timeout_seconds=settings.pgsql_timeout_seconds,
        )
