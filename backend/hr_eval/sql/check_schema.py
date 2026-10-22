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

from .db import SQL_DIR, connect, init_db

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


def check_file_numbers() -> None:
    """스키마 파일 번호가 001 부터 빠짐·겹침 없이 이어지는가. init_db 는 DB 판 번호 이하의 파일을 건너뛰므로
    운영 DB(판 N)에 나중에 들어온 빠진 번호는 영영 적용되지 않는다 — merge 순서를 문서가 아니라 검사로 막는다."""
    print("0. 스키마 파일 번호 (001부터 빠짐·겹침 없이)")
    nums = [int(f.name[:3]) for f in SQL_DIR.glob("[0-9][0-9][0-9]_*.sql")]
    missing = sorted(set(range(1, max(nums, default=0) + 1)) - set(nums))
    dup = sorted({n for n in nums if nums.count(n) > 1})
    _report(not missing and not dup, f"스키마 파일 번호가 001부터 빠짐·겹침 없이 이어짐 ({len(nums)}개)",
            f"빠진 번호 {missing} · 겹친 번호 {dup} — 서버의 init_db 는 DB 판 번호보다 작거나 같은 번호의 파일을 건너뜁니다."
            " 빠진 번호의 파일이 나중에 merge 되면 운영 DB 에는 영영 적용되지 않고, 겹친 번호는 뒤 파일이 건너뛰어집니다."
            " 빠진 번호를 가진 브랜치를 먼저 merge 하거나 이 브랜치의 파일 번호를 바꿔 주세요.")


def apply_schema(path: str) -> sqlite3.Connection | None:
    """서버와 같은 길(init_db)로 스키마를 넣고, 검사에 쓸 연결을 돌려준다."""
    print("1. 스키마 적용")
    last = max(int(f.name[:3]) for f in SQL_DIR.glob("[0-9][0-9][0-9]_*.sql"))
    try:
        version = init_db(path)
    except sqlite3.Error as exc:
        _report(False, "스키마 파일 적용 (init_db)", str(exc))
        return None
    _report(version == last, f"스키마 파일 적용 (init_db) — 판 {version}", f"마지막 파일 번호 {last}")

    # 서버는 시작할 때마다 init_db 를 부른다. 두 번째부터는 아무것도 하지 않아야 한다.
    try:
        again = init_db(path)
        _report(again == version, "init_db 를 다시 불러도 다시 넣지 않음", f"판 {again}")
    except sqlite3.Error as exc:
        _report(False, "init_db 를 다시 불러도 다시 넣지 않음", str(exc))

    conn = connect(path)

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
        "user_roles",
        "sessions",
        "sso_used_tokens",
        "sso_rate_hits",
        "phone_codes",
        "celeb_events",
        "workspace_data",
        "game_saves",
        "game_scores",
        "game_gifts",
        "kpi_years",
        "kpi_windows",
        "kpi_plans",
        "kpi_versions",
        "ceo_delegation",
        "kpi_progress",
        "kpi_month_notes",
        "kpi_results",
        "kpi_sched_changes",
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
    return conn


