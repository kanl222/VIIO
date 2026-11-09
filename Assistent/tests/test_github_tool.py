from unittest.mock import AsyncMock
import json
import httpx
import base64

import pytest

from assistent.config import Settings
from assistent.tools.github_tool import GitHubMCPClient, create_github_tool


@pytest.fixture
def setup_tool():
    settings = Settings(_env_file=None, github_mcp_enabled=True,
                        github_repository="company/HandbookBot2", github_mcp_token="test-only")
    backend = AsyncMock()
    backend.call.return_value = "repository data"
    return settings, backend, create_github_tool(settings, backend)


AUTH = {"configurable": {"user_context": {"user_id": "u", "role": "programmer"}}}


@pytest.mark.asyncio
@pytest.mark.parametrize("operation,extra,name", [
    ("files", {"path": "Program.cs", "ref": "main"}, "get_file_contents"),
    ("search", {"query": "Handler"}, "search_code"),
    ("commits", {}, "list_commits"),
    ("commit", {"ref": "abc123"}, "get_commit"),
    ("branches", {}, "list_branches"),
])
async def test_routes_to_ready_mcp_tools(setup_tool, operation, extra, name):
    _, backend, tool = setup_tool
    assert await tool.ainvoke({"operation": operation, **extra}, config=AUTH) == "repository data"
    called, args = backend.call.call_args.args
    assert called == name
    if operation == "search":
        assert args["query"] == "repo:company/HandbookBot2 Handler"
    else:
        assert args["owner"] == "company"
        assert args["repo"] == "HandbookBot2"


@pytest.mark.asyncio
async def test_denies_access_before_contacting_server(setup_tool):
    settings, backend, tool = setup_tool
    for context in ({}, {"user_id": 1, "role": "employee"}):
        assert "Доступ" in await tool.ainvoke({"operation": "files"}, config={
            "configurable": {"user_context": context}})
    settings.coding_allowed_roles = ["admin"]
    assert "Доступ" in await tool.ainvoke({"operation": "files"}, config=AUTH)
    backend.call.assert_not_awaited()


@pytest.mark.asyncio
async def test_configuration_scope_and_errors(setup_tool):
    settings, backend, tool = setup_tool
    for query in ("repo:other/private", "Handler OR secret", "user:test"):
        assert "обычные слова" in await tool.ainvoke({"operation": "search", "query": query}, config=AUTH)
    settings.github_mcp_token = ""
    assert "Настройте" in await tool.ainvoke({"operation": "files"}, config=AUTH)
    backend.call.assert_not_awaited()
    settings.github_mcp_token = "sensitive-value"
    backend.call.side_effect = RuntimeError("sensitive-value")
    result = await tool.ainvoke({"operation": "files"}, config=AUTH)
    assert "недоступен" in result and "sensitive-value" not in result
    assert "sensitive-value" not in repr(settings)


@pytest.mark.asyncio
async def test_client_refuses_write_tools():
    with pytest.raises(ValueError):
        await GitHubMCPClient(Settings(_env_file=None)).call("push_files", {})


@pytest.mark.asyncio
async def test_list_available_repositories(setup_tool):
    settings, backend, tool = setup_tool
    settings.github_repository = "*"
    backend.list_repositories.return_value = "company/project"
    assert await tool.ainvoke({"operation": "repositories", "page": 2}, config=AUTH) == "company/project"
    backend.list_repositories.assert_awaited_once_with(page=2)
    backend.call.assert_not_awaited()


@pytest.mark.asyncio
async def test_repository_listing_uses_authenticated_api_and_pagination(monkeypatch):
    from assistent.tools import github_tool
    requests = []
    def respond(request):
        requests.append(request)
        row = {'full_name': 'company/private', 'private': True, 'default_branch': 'main'}
        return httpx.Response(200, json=[row] if request.url.path == '/user/repos' else row,
            headers={'link': '<https://api.github.com/user/repos?page=3>; rel="next"'})
    factory = httpx.AsyncClient
    monkeypatch.setattr(github_tool.httpx, 'AsyncClient', lambda **kw: factory(transport=httpx.MockTransport(respond), **kw))
    client = GitHubMCPClient(Settings(_env_file=None, github_repository='*', github_mcp_token='test-only'))
    result = json.loads(await client.list_repositories(page=2))
    assert result['repositories'][0]['repository'] == 'company/private'
    assert result['next_page'] == 3
    assert requests[0].url.path == '/user/repos'
    assert requests[0].url.params['page'] == '2'
    client.settings.github_repository = 'company/fixed'
    await client.list_repositories()
    assert requests[-1].url.path == '/repos/company/fixed'


