from pathlib import Path
from datetime import date
import io
import time
import zipfile

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.weread import Account, ArticleListResult, ListedArticle


class FakeWeRead:
    def __init__(self):
        self.cookie = None
        self.list_calls = []

    def validate_cookie(self, cookie):
        self.cookie = cookie
        return [Account("MP_WXS_1", "测试公众号")]

    def list_articles(self, cookie, book_id, max_articles=None, from_date=None, to_date=None):
        assert cookie == self.cookie
        self.list_calls.append((max_articles, from_date, to_date))
        return ArticleListResult(
            [ListedArticle("文章一", "摘要", "测试公众号", "a1", 1767225600)],
            pages=2,
            stop_reason="empty_page",
            earliest_date="2026-01-01",
            latest_date="2026-01-01",
        )

    def get_article_html(self, cookie, book_id, original_id):
        return '<div id="js_content"><p>正文</p></div>'


def settings(tmp_path):
    return Settings(app_password="test-password-long", cookie_secure=False, artifact_ttl_seconds=60)


def login(client):
    response = client.get("/api/session")
    assert response.status_code == 200


def test_public_health_and_anonymous_session_routes(tmp_path):
    app = create_app(settings=settings(tmp_path), weread_client=FakeWeRead(), artifact_dir=tmp_path)
    client = TestClient(app)
    assert client.get("/healthz").json() == {"ok": True}
    session = client.get("/api/session")
    assert session.status_code == 200
    assert session.json()["authenticated"] is True


def test_session_is_created_without_an_access_password(tmp_path):
    app = create_app(settings=Settings(app_password="", cookie_secure=False), weread_client=FakeWeRead(), artifact_dir=tmp_path)
    client = TestClient(app)

    response = client.get("/api/session")

    assert response.status_code == 200
    assert response.json() == {"authenticated": True, "connected": False, "account_count": 0}
    assert "weread_session=" in response.headers["set-cookie"]


def test_connect_does_not_echo_cookie_and_article_list_reports_coverage(tmp_path):
    fake = FakeWeRead()
    app = create_app(settings=settings(tmp_path), weread_client=fake, artifact_dir=tmp_path)
    client = TestClient(app)
    login(client)
    cookie = "wr_skey=secret-value; wr_vid=private"
    response = client.post("/api/weread/connect", json={"cookie": cookie})
    assert response.status_code == 200
    assert "secret-value" not in response.text
    assert client.get("/api/accounts").json()["accounts"][0]["title"] == "测试公众号"

    response = client.get("/api/accounts/MP_WXS_1/articles")
    assert response.status_code == 200
    assert response.json()["earliest_date"] == "2026-01-01"
    assert response.json()["stop_reason"] == "empty_page"
    today = date.today()
    try:
        year_ago = today.replace(year=today.year - 1)
    except ValueError:
        year_ago = today.replace(year=today.year - 1, month=2, day=28)
    assert fake.list_calls[-1] == (100, year_ago.isoformat(), today.isoformat())
    response = client.get("/api/accounts/MP_WXS_1/articles?from_date=2025-01-01&to_date=2026-12-31")
    assert response.status_code == 200
    assert fake.list_calls[-1] == (100, "2025-01-01", "2026-12-31")
    assert client.get("/api/session").json()["connected"] is True


def test_article_date_range_rejects_invalid_or_reversed_dates(tmp_path):
    app = create_app(settings=settings(tmp_path), weread_client=FakeWeRead(), artifact_dir=tmp_path)
    client = TestClient(app)
    login(client)
    client.post("/api/weread/connect", json={"cookie": "wr_skey=s; wr_vid=v"})

    invalid = client.get("/api/accounts/MP_WXS_1/articles?from_date=2026-02-31")
    reversed_dates = client.get("/api/accounts/MP_WXS_1/articles?from_date=2026-10-01&to_date=2026-01-01")

    assert invalid.status_code == 400
    assert invalid.json()["detail"]["kind"] == "invalid_request"
    assert reversed_dates.status_code == 400
    assert reversed_dates.json()["detail"]["kind"] == "invalid_request"


def test_disconnect_clears_connection_state(tmp_path):
    app = create_app(settings=settings(tmp_path), weread_client=FakeWeRead(), artifact_dir=tmp_path)
    client = TestClient(app)
    login(client)
    client.post("/api/weread/connect", json={"cookie": "wr_skey=s; wr_vid=v"})
    assert client.delete("/api/weread/connect").status_code == 204
    assert client.get("/api/accounts").status_code == 409


def test_static_ui_is_available(tmp_path):
    app = create_app(settings=settings(tmp_path), weread_client=FakeWeRead(), artifact_dir=tmp_path)
    response = TestClient(app).get("/")
    assert response.status_code == 200
    assert "微信读书文章归档" in response.text


def test_login_page_and_script_require_cache_revalidation(tmp_path):
    app = create_app(settings=settings(tmp_path), weread_client=FakeWeRead(), artifact_dir=tmp_path)
    client = TestClient(app)
    assert client.get('/').headers['Cache-Control'] == 'no-cache'
    assert client.get('/static/app.js').headers['Cache-Control'] == 'no-cache'


