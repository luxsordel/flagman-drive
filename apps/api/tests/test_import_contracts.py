from grimp import ImportGraph

from tools.import_contracts import ForeignInternalsContract, Violation, find_violations


def _graph(*imports: tuple[str, str]) -> ImportGraph:
    graph = ImportGraph()
    for importer, imported in imports:
        graph.add_module(importer)
        graph.add_module(imported)
        graph.add_import(
            importer=importer,
            imported=imported,
            line_number=1,
            line_contents=f"import {imported}",
        )
    return graph


INTERNAL = {"models", "repository"}


def test_own_models_and_repository_are_allowed() -> None:
    graph = _graph(
        ("flagman.rentals.service", "flagman.rentals.models"),
        ("flagman.rentals.service", "flagman.rentals.repository"),
    )

    assert find_violations(graph, "flagman", INTERNAL) == []


def test_other_modules_service_and_core_are_allowed() -> None:
    graph = _graph(
        ("flagman.rentals.service", "flagman.fleet.service"),
        ("flagman.rentals.models", "flagman.core.db"),
    )

    assert find_violations(graph, "flagman", INTERNAL) == []


def test_other_modules_models_and_repository_are_forbidden() -> None:
    graph = _graph(
        ("flagman.rentals.service", "flagman.fleet.models"),
        ("flagman.rentals.api", "flagman.payments.repository.cards"),
    )

    assert find_violations(graph, "flagman", INTERNAL) == [
        Violation(
            "flagman.rentals.api",
            "flagman.payments.repository.cards",
            1,
            "import flagman.payments.repository.cards",
        ),
        Violation(
            "flagman.rentals.service", "flagman.fleet.models", 1, "import flagman.fleet.models"
        ),
    ]


def test_contract_reports_broken_on_violation() -> None:
    contract = ForeignInternalsContract(
        name="test",
        session_options={},
        contract_options={"root_package": "flagman", "internal_submodules": ["models"]},
    )

    kept = contract.check(_graph(("flagman.a.service", "flagman.a.models")), verbose=False)
    broken = contract.check(_graph(("flagman.a.service", "flagman.b.models")), verbose=False)

    assert kept.kept
    assert not broken.kept
