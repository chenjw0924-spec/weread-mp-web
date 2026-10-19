"""Vercel entry point for the FastAPI application."""

from typing import Any

from app.main import app as fastapi_app


async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
    """Remove the Vercel function mount path before FastAPI routing."""
    if scope.get("type") == "http":
        path = str(scope.get("path") or "")
        prefix = "/api/index"
        if path == prefix or path.startswith(prefix + "/"):
            normalized = path[len(prefix) :] or "/"
            if not normalized.startswith("/"):
                normalized = "/" + normalized
            scope = dict(scope)
            scope["path"] = normalized
            scope["raw_path"] = normalized.encode("utf-8")
    await fastapi_app(scope, receive, send)


__all__ = ["app"]
