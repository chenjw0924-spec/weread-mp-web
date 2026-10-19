"""Local, synthetic UI acceptance server. Never used by the production app.
Run: PYTHONPATH=.:tests uvicorn ui_server:app --host 127.0.0.1 --port 18766
Connection value: ui-test-cookie.
"""
import tempfile
import time
from app.config import Settings
from app.main import create_app
from app.weread import Account, ArticleListResult, ListedArticle, WeReadError


class UIWeRead:
    def __init__(self):
        self.article_attempts = {}
        self.list_calls = {}
        self.expire_next = False

    def validate_cookie(self, cookie):
        if cookie != 'ui-test-cookie':
            raise WeReadError('Invalid test connection', 'invalid_cookie')
        self.expire_next = False
        return [Account('MP_WXS_reading', '阅读笔记精选'), Account('MP_WXS_business', '商业观察'), Account('MP_WXS_empty', '暂无文章的公众号')]

    def list_articles(self, cookie, book_id, max_articles=None, from_date=None, to_date=None):
        if self.expire_next:
            self.expire_next = False
            raise WeReadError('Synthetic expired connection', 'expired')
        time.sleep(0.2)
        if book_id == 'MP_WXS_empty': return ArticleListResult([], 1, 'empty_page', None, None)
        name = '阅读笔记精选' if book_id == 'MP_WXS_reading' else '商业观察'
        articles = [
            ListedArticle('界面测试：建立自己的阅读体系', '把读过的文章整理成可以反复使用的知识。', name, 'a1', 1792252800),
            ListedArticle('界面测试：长期积累的价值', '慢下来，记录那些值得保存的观察与思考。', name, 'a2', 1792166400),
            ListedArticle('界面测试：未来的经营机会', '这是第一次导出会失败、重试会成功的测试文章。', name, 'a3', 1792080000),
        ]
        dates = [item.date for item in articles]
        return ArticleListResult(articles, 1, 'empty_page', min(dates), max(dates))

    def get_article_html(self, cookie, book_id, original_id):
        time.sleep(0.8)
        count = self.article_attempts.get(original_id, 0)
        self.article_attempts[original_id] = count + 1
        if original_id == 'a3' and count == 0:
            raise WeReadError('Synthetic article failure', 'network')
        return '<div id="js_content"><p>仅供界面验收的示例正文。</p><img data-src="https://mmbiz.qpic.cn/test.png"></div>'

fake = UIWeRead()
app = create_app(Settings(app_password='ui-test-password-only', cookie_secure=False), fake, tempfile.mkdtemp(prefix='weread-ui-test-'))
app.state.jobs.archive_service.image_fetcher = lambda url: (_ for _ in ()).throw(RuntimeError('Synthetic image failure'))

@app.post('/test/expire')
def expire():
    fake.expire_next = True
    return {'ok': True}
