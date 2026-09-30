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
| `openarchive import <폴더>` · `export <폴더>` · `search "…"` | 셸에서 문서를 넣고, 빼고, 찾습니다 |
| `openarchive create-user` · `reset-password` | 계정 추가 · 잊은 비밀번호 재설정 |

## 문서

- [README — 기능·아키텍처·사용 방법](https://github.com/jeongeundev/OpenArchive#readme)
- [운영 가이드](https://github.com/jeongeundev/OpenArchive/blob/main/docs/OPERATIONS.md)
- [설계 결정(ADR)](https://github.com/jeongeundev/OpenArchive/blob/main/docs/ADR.md)

MIT 라이선스.
