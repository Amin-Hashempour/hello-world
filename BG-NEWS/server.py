#!/usr/bin/env python3
"""Local news reader. Python 3.10+, no third-party dependencies."""
import concurrent.futures
import hashlib
import json
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
IMAGE_URLS = {}

class Images(HTMLParser):
    def __init__(self):
        super().__init__(); self.image = ''
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if self.image:
            return
        if tag == 'meta' and attrs.get('property') in ('og:image', 'og:image:secure_url'):
            self.image = attrs.get('content', '')
        elif tag == 'meta' and attrs.get('name') in ('twitter:image', 'twitter:image:src'):
            self.image = attrs.get('content', '')
        elif tag == 'link' and attrs.get('rel') == 'image_src':
            self.image = attrs.get('href', '')
        elif tag == 'img':
            self.image = (attrs.get('data-src') or attrs.get('data-lazy-src') or
                          attrs.get('data-original') or attrs.get('src', ''))
            if not self.image and attrs.get('srcset'):
                self.image = attrs['srcset'].split(',')[0].strip().split(' ')[0]

def safe_url(value, base):
    value = urljoin(base, value or '')
    return value if urlsplit(value).scheme in ('http', 'https') else ''

def request_bytes(url, *, accept, referer='', limit=8 * 1024 * 1024, timeout=18):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36',
        'Accept': accept,
        'Accept-Language': 'fa-IR,fa;q=0.9,en-US;q=0.7,en;q=0.6',
    }
    if referer:
        headers['Referer'] = referer
    with urlopen(Request(url, headers=headers), timeout=timeout) as response:
        data = response.read(limit + 1)
        content_type = response.headers.get_content_type()
    if len(data) > limit:
        raise ValueError('Response too large')
    return data, content_type

def image_content_type(data, reported):
    if reported.startswith('image/'):
        return reported
    signatures = ((b'\xff\xd8\xff', 'image/jpeg'), (b'\x89PNG\r\n\x1a\n', 'image/png'),
                  (b'GIF87a', 'image/gif'), (b'GIF89a', 'image/gif'), (b'RIFF', 'image/webp'))
    for signature, content_type in signatures:
        if data.startswith(signature):
            return content_type
    return ''

def find_article_image(url):
    try:
        data, content_type = request_bytes(url, accept='text/html,application/xhtml+xml', limit=2 * 1024 * 1024, timeout=10)
        if content_type not in ('text/html', 'application/xhtml+xml'):
            return ''
        parser = Images()
        parser.feed(data.decode('utf-8', errors='replace'))
        return safe_url(parser.image, url)
    except Exception:
        return ''

def add_image_proxies(items):
    missing = [item for item in items if not item['image']]
    if missing:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(6, len(missing))) as pool:
            found = pool.map(find_article_image, (item['url'] for item in missing))
            for item, image in zip(missing, found):
                item['image'] = image
    for item in items:
        if item['image']:
            token = hashlib.sha256(item['image'].encode()).hexdigest()
            IMAGE_URLS[token] = (item['image'], item['url'])
            item['image'] = '/api/image/' + token
    return items

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
                candidate = (node.get('url') or node.get('href') or node.get('src') or
                             node.get('file') or '')
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
            data, _ = request_bytes(base + path, accept='application/rss+xml, application/atom+xml, application/xml, text/xml')
            return code, {'items': add_image_proxies(parse_feed(data, base)), 'error': None}
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
        elif path.startswith('/api/image/'):
            token = path.rsplit('/', 1)[-1]
            image = IMAGE_URLS.get(token)
            if not image:
                self.send_error(404); return
            try:
                try:
                    data, reported_type = request_bytes(image[0], accept='image/avif,image/webp,image/*,*/*;q=0.8', referer=image[1], limit=10 * 1024 * 1024, timeout=15)
                except Exception:
                    data, reported_type = request_bytes(image[0], accept='image/avif,image/webp,image/*,*/*;q=0.8', limit=10 * 1024 * 1024, timeout=15)
                content_type = image_content_type(data, reported_type)
                if not content_type:
                    raise ValueError('Not an image')
            except Exception:
                self.send_error(502); return
            self.send_response(200); self.send_header('Content-Type', content_type)
            self.send_header('Cache-Control', 'private, max-age=3600'); self.send_header('Content-Length', str(len(data)))
            self.end_headers(); self.wfile.write(data)
        elif path in ('/', '/index.html', '/favicon.svg', '/fonts.css', '/styles.css', '/app.js') or path.startswith('/fonts/'):
            super().do_GET()
        else:
            self.send_error(404)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    print(f'News reader: http://127.0.0.1:{args.port}', flush=True)
    ThreadingHTTPServer(('127.0.0.1', args.port), Handler).serve_forever()
