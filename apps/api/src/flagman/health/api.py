from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, Depends, Response

from flagman.core.resources import Resources, get_resources
from flagman.health import service
from flagman.health.schemas import CheckStatus, LivenessResponse, ReadinessResponse

router = APIRouter(tags=["health"])


@router.get("/health", summary="Liveness: the process is up")
async def health() -> LivenessResponse:
    return LivenessResponse(status=CheckStatus.OK)


@router.get(
    "/ready",
    summary="Readiness: the database and Redis are reachable",
    responses={HTTPStatus.SERVICE_UNAVAILABLE: {"model": ReadinessResponse}},
)
async def ready(
    response: Response, resources: Annotated[Resources, Depends(get_resources)]
) -> ReadinessResponse:
    result = await service.check_readiness(
        resources.engine, resources.redis, resources.settings.readiness_timeout_seconds
    )
    if result.status is not CheckStatus.OK:
        response.status_code = HTTPStatus.SERVICE_UNAVAILABLE
    return result
