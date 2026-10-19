import time

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.weread import Account
from app.weread_login import LoginClient, LoginProtocolError, QRLogin


class FakeLoginClient:
    def __init__(self):
        self.info_calls = 0
        self.jar = []

    def get_uid(self):
        return "uid-for-test"

    def get_login_info(self, uid):
        assert uid == "uid-for-test"
        self.info_calls += 1
        if self.info_calls == 1:
            raise TimeoutError("long poll")
        return {"data": {"vid": "123", "skey": "temporary-skey", "code": "login-code", "pf": 2}}

    def web_login(self, data):
        assert data["vid"] == "123"
        assert data["skey"] == "temporary-skey"
        assert data["code"] == "login-code"
        assert data["uid"] == "uid-for-test"
        return {"data": {"vid": "123", "accessToken": "secret-access-token", "succeed": True}}

    def cookie_from_login(self, payload):
        return "wr_vid=123; wr_skey=secret-access-token"


class FakeWeRead:
    def validate_cookie(self, cookie):
        assert "secret-access-token" in cookie
        return [Account("MP_WXS_1", "测试公众号")]


class OtpLoginClient:
    def __init__(self):
        self.info_calls = 0
        self.web_login_calls = []

    def get_uid(self):
        return "uid-for-otp-test"

    def get_login_info(self, uid):
        assert uid == "uid-for-otp-test"
        self.info_calls += 1
        return {"data": {"vid": "456", "skey": "temporary-skey", "code": "login-code"}}

    def web_login(self, data):
        self.web_login_calls.append(dict(data))
        if "otp" not in data:
            return {"data": {"needRemoteLoginVerify": True}}
        if data["otp"] != "1234":
            return {"data": {"isVerifyFailed": True}}
        assert data["otp"] == "1234"
        return {"data": {"vid": "456", "accessToken": "secret-access-token", "succeed": True}}

    def cookie_from_login(self, payload):
        return "wr_vid=456; wr_skey=secret-access-token"


class RequestDrivenLoginClient(FakeLoginClient):
    def __init__(self):
        super().__init__()
        self.allow_poll = False

    def get_login_info(self, uid):
        if not self.allow_poll:
            raise AssertionError("QR polling must happen in the status request")
        self.info_calls += 1
        return {"data": {"vid": "123", "skey": "temporary-skey", "code": "login-code", "pf": 2}}


def settings():
    return Settings(app_password="test-password-long", cookie_secure=False)


def login(client):
    assert client.get("/api/session").status_code == 200


def wait_for_state(client, login_id, expected):
    for _ in range(60):
        response = client.get(f"/api/weread/qr/{login_id}/status")
        if response.json()["state"] == expected:
            return response
        time.sleep(0.01)
    raise AssertionError(f"QR login did not reach {expected}")


def wait_for_qr_state(login, expected):
    for _ in range(100):
        login.poll()
        status = login.status()
        if status["state"] == expected:
            return status
        time.sleep(0.01)
    raise AssertionError(f"QR login did not reach {expected}")


def test_qr_login_uses_current_we_read_protocol_and_never_returns_cookie():
    saved = []
    login = QRLogin(saved.append, client_factory=FakeLoginClient, ttl_seconds=30)

    status = login.start()
    assert status["state"] == "waiting"
    assert "https://weread.qq.com/web/confirm?pf=2&uid=uid-for-test" == login.confirm_url
    assert login.image().startswith(b"<?xml")

    result = login.wait_for_terminal(timeout=2)
    assert result["state"] == "success"
    assert saved == ["wr_vid=123; wr_skey=secret-access-token"]
    assert "secret-access-token" not in str(result)


def test_qr_api_is_session_scoped_and_connects_after_success():
    app = create_app(
        settings=settings(),
        weread_client=FakeWeRead(),
        qr_client_factory=FakeLoginClient,
    )
    first = TestClient(app)
    login(first)
    started = first.post("/api/weread/qr/start")
    assert started.status_code == 200
    payload = started.json()
    assert payload["state"] == "waiting"
    assert payload["image_url"].startswith("/api/weread/qr/")
    assert "secret-access-token" not in started.text

    image = first.get(payload["image_url"])
    assert image.status_code == 200
    assert image.headers["content-type"].startswith("image/svg+xml")

    second = TestClient(app)
    login(second)
    assert second.get(f"/api/weread/qr/{payload['login_id']}/status").status_code == 404

    completed = wait_for_state(first, payload["login_id"], "success")
    assert completed.json()["connected"] is True
    assert completed.json()["accounts"] == [{"book_id": "MP_WXS_1", "title": "测试公众号", "cover_url": ""}]
    assert "secret-access-token" not in completed.text


def test_qr_status_request_drives_upstream_poll_without_background_thread():
    clients = []

    def factory():
        client = RequestDrivenLoginClient()
        clients.append(client)
        return client

    app = create_app(
        settings=settings(),
        weread_client=FakeWeRead(),
        qr_client_factory=factory,
    )
    client = TestClient(app)
    login(client)
    started = client.post("/api/weread/qr/start").json()
    assert clients[0].info_calls == 0

    clients[0].allow_poll = True
    completed = client.get(f"/api/weread/qr/{started['login_id']}/status")

    assert completed.status_code == 200
    assert completed.json()["state"] == "success"


def test_qr_api_rejects_unknown_or_expired_login_without_leaking_state():
    app = create_app(settings=settings(), weread_client=FakeWeRead(), qr_client_factory=FakeLoginClient)
    client = TestClient(app)
    login(client)
    assert client.get("/api/weread/qr/missing/status").status_code == 404
    assert client.get("/api/weread/qr/missing/image").status_code == 404


