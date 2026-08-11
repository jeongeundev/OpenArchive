"""열람 가능한 문서를 주제 덩어리로 묶고 관계를 집계한다."""

from collections import Counter, defaultdict
from dataclasses import dataclass
from uuid import UUID

import psycopg

from app.services.visibility import VISIBLE_TO_USER

MAX_CLUSTERS = 20
UNCATEGORIZED = "미분류"
OTHER = "기타"
TAGGED = "tagged"
BUCKET = "bucket"
ClusterKey = tuple[str, str]

VISIBLE_DOCUMENTS_SQL = f"""
SELECT d.id, d.title, d.tags
FROM documents d
WHERE {VISIBLE_TO_USER}
ORDER BY d.title, d.id
"""

VISIBLE_EDGES_SQL = f"""
WITH visible_documents AS (
  SELECT d.id
  FROM documents d
  WHERE {VISIBLE_TO_USER}
)
SELECT e.src_document_id, e.dst_document_id
FROM document_edges e
JOIN visible_documents src ON src.id = e.src_document_id
JOIN visible_documents dst ON dst.id = e.dst_document_id
"""


@dataclass(frozen=True)
class ClusterDocument:
    document_id: UUID
    title: str


@dataclass(frozen=True)
class Cluster:
    name: str
    size: int
    documents: list[ClusterDocument]


@dataclass(frozen=True)
class ClusterConnection:
    source: str
    target: str
    count: int


@dataclass(frozen=True)
class ClusterResult:
    clusters: list[Cluster]
    connections: list[ClusterConnection]


def _display_name(key: ClusterKey) -> str:
    kind, name = key
    if kind == TAGGED and name in {UNCATEGORIZED, OTHER}:
        return f"{name} (태그)"
    return name


def _assign_clusters(rows: list[tuple]) -> dict[UUID, ClusterKey]:
    tag_counts = Counter(tag for _, _, tags in rows for tag in tags)
    # 여러 태그 중 저장소에서 가장 자주 쓰인 태그는 사용자가 이미 만든 주제 축을
    # 재사용하면서 별도 클러스터링 계산이 필요 없다. 동률은 이름순으로 고정해 결과를
    # 결정론적으로 유지한다. 무태그 문서는 추정 연산 없이 진단 화면과 같은 미분류로 둔다.
    assigned = {
        document_id: (
            (TAGGED, min(tags, key=lambda tag: (-tag_counts[tag], tag)))
            if tags
            else (BUCKET, UNCATEGORIZED)
        )
        for document_id, _, tags in rows
    }
    cluster_sizes = Counter(assigned.values())
    uncategorized_key = (BUCKET, UNCATEGORIZED)
    named = [key for key in cluster_sizes if key != uncategorized_key]
    named.sort(key=lambda key: (-cluster_sizes[key], key[1]))

    reserved = 1 if uncategorized_key in cluster_sizes else 0
    if len(cluster_sizes) > MAX_CLUSTERS:
        keep_count = MAX_CLUSTERS - reserved - 1
        kept = set(named[:keep_count])
        assigned = {
            document_id: key
            if key == uncategorized_key or key in kept
            else (BUCKET, OTHER)
            for document_id, key in assigned.items()
        }
    return assigned


async def get_clusters(
    conn: psycopg.AsyncConnection, *, user_id: str | None = None
) -> ClusterResult:
    """현재 사용자가 볼 수 있는 문서만으로 덩어리와 연결을 반환한다."""
    params = {"user": user_id}
    async with conn.transaction():
        document_rows = await (
            await conn.execute(VISIBLE_DOCUMENTS_SQL, params)
        ).fetchall()
        edge_rows = await (await conn.execute(VISIBLE_EDGES_SQL, params)).fetchall()

    assigned = _assign_clusters(document_rows)
    documents_by_cluster: dict[ClusterKey, list[ClusterDocument]] = defaultdict(list)
    for document_id, title, _ in document_rows:
        documents_by_cluster[assigned[document_id]].append(
            ClusterDocument(document_id=document_id, title=title)
        )

    # 선의 굵기는 "덩어리 사이를 잇는 문서쌍이 몇 개인가"다. 트리거가 관계 하나를
    # 양방향 두 행으로 저장하므로(ADR-029) 원시 edge를 세면 모든 값이 일률적으로
    # 2배가 되어 굵기의 의미가 없다. 문서쌍 단위로 접어 실제 연결 수를 센다.
    connection_counts: Counter[tuple[ClusterKey, ClusterKey]] = Counter()
    seen_document_pairs: set[tuple[UUID, UUID]] = set()
    for source_id, target_id in edge_rows:
        document_pair = tuple(sorted((source_id, target_id)))
        if document_pair in seen_document_pairs:
            continue
        seen_document_pairs.add(document_pair)
        source = assigned[source_id]
        target = assigned[target_id]
        if source == target:
            continue
        pair = tuple(sorted((source, target)))
        connection_counts[pair] += 1

    clusters = [
        Cluster(name=_display_name(key), size=len(documents), documents=documents)
        for key, documents in documents_by_cluster.items()
    ]
    clusters.sort(key=lambda cluster: (-cluster.size, cluster.name))
    connections = [
        ClusterConnection(
            source=_display_name(source), target=_display_name(target), count=count
        )
        for (source, target), count in sorted(connection_counts.items())
    ]
    return ClusterResult(clusters=clusters, connections=connections)
