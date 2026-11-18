"""Keep retrieval provenance separate from user-visible model prose."""

from __future__ import annotations

import html
import json
import re
from collections.abc import Sequence
from typing import Any, TypedDict

from viio_core.text.strip_thinking import strip_thinking


class AnswerSource(TypedDict):
    title: str
    document_id: str


CONTEXT = re.compile(r'<context source="(.*?)" doc_id="([^"]*)"[^>]*>([\s\S]*?)</context>')

# Границы слов обязательны: иначе «нет» совпадает внутри «интернет», «нетто» и т.п.
# [^.!?\n] не даёт условию перескакивать через границы предложений.
_NO_DATA = re.compile(
    r"(?:сведени[яй]|информаци[яи]|данны[ех]|упоминани[яй])[^.!?\n]{0,100}"
    r"\b(?:отсутству\w*|не найден\w*|нет)\b"
    r"|недостаточно данных|невозможно определить"
    r"|\bнет (?:информации|сведений)\b|не удалось (?:найти|получить)",
    re.I,
)

# Фальшивая реплика пользователя/ассистента, дописанная моделью после пустой строки
_FAKE_TURN = re.compile(r"(?is)\n[ \t]*\n[ \t]*(?:user|human|assistant)[ \t]*:.*\Z")
_CJK = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]+")

MIN_TITLE_MATCH_LEN = 4


def _source(item: Any) -> AnswerSource | None:
    """Приводит источник от любого инструмента к {title, document_id}; без обоих полей отбрасывает."""
    if not isinstance(item, dict):
        return None
    title = str(item.get("title") or "")
    identifier = str(item.get("document_id") or item.get("id") or "")
    if not title and not identifier:
        return None
    result: dict[str, Any] = {"title": title, "document_id": identifier}
    if item.get("type"):
        result["type"] = item["type"]
    return result  # type: ignore[return-value]


def reference_title(title: str, body: str) -> str:
    """Prefer the document heading over a filename-like catalog title."""
    title = html.unescape(title)
    if '_' in title and re.fullmatch(r'[a-zA-Z0-9_.-]+', title):
        heading = re.match(r'\s*# +([^\n<>]{3,300})(?=\r?\n|$)', body)
        if heading:
            return html.unescape(heading[1].strip())
    return title


def sources_from_reference(reference: str) -> list[AnswerSource]:
    sources = []
    seen = set()
    for title, identifier, body in CONTEXT.findall(reference):
        source = {"title": reference_title(title, body), "document_id": html.unescape(identifier)}
        identity = (source["title"], source["document_id"])
        if identity not in seen:
            sources.append(source)
            seen.add(identity)
    return sources


def reorder_lost_in_the_middle(items: Sequence[Any]) -> list[Any]:
    """Reorder items to mitigate the 'lost in the middle' phenomenon (Liu et al. 2023).

    Places the most relevant items at the beginning and end of the context
    where LLMs exhibit primacy and recency bias, and less relevant items in the middle.
    """
    item_list = list(items)
    if len(item_list) <= 2:
        return item_list

    left: list[Any] = []
    right: list[Any] = []
    for i, item in enumerate(item_list):
        if i % 2 == 0:
            left.append(item)
        else:
            right.insert(0, item)
    return left + right


