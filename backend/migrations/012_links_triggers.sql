-- 012_links_triggers.sql — 대괄호 리터럴을 위키링크에서 제외한다 (#49)
--
-- 011의 `[^\[\]]+`는 대괄호가 아닌 문자면 무엇이든 제목으로 받았다. 기술 문서의 JSON
-- 중첩 배열(`[["192.168.64.4", 5432, "primary"]]`)이 정확히 이 패턴에 맞아 target_title이
-- 되고, 그런 제목의 문서는 없으니 깨진 링크가 된다. 사람이 고칠 수 없는 항목이
-- `/diagnostics`의 「깨진 링크」에 섞이면 목록 전체를 못 믿게 된다.
--
-- ⚠️ 가르는 것은 큰따옴표의 **존재가 아니라 위치**다. 제목에서 `"`를 통째로 배제하면
-- `ADR-015: 제품은 "AI를 위한 문서 저장소"이며, …`를 가리키는 정상 링크 3건이 함께
-- 죽는다(seed 실측 — 셋 다 해석되는 링크다). JSON 문자열 리터럴은 큰따옴표로 시작하고
-- 문서 제목은 그렇지 않으므로, 시작 위치만 본다.
--
-- 개행은 제목에서 뺀다. 링크가 줄을 넘도록 의도된 적이 없고, `documents.title`이 한 줄인
-- 이상 개행이 든 제목은 어차피 해석되지 않는다.
--
-- ⚠️ `WikilinkContent.tsx`가 이 두 규칙(패턴 + 시작 문자)을 그대로 복제한다. 어긋나면
-- 트리거가 저장하지 않은 제목을 화면이 깨진 링크로 그린다.
--
-- 011을 고치지 않고 파일을 새로 두는 이유: 러너가 `schema_migrations`에 파일명으로 적용
-- 이력을 남기므로(`app/migrations.py`), 이미 적용된 DB는 011을 다시 읽지 않는다.

CREATE OR REPLACE FUNCTION replace_document_links() RETURNS trigger AS $$
BEGIN
  DELETE FROM document_links WHERE src_document_id = NEW.id;

  INSERT INTO document_links (src_document_id, src_chunk_index, target_title)
  SELECT DISTINCT NEW.id, NULL::int, btrim(match[1])
  FROM regexp_matches(NEW.content, '\[\[([^\[\]\n]+)\]\]', 'g') AS parsed(match)
  WHERE btrim(match[1]) <> ''
    AND btrim(match[1]) NOT LIKE '"%'
  ON CONFLICT DO NOTHING;

  RETURN NEW;
END; $$ LANGUAGE plpgsql;

-- 트리거는 함수를 이름으로 참조하므로 재생성하지 않는다.

-- 기존 행은 트리거가 다시 만들어 주지 않는다 — `AFTER INSERT OR UPDATE OF content_hash`라
-- 본문이 바뀔 때만 돈다. 새 규칙으로 전체를 다시 만든다. document_links를 채우는 것은
-- 이 트리거뿐이므로(애플리케이션에 INSERT 경로가 없다) 통째로 지워도 잃는 것이 없다.
DELETE FROM document_links;

INSERT INTO document_links (src_document_id, src_chunk_index, target_title)
SELECT DISTINCT d.id, NULL::int, btrim(parsed.match[1])
FROM documents d
CROSS JOIN LATERAL regexp_matches(d.content, '\[\[([^\[\]\n]+)\]\]', 'g') AS parsed(match)
WHERE btrim(parsed.match[1]) <> ''
  AND btrim(parsed.match[1]) NOT LIKE '"%'
ON CONFLICT DO NOTHING;
