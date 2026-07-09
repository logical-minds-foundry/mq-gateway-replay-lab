"""Smoke test: the package imports and reports its version."""

import gwlab


def test_version() -> None:
    assert gwlab.__version__ == "0.1.0"