@pytest.mark.asyncio
async def test_repository_listing_denies_unauthorized_and_redacts_errors(setup_tool):
    settings, backend, tool = setup_tool
    settings.github_repository = '*'
    denied = await tool.ainvoke({'operation': 'repositories'}, config={'configurable': {'user_context': {'user_id': 'u', 'role': 'employee'}}})
    assert 'Доступ' in denied
    backend.list_repositories.assert_not_awaited()
    backend.list_repositories.side_effect = RuntimeError('secret-token')
    result = await tool.ainvoke({'operation': 'repositories'}, config=AUTH)
    assert 'Не удалось' in result and 'secret-token' not in result


@pytest.mark.asyncio
async def test_all_repositories_scope(setup_tool):
    settings, backend, tool = setup_tool
    settings.github_repository = "*"
    await tool.ainvoke({"operation": "search", "query": "Handler"}, config=AUTH)
    backend.call.assert_awaited_with("search_code", {"query": "Handler", "perPage": 20, "page": 1})
    await tool.ainvoke({"operation": "files", "repository": "other/project"}, config=AUTH)
    backend.call.assert_awaited_with("get_file_contents", {"owner": "other", "repo": "project", "path": ""})
    await tool.ainvoke({"operation": "search", "repository": "other/project", "query": "Handler"}, config=AUTH)
    backend.call.assert_awaited_with("search_code", {"query": "repo:other/project Handler", "perPage": 20, "page": 1})
    backend.reset_mock()
    assert "Укажите" in await tool.ainvoke({"operation": "files"}, config=AUTH)
    assert "формате" in await tool.ainvoke({"operation": "files", "repository": "bad/repo/extra"}, config=AUTH)
    backend.call.assert_not_awaited()


@pytest.mark.asyncio
async def test_repository_override_cannot_escape_fixed_scope(setup_tool):
    _, backend, tool = setup_tool
    assert "вне" in await tool.ainvoke({"operation": "files", "repository": "other/project"}, config=AUTH)
    backend.call.assert_not_awaited()


@pytest.mark.asyncio
async def test_mcp_handshake_headers_and_output_limit(monkeypatch):
    from assistent.tools import github_tool
    backend = AsyncMock(return_value=('x' * 2000, False))
    monkeypatch.setattr(github_tool, 'call_read_tool', backend)
    settings = Settings(_env_file=None, github_mcp_token='test-only', github_mcp_max_chars=1000)
    result = await GitHubMCPClient(settings).call('get_file_contents', {'owner': 'o', 'repo': 'r'})
    args, kwargs = backend.call_args
    assert args[0] == 'https://api.githubcopilot.com/mcp/readonly'
    assert kwargs['headers']['Authorization'] == 'Bearer test-only'
    assert kwargs['headers']['X-MCP-Readonly'] == 'true'
    assert args[1:] == ('get_file_contents', {'owner': 'o', 'repo': 'r'})
    assert len(result) < 1100 and 'сокращён' in result


@pytest.mark.asyncio
async def test_mcp_symlink_parser_error_reads_same_file_through_rest(monkeypatch):
    from assistent.tools import github_tool
    monkeypatch.setattr(github_tool, 'call_read_tool', AsyncMock(return_value=(
        'failed to inspect repository file: blob "abc" is not a valid internal symbolic link target', True)))
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={'type': 'file', 'encoding': 'base64', 'size': 15,
            'content': base64.b64encode(b'\xef\xbb\xbffastapi==1.0\n').decode()})
    factory = httpx.AsyncClient
    monkeypatch.setattr(github_tool.httpx, 'AsyncClient', lambda **kw: factory(transport=httpx.MockTransport(respond), **kw))
    result = await GitHubMCPClient(Settings(_env_file=None, github_mcp_token='test-only')).call(
        'get_file_contents', {'owner': 'company', 'repo': 'project', 'path': 'requirements.txt', 'ref': 'feature/test'})
    assert 'fastapi==1.0' in result and 'отклонил' not in result
    assert '\ufeff' not in result
    assert requests[0].url.host == 'api.github.com'
    assert requests[0].url.path == '/repos/company/project/contents/requirements.txt'
    assert requests[0].url.params['ref'] == 'feature/test'


