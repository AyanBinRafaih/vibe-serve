"""Shared profiler invocation helpers.

Two loops drive the Profiler agent today: ``agent/loop.py`` (per-round
profiling, owns the round/progress.md side-effects) and
``evolve/loop.py`` (per-offspring profiling, with an optional
Pareto-frontier addendum).  Both build an MCP server spec for the
analysis tools (torch profiler or nsys), render their own system
prompt, and call ``ctx.invoke(kind="profiler", ...)`` with a
``ProfilerSummary`` fallback.

This module owns the parts that are identical across the two: the
``MCPServerSpec`` factory and the agent-invocation wrapper. Each loop
still renders its own prompt (the templates and bound variables differ)
and decides what to do with the returned summary.
"""

from __future__ import annotations

from vibe_serve.schemas import ProfilerSummary


def mcp_spec(profiler_kind: str):
    """Build an ``MCPServerSpec`` that spawns the analysis MCP server.

    Returns ``None`` when ``vibe_serve._agent_cli`` is not importable in the
    current environment (e.g. a unit-test process that doesn't pull in
    the cli runner).  Callers treat ``None`` as "skip MCP"; the
    profiler agent still runs, just without tool access.
    """
    try:
        from vibe_serve._agent_cli import MCPServerSpec
    except Exception:
        return None
    if profiler_kind == "none":
        return None
    if profiler_kind == "torch":
        return MCPServerSpec(
            name="vibeserve-torch-profiler",
            command="python",
            args=["torch_profiler/server.py"],
        )
    if profiler_kind == "neuron":
        return MCPServerSpec(
            name="vibeserve-neuron-profiler",
            command="python",
            args=["neuron_profiler/server.py"],
        )
    return MCPServerSpec(
        name="vibeserve-nsys-profiler",
        command="python",
        args=["nsys_profiler/server.py"],
    )


def invoke_profiler(
    ctx,
    *,
    system_prompt: str,
    round_label: str,
    fallback_suggestions: str = "Re-run profiling on the next round.",
) -> ProfilerSummary | None:
    """Run the Profiler agent and return its :class:`ProfilerSummary`.

    Side-effect free: the caller owns logging the result, writing it to
    progress.md, snapshotting the workspace, etc. Returns ``None`` on
    exception (the caller decides whether that's fatal).
    """
    spec = mcp_spec(ctx.profiler_kind)
    try:
        return ctx.invoke(
            kind="profiler",
            system_prompt=system_prompt,
            user_prompt=(
                "Profile the server and return exactly one JSON object matching the schema above."
            ),
            response_cls=ProfilerSummary,
            fallback_factory=lambda: ProfilerSummary(
                analysis="Profiler produced no structured response.",
                bottlenecks="n/a",
                suggestions=fallback_suggestions,
                perf_metric=None,
                perf_unit=None,
            ),
            round_label=round_label,
            mcp_servers=[spec] if spec is not None else None,
        )
    except Exception as exc:
        ctx.lprint(f"[warn] profiler failed: {exc}")
        return None


def run_example_benchmark(ctx, *, round_label: str) -> ProfilerSummary | None:
    """Run a generic example's bench.sh and parse its JSONL metrics (issue #76).

    Generic examples have no GPU profiler; the declared benchmark harness
    (bench.sh) is the source of truth for the primary metric. This runs
    bench.sh in the sandbox, parses ``{"metric","value"}`` JSONL from both
    stdout and ``VIBESERVE_OUTPUT_DIR``, and returns a ProfilerSummary whose
    ``perf_metric`` / ``metrics`` the evolve and agent loops already consume
    for comparison (evolve ranks by ``metrics[primary_metric]`` with the
    manifest's declared direction). Returns None when bench.sh fails or emits
    no primary metric, so the caller treats the candidate as unmeasured.
    """
    manifest = ctx.example_manifest
    if manifest is None:
        return None
    sandbox = ctx.implementer_backend
    out_dir = "/workspace/example_output"
    try:
        sandbox.execute(f"mkdir -p {out_dir}")
        result = sandbox.execute(
            "cd /workspace/example && bash bench.sh",
            timeout=manifest.setup.timeout_sec,
        )
    except Exception as exc:
        ctx.lprint(f"[{round_label}] example bench.sh failed to run: {exc}")
        return None
    if getattr(result, "exit_code", 0) != 0:
        ctx.lprint(f"[{round_label}] bench.sh exited {result.exit_code}; candidate unmeasured.")
        return None
    text = getattr(result, "output", "") or ""
    try:
        extra = sandbox.execute(f"cat {out_dir}/*.jsonl 2>/dev/null || true")
        text = text + "\n" + (getattr(extra, "output", "") or "")
    except Exception:
        pass
    metrics = manifest.parse_metrics(text)
    primary_name = manifest.benchmark.primary_metric
    primary = metrics.get(primary_name)
    ctx.lprint(
        f"[{round_label}] bench.sh metrics={metrics} "
        f"primary({primary_name})={primary} ({manifest.benchmark.direction})"
    )
    if primary is None:
        ctx.lprint(f"[{round_label}] bench.sh emitted no '{primary_name}' metric; unmeasured.")
        return None
    return ProfilerSummary(
        analysis=f"Parsed {len(metrics)} metric(s) from bench.sh JSONL output.",
        bottlenecks="n/a (generic example: no GPU profiler; bench.sh is the metric source)",
        suggestions="n/a",
        perf_metric=primary,
        perf_unit=primary_name,
        metrics=metrics,
    )
