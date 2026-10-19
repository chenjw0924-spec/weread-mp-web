from dataclasses import dataclass
from datetime import datetime, timezone
from http.cookies import SimpleCookie
import json
import random
import re
import time
from typing import Any, Callable
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

from .config import Settings


WEREAD_ORIGIN = "https://weread.qq.com"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class WeReadError(Exception):
    def __init__(self, message: str, kind: str = "upstream"):
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True)
class Account:
    book_id: str
    title: str
    cover_url: str = ""


@dataclass(frozen=True)
class ListedArticle:
    title: str
    summary: str
    account_name: str
    original_id: str
    published_at: int
    read_count: int = 0
    like_count: int = 0

    @property
    def date(self) -> str:
        return format_date(self.published_at)

    @property
    def selectable(self) -> bool:
        return bool(self.original_id)


@dataclass(frozen=True)
class ArticleListResult:
    articles: list[ListedArticle]
    pages: int
    stop_reason: str
    earliest_date: str | None
    latest_date: str | None


def format_date(timestamp: int) -> str:
    if not timestamp:
        return ""
    value = datetime.fromtimestamp(timestamp, timezone.utc).astimezone(ZoneInfo("Asia/Shanghai"))
    return value.date().isoformat()


class WeReadClient:
    def __init__(
        self,
        settings: Settings | None = None,
        urlopen: Callable[..., Any] = urllib.request.urlopen,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.settings = settings or Settings.from_env()
        self._urlopen = urlopen
        self._sleep = sleep

    @staticmethod
    def parse_cookie_header(cookie: str) -> dict[str, str]:
        if not cookie or len(cookie) > 16_384 or "\r" in cookie or "\n" in cookie:
            raise WeReadError("Paste a valid WeRead Cookie header.", "invalid_cookie")
        parsed = SimpleCookie()
        try:
            parsed.load(cookie)
        except Exception as exc:
            raise WeReadError("Paste a valid WeRead Cookie header.", "invalid_cookie") from exc
        values = {name: morsel.value for name, morsel in parsed.items()}
        missing = {"wr_skey", "wr_vid"}.difference(values)
        if missing:
            raise WeReadError("The Cookie header must include wr_skey and wr_vid.", "invalid_cookie")
        return values

    def validate_cookie(self, cookie: str) -> list[Account]:
        self.parse_cookie_header(cookie)
        return self.list_accounts(cookie)

    def list_accounts(self, cookie: str) -> list[Account]:
        data = self.request_json(
            "/web/shelf/sync", {"userVid": "", "synckey": 0}, cookie
        )
        books = data.get("books")
        if not isinstance(books, list):
            raise WeReadError("WeRead returned an invalid shelf response.", "upstream")
        accounts = []
        for book in books:
            if not isinstance(book, dict):
                continue
            book_id = book.get("bookId")
            if isinstance(book_id, str) and book_id.startswith("MP_WXS_"):
                accounts.append(Account(
                    book_id=book_id,
                    title=str(book.get("title") or "Untitled account"),
                    cover_url=str(book.get("cover") or book.get("coverUrl") or ""),
                ))
        return accounts

    def request_json(self, path: str, params: dict[str, Any], cookie: str) -> dict[str, Any]:
        if path not in {"/web/shelf/sync", "/web/mp/articles"}:
            raise WeReadError("Unsupported WeRead API route.", "invalid_request")
        query = urllib.parse.urlencode(params)
        url = f"{WEREAD_ORIGIN}{path}?{query}"
        request = urllib.request.Request(url, headers=self._headers(cookie))
        try:
            with self._urlopen(request, timeout=20) as response:
                if response.status in (401, 403):
                    raise WeReadError("WeRead login expired. Reconnect with a fresh Cookie.", "expired")
                raw = response.read(2 * 1024 * 1024 + 1)
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise WeReadError("WeRead login expired. Reconnect with a fresh Cookie.", "expired") from exc
            if exc.code == 429:
                raise WeReadError("WeRead temporarily limited this request. Wait before trying again.", "rate_limited") from exc
            raise WeReadError(f"WeRead request failed with HTTP {exc.code}.", "upstream") from exc
        except WeReadError:
            raise
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise WeReadError("Could not reach WeRead. Check the server network and try again.", "network") from exc

        if len(raw) > 2 * 1024 * 1024:
            raise WeReadError("WeRead response exceeded the configured size limit.", "response_too_large")
        try:
            data = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WeReadError("WeRead returned an unreadable response.", "upstream") from exc
        if not isinstance(data, dict):
            raise WeReadError("WeRead returned an invalid response.", "upstream")
        self._raise_for_api_error(data)
        return data

    def list_articles(
        self,
        cookie: str,
        book_id: str,
        max_articles: int | None = None,
    ) -> ArticleListResult:
        if not re.fullmatch(r"MP_WXS_[A-Za-z0-9_-]{1,100}", book_id):
            raise WeReadError("Choose a valid WeRead public account.", "invalid_request")

        article_limit = max_articles or self.settings.max_archive_articles
        article_limit = min(article_limit, self.settings.max_archive_articles)
        articles: list[ListedArticle] = []
        seen_ids: set[str] = set()
        fingerprints: set[tuple[str, ...]] = set()
        offset = 0
        pages = 0
        stop_reason = "page_limit"

        for page in range(self.settings.max_list_pages):
            data = self.request_json(
                "/web/mp/articles", {"bookId": book_id, "offset": offset}, cookie
            )
            pages += 1
            reviews = data.get("reviews")
            if not isinstance(reviews, list):
                raise WeReadError("WeRead returned an invalid article list.", "upstream")
            if not reviews:
                stop_reason = "empty_page"
                break

            page_items: list[ListedArticle] = []
            page_ids: list[str] = []
            for group in reviews:
                if not isinstance(group, dict):
                    continue
                for sub_review in group.get("subReviews", []):
                    info = ((sub_review or {}).get("review") or {}).get("mpInfo") or {}
                    if not isinstance(info, dict) or not info.get("title"):
                        continue
                    original_id = str(info.get("originalId") or "")
                    page_ids.append(original_id or f"missing:{info.get('time', 0)}:{info['title']}")
                    if original_id and original_id in seen_ids:
                        continue
                    if original_id:
                        seen_ids.add(original_id)
                    page_items.append(ListedArticle(
                        title=str(info["title"]),
                        summary=str(info.get("content") or ""),
                        account_name=str(info.get("mp_name") or ""),
                        original_id=original_id,
                        published_at=self._timestamp(info.get("time", 0)),
                        read_count=self._integer(info.get("readNum", 0)),
                        like_count=self._integer(info.get("likeNum", 0)),
                    ))

            fingerprint = tuple(page_ids)
            if fingerprint and fingerprint in fingerprints:
                stop_reason = "repeated_page"
                break
            if fingerprint:
                fingerprints.add(fingerprint)
            remaining = article_limit - len(articles)
            articles.extend(page_items[:remaining])
            if len(articles) >= article_limit:
                stop_reason = "article_limit"
                break
            if data.get("clearAll"):
                stop_reason = "we_read_terminal"
                break

            timestamps = [self._integer(group.get("createTime", 0)) for group in reviews]
            valid_timestamps = [value for value in timestamps if value > 0]
            if not valid_timestamps:
                stop_reason = "invalid_cursor"
                break
            next_offset = min(valid_timestamps)
            if next_offset == offset or (offset and next_offset > offset):
                stop_reason = "non_advancing_cursor"
                break
            offset = next_offset
            if page + 1 < self.settings.max_list_pages:
                self._sleep(random.uniform(1.5, 3.0))
        else:
            stop_reason = "page_limit"

        dates = [article.date for article in articles if article.date]
        return ArticleListResult(
            articles=articles,
            pages=pages,
            stop_reason=stop_reason,
            earliest_date=min(dates) if dates else None,
            latest_date=max(dates) if dates else None,
        )

    def get_article_html(self, cookie: str, book_id: str, original_id: str) -> str:
        if not re.fullmatch(r"MP_WXS_[A-Za-z0-9_-]{1,100}", book_id) or not original_id:
            raise WeReadError("Article reference is invalid.", "invalid_request")
        review_id = urllib.parse.quote(f"{book_id}_{original_id}", safe="")
        request = urllib.request.Request(
            f"{WEREAD_ORIGIN}/web/mp/content?reviewId={review_id}",
            headers=self._headers(cookie),
        )
        try:
            with self._urlopen(request, timeout=30) as response:
                raw = response.read(self.settings.max_response_bytes + 1)
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise WeReadError("WeRead login expired. Reconnect with a fresh Cookie.", "expired") from exc
            if exc.code == 429:
                raise WeReadError("WeRead temporarily limited this request. Wait before trying again.", "rate_limited") from exc
            raise WeReadError(f"WeRead article request failed with HTTP {exc.code}.", "upstream") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise WeReadError("Could not reach WeRead while loading an article.", "network") from exc
        if len(raw) > self.settings.max_response_bytes:
            raise WeReadError("Article response exceeded the configured size limit.", "response_too_large")
        html = raw.decode("utf-8", "replace")
        if html.lstrip().startswith("{"):
            try:
                data = json.loads(html)
            except json.JSONDecodeError:
                return html
            if isinstance(data, dict):
                self._raise_for_api_error(data)
        return html

    @staticmethod
    def _headers(cookie: str) -> dict[str, str]:
        if "\r" in cookie or "\n" in cookie:
            raise WeReadError("Cookie header contains invalid characters.", "invalid_cookie")
        return {
            "Cookie": cookie,
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, */*",
        }

    @staticmethod
    def _raise_for_api_error(data: dict[str, Any]) -> None:
        code = data.get("errCode", data.get("errcode"))
        if code in (None, 0, "0", ""):
            return
        if str(code) == "-2041":
            raise WeReadError(
                "WeRead requires a captcha. Complete its normal challenge in your WeRead browser, then reconnect.",
                "captcha",
            )
        if str(code) in {"-2010", "401", "403"}:
            raise WeReadError("WeRead login expired. Reconnect with a fresh Cookie.", "expired")
        if str(code) == "429":
            raise WeReadError("WeRead temporarily limited this request. Wait before trying again.", "rate_limited")
        raise WeReadError("WeRead rejected the request.", "upstream")

    @staticmethod
    def _timestamp(value: Any) -> int:
        try:
            timestamp = int(value)
        except (TypeError, ValueError):
            return 0
        return timestamp if timestamp > 0 else 0

    @staticmethod
    def _integer(value: Any) -> int:
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0
