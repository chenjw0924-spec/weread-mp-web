from pathlib import Path
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

    def validate_cookie(self, cookie):
        self.cookie = cookie
        return [Account("MP_WXS_1", "测试公众号")]

    def list_articles(self, cookie, book_id, max_articles=None):
        assert cookie == self.cookie
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
    response = client.post("/api/login", json={"password": "test-password-long"})
    assert response.status_code == 200


def test_public_health_and_password_protected_routes(tmp_path):
    app = create_app(settings=settings(tmp_path), weread_client=FakeWeRead(), artifact_dir=tmp_path)
    client = TestClient(app)
    assert client.get("/healthz").json() == {"ok": True}
    assert client.get("/api/session").status_code == 401
    assert client.post("/api/login", json={"password": "wrong"}).status_code == 401
    login(client)
    assert client.get("/api/session").json()["authenticated"] is True


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
    assert client.get("/api/session").json()["connected"] is True


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
    assert "微信读书公众号归档" in response.text


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
