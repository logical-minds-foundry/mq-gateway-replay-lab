"""Streaming stateful settlement collector (epic #65, streaming-collector-design).

A long-running, client-mode successor to the O(N)-per-scrape batch collector: a
single-threaded sweep loop browses the ``GW.SEC.*`` audit queues with persistent
cursors, folds each message once into an in-memory per-SEME settlement picture,
and serves a pre-computed metrics snapshot over HTTP. Deployed as an installed
``console_scripts`` entry point (``gwlab-collector``) on ``mon-probe``, beside the
base MQ Prometheus exporter.

This is the T1 packaging skeleton: :mod:`gwlab.swift.stream.serve_collector`
currently exposes only the ``main`` entry-point stub (argparse + print). The
incremental ingest core (``state``, ``sweep``, ``snapshot``, ``selfmetrics``) and
the box-only serve loop arrive in later tasks of the plan
(``epics/65-swift-settlement-tracking/streaming-collector-plan.md``).
"""
