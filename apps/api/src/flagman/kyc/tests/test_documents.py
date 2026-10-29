import asyncio
import time
import urllib.parse
import uuid
from collections.abc import Callable, Iterator
from datetime import timedelta
from http import HTTPStatus
from io import BytesIO
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import text

from conftest import SettingsFactory
from flagman.core.config import Settings
from flagman.core.db import create_engine, create_session_factory
from flagman.core.photos import MAX_UPLOAD_BYTES
from flagman.core.ratelimit import Limit
from flagman.kyc import service
from flagman.kyc.api import get_document_storage
from flagman.kyc.domain import REQUIRED_DOCUMENTS, DocumentKind, KycStatus
from flagman.kyc.ports import DocumentStorage, create_document_storage
from flagman.kyc.tests.conftest import (
    Person,
    S3Endpoint,
    client_for,
    open_url,
    photo,
    problem_type,
    query,
)

if TYPE_CHECKING:
    from types_aiobotocore_s3 import S3Client

pytestmark = pytest.mark.integration


def _keys(settings: Settings, phone: str) -> dict[DocumentKind, str]:
    rows = query(
        settings,
        """
        SELECT d.kind, d.object_key FROM kyc_documents d
        JOIN kyc_applications a ON a.id = d.application_id
        JOIN users u ON u.id = a.user_id
        WHERE u.phone = :phone
        """,
        phone=phone,
    )
    return {DocumentKind(row.kind): row.object_key for row in rows}


def _application_id(settings: Settings, phone: str) -> uuid.UUID:
    rows = query(
        settings,
        "SELECT a.id FROM kyc_applications a JOIN users u ON u.id = a.user_id"
        " WHERE u.phone = :phone",
        phone=phone,
    )
    application_id: uuid.UUID = rows[0].id
    return application_id


def _stored_keys(seaweedfs: S3Endpoint, prefix: str) -> set[str]:
    async def list_keys(s3: "S3Client") -> set[str]:
        page = await s3.list_objects_v2(Bucket=seaweedfs.bucket, Prefix=prefix)
        return {item["Key"] for item in page.get("Contents", []) if "Key" in item}

    return seaweedfs.call(list_keys)


def _stored_object(seaweedfs: S3Endpoint, key: str) -> bytes:
    async def read(s3: "S3Client") -> bytes:
        response = await s3.get_object(Bucket=seaweedfs.bucket, Key=key)
        async with response["Body"] as body:
            return await body.read()

    return seaweedfs.call(read)


def test_a_new_client_has_an_empty_application(person: Callable[[], Person]) -> None:
    response = person().application()

    assert response.status_code == HTTPStatus.OK
    assert response.json() == {
        "status": "not_submitted",
        "rejection_reason": None,
        "submitted_at": None,
        "documents": [],
        "missing_documents": sorted(REQUIRED_DOCUMENTS),
    }


def test_the_stored_photo_is_a_jpeg_without_metadata(
    person: Callable[[], Person], kyc_settings: Settings, seaweedfs: S3Endpoint
) -> None:
    client = person()
    png = BytesIO()
    Image.open(BytesIO(photo())).save(png, format="PNG")

    # The declared type is ignored: a PNG sent as JPEG is accepted and stored as JPEG.
    response = client.upload(DocumentKind.PASSPORT, png.getvalue(), content_type="image/jpeg")
    assert response.status_code == HTTPStatus.OK, response.text
    response = client.upload(DocumentKind.LICENSE_FRONT)
    assert response.status_code == HTTPStatus.OK, response.text

    body = response.json()
    assert [document["kind"] for document in body["documents"]] == ["license_front", "passport"]
    assert body["status"] == "not_submitted"
    for key in _keys(kyc_settings, client.phone).values():
        stored = _stored_object(seaweedfs, key)
        assert Image.open(BytesIO(stored)).format == "JPEG"
        assert b"SecretPhone" not in stored
        assert b"Exif" not in stored


def test_the_bucket_is_private(
    person: Callable[[], Person], kyc_settings: Settings, seaweedfs: S3Endpoint
) -> None:
    client = person()
    client.upload(DocumentKind.PASSPORT)
    key = _keys(kyc_settings, client.phone)[DocumentKind.PASSPORT]

    assert open_url(f"{seaweedfs.url}/{seaweedfs.bucket}/{key}").status == HTTPStatus.FORBIDDEN


