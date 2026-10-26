import uuid
from http import HTTPStatus
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from flagman.core.config import Settings
from flagman.core.photos import MAX_UPLOAD_BYTES
from flagman.fleet.tests.conftest import add_car
from flagman.rentals.domain import MAX_INSPECTION_PHOTOS, MIN_INSPECTION_PHOTOS
from flagman.rentals.tests.conftest import (
    CAR_POINT,
    FakeStorage,
    Person,
    execute,
    photo,
    problem_type,
)

pytestmark = pytest.mark.integration

_EXIF_MAKE = 0x010F


@pytest.fixture
def person(integration_settings: Settings, app_client: TestClient) -> Person:
    return Person(integration_settings, app_client)


@pytest.fixture
def rental(integration_settings: Settings, person: Person) -> str:
    response = person.reserve(add_car(integration_settings, CAR_POINT))
    assert response.status_code == HTTPStatus.CREATED
    return str(response.json()["id"])


def test_an_empty_inspection_is_ready_to_fill(person: Person, rental: str) -> None:
    response = person.client.get(f"/rentals/{rental}/inspections/before", headers=person.headers)

    assert response.json() == {
        "stage": "before",
        "photos": [],
        "damages": [],
        "submitted_at": None,
        "min_photos": MIN_INSPECTION_PHOTOS,
        "max_photos": MAX_INSPECTION_PHOTOS,
    }


def test_a_photo_is_stored_clean_under_a_key_without_the_client(
    person: Person, rental: str, storage: FakeStorage
) -> None:
    exif = Image.Exif()
    exif[_EXIF_MAKE] = "SecretPhone"
    original = BytesIO()
    Image.new("RGB", (64, 48), (10, 20, 30)).save(original, format="JPEG", exif=exif)

    response = person.upload(rental, original.getvalue())

    assert response.status_code == HTTPStatus.OK
    assert len(response.json()["photos"]) == 1
    [(key, stored)] = storage.objects.items()
    assert key.startswith(f"inspections/{rental}/")
    assert str(person.id) not in key
    assert b"SecretPhone" not in stored
    assert Image.open(BytesIO(stored)).format == "JPEG"


def test_photos_cannot_be_deleted(person: Person, rental: str) -> None:
    photo_id = person.upload(rental, photo()).json()["photos"][0]["id"]

    response = person.client.delete(
        f"/rentals/{rental}/inspections/before/photos/{photo_id}", headers=person.headers
    )

    # There is no such endpoint at all.
    assert response.status_code in {HTTPStatus.NOT_FOUND, HTTPStatus.METHOD_NOT_ALLOWED}
    inspection = person.client.get(f"/rentals/{rental}/inspections/before", headers=person.headers)
    assert [p["id"] for p in inspection.json()["photos"]] == [photo_id]


def test_an_inspection_takes_at_most_eight_photos(
    person: Person, rental: str, storage: FakeStorage
) -> None:
    for index in range(MAX_INSPECTION_PHOTOS):
        assert person.upload(rental, photo(index)).status_code == HTTPStatus.OK

    response = person.upload(rental, photo(99))

    assert response.status_code == HTTPStatus.CONFLICT
    assert problem_type(response) == "inspection-photo-limit"
    assert len(storage.objects) == MAX_INSPECTION_PHOTOS


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(
            (
                b"not a photo",
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                "inspection-photo-type-unsupported",
            ),
            id="text",
        ),
        pytest.param(
            (
                b"\xff" * (MAX_UPLOAD_BYTES + 1),
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                "inspection-photo-too-large",
            ),
            id="too-large",
        ),
    ],
)
def test_only_photos_within_the_limit_are_taken(
    person: Person, rental: str, storage: FakeStorage, case: tuple[bytes, int, str]
) -> None:
    body, status, slug = case

    response = person.upload(rental, body)

    assert response.status_code == status
    assert problem_type(response) == slug
    assert storage.objects == {}


