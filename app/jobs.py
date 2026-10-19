from dataclasses import dataclass, field
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from .archive import ArchiveResult, ArchiveService
from .auth import Session
from .weread import ListedArticle


@dataclass
class ArchiveJob:
    job_id: str
    article_ids: list[str]
    include_images: bool
    status: str = "queued"
    progress: int = 0
    completed: int = 0
    total: int = 0
    failures: list[tuple[str, str]] = field(default_factory=list)
    warnings: list[tuple[str, str]] = field(default_factory=list)
    zip_path: str | None = None


class JobRegistry:
    def __init__(self, archive_service: ArchiveService, fetch_article: Callable[[ListedArticle], str], max_jobs: int = 4):
        self.archive_service = archive_service
        self.fetch_article = fetch_article
        self.executor = ThreadPoolExecutor(max_workers=max_jobs, thread_name_prefix="weread-archive")
        self._lock = threading.RLock()

    def start(self, session: Session, book_id: str, article_ids: list[str], include_images: bool) -> ArchiveJob:
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
            job = ArchiveJob(uuid.uuid4().hex, [item.original_id for item in selected], include_images, total=len(selected))
            session.jobs[job.job_id] = job
        self.executor.submit(self._run, session, job, selected, book_id)
        return job

    def get(self, session: Session, job_id: str) -> ArchiveJob | None:
        return session.jobs.get(job_id)

    def retry_failed(self, session: Session, job_id: str) -> ArchiveJob:
        previous = self.get(session, job_id)
        if previous is None:
            raise KeyError(job_id)
        return self.start(session, self._book_for(session, previous), [item_id for item_id, _ in previous.failures], previous.include_images)

    def _book_for(self, session: Session, job: ArchiveJob) -> str:
        for book_id, articles in session.articles.items():
            ids = {str(item.get("original_id")) for item in articles}
            if set(job.article_ids).issubset(ids):
                return book_id
        raise ValueError("文章列表已过期，请重新加载")

    def _run(self, session: Session, job: ArchiveJob, articles: list[ListedArticle], book_id: str) -> None:
        with self._lock:
            job.status = "running"
        def update(value: int) -> None:
            with self._lock:
                job.progress = value
        result: ArchiveResult = self.archive_service.archive(articles, self.fetch_article, job.include_images, update, book_id)
        with self._lock:
            job.status = "completed" if not result.failures else "failed"
            job.progress = 100
            job.completed = result.completed
            job.failures = result.failures
            job.warnings = result.warnings
            job.zip_path = str(result.zip_path)
