import asyncio
import secrets
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass
from http import HTTPStatus
from io import BytesIO
from typing import TYPE_CHECKING, Any, NamedTuple

import aioboto3
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from redis import Redis
from sqlalchemy import Row, text
from testcontainers.core.container import DockerContainer

from conftest import SettingsFactory
from flagman.core.app import create_app
from flagman.core.config import Settings
from flagman.core.db import create_engine
from flagman.identity.api import get_sms_sender
from flagman.identity.domain import generate_otp_code
from flagman.kyc.domain import REQUIRED_DOCUMENTS, DocumentKind

if TYPE_CHECKING:
    from types_aiobotocore_s3 import S3Client

# Same image as infra/docker-compose.yml.
SEAWEEDFS_IMAGE = "chrislusf/seaweedfs:4.48"
S3_PORT = 8333
BUCKET = "flagman-private-test"
_STARTUP_TIMEOUT_SECONDS = 90
_EXIF_MAKE = 0x010F


@dataclass(frozen=True)
class S3Endpoint:
    url: str
    access_key: str
    secret_key: str
    bucket: str

    def call[T](self, action: "Callable[[S3Client], Awaitable[T]]") -> T:
        """Runs one action with the admin keys, from sync tests."""

        async def run() -> T:
            async with aioboto3.Session().client(
                "s3",
                endpoint_url=self.url,
                region_name="us-east-1",
                aws_access_key_id=self.access_key,
                aws_secret_access_key=self.secret_key,
            ) as s3:
                return await action(s3)

        return asyncio.run(run())


def _wait_until_healthy(url: str) -> None:
    deadline = time.monotonic() + _STARTUP_TIMEOUT_SECONDS
    while True:
        try:
            with urllib.request.urlopen(f"{url}/healthz", timeout=2):  # noqa: S310 - local container
                return
        except (urllib.error.URLError, ConnectionError):
            if time.monotonic() > deadline:
                raise
            time.sleep(0.5)


def _published_port(container: DockerContainer, port: int) -> int:
    """Docker Desktop reports the container running a moment before it publishes the port."""
    deadline = time.monotonic() + _STARTUP_TIMEOUT_SECONDS
    while True:
        try:
            return container.get_exposed_port(port)
        except ConnectionError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.2)


@pytest.fixture(scope="session")
def seaweedfs() -> Iterator[S3Endpoint]:
    access_key, secret_key = secrets.token_hex(10), secrets.token_hex(20)
    container = (
        DockerContainer(SEAWEEDFS_IMAGE)
        # With keys set the S3 gateway refuses anonymous access, as in infra/docker-compose.yml.
        .with_env("AWS_ACCESS_KEY_ID", access_key)
        .with_env("AWS_SECRET_ACCESS_KEY", secret_key)
        .with_command(
            f"server -dir=/data -ip.bind=0.0.0.0 -s3 -s3.port={S3_PORT} -master.telemetry=false"
        )
        .with_exposed_ports(S3_PORT)
    )
    with container:
        host = container.get_container_host_ip()
        url = f"http://{host}:{_published_port(container, S3_PORT)}"
        _wait_until_healthy(url)
        endpoint = S3Endpoint(url, access_key, secret_key, BUCKET)
        endpoint.call(lambda s3: s3.create_bucket(Bucket=BUCKET))
        yield endpoint


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


def reset_limits(settings: Settings) -> None:
    # The Redis container is shared by the test session.
    with Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        password=settings.redis_password.get_secret_value(),
    ) as redis:
        redis.flushdb()


def client_for(settings: Settings) -> Iterator[TestClient]:
    app = create_app(settings)
    app.dependency_overrides[get_sms_sender] = FakeSms
    reset_limits(settings)
    with TestClient(app, base_url="https://testserver") as client:
        yield client


@pytest.fixture
def client(kyc_settings: Settings) -> Iterator[TestClient]:
    yield from client_for(kyc_settings)


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
            content=photo() if data is None else data,
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

    def update_profile(self, **fields: str | None) -> Any:
        return self.client.patch("/me", json=fields, headers=self.headers)


@pytest.fixture
def person(client: TestClient) -> Callable[[], Person]:
    return lambda: Person(client)


def photo(marker: str = "SecretPhone") -> bytes:
    exif = Image.Exif()
    exif[_EXIF_MAKE] = marker
    output = BytesIO()
    Image.new("RGB", (64, 48), (10, 120, 200)).save(output, format="JPEG", exif=exif)
    return output.getvalue()


def query(settings: Settings, sql: str, **params: Any) -> list[Row[Any]]:
    async def run() -> list[Row[Any]]:
        engine = create_engine(settings)
        try:
            async with engine.begin() as connection:
                result = await connection.execute(text(sql), params)
                return list(result.all()) if result.returns_rows else []
        finally:
            await engine.dispose()

    return asyncio.run(run())


class Opened(NamedTuple):
    status: int
    body: bytes
    cache_control: str | None


def open_url(url: str) -> Opened:
    try:
        with urllib.request.urlopen(url, timeout=10) as response:  # noqa: S310 - test container
            return Opened(response.status, response.read(), response.headers.get("Cache-Control"))
    except urllib.error.HTTPError as error:
        return Opened(error.code, b"", None)


def problem_type(response: Any) -> str:
    value: str = response.json()["type"]
    return value.removeprefix("urn:flagman:problem:")
