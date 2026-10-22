"""Writes the API's OpenAPI schema to a file, for generating `packages/api-client`.

Builds the app without connecting to Postgres or Redis (the lifespan is not started), so it runs
in CI and on a clean machine. The settings are placeholders and never read the real `.env`.
"""

import json
import sys
from pathlib import Path

from pydantic import SecretStr

from flagman.core.app import create_app
from flagman.core.config import Settings

# Never used to connect anywhere; only needed to satisfy the required settings.
_PLACEHOLDER_SETTINGS = Settings(
    _env_file=None,
    api_env="dev",
    postgres_user="openapi",
    postgres_password=SecretStr("openapi"),
    postgres_db="openapi",
    redis_password=SecretStr("openapi"),
)


def render_schema() -> str:
    schema = create_app(_PLACEHOLDER_SETTINGS).openapi()
    # Sorted keys and a fixed layout keep the file byte-stable: a diff means the contract changed.
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str]) -> int:
    if len(argv) != 2:  # noqa: PLR2004 - script name + output path
        sys.stderr.write("usage: dump_openapi.py <output.json>\n")
        return 2
    # newline="" keeps LF on Windows (see .gitattributes).
    with Path(argv[1]).open("w", encoding="utf-8", newline="") as file:
        file.write(render_schema())
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
