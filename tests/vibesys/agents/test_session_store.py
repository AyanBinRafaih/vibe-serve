"""Durable provider-session persistence and AgentClient seeding on resume.

These tests exercise the machine-local session store in isolation and its
integration with :class:`~vibesys.agents.client.AgentClient`: a fresh client
(standing in for a resumed process) must seed a persisted provider session ID
into the very first turn, but only when the stored provider/model still match
the requested spec, and it must forget the ID on a reset or a spec change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from vibesys.agents.client import AgentClient
from vibesys.agents.contracts import (
    AgentCapabilities,
    AgentExecutionPolicy,
    AgentObserver,
    AgentSession,
    AgentSessionSpec,
    AgentTurnRequest,
    AgentTurnResult,
    SessionDisposition,
)
from vibesys.agents.session_store import (
    AgentSessionState,
    DurableSessionStore,
    NullSessionStore,
)
from vs_project import (
    PlainRunConfiguration,
    Project,
    RunEnvironmentRecord,
)


def _project(tmp_path: Path) -> Project:
    (tmp_path / "OBJECTIVE.md").write_text("Make it fast.\n", encoding="utf-8")
    project = Project.open(tmp_path)
    project.state.create_project("test")
    run = project.state.new_run_manifest(
        "test",
        run_id="run-1",
        branch="vibesys/run-1",
        vibesys_version="test",
        trusted_input_baseline="a" * 40,
        configuration=PlainRunConfiguration(
            outer_loop="plain",
            run_environment=RunEnvironmentRecord(name="local"),
            agent_backend="stub",
            compute_backend="cpu",
            max_rounds=2,
            max_attempts_per_issue=1,
            max_issues_per_perf_eval=1,
        ),
    )
    project.state.create_run(run)
    return project


def _store(tmp_path: Path) -> DurableSessionStore:
    project = _project(tmp_path)
    slot = project.state.local_namespace("run-1", "agent").slot("sessions.json", AgentSessionState)
    return DurableSessionStore(slot)


# --- DurableSessionStore ---------------------------------------------------


def test_record_then_get_returns_the_persisted_session(tmp_path: Path) -> None:
    store = _store(tmp_path)

    store.record(
        "hypothesis:H-01",
        provider="codex",
        model="gpt-5.6-sol",
        session_id="thread-abc",
        role="implementer",
    )
    record = store.get("hypothesis:H-01")

    assert record is not None
    assert record.session_id == "thread-abc"
    assert record.provider == "codex"
    assert record.model == "gpt-5.6-sol"
    assert record.role == "implementer"
    assert record.turn_count == 1


def test_get_misses_return_none(tmp_path: Path) -> None:
    assert _store(tmp_path).get("hypothesis:absent") is None


def test_record_bumps_turn_count_across_turns(tmp_path: Path) -> None:
    store = _store(tmp_path)

    store.record("k", provider="codex", model="m", session_id="s1")
    store.record("k", provider="codex", model="m", session_id="s2")

    record = store.get("k")
    assert record is not None
    assert record.session_id == "s2"
    assert record.turn_count == 2


def test_persisted_session_survives_a_new_store_instance(tmp_path: Path) -> None:
    project = _project(tmp_path)
    namespace = project.state.local_namespace("run-1", "agent")

    DurableSessionStore(namespace.slot("sessions.json", AgentSessionState)).record(
        "k", provider="codex", model="m", session_id="thread-xyz"
    )
    # A fresh instance over the same slot models a resumed process: the on-disk
    # map, not any in-memory cache, is the source of truth.
    reloaded = DurableSessionStore(namespace.slot("sessions.json", AgentSessionState)).get("k")

    assert reloaded is not None
    assert reloaded.session_id == "thread-xyz"


def test_clear_forgets_only_the_named_key(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.record("keep", provider="codex", model="m", session_id="s-keep")
    store.record("drop", provider="codex", model="m", session_id="s-drop")

    store.clear("drop")

    assert store.get("drop") is None
    assert store.get("keep") is not None


def test_clear_of_missing_key_is_a_noop(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.clear("never-recorded")  # must not raise
    assert store.get("never-recorded") is None


# --- NullSessionStore ------------------------------------------------------


def test_null_store_persists_nothing_and_always_misses() -> None:
    store = NullSessionStore()

    store.record("k", provider="codex", model="m", session_id="s")
    store.clear("k")

    assert store.get("k") is None


# --- AgentClient seeding on resume -----------------------------------------


@dataclass
class _SeedableSession:
    """A fake session that records seeds and returns queued turn results."""

    results: list[AgentTurnResult]
    error: Exception | None = None
    seeded: list[str] = field(default_factory=list)
    close_calls: int = 0

    def run_turn(
        self,
        request: AgentTurnRequest,  # noqa: ARG002
        observer: AgentObserver | None = None,  # noqa: ARG002
    ) -> AgentTurnResult:
        if self.error is not None:
            raise self.error
        return self.results.pop(0)

    def seed_provider_session(self, session_id: str) -> None:
        self.seeded.append(session_id)

    def close(self) -> None:
        self.close_calls += 1


@dataclass
class _FakeDriver:
    queued_sessions: list[_SeedableSession]
    specs: list[AgentSessionSpec] = field(default_factory=list)
    close_calls: int = 0

    @property
    def capabilities(self) -> AgentCapabilities:
        return AgentCapabilities()

    def create_session(self, spec: AgentSessionSpec) -> AgentSession:
        self.specs.append(spec)
        return self.queued_sessions.pop(0)

    def close(self) -> None:
        self.close_calls += 1


def _spec(*, provider: str = "codex", model: str = "gpt-5.6-sol") -> AgentSessionSpec:
    return AgentSessionSpec(
        role="implementer",
        provider=provider,
        model=model,
        workspace=Path("/workspace"),
        policy=AgentExecutionPolicy(),
        skills=(),
    )


def test_completed_turn_persists_its_provider_session_id(tmp_path: Path) -> None:
    store = _store(tmp_path)
    session = _SeedableSession(results=[AgentTurnResult("done", provider_session_id="thread-1")])
    client = AgentClient(_FakeDriver([session]), session_store=store)

    client.run(session_spec=_spec(), turn=AgentTurnRequest("one"), session_key="hypothesis:H-01")

    record = store.get("hypothesis:H-01")
    assert record is not None
    assert record.session_id == "thread-1"
    assert record.turn_count == 1


def test_turn_without_a_session_id_keeps_the_prior_id(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.record("hypothesis:H-01", provider="codex", model="gpt-5.6-sol", session_id="thread-old")
    session = _SeedableSession(results=[AgentTurnResult("done", provider_session_id=None)])
    client = AgentClient(_FakeDriver([session]), session_store=store)

    client.run(session_spec=_spec(), turn=AgentTurnRequest("one"), session_key="hypothesis:H-01")

    record = store.get("hypothesis:H-01")
    assert record is not None
    assert record.session_id == "thread-old"


def test_fresh_client_seeds_the_persisted_session_on_first_turn(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.record("hypothesis:H-01", provider="codex", model="gpt-5.6-sol", session_id="thread-1")
    session = _SeedableSession(results=[AgentTurnResult("resumed", provider_session_id="thread-1")])
    # A brand new client with no in-memory session models a resumed process.
    client = AgentClient(_FakeDriver([session]), session_store=store)

    client.run(
        session_spec=_spec(), turn=AgentTurnRequest("continue"), session_key="hypothesis:H-01"
    )

    assert session.seeded == ["thread-1"]


def test_seed_is_skipped_when_the_stored_provider_or_model_differs(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.record("hypothesis:H-01", provider="claude", model="gpt-5.6-sol", session_id="thread-1")
    session = _SeedableSession(results=[AgentTurnResult("fresh", provider_session_id="thread-2")])
    client = AgentClient(_FakeDriver([session]), session_store=store)

    # Requested provider is codex; the stored session is a claude thread.
    client.run(
        session_spec=_spec(provider="codex"),
        turn=AgentTurnRequest("go"),
        session_key="hypothesis:H-01",
    )

    assert session.seeded == []


def test_failed_seeded_turn_invalidates_the_persisted_session(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.record("hypothesis:H-01", provider="codex", model="gpt-5.6-sol", session_id="thread-stale")
    # A fresh client models a resumed process: the first turn seeds the stored
    # ID, then raises (e.g. a deleted/invalid Claude session with no fallback).
    failing = _SeedableSession(results=[], error=RuntimeError("resume failed: unknown session"))
    fresh = _SeedableSession(results=[AgentTurnResult("fresh", provider_session_id="thread-new")])
    client = AgentClient(_FakeDriver([failing, fresh]), session_store=store)

    with pytest.raises(RuntimeError):
        client.run(
            session_spec=_spec(), turn=AgentTurnRequest("resume"), session_key="hypothesis:H-01"
        )

    assert failing.seeded == ["thread-stale"]
    # The stale ID must be forgotten so the next attempt does not re-seed it.
    assert store.get("hypothesis:H-01") is None
    client.run(session_spec=_spec(), turn=AgentTurnRequest("retry"), session_key="hypothesis:H-01")
    assert fresh.seeded == []


def test_failed_turn_on_a_proven_session_keeps_the_persisted_session(tmp_path: Path) -> None:
    store = _store(tmp_path)
    # Turn one succeeds (proving the resume), turn two on the same cached session
    # fails transiently. The persisted ID stays: it named a valid rollout.
    session = _SeedableSession(results=[AgentTurnResult("one", provider_session_id="thread-1")])
    client = AgentClient(_FakeDriver([session]), session_store=store)
    client.run(session_spec=_spec(), turn=AgentTurnRequest("one"), session_key="hypothesis:H-01")

    session.error = RuntimeError("transient network error")
    with pytest.raises(RuntimeError):
        client.run(
            session_spec=_spec(), turn=AgentTurnRequest("two"), session_key="hypothesis:H-01"
        )

    record = store.get("hypothesis:H-01")
    assert record is not None
    assert record.session_id == "thread-1"


def test_reset_disposition_clears_the_persisted_session(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.record("hypothesis:H-01", provider="codex", model="gpt-5.6-sol", session_id="thread-1")
    session = _SeedableSession(
        results=[AgentTurnResult("reset", disposition=SessionDisposition.RESET_REQUIRED)]
    )
    client = AgentClient(_FakeDriver([session]), session_store=store)

    client.run(session_spec=_spec(), turn=AgentTurnRequest("one"), session_key="hypothesis:H-01")

    assert store.get("hypothesis:H-01") is None


def test_spec_change_clears_the_persisted_session(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = _SeedableSession(results=[AgentTurnResult("one", provider_session_id="thread-1")])
    second = _SeedableSession(results=[AgentTurnResult("two", provider_session_id="thread-2")])
    client = AgentClient(_FakeDriver([first, second]), session_store=store)

    client.run(
        session_spec=_spec(model="gpt-5.6-sol"),
        turn=AgentTurnRequest("one"),
        session_key="hypothesis:H-01",
    )
    # A within-process model change evicts the live session and drops the stale
    # durable ID before the new configuration persists its own.
    client.run(
        session_spec=_spec(model="gpt-6"),
        turn=AgentTurnRequest("two"),
        session_key="hypothesis:H-01",
    )

    record = store.get("hypothesis:H-01")
    assert record is not None
    assert record.session_id == "thread-2"
    assert record.model == "gpt-6"
    assert record.turn_count == 1
