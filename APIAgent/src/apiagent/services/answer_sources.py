"""Keep retrieval provenance separate from user-visible model prose."""

from __future__ import annotations

import html
import re
from typing import TypedDict


class AnswerSource(TypedDict):
    title: str
    document_id: str


CONTEXT = re.compile(r'<context source="(.*?)" doc_id="([^"]*)"[^>]*>([\s\S]*?)</context>')


def sources_from_reference(reference: str) -> list[AnswerSource]:
    sources = []
    seen = set()
    for title, identifier, _ in CONTEXT.findall(reference):
        source = {"title": html.unescape(title), "document_id": html.unescape(identifier)}
        identity = (source["title"], source["document_id"])
        if identity not in seen:
            sources.append(source)
            seen.add(identity)
    return sources


def readable_reference(reference: str) -> str:
    """The model needs facts and titles, not vector IDs or chunk counters."""
    matches = CONTEXT.findall(reference)
    if not matches:
        return reference
    return "\n\n".join(f'Материал «{html.unescape(title)}»:\n{body.strip()}' for title, _, body in matches)


def clean_answer(text: str, sources: list[AnswerSource]) -> str:
    """Remove leaked transport labels outside fenced code, preserving actual code."""
    identifiers = [s["document_id"] for s in sources if s["document_id"] != "unknown"]
    sections = re.split(r"(```[\s\S]*?```)", text)
    for index in range(0, len(sections), 2):
        part = sections[index]
        part = re.sub(r"(?im)^\s*(?:источник[и]?|документ[ы]?):[^\n]*(?:knowledge_reference|<context|doc_id)[^\n]*\n?", "", part)
        part = re.sub(r"</?(?:knowledge_reference|context)\b[^>]*>", "", part, flags=re.I)
        part = re.sub(r"\bknowledge_reference\b", "", part)
        part = re.sub(r"(?im)^\s*(?:chunk_index|chunk_id|similarity_score|vector_score)\s*[:=][^\n]*\n?", "", part)
        part = re.sub(r"(?im)^\s*Чанк\s*(?:№|#)?\s*\d+\s*[:—-]?[^\n]*\n?", "", part)
        for identifier in identifiers:
            part = re.sub(r"\(?\s*(?:(?:doc_id|document_id)\s*[:=]\s*)?[\"`]?" + re.escape(identifier) + r"[\"`]?\s*\)?", "", part)
        sections[index] = part
    return "".join(sections).strip()


class AgentReply(str):
    """String-compatible result for existing services; provenance is per-result."""

    def __new__(cls, text: str, sources: list[AnswerSource] | None = None, active_specialist: str | None = None):
        instance = super().__new__(cls, text)
        instance.sources = list(sources or [])
        instance.active_specialist = active_specialist
        return instance


def turn_provenance(messages):
    """Only current-turn results of server-controlled tools carry provenance."""
    latest = max((i for i, m in enumerate(messages) if m.type == "human"), default=-1)
    sources = []
    active = None
    for message in messages[latest + 1:]:
        if message.type != "tool":
            continue
        if message.name == "search_knowledge_base":
            sources.extend(sources_from_reference(str(message.content)))
        if message.name == "coding_agent" and isinstance(message.artifact, dict):
            sources.extend(message.artifact.get("sources", []))
            active = message.artifact.get("active_specialist")
    unique = {(source["title"], source["document_id"]): source for source in sources}
    return list(unique.values()), active
