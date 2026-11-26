"""Pack authorized instruction passages, not the prefix of a formatted search dump."""
import re
from collections import Counter
from viio_core.text.search import normalize_search_text

STOP_WORDS = set('какой какие какого каких нужно необходимо можно программа программе инструкция инструкции система системе информационной лекарственное обеспечение через после перед именно согласно ответь данные документа документе используется'.split())


def terms(text):
    return {word[:6] for word in re.findall(r'[а-яёa-z0-9]{3,}', normalize_search_text(text))
            if word not in STOP_WORDS and word not in {'что', 'такое', 'как', 'это'}}


def graph_definition_context(question, definitions, limit):
    """Use only exact matching, server-returned definitions for a definition question."""
    query = ' '.join(re.findall(r'\w+', normalize_search_text(question)))
    matches = []
    for item in definitions:
        if not isinstance(item, dict) or not isinstance(item.get('full_name'), str):
            continue
        names = [item.get('name'), item.get('short_form'), *(item.get('aliases') or [])]
        aliases = [' '.join(re.findall(r'\w+', normalize_search_text(str(name or '')))) for name in names]
        if not any(alias and re.fullmatch(
            r'(?:(?:что\s+\w+|расшифруй|как\s+расшифровывается)\s+)?' + re.escape(alias), query)
            for alias in aliases):
            continue
        block = f"{item.get('name', '')}: {item['full_name']}. {item.get('description') or ''}"
        if len(block) <= limit:
            matches.append(block)
            limit -= len(block) + 2
    return '\n\n'.join(matches)


def strip_invalid_citations(text, source_count):
    """Validate prose markers without changing code examples containing [1]."""
    parts = re.split(r'(```[\s\S]*?```|`[^`\n]*`)', text)
    for index in range(0, len(parts), 2):
        parts[index] = re.sub(r'(?<!\[)\[(\d+)\](?!\])',
            lambda match: match[0] if 1 <= int(match[1]) <= source_count else '', parts[index])
        parts[index] = re.sub(r'[ \t]+([.,!?;:])', r'\1', parts[index])
    return ''.join(parts)


def clean_passage(text):
    return '\n'.join(line for line in text.splitlines()
                     if not line.lstrip().startswith(('>', '#', '<!--')) and line.strip() != '---').strip()


def excerpt(text, query_terms, limit):
    blocks = [part.strip() for part in re.split(r'\n\s*\n', clean_passage(text)) if part.strip()]
    if len('\n\n'.join(blocks)) <= limit:
        return '\n\n'.join(blocks)
    ranked = sorted(range(len(blocks)), key=lambda i: len(terms(blocks[i]) & query_terms), reverse=True)
    chosen = set()
    remaining = limit
    for index in ranked:
        for neighbor in (index, index + 1):
            if neighbor >= len(blocks) or neighbor in chosen:
                continue
            block = blocks[neighbor]
            if len(block) + 2 <= remaining:
                chosen.add(neighbor)
                remaining -= len(block) + 2
    # Do not cut an instruction halfway through a line when a PDF has no blank paragraphs.
    if not chosen:
        lines = blocks[ranked[0]].splitlines()
        for i, line in sorted(enumerate(lines), key=lambda item: len(terms(item[1]) & query_terms), reverse=True):
            if len(line) > limit:
                continue
            selected = [line]
            for following in lines[i+1:]:
                if len('\n'.join(selected + [following])) > limit:
                    break
                selected.append(following)
            return '\n'.join(selected)
        return ''
    return '\n\n'.join(blocks[i] for i in sorted(chosen))


def build_evidence_context(question, fragments, max_chars=4000):
    query_terms = terms(question)
    candidates = {}
    for item in fragments:
        if not isinstance(item, dict) or not isinstance(item.get('document_id'), str):
            continue
        text = item.get('parent_context') or item.get('content')
        if not isinstance(text, str) or not any(
            line.strip() and not re.match(r'^\s*(#|>|---\s*$)', line) for line in text.splitlines()
        ):
            continue
        key = (item['document_id'], item.get('version'), tuple(item.get('section_path') or []))
        passage = clean_passage(text)
        if not passage:
            continue
        # Imported duplicates and old public windows must not occupy both slots.
        key = passage
        candidates.setdefault(key, {**item, 'text': passage})
    document_frequency = Counter(term for item in candidates.values() for term in terms(item['text']))
    def score(item):
        return sum(1 / document_frequency[term] for term in terms(item['text']) & query_terms)
    ranked = sorted(candidates.values(), key=score, reverse=True)
    blocks, sources = [], []
    remaining = max_chars
    best_score = score(ranked[0]) if ranked else 0
    for item in ranked:
        if len(sources) >= 2 or (sources and score(item) < best_score * .65):
            continue
        title = str(item.get('title') or 'Документ')[:180]
        label = f'[{len(sources)+1}] {title}\n'
        available = min(remaining-len(label)-4, 2800)
        if available < 150:
            break
        content = excerpt(item['text'], query_terms, available)
        if not content:
            continue
        block = label + content
        blocks.append(block)
        remaining -= len(block)+2
        sources.append({'document_id':item['document_id'], 'title':title})
    return '\n\n'.join(blocks), sources
