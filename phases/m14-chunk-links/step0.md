# Step 0: chunk-boundaries

## 배경 — 청킹이 자르는 자리의 65%가 문장 중간이다 (#93 K1·K3, #103)

`backend/app/services/chunking.py`의 `chunk_text`는 1,000자 창 안 **마지막 문단 경계(빈 줄)**에서
자르고, 없으면 창 끝에서 강제로 자른다. 실코퍼스 검증(#93)에서 두 가지가 드러났다.

- **K1 (PDF 규정집, 코퍼스 C)** — pypdf 출력은 줄바꿈 없이 이어진다(`…위임할 수 있다.제6조(채용방법) ① …`).
  빈 줄은 페이지 각주 자리(`…검토의\n\n-439-\n견을 받아…`)에만 있는데 그것이 문장 중간이다. 경계 2,107개 중
  1,360개(65%)가 문장 중간이었다 — 강제 절단 703 + **문장 중간의 각주 문단 경계 1,073**. 즉 "문단 경계를
  못 찾을 때만 폴백"으로는 절반이 남는다.
- **K3 (마크다운, 코퍼스 A)** — `본문\n\n### 절 제목\n\n본문`에서 창 안 마지막 빈 줄이 헤딩 바로 뒤라
  `### 절 제목`이 앞 청크 꼬리에 고립된다. 경계 896개 중 200개(22%).

그래서 경계 후보에 **등급**을 둔다. 실제 A·C 원문으로 잰 결과(#103 논의, 프로토타입):

| | A 문장 중간+헤딩 꼬리 | A 청크 | C 문장 중간 | C 청크 |
|---|---|---|---|---|
| 현재 | 28% (헤딩 꼬리 200) | 1,080 | 65% | 2,211 |
| 이 step의 규칙 | 4% (헤딩 꼬리 1) | 1,103 | **9%** | 1,954 |

시연 코퍼스(`scripts/seed_demo.py`)는 51/13(1청크/2청크)로 변하지 않는다 — `test_seed_demo.py`의
단언이 그대로 유지돼야 한다.

## 읽어야 할 파일

- `CLAUDE.md` — TDD, "벡터 컬럼 vector(1024) 고정"(청킹은 차원과 무관), 편집 규칙
- `docs/ARCHITECTURE.md` 「청킹 (services/chunking.py)」 — 순수 함수·1,000자·150자 오버랩
- `backend/app/services/chunking.py` — **수정 대상.** `chunk_text`·`_paragraph_cut`·`_PARAGRAPH_BREAK`
- `backend/tests/test_chunking.py` — **테스트 추가 대상.** 모듈 docstring의 불변식 4개와 헬퍼
  `nonspace`·`is_subsequence`·`shared_boundary`. 기존 테스트는 전부 그대로 통과해야 한다
- `backend/tests/test_seed_demo.py::test_corpus_mixes_single_and_multi_chunk_documents` — 청크 수 분포 단언

## 작업

### 1) 테스트를 먼저 쓴다 — `backend/tests/test_chunking.py`

재현 텍스트는 아래 규칙이 갈리는 모양으로 직접 만든다. 실코퍼스 발췌를 그대로 넣지 않는다(라이선스·길이).
모든 테스트는 `max_chars`·`overlap`을 작게 줘도 되지만, 기본값(1000/150)으로 최소 하나는 확인한다.

1. `test_pdf_text_without_newlines_is_cut_at_a_sentence_end_or_article_head` — 개행이 하나도 없고
   `…할 수 있다.제6조(채용방법) ① …` 꼴로 조문이 이어지는 본문. 첫 청크가 `다.`로 끝나거나 다음 청크가
   `제N조(`로 시작한다(둘 다 같은 자리다). 어떤 청크도 `…에 대한 검토의`처럼 어절 중간에서 끝나지 않는다.
2. `test_a_paragraph_break_in_the_middle_of_a_sentence_loses_to_a_later_sentence_end` — 페이지 각주 모양.
   `…검토의\n\n-439-\n견을 받아 첨부하여야 한다.  ② …`처럼 창 안 유일한 빈 줄이 문장 중간이고 그 뒤에
   문장 끝이 있으면, 빈 줄이 아니라 **뒤의 문장 끝**에서 자른다. 각주 `-439-`는 청크 안에 남는다(K2는 범위 밖).
3. `test_a_sentence_complete_paragraph_break_still_wins_over_later_sentence_ends` — 빈 줄 앞이 `한다.`로
   끝나면(1등급) 그 뒤에 문장 끝이 더 있어도 빈 줄에서 자른다. 기존 문단 우선 동작이 유지됨을 고정한다.
4. `test_a_heading_is_not_left_at_the_tail_of_a_chunk` — `본문\n\n### 절 제목\n\n본문…`에서 창 안 마지막 빈 줄이
   헤딩 바로 뒤일 때 **헤딩 앞**에서 자른다. 어떤 청크도 `#`으로 시작하는 줄로 끝나지 않는다.
   `## 상위\n\n### 하위\n\n본문`처럼 헤딩이 연속이면 `## 상위` 앞에서 자른다.
5. `test_heading_pullback_never_violates_the_overlap_floor` — 헤딩 앞 빈 줄이 `start + overlap` 이하라
   물릴 수 없을 때, 헤딩 뒤 빈 줄에서 자르지 **않고** 문장 끝/강제 절단으로 넘어간다(프로토타입에서
   162자짜리 "오버랩 + 헤딩" 청크가 생긴 결함). 헤딩 뒤에 빈 줄 없는 긴 코드 블록을 붙여 재현한다.
6. `test_list_markers_and_dates_are_not_sentence_ends` — `  1. 국가공무원법 …`·`제정 2021. 12. 28.`의
   `.`에서는 자르지 않는다. `다.`/`요.`/공백 앞 `.!?`만 문장 끝이다.
7. `test_structural_markdown_lines_count_as_complete_paragraphs` — `- 항목`·`| 표 |`·`> 인용`·`` ``` ``·`---`로
   끝나는 문단 뒤 빈 줄은 마침표가 없어도 1등급이다(마크다운 청킹이 지금과 같게 유지된다).
8. 기존 불변식(길이 상한·누락 없음·오버랩 공유·결정론)이 새 재현 텍스트 전부에서 성립한다 —
   `nonspace`·`is_subsequence`·`shared_boundary`를 재사용한다.

### 2) 구현 — `backend/app/services/chunking.py`

`chunk_text`의 시그니처·기본값·예외·"빈 문자열은 빈 리스트"·오버랩 의미는 **바꾸지 않는다**.
`_paragraph_cut`를 후보 등급 선택으로 바꾼다. 창 `(floor, limit)` 안에서 후보를 등급별로 모으고,
**가장 높은 등급의 마지막 위치**를 고른다. 같은 등급 안에서는 뒤쪽이 이긴다(창을 채우기 위해).

| 등급 | 후보 | 자르는 자리 |
|---|---|---|
| 1 | **문장이 끝난** 문단 경계 | 빈 줄의 시작(`match.start()`) |
| 2 | 문장 끝 · 조문 머리 | `다.`·`요.`·`(?<!\d)[.!?。](?=\s|$)`는 **부호 뒤**(`match.end()`), `제\s?\d+\s?조(?:의\s?\d+)?\s?\(`는 **`제` 앞**(`match.start()`) |
| 3 | 그 밖의 문단 경계(문장 중간) | 빈 줄의 시작 |
| 4 | 없음 → 강제 절단 | `limit` |

핵심 규칙 — 여기서 벗어나면 안 된다:

- **헤딩 뒤 문단 경계는 어느 등급에도 넣지 않는다 (K3).** 직전 문단(이전 빈 줄 뒤부터 이 빈 줄까지)의
  비어 있지 않은 줄이 전부 ATX 헤딩(`^\s*#{1,6}\s+\S`)이면 제외한다. 헤딩 앞의 빈 줄은 보통 후보다.
  `floor` 판정과 무관하게 "직전 문단"의 시작은 항상 갱신해야 한다 — 창 첫머리(오버랩 구간)의 빈 줄이
  `floor` 이하라 후보에서 빠져도 그 뒤 문단의 경계는 맞게 봐야 한다.
- **"문장이 끝났다"** = 빈 줄 앞의 마지막 비공백 문자가 종결 집합 `. ! ? 。 」 』 ) ] " ' ” ’ :` 에 있거나,
  빈 줄 앞의 마지막 줄(200자 이하)이 마크다운 구조 줄 `^\s*(#{1,6}\s|[-*+]\s|\d+[.)]\s|\||>|```|---)`에
  맞는 것. 200자 상한은 PDF 텍스트에서 "줄" 하나가 페이지 전체(1,500자)라 `\d+[.)]\s`에 우연히 맞는 것을
  막는다 — 프로토타입에서 실제로 났다.
- 모든 후보는 `floor = start + overlap`보다 **뒤**여야 하고(전진 보장, 기존 `_paragraph_cut` docstring의
  이유 그대로) `limit`보다 앞이어야 한다.
- 정규식은 모듈 상수로 둔다(`_PARAGRAPH_BREAK`처럼). 각 상수 위에 무엇을 왜 잡는지 한두 줄 주석.
- 모듈 docstring과 `chunk_text` docstring의 "문단 경계를 우선 존중한다" 설명을 등급 규칙으로 고친다.
  ARCHITECTURE.md는 step 3이 고친다 — 여기서 건드리지 않는다.

## Acceptance Criteria

```bash
cd backend && source .venv/bin/activate
ruff check .
pytest tests/test_chunking.py -q                          # 새 테스트 8건 포함 전부 통과
pytest tests/test_seed_demo.py tests/test_worker.py -q     # 청크 수 분포·워커 1:1 대응 유지
bash ../scripts/check.sh
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트: `chunking.py`가 여전히 순수 함수인가(DB·모델·시간·파일 의존 없음)? `chunk_text`
   시그니처가 그대로인가? CLAUDE.md CRITICAL 위반이 없는가?
3. `phases/m14-chunk-links/index.json`의 step 0을 갱신한다 — 성공 시 `"status": "completed"`와
   `"summary"`(추가한 상수 이름·등급 규칙 한 줄), 3회 실패 시 `"error"`, 사용자 개입 필요 시 `"blocked"`.

## 금지사항

- `max_chars`·`overlap` 기본값이나 오버랩 의미를 바꾸지 마라. 이유: 워커·테스트·문서가 1,000/150을 전제한다.
- 페이지 각주 `-NNN-`·`[목차로]`를 지우거나 코드 펜스·표를 인식하지 마라. 이유: #93 K2·K4는 한계로 기록했고
  이 이슈 범위 밖이다.
- 토크나이저나 문장 분리 라이브러리를 들이지 마라. 이유: 순수 함수·문자 수 단위가 이 모듈의 계약이다(ADR-003).
- 기존 테스트를 약화하거나 지우지 마라. 청크 수 단언(`test_seed_demo.py`)이 깨지면 규칙을 다시 보라 —
  시연 코퍼스는 51/13 그대로여야 한다.
