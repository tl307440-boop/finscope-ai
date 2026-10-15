from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import Engine

from app.api.deps import get_current_user
from app.api.responses import success_response
from app.core.exceptions import AppError
from app.repositories.watchlists import WatchGroup, WatchlistRepository
from app.services.auth_types import UserRecord

router = APIRouter(prefix="/watchlists", tags=["自选"])


class GroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=40)


class ItemCreate(BaseModel):
    instrument_id: UUID


class ItemData(BaseModel):
    instrument_id: UUID
    display_symbol: str
    name_cn: str


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
        items=[
            ItemData(
                instrument_id=item.instrument_id,
                display_symbol=item.display_symbol,
                name_cn=item.name_cn,
            )
            for item in group.items
        ],
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


@router.delete("/{group_id}/items/{instrument_id}", summary="移出自选")
def remove_watch_item(
    group_id: UUID,
    instrument_id: UUID,
    request: Request,
    user: Annotated[UserRecord, Depends(get_current_user)],
) -> JSONResponse:
    removed = WatchlistRepository(_engine(request)).remove_item(user.id, group_id, instrument_id)
    if removed is None:
        raise AppError(code="NOT_FOUND", message="找不到这个分组", status_code=404)
    return JSONResponse(
        status_code=200,
        content=success_response(request, GroupData(id=group_id, name="", items=[])),
    )
