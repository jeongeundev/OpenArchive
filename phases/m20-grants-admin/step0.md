# Step 0: grants-docs

이 phase(#97 b)는 #97을 네 PR(a 스키마·술어 → **b 그룹·부여 관리** → c 공유·공유 토큰 → d CLI·확정)로
나눈 것 중 두 번째다. a(PR #161)가 `groups`·`group_members`·`document_grants` 테이블과 열람 술어를
이미 넣었다. 지금은 부여를 만드는 경로가 테스트의 직접 INSERT밖에 없고, 기존 문서의 `visibility`를
바꾸는 API도 없다. b는 그 관리 경로를 만든다. 이 step은 **문서만** 고친다 — 뒤 step들이 이 문서를
근거로 구현한다.

## 읽어야 할 파일

- `/docs/ADR.md` — **ADR-044** 전체(특히 맨 끝 「구현 형태 (2026-10-01, #97 착수 결정)」), ADR-028(인증·관리자), ADR-034(위임 토큰, 결정 6 세션 전용 경계), ADR-040(남의 비밀번호 재설정을 웹에 두지 않은 이유 — is_admin이 문서 열람으로 번진다), ADR-027(존재 누출 금지)
- `/docs/ARCHITECTURE.md` — `services/visibility.py` 설명 단락(90행 근처), 스키마의 groups·document_grants 부분, API 표(`GET /api/admin/users` 등이 있는 표), 멱등키 표(「같은 키 + 다른 요청」)
- `/docs/UI_GUIDE.md` — 화면 목록(243행 근처 `/admin/users`)과 공개범위 문구가 나오는 곳 전부
- `/docs/PRD.md` — 열람 모델 절(§6)
- `backend/openarchive/migrations/025_grants_tables.sql`, `backend/openarchive/services/visibility.py`

## 작업

### 1) ADR-044 「구현 형태」 끝에 b 결정을 덧붙인다

상태 줄에 `· 2026-10-01 #97 b 결정 반영(그룹·부여 관리)`을 더한다. 새 단락 제목은
**「관리 경로 (2026-10-01, #97 b)」**로 하고 아래 다섯 결정을 근거와 함께 적는다.

1. **그룹은 관리자가 웹에서 관리하며, 그룹 부여는 관리자를 신뢰한다.** 관리자는 자기 자신이나
   새로 만든 계정을 그룹에 넣어 그 그룹에 부여된 제한 문서를 읽을 수 있다. "자기 추가 금지"는
   관리자가 계정을 만들 수 있는 한 우회되므로 두지 않는다. 디렉터리 관리자가 그룹을 쥐는 것은
   실무 관례(Google Workspace·AD)다. ADR-040이 막은 것은 **사용자 직접 부여·소유자 문서**까지 번지는
   경로였고, 그 경계는 그대로다 — **관리자도 보면 안 되는 문서는 그룹이 아니라 사용자에게 직접
   부여한다.** 트레이드오프로 남긴다.
2. **기존 문서의 열람 범위 변경은 세션 전용이다**(ADR-034 결정 6의 경계에 둔다). 이미 있는 문서의
   열람자를 넓히는 일은 관리이고, 문서를 공급하는 에이전트가 기존 제한 문서를 열 수 있으면 토큰
   유출이 곧 제한 문서 유출이다. **생성 시 대상 지정은 쓰기 토큰·MCP에도 허용한다** — 쓰기 토큰은
   원래 조직 공개 문서도 만들 수 있으므로 권한을 넓히지 않는다.
3. **부여 대상은 이름으로 지정한다**(사용자명·그룹명). 둘 다 UNIQUE이고 이름 변경 경로가 없다.
   MCP 에이전트와 d의 `import --grant`가 id를 먼저 조회하지 않아도 되고, 소유자가 사용자명
   (`owner_id`)으로 저장된 현재 계약과 맞는다. 모르는 이름은 400이며 그 이름을 짚는다.
4. **조직 공개(`public`) 문서에 부여 대상을 보내면 400이다.** 효력 없는 부여 행이라는 숨은 상태를
   남기지 않는다. 입력 기본값이 `public`이라 대상만 보내고 `visibility=private`를 빠뜨린 호출이
   여기 걸린다 — 문구가 `visibility=private`로 보내라고 안내한다. 열람 범위 교체는 통째로 하므로
   제한 → 조직 공개로 바꾸면 부여가 함께 사라진다.
5. **열람 범위는 소유자만 보고 바꾼다.** 볼 수 있는 비소유자는 403, 볼 수 없는 사람은 404(ADR-027 —
   403이면 존재가 샌다). 부여 대상 목록(`GET /api/principals`, 사용자명·그룹명)은 로그인 사용자에게
   열린다 — 조직 디렉터리이며 문서의 존재를 누출하지 않는다. 익명은 401.

같은 단락에 API 형태를 짧게 적는다:
`/api/admin/groups`(생성·목록·삭제, `PUT|DELETE /{id}/members/{username}`, 관리자·세션 전용),
`GET /api/principals`, `GET /api/documents/{id}/access`(로그인), `PUT /api/documents/{id}/access`
(세션 전용, 본문 `{visibility, users, groups}`), 업로드 Form·텍스트 JSON·MCP `create_document`의
`grant_users`·`grant_groups`. 그룹 이름 변경은 두지 않는다(이름이 지정 계약이다). 그룹을 지우면
부여가 cascade로 사라져 그 문서는 더 좁아진다(소유자·다른 부여만).

### 2) ARCHITECTURE.md

- API 표에 위 엔드포인트를 추가한다(권한 칸: 관리자·세션 전용 / 로그인 / 소유자·세션 전용).
- 멱등키 표의 요청 지문 설명에 **부여 대상**을 추가한다(파일·텍스트 둘 다).
- 서비스 목록이 있으면 `services/grants.py`(그룹·구성원·대상 이름 해석)를 추가한다.

### 3) UI_GUIDE.md

- 243행 `/admin/users`의 "열람 권한은 넓히지 않는다" 문장을 결정 1에 맞게 고친다: 관리자는 계정과
  그룹을 관리하고, 그룹 구성원 변경은 그 그룹에 부여된 문서의 열람을 바꾼다. 남의 비밀번호 경로는
  여전히 없다(ADR-040). `/admin/groups` 화면 항목을 추가한다.
- 문서 상세의 「열람 범위」 패널(소유자에게만 표시)과 업로드의 대상 선택을 화면 설명에 추가한다.
- 화면 문구의 공개범위 값을 **「조직 공개 / 제한」**으로 통일한다(값 `public`/`private`는 그대로).

## Acceptance Criteria

```bash
grep -n "관리 경로 (2026-10-01, #97 b)" docs/ADR.md
grep -n "/api/principals" docs/ADR.md docs/ARCHITECTURE.md
grep -n "/admin/groups" docs/UI_GUIDE.md
grep -n "조직 공개" docs/UI_GUIDE.md
git diff --stat -- backend frontend | tail -1   # 출력 없음이어야 한다
```

## 검증 절차

1. 위 AC 커맨드를 실행한다. 마지막 줄은 아무것도 출력하지 않아야 한다(코드 무변경).
2. ADR-044의 기존 결정·실측 문단을 지우거나 고쳐 쓰지 않았는지 `git diff docs/ADR.md`로 확인한다 — 추가만 있어야 한다(상태 줄 제외).
3. `phases/m20-grants-admin/index.json`의 step 0을 갱신한다. summary에 결정 5개의 한 줄 요약과 API 경로를 적는다.

## 금지사항

- 코드·테스트를 고치지 마라. 이유: 이 step은 뒤 step의 근거 문서다.
- ADR-044의 기존 문단(결정 1~5, 기각한 대안, 기존 「구현 형태」 항목)을 고쳐 쓰지 마라. 이유: 결정 이력은 덧붙여 남긴다.
- ADR-040·ADR-028 본문을 고치지 마라. 이유: 개정 사실은 ADR-044 쪽에 적고, 그 ADR들이 말한 경계(비밀번호·사용자 직접 부여)는 그대로 유효하다.
- 공유(share)·공유 토큰·`import --grant`를 결정으로 적지 마라. 이유: #97 c·d의 범위다.
