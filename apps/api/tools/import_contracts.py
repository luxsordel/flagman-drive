"""Custom import-linter contract for the module boundary rule in docs/02-architecture.md.

The built-in contracts cannot express "a module may import its own `models` and `repository`,
but not another module's", because the allowed set depends on who is importing.
"""

from dataclasses import dataclass
from typing import cast

from grimp import ImportGraph
from importlinter.application import output
from importlinter.domain import fields
from importlinter.domain.contract import Contract, ContractCheck


@dataclass(frozen=True)
class Violation:
    importer: str
    imported: str
    line_number: int
    line_contents: str


def _feature_module(module: str, root: str) -> str | None:
    """`flagman.rentals.service` -> `rentals`; None for the root package itself."""
    parts = module.split(".")
    if parts[0] != root or len(parts) < 2:  # noqa: PLR2004 - root + module name
        return None
    return parts[1]


def _internal_part(module: str, internal_submodules: set[str]) -> str | None:
    """`flagman.rentals.models.car` -> `models` if `models` is internal."""
    parts = module.split(".")
    if len(parts) >= 3 and parts[2] in internal_submodules:  # noqa: PLR2004 - root.module.part
        return parts[2]
    return None


def find_violations(
    graph: ImportGraph, root_package: str, internal_submodules: set[str]
) -> list[Violation]:
    violations: list[Violation] = []
    for importer in sorted(graph.modules):
        importer_module = _feature_module(importer, root_package)
        if importer_module is None:
            continue
        for imported in sorted(graph.find_modules_directly_imported_by(importer)):
            imported_module = _feature_module(imported, root_package)
            if imported_module in (None, importer_module):
                continue
            if _internal_part(imported, internal_submodules) is None:
                continue
            details = graph.get_import_details(importer=importer, imported=imported)
            violations.extend(
                Violation(importer, imported, detail["line_number"], detail["line_contents"])
                for detail in details
            )
    return violations


class ForeignInternalsContract(Contract):
    """Forbids importing `<root>.<other module>.<internal submodule>` from another module."""

    root_package = fields.StringField()
    internal_submodules = fields.SetField(subfield=fields.StringField())

    def check(self, graph: ImportGraph, verbose: bool) -> ContractCheck:
        # Contract.__init__ replaces the field descriptors with the parsed option values.
        root_package = cast(str, self.root_package)
        internal_submodules = cast(set[str], self.internal_submodules)
        violations = find_violations(graph, root_package, internal_submodules)
        return ContractCheck(kept=not violations, metadata={"violations": violations})

    def render_broken_contract(self, check: ContractCheck) -> None:
        for violation in check.metadata["violations"]:
            output.print_error(
                f"{violation.importer} is not allowed to import {violation.imported}:"
            )
            output.indent_cursor()
            output.print_error(
                f"{violation.importer}:{violation.line_number}: {violation.line_contents}",
                bold=False,
            )
            output.new_line()
