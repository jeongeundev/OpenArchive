import asyncio

import psycopg
import pytest
from conftest import insert_test_document, login_as
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def logged_in(db_client: TestClient):
    """덩어리 조회도 로그인을 요구한다 (ADR-028). 익명 경계는 아래에서 따로 단언한다."""
    login_as(db_client, "alice")


def test_clusters_require_login(db_client: TestClient):
    db_client.post("/api/auth/logout")
    assert db_client.get("/api/clusters").status_code == 401


def _insert_document(dsn: str, **kwargs) -> str:
    async def insert() -> str:
        async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
            return str(await insert_test_document(conn, **kwargs))

    return asyncio.run(insert())


def _insert_edge(dsn: str, source_id: str, target_id: str) -> None:
    with psycopg.connect(dsn) as conn:
        conn.execute(
            """
            INSERT INTO document_edges
                (src_document_id, dst_document_id, kind, score)
            VALUES (%s, %s, 'related', 0.8)
            """,
            (source_id, target_id),
        )


def _insert_bidirectional_edge(dsn: str, first_id: str, second_id: str) -> None:
    _insert_edge(dsn, first_id, second_id)
    _insert_edge(dsn, second_id, first_id)


def _connect_all(dsn: str, document_ids: list[str]) -> None:
    for index, first_id in enumerate(document_ids):
        for second_id in document_ids[index + 1 :]:
            _insert_bidirectional_edge(dsn, first_id, second_id)


def _cluster_map(response) -> dict[str, dict]:
    return {cluster["name"]: cluster for cluster in response.json()["clusters"]}


def test_connected_documents_form_one_cluster_named_by_top_tag(
    db_client: TestClient, migrated_db: str
):
    document_ids = [
        _insert_document(migrated_db, title="검색 A", content="검색 A", tags=["검색", "공통"]),
        _insert_document(migrated_db, title="검색 B", content="검색 B", tags=["검색"]),
        _insert_document(migrated_db, title="DB", content="DB", tags=["데이터베이스"]),
    ]
    _connect_all(migrated_db, document_ids)

    body = db_client.get("/api/clusters").json()

    assert body["clusters"] == [
        {
            "name": "검색",
            "size": 3,
            "documents": [
                {"document_id": document_ids[2], "title": "DB"},
                {"document_id": document_ids[0], "title": "검색 A"},
                {"document_id": document_ids[1], "title": "검색 B"},
            ],
        }
    ]
    assert body["connections"] == []


def test_two_dense_groups_joined_by_one_edge_become_two_clusters_with_one_connection(
    db_client: TestClient, migrated_db: str
):
    search_ids = [
        _insert_document(
            migrated_db, title=f"검색 {index}", content=f"검색 {index}", tags=["검색"]
        )
        for index in range(3)
    ]
    database_ids = [
        _insert_document(
            migrated_db,
            title=f"데이터베이스 {index}",
            content=f"데이터베이스 {index}",
            tags=["데이터베이스"],
        )
        for index in range(3)
    ]
    _connect_all(migrated_db, search_ids)
    _connect_all(migrated_db, database_ids)
    _insert_bidirectional_edge(migrated_db, search_ids[0], database_ids[0])

    body = db_client.get("/api/clusters").json()
    clusters = {cluster["name"]: cluster["size"] for cluster in body["clusters"]}

    assert clusters == {"검색": 3, "데이터베이스": 3}
    assert body["connections"] == [
        {"source": "검색", "target": "데이터베이스", "count": 1}
    ]


def test_documents_without_visible_edges_go_to_uncategorized(
    db_client: TestClient, migrated_db: str
):
    _insert_document(migrated_db, title="태그 있음", content="태그 있음", tags=["검색"])
    _insert_document(migrated_db, title="태그 없음", content="태그 없음")

    body = db_client.get("/api/clusters").json()

    assert body["clusters"][0]["name"] == "미분류"
    assert body["clusters"][0]["size"] == 2
    assert body["connections"] == []


