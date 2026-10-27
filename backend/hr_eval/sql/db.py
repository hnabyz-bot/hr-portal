"""DB 연결과 스키마 적용.

서버는 시작할 때 init_db 를 부른다(app.py). DB 파일은 docker-compose.yml 이
서버 폴더 data/ 에 보관하므로 merge·재빌드 뒤에도 남는다.

스키마 파일은 번호를 붙인다(001_init.sql, 002_….sql …). DB 안의 PRAGMA user_version 이
"몇 번까지 넣었는지"를 기억해서, 아직 안 넣은 번호만 차례로 넣는다.
DB 에 자료가 생긴 뒤 표를 바꿀 때는 001 을 고치지 말고 다음 번호 파일을 더한다.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SQL_DIR = Path(__file__).parent


def connect(path: str) -> sqlite3.Connection:
    """운영 코드가 써야 할 연결 방식.

    PRAGMA 는 트랜잭션 안에서 조용히 무시되므로, 어떤 쿼리보다도 먼저 건다.
    (근거 재현: check_sqlite_features.py)
    """
    conn = sqlite3.connect(path, isolation_level=None)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def init_db(path: str, upto: int | None = None) -> int:
    """아직 안 넣은 스키마 파일을 넣고, 지금 DB 의 판 번호를 돌려준다.
    upto 를 주면 그 번호까지만 넣는다 — 검사(check_schema)가 옛 판 DB 를 만들어 다음 판이 줄을 그대로 옮기는지 볼 때만 쓴다(서버는 주지 않는다)."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        for f in sorted(SQL_DIR.glob("[0-9][0-9][0-9]_*.sql")):
            n = int(f.name[:3])
            if n <= version or (upto is not None and n > upto):
                continue
            # 파일 하나를 한 트랜잭션으로 — 중간에 실패하면 그 파일 전체가 없던 일이 된다
            try:
                conn.executescript(
                    f"BEGIN IMMEDIATE;\n{f.read_text(encoding='utf-8')}\n"
                    f"PRAGMA user_version = {n};\nCOMMIT;"
                )
            except sqlite3.Error:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
            version = n
        return version
    finally:
        conn.close()
