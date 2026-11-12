"""Keep retrieval provenance separate from user-visible model prose."""

from __future__ import annotations

import html
import re
from typing import TypedDict


class AnswerSource(TypedDict):
    title: str
    document_id: str


CONTEXT = re.compile(r'<context source="(.*?)" doc_id="([^"]*)"[^>]*>([\s\S]*?)</context>')


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
        return "\n\n".join(fragments)
    # Длинный первый результат не должен занимать всё место для последующих релевантных источников.
    available = max(0, max_chars - 2 * (len(fragments) - 1))
    first_budget = min(len(fragments[0]), available if len(fragments) == 1 else available // 2)
    remaining = (available - first_budget) // max(1, len(fragments) - 1)
    excerpts = [fragments[0][:first_budget], *(fragment[:remaining] for fragment in fragments[1:])]
    return "\n\n".join(excerpts)[:max_chars]


def strip_thinking(text: str) -> str:
    """Remove model reasoning/thinking blocks like <think>...</think> from text."""
    if not text or not isinstance(text, str):
        return text or ""
    cleaned = re.sub(r"(?is)<(?:think|thought|reasoning)>.*?</(?:think|thought|reasoning)>\s*", "", text)
    cleaned = re.sub(r"(?is)<(?:think|thought|reasoning)>.*$", "", cleaned)
    return cleaned.strip()


def clean_answer(text: str, sources: list[AnswerSource]) -> str:
    """Remove leaked transport labels outside fenced code, preserving actual code."""
    text = strip_thinking(text)
    identifiers = [s["document_id"] for s in sources if s["document_id"] not in {"", "unknown"}]
    sections = re.split(r"(```[\s\S]*?```)", text)
    for index in range(0, len(sections), 2):
        part = sections[index]
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
    """String-compatible result for existing services; provenance is per-result."""

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
    if re.search(r"(?:сведени[яй]|информаци[яи]|данны[ех]|упоминани[яй]).{0,100}(?:отсутству|не найден|нет)|"
                 r"(?:недостаточно данных|невозможно определить|нет (?:информации|сведений)|не удалось (?:найти|получить))", prose, re.I | re.S):
        return []
    normalized = " ".join(prose.casefold().replace("ё", "е").split())
    cited_ids = set(re.findall(r'\(doc_id:\s*([^\s)]+)\)', prose))
    matched = {}
    for source in candidates:
        title = " ".join(source["title"].casefold().replace("ё", "е").split())
        stem = re.sub(r'\.(pdf|docx|odt|txt|md)$', '', title).strip()
        identifier = source["document_id"]
        key = identifier if identifier not in {"", "unknown"} else (identifier, title)
        if (title and title in normalized) or (stem and len(stem) >= 4 and stem in normalized):
            matched[key] = source
        elif identifier in cited_ids:
            matched.setdefault(key, source)
    return list(matched.values())


def turn_provenance(messages):
    """Only current-turn results of server-controlled tools carry provenance."""
    latest = max((i for i, m in enumerate(messages) if m.type == "human"), default=-1)
    sources = []
    active = None
    for message in messages[latest + 1:]:
        if message.type != "tool":
            continue
        if message.name in {"search_knowledge_base", "search_document_content"}:
            sources.extend(message.artifact.get('sources', [])
                           if isinstance(message.artifact, dict) and 'sources' in message.artifact
                           else sources_from_reference(str(message.content)))
        if message.name == 'list_documents' and isinstance(message.artifact, dict):
            sources.extend(message.artifact.get('sources', []))
        if message.name == "coding_agent" and isinstance(message.artifact, dict):
            sources.extend(message.artifact.get("sources", []))
            active = message.artifact.get("active_specialist")
        if message.name == "github_repository":
            # Извлекаем ссылки на репозиторий и файлы
            content = str(message.content)
            # Файл owner/repo/path
            file_match = re.search(r"Файл\s+([\w.-]+/[\w.-]+)/([^\s\n:]+)", content)
            if file_match:
                repo, path = file_match.group(1), file_match.group(2)
                sources.append({"title": f"{repo}/{path}", "document_id": f"github:{repo}/{path}"})
            # Структура owner/repo (ветка/коммит: sha, путь: path)
            tree_match = re.search(r"Структура\s+([\w.-]+/[\w.-]+)", content)
            if tree_match and not file_match:
                repo = tree_match.group(1)
                sources.append({"title": repo, "document_id": f"github:{repo}"})
    unique = {(source["title"], source["document_id"]): source for source in sources}
    return list(unique.values()), active
