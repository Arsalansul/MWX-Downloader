#!/usr/bin/env python3
"""Local browser interface for the MWX parser runner."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from mwx import DEFAULT_DOWNLOADS, DEFAULT_PARSERS, ParserError, download_chapter, load_source
from mwx_adapters import builtin_source_rows, create_engine


ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "web"
CHECK_REPORT = ROOT / "parser-check.json"


def open_folder(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def source_rows(parser_dir: Path) -> list[dict[str, Any]]:
    audit: dict[str, dict[str, Any]] = {}
    try:
        report = json.loads(CHECK_REPORT.read_text(encoding="utf-8"))
        audit = {row["parser"]: row for row in report.get("results", [])}
    except (OSError, ValueError, KeyError):
        pass
    rows = builtin_source_rows()
    for path in sorted(parser_dir.glob("*.json")):
        try:
            source = load_source(path.name, parser_dir)
        except ParserError:
            continue
        checked = audit.get(path.stem, {})
        stages = checked.get("stages", {})
        rows.append({
            "id": path.stem,
            "title": source.data.get("title") or source.name,
            "host": source.host,
            "status": checked.get("overall", "unknown"),
            "catalog": stages.get("catalog", {}).get("status") == "ok",
            "direct": stages.get("title", {}).get("status") == "ok",
            "download": stages.get("chapter", {}).get("status") == "ok",
        })
    order = {"working": 0, "partial": 1, "unknown": 2, "blocked_or_unavailable": 3, "broken_parser": 4}
    return sorted(rows, key=lambda row: (order.get(row["status"], 3), str(row["title"]).lower()))


@dataclass
class Job:
    id: str
    source: str
    title: str
    total: int
    state: str = "queued"
    completed: int = 0
    failed: int = 0
    current: str = ""
    page: int = 0
    pages: int = 0
    error: str = ""
    files: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)

    def public(self) -> dict[str, Any]:
        return dict(vars(self))


class App:
    def __init__(self, parser_dir: Path, output: Path, timeout: float, workers: int, cbz: bool):
        self.parser_dir = parser_dir
        self.output = output
        self.timeout = timeout
        self.workers = workers
        self.cbz = cbz
        self.titles: dict[str, dict[str, Any]] = {}
        self.jobs: dict[str, Job] = {}
        self.lock = threading.RLock()

    def inspect_title(self, source_id: str, url: str) -> dict[str, Any]:
        engine = create_engine(source_id, self.parser_dir, self.timeout)
        manga = engine.manga(url)
        token = uuid.uuid4().hex
        with self.lock:
            self.titles[token] = {"source": source_id, "manga": manga, "created_at": time.time()}
        result = dict(manga)
        result["token"] = token
        return result

    def create_job(self, token: str, selected: list[int]) -> Job:
        with self.lock:
            record = self.titles.get(token)
            if not record:
                raise ParserError("Карточка тайтла устарела. Загрузите её заново.")
            manga = record["manga"]
            chapters = manga["chapters"]
            if not isinstance(selected, list):
                raise ParserError("Список глав имеет неверный формат")
            indexes = list(dict.fromkeys(int(value) for value in selected))
            if not indexes or any(value < 0 or value >= len(chapters) for value in indexes):
                raise ParserError("Выберите хотя бы одну существующую главу")
            blocked = [chapters[value] for value in indexes if chapters[value].get("downloadable") is False]
            if blocked:
                reason = blocked[0].get("availability") or "Глава недоступна для скачивания"
                raise ParserError(str(reason))
            job = Job(uuid.uuid4().hex, record["source"], manga.get("title") or manga.get("uniq") or "title", len(indexes))
            self.jobs[job.id] = job
        thread = threading.Thread(target=self._run_job, args=(job, manga, indexes), daemon=True)
        thread.start()
        return job

    def open_job_folder(self, job_id: str) -> None:
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                raise ParserError("Загрузка не найдена")
            if not job.files:
                raise ParserError("Ни одна глава ещё не загружена")
            folder = Path(job.files[0]).resolve().parent
        try:
            folder.relative_to(self.output.resolve())
        except ValueError as exc:
            raise ParserError("Папка загрузки находится вне каталога программы") from exc
        if not folder.is_dir():
            raise ParserError("Папка загрузки не найдена")
        open_folder(folder)

    def _run_job(self, job: Job, manga: dict[str, Any], indexes: list[int]) -> None:
        with self.lock:
            job.state = "running"
        engine = create_engine(job.source, self.parser_dir, self.timeout)
        for ordinal, index in enumerate(indexes, 1):
            chapter = manga["chapters"][index]
            name = chapter.get("title") or f"chapter-{index + 1:04d}"
            with self.lock:
                job.current, job.page, job.pages = name, 0, 0
            try:
                def page_progress(done: int, total: int) -> None:
                    with self.lock:
                        job.page, job.pages = done, total
                result = download_chapter(engine, chapter["link"], self.output, job.title, name,
                                          self.workers, self.cbz, page_progress)
                with self.lock:
                    job.files.append(str(result.resolve()))
                    job.completed += 1
            except Exception as exc:
                with self.lock:
                    job.failed += 1
                    job.errors.append(f"{name}: {exc}")
            with self.lock:
                job.error = "" if not job.errors else job.errors[-1]
        with self.lock:
            job.current = ""
            job.state = "done" if not job.failed else ("failed" if not job.completed else "done_with_errors")


class Handler(BaseHTTPRequestHandler):
    server: "WebServer"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[web] {self.address_string()} {fmt % args}")

    def json_response(self, payload: Any, status: int = 200) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(raw)

    def error_response(self, exc: Exception, status: int = 400) -> None:
        self.json_response({"error": str(exc)}, status)

    def body(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            return json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError) as exc:
            raise ParserError("Некорректный JSON запроса") from exc

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/sources":
                return self.json_response(source_rows(self.server.app.parser_dir))
            if parsed.path == "/api/catalog":
                query = parse_qs(parsed.query)
                source_id = query.get("source", [""])[0]
                max_pages_raw = query.get("max_pages", ["3"])[0]
                max_pages = max(1, min(20, int(max_pages_raw)))
                engine = create_engine(source_id, self.server.app.parser_dir, self.server.app.timeout)
                return self.json_response(engine.catalog(max_pages))
            if parsed.path == "/api/jobs":
                with self.server.app.lock:
                    jobs = sorted(self.server.app.jobs.values(), key=lambda job: job.created_at, reverse=True)
                    return self.json_response([job.public() for job in jobs])
            if parsed.path.startswith("/api/jobs/"):
                job_id = parsed.path.rsplit("/", 1)[-1]
                with self.server.app.lock:
                    job = self.server.app.jobs.get(job_id)
                    if not job:
                        return self.error_response(ParserError("Загрузка не найдена"), 404)
                    return self.json_response(job.public())
            return self.static_file(parsed.path)
        except Exception as exc:
            return self.error_response(exc, 500 if not isinstance(exc, (ParserError, ValueError)) else 400)

    def do_POST(self) -> None:
        try:
            if self.path == "/api/title":
                data = self.body()
                return self.json_response(self.server.app.inspect_title(str(data.get("source", "")), str(data.get("url", ""))))
            if self.path == "/api/downloads":
                data = self.body()
                job = self.server.app.create_job(str(data.get("token", "")), data.get("chapters", []))
                return self.json_response(job.public(), HTTPStatus.ACCEPTED)
            if self.path.startswith("/api/jobs/") and self.path.endswith("/open"):
                job_id = self.path.removeprefix("/api/jobs/").removesuffix("/open")
                self.server.app.open_job_folder(job_id)
                return self.json_response({"opened": True})
            return self.error_response(ParserError("Маршрут не найден"), 404)
        except Exception as exc:
            return self.error_response(exc, 500 if not isinstance(exc, (ParserError, ValueError, TypeError)) else 400)

    def static_file(self, path: str) -> None:
        names = {"/": "index.html", "/app.js": "app.js", "/style.css": "style.css"}
        name = names.get(path)
        if not name:
            self.send_error(404)
            return
        target = WEB_ROOT / name
        if not target.exists():
            self.send_error(404)
            return
        content_types = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8"}
        raw = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_types[target.suffix])
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' http: https: data:; style-src 'self'; script-src 'self'; connect-src 'self'")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(raw)


class WebServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], app: App):
        self.app = app
        super().__init__(address, Handler)


def build_cli() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Локальный веб-интерфейс MWX")
    parser.add_argument("--host", default="127.0.0.1", choices=["127.0.0.1", "localhost"])
    parser.add_argument("--port", type=int, default=0, help="порт; 0 — выбрать свободный")
    parser.add_argument("--parsers", type=Path, default=DEFAULT_PARSERS)
    parser.add_argument("--output", type=Path, default=DEFAULT_DOWNLOADS)
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--cbz", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--no-browser", action="store_true")
    return parser


def main() -> int:
    args = build_cli().parse_args()
    app = App(args.parsers.resolve(), args.output.resolve(), args.timeout, max(1, args.workers), args.cbz)
    server = WebServer((args.host, args.port), app)
    url = f"http://127.0.0.1:{server.server_port}/"
    print(f"MWX запущен: {url}")
    print("Для остановки нажмите Ctrl+C")
    if not args.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nMWX остановлен")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
