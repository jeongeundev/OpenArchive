"""임베딩 프로바이더 선택 (ARCHITECTURE.md "임베딩 프로바이더").

질의 임베딩도 문서 임베딩과 같은 프로바이더를 써야 한다 — 벡터 공간이 다르면 검색이
에러 없이 무의미해진다. 그래서 프로바이더를 **고르는 것**과 **예열하는 것**을 이 모듈에
함께 둔다.
"""

import asyncio
import logging

from app.config import get_settings
from app.embeddings.base import EMBEDDING_DIM, EmbeddingProvider
from app.embeddings.fake import FakeProvider
from app.embeddings.local import LocalProvider

logger = logging.getLogger(__name__)

__all__ = [
    "EMBEDDING_DIM",
    "EmbeddingProvider",
    "FakeProvider",
    "LocalProvider",
    "get_provider",
    "warm_up",
]


def get_provider(name: str | None = None) -> EmbeddingProvider:
    """이름으로 프로바이더를 만든다. 이름이 없으면 `EMBEDDING_PROVIDER` 설정을 따른다.

    인스턴스를 캐시하지 않는다. `LocalProvider`가 모델을 첫 `embed()`까지 미루므로
    생성 비용이 사실상 0이고, 워커·API는 어차피 프로바이더를 한 번 만들어 재사용한다.
    캐시를 두면 초기화 수단까지 딸려 오는데, 그만한 값어치가 없다.
    """
    resolved = name if name is not None else get_settings().embedding_provider

    match resolved:
        case "fake":
            return FakeProvider()
        case "local":
            return LocalProvider()
        case _:
            # 상용 API 프로바이더는 존재하지 않는다 — 대회 규정상 만들 수 없다 (ADR-003).
            raise ValueError(f"알 수 없는 임베딩 프로바이더입니다: {resolved!r} (local | fake)")


async def warm_up(provider: EmbeddingProvider) -> None:
    """모델을 미리 로드해 첫 요청이 로딩을 기다리지 않게 한다 (ADR-003 "워커 예열").

    `LocalProvider`는 첫 `embed()`까지 모델(~2GB) 로딩을 미룬다 — 기동을 그만큼 막지
    않으려는 설계다. 대신 예열이 없으면 그 로딩이 통째로 **첫 요청**에 붙는다. 워커에서는
    첫 업로드의 처리 지연이 되고, **API에서는 사용자가 검색 버튼을 누르고 기다리는 시간**이
    된다 — 갓 기동한 API의 첫 검색이 12.5초에서 0.2초로 줄었다(2026-08-21 실측, 조건과
    나머지 수치는 ARCHITECTURE.md 「임베딩 프로바이더」). 아무도 기다리지 않는 기동 때
    치르는 편이 낫다.

    프로바이더를 만드는 자리와 같은 모듈에 둔다. 예열의 필요는 프로바이더를 만드는 데서
    따라 나오므로, 둘이 떨어져 있으면 한쪽만 고치게 된다 — 실제로 워커를 고치고 API를
    놓쳤다.

    **다만 프로바이더를 갖는 세 프로세스가 모두 이 함수를 부르는 것은 아니다.** API와
    워커는 부르고, MCP 서버(`mcp_server/server.py`)는 부르지 않는다 — stdio 서버라 기동이
    모델 로딩만큼 늦어지면 클라이언트의 기동 타임아웃에 걸릴 수 있고, 첫 호출을 기다리는
    주체가 사람이 아니라 에이전트다. 그 한계는 ARCHITECTURE.md 「임베딩 프로바이더」에 적었다.

    실패해도 넘어간다 — 예열은 최적화이지 새 실패 지점이 아니다 (LISTEN과 같은 원칙,
    ADR-009). 모델을 받을 수 없는 상황이라면 **기존 실패 경로가 더 나은 진단을 준다**:
    워커는 `fail_job`이 `last_error`에 이유를 남겨 사용자가 화면에서 보고, API는 검색
    요청이 그 자리에서 실패한다. 기동을 막으면 감독자가 되살리는 부팅 루프가 될 뿐이다.
    """
    logger.info("모델을 미리 로드한다 — 처음이라면 내려받느라 시간이 걸린다")
    try:
        # 내용은 무엇이든 상관없다. 로딩을 일으키는 것이 목적이다.
        await asyncio.to_thread(provider.embed, ["예열"])
    except Exception:
        logger.warning("모델 예열 실패 — 첫 요청에서 다시 시도한다", exc_info=True)
    else:
        logger.info("모델 준비 완료")
