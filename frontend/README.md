# OpenArchive Web UI

[OpenArchive](../README.md)의 Web UI입니다. Next.js(App Router) · TypeScript(strict) ·
Tailwind CSS로 만들어졌고, 문서 목록·상세·검색·주제 덩어리·진단과 관리 화면을 제공합니다.

Web UI는 여러 소비 인터페이스 중 하나입니다 — REST API·MCP 서버와 같은 서비스 계층을
소비하며, 어느 쪽도 다른 쪽의 축소판이 아닙니다 ([ADR-031](../docs/ADR.md)).

## 실행

**API 서버를 먼저 띄워야 합니다.** 이 앱은 `/api/*`를 백엔드로 프록시할 뿐이고
(`next.config.ts`), 스키마 마이그레이션은 API startup에서만 실행됩니다
([ADR-012](../docs/ADR.md)). DB·백엔드·워커를 포함한 전체 기동 순서는
[루트 README 「빠른 시작」](../README.md#빠른-시작)에 있습니다.

```bash
npm install
npm run dev        # http://localhost:3000
```

자체 가입이 없으므로 첫 로그인 전에 관리자 계정을 한 번 만들어야 합니다 — 루트 README의
`scripts/create_admin.py` 단계입니다.

| 환경변수 | 기본값 | 설명 |
|---|---:|---|
| `BACKEND_URL` | `http://localhost:8000` | `/api/*` 요청을 넘길 백엔드 주소 |

## 검증

```bash
npm run lint
npm run test       # vitest
npm run build
```

저장소 전체 검증은 루트에서 `bash scripts/check.sh`입니다 (백엔드 lint·test까지 함께 돕니다).

## 더 읽을 것

- [UI Guide](../docs/UI_GUIDE.md) — 디자인 원칙과 화면 구성
- [Architecture](../docs/ARCHITECTURE.md) — 프론트엔드 패턴과 상태 관리
- [Contributing](../CONTRIBUTING.md) — 브랜치·커밋 컨벤션
