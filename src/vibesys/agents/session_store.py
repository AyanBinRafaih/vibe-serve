"""Durable, machine-local persistence of coding-agent provider session IDs.

A provider session ID (a codex thread, a claude session) lets a resumed run
continue an implementor's conversation with ``codex exec resume <id>`` /
``claude --resume <id>`` instead of replaying the round from a fresh command.
The IDs are machine-local: the provider transcripts they name live under the
invoking user's home, so they must persist in the run's *local* state
namespace, never in the portable ``agent/state.json`` snapshot that travels
across machines.

The store records the provider and model alongside each ID so a resume with a
mismatched configuration never seeds a stale session, and it is intentionally a
thin key-value map keyed by the caller's ``session_key`` (e.g.
``"hypothesis:H-01"``). Callers persist after each successful turn and clear on
a configuration change or a provider-signalled reset; nothing here decides when
a session is invalid.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from vs_project import StateSlot


class ProviderSessionRecord(BaseModel):
    """One persisted provider session, keyed by the caller's ``session_key``."""

    model_config = ConfigDict(extra="forbid")

    provider: str
    model: str | None = None
    session_id: str
    turn_count: int = 0
    role: str | None = None


class AgentSessionState(BaseModel):
    """The machine-local session map for one run."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    sessions: dict[str, ProviderSessionRecord] = Field(default_factory=dict)


@runtime_checkable
class SessionStore(Protocol):
    """Persist and look up provider session IDs by ``session_key``."""

    def get(self, key: str) -> ProviderSessionRecord | None:
        """Return the persisted session for ``key``, or ``None``."""
        ...

    def record(
        self,
        key: str,
        *,
        provider: str,
        model: str | None,
        session_id: str,
        role: str | None = None,
    ) -> None:
        """Persist ``session_id`` for ``key``, bumping its turn count."""
        ...

    def clear(self, key: str) -> None:
        """Forget any persisted session for ``key``."""
        ...


class NullSessionStore:
    """A no-op store: nothing is persisted, every lookup misses.

    The default when no durable namespace is wired (ephemeral runs, tests, and
    non-agent loops), so session persistence is opt-in and never a hard
    dependency of :class:`~vibesys.agents.client.AgentClient`.
    """

    def get(self, key: str) -> ProviderSessionRecord | None:  # noqa: D102, ARG002
        return None

    def record(  # noqa: D102
        self,
        key: str,
        *,
        provider: str,
        model: str | None,
        session_id: str,
        role: str | None = None,
    ) -> None:
        del key, provider, model, session_id, role

    def clear(self, key: str) -> None:  # noqa: D102, ARG002
        return


class DurableSessionStore:
    """A :class:`SessionStore` backed by one machine-local state slot.

    Each mutation reloads the slot before writing so the on-disk map is the
    source of truth even across process restarts; the file is small and written
    at most once per agent turn.
    """

    def __init__(self, slot: StateSlot[AgentSessionState]) -> None:
        """Bind the store to a local-namespace ``sessions.json`` slot."""
        self._slot = slot

    def _load(self) -> AgentSessionState:
        return self._slot.load_optional() or AgentSessionState()

    def get(self, key: str) -> ProviderSessionRecord | None:
        """Return the persisted session for ``key``, or ``None``."""
        return self._load().sessions.get(key)

    def record(
        self,
        key: str,
        *,
        provider: str,
        model: str | None,
        session_id: str,
        role: str | None = None,
    ) -> None:
        """Persist ``session_id`` for ``key``, bumping its turn count."""
        state = self._load()
        previous = state.sessions.get(key)
        turn_count = (previous.turn_count if previous is not None else 0) + 1
        state.sessions[key] = ProviderSessionRecord(
            provider=provider,
            model=model,
            session_id=session_id,
            turn_count=turn_count,
            role=role,
        )
        self._slot.save(state)

    def clear(self, key: str) -> None:
        """Forget any persisted session for ``key``."""
        state = self._load()
        if state.sessions.pop(key, None) is not None:
            self._slot.save(state)
