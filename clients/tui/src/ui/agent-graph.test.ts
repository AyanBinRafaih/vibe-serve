import {describe, expect, test} from 'bun:test';
import type {AgentPhase} from '@vibesys/core-state';
import {graphPaneBounds, layoutAgentGraph, NODE_HEIGHT, stageKinds} from './agent-graph.js';

function phase(kind: string, status: AgentPhase['status'], roundNumber = 1): AgentPhase {
  return {kind, status, roundNumber, roundLabel: `round-${roundNumber}-${kind}`};
}

const CHAIN = [
  phase('orchestrator', 'completed'),
  phase('implementer', 'active'),
  phase('judge', 'pending'),
];

describe('stageKinds', () => {
  test('keeps the order the round first mentions each kind', () => {
    expect(stageKinds(CHAIN)).toEqual(['orchestrator', 'implementer', 'judge']);
  });

  test('collapses repeats of a kind into one stage', () => {
    const parallel = [phase('implementer', 'active'), phase('implementer', 'completed')];
    expect(stageKinds(parallel)).toEqual(['implementer']);
  });
});

describe('layoutAgentGraph', () => {
  test('places one column per stage, left to right', () => {
    const graph = layoutAgentGraph(CHAIN, graphPaneBounds(3).max - 4);
    const xs = graph.nodes.map(node => node.x);
    expect(xs).toEqual([...xs].sort((a, b) => a - b));
    expect(new Set(xs).size).toBe(3);
    expect(graph.nodes.every(node => node.y === 0)).toBe(true);
  });

  test('stacks several agents of one kind inside their column', () => {
    const parallel = [
      phase('orchestrator', 'completed'),
      phase('implementer', 'active'),
      phase('implementer', 'active'),
    ];
    const graph = layoutAgentGraph(parallel, graphPaneBounds(2).max - 4);
    const implementers = graph.nodes.filter(node => node.phase.kind === 'implementer');
    expect(implementers).toHaveLength(2);
    expect(implementers[0]?.x).toBe(implementers[1]?.x as number);
    expect((implementers[1]?.y as number) - (implementers[0]?.y as number)).toBeGreaterThanOrEqual(
      NODE_HEIGHT,
    );
  });

  test('centres a shorter stage against the tallest one', () => {
    const parallel = [
      phase('orchestrator', 'completed'),
      phase('implementer', 'active'),
      phase('implementer', 'active'),
    ];
    const graph = layoutAgentGraph(parallel, graphPaneBounds(2).max - 4);
    const orchestrator = graph.nodes.find(node => node.phase.kind === 'orchestrator');
    expect(orchestrator?.y).toBeGreaterThan(0);
  });

  test('draws an arrow into every target and keeps edges inside the gutter', () => {
    const graph = layoutAgentGraph(CHAIN, graphPaneBounds(3).max - 4);
    const arrows = graph.cells.filter(cell => cell.glyph === '▶');
    expect(arrows).toHaveLength(2);
    const nodeWidth = graph.nodes[0]?.width as number;
    for (const cell of graph.cells) {
      const column = cell.x % (nodeWidth + 5);
      expect(column).toBeGreaterThanOrEqual(nodeWidth);
    }
  });

  test('tones an edge by the phases it connects', () => {
    const graph = layoutAgentGraph(
      [...CHAIN, phase('profiler', 'pending')],
      graphPaneBounds(4).max - 4,
    );
    // The frontier glows: an edge is live while either end is running. Two
    // stages that have not run yet stay idle.
    expect(graph.cells.some(cell => cell.tone === 'live')).toBe(true);
    expect(graph.cells.some(cell => cell.tone === 'idle')).toBe(true);
  });

  test('a finished handover between finished stages reads as done', () => {
    const done = [phase('implementer', 'completed'), phase('judge', 'completed')];
    const graph = layoutAgentGraph(done, graphPaneBounds(2).max - 4);
    expect(graph.cells.every(cell => cell.tone === 'done')).toBe(true);
  });

  test('a failed phase colors the edge leaving it', () => {
    const failed = [phase('implementer', 'failed'), phase('judge', 'pending')];
    const graph = layoutAgentGraph(failed, graphPaneBounds(2).max - 4);
    expect(graph.cells.every(cell => cell.tone === 'failed')).toBe(true);
  });

  test('gives overlapping fan-outs their own lanes', () => {
    const fan = [
      phase('orchestrator', 'completed'),
      phase('implementer', 'active'),
      phase('implementer', 'active'),
      phase('implementer', 'active'),
    ];
    const graph = layoutAgentGraph(fan, graphPaneBounds(2).max - 4);
    // Every implementer must be reachable: one arrow head each.
    expect(graph.cells.filter(cell => cell.glyph === '▶')).toHaveLength(3);
  });

  test('narrow panes shrink the node, never below the readable floor', () => {
    const wide = layoutAgentGraph(CHAIN, graphPaneBounds(3).max - 4);
    const narrow = layoutAgentGraph(CHAIN, graphPaneBounds(3).min - 4);
    expect(narrow.nodes[0]?.width).toBeLessThan(wide.nodes[0]?.width as number);
    expect(narrow.nodes[0]?.width).toBeGreaterThanOrEqual(14);
  });

  test('handles an empty round without throwing', () => {
    const graph = layoutAgentGraph([], 60);
    expect(graph.nodes).toEqual([]);
    expect(graph.cells).toEqual([]);
    expect(graph.width).toBe(0);
  });
});

describe('graphPaneBounds', () => {
  test('grows with the number of stages', () => {
    expect(graphPaneBounds(4).min).toBeGreaterThan(graphPaneBounds(3).min);
    expect(graphPaneBounds(4).max).toBeGreaterThan(graphPaneBounds(4).min);
  });
});

describe('a round with many agents', () => {
  const many: AgentPhase[] = [
    phase('orchestrator', 'completed'),
    ...Array.from({length: 4}, () => phase('implementer', 'active')),
    ...Array.from({length: 3}, () => phase('judge', 'pending')),
    ...Array.from({length: 2}, () => phase('profiler', 'pending')),
  ];

  test('gives every agent its own node and every fed agent an arrow', () => {
    const graph = layoutAgentGraph(many, graphPaneBounds(4).max - 4);
    expect(graph.nodes).toHaveLength(10);
    // One arrow head per node that is fed, not one per edge: several sources
    // converging on a judge share the head they point at.
    expect(graph.cells.filter(cell => cell.glyph === '▶')).toHaveLength(4 + 3 + 2);
  });

  test('never stacks two nodes on the same cell', () => {
    const graph = layoutAgentGraph(many, graphPaneBounds(4).max - 4);
    const seen = new Set<string>();
    for (const node of graph.nodes) {
      for (let row = node.y; row < node.y + NODE_HEIGHT; row += 1) {
        const key = `${node.x},${row}`;
        expect(seen.has(key)).toBe(false);
        seen.add(key);
      }
    }
  });

  test('keeps edge cells out of the columns the nodes occupy', () => {
    const graph = layoutAgentGraph(many, graphPaneBounds(4).max - 4);
    const width = graph.nodes[0]?.width as number;
    for (const cell of graph.cells) {
      expect(cell.x % (width + 5)).toBeGreaterThanOrEqual(width);
    }
  });

  test('resolves crossing edges into junctions rather than overwriting', () => {
    const graph = layoutAgentGraph(many, graphPaneBounds(4).max - 4);
    const junctions = graph.cells.filter(cell => '┼├┤┬┴'.includes(cell.glyph));
    expect(junctions.length).toBeGreaterThan(0);
  });
});
