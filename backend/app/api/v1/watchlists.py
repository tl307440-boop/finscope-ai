import asyncio
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import Engine

from app.api.deps import get_current_user
from app.api.responses import success_response
from app.core.exceptions import AppError
from app.repositories.watchlists import (
    ExternalTarget,
    WatchGroup,
    WatchItem,
    WatchlistRepository,
)
from app.schemas.watchlist_insights import ImportResult
from app.services.auth_types import UserRecord
from app.services.watchlist_insights import (
    MAX_IMPORT,
    WatchlistInsightsService,
    get_insights_service,
    parse_targets,
)
from app.utils.watch_secid import a_share_secid, display_symbol, kind_label, kind_of_secid

router = APIRouter(prefix="/watchlists", tags=["自选"])


class GroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=40)


class ItemCreate(BaseModel):
    instrument_id: UUID


class ItemData(BaseModel):
    # item_id 是这一行自选的 id，移出自选用它；外部标的（板块、指数、美股……）没有 instrument_id。
    item_id: UUID | None = None
    instrument_id: UUID | None = None
    display_symbol: str
    name_cn: str
    secid: str | None = None
    kind: str = "other"
    kind_label: str = "其他"


class GroupData(BaseModel):
    id: UUID
    name: str
    items: list[ItemData]


class GroupList(BaseModel):
    groups: list[GroupData]


def _engine(request: Request) -> Engine:
    return cast(Engine, request.app.state.engine)


def _group(group: WatchGroup) -> GroupData:
    return GroupData(
        id=group.id,
        name=group.name,
        items=[_item(item) for item in group.items],
    )


def _item(item: WatchItem) -> ItemData:
    kind = kind_of_secid(item.secid) if item.secid else "other"
    return ItemData(
        item_id=item.item_id,
        instrument_id=item.instrument_id,
        display_symbol=item.display_symbol,
        name_cn=item.name_cn,
        secid=item.secid,
        kind=kind,
        kind_label=kind_label(kind),
    )


@router.get("", summary="我的自选")
def list_watchlists(
    request: Request,
    user: Annotated[UserRecord, Depends(get_current_user)],
) -> JSONResponse:
    groups = WatchlistRepository(_engine(request)).list_groups(user.id)
    return JSONResponse(
        status_code=200,
        content=success_response(request, GroupList(groups=[_group(group) for group in groups])),
    )


@router.post("", summary="新建自选分组")
def create_watchlist(
    body: GroupCreate,
    request: Request,
    user: Annotated[UserRecord, Depends(get_current_user)],
) -> JSONResponse:
    try:
        created = WatchlistRepository(_engine(request)).create_group(user.id, body.name)
    except ValueError as exc:
        raise AppError(code="VALIDATION_ERROR", message=str(exc), status_code=422) from exc
    if created is None:
        raise AppError(code="CONFLICT", message="这个分组名称已经有了", status_code=409)
    return JSONResponse(status_code=200, content=success_response(request, _group(created)))


@router.post("/{group_id}/items", summary="加入自选")
def add_watch_item(
    group_id: UUID,
    body: ItemCreate,
    request: Request,
    user: Annotated[UserRecord, Depends(get_current_user)],
) -> JSONResponse:
    added = WatchlistRepository(_engine(request)).add_item(user.id, group_id, body.instrument_id)
    if added is None:
        raise AppError(code="NOT_FOUND", message="找不到这个分组", status_code=404)
    if not added:
        raise AppError(code="NOT_FOUND", message="暂无该证券", status_code=404)
    groups = WatchlistRepository(_engine(request)).list_groups(user.id)
    current = next((group for group in groups if group.id == group_id), None)
    if current is None:
        raise AppError(code="NOT_FOUND", message="找不到这个分组", status_code=404)
    return JSONResponse(status_code=200, content=success_response(request, _group(current)))


class BatchImport(BaseModel):
    text: str = Field(min_length=1, max_length=8000)


