from pathlib import Path

import pytest

from tools.container_guard import CONTAINER_FIXTURES

API_DIR = Path(__file__).parents[1]
KYC_CONFTEST = API_DIR / "src" / "flagman" / "kyc" / "tests" / "conftest.py"


@pytest.fixture
def guarded(pytester: pytest.Pytester) -> pytest.Pytester:
    """A fresh pytest project with the real guard and conftest files, laid out like apps/api.

    Every case stops before a container starts, so no Docker is needed.
    """
    copies = {
        "conftest.py": API_DIR / "conftest.py",
        "tools/__init__.py": API_DIR / "tools" / "__init__.py",
        "tools/container_guard.py": API_DIR / "tools" / "container_guard.py",
        "kyc/conftest.py": KYC_CONFTEST,
    }
    for target, source in copies.items():
        path = pytester.path / target
        path.parent.mkdir(exist_ok=True)
        path.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    pytester.makeini(
        "[pytest]\n"
        "addopts = --import-mode=importlib -p no:cacheprovider\n"
        "pythonpath = .\n"
        "markers =\n    integration: needs Docker\n"
    )
    return pytester


def test_collection_refuses_an_unmarked_test_of_any_container(guarded: pytest.Pytester) -> None:
    guarded.path.joinpath("kyc", "test_collected.py").write_text(
        "def test_unmarked(seaweedfs):\n    pass\n", encoding="utf-8"
    )

    result = guarded.runpytest_subprocess()

    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*1 tests use Docker without the integration mark*"])


def test_a_container_requested_at_runtime_needs_the_mark(guarded: pytest.Pytester) -> None:
    # The way an autouse fixture reaches a container, invisible to the collection guard; run as
    # CI measures domain coverage.
    tests = "".join(
        f"def test_{name}(request):\n    request.getfixturevalue({name!r})\n\n\n"
        for name in sorted(CONTAINER_FIXTURES)
    )
    guarded.path.joinpath("kyc", "test_runtime.py").write_text(
        tests + "def test_unit():\n    pass\n", encoding="utf-8"
    )

    result = guarded.runpytest_subprocess("-m", "not integration")

    result.assert_outcomes(passed=1, failed=len(CONTAINER_FIXTURES))
    result.stdout.fnmatch_lines_random(
        [
            f"*test_{name} started {name} without the integration mark*"
            for name in CONTAINER_FIXTURES
        ]
    )
