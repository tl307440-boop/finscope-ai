from datetime import UTC, datetime, timedelta

from app.services.heartbeat_rules import HeartbeatSnapshot, evaluate_heartbeat

NOW = datetime(2026, 10, 15, 8, 0, tzinfo=UTC)
TTL = 30


def test_missing_heartbeat_is_down() -> None:
    result = evaluate_heartbeat(None, now=NOW, ttl_seconds=TTL)
    assert result.status == "down"
    assert result.reason == "heartbeat_missing"
    assert result.last_seen_at is None


def test_fresh_alive_heartbeat_is_up() -> None:
    beat = NOW - timedelta(seconds=5)
    result = evaluate_heartbeat(
        HeartbeatSnapshot(status="alive", beat_at=beat, pid=9),
        now=NOW,
        ttl_seconds=TTL,
    )
    assert result.status == "up"
    assert result.reason is None
    assert result.last_seen_at == beat


def test_heartbeat_at_ttl_boundary_stays_up() -> None:
    beat = NOW - timedelta(seconds=TTL)
    result = evaluate_heartbeat(
        HeartbeatSnapshot(status="alive", beat_at=beat, pid=9),
        now=NOW,
        ttl_seconds=TTL,
    )
    assert result.status == "up"


def test_expired_heartbeat_is_stale() -> None:
    beat = NOW - timedelta(seconds=TTL, milliseconds=1)
    result = evaluate_heartbeat(
        HeartbeatSnapshot(status="alive", beat_at=beat, pid=9),
        now=NOW,
        ttl_seconds=TTL,
    )
    assert result.status == "down"
    assert result.reason == "heartbeat_stale"
    assert result.last_seen_at == beat


def test_stopped_heartbeat_is_not_ready() -> None:
    result = evaluate_heartbeat(
        HeartbeatSnapshot(status="stopped", beat_at=NOW, pid=9),
        now=NOW,
        ttl_seconds=TTL,
    )
    assert result.status == "down"
    assert result.reason == "heartbeat_stopped"


def test_future_heartbeat_beyond_skew_is_invalid() -> None:
    result = evaluate_heartbeat(
        HeartbeatSnapshot(status="alive", beat_at=NOW + timedelta(seconds=6), pid=9),
        now=NOW,
        ttl_seconds=TTL,
    )
    assert result.status == "down"
    assert result.reason == "heartbeat_invalid"


def test_small_clock_skew_is_accepted() -> None:
    result = evaluate_heartbeat(
        HeartbeatSnapshot(status="alive", beat_at=NOW + timedelta(seconds=4), pid=9),
        now=NOW,
        ttl_seconds=TTL,
    )
    assert result.status == "up"


def test_naive_timestamp_is_treated_as_utc() -> None:
    beat = datetime(2026, 10, 15, 7, 59, 50)
    result = evaluate_heartbeat(
        HeartbeatSnapshot(status="alive", beat_at=beat, pid=9),
        now=NOW,
        ttl_seconds=TTL,
    )
    assert result.status == "up"
