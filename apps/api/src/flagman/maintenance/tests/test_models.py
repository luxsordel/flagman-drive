from flagman.maintenance.domain import OPEN_STATUSES
from flagman.maintenance.models import _OPEN, _listed


def test_open_index_condition_lists_the_open_statuses() -> None:
    assert f"status IN ({_listed(OPEN_STATUSES)})" == _OPEN
