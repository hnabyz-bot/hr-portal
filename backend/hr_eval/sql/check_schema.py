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
        "kpi_month_checks",
        "kpi_memos",
        "kpi_memo_replies",
        "kpi_mutes",
        "game_hours",
        "push_subscriptions",
        "push_morning",
        "pay_pins",
        "ev_rounds",
        "ev_targets",
        "ev_items",
        "ev_files",
        "ev_pools",
        "ev_appeals",
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


def check_kpi_review_tables(conn: sqlite3.Connection) -> None:
    """KPI 현황 · 월간 확인 표 (014 — 원천징수영수증이 013 을 먼저 써서 판 14) + kpi_years 에 더한 알람 기준 · 경영계획 칸. 계획서는 22 절(check_login_tables)이 만든 것을 쓴다."""
    print("26. KPI 현황 · 월간 확인 (014)")
    uid = conn.execute("SELECT id FROM users WHERE employee_no='T001'").fetchone()[0]
    user = "(SELECT id FROM users WHERE employee_no='T001')"
    boss = conn.execute("SELECT id FROM users WHERE employee_no='T020'").fetchone()[0]
    ver = conn.execute("PRAGMA user_version").fetchone()[0]
    _report(ver >= 14, f"판 번호가 14 이상 — {ver}", f"실제 {ver}")
    # 계획서는 22 절에서 만든 것을 쓴다: 2027 확정 · 2029 제출 · 2030 수정 요청 / 2028 · 2031 은 계획서가 없다.
    T = "'2027-03-02T09:00:00Z'"
    ln = lambda n: "가" * n  # 글자 수 맞춘 가상 문장

    # 알람 기준 (해마다, 기본 20) · 경영계획 (목표 목록 JSON)
    row = conn.execute("SELECT alarm_gap, biz_plan FROM kpi_years WHERE year = 2027").fetchone()
    _report(row == (20, None), "이미 있던 해 줄은 알람 기준 20 · 경영계획 비어 있음", f"실제 {row}")
    conn.execute("INSERT INTO kpi_years (year, edit_from, edit_to, eval_start, updated_at) VALUES (2032, NULL, NULL, NULL, " + T + ")")
    gap = conn.execute("SELECT alarm_gap FROM kpi_years WHERE year = 2032").fetchone()[0]
    _report(gap == 20, "기간 줄을 새로 넣으면 알람 기준이 20", f"실제 {gap}")
    for g in (1, 100):
        conn.execute("UPDATE kpi_years SET alarm_gap = ? WHERE year = 2027", (g,))
    _report(True, "알람 기준 경계값 저장 (1 · 100)")
    for g, why, word in ((0, "0", "CHECK"), (101, "101", "CHECK"), (-5, "-5", "CHECK"), (None, "비어 있음(NULL)", "NOT NULL")):
        ok, msg = _rejects(conn, "UPDATE kpi_years SET alarm_gap = ? WHERE year = 2027", (g,))
        _report(ok and word in msg, f"알람 기준 {why} 거부", msg)
    conn.execute("UPDATE kpi_years SET alarm_gap = 20 WHERE year = 2027")
    for bp in ('[{"goal": "가상 목표", "detail": "가상 내용"}]', "[]"):
        conn.execute("UPDATE kpi_years SET biz_plan = ? WHERE year = 2027", (bp,))
    conn.execute("UPDATE kpi_years SET biz_plan = NULL WHERE year = 2027")
    _report(True, "경영계획 저장 (목표 목록 / 빈 목록 / 다시 비움)")
    for bp, why in (("{}", "목록이 아닌 JSON"), ('"가상"', "글자 JSON"), ("[", "깨진 JSON")):
        ok, msg = _rejects(conn, "UPDATE kpi_years SET biz_plan = ? WHERE year = 2027", (bp,))
        _report(ok and "CHECK" in msg, f"경영계획 {why} 거부", msg)

    # 월간 확인 — 한 사람 한 해 한 달에 한 줄
    mc = "INSERT INTO kpi_month_checks (user_id, year, month, checked_at, checked_by) VALUES ({u}, ?, ?, " + T + ", ?)"
    for m in (1, 3, 12):
        conn.execute(mc.format(u=user), (2027, m, boss))
    _report(True, "월간 확인 저장 (1월 · 3월 · 12월)")
    for args, why, word in (((2027, 13, boss), "13월", "CHECK"), ((2027, 0, boss), "0월", "CHECK"),
                            ((2027, 3, boss), "같은 사람 같은 해 같은 달 두 줄", "UNIQUE"),
                            ((2027, 4, 9999), "없는 사람이 확인한 것으로 적기", "FOREIGN KEY"),
                            ((2027, 4, None), "확인한 사람 비움", "NOT NULL"),
                            ((2031, 4, boss), "계획서 없는 (직원, 해)", "FOREIGN KEY")):
        ok, msg = _rejects(conn, mc.format(u=user), args)
        _report(ok and word in msg, f"월간 확인 {why} 거부", msg)

    # 확인 메모 — 바로 위 관리자가 쓰고, 본인이 확인했다고 누르면 ack_at 이 찬다
    mm = "INSERT INTO kpi_memos (user_id, year, author_id, text, created_at) VALUES ({u}, ?, ?, ?, " + T + ")"
    memo = conn.execute(mm.format(u=user) + " RETURNING id", (2027, boss, "가상 메모")).fetchone()[0]
    conn.execute(mm.format(u=user), (2027, boss, ln(200)))
    _report(True, "확인 메모 저장 (짧은 글 / 200자)")
    left = conn.execute("SELECT edited_at, ack_at, seen_at FROM kpi_memos WHERE id = ?", (memo,)).fetchone()
    _report(left == (None, None, None), "처음엔 고친 때 · 확인한 때 · 답글 본 때가 모두 비어 있음", f"실제 {left}")
    for args, why, word in (((2027, boss, ""), "빈 글", "CHECK"), ((2027, boss, ln(201)), "201자 글", "CHECK"),
                            ((2027, uid, "가상 메모"), "본인이 본인에게 쓴 메모", "CHECK"),
                            ((2027, 9999, "가상 메모"), "없는 사람이 쓴 메모", "FOREIGN KEY"),
                            ((2031, boss, "가상 메모"), "계획서 없는 (직원, 해)", "FOREIGN KEY")):
        ok, msg = _rejects(conn, mm.format(u=user), args)
        _report(ok and word in msg, f"확인 메모 {why} 거부", msg)
    idx = [r[0] for r in conn.execute("SELECT name FROM pragma_index_info('kpi_memos_owner') ORDER BY seqno").fetchall()]
    _report(idx == ["user_id", "year"], "본인 · 해별 메모 찾기 색인 (kpi_memos_owner)", f"실제 {idx}")

    # 답글 — 본인('owner')과 쓴 사람('writer')이 주고받는다
    rp = "INSERT INTO kpi_memo_replies (memo_id, author_id, who, text, created_at) VALUES (?, ?, ?, ?, " + T + ")"
    conn.execute(rp, (memo, uid, "owner", "가상 답글"))
    conn.execute(rp, (memo, boss, "writer", ln(200)))
    _report(True, "답글 저장 (본인 / 쓴 사람 · 200자)")
    for args, why, word in (((memo, boss, "boss", "가상 답글"), "정해진 것이 아닌 who('boss')", "CHECK"),
                            ((memo, uid, "owner", ""), "빈 글", "CHECK"), ((memo, uid, "owner", ln(201)), "201자 글", "CHECK"),
                            ((9999, uid, "owner", "가상 답글"), "없는 메모에 단 답글", "FOREIGN KEY"),
                            ((memo, 9999, "owner", "가상 답글"), "없는 사람이 쓴 답글", "FOREIGN KEY")):
        ok, msg = _rejects(conn, rp, args)
        _report(ok and word in msg, f"답글 {why} 거부", msg)
    idx = [r[0] for r in conn.execute("SELECT name FROM pragma_index_info('kpi_memo_replies_memo') ORDER BY seqno").fetchall()]
    _report(idx == ["memo_id"], "메모별 답글 찾기 색인 (kpi_memo_replies_memo)", f"실제 {idx}")
    # 글쓴이는 지울 수 없다 (RESTRICT) — 다른 데 쓰이지 않는 새 사람으로 확인한다
    writer = conn.execute(
        "INSERT INTO users (employee_no, name, email, department_id)"
        " VALUES ('T033', '가상메모작성자', 't033@example.invalid', (SELECT id FROM departments WHERE code='DIV1')) RETURNING id"
    ).fetchone()[0]
    conn.execute(rp, (memo, writer, "writer", "가상 답글"))
    ok, msg = _rejects(conn, "DELETE FROM users WHERE id = ?", (writer,))
    _report(ok and "FOREIGN KEY" in msg, "답글을 쓴 사람은 지울 수 없음 (RESTRICT)", msg)
    conn.execute("DELETE FROM kpi_memo_replies WHERE author_id = ?", (writer,))
    conn.execute(mm.format(u=user), (2027, writer, "가상 메모"))
    ok, msg = _rejects(conn, "DELETE FROM users WHERE id = ?", (writer,))
    _report(ok and "FOREIGN KEY" in msg, "메모를 쓴 사람은 지울 수 없음 (RESTRICT)", msg)
    conn.execute("DELETE FROM kpi_memos WHERE author_id = ?", (writer,))
    conn.execute("DELETE FROM users WHERE id = ?", (writer,))
    # 메모를 지우면 그 답글도 함께 지워진다
    n = conn.execute("SELECT count(*) FROM kpi_memo_replies WHERE memo_id = ?", (memo,)).fetchone()[0]
    conn.execute("DELETE FROM kpi_memos WHERE id = ?", (memo,))
    gone = conn.execute("SELECT count(*) FROM kpi_memo_replies WHERE memo_id = ?", (memo,)).fetchone()[0]
    _report(n == 2 and gone == 0, "메모를 지우면 답글도 함께 지워짐 (CASCADE)", f"답글 {n} → {gone}")

    # 알람 끄기 — 실행목표 하나에 한 줄. why 글자는 화면 KV_MUTE 의 열쇠와 같다.
    mt = "INSERT INTO kpi_mutes (user_id, year, rid, why, text, until_month, set_at) VALUES ({u}, ?, ?, ?, ?, ?, " + T + ")"
    for args in ((2027, 1, "month", None, 6), (2027, 2, "year", None, 12), (2027, 3, "wait", None, 3),
                 (2027, 4, "etc", "가상 이유", 5), (2027, 5, "etc", ln(50), 4)):
        conn.execute(mt.format(u=user), args)
    _report(True, "알람 끄기 저장 (이번 달까지 · 올해 · 기다리는 중 · 직접 적기 / 50자)")
    for args, why, word in (((2027, 6, "etc", None, 5), "'직접 적기'인데 글이 없음", "CHECK"),
                            ((2027, 6, "wait", "가상", 5), "'직접 적기'가 아닌데 글이 있음", "CHECK"),
                            ((2027, 6, "year", None, 11), "'올해'인데 11월까지", "CHECK"),
                            ((2027, 6, "other", None, 5), "정해진 것이 아닌 이유", "CHECK"),
                            ((2027, 6, "etc", "", 5), "빈 글", "CHECK"), ((2027, 6, "etc", ln(51), 5), "51자 글", "CHECK"),
                            ((2027, 6, "month", None, 13), "13월까지", "CHECK"), ((2027, 6, "month", None, 0), "0월까지", "CHECK"),
                            ((2027, 0, "month", None, 5), "실행목표 번호 0", "CHECK"),
                            ((2027, 1, "month", None, 5), "같은 실행목표 두 줄", "UNIQUE"),
                            ((2031, 6, "month", None, 5), "계획서 없는 (직원, 해)", "FOREIGN KEY")):
        ok, msg = _rejects(conn, mt.format(u=user), args)
        _report(ok and word in msg, f"알람 끄기 {why} 거부", msg)

    # 기록이 달린 계획서는 지워지지 않는다 (RESTRICT) — 표마다 하나씩, 방금 넣은 줄은 바로 치운다
    for tbl, ins, args in (("kpi_month_checks", mc, (2029, 5, boss)), ("kpi_memos", mm, (2029, boss, "가상 메모")),
                           ("kpi_mutes", mt, (2029, 1, "month", None, 5))):
        conn.execute(ins.format(u=user), args)
        ok, msg = _rejects(conn, f"DELETE FROM kpi_plans WHERE user_id = {user} AND year = 2029")
        _report(ok and "FOREIGN KEY" in msg, f"{tbl} 기록이 있는 계획서 지우기 거부", msg)
        conn.execute(f"DELETE FROM {tbl} WHERE user_id = {user} AND year = 2029")


