from pathlib import Path

from flagman.maintenance import repository
from flagman.maintenance.domain import OPEN_STATUSES
from flagman.maintenance.models import OPEN_CONDITION, _listed


def test_open_index_condition_lists_the_open_statuses() -> None:
    assert f"status IN ({_listed(OPEN_STATUSES)})" == OPEN_CONDITION


def test_upsert_conflict_target_repeats_the_index_condition() -> None:
    assert f'text("{OPEN_CONDITION}")' in Path(repository.__file__).read_text(encoding="utf-8")
