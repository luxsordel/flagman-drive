"""What the prepared run hands to the Locust process. Holds tokens, deleted after the run."""

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TypedDict

STATE_ENV = "FLAGMAN_LOAD_STATE"
# Refreshed well before the 15 minutes of an access token.
ACCESS_TOKEN_REFRESH_AFTER_S = 600.0


class ZonePolygon(TypedDict):
    coordinates: list[list[list[float]]]


class MapZone(TypedDict):
    kind: str
    polygon: ZonePolygon


@dataclass
class ClientSession:
    ip: str
    access_token: str
    refresh_token: str
    issued_at: float = field(default_factory=time.time)

    def needs_refresh(self, now: float) -> bool:
        return now - self.issued_at > ACCESS_TOKEN_REFRESH_AFTER_S


@dataclass
class RunState:
    profile: str
    base_url: str
    ws_url: str
    origin: str
    # Locally each virtual user is its own client IP; the stand trusts only Caddy.
    send_client_ip: bool
    otp_code: str
    renters: list[ClientSession]
    map_clients: list[ClientSession]
    otp_phones: list[str]
    staff_tokens: list[str]
    zones: list[MapZone]
    summary_path: str

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(asdict(self)), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "RunState":
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["renters"] = [ClientSession(**s) for s in raw["renters"]]
        raw["map_clients"] = [ClientSession(**s) for s in raw["map_clients"]]
        return cls(**raw)
