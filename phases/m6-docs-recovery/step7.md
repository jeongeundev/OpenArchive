# Step 7: demo-docs-sync

## 배경 — 데모가 바뀌었는데 문서는 옛 데모를 설명하고 있다

step 6이 `scripts/demo_recovery.sh`를 재작성했다. 지금 문서 세 곳이 **폐기된 도커 경로**를
설명한다.

- `README.md` **45행** — 데모가 "DB 정지와 워커 강제 종료"를 검증한다고 쓴다
- `README.md` **181·187·190행** — 실행법과 `DB_STOP_CMD` 기본값(`docker compose stop db`)
- `docs/ARCHITECTURE.md` **344·350·353행** — 데모 절

그리고 **②etcd ③Patroni 시나리오는 코드가 아니라 문서로만 남기기로 했다**(#30). 그 자리도
여기서 만든다.

마지막으로 **`check.sh`에 셸 검사 단계가 없다**(M5 미대응 항목). 이 phase가 셸 스크립트를
산출물로 냈으므로 여기서 닫는다.

### 판단 — shellcheck는 도입하지 않고 `bash -n`을 쓴다

**shellcheck 기각.** 이유 셋:

1. **미설치다.** `check.sh`는 Stop 훅에서 **매 응답마다** 돈다. 외부 의존을 하나 더 붙이면
   미설치 환경에서 조용히 건너뛰거나 실패하는 단계가 하나 는다 — 지금도 `.venv` 부재로
   같은 문제를 겪고 있다
2. **CI가 없다.** 검사를 강제할 자리가 로컬 훅뿐이라, 설치를 전제로 한 단계는 안 도는 쪽으로 샌다
3. **심사위원은 이 스크립트를 못 돌린다.** 린트 품질이 아니라 **깨지지 않는 것**이 요구다

**`bash -n`(구문 검사)을 쓴다.** bash 내장이라 의존이 0이고, 실제 위험 — *아무도 돌리지 않는
스크립트가 문법으로 깨져 있다* — 을 정확히 잡는다.

## 읽어야 할 파일

- `scripts/demo_recovery.sh` — **step 6이 방금 만든 실물.** 문서는 이것을 설명해야 한다.
  **추측하지 말고 읽어라**
- `README.md` — 45행 부근과 178~192행 부근
- `docs/ARCHITECTURE.md` — 340~355행 부근 데모 절, 그리고 **책임 분리표**(320행 부근)
- `docs/OPENSQL_RESEARCH.md` §0 「Single 장애 주입 실측」 — step 1이 기록한 ②③ 실측값.
  문서로만 남기는 시나리오의 근거가 여기 있다
- `docs/ADR.md` **ADR-020** — step 2가 고정한 표현
- `scripts/check.sh` — 수정 대상

## 작업

### 1) `README.md`의 데모 서술을 고친다

- **45행 부근**: 데모가 검증하는 것을 새 시나리오로 바꾼다 — **DB 프로세스가 죽어도 Patroni가
  스스로 재기동하고, 앱이 재연결해 미처리 잡을 이어 처리하며, 정합성이 0으로 수렴한다.**
  기존의 ADR-015 연결(*"증명할 수 있는 주장을 하는 것이 과장된 최신성 표현보다 낫다"*)은 유지한다
- **실행법**: 새 환경변수(`OPENSQL_HOST` 등)와 **실 OpenSQL VM이 필요하다**는 전제를 적는다.
  `DB_STOP_CMD`·`DB_START_CMD` 설명을 삭제한다
- **한계 한 문장**: ADR-020의 표현을 그대로 쓴다 — 노드 사망은 복구되지 않으며 사무국 지시에
  따른 Single 구성의 제약이다

### 2) `docs/ARCHITECTURE.md`의 데모 절을 다시 쓴다

- 시나리오 ①(코드)과 ②③(문서만)을 **구분해서** 적는다
- **②etcd 정지**: `failsafe_mode=true`가 primary 강등을 막아 **99초 동안 앱이 아무것도
  눈치채지 못했다**. DCS 장애 ≠ 서비스 장애
- **③Patroni 정지**: PostgreSQL은 멀쩡히 쓰기를 받는데 **리더 키가 23.9초에 소멸**하고
  **아무것도 Patroni를 되살리지 않았다**(106초 관측). systemd 유닛이 `opensql-etcd.service`
  하나뿐이라는 사실과 이어진다
- **왜 코드로 만들지 않았는지**를 한 줄로 적는다 — 시연 시간과 서사 밀도. 실측은 이미 있고
  근거는 `OPENSQL_RESEARCH.md` §0이다
- **책임 분리표**를 다시 읽고, "DB 프로세스 재기동"이 **OpenSQL(Patroni)의 일**로 적혀 있는지
  확인하라. 없으면 행을 더한다

### 3) `scripts/check.sh`에 셸 구문 검사를 추가한다

backend·frontend 블록과 같은 형식으로, **같은 `FAILED` 합산 규칙**을 따른다.

```bash
echo "== scripts: 셸 구문 검사 =="
for f in "$ROOT"/scripts/*.sh "$ROOT"/scripts/hooks/*.sh; do
  [ -f "$f" ] || continue
  bash -n "$f" || FAILED=1
done
```

- **`set -uo pipefail` 아래에서 도는 것을 전제로 쓴다** — 매치가 없을 때 글롭이 그대로 남는
  경우를 `[ -f "$f" ]`로 막는다
- **`cd`에 의존하지 마라.** 앞 블록이 `cd`를 하므로 **절대 경로(`$ROOT`)로 쓴다**
- **첫 실패에서 중단하지 마라** — 이 파일의 설계 의도다(파일 상단 주석 참조)
- 위치는 **frontend 블록 다음, 최종 합산 앞**

### 4) 옛 데모를 참조하는 곳이 더 없는지 훑는다

`grep -rn "demo_recovery"`로 확인하고, `phases/` 아래(과거 기록)는 **고치지 마라.**
그것은 그때의 기록이다.

## Acceptance Criteria

```bash
# 1) 도커 경로 서술이 사라졌는지 — phases/ 제외하고 출력이 없어야 한다
grep -rn "DB_STOP_CMD\|docker compose stop db" README.md docs/

# 2) 새 전제가 문서에 들어갔는지
grep -n "OPENSQL_HOST" README.md
grep -nE "Patroni" README.md

# 3) ②③ 시나리오가 문서로 남았는지
grep -n "failsafe" docs/ARCHITECTURE.md
grep -n "23.9\|리더 키" docs/ARCHITECTURE.md

# 4) 금지 표현이 없는지 — 출력이 없어야 한다
grep -rnE "무중단|항상 최신|실시간 동기화|failover를 시연" README.md docs/ARCHITECTURE.md

# 5) 셸 구문 검사가 추가됐고 실제로 도는지
grep -n "bash -n" scripts/check.sh
bash scripts/check.sh 2>&1 | grep -n "셸 구문 검사"

# 6) 구문 검사가 진짜로 잡는지 — 일부러 깨뜨려 확인하고 반드시 되돌린다
cp scripts/demo_recovery.sh /tmp/demo_recovery.bak
printf '\nif [ 1 ; then\n' >> scripts/demo_recovery.sh
bash scripts/check.sh; echo "깨진 상태 exit=$?"   # 0이 아니어야 한다
cp /tmp/demo_recovery.bak scripts/demo_recovery.sh
bash scripts/check.sh; echo "복원 후 exit=$?"     # 0이어야 한다

# 7) 과거 기록은 건드리지 않았는지 — 출력이 없어야 한다
git diff --name-only | grep -E "^phases/m[0-5]-"

# 8) 문서와 실물이 맞는지 — README의 환경변수가 스크립트에 실제로 있는지 눈으로 대조
grep -nE "^[A-Z_]+=" scripts/demo_recovery.sh
```

## 검증 절차

1. 위 AC 커맨드를 실행한다. **6번의 복원을 반드시 확인하라** — 깨진 파일을 남기면 다음 step이
   전부 실패한다.
2. 아키텍처 체크리스트를 확인한다:
   - **README에 적은 실행법이 실제 스크립트와 맞는가?** 환경변수 이름·기본값을 하나씩 대조하라
   - ②③ 시나리오의 숫자가 **`OPENSQL_RESEARCH.md` §0과 같은가?**
   - 한계 문구가 **ADR-020과 글자 그대로 같은가?**
   - `check.sh`의 새 블록이 **`cd` 이후에도 올바른 경로를 보는가?**
     (앞 블록이 `cd "$ROOT/frontend"`를 한다)
   - 셸 검사가 **`scripts/hooks/tdd-guard.sh`까지 포함하는가?**
3. 결과에 따라 `phases/m6-docs-recovery/index.json`의 step 7을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- **shellcheck를 설치하거나 `check.sh`에 넣지 마라.** 이유: 위 「판단」에 근거가 있다.
  외부 의존을 매 응답 훅에 넣지 않는다
- **`scripts/demo_recovery.sh`를 고치지 마라.** 이유: step 6의 산출물이고 그 step이 실행으로
  검증했다. 여기서 고치면 검증되지 않은 변경이 된다.
  **단 AC 6번에서 일부러 깨뜨린 것은 반드시 되돌려라**
- **`phases/m0-*` ~ `phases/m5-*`를 수정하지 마라.** 이유: 그때의 기록이다
- **`check.sh`에 `demo_recovery.sh` **실행**을 넣지 마라.** 이유: 매 응답마다 DB를 죽이게 된다
- **etcd·Patroni 시나리오를 스크립트로 만들지 마라.** 이유: #30이 문서로만으로 정했다
- **CI 워크플로를 새로 만들지 마라.** 이유: 이 저장소에 CI가 없는 것은 상태이지 결함이 아니고,
  도입은 이 phase의 범위 밖이다
