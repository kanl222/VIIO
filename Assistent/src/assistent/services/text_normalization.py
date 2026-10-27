"""Conservative prose cleanup; never applied to source-code uploads or Markdown."""
import re
import unicodedata


def normalize_document_text(content: str) -> str:
    content = unicodedata.normalize('NFC', content)
    content = content.replace('\r\n', '\n').replace('\r', '\n').replace('\f', '\n\n').replace('\v', '\n')
    content = content.translate(str.maketrans({'\u00a0':' ', '\u202f':' ', '\u2007':' ',
        '\u200b':'', '\ufeff':'', '\u2060':'', '\u00ad':''}))
    content = re.sub(r'[\x00-\x08\x0e-\x1f\x7f]', '', content)
    lines, fence = [], None
    for raw in content.split('\n'):
        marker = re.match(r'^\s*(`{3,}|~{3,})', raw)
        if marker:
            symbol = marker[1][0]
            if fence is None:
                fence = (symbol, len(marker[1]))
            elif symbol == fence[0] and len(marker[1]) >= fence[1]:
                fence = None
            lines.append(raw.rstrip())
            continue
        if fence:
            lines.append(raw)
            continue
        expanded = raw.expandtabs(2)
        indent = len(expanded) - len(expanded.lstrip())
        line = re.sub(r'[^\S\n]+', ' ', expanded).strip()
        # Keep nesting of real list items, remove layout indentation elsewhere.
        if re.match(r'^(?:[-*•]|\d+[.)])\s', line):
            line = ' ' * min(indent, 16) + line
        if line or (lines and lines[-1] != ''):
            lines.append(line)
    return '\n'.join(lines).strip('\n')
