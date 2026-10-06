# Step 7: folder-docs

ADR-054의 「구현 때 정함」을 확정하고, ADR-011에 iterative_scan 채택을 개정 표기하며, 아키텍처·운영 문서와 CLAUDE.md 규칙을 이 phase의 결과에 맞춘다. 코드는 고치지 않는다.

## 공통 배경 — m25-folders 설계 결정 (모든 step 같음)

이슈 #187의 폴더 백엔드다(목록 찾기는 m24-doc-finder, 화면은 다음 phase, CLI `import --keep-folders --grant-group`은 그다음 phase). 근거 ADR-054, 스파이크 `notes/folder-acl-spike-20261005.md`(로컬 전용 — 없으면 아래 요약으로 충분하다). 2026-10-06 사용자 결정:

- **D1 범위는 최상위 폴더만 갖는다.** 최상위 폴더는 「조직 공개(public)」 또는 「제한(private) + 사용자·그룹 부여」. 하위 폴더는 항상 「상위 폴더 범위 따름」 — 자기 범위가 없다. 새 최상위 폴더 기본은 조직 공개.
- **D2 폴더 이동(폴더를 다른 폴더 아래로)은 없다.** 문서의 폴더 이동만 있다. 그래서 폴더의 최상위 조상은 만든 뒤 바뀌지 않는다.
- **D3 폴더를 볼 수 있는 사용자는 그 안에 하위 폴더를 만들고 자기 문서를 넣을 수 있다.** 넣은 문서는 기본 「폴더 범위 따름」이다.
- **D4 폴더를 만든 계정은 삭제를 거부한다** — 문서를 소유한 계정 삭제 거부(`services/auth.py` `delete_user`)와 같은 방식. 권한 이전 기능은 없다.
- **D5 볼 수 없는 폴더 안의 문서가 「개별 지정」으로 보이면** 문서는 목록·검색에 나오되 폴더 정보(id·이름·경로)는 어디에도 나오지 않는다. 🔒 같은 자리 표시도 없다(CLAUDE.md: 표시 자체가 존재를 누출한다).
- **D6 폴더 문서 수와 폴더별 목록은 그 폴더에 직접 든 문서만** 센다(하위 폴더 제외). 검색 폴더 필터만 하위 폴더를 포함한다.
- **D7 벡터 검색에 `SET LOCAL hnsw.iterative_scan = strict_order`를 건다.** 스파이크: 열람 범위가 좁은 사용자(문서 5.9%)의 recall@10 0.40 → 1.00, 폴더 필터 0.27 → 0.99. ADR-011 보강 3의 "켜지 않는다"를 뒤집는 결정이다.
- **D8 경계**: 폴더 열람 범위 변경은 **세션 전용**(`require_session_user`), 관리자도 불가 — 폴더를 **만든 사람만**. 폴더 만들기·이름 변경·삭제·문서 폴더 이동은 문서 쓰기와 같은 경계(`require_write_user_id`, 토큰 허용). 이름 변경·삭제는 만든 사람 또는 관리자(볼 수 있는 폴더에 한함). 문서 폴더 이동은 문서 소유자만. MCP는 이번에 바꾸지 않는다.
- 그 밖의 ADR-054 결정: 폴더 범위 변경·그룹 구성원 변경은 **조회 시점 판정**으로 즉시 반영(실효 범위를 저장하지 않는다). 폴더를 만든 사람은 자기 제한 폴더 안의 남의 문서(「폴더 범위 따름」)도 본다. 「개별 지정」 문서는 폴더 범위와 무관하게 문서 자신의 `visibility`·부여로 판정하며, 폴더를 만든 사람에게도 그 판정대로다. 문서 소유자는 언제나 자기 문서를 본다. 외부 공유 주체(`share:<uuid>`)는 폴더를 보지 못하고, 공유에 부여된 문서만 본다(지금과 같음). 삭제는 빈 폴더만("폴더가 비어 있지 않습니다.") — 하위 폴더가 있어도 비어 있지 않다.
- 술어 형태(스파이크 A1): 문서의 폴더에서 **조상으로 거슬러 올라가는 상관 재귀 `EXISTS (WITH RECURSIVE …)`**. 비상관 `IN (서브쿼리)`는 금지 — 3천 청크에서 HNSW를 버리고 generic plan에서 15배 느려졌다. `db.py`의 `prepare_threshold=None`은 유지한다.

