# Step 5: github-actions

## 배경 — GitHub 위에서는 어떤 변경도 검증되지 않는다 (#60)

이 저장소의 실제 게이트는 로컬 `scripts/check.sh`(Stop 훅) 하나뿐이고, `.github/`에 워크플로가
**0개**다. `.github/` 아래에는 `ISSUE_TEMPLATE/` 두 개와 `PULL_REQUEST_TEMPLATE.md`만 있다.

**자동 검증이 없어서 실제로 결함을 놓친 적이 있다.** PR #59가 고친 결함(두 pytest 세션이 서로의
테스트 DB를 `DROP DATABASE ... WITH (FORCE)`로 파괴)은 로컬에서 한 번씩 돌리는 한 드러나지
않는다 — 제출 전 clean clone 점검 중에 우연히 잡혔다.

평가 항목 두 개에도 직접 걸린다: 「프로젝트 관리체계」의 표준 구성요소이고, 「오픈소스 프로젝트로의
발전 가능성」이 요구하는 *"누구나 실행 가능한 개발 환경"*의 **실행 가능한 명세**가 워크플로 파일
자체다.

### ✅ 결정 1 — 워크플로는 `check.sh`를 그대로 부른다

**이 결정은 이미 닫혔다.** 검증 항목을 워크플로에 다시 나열하지 마라. `check.sh`가 backend
ruff·pytest, frontend lint·test·build, 셸 구문 검사를 모두 돌린다. 두 곳에 나눠 적으면 로컬과
CI가 갈라지고, 그때부터 "로컬은 통과하는데 CI만 빨갛다"가 시작된다.

### ✅ 결정 2 — 상용 OpenSQL 없이 돈다

이 저장소는 `pgvector/pgvector:pg17` 컨테이너에서 **전체 테스트가 돈다**(ADR-007·026). 실
OpenSQL VM/EC2 대상 실행과 `demo_recovery.sh`는 상용 바이너리·실 VM 전제라 CI 범위 밖이다.
`EMBEDDING_PROVIDER`도 기본값 `fake`로 둔다 — BGE-M3는 4.3GB라 CI에서 비현실적이다 (ADR-003).

### 🔴 결정 3 — venv 경로를 `backend/.venv`로 정확히 맞춘다

`scripts/check.sh:20-27`은 `$ROOT/backend/.venv/bin/ruff`·`/bin/pytest`를 **직접 호출**한다.
활성화 상태에 의존하지 않는 설계다. 따라서 CI에서 `pip install`을 시스템 파이썬에 하면
*"backend: 검증 불가 — backend/.venv 없음"*이 뜨고 `FAILED=1`로 끝난다. **반드시
`backend/.venv`를 만들어 거기에 설치하라.**

## 읽어야 할 파일

- `scripts/check.sh` — 워크플로가 부를 유일한 명령. 특히 `ROOT` 결정 방식(:10)과 venv 직접 호출(:20-27)
- `docker-compose.yml` — DB 컨테이너 구성. 이미지·환경변수·**호스트 포트 5433**
- `backend/app/config.py:15` — 기본 DSN이
  `postgresql://openarchive:openarchive@localhost:5433/openarchive`다
