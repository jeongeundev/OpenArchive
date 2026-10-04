from fastapi import APIRouter, HTTPException, Request

from openarchive.api.deps import SESSION_COOKIE, current_user, require_user_id
from openarchive.api.schemas import AskRequest, AskResponse
from openarchive.config import get_settings
from openarchive.db import connection
from openarchive.services.answer import gather_evidence, generate_answer

router = APIRouter(prefix="/api/ask", tags=["ask"])


@router.post("", response_model=AskResponse)
async def ask(body: AskRequest, request: Request) -> AskResponse:
    # 인증과 근거 수집을 한 번 대여로 끝내고, 반납한 뒤 생성한다 (ADR-043 구현 형태 1).
    # 인증을 Depends로 받지 않는 이유: Connection 의존성은 요청 함수가 끝날 때까지 커넥션을
    # 쥐어, 수십 초 걸리는 생성 동안 풀을 점유한다. 공유 주체 거절은 require_user_id가 한다.
    async with connection() as conn:
        user = await current_user(
            conn, request.headers.get("authorization"), request.cookies.get(SESSION_COOKIE)
        )
        user_id = await require_user_id(user)
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
