"""The local stack of a run: its own Compose project, ports, database and secrets."""

import base64
import secrets
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
COMPOSE_FILE = REPO_ROOT / "infra" / "docker-compose.yml"
PROJECT = "flagman-load"
API_PORT = 18000
BASE_URL = f"http://127.0.0.1:{API_PORT}"
WS_URL = f"ws://127.0.0.1:{API_PORT}/admin/fleet/stream"
# The staff stream closes sockets from any origin but the listed ones.
ORIGIN = "http://load.localhost"
_UP_TIMEOUT_S = 180


def _key() -> str:
    return base64.b64encode(secrets.token_bytes(32)).decode("ascii")


def local_env(car_count: int, phones: Sequence[str], otp_code: str) -> dict[str, str]:
    return {
        "API_ENV": "dev",
        "API_PORT": str(API_PORT),
        "API_CORS_ORIGINS": ORIGIN,
        "API_JWT_SECRET": secrets.token_hex(32),
        "API_OTP_PEPPER": secrets.token_hex(32),
        "API_TOTP_ENCRYPTION_KEY": _key(),
        # Each virtual user sends its own X-Forwarded-For, otherwise all of them share one IP and
        # its sign-in limit. Safe here only: the ports are bound to 127.0.0.1 and live for one run.
        "FORWARDED_ALLOW_IPS": "*",
        "SMS_BACKEND": "fixed",
        "SMS_FIXED_CODE": otp_code,
        "SMS_FIXED_PHONES": ",".join(phones),
        "POSTGRES_USER": "flagman",
        "POSTGRES_PASSWORD": secrets.token_hex(24),
        "POSTGRES_DB": "flagman",
        "POSTGRES_PORT": "15432",
        "REDIS_PASSWORD": secrets.token_hex(24),
        "REDIS_PORT": "16379",
        "S3_ACCESS_KEY": secrets.token_hex(16),
        "S3_SECRET_KEY": secrets.token_hex(32),
        "S3_BUCKET": "flagman-private",
        "S3_PORT": "18333",
        "PAYMENTS_PROVIDER": "fake",
        "PAYMENTS_WEBHOOK_SECRET": secrets.token_hex(32),
        "PAYMENTS_TOKEN_ENCRYPTION_KEY": _key(),
        "NOTIFICATIONS_PROVIDER": "fake",
        "EMULATOR_COMMAND_SECRET": secrets.token_hex(32),
        "EMULATOR_TELEMETRY_SECRET": secrets.token_hex(32),
        "EMULATOR_CAR_COUNT": str(car_count),
        "EMULATOR_SEED": "42",
        "EMULATOR_PORT": "18100",
        # A rented car starts driving after its rest and cannot end the rental then. Frozen cars
        # still report on schedule, so the telemetry load is the same.
        "EMULATOR_SIMULATION_ENABLED": "false",
    }


class Stack:
    def __init__(self, env_file: Path) -> None:
        self._env_file = env_file

    def write_env(self, env: Mapping[str, str]) -> None:
        lines = "".join(f"{key}={value}\n" for key, value in env.items())
        self._env_file.write_text(lines, encoding="utf-8")

    def compose(self, *args: str, stdin: str | None = None) -> None:
        subprocess.run(self._command(*args), input=stdin, text=True, check=True, cwd=REPO_ROOT)

    def up(self) -> None:
        # A stack left by --keep or a closed terminal has a database with another run's password.
        self.down()
        self.compose("build", "api", "car-emulator")
        self.compose("run", "--rm", "-T", "api", "alembic", "upgrade", "head")
        # In this order: tariffs go to the car models the fleet seed creates.
        for command in ("flagman-geo", "flagman-fleet", "flagman-pricing"):
            self.compose("run", "--rm", "-T", "api", command, "seed")
        self.compose(
            "up",
            "-d",
            "--wait",
            "--wait-timeout",
            str(_UP_TIMEOUT_S),
            "api",
            "worker",
            "car-emulator",
        )

    def ensure_operator(self, email: str, password: str, totp_secret: str) -> None:
        # Through stdin: arguments end up in the process list.
        self.compose(
            "run",
            "--rm",
            "-T",
            "api",
            "flagman-staff",
            "ensure",
            "--email",
            email,
            "--role",
            "operator",
            stdin=f"{password}\n{totp_secret}\n",
        )

    def save_logs(self, path: Path) -> None:
        with path.open("w", encoding="utf-8") as file:
            subprocess.run(
                self._command(
                    "logs", "--no-color", "--timestamps", "api", "worker", "car-emulator"
                ),
                stdout=file,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
                cwd=REPO_ROOT,
            )

    def down(self) -> None:
        self.compose("down", "--volumes", "--remove-orphans")

    def _command(self, *args: str) -> list[str]:
        return [
            "docker",
            "compose",
            "--project-name",
            PROJECT,
            "--env-file",
            str(self._env_file),
            "--file",
            str(COMPOSE_FILE),
            *args,
        ]
