import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from config.settings import get_settings
from database.models import Base  # noqa: F401 — register metadata
from database.seed import seed_reference_data
from database.session import SessionLocal, engine
from genai_pipeline.client import provider_chain
from src.api.analytics import router as analytics_router
from src.api.auth import router as auth_router
from src.api.complaints import router as complaints_router
from src.api.config_routes import router as config_router
from src.api.knowledge import router as knowledge_router
from src.api.prompts import router as prompts_router
from src.api.users import router as users_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("supportnova")


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        seed_reference_data(db)
    finally:
        db.close()
    yield


settings = get_settings()
app = FastAPI(
    title="SupportNova API",
    description="Generative AI complaint intelligence with an independent Python ground-truth pipeline.",
    version="1.1.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list or ["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(auth_router)
app.include_router(users_router)
app.include_router(complaints_router)
app.include_router(knowledge_router)
app.include_router(config_router)
app.include_router(analytics_router)
app.include_router(prompts_router)


@app.exception_handler(OperationalError)
async def database_unavailable(_: Request, exc: OperationalError):
    logger.error("Database unavailable: %s", exc.orig)
    return JSONResponse(status_code=503, content={"detail": "Database is unavailable. Try again shortly."})


@app.exception_handler(SQLAlchemyError)
async def database_error(_: Request, exc: SQLAlchemyError):
    logger.exception("Database error", exc_info=exc)
    return JSONResponse(status_code=500, content={"detail": "A database error occurred; the change was not saved."})


@app.exception_handler(Exception)
async def unexpected_error(_: Request, exc: Exception):
    logger.exception("Unhandled error", exc_info=exc)
    return JSONResponse(status_code=500, content={"detail": "Unexpected server error. It has been logged."})


@app.get("/health")
def health():
    database = "ok"
    try:
        with engine.connect() as conn:
            conn.execute(text("select 1"))
    except SQLAlchemyError:
        database = "unavailable"
    return {
        "status": "ok" if database == "ok" else "degraded",
        "service": "supportnova",
        "organization": settings.organization_name,
        "database": database,
        "genai_configured": bool(provider_chain(settings)),
    }