def test_qr_login_waits_for_otp_and_completes_without_exposing_credentials():
    saved = []
    clients = []

    def factory():
        client = OtpLoginClient()
        clients.append(client)
        return client

    login = QRLogin(saved.append, client_factory=factory, ttl_seconds=30)
    assert login.start()["state"] == "waiting"
    status = wait_for_qr_state(login, "otp_required")
    assert "4 位验证码" in status["message"]
    assert "secret-access-token" not in str(status)

    login.submit_otp("1234")
    result = wait_for_qr_state(login, "success")
    assert saved == ["wr_vid=456; wr_skey=secret-access-token"]
    assert "secret-access-token" not in str(result)
    assert clients[0].web_login_calls[1]["otp"] == "1234"


def test_qr_login_accepts_numeric_vid_from_we_read_login_response():
    class NumericVidClient(OtpLoginClient):
        def get_login_info(self, uid):
            assert uid == "uid-for-otp-test"
            return {"data": {"vid": 456, "skey": "temporary-skey", "code": "login-code"}}

    saved = []
    client = NumericVidClient()
    login = QRLogin(saved.append, client_factory=lambda: client, ttl_seconds=30)
    assert login.start()["state"] == "waiting"
    status = wait_for_qr_state(login, "otp_required")
    assert "4 位验证码" in status["message"]
    assert login.submit_otp("1234")["state"] == "success"
    assert wait_for_qr_state(login, "success")["state"] == "success"
    assert client.web_login_calls[1]["otp"] == "1234"
    assert client.web_login_calls[1]["pf"] == 2


def test_qr_login_rejects_invalid_otp_without_calling_upstream():
    client = OtpLoginClient()
    login = QRLogin(lambda _: None, client_factory=lambda: client, ttl_seconds=30)
    login.start()
    wait_for_qr_state(login, "otp_required")
    try:
        login.submit_otp("12ab")
    except LoginProtocolError as exc:
        assert str(exc) == "invalid_otp"
    else:
        raise AssertionError("invalid OTP should be rejected")
    assert len(client.web_login_calls) == 1


def test_qr_login_keeps_otp_form_available_after_wrong_code():
    client = OtpLoginClient()
    login = QRLogin(lambda _: None, client_factory=lambda: client, ttl_seconds=30)
    login.start()
    wait_for_qr_state(login, "otp_required")
    login.submit_otp("9876")
    status = wait_for_qr_state(login, "otp_required")
    assert "验证码错误" in status["message"]
    assert login.submit_otp("1234")["state"] == "success"
    assert wait_for_qr_state(login, "success")["state"] == "success"


def test_qr_login_expires_while_waiting_for_otp():
    login = QRLogin(lambda _: None, client_factory=lambda: OtpLoginClient(), ttl_seconds=30)
    login.start()
    wait_for_qr_state(login, "otp_required")
    login.expires_at = time.time() - 1
    status = login.status()
    assert status["state"] == "expired"
    assert "二维码已过期" in status["message"]


def test_qr_login_rejects_otp_before_qr_was_scanned():
    class WaitingLoginClient(OtpLoginClient):
        def get_login_info(self, uid):
            raise TimeoutError("poll pending")

    client = WaitingLoginClient()
    login = QRLogin(lambda _: None, client_factory=lambda: client, ttl_seconds=30)
    assert login.start()["state"] == "waiting"
    try:
        login.submit_otp("1234")
    except LoginProtocolError as exc:
        assert str(exc) == "otp_not_required"
    else:
        raise AssertionError("OTP should not be accepted before the QR scan")
    assert client.web_login_calls == []


def test_login_client_reuses_fingerprint_for_otp_retry():
    client = LoginClient(opener=object())
    calls = []
    client.request = lambda path, values, timeout=20: calls.append((path, values, timeout)) or {"data": {"needRemoteLoginVerify": True}}
    data = {"vid": "456", "skey": "temporary-skey", "code": "login-code", "uid": "uid-for-otp-test", "pf": 2}
    client.web_login(data)
    client.web_login({**data, "otp": "1234"})
    assert calls[0][1]["pf"] == 2
    assert calls[1][1]["pf"] == 2
    assert calls[0][1]["fp"] == calls[1][1]["fp"]


def test_qr_otp_api_is_session_scoped_and_does_not_return_tokens():
    clients = []

    def factory():
        client = OtpLoginClient()
        clients.append(client)
        return client

    app = create_app(settings=settings(), weread_client=FakeWeRead(), qr_client_factory=factory)
    first = TestClient(app)
    login(first)
    started = first.post("/api/weread/qr/start").json()
    wait_for_state(first, started["login_id"], "otp_required")

    second = TestClient(app)
    login(second)
    invalid = first.post(f"/api/weread/qr/{started['login_id']}/verify", json={"code": "12345"})
    assert invalid.status_code == 400
    assert "12345" not in invalid.text
    assert "secret-access-token" not in invalid.text
    assert second.post(f"/api/weread/qr/{started['login_id']}/verify", json={"code": "1234"}).status_code == 404

    submitted = first.post(f"/api/weread/qr/{started['login_id']}/verify", json={"code": "1234"})
    assert submitted.status_code == 200
    completed = wait_for_state(first, started["login_id"], "success")
    assert completed.json()["connected"] is True
    assert "secret-access-token" not in submitted.text
    assert "secret-access-token" not in completed.text
    assert clients[0].web_login_calls[1]["otp"] == "1234"
