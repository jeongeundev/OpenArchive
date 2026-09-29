-- 019_idempotency_tables.sql — 문서 생성 요청의 멱등키 (ADR-047, #120)
--
-- failover 중에는 COMMIT이 서버에 닿아 커밋된 뒤 응답만 잃는 "모호한 커밋"이 생긴다
-- (#110 B-6). 클라이언트가 같은 키로 다시 보내면 서비스가 이 테이블을 보고 처음 만든
-- 문서를 돌려준다. 키 행은 문서 INSERT와 같은 트랜잭션에서 들어가므로 둘은 함께 있거나
-- 함께 없다.
--
-- 앱이 직접 INSERT한다: 잡·관계를 트리거에 둔 규칙은 원본-벡터 정합성의 파생물에 대한
-- 것이고, 멱등키는 파생물이 아니라 요청의 기록이다 (ADR-047 트레이드오프 3).
--
-- 만료 인덱스를 두지 않는다: 24시간치 문서 생성 요청만 남는 작은 테이블이라 워커의
-- 정리 쿼리가 통째로 읽어도 싸다.

CREATE TABLE idempotency_keys (
  -- 소유자 범위다. 키는 클라이언트가 고르므로 전역이면 남의 키와 부딪혀 그 존재를
  -- 알게 된다 (ADR-018). documents.owner_id와 같은 타입이며 FK가 없는 것도 같다.
  owner_id     text NOT NULL,
  key          text NOT NULL CHECK (length(key) BETWEEN 1 AND 255),
  -- 같은 키에 다른 본문이 오면 재시도가 아니라 키 재사용이다 — 이 해시로 가려 거절한다.
  request_hash text NOT NULL,
  -- 문서를 지우면 키도 지운다. 지운 문서를 가리키는 키는 재시도에 돌려줄 것이 없다.
  document_id  uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  created_at   timestamptz NOT NULL DEFAULT now(),
  -- 같은 키의 동시 요청을 이 기본키가 직렬화한다: 뒤 요청은 앞 트랜잭션이 끝나기를
  -- 기다렸다가 충돌을 보고 커밋된 행을 읽는다.
  PRIMARY KEY (owner_id, key)
);
