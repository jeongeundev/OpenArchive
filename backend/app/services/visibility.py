"""볼 수 없는 문서는 존재하지 않는 것처럼 제외한다 (ADR-027)."""

# 쿼리에 그대로 끼워 넣는 SQL 조각. 바인딩 이름은 %(user)s로 고정한다.
VISIBLE_TO_USER = "(d.visibility = 'public' OR d.owner_id = %(user)s)"
