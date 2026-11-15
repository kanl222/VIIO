"""Read-only source ingestion: bounded pieces with original line references."""
import ast
from pathlib import PurePath

CODE_EXTENSIONS = {'.py', '.js', '.jsx', '.ts', '.tsx', '.sql', '.go', '.java', '.cs', '.rb', '.php', '.rs', '.yaml', '.yml', '.json', '.toml', '.sh', '.ps1', '.xml', '.wsdl', '.c', '.cpp', '.h'}


def is_code_file(filename):
    return PurePath(filename).suffix.lower() in CODE_EXTENSIONS


def split_code(content, filename, budget=2000):
    lines = content.splitlines(keepends=True)
    boundaries = {1, len(lines) + 1}
    if filename.lower().endswith('.py'):
        try:
            boundaries.update(node.lineno for node in ast.parse(content).body
                              if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)))
        except SyntaxError:
            pass  # Неполный исходный код остается доступным для поиска по строкам.
    boundaries = sorted(boundaries)
    pieces = []
    for start, stop in zip(boundaries, boundaries[1:]):
        text, first, last = '', start, start
        for line_number in range(start, stop):
            line = lines[line_number - 1]
            if text and len(text) + len(line) > budget:
                pieces.append((text, first, last))
                text, first = '', line_number
            while len(line) > budget:
                pieces.append((line[:budget], line_number, line_number))
                line = line[budget:]
            if not text:
                first = line_number
            text += line
            last = line_number
        if text:
            pieces.append((text, first, last))
    return pieces
