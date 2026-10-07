# Step 0: phase-slug

## 읽어야 할 파일

- `/CLAUDE.md`
- `scripts/execute.py` (scripts 아래 모듈과 테스트 관례 참고)

## 작업

`scripts/smoke_slug.py`에 아래 함수를 만든다.

```python
def phase_slug(title: str) -> str: ...
```

- 소문자로 바꾼다
- 공백은 `-`로 바꾼다
- 영문 소문자·숫자·하이픈 외의 문자는 지운다

테스트는 `scripts/test_smoke_slug.py`에 먼저 작성한다(TDD).

## Acceptance Criteria

```bash
backend/.venv/bin/python -m pytest -q scripts/test_smoke_slug.py
backend/.venv/bin/ruff check scripts/smoke_slug.py scripts/test_smoke_slug.py
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 결과에 따라 `phases/m98-ship-smoke/index.json`의 step 0을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`

## 금지사항

- `scripts/smoke_slug.py`·`scripts/test_smoke_slug.py` 밖의 파일을 바꾸지 마라. 이유: 시험용 phase다
