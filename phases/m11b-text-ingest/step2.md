# Step 2: ingest-client-example

## 배경 — "HTTP만으로 공급한다"를 무엇이 증명하는가

`docs/ROADMAP.md` 1단계(프로그래매틱 플랫폼)의 성공 기준은 이 문장이다: *"외부 프로세스가
`backend` 패키지를 import하지 않고 **HTTP만으로** 문서를 공급하며, 파이프라인(임베딩·관계·
링크)이 웹 업로드와 동일하게 동작한다."*

뒷절반(파생 동등성)은 step 1의 테스트가 단언했다. 앞절반 — **공급자가 우리 코드에 의존하지
않는다** — 은 아직 아무것도 증명하지 않는다. `pytest`의 `TestClient`는 정의상 `app`을
import하기 때문이다.

이 step은 그 절반을 산출물로 만든다: **표준 라이브러리만으로 로그인하고 문서를 공급하는
독립 클라이언트 예제**와, 그것이 계속 독립적임을 지키는 테스트.

> 알려진 한계: 이 예제가 **실제로 도는지**는 pytest가 확인하지 않는다(실서버 기동이 필요하다).
> pytest가 지키는 것은 ① 의존성 독립 ② 문법 유효성 ③ 예제가 부르는 경로가 실제 등록된
> 라우트라는 것 세 가지다. 이 한계를 축소해 적지 마라 — step 5의 ADR에도 그대로 기록한다.

## 이전 step에서 만들어진 것

- **step 0** — `backend/app/services/documents.py`에 `create_text_document` 신설. 직접 공급
  문서는 `filename`이 NULL이다
- **step 1** — `POST /api/documents/text` 추가. 요청 본문은
  `{title, content, content_type("txt"|"md", 기본 "md"), tags?, visibility?}`이고 응답은
  `DocumentSummary`(201). 인증은 세션 쿠키(`openarchive_session`)를 요구한다

정확한 요청/응답 형태는 `backend/app/api/schemas.py`의 `CreateTextDocumentRequest`와
`backend/app/api/documents.py`를 **직접 읽어 확인하라.** 위 요약과 코드가 다르면 코드가 맞다.

## 읽어야 할 파일

- `docs/ROADMAP.md` 「단계적 발전 경로」 1단계 — 이 step이 충족하는 성공 기준의 원문
- `docs/PRD.md` §4(경계 — Core와 Interface) — 공급자 쪽 N을 증명하는 일의 위치
- `backend/app/api/documents.py`·`backend/app/api/schemas.py` — step 1 산출물
- `backend/app/api/auth.py` — 로그인 요청 형태와 세션 쿠키 이름
- `backend/app/api/deps.py` — `SESSION_COOKIE` 상수
- `backend/tests/test_architecture.py` — **이 step이 확장할 파일 전체.** 정적 검사만 하는
  파일이며 앱을 import하지 않는다. 그 성격을 유지하라
- `backend/tests/test_seed.py` 첫 10줄 — 저장소 루트 밖 모듈을 테스트에서 import하는 관례
  (`sys.path.insert(0, str(ROOT))`)
- `scripts/seed_demo.py` — 저장소의 독립 실행 스크립트 관례(argparse·`if __name__`·docstring)
- `scripts/check.sh` — 이 step에서 한 줄 고친다

## 작업

### 1) 테스트를 먼저 쓴다

**`backend/tests/test_architecture.py`에 추가** (정적 검사 두 개):

- `examples/` 아래 모든 `.py`가 **`app`·`backend`·`mcp_server`를 import하지 않는다.**
  기존 `test_services_do_not_import_http_frameworks`처럼 `ast`로 import 노드를 모아 검사하라.
  문자열 `grep`으로 하지 마라 — 주석이나 문서 문자열에 걸려 오탐이 난다
- `examples/` 아래 모든 `.py`가 **표준 라이브러리만 import한다.** `sys.stdlib_module_names`
  (Python 3.10+)로 판정한다. 서드파티(`requests`·`httpx` 등)가 들어오면 실패해야 한다

**`backend/tests/test_documents_api.py`에 추가** (경로 대조 한 개):

- 예제가 부르는 경로 상수가 **실제 등록된 라우트**다. 예제 모듈을 import해 상수를 읽고
  `app.routes`와 대조한다. 이 테스트가 있어야 API가 바뀌었을 때 예제가 낡은 채로 남지 않는다

### 2) `examples/ingest_text.py`를 만든다

저장소 루트에 `examples/` 디렉토리를 새로 만든다.

- **표준 라이브러리만 쓴다.** `urllib.request`(요청) · `http.cookiejar`(세션 쿠키 유지) ·
  `json` · `argparse` · `time` 정도면 충분하다
- 동작: 로그인 → 텍스트 공급(`POST`) → 생성된 문서를 폴링해 `embedding_status`가 `pending`에서
  벗어날 때까지 기다린 뒤 결과를 출력한다. **폴링에는 상한을 둔다** — 무한 대기는 예제로
  나쁘다
- 부를 경로는 **모듈 레벨 상수**로 둔다. 테스트가 이 상수를 읽어 실제 라우트와 대조한다:

