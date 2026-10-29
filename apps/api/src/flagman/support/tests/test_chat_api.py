import asyncio
import uuid
from http import HTTPStatus
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from flagman.core.config import Settings
from flagman.core.db import create_engine
from flagman.core.photos import MAX_UPLOAD_BYTES
from flagman.fleet.tests.conftest import Staff, add_car, bearer, staff_token
from flagman.identity.domain import StaffRole
from flagman.rentals.tests.conftest import CAR_POINT, FakeStorage, Person, photo, problem_type
from flagman.support.service import MESSAGE_LIMIT, STAFF_MESSAGE_LIMIT, UPLOAD_LIMIT
from flagman.support.tests.conftest import (
    audit_actions,
    exhaust,
    name_staff,
    reply,
    send_photo,
    write,
)

pytestmark = pytest.mark.integration


def _messages(person: Person, after: int = 0) -> dict[str, object]:
    response = person.client.get(f"/support/messages?after={after}", headers=person.headers)
    assert response.status_code == HTTPStatus.OK
    body: dict[str, object] = response.json()
    return body


def _conversation_id(person: Person) -> str:
    return str(_messages(person)["conversation_id"])


def test_a_client_without_messages_has_no_conversation(person: Person) -> None:
    assert _messages(person) == {"conversation_id": None, "status": None, "items": []}


def _staff_conversation(client: TestClient, staff: Staff, conversation: str) -> Any:
    return client.get(f"/admin/support/conversations/{conversation}", headers=staff.headers)


def test_a_whole_conversation(person: Person, operator: Staff, app_client: TestClient) -> None:
    name_staff(operator, "Anna")
    assert write(person, "  The car smells of smoke  ").status_code == HTTPStatus.CREATED
    conversation = _conversation_id(person)

    mine = _staff_conversation(app_client, operator, conversation).json()
    assert mine["unread"] == 1
    assert mine["user_id"] == str(person.id)

    staff_view = app_client.get(
        f"/admin/support/conversations/{conversation}/messages", headers=operator.headers
    ).json()
    assert [item["text"] for item in staff_view["items"]] == ["The car smells of smoke"]
    first_seq = staff_view["items"][0]["seq"]
    taken = app_client.post(
        f"/admin/support/conversations/{conversation}/take", headers=operator.headers
    )
    assert taken.json()["assigned_staff_id"] == str(operator.id)
    answer = reply(app_client, operator, conversation, "We will clean it, sorry", first_seq)
    assert answer.status_code == HTTPStatus.CREATED
    assert (answer.json()["staff_id"], answer.json()["staff_name"]) == (str(operator.id), "Anna")

    assert person.client.get("/support/unread", headers=person.headers).json() == {"messages": 1}
    newer = _messages(person, after=first_seq)["items"]
    assert isinstance(newer, list)
    assert [item["text"] for item in newer] == ["We will clean it, sorry"]
    # The client sees "FLAGMAN Support" and the operator's name, not their staff account.
    assert newer[0]["author"] == "staff"
    assert newer[0]["staff_name"] == "Anna"
    assert "staff_id" not in newer[0]
    assert person.client.get("/support/unread", headers=person.headers).json() == {"messages": 0}

    closed = app_client.post(
        f"/admin/support/conversations/{conversation}/close", headers=operator.headers
    )
    assert closed.json()["status"] == "closed"
    assert audit_actions(person.settings, conversation) == [
        "support.conversation_taken",
        "support.message_sent",
        "support.conversation_closed",
    ]

    # After closing, the next message opens a new conversation.
    assert write(person, "One more question").status_code == HTTPStatus.CREATED
    assert _conversation_id(person) != conversation


