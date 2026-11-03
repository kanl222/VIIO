import sys
from pathlib import Path

root = Path(__file__).resolve().parents[2]
for relative in ('RAG/src', 'Documents/src', 'Documents/tests'):
    sys.path.insert(0, str(root / relative))
