import sys
from pathlib import Path
root=Path(__file__).resolve().parents[3]
for folder in ('packages/viio-core/src', 'services/assistent/src', 'Assistent/src','Documents/src','Documents/tests','Consultant/src','RAG/src','RAG/tests'):
    sys.path.insert(0,str(root/folder))
