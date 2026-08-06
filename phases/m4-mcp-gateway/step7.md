# Step 7: docs-sync

## 읽어야 할 파일

먼저 아래 파일들을 읽고, 이번 phase가 실제로 무엇을 만들었는지 코드에서 확인하라:

- `phases/m4-mcp-gateway/index.json` — step 0~6의 `summary`. **문서에 적을 내용의 1차 근거다**
- `backend/app/api/documents.py`, `backend/app/api/schemas.py` — 실제 엔드포인트와 응답 필드
- `backend/app/services/related.py`, `backend/mcp_server/server.py` — 실제 시그니처와 환경변수
- `backend/pyproject.toml` — 추가된 의존성
- `frontend/src/app/documents/[id]/page.tsx` — 상세 화면의 최종 구성
- `/docs/ARCHITECTURE.md` — "API 설계" 표, "관련 문서·태그 추천" 절, 디렉토리 구조
- `/docs/ADR.md` — 마지막 ADR 번호는 **ADR-023**이다. 새 ADR은 **024부터** 붙인다
- `/docs/UI_GUIDE.md` — "관련 문서 · 태그 추천" 절
- `/docs/PRD.md`, `README.md`, `/docs/OPENSQL_RESEARCH.md`

**추측으로 쓰지 마라.** 문서에 적는 모든 경로·필드명·커맨드는 코드에서 확인한 것이어야 한다.

## 작업

M4가 만든 계약과 결정을 문서에 반영한다.

### 1) `docs/ARCHITECTURE.md`

- API 표에 **`PUT /api/documents/{id}/tags`** 행을 추가한다. 내용에 "재임베딩을 유발하지 않는다(트리거는 `content_hash` UPDATE에만 걸린다)"를 명시한다
- "구현 현황 (M3 기준)" 문구를 **M4 기준**으로 갱신한다. `/related`·`/tag-suggestions`가 구현됐고 남은 것은 `reconnect_events`(M5)임을 반영한다
- `/related` 응답에 `identical`이 포함된다는 것을 "관련 문서·태그 추천" 절의 응답 예시에 반영한다. **청크가 없어도(`not_indexed`) `identical`은 계산된다**는 점을 한 줄로 적는다
- MCP 서버 절(또는 디렉토리 구조 설명)에 툴 3개의 이름과 반환 항목(발췌·출처·기준 버전), 사용자 컨텍스트 환경변수를 적는다

### 2) `docs/ADR.md` — 새 ADR 2건

**ADR-024: 태그 편집은 별도 엔드포인트이며 낙관적 잠금을 두지 않는다**

- 결정: `PUT /api/documents/{id}/tags`로 전체 교체. `PUT /api/documents/{id}`(추출 텍스트)와 분리
- 이유:
  - 태그 UPDATE는 트리거(`UPDATE OF content_hash`)를 발화시키지 않아 **재임베딩·버전 증가가 일어나지 않는다**. 두 경로의 부작용이 근본적으로 다르다
  - 텍스트 버전 이력은 추출 텍스트의 이력이며 태그는 그 대상이 아니다 (ADR-017)
  - **낙관적 잠금을 두지 않은 이유**: 태그 변경은 `version`을 올리지 않으므로 version 기반 잠금이 태그 변경끼리의 충돌을 애초에 감지하지 못한다. 감지하지 못하는 잠금은 보호받는다는 착각만 준다
- 트레이드오프: 태그는 last-write-wins다. 동시 편집이 드문 데모 규모에서 수용하며, 필요해지면 태그 전용 리비전 컬럼을 도입한다

**ADR-025: MCP 서버의 사용자 컨텍스트는 환경변수이며 기본값은 익명이다**

- 결정: `MCP_USER_ID` 환경변수로 주입하고, 없으면 `user_id=None`으로 **public 문서만** 조회한다. 툴 인자로 받지 않는다
- 이유: 툴 인자로 받으면 MCP 클라이언트가 임의의 사용자를 사칭해 private 문서를 읽을 수 있다. stdio 서버는 사용자 1인이 자기 자격으로 기동하는 프로세스이므로 프로세스 단위 주입이 신뢰 경계와 맞는다
- 트레이드오프: 여러 사용자가 한 MCP 서버를 공유할 수 없다. 데모 구성에서는 문제가 없고, 다중 사용자가 필요해지면 HTTP transport와 인증을 함께 도입한다 (ADR-008의 트레이드오프와 같은 방향)

### 3) `docs/UI_GUIDE.md`

