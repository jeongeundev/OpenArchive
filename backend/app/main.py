from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.documents import router as documents_router
from app.api.search import router as search_router
from app.config import get_settings
from app.db import close_pool, get_pool
from app.embeddings import get_provider
from app.migrations import run_migrations


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 마이그레이션은 API 서버만 실행한다. 워커·MCP 서버는 스키마가 준비된 것으로
    # 가정하므로, 세 프로세스가 같은 마이그레이션을 경쟁 실행하지 않는다 (ADR-012).
    # 실패하면 그대로 죽는다 — 스키마가 없는 채로 요청을 받는 것보다 낫다.
    await run_migrations(get_settings().database_url)
    pool = get_pool()
    await pool.open()
    app.state.provider = get_provider()
    try:
        yield
    finally:
        await close_pool()


app = FastAPI(title="OpenArchive API", lifespan=lifespan)
app.include_router(documents_router)
app.include_router(search_router)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