### 스키마 (step 0이 만든다 — `030_folders_tables.sql`)

- `folders(id uuid PK, parent_id uuid NULL → folders ON DELETE RESTRICT, name text, created_by text(사용자명, documents.owner_id와 같은 방식), visibility text NULL, created_at, updated_at)`
  - CHECK: 이름은 공백뿐이 아니고 `/`를 포함하지 않는다. `visibility IN ('public','private')`. **최상위만 범위를 갖는다**: `(parent_id IS NULL) = (visibility IS NOT NULL)`.
  - 같은 부모 아래 이름 유일(하위 폴더만). **최상위 이름은 유일하게 하지 않는다** — 남의 제한 폴더 이름과 충돌한다는 오류가 그 폴더의 존재를 누출한다.
- `folder_grants(folder_id → folders ON DELETE CASCADE, user_id → users CASCADE, group_id → groups CASCADE, created_at)` — 대상 정확히 하나(CHECK), 대상별 부분 유니크 인덱스. 외부 공유 부여는 없다.
- `documents.folder_id uuid NULL → folders ON DELETE RESTRICT`, `documents.follows_folder boolean NOT NULL DEFAULT true`. 판정: `folder_id IS NOT NULL AND follows_folder`이면 폴더(최상위) 범위, 아니면 문서 자신의 `visibility`·`document_grants`.

## 이 phase가 만든 것 (step 0~6)

- 마이그레이션 `030_folders_tables.sql`(folders·folder_grants·documents.folder_id·follows_folder), `031_folder_audit_triggers.sql`(`folder_access_changed`, 문서 `access_changed`의 `kind:'inherit'`·`kind:'folder'`)
- `services/visibility.py` — 최상위 범위 판정 조각 하나를 문서 술어·폴더 술어(`FOLDER_VISIBLE_TO_USER`)가 공유
- `services/folders.py`, `services/documents.py`(폴더 지정·이동·개별 지정·상세 폴더), `services/auth.py`(폴더 만든 계정 삭제 거부)
- `services/search.py` — `SET LOCAL hnsw.iterative_scan = strict_order`, 검색 폴더 필터(직접 결과만, 하위 포함)
- `api/folders.py`와 문서 라우터 변경 — 폴더 범위 변경만 세션 전용
- 각 step의 summary(`phases/m25-folders/index.json`)에 실측·EXPLAIN 결과가 있다 — 문서에 옮길 수치는 거기서 가져온다.

## 읽어야 할 파일

- `/docs/ADR.md` — ADR-054 전체(3939행 근처), ADR-011(보강 3·4·5), ADR-044(결정 2·4, 「컬렉션 단위 부여 안 함」 개정 표시), ADR-055(감사 동작 목록)
- `/docs/ARCHITECTURE.md` — 스키마·열람 술어·검색·감사 절
- `/docs/UI_GUIDE.md` 154~165행(열람 범위·업로드 대상 선택 — 157행에 "#187에서 개정 예정")
- `/docs/OPERATIONS.md` — API·권한 경계 표가 있으면
- `/docs/PRD.md`, `/docs/ROADMAP.md` — 폴더 관련 서술
- `CLAUDE.md` — 아키텍처 규칙 절
- `notes/folder-acl-spike-20261005.md` §3·§4 (로컬에 있으면 — ADR 문장 초안이 있다)
- `phases/m25-folders/index.json` — step summary

## 작업

