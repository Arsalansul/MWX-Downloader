import unittest

from mwx_adapters import WebtoonGlobalEngine


class WebtoonGlobalAdapterTests(unittest.TestCase):
    def test_paginates_title_and_reads_viewer_images(self):
        engine = WebtoonGlobalEngine()

        def listing(number, title):
            return f"""
            <meta property="og:description" content="Summary">
            <div class="detail_header"><img src="https://img.test/cover.png"></div>
            <h1 class="subj">Test Toon</h1><div class="author_area"><a>Author</a></div>
            <ul><li class="_episodeItem detail_list_item">
              <a href="https://www.webtoons.com/en/x/test/{title}/viewer?title_no=42&amp;episode_no={number}">
                <span class="subj"><span>{title}</span></span><span class="date">Jan {number}</span>
              </a>
            </li></ul>
            """

        viewer = """
        <div id="_imageList">
          <img class="_images" data-url="https://img.test/1.jpg?type=q90">
          <img data-url="https://img.test/thumb.jpg" class="_thumbnailImages">
          <img data-url="https://img.test/2.jpg?type=q90" class="foo _images bar">
        </div>
        """

        def fetch(url, section=None):
            if "/viewer?" in url:
                return viewer, url
            if "page=3" in url:
                return "<html></html>", url
            if "page=2" in url:
                return listing(2, "Episode 2"), url
            return listing(1, "Episode 1"), url

        engine.fetch = fetch
        manga = engine.manga("https://www.webtoons.com/en/x/test/list?title_no=42")
        self.assertEqual(manga["title"], "Test Toon")
        self.assertEqual(manga["author"], "Author")
        self.assertEqual([item["title"] for item in manga["chapters"]], ["Episode 1", "Episode 2"])
        self.assertEqual(engine.chapter(manga["chapters"][0]["link"]), [
            "https://img.test/1.jpg?type=q90", "https://img.test/2.jpg?type=q90",
        ])


if __name__ == "__main__":
    unittest.main()
