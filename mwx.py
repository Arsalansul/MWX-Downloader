#!/usr/bin/env python3
"""PC runner for Manga Watcher X JSON parsers.

The program intentionally uses only Python's standard library.  It interprets the
most common MWX parser primitives and stores fetched metadata in SQLite.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import html
import json
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable


ROOT = Path(__file__).resolve().parent
DEFAULT_PARSERS = ROOT / "parsers"
DEFAULT_DB = ROOT / "mwx.sqlite"
DEFAULT_DOWNLOADS = ROOT / "downloads"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36")


class ParserError(RuntimeError):
    pass


def unescape_entities(value: str) -> str:
    """Decode complete HTML entities without corrupting query parameters.

    html.unescape() deliberately accepts some names without a semicolon, so a
    URL parameter such as ``&current_page`` becomes ``¤t_page`` (``&curren``).
    MWX parser output contains URLs often enough that strict entities are safer.
    """
    return re.sub(r"&(?:#[0-9]+|#x[0-9a-fA-F]+|[A-Za-z][A-Za-z0-9]+);",
                  lambda match: html.unescape(match.group(0)), value)


def normalize_url(value: str) -> str:
    """Accept a plain URL and links accidentally copied as Markdown."""
    value = value.strip()
    match = re.fullmatch(r"\[[^]]*]\((https?://[^)]+)\)", value)
    if match:
        value = match.group(1)
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ParserError(f"Некорректный URL: {value!r}. Нужен адрес вида https://site/path")
    return value


def clean_name(value: str, fallback: str = "untitled") -> str:
    value = unescape_entities(re.sub(r"<[^>]*>", "", value or "")).strip()
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value)
    value = re.sub(r"\s+", " ", value).strip(" .")
    return (value or fallback)[:180]


def apply_replace(value: str, rules: Any, variables: dict[str, str] | None = None) -> str:
    if not rules:
        return value
    variables = variables or {}
    if isinstance(rules, list):
        for rule in rules:
            value = apply_replace(value, rule, variables)
        return value
    if isinstance(rules, str):
        return value
    if rules.get("clear_escape"):
        value = value.replace("\\/", "/").replace('\\"', '"').replace("\\\\", "\\")
    if "match" in rules:
        replacement = str(rules.get("text", ""))
        for key, item in variables.items():
            replacement = replacement.replace(f"%%{key}%%", item).replace(f"${key}$", item)
        # MWX rules use Java replacement syntax ($1), Python uses \g<1>.
        replacement = re.sub(r"\$(\d+)", r"\\g<\1>", replacement)
        try:
            value = re.sub(str(rules["match"]), replacement, value)
        except re.error:
            value = value.replace(str(rules["match"]), replacement)
    prefix = str(rules.get("prefix", ""))
    suffix = str(rules.get("sufix", rules.get("suffix", "")))
    for key, item in variables.items():
        prefix = prefix.replace(f"%%{key}%%", item).replace(f"${key}$", item)
        suffix = suffix.replace(f"%%{key}%%", item).replace(f"${key}$", item)
    return prefix + value + suffix


def window(text: str, spec: dict[str, Any], cursor: int = 0) -> tuple[str, int]:
    start = cursor
    if spec.get("after"):
        pos = text.find(str(spec["after"]), start)
        if pos < 0:
            return "", len(text)
        start = pos
    if spec.get("skip"):
        pos = text.find(str(spec["skip"]), start)
        if pos < 0:
            return "", len(text)
        start = pos + len(str(spec["skip"]))
    end = len(text)
    if spec.get("before"):
        pos = text.find(str(spec["before"]), start)
        if pos >= 0:
            end = pos
    return text[start:end], start


def get_string(text: str, spec: Any, variables: dict[str, str] | None = None) -> str:
    if spec is None:
        return ""
    if isinstance(spec, (str, int, float, bool)):
        return str(spec)
    token1, token2 = str(spec.get("token1", "")), str(spec.get("token2", ""))
    start = 0
    if spec.get("after"):
        pos = text.find(str(spec["after"]), start)
        if pos < 0:
            return ""
        start = pos
    if spec.get("skip"):
        pos = text.find(str(spec["skip"]), start)
        if pos < 0:
            return ""
        start = pos + len(str(spec["skip"]))
    end = len(text)
    if spec.get("before"):
        pos = text.find(str(spec["before"]), start)
        if pos < 0:
            return ""
        end = pos
        left = text.rfind(token1, start, end)
    else:
        left = text.find(token1, start, end)
    if left < 0:
        return ""
    left += len(token1)
    right = text.find(token2, left) if token2 else end
    if right < 0 or right > end:
        return ""
    return unescape_entities(apply_replace(text[left:right], spec.get("replace"), variables)).strip()


def get_array(text: str, spec: Any, variables: dict[str, str] | None = None) -> list[str]:
    if not isinstance(spec, dict):
        return []
    segment = text
    if spec.get("start"):
        pos = segment.find(str(spec["start"]))
        if pos < 0:
            return []
        segment = segment[pos + len(str(spec["start"])):]
    if spec.get("end"):
        pos = segment.find(str(spec["end"]))
        if pos >= 0:
            segment = segment[:pos + len(str(spec["end"]))]
    token1, token2 = str(spec.get("token1", "")), str(spec.get("token2", ""))
    if spec.get("split"):
        left = segment.find(token1)
        if left < 0:
            return []
        left += len(token1)
        right = segment.find(token2, left) if token2 else len(segment)
        if right < 0:
            return []
        raw = segment[left:right]
        values = raw.split(str(spec["split"])) if raw else []
    else:
        pattern = re.escape(token1) + "(.*?)" + re.escape(token2)
        values = re.findall(pattern, segment, flags=re.DOTALL) if token2 else []
        # A frequent legacy shorthand denotes a JSON-like string array with
        # token1='["' and token2='"'. Only the first element carries '[', so
        # normal repeated-token matching would otherwise return one page.
        if token1.endswith('"') and token1.startswith("[") and token2 == '"':
            left = segment.find(token1)
            if left >= 0:
                tail = segment[left + len(token1):]
                quoted = re.match(r'((?:\\.|[^"\\])*)"', tail)
                rest = re.findall(r',\s*"((?:\\.|[^"\\])*)"', tail)
                values = ([quoted.group(1)] if quoted else []) + rest
    return [unescape_entities(apply_replace(v, spec.get("replace"), variables)).strip() for v in values if v.strip()]


def join_url(base: str, value: str) -> str:
    return urllib.parse.urljoin(base, value)


def parse_items(text: str, spec: dict[str, Any], base_url: str,
                variables: dict[str, str] | None = None) -> list[dict[str, str]]:
    variables = dict(variables or {})
    segment = text
    if spec.get("start"):
        pos = segment.find(str(spec["start"]))
        if pos < 0:
            return []
        segment = segment[pos + len(str(spec["start"])):]
    if spec.get("end"):
        pos = segment.find(str(spec["end"]))
        if pos >= 0:
            segment = segment[:pos]
    marker = str(spec.get("next", ""))
    chunks = segment.split(marker)[1:] if marker else [segment]
    result: list[dict[str, str]] = []
    for chunk in chunks:
        item: dict[str, str] = {}
        item_vars = dict(variables)
        # Some rules reference auxiliary values regardless of their JSON order,
        # e.g. title uses %%chapter_lang%% while lang is declared afterwards.
        for field, field_spec in spec.items():
            if field not in {"start", "end", "next", "headers"} and isinstance(field_spec, dict):
                raw = get_string(chunk, field_spec, item_vars)
                if raw:
                    item_vars[field] = raw
                    item_vars[f"chapter_{field}"] = raw
        for field in ("link", "title", "uniq", "cover", "summary", "additional_title"):
            if field in spec:
                item[field] = get_string(chunk, spec[field], item_vars)
        if item.get("link"):
            item["link"] = join_url(base_url, item["link"])
            if not item.get("uniq"):
                item["uniq"] = urllib.parse.urlparse(item["link"]).path.strip("/").split("/")[-1]
            if item.get("cover"):
                item["cover"] = join_url(base_url, item["cover"])
            result.append(item)
    return result


def detect_enum(text: str, spec: Any) -> str:
    if not isinstance(spec, dict):
        return ""
    default = str(spec.get("default", ""))
    for name, rule in spec.items():
        if name == "default":
            continue
        if isinstance(rule, str) and rule in text:
            return name
        if isinstance(rule, dict):
            segment = text
            if rule.get("start"):
                pos = segment.find(str(rule["start"]))
                if pos < 0:
                    continue
                segment = segment[pos:]
            if rule.get("end"):
                pos = segment.find(str(rule["end"]))
                if pos >= 0:
                    segment = segment[:pos]
            needles = rule.get("values", [rule.get("value")])
            if any(str(needle) in segment for needle in needles if needle is not None):
                return name
    return default


@dataclass
class Source:
    path: Path
    data: dict[str, Any]

    @property
    def name(self) -> str:
        return str(self.data.get("name") or self.path.stem)

    @property
    def host(self) -> str:
        return str(self.data.get("host", ""))


class Engine:
    def __init__(self, source: Source, timeout: float = 30, delay: float = 0,
                 max_iterator_pages: int = 100, allow_sensitive_headers: bool = True):
        self.source, self.timeout, self.delay = source, timeout, delay
        self.max_iterator_pages = max_iterator_pages
        self.allow_sensitive_headers = allow_sensitive_headers
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor())

    def fetch(self, url: str, section: dict[str, Any] | None = None) -> tuple[str, str]:
        section = section or {}
        url = normalize_url(url)
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.7",
        }
        for key, value in section.get("headers", {}).items():
            if not self.allow_sensitive_headers and key.lower() in {
                "cookie", "authorization", "proxy-authorization", "x-api-key", "api-key"
            }:
                continue
            headers["Cookie" if key.lower() == "cookie" else key] = str(value)
        request = urllib.request.Request(url, headers=headers)
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                raw = response.read()
                final_url = response.geturl()
                charset = self.source.data.get("encoding") or response.headers.get_content_charset() or "utf-8"
                if self.delay:
                    time.sleep(self.delay)
                return raw.decode(str(charset), errors="replace"), final_url
        except urllib.error.HTTPError as exc:
            raise ParserError(f"HTTP {exc.code}: {url}") from exc
        except urllib.error.URLError as exc:
            raise ParserError(f"Ошибка сети для {url}: {exc.reason}") from exc

    def iterator(self, section: dict[str, Any]) -> list[str]:
        iterator = section.get("iterator")
        if isinstance(iterator, str):
            return [iterator]
        if isinstance(iterator, list):
            return [str(x) for x in iterator]
        if not isinstance(iterator, dict):
            return [self.source.data.get("public_link") or self.source.host]
        base = str(iterator.get("base_url", ""))
        if "append_array" in iterator:
            return [base + str(x) for x in iterator["append_array"]]
        nums = iterator.get("append_nums", {})
        start, stop, step = int(nums.get("from", 0)), int(nums.get("to", 0)), int(nums.get("step", 1))
        return [base + str(i) + str(nums.get("sufix", "")) for i in range(start, stop + (1 if step > 0 else -1), step)]

    def catalog(self, max_pages: int | None = None) -> list[dict[str, str]]:
        section = self.source.data.get("manga_list_complete")
        if not isinstance(section, dict) or "add_manga" not in section:
            raise ParserError(f"{self.source.name}: нет manga_list_complete/add_manga")
        urls = self.iterator(section)
        if max_pages is not None:
            urls = urls[:max_pages]
        items: list[dict[str, str]] = []
        for index, url in enumerate(urls, 1):
            print(f"[{self.source.name}] каталог {index}/{len(urls)}", file=sys.stderr)
            page, final_url = self.fetch(url, section)
            items.extend(parse_items(page, section["add_manga"], final_url))
        unique: dict[str, dict[str, str]] = {}
        for item in items:
            unique[item["link"]] = item
        result = list(unique.values())
        if not result:
            raise ParserError(f"{self.source.name}: каталог вернул 0 тайтлов; правило устарело или сайт заблокировал запрос")
        return result

    def manga(self, url: str) -> dict[str, Any]:
        section = self.source.data.get("manga_complete")
        if isinstance(section, list):
            section = section[0]
        if not isinstance(section, dict):
            raise ParserError(f"{self.source.name}: нет manga_complete")
        page, final_url = self.fetch(url, section)
        result: dict[str, Any] = {"source": self.source.name, "link": final_url}
        for field in ("title", "uniq", "author", "summary", "cover", "rating"):
            if field in section:
                result[field] = get_string(page, section[field])
        if result.get("cover"):
            result["cover"] = join_url(final_url, result["cover"])
        result["genres"] = get_array(page, section.get("add_genres"))
        if section.get("add_genre"):
            values = get_array(page, section["add_genre"])
            result["genres"].extend(values or ([str(section["add_genre"])] if isinstance(section["add_genre"], str) else []))
        result["tags"] = get_array(page, section.get("add_tags"))
        if section.get("add_tag"):
            result["tags"].extend(get_array(page, section["add_tag"]))
        result["status"] = detect_enum(page, section.get("status"))
        mature = section.get("is_mature")
        if isinstance(mature, dict):
            needles = mature.get("values", mature.get("patterns", []))
            result["is_mature"] = any(str(value) in page for value in needles)
        chapter_spec = section.get("add_chapter")
        if not isinstance(chapter_spec, dict):
            raise ParserError(f"{self.source.name}: нестандартный или отсутствующий add_chapter")
        variables = {
            "host": self.source.host.rstrip("/"),
            "path": urllib.parse.urlparse(final_url).path,
            "page": urllib.parse.urlparse(final_url).path.strip("/").split("/")[-1],
        }
        chapter_page = section.get("chapters_from_page")
        if isinstance(chapter_page, dict):
            iterator = chapter_page.get("iterator")
            chapter_pages: list[tuple[str, str]] = []
            if isinstance(iterator, dict) and isinstance(iterator.get("append_nums"), dict):
                nums = iterator["append_nums"]
                start, step = int(nums.get("from", 0)), int(nums.get("step", 1))
                explicit_stop = nums.get("to")
                values = (range(start, int(explicit_stop) + (1 if step > 0 else -1), step)
                          if explicit_stop is not None else
                          (start + step * i for i in range(self.max_iterator_pages)))
                for value in values:
                    page_vars = dict(variables, iterator_num=str(value))
                    chapter_url = get_string(page, chapter_page, page_vars)
                    if not chapter_url:
                        break
                    chapter_html, chapter_final = self.fetch(join_url(final_url, chapter_url), chapter_spec)
                    chapter_pages.append((chapter_html, chapter_final))
                    parsed = parse_items(chapter_html, chapter_spec, chapter_final, page_vars)
                    if explicit_stop is None and not parsed:
                        break
            else:
                chapter_url = get_string(page, chapter_page, variables)
                if not chapter_url:
                    # Sites sometimes move the chapter list back into the title
                    # page while an older parser still has chapters_from_page.
                    # Trying the already loaded page preserves compatibility.
                    chapter_pages.append((page, final_url))
                else:
                    chapter_pages.append(self.fetch(join_url(final_url, chapter_url), chapter_spec))
            chapters = []
            for chapter_html, chapter_final in chapter_pages:
                chapters.extend(parse_items(chapter_html, chapter_spec, chapter_final, variables))
        else:
            chapters = parse_items(page, chapter_spec, final_url, variables)
        if not chapters:
            raise ParserError(f"{self.source.name}: главы не найдены; возможно, источник требует адаптер")
        result["chapters"] = chapters
        return result

    def chapter(self, url: str) -> list[str]:
        section = self.source.data.get("chapter_complete")
        if isinstance(section, list):
            section = section[0]
        if not isinstance(section, dict):
            raise ParserError(f"{self.source.name}: нет chapter_complete")
        page, final_url = self.fetch(url, section)
        variables = {"page": urllib.parse.urlparse(final_url).path.strip("/").split("/")[-1]}
        images: list[str] = []
        for key in ("add_images", "add_pages"):
            if key in section:
                images.extend(get_array(page, section[key], variables))
        if "add_page" in section:
            images.extend(get_array(page, section["add_page"], variables))
        prefix = get_string(page, section.get("prefix"), variables) if section.get("prefix") else ""
        suffix = get_string(page, section.get("sufix"), variables) if section.get("sufix") else ""
        resolved = [join_url(final_url, prefix + image + suffix) for image in images]
        # In older parsers add_pages contains HTML page URLs; page_complete then
        # resolves each of those pages to the actual image.
        page_section = self.source.data.get("page_complete")
        if resolved and not section.get("images") and isinstance(page_section, dict):
            actual: list[str] = []
            for page_url in resolved:
                page_html, page_final_url = self.fetch(page_url, page_section)
                image = get_string(page_html, page_section.get("image"))
                if image:
                    actual.append(join_url(page_final_url, image))
            resolved = actual
        if not resolved:
            raise ParserError(f"{self.source.name}: изображения главы не найдены")
        return resolved


def load_source(name: str, parser_dir: Path) -> Source:
    path = Path(name)
    if not path.exists():
        path = parser_dir / (name if name.endswith(".json") else name + ".json")
    if not path.exists():
        raise ParserError(f"Парсер не найден: {name}")
    try:
        return Source(path.resolve(), json.loads(path.read_text(encoding="utf-8-sig")))
    except json.JSONDecodeError as exc:
        raise ParserError(f"Некорректный JSON {path}: {exc}") from exc


def init_db(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(path)
    db.executescript("""
    CREATE TABLE IF NOT EXISTS titles (
      source TEXT NOT NULL, url TEXT NOT NULL, uniq TEXT, title TEXT, cover TEXT,
      metadata_json TEXT NOT NULL, fetched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY(source, url)
    );
    """)
    return db


def save_titles(db: sqlite3.Connection, source: str, items: Iterable[dict[str, Any]]) -> int:
    count = 0
    for item in items:
        db.execute("""INSERT INTO titles(source,url,uniq,title,cover,metadata_json)
          VALUES(?,?,?,?,?,?) ON CONFLICT(source,url) DO UPDATE SET
          uniq=excluded.uniq,title=excluded.title,cover=excluded.cover,
          metadata_json=excluded.metadata_json,fetched_at=CURRENT_TIMESTAMP""",
          (source, item["link"], item.get("uniq"), item.get("title"), item.get("cover"), json.dumps(item, ensure_ascii=False)))
        count += 1
    db.commit()
    return count


def download_file(engine: Engine, url: str, target: Path, referer: str) -> None:
    if target.exists() and target.stat().st_size:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    url = normalize_url(url)
    headers = {"User-Agent": USER_AGENT, "Referer": normalize_url(referer), "Accept": "image/avif,image/webp,image/*,*/*;q=0.8"}
    request = urllib.request.Request(url, headers=headers)
    try:
        with engine.opener.open(request, timeout=engine.timeout) as response:
            target.write_bytes(response.read())
    except Exception:
        target.unlink(missing_ok=True)
        raise


def download_chapter(engine: Engine, chapter_url: str, output: Path, series: str,
                     chapter: str, workers: int, cbz: bool,
                     progress: Callable[[int, int], None] | None = None) -> Path:
    images = engine.chapter(chapter_url)
    chapter_dir = output / clean_name(engine.source.name) / clean_name(series) / clean_name(chapter)
    chapter_dir.mkdir(parents=True, exist_ok=True)
    targets: list[Path] = []
    for index, url in enumerate(images, 1):
        ext = Path(urllib.parse.urlparse(url).path).suffix.lower()
        if ext not in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"}:
            ext = ".jpg"
        targets.append(chapter_dir / f"{index:04d}{ext}")
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(download_file, engine, url, target, chapter_url) for url, target in zip(images, targets)]
        for index, future in enumerate(futures, 1):
            future.result()
            if progress:
                progress(index, len(futures))
            print(f"  страница {index}/{len(futures)}", file=sys.stderr)
    if cbz:
        archive = chapter_dir.parent / (chapter_dir.name + ".cbz")
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
            for target in targets:
                zf.write(target, target.name)
        return archive
    return chapter_dir


def command_meta(args: argparse.Namespace) -> None:
    sources = ([load_source(p.name, args.parsers) for p in sorted(args.parsers.glob("*.json"))]
               if args.all else [load_source(args.parser, args.parsers)])
    with init_db(args.db) as db:
        for source in sources:
            try:
                engine = Engine(source, args.timeout, args.delay)
                catalog = engine.catalog(args.max_pages)
                if args.max_titles is not None:
                    catalog = catalog[:args.max_titles]
                if args.full:
                    complete = []
                    for index, item in enumerate(catalog, 1):
                        print(f"[{source.name}] мета {index}/{len(catalog)}: {item.get('title', item['link'])}", file=sys.stderr)
                        try:
                            complete.append(engine.manga(item["link"]))
                        except ParserError as exc:
                            print(f"ПРОПУСК: {exc}", file=sys.stderr)
                    catalog = complete
                print(f"{source.name}: сохранено {save_titles(db, source.name, catalog)}")
            except ParserError as exc:
                if not args.all:
                    raise
                print(f"ОШИБКА {source.name}: {exc}", file=sys.stderr)


def command_chapter(args: argparse.Namespace) -> None:
    engine = Engine(load_source(args.parser, args.parsers), args.timeout, args.delay)
    result = download_chapter(engine, args.url, args.output, args.series or "single-chapter",
                              args.name or "chapter", args.workers, args.cbz)
    print(result)


def command_info(args: argparse.Namespace) -> None:
    engine = Engine(load_source(args.parser, args.parsers), args.timeout, args.delay)
    manga = engine.manga(args.url)
    payload = json.dumps(manga, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
        print(args.output)
    else:
        print(payload)


def command_title(args: argparse.Namespace) -> None:
    engine = Engine(load_source(args.parser, args.parsers), args.timeout, args.delay)
    manga = engine.manga(args.url)
    title = manga.get("title") or manga.get("uniq") or "title"
    chapters = manga["chapters"][:args.max_chapters] if args.max_chapters is not None else manga["chapters"]
    failures = 0
    for index, chapter in enumerate(chapters, 1):
        chapter_name = chapter.get("title") or f"chapter-{index:04d}"
        print(f"[{index}/{len(chapters)}] {chapter_name}", file=sys.stderr)
        try:
            download_chapter(engine, chapter["link"], args.output, title, chapter_name,
                             args.workers, args.cbz)
        except Exception as exc:
            failures += 1
            print(f"ОШИБКА: {exc}", file=sys.stderr)
            if not args.keep_going:
                raise
    print(f"Готово: {len(chapters) - failures}; ошибок: {failures}")


def command_all_titles(args: argparse.Namespace) -> None:
    source = load_source(args.parser, args.parsers)
    engine = Engine(source, args.timeout, args.delay)
    catalog = engine.catalog(args.max_pages)
    if args.max_titles is not None:
        catalog = catalog[:args.max_titles]
    failures = 0
    for index, item in enumerate(catalog, 1):
        print(f"Тайтл {index}/{len(catalog)}: {item.get('title', item['link'])}", file=sys.stderr)
        child = argparse.Namespace(**vars(args), url=item["link"])
        try:
            command_title(child)
        except Exception as exc:
            failures += 1
            print(f"ОШИБКА ТАЙТЛА: {exc}", file=sys.stderr)
            if not args.keep_going:
                raise
    print(f"Тайтлов обработано: {len(catalog) - failures}; ошибок: {failures}")


def error_kind(message: str) -> str:
    lowered = message.lower()
    if any(value in lowered for value in ("http 401", "http 403", "http 407", "http 429",
                                           "10054", "connection reset", "certificate", "ssl")):
        return "blocked"
    if any(value in lowered for value in ("getaddrinfo", "name or service", "timed out", "timeout",
                                           "10060", "11001", "11002", "network is unreachable")):
        return "unavailable"
    if any(value in lowered for value in ("не найден", "0 тайтлов", "не вернул url", "нет manga",
                                           "нет chapter", "нестандартный")):
        return "parser"
    return "error"


def test_link(source: Source, procedure: str) -> str | None:
    tests = source.data.get("test", [])
    if isinstance(tests, dict):
        tests = [tests]
    for item in tests if isinstance(tests, list) else []:
        if isinstance(item, dict) and item.get("proc") == procedure and item.get("link"):
            return str(item["link"])
    return None


def check_source(source: Source, timeout: float) -> dict[str, Any]:
    result: dict[str, Any] = {
        "parser": source.path.stem,
        "source": source.name,
        "title": source.data.get("title", source.name),
        "host": source.host,
        "stages": {},
    }
    engine = Engine(source, timeout=timeout, delay=0, max_iterator_pages=1,
                    allow_sensitive_headers=False)

    def run(stage: str, callback: Any) -> None:
        started = time.monotonic()
        try:
            value = callback()
            count = len(value) if isinstance(value, list) else None
            result["stages"][stage] = {
                "status": "ok", "count": count,
                "seconds": round(time.monotonic() - started, 2),
            }
        except Exception as exc:
            message = str(exc)
            result["stages"][stage] = {
                "status": error_kind(message), "error": message,
                "seconds": round(time.monotonic() - started, 2),
            }

    if isinstance(source.data.get("manga_list_complete"), dict):
        run("catalog", lambda: engine.catalog(1))
    else:
        result["stages"]["catalog"] = {"status": "missing"}

    manga_url = test_link(source, "manga_complete")
    if manga_url:
        run("title", lambda: engine.manga(manga_url))
    else:
        result["stages"]["title"] = {"status": "untested", "error": "нет тестового URL"}

    chapter_url = test_link(source, "chapter_complete")
    if chapter_url:
        run("chapter", lambda: engine.chapter(chapter_url))
    else:
        result["stages"]["chapter"] = {"status": "untested", "error": "нет тестового URL"}

    statuses = [item["status"] for item in result["stages"].values()]
    if statuses and all(status == "ok" for status in statuses):
        result["overall"] = "working"
    elif "blocked" in statuses or "unavailable" in statuses:
        result["overall"] = "blocked_or_unavailable"
    elif "ok" in statuses:
        result["overall"] = "partial"
    elif all(status in {"missing", "untested"} for status in statuses):
        result["overall"] = "untested"
    else:
        result["overall"] = "broken_parser"
    return result


def command_check(args: argparse.Namespace) -> None:
    paths = ([args.parsers / (args.parser if args.parser.endswith(".json") else args.parser + ".json")]
             if args.parser else sorted(args.parsers.glob("*.json")))
    sources: list[Source] = []
    results: list[dict[str, Any]] = []
    for path in paths:
        try:
            sources.append(load_source(str(path), args.parsers))
        except ParserError as exc:
            results.append({
                "parser": path.stem, "source": path.stem, "title": path.stem, "host": "",
                "overall": "broken_parser",
                "stages": {"config": {"status": "parser", "error": str(exc)}},
            })
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(check_source, source, args.check_timeout): source for source in sources}
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            results.append(result)
            stages = ", ".join(f"{key}={value['status']}" for key, value in result["stages"].items())
            print(f"[{len(results)}/{len(paths)}] {result['parser']}: {result['overall']} ({stages})",
                  file=sys.stderr)
    results.sort(key=lambda item: item["parser"])
    report = {
        "checked_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "count": len(results),
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown = args.output.with_suffix(".md")
    labels = {
        "working": "Рабочие",
        "partial": "Частично рабочие",
        "blocked_or_unavailable": "Сайт блокирует или недоступен",
        "broken_parser": "Парсер не соответствует сайту",
        "untested": "Недостаточно тестовых данных",
    }
    lines = ["# Проверка парсеров", "", f"Проверено: {len(results)}", ""]
    for status, label in labels.items():
        group = [item for item in results if item["overall"] == status]
        lines.extend([f"## {label} ({len(group)})", ""])
        for item in group:
            stages = ", ".join(f"{name}: {data['status']}" for name, data in item["stages"].items())
            lines.append(f"- `{item['parser']}` — {stages}")
        lines.append("")
    markdown.write_text("\n".join(lines), encoding="utf-8")
    print(args.output)
    print(markdown)


def build_cli() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Запуск JSON-парсеров Manga Watcher X на ПК")
    parser.add_argument("--parsers", type=Path, default=DEFAULT_PARSERS, help="папка parsers")
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--delay", type=float, default=0.25, help="пауза между HTML-запросами")
    sub = parser.add_subparsers(dest="command", required=True)

    meta = sub.add_parser("meta", help="получить каталог/метаданные")
    group = meta.add_mutually_exclusive_group(required=True)
    group.add_argument("--parser")
    group.add_argument("--all", action="store_true")
    meta.add_argument("--db", type=Path, default=DEFAULT_DB)
    meta.add_argument("--full", action=argparse.BooleanOptionalAction, default=True,
                      help="открыть страницу каждого тайтла (по умолчанию: да)")
    meta.add_argument("--max-pages", type=int, help="ограничить страницы каталога")
    meta.add_argument("--max-titles", type=int, help="ограничить число тайтлов")
    meta.set_defaults(func=command_meta)

    info = sub.add_parser("info", help="получить метаданные одного тайтла")
    info.add_argument("--parser", required=True)
    info.add_argument("--url", required=True)
    info.add_argument("--output", type=Path, help="сохранить JSON в файл")
    info.set_defaults(func=command_info)

    def downloads(cmd: argparse.ArgumentParser) -> None:
        cmd.add_argument("--parser", required=True)
        cmd.add_argument("--output", type=Path, default=DEFAULT_DOWNLOADS)
        cmd.add_argument("--workers", type=int, default=4)
        cmd.add_argument("--cbz", action=argparse.BooleanOptionalAction, default=True)

    chapter = sub.add_parser("chapter", help="скачать конкретную главу")
    downloads(chapter)
    chapter.add_argument("--url", required=True)
    chapter.add_argument("--name")
    chapter.add_argument("--series", help="имя тайтла для выходной папки")
    chapter.set_defaults(func=command_chapter)

    title = sub.add_parser("title", help="скачать все главы тайтла")
    downloads(title)
    title.add_argument("--url", required=True)
    title.add_argument("--max-chapters", type=int, help="скачать только первые N найденных глав")
    title.add_argument("--keep-going", action=argparse.BooleanOptionalAction, default=True)
    title.set_defaults(func=command_title)

    all_titles = sub.add_parser("all-titles", help="скачать все главы всех тайтлов источника")
    downloads(all_titles)
    all_titles.add_argument("--max-pages", type=int)
    all_titles.add_argument("--max-titles", type=int, help="обработать только первые N тайтлов")
    all_titles.add_argument("--max-chapters", type=int, help="скачать только первые N глав каждого тайтла")
    all_titles.add_argument("--keep-going", action=argparse.BooleanOptionalAction, default=True)
    all_titles.set_defaults(func=command_all_titles)

    check = sub.add_parser("check", help="проверить доступность и основные процедуры парсеров")
    check.add_argument("--parser", help="проверить только один источник")
    check.add_argument("--workers", type=int, default=8)
    check.add_argument("--check-timeout", type=float, default=15)
    check.add_argument("--output", type=Path, default=ROOT / "parser-check.json")
    check.set_defaults(func=command_check)

    sources = sub.add_parser("sources", help="показать доступные источники")
    sources.set_defaults(func=lambda a: print("\n".join(p.stem for p in sorted(a.parsers.glob("*.json")))))
    return parser


def main() -> int:
    parser = build_cli()
    args = parser.parse_args()
    try:
        args.func(args)
        return 0
    except (ParserError, OSError, sqlite3.Error) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