def check_game_hours_table(conn: sqlite3.Connection) -> None:
    """게임 오픈시간 (015 — KPI 2단계 ② 가 014 를 먼저 써서 판 15) — 한 줄짜리 표. 모양(시간대 · 날짜)은 backend/game.py hours_clean 이 본다."""
    print("27. 게임 오픈시간 (015)")
    hr = conn.execute("SELECT id FROM users WHERE employee_no='T001'").fetchone()[0]
    ver = conn.execute("PRAGMA user_version").fetchone()[0]
    _report(ver >= 15, f"판 번호가 15 이상 — {ver}", f"실제 {ver}")
    ins = "INSERT INTO game_hours (id, data, updated_at, updated_by) VALUES (?, ?, '2027-03-02T09:00:00Z', ?)"
    conn.execute(ins, (1, '{"slots": [], "weekend": "all", "days": [], "closed": null}', hr))
    _report(True, "오픈시간 한 줄 저장 (JSON 객체)")
    for args, why in (((1, "{}", hr), "두 번째 줄(같은 번호)"), ((2, "{}", hr), "번호 2"), ((3, "[]", hr), "객체가 아닌 JSON"),
                      ((4, "{", hr), "깨진 JSON"), ((5, "{}", 9999), "없는 바꾼 사람"), ((6, "{}", None), "바꾼 사람 비어 있음")):
        ok, _ = _rejects(conn, ins, args)
        _report(ok, f"오픈시간 {why} 거부")


def check_contract_live_tables(conn: sqlite3.Connection) -> None:
    """연봉계약서 표 (016 — 판 16 · KPI 현황 014 · 게임 오픈시간 015 가 먼저 써서 번호 016). 이름·금액은 전부 가상 — 잠근 칸은 잠근 글(nonce 12 + 내용 + 태그 16바이트)을 흉내 낸 아무 바이트.
    막혀야 할 것은 "막혔는가"만 보지 않고 어느 규칙이 막았는지(메시지의 CHECK 이름 · UNIQUE · READ_ONLY …)까지 본다 — 표가 없어서 막힌 것이 통과로 세어지지 않게."""
    print("28. 연봉계약서 (016)")
    up = conn.execute("SELECT id FROM users WHERE employee_no='T001'").fetchone()[0]
    emp = conn.execute(
        "INSERT INTO users (employee_no, name, email, department_id)"
        " VALUES ('T033', '가상계약직원', 't033@example.invalid', (SELECT id FROM departments WHERE code='DIV1')) RETURNING id"
    ).fetchone()[0]
    ver = conn.execute("PRAGMA user_version").fetchone()[0]
    _report(ver >= 16, f"판 번호가 16 이상 — {ver}", f"실제 {ver}")
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


