from dataclasses import dataclass, field
import mimetypes
from pathlib import Path
import re
import tempfile
from typing import Callable
from urllib.parse import urlsplit
import uuid
import zipfile

from .config import Settings
from .content import clean_media_url, parse_article_html, render_markdown
from .weread import ListedArticle


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
        self.image_fetcher = image_fetcher

    def archive(
        self,
        articles: list[ListedArticle],
        fetch_article: Callable[[ListedArticle], str],
        include_images: bool,
        progress_callback: Callable[[int], None] | None = None,
        book_id: str = "",
    ) -> ArchiveResult:
        total = len(articles)
        progress = progress_callback or (lambda value: None)
        progress(0)
        entries: list[tuple[str, bytes]] = []
        failures: list[tuple[str, str]] = []
        warnings: list[tuple[str, str]] = []
        used_names: set[str] = set()

        for index, listed in enumerate(articles):
            try:
                html = fetch_article(listed)
                parsed = parse_article_html(html, listed, book_id=book_id)
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
                markdown = render_markdown(parsed, local_images).encode("utf-8")
                markdown_name = _unique_name(f"{_safe_filename(parsed.title, index)}.md", used_names)
                used_names.add(markdown_name)
                entries.append((markdown_name, markdown))
                entries.extend(image_entries)
            except Exception as exc:
                failures.append((listed.original_id, _safe_error(exc)))
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
    return message[:300]
