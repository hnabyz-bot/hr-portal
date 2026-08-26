"""SQLite 기능 검증 하네스.

설계 문서(docs/superpowers/specs/2026-08-24-sqlite-auth-design.md)는
"SQLite에서 직접 돌려보고 확인했다"고 서술하지만, 저장소에는 그 검증
스크립트가 없었다. 이 파일이 그 주장을 CI에서 실제로 재현한다.

검증 대상 6가지:
  1. PRAGMA foreign_keys 가 기본으로 꺼져 있다 (§5 함정)
  2. 생성 칼럼 GENERATED ALWAYS AS (...) STORED 가 동작한다 (§5)
  3. 부분 유니크 인덱스가 동작한다 (§5)
  4. 트리거 RAISE(ABORT) 로 잠금이 걸린다 (§5)
  5. REAL 은 가중치 합 검증을 깨뜨리고, 100배 정수는 정확하다 (§5)
  6. BEGIN IMMEDIATE 가 SELECT ... FOR UPDATE 자리를 대신한다 (§5)

실패하면 종료코드 1 로 CI 를 깨뜨린다. 스키마 파일에는 의존하지 않는다
(스키마 교체는 M2 의 몫). 여기서 확인하는 것은 SQLite 엔진의 능력뿐이다.
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

MIN_SQLITE = (3, 31, 0)  # 생성 칼럼 도입 버전

_failures: list[str] = []
_checks = 0


def _report(ok: bool, label: str, detail: str = "") -> None:
    global _checks
    _checks += 1
    if ok:
        print(f"  [통과] {label}")
    else:
        print(f"  [실패] {label}" + (f" — {detail}" if detail else ""))
        _failures.append(label)


def _connect(path: str | None = None, **kwargs) -> sqlite3.Connection:
    return sqlite3.connect(path or ":memory:", **kwargs)


def check_version() -> None:
    print("0. SQLite 버전")
    actual = tuple(int(p) for p in sqlite3.sqlite_version.split("."))
    _report(
        actual >= MIN_SQLITE,
        f"sqlite {sqlite3.sqlite_version} >= {'.'.join(map(str, MIN_SQLITE))}",
        f"생성 칼럼은 {'.'.join(map(str, MIN_SQLITE))} 이상 필요",
    )


def check_foreign_keys_default_off() -> None:
    """외래키는 연결마다 꺼진 채로 시작한다. 켜지 않으면 선언만 되고 아무것도 막지 않는다."""
    print("1. PRAGMA foreign_keys")
    conn = _connect()
    default = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    _report(default == 0, "기본값이 꺼짐(0) — 연결마다 켜야 한다", f"실제 {default}")

    conn.executescript(
        """
        CREATE TABLE departments (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL);
        CREATE TABLE users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            name          TEXT NOT NULL,
            department_id INTEGER NOT NULL REFERENCES departments(id)
        );
        """
    )

    # 꺼진 상태 — 존재하지 않는 부서에 배정해도 조용히 저장된다.
    conn.execute("INSERT INTO users (name, department_id) VALUES ('없는부서직원', 9999)")
    leaked = conn.execute("SELECT COUNT(*) FROM users WHERE department_id = 9999").fetchone()[0]
    _report(leaked == 1, "꺼진 상태에서는 유령 외래키가 그대로 저장됨 (위험 재현)")

    # 함정 2 — PRAGMA 는 트랜잭션 안에서 조용히 무시된다.
    # 위 INSERT 가 암시적 트랜잭션을 열어둔 상태이므로 여기서 켜도 반영되지 않는다.
    conn.execute("PRAGMA foreign_keys = ON")
    in_txn = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    _report(
        conn.in_transaction and in_txn == 0,
        "트랜잭션 안에서 켜면 조용히 무시됨 (오류도 안 난다)",
        f"in_transaction={conn.in_transaction}, foreign_keys={in_txn}",
    )

    # 트랜잭션을 닫고 다시 켜야 실제로 적용된다.
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")
    applied = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    _report(applied == 1, "트랜잭션 밖에서 켜면 적용됨", f"실제 {applied}")

    conn.execute("DELETE FROM users")
    conn.commit()
    try:
        conn.execute("INSERT INTO users (name, department_id) VALUES ('없는부서직원', 9999)")
        _report(False, "켠 상태에서 유령 외래키 거부", "저장되어 버렸다")
    except sqlite3.IntegrityError:
        _report(True, "켠 상태에서 유령 외래키 거부")
    conn.close()


def check_generated_column() -> None:
    """총점 = KPI점수 + 가점 을 DB가 직접 계산하고, 그 값에 CHECK 를 걸 수 있어야 한다."""
    print("2. 생성 칼럼 (GENERATED ALWAYS AS ... STORED)")
    conn = _connect()
    try:
        conn.executescript(
            """
            CREATE TABLE evaluations (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                kpi_x100     INTEGER NOT NULL CHECK (kpi_x100 BETWEEN 0 AND 10000),
                bonus_x100   INTEGER NOT NULL DEFAULT 0 CHECK (bonus_x100 >= 0),
                total_x100   INTEGER GENERATED ALWAYS AS (kpi_x100 + bonus_x100) STORED,
                CHECK (kpi_x100 + bonus_x100 <= 11000)
            );
            """
        )
        _report(True, "생성 칼럼 + 총점 상한 CHECK DDL 수용")
    except sqlite3.OperationalError as exc:
        _report(False, "생성 칼럼 DDL 수용", str(exc))
        conn.close()
        return

    conn.execute("INSERT INTO evaluations (kpi_x100, bonus_x100) VALUES (9550, 300)")
    total = conn.execute("SELECT total_x100 FROM evaluations").fetchone()[0]
    _report(total == 9850, "총점이 DB에서 자동 계산됨 (95.50 + 3.00 = 98.50)", f"실제 {total}")

    # 총점 110점(=11000) 초과는 거부돼야 한다 — PR #26 에서 추가된 제약
    try:
        conn.execute("INSERT INTO evaluations (kpi_x100, bonus_x100) VALUES (10000, 1100)")
        _report(False, "총점 110점 초과 거부", "111점이 저장되어 버렸다")
    except sqlite3.IntegrityError:
        _report(True, "총점 110점 초과 거부")

    # 생성 칼럼에 직접 쓰기는 막혀야 한다
    try:
        conn.execute("INSERT INTO evaluations (kpi_x100, total_x100) VALUES (5000, 1)")
        _report(False, "생성 칼럼 직접 쓰기 거부", "임의 총점이 저장되어 버렸다")
    except sqlite3.OperationalError:
        _report(True, "생성 칼럼 직접 쓰기 거부")
    conn.close()


def check_partial_unique_index() -> None:
    """'취소되지 않은 계약은 1인당 1건' 같은 조건부 유일성이 필요하다."""
    print("3. 부분 유니크 인덱스")
    conn = _connect()
    conn.executescript(
        """
        CREATE TABLE salary_contracts (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            status  TEXT    NOT NULL CHECK (status IN ('DRAFT','SENT','SIGNED','CANCELLED'))
        );
        CREATE UNIQUE INDEX idx_contract_live
            ON salary_contracts(user_id) WHERE status <> 'CANCELLED';
        """
    )
    _report(True, "부분 유니크 인덱스 DDL 수용")

    conn.execute("INSERT INTO salary_contracts (user_id, status) VALUES (1, 'SENT')")
    try:
        conn.execute("INSERT INTO salary_contracts (user_id, status) VALUES (1, 'DRAFT')")
        _report(False, "살아있는 계약 중복 거부", "같은 직원에게 2건이 저장됐다")
    except sqlite3.IntegrityError:
        _report(True, "살아있는 계약 중복 거부")

    # 파기된 계약은 여러 건 남을 수 있어야 한다 (재발송 이력)
    conn.execute("INSERT INTO salary_contracts (user_id, status) VALUES (2, 'CANCELLED')")
    conn.execute("INSERT INTO salary_contracts (user_id, status) VALUES (2, 'CANCELLED')")
    cancelled = conn.execute(
        "SELECT COUNT(*) FROM salary_contracts WHERE user_id = 2"
    ).fetchone()[0]
    _report(cancelled == 2, "파기된 계약은 여러 건 공존 가능 (재발송 이력 보존)", f"실제 {cancelled}건")
    conn.close()


def check_raise_abort_trigger() -> None:
    """서명된 계약서는 수정도 삭제도 안 되어야 한다 (PostgreSQL plpgsql 트리거의 대체)."""
    print("4. 트리거 RAISE(ABORT)")
    conn = _connect()
    conn.executescript(
        """
        CREATE TABLE salary_contracts (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER NOT NULL,
            annual_salary INTEGER NOT NULL,
            status       TEXT    NOT NULL
        );
        CREATE TRIGGER trg_contract_lock
        BEFORE UPDATE ON salary_contracts
        WHEN old.status = 'SIGNED'
        BEGIN
            SELECT RAISE(ABORT, '서명된 계약서는 수정할 수 없습니다');
        END;
        CREATE TRIGGER trg_no_delete_contracts
        BEFORE DELETE ON salary_contracts
        BEGIN
            SELECT RAISE(ABORT, '계약서는 삭제할 수 없습니다');
        END;
        """
    )
    _report(True, "RAISE(ABORT) 트리거 DDL 수용")

    conn.execute(
        "INSERT INTO salary_contracts (user_id, annual_salary, status)"
        " VALUES (1, 50000000, 'SIGNED')"
    )
    try:
        conn.execute("UPDATE salary_contracts SET annual_salary = 99000000 WHERE id = 1")
        _report(False, "서명된 계약서 수정 차단", "연봉이 변조되었다")
    except sqlite3.IntegrityError as exc:
        _report("서명된 계약서는 수정할 수 없습니다" in str(exc), "서명된 계약서 수정 차단", str(exc))

    try:
        conn.execute("DELETE FROM salary_contracts WHERE id = 1")
        _report(False, "계약서 삭제 차단", "삭제되어 버렸다")
    except sqlite3.IntegrityError:
        _report(True, "계약서 삭제 차단")

    salary = conn.execute("SELECT annual_salary FROM salary_contracts WHERE id = 1").fetchone()[0]
    _report(salary == 50000000, "원본 연봉 보존", f"실제 {salary}")
    conn.close()


def check_real_vs_integer() -> None:
    """KPI 가중치 합계 100 검증이 부동소수점 오차로 깨지는지 실제로 확인한다."""
    print("5. REAL 부동소수점 vs 100배 정수")
    conn = _connect()
    conn.executescript(
        """
        CREATE TABLE w_real (weight REAL NOT NULL);
        CREATE TABLE w_int  (weight_x100 INTEGER NOT NULL);
        """
    )
    # 설계 문서 §5 의 예시(33.33 + 33.33 + 33.34)는 IEEE754 배정밀도에서
    # 정확히 100.0 이 되어 오차가 재현되지 않는다. 실제로 깨지는 조합을 쓴다.
    doc_example = [33.33, 33.33, 33.34]
    _report(
        sum(doc_example) == 100.0,
        "문서 §5 예시(33.33+33.33+33.34)는 오차가 나지 않음 — 문서 정정 대상",
        f"실제 {sum(doc_example)!r}",
    )

    weights = [7.64, 83.57, 8.79]  # 합계 100.00, 부동소수점에서는 99.99999999999999
    conn.executemany("INSERT INTO w_real (weight) VALUES (?)", [(w,) for w in weights])
    conn.executemany(
        "INSERT INTO w_int (weight_x100) VALUES (?)", [(round(w * 100),) for w in weights]
    )

    real_sum = conn.execute("SELECT SUM(weight) FROM w_real").fetchone()[0]
    int_sum = conn.execute("SELECT SUM(weight_x100) FROM w_int").fetchone()[0]

    _report(
        real_sum != 100.0,
        f"REAL 합계가 정확히 100.0 이 아님 (오차 재현: {real_sum!r})",
        "이 조합에서도 오차가 나지 않았다 — 문서의 근거를 재검토할 것",
    )
    _report(int_sum == 10000, "100배 정수 합계는 정확히 10000", f"실제 {int_sum}")

    # 정수 저장이면 CHECK 로 합계 검증이 안전하게 성립한다
    conn.executescript(
        """
        CREATE TABLE kpi_sheets (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            total_w_x100  INTEGER NOT NULL CHECK (total_w_x100 = 10000)
        );
        """
    )
    conn.execute("INSERT INTO kpi_sheets (total_w_x100) VALUES (10000)")
    try:
        conn.execute("INSERT INTO kpi_sheets (total_w_x100) VALUES (9999)")
        _report(False, "가중치 합 100 아닌 시트 거부", "저장되어 버렸다")
    except sqlite3.IntegrityError:
        _report(True, "가중치 합 100 아닌 시트 거부")
    conn.close()


def check_begin_immediate() -> None:
    """SELECT ... FOR UPDATE 자리를 BEGIN IMMEDIATE 가 대신하는지 확인한다."""
    print("6. BEGIN IMMEDIATE 배타 잠금")
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "lock-test.db")
        writer = _connect(db, isolation_level=None, timeout=0.1)
        writer.execute("CREATE TABLE quotas (id INTEGER PRIMARY KEY, cap_s INTEGER NOT NULL)")
        writer.execute("INSERT INTO quotas (id, cap_s) VALUES (1, 3)")

        other = _connect(db, isolation_level=None, timeout=0.1)
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("UPDATE quotas SET cap_s = 2 WHERE id = 1")
        try:
            other.execute("BEGIN IMMEDIATE")
            _report(False, "두 번째 쓰기 트랜잭션이 대기/거부됨", "동시에 잠금을 얻어 버렸다")
        except sqlite3.OperationalError as exc:
            _report("locked" in str(exc).lower(), "두 번째 쓰기 트랜잭션이 대기/거부됨", str(exc))
        writer.execute("COMMIT")

        # 잠금 해제 후에는 정상 진행되어야 한다
        other.execute("BEGIN IMMEDIATE")
        other.execute("UPDATE quotas SET cap_s = 1 WHERE id = 1")
        other.execute("COMMIT")
        final = other.execute("SELECT cap_s FROM quotas WHERE id = 1").fetchone()[0]
        _report(final == 1, "잠금 해제 후 정상 갱신", f"실제 {final}")
        writer.close()
        other.close()


def main() -> int:
    print("=" * 62)
    print("SQLite 기능 검증 — 설계 문서 §5 주장 재현")
    print("=" * 62)
    for check in (
        check_version,
        check_foreign_keys_default_off,
        check_generated_column,
        check_partial_unique_index,
        check_raise_abort_trigger,
        check_real_vs_integer,
        check_begin_immediate,
    ):
        check()
    print("-" * 62)
    if _failures:
        print(f"검사 {_checks}건 중 {len(_failures)}건 실패:")
        for name in _failures:
            print(f"  - {name}")
        return 1
    print(f"검사 {_checks}건 전부 통과 — 설계 문서 §5 의 전제가 실제로 성립합니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