def test_a_signed_link_opens_the_own_photo(
    person: Callable[[], Person], kyc_settings: Settings
) -> None:
    client = person()
    client.upload(DocumentKind.SELFIE_WITH_LICENSE)

    response = client.url(DocumentKind.SELFIE_WITH_LICENSE)

    assert response.status_code == HTTPStatus.OK, response.text
    assert response.json()["expires_in"] == 300
    url = response.json()["url"]
    assert url.startswith(f"{kyc_settings.storage_public_url}/")
    opened = open_url(url)
    assert opened.status == HTTPStatus.OK
    assert Image.open(BytesIO(opened.body)).format == "JPEG"


def test_a_signed_link_tells_the_browser_not_to_store_the_photo(
    person: Callable[[], Person],
) -> None:
    client = person()
    client.upload(DocumentKind.PASSPORT)

    response = client.url(DocumentKind.PASSPORT)

    assert response.headers["Cache-Control"] == "no-store"
    assert open_url(response.json()["url"]).cache_control == "no-store"


def test_the_no_store_parameter_is_signed(person: Callable[[], Person]) -> None:
    client = person()
    client.upload(DocumentKind.PASSPORT)
    url: str = client.url(DocumentKind.PASSPORT).json()["url"]
    parts = urllib.parse.urlsplit(url)
    params = urllib.parse.parse_qsl(parts.query)

    def rebuilt(dropped: str | None) -> str:
        kept = [(name, value) for name, value in params if name != dropped]
        return parts._replace(query=urllib.parse.urlencode(kept)).geturl()

    assert ("response-cache-control", "no-store") in params
    # Rebuilding alone must not break the link, or the 403 below would prove nothing.
    assert open_url(rebuilt(None)).status == HTTPStatus.OK
    assert open_url(rebuilt("response-cache-control")).status == HTTPStatus.FORBIDDEN


def test_a_signed_link_stops_working_after_its_ttl(
    person: Callable[[], Person], kyc_settings: Settings
) -> None:
    client = person()
    client.upload(DocumentKind.PASSPORT)
    key = _keys(kyc_settings, client.phone)[DocumentKind.PASSPORT]
    storage = create_document_storage(kyc_settings)

    url = asyncio.run(storage.signed_get_url(key, timedelta(seconds=1)))
    time.sleep(3)

    assert open_url(url).status == HTTPStatus.FORBIDDEN


def test_a_link_to_a_document_not_uploaded_is_404(person: Callable[[], Person]) -> None:
    response = person().url(DocumentKind.PASSPORT)

    assert response.status_code == HTTPStatus.NOT_FOUND
    assert problem_type(response) == "kyc-document-not-found"


def test_another_client_never_sees_the_documents(person: Callable[[], Person]) -> None:
    owner, stranger = person(), person()
    owner.upload(DocumentKind.PASSPORT)

    assert stranger.application().json()["documents"] == []
    assert stranger.url(DocumentKind.PASSPORT).status_code == HTTPStatus.NOT_FOUND
    assert owner.url(DocumentKind.PASSPORT).status_code == HTTPStatus.OK


def test_a_new_upload_replaces_the_photo_and_deletes_the_old_one(
    person: Callable[[], Person], kyc_settings: Settings, seaweedfs: S3Endpoint
) -> None:
    client = person()
    client.upload(DocumentKind.PASSPORT)
    old_key = _keys(kyc_settings, client.phone)[DocumentKind.PASSPORT]

    response = client.upload(DocumentKind.PASSPORT, photo("retake"))

    assert response.status_code == HTTPStatus.OK
    assert len(response.json()["documents"]) == 1
    new_key = _keys(kyc_settings, client.phone)[DocumentKind.PASSPORT]
    assert new_key != old_key
    prefix = f"kyc/{_application_id(kyc_settings, client.phone)}/"
    assert _stored_keys(seaweedfs, prefix) == {new_key}


class SubmitFromAnotherDevice:
    """Storage that lets the same client submit right before the new photo is written."""

    def __init__(self, inner: DocumentStorage, settings: Settings, phone: str) -> None:
        self.inner = inner
        self.settings = settings
        self.phone = phone
        self.put_keys: list[str] = []

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        engine = create_engine(self.settings)
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "UPDATE kyc_applications SET status = 'pending', submitted_at = now()"
                        " WHERE user_id = (SELECT id FROM users WHERE phone = :phone)"
                    ),
                    {"phone": self.phone},
                )
        finally:
            await engine.dispose()
        await self.inner.put(key, data, content_type)
        self.put_keys.append(key)

    async def delete(self, key: str) -> None:
        await self.inner.delete(key)

    async def signed_get_url(self, key: str, ttl: timedelta) -> str:
        return await self.inner.signed_get_url(key, ttl)