def test_a_failed_storage_leaves_no_photo(
    person: Person, rental: str, storage: FakeStorage
) -> None:
    storage.fail_put = True

    response = person.upload(rental, photo())

    assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR

    inspection = person.client.get(f"/rentals/{rental}/inspections/before", headers=person.headers)
    assert inspection.json()["photos"] == []


def test_the_inspection_needs_four_photos(person: Person, rental: str) -> None:
    for index in range(MIN_INSPECTION_PHOTOS - 1):
        person.upload(rental, photo(index))

    response = person.submit(rental)

    assert response.status_code == HTTPStatus.CONFLICT
    assert problem_type(response) == "inspection-incomplete"


def test_a_submitted_inspection_is_final(person: Person, rental: str) -> None:
    person.damage(rental, "rear", "  Scratch   on the bumper ")
    person.inspect(rental)

    assert person.get(rental).json()["inspection_submitted"] is True
    inspection = person.client.get(
        f"/rentals/{rental}/inspections/before", headers=person.headers
    ).json()
    assert inspection["submitted_at"] is not None
    assert [(d["side"], d["comment"]) for d in inspection["damages"]] == [
        ("rear", "Scratch on the bumper")
    ]
    for response in (
        person.upload(rental, photo()),
        person.damage(rental, "left", "dent"),
        person.submit(rental),
    ):
        assert response.status_code == HTTPStatus.CONFLICT
        assert problem_type(response) == "inspection-closed"


def test_the_inspection_after_waits_for_the_trip(person: Person, rental: str) -> None:
    response = person.upload(rental, photo(), stage="after")

    assert response.status_code == HTTPStatus.CONFLICT
    assert problem_type(response) == "inspection-closed"


@pytest.mark.parametrize(
    "body",
    [
        {"side": "left", "comment": "   "},
        {"side": "left", "comment": "x" * 301},
        {"side": "wheel", "comment": "flat"},
        {"side": "left", "comment": "dent", "photo": "x"},
    ],
)
def test_damages_are_checked(person: Person, rental: str, body: dict[str, str]) -> None:
    response = person.client.post(
        f"/rentals/{rental}/inspections/before/damages", json=body, headers=person.headers
    )

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_a_photo_link_is_short_lived_and_not_cached(person: Person, rental: str) -> None:
    photo_id = person.upload(rental, photo()).json()["photos"][0]["id"]

    response = person.client.get(
        f"/rentals/{rental}/inspections/before/photos/{photo_id}/url", headers=person.headers
    )

    assert response.status_code == HTTPStatus.OK
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json()["expires_in"] == 300
    assert f"inspections/{rental}/" in response.json()["url"]


def test_photo_links_are_for_the_photos_of_this_inspection(
    integration_settings: Settings, app_client: TestClient, person: Person, rental: str
) -> None:
    photo_id = person.upload(rental, photo()).json()["photos"][0]["id"]
    stranger = Person(integration_settings, app_client)

    unknown = person.client.get(
        f"/rentals/{rental}/inspections/before/photos/{uuid.uuid4()}/url", headers=person.headers
    )
    other_stage = person.client.get(
        f"/rentals/{rental}/inspections/after/photos/{photo_id}/url", headers=person.headers
    )
    strangers = stranger.client.get(
        f"/rentals/{rental}/inspections/before/photos/{photo_id}/url", headers=stranger.headers
    )

    assert problem_type(unknown) == "inspection-photo-not-found"
    assert problem_type(other_stage) == "inspection-photo-not-found"
    assert problem_type(strangers) == "rental-not-found"


def test_an_inspection_belongs_to_a_reservation_that_is_still_on(
    integration_settings: Settings, person: Person, rental: str
) -> None:
    execute(
        integration_settings,
        "UPDATE rentals SET status = 'cancelled', cancel_reason = 'client', ended_at = now() "
        "WHERE id = :id",
        id=uuid.UUID(rental),
    )

    response = person.upload(rental, photo())

    assert response.status_code == HTTPStatus.CONFLICT
    assert problem_type(response) == "inspection-closed"
