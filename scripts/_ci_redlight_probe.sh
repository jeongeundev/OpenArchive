#!/usr/bin/env bash
# 임시 프로브 — CI가 빨간불을 내는 것을 한 번 관측하기 위한 의도적 위반이다 (#60).
# 바로 다음 커밋에서 제거한다. 강화한 파생 테이블 가드가 셸 파일과 여러 줄 SQL을
# 함께 잡는지도 이 프로브가 증명한다 (backend/tests/test_architecture.py).
psql -c "
INSERT INTO
  embedding_jobs (document_id)
VALUES ('00000000-0000-0000-0000-000000000000')
"
