이 프로젝트에서 여러 레이어·모듈에 걸친 신규 기능을 구현할 때 Harness 프레임워크를 사용한다. 아래 워크플로우에 따라 작업을 진행하라.

> 적용 범위 밖: 하네스 자체(`scripts/execute.py`·`scripts/ship.py`, 이 문서)를 고치는 작업. 이런 작업은 이 워크플로우를 거치지 않고 바로 TDD로 진행한다.
>
> 한 모듈 안에서 끝나는 작은 작업(단순 버그 수정 등)도 이 워크플로우로 시작한다 — C에서 step 초안 대신 **TDD 계획**으로 갈라진다(C-2). 판정을 따로 묻지 않는다. 초안의 모양이 곧 판정이다.

---

## 워크플로우

### A. 탐색

`/docs/` 하위 문서(PRD, ARCHITECTURE, ADR 등)를 읽고 프로젝트의 기획·아키텍처·설계 의도를 파악한다. 필요시 Explore 에이전트를 병렬로 사용한다.

### B. 논의

구현을 위해 구체화하거나 기술적으로 결정해야 할 사항이 있으면 사용자에게 제시하고 논의한다.

### C. 계획 초안

탐색 결과로 초안의 종류를 고른다. 초안 첫 줄에 판정과 근거를 한 줄로 밝히고(예: "`services/parsing.py` 한 곳이라 step 없이 TDD로 진행한다"), 판정 때문에 따로 묻지 않는다 — 애매할 때만 묻는다. 사용자는 늘 「초안 승인 → 끝까지 진행」 한 번으로 같다.

#### C-1. Step 설계 — 여러 레이어·모듈에 걸치는 작업

사용자가 구현 계획 작성을 지시하면 여러 step으로 나뉜 초안을 작성해 피드백을 요청한다.

설계 원칙:

1. **Scope 최소화** — 하나의 step에서 하나의 레이어 또는 모듈만 다룬다. 여러 모듈을 동시에 수정해야 하면 step을 쪼갠다.
2. **자기완결성** — 각 step 파일은 독립된 에이전트 세션(Codex 또는 Claude)에서 실행된다. "이전 대화에서 논의한 바와 같이" 같은 외부 참조는 금지한다. 필요한 정보는 전부 파일 안에 적는다.
3. **사전 준비 강제** — 관련 문서 경로와 이전 step에서 생성/수정된 파일 경로를 명시한다. 세션이 코드를 읽고 맥락을 파악한 뒤 작업하도록 유도한다.
4. **시그니처 수준 지시** — 함수/클래스의 인터페이스만 제시하고 내부 구현은 에이전트 재량에 맡긴다. 단, 설계 의도에서 벗어나면 안 되는 핵심 규칙(멱등성, 보안, 데이터 무결성 등)은 반드시 명시한다.
5. **AC는 실행 가능한 커맨드** — "~가 동작해야 한다" 같은 추상적 서술이 아닌 `npm run build && npm test` 같은 실제 실행 가능한 검증 커맨드를 포함한다.
6. **주의사항은 구체적으로** — "조심해라" 대신 "X를 하지 마라. 이유: Y" 형식으로 적는다.
7. **네이밍** — step name은 kebab-case slug로, 해당 step의 핵심 모듈/작업을 한두 단어로 표현한다 (예: `project-setup`, `api-layer`, `auth-flow`).

#### C-2. TDD 계획 — 한 모듈 안에서 끝나는 작업

초안: 실패시킬 테스트 목록 · 고칠 파일 · 완료 확인 명령.

승인되면 **같은 세션에서 끝까지 간다**: `fix/`·`feat/` 브랜치 → 실패하는 `test:` 커밋 → 통과시키는 `fix:`/`feat:` 커밋(CLAUDE.md 「수동 작업」 커밋 단위) → 바꾼 범위 테스트 → push·PR → CI 확인. phase 파일·ship.py를 쓰지 않으므로 독립 리뷰가 자동으로 붙지 않는다 — **리뷰는 새 세션 `/code-review`**로 하고, 머지는 승인 뒤에 한다.

진행 중 다른 레이어·모듈로 번지면 그 자리에서 멈추고 C-1로 전환을 제안한다.

### D. 파일 생성

D 이후(파일 생성·실행·ship.py)는 C-1 경로다.

사용자가 승인하면 아래 파일들을 생성한다.

#### D-1. `phases/index.json` (전체 현황)

