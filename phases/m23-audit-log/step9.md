# Step 9: audit-docs

감사 로그 구현을 문서에 반영한다. 코드는 고치지 않는다.

## 배경 (이 파일만 읽고 작업할 수 있도록)

이 phase(m23-audit-log, 이슈 #186)가 만든 것:

- **028 `audit_log`**: `id, occurred_at, action(CHECK 7종), actor(사용자명 스냅샷), actor_via(session|token|mcp|cli|share|worker), db_role(DEFAULT current_user), document_id(FK 없음), document_title(스냅샷), detail jsonb`.
- **029**: UPDATE·DELETE 행 트리거 + TRUNCATE 문 단위 트리거로 거부. 기록 트리거 — documents INSERT/DELETE/UPDATE OF visibility, document_versions INSERT(v2 이상), document_files INSERT(2판 이상), document_grants INSERT/DELETE(공유 부여 제외, 연쇄 삭제는 건너뜀), group_members INSERT/DELETE(연쇄 삭제는 건너뜀). `audit_record()`가 유일한 INSERT 지점이고 GUC를 `NULLIF(…, '')`로 읽는다. `record_original_download(document_id, file_version)`.
- **앱**: `services/audit.py`의 `set_actor`(유일한 행위자 설정 지점, `set_config(..., true)`, autocommit 트랜잭션 밖이면 거부) — REST는 `current_user`, stdio MCP는 `create_document`, 운영자 CLI는 import·demo, 워커는 OCR 반영. `set_access`는 차이만 반영. `GET /api/admin/audit`(관리자·세션 전용), `/admin/audit` 화면.
- **사용자 결정(#186 코멘트, 2026-10-05)**: ① 행위자 전달 방식은 유지(실측에서 새지 않음) ② 앱 행위자 없는 쓰기는 `actor` NULL + `db_role` ③ 공유 토큰 내려받기는 공유 이름으로 ④ TRUNCATE 거부 ⑤ 원본 미리보기를 내려받기로 셀지는 #190에서 ⑥ 부여 대상(사용자·그룹) 추가·제거도 「열람 범위 변경」으로 기록.
- **넘긴 것**: 명세서 「폴더 열람 범위 변경」 기록은 폴더가 생기는 **#187**에서 — 폴더 테이블 트리거와 `action` CHECK 확장.

**HA 실측(2026-10-05, #186 코멘트)** — VIP → 앱 풀 `openarchive`(`pool_mode = transaction`, read/write splitting), Primary node1, 임시 스키마의 트리거로 측정:

| # | 시나리오 | 결과 |
|---|---|---|
| M1 | 6클라이언트×25회 `BEGIN` → `set_config('openarchive.actor_id', …, true)` → INSERT → COMMIT | 150/150 트리거가 그 행위자를 봄, backend pid 일치, replica 0 |
| M2 | 같은 연결의 다음 트랜잭션 150 + 새 클라이언트 100이 행위자 없이 INSERT(동시 실행) | 누수 0/250, 전부 행위자를 실었던 백엔드 10개 위 |
| M3 | ROLLBACK 뒤 20회 | 누수 0/20 |
| M4 | COMMIT·ROLLBACK 없이 소켓 끊은 뒤 50회 | 누수 0/50 |
| M0 대조군 | 세션 `SET`을 남긴 뒤 새 클라이언트 100회(풀 백엔드 20개 전부 겹침) | 트랜잭션 밖 `SET` 0/100(초기화) · **트랜잭션 안 `SET` 75/100 누수** |

값이 없을 때 `current_setting(name, true)`는 NULL이 아니라 `''`이 온다(M2 250행 중 241행).

## 읽어야 할 파일

- `/docs/ADR.md` — ADR-055 전체, ADR-022, ADR-044 「관리 경로 1」, ADR-034, 그리고 `#186`이 나오는 모든 자리(`grep -n '#186' docs/ADR.md`)
- `/docs/ARCHITECTURE.md` — 「DB 스키마」·트리거 절
- `/docs/OPENSQL_RESEARCH.md` — §5-2(세션 상태 부분 초기화, single **session** 모드 실측), §12 검증 목록
- `/docs/OPERATIONS.md` — 운영 절차
- `/docs/PRD.md`, `/docs/ROADMAP.md` — `#186` 언급
- `CLAUDE.md` — 「감사 로그는 DB가 쓴다」 규칙(내용이 구현과 맞는지만 확인)
- `backend/openarchive/migrations/028_audit_tables.sql`, `029_audit_triggers.sql`, `backend/openarchive/services/audit.py`, `backend/openarchive/api/audit.py`

## 작업

1. **ADR-055**: 상태를 "구현 #186(m23)"으로. 「구현 때 정함」을 위 결정 ①~⑥으로 닫는다(⑤는 #190으로 넘김을 명시). 결정 1 표 아래에 부여 대상 변경도 기록함(⑥)과 그 이유(누가 열람 권한을 얻고 잃었는가가 접근 감사의 핵심), 연쇄 삭제를 건너뛰는 규칙과 이유(문서 삭제가 「부여 제거」 기록으로 덮이지 않게), 워커 표기(`actor_via='worker'`)를 더한다. 트레이드오프에 "그룹·사용자 삭제로 사라진 부여·구성원은 기록되지 않는다(그룹·사용자 삭제는 기록 대상이 아니다)"를 더한다. 결정 3에 실측 결과 요약(M1~M4 누수 0, 대조군 75/100)과 "행위자 설정은 `set_actor` 하나 — 아키텍처 테스트가 막는다"를 적는다.
2. **ADR 다른 자리·PRD·ROADMAP**의 "결정·미구현 #186" 표기를 구현됨으로 고친다. 폴더 열람 범위 변경 기록이 #187로 넘어갔음을 해당 자리에 적는다.
3. **ARCHITECTURE.md**: DB 스키마에 `audit_log`, 트리거 절에 감사 트리거 표(대상·조건·action), 행위자 전달 흐름(진입점 → `set_actor` → GUC → 트리거), `record_original_download`.
4. **OPENSQL_RESEARCH.md**: §5-2 뒤에 HA **transaction** 풀 실측(위 표)을 새 소절로 추가하고 §12 검증 목록에 한 줄. §5-2의 "session 모드" 실측과 구분해 적는다.
5. **OPERATIONS.md**: 감사 로그 절 — 어디서 보나(관리 메뉴), psql로 보는 SQL 예시, 직접 SQL 쓰기는 「직접 접속(롤)」로 남는다는 점, UPDATE·DELETE·TRUNCATE는 거부되며 소유자·슈퍼유저의 트리거 비활성화는 범위 밖, PITR 복원은 감사 기록도 그 시점으로 되돌린다(ADR-055 트레이드오프 3).
6. CLAUDE.md 규칙 문장이 구현과 다르면 최소한으로 맞춘다(다르지 않으면 고치지 않는다).

## Acceptance Criteria

```bash
grep -n "구현 때 정함" docs/ADR.md
grep -c "결정·미구현 #186" docs/ADR.md docs/PRD.md docs/ROADMAP.md
grep -n "audit_log" docs/ARCHITECTURE.md docs/OPERATIONS.md
grep -n "transaction" docs/OPENSQL_RESEARCH.md
cd backend && .venv/bin/pytest tests/test_architecture.py -q
```

(두 번째 커맨드의 결과는 파일마다 0이어야 한다.)

## 검증 절차

1. 위 AC 커맨드를 실행하고 결과를 확인한다.
2. 문서의 수치·이름(테이블·트리거·함수·엔드포인트)이 실제 코드와 일치하는지 마이그레이션·서비스 파일을 열어 대조한다.
3. 사용자 대상 문구 규칙("항상 최신"·"실시간 동기화" 금지, "원본 파일/문서 텍스트/추출 텍스트/텍스트 버전" 구분)을 지켰는가.
4. `phases/m23-audit-log/index.json`의 step 9를 갱신한다.

## 금지사항

- 코드·테스트·마이그레이션을 고치지 마라. 이유: 문서 step이다. 코드와 문서가 어긋나면 문서를 코드에 맞추고, 코드 결함으로 보이면 summary에 적어라.
- 규칙 문서(CLAUDE.md·ADR)의 기존 금지어 원문을 지우거나 쪼개지 마라. 이유: AC의 grep을 피하려고 원문을 지운 사례가 있었다(m11-a). 바꿀 것은 "미구현" 표기뿐이다.
- 실측 수치를 새로 만들어 내지 마라 — 위 표의 값만 쓴다.
- 기존 테스트를 깨뜨리지 마라
