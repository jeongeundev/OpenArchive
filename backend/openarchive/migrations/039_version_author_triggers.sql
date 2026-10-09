-- 039_version_author_triggers.sql — 새 텍스트 버전의 작성자 (ADR-061 결정 6)
-- 감사(029)와 같은 트랜잭션 범위 GUC 전달 경로를 쓴다. 빈 placeholder는 NULL로 읽는다.
-- openarchive.text_source = 'extraction'은 추출이 텍스트를 썼다는 표시다.
-- 작성자는 워커로 남기되, 재추출을 요청한 사람을 기록하는 감사 행위자는 바꾸지 않는다.

CREATE OR REPLACE FUNCTION on_document_content_changed() RETURNS trigger AS $$
DECLARE
  v_actor text := NULLIF(current_setting('openarchive.actor_id', true), '');
  v_via text := NULLIF(current_setting('openarchive.actor_via', true), '');
  v_source text := NULLIF(current_setting('openarchive.text_source', true), '');
BEGIN
  -- (1) 버전 이력 기록 — 작성자는 트랜잭션의 행위자, 추출 텍스트는 워커다.
  --     애플리케이션은 document_versions에 직접 INSERT하지 않는다 — embedding_jobs와
  --     같은 원칙이다. INSERT의 v1도 여기서 기록되므로 이력이 append-only로 완결되고,
  --     문서 생성 직후부터 v1 조회가 가능하다.
  --     ON CONFLICT는 재실행 안전장치다. 같은 버전 번호로 트리거가 두 번 발화해도
  --     (예: reembed 경로) 이력이 중복되지 않는다.
  INSERT INTO document_versions (document_id, version, content, content_hash, author, author_via)
  VALUES (NEW.id, NEW.version, NEW.content, NEW.content_hash,
          CASE WHEN v_source = 'extraction' THEN NULL ELSE v_actor END,
          CASE WHEN v_source = 'extraction' THEN 'worker' ELSE COALESCE(v_via, 'direct') END)
  ON CONFLICT (document_id, version) DO NOTHING;

  -- (2) 임베딩 대기 상태로 전환.
  --     UI 배지와 /admin/status가 읽는 값이다. 이미 pending이면 건드리지 않아
  --     불필요한 행 갱신과 트리거 재진입 여지를 줄인다.
  UPDATE documents SET embedding_status = 'pending'
   WHERE id = NEW.id AND embedding_status <> 'pending';

  -- (3) 잡 생성 — 코얼레싱은 파셜 유니크 인덱스(uq_pending_job_per_doc)가 수행한다.
  --     충돌 대상을 명시하지 않는 것은 의도적이다: 제약의 정의를 여기 복사해두면
  --     002_tables.sql과 어긋날 수 있고, 어차피 이 INSERT가 부딪힐 유니크 제약은
  --     그것 하나뿐이다. 파셜이라 처리가 끝난 문서는 다시 pending 잡을 가질 수 있고,
  --     처리 중(processing) 재수정되면 새 pending 잡이 생겨 최신 내용이 반영된다.
  INSERT INTO embedding_jobs (document_id) VALUES (NEW.id)
    ON CONFLICT DO NOTHING;

  -- (4) 워커 깨우기 — **최적화이며 전달 보장 수단이 아니다** (ADR-009).
  --     OpenProxy 경유 시 LISTEN 동작이 문서로 보장되지 않으므로 워커는 폴링을 주
  --     경로로 삼는다. 이 알림이 통째로 유실돼도 파이프라인은 정상 동작한다.
  --     반대로 알림은 커밋 시에만 발행되므로, 롤백된 변경의 유령 이벤트도 없다.
  PERFORM pg_notify('embedding_jobs', NEW.id::text);

  -- AFTER 트리거의 반환값은 무시되지만, 함수 시그니처상 필요하다.
  RETURN NEW;
END; $$ LANGUAGE plpgsql;

