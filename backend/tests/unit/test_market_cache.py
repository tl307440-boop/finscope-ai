import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime

import pytest
from app.providers.errors import ProviderError
from app.services import cockpit
from app.services.market_cache import PacedGateway, TtlCache, close_gateways, market_cache
from tests.unit.test_cockpit_service import AFTER_CLOSE, FakeGateway, _service


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _failure() -> ProviderError:
    return ProviderError(code="UPSTREAM_TIMEOUT", message="上游超时", status_code=504)


def test_plain_cache_still_blocks_after_expiry() -> None:
    clock = Clock()
    cache = TtlCache(clock)
    calls = 0

    async def loader() -> int:
        nonlocal calls
        calls += 1
        return calls

    async def run() -> list[int]:
        first = await cache.get("k", 10, loader)
        clock.now += 11
        second = await cache.get("k", 10, loader)
        return [first.value, second.value]

    assert asyncio.run(run()) == [1, 2]


def test_expired_value_inside_grace_is_served_immediately_and_refreshed_once() -> None:
    clock = Clock()
    cache = TtlCache(clock, grace_seconds=600)
    calls = 0

    async def run() -> None:
        nonlocal calls
        release = asyncio.Event()

        async def loader() -> int:
            nonlocal calls
            calls += 1
            if calls > 1:
                await release.wait()
            return calls

        assert (await cache.get("k", 10, loader)).value == 1
        clock.now += 11

        # 回源被卡住时，请求也不能被卡住；并发多次只起一个后台刷新。
        served = await asyncio.wait_for(
            asyncio.gather(*(cache.get("k", 10, loader) for _ in range(5))), timeout=1
        )
        assert all(item.value == 1 and not item.stale and item.refreshing for item in served)
        await asyncio.sleep(0)
        assert calls == 2

        release.set()
        await asyncio.sleep(0.01)
        fresh = await cache.get("k", 10, loader)
        assert (fresh.value, fresh.refreshing, fresh.stale) == (2, False, False)
        assert calls == 2

    asyncio.run(run())


def test_value_past_grace_blocks_for_a_refresh() -> None:
    clock = Clock()
    cache = TtlCache(clock, grace_seconds=60)
    calls = 0

    async def loader() -> int:
        nonlocal calls
        calls += 1
        return calls

    async def run() -> int:
        await cache.get("k", 10, loader)
        clock.now += 10 + 61
        return (await cache.get("k", 10, loader)).value

    assert asyncio.run(run()) == 2


def test_failed_background_refresh_is_reported_as_stale_and_backs_off() -> None:
    clock = Clock()
    cache = TtlCache(clock, grace_seconds=600, failure_backoff_seconds=15)
    calls = 0
    healthy = True

    async def loader() -> int:
        nonlocal calls
        calls += 1
        if not healthy:
            raise _failure()
        return calls

    async def run() -> None:
        nonlocal healthy
        await cache.get("k", 10, loader)
        healthy = False
        clock.now += 11
        await cache.get("k", 10, loader)  # 宽限期内：先给旧值，后台刷新失败
        await asyncio.sleep(0.01)
        assert calls == 2

        failed = await cache.get("k", 10, loader)
        assert failed.stale and failed.stale_reason == "上游超时"
        assert calls == 2  # 退避期内不再打上游

        clock.now += 16
        healthy = True
        await cache.get("k", 10, loader)
        await asyncio.sleep(0.01)
        recovered = await cache.get("k", 10, loader)
        assert (recovered.value, recovered.stale) == (3, False)

    asyncio.run(run())


def test_blocking_failure_with_old_value_backs_off_instead_of_hammering_upstream() -> None:
    clock = Clock()
    cache = TtlCache(clock, failure_backoff_seconds=15)
    calls = 0
    healthy = True

    async def loader() -> str:
        nonlocal calls
        calls += 1
        if not healthy:
            raise _failure()
        return "ok"

    async def run() -> None:
        nonlocal healthy
        await cache.get("k", 10, loader)
        healthy = False
        clock.now += 11
        results = [await cache.get("k", 10, loader) for _ in range(4)]
        assert all(item.stale and item.value == "ok" for item in results)
        assert calls == 2  # 第一次真的失败了一回，之后 15 秒内都直接给旧值

    asyncio.run(run())


def test_failure_without_any_value_still_raises() -> None:
    cache = TtlCache(grace_seconds=600, failure_backoff_seconds=15)

    async def loader() -> int:
        raise _failure()

    with pytest.raises(ProviderError):
        asyncio.run(cache.get("k", 10, loader))


def test_unexpected_error_in_background_refresh_does_not_leak() -> None:
    clock = Clock()
    cache = TtlCache(clock, grace_seconds=600, failure_backoff_seconds=15)
    broken = False

    async def loader() -> int:
        if broken:
            raise ValueError("解析炸了")
        return 1

    async def run() -> None:
        nonlocal broken
        await cache.get("k", 10, loader)
        broken = True
        clock.now += 11
        await cache.get("k", 10, loader)
        await asyncio.sleep(0.01)
        assert (await cache.get("k", 10, loader)).stale

    asyncio.run(run())


def test_market_cache_factory_enables_grace_and_backoff() -> None:
    cache = market_cache()
    assert cache._grace == 600 and cache._backoff == 10


