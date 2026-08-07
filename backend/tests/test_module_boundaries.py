"""Our packages against the ownership tree in `docs/team-architecture.md`.

Owner: Jerome & Richard

The architecture's hardest rule is that the backend must not reproduce the Router's scoring, graph
propagation, capacity enforcement, or hysteresis — and reproducing them starts with importing
them. The Router is reached through the `RouterPort` protocol precisely so that no owned module
ever needs `backend.router`.

Ownership is parsed from the tracked document rather than listed here, so a package changing hands
changes what these cases assert instead of quietly disagreeing with them.
"""

from __future__ import annotations

import ast
from pathlib import Path

from backend.tests.tracked_documents import (
    JEROME_AND_RICHARD,
    REPO_ROOT,
    backend_packages_by_owner,
)


def imported_modules(source: Path) -> set[str]:
    """Every module named by an `import` or `from ... import` in one file."""
    tree = ast.parse(source.read_text())
    named: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            named.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            named.add(node.module)
    return named


def owned_packages() -> set[str]:
    return backend_packages_by_owner()[JEROME_AND_RICHARD]


def other_owners_packages() -> set[str]:
    grouped = backend_packages_by_owner()
    return {
        package
        for owner, packages in grouped.items()
        if owner != JEROME_AND_RICHARD
        for package in packages
    }


def owned_sources() -> list[Path]:
    return sorted(
        path
        for package in owned_packages()
        for path in (REPO_ROOT / "backend" / package).rglob("*.py")
    )


def test_the_tracked_tree_still_assigns_the_packages_these_cases_assume() -> None:
    """If this fails, ownership moved and the cases below are asserting the wrong thing."""
    grouped = backend_packages_by_owner()

    assert grouped[JEROME_AND_RICHARD] >= {"ingestion", "orchestration", "context", "models"}
    assert other_owners_packages() >= {"router", "telemetry"}


def test_the_ownership_tree_is_read_rather_than_restated() -> None:
    """The parse has to actually find entries; an empty map would pass every case below."""
    grouped = backend_packages_by_owner()

    assert len(grouped) >= 2, "expected at least two distinct owners in the tracked tree"


def test_no_owned_module_imports_another_owners_package() -> None:
    """Reported together rather than one case per file, so a spreading violation reads as one
    finding with every site listed instead of eighty-odd separate failures."""
    forbidden = {f"backend.{package}" for package in other_owners_packages()}

    violations = {
        str(source.relative_to(REPO_ROOT)): sorted(reached)
        for source in owned_sources()
        if (
            reached := {
                module
                for module in imported_modules(source)
                for prefix in forbidden
                if module == prefix or module.startswith(f"{prefix}.")
            }
        )
    }

    assert violations == {}, (
        "docs/team-architecture.md assigns these packages to another owner, and the Router is "
        f"meant to be reached through RouterPort: {violations}"
    )


def test_every_owned_source_is_actually_inspected() -> None:
    """The case above passes trivially if the file walk finds nothing."""
    inspected = owned_sources()

    assert len(inspected) > 50, f"only {len(inspected)} owned sources found; the walk is broken"
    assert any(path.name == "generation_coordinator.py" for path in inspected)


def test_the_router_is_reached_through_the_port_rather_than_the_implementation() -> None:
    """The seam that makes the case above satisfiable rather than merely true today."""
    port = REPO_ROOT / "backend" / "orchestration" / "router_port.py"

    assert port.is_file()
    assert "backend.router" not in port.read_text()
