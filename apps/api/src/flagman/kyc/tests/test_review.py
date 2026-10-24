import asyncio
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from http import HTTPStatus
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from flagman.core.config import Settings
from flagman.core.db import create_engine, create_session_factory
from flagman.core.ratelimit import Limit
from flagman.identity.domain import StaffAccessClaims, StaffRole, issue_staff_access_token
from flagman.kyc import service
from flagman.kyc.domain import REQUIRED_DOCUMENTS, DocumentKind
from flagman.kyc.tests.conftest import (
    Person,
    client_for,
    open_url,
    problem_type,
    query,
)

pytestmark = pytest.mark.integration

URL = "/admin/kyc/applications"
ADULT_BIRTH_DATE = "1990-05-17"
LICENSED = "2015-06-01"


class Staff:
    """A signed-in staff member; only the token matters to these endpoints."""

    def __init__(self, client: TestClient, settings: Settings, role: StaffRole) -> None:
        self.client = client
        self.id = uuid.uuid4()
        token = issue_staff_access_token(
            StaffAccessClaims(staff_id=self.id, session_id=uuid.uuid4(), role=role),
            settings.api_jwt_secret.get_secret_value(),
            datetime.now(UTC),
        )
        self.headers = {"Authorization": f"Bearer {token}"}

    def queue(self, limit: int = 100) -> Any:
        return self.client.get(URL, params={"limit": limit}, headers=self.headers)

    def card(self, application_id: uuid.UUID) -> Any:
        return self.client.get(f"{URL}/{application_id}", headers=self.headers)

    def url(self, application_id: uuid.UUID, kind: DocumentKind) -> Any:
        return self.client.get(f"{URL}/{application_id}/documents/{kind}/url", headers=self.headers)

    def approve(self, application_id: uuid.UUID, license_issued_on: object = LICENSED) -> Any:
        return self.client.post(
            f"{URL}/{application_id}/approve",
            json={"license_issued_on": license_issued_on},
            headers=self.headers,
        )

    def reject(self, application_id: uuid.UUID, reason: str = "The selfie is blurry") -> Any:
        return self.client.post(
            f"{URL}/{application_id}/reject", json={"reason": reason}, headers=self.headers
        )


@pytest.fixture
def operator(client: TestClient, kyc_settings: Settings) -> Staff:
    return Staff(client, kyc_settings, StaffRole.OPERATOR)


@pytest.fixture
def applicant(client: TestClient) -> Callable[..., Person]:
    """A client with a filled profile, every document uploaded; submitted unless told otherwise."""

    def make(*, birth_date: str = ADULT_BIRTH_DATE, submit: bool = True) -> Person:
        person = Person(client)
        response = person.update_profile(
            first_name="Анна", last_name="Ковалёва", birth_date=birth_date
        )
        assert response.status_code == HTTPStatus.OK, response.text
        person.upload_all()
        if submit:
            response = person.submit()
            assert response.status_code == HTTPStatus.OK, response.text
        return person

    return make


def _application_id(settings: Settings, person: Person) -> uuid.UUID:
    rows = query(
        settings,
        "SELECT a.id FROM kyc_applications a JOIN users u ON u.id = a.user_id"
        " WHERE u.phone = :phone",
        phone=person.phone,
    )
    application_id: uuid.UUID = rows[0].id
    return application_id


def _events(settings: Settings, application_id: uuid.UUID) -> list[Any]:
    return query(
        settings,
        "SELECT actor_type, actor_id, action, target_type, details FROM audit_events"
        " WHERE target_id = :id ORDER BY occurred_at",
        id=application_id,
    )


def _years_ago(years: int, days: int = 0) -> str:
    today = datetime.now(UTC).date()
    # 1 March for a 29 February today: never earlier than the exact date.
    try:
        then = today.replace(year=today.year - years)
    except ValueError:
        then = date(today.year - years, 3, 1)
    return (then + timedelta(days=days)).isoformat()


