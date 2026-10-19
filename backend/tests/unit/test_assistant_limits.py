from datetime import UTC, datetime
from uuid import uuid4

from app.services.assistant.limits import AssistantLimiter, daily_denial, shanghai_day_start


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_one_turn_per_user_at_a_time() -> None:
    limiter = AssistantLimiter(per_minute=6, workers=4)
    user = uuid4()
    assert limiter.try_start(user) is None
    denied = limiter.try_start(user)
    assert denied is not None and "上一条" in denied
    limiter.finish(user)
    assert limiter.try_start(user) is None


def test_worker_cap_rejects_instead_of_queueing() -> None:
    limiter = AssistantLimiter(per_minute=6, workers=2)
    first, second, third = uuid4(), uuid4(), uuid4()
    assert limiter.try_start(first) is None
    assert limiter.try_start(second) is None
    denied = limiter.try_start(third)
    assert denied is not None and "已满" in denied
    assert limiter.active_count() == 2
    limiter.finish(first)
    assert limiter.try_start(third) is None


def test_per_minute_window_slides_and_tells_how_long_to_wait() -> None:
    clock = FakeClock()
    limiter = AssistantLimiter(per_minute=2, workers=4, clock=clock)
    user = uuid4()
    for _ in range(2):
        assert limiter.try_start(user) is None
        limiter.finish(user)
        clock.now += 10
    denied = limiter.try_start(user)
    assert denied is not None and "2 次" in denied and "40 秒" in denied
    clock.now += 41
    assert limiter.try_start(user) is None


def test_a_denied_attempt_takes_no_slot_and_uses_no_quota() -> None:
    limiter = AssistantLimiter(per_minute=2, workers=4)
    user = uuid4()
    assert limiter.try_start(user) is None
    assert limiter.try_start(user) is not None  # 并发被拒，不能算一次提问
    limiter.finish(user)
    assert limiter.try_start(user) is None  # 若被拒的那次也计数，这里会超过每分钟 2 次


def test_users_do_not_share_the_minute_quota() -> None:
    limiter = AssistantLimiter(per_minute=1, workers=4)
    first, second = uuid4(), uuid4()
    assert limiter.try_start(first) is None
    limiter.finish(first)
    assert limiter.try_start(first) is not None
    assert limiter.try_start(second) is None


def test_daily_limit_boundary_and_message() -> None:
    assert daily_denial(199, 200) is None
    denied = daily_denial(200, 200)
    assert denied is not None and "200" in denied and "零点" in denied


def test_day_starts_at_shanghai_midnight_not_utc_midnight() -> None:
    # 北京时间 10-20 01:00，日历日是 10-20，零点是 UTC 10-19 16:00。
    assert shanghai_day_start(datetime(2026, 10, 19, 17, 0, tzinfo=UTC)) == datetime(
        2026, 10, 19, 16, 0, tzinfo=UTC
    )
    # 北京时间 10-19 23:59，日历日仍是 10-19。
    assert shanghai_day_start(datetime(2026, 10, 19, 15, 59, tzinfo=UTC)) == datetime(
        2026, 10, 18, 16, 0, tzinfo=UTC
    )
