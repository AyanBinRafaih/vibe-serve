from __future__ import annotations

import os
import re
import socket
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

import vibesys.skypilot.bridge as bridge_module
from vibesys.skypilot.bridge import SkyPilotBridge
from vibesys.skypilot.config import ResolvedSkyPilotResources
from vibesys.skypilot.protocol import (
    ArtifactFrame,
    ErrorFrame,
    EvaluationRequest,
    decode_response,
    encode_message,
)
from vibesys.skypilot.runner import JobResult, JobStatus

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence


def _resources() -> ResolvedSkyPilotResources:
    return ResolvedSkyPilotResources(
        profile_name="test",
        infra="slurm/example/gpu",
        nodes=1,
        accelerator_backend="rocm",
        accelerator_type="MI300A",
        accelerators_per_node=4,
        exclusive=True,
        remote_artifact_root="/remote/vibesys",
    )


class FakeRunner:
    def __init__(self) -> None:
        self.ensure_calls = 0
        self.release_calls = 0
        self.workdirs: list[Path] = []
        self.commands: list[tuple[str, ...]] = []

    def ensure_cluster(self, name: str, resources: object) -> None:  # noqa: ARG002
        self.ensure_calls += 1

    def run(  # noqa: PLR0913
        self,
        cluster_name: str,
        resources: object,  # noqa: ARG002
        *,
        workdir: Path,
        command: Sequence[str],
        stdout_sink: Callable[[str], None],
        stderr_sink: Callable[[str], None],
        job_started: Callable[[int], None],
    ) -> JobResult:
        self.workdirs.append(workdir)
        self.commands.append(tuple(command))
        assert not (workdir / ".env").exists()
        assert not (workdir / ".venv").exists()
        assert not (workdir / "private").exists()
        assert (workdir / "candidate.py").read_text() == "candidate"
        assert (workdir / ".vibesys-evaluator-package" / "checker.py").exists()
        job_started(9)
        if tuple(command[:2]) == ("sh", "-c"):
            begin = re.search(r"__VIBESYS_SKYPILOT_ARTIFACT_BEGIN_[0-9a-f]+__", command[2])
            end = re.search(r"__VIBESYS_SKYPILOT_ARTIFACT_END_[0-9a-f]+__", command[2])
            assert begin is not None
            assert end is not None
            stdout_sink(f"out\n{begin.group()}\neyJsYXRlbmN5IjoxfQ==\n{end.group()}\n")
        else:
            stdout_sink("out\n")
        stderr_sink("err\n")
        return JobResult(JobStatus.COMPLETED, 0, 9, "out\n", "err\n", cluster_name)

    def cancel(self, cluster_name: str, job_id: int) -> None:  # noqa: ARG002
        return

    def release(self, cluster_name: str) -> None:  # noqa: ARG002
        self.release_calls += 1


def test_bridge_stages_allowlisted_command_streams_and_cleans_up(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "candidate.py").write_text("candidate")
    (workspace / ".env").write_text("SECRET=x")
    (workspace / ".venv").mkdir()
    (workspace / ".venv" / "large-cache").write_text("excluded")
    (workspace / "private").mkdir()
    (workspace / "private" / "token").write_text("secret")
    package = tmp_path / "package"
    package.mkdir()
    (package / "checker.py").write_text("checker")
    runner = FakeRunner()
    bridge = SkyPilotBridge(
        runner=runner,  # pyright: ignore[reportArgumentType]
        cluster_name="lease",
        resources=_resources(),
        workspace=workspace,
        evaluator_package_root=package,
        hidden_paths=(Path("private"),),
        commands={"benchmark": ("python", ".vibesys-evaluator-package/checker.py")},
        benchmark_output_argument="--output-json",
        socket_path=tmp_path / "bridge.sock",
        log=lambda _: None,
    )
    bridge.start()
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.connect(str(bridge.socket_path))
            remote_result = "/tmp/vibesys-framework-benchmark-1-1.json"  # noqa: S108
            client.sendall(
                encode_message(
                    EvaluationRequest(
                        kind="benchmark",
                        arguments=("--output-json", remote_result),
                        artifacts=(remote_result,),
                    )
                )
            )
            reader = client.makefile("rb")
            frames = [decode_response(reader.readline()) for _ in range(4)]
        assert [frame.type for frame in frames] == ["stdout", "stderr", "artifact", "result"]
        assert isinstance(frames[2], ArtifactFrame)
        assert runner.commands[0][:2] == ("sh", "-c")
        assert runner.commands[0][2].index("rm -f --") < runner.commands[0][2].index("python")
        assert ".vibesys-evaluator-package/checker.py" in runner.commands[0][2]
        assert remote_result in runner.commands[0][2]
        assert bridge.socket_path.stat().st_mode & 0o777 == 0o600
    finally:
        bridge.close()


