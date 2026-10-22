"""`flagman-staff`: staff accounts until the admin panel can manage them (#32).

    flagman-staff create --email admin@flagman.by --role admin
    flagman-staff reset-totp --email admin@flagman.by

The password is asked for twice without echo, or read from stdin with --password-stdin. It is
never an argument: those end up in the shell history and in the process list.
"""

import argparse
import asyncio
import getpass
import sys
from datetime import UTC, datetime
from typing import TextIO

from flagman.core.config import load_settings
from flagman.core.db import create_engine, create_session_factory
from flagman.identity import service
from flagman.identity.domain import (
    PasswordPolicyError,
    StaffEmailTakenError,
    StaffNotFoundError,
    StaffRole,
    normalize_email,
)

EXIT_OK = 0
EXIT_FAILED = 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="flagman-staff", description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)

    create = commands.add_parser("create", help="create a staff member; 2FA is set up at sign-in")
    create.add_argument("--email", required=True)
    create.add_argument("--role", required=True, choices=[role.value for role in StaffRole])
    create.add_argument(
        "--password-stdin",
        action="store_true",
        help="read the password from the first line of stdin instead of asking for it",
    )

    reset = commands.add_parser(
        "reset-totp", help="forget the 2FA secret and end every session (lost phone, new key)"
    )
    reset.add_argument("--email", required=True)
    return parser


def _read_password(from_stdin: bool, stdin: TextIO) -> str:
    if from_stdin:
        return stdin.readline().rstrip("\r\n")
    password = getpass.getpass("Password: ")
    if getpass.getpass("Repeat the password: ") != password:
        raise PasswordPolicyError("the passwords do not match")
    return password


async def _run(args: argparse.Namespace, stdin: TextIO, stdout: TextIO) -> int:
    engine = create_engine(load_settings())
    try:
        async with create_session_factory(engine)() as db:
            now = datetime.now(UTC)
            if args.command == "create":
                password = _read_password(args.password_stdin, stdin)
                staff_id = await service.create_staff(
                    db, args.email, password, StaffRole(args.role), now
                )
                stdout.write(f"Created {args.role} {staff_id} ({normalize_email(args.email)}).\n")
            else:
                staff_id = await service.reset_staff_totp(db, args.email, now)
                stdout.write(f"2FA reset for {staff_id}; it is set up again at the next sign-in.\n")
    finally:
        await engine.dispose()
    return EXIT_OK


def main(
    argv: list[str] | None = None, stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout
) -> int:
    args = _parser().parse_args(argv)
    try:
        return asyncio.run(_run(args, stdin, stdout))
    except PasswordPolicyError as error:
        sys.stderr.write(f"error: {error}\n")
    except StaffEmailTakenError:
        sys.stderr.write("error: a staff member with this email already exists\n")
    except StaffNotFoundError:
        sys.stderr.write("error: no staff member with this email\n")
    return EXIT_FAILED


if __name__ == "__main__":
    raise SystemExit(main())
