# Step 0: research-opencrypto

## 배경 — 기각도 기록이 있어야 근거가 된다

출제자가 「기술 소개」에 **직접 올린** 기능(ARIA·SEED 암호화 모듈)이 배포판에 실재한다.
[#42](https://github.com/jeongeundev/OpenArchive/issues/42)가 실측으로 확인하고 **기각**했으나,
그 근거가 저장소 어디에도 없다. m6는 8 step 실행이 끝나 PR #40에 있어 얹을 자리가 없으므로
이 phase의 첫 step이 그 자리다.

> **결정은 이미 닫혔다. 이 step은 기록만 한다.** 채택 여부를 다시 열지 마라.

**놓친 이유가 이 step의 핵심이다.** `opencrypto 1.0.0`은 §1 번들 목록에 **처음부터 있었다**
(`docs/OPENSQL_RESEARCH.md:289`). 그런데 §0의 실측표는 *"METADATA와 **이름이 다른** 것"*만 담는
표라서, **이름은 같고 내용이 이름에 안 드러나는** 이 확장을 걸러냈다. 표에 그 축을 세우지 않으면
`utl_file`·`dbms_scheduler`처럼 아직 안 열어본 것에서 같은 누락이 반복된다.

## 읽어야 할 파일

- `docs/OPENSQL_RESEARCH.md` **192행 부근** — `### 번들 확장 실측` 절의 표가 수정 대상이다.
  ⚠️ #42 티켓은 이 표를 "§1 실측표"라 부르지만 **실제 위치는 §0 안**이다. `### 번들 익스텐션
  목록`(286행 부근, §1)은 배포판 원문 목록이라 **건드리지 않는다**
- `docs/OPENSQL_RESEARCH.md` **289행** — `opencrypto 1.0.0`이 이미 적혀 있는 줄. 이것이
  "몰랐던 게 아니라 안 열어봤다"의 증거다
- `backend/migrations/002_tables.sql` **12행** — `gen_random_uuid()`를 쓰는 자리.
  아래 제약 항목의 근거다

## 작업

### 1) 실측표에 축을 하나 세운다

지금 표는 「METADATA (§0) → 실제 `pg_available_extensions`」 두 칸이라 **이름이 어긋난 것만**
잡힌다. `opencrypto` 행을 추가하되, 이 행이 잡는 것은 이름이 아니라 **내용**임을 비고에 쓴다.

- METADATA 칸: `opencrypto 1.0.0`
- 실제 칸: `opencrypto` 1.0.0 (**이름·버전 모두 일치**)
- 비고: 이름이 같아 표에서 걸러졌으나 **내용이 이름에 드러나지 않는 경우**다.
  ARIA·SEED 국산 블록암호가 들어 있고, 이는 **출제자가 「기술 소개」에 직접 올린 항목**이다

표 아래에 한 문장을 덧붙인다 — **이름이 같은 확장도 열어봐야 한다**는 것, 그리고
`utl_file`·`dbms_scheduler`처럼 이름만으로 내용을 알 수 없는 것이 아직 남아 있다는 것.

### 2) `opencrypto` 절을 신설한다

`### 번들 확장 실측` 절 **다음**에 `### opencrypto — ARIA·SEED 실측 [실측 2026-08-10]`을 만든다.
아래 여섯 가지를 전부 담는다.

**(a) 실재 확인**

`CREATE EXTENSION opencrypto` 한 줄로 설치된다 — **preload 불필요**. 함수 33개가 pgcrypto와
시그니처까지 같다.

**(b) ARIA는 진짜다**

RFC 5794(KS X 1213) 공식 시험벡터가 **바이트 단위로 일치**한다. 세 키 길이 모두 적는다.

| 키 길이 | 기대 암호문 |
|---|---|
| 128 | `d718fbd6ab644c739da95f3be6451778` |
| 192 | `26449c1805dbe7aa25a468ce263a9e79` |
| 256 | `f92bd7c79fb72e2f2b8f80c1972d24fc` |

> 192·256 값은 **RFC 5794 원문에서 확인해 적는다.** 128 값만 확실히 기록돼 있으므로,
> 나머지 둘을 저장소에 없는 상태에서 지어내지 마라 — 확인이 안 되면 128만 적고
> "192·256도 일치했다"를 문장으로 쓴다.

**(c) SEED는 깨져 있다 — 에러 두 종의 차이가 핵심이다**

`encrypt()`/`encrypt_iv()` 경로가 모든 표기·키 길이에서 실패한다.

- 실제 에러: `Cipher cannot be initialized`
- 이것은 `No such cipher algorithm`과 **다른 에러**다 — 이름은 등록됐고 **초기화가 죽는다**

**배제한 가설 셋을 반드시 함께 적는다.** 이것이 "안 해봤다"와 "해보고 원인을 좁혔다"를 가른다.

1. OpenSSL 3.5.1 **default** provider에 `SEED-CBC`/`SEED-ECB`가 **있다**
2. `fips_enabled = 0`
3. `postgres`와 `opencrypto.so`가 **같은 `libcrypto.so.3`**를 쓴다

**(d) pgp 경로는 되지만 표준이 아니다**

`pgp_sym_encrypt`는 동작하며 **폴백이 아니다** — 미지원 값은 걸러지고 알고리즘 ID가
`aria = 0x0b` · `seed = 0x0e`로 갈린다. 다만 이 ID는 **RFC 4880 비표준**이라 표준 PGP 도구로
복호화되지 않는다.

**(e) 채택했다면 걸렸을 제약 넷**

- **pgcrypto와 같은 스키마에 공존 불가** — `digest` 충돌. 별도 스키마면 가능
- **`public.gen_random_uuid()`가 코어 것과 중복 정의된다** — `002_tables.sql:12`가 쓰는 함수다
- **로컬 컨테이너에 없고 PGDG에도 없다** — #28의 Dockerfile 해법이 통하지 않는 **첫 확장**
- **국산 해시(HAS-160·LSH)는 없다** — 블록암호만이다

**(f) 기각 근거 셋**

1. **본문 암호화는 벡터 검색만이 아니라 m9의 `pg_trgm` RRF와 스니펫까지 동시에 깨뜨린다** —
   #29가 `tsvector`를 버리고 고른 유일한 대안이 죽는다
2. **TDE가 아니라 컬럼 암호화이고 키가 SQL 인자다** — DB가 키를 관리하지 않으므로
   *"DB 계층에서 암호화"* 그림이 성립하지 않는다
3. **남는 자리가 전부 이미 기각된 논리에 걸린다** — `password_hash`는 해시지 암호화가 아니고,
   메타데이터는 화면에 안 보이며(#29의 `pg_cron` 논리), "민감 문서" 등급 신설은 요구에 없다
   (#37의 워크스페이스 논리)

여기에 지도의 기준으로 재본 결과를 한 줄 더한다 — **ARIA 컬럼 암호화는 외부 벡터 DB 구성에서도
RDBMS 쪽에서 똑같이 되고**, 오히려 우리 구조에서는 암호문과 평문 벡터가 한 테이블에 나란히
놓여 불리하다.

## Acceptance Criteria

```bash
# 1) 실측표에 opencrypto 행이 생겼는지 — 표 안에 있어야 한다
sed -n '/### 번들 확장 실측/,/^### /p' docs/OPENSQL_RESEARCH.md | grep -n "opencrypto"

# 2) 절이 신설됐는지
grep -n "### opencrypto" docs/OPENSQL_RESEARCH.md

# 3) 저장소에 없던 값들이 실제로 들어왔는지 — 확장 이름만으로는 통과할 수 없는 검사다
grep -c "d718fbd6ab644c739da95f3be6451778" docs/OPENSQL_RESEARCH.md   # 1 이상
grep -c "Cipher cannot be initialized" docs/OPENSQL_RESEARCH.md        # 1 이상
grep -c "No such cipher algorithm" docs/OPENSQL_RESEARCH.md            # 1 이상 (차이를 적었는가)
grep -c "0x0e" docs/OPENSQL_RESEARCH.md                                # 1 이상
grep -c "libcrypto.so.3" docs/OPENSQL_RESEARCH.md                      # 1 이상

# 4) 기각 근거가 함께 적혔는지 — m9와의 충돌이 근거 1번이다
sed -n '/### opencrypto/,/^### /p' docs/OPENSQL_RESEARCH.md | grep -n "pg_trgm"
sed -n '/### opencrypto/,/^### /p' docs/OPENSQL_RESEARCH.md | grep -nE "gen_random_uuid"

# 5) §1 배포판 목록은 건드리지 않았는지 — 출력이 없어야 한다
git diff -U0 docs/OPENSQL_RESEARCH.md | grep -E "^[+-].*번들 익스텐션 목록"

# 6) 이 step이 고치는 파일은 하나뿐이다 — 출력이 없어야 한다
git diff --name-only | grep -vE "^(docs/OPENSQL_RESEARCH\.md|phases/)"

# 7) 전체 검증
bash scripts/check.sh
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - **표의 새 행이 "이름이 다르다"가 아니라 "내용이 이름에 안 드러난다"를 말하는가?**
     이름 축으로 적으면 표의 원래 축과 섞여 다음 누락을 못 막는다
   - **SEED 항목이 두 에러의 차이를 적었는가?** `Cipher cannot be initialized`만 적고
     `No such cipher algorithm`과의 차이를 빼면, "등록은 됐고 초기화가 죽는다"는 진단이 사라진다
   - **192·256 시험벡터를 확인 없이 지어내지 않았는가?** 확인 불가면 128만 적는다
   - 기각 근거가 **셋 다** 들어갔는가? 특히 1번(m9 RRF와의 충돌)이 빠지면 기각이
     취향처럼 읽힌다
3. 결과에 따라 `phases/m7-graph-relations/index.json`의 step 0을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- **`opencrypto`를 채택하는 방향의 서술을 쓰지 마라.** 이유: #42가 근거 셋으로 기각했고
  이 step은 기록만 한다. "나중에 쓸 수 있다"는 여지도 붙이지 마라
- **`CREATE EXTENSION opencrypto`를 마이그레이션에 넣지 마라.** 이유: 위와 같다.
  로컬 컨테이너에 아예 없어 `pytest`가 통째로 죽는다
- **§1 `### 번들 익스텐션 목록`을 수정하지 마라.** 이유: 배포판 METADATA 원문 기록이다.
  실측은 §0에 적는다
- **`docs/ADR.md`를 건드리지 마라.** 이유: 이건 조사 기록이지 설계 결정이 아니다.
  ADR 번호를 쓰면 029(관계 그래프)와 충돌한다
- **확인하지 못한 값을 채우지 마라.** 이유: 이 프로젝트는 추론으로 세 번 틀렸다.
  모르면 적지 않는 편이 낫다
