"""Serving entrypoint for VibeSys frontend clients."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import webbrowser
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn
from urllib.parse import urlsplit

from entrypoints import cli
from server.settings import InteractiveSetupDefaults, TuiTheme, load_tui_theme
from server.transport.discovery import WebInstanceClaim, WebInstanceRecord
from vibesys.api import ConfigurationError
from vibesys.api.request import generate_experiment_name, repository_name_from_experiment
from vs_github import GitHubCLI, GitHubCLIError

_WEB_PORT_MAX = 65_535

if TYPE_CHECKING:
    import argparse
    from collections.abc import Callable

    from vibesys.api import Config


def _control_socket_from_argv(argv: list[str]) -> Path | None:
    """Read the transport bootstrap flag without parsing run configuration."""
    value = cli._option_from_argv(argv, "--control-socket")  # noqa: SLF001
    return Path(value) if value else None


def _web_requested(argv: list[str]) -> bool:
    return "--web" in argv or "--web-reopen" in argv


def _detach_requested(argv: list[str]) -> bool:
    return "--detach" in argv


def _web_port_from_argv(argv: list[str]) -> int:
    value = cli._option_from_argv(argv, "--web-port")  # noqa: SLF001
    if value is None:
        return 0
    try:
        port = int(value)
    except ValueError:
        raise ValueError("--web-port must be an integer") from None  # noqa: TRY003
    if not 0 <= port <= _WEB_PORT_MAX:
        raise ValueError("--web-port must be between 0 and 65535")  # noqa: TRY003
    return port


def _web_assets_from_argv(argv: list[str]) -> Path | None:
    value = cli._option_from_argv(argv, "--web-assets")  # noqa: SLF001
    if value is not None:
        return Path(value).expanduser().resolve()
    source_root = Path(__file__).resolve().parents[2]
    candidate = source_root / "clients" / "web" / "dist"
    return candidate if candidate.is_dir() else None


def _web_origins_from_argv(argv: list[str]) -> tuple[str, ...]:
    origins: list[str] = []
    index = 0
    while index < len(argv):
        argument = argv[index]
        if argument == "--web-origin":
            if index + 1 >= len(argv):
                raise ValueError("--web-origin requires an origin")  # noqa: TRY003
            value = argv[index + 1]
            index += 2
        elif argument.startswith("--web-origin="):
            value = argument.partition("=")[2]
            index += 1
        else:
            index += 1
            continue
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(  # noqa: TRY003
                "--web-origin must be an http:// or https:// origin without a path"
            )
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in origins:
            origins.append(origin)
    return tuple(origins)


def _web_instance_from_argv(argv: list[str]) -> Path:
    value = cli._option_from_argv(argv, "--web-instance")  # noqa: SLF001
    return (
        Path(value).expanduser().resolve()
        if value is not None
        else (Path.cwd() / ".vibesys" / "web-gateway.json").resolve()
    )


def _read_only_log_from_argv(argv: list[str]) -> Path | None:
    value = cli._option_from_argv(argv, "--web-reopen")  # noqa: SLF001
    return Path(value).expanduser().resolve() if value is not None else None


def _headless_argv(argv: list[str]) -> list[str]:
    """Remove server-only options before dispatching to the core CLI."""
    arguments: list[str] = []
    skip_next = False
    for argument in argv:
        if skip_next:
            skip_next = False
            continue
        if argument in {
            "--control-socket",
            "--theme",
            "--web-port",
            "--web-assets",
            "--web-origin",
            "--web-instance",
            "--web-reopen",
        }:
            skip_next = True
            continue
        if argument in {"--web", "--detach"}:
            continue
        if argument.startswith(
            (
                "--control-socket=",
                "--theme=",
                "--web-port=",
                "--web-assets=",
                "--web-origin=",
                "--web-instance=",
                "--web-reopen=",
            )
        ):
            continue
        arguments.append(argument)
    return arguments


def _suggest_repository_owner(config: Config) -> str | None:
    """Return a setup-form owner suggestion without requiring GitHub access."""
    repository = config.repository
    if repository.owner is not None:
        return str(repository.owner)
    try:
        return GitHubCLI().current_user()
    except GitHubCLIError:
        return None


def _resolve_tui_defaults(  # noqa: PLR0913  # tracked: #288
    *,
    config_path: Path | None = None,
    stub_agent: bool = False,
    input_path: Path | None = None,
    runs_dir: Path | None = None,
    experiment_name: str | None = None,
    theme: TuiTheme | None = None,
    directory_only: bool = False,
) -> InteractiveSetupDefaults:
    """Resolve launcher-facing defaults from local configuration."""
    config = cli._load_config_or_stub_default(  # noqa: SLF001
        config_path,
        stub_agent=stub_agent,
    )
    launch_config_path = config_path
    if launch_config_path is None:
        directory_config = Path.cwd() / "agent.toml"
        launch_config_path = directory_config if directory_config.is_file() else None
    resolved_input = input_path.expanduser().resolve() if input_path is not None else None
    resolved_runs_dir = (runs_dir or Path.cwd() / "exp_env").expanduser().resolve()
    resolved_name = experiment_name or generate_experiment_name(resolved_input)
    return InteractiveSetupDefaults(
        runs_dir=str(resolved_runs_dir),
        input_path=str(resolved_input) if resolved_input is not None else "",
        experiment_name=resolved_name,
        repository_owner=None if directory_only else _suggest_repository_owner(config),
        repository_name=repository_name_from_experiment(resolved_name),
        visibility=config.repository.visibility,
        theme=theme or load_tui_theme(launch_config_path),
    )


def _tui_defaults_from_argv(argv: list[str]) -> Callable[[], InteractiveSetupDefaults]:
    """Build the lazy defaults provider exposed over the control socket."""
    config = cli._option_from_argv(argv, "--config")  # noqa: SLF001
    theme = cli._option_from_argv(argv, "--theme")  # noqa: SLF001
    stub_agent = "--stub-agent" in argv

    def provide() -> InteractiveSetupDefaults:
        return _resolve_tui_defaults(
            config_path=Path(config) if config is not None else None,
            stub_agent=stub_agent,
            theme=TuiTheme(theme) if theme is not None else None,
            directory_only=True,
        )

    return provide


def _build_tui_defaults_parser() -> argparse.ArgumentParser:
    parser = cli._RunArgumentParser(  # noqa: SLF001
        prog="vibesys tui-defaults",
        description="Resolve configuration defaults for a TUI launcher.",
    )
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--input", type=Path, default=None)
    parser.add_argument("--runs-dir", type=cli._parse_runs_dir, default=None)  # noqa: SLF001
    parser.add_argument("--exp-name", default=None)
    parser.add_argument("--theme", type=TuiTheme, choices=list(TuiTheme), default=None)
    parser.add_argument("--stub-agent", action="store_true")
    parser.add_argument("--directory-only", action="store_true")
    return parser


def _run_tui_defaults(argv: list[str]) -> None:
    args = _build_tui_defaults_parser().parse_args(argv)
    try:
        defaults = _resolve_tui_defaults(
            config_path=args.config,
            stub_agent=args.stub_agent,
            input_path=args.input,
            runs_dir=args.runs_dir,
            experiment_name=args.exp_name,
            theme=args.theme,
            directory_only=args.directory_only,
        )
    except (ValueError, FileNotFoundError) as exc:
        cli._configuration_error(  # noqa: SLF001
            str(exc),
            code="config_load_failed",
            stage="config_loading",
        )
    print(defaults.model_dump_json())  # noqa: T201  # tracked: #288


def _missing_control_socket() -> NoReturn:
    cli._configuration_error(  # noqa: SLF001
        "--control-socket is required by the frontend server",
        code="invalid_arguments",
        stage="argument_parsing",
    )


def _spawn_detached(arguments: list[str], instance_path: Path) -> None:
    """Start the long-lived child and wait for its capability record."""
    environment = {**os.environ, "VIBESYS_DETACHED_CHILD": "1"}
    subprocess.Popen(  # noqa: S603
        [sys.executable, "-m", "entrypoints.server", *arguments],
        cwd=Path.cwd(),
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        record = WebInstanceRecord.discover(instance_path, cleanup_stale=False)
        if record is not None:
            print(f"VibeSys web UI: {record.url}", flush=True)  # noqa: T201
            webbrowser.open(record.url, new=2)
            return
        time.sleep(0.05)
    raise RuntimeError("Detached VibeSys web gateway did not become ready")  # noqa: TRY003


def _discover_web_instance(path: Path) -> WebInstanceRecord | None:
    """Wait through the bind-to-record race before deciding to launch again."""
    deadline = time.monotonic() + 2
    while True:
        record = WebInstanceRecord.discover(path, cleanup_stale=False)
        if record is not None:
            return record
        if not WebInstanceClaim.is_held(path):
            return WebInstanceRecord.discover(path)
        if time.monotonic() >= deadline:
            return WebInstanceRecord.discover(path)
        time.sleep(0.05)


def main(argv: list[str] | None = None) -> None:  # noqa: C901, PLR0912, PLR0915
    """Run the frontend server and headless engine in one process."""
    arguments = sys.argv[1:] if argv is None else argv
    if arguments and arguments[0] == "tui-defaults":
        try:
            _run_tui_defaults(arguments[1:])
        except ConfigurationError as exc:
            cli._render_configuration_error(exc)  # noqa: SLF001
        return

    web = _web_requested(arguments)
    detach = _detach_requested(arguments)
    if detach and not web:
        cli._configuration_error(  # noqa: SLF001
            "--detach requires --web",
            code="invalid_arguments",
            stage="argument_parsing",
        )
    instance_path = _web_instance_from_argv(arguments) if web else None
    if web and os.environ.get("VIBESYS_DETACHED_CHILD") != "1":
        if instance_path is None:  # pragma: no cover - web always supplies a path.
            raise RuntimeError("Web instance path was not resolved")  # noqa: TRY003
        existing = _discover_web_instance(instance_path)
        if existing is not None:
            print(f"VibeSys web UI: {existing.url}", flush=True)  # noqa: T201
            webbrowser.open(existing.url, new=2)
            return
        if detach:
            _spawn_detached(arguments, instance_path)
            return
    control_socket = _control_socket_from_argv(arguments)
    temp_socket_dir: tempfile.TemporaryDirectory[str] | None = None
    if control_socket is None and web:
        temp_socket_dir = tempfile.TemporaryDirectory(prefix="vibesys-web-")
        control_socket = Path(temp_socket_dir.name) / "control.sock"
    if control_socket is None:
        try:
            _missing_control_socket()
        except ConfigurationError as exc:
            cli._render_configuration_error(exc)  # noqa: SLF001
    try:
        web_port = _web_port_from_argv(arguments)
        web_assets = _web_assets_from_argv(arguments)
        web_origins = _web_origins_from_argv(arguments)
        read_only_log = _read_only_log_from_argv(arguments)
    except ValueError as exc:
        cli._configuration_error(  # noqa: SLF001
            str(exc),
            code="invalid_arguments",
            stage="argument_parsing",
        )
    from server.runtime import ServerRuntime  # noqa: PLC0415  # tracked: #288

    try:
        if web:
            runtime = ServerRuntime(
                socket_path=control_socket,
                tui_defaults=_tui_defaults_from_argv(arguments),
                web=True,
                web_port=web_port,
                web_assets=web_assets,
                web_origins=web_origins,
                instance_path=instance_path,
                detach=detach,
                read_only_log=read_only_log,
            )
        else:
            runtime = ServerRuntime(
                socket_path=control_socket,
                tui_defaults=_tui_defaults_from_argv(arguments),
            )
        try:
            if read_only_log is not None:
                result = runtime.run(lambda: None)
            else:
                invocation = cli.parse_cli_invocation(_headless_argv(arguments))
                request = cli.build_run_request(invocation)
                result = runtime.run(lambda: runtime.drive(request))
        except ConfigurationError as exc:
            raise SystemExit(exc.diagnostic.exit_code) from None
    finally:
        if temp_socket_dir is not None:
            temp_socket_dir.cleanup()
    # `result` is `None` when `ServerRuntime.run` absorbed an operator stop
    # (`RunStopped`) as a clean backend exit; only a completed run's
    # `RunResult.succeeded` decides the process exit code.
    if result is not None and not result.succeeded:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
