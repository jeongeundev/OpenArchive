# 서드파티 고지 (Third-Party Notices)

OpenArchive는 MIT 라이선스(`LICENSE`)로 배포된다. 이 문서는 OpenArchive가 의존하거나 함께 쓰는 서드파티
구성 요소와 그 라이선스를 적는다. 버전은 2026-10-12 기준 개발 환경에 설치된 것이며, 설치 시점에 따라 달라질 수 있다.

## 1. 배포물에 함께 들어가는 것

`backend/openarchive/static/`(정적 빌드한 웹 화면, ADR-041)에 번들되어 저장소·wheel과 함께 배포된다. 라이선스 본문은
`backend/licenses/`에 있고 wheel에도 함께 실린다.

| 구성 요소 | 버전 | 라이선스 | 본문(`backend/licenses/`) |
|---|---|---|---|
| Next.js(클라이언트 런타임) | 16.3.0 | MIT | `next-license.txt` |
| React · React DOM · scheduler | 19.2.8 · 19.2.8 · 0.27.0 | MIT | `react-license.txt` · `react-dom-license.txt` · `scheduler-license.txt` |
| JSZip | 3.10.1 | MIT 또는 GPL-3.0-or-later 중 선택 — **OpenArchive는 MIT를 택한다** | `jszip-license.txt` |
| pako (JSZip 의존) | 1.0.11 | MIT AND Zlib | `pako-license.txt` |
| readable-stream (JSZip 의존) | 2.3.8 | MIT | `readable-stream-license.txt` |
| lie · immediate · setimmediate (JSZip 의존) | 3.3.0 · 3.0.6 · 1.0.5 | MIT | `lie-license.txt` · `immediate-license.txt` · `setimmediate-license.txt` |
| inherits (readable-stream 의존) | 2.0.4 | ISC | `inherits-license.txt` |
| safe-buffer · string_decoder · core-util-is · util-deprecate · process-nextick-args · isarray (readable-stream 의존) | 5.1.2 · 1.1.1 · 1.0.3 · 1.0.2 · 2.0.1 · 1.0.0 | MIT | `safe-buffer-license.txt` · `string_decoder-license.txt` · `core-util-is-license.txt` · `util-deprecate-license.txt` · `process-nextick-args-license.txt` · `isarray-1.0.0-readme-license.txt`(패키지에 LICENSE 파일이 없어 README의 라이선스 절) |
| Next.js가 넣는 브라우저 폴리필 `buffer` (+ base64-js · ieee754) | Next 16.3.0 동봉판 | MIT (+ MIT · BSD-3-Clause) | `next-compiled-buffer-license.txt` · `next-compiled-buffer-base64-js-license.txt` · `next-compiled-buffer-ieee754-license.txt` |
| Next.js가 넣는 폴리필 `events` · `process` · `stream-browserify` | Next 16.3.0 동봉판 | MIT | `next-compiled-events-license.txt` · `next-compiled-process-license.txt` · `next-compiled-stream-browserify-license.txt` |
| Next.js가 넣는 구형 브라우저 폴리필(`@next/polyfill-nomodule`): core-js · whatwg-fetch · object-assign | 3.38.1 · 3.0.0 · 4.1.1 | MIT | `core-js-license.txt` · `whatwg-fetch-license.txt` · `object-assign-license.txt` |
| @swc/helpers (Next.js 컴파일 결과가 쓰는 모듈 도우미) | 0.5.15 | Apache-2.0 | `swc-helpers-license.txt` |
| Geist · Geist Mono 글꼴(`geist` 패키지) | 1.7.2 | SIL Open Font License 1.1 | `geist-OFL-1.1.txt` |

**확인 방법** — 목록은 `frontend/package-lock.json`의 비개발 의존성 트리(`npm ls --omit=dev --all`)와 정적 청크
(`backend/openarchive/static/_next/static/chunks/*.js`)에 남은 식별 문자열을 대조해 정했다(예: `pako deflate`,
`_readableState`, `kMaxLength`, `unstable_scheduleCallback`). `noModule`로 실리는 폴리필 청크도 대상이다 — 그 묶음의 구성은
Next.js 저장소 `packages/next-polyfill-nomodule`(16.3.0)의 `package.json`·`src/index.js`로 확인했다. 빌드 도구(PostCSS·
sharp·caniuse-lite 등)는 번들에 들어가지 않는다. 프론트 의존성을 바꾸면 같은 방법으로 이 표를 갱신한다 —
`backend/tests/test_third_party_notice.py`가 식별 문자열과 이 표의 대응을 확인한다.

