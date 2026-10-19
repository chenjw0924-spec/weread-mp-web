import ast
from dataclasses import dataclass
from datetime import datetime, timezone
from html import unescape
import json
from pathlib import PurePosixPath
import re
from typing import Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
from markdownify import markdownify

from .weread import ListedArticle


class ContentError(Exception):
    pass


@dataclass(frozen=True)
class ParsedArticle:
    title: str
    published_at: str
    account: str
    source_url: str
    account_id: str
    article_id: str
    markdown_body: str
    image_urls: list[str]


def parse_article_html(html: str, listed: ListedArticle, book_id: str = "") -> ParsedArticle:
    if not html or len(html) > 8 * 1024 * 1024:
        raise ContentError("文章响应为空或超过大小限制，无法生成正文。")

    soup = BeautifulSoup(html, "html.parser")
    title = _meta(soup, "og:title") or listed.title
    author = _meta(soup, "og:article:author") or listed.account_name
    source_url = _canonical_url(_meta(soup, "og:url"))

    body = soup.find(id="js_content") or soup.find(class_="rich_media_content")
    if body is None or not body.get_text(" ", strip=True):
        text_page = _text_page_body(soup)
        body = BeautifulSoup(text_page or "", "html.parser")
    if body is None or not body.get_text(" ", strip=True):
        raise ContentError("没有找到可归档的正文，可能是当前消息类型或微信读书未收录。")

    for node in body.select("script, style, noscript"):
        node.decompose()

    image_urls = []
    for image in body.select("img"):
        raw_url = image.get("data-src") or image.get("src") or ""
        url = clean_media_url(str(raw_url))
        if url and url not in image_urls:
            image_urls.append(url)
            image["src"] = url
        image.attrs.pop("data-src", None)
        if not url:
            image.decompose()

    body_markdown = markdownify(str(body), heading_style="ATX").strip()
    return ParsedArticle(
        title=title,
        published_at=_published_date(listed.published_at),
        account=author,
        source_url=source_url,
        account_id=book_id,
        article_id=listed.original_id,
        markdown_body=body_markdown,
        image_urls=image_urls,
    )


def parse_picture_pages(html: str) -> list[str]:
    marker = "picture_page_info_list"
    marker_index = html.find(marker)
    if marker_index < 0:
        return []
    array_start = html.find("[", marker_index + len(marker))
    if array_start < 0:
        return []
    array = _balanced_array(html, array_start)
    if array is None:
        return []

    urls = []
    for item in _array_objects(array):
        raw = _js_field(item, "cdn_url")
        width = _number_field(item, "width")
        height = _number_field(item, "height")
        url = clean_media_url(raw or "")
        if url and width > 0 and height > 0 and url not in urls:
            urls.append(url)
    return urls


def clean_media_url(raw: str) -> str:
    value = raw
    for _ in range(3):
        value = re.sub(r"\\+x([0-9a-fA-F]{2})", lambda match: chr(int(match.group(1), 16)), value)
        decoded = unescape(value)
        if decoded == value:
            break
        value = decoded
    parts = urlsplit(value.strip())
    host = (parts.hostname or "").lower().rstrip(".")
    if parts.scheme != "https" or not (host.endswith(".qpic.cn") or host.endswith(".qlogo.cn")):
        return ""
    return urlunsplit(parts._replace(fragment=""))


def render_markdown(
    article: ParsedArticle,
    local_images: Mapping[str, str],
) -> str:
    fields = (
        ("title", article.title),
        ("date", article.published_at),
        ("account", article.account),
        ("source", article.source_url),
        ("account_id", article.account_id),
        ("article_id", article.article_id),
    )
    frontmatter = "\n".join(
        f"{name}: {json.dumps(value, ensure_ascii=False)}" for name, value in fields
    )

    body = article.markdown_body
    for remote, relative in local_images.items():
        path = PurePosixPath(relative)
        if path.is_absolute() or ".." in path.parts:
            continue
        body = body.replace(f"]({remote})", f"]({path.as_posix()})")

    if not body.strip() and article.image_urls:
        images = []
        for remote in article.image_urls:
            relative = local_images.get(remote, remote)
            path = PurePosixPath(relative)
            if relative != remote and (path.is_absolute() or ".." in path.parts):
                relative = remote
            images.append(f"![]({relative})")
        body = "\n\n".join(images)

    heading = f"# {article.title}" if article.title else "# Untitled article"
    return f"---\n{frontmatter}\n---\n\n{heading}\n\n{body.strip()}\n"


def _meta(soup: BeautifulSoup, name: str) -> str:
    element = soup.find("meta", attrs={"property": name})
    return str(element.get("content", "")).strip() if element else ""


def _canonical_url(raw: str) -> str:
    if not raw:
        return ""
    parts = urlsplit(unescape(raw))
    if parts.hostname != "mp.weixin.qq.com" or parts.scheme not in {"http", "https"}:
        return ""
    query = [(key, value) for key, value in parse_qsl(parts.query) if key != "chksm"]
    return urlunsplit(("https", parts.netloc, parts.path, urlencode(query), ""))


def _text_page_body(soup: BeautifulSoup) -> str:
    for script in soup.find_all("script"):
        match = re.search(
            r"\bcontent_noencode\s*:\s*('(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\")",
            script.string or script.get_text(),
            flags=re.S,
        )
        if not match:
            continue
        try:
            value = ast.literal_eval(match.group(1))
        except (SyntaxError, ValueError):
            continue
        if isinstance(value, str):
            return unescape(value)
    return ""


def _balanced_array(source: str, start: int) -> str | None:
    depth = 0
    quote = ""
    escaped = False
    for index in range(start, len(source)):
        char = source[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            continue
        if char in {"'", '"'}:
            quote = char
        elif char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    return None


def _array_objects(array: str) -> list[str]:
    objects = []
    depth = 0
    start = None
    quote = ""
    escaped = False
    for index, char in enumerate(array):
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            continue
        if char in {"'", '"'}:
            quote = char
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0 and start is not None:
                objects.append(array[start:index + 1])
                start = None
    return objects


def _js_field(source: str, name: str) -> str | None:
    match = re.search(
        rf"\b{re.escape(name)}\s*:\s*('(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\")",
        source,
        flags=re.S,
    )
    if not match:
        return None
    try:
        value = ast.literal_eval(match.group(1))
    except (SyntaxError, ValueError):
        return None
    return value if isinstance(value, str) else None


def _number_field(source: str, name: str) -> int:
    match = re.search(rf"\b{re.escape(name)}\s*:\s*'?([0-9]+)", source)
    return int(match.group(1)) if match else 0


def _published_date(timestamp: int) -> str:
    if not timestamp:
        return ""
    return datetime.fromtimestamp(timestamp, timezone.utc).astimezone(
        ZoneInfo("Asia/Shanghai")
    ).date().isoformat()
