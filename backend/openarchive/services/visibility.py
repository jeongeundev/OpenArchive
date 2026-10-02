"""볼 수 없는 문서는 존재하지 않는 것처럼 제외한다 (ADR-027).

public은 조직(이 설치에 로그인한 사용자) 전체, private는 소유자와 부여 대상이 본다.
부여 대상은 사용자 본인이거나 사용자가 속한 그룹이다 (ADR-044).

공유 주체(share:<uuid>)는 외부 협업용 주체라 조직의 일원이 아니다. 그 공유에 부여된
문서만 보며, 조직 공개 문서라도 부여가 없으면 보지 못한다 (ADR-044 「공유」 결정 2).
"""

from uuid import UUID

VISIBILITY_VALUES: tuple[str, ...] = ("public", "private")

# 공유 주체 값의 접두사. users_reserved_share_prefix 제약(026)이 사용자명과의 충돌을 막는다.
SHARE_PRINCIPAL_PREFIX = "share:"


def share_principal(share_id: UUID) -> str:
    """공유를 열람 술어의 주체 값으로 바꾼다."""
    return f"{SHARE_PRINCIPAL_PREFIX}{share_id}"


# 쿼리에 그대로 끼워 넣는 SQL 조각. 바인딩 이름은 %(user)s로 고정하며, 값은 주체다 —
# 사용자명 · share:<uuid> · NULL(익명).
#
# 테이블과 주체 값 하나만 참조한다 — %(user)s를 current_setting('app.principal')로
# 바꾸면 그대로 RLS 정책의 USING 절이 되는 형태다 (ADR-044 결정 4, 전환은 #98).
#
# 공유 분기: 조직 공개·소유자·사용자·그룹 분기를 타지 않는다. 공유 id를 uuid로 캐스팅하지
# 않고 'share:' || s.id로 맞춘다 — 접두사를 뗀 값을 ::uuid로 바꾸면 사용자명이 들어온
# 커스텀 플랜에서 상수 접기가 그 캐스트를 미리 평가해 에러가 날 수 있다. 형식이 틀린
# 공유 값은 에러 없이 아무것도 보지 못한다.
#
# 익명(None): NULL LIKE …는 NULL이라 CASE가 ELSE로 간다. username = NULL이 참이 될 수
# 없어 public만 본다. 패턴의 %%는 바인딩 쿼리 안이라 이스케이프한 것이다.
VISIBLE_TO_USER = """(CASE WHEN %(user)s LIKE 'share:%%' THEN EXISTS (
    SELECT 1 FROM document_grants g JOIN shares s ON s.id = g.share_id
    WHERE g.document_id = d.id
      AND 'share:' || s.id::text = %(user)s
) ELSE (d.visibility = 'public' OR d.owner_id = %(user)s OR EXISTS (
    SELECT 1 FROM document_grants g, users u
    WHERE g.document_id = d.id
      AND u.username = %(user)s
      AND (g.user_id = u.id
           OR g.group_id IN (SELECT m.group_id FROM group_members m WHERE m.user_id = u.id))
)) END)"""
