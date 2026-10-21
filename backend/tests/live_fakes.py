"""实时数据工具（研报、板块详情、情绪、快讯、网页）测试共用的假件。"""

import json
from collections.abc import Mapping
from datetime import UTC, date, datetime
from decimal import Decimal
from types import ModuleType
from typing import Any, cast
from uuid import uuid4

import pytest
from app.providers import eastmoney_boards as em
from app.providers.errors import ProviderError
from app.repositories.board_market import StoredSnapshot
from app.services.assistant.tools.base import ToolContext
from sqlalchemy import Engine

NOW = datetime(2026, 10, 21, 8, 0, tzinfo=UTC)  # 上海 2026-10-21 16:00，周三
TODAY = date(2026, 10, 21)


def context(now: datetime = NOW) -> ToolContext:
    return ToolContext(engine=cast(Engine, None), user_id=uuid4(), now=now)


def snapshot(
    code: str, name: str, kind: em.BoardKind = "industry", level: int = 2, day: date = TODAY
) -> StoredSnapshot:
    return StoredSnapshot(
        node=em.BoardNode(code=code, name=name, kind=kind, level=level),
        quote=em.BoardQuote(
            code=code,
            name=name,
            last=Decimal("1234.5"),
            change_pct=Decimal("1.25"),
            change_5d_pct=None,
            change_10d_pct=None,
            change_60d_pct=None,
            change_ytd_pct=None,
            amount=None,
            turnover_pct=None,
            pe_ttm=None,
            market_cap=None,
            main_net_inflow=None,
            main_net_inflow_5d=None,
            advancers=None,
            decliners=None,
            unchanged=None,
            leader_code=None,
            leader_name=None,
            leader_change_pct=None,
        ),
        trade_date=day,
        fetched_at=NOW,
    )


class FakeBoardRepo:
    """假的板块仓库：`search_latest` 按关键词返回预置的快照，并记下被问了什么。"""

    data: dict[str, list[StoredSnapshot]] = {}
    asked: list[tuple[str, date, tuple[str, ...]]] = []

    def __init__(self, engine: object) -> None:
        del engine

    def search_latest(
        self, query: str, on_or_before: date, kinds: list[str], limit: int
    ) -> list[StoredSnapshot]:
        type(self).asked.append((query, on_or_before, tuple(kinds)))
        found = [
            item
            for item in type(self).data.get(query, [])
            if item.node.kind in kinds and item.trade_date <= on_or_before
        ]
        return found[:limit]


def install_boards(
    monkeypatch: pytest.MonkeyPatch, module: ModuleType, data: dict[str, list[StoredSnapshot]]
) -> type[FakeBoardRepo]:
    """把工具模块里的 `BoardMarketRepository` 换成假的；每个测试一份独立的数据。"""

    repo = type("InstalledBoardRepo", (FakeBoardRepo,), {"data": data, "asked": []})
    monkeypatch.setattr(module, "BoardMarketRepository", repo)
    return repo


class ScriptedGateway:
    """按网址片段回放预置响应的假网关：值是 bytes 就返回，是异常就抛出；记下所有请求。"""

    def __init__(self, routes: Mapping[str, bytes | Exception]) -> None:
        self.routes = dict(routes)
        self.urls: list[str] = []

    async def get(self, url: str, headers: Mapping[str, str]) -> bytes:
        self.urls.append(url)
        for fragment, response in self.routes.items():
            if fragment in url:
                if isinstance(response, Exception):
                    raise response
                return response
        raise ProviderError(code="UPSTREAM_ERROR", message="没有预置的响应", status_code=502)

    async def post(self, url: str, body: bytes, headers: Mapping[str, str]) -> bytes:
        raise NotImplementedError


def upstream_down(message: str = "上游连接失败") -> ProviderError:
    return ProviderError(code="UPSTREAM_ERROR", message=message, status_code=502)


def as_json(document: Any) -> bytes:
    return json.dumps(document, ensure_ascii=False).encode()
