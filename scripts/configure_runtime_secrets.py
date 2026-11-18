"""Generate missing deployment credentials in ignored .env; never print values."""
from pathlib import Path
import secrets

NAMES=['CHAT_API_KEY','DOCUMENTS_READ_KEY','DOCUMENTS_ADMIN_KEY','DOCUMENT_WORKER_KEY',
       'DOCLING_API_KEY','QDRANT_API_KEY','QDRANT_READ_KEY','CONSULTANT_API_KEY','CONSULTANT_SOURCE_KEY']

def main():
    path=Path(__file__).resolve().parents[1]/'.env'
    lines=path.read_text(encoding='utf-8').splitlines() if path.exists() else []
    found=set()
    for i,line in enumerate(lines):
        name,sep,value=line.partition('=')
        if sep and name in NAMES:
            found.add(name)
            if not value.strip():lines[i]=name+'='+secrets.token_hex(32)
    lines.extend(name+'='+secrets.token_hex(32) for name in NAMES if name not in found)
    path.write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print('Missing runtime credentials configured in .env; existing non-empty values preserved')

if __name__=='__main__':main()
