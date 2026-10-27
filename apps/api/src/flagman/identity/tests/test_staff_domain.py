import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import jwt
import pyotp
import pytest
from cryptography.exceptions import InvalidTag

from flagman.identity.domain import (
    JWT_ALGORITHM,
    PASSWORD_MAX_LENGTH,
    PASSWORD_MIN_LENGTH,
    STAFF_ACCESS_TOKEN_TTL,
    STAFF_CHALLENGE_TTL,
    TOTP_INTERVAL_SECONDS,
    AccessClaims,
    AccessTokenInvalidError,
    PasswordPolicyError,
    StaffAccessClaims,
    StaffChallengeInvalidError,
    StaffRole,
    TotpSecretPolicyError,
    check_password_policy,
    decrypt_totp_secret,
    dummy_password_hash,
    encrypt_totp_secret,
    generate_totp_secret,
    hash_password,
    issue_access_token,
    issue_staff_access_token,
    issue_staff_challenge,
    match_totp,
    normalize_email,
    normalize_totp_secret,
    password_needs_rehash,
    read_access_token,
    read_staff_access,
    read_staff_access_token,
    read_staff_challenge,
    totp_provisioning_uri,
    totp_step,
    verify_password,
)

SECRET = "test-jwt-secret-" + "j" * 32
KEY = b"k" * 32
NOW = datetime(2026, 10, 23, 12, 0, 0, tzinfo=UTC)
STAFF_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")
SESSION_ID = uuid.UUID("00000000-0000-4000-8000-000000000002")


def _code(secret: str, at: datetime) -> str:
    return pyotp.TOTP(secret).at(at)


# Passwords


def test_password_hash_is_argon2id_and_verifies() -> None:
    password_hash = hash_password("correct horse battery")

    assert password_hash.startswith("$argon2id$")
    assert "correct horse battery" not in password_hash
    assert verify_password(password_hash, "correct horse battery")
    assert not verify_password(password_hash, "correct horse batterY")
    assert not password_needs_rehash(password_hash)


def test_garbage_hash_does_not_verify() -> None:
    assert not verify_password("not-an-argon2-hash", "whatever")


def test_dummy_hash_never_matches_a_guess() -> None:
    assert not verify_password(dummy_password_hash(), "")
    assert dummy_password_hash() == dummy_password_hash()


@pytest.mark.parametrize(
    "password",
    ["a" * (PASSWORD_MIN_LENGTH - 1), "a" * (PASSWORD_MAX_LENGTH + 1), " " * PASSWORD_MIN_LENGTH],
)
def test_password_policy_rejects(password: str) -> None:
    with pytest.raises(PasswordPolicyError):
        check_password_policy(password)


@pytest.mark.parametrize("password", ["a" * PASSWORD_MIN_LENGTH, "a" * PASSWORD_MAX_LENGTH])
def test_password_policy_accepts_the_bounds(password: str) -> None:
    check_password_policy(password)


def test_email_is_normalized() -> None:
    assert normalize_email("  Operator@FLAGMAN.by ") == "operator@flagman.by"


# TOTP


def test_current_code_matches_and_returns_its_step() -> None:
    secret = generate_totp_secret()

    assert match_totp(secret, _code(secret, NOW), NOW, None) == totp_step(NOW)


@pytest.mark.parametrize("shift", [-1, 1])
def test_neighbouring_step_is_accepted_for_clock_drift(shift: int) -> None:
    secret = generate_totp_secret()
    at = NOW + timedelta(seconds=shift * TOTP_INTERVAL_SECONDS)

    assert match_totp(secret, _code(secret, at), NOW, None) == totp_step(at)


@pytest.mark.parametrize("shift", [-2, 2])
def test_code_outside_the_window_is_rejected(shift: int) -> None:
    secret = generate_totp_secret()
    at = NOW + timedelta(seconds=shift * TOTP_INTERVAL_SECONDS)

    assert match_totp(secret, _code(secret, at), NOW, None) is None


def test_used_code_is_not_accepted_again() -> None:
    secret = generate_totp_secret()
    code = _code(secret, NOW)
    step = match_totp(secret, code, NOW, None)

    assert match_totp(secret, code, NOW, step) is None


def test_older_code_is_not_accepted_after_a_newer_one() -> None:
    secret = generate_totp_secret()
    earlier = NOW - timedelta(seconds=TOTP_INTERVAL_SECONDS)

    assert match_totp(secret, _code(secret, earlier), NOW, totp_step(NOW)) is None


def test_code_of_another_secret_is_rejected() -> None:
    assert match_totp(generate_totp_secret(), _code(generate_totp_secret(), NOW), NOW, None) is None


def test_provisioning_uri_names_the_issuer_and_the_account() -> None:
    secret = generate_totp_secret()
    uri = urlsplit(totp_provisioning_uri(secret, "operator@flagman.by"))

    assert (uri.scheme, uri.netloc) == ("otpauth", "totp")
    assert "operator%40flagman.by" in uri.path
    query = parse_qs(uri.query)
    assert query["secret"] == [secret]
    assert query["issuer"] == ["FLAGMAN Drive"]


# TOTP secret encryption