def test_a_conversation_is_attached_to_the_clients_rental(
    integration_settings: Settings, person: Person, operator: Staff, app_client: TestClient
) -> None:
    car = add_car(integration_settings, CAR_POINT)
    rental = person.reserve(car).json()["id"]

    assert write(person, "Where do I park?", rental).status_code == HTTPStatus.CREATED

    conversation = _conversation_id(person)
    view = _staff_conversation(app_client, operator, conversation).json()
    assert view["rental_id"] == rental
    assert (view["rental_status"], view["car_id"]) == ("reserved", car)
    assert view["car_plate"]


def test_a_conversation_without_a_rental_has_no_car(
    person: Person, operator: Staff, app_client: TestClient
) -> None:
    write(person, "A question about tariffs")

    view = _staff_conversation(app_client, operator, _conversation_id(person)).json()

    assert (view["rental_id"], view["rental_status"], view["car_id"], view["car_plate"]) == (
        None,
        None,
        None,
        None,
    )


def test_another_clients_rental_cannot_be_attached(
    integration_settings: Settings, person: Person, app_client: TestClient
) -> None:
    other = Person(integration_settings, app_client)
    rental = other.reserve(add_car(integration_settings, CAR_POINT)).json()["id"]

    response = write(person, "Hello", rental)

    assert response.status_code == HTTPStatus.NOT_FOUND
    assert problem_type(response) == "rental-not-found"


def test_a_client_sees_only_their_own_conversation(
    integration_settings: Settings, person: Person, app_client: TestClient
) -> None:
    write(person, "My secret question")
    other = Person(integration_settings, app_client)

    assert _messages(other) == {"conversation_id": None, "status": None, "items": []}


def test_a_photo_goes_to_storage_and_comes_back_by_a_short_link(
    integration_settings: Settings,
    person: Person,
    operator: Staff,
    app_client: TestClient,
    storage: FakeStorage,
) -> None:
    sent = send_photo(person, photo())
    assert sent.status_code == HTTPStatus.CREATED
    message = sent.json()
    assert message["kind"] == "photo"
    (key,) = storage.objects
    assert key.startswith("support/messages/")
    assert str(person.id) not in key

    own = person.client.get(f"/support/messages/{message['id']}/photo-url", headers=person.headers)
    assert own.status_code == HTTPStatus.OK
    assert own.headers["Cache-Control"] == "no-store"
    assert own.json()["expires_in"] == 300

    other = Person(integration_settings, app_client)
    stolen = other.client.get(f"/support/messages/{message['id']}/photo-url", headers=other.headers)
    assert stolen.status_code == HTTPStatus.NOT_FOUND

    conversation = _conversation_id(person)
    staff_link = app_client.get(
        f"/admin/support/conversations/{conversation}/messages/{message['id']}/photo-url",
        headers=operator.headers,
    )
    assert staff_link.status_code == HTTPStatus.OK
    assert audit_actions(integration_settings, conversation) == ["support.photo_viewed"]


def test_a_file_that_is_not_a_photo_is_refused(person: Person, storage: FakeStorage) -> None:
    response = send_photo(person, b"%PDF-1.7 not an image")

    assert response.status_code == HTTPStatus.UNSUPPORTED_MEDIA_TYPE
    assert problem_type(response) == "support-photo-type-unsupported"
    assert storage.objects == {}
    assert _messages(person)["conversation_id"] is None


def test_messages_are_rate_limited(person: Person) -> None:
    for index in range(MESSAGE_LIMIT.max_hits):
        assert write(person, f"message {index}").status_code == HTTPStatus.CREATED

    response = write(person, "one too many")

    assert response.status_code == HTTPStatus.TOO_MANY_REQUESTS
    assert "Retry-After" in response.headers


def test_photo_uploads_are_rate_limited(person: Person, storage: FakeStorage) -> None:
    exhaust(person.settings, UPLOAD_LIMIT, str(person.id))

    response = send_photo(person, photo())

    assert response.status_code == HTTPStatus.TOO_MANY_REQUESTS
    assert storage.objects == {}


def _unread(person: Person) -> int:
    body = person.client.get("/support/unread", headers=person.headers).json()
    return int(body["messages"])