def test_a_photo_stored_while_the_application_was_submitted_is_rolled_back(
    person: Callable[[], Person], kyc_settings: Settings, seaweedfs: S3Endpoint
) -> None:
    client = person()
    client.upload_all()
    keys_before = _keys(kyc_settings, client.phone)
    prefix = f"kyc/{_application_id(kyc_settings, client.phone)}/"
    stored_before = _stored_keys(seaweedfs, prefix)
    racing = SubmitFromAnotherDevice(
        create_document_storage(kyc_settings), kyc_settings, client.phone
    )
    client.client.app.dependency_overrides[get_document_storage] = lambda: racing  # type: ignore[attr-defined]

    response = client.upload(DocumentKind.PASSPORT, photo("retake"))

    assert response.status_code == HTTPStatus.CONFLICT
    assert problem_type(response) == "kyc-locked"
    # The photo did reach the storage before the second check, and was deleted after it.
    assert len(racing.put_keys) == 1
    assert _stored_keys(seaweedfs, prefix) == stored_before
    assert _keys(kyc_settings, client.phone) == keys_before


def test_submission_needs_every_document(person: Callable[[], Person]) -> None:
    client = person()
    client.upload(DocumentKind.PASSPORT)

    response = client.submit()

    assert response.status_code == HTTPStatus.CONFLICT
    assert problem_type(response) == "kyc-incomplete"
    assert response.json()["detail"] == (
        "Missing documents: license_back, license_front, selfie_with_license."
    )


def test_submission_needs_the_name_and_birth_date(person: Callable[[], Person]) -> None:
    client = person()
    client.upload_all()

    response = client.submit()

    assert response.status_code == HTTPStatus.CONFLICT
    assert problem_type(response) == "kyc-profile-incomplete"
    assert client.application().json()["status"] == "not_submitted"
    # The profile stays open, so the client fills it in and submits again.
    client.fill_profile()
    assert client.submit().status_code == HTTPStatus.OK


def test_a_submitted_application_is_pending_and_locked(
    person: Callable[[], Person], kyc_settings: Settings, seaweedfs: S3Endpoint
) -> None:
    client = person()
    client.fill_profile()
    client.upload_all()

    response = client.submit()

    assert response.status_code == HTTPStatus.OK, response.text
    assert response.json()["status"] == "pending"
    assert response.json()["submitted_at"] is not None
    assert response.json()["missing_documents"] == []
    prefix = f"kyc/{_application_id(kyc_settings, client.phone)}/"
    stored_before = _stored_keys(seaweedfs, prefix)
    locked = client.upload(DocumentKind.PASSPORT)
    assert locked.status_code == HTTPStatus.CONFLICT
    assert problem_type(locked) == "kyc-locked"
    assert problem_type(client.submit()) == "kyc-locked"
    # The refused upload left nothing behind in the storage.
    assert _stored_keys(seaweedfs, prefix) == stored_before
    # The client still sees what was sent for review.
    assert client.url(DocumentKind.PASSPORT).status_code == HTTPStatus.OK


def test_after_a_rejection_the_client_fixes_one_photo_and_submits_again(
    person: Callable[[], Person], kyc_settings: Settings
) -> None:
    client = person()
    client.fill_profile()
    client.upload_all()
    client.submit()
    # Operator decisions come with #35; until then the test makes one in the database.
    query(
        kyc_settings,
        "UPDATE kyc_applications SET status = 'rejected', rejection_reason = :reason"
        " WHERE id = :id",
        reason="The selfie is blurry",
        id=_application_id(kyc_settings, client.phone),
    )
    rejected = client.application().json()
    assert rejected["status"] == "rejected"
    assert rejected["rejection_reason"] == "The selfie is blurry"

    assert client.upload(DocumentKind.SELFIE_WITH_LICENSE).status_code == HTTPStatus.OK
    resubmitted = client.submit()

    assert resubmitted.status_code == HTTPStatus.OK, resubmitted.text
    assert resubmitted.json()["status"] == "pending"
    assert resubmitted.json()["rejection_reason"] is None


