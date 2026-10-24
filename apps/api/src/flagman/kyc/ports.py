from dataclasses import dataclass
from datetime import timedelta
from http import HTTPStatus
from typing import TYPE_CHECKING, Protocol

import aioboto3
from aiobotocore.config import AioConfig

from flagman.core.config import Settings
from flagman.core.errors import AppError

if TYPE_CHECKING:
    from aiobotocore.session import ClientCreatorContext
    from types_aiobotocore_s3 import S3Client


class DocumentStorageUnavailableError(AppError):
    status = HTTPStatus.SERVICE_UNAVAILABLE
    slug = "document-storage-unavailable"
    title = "Document storage is unavailable"


class DocumentStorage(Protocol):
    """Private storage of document photos. Keys and links are not logged: a link opens the photo."""

    async def put(self, key: str, data: bytes, content_type: str) -> None: ...

    async def delete(self, key: str) -> None:
        """Deleting a key that is not there is not an error."""
        ...

    async def signed_get_url(self, key: str, ttl: timedelta) -> str:
        """A link that opens the photo without credentials until `ttl` runs out; never cached."""
        ...


# Shared by all requests: a session caches the S3 service model that every client is built from.
_session = aioboto3.Session()

_CLIENT_CONFIG = AioConfig(
    signature_version="s3v4",
    # SeaweedFS serves buckets as paths; virtual-host style would need DNS per bucket.
    s3={"addressing_style": "path"},
    connect_timeout=5,
    read_timeout=30,
    retries={"max_attempts": 3, "mode": "standard"},
)


@dataclass(frozen=True)
class S3DocumentStorage:
    endpoint_url: str
    public_url: str
    region: str
    bucket: str
    access_key: str
    secret_key: str

    def _client(self, endpoint_url: str) -> "ClientCreatorContext[S3Client]":
        return _session.client(
            "s3",
            endpoint_url=endpoint_url,
            region_name=self.region,
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key,
            config=_CLIENT_CONFIG,
        )

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        async with self._client(self.endpoint_url) as s3:
            await s3.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)

    async def delete(self, key: str) -> None:
        async with self._client(self.endpoint_url) as s3:
            await s3.delete_object(Bucket=self.bucket, Key=key)

    async def signed_get_url(self, key: str, ttl: timedelta) -> str:
        # Signed locally, no request to the storage. The signature covers the host, so the link
        # is signed for the address the client opens (docs/adr/0005), not the internal one.
        # no-store keeps the photo out of the browser's disk cache, where a copy would outlive the
        # link; it is signed too, so stripping it from the link breaks the link.
        async with self._client(self.public_url) as s3:
            return await s3.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": key, "ResponseCacheControl": "no-store"},
                ExpiresIn=int(ttl.total_seconds()),
            )


def create_document_storage(settings: Settings) -> DocumentStorage:
    if settings.s3_access_key is None or settings.s3_secret_key is None:
        raise DocumentStorageUnavailableError("No document storage is configured.")
    return S3DocumentStorage(
        endpoint_url=settings.s3_endpoint_url,
        public_url=settings.storage_public_url,
        region=settings.s3_region,
        bucket=settings.s3_bucket,
        access_key=settings.s3_access_key.get_secret_value(),
        secret_key=settings.s3_secret_key.get_secret_value(),
    )
