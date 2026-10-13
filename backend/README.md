# OpenArchive

**조직의 OpenSQL에 설치하는 AI 문서관리 플랫폼**입니다. 문서를 올리면 텍스트 추출 → 청킹 →
임베딩 → 벡터 저장 → 문서 관계 생성이 자동으로 일어나고, 정형 필터(태그·유형·권한)와 벡터
유사도와 문서 관계를 하나의 SQL로 결합해 검색합니다. 사람은 웹 화면으로, 프로그램은 REST API로,
AI 에이전트는 MCP로 같은 문서와 같은 권한 규칙 위에서 접근합니다.

## 설치

Python 3.12+와 [Tmax OpenSQL](https://docs.tibero.com/tmaxopensql/overview)(PostgreSQL 17 +
pgvector)이 필요합니다. DSN만 자기 환경으로 바꿉니다 — OpenProxy 경유라면 데이터베이스 자리에
풀 이름을 적습니다.

```bash
pipx install "openarchive-server[local]"
openarchive init --dsn "postgresql://app:secret@<OpenProxy 호스트>:6432/<풀 이름>"
EMBEDDING_PROVIDER=local openarchive serve
```

`init`은 연결·확장·권한을 점검하고 스키마를 적용한 뒤 첫 관리자 `admin`의 비밀번호를 묻습니다.
브라우저에서 http://localhost:8000 을 열어 로그인합니다. 예제 문서로 먼저 둘러보려면
`openarchive demo --user admin`을 실행합니다.

- `[local]`은 BGE-M3 임베딩 모델입니다(torch 포함, 수 GB). 첫 `serve`에 가중치를 내려받습니다.
- 이미지·스캔 PDF의 텍스트 인식에는 시스템 패키지 tesseract와 한국어 모델이 필요합니다.
- `vector` 확장은 슈퍼유저만 만들 수 있습니다 — 앱 전용 롤로 설치하면 DBA가 미리 만들어 둡니다.

## 명령

| 명령 | 하는 일 |
|---|---|
| `openarchive init` | 점검 → 스키마 적용 → 첫 관리자 생성 |
| `openarchive serve` | API + 임베딩 워커 + 웹 화면 |
| `openarchive import <폴더> --user <계정>` · `export <폴더> --user <계정>` · `search "…" --user <계정>` · `ask "…" --user <계정>` | 셸에서 문서를 넣고, 빼고, 찾고, 근거로 답합니다(`ask`는 `ANSWER_PROVIDER`가 켜져 있어야 함) |
| `openarchive demo --user <계정>` | 예제 문서를 넣어 봅니다 |
| `openarchive create-user` · `reset-password` | 계정 추가 · 잊은 비밀번호 재설정 |
| `openarchive rebuild-edges` · `rebuild-previews` · `reextract <문서 ID>\|--all` | 관계 전체 재계산 · 미리보기 변환 다시 걸기 · 보관된 원본에서 텍스트 다시 추출 |

위 명령은 서버 셸의 운영자 도구로 DB(`DATABASE_URL` 또는 `--dsn`)에 직접 붙습니다. 사용자는 웹에서 발급한
API 토큰으로 REST에만 붙는 사용자 명령을 씁니다 — DB 접속 정보는 쓰지 않습니다.

| 사용자 명령 | 하는 일 |
|---|---|
| `openarchive login --url <서버> --token <토큰>` · `whoami` | 로그인 · 로그인한 사용자와 토큰 범위 확인 |
| `openarchive doc list` · `show` · `download` · `upload` · `edit` · `restore` · `tag` · `delete` · `trash` | 문서 목록·텍스트 보기·원본 내려받기·올리기·텍스트 바꾸기·버전 되돌리기·태그·휴지통 |
| `openarchive search "…"` · `ask "…"` (`--user` 없이) | 로그인한 토큰의 열람 범위로 찾고 답합니다 |

## 문서

- [README — 기능·아키텍처·사용 방법](https://github.com/jeongeundev/OpenArchive#readme)
- [운영 가이드](https://github.com/jeongeundev/OpenArchive/blob/main/docs/OPERATIONS.md)
- [설계 결정(ADR)](https://github.com/jeongeundev/OpenArchive/blob/main/docs/ADR.md)

MIT 라이선스.

본 제품은 한컴의 HWP 문서 파일(.hwp) 공개 문서를 참고하여 개발하였습니다.
