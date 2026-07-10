from pathlib import Path

from vibe_serve.prompts import render_template

_TEMPLATE_DIR = Path("src/vibe_serve/loops/agent/templates")


def test_judge_prompt_uses_generic_instructions_when_flagged():
    out = render_template(
        "judge_prompt.j2",
        template_dir=_TEMPLATE_DIR,
        accuracy_checker_path="/workspace/acc_checker",
        bench_path="/workspace/bench",
        pass_criteria="check.sh passes",
        modality="text_generation",
        domain_judge="",
        retry=0,
        runtime_notes="",
        env_kind="docker",
        objective="minimize p50_ms",
        is_generic_example=True,
        target_check_instructions="Run `bash check.sh` from the example root.",
        target_bench_instructions="Run `bash bench.sh`; watch p50_ms.",
    )
    assert "from_pretrained" not in out
    assert "Decode invariants" not in out
    assert "Run `bash check.sh`" in out


def test_judge_prompt_uses_modality_include_when_not_generic():
    out = render_template(
        "judge_prompt.j2",
        template_dir=_TEMPLATE_DIR,
        accuracy_checker_path="/workspace/acc_checker",
        bench_path="/workspace/bench",
        pass_criteria="server boots",
        modality="text_generation",
        domain_judge="",
        retry=0,
        runtime_notes="",
        env_kind="docker",
        objective="maximize tok/s",
        is_generic_example=False,
        target_check_instructions=None,
        target_bench_instructions=None,
    )
    assert "VibeServeModel" in out


def test_implementer_prompt_uses_generic_instructions_when_flagged():
    out = render_template(
        "implementer_prompt.j2",
        template_dir=_TEMPLATE_DIR,
        reference_path="reference/",
        modality="text_generation",
        domain_implementer="",
        task="Reduce p50_ms",
        pass_criteria="check.sh passes",
        retry=0,
        feedback=None,
        runtime_notes="",
        env_kind="docker",
        is_generic_example=True,
        target_check_instructions="Run `bash check.sh`.",
        target_bench_instructions="Run `bash bench.sh`; watch p50_ms.",
    )
    assert "from_pretrained" not in out
    assert "Decode invariants" not in out
    assert "Run `bash bench.sh`" in out


def test_implementer_prompt_uses_modality_include_when_not_generic():
    out = render_template(
        "implementer_prompt.j2",
        template_dir=_TEMPLATE_DIR,
        reference_path="reference/model.py",
        modality="text_generation",
        domain_implementer="",
        task="Speed up decode",
        pass_criteria="tok/s improves",
        retry=0,
        feedback=None,
        runtime_notes="",
        env_kind="docker",
        is_generic_example=False,
        target_check_instructions=None,
        target_bench_instructions=None,
    )
    assert "VibeServeModel" in out
