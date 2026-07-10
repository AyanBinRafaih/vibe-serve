"""Regression coverage for the three issue-76 gap fixes."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from vibe_serve.cli import _resolve_objectives
from vibe_serve.example_manifest import ExampleManifest
from vibe_serve.loops.evolve.loop import _run_profiler
from vibe_serve.sandbox import run_environment as re_mod


def _example_ref(tmp_path: Path, *, manifest: bool, objectives_toml: bool) -> Path:
    example_dir = tmp_path / "examples" / "generic"
    ref = example_dir / "reference"
    ref.mkdir(parents=True)
    if manifest:
        (example_dir / "vibeserve.example.toml").write_text(
            '[benchmark]\nprimary_metric = "p50_ms"\ndirection = "minimize"\n'
        )
    if objectives_toml:
        (example_dir / "objectives.toml").write_text(
            '[[objective]]\nname = "tok_s"\ndirection = "max"\n'
        )
    return ref


def _args(ref: Path, objective=None) -> SimpleNamespace:
    return SimpleNamespace(ref=str(ref), objective=objective or [])


def test_resolve_objectives_uses_manifest_when_no_flag_or_toml(tmp_path):
    ref = _example_ref(tmp_path, manifest=True, objectives_toml=False)
    objs = _resolve_objectives(_args(ref))
    assert len(objs) == 1
    assert objs[0].name == "p50_ms"
    assert objs[0].direction == "min"


def test_objectives_toml_takes_precedence_over_manifest(tmp_path):
    ref = _example_ref(tmp_path, manifest=True, objectives_toml=True)
    objs = _resolve_objectives(_args(ref))
    assert len(objs) == 1
    assert objs[0].name == "tok_s"
    assert objs[0].direction == "max"


def test_cli_objective_flag_takes_precedence_over_manifest(tmp_path):
    ref = _example_ref(tmp_path, manifest=True, objectives_toml=True)
    flag = [SimpleNamespace(name="latency", direction="min")]
    objs = _resolve_objectives(_args(ref, objective=flag))
    assert objs[0].name == "latency"


def test_legacy_example_without_manifest_returns_empty(tmp_path):
    ref = _example_ref(tmp_path, manifest=False, objectives_toml=False)
    assert _resolve_objectives(_args(ref)) == []


def test_evolve_run_profiler_skips_when_kind_none():
    logs: list[str] = []
    ctx = SimpleNamespace(profiler_kind="none", example_manifest=None, lprint=logs.append)
    result = _run_profiler(
        ctx,
        generation=1,
        child_idx=0,
        modality="text_generation",
        objective="minimize p50_ms",
    )
    assert result is None
    assert any("profiler_kind=none" in line for line in logs)


def _socket_manifest(tmp_path: Path) -> ExampleManifest:
    example_dir = tmp_path / "examples" / "fake"
    example_dir.mkdir(parents=True)
    (example_dir / "vibeserve.example.toml").write_text(
        "[setup]\nneeds_docker_socket = true\n\n"
        '[benchmark]\nprimary_metric = "p50_ms"\ndirection = "minimize"\n'
    )
    return ExampleManifest.load(example_dir)


def test_modal_rejects_docker_socket_requirement(tmp_path):
    manifest = _socket_manifest(tmp_path)

    class FakeBackend:
        def make_sandbox(self, kind, **kwargs):
            raise AssertionError("open() must raise before building a sandbox")

    request = re_mod.RunEnvironmentRequest(
        log_dir=tmp_path / "logs",
        workspace=tmp_path / "workspace",
        ref_dir=None,
        backend=FakeBackend(),
        agent_backend="cli",
        cli_provider=None,
        example_manifest=manifest,
    )
    env = re_mod.ModalEnvironment.from_options({})
    with pytest.raises(RuntimeError, match="needs_docker_socket"):
        env.open(request)


def test_agent_run_profiler_skips_when_kind_none(tmp_path):
    from vibe_serve.loops.agent.loop import _run_profiler as agent_run_profiler

    logs: list[str] = []
    ctx = SimpleNamespace(profiler_kind="none", example_manifest=None, lprint=logs.append)
    result = agent_run_profiler(
        ctx,
        round_number=2,
        profile_focus="latency",
        modality="text_generation",
        progress_path=tmp_path / "progress.md",
        objective="minimize p50_ms",
    )
    assert result is None
    assert any("profiler_kind=none" in line for line in logs)


def _valid_manifest(tmp_path: Path) -> ExampleManifest:
    example_dir = tmp_path / "examples" / "valid"
    example_dir.mkdir(parents=True)
    (example_dir / "vibeserve.example.toml").write_text(
        '[benchmark]\nprimary_metric = "p50_ms"\ndirection = "minimize"\n'
    )
    return ExampleManifest.load(example_dir)


def test_manifest_load_missing_file_raises(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(FileNotFoundError):
        ExampleManifest.load(empty)


def test_require_script_missing_raises(tmp_path):
    manifest = _valid_manifest(tmp_path)
    with pytest.raises(FileNotFoundError):
        manifest.require_script("check.sh")


def test_script_path_unknown_name_raises(tmp_path):
    manifest = _valid_manifest(tmp_path)
    with pytest.raises(ValueError):
        manifest.script_path("bogus.sh")


def test_render_instructions_mention_scripts_and_metric(tmp_path):
    manifest = _valid_manifest(tmp_path)
    check = manifest.render_check_instructions()
    bench = manifest.render_bench_instructions()
    assert "check.sh" in check
    assert "bench.sh" in bench
    assert "p50_ms" in bench


def test_service_health_check_none_is_allowed():
    from vibe_serve.example_manifest import ServiceSpec

    spec = ServiceSpec(health_check=None)
    assert spec.health_check is None


def test_parse_metrics_jsonl_ignores_noise_and_extracts_primary(tmp_path):
    manifest = _valid_manifest(tmp_path)
    output = (
        "starting benchmark...\n"
        '{"metric": "p50_ms", "value": 5.64}\n'
        "warming up\n"
        '{"metric": "throughput", "value": 1200}\n'
        "not json at all\n"
        '{"metric": "p50_ms", "value": 4.90}\n'
    )
    metrics = manifest.parse_metrics(output)
    assert metrics == {"p50_ms": 4.90, "throughput": 1200.0}
    assert manifest.primary_metric_value(output) == 4.90


def test_parse_metrics_missing_primary_returns_none(tmp_path):
    manifest = _valid_manifest(tmp_path)
    assert manifest.primary_metric_value('{"metric": "other", "value": 1}') is None


def test_parse_metrics_rejects_bool_and_non_numeric(tmp_path):
    manifest = _valid_manifest(tmp_path)
    out = '{"metric": "p50_ms", "value": true}\n{"metric": "x", "value": "hi"}\n'
    assert manifest.parse_metrics(out) == {}


class _BenchSandbox:
    def __init__(self, bench_output, exit_code=0):
        self._bench_output = bench_output
        self._exit_code = exit_code
        self.commands = []

    def execute(self, cmd, timeout=None):
        self.commands.append(cmd)
        is_bench = "bench.sh" in cmd
        outer = self

        class R:
            exit_code = outer._exit_code if is_bench else 0
            output = outer._bench_output if is_bench else ""

        return R()


def _bench_ctx(manifest, sandbox):
    return SimpleNamespace(
        example_manifest=manifest,
        implementer_backend=sandbox,
        lprint=lambda *_: None,
    )


def test_run_example_benchmark_parses_primary_and_metrics(tmp_path):
    from vibe_serve.loops.profiler import run_example_benchmark

    manifest = _valid_manifest(tmp_path)
    sb = _BenchSandbox('{"metric": "p50_ms", "value": 4.2}\n{"metric": "qps", "value": 900}\n')
    summary = run_example_benchmark(_bench_ctx(manifest, sb), round_label="t")
    assert summary is not None
    assert summary.perf_metric == 4.2
    assert summary.perf_unit == "p50_ms"
    assert summary.metrics == {"p50_ms": 4.2, "qps": 900.0}
    assert any("bench.sh" in c for c in sb.commands)


def test_run_example_benchmark_none_when_bench_exits_nonzero(tmp_path):
    from vibe_serve.loops.profiler import run_example_benchmark

    manifest = _valid_manifest(tmp_path)
    sb = _BenchSandbox("boom", exit_code=1)
    assert run_example_benchmark(_bench_ctx(manifest, sb), round_label="t") is None


def test_run_example_benchmark_none_when_primary_metric_absent(tmp_path):
    from vibe_serve.loops.profiler import run_example_benchmark

    manifest = _valid_manifest(tmp_path)
    sb = _BenchSandbox('{"metric": "other", "value": 1}\n')
    assert run_example_benchmark(_bench_ctx(manifest, sb), round_label="t") is None
