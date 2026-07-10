from types import SimpleNamespace

import pytest

from vibe_serve.constants import ComputeBackend
from vibe_serve.prompts import Prompt

_TEMPLATE_DIR = "src/vibe_serve/loops/plain/templates"


def _fake_issue():
    return SimpleNamespace(
        id=7,
        type=SimpleNamespace(value="perf"),
        title="Reduce p50_ms",
        description="Speed things up.",
    )


@pytest.fixture
def prompt():
    return Prompt(_TEMPLATE_DIR, ComputeBackend.CUDA)


def test_implementer_generic_skips_fastapi_language(prompt):
    out = prompt.render(
        "implementer/system.j2",
        reference_path="reference/",
        runtime_notes="",
        issue=_fake_issue(),
        is_generic_example=True,
        target_check_instructions="Run bash check.sh.",
        target_bench_instructions="Run bash bench.sh; watch p50_ms.",
    )
    assert "uv init" not in out
    assert "token_throughput > 0" not in out
    assert "Run bash bench.sh" in out


def test_implementer_non_generic_keeps_fastapi_language(prompt):
    out = prompt.render(
        "implementer/system.j2",
        reference_path="reference/model.py",
        runtime_notes="",
        issue=_fake_issue(),
        is_generic_example=False,
        target_check_instructions=None,
        target_bench_instructions=None,
    )
    assert "FastAPI" in out


def test_judge_generic_skips_pytest_and_vibeservemodel_language(prompt):
    out = prompt.render(
        "judge/system.j2",
        accuracy_checker_path=None,
        bench_path=None,
        issue=_fake_issue(),
        is_generic_example=True,
        target_check_instructions="Run bash check.sh.",
        target_bench_instructions="Run bash bench.sh.",
    )
    assert "from_pretrained" not in out
    assert "uv run pytest" not in out
    assert "Run bash check.sh" in out


def test_judge_non_generic_keeps_pytest_language(prompt):
    out = prompt.render(
        "judge/system.j2",
        accuracy_checker_path="/workspace/acc_checker",
        bench_path="/workspace/bench",
        issue=_fake_issue(),
        is_generic_example=False,
        target_check_instructions=None,
        target_bench_instructions=None,
    )
    assert "uv run pytest" in out


def test_perf_eval_generic_skips_ttft_tpot_language(prompt):
    out = prompt.render(
        "perf_eval/system.j2",
        load_levels=None,
        progress_path="progress.md",
        perf_metrics_path="perf_metrics.json",
        previous_evaluator_feedback=[],
        issue_create_cap=3,
        runtime_notes="",
        is_generic_example=True,
        target_bench_instructions="Run bash bench.sh; watch p50_ms.",
        primary_metric="p50_ms",
        metric_direction="minimize",
    )
    # The generic branch legitimately *mentions* TTFT/TPOT/--max-tokens as
    # things NOT to expect -- so assert the Python-serving-only *procedural*
    # sections (multi-load-level curl/uvicorn steps, the ttft/tpot output
    # schema block) are absent, not that the words never appear at all.
    assert "uv run python bench/benchmark.py" not in out
    assert '"ttft": {"mean"' not in out
    assert "p50_ms" in out
    assert "minimize" in out


def test_perf_eval_non_generic_keeps_ttft_tpot_language(prompt):
    out = prompt.render(
        "perf_eval/system.j2",
        load_levels=None,
        progress_path="progress.md",
        perf_metrics_path="perf_metrics.json",
        previous_evaluator_feedback=[],
        issue_create_cap=3,
        runtime_notes="",
        is_generic_example=False,
        target_bench_instructions=None,
        primary_metric=None,
        metric_direction=None,
    )
    assert "TTFT" in out
