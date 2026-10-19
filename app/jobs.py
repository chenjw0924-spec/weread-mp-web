from dataclasses import dataclass, field
import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

from .archive import ArchiveResult, ArchiveService
from .auth import Session
from .weread import ListedArticle


LOGGER = logging.getLogger("weread.jobs")


@dataclass
class ArchiveJob:
    job_id: str
    article_ids: list[str]
    include_images: bool
    status: str = "queued"
    progress: int = 0
    completed: int = 0
    total: int = 0
    book_id: str = ""
    connection_expired: bool = False
    processed: int = 0
    items: list[dict[str, str]] = field(default_factory=list)
    failures: list[tuple[str, str]] = field(default_factory=list)
    warnings: list[tuple[str, str]] = field(default_factory=list)
    zip_path: str | None = None
    fetch_article: Callable[[ListedArticle], str] | None = field(default=None, repr=False)
    created_at: float = field(default_factory=time.time, repr=False)


class JobRegistry:
    def __init__(self, archive_service: ArchiveService, fetch_article: Callable[[ListedArticle], str], max_jobs: int = 4):
        self.archive_service = archive_service
        self.fetch_article = fetch_article
        self.executor = ThreadPoolExecutor(max_workers=max_jobs, thread_name_prefix="weread-archive")
        self._lock = threading.RLock()

    def start(self, session: Session, book_id: str, article_ids: list[str], include_images: bool, fetch_article: Callable[[ListedArticle], str] | None = None) -> ArchiveJob:
        snapshot = {
            str(article.get("original_id")): ListedArticle(
                title=str(article.get("title") or "未命名文章"),
                summary=str(article.get("summary") or ""),
                account_name=str(article.get("account_name") or ""),
                original_id=str(article.get("original_id") or ""),
                published_at=int(article.get("published_at") or 0),
            )
            for article in session.articles.get(book_id, [])
            if article.get("original_id")
        }
        selected = [snapshot[item_id] for item_id in dict.fromkeys(article_ids) if item_id in snapshot]
        if not selected:
            raise ValueError("请选择当前列表中的文章")
        with self._lock:
            active = sum(job.status in {"queued", "running"} for job in session.jobs.values())
            if active >= self.archive_service.settings.max_job_count:
                raise ValueError("归档任务过多，请等待当前任务完成")
            callback = fetch_article or self.fetch_article
            job = ArchiveJob(uuid.uuid4().hex, [item.original_id for item in selected], include_images, total=len(selected), fetch_article=callback)
            job.book_id = book_id
            job.items = [{"article_id": item.original_id, "title": item.title, "status": "queued", "reason": ""} for item in selected]
            session.jobs[job.job_id] = job
        self.executor.submit(self._run, session, job, selected, book_id, callback)
        LOGGER.info(
            "archive_job_queued job_id=%s total=%d include_images=%s",
            job.job_id,
            job.total,
            job.include_images,
        )
        return job

    def get(self, session: Session, job_id: str) -> ArchiveJob | None:
        job = session.jobs.get(job_id)
        if job and job.zip_path:
            path = Path(job.zip_path)
            try:
                expired = time.time() - path.stat().st_mtime > self.archive_service.settings.artifact_ttl_seconds
            except OSError:
                expired = True
            if expired:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
                job.zip_path = None
        return job

    def retry_failed(self, session: Session, job_id: str, fetch_article: Callable[[ListedArticle], str] | None = None) -> ArchiveJob:
        previous = self.get(session, job_id)
        if previous is None:
            raise KeyError(job_id)
        if previous.fetch_article is None:
            raise ValueError("任务缺少文章请求上下文")
        return self.start(session, self._book_for(session, previous), [item_id for item_id, _ in previous.failures], previous.include_images, fetch_article or previous.fetch_article)

    def _book_for(self, session: Session, job: ArchiveJob) -> str:
        if job.book_id in session.articles:
            return job.book_id
        for book_id, articles in session.articles.items():
            ids = {str(item.get("original_id")) for item in articles}
            if set(job.article_ids).issubset(ids):
                return book_id
        raise ValueError("文章列表已过期，请重新加载")

    def _run(self, session: Session, job: ArchiveJob, articles: list[ListedArticle], book_id: str, fetch_article: Callable[[ListedArticle], str]) -> None:
        with self._lock:
            job.status = "running"
        started_at = time.monotonic()
        LOGGER.info("archive_job_started job_id=%s total=%d", job.job_id, job.total)
        def update(value: int) -> None:
            with self._lock:
                job.progress = value
        def update_item(article_id: str, status: str, reason: str) -> None:
            with self._lock:
                for item in job.items:
                    if item["article_id"] == article_id:
                        item.update(status=status, reason=reason)
                        break
                job.completed = sum(item["status"] == "completed" for item in job.items)
                job.processed = sum(item["status"] in {"completed", "failed"} for item in job.items)
        try:
            def fetch_with_status(article: ListedArticle) -> str:
                try:
                    return fetch_article(article)
                except Exception as exc:
                    if getattr(exc, "kind", "") == "expired":
                        with self._lock:
                            job.connection_expired = True
                    raise
            result: ArchiveResult = self.archive_service.archive(
                articles,
                fetch_with_status,
                job.include_images,
                update,
                book_id,
                item_callback=update_item,
                job_id=job.job_id,
            )
        except Exception as exc:
            # Packaging failures must also terminate polling and leave a retryable task.
            with self._lock:
                job.status = "failed"
                job.failures = [(item.original_id, "无法生成下载文件，请重试") for item in articles]
                for item in job.items:
                    item.update(status="failed", reason="无法生成下载文件，请重试")
                job.completed = 0
                job.processed = job.total
            LOGGER.exception(
                "archive_job_failed job_id=%s stage=package error_type=%s elapsed_ms=%d",
                job.job_id,
                getattr(exc, "kind", None) or exc.__class__.__name__,
                round((time.monotonic() - started_at) * 1000),
            )
            return
        with self._lock:
            job.status = "completed" if not result.failures else "failed"
            job.progress = 100
            job.completed = result.completed
            job.failures = result.failures
            job.warnings = result.warnings
            job.zip_path = str(result.zip_path)
        LOGGER.info(
            "archive_job_finished job_id=%s status=%s completed=%d failed=%d warnings=%d elapsed_ms=%d",
            job.job_id,
            job.status,
            job.completed,
            len(job.failures),
            len(job.warnings),
            round((time.monotonic() - started_at) * 1000),
        )
