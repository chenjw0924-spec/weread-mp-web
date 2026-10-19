from dataclasses import dataclass, field
import hmac
import secrets
import threading
import time
from typing import Any

from .config import Settings


@dataclass
class Session:
    expires_at: float
    connected_cookie: str | None = field(default=None, repr=False)
    accounts: dict[str, dict[str, Any]] = field(default_factory=dict)
    articles: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    coverage: dict[str, dict[str, Any]] = field(default_factory=dict)
    jobs: dict[str, Any] = field(default_factory=dict)


class AuthManager:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._sessions: dict[str, Session] = {}
        self._failed_logins: dict[str, list[float]] = {}
        self._lock = threading.RLock()

    @property
    def session_count(self) -> int:
        with self._lock:
            return len(self._sessions)

    def login(self, password: str, client_ip: str, now: float | None = None) -> str | None:
        current = time.monotonic() if now is None else now
        with self._lock:
            attempts = [
                stamp for stamp in self._failed_logins.get(client_ip, [])
                if current - stamp < self._settings.login_window_seconds
            ]
            self._failed_logins[client_ip] = attempts
            if len(attempts) >= self._settings.login_attempt_limit:
                return None
            if not hmac.compare_digest(password.encode(), self._settings.app_password.encode()):
                attempts.append(current)
                return None

            self._failed_logins.pop(client_ip, None)
            self._prune_expired(current)
            token = secrets.token_urlsafe(32)
            self._sessions[token] = Session(
                expires_at=current + self._settings.session_ttl_seconds
            )
            return token

    def get_session(self, token: str | None, now: float | None = None) -> Session | None:
        if not token:
            return None
        current = time.monotonic() if now is None else now
        with self._lock:
            session = self._sessions.get(token)
            if session is None:
                return None
            if session.expires_at <= current:
                self._sessions.pop(token, None)
                return None
            return session

    def logout(self, token: str | None) -> None:
        if not token:
            return
        with self._lock:
            self._sessions.pop(token, None)

    def disconnect_wechat(self, token: str, now: float | None = None) -> None:
        session = self.get_session(token, now)
        if session is None:
            return
        with self._lock:
            session.connected_cookie = None
            session.accounts.clear()
            session.articles.clear()
            session.coverage.clear()
            session.jobs.clear()

    def _prune_expired(self, now: float) -> None:
        expired = [token for token, session in self._sessions.items() if session.expires_at <= now]
        for token in expired:
            self._sessions.pop(token, None)