여러 task를 관리하는 top-level 인덱스. 이미 존재하면 `phases` 배열에 새 항목을 추가한다.

```json
{
  "phases": [
    {
      "dir": "0-mvp",
      "status": "pending"
    }
  ]
}
```

- `dir`: task 디렉토리명.
- `status`: `"pending"` | `"completed"` | `"error"` | `"blocked"`. execute.py가 실행 중 자동으로 업데이트한다.
- 타임스탬프(`completed_at`, `failed_at`, `blocked_at`)는 execute.py가 상태 변경 시 자동 기록한다. 생성 시 넣지 않는다.

#### D-2. `phases/{task-name}/index.json` (task 상세)

```json
{
  "project": "<프로젝트명>",
  "phase": "<task-name>",
  "steps": [
    { "step": 0, "name": "project-setup", "type": "chore", "scope": "db",
      "desc": "로컬 컨테이너와 마이그레이션 러너", "status": "pending" },
    { "step": 1, "name": "core-types", "type": "feat", "scope": "db",
      "desc": "문서 변경 트리거와 임베딩 잡 생성", "status": "pending" },
    { "step": 2, "name": "api-layer", "type": "feat", "scope": "api",
      "desc": "문서 CRUD 라우터", "status": "pending" }
  ]
}
```

필드 규칙:

- `project`: 프로젝트명 (CLAUDE.md 참조).
- `phase`: task 이름. 디렉토리명과 일치시킨다.
- `steps[].step`: 0부터 시작하는 순번.
- `steps[].name`: kebab-case slug. 파일명(`step{N}.md`)과 대응한다.
- `steps[].status`: 초기값은 모두 `"pending"`.
- `issue`: 이 phase가 닫는 GitHub 이슈 번호. `ship.py`가 PR 본문의 `Closes #N`과 리뷰 스펙으로 쓴다 (ship.py로 돌릴 때 필수).
- `title`: PR 제목. 예: `feat: 휴지통 — 소프트 삭제·복원 (#198)` (ship.py로 돌릴 때 필수).
- `needs_vm`: OpenSQL VM 실측이 필요한 phase면 `true`. ship.py가 머지 대기에서 G3로 멈춘다 (기본 `false`).

**커밋 메시지를 만드는 세 필드** — execute.py가 `<type>(<scope>): <desc>`로 조립한다.

| 필드 | 값 | 없으면 |
|---|---|---|
| `type` | `feat` `fix` `docs` `test` `refactor` `chore` `ci` `perf` | `feat` |
| `scope` | `db` `worker` `api` `search` `mcp` `frontend` `adr` | 스코프 생략 |
| `desc` | 한국어 한 줄. 무엇을 만들었는지 | `step {N} — {name}` |

- **모든 step이 `feat`은 아니다.** 환경 구성·설정은 `chore`, 구조 정리는 `refactor`로 적는다.
- **phase 이름을 `scope`에 쓰지 마라.** `feat(m1-db-layer)`는 CLAUDE.md 허용 목록에 없다.
- step 하나가 여러 스코프에 걸치면 **step을 쪼개라** (설계 원칙 1).

상태 전이와 자동 기록 필드:

| 전이 | 기록되는 필드 | 기록 주체 |
|------|-------------|----------|
| → `completed` | `completed_at`, `summary` | 에이전트 세션 (summary), execute.py (timestamp) |
| → `error` | `failed_at`, `error_message` | 에이전트 세션 (message), execute.py (timestamp) |
| → `blocked` | `blocked_at`, `blocked_reason` | 에이전트 세션 (reason), execute.py (timestamp) |

`summary`는 step 완료 시 산출물을 한 줄로 요약한 것으로, execute.py가 다음 step 프롬프트에 컨텍스트로 누적 전달한다. 따라서 다음 step에 유용한 정보(생성된 파일, 핵심 결정 등)를 담아야 한다.

`created_at`은 execute.py가 최초 실행 시 task 레벨에 한 번만 기록한다. step 레벨의 `started_at`도 execute.py가 각 step 시작 시 자동 기록한다. 생성 시 넣지 않는다.

#### D-3. `phases/{task-name}/step{N}.md` (각 step마다 1개)

```markdown
# Step {N}: {이름}

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/docs/ARCHITECTURE.md`
- `/docs/ADR.md`
- {이전 step에서 생성/수정된 파일 경로}

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 작업

