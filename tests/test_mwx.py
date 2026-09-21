import tempfile
import unittest
from pathlib import Path

import mwx


class ParserPrimitiveTests(unittest.TestCase):
    def test_get_string_and_replacements(self):
        spec = {"skip": "<h1", "token1": ">", "token2": "</h1>",
                "replace": [{"match": "<b>", "text": ""}, {"match": "</b>", "text": ""}]}
        self.assertEqual(mwx.get_string("x<h1><b>Name</b></h1>", spec), "Name")

    def test_before_uses_nearest_preceding_token(self):
        text = '<a href="/manifest.json">x</a><a href="/read/1">Читать</a>'
        spec = {"before": '">Читать</a>', "token1": 'href="', "token2": '"'}
        self.assertEqual(mwx.get_string(text, spec), "/read/1")

    def test_get_array(self):
        spec = {"start": "BEGIN", "end": "END", "token1": "[", "token2": "]"}
        self.assertEqual(mwx.get_array("BEGIN[a][b]END", spec), ["a", "b"])

    def test_split_array(self):
        spec = {"start": '"data":', "end": "],", "token1": '["',
                "token2": '"]', "split": '","'}
        self.assertEqual(mwx.get_array('x"data":["a","b"],tail', spec), ["a", "b"])

    def test_legacy_json_array_without_split(self):
        spec = {"start": "images:", "end": ",],", "token1": '["', "token2": '"'}
        self.assertEqual(mwx.get_array('images:["1.jpg","2.png","3.webp",],', spec),
                         ["1.jpg", "2.png", "3.webp"])

    def test_java_style_regex_groups(self):
        rule = {"match": r"(.*)-(.*)", "text": "$2/$1"}
        self.assertEqual(mwx.apply_replace("one-two", rule), "two/one")

    def test_markdown_url_is_normalized(self):
        value = "[https://example.test/a](https://example.test/a)"
        self.assertEqual(mwx.normalize_url(value), "https://example.test/a")

    def test_invalid_url_is_rejected(self):
        with self.assertRaises(mwx.ParserError):
            mwx.normalize_url("not a URL")

    def test_query_parameter_is_not_html_entity(self):
        value = "https://example.test/x?a=1&current_page=2&amp;ok=3"
        self.assertEqual(mwx.unescape_entities(value),
                         "https://example.test/x?a=1&current_page=2&ok=3")

    def test_decimal_chapter_name_keeps_decimal_in_cbz(self):
        chapter_dir = Path("Title") / "Chapter 112.3"
        archive = chapter_dir.parent / (chapter_dir.name + ".cbz")
        self.assertEqual(archive.name, "Chapter 112.3.cbz")

    def test_chapters_from_page_can_fall_back_to_title_page(self):
        source = mwx.Source(Path("fixture.json"), {
            "name": "fixture", "host": "https://example.test",
            "manga_complete": {
                "title": {"token1": "<h1>", "token2": "</h1>"},
                "chapters_from_page": {"token1": "hx-get=\"", "token2": "\""},
                "add_chapter": {"next": "CH", "link": {"token1": "href='", "token2": "'"},
                                "title": {"token1": ">", "token2": "<"}},
            }
        })
        engine = mwx.Engine(source)
        engine.fetch = lambda _url, _section=None: ("<h1>A</h1>CHhref='/c'>C<", "https://example.test/t")
        self.assertEqual(engine.manga("https://example.test/t")["chapters"][0]["link"],
                         "https://example.test/c")

    def test_parse_items_and_relative_urls(self):
        spec = {"next": "ITEM", "link": {"token1": "href='", "token2": "'"},
                "title": {"token1": ">", "token2": "<"}}
        got = mwx.parse_items("ITEMhref='/a'>A<ITEMhref='/b'>B<", spec, "https://example.test/list")
        self.assertEqual([x["link"] for x in got], ["https://example.test/a", "https://example.test/b"])

    def test_database_upsert(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.sqlite"
            db = mwx.init_db(path)
            try:
                self.assertEqual(mwx.save_titles(db, "x", [{"link": "u", "title": "A"}]), 1)
                self.assertEqual(mwx.save_titles(db, "x", [{"link": "u", "title": "B"}]), 1)
                self.assertEqual(db.execute("SELECT title FROM titles").fetchone()[0], "B")
            finally:
                db.close()


if __name__ == "__main__":
    unittest.main()