def readable_reference(reference: str, max_chars: int | None = None, *, include_ids: bool = False) -> str:
    """Format excerpts, optionally exposing document IDs for targeted follow-up."""
    matches = CONTEXT.findall(reference)
    if not matches:
        return reference[:max_chars]
    fragments = []
    for title, identifier, body in matches:
        label = f'Материал «{reference_title(title, body)}»'
        if include_ids and identifier and identifier != 'unknown':
            label += f' (doc_id: {html.unescape(identifier)})'
        fragments.append(f'{label}:\n{body.strip()}')
    if max_chars is None:
        return "\n\n".join(reorder_lost_in_the_middle(fragments))
    # Длинный первый результат не должен занимать всё место для последующих релевантных источников.
    available = max(0, max_chars - 2 * (len(fragments) - 1))
    first_budget = min(len(fragments[0]), available if len(fragments) == 1 else available // 2)
    remaining = (available - first_budget) // max(1, len(fragments) - 1)
    excerpts = [fragments[0][:first_budget], *(fragment[:remaining] for fragment in fragments[1:])]
    reordered_excerpts = reorder_lost_in_the_middle(excerpts)
    return "\n\n".join(reordered_excerpts)[:max_chars]


def clean_answer(text: str, sources: list[AnswerSource], *, strip_cjk: bool = True) -> str:
    """Remove leaked transport labels outside fenced code, preserving actual code.

    strip_cjk=False нужен, когда пользователь сам пишет по-китайски/по-японски.
    """
    text = strip_thinking(text)
    identifiers = [s["document_id"] for s in sources if s["document_id"] not in {"", "unknown"}]
    sections = re.split(r"(```[\s\S]*?```)", text)
    last = len(sections) - 1  # split с одной группой всегда даёт нечётную длину: последний элемент - текст
    for index in range(0, len(sections), 2):
        part = sections[index]
        # Выдуманное продолжение диалога обрезаем только в конце ответа,
        # иначе пострадают легитимные строки вроде «User: admin» в середине.
        if index == last:
            part = _FAKE_TURN.sub("", part)
        if strip_cjk:
            part = _CJK.sub("", part)
        part = re.sub(r"(?im)^\s*(?:источник[и]?|документ[ы]?):[^\n]*(?:knowledge_reference|<context|doc_id)[^\n]*\n?", "", part)
        part = re.sub(r"</?(?:knowledge_reference|context)\b[^>]*>", "", part, flags=re.I)
        part = re.sub(r"\bknowledge_reference\b", "", part)
        part = re.sub(r"(?im)^\s*(?:chunk_index|chunk_id|similarity_score|vector_score)\s*[:=][^\n]*\n?", "", part)
        part = re.sub(r"(?im)^\s*Чанк\s*(?:№|#)?\s*\d+\s*[:—-]?[^\n]*\n?", "", part)
        part = re.sub(r"[ \t]*\(doc_id:[ \t]*[^\s)]+\)", "", part)
        for identifier in identifiers:
            part = re.sub(r"(?<![\w./%=:-])\(?[ \t]*(?:(?:doc_id|document_id)[ \t]*[:=][ \t]*)?[\"`]?"
                          + re.escape(identifier) + r"(?![\w./-])[\"`]?[ \t]*\)?", "", part)
        sections[index] = part
    return "".join(sections).strip()


class AgentReply(str):
    """String-compatible result for existing services; provenance is per-result.

    Внимание: строковые методы (.strip(), +, срезы) возвращают обычный str без sources.
    Провенанс нужно брать из исходного объекта AgentReply, а не из результата обработки.
    """

    def __new__(cls, text: str, sources: list[AnswerSource] | None = None, active_specialist: str | None = None, *, saved_documents: list | None = None, interrupt: Any = None):
        instance = super().__new__(cls, text)
        instance.sources = list(sources or [])
        instance.active_specialist = active_specialist
        instance.saved_documents = list(saved_documents or [])
        instance.interrupt = interrupt
        return instance


def cited_sources(text: str, candidates: list[AnswerSource]) -> list[AnswerSource]:
    """Match cited titles or explicit document IDs among current candidates.

    This is a conservative attribution filter, not a factual entailment verifier.
    """
    prose = re.sub(r"```[\s\S]*?```", "", strip_thinking(text))
    if _NO_DATA.search(prose):
        return []
    normalized = " ".join(prose.casefold().replace("ё", "е").split())
    cited_ids = set(re.findall(r'\(doc_id:\s*([^\s)]+)\)', prose))
    matched = {}
    for source in candidates:
        title = " ".join(source["title"].casefold().replace("ё", "е").split())
        stem = re.sub(r'\.(pdf|docx|odt|txt|md)$', '', title).strip()
        identifier = source["document_id"]
        key = identifier if identifier not in {"", "unknown"} else (identifier, title)
        # Короткие названия («1С», «ИС») совпадали бы почти с любым ответом
        if (len(title) >= MIN_TITLE_MATCH_LEN and title in normalized) or \
           (len(stem) >= MIN_TITLE_MATCH_LEN and stem in normalized):
            matched[key] = source
        elif identifier in cited_ids:
            matched.setdefault(key, source)
    return list(matched.values())


def turn_provenance(messages):
    """Only current-turn results of server-controlled tools carry provenance."""
    latest = max((i for i, m in enumerate(messages) if m.type == "human"), default=-1)
    sources: list[AnswerSource] = []
    active = None

    def add(items):
        for item in items or []:
            normalized = _source(item)
            if normalized:
                sources.append(normalized)

    for message in messages[latest + 1:]:
        if message.type != "tool":
            continue
        artifact = message.artifact if isinstance(message.artifact, dict) else {}
        if message.name in {"search_knowledge_base", "search_document_content"}:
            if "sources" in artifact:
                add(artifact["sources"])
            else:
                add(sources_from_reference(str(message.content)))
        elif message.name == "list_documents":
            add(artifact.get("sources"))
        elif message.name == "search_knowledge_graph":
            for s in artifact.get("sources", []):
                add([{**s, "type": "graph"}])
        elif message.name == "coding_agent":
            add(artifact.get("sources"))
            active = artifact.get("active_specialist")
        elif message.name == "github_repository":
            content = str(message.content)
            # Файл owner/repo/path
            file_match = re.search(r"Файл\s+([\w.-]+/[\w.-]+)/([^\s\n:]+)", content)
            if file_match:
                repo, path = file_match.group(1), file_match.group(2)
                add([{"title": f"{repo}/{path}", "document_id": f"github:{repo}/{path}"}])
            # Структура owner/repo
            tree_match = re.search(r"Структура\s+([\w.-]+/[\w.-]+)", content)
            if tree_match and not file_match:
                repo = tree_match.group(1)
                add([{"title": repo, "document_id": f"github:{repo}"}])
            if "repositories" in content and not file_match and not tree_match:
                try:
                    data = json.loads(content)
                    if isinstance(data, dict) and "repositories" in data:
                        add([{"title": "GitHub", "document_id": "github:repositories"}])
                except Exception:
                    pass
    unique = {(s["title"], s["document_id"]): s for s in sources}
    return list(unique.values()), active