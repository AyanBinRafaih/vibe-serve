import {type CliRenderer, TextRenderable} from '@opentui/core';
import type {ActiveAgentExecution, SessionState} from '../session-model.js';
import {visibleRoundNumber} from '../session-model.js';
import type {Theme} from './theme.js';

const SPINNER_FRAMES = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏'];
const SPINNER_INTERVAL_MS = 120;
export type ActivityBarScope = 'global' | 'conversation';

/** Stable, selection-aware status line for backend-authoritative execution activity. */
export class ActivityBarView {
  readonly output: TextRenderable;
  #executions: ActiveAgentExecution[] = [];
  #frame = 0;
  #timer: ReturnType<typeof setInterval> | null = null;

  constructor(renderer: CliRenderer, theme: Theme, id = 'activity-bar') {
    this.output = new TextRenderable(renderer, {
      id,
      height: 1,
      width: '100%',
      wrapMode: 'none',
      truncate: true,
      fg: theme.textMuted,
      content: '',
      visible: false,
    });
  }

  render(state: SessionState, scope: ActivityBarScope, visible = true): void {
    const all = Object.values(state.activeExecutions);
    const roundNumber = visibleRoundNumber(state);
    this.#executions = visible
      ? scope === 'global'
        ? all
        : all.filter(
            execution =>
              (roundNumber === null || execution.roundNumber === roundNumber) &&
              (state.selectedAgentKind === null || execution.agentKind === state.selectedAgentKind),
          )
      : [];
    this.output.visible = this.#executions.length > 0;
    this.#refresh();
    this.#syncTimer();
  }

  applyTheme(theme: Theme): void {
    this.output.fg = theme.textMuted;
  }

  destroy(): void {
    if (this.#timer !== null) clearInterval(this.#timer);
    this.#timer = null;
  }

  #syncTimer(): void {
    if (this.#executions.length === 0) {
      if (this.#timer !== null) clearInterval(this.#timer);
      this.#timer = null;
      return;
    }
    if (this.#timer !== null) return;
    this.#timer = setInterval(() => {
      this.#frame = (this.#frame + 1) % SPINNER_FRAMES.length;
      this.#refresh();
    }, SPINNER_INTERVAL_MS);
  }

  #refresh(): void {
    const spinner = SPINNER_FRAMES[this.#frame] ?? SPINNER_FRAMES[0];
    const nowMs = Date.now();
    if (this.#executions.length === 1) {
      const execution = this.#executions[0];
      if (execution === undefined) return;
      this.output.content = `${spinner} ${roleLabel(execution.agentKind)} · ${activitySummary(execution)} · ${elapsed(execution.startedAt, nowMs)}`;
      return;
    }
    const summaries = this.#executions
      .slice(0, 3)
      .map(execution => `${roleLabel(execution.agentKind)}: ${activitySummary(execution)}`)
      .join(' · ');
    const remainder = this.#executions.length > 3 ? ` · +${this.#executions.length - 3} more` : '';
    this.output.content = `${spinner} ${this.#executions.length} agents active · ${summaries}${remainder}`;
  }
}

export function activitySummary(_execution: ActiveAgentExecution): string {
  return 'Working';
}

function roleLabel(role: string): string {
  if (role === '') return 'Agent';
  return role.charAt(0).toUpperCase() + role.slice(1).replaceAll('_', ' ');
}

function elapsed(startedAt: string, nowMs: number): string {
  const milliseconds = nowMs - Date.parse(startedAt);
  const seconds = Number.isFinite(milliseconds) ? Math.max(0, Math.floor(milliseconds / 1000)) : 0;
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes}m ${seconds % 60}s`;
}
