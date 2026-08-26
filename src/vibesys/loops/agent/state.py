"""Typed persistence adapter for agent-loop state."""

from __future__ import annotations

from typing import TYPE_CHECKING

from vibesys.loops.agent.model import ActiveHypothesis, HypothesisLedger

if TYPE_CHECKING:
    from vs_project import StateNamespace, StateSlot, StateTransition


class AgentStateStore:
    """Persist typed agent-loop local state in one namespace."""

    _ACTIVE_FILE = "active.json"

    def __init__(self, namespace: StateNamespace) -> None:
        """Bind the adapter to one machine-local agent namespace."""
        self._slot: StateSlot[ActiveHypothesis] = namespace.slot(
            self._ACTIVE_FILE,
            ActiveHypothesis,
        )

    def load_active(self) -> ActiveHypothesis | None:
        """Load active state, returning ``None`` only when absent."""
        return self._slot.load_optional()

    def save_active(self, state: ActiveHypothesis | None) -> None:
        """Atomically save or clear active state."""
        self._slot.save(state)

    def prepare_active_transition(
        self,
        state: ActiveHypothesis | None,
    ) -> StateTransition:
        """Build the exact typed active-state transition without applying it."""
        return self._slot.transition(state)

    def apply_active_transition(self, transition: StateTransition) -> None:
        """Atomically apply a previously prepared active-state transition."""
        self._slot.apply(transition)


class HypothesisLedgerStore:
    """Persist authoritative hypothesis state in the portable agent namespace."""

    _LEDGER_FILE = "hypotheses.json"

    def __init__(self, namespace: StateNamespace) -> None:
        """Bind the ledger to one run's portable agent namespace."""
        self._namespace = namespace
        self._slot: StateSlot[HypothesisLedger] = namespace.slot(
            self._LEDGER_FILE,
            HypothesisLedger,
        )

    def load_optional(self) -> HypothesisLedger | None:
        """Load the ledger, returning ``None`` only for legacy runs."""
        return self._slot.load_optional()

    def save(self, ledger: HypothesisLedger) -> None:
        """Atomically replace the validated ledger."""
        self._slot.save(ledger)

    @property
    def namespace(self) -> StateNamespace:
        """Return the namespace used for durable Git snapshots."""
        return self._namespace
