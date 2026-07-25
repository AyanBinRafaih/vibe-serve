"""Deepagents implementation of :class:`AgentRunner`.

Wraps ``deepagents.create_deep_agent`` and the existing
``vibesys.agent_runner.run_typed_agent`` plumbing — no behavior
change vs. what the simple loop did before this abstraction landed.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, TextIO, TypeVar

from deepagents import create_deep_agent
from langchain.agents.structured_output import AutoStrategy
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.tools import BaseTool
from langgraph.checkpoint.memory import MemorySaver
from pydantic import BaseModel

from vibesys._agent_cli.base import MCPServerSpec
from vibesys.agent_runner import (
    log_agent_config,
    run_agent,
    run_typed_agent,
)
from vibesys.agents.callbacks import AgentLogger
from vibesys.agents.progress import AgentProgress

T = TypeVar("T", bound=BaseModel)


def _agent_label(kind: str) -> str:
    """Convert ``"perf_eval"`` to ``"Perf Eval"``, etc."""
    return kind.replace("_", " ").title()


class DeepAgentsRunner:
    """:class:`AgentRunner` backed by ``deepagents.create_deep_agent``."""

    backend_name = "deepagents"

    def __init__(
        self,
        *,
        model: Any,
        backends: dict[str, Any],
        skills: list[str],
        model_name: str | None,
        run_log_file: TextIO | None,
    ):
        self._model = model
        self._backends = backends
        self._skills = skills
        self._model_name = model_name
        self._run_log_file = run_log_file
        # Cache the built agent graph + its checkpointer per kind, so the
        # underlying agent graph (tools, model binding, skills wiring) is
        # only rebuilt when the inputs that actually define it change.
        # Keyed by (kind, system_prompt, tuple of tool names) so a change
        # in either triggers a rebuild rather than silently reusing a stale
        # agent. Each invocation still gets a fresh thread_id (below) so
        # conversation context does not leak across rounds even though the
        # graph itself is reused.
        self._agents: dict[str, Any] = {}
        self._agent_signatures: dict[str, tuple[str, tuple[str, ...], str | None]] = {}
        self._checkpointers: dict[str, MemorySaver] = {}

    def _get_agent(
        self,
        *,
        kind: str,
        system_prompt: str,
        skills: list[str] | None = None,
        response_format: Any = None,
        tools: list[BaseTool] | None = None,
    ) -> Any:
        """Return the cached agent for *kind*, rebuilding if inputs changed."""
        tool_names = tuple(sorted(getattr(t, "name", str(t)) for t in (tools or [])))
        # Include whether/what response_format was requested: invoke() (typed,
        # schema-bound) and invoke_text() (untyped) must never share a cached
        # agent for the same kind, since the underlying graph differs.
        response_format_key = (
            type(response_format).__name__ if response_format is not None else None
        )
        signature = (system_prompt, tool_names, response_format_key)
        cached = self._agents.get(kind)
        if cached is not None and self._agent_signatures.get(kind) == signature:
            return cached

        checkpointer = self._checkpointers.setdefault(kind, MemorySaver())
        kwargs: dict[str, Any] = dict(
            model=self._model,
            backend=self._backends[kind],
            system_prompt=system_prompt,
            skills=skills if skills is not None else self._skills,
            checkpointer=checkpointer,
            tools=tools,
        )
        if response_format is not None:
            kwargs["response_format"] = response_format
        agent = create_deep_agent(**kwargs)
        self._agents[kind] = agent
        self._agent_signatures[kind] = signature
        return agent

    def invoke(
        self,
        *,
        kind: str,
        workspace: Path,  # noqa: ARG002 — backend already encapsulates cwd
        system_prompt: str,
        env: dict[str, str] | None = None,  # noqa: ARG002 — env on the BaseSandbox
        user_prompt: str,
        response_cls: type[T],
        fallback_factory: Callable[[], T],
        round_label: str,
        invocation_id: str | None = None,
        progress: AgentProgress | None = None,
        mcp_servers: list[MCPServerSpec] | None = None,  # noqa: ARG002 — cli-only injection point; deepagents uses tools=
        tools: list[BaseTool] | None = None,
    ) -> T:
        label = _agent_label(kind)

        # Reuse the cached agent graph for this kind when the system prompt
        # and tools haven't changed (see _get_agent). A fresh thread_id is
        # still generated per invocation so each round starts with a clean
        # context window, matching the pre-caching behavior at
        # loop.py:410-411 — only the graph construction itself is reused.
        thread_id = uuid.uuid4().hex
        agent = self._get_agent(
            kind=kind,
            system_prompt=system_prompt,
            response_format=AutoStrategy(response_cls),
            tools=tools,
        )
        log_agent_config(agent, label, self._run_log_file)

        callbacks: list[BaseCallbackHandler] = [
            AgentLogger(
                log_file=self._run_log_file,
                model_name=self._model_name,
                agent_label=label,
                progress=progress,
                agent_kind=kind,
                round_label=round_label,
                invocation_id=invocation_id,
            )
        ]

        return run_typed_agent(
            agent,
            user_prompt,
            response_cls=response_cls,
            label=kind.upper(),
            fallback_factory=fallback_factory,
            callbacks=callbacks,
            thread_id=thread_id,
            round_label=round_label,
            log_file=self._run_log_file,
        )

    def invoke_text(
        self,
        *,
        kind: str,
        workspace: Path,  # noqa: ARG002 — backend already encapsulates cwd
        system_prompt: str,
        env: dict[str, str] | None = None,  # noqa: ARG002 — env on the BaseSandbox
        user_prompt: str,
        round_label: str,
        invocation_id: str | None = None,
        progress: AgentProgress | None = None,
        mcp_servers: list[MCPServerSpec] | None = None,  # noqa: ARG002 — cli-only
        tools: list[BaseTool] | None = None,
    ) -> str:
        """Run a conversational agent without imposing a response schema."""
        thread_id = uuid.uuid4().hex
        label = _agent_label(kind)
        agent = self._get_agent(
            kind=kind,
            system_prompt=system_prompt,
            tools=tools,
        )
        log_agent_config(agent, label, self._run_log_file)
        callbacks: list[BaseCallbackHandler] = [
            AgentLogger(
                log_file=self._run_log_file,
                model_name=self._model_name,
                agent_label=label,
                progress=progress,
                agent_kind=kind,
                round_label=round_label,
                invocation_id=invocation_id,
            )
        ]
        return run_agent(
            agent,
            user_prompt,
            callbacks=callbacks,
            thread_id=thread_id,
            round_label=round_label,
            log_file=self._run_log_file,
        )
