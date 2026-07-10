from pathlib import Path
from types import SimpleNamespace

import pytest
from jinja2 import Environment, FileSystemLoader

_TEMPLATE_DIR = Path("src/vibe_serve/loops/evolve/templates")
_AGENT_TEMPLATE_DIR = Path("src/vibe_serve/loops/agent/templates")


class _RenderShim:
    def __init__(self, env):
        self._env = env

    def render(self, name, **kwargs):
        return self._env.get_template(name).render(**kwargs)


@pytest.fixture
def prompt():
    env = Environment(
        loader=FileSystemLoader([str(_TEMPLATE_DIR), str(_AGENT_TEMPLATE_DIR)]),
        keep_trailing_newline=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    return _RenderShim(env)


def test_judge_generic_skips_python_reward_hack_language(prompt):
    out = prompt.render(
        "judge_prompt.j2",
        accuracy_checker_path=None,
        bench_path=None,
        pass_criteria="check.sh passes",
        modality="text_generation",
        runtime_notes="",
        env_kind="docker",
        objective="minimize p50_ms",
        is_generic_example=True,
        target_check_instructions="Run bash check.sh.",
        target_bench_instructions="Run bash bench.sh.",
    )
    assert "cuda_graph_replays" not in out
    assert "uv run pytest" not in out
    assert "Run bash check.sh" in out
    assert "check.sh" in out and "read-only reference files" in out


def test_judge_non_generic_keeps_python_reward_hack_language(prompt):
    out = prompt.render(
        "judge_prompt.j2",
        accuracy_checker_path="/workspace/acc_checker",
        bench_path="/workspace/bench",
        pass_criteria="tok/s improves",
        modality="text_generation",
        runtime_notes="",
        env_kind="docker",
        objective="maximize tok/s",
        is_generic_example=False,
        target_check_instructions=None,
        target_bench_instructions=None,
    )
    assert "cuda_graph_replays" in out
    assert "uv run pytest" in out


def test_mutator_generic_cold_start_skips_health_language(prompt):
    out = prompt.render(
        "mutator_prompt.j2",
        reference_path="reference/",
        modality="text_generation",
        objective="minimize p50_ms",
        parent=None,
        inspirations=[],
        is_cold_start=True,
        objectives=None,
        runtime_notes="",
        env_kind="docker",
        is_generic_example=True,
        target_check_instructions="Run bash check.sh.",
        target_bench_instructions="Run bash bench.sh; watch p50_ms.",
    )
    assert "Boots and exposes `/health`" not in out
    assert "Run bash bench.sh" in out


def test_mutator_non_generic_cold_start_keeps_health_language(prompt):
    out = prompt.render(
        "mutator_prompt.j2",
        reference_path="reference/model.py",
        modality="text_generation",
        objective="maximize tok/s",
        parent=None,
        inspirations=[],
        is_cold_start=True,
        objectives=None,
        runtime_notes="",
        env_kind="docker",
        is_generic_example=False,
        target_check_instructions=None,
        target_bench_instructions=None,
    )
    assert "Boots and exposes `/health`" in out


def test_mutator_generic_with_parent_skips_vibeservemodel(prompt):
    parent = SimpleNamespace(
        id=3,
        generation=2,
        perf_metric=42.0,
        perf_unit="p50_ms",
        metrics={},
        summary="Reduced lock contention.",
        feedback="",
    )
    out = prompt.render(
        "mutator_prompt.j2",
        reference_path="reference/",
        modality="text_generation",
        objective="minimize p50_ms",
        parent=parent,
        inspirations=[],
        is_cold_start=False,
        objectives=None,
        runtime_notes="",
        env_kind="docker",
        is_generic_example=True,
        target_check_instructions="Run bash check.sh.",
        target_bench_instructions="Run bash bench.sh; watch p50_ms.",
    )
    assert "from_pretrained" not in out
    assert "#3" in out
