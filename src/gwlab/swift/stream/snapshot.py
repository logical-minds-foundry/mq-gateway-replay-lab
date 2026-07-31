"""The pre-computed metrics snapshot the scrape reads for free (design §6).

After each sweep the collector renders the maintained state to a Prometheus
exposition string and publishes it here by an **atomic reference swap** — a bare
attribute assignment, which is atomic under the GIL, so no lock is needed. The
HTTP responder thread serves :meth:`SnapshotHolder.latest` on every scrape with a
single atomic read: no fold, no browse, constant latency independent of retained
volume.

The ingest loop is the sole publisher and the responder thread the sole reader;
because the reference swap is atomic, a scrape always sees either the previous
complete snapshot or the next complete one, never a half-built string.
"""

from __future__ import annotations


class SnapshotHolder:
    """Holds the latest rendered exposition, swapped atomically per sweep."""

    def __init__(self, initial: str) -> None:
        """Seed the holder with ``initial`` text (served until the first publish)."""
        self._latest = initial

    def publish(self, text: str) -> None:
        """Atomically replace the served snapshot with ``text`` (a reference swap)."""
        self._latest = text

    def latest(self) -> str:
        """Return the most recently published snapshot (a single atomic read)."""
        return self._latest
