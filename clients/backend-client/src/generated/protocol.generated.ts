/* Generated from the Python protocol models. Do not edit. */

export type Request =
  | PauseCommand
  | ResumeCommand
  | SteerCommand
  | SnapshotQuery
  | ChatQuery
  | HistoryQuery
  | PerformanceQuery
  | ExperimentQuery
  | EventsQuery
  | SubscribeRequest;
export type ProtocolVersion = 1;
export type RequestId = string;
export type Timestamp = string;
export type Type = "command.pause";
export type Mode = "after_current_agent_call";
export type ProtocolVersion1 = 1;
export type RequestId1 = string;
export type Timestamp1 = string;
export type Type1 = "command.resume";
export type ProtocolVersion2 = 1;
export type RequestId2 = string;
export type Timestamp2 = string;
export type Type2 = "command.steer";
export type Text = string;
export type ProtocolVersion3 = 1;
export type RequestId3 = string;
export type Timestamp3 = string;
export type Type3 = "query.snapshot";
export type ProtocolVersion4 = 1;
export type RequestId4 = string;
export type Timestamp4 = string;
export type Type4 = "query.chat";
export type Text1 = string;
export type ProtocolVersion5 = 1;
export type RequestId5 = string;
export type Timestamp5 = string;
export type Type5 = "query.history";
export type ProtocolVersion6 = 1;
export type RequestId6 = string;
export type Timestamp6 = string;
export type Type6 = "query.performance";
export type ProtocolVersion7 = 1;
export type RequestId7 = string;
export type Timestamp7 = string;
export type Type7 = "query.experiments";
export type ProtocolVersion8 = 1;
export type RequestId8 = string;
export type Timestamp8 = string;
export type Type8 = "query.events";
export type AfterSequence = number;
export type TimeoutMs = number;
export type ProtocolVersion9 = 1;
export type RequestId9 = string;
export type Timestamp9 = string;
export type Type9 = "subscribe";
export type AfterSequence1 = number;
export type ProtocolVersion10 = 1;
export type RequestId10 = string;
export type Timestamp10 = string;
export type Ok = boolean;
export type Error = string | null;
export type Id = string;
export type Code = string;
export type Summary = string;
export type Detail = string | null;
export type Hint = string | null;
/**
 * Boundary at which a diagnostic was raised.
 */
export type DiagnosticScope = "configuration" | "invocation" | "phase" | "run" | "request" | "protocol" | "transport";
/**
 * Operator-visible seriousness of a diagnostic.
 */
export type DiagnosticSeverity = "warning" | "error" | "fatal";
/**
 * Whether retrying the failed operation is expected to help.
 */
export type DiagnosticRetryability = "automatic" | "manual" | "never" | "unknown";
export type CauseId = string | null;
export type DebugRef = string | null;
export type Action = "pause" | "resume" | "steer";
export type Status = "pending" | "consumed";
export type Question = string;
export type Answer = string;
export type Effect = "none";
export type ProtocolVersion11 = 1;
export type RunId = string;
export type Sequence = number;
export type Status1 = string;
export type AgentKind = string | null;
export type RoundLabel = string | null;
export type ExecutionId = string;
export type AgentKind1 = string;
export type RoundLabel1 = string;
export type Stage = string;
export type Attempt = number | null;
export type Assignment = string;
export type StartedAt = string;
export type Kind = "agent_execution_activity_changed";
export type Mode1 = "thinking" | "responding" | "tool" | "waiting";
export type Summary1 = string;
export type Tool = string | null;
export type ActiveExecutions = ActiveAgentExecution[];
export type ProtocolVersion12 = 1;
export type Sequence1 = number;
export type RunId1 = string;
export type Timestamp11 = string;
export type EventType =
  | "server_started"
  | "server_ready"
  | "configuration_failed"
  | "run_started"
  | "experiments_changed"
  | "run_interrupted"
  | "chat"
  | "status_query"
  | "control"
  | "invocation_started"
  | "invocation_finished"
  | "agent_execution_started"
  | "agent_execution_activity_changed"
  | "agent_execution_finished"
  | "phase_started"
  | "phase_finished"
  | "agent_output_chunk"
  | "subprocess_output"
  | "judge_result"
  | "benchmark_result"
  | "round_finished"
  | "run_finished"
  | "run_failed"
  | "output"
  | "tool_call"
  | "tool_result"
  | "todo_update"
  | "usage_update";
