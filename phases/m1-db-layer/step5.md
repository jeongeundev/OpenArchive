# Step 5: 임베딩 프로바이더

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/docs/ARCHITECTURE.md` — **"임베딩 프로바이더" 절 전체**(Protocol 시그니처, LocalProvider·FakeProvider의 역할)
- `/docs/ADR.md` — **ADR-003 전체**(BGE-M3 단일 프로바이더, 상용 API 금지의 규정 근거, `FakeProvider`를 남기는 이유), ADR-002(코사인 거리와 정규화 임베딩의 궁합)
- **이전 step 산출물**: `/backend/app/services/chunking.py`(같은 계층의 코드 스타일), `/backend/app/config.py`(`embedding_provider` 설정이 이미 있다), `/backend/pyproject.toml`
- `/scripts/hooks/tdd-guard.sh` — `base.py`·`fake.py`·`__init__.py`는 테스트 면제지만 **`local.py`는 `test_*local*.py`를 요구한다**

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 배경

`config.py`에는 M0부터 `embedding_provider: Literal["local", "fake"]`가 있지만 그것을 실제 객체로 바꾸는 코드가 없다. 이 step이 그 연결을 만든다.

**`FakeProvider`는 편의 장치가 아니라 이 프로젝트 TDD의 핵심 인프라다** (ADR-003). BGE-M3는 약 2GB이고 첫 로딩에 수십 초가 걸린다. 워커·검색 테스트를 매번 그 위에서 돌릴 수는 없으므로, 파이프라인 전체를 CI 속도로 검증할 결정론적 대역이 필요하다.

## 작업

### 1. `backend/pyproject.toml` — optional extra 추가

```toml
[project.optional-dependencies]
dev = [ ... 기존 그대로 ... ]
local = ["sentence-transformers"]
```

- **기본 `dependencies`에 넣지 마라.** `sentence-transformers`는 torch를 끌고 와 설치 용량이 수 GB 늘어나는데, 테스트·CI는 전부 `FakeProvider`로 돈다.
- 이 결정을 `pyproject.toml`에 주석으로 남기고, `README.md`의 백엔드 설치 안내에 **실제 임베딩을 돌릴 때 필요한 명령 한 줄**을 추가하라.
  ```bash
  pip install -e ".[dev,local]"   # 실제 임베딩(BGE-M3)까지 쓸 때
  ```
- **상용 임베딩 API 클라이언트(openai·cohere·voyageai 등)를 넣지 마라.** 이유: 대회 규정 [별표2]가 API 전용 모델을 금지한다 (ADR-003).

### 2. `backend/app/embeddings/base.py`

```python
EMBEDDING_DIM = 1024

class EmbeddingProvider(Protocol):
    name: str
    dimension: int
    def embed(self, texts: list[str]) -> list[list[float]]: ...
```

- `EMBEDDING_DIM`은 `vector(1024)` 컬럼과 한 쌍이다. **프로바이더가 바뀌어도 이 값은 바꾸지 않는다** (ADR-003).
- Protocol 하나와 상수 하나면 충분하다. 추상 기반 클래스·등록 레지스트리·플러그인 로더를 만들지 마라.

### 3. `backend/app/embeddings/fake.py`

```python
class FakeProvider:
    name = "fake"
    dimension = EMBEDDING_DIM
    def embed(self, texts: list[str]) -> list[list[float]]: ...