@router.post(
    "/{group_id}/items/batch", summary="粘贴代码批量加入自选（A 股、板块、指数、美股、基金…）"
)
async def import_watch_items(
    group_id: UUID,
    body: BatchImport,
    request: Request,
    user: Annotated[UserRecord, Depends(get_current_user)],
    service: Annotated[WatchlistInsightsService, Depends(get_insights_service)],
) -> JSONResponse:
    parsed = parse_targets(body.text)
    total = len(parsed.cn) + len(parsed.external)
    if not total:
        raise AppError(
            code="VALIDATION_ERROR",
            message="没有识别到代码，支持 600519、SH600519、600519.SH，"
            "以及东财写法 90.BK1630（板块）、105.NVDA（美股）、171.US10Y（国债收益率）",
            status_code=422,
        )
    if total > MAX_IMPORT:
        raise AppError(
            code="VALIDATION_ERROR", message=f"一次最多导入 {MAX_IMPORT} 只", status_code=422
        )
    repository = WatchlistRepository(_engine(request))
    outcome = await asyncio.to_thread(repository.add_codes, user.id, group_id, parsed.cn)
    if outcome is None:
        raise AppError(code="NOT_FOUND", message="找不到这个分组", status_code=404)
    # 本系统没有收录的 A 股（如北交所新股、ETF）不丢弃，改按东财代码保存。
    secids = [
        *parsed.external,
        *(a_share_secid(exchange, code) for exchange, code in outcome.missing_keys),
    ]
    names = await service.resolve_names(secids)
    external = await asyncio.to_thread(
        repository.add_external,
        user.id,
        group_id,
        [ExternalTarget(secid, names.get(secid)) for secid in secids],
    )
    if external is None:
        raise AppError(code="NOT_FOUND", message="找不到这个分组", status_code=404)
    saved = [label for label in external.added if label not in external.linked]
    result = ImportResult(
        added=[*outcome.added, *external.added],
        existing=[*outcome.existing, *external.existing],
        external=saved,
        unnamed=[
            label
            for label in (display_symbol(secid) for secid in secids if secid not in names)
            if label in saved
        ],
        invalid=parsed.invalid[:50],
    )
    return JSONResponse(status_code=200, content=success_response(request, result))


@router.get("/{group_id}/insights", summary="自选股看板：行情、行业暴露、涨停、调研、热度")
async def read_watch_insights(
    group_id: UUID,
    request: Request,
    user: Annotated[UserRecord, Depends(get_current_user)],
    service: Annotated[WatchlistInsightsService, Depends(get_insights_service)],
) -> JSONResponse:
    repository = WatchlistRepository(_engine(request))
    group = await asyncio.to_thread(repository.get_group, user.id, group_id)
    if group is None:
        raise AppError(code="NOT_FOUND", message="找不到这个分组", status_code=404)
    insights = await service.build(group, repository.recent_closes)
    # 导入时东财没返回名称的外部标的，现在取到了就记下来，列表页不再只显示代码。
    stored = {item.item_id: item for item in group.items if item.instrument_id is None}
    unnamed = {
        row.item_id: row.name
        for row in insights.others
        if row.item_id is not None
        and row.item_id in stored
        and stored[row.item_id].name_cn == stored[row.item_id].symbol
        and row.name != stored[row.item_id].symbol
    }
    if unnamed:
        await asyncio.to_thread(repository.fill_external_names, user.id, group_id, unnamed)
    return JSONResponse(status_code=200, content=success_response(request, insights))


@router.delete("/{group_id}/items/{item_ref}", summary="移出自选")
def remove_watch_item(
    group_id: UUID,
    item_ref: UUID,
    request: Request,
    user: Annotated[UserRecord, Depends(get_current_user)],
) -> JSONResponse:
    """item_ref 为自选条目 id（列表里的 item_id）或本系统证券 id（兼容旧调用）。"""

    removed = WatchlistRepository(_engine(request)).remove_item(user.id, group_id, item_ref)
    if removed is None:
        raise AppError(code="NOT_FOUND", message="找不到这个分组", status_code=404)
    return JSONResponse(
        status_code=200,
        content=success_response(request, GroupData(id=group_id, name="", items=[])),
    )
