"""网页正文抽取：保留成段的正文，去掉导航、页脚、链接列表；编码、发布日期、截断。"""

from datetime import date

from app.services.page_text import decode_html, extract_article

TODAY = date(2026, 10, 21)

PARAGRAPH_1 = (
    "国务院办公厅近日印发通知，要求进一步加强集成电路产业链上下游协同，"
    "提升关键设备和材料的自主保障能力。"
)
PARAGRAPH_2 = (
    "通知提出，到明年底要建立重点企业清单，实行动态管理，并在财税、金融和人才政策上给予支持。"
)
PARAGRAPH_3 = (
    "各地区、各部门要加强统筹协调，压实工作责任，确保各项任务措施落实落地，"
    "重大情况及时报告国务院办公厅。"
)
PARAGRAPH_4 = (
    "文件同时明确，相关进展每季度向社会公开一次，接受行业协会、企业和公众的监督，"
    "并根据实施效果适时调整政策。"
)

ARTICLE = f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<title>关于加强产业链协同的通知 - 中国政府网</title>
<meta name="PubDate" content="2026-10-20T09:30:00+08:00">
<style>.x {{ color: red }}</style>
<script>var tracking = "这段脚本里的文字不应该出现在正文里，哪怕它有很长很长很长很长很长";</script>
</head><body>
<nav><a href="/">首页</a> <a href="/zc">政策</a> <a href="/xw">新闻动态与要闻回顾</a></nav>
<div class="banner">中国政府网</div>
<h1>国务院办公厅关于加强产业链协同的通知</h1>
<div class="info">来源：国务院办公厅 2026-10-20</div>
<div id="UCAP-CONTENT">
  <p>{PARAGRAPH_1}</p>
  <p>　　{PARAGRAPH_2}</p>
  <p>{PARAGRAPH_3}</p>
  <p>{PARAGRAPH_4}</p>
  <p>责任编辑：张三</p>
</div>
<h3>相关阅读</h3>
<ul>
  <li><a href="/a1">国务院印发另一份很长很长标题的政策文件通知</a></li>
  <li><a href="/a2">国家发展改革委发布另一份同样很长很长的公告文件</a></li>
