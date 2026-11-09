"""Split reviewed Markdown by section; repeat context without mixing procedures."""
import re

from typing import Literal
from langchain_text_splitters import RecursiveCharacterTextSplitter


def split_document_text(
    content: str, chunk_size: int = 500, chunk_overlap: int = 50,
    chunk_unit: Literal["characters", "words"] = "characters",
) -> list[str]:
    """Split by characters or word spans, preserving source line breaks."""
    if chunk_size <= 0 or not 0 <= chunk_overlap < chunk_size:
        raise ValueError("Размер фрагмента должен быть положительным, перекрытие — меньше размера")
    if chunk_unit == "characters":
        return RecursiveCharacterTextSplitter(
            chunk_size=chunk_size, chunk_overlap=chunk_overlap,
        ).split_text(content)
    if chunk_unit != "words":
        raise ValueError("Неизвестная единица разбиения текста")
    words = list(re.finditer(r"\S+", content))
    chunks = []
    for start in range(0, len(words), chunk_size - chunk_overlap):
        end = min(start + chunk_size, len(words))
        chunks.append(content[words[start].start():words[end - 1].end()])
        if end == len(words):
            break
    return chunks


CHUNKER_VERSION = 'sections-v3'

TOC_LINE_PAT = re.compile(r'(\.{2,}|\.\s\.\s\.)\s*\d+\s*$')
NUM_HEADING_PAT = re.compile(r'^(\d+(?:\.\d+)*\.?|[Рр]аздел\s+[IVX\d]+|[Гг]лава\s+\d+)\s+([А-ЯA-ZЁ«"][^\n\r]{2,100})$')
HEADING_PAT = re.compile(r'^(#{1,6})\s+(.+?)\s*#*\s*$')


def find_toc_range(lines):
    """Detect contiguous Table of Contents (TOC) line range to avoid index pollution."""
    toc_start = None
    for i, line in enumerate(lines):
        clean = line.strip()
        if not clean:
            continue
        if clean.lower() in ('оглавление', 'содержание', 'table of contents'):
            toc_start = i
            break
        if TOC_LINE_PAT.search(clean):
            dots_count = sum(1 for l in lines[i:i + 5] if TOC_LINE_PAT.search(l.strip()))
            if dots_count >= 2:
                toc_start = i
                break
    if toc_start is None:
        return None
    consecutive_non_toc = 0
    last_toc_idx = toc_start
    for i in range(toc_start + 1, min(len(lines), toc_start + 500)):
        clean = lines[i].strip()
        if not clean or clean.startswith('[Страница') or clean.isdigit():
            continue
        if TOC_LINE_PAT.search(clean) or '..' in clean:
            last_toc_idx = i
            consecutive_non_toc = 0
        else:
            consecutive_non_toc += 1
            if consecutive_non_toc > 5:
                break
    return toc_start, last_toc_idx + 1


def sections(content, strip_toc=True):
    raw_lines = content.splitlines()
    toc_range = find_toc_range(raw_lines) if strip_toc else None

    headings, lines, fence = [], [], None
    for idx, line in enumerate(raw_lines):
        if toc_range and toc_range[0] <= idx < toc_range[1]:
            continue
        marker = re.match(r'^\s*(`{3,}|~{3,})', line)
        if marker:
            if fence is None:
                fence = (marker[1][0], len(marker[1]))
            elif marker[1][0] == fence[0] and len(marker[1]) >= fence[1]:
                fence = None
            lines.append(line)
            continue
        heading = None
        if not fence:
            m = HEADING_PAT.match(line)
            if m:
                heading = (len(m[1]), m[2].strip())
            else:
                clean = line.strip()
                if clean and len(clean) <= 120 and not TOC_LINE_PAT.search(clean):
                    mnum = NUM_HEADING_PAT.match(clean)
                    if mnum:
                        prefix, title = mnum[1], mnum[2].strip()
                        if not title.endswith(('.', ':', ';', ',')):
                            parts = prefix.rstrip('.').split('.')
                            level = min(6, len(parts) + 1)
                            heading = (level, f"{prefix} {title}")
        if heading:
            level, title = heading
            if strip_toc and title.lower() in ('оглавление', 'содержание', 'table of contents'):
                continue
            if lines or (headings and level <= headings[-1][0]):
                yield list(headings), '\n'.join(lines).strip()
            headings = [(n, t) for n, t in headings if n < level]
            headings.append((level, title))
            lines = []
        else:
            lines.append(line)
    if lines or headings:
        yield headings, '\n'.join(lines).strip()


def split_table(block, size, overlap, unit):
    """Keep normal rows intact and repeat column headers on table continuation."""
    rows = block.splitlines()
    header = '\n'.join(rows[:2])
    measure = (lambda text: len(text.split())) if unit == 'words' else len
    budget = size - measure(header) - 2
    if budget < 16:
        yield from split_document_text(block, size, overlap, unit)
        return
    group = []
    for row in rows[2:]:
        if group and measure('\n'.join(group + [row])) > budget:
            yield header + '\n' + '\n'.join(group)
            group = []
        if measure(row) > budget:
            for part in split_document_text(row, budget, min(overlap, budget - 1), unit):
                yield header + '\n' + part
        else:
            group.append(row)
    if group:
        yield header + '\n' + '\n'.join(group)
    elif len(rows) == 2:
        yield header


def table_blocks(body):
    """Separate real pipe tables while leaving fenced examples in prose."""
    lines, table, fence = [], False, None
    for line in body.splitlines():
        marker = re.match(r'^\s*(`{3,}|~{3,})', line)
        is_table = fence is None and marker is None and line.lstrip().startswith('|')
        if lines and is_table != table:
            yield table, '\n'.join(lines)
            lines = []
        table = is_table
        lines.append(line)
        if marker:
            if fence is None:
                fence = (marker[1][0], len(marker[1]))
            elif marker[1][0] == fence[0] and len(marker[1]) >= fence[1]:
                fence = None
    if lines:
        yield table, '\n'.join(lines)


def document_chunks(content, size=500, overlap=50, unit='characters', strip_toc=True):
    """Return (text, metadata) pairs from the final, human-reviewed content.

    Source parser offsets are deliberately not reused: review can edit text.
    Large paragraphs still use the configured bounded splitter.
    """
    measure = (lambda text: len(text.split())) if unit == 'words' else len
    for headings, body in sections(content, strip_toc=strip_toc):
        path = [title for _, title in headings]
        prefix = '\n'.join('#' * level + ' ' + title for level, title in headings)
        # Необычно длинные пути заголовков сохраняются полностью в метаданных.
        if measure(prefix) > size // 2:
            prefix = (' '.join(prefix.split()[:size // 2]) if unit == 'words' else prefix[:size // 2])
        budget = max(1, size - measure(prefix) - (2 if prefix else 0))
        metadata = {'section_path': path, 'chunker_version': CHUNKER_VERSION}
        if not body:
            if prefix:
                yield prefix, metadata
            continue
        # Отделяем таблицы от прозы, даже если экспортер пропустил пустую строку.
        for is_table, block in table_blocks(body):
            safe_overlap = min(overlap, budget - 1)
            rows = block.splitlines()
            has_header = is_table and len(rows) > 1 and re.match(r'^\s*\|[\s:|\-]+\|\s*$', rows[1])
            parts = (split_table(block, budget, safe_overlap, unit) if has_header
                     else split_document_text(block, budget, safe_overlap, unit))
            for part in parts:
                yield (prefix + '\n\n' + part if prefix else part), metadata
