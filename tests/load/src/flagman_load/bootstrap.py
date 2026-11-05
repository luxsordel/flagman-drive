"""Prepares clients and staff over the public API, the way the apps do it.

No seed command creates a ready client, and one that writes straight to the database would skip the
code under test. Re-running against the stand skips what is already done and ends the rentals a
stopped run left open.
"""

import time
import uuid
from collections.abc import Collection, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pyotp
import requests

from flagman_load.photos import make_photo
from flagman_load.pools import client_ip
from flagman_load.problems import error_code
from flagman_load.profiles import INSPECTION_PHOTOS
from flagman_load.run_state import ClientSession, MapZone, StaffSession

# Only applications of this run with this first name are approved: on the stand the queue may hold
# real ones and those of another run.
LOAD_FIRST_NAME = "Loadtest"
_STAFF_REFRESH_COOKIE = "flagman_staff_refresh"
_KYC_DOCUMENTS = ("license_front", "license_back", "passport", "selfie_with_license")
_TEST_CARD = {"number": "4242424242424242", "expiry": "12/30", "cvc": "123", "action": "bind"}
_RETURN_URL = "flagmandrive://load-test"
_FAKE_CARD_PAGE = "/fake-provider/cards"
_TIMEOUT_S = 30
_PARALLEL = 8
_QUEUE_PAGE = 100


class BootstrapError(RuntimeError):
    pass


@dataclass(frozen=True)
class Target:
    base_url: str
    send_client_ip: bool


def _checked(response: requests.Response, step: str) -> Any:
    if not response.ok:
        raise BootstrapError(f"{step}: {response.status_code} {error_code(response)}")
    return response.json() if response.content else None


def _ip_headers(target: Target, ip: str) -> dict[str, str]:
    return {"X-Forwarded-For": ip} if target.send_client_ip else {}


def sign_in(target: Target, phone: str, code: str, ip: str) -> ClientSession:
    device_id = str(uuid.uuid4())
    headers = _ip_headers(target, ip)
    _checked(
        requests.post(
            f"{target.base_url}/auth/otp/request",
            json={"phone": phone},
            headers=headers,
            timeout=_TIMEOUT_S,
        ),
        "request a code",
    )
    tokens = _checked(
        requests.post(
            f"{target.base_url}/auth/otp/verify",
            json={"phone": phone, "code": code, "device_id": device_id},
            headers=headers,
            timeout=_TIMEOUT_S,
        ),
        "verify the code",
    )
    return ClientSession(ip, tokens["access_token"], tokens["refresh_token"])


def _client(target: Target, session: ClientSession) -> requests.Session:
    client = requests.Session()
    client.headers["Authorization"] = f"Bearer {session.access_token}"
    client.headers.update(_ip_headers(target, session.ip))
    return client


def _submit_kyc(target: Target, client: requests.Session, photo: bytes) -> None:
    status = _checked(client.get(f"{target.base_url}/kyc/application", timeout=_TIMEOUT_S), "kyc")[
        "status"
    ]
    if status in ("pending", "approved"):
        return
    _checked(
        client.patch(
            f"{target.base_url}/me",
            json={"first_name": LOAD_FIRST_NAME, "last_name": "Client", "birth_date": "1990-01-01"},
            timeout=_TIMEOUT_S,
        ),
        "fill the profile",
    )
    for kind in _KYC_DOCUMENTS:
        _checked(
            client.put(
                f"{target.base_url}/kyc/documents/{kind}",
                data=photo,
                headers={"Content-Type": "image/jpeg"},
                timeout=_TIMEOUT_S,
            ),
            "upload a document",
        )
    _checked(
        client.post(f"{target.base_url}/kyc/application/submit", timeout=_TIMEOUT_S),
        "submit the application",
    )


