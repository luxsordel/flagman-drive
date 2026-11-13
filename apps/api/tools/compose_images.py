"""Image tags of the compose files, read for the test containers.

Dependabot bumps the images in infra/docker-compose.yml. Tests that kept their own copy of a tag
would go on testing the old version while developers and the stand run the new one.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
LOCAL_COMPOSE = REPO_ROOT / "infra" / "docker-compose.yml"
STAND_COMPOSE = REPO_ROOT / "infra" / "stand" / "compose.yml"

# The compose files are written by hand in one layout: services two spaces in, their keys four.
# A YAML parser would be one more direct dependency for one key.
# A service header may carry a comment or an anchor after the colon; missing one would hand the
# next service's image to the previous service.
_SERVICE = re.compile(r"^  (?P<name>[\w.-]+):(?:\s|$)")
_IMAGE = re.compile(r"^    image:\s*(?P<image>\S+)\s*$")


def compose_image(service: str, compose_file: Path = LOCAL_COMPOSE) -> str:
    """The `image:` of a service, failing loudly when the file no longer has it."""
    in_services = False
    current: str | None = None
    for line in compose_file.read_text(encoding="utf-8").splitlines():
        # Top-level keys switch sections: `volumes:` and `networks:` have entries laid out like
        # services. A comment switches nothing.
        if line.strip() and not line.startswith(("  ", "#")):
            in_services = line.rstrip() == "services:"
            current = None
        elif not in_services:
            continue
        elif service_match := _SERVICE.match(line):
            current = service_match["name"]
        elif current == service and (image_match := _IMAGE.match(line)):
            return image_match["image"]
    raise LookupError(f"no image for service {service!r} in {compose_file}")
