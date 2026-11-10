from pathlib import Path

import pytest

from tools.compose_images import STAND_COMPOSE, compose_image

COMPOSE = """\
name: flagman

services:
  seaweedfs:
    image: chrislusf/seaweedfs:4.48
    command: server

# A comment between services.
  seaweedfs-init:
    depends_on:
      - seaweedfs
    image: amazon/aws-cli:2.0.0

  api:
    build:
      context: ../apps/api
    environment:
      image: not-an-image

volumes:
  api:
    image: not-a-service
"""


@pytest.fixture
def compose_file(tmp_path: Path) -> Path:
    path = tmp_path / "docker-compose.yml"
    path.write_text(COMPOSE, encoding="utf-8")
    return path


def test_the_image_of_a_service_is_read(compose_file: Path) -> None:
    assert compose_image("seaweedfs", compose_file) == "chrislusf/seaweedfs:4.48"


def test_a_service_whose_name_starts_like_another_gets_its_own_image(compose_file: Path) -> None:
    assert compose_image("seaweedfs-init", compose_file) == "amazon/aws-cli:2.0.0"


@pytest.mark.parametrize("service", ["api", "missing"])
def test_a_service_without_its_own_image_fails_loudly(compose_file: Path, service: str) -> None:
    # `api` has an `image` key only deeper in its block and under the top-level `volumes`.
    with pytest.raises(LookupError, match=service):
        compose_image(service, compose_file)


def test_the_local_compose_names_every_image_the_tests_start() -> None:
    for service in ("postgres", "redis", "seaweedfs"):
        assert ":" in compose_image(service)


def test_the_stand_backup_runs_the_same_image_as_the_database() -> None:
    # pg_dump must match the server version, and Dependabot bumps each compose file on its own.
    assert compose_image("backup", STAND_COMPOSE) == compose_image("postgres")
