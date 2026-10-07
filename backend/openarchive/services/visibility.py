"""볼 수 없는 문서는 존재하지 않는 것처럼 제외한다 (ADR-027).

public은 조직(이 설치에 로그인한 사용자) 전체, private는 소유자와 부여 대상이 본다.
부여 대상은 사용자 본인이거나 사용자가 속한 그룹이다 (ADR-044).

공유 주체(share:<uuid>)는 외부 협업용 주체라 조직의 일원이 아니다. 그 공유에 부여된
문서만 보며, 조직 공개 문서라도 부여가 없으면 보지 못한다 (ADR-044 「공유」 결정 2).

폴더 (ADR-054): 열람 범위는 최상위 폴더만 갖고 하위 폴더는 그것을 따른다. 폴더 안의 「폴더
범위 따름」 문서는 최상위 폴더의 범위(조직 공개 · 만든 사람 · 사용자·그룹 부여)로, 「개별
지정」 문서는 문서 자신의 visibility·부여로 판정한다. 소유자는 어느 쪽이든 자기 문서를 본다.
실효 범위를 저장하지 않으므로 폴더 부여·그룹 구성원 변경은 다음 조회부터 반영된다. 공유 주체는
폴더를 보지 않는다 — 공유에 부여된 문서만 본다.

휴지통 (ADR-060 결정 1): 휴지통 문서는 소유자·공유 주체에게도 존재하지 않는다.
휴지통 조건은 모든 열람 분기 바깥에 두고, 복원 때 기존 청크·관계를 그대로 다시 읽는다.
"""

from uuid import UUID

VISIBILITY_VALUES: tuple[str, ...] = ("public", "private")

# 공유 주체 값의 접두사. users_reserved_share_prefix 제약(026)이 사용자명과의 충돌을 막는다.
SHARE_PRINCIPAL_PREFIX = "share:"


def share_principal(share_id: UUID) -> str:
    """공유를 열람 술어의 주체 값으로 바꾼다."""
    return f"{SHARE_PRINCIPAL_PREFIX}{share_id}"


# 시작 폴더({start})에서 parent_id를 따라 최상위까지 올라가, 최상위 폴더의 범위가 주체에게
# 열려 있는지 본다. 문서 술어와 폴더 술어가 시작 폴더만 바꿔 같은 조각을 쓴다.
#
# 형태는 상관 재귀 EXISTS다 — 시작점이 바깥 행 하나라 깊이만큼만 돈다. 폴더 조상을 비상관
# IN (SELECT …)으로 미리 펼치면 3천 청크에서 플래너가 HNSW를 버렸고 generic plan에서 15배
# 느려졌다(#187 스파이크). 같은 이유로 db.py의 prepare_threshold=None을 전제한다.
#
# RLS 전환(#98) 때: folders에 자기 참조 정책을 걸면 이 재귀가 그 정책을 다시 타
# "infinite recursion detected in policy"가 났다(스파이크) — 판정을 정책 없는 경로로 빼야 한다.
_FOLDER_UP = """WITH RECURSIVE folder_up AS (
        SELECT fa.id, fa.parent_id, fa.visibility, fa.created_by
        FROM folders fa WHERE fa.id = {start}
        UNION ALL
        SELECT fp.id, fp.parent_id, fp.visibility, fp.created_by
        FROM folders fp JOIN folder_up c ON fp.id = c.parent_id
    )"""

_ROOT_FOLDER_OPEN = """EXISTS (
    """ + _FOLDER_UP + """
    SELECT 1 FROM folder_up r
    WHERE r.parent_id IS NULL
      AND (r.visibility = 'public' OR r.created_by = %(user)s OR EXISTS (
          SELECT 1 FROM folder_grants fg JOIN users fu ON fu.username = %(user)s
          WHERE fg.folder_id = r.id
            AND (fg.user_id = fu.id OR EXISTS (
                SELECT 1 FROM group_members fm
                WHERE fm.group_id = fg.group_id AND fm.user_id = fu.id))
      ))
)"""


def root_folder_visibility(start: str) -> str:
    """시작 폴더의 최상위 폴더 공개범위를 내는 스칼라 서브쿼리. 열람 판정과 같은 거슬러 오르기 조각이다."""
    return "(" + _FOLDER_UP.format(start=start) + """
    SELECT r.visibility FROM folder_up r WHERE r.parent_id IS NULL)"""


# 쿼리에 그대로 끼워 넣는 SQL 조각. 바인딩 이름은 %(user)s로 고정하며, 값은 주체다 —
# 사용자명 · share:<uuid> · NULL(익명).
#
# 테이블과 주체 값 하나만 참조한다 — %(user)s를 current_setting('app.principal')로
# 바꾸면 그대로 RLS 정책의 USING 절이 되는 형태다 (ADR-044 결정 4, 전환은 #98).
#
# 공유 분기: 조직 공개·소유자·사용자·그룹·폴더 분기를 타지 않는다. 공유 id를 uuid로
# 캐스팅하지 않고 'share:' || s.id로 맞춘다 — 접두사를 뗀 값을 ::uuid로 바꾸면 사용자명이
# 들어온 커스텀 플랜에서 상수 접기가 그 캐스트를 미리 평가해 에러가 날 수 있다. 형식이 틀린
# 공유 값은 에러 없이 아무것도 보지 못한다.
#
# 폴더 분기: 「폴더 범위 따름」(folder_id가 있고 follows_folder)이면 최상위 폴더의 범위가
# 문서 자신의 visibility·부여를 대신한다 — 조직 공개 폴더 안의 private 문서도 조직 전체가 본다.
#
# 익명(None): NULL LIKE …는 NULL이라 CASE가 ELSE로 간다. username = NULL이 참이 될 수
# 없어 public(문서 또는 최상위 폴더)만 본다. 패턴의 %%는 바인딩 쿼리 안이라 이스케이프한 것이다.
NOT_TRASHED = "d.deleted_at IS NULL"

VISIBLE_TO_USER = NOT_TRASHED + """ AND (CASE WHEN %(user)s LIKE 'share:%%' THEN EXISTS (
    SELECT 1 FROM document_grants g JOIN shares s ON s.id = g.share_id
    WHERE g.document_id = d.id
      AND 'share:' || s.id::text = %(user)s
) ELSE (d.owner_id = %(user)s OR CASE
    WHEN d.folder_id IS NOT NULL AND d.follows_folder THEN """ + _ROOT_FOLDER_OPEN.format(
    start="d.folder_id"
) + """
    ELSE (d.visibility = 'public' OR EXISTS (
        SELECT 1 FROM document_grants g, users u
        WHERE g.document_id = d.id
          AND u.username = %(user)s
          AND (g.user_id = u.id
               OR g.group_id IN (SELECT m.group_id FROM group_members m WHERE m.user_id = u.id))
    )) END) END)"""

# 폴더 열람 술어(별칭 f). 볼 수 없는 폴더는 트리·목록·필터 어디에도 나오지 않는다 (ADR-027).
# 판정은 문서 술어의 폴더 분기와 같은 조각이다. 공유 주체는 폴더를 보지 않는다.
FOLDER_VISIBLE_TO_USER = """(CASE WHEN %(user)s LIKE 'share:%%' THEN false
ELSE """ + _ROOT_FOLDER_OPEN.format(start="f.id") + """ END)"""