```python
LOGIN_PATH = "/api/auth/login"
INGEST_PATH = "/api/documents/text"
DOCUMENT_PATH = "/api/documents/{document_id}"
```

- CLI 인자(형태는 재량): `--base-url` · `--username` · `--password` · `--title` ·
  텍스트 입력(파일 경로 또는 stdin) · `--content-type` · `--tags` · `--visibility`
- `if __name__ == "__main__":` 가드를 반드시 둔다 — 테스트가 이 모듈을 import한다.
  **import 시점에 네트워크 호출이나 인자 파싱이 일어나면 안 된다**
- 파일 상단 docstring에 **무엇을 증명하는 예제인지** 적어라: 이 파일은 `backend`를 import하지
  않으며 HTTP만으로 문서를 공급한다는 것, 그리고 실행 예시 커맨드

**임베딩 파이프라인을 흉내 내지 마라.** 예제는 문서를 넣고 상태를 볼 뿐이다. 공급자는
INSERT만 하고 파생은 전부 DB 트리거가 만든다 — 그 사실이 이 예제가 짧은 이유다.

### 3) `scripts/check.sh`에서 `examples/`도 lint 대상에 넣는다

현재 `ruff`는 `backend/`에서만 돌아 `examples/`가 검사 사각지대에 남는다. backend ruff 호출에
경로를 하나 더한다:

```bash
"$VENV/bin/ruff" check . "$ROOT/examples" || FAILED=1
```

**이 한 줄 말고 `check.sh`를 다른 곳에서 고치지 마라.**

## Acceptance Criteria

```bash
cd backend

# 1) 새 아키텍처 검사와 경로 대조가 통과한다
.venv/bin/pytest tests/test_architecture.py tests/test_documents_api.py -q
#   → 전부 passed

# 2) 예제가 정말 backend에 의존하지 않는다 (테스트와 별개의 직접 확인)
.venv/bin/python -c "
import ast, sys
from pathlib import Path
root = Path('..').resolve()
for path in (root / 'examples').rglob('*.py'):
    tree = ast.parse(path.read_text(), filename=str(path))
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods |= {a.name.split('.')[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module.split('.')[0])
    forbidden = mods & {'app', 'backend', 'mcp_server'}
    assert not forbidden, f'{path}: {forbidden}'
    nonstdlib = mods - sys.stdlib_module_names
    assert not nonstdlib, f'{path}: 표준 라이브러리 밖 {nonstdlib}'
    print(f'{path.name}: import {sorted(mods)} — 전부 표준 라이브러리')
"
#   → 각 파일에 대해 "전부 표준 라이브러리" 출력

# 3) 예제가 실행 가능한 형태다 (import 부작용 없음 + --help 동작)
.venv/bin/python ../examples/ingest_text.py --help
#   → 사용법이 출력되고 종료 코드 0

# 4) ruff가 examples를 검사한다
cd .. && grep -n "examples" scripts/check.sh
#   → ruff 호출 줄에 examples 경로가 있어야 한다

# 5) 전체 검증
bash scripts/check.sh
#   → 마지막 줄에 "검증 실패"가 없어야 한다
```

## 검증 절차

1. 위 AC 커맨드를 순서대로 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - `examples/`가 `backend/`·`frontend/`와 나란한 최상위 디렉토리인가?
   - 예제에 임베딩·청킹·DB 접속 코드가 없는가? (공급자는 HTTP만 안다)
   - `test_architecture.py`가 여전히 앱을 import하지 않는 정적 검사 파일인가?
3. `docs/` 문서는 이 step에서 고치지 않는다 (step 5에서 디렉토리 구조와 함께 반영).
4. 결과에 따라 `phases/m11b-text-ingest/index.json`의 step 2를 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- **`requests`·`httpx` 등 서드파티 HTTP 라이브러리를 쓰지 마라.** 이유: 예제의 요점은 "우리
  코드에 의존하지 않는다"인데, 설치가 필요한 의존을 얹으면 "그냥 실행되는 예제"라는 가치가
  사라진다. 표준 라이브러리로 충분하다.
- **예제를 `backend/` 안에 두지 마라.** 이유: `backend/` 안에 있으면 `app`을 import할 수 있는
  자리가 되고, 독립성 주장이 위치로 반박된다.
- **예제에서 DB에 직접 접속하지 마라.** 이유: HTTP만으로 공급된다는 것이 증명 대상이다.
  psycopg를 쓰는 순간 그 명제가 깨진다.
- **`app`·`services`·`api` 아래 파일을 고치지 마라.** 이유: step 1이 API를 확정했다. 예제를
  쓰다가 API가 불편하게 느껴져도 여기서 고치지 마라 — 그러면 예제에 맞춘 API가 된다.
  정말 문제가 있으면 `blocked`로 멈추고 사유를 적어라.
- **`scripts/check.sh`를 ruff 경로 한 줄 외에 고치지 마라.** 이유: 이 스크립트는 Stop 훅과 CI가
  같이 쓰는 검증 진입점이라, 여기서의 변경은 프로젝트 전체의 검증 동작을 바꾼다.
- **기존 테스트를 깨뜨리지 마라.**
