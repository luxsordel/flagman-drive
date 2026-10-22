"""FastAPI dependencies other modules use to tell `audit.service.record` where a call came from."""

from typing import Annotated
from uuid import UUID

from fastapi import Depends, Request

from flagman.audit.domain import RequestOrigin
from flagman.core.errors import get_request_id
from flagman.core.resources import Resources, get_resources
from flagman.core.security import keyed_hash


def get_request_origin(
    request: Request, resources: Annotated[Resources, Depends(get_resources)]
) -> RequestOrigin:
    """Use as `origin: Annotated[RequestOrigin, Depends(get_request_origin)]`."""
    # The address is personal data: only its keyed hash is stored, enough to see that two events
    # came from one address. Behind a proxy this is the proxy unless uvicorn trusts it.
    ip_hash = (
        keyed_hash(request.client.host, resources.settings.api_otp_pepper.get_secret_value())
        if request.client
        else None
    )
    request_id = get_request_id(request)
    return RequestOrigin(
        ip_hash=ip_hash, request_id=UUID(request_id) if request_id is not None else None
    )
