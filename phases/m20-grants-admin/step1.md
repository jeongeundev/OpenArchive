# Step 1: groups-service

#97 b의 서비스 계층 첫 단계. 그룹·구성원 관리와 **부여 대상 이름 → id 해석**, 부여 행 삽입을 새
모듈 `services/grants.py`에 둔다. 라우터·문서 서비스는 다음 step들이 고친다.

## 읽어야 할 파일

- `/docs/ADR.md` — **ADR-044** 「관리 경로 (2026-10-01, #97 b)」(step 0이 추가) 전체
- `backend/openarchive/migrations/025_grants_tables.sql` — 테이블·FK cascade·부분 유니크 인덱스
- `backend/openarchive/services/auth.py` — `create_user`·`list_users`·`delete_user`와 예외 클래스 형식(이 모듈의 문체를 따른다)
- `backend/openarchive/services/visibility.py`
- `backend/tests/test_auth.py` — 서비스 테스트 형식(실 DB, `migrated_db` 픽스처)
- `backend/tests/test_visibility.py` — a가 부여를 직접 INSERT해 검증하는 방식
- `backend/tests/conftest.py`

## 작업

### 1) 테스트 먼저 — `backend/tests/test_grants.py`

실 DB(`migrated_db`)로 검증한다. 최소:
1. `create_group` → 반환에 id·name·created_at·members(빈 목록). 같은 이름 두 번 → `GroupAlreadyExists`. 앞뒤 공백은 잘라 저장하고, 빈 이름(공백만)은 `ValueError`.
2. `list_groups` → 이름순, 각 그룹의 `members`는 사용자명 이름순.
3. `add_member`는 멱등(두 번 해도 1행). 없는 그룹 → `GroupNotFound`, 없는 사용자 → `UserNotFound`(services.auth의 것을 재사용).
4. `remove_member`는 멱등(구성원이 아니어도 예외 없음). 없는 그룹·사용자는 3과 같다.
5. `delete_group` → 그 그룹의 `document_grants`가 cascade로 사라진다. 없는 그룹 → `GroupNotFound`.
6. `list_principals` → `{"users": [사용자명…], "groups": [그룹명…]}` 각각 이름순.
7. `resolve_grantees(conn, users=[...], groups=[...])` → `(user_ids, group_ids)`. 중복 이름은 하나로. 모르는 이름이 있으면 `UnknownGrantee` — 예외가 종류(`"user"`/`"group"`)와 **모르는 이름 전부**를 담는다(첫 번째만이 아니다).
8. `insert_grants(conn, document_id, user_ids, group_ids)` → 행이 정확히 그만큼 생기고, 같은 대상 두 번이면 한 행이다.
9. 그룹 구성원에게 부여한 private 문서가 `VISIBLE_TO_USER`로 보이고, `remove_member` 직후 안 보인다(서비스 함수로 만든 상태가 a의 술어와 맞물리는지 한 건만).

### 2) 구현 — `backend/openarchive/services/grants.py`

```python
class GroupAlreadyExists(Exception): ...
class GroupNotFound(Exception): ...
class UnknownGrantee(Exception):
    kind: Literal["user", "group"]
    names: list[str]

async def create_group(conn, name: str) -> dict
async def list_groups(conn) -> list[dict]          # {id, name, created_at, members: list[str]}
async def delete_group(conn, group_id: UUID) -> None
async def add_member(conn, group_id: UUID, username: str) -> None
async def remove_member(conn, group_id: UUID, username: str) -> None
async def list_principals(conn) -> dict            # {"users": [...], "groups": [...]}
async def resolve_grantees(conn, *, users: list[str], groups: list[str]) -> tuple[list[UUID], list[UUID]]
async def insert_grants(conn, document_id: UUID, user_ids: list[UUID], group_ids: list[UUID]) -> None
```

- `UnknownGrantee`의 메시지는 화면·API에 그대로 실린다: 예 `"알 수 없는 사용자: bob, carol"`, `"알 수 없는 그룹: 인사팀"`.
- `insert_grants`는 `ON CONFLICT DO NOTHING`으로 중복을 흡수한다(부분 유니크 인덱스라 충돌 대상 지정 없이 쓴다).
- 모듈 머리 docstring에 "부여는 사람이 내린 결정의 기록이라 앱이 INSERT한다(025 주석)"와 "그룹 부여는 관리자를 신뢰한다(ADR-044 관리 경로 결정 1)"를 적는다.

## Acceptance Criteria

```bash
docker compose up -d
cd backend && .venv/bin/pytest tests/test_grants.py -q
cd backend && .venv/bin/pytest -q -x
cd backend && .venv/bin/ruff check .
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 체크리스트: `grants.py`가 `services/documents.py`를 import하지 않는가(다음 step에서 documents가 grants를 import한다 — 순환 금지)? 열람 규칙을 Python으로 다시 구현하지 않았는가?
3. `phases/m20-grants-admin/index.json`의 step 1을 갱신한다. summary에 함수·예외 이름을 적는다.

## 금지사항

- `services/documents.py`를 import하지 마라. 이유: step 2에서 documents.py가 이 모듈을 import하므로 순환이 된다.
- 마이그레이션을 추가하지 마라. 이유: 025의 스키마로 충분하다. 그룹 이름 변경 칼럼·감사 칼럼은 범위 밖이다.
- 그룹 이름 변경 함수를 만들지 마라. 이유: 이름이 부여 지정 계약이다(ADR-044 관리 경로 결정 3).
- 관리자 자신을 그룹에 넣는 것을 막는 검사를 넣지 마라. 이유: 계정 생성으로 우회되는 가짜 경계다(결정 1).
- `embedding_jobs`·`document_edges`를 건드리지 마라. 이유: CLAUDE.md CRITICAL — 파생물은 트리거만 만든다.
- 기존 테스트를 깨뜨리지 마라
