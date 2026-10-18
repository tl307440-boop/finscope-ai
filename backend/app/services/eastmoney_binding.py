"""东方财富账号绑定：扫码登录，把东财自选单向同步成本系统的自选分组。

- 扫码：服务端向东财要二维码并代为轮询，前端只拿我们自己的 login_id；扫码成功后
  登录 cookie 用 SecretBox 加密落库，明文不落库、不进日志，用户可随时解除绑定（立即删除）。
- 同步方向是「东财 → 本系统」。东财分组按分组 id 对应到本系统分组（没有就新建，
  同名未绑定的分组直接认领）；同步来的条目标记为 eastmoney，东财里删掉后才会在这里移除，
  用户手动加的条目永远不动。
- 安全原则：任何失败（东财不通、登录失效、某个分组读不到、读到空列表）
  都不删除已同步的数据。
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import Settings
from app.core.exceptions import AppError
from app.core.secret_box import SecretBox
from app.providers.eastmoney_account import (
    QR_TTL_SECONDS,
    EastmoneyAccount,
    EastmoneyLoginExpired,
    FavorGroup,
    QrLogin,
)
from app.providers.errors import ProviderError
from app.repositories.eastmoney_bindings import BindingRecord, EastmoneyBindingRepository
from app.repositories.watchlists import ExternalTarget, WatchlistRepository
from app.services.watchlist_insights import (
    WatchlistInsightsService,
    get_insights_service,
    parse_targets,
)
from app.utils.time import utc_now
from app.utils.watch_secid import a_share_secid

logger = logging.getLogger(__name__)

# 页面打开时每分钟会请求一次同步；服务端按这个最小间隔合并，避免频繁打东财。
MIN_SYNC_INTERVAL_SECONDS = 30
_MIN_QR_INTERVAL_SECONDS = 3
_MAX_PENDING = 50


@dataclass(frozen=True)
class SyncSummary:
    groups: int
    items: int
    added: int
    removed: int
    message: str
    synced_at: datetime | None = None
    skipped_groups: tuple[str, ...] = ()
    cached: bool = False


@dataclass(frozen=True)
class QrStart:
    login_id: str
    png_base64: str
    expires_in: int


@dataclass(frozen=True)
class QrState:
    state: str  # waiting | expired | success
    alias: str = ""
    sync: SyncSummary | None = None
    sync_error: str = ""


@dataclass
class _Pending:
    login_id: str
    login: QrLogin
    started: float = field(default_factory=time.monotonic)


class EastmoneyBindingService:
    def __init__(
        self,
        *,
        engine: Engine,
        account: EastmoneyAccount,
        insights: WatchlistInsightsService,
        box: SecretBox,
    ) -> None:
        self.bindings = EastmoneyBindingRepository(engine)
        self.watch = WatchlistRepository(engine)
        self.account = account
        self.insights = insights
        self.box = box
        self._pending: dict[UUID, _Pending] = {}
        self._locks: dict[UUID, asyncio.Lock] = {}

    # ---- 扫码 ----------------------------------------------------------------

    async def start_qr(self, user_id: UUID) -> QrStart:
        await self._purge_pending()
        previous = self._pending.get(user_id)
        if previous is not None and time.monotonic() - previous.started < _MIN_QR_INTERVAL_SECONDS:
            raise AppError(
                code="RATE_LIMITED", message="二维码刚生成，请稍等几秒再刷新", status_code=429
            )
        if previous is not None:
            await self._drop_pending(user_id)
        if len(self._pending) >= _MAX_PENDING:
            raise AppError(
                code="RATE_LIMITED", message="扫码绑定的人太多，请稍后再试", status_code=429
            )
        try:
            login = await self.account.start_qr()
        except ProviderError as exc:
            raise AppError(code=exc.code, message=exc.message, status_code=502) from exc
        pending = _Pending(login_id=secrets.token_urlsafe(16), login=login)
        self._pending[user_id] = pending
        return QrStart(
            login_id=pending.login_id,
            png_base64=base64.b64encode(login.png).decode("ascii"),
            expires_in=QR_TTL_SECONDS,
        )

    async def poll_qr(self, user_id: UUID, login_id: str) -> QrState:
        pending = self._pending.get(user_id)
        if pending is None or not secrets.compare_digest(pending.login_id, login_id):
            return QrState(state="expired")
        try:
            result = await self.account.poll_qr(pending.login)
        except ProviderError as exc:
            raise AppError(code=exc.code, message=exc.message, status_code=502) from exc
        if result.state == "expired":
            await self._drop_pending(user_id)
            return QrState(state="expired")
        if result.state != "success":
            return QrState(state="waiting")
        await self._drop_pending(user_id)
        sealed = self.box.seal(json.dumps(dict(result.cookies)))
        await asyncio.to_thread(
            self.bindings.upsert, user_id, result.uid or "unknown", result.alias, sealed
        )
        try:
            summary = await self.sync(user_id, force=True)
        except AppError as exc:
            return QrState(state="success", alias=result.alias, sync_error=exc.message)
        return QrState(state="success", alias=result.alias, sync=summary)

    async def _drop_pending(self, user_id: UUID) -> None:
        pending = self._pending.pop(user_id, None)
        if pending is not None:
            await pending.login.close()

    async def _purge_pending(self) -> None:
        for user_id in [key for key, item in self._pending.items() if item.login.expired]:
            await self._drop_pending(user_id)

    # ---- 状态与解绑 ------------------------------------------------------------

    def status(self, user_id: UUID) -> BindingRecord | None:
        return self.bindings.get(user_id)

    async def unbind(self, user_id: UUID) -> bool:
        await self._drop_pending(user_id)
        return await asyncio.to_thread(self.bindings.delete, user_id)

    # ---- 同步 ----------------------------------------------------------------

    async def sync(self, user_id: UUID, *, force: bool = False) -> SyncSummary:
        lock = self._locks.setdefault(user_id, asyncio.Lock())
        async with lock:
            binding = await asyncio.to_thread(self.bindings.get, user_id)
            if binding is None:
                raise AppError(code="NOT_FOUND", message="还没有绑定东方财富", status_code=404)
            if binding.status != "active":
                raise AppError(
                    code="EASTMONEY_LOGIN_EXPIRED",
                    message="东财登录已失效，请重新扫码",
                    status_code=409,
                )
            if not force and _recent(binding.last_sync_at):
                return SyncSummary(
                    groups=binding.last_sync_groups or 0,
                    items=binding.last_sync_items or 0,
                    added=0,
                    removed=0,
                    message=binding.last_sync_message or "",
                    synced_at=binding.last_sync_at,
                    cached=True,
                )
            cookies = self._cookies(binding)
            if cookies is None:
                await asyncio.to_thread(
                    self.bindings.mark_expired, user_id, "登录凭证无法解密，请重新扫码绑定"
                )
                raise AppError(
                    code="EASTMONEY_LOGIN_EXPIRED",
                    message="登录凭证无法解密，请重新扫码绑定",
                    status_code=409,
                )
            try:
                groups = await self.account.fetch_groups(cookies)
            except EastmoneyLoginExpired as exc:
                await asyncio.to_thread(self.bindings.mark_expired, user_id, exc.message)
                raise AppError(code=exc.code, message=exc.message, status_code=409) from exc
            except ProviderError as exc:
                await asyncio.to_thread(self.bindings.mark_failed, user_id, exc.message)
                raise AppError(code=exc.code, message=exc.message, status_code=502) from exc
            summary = await self._apply(user_id, groups)
            await asyncio.to_thread(
                self.bindings.mark_synced,
                user_id,
                message=summary.message,
                groups=summary.groups,
                items=summary.items,
            )
            return summary

    async def sync_all(self) -> int:
        """后台定时：逐个用户同步。单个用户失败不影响其他人。返回成功的用户数。"""

        done = 0
        for binding in await asyncio.to_thread(self.bindings.list_active):
            try:
                await self.sync(binding.user_id)
                done += 1
            except AppError as exc:
                logger.info("eastmoney sync skipped code=%s", exc.code)
            except Exception:
                logger.warning("eastmoney sync failed error_type=unexpected")
            await asyncio.sleep(2)
        return done

    async def run_forever(self, interval_seconds: float) -> None:
        await asyncio.sleep(30)
        while True:
            try:
                await self.sync_all()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("eastmoney sync loop failed error_type=unexpected")
            await asyncio.sleep(interval_seconds)

    def _cookies(self, binding: BindingRecord) -> dict[str, str] | None:
        text = self.box.open(binding.cookies_enc)
        if text is None:
            return None
        try:
            data = json.loads(text)
        except ValueError:
            return None
        if not isinstance(data, dict):
            return None
        return {str(key): str(value) for key, value in data.items()}

    async def _apply(self, user_id: UUID, groups: list[FavorGroup]) -> SyncSummary:
        added = removed = items = synced = 0
        skipped: list[str] = []
        for group in groups:
            if group.secids is None:
                skipped.append(group.name)
                continue
            try:
                outcome = await self._apply_group(user_id, group.gid, group.name, group.secids)
            except SQLAlchemyError:
                logger.warning("eastmoney group apply failed error_type=db")
                skipped.append(group.name)
                continue
            added += outcome[0]
            removed += outcome[1]
            items += len(group.secids)
            synced += 1
        message = f"已同步 {synced} 个分组、{items} 个标的；新增 {added}、移除 {removed}"
        if skipped:
            message += f"；{len(skipped)} 个分组读取失败，保持原样"
        return SyncSummary(
            groups=synced,
            items=items,
            added=added,
            removed=removed,
            message=message,
            skipped_groups=tuple(skipped),
        )

    async def _apply_group(
        self, user_id: UUID, gid: str, name: str, secids: tuple[str, ...]
    ) -> tuple[int, int]:
        group_id, _created = await asyncio.to_thread(
            self.watch.ensure_eastmoney_group, user_id, gid, name
        )
        parsed = parse_targets(" ".join(secids))
        cn = await asyncio.to_thread(
            self.watch.add_codes, user_id, group_id, parsed.cn, source="eastmoney"
        )
        if cn is None:
            return 0, 0
        external_secids = [
            *parsed.external,
            *(a_share_secid(exchange, code) for exchange, code in cn.missing_keys),
        ]
        present = await asyncio.to_thread(self.watch.group_ext_secids, user_id, group_id)
        fresh = [secid for secid in external_secids if secid not in present]
        names = await self.insights.resolve_names(fresh) if fresh else {}
        external = await asyncio.to_thread(
            self.watch.add_external,
            user_id,
            group_id,
            [ExternalTarget(secid, names.get(secid)) for secid in external_secids],
            source="eastmoney",
        )
        added = len(cn.added) + (len(external.added) if external is not None else 0)
        removed = 0
        if secids:
            keep = {
                *(a_share_secid(exchange, code) for exchange, code in parsed.cn),
                *external_secids,
            }
            removed = await asyncio.to_thread(
                self.watch.remove_stale_synced, user_id, group_id, keep
            )
        return added, removed


def _recent(moment: datetime | None) -> bool:
    if moment is None:
        return False
    return (utc_now() - moment).total_seconds() < MIN_SYNC_INTERVAL_SECONDS


def build_service(engine: Engine, settings: Settings) -> EastmoneyBindingService:
    return EastmoneyBindingService(
        engine=engine,
        account=EastmoneyAccount(),
        insights=get_insights_service(),
        box=SecretBox(settings.settings_encryption_key or settings.jwt_secret),
    )
