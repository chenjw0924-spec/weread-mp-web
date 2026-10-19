import pytest
import yaml

from app.content import ContentError, parse_article_html, parse_picture_pages, render_markdown
from app.weread import ListedArticle


NORMAL_ARTICLE = r'''
<html><head>
<meta property="og:title" content="标题：他说 &quot;你好&quot;">
<meta property="og:url" content="https://mp.weixin.qq.com/s?__biz=abc&amp;mid=123&amp;chksm=volatile&amp;idx=1">
<meta property="og:article:author" content="作者甲">
</head><body>
<div class="rich_media_content" id="js_content">
  <p>第一段。</p><div class="inner"><p>嵌套段落。</p></div>
  <p><img data-src="https://mmbiz.qpic.cn/mmbiz_png/abc/640?wx_fmt=png&amp;from=appmsg" alt="图片"></p>
  <p>最后一段。</p>
</div></body></html>
'''


TEXT_MESSAGE = r'''
<html><head><meta property="og:title" content="从页面脚本提取文字消息"></head>
<script>
window.text_page_info = {
  content_noencode: '正文第一段。\x0a\x3cp\x3e正文第二段 &amp;amp; 结束。\x3c/p\x3e',
};
</script></html>
'''


def listed(title="列表标题", original_id="opaque-article-id"):
    return ListedArticle(
        title=title,
        summary="摘要",
        account_name="公众号甲",
        original_id=original_id,
        published_at=1767225600,
    )


def test_normal_article_preserves_nested_text_images_and_canonical_source():
    article = parse_article_html(NORMAL_ARTICLE, listed())

    assert article.title == '标题：他说 "你好"'
    assert article.account == "作者甲"
    assert article.source_url.startswith("https://mp.weixin.qq.com/s?")
    assert "chksm" not in article.source_url
    assert "嵌套段落" in article.markdown_body
    assert "最后一段" in article.markdown_body
    assert article.image_urls == [
        "https://mmbiz.qpic.cn/mmbiz_png/abc/640?wx_fmt=png&from=appmsg"
    ]


def test_text_message_body_is_read_as_a_string_without_executing_javascript():
    article = parse_article_html(TEXT_MESSAGE, listed("页面标题"))

    assert article.title == "从页面脚本提取文字消息"
    assert "正文第一段" in article.markdown_body
    assert "正文第二段 & 结束" in article.markdown_body


def test_empty_or_truncated_response_is_reported_as_a_content_error():
    with pytest.raises(ContentError, match="正文"):
        parse_article_html("<html><head></head><body></body></html>", listed())


def test_picture_message_urls_are_parsed_without_evaluating_script():
    html = r'''
    window.picture_page_info_list: [
      { cdn_url: 'https://mmbiz.qpic.cn/mmbiz_png/abc/640?wx_fmt=png\\x26amp;amp;from=appmsg',
        width: '828' * 1, height: '1800' * 1, },
      { cdn_url: 'https://mmbiz.qpic.cn/mmbiz_png/skip/0', width: '0' * 1, height: '0' * 1, },
    ];
    '''

    pictures = parse_picture_pages(html)

    assert pictures == ["https://mmbiz.qpic.cn/mmbiz_png/abc/640?wx_fmt=png&from=appmsg"]


def test_markdown_front_matter_round_trips_yaml_special_characters_and_local_images():
    article = parse_article_html(NORMAL_ARTICLE, listed())
    image = article.image_urls[0]

    markdown = render_markdown(article, {image: "images/article-01.png"})
    frontmatter = markdown.split("---\n", 2)[1]
    metadata = yaml.safe_load(frontmatter)

    assert metadata["title"] == '标题：他说 "你好"'
    assert metadata["account"] == "作者甲"
    assert metadata["article_id"] == "opaque-article-id"
    assert "![图片](images/article-01.png)" in markdown


def test_failed_image_download_keeps_the_remote_url_in_markdown():
    article = parse_article_html(NORMAL_ARTICLE, listed())

    markdown = render_markdown(article, {})

    assert article.image_urls[0] in markdown
    assert "![图片](https://mmbiz.qpic.cn/" in markdown
