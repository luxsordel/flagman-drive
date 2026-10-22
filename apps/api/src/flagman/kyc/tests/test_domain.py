import pytest

from flagman.kyc.domain import (
    REJECTION_REASON_MAX_LENGTH,
    REQUIRED_DOCUMENTS,
    DocumentKind,
    KycApplication,
    KycIncompleteError,
    KycLockedError,
    KycNotPendingError,
    KycStatus,
)


def _complete(status: KycStatus = KycStatus.NOT_SUBMITTED) -> KycApplication:
    return KycApplication(status=status, uploaded=REQUIRED_DOCUMENTS)


def test_a_new_client_has_nothing_uploaded_and_everything_missing() -> None:
    application = KycApplication()

    assert application.status is KycStatus.NOT_SUBMITTED
    assert application.missing == REQUIRED_DOCUMENTS


@pytest.mark.parametrize("status", [KycStatus.NOT_SUBMITTED, KycStatus.REJECTED])
def test_documents_can_change_before_submission_and_after_rejection(status: KycStatus) -> None:
    KycApplication(status=status).ensure_documents_editable()


@pytest.mark.parametrize("status", [KycStatus.PENDING, KycStatus.APPROVED])
def test_documents_are_locked_under_review_and_after_approval(status: KycStatus) -> None:
    with pytest.raises(KycLockedError):
        KycApplication(status=status).ensure_documents_editable()


def test_submit_moves_a_complete_application_to_pending() -> None:
    assert _complete().submit().status is KycStatus.PENDING


def test_submit_names_the_missing_documents() -> None:
    application = KycApplication(uploaded=REQUIRED_DOCUMENTS - {DocumentKind.PASSPORT})

    with pytest.raises(KycIncompleteError) as error:
        application.submit()

    assert error.value.detail == "Missing documents: passport."


def test_resubmitting_after_rejection_keeps_the_documents_and_clears_the_reason() -> None:
    rejected = _complete(KycStatus.PENDING).reject("Blurry selfie")

    resubmitted = rejected.submit()

    assert resubmitted.status is KycStatus.PENDING
    assert resubmitted.uploaded == REQUIRED_DOCUMENTS
    assert resubmitted.rejection_reason is None


@pytest.mark.parametrize("status", [KycStatus.PENDING, KycStatus.APPROVED])
def test_submit_is_refused_while_under_review_or_approved(status: KycStatus) -> None:
    with pytest.raises(KycLockedError):
        _complete(status).submit()


def test_approve_and_reject_need_a_pending_application() -> None:
    pending = _complete(KycStatus.PENDING)

    assert pending.approve().status is KycStatus.APPROVED
    rejected = pending.reject("  Passport photo is cut off  ")
    assert rejected.status is KycStatus.REJECTED
    assert rejected.rejection_reason == "Passport photo is cut off"


@pytest.mark.parametrize(
    "status", [KycStatus.NOT_SUBMITTED, KycStatus.APPROVED, KycStatus.REJECTED]
)
def test_decisions_are_refused_unless_pending(status: KycStatus) -> None:
    application = _complete(status)

    with pytest.raises(KycNotPendingError):
        application.approve()
    with pytest.raises(KycNotPendingError):
        application.reject("Blurry selfie")


@pytest.mark.parametrize("reason", ["", "   ", "x" * (REJECTION_REASON_MAX_LENGTH + 1)])
def test_reject_needs_a_readable_reason(reason: str) -> None:
    with pytest.raises(ValueError, match="rejection reason"):
        _complete(KycStatus.PENDING).reject(reason)
