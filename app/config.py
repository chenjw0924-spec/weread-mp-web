from dataclasses import dataclass
import os
from typing import Mapping


def _as_int(env: Mapping[str, str], key: str, default: int, minimum: int) -> int:
    try:
        value = int(env.get(key, str(default)))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be an integer") from exc
    if value < minimum:
        raise ValueError(f"{key} must be at least {minimum}")
    return value


@dataclass(frozen=True)
class Settings:
    app_password: str
    cookie_secure: bool = True
    session_ttl_seconds: int = 8 * 60 * 60
    login_attempt_limit: int = 5
    login_window_seconds: int = 60
    max_archive_articles: int = 100
    max_list_pages: int = 50
    artifact_ttl_seconds: int = 15 * 60
    max_job_count: int = 4
    max_response_bytes: int = 8 * 1024 * 1024
    max_image_bytes: int = 12 * 1024 * 1024

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        values = os.environ if env is None else env
        password = values.get("APP_PASSWORD", "")
        if len(password) < 12:
            raise ValueError("APP_PASSWORD is required and must contain at least 12 characters")

        secure_value = values.get("COOKIE_SECURE", "true").strip().lower()
        if secure_value not in {"true", "false"}:
            raise ValueError("COOKIE_SECURE must be true or false")

        return cls(
            app_password=password,
            cookie_secure=secure_value == "true",
            session_ttl_seconds=_as_int(values, "SESSION_TTL_SECONDS", 8 * 60 * 60, 60),
            login_attempt_limit=_as_int(values, "LOGIN_ATTEMPT_LIMIT", 5, 1),
            login_window_seconds=_as_int(values, "LOGIN_WINDOW_SECONDS", 60, 1),
            max_archive_articles=_as_int(values, "MAX_ARCHIVE_ARTICLES", 100, 1),
            max_list_pages=_as_int(values, "MAX_LIST_PAGES", 50, 1),
            artifact_ttl_seconds=_as_int(values, "ARTIFACT_TTL_SECONDS", 15 * 60, 60),
            max_job_count=_as_int(values, "MAX_JOB_COUNT", 4, 1),
            max_response_bytes=_as_int(values, "MAX_RESPONSE_BYTES", 8 * 1024 * 1024, 1024),
            max_image_bytes=_as_int(values, "MAX_IMAGE_BYTES", 12 * 1024 * 1024, 1024),
        )
