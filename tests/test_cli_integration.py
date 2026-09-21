import json
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        routes = {
            "/catalog": b"ITEM<a href='/title/a'>Title A</a>ITEM<a href='/title/b'>Title B</a>",
            "/title/a": b"<h1>Title A</h1>CH<a href='/chapter/a1'>Chapter A1</a>",
            "/title/b": b"<h1>Title B</h1>CH<a href='/chapter/b1'>Chapter B1</a>",
            "/chapter/a1": b'images:["/img/1.jpg","/img/2.png",],',
            "/chapter/b1": b'images:["/img/1.jpg",],',
            "/img/1.jpg": b"fake-jpeg-one",
            "/img/2.png": b"fake-png-two",
        }
        body = routes.get(self.path)
        if body is None:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg" if self.path.startswith("/img/") else "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *_args):
        pass


class CliIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.parsers = self.base / "parsers"
        self.parsers.mkdir()
        host = f"http://127.0.0.1:{self.server.server_port}"
        parser = {
            "name": "fixture", "host": host, "public_link": host + "/catalog",
            "manga_list_complete": {
                "iterator": host + "/catalog",
                "add_manga": {"next": "ITEM", "link": {"token1": "href='", "token2": "'"},
                              "title": {"token1": ">", "token2": "</a>"}}
            },
            "manga_complete": {
                "title": {"token1": "<h1>", "token2": "</h1>"},
                "add_chapter": {"next": "CH", "link": {"token1": "href='", "token2": "'"},
                                "title": {"token1": ">", "token2": "</a>"}}
            },
            "chapter_complete": {
                "images": True,
                "add_pages": {"start": "images:", "end": ",],", "token1": '["', "token2": '"'}
            },
            "test": [
                {"proc": "manga_complete", "link": host + "/title/a"},
                {"proc": "chapter_complete", "link": host + "/chapter/a1"},
            ],
        }
        (self.parsers / "fixture.json").write_text(json.dumps(parser), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, str(ROOT / "mwx.py"), "--parsers", str(self.parsers), "--delay", "0", *args],
            cwd=ROOT, text=True, capture_output=True, timeout=20, check=True,
        )

    def test_every_cli_command(self):
        self.assertIn("fixture", self.run_cli("sources").stdout)

        info_file = self.base / "title.json"
        self.run_cli("info", "--parser", "fixture", "--url",
                     f"http://127.0.0.1:{self.server.server_port}/title/a",
                     "--output", str(info_file))
        self.assertEqual(json.loads(info_file.read_text(encoding="utf-8"))["title"], "Title A")

        db = self.base / "meta.sqlite"
        self.run_cli("meta", "--parser", "fixture", "--db", str(db))
        connection = sqlite3.connect(db)
        try:
            self.assertEqual(connection.execute("SELECT count(*) FROM titles").fetchone()[0], 2)
        finally:
            connection.close()

        second = json.loads((self.parsers / "fixture.json").read_text(encoding="utf-8"))
        second["name"] = "fixture2"
        (self.parsers / "fixture2.json").write_text(json.dumps(second), encoding="utf-8")
        all_db = self.base / "all-meta.sqlite"
        self.run_cli("meta", "--all", "--no-full", "--db", str(all_db))
        connection = sqlite3.connect(all_db)
        try:
            self.assertEqual(connection.execute("SELECT count(*) FROM titles").fetchone()[0], 4)
        finally:
            connection.close()

        check_file = self.base / "check.json"
        self.run_cli("check", "--workers", "2", "--check-timeout", "2",
                     "--output", str(check_file))
        checked = json.loads(check_file.read_text(encoding="utf-8"))
        self.assertEqual(checked["count"], 2)
        self.assertTrue(all(item["overall"] == "working" for item in checked["results"]))

        chapter_out = self.base / "chapter-output"
        self.run_cli("chapter", "--parser", "fixture", "--url",
                     f"[http://127.0.0.1:{self.server.server_port}/chapter/a1]"
                     f"(http://127.0.0.1:{self.server.server_port}/chapter/a1)",
                     "--series", "Series", "--name", "Chapter", "--output", str(chapter_out))
        self.assertTrue((chapter_out / "fixture" / "Series" / "Chapter.cbz").exists())

        title_out = self.base / "title-output"
        self.run_cli("title", "--parser", "fixture", "--url",
                     f"http://127.0.0.1:{self.server.server_port}/title/a", "--output", str(title_out))
        self.assertTrue((title_out / "fixture" / "Title A" / "Chapter A1.cbz").exists())

        all_out = self.base / "all-output"
        self.run_cli("all-titles", "--parser", "fixture", "--output", str(all_out))
        self.assertTrue((all_out / "fixture" / "Title B" / "Chapter B1.cbz").exists())


if __name__ == "__main__":
    unittest.main()
