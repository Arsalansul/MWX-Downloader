import json
import unittest

from mwx import ParserError
from mwx_adapters import KakaoPageEngine, RidiEngine


class KoreanStoreAdapterTests(unittest.TestCase):
    def test_ridi_reads_embedded_series(self):
        engine = RidiEngine()
        page = """
        <meta property="og:title" content="테스트 만화 - RIDI">
        <meta property="og:description" content="설명">
        <meta property="og:image" content="https://img.test/cover.jpg">
        <script>
        var book = {"id":"42","author":"작가"};
        var bookDetail = {"series_title":"테스트 만화","author":"글작가, 그림작가","series_price_info":{"free_book_count":"1"}};
        var seriesBookListJson = [
          {"id":"42001","title":"1화","volume":1,"is_trial":true},
          {"id":"42002","title":"2화","volume":2,"is_trial":false}
        ];
        </script>
        """
        engine.fetch = lambda url, section=None: (page, url)

        manga = engine.manga("https://ridibooks.com/books/42")

        self.assertEqual(manga["title"], "테스트 만화")
        self.assertEqual(manga["author"], "글작가, 그림작가")
        self.assertEqual(len(manga["chapters"]), 2)
        self.assertTrue(manga["chapters"][0]["trial"])
        self.assertTrue(manga["chapters"][0]["free"])
        self.assertFalse(manga["chapters"][1]["free"])
        self.assertTrue(all(item["downloadable"] is False for item in manga["chapters"]))
        with self.assertRaises(ParserError):
            engine.chapter(manga["chapters"][0]["link"])

    def test_kakao_parses_api_response_but_disables_viewer_download(self):
        engine = KakaoPageEngine()
        overview = {"contentHomeOverview": {"content": {
            "title": "카카오 만화", "author": "작가", "description": "설명",
            "thumbnail": "https://img.test/cover.jpg",
        }}}
        products = {"contentHomeProductList": {"edges": [
            {"node": {"single": {"productId": 101, "title": "1화"}}},
            {"node": {"single": {"productId": 102, "title": "2화"}}},
        ]}}

        def fetch(url, section=None):
            return (json.dumps(products if "product/list" in url else overview), url)

        engine.fetch = fetch
        manga = engine.manga("https://page.kakao.com/content/57770713")

        self.assertEqual(manga["title"], "카카오 만화")
        self.assertEqual([item["title"] for item in manga["chapters"]], ["1화", "2화"])
        self.assertTrue(all(item["downloadable"] is False for item in manga["chapters"]))

    def test_kakao_reports_blocked_direct_requests(self):
        engine = KakaoPageEngine()
        engine.fetch = lambda url, section=None: ("<html>blocked</html>", url)
        with self.assertRaisesRegex(ParserError, "браузерную сессию"):
            engine.manga("https://page.kakao.com/content/57770713")


if __name__ == "__main__":
    unittest.main()