def seed(conn: sqlite3.Connection) -> None:
    """이후 검사가 쓸 가상 데이터. 전부 example.invalid / 가상 값이다."""
    print("2. 가상 데이터 준비")
    conn.execute(
        "INSERT INTO departments (code, name, level) VALUES ('DIV1', '가상본부', 'DIVISION')"
    )
    conn.execute(
        "INSERT INTO users (employee_no, name, email, department_id, hire_date)"
        " VALUES ('T001', '가상직원', 't001@example.invalid',"
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
        "INSERT INTO users (employee_no, name, email, department_id, hire_date)"
        " VALUES ('T999', '유령', 't999@example.invalid', 9999, '2020-01-01')",
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


def check_login_tables(conn: sqlite3.Connection) -> None:
    """로그인 설계 §6 의 표. 값은 전부 가상이다 (하이웍스 번호·확인표도 가짜)."""
    print("11. 직원 칸 · 사람별 설정")
    div = "(SELECT id FROM departments WHERE code='DIV1')"
    row = conn.execute(
        "SELECT portal_enabled, writes_kpi, in_evaluation, has_contract FROM users WHERE employee_no='T001'"
    ).fetchone()
    _report(row == (0, 1, 1, 1), "새 직원 기본값: 포털 사용만 꺼짐 (KPI 작성·평가 대상·연봉계약서 켬)", f"실제 {row}")

    # 명단 동기화로 들어온 사람은 사번·입사일이 없다. 그런 사람이 여럿이어도 된다
    for n in (1, 2):
        conn.execute(
            "INSERT INTO users (name, email, department_id, hiworks_user_no)"
            f" VALUES ('동기화직원{n}', 'sync{n}@example.invalid', {div}, 'HW-TEST-{n}')"
        )
    _report(True, "사번·입사일 없는 직원 둘 추가 허용 (명단 동기화)")

    ok, _ = _rejects(
        conn,
        "INSERT INTO users (employee_no, name, email, department_id)"
        f" VALUES ('T001', '사번겹침', 'dup@example.invalid', {div})",
    )
    _report(ok, "사번 중복 거부")

    ok, _ = _rejects(
        conn,
        "INSERT INTO users (name, email, department_id, hiworks_user_no)"
        f" VALUES ('번호겹침', 'dup2@example.invalid', {div}, 'HW-TEST-1')",
    )
    _report(ok, "하이웍스 사용자 번호 중복 거부")

    ok, _ = _rejects(conn, "UPDATE users SET is_active = 0 WHERE employee_no='T001'")
    _report(ok, "비활성 날짜 없이 비활성 거부")
    conn.execute(
        "UPDATE users SET is_active = 0, deactivated_at = '2027-03-01T09:00:00Z'"
        " WHERE hiworks_user_no='HW-TEST-2'"
    )
    ok, _ = _rejects(conn, "UPDATE users SET is_active = 1 WHERE hiworks_user_no='HW-TEST-2'")
    _report(ok, "다시 켤 때 비활성 날짜가 남아 있으면 거부")

    print("12. 직책 · HR 권한 (user_roles)")
    user = "(SELECT id FROM users WHERE employee_no='T001')"
    ok, _ = _rejects(conn, f"INSERT INTO user_roles (user_id, role) VALUES ({user}, 'EMPLOYEE')")
    _report(ok, "EMPLOYEE 역할 거부 (직책 줄이 없으면 직원)")

    conn.execute(f"INSERT INTO user_roles (user_id, role) VALUES ({user}, 'TEAM_LEADER')")
    conn.execute(f"INSERT INTO user_roles (user_id, role) VALUES ({user}, 'HR_ADMIN')")
    n = conn.execute(f"SELECT COUNT(*) FROM user_roles WHERE user_id = {user}").fetchone()[0]
    _report(n == 2, "직책과 HR 권한을 함께 가짐 (HR 팀장)", f"실제 {n}줄")

    ok, _ = _rejects(conn, f"INSERT INTO user_roles (user_id, role) VALUES ({user}, 'HR_ADMIN')")
    _report(ok, "같은 역할 두 줄 거부")

    ok, _ = _rejects(conn, "INSERT INTO user_roles (user_id, role) VALUES (9999, 'CEO')")
    _report(ok, "없는 직원에게 역할 거부")

    # 역할 말고는 아무 기록도 없는 직원이어야 역할 때문에 막혔는지 알 수 있다
    synced = "(SELECT id FROM users WHERE hiworks_user_no='HW-TEST-1')"
    conn.execute(f"INSERT INTO user_roles (user_id, role) VALUES ({synced}, 'CEO')")
    ok, msg = _rejects(conn, f"DELETE FROM users WHERE id = {synced}")
    _report(ok and "FOREIGN KEY" in msg, "역할이 있는 직원 삭제 거부 (비활성으로 처리)", msg)

    print("13. 로그인 기록")
    session = (
        "INSERT INTO sessions (token_hash, user_id, created_at, last_seen_at, expires_at)"
        " VALUES (?, {u}, '2027-03-02T09:00:00Z', '2027-03-02T09:00:00Z', '2027-03-02T21:00:00Z')"
    )
    conn.execute(session.format(u=user), ("b" * 64,))
    ok, _ = _rejects(conn, session.format(u=9999), ("c" * 64,))
    _report(ok, "없는 직원의 세션 거부")

    used = "INSERT INTO sso_used_tokens (token_hash, used_at) VALUES (?, ?)"
    conn.execute(used, ("d" * 64, "2027-03-02T09:00:00Z"))
    ok, _ = _rejects(conn, used, ("d" * 64, "2027-03-02T09:00:01Z"))
    _report(ok, "한 번 쓴 확인표를 다시 기록하면 거부 (재사용 막기)")

    upsert = (
        "INSERT INTO sso_rate_hits (ip, window_start, hits) VALUES ('192.0.2.20', '2027-03-02T09:00', 1)"
        " ON CONFLICT (ip, window_start) DO UPDATE SET hits = hits + 1 RETURNING hits"
    )
    counts = [conn.execute(upsert).fetchone()[0] for _ in range(3)]
    _report(counts == [1, 2, 3], "요청 횟수를 한 문장으로 더하고 셈", f"실제 {counts}")

    print("14. 감사로그의 직책 · HR 권한")
    audit = "INSERT INTO audit_logs (actor_position, actor_is_hr, action, entity_type) VALUES (?, ?, 'TEST', 'evaluation')"
    conn.execute(audit, ("CEO", 0))
    conn.execute(audit, ("TEAM_LEADER", 1))
    _report(True, "대표이사, 팀장 + HR 권한 기록 허용")

    ok, _ = _rejects(conn, audit, ("EMPLOYEE", 0))
    _report(ok, "직책 칸에 EMPLOYEE 거부")

    ok, _ = _rejects(conn, audit, (None, 2))
    _report(ok, "HR 권한 칸에 0·1 외 값 거부")

    # 002: HR 이 대표이사 대신 한 일 — 실제로 넣은 HR 과 대표이사를 함께 남긴다
    ceo = "(SELECT id FROM users WHERE hiworks_user_no='HW-TEST-1')"
    conn.execute(
        f"INSERT INTO audit_logs (actor_user_id, actor_is_hr, on_behalf_of, action, entity_type)"
        f" VALUES ({user}, 1, {ceo}, 'TEST', 'evaluation')"
    )
    row = conn.execute(
        f"SELECT actor_user_id = {user}, on_behalf_of = {ceo} FROM audit_logs WHERE on_behalf_of IS NOT NULL"
    ).fetchone()
    _report(row == (1, 1), "대신 입력: 넣은 HR 과 대표이사를 함께 기록", f"실제 {row}")

    ok, _ = _rejects(
        conn, "INSERT INTO audit_logs (on_behalf_of, action, entity_type) VALUES (9999, 'TEST', 'evaluation')"
    )
    _report(ok, "없는 사람을 대표이사로 적으면 거부")

    print("15. 명단 동기화 칸 (003)")
    conn.execute("INSERT INTO departments (code, name, level, hiworks_node_id) VALUES ('HW-N1', '동기화본부', 'DIVISION', 'N1')")
    ok, _ = _rejects(
        conn, "INSERT INTO departments (code, name, level, hiworks_node_id) VALUES ('HW-N1b', '겹침', 'DIVISION', 'N1')"
    )
    _report(ok, "하이웍스 조직 번호가 같은 부서 두 개 거부")
    conn.execute("INSERT INTO departments (code, name, level) VALUES ('DIV9', '짝없는본부', 'DIVISION')")
    n = conn.execute("SELECT COUNT(*) FROM departments WHERE hiworks_node_id IS NULL").fetchone()[0]
    _report(n >= 2, "하이웍스와 짝짓지 않은 부서는 여럿이어도 됨", f"실제 {n}")
    conn.execute(f"UPDATE users SET phone = '02-000-0000', mobile = '가상' WHERE id = {user}")
    row = conn.execute(f"SELECT phone, mobile FROM users WHERE id = {user}").fetchone()
    _report(row == ("02-000-0000", "가상"), "직원 전화·휴대폰 칸", f"실제 {row}")

    print("16. 휴대폰 등록 (004)")
    kind = conn.execute("SELECT kind FROM sessions WHERE token_hash = ?", ("b" * 64,)).fetchone()
    _report(kind == ("pc",), "앞서 있던 세션은 PC 세션으로 남음", f"실제 {kind}")
    ok, _ = _rejects(conn, f"UPDATE sessions SET kind = 'tablet' WHERE user_id = {user}")
    _report(ok, "세션 종류는 pc · phone 만")
    code = "INSERT INTO phone_codes (code_hash, user_id, created_at, expires_at) VALUES (?, {u}, '2027-03-02T09:00:00Z', '2027-03-02T09:03:00Z')"
    conn.execute(code.format(u=user), ("e" * 64,))
    ok, _ = _rejects(conn, code.format(u=user), ("e" * 64,))
    _report(ok, "같은 QR 값을 두 번 기록하면 거부")
    ok, _ = _rejects(conn, code.format(u=9999), ("f" * 64,))
    _report(ok, "없는 직원의 QR 값 거부")

    print("17. 조직도 칸 (005)")
    row = conn.execute(f"SELECT mobile_public, org_hidden, title, ext, hr_mobile, hr_email FROM users WHERE id = {user}").fetchone()
    _report(row == (1, 0, None, None, None, None), "처음엔 휴대폰 공개 · 조직도에 보임 · 비상연락망 칸 비어 있음", f"실제 {row}")
    for c in ("mobile_public", "org_hidden"):
        ok, _ = _rejects(conn, f"UPDATE users SET {c} = 2 WHERE id = {user}")
        _report(ok, f"{c} 는 0·1 만")

    print("18. 이번 주 소식 (006)")
    row = conn.execute(f"SELECT birthday, birthday_public FROM users WHERE id = {user}").fetchone()
    _report(row == (None, 1), "처음엔 생일 없음 · 생일 알림 켜짐", f"실제 {row}")
    conn.execute(f"UPDATE users SET birthday = '02-29' WHERE id = {user}")
    _report(True, "생일 02-29 저장 (월-일)")
    for bad in ("02-30", "13-01", "3-05", "1990-03-15"):
        ok, _ = _rejects(conn, f"UPDATE users SET birthday = '{bad}' WHERE id = {user}")
        _report(ok, f"생일 '{bad}' 거부 (월-일만)")
    ok, _ = _rejects(conn, f"UPDATE users SET birthday_public = 2 WHERE id = {user}")
    _report(ok, "birthday_public 는 0·1 만")
    ev = "INSERT INTO celeb_events (user_id, kind, label, until_date, created_by, created_at) VALUES ({u}, ?, ?, ?, {u}, '2027-03-02T09:00:00Z')"
    conn.execute(ev.format(u=user), ("joy", "결혼", "2027-03-09"))
    _report(True, "경사 한 건 저장")
    for args, why in ((("party", "결혼", "2027-03-09"), "종류"), (("joy", " ", "2027-03-09"), "빈 내용"), (("joy", "가" * 31, "2027-03-09"), "30자 넘음"),
                      (("joy", "결혼", "2027-02-30"), "없는 날"), (("joy", "결혼", "2027-3-9"), "날짜 모양")):
        ok, _ = _rejects(conn, ev.format(u=user), args)
        _report(ok, f"경사·조사 {why} 거부")
    ok, _ = _rejects(conn, ev.format(u=9999), ("joy", "결혼", "2027-03-09"))
    _report(ok, "없는 직원의 소식 거부")

    print("19. 워크스페이스 (007)")
    ws = "INSERT INTO workspace_data (user_id, kind, data, version, updated_at) VALUES ({u}, ?, ?, ?, '2027-03-02T09:00:00Z')"
    conn.execute(ws.format(u=user), ("tasks", '[{"id": 1, "title": "가상"}]', 1))
    _report(True, "나의 업무 목록 저장 (JSON 목록)")
    for args, why in ((("tasks", "[]", 1), "같은 사람 · 같은 종류 두 줄"), (("notes", "[]", 1), "종류"), (("routines", '{"a": 1}', 1), "목록이 아닌 JSON"),
                      (("routines", "[", 1), "깨진 JSON"), (("routines", "[]", 0), "판 0")):
        ok, _ = _rejects(conn, ws.format(u=user), args)
        _report(ok, f"워크스페이스 {why} 거부")
    ok, _ = _rejects(conn, ws.format(u=9999), ("routines", "[]", 1))
    _report(ok, "없는 직원의 워크스페이스 거부")

    print("20. 게임 (008)")
    sv = "INSERT INTO game_saves (user_id, data, version, updated_at) VALUES ({u}, ?, ?, '2027-03-02T09:00:00Z')"
    conn.execute(sv.format(u=user), ('{"best": "900"}', 1))
    _report(True, "게임 기록 저장 (JSON 객체)")
    for args, why in ((('{}', 1), "같은 사람 두 줄"),):
        ok, _ = _rejects(conn, sv.format(u=user), args)
        _report(ok, f"게임 기록 {why} 거부")
    conn.execute(f"DELETE FROM game_saves WHERE user_id = {user}")
    for args, why in ((("[]", 1), "객체가 아닌 JSON"), (("{", 1), "깨진 JSON"), (("{}", 0), "판 0")):
        ok, _ = _rejects(conn, sv.format(u=user), args)
        _report(ok, f"게임 기록 {why} 거부")
    sc = "INSERT INTO game_scores (user_id, week, score, at) VALUES ({u}, ?, ?, '2027-03-02T09:00:00Z')"
    conn.execute(sc.format(u=user), ("2027-03-01", 900))
    _report(True, "주간 점수 저장 (월요일)")
    for args, why in ((("2027-03-01", 950), "같은 사람 같은 주 두 줄"), (("2027-03-02", 900), "월요일이 아닌 날"), (("2027-02-30", 900), "없는 날"),
                      (("2027-03-08", 0), "점수 0"), (("2027-03-08", 100000001), "점수 범위")):
        ok, _ = _rejects(conn, sc.format(u=user), args)
        _report(ok, f"주간 점수 {why} 거부")
    gf = "INSERT INTO game_gifts (from_user, to_user, day, created_at) VALUES ({u}, ?, ?, '2027-03-02T09:00:00Z')"
    me = conn.execute(f"SELECT {user}").fetchone()[0]
    other = conn.execute(
        f"INSERT INTO users (employee_no, name, email, department_id) VALUES ('T020', '가상동료', 't020@example.invalid', {div}) RETURNING id"
    ).fetchone()[0]
    conn.execute(gf.format(u=user), (other, "2027-03-02"))
    _report(True, "선물 한 장 저장")
    for args, why in (((other, "2027-03-02"), "같은 날 같은 사람에게 두 장"), ((me, "2027-03-03"), "나에게"), ((other, "2027-3-3"), "날짜 모양"), ((9999, "2027-03-03"), "없는 직원에게")):
        ok, _ = _rejects(conn, gf.format(u=user), args)
        _report(ok, f"선물 {why} 거부")

    print("21. 메일 회사 번호 (009)")
    conn.execute(f"UPDATE users SET hiworks_mail_no = '33094' WHERE id = {user}")
    _report(True, "회사 번호 저장 (숫자)")
    for bad in ("0123", "33a94", "", "1234567890123"):
        ok, _ = _rejects(conn, f"UPDATE users SET hiworks_mail_no = '{bad}' WHERE id = {user}")
        _report(ok, f"회사 번호 '{bad}' 거부")

    print("22. KPI 계획서 (010)")
    # 맡은 부서 — 하이웍스에서 본부장이 '임원' 부서에 들어가도 맡은 본부를 따로 둔다
    conn.execute(f"UPDATE users SET led_department_id = {div} WHERE id = {user}")
    _report(True, "맡은 부서 저장")
    ok, msg = _rejects(conn, f"UPDATE users SET led_department_id = 9999 WHERE id = {user}")
    _report(ok and "FOREIGN KEY" in msg, "없는 부서를 맡은 부서로 적으면 거부", msg)

    # 해마다 KPI 작성 기간 (인사담당자가 정한다)
    yr = "INSERT INTO kpi_years (year, edit_from, edit_to, eval_start, updated_at) VALUES (?, ?, ?, ?, '2027-03-02T09:00:00Z')"
    conn.execute(yr, (2027, "2027-01-05", "2027-01-20", "2027-02-01"))
    _report(True, "2027년 KPI 작성 기간 저장")
    conn.execute(yr, (2029, None, None, None))
    _report(True, "기간을 아직 안 정한 해도 저장 (둘 다 비움)")
    for args, why in (((2028, "2027-01-05", None, None), "시작일만 있는 기간"), ((2028, None, "2027-01-20", None), "종료일만 있는 기간"),
                      ((2028, "2027-01-20", "2027-01-05", None), "종료일이 시작일보다 앞선 기간"),
                      ((2028, "2027-01-05", "2027-02-30", None), "없는 종료일 2027-02-30"), ((2028, "2027-02-30", "2027-03-05", None), "없는 시작일 2027-02-30"),
                      ((2028, "2027-1-5", "2027-01-20", None), "날짜 모양이 아닌 시작일"),
                      ((2025, None, None, None), "2026 이전 해"), ((2101, None, None, None), "2100 이후 해")):
        ok, msg = _rejects(conn, yr, args)
        _report(ok and "CHECK" in msg, f"작성 기간 {why} 거부", msg)
    ok, msg = _rejects(conn, yr, (2027, None, None, None))
    _report(ok and "UNIQUE" in msg, "같은 해 작성 기간 두 줄 거부", msg)

    # 한 사람만 따로 연 기간
    wd = "INSERT INTO kpi_windows (user_id, year, edit_from, edit_to, set_by, set_at) VALUES ({u}, ?, ?, ?, {u}, '2027-03-02T09:00:00Z')"
    conn.execute(wd.format(u=user), (2027, "2027-03-01", "2027-03-10"))
    _report(True, "한 사람만 따로 연 기간 저장")
    ok, msg = _rejects(conn, wd.format(u=user), (2027, "2027-04-01", "2027-04-10"))
    _report(ok and "UNIQUE" in msg, "같은 사람 같은 해 따로 연 기간 두 줄 거부", msg)
    ok, msg = _rejects(conn, wd.format(u=9999), (2027, "2027-03-01", "2027-03-10"))
    _report(ok and "FOREIGN KEY" in msg, "없는 직원의 따로 연 기간 거부", msg)

    # 계획서 — 한 사람 한 해에 한 줄
    pl = ("INSERT INTO kpi_plans (user_id, year, status, draft, confirmed_version, note, note_kind, change_reason, updated_at)"
          " VALUES ({u}, ?, ?, ?, ?, ?, ?, ?, '2027-03-02T09:00:00Z')")
    conn.execute(pl.format(u=user), (2027, "DRAFT", "[]", None, None, None, None))
    _report(True, "작성 중 계획서 저장 (빈 목록)")
    conn.execute(pl.format(u=user), (2029, "DRAFT", "[]", None, "가상 돌려보낸 사유", "RETURNED", None))
    _report(True, "돌려보낸 사유가 붙은 작성 중 계획서 저장")
    conn.execute(f"UPDATE kpi_plans SET status = 'SUBMITTED', submitted_at = '2027-03-03T09:00:00Z' WHERE user_id = {user} AND year = 2029")
    _report(True, "계획서를 제출 상태로 바꿈")
    conn.execute(pl.format(u=user), (2030, "CHANGE_REQUESTED", "[]", 1, None, None, "가상 수정 요청 사유"))
    _report(True, "수정 요청 계획서 저장 (확정본 판 + 사유)")
    for args, why in ((("APPROVED", "[]", None, None, None, None), "상태 APPROVED"),
                      (("CONFIRMED", "[]", None, None, None, None), "확정인데 확정본 판이 없음"),
                      (("CHANGE_REQUESTED", "[]", 1, None, None, None), "수정 요청인데 사유가 없음"),
                      (("SUBMITTED", "[]", 0, None, None, None), "확정본 판 번호 0"),
                      (("DRAFT", "[]", None, "가상", None, None), "종류 없이 메모만 있음"),
                      (("DRAFT", "[]", None, None, "RETURNED", None), "메모 없이 종류만 있음"),
                      (("DRAFT", "[]", None, "가상", "OTHER", None), "돌려보내기·거절이 아닌 메모 종류"),
                      (("DRAFT", "{}", None, None, None, None), "목록이 아닌 JSON 초안"), (("DRAFT", "[", None, None, None, None), "깨진 JSON 초안")):
        ok, msg = _rejects(conn, pl.format(u=user), (2028,) + args)
        _report(ok and "CHECK" in msg, f"계획서 {why} 거부", msg)
    ok, msg = _rejects(conn, pl.format(u=9999), (2028, "DRAFT", "[]", None, None, None, None))
    _report(ok and "FOREIGN KEY" in msg, "없는 직원의 계획서 거부", msg)
    ok, msg = _rejects(conn, pl.format(u=user), (2027, "DRAFT", "[]", None, None, None, None))
    _report(ok and "UNIQUE" in msg, "같은 사람 같은 해 계획서 두 줄 거부", msg)

    # 확정본 — 판이 쌓이기만 하고 고치거나 지울 수 없다
    boss = conn.execute("SELECT id FROM users WHERE employee_no='T020'").fetchone()[0]
    vs = "INSERT INTO kpi_versions (user_id, year, version, content, confirmed_at, confirmed_by, on_behalf_of) VALUES ({u}, ?, ?, ?, '2027-03-05T09:00:00Z', {u}, ?)"
    conn.execute(vs.format(u=user), (2027, 1, "[]", None))
    _report(True, "확정본 판 1 저장")
    conn.execute(f"UPDATE kpi_plans SET status = 'CONFIRMED', confirmed_version = 1 WHERE user_id = {user} AND year = 2027")
    _report(True, "계획서를 확정(CONFIRMED · 확정본 판 1)으로 바꿈")
    conn.execute(vs.format(u=user), (2027, 2, "[]", boss))
    _report(True, "다른 사람이 대신 확정한 판 2 저장 (대신한 사람 기록)")
    for args, why, word in (((2027, 0, "[]", None), "판 0", "CHECK"), ((2027, 3, "{}", None), "목록이 아닌 JSON 내용", "CHECK"),
                      ((2027, 3, "[", None), "깨진 JSON 내용", "CHECK"), ((2027, 1, "[]", None), "같은 판 두 번 저장", "UNIQUE"),
                      ((2031, 1, "[]", None), "없는 계획서에 붙이기", "FOREIGN KEY"), ((2027, 3, "[]", 9999), "없는 사람이 대신한 것으로 적기", "FOREIGN KEY")):
        ok, msg = _rejects(conn, vs.format(u=user), args)
        _report(ok and word in msg, f"확정본 {why} 거부", msg)
    ok, msg = _rejects(conn, "INSERT INTO kpi_versions (user_id, year, version, content, confirmed_at, confirmed_by) VALUES (" + user + ", 2027, 3, '[]', '2027-03-05T09:00:00Z', 9999)")
    _report(ok and "FOREIGN KEY" in msg, "없는 사람이 확정한 확정본 거부", msg)
    ok, msg = _rejects(conn, f"UPDATE kpi_versions SET content = '[1]' WHERE user_id = {user} AND year = 2027 AND version = 1")
    _report(ok and "확정본" in msg, "확정본 고치기 거부", msg)
    ok, msg = _rejects(conn, f"DELETE FROM kpi_versions WHERE user_id = {user} AND year = 2027 AND version = 1")
    _report(ok and "확정본" in msg, "확정본 지우기 거부", msg)
    ok, msg = _rejects(conn, f"DELETE FROM kpi_plans WHERE user_id = {user} AND year = 2027")
    _report(ok and "FOREIGN KEY" in msg, "확정본이 있는 계획서 지우기 거부", msg)

    # 대표이사 "HR에 맡기기" — 줄이 있으면 켜진 것
    cd = "INSERT INTO ceo_delegation (ceo_user_id, since) VALUES ({u}, '2027-03-02T09:00:00Z')"
    conn.execute(cd.format(u=user))
    _report(True, "HR에 맡기기 켜기 (줄 하나)")
    ok, msg = _rejects(conn, cd.format(u=user))
    _report(ok and "UNIQUE" in msg, "이미 켠 사람이 또 켜기 거부", msg)
    ok, msg = _rejects(conn, cd.format(u=9999))
    _report(ok and "FOREIGN KEY" in msg, "없는 직원의 맡기기 거부", msg)


def check_payslip_tables(conn: sqlite3.Connection) -> None:
    """급여명세서 표 (011). 금액·이름은 전부 가상 — data 는 잠근 글(nonce 12 + 내용 + 태그 16바이트)을 흉내 낸 아무 바이트."""
    print("23. 급여명세서 (011)")
    up = conn.execute("SELECT id FROM users WHERE employee_no='T001'").fetchone()[0]
    emp = conn.execute(
        "INSERT INTO users (employee_no, name, email, department_id)"
        " VALUES ('T031', '가상명세직원', 't031@example.invalid', (SELECT id FROM departments WHERE code='DIV1')) RETURNING id"
    ).fetchone()[0]
    ver = conn.execute("PRAGMA user_version").fetchone()[0]
    _report(ver >= 11, f"판 번호가 11 이상 — {ver}", f"실제 {ver}")

    mo = "INSERT INTO payslip_months (ym, pay_date, status, people, uploaded_at, uploaded_by) VALUES (?, ?, ?, ?, '2027-03-02T09:00:00Z', ?)"
    conn.execute(mo, ("2026-05", "2026-06-19", "DRAFT", 2, up))
    _report(True, "급여명세서 달 한 건 저장 (올려 둠)")
    for args, why in ((("2026-13", "2026-06-19", "DRAFT", 2, up), "13월"), (("2026-00", "2026-06-19", "DRAFT", 2, up), "0월"),
                      (("26-05", "2026-06-19", "DRAFT", 2, up), "귀속 월 연도 두 자리"), ((None, "2026-06-19", "DRAFT", 2, up), "귀속 월 비어 있음(NULL)"), (("2026-5", "2026-06-19", "DRAFT", 2, up), "귀속 월 월 한 자리"),
                      (("2026-05", "2026-06-19", "DRAFT", 2, up), "같은 달 두 번"), (("2026-06", "2026-6-19", "DRAFT", 2, up), "지급일 모양"),
                      (("2026-06", "2026-02-30", "DRAFT", 2, up), "없는 지급일(2월 30일)"), (("2026-06", "2026-19-39", "DRAFT", 2, up), "없는 지급일(19월 39일)"),
                      (("2026-06", "2026-06-19", "SENT", 2, up), "상태 SENT"), (("2026-06", "2026-06-19", "DRAFT", 0, up), "0명"), (("2026-06", "2026-06-19", "DRAFT", 1.5, up), "1.5명(정수 아님)"),
                      (("2026-06", "2026-06-19", "DRAFT", 2, 9999), "없는 올린 사람")):
        ok, _ = _rejects(conn, mo, args)
        _report(ok, f"급여명세서 달 {why} 거부")
    ok, msg = _rejects(conn, "INSERT INTO payslip_months (ym, pay_date, status, people, uploaded_at, uploaded_by, opened_at, opened_by)"
                             f" VALUES ('2026-06', '2026-07-20', 'OPEN', 2, '2027-03-02T09:00:00Z', {up}, NULL, {up})")
    _report(ok and "payslip_open_chk" in msg, "공개(OPEN)인데 공개한 때가 없으면 거부", msg)
    conn.execute("INSERT INTO payslip_months (ym, pay_date, status, people, revised, uploaded_at, uploaded_by, opened_at, opened_by)"
                 f" VALUES ('2026-06', '2026-07-20', 'OPEN', 2, 1, '2027-03-02T09:00:00Z', {up}, '2027-03-02T10:00:00Z', {up})")
    conn.execute("INSERT INTO payslip_months (ym, pay_date, status, people, uploaded_at, uploaded_by, opened_at, opened_by)"
                 f" VALUES ('2026-07', '2026-08-20', 'DRAFT', 1, '2027-03-02T09:00:00Z', {up}, '2027-03-02T10:00:00Z', {up})")
    _report(True, "공개 중(OPEN) · 내렸던 달(DRAFT 이면서 공개한 때 있음) 저장")
    ok, _ = _rejects(conn, "UPDATE payslip_months SET revised = 2 WHERE ym = '2026-05'")
    _report(ok, "수정됨 표시는 0·1 만")

    ps = "INSERT INTO payslips (ym, user_id, data) VALUES (?, ?, ?)"
    conn.execute(ps, ("2026-05", up, bytes(29)))
    conn.execute(ps, ("2026-05", emp, bytes(range(60))))
    _report(True, "잠근 명세서 두 건 저장 (29바이트 이상 BLOB)")
    seen = conn.execute("SELECT seen_at FROM payslips WHERE ym='2026-05' AND user_id = ?", (up,)).fetchone()[0]
    _report(seen is None, "처음엔 아직 안 봄 (seen_at 비어 있음)", f"실제 {seen}")
    for args, why in ((("2026-05", up, bytes(29)), "같은 달 같은 사람 두 줄"), (("2026-05", 9999, bytes(29)), "없는 직원"),
                      (("2027-01", up, bytes(29)), "없는 달"), (("2026-06", up, "abc"), "BLOB 이 아닌 글자"), (("2026-06", up, "x" * 40), "BLOB 이 아닌 긴 글자(길이는 충분)"),
                      (("2026-06", up, bytes(28)), "28바이트(내용 없음)"), (("2026-06", up, None), "빈 값")):
        ok, _ = _rejects(conn, ps, args)
        _report(ok, f"잠근 명세서 {why} 거부")
    ok, _ = _rejects(conn, "DELETE FROM users WHERE id = ?", (emp,))
    _report(ok, "명세서가 있는 직원은 지울 수 없음 (RESTRICT)")
    idx = conn.execute("SELECT 1 FROM pragma_index_info('idx_payslips_user') WHERE name = 'user_id'").fetchone()
    _report(idx is not None, "직원별 찾기 색인 (idx_payslips_user)")

    conn.execute("DELETE FROM payslip_months WHERE ym = '2026-05'")
    left = conn.execute("SELECT count(*) FROM payslips WHERE ym = '2026-05'").fetchone()[0]
    _report(left == 0, "달을 지우면 그 달 명세서도 함께 지워짐 (CASCADE)", f"남은 {left}")


def check_kpi_progress_tables(conn: sqlite3.Connection) -> None:
    """KPI 진행 기록 표 (012 — main #165 급여명세서가 011 을 먼저 써서 판 12). 계획서는 22 절(check_login_tables)이 만든 것을 쓴다."""
    print("24. KPI 진행 기록 (012)")
    user = "(SELECT id FROM users WHERE employee_no='T001')"
    boss = conn.execute("SELECT id FROM users WHERE employee_no='T020'").fetchone()[0]
    ver = conn.execute("PRAGMA user_version").fetchone()[0]
    _report(ver >= 12, f"판 번호가 12 이상 — {ver}", f"실제 {ver}")
    # 계획서는 22 절에서 만든 것을 쓴다: 2027 확정 · 2029 제출 · 2030 수정 요청 / 2028 · 2031 은 계획서가 없다.
    T = "'2027-03-02T09:00:00Z'"
    ln = lambda n: "가" * n  # 글자 수 맞춘 가상 문장
    lk = lambda n: "https://example.invalid/" + "a" * (n - 24)  # 길이 n 인 가상 링크

    # 실행목표별 진행 (실적 · 진행률 · 늦어지는 이유)
    pg = "INSERT INTO kpi_progress (user_id, year, rid, actual, pct, why, updated_at) VALUES ({u}, ?, ?, ?, ?, ?, " + T + ")"
    conn.execute(pg.format(u=user), (2027, 1, 12.5, 40, "가상 이유"))
    conn.execute(pg.format(u=user), (2027, 2, None, None, None))
    _report(True, "진행 저장 (실적 · 진행률 · 이유 / 모두 비움)")
    conn.execute(pg.format(u=user), (2027, 3, 0, 100, ln(100)))
    _report(True, "진행 경계값 저장 (실적 0 · 진행률 100 · 이유 100자)")
    for args, why, word in (((2027, 4, 1, 101, None), "진행률 101", "CHECK"), ((2027, 4, 1, -1, None), "진행률 -1", "CHECK"),
                            ((2027, 4, -0.5, 10, None), "음수 실적", "CHECK"), ((2027, 4, 1, 10, ln(101)), "101자 이유", "CHECK"),
                            ((2027, 0, 1, 10, None), "실행목표 번호 0", "CHECK"), ((2027, 1, 1, 10, None), "같은 실행목표 두 줄", "UNIQUE"),
                            ((2031, 1, 1, 10, None), "계획서 없는 (직원, 해)", "FOREIGN KEY")):
        ok, msg = _rejects(conn, pg.format(u=user), args)
        _report(ok and word in msg, f"진행 {why} 거부", msg)

    # 월 한 줄
    mn = "INSERT INTO kpi_month_notes (user_id, year, rid, month, text, link, updated_at) VALUES ({u}, ?, ?, ?, ?, ?, " + T + ")"
    conn.execute(mn.format(u=user), (2027, 1, 3, "가상 월 한 줄", "https://example.invalid/a"))
    conn.execute(mn.format(u=user), (2027, 1, 12, ln(200), None))
    _report(True, "월 한 줄 저장 (링크 있음 / 12월 · 200자 · 링크 없음)")
    conn.execute(mn.format(u=user), (2027, 1, 1, "가상", lk(300)))
    _report(True, "월 한 줄 링크 300자 저장")
    for args, why, word in (((2027, 1, 13, "가상", None), "13월", "CHECK"), ((2027, 1, 0, "가상", None), "0월", "CHECK"),
                            ((2027, 1, 4, "", None), "빈 문장", "CHECK"), ((2027, 1, 4, ln(201), None), "201자 문장", "CHECK"),
                            ((2027, 1, 4, "가상", lk(301)), "301자 링크", "CHECK"),
                            ((2027, 0, 4, "가상", None), "실행목표 번호 0", "CHECK"),
                            ((2027, 1, 3, "가상", None), "같은 달 두 줄", "UNIQUE"),
                            ((2031, 1, 4, "가상", None), "계획서 없는 (직원, 해)", "FOREIGN KEY")):
        ok, msg = _rejects(conn, mn.format(u=user), args)
        _report(ok and word in msg, f"월 한 줄 {why} 거부", msg)

    # 일정별 결과 한 줄
    rs = "INSERT INTO kpi_results (user_id, year, sid, text, link, updated_at) VALUES ({u}, ?, ?, ?, ?, " + T + ")"
    conn.execute(rs.format(u=user), (2027, 1, "가상 결과", "https://example.invalid/r"))
    conn.execute(rs.format(u=user), (2027, 2, ln(200), None))
    _report(True, "결과 한 줄 저장 (링크 있음 / 200자 · 링크 없음)")
    for args, why, word in (((2027, 3, "", None), "빈 문장", "CHECK"), ((2027, 3, ln(201), None), "201자 문장", "CHECK"),
                            ((2027, 3, "가상", lk(301)), "301자 링크", "CHECK"),
                            ((2027, 0, "가상", None), "일정 번호 0", "CHECK"), ((2027, 1, "가상", None), "같은 일정 두 줄", "UNIQUE"),
                            ((2031, 1, "가상", None), "계획서 없는 (직원, 해)", "FOREIGN KEY")):
        ok, msg = _rejects(conn, rs.format(u=user), args)
        _report(ok and word in msg, f"결과 한 줄 {why} 거부", msg)

    # 일정 변경 요청 — 한 일정에 대기 중인 요청은 하나뿐이고, 대기 중이어야만 결정한 때가 비어 있다
    sc = ("INSERT INTO kpi_sched_changes (user_id, year, sid, from_due, to_due, reason, status, note, requested_at, decided_at, decided_by, on_behalf_of)"
          " VALUES ({u}, ?, ?, ?, ?, ?, ?, ?, " + T + ", ?, ?, ?)")
    conn.execute(sc.format(u=user), (2027, 1, "2027-03-31", "2027-04-30", "가상 사유", "PENDING", None, None, None, None))
    _report(True, "일정 변경 요청 저장 (대기 중 · 결정한 때 비움)")
    conn.execute(sc.format(u=user), (2027, 2, "2027-05-31", "2027-06-30", "가상 사유", "PENDING", None, None, None, None))
    _report(True, "다른 일정의 대기 중 요청은 따로 저장")
    conn.execute(sc.format(u=user), (2027, 1, "2027-03-31", "2027-04-15", "가상 사유", "CONFIRMED", None, T[1:-1], boss, boss))
    conn.execute(sc.format(u=user), (2027, 1, "2027-03-31", "2027-04-20", "가상 사유", "CANCELLED", None, T[1:-1], None, None))
    conn.execute(sc.format(u=user), (2027, 1, "2027-03-31", "2027-04-25", ln(200), "RETURNED", ln(200), T[1:-1], boss, None))
    _report(True, "같은 일정에 결정이 끝난 요청 여러 건 저장 (확인 · 취소 · 돌려보내기 / 대신 처리 기록)")
    ok, msg = _rejects(conn, sc.format(u=user), (2027, 1, "2027-03-31", "2027-05-10", "가상 사유", "PENDING", None, None, None, None))
    _report(ok and "UNIQUE" in msg, "같은 일정에 대기 중 요청 둘 거부", msg)
    for args, why, word in (((2027, 3, "2027-03-31", "2027-04-30", "가상 사유", "RETURNED", None, T[1:-1], boss, None), "이유 없이 돌려보낸 요청", "CHECK"),
                            ((2027, 3, "2027-03-31", "2027-04-30", "가상 사유", "PENDING", None, T[1:-1], None, None), "대기 중인데 결정한 때가 있음", "CHECK"),
                            ((2027, 3, "2027-03-31", "2027-04-30", "가상 사유", "CONFIRMED", None, None, boss, None), "결정이 났는데 결정한 때가 없음", "CHECK"),
                            ((2027, 3, "2027-02-30", "2027-04-30", "가상 사유", "PENDING", None, None, None, None), "없는 원래 날짜 2027-02-30", "CHECK"),
                            ((2027, 3, "2027-03-31", "2027-4-30", "가상 사유", "PENDING", None, None, None, None), "날짜 모양이 아닌 바꿀 날짜", "CHECK"),
                            ((2027, 3, "2027-03-31", "2027-04-30", "", "PENDING", None, None, None, None), "빈 사유", "CHECK"),
                            ((2027, 3, "2027-03-31", "2027-04-30", ln(201), "PENDING", None, None, None, None), "201자 사유", "CHECK"),
                            ((2027, 3, "2027-03-31", "2027-04-30", "가상 사유", "OTHER", None, None, None, None), "정해진 것이 아닌 상태", "CHECK"),
                            ((2027, 3, "2027-03-31", "2027-04-30", "가상 사유", "CANCELLED", ln(201), T[1:-1], None, None), "201자 메모", "CHECK"),
                            ((2031, 3, "2027-03-31", "2027-04-30", "가상 사유", "PENDING", None, None, None, None), "계획서 없는 (직원, 해)", "FOREIGN KEY"),
                            ((2027, 3, "2027-03-31", "2027-04-30", "가상 사유", "CONFIRMED", None, T[1:-1], 9999, None), "없는 사람이 결정한 것으로 적기", "FOREIGN KEY"),
                            ((2027, 3, "2027-03-31", "2027-04-30", "가상 사유", "CONFIRMED", None, T[1:-1], boss, 9999), "없는 사람이 대신한 것으로 적기", "FOREIGN KEY")):
        ok, msg = _rejects(conn, sc.format(u=user), args)
        _report(ok and word in msg, f"일정 변경 {why} 거부", msg)

    # 기록이 달린 계획서는 지워지지 않는다 (RESTRICT) — 표마다 하나씩, 방금 넣은 줄은 바로 치운다
    for tbl, ins, args in (("kpi_progress", pg, (2029, 1, 1, 10, None)), ("kpi_month_notes", mn, (2029, 1, 3, "가상", None)),
                           ("kpi_results", rs, (2029, 1, "가상", None)),
                           ("kpi_sched_changes", sc, (2029, 1, "2029-03-31", "2029-04-30", "가상 사유", "PENDING", None, None, None, None))):
        conn.execute(ins.format(u=user), args)
        ok, msg = _rejects(conn, f"DELETE FROM kpi_plans WHERE user_id = {user} AND year = 2029")
        _report(ok and "FOREIGN KEY" in msg, f"{tbl} 기록이 있는 계획서 지우기 거부", msg)
        conn.execute(f"DELETE FROM {tbl} WHERE user_id = {user} AND year = 2029")


def check_withholding_tables(conn: sqlite3.Connection) -> None:
    """원천징수영수증 표 (013 — KPI 2단계 ① 이 012 를 먼저 써서 판 13). 금액·이름은 전부 가상 — sum · pdf 는 잠근 글(nonce 12 + 내용 + 태그 16바이트)을 흉내 낸 아무 바이트."""
    print("25. 원천징수영수증 (013)")
    up = conn.execute("SELECT id FROM users WHERE employee_no='T001'").fetchone()[0]
    emp = conn.execute(
        "INSERT INTO users (employee_no, name, email, department_id)"
        " VALUES ('T032', '가상영수직원', 't032@example.invalid', (SELECT id FROM departments WHERE code='DIV1')) RETURNING id"
    ).fetchone()[0]
    ver = conn.execute("PRAGMA user_version").fetchone()[0]
    _report(ver >= 13, f"판 번호가 13 이상 — {ver}", f"실제 {ver}")

    yr = "INSERT INTO withholding_years (year, status, pending, uploaded_at, uploaded_by) VALUES (?, ?, ?, '2027-03-02T09:00:00Z', ?)"
    conn.execute(yr, (2025, "DRAFT", 0, up))
    _report(True, "원천징수영수증 해 한 건 저장 (올려 둠)")
    for args, why in (((1999, "DRAFT", 0, up), "1999년"), ((2100, "DRAFT", 0, up), "2100년"), ((None, "DRAFT", 0, up), "해 비어 있음(NULL)"),
                      (("2026", "DRAFT", 0, up), "해가 글자('2026')"), ((2026, "X", 0, up), "상태 X"),
                      ((2026, "DRAFT", -1, up), "대기 -1명"), ((2026, "DRAFT", 1.5, up), "대기 1.5명(정수 아님)"),
                      ((2025, "DRAFT", 0, up), "같은 해 두 번"), ((2026, "DRAFT", 0, 9999), "없는 올린 사람")):
        ok, _ = _rejects(conn, yr, args)
        _report(ok, f"원천징수영수증 해 {why} 거부")
    oy = ("INSERT INTO withholding_years (year, status, pending, uploaded_at, uploaded_by, opened_at, opened_by)"
          " VALUES (?, 'OPEN', ?, '2027-03-02T09:00:00Z', ?, ?, ?)")
    ok, msg = _rejects(conn, oy, (2026, 0, up, None, up))
    _report(ok and "CHECK" in msg, "공개(OPEN)인데 공개한 때가 없으면 거부", msg)
    ok, msg = _rejects(conn, oy, (2026, 2, up, "2027-03-02T10:00:00Z", up))
    _report(ok and "CHECK" in msg, "공개(OPEN)인데 올릴 사람이 남아 있으면 거부", msg)
    conn.execute(oy, (2026, 0, up, "2027-03-02T10:00:00Z", up))
    conn.execute("INSERT INTO withholding_years (year, status, pending, uploaded_at, uploaded_by, opened_at, opened_by)"
                 f" VALUES (2027, 'DRAFT', 3, '2027-03-02T09:00:00Z', {up}, '2027-03-02T10:00:00Z', {up})")
    _report(True, "공개 중(OPEN · 대기 0) · 내렸던 해(DRAFT · 공개한 때 있음 · 대기 3) 저장")

    wh = "INSERT INTO withholding (year, user_id, sum, pdf, revised, uploaded_at) VALUES (?, ?, ?, ?, ?, '2027-03-02T09:00:00Z')"
    conn.execute(wh, (2025, up, bytes(29), bytes(29), 0))
    conn.execute(wh, (2025, emp, bytes(range(60)), bytes(range(60)), 1))
    _report(True, "잠근 영수증 두 건 저장 (29바이트 이상 BLOB)")
    seen = conn.execute("SELECT seen_at FROM withholding WHERE year=2025 AND user_id = ?", (up,)).fetchone()[0]
    _report(seen is None, "처음엔 아직 안 봄 (seen_at 비어 있음)", f"실제 {seen}")
    for args, why in (((2025, up, bytes(29), bytes(29), 0), "같은 해 같은 사람 두 줄"), ((2030, up, bytes(29), bytes(29), 0), "없는 해"),
                      ((2026, 9999, bytes(29), bytes(29), 0), "없는 직원"), ((2026, up, "x" * 40, bytes(29), 0), "sum 이 글자(길이는 충분)"),
                      ((2026, up, bytes(28), bytes(29), 0), "sum 28바이트(내용 없음)"), ((2026, up, bytes(29), bytes(28), 0), "pdf 28바이트(내용 없음)"),
                      ((2026, up, bytes(29), "x" * 40, 0), "pdf 가 글자(길이는 충분)"), ((2026, up, bytes(29), None, 0), "pdf 빈 값"),
                      ((2026, up, bytes(29), bytes(29), 2), "수정됨 표시 2")):
        ok, _ = _rejects(conn, wh, args)
        _report(ok, f"잠근 영수증 {why} 거부")
    ok, _ = _rejects(conn, "DELETE FROM users WHERE id = ?", (emp,))
    _report(ok, "영수증이 있는 직원은 지울 수 없음 (RESTRICT)")
    idx = conn.execute("SELECT 1 FROM pragma_index_info('idx_withholding_user') WHERE name = 'user_id'").fetchone()
    _report(idx is not None, "직원별 찾기 색인 (idx_withholding_user)")

    conn.execute("DELETE FROM withholding_years WHERE year = 2025")
    left = conn.execute("SELECT count(*) FROM withholding WHERE year = 2025").fetchone()[0]
    _report(left == 0, "해를 지우면 그 해 영수증도 함께 지워짐 (CASCADE)", f"남은 {left}")


def check_contract_live_tables(conn: sqlite3.Connection) -> None:
    """연봉계약서 표 (014 — 판 14). 이름·금액은 전부 가상 — 잠근 칸은 잠근 글(nonce 12 + 내용 + 태그 16바이트)을 흉내 낸 아무 바이트.
    막혀야 할 것은 "막혔는가"만 보지 않고 어느 규칙이 막았는지(메시지의 CHECK 이름 · UNIQUE · READ_ONLY …)까지 본다 — 표가 없어서 막힌 것이 통과로 세어지지 않게."""
    print("26. 연봉계약서 (014)")
    up = conn.execute("SELECT id FROM users WHERE employee_no='T001'").fetchone()[0]
    emp = conn.execute(
        "INSERT INTO users (employee_no, name, email, department_id)"
        " VALUES ('T033', '가상계약직원', 't033@example.invalid', (SELECT id FROM departments WHERE code='DIV1')) RETURNING id"
    ).fetchone()[0]
    ver = conn.execute("PRAGMA user_version").fetchone()[0]
    _report(ver >= 14, f"판 번호가 14 이상 — {ver}", f"실제 {ver}")
    B, T, H = bytes(29), "2027-01-10T09:00:00Z", "a" * 64
    ORDER = "정해진 순서"   # 상태 흐름 트리거 메시지에 들어 있는 말

    def rej(sql: str, params, word: str) -> tuple[bool, str]:
        ok, msg = _rejects(conn, sql, params)
        return ok and word in msg, msg

    # --- 직인 · 설정 -------------------------------------------------------------------------------------------------
    sl = "INSERT INTO contract_seals (png, created_at, created_by) VALUES (?, ?, ?)"
    seal = conn.execute(sl, (B, T, up)).lastrowid
    seal2 = conn.execute(sl, (bytes(range(60)), T, up)).lastrowid
    _report(True, "직인 그림 두 건 저장 (29바이트 이상 BLOB)")
    for args, why, word in (((bytes(28), T, up), "28바이트(내용 없음)", "CHECK"), (("x" * 40, T, up), "BLOB 이 아닌 글자(길이는 충분)", "CHECK"),
                            ((None, T, up), "빈 값", "NOT NULL"), ((B, T, 9999), "없는 올린 사람", "FOREIGN KEY")):
        ok, msg = rej(sl, args, word)
        _report(ok, f"직인 그림 {why} 거부", msg)
    st = "INSERT INTO contract_settings (id, data, seal_id, updated_at, updated_by) VALUES (?, ?, ?, ?, ?)"
    conn.execute(st, (1, B, seal, T, up))
    _report(True, "계약서 설정 한 줄 저장 (id = 1)")
    for args, why, word in (((2, B, seal, T, up), "id = 2", "CHECK"), ((1, B, seal, T, up), "id = 1 두 줄", "UNIQUE"),
                            ((1, B, None, T, up), "직인 없음", "NOT NULL"),
                            ((1, bytes(28), seal, T, up), "내용 28바이트", "CHECK"), ((1, "x" * 40, seal, T, up), "내용이 글자(길이는 충분)", "CHECK")):
        ok, msg = rej(st, args, word)
        _report(ok, f"계약서 설정 {why} 거부", msg)
    ok, msg = rej("UPDATE contract_settings SET seal_id = 9999 WHERE id = 1", (), "FOREIGN KEY")
    _report(ok, "계약서 설정 없는 직인 거부", msg)
    conn.execute("UPDATE contract_settings SET data = ?, seal_id = ?, updated_at = ? WHERE id = 1", (bytes(range(60)), seal2, T))
    _report(True, "계약서 설정은 바꿀 수 있음 (새 직인을 가리킴)")
    for sid, why in ((seal2, "설정이 가리키는 직인"), (conn.execute(sl, (B, T, up)).lastrowid, "아무도 안 쓰는 직인")):
        ok, msg = rej("DELETE FROM contract_seals WHERE id = ?", (sid,), "직인")
        _report(ok, f"{why} 지우기 거부 (옛 계약서가 그때 직인을 가리킨다)", msg)
    for col, val in (("png", bytes(range(70))), ("id", 9000), ("created_by", emp), ("created_at", "2027-01-11T09:00:00Z")):
        ok, msg = rej(f"UPDATE contract_seals SET {col} = ? WHERE id = ?", (val, seal), "READ_ONLY")
        _report(ok, f"직인 그림 {col} 바꾸기 거부 (옛 계약서가 가리키는 그림이 바뀌면 안 된다)", msg)

    # --- 계약서 한 줄 만들기 · 고치기 ----------------------------------------------------------------------------------
    base = dict(year=2027, user_id=emp, version=1, prev_id=None, status="DRAFT", starts_on="2027-02-01", body=B, created_at=T, created_by=up,
                sent_at=None, sent_by=None, due_on=None, company=None, seal_id=None, signed_at=None, sign=None, doc_hash=None, pdf=None,
                replaced_on=None, cancelled_at=None, cancelled_by=None, cancel_reason=None, ask=None, ask_at=None, ask_closed_at=None, ask_closed_by=None)
    sent = dict(status="SENT", sent_at=T, sent_by=up, due_on="2027-02-28", company=B, seal_id=seal)
    signed = dict(sent, status="SIGNED", signed_at=T, sign=B, doc_hash=H, pdf=B)
    cancelled = dict(sent, status="CANCELLED", cancelled_at=T, cancelled_by=up, cancel_reason="가상 사유")

    def row(parts, over):
        r = dict(base)
        for p in parts:
            r.update(p)
        r.update(over)
        return "INSERT INTO contracts (" + ", ".join(r) + ") VALUES (" + ", ".join(":" + k for k in r) + ")", r

    def add(*parts, **over) -> int:
        sql, r = row(parts, over)
        return conn.execute(sql, r).lastrowid

    def bad(word: str, *parts, **over) -> tuple[bool, str]:   # 이렇게 넣으면 `word` 규칙이 막아야 한다
        sql, r = row(parts, over)
        return rej(sql, r, word)

    def upd(cid: int, **sets) -> None:
        conn.execute("UPDATE contracts SET " + ", ".join(f"{k} = :{k}" for k in sets) + " WHERE id = :_id", dict(sets, _id=cid))

    def bad_upd(cid: int, word: str, **sets) -> tuple[bool, str]:
        return rej("UPDATE contracts SET " + ", ".join(f"{k} = :{k}" for k in sets) + " WHERE id = :_id", dict(sets, _id=cid), word)

    def blocked(cid: int, word: str, changes: dict) -> list[str]:   # 칸마다 바꿔 보아 막히지 않은 칸 이름들
        return [k for k, v in changes.items() if not bad_upd(cid, word, **{k: v})[0]]

    c1 = add(year=2027)
    _report(True, "발송 전(DRAFT) 한 건 저장")
    upd(c1, body=bytes(range(40)), starts_on="2027-03-01", due_on=None)
    _report(True, "발송 전 줄은 내용을 고칠 수 있음 (본문 · 계약 시작일)")
    upd(c1, **sent)
    _report(True, "발송 전 → 서명 대기(SENT) — 발송 칸을 채움")
    upd(c1, ask=B, ask_at=T)
    upd(c1, ask_closed_at=T, ask_closed_by=up)
    _report(True, "서명 대기 줄에도 문의 칸 · 문의 닫기 칸은 바뀜")
    sent_frozen = dict(year=2028, user_id=up, version=2, prev_id=c1, starts_on="2027-04-01", body=bytes(30), created_at="2027-01-11T09:00:00Z",
                       created_by=emp, sent_at="2027-01-12T09:00:00Z", sent_by=emp, due_on="2027-03-31", company=bytes(30), seal_id=seal2)
    miss = blocked(c1, "READ_ONLY", sent_frozen)
    _report(not miss, f"발송한 뒤에는 {len(sent_frozen)}칸(회차 · 직원 · 차수 · 앞 차수 · 시작일 · 본문 · 만든 · 발송 · 마감일 · 회사 · 직인)이 안 바뀜", f"막히지 않은 칸 {miss}")
    upd(c1, status="SIGNED", signed_at=T, sign=B, doc_hash=H, pdf=B)
    _report(True, "서명 대기 → 서명 완료(SIGNED) — 서명 칸을 채움")
    signed_frozen = dict(signed_at="2027-01-13T09:00:00Z", sign=bytes(30), doc_hash="b" * 64, pdf=bytes(30), body=bytes(30), due_on="2027-03-31")
    miss = blocked(c1, "READ_ONLY", signed_frozen)
    _report(not miss, "서명한 뒤에는 서명 · 문서 고유번호 · 서명본 · 본문이 안 바뀜", f"막히지 않은 칸 {miss}")
    upd(c1, ask=bytes(30), ask_at="2027-01-14T09:00:00Z")
    upd(c1, ask_closed_at="2027-01-15T09:00:00Z")
    _report(True, "서명한 뒤에도 문의 칸 · 문의 닫기 칸(ask_closed_at)은 바뀜")

    # 연중 변경 — 새 차수(DRAFT)는 서명된 줄과 함께 있을 수 있고, 둘 다 SIGNED 인 순간은 없다
    c2 = add(year=2027, version=2, prev_id=c1)
    _report(True, "서명 완료 줄이 있는 회차에 새 차수(DRAFT) 저장 (prev_id = 앞 차수)")
    for over, why in ((dict(version=3), "DRAFT 둘"), (dict(version=3, **sent), "DRAFT 가 있는데 SENT")):
        ok, msg = bad("UNIQUE", **over)
        _report(ok and "version" not in msg, f"같은 회차 · 직원에 {why} 거부", msg)
    upd(c2, **sent)
    ok, msg = bad("UNIQUE", version=3)
    _report(ok and "version" not in msg, "같은 회차 · 직원에 SENT 가 있는데 DRAFT 거부", msg)
    ok, msg = bad_upd(c2, "UNIQUE", status="SIGNED", signed_at=T, sign=B, doc_hash=H, pdf=B)
    _report(ok and "version" not in msg, "같은 회차 · 직원에 SIGNED 둘 거부 (옛 줄을 REPLACED 로 바꾸기 전)", msg)
    ok, msg = bad("UNIQUE", signed, version=3)
    _report(ok and "version" not in msg, "SIGNED 를 하나 더 넣는 것도 거부", msg)
    ok, msg = bad_upd(c1, "contracts_replaced_chk", status="REPLACED")
    _report(ok, "새 계약으로 바뀜(REPLACED)인데 바뀐 날이 없으면 거부", msg)
    upd(c1, status="REPLACED", replaced_on="2027-06-01")
    upd(c2, status="SIGNED", signed_at=T, sign=B, doc_hash=H, pdf=B)
    _report(True, "서명 완료 → 새 계약으로 바뀜(REPLACED) 다음 새 줄 서명 완료 — 흐름 끝까지 저장")
    miss = blocked(c1, "READ_ONLY", dict(ask=bytes(31), ask_at="2027-01-16T09:00:00Z"))
    _report(not miss, "새 계약으로 바뀐 줄은 문의 칸(ask · ask_at)이 안 바뀜", f"막히지 않은 칸 {miss}")
    upd(c1, ask_closed_at="2027-06-02T09:00:00Z", ask_closed_by=up)
    _report(True, "새 계약으로 바뀐 줄도 문의 닫기 칸은 바뀜")

    # 파기 — SENT 에서도 SIGNED 에서도 가고, 파기한 회차에 다시 보낼 수 있다
    c3 = add(year=2028, **sent)
    for k in ("cancelled_at", "cancelled_by", "cancel_reason"):
        ok, msg = bad_upd(c3, "contracts_cancelled_chk", status="CANCELLED", **{x: v for x, v in cancelled.items() if x in ("cancelled_at", "cancelled_by", "cancel_reason") and x != k})
        _report(ok, f"파기(CANCELLED)인데 {k} 가 없으면 거부", msg)
    upd(c3, status="CANCELLED", cancelled_at=T, cancelled_by=up, cancel_reason="가상 사유")
    _report(True, "서명 대기 → 파기(CANCELLED)")
    ok, msg = bad_upd(c3, ORDER, status="SENT")
    _report(ok, "파기한 줄을 SENT 로 되돌리기 거부", msg)
    miss = blocked(c3, "READ_ONLY", dict(ask=B, ask_at=T))
    _report(not miss, "파기한 줄은 문의 칸이 안 바뀜", f"막히지 않은 칸 {miss}")
    upd(c3, ask_closed_at=T)
    c4 = add(signed, year=2029)
    upd(c4, status="CANCELLED", cancelled_at=T, cancelled_by=up, cancel_reason="가상 사유")
    _report(True, "서명 완료 → 파기 (서명 칸은 그대로 남음)")
    c5 = add(year=2029, version=2, prev_id=c4)
    _report(True, "파기한 회차에 새 차수(DRAFT) 다시 저장 (재발송)")
    ok, msg = bad("UNIQUE", year=2029, version=2, status="CANCELLED", **{k: v for k, v in cancelled.items() if k != "status"})
    _report(ok and "version" in msg, "같은 회차 · 직원 · 차수 두 줄 거부", msg)

    # --- 상태 흐름 : 5 x 5 모든 짝 ---------------------------------------------------------------------------------------
    ids = {"DRAFT": add(year=2041), "SENT": add(sent, year=2042), "SIGNED": add(signed, year=2043),
           "REPLACED": add(signed, year=2044, status="REPLACED", replaced_on="2027-06-01"), "CANCELLED": add(cancelled, year=2045)}
    legal = {("DRAFT", "SENT"), ("SENT", "SIGNED"), ("SENT", "CANCELLED"), ("SIGNED", "REPLACED"), ("SIGNED", "CANCELLED")}
    wrong = []
    for a in ids:
        for b in ids:
            if a == b:
                continue
            ok, msg = _rejects(conn, "UPDATE contracts SET status = ? WHERE id = ?", (b, ids[a]))   # 상태만 바꿔 본다 — 칸 규칙(CHECK)에는 걸려도 흐름 규칙은 안 걸려야 하는 짝
            if (a, b) in legal:
                if not (ok and "CHECK" in msg and ORDER not in msg):
                    wrong.append(f"{a}→{b}")
            elif not (ok and ORDER in msg):
                wrong.append(f"{a}→{b}")
    _report(not wrong, "상태는 DRAFT→SENT · SENT→SIGNED · SENT→CANCELLED · SIGNED→REPLACED · SIGNED→CANCELLED 만 (나머지 15짝 거부)", f"어긋난 짝 {wrong}")
    d = add(year=2030)
    ok, msg = bad_upd(d, ORDER, **signed)
    _report(ok, "DRAFT→SIGNED 건너뛰기 거부 (서명 칸을 다 채워도)", msg)
    ok, msg = bad_upd(ids["CANCELLED"], ORDER, status="SENT")
    _report(ok, "CANCELLED→SENT 거부", msg)

    # --- 지우기 : DRAFT 만 --------------------------------------------------------------------------------------------
    for name in ("SENT", "SIGNED", "REPLACED", "CANCELLED"):
        ok, msg = rej("DELETE FROM contracts WHERE id = ?", (ids[name],), "지울 수 없어요")
        _report(ok, f"{name} 줄 지우기 거부", msg)
    conn.execute("DELETE FROM contracts WHERE id IN (?, ?)", (ids["DRAFT"], d))
    left = conn.execute("SELECT count(*) FROM contracts WHERE id IN (?, ?)", (ids["DRAFT"], d)).fetchone()[0]
    _report(left == 0, "DRAFT 줄은 지워짐", f"남은 {left}")

    # --- 칸 모양 (넣는 것) — 회차 2050 은 비어 있다 ------------------------------------------------------------------------
    Y = dict(year=2050)
    rows = (
        (dict(year="2027"), "회차가 글자('2027')", "CHECK"), (dict(year=None), "회차 비어 있음(NULL)", "NOT NULL"), (dict(year=1999), "회차 1999", "CHECK"),
        (dict(year=2100), "회차 2100", "CHECK"), (dict(year=2050.5), "회차 2050.5(정수 아님)", "CHECK"),
        (dict(user_id=9999), "없는 직원", "FOREIGN KEY"), (dict(user_id=None), "직원 비어 있음", "NOT NULL"),
        (dict(version=0), "차수 0", "CHECK"), (dict(version=1.5), "차수 1.5(정수 아님)", "CHECK"), (dict(version=None), "차수 비어 있음", "NOT NULL"),
        (dict(prev_id=9999), "없는 앞 차수", "FOREIGN KEY"), (dict(status="X"), "상태 X", "CHECK"),
        (dict(starts_on="2027-02-30"), "없는 날(2월 30일)이 시작일", "CHECK"), (dict(starts_on="2027-2-3"), "시작일 모양(한 자리)", "CHECK"),
        (dict(starts_on="2027/02/03"), "시작일 모양(/)", "CHECK"), (dict(starts_on=None), "시작일 비어 있음", "NOT NULL"),
        (dict(body=bytes(28)), "본문 28바이트(내용 없음)", "CHECK"), (dict(body="x" * 40), "본문이 글자(길이는 충분)", "CHECK"), (dict(body=None), "본문 비어 있음", "NOT NULL"),
        (dict(created_by=9999), "없는 만든 사람", "FOREIGN KEY"), (dict(created_at=None), "만든 때 비어 있음", "NOT NULL"),
        (dict(ask=bytes(28)), "문의 28바이트(내용 없음)", "CHECK"), (dict(ask="x" * 40), "문의가 글자(길이는 충분)", "CHECK"),
        (dict(sent_at=T), "DRAFT 인데 발송한 때가 있음", "contracts_draft_chk"), (dict(signed_at=T), "DRAFT 인데 서명한 때가 있음", "contracts_draft_chk"),
        (dict(sent, signed_at=T), "SENT 인데 서명한 때가 있음", "contracts_sent_chk"),
        (dict(sent, due_on="2027-02-30"), "SENT 의 마감일이 없는 날", "CHECK"), (dict(sent, due_on="2027-2-28"), "SENT 의 마감일 모양", "CHECK"),
        (dict(sent, company=bytes(28)), "SENT 의 회사 칸 28바이트", "CHECK"), (dict(sent, seal_id=9999), "SENT 의 없는 직인", "FOREIGN KEY"),
        (dict(signed, sign=bytes(28)), "SIGNED 의 서명 28바이트", "CHECK"), (dict(signed, pdf=bytes(28)), "SIGNED 의 PDF 28바이트", "CHECK"),
        (dict(signed, status="REPLACED"), "REPLACED 인데 바뀐 날이 없음", "contracts_replaced_chk"),
        (dict(signed, status="REPLACED", replaced_on="2027-02-30"), "REPLACED 의 바뀐 날이 없는 날", "CHECK"),
        (dict(cancelled, cancel_reason="가" * 201), "파기 사유 201자", "CHECK"), (dict(cancelled, cancel_reason=""), "파기 사유 빈 글자", "CHECK"),
        (dict(signed, doc_hash="a" * 63), "문서 고유번호 63자", "CHECK"), (dict(signed, doc_hash="a" * 65), "문서 고유번호 65자", "CHECK"),
        (dict(signed, doc_hash="A" * 64), "문서 고유번호 대문자", "CHECK"), (dict(signed, doc_hash="g" * 64), "문서 고유번호 16진수가 아닌 글자", "CHECK"),
        (dict(signed, doc_hash=""), "문서 고유번호 빈 글자", "CHECK"),
    )
    for part, why, word in rows:
        ok, msg = bad(word, Y, part)
        _report(ok, f"{why} 거부", msg)
    for name, part in (("SENT", sent), ("SIGNED", signed), ("CANCELLED", cancelled)):
        need = {"SENT": ("sent_at", "sent_by", "due_on", "company", "seal_id"),
                "SIGNED": ("sent_at", "sent_by", "due_on", "company", "seal_id", "signed_at", "sign", "doc_hash", "pdf"),
                "CANCELLED": ("cancelled_at", "cancelled_by", "cancel_reason")}[name]
        chk = {"SENT": "contracts_sent_chk", "SIGNED": "contracts_signed_chk", "CANCELLED": "contracts_cancelled_chk"}[name]
        miss = [k for k in need if not bad(chk, Y, dict(part, **{k: None}))[0]]
        _report(not miss, f"{name} 에 필요한 칸이 하나라도 비면 거부 ({len(need)}칸)", f"막히지 않은 칸 {miss}")
    add(Y, cancelled, cancel_reason="가" * 200)
    _report(True, "파기 사유는 200자(글자 수)까지 저장")

    # --- 직원 · 색인 --------------------------------------------------------------------------------------------------
    ok, msg = rej("DELETE FROM users WHERE id = ?", (emp,), "FOREIGN KEY")
    _report(ok, "계약서가 있는 직원은 지울 수 없음 (RESTRICT)", msg)
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'contracts'")}
    miss = {"uq_contracts_signed", "uq_contracts_open", "uq_contracts_version", "idx_contracts_live_user"} - names
    _report(not miss, "색인 (uq_contracts_signed · uq_contracts_open · uq_contracts_version · idx_contracts_live_user)", f"없는 색인 {sorted(miss)}")
    idx = conn.execute("SELECT 1 FROM pragma_index_info('idx_contracts_live_user') WHERE name = 'user_id'").fetchone()
    _report(idx is not None, "직원별 찾기 색인이 user_id 칸을 걸고 있음")


def main() -> int:
    print("=" * 62)
    print("SQLite 스키마 적용 + 제약조건 동작 검사")
    print("=" * 62)
    check_file_numbers()
    with tempfile.TemporaryDirectory() as tmp:
        conn = apply_schema(str(Path(tmp) / "schema-check.db"))
        if conn is None:
            return 1
        try:
            seed(conn)
            check_foreign_keys(conn)
            check_department_hierarchy(conn)
            check_evaluation_rules(conn)
            check_quota_rules(conn)
            check_kpi_rules(conn)
            check_contract_rules(conn)
            check_audit_log(conn)
            check_updated_at(conn)
            check_login_tables(conn)
            check_payslip_tables(conn)
            check_kpi_progress_tables(conn)
            check_withholding_tables(conn)
            check_contract_live_tables(conn)
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
