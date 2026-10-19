import io
import json
from datetime import datetime, timezone
import urllib.error

import pytest

from app.weread import WeReadClient, WeReadError
from app.config import Settings


class Response:
    status = 200

    def __init__(self, payload):
        self.body = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, limit=-1):
        return self.body if limit < 0 else self.body[:limit]


def account(book_id, title):
    return {"bookId": book_id, "title": title}


def review_group(timestamp, records):
    return {
        "createTime": timestamp,
        "subReviews": [
            {"review": {"mpInfo": {"title": title, "originalId": original_id, "time": published}}}
            for title, original_id, published in records
        ],
    }


def day_timestamp(value):
    return int(datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp())


def client_with(payloads):
    calls = []

    def urlopen(request, timeout=15):
        calls.append(request.full_url)
        payload = payloads.pop(0)
        return Response(payload)

    settings = Settings(app_password="test-password-long")
    return WeReadClient(settings=settings, urlopen=urlopen, sleep=lambda _: None), calls


def test_shelf_returns_only_followed_public_accounts():
    client, calls = client_with([{
        "books": [
            account("BOOK_1", "普通书籍"),
            account("MP_WXS_42", "刘润"),
            account("MP_WXS_73", "人间罗盘"),
        ]
    }])

    accounts = client.list_accounts("wr_skey=s; wr_vid=v")

    assert [(item.book_id, item.title) for item in accounts] == [
        ("MP_WXS_42", "刘润"), ("MP_WXS_73", "人间罗盘")
    ]
    assert "/web/shelf/sync?" in calls[0]


def test_cookie_validation_requires_we_read_credentials_and_a_valid_shelf():
    client, _ = client_with([{"books": []}])

    with pytest.raises(WeReadError, match="wr_skey"):
        client.validate_cookie("other=value")
    assert client.validate_cookie("wr_skey=s; wr_vid=v") == []


def test_article_content_retries_transient_rate_limit_before_returning_html():
    calls = []
    sleeps = []

    class HtmlResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, _limit=-1):
            return b'<div id="js_content"><p>body</p></div>'

    def urlopen(request, timeout=30):
        calls.append(request.full_url)
        if len(calls) == 1:
            raise urllib.error.HTTPError(request.full_url, 429, "limited", {}, io.BytesIO())
        return HtmlResponse()

    client = WeReadClient(
        settings=Settings(app_password="test-password-long"),
        urlopen=urlopen,
        sleep=sleeps.append,
    )

    html = client.get_article_html("wr_skey=s; wr_vid=v", "MP_WXS_42", "article-1")

    assert "js_content" in html
    assert len(calls) == 2
    assert sleeps == [1.0]


def test_article_response_expands_groups_deduplicates_and_reports_actual_dates():
    rows = [review_group(10, [(f"文章 {i}", f"id-{i}", 1767225600 + i) for i in range(76)])]
    rows.append(review_group(9, [("文章 0", "id-0", 1767225600)]))
    client, _ = client_with([{"reviews": rows, "clearAll": 1}])

    result = client.list_articles("wr_skey=s; wr_vid=v", "MP_WXS_42", max_articles=100)

    assert len(result.articles) == 76
    assert result.earliest_date <= result.latest_date
    assert result.pages == 1
    assert result.stop_reason == "we_read_terminal"


def test_article_list_uses_create_time_cursor_until_empty_page():
    client, calls = client_with([
        {"reviews": [review_group(20, [("较新文章", "new", 1767225600)])], "clearAll": 0},
        {"reviews": [review_group(10, [("较早文章", "old", 1735689600)])], "clearAll": 0},
        {"reviews": [], "clearAll": 0},
    ])

    result = client.list_articles("wr_skey=s; wr_vid=v", "MP_WXS_42", max_articles=100)

    assert [article.original_id for article in result.articles] == ["new", "old"]
    assert result.pages == 3
    assert "offset=20" in calls[1]
    assert "offset=10" in calls[2]
    assert result.stop_reason == "empty_page"


def test_clear_all_stops_without_guessing_another_pagination_mode():
    client, calls = client_with([{
        "reviews": [review_group(20, [("近期文章", "new", 1767225600)])],
        "clearAll": 1,
    }])

    result = client.list_articles("wr_skey=s; wr_vid=v", "MP_WXS_42", max_articles=100)

    assert result.stop_reason == "we_read_terminal"
    assert len(calls) == 1


@pytest.mark.parametrize(
    ("payload", "expected"),
    [({"errCode": -2041, "errMsg": "captcha"}, "captcha"),
     ({"errCode": -2010, "errMsg": "user not found"}, "expired")],
)
def test_we_read_auth_errors_are_classified_without_exposing_cookie(payload, expected):
    client, _ = client_with([payload])

    with pytest.raises(WeReadError) as caught:
        client.list_accounts("wr_skey=secret-value; wr_vid=private")

    assert caught.value.kind == expected
    assert "secret-value" not in str(caught.value)


def test_repeated_page_stops_instead_of_looping_forever():
    same_page = {
        "reviews": [review_group(20, [("文章", "id", 1767225600)])],
        "clearAll": 0,
    }
    client, calls = client_with([same_page, same_page])

    result = client.list_articles("wr_skey=s; wr_vid=v", "MP_WXS_42", max_articles=100)

    assert result.stop_reason == "repeated_page"
    assert len(calls) == 2


def test_list_stops_when_article_limit_is_reached_mid_page():
    client, calls = client_with([{
        "reviews": [review_group(20, [(f"文章 {i}", str(i), 1767225600 + i) for i in range(8)])],
        "clearAll": 0,
    }])

    result = client.list_articles("wr_skey=s; wr_vid=v", "MP_WXS_42", max_articles=3)

    assert len(result.articles) == 3
    assert result.stop_reason == "article_limit"
    assert len(calls) == 1


def test_list_default_capacity_is_not_limited_to_twenty_articles():
    records = [(f"文章 {i}", str(i), day_timestamp(f"2026-10-{i + 1:02d}")) for i in range(25)]
    client, calls = client_with([{
        "reviews": [review_group(20, records)],
        "clearAll": 1,
    }])

    result = client.list_articles("wr_skey=s; wr_vid=v", "MP_WXS_42")

    assert len(result.articles) == 25
    assert result.stop_reason == "we_read_terminal"
    assert len(calls) == 1


def test_date_range_continues_pagination_and_returns_only_matching_articles():
    client, calls = client_with([
        {"reviews": [review_group(30, [("较新", "new", day_timestamp("2026-10-10"))])], "clearAll": 1},
        {"reviews": [review_group(20, [
            ("命中", "match", day_timestamp("2026-09-10")),
            ("较早", "old", day_timestamp("2026-08-01")),
        ])], "clearAll": 0},
    ])

    result = client.list_articles(
        "wr_skey=s; wr_vid=v",
        "MP_WXS_42",
        max_articles=100,
        from_date="2026-09-01",
        to_date="2026-09-30",
    )

    assert [article.original_id for article in result.articles] == ["match"]
    assert result.stop_reason == "date_range_reached"
    assert result.pages == 2
    assert "offset=30" in calls[1]
