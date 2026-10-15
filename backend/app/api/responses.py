from typing import Any

from fastapi import Request
from pydantic import BaseModel

from app.core.config import get_settings
from app.core.exceptions import request_id_from
from app.schemas.common import DEMO_NOTICE, ResponseMeta
from app.utils.time import utc_now


def success_response(request: Request, data: BaseModel) -> dict[str, Any]:
    settings = get_settings()
    meta = ResponseMeta(
        request_id=request_id_from(request),
        as_of=utc_now(),
        source="internal",
        stale=False,
        data_mode=settings.data_mode,
        notice=DEMO_NOTICE if settings.data_mode == "demo" else None,
    )
    return {
        "data": data.model_dump(mode="json", exclude_none=True),
        "meta": meta.model_dump(mode="json", exclude_none=True),
    }
