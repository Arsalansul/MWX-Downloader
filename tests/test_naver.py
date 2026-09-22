import json
import unittest

from mwx import ParserError
from mwx_adapters import NaverSeriesEngine, NaverWebtoonEngine


class NaverAdapterTests(unittest.TestCase):
    def test_webtoon_metadata_chapters_and_images(self):
        engine = NaverWebtoonEngine()
        info = {
            "titleName": "Test toon", "webtoonLevelCode": "BEST_CHALLENGE",
            "posterThumbnailUrl": "https://img.test/cover.jpg", "synopsis": "Summary",
            "finished": False, "communityArtists": [{"name": "Author"}],
            "genres": [{"description": "Romance"}], "challengeTagList": ["tag"],
        }
        listing = {
            "articleList": [
                {"no": 2, "subtitle": "2화", "charge": True},
                {"no": 1, "subtitle": "1화", "charge": False},
            ],
            "pageInfo": {"totalPages": 1},
        }
        chapter_html = """
        <div class="wt_viewer">
          <img src="https://img.test/1.jpg" id="content_image_0">
          <img data-src="https://img.test/2.png" id="content_image_1">
        </div>
        """

        def fetch(url, _section=None):
            if "/api/article/list/info" in url:
                return json.dumps(info), url
            if "/api/article/list?" in url:
                return json.dumps(listing), url
            return chapter_html, url

        engine.fetch = fetch
        manga = engine.manga("https://comic.naver.com/challenge/list?titleId=830864")
        self.assertEqual(manga["title"], "Test toon")
        self.assertEqual([item["title"] for item in manga["chapters"]], ["1화", "2화"])
        self.assertTrue(manga["chapters"][0]["downloadable"])
        self.assertFalse(manga["chapters"][1]["downloadable"])
        self.assertIn("/bestChallenge/detail?", manga["chapters"][0]["link"])
        self.assertEqual(engine.chapter(manga["chapters"][0]["link"]),
                         ["https://img.test/1.jpg", "https://img.test/2.png"])

    def test_series_lists_volumes_but_does_not_claim_download(self):
        engine = NaverSeriesEngine()
        detail = """
        <meta property="og:title" content="Series title">
        <meta property="og:image" content="https://img.test/cover.jpg">
        <meta property="og:description" content="Description">
        <script>sVolumeListUrl : '/comic/volumeList.series?productNo=42&amp;sortOrder=ASC&amp;totalCount=31'</script>
        """
        first_page = {"resultData": [
            {"productNo": 100 + number, "volumeNo": number, "volumnNameText": f"{number}화",
             "freeYn": "Y" if number == 1 else "N", "personNameList": "Author"}
            for number in range(1, 31)
        ]}
        second_page = {"resultData": [
            {"productNo": 131, "volumeNo": 31, "volumnNameText": "31화", "freeYn": "N", "personNameList": "Author"},
        ]}

        def fetch(url, _section=None):
            if "volumeList.series" in url:
                return json.dumps(second_page if "page=2" in url else first_page), url
            return detail, url

        engine.fetch = fetch
        manga = engine.manga("https://series.naver.com/comic/detail.series?productNo=42")
        self.assertEqual(manga["title"], "Series title")
        self.assertEqual(len(manga["chapters"]), 31)
        self.assertTrue(manga["chapters"][0]["free"])
        self.assertTrue(all(item["downloadable"] is False for item in manga["chapters"]))
        with self.assertRaises(ParserError):
            engine.chapter(manga["chapters"][0]["link"])


if __name__ == "__main__":
    unittest.main()