def check_push_tables(conn: sqlite3.Connection) -> None:
    """휴대폰 알림 표 (017 — 판 17). 주소 · 키 값은 전부 가상(example.invalid). 누가 받는지(살아 있는 휴대폰 세션 · 활성 직원)는 표가 아니라 backend/push.py targets 가 거르고 auth_check.py 가 본다."""
    print("29. 휴대폰 알림 (017)")
    ver = conn.execute("PRAGMA user_version").fetchone()[0]
    _report(ver >= 17, f"판 번호가 17 이상 — {ver}", f"실제 {ver}")
    emp = conn.execute(
        "INSERT INTO users (employee_no, name, email, department_id)"
        " VALUES ('T034', '가상알림직원', 't034@example.invalid', (SELECT id FROM departments WHERE code='DIV1')) RETURNING id"
    ).fetchone()[0]
    ses = ("INSERT INTO sessions (token_hash, user_id, created_at, last_seen_at, expires_at, kind)"
           " VALUES (?, ?, '2027-03-02T09:00:00Z', '2027-03-02T09:00:00Z', '2027-04-01T09:00:00Z', 'phone')")
    conn.execute(ses, ("e" * 64, emp))
    conn.execute(ses, ("c" * 64, emp))  # 구독 없는 세션 — 없는 직원 거부를 따로 보려고
    sub = "INSERT INTO push_subscriptions (token_hash, user_id, endpoint, p256dh, auth, created_at) VALUES (?, ?, ?, ?, ?, '2027-03-02T09:01:00Z')"
    ep = "https://push.example.invalid/send/abc"
    conn.execute(sub, ("e" * 64, emp, ep, "k", "a"))
    _report(True, "휴대폰 세션의 구독 저장")

    def rej(sql: str, params: tuple, word: str) -> tuple[bool, str]:
        ok, msg = _rejects(conn, sql, params)
        return ok and word in msg, msg

    for args, why, word in ((("e" * 64, emp, ep, "k2", "a2"), "같은 세션에 두 번째 구독(덮어쓰기는 앱이 한다)", "UNIQUE"),
                            (("9" * 64, emp, ep, "k", "a"), "없는 세션", "FOREIGN KEY"),
                            (("c" * 64, 9999, ep, "k", "a"), "없는 직원", "FOREIGN KEY"),
                            ((None, emp, ep, "k", "a"), "세션 번호 비어 있음(NULL 은 외래키 검사를 건너뛴다)", "NOT NULL"),
                            (("8" * 64, emp, None, "k", "a"), "주소 비어 있음", "NOT NULL"),
                            (("8" * 64, emp, ep, None, "a"), "공개키 비어 있음", "NOT NULL"),
                            (("8" * 64, emp, ep, "k", None), "비밀값 비어 있음", "NOT NULL")):
        ok, msg = rej(sub, args, word)
        _report(ok, f"구독 {why} 거부", msg)
    idx = conn.execute("SELECT 1 FROM pragma_index_info('idx_push_subscriptions_user') WHERE name = 'user_id'").fetchone()
    _report(idx is not None, "직원별 찾기 색인이 user_id 칸을 걸고 있음")

    conn.execute("DELETE FROM sessions WHERE token_hash = ?", ("e" * 64,))
    gone = conn.execute("SELECT COUNT(*) FROM push_subscriptions WHERE token_hash = ?", ("e" * 64,)).fetchone()[0]
    _report(gone == 0, "세션이 지워지면 구독도 함께 지워짐")
    conn.execute(ses, ("d" * 64, emp))
    conn.execute(sub, ("d" * 64, emp, ep, "k", "a"))
    conn.execute("DELETE FROM users WHERE id = ?", (emp,))
    left = conn.execute("SELECT (SELECT COUNT(*) FROM sessions WHERE user_id = ?), (SELECT COUNT(*) FROM push_subscriptions WHERE user_id = ?)", (emp, emp)).fetchone()
    _report(left == (0, 0), "직원이 지워지면 세션 · 구독도 함께 지워짐 (구독이 지우기를 막지 않음)", f"실제 {left}")

    mo = "INSERT INTO push_morning (day, people, ran_at) VALUES (?, ?, '2027-03-02T00:00:05Z')"
    conn.execute(mo, ("2027-03-02", 3))
    _report(True, "아침 묶음 돌린 날 저장")
    for args, why, word in ((("2027-03-02", 5), "같은 날 두 번째", "UNIQUE"), (("2027-03-03", None), "보낸 사람 수 비어 있음", "NOT NULL"),
                            ((None, 1), "날짜 비어 있음", "NOT NULL")):
        ok, msg = rej(mo, args, word)
        _report(ok, f"아침 묶음 {why} 거부", msg)


def check_pay_pin_tables(conn: sqlite3.Connection) -> None:
    """급여·계약 비밀번호 표 (018 — 판 18). 해시 · 소금 값은 전부 가상. 번호 규칙 · 해시 계산은 표가 아니라 backend/paypin.py 가 하고 `python -m paypin check` 가 본다."""
    print("30. 급여 비밀번호 (018)")
    ver = conn.execute("PRAGMA user_version").fetchone()[0]
    _report(ver >= 18, f"판 번호가 18 이상 — {ver}", f"실제 {ver}")

    def person(no: str, name: str) -> int:
        return conn.execute(
            "INSERT INTO users (employee_no, name, email, department_id)"
            f" VALUES ('{no}', '{name}', '{no.lower()}@example.invalid', (SELECT id FROM departments WHERE code='DIV1')) RETURNING id"
        ).fetchone()[0]

    emp, emp2 = person("T035", "가상비밀번호직원"), person("T036", "가상비밀번호직원2")
    ins = "INSERT INTO pay_pins (user_id, pin_hash, salt, set_at, fails, locked_at) VALUES (?, ?, ?, '2027-03-02T09:00:00Z', ?, ?)"
    h, salt, at = "a" * 64, "b" * 32, "2027-03-02T09:05:00Z"
    conn.execute(ins, (emp, h, salt, 0, None))
    _report(True, "비밀번호 줄 저장 (틀린 횟수 0 · 잠긴 시각 없음)")

    def rej(sql: str, params: tuple, word: str) -> tuple[bool, str]:
        ok, msg = _rejects(conn, sql, params)
        return ok and word in msg, msg

    # emp2 는 줄이 없는 실제 직원 — 아래 CHECK 거부가 중복 · 외래키 때문이 아니라 정말 CHECK 때문인지 본다.
    for args, why, word in (((emp, h, salt, 0, None), "같은 직원의 두 번째 줄(다시 정하기는 줄을 지운 뒤에)", "UNIQUE"),
                            ((9999, h, salt, 0, None), "없는 직원", "FOREIGN KEY"),
                            ((None, h, salt, 0, None), "직원 번호 비어 있음", "NOT NULL"),
                            ((emp2, None, salt, 0, None), "해시 비어 있음", "NOT NULL"),
                            ((emp2, h, None, 0, None), "소금 비어 있음", "NOT NULL"),
                            ((emp2, h, salt, 6, at), "틀린 횟수 6", "CHECK"),
                            ((emp2, h, salt, -1, None), "틀린 횟수 -1", "CHECK"),
                            ((emp2, h, salt, 5, None), "5번 틀렸는데 잠긴 시각 없음", "CHECK"),
                            ((emp2, h, salt, 2, at), "5번 안 틀렸는데 잠긴 시각 있음", "CHECK")):
        ok, msg = rej(ins, args, word)
        _report(ok, f"비밀번호 {why} 거부", msg)
    conn.execute(ins, (emp2, h, salt, 5, at))
    _report(True, "5번 틀려 잠긴 줄 저장 (잠긴 시각 있음)")

    conn.execute("DELETE FROM users WHERE id IN (?, ?)", (emp, emp2))
    left = conn.execute("SELECT COUNT(*) FROM pay_pins WHERE user_id IN (?, ?)", (emp, emp2)).fetchone()[0]
    _report(left == 0, "직원이 지워지면 비밀번호 줄도 함께 지워짐", f"남은 줄 {left}")

    col = conn.execute("SELECT type, \"notnull\", dflt_value FROM pragma_table_info('sessions') WHERE name = 'pay_used_at'").fetchone()
    _report(col is not None and col[0] == "TEXT" and col[1] == 0 and col[2] is None, "sessions.pay_used_at 칸이 있고 비어 있어도 됨(기본 NULL)", f"실제 {col}")
    emp3 = person("T037", "가상비밀번호직원3")
    conn.execute("INSERT INTO sessions (token_hash, user_id, created_at, last_seen_at, expires_at)"
                 " VALUES (?, ?, '2027-03-02T09:00:00Z', '2027-03-02T09:00:00Z', '2027-03-02T21:00:00Z')", ("f" * 64, emp3))
    used = conn.execute("SELECT pay_used_at FROM sessions WHERE token_hash = ?", ("f" * 64,)).fetchone()[0]
    _report(used is None, "새 세션의 pay_used_at 은 NULL (잠긴 상태)", f"실제 {used}")
    conn.execute("DELETE FROM users WHERE id = ?", (emp3,))


