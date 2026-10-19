from pathlib import Path
from datetime import date
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
from .weread_login import LoginClient, LoginProtocolError, QRLogin


SESSION_COOKIE = "weread_session"
ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "app" / "static"
TEMPLATES = ROOT / "app" / "templates"


class CookieRequest(BaseModel):
    cookie: str = Field(min_length=1, max_length=16_384)


class OtpRequest(BaseModel):
    code: Any


class JobRequest(BaseModel):
    book_id: str = Field(min_length=1, max_length=120)
    article_ids: list[str] = Field(min_length=1, max_length=100)
    include_images: bool = False


def create_app(
    settings: Settings | None = None,
    weread_client: Any | None = None,
    artifact_dir: str | Path | None = None,
    qr_client_factory: Any | None = None,
) -> FastAPI:
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
    app.state.qr_client_factory = qr_client_factory

    @app.middleware("http")
    async def no_store_api(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        elif request.url.path == "/" or request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    @app.middleware("http")
    async def anonymous_session(request: Request, call_next):
        if request.url.path != "/" and not request.url.path.startswith("/api/"):
            return await call_next(request)
        token = request.cookies.get(SESSION_COOKIE)
        session = auth.get_session(token)
        created = False
        if session is None:
            token, session = auth.create_anonymous_session()
            created = True
        request.state.weread_session = session
        request.state.weread_session_token = token
        response = await call_next(request)
        if created:
            response.set_cookie(
                SESSION_COOKIE,
                token,
                max_age=settings.session_ttl_seconds,
                httponly=True,
                secure=settings.cookie_secure,
                samesite="strict",
                path="/",
            )
        return response

    def session_for(request: Request) -> Session:
        session = getattr(request.state, "weread_session", None)
        if session is None:
            session = auth.get_session(request.cookies.get(SESSION_COOKIE))
        if session is None:
            raise HTTPException(503, "当前会话不可用，请刷新页面重试")
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
        _cancel_qr(session)
        try:
            accounts = client.validate_cookie(payload.cookie)
        except WeReadError as exc:
            raise HTTPException(400, detail={"message": str(exc), "kind": exc.kind})
        session.connected_cookie = payload.cookie
        session.accounts = {account.book_id: {"book_id": account.book_id, "title": account.title, "cover_url": account.cover_url} for account in accounts}
        session.articles.clear()
        session.coverage.clear()
        return {"connected": True, "accounts": list(session.accounts.values())}

    @app.post("/api/weread/qr/start")
    def qr_start(request: Request) -> dict[str, Any]:
        session = session_for(request)
        if session.connected_cookie:
            raise HTTPException(409, "微信读书已经连接")
        if session.qr_login and session.qr_login_id:
            current = session.qr_login.status()
            if current["state"] in {"waiting", "scanned", "otp_required"}:
                return _qr_payload(session.qr_login_id, current)

        import secrets

        login_id = secrets.token_urlsafe(24)

        def save_cookie(cookie: str) -> None:
            accounts = client.validate_cookie(cookie)
            session.connected_cookie = cookie
            session.accounts = {
                account.book_id: {
                    "book_id": account.book_id,
                    "title": account.title,
                    "cover_url": account.cover_url,
                }
                for account in accounts
            }
            session.articles.clear()
            session.coverage.clear()

        login = QRLogin(
            save_cookie,
            client_factory=app.state.qr_client_factory or LoginClient,
        )
        session.qr_login_id = login_id
        session.qr_login = login
        try:
            status = login.start()
        except (LoginProtocolError, OSError) as exc:
            session.qr_login = None
            session.qr_login_id = None
            raise HTTPException(503, {"message": "暂时无法生成微信读书二维码，请稍后重试。", "kind": "qr_unavailable"}) from exc
        return _qr_payload(login_id, status)

    @app.get("/api/weread/qr/{login_id}/status")
    def qr_status(login_id: str, request: Request) -> dict[str, Any]:
        session = session_for(request)
        login = _session_qr(session, login_id)
        status = login.status()
        result = {"login_id": login_id, **status, "connected": status["state"] == "success"}
        if status["state"] == "success":
            result["accounts"] = list(session.accounts.values())
        return result

    @app.post("/api/weread/qr/{login_id}/verify")
    def qr_verify(login_id: str, payload: OtpRequest, request: Request) -> dict[str, Any]:
        session = session_for(request)
        login = _session_qr(session, login_id)
        try:
            status = login.submit_otp(payload.code)
        except LoginProtocolError as exc:
            kind = str(exc)
            messages = {
                "invalid_otp": "请输入手机显示的 4 位数字验证码。",
                "otp_expired": "二维码已过期，请重新生成。",
                "otp_not_required": "当前扫码任务不需要验证码，请等待状态更新。",
                "otp_verification_pending": "验证码正在验证，请稍候。",
            }
            raise HTTPException(400, detail={"message": messages.get(kind, "验证码提交失败，请重新扫码。"), "kind": kind}) from exc
        result = {"login_id": login_id, **status, "connected": status["state"] == "success"}
        if status["state"] == "success":
            result["accounts"] = list(session.accounts.values())
        return result

    @app.get("/api/weread/qr/{login_id}/image")
    def qr_image(login_id: str, request: Request) -> Response:
        session = session_for(request)
        login = _session_qr(session, login_id)
        image = login.image()
        if not image:
            raise HTTPException(410, "二维码已失效，请重新生成")
        return Response(content=image, media_type="image/svg+xml")

    @app.post("/api/weread/qr/{login_id}/cancel", status_code=204)
    def qr_cancel(login_id: str, request: Request) -> Response:
        session = session_for(request)
        _session_qr(session, login_id).cancel()
        session.qr_login = None
        session.qr_login_id = None
        return Response(status_code=204)

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
    def articles(
        book_id: str,
        request: Request,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> dict[str, Any]:
        session = session_for(request)
        if not session.connected_cookie or book_id not in session.accounts:
            raise HTTPException(409, "请先连接微信读书")
        try:
            if from_date is None and to_date is None:
                from_date, to_date = _default_article_range()
            from_date, to_date = WeReadClient._validate_date_range(from_date, to_date)
            date_search = bool(from_date or to_date)
            limit = settings.max_archive_articles if date_search else settings.default_list_articles
            result = client.list_articles(
                session.connected_cookie,
                book_id,
                limit,
                from_date=from_date,
                to_date=to_date,
            )
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
        if not session.connected_cookie:
            raise HTTPException(409, "请先连接微信读书")
        try:
            previous = jobs.get(session, job_id)
            if previous is None:
                raise KeyError(job_id)
            book_id = jobs._book_for(session, previous)
            cookie = session.connected_cookie
            callback = lambda item: client.get_article_html(cookie, book_id, item.original_id)
            return _job_dict(jobs.retry_failed(session, job_id, callback))
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


def _default_article_range(today: date | None = None) -> tuple[str, str]:
    current = today or date.today()
    try:
        year_ago = current.replace(year=current.year - 1)
    except ValueError:
        year_ago = current.replace(year=current.year - 1, month=2, day=28)
    return year_ago.isoformat(), current.isoformat()


def _cancel_qr(session: Session) -> None:
    if session.qr_login:
        session.qr_login.cancel()
    session.qr_login = None
    session.qr_login_id = None


def _session_qr(session: Session, login_id: str) -> Any:
    if not session.qr_login or session.qr_login_id != login_id:
        raise HTTPException(404, "扫码任务不存在")
    return session.qr_login


def _qr_payload(login_id: str, status: dict[str, Any]) -> dict[str, Any]:
    return {
        "login_id": login_id,
        "state": status["state"],
        "message": status.get("message", ""),
        "expires_at": status.get("expires_at"),
        "image_url": f"/api/weread/qr/{login_id}/image",
        "status_url": f"/api/weread/qr/{login_id}/status",
    }


def _article_dict(item: Any) -> dict[str, Any]:
    return {"title": item.title, "summary": item.summary, "account_name": item.account_name, "original_id": item.original_id, "published_at": item.published_at, "date": item.date, "read_count": item.read_count, "like_count": item.like_count}


def _job_dict(job: Any) -> dict[str, Any]:
    return {"job_id": job.job_id, "status": job.status, "progress": job.progress, "completed": job.completed, "processed": job.processed, "connection_expired": job.connection_expired, "total": job.total, "items": [dict(item) for item in job.items], "failures": [{"article_id": item, "reason": reason} for item, reason in job.failures], "warnings": [{"article_id": item, "reason": reason} for item, reason in job.warnings], "download_ready": bool(job.zip_path)}


def _remove_file(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


app = create_app()
