"""The card page of the fake payment provider (flagman.payments.fake_provider).

It stands in for the provider's own site: a real provider's page is not ours, so the card number
it takes never reaches the rest of the API. Here it is read only to pick the test card's behavior,
and only the last four digits leave this module, in the signed webhook.
"""

import base64
import hashlib
import html
import re
from datetime import UTC, datetime
from http import HTTPStatus
from typing import Annotated
from urllib.parse import parse_qs, urlsplit

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from flagman.core.db import get_session
from flagman.core.resources import Resources, get_resources
from flagman.payments import service
from flagman.payments.fake_provider import (
    PAGE_PATH,
    TEST_CARDS,
    FakeProvider,
    FakeSession,
    FakeSessionInvalidError,
    luhn_valid,
)
from flagman.payments.ports import WebhookEvent

# The form is a handful of short fields.
MAX_FORM_BYTES = 4 * 1024
# [0-9] rather than \d: \d also matches non-ASCII digits.
_NUMBER = re.compile(r"[0-9]{13,19}")
_EXPIRY = re.compile(r"(0[1-9]|1[0-2])/([0-9]{2})")
_CVC = re.compile(r"[0-9]{3,4}")

_STYLE = (
    "body{font-family:system-ui,sans-serif;max-width:26rem;margin:2rem auto;padding:0 1rem;"
    "color:#1b1f24;background:#fff}"
    "label{display:block;margin:.75rem 0 .25rem}"
    "input{width:100%;box-sizing:border-box;padding:.5rem;font-size:1rem}"
    "button{margin:1rem .5rem 0 0;padding:.5rem 1rem;font-size:1rem}"
    ".error{color:#b42318}.hint{color:#57606a;font-size:.875rem}"
)
_STYLE_HASH = base64.b64encode(hashlib.sha256(_STYLE.encode()).digest()).decode()

router = APIRouter(include_in_schema=False)


def get_fake_provider(resources: Annotated[Resources, Depends(get_resources)]) -> FakeProvider:
    return service.fake_provider(resources.settings)


Provider = Annotated[FakeProvider, Depends(get_fake_provider)]


def _csp(return_url: str | None) -> str:
    # The form posts here and the answer redirects to the app: form-action covers both.
    targets = "'self'"
    if return_url is not None:
        parts = urlsplit(return_url)
        targets += (
            f" {parts.scheme}://{parts.netloc}"
            if parts.scheme in ("http", "https")
            else f" {parts.scheme}:"
        )
    return (
        f"default-src 'none'; style-src 'sha256-{_STYLE_HASH}'; form-action {targets}; "
        "frame-ancestors 'none'; base-uri 'none'"
    )


def _page(body: str, status: int, return_url: str | None = None) -> HTMLResponse:
    document = (
        '<!doctype html><html lang="ru"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>Test payment provider</title><style>{_STYLE}</style></head>"
        f"<body>{body}</body></html>"
    )
    return HTMLResponse(
        document,
        status_code=status,
        headers={"Content-Security-Policy": _csp(return_url), "Cache-Control": "no-store"},
    )


def _invalid_link() -> HTMLResponse:
    return _page(
        "<h1>Ссылка недействительна</h1><p>Ссылка устарела или повреждена. Вернитесь в приложение "
        "и начните привязку карты заново.</p><p class=hint>The link has expired or is damaged.</p>",
        HTTPStatus.BAD_REQUEST,
    )


def _form(session: str, fake: FakeSession, error: str | None = None) -> HTMLResponse:
    cards = "".join(
        f"<li><code>{number[:4]} {number[4:8]} {number[8:12]} {number[12:]}</code> — "
        f"{'привязка отклонена' if card.behavior is None else card.behavior}</li>"
        for number, card in TEST_CARDS.items()
    )
    message = f'<p class="error" role="alert">{html.escape(error)}</p>' if error else ""
    body = (
        "<h1>Привязка карты</h1>"
        "<p class=hint>Тестовый платёжный провайдер FLAGMAN Drive: деньги не списываются. "
        "Test payment provider, no money moves.</p>"
        f"{message}"
        f'<form method="post" action="{PAGE_PATH}">'
        f'<input type="hidden" name="session" value="{html.escape(session)}">'
        '<label for="number">Номер карты / Card number</label>'
        '<input id="number" name="number" inputmode="numeric" autocomplete="cc-number" required>'
        '<label for="expiry">Срок действия, месяц/год / Expiry MM/YY</label>'
        '<input id="expiry" name="expiry" placeholder="12/30" autocomplete="cc-exp" required>'
        '<label for="cvc">CVC</label>'
        '<input id="cvc" name="cvc" inputmode="numeric" autocomplete="cc-csc" required>'
        '<button type="submit" name="action" value="bind">Привязать / Bind</button>'
        '<button type="submit" name="action" value="cancel" formnovalidate>'
        "Отмена / Cancel</button>"
        "</form>"
        f"<p class=hint>Тестовые карты, любой будущий срок и CVC:</p><ul class=hint>{cards}</ul>"
    )
    return _page(
        body, HTTPStatus.OK if error is None else HTTPStatus.UNPROCESSABLE_ENTITY, fake.return_url
    )


@router.get(PAGE_PATH)
async def show_card_page(
    provider: Provider, session: Annotated[str, Query(max_length=2048)]
) -> Response:
    try:
        fake = provider.read_session(session, datetime.now(UTC))
    except FakeSessionInvalidError:
        return _invalid_link()
    return _form(session, fake)


def _card_error(number: str, expiry: str, cvc: str, now: datetime) -> str | None:
    if not _NUMBER.fullmatch(number) or not luhn_valid(number):
        return "Проверьте номер карты / Check the card number"
    match = _EXPIRY.fullmatch(expiry)
    if match is None:
        return "Срок действия: месяц/год, например 12/30 / Expiry as MM/YY"
    month, year = int(match[1]), 2000 + int(match[2])
    if (year, month) < (now.year, now.month):
        return "Срок действия карты истёк / The card has expired"
    if not _CVC.fullmatch(cvc):
        return "Проверьте CVC / Check the CVC"
    return None


@router.post(PAGE_PATH)
async def submit_card_page(
    request: Request,
    provider: Provider,
    resources: Annotated[Resources, Depends(get_resources)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    raw = await request.body()
    if len(raw) > MAX_FORM_BYTES:
        return _invalid_link()
    form = {key: values[0] for key, values in parse_qs(raw.decode("utf-8", "replace")).items()}
    session = form.get("session", "")
    now = datetime.now(UTC)
    try:
        fake = provider.read_session(session, now)
    except FakeSessionInvalidError:
        return _invalid_link()

    event: WebhookEvent
    if form.get("action") == "cancel":
        event = provider.cancelled(fake)
    else:
        number = form.get("number", "").replace(" ", "")
        expiry = form.get("expiry", "").replace(" ", "")
        error = _card_error(number, expiry, form.get("cvc", ""), now)
        if error is not None:
            return _form(session, fake, error)
        match = _EXPIRY.fullmatch(expiry)
        if match is None:
            raise RuntimeError("the expiry was checked above")
        event = provider.card_entered(fake, number, int(match[1]), 2000 + int(match[2]))

    # A real provider calls the webhook over HTTP; the fake one hands the same signed request to
    # the same code in process, so the signature check runs exactly as it would.
    gw = service.gateway(resources.settings)
    await service.receive_webhook(db, gw, provider.signed_webhook(event, now), now)
    return RedirectResponse(provider.return_to(fake), status_code=HTTPStatus.SEE_OTHER)
