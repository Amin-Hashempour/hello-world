#!/usr/bin/env python3
"""Local news reader. Python 3.10+, no third-party dependencies."""
import concurrent.futures
import json
import re
import argparse
import xml.etree.ElementTree as ET
from html import unescape
from html.parser import HTMLParser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
SOURCES = {
    'Z': ('زومجی', 'https://www.zoomg.ir', ['/feed/', '/rss/']),
    'V': ('ویجیاتو', 'https://vigiato.net', ['/feed/']),
    'G': ('گیمفا', 'https://gamefa.com', ['/feed/']),
}

class Images(HTMLParser):
    def __init__(self):
        super().__init__(); self.image = ''
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'img' and not self.image:
            self.image = attrs.get('data-src') or attrs.get('src', '')

def safe_url(value, base):
    value = urljoin(base, value or '')
    return value if urlsplit(value).scheme in ('http', 'https') else ''

def parse_feed(data, base):
    root = ET.fromstring(data)
    def local(tag): return tag.rsplit('}', 1)[-1]
    entries = [e for e in root.iter() if local(e.tag) in ('item', 'entry')]
    result = []
    seen = set()
    for entry in entries:
        title = ''; link = ''; published = ''; image = ''
        for node in entry.iter():
            tag = local(node.tag)
            if tag == 'title' and not title:
                title = unescape(''.join(node.itertext())).strip()
            elif tag == 'link' and not link:
                if node.get('rel', 'alternate') == 'alternate':
                    link = node.get('href') or node.text or ''
            elif tag in ('pubDate', 'published', 'updated') and not published:
                published = node.text or ''
            if tag in ('thumbnail', 'content', 'enclosure'):
                candidate = node.get('url', '')
                if candidate and (tag != 'enclosure' or node.get('type', '').startswith('image/')):
                    image = image or candidate
            if tag in ('description', 'encoded', 'content') and node.text:
                parser = Images(); parser.feed(node.text)
                image = image or parser.image
        link = safe_url(link.strip(), base)
        if not title or not link or link in seen: continue
        seen.add(link)
        result.append({'id': link, 'url': link, 'title': title,
                       'image': safe_url(image, base) if image else '', 'published': published})
    if not result: raise ValueError('فهرست مطالب قابل خواندن نیست.')
    # RSS/Atom publishers normally supply newest entries first. Dates are sorted
    # when all entries have parseable timestamps; otherwise retain publisher order.
    from email.utils import parsedate_to_datetime
    from datetime import datetime
    def timestamp(item):
        value = item['published']
        try: return parsedate_to_datetime(value).timestamp()
        except (ValueError, TypeError, OverflowError):
            try: return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
            except (ValueError, TypeError, OverflowError): return None
    if all(timestamp(item) is not None for item in result):
        result.sort(key=timestamp, reverse=True)
    return result

def fetch_source(code):
    name, base, paths = SOURCES[code]
    for path in paths:
        try:
            request = Request(base + path, headers={'User-Agent': 'Mozilla/5.0 (compatible; LocalNewsReader/1.0)', 'Accept': 'application/rss+xml, application/atom+xml, application/xml, text/xml'})
            with urlopen(request, timeout=18) as response:
                data = response.read(8 * 1024 * 1024 + 1)
            if len(data) > 8 * 1024 * 1024: raise ValueError('Feed too large')
            return code, {'items': parse_feed(data, base), 'error': None}
        except Exception:
            continue
    return code, {'items': [], 'error': f'دریافت مطالب {name} ممکن نشد؛ اتصال اینترنت یا دسترسی سایت به RSS را بررسی کنید.'}

class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs): super().__init__(*args, directory=str(ROOT), **kwargs)
    def do_GET(self):
        path = urlsplit(self.path).path
        if path == '/api/news':
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
                result = dict(pool.map(fetch_source, SOURCES))
            body = json.dumps(result, ensure_ascii=False).encode()
            self.send_response(200); self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'no-store'); self.send_header('Content-Length', str(len(body)))
            self.end_headers(); self.wfile.write(body)
        elif path in ('/', '/index.html', '/styles.css', '/app.js'):
            super().do_GET()
        else:
            self.send_error(404)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    print(f'News reader: http://127.0.0.1:{args.port}', flush=True)
    ThreadingHTTPServer(('127.0.0.1', args.port), Handler).serve_forever()
