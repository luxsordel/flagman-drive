"""Pytest plugin: a test that starts a test container must carry the integration mark.

CI measures domain coverage with -m "not integration" on a promise of no database. It is a plugin
module rather than part of conftest.py because kyc/tests/conftest.py imports from the root
conftest, which loads that file a second time: state kept there would not be shared.
"""

from collections.abc import Generator

import pytest

# Starting any of these containers is what makes a test an integration test. A new fixture that
# starts a container belongs here too, wherever it is defined, and calls refuse_unmarked_test.
CONTAINER_FIXTURES = frozenset({"postgres_container", "redis_container", "seaweedfs"})

_running_test = pytest.StashKey[pytest.Item]()


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    # An unmarked test that asks for a container by argument, even through an autouse fixture.
    unmarked = [
        item.nodeid
        for item in items
        if item.get_closest_marker("integration") is None
        and CONTAINER_FIXTURES & set(getattr(item, "fixturenames", ()))
    ]
    if unmarked:
        shown = "\n  ".join(unmarked[:10])
        raise pytest.UsageError(
            f"{len(unmarked)} tests use Docker without the integration mark, e.g.:\n  {shown}"
        )


@pytest.hookimpl(wrapper=True)
def pytest_runtest_protocol(item: pytest.Item) -> Generator[None, object, object]:
    item.config.stash[_running_test] = item
    try:
        return (yield)
    finally:
        del item.config.stash[_running_test]


def refuse_unmarked_test(request: pytest.FixtureRequest) -> None:
    """Called first by every fixture in CONTAINER_FIXTURES.

    The collection guard cannot see a fixture asked for with request.getfixturevalue, so the
    container itself refuses to start for an unmarked test. The failure is cached with the session
    fixture, so later tests that need it fail too, naming the culprit. A container that an earlier
    integration test started is reused unchecked, but the unit run has no such test.
    """
    item = request.config.stash.get(_running_test, None)
    if item is not None and item.get_closest_marker("integration") is None:
        pytest.fail(
            f"{item.nodeid} started {request.fixturename} without the integration mark",
            pytrace=False,
        )