def check_pay_ask_table(conn: sqlite3.Connection) -> None:
    """급여 문의 받는 사람 표 (019 — 판 19). 한 줄(id = 1)뿐 · 직원은 실제 있는 사람 · 받는 사람이 정해져 있으면 그 직원을 지울 수 없다 ·
    그 사람이 비활성이 되거나 HR 권한이 빠지면 줄이 지워진다(트리거). 사람은 전부 가상."""
    print("31. 급여 문의 받는 사람 (019)")
    ver = conn.execute("PRAGMA user_version").fetchone()[0]
    _report(ver >= 19, f"판 번호가 19 이상 — {ver}", f"실제 {ver}")
    hr = conn.execute(
        "INSERT INTO users (employee_no, name, email, department_id)"
        " VALUES ('T038', '가상문의담당', 't038@example.invalid', (SELECT id FROM departments WHERE code='DIV1')) RETURNING id").fetchone()[0]
    ins = "INSERT INTO pay_ask_to (id, user_id, updated_at, updated_by) VALUES (?, ?, '2027-03-02T09:00:00Z', ?)"
    conn.execute(ins, (1, hr, hr))
    _report(True, "받는 사람 줄 저장 (id 1)")
    for args, why, word in (((2, hr, hr), "두 번째 줄(id 2)", "CHECK"), ((1, hr, hr), "같은 id 1 한 번 더", "UNIQUE")):
        ok, msg = _rejects(conn, ins, args)
        _report(ok and word in msg, f"받는 사람 {why} 거부", msg)
    conn.execute("DELETE FROM pay_ask_to")
    for args, why, word in (((1, 9999, hr), "없는 직원", "FOREIGN KEY"), ((1, None, hr), "직원 비어 있음", "NOT NULL"),
                            ((1, hr, 9999), "없는 직원이 바꿈", "FOREIGN KEY")):
        ok, msg = _rejects(conn, ins, args)
        _report(ok and word in msg, f"받는 사람 {why} 거부", msg)
    conn.execute(ins, (1, hr, hr))
    ok, msg = _rejects(conn, "DELETE FROM users WHERE id = ?", (hr,))
    _report(ok and "FOREIGN KEY" in msg, "받는 사람으로 정한 직원은 지울 수 없음 (퇴직자는 비활성으로)", msg)
    n = lambda: conn.execute("SELECT COUNT(*) FROM pay_ask_to").fetchone()[0]  # noqa: E731
    conn.execute("INSERT INTO user_roles (user_id, role) VALUES (?, 'HR_ADMIN'), (?, 'TEAM_LEADER')", (hr, hr))
    conn.execute("DELETE FROM user_roles WHERE user_id = ? AND role = 'TEAM_LEADER'", (hr,))
    _report(n() == 1, "다른 권한(팀장)이 빠지면 줄은 그대로", f"남은 줄 {n()}")
    conn.execute("UPDATE users SET name = '가상문의담당2' WHERE id = ?", (hr,))
    _report(n() == 1, "활성인 채로 다른 칸이 바뀌면 줄은 그대로", f"남은 줄 {n()}")
    conn.execute("DELETE FROM user_roles WHERE user_id = ? AND role = 'HR_ADMIN'", (hr,))
    _report(n() == 0, "HR 권한이 빠지면 줄이 지워짐 (다시 HR 이 돼도 저절로 돌아오지 않음)", f"남은 줄 {n()}")
    conn.execute(ins, (1, hr, hr))
    conn.execute("UPDATE users SET is_active = 0, deactivated_at = '2027-03-03T00:00:00Z' WHERE id = ?", (hr,))
    _report(n() == 0, "비활성이 되면 줄이 지워짐", f"남은 줄 {n()}")
    conn.execute("DELETE FROM users WHERE id = ?", (hr,))


