"""번호 붙은 raw SQL 파일을 순서대로 적용하는 소형 러너 (ADR-005).

호출 주체는 둘이다 — 상시 프로세스 중에서는 API 서버 하나, 그리고 운영자가 명시적으로
부르는 `openarchive init` (ADR-012 개정, ADR-038). 워커·MCP 서버는 스키마가 이미 준비된
것으로 가정하고 이 모듈을 부르지 않는다 — 상시 프로세스 셋이 같은 마이그레이션을 경쟁
실행하는 상황 자체를 없애는 것이 분산 락보다 단순하다. init은 일회성 동기 명령이라 그
경쟁에 들어가지 않는다.

커넥션은 여기서 직접 열고 닫는다. `app.db`의 풀을 쓰지 않는 이유는 마이그레이션이
기동 시 1회성 작업이라 풀의 수명주기와 얽힐 이유가 없고, 얽히면 `db.py`의
"import 부작용 없음" 성질이 흐려지기 때문이다.
"""

from pathlib import Path

import psycopg

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"

# 이력 테이블은 러너가 만든다. 마이그레이션 파일로 두면 자기 자신을 기록할 테이블이
# 없는 상태를 먼저 풀어야 한다.
_CREATE_HISTORY = """
CREATE TABLE IF NOT EXISTS schema_migrations (
  filename   text PRIMARY KEY,
  applied_at timestamptz NOT NULL DEFAULT now()
)
"""


async def run_migrations(dsn: str, migrations_dir: Path = MIGRATIONS_DIR) -> list[str]:
    """아직 적용되지 않은 마이그레이션을 파일명 순으로 적용하고, 적용한 파일명을 반환한다.

    파일명은 `001_`, `002_` 형태의 3자리 zero-padding을 규약으로 하므로 사전순이 곧
    번호순이다. 실패하면 예외를 그대로 올린다 — 부분 적용된 스키마 위에서 애플리케이션이
    도는 것이 기동 실패보다 훨씬 위험하다.
    """
    files = sorted(migrations_dir.glob("*.sql"), key=lambda p: p.name)
    applied: list[str] = []

    async with await psycopg.AsyncConnection.connect(dsn) as conn:
        await conn.execute(_CREATE_HISTORY)
        await conn.commit()

        cur = await conn.execute("SELECT filename FROM schema_migrations")
        done = {row[0] for row in await cur.fetchall()}

        for path in files:
            if path.name in done:
                continue

            # 파일 하나 = 트랜잭션 하나. SQL 적용과 이력 기록이 갈라지면 다음 실행이
            # 같은 파일을 다시 적용하거나 영영 건너뛴다.
            await conn.execute(path.read_text(encoding="utf-8"))
            await conn.execute(
                "INSERT INTO schema_migrations (filename) VALUES (%s)", (path.name,)
            )
            await conn.commit()
            applied.append(path.name)

    return applied
