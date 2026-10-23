"""FastAPI dependencies other modules use to know who is calling."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from flagman.core.resources import Resources, get_resources
from flagman.identity.domain import (
    AccessTokenInvalidError,
    StaffRole,
    StaffRoleForbiddenError,
    read_access_token,
    read_staff_access_token,
)

# auto_error=False: a missing header gets our problem+json 401, not FastAPI's own 403.
_bearer = HTTPBearer(
    auto_error=False,
    description=(
        "Access token: a client's from `POST /auth/otp/verify`, "
        "a staff member's from `POST /staff/auth/totp`."
    ),
)


@dataclass(frozen=True)
class CurrentClient:
    user_id: UUID
    session_id: UUID


async def get_current_client(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    resources: Annotated[Resources, Depends(get_resources)],
) -> CurrentClient:
    """Use as `client: Annotated[CurrentClient, Depends(get_current_client)]`.

    Only the token is checked, not the database: a revoked session keeps working until its
    access token expires (at most 15 minutes), see docs/adr/0009.
    """
    if credentials is None:
        raise AccessTokenInvalidError
    claims = read_access_token(
        credentials.credentials, resources.settings.api_jwt_secret.get_secret_value()
    )
    return CurrentClient(user_id=claims.user_id, session_id=claims.session_id)


@dataclass(frozen=True)
class CurrentStaff:
    staff_id: UUID
    role: StaffRole


async def get_current_staff(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    resources: Annotated[Resources, Depends(get_resources)],
) -> CurrentStaff:
    """Any signed-in staff member; endpoints of particular roles use `require_staff_role`.

    Only the token is checked, as for clients: a disabled account or a new role takes effect
    within the access token lifetime (15 minutes), see docs/adr/0011.
    """
    if credentials is None:
        raise AccessTokenInvalidError
    claims = read_staff_access_token(
        credentials.credentials, resources.settings.api_jwt_secret.get_secret_value()
    )
    return CurrentStaff(staff_id=claims.staff_id, role=claims.role)


def require_staff_role(*roles: StaffRole) -> Callable[[CurrentStaff], Awaitable[CurrentStaff]]:
    """Use as `dependencies=[Depends(require_staff_role(StaffRole.ADMIN))]` on the route."""
    allowed = frozenset(roles)

    async def check(staff: Annotated[CurrentStaff, Depends(get_current_staff)]) -> CurrentStaff:
        if staff.role not in allowed:
            raise StaffRoleForbiddenError
        return staff

    return check