```

반드시 지킬 규칙:

1. **`hashlib`을 써라. 내장 `hash()`를 쓰지 마라.** 이유: 파이썬의 `hash()`는 `PYTHONHASHSEED`에 따라 **프로세스마다 값이 달라진다.** 워커가 만든 벡터와 검색이 만든 질의 벡터가 다른 프로세스에서 계산되므로, `hash()`를 쓰면 벡터 공간이 프로세스마다 달라져 검색이 조용히 망가진다.
2. **토큰 단위로 누적하라.** 텍스트 전체를 한 번에 해싱하지 말고, 공백으로 나눈 토큰마다 해시로 차원 인덱스·부호를 정해 벡터에 더한다. 이유: **어휘가 겹치는 텍스트가 실제로 가까워져야** 후속 검색 phase에서 "질의와 관련된 문서가 상위에 온다"를 검증할 수 있다. 텍스트 전체를 한 번 해싱하면 모든 벡터가 무작위로 흩어져 그 테스트가 불가능해진다.
3. **L2 정규화한다.** 코사인 거리(`<=>`)를 쓰므로 노름이 1이어야 실제 임베딩과 같은 성질을 갖는다. 0 벡터가 나오는 입력(토큰 없음)은 정규화에서 0으로 나누지 않도록 처리하라.
4. 길이는 항상 `EMBEDDING_DIM`, 입력 리스트와 출력 리스트의 길이가 같다.
5. 모델·네트워크·파일 접근이 없다.

### 4. `backend/app/embeddings/local.py`

```python
class LocalProvider:
    name = "BAAI/bge-m3"
    dimension = EMBEDDING_DIM
    def embed(self, texts: list[str]) -> list[list[float]]: ...
```

반드시 지킬 규칙:

1. **lazy-load다.** 인스턴스를 만드는 것만으로 모델을 내려받거나 로드하지 않는다. 첫 `embed()` 호출 때 로드하고 이후 재사용한다. 이유: `get_provider()`가 워커 기동 시점에 호출되는데, 그때 2GB를 로드하면 기동이 막힌다.
2. `sentence_transformers` import는 **함수 안에서** 한다. 모듈 최상위에서 import하면 `.[local]`을 설치하지 않은 환경에서 `app.embeddings` import 자체가 깨진다.
3. **미설치 시 설치 방법이 담긴 에러를 던져라.** `ImportError`를 그대로 흘리지 말고, `pip install -e ".[dev,local]"`을 안내하는 메시지로 감싼다.
4. **정규화된 임베딩을 반환하라** (`normalize_embeddings=True`). 코사인 거리 전제와 맞춘다 (ADR-002).
5. 배칭·캐싱·폴백 체인을 만들지 마라 (`ARCHITECTURE.md` 임베딩 프로바이더 절에 명시).

### 5. `backend/app/embeddings/__init__.py` — 선택 함수

```python
def get_provider(name: str | None = None) -> EmbeddingProvider:
    """name이 없으면 설정(EMBEDDING_PROVIDER)을 따른다."""
```

- `"fake"` → `FakeProvider`, `"local"` → `LocalProvider`. 그 외 값은 명확한 에러.
- **`local`을 반환할 때도 모델을 로드하지 않는다** (위 lazy 규칙과 한 쌍).
- 프로바이더 인스턴스를 캐시할지는 재량이지만, 캐시한다면 테스트가 설정을 바꿔가며 검증할 수 있도록 초기화 수단을 남겨라.

### 6. 테스트 — 먼저 작성한다

`base.py`·`fake.py`·`__init__.py`는 tdd-guard 면제지만 **`CLAUDE.md`의 TDD 규칙은 그대로 적용된다.** 테스트를 먼저 쓰고 실패를 확인한 뒤 구현하라.

**`backend/tests/test_embeddings.py`**
1. `FakeProvider().embed([...])`가 입력 개수만큼, 각각 1024차원 벡터를 반환한다.
2. **결정론** — 같은 텍스트를 두 번 임베딩하면 완전히 동일하다.
3. **프로세스 간 안정성** — 별도 파이썬 프로세스(`subprocess`)에서 `PYTHONHASHSEED`를 다르게 주고 같은 텍스트를 임베딩해도 결과가 같다. 이유: 위 1번 규칙(`hash()` 금지)을 실제로 강제하는 유일한 방법이다.
4. **정규화** — 노름이 1에 가깝다.
5. **어휘 유사성** — 단어를 많이 공유하는 두 텍스트의 코사인 유사도가, 전혀 겹치지 않는 두 텍스트보다 **높다.**
6. `get_provider("fake")`가 `FakeProvider`를, `get_provider("local")`이 `LocalProvider`를 반환하고, 알 수 없는 이름은 에러다. 설정 기반 선택(`EMBEDDING_PROVIDER`)도 확인한다 — `conftest.py`에 이미 설정 캐시를 비우는 픽스처가 있다.
7. **`get_provider("local")` 호출이 모델을 로드하지 않는다** — 반환은 되지만 무거운 작업이 일어나지 않아야 한다.

**`backend/tests/test_embeddings_local.py`** (파일명이 `test_*local*.py`여야 tdd-guard가 `local.py` 쓰기를 허용한다)
1. `name`·`dimension` 값이 계약대로다.
2. **인스턴스화가 모델을 로드하지 않는다** — 내부 모델 핸들이 아직 비어 있다.
3. **미설치 에러** — `sys.modules`에 `sentence_transformers`를 `None`으로 넣어 ImportError를 유발한 뒤, `embed()`가 **설치 방법을 담은 에러**를 던지는지 확인한다. 이 방식은 실제 설치 여부와 무관하게 결정론적으로 동작한다.

> **실제 BGE-M3 모델을 로드하는 테스트를 만들지 마라.** 2GB를 내려받고 수십 초가 걸려 `check.sh`가 매번 그 비용을 치르게 된다. 실제 모델 동작 확인은 `EMBEDDING_PROVIDER=local`로 워커를 한 번 돌려보는 수동 검증으로 대신하며, 그것은 이 step의 AC가 아니다.

## Acceptance Criteria

```bash
cd backend
.venv/bin/pip install -e ".[dev]"   # optional extra 추가가 기존 설치를 깨지 않는지 확인
.venv/bin/ruff check .
.venv/bin/pytest tests/test_embeddings.py tests/test_embeddings_local.py -v
.venv/bin/pytest                    # 전체 통과