def test_the_queue_lists_pending_applications_the_longest_waiting_first(
    applicant: Callable[..., Person], operator: Staff, kyc_settings: Settings
) -> None:
    first, second = applicant(), applicant()
    draft = applicant(submit=False)
    first_id, second_id = (_application_id(kyc_settings, p) for p in (first, second))

    response = operator.queue()

    assert response.status_code == HTTPStatus.OK, response.text
    body = response.json()
    ids = [uuid.UUID(item["id"]) for item in body["items"]]
    assert ids.index(first_id) < ids.index(second_id)
    assert _application_id(kyc_settings, draft) not in ids
    assert body["total"] >= len(ids) >= 2
    item = body["items"][ids.index(first_id)]
    assert (item["first_name"], item["last_name"]) == ("Анна", "Ковалёва")


def test_the_queue_size_is_limited(operator: Staff, applicant: Callable[..., Person]) -> None:
    applicant()
    applicant()

    response = operator.queue(limit=1)

    assert response.status_code == HTTPStatus.OK, response.text
    assert len(response.json()["items"]) == 1
    assert response.json()["total"] >= 2
    assert operator.queue(limit=101).status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert operator.queue(limit=0).status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_the_card_shows_what_the_documents_are_checked_against(
    applicant: Callable[..., Person], operator: Staff, kyc_settings: Settings
) -> None:
    application_id = _application_id(kyc_settings, applicant(birth_date=_years_ago(30)))

    response = operator.card(application_id)

    assert response.status_code == HTTPStatus.OK, response.text
    body = response.json()
    assert body["status"] == "pending"
    assert (body["first_name"], body["last_name"]) == ("Анна", "Ковалёва")
    assert body["birth_date"] == _years_ago(30)
    assert body["age_years"] == 30
    assert body["license_issued_on"] is None
    assert sorted(document["kind"] for document in body["documents"]) == sorted(REQUIRED_DOCUMENTS)


def test_an_unknown_application_is_404(operator: Staff) -> None:
    unknown = uuid.uuid4()

    for response in (
        operator.card(unknown),
        operator.approve(unknown),
        operator.reject(unknown),
        operator.url(unknown, DocumentKind.PASSPORT),
    ):
        assert response.status_code == HTTPStatus.NOT_FOUND, response.text
        assert problem_type(response) == "kyc-application-not-found"


def test_approval_admits_the_client_and_is_audited(
    applicant: Callable[..., Person], operator: Staff, kyc_settings: Settings
) -> None:
    client = applicant()
    application_id = _application_id(kyc_settings, client)

    response = operator.approve(application_id)

    assert response.status_code == HTTPStatus.OK, response.text
    assert response.json()["status"] == "approved"
    assert response.json()["license_issued_on"] == LICENSED
    assert response.json()["decided_at"] is not None
    assert client.application().json()["status"] == "approved"
    assert application_id not in [uuid.UUID(i["id"]) for i in operator.queue().json()["items"]]
    [event] = _events(kyc_settings, application_id)
    assert (event.actor_type, event.actor_id, event.action) == (
        "staff",
        operator.id,
        "kyc.approved",
    )
    assert (event.target_type, event.details) == ("kyc_application", {})


# Years and days ago, (birth date, license issue date): each one day short of the threshold.
# Turned into dates inside the test, so a run that crosses midnight does not shift them.
@pytest.mark.parametrize(
    ("ages", "detail"),
    [
        (((21, 1), (3, 0)), "The client must be at least 21 years old."),
        (((30, 0), (2, 1)), "The client must have held a license for at least 2 years."),
    ],
)
def test_approval_is_refused_below_the_requirements(
    applicant: Callable[..., Person],
    operator: Staff,
    kyc_settings: Settings,
    ages: tuple[tuple[int, int], tuple[int, int]],
    detail: str,
) -> None:
    birth_date, license_issued_on = (_years_ago(years, days) for years, days in ages)
    application_id = _application_id(kyc_settings, applicant(birth_date=birth_date))

    response = operator.approve(application_id, license_issued_on)

    assert response.status_code == HTTPStatus.CONFLICT, response.text
    assert problem_type(response) == "kyc-requirements-not-met"
    assert response.json()["detail"] == detail
    assert operator.card(application_id).json()["status"] == "pending"
    assert _events(kyc_settings, application_id) == []


