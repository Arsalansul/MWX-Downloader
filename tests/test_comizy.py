import json
import unittest

from mwx_adapters import ComizyEngine


class ComizyAdapterTests(unittest.TestCase):
    def test_metadata_chapters_and_images(self):
        engine = ComizyEngine()
        next_data = {"props": {"pageProps": {"initialManga": {
            "name": "Pure Villain", "summary": "Summary", "cover": "https://img.test/cover.webp",
            "status": "Ongoing", "genres": [{"name": "Action"}], "authors": [{"name": "Author"}],
            "chapters": [
                {"id": "two", "name": "Chapter 2", "url": "/pure-villain/chapter-2"},
                {"id": "one", "name": "Chapter 1", "url": "/pure-villain/chapter-1"},
            ],
        }}}}
        title_page = """
        <meta property="og:title" content="Pure Villain - Comizy">
        <meta property="og:description" content="Summary">
        <meta property="og:image" content="https://img.test/cover.webp">
        <a href="/pure-villain/chapter-2">Chapter 2</a>
        <a href="/pure-villain/chapter-1"><span>Chapter 1</span></a>
        <a href="/pure-villain/chapter-2"><img alt="duplicate"></a>
        """ + '<script id="__NEXT_DATA__" type="application/json">' + json.dumps(next_data) + "</script>"
        chapter_page = """
        <img src="https://x1.cmzcdn.org/e/first.webp">
        <img src="https://x2.cmzcdn.org/e/second.webp">
        <script>"https://x1.cmzcdn.org/e/first.webp"</script>
        """

        def fetch(url, section=None):
            if "chapter-1" in url:
                return chapter_page, url
            return title_page, "https://mangabuddy.com/pure-villain"

        engine.fetch = fetch
        manga = engine.manga("https://mangabuddy.com/pure-villain")
        self.assertEqual(manga["title"], "Pure Villain")
        self.assertEqual(manga["author"], "Author")
        self.assertEqual(manga["genres"], ["Action"])
        self.assertEqual([item["title"] for item in manga["chapters"]], ["Chapter 1", "Chapter 2"])
        self.assertTrue(all(item["downloadable"] for item in manga["chapters"]))
        self.assertEqual(engine.chapter(manga["chapters"][0]["link"]), [
            "https://x1.cmzcdn.org/e/first.webp",
            "https://x2.cmzcdn.org/e/second.webp",
        ])


if __name__ == "__main__":
    unittest.main()
