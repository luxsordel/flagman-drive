"""`flagman-staff`: staff accounts until the admin panel can manage them (#32).

    flagman-staff create --email admin@flagman.by --role admin
    flagman-staff set-password --email admin@flagman.by
    flagman-staff reset-totp --email admin@flagman.by
    flagman-staff disable --email admin@flagman.by
    flagman-staff ensure --email operator@stand.test --role operator < password-and-2fa-secret

The password is asked for twice without echo, or read from stdin with --password-stdin. It is
never an argument: those end up in the shell history and in the process list.

`ensure` is for the demo stand seed (#123, docs/08-demo-stand.md): it reads the password and the
base32 2FA secret from the first two lines of stdin, creates the account with 2FA already set up
and leaves an existing account untouched, so it is safe to run on every deploy.
"""

import argparse
import asyncio
import getpass
import re
import sys
from datetime import UTC, datetime
from typing import TextIO

from flagman.core.config import load_settings
from flagman.core.db import create_engine, create_session_factory
from flagman.identity import service
from flagman.identity.domain import (
    EMAIL_MAX_LENGTH,
    EMAIL_PATTERN,
    STAFF_ACCESS_TOKEN_TTL,
    PasswordPolicyError,
    StaffAlreadyDisabledError,
    StaffEmailTakenError,
    StaffNotFoundError,
    StaffRole,
    TotpSecretPolicyError,
    normalize_email,
)

EXIT_OK = 0
EXIT_FAILED = 1

# Sessions end at once, but an access token already issued is not checked against the database.
_TOKENS_STILL_VALID = (
    f"Access tokens already issued keep working for up to "
    f"{int(STAFF_ACCESS_TOKEN_TTL.total_seconds()) // 60} minutes.\n"
)


def _email(value: str) -> str:
    # The same check as the sign-in API applies, so the account can actually sign in.
    if len(value) > EMAIL_MAX_LENGTH or not re.fullmatch(EMAIL_PATTERN, value):
        raise argparse.ArgumentTypeError("not an email address")
    return value


def _add_password_stdin(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "--password-stdin",
        action="store_true",
        help="read the password from the first line of stdin instead of asking for it",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="flagman-staff", description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)

    create = commands.add_parser("create", help="create a staff member; 2FA is set up at sign-in")
    create.add_argument("--email", required=True, type=_email)
    create.add_argument("--role", required=True, choices=[role.value for role in StaffRole])
    _add_password_stdin(create)

    set_password = commands.add_parser(
        "set-password", help="set a new password and end every session (forgotten or leaked)"
    )
    set_password.add_argument("--email", required=True)
    _add_password_stdin(set_password)

    reset = commands.add_parser(
        "reset-totp", help="forget the 2FA secret and end every session (lost phone, new key)"
    )
    reset.add_argument("--email", required=True)

    ensure = commands.add_parser(
        "ensure",
        help="create a staff member with a preset 2FA secret unless the email exists (stand seed)",
    )
    ensure.add_argument("--email", required=True, type=_email)
    ensure.add_argument("--role", required=True, choices=[role.value for role in StaffRole])

    disable = commands.add_parser(
        "disable", help="block sign-in and end every session (left the company, compromised)"
    )
    disable.add_argument("--email", required=True)
    return parser


def _read_password(from_stdin: bool, stdin: TextIO) -> str:
    if from_stdin:
        return stdin.readline().rstrip("\r\n")
    password = getpass.getpass("Password: ")
    if getpass.getpass("Repeat the password: ") != password:
        raise PasswordPolicyError("the passwords do not match")
    return password


async def _run(args: argparse.Namespace, stdin: TextIO, stdout: TextIO) -> int:
    settings = load_settings()
    engine = create_engine(settings)
    try:
        async with create_session_factory(engine)() as db:
            now = datetime.now(UTC)
            match args.command:
                case "create":
                    password = _read_password(args.password_stdin, stdin)
                    staff_id = await service.create_staff(
                        db, args.email, password, StaffRole(args.role), now
                    )
                    email = normalize_email(args.email)
                    stdout.write(f"Created {args.role} {staff_id} ({email}).\n")
                case "ensure":
                    account = service.StandStaff(
                        email=args.email,
                        password=stdin.readline().rstrip("\r\n"),
                        role=StaffRole(args.role),
                        totp_secret=stdin.readline().rstrip("\r\n"),
                    )
                    ensured = await service.ensure_staff(
                        db, account, settings.totp_encryption_key, now
                    )
                    email = normalize_email(args.email)
                    if ensured.created:
                        stdout.write(f"Created {args.role} {ensured.id} ({email}) with 2FA.\n")
                    else:
                        stdout.write(
                            f"Already there as {ensured.role.value} {ensured.id} ({email}); "
                            "nothing changed.\n"
                        )
                case "set-password":
                    password = _read_password(args.password_stdin, stdin)
                    staff_id = await service.set_staff_password(db, args.email, password, now)
                    stdout.write(f"New password set for {staff_id}; every session ended.\n")
                    stdout.write(_TOKENS_STILL_VALID)
                case "reset-totp":
                    staff_id = await service.reset_staff_totp(db, args.email, now)
                    stdout.write(f"2FA reset for {staff_id}; it is set up again at sign-in.\n")
                    stdout.write(_TOKENS_STILL_VALID)
                    # The setup needs only the password, see docs/adr/0011.
                    stdout.write("Suspected break-in? Run set-password as well.\n")
                case "disable":
                    try:
                        staff_id = await service.disable_staff(db, args.email, now)
                    except StaffAlreadyDisabledError:
                        # The goal is reached either way, so this is not an error.
                        stdout.write("Already disabled; nothing changed.\n")
                    else:
                        stdout.write(f"Disabled {staff_id}; every session ended.\n")
                        stdout.write(_TOKENS_STILL_VALID)
    finally:
        await engine.dispose()
    return EXIT_OK


def main(
    argv: list[str] | None = None, stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout
) -> int:
    args = _parser().parse_args(argv)
    try:
        return asyncio.run(_run(args, stdin, stdout))
    except (PasswordPolicyError, TotpSecretPolicyError) as error:
        sys.stderr.write(f"error: {error}\n")
    except StaffEmailTakenError:
        sys.stderr.write("error: a staff member with this email already exists\n")
    except StaffNotFoundError:
        sys.stderr.write("error: no staff member with this email\n")
    return EXIT_FAILED


if __name__ == "__main__":
    raise SystemExit(main())