def test_the_requirements_are_exactly_met_on_the_day(
    applicant: Callable[..., Person], operator: Staff, kyc_settings: Settings
) -> None:
    application_id = _application_id(kyc_settings, applicant(birth_date=_years_ago(21)))

    response = operator.approve(application_id, _years_ago(2))

    assert response.status_code == HTTPStatus.OK, response.text


def test_the_thresholds_come_from_the_settings(
    kyc_settings: Settings, applicant: Callable[..., Person]
) -> None:
    application_id = _application_id(kyc_settings, applicant(birth_date=_years_ago(25)))
    strict = kyc_settings.model_copy(update={"kyc_min_age_years": 30})

    for client in client_for(strict):
        response = Staff(client, strict, StaffRole.OPERATOR).approve(application_id)

        assert response.status_code == HTTPStatus.CONFLICT, response.text
        assert response.json()["detail"] == "The client must be at least 30 years old."


def test_approval_needs_the_name_and_birth_date(
    client: TestClient, operator: Staff, kyc_settings: Settings
) -> None:
    # Submitting does not ask for the profile yet; the approval does.
    person = Person(client)
    person.upload_all()
    assert person.submit().status_code == HTTPStatus.OK

    response = operator.approve(_application_id(kyc_settings, person))

    assert response.status_code == HTTPStatus.CONFLICT, response.text
    assert problem_type(response) == "kyc-profile-incomplete"


_TOMORROW = "tomorrow"


@pytest.mark.parametrize(
    ("license_issued_on", "problem"),
    [
        (_TOMORROW, "license-issue-date-invalid"),
        ("1990-05-16", "license-issue-date-invalid"),  # before the birth date
        ("2015-6-1", "validation-error"),
        (1433116800, "validation-error"),
        ("2015-06-01T00:00:00Z", "validation-error"),
        (None, "validation-error"),
    ],
)
def test_the_license_issue_date_must_be_a_plausible_date(
    applicant: Callable[..., Person],
    operator: Staff,
    kyc_settings: Settings,
    license_issued_on: object,
    problem: str,
) -> None:
    application_id = _application_id(kyc_settings, applicant())
    if license_issued_on == _TOMORROW:
        license_issued_on = _years_ago(0, days=1)

    response = operator.approve(application_id, license_issued_on)

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY, response.text
    assert problem_type(response) == problem


def test_the_body_takes_no_unknown_fields(
    applicant: Callable[..., Person], operator: Staff, kyc_settings: Settings, client: TestClient
) -> None:
    application_id = _application_id(kyc_settings, applicant())

    response = client.post(
        f"{URL}/{application_id}/approve",
        json={"license_issued_on": LICENSED, "status": "approved"},
        headers=operator.headers,
    )

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY, response.text


def test_rejection_tells_the_client_what_to_fix_and_keeps_the_reason_out_of_the_audit(
    applicant: Callable[..., Person], operator: Staff, kyc_settings: Settings
) -> None:
    client = applicant()
    application_id = _application_id(kyc_settings, client)

    response = operator.reject(application_id, "  The selfie is blurry  ")

    assert response.status_code == HTTPStatus.OK, response.text
    assert response.json()["status"] == "rejected"
    application = client.application().json()
    assert (application["status"], application["rejection_reason"]) == (
        "rejected",
        "The selfie is blurry",
    )
    [event] = _events(kyc_settings, application_id)
    assert (event.action, event.actor_id, event.details) == ("kyc.rejected", operator.id, {})

    # The client fixes one photo and the application is back in the queue.
    assert client.upload(DocumentKind.SELFIE_WITH_LICENSE).status_code == HTTPStatus.OK
    assert client.submit().status_code == HTTPStatus.OK
    card = operator.card(application_id).json()
    assert (card["status"], card["decided_at"], card["rejection_reason"]) == ("pending", None, None)


# Control characters count as blank: pydantic's own trimming would keep U+001F and U+001C.
@pytest.mark.parametrize("reason", ["", "   ", "\x1f", " \x1c\t", "x" * 501])
def test_rejection_needs_a_reason(
    applicant: Callable[..., Person], operator: Staff, kyc_settings: Settings, reason: str
) -> None:
    application_id = _application_id(kyc_settings, applicant())

    response = operator.reject(application_id, reason)

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY, response.text
    assert operator.card(application_id).json()["status"] == "pending"