def test_reading_a_page_clears_only_what_it_showed(
    person: Person, operator: Staff, app_client: TestClient
) -> None:
    write(person, "Help")
    conversation = _conversation_id(person)
    for answer in ("One", "Two", "Three"):
        reply(app_client, operator, conversation, answer, 0)

    page = person.client.get("/support/messages?limit=2", headers=person.headers).json()

    assert [item["text"] for item in page["items"]] == ["Help", "One"]
    assert _unread(person) == 2


async def _wait_for_a_lock_waiter(watcher: AsyncConnection) -> None:
    for _ in range(200):
        waiting = await watcher.scalar(
            text(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE datname = current_database() AND wait_event_type = 'Lock'"
            )
        )
        # pg_stat_activity is a snapshot per transaction: a fresh one on every try.
        await watcher.rollback()
        if waiting:
            return
        await asyncio.sleep(0.05)
    raise AssertionError("the client's read never waited for the answer being saved")


def test_an_answer_saved_while_the_client_reads_stays_unread(
    integration_settings: Settings, person: Person, operator: Staff, app_client: TestClient
) -> None:
    write(person, "Help")
    conversation = uuid.UUID(_conversation_id(person))
    reply(app_client, operator, str(conversation), "Looking", 0)

    async def race() -> int:
        engine = create_engine(integration_settings)
        try:
            async with engine.connect() as writer, engine.connect() as watcher:
                # What the operator's reply does, stopped just before its commit.
                await writer.execute(
                    text("SELECT 1 FROM support_conversations WHERE id = :id FOR UPDATE"),
                    {"id": conversation},
                )
                await writer.execute(
                    text(
                        "INSERT INTO support_messages "
                        "(id, conversation_id, author, staff_id, kind, body, created_at) "
                        "VALUES (:mid, :id, 'staff', :staff, 'text', 'Found it', now())"
                    ),
                    {"mid": uuid.uuid4(), "id": conversation, "staff": operator.id},
                )
                await writer.execute(
                    text(
                        "UPDATE support_conversations SET client_unread = client_unread + 1 "
                        "WHERE id = :id"
                    ),
                    {"id": conversation},
                )
                reading = asyncio.create_task(
                    asyncio.to_thread(
                        person.client.get, "/support/messages", headers=person.headers
                    )
                )
                await _wait_for_a_lock_waiter(watcher)
                await writer.commit()
                response = await reading
        finally:
            await engine.dispose()
        return [item["text"] for item in response.json()["items"]].count("Found it")

    seen = asyncio.run(race())

    # The read started before "Found it" was saved, so the badge must still count it.
    assert seen == 0
    assert _unread(person) == 1


def test_another_operator_cannot_answer_a_taken_conversation(
    integration_settings: Settings, person: Person, operator: Staff, app_client: TestClient
) -> None:
    write(person, "Help")
    conversation = _conversation_id(person)
    app_client.post(f"/admin/support/conversations/{conversation}/take", headers=operator.headers)
    colleague = Staff(integration_settings, StaffRole.OPERATOR)
    admin = Staff(integration_settings, StaffRole.ADMIN)
    close_url = f"/admin/support/conversations/{conversation}/close"

    refused = reply(app_client, colleague, conversation, "Me too", 0)
    not_closed = app_client.post(close_url, headers=colleague.headers)
    allowed = reply(app_client, admin, conversation, "Admin here", 0)

    assert refused.status_code == HTTPStatus.CONFLICT
    assert problem_type(refused) == "conversation-taken"
    assert not_closed.status_code == HTTPStatus.CONFLICT
    assert problem_type(not_closed) == "conversation-taken"
    assert allowed.status_code == HTTPStatus.CREATED


