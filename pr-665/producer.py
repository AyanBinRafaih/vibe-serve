"""Run the checked-out implementation; write its result and a TUI display fixture."""

from collections import Counter
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import random
import subprocess
import sys

from server.events import RunEvent
from vibesys.loops.evolve.population import Individual, Population
from vibesys.loops.metrics import MetricSpace, Objective

out = Path(sys.argv[1])
side = sys.argv[2]
out.mkdir(parents=True, exist_ok=True)
space = MetricSpace(objectives=(
    Objective(name='latency_ms', direction='min'),
    Objective(name='quality', direction='max'),
))
candidates = [
    Individual(id=1, generation=0, parent_id=None, commit='1' * 40, passed=True,
               perf_metric=10.0, perf_unit='ms', metrics={'latency_ms': 10.0, 'quality': 80.0}),
    Individual(id=2, generation=0, parent_id=None, commit='2' * 40, passed=True,
               perf_metric=100.0, perf_unit='ms', metrics={'latency_ms': 100.0, 'quality': 90.0}),
]
population = Population(candidates)
options = {'space': space, 'frontier_bias': 0.0, 'temperature': 0.01}
selected = population.select_parent(rng=random.Random(0), **options)
assert selected is not None
rng = random.Random(0)
counts = Counter(population.select_parent(rng=rng, **options).id for _ in range(100))
result = {
    'revision': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
    'implementation_module': str(Path(sys.modules[Population.__module__].__file__).resolve()),
    'function': 'Population.select_parent',
    'space': space.model_dump(), 'seed': 0, 'frontier_bias': 0.0, 'temperature': 0.01,
    'candidates': [{'id': i.id, 'perf_metric': i.perf_metric, 'perf_unit': i.perf_unit,
                    'metrics': i.metrics} for i in candidates],
    'selected_parent_id': selected.id,
    'selected_latency_ms': selected.perf_metric,
    'selected_quality': selected.metrics['quality'],
    'draw_count': 100,
    'draw_counts': {str(i.id): counts[i.id] for i in candidates},
}
(out / f'{side}-producer.json').write_text(json.dumps(result, indent=2) + '\n')
lines = [
    'NATIVE EVOLVE PARENT SELECTION | latency_ms MINIMIZE; quality MAXIMIZE',
    'Inputs: ' + '; '.join(f'candidate {i.id}: {i.perf_metric:g} ms, quality {i.metrics["quality"]:g}' for i in candidates),
    'Scalar fallback: frontier_bias=0, temperature=0.01, RNG seed=0',
    f'Selected parent: candidate {selected.id} | Selected latency: {selected.perf_metric:g} ms',
    f'100 seeded draws: candidate 1 = {counts[1]}, candidate 2 = {counts[2]}',
    'Producer: Population.select_parent (unmodified checkout). No agent or benchmark process was run.',
]
content = '\n'.join(lines) + '\n'
(out / f'{side}-producer.txt').write_text(content)
start = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)
envelopes = [
    {'type': 'run_started', 'status': 'active', 'data': {
        'kind': 'run_started', 'outer_loop': 'evolve', 'input': 'deterministic parent selection probe',
        'max_rounds': 1, 'expected_roles': [],
    }},
    {'type': 'subprocess_output', 'round_label': 'round-1', 'data': {
        'kind': 'subprocess_output', 'process_id': 'selection-probe',
        'process_kind': 'selection-probe', 'stream': 'stdout', 'content': content,
    }},
    {'type': 'round_finished', 'round_label': 'round-1', 'status': 'completed', 'data': {
        'kind': 'round_finished', 'attempts': 0, 'judge_verdict': 'skipped', 'profile_skipped': True,
    }},
    {'type': 'run_finished', 'status': 'completed', 'text': 'Selection probe complete.'},
]
events = [RunEvent.model_validate({
    'protocol_version': 1, 'sequence': index + 1, 'run_id': 'parent-selection-probe',
    'timestamp': start + timedelta(seconds=index), **envelope,
}) for index, envelope in enumerate(envelopes)]
(out / f'{side}-fixture.jsonl').write_text(''.join(e.model_dump_json() + '\n' for e in events))
print(content)
