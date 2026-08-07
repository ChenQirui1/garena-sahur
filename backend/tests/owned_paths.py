"""What this clone owns, derived rather than listed.

Owner: Jerome & Richard

`AGENTS.md` carries the authoritative ownership list, but it is excluded from Git in this clone,
so a case reading it passes here and dies on a CI checkout. Ownership therefore comes from two
places a hosted run can actually read: the tracked ownership tree in `docs/team-architecture.md`,
and the `Owner:` line every source file in this repository carries — which is the only statement
of ownership for a file the tracked tree does not name, such as a script added since it was
written.

This sits beside `tracked_documents` rather than inside it because it reaches for the filesystem,
and that module's contract is that it only ever parses a tracked document.
"""

from __future__ import annotations

from pathlib import Path

from backend.tests.tracked_documents import (
    JEROME_AND_RICHARD,
    REPO_ROOT,
    tracked_paths_by_owner,
)

# The tree writes owners in upper case; a module docstring writes the same owner as prose.
OWNER_MARKER = "Owner: Jerome & Richard"


def tracked_owned_paths() -> set[str]:
    """Repository-relative paths the tracked tree assigns to Jerome & Richard alone.

    Shared and jointly owned paths are excluded deliberately: `tests/test_end_to_end.py` names
    us among five owners, and a change there is not ours to land on our own hosted run.
    """
    return set(tracked_paths_by_owner()[JEROME_AND_RICHARD])


def owned_scripts() -> list[Path]:
    """Our execution entry points, including any the tracked tree predates."""
    return sorted(
        path
        for path in (REPO_ROOT / "scripts").glob("*.py")
        if OWNER_MARKER in path.read_text()
    )
