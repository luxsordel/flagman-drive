from datetime import date

import pytest

from flagman.kyc.domain import (
    REJECTION_REASON_MAX_LENGTH,
    REQUIRED_DOCUMENTS,
    AdmissionRules,
    Applicant,
    DocumentKind,
    KycApplication,
    KycIncompleteError,
    KycLockedError,
    KycNotPendingError,
    KycProfileIncompleteError,
    KycRequirementsNotMetError,
    KycStatus,
    LicenseIssueDateInvalidError,
    full_years,
)

RULES = AdmissionRules(min_age_years=21, min_driving_years=2)
TODAY = date(2026, 10, 23)
ADULT = Applicant(first_name="Анна", last_name="Ковалёва", birth_date=date(1990, 5, 17))
LICENSED = date(2015, 6, 1)


def _complete(status: KycStatus = KycStatus.NOT_SUBMITTED) -> KycApplication:
    return KycApplication(status=status, uploaded=REQUIRED_DOCUMENTS)


def test_a_new_client_has_nothing_uploaded_and_everything_missing() -> None:
    application = KycApplication()

    assert application.status is KycStatus.NOT_SUBMITTED
    assert application.missing == REQUIRED_DOCUMENTS


@pytest.mark.parametrize("status", [KycStatus.NOT_SUBMITTED, KycStatus.REJECTED])
def test_documents_can_change_before_submission_and_after_rejection(status: KycStatus) -> None:
    KycApplication(status=status).ensure_editable()


@pytest.mark.parametrize("status", [KycStatus.PENDING, KycStatus.APPROVED])
def test_documents_are_locked_under_review_and_after_approval(status: KycStatus) -> None:
    with pytest.raises(KycLockedError):
        KycApplication(status=status).ensure_editable()


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

    approved = pending.approve(ADULT, LICENSED, RULES, TODAY)
    assert approved.status is KycStatus.APPROVED
    assert approved.license_issued_on == LICENSED
    rejected = pending.reject("  Passport photo is cut off  ")
    assert rejected.status is KycStatus.REJECTED
    assert rejected.rejection_reason == "Passport photo is cut off"


@pytest.mark.parametrize(
    "status", [KycStatus.NOT_SUBMITTED, KycStatus.APPROVED, KycStatus.REJECTED]
)
def test_decisions_are_refused_unless_pending(status: KycStatus) -> None:
    application = _complete(status)

    with pytest.raises(KycNotPendingError):
        application.approve(ADULT, LICENSED, RULES, TODAY)
    with pytest.raises(KycNotPendingError):
        application.reject("Blurry selfie")


@pytest.mark.parametrize("reason", ["", "   ", "", "x" * (REJECTION_REASON_MAX_LENGTH + 1)])
def test_reject_needs_a_readable_reason(reason: str) -> None:
    with pytest.raises(ValueError, match="rejection reason"):
        _complete(KycStatus.PENDING).reject(reason)


@pytest.mark.parametrize(
    ("since", "today", "years"),
    [
        (date(2005, 10, 23), date(2026, 10, 23), 21),
        (date(2005, 10, 24), date(2026, 10, 23), 20),
        (date(2005, 11, 1), date(2026, 10, 23), 20),
        (date(2004, 2, 29), date(2025, 2, 28), 20),
        (date(2004, 2, 29), date(2025, 3, 1), 21),
        (date(2004, 2, 29), date(2028, 2, 29), 24),
    ],
)
def test_full_years_counts_only_completed_years(since: date, today: date, years: int) -> None:
    assert full_years(since, today) == years


@pytest.mark.parametrize(
    "applicant",
    [
        Applicant(first_name=None, last_name="Ковалёва", birth_date=date(1990, 5, 17)),
        Applicant(first_name="Анна", last_name=None, birth_date=date(1990, 5, 17)),
        Applicant(first_name="Анна", last_name="Ковалёва", birth_date=None),
    ],
)
def test_approval_needs_the_profile_the_documents_are_checked_against(
    applicant: Applicant,
) -> None:
    with pytest.raises(KycProfileIncompleteError):
        _complete(KycStatus.PENDING).approve(applicant, LICENSED, RULES, TODAY)


def test_the_client_turns_21_on_the_birthday() -> None:
    pending = _complete(KycStatus.PENDING)
    birthday = Applicant(first_name="Анна", last_name="Ковалёва", birth_date=date(2005, 10, 23))
    day_before = Applicant(first_name="Анна", last_name="Ковалёва", birth_date=date(2005, 10, 24))

    assert pending.approve(birthday, date(2023, 1, 1), RULES, TODAY).status is KycStatus.APPROVED
    with pytest.raises(KycRequirementsNotMetError) as error:
        pending.approve(day_before, date(2023, 1, 1), RULES, TODAY)

    # The detail names the rule, not the client's birth date or age.
    assert error.value.detail == "The client must be at least 21 years old."


def test_driving_experience_is_counted_from_the_license_issue_date() -> None:
    pending = _complete(KycStatus.PENDING)

    assert pending.approve(ADULT, date(2024, 10, 23), RULES, TODAY).status is KycStatus.APPROVED
    with pytest.raises(KycRequirementsNotMetError) as error:
        pending.approve(ADULT, date(2024, 10, 24), RULES, TODAY)

    assert error.value.detail == "The client must have held a license for at least 2 years."


def test_thresholds_come_from_the_rules() -> None:
    lenient = AdmissionRules(min_age_years=18, min_driving_years=0)
    young = Applicant(first_name="Анна", last_name="Ковалёва", birth_date=date(2007, 1, 1))

    approved = _complete(KycStatus.PENDING).approve(young, TODAY, lenient, TODAY)

    assert approved.status is KycStatus.APPROVED


@pytest.mark.parametrize(
    "issued_on",
    [
        date(2026, 10, 24),  # tomorrow
        date(1990, 5, 16),  # the day before the birth date
    ],
)
def test_the_license_issue_date_must_be_plausible(issued_on: date) -> None:
    with pytest.raises(LicenseIssueDateInvalidError):
        _complete(KycStatus.PENDING).approve(ADULT, issued_on, RULES, TODAY)


def test_a_decided_application_is_not_checked_against_the_rules_again() -> None:
    # The status comes first: a second click on "approve" is a conflict, not a profile error.
    nobody = Applicant(first_name=None, last_name=None, birth_date=None)

    with pytest.raises(KycNotPendingError):
        _complete(KycStatus.APPROVED).approve(nobody, LICENSED, RULES, TODAY)