def _bind_card(target: Target, client: requests.Session) -> None:
    cards = _checked(client.get(f"{target.base_url}/payment-cards", timeout=_TIMEOUT_S), "cards")
    if cards["items"]:
        return
    binding = _checked(
        client.post(
            f"{target.base_url}/payment-cards/bindings",
            json={"return_url": _RETURN_URL},
            timeout=_TIMEOUT_S,
        ),
        "start a card binding",
    )
    session = parse_qs(urlsplit(binding["page_url"]).query)["session"][0]
    # Not followed: the redirect goes to the app's own scheme.
    page = requests.post(
        f"{target.base_url}{_FAKE_CARD_PAGE}",
        data={"session": session, **_TEST_CARD},
        allow_redirects=False,
        timeout=_TIMEOUT_S,
    )
    if page.status_code != requests.codes.see_other:
        raise BootstrapError(f"enter the test card: {page.status_code}")
    outcome = _checked(
        client.get(f"{target.base_url}/payment-cards/bindings/{binding['id']}", timeout=_TIMEOUT_S),
        "read the card binding",
    )
    if outcome["status"] != "succeeded":
        raise BootstrapError(f"the card binding ended as {outcome['status']}")


def refuse_real_clients(target: Target, sessions: Sequence[ClientSession]) -> None:
    """Before anything changes: a run renames a client without a profile and rents cars on it."""

    def first_name(session: ClientSession) -> object:
        client = _client(target, session)
        profile = _checked(
            client.get(f"{target.base_url}/me", timeout=_TIMEOUT_S), "read a profile"
        )
        return profile["first_name"]

    with ThreadPoolExecutor(_PARALLEL) as pool:
        names = list(pool.map(first_name, sessions))
    real = sum(name not in (None, "", LOAD_FIRST_NAME) for name in names)
    if real:
        # Without the phones or the names: this message ends up in logs.
        raise BootstrapError(
            f"{real} phones of the pool belong to real profiles, use dedicated ones"
        )


def _refuse_debt(target: Target, client: requests.Session) -> None:
    """The client could not reserve, and the run would only show it as failed reservations."""
    debts = _checked(
        client.get(f"{target.base_url}/rentals/debts", timeout=_TIMEOUT_S), "read debts"
    )
    if debts["debts"]:
        raise BootstrapError("a client of the pool owes for an earlier rental, settle it first")


def close_open_rental(target: Target, client: requests.Session, photo: bytes) -> None:
    """Ends what a stopped run left behind: with an open rental the client cannot reserve."""
    rental = _checked(
        client.get(f"{target.base_url}/rentals/current", timeout=_TIMEOUT_S),
        "read the current rental",
    )["rental"]
    if rental is None:
        return
    url = f"{target.base_url}/rentals/{rental['id']}"
    if rental["status"] == "reserved":
        _checked(client.post(f"{url}/cancel", timeout=_TIMEOUT_S), "cancel a left rental")
        return
    if not rental["after_inspection_submitted"]:
        for _ in range(INSPECTION_PHOTOS):
            _checked(
                client.post(
                    f"{url}/inspections/after/photos",
                    data=photo,
                    headers={"Content-Type": "image/jpeg"},
                    timeout=_TIMEOUT_S,
                ),
                "upload a photo of a left rental",
            )
        _checked(
            client.post(f"{url}/inspections/after/submit", timeout=_TIMEOUT_S),
            "submit the inspection of a left rental",
        )
    _checked(client.post(f"{url}/finish", timeout=_TIMEOUT_S), "finish a left rental")


def prepare_renter(target: Target, session: ClientSession, photo: bytes) -> None:
    client = _client(target, session)
    close_open_rental(target, client, photo)
    _refuse_debt(target, client)
    _submit_kyc(target, client, photo)
    _bind_card(target, client)


def _staff_refresh_cookie(response: requests.Response) -> str:
    cookie = response.cookies.get(_STAFF_REFRESH_COOKIE)
    if cookie is None:
        raise BootstrapError("the staff refresh cookie is missing")
    return str(cookie)


def staff_login(target: Target, email: str, password: str, totp_secret: str) -> StaffSession:
    """One sign-in per account per 30 s: a code cannot be used twice."""
    challenge = _checked(
        requests.post(
            f"{target.base_url}/staff/auth/password",
            json={"email": email, "password": password},
            timeout=_TIMEOUT_S,
        ),
        "staff password",
    )
    if challenge["next_step"] != "totp":
        raise BootstrapError(f"staff sign-in wants {challenge['next_step']}")
    response = requests.post(
        f"{target.base_url}/staff/auth/totp",
        json={
            "challenge_token": challenge["challenge_token"],
            "code": pyotp.TOTP(totp_secret).now(),
        },
        timeout=_TIMEOUT_S,
    )
    token = _checked(response, "staff code")
    return StaffSession(str(token["access_token"]), _staff_refresh_cookie(response))


