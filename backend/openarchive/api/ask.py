from fastapi import APIRouter, HTTPException, Request

from openarchive.api.deps import SESSION_COOKIE, current_user, require_released_user_id
from openarchive.api.schemas import AskRequest, AskResponse
from openarchive.config import get_settings
from openarchive.db import connection
from openarchive.services.answer import gather_evidence, generate_answer

router = APIRouter(prefix="/api/ask", tags=["ask"])


@router.post("", response_model=AskResponse)
async def ask(body: AskRequest, request: Request) -> AskResponse:
    # 인증과 근거 수집을 한 번 대여로 끝낸다 (ADR-043 구현 형태 1).
    async with connection() as conn:
        user = await current_user(
            conn, request.headers.get("authorization"), request.cookies.get(SESSION_COOKIE)
        )
        user_id = await require_released_user_id(user)
        if not body.query.strip():
            raise HTTPException(status_code=400, detail="검색어는 비어 있을 수 없습니다.")
        evidence = await gather_evidence(
            conn, request.app.state.provider, query=body.query, user_id=user_id,
            tags=body.tags, content_type=body.content_type, k=body.k,
            context_chars=get_settings().answer_context_chars,
        )
    result = await generate_answer(evidence, request.app.state.answer_provider)
    return AskResponse(
        status=result.status, answer=result.answer, detail=result.detail,
        sources=result.sources, items=result.hits,
    )