{구체적인 구현 지시. 파일 경로, 클래스/함수 시그니처, 로직 설명을 포함.
코드 스니펫은 인터페이스/시그니처 수준만 제시하고, 구현체는 에이전트에게 맡겨라.
단, 설계 의도에서 벗어나면 안 되는 핵심 규칙은 명확히 박아넣어라.}

## Acceptance Criteria

```bash
npm run build   # 컴파일 에러 없음
npm test        # 테스트 통과
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - ARCHITECTURE.md 디렉토리 구조를 따르는가?
   - ADR 기술 스택을 벗어나지 않았는가?
   - CLAUDE.md CRITICAL 규칙을 위반하지 않았는가?
3. 결과에 따라 `phases/{task-name}/index.json`의 해당 step을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 (API 키, 외부 인증, 수동 설정 등) → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- {이 step에서 하지 말아야 할 것. "X를 하지 마라. 이유: Y" 형식}
- 기존 테스트를 깨뜨리지 마라
```

### E. 실행

```bash
python3 scripts/execute.py {task-name}                 # 순차 실행 (Codex 우선, 한도 소진 시 Claude 폴백)
python3 scripts/execute.py {task-name} --push          # 실행 후 push
python3 scripts/execute.py {task-name} --agent claude  # 처음부터 Claude만
```

> 기본은 Codex 우선이고, 한도 소진 신호(실패 응답의 `rate limit`/`usage limit`/`429` 문구
> **또는 응답 없는 30분 타임아웃**)가 오면 같은 step을 Claude로 다시 돌리고 남은 step도
> Claude로 간다(sticky). 타임아웃을 신호에 넣은 이유: 한도에 걸린 Codex는 에러 대신 응답
> 없이 매달려, 문구 매칭만으로는 폴백이 잡지 못하고 executor가 죽었다(m13 step 2).
> 그래서 한도에 걸린 run은 첫 폴백까지 최대 30분이 비어 있을 수 있다.

execute.py가 자동으로 처리하는 것:

