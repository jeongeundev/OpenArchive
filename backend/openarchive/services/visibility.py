"""볼 수 없는 문서는 존재하지 않는 것처럼 제외한다 (ADR-027).

public은 조직(이 설치에 로그인한 사용자) 전체, private는 소유자와 부여 대상이 본다.
부여 대상은 사용자 본인이거나 사용자가 속한 그룹이다 (ADR-044).
"""

VISIBILITY_VALUES: tuple[str, ...] = ("public", "private")

# 쿼리에 그대로 끼워 넣는 SQL 조각. 바인딩 이름은 %(user)s로 고정한다.
#
# 테이블과 주체 값 하나만 참조한다 — %(user)s를 current_setting('app.principal')로
# 바꾸면 그대로 RLS 정책의 USING 절이 되는 형태다 (ADR-044 결정 4, 전환은 #98).
# 익명(None)은 username = NULL이 참이 될 수 없어 public만 본다.
VISIBLE_TO_USER = """(d.visibility = 'public' OR d.owner_id = %(user)s OR EXISTS (
    SELECT 1 FROM document_grants g, users u
    WHERE g.document_id = d.id
      AND u.username = %(user)s
      AND (g.user_id = u.id
           OR g.group_id IN (SELECT m.group_id FROM group_members m WHERE m.user_id = u.id))
))"""
