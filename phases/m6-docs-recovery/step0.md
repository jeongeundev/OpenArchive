# Step 0: research-extensions

## 배경 — 왜 이 step이 m6의 첫 순서인가

**저장소의 조사 문서가 틀린 채로 남아 있다.** m6의 나머지 step은 전부 이 문서를 근거로 ADR과
설계 문서를 고친다. 틀린 것을 인용하면 틀린 것이 번진다. 그래서 근거 문서가 먼저다.

이슈 #25가 실 VM에서 직접 확인한 것 네 가지가 저장소 어디에도 없다.

1. **`shared_preload_libraries`에 이미 12개가 올라가 있다.** 저장소에는 `opensql_license`
   하나만 적혀 있다. 이걸 보고 "다른 확장을 쓰려면 preload를 고치고 재시작해야 한다"고 썼다면
   **세 번째 추론 오류**가 될 자리였다
2. **`METADATA`의 확장 이름을 SQL에 그대로 쓰면 실패한다.** `pgvectorscale`이 아니라
   **`vectorscale`**이고, `o2`라는 확장은 **없다**
3. **번들은 13종이 아니라 30종 이상이다**
4. **`postgis`는 이 VM에서 설치 자체가 깨져 있다** — `libSFCGAL.so.2` 누락

이 step은 **`docs/OPENSQL_RESEARCH.md` 한 파일만 고친다. ADR과 코드는 건드리지 않는다.**

## 읽어야 할 파일

- `docs/OPENSQL_RESEARCH.md` — **§0**(19~124행 부근), **§1 번들 익스텐션 목록**(142~163행 부근).
  이 둘이 수정 대상이다
- `docs/OPENSQL_RESEARCH.md` **§8**(664행 부근) — pgvectorscale 서술이 있는지 확인하고,
  있으면 실명 정정이 필요한지 판단하라
- `backend/migrations/001_extensions.sql` — 지금 실제로 만드는 확장이 무엇인지. 문서가 코드와
  어긋나면 안 된다

## 작업

### 1) §0 "실 VM 실측 결과" 아래에 새 소절을 만든다

제목: **`### 번들 확장 실측 — 이름과 개수가 METADATA와 다르다 [실측 2026-08-09]`**

넣을 것:

**(a) `shared_preload_libraries` 전체 목록**

```
opensql=> SHOW shared_preload_libraries;
opensql_license, o2scheduler, pg_stat_statements, dbms_rls, pg_hint_plan,
pgaudit, pg_cron, dbms_alert, dbms_pipe, dbms_assert, dbms_output, credcheck
```

**`pg_cron`·`pgaudit`·`pg_hint_plan`·`pg_stat_statements`·`credcheck`는 이미 올라가 있다.**
이 5종은 preload 변경도 재시작도 필요 없다. 배포판이 미리 올려둔 것은 제품이 쓰라고 준 것이라는
신호로 읽어야 한다.

**(b) METADATA와 실제 이름 대조표**

| METADATA (§0) | 실제 `pg_available_extensions` | 비고 |
|---|---|---|
| `pgvectorscale 0.9.0` | **`vectorscale` 0.9.0** | 이름이 다르다. `CREATE EXTENSION pgvectorscale`은 실패한다 |
| `o2 1.4` | `o2functions` 1.2 · `o2scheduler` 1.0 · `o2types` 1.1 · `o2views` 1.1 | **`o2`라는 확장은 없다.** 4개로 쪼개져 있다 |
| `system_stats 3.2` | `system_stats` **3.0** | 패키지 버전 ≠ 확장 SQL 버전 |
| `tibero_fdw 0.6.4` | `tibero_fdw` **1.0** | 위와 동일 |
| `postgis 3.5.4` | `postgis` + `postgis_raster` `postgis_sfcgal` `postgis_tiger_geocoder` `postgis_topology` `address_standardizer(_data_us)` | 6종으로 전개 |
| (목록에 없음) | `dbms_alert` `dbms_assert` `dbms_job` `dbms_output` `dbms_pipe` `dbms_random` `dbms_rls` `dbms_scheduler` `dbms_sql` `utl_file` | METADATA에 없는 10종이 더 있다 |
| (목록에 없음) | `opensql_license` 1.0 | 라이선스 검증 확장 |

**즉 번들은 13종이 아니라 30종 이상이며, `o2`는 단일 확장이 아니라 Oracle 호환 스위트다.**
근거 URL: <https://docs.tibero.com/tmaxopensql.en/tmax-o2-extensions/installation/o2-extension-installation>

**(c) `postgis` 파손**

```
ERROR: "/home/opensql/lib/postgis-3.so" 라이브러리를 불러 올 수 없음:
       libSFCGAL.so.2: 그런 파일이나 디렉터리가 없습니다
```

우연이 아니다 — `SETUP_OPENSQL.md`가 설치 중 SFCGAL 요구사항을 우회하는 절차를 담고 있고
그 우회의 결과다. **`postgis`는 이 프로젝트에 접점이 없을 뿐 아니라 쓰려면 재설치부터 해야 한다.**

**(d) `dbms_scheduler`는 `CASCADE`가 필요하다** — `o2scheduler` 확장 모듈에 의존한다.

### 2) §0 "조사 결과가 틀렸던 항목" 표의 확장 목록 행을 고친다

현재 행:

| 확장 목록 | 12종 | **13종** — `pg_repack 1.5.2` 추가 | 영향 없음 |

