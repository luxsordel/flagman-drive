import json
import uuid
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flagman.core.app import create_app
from flagman.core.config import Settings
from flagman.core.ratelimit import Limit
from flagman.fleet import signing
from flagman.payments import api, service
from flagman.payments.domain import CardBrand, CardDetails
from flagman.payments.fake_provider import FakeProvider
from flagman.payments.models import PaymentCard
from flagman.payments.ports import WebhookEvent, WebhookEventType
from flagman.payments.tests.conftest import (
    RETURN_URL,
    WEB_ORIGIN,
    Caller,
    bind_card,
    page_session,
    start_binding,
    submit_card,
)

pytestmark = pytest.mark.integration

CARD_FIELDS = {"id", "brand", "last4", "exp_month", "exp_year", "is_default", "expired"}


def test_card_is_bound_through_the_provider_page(client: TestClient, caller: Caller) -> None:
    started = start_binding(client, caller)
    path = urlsplit(started["page_url"])

    page = client.get(f"{path.path}?{path.query}")
    submitted = submit_card(client, started["page_url"], "4242 4242 4242 4242")
    binding = client.get(f"/payment-cards/bindings/{started['id']}", headers=caller.headers)

    assert page.status_code == HTTPStatus.OK
    assert "form-action 'self' flagmandrive:" in page.headers["content-security-policy"]
    assert page.headers["cache-control"] == "no-store"
    assert submitted.status_code == HTTPStatus.SEE_OTHER
    assert submitted.headers["location"] == f"{RETURN_URL}?binding_id={started['id']}"
    assert binding.json()["status"] == "succeeded"
    card = binding.json()["card"]
    assert set(card) == CARD_FIELDS
    assert (card["brand"], card["last4"], card["exp_month"], card["exp_year"]) == (
        "visa",
        "4242",
        12,
        2030,
    )
    assert card["is_default"] is True
    listed = client.get("/payment-cards", headers=caller.headers)
    assert listed.json() == {"items": [card]}
    assert listed.headers["cache-control"] == "no-store"


def test_binding_is_pending_until_the_card_is_entered(client: TestClient, caller: Caller) -> None:
    started = start_binding(client, caller)

    binding = client.get(f"/payment-cards/bindings/{started['id']}", headers=caller.headers)

    assert binding.json() == {
        "id": started["id"],
        "status": "pending",
        "card": None,
        "decline_reason": None,
    }


async def test_the_token_is_stored_encrypted_and_never_shown(
    client: TestClient, caller: Caller, db: AsyncSession
) -> None:
    bound = bind_card(client, caller)

    async with db.begin():
        sealed = await db.scalar(
            select(PaymentCard.token_ciphertext).where(
                PaymentCard.id == uuid.UUID(bound["card"]["id"])
            )
        )

    assert sealed is not None
    assert b"fake_" not in sealed
    assert "token" not in json.dumps(client.get("/payment-cards", headers=caller.headers).json())


def test_second_card_is_not_the_default(client: TestClient, caller: Caller) -> None:
    first = bind_card(client, caller)["card"]
    second = bind_card(client, caller, "5555555555554444")["card"]

    assert first["is_default"] is True
    assert second["is_default"] is False
    assert second["brand"] == "mastercard"


def test_declined_card(client: TestClient, caller: Caller) -> None:
    binding = bind_card(client, caller, "4000000000000002")

    assert binding["status"] == "declined"
    assert binding["decline_reason"] == "declined"
    assert binding["card"] is None
    assert client.get("/payment-cards", headers=caller.headers).json() == {"items": []}


def test_cancelled_on_the_page(client: TestClient, caller: Caller) -> None:
    started = start_binding(client, caller)

    response = submit_card(client, started["page_url"], number="", action="cancel")
    binding = client.get(f"/payment-cards/bindings/{started['id']}", headers=caller.headers)

    assert response.status_code == HTTPStatus.SEE_OTHER
    assert binding.json()["status"] == "cancelled"


@pytest.mark.parametrize(
    "fields",
    [
        {"number": "4242424242424241"},
        {"number": "4242"},
        {"expiry": "13/30"},
        {"expiry": "01/20"},
        {"cvc": "12"},
    ],
)
def test_invalid_card_input_shows_the_form_again(
    client: TestClient, caller: Caller, fields: dict[str, str]
) -> None:
    started = start_binding(client, caller)

    response = submit_card(client, started["page_url"], **fields)
    binding = client.get(f"/payment-cards/bindings/{started['id']}", headers=caller.headers)

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert 'role="alert"' in response.text
    # The number is not put back into the form: the page holds it no longer than one request.
    assert 'name="number" value' not in response.text
    assert binding.json()["status"] == "pending"


