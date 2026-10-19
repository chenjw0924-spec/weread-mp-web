import pytest

from app.auth import AuthManager
from app.config import Settings


def make_settings(**overrides):
    values = {
        "app_password": "test-password",
        "cookie_secure": True,
        "session_ttl_seconds": 60,
        "login_attempt_limit": 3,
        "login_window_seconds": 60,
        "max_archive_articles": 100,
        "max_list_pages": 20,
        "artifact_ttl_seconds": 300,
        "max_job_count": 4,
        "max_response_bytes": 8 * 1024 * 1024,
        "max_image_bytes": 12 * 1024 * 1024,
    }
    values.update(overrides)
    return Settings(**values)


def test_settings_do_not_require_an_application_password():
    assert Settings.from_env({}).app_password == ""


def test_settings_default_article_list_capacity_is_one_hundred():
    settings = Settings.from_env({"APP_PASSWORD": "test-password-long"})

    assert settings.default_list_articles == 100


def test_login_creates_an_expiring_opaque_session():
    auth = AuthManager(make_settings())

    token = auth.login("test-password", "127.0.0.1", now=100)

    assert token
    assert token != "test-password"
    assert auth.get_session(token, now=159) is not None
    assert auth.get_session(token, now=160) is None


def test_invalid_password_does_not_create_a_session():
    auth = AuthManager(make_settings())

    assert auth.login("wrong", "127.0.0.1", now=100) is None
    assert auth.session_count == 0


def test_login_attempts_are_throttled_per_ip():
    auth = AuthManager(make_settings())

    assert auth.login("wrong", "127.0.0.1", now=100) is None
    assert auth.login("wrong", "127.0.0.1", now=101) is None
    assert auth.login("wrong", "127.0.0.1", now=102) is None
    assert auth.login("test-password", "127.0.0.1", now=103) is None
    assert auth.login("test-password", "127.0.0.2", now=103) is not None


def test_disconnect_clears_cookie_and_article_snapshot():
    auth = AuthManager(make_settings())
    token = auth.login("test-password", "127.0.0.1", now=100)
    session = auth.get_session(token, now=101)
    session.connected_cookie = "wr_skey=secret; wr_vid=private"
    session.accounts["MP_WXS_1"] = {"title": "测试号"}
    session.articles["MP_WXS_1"] = [{"title": "测试文章"}]

    auth.disconnect_wechat(token, now=102)

    assert session.connected_cookie is None
    assert session.accounts == {}
    assert session.articles == {}
