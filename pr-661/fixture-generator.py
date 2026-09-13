#!/usr/bin/env python3
"""Generate a TUI replay from each revision's real Docker execute result."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worktree", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def run_product_code(worktree: Path) -> object:
    sys.path.insert(0, str(worktree / "libs/vs-sandbox/src"))
    from vs_sandbox.docker_sandbox import DockerSandbox  # noqa: PLC0415

    with tempfile.TemporaryDirectory(prefix="pr661-sandbox-") as workspace:
        sandbox = DockerSandbox(host_workspace=workspace, image="fixture-only")
        sandbox._container_id = "fixture-container"  # noqa: SLF001
        sandbox._max_output_bytes = 80  # noqa: SLF001
        completed = subprocess.CompletedProcess(
            args=["docker", "exec"],
            returncode=1,
            stdout="x" * 200,
            stderr="fatal compiler error\n",
        )
        with patch("subprocess.run", return_value=completed):
            return sandbox.execute("failing compiler")


def event(
    sequence: int,
    kind: str,
    *,
    data: dict[str, object] | None = None,
    status: str | None = None,
    agent: bool = False,
) -> dict[str, object]:
    start = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)
    return {
        "protocol_version": 1,
        "sequence": sequence,
        "run_id": "pr-661-screenshot",
        "timestamp": (start + timedelta(milliseconds=sequence * 100)).isoformat().replace(
            "+00:00", "Z"
        ),
        "type": kind,
        "text": "",
        "diagnostic": None,
        "status": status,
        "round_label": "round-1-retry-1-implementer" if agent else None,
        "agent_kind": "implementer" if agent else None,
        "invocation_id": "pr661-implementer" if agent else None,
        "execution_id": "pr661-implementer" if agent else None,
        "chat_thread_id": None,
        "data": data,
    }


def build(result: object) -> list[dict[str, object]]:
    execution_started = {
        "kind": "agent_execution_started",
        "stage": "implementer",
        "attempt": 1,
        "system_prompt": "Run the compiler gate.",
        "user_prompt": "Compile the candidate.",
        "activity": {
            "kind": "agent_execution_activity_changed",
            "mode": "tool",
            "summary": "Running compiler gate",
            "tool": "sandbox",
        },
        "driver": "agentshim",
        "provider": "fixture",
        "model": "fixture",
    }
    events = [
        event(1, "server_started", status="active"),
        event(
            2,
            "server_ready",
            data={"kind": "server_ready", "socket_protocol": "jsonl"},
            status="active",
        ),
        event(
            3,
            "run_started",
            data={
                "kind": "run_started",
                "outer_loop": "agent",
                "input": "bounded stderr screenshot",
                "max_rounds": 1,
                "expected_roles": ["orchestrator", "implementer", "judge", "profiler"],
            },
            status="active",
        ),
        event(4, "agent_execution_started", data=execution_started, status="active", agent=True),
        event(
            5,
            "phase_started",
            data={"kind": "phase", "phase": "implement", "attempt": 1},
            status="active",
            agent=True,
        ),
    ]
    if hasattr(result, "stdout"):
        streams = (("stdout", result.stdout), ("stderr", result.stderr))
    else:
        streams = (("stdout", result.output),)
    sequence = 6
    for stream, content in streams:
        if not content:
            continue
        events.append(
            event(
                sequence,
                "subprocess_output",
                data={
                    "kind": "subprocess_output",
                    "process_id": "compiler-1",
                    "process_kind": "accuracy_checker",
                    "stream": stream,
                    "content": content,
                },
                status="active",
                agent=True,
            )
        )
        sequence += 1
    events.extend(
        [
            event(
                sequence,
                "agent_execution_finished",
                data={
                    "kind": "agent_execution_finished",
                    "result": None,
                    "error": "Compiler gate failed",
                },
                status="failed",
                agent=True,
            ),
            event(
                sequence + 1,
                "phase_finished",
                data={"kind": "phase", "phase": "implement", "attempt": 1},
                status="failed",
                agent=True,
            ),
            event(
                sequence + 2,
                "round_finished",
                data={
                    "kind": "round_finished",
                    "attempts": 1,
                    "judge_verdict": "fail",
                    "perf_metric": None,
                    "perf_unit": None,
                    "profile_skipped": True,
                },
                status="failed",
                agent=True,
            ),
            event(sequence + 3, "run_finished", status="failed"),
        ]
    )
    return events


def main() -> None:
    args = parse_args()
    result = run_product_code(args.worktree)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "\n".join(json.dumps(item, separators=(",", ":")) for item in build(result)) + "\n"
    )
    print(json.dumps({"output": str(args.output), "result": result.output}))


if __name__ == "__main__":
    main()