def test_totp_secret_round_trips_and_is_not_stored_in_clear() -> None:
    secret = generate_totp_secret()
    ciphertext = encrypt_totp_secret(secret, KEY, STAFF_ID)

    assert secret.encode() not in ciphertext
    assert decrypt_totp_secret(ciphertext, KEY, STAFF_ID) == secret
    # A fresh nonce every time: equal secrets do not give equal ciphertexts.
    assert encrypt_totp_secret(secret, KEY, STAFF_ID) != ciphertext


def test_ciphertext_copied_to_another_staff_member_does_not_decrypt() -> None:
    ciphertext = encrypt_totp_secret(generate_totp_secret(), KEY, STAFF_ID)

    with pytest.raises(InvalidTag):
        decrypt_totp_secret(ciphertext, KEY, uuid.uuid4())


def test_ciphertext_does_not_decrypt_with_another_key() -> None:
    ciphertext = encrypt_totp_secret(generate_totp_secret(), KEY, STAFF_ID)

    with pytest.raises(InvalidTag):
        decrypt_totp_secret(ciphertext, b"x" * 32, STAFF_ID)


# Tokens


def test_challenge_round_trips() -> None:
    now = datetime.now(UTC)
    first = read_staff_challenge(issue_staff_challenge(STAFF_ID, SECRET, now), SECRET)
    second = read_staff_challenge(issue_staff_challenge(STAFF_ID, SECRET, now), SECRET)

    assert first.staff_id == second.staff_id == STAFF_ID
    # Each challenge has an id of its own, so using one up does not affect another.
    assert first.challenge_id != second.challenge_id


def test_expired_challenge_is_rejected() -> None:
    issued = datetime.now(UTC) - STAFF_CHALLENGE_TTL - timedelta(seconds=1)

    with pytest.raises(StaffChallengeInvalidError):
        read_staff_challenge(issue_staff_challenge(STAFF_ID, SECRET, issued), SECRET)


def test_staff_access_tells_when_the_token_ends() -> None:
    claims = StaffAccessClaims(uuid.uuid4(), uuid.uuid4(), StaffRole.TECHNICIAN)
    now = datetime.now(UTC).replace(microsecond=0)
    token = issue_staff_access_token(claims, SECRET, now)

    access = read_staff_access(token, SECRET)

    assert access.claims == claims
    assert access.expires_at == now + STAFF_ACCESS_TOKEN_TTL


def test_staff_access_token_round_trips() -> None:
    claims = StaffAccessClaims(staff_id=STAFF_ID, session_id=SESSION_ID, role=StaffRole.OPERATOR)

    token = issue_staff_access_token(claims, SECRET, datetime.now(UTC))

    assert read_staff_access_token(token, SECRET) == claims


def test_tokens_of_one_kind_are_not_accepted_as_another() -> None:
    now = datetime.now(UTC)
    client = issue_access_token(AccessClaims(user_id=STAFF_ID, session_id=SESSION_ID), SECRET, now)
    staff = issue_staff_access_token(
        StaffAccessClaims(staff_id=STAFF_ID, session_id=SESSION_ID, role=StaffRole.ADMIN),
        SECRET,
        now,
    )
    challenge = issue_staff_challenge(STAFF_ID, SECRET, now)

    with pytest.raises(AccessTokenInvalidError):
        read_staff_access_token(client, SECRET)
    with pytest.raises(AccessTokenInvalidError):
        read_staff_access_token(challenge, SECRET)
    with pytest.raises(AccessTokenInvalidError):
        read_access_token(staff, SECRET)
    with pytest.raises(StaffChallengeInvalidError):
        read_staff_challenge(staff, SECRET)


def test_staff_token_with_unknown_role_is_rejected() -> None:
    now = int(datetime.now(UTC).timestamp())
    payload = {
        "iss": "flagman",
        "aud": "staff",
        "sub": str(STAFF_ID),
        "sid": str(SESSION_ID),
        "role": "superuser",
        "iat": now,
        "exp": now + 60,
    }

    with pytest.raises(AccessTokenInvalidError):
        read_staff_access_token(jwt.encode(payload, SECRET, algorithm=JWT_ALGORITHM), SECRET)


def test_staff_token_signed_with_another_secret_is_rejected() -> None:
    claims = StaffAccessClaims(staff_id=STAFF_ID, session_id=SESSION_ID, role=StaffRole.ADMIN)
    token = issue_staff_access_token(claims, "another-secret-" + "x" * 32, datetime.now(UTC))

    with pytest.raises(AccessTokenInvalidError):
        read_staff_access_token(token, SECRET)


def test_preset_totp_secret_is_accepted_as_authenticator_apps_show_it() -> None:
    secret = generate_totp_secret()
    grouped = " ".join(secret[i : i + 4] for i in range(0, len(secret), 4)).lower()

    assert normalize_totp_secret(grouped) == secret


@pytest.mark.parametrize(
    "value",
    ["", "not base32!", "JBSWY3DPEHPK3PXP"],  # the last one is 80 bits, a common docs example
)
def test_preset_totp_secret_must_be_base32_of_at_least_160_bits(value: str) -> None:
    with pytest.raises(TotpSecretPolicyError):
        normalize_totp_secret(value)