## 2. 설치 시 받는 Python 패키지 (`backend/pyproject.toml`)

저장소에 포함하지 않으며 수정하지 않고 import해 쓴다.

| 패키지 | 라이선스 | 비고 |
|---|---|---|
| FastAPI · Pydantic · pydantic-settings | MIT | |
| Uvicorn | BSD-3-Clause | |
| psycopg · psycopg-binary · psycopg-pool | **LGPL-3.0-only** | 수정 없이 동적으로 import한다. 사용자는 설치된 psycopg를 다른 버전으로 바꿔 쓸 수 있다 |
| mcp (Python SDK) | MIT | |
| httpx | BSD-3-Clause | |
| pypdf | BSD-3-Clause | |
| pypdfium2 | BSD-3-Clause, Apache-2.0 (+ 번들한 PDFium과 그 의존성의 라이선스) | PDFium 바이너리에는 FreeType·libjpeg-turbo·OpenJPEG·lcms2·ICU 등이 함께 들어 있다 — pypdfium2 배포물의 라이선스 고지를 따른다 |
| pytesseract | Apache-2.0 | |
| Pillow | MIT-CMU | |
| python-docx · openpyxl · python-pptx · PyYAML | MIT | |
| olefile | BSD-2-Clause | HWP 5.0 읽기. AGPL-3.0인 pyhwp는 쓰지 않는다 |
| python-multipart | Apache-2.0 | |
| networkx | BSD-3-Clause | |
| sentence-transformers (`[local]` 선택 설치) | Apache-2.0 | PyTorch(`Apache-2.0 AND … AND BSD-3-Clause AND MIT` 복합 — 패키지 메타데이터 참조)·Transformers(Apache-2.0)를 함께 설치한다 |

## 3. 프로그램 밖에서 호출하는 도구 (별도 설치, 저장소에 포함하지 않음)

| 도구 | 라이선스 | 쓰임 |
|---|---|---|
| Tesseract OCR 5 + `kor`·`eng` 학습 데이터 | Apache-2.0 | 이미지·스캔 PDF 텍스트 인식(ADR-052). 별도 프로세스로 실행 |
| LibreOffice | MPL-2.0 | 오피스 원본 미리보기용 PDF 변환(ADR-058). 별도 프로세스로 실행 |
| rhwp | MIT | 한글 원본 미리보기용 변환(ADR-058). 별도 프로세스로 실행 |
| bubblewrap (`bwrap`) | LGPL-2.0-or-later | 미리보기 변환을 격리해 실행(ADR-058). 별도 프로세스로 실행 |
| fontconfig (`fc-list`) | fontconfig 라이선스(MIT 계열 허용형) | 미리보기 변환 전 한글 글꼴 확인. 별도 프로세스로 실행 |
| Noto Sans CJK · Noto Serif CJK 글꼴 | SIL Open Font License 1.1 | 미리보기 변환의 한글 글꼴(OS 패키지로 설치) |
| Ollama | MIT | 근거 기반 답변의 로컬 모델 서버(선택, 기본 꺼짐, ADR-043). HTTP로 호출 |

## 4. 모델 (설치 뒤 내려받음, 저장소에 포함하지 않음)

| 모델 | 라이선스 | 쓰임 |
|---|---|---|
| BAAI/bge-m3 | MIT | 임베딩(1024차원, ADR-003) |
| Qwen3-8B (`qwen3:8b`) | Apache-2.0 | 근거 기반 답변(선택, 기본 꺼짐) |

## 5. 데이터베이스

Tmax OpenSQL(PostgreSQL 17.8 · pgvector · OpenHA · OpenProxy)은 운영 환경에 따로 설치하는 DBMS이며 이 저장소에 포함하지 않는다.
일상 개발용 컨테이너 이미지 `pgvector/pgvector:pg17`(PostgreSQL License · pgvector PostgreSQL License)도 같다.

## 6. 테스트 픽스처

`backend/tests/fixtures/`의 정책브리핑 보도자료 파생 파일은 「텍스트에 한하여 공공누리 제1유형(출처표시)」으로 이용한다.
출처와 가공 내용은 `backend/tests/fixtures/SOURCE.md`에 있다.

## 7. 한글 문서 형식

본 제품은 한컴의 HWP 문서 파일(.hwp) 공개 문서를 참고하여 개발하였습니다.
