import asyncio
import secrets
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable, Iterator
from datetime import timedelta
from http import HTTPStatus
from io import BytesIO
from typing import TYPE_CHECKING, Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from redis import Redis
from sqlalchemy import Row, text

from conftest import SettingsFactory
from flagman.core.app import create_app
from flagman.core.config import Settings
from flagman.core.db import create_engine, create_session_factory
from flagman.core.ratelimit import Limit
from flagman.identity.api import get_sms_sender
from flagman.identity.domain import generate_otp_code
from flagman.kyc import service
from flagman.kyc.domain import REQUIRED_DOCUMENTS, DocumentKind, KycStatus
from flagman.kyc.images import MAX_UPLOAD_BYTES
from flagman.kyc.ports import create_document_storage
from flagman.kyc.tests.conftest import S3Endpoint

if TYPE_CHECKING:
    from types_aiobotocore_s3 import S3Client

pytestmark = pytest.mark.integration

_EXIF_MAKE = 0x010F


class FakeSms:
    def __init__(self) -> None:
        self.sent: dict[str, str] = {}

    def choose_code(self, phone: str) -> str:
        return generate_otp_code()

    async def send_otp(self, phone: str, code: str) -> None:
        self.sent[phone] = code


@pytest.fixture
def env_values(integration_env: dict[str, str], migrated_database: None) -> dict[str, str]:
    return {name.lower(): value for name, value in integration_env.items()}


@pytest.fixture
def kyc_settings(
    env_values: dict[str, str], make_settings: SettingsFactory, seaweedfs: S3Endpoint
) -> Settings:
    return make_settings(
        **env_values,
        s3_endpoint_url=seaweedfs.url,
        # Another name for the same container: proves links are signed for the public address.
        s3_public_url=seaweedfs.url.replace("localhost", "127.0.0.1"),
        s3_access_key=seaweedfs.access_key,
        s3_secret_key=seaweedfs.secret_key,
        s3_bucket=seaweedfs.bucket,
    )


def _reset_limits(settings: Settings) -> None:
    # The Redis container is shared by the test session.
    with Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        password=settings.redis_password.get_secret_value(),
    ) as redis:
        redis.flushdb()


def _client_for(settings: Settings) -> Iterator[TestClient]:
    app = create_app(settings)
    app.dependency_overrides[get_sms_sender] = FakeSms
    _reset_limits(settings)
    with TestClient(app, base_url="https://testserver") as client:
        yield client


@pytest.fixture
def client(kyc_settings: Settings) -> Iterator[TestClient]:
    yield from _client_for(kyc_settings)


class Person:
    """A signed-in client with a fresh phone: users stay in the shared database between tests."""

    def __init__(self, client: TestClient) -> None:
        self.client = client
        self.phone = f"+37529{secrets.randbelow(10**7):07d}"
        sms = FakeSms()
        self.client.app.dependency_overrides[get_sms_sender] = lambda: sms  # type: ignore[attr-defined]
        accepted = client.post("/auth/otp/request", json={"phone": self.phone})
        assert accepted.status_code == HTTPStatus.ACCEPTED, accepted.text
        verified = client.post(
            "/auth/otp/verify",
            json={
                "phone": self.phone,
                "code": sms.sent[self.phone],
                "device_id": str(uuid.uuid4()),
            },
        )
        assert verified.status_code == HTTPStatus.OK, verified.text
        self.headers = {"Authorization": f"Bearer {verified.json()['access_token']}"}

    def application(self) -> Any:
        return self.client.get("/kyc/application", headers=self.headers)

    def upload(
        self, kind: DocumentKind | str, data: bytes | None = None, content_type: str = "image/jpeg"
    ) -> Any:
        return self.client.put(
            f"/kyc/documents/{kind}",
            content=_photo() if data is None else data,
            headers={**self.headers, "Content-Type": content_type},
        )

    def upload_all(self) -> None:
        for kind in REQUIRED_DOCUMENTS:
            response = self.upload(kind)
            assert response.status_code == HTTPStatus.OK, response.text

    def url(self, kind: DocumentKind) -> Any:
        return self.client.get(f"/kyc/documents/{kind}/url", headers=self.headers)

    def submit(self) -> Any:
        return self.client.post("/kyc/application/submit", headers=self.headers)


@pytest.fixture
def person(client: TestClient) -> Callable[[], Person]:
    return lambda: Person(client)


def _photo(marker: str = "SecretPhone") -> bytes:
    exif = Image.Exif()
    exif[_EXIF_MAKE] = marker
    output = BytesIO()
    Image.new("RGB", (64, 48), (10, 120, 200)).save(output, format="JPEG", exif=exif)
    return output.getvalue()


