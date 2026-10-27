"""Download only document links on the requested public IS LO page; no execution.

Originals and resumable inventory live in ignored data/, not in Git.
"""
import concurrent.futures
import hashlib
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse, unquote, urlencode, quote
from urllib.request import Request, urlopen

PAGE = 'https://gosapteka56.ru/onas/osmp/is-lekarstvennoe-obespechenie.html'
ROOT = Path(__file__).resolve().parents[2] / 'data' / 'gosapteka56' / '2026-10-27'


def fetch(url):
    request = Request(url, headers={'User-Agent':'VIIO document import/1.0'})
    with urlopen(request, timeout=90) as response:
        data = response.read(100 * 1024 * 1024 + 1)
        if len(data) > 100 * 1024 * 1024:
            raise ValueError('Document exceeds 100 MiB download limit')
        return data, response.headers.get_content_type(), response.url


class Links(HTMLParser):
    def __init__(self, base=PAGE):
        super().__init__()
        self.base = base
        self.current, self.items = None, []
    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.current = {'url':urljoin(self.base,dict(attrs).get('href','')), 'parts':[]}
    def handle_data(self, data):
        if self.current is not None:
            self.current['parts'].append(data)
    def handle_endtag(self, tag):
        if tag == 'a' and self.current:
            self.items.append({'url':self.current['url'],'title':' '.join(' '.join(self.current['parts']).split())})
            self.current = None


def download(item):
    try:
        url = item['url']
        if 'disk.yandex.ru' in urlparse(url).netloc:
            metadata = json.loads(fetch('https://cloud-api.yandex.net/v1/disk/public/resources?' + urlencode({'public_key':url}))[0])
            params = {'public_key':url}
            if metadata.get('type') == 'dir':
                children = metadata.get('_embedded',{}).get('items',[])
                if len(children) != 1 or children[0].get('type') != 'file':
                    raise ValueError('Public folder needs explicit enumeration')
                metadata = children[0]
                params['path'] = metadata['path']
                item['public_path'] = metadata['path']
            item['remote_name'] = metadata.get('name','')
            info = json.loads(fetch('https://cloud-api.yandex.net/v1/disk/public/resources/download?' + urlencode(params))[0])
            url = info['href']
        suffix = Path(unquote(urlparse(item.get('remote_name') or item['url']).path)).suffix.lower()
        if not suffix:
            suffix = '.zip'
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', item['title'])[:150].strip(' .') or 'document'
        path = ROOT / 'originals' / f"{item['number']:02d} {name}{suffix}"
        if path.exists():
            data = path.read_bytes()
            content_type = item.get('content_type','')
        else:
            data, content_type, resolved = fetch(url)
            if content_type == 'text/html' or data.lstrip().lower().startswith((b'<!doctype html',b'<html')):
                raise ValueError('Link returned HTML instead of a document')
            path.write_bytes(data)
        item.update(status='downloaded',path=str(path.relative_to(ROOT)),bytes=len(data),sha256=hashlib.sha256(data).hexdigest(),content_type=content_type)
    except Exception as exc:
        item.update(status='error',error=type(exc).__name__ + ': ' + str(exc)[:250])
    return item


def main():
    (ROOT / 'originals').mkdir(parents=True,exist_ok=True)
    html, _, _ = fetch(PAGE)
    (ROOT / 'source.html').write_bytes(html)
    source = html.decode('utf-8-sig')
    base = re.search(r'<base\b[^>]*href=["\']([^"\']+)',source,re.I)
    parser = Links(urljoin(PAGE,base.group(1)) if base else PAGE)
    start = source.index('Документация по ИС')
    end = source.index('Отчеты по итогам',start)
    parser.feed(source[start:end])
    items, seen = [], set()
    for item in parser.items:
        path = unquote(urlparse(item['url']).path).lower()
        if not (re.search(r'\.(?:pdf|docx?|xlsx?|rtf|odt|ods|zip|rar|7z)(?:$|/)',path) or 'disk.yandex.ru' in urlparse(item['url']).netloc):
            continue
        if item['url'] in seen:
            continue
        seen.add(item['url'])
        items.append({**item,'number':len(items)+1})
    manifest = {'source_page':PAGE,'documents':items}
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(download,item) for item in items]
        for future in concurrent.futures.as_completed(futures):
            item = future.result()
            (ROOT / 'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps({key:item.get(key) for key in ('number','title','status','bytes','error')},ensure_ascii=True),flush=True)
    print(json.dumps({'total':len(items),'downloaded':sum(item['status']=='downloaded' for item in items),'directory':str(ROOT)}),flush=True)


if __name__ == '__main__':
    main()