def test_a_closed_conversation_takes_no_answer(
    person: Person, operator: Staff, app_client: TestClient
) -> None:
    write(person, "Thanks, all good")
    conversation = _conversation_id(person)
    app_client.post(f"/admin/support/conversations/{conversation}/close", headers=operator.headers)

    answer = reply(app_client, operator, conversation, "Anything else?", 0)
    again = app_client.post(
        f"/admin/support/conversations/{conversation}/close", headers=operator.headers
    )

    for response in (answer, again):
        assert response.status_code == HTTPStatus.CONFLICT
        assert problem_type(response) == "conversation-closed"


def test_an_answer_keeps_unseen_client_messages_unread(
    person: Person, operator: Staff, app_client: TestClient
) -> None:
    write(person, "Help")
    conversation = _conversation_id(person)
    shown = app_client.get(
        f"/admin/support/conversations/{conversation}/messages", headers=operator.headers
    ).json()["items"]
    # Written after the operator's last poll: they answer without having seen it.
    write(person, "The doors do not open")

    reply(app_client, operator, conversation, "Looking into it", shown[-1]["seq"])

    assert _staff_conversation(app_client, operator, conversation).json()["unread"] == 1


def test_an_operator_without_a_name_is_just_support(
    person: Person, operator: Staff, app_client: TestClient
) -> None:
    write(person, "Help")
    reply(app_client, operator, _conversation_id(person), "Here", 0)

    items = person.client.get("/support/messages", headers=person.headers).json()["items"]

    assert [(item["author"], item["staff_name"]) for item in items] == [
        ("client", None),
        ("staff", None),
    ]


def test_staff_answers_are_rate_limited(
    person: Person, operator: Staff, app_client: TestClient
) -> None:
    write(person, "Help")
    exhaust(person.settings, STAFF_MESSAGE_LIMIT, str(operator.id))

    response = reply(app_client, operator, _conversation_id(person), "One too many", 0)

    assert response.status_code == HTTPStatus.TOO_MANY_REQUESTS


def test_a_photo_over_the_size_limit_is_refused(person: Person, storage: FakeStorage) -> None:
    response = send_photo(person, b"\xff" * (MAX_UPLOAD_BYTES + 1))

    assert response.status_code == HTTPStatus.REQUEST_ENTITY_TOO_LARGE
    assert problem_type(response) == "support-photo-too-large"
    assert storage.objects == {}


def test_a_cursor_past_the_database_range_is_refused(person: Person) -> None:
    response = person.client.get(f"/support/messages?after={2**63}", headers=person.headers)

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_a_technician_has_no_access_to_support(
    integration_settings: Settings, app_client: TestClient
) -> None:
    headers = bearer(staff_token(integration_settings, StaffRole.TECHNICIAN))
    some = uuid.uuid4()

    for url in (
        "/admin/support/conversations",
        "/admin/support/unread",
        "/admin/support/incidents",
        f"/admin/support/conversations/{some}/messages",
    ):
        assert app_client.get(url, headers=headers).status_code == HTTPStatus.FORBIDDEN
    for url, body in (
        (f"/admin/support/conversations/{some}/take", None),
        (f"/admin/support/conversations/{some}/messages", {"text": "Hi", "seen_seq": 0}),
        (f"/admin/support/conversations/{some}/close", None),
        (f"/admin/support/incidents/{some}/take", None),
        (f"/admin/support/incidents/{some}/resolve", {"comment": "Done"}),
        (f"/admin/support/incidents/{some}/send-to-service", None),
    ):
        response = app_client.post(url, json=body, headers=headers)
        assert response.status_code == HTTPStatus.FORBIDDEN, url


def test_a_client_token_does_not_open_the_staff_side(person: Person) -> None:
    response = person.client.get("/admin/support/conversations", headers=person.headers)

    assert response.status_code == HTTPStatus.UNAUTHORIZED


def test_unknown_fields_are_refused(person: Person) -> None:
    response = person.client.post(
        "/support/messages", json={"text": "hi", "staff_id": "x"}, headers=person.headers
    )

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_an_empty_message_is_refused(person: Person) -> None:
    assert write(person, "   ").status_code == HTTPStatus.UNPROCESSABLE_ENTITY
