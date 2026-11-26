"""Split reviewed Markdown by section; repeat context without mixing procedures."""
import re

from typing import Literal
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter


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


CHUNKER_VERSION = 'markdown-procedure-v5'

TOC_LINE_PAT = re.compile(r'(\.{2,}|\.\s\.\s\.)\s*\d+\s*$')
NUM_HEADING_PAT = re.compile(r'^(\d+(?:\.\d+)*\.?|[Рр]аздел\s+[IVX\d]+|[Гг]лава\s+\d+)\s+([А-ЯA-ZЁ«"][^\n\r]{2,100})$')
HEADING_PAT = re.compile(r'^(#{1,6})\s+(.+?)\s*#*\s*$')

MARKDOWN_HEADERS: list[tuple[str, str]] = [
    ('#', 'h1'),
    ('##', 'h2'),
    ('###', 'h3'),
    ('####', 'h4'),
    ('#####', 'h5'),
    ('######', 'h6'),
]


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


def sections(content: str, strip_toc: bool = True):
    """Split reviewed Markdown by section using standard MarkdownHeaderTextSplitter.

    Preserves heading hierarchy in metadata, normalizes numbered headings,
    protects code fences against false matches, and strips Table of Contents (TOC).
    Yields (headings, body) tuples where headings is a list of (level, title).
    """
    raw_lines = content.splitlines()
    if strip_toc:
        toc_range = find_toc_range(raw_lines)
        if toc_range:
            raw_lines = raw_lines[:toc_range[0]] + raw_lines[toc_range[1]:]

    prepared_lines: list[str] = []
    code_blocks: list[str] = []
    in_code = False
    fence_char = None
    fence_len = 0
    code_buffer: list[str] = []

    for line in raw_lines:
        marker = re.match(r'^\s*(`{3,}|~{3,})', line)
        if marker:
            c = marker[1][0]
            l = len(marker[1])
            if not in_code:
                in_code = True
                fence_char = c
                fence_len = l
                code_buffer = [line]
                continue
            elif c == fence_char and l >= fence_len:
                in_code = False
                code_buffer.append(line)
                idx = len(code_blocks)
                code_blocks.append('\n'.join(code_buffer))
                prepared_lines.append(f'<!--CODE_BLOCK_{idx}-->')
                code_buffer = []
                continue

        if in_code:
            code_buffer.append(line)
            continue

        clean = line.strip()
        if clean and not clean.startswith('#'):
            mnum = NUM_HEADING_PAT.match(clean)
            if mnum:
                prefix, title = mnum[1], mnum[2].strip()
                if not re.match(r'(?i)(нажм|выбер|введ|откро|перей|укаж|запол|сохран|добав|проверь|отскан)', title) and not title.endswith(('.', ':', ';', ',')):
                    parts = prefix.rstrip('.').split('.')
                    level = min(6, len(parts) + 1)
                    prepared_lines.append('#' * level + ' ' + clean)
                    continue

        prepared_lines.append(line)

    if code_buffer:
        idx = len(code_blocks)
        code_blocks.append('\n'.join(code_buffer))
        prepared_lines.append(f'<!--CODE_BLOCK_{idx}-->')

    splitter = MarkdownHeaderTextSplitter(
        headers_to_split_on=MARKDOWN_HEADERS,
        strip_headers=True,
    )
    # Preserve empty headings for diagnostics without returning them as evidence.
    for index in range(len(prepared_lines)-1, -1, -1):
        if HEADING_PAT.match(prepared_lines[index]):
            following = next((line for line in prepared_lines[index+1:] if line.strip()), '')
            if not following or HEADING_PAT.match(following):
                prepared_lines.insert(index+1, '<!--EMPTY_SECTION-->')
    docs = splitter.split_text('\n'.join(prepared_lines))

    def restore_code(text: str) -> str:
        for idx, cb in enumerate(code_blocks):
            placeholder = f'<!--CODE_BLOCK_{idx}-->'
            if placeholder in text:
                text = text.replace(placeholder, cb)
        return text

    for doc in docs:
        headings = [
            (int(tag[1:]), doc.metadata[tag])
            for tag in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6')
            if tag in doc.metadata
        ]
        body = restore_code(doc.page_content).replace('<!--EMPTY_SECTION-->', '').strip()
        yield headings, body


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


