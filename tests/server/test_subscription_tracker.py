"""Unit contracts of the transport's subscription lifetime tracker."""

import threading
import time

from server.transport.subscriptions import SubscriptionTracker


def test_disconnect_wait_bridges_a_redial_inside_the_settle_window() -> None:
    tracker = SubscriptionTracker()
    unblocked = threading.Event()
    first = tracker.track()
    first.__enter__()

    def wait_for_none() -> None:
        tracker.wait_for_none_active(settle_seconds=0.4)
        unblocked.set()

    waiter = threading.Thread(target=wait_for_none, daemon=True)
    waiter.start()
    time.sleep(0.05)
    first.__exit__(None, None, None)
    # The redial lands inside the settle window, so the wait must keep
    # blocking well past where the window alone would have expired.
    with tracker.track():
        assert not unblocked.wait(timeout=0.6)

    assert unblocked.wait(timeout=2)
    waiter.join(timeout=2)
    assert not waiter.is_alive()