def _query(settings: Settings, sql: str, **params: Any) -> list[Row[Any]]:
    async def run() -> list[Row[Any]]:
        engine = create_engine(settings)
        try:
            async with engine.begin() as connection:
                result = await connection.execute(text(sql), params)
                return list(result.all()) if result.returns_rows else []
        finally:
            await engine.dispose()

    return asyncio.run(run())


def _keys(settings: Settings, phone: str) -> dict[DocumentKind, str]:
    rows = _query(
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
    rows = _query(
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


def _open(url: str) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(url, timeout=10) as response:  # noqa: S310 - test container
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, b""


def _problem_type(response: Any) -> str:
    problem_type: str = response.json()["type"]
    return problem_type.removeprefix("urn:flagman:problem:")


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
    Image.open(BytesIO(_photo())).save(png, format="PNG")

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

    status, _ = _open(f"{seaweedfs.url}/{seaweedfs.bucket}/{key}")

    assert status == HTTPStatus.FORBIDDEN


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
    status, photo = _open(url)
    assert status == HTTPStatus.OK
    assert Image.open(BytesIO(photo)).format == "JPEG"


def test_a_signed_link_stops_working_after_its_ttl(
    person: Callable[[], Person], kyc_settings: Settings
) -> None:
    client = person()
    client.upload(DocumentKind.PASSPORT)
    key = _keys(kyc_settings, client.phone)[DocumentKind.PASSPORT]
    storage = create_document_storage(kyc_settings)

    url = asyncio.run(storage.signed_get_url(key, timedelta(seconds=1)))
    time.sleep(3)

    assert _open(url)[0] == HTTPStatus.FORBIDDEN


def test_a_link_to_a_document_not_uploaded_is_404(person: Callable[[], Person]) -> None:
    response = person().url(DocumentKind.PASSPORT)

    assert response.status_code == HTTPStatus.NOT_FOUND
    assert _problem_type(response) == "kyc-document-not-found"


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

    response = client.upload(DocumentKind.PASSPORT, _photo("retake"))

    assert response.status_code == HTTPStatus.OK
    assert len(response.json()["documents"]) == 1
    new_key = _keys(kyc_settings, client.phone)[DocumentKind.PASSPORT]
    assert new_key != old_key
    prefix = f"kyc/{_application_id(kyc_settings, client.phone)}/"
    assert _stored_keys(seaweedfs, prefix) == {new_key}


def test_submission_needs_every_document(person: Callable[[], Person]) -> None:
    client = person()
    client.upload(DocumentKind.PASSPORT)

    response = client.submit()

    assert response.status_code == HTTPStatus.CONFLICT
    assert _problem_type(response) == "kyc-incomplete"
    assert response.json()["detail"] == (
        "Missing documents: license_back, license_front, selfie_with_license."
    )


def test_a_submitted_application_is_pending_and_locked(
    person: Callable[[], Person], kyc_settings: Settings, seaweedfs: S3Endpoint
) -> None:
    client = person()
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
    assert _problem_type(locked) == "kyc-locked"
    assert _problem_type(client.submit()) == "kyc-locked"
    # The refused upload left nothing behind in the storage.
    assert _stored_keys(seaweedfs, prefix) == stored_before
    # The client still sees what was sent for review.
    assert client.url(DocumentKind.PASSPORT).status_code == HTTPStatus.OK


def test_after_a_rejection_the_client_fixes_one_photo_and_submits_again(
    person: Callable[[], Person], kyc_settings: Settings
) -> None:
    client = person()
    client.upload_all()
    client.submit()
    # Operator decisions come with #35; until then the test makes one in the database.
    _query(
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
    client.upload_all()
    client.submit()
    user_id = _query(kyc_settings, "SELECT id FROM users WHERE phone = :p", p=client.phone)[0].id

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

    assert _problem_type(response) == problem
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
    response = client.request(method, path, content=_photo() if method == "PUT" else None)

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert _problem_type(response) == "access-token-invalid"


def test_without_storage_uploads_answer_503_and_the_rest_works(
    env_values: dict[str, str], make_settings: SettingsFactory
) -> None:
    settings = make_settings(**env_values, s3_access_key=None, s3_secret_key=None)
    for client in _client_for(settings):
        someone = Person(client)

        upload = someone.upload(DocumentKind.PASSPORT)
        link = someone.url(DocumentKind.PASSPORT)

        assert upload.status_code == HTTPStatus.SERVICE_UNAVAILABLE
        assert _problem_type(upload) == "document-storage-unavailable"
        assert link.status_code == HTTPStatus.SERVICE_UNAVAILABLE
        assert someone.application().status_code == HTTPStatus.OK


def test_keys_and_links_stay_out_of_the_logs(
    person: Callable[[], Person], kyc_settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    client = person()
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