</ul>
<p>这一段在相关阅读之后，属于推荐区域，不应该被当成正文的一部分被收进来。</p>
<footer>版权所有 中国政府网 京ICP备05070218号 联系我们</footer>
</body></html>""".encode()


def _page(body: str, head: str = "") -> str:
    return f"<html><head>{head}</head><body>{body}</body></html>"


def test_article_paragraphs_are_kept_and_the_chrome_is_dropped() -> None:
    article = extract_article(ARTICLE, max_chars=6000, today=TODAY)
    assert article.title == "国务院办公厅关于加强产业链协同的通知"
    assert article.text.splitlines() == [
        "国务院办公厅关于加强产业链协同的通知",
        "来源：国务院办公厅 2026-10-20",
        PARAGRAPH_1,
        PARAGRAPH_2,  # 全角空格缩进被去掉
        PARAGRAPH_3,
        PARAGRAPH_4,
    ]
    for dropped in ("首页", "跟踪", "tracking", "版权所有", "ICP备", "责任编辑", "推荐区域"):
        assert dropped not in article.text
    assert not article.truncated


def test_the_page_title_is_not_repeated_in_the_text() -> None:
    article = extract_article(ARTICLE, max_chars=6000, today=TODAY)
    assert "中国政府网" not in article.text.replace("国务院办公厅", "")


def test_the_publication_date_comes_from_the_page_metadata() -> None:
    assert extract_article(ARTICLE, max_chars=6000, today=TODAY).published == date(2026, 10, 20)


def test_without_metadata_a_labelled_date_near_the_top_is_used() -> None:
    html = _page(f"<p>发布时间：2026年10月19日 来源：某部委办公室</p><p>{PARAGRAPH_1}</p>")
    assert extract_article(html.encode(), max_chars=6000, today=TODAY).published == date(
        2026, 10, 19
    )


def test_a_source_line_with_a_date_counts_as_a_labelled_date() -> None:
    html = _page(f"<p>来源：新华社 2026-10-18</p><p>{PARAGRAPH_1}</p>")
    assert extract_article(html.encode(), max_chars=6000, today=TODAY).published == date(
        2026, 10, 18
    )


def test_todays_date_in_the_page_banner_is_not_taken_as_the_publication_date() -> None:
    html = _page(f"<div>今天是 2026年10月21日 星期三，欢迎访问本站</div><p>{PARAGRAPH_1}</p>")
    assert extract_article(html.encode(), max_chars=6000, today=TODAY).published is None


def test_a_date_in_the_future_is_not_taken_as_the_publication_date() -> None:
    html = _page(f"<p>{PARAGRAPH_1}</p>", head='<meta name="pubdate" content="2027-03-01">')
    assert extract_article(html.encode(), max_chars=6000, today=TODAY).published is None


def test_no_date_anywhere_means_none_not_today() -> None:
    html = _page(f"<p>{PARAGRAPH_1}</p>")
    assert extract_article(html.encode(), max_chars=6000, today=TODAY).published is None


def test_pages_in_gbk_are_decoded_by_their_declared_charset() -> None:
    declared = '<meta http-equiv="Content-Type" content="text/html; charset=gb2312">'
    html = _page(f"<p>{PARAGRAPH_1}</p>", head=declared)
    article = extract_article(html.encode("gb18030"), max_chars=6000, today=TODAY)
    assert PARAGRAPH_1 in article.text


def test_undeclared_gbk_falls_back_when_utf8_does_not_decode() -> None:
    html = _page(f"<p>{PARAGRAPH_1}</p>").encode("gb18030")
    assert PARAGRAPH_1 in extract_article(html, max_chars=6000, today=TODAY).text


def test_a_utf8_bom_is_removed_before_decoding() -> None:
    assert decode_html("\ufeff<p>正文</p>".encode()) == "<p>正文</p>"


def test_an_unknown_charset_name_does_not_crash() -> None:
    html = _page(f"<p>{PARAGRAPH_1}</p>", head='<meta charset="no-such-charset">')
    assert PARAGRAPH_1 in extract_article(html.encode(), max_chars=6000, today=TODAY).text


def test_text_is_cut_at_the_limit_and_flagged() -> None:
    body = "".join(f"<p>{PARAGRAPH_1}（第 {n} 段）</p>" for n in range(30))
    article = extract_article(_page(body).encode(), max_chars=300, today=TODAY)
    assert article.truncated and len(article.text) <= 300


def test_text_that_fits_exactly_is_not_reported_as_cut() -> None:
    article = extract_article(_page(f"<p>{PARAGRAPH_1}</p>").encode(), max_chars=6000, today=TODAY)
    exact = extract_article(
        _page(f"<p>{PARAGRAPH_1}</p>").encode(), max_chars=len(PARAGRAPH_1), today=TODAY
    )
    assert not article.truncated and not exact.truncated and exact.text == PARAGRAPH_1


def test_a_page_wrapped_in_a_form_still_yields_its_article() -> None:
    # ASP.NET 站点常把整个页面包在 <form> 里
    html = _page(f"<form><div><p>{PARAGRAPH_1}</p></div></form>")
    assert PARAGRAPH_1 in extract_article(html.encode(), max_chars=6000, today=TODAY).text


def test_a_page_built_by_scripts_has_no_text() -> None:
    html = _page('<div id="app"></div><script src="/app.js"></script>')
    article = extract_article(html.encode(), max_chars=6000, today=TODAY)
    assert article.text == "" and article.title == ""


def test_link_lists_are_not_mistaken_for_an_article() -> None:
    items = "".join(
        f'<li><a href="/n{n}">第 {n} 条要闻的标题写得很长很长很长很长</a></li>' for n in range(20)
    )
    article = extract_article(_page(f"<ul>{items}</ul>").encode(), max_chars=6000, today=TODAY)
    assert article.text == ""


def test_english_pages_need_longer_blocks_than_chinese_ones() -> None:
    sentence = "The Committee decided to maintain the target range for the federal funds rate."
    html = _page(f"<p>Home</p><p>Menu item list</p><p>{sentence}</p>")
    assert extract_article(html.encode(), max_chars=6000, today=TODAY).text == sentence
