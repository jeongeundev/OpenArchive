# Contributing to OpenArchive

## 개발 환경

**OpenSQL 라이선스 없이 아래 절차와 `scripts/check.sh`가 그대로 완주합니다.** 무엇이 로컬로
되고 무엇이 실 OpenSQL 환경을 요구하는지는 [README 「빠른 시작」](README.md#빠른-시작)의 표에
있습니다.

```bash
docker compose up -d                 # 로컬 DB (pgvector 컨테이너)

cd backend
python3 -m venv .venv && source .venv/bin/activate   # scripts/check.sh가 backend/.venv를 찾는다
pip install -e ".[dev]"
uvicorn app.main:app --reload        # API — 마이그레이션이 여기서 실행된다
python -m app.worker                 # 임베딩 워커 (별도 프로세스)

# 최초 관리자 계정 — 자체 가입이 없으므로 UI 로그인 전에 한 번 실행한다
cd .. && ADMIN_PASSWORD='<초기 비밀번호>' python scripts/create_admin.py admin --admin

cd frontend && npm install && npm run dev
```

검증은 한 번에:

```bash
bash scripts/check.sh
```

> **가상환경 주의**: `scripts/check.sh`는 `backend/.venv/bin`의 실행 파일을 직접 부릅니다.
> `.venv` 없이 의존성을 설치하면 검증이 "backend/.venv 없음"으로 실패합니다.

> **기동 순서 주의**: 마이그레이션은 API 서버만 실행합니다 (ADR-012). 워커나 MCP 서버를 먼저 띄우면 스키마가 없어 실패합니다.

> **환경변수 파일 주의**: `.env`를 만든다면 위치는 `backend/.env`입니다. 저장소 루트에 두면
> 어느 프로세스도 읽지 않습니다 ([README 「환경변수 파일」](README.md#환경변수-파일은-backendenv-하나입니다)).

---

## 브랜치 전략

`main` 하나에 작업 브랜치를 붙이는 **GitHub Flow**입니다. `develop`·`release/*`·`hotfix/*`는 쓰지 않습니다 (근거: [ADR-013](docs/ADR.md)).

```
feat/   새 기능
fix/    버그 수정
docs/   문서
test/   테스트
chore/  빌드·설정
```

예: `feat/embedding-worker`, `fix/worker-race`, `docs/adr-revision`

---

## 커밋 메시지

[Conventional Commits](https://www.conventionalcommits.org/)를 따릅니다.

```
<type>(<scope>): <설명>
```

**타입**: `feat` `fix` `docs` `test` `refactor` `chore` `ci` `perf`

**스코프**: `db` `worker` `api` `search` `mcp` `frontend` `adr`

```
feat(worker): SKIP LOCKED 기반 잡 claim 구현
test(db): 트리거가 embedding_jobs를 생성하는지 검증
fix(search): BEGIN READ ONLY를 plain BEGIN으로 교체
docs(adr): ADR-006 OpenProxy 경유로 재작성
```

### TDD와 커밋 순서

이 프로젝트는 테스트를 먼저 작성합니다. 커밋도 그 순서를 따르십시오.

```
test(db): 문서 수정 시 재임베딩 잡이 생성되는지 검증   ← 실패하는 테스트
feat(db): content_hash 변경 트리거 추가                ← 통과시키는 구현
```

---

## Pull Request

**PR 하나 = 작업 단위 하나**입니다. 머지는 **Squash merge**로 통일합니다.

1. 브랜치를 만들고 작업합니다
2. `bash scripts/check.sh`가 통과하는지 확인합니다
3. PR을 엽니다 — 템플릿의 체크리스트를 채웁니다
4. 코드 리뷰를 남깁니다 (아래)
5. Squash merge

### 코드 리뷰

1인 프로젝트라도 리뷰 흔적을 남깁니다.

- `/code-review` 실행 결과를 PR 리뷰 코멘트로 게시
- 지적사항이 있으면 수정 커밋을 올린 뒤 머지

---

## 설계 규약

구현 전에 [`CLAUDE.md`](CLAUDE.md)의 CRITICAL 규칙을 확인하십시오. 특히:

- **임베딩 잡 생성은 DB 트리거만 한다.** 애플리케이션에서 `embedding_jobs`에 직접 INSERT 금지
- **검색은 plain `BEGIN`으로 감싼다.** `BEGIN READ ONLY`는 Replica로 라우팅되어 정합성이 깨진다
- **DB 접속은 OpenProxy VIP 단일 엔드포인트.** 멀티호스트 DSN 금지
- **스키마 변경은 번호 붙은 raw SQL로만.** ORM 마이그레이션 도구 금지

설계 결정을 바꾸려면 [ADR](docs/ADR.md)에 근거를 남기십시오. 인프라·접속·인덱스 관련 결정 전에는 [OpenSQL 조사 결과](docs/OPENSQL_RESEARCH.md)를 먼저 확인하십시오.

---

## 라이선스

기여한 코드는 [MIT License](LICENSE)로 배포됩니다.

새 의존성을 추가할 때는 라이선스를 확인하십시오. 이 프로젝트는 의존하는 모든 오픈소스의 출처와 라이선스를 공개합니다.
