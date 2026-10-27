"""Extract reviewable text for the requested public files; preserve every original."""
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path

from pypdf import PdfReader
from docx import Document
import openpyxl

from viio_documents.services.text_normalization import normalize_document_text

ROOT = Path(__file__).resolve().parents[2] / 'data/gosapteka56/2026-10-27'
sys.path.insert(0,str(ROOT/'_libs'))
import xlrd


def main():
    manifest = json.loads((ROOT/'manifest.json').read_text(encoding='utf-8'))
    target = ROOT/'canonical'
    target.mkdir(exist_ok=True)
    prepared=[]
    for row in manifest['documents']:
        path=ROOT/row['path']; ext=path.suffix.lower(); details={}; engine=''
        if ext=='.pdf':
            reader=PdfReader(path)
            pages=[page.extract_text() or '' for page in reader.pages]
            details={'pages':len(pages),'empty_pages':sum(len(page.strip())<20 for page in pages)}
            body='\n\n'.join(f'[Страница {i+1}]\n'+text for i,text in enumerate(pages))
            engine='pypdf'
            if sum(len(page.strip()) for page in pages)<50:
                details['needs_ocr']=True
                ocr=target/f"{row['number']:02d}-ocr-body.txt"
                if ocr.exists():
                    body=ocr.read_text(encoding='utf-8')
                    # Checked against the rendered first page: handwritten 740, not OCR F40.
                    body=re.sub(r'(Оренбург\s+№\s*)F40\b',r'\g<1>740',body)
                    body='Текст получен OCR. Возможны ошибки в цифрах и таблицах; при расхождении используйте оригинал PDF. Реквизиты документа указаны в заголовке по исходной странице сайта.\n\n'+body
                    details.update(needs_ocr=False,ocr_review='first/last page rendered; tables not cell-by-cell verified')
                    engine='tesseract-cpu'
        elif ext=='.docx':
            doc=Document(path)
            body='\n'.join(p.text for p in doc.paragraphs)
            for table in doc.tables:
                body+='\n\n'+'\n'.join(' | '.join(cell.text for cell in cells.cells) for cells in table.rows)
            engine='python-docx'
        elif row['number']==34:
            book=xlrd.open_workbook(ROOT/'archive-contents/34_attachment.xls')
            body='\n\n'.join('Лист: '+sheet.name+'\n'+'\n'.join(' | '.join(map(str,sheet.row_values(i))) for i in range(sheet.nrows)) for sheet in book.sheets())
            engine='xlrd; complete archived XLS'
        elif row['number']==37:
            book=openpyxl.load_workbook(ROOT/'archive-contents/37_attachment.xlsx',read_only=True,data_only=True)
            body='В архиве находится полный справочник Excel. Ниже индексируется только структура и образец первых 8 строк; полный построчный поиск по справочнику не построен. Полная таблица доступна в оригинале архива.\n'
            details['content_scope']='spreadsheet_structure_and_preview'
            for sheet in book:
                body+=f'\nЛист {sheet.title}: {sheet.max_row} строк, {sheet.max_column} столбцов.\n'
                body+='\n'.join(' | '.join('' if value is None else str(value) for value in values) for values in sheet.iter_rows(max_row=8,values_only=True))
                details['rows']=sheet.max_row
            book.close(); engine='openpyxl; archive catalog only'
        elif ext=='.zip':
            with zipfile.ZipFile(path) as archive:
                names=archive.namelist()
            body='Состав архива:\n'+'\n'.join(names)
            if row['number']==40 and (ROOT/'archive-contents/40_attachment.txt').exists():
                body+='\n\n'+(ROOT/'archive-contents/40_attachment.txt').read_text(encoding='utf-8')
                engine='antiword; full archived DOC'
            else:
                body+='\nЭто архив установки программы, а не текстовая инструкция. Содержимое исполняемых файлов не индексировалось и программы не запускались.'
                engine='zip catalog only'; details['content_scope']='archive_file_inventory'
        else:
            raise ValueError('Unsupported source '+str(path))
        body=body.replace('\x00','')
        if ext in {'.pdf', '.docx'}:
            body=normalize_document_text(body)
        text=f"# {row['title']}\n\nИсточник: {row['url']}\nСтраница раздела: {manifest['source_page']}\n\n"+body
        destination=target/f"{row['number']:02d}.txt"
        destination.write_text(text,encoding='utf-8')
        item={**row,'path':row['path'].replace('\\','/'),'text_path':destination.relative_to(ROOT).as_posix(),
              'engine':engine,'characters':len(text),'replacement_characters':text.count('\ufffd'),
              'text_sha256':hashlib.sha256(text.encode()).hexdigest(),**details}
        prepared.append(item)
        print(json.dumps({key:item.get(key) for key in ('number','title','characters','pages','empty_pages','needs_ocr','content_scope')},ensure_ascii=True),flush=True)
    (ROOT/'prepared.json').write_text(json.dumps({'source_page':manifest['source_page'],'documents':prepared},ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':
    main()
