from pathlib import Path
import subprocess

from bs4 import BeautifulSoup


ROOT = Path(__file__).parents[1]


def test_frontend_has_required_operator_states_and_controls():
    html = (ROOT / "app/templates/index.html").read_text()
    script = (ROOT / "app/static/app.js").read_text()
    css = (ROOT / "app/static/app.css").read_text()

    for text in ("Cookie", "我的公众号", "下载图片", "重试失败项", "已收藏", "按日期读取"):
        assert text in html
    for endpoint in ("/api/session", "/api/weread/connect", "/api/accounts/", "/api/jobs", "/api/jobs/${state.jobId}/retry"):
        assert endpoint in script
    assert "已读取最近 1 年内" in script
    assert "aria-live" in html
    assert "@media" in css


def test_frontend_starts_with_we_read_scan_instead_of_access_password():
    html = (ROOT / "app/templates/index.html").read_text()
    script = (ROOT / "app/static/app.js").read_text()

    assert "输入访问密码" not in html
    assert 'id="password"' not in html
    assert "startQrLogin()" in script


def test_frontend_uses_text_content_for_article_values():
    script = (ROOT / "app/static/app.js").read_text()
    assert "textContent = article.title" in script
    assert "textContent = article.summary" in script


def test_scan_status_is_visible_on_the_initial_connection_page():
    page = BeautifulSoup((ROOT / "app/templates/index.html").read_text(), 'html.parser')
    status = page.select_one('#connect-panel #connect-message[role="status"]')
    assert status is not None
    assert status.get('id') == 'connect-message'
    assert not any(ancestor.has_attr('hidden') for ancestor in status.parents)


def test_homepage_includes_collapsed_operation_guide():
    page = BeautifulSoup((ROOT / "app/templates/index.html").read_text(), "html.parser")
    guide = page.select_one("#operation-guide")

    assert guide is not None
    assert guide.parent.select_one(":scope > #operation-guide") is guide
    assert guide.parent.get("class") == ["shell", "operation-guide-top"]
    assert guide.name == "details"
    assert not guide.has_attr("open")
    assert guide.select_one("summary strong").get_text(" ", strip=True) == "操作指引"
    guide_text = guide.get_text(" ", strip=True)
    for text in ("使用微信 App 扫码", "输入 4 位验证码", "选择公众号", "导出 Markdown ZIP"):
        assert text in guide_text


def test_homepage_has_public_ad_review_content_and_site_links():
    page = BeautifulSoup((ROOT / "app/templates/index.html").read_text(), "html.parser")
    assert page.select_one('link[rel="canonical"][href="https://www.weread-export.top/"]') is not None
    public_info = page.select_one("#public-info")
    assert public_info is not None
    assert "不建立在线文章库" in public_info.get_text(" ", strip=True)
    footer = page.select_one(".site-footer")
    assert footer is not None
    assert footer.select_one('a[href="/privacy"]') is not None
    assert footer.select_one('a[href="/support"]') is not None


def test_scan_events_show_loading_otp_and_verified_success():
    result = subprocess.run(['node', '--test', 'tests/login-ui.cjs', 'tests/archive-ui.cjs'], cwd=ROOT, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr


def test_manual_credentials_are_only_in_explicit_advanced_section():
    page = BeautifulSoup((ROOT / "app/templates/index.html").read_text(), "html.parser")
    advanced = page.select_one("details#advanced-connect")
    assert advanced is not None and not advanced.has_attr("open")
    assert advanced.select_one("#cookie") is not None
    assert "扫码连接微信读书" in page.get_text()
    assert page.select_one("#qr-image") is not None
    assert page.select_one("#qr-status[role='status']") is not None
    assert page.select_one(".demo-nav") is None
    assert page.select_one("#download-dialog").name == "dialog"
    assert page.select_one("#include-images").get("checked") is None
    for image in page.select("img"):
        if image.get("id") == "qr-image":
            continue
        assert image.get("src", "").startswith("/static/")
        assert (ROOT / "app" / image["src"].lstrip("/")).is_file()
