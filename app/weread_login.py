"""In-memory WeRead web QR login.

The browser receives only an SVG QR image and coarse status. Login credentials
are converted to the existing Cookie header on the server and are never
included in API responses.
"""

import hashlib
import http.cookiejar
import io
import json
import re
import secrets
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

import qrcode
import qrcode.image.svg


WEREAD_ORIGIN = "https://weread.qq.com"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class LoginProtocolError(Exception):
    """A safe, user-facing login protocol error."""


class LoginWaitTimeout(LoginProtocolError):
    """The upstream long poll ended without a login result."""


def _nested_data(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("data")
    return value if isinstance(value, dict) else payload


def _login_flag(payload: dict[str, Any], name: str) -> bool:
    data = _nested_data(payload)
    return bool(data.get(name) or payload.get(name))


class LoginClient:
    """Small client for the current WeRead web login endpoints."""

    def __init__(self, opener: Any | None = None):
        self.jar = http.cookiejar.CookieJar()
        self._login_fingerprint: str | None = None
        if opener is None:
            class NoRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, *args: Any, **kwargs: Any) -> None:
                    return None

            opener = urllib.request.build_opener(
                NoRedirect, urllib.request.HTTPCookieProcessor(self.jar)
            )
        self.opener = opener

    def request(self, path: str, values: dict[str, Any] | None = None, timeout: float = 20) -> dict[str, Any]:
        body = urllib.parse.urlencode(values or {}).encode("utf-8")
        request = urllib.request.Request(
            WEREAD_ORIGIN + path,
            data=body,
            method="POST",
            headers={
                "Accept": "application/json, text/plain, */*",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Origin": WEREAD_ORIGIN,
                "Referer": WEREAD_ORIGIN + "/web/shelf",
                "User-Agent": USER_AGENT,
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        try:
            with self.opener.open(request, timeout=timeout) as response:
                raw = response.read(1024 * 1024 + 1)
        except (TimeoutError, socket.timeout) as exc:
            raise LoginWaitTimeout("waiting_timeout") from exc
        except urllib.error.HTTPError as exc:
            if exc.code in (408, 502, 504):
                raise LoginWaitTimeout("waiting_timeout") from exc
            raise LoginProtocolError("login_request_failed") from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)) or "timed out" in str(exc.reason).lower():
                raise LoginWaitTimeout("waiting_timeout") from exc
            raise LoginProtocolError("login_request_failed") from exc
        except OSError as exc:
            raise LoginProtocolError("login_request_failed") from exc
        if len(raw) > 1024 * 1024:
            raise LoginProtocolError("response_too_large")
        try:
            payload = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LoginProtocolError("invalid_response") from exc
        if not isinstance(payload, dict):
            raise LoginProtocolError("invalid_response")
        return payload

    def get_uid(self) -> str:
        payload = self.request("/web/login/getuid")
        value = _nested_data(payload).get("uid")
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,512}", value):
            raise LoginProtocolError("missing_login_uid")
        return value

    def get_login_info(self, uid: str) -> dict[str, Any]:
        # Keep one status request below Vercel Hobby's function timeout. The
        # browser calls this endpoint again when the upstream long poll times
        # out without a login result.
        return self.request("/web/login/getinfo", {"uid": uid}, timeout=8)

    def web_login(self, data: dict[str, Any]) -> dict[str, Any]:
        uid = data.get("uid")
        if self._login_fingerprint is None:
            supplied_fp = data.get("fp")
            self._login_fingerprint = supplied_fp if isinstance(supplied_fp, str) and supplied_fp else self._fingerprint()
        values = {
            "vid": data.get("vid"),
            "skey": data.get("skey"),
            "code": data.get("code"),
            "fp": self._login_fingerprint,
            "isAutoLogout": 0,
            "uid": uid,
            "pf": data.get("pf", 2),
        }
        if data.get("otp"):
            values["otp"] = data["otp"]
        return self.request("/web/login/weblogin", values, timeout=8)

    @staticmethod
    def _fingerprint() -> str:
        # The official page sends a stable opaque browser fingerprint. A
        # per-login hash avoids exposing any host-identifying information.
        return hashlib.sha256(secrets.token_bytes(32)).hexdigest()[:32]

    def cookie_from_login(self, payload: dict[str, Any]) -> str:
        data = _nested_data(payload)
        vid = data.get("vid") or data.get("webLoginVid")
        token = data.get("accessToken")
        if not data.get("succeed", True) or not str(vid).isdigit() or not isinstance(token, str):
            raise LoginProtocolError("missing_login_credentials")
        if not re.fullmatch(r"[A-Za-z0-9_~.+/=-]+", token):
            raise LoginProtocolError("invalid_login_credentials")
        values = {
            item.name: item.value
            for item in self.jar
            if item.domain.lstrip(".") in {"weread.qq.com", "qq.com"}
        }
        values.pop("wr_vid", None)
        values.pop("wr_skey", None)
        return "; ".join(
            [f"wr_vid={int(vid)}", f"wr_skey={token}"]
            + [f"{name}={value}" for name, value in values.items()]
        )


