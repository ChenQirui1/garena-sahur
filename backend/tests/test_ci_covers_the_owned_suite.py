"""The CI trigger against `pytest.ini`.

Owner: Jerome & Richard

The workflow added in #45 is filtered by `paths`, and its first omission was found the hard way:
`backend/tests` is in `testpaths` but was missing from the filter, so the pull request that added
these very cases changed only files CI did not watch and got no run at all. Nothing failed — the
check simply never reported, which is the quietest way for a check to be useless.

These cases apply to our own tooling the rule the rest of this directory applies to the team's
documents: derive rather than restate. Both files are parsed, and neither is allowed to name a
path the other does not.
"""

from __future__ import annotations

import configparser
import re

from backend.tests.tracked_documents import REPO_ROOT

WORKFLOW = REPO_ROOT / ".github" / "workflows" / "backend-owned-suite.yml"
PYTEST_INI = REPO_ROOT / "pytest.ini"

_QUOTED_GLOB = re.compile(r"^\s+- '([^']+)'\s*$", re.M)


def collected_test_paths() -> list[str]:
    parsed = configparser.ConfigParser()
    parsed.read(PYTEST_INI)
    return parsed["pytest"]["testpaths"].split()


def watched_globs() -> set[str]:
    return set(_QUOTED_GLOB.findall(WORKFLOW.read_text()))


def is_watched(path: str, globs: set[str]) -> bool:
    """A directory is watched when some glob covers it or everything beneath it."""
    return any(
        glob == path or glob == f"{path}/**" or path.startswith(glob.removesuffix("/**") + "/")
        for glob in globs
    )


def test_the_workflow_watches_every_path_the_owned_suite_collects() -> None:
    """Otherwise a change lands with no hosted run and nothing says so."""
    globs = watched_globs()

    unwatched = [path for path in collected_test_paths() if not is_watched(path, globs)]

    assert unwatched == [], (
        f"pytest.ini collects {unwatched}, which the CI paths filter does not watch, so a change "
        f"confined to those paths would merge without a hosted run. Watched: {sorted(globs)}"
    )


def test_the_workflow_watches_its_own_definition_and_its_dependencies() -> None:
    """A change to the trigger or the pinned dependencies has to re-run the suite."""
    globs = watched_globs()

    assert "pytest.ini" in globs
    assert "requirements.txt" in globs
    assert ".github/workflows/backend-owned-suite.yml" in globs


def test_both_of_the_workflows_path_filters_are_the_same() -> None:
    """GitHub Actions has no YAML anchors, so the pull-request and push lists are duplicated.

    Duplicated lists drift. This is the cheapest thing that notices.
    """
    lists = re.findall(r"paths:\n((?:\s+- '[^']+'\n)+)", WORKFLOW.read_text())

    assert len(lists) == 2, f"expected a filter on pull_request and on push, found {len(lists)}"
    assert lists[0] == lists[1], "the pull-request and push path filters have drifted apart"


def test_the_suite_is_not_run_by_naming_directories_in_the_workflow() -> None:
    """`pytest.ini` decides collection; a workflow that lists directories would outrank it.

    Listing them there is how another owner's scaffolding eventually gets swept into a green tick.
    """
    workflow = WORKFLOW.read_text()

    assert re.search(r"^\s+- run: python -m pytest\s*$", workflow, re.M), (
        "the workflow must invoke pytest with no path arguments"
    )
