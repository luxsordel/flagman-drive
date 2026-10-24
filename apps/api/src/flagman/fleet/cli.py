"""`flagman-fleet`: demo cars for the pilot until the admin panel can manage the fleet.

    flagman-fleet seed

Adds the demo car models and EMULATOR_CAR_COUNT cars whose telematics ids match the emulator
started with the same EMULATOR_SEED. Running it again adds only what is missing.
"""

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from typing import TextIO

from flagman.core.config import load_settings
from flagman.core.db import create_engine, create_session_factory
from flagman.fleet import service
from flagman.fleet.domain import CarIdentityTakenError

EXIT_OK = 0
EXIT_FAILED = 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="flagman-fleet", description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("seed", help="add the demo models and cars matched to the emulator")
    return parser


async def _seed(stdout: TextIO) -> int:
    settings = load_settings()
    # Made-up cars in a production database would be offered to real clients.
    if settings.is_prod:
        sys.stderr.write("error: the demo fleet is not seeded when API_ENV=prod\n")
        return EXIT_FAILED
    engine = create_engine(settings)
    try:
        async with create_session_factory(engine)() as db:
            result = await service.seed_demo_fleet(
                db, settings.emulator_seed, settings.emulator_car_count, datetime.now(UTC)
            )
    except CarIdentityTakenError:
        sys.stderr.write(
            "error: the demo cars are already there with other telematics ids, seeded for another "
            "EMULATOR_SEED; nothing was added. Reset the database and seed again "
            "(see the demo fleet section of apps/api/README.md).\n"
        )
        return EXIT_FAILED
    finally:
        await engine.dispose()
    stdout.write(
        f"Car models created: {result.models_created}. "
        f"Cars created: {result.cars_created}, already there: {result.cars_existing}.\n"
    )
    return EXIT_OK


def main(argv: list[str] | None = None, stdout: TextIO = sys.stdout) -> int:
    args = _parser().parse_args(argv)
    match args.command:
        case "seed":
            return asyncio.run(_seed(stdout))
    return EXIT_FAILED


if __name__ == "__main__":
    raise SystemExit(main())
