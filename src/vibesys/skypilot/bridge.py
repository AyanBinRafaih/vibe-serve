"""Host-owned Unix-socket bridge for sequential SkyPilot evaluations."""

from __future__ import annotations

import base64
import os
import re
import secrets
import select
import shlex
import shutil
import socket
import socketserver
import tempfile
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from vibesys.skypilot.protocol import (
    ArtifactFrame,
    ErrorFrame,
    EvaluationRequest,
    OutputFrame,
    ResultFrame,
    decode_request,
    encode_message,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from vibesys.skypilot.config import ResolvedSkyPilotResources
    from vibesys.skypilot.runner import SkyPilotJobRunner

_MAX_REQUEST_BYTES = 4096
_OUTPUT_CHUNK_CHARACTERS = 64 * 1024
_MAX_ARTIFACT_BYTES = 512 * 1024
_MAX_STAGED_FILES = 50_000
_MAX_STAGED_BYTES = 512 * 1024 * 1024
_FRAMEWORK_ARGUMENT_COUNT = 2
_FRAMEWORK_ARTIFACT = re.compile(r"^/tmp/vibesys-framework-benchmark-[a-zA-Z0-9._-]+\.json$")
_STAGING_EXCLUDED_NAMES = frozenset(
    {
        ".cache",
        ".env",
        ".git",
        ".mypy_cache",
        ".nox",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        "__pycache__",
        "agent.toml",
        "build",
        "dist",
        "node_modules",
    }
)


class _BridgeServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    block_on_close = False


class _ArtifactStream:
    def __init__(
        self,
        sink: Callable[[str], None],
        *,
        expected: bool,
        begin_marker: str,
        end_marker: str,
    ) -> None:
        self._sink = sink
        self._expected = expected
        self._begin_marker = begin_marker
        self._end_marker = end_marker
        self._capturing = False
        self._captures = 0
        self._encoded: list[str] = []
        self._encoded_characters = 0
        self._oversized = False

    def feed(self, data: str) -> None:
        for line in data.splitlines(keepends=True):
            marker = line.strip()
            if marker == self._begin_marker:
                if self._capturing or self._captures:
                    self._oversized = True
                self._capturing = True
            elif marker == self._end_marker and self._capturing:
                self._capturing = False
                self._captures += 1
            elif self._capturing:
                self._encoded.append(marker)
                self._encoded_characters += len(marker)
                if self._encoded_characters > (_MAX_ARTIFACT_BYTES * 2):
                    self._oversized = True
            else:
                self._sink(line)

    def result(self) -> bytes | None:
        if not self._expected:
            return None
        if self._capturing or self._oversized or self._captures != 1 or not self._encoded:
            raise ValueError("remote evaluator artifact was missing or invalid")  # noqa: TRY003
        try:
            data = base64.b64decode("".join(self._encoded), validate=True)
        except ValueError as exc:
            raise ValueError("remote evaluator artifact was invalid") from exc  # noqa: TRY003
        if len(data) > _MAX_ARTIFACT_BYTES:
            raise ValueError("remote evaluator artifact is too large")  # noqa: TRY003
        return data


class SkyPilotBridge:
    """Serve a fixed evaluator allowlist over a host-owned Unix socket."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        runner: SkyPilotJobRunner,
        cluster_name: str,
        resources: ResolvedSkyPilotResources,
        workspace: Path,
        evaluator_package_root: Path | None,
        hidden_paths: Sequence[Path],
        commands: Mapping[str, Sequence[str]],
        benchmark_output_argument: str | None,
        socket_path: Path,
        log: Callable[[str], None],
    ) -> None:
        """Bind fixed host policy and trusted evaluator commands."""
        self._runner = runner
        self._cluster_name = cluster_name
        self._resources = resources
        self._workspace = workspace
        self._evaluator_package_root = evaluator_package_root
        self._hidden_paths = tuple(hidden_paths)
        self._commands = {kind: tuple(command) for kind, command in commands.items()}
        self._benchmark_output_argument = benchmark_output_argument
        self.socket_path = socket_path
        self._log = log
        self._server: _BridgeServer | None = None
        self._thread: threading.Thread | None = None
        self._closed = False
        self._active_jobs: set[int] = set()
        self._active_lock = threading.Lock()
        self._handler_condition = threading.Condition()
        self._active_handlers = 0
        self._evaluation_lock = threading.Lock()
        self._closing = threading.Event()

    def start(self) -> None:
        """Allocate or reuse compute, then start accepting requests."""
        if self._server is not None:
            return
        if self._closed:
            raise RuntimeError(  # noqa: TRY003
                "SkyPilot bridge cannot be restarted after close"
            )
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        self.socket_path.unlink(missing_ok=True)
        try:
            self._runner.ensure_cluster(self._cluster_name, self._resources)
            bridge = self

            class Handler(socketserver.StreamRequestHandler):
                def handle(self) -> None:
                    bridge._handle(self.rfile, self.wfile, self.request)

            self._server = _BridgeServer(str(self.socket_path), Handler)
            self.socket_path.chmod(0o600)
            self._thread = threading.Thread(
                target=self._server.serve_forever,
                name=f"skypilot-bridge-{self._cluster_name}",
                daemon=True,
            )
            self._thread.start()
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        """Stop serving, release the allocation, and remove the socket."""
        if self._closed:
            return
        self._closed = True
        self._closing.set()
        if self._server is not None:
            self._server.shutdown()
        with self._active_lock:
            active_jobs = tuple(self._active_jobs)
        for job_id in active_jobs:
            try:
                self._runner.cancel(self._cluster_name, job_id)
            except Exception as exc:  # noqa: BLE001
                self._log(f"[warn] SkyPilot job cancellation failed: {type(exc).__name__}")
        try:
            self._runner.release(self._cluster_name)
        except Exception as exc:  # noqa: BLE001
            self._log(f"[warn] SkyPilot allocation release failed: {type(exc).__name__}")
        if self._server is not None:
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
        with self._handler_condition:
            if not self._handler_condition.wait_for(lambda: self._active_handlers == 0, timeout=10):
                self._log("[warn] SkyPilot bridge handler did not stop after allocation release")
        self.socket_path.unlink(missing_ok=True)

    def _handle(self, reader: object, writer: object, connection: socket.socket) -> None:
        with self._handler_condition:
            self._active_handlers += 1
        try:
            if self._closed:
                raise ValueError("SkyPilot bridge is closing")  # noqa: TRY003, TRY301
            with self._evaluation_lock:
                if self._closed:
                    raise ValueError("SkyPilot bridge is closing")  # noqa: TRY003, TRY301
                self._handle_request(reader, writer, connection)
        except Exception as exc:  # noqa: BLE001
            self._write(writer, ErrorFrame(error=type(exc).__name__))
        finally:
            with self._handler_condition:
                self._active_handlers -= 1
                self._handler_condition.notify_all()

    def _handle_request(self, reader: object, writer: object, connection: socket.socket) -> None:
        payload = reader.readline(_MAX_REQUEST_BYTES + 1)  # type: ignore[attr-defined]
        if not payload or len(payload) > _MAX_REQUEST_BYTES or not payload.endswith(b"\n"):
            raise ValueError("invalid bridge request framing")  # noqa: TRY003
        request = decode_request(payload)
        command = self._commands.get(request.kind)
        if command is None:
            raise ValueError(  # noqa: TRY003
                f"evaluator {request.kind!r} is not configured"
            )
        if request.arguments:
            valid_dynamic_output = (
                request.kind == "benchmark"
                and len(request.arguments) == _FRAMEWORK_ARGUMENT_COUNT
                and request.arguments[0] == self._benchmark_output_argument
                and request.artifacts == (request.arguments[1],)
                and _FRAMEWORK_ARTIFACT.fullmatch(request.arguments[1]) is not None
            )
            if not valid_dynamic_output:
                raise ValueError("invalid evaluator arguments")  # noqa: TRY003
        elif request.artifacts:
            raise ValueError("invalid evaluator artifact")  # noqa: TRY003
        self._run(request, (*command, *request.arguments), writer, connection)

    def _run(
        self,
        request: EvaluationRequest,
        command: tuple[str, ...],
        writer: object,
        connection: socket.socket,
    ) -> None:
        self._log(f"[skypilot] running trusted {request.kind} evaluator")
        write_lock = threading.Lock()
        disconnected = threading.Event()
        finished = threading.Event()

        def monitor_disconnect() -> None:
            while not finished.wait(0.1):
                if self._closing.is_set():
                    disconnected.set()
                    return
                readable, _, _ = select.select([connection], [], [], 0)
                if not readable:
                    continue
                try:
                    if connection.recv(1, socket.MSG_PEEK):
                        continue
                except OSError:
                    pass
                disconnected.set()
                with self._active_lock:
                    active = tuple(self._active_jobs)
                for job_id in active:
                    self._runner.cancel(self._cluster_name, job_id)
                return

        monitor = threading.Thread(target=monitor_disconnect, daemon=True)
        with tempfile.TemporaryDirectory(
            prefix=f"vibesys-skypilot-{request.kind}-"
        ) as staging_text:
            self._validate_workspace_symlinks()
            staging = Path(staging_text)
            self._stage_workspace(staging)
            if self._evaluator_package_root is not None:
                shutil.copytree(
                    self._evaluator_package_root,
                    staging / ".vibesys-evaluator-package",
                    symlinks=True,
                )
            nonce = secrets.token_hex(16)
            begin_marker = f"__VIBESYS_SKYPILOT_ARTIFACT_BEGIN_{nonce}__"
            end_marker = f"__VIBESYS_SKYPILOT_ARTIFACT_END_{nonce}__"
            artifact_stream = _ArtifactStream(
                lambda data: self._write_output(writer, "stdout", data, write_lock),
                expected=bool(request.artifacts),
                begin_marker=begin_marker,
                end_marker=end_marker,
            )
            effective_command = self._with_artifact_transport(
                command,
                request.artifacts,
                begin_marker=begin_marker,
                end_marker=end_marker,
            )
            monitor.start()
            try:
                result = self._runner.run(
                    self._cluster_name,
                    self._resources,
                    workdir=staging,
                    command=effective_command,
                    stdout_sink=artifact_stream.feed,
                    stderr_sink=lambda data: self._write_output(writer, "stderr", data, write_lock),
                    job_started=lambda job_id: self._job_started(job_id, disconnected),
                )
            finally:
                finished.set()
                monitor.join(timeout=1)
            with self._active_lock:
                self._active_jobs.discard(result.remote_job_id)
            artifact = artifact_stream.result() if result.status.value == "COMPLETED" else None
            if artifact is not None:
                self._write(
                    writer,
                    ArtifactFrame(
                        path=request.artifacts[0],
                        data_base64=base64.b64encode(artifact).decode("ascii"),
                    ),
                    write_lock,
                )
        self._write(
            writer,
            ResultFrame(
                status=result.status.value,
                sky_exit_code=result.sky_exit_code,
                remote_job_id=result.remote_job_id,
            ),
            write_lock,
        )

    @staticmethod
    def _with_artifact_transport(
        command: tuple[str, ...],
        artifacts: tuple[str, ...],
        *,
        begin_marker: str,
        end_marker: str,
    ) -> tuple[str, ...]:
        if not artifacts:
            return command
        path = shlex.quote(artifacts[0])
        script = "\n".join(
            [
                f"rm -f -- {path}",
                shlex.join(command),
                "status=$?",
                f'if [ -f {path} ] && [ "$(wc -c < {path})" -le {_MAX_ARTIFACT_BYTES} ]; then',
                f"  printf '\\n{begin_marker}\\n'",
                f"  base64 < {path} | tr -d '\\n'",
                f"  printf '\\n{end_marker}\\n'",
                "fi",
                'exit "$status"',
            ]
        )
        return ("sh", "-c", script)

    def _job_started(self, job_id: int, disconnected: threading.Event) -> None:
        with self._active_lock:
            self._active_jobs.add(job_id)
        if disconnected.is_set() or self._closing.is_set():
            self._runner.cancel(self._cluster_name, job_id)

    def _validate_workspace_symlinks(self) -> None:
        root = self._workspace.resolve()
        for path in self._workspace.rglob("*"):
            if path.is_symlink() and not path.resolve().is_relative_to(root):
                raise ValueError("workspace symlink escapes the project")  # noqa: TRY003

    def _stage_workspace(self, staging: Path) -> None:
        hidden = frozenset(self._hidden_paths)
        file_count = 0
        total_bytes = 0
        for current_text, directory_names, file_names in os.walk(
            self._workspace, followlinks=False
        ):
            current = Path(current_text)
            relative_parent = current.relative_to(self._workspace)
            directory_names[:] = [
                name
                for name in directory_names
                if not self._excluded(relative_parent / name, hidden)
            ]
            for name in (*directory_names, *file_names):
                relative = relative_parent / name
                if self._excluded(relative, hidden):
                    continue
                path = current / name
                if path.is_symlink():
                    continue
                if path.is_dir():
                    continue
                if not path.is_file():
                    raise ValueError("workspace contains a special file")  # noqa: TRY003
                file_count += 1
                total_bytes += path.stat().st_size
                if file_count > _MAX_STAGED_FILES or total_bytes > _MAX_STAGED_BYTES:
                    raise ValueError("workspace exceeds remote staging limits")  # noqa: TRY003

        def ignore(current_text: str, names: list[str]) -> set[str]:
            parent = Path(current_text).relative_to(self._workspace)
            return {name for name in names if self._excluded(parent / name, hidden)}

        shutil.copytree(
            self._workspace,
            staging,
            dirs_exist_ok=True,
            symlinks=True,
            ignore=ignore,
        )

    @staticmethod
    def _excluded(relative: Path, hidden: frozenset[Path]) -> bool:
        local_state_or_logs = relative.parts[:2] in {
            (".vibesys", "logs"),
            (".vibesys", "state"),
        }
        return (
            relative.name in _STAGING_EXCLUDED_NAMES
            or relative.name.startswith(".env")
            or local_state_or_logs
            or any(relative == path or relative.is_relative_to(path) for path in hidden)
        )

    @classmethod
    def _write_output(
        cls,
        writer: object,
        stream: Literal["stdout", "stderr"],
        data: str,
        lock: threading.Lock,
    ) -> None:
        for start in range(0, len(data), _OUTPUT_CHUNK_CHARACTERS):
            cls._write(
                writer,
                OutputFrame(
                    type=stream,
                    data=data[start : start + _OUTPUT_CHUNK_CHARACTERS],
                ),
                lock,
            )

    @staticmethod
    def _write(
        writer: object,
        message: OutputFrame | ArtifactFrame | ResultFrame | ErrorFrame,
        lock: threading.Lock | None = None,
    ) -> None:
        context = lock or threading.Lock()
        with context:
            writer.write(encode_message(message))  # type: ignore[attr-defined]
            writer.flush()  # type: ignore[attr-defined]
