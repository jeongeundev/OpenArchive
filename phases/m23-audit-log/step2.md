# Step 2: access-diff

`services/documents.py`의 `set_access`가 부여 대상을 **통째로 지우고 다시 넣는** 대신 **차이만** 반영하도록 바꾼다. 동작(결과 상태·응답·오류)은 바꾸지 않는다.

## 배경 (이 파일만 읽고 작업할 수 있도록)

- step 1이 `document_grants` INSERT·DELETE마다 감사 행(`access_changed`, `detail.kind = "grant"`)을 남기는 트리거를 만들었다(외부 공유 부여 `share_id IS NOT NULL`은 제외).
- 지금의 `set_access`는 `DELETE FROM document_grants WHERE document_id = … AND share_id IS NULL` 뒤에 전부 다시 INSERT한다. 이대로면 **아무것도 안 바꾼 저장도** 「bob 제거」「bob 추가」 기록을 쌍으로 남긴다. 감사 기록은 실제 변경만 보여야 한다.
- 열람 범위 값(public↔private) 기록은 step 1의 `UPDATE OF visibility … WHEN (OLD IS DISTINCT FROM NEW)` 트리거가 맡으므로 같은 값 UPDATE는 기록되지 않는다. 그래도 값이 바뀌지 않았으면 UPDATE에서 `visibility`를 쓰지 않아도 된다 — 재량. `updated_at` 갱신 여부는 지금 동작을 유지한다.

## 읽어야 할 파일

- `backend/openarchive/services/documents.py` — `set_access`(1251행 근처), `_read_access`, `_load_owner_document`, `_check_grantees`
- `backend/openarchive/services/grants.py` — `resolve_grantees`, `insert_grants`
- `backend/openarchive/migrations/029_audit_triggers.sql` — step 1의 부여 트리거
- `backend/tests/test_document_access.py`, `backend/tests/test_access_api.py` — 기존 열람 범위 테스트
- `backend/tests/test_audit_log.py` — step 0·1 테스트(헬퍼 재사용)
- `/docs/ADR.md` — ADR-044(부여·공유 축 분리, 「공유」 결정 2: 열람 범위를 고쳐도 공유 부여는 남는다), ADR-055

## 작업

### 1) 테스트 먼저 — `backend/tests/test_document_access.py`에 추가

`set_access`를 서비스 함수로 직접 부르고 `audit_log`의 `access_changed` 행을 센다. 행위자 GUC는 테스트 안에서 `set_config(..., true)`로 걸어도 되고 안 걸어도 된다(이 step은 행 수와 detail만 본다).

1. 제한 문서에 bob 부여가 있는 상태에서 **같은 값으로 다시 저장** → `access_changed` 새 행 0.
2. bob → bob + carol → 새 행 1 (`added`, `carol`).
3. bob + carol → carol → 새 행 1 (`removed`, `bob`).
4. 사용자 bob → 그룹 「재무팀」으로 교체 → 2행(`removed user bob`, `added group 재무팀`).
5. 조직 공개 → 제한 + bob 부여 → 2행(`kind:visibility`, `added bob`). 제한 + bob → 조직 공개(부여 대상 비움) → 2행(`kind:visibility`, `removed bob`).
6. 외부 공유 부여가 있는 문서의 열람 범위를 바꿔도 공유 부여는 남고 감사 행에 공유가 나타나지 않는다(기존 동작 유지 확인).
7. 모르는 사용자 이름이 섞이면 아무것도 바뀌지 않고 감사 행도 0(기존의 "실패하면 아무것도 바뀌지 않는다" 유지).

### 2) 구현 — `set_access`

- 잠금(`FOR NO KEY UPDATE`)과 이름 해석 순서는 그대로 둔다.
- 현재 사용자·그룹 부여(`share_id IS NULL`)를 읽고, 요청한 집합과 비교해 **빠진 것만 DELETE, 새것만 INSERT** 한다. INSERT는 기존 `insert_grants`를 재사용한다.
- 동시 교체 안전성은 기존 잠금이 보장한다 — 같은 문서 행을 `FOR NO KEY UPDATE`로 잡은 뒤 읽고 쓰므로 두 요청이 끼어들지 않는다. 주석의 근거 문장을 새 방식에 맞게 고친다.
- docstring에 "차이만 반영 — 감사 로그가 실제 변경만 남기게(ADR-055)"를 적는다.

## Acceptance Criteria

```bash
docker compose up -d
cd backend && .venv/bin/pytest tests/test_document_access.py tests/test_access_api.py tests/test_grants.py tests/test_share_access.py -q
cd backend && .venv/bin/pytest -q -x
cd backend && .venv/bin/ruff check .
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. mutant 확인: 차이 계산을 지우고 옛 방식(전부 DELETE 후 전부 INSERT)으로 되돌리면 테스트 1이 실패해야 한다. 통과하면 테스트를 보강한다.
3. 기존 열람 범위 테스트가 전부 그대로 통과하는지 확인한다 — 이 step은 결과 상태를 바꾸지 않는다.
4. `phases/m23-audit-log/index.json`의 step 2를 갱신한다.

## 금지사항

- 공유 부여(`share_id IS NOT NULL`)를 건드리지 마라. 이유: 공유는 열람 범위와 별개 축이다(ADR-044 「공유」 결정 2) — 지우면 열람 범위를 고칠 때마다 외부 공유가 조용히 끊긴다.
- 앱에서 `audit_log`에 INSERT하지 마라. 이유: 감사 로그는 DB가 쓴다(ADR-055 결정 2). 이 step은 트리거가 실제 변경에만 발화하게 쓰기를 줄일 뿐이다.
- `set_access`의 응답 모양·오류 메시지를 바꾸지 마라. 이유: 라우터·프런트가 그대로 쓴다.
- 기존 테스트를 깨뜨리지 마라
