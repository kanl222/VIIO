"""Адаптер к инструментам GitHub (MCP и REST)."""

import base64
import json
import re
from datetime import datetime, timezone
from typing import Literal
from urllib.parse import quote

import httpx
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from assistent.config import Settings
from assistent.security import require_roles
from assistent.tools.mcp_adapter import call_read_tool

GITHUB_MCP_URL = "https://api.githubcopilot.com/mcp/readonly"
READ_TOOLS = frozenset({"get_file_contents", "search_code", "list_commits", "get_commit", "list_branches"})


def _format_rate_limit_error(headers: httpx.Headers | dict) -> str:
    """Форматирует сообщение о превышении лимитов GitHub API."""
    retry_after = headers.get("retry-after")
    reset_ts = headers.get("x-ratelimit-reset")
    if retry_after:
        try:
            secs = int(retry_after)
            mins = max(1, round(secs / 60))
            return f"GitHub: превышен лимит запросов (Rate Limit). Повторите попытку через {mins} мин."
        except (ValueError, TypeError):
            pass
    if reset_ts:
        try:
            reset_time = datetime.fromtimestamp(int(reset_ts), tz=timezone.utc)
            now = datetime.now(timezone.utc)
            wait_seconds = max(0, int((reset_time - now).total_seconds()))
            wait_minutes = max(1, round(wait_seconds / 60))
            time_str = reset_time.strftime("%H:%M UTC")
            return f"GitHub: превышен лимит запросов (Rate Limit). Доступ возобновится в {time_str} (через ~{wait_minutes} мин)."
        except (ValueError, TypeError):
            pass
    return "GitHub: доступ запрещён или исчерпан лимит запросов."


def _is_repository_allowed(repo: str, scope: str, allowed_list: list[str]) -> bool:
    """Проверяет, разрешён ли репозиторий политикой доступа."""
    repo = repo.strip().lower()
    scope = scope.strip().lower()
    if scope != "*":
        return repo == scope
    if not allowed_list:
        return True
    for pattern in allowed_list:
        p = pattern.strip().lower()
        if not p:
            continue
        if p == "*" or p == repo:
            return True
        if p.endswith("/*"):
            prefix = p[:-2]
            if repo.startswith(prefix + "/"):
                return True
    return False


class GitHubMCPClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def list_repositories(self, *, page: int = 1) -> str:
        """Получение списка доступных репозиториев через REST API."""
        scope = self.settings.github_repository.strip()
        url = 'https://api.github.com/user/repos' if scope == '*' else f'https://api.github.com/repos/{scope}'
        async with httpx.AsyncClient(timeout=self.settings.github_mcp_timeout_seconds, follow_redirects=True) as client:
            response = await client.get(url, headers={
                'Authorization': f'Bearer {self.settings.github_mcp_token}',
                'Accept': 'application/vnd.github+json',
            }, params={'per_page': 20, 'page': page, 'sort': 'full_name'} if scope == '*' else None)
            if response.status_code in (403, 429):
                return _format_rate_limit_error(response.headers)
            response.raise_for_status()
        rows = response.json() if scope == '*' else [response.json()]

        allowed = self.settings.github_allowed_repositories
        repos = []
        for row in rows:
            full_name = row.get('full_name', '')
            if _is_repository_allowed(full_name, scope, allowed):
                repos.append({
                    'repository': full_name,
                    'private': row.get('private', False),
                    'default_branch': row.get('default_branch', 'main')
                })

        next_page = page + 1 if scope == '*' and 'next' in response.links else None
        result = {'repositories': repos, 'next_page': next_page}
        return json.dumps(result, ensure_ascii=False)

    async def get_tree(self, owner: str, repo: str, *, sha: str = "", path: str = "") -> str:
        """Получение структуры (дерева) репозитория через Git Trees API."""
        if any(not re.fullmatch(r'[\w.-]+', item) or item in {'.', '..'} for item in (owner, repo)):
            return "Укажите корректный репозиторий в формате owner/repo."

        tree_sha = sha.strip() or "HEAD"
        url = f"https://api.github.com/repos/{quote(owner, safe='')}/{quote(repo, safe='')}/git/trees/{quote(tree_sha, safe='')}"
        async with httpx.AsyncClient(timeout=self.settings.github_mcp_timeout_seconds, follow_redirects=True) as client:
            response = await client.get(url, headers={
                "Authorization": f"Bearer {self.settings.github_mcp_token}",
                "Accept": "application/vnd.github+json",
            }, params={"recursive": "1"})
            if response.status_code in (401, 403, 404, 429):
                if response.status_code in (403, 429):
                    return _format_rate_limit_error(response.headers)
                return {
                    401: "GitHub: токен недействителен или истёк.",
                    404: "GitHub: репозиторий, ветка или коммит не найдены либо недоступны токену.",
                }.get(response.status_code, "GitHub: ошибка доступа.")
            response.raise_for_status()

        data = response.json()
        tree_entries = data.get("tree", [])
        clean_path = path.strip().strip("/")
        if clean_path:
            tree_entries = [e for e in tree_entries if e.get("path", "").startswith(clean_path + "/") or e.get("path") == clean_path]

        tree_lines = []
        for item in tree_entries:
            p = item.get("path", "")
            t = "📁 " if item.get("type") == "tree" else "📄 "
            size_info = f" ({item['size']} B)" if "size" in item else ""
            tree_lines.append(f"{t}{p}{size_info}")

        is_truncated = data.get("truncated", False)
        prefix = f"Структура {owner}/{repo} (ветка/коммит: {tree_sha}" + (f", путь: {clean_path}" if clean_path else "") + "):\n"
        output = prefix + ("\n".join(tree_lines) if tree_lines else "Каталог пуст.")
        if is_truncated:
            output += "\n[Список усечён GitHub API: превышен лимит записей]"

        limit = self.settings.github_mcp_max_chars
        if len(output) > limit:
            output = output[:limit] + "\n[Результат сокращён]"
        return output

    async def call(self, name: str, arguments: dict) -> str:
        if name not in READ_TOOLS:
            raise ValueError("Unsupported GitHub tool")
        headers = {"Authorization": f"Bearer {self.settings.github_mcp_token}",
                   "X-MCP-Readonly": "true", "X-MCP-Toolsets": "repos"}
        text, failed = await call_read_tool(
            GITHUB_MCP_URL, name, arguments, allowed=READ_TOOLS,
            timeout=self.settings.github_mcp_timeout_seconds, headers=headers)
        if (failed and name == 'get_file_contents'
                and 'not a valid internal symbolic link target' in text):
            text = await self._read_file_rest(arguments)
            failed = False
        if failed:
            if "rate limit" in text.lower():
                return "GitHub: превышен лимит запросов (Rate Limit). Повторите попытку позже."
            return "GitHub MCP отклонил запрос. Проверьте репозиторий, ветку, путь и права токена."
        limit = self.settings.github_mcp_max_chars
        return text[:limit] + ("\n[Результат сокращён]" if len(text) > limit else "")

    async def _read_file_rest(self, arguments: dict) -> str:
        """Чтение файла через REST API при сбое парсера MCP."""
        path = arguments.get('path', '').lstrip('/')
        owner, repo = arguments['owner'], arguments['repo']
        if (any(part in {'.', '..'} for part in path.split('/')) or
                any(not re.fullmatch(r'[\w.-]+', item) or item in {'.', '..'} for item in (owner, repo))):
            return 'Укажите корректный путь файла внутри репозитория.'
        url = f'https://api.github.com/repos/{quote(owner, safe="")}/{quote(repo, safe="")}/contents/{quote(path, safe="/")}'
        ref_val = arguments.get('ref') or arguments.get('sha')
        async with httpx.AsyncClient(timeout=self.settings.github_mcp_timeout_seconds, follow_redirects=True) as client:
            async with client.stream('GET', url, headers={
                'Authorization': f'Bearer {self.settings.github_mcp_token}',
                'Accept': 'application/vnd.github+json',
            }, params={'ref': ref_val} if ref_val else None) as response:
                if response.status_code in (401, 403, 404, 429):
                    if response.status_code in (403, 429):
                        return _format_rate_limit_error(response.headers)
                    return {401: 'GitHub: токен недействителен или истёк.',
                            404: 'GitHub: файл, ветка или репозиторий не найдены либо недоступны токену.'}[response.status_code]
                response.raise_for_status()
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > 2 * 1024 * 1024:
                        return 'Файл слишком велик для чтения через GitHub REST.'
        data = json.loads(body)
        if (not isinstance(data, dict) or data.get('type') != 'file'
                or data.get('submodule_git_url') or data.get('encoding') != 'base64'):
            return 'GitHub: это не обычный текстовый файл; внешние ссылки и подмодули не загружаются.'
        raw = base64.b64decode(data['content'])
        try:
            text = raw.decode('utf-16' if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig')
        except UnicodeError:
            return 'GitHub: файл имеет двоичный формат или неподдерживаемую кодировку.'
        if '\x00' in text:
            return 'GitHub: файл имеет двоичный формат.'
        return f'Файл {owner}/{repo}/{path} (GitHub REST):\n{text}'


def _clean_search_query(query: str) -> str | None:
    """Проверяет поисковый запрос, запрещая квалификаторы области поиска и опасные символы."""
    raw = query.strip()
    if not raw:
        return None
    # Запрещаем опасные управляющие символы
    if re.search(r'[\n\r\\]', raw):
        return None
    # Разрешаем C++/Rust оператор :: временно заменяя его
    sanitized = raw.replace('::', '__DOUBLE_COLON__')
    # Запрещаем GitHub search qualifiers (repo:, org:, user:, path:, etc.)
    if re.search(r'\b(repo|org|user|fork|is|path|size|stars|filename)\s*:', sanitized, re.I):
        return None
    # Запрещаем логические операторы GitHub OR / NOT в верхнем регистре, ломающие поиск
    if re.search(r'\b(?:OR|NOT)\b', sanitized):
        return None
    # Запрещаем одиночные оставшиеся двоеточия (если это не ::)
    if ':' in sanitized:
        return None
    return raw


def create_github_tool(settings: Settings, client: GitHubMCPClient | None = None):
    allowed_roles = sorted({'programmer', 'admin'}.intersection(settings.coding_allowed_roles))
    if not allowed_roles:
        return None

    backend = client or GitHubMCPClient(settings)

    @tool("github_repository")
    @require_roles(
        lambda: {"programmer", "admin"}.intersection(settings.coding_allowed_roles),
        "Доступ к GitHub разрешён только уполномоченным программистам и администраторам.",
    )
    async def github_repository(
        operation: Literal["repositories", "structure", "files", "search", "commits", "commit", "branches"],
        config: RunnableConfig, path: str = "", query: str = "", ref: str = "", sha: str = "",
        repository: str = "",
        page: int = 1,
    ) -> str:
        """Чтение данных GitHub (MCP и REST).
        operation:
          - repositories: список доступных репозиториев (пагинация через page);
          - structure: дерево каталогов и файлов репозитория (path для подкаталога, ref/sha для ветки);
          - files: чтение содержимого файла по пути path (ref для ветки);
          - search: поиск кода по query (page для пагинации);
          - commits: история коммитов (ref для ветки, page для пагинации);
          - commit: просмотр конкретного коммита по хэшу sha;
          - branches: список веток (page для пагинации).
        """
        if not settings.github_mcp_enabled:
            return "GitHub MCP выключен."
        scope = settings.github_repository.strip()
        if not settings.github_mcp_token or (scope != "*" and not re.fullmatch(r"[\w.-]+/[\w.-]+", scope)):
            return "Настройте GITHUB_MCP_TOKEN и GITHUB_REPOSITORY=owner/repo или * на сервере."

        if operation == 'repositories':
            if page < 1:
                return 'Номер страницы должен быть не меньше 1.'
            try:
                return await backend.list_repositories(page=page)
            except Exception:
                return 'Не удалось получить репозитории GitHub. Проверьте токен, его срок действия и права доступа.'

        repository = repository.strip()
        if scope != "*" and repository and repository.lower() != scope.lower():
            return "Репозиторий вне разрешённой сервером области доступа."
        repository = repository or (scope if scope != "*" else "")
        if repository and not re.fullmatch(r"[\w.-]+/[\w.-]+", repository):
            return "Укажите repository в формате owner/repo."
        if not repository and operation != "search":
            return "Укажите repository=owner/repo для чтения файлов, структуры, веток или истории."

        # Проверка allowlist репозиториев при scope=*
        if repository and not _is_repository_allowed(repository, scope, settings.github_allowed_repositories):
            return "Репозиторий не входит в список разрешённых."

        parts = repository.split("/") if repository else []
        owner, repo_name = (parts[0], parts[1]) if len(parts) == 2 else ("", "")
        args = {"owner": owner, "repo": repo_name} if repository else {}

        if operation == "structure":
            try:
                target_sha = sha.strip() or ref.strip()
                return await backend.get_tree(owner, repo_name, sha=target_sha, path=path)
            except Exception:
                return "Не удалось получить структуру репозитория. Проверьте репозиторий, ветку и права доступа."

        if operation == "files":
            name = "get_file_contents"
            args.update(path=path)
            branch_or_ref = ref.strip() or sha.strip()
            if branch_or_ref:
                args["ref"] = branch_or_ref
        elif operation == "search":
            cleaned = _clean_search_query(query)
            if not cleaned:
                return "Для поиска передайте обычные слова без квалификаторов GitHub."
            search_query = f"repo:{repository} {cleaned}" if repository else cleaned
            name, args = "search_code", {"query": search_query, "perPage": 20, "page": page}
        elif operation == "commits":
            name = "list_commits"
            args.update(perPage=20, page=page)
            commit_ref = ref.strip() or sha.strip()
            if commit_ref:
                args["sha"] = commit_ref
        elif operation == "commit":
            target_sha = sha.strip() or ref.strip()
            if not target_sha:
                return "Укажите хэш коммита в sha."
            name = "get_commit"
            args.update(sha=target_sha, perPage=20)
        else:
            name = "list_branches"
            args.update(perPage=20, page=page)

        try:
            return await backend.call(name, args)
        except Exception:
            return "GitHub MCP недоступен. Проверьте сеть, токен и доступ к репозиторию."

    github_repository.metadata = {
        'allowed_roles': allowed_roles,
    }
    return github_repository
