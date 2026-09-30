-- 023_links_triggers.sql — 표 셀 이스케이프 `[[제목\|별칭]]`의 `\|`도 별칭 구분자로 본다 (#112)
--
-- Obsidian은 마크다운 표 셀 안에서 `|`가 열 구분자와 겹치므로 `[[제목\|별칭]]`으로 적는다.
-- 015는 첫 `|`에서 잘라 `제목\`을 저장했고, 코퍼스 A의 미해결 링크 53행 중 15행이 그렇게
-- 끝났다(전부 실재 문서). 절이 있는 `[[A#b\|c]]`는 `\`가 절과 함께 잘려 우연히 풀렸다.
--
-- 정규화 전에 `\|`를 `|`로 바꾸는 것 말고는 015와 같다. `frontend/src/lib/wikilink.ts`가
-- 같은 규칙을 복제한다 — 한쪽만 바뀌면 API가 해석한 정상 링크를 화면이 깨진 링크로 그린다.
--
-- 015를 고치지 않고 파일을 새로 두는 이유는 015와 같다: 러너가 파일명으로 적용 이력을 남긴다.

CREATE OR REPLACE FUNCTION wikilink_targets(content text) RETURNS SETOF text
  LANGUAGE sql IMMUTABLE STRICT AS $$
  WITH parsed AS (
    SELECT match[1] AS embed_marker, btrim(replace(match[2], '\|', '|')) AS raw_target
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

-- 트리거 함수 replace_document_links()는 wikilink_targets를 이름으로 부르므로 그대로 둔다.

-- 기존 행은 본문이 바뀔 때만 트리거가 다시 만든다. 새 규칙으로 전체를 다시 만든다.
DELETE FROM document_links;

INSERT INTO document_links (src_document_id, src_chunk_index, target_title)
SELECT d.id, NULL::int, target
FROM documents d
CROSS JOIN LATERAL wikilink_targets(d.content) AS target
ON CONFLICT DO NOTHING;