def test_bridge_releases_cluster_when_socket_startup_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runner = FakeRunner()

    class BrokenServer:
        def __init__(self, *args: object, **kwargs: object) -> None:
            del args, kwargs
            raise OSError("bind failed")  # noqa: TRY003

    monkeypatch.setattr(bridge_module, "_BridgeServer", BrokenServer)
    bridge = SkyPilotBridge(
        runner=runner,  # pyright: ignore[reportArgumentType]
        cluster_name="lease",
        resources=_resources(),
        workspace=workspace,
        evaluator_package_root=None,
        hidden_paths=(),
        commands={"accuracy": ("true",)},
        benchmark_output_argument=None,
        socket_path=tmp_path / "bridge.sock",
        log=lambda _: None,
    )

    with pytest.raises(OSError, match="bind failed"):
        bridge.start()

    assert runner.ensure_calls == 1
    assert runner.release_calls == 1
    assert not bridge.socket_path.exists()


def test_bridge_rejects_special_workspace_file(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    os.mkfifo(workspace / "pipe")
    runner = FakeRunner()
    bridge = SkyPilotBridge(
        runner=runner,  # pyright: ignore[reportArgumentType]
        cluster_name="lease",
        resources=_resources(),
        workspace=workspace,
        evaluator_package_root=None,
        hidden_paths=(),
        commands={"accuracy": ("true",)},
        benchmark_output_argument=None,
        socket_path=tmp_path / "bridge.sock",
        log=lambda _: None,
    )
    bridge.start()
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.connect(str(bridge.socket_path))
            client.sendall(encode_message(EvaluationRequest(kind="accuracy")))
            frame = decode_response(client.makefile("rb").readline())
        assert isinstance(frame, ErrorFrame)
        assert frame.error == "ValueError"
        assert runner.commands == []
    finally:
        bridge.close()
        bridge.close()

    assert runner.ensure_calls == 1
    assert runner.release_calls == 1
    assert not bridge.socket_path.exists()


def test_bridge_rejects_workspace_symlink_escape(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "secret"
    outside.write_text("secret")
    (workspace / "escape").symlink_to(outside)
    runner = FakeRunner()
    bridge = SkyPilotBridge(
        runner=runner,  # pyright: ignore[reportArgumentType]
        cluster_name="lease",
        resources=_resources(),
        workspace=workspace,
        evaluator_package_root=None,
        hidden_paths=(),
        commands={"accuracy": ("python", "checker.py")},
        benchmark_output_argument=None,
        socket_path=tmp_path / "bridge.sock",
        log=lambda _: None,
    )
    bridge.start()
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.connect(str(bridge.socket_path))
            client.sendall(encode_message(EvaluationRequest(kind="accuracy")))
            frame = decode_response(client.makefile("rb").readline())
        assert isinstance(frame, ErrorFrame)
        assert frame.error == "ValueError"
        assert runner.commands == []
    finally:
        bridge.close()
