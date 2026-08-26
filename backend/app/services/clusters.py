"""열람 가능한 문서를 주제 덩어리로 묶고 관계를 집계한다."""

from collections import Counter, defaultdict
from dataclasses import dataclass
from uuid import UUID

import networkx as nx
import psycopg
from networkx.algorithms.community import louvain_communities

from app.services.visibility import VISIBLE_TO_USER

MAX_CLUSTERS = 20
LOUVAIN_SEED = 42
LOUVAIN_RESOLUTION = 1.0
UNCATEGORIZED = "미분류"
OTHER = "기타"
TAGGED = "tagged"
COMMUNITY = "community"
BUCKET = "bucket"
ClusterKey = tuple[str, str]

VISIBLE_DOCUMENTS_SQL = f"""
SELECT d.id, d.title, d.tags
FROM documents d
WHERE {VISIBLE_TO_USER}
ORDER BY d.id
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
    return key[1]


def _assign_communities(
    documents: list[tuple[UUID, str, list[str]]],
    pairs: set[tuple[UUID, UUID]],
) -> dict[UUID, ClusterKey]:
    """열람 가능한 무가중 관계 그래프를 결정론적인 Louvain 덩어리로 나눈다."""
    document_by_id = {
        document_id: (title, tags) for document_id, title, tags in documents
    }
    connected_ids = {document_id for pair in pairs for document_id in pair}
    graph = nx.Graph()
    graph.add_nodes_from(sorted(connected_ids))
    graph.add_edges_from(sorted(pairs))

    communities = (
        [
            sorted(community)
            for community in louvain_communities(
                graph,
                weight=None,
                resolution=LOUVAIN_RESOLUTION,
                seed=LOUVAIN_SEED,
            )
        ]
        if graph
        else []
    )

    labeled: list[tuple[list[UUID], str, str, str]] = []
    for members in communities:
        tag_counts = Counter(
            tag for document_id in members for tag in document_by_id[document_id][1]
        )
        first_title = min(document_by_id[document_id][0] for document_id in members)
        if tag_counts:
            label = min(tag_counts, key=lambda tag: (-tag_counts[tag], tag))
            if label in {UNCATEGORIZED, OTHER}:
                label = f"{label} (태그)"
            kind = TAGGED
        else:
            label = min(
                members,
                key=lambda document_id: (
                    -graph.degree[document_id],
                    document_by_id[document_id][0],
                    document_id,
                ),
            )
            label = document_by_id[label][0]
            kind = COMMUNITY
        labeled.append((members, kind, label, first_title))

    # 같은 이름은 큰 덩어리부터 기본 이름을 쓰고 이후에 번호를 붙인다.
    occurrences: Counter[str] = Counter()
    labeled.sort(key=lambda item: (-len(item[0]), item[3]))
    assigned: dict[UUID, ClusterKey] = {}
    for members, kind, label, _ in labeled:
        occurrences[label] += 1
        suffix = f" ({occurrences[label]})" if occurrences[label] > 1 else ""
        key = (kind, f"{label}{suffix}")
        assigned.update(dict.fromkeys(members, key))

    for document_id, _, _ in documents:
        assigned.setdefault(document_id, (BUCKET, UNCATEGORIZED))

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

    document_pairs = {
        tuple(sorted((source_id, target_id))) for source_id, target_id in edge_rows
    }
    assigned = _assign_communities(document_rows, document_pairs)
    documents_by_cluster: dict[ClusterKey, list[ClusterDocument]] = defaultdict(list)
    for document_id, title, _ in document_rows:
        documents_by_cluster[assigned[document_id]].append(
            ClusterDocument(document_id=document_id, title=title)
        )
    for documents in documents_by_cluster.values():
        documents.sort(key=lambda document: (document.title, document.document_id))

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
