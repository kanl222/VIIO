"""Real file packages used by parser and ingestion integration tests."""
from io import BytesIO


def office_sample(suffix, marker='Контрольная фраза'):
    if suffix == '.docx':
        from docx import Document
        doc=Document()
        doc.add_heading('  Порядок   работы  ',0)
        doc.add_paragraph('  '+marker+'\u00a0\u00a0до\u00adговор\u200b  № 42.\t Сумма 1\u202f000,50 ₽.  ')
        doc.add_paragraph('Первый пункт',style='List Bullet')
        table=doc.add_table(rows=2,cols=2)
        table.cell(0,0).text='Поле';table.cell(0,1).text='Значение'
        table.cell(1,0).text='Срок';table.cell(1,1).text='14 дней'
        doc.add_paragraph('После таблицы.')
        target=BytesIO();doc.save(target);return target.getvalue()
    from odf.opendocument import OpenDocumentText
    from odf import text, table, teletype
    doc=OpenDocumentText()
    doc.text.addElement(text.H(outlinelevel=1,text='  Порядок   работы  '))
    p=text.P();teletype.addTextToElement(p,'  '+marker+'\u00a0\u00a0до\u00adговор\u200b  № 42.\t Сумма 1\u202f000,50 ₽.  ')
    doc.text.addElement(p)
    items=text.List();item=text.ListItem();item.addElement(text.P(text='Первый пункт'));items.addElement(item);doc.text.addElement(items)
    grid=table.Table()
    for values in [('Поле','Значение'),('Срок','14 дней')]:
        row=table.TableRow()
        for value in values:
            cell=table.TableCell();cell.addElement(text.P(text=value));row.addElement(cell)
        grid.addElement(row)
    doc.text.addElement(grid);doc.text.addElement(text.P(text='После таблицы.'))
    target=BytesIO();doc.save(target);return target.getvalue()
