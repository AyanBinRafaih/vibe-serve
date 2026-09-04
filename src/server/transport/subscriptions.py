"""Subscription lifetime accounting for the transport server."""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Generator


class SubscriptionTracker:
    """Count active event subscriptions across handler threads.

    A disconnect must not end the server's lifetime while another subscription
    is still streaming: a client that reconnects after a dropped connection,
    or a second concurrent client, holds the count above zero until its own
    stream closes. ``wait_for_subscriber`` answers the separate question of
    whether any client has ever subscribed, and never resets.
    """

    def __init__(self) -> None:  # noqa: D107  # tracked: #288
        self._ever_subscribed = threading.Event()
        self._condition = threading.Condition()
        self._active = 0

    @contextmanager
    def track(self) -> Generator[None]:
        """Count one subscription stream for the duration of the block."""
        with self._condition:
            self._active += 1
        self._ever_subscribed.set()
        try:
            yield
        finally:
            with self._condition:
                self._active -= 1
                if self._active == 0:
                    self._condition.notify_all()

    def wait_for_subscriber(self, timeout: float) -> bool:
        """Wait until any client has established an event stream."""
        return self._ever_subscribed.wait(timeout)

    def wait_for_none_active(self) -> None:
        """Block until no subscription streams remain active."""
        with self._condition:
            self._condition.wait_for(lambda: self._active == 0)
