import json
from datetime import datetime
from typing import Any

import pyotp
import pytest
import requests
import responses

from flagman_load.bootstrap import (
    LOAD_FIRST_NAME,
    BootstrapError,
    Target,
    approve_load_clients,
    close_open_rental,
    load_applications,
    prepare_renter,
    read_zones,
    refresh_staff,
    refuse_real_clients,
    require_approved,
    sign_in,
    staff_login,
)
from flagman_load.run_state import ClientSession, StaffSession

BASE = "https://api.stand.example"
TARGET = Target(BASE, send_client_ip=False)
SESSION = ClientSession("10.0.0.1", "access", "refresh")
QUEUE = f"{BASE}/admin/kyc/applications"
SUBMITTED_AT = "2026-11-05T12:00:00.123456Z"
OURS = {"id": "ours", "first_name": LOAD_FIRST_NAME, "submitted_at": SUBMITTED_AT}
REAL = {"id": "real", "first_name": "Анна", "submitted_at": SUBMITTED_AT}
OTHER_RUN = {
    "id": "other-run",
    "first_name": LOAD_FIRST_NAME,
    "submitted_at": "2026-11-05T11:00:00.654321Z",
}
STAFF_COOKIE = {"Set-Cookie": "flagman_staff_refresh=rotated; Path=/staff/auth; Secure; HttpOnly"}


def _rental(status: str, *, after_submitted: bool = False) -> dict[str, Any]:
    return {"id": "r1", "status": status, "after_inspection_submitted": after_submitted}


def _current(rental: dict[str, Any] | None) -> None:
    responses.get(f"{BASE}/rentals/current", json={"rental": rental})


def _calls(url: str) -> int:
    return sum(call.request.url == url for call in responses.calls)


def test_neither_a_real_application_nor_another_run_is_picked() -> None:
    picked = load_applications([OURS, REAL, OTHER_RUN], {datetime.fromisoformat(SUBMITTED_AT)})
    assert picked == [OURS]


@responses.activate
def test_only_applications_of_this_run_are_approved() -> None:
    responses.get(
        f"{BASE}/kyc/application", json={"status": "pending", "submitted_at": SUBMITTED_AT}
    )
    responses.get(QUEUE, json={"items": [REAL, OTHER_RUN, OURS], "total": 3})
    responses.get(QUEUE, json={"items": [REAL, OTHER_RUN], "total": 2})
    approve = responses.post(f"{QUEUE}/ours/approve", json={})
    # An unregistered URL raises, so approving anything else would fail this test.
    approve_load_clients(TARGET, [SESSION], "operator")
    assert approve.call_count == 1


@responses.activate
def test_a_renter_left_unapproved_stops_the_preparation() -> None:
    responses.get(f"{BASE}/kyc/application", json={"status": "pending"})
    with pytest.raises(BootstrapError, match="1 of 1"):
        require_approved(TARGET, [SESSION])


@responses.activate
def test_approved_renters_pass() -> None:
    responses.get(f"{BASE}/kyc/application", json={"status": "approved"})
    require_approved(TARGET, [SESSION])


@responses.activate
def test_no_open_rental_changes_nothing() -> None:
    _current(None)
    close_open_rental(TARGET, requests.Session(), b"photo")
    assert len(responses.calls) == 1


@responses.activate
def test_a_left_reservation_is_cancelled() -> None:
    _current(_rental("reserved"))
    cancel = responses.post(f"{BASE}/rentals/r1/cancel", json={})
    close_open_rental(TARGET, requests.Session(), b"photo")
    assert cancel.call_count == 1


@responses.activate
def test_a_left_trip_gets_its_inspection_and_is_finished() -> None:
    _current(_rental("in_trip"))
    photos = responses.post(f"{BASE}/rentals/r1/inspections/after/photos", json={})
    submit = responses.post(f"{BASE}/rentals/r1/inspections/after/submit", json={})
    finish = responses.post(f"{BASE}/rentals/r1/finish", json={})
    close_open_rental(TARGET, requests.Session(), b"photo")
    assert (photos.call_count, submit.call_count, finish.call_count) == (4, 1, 1)


