#!/usr/bin/env python3
import argparse
import json
import os
import socketserver
import threading
import time


STAMP = "2026-09-13T16:45:00Z"
RUN_ID = "pr737-store-swap"


def event(sequence, kind, *, content=None, data=None, round_label=None, agent_kind=None):
    payload = data
    if content is not None:
        payload = {"kind": "agent_output_chunk", "channel": "assistant", "content": content}
    return {
        "protocol_version": 1,
        "sequence": sequence,
        "run_id": RUN_ID,
        "timestamp": STAMP,
        "type": kind,
        "text": "",
        "diagnostic": None,
        "status": "active" if kind in {"run_started", "agent_execution_started"} else None,
        "round_label": round_label,
        "agent_kind": agent_kind,
        "invocation_id": "pr737-execution" if agent_kind else None,
        "execution_id": "pr737-execution" if agent_kind else None,
        "chat_thread_id": None,
        "data": payload,
    }


RUN_STARTED = lambda maximum: event(
    1,
    "run_started",
    data={
        "kind": "run_started",
        "outer_loop": "agent",
        "input": "/tmp/pr737-controlled-candidate",
        "max_rounds": maximum,
        "expected_roles": ["implementer"],
    },
)
EXECUTION = event(
    2,
    "agent_execution_started",
    data={
        "kind": "agent_execution_started",
        "stage": "implementer",
        "attempt": None,
        "system_prompt": "Controlled PR #737 reconnect scenario.",
        "user_prompt": "Demonstrate event-store identity handling.",
        "activity": {"kind": "agent_execution_activity_changed", "mode": "thinking", "summary": "Reconnecting", "tool": None},
        "driver": "agentshim",
        "provider": "fixture",
        "model": "controlled",
    },
    round_label="round-1-implementer",
    agent_kind="implementer",
)


def execution_finished(sequence):
    completed = event(
        sequence,
        "agent_execution_finished",
        data={"kind": "agent_execution_finished", "result": None, "error": None},
        round_label="round-1-implementer",
        agent_kind="implementer",
    )
    completed["status"] = "completed"
    return completed


STORE_A = [
    RUN_STARTED(2),
    EXECUTION,
    event(3, "agent_output_chunk", content="STALE SCRATCH STORE: this prefix must be replaced.\n", round_label="round-1-implementer", agent_kind="implementer"),
    execution_finished(4),
]
STORE_B = [
    RUN_STARTED(4),
    EXECUTION,
    event(3, "agent_output_chunk", content="DURABLE STORE PREFIX: replayed after reconnect.\n", round_label="round-1-implementer", agent_kind="implementer"),
    event(4, "agent_output_chunk", content="DURABLE SETUP: canonical state.\n", round_label="round-1-implementer", agent_kind="implementer"),
    event(5, "agent_output_chunk", content="DURABLE WORK: live store suffix.\n", round_label="round-1-implementer", agent_kind="implementer"),
    execution_finished(6),
]


class Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True

    def __init__(self, path):
        self.subscribe_count = 0
        self.counter_lock = threading.Lock()
        super().__init__(path, Handler)


class Handler(socketserver.StreamRequestHandler):
    def send(self, value):
        self.wfile.write(json.dumps(value).encode() + b"\n")
        self.wfile.flush()

    def handle(self):
        for raw in self.rfile:
            request = json.loads(raw)
            request_id = request.get("request_id", "request")
            request_type = request.get("type")
            if request_type == "subscribe":
                with self.server.counter_lock:
                    self.server.subscribe_count += 1
                    dial = self.server.subscribe_count
                self.send({"type": "subscribed", "request_id": request_id, "run_id": RUN_ID, "latest_sequence": 4 if dial == 1 else 6})
                if dial == 1:
                    events, store_id = STORE_A, "store-a"
                else:
                    store_id = "store-b"
                    events = STORE_B if request.get("store_id") == "store-a" else STORE_B[4:]
                self.send({
                    "type": "event_batch",
                    "events": events,
                    "through_sequence": 4 if dial == 1 else 6,
                    "active_executions": [],
                    "store_id": store_id,
                    "history_after_sequence": 0,
                })
                if dial == 1:
                    time.sleep(0.35)
                    return
                threading.Event().wait(120)
                return
            response = {
                "protocol_version": 1,
                "request_id": request_id,
                "timestamp": STAMP,
                "ok": True,
            }
            if request_type == "query.snapshot":
                response["snapshot"] = {"protocol_version": 1, "run_id": RUN_ID, "sequence": 0, "status": "starting", "agent_kind": None, "round_label": None, "active_executions": [], "chat_threads": []}
            elif request_type == "query.experiments":
                response.update({"experiments": [], "experiments_ready": True})
            elif request_type == "query.design":
                response.update({"design": [], "design_ready": True})
            elif request_type == "query.events":
                response["events"] = []
            self.send(response)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", required=True)
    args = parser.parse_args()
    try:
        os.unlink(args.socket)
    except FileNotFoundError:
        pass
    with Server(args.socket) as server:
        server.serve_forever(poll_interval=0.05)


if __name__ == "__main__":
    main()
