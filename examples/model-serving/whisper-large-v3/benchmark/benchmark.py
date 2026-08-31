"""Offline serving benchmark for whisper-large-v3 through Request Factory.

The evaluator package injects a pinned ``session_runner`` path. This adapter
materializes immutable audio requests, runs an unmeasured warmup phase followed
by a measured saturated phase, and projects Request Factory's summary onto the
historical top-level ``requests_per_second`` result contract.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import signal
import subprocess
import sys
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_MODEL = "whisper-large-v3"
_OUTPUT_MAX_TOKENS = 448


class BenchmarkError(RuntimeError):
    """A materialization, engine, or result-contract failure."""


@dataclass(frozen=True)
class AudioClip:
    """One verified WAV input and its benchmark duration."""

    path: Path
    duration_s: float
    sha256: str


@dataclass(frozen=True)
class PhaseArtifacts:
    """Request Factory inputs and outputs for one phase."""

    trace: Path
    request_log: Path
    summary: Path


@dataclass(frozen=True)
class PhaseSummary:
    """The stable Request Factory fields this benchmark consumes."""

    attempted: int
    succeeded: int
    failed: int
    duration_s: float
    requests_per_second: float


@dataclass(frozen=True)
class MeasuredRequests:
    """Success-only diagnostic observations from the measured request log."""

    latencies_s: tuple[float, ...]
    audio_seconds: float
    failed: int


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def _positive_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("must be finite and positive")
    return parsed


def _load_manifest(audio_dir: Path) -> dict[str, dict[str, Any]]:
    path = audio_dir / "manifest.json"
    if not path.exists():
        return {}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise BenchmarkError(f"failed to read audio manifest {path}: {error}") from error
    if not isinstance(document, list):
        raise BenchmarkError(f"audio manifest {path} must contain a JSON list")
    manifest: dict[str, dict[str, Any]] = {}
    for index, entry in enumerate(document):
        if not isinstance(entry, dict) or not isinstance(entry.get("file"), str):
            raise BenchmarkError(f"audio manifest {path} entry {index} must name a file")
        filename = entry["file"]
        if filename in manifest:
            raise BenchmarkError(f"audio manifest {path} repeats file {filename!r}")
        manifest[filename] = entry
    return manifest


def load_audio_pool(audio_dir: Path) -> list[AudioClip]:
    """Load sorted PCM16 mono 16 kHz WAV metadata and immutable hashes."""
    manifest = _load_manifest(audio_dir)
    pool: list[AudioClip] = []
    for path in sorted(audio_dir.glob("*.wav")):
        try:
            with wave.open(str(path), "rb") as reader:
                if reader.getsampwidth() != 2:
                    raise BenchmarkError(f"{path} is not 16-bit PCM")
                if reader.getnchannels() != 1:
                    raise BenchmarkError(f"{path} is not mono")
                if reader.getframerate() != 16_000:
                    raise BenchmarkError(f"{path} is not 16 kHz")
                measured_duration = reader.getnframes() / reader.getframerate()
        except (OSError, EOFError, wave.Error) as error:
            raise BenchmarkError(f"failed to read WAV file {path}: {error}") from error
        declared_duration = manifest.get(path.name, {}).get("duration_s", measured_duration)
        if (
            isinstance(declared_duration, bool)
            or not isinstance(declared_duration, (int, float))
            or not math.isfinite(declared_duration)
            or declared_duration <= 0
        ):
            raise BenchmarkError(f"invalid duration_s for {path.name}: {declared_duration!r}")
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as error:
            raise BenchmarkError(f"failed to hash audio file {path}: {error}") from error
        pool.append(AudioClip(path.resolve(), float(declared_duration), digest))
    if not pool:
        raise BenchmarkError(f"no WAV files in {audio_dir}")
    return pool


def _request_id(phase: str, index: int, clip: AudioClip) -> str:
    return f"{phase}-{index:06d}-{clip.path.stem}"


def materialize_trace(
    path: Path,
    pool: list[AudioClip],
    *,
    phase: str,
    count: int,
) -> dict[str, float]:
    """Write canonical Request Factory rows and return request durations by ID."""
    durations: dict[str, float] = {}
    with path.open("w", encoding="utf-8") as writer:
        for index in range(count):
            clip = pool[index % len(pool)]
            request_id = _request_id(phase, index, clip)
            durations[request_id] = clip.duration_s
            request = {
                "id": request_id,
                "arrival_time_ms": 0.0,
                "inputs": [
                    {
                        "type": "audio",
                        "asset": {
                            "path": str(clip.path),
                            "sha256": clip.sha256,
                            "media_type": "audio/wav",
                        },
                    }
                ],
                "outputs": [{"type": "text", "max_tokens": _OUTPUT_MAX_TOKENS}],
            }
            writer.write(json.dumps(request, separators=(",", ":")) + "\n")
    return durations


def _base_url(url: str) -> str:
    normalized = url.rstrip("/")
    return normalized if normalized.endswith("/v1") else f"{normalized}/v1"


def _phase_artifacts(run_dir: Path, phase: str) -> PhaseArtifacts:
    return PhaseArtifacts(
        trace=run_dir / f"{phase}.trace.jsonl",
        request_log=run_dir / f"{phase}.requests.jsonl",
        summary=run_dir / f"{phase}.summary.json",
    )


def _engine_command(
    engine: Path,
    artifacts: PhaseArtifacts,
    *,
    url: str,
    concurrency: int,
) -> list[str]:
    return [
        str(engine),
        "--trace",
        str(artifacts.trace),
        "--input-file-format",
        "multimodal-independent-v1",
        "--base-url",
        _base_url(url),
        "--model",
        _MODEL,
        "--backend",
        "openai-transcriptions",
        "--dialect",
        "openai",
        "--temperature",
        "0",
        "--arrival-mode",
        "saturated",
        "--max-concurrency",
        str(concurrency),
        "--timeline",
        "false",
        "--request-log",
        "true",
        "--log-path",
        str(artifacts.request_log),
        "--summary-path",
        str(artifacts.summary),
    ]


def _diagnostic_tail(completed: subprocess.CompletedProcess[str]) -> str:
    combined = "\n".join(
        part.strip() for part in (completed.stdout, completed.stderr) if part.strip()
    )
    return combined[-4_000:] if combined else "no engine diagnostics"


def _terminate_engine(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()


def _run_engine_process(command: list[str]) -> subprocess.CompletedProcess[str]:
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    previous_handlers: dict[signal.Signals, Any] = {}

    def interrupted(signum: int, _frame: object) -> None:
        raise BenchmarkError(f"interrupted while Request Factory was running (signal {signum})")

    try:
        for watched in (signal.SIGTERM, getattr(signal, "SIGHUP", None)):
            if watched is None:
                continue
            try:
                previous_handlers[watched] = signal.signal(watched, interrupted)
            except ValueError:
                # Unit callers may run this adapter outside the main thread.
                previous_handlers.clear()
                break
        try:
            stdout, stderr = process.communicate()
        except BaseException:
            _terminate_engine(process)
            raise
    finally:
        for watched, previous in previous_handlers.items():
            signal.signal(watched, previous)
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def _mapping(value: object, at: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise BenchmarkError(f"{at} must be a JSON object")
    return value


def _integer(mapping: dict[str, Any], key: str, at: str) -> int:
    value = mapping.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise BenchmarkError(f"{at}.{key} must be a non-negative integer")
    return value


def _number(mapping: dict[str, Any], key: str, at: str) -> float:
    value = mapping.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BenchmarkError(f"{at}.{key} must be numeric")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise BenchmarkError(f"{at}.{key} must be finite and non-negative")
    return number


def _read_summary(path: Path, expected_requests: int) -> PhaseSummary:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise BenchmarkError(f"failed to read Request Factory summary {path}: {error}") from error
    root = _mapping(document, str(path))
    replay = _mapping(root.get("replay"), f"{path}.replay")
    if replay.get("kind") != "multimodal_requests":
        raise BenchmarkError(f"{path}.replay.kind must be 'multimodal_requests'")
    common = _mapping(replay.get("common"), f"{path}.replay.common")
    attempted = _integer(common, "attempted_steps", f"{path}.replay.common")
    succeeded = _integer(common, "success_steps", f"{path}.replay.common")
    failed = _integer(common, "failed_steps", f"{path}.replay.common")
    if attempted != expected_requests:
        raise BenchmarkError(f"{path} reports {attempted} attempts, expected {expected_requests}")
    if succeeded + failed != attempted:
        raise BenchmarkError(f"{path} success and failure counts do not sum to attempts")
    return PhaseSummary(
        attempted=attempted,
        succeeded=succeeded,
        failed=failed,
        duration_s=_number(common, "run_duration_ms", f"{path}.replay.common") / 1_000,
        requests_per_second=_number(common, "request_throughput_per_s", f"{path}.replay.common"),
    )


def _run_phase(
    engine: Path,
    artifacts: PhaseArtifacts,
    *,
    url: str,
    concurrency: int,
    expected_requests: int,
) -> PhaseSummary:
    command = _engine_command(
        engine,
        artifacts,
        url=url,
        concurrency=concurrency,
    )
    try:
        completed = _run_engine_process(command)
    except OSError as error:
        raise BenchmarkError(
            f"failed to launch Request Factory engine {engine}: {error}"
        ) from error
    if completed.returncode != 0:
        raise BenchmarkError(
            f"Request Factory engine exited with {completed.returncode}:\n"
            f"{_diagnostic_tail(completed)}"
        )
    return _read_summary(artifacts.summary, expected_requests)


def _read_measured_requests(
    path: Path,
    durations: dict[str, float],
) -> MeasuredRequests:
    latencies: list[float] = []
    audio_seconds = 0.0
    failed = 0
    seen: set[str] = set()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise BenchmarkError(
            f"failed to read Request Factory request log {path}: {error}"
        ) from error
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            record = _mapping(json.loads(line), f"{path}:{line_number}")
        except json.JSONDecodeError as error:
            raise BenchmarkError(f"invalid JSON in {path}:{line_number}: {error}") from error
        source = _mapping(record.get("source"), f"{path}:{line_number}.source")
        if source.get("type") != "multimodal_request":
            raise BenchmarkError(f"{path}:{line_number} has an unexpected source type")
        source_data = _mapping(source.get("data"), f"{path}:{line_number}.source.data")
        request_id = source_data.get("id")
        if not isinstance(request_id, str) or request_id not in durations:
            raise BenchmarkError(f"{path}:{line_number} has an unknown request_id")
        if request_id in seen:
            raise BenchmarkError(f"{path}:{line_number} repeats request_id {request_id!r}")
        seen.add(request_id)
        outcome = _mapping(record.get("outcome"), f"{path}:{line_number}.outcome")
        outcome_request_id = outcome.get("request_id")
        if outcome_request_id is not None and outcome_request_id != request_id:
            raise BenchmarkError(f"{path}:{line_number} source and outcome request IDs disagree")
        status = outcome.get("status")
        if status == "SUCCESS":
            latencies.append(_number(outcome, "total_duration_ms", f"{path}:{line_number}") / 1_000)
            audio_seconds += durations[request_id]
        else:
            failed += 1
    if seen != durations.keys():
        missing = sorted(durations.keys() - seen)
        raise BenchmarkError(f"{path} omitted {len(missing)} measured request records")
    return MeasuredRequests(tuple(sorted(latencies)), audio_seconds, failed)


def _percentile(sorted_values: tuple[float, ...], percentile: int) -> float:
    if not sorted_values:
        return 0.0
    index = min(
        len(sorted_values) - 1,
        int(round((percentile / 100) * (len(sorted_values) - 1))),
    )
    return sorted_values[index]


def _print_report(
    args: argparse.Namespace,
    pool: list[AudioClip],
    summary: PhaseSummary,
    requests: MeasuredRequests,
) -> None:
    print("=" * 44)
    print("  whisper-large-v3 offline benchmark")
    print("=" * 44)
    print(f"URL:            {args.url}")
    print(f"Concurrency:    {args.concurrency}")
    print(f"Completed:      {summary.succeeded}/{summary.attempted} ({summary.failed} errors)")
    print(f"Wall time:      {summary.duration_s:.2f}s")
    print(f"Audio pool:     {sum(clip.duration_s for clip in pool):.1f}s over {len(pool)} clips")
    print()
    print(f"Throughput:     {summary.requests_per_second:.2f} req/s")
    audio_rate = requests.audio_seconds / summary.duration_s if summary.duration_s > 0 else 0.0
    print(f"                {audio_rate:.1f} audio-s / wall-s")
    if requests.latencies_s:
        mean = sum(requests.latencies_s) / len(requests.latencies_s)
        print(
            f"Latency (s):    mean {mean:.3f}  "
            f"p50 {_percentile(requests.latencies_s, 50):.3f}  "
            f"p95 {_percentile(requests.latencies_s, 95):.3f}  "
            f"p99 {_percentile(requests.latencies_s, 99):.3f}"
        )
    print("=" * 44)
    if summary.succeeded < args.num_requests:
        print(f"WARNING: only {summary.succeeded}/{args.num_requests} requests succeeded.")


def run_benchmark(args: argparse.Namespace) -> dict[str, float]:
    """Execute warmup and measured Request Factory phases."""
    engine = Path(args.request_factory_engine).resolve()
    if not engine.is_file() or not os.access(engine, os.X_OK):
        raise BenchmarkError(f"Request Factory engine is not executable: {engine}")
    audio_dir = (
        Path(args.audio_dir).resolve()
        if args.audio_dir
        else (Path(__file__).parent.parent / "test_audio").resolve()
    )
    pool = load_audio_pool(audio_dir)
    warmup_count = min(args.concurrency, len(pool))

    with tempfile.TemporaryDirectory(prefix="whisper-request-factory-") as temporary:
        run_dir = Path(temporary)
        warmup = _phase_artifacts(run_dir, "warmup")
        measured = _phase_artifacts(run_dir, "measure")
        materialize_trace(warmup.trace, pool, phase="warmup", count=warmup_count)
        measured_durations = materialize_trace(
            measured.trace,
            pool,
            phase="measure",
            count=args.num_requests,
        )
        _run_phase(
            engine,
            warmup,
            url=args.url,
            concurrency=args.concurrency,
            expected_requests=warmup_count,
        )
        summary = _run_phase(
            engine,
            measured,
            url=args.url,
            concurrency=args.concurrency,
            expected_requests=args.num_requests,
        )
        requests = _read_measured_requests(measured.request_log, measured_durations)

    if requests.failed != summary.failed or len(requests.latencies_s) != summary.succeeded:
        raise BenchmarkError(
            "measured request log counts disagree with the Request Factory summary"
        )
    _print_report(args, pool, summary, requests)
    return {"requests_per_second": summary.requests_per_second}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request-factory-engine", required=True)
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--concurrency", type=_positive_int, default=8)
    parser.add_argument("--num-requests", type=_positive_int, default=64)
    parser.add_argument(
        "--request-timeout",
        type=_positive_float,
        default=120.0,
        help=(
            "Accepted for CLI compatibility. Request Factory currently uses a fixed timeout "
            "for nonstreaming responses, so this setting cannot be enforced."
        ),
    )
    parser.add_argument("--audio-dir")
    parser.add_argument("--output-json")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the benchmark and write the trusted scalar result."""
    args = _parser().parse_args(argv)
    try:
        result = run_benchmark(args)
        if args.output_json:
            output = Path(args.output_json)
            output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
            print(f"\nResults written to {output}")
    except BenchmarkError as error:
        print(f"benchmark failed: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("benchmark interrupted", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
