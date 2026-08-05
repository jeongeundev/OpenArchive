# Step 3: 추출 텍스트 편집·삭제·재임베딩

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/docs/ARCHITECTURE.md` — **"인라인 편집과 낙관적 동시성 (`PUT /api/documents/{id}`)" 절 전체** · **"임베딩 실패 복구 (`POST /api/documents/{id}/reembed`)" 절 전체** · "API 설계" 표의 `DELETE` 행
- `/docs/ADR.md` — **ADR-017**(인라인 편집은 추출 텍스트의 새 논리 버전을 만든다) · **ADR-015**(보장 범위는 버전 일관성과 최신 수렴)
- `/docs/PRD.md` — 핵심 기능 3(텍스트 버전 관리와 자동 재임베딩, 검색 공백 없음)
- `/CLAUDE.md` — **"애플리케이션 코드에서 `embedding_jobs`에 직접 INSERT 하지 마라"** · "편집·버전 관리의 대상은 **추출 텍스트**이며 원본 파일이 아니다"
- **이전 step 산출물**:
  - `/backend/app/api/documents.py`(step 2) — 이 파일에 엔드포인트를 **추가**한다. 기존 핸들러의 스타일·응답 모델을 따르라
  - `/backend/app/api/deps.py`(step 2) — `require_user_id`·`get_conn`을 그대로 쓴다
  - `/backend/tests/test_documents_api.py`(step 2)와 `/backend/tests/conftest.py` — 픽스처와 헬퍼를 재사용한다
- `/backend/migrations/003_triggers.sql` — **트리거가 무엇을 자동으로 하는지.** 이 step의 핸들러들은 트리거가 하는 일을 중복하지 않는다

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 배경

이 step은 **데모의 핵심 장면**을 만든다: 문서를 편집하면 버전이 오르고, `embedding_status`가 `pending`으로 돌아가고, 정합성 카운터가 1 올랐다가, 워커 처리 후 0으로 복귀한다.

그 전부를 **트리거가** 한다. 이 step의 핸들러들은 `documents` 테이블 한 곳만 UPDATE하고, 나머지는 DB가 알아서 하는 것을 확인만 한다.

`reembed`가 특히 그렇다 — 임베딩 실패를 복구하는데 `embedding_jobs`를 건드리지 않는다. `content_hash`를 `SET` 절에 언급하기만 하면 `UPDATE OF content_hash` 트리거가 발화한다(값이 같아도 컬럼이 SET 절에 있으면 발화하는 것이 PostgreSQL 동작이며, M1에서 실측 확인됐다).

## 작업

`backend/app/api/documents.py`에 엔드포인트 3개를 추가한다.

### 1. `PUT /api/documents/{id}` — 편집과 낙관적 동시성

받는 것: JSON `{ "content": "...", "version": 2 }`. `X-User-Id` 필수.

**파일 재업로드(multipart)는 이 step에서 만들지 마라.** M3에서 화면과 함께 판단할 항목이다. 지금은 편집된 추출 텍스트를 JSON으로 받는 경로 하나만 만든다.

처리 순서:

1. 문서를 조회해 **존재·권한**을 먼저 확인한다 (아래 "404와 403을 가르는 기준")
2. `content`가 공백 제거 후 비면 **400**. DB의 `documents_content_not_blank` CHECK가 이중으로 막지만, 제약 위반 예외가 500으로 새어나가게 두지 마라
3. 갱신은 **한 문장**으로:
   ```sql
   UPDATE documents
      SET version = version + 1, content = %(content)s, content_hash = %(hash)s, updated_at = now()
    WHERE id = %(id)s AND version = %(client_version)s
   ```
4. **0건 갱신이면 409** — 그 사이에 다른 곳에서 바뀐 것이다. 현재 `version`을 다시 조회해 응답에 담는다:
   ```
   409 { "detail": "다른 곳에서 문서가 수정되었습니다. 새로고침 후 다시 시도하세요.",
         "current_version": 3 }
   ```

**`WHERE ... AND version = %(client_version)s`로 비교와 갱신을 한 문장에 두는 것이 핵심이다.** 먼저 `SELECT version`으로 확인한 뒤 `UPDATE`하면 그 사이에 다른 요청이 끼어들 수 있다.

> **소유권 확인을 같은 `WHERE`에 합치지 마라.** `AND owner_id = %(user)s`까지 넣으면 0건 갱신의 원인이 "버전 충돌"인지 "권한 없음"인지 구분할 수 없어, 남의 문서를 수정하려 한 요청에 409를 돌려주게 된다. 소유권은 1단계에서 따로 확인한다 — `owner_id`는 이 API로 바뀌지 않으므로 경쟁 조건이 없다.

**버전 이력 기록과 재임베딩 잡 생성 코드를 넣지 마라.** 트리거가 한다. 이 핸들러는 `documents`만 UPDATE한다.

`content_hash`는 step 2와 같은 방식(`sha256(content.encode("utf-8")).hexdigest()`)으로 계산한다.

### 2. `DELETE /api/documents/{id}` — 204

소유자만 삭제할 수 있다. `document_chunks`·`document_versions`·`embedding_jobs`는 FK `ON DELETE CASCADE`로 함께 사라진다 — **삭제 쿼리를 여러 개 쓰지 마라.**

### 3. `POST /api/documents/{id}/reembed`

소유자만 요청할 수 있다.

```sql
-- 애플리케이션은 embedding_jobs를 직접 건드리지 않는다.
UPDATE documents SET content_hash = content_hash WHERE id = %(doc_id)s;
```

트리거가 상태를 `pending`으로 되돌리고 새 잡을 만든다. **버전은 오르지 않으므로 이력이 오염되지 않고**, 트리거의 `ON CONFLICT (document_id, version) DO NOTHING`이 중복 이력을 막는다.

이미 `pending` 잡이 있는 문서에 다시 요청해도 안전하다 — 파셜 유니크 인덱스 `uq_pending_job_per_doc`와 `ON CONFLICT DO NOTHING`이 코얼레싱한다. **애플리케이션에서 "이미 pending이면 건너뛰기" 분기를 만들지 마라.**

### 404와 403을 가르는 기준 (세 엔드포인트 공통)

- 문서가 **없으면** 404
- 문서가 **private이고 타인 소유**면 **404** — 존재 자체를 숨긴다. 검색·목록·상세에서 보이지 않는 것과 일관된다
- 문서가 **public인데 타인 소유**면 **403** — 존재는 이미 보이므로 숨길 것이 없고, 권한이 없다는 사실이 정확한 정보다

이 규칙을 세 엔드포인트에 동일하게 적용하고, 테스트로 고정하라.

### 4. `backend/tests/test_documents_api.py`에 테스트 추가 — 먼저 작성한다

**구현보다 테스트를 먼저 작성하고 실패를 확인한 뒤 구현하라.**

최소 아래를 확인한다.

**편집**

1. **정상 편집** — 200이고 `version`이 1 오르며 `content`가 바뀐다.
2. **트리거가 따라 움직였다** — 편집 후 `document_versions`에 새 버전 행이 생기고, `embedding_jobs`에 pending 잡이 있으며, `documents.embedding_status`가 `pending`으로 돌아간다. **핸들러 코드가 만든 것이 아님을 이 테스트가 증명한다.**
3. **낡은 `version`은 409** — 응답 본문에 `current_version`이 실제 현재 버전으로 들어 있다.
4. **409일 때 문서가 바뀌지 않았다** — `content`와 `version`이 그대로다.
5. **빈 `content`는 400** — 공백·개행뿐인 문자열. 문서가 바뀌지 않는다.
6. **검색 공백 없음** — 청크가 있는 문서를 편집한 직후(워커 미실행) 청크가 **여전히 이전 버전으로 남아 있다.** 이것이 PRD의 "재임베딩 완료 전까지 이전 벡터로 검색이 계속된다"를 API 레벨에서 확인하는 테스트다.
7. **정합성 카운터가 관측된다** — 6번 상태에서 `document_chunks.version <> documents.version`인 문서가 1건이고, 워커를 돌린 뒤 0건으로 돌아온다. (step 1에서 conftest에 넣은 임베딩 처리 헬퍼를 쓴다.) **ADR-015가 "어긋난 구간을 관측할 수 있다"고 주장하는 그 카운터다.**

**삭제**

8. **정상 삭제** — 204이고 문서가 사라진다.
9. **CASCADE 확인** — 청크가 있는 문서를 삭제하면 `document_chunks`·`document_versions`·`embedding_jobs`의 관련 행이 함께 사라진다. **삭제 전에 각 테이블에 행이 있었음을 먼저 확인하라** — 원래 0건이었다면 CASCADE를 검증한 것이 아니다.

**재임베딩**

10. **새 잡이 생긴다** — `error` 상태 문서에 요청하면 `embedding_status`가 `pending`이 되고 pending 잡이 생긴다.
11. **버전이 오르지 않는다** — `documents.version`이 그대로이고 `document_versions`에 행이 늘지 않는다.
12. **중복 요청이 안전하다** — 연속 두 번 호출해도 pending 잡은 1건이다(코얼레싱).
13. **실제로 복구된다** — reembed 후 워커를 돌리면 청크가 만들어지고 상태가 `ready`가 된다.

**권한 (세 엔드포인트 공통)**

14. **타인의 private 문서** → 404 (편집·삭제·reembed 각각).
15. **타인의 public 문서** → 403 (편집·삭제·reembed 각각).
16. **`X-User-Id` 없는 요청** → 400.

## Acceptance Criteria

```bash
docker compose up -d              # 프로젝트 루트에서
cd backend
.venv/bin/ruff check .
.venv/bin/pytest tests/test_documents_api.py -v
.venv/bin/pytest                  # 전체 통과
cd ..
bash scripts/check.sh             # 종료 코드 0
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - **`embedding_jobs`에 INSERT하거나 UPDATE하는 코드가 없는가?**
   - `document_versions`에 INSERT하는 코드가 없는가? (트리거의 책임이다)
   - PUT의 비교와 갱신이 한 문장인가?
   - 소유권 확인이 버전 확인과 분리되어 있는가?
   - DELETE가 CASCADE에 맡기고 있는가 — 자식 테이블을 손으로 지우지 않는가?
   - 404/403 규칙이 세 엔드포인트에서 동일한가?
