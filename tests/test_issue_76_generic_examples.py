from pathlib import Path

from vibe_serve.loops.profiler import mcp_spec
from vibe_serve.schemas import LoadLevelMetrics, ThroughputStats


def test_mcp_spec_none_returns_none():
    assert mcp_spec("none") is None


def test_mcp_spec_torch_unchanged():
    spec = mcp_spec("torch")
    assert spec is None or spec.name == "vibeserve-torch-profiler"


def test_throughput_stats_token_throughput_optional():
    stats = ThroughputStats(request_throughput=120.5)
    assert stats.token_throughput is None


def test_throughput_stats_token_throughput_still_settable():
    stats = ThroughputStats(request_throughput=120.5, token_throughput=980.0)
    assert stats.token_throughput == 980.0


def test_load_level_metrics_without_token_throughput(tmp_path):
    m = LoadLevelMetrics(
        target_rate=100.0,
        actual_rate=98.2,
        num_requests=1000,
        num_completed=990,
        num_failed=10,
        duration=10.0,
        throughput=ThroughputStats(request_throughput=98.2),
    )
    assert m.throughput.token_throughput is None


def test_cli_expand_example_flag_fills_all_three(monkeypatch):
    from vibe_serve.cli import _expand_example_flag

    argv = ["--outer-loop", "agent", "--example", "examples/social-network-read-timeline"]
    out = _expand_example_flag(argv)
    assert "--ref" in out
    assert str(Path("examples/social-network-read-timeline/reference")) in out
    assert str(Path("examples/social-network-read-timeline/accuracy_checker")) in out
    assert str(Path("examples/social-network-read-timeline/benchmark")) in out


def test_cli_expand_example_flag_respects_explicit_ref():
    from vibe_serve.cli import _expand_example_flag

    argv = [
        "--example",
        "examples/social-network-read-timeline",
        "--ref",
        "custom/ref/path",
    ]
    out = _expand_example_flag(argv)
    assert out.count("--ref") == 1
    assert "custom/ref/path" in out


def test_cli_expand_example_flag_noop_when_absent():
    from vibe_serve.cli import _expand_example_flag

    argv = ["--outer-loop", "agent", "--ref", "examples/Llama-3-8B/reference"]
    assert _expand_example_flag(argv) == argv
