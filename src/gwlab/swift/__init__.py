"""SWIFT MT540-548 settlement-lifecycle tracking (Confirmation Tracking v2).

A parallel, independent successor to the v1 header/CorrId confirmation-tracking
work (``docs/mq-native-confirmation-tracking.md``). Where v1 correlates on the MQ
message descriptor (request MsgId echoed as reply CorrelId), v2 parses the SWIFT
message **body**: it correlates each response to its instruction by reference
(``:20C::SEME//`` -> ``:20C::RELA//``) and drives each instruction through an
explicit six-state settlement lifecycle. See ``epics/65-swift-settlement-tracking``.

This subpackage is data-driven: the parser, the state machine, and the field
validators consume declarative tables in :mod:`gwlab.swift.reference`, which are
grounded against cited public sources by the reference-grounding spike (epic #65,
task #74). v1 modules are not touched.
"""