3. 결과에 따라 `phases/m2-hybrid-search/index.json`의 step 3을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"` — **PUT 요청·응답 스키마(409 본문의 `current_version` 포함), 404/403 규칙, 파일 재업로드(multipart)를 만들지 않았다는 점을 반드시 포함시켜라.** M3(프론트엔드)가 이 계약에 맞춰 편집 화면을 만든다.
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "..."` 후 즉시 중단

## 금지사항

- **`embedding_jobs`를 INSERT·UPDATE·DELETE 하지 마라.** 이유: CLAUDE.md의 CRITICAL 규칙이자 심사 핵심이다. `reembed`조차 `documents`만 UPDATE해서 트리거를 깨우는 방식으로 만든다.
- **`document_versions`에 INSERT하지 마라.** 이유: 트리거의 책임이다. 애플리케이션이 함께 쓰면 이력이 중복된다.
- **소유권 조건을 버전 UPDATE의 `WHERE`에 합치지 마라.** 이유: 0건 갱신의 원인을 구분할 수 없어, 권한 없는 요청에 409를 돌려주게 된다.
- **`SELECT version` 후 별도 `UPDATE`로 버전을 비교하지 마라.** 이유: 확인과 쓰기 사이에 다른 요청이 끼어든다. 한 문장으로 처리한다.
- **`reembed`에 "이미 pending이면 건너뛰기" 분기를 만들지 마라.** 이유: DB의 파셜 유니크 인덱스와 `ON CONFLICT DO NOTHING`이 이미 코얼레싱한다. 애플리케이션이 중복 구현하면 경쟁 조건이 생긴다.
- **DELETE에서 자식 테이블을 손으로 지우지 마라.** 이유: FK `ON DELETE CASCADE`가 원자적으로 처리한다. 손으로 지우면 순서가 틀렸을 때 고아 행이 남는다.
- **파일 재업로드(multipart PUT)를 만들지 마라.** 이유: ARCHITECTURE의 API 표에 언급은 있으나 M3에서 화면과 함께 판단할 항목이며, 지금 만들면 검증되지 않은 경로가 늘어난다.
- **`version`을 애플리케이션에서 임의로 지정하지 마라.** 이유: `version = version + 1`로 DB가 계산해야 동시 요청에서 값이 어긋나지 않는다.
- **`app/services/`의 파일을 수정하지 마라.** 이유: 이전 step의 산출물이다.
- 기존 테스트를 깨뜨리지 마라.