- "태그 추천은 클릭하면 해당 태그가 문서에 추가되는 형태"가 이제 실제 동작이다. 문서 상세에서 **태그를 직접 추가·삭제할 수 있다**는 것을 함께 적는다
- 태그 변경이 새 텍스트 버전을 만들지 않고 상태 배지를 바꾸지 않는다는 점을 한 줄로 명시한다 — 화면 동작에 대한 기대를 정확히 하기 위함이다

### 4) `README.md`

- "주요 기능"에 관련 문서·태그 추천과 MCP 근거 게이트웨이를 반영한다(이미 있으면 실제 구현과 어긋나지 않는지 확인만 한다)
- "빠른 시작 > 실행"에 **MCP 서버 실행**을 5번 항목으로 추가한다:

```bash
# 5. MCP 서버 (Claude Desktop/Code가 기동한다. 수동 확인은 아래 커맨드)
cd backend && source .venv/bin/activate
MCP_USER_ID=alice python -m mcp_server.server
```

- **Claude Code/Desktop 등록 방법**을 절로 추가한다. `claude mcp add` 커맨드 또는 설정 JSON 예시를 적고, `DATABASE_URL`·`EMBEDDING_PROVIDER`·`MCP_USER_ID` 환경변수가 함께 전달되어야 함을 명시한다. **API 서버가 먼저 기동되어 마이그레이션이 적용된 상태여야 한다**(ADR-012)
- `MCP_USER_ID`를 설정하지 않으면 public 문서만 보인다는 것을 적는다

### 5) `docs/OPENSQL_RESEARCH.md`

§12의 "🔴 아직 남은 실측"에 **실 OpenSQL VM에서 관련 문서·태그 추천 쿼리가 HNSW를 사용하는지 `EXPLAIN`으로 확인**을 항목으로 추가한다. 로컬 컨테이너는 데이터가 적어 플래너가 Seq Scan을 골라도 정상이므로, 이 확인은 VM에서만 의미가 있다. **직접 측정하지 않았다면 측정했다고 쓰지 마라.**

### 6) 이슈 #9 완료 조건과의 차이 기록

이슈는 테스트 파일을 `tests/test_mcp.py`로 적었지만 실제 파일명은 **`tests/test_mcp_server.py`**다(tdd-guard 훅이 `test_*server*.py`를 찾는다). README나 이슈 코멘트가 아니라, `phases/m4-mcp-gateway/index.json`의 step 7 `summary`에 이 차이를 한 줄로 남긴다.

## Acceptance Criteria

```bash
bash scripts/check.sh                     # 백엔드 + 프론트엔드 통합 검증 통과

# 문서에 적은 커맨드가 실제로 도는지 확인 (문서를 고친 뒤 반드시 실행)
cd backend && source .venv/bin/activate
python -m mcp_server.server < /dev/null   # 트레이스백 없이 종료
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 문서 정합성 체크리스트를 확인한다:
   - 문서에 적은 엔드포인트 경로·응답 필드명이 `backend/app/api/schemas.py`와 **글자 단위로 일치**하는가?
   - 문서에 적은 실행 커맨드를 실제로 실행해봤는가?
   - "항상 최신"·"실시간 동기화"·"무중단" 같은 과장 표현을 쓰지 않았는가? (ADR-015)
   - 유사 문서를 "중복"이라고 표현하지 않았는가? (ADR-018)
   - 새 ADR 번호가 기존과 충돌하지 않는가? (마지막은 ADR-023)
3. 결과에 따라 `phases/m4-mcp-gateway/index.json`의 step 7을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"` (위 6번의 파일명 차이 포함)
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- 코드에서 확인하지 않은 경로·필드·커맨드를 문서에 쓰지 마라. 이유: 이 프로젝트는 이미 추론으로 두 번 틀렸다(OpenSQL 구성, PostgreSQL 버전). 문서의 신뢰도가 심사 대상이다
- 실측하지 않은 성능 수치를 쓰지 마라. VM 확인이 필요한 항목은 "아직 남은 실측"으로 남긴다
- 이번 phase와 무관한 문서 내용을 다듬거나 재구성하지 마라. 이유: 변경된 모든 줄이 M4 산출물로 추적되어야 한다
- 코드를 고치지 마라. 문서와 코드가 어긋나면 **문서를 코드에 맞추되**, 코드가 명백히 틀린 경우에만 `error_message`에 사유를 남기고 중단한다
- 기존 테스트를 깨뜨리지 마라
