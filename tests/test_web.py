import json
import tempfile
import threading
import time
import unittest
import urllib.request
from unittest import mock
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from mwx_web import App, WebServer


class ContentHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        routes = {
            "/catalog": b"ITEM<a href='/title'>Web title</a>",
            "/title": b"<h1>Web title</h1>CH<a href='/chapter'>Chapter one</a>",
            "/chapter": b'images:["/one.jpg","/two.png",],',
            "/one.jpg": b"one", "/two.png": b"two",
        }
        body = routes.get(self.path)
        if body is None:
            self.send_error(404); return
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *_args):
        pass


class WebIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.parsers = self.base / "parsers"
        self.parsers.mkdir()
        self.content = ThreadingHTTPServer(("127.0.0.1", 0), ContentHandler)
        threading.Thread(target=self.content.serve_forever, daemon=True).start()
        host = f"http://127.0.0.1:{self.content.server_port}"
        parser = {
            "name": "fixture", "title": "Fixture", "host": host,
            "manga_list_complete": {"iterator": host + "/catalog", "add_manga": {
                "next": "ITEM", "link": {"token1": "href='", "token2": "'"},
                "title": {"token1": ">", "token2": "</a>"}}},
            "manga_complete": {"title": {"token1": "<h1>", "token2": "</h1>"},
                "add_chapter": {"next": "CH", "link": {"token1": "href='", "token2": "'"},
                                "title": {"token1": ">", "token2": "</a>"}}},
            "chapter_complete": {"images": True, "add_pages": {
                "start": "images:", "end": ",],", "token1": '["', "token2": '"'}},
        }
        (self.parsers / "fixture.json").write_text(json.dumps(parser), encoding="utf-8")
        app = App(self.parsers, self.base / "downloads", 5, 2, True)
        self.web = WebServer(("127.0.0.1", 0), app)
        threading.Thread(target=self.web.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.web.server_port}"

    def tearDown(self):
        self.web.shutdown(); self.web.server_close()
        self.content.shutdown(); self.content.server_close()
        self.temp.cleanup()

    def request(self, path, payload=None):
        data = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(self.url + path, data=data,
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read())

    def test_catalog_title_and_download_job(self):
        source_ids = {item["id"] for item in self.request("/api/sources")}
        self.assertTrue({"fixture", "naver-webtoon", "naver-series"}.issubset(source_ids))
        catalog = self.request("/api/catalog?source=fixture&max_pages=1")
        self.assertEqual(catalog[0]["title"], "Web title")
        title = self.request("/api/title", {"source": "fixture", "url": catalog[0]["link"]})
        self.assertEqual(len(title["chapters"]), 1)
        job = self.request("/api/downloads", {"token": title["token"], "chapters": [0]})
        for _ in range(100):
            job = self.request("/api/jobs/" + job["id"])
            if job["state"] not in {"queued", "running"}:
                break
            time.sleep(0.03)
        self.assertEqual(job["state"], "done")
        self.assertTrue(Path(job["files"][0]).exists())
        with mock.patch("mwx_web.open_folder") as opener:
            response = self.request(f'/api/jobs/{job["id"]}/open', {})
        self.assertTrue(response["opened"])
        opener.assert_called_once_with(Path(job["files"][0]).resolve().parent)


if __name__ == "__main__":
    unittest.main()