def check_eval_tables(conn: sqlite3.Connection) -> None:
    """인사평가 표 (020 — 판 20): 회차 · 평가 대상 · 실행목표 · 증빙 · 묶음 기록 · 이의 신청. 이름 · 글은 전부 가상.
    막혀야 할 것은 "막혔는가"만 보지 않고 어느 규칙이 막았는지(메시지의 CHECK 식 · UNIQUE · FOREIGN KEY …)까지 본다 — 표가 없어서 막힌 것이 통과로 세어지지 않게.
    평가 대상 · 실행목표 줄은 지울 수 없어서(트리거) 이 절은 줄을 남긴다 — 그래서 맨 뒤 절이다. 다음 절이 필요하면 이 줄들(2027 · 2029)을 그대로 쓴다."""
    print("32. 인사평가 (020)")
    ver = conn.execute("PRAGMA user_version").fetchone()[0]
    _report(ver >= 20, f"판 번호가 20 이상 — {ver}", f"실제 {ver}")
    up = conn.execute("SELECT id FROM users WHERE employee_no='T001'").fetchone()[0]    # 2027 확정본 판 1 · 2 가 있는 사람 (22 절)
    boss = conn.execute("SELECT id FROM users WHERE employee_no='T020'").fetchone()[0]

    def person(no: str, name: str) -> int:
        return conn.execute(
            "INSERT INTO users (employee_no, name, email, department_id)"
            f" VALUES ('{no}', '{name}', '{no.lower()}@example.invalid', (SELECT id FROM departments WHERE code='DIV1')) RETURNING id"
        ).fetchone()[0]

    p2, spare = person("T039", "가상평가대상2"), person("T040", "가상평가대상3")
    T, ln = "2027-03-02T09:00:00Z", (lambda n: "가" * n)  # 글자 수 맞춘 가상 문장

    def row(table: str, base: dict, over: dict) -> tuple[str, tuple]:
        cols = {**base, **over}
        return f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", tuple(cols.values())

    def put(table: str, base: dict, **over) -> None:
        conn.execute(*row(table, base, over))

    def bad(table: str, base: dict, word: str, why: str, **over) -> None:
        """over 로 바꾼 줄이 거부되고, 거부 메시지에 word(CHECK 식의 한 토막 · UNIQUE · FOREIGN KEY …)가 들어 있나."""
        ok, msg = _rejects(conn, *row(table, base, over))
        _report(ok and word in msg, f"{table} {why} 거부", msg)

    # --- 회차 (해마다 한 줄) ------------------------------------------------------------------------------------------
    RD = dict(year=2027, self_to="2027-02-14", evid_due="2027-02-20", score_to="2027-02-28", grade_to="2027-03-07",
              ceo_to="2027-03-14", final_to="2027-03-21", updated_at=T)
    RD32 = {**RD, "year": 2032}   # 평가 시작일 줄(kpi_years)은 있고 회차는 없는 해 — 거부 시험의 바탕 (26 절이 만든다)
    put("ev_rounds", RD)
    got = conn.execute("SELECT band_a, band_b, band_c, started_at, started_by, att_done_at, released_at, released_by, updated_by"
                       " FROM ev_rounds WHERE year = 2027").fetchone()
    _report(got == (90, 80, 70, None, None, None, None, None, None), "회차 기본값: 등급 기준 90 · 80 · 70 · 시작 · 근태 마침 · 공개 · 고친 사람 비어 있음", f"실제 {got}")
    put("ev_rounds", RD, year=2029, started_at=T, started_by=boss, att_done_at=T, released_at=T, released_by=boss, band_a=100, band_b=2, band_c=1, updated_by=boss)
    _report(True, "시작 · 근태 마침 · 공개까지 한 회차 저장 (등급 기준 경계 100 · 2 · 1)")
    for kw, word, why in ((dict(self_to="2027-03-01"), "self_to <= score_to", "자기평가 마감이 평가 마감보다 뒤"),
                          (dict(score_to="2027-03-08"), "self_to <= score_to", "평가 마감이 등급 확정 마감보다 뒤"),
                          (dict(grade_to="2027-03-15"), "self_to <= score_to", "등급 확정 마감이 대표이사 마감보다 뒤"),
                          (dict(ceo_to="2027-03-22"), "self_to <= score_to", "대표이사 마감이 최종 마감보다 뒤"),
                          (dict(evid_due="2027-03-01"), "evid_due <= score_to", "증빙 기한이 평가 마감보다 뒤"),
                          (dict(band_a=101), "band_a <= 100", "A 기준 101"),
                          (dict(band_b=90), "band_a > band_b", "B 기준이 A 기준과 같음"),
                          (dict(band_b=95), "band_a > band_b", "B 기준이 A 기준보다 큼"),
                          (dict(band_c=80), "band_b > band_c", "C 기준이 B 기준과 같음"),
                          (dict(band_c=0), "band_c >= 1", "C 기준 0"),
                          (dict(band_a=None), "NOT NULL", "A 기준 비어 있음"),
                          (dict(released_at=T), "released_at IS NULL", "공개했는데 시작 안 함"),
                          (dict(year=2099), "FOREIGN KEY", "평가 시작일 줄(kpi_years)이 없는 해"),
                          (dict(year=2025), "year BETWEEN", "2026 이전 해"), (dict(year=2101), "year BETWEEN", "2100 이후 해"),
                          (dict(year=2027), "UNIQUE", "같은 해 두 줄"),
                          (dict(started_by=9999, started_at=T), "FOREIGN KEY", "없는 사람이 시작한 것으로 적기"),
                          (dict(updated_by=9999), "FOREIGN KEY", "없는 사람이 고친 것으로 적기"),
                          (dict(updated_at=None), "NOT NULL", "고친 때 비어 있음")):
        bad("ev_rounds", RD32, word, why, **kw)
    for col in ("self_to", "evid_due", "score_to", "grade_to", "ceo_to", "final_to"):
        bad("ev_rounds", RD32, f"date({col}) IS {col}", f"없는 날짜 2027-02-30 ({col})", **{col: "2027-02-30"})
        bad("ev_rounds", RD32, f"date({col}) IS {col}", f"날짜 모양이 아닌 글 ({col})", **{col: "2027-2-5"})
        bad("ev_rounds", RD32, "NOT NULL", f"비어 있는 마감일 ({col})", **{col: None})
    ok, msg = _rejects(conn, "DELETE FROM kpi_years WHERE year = 2027")
    _report(ok and "FOREIGN KEY" in msg, "회차가 있는 해의 기간 줄(kpi_years)은 지울 수 없음", msg)

    # --- 평가 대상 (한 사람 한 해 한 줄 · 시작 때 떠 둔다) ----------------------------------------------------------------
    TG = dict(year=2027, user_id=up, pool="DIV:1", div_name="가상본부", team_name="본부 직속", is_leader=0, writes_kpi=1, kpi_version=2, stage="SELF")
    put("ev_targets", TG, division_id=1, team_id=None, title="사원", rater_id=boss, assigner_id=boss)
    put("ev_targets", TG, user_id=p2, pool="DIRECT", writes_kpi=0, kpi_version=None, stage="SCORE")
    got = conn.execute("SELECT late, leave_x2, bonus_x2, bonus_reason, comment, head_comment, grade, proposed, sub_score_x10, sub_marks, out_at, rater_id, assigner_id, kpi_version"
                       " FROM ev_targets WHERE user_id = ?", (p2,)).fetchone()
    _report(got == (0, 0, 0, "", "", "", None, None, None, None, None, None, None, None), "대상 기본값: 근태 · 가점 0 · 글 비어 있음 · 등급 · 제안 · 점수 · 제외 · 평가자 · 배정자 · KPI 판 비어 있음", f"실제 {got}")
    TB = {**TG, "user_id": spare, "kpi_version": None, "writes_kpi": 0}   # 거부 시험의 바탕 — spare 는 아직 줄이 없다
    for kw, word, why in ((dict(pool="DIV:0"), "pool IN", "묶음 'DIV:0'"), (dict(pool="TEAM"), "pool IN", "묶음 'TEAM'"),
                          (dict(pool="DIV:"), "pool IN", "묶음 'DIV:'(번호 없음)"), (dict(pool="div:1"), "pool IN", "소문자 묶음 'div:1'"),
                          (dict(pool=None), "NOT NULL", "묶음 비어 있음"),
                          (dict(stage="X"), "stage IN", "단계 'X'"), (dict(stage=None), "NOT NULL", "단계 비어 있음"),
                          (dict(leave_x2=731), "leave_x2 BETWEEN", "연차 초과 731(365.5일을 넘음)"), (dict(leave_x2=-1), "leave_x2 BETWEEN", "연차 초과 -1"),
                          (dict(late=1000), "late BETWEEN", "지각 1000"), (dict(late=-1), "late BETWEEN", "지각 -1"),
                          (dict(bonus_x2=21, bonus_reason="가상"), "bonus_x2 BETWEEN", "가점 21(0.5점 단위, 10점 초과)"),
                          (dict(bonus_x2=-1, bonus_reason="가상"), "bonus_x2 BETWEEN", "가점 -1"),
                          (dict(bonus_x2=2), "bonus_reason", "가점이 있는데 사유가 빈칸"),
                          (dict(bonus_x2=2, bonus_reason="   "), "bonus_reason", "가점이 있는데 사유가 공백뿐"),
                          (dict(bonus_x2=2, bonus_reason=ln(101)), "bonus_reason", "가점 사유 101자"),
                          (dict(stage="HR", proposed="A"), "stage NOT IN", "'HR' 단계인데 등급 없음"),
                          (dict(stage="DONE", proposed="A"), "stage NOT IN", "'DONE' 단계인데 등급 없음"),
                          (dict(stage="GRADE"), "proposed IS NOT NULL", "'GRADE' 단계인데 평가자 제안 없음"),
                          (dict(stage="CEO"), "proposed IS NOT NULL", "'CEO' 단계인데 평가자 제안 없음"),
                          (dict(stage="HR", grade="A"), "proposed IS NOT NULL", "'HR' 단계인데 평가자 제안 없음"),
                          (dict(user_id=up, year=2029, kpi_version=1, writes_kpi=0), "writes_kpi = 1", "KPI 판이 있는데 KPI 작성 대상이 아님"),
                          (dict(user_id=up, year=2029, kpi_version=9, writes_kpi=1), "FOREIGN KEY", "없는 확정본 판 번호(판 9)"),
                          (dict(year=2031), "FOREIGN KEY", "회차가 없는 해"),
                          (dict(user_id=9999), "FOREIGN KEY", "없는 직원"),
                          (dict(rater_id=9999), "FOREIGN KEY", "없는 평가자"), (dict(assigner_id=9999), "FOREIGN KEY", "없는 배정자"),
                          (dict(out_by=9999), "FOREIGN KEY", "없는 사람이 제외한 것으로 적기"), (dict(no_self_by=9999), "FOREIGN KEY", "없는 사람이 자기평가 없이로 적기"),
                          (dict(scored_by=9999), "FOREIGN KEY", "없는 사람이 평가한 것으로 적기"), (dict(proxy_by=9999), "FOREIGN KEY", "없는 사람이 대신 넣은 것으로 적기"),
                          (dict(out_at=T), "out_at IS NULL", "제외 날짜만 있고 이유 없음"), (dict(out_reason="가상"), "out_at IS NULL", "제외 이유만 있고 날짜 없음"),
                          (dict(out_at=T, out_reason=""), "length(out_reason)", "제외 이유 빈칸"),
                          (dict(out_at=T, out_reason=ln(101)), "length(out_reason)", "제외 이유 101자"),
                          (dict(comment=ln(501)), "length(comment)", "평가자 코멘트 501자"),
                          (dict(head_comment=ln(501)), "length(head_comment)", "본부장 코멘트 501자"),
                          (dict(comment=None), "NOT NULL", "코멘트 비어 있음(NULL)"),
                          (dict(grade="E"), "grade IN", "등급 'E'"), (dict(proposed="X"), "proposed IN", "평가자 제안 'X'"),
                          (dict(sub_score_x10=1101), "sub_score_x10 BETWEEN", "KPI 점수 110.1"), (dict(sub_score_x10=-1), "sub_score_x10 BETWEEN", "KPI 점수 -0.1"),
                          (dict(sub_marks="[]"), "json_type(sub_marks)", "객체가 아닌 JSON 항목 등급"), (dict(sub_marks="{"), "json_valid(sub_marks)", "깨진 JSON 항목 등급"),
                          (dict(is_leader=2), "is_leader IN", "팀장 칸 2"), (dict(writes_kpi=2, kpi_version=None), "writes_kpi IN", "KPI 작성 칸 2"),
                          (dict(div_name=None), "NOT NULL", "본부 이름 비어 있음"), (dict(team_name=None), "NOT NULL", "팀 이름 비어 있음"),
                          (dict(user_id=up), "UNIQUE", "같은 해 같은 사람 두 줄")):
        bad("ev_targets", TB, word, why, **kw)
    put("ev_targets", TG, year=2029, kpi_version=None, writes_kpi=0, stage="SCORE")   # 같은 사람 다른 해 — KPI 없이 등급만 (확정본은 2027 만 있다)
    _report(True, "같은 사람의 다른 해 대상 저장 (KPI 없이 등급만)")
    idx = [r[0] for r in conn.execute("SELECT name FROM pragma_index_info('ev_targets_pool') ORDER BY seqno").fetchall()]
    _report(idx == ["year", "pool"], "묶음별 찾기 색인 (ev_targets_pool)", f"실제 {idx}")
    # 경계값 한 줄 — 가장 큰 값들을 한꺼번에 넣어 본다
    put("ev_targets", TG, user_id=spare, pool="DIV:12", kpi_version=None, writes_kpi=0, stage="DONE", grade="S", proposed="A",
        late=999, leave_x2=730, bonus_x2=20, bonus_reason=ln(100), comment=ln(500), head_comment=ln(500), out_at=T, out_reason=ln(100),
        sub_score_x10=1100, sub_marks='{"1": "A"}', edit_seen="가상 고친 내용")
    put("ev_targets", TG, year=2029, user_id=spare, pool="LEADERS", kpi_version=None, writes_kpi=0, stage="CEO", proposed="B", sub_score_x10=0, sub_marks="{}")
    _report(True, "경계값 대상 저장 (근태 999 · 730, 가점 10점, 글 500 · 100자, KPI 점수 110.0 · 0, 묶음 'DIV:12' · 'LEADERS')")
    ok, msg = _rejects(conn, "DELETE FROM ev_targets WHERE user_id = ?", (p2,))
    left = conn.execute("SELECT COUNT(*) FROM ev_targets WHERE user_id = ?", (p2,)).fetchone()[0]
    _report(ok and "삭제할 수 없습니다" in msg and left == 1, "평가 대상 줄 지우기 거부 (평가 기록 보존)", f"{msg} · 남은 줄 {left}")
    ok, msg = _rejects(conn, "DELETE FROM users WHERE id = ?", (p2,))
    _report(ok and "FOREIGN KEY" in msg, "평가 대상인 직원은 지울 수 없음 (퇴직자는 비활성으로)", msg)
    ok, msg = _rejects(conn, "DELETE FROM ev_rounds WHERE year = 2027")
    _report(ok and "FOREIGN KEY" in msg, "대상이 있는 회차는 지울 수 없음", msg)

    # --- 실행목표 (떠 둔 KPI 의 실행목표마다 한 줄) ----------------------------------------------------------------------
    IT = dict(year=2027, user_id=up, rid=1)
    put("ev_items", IT)
    put("ev_items", IT, rid=2, note=ln(500), self_grade="S", grade="D")
    got = conn.execute("SELECT note, self_grade, grade FROM ev_items WHERE rid = 1").fetchone()
    _report(got == ("", None, None), "실행목표 기본값: 실적 글 비어 있음 · 등급 둘 다 비어 있음", f"실제 {got}")
    for kw, word, why in ((dict(rid=1), "UNIQUE", "같은 실행목표 두 줄"), (dict(rid=0), "rid >= 1", "실행목표 번호 0"),
                          (dict(rid=3, note=ln(501)), "length(note)", "실적 501자"), (dict(rid=3, note=None), "NOT NULL", "실적 비어 있음(NULL)"),
                          (dict(rid=3, self_grade="X"), "self_grade IN", "자기평가 등급 'X'"), (dict(rid=3, grade="E"), "grade IN", "평가자 등급 'E'"),
                          (dict(rid=None), "NOT NULL", "번호 비어 있음"),
                          (dict(user_id=9999, rid=3), "FOREIGN KEY", "없는 직원"),
                          (dict(year=2031, rid=3), "FOREIGN KEY", "회차가 없는 해")):
        bad("ev_items", IT, word, why, **kw)
    ok, msg = _rejects(conn, "INSERT INTO ev_items (year, user_id, rid) VALUES (2029, ?, 1)", (p2,))
    _report(ok and "FOREIGN KEY" in msg, "ev_items 같은 사람이어도 대상 줄이 없는 해(2029)는 거부", msg)
    ok, msg = _rejects(conn, "DELETE FROM ev_items WHERE user_id = ? AND rid = 1", (up,))
    left = conn.execute("SELECT COUNT(*) FROM ev_items WHERE user_id = ?", (up,)).fetchone()[0]
    _report(ok and "삭제할 수 없습니다" in msg and left == 2, "실행목표 줄 지우기 거부 (평가 기록 보존)", f"{msg} · 남은 줄 {left}")

    # --- 증빙 (파일 내용은 서버 폴더에 있고 표에는 이름 · 종류 · 크기 · 지문만) ------------------------------------------------
    FL = dict(year=2027, user_id=up, rid=1, name="가상 증빙.pdf", kind="pdf", size=1024, sha256="a" * 64, at=T)
    put("ev_files", FL)
    for k in ("pdf", "png", "jpg", "xlsx", "xls", "docx", "doc", "hwp", "hwpx"):
        put("ev_files", FL, kind=k, name=ln(120), size=10485760)
    put("ev_files", FL, rid=2, size=1)
    _report(True, "증빙 저장 (종류 아홉 가지 · 이름 120자 · 크기 1 · 10485760 바이트 · 같은 파일 두 번)")
    for kw, word, why in ((dict(rid=9), "FOREIGN KEY", "없는 실행목표"), (dict(user_id=p2), "FOREIGN KEY", "실행목표가 없는 사람"),
                          (dict(size=10485761), "size BETWEEN", "크기 10MB + 1"), (dict(size=0), "size BETWEEN", "크기 0"),
                          (dict(kind="exe"), "kind IN", "종류 'exe'"), (dict(kind="jpeg"), "kind IN", "종류 'jpeg'(jpg 로 줄여 적는다)"),
                          (dict(kind="PDF"), "kind IN", "종류 'PDF'(소문자만)"),
                          (dict(name=""), "length(name)", "이름 빈칸"), (dict(name=ln(121)), "length(name)", "이름 121자"),
                          (dict(sha256=None), "NOT NULL", "지문 비어 있음"), (dict(at=None), "NOT NULL", "올린 때 비어 있음")):
        bad("ev_files", FL, word, why, **kw)
    n = conn.execute("SELECT COUNT(*) FROM ev_files WHERE user_id = ? AND rid = 1", (up,)).fetchone()[0]
    conn.execute("DELETE FROM ev_files WHERE id = (SELECT MIN(id) FROM ev_files)")
    gone = conn.execute("SELECT COUNT(*) FROM ev_files WHERE user_id = ? AND rid = 1", (up,)).fetchone()[0]
    _report(n - gone == 1, "증빙 줄은 지울 수 있음 (본인이 마감 전에 지운다)", f"{n} → {gone}")
    top = conn.execute("SELECT MAX(id) FROM ev_files").fetchone()[0]
    conn.execute("DELETE FROM ev_files WHERE id = ?", (top,))
    put("ev_files", FL)
    new = conn.execute("SELECT MAX(id) FROM ev_files").fetchone()[0]
    _report(new == top + 1, "증빙 번호는 다시 쓰지 않음 (AUTOINCREMENT — 맨 끝 번호를 지워도 다음 줄은 그다음 번호, 파일 이름이 번호라서)", f"지운 {top} → 새 {new}")

    # --- 묶음 기록 (상태는 사람들의 단계로 센다 — 여기엔 마감 · 확인 · 돌려보냄 · 되돌림 기록만) -----------------------------------
    PL = dict(year=2027, pool="DIV:1")
    put("ev_pools", PL)
    got = conn.execute("SELECT closed_at, closed_by, hr_seen, ceo_at, back_at, back_reason, undo_at, undo_from FROM ev_pools").fetchone()
    _report(got == (None, None, "[]", None, None, None, None, None), "묶음 기록 기본값: 마감 · 확인 · 돌려보냄 · 되돌림 비어 있음 · HR 확인 목록 []", f"실제 {got}")
    put("ev_pools", PL, pool="LEADERS", closed_at=T, closed_by=boss, hr_seen="[3, 5]", ceo_at=T, ceo_by=up, ceo_behalf=boss,
        back_at=T, back_by=up, back_behalf=boss, back_reason=ln(200), undo_at=T, undo_by=boss, undo_from="CEO")
    put("ev_pools", PL, pool="DIRECT", undo_at=T, undo_by=boss, undo_from="HR")
    _report(True, "모든 칸이 찬 묶음 기록 저장 (돌려보낸 이유 200자 · 되돌린 단계 'CEO' · 'HR')")
    for kw, word, why in ((dict(pool="DIV:1"), "UNIQUE", "같은 해 같은 묶음 두 줄"), (dict(pool="TEAM"), "pool IN", "묶음 'TEAM'"),
                          (dict(pool="DIV:0"), "pool IN", "묶음 'DIV:0'"),
                          (dict(pool="DIV:2", back_at=T), "back_at IS NULL", "돌려보낸 날짜만 있고 이유 없음"),
                          (dict(pool="DIV:2", back_reason="가상"), "back_at IS NULL", "돌려보낸 이유만 있고 날짜 없음"),
                          (dict(pool="DIV:2", back_at=T, back_reason=""), "length(back_reason)", "돌려보낸 이유 빈칸"),
                          (dict(pool="DIV:2", back_at=T, back_reason=ln(201)), "length(back_reason)", "돌려보낸 이유 201자"),
                          (dict(pool="DIV:2", undo_from="X"), "undo_from IN", "되돌린 단계 'X'"),
                          (dict(pool="DIV:2", hr_seen="{}"), "json_type(hr_seen)", "목록이 아닌 JSON HR 확인"),
                          (dict(pool="DIV:2", hr_seen="["), "json_valid(hr_seen)", "깨진 JSON HR 확인"),
                          (dict(pool="DIV:2", hr_seen=None), "NOT NULL", "HR 확인 목록 비어 있음(NULL)"),
                          (dict(pool="DIV:2", year=2031), "FOREIGN KEY", "회차가 없는 해"),
                          (dict(pool="DIV:2", closed_by=9999), "FOREIGN KEY", "없는 사람이 마감한 것으로 적기"),
                          (dict(pool="DIV:2", ceo_by=9999), "FOREIGN KEY", "없는 사람이 확인한 것으로 적기"),
                          (dict(pool="DIV:2", ceo_behalf=9999), "FOREIGN KEY", "없는 사람의 몫을 대신 확인한 것으로 적기"),
                          (dict(pool="DIV:2", back_by=9999), "FOREIGN KEY", "없는 사람이 돌려보낸 것으로 적기"),
                          (dict(pool="DIV:2", back_behalf=9999), "FOREIGN KEY", "없는 사람의 몫을 대신 돌려보낸 것으로 적기"),
                          (dict(pool="DIV:2", undo_by=9999), "FOREIGN KEY", "없는 사람이 되돌린 것으로 적기")):
        bad("ev_pools", PL, word, why, **kw)

    # --- 이의 신청 (한 사람 한 해 한 번 — 쓰는 곳은 평가 3단계 ②) ---------------------------------------------------------
    AP = dict(year=2027, user_id=up, reason="가상 이의 이유", at=T, status="OPEN", before="B")
    put("ev_appeals", AP)
    put("ev_appeals", AP, user_id=p2, reason=ln(500), status="CEO", before="D", after="C", marks='{"1": "B"}', answer=ln(500),
        answered_at=T, answered_by=boss, decided_at=T, decided_by=boss, decided_behalf=up)
    put("ev_appeals", AP, user_id=spare, status="DONE", before="S", after="S", answer="가상 답변")
    got = conn.execute("SELECT after, marks, answer, answered_at, answered_by, decided_at, decided_by, decided_behalf FROM ev_appeals WHERE user_id = ?", (up,)).fetchone()
    _report(got == (None,) * 8, "이의 신청 처음엔 답변 · 바뀐 등급 · 점수 · 결정이 모두 비어 있음", f"실제 {got}")
    for kw, word, why in ((dict(year=2027, user_id=up), "UNIQUE", "같은 사람 같은 해 두 번째 이의"),
                          (dict(year=2029, user_id=p2), "FOREIGN KEY", "대상 줄이 없는 해"),
                          (dict(user_id=9999), "FOREIGN KEY", "없는 직원"),
                          (dict(status="DONE"), "answer IS NOT NULL", "'DONE' 인데 답변 · 바뀐 등급 없음"),
                          (dict(status="DONE", answer="가상 답변"), "answer IS NOT NULL", "'DONE' 인데 바뀐 등급 없음"),
                          (dict(status="DONE", after="B"), "answer IS NOT NULL", "'DONE' 인데 답변 없음"),
                          (dict(status="CEO", after="B"), "answer IS NOT NULL", "'CEO' 인데 답변 없음"),
                          (dict(status="X"), "status IN", "상태 'X'"), (dict(status=None), "NOT NULL", "상태 비어 있음"),
                          (dict(reason=""), "length(reason)", "이유 빈칸"), (dict(reason=ln(501)), "length(reason)", "이유 501자"),
                          (dict(before="X"), "before IN", "이의 전 등급 'X'"), (dict(before=None), "NOT NULL", "이의 전 등급 비어 있음"),
                          (dict(at=None), "NOT NULL", "이의 낸 때 비어 있음"),
                          (dict(after="X", answer="가상", status="DONE"), "after IN", "바뀐 등급 'X'"),
                          (dict(marks="[]"), "json_type(marks)", "객체가 아닌 JSON 점수"), (dict(marks="{"), "json_valid(marks)", "깨진 JSON 점수"),
                          (dict(answer=ln(501), after="B", status="DONE"), "length(answer)", "답변 501자"),
                          (dict(answered_by=9999), "FOREIGN KEY", "없는 사람이 답한 것으로 적기"),
                          (dict(decided_by=9999), "FOREIGN KEY", "없는 사람이 결정한 것으로 적기"),
                          (dict(decided_behalf=9999), "FOREIGN KEY", "없는 사람의 몫을 대신 결정한 것으로 적기")):
        # 바탕은 (2029, spare) — 대상 줄은 있고 이의는 아직 없는 자리라서, 거부는 바꾼 칸의 규칙 때문이다
        bad("ev_appeals", {**AP, "year": 2029, "user_id": spare}, word, why, **kw)


