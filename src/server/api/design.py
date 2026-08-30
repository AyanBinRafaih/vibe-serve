"""Project per-round design changes: files touched and stage conclusions.

The agent loop's round records are authoritative for stage outcomes; this
module copies them, never recomputes them. File lists are derived with
read-only ``git diff`` over each round's net commit range in the run's
workspace, filtered down to the system under optimization: the framework's
own bookkeeping paths (run state, roadmap, progress notes) are excluded.
"""

from __future__ import annotations

import re
import subprocess
from typing import TYPE_CHECKING, Literal

from server.api.protocol import DesignFileChange, DesignRound
from vibesys.schemas import derive_hypothesis_title

if TYPE_CHECKING:
    from pathlib import Path

    from vibesys.loops.agent.model import AgentRunState
    from vs_loop_state import RoundRecord

# Round checkpoints and the trusted baseline are recorded as commit hashes.
# Anything else (legacy placeholders, corrupt state) must not reach the git
# command line, where a leading "-" would read as an option.
_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{7,64}$")

# Top-level workspace names the framework writes into round commits, for both
# memory layouts (single files and directories, plus the artifact and Pareto
# archive siblings of a ``progress.md``).
_FRAMEWORK_TOP_LEVEL = frozenset(
    {
        ".vibesys",
        "roadmap",
        "roadmap.md",
        "progress",
        "progress.md",
        "progress-artifacts",
        "pareto-frontier.md",
    }
)

_GIT_TIMEOUT_SECONDS = 10.0

_CHANGE_BY_STATUS: dict[str, Literal["added", "modified", "deleted"]] = {
    "A": "added",
    "D": "deleted",
}


def build_design_log(state: AgentRunState, *, workspace: Path, baseline: str) -> list[DesignRound]:
    """Return one design entry per recorded round, in round order.

    Each round's file list covers exactly the range that round produced: a
    hypothesis's first round starts from the hypothesis's parent checkpoint
    (which differs from the chronologically previous round when the
    orchestrator reverted), every later round from the same hypothesis's
    previous round. ``baseline`` is the run manifest's trusted input
    baseline, the commit the run branched from, and anchors hypotheses that
    recorded no parent of their own.
    """
    titles = {
        hypothesis.hypothesis_id: _text(hypothesis.plan.title)
        or derive_hypothesis_title(hypothesis.plan.hypothesis)
        for hypothesis in state.hypotheses
    }
    chronological = _chronological_bases(state, baseline)
    entries: list[DesignRound] = []
    for hypothesis in state.hypotheses:
        previous = _commit(hypothesis.parent_commit)
        for record in hypothesis.rounds:
            base = previous if previous is not None else chronological.get(record.round_number)
            commit = _commit(record.commit)
            files = (
                _changed_files(workspace, base, commit)
                if base is not None and commit is not None
                else None
            )
            entries.append(_entry(record, titles, files))
            if commit is not None:
                previous = commit
    return sorted(entries, key=lambda entry: entry.round)


def _chronological_bases(state: AgentRunState, baseline: str) -> dict[int, str]:
    """Map each round to the newest checkpoint recorded before it.

    This is the fallback base for hypotheses without a recorded parent
    checkpoint (legacy runs): without a revert, a hypothesis's first round
    continues from wherever the run last left the workspace.
    """
    bases: dict[int, str] = {}
    previous = baseline if _COMMIT_PATTERN.fullmatch(baseline) else None
    for record in state.rounds:
        if previous is not None:
            bases[record.round_number] = previous
        commit = _commit(record.commit)
        if commit is not None:
            previous = commit
    return bases


def _entry(
    record: RoundRecord,
    titles: dict[str, str | None],
    files: list[DesignFileChange] | None,
) -> DesignRound:
    return DesignRound(
        round=record.round_number,
        commit=_text(record.commit),
        hypothesis_id=_text(record.hypothesis_id),
        title=titles.get(record.hypothesis_id or ""),
        claim=_text(record.hypothesis_claim),
        task=_text(record.hypothesis_task),
        passed=record.passed,
        hypothesis_outcome=_text(record.hypothesis_outcome),
        judge_verdict=record.judge_verdict,
        official_evaluation=record.official_evaluation,
        candidate_disposition=_text(record.candidate_disposition),
        perf_metric=record.perf_metric,
        perf_unit=_text(record.perf_unit),
        perf_delta_pct=record.perf_delta_pct,
        files=files,
    )


def _commit(value: str | None) -> str | None:
    return value if value is not None and _COMMIT_PATTERN.fullmatch(value) else None


def _changed_files(workspace: Path, base: str, commit: str) -> list[DesignFileChange] | None:
    """Diff two checkpoints read-only; None when the range does not resolve."""
    command = [
        "git",
        "-C",
        str(workspace),
        "diff",
        "--name-status",
        "--find-renames",
        "-z",
        base,
        commit,
    ]
    try:
        result = subprocess.run(  # noqa: S603
            command, capture_output=True, check=False, timeout=_GIT_TIMEOUT_SECONDS
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return _parse_name_status(result.stdout.decode("utf-8", errors="replace"))


def _parse_name_status(output: str) -> list[DesignFileChange]:
    """Parse NUL-delimited ``--name-status`` records into typed changes."""
    tokens = output.split("\0")
    changes: list[DesignFileChange] = []
    index = 0
    while index < len(tokens):
        status = tokens[index]
        if not status:
            break
        if status.startswith(("R", "C")) and index + 2 < len(tokens):
            renamed_from, path = tokens[index + 1], tokens[index + 2]
            index += 3
            change: Literal["added", "modified", "deleted", "renamed"] = (
                "renamed" if status.startswith("R") else "added"
            )
            if change != "renamed":
                renamed_from = ""
        elif index + 1 < len(tokens):
            path = tokens[index + 1]
            renamed_from = ""
            index += 2
            change = _CHANGE_BY_STATUS.get(status[:1], "modified")
        else:
            break
        if _is_framework_path(path):
            continue
        changes.append(
            DesignFileChange(path=path, change=change, renamed_from=renamed_from or None)
        )
    return changes


def _is_framework_path(path: str) -> bool:
    return path.split("/", 1)[0] in _FRAMEWORK_TOP_LEVEL


def _text(value: str | None) -> str | None:
    return value or None
