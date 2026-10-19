from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_deployment_uses_anonymous_sessions_and_one_local_worker():
    dockerfile = (ROOT / "Dockerfile").read_text()
    compose = (ROOT / "compose.yaml").read_text()
    env = (ROOT / ".env.example").read_text()
    readme = (ROOT / "README.md").read_text()

    assert "USER app" in dockerfile
    assert '"--workers", "1"' in dockerfile
    assert "127.0.0.1:8765:8000" in compose
    assert "APP_PASSWORD" not in compose
    assert "APP_PASSWORD" not in env
    assert "COOKIE_SECURE" in readme
    assert "不再设置访问密码" in readme
    assert "HTTPS" in readme


def test_compose_does_not_mount_cookie_or_article_storage():
    compose = (ROOT / "compose.yaml").read_text()
    assert "volumes:" not in compose
