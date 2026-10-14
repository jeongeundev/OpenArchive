# OpenArchive

**문서를 고치면 DB가 벡터를 따라 맞추는, AI를 위한 문서관리 플랫폼**

[![CI](https://github.com/jeongeundev/OpenArchive/actions/workflows/ci.yml/badge.svg)](https://github.com/jeongeundev/OpenArchive/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![PostgreSQL](https://img.shields.io/badge/OpenSQL-PostgreSQL%2017-336791.svg)](https://docs.tibero.com/tmaxopensql/overview)
[![Embedding](https://img.shields.io/badge/embedding-BGE--M3%20(MIT)-orange.svg)](https://huggingface.co/BAAI/bge-m3)

---

## 소개

OpenArchive는 **조직의 OpenSQL에 설치하는 AI 문서관리 플랫폼**입니다. 문서를 올리면 **텍스트
추출 → 청킹 → 임베딩 → 벡터 저장 → 문서 관계 생성**이 자동으로 일어나고, 정형 필터(태그·유형·권한)와
벡터 유사도와 문서 관계를 **하나의 SQL**로 결합해 검색합니다. 사람은 웹 화면으로, 프로그램은 REST
API로, AI 에이전트는 MCP로 같은 문서와 같은 권한 규칙 위에서 접근합니다.

[Tmax OpenSQL](https://docs.tibero.com/tmaxopensql/overview)(PostgreSQL 17 + pgvector) 위에서
기업 과제가 제시한 두 문제를 **DB 계층에서** 풉니다.

- **원본과 벡터의 정합성** — 문서를 저장하면 DB 트리거가 **같은 트랜잭션 안에서** 임베딩
  작업·버전 이력·위키링크를 기록합니다. 워커가 벡터를 반영하면 트리거가 관계 작업을 남기고,
  관계 판정은 별도 트랜잭션에서 수행합니다. 문서 저장 경로가 작업 생성을 빠뜨리지 않도록
  DB가 보장하며, 임베딩 실행과 재시도는 워커가 맡습니다.
- **장애 자동 복구** — 장애 감지·재기동·재연결은 OpenSQL(Patroni·OpenProxy)이 맡고,
  애플리케이션은 단일 엔드포인트로만 접속합니다. 미처리 작업은 DB 테이블에 남아 복구 뒤
  그대로 재개됩니다.

보장 범위는 **버전 일관성 + 최신 수렴**입니다 — 검색되는 청크는 어느 시점에도 하나의 텍스트
버전이며, 변경은 유한 시간 안에 최신으로 수렴합니다. 즉시 반영은 약속하지 않습니다
([ADR-015](docs/ADR.md)).

---

## 검증 요약

아래는 이미 문서에 기록한 측정값의 요약입니다. 모두 관측값이며 보장이 아닙니다 — 조건과 한계는 각 출처에 있습니다.

| 항목 | 결과 | 출처 |
|---|---|---|
| HA 장애 전환 (3노드, 비동기 복제, 부하 중 주입, 2026-10-04) | 쓰기 중단: Primary 노드 사망 30.6~40.8초 · postmaster kill 14.1초 · switchover·VIP 이동·Leader 재부팅 10.1~10.3초 · Replica·etcd 장애(과반 상실 포함) 0초. 커밋 응답을 받은 업로드(원본 파일 포함)의 유실·중복은 전 회차 0 | [OpenSQL 환경 구축 §16](docs/SETUP_OPENSQL.md#16-ha-3노드-구성-110) |
| Leader 네트워크 분리 (2026-10-12) | etcd `endpoints` 교정(#258) 전 111초(업로드·검색 각 1건이 재시도 예산을 넘겨 실패) → 교정 뒤 30.7초(실패 0), 두 회차 모두 유실 0 | [OpenSQL 조사 §21](docs/OPENSQL_RESEARCH.md#21-openproxy-etcd-watch가-사라진-노드에-매달린다-실측-2026-10-12) |
| 백업·복원 (Barman, 2026-10-04) | PITR: 복원본이 복원 지점과 같음(지점 뒤에 지운 문서 5건이 되살아나고 지점 뒤 업로드 10건은 없음), 쓰기 가능까지 161초 · 전체 복원: Barman 수신을 끊기 전 응답 성공한 업로드 120건 유실 0, 쓰기 가능까지 166초 | [운영 가이드 「복원 재현」](docs/OPERATIONS.md#복원-재현--scriptsdr_restorepy) |
| 원본-벡터 정합성 | 위 HA 장애 전 회차에서 텍스트 버전과 청크 버전이 어긋난 문서 수가 0으로 수렴 | [OpenSQL 환경 구축 §16](docs/SETUP_OPENSQL.md#16-ha-3노드-구성-110) |
| 검색 품질 (형식별 실문서 10개·질문 24개, #167) | 정답 문서 첫 결과 18/20 · 상위 5 20/20 · MRR 0.950 · 다른 계정의 비공개 문서 누출 0 | [검색 품질 검증 #167](docs/SEARCH_QUALITY_VALIDATION.md#형식별-실문서-기준선-167) |
| 검색 성능 (20,000 청크, 2026-10-03) | 15개 계획 모두 HNSW 사용, 제품 SQL 중앙값 광범위 351ms · 태그 142ms · 유형 110ms. iterative_scan(10/6)·클라이언트 바인딩(10/7) 전 측정 | [성능 검증](docs/PERFORMANCE_VALIDATION.md#20000-청크의-검색) |
| OpenProxy — `BEGIN READ ONLY` | `BEGIN READ ONLY` 트랜잭션 안의 SELECT는 Replica로 간다(10회 중 10회, 2026-09-26). 검색은 plain `BEGIN`으로 Primary에서 읽는다 | [OpenSQL 조사 §6](docs/OPENSQL_RESEARCH.md#6-읽기쓰기-분리와-복제-지연-확정--이-프로젝트에-결정적) |
| OpenProxy — 세션 상태 초기화 | 풀 백엔드를 넘길 때 `RESET ALL`만 하고 `DISCARD ALL`은 하지 않아 임시 테이블·`LISTEN` 등록이 다음 클라이언트로 샌다. 임시 테이블을 쓰지 않는다 | [OpenSQL 조사 §5-2](docs/OPENSQL_RESEARCH.md#5-2-실측--백엔드를-넘길-때-세션-상태를-부분만-초기화한다-실측-2026-08-05) |
| OpenProxy — 서버 명령문 누적 × assert 빌드 | 트랜잭션 안 `select 1`이 4ms → 126ms. 앱은 파라미터를 클라이언트에서 바인딩한다 | [OpenSQL 조사 §18](docs/OPENSQL_RESEARCH.md#18-openproxy-서버-명령문-누적--assert-빌드--쓸수록-모든-문장이-느려진다-실측-2026-10-07) |
| OpenProxy — 콜드 부팅 | 꺼진 클러스터를 켤 때 etcd 공유 설정을 불완전한 판으로 덮어써 VIP 접속이 끊겼다. Patroni가 모일 때까지 기다리게 했다(#231) | [OpenSQL 조사 §20](docs/OPENSQL_RESEARCH.md#20-openproxy-콜드-부팅--공유-설정-덮어쓰기-실측-2026-10-11) |
| OpenProxy — etcd watch | 응답 없이 사라진 etcd 노드에 watch가 매달려 쓰기가 111초 멈췄다. `endpoints`를 자기 노드 하나로 둔다(#258) | [OpenSQL 조사 §21](docs/OPENSQL_RESEARCH.md#21-openproxy-etcd-watch가-사라진-노드에-매달린다-실측-2026-10-12) |

---

## 주요 기능

| 기능 | 설명 |
|---|---|
| 자동 임베딩 파이프라인 | PDF·DOCX·TXT·MD·HWP·HWPX·XLSX·PPTX·PNG·JPG·JPEG 업로드(ZIP 일괄 포함) 시 트리거가 작업을 만들고 워커가 청킹·임베딩·저장. **원본 파일은 DB 안에 판으로 보관되어 내려받고, 교체하고, 다시 추출할 수 있음** |
| 하이브리드 검색 | 태그·유형·권한 필터 + 벡터 유사도 + 저장된 문서 관계(깊이 2)를 **단일 SQL**로 결합. 결과에 "왜 나왔는지"를 함께 표시 |
| 이미지·스캔 PDF 텍스트 인식 | 이미지와 스캔 PDF(텍스트 쪽과 섞인 경우 스캔 쪽만)는 워커가 tesseract로 한국어·영어 텍스트를 인식해 문서 텍스트를 채움. 인식 중에는 「텍스트 인식 중」으로 먼저 보임 ([ADR-052](docs/ADR.md)) |
| 텍스트 버전 관리 | 문서 텍스트(파일 업로드 문서는 추출 텍스트)를 직접 편집하면 이력이 쌓이고 재임베딩이 자동 기동. 처리 중에는 이전 벡터로 검색이 계속됨. 되돌리기는 새 버전을 만듦. 버전마다 작성자를 표시하고 두 텍스트 버전의 바뀐 대목을 비교 |
| 원본 미리보기 | PDF·PNG·JPG(JPEG) 원본은 브라우저에서 바로 열고, HWP·HWPX·DOCX·XLSX·PPTX는 서버의 격리된 변환기(rhwp·LibreOffice, 선택 설치)가 PDF로 바꿔 미리보기. 미리보기는 내려받기와 별도로 감사 로그에 남음 ([ADR-058](docs/ADR.md)) |
| 자동 정리 | 폴더에 든 관련 문서의 절반 이상이 모인 폴더를 추천하고(「정리할 문서」 화면, 옮기는 것은 사람이 함 — [ADR-064](docs/ADR.md)), 관계 그래프의 Louvain 군집으로 문서를 태그 없이 묶어 관계 지도를 그림. 관련 문서·태그 추천, 고아·중복·깨진 위키링크 진단 |
| 권한 | 볼 수 없는 문서는 검색·관련 문서·그래프·집계 어디에도 나타나지 않음. 웹·REST·MCP가 같은 열람 규칙을 공유 |
| 폴더와 열람 범위 상속 | 폴더 트리로 문서를 정리하고, 열람 범위는 폴더에서 상속. 문서마다 「폴더 범위 따름」 또는 「개별 지정」을 고름. 볼 수 없는 폴더는 트리와 문서 수에서도 사라짐 ([ADR-054](docs/ADR.md)) |
| 열람 부여·외부 공유 | 비공개 문서에 사용자·그룹 열람 권한을 부여하고, 소유한 문서와 내가 만든 최상위 폴더의 트리를 외부 공유에 넣어 읽기 전용 토큰으로 제공. 폴더를 넣으면 그 아래의 「폴더 범위 따름」 문서가 소유자와 관계없이(남이 넣은 문서도) 포함됨. 공유 주체는 지정 문서·폴더만 열람 |
| 휴지통 | 삭제한 문서는 휴지통으로 가 어디에도 보이지 않다가, 소유자가 복원하면 재임베딩 없이 바로 다시 검색됨. 기본 30일 뒤 또는 소유자의 영구 삭제로 사라짐 ([ADR-060](docs/ADR.md)) |
| 소유권 이전 | 소유자가 문서·폴더를 다른 사용자에게 이전. 관리자는 사용자 삭제 전에 소유물을 지정 사용자에게 일괄 이전 — 이전해도 관리자가 열람을 얻지는 않음 ([ADR-061](docs/ADR.md)) |
| 감사 로그 | 생성·텍스트 수정·삭제·복원·열람 범위 변경·원본 교체·소유자 변경·공유·그룹·사용자 변경 등을 **DB 트리거가 같은 트랜잭션에서** 기록하고, 원본 내려받기·미리보기는 DB 함수가 기록. 감사 행의 수정·삭제는 DB가 거부. 관리자 화면(`/admin/audit`)에서 조회하고 CSV로 내보냄 ([ADR-055](docs/ADR.md)) |
| 근거 기반 답변 (옵션) | 검색된 발췌만 근거로 로컬 LLM(Ollama)이 답하고, 인용 번호가 그 텍스트 버전의 그 대목으로 이어짐. 웹 검색 화면의 답변 패널 · `openarchive ask` · `POST /api/ask`. **기본 꺼짐**(`ANSWER_PROVIDER=ollama`로 켬)이며 근거를 고르는 검색에는 LLM이 없음 ([ADR-043](docs/ADR.md)) |
| MCP 근거 게이트웨이 | AI 에이전트에 발췌·출처·기준 버전을 공급하고(`search_documents` 등), `create_document`로 문서를 공급받음. 로컬은 stdio, 원격은 Streamable HTTP `/mcp`에 API 토큰 Bearer로 연결 ([ADR-056](docs/ADR.md)). MCP 경로에서는 답변 생성을 클라이언트(에이전트)가 수행 |
| 사용자 CLI | `openarchive login`으로 본인 API 토큰을 저장한 뒤 `doc …`·`search`·`ask`가 REST에 그 토큰으로 붙음. DB 접속 정보(DSN)를 쓰지 않으므로 열람 범위가 웹과 같음 ([ADR-057](docs/ADR.md)) |
| 위임 API 토큰 | 계정 설정에서 프로그램용 토큰을 발급·폐기. scope는 `read`·`read_write`. 만료일은 선택(기본 없음)이며 마지막 사용 시각을 표시 ([ADR-061](docs/ADR.md)) |
| 장애 자동 복구 | 실 OpenSQL 3노드(비동기 복제)에서 Primary 프로세스·노드 사망, switchover, VIP 이동, Leader 재부팅·네트워크 분리, etcd 장애를 부하 중에 주입해 앱 재시작 없이 복구됨을 검증(#165). 관측 유실 0은 보장이 아님 — 아래 설명 참조 |
| 백업·시점 복원 | 별도 백업 노드의 Barman이 WAL을 스트리밍으로 받아 전체 복원과 시점 지정 복원(PITR)을 지원. 복원은 운영 클러스터가 아닌 격리 인스턴스로 함 ([ADR-053](docs/ADR.md), [운영 가이드](docs/OPERATIONS.md#백업과-복원-barman)) |

---

## 시작하기

필요한 것은 Python 3.12+와 OpenSQL입니다. 세 줄이면 설치부터 실행까지 끝납니다. **DSN만 자기 환경으로
바꿉니다** — OpenProxy 경유라면 데이터베이스 자리에 **풀 이름**을 적습니다. `<OpenProxy 호스트>`와
`<풀 이름>`을 실제 값으로 바꾸지 않으면 그 문자열이 그대로 호스트명이 되어 연결에 실패합니다.

```bash
pipx install "openarchive-server[local]"
openarchive init --dsn "postgresql://app:secret@<OpenProxy 호스트>:6432/<풀 이름>"
EMBEDDING_PROVIDER=local openarchive serve
```

`init`은 연결·확장·권한을 점검한 뒤 적용해도 되는지 묻고, 이어서 첫 관리자 `admin`의 비밀번호를
묻습니다. 브라우저에서 http://localhost:8000 을 열고 `admin`과 방금 정한 비밀번호로 로그인합니다.
예제 문서로 먼저 둘러보려면 `openarchive demo --user admin`을 실행합니다(가상 회사의 사내 규정 64건).

![검색 화면 — 예제 문서에서 "연차 휴가는 어떻게 신청하나요"를 찾은 결과](docs/images/search.png)

이미지·스캔 PDF의 텍스트 인식에는 시스템 패키지 **tesseract와 한국어 모델**이 필요합니다 — 없으면 그
문서(텍스트 레이어가 빈 쪽이 있는 PDF 포함)만 「텍스트 인식 실패」로 끝나고 다른 형식은 그대로 동작합니다 (설치 명령은
[운영 가이드](docs/OPERATIONS.md#ocr-엔진-tesseract)).

> ⚠️ 풀 이름과 계정은 OpenProxy 설정(`openproxy.toml`)에서 확인합니다. 설치기는 `opensql`
> 데이터베이스를 만들어 놓고 정작 풀은 관리용 `postgres`를 바라보게 설정하니, DSN을 주기 전에
> 풀이 어느 DB를 가리키는지 확인하세요 — 파일 위치와 교정 절차는
> [OpenSQL 환경 구축 §10](docs/SETUP_OPENSQL.md#10-설치-확인)에 있습니다. 업무 데이터가 이미 있는
> OpenSQL이라면 새 DB·새 풀로 설치합니다 — DBA가 미리 만들 확장(`vector`만, `pg_trgm`은 만들지 않음)과
> 풀 크기(설치기 기본 `pool_size = 10`은 모자람, 권장 20)가 같은 절에 있습니다. 새 DB를 만들 수 없으면
> `init --schema`로 접속 롤 이름의 스키마에 설치합니다 ([운영 가이드](docs/OPERATIONS.md#openarchive-init)).

### 컨테이너로 실행

Python 없이 컨테이너로도 띄울 수 있습니다. 이미지에는 API·워커·웹 화면과 tesseract가 들어 있고
**DB는 들어 있지 않습니다** — 위와 같은 DSN을 줍니다. 이미지는 아직 레지스트리에 게시하지 않아
저장소에서 빌드합니다.

```bash
git clone https://github.com/jeongeundev/OpenArchive && cd OpenArchive
docker build -t openarchive backend/
docker run -d --name openarchive -p 8000:8000 -v openarchive:/data \
  -e DATABASE_URL="postgresql://app:secret@<OpenProxy 호스트>:6432/<풀 이름>" \
  -e ADMIN_PASSWORD='change-me' \
  openarchive
```

기동할 때마다 `init`을 먼저 실행한 뒤 `serve`로 넘어갑니다. 첫 기동에는 스키마를 적용하고
`ADMIN_PASSWORD`로 관리자 `admin`을 만들며, 그 뒤로는 "스키마는 이미 최신입니다." · "관리자 계정이
이미 있습니다."를 출력하고 지나갑니다.
`ADMIN_PASSWORD`는 컨테이너 설정에 남으므로, 첫 로그인 뒤 비밀번호를 바꾸고 이 값 없이 컨테이너를 다시
만들어도 됩니다. 일회성 명령은 명령을 붙여 실행합니다 — 예:
`docker exec openarchive openarchive demo --user admin`.

- **첫 기동은 BGE-M3 가중치를 받느라 1~2분 걸리고, 메모리는 5GB 이상**을 줍니다. 가중치는 `/data`
  볼륨에 남습니다 — 볼륨·자원·진입점 상세는 [운영 가이드 「앱 이미지」](docs/OPERATIONS.md#앱-이미지).
- 같은 호스트의 DB에 붙을 때는 `localhost` 대신 `host.docker.internal`(Linux는
  `--add-host=host.docker.internal:host-gateway`를 함께)을 씁니다.

### 각 단계가 하는 일

- **`pipx install "openarchive-server[local]"`** — `local`은 BGE-M3 임베딩 모델입니다 (torch 포함, 수 GB).
  pipx가 3.12보다 낮은 Python을 기본으로 쓰면 `--python python3.12`를 붙입니다. 동작만 빠르게 확인하려면
  `[local]` 없이 설치하고 `EMBEDDING_PROVIDER=local`을 빼도 됩니다 — 가짜 벡터로 파이프라인 전 구간이
  돌지만 **검색 결과에 의미가 없으므로** 실제 사용에는 `local`이 필요합니다.
- **`openarchive init`** — **연결 확인 → 확장·권한 점검 → 스키마 적용 → 준비 상태 보고**를 한 번에
  합니다. 확인이 적용보다 먼저라 `vector` 확장이 없거나 권한이 모자라면 스키마를 건드리기 전에 무엇이
  왜 필요한지 알려주고 멈추고, 기존 테이블이 있으면 아무것도 바꾸지 않습니다
  ([ADR-039](docs/ADR.md)). `--yes`를 붙이면 묻지 않고 진행합니다 — 건너뛰는 것은 예/아니오 확인뿐이라
  스크립트처럼 입력이 없는 곳에서는 `--dsn`과 `ADMIN_PASSWORD`를 함께 줍니다. 확인한 DSN은
  `~/.openarchive/.env`에 기록됩니다(`OPENARCHIVE_HOME`으로 위치를 바꿉니다). 관리자가 없으면 **첫
  관리자**를 만듭니다 — 자체 가입이 없으므로 계정을 만들어 줄 사람이 있어야 합니다. 이름은
  `--admin-username`으로 바꾸고, 계정은 이후 웹 관리 화면이나 `openarchive create-user`로 더 만듭니다.
- **`openarchive serve`** — API + 임베딩 워커 + 웹 화면을 한 명령으로 띄웁니다. 웹 화면은 백엔드에
  동봉된 정적 빌드라 Node.js가 필요 없습니다. **최초 1회는 Hugging Face에서 BGE-M3 모델(약 2GB)을
  내려받으므로 인터넷이 필요하고 기동이 오래 걸립니다.** 한 번 받은 뒤에는 `HF_HUB_OFFLINE=1`로 인터넷 없이
  구동됩니다 ([운영 가이드](docs/OPERATIONS.md#임베딩-프로바이더)).

### 로컬 개발·평가용 대체 환경

OpenSQL 환경이 없으면 `pgvector/pgvector:pg17` Docker 컨테이너를 DB로 쓸 수 있습니다.
OpenArchive는 표준 PostgreSQL 17 + pgvector 인터페이스에 의존하므로 **동일한 애플리케이션
코드가 그대로 동작합니다.** 단, OpenHA(Patroni)의 장애 복구와 OpenProxy의 커넥션 풀링은
실 OpenSQL 환경에서만 검증할 수 있습니다 ([ADR-006](docs/ADR.md)).

<details>
<summary>Docker로 DB만 띄우는 명령 — 바꿀 줄이 없습니다</summary>

```bash
docker run -d --name openarchive-db -p 5433:5432 \
  -e POSTGRES_USER=openarchive -e POSTGRES_PASSWORD=openarchive -e POSTGRES_DB=openarchive \
  pgvector/pgvector:pg17
until docker exec openarchive-db pg_isready -h 127.0.0.1 -q; do sleep 1; done   # DB가 연결을 받을 때까지
pipx install "openarchive-server[local]"
ADMIN_PASSWORD='change-me' openarchive init --yes --dsn "postgresql://openarchive:openarchive@localhost:5433/openarchive"
EMBEDDING_PROVIDER=local openarchive serve
```

</details>

소스에서 설치해 개발하는 절차는 [CONTRIBUTING](CONTRIBUTING.md#개발-환경)에 있습니다.

---

## 사용 방법

### 문서 올리기

로그인 후 첫 화면(`/`)의 드롭존에 **PDF·DOCX·TXT·MD·HWP·HWPX·XLSX·PPTX·PNG·JPG·JPEG 파일이나 ZIP**을 끌어다 놓습니다
(파일당 50MB). 올린 문서는 목록에 **임베딩 중**으로 나타나고, 워커가 텍스트 추출·청킹·임베딩을
마치면 완료로 바뀝니다 — 보통 문서 하나에 몇 초입니다. 올린 원본 파일은 추출 텍스트와 함께 DB에
보관되어 문서 상세에서 다시 내려받을 수 있습니다. 이미지와 스캔 PDF는 먼저 **텍스트 인식 중**으로
나타나고, 워커가 인식을 마치면 같은 과정을 거칩니다 — 쪽당 수 초가 걸립니다.

프로그램에서 올리려면 `/settings`에서 발급한 API 토큰(`read_write` scope)을 씁니다.

```bash
# 파일 업로드
curl -X POST http://localhost:8000/api/documents \
  -H "Authorization: Bearer $OPENARCHIVE_API_TOKEN" \
  -F "file=@guide.pdf" -F "title=설치 가이드" -F "tags=opensql" -F "visibility=private"

# 텍스트 직접 공급 — 임베딩이 끝날 때까지 폴링하는 독립 예제 (백엔드 패키지 불필요)
python3 examples/ingest_text.py notes.md --base-url http://localhost:8000 \
  --token "$OPENARCHIVE_API_TOKEN" --title "회의록 8/27" --tags 회의
```

`visibility`는 `public`(로그인한 모두) 또는 `private`(소유자와 열람을 부여받은 사용자·그룹)입니다.
문서 상세의 **열람 범위**에서 소유자가 사용자·그룹을 지정할 수 있습니다. 볼 수 없는 문서는 다른
사용자의 검색·관련 문서·그래프·집계 어디에도 나타나지 않습니다.

외부 프로그램에는 `/settings`의 **외부 공유**에서 공유를 만들고, 소유한 문서의 상세 화면에서
그 공유에 문서를 추가한 뒤 읽기 전용 공유 토큰을 발급합니다. 내가 만든 최상위 폴더의 트리라면 폴더
화면에서 폴더째 넣을 수 있고, 그 아래의 「폴더 범위 따름」 문서가 조회 시점에 포함됩니다. 이 토큰은
같은 공유에 지정된 문서·폴더만 볼 수 있으며, 조직 공개 문서라도 지정되지 않으면 보지 못합니다. 문서를
공유에서 빼거나 토큰을 폐기하면 이후 요청의 열람 범위에 반영되고, 공유를 삭제해도 문서 자체는 남습니다.

문서를 한꺼번에 많이 올렸다면 `openarchive rebuild-edges`를 한 번 실행합니다 — 문서 사이의 관계는
각 문서가 임베딩되는 시점까지 들어온 문서만 이웃 후보로 보므로, 적재가 끝난 뒤 전체 기준으로 다시
계산해야 먼저 올린 문서도 나중 문서와 이어집니다. 계산은 워커가 하므로 `openarchive serve`가 돌고 있어야
합니다 ([운영 가이드](docs/OPERATIONS.md#openarchive-rebuild-edges)).

### 검색하기

`/search`에서 **검색어**를 입력하고, 필요하면 **태그**(쉼표 구분)·**문서 유형**·**결과 수**로
좁힙니다. 필터는 벡터 정렬 안쪽에서 적용되므로, 필터에 걸리거나 볼 수 없는 문서가 후보 자리를
차지하지 않습니다.

결과는 문서마다 발췌와 함께 **왜 나왔는지**를 보여줍니다 — 직접 맞은 결과는 `유사도 0.xxx`,
저장된 관계를 타고 온 결과는 *「설치 가이드」에서 「여러 대목에서 만난다」로 이어짐*처럼
출발 문서와 관계 종류가 붙습니다. 편집 직후에는 재임베딩이 끝날 때까지 이전 텍스트 버전으로
검색됩니다.

```bash
curl -X POST http://localhost:8000/api/search \
  -H "Authorization: Bearer $OPENARCHIVE_API_TOKEN" -H "Content-Type: application/json" \
  -d '{"query": "OpenProxy 풀 설정", "tags": ["opensql"], "content_type": "pdf", "k": 10}'
```

DB가 잠시 응답할 수 없는 동안(페일오버·switchover 등) API는 **`503` + `Retry-After`**를 돌려줍니다.
프로그램에서 호출한다면 지수 백오프로 다시 시도하되, 쓰기 요청은 첫 시도가 이미 반영됐을 수 있으니
자동으로 다시 보내지 마세요. 예외는 문서 생성입니다 — 요청마다 `Idempotency-Key` 헤더에 새 키를 붙이면
같은 키로 다시 보내도 문서는 한 번만 생기고 처음 문서가 돌아옵니다(`docs/ARCHITECTURE.md` 「멱등키」).
웹 화면(읽기·업로드)과 MCP 도구는 최대 60초까지 알아서 기다립니다.

### 근거 기반 답변 (선택)

로그인한 상태로 검색하면 결과 위에 **이 검색어로 답변 받기**가 나옵니다. 누르면, 검색이 고른 근거만으로 로컬 모델이 답하고 `[1]` 같은 인용이 그 텍스트 버전의
그 대목으로 이어집니다. 개정된 문서의 근거는 「v3 기준 · 현재 v4」로 표시됩니다. 답은 저장하지 않습니다.
**기본 꺼짐**이라 켜려면 Ollama와 모델(`qwen3:8b`, 약 5.2GB)을 설치합니다.

```bash
brew install ollama && brew services start ollama   # Linux: curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen3:8b
ANSWER_PROVIDER=ollama openarchive serve
```

맥 M2 Pro에서 한 번에 15~25초가 걸립니다. 근거에 없는 내용은 답하지 않도록 지시하지만 보장은 아닙니다. 모델 선정 실측과
설치 세부(메모리·오프라인·다른 호스트)는 [ADR-043](docs/ADR.md)과 [운영 가이드 「답변 생성」](docs/OPERATIONS.md#답변-생성-ollama-선택)에 있습니다.

### 문서 다듬기

문서 상세(`/documents/[id]`)에서 **문서 텍스트(파일 업로드 문서는 추출 텍스트)를 직접 편집**하면 텍스트 버전이 하나 쌓이고
재임베딩이 자동으로 기동합니다. 과거 버전을 펼쳐 보고 **되돌리기**를 누르면 이력을 되감는 것이
아니라 새 버전이 생겨 같은 파이프라인을 다시 탑니다. 같은 화면에서 **관련 문서**와 **태그
추천**(관련 문서들의 태그)을 보고 태그를 붙일 수 있습니다. 본문의 `[[제목]]`은 위키링크로
해석되어 백링크가 만들어집니다. `[[제목#절|별칭]]`과 `[[폴더/제목]]`도 제목으로 해석하며,
`![[첨부]]`는 링크가 아닙니다. 문서를 **삭제**하면 **휴지통**(`/trash`)으로 갑니다. 휴지통에 있는
동안 문서는 목록·검색·관련 문서·그래프 어디에도 보이지 않지만 청크·관계·버전은 그대로라, 소유자가
**복원**하면 재임베딩 없이 바로 다시 검색됩니다. 기본 30일(`TRASH_RETENTION_DAYS`)이 지나거나 소유자가
**영구 삭제**하면 청크·벡터·관계·대기 중인 작업까지 함께 사라집니다 — 애플리케이션이 지우는 것이 아니라
FK CASCADE가 처리합니다. 영구 삭제한 문서는 관리자가 Barman 백업을 삭제 전 시점으로 격리 인스턴스에
복원해 거기서 내보내 다시 넣는 수밖에 없습니다 ([운영 가이드 「백업과 복원」](docs/OPERATIONS.md#백업과-복원-barman)).

같은 화면의 **원본 파일** 절에서는 올린 파일을 판별로 **내려받을** 수 있습니다. **새 파일로 교체**하면
이전 원본은 지워지지 않고 판으로 남으며, 추출 텍스트가 달라졌을 때만 새 텍스트 버전이 생깁니다.
**원본에서 다시 추출**은 파서가 바뀐 뒤 보관된 원본으로 텍스트를 다시 뽑습니다 — 여러 문서에
한 번에 적용하려면 운영자가 `openarchive reextract --all`을 씁니다.

문서가 쌓이면 세 화면이 정리를 돕습니다.

- `/organize` — **정리할 문서**. 폴더 밖에 있는 내 문서마다 폴더에 든 관련 문서의 절반 이상이 모인 폴더를 추천합니다. 문서 화면의 폴더 칸에도 같은 추천이 나옵니다.
- `/clusters` — **관계 지도**. 태그 없이 저장된 관계 그래프의 Louvain 군집으로 묶입니다.
- `/diagnostics` — 고아 문서 · 동일 텍스트 · 깨진 위키링크.

### 터미널에서 쓰기 (사용자 CLI)

`openarchive`의 사용자 명령은 서버의 REST API에 **본인 API 토큰**으로 붙습니다 — DB 접속 정보(DSN)를
쓰지 않으므로 웹과 같은 열람 범위가 적용됩니다([ADR-057](docs/ADR.md)). 토큰은 `/settings`에서
발급하고, 로그인하면 `~/.openarchive/credentials.json`(권한 600)에 저장됩니다.

```bash
openarchive login --url http://<서버>:8000 --token <API 토큰>
openarchive doc upload guide.pdf --title "설치 가이드" --tag opensql
openarchive doc list
openarchive doc show <문서 ID>                      # --version N으로 과거 텍스트 버전
openarchive doc edit <문서 ID> --file notes.md      # 문서 텍스트를 파일 내용으로 바꿈
openarchive search "OpenProxy 풀 설정" --tag opensql
openarchive ask "연차 휴가는 어떻게 신청하나요"         # 서버에서 답변 생성이 켜져 있을 때
```

이 밖에 `whoami`, `doc download`·`restore`·`tag`·`delete`, `doc trash list`·`restore`가 있습니다. 열람 범위
변경·외부 공유·토큰 발급처럼 세션으로만 할 수 있는 일은 CLI에 없습니다. `--user`·`--dsn`을 붙인
`search`·`ask`와 `import`·`export` 등은 DB에 직접 붙는 운영자 명령입니다
([운영 가이드 「사용자 CLI」](docs/OPERATIONS.md#사용자-cli)).

### AI 에이전트 연결 (MCP)

Claude Desktop / Claude Code의 MCP 설정에 stdio 서버로 등록합니다. `command`는 위 `pipx install`이
만든 가상환경의 Python입니다 — `<PIPX_VENVS>`는 `pipx environment --value PIPX_LOCAL_VENVS`가 알려주는
경로로 바꿉니다(소스에서 설치했다면 그 가상환경의 `bin/python`). `DATABASE_URL`은
`~/.openarchive/.env`와 같은 값입니다.

```json
{
  "mcpServers": {
    "openarchive": {
      "command": "<PIPX_VENVS>/openarchive-server/bin/python",
      "args": ["-m", "openarchive.mcp_server.server"],
      "env": {
        "DATABASE_URL": "postgresql://openarchive:openarchive@localhost:5433/openarchive",
        "EMBEDDING_PROVIDER": "local",
        "MCP_USER_ID": "admin"
      }
    }
  }
}
```

에이전트는 `search_documents`로 발췌·출처·기준 버전을 받아 근거로 쓰고(`folder_id`로 폴더 범위 지정), `get_document`
(`version`으로 과거 텍스트 버전) · `list_documents`로 읽으며, `create_document`로 문서를 공급합니다. MCP 경로에서 답변 생성은 에이전트
쪽입니다 — 플랫폼 안의 답변(웹 답변 패널·`openarchive ask`·`POST /api/ask`)은 별도 옵션입니다.
`MCP_USER_ID`가 열람 범위와 문서 소유자를 정하므로 **실제 계정명과 정확히 같게** 적습니다
(실존 여부는 검증되지 않습니다 — [ADR-036](docs/ADR.md)). `EMBEDDING_PROVIDER`는 `serve`에
준 값과 같아야 합니다 — 다르면 에러 없이 검색 결과만 무의미해집니다.

원격 MCP는 `openarchive serve`의 `/mcp`에 주소와 설정 화면에서 발급한 API 토큰만 등록합니다:

```bash
claude mcp add --transport http openarchive http://<서버>:8000/mcp --header "Authorization: Bearer <토큰>"
```

읽기는 `read`·공유 토큰도 가능하고 생성은 `read_write` 사용자 토큰만 가능합니다.
다른 PC에서는 토큰 보호를 위해 TLS 프록시 뒤의 HTTPS 주소를 쓰세요.
[원격 등록 절차](docs/OPERATIONS.md#원격http)에 JSON 설정 예시와 인증 점검 방법이 있습니다.

### 계정과 토큰

- `/settings` — 내 비밀번호 변경 · API 토큰 발급·폐기 · 외부 공유와 읽기 전용 공유 토큰 관리.
  토큰 원문은 발급 직후 한 번만 보입니다.
- `/admin/groups` — 그룹 생성·삭제와 구성원 관리. 관리자 전용이며 그룹 열람 부여는 문서 소유자가 합니다.
- `/admin/users` · `/admin/status` — 사용자 발급 · 시스템 상태(임베딩 작업 카운터, 텍스트와 청크 버전이
  어긋난 문서 수). 관리자 전용이며, 관리자라도 남의 비공개 문서는 볼 수 없습니다.
- `/admin/audit` · `/admin/shares` — 감사 로그 조회·CSV 내보내기 · 외부 공유 전수 조회와 토큰 비상 폐기.
  관리자 전용입니다.
- `openarchive reset-password <user>` — 비밀번호를 잊은 계정을 서버에서 재설정. 웹에는 이 경로를
  두지 않습니다 ([ADR-040](docs/ADR.md)).

REST 전체 목록과 스키마는 `http://localhost:8000/docs`(OpenAPI)에 있습니다. 환경변수·프로세스
구성·복구 데모 등 운영 세부는 [운영 가이드](docs/OPERATIONS.md)를 보십시오.

---

## 아키텍처

이 아키텍처의 핵심은 **DB 안에 있는 것과 밖에 있는 것의 경계**입니다. 문서를 저장하면 트리거가
같은 트랜잭션에서 임베딩 작업·버전 이력·위키링크를 기록하고, 벡터 반영 뒤에는 관계 작업도
트리거가 만듭니다. 워커는 텍스트 추출·OCR·청킹·임베딩을 실행하고, 관계 판정은 DB 함수를
호출합니다. 워커가 중단돼도 커밋된 작업은 DB에 남으며, 처리 중 작업은 lease 만료 후 회수됩니다.

```
 ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
 │  Web UI      │  │  REST API    │  │  MCP Server  │   인터페이스
 │  (정적 빌드)   │  │  (위임 토큰)   │  │ (stdio·HTTP) │
 └──────┬───────┘  └──────┬───────┘  └──────┬───────┘
        └─────────────────┼─────────────────┘
                          ▼
              backend/openarchive/services/
              모든 인터페이스가 공유하는 단일 진입점
                          │  documents INSERT/UPDATE만 — 임베딩 호출 없음
                          ▼
        ┌──────────────────────────────────────────┐
        │  OpenProxy (VIP:6432)                    │
        │  커넥션 풀 · Primary 추적 · 재연결            │
        └────────────────────┬─────────────────────┘
                             ▼
 ┌──────────────────────────────────────────────────────┐
 │  OpenSQL — PostgreSQL 17.8 + pgvector 0.8.1          │
 │                                                      │
 │   documents ──AFTER trigger──▶ document_versions     │  ← 같은 트랜잭션
 │        │                  └──▶ embedding_jobs        │     (아웃박스)
 │        └──AFTER trigger─────▶ document_links         │
 │                                                      │
 │   document_chunks (vector(1024), HNSW)               │
 │        └─ready 전이 트리거─▶ embedding_jobs(kind=edges) │
 │   관계 워커 ──별도 트랜잭션의 DB 함수─▶ document_edges     │
 └───────────────────────┬──────────────────────────────┘
                         │  5초 폴링(주 경로) + NOTIFY(최적화)
                         ▼
              Embedding Worker — 무상태 실행기
              SKIP LOCKED claim → 청킹 → 임베딩 →
              해시 재확인 + 청크 교체 + job done (단일 트랜잭션)
```

청크가 있는 문서에서 텍스트 버전과 벡터 버전이 어긋난 문서 수는 아래 쿼리로 셉니다. 처리와
재시도가 성공하면 0으로 수렴합니다. 아직 청크가 없는 문서·실패한 작업·관계 미반영은 이 쿼리로
세지 않으므로 `/admin/status`의 작업·정합성 카운터도 함께 확인합니다.

```sql
SELECT count(DISTINCT d.id) FROM documents d
  JOIN document_chunks c ON c.document_id = d.id
WHERE c.version <> d.version;
```

**장애 복구와 데이터 내구성의 범위는 구분합니다.** 현재 구성(공식 3노드, 비동기 복제)에서
2026-10-04에 부하 중 장애를 주입해 다시 쟀습니다(#165) — 쓰기 중단은 Primary 노드 사망 30.6~40.8초,
Leader 네트워크 분리 30.7초, postmaster kill 14.1초, switchover·VIP 이동·Leader 재부팅 10.1~10.3초,
Replica·etcd 장애(과반 상실 포함) 0초였고, 커밋 응답을 받은 업로드(원본 파일 포함)의 유실·중복은
모든 회차에서 0이었습니다. **이는 관측값이지 보장이 아닙니다** — 비동기 복제라 Primary를 잃는 순간
replica에 닿지 않은 커밋은 사라질 수 있고, 복구 구간에는 쓰기가 멈춥니다(재시도가 그 구간을 흡수할
뿐 "무중단"이 아닙니다). 회차별 표와 재현 명령은 [OpenSQL 환경 구축 §16](docs/SETUP_OPENSQL.md)
「최종 구성 장애 검증」에, 공식 구성과 다르게 둔 설정은
[공식 구성과 다른 점](docs/OPENSQL_DEVIATIONS.md)에 있습니다. 위 수치는 #231·#258 교정(10/11·10/12)
**전** 구성에서 잰 것입니다 — #258 교정 전(etcd `endpoints`에 세 노드) 구성으로 10/12에 Leader 네트워크
분리를 다시 재니 쓰기가 111초 멈췄고, 교정 뒤 같은 분리는 30.7초였습니다([OpenSQL 조사 §21](docs/OPENSQL_RESEARCH.md#21-openproxy-etcd-watch가-사라진-노드에-매달린다-실측-2026-10-12)).

이 그림을 떠받치는 설계 결정은 셋입니다 — 워커의 **기동 방식은 정합성의 일부가 아니고**(5초 폴링이
주 경로, `NOTIFY`는 최적화), **검색은 필터·벡터 유사도·관계 확장까지 단일 SQL**이며, **볼 수 없는
문서는 자리 표시조차 남기지 않습니다**. 각 결정의 근거와 트레이드오프는 [ADR](docs/ADR.md)에,
스키마·트리거·워커·검색 상세는 [Architecture](docs/ARCHITECTURE.md)에 있습니다.

### 기술 스택

| 영역 | 사용 기술 |
|---|---|
| DB | [Tmax OpenSQL v3](https://docs.tibero.com/tmaxopensql/overview) — PostgreSQL 17 + pgvector 0.8.1 · OpenHA(Patroni) · OpenHA DCS(etcd) · OpenProxy |
| 백엔드 | Python 3.12+ · FastAPI · psycopg3 · `openarchive` CLI |
| 임베딩 | [`BAAI/bge-m3`](https://huggingface.co/BAAI/bge-m3) (MIT, 1024차원) — `sentence-transformers`로 로컬 구동 |
| 답변 생성 (선택) | [Qwen3-8B](https://huggingface.co/Qwen/Qwen3-8B) (Apache-2.0) — [Ollama](https://github.com/ollama/ollama)(MIT)로 로컬 구동, 기본 꺼짐 |
| 프론트엔드 | Next.js (App Router) · TypeScript · Tailwind CSS · JSZip — 정적 빌드를 백엔드에 동봉 |
| 군집 | `networkx` Louvain |
| MCP | Python `mcp` SDK (FastMCP, stdio·Streamable HTTP) |

---

## 문서

| 문서 | 내용 |
|---|---|
| [Architecture](docs/ARCHITECTURE.md) | 스키마·트리거·워커·검색·고가용성 상세 |
| [ADR](docs/ADR.md) | 설계 결정과 각각의 근거·트레이드오프 — 재보고 물러난 결정 포함 |
| [PRD](docs/PRD.md) | 제품 요구사항, 하지 않는 것 |
| [Roadmap](docs/ROADMAP.md) | 확장점 지도와 단계적 발전 경로 |
| [운영 가이드](docs/OPERATIONS.md) | 환경변수, 프로세스 구성, 인증, 백업·복원, 복구 데모 |
| [성능 검증](docs/PERFORMANCE_VALIDATION.md) | 검색 인덱스·지속 업로드·큰 파일의 재현 절차와 측정 한계 |
| [검색 품질 검증](docs/SEARCH_QUALITY_VALIDATION.md) | 프로젝트 예시 질문의 문서 순위·발췌 평가와 개선점 |
| [OpenSQL 조사](docs/OPENSQL_RESEARCH.md) | 배포판 확정 사항, 공식 문서 조사, 실측 기록 |
| [OpenSQL 환경 구축](docs/SETUP_OPENSQL.md) | Rocky Linux 9.7 VM 준비부터 설치·검증까지, HA 3노드·Barman 백업 노드 |
| [공식 구성과 다른 점](docs/OPENSQL_DEVIATIONS.md) | HA 환경이 OpenSQL 문서·배포판과 다른 설정과 그 이유 |
| [UI Guide](docs/UI_GUIDE.md) | 디자인 원칙, 화면 구성 |
| [Contributing](CONTRIBUTING.md) | 개발 규약, 브랜치·커밋 컨벤션 |

---

## 고지

본 제품은 한컴의 HWP 문서 파일(.hwp) 공개 문서를 참고하여 개발하였습니다.

서드파티 구성 요소와 라이선스는 [NOTICE.md](NOTICE.md)에 있습니다.
