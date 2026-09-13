#!/usr/bin/env python3
import argparse
import json
import os
import socketserver
import threading
import time


STAMP = "2026-09-13T18:00:00Z"
RUN_ID = "pr656-revision-delta"


def hypothesis(hypothesis_id, title, first_round, last_round, **overrides):
    value = {
        "hypothesis_id": hypothesis_id,
        "identified": True,
        "title": title,
        "claim": title,
        "action": None,
        "first_round": first_round,
        "last_round": last_round,
        "rounds": [],
        "resolved_outcome": None,
        "kept": False,
        "active": False,
    }
    value.update(overrides)
    return value


INITIAL = [
    hypothesis(
        "H-KEEP",
        "Unchanged baseline must survive the delta",
        1,
        1,
        resolved_outcome="proven",
        kept=True,
    ),
    hypothesis("H-CHANGE", "Pending hypothesis at revision one", 2, 2, active=True),
]

DELTA = [
    hypothesis(
        "H-CHANGE",
        "Changed hypothesis at revision two",
        2,
        3,
        resolved_outcome="rejected",
    )
]


def run_started():
    return {
        "protocol_version": 1,
        "sequence": 1,
        "run_id": RUN_ID,
        "timestamp": STAMP,
        "type": "run_started",
        "status": "active",
        "data": {
            "kind": "run_started",
            "outer_loop": "agent",
            "input": "/tmp/pr656-controlled-candidate",
            "max_rounds": 4,
            "expected_roles": ["implementer"],
        },
    }


def experiments_changed():
    return {
        "protocol_version": 1,
        "sequence": 2,
        "run_id": RUN_ID,
        "timestamp": STAMP,
        "type": "experiments_changed",
        "data": {
            "kind": "experiments_changed",
            "reason": "round_persisted",
            "revision": 2,
        },
    }


class Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True

    def __init__(self, path, trace_path):
        self.experiment_queries = 0
        self.counter_lock = threading.Lock()
        self.trace_path = trace_path
        self.trace = []
        super().__init__(path, Handler)

    def record(self, request, response):
        encoded = json.dumps(response).encode() + b"\n"
        with self.counter_lock:
            self.trace.append(
                {
                    "query_number": self.experiment_queries,
                    "request": {
                        "type": request.get("type"),
                        "after": request.get("after"),
                    },
                    "response": {
                        "experiment_count": len(response.get("experiments", [])),
                        "experiment_update": response.get("experiment_update"),
                        "encoded_bytes": len(encoded),
                    },
                }
            )
            with open(self.trace_path, "w", encoding="utf-8") as output:
                json.dump(self.trace, output, indent=2)
                output.write("\n")


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
                self.send(
                    {
                        "type": "subscribed",
                        "request_id": request_id,
                        "run_id": RUN_ID,
                        "latest_sequence": 2,
                    }
                )
                self.send(
                    {
                        "type": "event_batch",
                        "events": [run_started()],
                        "through_sequence": 1,
                        "active_executions": [],
                        "history_after_sequence": 0,
                    }
                )
                time.sleep(0.8)
                self.send({"type": "event", "event": experiments_changed()})
                threading.Event().wait(120)
                return

            response = {
                "protocol_version": 1,
                "request_id": request_id,
                "timestamp": STAMP,
                "ok": True,
            }
            if request_type == "query.snapshot":
                response["snapshot"] = {
                    "protocol_version": 1,
                    "run_id": RUN_ID,
                    "sequence": 0,
                    "status": "starting",
                    "agent_kind": None,
                    "round_label": None,
                    "active_executions": [],
                    "chat_threads": [],
                }
            elif request_type == "query.experiments":
                with self.server.counter_lock:
                    self.server.experiment_queries += 1
                    query_number = self.server.experiment_queries
                if query_number == 1:
                    response.update(
                        {
                            "experiments": INITIAL,
                            "experiments_ready": True,
                            "experiment_update": {
                                "run_id": RUN_ID,
                                "projection_id": "controlled-project",
                                "through_revision": 1,
                                "reset": True,
                            },
                        }
                    )
                else:
                    # Keep the update in flight long enough to exercise the
                    # asynchronous refresh path in both exact revisions.
                    time.sleep(0.8)
                    after = request.get("after")
                    if after is None:
                        # The base client requests a complete replacement, so
                        # honor that contract with the complete current list.
                        response.update(
                            {
                                "experiments": [INITIAL[0], DELTA[0]],
                                "experiments_ready": True,
                            }
                        )
                    else:
                        # The revisioned client proves its base and receives
                        # only the row changed since that cursor.
                        response.update(
                            {
                                "experiments": DELTA,
                                "experiments_ready": True,
                                "experiment_update": {
                                    "run_id": RUN_ID,
                                    "projection_id": "controlled-project",
                                    "from_revision": 1,
                                    "through_revision": 2,
                                    "reset": False,
                                },
                            }
                        )
                self.server.record(request, response)
            elif request_type == "query.design":
                response.update({"design": [], "design_ready": True})
            elif request_type == "query.events":
                response["events"] = []
            self.send(response)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", required=True)
    parser.add_argument("--trace", required=True)
    args = parser.parse_args()
    try:
        os.unlink(args.socket)
    except FileNotFoundError:
        pass
    with Server(args.socket, args.trace) as server:
        server.serve_forever(poll_interval=0.05)


if __name__ == "__main__":
    main()