def test_a_decision_is_made_once(
    applicant: Callable[..., Person], operator: Staff, kyc_settings: Settings
) -> None:
    approved = _application_id(kyc_settings, applicant())
    rejected = _application_id(kyc_settings, applicant())
    draft = _application_id(kyc_settings, applicant(submit=False))
    assert operator.approve(approved).status_code == HTTPStatus.OK
    assert operator.reject(rejected).status_code == HTTPStatus.OK

    for application_id in (approved, rejected, draft):
        for response in (operator.approve(application_id), operator.reject(application_id)):
            assert response.status_code == HTTPStatus.CONFLICT, response.text
            assert problem_type(response) == "kyc-not-pending"


def test_a_document_link_opens_the_photo_and_is_audited(
    applicant: Callable[..., Person], operator: Staff, kyc_settings: Settings
) -> None:
    application_id = _application_id(kyc_settings, applicant())

    response = operator.url(application_id, DocumentKind.PASSPORT)

    assert response.status_code == HTTPStatus.OK, response.text
    assert response.json()["expires_in"] == 300
    assert response.headers["Cache-Control"] == "no-store"
    opened = open_url(response.json()["url"])
    assert opened.status == HTTPStatus.OK
    assert opened.body.startswith(b"\xff\xd8")  # JPEG
    assert opened.cache_control == "no-store"
    [event] = _events(kyc_settings, application_id)
    assert (event.action, event.actor_id, event.details) == (
        "kyc.document_viewed",
        operator.id,
        {"kind": "passport"},
    )


def test_document_links_are_limited_per_staff_member(
    applicant: Callable[..., Person],
    operator: Staff,
    client: TestClient,
    kyc_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        service, "STAFF_DOCUMENT_URL_LIMIT", Limit("kyc-document-url-staff-test", 1, 3600)
    )
    application_id = _application_id(kyc_settings, applicant())

    assert operator.url(application_id, DocumentKind.PASSPORT).status_code == HTTPStatus.OK
    limited = operator.url(application_id, DocumentKind.PASSPORT)

    assert limited.status_code == HTTPStatus.TOO_MANY_REQUESTS, limited.text
    assert "Retry-After" in limited.headers
    # Counted per staff member: a colleague still opens the photos.
    colleague = Staff(client, kyc_settings, StaffRole.OPERATOR)
    assert colleague.url(application_id, DocumentKind.PASSPORT).status_code == HTTPStatus.OK
    # Only the links handed out are in the audit log.
    assert len(_events(kyc_settings, application_id)) == 2


def test_a_link_to_a_document_not_uploaded_is_404(
    client: TestClient, operator: Staff, kyc_settings: Settings
) -> None:
    person = Person(client)
    assert person.upload(DocumentKind.PASSPORT).status_code == HTTPStatus.OK

    response = operator.url(_application_id(kyc_settings, person), DocumentKind.LICENSE_FRONT)

    assert response.status_code == HTTPStatus.NOT_FOUND, response.text
    assert problem_type(response) == "kyc-document-not-found"


def _review_requests(application_id: uuid.UUID) -> list[tuple[str, str, Any]]:
    return [
        ("GET", URL, None),
        ("GET", f"{URL}/{application_id}", None),
        ("GET", f"{URL}/{application_id}/documents/passport/url", None),
        ("POST", f"{URL}/{application_id}/approve", {"license_issued_on": LICENSED}),
        ("POST", f"{URL}/{application_id}/reject", {"reason": "The selfie is blurry"}),
    ]


def _status(operator: Staff, application_id: uuid.UUID) -> str:
    status: str = operator.card(application_id).json()["status"]
    return status


@pytest.mark.parametrize("role", [StaffRole.OPERATOR, StaffRole.ADMIN])
def test_operators_and_admins_review(
    client: TestClient, kyc_settings: Settings, role: StaffRole
) -> None:
    response = Staff(client, kyc_settings, role).queue()

    assert response.status_code == HTTPStatus.OK, response.text


