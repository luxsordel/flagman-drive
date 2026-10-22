from datetime import UTC, datetime
from http import HTTPStatus
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from flagman.core.db import get_session
from flagman.core.errors import RATE_LIMITED_RESPONSE, Problem
from flagman.core.ratelimit import RateLimiter, get_rate_limiter
from flagman.core.resources import Resources, get_resources
from flagman.identity.dependencies import CurrentClient, get_current_client
from flagman.kyc.domain import DocumentKind, DocumentTooLargeError
from flagman.kyc.images import ACCEPTED_CONTENT_TYPES, MAX_UPLOAD_BYTES
from flagman.kyc.ports import DocumentStorage, create_document_storage
from flagman.kyc.schemas import DocumentUrlOut, KycApplicationOut
from flagman.kyc.service import KycService

router = APIRouter(
    prefix="/kyc",
    tags=["kyc"],
    responses={
        HTTPStatus.UNAUTHORIZED: {
            "model": Problem,
            "description": "`urn:flagman:problem:access-token-invalid`: refresh or sign in.",
        }
    },
)


def get_document_storage(
    resources: Annotated[Resources, Depends(get_resources)],
) -> DocumentStorage:
    return create_document_storage(resources.settings)


def get_kyc_service(
    db: Annotated[AsyncSession, Depends(get_session)],
    limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
) -> KycService:
    return KycService(db=db, limiter=limiter)


# The client comes first in every signature: without a token the answer is 401, not 503.
Client = Annotated[CurrentClient, Depends(get_current_client)]
Service = Annotated[KycService, Depends(get_kyc_service)]
Storage = Annotated[DocumentStorage, Depends(get_document_storage)]

_LOCKED_RESPONSE: dict[str, Any] = {
    "model": Problem,
    "description": "`urn:flagman:problem:kyc-locked`: the application is under review or approved.",
}
_STORAGE_UNAVAILABLE_RESPONSE: dict[str, Any] = {
    "model": Problem,
    "description": "`urn:flagman:problem:document-storage-unavailable`.",
}
# The photo is the raw request body: a multipart form would be written to a temporary file in
# full before the size limit could stop it.
_PHOTO_BODY: dict[str, Any] = {
    "requestBody": {
        "required": True,
        "description": (
            f"The photo itself, at most {MAX_UPLOAD_BYTES // (1024 * 1024)} MB. "
            "The type is detected from the content; the stored copy is a JPEG without metadata."
        ),
        "content": {
            content_type: {"schema": {"type": "string", "format": "binary"}}
            for content_type in ACCEPTED_CONTENT_TYPES
        },
    }
}


async def _read_photo(request: Request) -> bytes:
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_UPLOAD_BYTES:
        raise DocumentTooLargeError
    body = bytearray()
    # Chunked uploads have no length up front, so the limit is also enforced while reading.
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_UPLOAD_BYTES:
            raise DocumentTooLargeError
    return bytes(body)


@router.get("/application", summary="The client's own application and uploaded documents")
async def get_application(client: Client, service: Service) -> KycApplicationOut:
    return await service.get_application(client.user_id)


@router.put(
    "/documents/{kind}",
    summary="Upload or replace a document photo",
    openapi_extra=_PHOTO_BODY,
    responses={
        HTTPStatus.CONFLICT: _LOCKED_RESPONSE,
        HTTPStatus.REQUEST_ENTITY_TOO_LARGE: {
            "model": Problem,
            "description": "`urn:flagman:problem:document-too-large`: too large.",
        },
        HTTPStatus.UNSUPPORTED_MEDIA_TYPE: {
            "model": Problem,
            "description": "`urn:flagman:problem:document-type-unsupported`: not a photo.",
        },
        HTTPStatus.TOO_MANY_REQUESTS: RATE_LIMITED_RESPONSE,
        HTTPStatus.SERVICE_UNAVAILABLE: _STORAGE_UNAVAILABLE_RESPONSE,
    },
)
async def upload_document(
    client: Client, kind: DocumentKind, request: Request, service: Service, storage: Storage
) -> KycApplicationOut:
    await service.count_upload(client.user_id)
    photo = await _read_photo(request)
    return await service.upload_document(storage, client.user_id, kind, photo, datetime.now(UTC))


@router.get(
    "/documents/{kind}/url",
    summary="A short-lived link to the client's own document photo",
    responses={
        HTTPStatus.NOT_FOUND: {
            "model": Problem,
            "description": "`urn:flagman:problem:kyc-document-not-found`.",
        },
        HTTPStatus.TOO_MANY_REQUESTS: RATE_LIMITED_RESPONSE,
        HTTPStatus.SERVICE_UNAVAILABLE: _STORAGE_UNAVAILABLE_RESPONSE,
    },
)
async def get_document_url(
    client: Client, kind: DocumentKind, service: Service, storage: Storage
) -> DocumentUrlOut:
    return await service.document_url(storage, client.user_id, kind)


@router.post(
    "/application/submit",
    summary="Send the application for review, also again after a rejection",
    responses={
        HTTPStatus.CONFLICT: {
            "model": Problem,
            "description": (
                "`urn:flagman:problem:kyc-incomplete`: documents are missing; "
                "`urn:flagman:problem:kyc-locked`: already under review or approved."
            ),
        },
    },
)
async def submit_application(client: Client, service: Service) -> KycApplicationOut:
    return await service.submit(client.user_id, datetime.now(UTC))
