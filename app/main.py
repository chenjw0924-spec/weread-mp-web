from pathlib import Path
import os
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from .archive import ArchiveService
from .auth import AuthManager, Session
from .config import Settings
from .jobs import JobRegistry
from .weread import WeReadClient, WeReadError


SESSION_COOKIE = "weread_session"
ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "app" / "static"
TEMPLATES = ROOT / "app" / "templates"


class LoginRequest(BaseModel):
    password: str = Field(max_length=512)


class CookieRequest(BaseModel):
    cookie: str = Field(min_length=1, max_length=16_384)


class JobRequest(BaseModel):
    book_id: str = Field(min_length=1, max_length=120)
    article_ids: list[str] = Field(min_length=1, max_length=100)
    include_images: bool = False


def create_app(settings: Settings | None = None, weread_client: Any | None = None, artifact_dir: str | Path | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    client = weread_client or WeReadClient(settings)
    auth = AuthManager(settings)
    archive = ArchiveService(settings, temp_dir=artifact_dir)
    jobs = JobRegistry(archive, fetch_article=lambda _: "")
    app = FastAPI(title="微信读书公众号归档")
    app.state.settings = settings
    app.state.auth = auth
    app.state.weread = client
    app.state.jobs = jobs

    @app.middleware("http")
    async def no_store_api(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def session_for(request: Request) -> Session:
        session = auth.get_session(request.cookies.get(SESSION_COOKIE))
        if session is None:
            raise HTTPException(401, "请先输入访问密码")
        return session

    @app.get("/healthz")
    def healthz() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        path = TEMPLATES / "index.html"
        if not path.exists():
            return HTMLResponse("<h1>微信读书公众号归档</h1>")
        return HTMLResponse(path.read_text(encoding="utf-8"))

    @app.get("/static/{filename}")
    def static_file(filename: str) -> FileResponse:
        path = (STATIC / filename).resolve()
        if STATIC not in path.parents or not path.is_file():
            raise HTTPException(404)
        return FileResponse(path)

    @app.post("/api/login")
    def login(payload: LoginRequest, request: Request, response: Response) -> dict[str, bool]:
        token = auth.login(payload.password, request.client.host if request.client else "unknown")
        if token is None:
            raise HTTPException(401, "访问密码不正确或尝试过于频繁")
        response.set_cookie(SESSION_COOKIE, token, max_age=settings.session_ttl_seconds, httponly=True, secure=settings.cookie_secure, samesite="strict", path="/")
        return {"authenticated": True}

    @app.post("/api/logout", status_code=204)
    def logout(request: Request) -> Response:
        auth.logout(request.cookies.get(SESSION_COOKIE))
        response = Response(status_code=204)
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response

    @app.get("/api/session")
    def session_state(request: Request) -> dict[str, Any]:
        session = session_for(request)
        return {"authenticated": True, "connected": bool(session.connected_cookie), "account_count": len(session.accounts)}

    @app.post("/api/weread/connect")
    def connect(payload: CookieRequest, request: Request) -> dict[str, Any]:
        session = session_for(request)
        try:
            accounts = client.validate_cookie(payload.cookie)
        except WeReadError as exc:
            raise HTTPException(400, detail={"message": str(exc), "kind": exc.kind})
        session.connected_cookie = payload.cookie
        session.accounts = {account.book_id: {"book_id": account.book_id, "title": account.title, "cover_url": account.cover_url} for account in accounts}
        session.articles.clear()
        session.coverage.clear()
        return {"connected": True, "accounts": list(session.accounts.values())}

    @app.delete("/api/weread/connect", status_code=204)
    def disconnect(request: Request) -> Response:
        session_for(request)
        auth.disconnect_wechat(request.cookies.get(SESSION_COOKIE))
        return Response(status_code=204)

    @app.get("/api/accounts")
    def accounts(request: Request) -> dict[str, Any]:
        session = session_for(request)
        if not session.connected_cookie:
            raise HTTPException(409, "请先连接微信读书")
        return {"accounts": list(session.accounts.values())}

    @app.get("/api/accounts/{book_id}/articles")
    def articles(book_id: str, request: Request) -> dict[str, Any]:
        session = session_for(request)
        if not session.connected_cookie or book_id not in session.accounts:
            raise HTTPException(409, "请先连接微信读书")
        try:
            result = client.list_articles(session.connected_cookie, book_id, settings.max_archive_articles)
        except WeReadError as exc:
            raise HTTPException(400, detail={"message": str(exc), "kind": exc.kind})
        session.articles[book_id] = [_article_dict(item) for item in result.articles]
        session.coverage[book_id] = {"earliest_date": result.earliest_date, "latest_date": result.latest_date, "pages": result.pages, "stop_reason": result.stop_reason}
        return {"articles": session.articles[book_id], **session.coverage[book_id]}

    @app.post("/api/jobs")
    def create_job(payload: JobRequest, request: Request) -> dict[str, Any]:
        session = session_for(request)
        if not session.connected_cookie:
            raise HTTPException(409, "请先连接微信读书")
        cookie = session.connected_cookie
        callback = lambda item: client.get_article_html(cookie, payload.book_id, item.original_id)
        try:
            job = jobs.start(session, payload.book_id, payload.article_ids, payload.include_images, callback)
        except (ValueError, KeyError) as exc:
            raise HTTPException(400, str(exc))
        return _job_dict(job)

    @app.get("/api/jobs/{job_id}")
    def job_state(job_id: str, request: Request) -> dict[str, Any]:
        session = session_for(request)
        job = jobs.get(session, job_id)
        if job is None:
            raise HTTPException(404, "任务不存在")
        return _job_dict(job)

    @app.post("/api/jobs/{job_id}/retry")
    def retry_job(job_id: str, request: Request) -> dict[str, Any]:
        session = session_for(request)
        try:
            return _job_dict(jobs.retry_failed(session, job_id))
        except (KeyError, ValueError) as exc:
            raise HTTPException(400, str(exc))

    @app.get("/api/jobs/{job_id}/download")
    def download_job(job_id: str, request: Request, background_tasks: BackgroundTasks) -> FileResponse:
        session = session_for(request)
        job = jobs.get(session, job_id)
        if job is None or not job.zip_path:
            raise HTTPException(404, "归档文件尚未生成")
        path = Path(job.zip_path)
        if not path.is_file():
            raise HTTPException(404, "归档文件已过期")
        background_tasks.add_task(_remove_file, path)
        return FileResponse(path, media_type="application/zip", filename="weread-articles.zip", headers={"Cache-Control": "no-store"})

    return app


def _article_dict(item: Any) -> dict[str, Any]:
    return {"title": item.title, "summary": item.summary, "account_name": item.account_name, "original_id": item.original_id, "published_at": item.published_at, "date": item.date, "read_count": item.read_count, "like_count": item.like_count}


def _job_dict(job: Any) -> dict[str, Any]:
    return {"job_id": job.job_id, "status": job.status, "progress": job.progress, "completed": job.completed, "total": job.total, "failures": [{"article_id": item, "reason": reason} for item, reason in job.failures], "warnings": [{"article_id": item, "reason": reason} for item, reason in job.warnings], "download_ready": bool(job.zip_path)}


def _remove_file(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


app = create_app() if os.environ.get("APP_PASSWORD") else None
