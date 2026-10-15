from datetime import datetime

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.db.models.worker_heartbeat import WorkerHeartbeat
from app.services.heartbeat_rules import HeartbeatSnapshot


class WorkerHeartbeatRepository:
    def __init__(self, engine: Engine, service_name: str) -> None:
        self.engine = engine
        self.service_name = service_name

    def upsert(self, *, status: str, beat_at: datetime, pid: int) -> None:
        with Session(self.engine) as session:
            row = session.get(WorkerHeartbeat, self.service_name)
            if row is None:
                session.add(
                    WorkerHeartbeat(
                        service_name=self.service_name,
                        status=status,
                        beat_at=beat_at,
                        pid=pid,
                    )
                )
            else:
                row.status = status
                row.beat_at = beat_at
                row.pid = pid
            session.commit()

    def fetch(self) -> HeartbeatSnapshot | None:
        with Session(self.engine) as session:
            row = session.get(WorkerHeartbeat, self.service_name)
            if row is None:
                return None
            return HeartbeatSnapshot(status=row.status, beat_at=row.beat_at, pid=row.pid)
