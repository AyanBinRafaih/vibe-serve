import {describe, expect, it} from 'bun:test';
import type {ProtocolResponse, RunEvent} from '@vibesys/backend-client';
import {renderPerformanceCurve} from './performance-chart.js';

describe('renderPerformanceCurve', () => {
  it('plots persisted performance records by round', () => {
    const chart = renderPerformanceCurve([
      performance(1, 1000),
      performance(2, 2000),
      performance(3, 1500),
    ]);

    expect(chart).toContain('Performance · total_ops_per_sec');
    expect(chart).toContain('r1');
    expect(chart).toContain('r3');
    expect(chart).toContain('best r2 2k total_ops_per_sec');
    expect(chart).toContain('latest r3 1.5k total_ops_per_sec');
    expect(chart.match(/●/g)).toHaveLength(3);
  });

  it('falls back to benchmark events', () => {
    const chart = renderPerformanceCurve(
      [],
      [benchmark(1, 1, 1000), benchmark(2, 2, 2000), benchmark(3, 3, 1500)],
    );

    expect(chart).toContain('Performance · total_ops_per_sec');
    expect(chart).toContain('r1');
    expect(chart).toContain('r3');
    expect(chart).toContain('best r2 2k ops/s');
    expect(chart).toContain('latest r3 1.5k ops/s');
    expect(chart.match(/●/g)).toHaveLength(3);
  });

  it('handles missing data', () => {
    expect(renderPerformanceCurve([])).toBe('No performance data yet.');
  });

  it('titles the plot with the backend metric name instead of the unit', () => {
    const record = {...performance(1, 1000), perf_unit: 'ops/s'};
    const chart = renderPerformanceCurve([record], [], context({objective_unit: 'ops/s'}));

    expect(chart).toContain('Performance · total_ops_per_sec');
    expect(chart).not.toContain('Performance · ops/s');
    expect(chart.match(/●/g)).toHaveLength(1);
  });

  it('states metric, unit, direction, and baseline above the plot', () => {
    const chart = renderPerformanceCurve(
      [performance(1, 1000), performance(2, 2000)],
      [],
      context({
        objective_unit: 'ops/s',
        objective_direction: 'max',
        objective_baseline_value: 1234.5,
        objective_baseline_round: 1,
        objective_baseline_commit: 'e17fce8123abc',
        objective_description: 'Throughput of the MPMC queue benchmark.',
      }),
    );

    expect(chart).toContain('Metric    total_ops_per_sec (ops/s) · maximize ↑');
    expect(chart).toContain('Baseline  1234.5 · r1 · commit e17fce8');
    expect(chart).toContain('Measures  Throughput of the MPMC queue benchmark.');
  });

  it('spells minimize with its glyph for a lower-is-better objective', () => {
    const chart = renderPerformanceCurve(
      [performance(1, 1000)],
      [],
      context({objective_metric: 'p99_latency_us', objective_direction: 'min'}),
    );

    expect(chart).toContain('Metric    p99_latency_us · minimize ↓');
  });

  it('drops the lines for facts the run never recorded', () => {
    const chart = renderPerformanceCurve([performance(1, 1000)], [], context({}));

    expect(chart).toContain('Metric    total_ops_per_sec');
    expect(chart).not.toContain('maximize');
    expect(chart).not.toContain('minimize');
    expect(chart).not.toContain('Baseline');
    expect(chart).not.toContain('Measures');
  });

  it('does not repeat a unit slot that just holds the metric name', () => {
    const chart = renderPerformanceCurve(
      [performance(1, 1000)],
      [],
      context({objective_unit: 'total_ops_per_sec'}),
    );

    expect(chart).not.toContain('(total_ops_per_sec)');
  });

  it('renders a description-only context when only the prose is known', () => {
    const chart = renderPerformanceCurve([], [], {
      objective_description: 'Maximize queue throughput.',
    });

    expect(chart).toContain('Measures  Maximize queue throughput.');
    expect(chart).not.toContain('Metric ');
    expect(chart.endsWith('No performance data yet.')).toBe(true);
  });

  it('shows the objective before the first measurement', () => {
    const chart = renderPerformanceCurve(
      [],
      [],
      context({objective_direction: 'max', objective_description: 'Ops per second.'}),
    );

    expect(chart).toContain('Metric    total_ops_per_sec · maximize ↑');
    expect(chart).toContain('Measures  Ops per second.');
    expect(chart.endsWith('No performance data yet.')).toBe(true);
    expect(chart).not.toContain('●');
  });
});

function context(
  overrides: Partial<NonNullable<ProtocolResponse['performance_context']>>,
): NonNullable<ProtocolResponse['performance_context']> {
  return {objective_metric: 'total_ops_per_sec', ...overrides};
}

function performance(
  round: number,
  value: number,
): NonNullable<ProtocolResponse['performance']>[number] {
  return {
    round,
    perf_metric: value,
    perf_unit: 'total_ops_per_sec',
    passed: true,
    profile_skipped: false,
  };
}

function benchmark(sequence: number, round: number, value: number): RunEvent {
  return {
    sequence,
    timestamp: '2026-01-01T00:00:00Z',
    type: 'benchmark_result',
    round_label: `round-${round}`,
    data: {
      kind: 'benchmark_result',
      metric: 'total_ops_per_sec',
      value,
      unit: 'ops/s',
    },
  };
}