def test_the_admission_status_is_public_for_other_modules(
    person: Callable[[], Person], kyc_settings: Settings
) -> None:
    client = person()
    client.fill_profile()
    client.upload_all()
    client.submit()
    user_id = query(kyc_settings, "SELECT id FROM users WHERE phone = :p", p=client.phone)[0].id

    async def statuses() -> tuple[KycStatus, KycStatus, KycStatus]:
        engine = create_engine(kyc_settings)
        try:
            async with create_session_factory(engine)() as db:
                standalone = await service.get_admission_status(db, user_id)
                async with db.begin():
                    # Called from inside the caller's own transaction.
                    joined = await service.get_admission_status(db, user_id)
                unknown = await service.get_admission_status(db, uuid.uuid4())
                return standalone, joined, unknown
        finally:
            await engine.dispose()

    assert asyncio.run(statuses()) == (
        KycStatus.PENDING,
        KycStatus.PENDING,
        KycStatus.NOT_SUBMITTED,
    )


@pytest.mark.parametrize(
    ("data", "problem"),
    [
        pytest.param(b"not a photo", "document-type-unsupported", id="text"),
        pytest.param(b"", "document-type-unsupported", id="empty"),
        pytest.param(b"\xff" * (MAX_UPLOAD_BYTES + 1), "document-too-large", id="too-large"),
    ],
)
def test_a_bad_file_is_refused(person: Callable[[], Person], data: bytes, problem: str) -> None:
    client = person()

    response = client.upload(DocumentKind.PASSPORT, data)

    assert problem_type(response) == problem
    assert client.application().json()["documents"] == []


def test_a_chunked_upload_is_cut_off_at_the_limit(person: Callable[[], Person]) -> None:
    client = person()

    def chunks() -> Iterator[bytes]:
        # No Content-Length: the limit has to be enforced while reading.
        for _ in range(MAX_UPLOAD_BYTES // (1024 * 1024) + 2):
            yield b"\xff" * (1024 * 1024)

    response = client.client.put(
        f"/kyc/documents/{DocumentKind.PASSPORT}",
        content=chunks(),
        headers={**client.headers, "Content-Type": "image/jpeg"},
    )

    assert response.status_code == HTTPStatus.REQUEST_ENTITY_TOO_LARGE


def test_an_unknown_document_kind_is_422(person: Callable[[], Person]) -> None:
    assert person().upload("diploma").status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_uploads_are_rate_limited(
    person: Callable[[], Person], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service, "UPLOAD_LIMIT", Limit("kyc-upload-user-test", 1, 3600))
    client = person()

    assert client.upload(DocumentKind.PASSPORT).status_code == HTTPStatus.OK
    limited = client.upload(DocumentKind.PASSPORT)

    assert limited.status_code == HTTPStatus.TOO_MANY_REQUESTS
    assert "Retry-After" in limited.headers


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/kyc/application"),
        ("PUT", "/kyc/documents/passport"),
        ("GET", "/kyc/documents/passport/url"),
        ("POST", "/kyc/application/submit"),
    ],
)
def test_every_endpoint_needs_a_client_token(client: TestClient, method: str, path: str) -> None:
    response = client.request(method, path, content=photo() if method == "PUT" else None)

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert problem_type(response) == "access-token-invalid"


def test_without_storage_uploads_answer_503_and_the_rest_works(
    env_values: dict[str, str], make_settings: SettingsFactory
) -> None:
    settings = make_settings(**env_values, s3_access_key=None, s3_secret_key=None)
    for client in client_for(settings):
        someone = Person(client)

        upload = someone.upload(DocumentKind.PASSPORT)
        link = someone.url(DocumentKind.PASSPORT)

        assert upload.status_code == HTTPStatus.SERVICE_UNAVAILABLE
        assert problem_type(upload) == "document-storage-unavailable"
        assert link.status_code == HTTPStatus.SERVICE_UNAVAILABLE
        assert someone.application().status_code == HTTPStatus.OK


def test_keys_and_links_stay_out_of_the_logs(
    person: Callable[[], Person], kyc_settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    client = person()
    client.fill_profile()
    client.upload_all()
    url = client.url(DocumentKind.PASSPORT).json()["url"]
    client.submit()

    logs = caplog.text

    assert "kyc_document_uploaded" in logs
    assert "kyc_application_submitted" in logs
    for secret in (
        url,
        "X-Amz-Signature",
        client.phone,
        *_keys(kyc_settings, client.phone).values(),
    ):
        assert secret not in logs
