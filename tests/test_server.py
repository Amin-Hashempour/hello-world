import unittest
from unittest.mock import patch
import server

RSS = b'''<rss xmlns:media="http://search.yahoo.com/mrss/"><channel>
<item><title>Older &amp; title</title><link>https://example.org/old</link><pubDate>Mon, 12 Oct 2026 10:00:00 GMT</pubDate><description>&lt;img src="/old.jpg"&gt;</description></item>
<item><title>Newest</title><link>https://example.org/new</link><pubDate>Tue, 13 Oct 2026 10:00:00 GMT</pubDate><media:thumbnail url="https://example.org/new.jpg"/></item>
<item><title>Duplicate</title><link>https://example.org/new</link></item>
</channel></rss>'''
class FeedTests(unittest.TestCase):
    def test_articles_dates_images_and_deduplication(self):
        items = server.parse_feed(RSS, 'https://example.org')
        self.assertEqual([x['title'] for x in items], ['Newest', 'Older & title'])
        self.assertEqual(items[0]['image'], 'https://example.org/new.jpg')
        self.assertEqual(items[1]['image'], 'https://example.org/old.jpg')
    def test_atom(self):
        xml = b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>News</title><link href="https://example.org/news"/><updated>2026-10-18T12:00:00Z</updated></entry></feed>'
        self.assertEqual(server.parse_feed(xml, 'https://example.org')[0]['url'], 'https://example.org/news')
    def test_lazy_image(self):
        xml = b'<rss><channel><item><title>Lazy</title><link>https://example.org/lazy</link><description>&lt;img data-lazy-src="/lazy.jpg"&gt;</description></item></channel></rss>'
        self.assertEqual(server.parse_feed(xml, 'https://example.org')[0]['image'], 'https://example.org/lazy.jpg')
    def test_article_open_graph_image(self):
        response = unittest.mock.MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b'<meta property="og:image" content="/cover.jpg">'
        response.headers.get_content_type.return_value = 'text/html'
        with patch('server.urlopen', return_value=response):
            self.assertEqual(server.find_article_image('https://example.org/post'), 'https://example.org/cover.jpg')
    def test_invalid_feed_is_failure(self):
        with self.assertRaises(ValueError): server.parse_feed(b'<html/>', 'https://example.org')
    def test_source_failure_is_isolated(self):
        with patch('server.urlopen', side_effect=OSError('offline')):
            code, result = server.fetch_source('V')
        self.assertEqual(code, 'V'); self.assertEqual(result['items'], []); self.assertTrue(result['error'])
    def test_unsafe_url(self):
        self.assertEqual(server.safe_url('javascript:alert(1)', 'https://example.org'), '')
if __name__ == '__main__': unittest.main()
