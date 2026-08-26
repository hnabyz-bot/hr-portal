"""SQLite 스키마 적용 + 제약조건 동작 검사.

001_init.sql 을 임시 DB 에 적용하고, "막혀야 할 것"이 실제로 막히는지
확인한다. PostgreSQL 시절의 checks.sql 을 파이썬으로 옮긴 것이며,
그때 없던 검사(외래키 강제, 부분 유니크 인덱스, 생성 칼럼, updated_at
자동 갱신)를 더했다.

이 파일에는 실제 직원 정보도, 실제 인상률 수치도 들어가지 않는다.
전부 가상 값이다. hr-portal 은 Public 저장소다.

CI: python -m hr_eval.sql.check_schema
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

SCHEMA_PATH = Path(__file__).with_name("001_init.sql")

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


def _rejects(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> tuple[bool, str]:
    """SQL 이 거부되면 (True, 메시지). 통과해 버리면 (False, '')."""
    try:
        conn.execute(sql, params)
    except sqlite3.IntegrityError as exc:
        return True, str(exc)
    except sqlite3.OperationalError as exc:
        return True, str(exc)
    return False, ""


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


def apply_schema(conn: sqlite3.Connection) -> bool:
    print("1. 스키마 적용")
    try:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    except sqlite3.Error as exc:
        _report(False, "001_init.sql 적용", str(exc))
        return False
    _report(True, "001_init.sql 적용")

    tables = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    expected = {
        "departments",
        "users",
        "evaluation_periods",
        "department_quotas",
        "kpi_sheets",
        "kpis",
        "kpi_change_requests",
        "evaluations",
        "salary_raise_rates",
        "salary_contracts",
        "audit_logs",
    }
    missing = expected - tables
    _report(not missing, f"테이블 {len(expected)}개 생성", f"누락: {sorted(missing)}")

    triggers = {
        r[0]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchall()
    }
    _report(len(triggers) >= 15, f"트리거 {len(triggers)}개 생성", f"실제 {sorted(triggers)}")

    # executescript 는 스크립트 시작 시 커밋하고 트랜잭션을 열 수 있으므로
    # 외래키가 실제로 켜져 있는지 다시 확인한다.
    fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    _report(fk == 1, "외래키가 켜진 상태로 유지됨", f"실제 {fk}")
    return True


def seed(conn: sqlite3.Connection) -> None:
    """이후 검사가 쓸 가상 데이터. 전부 example.invalid / 가상 값이다."""
    print("2. 가상 데이터 준비")
    conn.execute(
        "INSERT INTO departments (code, name, level) VALUES ('DIV1', '가상본부', 'DIVISION')"
    )
    conn.execute(
        "INSERT INTO users (employee_no, name, email, role, department_id, hire_date)"
        " VALUES ('T001', '가상직원', 't001@example.invalid', 'EMPLOYEE',"
        " (SELECT id FROM departments WHERE code='DIV1'), '2020-01-01')"
    )
    conn.execute(
        "INSERT INTO evaluation_periods (year, type, name, starts_on, ends_on)"
        " VALUES (2027, 'ANNUAL', '가상 정기평가', '2027-01-01', '2027-12-31')"
    )
    _report(True, "부서·직원·평가기간 각 1건 생성")


def check_foreign_keys(conn: sqlite3.Connection) -> None:
    print("3. 외래키 강제")
    ok, _ = _rejects(
        conn,
        "INSERT INTO users (employee_no, name, email, role, department_id, hire_date)"
        " VALUES ('T999', '유령', 't999@example.invalid', 'EMPLOYEE', 9999, '2020-01-01')",
    )
    _report(ok, "존재하지 않는 부서에 직원 배정 거부")


def check_department_hierarchy(conn: sqlite3.Connection) -> None:
    print("4. 조직 계층 제약")
    ok, _ = _rejects(
        conn,
        "INSERT INTO departments (code, name, level, parent_id)"
        " VALUES ('DIV2', '상위 있는 본부', 'DIVISION',"
        " (SELECT id FROM departments WHERE code='DIV1'))",
    )
    _report(ok, "본부(DIVISION)에 상위 부서 지정 거부")

    ok, _ = _rejects(
        conn,
        "INSERT INTO departments (code, name, level) VALUES ('TEAM1', '상위 없는 팀', 'TEAM')",
    )
    _report(ok, "팀(TEAM)에 상위 부서 누락 거부")


def check_evaluation_rules(conn: sqlite3.Connection) -> None:
    print("5. 평가 점수 제약")
    period = "(SELECT id FROM evaluation_periods LIMIT 1)"
    user = "(SELECT id FROM users LIMIT 1)"
    div = "(SELECT id FROM departments WHERE code='DIV1')"

    # (1) 100점 이하 S등급은 거부돼야 한다  (100.00점 = 10000)
    ok, _ = _rejects(
        conn,
        f"INSERT INTO evaluations (period_id, user_id, division_id, kpi_score_x100, grade)"
        f" VALUES ({period}, {user}, {div}, 10000, 'S')",
    )
    _report(ok, "총점 100점 이하 S등급 거부")

    # (2) 총점 110점 초과는 거부돼야 한다  (110.00점 = 11000)
    ok, _ = _rejects(
        conn,
        f"INSERT INTO evaluations"
        f" (period_id, user_id, division_id, kpi_score_x100, bonus_score_x100, bonus_reason)"
        f" VALUES ({period}, {user}, {div}, 10000, 1100, '가상 가점 사유')",
    )
    _report(ok, "총점 110점 초과 거부")

    # (3) 가점이 있는데 사유가 없으면 거부돼야 한다
    ok, _ = _rejects(
        conn,
        f"INSERT INTO evaluations"
        f" (period_id, user_id, division_id, kpi_score_x100, bonus_score_x100)"
        f" VALUES ({period}, {user}, {div}, 9000, 500)",
    )
    _report(ok, "가점 사유 없는 가점 거부")

    # (4) 정상 평가 1건 — 이후 계약 검사의 전제
    conn.execute(
        f"INSERT INTO evaluations"
        f" (period_id, user_id, division_id, kpi_score_x100, bonus_score_x100,"
        f"  bonus_reason, grade, status)"
        f" VALUES ({period}, {user}, {div}, 9500, 1000, '가상 가점 사유', 'S', 'CONFIRMED')"
    )
    total = conn.execute("SELECT total_score_x100 FROM evaluations").fetchone()[0]
    _report(total == 10500, "총점이 DB에서 자동 계산됨 (95.00 + 10.00 = 105.00)", f"실제 {total}")


def check_quota_rules(conn: sqlite3.Connection) -> None:
    print("6. 본부 쿼터 제약")
    period = "(SELECT id FROM evaluation_periods LIMIT 1)"
    dept = "(SELECT id FROM departments WHERE code='DIV1')"

    # 그룹 정원 합계가 인원수와 다르면 거부
    ok, _ = _rejects(
        conn,
        f"INSERT INTO department_quotas"
        f" (period_id, department_id, headcount, quota_mode, quota_upper, quota_lower, cap_s)"
        f" VALUES ({period}, {dept}, 4, 'GROUPED', 1, 99, 1)",
    )
    _report(ok, "그룹 정원 합계 불일치 거부")

    # GROUPED 모드에 개별 정원 칼럼을 채우면 거부
    ok, _ = _rejects(
        conn,
        f"INSERT INTO department_quotas"
        f" (period_id, department_id, headcount, quota_mode,"
        f"  quota_upper, quota_lower, quota_a, cap_s)"
        f" VALUES ({period}, {dept}, 4, 'GROUPED', 1, 3, 1, 1)",
    )
    _report(ok, "모드에 맞지 않는 칼럼 거부")

    # INDIVIDUAL 모드에서 S 상한이 A 정원을 넘으면 거부
    ok, _ = _rejects(
        conn,
        f"INSERT INTO department_quotas"
        f" (period_id, department_id, headcount, quota_mode,"
        f"  quota_a, quota_b, quota_c, quota_d, cap_s)"
        f" VALUES ({period}, {dept}, 10, 'INDIVIDUAL', 2, 3, 3, 2, 3)",
    )
    _report(ok, "S 상한이 A 정원 초과 시 거부")

    # 정상 케이스는 저장돼야 한다
    conn.execute(
        f"INSERT INTO department_quotas"
        f" (period_id, department_id, headcount, quota_mode,"
        f"  quota_a, quota_b, quota_c, quota_d, cap_s)"
        f" VALUES ({period}, {dept}, 10, 'INDIVIDUAL', 2, 3, 3, 2, 2)"
    )
    saved = conn.execute("SELECT COUNT(*) FROM department_quotas").fetchone()[0]
    _report(saved == 1, "정상 쿼터 저장", f"실제 {saved}건")


def check_kpi_rules(conn: sqlite3.Connection) -> None:
    print("7. KPI 제약")
    period = "(SELECT id FROM evaluation_periods LIMIT 1)"
    user = "(SELECT id FROM users LIMIT 1)"
    conn.execute(f"INSERT INTO kpi_sheets (period_id, user_id) VALUES ({period}, {user})")
    sheet = "(SELECT id FROM kpi_sheets LIMIT 1)"

    # 가중치는 0 초과 100 이하 (100배 정수)
    ok, _ = _rejects(
        conn,
        f"INSERT INTO kpis (sheet_id, title, weight_pct_x100)"
        f" VALUES ({sheet}, '가상 KPI', 0)",
    )
    _report(ok, "가중치 0 거부")

    ok, _ = _rejects(
        conn,
        f"INSERT INTO kpis (sheet_id, title, weight_pct_x100)"
        f" VALUES ({sheet}, '가상 KPI', 10001)",
    )
    _report(ok, "가중치 100% 초과 거부")

    # 100배 정수 합계는 정확히 10000 이 된다 (REAL 이었다면 오차가 났을 조합)
    for title, w in (("KPI 1", 764), ("KPI 2", 8357), ("KPI 3", 879)):
        conn.execute(
            f"INSERT INTO kpis (sheet_id, title, weight_pct_x100) VALUES ({sheet}, ?, ?)",
            (title, w),
        )
    total = conn.execute("SELECT SUM(weight_pct_x100) FROM kpis").fetchone()[0]
    _report(total == 10000, "가중치 합계가 정확히 10000 (7.64+83.57+8.79)", f"실제 {total}")

    # 수정 요청의 JSON 검증
    ok, _ = _rejects(
        conn,
        f"INSERT INTO kpi_change_requests (sheet_id, requested_by, reason, proposed_kpis)"
        f" VALUES ({sheet}, {user}, '가상 사유', '이건 JSON 이 아니다')",
    )
    _report(ok, "JSON 아닌 수정 요청 본문 거부")

    conn.execute(
        f"INSERT INTO kpi_change_requests (sheet_id, requested_by, reason, proposed_kpis)"
        f" VALUES ({sheet}, {user}, '가상 사유', '[]')"
    )
    ok, _ = _rejects(
        conn,
        f"INSERT INTO kpi_change_requests (sheet_id, requested_by, reason, proposed_kpis)"
        f" VALUES ({sheet}, {user}, '가상 사유 2', '[]')",
    )
    _report(ok, "한 시트에 미결 수정 요청 2건 거부 (부분 유니크 인덱스)")


def _sign_contract(conn: sqlite3.Connection) -> None:
    """서명 완료 계약서 1건. 금액·인상률은 전부 가상 값이다."""
    conn.execute(
        "INSERT INTO salary_contracts ("
        "  period_id, user_id, evaluation_id, grade,"
        "  base_salary_before, raise_pct_x100, base_salary_after,"
        "  contract_starts_on, contract_ends_on,"
        "  status, is_locked, consent_checked,"
        "  signer_user_id, signer_name, signer_ip, signature_image, document_hash, signed_at"
        ") VALUES ("
        "  (SELECT id FROM evaluation_periods LIMIT 1),"
        "  (SELECT id FROM users LIMIT 1),"
        "  (SELECT id FROM evaluations LIMIT 1),"
        "  'S', 50000000, 1111, 55555000,"
        "  '2027-01-01', '2027-12-31',"
        "  'SIGNED', 1, 1,"
        "  (SELECT id FROM users LIMIT 1), '가상직원', '192.0.2.10', X'00', ?,"
        "  '2027-01-02T09:00:00Z')",
        ("a" * 64,),
    )


def check_contract_rules(conn: sqlite3.Connection) -> None:
    print("8. 연봉계약 불변성")

    # 서명 상태인데 서명 증적이 없으면 거부돼야 한다
    ok, _ = _rejects(
        conn,
        "INSERT INTO salary_contracts"
        " (period_id, user_id, evaluation_id, grade, base_salary_before, raise_pct_x100,"
        "  base_salary_after, contract_starts_on, contract_ends_on, status)"
        " VALUES ((SELECT id FROM evaluation_periods LIMIT 1), (SELECT id FROM users LIMIT 1),"
        "  (SELECT id FROM evaluations LIMIT 1), 'S', 50000000, 1111, 55555000,"
        "  '2027-01-01', '2027-12-31', 'SIGNED')",
    )
    _report(ok, "서명 증적 없는 SIGNED 상태 거부")

    _sign_contract(conn)
    _report(True, "서명 완료 계약서 1건 생성")

    # 잠긴 계약서의 금액 수정은 거부돼야 한다
    ok, msg = _rejects(conn, "UPDATE salary_contracts SET base_salary_after = 99000000")
    _report(ok and "READ_ONLY" in msg, "잠긴 계약서 금액 수정 거부", msg)

    # 잠긴 계약서를 SENT 로 되돌리는 것도 거부돼야 한다
    ok, msg = _rejects(conn, "UPDATE salary_contracts SET status = 'SENT'")
    _report(ok and "READ_ONLY" in msg, "잠긴 계약서 상태 되돌리기 거부", msg)

    # 살아있는 계약은 1인 1기간 1건
    ok, _ = _rejects(
        conn,
        "INSERT INTO salary_contracts"
        " (period_id, user_id, evaluation_id, grade, base_salary_before, raise_pct_x100,"
        "  base_salary_after, contract_starts_on, contract_ends_on, status)"
        " VALUES ((SELECT id FROM evaluation_periods LIMIT 1), (SELECT id FROM users LIMIT 1),"
        "  (SELECT id FROM evaluations LIMIT 1), 'S', 50000000, 1111, 55555000,"
        "  '2027-01-01', '2027-12-31', 'DRAFT')",
    )
    _report(ok, "살아있는 계약 중복 거부 (부분 유니크 인덱스)")

    # 파기는 허용돼야 한다
    conn.execute(
        "UPDATE salary_contracts SET status = 'CANCELLED',"
        " cancelled_at = '2027-02-01T09:00:00Z',"
        " cancelled_by = (SELECT id FROM users LIMIT 1),"
        " cancel_reason = '가상 파기 사유'"
    )
    status = conn.execute("SELECT status FROM salary_contracts").fetchone()[0]
    _report(status == "CANCELLED", "잠긴 계약서 파기 허용", f"실제 {status}")

    # 파기 후에는 같은 기간에 새 계약을 만들 수 있어야 한다 (재발송)
    conn.execute(
        "INSERT INTO salary_contracts"
        " (period_id, user_id, evaluation_id, grade, base_salary_before, raise_pct_x100,"
        "  base_salary_after, contract_starts_on, contract_ends_on, status, version)"
        " VALUES ((SELECT id FROM evaluation_periods LIMIT 1), (SELECT id FROM users LIMIT 1),"
        "  (SELECT id FROM evaluations LIMIT 1), 'S', 50000000, 1111, 55555000,"
        "  '2027-01-01', '2027-12-31', 'DRAFT', 2)"
    )
    _report(True, "파기 후 재발송 계약 생성 허용")

    # 삭제는 거부돼야 한다
    ok, _ = _rejects(conn, "DELETE FROM salary_contracts")
    _report(ok, "계약서 삭제 거부")


def check_audit_log(conn: sqlite3.Connection) -> None:
    print("9. 감사로그 불변성")
    conn.execute(
        "INSERT INTO audit_logs (action, entity_type, entity_id)"
        " VALUES ('TEST', 'salary_contract', 1)"
    )
    ok, _ = _rejects(conn, "UPDATE audit_logs SET action = 'TAMPERED'")
    _report(ok, "감사로그 수정 거부")

    ok, _ = _rejects(conn, "DELETE FROM audit_logs")
    _report(ok, "감사로그 삭제 거부")

    ok, _ = _rejects(
        conn,
        "INSERT INTO audit_logs (action, entity_type, after_data)"
        " VALUES ('TEST', 'kpi_sheet', '이건 JSON 이 아니다')",
    )
    _report(ok, "JSON 아닌 감사로그 본문 거부")


def check_updated_at(conn: sqlite3.Connection) -> None:
    print("10. updated_at 자동 갱신")
    conn.execute("UPDATE users SET updated_at = '2020-01-01T00:00:00Z'")
    before = conn.execute("SELECT updated_at FROM users").fetchone()[0]
    conn.execute("UPDATE users SET name = '가상직원(개명)'")
    after = conn.execute("SELECT updated_at FROM users").fetchone()[0]
    _report(after != before, "수정 시 updated_at 이 자동 갱신됨", f"{before} -> {after}")


def main() -> int:
    print("=" * 62)
    print("SQLite 스키마 적용 + 제약조건 동작 검사")
    print("=" * 62)
    with tempfile.TemporaryDirectory() as tmp:
        conn = connect(str(Path(tmp) / "schema-check.db"))
        try:
            if not apply_schema(conn):
                return 1
            seed(conn)
            check_foreign_keys(conn)
            check_department_hierarchy(conn)
            check_evaluation_rules(conn)
            check_quota_rules(conn)
            check_kpi_rules(conn)
            check_contract_rules(conn)
            check_audit_log(conn)
            check_updated_at(conn)
        finally:
            conn.close()

    print("-" * 62)
    if _failures:
        print(f"검사 {_checks}건 중 {len(_failures)}건 실패:")
        for name in _failures:
            print(f"  - {name}")
        return 1
    print(f"검사 {_checks}건 전부 통과 — 스키마 제약조건이 실제로 동작합니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
