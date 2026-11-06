"""Private object storage of photos (SeaweedFS over S3), see docs/adr/0005 and docs/adr/0012.

Shared by KYC documents and car inspections. Keys and links are not logged: a link opens the
photo.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING, Protocol

import aioboto3
from aiobotocore.config import AioConfig

from flagman.core.config import Settings

if TYPE_CHECKING:
    from aiobotocore.session import ClientCreatorContext
    from types_aiobotocore_s3 import S3Client


class ObjectStorage(Protocol):
    async def put(self, key: str, data: bytes, content_type: str) -> None: ...

    async def delete(self, key: str) -> None:
        """Deleting a key that is not there is not an error."""
        ...

    async def signed_get_url(self, key: str, ttl: timedelta) -> str:
        """A link that opens the photo without credentials until `ttl` runs out; never cached."""
        ...


# A session caches the S3 service model that every client is built from.
_session = aioboto3.Session()

_CONNECT_TIMEOUT = 5.0
# Connections to the storage per process. Ten is botocore's default; a pilot process uses about
# one at a time (2.4 photos/s at about 25 ms each, #162).
_MAX_CONNECTIONS = 10

_CLIENT_CONFIG = AioConfig(
    signature_version="s3v4",
    # SeaweedFS serves buckets as paths; virtual-host style would need DNS per bucket.
    s3={"addressing_style": "path"},
    connect_timeout=_CONNECT_TIMEOUT,
    max_pool_connections=_MAX_CONNECTIONS,
    read_timeout=30,
    retries={"max_attempts": 3, "mode": "standard"},
)


@dataclass(frozen=True)
class S3ObjectStorage:
    """Works on clients opened once per process by `open_object_storage`.

    Building a client parses the service model and makes an aiohttp connector: about 20 ms of
    event-loop CPU per call, measured in #162. One client serves concurrent requests of the loop.
    """

    client: "S3Client"
    # Never sends a request: it only signs links for the public address.
    signer: "S3Client"
    bucket: str
    connections: asyncio.Semaphore = field(
        default_factory=lambda: asyncio.Semaphore(_MAX_CONNECTIONS)
    )

    @asynccontextmanager
    async def _connection(self) -> AsyncIterator[None]:
        # aiobotocore gives aiohttp only socket timeouts, so a call would wait for a free pooled
        # connection with no limit: with the storage stalled, every later upload of the process
        # would queue behind ten hung ones. Waiting for one counts as connecting instead.
        async with asyncio.timeout(_CONNECT_TIMEOUT):
            await self.connections.acquire()
        try:
            yield
        finally:
            self.connections.release()

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        async with self._connection():
            await self.client.put_object(
                Bucket=self.bucket, Key=key, Body=data, ContentType=content_type
            )

    async def delete(self, key: str) -> None:
        async with self._connection():
            await self.client.delete_object(Bucket=self.bucket, Key=key)

    async def signed_get_url(self, key: str, ttl: timedelta) -> str:
        # Signed locally, no request to the storage. The signature covers the host, so the link
        # is signed for the address the client opens (docs/adr/0005), not the internal one.
        # no-store keeps the photo out of the browser's disk cache, where a copy would outlive the
        # link; it is signed too, so stripping it from the link breaks the link.
        return await self.signer.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key, "ResponseCacheControl": "no-store"},
            ExpiresIn=int(ttl.total_seconds()),
        )


@asynccontextmanager
async def open_object_storage(settings: Settings) -> AsyncIterator[ObjectStorage | None]:
    """The process-wide storage for the app lifespan; closes its clients on exit.

    None without `S3_ACCESS_KEY` / `S3_SECRET_KEY`: the API runs, uploads answer 503. Opening
    connects nowhere, so the API starts while the storage is down.
    """
    if settings.s3_access_key is None or settings.s3_secret_key is None:
        yield None
        return
    access_key = settings.s3_access_key.get_secret_value()
    secret_key = settings.s3_secret_key.get_secret_value()

    def client(endpoint_url: str) -> "ClientCreatorContext[S3Client]":
        return _session.client(
            "s3",
            endpoint_url=endpoint_url,
            region_name=settings.s3_region,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            config=_CLIENT_CONFIG,
        )

    async with AsyncExitStack() as stack:
        yield S3ObjectStorage(
            client=await stack.enter_async_context(client(settings.s3_endpoint_url)),
            signer=await stack.enter_async_context(client(settings.storage_public_url)),
            bucket=settings.s3_bucket,
        )
