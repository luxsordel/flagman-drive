from flagman.rentals.domain import ACTIVE_STATUSES
from flagman.rentals.models import _ACTIVE, _listed


def test_active_index_condition_lists_the_active_statuses() -> None:
    assert f"status IN ({_listed(ACTIVE_STATUSES)})" == _ACTIVE
