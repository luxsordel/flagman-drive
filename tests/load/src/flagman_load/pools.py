"""Test identities of a local run: fake numbers in a throwaway database, generated each time."""

import secrets

_PHONE_BASE = 1_000_000
_RUNS = "0123456789" + "9876543210"


def phones(count: int) -> list[str]:
    return [f"+37529{_PHONE_BASE + index}" for index in range(count)]


def client_ip(index: int) -> str:
    return f"10.{(index >> 16) & 255}.{(index >> 8) & 255}.{index & 255}"


def is_guessable_code(code: str) -> bool:
    """The API refuses these for SMS_BACKEND=fixed."""
    return len(set(code)) == 1 or code in _RUNS


def new_otp_code() -> str:
    while True:
        code = f"{secrets.randbelow(10**6):06d}"
        if not is_guessable_code(code):
            return code
