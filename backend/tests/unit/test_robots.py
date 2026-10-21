"""robots.txt 匹配：按 RFC 9309 的分组、最长匹配、通配符和 `$`。"""

import pytest
from app.providers.robots import RobotsRules

AGENT = "finscopeai"


def _allows(text: str, path: str, agent: str = AGENT) -> bool:
    return RobotsRules.parse(text).allows(agent, path)


def test_no_rules_means_everything_is_allowed() -> None:
    assert _allows("", "/anything")
    assert _allows("User-agent: *\nDisallow:\n", "/anything")


def test_star_group_applies_when_our_agent_is_not_named() -> None:
    text = "User-agent: *\nDisallow: /private/\n"
    assert not _allows(text, "/private/a.html")
    assert _allows(text, "/public/a.html")


def test_named_group_beats_the_star_group() -> None:
    text = "User-agent: *\nDisallow: /\n\nUser-agent: FinScopeAI\nAllow: /news/\nDisallow: /\n"
    assert _allows(text, "/news/1.html")
    assert not _allows(text, "/other")
    assert not _allows(text, "/news/1.html", agent="otherbot")


def test_longest_match_wins_and_allow_wins_a_tie() -> None:
    text = "User-agent: *\nDisallow: /a\nAllow: /a/b\n"
    assert _allows(text, "/a/b/c")
    assert not _allows(text, "/a/x")
    tie = "User-agent: *\nDisallow: /same\nAllow: /same\n"
    assert _allows(tie, "/same")


def test_wildcards_and_end_anchor() -> None:
    text = "User-agent: *\nDisallow: /*.pdf$\nDisallow: /tmp*/draft\n"
    assert not _allows(text, "/files/report.pdf")
    assert _allows(text, "/files/report.pdf.html")
    assert not _allows(text, "/tmp2026/draft")
    assert _allows(text, "/tmp2026/final")


def test_query_string_is_part_of_the_path() -> None:
    text = "User-agent: *\nDisallow: /search?\n"
    assert not _allows(text, "/search?q=1")
    assert _allows(text, "/search")


def test_comments_blank_lines_and_unknown_fields_are_ignored() -> None:
    text = (
        "# 注释\nUser-agent: *  # 所有爬虫\nCrawl-delay: 10\nSitemap: https://x.test/map.xml\n"
        "Disallow: /x # 不让抓\n"
    )
    assert not _allows(text, "/x/y")
    assert _allows(text, "/y")


def test_several_user_agent_lines_share_one_group() -> None:
    text = "User-agent: botA\nUser-agent: finscopeai\nDisallow: /secret\n"
    assert not _allows(text, "/secret")
    assert _allows(text, "/open")


@pytest.mark.parametrize("path", ["/", "/index.html"])
def test_disallow_everything(path: str) -> None:
    assert not _allows("User-agent: *\nDisallow: /\n", path)
