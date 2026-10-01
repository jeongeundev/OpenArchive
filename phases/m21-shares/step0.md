# Step 0: shares-docs

#97 c(공유 주체·공유 토큰)의 결정을 문서에 기록한다. 코드는 바꾸지 않는다.

## 읽어야 할 파일

- `/docs/ADR.md` — **ADR-044** 전체(특히 「구현 형태 (2026-10-01)」의 결정 2·3 개정, 「관리 경로 (2026-10-01, #97 b)」), ADR-034(위임 토큰: 결정 2·4·5·6), ADR-027(볼 수 없는 문서는 없는 것처럼), ADR-040
- `/docs/ARCHITECTURE.md` — 「DB 스키마」 절(`document_grants`·`api_tokens`가 적힌 곳), 「API 설계」 절
- `/docs/UI_GUIDE.md` — 「열람 범위 · 업로드 대상 선택 (ADR-044, #97 b 구현 계약)」 절, `/settings` 화면 설명
- `/docs/PRD.md` — §6 열람 모델
- `backend/openarchive/services/visibility.py`, `backend/openarchive/migrations/025_grants_tables.sql`, `backend/openarchive/migrations/013_token_tables.sql`, `backend/openarchive/api/deps.py`

## 작업

ADR-044 끝(「관리 경로」의 API 표와 그 뒤 문단 다음, ADR-045 앞)에 **「공유 (2026-10-02, #97 c)」** 절을 추가하고, 상태 줄에 「2026-10-02 #97 c 결정 반영(공유 주체·공유 토큰)」을 덧붙인다. 아래 결정을 그대로 기록한다(문장은 다듬어도 되지만 내용을 바꾸거나 빼지 마라).

1. **공유는 외부 협업용 주체이며 소유자가 있다.** 로그인 사용자가 **세션으로** 만들고(ADR-034 결정 6), 공유에 넣을 수 있는 문서는 **자기가 소유한 문서뿐이다** — 열람 범위는 문서 소유자가 정한다는 관리 경로 결정 5와 같은 축이다. 관리자도 남의 문서를 공유에 넣지 못한다(ADR-040 경계). 공유는 소유자 계정이 지워지면 함께 지워진다(CASCADE). 공유 이름은 소유자 안에서만 UNIQUE다.
2. **공유 부여는 열람 범위(조직 공개/제한)와 별개 축이다.** 공유 주체에게 조직 공개는 열리지 않으므로(구현 형태 결정 2 개정) B사에 줄 제품 문서는 대개 조직 공개 문서다. 그래서 공유 부여는 `public`·`private` 문서 모두에 허용한다. 「조직 공개 + 부여 = 400」(관리 경로 결정 4)은 **사용자·그룹 부여에만** 적용한다. 열람 범위 교체(`PUT /api/documents/{id}/access`)는 사용자·그룹 부여만 교체하고 공유 부여는 건드리지 않는다.
3. **술어는 주체 값 하나를 유지하고, 공유 주체는 `share:<공유 uuid>`로 나타낸다.** 사용자 주체는 지금처럼 사용자명, 익명은 NULL이다. 값이 `share:`로 시작하면 술어는 **그 공유에 부여된 문서만** 참이 되고(소유자·조직 공개·사용자·그룹 부여 분기 없음), 아니면 기존 술어와 같다. 결정 4의 `app.principal`도 text 값 하나이므로 RLS 정책으로 옮기는 형태가 같다. 사용자명이 자유 텍스트라 `share:`로 시작하는 사용자명은 DB CHECK로 막는다 — 막지 않으면 그 사용자명이 공유 주체로 해석된다. 주체 값을 바인딩 하나 더(`%(share)s`)로 두는 안은 쿼리 13곳·서비스 시그니처·테스트 호출부 전부를 바꿔야 해서 기각했다.
4. **공유 토큰은 `api_tokens` 행이다.** `user_id`·`share_id` 중 정확히 하나를 가지며, 공유 토큰의 scope는 `read`뿐이다(DB CHECK). 공유 하나에 토큰 여러 개를 두고 각각 폐기한다. 공유를 지우면 그 토큰과 공유 부여가 CASCADE로 사라진다. 발급·폐기는 공유 소유자의 세션 전용이다. 원문은 발급 응답에서 한 번만 보인다(ADR-034).
5. **공유 토큰은 허용 목록의 읽기 경로만 통과한다.** 허용: `POST /api/search`, `GET /api/documents`, `GET /api/documents/progress`, `GET /api/documents/{id}`, `GET …/file`, `GET …/files/{file_version}`, `GET …/links`, `GET …/backlinks`, `GET …/related`, `GET …/versions/{version}`, `GET /api/clusters`, `GET /api/diagnostics`. 그 밖의 경로는 공유 토큰에 **403**이다 — `/api/principals`(조직 디렉터리 누출), `/api/system/status`(전체 잡 수 누출), `GET …/access`, `…/tag-suggestions`(편집 보조), `/api/auth/me`(사람 계정이 아니다), 모든 쓰기·세션 전용·관리 경로. 새 경로는 기본적으로 공유를 막는다(허용 목록에 넣어야 열린다).
6. **공유 주체의 접속은 REST만이다**(구현 형태 결정 3 개정 그대로). MCP는 바꾸지 않는다.

**API 형태** 표를 덧붙인다:

| 경로 | 계약 |
|---|---|
| `POST /api/shares` `{name}` · `GET /api/shares` · `DELETE /api/shares/{id}` | 내 공유 생성·목록(포함 문서 id·제목, 토큰 메타)·삭제. 세션 전용 |
| `PUT /api/shares/{id}/documents/{document_id}` · `DELETE …` | 공유에 내 문서 넣기·빼기(멱등 204). 세션 전용 |
| `POST /api/shares/{id}/tokens` `{name}` · `DELETE /api/shares/{id}/tokens/{token_id}` | 공유 토큰 발급(원문 1회)·폐기. 세션 전용 |

남의 공유 id는 404(존재를 드러내지 않는다). 공유에 넣으려는 문서가 안 보이면 404, 보이지만 남의 문서면 403.

**트레이드오프**에 적는다: ① 문서 상세의 `owner_id`(사용자명)가 공유 주체에게 보인다 — 출처 표시로 받아들인다. ② 공유 토큰 유출 시 그 공유 범위가 새며 만료가 없다(기존 트레이드오프 그대로). ③ 문서 소유자가 공유에 넣은 조직 공개 문서는 열람 범위 화면만으로는 외부 노출이 보이지 않으므로, 문서 상세 열람 범위 패널에 「내 공유에 포함」을 함께 보인다(UI).

그리고:
- `docs/ARCHITECTURE.md` 「DB 스키마」에 `shares` 테이블, `document_grants.share_id`, `api_tokens.share_id`·CHECK, `users.username` CHECK를 추가한다(026 마이그레이션 이름 `026_shares_tables.sql`). 「API 설계」에 위 경로를 추가한다.
- `docs/UI_GUIDE.md`: `/settings`에 「외부 공유」 절(공유 생성·삭제(확인)·포함 문서 목록과 빼기·토큰 발급(원문 한 번 표시)·폐기), 문서 상세 「열람 범위」 패널에 「내 공유에 포함」 체크(조직 공개·제한 둘 다에서 보이며 열람 범위 저장과 독립적으로 즉시 반영)를 적는다.
- `docs/PRD.md` §6에 공유 주체가 c에서 구현됨을 한 줄로 반영한다(기존 문장과 어긋나면 고친다).

## Acceptance Criteria

```bash
grep -n "공유 (2026-10-02, #97 c)" docs/ADR.md
grep -n "share:<" docs/ADR.md
grep -n "026_shares_tables" docs/ARCHITECTURE.md
grep -n "외부 공유" docs/UI_GUIDE.md
git diff --stat -- backend frontend   # 출력이 비어야 한다
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. ADR-044의 기존 문장 중 새 결정과 모순되는 것(예: "공유 주체 MCP", "share_documents")이 남아 있지 않은지 읽어 본다. 모순이면 개정 표시와 함께 고친다. **규칙 원문(금지어 문장)을 grep 회피용으로 지우지 마라.**
3. `phases/m21-shares/index.json`의 step 0을 갱신한다.

## 금지사항

- 코드·테스트·마이그레이션을 만들지 마라. 이유: 이 step은 결정 기록만 한다.
- ADR-045 이후 내용을 건드리지 마라.
- 기존 테스트를 깨뜨리지 마라
