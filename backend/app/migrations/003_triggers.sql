-- 003_triggers.sql — 문서 변경을 파이프라인 기동으로 바꾸는 트랜잭셔널 아웃박스 (ADR-001)
--
-- 이 파일이 "임베딩 갱신이 DB 계층에서 자동 트리거되고 원본-벡터 정합성이 유지된다"는
-- 주장의 본체다. 애플리케이션은 `documents`만 UPDATE하고, 버전 이력 기록과 잡 생성은
-- 전부 여기서 **같은 트랜잭션 안에** 일어난다. 그래서 문서만 커밋되고 이력이나 잡이
-- 유실되는 상태가 구조적으로 존재하지 않는다.
--
-- 잡에는 본문 페이로드를 담지 않는다. "이 문서는 재임베딩이 필요하다"는 신호뿐이라
-- (a) 연속 수정이 자연스럽게 하나로 합쳐지고 (b) 워커가 처리 시점의 최신 content를
-- 읽으므로 재처리가 항상 최신 상태로 수렴한다.

CREATE FUNCTION on_document_content_changed() RETURNS trigger AS $$
BEGIN
  -- (1) 버전 이력 기록.
  --     애플리케이션은 document_versions에 직접 INSERT하지 않는다 — embedding_jobs와
  --     같은 원칙이다. INSERT의 v1도 여기서 기록되므로 이력이 append-only로 완결되고,
  --     문서 생성 직후부터 v1 조회가 가능하다.
  --     ON CONFLICT는 재실행 안전장치다. 같은 버전 번호로 트리거가 두 번 발화해도
  --     (예: reembed 경로) 이력이 중복되지 않는다.
  INSERT INTO document_versions (document_id, version, content, content_hash)
  VALUES (NEW.id, NEW.version, NEW.content, NEW.content_hash)
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

-- 세 조건이 모두 설계의 일부다. 하나라도 빠지면 다음이 깨진다.
--
--   AFTER               — 아웃박스는 행이 확정된 뒤에 기록되어야 한다. BEFORE로 바꾸면
--                         제약 위반으로 취소될 행에 대해서도 이력·잡이 만들어진다.
--   UPDATE OF content_hash
--                       — 제목·태그만 바꾼 UPDATE로 재임베딩이 돌면 낭비이고, 본문이
--                         그대로인데 이력에 새 버전이 쌓여 이력 자체가 거짓말이 된다.
--                         값이 바뀌지 않아도 SET 절에 컬럼이 언급되면 발화한다는 것이
--                         PostgreSQL의 동작이며, 이것이 재임베딩 복구 경로
--                         (UPDATE documents SET content_hash = content_hash)의 근거다.
--                         애플리케이션이 embedding_jobs를 직접 건드리지 않고도 잡을
--                         만들 수 있는 유일한 수단이다.
--   pg_trigger_depth()  — (2)의 UPDATE가 같은 테이블을 건드리므로 재진입 방어를 둔다.
--                         지금은 SET 절에 content_hash가 없어 어차피 발화하지 않지만,
--                         나중에 이 함수나 다른 트리거가 content_hash를 만지게 되면
--                         이 조건 하나가 무한 재귀와의 차이가 된다.
CREATE TRIGGER trg_documents_content_changed
  AFTER INSERT OR UPDATE OF content_hash ON documents
  FOR EACH ROW
  WHEN (pg_trigger_depth() = 0)
  EXECUTE FUNCTION on_document_content_changed();
