from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_frontend_has_required_operator_states_and_controls():
    html = (ROOT / "app/templates/index.html").read_text()
    script = (ROOT / "app/static/app.js").read_text()
    css = (ROOT / "app/static/app.css").read_text()

    for text in ("输入访问密码", "Cookie", "选择已关注公众号", "下载图片", "重试失败文章"):
        assert text in html
    for endpoint in ("/api/login", "/api/weread/connect", "/api/accounts/", "/api/jobs", "/api/jobs/${state.jobId}/retry"):
        assert endpoint in script
    assert "aria-live" in html
    assert "@media" in css


def test_frontend_uses_text_content_for_article_values():
    script = (ROOT / "app/static/app.js").read_text()
    assert "textContent = article.title" in script
    assert "textContent = article.summary" in script
