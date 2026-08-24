-- 001_init.sql 이 적용된 DB에서 제약조건이 실제로 동작하는지 확인한다.
-- 막혀야 할 것이 통과하면 RAISE EXCEPTION 으로 CI를 깨뜨린다.
-- 마지막에 ROLLBACK 하므로 데이터가 남지 않는다.

BEGIN;

INSERT INTO departments (code, name, level) VALUES ('DIV1', '가상본부', 'DIVISION');
INSERT INTO users (employee_no, name, email, role, department_id, hire_date)
VALUES ('T001', '가상직원', 't001@example.invalid', 'EMPLOYEE',
        (SELECT id FROM departments WHERE code = 'DIV1'), DATE '2020-01-01');
INSERT INTO evaluation_periods (year, type, name, starts_on, ends_on)
VALUES (2027, 'ANNUAL', '가상 정기평가', DATE '2027-01-01', DATE '2027-12-31');

-- (1) 100점 이하 S등급은 거부돼야 한다
DO $chk$
BEGIN
    INSERT INTO evaluations (period_id, user_id, division_id, kpi_score, grade)
    VALUES ((SELECT id FROM evaluation_periods LIMIT 1),
            (SELECT id FROM users LIMIT 1),
            (SELECT id FROM departments LIMIT 1),
            100, 'S');
    RAISE EXCEPTION '[검사 실패] 100점짜리 S등급이 저장됐습니다';
EXCEPTION WHEN check_violation THEN
    RAISE NOTICE '[통과] 100점 이하 S등급 거부';
END
$chk$;

-- (1-2) 총점이 110점을 넘으면 거부돼야 한다
DO $chk$
BEGIN
    INSERT INTO evaluations (period_id, user_id, division_id, kpi_score, bonus_score, bonus_reason)
    VALUES ((SELECT id FROM evaluation_periods LIMIT 1),
            (SELECT id FROM users LIMIT 1),
            (SELECT id FROM departments LIMIT 1),
            100, 11, '가상 가점 사유');
    RAISE EXCEPTION '[검사 실패] 총점 111점이 저장됐습니다';
EXCEPTION WHEN check_violation THEN
    RAISE NOTICE '[통과] 총점 110점 초과 거부';
END
$chk$;

-- (2) 그룹 정원 합계가 인원수와 다르면 거부돼야 한다
DO $chk$
BEGIN
    INSERT INTO department_quotas (period_id, department_id, headcount, quota_mode,
                                   quota_upper, quota_lower, cap_s)
    VALUES ((SELECT id FROM evaluation_periods LIMIT 1),
            (SELECT id FROM departments LIMIT 1),
            4, 'GROUPED', 1, 99, 1);
    RAISE EXCEPTION '[검사 실패] 정원 합계가 인원수와 다른데 저장됐습니다';
EXCEPTION WHEN check_violation THEN
    RAISE NOTICE '[통과] 그룹 정원 합계 불일치 거부';
END
$chk$;

-- (3) GROUPED 모드에 개별 정원 칼럼을 채우면 거부돼야 한다
DO $chk$
BEGIN
    INSERT INTO department_quotas (period_id, department_id, headcount, quota_mode,
                                   quota_upper, quota_lower, quota_a, cap_s)
    VALUES ((SELECT id FROM evaluation_periods LIMIT 1),
            (SELECT id FROM departments LIMIT 1),
            4, 'GROUPED', 1, 3, 1, 1);
    RAISE EXCEPTION '[검사 실패] 모드에 맞지 않는 칼럼이 저장됐습니다';
EXCEPTION WHEN check_violation THEN
    RAISE NOTICE '[통과] 모드 불일치 칼럼 거부';
END
$chk$;

-- 서명 완료 계약서 하나를 만든다 (이후 검사의 대상)
-- raise_pct/base_salary_after는 전부 가상 값이다 (실제 인상률은 salary_raise_rates
-- 테이블에만 있다. hr-portal은 Public 저장소다).
INSERT INTO evaluations (period_id, user_id, division_id, kpi_score, bonus_score,
                         bonus_reason, grade, status)
VALUES ((SELECT id FROM evaluation_periods LIMIT 1),
        (SELECT id FROM users LIMIT 1),
        (SELECT id FROM departments LIMIT 1),
        95, 10, '가상 가점 사유', 'S', 'CONFIRMED');

INSERT INTO salary_contracts (
    period_id, user_id, evaluation_id, grade,
    base_salary_before, raise_pct, base_salary_after,
    contract_starts_on, contract_ends_on,
    status, is_locked, consent_checked,
    signer_user_id, signer_name, signer_ip, signature_image, document_hash, signed_at
) VALUES (
    (SELECT id FROM evaluation_periods LIMIT 1),
    (SELECT id FROM users LIMIT 1),
    (SELECT id FROM evaluations LIMIT 1),
    'S', 50000000, 11.11, 55555000,
    DATE '2027-01-01', DATE '2027-12-31',
    'SIGNED', TRUE, TRUE,
    (SELECT id FROM users LIMIT 1), '가상직원', '192.0.2.1'::inet,
    '\x00'::bytea, repeat('a', 64), now()
);

-- (4) 잠긴 계약서의 금액 수정은 거부돼야 한다
DO $chk$
BEGIN
    UPDATE salary_contracts SET base_salary_after = 99000000
    WHERE id = (SELECT id FROM salary_contracts LIMIT 1);
    RAISE EXCEPTION '[검사 실패] 잠긴 계약서의 금액이 수정됐습니다';
EXCEPTION WHEN restrict_violation THEN
    RAISE NOTICE '[통과] 잠긴 계약서 수정 거부';
END
$chk$;

-- (5) 잠긴 계약서의 파기는 허용돼야 한다
UPDATE salary_contracts
SET status = 'CANCELLED', cancelled_at = now(),
    cancelled_by = (SELECT id FROM users LIMIT 1),
    cancel_reason = '가상 파기 사유'
WHERE id = (SELECT id FROM salary_contracts LIMIT 1);

DO $chk$
BEGIN
    IF (SELECT status FROM salary_contracts LIMIT 1) <> 'CANCELLED' THEN
        RAISE EXCEPTION '[검사 실패] 잠긴 계약서를 파기하지 못했습니다';
    END IF;
    RAISE NOTICE '[통과] 잠긴 계약서 파기 허용';
END
$chk$;

-- (6) 계약서 삭제는 거부돼야 한다
DO $chk$
BEGIN
    DELETE FROM salary_contracts;
    RAISE EXCEPTION '[검사 실패] 계약서가 삭제됐습니다';
EXCEPTION WHEN restrict_violation THEN
    RAISE NOTICE '[통과] 계약서 삭제 거부';
END
$chk$;

-- (7) 감사로그 수정·삭제는 거부돼야 한다
INSERT INTO audit_logs (action, entity_type, entity_id)
VALUES ('TEST', 'salary_contract', 1);

DO $chk$
BEGIN
    UPDATE audit_logs SET action = 'TAMPERED';
    RAISE EXCEPTION '[검사 실패] 감사로그가 수정됐습니다';
EXCEPTION WHEN restrict_violation THEN
    RAISE NOTICE '[통과] 감사로그 수정 거부';
END
$chk$;

ROLLBACK;
