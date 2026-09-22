"""Built-in adapters for sources that cannot be expressed as MWX JSON rules."""

from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

from mwx import Engine, ParserError, Source, normalize_url


BUILTIN_SOURCES: list[dict[str, Any]] = [
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
]


def builtin_source_rows() -> list[dict[str, Any]]:
    return [dict(item) for item in BUILTIN_SOURCES]


def create_engine(source_id: str, parser_dir: Path, timeout: float) -> Engine:
    if source_id == "naver-webtoon":
        return NaverWebtoonEngine(timeout=timeout)
    if source_id == "naver-series":
        return NaverSeriesEngine(timeout=timeout)
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