def test_paced_gateway_reuses_one_client_per_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[dict[str, object]] = []

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            created.append(kwargs)
            self.closed = False

        async def aclose(self) -> None:
            self.closed = True

    async def call(_: object) -> bytes:
        return b"ok"

    monkeypatch.setattr("app.services.market_cache.httpx.AsyncClient", FakeClient)

    async def run() -> None:
        gateway = PacedGateway(min_interval=0, concurrency=2)
        await asyncio.gather(*(gateway._send(call) for _ in range(5)))
        assert len(created) == 1
        assert created[0]["trust_env"] is False
        raw = gateway._raw
        assert isinstance(raw, FakeClient)
        await gateway.aclose()
        assert raw.closed

    asyncio.run(run())


def test_slow_host_does_not_block_other_hosts(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeClient:
        def __init__(self, **_: object) -> None:
            pass

    monkeypatch.setattr("app.services.market_cache.httpx.AsyncClient", FakeClient)

    async def stuck(_: object) -> bytes:
        await asyncio.sleep(0.3)
        return b"slow"

    async def quick(_: object) -> bytes:
        return b"fast"

    async def run() -> float:
        gateway = PacedGateway(min_interval=0, concurrency=1)
        slow = asyncio.ensure_future(gateway._send(stuck, "dead.example"))
        await asyncio.sleep(0.01)
        started = asyncio.get_running_loop().time()
        assert await gateway._send(quick, "healthy.example") == b"fast"
        elapsed = asyncio.get_running_loop().time() - started
        await slow
        return elapsed

    assert asyncio.run(run()) < 0.1


def test_paced_gateway_enforces_an_overall_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeClient:
        def __init__(self, **_: object) -> None:
            pass

    monkeypatch.setattr("app.services.market_cache.httpx.AsyncClient", FakeClient)

    async def never(_: object) -> bytes:
        await asyncio.sleep(10)
        return b""

    async def run() -> None:
        gateway = PacedGateway(min_interval=0, timeout=0.05)
        gateway._deadline = 0.1
        with pytest.raises(ProviderError) as caught:
            await gateway._send(never, "slow.example")
        assert caught.value.code == "UPSTREAM_TIMEOUT"

        # 槽位已经释放：同主机的下一个请求不会被卡死。
        async def ok(_: object) -> bytes:
            return b"ok"

        assert await gateway._send(ok, "slow.example") == b"ok"

    asyncio.run(run())


def test_failure_without_old_value_is_remembered_briefly() -> None:
    clock = Clock()
    cache = TtlCache(clock, failure_backoff_seconds=10)
    calls = 0
    healthy = False

    async def loader() -> int:
        nonlocal calls
        calls += 1
        if not healthy:
            await asyncio.sleep(0.05)
            raise _failure()
        return 7

    async def run() -> None:
        nonlocal healthy
        # 五个并发请求只会真的回源一次，其余直接复述失败。
        results = await asyncio.gather(
            *(cache.get("k", 10, loader) for _ in range(5)), return_exceptions=True
        )
        assert all(isinstance(item, ProviderError) for item in results)
        assert calls == 1
        with pytest.raises(ProviderError):
            await cache.get("k", 10, loader)
        assert calls == 1

        clock.now += 11
        healthy = True
        assert (await cache.get("k", 10, loader)).value == 7
        assert calls == 2

    asyncio.run(run())


def test_close_gateways_tolerates_clients_from_other_loops() -> None:
    gateway = PacedGateway(min_interval=0)

    async def call(_: object) -> bytes:
        return b"ok"

    async def use() -> None:
        await gateway._send(call)

    asyncio.run(use())
    asyncio.run(close_gateways())


def test_warm_interval_is_short_only_during_trading_hours() -> None:
    def at(day: int, hour: int, minute: int) -> datetime:
        # 北京时间 = UTC + 8
        return datetime(2026, 10, day, hour - 8, minute, tzinfo=UTC)

    assert cockpit.warm_interval(at(20, 10, 30)) == 30  # 周二盘中
    assert cockpit.warm_interval(at(20, 8, 0)) == 300  # 盘前
    assert cockpit.warm_interval(at(20, 16, 0)) == 300  # 盘后
    assert cockpit.warm_interval(at(24, 10, 30)) == 300  # 周六


class SlowGateway(FakeGateway):
    """每次上游调用固定耗时，用来区分「串行」与「并发」。"""

    delay = 0.1

    async def get(self, url: str, headers: Mapping[str, str]) -> bytes:
        await asyncio.sleep(self.delay)
        return await super().get(url, headers)


def test_market_overview_fetches_upstreams_concurrently() -> None:
    service = _service(SlowGateway(), now=AFTER_CLOSE)

    async def run() -> float:
        started = asyncio.get_running_loop().time()
        await service.market()
        return asyncio.get_running_loop().time() - started

    # 指数快照、涨跌分布、涨停池（再带炸板 / 跌停）、三条日K 共 8 次上游调用：
    # 串行至少 0.8s；并发只取决于最长的依赖链（涨停池 → 炸板 / 跌停，约 0.2s）。
    assert asyncio.run(run()) < 0.5


def test_warm_market_fills_every_cache_the_endpoints_read() -> None:
    gateway = FakeGateway()
    service = _service(gateway, now=AFTER_CLOSE)

    async def run() -> None:
        await service.warm_market()
        gateway.fail = {""}  # 预热之后任何上游都不可用，页面仍应完整出数
        market, boards, announcements = await asyncio.gather(
            service.market(), service.boards(), service.announcements()
        )
        assert market.quotes_section.status == "ok"
        assert market.breadth_section.status == "ok"
        assert market.history_section.status == "ok"
        assert announcements.section.status != "unavailable"
        assert boards.heat_section.status == "unavailable"  # FakeGateway 没配置同花顺

    asyncio.run(run())
