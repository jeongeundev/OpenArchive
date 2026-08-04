## 무엇을 했는가

<!-- 이 PR이 끝나면 무엇이 달라지는지 한두 문장으로 -->

Closes #

## 어떻게 했는가

<!-- 핵심 구현 방식. 자명하면 생략 가능 -->

## 설계 결정

<!-- ADR을 추가·변경했다면 링크. 없으면 "없음" -->

## 체크리스트

- [ ] `bash scripts/check.sh` 통과
- [ ] 테스트를 먼저 작성했다 (TDD)
- [ ] `CLAUDE.md`의 CRITICAL 규칙을 위반하지 않는다
  - [ ] 애플리케이션에서 `embedding_jobs`에 직접 INSERT하지 않음
  - [ ] 검색 쿼리를 plain `BEGIN`으로 감쌈 (`BEGIN READ ONLY` 아님)
  - [ ] DB 접속이 단일 엔드포인트 (멀티호스트 DSN 없음)
  - [ ] 스키마 변경이 `backend/migrations/`의 번호 붙은 SQL로만 이뤄짐
- [ ] 관련 문서를 갱신했다 (해당 시)

## 리뷰 메모

<!-- 셀프 리뷰 결과 또는 /code-review 실행 결과를 리뷰 코멘트로 남길 것 -->
