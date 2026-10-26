import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from flagman.payments.domain import (
    CardBinding,
    CardBindingStatus,
    CardBrand,
    CardDetails,
    CardTokenUnreadableError,
    OperationKind,
    OperationStatus,
    PaymentCard,
    PaymentOperation,
    PaymentOperationInvalidError,
    ReturnUrlNotAllowedError,
    check_amount,
    check_child,
    check_idempotency_key,
    check_return_url,
    decrypt_card_token,
    default_after_removal,
    encrypt_card_token,
)

NOW = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)
WEB = frozenset({"https://app.flagman.by", "http://localhost:8081"})
KEY = b"k" * 32


@pytest.mark.parametrize(
    "url",
    [
        "flagmandrive://payment-methods",
        "flagmandrive://payment-methods?from=profile",
        "https://app.flagman.by/payment-methods",
        "http://localhost:8081/payment-methods",
    ],
)
def test_return_url_to_our_app_or_web_client(url: str) -> None:
    assert check_return_url(url, WEB, allow_expo_go=False) == url


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/payment-methods",
        "https://app.flagman.by.evil.example/",
        "https://user@app.flagman.by/",
        "http://app.flagman.by/payment-methods",
        "javascript:alert(1)",
        "exp://192.168.1.10:8081/--/payment-methods",
        "",
        "flagmandrive://" + "a" * 600,
        "flagmandrive://платёж",
    ],
)
def test_return_url_elsewhere_is_refused(url: str) -> None:
    with pytest.raises(ReturnUrlNotAllowedError):
        check_return_url(url, WEB, allow_expo_go=False)


def test_expo_go_links_only_when_allowed() -> None:
    url = "exp://192.168.1.10:8081/--/payment-methods"

    assert check_return_url(url, WEB, allow_expo_go=True) == url


@pytest.mark.parametrize(
    ("month", "year", "expired"),
    [(10, 2026, False), (9, 2026, True), (12, 2026, False), (12, 2025, True), (1, 2030, False)],
)
def test_card_works_through_the_last_day_of_its_month(month: int, year: int, expired: bool) -> None:
    card = CardDetails(CardBrand.VISA, "4242", month, year)

    assert card.is_expired(NOW) is expired


def test_december_card_expires_with_the_new_year() -> None:
    card = CardDetails(CardBrand.VISA, "4242", 12, 2026)

    assert not card.is_expired(datetime(2026, 12, 31, 23, 59, tzinfo=UTC))
    assert card.is_expired(datetime(2027, 1, 1, tzinfo=UTC))


@pytest.mark.parametrize(
    ("last4", "month", "year"),
    [("424", 1, 2030), ("42a2", 1, 2030), ("4242", 13, 2030), ("4242", 1, 30)],
)
def test_card_details_are_checked(last4: str, month: int, year: int) -> None:
    with pytest.raises(ValueError, match=r"last4|exp_"):
        CardDetails(CardBrand.VISA, last4, month, year)


def test_pending_binding_expires() -> None:
    binding = CardBinding(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        stored_status=CardBindingStatus.PENDING,
        return_url="flagmandrive://payment-methods",
        card_id=None,
        decline_reason=None,
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=30),
    )

    assert binding.status(NOW) is CardBindingStatus.PENDING
    assert binding.status(NOW + timedelta(minutes=30)) is CardBindingStatus.EXPIRED
    done = replace(binding, stored_status=CardBindingStatus.CANCELLED)
    assert done.status(NOW + timedelta(days=1)) is CardBindingStatus.CANCELLED


def _operation(
    kind: OperationKind, amount: int, status: OperationStatus, **kwargs: object
) -> PaymentOperation:
    values: dict[str, object] = {
        "id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "card_id": uuid.uuid4(),
        "kind": kind,
        "amount": amount,
        "parent_id": None,
        "idempotency_key": uuid.uuid4().hex,
        "status": status,
        "provider_ref": "ref",
        "decline_reason": None,
        "created_at": NOW,
        **kwargs,
    }
    return PaymentOperation(**values)  # type: ignore[arg-type]


HOLD = _operation(OperationKind.HOLD, 3000, OperationStatus.SUCCEEDED)


def test_capture_up_to_the_hold() -> None:
    check_child(HOLD, OperationKind.CAPTURE, 3000, [])
    check_child(HOLD, OperationKind.CAPTURE, 1200, [])

    with pytest.raises(PaymentOperationInvalidError):
        check_child(HOLD, OperationKind.CAPTURE, 3001, [])


