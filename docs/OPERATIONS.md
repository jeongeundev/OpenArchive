# 운영 가이드

README 「시작하기」로 기동한 뒤, 설정을 바꾸거나 운영하면서 걸리기 쉬운 것들을 모았습니다.
기동 절차 자체는 [README](../README.md#시작하기)가 정본이며 여기에 복제하지 않습니다.

## 환경변수 파일

애플리케이션 설정 파일은 **`~/.openarchive/.env` 하나**입니다. `openarchive init`이 DSN을 여기에
기록하고, 다른 설정은 바꿀 때만 적습니다. 위치는 환경변수 `OPENARCHIVE_HOME`으로 바꿉니다
(`$OPENARCHIVE_HOME/.env`).

```bash
mkdir -p ~/.openarchive && cp backend/.env.example ~/.openarchive/.env   # 예시에서 시작할 때
```

설정을 읽는 주체는 `openarchive` CLI·API·워커·MCP 서버인데 실행 디렉토리가 서로 다릅니다.
`openarchive/config.py`가 이 파일 한 곳만 절대경로로 읽어 모두 같은 값을 보게 합니다. 설치 위치(패키지 옆)에
두지 않는 이유는 `pip install`로 깐 설치본에서 그 자리가 site-packages이기 때문입니다.
환경변수를 직접 주는 방식(`DATABASE_URL=... openarchive serve`)은 언제나 파일보다 우선합니다.

> **저장소 루트의 `.env`는 다른 파일입니다.** 애플리케이션은 읽지 않지만 **`docker compose`가
> 읽습니다** — `docker-compose.yml`의 `${POSTGRES_USER:-openarchive}` 세 자리를 채우는 것이 그
> 파일입니다. 로컬 DB의 자격증명·DB 이름을 바꾸려면 루트 `.env`에 `POSTGRES_*`를 두고, 앱이 붙을
> 주소는 `~/.openarchive/.env`의 `DATABASE_URL`에 둡니다. 한쪽에 몰아 쓰면 컨테이너와 앱이 서로 다른
> DB를 가리킵니다.

| 환경변수 | 기본값 | 설명 |
|---|---:|---|
| `DATABASE_URL` | 로컬 컨테이너 | 실 OpenSQL은 OpenProxy 단일 엔드포인트 `postgresql://app@<vip>:6432/<pool_name>` (ADR-006). 앱은 여기에 없는 TCP keepalive 설정(`keepalives_idle=30`·`keepalives_interval=10`·`keepalives_count=3`·`tcp_user_timeout=60000`)을 기본값으로 채워, 죽은 연결을 약 60초 안에 감지합니다. 바꾸려면 DSN에 같은 키를 적습니다 — 적은 값이 이깁니다 (ADR-048) |
| `EMBEDDING_PROVIDER` | `fake` | `local` — `BAAI/bge-m3` · `fake` — 테스트용. 아래 「임베딩 프로바이더」 |
| `JOB_LEASE_SECONDS` | `60` | 잡 선점 lease. 워커가 처리 중 1/3마다 연장하고, 연장이 끊긴 잡(워커 사망·연결 끊김)은 이만큼 뒤에 회수됩니다. 스윕도 drain 중 이 주기로 돕니다 (ADR-050). 옛 `ZOMBIE_TIMEOUT_MINUTES`는 없어졌습니다 — 남아 있어도 무시됩니다 |
| `SESSION_LIFETIME_HOURS` | `24` | 서버 세션과 로그인 쿠키의 수명 |
| `SESSION_COOKIE_SECURE` | `false` | 로컬 HTTP에서는 `false`. HTTPS 상시 배포에서는 반드시 `true` |
| `MAX_UPLOAD_MB` | `50` | 업로드·원본 교체 한 건의 상한(십진 MB). 원본 한 판이 DB에 차지하는 크기의 상한이기도 하다 — 아래 「원본 파일 보관」 |

## 임베딩 프로바이더

**기본값 `fake`는 의미 없는 벡터를 만듭니다.** 이 상태에서도 업로드·검색이 동작하기 때문에
알아채기 어렵습니다. 검색 품질을 판단하거나 시연하려면 `local`로 바꿔야 합니다.

```bash
pip install -e ".[dev,local]"                              # sentence-transformers + torch (수 GB)
EMBEDDING_PROVIDER=local uvicorn openarchive.main:app --reload # API — 검색 질의를 임베딩한다
EMBEDDING_PROVIDER=local python -m openarchive.worker      # 워커 — 문서를 임베딩한다
```

**API·워커·MCP 서버는 각자 프로바이더를 생성하므로 세 프로세스에 같은 값을 주어야 합니다.**
값이 엇갈리면 질의 벡터와 문서 벡터가 다른 공간에 놓여, 에러 없이 검색 결과만 무의미해집니다.
모델은 API·워커가 **기동할 때** 내려받아 캐시하므로 최초 1회는 기동이 오래 걸리고, 대신 첫
검색·첫 업로드가 로딩을 기다리지 않습니다 (ADR-003 보강).

**최초 1회는 인터넷이 필요합니다** — BGE-M3 가중치 약 2GB를 Hugging Face에서 받아 `~/.cache/huggingface`에
둡니다. 그 뒤로는 외부 접속 없이 구동됩니다. 캐시가 있어도 기동 시 Hugging Face에 메타데이터를 확인하러
가므로, 폐쇄망에서는 `HF_HUB_OFFLINE=1`을 함께 줍니다(캐시가 있으면 `dim=1024`로 정상 로드 — 실측).

```bash
HF_HUB_OFFLINE=1 EMBEDDING_PROVIDER=local openarchive serve
```

## OCR 엔진 (tesseract)

이미지(PNG·JPG·JPEG)와 텍스트 레이어가 없는 스캔 PDF는 워커가 tesseract로 텍스트를 인식합니다
(`kor+eng`, ADR-052). tesseract는 pip 의존성이 아니라 **시스템 패키지**라 워커가 도는 호스트에 따로
설치합니다.

```bash
sudo dnf install tesseract tesseract-langpack-kor        # Rocky Linux 9 (AppStream, tesseract 4.1.1)
sudo apt install tesseract-ocr tesseract-ocr-kor         # Ubuntu
brew install tesseract tesseract-lang                    # macOS
```

설치 확인은 `tesseract --list-langs` 출력에 `kor`가 있는지 봅니다.

- **오프라인 설치가 가능합니다.** 한국어 모델은 패키지 안의 파일(`kor.traineddata`) 하나라 인식할 때
  인터넷이 필요 없습니다. 패키지를 미리 받아 옮겨 설치하면 됩니다.
- **엔진이 없어도 기동은 됩니다.** 이미지·스캔 PDF 업로드는 받아지고, 워커가 인식을 시도하다 실패해
  재시도 예산을 쓴 뒤 그 문서만 「텍스트 인식 실패」가 됩니다. 다른 형식은 영향이 없습니다.
- **대기와 실패는 `/admin/status`의 「텍스트 인식」 카드에서 봅니다** — 인식 대기 문서 수와 인식 실패
  문서 수. 인식 결과가 비었거나 500KB를 넘은 문서는 재시도 없이 바로 실패로 표시됩니다. 실패 문서는
  원본 교체나 `openarchive reextract`로 다시 돌립니다.
- 인식은 쪽당 수 초(맥 M2 Pro · tesseract 5.5 실측 약 3.3초)이고, 워커는 잡을 하나씩 처리하므로 긴
  스캔 문서를 인식하는 동안 다른 문서의 임베딩이 밀립니다. Rocky 9 패키지(tesseract 4.1.1)는 컨테이너
  실측에서 문자 오류율 3~5%, 쪽당 3.4~5.5초였습니다(arm64 — x86 호스트 시간은 다를 수 있습니다).

## 프로세스 구성

### `openarchive init`

도입할 DB를 준비 상태로 만듭니다 — **연결 확인 → capability 확인(PostgreSQL 버전·`vector`·
`pg_trgm`·CREATE 권한) → 마이그레이션 적용 → 준비 상태 보고**. 확인이 적용보다 먼저이므로,
확장이 없거나 권한이 모자라면 스키마를 건드리기 전에 무엇이 왜 필요한지 알려주고 멈춥니다.

```bash
openarchive init                                                         # 대화형 — DSN 한 줄만 입력
openarchive init --dsn "postgresql://app@<vip>:6432/<pool_name>" --yes   # 비대화형 — --dsn 필수
openarchive init --dsn "..." --schema                                    # 기존 DB 안 전용 스키마(= 접속 롤 이름)
```

DSN을 확인한 뒤 `~/.openarchive/.env`의 `DATABASE_URL` 줄만 갈아 끼웁니다. 다른 설정은 보존됩니다.
관리자 계정이 없으면 첫 관리자(`admin`, `--admin-username`으로 변경)를 만듭니다 — 아래 「인증과 계정」.

> **기존 데이터베이스를 덮어쓰지 않습니다.** `schema_migrations`가 없는데 OpenArchive가 쓰는
> 테이블 이름(`documents`·`users` 등)이 이미 있으면 아무것도 바꾸지 않고 중단합니다.
> 마이그레이션에 `ALTER TABLE documents`가 있어, 같은 이름의 다른 테이블 위에서 돌면 그 데이터가
> 손상되기 때문입니다 (ADR-039). 새 데이터베이스를 만들 수 없으면 `--schema`로 접속 롤 이름의 스키마에
> 설치합니다 — public은 건드리지 않고, 앱에 따로 설정할 것은 없습니다. 준비 절차는
> [OpenSQL 환경 구축 §10 ④](SETUP_OPENSQL.md#이미-쓰고-있는-opensql에-설치할-때--새-db와-새-풀).

**하지 않는 것**: API·워커·프론트 기동, DB 자동 탐색·설치, 문서 공급, 계정 생성. 이 명령을
건너뛰어도 API 서버가 startup에서 같은 마이그레이션을 적용하므로(ADR-012), init은 **필수가 아니라
사전 점검**입니다.

### `openarchive serve`

API 서버와 임베딩 워커를 함께 띄웁니다. **워커를 따로 켜는 것을 잊을 수 없게 하는 것**이
목적입니다 — 워커가 없으면 업로드는 성공하는데 검색에 잡히지 않고, 에러가 나지 않아 원인을
짐작하기 어렵습니다.

```bash
openarchive serve                              # 127.0.0.1:8000
openarchive serve --host 0.0.0.0 --port 9000
```

- 웹 화면·API·워커가 한 주소에서 나옵니다. 별도 프론트 서버가 필요 없습니다.
- Ctrl-C 한 번에 둘 다 멈춥니다. 워커는 **처리 중인 잡을 마치고** 종료합니다.
- `kill <pid>`·컨테이너 진입점·감독자의 stop처럼 **SIGTERM이 부모에게만 와도** 자식까지
  함께 내려갑니다. 정리 도중 신호가 한 번 더 와도 정리를 끝까지 마칩니다.
- 한쪽이 멈추면 나머지도 내리고 0이 아닌 코드로 끝납니다 — 반쪽만 도는 상태를 만들지 않습니다.
- **죽은 프로세스를 되살리지는 않습니다.** 배포 호스트에서는 systemd가 그 역할을 합니다
  (ADR-038 · `scripts/openarchive-worker.service`).

### 앱 이미지

`backend/Dockerfile`로 만드는 이미지입니다. 안에는 API·워커·웹 화면과 tesseract 한국어 모델이 있고
**DB는 없습니다** (ADR-039 개정 #95-e2). 실행 명령은 [README 「컨테이너로 실행」](../README.md#컨테이너로-실행)에 있습니다.

```bash
docker build -t openarchive backend/
```

| 항목 | 값 |
|---|---|
| 진입점 | 인자가 없으면 `openarchive init --yes --dsn "$DATABASE_URL"`을 실행하고, 이어서 `exec openarchive serve --host 0.0.0.0 --port 8000`. init이 실패하면 serve를 띄우지 않습니다. 인자를 주면 그 명령만 실행합니다 |
| 필수 환경변수 | `DATABASE_URL` — 없으면 아무것도 실행하지 않고 멈춥니다. 첫 기동에는 `ADMIN_PASSWORD`도 줍니다 |
| 볼륨 `/data` | `OPENARCHIVE_HOME`(설정)과 `HF_HOME`(BGE-M3 가중치 약 2.3GB). 볼륨이 없으면 컨테이너를 다시 만들 때마다 가중치를 다시 받습니다 |
| 기본값 | `EMBEDDING_PROVIDER=local` · 사용자 uid 1000 · 포트 8000 |
| 자원 | 메모리 5GB 이상 — API와 워커가 모델을 각자 올립니다(실측 약 4.2GB) |

전용 스키마(`--schema`)로 설치하려면 첫 기동 전에 한 번 명령으로 실행합니다. 그 뒤 진입점의 init은
롤 이름 스키마의 설치를 보고 그냥 지나갑니다(실측).

```bash
docker run --rm -e ADMIN_PASSWORD='change-me' openarchive \
  openarchive init --schema --yes --dsn "postgresql://…"
```

### `openarchive import` · `export` · `search`

셸에서 문서를 넣고, 빼고, 찾습니다. 셋 다 `--user`로 준 계정의 권한으로 동작합니다 — 넣은 문서의
소유자, 검색·내보내기의 열람 범위가 이 계정입니다. 계정이 없으면 아무것도 하지 않고 끝납니다.

```bash
openarchive import ./docs --user alice                        # 하위 폴더까지
openarchive import ./docs --user alice --tag 회의 --visibility private
openarchive export ./backup --user alice                      # 비어 있거나 없는 폴더
openarchive search "설치 절차" --user alice --tag 운영 -k 5
```

**import**
- 업로드와 같은 형식을 받고 같은 상한(`MAX_UPLOAD_MB`)을 적용합니다. 원본 파일도 업로드처럼 보관합니다.
  숨김 파일·폴더(`.obsidian`, `.git`)는 건너뛰고, 모르는 형식은 **지원하지 않는 형식**으로 셉니다.
- **frontmatter가 있는 마크다운**은 `title`·`tags`·`visibility`를 메타데이터로 읽고 본문만 문서 텍스트로
  넣습니다(원본 파일 없음). `--tag`는 더해지고, `--visibility`는 frontmatter에 없을 때만 씁니다.
- **같은 내용은 다시 넣지 않습니다** — 같은 소유자에게 같은 원본 파일(교체된 옛 판 포함)이나 같은 문서
  텍스트(원본이 있는 문서의 텍스트 포함)가 있으면 **이미 있음**으로 셉니다. 중간에 실패했으면 그대로 다시 실행하면 됩니다.
- 한 파일의 실패(추출 실패·빈 파일·상한 초과·잘못된 frontmatter)는 **실패**로 출력하고 계속합니다.
  실패가 하나라도 있으면 종료 코드가 1입니다.
- 임베딩과 관계 판정은 워커가 합니다. `openarchive serve`가 돌고 있어야 검색에 나타나고, 이미지·스캔은
  워커가 텍스트를 인식한 뒤 채워집니다. 많이 넣었다면 워커가 다 처리한 뒤 `rebuild-edges`를 한 번 돌립니다.

**export**
- `--user` **소유 문서만** 문서 텍스트 + frontmatter(`title`·`tags`·`visibility`) 마크다운으로 씁니다.
  남의 공개 문서를 빼는 이유는 다시 넣으면 넣은 사람의 소유가 되기 때문입니다.
- 원본 파일은 내보내지 않습니다. 텍스트 인식 중이거나 인식에 실패한 문서는 텍스트가 없어 건너뜁니다.
- 파일 이름은 제목에서 만들고, 겹치면 ` (2)`를 붙입니다. 이미 파일이 있는 폴더에는 쓰지 않습니다.
- 결과를 다른 설치에 `import`하면 문서 텍스트·태그·열람 범위가 그대로 돌아옵니다. 파일 문서는 원본 없는
  문서로 돌아오므로 형식이 `md`가 됩니다. 같은 설치에 다시 넣으면 전부 **이미 있음**입니다.

**search**
- 웹 검색과 같은 단일 SQL(`search_documents`)입니다. `--tag`(여러 번)·`--type`·`-k`(1~20)를 받습니다.
- 질의 임베딩은 `EMBEDDING_PROVIDER`를 따릅니다. **문서를 임베딩한 워커와 같은 값이어야** 합니다 —
  다르면 에러 없이 무의미한 결과가 나옵니다. 워커를 `local`로 돌렸다면 `EMBEDDING_PROVIDER=local
  openarchive search …`처럼 맞춥니다.

### `openarchive demo`

예제 문서 64건(가상 회사 다섯 부서의 사내 규정, `backend/openarchive/demo_corpus/`)을 넣어 검색·관계·군집을 바로
살펴봅니다. 코퍼스는 패키지에 실려 있어 pip 설치본에서도 이 한 줄로 됩니다.

```bash
openarchive demo --user admin              # 넣고 → 임베딩 대기 → 관계 재계산까지
openarchive demo --user admin --no-wait    # 넣기만
```

- `--user`는 필수입니다. 예제 문서의 소유자가 되고, 비공개 4건은 이 계정에만 보입니다(ADR-018) —
  로그인해서 볼 계정을 줍니다.
- 기본은 **워커가 전부 임베딩할 때까지 기다린 뒤 `rebuild-edges`를 한 번** 합니다(관계 잡을 걸고 워커가
  비울 때까지 기다림). 다른 터미널에서 `openarchive serve`가 돌고 있어야 하며, 두 대기는 각각
  `--timeout`(기본 600초) 안에 끝나지 않으면 종료 코드 1로 끝납니다 — 임베딩 대기에서 끝났다면 문서는
  들어가 있으니 워커를 띄워 처리한 뒤 `rebuild-edges`만 실행하면 되고, 관계 잡 대기에서 끝났다면 잡이
  이미 걸려 있어 워커가 돌면 저절로 맞춰집니다.
- 같은 계정에 같은 제목이 이미 있으면 건너뜁니다. 다시 실행해도 두 벌이 되지 않습니다.
- 측정용 `scripts/seed_demo.py`도 같은 적재 로직을 씁니다. 계정 없는 소유자(`seed`)와 `--reset`이 더 있습니다.

### `openarchive rebuild-edges`

모든 문서의 관계(`document_edges`)를 **전체 코퍼스 기준으로** 다시 계산합니다. **대량 적재 뒤 한 번**
실행합니다 — 관계를 만드는 트리거는 각 문서가 임베딩되는 시점까지 들어온 문서만 이웃 후보로 보므로,
먼저 올린 문서는 나중 문서와 이어질 기회가 없습니다 (ADR-029 결정 6).

```bash
openarchive rebuild-edges                # DATABASE_URL을 쓴다
openarchive rebuild-edges --dsn "postgresql://app@<vip>:6432/<pool_name>"
```

- **계산은 워커가 합니다.** 이 명령은 `ready` 문서마다 관계 잡(`embedding_jobs`의 `kind='edges'`)을 DB
  함수 `enqueue_all_edge_jobs()`로 걸고, 워커가 그 잡들을 비울 때까지 기다립니다. 관계를 쓰는 곳은
  워커 하나뿐이라 평소의 관계 잡과 겹쳐도 같은 문서를 두 곳이 동시에 고치지 않습니다 (ADR-029 결정 6 개정).
  그래서 `openarchive serve`가 돌고 있어야 끝납니다.
- 진행은 `done/total`로 출력됩니다. 30초 동안 처리되지 않으면 워커를 확인하라고 한 번 알립니다.
  `Ctrl+C`로 기다리기를 멈춰도 건 잡은 큐에 남아 워커가 처리합니다. 다시 실행하면 이미 대기 중인
  잡과 합쳐지므로 두 번 계산하지 않습니다.
- 요청 뒤에 새로 들어온 문서의 잡은 기다리지 않습니다 — 적재가 계속되는 중에도 명령은 끝납니다.
- `openarchive demo`(와 `scripts/seed_demo.py`)는 적재를 마친 뒤 이것을 자동으로 한 번 합니다.
- 적재 직후 관계가 보이지 않으면 이 명령부터 돌리기 전에 **워커가 도는지**와 `/admin/status`의
  관계 미반영 문서 수를 먼저 확인하세요 — 평소의 관계 잡이 아직 처리되지 않았거나 워커가 멈춘 것일 수 있습니다.
- **`error`로 격리된 관계 잡의 복구 경로**이기도 합니다. 다시 건 잡의 판정이 성공하면 워커가 그 문서의
  격리된 잡을 함께 마감해 관계 미반영 문서 수가 내려옵니다. 또 실패하면 격리된 채 남고, 명령은 격리된
  문서 수를 알리며 종료 코드 1로 끝납니다.

### `openarchive reextract`

보관된 최신 원본 파일에서 텍스트를 다시 추출합니다. **파서를 고치거나 올린 뒤** 기존 문서에 적용할 때
씁니다 (ADR-046).

```bash
openarchive reextract <문서 ID>          # 한 건
openarchive reextract --all              # 원본이 있는 문서 전부
openarchive reextract --all --dsn "postgresql://app@<vip>:6432/<pool_name>"
```

- 추출 결과가 현재 텍스트와 **다른 문서만** 새 텍스트 버전이 됩니다. 바뀐 문서마다 워커가 재임베딩하고,
  임베딩이 끝나면 관계 재계산이 뒤따릅니다 — 문서가 많으면 큐가 그만큼 밀립니다. 결과가 같은 문서는
  아무것도 쓰지 않습니다.
- 사람이 직접 고쳐 둔 추출 텍스트도 원본 기준으로 덮입니다. 덮인 내용은 텍스트 버전 이력에 남아
  화면에서 되돌릴 수 있습니다.
- `--all`은 대상 조회와 문서별 처리를 각각 커밋합니다. 조회 뒤 누가 편집한 문서, 추출에 실패하거나
  500KB를 넘는 문서, 그 사이 삭제된 문서는 건너뛰고 **실패**로 출력한 뒤 계속합니다. 마지막에
  `바뀜 · 같음 · 실패` 건수를 출력하고, 실패가 하나라도 있으면 종료 코드가 1입니다.
- 원본이 없는 문서(원본 보관 이전에 올린 문서, 텍스트로 공급한 문서)는 `--all`의 대상이 아니며,
  단건으로 지정하면 안내 후 종료 코드 1로 끝납니다. 원본을 붙이려면 문서 상세에서 「원본 파일 올리기」를 씁니다.
- 운영자 경로라 권한을 묻지 않습니다. 웹·REST의 `POST /api/documents/{id}/reextract`는 소유자만 쓸 수 있습니다.
- 원본이 이미지나 스캔 PDF인 문서는 그 자리에서 추출하지 않고 워커의 텍스트 인식으로 넘기며, 마지막에
  `텍스트 인식 대기 N건`으로 따로 출력합니다. 텍스트 인식 중인 문서는 건너뛰고 **실패**로 셉니다.

### 기동 순서

스키마를 준비하는 것은 `openarchive init`과 API 서버뿐입니다 (ADR-012·039). 워커나 MCP 서버를
먼저 띄우면 스키마가 없어 실패합니다.

### 워커 장애

워커 프로세스가 강제 종료되면 systemd 유닛이 되살리고, 방치된 잡은 lease(`JOB_LEASE_SECONDS`,
기본 60초)가 만료된 뒤 회수됩니다. 워커는 살아 있는데 DB 연결만 끊긴 경우(HA의 failover·
switchover)도 같은 경로입니다. 그 회수를 기다리는 동안에도 나머지 잡은 계속 처리되며,
워커를 반복적으로 죽이는 잡은 재시도 예산을 소진한 뒤 `error`로 격리되어 파이프라인을 막지
않습니다 (ADR-038).

워커가 멈춰 있으면 임베딩뿐 아니라 **관계 판정도 함께 멈춥니다** — 관계도 같은 큐의 잡이기
때문입니다. 점검은 `/admin/status`의 정합성 카드에서 두 수를 함께 봅니다: 원본과 청크 버전이
어긋난 문서 수, 그리고 관계가 아직 반영되지 않은 문서 수. 둘 다 평상시 0으로 수렴하며, 올라간
채로 내려오지 않으면 워커가 도는지부터 확인하세요. 관계 잡이 재시도를 소진해 `error`로 격리돼도
그 문서는 관계 미반영 수에 계속 세어집니다 — 격리했다고 어긋남을 숨기지 않습니다. 이때도
임베딩 상태 배지는 `ready` 그대로이고 검색은 정상 동작합니다. **격리된 잡은 워커가 다시 집지 않으므로
그 수는 저절로 내려오지 않습니다** — 위의 `openarchive rebuild-edges`가 복구 경로입니다.

## 원본 파일 보관

업로드한 원본 파일은 DB 안 `document_files` 테이블에 `bytea`로, 교체할 때마다 판을 하나씩 쌓아 보관합니다
(ADR-046). 이전 판을 지우는 기능과 보관 정책(개수·기간)은 아직 없어 **교체할수록 누적**됩니다.

원본이 차지하는 크기는 쿼리 하나로 봅니다.

```sql
SELECT pg_size_pretty(pg_total_relation_size('document_files'));
```

원본이 DB 안에 있으므로 DB를 백업하면 원본도 함께 들어가고, 그만큼 백업이 커집니다. HA 환경의 백업·PITR은
아래 「백업과 복원」을 보세요 (ADR-053).

`MAX_UPLOAD_MB`(기본 50)가 한 판의 상한입니다. 실 OpenSQL에서 OpenProxy를 거친 50MB 원본 저장이
시간 제한 안에 들어가는지는 아직 측정하지 않았습니다 — 큰 파일을 다루는 설치라면 먼저 확인하세요.

## 백업과 복원 (Barman)

HA 환경(`SETUP_OPENSQL.md` §16)은 Barman 전용 노드 `node4`(192.168.64.204)가 백업을 받습니다 (ADR-053, 구축은
`SETUP_OPENSQL.md` §17). node4는 PostgreSQL을 띄우지 않으므로 OpenSQL 라이선스를 쓰지 않습니다.

| 항목 | 값 |
|---|---|
| WAL 보관 | streaming — `pg_receivewal` + Patroni 영구 슬롯 `barman`. `archive_command`는 `/bin/true` 그대로 |
| 기준 백업 | 매일 전체(`backup_method = postgres`), 보존 `RECOVERY WINDOW OF 7 DAYS`, `minimum_redundancy = 1` |
| failover 추종 | Barman Agent(`barman-agent.service`, :8080) + 각 노드 Patroni `on_role_change` 콜백 |
| 실측 | 기준 백업 324MiB·12초 · 복원 시작→쓰기 가능 161~166초(`--get-wal`, 없이는 97초) · failover 뒤 추종 17~18초 |

백업은 HA를 대신하지 않습니다. failover는 노드가 죽어도 서비스를 잇는 장치이고, 백업은 잘못 지운 데이터·손상·클러스터
전체 상실에서 **과거 시점으로 되살리는** 장치입니다 — 복원하는 동안 서비스는 멈춥니다.

### 상태 확인 — 매일 보는 것

```bash
# node4에서. 종료 코드 0이 정상이다. FAILED가 하나라도 있으면 1
sudo -u barman -i barman check opensql
sudo -u barman -i barman list-backups opensql

# Primary에서. barman 슬롯이 active=t이고 지연이 작아야 한다
psql -c "SELECT active, pg_size_pretty(pg_current_wal_lsn() - restart_lsn) FROM pg_replication_slots WHERE slot_name = 'barman'"
```

- **Barman이 멈추면 Primary 디스크가 찹니다.** 슬롯이 WAL을 붙잡고 상한(`max_slot_wal_keep_size`)은 걸려 있지
  않습니다. 39초 정지에 슬롯 지연 122MB가 쌓였습니다. `barman check`가 `replication slot: FAILED`·`receive-wal
  running: FAILED`를 내면 node4의 `crond`(매분 `barman cron`)부터 확인하세요 — 다시 돌면 1분 안에 붙어 따라잡습니다.
- Agent는 콜백을 받을 때만 움직입니다. Agent가 꺼진 사이 failover가 나면 Barman이 옛 노드에 붙어 있으니
  `barman check`로 알아채고 `sudo -u barman -i barman config-switch opensql <새 Leader 멤버 이름>`을 실행합니다.
- 쓰기가 거의 없는 시간에 `barman backup --wait`는 마지막 세그먼트가 닫힐 때까지 기다립니다. `barman switch-wal
  opensql`로 닫거나 `--wait` 없이 받습니다.

### 시점 지정 — 복원 지점을 미리 찍어 두기

위험한 작업(대량 삭제·마이그레이션) 전에 이름 붙은 복원 지점을 남기면 시각보다 정확하게 돌아갈 수 있습니다.

```bash
# node4에서(barman 롤에 EXECUTE 권한이 있다). host는 지금 Leader
sudo -u barman -i /opt/opensql/bin/psql -h <Leader IP> -U barman -d postgres \
  -c "SELECT pg_create_restore_point('before_bulk_delete')"
```

### 복원 절차 — 격리 인스턴스로

복원은 운영 클러스터를 덮지 않고 **노드 하나에 별도 인스턴스**(다른 디렉터리·포트)로 띄웁니다. 그 노드의 라이선스로
뜹니다. 아래는 node2에 `/home/opensql/restore`로, 포트 5433에 띄우는 예입니다.

**준비물(한 번)**: 대상 노드에 `rsync`와 Barman 클라이언트(`install.sh barman` — `barman-wal-restore`가 들어 있다),
node4 `barman` → 대상 `opensql`과 대상 `opensql` → node4 `barman` 양방향 SSH 키. node2에는 rsync와 클라이언트가
설치돼 있고, 키는 복원할 때만 넣고 끝나면 지웁니다.

```bash
# 1. node4에서 복원 파일을 보낸다. 두 옵션 모두 빠뜨리면 안 된다
sudo -u barman -i barman recover \
  --remote-ssh-command "ssh opensql@192.168.64.202" \
  --target-tli latest --get-wal \
  [--target-name before_bulk_delete | --target-time "2026-10-04 11:35:00+09"] [--target-action promote] \
  opensql latest /home/opensql/restore
```

- **`--target-tli latest`** — 없으면 백업 시점 타임라인의 WAL만 복사됩니다. failover·switchover가 한 번이라도 있었으면
  `recover`는 성공하고 기동에서 "recovery target 도달 전에 복구 끝남"으로 죽습니다.
- **`--get-wal`** — 없으면 닫히지 않은 마지막 세그먼트(`.partial`)를 버립니다. 실측에서 끊기 직전까지 응답 성공한
  업로드 120건이 전부 사라졌습니다(`--get-wal`로는 0건).
- 대상 시점을 주지 않으면 받은 WAL의 끝까지 복원합니다.

```bash
# 2. 대상 노드에서 격리 설정을 덮어쓴다. 백업 안의 Patroni 설정이 운영 클러스터를 가리키기 때문이다
D=/home/opensql/restore
sudo -u opensql tee -a $D/postgresql.auto.conf <<'EOF'
port = 5433
listen_addresses = 'localhost'
cluster_name = 'restore'
primary_conninfo = ''
primary_slot_name = ''
archive_mode = off
hba_file = '/home/opensql/restore/pg_hba.conf'
ident_file = '/home/opensql/restore/pg_ident.conf'
cron.launch_active_jobs = off
restore_command = '/usr/local/bin/barman-wal-restore -P -U barman 192.168.64.204 opensql %f %p'
EOF

# 3. 기동한다. 라이선스 경로는 Patroni가 넘기던 것을 직접 준다
sudo -u opensql env OPENSQL_LICENSE_PATH=/home/opensql/license/license.xml LD_LIBRARY_PATH=/home/opensql/lib \
  /home/opensql/bin/pg_ctl -D $D -l $D/restore.log -w -t 900 start
sudo -u opensql /home/opensql/bin/psql -h /home/opensql/tmp -p 5433 -U postgres -c "SELECT pg_is_in_recovery()"   # f면 끝
```

- `primary_conninfo`·`primary_slot_name`을 비우지 않으면 보관 WAL을 다 쓴 뒤 운영 클러스터에 복제로 붙어 운영 슬롯을
  씁니다. `hba_file`은 운영 노드의 데이터 디렉터리를 가리키고 있습니다.
- `restore_command`를 덮어쓰는 이유: Barman은 자기 hostname(`node4`, 대상 `/etc/hosts`에 없음)과 PATH 밖 명령
  이름을 넣습니다 — 그대로 두면 "해당 명령어 없음"으로 기동이 실패합니다.

**4. 앱을 붙여 확인한다.** 복원본은 단독 PostgreSQL이라 OpenProxy 없이 직결합니다(복구 작업 전용 — ADR-006을 바꾸지
않는다). `listen_addresses = 'localhost'`이므로 SSH 터널로 붙습니다.

```bash
ssh -N -L 15433:127.0.0.1:5433 <대상 노드> &
DATABASE_URL=postgresql://openarchive:…@127.0.0.1:15433/openarchive openarchive serve --port 8011
```

복원 시점에 처리 중이던 잡은 lease가 만료돼 있어 워커가 "좀비 잡을 pending으로 회수"하고 다시 처리합니다(실측 30건 16초).
`/admin/status`의 대기·처리 중·정합성 카운터가 0이 되면 검색까지 정상입니다.

**5. 복원본을 어떻게 쓸지 정한다.** 필요한 문서만 내보내 운영에 다시 넣거나(`openarchive export`), 운영 클러스터 전체를
되돌리기로 했다면 Patroni 클러스터를 이 데이터로 다시 부트스트랩합니다 — 후자는 실측하지 않았습니다. 끝나면
`pg_ctl -D $D stop`, 디렉터리와 복원용 SSH 키를 지웁니다.

### 복원 재현 — `scripts/dr_restore.py`

위 절차가 데이터를 실제로 되살리는지 다시 확인하는 도구입니다. 운영 클러스터는 덮지 않고, 시험 계정의 `ha-<label>-`
문서만 만들고 대조합니다. 준비물은 「복원 절차」와 같고(`SETUP_OPENSQL.md` §17-6), 맥에서 앱(`openarchive serve`)이
`DATABASE_URL`(VIP)로 떠 있어야 합니다. SSH는 `DR_SSH`(예: `ssh -F notes/ha110/ssh_config`)로 줍니다.

```bash
export DATABASE_URL=… HA_API=http://127.0.0.1:8010/api HA_USER=… HA_PASSWORD=… DR_SSH="ssh -F …"
N=192.168.64.201,192.168.64.202,192.168.64.203
PY=backend/.venv/bin/python

# PITR — 복원 지점 뒤의 삭제·폐기가 되돌아가고 뒤 업로드는 없어야 한다
$PY scripts/ha_failover.py run dr1-a --nodes $N --load 60         # 원본 판 있는 문서 + 업로드 장부
$PY scripts/dr_restore.py mark dr1 --nodes $N                      # 토큰·공유·업로드 → S1 → 복원 지점 dr1_before → S2
$PY scripts/dr_restore.py break dr1                                # 문서 5 삭제·토큰 폐기·공유 삭제·업로드 10
$PY scripts/dr_restore.py restore dr1 pitr --target-name dr1_before
ssh -f -N -L 15433:127.0.0.1:5433 192.168.64.202                   # 복원본은 localhost에만 열린다
$PY scripts/dr_restore.py verify dr1 pitr --dsn "…@127.0.0.1:15433/openarchive" --ledger ha-runs/dr1-a.jsonl
DATABASE_URL="…@127.0.0.1:15433/openarchive" openarchive serve --port 8011 &   # 복원본에 앱을 붙이면
$PY scripts/dr_restore.py converge dr1 --dsn "…@127.0.0.1:15433/openarchive"    # 처리 중이던 잡이 회수된다

# 전체 복원 RPO — 부하 중 +60초에 Barman 수신을 끊고, 끊은 채로 받은 WAL 끝까지 복원한다
$PY scripts/ha_failover.py run dr1-rpo --nodes $N --load 100 --inject-at 60 \
    --inject "$PWD/backend/.venv/bin/python $PWD/scripts/dr_restore.py freeze"
$PY scripts/dr_restore.py restore dr1 full                         # thaw보다 먼저 — 반대면 끊은 뒤 WAL도 받는다
$PY scripts/dr_restore.py rpo ha-runs/dr1-rpo.jsonl --dsn "…@127.0.0.1:15433/openarchive"
$PY scripts/dr_restore.py thaw                                     # crond 재개 → 다음 분 경계에 receive-wal
$PY scripts/dr_restore.py drop                                     # 복원 인스턴스 정지·디렉터리 삭제
```

- `restore`는 늘 `--target-tli latest --get-wal`로 복원하고, 대상 지점이 있으면 `--target-action promote`를 붙입니다.
  격리 설정(「복원 절차」 2번)을 덧붙여 띄우고, 쓰기 가능이 될 때까지의 시간을 `ha-runs/dr-<label>.json`에 남깁니다.
  대상 디렉터리는 이름이 `restore`로 시작해야 합니다 — 복원 전에 지우기 때문입니다.
- `verify`·`rpo`·`converge`는 위반이 있으면 종료 코드 1입니다. `rpo`는 끊기 전에 응답 성공한 업로드의 유실·내용 불일치와,
  끊은 뒤 업로드가 복원본에 있는지(= 끊기가 안 걸렸다)를 봅니다.
- 끝나면 시험 계정의 문서를 지우고 계정을 삭제한 뒤, 복원용 SSH 키를 지웁니다.

**재실측 (2026-10-04 13시, 이 도구)**

| 회차 | 결과 |
|---|---|
| PITR (`--target-name`) | 복원본 = 복원 지점: 문서 150(삭제 5 되살아남·뒤 업로드 10 없음)·개인·공유 토큰 2·공유 1·부여 2. 장부 120건 유실·불일치·원본 판·버전 이동 0. 재생이 복원 지점 `1/F111888` 바로 뒤 `1/F111938`에서 끝남. 쓰기 가능 161초(복사 49초). 복원본 워커가 좀비 잡 1건 회수, 미완료 30건 16초에 0 |
| 전체 복원 (`--get-wal`) | 끊기 전 응답 성공 120건 유실 0·불일치 0, 끊는 중 15건 중 13건 있음, 끊은 뒤 65건 없음. 마지막 생존 업로드 → 끊기 0.09초. 쓰기 가능 166초 |

`--get-wal`은 재생 중 WAL을 세그먼트마다 SSH로 가져와 쓰기 가능까지 걸리는 시간이 늘어납니다(97초 → 161~166초).
그 대가로 닫히지 않은 마지막 세그먼트까지 되살립니다.

## 문서 생성 멱등키

`POST /api/documents`·`/api/documents/text`에 붙은 `Idempotency-Key`는 `idempotency_keys` 테이블에
문서와 함께 기록됩니다(ADR-047). **24시간**이 지난 키는 워커가 좀비 스윕과 같은 주기에 지우므로,
워커가 멈춰 있으면 정리도 멈춥니다(재시도 판정에는 영향이 없고 테이블만 자랍니다). 행 하나는 키와 해시
정도라 작습니다.

```sql
SELECT count(*), min(created_at) FROM idempotency_keys;
```

`min(created_at)`이 24시간보다 한참 전이면 워커가 도는지 확인하세요.

## 인증과 계정

첫 관리자는 `openarchive init`이 만듭니다. 관리자가 하나도 없을 때만 비밀번호를 묻고, 이미 있으면
건너뜁니다. 입력이 없는 환경(스크립트·컨테이너)은 `ADMIN_PASSWORD`로 줍니다 — 비밀번호를 인자로 받지
않는 것은 셸 이력과 `ps`에 평문이 남기 때문입니다. 비밀번호가 없으면 스키마만 적용하고 계정은 만들지
않습니다.

```bash
ADMIN_PASSWORD='<초기 비밀번호>' openarchive init --yes --dsn "..."    # 첫 관리자까지
ADMIN_PASSWORD='<비밀번호>' openarchive create-user alice [--admin]    # 셸에서 계정 추가
```

웹의 "첫 가입자가 관리자" 방식은 두지 않습니다. 설치 직후 URL에 먼저 닿은 사람이 관리자를 차지하기
때문입니다 (ADR-028).

이후 관리자는 `/admin/users`에서 일반 사용자나 다른 관리자를 발급합니다. **관리자 권한은 계정 관리
전용**이며 다른 사용자의 private 문서를 열람하게 하지는 않습니다.

각 사용자는 `/settings`에서 자기 비밀번호를 바꾸고 API 토큰을 발급·폐기합니다. 비밀번호를 바꾸면
그 계정으로 열려 있던 모든 기기의 로그인이 끊기고, 발급한 API 토큰은 영향을 받지 않습니다 (ADR-040).

**비밀번호를 잊어 로그인조차 못 하는 계정**은 운영자가 서버에서 재설정합니다. 관리 화면에는 남의
비밀번호를 바꾸는 경로를 두지 않습니다 — 관리자가 남의 계정을 탈취해 그 사람의 private 문서를
읽게 되면 "관리자 권한은 계정 관리 전용"이라는 경계가 무너지기 때문입니다.

```bash
cd backend && source .venv/bin/activate
openarchive reset-password alice     # 새 비밀번호는 화면에 남지 않게 입력받는다
```

재설정하면 그 계정의 로그인 세션이 모두 끊깁니다. 발급된 API 토큰은 그대로 유효하므로, 자격증명까지
갈아야 하면 다시 로그인해 `/settings`에서 폐기합니다.

### API 토큰

기본 scope는 `read`이며 문서 공급에는 `read_write`가 필요합니다. 원문 토큰은 발급 응답에만 나오고
`GET /api/auth/tokens` 목록에는 다시 나타나지 않습니다. 발급·목록·폐기와 `/api/admin/*`는 세션
전용입니다 — 토큰이 토큰을 발급하면 폐기 뒤에도 자격증명을 스스로 재생할 수 있기 때문입니다
(ADR-034). `POST /api/auth/tokens`를 직접 호출해도 됩니다.

`examples/ingest_text.py`의 실제 서버 완주는 CI가 확인하지 않으므로 API·워커·DB를 함께 기동한
환경에서 실행합니다.

### MCP 서버

MCP 서버는 HTTP를 거치지 않고 서비스를 직접 호출하므로 API 인증 경계와 무관하며, 열람 범위는
`MCP_USER_ID`가 정합니다. `DATABASE_URL`·`EMBEDDING_PROVIDER`·`MCP_USER_ID`를 MCP 프로세스에
함께 전달해야 하고, MCP 서버는 마이그레이션을 실행하지 않으므로 스키마가 적용된 상태여야
합니다 (ADR-012·036).

> ⚠️ **`MCP_USER_ID`는 실존 계정인지 검증되지 않습니다.** 미설정·빈 값·공백은 거부되지만, 그
> 검사를 통과한 이름은 `users` 테이블에 없어도 그대로 문서 소유자가 됩니다 (ADR-036). 실제
> 계정명과 정확히 같게 적어야 `create_document`로 만든 문서가 Web UI에서 자기 문서로 보입니다.
> 생략하면 public 문서 읽기만 가능하고 `create_document`는 거부됩니다.

## 실 OpenSQL에서만 검증되는 것

로컬 `pgvector/pgvector:pg17` 컨테이너로 완주되는 범위와 실 OpenSQL 환경이 필요한 범위는
다음과 같습니다 (ADR-007).

| 라이선스 없이 검증할 수 있는 범위 | 실 OpenSQL 환경이 필요한 검증 |
|---|---|
| 자동 임베딩 파이프라인 전 구간 — 트리거·아웃박스·`SKIP LOCKED`·청킹·임베딩 | OpenProxy 경유 세션 동작 (ADR-009) |
| 하이브리드 검색 · 관계 그래프 · 태그 추천 · 군집 | 읽기/쓰기 분리와 복제 지연 (ADR-010) |
| 텍스트 버전·되돌리기, 권한 모델, API 토큰 | Patroni 리더 선출·승격 |
| Web UI · REST API · MCP 서버 전체 | 장애 복구 데모 (`scripts/demo_recovery.sh`) |
| `bash scripts/check.sh` 전체 통과 | 라이선스·번들 확장 실동작 |

### OpenProxy 풀이 바라보는 데이터베이스

설치기는 `opensql` 데이터베이스를 만들어 놓고 정작 풀은 관리용 `postgres`를 바라보게 설정합니다.
클라이언트는 DSN에 **풀 이름**을 적으므로 실제 저장 위치가 드러나지 않아, 그대로 두면
마이그레이션과 문서가 `postgres`에 쌓입니다. 교정 절차는 [OpenSQL 환경 구축](SETUP_OPENSQL.md)의
§10 「풀이 바라보는 데이터베이스를 교정한다」에 있습니다.

## 복구 데모

DB 프로세스 장애에서의 자동 복구를 단일 타임라인으로 확인합니다. 마이그레이션이 적용된
**실 OpenSQL VM**과 `backend/.venv`의 개발 의존성이 필요하며, 로컬 Docker DB에서는 실행할 수
없습니다. 스크립트가 API와 워커를 직접 띄우므로 별도로 실행해 둘 필요는 없습니다.

```bash
# 기본값: OPENSQL_HOST=192.168.64.4, OPENSQL_SSH=$OPENSQL_HOST,
# PATRONI_URL=http://$OPENSQL_HOST:8008, PATRONI_LOG=/home/opensql/logs/patroni.log, API_PORT=18000
OPENSQL_HOST=<vm-ip> \
OPENSQL_SSH=<ssh-host> \
DATABASE_URL="postgresql://postgres:pg_password@<vm-ip>:6432/opensql" \
PATRONI_URL="http://<vm-ip>:8008" \
PATRONI_LOG="/home/opensql/logs/patroni.log" \
API_PORT=18000 \
bash scripts/demo_recovery.sh
```

SSH 공개키 인증과 원격 호스트의 비밀번호 없는 `sudo`가 필요합니다. 데모는 postmaster 부모
프로세스에 `SIGKILL`을 한 번 보내고 Patroni의 자동 재기동, 앱 연결 예외와 재접속, 미처리 잡 재개,
정합성 수렴을 확인합니다.

**이 데모가 검증하는 것은 DB 프로세스 장애 자동 복구와 애플리케이션의 재연결·잡 재개·정합성
수렴입니다.** 노드 사망·승격·VIP 이동은 3노드 클러스터에서 `scripts/ha_failover.py`로 따로
검증합니다 — 구성과 실행법, 결과는 [OpenSQL 환경 구축](SETUP_OPENSQL.md) §16에 있습니다
(ADR-020 2026-09-28 개정).

### 측정 결과를 인용할 때의 복제 구성

2026-09-27~28의 3노드 장애 결과와 유실 0은 **동기 standby 1대** 구성에서 관측했다.
2026-09-30에 OpenProxy 1.1.3의 `sync_standby` 호환 문제로 **비동기 복제**로 되돌렸다.
비동기 복제에서는 성공 응답한 쓰기도 replica에 전달되기 전에 Primary를 잃으면 유실될 수 있다.
작업 큐의 재개는 복구된 DB에 남아 있는 커밋을 대상으로 하며, 복제되지 않은 커밋을 재생하지 않는다.

따라서 과거 유실 0을 현재 구성의 무손실 보장으로 인용하지 않는다. 현재 구성 재검증에는 실행
커밋·Patroni 복제 설정·노드 상태를 함께 기록하고, DB 밖에 남긴 업로드 응답 장부를 복구 후 문서와
대조한다. `scripts/ha_failover.py`의 노드별 수렴·장부 대조 결과와 실제 승격 이력을 함께 판정한다.
