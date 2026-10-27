"""`flagman-pricing`: demo tariffs for the pilot.

    flagman-pricing seed

Gives every car model without a tariff the demo one (run `flagman-fleet seed` first). Running it
again changes nothing, also after a tariff was changed in the admin panel.
"""

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from typing import TextIO

from flagman.core.config import load_settings
from flagman.core.db import create_engine, create_session_factory
from flagman.pricing import service

EXIT_OK = 0
EXIT_FAILED = 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="flagman-pricing", description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("seed", help="give every car model without a tariff the demo one")
    return parser


async def _seed(stdout: TextIO) -> int:
    settings = load_settings()
    # Placeholder prices in a production database would be charged to real clients.
    if settings.is_prod:
        sys.stderr.write("error: the demo tariffs are not seeded when API_ENV=prod\n")
        return EXIT_FAILED
    engine = create_engine(settings)
    try:
        async with create_session_factory(engine)() as db:
            created = await service.seed_demo_tariffs(db, datetime.now(UTC))
    finally:
        await engine.dispose()
    stdout.write(f"Tariffs created: {created}.\n")
    return EXIT_OK


def main(argv: list[str] | None = None, stdout: TextIO = sys.stdout) -> int:
    args = _parser().parse_args(argv)
    match args.command:
        case "seed":
            return asyncio.run(_seed(stdout))
    return EXIT_FAILED


if __name__ == "__main__":
    raise SystemExit(main())
