"""Audit-queue depth on the in-memory ``FakeMessaging`` (the ``inquire_depth`` seam).

``inquire_depth`` mirrors ``MQINQ`` on an audit queue: it returns
``(current_depth, max_depth)`` so the collector can expose queue-fill health
without draining the queue. The fake lets a test configure those values with
``set_depth`` and read them back, exercising the port the pymqi adapter also
implements — no queue manager needed.
"""

from __future__ import annotations

from gwlab.fake_messaging import FakeMessaging


def test_inquire_depth_returns_current_and_max() -> None:
    mq = FakeMessaging()
    mq.set_depth("GW.SEC.INSTRUCT.AUDIT", current=1200, maximum=5000)
    assert mq.inquire_depth("GW.SEC.INSTRUCT.AUDIT") == (1200, 5000)


def test_inquire_depth_defaults_to_zero_for_an_unconfigured_queue() -> None:
    mq = FakeMessaging()
    assert mq.inquire_depth("GW.SEC.CONFIRM.AUDIT") == (0, 0)


def test_set_depth_overwrites_a_previous_configuration() -> None:
    mq = FakeMessaging()
    mq.set_depth("GW.SEC.INSTRUCT.AUDIT", current=10, maximum=5000)
    mq.set_depth("GW.SEC.INSTRUCT.AUDIT", current=4200, maximum=5000)
    assert mq.inquire_depth("GW.SEC.INSTRUCT.AUDIT") == (4200, 5000)
