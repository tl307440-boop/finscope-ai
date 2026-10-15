import threading
from datetime import UTC, datetime

from app.workers.main import serve, serve_with_jobs

NOW = datetime(2026, 10, 15, tzinfo=UTC)


class MemoryWriter:
    def __init__(self, fail_times: int = 0) -> None:
        self.fail_times = fail_times
        self.rows: list[str] = []

    def upsert(self, *, status: str, beat_at: datetime, pid: int) -> None:
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("postgresql://finscope:super-secret@db/finscope")
        assert beat_at == NOW
        assert pid == 7
        self.rows.append(status)


def _serve(writer: MemoryWriter, stop: threading.Event) -> None:
    timer = threading.Timer(2, stop.set)
    timer.start()
    try:
        serve(writer, stop, 0.01, lambda: NOW, 7)
    finally:
        timer.cancel()


def test_serve_writes_alive_then_stopped() -> None:
    writer = MemoryWriter()
    stop = threading.Event()
    original = writer.upsert

    def upsert(*, status: str, beat_at: datetime, pid: int) -> None:
        original(status=status, beat_at=beat_at, pid=pid)
        if status == "alive":
            stop.set()

    writer.upsert = upsert  # type: ignore[method-assign]
    _serve(writer, stop)
    assert writer.rows == ["alive", "stopped"]


def test_serve_keeps_running_after_a_failed_write() -> None:
    writer = MemoryWriter(fail_times=1)
    stop = threading.Event()
    attempts = {"count": 0}
    original = writer.upsert

    def upsert(*, status: str, beat_at: datetime, pid: int) -> None:
        attempts["count"] += 1
        try:
            original(status=status, beat_at=beat_at, pid=pid)
        finally:
            if attempts["count"] >= 2:
                stop.set()

    writer.upsert = upsert  # type: ignore[method-assign]
    _serve(writer, stop)
    assert writer.rows == ["alive", "stopped"]
    assert "super-secret" not in " ".join(writer.rows)


def test_job_pump_runs_inside_the_heartbeat_loop() -> None:
    writer = MemoryWriter()
    stop = threading.Event()
    pumped = {"count": 0}

    def pump(now: datetime) -> None:
        assert now == NOW
        pumped["count"] += 1
        stop.set()

    serve_with_jobs(writer, pump, stop, 0.01, lambda: NOW, 7)
    assert pumped["count"] == 1
    assert writer.rows == ["alive", "stopped"]
