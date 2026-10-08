-- 038_version_author_tables.sql — 텍스트 버전 작성자 스냅샷 (ADR-061 결정 6)
-- 사용자 삭제 뒤에도 이름이 남도록 author에는 FK를 걸지 않는다.
-- 이름 없는 워커와 직접 접속을 구분하기 위해 author_via를 별도로 저장한다.
-- 짝이 없는 칼럼 도입 이전 행의 NULL·NULL은 「기록 없음」이다.
-- 이후 버전의 직접 접속은 트리거(039)가 'direct'로 명시한다.

ALTER TABLE document_versions
  ADD COLUMN author text,
  ADD COLUMN author_via text,
  ADD CONSTRAINT document_versions_author_via_valid CHECK (author_via IN (
    'session', 'token', 'mcp', 'cli', 'share', 'worker', 'direct'
  ));

-- 두 시각은 DEFAULT now()라 같은 트랜잭션에서 기록됐을 때만 짝짓는다.
-- 스캔 문서 v1은 생성 뒤 다른 트랜잭션에서 쓰여 document_created와 짝이 없다.
-- 중복 짝이 있으면 가장 작은 감사 id를 선택해 UPDATE 결과를 결정적으로 만든다.
-- 감사 테이블은 읽기만 하며, 버전 UPDATE는 INSERT 전용 감사 트리거를 발화하지 않는다.
WITH matched AS (
  SELECT DISTINCT ON (v.document_id, v.version)
         v.document_id, v.version, a.actor, a.actor_via
    FROM document_versions v
    JOIN audit_log a ON a.document_id = v.document_id
                   AND a.occurred_at = v.created_at
   WHERE (v.version = 1 AND a.action = 'document_created')
      OR (v.version > 1 AND a.action = 'text_updated'
          AND (a.detail->>'version')::int = v.version)
   ORDER BY v.document_id, v.version, a.id
)
UPDATE document_versions v
   SET author = a.actor, author_via = COALESCE(a.actor_via, 'direct')
  FROM matched a
 WHERE a.document_id = v.document_id AND a.version = v.version;
