from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.api.responses import success_response
from app.core.config import get_settings
from app.services.llm_gateway import model_availability

router = APIRouter(prefix="/analysis", tags=["分析"])


class ModelStatusData(BaseModel):
    available: bool
    message: str


@router.get("/model-status", summary="模型是否已配置")
def model_status(request: Request) -> JSONResponse:
    settings = get_settings()
    available, message = model_availability(
        api_key=settings.llm_api_key,
        base_url=settings.llm_api_base_url,
        model=settings.llm_model,
    )
    return JSONResponse(
        status_code=200,
        content=success_response(
            request,
            ModelStatusData(available=available, message=message),
        ),
    )
