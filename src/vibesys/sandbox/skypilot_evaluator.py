"""Sandbox-side client for the narrow SkyPilot evaluator bridge."""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
import socket
import sys
from pathlib import Path
from typing import TextIO

_PROTOCOL_VERSION = 1
_MAX_FRAME_BYTES = 1024 * 1024
_FRAMEWORK_ARGUMENT_COUNT = 2


def run_evaluator(  # noqa: C901, PLR0911, PLR0912, PLR0915
    kind: str,
    socket_path: Path,
    *,
    arguments: tuple[str, ...] = (),
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    """Request one trusted evaluator and relay its streamed output."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.connect(str(socket_path))
        artifacts: tuple[str, ...] = ()
        if arguments:
            if (
                len(arguments) != _FRAMEWORK_ARGUMENT_COUNT
                or not arguments[0].startswith("-")
                or any(character.isspace() for character in arguments[0])
                or not arguments[1].startswith(
                    "/tmp/vibesys-framework-benchmark-"  # noqa: S108
                )
                or not arguments[1].endswith(".json")
            ):
                print("Unsupported SkyPilot evaluator arguments", file=stderr)
                return 2
            artifacts = (arguments[1],)
        request = {
            "version": _PROTOCOL_VERSION,
            "kind": kind,
            "arguments": arguments,
            "artifacts": artifacts,
        }
        client.sendall(json.dumps(request, separators=(",", ":")).encode() + b"\n")
        reader = client.makefile("rb")
        artifact_received = False
        while payload := reader.readline(_MAX_FRAME_BYTES + 1):
            if len(payload) > _MAX_FRAME_BYTES or not payload.endswith(b"\n"):
                print("SkyPilot bridge returned an invalid frame", file=stderr)
                return 2
            try:
                frame = json.loads(payload)
            except json.JSONDecodeError:
                print("SkyPilot bridge returned invalid JSON", file=stderr)
                return 2
            if not isinstance(frame, dict) or frame.get("version") != _PROTOCOL_VERSION:
                print("SkyPilot bridge protocol version mismatch", file=stderr)
                return 2
            frame_type = frame.get("type")
            if (
                frame_type == "stdout"
                and set(frame) == {"version", "type", "data"}
                and isinstance(frame.get("data"), str)
            ):
                stdout.write(frame["data"])
                stdout.flush()
            elif (
                frame_type == "stderr"
                and set(frame) == {"version", "type", "data"}
                and isinstance(frame.get("data"), str)
            ):
                stderr.write(frame["data"])
                stderr.flush()
            elif frame_type == "result":
                status = frame.get("status")
                if (
                    set(frame) != {"version", "type", "status", "sky_exit_code", "remote_job_id"}
                    or status not in {"COMPLETED", "APPLICATION_FAILED", "CANCELLED"}
                    or not _strict_int(frame.get("sky_exit_code"))
                    or not _strict_int(frame.get("remote_job_id"))
                    or (status == "COMPLETED" and bool(artifacts) != artifact_received)
                ):
                    print("SkyPilot bridge returned an invalid result", file=stderr)
                    return 2
                return 0 if status == "COMPLETED" else 130 if status == "CANCELLED" else 1
            elif frame_type == "artifact":
                path = frame.get("path")
                data = frame.get("data_base64")
                if (
                    set(frame) != {"version", "type", "path", "data_base64"}
                    or not isinstance(path, str)
                    or path not in artifacts
                    or not isinstance(data, str)
                    or artifact_received
                ):
                    print("SkyPilot bridge returned an invalid artifact", file=stderr)
                    return 2
                try:
                    decoded = base64.b64decode(data, validate=True)
                    descriptor = os.open(
                        path,
                        os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW,
                        0o600,
                    )
                    with os.fdopen(descriptor, "wb") as output:
                        output.write(decoded)
                except (binascii.Error, OSError, ValueError):
                    print("SkyPilot bridge returned an invalid artifact", file=stderr)
                    return 2
                artifact_received = True
            elif frame_type == "error":
                error = frame.get("error")
                if set(frame) != {"version", "type", "error"} or not isinstance(error, str):
                    print("SkyPilot bridge returned an invalid frame", file=stderr)
                    return 2
                print(f"SkyPilot bridge error: {error}", file=stderr)
                return 2
            else:
                print("SkyPilot bridge returned an invalid frame", file=stderr)
                return 2
    print("SkyPilot bridge closed without a result", file=stderr)
    return 2


def _strict_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def main() -> None:
    """Run the sandbox-side bridge client."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", type=Path, required=True)
    parser.add_argument("kind", choices=("accuracy", "benchmark"))
    args, arguments = parser.parse_known_args()
    raise SystemExit(
        run_evaluator(
            args.kind,
            args.socket,
            arguments=tuple(arguments),
            stdout=sys.stdout,
            stderr=sys.stderr,
        )
    )


if __name__ == "__main__":
    main()
