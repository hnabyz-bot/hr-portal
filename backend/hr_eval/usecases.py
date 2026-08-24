"""부수효과가 있는 유스케이스.

각 함수가 트랜잭션 하나다. 검증에 실패하면 commit()에 도달하지 않으므로
부분 저장이 남지 않는다.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import Sequence

from hr_eval.domain.contract_rules import build_document_hash
from hr_eval.domain.errors import (
    Issue,
    PermissionDeniedError,
    StateConflictError,
    ValidationError,
)
from hr_eval.domain.kpi import KpiInput, validate_kpi_set
from hr_eval.domain.models import (
    Actor,
    AuditEntry,
    ContractStatus,
    KpiSheet,
    KpiSheetStatus,
    PdfStatus,
    Role,
    SalaryContract,
    SignatureInput,
)
from hr_eval.ports import UnitOfWork


def _now() -> datetime:
    return datetime.now(timezone.utc)


def submit_kpi_goal(
    *,
    actor: Actor,
    period_id: int,
    user_id: int,
    kpis: Sequence[KpiInput],
    uow: UnitOfWork,
) -> KpiSheet:
    if actor.user_id != user_id and actor.role is not Role.HR_ADMIN:
        raise PermissionDeniedError("본인 또는 인사담당자만 KPI를 제출할 수 있습니다")

    with uow:
        period = uow.periods.get(period_id)
        if not period.is_kpi_window_open:
            raise StateConflictError(
                "KPI 수정 기간이 열려 있지 않습니다. 인사담당자에게 문의하십시오."
            )

        sheet = uow.kpis.get_sheet(period_id, user_id)
        if sheet is not None and sheet.status is KpiSheetStatus.DIVISION_HEAD_APPROVED:
            raise StateConflictError(
                "확정된 KPI는 직접 수정할 수 없습니다. 수정 요청을 등록하십시오."
            )

        validate_kpi_set(kpis)

        if sheet is None:
            sheet = uow.kpis.create_sheet(period_id, user_id, actor.user_id)

        uow.kpis.replace_kpis(sheet.id, kpis)
        sheet = replace(sheet, status=KpiSheetStatus.DRAFT, submitted_at=_now())
        uow.kpis.save_sheet(sheet)

        uow.audit.append(
            AuditEntry(
                action="KPI_SUBMITTED",
                entity_type="kpi_sheet",
                entity_id=sheet.id,
                actor_user_id=actor.user_id,
                actor_role=actor.role,
                after_data={"kpi_count": len(kpis)},
            )
        )
        uow.commit()

    return sheet


def finalize_salary_contract(
    *,
    actor: Actor,
    contract_id: int,
    signature: SignatureInput,
    uow: UnitOfWork,
) -> SalaryContract:
    """전자서명 완료 처리. 성공하면 계약서가 READ_ONLY로 잠긴다."""
    with uow:
        contract = uow.contracts.get_for_update(contract_id)

        if actor.user_id != contract.user_id:
            raise PermissionDeniedError(
                "연봉계약서는 본인만 서명할 수 있습니다 (대리 서명 불가)"
            )
        if contract.status is not ContractStatus.SENT:
            raise StateConflictError(
                f"서명할 수 없는 상태입니다 (현재 {contract.status.value})"
            )

        issues: list[Issue] = []
        if not signature.consent_checked:
            issues.append(Issue("CONSENT_REQUIRED", "계약 내용 동의에 체크해야 합니다"))
        if not signature.signer_name.strip():
            issues.append(Issue("SIGNER_NAME_REQUIRED", "서명자 성명을 입력해야 합니다"))
        if not signature.signature_image:
            issues.append(Issue("SIGNATURE_IMAGE_REQUIRED", "손글씨 서명이 필요합니다"))
        if not signature.ip:
            issues.append(Issue("SIGNER_IP_REQUIRED", "서명자 IP를 확인할 수 없습니다"))
        if issues:
            raise ValidationError(issues)

        signed = replace(
            contract,
            status=ContractStatus.SIGNED,
            is_locked=True,
            consent_checked=True,
            signer_user_id=actor.user_id,
            signer_name=signature.signer_name.strip(),
            signer_ip=signature.ip,
            signer_user_agent=signature.user_agent,
            signature_image=signature.signature_image,
            document_hash=build_document_hash(contract.as_document()),
            signed_at=_now(),
            pdf_status=PdfStatus.PENDING,
        )
        uow.contracts.save(signed)

        uow.audit.append(
            AuditEntry(
                action="CONTRACT_SIGNED",
                entity_type="salary_contract",
                entity_id=signed.id,
                actor_user_id=actor.user_id,
                actor_role=actor.role,
                before_data={"status": contract.status.value},
                after_data={
                    "status": signed.status.value,
                    "document_hash": signed.document_hash,
                },
                ip=signature.ip,
                user_agent=signature.user_agent,
            )
        )
        uow.commit()

    return signed


def _apply_cancel(
    contract: SalaryContract, actor: Actor, reason: str
) -> SalaryContract:
    return replace(
        contract,
        status=ContractStatus.CANCELLED,
        cancelled_at=_now(),
        cancelled_by=actor.user_id,
        cancel_reason=reason.strip(),
    )


def _guard_cancel(actor: Actor, reason: str) -> None:
    if actor.role is not Role.HR_ADMIN:
        raise PermissionDeniedError("계약 파기·재발송은 인사담당자만 할 수 있습니다")
    if not reason or not reason.strip():
        raise ValidationError([Issue("CANCEL_REASON_REQUIRED", "파기 사유를 입력해야 합니다")])


def cancel_salary_contract(
    *, actor: Actor, contract_id: int, reason: str, uow: UnitOfWork
) -> SalaryContract:
    _guard_cancel(actor, reason)

    with uow:
        contract = uow.contracts.get_for_update(contract_id)
        if contract.status is ContractStatus.CANCELLED:
            raise StateConflictError("이미 파기된 계약서입니다")

        cancelled = _apply_cancel(contract, actor, reason)
        uow.contracts.save(cancelled)
        uow.audit.append(
            AuditEntry(
                action="CONTRACT_CANCELLED",
                entity_type="salary_contract",
                entity_id=cancelled.id,
                actor_user_id=actor.user_id,
                actor_role=actor.role,
                before_data={"status": contract.status.value},
                after_data={"status": cancelled.status.value},
                reason=cancelled.cancel_reason,
            )
        )
        uow.commit()

    return cancelled


def resend_salary_contract(
    *, actor: Actor, contract_id: int, reason: str, uow: UnitOfWork
) -> SalaryContract:
    """파기와 새 계약서 발행을 한 트랜잭션으로 처리한다.

    원본은 서명값을 그대로 안은 채 CANCELLED로 남는다. 감사 추적이
    끊기지 않도록 덮어쓰지 않고 새 행을 만든다.
    """
    _guard_cancel(actor, reason)

    with uow:
        original = uow.contracts.get_for_update(contract_id)
        if original.status is ContractStatus.CANCELLED:
            raise StateConflictError("이미 파기된 계약서입니다")

        cancelled = _apply_cancel(original, actor, reason)
        uow.contracts.save(cancelled)
        uow.audit.append(
            AuditEntry(
                action="CONTRACT_CANCELLED",
                entity_type="salary_contract",
                entity_id=cancelled.id,
                actor_user_id=actor.user_id,
                actor_role=actor.role,
                before_data={"status": original.status.value},
                after_data={"status": cancelled.status.value},
                reason=cancelled.cancel_reason,
            )
        )

        fresh = replace(
            original,
            id=None,
            version=original.version + 1,
            resent_from_id=original.id,
            status=ContractStatus.SENT,
            is_locked=False,
            sent_at=_now(),
            consent_checked=False,
            signer_user_id=None,
            signer_name=None,
            signer_ip=None,
            signer_user_agent=None,
            signature_image=None,
            document_hash=None,
            signed_at=None,
            pdf_status=PdfStatus.NONE,
            pdf_path=None,
            pdf_generated_at=None,
            cancelled_at=None,
            cancelled_by=None,
            cancel_reason=None,
        )
        fresh = uow.contracts.insert(fresh)

        uow.audit.append(
            AuditEntry(
                action="CONTRACT_RESENT",
                entity_type="salary_contract",
                entity_id=fresh.id,
                actor_user_id=actor.user_id,
                actor_role=actor.role,
                after_data={"version": fresh.version, "resent_from_id": fresh.resent_from_id},
                reason=reason.strip(),
            )
        )
        uow.commit()

    return fresh
