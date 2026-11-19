"""Generate missing deployment credentials in ignored .env; never print values."""
from pathlib import Path
import secrets

NAMES=['CHAT_API_KEY','DOCUMENTS_READ_KEY','DOCUMENTS_ADMIN_KEY','DOCUMENT_WORKER_KEY',
       'DOCLING_API_KEY','QDRANT_API_KEY','QDRANT_READ_KEY','CONSULTANT_API_KEY','CONSULTANT_SOURCE_KEY']

def main():
    path=Path(__file__).resolve().parents[1]/'.env'
    lines=path.read_text(encoding='utf-8').splitlines() if path.exists() else []
    found=set()
    bootstrap_admin_created = False
    for i,line in enumerate(lines):
        name,sep,value=line.partition('=')
        name = name.strip()
        if not sep:
            continue
        if name in NAMES:
            found.add(name)
            if not value.strip():
                lines[i]=name+'='+secrets.token_hex(32)
        elif name == 'POSTGRES_PASSWORD':
            found.add(name)
            if not value.strip():
                lines[i]='POSTGRES_PASSWORD=postgres'
        elif name == 'BOOTSTRAP_ADMIN_USERNAME':
            found.add(name)
            if not value.strip():
                lines[i]='BOOTSTRAP_ADMIN_USERNAME=admin'
        elif name == 'BOOTSTRAP_ADMIN_PASSWORD':
            found.add(name)
            if not value.strip():
                lines[i]='BOOTSTRAP_ADMIN_PASSWORD='+secrets.token_urlsafe(16)
                bootstrap_admin_created = True

    lines.extend(name+'='+secrets.token_hex(32) for name in NAMES if name not in found)
    if 'POSTGRES_PASSWORD' not in found:
        lines.append('POSTGRES_PASSWORD=postgres')
    if 'BOOTSTRAP_ADMIN_USERNAME' not in found:
        lines.append('BOOTSTRAP_ADMIN_USERNAME=admin')
    if 'BOOTSTRAP_ADMIN_PASSWORD' not in found:
        lines.append('BOOTSTRAP_ADMIN_PASSWORD='+secrets.token_urlsafe(16))
        bootstrap_admin_created = True

    path.write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print('Missing runtime credentials configured in .env; existing non-empty values preserved')
    if bootstrap_admin_created:
        print('Initial bootstrap admin account configured in .env (username: admin); password saved to .env')

if __name__=='__main__':main()