- `backend/tests/conftest.py:20-35` — 테스트 DB는 PID로 이름을 격리한다(PR #59). **이 전제를
  무너뜨리는 변경을 하지 마라**
- `backend/pyproject.toml` — `requires-python = ">=3.12"`, `[project.optional-dependencies] dev`
- `frontend/package.json` — `next 16.3.0`. 로컬 개발 환경의 Node는 24.2.0이다
- `README.md:7-9` — 배지 세 개가 있는 자리
- `docs/ADR.md` ADR-026(로컬 개발 환경), ADR-013(브랜치·PR 규약)

## 작업

### 1) `.github/workflows/ci.yml`을 만든다

**트리거**: `push`는 `main`만, `pull_request`는 전부.

**서비스 컨테이너**:

```yaml
services:
  db:
    image: pgvector/pgvector:pg17
    env:
      POSTGRES_USER: openarchive
      POSTGRES_PASSWORD: openarchive
      POSTGRES_DB: openarchive
    ports:
      - 5433:5432
    options: >-
      --health-cmd "pg_isready -U openarchive -d openarchive"
      --health-interval 2s --health-timeout 2s --health-retries 15
```

- **포트는 5433으로 매핑한다.** 그래야 `config.py:15`의 기본 DSN과 `docker-compose.yml`이 그대로
  맞는다. 로컬과 CI가 같은 주소를 쓰는 것이 이 저장소의 재현성 원칙이다.
- 그럼에도 `DATABASE_URL`을 job env로 **명시**하라. 기본값에 의존하면 나중에 기본값이 바뀔 때
  CI가 조용히 다른 DB를 본다.
- `pg_isready`가 통과해도 첫 연결이 실패할 수 있다. `--health-*` 옵션을 위처럼 걸어 둔다.

**단계 순서**:

1. `actions/checkout`
2. `actions/setup-python` — `python-version: "3.12"`, `cache: pip`,
   `cache-dependency-path: backend/pyproject.toml`
3. `actions/setup-node` — `node-version: "24"`(로컬 개발 환경과 같은 메이저), `cache: npm`,
   `cache-dependency-path: frontend/package-lock.json`
4. backend 의존성 — `cd backend && python -m venv .venv && .venv/bin/pip install -e ".[dev]"`
5. frontend 의존성 — `cd frontend && npm ci`
6. `bash scripts/check.sh`

`CLAUDE_PROJECT_DIR`은 설정하지 마라. `check.sh:10`이 없으면 스크립트 위치 기준으로 루트를
잡으므로 CI에서는 그 경로가 옳다.

### 2) README에 배지를 추가한다

`README.md:7-9`의 배지 줄 **맨 앞**에 CI 배지를 넣는다. 저장소는
`github.com/jeongeundev/OpenArchive`다.

```markdown
[![CI](https://github.com/jeongeundev/OpenArchive/actions/workflows/ci.yml/badge.svg)](https://github.com/jeongeundev/OpenArchive/actions/workflows/ci.yml)
```

배지 문구를 과장하지 마라. 배지는 "이 워크플로가 마지막으로 어떻게 끝났는지"만 말한다.

### 3) 로컬에서 워크플로 문법을 확인한다

CI는 push 이후에야 실제로 돈다. 이 세션에서 할 수 있는 검증은 두 가지다:

- YAML이 파싱되는지 (`python -c "import yaml; yaml.safe_load(open(...))"`. `yaml` 모듈이 없으면
  설치하지 말고 이 검사를 건너뛰고 그 사실을 summary에 적어라)
- 워크플로가 부르는 명령이 로컬에서 실제로 도는지 (`bash scripts/check.sh`)

**`act` 같은 도구를 설치하려 하지 마라.** 새 의존성이며, 이 저장소의 SBOM을 바꾼다.

### 🔴 빨간불 확인은 사람의 몫이다

#60의 완료 조건에는 *"의도적으로 깨뜨린 커밋에서 빨간불이 뜨는 것을 한 번 확인한다"*가 있다.
이것은 PR을 올린 뒤에만 가능하므로 **이 step에서 하지 마라.** summary에 *"빨간불 확인은 PR
단계에서 사람이 수행"*이라고 남겨, 다음 사람이 잊지 않게 하라.

## Acceptance Criteria

```bash
# 1) 워크플로 파일이 생겼다
ls -l .github/workflows/ci.yml
#   → 파일 존재

# 2) YAML이 파싱된다 (yaml 모듈이 없으면 이 검사만 건너뛰고 summary에 적는다)
backend/.venv/bin/python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/ci.yml')); print('YAML OK')" \
  || echo "yaml 모듈 없음 — 이 검사는 건너뜀"
#   → "YAML OK" 또는 건너뜀 메시지

# 3) 워크플로가 check.sh를 부른다 (검증 항목을 따로 나열하지 않았다)
grep -c "scripts/check.sh" .github/workflows/ci.yml
#   → 1

# 4) venv 경로가 맞다
grep -c "backend/.venv\|python -m venv .venv" .github/workflows/ci.yml
#   → 1 이상

# 5) 포트 매핑이 5433이다
grep -c "5433:5432" .github/workflows/ci.yml
#   → 1

# 6) README 배지
grep -c "actions/workflows/ci.yml/badge.svg" README.md
#   → 1

# 7) 워크플로가 부르는 명령이 로컬에서 실제로 돈다
bash scripts/check.sh
#   → 마지막 줄에 "검증 실패"가 없어야 한다
```

## 검증 절차

1. 위 AC 커맨드를 순서대로 실행한다.
2. 체크리스트를 확인한다:
   - 검증 항목을 워크플로에 다시 나열하지 않았는가? (`check.sh` 한 줄이어야 한다)
   - 실 OpenSQL·`demo_recovery.sh`·BGE-M3를 CI에 넣지 않았는가?
   - `conftest.py`의 PID 기반 테스트 DB 이름을 고치지 않았는가?
3. `docs/` 문서는 이 step에서 고치지 않는다 (step 6에서 일괄 처리). README 배지만 예외다.
4. 결과에 따라 `phases/m11a-contract-seal/index.json`의 step 5를 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약 + 빨간불 확인은 PR 단계에서 사람이 수행"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- **검증 항목을 워크플로에 다시 나열하지 마라.** 이유: `check.sh`와 갈라지면 로컬과 CI가 다른
  것을 검사하게 된다.
- **시스템 파이썬에 의존성을 설치하지 마라.** 이유: `check.sh`가 `backend/.venv/bin/`을 직접
  호출한다. venv가 없으면 backend 검증이 통째로 건너뛰어지고 `FAILED=1`이 된다.
- **실 OpenSQL VM·EC2·`demo_recovery.sh`를 CI에 넣지 마라.** 이유: 상용 바이너리이고 실 VM
  전제다. CI에서 재현할 수 없다.
- **`EMBEDDING_PROVIDER=local`을 켜지 마라.** 이유: BGE-M3 모델이 4.3GB다. 기본값 `fake`로 돈다.
- **`conftest.py`의 PID 기반 테스트 DB 이름을 고정 이름으로 되돌리지 마라.** 이유: PR #59가 고친
  결함이 그대로 돌아온다 — 병렬 job이 서로의 DB를 파괴한다.
- **`act` 등 새 도구를 설치하지 마라.** 이유: 이 저장소의 의존성을 늘린다.
- **배포 자동화를 추가하지 마라.** 이유: 배포는 수동 절차로 남긴다 (`SETUP_OPENSQL.md` §14·§15).
- **`.github/` 아래 기존 템플릿을 고치지 마라.** 이유: 이 step의 범위는 CI 워크플로 하나다.
- **기존 테스트를 깨뜨리지 마라.**
