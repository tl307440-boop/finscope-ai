from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True)
class HeartbeatSnapshot:
    status: str
    beat_at: datetime
    pid: int


@dataclass(frozen=True)
class ProbeResult:
    status: str
    reason: str | None = None
    latency_ms: int | None = None
    last_seen_at: datetime | None = None


def evaluate_heartbeat(
    snapshot: HeartbeatSnapshot | None,
    *,
    now: datetime,
    ttl_seconds: int,
    future_skew_seconds: int = 5,
) -> ProbeResult:
    """根据已读出的心跳判断 Worker 是否可接受。不访问数据库。"""
    if snapshot is None:
        return ProbeResult(status="down", reason="heartbeat_missing")

    beat_at = snapshot.beat_at
    if beat_at.tzinfo is None:
        beat_at = beat_at.replace(tzinfo=UTC)
    now_utc = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
    age_seconds = (now_utc - beat_at).total_seconds()

    if age_seconds < -future_skew_seconds:
        return ProbeResult(status="down", reason="heartbeat_invalid", last_seen_at=beat_at)
    if snapshot.status != "alive":
        return ProbeResult(status="down", reason="heartbeat_stopped", last_seen_at=beat_at)
    if age_seconds > ttl_seconds:
        return ProbeResult(status="down", reason="heartbeat_stale", last_seen_at=beat_at)
    return ProbeResult(status="up", last_seen_at=beat_at)