- **ADR-054** 상태 줄에 구현 표기(#187, m25-folders). 「구현 때 정함」 각 항목을 결정으로 바꾼다:
  - 술어 형태: 조회 때 상관 재귀 `EXISTS`, 실효 범위 저장 안 함 — 스파이크 수치(로컬 3,120문서·58,620청크 p50 20.7ms 대 저장형 19.5ms, 깊이 12에서 23.2 대 20.1, VM 동일 계획), 비상관 `IN` 금지 이유, RLS 전환 시 `folders` 자기 참조 정책의 무한 재귀.
  - 볼 수 없는 폴더 안 개별 지정 문서: 폴더 정보 없음(D5).
  - 하위 폴더 자기 범위: 없음(D1), 폴더 이동 없음(D2) — 이유와 재검토 조건.
  - 남의 폴더 안 하위 폴더: 허용(D3). 만든 사람 계정 삭제: 거부(D4).
  - 추가로 정한 것: 최상위 이름 유일성 없음(존재 누출), 폴더 범위 요약을 폴더 열람자에게 보임(업로드 표시), 폴더 문서 수는 직속·볼 수 있는 것만(D6), 비어 있지 않은 폴더 판정이 볼 수 없는 문서도 센다는 트레이드오프, 폴더 지정 업로드 문서의 자기 범위는 private로 닫힘.
- **ADR-011**에 개정 블록: `hnsw.iterative_scan = strict_order` 채택(D7) — 스파이크 recall 0.40→1.00·폴더 필터 0.27→0.99, `relaxed_order`를 안 쓴 이유, VM 비용 약 1.5배, `MAX_K * 배수 < EF_SEARCH` 불변식은 안전망으로 유지. 보강 3의 "켜지 않는다"는 지우지 말고 개정 표기로 쌓는다(기존 ADR 개정 관례).
- **ADR-044**·**ADR-055**: 폴더 관련 개정 표기가 「결정·미구현」이면 「구현」으로. ADR-055 동작 목록에 `folder_access_changed`와 문서 `access_changed`의 새 `kind` 두 개.
- **ARCHITECTURE.md**: 스키마(030), 열람 술어 형태(공유 조각·상관 재귀), 검색 설정(iterative_scan)·폴더 필터(직접 결과만), 폴더 API와 경계, 감사 트리거(031).
- **UI_GUIDE.md** 157행의 "개정 예정"을 실제 규칙으로(폴더를 고르면 「폴더 범위 따름(…)」, 업로드에서 개별 지정 불가 — 상세에서). 화면 상세는 다음 phase가 채운다는 한 줄.
- **CLAUDE.md** 아키텍처 규칙: 폴더 상속 술어는 상관 `EXISTS`로 쓰고 비상관 `IN`을 쓰지 않는다 + 벡터 검색 트랜잭션에 `iterative_scan = strict_order`를 건다(근거 수치 한 줄씩). 기존 규칙 문구를 지우거나 고쳐 쓰지 말고 더한다.
- `OPERATIONS.md`·`PRD.md`·`ROADMAP.md`: 해당 서술이 있으면 맞춘다.

## Acceptance Criteria

```bash
grep -n "iterative_scan" docs/ADR.md docs/ARCHITECTURE.md CLAUDE.md
grep -n "folder_access_changed" docs/ADR.md docs/ARCHITECTURE.md
git diff --stat -- backend frontend   # 출력 없음(코드 변경 없음)
```

## 검증 절차

1. 위 AC 커맨드를 실행한다 — 앞의 둘은 각 파일에서 한 줄 이상, 마지막은 비어 있어야 한다.
2. 문서의 수치·이름(함수·엔드포인트·마이그레이션 번호)이 코드와 step summary와 맞는지 대조한다.
3. `phases/m25-folders/index.json`의 step 7을 갱신한다.

## 금지사항

- 규칙 문서의 금지어 원문(예: `BEGIN READ ONLY`, `is_admin`, `avg(`)을 지우거나 쪼개 쓰지 마라. 이유: 과거에 AC grep을 피하려고 규칙 원문이 지워진 사고가 있었다 — 규칙은 원문 그대로 남는다.
- 기존 ADR 본문을 덮어쓰지 마라 — 개정 블록·표기로 쌓는다. 이유: 결정의 이력이 ADR의 가치다.
- 사용자 대상 문구에 "항상 최신"·"실시간 동기화"를 쓰지 마라. 이유: CLAUDE.md(보장 범위는 버전 일관성 + 최신 수렴).
- 코드 파일을 고치지 마라
