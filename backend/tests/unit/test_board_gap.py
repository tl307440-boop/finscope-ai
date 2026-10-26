"""快照落后时才用新浪补当日涨跌；回看历史日期不补。一边榜单失败时另一边仍可用。"""

import asyncio
from datetime import UTC, datetime

import pytest
from app.providers import sina_market as sina
from app.providers.errors import ProviderError
from app.services.board_gap import fetch_sina_boards, needs_live_change, session_date

MONDAY_MORNING = datetime(2026, 10, 26, 2, 10, tzinfo=UTC)  # 上海 10:10
WEDNESDAY_CLOSE = datetime(2026, 10, 21, 8, 0, tzinfo=UTC)  # 上海 16:00


def test_monday_morning_session_is_the_previous_friday() -> None:
    assert session_date(MONDAY_MORNING).isoformat() == "2026-10-23"


def test_after_the_close_the_session_is_today() -> None:
    assert session_date(WEDNESDAY_CLOSE).isoformat() == "2026-10-21"


def test_a_snapshot_from_wednesday_needs_a_live_change_on_monday() -> None:
    assert needs_live_change(session_date(WEDNESDAY_CLOSE), MONDAY_MORNING, historical=False)


def test_asking_for_a_past_day_does_not_overlay_today() -> None:
    assert not needs_live_change(session_date(WEDNESDAY_CLOSE), MONDAY_MORNING, historical=True)


_INDUSTRY = '{"k":"new_semis,半导体,12,10,1,2.5,1,100,sz688981,4.2,1,1,北方华创"}'.encode("gbk")
_CONCEPT = '{"k":"new_chip,国产芯片,8,10,1,1.2,1,50,sz002371,3.0,1,1,北方华创"}'.encode("gbk")


class _Gateway:
    def __init__(self, routes: dict[str, bytes | Exception]) -> None:
        self.routes = routes

    async def get(self, url: str, headers: object) -> bytes:
        del headers
        found = self.routes[url]
        if isinstance(found, Exception):
            raise found
        return found


def test_one_sina_list_failing_keeps_the_other() -> None:
    down = ProviderError(code="UPSTREAM_ERROR", message="概念榜失败", status_code=502)
    boards = asyncio.run(
        fetch_sina_boards(_Gateway({sina.INDUSTRY_URL: _INDUSTRY, sina.CONCEPT_URL: down}))
    )
    assert [board.name for board in boards] == ["半导体"]
    assert boards[0].change_pct is not None and str(boards[0].change_pct) == "2.5"


def test_both_sina_lists_failing_raises() -> None:
    down = ProviderError(code="UPSTREAM_ERROR", message="榜单失败", status_code=502)
    with pytest.raises(ProviderError, match="榜单失败"):
        asyncio.run(fetch_sina_boards(_Gateway({sina.INDUSTRY_URL: down, sina.CONCEPT_URL: down})))
