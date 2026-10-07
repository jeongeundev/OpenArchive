# Step 2: doc-read

읽기 명령 `doc list`·`doc show [--version N]`·`doc download [-o 경로]`를 만든다. 쓰기 명령은 step 3이다.

## 공통 배경 — m30-user-cli 설계 결정 (모든 step 같음)

이슈 #189. 근거 **ADR-057**(사용자 CLI는 REST API의 클라이언트, 채택 2026-10-05)·**ADR-060 결정 8**(삭제는 휴지통)·**ADR-048**(503 재시도)·ADR-034(위임 토큰)·ADR-061 결정 1(토큰 만료일, #199에서 구현됨).

지금 `openarchive` CLI(`backend/openarchive/cli.py`, 1651줄)는 **운영자 도구**다 — `--dsn`(또는 `DATABASE_URL`)으로 DB에 직결하고 `--user`로 아무 사용자나 비밀번호 없이 지정한다. 이 phase는 같은 실행 파일에 **사용자 CLI**를 더한다: `gh`처럼 서버 REST API에 **본인 API 토큰(Bearer)** 으로만 붙는 클라이언트다. 열람 범위·소유자 검사·낙관적 잠금·read 토큰 거부·감사 로그는 서버가 웹과 똑같이 적용한다 — **CLI가 규칙을 다시 구현하지 않는다.**

사실 (탐색으로 확인):

- 필요한 REST 엔드포인트는 **이미 전부 있다**(`backend/openarchive/api/documents.py`·`search.py`·`ask.py`·`auth.py`):
  - 목록 `GET /api/documents?sort=updated&limit=1..100&offset=` → `list[DocumentSummary]`(`id,title,filename,content_type,version,tags,embedding_status,extraction_status,…`)
  - 상세 `GET /api/documents/{id}` → `DocumentDetail`(`content`, `versions`, `files[]`(`file_version,filename,size,sha256,…`))
  - 텍스트 버전 `GET /api/documents/{id}/versions/{n}` → `TextVersionDetail`(`content` 포함)
  - 업로드 `POST /api/documents` multipart(`file`, `title`, `tags`(반복 필드), …) + 헤더 `Idempotency-Key` → 201 `DocumentSummary`
  - 편집 `PUT /api/documents/{id}` JSON `{content, version}` → `EditDocumentResponse`(새 `version`)
  - 버전 되돌리기 `POST /api/documents/{id}/versions/{n}/restore` JSON `{current_version}` → `EditDocumentResponse`
  - 태그 `PUT /api/documents/{id}/tags` JSON `{tags}` → `DocumentSummary`
  - 원본 `GET /api/documents/{id}/file` → 최신 원본 바이트
  - 삭제 `DELETE /api/documents/{id}`(휴지통) · `DELETE /api/documents/{id}?permanent=true`(영구) → 204
  - 휴지통 `GET /api/documents/trash` → `list[TrashItem]`(`id,title,deleted_at,purge_at`, **write scope 필요**) · 복원 `POST /api/documents/{id}/restore`
  - 검색 `POST /api/search` JSON `{query,tags,content_type,k}` → `{items:[SearchResult], sql}` · 답변 `POST /api/ask` → `{status: answered|no_evidence|disabled|failed, answer, detail, sources, items}`
  - 주체 `GET /api/auth/me` → `AuthStatus` — **step 0에서 `scope`·`expires_at`을 더한다**
- 서버 오류 문구: 볼 수 없거나 없는 문서는 404 `"문서를 찾을 수 없습니다."`(존재를 드러내지 않음), read 토큰의 쓰기는 403 `"쓰기 권한이 필요합니다."`, 버전 충돌은 409 `{"detail": "...", "current_version": N}`(다른 409 — 인식 중 등 — 에는 `current_version` 키가 없다), DB 일시 불가용은 503 + `Retry-After`.
- 토큰 판정은 `services/auth.py validate_token` 하나다. 만료·폐기·틀린 값은 모두 익명 처리된다(`/api/auth/me`가 `authenticated: false`). 공유 토큰으로 `/me`를 부르면 403이다.
- 설정 디렉터리는 `openarchive.config.openarchive_home()`(`$OPENARCHIVE_HOME` 또는 `~/.openarchive`). 같은 디렉터리에 서버용 `.env`가 있을 수 있다.
- `httpx` 0.28은 이미 설치되어 있다(`mcp`가 끌고 옴, BSD-3). 지금은 `[dev]`에만 적혀 있다.

