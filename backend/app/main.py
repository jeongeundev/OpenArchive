from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.admin import router as admin_router
from app.api.auth import router as auth_router
from app.api.documents import router as documents_router
from app.api.retry import RetryOnOperationalError
from app.api.search import router as search_router
from app.api.system import router as system_router
from app.config import get_settings
from app.db import close_pool, get_pool
from app.embeddings import get_provider
from app.migrations import run_migrations
from app.services import documents as documents_service


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
app.add_middleware(RetryOnOperationalError)
app.include_router(admin_router)
app.include_router(auth_router)
app.include_router(documents_router)
app.include_router(search_router)
app.include_router(system_router)


# 서비스 계층은 HTTP를 모른다. 도메인 예외를 상태 코드로 옮기는 일은 조립 지점인
# 여기서 한 번만 한다 — 라우터마다 같은 try/except를 반복하지 않기 위함이다.
@app.exception_handler(documents_service.DocumentNotFound)
async def _document_not_found(request: Request, error: Exception) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": "문서를 찾을 수 없습니다."})


@app.exception_handler(documents_service.DocumentAccessDenied)
async def _document_access_denied(request: Request, error: Exception) -> JSONResponse:
    return JSONResponse(status_code=403, content={"detail": "문서를 수정할 권한이 없습니다."})


@app.exception_handler(documents_service.EmptyExtractedText)
async def _empty_extracted_text(request: Request, error: Exception) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(error)})


@app.exception_handler(documents_service.ExtractedTextTooLarge)
async def _extracted_text_too_large(request: Request, error: Exception) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(error)})


@app.exception_handler(documents_service.VersionConflict)
async def _version_conflict(
    request: Request, error: documents_service.VersionConflict
) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content={"detail": str(error), "current_version": error.current_version},
    )


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
