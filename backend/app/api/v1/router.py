from fastapi import APIRouter

from app.api.v1.admin import router as admin_router
from app.api.v1.analysis import router as analysis_router
from app.api.v1.assistant import router as assistant_router
from app.api.v1.auth import me_router
from app.api.v1.auth import router as auth_router
from app.api.v1.companies import router as company_router
from app.api.v1.documents import router as document_router
from app.api.v1.filings import router as filing_router
from app.api.v1.health import router as health_router
from app.api.v1.instruments import router as instrument_router
from app.api.v1.jobs import router as job_router
from app.api.v1.macro import router as macro_router
from app.api.v1.market import router as market_router
from app.api.v1.metrics import router as metrics_router
from app.api.v1.watchlists import router as watchlist_router

router = APIRouter()
router.include_router(health_router)
router.include_router(instrument_router)
router.include_router(macro_router)
router.include_router(market_router)
router.include_router(filing_router)
router.include_router(metrics_router)
router.include_router(company_router)
router.include_router(document_router)
router.include_router(auth_router)
router.include_router(me_router)
router.include_router(admin_router)
router.include_router(analysis_router)
router.include_router(assistant_router)
router.include_router(watchlist_router)
router.include_router(job_router)