결정 (2026-10-08 사용자 승인):

- **D1** `httpx`를 `backend/pyproject.toml` 런타임 `dependencies`에 **명시**한다(주석에 라이선스와 이유 — 사용자 CLI가 직접 import).
- **D2** `/api/auth/me`의 `AuthStatus`에 `scope: str | None`, `expires_at: datetime | None`을 더한다. **토큰 주체일 때만** 값이 있고 세션·익명이면 둘 다 `null`. 기존 세 필드의 의미는 그대로다.
- **D3 재시도** — 웹 UI(`frontend/src/lib/api.ts fetchWithBackoff`)와 **같은 기준**: 읽기(GET), 메서드만 POST인 읽기(`/api/search`·`/api/ask`), `Idempotency-Key`가 붙은 업로드(`POST /api/documents`)만 503·연결 오류에 재시도한다. 지수 백오프 1초 시작·상한 8초·전체 지터(`random() * min(8, 1 * 2**attempt)`), 총 60초 예산, 503의 `Retry-After`(초 또는 HTTP 날짜)보다 일찍 보내지 않는다(`max(Retry-After, 지터)`). 재시도 중이면 stderr에 한 번 「서버가 일시적으로 응답하지 않아 다시 시도하는 중입니다…」를 알린다. 업로드는 명령 실행마다 키를 한 번 만들어 재시도에 재사용한다. **다른 쓰기(편집·되돌리기·태그·삭제·복원)는 재시도하지 않는다** — 서버가 키를 지키지 않는다. 예산을 넘기거나 재시도 대상이 아닌 503은 「서버가 일시적으로 응답하지 못했습니다. 잠시 후 다시 실행하세요.」로 실패한다.
- **D4 자격증명 파일** — `openarchive_home() / "credentials.json"`에 `{"url": "...", "token": "..."}`. **권한 600**으로 쓴다(새 파일이든 기존 파일이든 쓴 뒤 0600이어야 한다). 디렉터리가 없으면 0700으로 만든다(있는 디렉터리의 권한은 바꾸지 않는다). 서버는 하나만 기억하고 다시 로그인하면 덮어쓴다. `logout` 명령은 만들지 않는다.
- **D5 모듈 경계** — `backend/openarchive/client.py`(REST 호출·자격증명 읽기/쓰기·오류 변환·재시도)와 `backend/openarchive/user_cli.py`(사용자 명령 실행·출력). `cli.py`는 서브커맨드 등록과 분기만 한다. **사용자 경로는 DB에 붙지 않고 `get_settings()`·`DATABASE_URL`·`.env`를 읽지 않는다**(CLAUDE.md CRITICAL, ADR-057 결정 2). `client.py`·`user_cli.py`는 `openarchive.config`에서 `openarchive_home`만 가져오고 `openarchive.services`·`openarchive.db`·`psycopg`를 import하지 않는다.
- **D6 편집 충돌 문구** — `current_version` 키가 있는 409만 CLI가 「다른 곳에서 먼저 수정되었습니다」(+ 「현재 버전은 vN입니다」)로 출력한다. 서버 문구는 웹이 쓰므로 바꾸지 않는다.
- **D7 `doc list`** — 웹과 같은 정렬(`sort=updated`)로 `limit=100` 페이지를 끝까지(받은 수 < 100이 될 때까지) 받아 표로 출력: ID·제목·유형·버전·처리 상태. 처리 상태 라벨은 웹 `frontend/src/components/StatusBadge.tsx DocumentStatusBadge`와 같다 — `extraction_status`가 `pending`이면 「텍스트 인식 중」, `failed`면 「텍스트 인식 실패」, `done`이면 `embedding_status`로 `pending` 「대기 중」·`processing` 「처리 중…」·`ready` 「완료」·`error` 「실패」. 필터 옵션은 두지 않는다. 문서가 없으면 「문서가 없습니다.」.
- **D8 `search`/`ask` 분기** — 두 명령의 `--user`를 **선택 인자**로 바꾼다. `--user`가 있으면 기존 운영자 직결(그대로), 없으면 로그인 토큰으로 REST. `--dsn`만 있고 `--user`가 없으면 「--dsn은 --user와 함께 쓰는 운영자 옵션입니다.」로 종료 코드 2.
- **D9 테스트** — 사용자 CLI 테스트는 **실제 앱 + 실제 DB**(pgvector 컨테이너, `db_client` fixture)로 관통한다. 주입 지점은 `client.py`의 모듈 변수 하나(`TRANSPORT: httpx.BaseTransport | None = None`)이고, 테스트는 `monkeypatch.setattr(openarchive.client, "TRANSPORT", db_client._transport)`로 lifespan이 돈 앱에 요청을 보낸다(로그인 URL은 `http://testserver`). 응답을 흉내 내는 가짜 서버로 문서·권한 동작을 검증하지 마라. **예외는 D3 재시도 로직뿐**이다 — 503을 실제로 만들 수 없으므로 `httpx.MockTransport`로 상태 코드·헤더만 흉내 내고, 대기는 주입한 `sleep`으로 기록해 시간을 쓰지 않는다. 자격증명 위치는 `monkeypatch.setenv("OPENARCHIVE_HOME", str(tmp_path))`로 격리한다.
- **D10 `needs_vm=false`** — 서버 변경은 `/me` 필드뿐이고 OpenProxy 고유 동작이 없다.
- **D11 삭제** — ADR-060 결정 8: `doc delete <id>`는 **확인 없이 휴지통**으로 옮긴다(「휴지통으로 옮겼습니다. openarchive doc trash restore <id>로 되돌릴 수 있습니다.」). `doc delete <id> --permanent`는 「삭제하면 되돌릴 수 없습니다. 계속할까요? [y/N]」를 묻고 `y`(대소문자 무관)일 때만 영구 삭제한다. `doc trash list`·`doc trash restore <id>`.

