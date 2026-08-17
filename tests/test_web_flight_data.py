"""Flight-data prose recovery for client-rendered Next.js pages."""

from knowledge_extractor_v3.fetchers.multi_channel import _body_from_html
from knowledge_extractor_v3.fetchers.web import _flight_data_text, _text_richness, extract_page


def _flight_html() -> str:
    # Shape mirrors elsewhere.news: article body lives only in escaped
    # self.__next_f.push chunks; the rendered DOM carries nav boilerplate.
    body = (
        "\\u5728\\u51fa\\u6d77\\u8d5b\\u9053\\uff0c\\u5fae\\u89c2\\u4f53\\u611f"
        "\\u4e0e\\u5b8f\\u89c2\\u6570\\u636e\\u6b63\\u4e0a\\u6f14\\u7740\\u4e00"
        "\\u573a\\u8010\\u4eba\\u5bfb\\u5473\\u7684\\u9519\\u4f4d\\u3002"
        "\\u521b\\u6295\\u5708\\u7684\\u6ce8\\u610f\\u529b\\u51e0\\u4e4e\\u88ab"
        "\\u5177\\u8eab\\u667a\\u80fd\\u8d5b\\u9053\\u72ec\\u5360\\uff0c"
        "\\u8fd9\\u662f\\u4e00\\u6bb5\\u8db3\\u591f\\u957f\\u7684\\u6b63\\u6587"
        "\\u5185\\u5bb9\\u7528\\u4e8e\\u89e6\\u53d1\\u63d0\\u53d6\\u3002"
    )
    return (
        "<!DOCTYPE html><html><head><title>为什么我们仍然看好AI+硬件+出海赛道？</title>"
        "<meta property=\"og:title\" content=\"为什么我们仍然看好AI+硬件+出海赛道？\"/>"
        "</head><body><nav>首页 播客 关于 登录</nav><div id=\"root\"></div>"
        "<script>self.__next_f.push([1,\"0:{'body':'" + body + "'}\"])</script>"
        "</body></html>"
    )


def test_flight_data_text_recovers_cjk_prose():
    text = _flight_data_text(_flight_html())

    assert "在出海赛道" in text
    assert "创投圈" in text
    assert "首页 播客" not in text


def test_flight_data_text_ignores_pages_without_payload():
    assert _flight_data_text("<html><body>plain page</body></html>") == ""


def test_extract_page_prefers_flight_text_over_boilerplate():
    page = extract_page(_flight_html(), fallback_title="elsewhere.news")

    assert page.title == "为什么我们仍然看好AI+硬件+出海赛道？"
    assert "在出海赛道" in page.text
    assert "首页 播客" not in page.text


def test_text_richness_counts_cjk_not_spaces():
    assert _text_richness("在出海赛道微观体感与宏观数据") > _text_richness("nav menu login home page")


def test_body_from_html_strips_scripts_and_recovers_flight_prose():
    body = _body_from_html(_flight_html())

    # The inline script (40KB+ on real client-rendered pages) must not leak.
    assert "self.__next_f" not in body
    assert "=>{" not in body
    assert "在出海赛道" in body
    assert "首页 播客" not in body