SENTENCE_SPLIT_REGEX = re.compile(r'(?<=[.!?…])\s+(?=[А-ЯA-ZЁ«"\d])')


def split_into_sentences(text: str) -> list[str]:
    """Split text into sentences preserving punctuation."""
    if not text or not text.strip():
        return []
    paragraphs = [p.strip() for p in text.split('\n') if p.strip()]
    sentences: list[str] = []
    for p in paragraphs:
        parts = SENTENCE_SPLIT_REGEX.split(p)
        for part in parts:
            clean = part.strip()
            if clean:
                sentences.append(clean)
    return sentences


def sentence_window_chunks(content: str, window_sentences: int = 2, strip_toc: bool = True):
    """Yield fine-grained sentence-level (Small) chunks with expanded window (Big) context.

    Each yield is (small_chunk_text, metadata) where metadata contains 'window_context'.
    """
    for headings, body in sections(content, strip_toc=strip_toc):
        path = [title for _, title in headings]
        prefix = '\n'.join('#' * level + ' ' + title for level, title in headings)
        metadata = {
            'section_path': path,
            'chunker_version': 'sentence-window-v1',
            'chunker_strategy': 'small2big',
        }
        if not body:
            if prefix:
                yield prefix, {**metadata, 'window_context': prefix}
            continue

        raw_items: list[tuple[str, str]] = []
        for is_table, block in table_blocks(body):
            if is_table:
                table_parts = list(split_table(block, 800, 100, 'characters'))
                for part in table_parts:
                    raw_items.append(('table', part))
            else:
                sentences = split_into_sentences(block)
                for sent in sentences:
                    raw_items.append(('sentence', sent))

        total_items = len(raw_items)
        for idx, (kind, item_text) in enumerate(raw_items):
            small_text = prefix + '\n\n' + item_text if prefix else item_text
            if kind == 'sentence' and window_sentences > 0 and total_items > 1:
                w_start = max(0, idx - window_sentences)
                w_end = min(total_items, idx + window_sentences + 1)
                w_body = ' '.join(t for _, t in raw_items[w_start:w_end])
                window_context = prefix + '\n\n' + w_body if prefix else w_body
            else:
                window_context = small_text

            yield small_text, {
                **metadata,
                'window_context': window_context,
                'sentence_index': idx,
                'sentence_count': total_items,
                'window_sentences': window_sentences,
            }


def document_chunks(content, size=500, overlap=50, unit='characters', strip_toc=True, window_size=1):
    """Return (text, metadata) pairs from the final, human-reviewed content.

    Includes Small2Big 'window_context' in metadata when window_size > 0.
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
        if body.strip():
            # Small instructions fit as a parent document; long manuals keep section boundaries.
            metadata['parent_context'] = content if len(content) <= 12000 else body[:12000]
        if not body:
            if prefix:
                yield prefix, {**metadata, 'heading_only': True, 'window_context': prefix}
            continue

        raw_parts: list[str] = []
        for is_table, block in table_blocks(body):
            safe_overlap = min(overlap, budget - 1)
            rows = block.splitlines()
            has_header = is_table and len(rows) > 1 and re.match(r'^\s*\|[\s:|\-]+\|\s*$', rows[1])
            parts = (split_table(block, budget, safe_overlap, unit) if has_header
                     else split_document_text(block, budget, safe_overlap, unit))
            raw_parts.extend(parts)

        total_parts = len(raw_parts)
        for idx, part in enumerate(raw_parts):
            chunk_text = prefix + '\n\n' + part if prefix else part
            if window_size > 0 and total_parts > 1:
                w_start = max(0, idx - window_size)
                w_end = min(total_parts, idx + window_size + 1)
                w_body = '\n\n'.join(raw_parts[w_start:w_end])
                window_context = prefix + '\n\n' + w_body if prefix else w_body
            else:
                window_context = chunk_text

            chunk_meta = {
                **metadata,
                'window_context': window_context,
                'chunk_window_size': window_size,
            }
            yield chunk_text, chunk_meta
