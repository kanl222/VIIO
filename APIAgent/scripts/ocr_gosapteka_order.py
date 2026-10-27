"""Read-only OCR of the scanned 16-page order; run in the CPU PDF worker."""
from io import BytesIO
import json
from pathlib import Path
import subprocess
import pypdfium2

pdf=pypdfium2.PdfDocument('/tmp/gosapteka-order740.pdf')
parts=[]
for index in range(len(pdf)):
    page=pdf[index]
    bitmap=page.render(scale=2.5)
    image=bitmap.to_pil()
    if index in (0,len(pdf)-1):
        image.save(f'/tmp/gosapteka-740-{index+1}.png')
    buffer=BytesIO()
    image.save(buffer,format='PNG')
    result=subprocess.run(['tesseract','stdin','stdout','-l','rus+eng','--psm','3'],input=buffer.getvalue(),capture_output=True,check=True)
    text=result.stdout.decode('utf-8').strip()
    if len(text)<40:
        raise ValueError('Scanned page has too little readable text: '+str(index+1))
    parts.append(f'[Страница {index+1}]\n'+text)
    print(json.dumps({'page':index+1,'characters':len(text)}),flush=True)
    bitmap.close()
    page.close()
Path('/tmp/gosapteka-order740.txt').write_text('\n\n'.join(parts),encoding='utf-8')
pdf.close()
