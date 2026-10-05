# Step 5: actor-cli

운영자 CLI(`openarchive …`, DSN으로 직접 붙는 경로)가 감사 대상 테이블을 쓸 때 행위자를 DB에 넘기게 한다.

## 배경 (이 파일만 읽고 작업할 수 있도록)

- 감사 로그는 DB 트리거가 쓰고(step 0·1), 행위자는 트랜잭션 범위 GUC로 넘긴다. 수단은 `backend/openarchive/services/audit.py`의 `set_actor(conn, *, actor, via, share_id=None)` 하나뿐이다(step 3). `set_config(..., true)`만 쓰며, **autocommit 연결에서 트랜잭션 밖이면 `RuntimeError`**를 던진다 — 트랜잭션 밖에서 건 값은 그 문장과 함께 사라져 효과가 없기 때문이다.
- **운영자 CLI는 autocommit 연결을 쓴다**(`cli.py`의 `_connect(dsn, autocommit=True)`). 그래서 행위자는 문서를 쓰는 **각 `async with conn.transaction():` 블록 안에서** 걸어야 한다. 바깥 블록에서 한 번 걸면 첫 트랜잭션이 끝나는 순간 사라진다.
- 감사 대상 테이블: `documents`(생성·삭제·열람 범위), `document_versions`(v2 이상), `document_files`(2판 이상), `document_grants`(공유 아닌 부여), `group_members`.
- 행위자 결정:
  - `--user`를 받는 쓰기 명령(`import`, `demo` — 넣은 문서의 소유자) → `actor=<--user>`, `via="cli"`.
  - `--user` 없이 감사 대상을 쓰는 운영자 명령이 있으면(예: 재추출이 텍스트 버전을 만드는 경로) → `actor=None`, `via="cli"`. 화면에는 「운영자 CLI」로 보인다. 그런 경로가 있는지 직접 확인하라 — `reextract_one`·`reextract_all`이 문서 텍스트를 직접 바꾸는지, 추출 잡만 거는지(그러면 워커가 반영하고 step 6이 맡는다) 코드로 판단한다.
  - 계정 생성·비밀번호 재설정·관계 재계산처럼 감사 대상이 아닌 명령은 건드리지 않는다.
- 사용자 CLI(REST 클라이언트, #189)는 아직 없다. 생기면 REST 토큰 경로라 step 3이 이미 처리한다.

## 읽어야 할 파일

- `backend/openarchive/services/audit.py` — step 3 산출물
- `backend/openarchive/cli.py` — `_import_file`(916행 근처, `conn.transaction()` 안에서 `create_document`·`create_text_document`), `_import`, `_demo`, `_reextract_one`·`_reextract_all`
- `backend/openarchive/demo.py` — `seed_documents`(autocommit 연결로 `create_text_document`를 반복 호출)
- `backend/openarchive/services/documents.py` — `create_text_document`·`create_document`가 내부에서 트랜잭션을 여는지, `reextract_one`·`reextract_all` 위치
- `backend/tests/test_cli.py`, `backend/tests/test_cli_archive.py`, `backend/tests/test_demo.py` — CLI 테스트 헬퍼
- `backend/tests/test_audit.py` — 감사 행 조회 헬퍼

## 작업

### 1) 테스트 먼저

1. `test_cli_archive.py`(또는 import 테스트가 있는 파일): `import --user alice`로 파일 3개를 넣으면 `document_created` 3행, 전부 `actor='alice'`, `actor_via='cli'`.
2. `test_demo.py`: 예제 적재(`seed_documents`)로 만든 문서 수만큼 `document_created`, 전부 `actor`=그 사용자, `via='cli'`.
3. `--user` 없이 감사 대상을 쓰는 명령이 있다고 판단했으면, 그 명령의 기록이 `actor IS NULL`, `via='cli'`임을 단언한다. 없다고 판단했으면 그 근거를 summary에 적는다.

### 2) 구현

- `_import_file`의 `async with conn.transaction():` 안 첫 줄에 `await set_actor(conn, actor=username, via="cli")`.
- `seed_documents`: 문서마다 `async with conn.transaction():`로 감싸 그 안에서 `set_actor` 후 `create_text_document`. 호출자가 이미 트랜잭션을 열었으면 SAVEPOINT가 되므로 안전하다. 시그니처는 바꾸지 않는다(소유자 인자를 행위자로 쓴다).
- 3에서 찾은 경로가 있으면 같은 방식.

## Acceptance Criteria

```bash
docker compose up -d
cd backend && .venv/bin/pytest tests/test_cli.py tests/test_cli_archive.py tests/test_demo.py tests/test_architecture.py -q
cd backend && .venv/bin/pytest -q -x
cd backend && .venv/bin/ruff check .
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. mutant 확인: `_import_file`의 `set_actor`를 `conn.transaction()` **바깥**으로 옮기면 `RuntimeError` 또는 테스트 1 실패가 나야 한다. 조용히 통과하면 테스트를 보강한다.
3. `phases/m23-audit-log/index.json`의 step 5를 갱신한다. summary에 행위자를 건 명령 목록과, `--user` 없는 경로 판단 근거를 적는다.

## 금지사항

- `SET openarchive.…`·`set_config(...)`를 직접 쓰지 마라 — `set_actor`만. 이유: 세션 범위 SET은 다른 클라이언트로 샌다(HA 실측 75/100).
- 연결을 autocommit=False로 바꿔 문제를 피하지 마라. 이유: CLI의 다른 명령들이 autocommit을 전제로 짜여 있다 — 범위 밖 변경이다.
- 서비스 함수 시그니처에 행위자 인자를 추가하지 마라. 이유: 행위자는 연결의 트랜잭션 상태이지 서비스 인자가 아니다 — REST·MCP와 같은 헬퍼 하나로 간다.
- 기존 테스트를 깨뜨리지 마라