# .[local]을 설치하지 않은 상태에서도 임포트가 깨지지 않아야 한다
.venv/bin/python -c "from app.embeddings import get_provider; p = get_provider('local'); print(p.name, p.dimension)"

cd ..
bash scripts/check.sh               # 종료 코드 0
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - `base.py`(Protocol)·`local.py`·`fake.py` 구성이 `ARCHITECTURE.md`와 일치하는가?
   - 차원이 1024로 고정되어 있는가? (ADR-003)
   - `FakeProvider`가 `hashlib` 기반이고 프로세스 간 안정적인가?
   - `sentence-transformers`가 기본 의존성이 아니라 `local` extra에 있는가?
   - 상용 임베딩 API 클라이언트가 들어가지 않았는가? (ADR-003 — 대회 규정)
3. 결과에 따라 `phases/m1-db-layer/index.json`의 step 5를 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"` — **`get_provider()` 시그니처와 `FakeProvider`의 벡터 생성 방식(토큰 해싱·정규화)을 포함시켜라.** 워커 step과 후속 검색 phase가 이 성질에 의존한다.
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason": "..."` 후 즉시 중단

## 금지사항

- **상용 임베딩 API 프로바이더를 만들지 마라** (OpenAI·Cohere·Voyage 등). 이유: 대회 운영규정 [별표2]가 "외부 API 호출을 통해서만 작동하는 API 전용 모델" 사용을 금지한다. 규정 위반은 결격 사유다 (ADR-003).
- **`sentence-transformers`를 기본 `dependencies`에 넣지 마라.** 이유: 이번 phase에서 확정한 결정이다. `local` extra로 분리한다.
- **내장 `hash()`로 가짜 벡터를 만들지 마라.** 이유: 프로세스마다 값이 달라져 벡터 공간이 어긋난다.
- **모델 로딩을 모듈 최상위나 생성자에서 하지 마라.** 이유: 워커 기동이 2GB 로딩에 막힌다.
- **실제 BGE-M3를 로드하는 테스트를 만들지 마라.** 이유: `check.sh`가 매 실행마다 수십 초를 쓴다.
- **배칭·캐싱·폴백 체인·재시도를 만들지 마라.** 이유: `ARCHITECTURE.md`가 명시적으로 만들지 않기로 한 것들이다.
- **`app/worker.py`를 만들지 마라.** 이유: 다음 step의 범위다.
- **차원을 설정 가능하게 만들지 마라.** 이유: `vector(1024)` 컬럼과 한 쌍으로 고정이다 (ADR-003).
- 기존 테스트를 깨뜨리지 마라.
