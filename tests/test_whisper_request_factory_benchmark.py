from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import textwrap
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from vibesys.input_manifest import load_input_bundle

if TYPE_CHECKING:
    from types import ModuleType

_REPO_ROOT = Path(__file__).parents[1]
_TASK_ROOT = _REPO_ROOT / "examples" / "model-serving" / "whisper-large-v3"
_BENCHMARK_PATH = _TASK_ROOT / "benchmark" / "benchmark.py"


def _load_benchmark() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "whisper_request_factory_benchmark", _BENCHMARK_PATH
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def benchmark_module() -> ModuleType:
    return _load_benchmark()


@pytest.fixture
def fake_engine(tmp_path: Path) -> Path:
    path = tmp_path / "session_runner"
    path.write_text(
        f"#!{sys.executable}\n"
        + textwrap.dedent(
            """
            import json
            import os
            import sys
            from pathlib import Path

            arguments = sys.argv[1:]

            def option(name):
                return arguments[arguments.index(name) + 1]

            trace_path = Path(option("--trace"))
            phase = trace_path.name.split(".", 1)[0]
            requests = [json.loads(line) for line in trace_path.read_text().splitlines() if line]
            capture = Path(os.environ["FAKE_REQUEST_FACTORY_CAPTURE"])
            with capture.open("a") as writer:
                writer.write(json.dumps({"phase": phase, "argv": arguments, "requests": requests}) + "\\n")

            mode = os.environ.get("FAKE_REQUEST_FACTORY_MODE", "success")
            if mode == "engine-error" and phase == "warmup":
                print("synthetic engine failure", file=sys.stderr)
                raise SystemExit(7)

            failed = 1 if mode == "one-failure" and phase == "measure" else 0
            attempted = len(requests)
            succeeded = attempted - failed
            reported_attempted = attempted
            if mode == "bad-summary" and phase == "measure":
                reported_attempted -= 1
            summary = {
                "replay": {
                    "kind": "multimodal_requests",
                    "common": {
                        "attempted_steps": reported_attempted,
                        "success_steps": succeeded,
                        "failed_steps": failed,
                        "run_duration_ms": 2000.0,
                        "request_throughput_per_s": succeeded / 2.0,
                    },
                }
            }
            Path(option("--summary-path")).write_text(json.dumps(summary))

            logged = requests
            if mode == "missing-log" and phase == "measure":
                logged = requests[:-1]
            with Path(option("--log-path")).open("w") as writer:
                for index, request in enumerate(logged):
                    status = "FAILED" if index >= succeeded else "SUCCESS"
                    record = {
                        "source": {"type": "multimodal_request", "data": {"id": request["id"]}},
                        "outcome": {
                            "request_id": request["id"],
                            "status": status,
                            "total_duration_ms": float((index + 1) * 100),
                        },
                    }
                    writer.write(json.dumps(record) + "\\n")
            """
        ),
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def _run_arguments(fake_engine: Path, output: Path, *, concurrency: int = 3) -> list[str]:
    return [
        "--request-factory-engine",
        str(fake_engine),
        "--url",
        "http://candidate:8000/v1/",
        "--concurrency",
        str(concurrency),
        "--num-requests",
        "5",
        "--audio-dir",
        str(_TASK_ROOT / "test_audio"),
        "--output-json",
        str(output),
    ]


def _captures(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_runs_warmup_then_measurement_with_canonical_traces_and_projects_result(
    benchmark_module: ModuleType,
    fake_engine: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    capture = tmp_path / "capture.jsonl"
    output = tmp_path / "result.json"
    monkeypatch.setenv("FAKE_REQUEST_FACTORY_CAPTURE", str(capture))

    assert benchmark_module.main(_run_arguments(fake_engine, output, concurrency=8)) == 0

    assert json.loads(output.read_text()) == {"requests_per_second": 2.5}
    phases = _captures(capture)
    assert [phase["phase"] for phase in phases] == ["warmup", "measure"]
    warmup, measured = phases
    assert len(warmup["requests"]) == 4
    assert len(measured["requests"]) == 5

    measured_argv = measured["argv"]
    assert measured_argv[measured_argv.index("--base-url") + 1] == "http://candidate:8000/v1"
    assert measured_argv[measured_argv.index("--input-file-format") + 1] == (
        "multimodal-independent-v1"
    )
    assert measured_argv[measured_argv.index("--backend") + 1] == "openai-transcriptions"
    assert measured_argv[measured_argv.index("--dialect") + 1] == "openai"
    assert measured_argv[measured_argv.index("--temperature") + 1] == "0"
    assert measured_argv[measured_argv.index("--arrival-mode") + 1] == "saturated"
    assert measured_argv[measured_argv.index("--max-concurrency") + 1] == "8"
    assert measured_argv[measured_argv.index("--timeline") + 1] == "false"
    assert measured_argv[measured_argv.index("--request-log") + 1] == "true"
    assert "--stream-idle-timeout-secs" not in measured_argv

    requests = measured["requests"]
    assert [request["id"].rsplit("-", 1)[1] for request in requests] == [
        "sample1",
        "sample2",
        "sample3",
        "sample4",
        "sample1",
    ]
    for request in requests:
        assert request["arrival_time_ms"] == 0.0
        assert request["outputs"] == [{"type": "text", "max_tokens": 448}]
        [audio] = request["inputs"]
        assert audio["type"] == "audio"
        asset_path = Path(audio["asset"]["path"])
        assert asset_path.is_absolute()
        assert audio["asset"]["media_type"] == "audio/wav"
        assert audio["asset"]["sha256"] == hashlib.sha256(asset_path.read_bytes()).hexdigest()

    report = capsys.readouterr().out
    assert "Completed:      5/5 (0 errors)" in report
    assert "Throughput:     2.50 req/s" in report
    assert "p50 0.300  p95 0.500  p99 0.500" in report


def test_preserves_partial_success_metric_and_warning(
    benchmark_module: ModuleType,
    fake_engine: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "result.json"
    monkeypatch.setenv("FAKE_REQUEST_FACTORY_CAPTURE", str(tmp_path / "capture.jsonl"))
    monkeypatch.setenv("FAKE_REQUEST_FACTORY_MODE", "one-failure")

    assert benchmark_module.main(_run_arguments(fake_engine, output)) == 0

    assert json.loads(output.read_text()) == {"requests_per_second": 2.0}
    assert "WARNING: only 4/5 requests succeeded." in capsys.readouterr().out


@pytest.mark.parametrize(
    "case",
    [
        ("engine-error", "engine exited with 7"),
        ("bad-summary", "reports 4 attempts, expected 5"),
        ("missing-log", "omitted 1 measured request records"),
    ],
)
def test_structural_or_engine_failure_writes_no_metric(
    fake_engine: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    case: tuple[str, str],
) -> None:
    benchmark_module = _load_benchmark()
    mode, message = case
    output = tmp_path / "result.json"
    monkeypatch.setenv("FAKE_REQUEST_FACTORY_CAPTURE", str(tmp_path / "capture.jsonl"))
    monkeypatch.setenv("FAKE_REQUEST_FACTORY_MODE", mode)

    assert benchmark_module.main(_run_arguments(fake_engine, output)) == 1

    assert not output.exists()
    assert message in capsys.readouterr().err


def test_manifest_resolves_pinned_request_factory_adapter() -> None:
    bundle = load_input_bundle(_TASK_ROOT)

    assert bundle.manifest.evaluator is not None
    assert bundle.manifest.evaluator.name == "vibesys-evaluator-request-factory"
    assert bundle.manifest.evaluator.version == "0.1.0"
    assert bundle.manifest.benchmark.entrypoint == "request-factory-adapter"
    assert bundle.manifest.benchmark.args == ("benchmark/benchmark.py",)
    assert bundle.benchmark_result is not None
    assert bundle.benchmark_result.json_argument == "--output-json"
    assert bundle.benchmark_result.metric == "requests_per_second"
    assert bundle.benchmark_command[-1] == "benchmark/benchmark.py"
    assert any(Path(part).name == "adapter.py" for part in bundle.benchmark_command)