export type Text2 = string;
export type EventStatus =
  "active" | "answered" | "pending" | "consumed" | "completed" | "failed" | "cancelled" | "interrupted";
export type RoundLabel2 = string | null;
export type AgentKind2 = string | null;
export type InvocationId = string | null;
export type ExecutionId1 = string | null;
export type Data =
  | (
      | ChatData
      | InvocationStartedData
      | InvocationFinishedData
      | AgentExecutionStartedData
      | AgentExecutionActivityData
      | AgentExecutionFinishedData
      | OutputData
      | ServerReadyData
      | RunStartedData
      | RunInterruptedData
      | ExperimentsChangedData
      | ConfigurationFailedData
      | PhaseData
      | AgentOutputChunkData
      | SubprocessOutputData
      | JudgeResultData
      | BenchmarkResultData
      | RoundFinishedData
      | ToolCallData
      | ToolResultData
      | TodoUpdateData
      | UsageUpdateData
    )
  | null;
export type Kind1 = "chat";
export type Answer1 = string;
export type Kind2 = "invocation_started";
export type SystemPrompt = string;
export type UserPrompt = string;
export type Kind3 = "invocation_finished";
export type Error1 = string | null;
export type Kind4 = "agent_execution_started";
export type Stage1 = string;
export type Attempt1 = number | null;
export type SystemPrompt1 = string;
export type UserPrompt1 = string;
export type Driver = string | null;
export type Provider = string | null;
export type Model = string | null;
export type Kind5 = "agent_execution_finished";
export type Error2 = string | null;
export type Kind6 = "output";
export type Stream = "stdout" | "stderr";
export type Source = string;
export type Content = string;
export type Kind7 = "server_ready";
export type SocketProtocol = "jsonl";
export type Kind8 = "run_started";
export type OuterLoop = string;
export type Input = string;
export type MaxRounds = number;
export type Kind9 = "run_interrupted";
export type Reason = string;
export type Signal = string | null;
export type Kind10 = "experiments_changed";
export type Reason1 = "project_attached" | "active_hypothesis_changed" | "round_persisted";
export type Kind11 = "configuration_failed";
export type Code1 = string;
export type Stage2 = string;
export type Message = string;
export type Usage = string | null;
export type ExitCode = number;
export type Kind12 = "phase";
export type Phase = string;
export type Attempt2 = number | null;
export type Kind13 = "agent_output_chunk";
export type Channel = "assistant" | "analysis" | "tool" | "diagnostic" | "prompt";
export type Content1 = string;
export type Progress = string | null;
export type AgentLabel = string | null;
export type ElapsedSeconds = number;
export type InputTokens = number;
export type ContextWindow = number | null;
export type Kind14 = "subprocess_output";
export type ProcessId = string;
export type ProcessKind = string;
export type Stream1 = "stdout" | "stderr";
export type Content2 = string;
export type Kind15 = "judge_result";
export type Verdict = "pass" | "fail";
export type Feedback = string;
export type Attempt3 = number;
export type Kind16 = "benchmark_result";
export type Metric = string;
export type Value = number;
export type Unit = string;
export type Kind17 = "round_finished";
export type Attempts = number;
export type JudgeVerdict = "pass" | "fail" | "skipped";
export type PerfMetric = number | null;
export type PerfUnit = string | null;
export type Kind18 = "tool_call";
export type Tool1 = string;
export type CallId = string | null;
export type Kind19 = "tool_result";
export type Tool2 = string;
export type CallId1 = string | null;
export type Content3 = string;
export type IsError = boolean;
export type Payload = (CommandResultPayload | JsonResultPayload) | null;
export type Kind20 = "command";
export type Stdout = string;
export type Stderr = string;
export type ExitCode1 = number | null;
export type Duration = number | null;
export type Kind21 = "json";
export type Value1 =
  | {
      [k: string]: unknown;
    }
  | unknown[];