- `feat/{task-name}` 브랜치 생성/checkout (ADR-013 접두사 규칙)
- 가드레일 주입 — CLAUDE.md + docs/*.md 내용을 매 step 프롬프트에 포함
- 컨텍스트 누적 — 완료된 step의 summary를 다음 step 프롬프트에 전달
- 자가 교정 — 실패 시 최대 3회 재시도하며, 이전 에러 메시지를 프롬프트에 피드백
- **커밋** — step당 코드 커밋 1개(`<type>(<scope>): <desc>`) + 산출물 커밋 1개(`chore: ...`), phase 종료 시 완료 커밋 1개
- 타임스탬프 — started_at, completed_at, failed_at, blocked_at 자동 기록

> **step 세션은 직접 커밋하지 않는다.** 커밋 메시지 규칙을 한 곳(execute.py)에서만 관리하기 위해서다. step 세션은 파일 저장과 `index.json` status 갱신까지만 한다.

### F. 머지 대기까지 잇기 — `scripts/ship.py`

설계를 승인받아 커밋했으면(G1) `execute.py` 대신 `ship.py`로 머지 대기까지 한 번에 돌릴 수 있다.
execute.py는 그대로 두고 그 바깥에서 단계를 넘기는 오케스트레이션 레이어다.

**전제**: 설계(`phases/{task}/`)를 `feat/{task}` 브랜치에 커밋하고 그 브랜치에서 실행한다. execute.py와
달리 ship.py는 브랜치를 만들지 않는다 — 다른 브랜치면 멈춘다. `backend/.venv`가 있는 checkout에서 돌린다.

```bash
python3 scripts/ship.py {task-name}                # execute → verify → pr → review ⇄ fix → ci → ready
python3 scripts/ship.py {task-name} --from review  # 그 단계부터 다시 (리뷰·수정 횟수 초기화)
python3 scripts/ship.py {task-name} --merge        # G4: squash 머지 + 이슈 닫힘 확인
python3 scripts/ship.py {task-name} --merge --vm-verified  # needs_vm phase: G3 실측을 마쳤을 때만
```

| 단계 | 하는 일 |
|---|---|
| execute | pending step이 있으면 `execute.py`를 부른다. phase 디렉토리에 커밋 안 된 변경이 있으면 G1로 멈춘다 |
| verify | PR 전 검증 — 전 step completed, working tree 깨끗함, 구현 변경에 테스트 변경 동반, 바뀐 테스트 파일 재실행 |
| pr | push 후 PR 생성(이미 있으면 재사용). 본문 첫 줄 `Closes #N` |
| review | **새 `claude -p` 프로세스**가 `phases/{task}/review-N.json`을 쓴다. 지적은 `fix`·`decision`·`follow-up`. 결과는 커밋하고 PR 코멘트로 남긴다. 리뷰 결과 커밋을 `reviewed_head`로 기록하고, 다음 리뷰에는 `git diff <reviewed_head>..HEAD`를 「직전 리뷰 이후 변경」으로 따로 준다(전체 diff도 함께) |
| fix | `fix` 지적만 같은 PR 브랜치에 수정 커밋으로 추가한다. 최대 2회, 리뷰 최대 3회 — 두 번째 수정 뒤에도 독립 리뷰로 확인한다 |
| ci | `gh pr checks --watch`. 실패하면 로그를 넘겨 1회 자동 수정하고 **review로 돌아가 재리뷰**받는다(리뷰 한도면 멈춘다) |
| ready | 요약·이슈 후보·알림을 내고 멈춘다 |

멈추는 곳:

- **G1** 설계 미커밋 · **G2** `decision` 지적 · **G3** `needs_vm` phase(`--vm-verified` 없이는 머지 거부) · **G4** 머지(`--merge`)
- **리뷰받은 커밋만 머지한다** — `--merge`는 PR head가 `reviewed_head`가 아니면 거부하고(「리뷰 뒤 커밋 N개」), `gh pr merge --match-head-commit <reviewed_head>`로 머지한다. 머지 대기(ready) 뒤에 세션·사람이 직접 커밋했으면 `--from review`로 다시 리뷰받는다. 실무의 「새 커밋이 오면 승인 무효」를 1인 저장소에서는 GitHub 브랜치 보호로 걸 수 없어(본인 PR 승인 불가) ship.py가 강제한다
- 자동 수정 한도 초과, CI 재실패, CI 대기 시간 초과(자동 수정하지 않음), PR 전 테스트 실패, 리뷰어가 결과 파일 밖을 바꿈
- **변조 검사** — 모든 자동 수정(리뷰 fix·CI fix)은 테스트 파일 삭제, 테스트 함수 삭제(`def test_…`·`it(`·`test(` 이름이 다시 추가되지 않음), `skip`·`xfail` 추가가 있으면 커밋하지 않고 working tree에 남긴 채 멈춘다. 단언 약화는 잡지 못한다 — 사람이 diff로 본다
- 에이전트 세션은 권한 확인 없이 돌지만 `gh issue create`·`gh api`·`gh pr merge`·`git commit`·`git push`는 `--disallowedTools`로 막는다 — 커밋·push는 변조 검사를 거쳐 ship.py만 한다

`--from review`는 리뷰·수정 횟수를 0으로 되돌린다. 의도된 동작이다 — G2·한도로 멈춘 뒤 사람이 반영·커밋했으면 그 판을 새로 리뷰한다. 한도는 무인 구간의 상한이지 PR 전체의 상한이 아니다.

`follow-up` 지적은 **이슈를 만들지 않는다.** review JSON과 ready 요약에 이슈 후보(title·reason·severity)로만 남고, 등록은 사람이 판단한다.

상태는 `.git/ship/{task}.json`(작업 트리 밖)에 stage·issue·branch·pr·리뷰/수정 횟수·last_commit·reviewed_head·review artifact를 남긴다. 멈춘 뒤 다시 실행하면 그 단계부터 재개한다.

### 머지

phase가 끝나면 PR을 열고 **squash merge**한다 (ADR-013). `ship.py`를 쓰면 PR 생성부터 머지 대기까지가 자동이고, 머지는 `--merge`로 사람이 한다.

- step별 커밋·산출물·검증 이력은 **PR에 보존**된다
- `main`에는 **검토가 끝난 phase 단위 결과**만 남는다

PR에는 self-review 체크리스트(ADR 준수 / AC 통과 / CLAUDE.md CRITICAL 위반 없음)를 코멘트로 남긴다.

에러 복구:

- **error 발생 시**: `phases/{task-name}/index.json`에서 해당 step의 `status`를 `"pending"`으로 바꾸고 `error_message`를 삭제한 뒤 재실행한다.
- **blocked 발생 시**: `blocked_reason`에 적힌 사유를 해결한 뒤, `status`를 `"pending"`으로 바꾸고 `blocked_reason`을 삭제한 뒤 재실행한다.
