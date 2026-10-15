from typing import Literal

from pydantic import BaseModel


class ProbeRequest(BaseModel):
    scenario: Literal["status_429", "status_503", "timeout"]