export type Kind22 = "todo_update";
export type Content4 = string;
export type Status2 = string;
export type Todos = TodoItemData[];
export type Kind23 = "usage_update";
export type InputTokens1 = number;
export type ContextWindow1 = number | null;
export type Model1 = string | null;
export type Events = RunEvent[];
export type Round = number;
export type PerfMetric1 = number;
export type PerfUnit1 = string;
export type Passed = boolean;
export type ProfileSkipped = boolean;
export type Performance = PerformanceRound[];
export type HypothesisId = string;
export type Identified = boolean;
export type Title = string | null;
export type Claim = string | null;
export type Action1 = string | null;
export type FirstRound = number;
export type LastRound = number;
export type Round1 = number;
export type Passed1 = boolean;
export type Reviewed = boolean;
export type HypothesisOutcome = string | null;
export type PerfMetric2 = number | null;
export type PerfUnit2 = string | null;
export type Commit = string | null;
export type OfficialEvaluation = boolean;
export type CandidateDisposition = string | null;
export type Rounds = HypothesisRound[];
export type ResolvedOutcome = string | null;
export type JudgeVerdict1 = ("pass" | "fail") | null;
export type PerfMetric3 = number | null;
export type PerfUnit3 = string | null;
export type PerfDeltaPct = number | null;
export type Kept = boolean | null;
export type StrategyDisposition = ("available" | "parked" | "abandoned") | null;
export type StrategyReason = string | null;
export type Active = boolean;
export type Experiments = HypothesisEntry[];
export type ExperimentsReady = boolean | null;
export type ServerMessage = SubscribedMessage | EventMessage | EventBatchMessage | ProtocolErrorMessage;
export type Type10 = "subscribed";
export type RequestId11 = string;
export type RunId2 = string;
export type LatestSequence = number;
export type Type11 = "event";
export type Type12 = "event_batch";
export type Events1 = RunEvent[];
export type ThroughSequence = number;
export type ActiveExecutions1 = ActiveAgentExecution[];
export type Type13 = "protocol_error";
export type RequestId12 = string | null;
export type Code2 = string;
export type Message1 = string;

export interface ProtocolDocument {
  request: Request;
  response: Response;
  event: RunEvent;
  snapshot: RunSnapshot;
  server_message: ServerMessage;
  [k: string]: unknown;
}
export interface PauseCommand {
  protocol_version?: ProtocolVersion;
  request_id?: RequestId;
  timestamp?: Timestamp;
  type?: Type;
  mode?: Mode;
}
export interface ResumeCommand {
  protocol_version?: ProtocolVersion1;
  request_id?: RequestId1;
  timestamp?: Timestamp1;
  type?: Type1;
}
export interface SteerCommand {
  protocol_version?: ProtocolVersion2;
  request_id?: RequestId2;
  timestamp?: Timestamp2;
  type?: Type2;
  text: Text;
}
export interface SnapshotQuery {
  protocol_version?: ProtocolVersion3;
  request_id?: RequestId3;
  timestamp?: Timestamp3;
  type?: Type3;
}
export interface ChatQuery {
  protocol_version?: ProtocolVersion4;
  request_id?: RequestId4;
  timestamp?: Timestamp4;
  type?: Type4;
  text: Text1;
}
export interface HistoryQuery {
  protocol_version?: ProtocolVersion5;
  request_id?: RequestId5;
  timestamp?: Timestamp5;
  type?: Type5;
}
export interface PerformanceQuery {
  protocol_version?: ProtocolVersion6;
  request_id?: RequestId6;
  timestamp?: Timestamp6;
  type?: Type6;
}
/**
 * Request the hypothesis-level experiment log for the attached run.
 */
export interface ExperimentQuery {
  protocol_version?: ProtocolVersion7;
  request_id?: RequestId7;
  timestamp?: Timestamp7;
  type?: Type7;
}
export interface EventsQuery {
  protocol_version?: ProtocolVersion8;
  request_id?: RequestId8;
  timestamp?: Timestamp8;
  type?: Type8;
  after_sequence?: AfterSequence;
  timeout_ms?: TimeoutMs;
}
export interface SubscribeRequest {
  protocol_version?: ProtocolVersion9;
  request_id?: RequestId9;
  timestamp?: Timestamp9;
  type?: Type9;
  after_sequence?: AfterSequence1;
}
export interface Response {
  protocol_version?: ProtocolVersion10;
  request_id: RequestId10;
  timestamp?: Timestamp10;
  ok?: Ok;
  error?: Error;
  diagnostic?: Diagnostic | null;
  ack?: CommandAck | null;
  chat?: ChatResult | null;
  snapshot?: RunSnapshot | null;
  events?: Events;
  performance?: Performance;
  experiments?: Experiments;
  experiments_ready?: ExperimentsReady;
}
/**
 * Structured, provider-neutral description of an operator diagnostic.
 */
