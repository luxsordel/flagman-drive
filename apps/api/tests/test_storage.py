import asyncio
from datetime import timedelta
from types import TracebackType
from typing import Any, Self

import pytest

from conftest import SettingsFactory
from flagman.core import storage
from flagman.core.storage import open_object_storage

INTERNAL_URL = "http://seaweedfs:8333"
PUBLIC_URL = "https://files.example.test"


class FakeClient:
    def __init__(self, endpoint_url: str) -> None:
        self.endpoint_url = endpoint_url
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.closed = False
        # Cleared to make the storage hang, as a stalled SeaweedFS does.
        self.responding = asyncio.Event()
        self.responding.set()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.closed = True

    async def put_object(self, **params: Any) -> None:
        self.calls.append(("put_object", params))
        await self.responding.wait()

    async def delete_object(self, **params: Any) -> None:
        self.calls.append(("delete_object", params))

    async def generate_presigned_url(self, operation: str, **params: Any) -> str:
        self.calls.append((operation, params))
        return f"{self.endpoint_url}/signed"


class FakeSession:
    """Counts clients: building one is the cost the shared storage avoids."""

    def __init__(self) -> None:
        self.clients: list[FakeClient] = []

    def client(self, service_name: str, *, endpoint_url: str, **kwargs: Any) -> FakeClient:
        assert service_name == "s3"
        client = FakeClient(endpoint_url)
        self.clients.append(client)
        return client


@pytest.fixture
def session(monkeypatch: pytest.MonkeyPatch) -> FakeSession:
    fake = FakeSession()
    monkeypatch.setattr(storage, "_session", fake)
    return fake


async def test_without_keys_there_is_no_storage(
    session: FakeSession, make_settings: SettingsFactory
) -> None:
    async with open_object_storage(make_settings()) as opened:
        assert opened is None

    assert session.clients == []


async def test_requests_reuse_the_two_clients_opened_once(
    session: FakeSession, make_settings: SettingsFactory
) -> None:
    settings = make_settings(
        s3_endpoint_url=INTERNAL_URL,
        s3_public_url=PUBLIC_URL,
        s3_access_key="key",
        s3_secret_key="secret",
        s3_bucket="photos",
    )

    async with open_object_storage(settings) as opened:
        assert opened is not None
        for _ in range(3):
            await opened.put("a/1.jpg", b"jpeg", "image/jpeg")
            url = await opened.signed_get_url("a/1.jpg", timedelta(minutes=5))
            await opened.delete("a/1.jpg")

        internal, signer = session.clients
        assert [internal.endpoint_url, signer.endpoint_url] == [INTERNAL_URL, PUBLIC_URL]
        assert not internal.closed
        assert not signer.closed

    assert len(session.clients) == 2
    assert internal.closed
    assert signer.closed
    assert [name for name, _ in internal.calls] == ["put_object", "delete_object"] * 3
    assert internal.calls[0][1] == {
        "Bucket": "photos",
        "Key": "a/1.jpg",
        "Body": b"jpeg",
        "ContentType": "image/jpeg",
    }
    # Links are signed for the public address only; the signer never stores or deletes.
    assert url == f"{PUBLIC_URL}/signed"
    signed = (
        "get_object",
        {
            "Params": {"Bucket": "photos", "Key": "a/1.jpg", "ResponseCacheControl": "no-store"},
            "ExpiresIn": 300,
        },
    )
    assert signer.calls == [signed] * 3


async def test_a_call_waits_for_a_free_connection_only_as_long_as_for_a_connect(
    session: FakeSession, make_settings: SettingsFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    # aiohttp itself would queue a call for a pooled connection with no time limit.
    monkeypatch.setattr(storage, "_CONNECT_TIMEOUT", 0.05)
    settings = make_settings(s3_access_key="key", s3_secret_key="secret")

    async with open_object_storage(settings) as opened:
        assert opened is not None
        internal = session.clients[0]
        internal.responding.clear()
        stalled = [
            asyncio.create_task(opened.put(f"a/{n}.jpg", b"jpeg", "image/jpeg"))
            for n in range(storage._MAX_CONNECTIONS)
        ]
        await asyncio.sleep(0)

        with pytest.raises(TimeoutError):
            await opened.put("a/queued.jpg", b"jpeg", "image/jpeg")
        with pytest.raises(TimeoutError):
            await opened.delete("a/queued.jpg")
        # Signing sends nothing, so a stalled storage does not hold links up.
        await opened.signed_get_url("a/1.jpg", timedelta(minutes=5))

        internal.responding.set()
        await asyncio.gather(*stalled)
        await opened.delete("a/1.jpg")

    assert len(internal.calls) == storage._MAX_CONNECTIONS + 1


def test_the_connection_cap_is_the_client_pool_size() -> None:
    # The waiting limit in S3ObjectStorage only works if no call ever queues inside aiohttp.
    # The stubs of botocore Config do not declare the option.
    pool_size = vars(storage._CLIENT_CONFIG)["max_pool_connections"]
    assert pool_size == storage._MAX_CONNECTIONS