def refresh_staff(target: Target, session: StaffSession) -> None:
    """The cookie goes by hand: it is `Secure`, and a local run speaks plain HTTP."""
    response = requests.post(
        f"{target.base_url}/staff/auth/token/refresh",
        json={},
        headers={"Cookie": f"{_STAFF_REFRESH_COOKIE}={session.refresh_token}"},
        timeout=_TIMEOUT_S,
    )
    token = _checked(response, "staff token refresh")
    session.refresh_token = _staff_refresh_cookie(response)
    session.access_token = str(token["access_token"])
    session.issued_at = time.time()


def load_applications(
    items: Sequence[dict[str, Any]], submitted: Collection[datetime]
) -> list[dict[str, Any]]:
    return [
        item
        for item in items
        if item["first_name"] == LOAD_FIRST_NAME
        and datetime.fromisoformat(item["submitted_at"]) in submitted
    ]


def _submitted_at(target: Target, sessions: Sequence[ClientSession]) -> set[datetime]:
    """The queue does not name the client, and this moment is what both sides of it can see."""

    def one(session: ClientSession) -> str | None:
        application = _checked(
            _client(target, session).get(f"{target.base_url}/kyc/application", timeout=_TIMEOUT_S),
            "read an application",
        )
        moment: str | None = application["submitted_at"]
        return moment

    with ThreadPoolExecutor(_PARALLEL) as pool:
        moments = list(pool.map(one, sessions))
    return {datetime.fromisoformat(moment) for moment in moments if moment is not None}


def approve_load_clients(
    target: Target, sessions: Sequence[ClientSession], operator_token: str
) -> None:
    submitted = _submitted_at(target, sessions)
    client = requests.Session()
    client.headers["Authorization"] = f"Bearer {operator_token}"
    issued_on = (datetime.now(UTC) - timedelta(days=5 * 365)).date().isoformat()
    while True:
        queue = _checked(
            client.get(
                f"{target.base_url}/admin/kyc/applications",
                params={"limit": _QUEUE_PAGE},
                timeout=_TIMEOUT_S,
            ),
            "read the KYC queue",
        )
        ours = load_applications(queue["items"], submitted)
        if not ours:
            return
        for item in ours:
            _checked(
                client.post(
                    f"{target.base_url}/admin/kyc/applications/{item['id']}/approve",
                    json={"license_issued_on": issued_on},
                    timeout=_TIMEOUT_S,
                ),
                "approve an application",
            )


def require_approved(target: Target, sessions: Sequence[ClientSession]) -> None:
    """The queue shows only its oldest page, so a full one can hide our applications from us."""
    waiting = 0
    for session in sessions:
        application = _checked(
            _client(target, session).get(f"{target.base_url}/kyc/application", timeout=_TIMEOUT_S),
            "read an application",
        )
        if application["status"] != "approved":
            waiting += 1
    if waiting:
        raise BootstrapError(f"{waiting} of {len(sessions)} renters were not approved")


def prepare_clients(target: Target, phones: Sequence[str], code: str) -> list[ClientSession]:
    def one(item: tuple[int, str]) -> ClientSession:
        index, phone = item
        return sign_in(target, phone, code, client_ip(index))

    with ThreadPoolExecutor(_PARALLEL) as pool:
        return list(pool.map(one, enumerate(phones)))


def prepare_renters(target: Target, sessions: Sequence[ClientSession], operator_token: str) -> None:
    photo = make_photo()
    with ThreadPoolExecutor(_PARALLEL) as pool:
        list(pool.map(lambda session: prepare_renter(target, session, photo), sessions))
    approve_load_clients(target, sessions, operator_token)
    require_approved(target, sessions)


def read_zones(target: Target, session: ClientSession) -> list[MapZone]:
    zones = _checked(
        _client(target, session).get(f"{target.base_url}/map/zones", timeout=_TIMEOUT_S),
        "read zones",
    )
    return list(zones["items"])