@pytest.mark.parametrize(
    "status", [OperationStatus.SUCCEEDED, OperationStatus.PENDING, OperationStatus.FAILED]
)
@pytest.mark.parametrize("taken", [OperationKind.CAPTURE, OperationKind.VOID])
@pytest.mark.parametrize("kind", [OperationKind.CAPTURE, OperationKind.VOID])
def test_a_hold_is_captured_or_voided_once(
    kind: OperationKind, taken: OperationKind, status: OperationStatus
) -> None:
    sibling = _operation(taken, 3000, status, parent_id=HOLD.id)

    with pytest.raises(PaymentOperationInvalidError):
        check_child(HOLD, kind, 3000, [sibling])


def test_a_declined_capture_does_not_take_the_hold() -> None:
    declined = _operation(OperationKind.CAPTURE, 3000, OperationStatus.DECLINED, parent_id=HOLD.id)

    check_child(HOLD, OperationKind.CAPTURE, 3000, [declined])


@pytest.mark.parametrize(
    "status", [OperationStatus.DECLINED, OperationStatus.PENDING, OperationStatus.FAILED]
)
def test_only_a_succeeded_operation_has_children(status: OperationStatus) -> None:
    with pytest.raises(PaymentOperationInvalidError):
        check_child(replace(HOLD, status=status), OperationKind.VOID, 3000, [])


def test_refunds_stay_within_what_was_taken() -> None:
    capture = _operation(OperationKind.CAPTURE, 2000, OperationStatus.SUCCEEDED, parent_id=HOLD.id)
    refunded = [
        _operation(OperationKind.REFUND, 500, OperationStatus.SUCCEEDED, parent_id=capture.id),
        _operation(OperationKind.REFUND, 700, OperationStatus.PENDING, parent_id=capture.id),
        _operation(OperationKind.REFUND, 900, OperationStatus.DECLINED, parent_id=capture.id),
    ]

    check_child(capture, OperationKind.REFUND, 800, refunded)
    with pytest.raises(PaymentOperationInvalidError):
        check_child(capture, OperationKind.REFUND, 801, refunded)


@pytest.mark.parametrize(
    ("parent_kind", "kind"),
    [
        (OperationKind.HOLD, OperationKind.REFUND),
        (OperationKind.CHARGE, OperationKind.CAPTURE),
        (OperationKind.CAPTURE, OperationKind.VOID),
    ],
)
def test_children_fit_their_parent_kind(parent_kind: OperationKind, kind: OperationKind) -> None:
    parent = _operation(parent_kind, 3000, OperationStatus.SUCCEEDED)

    with pytest.raises(PaymentOperationInvalidError):
        check_child(parent, kind, 100, [])


@pytest.mark.parametrize("amount", [0, -1, True])
def test_amounts_are_positive_kopecks(amount: int) -> None:
    with pytest.raises(ValueError, match="positive"):
        check_amount(amount)


@pytest.mark.parametrize("key", ["", "a" * 129, "ключ", "a\nb"])
def test_idempotency_keys_are_short_printable_ascii(key: str) -> None:
    with pytest.raises(ValueError, match="idempotency"):
        check_idempotency_key(key)


def _card(created: datetime, *, default: bool = False) -> PaymentCard:
    return PaymentCard(
        uuid.uuid4(), uuid.uuid4(), CardDetails(CardBrand.VISA, "4242", 1, 2030), default, created
    )


def test_the_newest_other_card_becomes_the_default() -> None:
    oldest, default, newest = (
        _card(NOW),
        _card(NOW + timedelta(1), default=True),
        _card(NOW + timedelta(2)),
    )

    assert default_after_removal([oldest, default, newest], default.id) == newest
    assert default_after_removal([default], default.id) is None


def test_card_token_decrypts_only_for_its_card() -> None:
    card_id = uuid.uuid4()
    sealed = encrypt_card_token("fake_ok_secret", KEY, card_id)

    assert b"fake_ok_secret" not in sealed
    assert decrypt_card_token(sealed, KEY, card_id) == "fake_ok_secret"
    with pytest.raises(CardTokenUnreadableError):
        decrypt_card_token(sealed, KEY, uuid.uuid4())
    with pytest.raises(CardTokenUnreadableError):
        decrypt_card_token(sealed, b"x" * 32, card_id)
