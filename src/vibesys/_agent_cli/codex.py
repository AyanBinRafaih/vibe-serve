import json
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agentshim.codex import CodexGenerationSession
from agentshim.codex.events import CodexEvent, ToolResultEvent, ToolUseEvent
from agentshim.events import AgentEventHandler

from .base import MCPServerSpec
from .cli_agent import CLICodingAgent

if TYPE_CHECKING:
    from agentshim.executor import CommandExecutor

_COMMAND_TOOL = "execute"


def _item_lifecycle(line: str) -> tuple[str, str] | None:
    """Read lifecycle kind and stable item identity before callbacks discard them."""
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or data.get("type") not in ("item.started", "item.completed"):
        return None
    item = data.get("item")
    if not isinstance(item, dict) or not isinstance(item.get("id"), str):
        return None
    return data["type"], item["id"]


def _completion_output(parameters: Any) -> str:  # noqa: ANN401  # tracked: #288
    """Preserve output and error payloads added when a generic item completes."""
    if isinstance(parameters, dict):
        for key in ("aggregated_output", "output", "text", "summary", "result", "error", "message"):
            value = parameters.get(key)
            if value:
                return value if isinstance(value, str) else json.dumps(value)
    return ""


class _PairedCodexSession(CodexGenerationSession):
    """Repair AgentShim's generic tool lifecycle before item IDs are discarded.

    AgentShim 0.5.x maps generic starts and completions to ToolUseEvent.
    Completion arguments can change, so use the raw lifecycle kind to convert
    only completions to ToolResultEvent. The base session then pairs by ID,
    including when a corrected parser supplies ToolResultEvent itself.
    """

    def __init__(self, **kwargs: Any) -> None:  # noqa: ANN401  # tracked: #288
        super().__init__(**kwargs)
        self._lifecycle: tuple[str, str] | None = None

    def _process_stdout(self, line: str) -> None:
        self._lifecycle = _item_lifecycle(line)
        try:
            super()._process_stdout(line)
        finally:
            self._lifecycle = None

    def _handle_event(self, event: CodexEvent) -> None:
        if (
            isinstance(event, ToolUseEvent)
            and event.tool_name != _COMMAND_TOOL
            and self._lifecycle is not None
            and event.tool_id == self._lifecycle[1]
        ):
            if self._lifecycle[0] == "item.started":
                # AgentShim registers only command executions. Register generic
                # starts too, using the same clock as its duration calculation.
                self.tool_map[event.tool_id] = event.tool_name
                self.tool_start_times[event.tool_id] = time.time()
                self.tool_args[event.tool_id] = event.parameters
            else:
                event = ToolResultEvent(
                    tool_id=event.tool_id,
                    tool_name=event.tool_name,
                    parameters=event.parameters,
                    output=_completion_output(event.parameters),
                )
        super()._handle_event(event)
        if (
            isinstance(event, ToolResultEvent)
            and event.tool_id
            and event.tool_name != _COMMAND_TOOL
        ):
            self.tool_map.pop(event.tool_id, None)
            self.tool_start_times.pop(event.tool_id, None)
            self.tool_args.pop(event.tool_id, None)