class QRLogin:
    """One expiring QR login advanced by the HTTP status request.

    Vercel can freeze a function as soon as a response is returned, so a
    background thread started by ``/qr/start`` cannot own the login state.
    Keeping the upstream poll inside ``/qr/{id}/status`` makes the flow work
    for both a single Uvicorn worker and a serverless function.
    """

    TERMINAL_STATES = {"success", "expired", "failed", "cancelled"}

    def __init__(
        self,
        save_cookie: Callable[[str], Any],
        client_factory: Callable[[], LoginClient] = LoginClient,
        ttl_seconds: int = 180,
    ):
        self.save_cookie = save_cookie
        self.client_factory = client_factory
        self.ttl_seconds = ttl_seconds
        self.lock = threading.RLock()
        self.done = threading.Event()
        self.generation = 0
        self.state = "idle"
        self.message = ""
        self.svg = b""
        self.confirm_url = ""
        self.expires_at = 0.0
        self._pending_login: dict[str, Any] | None = None
        self._pending_otp: str | None = None
        self._client: LoginClient | Any | None = None
        self._uid: str | None = None
        self._polling = False

    def status(self) -> dict[str, Any]:
        with self.lock:
            state = self.state
            if state in {"waiting", "scanned", "otp_required"} and time.time() >= self.expires_at:
                state = "expired"
                self.state = state
                self.message = "二维码已过期，请重新生成。"
                self.svg = b""
                self._pending_login = None
                self._pending_otp = None
                self.done.set()
            return {
                "state": state,
                "message": self.message,
                "expires_at": self.expires_at or None,
            }

    def image(self) -> bytes:
        with self.lock:
            return self.svg

    def start(self) -> dict[str, Any]:
        with self.lock:
            if self.state in {"waiting", "scanned", "otp_required"} and time.time() < self.expires_at:
                return self.status()
            client = self.client_factory()
            uid = client.get_uid()
            self.confirm_url = WEREAD_ORIGIN + "/web/confirm?" + urllib.parse.urlencode({"pf": 2, "uid": uid})
            qr = qrcode.make(
                self.confirm_url,
                image_factory=qrcode.image.svg.SvgPathImage,
                box_size=8,
                border=4,
            )
            buffer = io.BytesIO()
            qr.save(buffer)
            self.svg = buffer.getvalue()
            self.state = "waiting"
            self.message = "请使用微信扫描二维码。"
            self.expires_at = time.time() + self.ttl_seconds
            self._pending_login = None
            self._pending_otp = None
            self._client = client
            self._uid = uid
            self.generation += 1
            self.done.clear()
            return self.status()

    def cancel(self) -> None:
        with self.lock:
            if self.state in self.TERMINAL_STATES:
                return
            self.generation += 1
            self.state = "cancelled"
            self.message = "扫码连接已取消。"
            self.svg = b""
            self._pending_login = None
            self._pending_otp = None
            self._client = None
            self._uid = None
            self.done.set()

    def submit_otp(self, value: str) -> dict[str, Any]:
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}", value):
            raise LoginProtocolError("invalid_otp")
        with self.lock:
            if self.state == "expired" or time.time() >= self.expires_at:
                self.state = "expired"
                self.message = "二维码已过期，请重新生成。"
                self.svg = b""
                self._pending_login = None
                self._pending_otp = None
                self.done.set()
                raise LoginProtocolError("otp_expired")
            if self.state not in {"scanned", "otp_required"}:
                raise LoginProtocolError("otp_not_required")
            if self.state == "scanned" and not self._pending_login:
                self._pending_otp = value
                return self.status()
            if not self._pending_login or not self._client:
                raise LoginProtocolError("otp_not_required")
            generation = self.generation
            client = self._client
            pending = dict(self._pending_login)
            self._pending_otp = value
            self.state = "scanned"
            self.message = "正在验证手机验证码，请稍候。"
        self._verify_otp(client, pending, value, generation)
        return self.status()

    def wait_for_terminal(self, timeout: float | None = None) -> dict[str, Any]:
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            current = self.status()
            if current["state"] in self.TERMINAL_STATES:
                return current
            if deadline is not None and time.monotonic() >= deadline:
                return current
            self.poll()
            time.sleep(0.01)

    def poll(self) -> dict[str, Any]:
        """Advance one upstream login poll and return the coarse status."""
        with self.lock:
            if self.state != "waiting" or self._polling or not self._client or not self._uid:
                return self.status()
            if time.time() >= self.expires_at:
                self._expire_locked()
                return self.status()
            client = self._client
            uid = self._uid
            generation = self.generation
            self._polling = True
        try:
            try:
                response = client.get_login_info(uid)
            except (LoginWaitTimeout, TimeoutError):
                return self.status()
            except Exception:
                self._fail(generation, "暂时无法连接微信读书，请稍后重试。")
                return self.status()
            self._handle_login_response(client, uid, response, generation)
            return self.status()
        finally:
            with self.lock:
                self._polling = False

    def _handle_login_response(self, client: LoginClient, uid: str, response: dict[str, Any], generation: int) -> None:
        data = _nested_data(response)
        if data.get("vid") and data.get("skey") and data.get("code"):
            with self.lock:
                if generation != self.generation:
                    return
                self.state = "scanned"
                self.message = "已扫码，正在等待微信读书确认。"
            data = {**data, "uid": uid, "pf": data.get("pf", 2)}
            try:
                result = client.web_login(data)
                if _login_flag(result, "needRemoteLoginVerify"):
                    self._require_otp(data, generation)
                    return
                if _login_flag(result, "verifyCodeExpired"):
                    self._expire(generation)
                    return
                self._complete_login(client, result, generation)
            except LoginWaitTimeout:
                self._fail(generation, "微信读书登录验证超时，请重新扫码。")
            except Exception:
                self._fail(generation, "微信读书登录未完成，请在手机上确认后重试。")
            return
        if data.get("needRemoteLoginVerify"):
            self._require_otp({**data, "uid": uid}, generation)
            return
        if response.get("errCode") not in (None, 0, "0", ""):
            self._fail(generation, "微信读书未确认登录，请重新扫码。")

    def _require_otp(self, data: dict[str, Any], generation: int) -> None:
        pending: dict[str, Any] = {}
        for key in ("vid", "skey", "code", "uid"):
            value = data.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                value = str(value)
            pending[key] = value
        pending["pf"] = data.get("pf", 2)
        has_credentials = all(
            isinstance(pending[key], str) and pending[key]
            for key in ("vid", "skey", "code", "uid")
        )
        if not has_credentials or not pending["vid"].isdigit():
            self._fail(generation, "微信读书登录未完成，请重新扫码。")
            return
        with self.lock:
            if generation != self.generation:
                return
            self._pending_login = pending
            self.state = "otp_required"
            self.message = "请在网页输入手机显示的 4 位验证码。"

    def _verify_otp(self, client: LoginClient, pending: dict[str, Any], otp: str, generation: int) -> None:
        try:
            result = client.web_login({**pending, "otp": otp})
            if _login_flag(result, "verifyCodeExpired"):
                self._expire(generation)
                return
            if _login_flag(result, "isVerifyFailed"):
                with self.lock:
                    if generation != self.generation:
                        return
                    self._pending_otp = None
                    self.state = "otp_required"
                    self.message = "验证码错误，请重新输入手机显示的 4 位验证码。"
                return
            if _login_flag(result, "needRemoteLoginVerify"):
                with self.lock:
                    if generation != self.generation:
                        return
                    self._pending_otp = None
                    self.state = "otp_required"
                    self.message = "验证码未通过，请重新输入手机显示的 4 位验证码。"
                return
            self._complete_login(client, result, generation)
        except LoginWaitTimeout:
            self._fail(generation, "验证码验证超时，请重新扫码。")
        except Exception:
            self._fail(generation, "验证码验证失败，请重新扫码。")

    def _complete_login(self, client: LoginClient, result: dict[str, Any], generation: int) -> None:
        cookie = client.cookie_from_login(result)
        # Serialize the final handoff with cancellation. A user who cancels
        # before this lock is acquired must not be connected by a late response.
        with self.lock:
            if generation != self.generation:
                return
            self.save_cookie(cookie)
            if generation != self.generation:
                return
            self._pending_login = None
            self._pending_otp = None
            self._client = None
            self._uid = None
            self.state = "success"
            self.message = "微信读书已连接。"
            self.done.set()

    def _expire_locked(self) -> None:
        self.state = "expired"
        self.message = "二维码已过期，请重新生成。"
        self.svg = b""
        self._pending_login = None
        self._pending_otp = None
        self._client = None
        self._uid = None
        self.done.set()

    def _expire(self, generation: int) -> None:
        with self.lock:
            if generation != self.generation:
                return
            self._expire_locked()

    def _fail(self, generation: int, message: str) -> None:
        with self.lock:
            if generation != self.generation:
                return
            self.state = "failed"
            self.message = message
            self.svg = b""
            self._pending_login = None
            self._pending_otp = None
            self._client = None
            self._uid = None
            self.done.set()
