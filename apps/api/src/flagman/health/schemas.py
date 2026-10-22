from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class CheckStatus(StrEnum):
    OK = "ok"
    FAIL = "fail"


class LivenessResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: CheckStatus


class ReadinessResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: CheckStatus
    checks: dict[str, CheckStatus]
