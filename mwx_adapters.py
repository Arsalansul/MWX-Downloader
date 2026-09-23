"""Built-in adapters for sources that cannot be expressed as MWX JSON rules."""

from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

from mwx import Engine, ParserError, Source, normalize_url


BUILTIN_SOURCES: list[dict[str, Any]] = [
    {
        "id": "webtoon-global",
        "title": "WEBTOON (official)",
        "host": "https://www.webtoons.com",
        "status": "working",
        "catalog": False,
        "direct": True,
        "download": True,
    },
    {
        "id": "comizy",
        "title": "Comizy / MangaBuddy",
        "host": "https://mangabuddy.com",
        "status": "working",
        "catalog": False,
        "direct": True,
        "download": True,
    },
    {
        "id": "naver-webtoon",
        "title": "Naver Webtoon",
        "host": "https://comic.naver.com",
        "status": "working",
        "catalog": False,
        "direct": True,
        "download": True,
    },
    {
        "id": "naver-series",
        "title": "Naver Series",
        "host": "https://series.naver.com",
        "status": "partial",
        "catalog": False,
        "direct": True,
        "download": False,
    },
    {
        "id": "kakao-page",
        "title": "Kakao Page",
        "host": "https://page.kakao.com",
        "status": "blocked",
        "catalog": False,
        "direct": True,
        "download": False,
    },
    {
        "id": "ridibooks",
        "title": "Ridi",
        "host": "https://ridibooks.com",
        "status": "partial",
        "catalog": False,
        "direct": True,
        "download": False,
    },
]


def builtin_source_rows() -> list[dict[str, Any]]:
    return [dict(item) for item in BUILTIN_SOURCES]


def create_engine(source_id: str, parser_dir: Path, timeout: float) -> Engine:
    if source_id == "webtoon-global":
        return WebtoonGlobalEngine(timeout=timeout)
    if source_id == "comizy":
        return ComizyEngine(timeout=timeout)
    if source_id == "naver-webtoon":
        return NaverWebtoonEngine(timeout=timeout)
    if source_id == "naver-series":
        return NaverSeriesEngine(timeout=timeout)
    if source_id == "kakao-page":
        return KakaoPageEngine(timeout=timeout)
    if source_id == "ridibooks":
        return RidiEngine(timeout=timeout)
    from mwx import load_source
    return Engine(load_source(source_id, parser_dir), timeout=timeout)


def _query_number(url: str, key: str) -> int:
    values = parse_qs(urlparse(normalize_url(url)).query).get(key, [])
    if not values or not str(values[0]).isdigit():
        raise ParserError(f"В ссылке отсутствует числовой параметр {key}")
    return int(values[0])


def _meta(page: str, property_name: str) -> str:
    patterns = [
        rf'<meta[^>]+property=["\']{re.escape(property_name)}["\'][^>]+content=["\']([^"\']*)',
        rf'<meta[^>]+content=["\']([^"\']*)["\'][^>]+property=["\']{re.escape(property_name)}["\']',
    ]
    for pattern in patterns:
        match = re.search(pattern, page, re.I)
        if match:
            return html.unescape(match.group(1)).strip()
    return ""


def _path_number(url: str, marker: str) -> int:
    parts = [part for part in urlparse(normalize_url(url)).path.split("/") if part]
    try:
        value = parts[parts.index(marker) + 1]
    except (ValueError, IndexError) as exc:
        raise ParserError(f"В ссылке отсутствует числовой идентификатор после /{marker}/") from exc
    if not value.isdigit():
        raise ParserError(f"Идентификатор после /{marker}/ должен быть числом")
    return int(value)


def _assigned_json(page: str, variable: str) -> Any:
    match = re.search(rf"\bvar\s+{re.escape(variable)}\s*=\s*", page)
    if not match:
        raise ParserError(f"Страница не содержит {variable}")
    try:
        value, _ = json.JSONDecoder().raw_decode(page[match.end():])
    except json.JSONDecodeError as exc:
        raise ParserError(f"Страница содержит некорректный {variable}") from exc
    return value