def test_a_damaged_page_link_binds_nothing(client: TestClient, caller: Caller) -> None:
    started = start_binding(client, caller)
    session = page_session(started["page_url"])

    page = client.get("/fake-provider/cards", params={"session": session + "0"})
    submitted = client.post(
        "/fake-provider/cards",
        data={"session": session[:-2], "number": "4242424242424242", "expiry": "12/30"},
    )

    assert page.status_code == HTTPStatus.BAD_REQUEST
    assert submitted.status_code == HTTPStatus.BAD_REQUEST


def test_a_finished_binding_does_not_change(client: TestClient, caller: Caller) -> None:
    started = start_binding(client, caller)
    submit_card(client, started["page_url"])

    submit_card(client, started["page_url"], "5555555555554444")
    submit_card(client, started["page_url"], action="cancel")

    binding = client.get(f"/payment-cards/bindings/{started['id']}", headers=caller.headers)
    assert binding.json()["status"] == "succeeded"
    assert len(client.get("/payment-cards", headers=caller.headers).json()["items"]) == 1


def test_web_client_return_url(client: TestClient, caller: Caller) -> None:
    started = start_binding(client, caller, f"{WEB_ORIGIN}/payment-methods")

    response = submit_card(client, started["page_url"])

    assert response.headers["location"] == (
        f"{WEB_ORIGIN}/payment-methods?binding_id={started['id']}"
    )


@pytest.mark.parametrize(
    "return_url", ["https://evil.example/cards", "javascript:alert(1)", "http://localhost:9999/"]
)
def test_return_url_elsewhere_is_refused(
    client: TestClient, caller: Caller, return_url: str
) -> None:
    response = client.post(
        "/payment-cards/bindings", json={"return_url": return_url}, headers=caller.headers
    )

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert response.json()["type"] == "urn:flagman:problem:return-url-not-allowed"


def test_unknown_fields_are_refused(client: TestClient, caller: Caller) -> None:
    response = client.post(
        "/payment-cards/bindings",
        json={"return_url": RETURN_URL, "user_id": str(uuid.uuid4())},
        headers=caller.headers,
    )

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_others_cards_and_bindings_are_not_found(
    client: TestClient, caller: Caller, other: Caller
) -> None:
    binding = bind_card(client, caller)
    card_id = binding["card"]["id"]

    responses = [
        client.get(f"/payment-cards/bindings/{binding['id']}", headers=other.headers),
        client.delete(f"/payment-cards/{card_id}", headers=other.headers),
        client.post(f"/payment-cards/{card_id}/default", headers=other.headers),
    ]

    assert [response.status_code for response in responses] == [HTTPStatus.NOT_FOUND] * 3
    assert responses[0].json()["type"] == "urn:flagman:problem:card-binding-not-found"
    assert responses[1].json()["type"] == "urn:flagman:problem:payment-card-not-found"
    assert client.get("/payment-cards", headers=other.headers).json() == {"items": []}
    assert len(client.get("/payment-cards", headers=caller.headers).json()["items"]) == 1


def test_removing_the_default_card_hands_the_role_on(client: TestClient, caller: Caller) -> None:
    first = bind_card(client, caller)["card"]
    second = bind_card(client, caller, "5555555555554444")["card"]

    removed = client.delete(f"/payment-cards/{first['id']}", headers=caller.headers)
    cards = client.get("/payment-cards", headers=caller.headers).json()["items"]

    assert removed.status_code == HTTPStatus.NO_CONTENT
    assert [(card["id"], card["is_default"]) for card in cards] == [(second["id"], True)]
    again = client.delete(f"/payment-cards/{first['id']}", headers=caller.headers)
    assert again.status_code == HTTPStatus.NOT_FOUND


def test_choosing_the_default_card(client: TestClient, caller: Caller) -> None:
    first = bind_card(client, caller)["card"]
    second = bind_card(client, caller, "5555555555554444")["card"]

    chosen = client.post(f"/payment-cards/{second['id']}/default", headers=caller.headers)
    cards = client.get("/payment-cards", headers=caller.headers).json()["items"]

    assert chosen.json()["is_default"] is True
    assert {card["id"]: card["is_default"] for card in cards} == {
        first["id"]: False,
        second["id"]: True,
    }


