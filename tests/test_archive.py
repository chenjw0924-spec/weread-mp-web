import io
import time
import zipfile

from app.archive import ArchiveService
from app.auth import AuthManager
from app.config import Settings
from app.jobs import JobRegistry
from app.weread import ListedArticle


def listed(article_id, title):
    return ListedArticle(title, "摘要", "测试号", article_id, 1767225600)


def parsed_html(title, image_url=""):
    image = f'<p><img data-src="{image_url}" alt="配图"></p>' if image_url else ""
    return f'<meta property="og:title" content="{title}"><div id="js_content"><p>正文 {title}</p>{image}</div>'


def test_archive_creates_markdown_and_optional_image_entries(tmp_path):
    article = listed("article-1", "第一篇")
    image_url = "https://mmbiz.qpic.cn/mmbiz_png/a/640?wx_fmt=png"
    service = ArchiveService(temp_dir=tmp_path, image_fetcher=lambda url: b"PNG")

    result = service.archive(
        [article], lambda item: parsed_html(item.title, image_url), include_images=True
    )

    with zipfile.ZipFile(result.zip_path) as archive:
        names = archive.namelist()
        markdown = archive.read("第一篇.md").decode()
        assert names == ["第一篇.md", "images/第一篇-01.png"]
        assert "![配图](images/第一篇-01.png)" in markdown
        assert archive.read("images/第一篇-01.png") == b"PNG"


def test_failed_image_download_preserves_remote_link_and_records_warning(tmp_path):
    article = listed("article-1", "第一篇")
    image_url = "https://mmbiz.qpic.cn/mmbiz_png/a/640?wx_fmt=png"
    service = ArchiveService(
        temp_dir=tmp_path,
        image_fetcher=lambda url: (_ for _ in ()).throw(RuntimeError("拒绝")),
    )

    result = service.archive(
        [article], lambda item: parsed_html(item.title, image_url), include_images=True
    )

    with zipfile.ZipFile(result.zip_path) as archive:
        markdown = archive.read("第一篇.md").decode()
    assert image_url in markdown
    assert result.warnings == [("article-1", "图片下载失败 1 个")]


def test_article_failure_does_not_discard_success_and_progress_is_monotonic(tmp_path):
    articles = [listed("ok", "成功"), listed("bad", "失败")]
    service = ArchiveService(temp_dir=tmp_path)
    progress = []

    def fetch(item):
        if item.original_id == "bad":
            raise RuntimeError("上游错误")
        return parsed_html(item.title)

    result = service.archive(articles, fetch, include_images=False, progress_callback=progress.append)

    assert result.completed == 1
    assert result.failures == [("bad", "上游错误")]
    assert progress == [0, 50, 100]
    with zipfile.ZipFile(result.zip_path) as archive:
        assert archive.namelist() == ["成功.md"]


def make_settings():
    return Settings(app_password="test-password-long", max_job_count=2)


def wait_done(registry, session, job_id):
    for _ in range(100):
        job = registry.get(session, job_id)
        if job.status in {"completed", "failed"}:
            return job
        time.sleep(0.01)
    raise AssertionError("job did not finish")


def test_job_registry_retries_only_failed_ids_and_denies_cross_session_access(tmp_path):
    auth = AuthManager(make_settings())
    token1 = auth.login("test-password-long", "one", now=1)
    token2 = auth.login("test-password-long", "two", now=1)
    session1 = auth.get_session(token1, now=1)
    session2 = auth.get_session(token2, now=1)
    session1.articles["MP_WXS_1"] = [
        {"title": "成功", "summary": "", "account_name": "号", "original_id": "ok", "published_at": 1767225600},
        {"title": "失败", "summary": "", "account_name": "号", "original_id": "bad", "published_at": 1767225600},
    ]
    service = ArchiveService(temp_dir=tmp_path)
    attempts = []

    def fetch(item):
        attempts.append(item.original_id)
        if item.original_id == "bad" and attempts.count("bad") == 1:
            raise RuntimeError("暂时失败")
        return parsed_html(item.title)

    registry = JobRegistry(service, fetch_article=fetch, max_jobs=2)
    job = registry.start(session1, "MP_WXS_1", ["ok", "bad"], include_images=False)
    finished = wait_done(registry, session1, job.job_id)
    assert finished.failures == [("bad", "暂时失败")]
    assert registry.get(session2, job.job_id) is None

    retry = registry.retry_failed(session1, job.job_id)
    retried = wait_done(registry, session1, retry.job_id)
    assert retried.failures == []
    assert attempts == ["ok", "bad", "bad"]
