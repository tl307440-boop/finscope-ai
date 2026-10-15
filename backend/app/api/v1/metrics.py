from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.api.responses import success_response
from app.core.exceptions import AppError
from app.schemas.metrics import EvaluationData, QuarterRequest, evaluate_request

router = APIRouter(prefix="/metrics", tags=["财务指标"])


@router.post("/evaluations", summary="试算单季和市盈率")
def evaluate_metrics(body: QuarterRequest, request: Request) -> JSONResponse:
    try:
        payload: EvaluationData = evaluate_request(body)
    except ValueError as exc:
        raise AppError(code="VALIDATION_ERROR", message="金额无法解析", status_code=422) from exc
    return JSONResponse(status_code=200, content=success_response(request, payload))
