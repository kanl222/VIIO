"""Extract reviewable text for the requested public files; preserve every original."""
import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
import zipfile
from pathlib import Path

from viio_documents.config import Settings
from viio_documents.services.pdf_extraction import PdfExtractionService
import openpyxl

from viio_documents.services.text_normalization import normalize_document_text
from viio_documents.services.office_extraction import OfficeExtractionService

ROOT = Path(os.environ.get('GOSAPTEKA_IMPORT_ROOT', str(Path(__file__).resolve().parents[2] / 'data/gosapteka56/2026-10-27')))
sys.path.insert(0,str(ROOT/'_libs'))


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--numbers', type=int, nargs='+', help='Prepare only these manifest document numbers')
    parser.add_argument('--output', type=Path, default=ROOT / 'canonical-docling')
    args = parser.parse_args()
    settings = Settings()
    extractor = PdfExtractionService(settings)
    manifest = json.loads((ROOT/'manifest.json').read_text(encoding='utf-8'))
    target = args.output
    target.mkdir(parents=True, exist_ok=True)
    prepared=[]
    for row in manifest['documents']:
        if args.numbers and row['number'] not in args.numbers:
            continue
        path=ROOT/row['path']; ext=path.suffix.lower(); details={}; engine=''
        if ext=='.pdf':
            extracted = await extractor.extract(path.read_bytes())
            body, engine = extracted['content'], extracted['engine']
            details = {'pages': extracted['pages'], 'needs_ocr': False}
            if 'document' in extracted:
                (target / f"{row['number']:02d}.docling.json").write_text(
                    json.dumps(extracted['document'], ensure_ascii=False), encoding='utf-8')
        elif ext=='.docx':
            extracted = await OfficeExtractionService(settings).extract(path.read_bytes(), path.name)
            body, engine = extracted['content'], extracted['engine']
        elif row['number']==34:
            import xlrd
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
        if ext == '.docx':
            body=normalize_document_text(body)
        text=f"# {row['title']}\n\nИсточник: {row['url']}\nСтраница раздела: {manifest['source_page']}\n\n"+body
        destination=target/f"{row['number']:02d}.txt"
        destination.write_text(text,encoding='utf-8')
        item={**row,'path':row['path'].replace('\\','/'),'text_path':destination.relative_to(ROOT).as_posix(),
              'engine':engine,'characters':len(text),'replacement_characters':text.count('\ufffd'),
              'text_sha256':hashlib.sha256(text.encode()).hexdigest(),**details}
        prepared.append(item)
        print(json.dumps({key:item.get(key) for key in ('number','title','characters','pages','empty_pages','needs_ocr','content_scope')},ensure_ascii=True),flush=True)
    (target/'prepared.json').write_text(json.dumps({'source_page':manifest['source_page'],'documents':prepared},ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':
    asyncio.run(main())