@responses.activate
def test_a_submitted_inspection_is_not_repeated() -> None:
    _current(_rental("parked", after_submitted=True))
    finish = responses.post(f"{BASE}/rentals/r1/finish", json={})
    close_open_rental(TARGET, requests.Session(), b"photo")
    assert finish.call_count == 1


@responses.activate
def test_a_finish_the_api_refuses_stops_the_preparation() -> None:
    _current(_rental("in_trip", after_submitted=True))
    responses.post(
        f"{BASE}/rentals/r1/finish",
        status=409,
        json={"type": "urn:flagman:problem:outside-end-zone"},
    )
    with pytest.raises(BootstrapError, match="outside-end-zone"):
        close_open_rental(TARGET, requests.Session(), b"photo")


@responses.activate
def test_a_phone_of_a_real_client_is_refused_without_naming_it() -> None:
    responses.get(f"{BASE}/me", json={"first_name": "Анна"})
    with pytest.raises(BootstrapError) as error:
        refuse_real_clients(TARGET, [SESSION])
    assert "Анна" not in str(error.value)
    assert len(responses.calls) == 1


@pytest.mark.parametrize("name", [None, "", LOAD_FIRST_NAME])
@responses.activate
def test_a_client_without_a_profile_or_of_an_earlier_run_is_accepted(name: str | None) -> None:
    responses.get(f"{BASE}/me", json={"first_name": name})
    refuse_real_clients(TARGET, [SESSION])


@responses.activate
def test_a_client_with_a_debt_stops_the_preparation() -> None:
    _current(None)
    responses.get(f"{BASE}/rentals/debts", json={"debts": [{"rental_id": "r0"}]})
    with pytest.raises(BootstrapError, match="owes"):
        prepare_renter(TARGET, SESSION, b"photo")


@responses.activate
def test_sign_in_asks_for_a_code_and_keeps_the_tokens() -> None:
    target = Target(BASE, send_client_ip=True)
    request = responses.post(f"{BASE}/auth/otp/request", status=202, json={})
    verify = responses.post(
        f"{BASE}/auth/otp/verify", json={"access_token": "a", "refresh_token": "r"}
    )
    session = sign_in(target, "+375291000000", "402918", "10.0.0.9")
    assert (session.access_token, session.refresh_token, session.ip) == ("a", "r", "10.0.0.9")
    assert request.calls[0].request.headers["X-Forwarded-For"] == "10.0.0.9"
    assert verify.calls[0].request.headers["X-Forwarded-For"] == "10.0.0.9"


@responses.activate
def test_sign_in_sends_no_client_ip_to_the_stand() -> None:
    request = responses.post(f"{BASE}/auth/otp/request", status=202, json={})
    responses.post(f"{BASE}/auth/otp/verify", json={"access_token": "a", "refresh_token": "r"})
    sign_in(TARGET, "+375291000000", "402918", "10.0.0.9")
    assert "X-Forwarded-For" not in request.calls[0].request.headers


@responses.activate
def test_a_refused_code_stops_the_sign_in() -> None:
    responses.post(
        f"{BASE}/auth/otp/request",
        status=429,
        json={"type": "urn:flagman:problem:rate-limited"},
    )
    with pytest.raises(BootstrapError, match="rate-limited"):
        sign_in(TARGET, "+375291000000", "402918", "10.0.0.9")


@responses.activate
def test_staff_login_answers_the_challenge_with_a_current_code() -> None:
    secret = pyotp.random_base32()
    responses.post(
        f"{BASE}/staff/auth/password", json={"challenge_token": "c", "next_step": "totp"}
    )
    totp = responses.post(
        f"{BASE}/staff/auth/totp", json={"access_token": "staff"}, headers=STAFF_COOKIE
    )
    session = staff_login(TARGET, "load@flagman.example", "password", secret)
    assert (session.access_token, session.refresh_token) == ("staff", "rotated")
    sent = json.loads(totp.calls[0].request.body)
    assert sent["challenge_token"] == "c"
    assert pyotp.TOTP(secret).verify(sent["code"], valid_window=1)


