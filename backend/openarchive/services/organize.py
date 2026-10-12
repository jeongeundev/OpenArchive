"""정리 추천 — 기존 서가에 새 책을 꽂아 주는 사서 (ADR-064).

저장된 관계 이웃(`document_edges` src ∪ dst)이 가장 많이 든 폴더를 제안만 한다. 옮기는
것은 사람이 기존 폴더 이동으로 한다. 이웃은 열람 술어를 통과한 문서만, 표와 분모는 볼 수 있는
폴더에 든 이웃만 센다 — 볼 수 없는 폴더의 이름이 제안으로 새지 않게 (ADR-027). 폴더 없는
이웃은 어느 쪽 근거도 아니다 — 함께 올린 새 문서끼리 서로 이웃이라, 그들을 분모에 넣으면
일괄 업로드에서 제안이 사라졌다.
"""

from dataclasses import dataclass
from uuid import UUID

import psycopg

from openarchive.services.documents import ensure_visible
from openarchive.services.folders import folder_path
from openarchive.services.visibility import FOLDER_VISIBLE_TO_USER, VISIBLE_TO_USER

# 폴더에 든 이웃의 이 비율 이상이자 이 표 수 이상이 한 폴더에 있어야 제안한다. 서울시설공단 규정
# 86건을 가리고 되맞힌 실측(notes/demo-data-20261012): 한 건씩이면 53건 제안·73% 정답, 절반을
# 동시에 가리면 51% 제안·64%. 최소 표 수가 없으면 이웃 하나로 제안해 절반 동시에서 58%였다.
SUGGESTION_MIN_SHARE = 0.5
SUGGESTION_MIN_VOTES = 2
UNFILED_LIMIT = 50

# targets는 호출부가 채운다. 문서 술어는 별칭 d, 폴더 술어는 별칭 f를 쓴다.
_SUGGESTION_SQL = """
WITH targets AS ({targets}),
pairs AS (
  SELECT t.id AS target_id, e.dst_document_id AS other_id, e.score
  FROM targets t JOIN document_edges e ON e.src_document_id = t.id
  UNION ALL
  -- 역방향 — 남이 발견한 관계도 이웃이다 (ADR-029 개정, 저장은 단방향).
  SELECT t.id, e.src_document_id, e.score
  FROM targets t JOIN document_edges e ON e.dst_document_id = t.id
),
neighbours AS (
  SELECT target_id, other_id, max(score) AS score FROM pairs GROUP BY 1, 2
),
visible_neighbours AS (
  SELECT n.target_id, n.score, d.folder_id
  FROM neighbours n JOIN documents d ON d.id = n.other_id
  WHERE {visible_document}
),
votes AS (
  SELECT v.target_id, f.id AS folder_id, count(*) AS votes, sum(v.score) AS score_sum
  FROM visible_neighbours v JOIN folders f ON f.id = v.folder_id
  WHERE {visible_folder}
  GROUP BY 1, 2
),
totals AS (
  SELECT target_id, sum(votes)::int AS total FROM votes GROUP BY 1
),
best AS (
  SELECT DISTINCT ON (target_id) target_id, folder_id, votes
  FROM votes ORDER BY target_id, votes DESC, score_sum DESC, folder_id
)
SELECT t.id, t.title, t.folder_id, b.folder_id, b.votes, tot.total, t.total_count
FROM targets t
LEFT JOIN best b ON b.target_id = t.id
LEFT JOIN totals tot ON tot.target_id = t.id
ORDER BY t.created_at DESC, t.id
"""

_ONE_TARGET = """
  SELECT d.id, d.title, d.folder_id, d.created_at, 1 AS total_count
  FROM documents d WHERE d.id = %(id)s
"""

# 옮길 수 있는 것은 소유자뿐이라(move_document) 내 문서만 모은다.
_UNFILED_TARGETS = f"""
  SELECT d.id, d.title, d.folder_id, d.created_at, count(*) OVER () AS total_count
  FROM documents d
  WHERE d.owner_id = %(user)s AND d.folder_id IS NULL AND {VISIBLE_TO_USER}
  ORDER BY d.created_at DESC, d.id
  LIMIT %(limit)s
"""


def _sql(targets: str) -> str:
    return _SUGGESTION_SQL.format(
        targets=targets,
        visible_document=VISIBLE_TO_USER,
        visible_folder=FOLDER_VISIBLE_TO_USER,
    )


@dataclass(frozen=True)
class FolderSuggestion:
    folder_id: UUID
    path: list[dict]
    votes: int
    total: int


@dataclass(frozen=True)
class UnfiledDocument:
    document_id: UUID
    title: str
    suggestion: FolderSuggestion | None


@dataclass(frozen=True)
class UnfiledResult:
    count: int
    items: list[UnfiledDocument]


async def _suggestion(conn, row) -> FolderSuggestion | None:
    _, _, current_folder, folder_id, votes, total, _ = row
    if (
        folder_id is None
        or folder_id == current_folder
        or votes < SUGGESTION_MIN_VOTES
        or votes < total * SUGGESTION_MIN_SHARE
    ):
        return None
    path = [{"id": item["id"], "name": item["name"]} for item in await folder_path(conn, folder_id)]
    return FolderSuggestion(folder_id=folder_id, path=path, votes=votes, total=total)


async def suggest_folder(
    conn: psycopg.AsyncConnection, *, document_id: UUID, user_id: str | None
) -> FolderSuggestion | None:
    """폴더에 든 관계 이웃의 절반 이상이 모인 폴더를 제안한다. 지금 폴더와 같거나 그런 폴더가 없으면 None."""
    params = {"id": document_id, "user": user_id}
    async with conn.transaction():
        await ensure_visible(conn, document_id, user_id=user_id)
        row = await (await conn.execute(_sql(_ONE_TARGET), params)).fetchone()
        return await _suggestion(conn, row)


async def list_unfiled(conn: psycopg.AsyncConnection, *, user_id: str) -> UnfiledResult:
    """폴더 없는 내 문서를 최근 것부터, 문서마다 제안 폴더와 함께 반환한다."""
    params = {"user": user_id, "limit": UNFILED_LIMIT}
    async with conn.transaction():
        rows = await (await conn.execute(_sql(_UNFILED_TARGETS), params)).fetchall()
        items = [
            UnfiledDocument(document_id=row[0], title=row[1], suggestion=await _suggestion(conn, row))
            for row in rows
        ]
    return UnfiledResult(count=rows[0][6] if rows else 0, items=items)