def test_binding_is_rate_limited(
    client: TestClient, caller: Caller, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(api, "BINDING_LIMIT", Limit(f"test-binding-{uuid.uuid4()}", 2, 60))

    statuses = [
        client.post(
            "/payment-cards/bindings", json={"return_url": RETURN_URL}, headers=caller.headers
        ).status_code
        for _ in range(3)
    ]

    assert statuses == [HTTPStatus.CREATED, HTTPStatus.CREATED, HTTPStatus.TOO_MANY_REQUESTS]


def test_sign_in_is_required(client: TestClient) -> None:
    assert client.get("/payment-cards").status_code == HTTPStatus.UNAUTHORIZED
    assert client.post("/payment-cards/bindings", json={"return_url": RETURN_URL}).status_code == (
        HTTPStatus.UNAUTHORIZED
    )


def test_without_a_provider_payments_are_unavailable(
    integration_settings: Settings, payments_settings: Settings
) -> None:
    caller = Caller(integration_settings, uuid.uuid4())
    with TestClient(create_app(integration_settings), base_url="https://testserver") as client:
        response = client.post(
            "/payment-cards/bindings", json={"return_url": RETURN_URL}, headers=caller.headers
        )
        page = client.get("/fake-provider/cards", params={"session": "x"})

    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert response.json()["type"] == "urn:flagman:problem:payments-unavailable"
    assert page.status_code == HTTPStatus.SERVICE_UNAVAILABLE


# The webhook over HTTP, as a real provider calls it.


def _post_webhook(
    client: TestClient, settings: Settings, event: WebhookEvent, now: datetime | None = None
) -> tuple[int, object]:
    provider = service.fake_provider(settings)
    webhook = provider.signed_webhook(event, now or datetime.now(UTC))
    response = client.post(
        webhook.target.decode(), content=webhook.body, headers=dict(webhook.headers)
    )
    return response.status_code, response.json()


def _bound_event(binding_id: str, event_id: str) -> WebhookEvent:
    return WebhookEvent(
        event_id,
        WebhookEventType.CARD_BOUND,
        uuid.UUID(binding_id),
        card=CardDetails(CardBrand.VISA, "4242", 12, 2030),
        token="fake_ok_" + uuid.uuid4().hex,
    )


def test_webhook_applies_once(
    client: TestClient, caller: Caller, payments_settings: Settings
) -> None:
    started = start_binding(client, caller)
    event = _bound_event(started["id"], uuid.uuid4().hex)

    first = _post_webhook(client, payments_settings, event)
    repeated = _post_webhook(client, payments_settings, event)

    assert first == (HTTPStatus.OK, {"applied": True})
    assert repeated == (HTTPStatus.OK, {"applied": False})
    assert len(client.get("/payment-cards", headers=caller.headers).json()["items"]) == 1


def test_webhook_for_a_finished_binding_changes_nothing(
    client: TestClient, caller: Caller, payments_settings: Settings
) -> None:
    started = start_binding(client, caller)
    _post_webhook(client, payments_settings, _bound_event(started["id"], uuid.uuid4().hex))

    late = _post_webhook(client, payments_settings, _bound_event(started["id"], uuid.uuid4().hex))

    assert late == (HTTPStatus.OK, {"applied": False})
    assert len(client.get("/payment-cards", headers=caller.headers).json()["items"]) == 1


def test_webhook_with_a_bad_signature_is_refused(
    client: TestClient, caller: Caller, payments_settings: Settings
) -> None:
    started = start_binding(client, caller)
    provider = service.fake_provider(payments_settings)
    webhook = provider.signed_webhook(
        _bound_event(started["id"], uuid.uuid4().hex), datetime.now(UTC)
    )
    headers = {**webhook.headers, signing.SIGNATURE_HEADER: "sha256=" + "0" * 64}

    response = client.post(webhook.target.decode(), content=webhook.body, headers=headers)

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert response.json()["type"] == "urn:flagman:problem:webhook-signature-invalid"
    assert client.get("/payment-cards", headers=caller.headers).json() == {"items": []}


def test_webhook_out_of_the_time_window_is_refused(
    client: TestClient, caller: Caller, payments_settings: Settings
) -> None:
    started = start_binding(client, caller)

    status, _ = _post_webhook(
        client,
        payments_settings,
        _bound_event(started["id"], uuid.uuid4().hex),
        datetime.now(UTC) - timedelta(minutes=10),
    )

    assert status == HTTPStatus.UNAUTHORIZED


def test_webhook_signed_with_another_secret_is_refused(
    client: TestClient, caller: Caller, payments_settings: Settings
) -> None:
    started = start_binding(client, caller)
    webhook = FakeProvider(b"f" * 32, 300).signed_webhook(
        _bound_event(started["id"], uuid.uuid4().hex), datetime.now(UTC)
    )

    response = client.post(
        webhook.target.decode(), content=webhook.body, headers=dict(webhook.headers)
    )

    assert response.status_code == HTTPStatus.UNAUTHORIZED


def test_webhook_with_a_malformed_body_is_refused(
    client: TestClient, payments_settings: Settings
) -> None:
    provider = service.fake_provider(payments_settings)
    now = datetime.now(UTC)
    body = b'{"event_id": "x", "type": "card_bound"}'
    timestamp = int(now.timestamp())
    target = b"/payments/webhooks/fake"
    headers = {
        signing.TIMESTAMP_HEADER: str(timestamp),
        signing.SIGNATURE_HEADER: signing.sign(provider.secret, timestamp, "POST", target, body),
    }

    response = client.post(target.decode(), content=body, headers=headers)

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_webhook_is_not_in_the_api_schema(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]

    assert "/payments/webhooks/fake" not in paths
    assert "/fake-provider/cards" not in paths
    assert "/payment-cards/bindings" in paths
