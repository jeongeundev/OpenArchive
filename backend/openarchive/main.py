from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from openarchive.api.admin import router as admin_router
from openarchive.api.auth import router as auth_router
from openarchive.api.clusters import router as clusters_router
from openarchive.api.diagnostics import router as diagnostics_router
from openarchive.api.documents import router as documents_router
from openarchive.api.groups import principals_router
from openarchive.api.groups import router as groups_router
from openarchive.api.retry import RetryOnUnavailable
from openarchive.api.search import router as search_router
from openarchive.api.system import router as system_router
from openarchive.config import get_settings
from openarchive.db import close_pool, get_pool
from openarchive.embeddings import get_provider, warm_up
from openarchive.frontend import mount_frontend
from openarchive.migrations import run_migrations
from openarchive.services import documents as documents_service
from openarchive.services import grants as grants_service


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 마이그레이션을 실행하는 상시 프로세스는 API 서버 하나다. 워커·MCP 서버는 스키마가
    # 준비된 것으로 가정하므로, 셋이 같은 마이그레이션을 경쟁 실행하지 않는다 (ADR-012).
    # 실패하면 그대로 죽는다 — 스키마가 없는 채로 요청을 받는 것보다 낫다.
    await run_migrations(get_settings().database_url)
    pool = get_pool()
    await pool.open()
    app.state.provider = get_provider()
    # 예열하지 않으면 이 로딩이 통째로 첫 검색 요청에 붙는다 (실측 12.5초).
    await warm_up(app.state.provider)
    try:
        yield
    finally:
        await close_pool()


app = FastAPI(title="OpenArchive API", lifespan=lifespan)
app.add_middleware(RetryOnUnavailable)
app.include_router(admin_router)
app.include_router(groups_router)
app.include_router(principals_router)
app.include_router(auth_router)
app.include_router(documents_router)
app.include_router(clusters_router)
app.include_router(diagnostics_router)
app.include_router(search_router)
app.include_router(system_router)


# 서비스 계층은 HTTP를 모른다. 도메인 예외를 상태 코드로 옮기는 일은 조립 지점인
# 여기서 한 번만 한다 — 라우터마다 같은 try/except를 반복하지 않기 위함이다.
@app.exception_handler(documents_service.DocumentNotFound)
async def _document_not_found(request: Request, error: Exception) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": "문서를 찾을 수 없습니다."})


@app.exception_handler(documents_service.OriginalFileNotFound)
async def _original_file_not_found(request: Request, error: Exception) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": "원본 파일이 없습니다."})


@app.exception_handler(documents_service.OriginalFileMissing)
async def _original_file_missing(request: Request, error: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content={"detail": "원본 파일이 없는 문서는 다시 추출할 수 없습니다."},
    )


@app.exception_handler(documents_service.DocumentAccessDenied)
async def _document_access_denied(request: Request, error: Exception) -> JSONResponse:
    return JSONResponse(status_code=403, content={"detail": "문서를 수정할 권한이 없습니다."})


@app.exception_handler(documents_service.EmptyExtractedText)
async def _empty_extracted_text(request: Request, error: Exception) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(error)})


@app.exception_handler(documents_service.ExtractedTextTooLarge)
async def _extracted_text_too_large(request: Request, error: Exception) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(error)})


@app.exception_handler(documents_service.GrantsOnPublicDocument)
@app.exception_handler(documents_service.GrantToOwner)
@app.exception_handler(grants_service.UnknownGrantee)
async def _invalid_grantees(request: Request, error: Exception) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(error)})


@app.exception_handler(documents_service.IdempotencyKeyReused)
async def _idempotency_key_reused(request: Request, error: Exception) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": str(error)})


@app.exception_handler(documents_service.ExtractionInProgress)
@app.exception_handler(documents_service.NoTextToEdit)
@app.exception_handler(documents_service.RecognitionFailed)
async def _extraction_blocks_text_change(request: Request, error: Exception) -> JSONResponse:
    # 새로고침으로 풀리지 않으므로 current_version을 싣지 않는다 — 버전 충돌과 구분된다.
    return JSONResponse(status_code=409, content={"detail": str(error)})


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


# 빌드된 프론트를 같은 오리진에서 내려준다 (ADR-041). catch-all 라우트를 더하므로
# 반드시 API 라우트를 전부 등록한 **뒤에** 호출해야 한다 — 먼저 부르면 /api/*까지
# 삼킨다. 산출물이 없는 개발 환경에서는 아무것도 하지 않는다.
mount_frontend(app)