**공통 출력·종료 규칙** (모든 사용자 명령):

- 성공 0, 실패 1, 잘못된 사용 2. 트레이스백을 내지 않는다.
- 로그인 정보가 없으면 「로그인이 필요합니다. openarchive login --url <서버> --token <API 토큰>으로 먼저 로그인하세요.」(1).
- 401(토큰 폐기·만료) → 「토큰이 올바르지 않습니다. openarchive login으로 다시 로그인하세요.」 / 연결 실패 → 「서버에 연결하지 못했습니다: <url>」 / 그 밖의 4xx → 서버 `detail` 그대로(404 「문서를 찾을 수 없습니다.」, 403 「쓰기 권한이 필요합니다.」 등).
- 문서 ID 인자는 UUID로 파싱한다. 형식이 틀리면 서버에 보내지 않고 「문서를 찾을 수 없습니다.」(1) — 존재 여부와 무관한 같은 문구.
- 메시지는 stdout, 재시도 안내만 stderr.

**세션 전용 동작은 만들지 않는다**(ADR-057 결정 3): 열람 범위 변경·폴더 열람 범위·외부 공유·토큰 발급/목록/폐기·`/api/admin/*`. 운영자 CLI(`init`·`create-user`·`serve`·`reset-password`·`rebuild-edges`·`reextract`·`import`·`export`·`demo`, `--user` 있는 `search`·`ask`)는 동작을 바꾸지 않는다.

### 완료 조건 — 이슈 #189 시험항목 14 (제출 명세서 TC는 이 중 5개, 문구가 구현 계약이다)

정본: 제출본 `notes/contest/submission/functional-spec-submit.md`(★ 표시가 제출 TC). 「」 안 문구는 글자 그대로 출력한다.

