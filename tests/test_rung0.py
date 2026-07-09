"""Tests for the Rung-0 async-log silent-loss reproduction."""

from gwlab.rung0 import Rung0Gateway


def test_host_loss_before_ship_silently_loses_an_accepted_message() -> None:
    gw = Rung0Gateway()
    gw.send(b"A")
    gw.ship()  # A reaches the alternate hosts

    gw.send(b"B")  # B is accepted (logged locally) but not yet shipped
    assert b"B" in gw.local_log  # the app believes B is sent

    gw.lose_host()  # the host dies in the un-shipped window

    recovered = gw.recover()
    assert b"A" in recovered
    # B was accepted but is unrecoverable — "sent, but no surviving record".
    # This is the silent-loss third state (I1 violated).
    assert b"B" not in recovered


def test_shipping_before_the_loss_preserves_the_message() -> None:
    gw = Rung0Gateway()
    gw.send(b"A")
    gw.ship()
    gw.lose_host()
    assert gw.recover() == {b"A"}


def test_a_clean_run_recovers_everything() -> None:
    gw = Rung0Gateway()
    for msg_id in (b"A", b"B", b"C"):
        gw.send(msg_id)
    gw.ship()
    assert gw.recover() == {b"A", b"B", b"C"}
