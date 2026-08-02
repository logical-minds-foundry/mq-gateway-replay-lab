"""Pure anomaly-scoring primitives: binomial-proportion z for spikes, EWMA for drift."""

from __future__ import annotations

import math


def deviation_z(
    observed_fails: int, observed_n: int, expected_p: float, *, min_sample: int = 30
) -> float | None:
    """Binomial-proportion z-score of the observed fail rate vs the expected baseline.

    Returns None below ``min_sample`` (small-sample noise gate). The binomial
    standard error bakes sample size in, so low n needs a larger deviation to flag.
    """
    if observed_n < min_sample:
        return None
    se = math.sqrt(expected_p * (1.0 - expected_p) / observed_n)
    if se == 0.0:
        return 0.0
    return ((observed_fails / observed_n) - expected_p) / se


class Ewma:
    """Exponentially weighted moving average — an SPC detector for small sustained shifts."""

    def __init__(self, alpha: float) -> None:
        self._alpha = alpha
        self._value: float | None = None

    def update(self, x: float) -> float:
        if self._value is None:
            self._value = x
        else:
            self._value = self._alpha * x + (1 - self._alpha) * self._value
        return self._value

    @property
    def value(self) -> float:
        return 0.0 if self._value is None else self._value
