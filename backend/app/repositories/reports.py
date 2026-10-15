import uuid
from dataclasses import dataclass
from datetime import date
from uuid import UUID

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.db.models.analysis import AnalysisRun, Report
from app.services.filing_report import CALC_VERSION, ReportDraft
from app.services.llm_gateway import PROMPT_VERSION
from app.utils.time import utc_now


@dataclass(frozen=True)
class SavedReport:
    run_id: UUID
    report_id: UUID
    status: str
    title: str
    message: str
    body: str
    as_of: date


class ReportRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def save(
        self,
        *,
        user_id: UUID,
        instrument_id: UUID,
        symbol: str,
        as_of: date,
        period_a: date,
        period_b: date,
        draft: ReportDraft,
    ) -> SavedReport:
        now = utc_now()
        run_id = uuid.uuid4()
        report_id = uuid.uuid4()
        with Session(self.engine) as session, session.begin():
            session.add(
                AnalysisRun(
                    id=run_id,
                    user_id=user_id,
                    analysis_type="company_filing_review",
                    target={
                        "instrument_id": str(instrument_id),
                        "symbol": symbol,
                        "period_a": period_a.isoformat(),
                        "period_b": period_b.isoformat(),
                    },
                    as_of=as_of,
                    status=draft.status,
                    input_snapshot={
                        "fact_ids": [str(item) for item in draft.fact_ids],
                        "chunk_ids": [str(item) for item in draft.chunk_ids],
                    },
                    result={"message": draft.message},
                    model_name=draft.model_name,
                    prompt_version=PROMPT_VERSION,
                    calc_version=CALC_VERSION,
                    started_at=now,
                    finished_at=now,
                    error_message=None if draft.status == "succeeded" else draft.message,
                    token_usage={"prompt_tokens": 0, "completion_tokens": 0},
                )
            )
            session.add(
                Report(
                    id=report_id,
                    user_id=user_id,
                    run_id=run_id,
                    report_type="filing_review",
                    title=draft.title,
                    body_markdown=draft.body,
                    summary_json={"message": draft.message},
                    report_as_of=as_of,
                    created_at=now,
                    version=1,
                )
            )
        return SavedReport(
            run_id=run_id,
            report_id=report_id,
            status=draft.status,
            title=draft.title,
            message=draft.message,
            body=draft.body,
            as_of=as_of,
        )

    def list_owned(self, user_id: UUID, query: str) -> list[SavedReport]:
        needle = query.strip()
        with Session(self.engine) as session:
            statement = select(Report).where(
                Report.user_id == user_id,
                Report.deleted_at.is_(None),
            )
            if needle:
                statement = statement.where(Report.title.contains(needle))
            rows = session.scalars(statement.order_by(Report.created_at.desc())).all()
            saved: list[SavedReport] = []
            for report in rows:
                run = session.get(AnalysisRun, report.run_id)
                if run is None:
                    continue
                saved.append(_saved(run, report))
            return saved

    def hide(self, user_id: UUID, report_id: UUID) -> bool | None:
        with Session(self.engine) as session, session.begin():
            report = session.get(Report, report_id)
            if report is None or report.user_id != user_id:
                return None
            if report.deleted_at is not None:
                return False
            report.deleted_at = utc_now()
            return True

    def get_owned(self, user_id: UUID, run_id: UUID) -> SavedReport | None:
        with Session(self.engine) as session:
            run = session.get(AnalysisRun, run_id)
            if run is None or run.user_id != user_id:
                return None
            report = session.scalars(select(Report).where(Report.run_id == run_id)).one_or_none()
            if report is None or report.user_id != user_id or report.deleted_at is not None:
                return None
            return _saved(run, report)


def _saved(run: AnalysisRun, report: Report) -> SavedReport:
    return SavedReport(
        run_id=run.id,
        report_id=report.id,
        status=run.status,
        title=report.title,
        message=run.error_message or _message(run.result),
        body=report.body_markdown,
        as_of=report.report_as_of,
    )


def _message(result: dict[str, object]) -> str:
    value = result.get("message")
    return value if isinstance(value, str) else ""
