import re

with open("/source/data/gosapteka56/2026-10-27/source.html", "r", encoding="utf-8", errors="ignore") as f:
    html = f.read()

pattern = re.compile(r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.DOTALL | re.IGNORECASE)
matches = pattern.findall(html)

download_links = []
for href, text in matches:
    clean_text = re.sub(r'<[^>]+>', '', text).strip()
    if any(ext in href.lower() for ext in ['.pdf', '.doc', '.docx', '.xls', '.xlsx', '.zip', '.rar', 'yandex.ru', 'disk.yandex']):
        download_links.append((clean_text, href))

print(f"Total links: {len(download_links)}")
for t, h in download_links:
    print(f"{t[:60]:60s} | {h}")
