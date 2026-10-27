import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest

from flagman.core import signing
from flagman.payments.domain import (
    CardBrand,
    DeclineReason,
    OperationStatus,
    WebhookSignatureInvalidError,
)
from flagman.payments.fake_provider import (
    FakeProvider,
    FakeSession,
    FakeSessionInvalidError,
    luhn_valid,
)
from flagman.payments.ports import CardBindingStart, OperationRequest, WebhookEventType

NOW = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)
SECRET = b"s" * 32
RETURN_URL = "flagmandrive://payment-methods"


@pytest.fixture
def provider() -> FakeProvider:
    return FakeProvider(SECRET, tolerance_s=300)


def _session(provider: FakeProvider) -> tuple[str, uuid.UUID]:
    binding_id = uuid.uuid4()
    page = provider.start_card_binding(
        CardBindingStart(
            binding_id, RETURN_URL, "http://api.test:8000/", NOW + timedelta(minutes=30)
        )
    )
    assert page.url.startswith("http://api.test:8000/fake-provider/cards?session=")
    return page.url.split("session=", 1)[1], binding_id


def test_page_session_comes_back_as_it_was_signed(provider: FakeProvider) -> None:
    session, binding_id = _session(provider)

    assert provider.read_session(session, NOW) == FakeSession(
        binding_id, RETURN_URL, NOW + timedelta(minutes=30)
    )


def test_page_session_expires(provider: FakeProvider) -> None:
    session, _ = _session(provider)

    with pytest.raises(FakeSessionInvalidError):
        provider.read_session(session, NOW + timedelta(minutes=30))


@pytest.mark.parametrize(
    "damage",
    [
        lambda session: session[:-1] + ("0" if session[-1] != "0" else "1"),
        lambda session: "x" + session,
        lambda session: session.split(".")[0],
        lambda session: "",
    ],
)
def test_forged_session_is_refused(provider: FakeProvider, damage: Callable[[str], str]) -> None:
    session, _ = _session(provider)

    with pytest.raises(FakeSessionInvalidError):
        provider.read_session(damage(session), NOW)


def test_session_of_another_secret_is_refused(provider: FakeProvider) -> None:
    session, _ = _session(FakeProvider(b"o" * 32, tolerance_s=300))

    with pytest.raises(FakeSessionInvalidError):
        provider.read_session(session, NOW)


@pytest.mark.parametrize(
    ("number", "valid"),
    [("4242424242424242", True), ("4242424242424241", False), ("5555555555554444", True)],
)
def test_luhn(number: str, valid: bool) -> None:
    assert luhn_valid(number) is valid


def _fake_session() -> FakeSession:
    return FakeSession(uuid.uuid4(), RETURN_URL, NOW + timedelta(minutes=30))


def test_test_card_binds_with_only_the_last_digits(provider: FakeProvider) -> None:
    fake = _fake_session()

    event = provider.card_entered(fake, "5555555555554444", 12, 2030)

    assert event.type is WebhookEventType.CARD_BOUND
    assert event.binding_id == fake.binding_id
    assert event.card is not None
    assert (event.card.brand, event.card.last4) == (CardBrand.MASTERCARD, "4444")
    assert event.token is not None
    assert "5555555555554444" not in event.token


@pytest.mark.parametrize("number", ["4000000000000002", "4111111111111111"])
def test_other_cards_are_declined(provider: FakeProvider, number: str) -> None:
    event = provider.card_entered(_fake_session(), number, 12, 2030)

    assert event.type is WebhookEventType.CARD_BINDING_DECLINED
    assert event.decline_reason is DeclineReason.DECLINED
    assert event.card is None


def test_signed_webhook_reads_back(provider: FakeProvider) -> None:
    event = provider.card_entered(_fake_session(), "4242424242424242", 1, 2031)

    webhook = provider.signed_webhook(event, NOW)

    assert b"4242424242424242" not in webhook.body
    assert provider.parse_webhook(webhook, NOW) == event


def test_webhook_with_a_changed_body_is_refused(provider: FakeProvider) -> None:
    webhook = provider.signed_webhook(provider.cancelled(_fake_session()), NOW)
    body = json.loads(webhook.body)
    body["type"] = "card_bound"

    with pytest.raises(WebhookSignatureInvalidError):
        provider.parse_webhook(
            type(webhook)(
                webhook.method, webhook.target, webhook.headers, json.dumps(body).encode()
            ),
            NOW,
        )


def test_old_webhook_is_refused(provider: FakeProvider) -> None:
    webhook = provider.signed_webhook(provider.cancelled(_fake_session()), NOW)

    with pytest.raises(WebhookSignatureInvalidError):
        provider.parse_webhook(webhook, NOW + timedelta(seconds=301))


def test_unsigned_webhook_is_refused(provider: FakeProvider) -> None:
    webhook = provider.signed_webhook(provider.cancelled(_fake_session()), NOW)
    headers = {
        name: value for name, value in webhook.headers.items() if name != signing.SIGNATURE_HEADER
    }

    with pytest.raises(WebhookSignatureInvalidError):
        provider.parse_webhook(type(webhook)("POST", webhook.target, headers, webhook.body), NOW)


def test_return_to_names_the_binding() -> None:
    fake = FakeSession(uuid.uuid4(), "https://app.flagman.by/cards?from=profile", NOW)

    assert FakeProvider.return_to(fake) == (
        f"https://app.flagman.by/cards?from=profile&binding_id={fake.binding_id}"
    )


async def test_operations_follow_the_card(provider: FakeProvider) -> None:
    ok = provider.card_entered(_fake_session(), "4242424242424242", 1, 2031).token
    no_funds = provider.card_entered(_fake_session(), "4000000000009995", 1, 2031).token

    held = await provider.hold(OperationRequest("key-1", 3000, token=ok))
    again = await provider.hold(OperationRequest("key-1", 3000, token=ok))
    short = await provider.hold(OperationRequest("key-2", 3000, token=no_funds))
    captured = await provider.capture(OperationRequest("key-3", 2000, parent_ref=held.provider_ref))
    orphan = await provider.void(OperationRequest("key-4", 3000, parent_ref="unknown"))

    assert held.status is OperationStatus.SUCCEEDED
    assert again == held
    assert (short.status, short.decline_reason) == (
        OperationStatus.DECLINED,
        DeclineReason.INSUFFICIENT_FUNDS,
    )
    assert captured.status is OperationStatus.SUCCEEDED
    assert orphan.status is OperationStatus.DECLINED
