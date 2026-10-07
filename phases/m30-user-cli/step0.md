# Step 0: me-scope

`GET /api/auth/me`에 토큰 범위(`scope`)와 만료일(`expires_at`)을 싣는다(D2). 사용자 CLI의 `login` 검증과 `whoami`가 이 한 호출을 쓴다. CLI 코드는 이 step에서 만들지 않는다.

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

- `/docs/ADR.md` — ADR-057(전체), ADR-034 결정 4·5, ADR-061 결정 1
- `backend/openarchive/services/auth.py` — `validate_token`(반환 dict), `validate_session`(세션 dict에도 `scope="read_write"`가 들어 있다 — `/me`에서는 세션이면 `null`로 낸다)
- `backend/openarchive/api/auth.py` — `me`
- `backend/openarchive/api/schemas.py` — `AuthStatus`
- `backend/openarchive/api/deps.py` — `current_user`(`credential` 키: 토큰/세션 구분)
- `backend/tests/test_auth_api.py` — `/me`를 **dict 전체 일치**로 단언하는 테스트가 여럿 있다(익명·로그아웃·토큰·무효 Bearer)
- `frontend/src/lib/types.ts`의 `AuthStatus` 타입(읽기만 — 필드가 늘어도 프론트는 바꾸지 않는다)

## 작업

### 1) 테스트 먼저 — `backend/tests/test_auth_api.py`

1. read 토큰·read_write 토큰으로 `/me` → `scope`가 각각 `"read"`·`"read_write"`, 만료 없이 발급했으면 `expires_at`이 `null`.
2. 만료일을 정해 발급한 토큰(`POST /api/auth/tokens` body `expires_at` — #199 구현)으로 `/me` → `expires_at`이 발급 응답의 값과 같은 시각.
3. 쿠키 세션으로 `/me` → `authenticated: true`, `scope: null`, `expires_at: null`.
4. 익명·무효 Bearer·만료(`UPDATE api_tokens SET expires_at = now() - interval '1 second'`)·폐기된 토큰 → `authenticated: false`, `scope: null`, `expires_at: null`.
5. 공유 토큰으로 `/me` → 지금처럼 403(회귀).
6. 기존 dict 전체 일치 단언은 새 두 키를 포함하도록 **기대값을 늘린다**(단언을 부분 비교로 약화하지 마라).

### 2) 구현

- `AuthStatus`에 `scope: str | None = None`, `expires_at: datetime | None = None`.
- `validate_token`의 조회에 `t.expires_at`을 더해 반환 dict에 `expires_at` 키로 싣는다(사용자 토큰·공유 토큰 둘 다). 기존 키·의미는 바꾸지 않는다.
- `me`: `credential`이 토큰이면 `scope`·`expires_at`을 채우고, 세션이면 `None`. 다른 응답 경로(`login`·`logout`·`password`)의 `AuthStatus`는 새 필드를 비워 둔다(기본값).

## Acceptance Criteria

```bash
docker compose up -d
cd backend && .venv/bin/pytest tests/test_auth_api.py tests/test_auth.py tests/test_token_access.py tests/test_share_access.py tests/test_mcp_http.py -q
cd backend && .venv/bin/ruff check .
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. mutant 확인 — `me`에서 세션 분기를 지우고 늘 `user["scope"]`를 내면 테스트 3이 실패해야 한다. `expires_at`을 늘 `None`으로 내면 테스트 2가 실패해야 한다. 확인 뒤 되돌린다.
3. 아키텍처 체크리스트: 토큰 판정이 `validate_token` 한 곳에 남아 있는가, `api/deps.py`·`mcp_server/http.py`를 바꾸지 않았는가.
4. `phases/m30-user-cli/index.json`의 step 0을 갱신한다(summary에 `/me` 응답 필드와 세션·익명일 때 값).

## 금지사항

- `/me`에서 세션 주체에 `scope: "read_write"`를 내지 마라. 이유: D2 — `whoami`가 다루는 것은 토큰 범위이고, 세션 dict의 `scope`는 쓰기 판정용 내부 값이다.
- 공유 토큰을 `/me`에 열지 마라. 이유: 공유 주체는 사람 계정이 아니다(ADR-044 「공유」 결정 5).
- 사용자 CLI(`client.py`·`user_cli.py`)·프론트엔드를 건드리지 마라. 이유: 다음 step의 범위이며, 프론트는 새 필드를 쓰지 않는다.
- 기존 테스트를 깨뜨리지 마라