export interface Diagnostic {
  id?: Id;
  code: Code;
  summary: Summary;
  detail?: Detail;
  hint?: Hint;
  scope: DiagnosticScope;
  severity?: DiagnosticSeverity;
  retryability?: DiagnosticRetryability;
  cause_id?: CauseId;
  debug_ref?: DebugRef;
}
export interface CommandAck {
  action: Action;
  status: Status;
}
export interface ChatResult {
  question: Question;
  answer: Answer;
  effect?: Effect;
}
export interface RunSnapshot {
  protocol_version?: ProtocolVersion11;
  run_id: RunId;
  sequence: Sequence;
  status: Status1;
  agent_kind?: AgentKind;
  round_label?: RoundLabel;
  active_executions?: ActiveExecutions;
}
/**
 * Authoritative activity checkpoint for one running agent execution.
 */
export interface ActiveAgentExecution {
  execution_id: ExecutionId;
  agent_kind: AgentKind1;
  round_label: RoundLabel1;
  stage: Stage;
  attempt?: Attempt;
  assignment: Assignment;
  started_at: StartedAt;
  activity: AgentExecutionActivityData;
}
/**
 * Complete current activity for an active agent execution.
 */
export interface AgentExecutionActivityData {
  kind?: Kind;
  mode: Mode1;
  summary: Summary1;
  tool?: Tool;
  [k: string]: unknown;
}
/**
 * One reproducible human, control, or invocation event.
 */
export interface RunEvent {
  protocol_version?: ProtocolVersion12;
  sequence?: Sequence1;
  run_id?: RunId1;
  timestamp: Timestamp11;
  type: EventType;
  text?: Text2;
  diagnostic?: Diagnostic | null;
  status?: EventStatus | null;
  round_label?: RoundLabel2;
  agent_kind?: AgentKind2;
  invocation_id?: InvocationId;
  execution_id?: ExecutionId1;
  data?: Data;
}
export interface ChatData {
  kind?: Kind1;
  answer: Answer1;
  [k: string]: unknown;
}
export interface InvocationStartedData {
  kind?: Kind2;
  system_prompt: SystemPrompt;
  user_prompt: UserPrompt;
  [k: string]: unknown;
}
export interface InvocationFinishedData {
  kind?: Kind3;
  result?: Result;
  error?: Error1;
  [k: string]: unknown;
}
export interface Result {
  [k: string]: unknown;
}
/**
 * Semantic context for one prompt-to-result agent execution.
 */
export interface AgentExecutionStartedData {
  kind?: Kind4;
  stage: Stage1;
  attempt?: Attempt1;
  system_prompt?: SystemPrompt1;
  user_prompt?: UserPrompt1;
  activity: AgentExecutionActivityData;
  driver?: Driver;
  provider?: Provider;
  model?: Model;
  [k: string]: unknown;
}
/**
 * Terminal result for one agent execution.
 */
export interface AgentExecutionFinishedData {
  kind?: Kind5;
  result?: Result1;
  error?: Error2;
  [k: string]: unknown;
}
export interface Result1 {
  [k: string]: unknown;
}
export interface OutputData {
  kind?: Kind6;
  stream: Stream;
  source?: Source;
  content: Content;
  [k: string]: unknown;
}
export interface ServerReadyData {
  kind?: Kind7;
  socket_protocol?: SocketProtocol;
  [k: string]: unknown;
}
export interface RunStartedData {
  kind?: Kind8;
  outer_loop: OuterLoop;
  input: Input;
  max_rounds: MaxRounds;
  [k: string]: unknown;
}
export interface RunInterruptedData {
  kind?: Kind9;
  reason: Reason;
  signal?: Signal;
  [k: string]: unknown;
}
export interface ExperimentsChangedData {
  kind?: Kind10;
  reason: Reason1;
  [k: string]: unknown;
}
export interface ConfigurationFailedData {
  kind?: Kind11;
  code: Code1;
  stage: Stage2;
  message: Message;
  usage?: Usage;
  exit_code: ExitCode;
  [k: string]: unknown;
}
export interface PhaseData {
  kind?: Kind12;
  phase: Phase;
  attempt?: Attempt2;
  [k: string]: unknown;
}
export interface AgentOutputChunkData {
  kind?: Kind13;
  channel: Channel;
  content: Content1;
  status?: AgentStatusData | null;
  [k: string]: unknown;
}
/**
 * Structured progress readings for one agent invocation.
 *
 * Carried on presentation events so renderers can format their own status
 * prefix (e.g. ``[Round 3/24 | Implementer | 12.3s | 20k/1.0M]``) without
 * the backend baking any layout or styling into the payload.
 */