@responses.activate
def test_a_staff_refresh_sends_the_cookie_and_keeps_the_rotated_one() -> None:
    refresh = responses.post(
        f"{BASE}/staff/auth/token/refresh", json={"access_token": "new"}, headers=STAFF_COOKIE
    )
    session = StaffSession("old", "current", issued_at=0.0)
    refresh_staff(TARGET, session)
    assert refresh.calls[0].request.headers["Cookie"] == "flagman_staff_refresh=current"
    assert (session.access_token, session.refresh_token) == ("new", "rotated")
    assert not session.needs_refresh(session.issued_at)


@responses.activate
def test_a_refused_staff_refresh_keeps_the_session_and_stops_with_an_error() -> None:
    responses.post(f"{BASE}/staff/auth/token/refresh", json={}, status=401)
    session = StaffSession("old", "current", issued_at=0.0)
    with pytest.raises(BootstrapError, match="401"):
        refresh_staff(TARGET, session)
    assert session.refresh_token == "current"


@responses.activate
def test_staff_without_2fa_set_up_stops_the_login() -> None:
    responses.post(
        f"{BASE}/staff/auth/password",
        json={"challenge_token": "c", "next_step": "totp_enrollment"},
    )
    with pytest.raises(BootstrapError, match="totp_enrollment"):
        staff_login(TARGET, "load@flagman.example", "password", pyotp.random_base32())


@responses.activate
def test_a_renter_already_prepared_only_gets_checked() -> None:
    _current(None)
    responses.get(f"{BASE}/rentals/debts", json={"debts": []})
    responses.get(f"{BASE}/kyc/application", json={"status": "approved"})
    responses.get(f"{BASE}/payment-cards", json={"items": [{"id": "card"}]})
    prepare_renter(TARGET, SESSION, b"photo")
    assert all(call.request.method == "GET" for call in responses.calls)


@responses.activate
def test_a_new_renter_gets_a_profile_documents_and_a_card() -> None:
    _current(None)
    responses.get(f"{BASE}/rentals/debts", json={"debts": []})
    responses.get(f"{BASE}/kyc/application", json={"status": "not_submitted"})
    profile = responses.patch(f"{BASE}/me", json={})
    documents = [
        responses.put(f"{BASE}/kyc/documents/{kind}", json={})
        for kind in ("license_front", "license_back", "passport", "selfie_with_license")
    ]
    submit = responses.post(f"{BASE}/kyc/application/submit", json={})
    responses.get(f"{BASE}/payment-cards", json={"items": []})
    responses.post(
        f"{BASE}/payment-cards/bindings",
        status=201,
        json={"id": "b1", "page_url": f"{BASE}/fake-provider/cards?session=s1"},
    )
    page = responses.post(
        f"{BASE}/fake-provider/cards", status=303, headers={"Location": "flagmandrive://x"}
    )
    responses.get(f"{BASE}/payment-cards/bindings/b1", json={"status": "succeeded"})

    prepare_renter(TARGET, SESSION, b"photo")

    assert profile.call_count == 1
    assert all(document.call_count == 1 for document in documents)
    assert submit.call_count == 1
    assert page.call_count == 1


@responses.activate
def test_a_declined_card_stops_the_preparation() -> None:
    _current(None)
    responses.get(f"{BASE}/rentals/debts", json={"debts": []})
    responses.get(f"{BASE}/kyc/application", json={"status": "approved"})
    responses.get(f"{BASE}/payment-cards", json={"items": []})
    responses.post(
        f"{BASE}/payment-cards/bindings",
        status=201,
        json={"id": "b1", "page_url": f"{BASE}/fake-provider/cards?session=s1"},
    )
    responses.post(f"{BASE}/fake-provider/cards", status=303)
    responses.get(f"{BASE}/payment-cards/bindings/b1", json={"status": "declined"})
    with pytest.raises(BootstrapError, match="declined"):
        prepare_renter(TARGET, SESSION, b"photo")


@responses.activate
def test_reading_zones_passes_a_timeout() -> None:
    zones = responses.get(f"{BASE}/map/zones", json={"items": [{"kind": "allowed"}]})
    assert read_zones(TARGET, SESSION)[0]["kind"] == "allowed"
    assert zones.calls[0].request.req_kwargs["timeout"] is not None
