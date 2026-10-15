<div align="center">

# OpenArchive

**문서를 고치면 DB가 벡터를 따라 맞추는, AI를 위한 문서관리 플랫폼**

OpenArchive는 조직의 PostgreSQL·[Tmax OpenSQL](https://docs.tibero.com/tmaxopensql/overview)에 설치하는 문서관리 플랫폼입니다.
문서를 올리면 텍스트 추출부터 임베딩·문서 관계까지 자동으로 이어지고, 사람·프로그램·AI 에이전트가
같은 문서를 같은 권한 규칙 아래에서 찾습니다. 임베딩은 상용 API 없이 로컬 모델로 합니다.

[![CI](https://github.com/jeongeundev/OpenArchive/actions/workflows/ci.yml/badge.svg)](https://github.com/jeongeundev/OpenArchive/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/openarchive-server.svg)](https://pypi.org/project/openarchive-server/)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![OpenSQL](https://img.shields.io/badge/OpenSQL-PostgreSQL%2017-336791.svg)](https://docs.tibero.com/tmaxopensql/overview)
[![Embedding](https://img.shields.io/badge/embedding-BGE--M3%20(MIT)-orange.svg)](https://huggingface.co/BAAI/bge-m3)

[시작하기](#시작하기) · [사용 방법](#사용-방법) · [명령어](#명령어) · [아키텍처](#아키텍처) · [문서](#문서) · [운영 가이드](docs/OPERATIONS.md) · [로드맵](docs/ROADMAP.md)

</div>

---

## OpenArchive란?

문서를 벡터 DB에 넣는 일은 쉽습니다. 어려운 것은 **문서가 바뀐 뒤**입니다. 누군가 규정을 고쳤는데 검색은 여전히 옛
문장을 근거로 내놓고, 지운 문서가 AI의 답변에 계속 인용되고, 장애 한 번에 처리 대기 중이던 작업이 사라집니다.
원본과 벡터를 애플리케이션 코드가 따로 맞추면 그 사이에 틈이 생깁니다.

OpenArchive는 이 정합성을 **DB 계층에서** 지킵니다. 문서를 저장하면 DB 트리거가 같은 트랜잭션 안에서 임베딩 작업·버전
이력·위키링크를 기록하므로, 어떤 경로로 저장하든 작업이 빠지지 않습니다. 워커는 그 작업을 꺼내 추출·청킹·임베딩만
수행하고, 중단되더라도 커밋된 작업은 DB에 남아 다시 처리됩니다. 검색은 태그·유형·폴더·권한 필터와 벡터 유사도, 저장된
문서 관계를 **하나의 SQL**로 결합합니다.

보장 범위는 **버전 일관성 + 최신 수렴**입니다 — 검색되는 청크는 어느 시점에도 하나의 텍스트 버전이며, 변경은 유한 시간
안에 최신으로 수렴합니다. 즉시 반영은 약속하지 않습니다([ADR-015](docs/ADR.md)).

같은 문서에 네 가지 입구가 있습니다. 사람은 **웹 화면**으로, 프로그램은 **REST API**로, 터미널에서는 **사용자 CLI**로,
AI 에이전트는 **MCP**로 접근하며, 넷 모두 같은 열람 규칙을 따릅니다 — 볼 수 없는 문서는 어느 입구에서도 존재하지 않는
것처럼 보입니다.

---

## 주요 기능

### 올리면 알아서 정리됩니다

- **11개 형식** — PDF·DOCX·TXT·MD·HWP·HWPX·XLSX·PPTX·PNG·JPG·JPEG, ZIP 일괄 업로드. 이미지와 스캔 PDF는 tesseract로
  한국어·영어 텍스트를 인식합니다([ADR-052](docs/ADR.md)).
- **원본 보관** — 올린 원본 파일은 DB 안에 판으로 보관되어 내려받고, 교체하고, 다시 추출할 수 있습니다.
- **자동 관계** — 임베딩이 끝나면 문서 사이의 관계(「관련 있음」·「여러 대목에서 만난다」·「본문에서 가리킨다」)가 저장됩니다.
  관계 그래프의 Louvain 군집으로 태그 없이 묶은 **관계 지도**를 그립니다.
- **정리 추천** — 관련 문서의 절반 이상이 모인 폴더를 추천합니다. 옮기는 것은 사람이 합니다([ADR-064](docs/ADR.md)).
  고아 문서·동일 텍스트·깨진 위키링크도 진단합니다.

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/images/relation-map-dark.png">
    <img alt="관계 지도 — 예제 문서 64건이 관계 그래프의 군집으로 재무·고객지원·인사·보안·개인정보·재고 여섯 덩어리로 묶인 모습" src="docs/images/relation-map-light.png" width="560">
  </picture>
  <br>
  <sub>관계 지도 — 예제 문서 64건을 태그 없이 관계만으로 묶은 결과</sub>
</p>

### 필요한 것을 찾아 줍니다

- **하이브리드 검색** — 필터와 벡터 유사도, 문서 관계(깊이 2)를 단일 SQL로 결합합니다. 필터는 벡터 정렬 안쪽에서
  적용되므로 볼 수 없는 문서가 후보 자리를 차지하지 않습니다.
- **왜 나왔는지** — 직접 맞은 결과는 유사도와 맞은 대목으로, 관계를 타고 온 결과는 「「연차휴가 사용 기준」에서
  「관련 있음」로 이어짐」처럼 출발 문서와 관계 종류로 이유를 보여 줍니다.
- **근거 기반 답변 (선택, 기본 꺼짐)** — 검색이 고른 발췌만 근거로 로컬 LLM(Ollama)이 답하고, 인용 번호가 그 텍스트
  버전의 그 대목으로 이어집니다. 근거를 고르는 검색에는 LLM을 쓰지 않습니다([ADR-043](docs/ADR.md)).

### 고치고, 되돌리고, 비교합니다

- **텍스트 버전** — 문서 텍스트를 편집하면 버전이 쌓이고 재임베딩이 자동으로 걸립니다. 처리 중에는 이전 버전으로
  검색이 계속됩니다. 되돌리기는 새 버전을 만들며, 두 버전의 바뀐 대목을 비교합니다.
- **원본 미리보기** — PDF·이미지는 브라우저에서 바로, HWP·HWPX·DOCX·XLSX·PPTX는 격리된 변환기가 PDF로 바꿔
  보여 줍니다(선택 설치, [ADR-058](docs/ADR.md)).
- **위키링크** — 본문의 `[[제목]]`이 링크와 백링크가 됩니다.
- **휴지통** — 삭제한 문서는 어디에도 보이지 않다가, 소유자가 복원하면 재임베딩 없이 바로 다시 검색됩니다
  ([ADR-060](docs/ADR.md)).

### 볼 사람만 봅니다

- **열람 범위** — 조직 공개·비공개와 사용자·그룹 부여, 폴더에서 상속되는 범위([ADR-054](docs/ADR.md)). 볼 수 없는 문서와
  폴더는 검색·관련 문서·그래프·집계·문서 수 어디에도 나타나지 않습니다.
- **관리와 열람의 분리** — 관리자라도 남의 비공개 문서는 보지 못합니다([ADR-040](docs/ADR.md)).
- **외부 공유** — 지정한 문서·폴더만 읽는 읽기 전용 공유 토큰. 위임 API 토큰은 `read`·`read_write`로 나뉩니다.
- **감사 로그** — 생성·수정·삭제·열람 범위 변경·원본 내려받기 등을 **DB가 같은 트랜잭션에서** 기록하고, 감사 행의
  수정·삭제는 DB가 거부합니다([ADR-055](docs/ADR.md)).

### 장애에서 돌아옵니다

- **자동 복구** — 장애 감지·재기동·재연결은 OpenSQL(Patroni·OpenProxy)이 맡고, 앱은 단일 엔드포인트로만 접속합니다.
  복구 구간에는 쓰기가 잠시 멈추고, API는 `503` + `Retry-After`로 알립니다.
- **백업·시점 복원** — 별도 백업 노드의 Barman이 WAL을 받아 전체 복원과 PITR을 지원합니다([ADR-053](docs/ADR.md)).

---

## 시작하기

필요한 것은 Python 3.12+와 OpenSQL(또는 pgvector가 있는 PostgreSQL 17)입니다. **DSN만 자기 환경으로 바꿉니다** —
OpenProxy 경유라면 데이터베이스 자리에 **풀 이름**을 적습니다.

```bash
pipx install "openarchive-server[local]"
openarchive init --dsn "postgresql://app:secret@<OpenProxy 호스트>:6432/<풀 이름>"
EMBEDDING_PROVIDER=local openarchive serve
```

`init`은 연결·확장·권한을 점검한 뒤 적용해도 되는지 묻고, 첫 관리자 `admin`의 비밀번호를 묻습니다. 브라우저에서
http://localhost:8000 을 열고 로그인합니다.

### 5분 둘러보기

예제 문서(가상 회사의 사내 규정 64건)를 넣고 바로 찾아봅니다.

```console
$ openarchive demo --user admin
완료: 문서 64건 · 청크 77개 · 관계 228쌍 · 관계 재계산 64건 · 8.0초

$ openarchive search "연차 휴가는 어떻게 신청하나요" --user admin
1. 연차휴가 사용 기준  0.645  [경영지원, 인사, 근무제도]
   # 연차휴가 사용 기준 ## 발생 입사 첫 해에는 한 달을 개근할 때마다 하루씩 생긴다. …
2. 유연근무제 신청 절차  0.571  [경영지원, 인사, 근무제도]
   …
```

웹 화면의 **관계 지도**(`/clusters`)에서 이 문서들이 어떻게 묶였는지 보고, 문서 하나를 열어 텍스트를 고친 뒤 다시
검색해 보세요.

<details>
<summary>설치 전에 알아 둘 것</summary>

- **`[local]`** 은 BGE-M3 임베딩 모델입니다(torch 포함, 수 GB). 처음 `serve`할 때 Hugging Face에서 모델(약 2GB)을
  내려받으므로 인터넷이 필요하고 기동이 오래 걸립니다. 한 번 받은 뒤에는 `HF_HUB_OFFLINE=1`로 인터넷 없이 구동됩니다.
  동작만 빠르게 확인하려면 `[local]` 없이 설치하고 `EMBEDDING_PROVIDER=local`을 빼도 됩니다 — 가짜 벡터로 파이프라인
  전 구간이 돌지만 **검색 결과에 의미가 없습니다**.
- pipx가 3.12보다 낮은 Python을 기본으로 쓰면 `--python python3.12`를 붙입니다.
- `init --yes`는 예/아니오 확인만 건너뜁니다. 입력이 없는 곳에서는 `--dsn`과 `ADMIN_PASSWORD`를 함께 줍니다. 확인한 DSN은
  `~/.openarchive/.env`에 기록됩니다(`OPENARCHIVE_HOME`으로 위치를 바꿉니다). 기존 테이블이 있으면 아무것도 바꾸지
  않습니다([ADR-039](docs/ADR.md)).
- 이미지·스캔 PDF의 텍스트 인식에는 시스템 패키지 **tesseract와 한국어 모델**이 필요합니다 — 없으면 그 문서만
  「텍스트 인식 실패」로 끝나고 다른 형식은 그대로 동작합니다([운영 가이드](docs/OPERATIONS.md#ocr-엔진-tesseract)).
- ⚠️ 풀 이름과 계정은 OpenProxy 설정(`openproxy.toml`)에서 확인합니다. 설치기는 `opensql` 데이터베이스를 만들어 놓고
  정작 풀은 관리용 `postgres`를 바라보게 설정하니, DSN을 주기 전에 풀이 어느 DB를 가리키는지 확인하세요
  ([OpenSQL 환경 구축 §10](docs/SETUP_OPENSQL.md#10-설치-확인)). 업무 데이터가 이미 있는 OpenSQL이라면 새 DB·새 풀로
  설치합니다 — DBA가 미리 만들 확장(`vector`만, `pg_trgm`은 만들지 않음)과 풀 크기(설치기 기본 `pool_size = 10`은 모자람,
  권장 20)가 같은 절에 있습니다. 새 DB를 만들 수 없으면 `init --schema`로 접속 롤 이름의 스키마에 설치합니다
  ([운영 가이드](docs/OPERATIONS.md#openarchive-init)).
- 0.1.0에서 올리는 절차는 [운영 가이드 「판 올리기」](docs/OPERATIONS.md#판-올리기--pypi-010에서-020으로)에 있습니다.

</details>

<details>
<summary>OpenSQL 없이 써 보기 — Docker로 DB만 띄우기</summary>

OpenArchive는 표준 PostgreSQL 17 + pgvector 인터페이스에 의존하므로 `pgvector/pgvector:pg17` 컨테이너에서도
**같은 애플리케이션 코드가 그대로 동작합니다.** 단, OpenHA(Patroni)의 장애 복구와 OpenProxy의 커넥션 풀링은 실
OpenSQL 환경에서만 검증할 수 있습니다([ADR-006](docs/ADR.md)).

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

### 컨테이너로 실행

Python 없이 컨테이너로도 띄울 수 있습니다. 이미지에는 API·워커·웹 화면과 tesseract가 들어 있고 **DB는 들어 있지
않습니다** — 위와 같은 DSN을 줍니다. 이미지는 아직 레지스트리에 게시하지 않아 저장소에서 빌드합니다.

```bash
git clone https://github.com/jeongeundev/OpenArchive && cd OpenArchive
docker build -t openarchive backend/
docker run -d --name openarchive -p 8000:8000 -v openarchive:/data \
  -e DATABASE_URL="postgresql://app:secret@<OpenProxy 호스트>:6432/<풀 이름>" \
  -e ADMIN_PASSWORD='change-me' \
  openarchive
```

기동할 때마다 `init`을 먼저 실행한 뒤 `serve`로 넘어갑니다. 첫 기동은 BGE-M3 가중치를 받느라 1~2분 걸리고, 메모리는
5GB 이상을 줍니다. 가중치는 `/data` 볼륨에 남습니다. 같은 호스트의 DB에 붙을 때는 `localhost` 대신
`host.docker.internal`(Linux는 `--add-host=host.docker.internal:host-gateway`를 함께)을 씁니다
([운영 가이드 「앱 이미지」](docs/OPERATIONS.md#앱-이미지)).

**텔레메트리는 없습니다.** 앱이 DB 밖으로 여는 연결은 두 가지뿐입니다 — 처음 실행할 때 BGE-M3를 Hugging Face에서
내려받는 것, 그리고 답변 생성을 켰을 때(`ANSWER_PROVIDER=ollama`) 설정한 Ollama 주소입니다. 2026-10-15에
`HF_HUB_OFFLINE=1`로 업로드·임베딩·검색을 하는 동안 API·워커 프로세스의 연결은 DB와 자기 포트뿐이었습니다.

---

## 사용 방법

### 웹 화면

| 화면 | 하는 일 |
|---|---|
| `/` 문서 | 드롭존에 파일·ZIP을 끌어다 놓아 올리고(파일당 50MB), 폴더 트리·필터로 문서를 봅니다. 처리 상태가 「임베딩 중」→「완료」로 바뀝니다 |
| `/search` 검색 | 검색어와 태그·유형·폴더로 찾습니다. 결과마다 왜 나왔는지가 붙고, 답변 생성을 켰다면 「이 검색어로 답변 받기」가 나옵니다 |
| `/documents/[id]` 문서 | 텍스트 편집·버전 비교·되돌리기, 원본 내려받기·교체·미리보기, 열람 범위·태그·폴더, 관련 문서와 태그 추천 |
| `/clusters` 관계 지도 | 관계 그래프의 군집 |
| `/organize` 정리할 문서 | 폴더 밖 문서마다 추천 폴더 |
| `/diagnostics` 문서 진단 | 고아 문서 · 동일 텍스트 · 깨진 위키링크 |
| `/trash` 휴지통 | 복원 · 영구 삭제(기본 30일 뒤 자동) |
| `/settings` 설정 | 비밀번호 · API 토큰 발급·폐기 · 외부 공유 |
| `/admin/*` 관리 | 사용자·그룹 · 외부 공유 전수 조회 · 감사 로그(CSV) · 시스템 상태. 관리자 전용 |

### 터미널 (사용자 CLI)

사용자 명령은 서버의 REST API에 **본인 API 토큰**으로 붙습니다 — DB 접속 정보를 쓰지 않으므로 웹과 같은 열람 범위가
적용됩니다([ADR-057](docs/ADR.md)). 토큰은 `/settings`에서 발급합니다.

```bash
openarchive login --url http://<서버>:8000 --token <API 토큰>
openarchive doc upload guide.pdf --title "설치 가이드" --tag opensql
openarchive search "OpenProxy 풀 설정" --tag opensql
openarchive doc show <문서 ID> --version 2
openarchive ask "연차 휴가는 어떻게 신청하나요"         # 서버에서 답변 생성이 켜져 있을 때
```

### 프로그램 (REST API)

```bash
# 파일 업로드 (read_write 토큰)
curl -X POST http://localhost:8000/api/documents \
  -H "Authorization: Bearer $OPENARCHIVE_API_TOKEN" \
  -F "file=@guide.pdf" -F "title=설치 가이드" -F "tags=opensql" -F "visibility=private"

# 검색
curl -X POST http://localhost:8000/api/search \
  -H "Authorization: Bearer $OPENARCHIVE_API_TOKEN" -H "Content-Type: application/json" \
  -d '{"query": "OpenProxy 풀 설정", "tags": ["opensql"], "content_type": "pdf", "k": 10}'
```

전체 목록과 스키마는 `http://localhost:8000/docs`(OpenAPI)에 있습니다. 텍스트를 직접 공급하고 임베딩이 끝날 때까지
기다리는 독립 예제는 [`examples/ingest_text.py`](examples/ingest_text.py)입니다.

DB가 잠시 응답할 수 없는 동안(페일오버·switchover 등) API는 **`503` + `Retry-After`**를 돌려줍니다. 지수 백오프로 다시
시도하되, 쓰기 요청은 첫 시도가 이미 반영됐을 수 있으니 자동으로 다시 보내지 마세요. 예외는 문서 생성입니다 — 요청마다
`Idempotency-Key` 헤더에 새 키를 붙이면 같은 키로 다시 보내도 문서는 한 번만 생깁니다.

### AI 에이전트 (MCP)

원격 MCP는 `openarchive serve`의 `/mcp`에 주소와 API 토큰만 등록합니다.

```bash
claude mcp add --transport http openarchive http://<서버>:8000/mcp --header "Authorization: Bearer <토큰>"
```

| 도구 | 하는 일 |
|---|---|
| `search_documents` | 발췌·출처·기준 텍스트 버전을 돌려줍니다. `folder_id`로 폴더 범위를 좁힙니다 |
| `get_document` | 문서 텍스트·텍스트 버전 목록·청크 상태·관련 문서. `version`으로 과거 텍스트 버전 |
| `list_documents` | 볼 수 있는 문서 목록 |
| `create_document` | 문서를 공급합니다(`read_write` 사용자 토큰만) |

MCP 경로에서 답변 생성은 에이전트 쪽이 합니다. Claude Desktop 등에 stdio로 등록하는 설정은
[운영 가이드 「로컬(stdio)」](docs/OPERATIONS.md#로컬stdio)에, 원격 등록의 JSON 예시와 인증 점검은
[「원격(HTTP)」](docs/OPERATIONS.md#원격http)에 있습니다. 다른 PC에서는 TLS 프록시 뒤의 HTTPS 주소를 쓰세요.

---

## 명령어

`openarchive` 하나에 두 종류의 명령이 있습니다. **사용자 명령**은 REST에 API 토큰으로 붙고, **운영자 명령**은 DB에
직접 붙습니다(`--dsn` 또는 `~/.openarchive/.env`, 열람 주체는 `--user`). 각 명령의 옵션은 `openarchive <명령> --help`로
봅니다.

| 사용자 명령 | 하는 일 |
|---|---|
| `login` · `whoami` | API 토큰을 저장한다 · 로그인한 사용자와 토큰 범위를 보인다 |
| `doc list` · `show` · `download` | 문서 목록 · 텍스트 출력(`--version N`) · 최신 원본 내려받기 |
| `doc upload` · `edit` · `restore` · `tag` | 새 문서 · 텍스트를 파일 내용으로 교체 · 과거 버전으로 새 버전 · 태그 교체 |
| `doc delete` · `doc trash list` · `doc trash restore` | 휴지통으로 · 휴지통 보기 · 복원 |
| `search` · `ask` | 검색 · 근거 기반 답변(서버에서 켜져 있을 때) |

| 운영자 명령 | 하는 일 |
|---|---|
| `init` | DB를 점검하고 스키마를 적용한다. 첫 관리자를 만든다 |
| `serve` | API + 임베딩 워커 + 웹 화면을 함께 실행한다 |
| `create-user` · `reset-password` | 계정을 만든다 · 비밀번호를 잊은 계정을 재설정한다(웹에는 이 경로가 없다) |
| `import` · `export` | 폴더째 넣는다(이미 있는 내용은 건너뜀) · 소유 문서를 frontmatter 붙은 마크다운으로 내보낸다 |
| `demo` | 예제 문서 64건을 넣는다 |
| `rebuild-edges` | 대량 적재 뒤 관계를 전체 코퍼스 기준으로 다시 계산한다 |
| `reextract` | 보관된 원본에서 텍스트를 다시 추출한다(파서를 고친 뒤) |
| `rebuild-previews` | 한글·오피스 원본의 미리보기 변환을 다시 건다 |
| `search` · `ask` (`--user`) | DB에 직접 붙어 지정한 사용자의 열람 범위로 검색·답변한다 |

환경변수 전체와 프로세스 구성은 [운영 가이드](docs/OPERATIONS.md)에 있습니다.

---

## 아키텍처

```
   Web UI          REST API · user CLI        MCP (stdio · HTTP)      operator CLI
      │                    │                          │                     │
      ▼                    ▼                          ▼                     ▼
  ┌───────────────────────────────────────────────────────────────────────────┐
  │                openarchive services  (one visibility rule)                │
  └─────────────────────────────────────┬─────────────────────────────────────┘
                                        │  INSERT / UPDATE only
                                        ▼
                          ┌────────────────────────────┐
                          │   OpenProxy  (VIP :6432)   │
                          └─────────────┬──────────────┘
                                        ▼
  ┌─────────────────────────────────────────────────────────┐   ┌──────────────┐
  │     OpenSQL HA  ·  PostgreSQL 17 + pgvector  (3 nodes)  │──>│    Barman    │
  │     triggers ─> jobs · versions · links · audit log     │   │  (WAL, PITR) │
  └─────────────────────────────────────┬───────────────────┘   └──────────────┘
                                        │  claim jobs  (5s poll + NOTIFY)
                                        ▼
                          ┌────────────────────────────┐
                          │           Worker           │
                          │  OCR · embed · edges · PDF │
                          └────────────────────────────┘
```

경계는 **DB 안과 밖**입니다. 앱은 문서를 쓰기만 하고, 해야 할 일은 DB가 정합니다.

- **DB 안 (같은 트랜잭션)** — 문서를 저장하면 트리거가 텍스트 버전·처리 작업·위키링크·감사 로그를 함께 남깁니다. 원본 판을
  올리면 미리보기 작업과 감사 로그가, 임베딩이 끝나면 관계 작업이 생깁니다. 관계 판정 규칙도 DB 함수가 소유합니다.
- **DB 밖 (워커)** — 작업을 `SKIP LOCKED`로 집어 실행만 하는 무상태 실행기입니다. 작업은 네 종류입니다 — `extract`(스캔
  쪽 OCR) · `embed`(청킹·임베딩 후 해시를 다시 확인하고 청크를 교체) · `edges`(관계 판정 DB 함수 호출) · `preview`(한글·
  오피스 원본을 격리된 변환기로 PDF). 보존 기간이 지난 휴지통 문서도 워커가 영구 삭제합니다. 워커가 중단돼도 커밋된 작업은
  DB에 남고, 처리 중이던 작업은 lease 만료 뒤 다시 집힙니다.
- **장애** — Primary 추적과 재연결은 OpenProxy가, 장애 감지와 승격은 Patroni·etcd가 맡습니다. 앱은 VIP 하나에만 붙습니다.

이 그림을 떠받치는 설계 결정은 셋입니다.

1. **작업 생성은 DB가 한다.** 애플리케이션은 `embedding_jobs`에 직접 넣지 않습니다. 워커의 기동 방식은 정합성의 일부가
   아닙니다 — 5초 폴링이 주 경로이고 `NOTIFY`는 최적화입니다.
2. **검색은 단일 SQL이다.** 필터·벡터 유사도·관계 확장을 한 쿼리로 결합하고, DB에서 넓게 가져와 앱에서 거르지 않습니다.
3. **볼 수 없는 문서는 자리 표시조차 남기지 않는다.** 모든 경로가 `services/visibility.py`의 열람 술어 하나를 씁니다.

텍스트 버전과 벡터 버전이 어긋난 문서 수는 아래 쿼리로 셉니다. 처리와 재시도가 성공하면 0으로 수렴하며, 관리자 화면
`/admin/status`에도 작업·정합성 카운터가 있습니다.

```sql
SELECT count(DISTINCT d.id) FROM documents d
  JOIN document_chunks c ON c.document_id = d.id
WHERE c.version <> d.version;
```

각 결정의 근거와 트레이드오프는 [ADR](docs/ADR.md)에, 스키마·트리거·워커·검색 상세는 [Architecture](docs/ARCHITECTURE.md)에
있습니다.

### 기술 스택

| 영역 | 사용 기술 |
|---|---|
| DB | [Tmax OpenSQL v3](https://docs.tibero.com/tmaxopensql/overview) — PostgreSQL 17 + pgvector 0.8.1 · OpenHA(Patroni) · OpenHA DCS(etcd) · OpenProxy |
| 백엔드 | Python 3.12+ · FastAPI · psycopg3 · `openarchive` CLI |
| 임베딩 | [`BAAI/bge-m3`](https://huggingface.co/BAAI/bge-m3) (MIT, 1024차원) — `sentence-transformers`로 로컬 구동 |
| 답변 생성 (선택) | [Qwen3-8B](https://huggingface.co/Qwen/Qwen3-8B) (Apache-2.0) — [Ollama](https://github.com/ollama/ollama)(MIT)로 로컬 구동, 기본 꺼짐 |
| 프론트엔드 | Next.js (App Router) · TypeScript · Tailwind CSS · JSZip — 정적 빌드를 백엔드에 동봉 |
| 텍스트 인식 · 군집 | tesseract 5 (kor·eng) · `networkx` Louvain |
| MCP | Python `mcp` SDK (FastMCP, stdio·Streamable HTTP) |

---

## 검증된 범위

실제 OpenSQL HA 3노드(비동기 복제)와 Barman 백업 서버에서 잰 값입니다. **모두 관측값이며 보장이 아닙니다** — 조건과
한계는 각 출처에 있습니다.

| 항목 | 결과 | 출처 |
|---|---|---|
| 장애 전환 (부하 중 주입, 2026-10-04) | 쓰기 중단: Primary 노드 사망 30.6~40.8초 · postmaster kill 14.1초 · switchover·VIP 이동·Leader 재부팅 10.1~10.3초 · Replica·etcd 장애 0초. 커밋 응답을 받은 업로드의 유실·중복은 전 회차 0 | [OpenSQL 환경 구축 §16](docs/SETUP_OPENSQL.md#16-ha-3노드-구성-110) |
| 원본-벡터 정합성 | 위 장애 전 회차에서 텍스트 버전과 청크 버전이 어긋난 문서 수가 0으로 수렴 | [OpenSQL 환경 구축 §16](docs/SETUP_OPENSQL.md#16-ha-3노드-구성-110) |
| 백업·복원 (Barman, 2026-10-04) | PITR 복원본이 복원 지점과 같음, 쓰기 가능까지 161초 · 전체 복원에서 응답 성공한 업로드 120건 유실 0 | [운영 가이드 「복원 재현」](docs/OPERATIONS.md#복원-재현--scriptsdr_restorepy) |
| 검색 품질 (형식별 실문서 10개·질문 24개) | 정답 문서 첫 결과 18/20 · 상위 5 20/20 · MRR 0.950 · 다른 계정의 비공개 문서 누출 0 | [검색 품질 검증](docs/SEARCH_QUALITY_VALIDATION.md#형식별-실문서-기준선-167) |
| 검색 성능 (20,000 청크, 2026-10-15) | 15개 계획 모두 HNSW 사용, 제품 SQL 중앙값 광범위 358ms · 태그 920ms · 유형 155ms | [성능 검증](docs/PERFORMANCE_VALIDATION.md#실제-opensql-재측정--2026-10-15) |

비동기 복제라 Primary를 잃는 순간 Replica에 닿지 않은 커밋은 사라질 수 있고, 복구 구간에는 쓰기가 멈춥니다 — 재시도가
그 구간을 흡수할 뿐 무중단이 아닙니다. 실측 중에 찾은 OpenProxy의 동작(`BEGIN READ ONLY`의 Replica 라우팅, 세션 상태
부분 초기화, 서버 명령문 누적, 콜드 부팅 설정 덮어쓰기, etcd watch 매달림)과 그 대응은
[OpenSQL 조사](docs/OPENSQL_RESEARCH.md)에, 공식 구성과 다르게 둔 설정은 [공식 구성과 다른 점](docs/OPENSQL_DEVIATIONS.md)에
있습니다.

---

## 문서

| 하려는 일 | 여기서 시작 |
|---|---|
| 설치하고 운영하기 — 환경변수·프로세스·인증·백업과 복원 | [운영 가이드](docs/OPERATIONS.md) |
| OpenSQL HA 환경 만들기 — VM 준비부터 3노드·Barman까지 | [OpenSQL 환경 구축](docs/SETUP_OPENSQL.md) · [공식 구성과 다른 점](docs/OPENSQL_DEVIATIONS.md) |
| 구조 이해하기 — 스키마·트리거·워커·검색 | [Architecture](docs/ARCHITECTURE.md) |
| 왜 이렇게 만들었는지 — 물러난 결정 포함 | [ADR](docs/ADR.md) |
| 무엇을 하고 무엇을 하지 않는지 | [PRD](docs/PRD.md) · [Roadmap](docs/ROADMAP.md) |
| 측정을 다시 해 보기 | [성능 검증](docs/PERFORMANCE_VALIDATION.md) · [검색 품질 검증](docs/SEARCH_QUALITY_VALIDATION.md) |
| OpenSQL의 실제 동작 | [OpenSQL 조사](docs/OPENSQL_RESEARCH.md) |
| 화면 규칙 | [UI Guide](docs/UI_GUIDE.md) |

---

## 개발

기여 규약과 개발 환경은 [CONTRIBUTING](CONTRIBUTING.md)에 있습니다. 전체 검증의 정본은 CI와 같은 `scripts/check.sh`입니다.

```bash
docker compose up -d                                   # 로컬 DB (pgvector 컨테이너)
cd backend && python3 -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]"
bash scripts/check.sh                                  # 저장소 루트에서 — backend + frontend
```

---

## 프로젝트 상태

1인이 개발·유지보수하며, 상용 지원은 없습니다. 실측한 구성은 OpenSQL 17.8 HA 3노드(같은 L2의 VRRP VIP)와 Barman 백업 서버
위에 앱 한 벌이고, 그 밖의 구성(클라우드 VIP, 앱 여러 벌, 대량 이관)은 실측하지 않았습니다 —
[알려진 한계](docs/OPERATIONS.md#알려진-한계) · [로드맵](docs/ROADMAP.md).

---

## 라이선스

[MIT](LICENSE). 서드파티 구성 요소와 라이선스는 [NOTICE.md](NOTICE.md)에 있습니다.

본 제품은 한컴의 HWP 문서 파일(.hwp) 공개 문서를 참고하여 개발하였습니다.
