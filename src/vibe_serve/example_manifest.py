"""Generic example directory manifest (vibeserve.example.toml).

Loaded when an example directory contains ``vibeserve.example.toml``
sibling to ``OBJECTIVE.md`` (i.e. at ``ref.parent``). Presence of this
file switches ``_RunContext`` from the legacy Python/model-serving
directory contract (a single ``reference.py`` plus ``checker.py`` /
``benchmark.py``) to the generic script contract described in issue #76:
``setup.sh`` / ``build.sh`` / ``check.sh`` / ``bench.sh`` / ``teardown.sh``
at the example root.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

_LIFECYCLE_SCRIPTS = ("setup.sh", "build.sh", "check.sh", "bench.sh", "teardown.sh")
_REQUIRED_SCRIPTS = ("check.sh", "bench.sh")
_OPTIONAL_SCRIPTS = ("setup.sh", "build.sh", "teardown.sh")


class WorkspaceSpec(BaseModel):
    candidate_path: str | None = None
    requires_model_weights: bool = False
    externally_managed: bool = False


class SetupSpec(BaseModel):
    needs_docker_socket: bool = False
    timeout_sec: int = 300


class ServiceSpec(BaseModel):
    base_url: str | None = None
    health_check: str | None = None
    readiness_timeout_sec: int = 60

    @field_validator("health_check")
    @classmethod
    def _validate_health_check(cls, v: str | None) -> str | None:
        if v is None:
            return v
        parts = v.split(maxsplit=1)
        if len(parts) != 2 or parts[0].upper() not in ("GET", "POST", "HEAD"):
            raise ValueError(
                f"service.health_check must be 'METHOD path', e.g. 'GET /health'. Got: {v!r}"
            )
        return v


class BenchmarkSpec(BaseModel):
    primary_metric: str
    direction: Literal["minimize", "maximize"]
    metrics_format: Literal["jsonl"] = "jsonl"


class ProfilerSpec(BaseModel):
    kind: Literal["none", "torch", "nsys", "neuron"] = "none"


class ExampleManifest(BaseModel):
    workspace: WorkspaceSpec = Field(default_factory=WorkspaceSpec)
    setup: SetupSpec = Field(default_factory=SetupSpec)
    service: ServiceSpec = Field(default_factory=ServiceSpec)
    benchmark: BenchmarkSpec
    profiler: ProfilerSpec = Field(default_factory=ProfilerSpec)

    example_dir: Path = Field(exclude=True)

    @classmethod
    def load(cls, example_dir: Path) -> ExampleManifest:
        manifest_path = example_dir / "vibeserve.example.toml"
        if not manifest_path.is_file():
            raise FileNotFoundError(f"No vibeserve.example.toml at {manifest_path}")
        data = tomllib.loads(manifest_path.read_text())
        return cls(**data, example_dir=example_dir)

    @classmethod
    def detect(cls, ref_path: Path) -> ExampleManifest | None:
        """Return the manifest for *ref_path*'s example dir, or None if absent.

        *ref_path* is the resolved ``--ref`` path (typically
        ``examples/<name>/reference``). The manifest lives at
        ``ref_path.parent / "vibeserve.example.toml"``, the same
        location ``OBJECTIVE.md`` and ``objectives.toml`` already use.

        Also handles externally-managed targets (issue #76 Docker-in-Docker
        Alternative C: e.g. train-ticket) that have no ``reference/``
        directory at all -- *ref_path* itself may not exist on disk. In that
        case *ref_path* is treated as the would-be example directory name and
        the manifest is looked up directly at
        ``ref_path.parent / "vibeserve.example.toml"`` without requiring
        ``ref_path`` to exist.
        """
        example_dir = ref_path.parent
        manifest_path = example_dir / "vibeserve.example.toml"
        if not manifest_path.is_file():
            return None
        return cls.load(example_dir)

    @classmethod
    def detect_from_example_dir(cls, example_dir: Path) -> ExampleManifest | None:
        """Same as :meth:`detect` but takes the example directory itself.

        For externally-managed targets where there is no conventional
        ``--ref`` subdirectory (``reference/``) at all -- the manifest sits
        directly in the example root passed as ``--ref``.
        """
        manifest_path = example_dir / "vibeserve.example.toml"
        if not manifest_path.is_file():
            return None
        return cls.load(example_dir)

    def script_path(self, name: str) -> Path:
        if name not in _LIFECYCLE_SCRIPTS:
            raise ValueError(
                f"Unknown lifecycle script {name!r}; expected one of {_LIFECYCLE_SCRIPTS}"
            )
        return self.example_dir / name

    def has_script(self, name: str) -> bool:
        return self.script_path(name).is_file()

    def require_script(self, name: str) -> Path:
        path = self.script_path(name)
        if not path.is_file():
            raise FileNotFoundError(
                f"{name} is required at {path} (example directory contract: "
                f"check.sh and bench.sh are mandatory; setup.sh/build.sh/teardown.sh are optional)"
            )
        return path

    def env(
        self,
        *,
        base_url: str | None = None,
        output_dir: str | None = None,
        load_level: str = "medium",
    ) -> dict[str, str]:
        """Standard env vars passed to every lifecycle script (issue #76)."""
        resolved_base_url = base_url or self.service.base_url or ""
        out: dict[str, str] = {
            "VIBESERVE_BASE_URL": resolved_base_url,
            "VIBESERVE_LOAD_LEVEL": load_level,
        }
        if output_dir is not None:
            out["VIBESERVE_OUTPUT_DIR"] = output_dir
        return out

    def to_objective(self):
        """Adapt benchmark.{primary_metric,direction} to loops.evolve.population.Objective.

        The manifest's vocabulary ("minimize"/"maximize", per issue #76's
        literal proposal) differs from Objective's ("min"/"max"); this is
        the single translation point.
        """
        from vibe_serve.loops.evolve.population import Objective

        direction_map = {"minimize": "min", "maximize": "max"}
        return Objective(
            name=self.benchmark.primary_metric,
            direction=direction_map[self.benchmark.direction],
        )

    def render_check_instructions(self) -> str:
        return (
            f"Run `bash check.sh` from the example root ({self.example_dir}). "
            "Exit code 0 means all correctness checks passed; nonzero means at "
            "least one failed. Inspect stdout/stderr for details on any failure."
        )

    def render_bench_instructions(self) -> str:
        return (
            f"Run `bash bench.sh` from the example root ({self.example_dir}). "
            f"It prints one JSON object per line in the form "
            f'{{"metric": "<name>", "value": <number>}}. The metric VibeServe '
            f"is optimizing is `{self.benchmark.primary_metric}` "
            f"({self.benchmark.direction} is better)."
        )

    def parse_metrics(self, text: str) -> dict[str, float]:
        """Parse bench.sh output into ``{metric_name: value}`` (issue #76).

        The declared ``benchmark.metrics_format`` is ``jsonl``: one JSON
        object per line of the form ``{"metric": "<name>", "value": <num>}``.
        Lines that are blank, not JSON, not an object, or missing a string
        ``metric`` / numeric ``value`` are ignored, so bench.sh may freely
        interleave human-readable logging with metric lines. On duplicate
        metric names the last value wins.
        """
        metrics: dict[str, float] = {}
        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if isinstance(obj, dict) and isinstance(obj.get("metric"), str):
                value = obj.get("value")
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    metrics[obj["metric"]] = float(value)
        return metrics

    def primary_metric_value(self, text: str) -> float | None:
        """Return the declared ``primary_metric`` value from bench.sh output, or None."""
        return self.parse_metrics(text).get(self.benchmark.primary_metric)
