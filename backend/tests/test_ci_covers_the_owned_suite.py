"""The CI trigger against `pytest.ini` and against everything we own.

Owner: Jerome & Richard

The workflow added in #45 is filtered by `paths`, and its first omission was found the hard way:
`backend/tests` is in `testpaths` but was missing from the filter, so the pull request that added
these very cases changed only files CI did not watch and got no run at all. Nothing failed — the
check simply never reported, which is the quietest way for a check to be useless.

Watching what the suite *collects* was the wrong rule, and it hid a second omission of the same
shape: the owned suite reads the shipped `data/npc_profiles.json` and `data/cached_dialogue.json`,
which are ours and which no `testpaths` entry covers, so either could have been edited into a
broken document with no hosted run. The rule is now the one the ownership tree states — every path
the team assigns to us is watched — so a path we acquire is covered by acquiring it.

These cases apply to our own tooling the rule the rest of this directory applies to the team's
documents: derive rather than restate. Every source is parsed, and none is allowed to name a path
another does not.
"""

from __future__ import annotations

import configparser
import re

from backend.tests.owned_paths import owned_scripts, tracked_owned_paths
from backend.tests.tracked_documents import REPO_ROOT

WORKFLOW = REPO_ROOT / ".github" / "workflows" / "backend-owned-suite.yml"
PYTEST_INI = REPO_ROOT / "pytest.ini"
MYPY_INI = REPO_ROOT / "backend" / "tests" / "mypy.ini"

# Where mypy would find a configuration without being told. Ours is deliberately not here.
AUTO_DISCOVERED = ("mypy.ini", ".mypy.ini", "setup.cfg", "pyproject.toml")

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


def test_the_workflow_watches_every_path_the_team_assigns_to_us() -> None:
    """Ownership is the rule, not collection: a data document is ours and no test path covers it.

    Derived from the tracked tree, so a package changing hands changes what this asserts. A path
    the tree names but that does not exist here yet is skipped — the tree describes the intended
    repository, and watching a path nothing can change proves nothing.
    """
    globs = watched_globs()

    unwatched = sorted(
        path
        for path in tracked_owned_paths()
        if (REPO_ROOT / path).exists() and not is_watched(path, globs)
    )

    assert unwatched == [], (
        f"the ownership tree assigns {unwatched} to us and the CI paths filter does not watch "
        f"them, so a change confined to those paths would merge without a hosted run"
    )


def test_the_workflow_watches_every_owned_script() -> None:
    """The tracked tree predates our newer scripts, so their own `Owner:` line is the source."""
    globs = watched_globs()

    unwatched = sorted(
        str(script.relative_to(REPO_ROOT))
        for script in owned_scripts()
        if not is_watched(str(script.relative_to(REPO_ROOT)), globs)
    )

    assert unwatched == [], f"owned scripts the CI paths filter does not watch: {unwatched}"


def test_the_workflow_watches_no_path_another_owner_holds() -> None:
    """A tick on their change would claim our suite covers work it does not exercise."""
    others = sorted(
        glob
        for glob in watched_globs()
        for held in [glob.removesuffix("/**")]
        if held in {"backend/router", "backend/telemetry", "dashboard", "minecraft-mod"}
        or held.startswith(("backend/router/", "backend/telemetry/", "tests/"))
    )

    assert others == [], f"the filter watches paths another owner holds: {others}"


def test_the_workflow_watches_its_own_definition_and_its_dependencies() -> None:
    """A change to the trigger, either tool's configuration, or the pins has to re-run it."""
    globs = watched_globs()

    assert "pytest.ini" in globs
    assert is_watched(str(MYPY_INI.relative_to(REPO_ROOT)), globs)
    assert "requirements.txt" in globs
    assert ".github/workflows/backend-owned-suite.yml" in globs


def test_both_of_the_workflows_path_filters_are_the_same() -> None:
    """GitHub Actions has no YAML anchors, so the pull-request and push lists are duplicated.

    Duplicated lists drift. This is the cheapest thing that notices.
    """
    lists = re.findall(r"paths:\n((?:\s+- '[^']+'\n)+)", WORKFLOW.read_text())

    assert len(lists) == 2, f"expected a filter on pull_request and on push, found {len(lists)}"
    assert lists[0] == lists[1], "the pull-request and push path filters have drifted apart"


