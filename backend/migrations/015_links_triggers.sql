-- 015_links_triggers.sql — Obsidian 위키링크 대상을 제목으로 정규화한다 (#93 L1·L2, #103)
--
-- 코퍼스 A의 링크 1,619행 중 1,095행(68%)이 해석되지 않았고, 그중 701행(43%)은
-- 별칭·절·경로를 제거하면 존재하는 문서였다(alias 414 · section 287). 링크 추출과
-- 정규화를 한 함수에 두어 트리거와 기존 행 전량 재생성이 같은 규칙을 사용하게 한다.
-- `![[…]]` 임베드 197건과 미디어 첨부는 링크가 아니므로 제외하되, PDF·ZIP 같은 문서
-- 형식은 사람이 고칠 수 있는 깨진 링크로 남긴다.
--
-- 정규화 순서: 양끝 공백 → `|` 별칭 → `#` 절 → 마지막 `/` 앞 경로 → 양끝 공백.
-- 정규화 전에는 임베드·빈 값·큰따옴표 시작(JSON 리터럴)을, 정규화 뒤에는 빈 값과
-- png/jpeg/gif/svg/webp/mp4/mov/mp3 미디어 확장자를 제외한다. 동일 제목은 DISTINCT로 접는다.
--
-- 012를 고치지 않고 파일을 새로 두는 이유: 러너가 `schema_migrations`에 파일명으로 적용
-- 이력을 남기므로(`app/migrations.py`), 이미 적용된 DB는 012를 다시 읽지 않는다.

CREATE FUNCTION wikilink_targets(content text) RETURNS SETOF text
  LANGUAGE sql IMMUTABLE STRICT AS $$
  WITH parsed AS (
    SELECT match[1] AS embed_marker, btrim(match[2]) AS raw_target
    FROM regexp_matches(content, '(!?)\[\[([^\[\]\n]+)\]\]', 'g') AS matches(match)
  ),
  normalized AS (
    SELECT btrim(
             regexp_replace(
               split_part(split_part(raw_target, '|', 1), '#', 1),
               '^.*/',
               ''
             )
           ) AS target
    FROM parsed
    WHERE embed_marker <> '!'
      AND raw_target <> ''
      AND raw_target NOT LIKE '"%'
  )
  SELECT DISTINCT target
  FROM normalized
  WHERE target <> ''
    AND target !~* '\.(png|jpe?g|gif|svg|webp|mp4|mov|mp3)$';
$$;

CREATE OR REPLACE FUNCTION replace_document_links() RETURNS trigger AS $$
BEGIN
  DELETE FROM document_links WHERE src_document_id = NEW.id;

  INSERT INTO document_links (src_document_id, src_chunk_index, target_title)
  SELECT NEW.id, NULL::int, target
  FROM wikilink_targets(NEW.content) AS target
  ON CONFLICT DO NOTHING;

  RETURN NEW;
END; $$ LANGUAGE plpgsql;

-- 트리거는 함수를 이름으로 참조하므로 재생성하지 않는다.

-- 기존 행은 트리거가 다시 만들어 주지 않는다 — `AFTER INSERT OR UPDATE OF content_hash`라
-- 본문이 바뀔 때만 돈다. 새 규칙으로 전체를 다시 만든다.
DELETE FROM document_links;

INSERT INTO document_links (src_document_id, src_chunk_index, target_title)
SELECT d.id, NULL::int, target
FROM documents d
CROSS JOIN LATERAL wikilink_targets(d.content) AS target
ON CONFLICT DO NOTHING;
