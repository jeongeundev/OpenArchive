-- 020_lease_tables.sql — 잡 선점 lease (ADR-050, #121)
--
-- 좀비 판정을 "시작 후 5분"에서 "lease 만료"로 바꾼다. 워커는 선점할 때 lease를 찍고
-- 처리하는 동안 heartbeat로 연장한다. 연결이 끊겨 버려진 잡은 연장이 멈추므로 lease
-- 길이(기본 60초) 뒤에 회수된다 — HA에서는 워커가 살아 있어도 연결이 죽는다(#110 B-7).
--
-- lease는 processing일 때만 뜻이 있다. 반납·실패·마감이 값을 지우지 않아도 되도록
-- 제약은 processing 쪽만 건다.

ALTER TABLE embedding_jobs ADD COLUMN lease_expires_at timestamptz;

-- 이행 순간 처리 중인 잡은 옛 판정과 같은 시각에 회수되게 한다. now()로 두면 아직 살아
-- 있을 수 있는 잡을 배포 직후 스윕이 곧바로 회수한다.
UPDATE embedding_jobs
   SET lease_expires_at = coalesce(started_at, now()) + interval '5 minutes'
 WHERE status = 'processing';

-- lease가 NULL인 processing 잡은 스윕이 영원히 회수하지 못한다. lease를 찍지 않는 옛
-- 워커가 배포 중에 남아 있으면 그런 행을 만드는데, 이 제약이 조용한 멈춤을 선점 실패로
-- 바꾼다.
ALTER TABLE embedding_jobs
  ADD CONSTRAINT embedding_jobs_processing_has_lease
  CHECK (status <> 'processing' OR lease_expires_at IS NOT NULL);
