from __future__ import annotations

import base64
import io
import socket
import threading
from pathlib import Path

import pytest

from vibesys.sandbox.skypilot_evaluator import run_evaluator
from vibesys.skypilot.protocol import (
    ArtifactFrame,
    ErrorFrame,
    OutputFrame,
    ResultFrame,
    encode_message,
)


def _serve_frames(socket_path: Path, frames: list[object]) -> threading.Thread:
    ready = threading.Event()

    def serve() -> None:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(str(socket_path))
            server.listen(1)
            ready.set()
            connection, _ = server.accept()
            with connection:
                connection.makefile("rb").readline()
                for frame in frames:
                    connection.sendall(encode_message(frame))  # type: ignore[arg-type]

    thread = threading.Thread(target=serve)
    thread.start()
    ready.wait()
    return thread


@pytest.mark.parametrize(
    ("status", "expected"),
    [("COMPLETED", 0), ("APPLICATION_FAILED", 1), ("CANCELLED", 130)],
)
def test_helper_relays_streams_and_maps_terminal_status(
    tmp_path: Path, status: str, expected: int
) -> None:
    path = tmp_path / "bridge.sock"
    thread = _serve_frames(
        path,
        [
            OutputFrame(type="stdout", data="out\n"),
            OutputFrame(type="stderr", data="err\n"),
            ResultFrame(
                status=status,  # pyright: ignore[reportArgumentType]
                sky_exit_code=0,
                remote_job_id=7,
            ),
        ],
    )
    stdout, stderr = io.StringIO(), io.StringIO()

    assert run_evaluator("accuracy", path, stdout=stdout, stderr=stderr) == expected
    thread.join()
    assert stdout.getvalue() == "out\n"
    assert stderr.getvalue() == "err\n"


def test_helper_reports_bridge_error_as_transport_failure(tmp_path: Path) -> None:
    path = tmp_path / "bridge.sock"
    thread = _serve_frames(path, [ErrorFrame(error="SkyPilotTimeoutError")])
    stderr = io.StringIO()

    assert run_evaluator("benchmark", path, stdout=io.StringIO(), stderr=stderr) == 2
    thread.join()
    assert "SkyPilotTimeoutError" in stderr.getvalue()


def test_helper_rejects_incomplete_terminal_result(tmp_path: Path) -> None:
    path = tmp_path / "bridge.sock"
    ready = threading.Event()

    def serve() -> None:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(str(path))
            server.listen(1)
            ready.set()
            connection, _ = server.accept()
            with connection:
                connection.makefile("rb").readline()
                connection.sendall(b'{"version":1,"type":"result","status":"COMPLETED"}\n')

    raw_thread = threading.Thread(target=serve)
    raw_thread.start()
    ready.wait()
    stderr = io.StringIO()

    assert run_evaluator("accuracy", path, stdout=io.StringIO(), stderr=stderr) == 2
    raw_thread.join()
    assert "invalid result" in stderr.getvalue()


def test_helper_materializes_narrow_framework_result_artifact(tmp_path: Path) -> None:
    socket_path = tmp_path / "bridge.sock"
    output_path = Path("/tmp/vibesys-framework-benchmark-helper-test.json")  # noqa: S108
    output_path.unlink(missing_ok=True)
    thread = _serve_frames(
        socket_path,
        [
            ArtifactFrame(
                path=str(output_path),
                data_base64=base64.b64encode(b'{"score": 1}').decode(),
            ),
            ResultFrame(status="COMPLETED", sky_exit_code=0, remote_job_id=7),
        ],
    )
    try:
        assert (
            run_evaluator(
                "benchmark",
                socket_path,
                arguments=("--output-json", str(output_path)),
                stdout=io.StringIO(),
                stderr=io.StringIO(),
            )
            == 0
        )
        thread.join()
        assert output_path.read_text() == '{"score": 1}'
    finally:
        output_path.unlink(missing_ok=True)