def _toml_str(value: str) -> str:
    """Quote *value* as a TOML basic string literal."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _toml_array(values: list[str]) -> str:
    """Render a list of strings as a TOML inline array of basic strings."""
    return "[" + ",".join(_toml_str(v) for v in values) + "]"


def _shell_path_config_args(env: dict[str, str]) -> list[str]:
    """Preserve the launcher PATH in commands spawned by Codex."""
    path = env.get("PATH")
    if not path:
        return []
    return ["--config", f"shell_environment_policy.set.PATH={_toml_str(path)}"]


class CodexCodingAgent(CLICodingAgent[CodexGenerationSession]):
    """Coding agent implementation using the Codex CLI tool."""

    supports_native_output_schema = True
    # ``codex exec resume <thread-id>`` continues a stored rollout, so a
    # checkpointed thread ID can be adopted before the next turn.
    supports_session_resume = True

    def __init__(  # noqa: ANN204  # tracked: #288
        self,
        model: str | None = None,
        event_handler: AgentEventHandler | None = None,
        *,
        executor: "CommandExecutor | None" = None,
    ):
        """Initialize the Codex coding agent.

        Args:
            model: Optional model name to use with codex. If None, uses default.
            event_handler: Optional event handler for UI updates.
            executor: Optional agentshim :class:`CommandExecutor`.
        """
        super().__init__(
            "codex",
            model,
            event_handler,
            executor=executor,
        )
        # Extra ``--config key=value`` flags appended to ``codex exec`` by
        # :meth:`_get_command`. Populated by :meth:`install_mcp_servers`
        # because Codex has no project-level config file discovery — its
        # only project-scoped knob is the runtime ``--config`` override
        # layer (verified via codex-rs/core/src/config_loader/README.md).
        self.base_config_args = _shell_path_config_args(self.env)
        self.extra_config_args: list[str] = []
        self.output_schema_path: str | None = None

    def set_reasoning_effort(self, effort: str) -> None:
        """Apply a per-agent Codex reasoning effort to fresh and resumed turns."""
        self.base_config_args.extend(["--config", f"model_reasoning_effort={_toml_str(effort)}"])

    def set_output_schema_path(self, path: str | None) -> None:
        """Apply a native final-response schema to the next Codex turn."""
        self.output_schema_path = path

    def _append_output_schema(self, cmd: list[str]) -> None:
        path = getattr(self, "output_schema_path", None)
        if path:
            cmd.extend(["--output-schema", path])

    @property
    def codex_path(self) -> str:
        """Return path to codex binary (for backward compatibility)."""
        return self.binary_path

    @property
    def _log_prefix(self) -> str:
        """Return the log prefix for this agent."""
        return "[Codex]"

    def _get_command(self, prompt: str) -> list[str]:  # noqa: ARG002  # tracked: #288
        cmd = [
            self.binary_path,
            "exec",
            "--dangerously-bypass-approvals-and-sandbox",
            "--skip-git-repo-check",
            "--json",
        ]
        if self.model:
            cmd.extend(["--model", self.model])
        if self.base_config_args:
            cmd.extend(self.base_config_args)
        if self.extra_config_args:
            cmd.extend(self.extra_config_args)
        self._append_output_schema(cmd)
        return cmd

    def _get_resume_command(self, prompt: str, session_id: str) -> list[str]:  # noqa: ARG002  # tracked: #288
        # ``codex exec resume`` does NOT fall back to stdin when the ``[PROMPT]``
        # positional is omitted — only the literal ``-`` sentinel makes it read
        # from stdin. Pass ``-`` so the prompt we write to the subprocess's
        # stdin is actually consumed. (``codex exec`` is more lenient and
        # treats stdin as the default fallback, so we don't need this there.)
        cmd = [
            self.binary_path,
            "exec",
            "resume",
            session_id,
            "-",
            "--dangerously-bypass-approvals-and-sandbox",
            "--skip-git-repo-check",
            "--json",
        ]
        if self.model:
            cmd.extend(["--model", self.model])
        if self.base_config_args:
            cmd.extend(self.base_config_args)
        if self.extra_config_args:
            cmd.extend(self.extra_config_args)
        self._append_output_schema(cmd)
        return cmd

    def _create_session(
        self,
        cmd: list[str],
        cwd: str | None = None,
        timeout: int | None = None,
        silent: bool = False,  # noqa: FBT001, FBT002  # tracked: #288
    ) -> CodexGenerationSession:
        return _PairedCodexSession(
            binary_name=self.binary_name,
            env=self.env,
            log_prefix=self._log_prefix,
            cmd=cmd,
            logger=self.logger,
            cwd=cwd,
            timeout=timeout,
            silent=silent,
            event_handler=self.event_handler,
            executor=self.executor,
        )

    def _extract_session_id(self, session: CodexGenerationSession) -> str | None:
        return session.session_id

    def install_mcp_servers(self, workspace: Path, servers: list[MCPServerSpec]) -> None:  # noqa: ARG002  # tracked: #288
        """Stash ``--config mcp_servers.<name>.<key>=<value>`` flags on the
        instance for the next ``codex exec`` invocation.

        Codex has no project-scoped config file (its config loader only
        looks at MDM, system-managed config, session ``--config`` flags,
        and ``~/.codex/config.toml``), so MCP servers are configured by
        passing dotted-path TOML overrides at the command line. ``--config``
        values are parsed as TOML literals, so strings need TOML quoting
        and arrays use TOML inline array syntax.

        TOML table keys are snake_case by convention, so ``"vibesys-issues"``
        becomes ``mcp_servers.vibesys_issues``.
        """  # noqa: D205  # tracked: #288
        flags: list[str] = []
        for s in servers:
            key = s.name.replace("-", "_")
            flags.extend(
                [
                    "--config",
                    f"mcp_servers.{key}.command={_toml_str(s.command)}",
                    "--config",
                    f"mcp_servers.{key}.args={_toml_array(list(s.args))}",
                ]
            )
            for env_key, env_val in s.env.items():
                flags.extend(
                    [
                        "--config",
                        f"mcp_servers.{key}.env.{env_key}={_toml_str(env_val)}",
                    ]
                )
        self.extra_config_args = flags

    def uninstall_mcp_servers(self, workspace: Path, servers: list[MCPServerSpec]) -> None:  # noqa: ARG002  # tracked: #288
        """Clear the runtime ``--config`` flags. Idempotent."""
        self.extra_config_args = []