def _plain_text(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", value))).strip()


class WebtoonGlobalEngine(Engine):
    host = "https://www.webtoons.com"

    def __init__(self, timeout: float = 30):
        super().__init__(Source(Path("webtoon-global"), {
            "name": "webtoon-global", "title": "WEBTOON (official)", "host": self.host,
        }), timeout=timeout)

    @staticmethod
    def _episode_rows(page: str, base_url: str) -> list[dict[str, Any]]:
        rows = []
        for match in re.finditer(r'<li\b[^>]*class=["\'][^"\']*_episodeItem[^"\']*["\'][^>]*>(.*?)</li>', page, re.I | re.S):
            block = match.group(1)
            link_match = re.search(r'<a\b[^>]*href=["\']([^"\']+/viewer\?[^"\']+)["\']', block, re.I)
            if not link_match:
                continue
            link = urljoin(base_url, html.unescape(link_match.group(1)))
            query = parse_qs(urlparse(link).query)
            episode_no = str((query.get("episode_no") or [""])[0])
            title_match = re.search(r'<span\b[^>]*class=["\']subj["\'][^>]*>(.*?)</span>', block, re.I | re.S)
            date_match = re.search(r'<span\b[^>]*class=["\']date["\'][^>]*>(.*?)</span>', block, re.I | re.S)
            rows.append({
                "title": _plain_text(title_match.group(1)) if title_match else f"Episode {episode_no}",
                "uniq": episode_no or link,
                "link": link,
                "downloadable": True,
                "availability": "Публичный эпизод WEBTOON",
                "date": _plain_text(date_match.group(1)) if date_match else "",
            })
        return rows

    def manga(self, url: str) -> dict[str, Any]:
        parsed = urlparse(normalize_url(url))
        if parsed.hostname not in {"webtoons.com", "www.webtoons.com"}:
            raise ParserError("Для WEBTOON нужна ссылка www.webtoons.com/.../list?title_no=ID")
        title_no = _query_number(url, "title_no")
        page, final_url = self.fetch(url, {"headers": {"Referer": self.host + "/en/"}})
        chapters: dict[str, dict[str, Any]] = {}
        page_number = 1
        current_page = page
        while page_number <= 200:
            batch = self._episode_rows(current_page, final_url)
            before = len(chapters)
            for row in batch:
                chapters[row["link"]] = row
            if not batch or len(chapters) == before:
                break
            page_number += 1
            separator = "&" if "?" in final_url else "?"
            current_page, _ = self.fetch(
                final_url + separator + urlencode({"page": page_number}),
                {"headers": {"Referer": final_url}},
            )
        if not chapters:
            raise ParserError("WEBTOON не вернул публичные эпизоды")
        ordered = sorted(chapters.values(), key=lambda item: int(item["uniq"]) if str(item["uniq"]).isdigit() else 0)
        title_match = re.search(r'<h1\b[^>]*class=["\']subj["\'][^>]*>(.*?)</h1>', page, re.I | re.S)
        author_match = re.search(r'<div\b[^>]*class=["\']author_area["\'][^>]*>(.*?)</div>', page, re.I | re.S)
        cover_match = re.search(r'<div\b[^>]*class=["\']detail_header[^"\']*["\'][^>]*>.*?<img\b[^>]*src=["\']([^"\']+)', page, re.I | re.S)
        return {
            "source": "webtoon-global", "link": final_url, "uniq": str(title_no),
            "title": _plain_text(title_match.group(1)) if title_match else (_meta(page, "og:title") or str(title_no)),
            "author": _plain_text(author_match.group(1)) if author_match else "",
            "summary": _meta(page, "og:description"),
            "cover": html.unescape(cover_match.group(1)) if cover_match else _meta(page, "og:image"),
            "chapters": ordered,
        }

    def chapter(self, url: str) -> list[str]:
        parsed = urlparse(normalize_url(url))
        if parsed.hostname not in {"webtoons.com", "www.webtoons.com"}:
            raise ParserError("Некорректная ссылка эпизода WEBTOON")
        page, _ = self.fetch(url, {"headers": {"Referer": url}})
        images = []
        for tag in re.findall(r'<img\b[^>]*>', page, re.I | re.S):
            if not re.search(r'class=["\'][^"\']*\b_images\b', tag, re.I):
                continue
            source = re.search(r'data-url=["\']([^"\']+)', tag, re.I)
            if source:
                images.append(html.unescape(source.group(1)))
        images = list(dict.fromkeys(images))
        if not images:
            raise ParserError("WEBTOON не вернул изображения: эпизод может быть закрыт или доступен только в приложении")
        return images


class ComizyEngine(Engine):
    """Public chapter adapter for MangaBuddy links currently served by Comizy."""

    host = "https://mangabuddy.com"
    accepted_hosts = {
        "mangabuddy.com", "www.mangabuddy.com", "comizy.com", "www.comizy.com",
        "comizy.io", "www.comizy.io",
    }

    def __init__(self, timeout: float = 30):
        super().__init__(Source(Path("comizy"), {
            "name": "comizy", "title": "Comizy / MangaBuddy", "host": self.host,
        }), timeout=timeout)

    def manga(self, url: str) -> dict[str, Any]:
        parsed = urlparse(normalize_url(url))
        if parsed.hostname not in self.accepted_hosts:
            raise ParserError("Для Comizy нужна ссылка mangabuddy.com или comizy.com")
        page, final_url = self.fetch(url, {"headers": {"Referer": self.host + "/"}})
        chapters: dict[str, dict[str, Any]] = {}
        manga_data: dict[str, Any] = {}
        site_config: dict[str, Any] = {}
        next_data = re.search(
            r'<script\b[^>]*id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', page, re.I | re.S
        )
        if next_data:
            try:
                payload = json.loads(html.unescape(next_data.group(1)))
                page_props = payload.get("props", {}).get("pageProps", {})
                manga_data = page_props.get("initialManga", {})
                site_config = page_props.get("siteConfig", {})
            except (json.JSONDecodeError, AttributeError):
                manga_data = {}
        chapter_data = manga_data.get("chapters") or []
        if manga_data.get("id") and site_config.get("apiUrl"):
            api_url = str(site_config["apiUrl"]).rstrip("/") + "/titles/" + str(manga_data["id"]) + "/chapters"
            if manga_data.get("cv"):
                api_url += "?" + urlencode({"cv": manga_data["cv"]})
            try:
                raw, _ = self.fetch(api_url, {"headers": {"Origin": urlparse(final_url).scheme + "://" + str(urlparse(final_url).hostname), "Referer": final_url}})
                api_payload = json.loads(raw)
                api_data = api_payload.get("data") or api_payload
                if isinstance(api_data, dict) and isinstance(api_data.get("chapters"), list):
                    chapter_data = api_data["chapters"]
            except (ParserError, json.JSONDecodeError, AttributeError):
                pass
        for row in chapter_data:
            if not isinstance(row, dict) or not row.get("url"):
                continue
            link = urljoin(final_url, str(row["url"]))
            title = str(row.get("name") or row.get("slug") or row.get("id") or "Chapter")
            chapters[link] = {
                "title": title,
                "uniq": str(row.get("id") or row.get("slug") or link),
                "link": link,
                "downloadable": True,
                "availability": "Публичная глава",
                "date": str(row.get("updatedAt") or row.get("updated_at") or ""),
            }
        for match in re.finditer(r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', page, re.I | re.S):
            link = urljoin(final_url, html.unescape(match.group(1)))
            path = urlparse(link).path.rstrip("/")
            if not re.search(r"/(?:chapter|notice)-[^/]+$", path, re.I):
                continue
            title = _plain_text(match.group(2))
            if not title:
                continue
            chapters.setdefault(link, {
                "title": title,
                "uniq": path.rsplit("/", 1)[-1],
                "link": link,
                "downloadable": True,
                "availability": "Публичная глава",
            })
        if not chapters:
            raise ParserError("Comizy не вернул список глав")

        def chapter_key(item: dict[str, Any]) -> tuple[int, float, str]:
            number = re.search(r"(\d+(?:\.\d+)?)", item["title"])
            return (0 if number else 1, float(number.group(1)) if number else 0, item["title"])

        chapter_rows = sorted(chapters.values(), key=chapter_key)
        slug = [part for part in urlparse(final_url).path.split("/") if part]
        title = str(manga_data.get("name") or _meta(page, "og:title") or (slug[0].replace("-", " ").title() if slug else "Comizy"))
        title = re.sub(r"\s*[-|]\s*(?:Comizy|MangaBuddy).*$", "", title, flags=re.I).strip()
        authors = manga_data.get("authors") or []
        return {
            "source": "comizy",
            "link": final_url,
            "uniq": slug[0] if slug else title,
            "title": title,
            "author": ", ".join(str(item.get("name")) for item in authors if isinstance(item, dict) and item.get("name")),
            "summary": str(manga_data.get("summary") or _meta(page, "og:description")),
            "cover": str(manga_data.get("cover") or _meta(page, "og:image")),
            "genres": [str(item.get("name")) for item in manga_data.get("genres") or [] if isinstance(item, dict) and item.get("name")],
            "status": str(manga_data.get("status") or ""),
            "chapters": chapter_rows,
        }

    def chapter(self, url: str) -> list[str]:
        parsed = urlparse(normalize_url(url))
        if parsed.hostname not in self.accepted_hosts:
            raise ParserError("Некорректная ссылка главы Comizy")
        page, _ = self.fetch(url, {"headers": {"Referer": self.host + "/"}})
        images = re.findall(
            r'https://x\d+\.cmzcdn\.org/[^"\'\\<>\s]+\.(?:webp|jpe?g|png)(?:\?[^"\'\\<>\s]*)?',
            page,
            re.I,
        )
        images = list(dict.fromkeys(html.unescape(item) for item in images))
        if not images:
            raise ParserError("Comizy не вернул изображения главы")
        return images


class NaverWebtoonEngine(Engine):
    host = "https://comic.naver.com"

    def __init__(self, timeout: float = 30):
        super().__init__(Source(Path("naver-webtoon"), {
            "name": "naver-webtoon", "title": "Naver Webtoon", "host": self.host,
        }), timeout=timeout)

    def _json(self, path: str) -> dict[str, Any]:
        page, _ = self.fetch(self.host + path, {"headers": {"Referer": self.host + "/"}})
        try:
            payload = json.loads(page)
        except json.JSONDecodeError as exc:
            raise ParserError("Naver Webtoon вернул некорректный JSON") from exc
        if not isinstance(payload, dict):
            raise ParserError("Naver Webtoon вернул неожиданный ответ")
        return payload

    def manga(self, url: str) -> dict[str, Any]:
        parsed = urlparse(normalize_url(url))
        if parsed.hostname not in {"comic.naver.com", "m.comic.naver.com"}:
            raise ParserError("Для Naver Webtoon нужна ссылка comic.naver.com")
        title_id = _query_number(url, "titleId")
        info = self._json(f"/api/article/list/info?{urlencode({'titleId': title_id})}")
        first_page = self._json(f"/api/article/list?{urlencode({'titleId': title_id, 'page': 1})}")
        articles = list(first_page.get("articleList") or [])
        page_info = first_page.get("pageInfo") or {}
        for page_number in range(2, int(page_info.get("totalPages") or 1) + 1):
            payload = self._json(f"/api/article/list?{urlencode({'titleId': title_id, 'page': page_number})}")
            articles.extend(payload.get("articleList") or [])
        level = str(info.get("webtoonLevelCode") or "WEBTOON")
        route = {"BEST_CHALLENGE": "bestChallenge", "CHALLENGE": "challenge"}.get(level, "webtoon")
        chapters = []
        for article in sorted(articles, key=lambda item: int(item.get("no") or 0)):
            number = int(article.get("no") or 0)
            charge = bool(article.get("charge"))
            chapters.append({
                "title": str(article.get("subtitle") or f"Episode {number}"),
                "uniq": str(number),
                "link": f"{self.host}/{route}/detail?{urlencode({'titleId': title_id, 'no': number})}",
                "downloadable": not charge,
                "availability": "Платный эпизод Naver" if charge else "Доступен публично",
                "date": str(article.get("serviceDateDescription") or ""),
            })
        if not chapters:
            raise ParserError("Naver Webtoon не вернул главы")
        artists = info.get("communityArtists") or []
        return {
            "source": "naver-webtoon",
            "link": f"{self.host}/{route}/list?{urlencode({'titleId': title_id})}",
            "uniq": str(title_id),
            "title": str(info.get("titleName") or title_id),
            "author": ", ".join(str(item.get("name")) for item in artists if item.get("name")),
            "summary": str(info.get("synopsis") or ""),
            "cover": str(info.get("posterThumbnailUrl") or info.get("thumbnailUrl") or ""),
            "genres": [str(item.get("description")) for item in info.get("genres") or [] if item.get("description")],
            "tags": [str(item) for item in info.get("challengeTagList") or []],
            "status": "completed" if info.get("finished") else "ongoing",
            "chapters": chapters,
        }

    def chapter(self, url: str) -> list[str]:
        parsed = urlparse(normalize_url(url))
        if parsed.hostname not in {"comic.naver.com", "m.comic.naver.com"}:
            raise ParserError("Некорректная ссылка главы Naver Webtoon")
        page, _ = self.fetch(url, {"headers": {"Referer": self.host + "/"}})
        images = []
        for match in re.finditer(r'<img\b(?=[^>]*\bid=["\']content_image_\d+["\'])([^>]*)>', page, re.I | re.S):
            tag = match.group(0)
            source = re.search(r'\b(?:data-src|src)=["\']([^"\']+)', tag, re.I)
            if source:
                images.append(html.unescape(source.group(1)))
        if not images:
            raise ParserError("Изображения главы недоступны: эпизод может требовать вход или оплату")
        return list(dict.fromkeys(images))


class NaverSeriesEngine(Engine):
    host = "https://series.naver.com"

    def __init__(self, timeout: float = 30):
        super().__init__(Source(Path("naver-series"), {
            "name": "naver-series", "title": "Naver Series", "host": self.host,
        }), timeout=timeout)

    def manga(self, url: str) -> dict[str, Any]:
        parsed = urlparse(normalize_url(url))
        if parsed.hostname not in {"series.naver.com", "m.series.naver.com"}:
            raise ParserError("Для Naver Series нужна ссылка series.naver.com")
        product_no = _query_number(url, "productNo")
        canonical = f"{self.host}/comic/detail.series?{urlencode({'productNo': product_no})}"
        page, _ = self.fetch(canonical, {"headers": {"Referer": self.host + "/comic/home.series"}})
        endpoint_match = re.search(r"sVolumeListUrl\s*:\s*['\"]([^'\"]+)", page)
        if not endpoint_match:
            raise ParserError("Naver Series не вернул адрес списка выпусков")
        endpoint = html.unescape(endpoint_match.group(1))
        total_match = re.search(r"[?&]totalCount=(\d+)", endpoint)
        total = int(total_match.group(1)) if total_match else 0
        volumes: list[dict[str, Any]] = []
        seen: set[int] = set()
        for page_number in range(1, 101):
            separator = "&" if "?" in endpoint else "?"
            raw, _ = self.fetch(self.host + endpoint + separator + urlencode({"page": page_number}),
                                {"headers": {"Referer": canonical}})
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ParserError("Naver Series вернул некорректный список выпусков") from exc
            batch = payload.get("resultData") or []
            if not batch:
                break
            before = len(volumes)
            for volume in batch:
                volume_product = int(volume.get("productNo") or 0)
                if volume_product and volume_product not in seen:
                    seen.add(volume_product)
                    volumes.append(volume)
            if (total and len(volumes) >= total) or len(volumes) == before:
                break
        chapters = []
        for volume in sorted(volumes, key=lambda item: int(item.get("volumeNo") or 0)):
            number = int(volume.get("volumeNo") or 0)
            volume_product = int(volume.get("productNo") or 0)
            free = bool(volume.get("freeYn") == "Y" or volume.get("lendingFree"))
            chapters.append({
                "title": str(volume.get("volumnNameText") or volume.get("volumeName") or f"Episode {number}"),
                "uniq": str(volume_product),
                "link": f"{self.host}/comic/volumeDetail.series?{urlencode({'productNo': volume_product})}",
                "downloadable": False,
                "free": free,
                "availability": "Naver Series Viewer: скачивание пока не поддерживается",
            })
        if not chapters:
            raise ParserError("Naver Series не вернул выпуски")
        title = _meta(page, "og:title")
        if not title:
            heading = re.search(r'<h2[^>]*>(.*?)</h2>', page, re.I | re.S)
            title = re.sub(r"<[^>]+>", "", heading.group(1)).strip() if heading else str(product_no)
        first = volumes[0] if volumes else {}
        return {
            "source": "naver-series",
            "link": canonical,
            "uniq": str(product_no),
            "title": title,
            "author": str(first.get("personNameList") or ""),
            "summary": _meta(page, "og:description"),
            "cover": _meta(page, "og:image"),
            "status": "",
            "chapters": chapters,
            "notice": "Список выпусков доступен. Изображения защищены Naver Series Viewer.",
        }

    def chapter(self, url: str) -> list[str]:
        raise ParserError("Скачивание Naver Series пока недоступно: главы открываются в защищённом Viewer")


class KakaoPageEngine(Engine):
    host = "https://page.kakao.com"

    def __init__(self, timeout: float = 30):
        super().__init__(Source(Path("kakao-page"), {
            "name": "kakao-page", "title": "Kakao Page", "host": self.host,
        }), timeout=timeout)

    def _json(self, path: str, referer: str) -> dict[str, Any]:
        try:
            raw, _ = self.fetch(self.host + path, {"headers": {
                "Accept": "application/json, text/plain, */*",
                "Referer": referer,
                "X-Requested-With": "XMLHttpRequest",
            }})
            payload = json.loads(raw)
        except (ParserError, json.JSONDecodeError) as exc:
            raise ParserError(
                "Kakao Page отклонил прямой запрос. Источник требует браузерную сессию; "
                "метаданные и скачивание пока недоступны."
            ) from exc
        if not isinstance(payload, dict):
            raise ParserError("Kakao Page вернул неожиданный ответ")
        return payload

    def manga(self, url: str) -> dict[str, Any]:
        parsed = urlparse(normalize_url(url))
        if parsed.hostname not in {"page.kakao.com", "m-page.kakao.com"}:
            raise ParserError("Для Kakao Page нужна ссылка page.kakao.com/content/ID")
        series_id = _path_number(url, "content")
        canonical = f"{self.host}/content/{series_id}"
        overview = self._json(
            f"/api/gateway/api/v1/content/overview?{urlencode({'series_id': series_id})}", canonical
        )
        product_list = self._json(
            f"/api/gateway/api/v2/content/product/list?{urlencode({'series_id': series_id, 'sort_type': 'asc', 'after': 0})}",
            canonical,
        )
        content = overview.get("contentHomeOverview") or overview.get("content") or overview
        if isinstance(content, dict) and isinstance(content.get("content"), dict):
            content = content["content"]
        edges = product_list.get("contentHomeProductList") or product_list.get("edges") or []
        if isinstance(edges, dict):
            edges = edges.get("edges") or edges.get("products") or []
        chapters = []
        for edge in edges if isinstance(edges, list) else []:
            item = edge.get("node") if isinstance(edge, dict) else None
            item = item if isinstance(item, dict) else edge
            single = item.get("single") if isinstance(item, dict) else None
            single = single if isinstance(single, dict) else item
            product_id = single.get("productId") or single.get("id")
            if not product_id:
                continue
            chapters.append({
                "title": str(single.get("title") or single.get("name") or product_id),
                "uniq": str(product_id),
                "link": f"{self.host}/viewer?productId={product_id}",
                "downloadable": False,
                "availability": "Защищённый Kakao Page Viewer",
            })
        return {
            "source": "kakao-page", "link": canonical, "uniq": str(series_id),
            "title": str(content.get("title") or content.get("seriesTitle") or series_id),
            "author": str(content.get("author") or content.get("authors") or ""),
            "summary": str(content.get("description") or content.get("synopsis") or ""),
            "cover": str(content.get("thumbnail") or content.get("image") or ""),
            "chapters": chapters,
            "notice": "Список доступен только когда Kakao разрешает запросы текущей браузерной сессии. Скачивание Viewer не поддерживается.",
        }

    def chapter(self, url: str) -> list[str]:
        raise ParserError("Скачивание Kakao Page недоступно: главы открываются в защищённом Viewer")


class RidiEngine(Engine):
    host = "https://ridibooks.com"

    def __init__(self, timeout: float = 30):
        super().__init__(Source(Path("ridibooks"), {
            "name": "ridibooks", "title": "Ridi", "host": self.host,
        }), timeout=timeout)

    def manga(self, url: str) -> dict[str, Any]:
        parsed = urlparse(normalize_url(url))
        if parsed.hostname not in {"ridibooks.com", "www.ridibooks.com"}:
            raise ParserError("Для Ridi нужна ссылка ridibooks.com/books/ID")
        book_id = _path_number(url, "books")
        canonical = f"{self.host}/books/{book_id}"
        page, _ = self.fetch(canonical, {"headers": {"Referer": self.host + "/webtoon/recommendation"}})
        rows = _assigned_json(page, "seriesBookListJson")
        if not isinstance(rows, list) or not rows:
            raise ParserError("Ridi не вернул список выпусков")
        book = _assigned_json(page, "book")
        if not isinstance(book, dict):
            book = {}
        try:
            detail = _assigned_json(page, "bookDetail")
        except ParserError:
            detail = {}
        if not isinstance(detail, dict):
            detail = {}
        price_info = detail.get("series_price_info") or {}
        try:
            free_count = int(price_info.get("free_book_count") or 0)
        except (TypeError, ValueError):
            free_count = 0
        chapters = []
        for index, row in enumerate(rows, 1):
            if not isinstance(row, dict):
                continue
            chapter_id = str(row.get("id") or "")
            if not chapter_id:
                continue
            volume = row.get("volume")
            chapters.append({
                "title": str(row.get("title") or (f"Episode {volume}" if volume else f"Episode {index}")),
                "uniq": chapter_id,
                "link": f"{self.host}/books/{chapter_id}",
                "downloadable": False,
                "trial": bool(row.get("is_trial")),
                "free": index <= free_count,
                "availability": "Ridi Viewer: скачивание не поддерживается",
            })
        title = str(detail.get("series_title") or _meta(page, "og:title") or book.get("title") or book_id)
        title = re.sub(r"\s*[-|]\s*RIDI.*$", "", title, flags=re.I).strip()
        summary = str(detail.get("description") or _meta(page, "og:description"))
        summary = html.unescape(re.sub(r"<[^>]+>", "", summary)).strip()
        return {
            "source": "ridibooks", "link": canonical, "uniq": str(book_id),
            "title": title,
            "author": str(detail.get("author") or book.get("author") or book.get("author_name") or ""),
            "summary": summary,
            "cover": _meta(page, "og:image") or str(book.get("thumbnail") or ""),
            "chapters": chapters,
            "notice": "Метаданные и список выпусков доступны. Изображения защищены Ridi Viewer.",
        }

    def chapter(self, url: str) -> list[str]:
        raise ParserError("Скачивание Ridi недоступно: главы открываются в защищённом Viewer")
