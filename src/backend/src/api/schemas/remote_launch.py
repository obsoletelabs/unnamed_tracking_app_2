"""Remote launch v1 carries identifiers only, never commands, paths or URI templates."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StrictBool, model_validator


class LaunchSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LaunchDeviceWrite(LaunchSchema):
    enabled: StrictBool


class LaunchCreate(LaunchSchema):
    device_id: UUID
    game_id: UUID
    request_key: UUID


class LaunchResult(LaunchSchema):
    state: Literal["accepted", "failed"]
    failure_code: (
        Literal["game_not_found", "not_installed", "already_running", "declined", "launch_failed"]
        | None
    ) = None

    @model_validator(mode="after")
    def consistent_result(self) -> "LaunchResult":
        if (self.state == "failed") != (self.failure_code is not None):
            raise ValueError("Only a failed launch requires a failure code.")
        return self
