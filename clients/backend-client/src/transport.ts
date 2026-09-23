import type {ProtocolResponse, RequestInput, ServerMessage} from './protocol.js';

export interface EventSubscription {
  close(): Promise<void>;
}

export interface SubscribeOptions {
  /** Request only the newest events when the server supports tail bootstrap. */
  tail?: number;
  /** Identify the event-store generation that owns a resume cursor. */
  storeId?: string;
}

/** The runtime-neutral transport contract shared by the TUI and browser clients. */
export interface ServerTransport {
  request(input: RequestInput): Promise<ProtocolResponse>;
  subscribe(
    afterSequence: number,
    onMessage: (message: ServerMessage) => void,
    onDisconnect: (error: Error) => void,
    options?: SubscribeOptions,
  ): Promise<EventSubscription>;
  close(): Promise<void>;
}