def test_technicians_do_not_see_the_queue(
    client: TestClient,
    kyc_settings: Settings,
    operator: Staff,
    applicant: Callable[..., Person],
) -> None:
    application_id = _application_id(kyc_settings, applicant())
    technician = Staff(client, kyc_settings, StaffRole.TECHNICIAN)

    for method, path, json in _review_requests(application_id):
        response = client.request(method, path, json=json, headers=technician.headers)

        assert response.status_code == HTTPStatus.FORBIDDEN, (path, response.text)
        assert problem_type(response) == "forbidden"
    assert _status(operator, application_id) == "pending"
    assert _events(kyc_settings, application_id) == []


def test_clients_and_anonymous_callers_are_refused(
    client: TestClient,
    kyc_settings: Settings,
    operator: Staff,
    applicant: Callable[..., Person],
) -> None:
    owner = applicant()
    application_id = _application_id(kyc_settings, owner)

    # Not even the client the application belongs to: a client token is not a staff token.
    for headers in (owner.headers, {}):
        for method, path, json in _review_requests(application_id):
            response = client.request(method, path, json=json, headers=headers)

            assert response.status_code == HTTPStatus.UNAUTHORIZED, (path, response.text)
            assert problem_type(response) == "access-token-invalid"
    assert _status(operator, application_id) == "pending"


def _assert_profile_locked(client: Person) -> None:
    for change in ({"first_name": "Мария"}, {"last_name": "Иванова"}, {"birth_date": "1991-01-01"}):
        response = client.update_profile(**change)

        assert response.status_code == HTTPStatus.CONFLICT, response.text
        assert problem_type(response) == "kyc-locked"
    # The app sends the whole form back: unchanged values and the other fields still save.
    response = client.update_profile(
        first_name="Анна",
        last_name="Ковалёва",
        birth_date=ADULT_BIRTH_DATE,
        email="anna@example.com",
        language="en",
    )
    assert response.status_code == HTTPStatus.OK, response.text


def test_the_profile_is_locked_while_under_review_and_after_approval(
    applicant: Callable[..., Person], operator: Staff, kyc_settings: Settings
) -> None:
    client = applicant()

    _assert_profile_locked(client)
    assert operator.approve(_application_id(kyc_settings, client)).status_code == HTTPStatus.OK
    _assert_profile_locked(client)


@pytest.mark.parametrize("rejected", [False, True])
def test_the_profile_can_change_before_submission_and_after_rejection(
    applicant: Callable[..., Person], operator: Staff, kyc_settings: Settings, rejected: bool
) -> None:
    client = applicant(submit=rejected)
    if rejected:
        assert operator.reject(_application_id(kyc_settings, client)).status_code == HTTPStatus.OK

    response = client.update_profile(first_name="Мария", birth_date="1991-01-01")

    assert response.status_code == HTTPStatus.OK, response.text
    assert response.json()["first_name"] == "Мария"


def test_a_submission_waits_for_a_profile_change_in_progress(
    applicant: Callable[..., Person], operator: Staff, kyc_settings: Settings
) -> None:
    client = applicant(submit=False)
    user_id: uuid.UUID = query(
        kyc_settings, "SELECT id FROM users WHERE phone = :phone", phone=client.phone
    )[0].id
    too_young = date.fromisoformat(_years_ago(20))

    async def change_birth_date_while_submitting() -> Any:
        engine = create_engine(kyc_settings)
        try:
            async with create_session_factory(engine)() as db:
                async with db.begin():
                    # What PATCH /me does for a changed birth date, left uncommitted for a while.
                    await service.lock_personal_data(db, user_id)
                    await db.execute(
                        text("UPDATE users SET birth_date = :birth_date WHERE id = :id"),
                        {"birth_date": too_young, "id": user_id},
                    )
                    submission = asyncio.create_task(asyncio.to_thread(client.submit))
                    await asyncio.sleep(0.5)
                    waited = not submission.done()
                return waited, await submission
        finally:
            await engine.dispose()

    waited, submitted = asyncio.run(change_birth_date_while_submitting())

    assert waited, "the submission did not wait for the profile change"
    assert submitted.status_code == HTTPStatus.OK, submitted.text
    # So the operator checks the documents against the birth date the client ended up with.
    response = operator.approve(_application_id(kyc_settings, client))
    assert response.status_code == HTTPStatus.CONFLICT, response.text
    assert response.json()["detail"] == "The client must be at least 21 years old."
