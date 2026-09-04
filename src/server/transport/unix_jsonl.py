"""Local JSONL transport for presentation clients."""

from __future__ import annotations

import json
import os
import socket
import socketserver
import threading
import time
from contextlib import suppress
from pathlib import Path  # noqa: TC003  # tracked: #288
from typing import TYPE_CHECKING

from pydantic import BaseModel, TypeAdapter

from server.api.protocol import (
    EventBatchMessage,
    ProtocolErrorMessage,
    ProtocolRequest,
    Response,
    SubscribedMessage,
    SubscribeRequest,
)
from server.transport.subscriptions import SubscriptionTracker
from vibesys.unix_socket import validate_socket_path

if TYPE_CHECKING:
    from server.api.service import RunApi

_REQUEST_ADAPTER = TypeAdapter(ProtocolRequest)


def _history_floor(request: SubscribeRequest, latest_sequence: int) -> tuple[int, int]:
    """Return the replay floor, and the floor to report until the next bootstrap.

    Without ``tail`` the reported floor stays 0: the client asked for
    everything from its own cursor onward, so nothing was withheld and old
    clients see the field's default.
    """
    if request.tail is None:
        return request.after_sequence, 0
    floor = max(request.after_sequence, latest_sequence - request.tail)
    return floor, floor


class _RequestHandler(socketserver.StreamRequestHandler):
    server: _JsonlUnixServer

    def handle(self) -> None:
        api = self.server.api
        for line in self.rfile:
            request_id = "unknown"
            try:
                raw = json.loads(line)
                request_id = str(raw.get("request_id", request_id))
                request = _REQUEST_ADAPTER.validate_python(raw)
                if isinstance(request, SubscribeRequest):
                    with self.server.subscriptions.track():
                        try:
                            self._stream(request)
                        except (BrokenPipeError, ConnectionResetError):
                            pass
                        except Exception as exc:  # noqa: BLE001  # tracked: #288
                            self._write_stream_error(request.request_id, exc)
                    return
                response = api.execute(request)
            except Exception as exc:  # noqa: BLE001  # tracked: #288
                response = Response.from_exception(
                    request_id,
                    exc,
                    operation="Request",
                )
            self.wfile.write(response.model_dump_json().encode() + b"\n")
            self.wfile.flush()

    def _stream(self, request: SubscribeRequest) -> None:
        api = self.server.api
        snapshot = api.snapshot()
        self._write_message(
            SubscribedMessage(
                request_id=request.request_id,
                run_id=snapshot.run_id,
                latest_sequence=snapshot.sequence,
            )
        )
        cursor, reported_floor = self._write_bootstrap(api, request, snapshot.sequence)
        while True:
            if not api.wait_for_change(cursor, timeout=1.0):
                if self._client_disconnected():
                    return
                time.sleep(0.05)
                continue
            latest_sequence = api.latest_sequence
            if request.tail is not None and latest_sequence - cursor > request.tail:
                # The run's durable event store is attached after the client
                # subscribes, so a subscription that bootstrapped against the
                # near-empty server store now faces the whole history as if it
                # were live output. Bootstrap again at a fresh tail rather than
                # replay a window the tail bound was meant to exclude.
                cursor, reported_floor = self._write_bootstrap(api, request, latest_sequence)
                continue
            # ``wait_for_change`` only tells us that the stream changed. Take
            # one watermark-consistent snapshot before writing so a resumed
            # run, or a burst of live output, reaches the client as one state
            # transition instead of thousands of repaint-triggering messages.
            through_sequence, events, active_executions = api.subscription_checkpoint(cursor)
            self._write_message(
                EventBatchMessage(
                    events=events,
                    through_sequence=through_sequence,
                    active_executions=active_executions,
                    history_after_sequence=reported_floor,
                )
            )
            cursor = through_sequence

    def _write_bootstrap(
        self,
        api: RunApi,
        request: SubscribeRequest,
        latest_sequence: int,
    ) -> tuple[int, int]:
        """Send one tail-bounded replay batch; return the new cursor and floor."""
        history_after, reported_floor = _history_floor(request, latest_sequence)
        through_sequence, replay, active_executions = api.subscription_checkpoint(
            history_after, bootstrap_spine=request.tail is not None
        )
        self._write_message(
            EventBatchMessage(
                events=replay,
                through_sequence=through_sequence,
                active_executions=active_executions,
                history_after_sequence=reported_floor,
            )
        )
        return through_sequence, reported_floor

    def _write_stream_error(self, request_id: str, error: Exception) -> None:
        """Report a replay or stream failure without hiding a live connection."""
        protocol_error = ProtocolErrorMessage.from_exception(
            error,
            operation="Event stream",
            code="stream_failed",
            request_id=request_id,
        )
        with suppress(BrokenPipeError, ConnectionResetError):
            self._write_message(protocol_error)

    def _client_disconnected(self) -> bool:
        try:
            return self.connection.recv(1, socket.MSG_PEEK | socket.MSG_DONTWAIT) == b""
        except BlockingIOError:
            return False
        except OSError:
            return True

    def _write_message(self, message: BaseModel) -> None:
        payload = message.model_dump_json()
        self.wfile.write(payload.encode() + b"\n")
        self.wfile.flush()


class _JsonlUnixServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(  # noqa: ANN204  # tracked: #288
        self,
        path: Path,
        api: RunApi,
        subscriptions: SubscriptionTracker,
    ):
        self.api = api
        self.subscriptions = subscriptions
        super().__init__(str(path), _RequestHandler)


class UnixJsonlServer:
    """Own a private Unix socket serving one or more concurrent clients."""

    def __init__(self, path: Path, api: RunApi):  # noqa: ANN204, D107  # tracked: #288
        self.path = path
        self.api = api
        self._server: _JsonlUnixServer | None = None
        self._thread: threading.Thread | None = None
        self._subscriptions = SubscriptionTracker()

    def start(self) -> None:  # noqa: D102  # tracked: #288
        validate_socket_path(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.unlink(missing_ok=True)
        self._server = _JsonlUnixServer(self.path, self.api, self._subscriptions)
        os.chmod(self.path, 0o600)  # noqa: PTH101  # tracked: #288
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="vibesys-server-jsonl",
            daemon=True,
        )
        self._thread.start()

    def wait_for_subscriber(self, timeout: float) -> bool:
        """Wait until a presentation client has established its event stream."""
        return self._subscriptions.wait_for_subscriber(timeout)

    def wait_for_subscriber_disconnect(self) -> None:
        """Keep terminal events queryable until the last active subscriber exits."""
        self._subscriptions.wait_for_none_active()

    def close(self) -> None:  # noqa: D102  # tracked: #288
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
        self.path.unlink(missing_ok=True)

    def __enter__(self) -> UnixJsonlServer:  # noqa: D105  # tracked: #288
        self.start()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:  # noqa: D105  # tracked: #288
        self.close()