def test_private_documents_do_not_shape_other_users_clusters(
    db_client: TestClient, migrated_db: str
):
    public_groups = []
    for prefix in ("A", "B"):
        group = [
            _insert_document(
                migrated_db,
                title=f"{prefix}-{index}",
                content=f"{prefix}-{index}",
                tags=[prefix],
            )
            for index in range(3)
        ]
        _connect_all(migrated_db, group)
        public_groups.append(group)
    private_id = _insert_document(
        migrated_db,
        title="비공개 다리",
        content="비공개 다리",
        owner_id="alice",
        visibility="private",
    )
    _insert_bidirectional_edge(migrated_db, private_id, public_groups[0][0])
    _insert_bidirectional_edge(migrated_db, private_id, public_groups[1][0])

    login_as(db_client, "bob")
    bob_body = db_client.get("/api/clusters").json()

    assert len(bob_body["clusters"]) == 2
    assert sum(cluster["size"] for cluster in bob_body["clusters"]) == 6
    assert bob_body["connections"] == []

    login_as(db_client, "alice")
    alice_body = db_client.get("/api/clusters").json()
    assert sum(cluster["size"] for cluster in alice_body["clusters"]) == 7
    # 다리가 보이는 사람에게는 덩어리 사이 연결도 보인다. bob의 빈 connections가
    # "연결 집계가 늘 비어 있다"가 아니라 "다리가 열람 범위 밖이다"임을 여기서 고정한다.
    assert [item["count"] for item in alice_body["connections"]] == [1]


def test_cluster_count_is_capped_and_small_clusters_are_merged_into_other(
    db_client: TestClient, migrated_db: str
):
    for index in range(22):
        pair = [
            _insert_document(
                migrated_db,
                title=f"주제 {index:02d}-{suffix}",
                content=f"서로 다른 내용 {index}-{suffix}",
                tags=[f"태그-{index:02d}"],
            )
            for suffix in ("A", "B")
        ]
        _insert_bidirectional_edge(migrated_db, *pair)

    clusters = db_client.get("/api/clusters").json()["clusters"]

    assert len(clusters) == 20
    assert sum(cluster["size"] for cluster in clusters) == 44
    assert next(cluster for cluster in clusters if cluster["name"] == "기타")["size"] == 6


def test_reserved_bucket_names_do_not_merge_with_same_named_tags(
    db_client: TestClient, migrated_db: str
):
    tagged_pair = [
        _insert_document(
            migrated_db,
            title=f"명시적 미분류 {index}",
            content=f"명시적 미분류 {index}",
            tags=["미분류"],
        )
        for index in range(2)
    ]
    _insert_bidirectional_edge(migrated_db, *tagged_pair)
    _insert_document(migrated_db, title="edge 없음", content="edge 없음")

    clusters = _cluster_map(db_client.get("/api/clusters"))

    assert clusters["미분류 (태그)"]["size"] == 2
    assert clusters["미분류"]["size"] == 1


def test_duplicate_top_tags_get_numbered_names(db_client: TestClient, migrated_db: str):
    triangle = [
        _insert_document(
            migrated_db, title=f"삼각형 {index}", content=f"삼각형 {index}", tags=["공통"]
        )
        for index in range(3)
    ]
    pair = [
        _insert_document(migrated_db, title=f"쌍 {index}", content=f"쌍 {index}", tags=["공통"])
        for index in range(2)
    ]
    _connect_all(migrated_db, triangle)
    _insert_bidirectional_edge(migrated_db, *pair)

    clusters = _cluster_map(db_client.get("/api/clusters"))

    assert clusters["공통"]["size"] == 3
    assert clusters["공통 (2)"]["size"] == 2


