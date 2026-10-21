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

    print("23. KPI 진행 기록 (011)")
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


def main() -> int:
    print("=" * 62)
    print("SQLite 스키마 적용 + 제약조건 동작 검사")
    print("=" * 62)
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
