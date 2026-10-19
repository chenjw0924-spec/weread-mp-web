from dataclasses import dataclass, field
import hashlib
import logging
import mimetypes
from pathlib import Path
import re
import tempfile
import time
from typing import Callable
from urllib.parse import urlsplit
import urllib.error
import urllib.request
import uuid
import zipfile

from .config import Settings
from .content import clean_media_url, parse_article_html, render_markdown
from .weread import ListedArticle


LOGGER = logging.getLogger("weread.archive")


@dataclass
class ArchiveResult:
    zip_path: Path
    total: int
    completed: int
    failures: list[tuple[str, str]] = field(default_factory=list)
    warnings: list[tuple[str, str]] = field(default_factory=list)


class ArchiveService:
    def __init__(
        self,
        settings: Settings | None = None,
        temp_dir: str | Path | None = None,
        image_fetcher: Callable[[str], bytes] | None = None,
    ):
        self.settings = settings or Settings(app_password="archive-default-password")
        self.temp_dir = Path(temp_dir) if temp_dir else Path(tempfile.gettempdir()) / "weread-mp-web"
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.image_fetcher = image_fetcher or self._fetch_image

    def _fetch_image(self, url: str) -> bytes:
        request = urllib.request.Request(url, headers={"User-Agent": "WeRead-MP-Web/1.0", "Accept": "image/*"})
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                payload = response.read(self.settings.max_image_bytes + 1)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RuntimeError("图片网络请求失败") from exc
        if len(payload) > self.settings.max_image_bytes:
            raise RuntimeError("图片超过大小限制")
        return payload

    def archive(
        self,
        articles: list[ListedArticle],
        fetch_article: Callable[[ListedArticle], str],
        include_images: bool,
        progress_callback: Callable[[int], None] | None = None,
        book_id: str = "",
        item_callback: Callable[[str, str, str], None] | None = None,
        job_id: str = "",
    ) -> ArchiveResult:
        total = len(articles)
        progress = progress_callback or (lambda value: None)
        progress(0)
        entries: list[tuple[str, bytes]] = []
        failures: list[tuple[str, str]] = []
        warnings: list[tuple[str, str]] = []
        used_names: set[str] = set()

        for index, listed in enumerate(articles):
            started_at = time.monotonic()
            title_hash = hashlib.sha256(listed.title.encode("utf-8", "replace")).hexdigest()[:12]
            LOGGER.info(
                "archive_article_start job_id=%s article_id=%s title_hash=%s index=%d total=%d",
                job_id or "-",
                listed.original_id,
                title_hash,
                index + 1,
                total,
            )
            if item_callback:
                item_callback(listed.original_id, "running", "")
            try:
                try:
                    html = fetch_article(listed)
                except Exception as exc:
                    LOGGER.warning(
                        "archive_article_failed job_id=%s article_id=%s title_hash=%s stage=fetch error_type=%s elapsed_ms=%d",
                        job_id or "-",
                        listed.original_id,
                        title_hash,
                        _error_type(exc),
                        round((time.monotonic() - started_at) * 1000),
                    )
                    raise
                try:
                    parsed = parse_article_html(html, listed, book_id=book_id)
                except Exception as exc:
                    LOGGER.warning(
                        "archive_article_failed job_id=%s article_id=%s title_hash=%s stage=parse error_type=%s elapsed_ms=%d",
                        job_id or "-",
                        listed.original_id,
                        title_hash,
                        _error_type(exc),
                        round((time.monotonic() - started_at) * 1000),
                    )
                    raise
                local_images: dict[str, str] = {}
                image_entries: list[tuple[str, bytes]] = []
                image_failures = 0
                if include_images and self.image_fetcher:
                    for image_index, image_url in enumerate(parsed.image_urls, start=1):
                        try:
                            clean_url = clean_media_url(image_url)
                            if not clean_url:
                                raise ValueError("图片地址不是受支持的微信媒体地址")
                            payload = self.image_fetcher(clean_url)
                            if not isinstance(payload, (bytes, bytearray)):
                                raise ValueError("图片响应不是二进制内容")
                            if len(payload) > self.settings.max_image_bytes:
                                raise ValueError("图片超过大小限制")
                            relative = f"images/{_safe_filename(parsed.title, index)}-{image_index:02d}{_extension(clean_url)}"
                            relative = _unique_name(relative, used_names)
                            used_names.add(relative)
                            local_images[image_url] = relative
                            image_entries.append((relative, bytes(payload)))
                        except Exception:
                            image_failures += 1
                if image_failures:
                    warnings.append((listed.original_id, f"图片下载失败 {image_failures} 个"))
                    LOGGER.warning(
                        "archive_article_warning job_id=%s article_id=%s title_hash=%s stage=image failed_count=%d",
                        job_id or "-",
                        listed.original_id,
                        title_hash,
                        image_failures,
                    )
                markdown = render_markdown(parsed, local_images).encode("utf-8")
                markdown_name = _unique_name(f"{_safe_filename(parsed.title, index)}.md", used_names)
                used_names.add(markdown_name)
                entries.append((markdown_name, markdown))
                entries.extend(image_entries)
                if item_callback:
                    item_callback(listed.original_id, "completed", "")
                LOGGER.info(
                    "archive_article_completed job_id=%s article_id=%s title_hash=%s elapsed_ms=%d",
                    job_id or "-",
                    listed.original_id,
                    title_hash,
                    round((time.monotonic() - started_at) * 1000),
                )
            except Exception as exc:
                reason = _safe_error(exc)
                failures.append((listed.original_id, reason))
                if item_callback:
                    item_callback(listed.original_id, "failed", reason)
            progress(round((index + 1) * 100 / total) if total else 100)

        zip_path = self.temp_dir / f"archive-{uuid.uuid4().hex}.zip"
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, payload in entries:
                archive.writestr(name, payload)
        return ArchiveResult(zip_path, total, total - len(failures), failures, warnings)


def _safe_filename(value: str, index: int) -> str:
    value = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", "_", value or "未命名文章").strip(" .")
    return (value or f"article-{index:03d}")[:120]


def _unique_name(name: str, used: set[str]) -> str:
    if name not in used:
        return name
    path = Path(name)
    for index in range(2, 1000):
        candidate = f"{path.stem}-{index}{path.suffix}"
        if path.parent != Path("."):
            candidate = str(path.parent / candidate)
        if candidate not in used:
            return candidate
    raise RuntimeError("归档文件名数量超过限制")


def _extension(url: str) -> str:
    suffix = Path(urlsplit(url).path).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}:
        return suffix
    fmt = dict(item.split("=", 1) for item in urlsplit(url).query.split("&") if "=" in item).get("wx_fmt", "")
    return "." + fmt.lower() if fmt.lower() in {"jpg", "jpeg", "png", "gif", "webp", "bmp"} else ".bin"


def _safe_error(exc: Exception) -> str:
    message = str(exc).strip() or exc.__class__.__name__
    message = re.sub(r"(?i)(wr_skey|wr_vid|cookie)=[^;\s]+", r"\1=[redacted]", message)
    return message[:300]


def _error_type(exc: Exception) -> str:
    kind = getattr(exc, "kind", "")
    return str(kind or exc.__class__.__name__)[:80]