| 항목 | 시험 내용 |
|---|---|
| ★ 토큰 로그인 | `openarchive login --url http://<서버>:8000 --token <API 토큰>` → 「<사용자명>(으)로 로그인했습니다」, 토큰이 `~/.openarchive`에 권한 600으로 저장, `openarchive whoami`가 사용자명과 토큰 범위(read/read_write)(+ 만료일, #199)를 보임 |
| 잘못된 토큰 | 폐기했거나 틀린 토큰으로 `login` → 「토큰이 올바르지 않습니다」, 저장되지 않음 |
| 문서 목록 | `doc list` → 웹 문서 목록과 같은 문서(제목·유형·버전·처리 상태) 표 |
| 문서 보기 | `doc show <ID>` 현재 버전 텍스트, `--version 1`이면 v1 텍스트 |
| ★ 문서 올리기 | `doc upload 회의록.docx --title "10월 회의록" --tag 회의` → 문서 ID 출력, 웹 목록에 같은 제목·태그, 처리 후 검색됨 |
| ★ 텍스트 편집 | `doc edit <ID> --file 수정본.txt` → 새 버전 번호 출력, 버전 이력에 추가 |
| 편집 충돌 | `doc show`로 v1을 받은 뒤 웹에서 먼저 편집(v2) → `doc edit <ID> --file 수정본.txt --base-version 1` → 「다른 곳에서 먼저 수정되었습니다」로 거부, 웹 내용이 덮이지 않음 |
| 버전 되돌리기 | `doc restore <ID> 1` → v1 내용으로 새 버전, 이전 버전들은 이력에 남음 |
| 태그 편집 | `doc tag <ID> --set 인사,규정` → 태그가 「인사」「규정」으로 바뀜 |
| 원본 내려받기 | `doc download <ID> -o 받은파일.pdf` → sha256이 업로드 원본과 같음 |
| 문서 삭제 | (D11로 개정) `doc delete <ID>` → 휴지통, 웹 목록에서 사라짐 · `--permanent`는 「삭제하면 되돌릴 수 없습니다」 확인에 `y`를 넣어야 영구 삭제 |
| ★ 검색·답변 | 로그인 상태에서 `--user` 없이 `search "출장비 정산 기한"`·`ask "출장비 정산 기한은?"` → 토큰 주인이 볼 수 있는 문서만으로 결과·답변 |
| 열람 범위 | 다른 사용자의 비공개 문서 ID로 `doc show` → 「문서를 찾을 수 없습니다」 |
| ★ 읽기 전용 토큰 | read 토큰으로 로그인 → `doc edit`·`doc upload`·`doc delete`는 「쓰기 권한이 필요합니다」로 거부, `doc list`·`doc show`·`search`는 동작 |

## 읽어야 할 파일

- `phases/m30-user-cli/index.json` — step 0·1 summary
- `backend/openarchive/client.py`, `backend/openarchive/user_cli.py`, `backend/tests/test_user_cli.py`(fixture) — step 1 산출물
- `backend/openarchive/api/documents.py` — `list_documents`(쿼리 파라미터·`limit` 상한 100), `get_document`, `get_text_version`(`/versions/{n}`), `download_latest_original`·`_original_file_response`(`Content-Disposition`의 `filename*`)
- `backend/openarchive/api/schemas.py` — `DocumentSummary`·`DocumentDetail`·`TextVersionDetail`·`OriginalFile`
- `frontend/src/components/StatusBadge.tsx` — 처리 상태 라벨(D7)
- `backend/tests/conftest.py` — `insert_test_document`, `seed_extraction_states`, `upload_document`, `run_embedding_worker`

## 작업

### 1) 테스트 먼저 — `backend/tests/test_user_cli.py`에 추가

1. **문서 목록**: alice가 볼 수 있는 문서(본인 비공개·공개·남의 공개)와 볼 수 없는 문서(bob 비공개)를 둔다. `doc list` 출력의 문서 ID 집합과 **순서**가 alice 세션의 `GET /api/documents?sort=updated`와 같고, bob 비공개 문서의 ID·제목은 출력에 없다. 각 행에 제목·유형·버전이 보인다.
2. **처리 상태 라벨**: 추출 pending/failed, 임베딩 pending/processing/ready/error 문서가 각각 D7 라벨로 출력된다(`seed_extraction_states`·직접 UPDATE 등으로 상태를 만든다).
3. **페이지 끝까지**: 문서 101건(`insert_test_document`) → `doc list`에 101개 ID가 모두 나온다.
4. 문서가 없으면 「문서가 없습니다.」.
5. **문서 보기**: 편집으로 v2를 만든 문서에서 `doc show <ID>`는 v2 텍스트, `doc show <ID> --version 1`은 v1 텍스트를 출력한다.
6. **열람 범위**: bob 비공개 문서 ID로 alice가 `doc show` → 1, 「문서를 찾을 수 없습니다」. 존재하지 않는 UUID·UUID 형식이 아닌 값도 같은 문구다. `doc download`도 같다.
7. **원본 내려받기**: 바이너리 파일(예: `tests/fixtures/`의 PDF)을 업로드한 뒤 `doc download <ID> -o <tmp>/받은파일.pdf` → 0, 받은 파일의 sha256이 업로드 바이트의 sha256과 같다. `-o` 없이 실행하면 현재 디렉터리(`monkeypatch.chdir(tmp_path)`)에 원본 파일명(경로 성분 제거)으로 저장된다. 출력 경로에 파일이 이미 있으면 1이고 기존 파일은 바뀌지 않는다.
8. 원본 없는 텍스트 문서의 `doc download` → 1, 서버 문구(「원본 파일이 없습니다.」).
9. **읽기 전용 토큰**: read 토큰으로 로그인해도 `doc list`·`doc show`·`doc download`가 동작한다.

### 2) 구현

`user_cli.py`:

```python
def run_doc_list() -> int
def run_doc_show(*, document_id: str, version: int | None) -> int
def run_doc_download(*, document_id: str, output: Path | None) -> int
```

- `doc list`: D7. 표는 고정폭 열(한글 폭을 정확히 맞출 필요는 없다 — 열 구분이 보이면 된다). 열: ID(전체 UUID — 다른 명령에 복사해 쓴다)·제목·유형·버전(`v3`)·처리 상태.
- `doc show`: 텍스트만 stdout에 그대로 쓴다(머리말·꼬리말 없이 — 파이프로 파일에 받아 편집하는 용도다. 편집 충돌 시나리오가 `doc show`로 v1을 받아 수정본을 만든다).
- `doc download`: `GET /api/documents/{id}/file`의 바이트를 쓴다. `-o`가 없으면 응답 `Content-Disposition`의 `filename*`(없으면 `filename`)에서 이름을 꺼내 `Path(name).name`만 쓴다. 이미 있는 경로면 쓰지 않고 「이미 있는 파일입니다: <경로>」(1). 성공하면 「<경로>에 저장했습니다 (<크기> 바이트, sha256 <앞 12자리>…)」.
- 문서 ID 파싱 실패는 공통 규칙대로 「문서를 찾을 수 없습니다.」.

`cli.py`: `doc` 서브커맨드 아래에 `list`·`show`(`document_id`, `--version`)·`download`(`document_id`, `-o/--output`)를 등록하고 분기만 한다. 다음 step이 같은 `doc` 파서에 쓰기 명령을 더하므로, `doc`의 하위 파서 생성은 한 곳에 둔다.

## Acceptance Criteria

```bash
docker compose up -d
cd backend && .venv/bin/pytest tests/test_user_cli.py tests/test_client.py tests/test_cli.py -q
cd backend && .venv/bin/ruff check .
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. mutant 확인(테스트가 실패해야 한다, 확인 뒤 되돌린다): 목록에서 한 페이지만 받는다 · `--version`을 무시한다 · `-o` 경로가 있어도 덮어쓴다.
3. 아키텍처 체크리스트: 열람 판정을 CLI가 하지 않고 서버 404를 그대로 전하는가, 목록을 CLI에서 걸러내지 않는가.
4. `phases/m30-user-cli/index.json`의 step 2를 갱신한다.

## 금지사항

- 목록·보기 결과를 CLI에서 필터링하거나 정렬을 바꾸지 마라. 이유: 「웹 문서 목록과 같은 문서」 — 서버 결과가 정본이다(CLAUDE.md 「앱에서 후처리 필터링 금지」와 같은 원칙).
- 내려받은 파일명에 서버가 준 경로 성분을 쓰지 마라. 이유: `../` 등으로 현재 디렉터리 밖에 쓸 수 있다.
- 쓰기 명령(upload·edit·…)을 이 step에서 만들지 마라. 이유: step 3의 범위다.
- 기존 테스트를 깨뜨리지 마라
