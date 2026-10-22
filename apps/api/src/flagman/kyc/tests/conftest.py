import asyncio
import secrets
import time
import urllib.error
import urllib.request
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING

import aioboto3
import pytest
from testcontainers.core.container import DockerContainer

if TYPE_CHECKING:
    from types_aiobotocore_s3 import S3Client

# Same image as infra/docker-compose.yml.
SEAWEEDFS_IMAGE = "chrislusf/seaweedfs:4.48"
S3_PORT = 8333
BUCKET = "flagman-private-test"
_STARTUP_TIMEOUT_SECONDS = 90


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
        url = f"http://{host}:{container.get_exposed_port(S3_PORT)}"
        _wait_until_healthy(url)
        endpoint = S3Endpoint(url, access_key, secret_key, BUCKET)
        endpoint.call(lambda s3: s3.create_bucket(Bucket=BUCKET))
        yield endpoint