def test_job_export_poll_and_download_cleanup(tmp_path):
    fake = FakeWeRead()
    app = create_app(settings=settings(tmp_path), weread_client=fake, artifact_dir=tmp_path)
    client = TestClient(app)
    login(client)
    client.post("/api/weread/connect", json={"cookie": "wr_skey=s; wr_vid=v"})
    client.get("/api/accounts/MP_WXS_1/articles")
    response = client.post("/api/jobs", json={"book_id": "MP_WXS_1", "article_ids": ["a1"], "include_images": False})
    assert response.status_code == 200
    job_id = response.json()["job_id"]
    for _ in range(30):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["download_ready"]:
            break
        time.sleep(0.02)
    download = client.get(f"/api/jobs/{job_id}/download")
    assert download.status_code == 200
    with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
        assert "文章一.md" in archive.namelist()
    time.sleep(0.05)
    assert not list(tmp_path.glob("archive-*.zip"))


def test_job_exposes_real_article_states_and_image_warnings(tmp_path):
    fake = FakeWeRead()
    app = create_app(settings=settings(tmp_path), weread_client=fake, artifact_dir=tmp_path)
    client = TestClient(app)
    login(client)
    client.post("/api/weread/connect", json={"cookie": "wr_skey=s; wr_vid=v"})
    client.get("/api/accounts/MP_WXS_1/articles")
    job = client.post("/api/jobs", json={"book_id": "MP_WXS_1", "article_ids": ["a1"]}).json()
    for _ in range(50):
        job = client.get(f"/api/jobs/{job['job_id']}").json()
        if job["status"] in {"completed", "failed"}:
            break
        time.sleep(0.01)
    assert job["items"] == [{"article_id": "a1", "title": "文章一", "status": "completed", "reason": ""}]
    assert job["processed"] == 1
    assert job["completed"] == 1
    assert job["warnings"] == []


def test_failed_retry_uses_renewed_connection_and_reports_item_failure(tmp_path):
    class RenewedWeRead(FakeWeRead):
        def get_article_html(self, cookie, book_id, original_id):
            if cookie == 'old-cookie':
                raise RuntimeError('expired')
            assert cookie == 'new-cookie'
            return '<div id="js_content"><p>新连接正文</p></div>'
    app = create_app(settings=settings(tmp_path), weread_client=RenewedWeRead(), artifact_dir=tmp_path)
    client = TestClient(app)
    login(client)
    client.post('/api/weread/connect', json={'cookie': 'old-cookie'})
    client.get('/api/accounts/MP_WXS_1/articles')
    job = client.post('/api/jobs', json={'book_id': 'MP_WXS_1', 'article_ids': ['a1']}).json()
    for _ in range(50):
        job = client.get(f"/api/jobs/{job['job_id']}").json()
        if job['status'] == 'failed': break
        time.sleep(0.01)
    assert job['items'][0]['status'] == 'failed'
    client.post('/api/weread/connect', json={'cookie': 'new-cookie'})
    client.get('/api/accounts/MP_WXS_1/articles')
    retry = client.post(f"/api/jobs/{job['job_id']}/retry").json()
    for _ in range(50):
        retry = client.get(f"/api/jobs/{retry['job_id']}").json()
        if retry['status'] in {'completed', 'failed'}: break
        time.sleep(0.01)
    assert retry['status'] == 'completed'


def test_ui_job_reports_image_warning_and_keeps_remote_markdown_link(tmp_path):
    class ImagesWeRead(FakeWeRead):
        def get_article_html(self, cookie, book_id, original_id):
            return '<div id="js_content"><p>正文</p><img data-src="https://mmbiz.qpic.cn/test.png"></div>'
    app = create_app(settings=settings(tmp_path), weread_client=ImagesWeRead(), artifact_dir=tmp_path)
    def fail_image(url):
        raise RuntimeError('image unavailable')
    app.state.jobs.archive_service.image_fetcher = fail_image
    client = TestClient(app)
    login(client)
    client.post('/api/weread/connect', json={'cookie': 'test-cookie'})
    client.get('/api/accounts/MP_WXS_1/articles')
    job = client.post('/api/jobs', json={'book_id': 'MP_WXS_1', 'article_ids': ['a1'], 'include_images': True}).json()
    for _ in range(50):
        job = client.get(f"/api/jobs/{job['job_id']}").json()
        if job['status'] in {'completed', 'failed'}: break
        time.sleep(0.01)
    assert job['warnings'] == [{'article_id': 'a1', 'reason': '图片下载失败 1 个'}]
    assert job['items'][0]['status'] == 'completed'
    data = client.get(f"/api/jobs/{job['job_id']}/download").content
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert 'https://mmbiz.qpic.cn/test.png' in archive.read('文章一.md').decode()


def test_expiry_during_export_is_reported_for_reconnection(tmp_path):
    from app.weread import WeReadError
    class ExpiredWeRead(FakeWeRead):
        def get_article_html(self, cookie, book_id, original_id):
            raise WeReadError('WeRead login expired', 'expired')
    app = create_app(settings=settings(tmp_path), weread_client=ExpiredWeRead(), artifact_dir=tmp_path)
    client = TestClient(app)
    login(client)
    client.post('/api/weread/connect', json={'cookie': 'test-cookie'})
    client.get('/api/accounts/MP_WXS_1/articles')
    job = client.post('/api/jobs', json={'book_id': 'MP_WXS_1', 'article_ids': ['a1']}).json()
    for _ in range(50):
        job = client.get(f"/api/jobs/{job['job_id']}").json()
        if job['status'] in {'completed', 'failed'}: break
        time.sleep(0.01)
    assert job['connection_expired'] is True
    assert job['items'][0]['status'] == 'failed'
