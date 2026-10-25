"""`flagman-geo`: the demo zone for the pilot.

    flagman-geo seed

Adds one allowed zone inside the ring road of Minsk, over the area where the car emulator parks
its cars. Running it again changes nothing, also after the zone was edited in the admin panel.
"""

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from typing import TextIO

from flagman.core.config import load_settings
from flagman.core.db import create_engine, create_session_factory
from flagman.geo import service

EXIT_OK = 0
EXIT_FAILED = 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="flagman-geo", description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("seed", help="add the demo zone inside the ring road of Minsk")
    return parser


async def _seed(stdout: TextIO) -> int:
    settings = load_settings()
    # Production zones are drawn by an admin for the real service area, not guessed by a seed.
    if settings.is_prod:
        sys.stderr.write("error: the demo zone is not seeded when API_ENV=prod\n")
        return EXIT_FAILED
    engine = create_engine(settings)
    try:
        async with create_session_factory(engine)() as db:
            created = await service.seed_demo_zone(db, datetime.now(UTC))
    finally:
        await engine.dispose()
    stdout.write("Demo zone created.\n" if created else "Demo zone already there.\n")
    return EXIT_OK


def main(argv: list[str] | None = None, stdout: TextIO = sys.stdout) -> int:
    args = _parser().parse_args(argv)
    match args.command:
        case "seed":
            return asyncio.run(_seed(stdout))
    return EXIT_FAILED


if __name__ == "__main__":
    raise SystemExit(main())