def test_untagged_community_is_named_after_its_best_connected_document(
    db_client: TestClient, migrated_db: str
):
    center = _insert_document(migrated_db, title="중심 문서", content="중심 문서")
    ring = [
        _insert_document(migrated_db, title=title, content=title)
        for title in ("가 문서", "나 문서", "다 문서", "라 문서")
    ]
    # 허브 + 링(바퀴 모양). 허브만 차수 4로 유일한 최대이고 한 덩어리로 남는다. 삼각형에
    # 문서 하나를 매단 모양은 Louvain이 두 덩어리로 쪼개므로 이름 규칙을 가리지 못한다.
    for index, spoke in enumerate(ring):
        _insert_bidirectional_edge(migrated_db, center, spoke)
        _insert_bidirectional_edge(migrated_db, spoke, ring[(index + 1) % len(ring)])

    clusters = db_client.get("/api/clusters").json()["clusters"]

    assert len(clusters) == 1
    assert clusters[0]["size"] == 5
    assert clusters[0]["name"] == "중심 문서"


def test_result_is_deterministic_across_calls(db_client: TestClient, migrated_db: str):
    groups = []
    for group_index in range(3):
        group = [
            _insert_document(
                migrated_db,
                title=f"그룹 {group_index}-{index}",
                content=f"그룹 {group_index}-{index}",
                tags=[f"태그-{group_index}"],
            )
            for index in range(3)
        ]
        _connect_all(migrated_db, group)
        groups.append(group)
    _insert_bidirectional_edge(migrated_db, groups[0][0], groups[1][0])
    _insert_bidirectional_edge(migrated_db, groups[1][1], groups[2][0])

    first = db_client.get("/api/clusters").json()
    second = db_client.get("/api/clusters").json()

    assert first == second


def test_untagged_community_named_after_reserved_title_stays_separate_from_bucket(
    db_client: TestClient, migrated_db: str
):
    """제목이 예약 이름과 겹쳐도 덩어리 이름은 버킷과 구분된다.

    화면은 덩어리 이름을 식별자로 쓰므로(원 하나, 선 하나) 두 덩어리가 같은 이름으로
    나가면 원이 겹쳐 그려지고 안내 문구가 엉뚱한 덩어리에 붙는다.
    """
    community = [
        _insert_document(migrated_db, title=title, content=title)
        for title in ("미분류", "이어진 문서")
    ]
    _insert_bidirectional_edge(migrated_db, *community)
    _insert_document(migrated_db, title="edge 없음", content="edge 없음")

    response = db_client.get("/api/clusters")
    names = [cluster["name"] for cluster in response.json()["clusters"]]

    assert len(names) == len(set(names))
    clusters = _cluster_map(response)
    assert clusters["미분류 (문서)"]["size"] == 2
    assert clusters["미분류"]["size"] == 1


def test_community_name_uses_degree_inside_the_community(
    db_client: TestClient, migrated_db: str
):
    """이름을 정하는 차수는 군집 안에서 센다 (ADR-042 결정 4).

    군집 밖으로 뻗은 edge까지 세면 다른 덩어리와 이어졌다는 이유만으로 대표가 바뀐다.
    """
    untagged = [
        _insert_document(migrated_db, title=title, content=title)
        for title in ("하 문서", "나 문서", "다 문서")
    ]
    _connect_all(migrated_db, untagged)
    tagged = [
        _insert_document(
            migrated_db, title=f"태그 {index}", content=f"태그 {index}", tags=["와이"]
        )
        for index in range(4)
    ]
    _connect_all(migrated_db, tagged)
    _insert_bidirectional_edge(migrated_db, untagged[0], tagged[0])

    clusters = _cluster_map(db_client.get("/api/clusters"))

    # 군집 안 차수는 셋 다 2로 동률이라 제목순 최소인 "나 문서"가 이름이 된다.
    # 군집 밖 edge까지 세면 차수 3인 "하 문서"가 뽑힌다.
    assert clusters["나 문서"]["size"] == 3
    assert clusters["와이"]["size"] == 4