def check_cards_table(conn: sqlite3.Connection) -> None:
    """축하 · 칭찬 카드 표 (021 — 판 21). 한 장 = 한 줄 · 같은 사람에게 하루 한 장 · 본인에게 못 보냄 · 종류와 축하 소식의 짝 · 그림 1~4 · 글은 잠근 BLOB ·
    날짜는 실제 있는 날 · 읽기 전엔 고마워요 없음 · 내용 칸은 보낸 뒤 못 고침(트리거), 읽음 · 고마워요 · 숨김 칸만 고침 · 카드가 있는 직원은 못 지움 · 카드 줄 DELETE 는 막지 않음.
    사람은 전부 가상. 막힌 이유는 메시지의 규칙 토막(CHECK 식 · UNIQUE · FOREIGN KEY · READ_ONLY)까지 본다 — 표가 없어서 막힌 것이 통과로 세어지지 않게.
    쓴 카드 · 직원(T041 · T042 — T040 은 앞 절(32)이 쓰고 남긴다)은 끝에 지운다."""
    print("33. 축하 · 칭찬 카드 (021)")
    ver = conn.execute("PRAGMA user_version").fetchone()[0]
    _report(ver >= 21, f"판 번호가 21 이상 — {ver}", f"실제 {ver}")
    got = [r[1] for r in conn.execute("PRAGMA table_info(cards)")]
    want = ["id", "sender_id", "recipient_id", "kind", "reason", "art", "body", "sent_on", "created_at", "read_at", "thanked_at", "hidden_at"]
    _report(got == want, "cards 표 칸 12개 (이후 작업이 이 이름을 쓴다)", f"실제 {got}")

    def person(no: str, name: str) -> int:
        return conn.execute(
            "INSERT INTO users (employee_no, name, email, department_id)"
            f" VALUES ('{no}', '{name}', '{no.lower()}@example.invalid', (SELECT id FROM departments WHERE code='DIV1')) RETURNING id"
        ).fetchone()[0]

    a, b = person("T041", "가상카드보냄"), person("T042", "가상카드받음")
    T, blob = "2027-03-02T09:00:00Z", (lambda n: b"\x01" * n)   # 잠근 글 = 12바이트 nonce + 내용 + 16바이트 태그 → 29바이트 이상
    CD = dict(sender_id=a, recipient_id=b, kind="praise", reason=None, art=1, body=blob(29), sent_on="2027-03-02", created_at=T)

    def row(base: dict, over: dict) -> tuple[str, tuple]:
        cols = {**base, **over}
        return f"INSERT INTO cards ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", tuple(cols.values())

    def bad(word: str, why: str, **over) -> None:
        """over 로 바꾼 줄이 거부되고, 거부 메시지에 word(CHECK 식의 한 토막 · UNIQUE · FOREIGN KEY …)가 들어 있나."""
        ok, msg = _rejects(conn, *row(CD, over))
        _report(ok and word in msg, f"카드 {why} 거부", msg)

    conn.execute(*row(CD, {}))
    got = conn.execute("SELECT kind, reason, art, typeof(body), length(body), sent_on, read_at, thanked_at, hidden_at FROM cards").fetchone()
    _report(got == ("praise", None, 1, "blob", 29, "2027-03-02", None, None, None), "정상 카드 저장: 칭찬 · 이유 없음 · 그림 1 · 글 29바이트 · 읽음 · 고마워요 · 숨김 비어 있음", f"실제 {got}")
    conn.execute(*row(CD, {"sent_on": "2027-03-03"}))
    conn.execute(*row(CD, {"sender_id": b, "recipient_id": a}))
    _report(True, "같은 사람에게 다른 날 · 거꾸로(받은 사람이 보냄) 같은 날은 저장됨")
    for kw in (dict(kind="cheer", reason="birthday"), dict(kind="thanks"), dict(kind="comfort")):   # 날짜를 안 바꾸면 하루 한 장 규칙이 걸리는 자리
        ok, msg = _rejects(conn, *row(CD, kw))
        _report(ok and "UNIQUE" in msg, f"같은 (보낸 · 받는 · 날짜)에 종류만 바꿔 한 장 더({kw['kind']}) 거부", msg)
    for kind, reason in (("cheer", "birthday"), ("cheer", "welcome"), ("cheer", "anniv"), ("cheer", "joy"), ("thanks", None), ("comfort", None)):
        conn.execute(*row(CD, dict(kind=kind, reason=reason, sent_on="2027-03-04")))
        conn.execute("DELETE FROM cards WHERE sent_on = '2027-03-04'")
    _report(True, "종류 4개 · 축하 소식 4개의 짝 6가지 저장됨")

    bad("sender_id <> recipient_id", "본인에게 보냄", recipient_id=a)
    bad("kind IN", "종류가 없는 값(love)", kind="love")
    bad("NOT NULL", "종류 비어 있음", kind=None)
    bad("(kind = 'cheer') = (reason IS NOT NULL)", "축하인데 소식 이유 없음", kind="cheer", sent_on="2027-03-05")
    bad("(kind = 'cheer') = (reason IS NOT NULL)", "칭찬인데 소식 이유(birthday)", reason="birthday", sent_on="2027-03-05")
    bad("(kind = 'cheer') = (reason IS NOT NULL)", "위로인데 소식 이유(joy)", kind="comfort", reason="joy", sent_on="2027-03-05")
    bad("reason IN", "없는 소식 이유(xmas)", kind="cheer", reason="xmas", sent_on="2027-03-05")
    for art, why in ((0, "0"), (5, "5"), ("1", "글자 '1'"), (1.5, "1.5"), (None, "비어 있음")):
        bad("BETWEEN 1 AND 4" if art is not None else "NOT NULL", f"그림 번호 {why}", art=art, sent_on="2027-03-05")
    for body, why in (("가" * 40, "글자(BLOB 아님)"), (blob(28), "28바이트"), (b"", "빈 BLOB"), (None, "비어 있음")):
        bad("length(body)" if body is not None else "NOT NULL", f"글 {why}", body=body, sent_on="2027-03-05")
    for day, why in (("2027-02-30", "2027-02-30"), ("2027-13-01", "13월"), ("2027-3-5", "0 없는 날짜"), ("20270305", "하이픈 없는 날짜"), (None, "비어 있음")):
        bad("date(sent_on)" if day is not None else "NOT NULL", f"날짜 {why}", sent_on=day)
    bad("FOREIGN KEY", "없는 직원이 보냄", sender_id=9999, sent_on="2027-03-05")
    bad("FOREIGN KEY", "없는 직원에게 보냄", recipient_id=9999, sent_on="2027-03-05")
    bad("NOT NULL", "만든 때 비어 있음", created_at=None, sent_on="2027-03-05")
    bad("thanked_at IS NULL OR read_at IS NOT NULL", "읽지 않았는데 고마워요", thanked_at=T, sent_on="2027-03-05")

    # --- 내용 칸은 못 고침 · 읽음 · 고마워요 · 숨김 칸은 고침 ----------------------------------------------------------
    cid = conn.execute("SELECT id FROM cards WHERE sender_id = ? AND recipient_id = ? AND sent_on = '2027-03-02'", (a, b)).fetchone()[0]
    before = conn.execute("SELECT sender_id, recipient_id, kind, reason, art, body, sent_on, created_at FROM cards WHERE id = ?", (cid,)).fetchone()
    for col, val in (("sender_id", b), ("recipient_id", a), ("kind", "comfort"), ("reason", "joy"), ("art", 2), ("body", blob(30)),
                     ("sent_on", "2027-03-09"), ("created_at", "2027-03-02T10:00:00Z")):
        ok, msg = _rejects(conn, f"UPDATE cards SET {col} = ? WHERE id = ?", (val, cid))
        _report(ok and "READ_ONLY" in msg, f"보낸 카드의 {col} 칸 고치기 거부", msg)
    ok, msg = _rejects(conn, "UPDATE cards SET art = art WHERE id = ?", (cid,))
    _report(ok and "READ_ONLY" in msg, "같은 값으로 다시 쓰는 UPDATE 도 거부 (내용 칸에는 손대지 않는 것으로 정함)", msg)
    after = conn.execute("SELECT sender_id, recipient_id, kind, reason, art, body, sent_on, created_at FROM cards WHERE id = ?", (cid,)).fetchone()
    _report(after == before, "거부된 고치기는 내용을 하나도 바꾸지 않음")
    ok, msg = _rejects(conn, "UPDATE cards SET read_at = NULL, thanked_at = ? WHERE id = ?", (T, cid))
    _report(ok and "thanked_at IS NULL OR read_at IS NOT NULL" in msg, "읽음 없이 고마워요만 넣기 거부", msg)
    conn.execute("UPDATE cards SET read_at = ? WHERE id = ?", (T, cid))
    conn.execute("UPDATE cards SET thanked_at = ? WHERE id = ?", (T, cid))
    conn.execute("UPDATE cards SET hidden_at = ? WHERE id = ?", (T, cid))
    got = conn.execute("SELECT read_at, thanked_at, hidden_at FROM cards WHERE id = ?", (cid,)).fetchone()
    _report(got == (T, T, T), "읽음 · 고마워요 · 숨김 칸 고치기는 됨", f"실제 {got}")
    conn.execute("UPDATE cards SET hidden_at = NULL WHERE id = ?", (cid,))
    got = conn.execute("SELECT hidden_at FROM cards WHERE id = ?", (cid,)).fetchone()[0]
    _report(got is None, "숨김을 풀어 다시 보이게 하기는 됨 (hidden_at 을 비움)", f"실제 {got}")
    ok, msg = _rejects(conn, "UPDATE cards SET read_at = NULL WHERE id = ?", (cid,))
    _report(ok and "thanked_at IS NULL OR read_at IS NOT NULL" in msg, "고마워요 한 카드의 읽음 지우기 거부", msg)

    # --- 지우기: 카드가 있는 직원은 못 지움 · 카드 줄 자체는 지워짐 ---------------------------------------------------------
    conn.execute("DELETE FROM cards WHERE sender_id = ?", (b,))   # 거꾸로 보낸 카드를 치워서, a 는 보내기만 · b 는 받기만 한 사람으로 — 두 칸의 외래키를 따로 본다
    for who, why in ((a, "보낸"), (b, "받은")):
        ok, msg = _rejects(conn, "DELETE FROM users WHERE id = ?", (who,))
        _report(ok and "FOREIGN KEY" in msg, f"카드를 {why} 직원은 지울 수 없음 (퇴직자는 비활성으로)", msg)
    conn.execute("DELETE FROM cards WHERE id = ?", (cid,))
    left = conn.execute("SELECT COUNT(*) FROM cards WHERE id = ?", (cid,)).fetchone()[0]
    _report(left == 0, "카드 줄 DELETE 는 됨 (지우는 길이 서버에 없을 뿐 — 검사 정리용으로 막지 않음)", f"남은 줄 {left}")
    conn.execute("DELETE FROM cards")
    conn.execute("DELETE FROM users WHERE id IN (?, ?)", (a, b))


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
            check_kpi_review_tables(conn)
            check_game_hours_table(conn)
            check_contract_live_tables(conn)
            check_push_tables(conn)
            check_pay_pin_tables(conn)
            check_pay_ask_table(conn)
            check_eval_tables(conn)
            check_cards_table(conn)
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