export interface AgentStatusData {
  progress?: Progress;
  agent_label?: AgentLabel;
  elapsed_seconds?: ElapsedSeconds;
  input_tokens?: InputTokens;
  context_window?: ContextWindow;
  [k: string]: unknown;
}
export interface SubprocessOutputData {
  kind?: Kind14;
  process_id: ProcessId;
  process_kind: ProcessKind;
  stream: Stream1;
  content: Content2;
  [k: string]: unknown;
}
export interface JudgeResultData {
  kind?: Kind15;
  verdict: Verdict;
  feedback: Feedback;
  attempt: Attempt3;
  [k: string]: unknown;
}
export interface BenchmarkResultData {
  kind?: Kind16;
  metric: Metric;
  value: Value;
  unit: Unit;
  [k: string]: unknown;
}
export interface RoundFinishedData {
  kind?: Kind17;
  attempts: Attempts;
  judge_verdict: JudgeVerdict;
  perf_metric?: PerfMetric;
  perf_unit?: PerfUnit;
  [k: string]: unknown;
}
export interface ToolCallData {
  kind?: Kind18;
  tool: Tool1;
  call_id?: CallId;
  args?: Args;
  status?: AgentStatusData | null;
  [k: string]: unknown;
}
export interface Args {
  [k: string]: unknown;
}
export interface ToolResultData {
  kind?: Kind19;
  tool: Tool2;
  call_id?: CallId1;
  content: Content3;
  is_error?: IsError;
  payload?: Payload;
  [k: string]: unknown;
}
/**
 * Structured result of a command-style tool execution.
 */
export interface CommandResultPayload {
  kind?: Kind20;
  stdout: Stdout;
  stderr: Stderr;
  exit_code?: ExitCode1;
  duration?: Duration;
  [k: string]: unknown;
}
/**
 * A tool result that is a JSON object or array, already parsed.
 */
export interface JsonResultPayload {
  kind?: Kind21;
  value: Value1;
  [k: string]: unknown;
}
export interface TodoUpdateData {
  kind?: Kind22;
  todos?: Todos;
  [k: string]: unknown;
}
export interface TodoItemData {
  content: Content4;
  status: Status2;
  [k: string]: unknown;
}
export interface UsageUpdateData {
  kind?: Kind23;
  input_tokens: InputTokens1;
  context_window?: ContextWindow1;
  model?: Model1;
  [k: string]: unknown;
}
export interface PerformanceRound {
  round: Round;
  perf_metric: PerfMetric1;
  perf_unit: PerfUnit1;
  passed: Passed;
  profile_skipped?: ProfileSkipped;
}
/**
 * One unit of investigation: a hypothesis and every round it spans.
 *
 * ``resolved_outcome`` is copied from the backend's typed hypothesis state,
 * never recomputed by the server or client.
 */
export interface HypothesisEntry {
  hypothesis_id: HypothesisId;
  identified?: Identified;
  title?: Title;
  claim?: Claim;
  action?: Action1;
  first_round: FirstRound;
  last_round: LastRound;
  rounds?: Rounds;
  resolved_outcome?: ResolvedOutcome;
  judge_verdict?: JudgeVerdict1;
  perf_metric?: PerfMetric3;
  perf_unit?: PerfUnit3;
  perf_delta_pct?: PerfDeltaPct;
  kept?: Kept;
  strategy_disposition?: StrategyDisposition;
  strategy_reason?: StrategyReason;
  active?: Active;
}
/**
 * One round belonging to a hypothesis, for the experiment-log drill-down.
 */
export interface HypothesisRound {
  round: Round1;
  passed: Passed1;
  reviewed: Reviewed;
  hypothesis_outcome?: HypothesisOutcome;
  perf_metric?: PerfMetric2;
  perf_unit?: PerfUnit2;
  commit?: Commit;
  official_evaluation?: OfficialEvaluation;
  candidate_disposition?: CandidateDisposition;
}
export interface SubscribedMessage {
  type?: Type10;
  request_id: RequestId11;
  run_id: RunId2;
  latest_sequence: LatestSequence;
}
export interface EventMessage {
  type?: Type11;
  event: RunEvent;
}
export interface EventBatchMessage {
  type?: Type12;
  events: Events1;
  through_sequence?: ThroughSequence;
  active_executions?: ActiveExecutions1;
}
export interface ProtocolErrorMessage {
  type?: Type13;
  request_id?: RequestId12;
  code: Code2;
  message: Message1;
  diagnostic?: Diagnostic | null;
}