@pytest.mark.asyncio
async def test_other_mcp_errors_do_not_trigger_rest_fallback(monkeypatch):
    from assistent.tools import github_tool
    monkeypatch.setattr(github_tool, 'call_read_tool', AsyncMock(return_value=('Forbidden', True)))
    client = GitHubMCPClient(Settings(_env_file=None, github_mcp_token='test-only'))
    client._read_file_rest = AsyncMock()
    result = await client.call('get_file_contents', {'owner': 'company', 'repo': 'project', 'path': 'file'})
    assert 'отклонил' in result
    client._read_file_rest.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('payload', [
    {'type': 'symlink', 'target': 'https://untrusted.invalid'},
    {'type': 'file', 'submodule_git_url': 'https://untrusted.invalid', 'encoding': 'base64', 'content': 'YQ=='},
])
async def test_rest_fallback_does_not_follow_external_links(monkeypatch, payload):
    from assistent.tools import github_tool
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(200, json=payload)
    factory = httpx.AsyncClient
    monkeypatch.setattr(github_tool.httpx, 'AsyncClient', lambda **kw: factory(transport=httpx.MockTransport(respond), **kw))
    client = GitHubMCPClient(Settings(_env_file=None, github_mcp_token='test-only'))
    result = await client._read_file_rest({'owner': 'company', 'repo': 'project', 'path': 'file'})
    assert 'не загружаются' in result
    assert len(requests) == 1 and requests[0].url.host == 'api.github.com'
    assert 'test-only' not in result


@pytest.mark.asyncio
async def test_structure_operation(setup_tool):
    settings, backend, tool = setup_tool
    backend.get_tree = AsyncMock(return_value="📁 src\n📄 src/main.py (120 B)")
    res = await tool.ainvoke({"operation": "structure", "path": "src", "ref": "dev"}, config=AUTH)
    assert "src/main.py" in res
    backend.get_tree.assert_awaited_once_with("company", "HandbookBot2", sha="dev", path="src")


@pytest.mark.asyncio
async def test_get_tree_rest_api(monkeypatch):
    from assistent.tools import github_tool
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={
            "sha": "main",
            "truncated": False,
            "tree": [
                {"path": "README.md", "type": "blob", "size": 42},
                {"path": "src", "type": "tree"},
                {"path": "src/app.py", "type": "blob", "size": 256},
                {"path": "docs/guide.md", "type": "blob", "size": 512},
            ]
        })
    factory = httpx.AsyncClient
    monkeypatch.setattr(github_tool.httpx, 'AsyncClient', lambda **kw: factory(transport=httpx.MockTransport(respond), **kw))
    client = GitHubMCPClient(Settings(_env_file=None, github_mcp_token="test-only"))

    # Дерево всего репозитория
    res_all = await client.get_tree("my-org", "my-repo")
    assert "📄 README.md (42 B)" in res_all
    assert "📁 src" in res_all
    assert "📄 src/app.py (256 B)" in res_all

    # Фильтрация по подкаталогу path="src"
    res_subdir = await client.get_tree("my-org", "my-repo", path="src")
    assert "README.md" not in res_subdir
    assert "docs/guide.md" not in res_subdir
    assert "src/app.py" in res_subdir


@pytest.mark.asyncio
async def test_empty_allowed_roles_returns_none():
    settings = Settings(_env_file=None, coding_allowed_roles=[])
    assert create_github_tool(settings) is None


@pytest.mark.asyncio
async def test_allowlist_repositories():
    settings = Settings(
        _env_file=None,
        github_mcp_enabled=True,
        github_repository="*",
        github_allowed_repositories=["org/repo1", "allowed-org/*"],
        github_mcp_token="test-only"
    )
    backend = AsyncMock()
    backend.call.return_value = "data"
    tool = create_github_tool(settings, backend)

    # Разрешённый точный репозиторий
    res1 = await tool.ainvoke({"operation": "files", "repository": "org/repo1"}, config=AUTH)
    assert res1 == "data"

    # Разрешённый репозиторий по wildcard
    res2 = await tool.ainvoke({"operation": "files", "repository": "allowed-org/any-repo"}, config=AUTH)
    assert res2 == "data"

    # Запрещённый репозиторий
    res3 = await tool.ainvoke({"operation": "files", "repository": "other-org/secret"}, config=AUTH)
    assert "не входит в список разрешённых" in res3


@pytest.mark.asyncio
async def test_search_query_allows_double_colon_and_quotes(setup_tool):
    settings, backend, tool = setup_tool
    # Запрос с :: и кавычками
    await tool.ainvoke({"operation": "search", "query": 'std::vector "exact phrase"'}, config=AUTH)
    backend.call.assert_awaited_with(
        "search_code",
        {"query": 'repo:company/HandbookBot2 std::vector "exact phrase"', "perPage": 20, "page": 1}
    )

    # Запрос с запрещённым квалификатором repo:
    res = await tool.ainvoke({"operation": "search", "query": "repo:evil/repo test"}, config=AUTH)
    assert "обычные слова" in res
