"""Read the team's tracked contract documents.

Owner: Jerome & Richard

Everything here parses a document rather than restating one. A test that restates a contract is a
second source of truth: it stays green while the document moves, and a passing check asserting
something false is worse than no check at all.

Only tracked documents may be read. `AGENTS.md`, `CODING_STANDARDS.md`, `CONTRIBUTING.md`,
`CONTEXT.md`, and `MAP.md` are excluded from Git in this clone, so a case reading one passes
locally and dies with `FileNotFoundError` on a CI checkout, where the file does not exist.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]

MESSAGE_SCHEMAS = REPO_ROOT / "docs" / "message_schemas.md"
TEAM_ARCHITECTURE = REPO_ROOT / "docs" / "team-architecture.md"

JEROME_AND_RICHARD = "JEROME & RICHARD"

_SECTION = re.compile(r"\n## ")
_JSON_BLOCK = re.compile(r"```json\n(.*?)\n```", re.S)
_TREE = re.compile(r"```text\n(.*?)\n```", re.S)

# One entry in the ownership tree: a directory or module at the first level under a top-level
# area, followed by the owner comment. Deeper entries are not needed — ownership in that document
# is assigned per package, and a package's owner governs everything beneath it.
_OWNED_ENTRY = re.compile(r"^│   [├└]── ([A-Za-z0-9_]+(?:\.py)?)/?\s+# (.+?)\s*$")

# Any entry at any depth, as its indent, name, and the owner comment when it carries one. Depth
# is what turns a bare name into a path: `main.py` and `run_backend.py` are both first-level
# entries, and only the area above them says which is `backend/` and which is `scripts/`.
_TREE_ENTRY = re.compile(r"^((?:│   )*)[├└]── ([A-Za-z0-9_.-]+)/?(?:\s+# (.+?))?\s*$")


def documented_payload(section: str, which: int = 0) -> dict[str, Any]:
    """The example payload under a `## ` heading, by the heading's opening text.

    `which` selects among several blocks in one section: `telemetry.record` documents a
    model-call record and a routing record under the same heading.
    """
    text = MESSAGE_SCHEMAS.read_text()
    for part in _SECTION.split(text):
        if part.startswith(section):
            blocks = _JSON_BLOCK.findall(part)
            if not blocks:
                raise AssertionError(f"section {section!r} has no JSON block")
            if which >= len(blocks):
                raise AssertionError(
                    f"section {section!r} has {len(blocks)} JSON blocks, wanted index {which}"
                )
            payload: dict[str, Any] = json.loads(blocks[which])
            return payload
    raise AssertionError(f"no section of {MESSAGE_SCHEMAS.name} starts with {section!r}")


def documented_keys(section: str, which: int = 0) -> set[str]:
    return set(documented_payload(section, which))


def documented_topics(direction: str) -> set[str]:
    """The transport topics the schema document's topic table lists for one direction.

    The table is the document's only statement of the topic names themselves; every section
    below it describes a payload. A topic we accept under another spelling would pass every
    payload comparison and still be unreachable from Ivan's publisher.
    """
    rows = re.findall(
        rf"^\|\s*{re.escape(direction)}\s*\|\s*`([^`]+)`\s*\|",
        MESSAGE_SCHEMAS.read_text(),
        re.M,
    )
    assert rows, f"no topic rows for {direction!r}; the table's shape has changed"
    return set(rows)


def owner_by_name() -> dict[str, str]:
    """Every package and root module the tracked ownership tree names, mapped to its owner."""
    tree = _TREE.search(TEAM_ARCHITECTURE.read_text())
    assert tree is not None, f"{TEAM_ARCHITECTURE.name} has no ownership tree"
    owners = {
        match.group(1): match.group(2)
        for match in (_OWNED_ENTRY.match(line) for line in tree.group(1).splitlines())
        if match is not None
    }
    assert owners, "parsed no ownership entries; the tree's shape has changed"
    return owners


def tracked_paths_by_owner() -> dict[str, set[str]]:
    """Every path the ownership tree names, at every depth, grouped by the owner beside it.

    Paths are repository-relative and carry no trailing slash, so a caller cannot tell a package
    from a module by shape alone — which is correct, because the document assigns both the same
    way and a caller that cares can ask the filesystem.
    """
    tree = _TREE.search(TEAM_ARCHITECTURE.read_text())
    assert tree is not None, f"{TEAM_ARCHITECTURE.name} has no ownership tree"

    grouped: dict[str, set[str]] = {}
    ancestors: list[str] = []
    for line in tree.group(1).splitlines():
        entry = _TREE_ENTRY.match(line)
        if entry is None:
            continue
        indent, name, owner = entry.groups()
        depth = len(indent) // 4
        ancestors[depth:] = [name]
        if owner is not None:
            grouped.setdefault(owner.strip(), set()).add("/".join(ancestors))

    assert grouped, "parsed no ownership entries; the tree's shape has changed"
    return grouped


def backend_packages_by_owner() -> dict[str, set[str]]:
    """Backend packages that exist on disk, grouped by the owner the document assigns them."""
    grouped: dict[str, set[str]] = {}
    for name, owner in owner_by_name().items():
        if (REPO_ROOT / "backend" / name).is_dir():
            grouped.setdefault(owner, set()).add(name)
    return grouped