**"13종"이 아니라 "30종 이상"이며, "영향 없음"도 아니다** — 이름을 그대로 SQL에 쓰면 실패한다.
새 소절(작업 1)을 가리키도록 고친다.

### 3) §0 라이선스 절의 `opensql_license` 불릿에 참조를 단다

현재 이렇게 적혀 있다:

> `patroni.yml`의 `shared_preload_libraries`에 **`opensql_license`가 포함**되어, 라이선스가 맞지 않으면 PostgreSQL이 기동하지 않는다

**이 문장은 맞다. 지우지 마라.** 다만 **`opensql_license`는 12개 중 하나일 뿐**이라는 사실과
전체 목록이 있는 위치를 한 문장으로 덧붙인다.

### 4) §1 번들 익스텐션 목록에 경고를 단다

지금 표는 METADATA를 그대로 옮긴 것이라 **SQL에 쓸 수 없는 이름**을 담고 있다. 표는 그대로 두되
(METADATA 기록이라는 사실 자체가 근거다) 표 바로 아래에 경고를 붙인다:

> ⚠️ **이 표는 `METADATA` 원문이며 `CREATE EXTENSION`에 쓸 이름이 아니다.** 실제 확장 이름과
> 개수는 §0의 「번들 확장 실측」을 보라 — `pgvectorscale`이 아니라 `vectorscale`이고, `o2`는 없다.

그리고 "설계 영향 (중요)" 불릿 중 **`pgvectorscale 0.9.0`을 언급하는 줄의 이름을 정정**한다.

### 5) 로컬 컨테이너와의 버전 차를 §11에 기록한다

`docs/OPENSQL_RESEARCH.md` **§11 로컬 개발 환경**에 실측 한 줄을 더한다 **[실측 2026-08-10]**:

- 로컬 `pgvector/pgvector:pg17`: `vector` **0.8.6** · `pg_trgm` **1.6 사용 가능** ·
  `pg_cron`·`vectorscale` **없음**
- 실 VM: `vector` **0.8.1**
- **`pg_trgm`은 contrib이라 로컬 컨테이너에 이미 있다** — 별도 이미지가 필요 없다

이 사실은 step 4(ADR-026)가 근거로 인용한다.

## Acceptance Criteria

```bash
# 1) preload 전체 목록이 들어갔는지 — 12개 중 저장소에 없던 이름으로 확인한다
grep -n "o2scheduler" docs/OPENSQL_RESEARCH.md
grep -n "credcheck" docs/OPENSQL_RESEARCH.md
grep -n "dbms_pipe" docs/OPENSQL_RESEARCH.md

# 2) 확장 실명 정정이 들어갔는지
grep -n "o2functions" docs/OPENSQL_RESEARCH.md
grep -nE "30종" docs/OPENSQL_RESEARCH.md

# 3) postgis 파손이 기록됐는지
grep -n "libSFCGAL" docs/OPENSQL_RESEARCH.md

# 4) 로컬/VM 버전 차가 §11에 기록됐는지
grep -n "0.8.6" docs/OPENSQL_RESEARCH.md

# 5) 이 step은 조사 문서 한 개만 고친다 — 아래는 아무 것도 출력되지 않아야 한다
git diff --name-only | grep -vE "^(docs/OPENSQL_RESEARCH\.md|phases/)"

# 6) 전체 검증 (문서만 바뀌었으므로 통과해야 한다)
bash scripts/check.sh
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - `vectorscale`·`o2functions` 등 실명이 **표 안에만이 아니라 본문 서술에도** 반영됐는가?
     이름을 고친 목적은 "SQL에 쓸 이름을 틀리지 않게 하는 것"이다
   - `opensql_license`가 라이선스 기동 실패 근거로 인용되던 문장이 **살아 있는가?**
     (지우면 ADR-021의 근거가 끊긴다)
   - §1의 METADATA 표를 **삭제하지 않고** 경고만 붙였는가?
   - `backend/migrations/001_extensions.sql`이 만드는 확장과 문서 서술이 어긋나지 않는가?
3. 결과에 따라 `phases/m6-docs-recovery/index.json`의 step 0을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- **`docs/ADR.md`를 수정하지 마라.** 이유: ADR 정정은 step 2·3·4의 일이고, 그 step들이 이
  step의 결과를 인용한다. 여기서 미리 고치면 어느 커밋이 무엇의 근거인지 흐려진다
- **코드 파일(`.py`, `.ts`, `.tsx`, `.sql`, `.sh`)을 수정하지 마라.** 이유: 이 step은 조사
  기록의 정정이다. 확장을 실제로 채택하는 결정은 #29가 이미 전부 기각했다
- **`vectorscale`·`pg_cron`을 마이그레이션에 넣지 마라.** 이유: #29가 기능 관점에서 기각했고,
  이 step은 "이름이 무엇인지"를 기록할 뿐 채택을 뜻하지 않는다
- **§0의 "조사 결과가 틀렸던 항목" 표에서 PostgreSQL 17.8 행과 그 아래 교훈 문단을 건드리지 마라.**
  이유: 이 프로젝트가 추론으로 틀린 사례의 원본 기록이며 `CLAUDE.md`가 이것을 인용한다
- **§12의 M0 검증 목록을 건드리지 마라.** 이유: 장애 주입 실측 반영은 step 1의 범위다
