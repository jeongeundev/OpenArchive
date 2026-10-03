-- 027_edge_jobs_triggers.sql — 전량 재계산은 관계 잡을 거는 것으로 끝난다 (ADR-029 결정 6 개정, #156)
--
-- `openarchive rebuild-edges`와 demo는 워커 옆에서 rebuild_document_edges를 직접 불렀다. 관계를
-- 쓰는 곳이 둘이었고, 한 문서에서 둘이 겹치면 교착했다(#95-e2 — 교착 자체는 024의 잠금이 고쳤다).
-- 여기서는 쓰는 곳을 워커 하나로 모은다: 전량 재계산은 ready 문서마다 관계 잡을 걸 뿐이고,
-- 판정은 워커가 잡마다 같은 함수로 한다. 앱은 embedding_jobs에 직접 INSERT하지 않는다 —
-- 잡을 만드는 것은 017의 트리거와 이 함수다.
--
-- 코얼레싱은 016의 uq_pending_job_per_doc_kind가 맡는다: 이미 대기 중인 관계 잡은 그대로 두고,
-- 처리 중인 잡 옆에는 새 잡을 건다 — 처리 중인 잡은 그 시점의 코퍼스로 판정하기 때문이다.
-- 격리된(error) 관계 잡은 닫지 않는다. 닫는 것은 같은 문서의 판정이 실제로 성공한 순간이다(워커).
--
-- 돌려주는 값은 대상 ready 문서 수다. 새로 건 잡 수가 아니다 — 코얼레싱된 잡도 이 요청의 일을 한다.

CREATE FUNCTION enqueue_all_edge_jobs() RETURNS bigint
  LANGUAGE plpgsql
AS $$
DECLARE
  target_count bigint;
BEGIN
  INSERT INTO embedding_jobs (document_id, kind)
    SELECT id, 'edges' FROM documents WHERE embedding_status = 'ready'
    ON CONFLICT DO NOTHING;
  SELECT count(*) INTO target_count FROM documents WHERE embedding_status = 'ready';
  PERFORM pg_notify('embedding_jobs', '');
  RETURN target_count;
END; $$;