def test_neither_tool_is_run_by_naming_directories_in_the_workflow() -> None:
    """The configuration files decide scope; a workflow that lists paths would outrank them.

    Listing them there is how another owner's scaffolding eventually gets swept into a green tick.
    """
    workflow = WORKFLOW.read_text()

    assert re.search(r"^\s+- run: python -m pytest\s*$", workflow, re.M), (
        "the workflow must invoke pytest with no path arguments"
    )
    # mypy is told which configuration to read, because ours is deliberately not where it
    # would look by itself. That is a configuration flag, not a path argument: `files` still
    # decides the scope.
    assert re.search(
        rf"^\s+- run: python -m mypy --config-file {re.escape(str(MYPY_INI.relative_to(REPO_ROOT)))}\s*$",
        workflow,
        re.M,
    ), "the workflow must invoke mypy with the owned configuration and no path arguments"


def type_checked_paths() -> set[str]:
    parsed = configparser.ConfigParser()
    parsed.read(MYPY_INI)
    return {path.strip() for path in parsed["mypy"]["files"].split(",")}


def test_the_type_check_covers_what_the_suite_covers() -> None:
    """A gate over a narrower scope than the suite is a gate with a hole in it.

    `mypy.ini`'s `files` is compared against `pytest.ini`'s `testpaths` rather than against a
    list here, so the two configurations cannot drift apart quietly. mypy legitimately covers
    more — the production modules a test imports — and only a shortfall is a finding.
    """
    checked = type_checked_paths()

    uncovered = [
        path
        for path in collected_test_paths()
        if not any(path == one or path.startswith(f"{one}/") for one in checked)
    ]

    assert uncovered == [], (
        f"pytest collects {uncovered} and mypy does not check it, so a type regression there "
        f"would pass the hosted run. mypy checks: {sorted(checked)}"
    )


def test_no_type_configuration_sits_where_mypy_would_find_it_by_itself() -> None:
    """Our profile must not govern a run we did not start.

    A configuration at the repository root is auto-discovered, so `mypy their_file.py` from this
    directory would apply our strict profile to Elson & Daniel's or Ivan's code — a
    repository-wide decision, which issue #36 records as not ours to take. Ours is passed with
    `--config-file` instead, so their runs behave exactly as they did before we adopted anything.
    """
    imposed = [
        name
        for name in AUTO_DISCOVERED
        if (REPO_ROOT / name).is_file() and "[mypy]" in (REPO_ROOT / name).read_text()
        or (REPO_ROOT / name).is_file() and "[tool.mypy]" in (REPO_ROOT / name).read_text()
    ]

    assert imposed == [], (
        f"{imposed} configures mypy where it is auto-discovered, so it governs every run started "
        f"from the repository root, including another owner's"
    )


def test_the_type_check_covers_every_owned_script() -> None:
    """Naming our scripts one by one is only safe if something notices the next one."""
    checked = type_checked_paths()

    unchecked = sorted(
        str(script.relative_to(REPO_ROOT))
        for script in owned_scripts()
        if str(script.relative_to(REPO_ROOT)) not in checked
    )

    assert unchecked == [], f"owned scripts mypy does not check: {unchecked}"


def test_the_type_check_reaches_no_path_another_owner_holds() -> None:
    """The counterpart of the paths-filter case, and the reason `scripts` is not named whole.

    A bare `scripts` swept in `run_benchmark.py` and `generate_charts.py`. Both are scaffolds
    today, so it was green; filled in, they would fail a check on a pull request that never
    touched them and that we could not fix without crossing an ownership boundary.
    """
    ours = {str(script.relative_to(REPO_ROOT)) for script in owned_scripts()}

    theirs = sorted(
        path
        for path in type_checked_paths()
        for candidate in [(REPO_ROOT / path)]
        if path in {"backend/router", "backend/telemetry", "tests", "scripts"}
        or (candidate.is_file() and path.startswith("scripts/") and path not in ours)
    )

    assert theirs == [], f"mypy is checking paths another owner holds: {theirs}"
